"""A sparse state vector, for the circuits with T gates in them.

The 15-to-1 factory is the one gadget a stabilizer tableau cannot follow.  Its state is
sparse -- the encoder puts 5 pivots in |+>, so at most 2^5 basis states carry amplitude
until the decoder's Hadamards -- so a dictionary from basis state to amplitude simulates
it exactly and fast.  Measurements are post-selected on a stated outcome, returning the
probability of that branch; that is what "the factory accepts" means.
"""

from __future__ import annotations

import cmath
import math

from .circuit import Circuit

__all__ = ["SparseState", "run_postselected", "qubit_state"]

_R2 = 1 / math.sqrt(2)
_EPS = 1e-12


class SparseState:
    def __init__(self, n: int):
        self.n = n
        self.amp: dict[int, complex] = {0: 1.0 + 0j}

    def _prune(self, new):
        self.amp = {b: a for b, a in new.items() if abs(a) > _EPS}

    def h(self, q):
        m = 1 << q
        new: dict[int, complex] = {}
        for b, a in self.amp.items():
            s = a * _R2
            b0, b1 = b & ~m, b | m
            new[b0] = new.get(b0, 0) + s
            new[b1] = new.get(b1, 0) + (-s if b & m else s)
        self._prune(new)

    def phase(self, q, ph: complex):
        m = 1 << q
        self.amp = {b: (a * ph if b & m else a) for b, a in self.amp.items()}

    def x(self, q):
        m = 1 << q
        self.amp = {b ^ m: a for b, a in self.amp.items()}

    def cx(self, c, t):
        mc, mt = 1 << c, 1 << t
        self.amp = {(b ^ mt if b & mc else b): a for b, a in self.amp.items()}

    def cz(self, a_, b_):
        m = (1 << a_) | (1 << b_)
        self.amp = {b: (-a if b & m == m else a) for b, a in self.amp.items()}

    def prob(self, q, value: int) -> float:
        m = 1 << q
        return sum(abs(a) ** 2 for b, a in self.amp.items() if bool(b & m) == bool(value))

    def postselect(self, q, value: int) -> float:
        m = 1 << q
        p = self.prob(q, value)
        if p <= _EPS:
            self.amp = {}
            return 0.0
        norm = 1 / math.sqrt(p)
        self.amp = {b: a * norm for b, a in self.amp.items() if bool(b & m) == bool(value)}
        return p

    def reset(self, q):
        """Reset to |0>: exact when q is unentangled (the only use here), checked."""
        m = 1 << q
        p1 = self.prob(q, 1)
        if p1 <= _EPS:
            return
        zero = {b: a for b, a in self.amp.items() if not b & m}
        one = {b & ~m: a for b, a in self.amp.items() if b & m}
        p0 = 1 - p1
        if p0 > _EPS:
            # product with the rest iff the two branches are proportional
            k = next(iter(one))
            ratio = one[k] / zero[k] if k in zero else None
            if ratio is None or any(abs(one.get(b, 0) - ratio * a) > 1e-9 for b, a in zero.items()):
                raise ValueError("reset of an entangled qubit: not representable as a pure state")
            self.amp = {b: a / math.sqrt(p0) for b, a in zero.items()}
        else:
            self.amp = one


_PHASES = {"S": 1j, "S_DAG": -1j, "T": cmath.exp(1j * math.pi / 4),
           "T_DAG": cmath.exp(-1j * math.pi / 4), "Z": -1}


def run_postselected(circuit: Circuit, outcomes=None, *, inject=None) -> tuple[SparseState, float]:
    """Run from |0...0>; every measurement is post-selected on `outcomes[record]` (default 0).
    `inject(op_index, op)` may return `[(qubit, 'X'|'Y'|'Z')]` Paulis applied after the op.
    Returns the final state and the probability of the selected branch."""
    st = SparseState(circuit.n)
    p_total = 1.0
    rec = 0
    for k, op in enumerate(circuit.ops):
        name, t = op.name, op.targets
        if name == "H":
            for q in t:
                st.h(q)
        elif name in _PHASES:
            for q in t:
                st.phase(q, _PHASES[name])
        elif name == "X":
            for q in t:
                st.x(q)
        elif name == "Y":
            for q in t:
                st.x(q)
                st.phase(q, -1)
        elif name == "CX":
            for i in range(0, len(t), 2):
                st.cx(t[i], t[i + 1])
        elif name == "CZ":
            for i in range(0, len(t), 2):
                st.cz(t[i], t[i + 1])
        elif name == "R":
            for q in t:
                st.reset(q)
        elif name == "M":
            for q in t:
                want = (outcomes or {}).get(rec, 0)
                p_total *= st.postselect(q, want)
                rec += 1
        elif name == "I":
            pass
        else:
            raise ValueError(f"state vector: unsupported {name}")
        for q, p in (inject(k, op) if inject else ()) or ():
            if p in ("X", "Y"):
                st.x(q)
            if p in ("Z", "Y"):
                st.phase(q, -1)
    return st, p_total


def qubit_state(st: SparseState, q: int) -> tuple[complex, complex] | None:
    """The state of qubit q when every other qubit is in a definite basis state."""
    m = 1 << q
    rest = {b & ~m for b in st.amp}
    if len(rest) != 1:
        return None
    r = rest.pop()
    return st.amp.get(r, 0), st.amp.get(r | m, 0)
