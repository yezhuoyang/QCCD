"""Mutation test for the C1 comparator: prove it can fail.

507/507 circuits matching is only evidence if a mismatch would have been reported.  This
takes a circuit both front ends agree on, corrupts our side in ways a real front-end bug
would, and asserts the comparator rejects every one.

The mutations are chosen to be the actual failure modes of a QASM front end, not
arbitrary noise:

  operand_swap    `cx a,b` read as `cx b,a` -- the classic, and invisible in op counts
  drop_op         one instruction lost, e.g. a broadcast that emitted n-1
  extra_op        one instruction too many, e.g. a barrier flattened per qubit
  param_shift     an angle off by 1e-6 -- inside float noise, outside physical noise
  rename          a gate read as a different gate of the same arity
  wire_swap       right ops, wrong dependency order on one wire
  barrier_split   one barrier over k qubits read as k barriers over one

`barrier_split` is the one worth naming: it leaves the op *set* almost intact and changes
only the DAG, so a comparator that checked ops and edge counts but not per-wire order
would pass it.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_c1 import EXE, OCAML_ENV, compare  # noqa: E402

from qasm_oracle import to_canonical  # noqa: E402
from qiskit import qasm2  # noqa: E402


def _reindex(doc: dict) -> dict:
    """Rebuild `wires` and `edges` from `ops`, so a mutation stays self-consistent.

    Without this a mutation would be caught only because the derived fields no longer
    match the ops -- which tests nothing. The mutant must be a document a *plausible*
    buggy front end would have produced.
    """
    nq = doc["n_qubits"]
    for i, o in enumerate(doc["ops"]):
        o["i"] = i
    seqs: dict[int, list[int]] = {}
    for o in doc["ops"]:
        for w in list(o["qubits"]) + [nq + b for b in o["clbits"]]:
            seqs.setdefault(w, []).append(o["i"])
    doc["wires"] = [{"wire": w, "ops": seqs.get(w, [])}
                    for w in range(nq + doc["n_clbits"])]
    edges = set()
    for seq in seqs.values():
        for a, b in zip(seq, seq[1:]):
            edges.add((a, b))
    doc["edges"] = sorted([list(e) for e in edges])
    return doc


def operand_swap(d):
    for o in d["ops"]:
        if len(o["qubits"]) == 2:
            o["qubits"] = [o["qubits"][1], o["qubits"][0]]
            return _reindex(d)
    return None


def drop_op(d):
    if len(d["ops"]) < 2:
        return None
    del d["ops"][len(d["ops"]) // 2]
    return _reindex(d)


def extra_op(d):
    if not d["ops"]:
        return None
    d["ops"].insert(1, copy.deepcopy(d["ops"][0]))
    return _reindex(d)


def param_shift(d):
    for o in d["ops"]:
        if o["params"]:
            o["params"][0] = float(o["params"][0]) + 1e-6
            return d
    return None


def rename(d):
    swaps = {"x": "y", "y": "x", "h": "s", "cx": "cy", "cy": "cx", "cz": "cx",
             "s": "h", "t": "tdg", "swap": "cz"}
    for o in d["ops"]:
        if o["name"] in swaps:
            o["name"] = swaps[o["name"]]
            return d
    return None


def wire_swap(d):
    """Same ops, one wire's order reversed at a point: a pure DAG-order bug."""
    for w in d["wires"]:
        if len(w["ops"]) >= 2:
            w["ops"][0], w["ops"][1] = w["ops"][1], w["ops"][0]
            edges = set(tuple(e) for e in d["edges"])
            a, b = w["ops"][1], w["ops"][0]
            edges.discard((a, b))
            edges.add((b, a))
            d["edges"] = sorted([list(e) for e in edges])
            return d
    return None


def barrier_split(d):
    for i, o in enumerate(d["ops"]):
        if o["name"] == "barrier" and len(o["qubits"]) > 1:
            expanded = [dict(o, qubits=[q]) for q in o["qubits"]]
            d["ops"][i : i + 1] = expanded
            return _reindex(d)
    return None


MUTATIONS = {
    "operand_swap": operand_swap,
    "drop_op": drop_op,
    "extra_op": extra_op,
    "param_shift": param_shift,
    "rename": rename,
    "wire_swap": wire_swap,
    "barrier_split": barrier_split,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", default=str(HERE.parent / "bench"))
    args = ap.parse_args(argv)

    bench = Path(args.bench)
    # circuits chosen to have between them: 2Q gates, params, barriers, broadcasts
    subjects = ["broadcast_zoo", "ghz8", "qft6", "custom_gates", "clifford12"]

    caught = missed = skipped = 0
    misses: list[str] = []
    for stem in subjects:
        f = bench / f"{stem}.qasm"
        if not f.exists():
            continue
        out = Path(str(f) + ".tmp.json")
        r = subprocess.run([str(EXE), "parse", str(f), "-o", str(out)],
                           capture_output=True, text=True, env=OCAML_ENV)
        if r.returncode != 0:
            print(f"  {stem}: OCaml refused it: {r.stderr.strip()}")
            return 2
        ours = json.loads(out.read_text(encoding="utf-8"))
        out.unlink()
        oracle = to_canonical(
            qasm2.load(str(f), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS),
            stem)

        # sanity: the unmutated pair must agree, or the mutation test proves nothing
        if compare(ours, oracle):
            print(f"  {stem}: baseline does NOT match; mutation test is meaningless")
            return 2

        for mname, fn in MUTATIONS.items():
            mutant = fn(copy.deepcopy(ours))
            if mutant is None:
                skipped += 1
                continue
            if compare(mutant, oracle):
                caught += 1
            else:
                missed += 1
                misses.append(f"{stem}/{mname}")

    print(f"mutation test: {caught} caught, {missed} MISSED, "
          f"{skipped} not applicable to the subject")
    for m in misses:
        print(f"  MISSED: {m}")
    print("\nMUTATION PASS" if missed == 0 else "\nMUTATION FAIL")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
