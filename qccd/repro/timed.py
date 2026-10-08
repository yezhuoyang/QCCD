"""Timed schedules and the checker that replays them.

A *timed schedule* is what most QCCD papers actually compute: a list of primitive events,
each with a start and an end in microseconds, over a machine whose resources -- a trap, a
segment, a junction -- are held while an event uses them.  Nothing about it is lockstep: a
gate in one trap overlaps a split in another, and the makespan is the last event's end.

The checker replays such a schedule in start-time order and judges it against a
`Profile`, the rules the paper says it assumes:

    positions    every event finds its ions where it says they are, and leaves them where
                 the next event expects them (a split takes the ion out of its trap onto a
                 segment, a move carries it across a junction, a merge puts it back)
    capacity     no trap ever holds more ions than its capacity, at any instant
    exclusivity  one ion per segment and one per junction at a time, when the profile says so
    seriality    the operations of one trap never overlap, when the profile says so; the
                 operations of one ion never overlap, always
    chain order  a split takes an ion from the end of the chain that faces its segment, or
                 says which reorder it paid for; a merge inserts at the end it arrives at
    durations    each event lasts what the paper's duration law says it lasts
    circuit      the two-qubit gates executed are the circuit's, each once, in an order the
                 circuit allows

Every check that is configured reports a count of violations, and a check that is not
configured says so instead of passing.  `Report.ok` is true only when every configured
check found nothing.

Intervals are closed-open, ``[t0, t1)``: an event that starts exactly when another on the
same resource ends does not overlap it.  Times are compared with `Profile.tol`, because
several artifacts compute in floats.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "Event",
    "PortGraph",
    "Profile",
    "TimedSchedule",
    "Violation",
    "Report",
    "check",
    "MOVE_KINDS",
    "PLACE_KINDS",
]

#: Events that change where an ion is.
MOVE_KINDS = ("split", "merge", "move", "transit", "j_enter", "j_exit", "hop")
#: Events that act on ions where they stand.
PLACE_KINDS = ("gate", "gate1", "measure", "reset", "prepare", "swap", "cool")

INF = math.inf


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class Event:
    """One primitive, as the artifact recorded it.

    ``at``    the trap (site) a gate, split, merge, swap or single-ion op happens in
    ``seg``   the segment a split leaves onto or a merge arrives from
    ``src``/``dst``/``via``  a move: from one segment to the next, across node ``via``;
              a hop: from node ``src`` to node ``dst`` through the nodes in ``path``
    ``swap``  a reorder folded into a split: ``{"kind": "gate"|"ion", "with": ion,
              "hops": n}`` -- QCCDSim prices the swap inside the split rather than as an
              event of its own
    ``meta``  everything else the artifact said, verbatim
    """

    id: int
    kind: str
    t0: float
    t1: float
    ions: tuple[str, ...]
    at: str | None = None
    seg: str | None = None
    src: str | None = None
    dst: str | None = None
    via: str | None = None
    path: tuple[str, ...] = ()
    swap: Mapping[str, Any] | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    def to_json(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v not in (None, (), {})}
        d["ions"] = list(self.ions)
        if self.path:
            d["path"] = list(self.path)
        return d

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Event":
        return cls(
            id=int(d["id"]), kind=str(d["kind"]), t0=float(d["t0"]), t1=float(d["t1"]),
            ions=tuple(str(i) for i in d.get("ions", ())), at=d.get("at"), seg=d.get("seg"),
            src=d.get("src"), dst=d.get("dst"), via=d.get("via"),
            path=tuple(d.get("path", ())), swap=d.get("swap"), meta=dict(d.get("meta", {})),
        )


@dataclass
class PortGraph:
    """The machine as the timing model sees it.

    ``sites``     trap id -> {"capacity": int, "ports": {segment id: "L" | "R"}, ...}; the
                  port side says which end of the chain a segment attaches to
    ``nodes``     junction id -> {...}; an ion may stand on one only between a ``j_enter``
                  and a ``j_exit`` (or for the duration of a move or hop that crosses it)
    ``segments``  segment id -> (endpoint, endpoint), each a site or a node
    ``adjacent``  for profiles whose two-qubit gate acts across neighbouring zones (TISCC):
                  site id -> the sites a gate partner may stand in
    """

    sites: dict[str, dict]
    nodes: dict[str, dict] = field(default_factory=dict)
    segments: dict[str, tuple[str, str]] = field(default_factory=dict)
    adjacent: dict[str, list[str]] = field(default_factory=dict)

    def capacity(self, site: str) -> int:
        return int(self.sites[site]["capacity"])

    def port(self, site: str, seg: str) -> str | None:
        return self.sites[site].get("ports", {}).get(seg)

    def incident(self, node: str) -> list[str]:
        return [s for s, (a, b) in self.segments.items() if node in (a, b)]

    def degree(self, node: str) -> int:
        return len(self.incident(node))

    def to_json(self) -> dict:
        return {"sites": self.sites, "nodes": self.nodes,
                "segments": {k: list(v) for k, v in self.segments.items()},
                "adjacent": self.adjacent}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "PortGraph":
        return cls(sites={k: dict(v) for k, v in d["sites"].items()},
                   nodes={k: dict(v) for k, v in d.get("nodes", {}).items()},
                   segments={k: (v[0], v[1]) for k, v in d.get("segments", {}).items()},
                   adjacent={k: list(v) for k, v in d.get("adjacent", {}).items()})


@dataclass(frozen=True)
class Profile:
    """The rules a paper says it assumes.  Nothing here is a default for OUR machine.

    ``trap_serial``     event kinds that hold their trap exclusively ("one operation at a
                        time per trap"); empty = no such rule
    ``segment_mutex``   at most one ion on a segment at any time
    ``junction_mutex``  at most one ion in or crossing a junction at any time
    ``chain_order``     None (not tracked), "qccdsim" (a reorder removes the moving ion
                        from where it stood, QCCDSim's bookkeeping) or "physical" (a gate
                        swap exchanges the two ions' states, so the end ion's label moves to
                        the moving ion's old position)
    ``gate_locality``   "same_site", or "adjacent" (both ions in neighbouring zones, TISCC)
    ``circuit_order``   "strict" (each qubit's gates in program order), "commuting" (each
                        qubit's gates in program order up to reordering a run in which that
                        qubit plays the same CX role), or "multiset"
    ``broadcast``       event kinds driven by one shared waveform (TrapSIMD's JT-SIMD inter-trap
                        transport): all such events that start together must carry the same
                        ``meta["class"]`` and start and end together -- one class per cycle --
                        and two cycles never overlap
    ``op_zones``        (kind, zones): an event of that kind runs only on a site whose
                        ``zone`` is one of these (TISCC: rotations, preparation and
                        measurement in O zones)
    ``declared_locks``  also check the artifact's OWN statement of what each event holds
                        (``meta["locks"]``): two events naming one component never overlap.
                        This judges the schedule by the rule its author wrote down, beside
                        the physical checks, which do not depend on that statement
    """

    name: str
    trap_serial: tuple[str, ...] = ()
    segment_mutex: bool = False
    junction_mutex: bool = False
    chain_order: str | None = None
    gate_locality: str = "same_site"
    circuit_order: str = "strict"
    broadcast: tuple[str, ...] = ()
    declared_locks: bool = False
    op_zones: tuple[tuple[str, tuple[str, ...]], ...] = ()
    tol: float = 1e-6

    def to_json(self) -> dict:
        d = asdict(self)
        d["trap_serial"] = list(self.trap_serial)
        return d


@dataclass
class TimedSchedule:
    """A schedule plus everything needed to judge it.

    ``chains``   the initial ions of every trap, in chain order (left to right)
    ``qubits``   program qubit -> ion, when the artifact distinguishes them
    ``source``   provenance: repository, commit, command, configuration
    ``claims``   what the artifact printed, under its own labels
    """

    name: str
    device: PortGraph
    chains: dict[str, list[str]]
    events: list[Event]
    qubits: dict[str, str] = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    claims: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"name": self.name, "device": self.device.to_json(), "chains": self.chains,
                "qubits": self.qubits, "source": self.source, "claims": self.claims,
                "events": [e.to_json() for e in self.events]}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "TimedSchedule":
        return cls(name=d["name"], device=PortGraph.from_json(d["device"]),
                   chains={k: list(v) for k, v in d["chains"].items()},
                   events=[Event.from_json(e) for e in d["events"]],
                   qubits=dict(d.get("qubits", {})), source=dict(d.get("source", {})),
                   claims=dict(d.get("claims", {})))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_json(), separators=(",", ":")), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "TimedSchedule":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


# ----------------------------------------------------------------------- results


@dataclass(frozen=True)
class Violation:
    check: str
    message: str
    events: tuple[int, ...] = ()


@dataclass
class Report:
    """What the replay found.

    ``checks``  check name -> "passed" | "failed" | "skipped: <why>"
    ``metrics`` makespan, counts and summed durations by kind, and whatever the duration
                model adds (fidelity, heating)
    ``notes``   things that are legal but worth saying -- a reorder charged for an ion
                that was already at the end of its chain is wasted time, not a fault
    """

    schedule: str
    profile: str
    checks: dict[str, str] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: Counter = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.violations and all(not v.startswith("failed") for v in self.checks.values())

    def failed(self, name: str) -> list[Violation]:
        return [v for v in self.violations if v.check == name]

    def summary(self) -> dict:
        by = Counter(v.check for v in self.violations)
        return {"schedule": self.schedule, "profile": self.profile, "ok": self.ok,
                "checks": self.checks, "violations": dict(by), "metrics": self.metrics,
                "notes": dict(self.notes),
                "first": [asdict(v) for v in self.violations[:12]]}


# ------------------------------------------------------------------------ checker


@dataclass
class Context:
    """What a duration law may look at: the state just BEFORE the event."""

    device: PortGraph
    chain: list[str] | None        # the chain of ``event.at`` (a copy), if tracked
    occupancy: int | None          # ions in ``event.at``


class _Intervals:
    """Closed-open intervals per resource; reports the overlapping pairs."""

    def __init__(self) -> None:
        self.by: dict[str, list[tuple[float, float, int]]] = defaultdict(list)

    def add(self, key: str, t0: float, t1: float, eid: int) -> None:
        self.by[key].append((t0, t1, eid))

    def overlaps(self, tol: float) -> Iterable[tuple[str, int, int, float, float]]:
        for key, ivs in self.by.items():
            ivs.sort()
            end, owner = -INF, None
            for t0, t1, eid in ivs:
                if t0 < end - tol:
                    yield key, owner, eid, t0, end
                if t1 > end:
                    end, owner = t1, eid


def _same(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def check(
    sched: TimedSchedule,
    profile: Profile,
    *,
    duration: Callable[[Event, Context], float | None] | None = None,
    circuit: Sequence[tuple[str, str]] | None = None,
    observers: Sequence[Callable[[Event, Context], None]] = (),
    strict: bool = False,
) -> Report:
    """Replay ``sched`` under ``profile`` and report every violation.

    ``duration(event, ctx)`` returns the duration the paper's law gives the event (or
    None for no claim).  ``circuit`` lists the circuit's two-qubit gates as (control,
    target) program qubits, in program order.  ``observers`` see every event with the state
    before it, in replay order -- that is how a fidelity or heating model rides along
    without the checker knowing about it.

    ``strict`` is for schedules we write ourselves: an event the law gives no duration is
    not an operation of the paper's model, and fails ``durations`` instead of passing
    unpriced (a zero-time ``hop`` under QCCDSim's law, say).  An artifact's own schedule is
    replayed without it, since a law may leave alone what the paper does not time.
    """
    dev, tol = sched.device, profile.tol
    rep = Report(schedule=sched.name, profile=profile.name)
    seg_between: dict[frozenset, str] = {}
    for sid, ends in dev.segments.items():
        seg_between.setdefault(frozenset(ends), sid)
    bad = rep.violations

    def v(check_name: str, msg: str, *eids: int) -> None:
        bad.append(Violation(check_name, msg, tuple(eids)))

    # -- initial state ---------------------------------------------------------
    loc: dict[str, tuple[str, str]] = {}
    chains: dict[str, list[str]] = {s: [] for s in dev.sites}
    arrival: dict[str, float] = {}              # ion -> time it arrived where it is now
    site_res = _Intervals()                     # residency, for capacity
    seg_res = _Intervals()
    node_res = _Intervals()
    trap_ops = _Intervals()
    ion_ops = _Intervals()
    locks = _Intervals()
    for site, ions in sched.chains.items():
        if site not in dev.sites:
            v("structure", f"initial chain on unknown site {site!r}")
            continue
        for ion in ions:
            if ion in loc:
                v("structure", f"ion {ion} placed twice initially")
            loc[ion] = ("site", site)
            chains[site].append(ion)
            arrival[ion] = -INF

    def leave(ion: str, t: float) -> None:
        kind, where = loc[ion]
        if kind == "site":
            site_res.add(where, arrival[ion], t, -1)
        elif kind == "seg":
            seg_res.add(where, arrival[ion], t, -1)
        elif kind == "node":
            node_res.add(where, arrival[ion], t, -1)

    def arrive(ion: str, kind: str, where: str, t: float) -> None:
        loc[ion] = (kind, where)
        arrival[ion] = t

    counts: Counter[str] = Counter()
    busy: defaultdict[str, float] = defaultdict(float)
    gates_done: list[tuple[float, int, tuple[str, ...]]] = []
    dur_bad = 0

    events = sorted(sched.events, key=lambda e: (e.t0, e.id))
    for e in events:
        counts[e.kind] += 1
        busy[e.kind] += e.duration
        if e.t1 < e.t0 - tol:
            v("structure", f"event {e.id} ends before it starts", e.id)
        for ion in e.ions:
            if ion not in loc:
                v("structure", f"event {e.id} names unknown ion {ion}", e.id)
        if any(ion not in loc for ion in e.ions):
            continue
        for ion in e.ions:
            ion_ops.add(ion, e.t0, e.t1, e.id)
        if e.at is not None and e.kind in profile.trap_serial:
            trap_ops.add(e.at, e.t0, e.t1, e.id)
        if profile.declared_locks:
            if strict and not e.meta.get("locks"):
                v("declared_locks", f"event {e.id} {e.kind} declares nothing it holds", e.id)
            for comp in e.meta.get("locks", ()):
                locks.add(str(comp), e.t0, e.t1, e.id)

        chain = chains.get(e.at) if e.at in chains else None
        ctx = Context(dev, list(chain) if chain is not None else None,
                      len(chain) if chain is not None else None)
        for obs in observers:
            obs(e, ctx)
        if duration is not None:
            want = duration(e, ctx)
            if want is None and strict:
                dur_bad += 1
                v("durations", f"event {e.id}: the law gives {e.kind} no duration, so it is not "
                  "an operation of this model", e.id)
            elif want is not None and not _same(e.duration, want, tol):
                dur_bad += 1
                v("durations", f"event {e.id} {e.kind} lasts {e.duration:g} us, the law says "
                  f"{want:g}", e.id)

        # -- in-place events ------------------------------------------------------
        if e.kind in PLACE_KINDS:
            where = [loc[i] for i in e.ions]
            if e.kind == "gate" and len(e.ions) == 2:
                (k1, s1), (k2, s2) = where
                if k1 != "site" or k2 != "site":
                    v("positions", f"gate {e.id} on ions not in traps: {where}", e.id)
                elif profile.gate_locality == "adjacent":
                    if not (s1 == s2 or s2 in dev.adjacent.get(s1, ()) or s1 in dev.adjacent.get(s2, ())):
                        v("positions", f"gate {e.id}: {e.ions} in {s1} and {s2}, not neighbours", e.id)
                elif s1 != s2:
                    v("positions", f"gate {e.id}: {e.ions[0]} in {s1}, {e.ions[1]} in {s2}", e.id)
                if e.at is not None and k1 == "site" and e.at not in (s1, s2):
                    v("positions", f"gate {e.id} says trap {e.at}, ions are in {s1}/{s2}", e.id)
                gates_done.append((e.t0, e.id, e.ions))
            else:
                zones = dict(profile.op_zones).get(e.kind)
                for ion, (k, s) in zip(e.ions, where):
                    if k != "site":
                        v("positions", f"{e.kind} {e.id} on {ion} while it is on {k} {s}", e.id)
                    elif e.at is not None and s != e.at:
                        v("positions", f"{e.kind} {e.id} says {e.at}, {ion} is in {s}", e.id)
                    elif zones is not None and dev.sites[s].get("zone") not in zones:
                        v("positions", f"{e.kind} {e.id} in {s}, a {dev.sites[s].get('zone')} zone; "
                          f"this model runs it only in {'/'.join(zones)}", e.id)
            if e.kind == "swap" and len(e.ions) == 2 and chain is not None and profile.chain_order:
                a, b = e.ions
                if a in chain and b in chain:
                    i, j = chain.index(a), chain.index(b)
                    chain[i], chain[j] = chain[j], chain[i]
            continue

        # -- moves ----------------------------------------------------------------
        if len(e.ions) != 1:
            v("structure", f"{e.kind} {e.id} carries {len(e.ions)} ions; one expected", e.id)
            continue
        ion = e.ions[0]
        kind, where = loc[ion]
        if e.kind == "split":
            if kind != "site" or where != e.at:
                v("positions", f"split {e.id}: {ion} is on {kind} {where}, not in {e.at}", e.id)
                continue
            if e.seg not in dev.segments or e.at not in dev.segments[e.seg]:
                v("structure", f"split {e.id}: segment {e.seg} does not touch {e.at}", e.id)
                continue
            if profile.chain_order:
                _split_order(chains[e.at], ion, dev.port(e.at, e.seg), e, profile.chain_order, v,
                             rep.notes)
            else:
                chains[e.at].remove(ion)
            leave(ion, e.t1)
            arrive(ion, "seg", e.seg, e.t0)
        elif e.kind == "merge":
            if kind != "seg" or where != e.seg:
                v("positions", f"merge {e.id}: {ion} is on {kind} {where}, not on {e.seg}", e.id)
                continue
            if e.at not in dev.sites or e.at not in dev.segments.get(e.seg, ()):
                v("structure", f"merge {e.id}: segment {e.seg} does not touch {e.at}", e.id)
                continue
            leave(ion, e.t1)
            side = dev.port(e.at, e.seg)
            if profile.chain_order and side == "L":
                chains[e.at].insert(0, ion)
            else:
                chains[e.at].append(ion)
            arrive(ion, "site", e.at, e.t0)
        elif e.kind == "move":
            if kind != "seg" or where != e.src:
                v("positions", f"move {e.id}: {ion} is on {kind} {where}, not on {e.src}", e.id)
                continue
            ends = set(dev.segments.get(e.src, ())) & set(dev.segments.get(e.dst, ()))
            if e.via is None and len(ends) == 1:
                via = next(iter(ends))
            else:
                via = e.via
            if via not in ends:
                v("structure", f"move {e.id}: {e.src} and {e.dst} do not meet at {via}", e.id)
                continue
            node_res.add(via, e.t0, e.t1, e.id)
            leave(ion, e.t1)
            arrive(ion, "seg", e.dst, e.t0)
        elif e.kind == "transit":
            if kind != "seg" or where != e.seg:
                v("positions", f"transit {e.id}: {ion} is on {kind} {where}, not on {e.seg}", e.id)
                continue
        elif e.kind == "j_enter":
            if kind != "seg" or where != e.src:
                v("positions", f"j_enter {e.id}: {ion} is on {kind} {where}, not on {e.src}", e.id)
                continue
            if e.via not in dev.segments.get(e.src, ()):
                v("structure", f"j_enter {e.id}: {e.src} does not touch {e.via}", e.id)
                continue
            leave(ion, e.t1)
            arrive(ion, "node", e.via, e.t0)
        elif e.kind == "j_exit":
            if kind != "node" or where != e.via:
                v("positions", f"j_exit {e.id}: {ion} is on {kind} {where}, not on {e.via}", e.id)
                continue
            if e.via not in dev.segments.get(e.dst, ()):
                v("structure", f"j_exit {e.id}: {e.dst} does not touch {e.via}", e.id)
                continue
            leave(ion, e.t1)
            arrive(ion, "seg", e.dst, e.t0)
        elif e.kind == "hop":
            if kind != "site" or where != e.src:
                v("positions", f"hop {e.id}: {ion} is on {kind} {where}, not in {e.src}", e.id)
                continue
            if e.dst not in dev.sites:
                v("structure", f"hop {e.id}: destination {e.dst} is not a trap", e.id)
                continue
            walk = [e.src, *e.path, e.dst]
            if any(n not in dev.nodes for n in e.path):
                v("structure", f"hop {e.id}: its path {list(e.path)} passes something that is not "
                  "a junction", e.id)
                continue
            used = [seg_between.get(frozenset((x, y))) for x, y in zip(walk, walk[1:])]
            if None in used:
                x, y = next((x, y) for (x, y), sg in zip(zip(walk, walk[1:]), used) if sg is None)
                v("structure", f"hop {e.id}: no segment joins {x} and {y}", e.id)
                continue
            for sg in used:
                seg_res.add(sg, e.t0, e.t1, e.id)
            for n in e.path:
                node_res.add(n, e.t0, e.t1, e.id)
            chains[e.src].remove(ion)
            leave(ion, e.t1)
            chains[e.dst].append(ion)
            arrive(ion, "site", e.dst, e.t0)
        else:
            v("structure", f"event {e.id}: unknown kind {e.kind!r}", e.id)

    # -- close open residencies at +inf --------------------------------------------
    for ion, (kind, where) in loc.items():
        if kind == "site":
            site_res.add(where, arrival[ion], INF, -1)
        elif kind == "seg":
            seg_res.add(where, arrival[ion], INF, -1)
            v("positions", f"{ion} ends the schedule on segment {where}")
        elif kind == "node":
            node_res.add(where, arrival[ion], INF, -1)
            v("positions", f"{ion} ends the schedule on junction {where}")

    # -- capacity --------------------------------------------------------------------
    peak: dict[str, int] = {}
    for site, ivs in site_res.by.items():
        marks = sorted([(t0, 1) for t0, _, _ in ivs] + [(t1, -1) for _, t1, _ in ivs],
                       key=lambda m: (m[0], m[1]))
        n = top = 0
        when = None
        for t, d in marks:
            n += d
            if n > top:
                top, when = n, t
        peak[site] = top
        cap = dev.capacity(site)
        if top > cap:
            v("capacity", f"trap {site} holds {top} ions at t={when:g} us; capacity {cap}")

    # -- exclusivity and seriality ----------------------------------------------------
    def overlaps(intervals: _Intervals, name: str, what: str) -> None:
        for key, a, b, t0, t1 in intervals.overlaps(tol):
            ids = tuple(x for x in (a, b) if x is not None and x >= 0)
            v(name, f"{what} {key}: overlap at [{t0:g}, {t1:g}) us", *ids)

    if profile.segment_mutex:
        overlaps(seg_res, "segment_mutex", "segment")
    if profile.junction_mutex:
        overlaps(node_res, "junction_mutex", "junction")
    if profile.trap_serial:
        overlaps(trap_ops, "trap_serial", "trap")
    if profile.declared_locks:
        overlaps(locks, "declared_locks", "component")
    overlaps(ion_ops, "ion_serial", "ion")

    # -- broadcast: one transport class per cycle ---------------------------------------------
    if profile.broadcast:
        cycles: dict[float, list[Event]] = defaultdict(list)
        for e in sched.events:
            if e.kind in profile.broadcast:
                cycles[e.t0].append(e)
        for t0, evs in sorted(cycles.items()):
            classes = {e.meta.get("class") for e in evs}
            if len(classes) > 1 or None in classes:
                v("broadcast", f"cycle at {t0:g} us drives {len(classes)} classes "
                  f"({sorted(map(str, classes))}) with one waveform", *[e.id for e in evs])
            if len({e.t1 for e in evs}) > 1:
                v("broadcast", f"cycle at {t0:g} us: its moves end at different times",
                  *[e.id for e in evs])
        spans = sorted((t0, max(e.t1 for e in evs), evs[0].id) for t0, evs in cycles.items())
        for (a0, a1, ia), (b0, _, ib) in zip(spans, spans[1:]):
            if b0 < a1 - tol:
                v("broadcast", f"cycles at {a0:g} and {b0:g} us overlap: one waveform drives one "
                  "cycle at a time", ia, ib)

    # -- circuit ---------------------------------------------------------------------------
    if circuit is not None:
        _check_circuit(sched, circuit, gates_done, profile.circuit_order, v)

    # -- verdicts ------------------------------------------------------------------------
    names = ["structure", "positions", "capacity", "ion_serial"]
    for name, on, why in (("segment_mutex", profile.segment_mutex, "the profile allows sharing"),
                          ("junction_mutex", profile.junction_mutex, "the profile allows sharing"),
                          ("trap_serial", bool(profile.trap_serial), "the profile lets a trap overlap"),
                          ("chain_order", bool(profile.chain_order), "chain order is not tracked"),
                          ("declared_locks", profile.declared_locks, "the artifact states no locks"),
                          ("broadcast", bool(profile.broadcast), "transport is not broadcast in this model"),
                          ("durations", duration is not None, "no duration law given"),
                          ("circuit", circuit is not None, "no circuit given")):
        if on:
            names.append(name)
        else:
            rep.checks[name] = f"skipped: {why}"
    by = Counter(x.check for x in bad)
    for name in names:
        rep.checks[name] = "failed" if by.get(name) else "passed"

    rep.metrics = {
        "makespan_us": max((e.t1 for e in sched.events), default=0.0),
        "last_start_us": max((e.t0 for e in sched.events), default=0.0),
        "events": len(sched.events),
        "counts": dict(counts),
        "busy_us": {k: round(x, 6) for k, x in busy.items()},
        "peak_occupancy": peak,
        "duration_mismatches": dur_bad,
    }
    return rep


def _split_order(chain: list[str], ion: str, side: str | None, e: Event, mode: str, v,
                 notes: Counter) -> None:
    """Remove ``ion`` from ``chain`` the way the profile's reorder semantics say."""
    if side not in ("L", "R"):
        v("chain_order", f"split {e.id}: segment {e.seg} has no port side at {e.at}", e.id)
        chain.remove(ion)
        return
    end = 0 if side == "L" else len(chain) - 1
    if chain[end] == ion:
        if e.swap:
            notes["reorder charged for an ion already at the end"] += 1
        chain.pop(end)
        return
    sw = e.swap or {}
    if not sw:
        v("chain_order", f"split {e.id}: {ion} is not at the {side} end of {e.at} "
          f"({chain[end]} is) and no reorder was charged", e.id)
        chain.remove(ion)
        return
    k = sw.get("kind")
    partner = sw.get("with")
    hops = sw.get("hops")
    pos = chain.index(ion)
    if hops is not None and int(hops) != abs(end - pos):
        v("chain_order", f"split {e.id}: reorder charged {hops} hops, {ion} is "
          f"{abs(end - pos)} from the {side} end", e.id)
    if k == "gate":
        if partner is not None and partner != chain[end]:
            v("chain_order", f"split {e.id}: gate swap with {partner}, but the {side} end "
              f"ion is {chain[end]}", e.id)
        if mode == "physical":
            chain[pos] = chain[end]       # the end ion's state now sits where ion was
            chain.pop(end)
        else:                             # "qccdsim": the moving ion is lifted out in place
            chain.pop(pos)
    else:                                 # an ion swap walks the ion to the end
        chain.pop(pos)


def _check_circuit(sched: TimedSchedule, circuit: Sequence[tuple[str, str]],
                   done: list[tuple[float, int, tuple[str, ...]]], order: str, v) -> None:
    ion_of = sched.qubits or {}
    to_ion = (lambda q: ion_of.get(str(q), str(q)))
    want = [(to_ion(a), to_ion(b)) for a, b in circuit]
    got = [ions for _, _, ions in sorted(done)]
    key = lambda p: tuple(sorted(p))
    cw, cg = Counter(map(key, want)), Counter(map(key, got))
    if cw != cg:
        missing = cw - cg
        extra = cg - cw
        v("circuit", f"executed two-qubit gates differ from the circuit: {sum(missing.values())} "
          f"missing (e.g. {list(missing)[:3]}), {sum(extra.values())} extra (e.g. {list(extra)[:3]})")
        return
    if order == "multiset":
        return
    seq_w: dict[str, list] = defaultdict(list)
    seq_g: dict[str, list] = defaultdict(list)
    directed = set(want)
    for a, b in want:
        seq_w[a].append(("c", b))
        seq_w[b].append(("t", a))
    for a, b in got:
        # the artifact may not say which ion is the control; take the circuit's word for it
        # unless the circuit has the pair both ways, when the artifact's order stands
        if (a, b) in directed or (b, a) not in directed:
            c, t = a, b
        else:
            c, t = b, a
        seq_g[c].append(("c", t))
        seq_g[t].append(("t", c))

    def canon(seq: list) -> list:
        if order == "strict":
            return list(seq)            # (role, partner): a CX and its reverse differ
        out, run, role = [], [], None
        for r, p in seq:
            if r != role and run:
                out.append((role, tuple(sorted(run))))
                run = []
            role = r
            run.append(p)
        if run:
            out.append((role, tuple(sorted(run))))
        return out

    wrong = [q for q in seq_w if canon(seq_w[q]) != canon(seq_g.get(q, []))]
    if wrong:
        v("circuit", f"{len(wrong)} qubit(s) see their gates in an order the circuit does not "
          f"allow (e.g. {wrong[:4]})")
