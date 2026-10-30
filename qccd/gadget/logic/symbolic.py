"""What a gadget program computes, exactly, without enumerating its branches.

A program lowered from FormalRV's `.qec` is Clifford with Pauli feedback: blocks are prepared in
|0̄⟩, |+̄⟩ or |Ȳ⟩, Pauli products are measured, and every classically controlled operation is a
Pauli (a frame).  So it is simulated here as a stabilizer state of one qubit per block whose
signs are AFFINE GF(2) EXPRESSIONS of the random measurement outcomes: a random outcome is a
fresh variable, a deterministic one is an expression in earlier variables, and a Pauli applied
under a condition `m ⊕ k ⊕ …` flips the signs it anticommutes with by that expression.

The result is exact for every branch at once -- 2^25 branches of Deutsch-Jozsa are one run --
which is what lets a spec like `Pr[ c[0,n) = a ] = 1` be decided rather than sampled: the
final outcomes' expressions must be the constants `a` names.  (`logic.contract` enumerates
branches with a state vector instead; it handles T states, and is right for small programs.)

Not handled, and refused by name: non-Clifford preparations (`inject`, a T state), guards that
are conjunctions (`a & b`: not affine).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..algorithm import Algorithm


class NotClifford(ValueError):
    pass


def _xor(a: frozenset, b: frozenset) -> frozenset:
    """Affine expressions are frozensets of variables; the constant 1 is the variable '1'."""
    return a ^ b


ONE = frozenset({"1"})
ZERO = frozenset()


@dataclass
class Gen:
    x: int
    z: int
    s: frozenset = ZERO                    # the sign: (-1)^s, s an affine expression


def _g(x1, z1, x2, z2) -> int:
    """Aaronson-Gottesman: the power of i when a Pauli (x1,z1) multiplies (x2,z2) on one qubit."""
    if not x1 and not z1:
        return 0
    if x1 and z1:
        return z2 - x2
    if x1 and not z1:
        return z2 * (2 * x2 - 1)
    return x2 * (1 - 2 * z2)


def _mul(a: Gen, b: Gen, n: int) -> Gen:
    """a·b, with its sign (both Hermitian and commuting: the product's phase is ±1)."""
    e = 0
    for j in range(n):
        e += _g((a.x >> j) & 1, (a.z >> j) & 1, (b.x >> j) & 1, (b.z >> j) & 1)
    e %= 4
    if e not in (0, 2):
        raise AssertionError("the product of commuting Paulis is Hermitian")
    s = _xor(a.s, b.s)
    if e == 2:
        s = _xor(s, ONE)
    return Gen(a.x ^ b.x, a.z ^ b.z, s)


def _anti(a: Gen, x: int, z: int) -> bool:
    return (bin(a.x & z).count("1") + bin(a.z & x).count("1")) % 2 == 1


@dataclass
class SymState:
    index: dict = field(default_factory=dict)      # block -> qubit
    gens: list = field(default_factory=list)
    n: int = 0
    nvars: int = 0

    def fresh(self, name: str) -> frozenset:
        self.nvars += 1
        return frozenset({name})

    def prep(self, q: str, basis: str) -> None:
        j = self.n
        self.n += 1
        self.index[q] = j
        x, z = {"Z": (0, 1), "X": (1, 0), "Y": (1, 1)}[basis]
        self.gens.append(Gen(x << j, z << j, ZERO))

    def pauli(self, letters: dict) -> tuple[int, int]:
        x = z = 0
        for q, p in letters.items():
            j = self.index[q]
            if p in ("X", "Y"):
                x |= 1 << j
            if p in ("Z", "Y"):
                z |= 1 << j
        return x, z

    def apply(self, letters: dict, cond: frozenset = ONE) -> None:
        """A Pauli, applied when the affine condition is 1: flips anticommuting signs by it."""
        x, z = self.pauli(letters)
        for g in self.gens:
            if _anti(g, x, z):
                g.s = _xor(g.s, cond)

    def measure(self, letters: dict, name: str) -> frozenset:
        """Measure the product; return the outcome bit as an affine expression."""
        x, z = self.pauli(letters)
        anti = [k for k, g in enumerate(self.gens) if _anti(g, x, z)]
        if anti:
            p = anti[0]
            for k in anti[1:]:
                self.gens[k] = _mul(self.gens[k], self.gens[p], self.n)
            v = self.fresh(name)
            self.gens[p] = Gen(x, z, v)
            return v
        # deterministic: the product of generators that equals the measured Pauli
        rows = [(g.x, g.z) for g in self.gens]
        pick = _solve(rows, (x, z), self.n)
        if pick is None:
            raise AssertionError("a commuting Pauli outside the stabilizer group: the state is not pure")
        acc = Gen(0, 0, ZERO)
        for k in pick:
            acc = _mul(acc, self.gens[k], self.n)
        return acc.s

    def discard(self, q: str, basis: str) -> None:
        """Remove a qubit just measured in `basis`: clear it from every generator, drop it."""
        j = self.index.pop(q)
        xj, zj = (1, 0) if basis == "X" else (0, 1) if basis == "Z" else (1, 1)
        own = next(k for k, g in enumerate(self.gens)
                   if ((g.x >> j) & 1, (g.z >> j) & 1) == (xj, zj)
                   and (g.x & ~(1 << j)) == 0 and (g.z & ~(1 << j)) == 0)
        for k, g in enumerate(self.gens):
            if k != own and (((g.x >> j) & 1) or ((g.z >> j) & 1)):
                self.gens[k] = _mul(g, self.gens[own], self.n)
        del self.gens[own]
        # renumber: qubit j's column is now zero everywhere, keep it (simplest, still exact)


def _solve(rows, target, n):
    """Indices of rows whose XOR is `target` (GF(2), over 2n bits), or None."""
    vecs = [(r[0] | (r[1] << n), 1 << i) for i, r in enumerate(rows)]
    t = target[0] | (target[1] << n)
    basis: dict = {}
    for v, tag in vecs:
        while v:
            h = v.bit_length() - 1
            if h in basis:
                bv, bt = basis[h]
                v ^= bv
                tag ^= bt
            else:
                basis[h] = (v, tag)
                break
    tag = 0
    while t:
        h = t.bit_length() - 1
        if h not in basis:
            return None
        bv, bt = basis[h]
        t ^= bv
        tag ^= bt
    return [i for i in range(len(rows)) if (tag >> i) & 1]


def run(alg: Algorithm) -> dict:
    """Every outcome of `alg` (its gate calls expanded) as an affine expression."""
    st = SymState()
    out: dict = {}
    for ins in alg.instructions:
        cond = ONE
        if ins.cond is not None:
            if len(ins.cond.parities) != 1:
                raise NotClifford(f"line {ins.line}: a conjunction guard ({ins.guard}) is not affine")
            const, names = ins.cond.parities[0]
            cond = ONE if const else ZERO
            for nm in names:
                cond = _xor(cond, out[nm])
        k = ins.kind
        if k == "prep":
            st.prep(ins.blocks[0], ins.basis)
        elif k == "inject":
            raise NotClifford(f"line {ins.line}: a magic state is not a stabilizer state")
        elif k in ("x", "y", "z"):
            st.apply({ins.blocks[0]: k.upper()}, cond)
        elif k in ("ppm", "zz", "xx", "read"):
            if ins.cond is not None:
                raise NotClifford(f"line {ins.line}: a guarded measurement")
            if k == "ppm":
                letters = dict(zip(ins.blocks, ins.paulis))
            elif k in ("zz", "xx"):
                letters = {b: k[0].upper() for b in ins.blocks}
            else:
                letters = {ins.blocks[0]: ins.basis}
            v = st.measure(letters, ins.cvar or f"#{ins.index}")
            if ins.cvar:
                out[ins.cvar] = v
            if k == "read":
                st.discard(ins.blocks[0], ins.basis)
        elif k == "bit":
            # affine when every term reads at most one outcome
            if any(len(t) > 1 for t in ins.formula):
                raise NotClifford(f"line {ins.line}: a bit that is a product of outcomes is not affine")
            v = ZERO
            for t in ins.formula:
                v = _xor(v, ONE) if not t else _xor(v, _xor(out[t[0][1]], ONE if t[0][0] else ZERO))
            out[ins.cvar] = v
        elif k in ("decode", "se", "store"):
            pass
        elif k == "cx":
            raise NotClifford(f"line {ins.line}: transversal CX is not modelled here yet")
        else:
            raise NotClifford(f"line {ins.line}: no symbolic rule for '{k}'")
    return out


def expr_text(e: frozenset) -> str:
    if not e:
        return "0"
    parts = sorted(v for v in e if v != "1")
    return " ⊕ ".join((["1"] if "1" in e else []) + parts)


def check_outcome_spec(alg: Algorithm, data_blocks: list, value: int, width: int) -> dict:
    """`Pr[ c[0,width) = value ] = 1`, with c[i] the last Z measurement of data block i."""
    out = run(alg)
    last: dict = {}
    for ins in alg.instructions:
        if ins.cvar and ((ins.kind == "ppm" and ins.blocks and len(ins.blocks) == 1 and ins.paulis == ["Z"])
                         or (ins.kind == "read" and ins.basis == "Z")):
            last[ins.blocks[0]] = ins.cvar
    bits = []
    ok = True
    for i in range(width):
        b = data_blocks[i]
        name = last.get(b)
        e = out.get(name, None) if name else None
        want = (value >> i) & 1
        good = e is not None and e == (ONE if want else ZERO)
        ok &= good
        bits.append({"c": i, "block": b, "outcome": name, "expression": expr_text(e) if e is not None else None,
                     "want": want, "ok": good})
    return {"checked": True, "kind": "outcome", "spec": f"Pr[ c[0,{width}) = {value} ] = 1",
            "passed": ok, "bits": bits, "random_outcomes": len([v for v in out.values() if v - ONE]),
            "method": "symbolic stabilizer simulation: every branch at once"}
