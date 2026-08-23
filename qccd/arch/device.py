"""The expanded architecture: a capacitated trap multigraph plus its transport loops.

Everything downstream consumes this, never the generator parameters.  Two properties are
*derived here and nowhere else*, because the whole cost model hangs off them:

**Degree.**  `Device.degree(node)` counts segment incidences in the expanded graph.
Rule R18 then reads directly off it -- a node is a junction iff its degree is >= 3, and
the junction primitive is charged by that degree.  Nothing declares "this is a junction";
drawing a spur onto a rail makes the rail node degree 3 and it *becomes* one.  That is
the point: the 24 vertical shuttling lines of the shipped design are expensive because
of what they do to the graph, not because anyone labelled them expensive.

**Corners.**  A corner is a property of a *transport loop*, not of a node in isolation.
At a dock the spur and the rail can be collinear (in the shipped ring the two corner
docks sit exactly on the end-caps), so deriving "the direction changed here" from all
incident segments misclassifies every T-junction.  Corners are therefore derived by
walking the loop's cyclic node order and comparing the in- and out-directions there.

The deck's ring model charges a *corner segment* -- one whose two endpoints are both
loop corners, i.e. a segment that contains an entire turn -- three primitive hops
(`Knowledge: fd_rotation_is_on_ions`).  In the shipped 2x72 ring the two end-caps are
exactly those segments, giving 142 x 1 + 2 x 3 = 148 cost and depth 3 per rigid hop.
PLAN §0.5 supersedes that number (a bend is one shuttle), but the deck model has to be
reproducible to serve as M1's oracle, so the graph records the structure and the cost
model in `qccd.cost` decides what to charge for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Iterable, Mapping, Sequence

from .control import ControlPlane, build_control_plane
from .curves import PrimitiveSet
from .schema import MIN_LOOP_NODES, SCHEMA_VERSION, check

__all__ = [
    "Node",
    "Segment",
    "Loop",
    "Device",
    "Architecture",
    "ExpansionError",
    "resolve_capacities",
]

_ROUND = 9


class ExpansionError(ValueError):
    """A structural problem in the expanded graph (dangling ids, degree mismatch, ...)."""


# The two structural messages that have TWO callers: `check_structure`, which reports
# everything, and `dangling_references`, which reports only what blocks serialization.
# Written once so the two can never drift into saying the same thing differently.
def _msg_unknown_endpoint(sid: str, end: str) -> str:
    return f"segment {sid!r}: unknown endpoint {end!r}"


def _msg_unknown_loop_node(lid: str, nid: str) -> str:
    return f"loop {lid!r}: unknown node {nid!r}"


def _hist(values) -> dict[int, int]:
    out: dict[int, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _unit(a: Sequence[float], b: Sequence[float]) -> tuple[float, ...]:
    """Canonical unit direction from `a` to `b`, robust to float noise."""
    d = [float(y) - float(x) for x, y in zip(a, b)]
    scale = max((abs(c) for c in d), default=0.0)
    if scale == 0.0:
        return tuple(0.0 for _ in d)
    return tuple(round(c / scale, _ROUND) + 0.0 for c in d)


@dataclass(frozen=True, slots=True)
class Node:
    """A trap site (`kind="site"`, capacity >= 1) or a bare junction (`kind="junction"`)."""

    id: str
    pos: tuple[float, ...]
    kind: str = "site"
    capacity: int = 0
    zone_type: str | None = None
    labels: tuple[str, ...] = ()
    #: True when this site's capacity was set on the site itself rather than inherited
    #: from its zone type.  Retuning the zone then leaves it alone -- a trap you
    #: deliberately made bigger should not silently shrink when the zone changes.
    capacity_explicit: bool = False

    @property
    def is_site(self) -> bool:
        return self.kind == "site"

    def has(self, label: str) -> bool:
        return label in self.labels

    def to_json(self, *, degree: int, corner: bool) -> dict:
        d: dict = {"id": self.id, "pos": list(self.pos), "kind": self.kind}
        if self.kind == "site" or self.capacity:
            d["capacity"] = self.capacity
        if self.capacity_explicit:
            d["capacity_explicit"] = True
        if self.zone_type is not None:
            d["zone_type"] = self.zone_type
        if self.labels:
            d["labels"] = list(self.labels)
        d["degree"] = degree
        d["corner"] = corner
        return d

    @staticmethod
    def from_json(d: Mapping) -> "Node":
        return Node(
            id=str(d["id"]),
            pos=tuple(float(x) for x in d["pos"]),
            kind=str(d.get("kind", "site")),
            capacity=int(d.get("capacity", 0)),
            zone_type=d.get("zone_type"),
            labels=tuple(d.get("labels", ())),
            capacity_explicit=bool(d.get("capacity_explicit", False)),
        )


@dataclass(frozen=True, slots=True)
class Segment:
    """A shuttling segment.  `capacity` is the R3 occupancy bound (normally 1)."""

    id: str
    ends: tuple[str, str]
    length: float = 1.0
    capacity: int = 1
    loop: str | None = None
    labels: tuple[str, ...] = ()

    def other(self, node_id: str) -> str:
        a, b = self.ends
        if node_id == a:
            return b
        if node_id == b:
            return a
        raise KeyError(f"{node_id!r} is not an endpoint of segment {self.id!r}")

    def to_json(self, *, corner_endpoints: int) -> dict:
        d: dict = {
            "id": self.id,
            "ends": list(self.ends),
            "length": self.length,
            "capacity": self.capacity,
            "loop": self.loop,
        }
        if self.labels:
            d["labels"] = list(self.labels)
        d["corner_endpoints"] = corner_endpoints
        return d

    @staticmethod
    def from_json(d: Mapping) -> "Segment":
        ends = tuple(str(x) for x in d["ends"])
        if len(ends) != 2:
            raise ExpansionError(f"segment {d.get('id')!r} needs exactly two ends")
        return Segment(
            id=str(d["id"]),
            ends=(ends[0], ends[1]),
            length=float(d.get("length", 1.0)),
            capacity=int(d.get("capacity", 1)),
            loop=d.get("loop"),
            labels=tuple(d.get("labels", ())),
        )


@dataclass(frozen=True, slots=True)
class Loop:
    """A named transport path.

    A closed loop is the domain of exactly one movement template -- "shift every ion on
    L by k" -- which is the whole reason rigid rotation needs one SIMD class where an
    odd-even sort needs many (PLAN §1).  Keeping it in the geometry rather than
    rediscovering cycles at compile time is what makes that template expressible.
    """

    id: str
    nodes: tuple[str, ...]
    closed: bool = True
    kind: str = "ring"
    note: str | None = None

    def __len__(self) -> int:
        return len(self.nodes)

    def index_of(self) -> dict[str, int]:
        return {n: i for i, n in enumerate(self.nodes)}

    def to_json(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "nodes": list(self.nodes), "closed": self.closed}
        if self.note:
            d["note"] = self.note
        return d

    @staticmethod
    def from_json(d: Mapping) -> "Loop":
        return Loop(
            id=str(d["id"]),
            nodes=tuple(str(n) for n in d["nodes"]),
            closed=bool(d["closed"]),
            kind=str(d.get("kind", "ring")),
            note=d.get("note"),
        )


@dataclass(frozen=True)
class Device:
    """The expanded graph.  Immutable; every derived quantity is cached."""

    nodes: Mapping[str, Node]
    segments: Mapping[str, Segment]
    loops: Mapping[str, Loop] = field(default_factory=dict)
    generator: str = "explicit"
    params: Mapping[str, object] = field(default_factory=dict)

    # ---------------------------------------------------------------- incidence

    @cached_property
    def incidence(self) -> Mapping[str, tuple[str, ...]]:
        """node id -> the ids of every segment incident to it, in declaration order."""
        inc: dict[str, list[str]] = {n: [] for n in self.nodes}
        for seg in self.segments.values():
            for end in seg.ends:
                if end not in inc:
                    raise ExpansionError(
                        f"segment {seg.id!r} references unknown node {end!r}"
                    )
                inc[end].append(seg.id)
        return {k: tuple(v) for k, v in inc.items()}

    def degree(self, node_id: str) -> int:
        """Segment incidence count.  A self-loop would count twice, by construction."""
        return len(self.incidence[node_id])

    def neighbours(self, node_id: str) -> tuple[str, ...]:
        return tuple(self.segments[s].other(node_id) for s in self.incidence[node_id])

    def segment_between(self, a: str, b: str) -> Segment:
        for sid in self.incidence[a]:
            seg = self.segments[sid]
            if seg.other(a) == b:
                return seg
        raise KeyError(f"no segment between {a!r} and {b!r}")

    # ---------------------------------------------------------------- R18

    @cached_property
    def junction_nodes(self) -> tuple[str, ...]:
        """Nodes of degree >= 3 -- junctions in the sense of R18, in id order."""
        return tuple(sorted(n for n in self.nodes if self.degree(n) >= 3))

    @cached_property
    def bend_nodes(self) -> tuple[str, ...]:
        """Degree-2 nodes at which a transport loop turns: bends, not junctions (R18)."""
        corners = self.all_corners
        return tuple(sorted(n for n in corners if self.degree(n) == 2))

    def is_junction(self, node_id: str) -> bool:
        return self.degree(node_id) >= 3

    # ---------------------------------------------------------------- corners

    def corners(self, loop_id: str) -> frozenset[str]:
        """Nodes at which the named loop changes direction."""
        loop = self.loops[loop_id]
        seq = loop.nodes
        k = len(seq)
        out: set[str] = set()
        rng = range(k) if loop.closed else range(1, k - 1)
        for i in rng:
            prev = seq[(i - 1) % k]
            nxt = seq[(i + 1) % k]
            here = seq[i]
            if _unit(self.nodes[prev].pos, self.nodes[here].pos) != _unit(
                self.nodes[here].pos, self.nodes[nxt].pos
            ):
                out.add(here)
        return frozenset(out)

    @cached_property
    def geometric_bends(self) -> frozenset[str]:
        """Degree-2 nodes whose two incident segments are not collinear.

        Complements the loop test.  A turn can happen where two *different* named paths
        meet -- the degree-2 corner junctions of a grid, for instance -- which no single
        loop walk sees.  The test is only sound at degree 2, where "the two directions"
        is unambiguous; at a T-junction the spur is a third direction that means nothing
        about whether the rail turns, which is exactly why corners on a loop are found
        by walking the loop instead.
        """
        out: set[str] = set()
        for nid in self.nodes:
            inc = self.incidence[nid]
            if len(inc) != 2:
                continue
            a = self.segments[inc[0]].other(nid)
            b = self.segments[inc[1]].other(nid)
            d1 = _unit(self.nodes[nid].pos, self.nodes[a].pos)
            d2 = _unit(self.nodes[nid].pos, self.nodes[b].pos)
            if tuple(-c + 0.0 for c in d1) != d2:
                out.add(nid)
        return frozenset(out)

    @cached_property
    def all_corners(self) -> frozenset[str]:
        out: set[str] = set(self.geometric_bends)
        for lid in self.loops:
            out |= self.corners(lid)
        return frozenset(out)

    @cached_property
    def corner_endpoints(self) -> Mapping[str, int]:
        """segment id -> how many of its endpoints are corners of the loop it lies on.

        2 means the segment *contains* a whole turn (the end-cap of a height-2 ring);
        that is what the deck charges `corner_hops` for.  Segments off every loop get 0.
        """
        corners = self.all_corners
        out: dict[str, int] = {}
        for seg in self.segments.values():
            if seg.loop is None or seg.loop not in self.loops:
                out[seg.id] = 0
                continue
            out[seg.id] = sum(1 for e in seg.ends if e in corners)
        return out

    # ---------------------------------------------------------------- loops

    def loop_segments(self, loop_id: str) -> tuple[Segment, ...]:
        """The loop's segments in traversal order (node i -> node i+1)."""
        loop = self.loops[loop_id]
        seq = loop.nodes
        pairs = list(zip(seq, seq[1:]))
        if loop.closed:
            pairs.append((seq[-1], seq[0]))
        return tuple(self.segment_between(a, b) for a, b in pairs)

    def shift_map(self, loop_id: str, delta: int) -> Mapping[str, str]:
        """The rigid-rotation movement template: node -> node after shifting by `delta`.

        `delta > 0` advances along the loop's declared node order.  This single mapping
        *is* the one movement primitive that PLAN §1's thesis rests on.
        """
        loop = self.loops[loop_id]
        if not loop.closed and delta != 0:
            raise ValueError(f"loop {loop_id!r} is open; a rigid shift is undefined")
        seq = loop.nodes
        k = len(seq)
        return {seq[i]: seq[(i + delta) % k] for i in range(k)}

    # ---------------------------------------------------------------- queries

    def sites(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes.values() if n.is_site)

    def labelled(self, label: str) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes.values() if n.has(label))

    def total_capacity(self) -> int:
        return sum(n.capacity for n in self.nodes.values())

    def summary(self) -> dict:
        """The counts M0's acceptance criterion is phrased in."""
        deg_hist: dict[int, int] = {}
        for n in self.nodes:
            deg_hist[self.degree(n)] = deg_hist.get(self.degree(n), 0) + 1
        corners = self.all_corners
        docks = {n.id for n in self.labelled("dock")}
        return {
            "generator": self.generator,
            "params": dict(self.params),
            "n_nodes": len(self.nodes),
            "n_sites": len(self.sites()),
            "n_segments": len(self.segments),
            "n_loops": len(self.loops),
            "total_capacity": self.total_capacity(),
            "capacity_histogram": _hist(
                n.capacity for n in self.nodes.values() if n.kind == "site"),
            "n_site_capacity_overrides": sum(
                1 for n in self.nodes.values() if n.capacity_explicit),
            "degree_histogram": dict(sorted(deg_hist.items())),
            "n_junction_nodes": len(self.junction_nodes),
            "n_corners": len(corners),
            "n_bends": len(self.bend_nodes),
            "n_docks": len(docks),
            "n_dock_corners": len(docks & corners),
            "corner_ids": sorted(corners),
            "n_corner_segments": sum(1 for v in self.corner_endpoints.values() if v == 2),
        }

    # ---------------------------------------------------------------- validation

    def dangling_references(self) -> list[str]:
        """Ids a segment or a loop names that the node table does not hold.

        These are the two structural errors `to_json` CANNOT SURVIVE: `all_corners` walks
        every loop and reads `self.nodes[nxt].pos`, so a loop naming an unknown node dies
        as a bare `KeyError: 'ZZ'` deep inside `corners` -- and `Machine.blank_device`
        serializes before `Architecture.from_json` ever gets to `check_structure`, which
        holds the right words.  A dispatcher (`qccd.arch.edit.apply_call`) cannot be more
        careful than the constructor it dispatches into, so the constructor asks this
        first.  `check_structure` uses the same two message builders, so the wording has
        exactly one home.
        """
        errors: list[str] = []
        for seg in self.segments.values():
            for end in seg.ends:
                if end not in self.nodes:
                    errors.append(_msg_unknown_endpoint(seg.id, end))
        for loop in self.loops.values():
            for nid in loop.nodes:
                if nid not in self.nodes:
                    errors.append(_msg_unknown_loop_node(loop.id, nid))
        return errors

    def check_structure(self, declared: Mapping[str, Mapping] | None = None) -> list[str]:
        """Structural checks that need the expanded graph.

        `declared` optionally maps node/segment id -> the cached derived fields read out
        of a file, so a hand-edited `.arch.json` cannot quietly disagree with its own
        graph.
        """
        errors: list[str] = []
        for seg in self.segments.values():
            for end in seg.ends:
                if end not in self.nodes:
                    errors.append(_msg_unknown_endpoint(seg.id, end))
            if seg.ends[0] == seg.ends[1]:
                errors.append(f"segment {seg.id!r}: self-loop is not a transport segment")
            if seg.loop is not None and seg.loop not in self.loops:
                errors.append(f"segment {seg.id!r}: unknown loop {seg.loop!r}")
        seen_pairs: dict[frozenset[str], str] = {}
        for seg in self.segments.values():
            key = frozenset(seg.ends)
            if len(key) == 2 and key in seen_pairs:
                errors.append(
                    f"segment {seg.id!r} duplicates {seen_pairs[key]!r} "
                    f"between {sorted(key)} -- parallel segments are not modelled"
                )
            seen_pairs[key] = seg.id
        for loop in self.loops.values():
            # THE INVARIANT THE SCHEMA ALWAYS HAD AND THIS CHECK DID NOT.  A loop with
            # fewer than two nodes has no consecutive pair at all: `loop_segments` yields
            # nothing, `shift_map` moves nothing, `corners` finds nothing.  It is not a
            # small loop, it is an empty annotation -- and `$.geometry.loops[].nodes` has
            # required two since the schema was written.  Without this line an edit could
            # leave a device that every structural check passed and `Architecture.from_json`
            # refused, which is precisely how deleting the one segment of a two-node chain
            # produced an unloadable export.  `qccd.arch.edit` repairs the loop away before
            # it ever gets here; this is the backstop that proves the repair is complete
            # and that any FUTURE edit op cannot reopen the hole.
            if len(loop.nodes) < MIN_LOOP_NODES:
                errors.append(
                    f"loop {loop.id!r}: has {len(loop.nodes)} node(s); a transport loop "
                    f"needs at least {MIN_LOOP_NODES}, because a shorter one has no "
                    f"segment to walk and no .arch.json can hold it")
            if len(set(loop.nodes)) != len(loop.nodes):
                errors.append(f"loop {loop.id!r}: repeats a node")
            for nid in loop.nodes:
                if nid not in self.nodes:
                    errors.append(_msg_unknown_loop_node(loop.id, nid))
            if errors:
                continue
            try:
                self.loop_segments(loop.id)
            except KeyError as exc:
                errors.append(f"loop {loop.id!r}: {exc}")
        for node in self.nodes.values():
            if node.kind == "site" and node.capacity < 1:
                errors.append(f"node {node.id!r}: a site needs capacity >= 1")
            if node.kind == "junction" and node.capacity != 0:
                errors.append(
                    f"node {node.id!r}: a bare junction holds no ions, so capacity must be 0"
                )
        if errors:
            return errors  # derived checks below assume a well-formed graph

        if declared:
            corners = self.all_corners
            for nid, d in declared.get("nodes", {}).items():  # type: ignore[union-attr]
                if "degree" in d and d["degree"] != self.degree(nid):
                    errors.append(
                        f"node {nid!r}: file says degree {d['degree']}, "
                        f"graph gives {self.degree(nid)}"
                    )
                if "corner" in d and bool(d["corner"]) != (nid in corners):
                    errors.append(
                        f"node {nid!r}: file says corner={d['corner']}, "
                        f"loop geometry gives {nid in corners}"
                    )
            ce = self.corner_endpoints
            for sid, d in declared.get("segments", {}).items():  # type: ignore[union-attr]
                if "corner_endpoints" in d and d["corner_endpoints"] != ce[sid]:
                    errors.append(
                        f"segment {sid!r}: file says corner_endpoints="
                        f"{d['corner_endpoints']}, geometry gives {ce[sid]}"
                    )
        return errors

    def reproducible_from_generator(self) -> bool:
        """Would re-running the generator on `params` give back exactly this graph?

        False for an explicit geometry, for a hand-edited expansion, or for any device
        whose params no longer describe it -- in which case only the expanded form is a
        faithful serialization.
        """
        if self.generator == "explicit":
            return False
        from .generators import GENERATORS

        if self.generator not in GENERATORS:
            return False
        try:
            other = GENERATORS[self.generator](**dict(self.params))  # type: ignore[arg-type]
        except Exception:
            return False
        if set(other.nodes) != set(self.nodes) or set(other.segments) != set(self.segments):
            return False
        for nid, node in self.nodes.items():
            theirs = other.nodes[nid]
            # capacity is resolved from the zone types after expansion, so compare
            # everything the generator itself is responsible for
            if (node.pos, node.kind, node.zone_type, node.labels,
                    node.capacity_explicit) != (
                theirs.pos, theirs.kind, theirs.zone_type, theirs.labels,
                theirs.capacity_explicit
            ):
                return False
            if node.capacity_explicit and node.capacity != theirs.capacity:
                return False
        for sid, seg in self.segments.items():
            if seg != other.segments[sid]:
                return False
        return {k: v.nodes for k, v in self.loops.items()} == {
            k: v.nodes for k, v in other.loops.items()
        }

    # ---------------------------------------------------------------- json

    def to_json(self) -> dict:
        corners = self.all_corners
        ce = self.corner_endpoints
        # ONE ordered array, in `self.nodes` order.  The old `sites` + `junctions` split
        # was a LOSSY encoding of an ordered dict -- `from_json` rebuilt as
        # `sites + junctions`, so `grid`, which interleaves, came back reordered.  Node
        # order is load-bearing twice over: `architecture_listing(mode="explicit")`
        # emits statements in it (the same architecture differed by 166 lines before and
        # after a reload), and `layout._bows` sums a centroid over `pos.values()` in it,
        # where a 2-5 ulp difference flips a collinear tie-break and a segment's bow sign.
        nodes = [
            n.to_json(degree=self.degree(n.id), corner=n.id in corners)
            for n in self.nodes.values()
        ]
        d: dict = {"generator": self.generator, "params": dict(self.params), "nodes": nodes}
        d["segments"] = [s.to_json(corner_endpoints=ce[s.id]) for s in self.segments.values()]
        if self.loops:
            d["loops"] = [l.to_json() for l in self.loops.values()]
        return d

    @staticmethod
    def from_json(d: Mapping) -> tuple["Device", dict]:
        """Parse an explicit geometry block.  Returns `(device, declared_derived)`."""
        nodes: dict[str, Node] = {}
        declared_nodes: dict[str, dict] = {}
        # `nodes` when the writer recorded an order; otherwise the legacy split, which
        # cannot promise an order it never wrote down.  Not retroactive, and cannot be.
        raw_nodes = (list(d["nodes"]) if d.get("nodes") is not None
                     else list(d.get("sites", ())) + list(d.get("junctions", ())))
        for raw in raw_nodes:
            node = Node.from_json(raw)
            if node.id in nodes:
                raise ExpansionError(f"duplicate node id {node.id!r}")
            nodes[node.id] = node
            cached = {k: raw[k] for k in ("degree", "corner") if k in raw}
            if cached:
                declared_nodes[node.id] = cached
        segments: dict[str, Segment] = {}
        declared_segments: dict[str, dict] = {}
        for raw in d.get("segments", ()):
            seg = Segment.from_json(raw)
            if seg.id in segments:
                raise ExpansionError(f"duplicate segment id {seg.id!r}")
            segments[seg.id] = seg
            if "corner_endpoints" in raw:
                declared_segments[seg.id] = {"corner_endpoints": raw["corner_endpoints"]}
        loops = {}
        for raw in d.get("loops", ()):
            loop = Loop.from_json(raw)
            loops[loop.id] = loop
        device = Device(
            nodes=nodes,
            segments=segments,
            loops=loops,
            generator=str(d.get("generator", "explicit")),
            params=dict(d.get("params", {})),
        )
        return device, {"nodes": declared_nodes, "segments": declared_segments}


@dataclass(frozen=True)
class Architecture:
    """A parsed, expanded `.arch.json` document."""

    name: str
    device: Device
    zone_types: Mapping[str, Mapping]
    primitives: PrimitiveSet
    control: Mapping = field(default_factory=dict)
    heating: Mapping = field(default_factory=dict)
    species: Mapping = field(default_factory=dict)
    budget: Mapping = field(default_factory=dict)
    description: str | None = None
    provenance: Mapping = field(default_factory=dict)

    # ---------------------------------------------------------------- zones

    def zone(self, zone_type: str) -> Mapping:
        try:
            return self.zone_types[zone_type]
        except KeyError:
            raise KeyError(f"architecture declares no zone type {zone_type!r}") from None

    def can(self, node_id: str, capability: str) -> bool:
        """R6: does the node's zone type carry `capability` ('gate'|'spam'|'cool')?"""
        node = self.device.nodes[node_id]
        if node.zone_type is None:
            return False
        return bool(self.zone(node.zone_type).get(capability, False))

    def anomalous_rate(self) -> float:
        """R17 background heating, quanta per millisecond of elapsed time."""
        return float(self.heating.get("anomalous_rate_quanta_per_ms", 0.0))

    # ---------------------------------------------------------------- control

    @cached_property
    def control_plane(self) -> ControlPlane:
        """Which electrodes share a channel, and therefore which zones move together.

        This is the level PLAN §2's scope boundary sits at: the wiring is modelled, the
        voltages on it are not. An architecture that declares no `control.channels` gets
        an undeclared plane -- counts still work, drivability is simply not judged.
        """
        return build_control_plane(self.device, self.control)

    @cached_property
    def simd_classes(self) -> Mapping[str, Mapping]:
        """Declared SIMD class id -> its definition.  R4's class list."""
        classes = self.control.get("classes", {}) or {}
        out: dict[str, Mapping] = {}
        for extra in classes.get("extra", ()) or ():
            out[str(extra["id"])] = dict(extra)
        return out

    def simd_class(self, cls_id: str) -> Mapping:
        try:
            return self.simd_classes[cls_id]
        except KeyError:
            known = ", ".join(sorted(self.simd_classes)) or "(none declared)"
            raise KeyError(
                f"architecture {self.name!r} declares no SIMD class {cls_id!r} "
                f"(declared: {known})"
            ) from None

    def entails(self, cls_id: str) -> tuple[str, ...]:
        """The extra primitives a movement class implies.

        A rigid rotation is a conveyor-belt transport of a whole chain along a moving
        potential -- nothing splits (PLAN §0.8; H2 drives 20 wells per side from three
        broadcast signals).  A dock lifts one ion out of the rail's potential and inserts
        it into a separate trap, so it costs a split at the source and a merge at the
        destination, each way.  That difference is a property of the movement class, not
        of occupancy, so the architecture declares it.
        """
        try:
            return tuple(self.simd_class(cls_id).get("entails", ()) or ())
        except KeyError:
            return ()

    def max_simd_classes(self) -> int:
        return int(self.control.get("max_simd_classes_per_cycle", 1))

    # ---------------------------------------------------------------- json

    def to_json(self, *, expanded: bool = True) -> dict:
        geometry: dict
        if expanded:
            geometry = self.device.to_json()
        else:
            # the compact form is only legitimate if re-expanding it reproduces this
            # exact graph.  Emitting generator+params for a device that has since been
            # edited would silently throw the real graph away and hand back a file that
            # loads as something else.
            if not self.device.reproducible_from_generator():
                geometry = self.device.to_json()
            else:
                geometry = {
                    "generator": self.device.generator,
                    "params": dict(self.device.params),
                }
        doc: dict = {
            "name": self.name,
            "schema_version": SCHEMA_VERSION,
        }
        if self.description:
            doc["description"] = self.description
        if self.provenance:
            doc["provenance"] = dict(self.provenance)
        doc["geometry"] = geometry
        doc["zone_types"] = {k: dict(v) for k, v in self.zone_types.items()}
        if self.control:
            doc["control"] = dict(self.control)
        doc["primitives"] = self.primitives.to_json()
        if self.heating:
            doc["heating"] = dict(self.heating)
        if self.species:
            doc["species"] = dict(self.species)
        if self.budget:
            doc["budget"] = dict(self.budget)
        return doc

    @staticmethod
    def from_json(doc: Mapping, *, validate: bool = True) -> "Architecture":
        from .generators import expand_generator  # local import: generators need Device

        if validate:
            check(doc)
        geometry = doc["geometry"]
        zone_types = {k: dict(v) for k, v in doc["zone_types"].items()}
        declared: dict = {"nodes": {}, "segments": {}}
        # PRESENCE, not truthiness.  `DeviceBuilder("explicit").build().to_json()` carries
        # `"nodes": []`, which is falsy -- so an EMPTY explicit device fell through to the
        # generator branch and died with "geometry.generator is 'explicit' but the document
        # carries no `nodes`".  An empty device is the from-scratch starting point of the
        # design tool and has to be expressible; `expand_generator("explicit", ...)` keeps
        # raising, because it is now only reached by a document that genuinely omits the
        # key, which is the error it was written for.
        explicit = str(geometry.get("generator", "")) == "explicit"
        if "nodes" in geometry or "sites" in geometry or explicit:
            device, declared = Device.from_json(geometry)
        else:
            device = expand_generator(
                str(geometry["generator"]), dict(geometry.get("params", {})), zone_types
            )
        device = resolve_capacities(device, zone_types)
        errors = device.check_structure(declared)
        # A site whose zone type is not declared passed every structural check and then
        # died at RUN time with `KeyError: architecture declares no zone type 'X'` from
        # `Architecture.can`, which RAISES rather than returning False -- so R6 could not
        # run at all and the tool would have printed "0 violations" for a check that never
        # executed.  `generators.py` has this check for the generator path; this is the
        # explicit path, which is the one a design tool actually uses.
        errors += [
            f"node {nid!r}: no zone type {node.zone_type!r} is declared "
            f"(have: {', '.join(sorted(zone_types)) or 'none'})"
            for nid, node in device.nodes.items()
            if node.zone_type is not None and node.zone_type not in zone_types
        ]
        if errors:
            raise ExpansionError(
                f"{len(errors)} structural error(s) in {doc.get('name')!r}:\n  "
                + "\n  ".join(errors)
            )
        return Architecture(
            name=str(doc["name"]),
            device=device,
            zone_types=zone_types,
            primitives=PrimitiveSet.from_json(doc["primitives"]),
            control=dict(doc.get("control", {})),
            heating=dict(doc.get("heating", {})),
            species=dict(doc.get("species", {})),
            budget=dict(doc.get("budget", {})),
            description=doc.get("description"),
            provenance=dict(doc.get("provenance", {})),
        )


def resolve_capacities(device: Device, zone_types: Mapping[str, Mapping]) -> Device:
    """Give each site the capacity of its zone type.

    Called both when a document is first expanded and whenever a zone is retuned, so a
    site that inherited its capacity follows the zone rather than keeping a stale value
    that R1 would then check against.
    """
    changed = False
    nodes = dict(device.nodes)
    for nid, node in nodes.items():
        if node.kind != "site" or node.zone_type is None:
            continue
        if node.capacity_explicit:
            continue  # the site states its own capacity; the zone does not override it
        cap = int(zone_types.get(node.zone_type, {}).get("capacity", 0))
        if cap and cap != node.capacity:
            nodes[nid] = replace(node, capacity=cap)
            changed = True
    if not changed:
        return device
    return Device(
        nodes=nodes,
        segments=device.segments,
        loops=device.loops,
        generator=device.generator,
        params=device.params,
    )


def iter_nodes(device: Device, ids: Iterable[str]) -> Iterable[Node]:
    for i in ids:
        yield device.nodes[i]
