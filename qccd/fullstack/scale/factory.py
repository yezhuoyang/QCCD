"""The magic-state factory of the stationary-tile machine (docs/FULLSTACK_PLAN.md, slice S9 step
4), as lattice instruction streams (`lattice.schedule` runs them).

**15-to-1** (`distill_15to1`): five patches in |+>; for each of the 15 nonzero v in F_2^4, a π/8
rotation of Z on the patches of supp(v) and the output patch; then the first four read in X,
and the output kept when all four read +1.  With no faults it accepts every time and leaves
T†|+>; every single and double fault in a magic state is caught, 35 sets of three are not --
the output fails with 35 p³ (`fault_census`, by state vector).

Each rotation is a teleported T: a fresh patch injected with |T> (`prep m T`:
`qccd.qec.experiments.injection`), the product of the rotation's Z with Z on it, and its X
readout.  A product of -1 turned the rotation the wrong way, so exp(-iπ/4 P) follows, by a
|Ȳ> (`prep y Y`: Gidney's in place, `y_memory`), the product with it and its X readout --
which is exact up to a Pauli.  What is left is Pauli frames, Z on the rotation's patches.
`run` executes a stream on a state vector, outcomes sampled, frames applied.
"""
from __future__ import annotations

import itertools
import random

import numpy as np

__all__ = ["distill_15to1", "run", "fault_census"]


def distill_15to1(tag: str = "f") -> tuple:
    """(stream, blocks): the 15-to-1 as lattice instructions on blocks `{tag}q0..q4` (q4 the
    output); fresh `{tag}m<j>` (|T>) and `{tag}y<j>` (|Ȳ>) per rotation; outcomes `{tag}r0..r3`
    are the four checks."""
    q = [f"{tag}q{i}" for i in range(5)]
    lines = [("prep", [b, "X"], None, []) for b in q]
    vs = [v for v in itertools.product((0, 1), repeat=4) if any(v)]
    for j, v in enumerate(vs):
        blocks = [q[i] for i in range(4) if v[i]] + [q[4]]
        m, y = f"{tag}m{j}", f"{tag}y{j}"
        a, b, c, e = (f"{tag}{x}{j}" for x in "abce")
        prod = [x for blk in blocks for x in ("Z", blk)]
        lines += [("prep", [m, "T"], None, []), ("ppm", prod + ["Z", m], a, []), ("read", [m, "X"], b, []),
                  ("prep", [y, "Y"], None, []), ("ppm", prod + ["Z", y], c, [a]), ("read", [y, "X"], e, [a])]
        for blk in blocks:
            lines += [("z", [blk], None, [b]), ("z", [blk], None, [a, c]), ("z", [blk], None, [a, e])]
    lines += [("read", [q[i], "X"], f"{tag}r{i}", []) for i in range(4)]
    return lines, q


# ---- a state vector over named qubits ----------------------------------------------------------

_H = np.array([[1, 1], [1, -1]]) / np.sqrt(2)
_Z = np.diag([1, -1])
_STATES = {"X": [1, 1], "Z": [1, 0], "Y": [1, 1j], "T": [1, np.exp(1j * np.pi / 4)]}


class _SV:
    def __init__(self):
        self.names: list = []
        self.psi = np.ones(1, dtype=complex)

    def add(self, name, vec):
        self.names.append(name)
        self.psi = np.kron(self.psi, np.asarray(vec, dtype=complex) / np.linalg.norm(vec))

    def gate(self, name, U):
        n, ax = len(self.names), self.names.index(name)
        t = np.moveaxis(np.tensordot(U, self.psi.reshape([2] * n), axes=([1], [ax])), 0, ax)
        self.psi = t.reshape(-1)

    def measure_z(self, names, rng) -> int:
        n, idx = len(self.names), np.arange(2 ** len(self.names))
        par = np.zeros_like(idx)
        for nm in names:
            par ^= (idx >> (n - 1 - self.names.index(nm))) & 1
        out = int(rng.random() < float(np.sum(np.abs(self.psi[par == 1]) ** 2)))
        self.psi = np.where(par == out, self.psi, 0)
        self.psi /= np.linalg.norm(self.psi)
        return out

    def drop(self, name):
        """Remove a qubit in a product state with the rest (measured, or never touched)."""
        n, ax = len(self.names), self.names.index(name)
        t = np.moveaxis(self.psi.reshape([2] * n), ax, 0)
        v = t[0] if np.linalg.norm(t[0]) > 1e-9 else t[1]
        self.names.pop(ax)
        self.psi = v.reshape(-1) / np.linalg.norm(v)


def run(stream, rng, faults=()) -> tuple:
    """(state vector, outcomes): the stream executed, outcomes sampled, Pauli frames applied as
    gates; a guarded instruction runs only when its guard holds (an ancilla whose readout is
    skipped was never touched, and is discarded).  `faults`: indices of `prep ... T` lines whose
    magic state is wrong by Z."""
    sv, out = _SV(), {}
    for k, (w, a, o, g) in enumerate(stream):
        if g and not all(out[x[1:]] == 0 if x.startswith("!") else out[x] for x in g):
            if w == "read":
                sv.drop(a[0])
            if w != "prep":
                continue
        if w == "prep":
            sv.add(a[0], _STATES[a[1]])
            if k in faults:
                sv.gate(a[0], _Z)
        elif w == "ppm":
            out[o] = sv.measure_z(a[1::2], rng)
        elif w == "read":
            if a[1] == "X":
                sv.gate(a[0], _H)
            out[o] = sv.measure_z([a[0]], rng)
            sv.drop(a[0])
        elif w == "z":
            sv.gate(a[0], _Z)
        else:
            raise ValueError(f"line {k}: {w} is not run here")
    return sv, out


def fault_census(trials: int = 200, seed: int = 1) -> dict:
    """The 15-to-1 by state vector: {"accepted": of `trials` faultless runs, "fidelity": the worst
    accepted output's with T†|+>, "caught": single magic-state faults always rejected (of 15)}."""
    stream, q = distill_15to1()
    rng = random.Random(seed)
    want = np.array([1, np.exp(-1j * np.pi / 4)]) / np.sqrt(2)
    checks = [f"fr{i}" for i in range(4)]
    acc, worst = 0, 1.0
    for _ in range(trials):
        sv, out = run(stream, rng)
        if not any(out[c] for c in checks):
            acc += 1
            worst = min(worst, abs(np.vdot(want, sv.psi)) ** 2)
    mags = [k for k, (w, a, _, _) in enumerate(stream) if w == "prep" and a[1] == "T"]
    caught = sum(all(any(run(stream, rng, (k,))[1][c] for c in checks) for _ in range(10)) for k in mags)
    return {"accepted": acc, "fidelity": worst, "caught": caught}
