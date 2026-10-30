"""Goal 3's fixed codes: Alex's syndrome circuits, checked, imported, written to `codes/`.

    python -I goals/goal3/make_codes.py --builders <dir with surface_code.py, bb_code.py,
                                                      color_code.py> [--only surface5 ...]

Each code is built in BOTH bases (a Z memory and an X memory, d rounds unless the table says
otherwise) by the builders Alex shared on 2026-10-29 (`circuits.zip`), with the two fixes
listed in `CODES.md` applied.  For each circuit:

1. stim must find every detector deterministic (a noisy detector error model builds);
2. the circuit distance is searched for (an upper bound from stim's search for an undetectable
   logical error; for the colour code arXiv:2603.28852 predicts d - floor((d+3)/6));
3. it is read into a `MemoryExperiment` (`qccd.qec.stim_import.from_stim`) and the import is
   proved equivalent (same detector error model under the same two-qubit noise);
4. `codes/<key>_<b>/` gets `circuit.qasm`, `detectors.json` (the `qccd.detectors` spec),
   `source.stim` and `meta.json` (sizes, distances, the checks, the builder digests).

Needs stim, numpy, scipy, qLDPC 0.2.4 and igraph (Alex's requirements.txt); the repo itself
does not, so this runs in its own environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

# key: (builder, distance, rounds, the code's name, [[n,k,d]])
CODES = {
    "surface3": ("surface", 3, 3, "rotated surface code", (9, 1, 3)),
    "surface5": ("surface", 5, 5, "rotated surface code", (25, 1, 5)),
    "surface7": ("surface", 7, 7, "rotated surface code", (49, 1, 7)),
    "color3": ("color", 3, 3, "6.6.6 colour code", (7, 1, 3)),
    "color5": ("color", 5, 5, "6.6.6 colour code", (19, 1, 5)),
    "color7": ("color", 7, 7, "6.6.6 colour code", (37, 1, 7)),
    "bb72": ("bb", 6, 6, "bivariate bicycle code", (72, 12, 6)),
    "bb108": ("bb", 10, 10, "bivariate bicycle code", (108, 8, 10)),
    "bb144": ("bb", 12, 12, "bivariate bicycle code (gross)", (144, 12, 12)),
}
#: circuit-distance searches above this many qubits are skipped (they take minutes)
SEARCH_MAX_QUBITS = 150


def noisy(c, p: float):
    """Uniform circuit noise for the checks: DEP2 after two-qubit gates, DEP1 after one-qubit
    gates, a flip before each measurement and after each reset."""
    import stim

    meas = {"M": "X_ERROR", "MX": "Z_ERROR", "MR": "X_ERROR", "MRX": "Z_ERROR"}
    reset = {"R": "X_ERROR", "RX": "Z_ERROR"}
    out = stim.Circuit()
    for ins in c.flattened():
        q = [t.value for t in ins.targets_copy() if t.is_qubit_target]
        if ins.name in meas:
            out.append(meas[ins.name], q, p)
            out.append(ins)
            if ins.name.startswith("MR"):
                out.append(meas[ins.name], q, p)
        elif ins.name in reset:
            out.append(ins)
            out.append(reset[ins.name], q, p)
        elif ins.name in ("CX", "CZ"):
            out.append(ins)
            out.append("DEPOLARIZE2", q, p)
        elif ins.name in ("H", "S", "S_DAG"):
            out.append(ins)
            out.append("DEPOLARIZE1", q, p)
        else:
            out.append(ins)
    return out


def circuit_distance(c) -> int | None:
    if c.num_qubits > SEARCH_MAX_QUBITS:
        return None
    return len(noisy(c, 1e-3).search_for_undetectable_logical_errors(
        dont_explore_detection_event_sets_with_size_above=4, dont_explore_edges_with_degree_above=4,
        dont_explore_edges_increasing_symptom_degree=False, canonicalize_circuit_errors=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--builders", required=True, type=Path)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--out", type=Path, default=HERE / "codes")
    a = ap.parse_args()
    sys.path.insert(0, str(a.builders))
    import stim
    from qldpc.objects import Pauli

    import bb_code
    import color_code
    import surface_code
    from qccd.qec.stim_import import check_equivalent, from_stim

    build = {"surface": surface_code.build_circuit, "bb": bb_code.build_circuit,
             "color": color_code.build_circuit}
    digests = {f: hashlib.sha256((a.builders / f).read_bytes()).hexdigest()
               for f in ("surface_code.py", "bb_code.py", "color_code.py")}
    ok = True
    for key, (b, d, rounds, family, nkd) in CODES.items():
        if a.only and key not in a.only:
            continue
        for basis, P in (("z", Pauli.Z), ("x", Pauli.X)):
            t0 = time.time()
            src = build[b](d, rounds, P)
            name = f"{key}_{basis}"
            exp = from_stim(src, name=f"{key}_r{rounds}_{basis}", code=f"{b}_{nkd[0]}_{nkd[1]}_{nkd[2]}",
                            distance=d, rounds=rounds, basis=basis.upper())
            problems = check_equivalent(src, exp)
            try:
                noisy(src, 1e-3).detector_error_model(allow_gauge_detectors=False)
                det = "deterministic"
            except ValueError as exc:
                det = "NOT deterministic: " + str(exc).splitlines()[0][:160]
            dist = circuit_distance(src)
            meta = {
                "key": name, "code": key, "family": family, "nkd": list(nkd), "basis": basis.upper(),
                "rounds": rounds, "ions": exp.n_data + exp.n_ancilla, "n_data": exp.n_data,
                "n_ancilla": exp.n_ancilla, "detectors": len(exp.detectors),
                "observables": len(exp.observables), "logical_qubits": exp.logical_qubits,
                "two_qubit_gates": sum(1 for ln in exp.qasm.splitlines() if ln.startswith(("cx ", "cz "))),
                "matchable": exp.matchable, "determinism": det,
                "circuit_distance_upper_bound": dist, "import_differences": problems,
                "builder": b, "builder_sha256": digests[f"{b}_code.py"],
                "seconds": round(time.time() - t0, 1),
            }
            o = a.out / name
            o.mkdir(parents=True, exist_ok=True)
            (o / "circuit.qasm").write_text(exp.qasm, encoding="utf-8", newline="\n")
            (o / "detectors.json").write_text(json.dumps(exp.spec(), indent=1), encoding="utf-8", newline="\n")
            (o / "source.stim").write_text(str(src) + "\n", encoding="utf-8", newline="\n")
            (o / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8", newline="\n")
            ok &= det == "deterministic" and not problems
            print(json.dumps({k: meta[k] for k in ("key", "ions", "two_qubit_gates", "determinism",
                                                   "circuit_distance_upper_bound", "import_differences",
                                                   "seconds")}), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
