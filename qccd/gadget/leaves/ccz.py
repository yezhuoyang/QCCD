"""`msccz`: the |AutoCCZ> factory.  One CCZ state distilled from eight T states, with the three
routing pairs that make its Clifford fix-ups measurement bases.

Gidney and Ekera's Shor (FormalRV/Audit/GidneyEkera2021) spends one |AutoCCZ> state per
Toffoli: `1 magic |AutoCCZ> -> LogicalAncilla[s, s+9)` -- nine sites, FormalRV's own words
(compiler/lib/prog_to_qppm/ppmc.ml, `auto_ccx`): "the CCZ state x y z at s, s+1, s+2 and one
routing pair per possible CZ fix-up, (s+3, s+4) for (b,t), (s+5, s+6) for (a,t), (s+7, s+8)
for (a,b), each prepared as CZ|++> and CNOT-linked from its two state qubits."  No place made
a CCZ state before this one (the user, 2026-10-29: a new CCZ factory, not CCZ from T states).

**The device** is the 15-to-1 factory's ring (`msf15`): 20 slots, one gate dock.  Five ions
live on it (the distillation's detectors); nine fresh ions come in through `in`, become the
nine sites, and leave through `out` holding the state, in FormalRV's site order.

**The protocol is a program**, `protocols/autoccz.circ`, compiled operation by operation by the
same ring compiler as the T factory (`ring_circuit`), every TSIR gate, reset and measurement
naming its line:

    8T -> CCZ on the [[8,3,2]] cube code: encode |+++> (RM(1,3), pivots 0 1 2 4), T on the
    even vertices and T-dagger on the odd (logical CCZ), decode, undo pivot 0's X link and read
    it in X; the five detectors all 0 is the post-selection; qubits 1, 2, 4 hold CCZ|+++>
    routing: three pairs CZ|++>, each CNOT-linked from its two state qubits

**What is checked** (the op's logic check, on the circuit read back off the compiled TSIR):

* post-selected on the five detectors (probability 1 with ideal gates), the nine visitors hold
  exactly the |AutoCCZ> state;
* a Z fault after any one of the eight T gates always shows in the detectors, and every pair
  does not (distance 2: output error 28 p^2, the 8T -> CCZ protocol's figure);
* the TSIR carries out `autoccz.circ` operation for operation, in its order;
* the state is the one FormalRV's Toffoli consumes: running FormalRV's `auto_ccx` lowering on
  it -- three surgeries, three X readouts, each routing pair read in Z or X by its choice
  record, every frame -- with the data maximally entangled with a reference, every one of the
  4,096 branches leaves exactly CCX(a, b, t) (with the pairs' links crossed, none does).

Not checked: errors on the Clifford gates, which distillation assumes far below the T error.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..model import Port
from .bay import BayProgram, bay
from .factory import PROTOCOLS, read_circuit, ring_circuit, source_order

__all__ = ["ccz_factory", "read_outputs", "autoccz_vector", "auto_ccx_check", "EXTRA_PLACES"]

SOURCE = "autoccz.circ"


def read_outputs(path) -> list[int]:
    """The `output` line of a protocol: the qubits that hold its result, in order."""
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        t = raw.split("#", 1)[0].split()
        if t and t[0] == "output":
            return [int(x) for x in t[1:]]
    raise ValueError(f"{path}: no output line")


# --------------------------------------------------------------------------- the state

_H = np.array([[1, 1], [1, -1]], dtype=complex) / np.sqrt(2)
_P = {"X": np.array([[0, 1], [1, 0]], dtype=complex), "Z": np.diag([1, -1]).astype(complex)}


class _Vec:
    """A dense state on n qubits, qubit k the k-th axis."""

    def __init__(self, n: int):
        self.n = n
        self.v = np.zeros((2,) * n, dtype=complex)
        self.v[(0,) * n] = 1

    def g1(self, U, q):
        self.v = np.moveaxis(np.tensordot(U, self.v, axes=([1], [q])), 0, q)

    def cx(self, c, t):
        idx = [slice(None)] * self.n
        idx[c] = 1
        self.v[tuple(idx)] = np.flip(self.v[tuple(idx)], axis=t if t < c else t - 1)

    def cz(self, *qs):
        idx = [slice(None)] * self.n
        for q in qs:
            idx[q] = 1
        self.v[tuple(idx)] *= -1

    def pauli(self, ps: dict):
        for q, p in ps.items():
            self.g1(_P[p], q)

    def project(self, ps: dict, bit: int) -> float:
        other = _Vec(self.n)
        other.v = self.v.copy()
        other.pauli(ps)
        new = (self.v + (1 if bit == 0 else -1) * other.v) / 2
        p = float(np.vdot(new, new).real)
        if p > 1e-12:
            self.v = new / np.sqrt(p)
        return p


def _prepare_autoccz(st: _Vec, sites: list[int]) -> None:
    x, y, z = sites[:3]
    for q in (x, y, z):
        st.g1(_H, q)
    st.cz(x, y, z)
    for (k1, k2), (su, sv) in (((sites[3], sites[4]), (y, z)), ((sites[5], sites[6]), (x, z)),
                               ((sites[7], sites[8]), (x, y))):
        st.g1(_H, k1)
        st.g1(_H, k2)
        st.cz(k1, k2)
        st.cx(su, k1)
        st.cx(sv, k2)


def autoccz_vector() -> np.ndarray:
    """The |AutoCCZ> state on its nine sites (x y z s3 .. s8), as a 512-vector."""
    st = _Vec(9)
    _prepare_autoccz(st, list(range(9)))
    return st.v.reshape(-1)


def auto_ccx_check() -> dict:
    """FormalRV's Toffoli lowering through one |AutoCCZ> state (ppmc.ml `auto_ccx`, as data),
    run branch by branch on the data a, b, t maximally entangled with a reference: is every
    branch exactly CCX(a, b, t)?"""
    REF, A, B, T = (0, 1, 2), 3, 4, 5
    S = list(range(6, 15))
    st = _Vec(15)
    for q in range(3):
        st.g1(_H, REF[q])
        st.cx(REF[q], 3 + q)
    _prepare_autoccz(st, S)
    fa, fb, ft = {A: "Z"}, {B: "Z"}, {T: "X"}

    def anc(p, i):
        return {S[i]: p}

    def pair(choice, u, pu, v, pv, k1, k2, r):
        return [("cond", choice, 1, [("meas", anc("Z", k1), r), ("meas", anc("Z", k2), r + 2),
                                     ("cond", r + 2, 1, [("frame", u)]), ("cond", pv, 1, [("frame", u)]),
                                     ("cond", r, 1, [("frame", v)]), ("cond", pu, 1, [("frame", v)])]),
                ("cond", choice, 0, [("meas", anc("X", k1), r + 1), ("meas", anc("X", k2), r + 3),
                                     ("cond", r + 1, 1, [("frame", u)]), ("cond", r + 3, 1, [("frame", v)])])]
    prog = ([("meas", {**fa, **anc("Z", 0)}, 0), ("meas", {**fb, **anc("Z", 1)}, 1),
             ("meas", {**ft, **anc("Z", 2)}, 2),
             ("meas", anc("X", 0), 3), ("meas", anc("X", 1), 4), ("meas", anc("X", 2), 5)]
            + pair(0, fb, 1, ft, 2, 3, 4, 6) + pair(1, fa, 0, ft, 2, 5, 6, 10) + pair(2, fa, 0, fb, 1, 7, 8, 14)
            + [("cond", 0, 1, [("cond", 1, 1, [("frame", ft)])]), ("cond", 0, 1, [("cond", 2, 1, [("frame", fb)])]),
               ("cond", 1, 1, [("cond", 2, 1, [("frame", fa)])])]
            + [("cond", 3, 1, [("frame", fa)]), ("cond", 4, 1, [("frame", fb)]), ("cond", 5, 1, [("frame", ft)])])
    tgt = _Vec(6)
    for q in range(3):
        tgt.g1(_H, q)
        tgt.cx(q, 3 + q)
    tgt.g1(_H, 5)
    tgt.cz(3, 4, 5)
    tgt.g1(_H, 5)
    tv = tgt.v.reshape(64)
    leaves: list = []

    def run(ops, s, rec, p):
        if not ops:
            leaves.append((s, p))
            return
        op, rest = ops[0], ops[1:]
        if op[0] == "frame":
            s.pauli(op[1])
            run(rest, s, rec, p)
        elif op[0] == "cond":
            run((op[3] + rest) if rec[op[1]] == op[2] else rest, s, rec, p)
        else:
            for bit in (0, 1):
                s2 = _Vec(s.n)
                s2.v = s.v.copy()
                pb = s2.project(op[1], bit)
                if pb > 1e-12:
                    run(rest, s2, {**rec, op[2]: bit}, p * pb)
    run(prog, st, {}, 1.0)
    worst = 1.0
    for s, _ in leaves:
        v = s.v.reshape(64, 512)
        rho = v @ v.conj().T
        worst = min(worst, float(np.real(tv.conj() @ rho @ tv)))
    total = sum(p for _, p in leaves)
    return {"kind": "consumption", "branches": len(leaves), "probability": round(total, 9),
            "worst_fidelity": round(worst, 9), "passed": abs(total - 1) < 1e-9 and worst > 1 - 1e-9,
            "claim": f"FormalRV's Toffoli lowering through this state is CCX(a, b, t) in all {len(leaves)} branches"}


# --------------------------------------------------------------------------- the place


def ccz_factory(*, name: str = "msccz"):
    from ..logic.faults import propagate
    from ..logic.statevec import run_postselected
    from .places import KEEP, _cooled, _master

    W = 10
    b = bay(name, W, [1], {"in": "BL", "out": "TR"}, {"in": 9, "out": 9})
    gates, lines, _ = read_circuit(PROTOCOLS / SOURCE)
    outs = read_outputs(PROTOCOLS / SOURCE)
    n = 1 + max(q for g in gates for q in g[1:])
    names = [f"m{outs.index(i)}" if i in outs else f"s{i}" for i in range(n)]
    visitors = [f"m{j}" for j in range(len(outs))]
    staff = [nm for nm in names if nm not in visitors]
    slot_of = {nm: s for nm, s in zip(names, range(2, 2 + n))}
    home = {nm: f"S{slot_of[nm]}" for nm in staff}
    from .common import LeafBuild
    build = LeafBuild(master=None, machine=b.machine, home=dict(home),
                      roles={**{s: "magic" for s in staff}, **{m: "magic" for m in visitors}})
    key = "produce"
    bp = BayProgram(b, f"{name}.{key}", {**home, **{m: "X_in" for m in visitors}})
    bp.absorb("in", visitors, [slot_of[m] for m in visitors], cool_every=3, op="enter")
    bp.restore(op="enter")
    ring_circuit(bp, names, gates, slot="S1", dock="A1", op="produce", source=SOURCE, lines=lines)
    bp.restore(op="produce")
    bp.emit("out", visitors, cool_every=3, op="leave")
    bp.restore(op="leave")
    build.programs[key] = _cooled(bp.prog, b.machine)
    build.sources[key] = {"name": SOURCE, "text": (PROTOCOLS / SOURCE).read_text(encoding="utf-8")}
    build.titles[key] = "8T -> CCZ distillation and three routing pairs; nine visitors leave holding |AutoCCZ>"
    build.expect_end[key] = {**home, **{m: "X_out" for m in visitors}}
    n_cx = sum(1 for g in gates if g[0] == "CX")
    build.notes[key] = [
        f"{n_cx} CX, 8 T or T-dagger, 5 detector measurements at one dock trap, operation by operation",
        "checked: post-selected on the detectors the visitors hold |AutoCCZ> exactly; one T fault "
        "always shows (distance 2, output error 28p^2); FormalRV's Toffoli through the state is "
        "CCX in every branch",
        "not checked: Clifford-gate errors, which distillation assumes far below the T error"]
    target = autoccz_vector()

    def check(circ):
        rep = {"kind": "statevector"}
        st, p_acc = run_postselected(circ)
        qs = [circ.index[m] for m in visitors]
        # the nine visitors' state, every other ion in a definite basis state
        mask = sum(1 << q for q in qs)
        rest = {bb & ~mask for bb in st.amp}
        vec = None
        if len(rest) == 1:
            r = rest.pop()
            vec = np.zeros(2 ** len(qs), dtype=complex)
            for bb, a in st.amp.items():
                # site j is the j-th axis of `target` (most significant first)
                k = sum(((bb >> q) & 1) << (len(qs) - 1 - j) for j, q in enumerate(qs))
                vec[k] += a if (bb & ~mask) == r else 0
        fid = float(abs(np.vdot(target, vec / np.linalg.norm(vec))) ** 2) if vec is not None else 0.0
        rep.update(accept=round(p_acc, 12), output="|AutoCCZ>", fidelity=round(fid, 12))
        tq = [(k, t) for k, op in enumerate(circ.ops) if op.name in ("T", "T_DAG") for t in op.targets]

        def custom(k, op):
            return [((t,), ("Z",)) for kk, t in tq if kk == k]
        prop = propagate(circ, custom=custom)
        syn = []
        for f in range(len(prop.faults)):
            s = 0
            for r_, flips in enumerate(prop.flips):
                if (flips >> f) & 1:
                    s |= 1 << r_
            syn.append(s)
        nT = len(syn)
        d1 = sum(1 for s in syn if s)
        d2 = sum(1 for i in range(nT) for j in range(i + 1, nT) if syn[i] ^ syn[j])
        rep.update(t_gates=nT, detected_1=d1, detected_2=d2, pairs=nT * (nT - 1) // 2)
        order = source_order(circ, names, gates, lines, SOURCE)
        rep["source_order"] = order
        cons = auto_ccx_check()
        rep["consumption"] = cons
        rep["passed"] = bool(abs(p_acc - 1) < 1e-9 and fid > 1 - 1e-9 and d1 == nT == 8
                             and order["passed"] and cons["passed"])
        rep["claim"] = (f"carries out {SOURCE} operation by operation ({order['operations']} operations, "
                        f"each naming its line); accepts with probability {rep['accept']:.6g} and the nine "
                        f"visitors hold |AutoCCZ> exactly; every single T-gate Z error detected "
                        f"({d2} of {rep['pairs']} pairs: distance 2); {cons['claim']}"
                        if order["passed"] else order["claim"])
        return rep

    build.logic_checks[key] = check
    from .common import tidy
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="cczfactory", title="AutoCCZ factory (8T -> CCZ)",
                   doc="five resident ions on a 20-slot ring with one gate dock; nine visiting ions "
                       "become the nine sites of an |AutoCCZ> state each run",
                   ports=[Port("in", "in", "fresh", 9, "W", 0.8, node=b.trap("in"), stub=b.stub("in")),
                          Port("out", "out", "magic", 9, "E", 0.2, node=b.trap("out"), stub=b.stub("out"))],
                   inventory={"magic": len(staff)}, capacity=n,
                   params={"ring_slots": b.N, "protocol": "8T -> CCZ on the [[8,3,2]] code, with "
                                                         "AutoCCZ routing pairs (Gidney & Fowler 2019)",
                           "sites": len(visitors)},
                   size=b.size())


EXTRA_PLACES = {"msccz": ccz_factory}
