"""Write Goal 3's boards (task releases): one per fixed code -- a maintainer tool.

    python goals/goal3/boards.py                 # writes any release that is missing; never rewrites one

A Goal 3 release is one code's memory as ONE experiment: the code's Z memory for d rounds, read
out in the program, every ion reset, then its X memory for d rounds, read out by the grader
(`qccd.qec.stim_import.memory_seq` over `codes/<code>_{z,x}`).  A submission is then one design,
and the grader's two numbers are the ones the Goal 3 figure plots:

* `ler_per_round` = 1 - (1 - p_Z)(1 - p_X): a shot fails when either memory does, and a round is
  one memory's (the experiment's `rounds` is d);
* time per round = `T_quantinuum` / 2d, both memories' rounds.

The physics, noise model and metrics are Goal 1's (qccd-physics@2, qccd-noise@2, the
`quantinuum` table), so a design graded here and one graded on a Goal 1 board are graded alike.
The LER budget is the sweep's (sweep.py BUDGET) without its time limit: every colour point of the
sweep stopped on its error count, and a time limit would make the estimate depend on the
machine.  Each manifest says `"goal": "goal3"`, which keeps these boards off Goal 1's ranking.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from make_releases import LER_METRICS, METRICS_2, PER_LOGICAL, PHYSICS_2  # noqa: E402

from qccd.qec.experiments import MemoryExperiment  # noqa: E402
from qccd.qec.stim_import import memory_seq  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR, build_release, find_release  # noqa: E402

SEED = 20261029
_MATCH = {"decoder": "pymatching", "max_shots": 2_000_000, "max_errors": 200, "seed": SEED}
_BPOSD = {"decoder": "bposd", "max_shots": 2_000_000, "max_errors": 200, "batch": 10_000, "seed": SEED,
          "bp_max_iter": 30}
_LONG = {"compile_timeout_s": 3600, "lean_timeout_s": 3600}
_ROUNDS = {3: "Three", 5: "Five", 6: "Six", 7: "Seven", 10: "Ten", 12: "Twelve"}
_NOISE = ("Goal 3 fixes this code and compares architectures by the time per round and the logical error "
          "rate per round (a round fails when either memory does) under qccd-noise@2, with Helios-class "
          "physics in which every number is cited.")
_SCHEDULE = {
    "surface": "the hook-safe N-Z schedule (circuit distance {d})",
    "color": "the off-the-hook schedule with one auxiliary per plaquette (circuit distance {cd}, "
             "arXiv:2603.28852)",
    "bb": "Bravyi et al.'s depth-8 schedule (arXiv:2308.07915; circuit distance {cd})",
    "hgp": "the coloration schedule of Tremblay, Delfosse and Beverland (arXiv:2109.14609, Algorithm 1: X "
           "checks, then Z checks, seven layers each; circuit distance {cd}); the code is the product of "
           "the [7,4,3] Hamming code with itself",
}

#: the starter of a code no grid compiles: the lowest-LER design the reference compiler does
#: compile (C2LR aside: on a Goal 3 release its own physics makes it exploratory)
_BB72_START = {"generator": "ring", "name": "conveyor96_96d", "params": {"width": 96, "height": 2, "verticals": 96}}

#: code, order on the Leaderboard, starter (the sweep's lowest-LER grid as (a, b), or a device), limits
RELEASES = [
    ("surface3", 20, (5, 5), None),
    ("surface5", 21, (9, 8), None),
    ("surface7", 22, (10, 9), _LONG),
    ("color3", 23, (4, 3), None),
    ("color5", 24, (6, 5), None),
    ("color7", 25, (9, 8), _LONG),
    ("bb72", 26, _BB72_START, _LONG),
    ("hgp58", 27, (12, 12), _LONG),
]


def task_id(code: str) -> str:
    return f"goal3_{code}_mem"


def experiment(code: str) -> MemoryExperiment:
    """The release's experiment: the code's Z memory, then its X memory, on the same ions."""
    one = {}
    for b in ("z", "x"):
        d = HERE / "codes" / f"{code}_{b}"
        one[b] = MemoryExperiment.from_spec(json.loads((d / "detectors.json").read_text(encoding="utf-8")),
                                            (d / "circuit.qasm").read_text(encoding="utf-8"))
    meta = json.loads((HERE / "codes" / f"{code}_z" / "meta.json").read_text(encoding="utf-8"))
    return memory_seq(one["z"], one["x"], name=f"{code}seq_r{meta['rounds']}", code=f"{meta['builder']}_seq")


def describe(code: str, exp: MemoryExperiment) -> tuple[str, str]:
    meta = json.loads((HERE / "codes" / f"{code}_z" / "meta.json").read_text(encoding="utf-8"))
    n, k, d = meta["nkd"]
    name = {"surface": "rotated surface code", "color": "6.6.6 colour code",
            "bb": "bivariate bicycle code", "hgp": "hypergraph product code"}[meta["builder"]]
    title = f"Goal 3: {name}, distance {d}"
    words = _ROUNDS[exp.rounds]
    sched = _SCHEDULE[meta["builder"]].format(d=d, cd=meta["circuit_distance_upper_bound"])
    ions = exp.n_data + exp.n_ancilla
    text = (f"{words} rounds of the [[{n},{k},{d}]] {name} as a Z memory, read out in the program; every ion "
            f"reset; then {words.lower()} rounds as an X memory, read out by the grader ({ions} ions: "
            f"{exp.n_data} data, {exp.n_ancilla} ancillas, reset and reused every round). The circuit is "
            f"{sched}. " + _NOISE)
    return title, text


def main() -> int:
    physics = json.loads(PHYSICS_2.read_text(encoding="utf-8"))
    for code, order, start, limits in RELEASES:
        task = task_id(code)
        if (RELEASES_DIR / f"{task}@1").exists():
            print(f"{task}@1 exists; left alone")
            continue
        exp = experiment(code)
        title, text = describe(code, exp)
        budget = _MATCH if exp.matchable else _BPOSD
        d = build_release(task, "1", exp.qasm, physics, title=title, description=text, out_dir=RELEASES_DIR,
                          metrics=METRICS_2 + LER_METRICS + [PER_LOGICAL], rank_by="ler_per_round",
                          starter=start if isinstance(start, dict) else
                          {"generator": "grid", "name": f"grid{start[0]}x{start[1]}", "params": {"a": start[0], "b": start[1]}},
                          order=order, rank_table="quantinuum", tables=["quantinuum"], limits=limits,
                          qec={"experiment": exp.name, "detectors": exp.spec(), "noise": "qccd-noise@2",
                               "ler": budget, "logical_qubits": exp.logical_qubits,
                               "n_ions": exp.n_data + exp.n_ancilla},
                          goal="goal3")
        rel = find_release(f"{task}@1")
        print(f"{rel.id}: {rel.title} -> {d} ({rel.digest[:23]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
