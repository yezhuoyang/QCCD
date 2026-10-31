"""What code distance RSA-2048 needs on trapped ions: the tiled QCCD memory round under published
trapped-ion fidelities, fitted and solved against Gidney 2025's own error budget.

The circuit is the d = 25 machine's idle gadget: a stationary d x d patch, d rounds, compiled by
`qccd.compile.tiled_surface` on the physics@2 clock, X and Z memories.  Its noise is qccd-noise@2
with the device's fidelities replaced by each scenario's (everything not replaced stays Helios):

  helios  Quantinuum Helios 2025 as qccd-noise@2 / physics@2 pin it (arXiv:2511.05465):
          2Q 7.9e-4, 1Q 2.5e-5, reset 5e-4, memory 7.9e-3/s, readout crosstalk 2.1e-4 / 4.3e-5
  gate2q  Helios with only its two-qubit gate replaced by the "smooth gate" (arXiv:2510.17286):
          8.4e-5 in the ground state, <= 5e-4 up to n-bar 9.4, so 4.4e-5 per quantum
  best    the best published number for each operation, each from its own experiment
          (qccd/workspace/physics/qccd-physics-best.json): the smooth gate, 1Q 1.5e-7
          (arXiv:2412.04421), SPAM 7e-6 (arXiv:2409.05805, heralded) as the reset error; memory,
          readout crosstalk and the cooling floor stay Helios's.  At s = 1/2 it is the Shor
          machine's noise (`qccd.fullstack.scale.machine`)

`s` multiplies every probability of a scenario (`NoiseModel2.scale`): a scenario too clean to
sample at large d is also measured at s = 2, 4, and every scenario is fitted as
p(d, s) = A (s / s_th)^((d + 1) / 2), per patch per round, p = p_X + p_Z (Lambda = s_th / s).

The budget is Gidney 2025's (arXiv:2505.15917): 6.9e13 logical-qubit rounds a shot at d = 25,
with 93.3 % of shots surviving logical errors.  Every lattice-surgery step lasts d rounds, so a
shot at distance d is V(d) = 6.9e13 d / 25 rounds and needs V(d) p(d) <= -ln 0.933.

    python tools/distance_study.py build  [SCENARIO ...]   noisy circuits + budgets -> out/distance_study
    python tools/distance_study.py sample [WORKERS]        sinter, resumable (stats.csv)
    python tools/distance_study.py fit                     the fits and the distance each needs
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "out" / "distance_study"
sys.path.insert(0, str(REPO))

#: which primitives each scenario takes from qccd-physics-best (the rest stay physics@2's)
SCENARIOS = {
    "helios": (),
    "gate2q": ("ms_gate",),
    "best": ("ms_gate", "1q_gate", "reset"),
}
RUNS = [  # (scenario, s, distances)
    ("helios", 1.0, (3, 5, 7, 9)),
    ("helios", 0.5, (3, 5, 7)),
    ("gate2q", 1.0, (3, 5, 7)),
    ("gate2q", 2.0, (3, 5, 7)),
    ("best", 0.5, (3, 5)),          # the Shor machine's noise (qccd.fullstack.scale.machine)
    ("best", 1.0, (3, 5)),
    ("best", 2.0, (3, 5, 7)),
    ("best", 4.0, (3, 5, 7, 9)),
]
SHOTS = {3: 400_000_000, 5: 400_000_000, 7: 150_000_000, 9: 60_000_000}
V25, SURVIVAL = 6.9e13, 0.933
BEST = json.loads((REPO / "qccd" / "workspace" / "physics" / "qccd-physics-best.json").read_text(encoding="utf-8"))


def scenario_arch(doc: dict, name: str) -> dict:
    doc = json.loads(json.dumps(doc))
    for prim in SCENARIOS[name]:
        doc["primitives"][prim] = json.loads(json.dumps(BEST["primitives"][prim]))
    return doc


def build(only=None) -> None:
    from qccd.compile.cooling import insert_cooling
    from qccd.compile.tiled_surface import compile_tiled, device
    from qccd.cost.models import corrected_model
    from qccd.qec.experiments import rotated_surface
    from qccd.qec.extract import extract
    from qccd.qec.noise import NoiseModel2

    OUT.mkdir(parents=True, exist_ok=True)
    model = corrected_model("quantinuum")
    compiled, budgets = {}, []
    for scen, s, ds in RUNS:
        if only and scen not in only:
            continue
        for d in ds:
            for basis in "XZ":
                if (d, basis) not in compiled:
                    exp = rotated_surface(d, d, basis)
                    prog, cert, arch = compile_tiled(exp.qasm, d, arch=device(d, physics="physics@2"))
                    cooled = insert_cooling(prog, arch, model).program
                    cooled.meta.update(prog.meta)
                    compiled[d, basis] = (exp, cooled, cert, arch.to_json())
                exp, cooled, cert, doc = compiled[d, basis]
                t0 = time.time()
                ex = extract(cooled, scenario_arch(doc, scen), exp, noise=replace(NoiseModel2(), scale=s),
                             cert=cert)
                name = f"{scen}_s{s:g}_d{d}_{basis}"
                (OUT / f"{name}.stim").write_text(str(ex.circuit), encoding="utf-8")
                row = {"name": name, "scenario": scen, "s": s, "d": d, "basis": basis,
                       "budget": {k: round(v, 6) for k, v in ex.budget.items()},
                       "budget_per_data_round": round(sum(ex.budget.values()) / (d * d) / d, 7),
                       "build_s": round(time.time() - t0, 1)}
                budgets.append(row)
                print(json.dumps(row), flush=True)
    tag = "_".join(only) if only else "all"
    (OUT / f"budgets_{tag}.json").write_text(json.dumps(budgets, indent=1), encoding="utf-8")


def sample(workers: int) -> None:
    import sinter
    import stim

    tasks = []
    for p in sorted(OUT.glob("*.stim")):
        scen, s, d, basis = p.stem.split("_")
        d = int(d[1:])
        tasks.append(sinter.Task(circuit=stim.Circuit(p.read_text(encoding="utf-8")),
                                 json_metadata={"name": p.stem, "scenario": scen, "s": float(s[1:]),
                                                "d": d, "basis": basis},
                                 collection_options=sinter.CollectionOptions(max_shots=SHOTS[d],
                                                                             max_errors=300)))
    sinter.collect(num_workers=workers, tasks=tasks, decoders=["pymatching"],
                   save_resume_filepath=str(OUT / "stats.csv"), print_progress=True)


def distance_needed(A: float, lam: float, volume_x: float = 1.0, fixed_rounds: bool = False) -> int | None:
    """The smallest odd d whose shot keeps Gidney's survival; `fixed_rounds`: every merge keeps 25
    rounds whatever d (the paper-faithful count), so a shot is V25 rounds at every distance."""
    for d in range(3, 101, 2):
        v = volume_x * V25 * (1 if fixed_rounds else d / 25)
        if v * A * lam ** (-(d + 1) / 2) <= -math.log(SURVIVAL):
            return d
    return None


def fit() -> None:
    import numpy as np
    import sinter

    rows = defaultdict(dict)
    for st in sinter.read_stats_from_csv_files(str(OUT / "stats.csv")):
        m = st.json_metadata
        rows[m["scenario"], m["s"], m["d"]][m["basis"]] = st
    points = []
    for (scen, s, d), b in sorted(rows.items()):
        if set(b) != {"X", "Z"}:
            continue
        pr = {k: 1 - (1 - v.errors / v.shots) ** (1 / d) for k, v in b.items()}
        errs = {k: v.errors for k, v in b.items()}
        points.append({"scenario": scen, "s": s, "d": d, "p_round": pr["X"] + pr["Z"], "errors": errs,
                       "shots": {k: v.shots for k, v in b.items()}})
        print(f"{scen:7s} s={s:<4g} d={d}: p_round {pr['X'] + pr['Z']:.3g} "
              f"(X {pr['X']:.2g} on {errs['X']} errors, Z {pr['Z']:.2g} on {errs['Z']})")
    print()
    for scen in SCENARIOS:
        pts = [p for p in points if p["scenario"] == scen and min(p["errors"].values()) >= 10]
        if len(pts) < 3:
            continue
        X = np.array([[1.0, -(p["d"] + 1) / 2] for p in pts])
        y = np.array([math.log(p["p_round"]) - (p["d"] + 1) / 2 * math.log(p["s"]) for p in pts])
        (logA, log_sth), *_ = np.linalg.lstsq(X, y, rcond=None)
        A, lam = math.exp(logA), math.exp(log_sth)
        worst = max(math.exp(abs(e)) for e in y - X @ np.array([logA, log_sth]))
        need = distance_needed(A, lam)
        print(f"{scen}: p = {A:.3g} * {lam:.3g}^-((d+1)/2) over {len(pts)} points (worst point off by "
              f"x{worst:.2f}); d needed {need} (volume x0.1: {distance_needed(A, lam, 0.1)}, "
              f"x10: {distance_needed(A, lam, 10)}, x100: {distance_needed(A, lam, 100)})")
        if scen == "helios":
            print(f"helios, every error halved: Lambda {2 * lam:.3g}, d needed {distance_needed(A, 2 * lam)}")
        if scen == "best":
            lam2 = 2 * lam
            d = distance_needed(A, lam2, fixed_rounds=True)
            p = A * lam2 ** (-(d + 1) / 2)
            print(f"best at scale 1/2 (the Shor machine): Lambda {lam2:.3g}, d needed {distance_needed(A, lam2)}, "
                  f"{d} with 25 rounds a merge (p {p:.2g} per patch per round, "
                  f"{100 * (1 - math.exp(-V25 * p)):.1f} % of shots lost)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build(sys.argv[2:] or None)
    elif cmd == "sample":
        sample(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    elif cmd == "fit":
        fit()
    else:
        sys.exit(__doc__)
