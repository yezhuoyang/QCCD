"""Write the Compiler board's benchmark suite -- a maintainer tool.

    python tools/make_suite.py            # writes qccd/bench/suites/suite@1 if it is missing

The suite is every pair of these circuits and these devices (docs/PLAN-boards.md, Phase 3):

  memory     rep3, rep5, surface3, steane      (qccd.qec.experiments: QASM + detectors)
  clifford   bell2, ghz8, ghz16, ghz32, bv6, clifford12
  general    qft6, adder3                      (<= 10 qubits: checked by exact unitary)
  devices    the nine reference architectures in arch/

The reference compiler (qccdc + cooling) is run on every pair and its graded results are
pinned as baseline.json, the denominator of every speedup.  Measured 2026-09-28 (macOS, 4 jobs,
202 s): 68 of the 108 pairs valid, 20 wrong (R22: the router's moves on grids and rings need
more than one waveform per cycle), 19 refused, 1 timeout -- the room a better compiler has.  A published suite is immutable: change it by adding suite@2.
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
