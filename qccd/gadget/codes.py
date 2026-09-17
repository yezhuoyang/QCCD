"""The codes a logical program declares, reduced to what the hardware needs.

The gadget layer does not type-check logical programs -- LogicQ does, with proofs.  It
needs four facts about each code block, and derives each one the way LogicQ does so the
two tools cannot disagree about what a program means:

* `n`, the data ions a block holds;
* `k`, from GF(2) rank (`ChainQ/Core/Params.lean::CSSCode.k`);
* the support of every logical operator, from `deriveLogicalBasis?` -- this decides which
  ions a messenger must visit for `M q[i]↦Z`, and LogicQ's own physical lowering XORs
  exactly these rows (`Compiler/Verification/Basic.lean`);
* whether a block-transversal layer maps the stabilizer group to itself.

Bivariate-bicycle matrices follow `ChainQ/BBCode/Basic.lean::Internal.bb`:
`hx = [A | B]`, `hz = [Bᵀ | Aᵀ]`, with the monomial `x^i y^j` lifted to `S_l^i ⊗ S_m^j`
(row `a·m+b` has its 1 in column `((a+i) mod l)·m + ((b+j) mod m)`), terms XOR-summed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from . import gf2

__all__ = ["CSSCode", "bivariate_bicycle", "bare", "parse_poly", "PolyError",
           "TUTORIAL_BB", "code_name"]


class PolyError(ValueError):
    pass


@dataclass(frozen=True)
class CSSCode:
    """A CSS code as LogicQ holds one: `n` and the two check matrices, rows as bitsets."""

    name: str
    n: int
    hx: tuple[int, ...]
    hz: tuple[int, ...]
    family: str = "css"
    params: dict = field(default_factory=dict, compare=False, hash=False)

    def valid(self) -> bool:
        """`CSSCode.valid`: every X check commutes with every Z check."""
        return all(gf2.dot(x, z) == 0 for x in self.hx for z in self.hz)

    @cached_property
    def k(self) -> int:
        return self.n - gf2.rank(self.hx) - gf2.rank(self.hz)

    @cached_property
    def logical_basis(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """`deriveLogicalBasis?`: `(lx, lz)` with `dot(lx[i], lz[j]) = [i = j]`."""
        if not self.valid():
            raise ValueError(f"{self.name}: X and Z checks do not commute")
        x_logs = gf2.quotient_basis(self.hx, gf2.kernel_basis(self.hz, self.n))
        z_logs = gf2.quotient_basis(self.hz, gf2.kernel_basis(self.hx, self.n))
        k = len(x_logs)
        if k != len(z_logs) or k != self.k:
            raise ValueError(f"{self.name}: quotient bases have {k} and {len(z_logs)} "
                             f"rows but k = {self.k}")
        pairing = [sum(gf2.dot(x, z) << j for j, z in enumerate(z_logs)) for x in x_logs]
        pinv = gf2.gf2_inv(pairing, k)
        if pinv is None:
            raise ValueError(f"{self.name}: logical pairing is singular")
        lz = []
        for i in range(k):
            row = 0
            for r in range(k):
                if (pinv[r] >> i) & 1:
                    row ^= z_logs[r]
            lz.append(row)
        return tuple(x_logs), tuple(lz)

    def logical_support(self, index: int, pauli: str) -> list[int]:
        """The data qubits a logical Pauli's representative acts on.

        `Y` is `X·Z`: both rows, which is the support LogicQ's lowering XORs.
        """
        if not 0 <= index < self.k:
            raise IndexError(f"{self.name} has k = {self.k}; there is no logical {index}")
        lx, lz = self.logical_basis
        rows = {"X": lx[index], "Z": lz[index], "Y": lx[index] | lz[index]}
        if pauli not in rows:
            raise ValueError(f"not a Pauli: {pauli!r}")
        return gf2.support(rows[pauli])

    def transversal_preserves(self, gate: str) -> bool:
        """`checkTransversal` (TypeChecker/Judgment/Transversal/Check.lean), phase-free.

        LogicQ applies the gate's `2n×2n` symplectic map to every stabilizer row and asks
        for row-span membership.  H sends `(x|z)` to `(z|x)`, so every X check must lie in
        `rowSpan hz` and every Z check in `rowSpan hx`.  S sends `(x|z)` to `(x|x⊕z)`, so
        every X check must lie in `rowSpan hz`.  Like LogicQ's, the check ignores signs.
        """
        if gate == "H":
            return (all(gf2.in_span(self.hz, x) for x in self.hx)
                    and all(gf2.in_span(self.hx, z) for z in self.hz))
        if gate == "S":
            return all(gf2.in_span(self.hz, x) for x in self.hx)
        raise ValueError(f"no transversal rule for {gate!r}")

    def summary(self) -> dict:
        return {"name": self.name, "family": self.family, "n": self.n, "k": self.k,
                "x_checks": len(self.hx), "z_checks": len(self.hz), **self.params}


# --------------------------------------------------------------------- polynomials


def parse_poly(text: str, l: int, m: int) -> tuple[tuple[int, int], ...]:
    """A ChainQ polynomial over `F₂[x,y]/(x^l - 1, y^m - 1)`, as its exponent support.

    Grammar (`ChainQ/SurfaceSyntax.lean`): `poly = mono {"+" mono}`,
    `mono = factor {"*" factor}`, `factor = atom ["^" nat]`,
    `atom = "x" | "y" | nat | "(" poly ")"`; a literal `n` is 1 when odd, 0 when even.
    Terms are XOR-summed, so a repeated monomial cancels.
    """
    toks = _tokens(text)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take(expect=None):
        nonlocal pos
        tok = peek()
        if tok is None or (expect is not None and tok != expect):
            raise PolyError(f"in {text!r}: expected {expect or 'a term'}, found {tok!r}")
        pos += 1
        return tok

    def xor_add(a: dict, b: dict) -> dict:
        out = dict(a)
        for e in b:
            if e in out:
                del out[e]
            else:
                out[e] = True
        return out

    def mul(a: dict, b: dict) -> dict:
        out: dict = {}
        for (i1, j1) in a:
            for (i2, j2) in b:
                out = xor_add(out, {((i1 + i2) % l, (j1 + j2) % m): True})
        return out

    def power(a: dict, p: int) -> dict:
        out = {(0, 0): True}
        for _ in range(p):
            out = mul(out, a)
        return out

    def atom() -> dict:
        tok = take()
        if tok == "x":
            return {(1 % l, 0): True}
        if tok == "y":
            return {(0, 1 % m): True}
        if tok == "(":
            v = poly()
            take(")")
            return v
        if tok.isdigit():
            return {(0, 0): True} if int(tok) % 2 else {}
        raise PolyError(f"in {text!r}: unexpected {tok!r}")

    def factor() -> dict:
        a = atom()
        if peek() == "^":
            take("^")
            exp = take()
            if not exp.isdigit():
                raise PolyError(f"in {text!r}: exponent must be a natural number")
            a = power(a, int(exp))
        return a

    def mono() -> dict:
        v = factor()
        while peek() == "*":
            take("*")
            v = mul(v, factor())
        return v

    def poly() -> dict:
        v = mono()
        while peek() == "+":
            take("+")
            v = xor_add(v, mono())
        return v

    result = poly()
    if peek() is not None:
        raise PolyError(f"in {text!r}: unexpected {peek()!r}")
    return tuple(sorted(result))


def _tokens(text: str) -> list[str]:
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit():
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            out.append(text[i:j])
            i = j
        elif c in "xy+*^()":
            out.append(c)
            i += 1
        else:
            raise PolyError(f"in {text!r}: unexpected character {c!r}")
    return out


# --------------------------------------------------------------------- families


def _bicirculant(l: int, m: int, terms) -> list[int]:
    rows = []
    for a in range(l):
        for b in range(m):
            v = 0
            for (i, j) in terms:
                v ^= 1 << (((a + i) % l) * m + (b + j) % m)
            rows.append(v)
    return rows


def _transpose(rows: list[int], n: int) -> list[int]:
    out = [0] * n
    for r, row in enumerate(rows):
        for c in gf2.support(row):
            out[c] |= 1 << r
    return out


def code_name(l: int, m: int, a_terms, b_terms) -> str:
    """A code's identity, from its content: blocks with equal matrices share one name.

    A LogicQ program names BLOCKS (`code q0 as …`, `code q1 as …`); the hardware cares about
    CODES, because two blocks of one code share one memory master and one set of compiled
    programs.  The tutorial's name when the polynomials match one of its codes, otherwise
    `bb<n>_<digest>`.
    """
    a, b = tuple(sorted(a_terms)), tuple(sorted(b_terms))
    for name, (tl, tm, ta, tb, _) in TUTORIAL_BB.items():
        if (tl, tm) == (l, m) and parse_poly(ta, l, m) == a and parse_poly(tb, l, m) == b:
            return name
    import hashlib
    digest = hashlib.sha256(repr((l, m, a, b)).encode()).hexdigest()[:6]
    return f"bb{2 * l * m}_{digest}"


def bivariate_bicycle(name: str | None, l: int, m: int, a_terms, b_terms,
                      declared: tuple[int, int, int] | None = None) -> CSSCode:
    """`Internal.bb l m a b`: `hx = [A | B]`, `hz = [Bᵀ | Aᵀ]`, `n = 2lm`.

    `name=None` names the code by its content (`code_name`)."""
    name = name or code_name(l, m, a_terms, b_terms)
    if l < 1 or m < 1 or not a_terms or not b_terms:
        raise ValueError("a bivariate-bicycle code needs l, m >= 1 and nonempty A, B")
    half = l * m
    A = _bicirculant(l, m, a_terms)
    B = _bicirculant(l, m, b_terms)
    hx = tuple(A[r] | (B[r] << half) for r in range(half))
    Bt, At = _transpose(B, half), _transpose(A, half)
    hz = tuple(Bt[r] | (At[r] << half) for r in range(half))
    params = {"l": l, "m": m, "A": list(a_terms), "B": list(b_terms)}
    if declared:
        params["declared"] = list(declared)
    return CSSCode(name=name, n=2 * half, hx=hx, hz=hz, family="bivariate_bicycle",
                   params=params)


def bare(name: str) -> CSSCode:
    """`code q as Bare`: one physical qubit, no checks, `k = 1`."""
    return CSSCode(name=name, n=1, hx=(), hz=(), family="bare")


#: The bivariate-bicycle codes of LogicQ's `Tutorial/BBCodes.lean`, by the names it uses:
#: `(l, m, A, B, [[n, k, d]])`.  bb18 is the tutorial's own (d <= 4); the rest are
#: Bravyi et al. 2024's.
TUTORIAL_BB = {
    "bb18": (3, 3, "1 + x + y^2", "1 + x^2 + y", (18, 4, 4)),
    "bb72": (6, 6, "x^3 + y + y^2", "y^3 + x + x^2", (72, 12, 6)),
    "bb90": (15, 3, "x^9 + y + y^2", "1 + x^2 + x^7", (90, 8, 10)),
    "bb108": (9, 6, "x^3 + y + y^2", "y^3 + x + x^2", (108, 8, 10)),
    "bb144": (12, 6, "x^3 + y + y^2", "y^3 + x + x^2", (144, 12, 12)),
}
