"""Bivariate-bicycle codes -- the front end's input.  PLAN §7.

A BB code is built from two polynomials over the group Z_l x Z_m. For the gross code
[[144,12,12]] (arXiv:2308.07915, IBM):

    l = 12, m = 6,  A = x^3 + y   + y^2,  B = y^3 + x   + x^2

with `x = S_l (x) I_m` and `y = I_l (x) S_m` the cyclic shift matrices. Then
`H_X = [A | B]` and `H_Z = [B^T | A^T]`, each `n/2 x n` with `n = 2lm = 144`.

Every check touches exactly **six** data qubits -- three from each polynomial -- which is
where the shipped schedule's "144 checks x 6 members" comes from, and why every data ion
takes part in exactly six contacts per round.

This module supplies the *interaction multiset* a compiler has to realize. It deliberately
stops there: which ancilla serves which check, and in what order, is a scheduling decision
(PLAN §7 pass 2), not a property of the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = ["BBCode", "gross_code", "Check"]


@dataclass(frozen=True)
class Check:
    """One stabilizer: its type, its index, and the data qubits it touches."""

    name: str
    type: str  # "X" | "Z"
    index: int
    members: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.members)


@dataclass
class BBCode:
    l: int
    m: int
    a_terms: Sequence[tuple[str, int]]
    b_terms: Sequence[tuple[str, int]]
    name: str = "bb"
    checks: list[Check] = field(default_factory=list)

    @property
    def n(self) -> int:
        """Data qubits."""
        return 2 * self.l * self.m

    @property
    def n_checks(self) -> int:
        return self.n // 2 * 2  # X checks + Z checks

    @property
    def weight(self) -> int:
        return len(self.a_terms) + len(self.b_terms)

    def summary(self) -> dict:
        per_qubit: dict[int, int] = {}
        for c in self.checks:
            for q in c.members:
                per_qubit[q] = per_qubit.get(q, 0) + 1
        return {
            "name": self.name,
            "n": self.n,
            "l": self.l,
            "m": self.m,
            "checks": len(self.checks),
            "check_weight": sorted({len(c) for c in self.checks}),
            "contacts": sum(len(c) for c in self.checks),
            "contacts_per_data_qubit": sorted(set(per_qubit.values())),
        }


def _shift_index(q: int, l: int, m: int, dx: int, dy: int) -> int:
    """Apply `x^dx y^dy` to the data qubit index `q` on the Z_l x Z_m torus."""
    half = l * m
    side, r = divmod(q, half)
    i, j = divmod(r, m)
    return side * half + ((i + dx) % l) * m + (j + dy) % m


def _apply(terms: Sequence[tuple[str, int]], q: int, l: int, m: int) -> list[int]:
    out = []
    for var, power in terms:
        dx, dy = (power, 0) if var == "x" else (0, power)
        out.append(_shift_index(q, l, m, dx, dy))
    return out


def build_checks(code: BBCode) -> list[Check]:
    """The `n` checks, each on six data qubits.

    Left-block qubits are `0 .. lm-1`, right-block `lm .. 2lm-1`. An X check at position
    `v` touches `A` applied to the left block and `B` applied to the right; a Z check
    touches the transposes, which on cyclic groups are the inverse shifts.
    """
    l, m, half = code.l, code.m, code.l * code.m
    checks: list[Check] = []
    for v in range(half):
        members = tuple(
            sorted(set(
                _apply(code.a_terms, v, l, m)
                + [half + q for q in _apply(code.b_terms, v, l, m)]
            ))
        )
        checks.append(Check(f"X_{v}", "X", v, members))
    for v in range(half):
        inv_a = [(var, -p) for var, p in code.a_terms]
        inv_b = [(var, -p) for var, p in code.b_terms]
        members = tuple(
            sorted(set(
                _apply(inv_b, v, l, m)
                + [half + q for q in _apply(inv_a, v, l, m)]
            ))
        )
        checks.append(Check(f"Z_{v}", "Z", v, members))
    return checks


def gross_code() -> BBCode:
    """BB [[144,12,12]] -- the gross code (arXiv:2308.07915).

    `l = 12, m = 6, A = x^3 + y + y^2, B = y^3 + x + x^2`; 144 data qubits, 144 checks of
    weight 6, and every data qubit in exactly 6 checks.
    """
    code = BBCode(
        l=12, m=6,
        a_terms=(("x", 3), ("y", 1), ("y", 2)),
        b_terms=(("y", 3), ("x", 1), ("x", 2)),
        name="bb144_12_12",
    )
    code.checks = build_checks(code)
    return code
