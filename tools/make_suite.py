"""Write the Compiler board's benchmark suite -- a maintainer tool.

    python tools/make_suite.py            # writes qccd/bench/suites/suite@1 if it is missing

The suite is every pair of these circuits and these devices (docs/PLAN-boards.md, Phase 3):

  memory     rep3, rep5, surface3, steane      (qccd.qec.experiments: QASM + detectors)
  clifford   bell2, ghz8, ghz16, ghz32, bv6, clifford12
  general    qft6, adder3                      (<= 10 qubits: checked by exact unitary)
  devices    the nine reference architectures in arch/

The reference compiler (qccdc + cooling) is run on every pair and its graded results are
pinned as baseline.json, the denominator of every speedup and of every LER ratio.

MEASURE THE BASELINE ON THE PLATFORM THAT GRADES IT: Linux, in the official service's image,
never a Windows host.  The memory pairs' logical error rates come from seeded stim sampling,
which is deterministic on one platform but differs between Windows and Linux.  With suite@1
measured on Windows, the reference compiler graded ler_ratio 0.992 on its own board: 29 of the
32 memory pairs had a different LER under the same seed, although every status and every
metric matched.

The current baseline was measured 2026-10-20 on Linux in the official image qccd-official:dfdf77b
(the published Linux qccdc e799ecf5a5cd), by this file's main() run inside it: 100 of the 108
public pairs valid, 0 wrong and 8 refused -- the room a better compiler has.  The pair timings
(`seconds`) are the sandbox's, and the one refusal that names its circuit file carries the
sandbox's path.

main() measures on the machine it runs on, and only when suite@1 is missing from SUITES_DIR.
The in-image route, as used on 2026-10-20: `git archive <commit> -- qccd arch tools
Compiler/examples Compiler/bench Compiler/bridge` into a scratch directory, move its
qccd/bench/suites/suite@1 aside, and run `docker run --rm -v <dir>:/repo -w /repo -e JOBS=2
--entrypoint python qccd-official:<commit> tools/make_suite.py` (the image's QCCD_QCCDC is the
published Linux compiler).  Then check that every file but baseline.json and suite.json is
byte-identical to the old suite, and copy those two back.

History.  2026-09-28: the first measurement, with a router that predated R22 (20 wrong pairs, so
the reference could not pass its own board).  2026-09-29: re-pinned the day it was published,
before any public submission (Windows, qccdc f52d3ba63b9a: 88 valid, 0 wrong, 20 refused).
2026-10-17: re-pinned for qccdc 7265883d596f (Windows: 100 / 0 / 8).  2026-10-18: the same
programs measured on Linux; only the LERs, the timings and that refusal's path changed.
2026-10-19: re-pinned for qccdc dbd13d8952f3, which keeps every qubit's gate order (commuting or
not) and sweeps the conveyor greedily, measured the same way in a rehearsal of the image
qccd-official:b4535df (private run os_3275b2eadbe9a312): still 100 / 0 / 8; the three memory
pairs on ring144_24v, the rotation pass's, changed their times (and two their LERs).
2026-10-20: re-pinned for the platform fixes #15-#21 and qccdc e799ecf5a5cd -- split and merge
charged for what a move does (#16), R22's direction labels (#19), the device's canonical order
(#20) -- measured in qccd-official:dfdf77b: still 100 / 0 / 8, no status changed; T_jones moved
on 96 of the 100 valid pairs (median 1.78x, at most 7.37x on surface3_r3_z@h2_racetrack; only
ghz8@stationary_chain and the three rotation-pass memory pairs on ring144_24v unchanged), and
the LER on 24 memory pairs.  A published suite is otherwise immutable: change it by adding
suite@2.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qccd.bench.harness import reference_compiler, run_suite  # noqa: E402
from qccd.bench.suite import SUITES_DIR, Suite, build_suite  # noqa: E402
from qccd.qec import get_experiment  # noqa: E402
from qccd.workspace.tasks import find_release  # noqa: E402

NAME, RELEASE = "suite", "1"
MEMORY = ["rep3", "rep5", "surface3", "steane"]
FILES = [("bell2", "Compiler/examples/bell2.qasm"), ("ghz8", "Compiler/bench/ghz8.qasm"),
         ("ghz16", "Compiler/examples/ghz16.qasm"), ("ghz32", "Compiler/bench/ghz32.qasm"),
         ("bv6", "Compiler/examples/bv6.qasm"), ("clifford12", "Compiler/bench/clifford12.qasm"),
         ("qft6", "Compiler/bench/qft6.qasm"), ("adder3", "Compiler/examples/adder3.qasm")]
DEVICES = ["chain", "cyclone_base", "cyclone_dual_loop", "deck_unit_cell", "grid9x9", "h2_racetrack",
           "ladder_2x72", "ring144_24v", "stationary_chain"]
LIMITS = {"pair_timeout_s": 120, "pair_mem_mb": 4096, "build_timeout_s": 600, "suite_timeout_s": 5400,
          "max_instructions": 500000}
LER = {"noise": "qccd-noise@1", "decoder": "auto", "max_shots": 100000, "max_errors": 100, "seed": 20260928}
HIDDEN = 8


def main() -> int:
    if (SUITES_DIR / f"{NAME}@{RELEASE}").exists():
        print(f"{NAME}@{RELEASE} exists; left alone")
        return 0
    physics = find_release("ghz4@1").physics()
    circuits = []
    for k in MEMORY:
        e = get_experiment(k)
        circuits.append({"name": e.name, "qasm": e.qasm, "detectors": e.spec(), "source": f"qccd.qec.experiments:{k}"})
    for name, path in FILES:
        circuits.append({"name": name, "qasm": (REPO / path).read_text(encoding="utf-8"), "source": path})
    devices = [{"name": d, "doc": json.loads((REPO / "arch" / f"{d}.arch.json").read_text(encoding="utf-8")),
                "source": f"arch/{d}.arch.json"} for d in DEVICES]
    kw = dict(circuits=circuits, devices=devices, physics=physics, title="Compiler benchmark",
              description=("Every pair of twelve circuits (four QEC memory experiments, six Clifford circuits, "
                           "two small non-Clifford ones) and the nine reference devices. A compiler is ranked by "
                           "how much faster its programs run than the reference compiler's, over the pairs both "
                           "compile; a single wrong program makes an entry ineligible."),
              limits=LIMITS, ler=LER, hidden_count=HIDDEN)
    with tempfile.TemporaryDirectory() as td:
        draft = Suite.load(build_suite(NAME, RELEASE, out_dir=Path(td), **kw))
        t0 = time.time()
        rep = run_suite(draft, reference_compiler(), workdir=Path(td) / "work", jobs=int(os.environ.get("JOBS", "4")), reference={},
                        progress=lambda i, n, pid, st: print(f"[{i}/{n}] {pid}: {st}", flush=True))
        base = {"compiler": "qccdc (reference) + insert_cooling", "measured": time.strftime("%Y-%m-%d"),
                "pairs": {r["id"]: {k: r.get(k) for k in ("status", "reason", "seconds", "metrics", "ler")}
                          for r in rep["pairs"]}}
        print(f"reference: {rep['summary']['counts']} in {time.time() - t0:.0f} s")
    d = build_suite(NAME, RELEASE, out_dir=SUITES_DIR, baseline=base, **kw)
    s = Suite.load(d)
    print(f"{s.id}: {len(s.manifest['pairs'])} pairs -> {d} ({s.digest[:23]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
