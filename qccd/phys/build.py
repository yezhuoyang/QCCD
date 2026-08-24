"""Metal from a device, by derivation.  Nothing here is authored.

`build_layout(arch, tech)` is a pure function of an architecture and a technology.  No
field is added to `Node` or `Segment` for it, no section is added to `.arch.json`, and no
architecture file changes -- which is what keeps the blast radius at zero, and is also the
honest description of the situation: the metal carries no information the pair does not
already determine, so storing it would only create something to lose.

**What a segment becomes.**  Two RF rails flanking its axis at `w_g/2`, each `w_rf` wide;
one segmented centre control electrode between them; two segmented control columns
outboard at `dc_setback`.  Pads are tiled at `dc_pitch` with one fabrication gap between
them, and a site gets a `well_gap` break so that a trapping position is visible in the
metal rather than being an invisible boundary between two abutting pads.

**What a junction becomes: the paper's own counterexample, on purpose.**  A node of degree
three or more gets each incident axis's rail pair *extended straight through* the node.
That is the naive crossing, and it is known to be bad:

    "An X-junction constructed by simply crossing two linear traps, each with 99.5 um wide
     RF electrodes spaced by 41.5 um, same as in tab:junction_linear."   -- ms.tex:589

    the minimal-pseudopotential path is forced up to h = 84 um, and the confinement along
    it falls to 0.07 meV/um^2, about 30% of the optimized junction   -- ms.tex:596-604

Those numbers are computed with the *same gapless model* this package implements, not with
FEM, so they are a target `qccd/analysis/field.py` can be held to rather than a caution to
cite.  The optimized alternative in that paper is a cubic B-spline boundary fitted by
Nelder-Mead, and building an optimizer is out of scope; drawing the counterexample and
measuring how bad it is, is not.

**The naive crossing also deletes control electrodes, and the report says how many.**  A
perpendicular rail runs straight through the band where the parallel segment's outboard
control column sits, so every pad it would touch is dropped -- one fabrication gap clear,
the same rule that sets `dc_setback`.  That is a second, independent cost of the naive
geometry, and unlike the confinement it is a count rather than a simulation.  2201.12579
pays it too, with 40 um electrodes beside the junction centre and a 30 um square at it.

**Integers all the way in.**  Lattice coordinates are exact rationals times an integer
scale, so nothing rounds for any device this project ships -- and the residual is measured
rather than assumed: `Layout.notes` reports the largest rounding actually applied, which is
zero everywhere today.  A non-axis-aligned segment is refused by id rather than
approximated.  Physical coordinates never reach `qccd/viz/layout.py`, whose `COORD_MAX` of
1e6 would refuse a 20 mm die in nanometres anyway.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Mapping, Sequence

from ..arch.device import Device
from .shapes import Cell, Inst, Layout, Poly, Refusal
from .tech import Technology

__all__ = ["build_layout", "rects_for_field", "NAIVE_CROSSING_SOURCE"]

#: Why a degree>=3 node is drawn the way it is.  Carried on every crossing polygon's role
#: documentation and repeated in the layout notes, because the shape is deliberately the
#: bad one and a reader who does not know that will think it is a bug.
NAIVE_CROSSING_SOURCE = (
    "naive crossing: two linear sections simply crossed, the benchmark counterexample of "
    "arXiv:2201.12579 ms.tex:589 and 596-604 (path height rises to 84 um, confinement "
    "falls to 0.07 meV/um^2, ~30% of their optimized junction). Not a junction design.")

RF_NET = "RF"


def _axis_of(p: Sequence[float], q: Sequence[float]) -> str | None:
    """'x', 'y', or None when the segment is neither axis-aligned nor degenerate."""
    same_y = p[1] == q[1]
    same_x = p[0] == q[0]
    if same_x and same_y:
        return None
    if same_y:
        return "x"
    if same_x:
        return "y"
    return None


def _incident_axes(device: Device, node_id: str, pos_nm: Mapping) -> set[str]:
    """Which axes the segments at this node run along, ignoring refused neighbours."""
    axes = set()
    for sid in device.incidence[node_id]:
        other = device.segments[sid].other(node_id)
        if other in pos_nm:
            ax = _axis_of(device.nodes[node_id].pos, device.nodes[other].pos)
            if ax:
                axes.add(ax)
    return axes


def _has_perpendicular(device: Device, node_id: str, axis: str, pos_nm: Mapping) -> bool:
    """True when some segment at this node runs across `axis` rather than along it."""
    return any(ax != axis for ax in _incident_axes(device, node_id, pos_nm))


def _rot(dx: int, dy: int, quarter: int) -> tuple[int, int]:
    """The quarter-turn `translate_point` applies, on a direction not a point."""
    q = quarter % 4
    if q == 1:
        return (-dy, dx)
    if q == 2:
        return (-dx, -dy)
    if q == 3:
        return (dy, -dx)
    return (dx, dy)


def _arm_directions(device: Device, node_id: str, pos_nm: Mapping) -> set[tuple[int, int]]:
    """Unit directions of the axis-aligned segments leaving this node, as +/-1 pairs."""
    out: set[tuple[int, int]] = set()
    for sid in device.incidence[node_id]:
        other = device.segments[sid].other(node_id)
        if other not in pos_nm:
            continue
        ax, ay = pos_nm[node_id]
        bx, by = pos_nm[other]
        dx, dy = bx - ax, by - ay
        if dx and not dy:
            out.add((1 if dx > 0 else -1, 0))
        elif dy and not dx:
            out.add((0, 1 if dy > 0 else -1))
    return out


def unconnected_crossings(device: Device) -> tuple[tuple[str, str], ...]:
    """(segment, node) pairs where a segment runs through a node it does not touch.

    The graph says they do not meet; the plane says they do, and a planar trap has no
    overpass.  So one of the two is wrong, and which one matters: if the crossing is real
    the node's degree is understated, it is not being charged as a junction, and the router
    is free to send two ions through the same metal at once.

    This is a property of the *device*, found here only because nothing before this package
    ever asked what the coordinates meant.  It is reported, never repaired -- `arch/` is not
    this feature's to edit, and the two possible fixes say different things about the
    machine.
    """
    out: list[tuple[str, str]] = []
    for seg in device.segments.values():
        p = device.nodes[seg.ends[0]].pos
        q = device.nodes[seg.ends[1]].pos
        for node in device.nodes.values():
            if node.id in seg.ends:
                continue
            r = node.pos
            if p[0] == q[0] == r[0] and min(p[1], q[1]) < r[1] < max(p[1], q[1]):
                out.append((seg.id, node.id))
            elif p[1] == q[1] == r[1] and min(p[0], q[0]) < r[0] < max(p[0], q[0]):
                out.append((seg.id, node.id))
    return tuple(out)


def _exact_nm(coord: float, scale: int) -> tuple[int, Fraction]:
    """Nanometres, plus the exact residual that rounding threw away.

    `Fraction(float)` is the float's exact binary value, so a lattice coordinate that is a
    dyadic rational -- every one in every shipped architecture -- times an integer scale is
    an exact integer and the residual is 0.  Anything else is disclosed rather than hidden.
    """
    exact = Fraction(coord) * scale
    n = round(exact)
    return int(n), exact - n


def build_layout(arch, tech: Technology) -> Layout:
    """Derive the electrode layout of an architecture under a technology.

    Segments are walked in declaration order and junction nodes in node order, so the
    polygon list is a deterministic function of the document.
    """
    device: Device = getattr(arch, "device", arch)
    tech.require_coplanar("rf", "dc")

    sx, sy = tech.nm_per_unit_x.nm, tech.nm_per_unit_y.nm
    w_g, w_rf = tech.nm("w_g"), tech.nm("w_rf")
    gap, pitch = tech.nm("gap"), tech.nm("dc_pitch")
    dc_w, setback = tech.nm("dc_width"), tech.nm("dc_setback")
    centre_w, well_gap = tech.nm("dc_centre_width"), tech.nm("well_gap")
    extension = tech.nm("rail_end_extension")
    rf_layer, dc_layer = tech.layer("rf").name, tech.layer("dc").name

    #: half-width of the RF rail pair, and the axial reach of a crossing rail
    reach = w_g // 2 + w_rf
    if setback != reach + gap:
        raise ValueError(
            f"technology {tech.name!r} has dc_setback={setback} but w_g/2 + w_rf + gap = "
            f"{reach + gap}; the control column must clear the rails by exactly one "
            f"fabrication gap or the derivation below is drawing something else")

    refused: list[Refusal] = []
    worst_residual = Fraction(0)

    # --------------------------------------------------------------- coordinates
    pos_nm: dict[str, tuple[int, int]] = {}
    for nid, node in device.nodes.items():
        if len(node.pos) != 2:
            refused.append(Refusal("node", nid, (
                f"position {node.pos!r} is {len(node.pos)}-dimensional; this builder draws "
                f"a planar trap and will not project one")))
            continue
        (nx, rx), (ny, ry) = _exact_nm(node.pos[0], sx), _exact_nm(node.pos[1], sy)
        worst_residual = max(worst_residual, abs(rx), abs(ry))
        pos_nm[nid] = (nx, ny)

    junctions = {n for n in device.junction_nodes if n in pos_nm}

    # ------------------------------------------------------------- what to build
    cells: dict[str, Cell] = {}
    insts: list[Inst] = []
    dropped_pads = 0

    def _cell(key: str, make) -> str:
        if key not in cells:
            cells[key] = Cell(key, tuple(make()))
        return key

    for seg in device.segments.values():
        a, b = seg.ends
        if a not in pos_nm or b not in pos_nm:
            refused.append(Refusal("segment", seg.id,
                                   "an endpoint was refused, so the segment is too"))
            continue
        pa, pb = device.nodes[a].pos, device.nodes[b].pos
        axis = _axis_of(pa, pb)
        if axis is None:
            refused.append(Refusal("segment", seg.id, (
                f"{a}{tuple(pa)} -> {b}{tuple(pb)} is neither axis-aligned nor a point; "
                f"this builder emits rectilinear metal only, and a diagonal rail would "
                f"need a taper it has no rule for")))
            continue

        # order the endpoints so the segment always runs in the +axis direction, then
        # place a single cell with a quarter-turn.  Two rails of one length are one cell.
        i = 0 if axis == "x" else 1
        lo_id, hi_id = (a, b) if pos_nm[a][i] < pos_nm[b][i] else (b, a)
        origin = pos_nm[lo_id]
        length = pos_nm[hi_id][i] - pos_nm[lo_id][i]

        # a rail runs past a dead end so the last trap is not also a field discontinuity
        ext_lo = extension if device.degree(lo_id) == 1 else 0
        ext_hi = extension if device.degree(hi_id) == 1 else 0
        inset_lo = well_gap // 2 if device.nodes[lo_id].is_site else 0
        inset_hi = well_gap // 2 if device.nodes[hi_id].is_site else 0
        # A pad may not be drawn where a PERPENDICULAR rail crosses its column -- and
        # that is a question about direction, not about degree.  A degree-2 corner has a
        # perpendicular rail through it just as a degree-4 crossing does; keying this on
        # `junction_nodes` instead put an RF-to-DC short at every bend of every ring.
        keepouts = tuple(sorted(
            pos_nm[n][i] - origin[i] for n in (lo_id, hi_id)
            if _has_perpendicular(device, n, axis, pos_nm)))

        # A rail stops at a perpendicular trap's GAP.  The arm directions are global, so
        # they are rotated into the segment's own frame before choosing which side to cut.
        quarter = 0 if axis == "x" else 1
        half_g = w_g // 2
        rail_cuts: list[tuple[int, int, int]] = []
        for node in (lo_id, hi_id):
            at = pos_nm[node][i] - origin[i]
            for gx, gy in sorted(_arm_directions(device, node, pos_nm)):
                lx, ly = _rot(gx, gy, (4 - quarter) % 4)
                if ly:
                    rail_cuts.append((1 if ly > 0 else -1, at - half_g, at + half_g))
        rail_cuts = sorted(set(rail_cuts))

        key = (f"seg_L{length}_e{ext_lo}_{ext_hi}_i{inset_lo}_{inset_hi}"
               f"_k{'.'.join(str(k) for k in keepouts)}"
               f"_c{'.'.join(f'{s}:{a}:{b}' for s, a, b in rail_cuts)}")

        def make(length=length, ext_lo=ext_lo, ext_hi=ext_hi, inset_lo=inset_lo,
                 inset_hi=inset_hi, keepouts=keepouts, rail_cuts=tuple(rail_cuts)):
            return _segment_polys(
                length, ext_lo, ext_hi, inset_lo, inset_hi, keepouts, rail_cuts,
                w_g=w_g, w_rf=w_rf, gap=gap, pitch=pitch, dc_w=dc_w, setback=setback,
                centre_w=centre_w, rf_layer=rf_layer, dc_layer=dc_layer)

        _cell(key, make)
        dropped_pads += _pads_dropped(length, inset_lo, inset_hi, keepouts, pitch, gap,
                                      setback)
        insts.append(Inst(key, origin[0], origin[1], 0 if axis == "x" else 1, seg.id))

    # ------------------------------------------------------------ the crossings
    for nid in device.nodes:
        if nid not in junctions:
            continue
        axes = _incident_axes(device, nid, pos_nm)
        if len(axes) < 2:
            refused.append(Refusal("junction", nid, (
                f"degree {device.degree(nid)} but its usable segments run along "
                f"{sorted(axes) or 'no axis'}; a crossing needs two directions")))
            continue
        dirs = _arm_directions(device, nid, pos_nm)
        key = "naive_crossing_" + "".join(f"{dx}{dy}" for dx, dy in sorted(dirs))

        def make(dirs=frozenset(dirs)):
            return _crossing_polys(dirs, w_g=w_g, w_rf=w_rf, rf_layer=rf_layer)

        _cell(key, make)
        x, y = pos_nm[nid]
        insts.append(Inst(key, x, y, 0, nid))

    # ------------------------------------------------------------------- notes
    notes = [
        f"{len(cells)} cells instantiated {len(insts)} times",
        f"lattice scale: {sx} nm/unit in x, {sy} nm/unit in y "
        f"({'isotropic' if tech.is_isotropic else 'ANISOTROPIC'})",
        (f"largest coordinate rounding: {float(worst_residual):g} nm"
         if worst_residual else
         "every lattice coordinate landed on an exact nanometre; nothing rounded"),
    ]
    n_crossings = sum(1 for i in insts if i.cell.startswith("naive_crossing"))
    if n_crossings:
        notes.append(f"{n_crossings} naive crossings drawn. {NAIVE_CROSSING_SOURCE}")
        notes.append(
            f"{dropped_pads} control electrodes dropped where a perpendicular rail "
            f"crosses their column; this is the naive crossing's second cost, and it is "
            f"a count rather than a simulation")
    crossings = unconnected_crossings(device)
    if crossings:
        pairs = ", ".join(f"{s} through {n}(deg {device.degree(n)})"
                          for s, n in crossings[:6])
        notes.append(
            f"{len(crossings)} segment(s) run through a node they are not incident to "
            f"({pairs}{', ...' if len(crossings) > 6 else ''}). The graph says they do not "
            f"meet and the plane says they do, and a planar trap has no overpass. The "
            f"crossed node's degree says which case it is: degree 1 is a dock drawn ON a "
            f"rail rather than beside it -- documented for this ring's two end-cap docks "
            f"in docs/adl.md -- while degree 2 or more is two transport paths sharing "
            f"metal, where the node's degree is understated and it is not being charged "
            f"as a junction. Reported, not repaired.")
    if tech.declared():
        notes.append("technology dimensions this project chose rather than read: "
                     + ", ".join(tech.declared()))
    notes.append("clearance between metal derived from DIFFERENT elements is not checked "
                 "here; qccd/phys/drc.py does that over the flattened layout")
    return Layout(tech, cells, tuple(insts), tuple(refused), (), tuple(notes),
                  global_nets=(RF_NET,))


# ------------------------------------------------------------------ the geometry

def _pad_spans(length: int, inset_lo: int, inset_hi: int, keepouts: Sequence[int],
               pitch: int, gap: int, setback: int) -> list[tuple[int, int]]:
    """Axial extents of the control pads of one segment, in its own frame.

    Tiled from the low end at `pitch`, each pad one fabrication gap shorter than the pitch,
    inset at a site so the well shows, and dropped where a perpendicular rail crosses.
    """
    lo, hi = inset_lo, length - inset_hi
    if hi - lo < pitch - gap:
        return []
    pad = pitch - gap
    n = (hi - lo + gap) // pitch
    if n <= 0:
        return []
    used = n * pitch - gap
    start = lo + (hi - lo - used) // 2
    out = []
    for k in range(n):
        x0 = start + k * pitch
        x1 = x0 + pad
        if any(x0 < j + setback and x1 > j - setback for j in keepouts):
            continue
        out.append((x0, x1))
    return out


def _pads_dropped(length: int, inset_lo: int, inset_hi: int, keepouts: Sequence[int],
                  pitch: int, gap: int, setback: int) -> int:
    """How many pads the keep-outs removed, counted over all three columns."""
    if not keepouts:
        return 0
    full = len(_pad_spans(length, inset_lo, inset_hi, (), pitch, gap, setback))
    kept = len(_pad_spans(length, inset_lo, inset_hi, keepouts, pitch, gap, setback))
    return 3 * (full - kept)


def _split(lo: int, hi: int, cuts: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """`[lo, hi)` with every interval in `cuts` removed, in order."""
    pieces = [(lo, hi)]
    for c0, c1 in sorted(cuts):
        nxt: list[tuple[int, int]] = []
        for p0, p1 in pieces:
            if c1 <= p0 or c0 >= p1:
                nxt.append((p0, p1))
                continue
            if p0 < c0:
                nxt.append((p0, c0))
            if c1 < p1:
                nxt.append((c1, p1))
        pieces = nxt
    return pieces


def _segment_polys(length: int, ext_lo: int, ext_hi: int, inset_lo: int, inset_hi: int,
                   keepouts: Sequence[int], rail_cuts: Sequence[tuple[int, int, int]],
                   *, w_g: int, w_rf: int, gap: int, pitch: int,
                   dc_w: int, setback: int, centre_w: int, rf_layer: str,
                   dc_layer: str) -> list[Poly]:
    """One segment's metal, in a frame where it runs along +x from the origin.

    **A rail stops at a perpendicular trap's gap.**  `rail_cuts` carries `(side, x0, x1)`
    for every channel that crosses this segment, and the rail on that side is drawn in
    pieces around them.  Running the rails straight through instead -- which is the
    obvious reading of "two rail pairs extended through each other" -- puts RF metal
    across the other trap's axis, and a solve of that geometry finds **no confined ion
    position anywhere near the junction**: the pseudopotential falls monotonically to
    infinity, because the ion is directly over driven metal.  Interrupting the rails is
    what leaves the four L-shaped quadrant electrodes that 2201.12579's fig. 7(a) shows,
    and that geometry reproduces its 84 um path height to 2%.
    """
    half = w_g // 2
    out: list[Poly] = []
    for sign in (1, -1):
        y0, y1 = sorted((sign * half, sign * (half + w_rf)))
        cuts = [(c0, c1) for s, c0, c1 in rail_cuts if s == sign]
        for x0, x1 in _split(-ext_lo, length + ext_hi, cuts):
            if x1 > x0:
                out.append(Poly.rect(rf_layer, x0, y0, x1, y1,
                                     role="rail", net=RF_NET, owner=""))
    spans = _pad_spans(length, inset_lo, inset_hi, keepouts, pitch, gap, setback)
    bands = [("centre", -(centre_w // 2), centre_w // 2),
             ("north", setback, setback + dc_w),
             ("south", -(setback + dc_w), -setback)]
    for k, (x0, x1) in enumerate(spans):
        for band, y0, y1 in bands:
            out.append(Poly.rect(dc_layer, x0, y0, x1, y1, role="dc_pad",
                                 net=f"DC:{band}:{k}", owner=""))
    return out


def _crossing_polys(dirs, *, w_g: int, w_rf: int, rf_layer: str) -> list[Poly]:
    """The corner squares that join the interrupted rails around a node: the naive crossing.

    One square per (x-direction, y-direction) pair of arms actually present, so a degree-3
    T gets two and a degree-4 X gets four.  Together with the rails they are the four
    L-shaped quadrant electrodes of 2201.12579 fig. 7(a) -- the shape whose transport path
    is forced up to 84 um and whose confinement falls to 30% of the optimized junction.

    Every square is on the RF net and every one is already covered by the rails it joins,
    so it adds no metal: it exists to carry the role and the citation, and to make the
    crossings countable in a report.  Drawing the bad geometry on purpose is the point --
    see `NAIVE_CROSSING_SOURCE`.
    """
    half = w_g // 2
    xs = [d for d in dirs if d[1] == 0]
    ys = [d for d in dirs if d[0] == 0]
    out: list[Poly] = []
    for sx, _ in sorted(xs):
        for _, sy in sorted(ys):
            x0, x1 = sorted((sx * half, sx * (half + w_rf)))
            y0, y1 = sorted((sy * half, sy * (half + w_rf)))
            out.append(Poly.rect(rf_layer, x0, y0, x1, y1,
                                 role="naive_crossing", net=RF_NET, owner=""))
    return out


# ------------------------------------------------------- handing metal to the solver

def rects_for_field(layout: Layout, *, purpose: str = "rf"):
    """The RF metal as `field.Rect`s in metres -- the one place nanometres become floats.

    Nothing upstream of here has seen a float and nothing downstream will see an integer,
    which is why the conversion is a single named function rather than a division sprinkled
    through the solver.  Overlapping rails are unioned first: the gapless-plane potential
    of a set of electrodes at one potential is the potential of their union, and summing an
    overlap twice would put twice the metal there.
    """
    from .field import Rect

    layer = layout.tech.layer(purpose).name
    out = []
    for net, boxes in layout.union_by_net(layer).items():
        for x0, y0, x1, y1 in boxes:
            out.append(Rect(x0 * 1e-9, y0 * 1e-9, x1 * 1e-9, y1 * 1e-9))
    return tuple(out)
