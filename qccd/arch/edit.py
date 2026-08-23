"""Incremental topology editing: add and remove nodes and segments, safely.

`DeviceBuilder` constructs a device from nothing and `Machine.move_site` drags one that
already exists.  Between them sits the operation a design tool actually spends its time
on -- *change the graph* -- and it is the one place where a naive edit silently produces
a device that still loads, still renders, and prices the wrong thing.

Three things make a topology edit different from a retune.

**`Device.loops` is an ORDERED node list, and `shift_map` walks it by index.**  A loop is
not "the set of nodes on the ring"; it is the cyclic order in which a rigid rotation
visits them.  Dropping a trap onto a rail without splicing it into that order leaves a
device that loads cleanly, draws correctly, and prices `rotate(+1)` as an orbit that skips
the new node -- a wrong number with no error attached.  So every operation here maintains
the invariant `loop_segments(L)` can walk: *consecutive nodes in a loop's order are joined
by an actual segment*, and it maintains it for EVERY loop the edit touches, not just the
one named in `Segment.loop` (that field is a colouring/pricing annotation, and a node pair
may be consecutive in more than one loop).

**Degree is derived, so topology edits change the price.**  R18 charges `junction_cross`
by degree; drawing one chord across a ring turns two degree-2 rail nodes into junctions
and the architecture may price no junction at that degree, at which point
`architecture_violations` raises R11.  Every operation therefore reports the derived
delta -- which nodes changed degree, which became or stopped being corners, which
segments changed `corner_endpoints` -- because those are the numbers that moved.

**Removal can disconnect.**  Nothing in `check_structure` forbids two islands (a real chip
may have them), so a disconnection is reported, not rejected -- but it is reported LOUDLY
and the `Machine` wrappers refuse it unless asked, because the overwhelmingly likely cause
is a misplaced click rather than a deliberate two-island design.

Everything here is pure: `(Device, ...) -> (Device, EditReport)`.  `Device` is frozen and
caches every derived quantity, so an edit always builds a new one -- the same discipline
`Machine._rebuild` already follows.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Sequence

from .device import Device, ExpansionError, Loop, Node, Segment
from .schema import MIN_LOOP_NODES

__all__ = [
    "TopologyError",
    "EditReport",
    "add_site",
    "add_junction",
    "add_segment",
    "remove_node",
    "remove_segment",
    "components",
    "derived",
    "derived_delta",
    "device_to_wire",
    "device_from_wire",
    "apply_edit",
    "trace",
    # the architecture-command whitelist (as opposed to the topology one above)
    "EditError",
    "SEED",
    "MUTATE",
    "BUILD",
    "apply_call",
    "apply_program",
    "statement_vocabulary",
    "canonical",
    "arch_fingerprint",
]

_ROUND = 9

#: The id grammar `qccd/arch/schema.py` enforces on every saved document, checked HERE
#: so an editor cannot mint an id that only fails hours later at `m.save()`.  The bridge
#: segment a splice creates used to be `E143+E0`, which loads, renders, prices and then
#: refuses to serialize -- exactly the class of failure a design tool must not have.
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*")


def _check_id(kind: str, value: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise TopologyError(
            f"{value!r} is not a usable {kind} id: it must start with a letter or "
            f"underscore and contain only letters, digits, and _ . : -")
    return value


class TopologyError(ValueError):
    """An edit that would produce a device the rest of the stack cannot price."""


# --------------------------------------------------------------------------- report


@dataclass(frozen=True)
class EditReport:
    """What one edit did, and what it means.

    `errors` is always empty on a returned report -- a failing edit raises
    `TopologyError` instead, so a caller cannot ignore one.  `warnings` are the things
    that are legal but load-bearing: a disconnection, a loop that lost its rigid shift,
    a site nothing drives.
    """

    op: str
    nodes_added: tuple[str, ...] = ()
    nodes_removed: tuple[str, ...] = ()
    segments_added: tuple[str, ...] = ()
    segments_removed: tuple[str, ...] = ()
    loops_changed: tuple[str, ...] = ()
    loops_opened: tuple[str, ...] = ()
    loops_removed: tuple[str, ...] = ()
    degree_changed: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    corner_changed: Mapping[str, tuple[bool, bool]] = field(default_factory=dict)
    corner_endpoints_changed: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    disconnects: bool = False
    components_after: tuple[tuple[str, ...], ...] = ()
    orphaned: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "op": self.op,
            "nodes_added": list(self.nodes_added),
            "nodes_removed": list(self.nodes_removed),
            "segments_added": list(self.segments_added),
            "segments_removed": list(self.segments_removed),
            "loops_changed": list(self.loops_changed),
            "loops_opened": list(self.loops_opened),
            "loops_removed": list(self.loops_removed),
            "degree_changed": {k: list(v) for k, v in sorted(self.degree_changed.items())},
            "corner_changed": {k: list(v) for k, v in sorted(self.corner_changed.items())},
            "corner_endpoints_changed": {
                k: list(v) for k, v in sorted(self.corner_endpoints_changed.items())},
            "disconnects": self.disconnects,
            "components_after": [list(c) for c in self.components_after],
            "orphaned": list(self.orphaned),
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------- derived


def derived(dev: Device) -> dict:
    """The three derived quantities a topology edit moves, as plain JSON.

    This is the exact set the emitted page ships precomputed (`node.deg`, `node.corner`,
    `segment.corner_endpoints`) and therefore the exact set a client-side mirror has to
    recompute for itself.  Having one function produce it on both sides is what the
    parity test diffs.
    """
    corners = dev.all_corners
    return {
        "degree": {nid: dev.degree(nid) for nid in sorted(dev.nodes)},
        "corner": {nid: (nid in corners) for nid in sorted(dev.nodes)},
        "corner_endpoints": {sid: dev.corner_endpoints[sid]
                             for sid in sorted(dev.segments)},
    }


def derived_delta(before: dict, after: dict) -> tuple[dict, dict, dict]:
    """`(degree, corner, corner_endpoints)` maps of id -> (was, now), changes only."""
    def diff(a: Mapping, b: Mapping) -> dict:
        out = {}
        for k in set(a) | set(b):
            x, y = a.get(k), b.get(k)
            if k in a and k in b and x != y:
                out[k] = (x, y)
        return out
    return (diff(before["degree"], after["degree"]),
            diff(before["corner"], after["corner"]),
            diff(before["corner_endpoints"], after["corner_endpoints"]))


def components(dev: Device) -> tuple[tuple[str, ...], ...]:
    """Connected components of the segment graph, each sorted, ordered by first id.

    Isolated nodes are components of size one -- they are exactly what a two-step
    "drop a trap, then wire it" gesture leaves behind mid-gesture, so they must be
    visible rather than filtered out.
    """
    adj: dict[str, list[str]] = {n: [] for n in dev.nodes}
    for seg in dev.segments.values():
        a, b = seg.ends
        if a in adj and b in adj:
            adj[a].append(b)
            adj[b].append(a)
    seen: set[str] = set()
    out: list[tuple[str, ...]] = []
    for start in sorted(dev.nodes):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out.append(tuple(sorted(comp)))
    return tuple(sorted(out, key=lambda c: c[0]))


# --------------------------------------------------------------------------- helpers


def _rebuilt(dev: Device, nodes, segments, loops) -> Device:
    return Device(nodes=nodes, segments=segments, loops=loops,
                  generator=dev.generator, params=dev.params)


def _pair_positions(loop: Loop, a: str, b: str) -> list[int]:
    """Indices `i` at which `{loop.nodes[i], loop.nodes[i+1]} == {a, b}`.

    Direction-agnostic on purpose: a segment's `ends` order is independent of the loop's
    traversal order, and `loop_segments` resolves by `segment_between` which is also
    direction-agnostic.  Matching on `ends` order instead would splice half the ring's
    segments at the wrong index and price the wrong orbit.
    """
    seq, k = loop.nodes, len(loop.nodes)
    rng = range(k) if loop.closed else range(k - 1)
    return [i for i in rng if {seq[i], seq[(i + 1) % k]} == {a, b}]


def _adjacent_on(dev: Device, a: str, b: str) -> tuple[str, ...]:
    """Loop ids in which `a` and `b` are consecutive."""
    return tuple(lid for lid, lp in dev.loops.items() if _pair_positions(lp, a, b))


def _existing_between(dev: Device, a: str, b: str) -> str | None:
    for seg in dev.segments.values():
        if set(seg.ends) == {a, b}:
            return seg.id
    return None


def _replace_key(d: dict, key: str, entries: Sequence[tuple[str, object]]) -> dict:
    """Rebuild `d` with `key` replaced, IN PLACE, by `entries`.

    `del d[k]` then `d[new] = v` appends, so subdividing E0 would move E0a and E0b to the
    bottom of a 168-segment dict -- and the explicit listing emits `d.segment(...)` in
    exactly that order, so one click would reshuffle the emitted geometry and every
    subsequent diff of the file.  Keeping the slot is not cosmetic: it is what makes the
    listing's diff after an edit the size of the edit.
    """
    out: dict = {}
    for k, v in d.items():
        if k == key:
            for nk, nv in entries:
                out[nk] = nv
        else:
            out[k] = v
    return out


def _fresh(prefix: str, taken: Iterable[str]) -> str:
    taken = set(taken)
    if prefix not in taken:
        return prefix
    i = 1
    while f"{prefix}{i}" in taken:
        i += 1
    return f"{prefix}{i}"


def _split_lengths(dev: Device, seg: Segment, pos: Sequence[float]) -> tuple[float, float]:
    """Split a segment's declared length at `pos`, by the projection of `pos` onto it.

    Length is an independently declared property (only `length_scaling` cost models read
    it), so it must be *conserved*: subdividing a length-2 segment must give two pieces
    summing to 2, or a cost model that reads lengths sees the device get longer every
    time a trap is dropped onto a rail.
    """
    pa = dev.nodes[seg.ends[0]].pos
    pb = dev.nodes[seg.ends[1]].pos
    dim = min(len(pa), len(pb), len(pos))
    vx = [float(pb[i]) - float(pa[i]) for i in range(dim)]
    wx = [float(pos[i]) - float(pa[i]) for i in range(dim)]
    denom = sum(c * c for c in vx)
    if denom <= 0.0:
        t = 0.5
    else:
        t = sum(v * w for v, w in zip(vx, wx)) / denom
        t = min(max(t, 0.0), 1.0)
    total = float(seg.length)
    first = round(total * t, _ROUND)
    return first, round(total - first, _ROUND)


def _prune_degenerate_loops(loops: dict, segments: dict,
                            warnings: list[str]) -> tuple[dict, dict, list[str]]:
    """Drop every loop left with fewer nodes than a transport loop can have.

    REPAIR, NOT REFUSAL -- and the choice is not a close call.

    Deleting the only segment of a two-node chain is the most ordinary edit a design tool
    has to support.  What it leaves behind is a one-node `Loop`, and a one-node loop is
    not a small loop: it has no consecutive pair, so `loop_segments` yields nothing,
    `shift_map` moves nothing, `corners` finds nothing and it prices nothing.  It is an
    empty annotation on a node id the geometry already carries.  Refusing the delete to
    protect it would mean telling the user "you may not remove this segment, because a
    piece of my own bookkeeping would stop meaning anything" -- the tool punishing the
    user for the tool's data structure.  So the loop goes, and the segment deletion
    succeeds.

    What is NOT acceptable is doing either silently.  Silence is how this defect existed:
    the edit succeeded, four correct warnings were printed, and the resulting document was
    one `Architecture.from_json` refuses (`$.geometry.loops[0].nodes: needs at least 2
    item(s), has 1`).  So every drop is a warning, lands in `EditReport.loops_removed`
    beside the ones `on_loop='delete'` puts there, and clears `Segment.loop` on anything
    still pointing at it -- nothing is left referring to a loop that is gone.

    Called by every removal op just before `_finish`.  ONE site rather than four: the four
    places a loop can shrink (an open loop cut in two, an endpoint spliced out, a node
    opened out of a two-node path, the trailing sweep) would each need the same three
    lines, and a fifth path added later would silently not have them.
    """
    dropped: list[str] = []
    for lid, lp in list(loops.items()):
        if len(lp.nodes) >= MIN_LOOP_NODES:
            continue
        del loops[lid]
        dropped.append(lid)
        warnings.append(
            f"loop {lid!r} is down to {len(lp.nodes)} node(s), fewer than the "
            f"{MIN_LOOP_NODES} a transport loop needs, so it is DROPPED: it has no "
            f"segment left to walk, prices no corner, and no .arch.json can hold it")
    if dropped:
        n = 0
        for sid, seg in list(segments.items()):
            if seg.loop in dropped:
                segments[sid] = replace(seg, loop=None)
                n += 1
        if n:
            warnings.append(
                f"{n} segment(s) pointed at {', '.join(sorted(dropped))} and are now off "
                f"every loop; they price no corner")
    return loops, segments, dropped


def _prune_structure(dev: Device) -> list[str]:
    errors = dev.check_structure()
    return errors


def _finish(dev: Device, report: EditReport, before: dict) -> tuple[Device, EditReport]:
    """Validate, then attach the derived delta and the connectivity verdict."""
    errors = _prune_structure(dev)
    if errors:
        raise TopologyError(
            f"{report.op} would leave {len(errors)} structural error(s):\n  "
            + "\n  ".join(errors[:8]))
    after = derived(dev)
    dd, dc, dce = derived_delta(before, after)
    comps = components(dev)
    warn = list(report.warnings)
    if len(comps) > 1:
        sizes = ", ".join(str(len(c)) for c in comps)
        warn.append(
            f"the device is in {len(comps)} disconnected pieces ({sizes} nodes); "
            f"no ion can move between them")
    return dev, replace(
        report,
        degree_changed=dd, corner_changed=dc, corner_endpoints_changed=dce,
        disconnects=len(comps) > 1, components_after=comps, warnings=tuple(warn))


# --------------------------------------------------------------------------- add node


def add_site(dev: Device, node_id: str, *pos: float, zone: str | None = None,
             capacity: int = 0, labels: Sequence[str] = (),
             on: str | None = None, to: Sequence[str] = (),
             segment_ids: Sequence[str] | None = None,
             zone_types: Mapping[str, Mapping] | None = None,
             kind: str = "site") -> tuple[Device, EditReport]:
    """Add one node.  Three gestures, one function.

    `on="E7"` **subdivides** segment E7: the new node lands between E7's endpoints, E7 is
    replaced by two segments inheriting its loop, labels and capacity, its declared
    length is split at the projection of `pos` onto it, and the new node is SPLICED into
    the order of every loop in which E7's endpoints were consecutive.  This is the "click
    a rail, drop a trap in" gesture and it is the only one that can safely put a node
    onto a transport loop, because it is the only one with an unambiguous splice index.

    `to=["S3","S9"]` wires the new node to existing nodes with fresh segments off every
    loop -- a spur, or a dock.  Each raises the far node's degree by one, which is what
    turns a rail slot into an R18 junction.

    Neither: an isolated node, which is legal and is what the two-step "place, then wire"
    gesture leaves behind between clicks.  It is reported as `orphaned`.

    `capacity=0` means "inherit from `zone_types[zone]["capacity"]`", resolved HERE
    rather than by a later `resolve_capacities` pass, so that the edit either produces a
    device that already passes `check_structure` or refuses -- there is no intermediate
    state in which a new site has capacity 0.  That matters for the client-side mirror:
    the page has `arch.zone_types` but has no `resolve_capacities`, and an edit that only
    becomes valid after a second pass is an edit whose validity the two implementations
    would disagree about.
    """
    if node_id in dev.nodes:
        raise TopologyError(f"node id {node_id!r} is already taken")
    if not node_id:
        raise TopologyError("a node needs an id")
    _check_id("node", node_id)
    if on and to:
        raise TopologyError(
            "give `on=` (subdivide a segment) or `to=` (wire to existing nodes), "
            "not both -- subdividing already wires the node to two neighbours")
    if kind not in ("site", "junction"):
        raise TopologyError(f"node kind must be 'site' or 'junction', not {kind!r}")
    if kind == "junction" and (capacity or zone):
        raise TopologyError("a bare junction holds no ions: it has no zone and no capacity")
    if kind == "site" and capacity < 0:
        raise TopologyError("capacity must be >= 0 (0 means inherit from the zone)")
    explicit = bool(capacity) and kind == "site"
    if kind == "site" and not capacity:
        zt = dict(zone_types or {})
        if zone is None:
            raise TopologyError(
                "a site needs capacity >= 1: pass capacity=, or zone= with the zone "
                "types so it can inherit one")
        if zt and zone not in zt:
            raise TopologyError(
                f"no zone type {zone!r} is declared (have: "
                f"{', '.join(sorted(zt)) or 'none'})")
        capacity = int(zt.get(zone, {}).get("capacity", 0))
        if capacity < 1:
            raise TopologyError(
                f"zone type {zone!r} declares capacity {capacity}; a site needs >= 1, "
                f"so pass capacity= explicitly")

    before = derived(dev)
    pos_t = tuple(float(c) for c in pos)
    if not pos_t:
        raise TopologyError("a node needs a position")

    node = Node(id=node_id, pos=pos_t, kind=kind,
                capacity=int(capacity) if kind == "site" else 0,
                zone_type=zone if kind == "site" else None,
                labels=tuple(labels), capacity_explicit=explicit)
    nodes = dict(dev.nodes)
    nodes[node_id] = node
    segments = dict(dev.segments)
    loops = dict(dev.loops)
    added_segs: list[str] = []
    removed_segs: list[str] = []
    touched_loops: list[str] = []
    warnings: list[str] = []

    if on is not None:
        if on not in dev.segments:
            raise TopologyError(f"no such segment {on!r} to subdivide")
        seg = dev.segments[on]
        a, b = seg.ends
        ids = list(segment_ids) if segment_ids else [f"{on}a", f"{on}b"]
        if len(ids) != 2:
            raise TopologyError("subdividing a segment needs exactly two new segment ids")
        for sid in ids:
            _check_id("segment", sid)
            if sid in dev.segments and sid != on:
                raise TopologyError(f"segment id {sid!r} is already taken")
        la, lb = _split_lengths(dev, seg, pos_t)
        segments = _replace_key(segments, on, [
            (ids[0], replace(seg, id=ids[0], ends=(a, node_id), length=la)),
            (ids[1], replace(seg, id=ids[1], ends=(node_id, b), length=lb)),
        ])
        removed_segs.append(on)
        added_segs += ids
        # splice into EVERY loop that walked this pair, not just `seg.loop`
        for lid, lp in dev.loops.items():
            hits = _pair_positions(lp, a, b)
            if not hits:
                continue
            seq = list(lp.nodes)
            for i in sorted(hits, reverse=True):
                # after index i the loop is at seq[i]; the new node goes immediately
                # after it, whichever way round `seg.ends` happens to be written
                seq.insert(i + 1, node_id)
            loops[lid] = replace(lp, nodes=tuple(seq))
            touched_loops.append(lid)
    elif to:
        seen: set[str] = set()
        for j, other in enumerate(to):
            if other == node_id:
                raise TopologyError("a segment from a node to itself is not transport")
            if other not in dev.nodes:
                raise TopologyError(f"no such node {other!r} to wire {node_id!r} to")
            if other in seen:
                raise TopologyError(f"{other!r} given twice in `to=`")
            seen.add(other)
            sid = (list(segment_ids)[j] if segment_ids and j < len(segment_ids)
                   else _fresh(f"{node_id}-{other}", segments))
            _check_id("segment", sid)
            if sid in segments:
                raise TopologyError(f"segment id {sid!r} is already taken")
            segments[sid] = Segment(id=sid, ends=(other, node_id), length=1.0,
                                    capacity=1, loop=None, labels=("spur",))
            added_segs.append(sid)
    else:
        warnings.append(
            f"{node_id} is wired to nothing; no ion can reach it until it gets a segment")

    new = _rebuilt(dev, nodes, segments, loops)
    report = EditReport(
        op=f"add_{kind}", nodes_added=(node_id,),
        segments_added=tuple(added_segs), segments_removed=tuple(removed_segs),
        loops_changed=tuple(sorted(set(touched_loops))),
        orphaned=() if (on or to) else (node_id,),
        warnings=tuple(warnings))
    return _finish(new, report, before)


def add_junction(dev: Device, node_id: str, *pos: float,
                 labels: Sequence[str] = (), on: str | None = None,
                 to: Sequence[str] = (),
                 segment_ids: Sequence[str] | None = None) -> tuple[Device, EditReport]:
    """`add_site` for a bare junction: no zone, no capacity, holds no ions.

    Being *called* a junction is not what makes R18 charge one -- degree is.  A junction
    added with `to=` naming fewer than three neighbours is a legal node that R18 prices
    as nothing, and the report says so.
    """
    dev2, rep = add_site(dev, node_id, *pos, labels=labels, on=on, to=to,
                         segment_ids=segment_ids, kind="junction")
    deg = dev2.degree(node_id)
    if deg < 3:
        rep = replace(rep, warnings=rep.warnings + (
            f"{node_id} is declared a junction but has degree {deg}; R18 prices a "
            f"junction only at degree >= 3, so this node is charged as plain transport",))
    return dev2, rep


# ------------------------------------------------------------------------ add segment


def add_segment(dev: Device, seg_id: str, a: str, b: str, *, loop: str | None = None,
                labels: Sequence[str] = (), length: float = 1.0,
                capacity: int = 1) -> tuple[Device, EditReport]:
    """Join two existing nodes.

    `loop=L` is accepted **only** when `a` and `b` are already consecutive in `L`'s node
    order -- that is, only when the new segment is the missing edge of a walk that L
    already declares.  A *chord* (both ends on L but not adjacent) put on a loop would be
    scored by `corner_endpoints`, which counts corners of the loop the segment lies on;
    a chord joining two corners would be charged `corner_hops` as though it contained a
    whole turn, which it does not.  So a chord is legal, and it stays `loop=None`.

    A chord's real effect is on degree: both endpoints gain one, and a degree-2 rail node
    becoming degree 3 becomes an R18 junction.  If the architecture prices no
    `junction_cross` at that degree, `architecture_violations` reports R11 -- which is
    where the user is told, so the report here carries the degree delta and lets the
    rule speak.
    """
    if seg_id in dev.segments:
        raise TopologyError(f"segment id {seg_id!r} is already taken")
    _check_id("segment", seg_id)
    if a == b:
        raise TopologyError("a self-loop is not a transport segment")
    for end in (a, b):
        if end not in dev.nodes:
            raise TopologyError(f"no such node {end!r}")
    dup = _existing_between(dev, a, b)
    if dup is not None:
        raise TopologyError(
            f"{a} and {b} are already joined by segment {dup!r}; parallel segments are "
            f"not modelled (set_segment_length or set capacity on {dup!r} instead)")
    if length <= 0:
        raise TopologyError("a segment must have positive length")
    if capacity < 1:
        raise TopologyError("a segment must be able to carry at least one ion")

    before = derived(dev)
    warnings: list[str] = []
    on_loops = _adjacent_on(dev, a, b)
    if loop is not None:
        if loop not in dev.loops:
            raise TopologyError(
                f"no such loop {loop!r} (have: {', '.join(sorted(dev.loops)) or 'none'})")
        if loop not in on_loops:
            raise TopologyError(
                f"{a} and {b} are not consecutive in loop {loop!r}, so a segment between "
                f"them is a CHORD, not a loop edge.  A chord on a loop would be priced "
                f"by corner_endpoints as though it contained a turn.  Add it with "
                f"loop=None, or reorder the loop first")
    else:
        shared = [lid for lid in dev.loops
                  if a in dev.loops[lid].nodes and b in dev.loops[lid].nodes]
        if shared and not on_loops:
            warnings.append(
                f"{seg_id} is a chord across loop(s) {', '.join(sorted(shared))}: it "
                f"shortcuts the declared orbit, so a rigid shift still walks the long "
                f"way round while a routed move may not")
        if on_loops:
            warnings.append(
                f"{a} and {b} are consecutive in loop(s) {', '.join(sorted(on_loops))} "
                f"but {seg_id} is off every loop; pass loop=... to price it as a loop edge")

    segments = dict(dev.segments)
    segments[seg_id] = Segment(id=seg_id, ends=(a, b), length=float(length),
                               capacity=int(capacity), loop=loop, labels=tuple(labels))
    new = _rebuilt(dev, dict(dev.nodes), segments, dict(dev.loops))
    for end in (a, b):
        if dev.degree(end) == 2 and new.degree(end) == 3:
            warnings.append(
                f"{end} is now degree 3, so R18 makes it a junction; the architecture "
                f"must price junction_cross at degree 3 or R11 will refuse it")
    report = EditReport(op="add_segment", segments_added=(seg_id,),
                        warnings=tuple(warnings))
    return _finish(new, report, before)


# --------------------------------------------------------------------------- removal


def remove_segment(dev: Device, seg_id: str, *,
                   on_loop: str = "refuse") -> tuple[Device, EditReport]:
    """Delete one segment.

    If the segment is an edge of a transport loop -- its endpoints are consecutive in
    some loop's node order -- deleting it breaks that loop's walk, and `loop_segments`
    would raise on the next rotation.  `on_loop` says what to do instead:

    * `"refuse"` (default) -- raise, naming the loops.
    * `"open"` -- keep the loop's nodes but mark it open, ROTATING the node list so the
      deleted edge becomes the seam.  An open loop's walk is `(0,1) .. (k-2,k-1)` with no
      wraparound, so the rotation is what keeps every remaining consecutive pair a real
      segment.  Rigid rotation on an open loop is undefined (`shift_map` raises), and the
      report says so.
    * `"delete"` -- drop the loop entirely and clear `Segment.loop` on every segment that
      pointed at it, so nothing is left referring to a loop that is gone.

    Removal can disconnect the device.  That is not a structural error -- two islands are
    a legal, if unusual, chip -- so it is reported rather than refused here; the `Machine`
    wrapper refuses it unless asked.
    """
    if seg_id not in dev.segments:
        raise TopologyError(f"no such segment {seg_id!r}")
    if on_loop not in ("refuse", "open", "delete"):
        raise TopologyError("on_loop must be 'refuse', 'open' or 'delete'")

    before = derived(dev)
    seg = dev.segments[seg_id]
    a, b = seg.ends
    affected = _adjacent_on(dev, a, b)

    segments = dict(dev.segments)
    del segments[seg_id]
    loops = dict(dev.loops)
    opened: list[str] = []
    dropped: list[str] = []
    warnings: list[str] = []

    if affected:
        if on_loop == "refuse":
            raise TopologyError(
                f"{seg_id} is an edge of loop(s) {', '.join(sorted(affected))}; deleting "
                f"it breaks the walk that prices a rigid rotation.  Pass "
                f"on_loop='open' to cut the loop open there, or on_loop='delete' to drop "
                f"the loop")
        for lid in affected:
            lp = dev.loops[lid]
            hits = _pair_positions(lp, a, b)
            if on_loop == "delete":
                del loops[lid]
                dropped.append(lid)
                continue
            if len(hits) > 1:
                raise TopologyError(
                    f"loop {lid!r} walks {a}-{b} {len(hits)} times; cutting it open "
                    f"there is ambiguous")
            i = hits[0]
            seq = list(lp.nodes)
            k = len(seq)
            if lp.closed:
                # the cut edge joins seq[i] and seq[i+1]; start the open walk at i+1 so
                # the seam falls exactly where the segment was
                seq = seq[(i + 1) % k:] + seq[: (i + 1) % k]
                loops[lid] = replace(lp, nodes=tuple(seq), closed=False)
            else:
                # an open loop cut in the middle becomes two paths; keep the longer one
                head, tail = seq[: i + 1], seq[i + 1:]
                keep = head if len(head) >= len(tail) else tail
                loops[lid] = replace(lp, nodes=tuple(keep))
                if len(keep) < MIN_LOOP_NODES:
                    # Neither piece is long enough to be a transport loop.
                    # `_prune_degenerate_loops` drops it below and says so; saying
                    # "kept the 1-node piece" and "is now OPEN" here as well would be
                    # two warnings about a loop that no longer exists -- which is what
                    # the page printed while the export was quietly unloadable.
                    continue
                warnings.append(
                    f"loop {lid!r} was already open and is cut in two; keeping the "
                    f"{len(keep)}-node piece and dropping {len(seq) - len(keep)} node(s) "
                    f"from the path")
            opened.append(lid)
            warnings.append(
                f"loop {lid!r} is now OPEN: shift_map raises on it, so every declared "
                f"movement class with orbit {lid!r} can no longer run a rigid rotation")
    if dropped:
        for sid, s in list(segments.items()):
            if s.loop in dropped:
                segments[sid] = replace(s, loop=None)
        warnings.append(
            f"loop(s) {', '.join(sorted(dropped))} deleted; "
            f"{sum(1 for s in dev.segments.values() if s.loop in dropped)} segment(s) "
            f"are now off every loop and price no corner")

    loops, segments, degenerate = _prune_degenerate_loops(loops, segments, warnings)
    dropped.extend(degenerate)
    opened = [lid for lid in opened if lid not in degenerate]

    new = _rebuilt(dev, dict(dev.nodes), segments, loops)
    orphans = tuple(sorted(n for n in (a, b) if new.degree(n) == 0))
    if orphans:
        warnings.append(
            f"{', '.join(orphans)} now has no segment at all; nothing can reach it")
    report = EditReport(
        op="remove_segment", segments_removed=(seg_id,),
        loops_changed=tuple(sorted(set(opened) | set(dropped))),
        loops_opened=tuple(sorted(set(opened))), loops_removed=tuple(sorted(set(dropped))),
        orphaned=orphans, warnings=tuple(warnings))
    return _finish(new, report, before)


def remove_node(dev: Device, node_id: str, *, mend: str = "splice",
                cascade: bool = False) -> tuple[Device, EditReport]:
    """Delete one node and every segment on it.

    A node on a transport loop leaves two dangling loop edges and a hole in the ordered
    node list.  `mend` says how the loop closes:

    * `"splice"` (default) -- the inverse of `add_site(on=...)`.  The node's two loop
      edges are replaced by ONE segment joining its two loop neighbours, inheriting the
      loop, labels and capacity of the first and the SUM of both lengths, and the node is
      dropped from the ordered list.  The orbit shrinks by one and every remaining
      consecutive pair is still a real segment.  Refused if a segment between those two
      neighbours already exists (that would be a parallel edge) or if the loop would drop
      below three nodes.
    * `"open"` -- cut the loop open at the node instead of closing over it.
    * `"delete"` -- drop every loop the node was on.

    Segments not on any loop (spurs, couplings) are simply deleted; their far endpoints
    may fall to degree 0, which is reported as `orphaned` and, with `cascade=True`,
    deletes them too.
    """
    if node_id not in dev.nodes:
        raise TopologyError(f"no such node {node_id!r}")
    if mend not in ("splice", "open", "delete"):
        raise TopologyError("mend must be 'splice', 'open' or 'delete'")

    before = derived(dev)
    nodes = dict(dev.nodes)
    segments = dict(dev.segments)
    loops = dict(dev.loops)
    removed_segs: list[str] = []
    added_segs: list[str] = []
    touched: list[str] = []
    opened: list[str] = []
    dropped: list[str] = []
    warnings: list[str] = []

    on_loops = [lid for lid, lp in dev.loops.items() if node_id in lp.nodes]

    for lid in on_loops:
        lp = dev.loops[lid]
        seq = list(lp.nodes)
        i = seq.index(node_id)
        k = len(seq)
        if mend == "delete":
            del loops[lid]
            dropped.append(lid)
            continue
        if mend == "open":
            if lp.closed:
                seq = seq[i + 1:] + seq[:i]
                loops[lid] = replace(lp, nodes=tuple(seq), closed=False)
            else:
                head, tail = seq[:i], seq[i + 1:]
                keep = head if len(head) >= len(tail) else tail
                loops[lid] = replace(lp, nodes=tuple(keep))
                if len(keep) < MIN_LOOP_NODES:
                    # too short to be a transport loop at all; `_prune_degenerate_loops`
                    # drops it below.  Do not also claim it is "now open".
                    continue
            opened.append(lid)
            touched.append(lid)
            warnings.append(
                f"loop {lid!r} is now OPEN: rigid rotation on it is undefined")
            continue
        # splice
        if lp.closed and k <= 3:
            raise TopologyError(
                f"loop {lid!r} has {k} nodes; splicing {node_id} out would leave a "
                f"closed loop of {k - 1}, which cannot be walked without a parallel edge")
        prev_id = seq[(i - 1) % k]
        next_id = seq[(i + 1) % k]
        if not lp.closed and (i == 0 or i == k - 1):
            # an endpoint of an open path: nothing to bridge, just shorten it
            loops[lid] = replace(lp, nodes=tuple(n for n in seq if n != node_id))
            touched.append(lid)
            continue
        clash = _existing_between(dev, prev_id, next_id)
        if clash is not None:
            raise TopologyError(
                f"splicing {node_id} out of loop {lid!r} needs a segment between "
                f"{prev_id} and {next_id}, but {clash!r} already joins them; that would "
                f"be a parallel edge.  Use mend='open' or delete {clash!r} first")
        s_prev = dev.segment_between(prev_id, node_id)
        s_next = dev.segment_between(node_id, next_id)
        bridge = _fresh(f"{s_prev.id}.{s_next.id}", segments)
        segments = _replace_key(segments, s_prev.id, [(bridge, replace(
            s_prev, id=bridge, ends=(prev_id, next_id),
            length=round(float(s_prev.length) + float(s_next.length), _ROUND)))])
        added_segs.append(bridge)
        for sid in (s_prev.id, s_next.id):
            if sid in segments:
                del segments[sid]
            removed_segs.append(sid)
        loops[lid] = replace(lp, nodes=tuple(n for n in seq if n != node_id))
        touched.append(lid)

    # every remaining segment on the node goes
    for sid in list(segments):
        if node_id in segments[sid].ends:
            del segments[sid]
            removed_segs.append(sid)
    if dropped:
        for sid, s in list(segments.items()):
            if s.loop in dropped:
                segments[sid] = replace(s, loop=None)
    del nodes[node_id]
    # a loop may still name the node if mend='delete' left another loop untouched
    for lid, lp in list(loops.items()):
        if node_id in lp.nodes:
            loops[lid] = replace(lp, nodes=tuple(n for n in lp.nodes if n != node_id))
            touched.append(lid)

    loops, segments, degenerate = _prune_degenerate_loops(loops, segments, warnings)
    dropped.extend(degenerate)
    opened = [lid for lid in opened if lid not in degenerate]
    touched = [lid for lid in touched if lid not in degenerate]

    new = _rebuilt(dev, nodes, segments, loops)
    orphans = tuple(sorted(n for n in new.nodes if new.degree(n) == 0
                           and dev.degree(n) > 0))
    nodes_removed: tuple[str, ...] = (node_id,)
    if orphans and cascade:
        for nid in orphans:
            del nodes[nid]
        new = _rebuilt(dev, nodes, segments, loops)
        warnings.append(f"cascade removed {len(orphans)} node(s) left with no segment: "
                        f"{', '.join(orphans)}")
        # capture BEFORE clearing: `nodes_removed` is what the editor undoes and what the
        # page removes from the stage, so losing the cascaded ids here would leave marks
        # on screen for nodes the device no longer has
        nodes_removed = nodes_removed + orphans
        orphans = ()
    elif orphans:
        warnings.append(
            f"{len(orphans)} node(s) are now wired to nothing: {', '.join(orphans[:6])}")

    zones_emptied = sorted({dev.nodes[node_id].zone_type} - {
        n.zone_type for n in new.nodes.values()} - {None})
    if zones_emptied:
        warnings.append(
            f"zone type(s) {', '.join(zones_emptied)} now have no sites; they stay "
            f"declared but unused")

    report = EditReport(
        op="remove_node",
        nodes_removed=nodes_removed,
        segments_added=tuple(added_segs), segments_removed=tuple(sorted(set(removed_segs))),
        loops_changed=tuple(sorted(set(touched) | set(dropped))),
        loops_opened=tuple(sorted(set(opened))), loops_removed=tuple(sorted(set(dropped))),
        orphaned=orphans, warnings=tuple(warnings))
    return _finish(new, report, before)


# ------------------------------------------------------------------------ the wire

# The ONE definition of how a `Device` crosses into JS.  The emitted page already ships
# `arch.nodes` / `arch.segments` / `arch.loops` in a flatter, lossy shape tuned for
# drawing (no `capacity_explicit`, no per-segment `length`, loops as bare node lists); an
# editor needs the lossless form, and the parity test needs both sides to agree on what
# "the device" is before it can diff anything.  Field names match `qccd/viz/js/edit.js`
# exactly, and dict insertion order is significant on both sides.


def device_to_wire(dev: Device) -> dict:
    return {
        "nodes": {nid: {"id": nid, "pos": [float(c) for c in n.pos], "kind": n.kind,
                        "cap": int(n.capacity), "zone": n.zone_type,
                        "labels": list(n.labels),
                        "capacity_explicit": bool(n.capacity_explicit)}
                  for nid, n in dev.nodes.items()},
        "segments": {sid: {"id": sid, "a": s.ends[0], "b": s.ends[1],
                           "length": float(s.length), "cap": int(s.capacity),
                           "loop": s.loop, "labels": list(s.labels)}
                     for sid, s in dev.segments.items()},
        "loops": {lid: {"id": lid, "nodes": list(l.nodes), "closed": bool(l.closed),
                        "kind": l.kind, "note": l.note}
                  for lid, l in dev.loops.items()},
        "generator": dev.generator,
        "params": dict(dev.params),
    }


def device_from_wire(w: Mapping) -> Device:
    nodes = {nid: Node(id=nid, pos=tuple(float(c) for c in n["pos"]),
                       kind=n["kind"], capacity=int(n.get("cap", 0)),
                       zone_type=n.get("zone"), labels=tuple(n.get("labels", ())),
                       capacity_explicit=bool(n.get("capacity_explicit", False)))
             for nid, n in w["nodes"].items()}
    segments = {sid: Segment(id=sid, ends=(s["a"], s["b"]),
                             length=float(s.get("length", 1.0)),
                             capacity=int(s.get("cap", 1)), loop=s.get("loop"),
                             labels=tuple(s.get("labels", ())))
                for sid, s in w["segments"].items()}
    loops = {lid: Loop(id=lid, nodes=tuple(l["nodes"]), closed=bool(l["closed"]),
                       kind=l.get("kind", "ring"), note=l.get("note"))
             for lid, l in w["loops"].items()}
    return Device(nodes=nodes, segments=segments, loops=loops,
                  generator=w.get("generator", "explicit"),
                  params=dict(w.get("params", {})))


#: op name -> the pure function, so an edit script is data on both sides.
OPS = {
    "add_site": lambda dev, a: add_site(
        dev, a["id"], *a["pos"], zone=a.get("zone"), capacity=a.get("capacity", 0),
        labels=a.get("labels", ()), on=a.get("on"), to=a.get("to", ()),
        segment_ids=a.get("segment_ids"), zone_types=a.get("zone_types")),
    "add_junction": lambda dev, a: add_junction(
        dev, a["id"], *a["pos"], labels=a.get("labels", ()), on=a.get("on"),
        to=a.get("to", ()), segment_ids=a.get("segment_ids")),
    "add_segment": lambda dev, a: add_segment(
        dev, a["id"], a["a"], a["b"], loop=a.get("loop"), labels=a.get("labels", ()),
        length=a.get("length", 1.0), capacity=a.get("capacity", 1)),
    "remove_node": lambda dev, a: remove_node(
        dev, a["id"], mend=a.get("mend", "splice"), cascade=a.get("cascade", False)),
    "remove_segment": lambda dev, a: remove_segment(
        dev, a["id"], on_loop=a.get("on_loop", "refuse")),
}


def apply_edit(dev: Device, edit: Mapping) -> tuple[Device, EditReport]:
    """Run one `{"op": ..., "args": {...}}` record.  The editor's whitelist entry point.

    `qccd.arch.listing.rebuild` warns that `exec` on user-edited text is arbitrary code
    execution and that a design tool must route edits through a method whitelist instead.
    This is that whitelist for topology: nothing here can name a Python attribute.
    """
    op = str(edit.get("op", ""))
    if op not in OPS:
        raise TopologyError(
            f"unknown edit op {op!r} (have: {', '.join(sorted(OPS))})")
    return OPS[op](dev, dict(edit.get("args", {})))


def trace(dev: Device, script: Sequence[Mapping]) -> list[dict]:
    """Run an edit script, recording the full state after every step.

    This is the object the parity test diffs: one record per edit, carrying the device,
    the derived quantities, the report and the connectivity verdict -- or, when the edit
    was refused, the exact error message.  Diffing the *trace* rather than the final
    device is what localises a drift to the edit that caused it.
    """
    out: list[dict] = []
    cur = dev
    for i, edit in enumerate(script):
        rec: dict = {"i": i, "op": edit.get("op"), "args": dict(edit.get("args", {}))}
        try:
            cur, rep = apply_edit(cur, edit)
            rec["ok"] = True
            rec["report"] = rep.to_json()
            rec["device"] = device_to_wire(cur)
            rec["derived"] = derived(cur)
        except (TopologyError, ExpansionError, KeyError, ValueError) as exc:
            rec["ok"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"
        out.append(rec)
    return out


# ---------------------------------------------------------------- the command whitelist
#
# `architecture_listing` already emits the architecture as a STRUCTURED COMMAND LIST:
# every editable record carries `call = {"method", "args", "kwargs"}` and a stable
# namespaced `target`.  The browser editor applies those records, and so must the parity
# test -- so there has to be one Python entry point that runs them.
#
# THIS IS NOT A RE-IMPLEMENTATION OF `Machine`.  It is a dispatcher INTO `Machine`:
# `getattr(Machine, method)(...)` behind a name whitelist.  It therefore cannot drift from
# `Machine`, because it *is* `Machine`, and only the JS half can be wrong -- which is
# exactly the property the parity test needs to be able to localise a failure.
#
# `qccd.arch.listing.rebuild`'s own docstring demands this ("A design tool must apply
# `ArchLine.call` records through a method whitelist instead of routing edits back through
# here", because `exec` on user-edited text is arbitrary code execution).  This is that
# whitelist for architecture commands; `apply_edit` above is its twin for topology.

#: Constructors.  These START a machine rather than mutating one.
SEED = frozenset({"blank", "blank_device", "from_template", "from_device"})

#: Retuning methods.  Chainable, and each takes the machine as `self`.
MUTATE = frozenset({
    "describe", "set_zone", "set_site_capacity", "set_segment_length", "set_curve",
    "set_degree_curve", "set_primitive", "declare_class", "set_control", "set_wiring",
    "set_heating", "set_species", "set_budget", "move_site",
})

#: Explicit-geometry builder verbs, accumulated into a `DeviceBuilder` and then sealed by
#: a `blank_device` / `from_device` seed.
BUILD = frozenset({"DeviceBuilder", "d.site", "d.junction", "d.segment", "d.loop"})


class EditError(ValueError):
    """One refused command, carrying enough to point at the statement that failed."""

    def __init__(self, code: str, method: str, message: str, index: int | None = None):
        super().__init__(message)
        self.code = code
        self.method = method
        self.index = index

    def to_json(self) -> dict:
        return {"code": self.code, "method": self.method, "index": self.index,
                "message": str(self)}


def statement_vocabulary(archs) -> set[str]:
    """Every `call.method` the shipped listings actually emit.

    COMPUTED, never hand-listed.  Add a thirteenth method to `listing.py` and the shipped
    architectures start emitting it, and `test_the_js_covers_every_emitted_statement` goes
    red NAMING the method before any numeric diff runs.  That costs zero maintenance and
    is the difference between a corpus and a fixture.
    """
    from .listing import architecture_listing
    out: set[str] = set()
    for arch in archs:
        for line in architecture_listing(arch, verify=False).lines:
            if line.kind == "call" and line.call:
                out.add(str(line.call["method"]))
    return out


def apply_call(state, call: Mapping):
    """Run one `{"method", "args", "kwargs"}` record.  Returns `(machine, builder)`.

    `state` is the `(machine, builder)` pair the previous call returned, or `None`.
    Never `exec`; nothing here can name a Python attribute the whitelist does not list.
    """
    from ..api import Machine
    from .builder import DeviceBuilder

    machine, builder = state if state else (None, None)
    method = str(call.get("method", ""))
    args = list(call.get("args", ()))
    kwargs = dict(call.get("kwargs", {}))

    if method in SEED:
        if method == "blank":
            if not args:
                raise EditError(
                    "missing_generator", method,
                    "Machine.blank() needs the generator name as its first positional "
                    "argument; the listing record must carry it in `args`, not only in "
                    "`target`")
            return Machine.blank(args[0], **kwargs), builder
        if method == "from_template":
            gen = args[0] if args else kwargs.pop("generator", None)
            if gen is None:
                raise EditError("missing_generator", method,
                                "a template seed needs its generator name in `args`")
            return getattr(Machine, str(gen))(**kwargs), builder
        if builder is None:
            raise EditError("no_builder", method,
                            f"{method} needs a DeviceBuilder to have been built first")
        device = builder.build()
        if method == "blank_device":
            zones = kwargs.pop("zones", None)
            if zones is None:
                zones = sorted({n.zone_type for n in device.nodes.values() if n.zone_type})
            kwargs.pop("template", None)
            return Machine.blank_device(device, zones=zones, **kwargs), builder
        return Machine.from_device(device, **kwargs), builder

    if method in BUILD:
        if method == "DeviceBuilder":
            gen = args[0] if args else "explicit"
            return machine, DeviceBuilder(gen, **kwargs)
        if builder is None:
            raise EditError("no_builder", method, f"{method} needs a DeviceBuilder first")
        getattr(builder, method.split(".", 1)[1])(*args, **kwargs)
        return machine, builder

    if method in MUTATE:
        if machine is None:
            raise EditError("no_machine", method,
                            f"no geometry yet: {method} needs a Machine.blank(...) first")
        try:
            getattr(machine, method)(*args, **kwargs)
        except (ValueError, KeyError, TypeError) as exc:
            raise EditError(type(exc).__name__, method, str(exc)) from None
        return machine, builder

    raise EditError("unknown_method", method,
                    f"{method!r} is not an editable method "
                    f"(have: {', '.join(sorted(SEED | MUTATE | BUILD))})")


def apply_program(calls: Sequence[Mapping]):
    """Replay a whole command list from scratch.  Returns the `Machine`.

    Raises `EditError` with `index` set to the statement that failed, so the browser and
    the test agree on WHICH statement was refused and not merely that one was.
    """
    state = None
    for i, call in enumerate(calls):
        try:
            state = apply_call(state, call)
        except EditError as exc:
            exc.index = i
            raise
        except (ExpansionError, ValueError, KeyError, TypeError) as exc:
            raise EditError(type(exc).__name__, str(call.get("method", "")), str(exc), i) from None
    if state is None or state[0] is None:
        raise EditError("empty", "", "the command list declared no geometry", None)
    return state[0]


def canonical(doc: Mapping) -> str:
    """A canonical text form for diffing two architecture documents.

    Object KEY order is not semantic, so it is sorted; LIST order is (the exported
    `.arch.json` is byte-compared and `Device.to_json` emits dict-insertion order), so it
    is preserved exactly.  Numbers compare at ZERO tolerance: every operation on this path
    is a copy or a lookup rather than arithmetic, and a tolerance would only hide drift.
    """
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def arch_fingerprint(arch) -> str:
    """`sha256` of the canonical expanded document.

    Shipped in the page so it can re-run its own interpreter over `A.listing.lines` at
    load, before the user touches anything, and refuse to enable editing if the two
    disagree.  That extends the existing `checksum` self-check to the architecture rather
    than adding a second status mechanism -- a drift that escapes CI is then visible at
    the user's desk instead of producing a confidently wrong price.
    """
    return hashlib.sha256(canonical(arch.to_json(expanded=True)).encode("utf-8")).hexdigest()
