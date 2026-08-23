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
    ancilla_zone: str = "ancilla",
    segment_capacity: int = 1,
    loop_id: str = "L0",
) -> Device:
    """A rectangular transport loop of `2W + 2H - 4` slots with `V` dock spurs.

    Each vertical is a spur from an evenly spaced perimeter slot inward to an ancilla
    site on the mid-line.  Attaching it makes that perimeter slot degree 3, so R18 turns
    it into a junction on the rotation path -- the single most expensive structural
    decision in the shipped design (PLAN §0.5).  `verticals=0` gives the base Cyclone
    shape: a loop whose only non-straight nodes are degree-2 bends.
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
    dock_slots = {i * spacing for i in range(verticals)}

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
        # mid-line -- which is where the deck draws its ancillas
        ax, ay = (x, mid_y) if side in ("top", "bottom") else (mid_x, y)
        nodes[f"A{s}"] = Node(
            id=f"A{s}",
            pos=(ax, ay),
            kind="site",
            zone_type=ancilla_zone,
            labels=("ancilla",),
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
        },
    )


# --------------------------------------------------------------------------- grid


def grid(
    a: int,
    b: int,
    *,
    site_zone: str = "trap",
    segment_capacity: int = 1,
) -> Device:
    """An `a x b` lattice of junctions with one trap in the middle of every wire.

    This is the baseline grid QCCD of arXiv:2004.04706 and the README's description
    ("ion traps put in the middle of the wire of a grid").  Interior lattice points are
    degree-4 X-junctions, boundary points degree-3 T-junctions and the four lattice
    corners degree-2 bends, all of which fall straight out of the incidence count.

    Trap count is `2ab - a - b`; `grid(9, 9)` gives exactly 144, one per data qubit of
    BB [[144,12,12]].
    """
    if a < 2 or b < 2:
        raise ExpansionError("grid needs a >= 2 and b >= 2")
    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}

    for i in range(a):
        for j in range(b):
            nodes[f"J{i}_{j}"] = Node(
                id=f"J{i}_{j}",
                pos=(float(i), float(j)),
                kind="junction",
                capacity=0,
                labels=("lattice",),
            )

    def add_trap(tid: str, pos: tuple[float, float], u: str, v: str) -> None:
        nodes[tid] = Node(
            id=tid, pos=pos, kind="site", zone_type=site_zone, labels=("trap",)
        )
        segments[f"{tid}.a"] = Segment(
            id=f"{tid}.a", ends=(u, tid), length=0.5, capacity=segment_capacity
        )
        segments[f"{tid}.b"] = Segment(
            id=f"{tid}.b", ends=(tid, v), length=0.5, capacity=segment_capacity
        )

    for i in range(a - 1):
        for j in range(b):
            add_trap(f"T{i}_{j}h", (i + 0.5, float(j)), f"J{i}_{j}", f"J{i + 1}_{j}")
    for i in range(a):
        for j in range(b - 1):
            add_trap(f"T{i}_{j}v", (float(i), j + 0.5), f"J{i}_{j}", f"J{i}_{j + 1}")

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
    ancilla_zone: str = "ancilla",
    segment_capacity: int = 1,
) -> Device:
    """Two concentric loops: an inner data loop and an outer ancilla loop (deck p.12).

    "The data loop stays still while the ancilla loop rotates past it"; one rotation
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
            ("A", ancilla_zone, 0.0, 3.0, "ancilla", "outer"))
    for tag, zone, y_top, y_bot, label, side in plan:
        order: list[str] = []
        for x in range(width):
            nid = f"{tag}T{x}"
            nodes[nid] = Node(id=nid, pos=(float(x), y_top), kind="site",
                              zone_type=zone, labels=(label, side, "top"))
            order.append(nid)
        for x in range(width - 1, -1, -1):
            nid = f"{tag}B{x}"
            nodes[nid] = Node(id=nid, pos=(float(x), y_bot), kind="site",
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

GENERATORS = {
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
