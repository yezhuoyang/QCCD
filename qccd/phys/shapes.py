"""Metal, as integers.  No float appears in this module, and a test says so.

A layout is the one artefact in this project that has to survive a round trip through a
foreign tool -- a GDSII writer, a GDSII reader, a fab -- and come back bit for bit.  That
is only possible if the coordinates are exact, so they are integer nanometres and nothing
else: `Poly` refuses a float, `Inst` refuses a float, the shoelace is an integer shoelace,
and the design-rule distances are compared as squared integers so that a gap check never
takes a square root.  `tests/test_shapes.py::test_no_float_literal_appears_in_shapes`
greps this file for a decimal point.

**Quarter-turns only.**  Placement goes through `qccd.arch.component.translate_point`,
which is written as swaps and negations rather than a rotation matrix.  On integers it is
exact.  The reason it exists at all is that one of the 24 measured cos/sin values differs
by 1 ulp between CPython and V8, and 2-5 ulp flips a segment's bow -- so this project does
no trigonometry in placement, in either language.

**Overlap on one net is a merge, not a defect.**  Two perpendicular RF rails necessarily
overlap at every degree-4 node.  So the DRC path is: flatten, group by net, union each
net into disjoint rectangles, and only then look for gaps *between* nets.  `union_by_net`
is the exact integer rectangle union that makes that possible without `shapely`.

**What the width check does and does not see.**  `min_width_violations` checks each drawn
rectangle's own two dimensions.  It catches the failure that actually happens -- a DC pad
tiled thinner than the process allows because the pitch was set too fine.  It does not
find a neck formed *between* two shapes on the same net, which would need a morphological
erosion this package does not have.  Saying which is which is cheaper than pretending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Sequence

from ..arch.component import translate_point
from .tech import Technology

__all__ = ["Poly", "Cell", "Inst", "Layout", "Refusal", "Violation",
           "min_width_violations", "min_gap_violations", "union_rects"]


def _int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{what} must be an int in nanometres, got {type(value).__name__} "
            f"{value!r}; a float coordinate is how a layout stops round-tripping")
    return value


# --------------------------------------------------------------------------- polygons

@dataclass(frozen=True)
class Poly:
    """One closed polygon of metal, in integer nanometres.

    `xy` is flat and implicitly closed: `(x0, y0, x1, y1, ...)` with no repeat of the
    first point.  `net` is the electrical node the shape belongs to -- shapes sharing a
    net may overlap freely.  `role` says what the builder made it for (`rail`, `dc_pad`,
    `naive_crossing`, ...) and is what a report groups by.  `owner` is the id of the
    device element it was derived from, so every rectangle can name the segment or node
    it came out of.
    """

    layer: str
    xy: tuple[int, ...]
    role: str
    net: str
    owner: str

    def __post_init__(self) -> None:
        xy = tuple(_int(v, "Poly.xy") for v in self.xy)
        if len(xy) < 6 or len(xy) % 2:
            raise ValueError(
                f"Poly.xy needs an even number of at least 6 coordinates (3 points), "
                f"got {len(xy)}")
        object.__setattr__(self, "xy", xy)
        for name in ("layer", "role", "net", "owner"):
            v = getattr(self, name)
            if not isinstance(v, str):
                raise TypeError(f"Poly.{name} must be a str, got {type(v).__name__}")

    # ------------------------------------------------------------ constructors

    @classmethod
    def rect(cls, layer: str, x0: int, y0: int, x1: int, y1: int, *,
             role: str, net: str, owner: str) -> "Poly":
        """An axis-aligned rectangle, counter-clockwise, with normalised bounds."""
        x0, y0, x1, y1 = (_int(v, "Poly.rect") for v in (x0, y0, x1, y1))
        if x1 <= x0 or y1 <= y0:
            raise ValueError(
                f"Poly.rect needs x1 > x0 and y1 > y0, got x=({x0}, {x1}) y=({y0}, {y1}); "
                f"a reversed or zero-area rectangle is a builder bug, not a shape")
        return cls(layer, (x0, y0, x1, y0, x1, y1, x0, y1), role, net, owner)

    # ------------------------------------------------------------- properties

    @property
    def points(self) -> tuple[tuple[int, int], ...]:
        it = iter(self.xy)
        return tuple(zip(it, it))

    def bbox(self) -> tuple[int, int, int, int]:
        xs = self.xy[0::2]
        ys = self.xy[1::2]
        return (min(xs), min(ys), max(xs), max(ys))

    def is_rect(self) -> bool:
        """True when this is exactly the four corners of an axis-aligned rectangle."""
        if len(self.xy) != 8:
            return False
        x0, y0, x1, y1 = self.bbox()
        return set(self.points) == {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}

    def is_rectilinear(self) -> bool:
        pts = self.points
        return all(a[0] == b[0] or a[1] == b[1]
                   for a, b in zip(pts, pts[1:] + pts[:1]))

    def twice_signed_area_nm2(self) -> int:
        """The integer shoelace.  Exact; positive when the winding is counter-clockwise."""
        pts = self.points
        return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))

    def area_nm2(self) -> int:
        """Unsigned area, exactly, in square nanometres.

        Refuses rather than rounding if the doubled shoelace is odd -- which cannot happen
        for a rectilinear polygon on an integer grid, so an odd value means the shape is
        not what this package makes.
        """
        twice = abs(self.twice_signed_area_nm2())
        if twice % 2:
            raise ValueError(
                f"polygon on layer {self.layer!r} owned by {self.owner!r} has an odd "
                f"doubled area ({twice}); it is not rectilinear on the integer grid, and "
                f"halving it would silently round")
        return twice // 2

    # ---------------------------------------------------------- transformation

    def placed(self, dx: int, dy: int, quarter: int) -> "Poly":
        """Quarter-turn about the origin then translate, exactly, on integers."""
        dx, dy = _int(dx, "dx"), _int(dy, "dy")
        out: list[int] = []
        for x, y in self.points:
            nx, ny = translate_point(x, y, dx, dy, quarter)
            out.append(_int(nx, "placed x"))
            out.append(_int(ny, "placed y"))
        return Poly(self.layer, tuple(out), self.role, self.net, self.owner)


@dataclass(frozen=True)
class Cell:
    """A named bundle of polygons, placed by reference.  GDSII's own unit of reuse."""

    name: str
    polys: tuple[Poly, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "polys", tuple(self.polys))


@dataclass(frozen=True)
class Inst:
    """One placement of a cell: quarter-turn about the origin, then translate."""

    cell: str
    dx: int = 0
    dy: int = 0
    quarter: int = 0
    owner: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "dx", _int(self.dx, "Inst.dx"))
        object.__setattr__(self, "dy", _int(self.dy, "Inst.dy"))
        q = self.quarter
        if isinstance(q, bool) or not isinstance(q, int) or q not in (0, 1, 2, 3):
            raise ValueError(
                f"Inst.quarter must be 0, 1, 2 or 3, got {q!r}; this package places by "
                f"quarter-turns because an arbitrary angle needs trigonometry that two "
                f"runtimes do not agree on")


# ------------------------------------------------------------------------- findings

@dataclass(frozen=True)
class Refusal:
    """Something the builder would have had to guess at, named instead of guessed."""

    kind: str
    owner: str
    reason: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "owner": self.owner, "reason": self.reason}


@dataclass(frozen=True)
class Violation:
    """A design rule the drawn metal does not meet."""

    rule: str
    layer: str
    owners: tuple[str, ...]
    measured_nm: int
    required_nm: int
    where: tuple[int, int]

    def as_dict(self) -> dict:
        return {"rule": self.rule, "layer": self.layer, "owners": list(self.owners),
                "measured_nm": self.measured_nm, "required_nm": self.required_nm,
                "where": list(self.where)}


# ------------------------------------------------------------- the rectangle union

def union_rects(boxes: Sequence[tuple[int, int, int, int]]
                ) -> tuple[tuple[int, int, int, int], ...]:
    """Exact union of axis-aligned integer rectangles, as disjoint rectangles.

    A vertical-slab sweep: cut at every distinct x, merge the y-intervals of the boxes
    covering each slab, then coalesce consecutive slabs whose merged intervals are
    identical.  All-integer, no tolerance, no `shapely`.  The coalescing step is what
    keeps the output near-minimal -- without it a long rail comes back as one rectangle
    per cut, which is correct and useless.
    """
    if not boxes:
        return ()
    xs = sorted({v for b in boxes for v in (b[0], b[2])})
    out: list[tuple[int, int, int, int]] = []
    pending: list[tuple[int, int]] = []
    pending_x0 = 0
    for lo, hi in zip(xs, xs[1:]):
        spans = sorted((b[1], b[3]) for b in boxes if b[0] <= lo and b[2] >= hi)
        merged: list[tuple[int, int]] = []
        for y0, y1 in spans:
            if merged and y0 <= merged[-1][1]:
                if y1 > merged[-1][1]:
                    merged[-1] = (merged[-1][0], y1)
            else:
                merged.append((y0, y1))
        if merged == pending:
            continue
        if pending:
            out.extend((pending_x0, y0, lo, y1) for y0, y1 in pending)
        pending, pending_x0 = merged, lo
    if pending:
        out.extend((pending_x0, y0, xs[-1], y1) for y0, y1 in pending)
    return tuple(out)


# ----------------------------------------------------------------- design rules

def _box(p: Poly) -> tuple[int, int, int, int]:
    return p.bbox()


def min_width_violations(polys: Sequence[Poly], tech: Technology) -> tuple[Violation, ...]:
    """Every drawn rectangle narrower than its layer allows, in either dimension."""
    out: list[Violation] = []
    for p in polys:
        lay = tech.layer_by_name(p.layer)
        x0, y0, x1, y1 = p.bbox()
        for measured in (x1 - x0, y1 - y0):
            if measured < lay.min_width_nm:
                out.append(Violation("min_width", p.layer, (p.owner,), measured,
                                     lay.min_width_nm, (x0, y0)))
                break
    return tuple(out)


def min_gap_violations(polys: Sequence[Poly], tech: Technology, *,
                       same_net: bool = False) -> tuple[Violation, ...]:
    """Pairs on one layer that come closer than the layer's minimum gap.

    Distance is compared as `dx*dx + dy*dy` against `min_gap**2`, so a corner-to-corner
    separation is measured properly and no square root is ever taken -- the whole check
    stays in integers.  `dx` and `dy` are each zero when the boxes overlap on that axis,
    which makes the side-by-side case fall out as plain `dx`.

    By default shapes on the same net are skipped entirely: two perpendicular RF rails
    overlap at every degree-4 node, and that is a merge.  `same_net=True` looks at them,
    which is how a sliver between two same-net shapes would be found.
    """
    out: list[Violation] = []
    by_layer: dict[str, list[Poly]] = {}
    for p in polys:
        by_layer.setdefault(p.layer, []).append(p)
    for layer, group in by_layer.items():
        limit = tech.layer_by_name(layer).min_gap_nm
        limit2 = limit * limit
        boxes = [(_box(p), p) for p in group]
        for i in range(len(boxes)):
            (ax0, ay0, ax1, ay1), pa = boxes[i]
            for j in range(i + 1, len(boxes)):
                (bx0, by0, bx1, by1), pb = boxes[j]
                if (pa.net == pb.net) != same_net:
                    continue
                dx = max(bx0 - ax1, ax0 - bx1, 0)
                dy = max(by0 - ay1, ay0 - by1, 0)
                if dx == 0 and dy == 0:
                    continue  # overlapping or touching: an overlap finding, not a gap
                d2 = dx * dx + dy * dy
                if d2 < limit2:
                    # report the exact separation when it is axis-aligned, and the
                    # conservative floor max(dx, dy) when it is a corner
                    measured = dx + dy if (dx == 0 or dy == 0) else max(dx, dy)
                    out.append(Violation("min_gap", layer, (pa.owner, pb.owner),
                                         measured, limit, (ax0, ay0)))
    return tuple(out)


# ----------------------------------------------------------------------- the layout

@dataclass(frozen=True)
class Layout:
    """Cells, their placements, and everything the builder could not or would not draw."""

    tech: Technology
    cells: Mapping[str, Cell] = field(default_factory=dict)
    insts: tuple[Inst, ...] = ()
    refused: tuple[Refusal, ...] = ()
    violations: tuple[Violation, ...] = ()
    notes: tuple[str, ...] = ()
    #: Nets that mean the same electrode everywhere.  Every other net is CELL-LOCAL and is
    #: qualified by the placing instance's owner when the layout is flattened -- because a
    #: cell placed 168 times is 168 different control electrodes, not one net shorting the
    #: whole device together.  RF is the usual member: one drive, one net, everywhere.
    global_nets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", dict(self.cells))
        object.__setattr__(self, "insts", tuple(self.insts))
        object.__setattr__(self, "global_nets", tuple(self.global_nets))
        object.__setattr__(self, "refused", tuple(self.refused))
        object.__setattr__(self, "violations", tuple(self.violations))
        object.__setattr__(self, "notes", tuple(self.notes))
        for inst in self.insts:
            if inst.cell not in self.cells:
                known = ", ".join(sorted(self.cells)) or "(none)"
                raise KeyError(
                    f"instance of {inst.cell!r} but there is no such cell. Cells: {known}")

    # ------------------------------------------------------------------ shapes

    def flatten(self) -> tuple[Poly, ...]:
        """Every polygon, placed, in instance order then polygon order within a cell.

        Deterministic by construction: two runs give the same list in the same order, and
        the GDS and SVG writers both consume exactly this.  One table, two renderers.

        A cell-local net is qualified by the placing instance's owner, so the same cell
        placed many times yields many distinct electrodes.  Without that, a control pad
        named `DC:north:1` inside a rail cell would be the *same net* on all 168 rails --
        one electrode wrapped round the whole ring, which would silently suppress every
        spacing check between neighbours and merge them in the union.
        """
        out: list[Poly] = []
        for inst in self.insts:
            cell = self.cells[inst.cell]
            for p in cell.polys:
                placed = p.placed(inst.dx, inst.dy, inst.quarter)
                net = (p.net if (p.net in self.global_nets or not inst.owner)
                       else f"{inst.owner}/{p.net}")
                if inst.owner or net != p.net:
                    placed = Poly(placed.layer, placed.xy, placed.role, net,
                                  inst.owner or placed.owner)
                out.append(placed)
        return tuple(out)

    def bbox(self) -> tuple[int, int, int, int] | None:
        polys = self.flatten()
        if not polys:
            return None
        boxes = [p.bbox() for p in polys]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def n_polys(self) -> int:
        return sum(len(self.cells[i.cell].polys) for i in self.insts)

    # ------------------------------------------------------------------- areas

    def area_nm2(self, role: str | None = None, layer: str | None = None) -> int:
        """Summed polygon area, exactly.

        This is the sum over shapes, so metal counted twice where two shapes on one net
        overlap.  For the area actually covered, union first: `union_by_net`.
        """
        return sum(p.area_nm2() for p in self.flatten()
                   if (role is None or p.role == role)
                   and (layer is None or p.layer == layer))

    def union_by_net(self, layer: str | None = None
                     ) -> dict[str, tuple[tuple[int, int, int, int], ...]]:
        """Per net, the disjoint rectangles covering exactly that net's metal.

        Every shape this package draws is a rectangle, so the union is exact.  A polygon
        that is not a rectangle raises rather than being approximated by its bounding box,
        which would quietly grow the metal.
        """
        groups: dict[str, list[tuple[int, int, int, int]]] = {}
        for p in self.flatten():
            if layer is not None and p.layer != layer:
                continue
            if not p.is_rect():
                raise ValueError(
                    f"union_by_net is exact for rectangles only, and {p.owner!r} on layer "
                    f"{p.layer!r} is not one; its bounding box would be more metal than "
                    f"was drawn")
            groups.setdefault(p.net, []).append(p.bbox())
        return {net: union_rects(boxes) for net, boxes in sorted(groups.items())}

    def union_area_nm2(self, layer: str | None = None) -> int:
        """Area actually covered, per the union, summed over nets."""
        total = 0
        for boxes in self.union_by_net(layer).values():
            total += sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes)
        return total

    # ------------------------------------------------------------------ report

    def summary(self) -> dict:
        box = self.bbox()
        roles: dict[str, int] = {}
        layers: dict[str, int] = {}
        for p in self.flatten():
            roles[p.role] = roles.get(p.role, 0) + 1
            layers[p.layer] = layers.get(p.layer, 0) + 1
        return {
            "technology": self.tech.name,
            "n_cells": len(self.cells),
            "n_insts": len(self.insts),
            "n_polys": self.n_polys(),
            "bbox_nm": list(box) if box else None,
            "polys_by_role": dict(sorted(roles.items())),
            "polys_by_layer": dict(sorted(layers.items())),
            "n_refused": len(self.refused),
            "n_violations": len(self.violations),
            "notes": list(self.notes),
        }
