"""GF(2) linear algebra, mirroring LogicQ's ChainQ kernel step for step.

A logical qubit index means something only relative to a logical basis, and LogicQ
*derives* that basis (`ChainQ/Core/Params.lean::deriveLogicalBasis?`) by a particular
elimination.  If this module eliminated in any other order, `q[3]` in a LogicQ program
would name a different logical operator here than it does there, and every messenger
visit this layer schedules would touch the wrong ions.  So these functions are not
"GF(2) routines"; they are transcriptions of named Lean definitions, in the same order:

    rowReduce, reduceInto, addToBasis   ChainQ/Algebra/GF2Rank.lean
    kernelBasis, quotientBasis, gf2Inv  ChainQ/Algebra/Kernel.lean
    dotBit, gemmT                       ChainQ/Algebra/GF2.lean

A vector is a Python int: bit `c` is column `c`, so Lean's `firstOne` (the lowest index
holding a 1) is the lowest set bit, and `vecXor` is `^`.
"""

from __future__ import annotations

from typing import Iterable, Sequence

__all__ = [
    "first_one", "reduce_into", "add_to_basis", "row_reduce", "rank", "in_span",
    "kernel_basis", "quotient_basis", "gf2_inv", "dot", "weight", "support",
    "from_bits", "to_bits",
]


def first_one(v: int) -> int | None:
    """`firstOne`: the pivot column, or None for the zero vector."""
    return (v & -v).bit_length() - 1 if v else None


def reduce_into(basis: Sequence[int], v: int) -> int:
    """`reduceInto`: clear every basis pivot from `v`, in basis order."""
    for b in basis:
        j = first_one(b)
        if j is not None and (v >> j) & 1:
            v ^= b
    return v


def add_to_basis(basis: list[int], v: int) -> list[int]:
    """`addToBasis`: insert `v` into an RREF basis, keeping it reduced."""
    w = reduce_into(basis, v)
    j = first_one(w)
    if j is None:
        return basis
    return [b ^ w if (b >> j) & 1 else b for b in basis] + [w]


def row_reduce(rows: Iterable[int]) -> list[int]:
    """`rowReduce`: fold `addToBasis` over the rows, left to right."""
    basis: list[int] = []
    for r in rows:
        basis = add_to_basis(basis, r)
    return basis


def rank(rows: Iterable[int]) -> int:
    return len(row_reduce(rows))


def in_span(rows: Iterable[int], v: int) -> bool:
    return reduce_into(row_reduce(rows), v) == 0


def kernel_basis(h: Sequence[int], n: int) -> list[int]:
    """`kernelBasis`: one vector per free column, by RREF back-substitution."""
    r = row_reduce(h)
    pivot_row = {first_one(row): row for row in r}
    out = []
    for f in range(n):
        if f in pivot_row:
            continue
        v = 1 << f
        for c, row in pivot_row.items():
            if (row >> f) & 1:
                v |= 1 << c
        out.append(v)
    return out


def quotient_basis(stab: Sequence[int], cands: Sequence[int]) -> list[int]:
    """`quotientBasis`: the candidates independent modulo `stab`, as given (not reduced)."""
    basis = row_reduce(stab)
    chosen = []
    for v in cands:
        # Lean re-row-reduces `acc.1` inside `inSpan`; an RREF basis row-reduces to
        # itself, so reducing against `basis` directly decides the same membership
        if reduce_into(basis, v) == 0:
            continue
        basis = add_to_basis(basis, v)
        chosen.append(v)
    return chosen


def gf2_inv(m: Sequence[int], n: int) -> list[int] | None:
    """`gf2Inv`: row-reduce `[M | I]`; None when `M` is singular."""
    aug = [m[i] | (1 << (n + i)) for i in range(n)]
    r = row_reduce(aug)
    by_pivot = {first_one(row): row for row in r}
    rows = []
    for i in range(n):
        if i not in by_pivot:
            return None
        rows.append(by_pivot[i] >> n)
    return rows


def dot(a: int, b: int) -> int:
    """`dotBit`: the GF(2) inner product."""
    return (a & b).bit_count() & 1


def weight(v: int) -> int:
    return v.bit_count()


def support(v: int) -> list[int]:
    out = []
    while v:
        low = v & -v
        out.append(low.bit_length() - 1)
        v ^= low
    return out


def from_bits(bits: Sequence[bool | int]) -> int:
    v = 0
    for i, b in enumerate(bits):
        if b:
            v |= 1 << i
    return v


def to_bits(v: int, n: int) -> list[int]:
    return [(v >> i) & 1 for i in range(n)]
