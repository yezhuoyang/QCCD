"""Geometry generators.  PLAN §3, M0.

A generator turns a handful of parameters into the explicit `sites` / `junctions` /
`segments` / `loops` graph that everything downstream consumes.  Nothing but these
functions ever reasons about "a ring" or "a grid"; the compiler, the verifier and the
cost model see only nodes, segments, degrees and loops.

    ring(width, height, verticals)   the deck's rectangular loop with V dock spurs
    grid(a, b)                       Murali-style grid QCCD: traps on the wires,
                                     junctions at the lattice points
    chain(n)                         a linear register: no loop, no junctions

Ring slot numbering reproduces the shipped visualizer's `slot_coordinates` exactly --
top row left to right, then down the right end, then the bottom row right to left, then
up the left end -- so slot ids in an imported schedule mean the same thing here.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from .device import Device, ExpansionError, Loop, Node, Segment

__all__ = [
    "ring",
    "grid",
    "chain",
    "ladder",
    "racetrack",
    "dual_loop",
    "expand_generator",
    "GENERATORS",
]


# --------------------------------------------------------------------------- ring


def _ring_slots(width: int, height: int) -> list[tuple[str, float, float]]:
    """The perimeter cycle as `(side, x, y)`, in the deck's slot order."""
    if width < 2 or height < 2:
        raise ExpansionError("ring needs width >= 2 and height >= 2")
    slots: list[tuple[str, float, float]] = []
    for x in range(width):  # top row, left -> right
        slots.append(("top", float(x), 0.0))
    for y in range(1, height - 1):  # right end, top -> bottom
        slots.append(("right", float(width - 1), float(y)))
    for x in range(width - 1, -1, -1):  # bottom row, right -> left
        slots.append(("bottom", float(x), float(height - 1)))
    for y in range(height - 2, 0, -1):  # left end, bottom -> top
        slots.append(("left", 0.0, float(y)))
    assert len(slots) == 2 * width + 2 * height - 4
    return slots


def ring(
    width: int,
    height: int = 2,
    verticals: int = 0,
    *,
    site_zone: str = "data",
    # A DOCK SPUR ENDS IN A TRAP, and `trap` is what the zone is called: gate, measure and
    # cool, capacity 2.  The default used to be `ancilla`, a zone whose flags were `trap`'s
    # exactly under a name borrowed from error correction -- a role a code assigns, not a
    # capability of the metal.  The keyword keeps its name because architecture documents
    # in the wild store it as `ancilla_zone`; only what it defaults to changed, in step
    # with `engine.js::ring`, whose differential test would fail on any divergence.
    ancilla_zone: str = "trap",
    segment_capacity: int = 1,
    loop_id: str = "L0",
    dock_offset: int = 0,
) -> Device:
    """A rectangular transport loop of `2W + 2H - 4` slots with `V` dock spurs.

    Each vertical is a spur from an evenly spaced perimeter slot inward to a trap
    site on the mid-line.  Attaching it makes that perimeter slot degree 3, so R18 turns
    it into a junction on the rotation path -- the single most expensive structural
    decision in the shipped design (PLAN §0.5).  `verticals=0` gives the base Cyclone
    shape: a loop whose only non-straight nodes are degree-2 bends.

    `dock_offset` shifts every dock by that many slots around the loop.  The default
    puts the first dock at slot 0, which is a corner, and on a loop of height >= 3 a
    corner dock's spur lies along the side rail and fails DRC (`Codesign/findings/q06`);
    an offset that keeps every dock on a top or bottom straight costs nothing in the
    transport model and makes the device buildable.
    """
    slots = _ring_slots(width, height)
    capacity = len(slots)
    if verticals < 0:
        raise ExpansionError("verticals must be >= 0")
    if verticals and capacity % verticals:
        raise ExpansionError(
            f"{verticals} verticals do not divide {capacity} slots evenly; "
            f"the deck spaces docks uniformly around the loop"
        )
    spacing = capacity // verticals if verticals else 0
    if dock_offset < 0 or (spacing and dock_offset >= spacing):
        raise ExpansionError(
            f"dock_offset must be in [0, {spacing}) -- one dock spacing -- got {dock_offset}"
        )
    dock_slots = {(dock_offset + i * spacing) % capacity for i in range(verticals)}

    mid_x = (width - 1) / 2.0
    mid_y = (height - 1) / 2.0

    nodes: dict[str, Node] = {}
    for s, (side, x, y) in enumerate(slots):
        labels = [side]
        if s in dock_slots:
            labels.append("dock")
        nodes[f"S{s}"] = Node(
            id=f"S{s}",
            pos=(x, y),
            kind="site",
            zone_type=site_zone,
            labels=tuple(labels),
        )

    segments: dict[str, Segment] = {}
    for s in range(capacity):
        t = (s + 1) % capacity
        segments[f"E{s}"] = Segment(
            id=f"E{s}",
            ends=(f"S{s}", f"S{t}"),
            length=1.0,
            capacity=segment_capacity,
            loop=loop_id,
            labels=("rail",),
        )

    for s in sorted(dock_slots):
        side, x, y = slots[s]
        # the spur runs inward, perpendicular to the side the dock sits on, to the
        # mid-line -- which is where the deck draws its dock traps.  At a CORNER slot
        # the inward direction runs along the end-cap rail (the dock would sit on the
        # rail, at 0 degrees to it: R20, R21), so a corner's spur runs outward instead,
        # perpendicular to its row, over the same length
        corner = x in (0.0, float(width - 1)) and y in (0.0, float(height - 1))
        if corner:
            ax, ay = (x, -mid_y) if side == "top" else (x, float(height - 1) + mid_y)
        else:
            ax, ay = (x, mid_y) if side in ("top", "bottom") else (mid_x, y)
        nodes[f"A{s}"] = Node(
            id=f"A{s}",
            pos=(ax, ay),
            kind="site",
            zone_type=ancilla_zone,
            # NOT "dock": the RAIL slot carries that label (line ~110), and
            # `device.labelled("dock")` / `pipeline.py` read it to recover the dock slot
            # indices from the `S{n}` ids -- labelling the spur-end trap "dock" too
            # doubled that list and the deck artifact stopped matching the architecture.
            labels=("spur_trap",),
        )
        segments[f"V{s}"] = Segment(
            id=f"V{s}",
            ends=(f"S{s}", f"A{s}"),
            length=abs(ax - x) + abs(ay - y),
            capacity=segment_capacity,
            loop=None,
            labels=("spur",),
        )

    loops = {
        loop_id: Loop(
            id=loop_id,
            nodes=tuple(f"S{s}" for s in range(capacity)),
            closed=True,
            kind="ring",
            note="the rigid-rotation orbit; one movement template shifts every ion on it",
        )
    }
    return Device(
        nodes=nodes,
        segments=segments,
        loops=loops,
        generator="ring",
        params={
            "width": width,
            "height": height,
            "verticals": verticals,
            "site_zone": site_zone,
            "ancilla_zone": ancilla_zone,
            "segment_capacity": segment_capacity,
            "loop_id": loop_id,
            "dock_offset": dock_offset,
        },
    )


# --------------------------------------------------------------------------- grid


def grid(
    a: int,
    b: int,
    *,
    site_zone: str = "trap",
    segment_capacity: int = 1,
    spacing: int = 1,
    periodic: bool = False,
) -> Device:
    """An `a x b` lattice of junctions, with `spacing` lattice units between neighbours.

    `periodic=True` closes both directions: the last column's east wire returns to the
    first column and the top row's north wire to the bottom, so every junction is degree
    4 and every row and column is a closed loop.  That is the torus the BB code lives on
    (`Codesign/findings/q06j`), drawn as a graph -- the wrap wires are placed beyond the
    last column/row, and a physical die would fold them; the design rules are not asked
    about it here.

    This is the baseline grid QCCD of arXiv:2004.04706 and the README's description
    ("ion traps put in the middle of the wire of a grid").  Interior lattice points are
    degree-4 X-junctions, boundary points degree-3 T-junctions and the four lattice
    corners degree-2 bends, all of which fall straight out of the incidence count.

    At the default `spacing = 1` the junctions sit one lattice unit apart and the single
    trap on each wire is at the midpoint.  Trap count is `2ab - a - b`; `grid(9, 9)` gives
    exactly 144, one per data qubit of BB [[144,12,12]].

    **That default is not fabricable, and `spacing` is why the parameter exists.**  A
    crossing rail forbids control metal within `keepout_half_width` of its axis -- 180 um
    in the shipped technology -- while one lattice unit is 225 um, so a midpoint trap sits
    112.5 um from each of the two junctions that flank it and is swallowed by BOTH
    keep-outs.  Every `grid(a, b)` therefore builds with zero control electrodes per trap
    at every `a` and `b`, which `qccd.phys.drc` discloses as "no control electrodes
    survived" (`Codesign/findings/q06i`).  Raising `spacing` moves the traps off the
    junctions: at `spacing = s` the wire from one junction to the next carries `s - 1`
    traps at unit offsets, so a trap is at least one full unit from a junction and the
    outermost is `s - 1` units away.
    """
    if a < 2 or b < 2:
        raise ExpansionError("grid needs a >= 2 and b >= 2")
    if spacing < 1:
        raise ExpansionError("grid needs spacing >= 1")
    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}

    for i in range(a):
        for j in range(b):
            nodes[f"J{i}_{j}"] = Node(
                id=f"J{i}_{j}",
                pos=(float(i * spacing), float(j * spacing)),
                kind="junction",
                capacity=0,
                labels=("lattice",),
            )

    def add_node(tid: str, pos: tuple[float, float]) -> None:
        nodes[tid] = Node(
            id=tid, pos=pos, kind="site", zone_type=site_zone, labels=("trap",)
        )

    def link(sid: str, u: str, v: str, length: float) -> None:
        segments[sid] = Segment(
            id=sid, ends=(u, v), length=length, capacity=segment_capacity
        )

    def add_wire(tag: str, u: str, v: str, at) -> None:
        """The traps on one junction-to-junction wire, in order, then the chain of hops.

        At `spacing = 1` that is the single midpoint trap the baseline draws.  Above it,
        the wire carries `spacing - 1` traps at unit offsets, so the run reads
        junction, trap, trap, ..., junction and every hop is one lattice unit.
        """
        offs = [0.5] if spacing == 1 else [float(k) for k in range(1, spacing)]
        ids = []
        for k, off in enumerate(offs):
            # a wire carrying ONE trap keeps the baseline's bare name, so spacing 1 and
            # spacing 2 agree on every node id as well as every segment id
            tid = f"T{tag}" if len(offs) == 1 else f"T{tag}_{k}"
            add_node(tid, at(off))
            ids.append(tid)
        run = [u, *ids, v]
        step = 0.5 if spacing == 1 else 1.0
        # the baseline's own segment ids, unchanged wherever the wire still has two of
        # them -- which covers `spacing = 1` AND `spacing = 2`.  Those two are the SAME
        # GRAPH (one trap per wire, a segment either side); spacing 2 only pulls the
        # junctions apart so the trap clears their keep-outs.  Keeping the names means a
        # programme compiled against the unbuildable baseline replays unchanged on the
        # buildable stretch, which is how q06i prices the lattice honestly.
        names = (("a", "b") if len(run) == 3
                 else tuple(str(k) for k in range(len(run) - 1)))
        for k in range(len(run) - 1):
            link(f"T{tag}.{names[k]}", run[k], run[k + 1], step)

    for i in range(a if periodic else a - 1):
        for j in range(b):
            add_wire(f"{i}_{j}h", f"J{i}_{j}", f"J{(i + 1) % a}_{j}",
                     lambda o, i=i, j=j: (i * spacing + o, float(j * spacing)))
    for i in range(a):
        for j in range(b if periodic else b - 1):
            add_wire(f"{i}_{j}v", f"J{i}_{j}", f"J{i}_{(j + 1) % b}",
                     lambda o, i=i, j=j: (float(i * spacing), j * spacing + o))

    return Device(
        nodes=nodes,
        segments=segments,
        loops={},
        generator="grid",
        params={
            "a": a,
            "b": b,
            "site_zone": site_zone,
            "segment_capacity": segment_capacity,
            "spacing": spacing,
            "periodic": periodic,
        },
    )


# --------------------------------------------------------------------------- chain


def chain(
    n: int,
    *,
    site_zone: str = "trap",
    segment_capacity: int = 1,
    path_id: str = "P0",
) -> Device:
    """`n` sites in a line.  No junctions, no loop, two degree-1 ends.

    The degenerate architecture the whole platform has to express without special
    casing (PLAN §2): with `n = 1` and a large capacity it is the stationary chain of
    arXiv:2606.06455, the baseline that already demonstrated breakeven with no transport
    at all.
    """
    if n < 1:
        raise ExpansionError("chain needs n >= 1")
    nodes = {
        f"C{i}": Node(
            id=f"C{i}",
            pos=(float(i), 0.0),
            kind="site",
            zone_type=site_zone,
            labels=("trap",),
        )
        for i in range(n)
    }
    segments = {
        f"E{i}": Segment(
            id=f"E{i}",
            ends=(f"C{i}", f"C{i + 1}"),
            length=1.0,
            capacity=segment_capacity,
            loop=path_id if n > 1 else None,
            labels=("rail",),
        )
        for i in range(n - 1)
    }
    loops = (
        {
            path_id: Loop(
                id=path_id,
                nodes=tuple(f"C{i}" for i in range(n)),
                closed=False,
                kind="path",
                note="open register: no rigid rotation, so no single movement template",
            )
        }
        if n > 1
        else {}
    )
    return Device(
        nodes=nodes,
        segments=segments,
        loops=loops,
        generator="chain",
        params={
            "n": n,
            "site_zone": site_zone,
            "segment_capacity": segment_capacity,
            "path_id": path_id,
        },
    )


# --------------------------------------------------------------------------- ladder


def ladder(
    width: int,
    rungs: Sequence[int] | int | None = None,
    highways: int = 0,
    *,
    site_zone: str = "data",
    highway_zone: str = "trap",
    segment_capacity: int = 1,
) -> Device:
    """Two parallel rails joined by vertical rungs, with optional highway lanes.

    The deck's routing scheme B (p.5, p.16, p.18): the rails carry the data region, the
    green vertical rungs are the computing region, and separate top/bottom highways let
    an ion be ejected clear of the rails, shuttled, and re-inserted.  A rail site
    carrying both a rung and a highway on-ramp is degree 4 -- a real X-junction, which is
    what the deck's transport primitives (p.13) charge the extra step for.

    ``rungs``     x positions carrying a rail-to-rail rung.  `None` puts one at every x;
                  an int `k` spaces them every `k`.
    ``highways``  0, 1 (top) or 2 (top and bottom) extra lanes, linked to the nearer rail
                  at every rung position.
    """
    if width < 2:
        raise ExpansionError("ladder needs width >= 2")
    if highways not in (0, 1, 2):
        raise ExpansionError("ladder supports 0, 1 or 2 highways")
    if rungs is None:
        rung_x = list(range(width))
    elif isinstance(rungs, int):
        if rungs < 1:
            raise ExpansionError("rung spacing must be >= 1")
        rung_x = list(range(0, width, rungs))
    else:
        rung_x = sorted({int(x) for x in rungs})
    for x in rung_x:
        if not 0 <= x < width:
            raise ExpansionError(f"rung position {x} is outside the ladder")

    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}

    for x in range(width):
        nodes[f"T{x}"] = Node(id=f"T{x}", pos=(float(x), 1.0), kind="site",
                              zone_type=site_zone, labels=("top", "rail"))
        nodes[f"B{x}"] = Node(id=f"B{x}", pos=(float(x), 0.0), kind="site",
                              zone_type=site_zone, labels=("bottom", "rail"))
    for x in range(width - 1):
        segments[f"ET{x}"] = Segment(id=f"ET{x}", ends=(f"T{x}", f"T{x + 1}"),
                                     capacity=segment_capacity, loop="TOP",
                                     labels=("rail",))
        segments[f"EB{x}"] = Segment(id=f"EB{x}", ends=(f"B{x}", f"B{x + 1}"),
                                     capacity=segment_capacity, loop="BOTTOM",
                                     labels=("rail",))
    for x in rung_x:
        segments[f"R{x}"] = Segment(id=f"R{x}", ends=(f"B{x}", f"T{x}"),
                                    capacity=segment_capacity,
                                    labels=("rung", "compute"))

    loops = {
        "TOP": Loop(id="TOP", nodes=tuple(f"T{x}" for x in range(width)), closed=False,
                    kind="path", note="upper rail"),
        "BOTTOM": Loop(id="BOTTOM", nodes=tuple(f"B{x}" for x in range(width)),
                       closed=False, kind="path", note="lower rail"),
    }

    lanes = []
    if highways >= 1:
        lanes.append(("HT", 2.0, "T", "top"))
    if highways >= 2:
        lanes.append(("HB", -1.0, "B", "bottom"))
    for prefix, y, rail, side in lanes:
        for x in range(width):
            nodes[f"{prefix}{x}"] = Node(id=f"{prefix}{x}", pos=(float(x), y),
                                         kind="site", zone_type=highway_zone,
                                         labels=("highway", side))
        for x in range(width - 1):
            segments[f"E{prefix}{x}"] = Segment(
                id=f"E{prefix}{x}", ends=(f"{prefix}{x}", f"{prefix}{x + 1}"),
                capacity=segment_capacity, loop=prefix, labels=("highway",))
        for x in rung_x:
            segments[f"L{prefix}{x}"] = Segment(
                id=f"L{prefix}{x}", ends=(f"{rail}{x}", f"{prefix}{x}"),
                capacity=segment_capacity, labels=("onramp",))
        loops[prefix] = Loop(id=prefix,
                             nodes=tuple(f"{prefix}{x}" for x in range(width)),
                             closed=False, kind="path",
                             note=f"{side} shuttling highway")

    return Device(
        nodes=nodes, segments=segments, loops=loops, generator="ladder",
        params={"width": width, "rungs": rung_x, "highways": highways,
                "site_zone": site_zone, "highway_zone": highway_zone,
                "segment_capacity": segment_capacity},
    )


# ------------------------------------------------------------------------ racetrack


def racetrack(
    straight: int,
    *,
    site_zone: str = "trap",
    segment_capacity: int = 1,
    loop_id: str = "L0",
) -> Device:
    """A linear trap with periodic boundary conditions -- Quantinuum's H2 (2305.03828).

    One continuous RF null: the curved end zones are ordinary conveyor-belt regions
    driven by the same broadcast tiling as the straights, so by R18 they are degree-2
    bends carrying no junction cost.  Structurally a height-2 ring, which is the point --
    the platform should not need a second code path to express shipped hardware.
    """
    dev = ring(straight, 2, 0, site_zone=site_zone,
               segment_capacity=segment_capacity, loop_id=loop_id)
    return Device(nodes=dev.nodes, segments=dev.segments, loops=dev.loops,
                  generator="racetrack",
                  params={"straight": straight, "site_zone": site_zone,
                          "segment_capacity": segment_capacity, "loop_id": loop_id})


# ------------------------------------------------------------------------ dual loop


def dual_loop(
    width: int,
    couplings: Sequence[int] | int | None = None,
    *,
    data_zone: str = "data",
    # `trap`, for the same reason as `ring`'s dock zone above, and mirrored in
    # `engine.js::dualLoop`.
    ancilla_zone: str = "trap",
    segment_capacity: int = 1,
) -> Device:
    """Two concentric loops: an inner storage loop and an outer trap loop (deck p.12).

    "The data loop stays still while the outer loop rotates past it"; one rotation
    finishes one syndrome type, two complete the ESM, and Data : Ancilla = 1 : 1 at one
    ion per trap.

    ``couplings`` optionally joins the two loops at the given x positions so an ion can
    cross between them.  Each coupling makes a node on *both* loops degree 3, costing
    exactly what the shipped ring's dock spurs cost -- which is why the default is none,
    and why whether a gate needs transport or only adjacency is an open question
    (`Knowledge: q_dual_loop_gate_mechanism`).
    """
    if width < 2:
        raise ExpansionError("dual_loop needs width >= 2")
    if couplings is None:
        coupling_x: list[int] = []
    elif isinstance(couplings, int):
        coupling_x = list(range(0, width, couplings)) if couplings else []
    else:
        coupling_x = sorted({int(x) for x in couplings})

    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}
    loops: dict[str, Loop] = {}

    plan = (("D", data_zone, 1.0, 2.0, "data", "inner"),
            ("A", ancilla_zone, 0.0, 3.0, "outer_ring", "outer"))
    # The outer loop's end caps run vertically at x = 0 and x = width - 1, exactly
    # where the inner loop's end nodes sit: drawn straight, the cap passed THROUGH DT0
    # and DB0 (R21).  The inner racetrack is the shorter one, so its two end columns are
    # inset by half a slot; the ids, the loop order and every coupling in between are
    # untouched, and a coupling at an end column now runs a half-slot diagonal.
    def inner_x(tag: str, x: int) -> float:
        if tag != "D":
            return float(x)
        if x == 0:
            return 0.5
        if x == width - 1:
            return float(width - 1) - 0.5
        return float(x)

    for tag, zone, y_top, y_bot, label, side in plan:
        order: list[str] = []
        for x in range(width):
            nid = f"{tag}T{x}"
            nodes[nid] = Node(id=nid, pos=(inner_x(tag, x), y_top), kind="site",
                              zone_type=zone, labels=(label, side, "top"))
            order.append(nid)
        for x in range(width - 1, -1, -1):
            nid = f"{tag}B{x}"
            nodes[nid] = Node(id=nid, pos=(inner_x(tag, x), y_bot), kind="site",
                              zone_type=zone, labels=(label, side, "bottom"))
            order.append(nid)
        for i in range(len(order)):
            j = (i + 1) % len(order)
            segments[f"E{tag}{i}"] = Segment(
                id=f"E{tag}{i}", ends=(order[i], order[j]),
                capacity=segment_capacity, loop=tag, labels=("rail",))
        loops[tag] = Loop(id=tag, nodes=tuple(order), closed=True, kind="ring",
                          note=f"{label} loop")

    for x in coupling_x:
        segments[f"C{x}"] = Segment(id=f"C{x}", ends=(f"DT{x}", f"AT{x}"),
                                    capacity=segment_capacity, labels=("coupling",))

    return Device(
        nodes=nodes, segments=segments, loops=loops, generator="dual_loop",
        params={"width": width, "couplings": coupling_x, "data_zone": data_zone,
                "ancilla_zone": ancilla_zone, "segment_capacity": segment_capacity},
    )


# --------------------------------------------------------------------------- dispatch

def cylinder(
    a: int,
    b: int,
    *,
    wrap_spokes: bool = False,
    declare_loops: bool = False,
    site_zone: str = "trap",
    segment_capacity: int = 1,
    r0: float = 2.0,
    pitch: float = 1.0,
) -> Device:
    """The torus grid drawn as a chip: `b` concentric closed loops joined by `a` spokes.

    A periodic lattice is not planar, so `grid(..., periodic=True)` cannot be fabricated
    as it is drawn -- its wrap wires cross the whole die.  This is the drawing with the
    fewest crossings there is.  Each row `j` of the torus is a closed loop of `a` traps at
    radius `r0 + j*pitch`; each column `i` is a radial spoke, one trap per ring gap; so the
    x-direction wraps for free and nothing crosses.  That is a CYLINDER, `C_a x P_b`, and
    it is planar (`Codesign/findings/q06k`).

    `wrap_spokes=True` adds the y-wrap too: a spoke from the outer ring back to the inner
    one at every column, run between the regular spokes so it crosses the `b - 2`
    intermediate rings on their wires.  Every crossing is a degree-4 node inserted into
    the ring wire between its junction and its trap, which is what a crossing IS in a
    surface trap, and what the cost model charges as a junction (R18).  That makes the
    drawing a torus at `a*(b-2)` crossings -- the known minimum for `C_a x C_b` -- and it
    puts one crossing on every ring wire of the inner `b - 2` rings, so the price of the
    y-wrap is paid on the x-hops.  Whether that is worth it is a measurement, not a rule.

    Node ids follow `grid`: `J{i}_{j}`, ring wires `T{i}_{j}h` (from `J{i}_{j}` towards
    `J{i+1}_{j}`), spoke wires `T{i}_{j}v` (from ring `j` out to ring `j+1`); the wrap
    spoke's trap is `T{i}_{b-1}v` and its crossings `X{i}_{r}`.
    """
    import math

    if a < 3 or b < 2:
        raise ExpansionError("cylinder needs a >= 3 and b >= 2")
    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}
    loops: dict[str, Loop] = {}

    def at(i: float, j: float) -> tuple[float, float]:
        r = r0 + j * pitch
        th = 2 * math.pi * i / a
        return (round(r * math.cos(th), 6), round(r * math.sin(th), 6))

    def site(tid: str, pos) -> None:
        nodes[tid] = Node(id=tid, pos=pos, kind="site", zone_type=site_zone, labels=("trap",))

    def link(sid: str, u: str, v: str, length: float = 0.5, loop: str | None = None) -> None:
        segments[sid] = Segment(id=sid, ends=(u, v), length=length,
                                capacity=segment_capacity, loop=loop)

    for i in range(a):
        for j in range(b):
            nodes[f"J{i}_{j}"] = Node(id=f"J{i}_{j}", pos=at(i, j), kind="junction",
                                      capacity=0, labels=("lattice",))
    # the rings: closed loops, one trap per wire
    for j in range(b):
        lid = f"R{j}"
        tag = lid if declare_loops else None
        order: list[str] = []
        for i in range(a):
            tid = f"T{i}_{j}h"
            site(tid, at(i + 0.5, j))
            link(f"{tid}.a", f"J{i}_{j}", tid, loop=tag)
            link(f"{tid}.b", tid, f"J{(i + 1) % a}_{j}", loop=tag)
            order += [f"J{i}_{j}", tid]
        if declare_loops:
            loops[lid] = Loop(id=lid, nodes=tuple(order), closed=True, kind="ring")
    # the spokes: open, one trap per ring gap
    for i in range(a):
        for j in range(b - 1):
            tid = f"T{i}_{j}v"
            site(tid, at(i, j + 0.5))
            link(f"{tid}.a", f"J{i}_{j}", tid)
            link(f"{tid}.b", tid, f"J{i}_{j + 1}")
    if wrap_spokes:
        # outer ring back to inner, between spokes i and i+1, crossing rings 1..b-2 on
        # the wire T{i}_{r}h -- inserted between the wire's junction and its trap
        for i in range(a):
            tid = f"T{i}_{b - 1}v"
            site(tid, at(i + 0.5, b - 0.5))
            chain = [f"J{i}_{b - 1}", tid]
            for r in range(b - 2, 0, -1):
                x = f"X{i}_{r}"
                nodes[x] = Node(id=x, pos=at(i + 0.5, r + 0.02), kind="junction",
                                capacity=0, labels=("crossing",))
                # split the ring wire's first segment J -> T into J -> X -> T
                w = f"T{i}_{r}h"
                old = segments.pop(f"{w}.a")
                link(f"{w}.a", f"J{i}_{r}", x, loop=old.loop)
                link(f"{w}.x", x, w, loop=old.loop)
                if f"R{r}" in loops:
                    lp = loops[f"R{r}"]
                    nl = list(lp.nodes)
                    nl.insert(nl.index(w), x)
                    loops[f"R{r}"] = Loop(id=lp.id, nodes=tuple(nl), closed=True, kind="ring")
                chain.append(x)
            chain.append(f"J{i}_0")
            for k in range(len(chain) - 1):
                link(f"{tid}.{k}", chain[k], chain[k + 1])
    return Device(
        nodes=nodes, segments=segments, loops=loops, generator="cylinder",
        params={"a": a, "b": b, "wrap_spokes": wrap_spokes, "declare_loops": declare_loops,
                "site_zone": site_zone,
                "segment_capacity": segment_capacity, "r0": r0, "pitch": pitch},
    )


GENERATORS = {
    "cylinder": cylinder,
    "ring": ring,
    "grid": grid,
    "chain": chain,
    "ladder": ladder,
    "racetrack": racetrack,
    "dual_loop": dual_loop,
}


def expand_generator(
    name: str, params: Mapping, zone_types: Mapping[str, Mapping] | None = None
) -> Device:
    """Expand `geometry.generator` + `geometry.params` into a `Device`."""
    if name == "explicit":
        raise ExpansionError(
            "geometry.generator is 'explicit' but the document carries no `nodes`"
        )
    try:
        fn = GENERATORS[name]
    except KeyError:
        known = ", ".join(sorted(GENERATORS))
        raise ExpansionError(f"unknown generator {name!r} (have: {known})") from None
    kwargs = dict(params)
    if zone_types is not None:
        for key in ("site_zone", "ancilla_zone"):
            if key in kwargs and kwargs[key] not in zone_types:
                raise ExpansionError(
                    f"geometry.params.{key} = {kwargs[key]!r} is not a declared zone type"
                )
    try:
        return fn(**kwargs)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ExpansionError(f"generator {name!r}: {exc}") from None
