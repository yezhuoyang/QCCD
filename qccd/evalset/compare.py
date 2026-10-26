"""When are two stim circuits the same experiment?  When they agree qubit by qubit.

Two operations on different qubits commute, so a circuit is fixed -- as a distribution of
measurement records -- by what happens to each qubit in order, together with the order of
its measurements.  `same_circuit(a, b)` checks exactly that:

* the same measurements, in the same order (the records the detectors point into);
* for every qubit, the same sequence of operations touching it: name, arguments (to a
  relative 1e-9) and, for a two-qubit operation, the other qubit;
* the same detectors and observables.

A noise operation of probability 0 is the identity and is skipped.  This is equality of
the circuits up to the order of operations on disjoint qubits (their Mazurkiewicz traces),
which implies they sample the same distribution; it is stronger than comparing their
detector error models, and much cheaper.
"""

from __future__ import annotations

from collections import defaultdict

__all__ = ["same_circuit", "qubit_traces"]

_PAIR = {"CX", "CY", "CZ", "SWAP", "ISWAP", "ISWAP_DAG", "SQRT_XX", "SQRT_XX_DAG", "SQRT_YY",
         "SQRT_YY_DAG", "SQRT_ZZ", "SQRT_ZZ_DAG", "DEPOLARIZE2", "PAULI_CHANNEL_2", "XCX", "XCY",
         "XCZ", "YCX", "YCY", "YCZ", "ZCX", "ZCY", "ZCZ", "CNOT", "MXX", "MYY", "MZZ"}
_MEASURE = {"M", "MZ", "MX", "MY", "MR", "MRX", "MRY", "MRZ"}
_NOISE = {"X_ERROR", "Y_ERROR", "Z_ERROR", "DEPOLARIZE1", "DEPOLARIZE2", "PAULI_CHANNEL_1",
          "PAULI_CHANNEL_2"}


def qubit_traces(circuit) -> tuple[dict, list, list]:
    """(qubit -> [(name, args, partner)], [(name, qubit)] measurements, [detector lines])."""
    per: dict[int, list] = defaultdict(list)
    meas: list = []
    decl: list = []
    for inst in circuit.flattened():
        name = inst.name
        args = tuple(inst.gate_args_copy())
        if name in ("DETECTOR", "OBSERVABLE_INCLUDE"):
            decl.append((name, args, tuple(t.value for t in inst.targets_copy())))
            continue
        if name in ("TICK", "QUBIT_COORDS", "SHIFT_COORDS"):
            continue
        if name in _NOISE and all(a == 0 for a in args):
            continue
        qs = [t.value for t in inst.targets_copy()]
        if name in _PAIR:
            for a, b in zip(qs[::2], qs[1::2]):
                per[a].append((name, args, b, 0))
                per[b].append((name, args, a, 1))
                if name in _MEASURE:
                    meas.append((name, a, b))
        else:
            for q in qs:
                per[q].append((name, args, None, 0))
                if name in _MEASURE:
                    meas.append((name, q))
    return per, meas, decl


def _close(a: tuple, b: tuple, rel: float) -> bool:
    if len(a) != len(b):
        return False
    return all(x == y or abs(x - y) <= rel * max(abs(x), abs(y)) for x, y in zip(a, b))


def same_circuit(a, b, *, rel: float = 1e-9) -> tuple[bool, str]:
    """(equal, the first difference in words)."""
    pa, ma, da = qubit_traces(a)
    pb, mb, db = qubit_traces(b)
    if ma != mb:
        k = next((i for i, (x, y) in enumerate(zip(ma, mb)) if x != y), min(len(ma), len(mb)))
        return False, (f"the measurements differ at record {k}: "
                       f"{ma[k] if k < len(ma) else 'none'} vs {mb[k] if k < len(mb) else 'none'}")
    if da != db:
        k = next((i for i, (x, y) in enumerate(zip(da, db)) if x != y), min(len(da), len(db)))
        return False, f"the detectors differ at declaration {k}"
    for q in sorted(set(pa) | set(pb)):
        xa, xb = pa.get(q, []), pb.get(q, [])
        for k, (u, v) in enumerate(zip(xa, xb)):
            if u[0] != v[0] or u[2] != v[2] or u[3] != v[3] or not _close(u[1], v[1], rel):
                return False, f"qubit {q}, operation {k}: {u} vs {v}"
        if len(xa) != len(xb):
            return False, f"qubit {q}: {len(xa)} operations vs {len(xb)}"
    return True, "the same operations on every qubit, in the same order"
