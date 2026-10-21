"""Write a board (task release) for each leaderboard of qccd.academy -- a maintainer tool.

    python tools/make_releases.py            # writes any board that is missing; never rewrites one

Each board is `qccd/workspace/releases/<task>@1/`: the website board's own title, description
and circuit (`Compiler/examples/<task>_esm.qasm`, byte for byte; five_qubit from
`tasks/five_qubit/circuit.qasm`), the physics every board shares (the fixed blocks of the
starter board, qccd/workspace/releases/ghz4@1/physics.json), the same metrics and required
checks, and a suggested first device known to run the board's circuit (measured 2026-09-24:
rotate on a ring with docks for the syndrome rounds, the router on a 3x3 grid for the
five-qubit code; BB on ring(72,2,24) in 21 s).  A published release is immutable: change a
board by adding `<task>@2`.

MEMORY BOARDS (docs/PLAN-boards.md, Phase 2) rank by the logical error rate per round.  Their
circuit is a QEC memory experiment from qccd.qec.experiments: the syndrome rounds, with the data
readout appended by the grader (qccdc's rotation cannot measure an ion that rides the loop).
Measured 2026-09-28: rep5 and surface3 compile by rotation on ring(8,2,8) and pass every rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qccd.workspace.tasks import RELEASES_DIR, build_release, find_release  # noqa: E402

RING_SMALL = {"generator": "ring", "name": "ring16", "params": {"width": 8, "height": 2, "verticals": 8}}
BOARDS = [
    # task, order, circuit file, suggested start, limits
    ("bb144", 1, "Compiler/examples/bb144_esm.qasm",
     {"generator": "ring", "name": "ring144", "params": {"width": 72, "height": 2, "verticals": 24}},
     {"compile_timeout_s": 1800, "lean_timeout_s": 3600}),
    ("rep9", 2, "Compiler/examples/rep9_esm.qasm", RING_SMALL, None),
    ("five_qubit", 3, "tasks/five_qubit/circuit.qasm",
     {"generator": "grid", "name": "grid3x3", "params": {"a": 3, "b": 3}}, None),
    ("steane", 4, "Compiler/examples/steane_esm.qasm", RING_SMALL, None),
    ("surface17", 5, "Compiler/examples/surface17_esm.qasm", RING_SMALL, None),
]


MEMORY = [
    # task, order, experiment key, title, description
    ("rep5_mem", 6, "rep5", "Repetition code memory, distance 5",
     "Five rounds of the distance-5 repetition code's syndrome extraction (four ancillas, reset and reused "
     "every round), then the data read out; ranked by the logical error rate per round under the noise "
     "model built from the device's physics."),
    ("surface3_mem", 7, "surface3", "Surface code memory, distance 3",
     "Three rounds of the distance-3 rotated surface code's syndrome extraction (eight ancillas, reset and "
     "reused every round), then the data read out; ranked by the logical error rate per round under the "
     "noise model built from the device's physics."),
]
LER_METRICS = [
    {"name": "ler_per_round", "unit": "per round", "better": "low",
     "about": "logical error rate per syndrome round, 1-(1-LER)^(1/rounds), under the release's noise model"},
    {"name": "ler", "unit": "per experiment", "better": "low",
     "about": "logical error rate of the whole memory experiment (Monte Carlo, fixed seed, 95% interval in the report)"},
]
LER_BUDGET = {"decoder": "pymatching", "max_shots": 200000, "max_errors": 200, "seed": 20260928}

# GOAL 1 (the best QEC design under 200 ions, lowest LER): boards under qccd-noise@2 and the
# physics it is graded with (qccd/workspace/physics/qccd-physics@2.json, every number cited).
# They measure BOTH bases -- a Z-basis memory cannot see dephasing -- and rank time on the
# "quantinuum" table, the only one that physics declares.  Measured 2026-10-21 with qccdc
# c774b87b677f: surface5xz compiles on grid9x9 (6.0e-5 per round, 598 ms), deck_unit_cell and
# cyclone_base, and passes every rule.
PHYSICS_2 = REPO / "qccd" / "workspace" / "physics" / "qccd-physics@2.json"
GRID_9 = {"generator": "grid", "name": "grid9x9", "params": {"a": 9, "b": 9}}
GRID_5 = {"generator": "grid", "name": "grid5x5", "params": {"a": 5, "b": 5}}
#: Goal 1 is ONE leaderboard over every curated code that fits in 200 ions (the user, 2026-10-21):
#: entries of all these releases are ranked together by the logical error rate per round PER
#: LOGICAL QUBIT (`ler_per_round_per_logical`), X and Z both measured.  surface5xz_mem@1 predates
#: that metric; for its one logical qubit it equals `ler_per_round`.
MEMORY_2 = [
    # task, order, experiment key, title, description, starter, rank_by, LER budget
    ("surface5xz_mem", 8, "surface5xz", "Surface code memory, distance 5, both bases",
     "Five rounds of two distance-5 rotated surface-code patches in lockstep, one a Z memory and one an "
     "X memory (98 ions: 50 data, 48 ancillas, reset and reused every round), then the data read out, "
     "each patch in its own basis. Ranked by the logical error rate per round (a shot fails if either "
     "patch does) under qccd-noise@2, with Helios-class physics in which every number is cited.",
     None, "ler_per_round", None),
    ("surface3xz_mem", 9, "surface3xz", "Surface code memory, distance 3, both bases",
     "Three rounds of two distance-3 rotated surface-code patches in lockstep, one a Z memory and one an "
     "X memory (34 ions: 18 data, 16 ancillas, reset and reused every round), then the data read out, "
     "each patch in its own basis. Ranked with every Goal 1 code by the logical error rate per round per "
     "logical qubit under qccd-noise@2, with Helios-class physics in which every number is cited.",
     GRID_5, "ler_per_round_per_logical",
     {"decoder": "pymatching", "max_shots": 2000000, "max_errors": 400, "seed": 20261021}),
    # 194 ions, under the cap.  Measured 2026-10-21 with qccdc c774b87b677f: grid(12, 12) compiles it
    # in about 15 minutes (split rounds), passes every rule, 3.1e-5 per round, 1.64 s
    ("surface7xz_mem", 10, "surface7xz", "Surface code memory, distance 7, both bases",
     "Seven rounds of two distance-7 rotated surface-code patches in lockstep, one a Z memory and one an "
     "X memory (194 ions: 98 data, 96 ancillas, reset and reused every round), then the data read out, "
     "each patch in its own basis. Ranked with every Goal 1 code by the logical error rate per round per "
     "logical qubit under qccd-noise@2, with Helios-class physics in which every number is cited.",
     {"generator": "grid", "name": "grid12x12", "params": {"a": 12, "b": 12}}, "ler_per_round_per_logical",
     {"decoder": "pymatching", "max_shots": 2000000, "max_errors": 400, "seed": 20261021}),
]
#: what a 194-ion compile and its certificate need: about 15 minutes of split rounds
LIMITS_2 = {"surface7xz_mem": {"compile_timeout_s": 3600, "lean_timeout_s": 3600}}
PER_LOGICAL = {"name": "ler_per_round_per_logical", "unit": "per round per logical qubit", "better": "low",
               "about": "logical error rate per round divided by the logical qubits the experiment tests "
                        "(qccd.qec.experiments.logical_qubits): what Goal 1 ranks every code by"}
METRICS_2 = [
    {"name": "T_quantinuum", "unit": "ms", "better": "low",
     "about": "replayed round time under corrected_model('quantinuum')"},
    {"name": "instructions", "unit": "count", "better": "low", "about": "hardware instructions in the final program"},
    {"name": "dacs", "unit": "count", "better": "low", "about": "qccd.cost.hardware.hardware_report(arch).dacs"},
    {"name": "ions_per_instruction", "unit": "ions", "better": "high", "about": "mean ions moved per simd instruction"},
]
LER_BUDGET_2 = {"decoder": "pymatching", "max_shots": 2000000, "max_errors": 400, "seed": 20261021}


def main() -> int:
    base = find_release("ghz4@1")
    physics = base.physics()
    metrics = base.manifest["metrics"]
    for task, order, circuit, start, limits in BOARDS:
        if (RELEASES_DIR / f"{task}@1").exists():
            print(f"{task}@1 exists; left alone")
            continue
        info = json.loads((REPO / "tasks" / task / "task.json").read_text(encoding="utf-8"))
        text = (REPO / circuit).read_bytes().decode("utf-8")
        d = build_release(task, "1", text, physics, title=info["title"], description=info["description"],
                          out_dir=RELEASES_DIR, metrics=metrics, rank_by=base.manifest["rank_by"],
                          starter=start, limits=limits, order=order)
        rel = find_release(f"{task}@1")
        print(f"{rel.id}: {rel.title} -> {d} ({rel.digest[:23]}...)")
    from qccd.qec import get_experiment
    for task, order, key, title, description in MEMORY:
        if (RELEASES_DIR / f"{task}@1").exists():
            print(f"{task}@1 exists; left alone")
            continue
        exp = get_experiment(key)
        d = build_release(task, "1", exp.qasm, physics, title=title, description=description,
                          out_dir=RELEASES_DIR, metrics=metrics + LER_METRICS, rank_by="ler_per_round",
                          starter=RING_SMALL, order=order,
                          qec={"experiment": exp.name, "detectors": exp.spec(), "noise": "qccd-noise@1",
                               "ler": LER_BUDGET})
        rel = find_release(f"{task}@1")
        print(f"{rel.id}: {rel.title} -> {d} ({rel.digest[:23]}...)")
    physics_2 = json.loads(PHYSICS_2.read_text(encoding="utf-8"))
    for task, order, key, title, description, start, rank_by, budget in MEMORY_2:
        if (RELEASES_DIR / f"{task}@1").exists():
            print(f"{task}@1 exists; left alone")
            continue
        exp = get_experiment(key)
        per_logical = rank_by == "ler_per_round_per_logical"
        d = build_release(task, "1", exp.qasm, physics_2, title=title, description=description,
                          out_dir=RELEASES_DIR, metrics=METRICS_2 + LER_METRICS + ([PER_LOGICAL] if per_logical else []),
                          rank_by=rank_by, starter=start or GRID_9, order=order, rank_table="quantinuum",
                          limits=LIMITS_2.get(task),
                          tables=["quantinuum"],
                          qec={"experiment": exp.name, "detectors": exp.spec(), "noise": "qccd-noise@2",
                               "ler": budget or LER_BUDGET_2,
                               **({"logical_qubits": exp.logical_qubits, "n_ions": exp.n_data + exp.n_ancilla}
                                  if per_logical else {})})
        rel = find_release(f"{task}@1")
        print(f"{rel.id}: {rel.title} -> {d} ({rel.digest[:23]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
