"""Circuit faults, all propagated at once, and the fewest that fool the detectors.

**The fault model** is the standard circuit-level one, location by location: after a
one-qubit gate, X, Y or Z on its qubit (3 faults); after a two-qubit gate, any of the 15
non-identity Paulis on its pair; a measurement reports the wrong bit (1); a reset leaves
|1> (X after it, 1).  Only operations marked `noisy` are fault locations; the ideal
encoders and decoders of a reference experiment are not.

**Propagation.**  Fault f is bit f of a Pauli frame.  Each qubit keeps two ints over all
faults, its X part and its Z part, and a Clifford gate updates them the way it conjugates
a Pauli: every fault moves through the circuit in the same few int operations.  A Z-basis
measurement's record flips for the faults whose frame has X on that qubit; an MPP flips
for the faults whose frame anticommutes with its product.

**Distance.**  A detector is a record parity that is deterministic without faults; an
observable is one that carries logical information.  A set of faults is an undetectable
logical error when it flips no detector and some observable.  One fault is, when its
detector signature is empty and its observable signature is not; two are, exactly when
their detector signatures are equal and their observable signatures differ.  So distance
at least 3 is decided by hashing signatures: no search.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tableau import NotClifford, bits

__all__ = ["Fault", "Propagation", "propagate", "signatures", "low_weight_logical"]

_P1 = ("X", "Y", "Z")
_P2 = [(a, b) for a in ("I", "X", "Y", "Z") for b in ("I", "X", "Y", "Z") if (a, b) != ("I", "I")]


@dataclass(frozen=True)
class Fault:
    op: int                     # index into circuit.ops
    kind: str                   # "gate" | "measure" | "reset" | "custom"
    qubits: tuple[int, ...]
    pauli: str                  # e.g. "XZ" for a two-qubit fault, "flip" for a measurement

    def describe(self, circuit) -> str:
        op = circuit.ops[self.op]
        names = [circuit.qubits[q] for q in self.qubits]
        where = f"instruction {op.source}" if op.source is not None else f"op {self.op}"
        if self.kind == "measure":
            return f"{where}: measurement of {names[0]} reports the wrong bit"
        if self.kind == "reset":
            return f"{where}: reset leaves {names[0]} in |1>"
        ps = " ".join(f"{p}({n})" for p, n in zip(self.pauli, names) if p != "I")
        return f"{where}: {ps} after {op.name}"


@dataclass
class Propagation:
    faults: list[Fault]
    #: per record: int over faults, the faults that flip it
    flips: list[int]
    #: per qubit, the frame each fault leaves at the end: (x ints, z ints)
    final_x: list[int]
    final_z: list[int]


def propagate(circuit, *, custom=None) -> Propagation:
    """Push every fault of the noisy operations to the end.

    `custom`, if given, replaces the standard model: a function `(op index, op)` returning
    a list of `(qubits, paulis)` faults to insert AFTER that operation (used for the T
    factory, where only Z faults after T gates matter and T itself is not Clifford).
    """
    n = circuit.n
    fx = [0] * n
    fz = [0] * n
    faults: list[Fault] = []
    flips: list[int] = []
    f = 0

    def inject(q, pauli, bit):
        if pauli in ("X", "Y"):
            fx[q] ^= bit
        if pauli in ("Z", "Y"):
            fz[q] ^= bit

    for k, op in enumerate(circuit.ops):
        name, t = op.name, op.targets
        live = op.noisy and custom is None
        if name == "M":
            for q in t:
                flip = fx[q]
                if live:
                    faults.append(Fault(k, "measure", (q,), "flip"))
                    flip ^= 1 << f
                    f += 1
                flips.append(flip)
                fz[q] = 0                     # a Z frame on a Z eigenstate is a phase
        elif name == "MPP":
            flip = 0
            for q, p in op.pauli:
                if p in ("X", "Y"):
                    flip ^= fz[q]
                if p in ("Z", "Y"):
                    flip ^= fx[q]
            flips.append(flip)
        elif name == "R":
            for q in t:
                fx[q] = fz[q] = 0
                if live:
                    faults.append(Fault(k, "reset", (q,), "X"))
                    fx[q] ^= 1 << f
                    f += 1
        elif name == "CX":
            for i in range(0, len(t), 2):
                c, tt = t[i], t[i + 1]
                fx[tt] ^= fx[c]
                fz[c] ^= fz[tt]
                if live:
                    for pa, pb in _P2:
                        bit = 1 << f
                        inject(c, pa, bit)
                        inject(tt, pb, bit)
                        faults.append(Fault(k, "gate", (c, tt), pa + pb))
                        f += 1
        elif name == "CZ":
            for i in range(0, len(t), 2):
                a, b = t[i], t[i + 1]
                fz[a] ^= fx[b]
                fz[b] ^= fx[a]
                if live:
                    for pa, pb in _P2:
                        bit = 1 << f
                        inject(a, pa, bit)
                        inject(b, pb, bit)
                        faults.append(Fault(k, "gate", (a, b), pa + pb))
                        f += 1
        elif name in ("H", "S", "S_DAG", "X", "Y", "Z", "I", "T", "T_DAG"):
            for q in t:
                if name == "H":
                    fx[q], fz[q] = fz[q], fx[q]
                elif name in ("S", "S_DAG"):
                    fz[q] ^= fx[q]
                elif name in ("T", "T_DAG") and fx[q]:
                    raise NotClifford("a fault with an X part reaches a T gate: its frame is "
                                      "no longer a Pauli")
                if live and name not in ("I", "T", "T_DAG"):
                    for p in _P1:
                        inject(q, p, 1 << f)
                        faults.append(Fault(k, "gate", (q,), p))
                        f += 1
        else:
            raise NotClifford(f"cannot propagate faults through {name}")
        if custom is not None:
            for qubits, paulis in custom(k, op) or ():
                bit = 1 << f
                for q, p in zip(qubits, paulis):
                    inject(q, p, bit)
                faults.append(Fault(k, "custom", tuple(qubits), "".join(paulis)))
                f += 1
    return Propagation(faults=faults, flips=flips, final_x=fx, final_z=fz)


def signatures(prop: Propagation, parities: list[int]) -> list[int]:
    """Per fault, the bitset of parities (record sets) it flips."""
    sig = [0] * len(prop.faults)
    for j, recs in enumerate(parities):
        acc = 0
        for r in bits(recs):
            acc ^= prop.flips[r]
        for f in bits(acc):
            sig[f] |= 1 << j
    return sig


def low_weight_logical(prop: Propagation, detectors: list[int], observables: list[int]):
    """The smallest undetectable logical error of weight 1 or 2, or None if there is none
    (the circuit's distance is then at least 3)."""
    dsig = signatures(prop, detectors)
    osig = signatures(prop, observables)
    for f, (d, o) in enumerate(zip(dsig, osig)):
        if not d and o:
            return [f]
    seen: dict[int, tuple[int, int]] = {}
    for f, (d, o) in enumerate(zip(dsig, osig)):
        prev = seen.get(d)
        if prev is None:
            seen[d] = (o, f)
        elif prev[0] != o:
            return [prev[1], f]
    return None
