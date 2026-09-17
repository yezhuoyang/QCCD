"""Rotated surface-code patches: the geometry every verified gadget is compiled from.

A patch of `rows × cols` data qubits (both odd) has data qubit `(r, c)` at index
`r·cols + c`.  Plaquette `(i, j)` covers data `(i, j) (i, j+1) (i+1, j) (i+1, j+1)` for
`-1 ≤ i < rows`, `-1 ≤ j < cols`; it is a Z check when `i + j` is even and an X check when
odd.  Interior plaquettes are weight 4.  On the top and bottom edges only the X plaquettes
are kept (weight 2), on the left and right edges only the Z ones -- so X̄ runs top to
bottom along a column and Z̄ runs left to right along a row.  At d = 3 this is exactly the
17-qubit layout of `SmallCode/surface17`.

**The syndrome-extraction schedule** is the standard four-layer one (Tomita & Svore 2014):
an X plaquette touches its corners in the order NW, NE, SW, SE ("Z" shape), a Z plaquette
NW, SW, NE, SE ("N" shape), corner t in layer t.  Two reasons it is the one to use:

* it is *valid*: wherever an X and a Z plaquette share two data qubits, both of them see
  the two checks in the same relative order, so the two measured operators still commute
  as circuits and each ancilla measures its stabilizer;
* it is *hook-safe*: a fault on an ancilla halfway through spreads to its last two data
  qubits, which lie across the logical of the same type (horizontal for X checks, whose
  X̄ is vertical; vertical for Z checks, whose Z̄ is horizontal), so the circuit keeps
  distance d.

Neither property is assumed downstream: `qccd.gadget.logic` checks the compiled circuit's
flows and fault distance.  The schedule is recorded as a *partial order* (each ancilla's
four contacts in sequence; on each data qubit, contacts with checks of different type in
layer order), because a compiler that moves ions may realise any linear extension of it,
and every linear extension is the same circuit up to commuting gates.

**Lattice surgery** merges two d×d patches through one row (Z̄Z̄) or one column (X̄X̄) of
fresh data qubits into a `(2d+1)×d` or `d×(2d+1)` patch of the same rules.  The merged
patch contains both patches' checks except the two boundary rows of weight-2 checks that
face each other, which become weight-4 plaquettes across the seam; the product of the new
plaquettes of the measured type equals Z̄_A Z̄_B (or X̄_A X̄_B).  Horsman et al. 2012.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from .codes import CSSCode

__all__ = ["Check", "Patch", "patch", "surface_code", "merged_patch", "Contact"]

_X_ORDER = ("NW", "NE", "SW", "SE")
_Z_ORDER = ("NW", "SW", "NE", "SE")


@dataclass(frozen=True)
class Check:
    name: str                   # "X0", "Z3": basis, then row-major rank within the basis
    basis: str                  # "X" | "Z"
    i: int                      # plaquette row (top-left corner)
    j: int                      # plaquette column
    corners: tuple[tuple[str, int], ...]   # (corner, data index), in schedule order
    layers: tuple[int, ...]     # the layer of each contact, aligned with `corners`

    @property
    def support(self) -> list[int]:
        return [q for _, q in self.corners]

    @property
    def weight(self) -> int:
        return len(self.corners)


@dataclass(frozen=True)
class Contact:
    check: str
    data: int
    layer: int
    basis: str


@dataclass
class Patch:
    rows: int
    cols: int
    checks: list[Check] = field(default_factory=list)
    name: str = ""

    @property
    def n(self) -> int:
        return self.rows * self.cols

    def index(self, r: int, c: int) -> int:
        return r * self.cols + c

    def coord(self, q: int) -> tuple[int, int]:
        return divmod(q, self.cols)

    def check(self, name: str) -> Check:
        return next(c for c in self.checks if c.name == name)

    @cached_property
    def code(self) -> CSSCode:
        hx = tuple(sum(1 << q for q in c.support) for c in self.checks if c.basis == "X")
        hz = tuple(sum(1 << q for q in c.support) for c in self.checks if c.basis == "Z")
        return CSSCode(name=self.name, n=self.n, hx=hx, hz=hz, family="rotated_surface",
                       params={"rows": self.rows, "cols": self.cols, "patch": self})

    def contacts(self) -> list[Contact]:
        return [Contact(c.name, q, layer, c.basis)
                for c in self.checks for (_, q), layer in zip(c.corners, c.layers)]

    def precedence(self) -> dict[tuple[str, int], list[tuple[str, int]]]:
        """For each contact (check, data): the contacts that must come before it."""
        before: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for c in self.checks:
            prev = None
            for _, q in c.corners:
                before.setdefault((c.name, q), [])
                if prev is not None:
                    before[(c.name, q)].append((c.name, prev))
                prev = q
        by_data: dict[int, list[Contact]] = {}
        for ct in self.contacts():
            by_data.setdefault(ct.data, []).append(ct)
        for q, cts in by_data.items():
            for a in cts:
                for b in cts:
                    if a.basis != b.basis and a.layer < b.layer:
                        before[(b.check, q)].append((a.check, q))
        return before

    def logical(self, basis: str) -> list[int]:
        """The canonical representative: Z̄ along row 0, X̄ down column 0."""
        if basis == "Z":
            return [self.index(0, c) for c in range(self.cols)]
        return [self.index(r, 0) for r in range(self.rows)]


def patch(rows: int, cols: int, *, name: str | None = None) -> Patch:
    if rows % 2 == 0 or cols % 2 == 0:
        raise ValueError("a rotated surface patch needs odd rows and columns")
    raw = []
    for i in range(-1, rows):
        for j in range(-1, cols):
            basis = "Z" if (i + j) % 2 == 0 else "X"
            corners = {k: (r, c) for k, (r, c) in (("NW", (i, j)), ("NE", (i, j + 1)),
                                                    ("SW", (i + 1, j)), ("SE", (i + 1, j + 1)))
                       if 0 <= r < rows and 0 <= c < cols}
            if len(corners) == 4:
                keep = True
            elif len(corners) == 2:
                keep = (basis == "X" and i in (-1, rows - 1)) or \
                       (basis == "Z" and j in (-1, cols - 1))
            else:
                keep = False
            if not keep:
                continue
            order = _X_ORDER if basis == "X" else _Z_ORDER
            seq = [(k, corners[k][0] * cols + corners[k][1], t)
                   for t, k in enumerate(order) if k in corners]
            raw.append((basis, i, j, seq))
    checks = []
    for basis in ("X", "Z"):
        rank = 0
        for b, i, j, seq in sorted((r for r in raw if r[0] == basis), key=lambda r: (r[1], r[2])):
            checks.append(Check(f"{basis}{rank}", basis, i, j,
                                tuple((k, q) for k, q, _ in seq), tuple(t for _, _, t in seq)))
            rank += 1
    return Patch(rows, cols, checks, name or f"surface_{rows}x{cols}")


def surface_code(d: int = 3) -> CSSCode:
    p = patch(d, d, name=f"surface_d{d}")
    return p.code


def merged_patch(d: int, kind: str) -> tuple[Patch, dict]:
    """The merged patch for a Z̄Z̄ ("ZZ") or X̄X̄ ("XX") surgery, and where everything sits:
    `{"A": [merged index of A's qubit i], "B": [...], "seam": [...], "measured": [checks
    whose product is the logical outcome], "seam_basis": 'X'|'Z'}`."""
    if kind == "ZZ":
        p = patch(2 * d + 1, d, name=f"surface_d{d}_zz")
        a = [p.index(r, c) for r in range(d) for c in range(d)]
        seam = [p.index(d, c) for c in range(d)]
        b = [p.index(r + d + 1, c) for r in range(d) for c in range(d)]
        # the Z plaquettes touching the seam row: their product is Z̄_A Z̄_B
        measured = [c.name for c in p.checks if c.basis == "Z" and set(c.support) & set(seam)]
        seam_basis = "X"                       # seam qubits start in |+>, end measured in X
    elif kind == "XX":
        p = patch(d, 2 * d + 1, name=f"surface_d{d}_xx")
        a = [p.index(r, c) for r in range(d) for c in range(d)]
        seam = [p.index(r, d) for r in range(d)]
        b = [p.index(r, c + d + 1) for r in range(d) for c in range(d)]
        measured = [c.name for c in p.checks if c.basis == "X" and set(c.support) & set(seam)]
        seam_basis = "Z"                       # seam qubits start in |0>, end measured in Z
    else:
        raise ValueError(f"surgery kind {kind!r}: 'ZZ' or 'XX'")
    return p, {"A": a, "B": b, "seam": seam, "measured": measured, "seam_basis": seam_basis}
