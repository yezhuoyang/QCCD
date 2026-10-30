"""Contracts: what a gate built from places computes, checked exactly on logical states.

The place library verifies each op twice -- as hardware and as a logical channel (flows,
fault distance) -- and the flat sign-off shows a schedule runs exactly the Clifford program
its algorithm states.  What neither can say is whether a *program* of those ops is the gate
it is meant to be, because that is a statement about non-Clifford states: the factory's
magic state and every rotation built from it.  This module says it, on the states the places
are verified to produce (docs/ARCHITECTURE.md §6):

* the **primitive contracts** -- what each instruction does to one logical qubit per block:

      prep Z / X       |0⟩ / |+⟩
      inject           T†|+⟩: msf15's output, verified by state vector (GADGETS.md §9.2)
      s, sx            S and √X -- each itself a composite (a |Y⟩ block from the pharmacy, a
                       lattice surgery, a readout, a Pauli correction), checked below
      x, y, z          the Pauli
      cx               CNOT, the first block controls
      zz, xx           the Pauli product measured, one outcome per branch
      read Z / X       the block measured, and gone
      se, store, decode  nothing (the logical state does not change)

* a **gate's contract**: `gate NAME P... implements U:` -- run its body with every parameter
  Bell-paired with a reference qubit, in every branch of every measurement the body makes,
  with every guard evaluated on that branch's outcomes; every reachable branch must leave
  exactly (U ⊗ 1)|Φ⟩ on the parameters and references, up to a global phase, and every
  block the body created must be gone.  A call inside a body counts as its own contract --
  checked first -- which is what makes this compositional: a Toffoli of seven T gates is one
  branch, not 4⁷.
* a **program's semantics**: the same run on a whole program, calls as contracts, returning
  the probability of every outcome its top-level measurements can take.

Exact arithmetic would be overkill: amplitudes are complex floats, compared to 1e-9.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field

from ..algorithm import (CONTRACT_ARITY, Algorithm, Gate, _split, parse_algorithm,
                         parse_guard, rotation_basis)

__all__ = ["State", "Unitary", "UNITARIES", "check_gate", "check_gates", "check_pi4",
           "program_outcomes", "ContractError", "MAGIC_STATE"]

_R2 = 1 / math.sqrt(2)
_EPS = 1e-12
_TOL = 1e-9

#: the factory's output, as places.py verifies it: T†|+⟩
MAGIC_STATE = (_R2, cmath.exp(-1j * math.pi / 4) * _R2)
#: the pharmacy's inject.Y output: S H |0⟩ = |+i⟩
Y_STATE = (_R2, 1j * _R2)


class ContractError(ValueError):
    pass


# ============================================================================ the simulator

class State:
    """A sparse state over named qubits: basis bitmask -> amplitude."""

    def __init__(self):
        self.names: list[str] = []
        self.amp: dict[int, complex] = {0: 1.0 + 0j}

    def copy(self) -> "State":
        s = State()
        s.names = list(self.names)
        s.amp = dict(self.amp)
        return s

    def bit(self, q: str) -> int:
        try:
            return self.names.index(q)
        except ValueError:
            raise ContractError(f"no block {q} is alive") from None

    def add(self, q: str, a0: complex, a1: complex) -> None:
        if q in self.names:
            raise ContractError(f"block {q} already exists")
        k = len(self.names)
        self.names.append(q)
        new: dict[int, complex] = {}
        for b, a in self.amp.items():
            if abs(a0) > _EPS:
                new[b] = a * a0
            if abs(a1) > _EPS:
                new[b | (1 << k)] = a * a1
        self.amp = new

    def u1(self, q: str, m) -> None:
        """A 2x2 matrix m = ((m00, m01), (m10, m11)) on q."""
        k = 1 << self.bit(q)
        new: dict[int, complex] = {}
        for b, a in self.amp.items():
            hi = 1 if b & k else 0
            b0 = b & ~k
            for out in (0, 1):
                c = m[out][hi]
                if c != 0:
                    key = b0 | (k if out else 0)
                    new[key] = new.get(key, 0) + c * a
        self.amp = {b: a for b, a in new.items() if abs(a) > _EPS}

    def perm(self, f) -> None:
        """A basis permutation with phases: f(basis) -> (basis, phase)."""
        new: dict[int, complex] = {}
        for b, a in self.amp.items():
            nb, ph = f(b)
            new[nb] = new.get(nb, 0) + a * ph
        self.amp = {b: a for b, a in new.items() if abs(a) > _EPS}

    def cx(self, c: str, t: str) -> None:
        kc, kt = 1 << self.bit(c), 1 << self.bit(t)
        self.perm(lambda b: ((b ^ kt) if b & kc else b, 1))

    def measure_pauli(self, terms: dict[str, str], outcome: int) -> float:
        """Project on the (-1)^outcome eigenspace of a Z/X product; return the probability."""
        xs = [q for q, p in terms.items() if p == "X"]
        for q in xs:
            self.u1(q, H)
        mask = 0
        for q in terms:
            mask |= 1 << self.bit(q)
        keep = {b: a for b, a in self.amp.items() if (bin(b & mask).count("1") & 1) == outcome}
        p = sum(abs(a) ** 2 for a in keep.values())
        self.amp = {b: a / math.sqrt(p) for b, a in keep.items()} if p > _EPS else {}
        for q in xs:
            self.u1(q, H)
        return p

    def discard(self, q: str) -> None:
        """Remove q, which must be in |0⟩ or |1⟩ (it was just measured in Z)."""
        k = self.bit(q)
        vals = {(b >> k) & 1 for b in self.amp}
        if len(vals) > 1:
            raise ContractError(f"{q} is not in a basis state: it cannot be removed")
        new: dict[int, complex] = {}
        lo = (1 << k) - 1
        for b, a in self.amp.items():
            nb = (b & lo) | ((b >> (k + 1)) << k)
            new[nb] = new.get(nb, 0) + a
        self.amp = new
        self.names.pop(k)

    def overlap(self, other: "State") -> float:
        """|⟨other|self⟩|, with qubits matched by name."""
        if sorted(self.names) != sorted(other.names):
            raise ContractError(f"different blocks: {self.names} vs {other.names}")
        perm = [other.names.index(n) for n in self.names]

        def remap(b):
            out = 0
            for i, j in enumerate(perm):
                if b >> i & 1:
                    out |= 1 << j
            return out
        s = 0j
        for b, a in self.amp.items():
            s += other.amp.get(remap(b), 0).conjugate() * a
        return abs(s)


I2 = ((1, 0), (0, 1))
X = ((0, 1), (1, 0))
Y = ((0, -1j), (1j, 0))
Z = ((1, 0), (0, -1))
H = ((_R2, _R2), (_R2, -_R2))
S = ((1, 0), (0, 1j))
SDG = ((1, 0), (0, -1j))
T = ((1, 0), (0, cmath.exp(1j * math.pi / 4)))
TDG = ((1, 0), (0, cmath.exp(-1j * math.pi / 4)))
SX = ((0.5 + 0.5j, 0.5 - 0.5j), (0.5 - 0.5j, 0.5 + 0.5j))
SXDG = ((0.5 - 0.5j, 0.5 + 0.5j), (0.5 + 0.5j, 0.5 - 0.5j))

ONE_QUBIT = {"I": I2, "X": X, "Y": Y, "Z": Z, "H": H, "S": S, "Sdg": SDG, "T": T, "Tdg": TDG,
             "SX": SX, "SXdg": SXDG}


@dataclass
class Unitary:
    """A named gate on k blocks, as it acts on a State."""

    name: str

    def apply(self, st: State, qs: list[str]) -> None:
        n = self.name
        if n in ONE_QUBIT:
            st.u1(qs[0], ONE_QUBIT[n])
            return
        k = [1 << st.bit(q) for q in qs]
        if n == "CX":
            st.perm(lambda b: ((b ^ k[1]) if b & k[0] else b, 1))
        elif n == "CZ":
            st.perm(lambda b: (b, -1 if (b & k[0] and b & k[1]) else 1))
        elif n == "SWAP":
            st.perm(lambda b: (_swap(b, k[0], k[1]), 1))
        elif n == "CCX":
            st.perm(lambda b: ((b ^ k[2]) if (b & k[0] and b & k[1]) else b, 1))
        elif n == "CCZ":
            st.perm(lambda b: (b, -1 if (b & k[0] and b & k[1] and b & k[2]) else 1))
        elif n == "CSWAP":
            st.perm(lambda b: (_swap(b, k[1], k[2]) if b & k[0] else b, 1))
        else:
            raise ContractError(f"no unitary {n}")


def _swap(b, ka, kb):
    va, vb = bool(b & ka), bool(b & kb)
    if va == vb:
        return b
    return b ^ ka ^ kb


UNITARIES = {n: Unitary(n) for n in CONTRACT_ARITY}


# ============================================================================ running bodies

@dataclass
class Branch:
    state: State
    outcomes: dict[str, int] = field(default_factory=dict)
    prob: float = 1.0
    #: every measurement this branch took, in order: (what, outcome)
    path: list = field(default_factory=list)


def _primitive(st: State, word: str, args: list[str], outcome: int | None = None):
    """Apply one primitive's contract.  A measurement needs `outcome` and returns its
    probability; everything else returns None."""
    if word == "prep":
        q, basis = args[0], args[1].upper()
        rot = rotation_basis(basis)
        if rot:
            # a rotation source's ion, injected: RZn|+⟩ (or its inverse), qccd.gadget.leaves.rotation
            n, dg = rot
            st.add(q, _R2, _R2 * cmath.exp((-1j if dg else 1j) * math.pi / 2 ** n))
        else:
            st.add(q, *({"Z": (1, 0), "X": (_R2, _R2), "Y": Y_STATE}[basis]))
    elif word == "inject":
        st.add(args[0], *MAGIC_STATE)
    elif word == "s":
        st.u1(args[0], S)
    elif word == "sx":
        st.u1(args[0], SX)
    elif word == "ht":
        st.u1(args[0], H)
    elif word in ("x", "y", "z"):
        st.u1(args[0], {"x": X, "y": Y, "z": Z}[word])
    elif word == "cx":
        st.cx(args[0], args[1])
    elif word in ("zz", "xx"):
        p = "Z" if word == "zz" else "X"
        return st.measure_pauli({args[0]: p, args[1]: p}, outcome)
    elif word == "ppm":
        return st.measure_pauli({b: p.upper() for p, b in zip(args[0::2], args[1::2])}, outcome)
    elif word == "read":
        q, basis = args[0], args[1].upper()
        pr = st.measure_pauli({q: basis}, outcome)
        if pr > _EPS:
            if basis == "X":
                st.u1(q, H)
            st.discard(q)
        return pr
    elif word in ("se", "store", "decode"):
        pass
    else:
        raise ContractError(f"no contract for '{word}'")
    return None


MEASURES = ("zz", "xx", "ppm", "read")


def run_lines(lines: list[tuple[int, str]], start: State, gates: dict[str, Gate], *,
              rename: dict[str, str] | None = None, what: str = "the program",
              check_contracts: bool = True) -> list[Branch]:
    """Every reachable branch of `lines` from `start`.  A call is applied as its gate's
    contract (the gate must name one)."""
    rename = dict(rename or {})
    branches = [Branch(start)]
    for lineno, text in lines:
        sp = _split(text)
        if sp is None:
            continue
        g_text, word, args, cvar, _at = sp
        if word == "title":
            continue
        guard = parse_guard(g_text) if g_text else None
        args = [rename.get(a, a) for a in args]
        out: list[Branch] = []
        for br in branches:
            if guard is not None and not guard.holds(br.outcomes):
                out.append(br)
                continue
            if word in gates:
                g = gates[word]
                if g.contract is None:
                    raise ContractError(f"{what} calls {word}, which names no contract "
                                        f"(write `gate {word} ... implements U:`)")
                UNITARIES[g.contract].apply(br.state, args)
                out.append(br)
                continue
            if word == "bit":
                # a bit the archive computes: this branch's outcomes decide it
                from ..algorithm import bit_value, parse_bit
                oc = dict(br.outcomes)
                oc[cvar] = bit_value(parse_bit(" ".join(args)), br.outcomes)
                out.append(Branch(br.state, oc, br.prob, br.path))
                continue
            if word in MEASURES:
                for o in (0, 1):
                    st = br.state.copy()
                    p = _primitive(st, word, args, o)
                    if p is not None and p > _TOL:
                        oc = dict(br.outcomes)
                        if cvar:
                            oc[cvar] = o
                        out.append(Branch(st, oc, br.prob * p, br.path + [(text, o)]))
                continue
            _primitive(br.state, word, args)
            out.append(br)
        branches = out
    return branches


def _bell_start(params: list[str]) -> State:
    st = State()
    for p in params:
        st.add(f"ref·{p}", _R2, _R2)
        st.add(p, 1, 0)
        st.cx(f"ref·{p}", p)
    return st


def check_gate(gate: Gate, gates: dict[str, Gate]) -> dict:
    """Is `gate`'s body its contract, in every branch?  A report, never an exception for a
    gate that is merely wrong."""
    if gate.contract is None:
        return {"gate": gate.name, "contract": None, "passed": None,
                "why": "the gate names no contract"}
    params = list(gate.params)
    target = _bell_start(params)
    UNITARIES[gate.contract].apply(target, params)
    try:
        branches = run_lines(gate.body, _bell_start(params), gates, what=f"gate {gate.name}")
    except ContractError as exc:
        return {"gate": gate.name, "contract": gate.contract, "passed": False, "why": str(exc),
                "branches": []}
    rows, ok = [], True
    total = 0.0
    for br in branches:
        total += br.prob
        left = [n for n in br.state.names if n not in target.names]
        if left:
            ok = False
            rows.append({"outcomes": br.outcomes, "prob": round(br.prob, 6), "ok": False,
                         "why": f"the body leaves {', '.join(left)} alive"})
            continue
        f = br.state.overlap(target)
        good = abs(f - 1) < 1e-7
        ok &= good
        rows.append({"outcomes": br.outcomes, "prob": round(br.prob, 6), "ok": good,
                     "fidelity": round(f, 9)})
    return {"gate": gate.name, "contract": gate.contract, "params": params,
            "passed": ok and abs(total - 1) < 1e-7, "branches": rows,
            "probability": round(total, 9)}


def check_gates(alg_or_gates) -> list[dict]:
    """Every gate of a program (or a dict of gates), in definition order."""
    gates = alg_or_gates.gates if isinstance(alg_or_gates, Algorithm) else dict(alg_or_gates)
    return [check_gate(g, gates) for g in gates.values()]


def check_pi4(kind: str) -> dict:
    """The S̄ and √X̄ gadgets, as composites of places: a |Ȳ⟩ block (the pharmacy's
    inject.Y), a Z̄Z̄ (X̄X̄) lattice surgery, an X̄ (Z̄) readout of the |Ȳ⟩ block, and the Pauli
    correction the scheduler writes into the frame -- Z̄^(m_zz ⊕ m_x) for S̄,
    X̄^(1 ⊕ m_xx ⊕ m_z) for √X̄.  Every branch must be S (√X) exactly."""
    meas, rb, corr, const, U = (("zz", "X", "z", 0, "S") if kind == "s" else
                                ("xx", "Z", "x", 1, "SX"))
    st = _bell_start(["q"])
    target = _bell_start(["q"])
    UNITARIES[U].apply(target, ["q"])
    st.add("y", *Y_STATE)
    rows, ok, total = [], True, 0.0
    for a in (0, 1):
        for b in (0, 1):
            s = st.copy()
            p1 = s.measure_pauli({"q": "Z" if kind == "s" else "X",
                                  "y": "Z" if kind == "s" else "X"}, a)
            p2 = s.measure_pauli({"y": rb}, b) if p1 > _EPS else 0.0
            if p1 * p2 <= _TOL:
                continue
            if rb == "X":
                s.u1("y", H)
            s.discard("y")
            if const ^ a ^ b:
                s.u1("q", {"z": Z, "x": X}[corr])
            f = s.overlap(target)
            good = abs(f - 1) < 1e-7
            ok &= good
            total += p1 * p2
            rows.append({"outcomes": {"m_" + meas: a, "m_" + rb.lower(): b},
                         "prob": round(p1 * p2, 6), "ok": good, "fidelity": round(f, 9)})
    return {"gate": kind, "contract": U, "passed": ok and abs(total - 1) < 1e-7,
            "branches": rows, "correction": f"{corr.upper()}̄ when {const} ⊕ m_{meas} ⊕ "
                                             f"m_{rb.lower()} = 1"}


def program_outcomes(alg: Algorithm | str, *, outcomes: list[str] | None = None) -> dict:
    """The probability of every assignment of a program's top-level outcomes, every call
    counted as its contract.  `outcomes` picks which outcomes to report (default: all)."""
    if isinstance(alg, str):
        alg = parse_algorithm(alg)
    top = [(i + 1, ln) for i, ln in enumerate(alg.source.splitlines())]
    body: list[tuple[int, str]] = []
    skip = False
    for n, ln in top:
        stripped = ln.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if stripped.startswith("gate "):
            skip = True
            continue
        if skip and stripped[:1].isspace():
            continue
        skip = False
        if stripped.strip().startswith(("include ", "title", "floorplan ")):
            continue
        body.append((n, stripped))
    branches = run_lines(body, State(), alg.gates, what=alg.name)
    dist: dict[tuple, float] = {}
    keys = outcomes
    for br in branches:
        if keys is None:
            keys = sorted(br.outcomes)
        k = tuple(br.outcomes.get(x) for x in keys)
        dist[k] = dist.get(k, 0.0) + br.prob
    return {"outcomes": keys or [], "distribution": {k: round(v, 9) for k, v in sorted(dist.items())},
            "branches": len(branches)}
