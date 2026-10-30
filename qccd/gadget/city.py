"""Scheduling a logical algorithm on a design of verified places.  GADGETS.md §9.

A design is a composite of place instances (prep, clinic, workshop, bridge, readout,
injection, factory, zones, depots) and road junctions, joined by channels.  A logical block
is a bundle of nine ions that travels between places; every instruction of the algorithm
(`qccd.gadget.algorithm`) is one op at one place, plus the trips that bring its blocks
there.  Only characterized abstracts are read: op durations, per-ion door crossing times,
junction passes, and channel transits replayed from the conveyor reference.

Two policies (docs/ARCHITECTURE.md §7):

* **parallel** (the default): program order is the order instructions are *issued*, and each
  is placed as early as its own blocks and the resources it needs allow -- so a factory
  produces a magic state while a lattice surgery runs elsewhere, exactly when their places,
  roads, junctions and ions are disjoint (G13).  A place serves the instructions it is given
  in the order they are issued, and sends a bundle out of a door only once the road in front
  of that door is clear of the last one.  A trip is a **green wave**: the bundle waits only on
  the road in front of the door it leaves and the road in front of the door it reaches (each
  holds a whole bundle, F5); between them every junction and road is leased to it for exactly
  the time it is there, and the whole wave starts later if any of them is taken.
* **sequential**: instructions run one after another (an instruction starts once everything
  the previous one scheduled has finished) and road trips too, the policy the place model
  began with, kept to compare against.

In both:

* a trip is the shortest route over channels and junctions (places are endpoints, never
  thoroughfares); a junction passes a bundle when it is free, and until then the bundle
  queues in the channel in front of it -- every channel a bundle can queue in holds at least
  nine sites, which G4 checks;
* a block that is not needed by the very next instruction goes to rest in a free logical
  zone, so no block ever blocks a door;
* depots are sources and sinks: `load` sends out newly loaded ions (fresh global ids),
  `dump` takes used ones out of the computation.

The classical half is scheduled in the same way, with its own two moves:

* a **message** is bits crossing a wire: it leaves a place's classical port when the bits
  exist (the last measurement of the op that produced them, not the end of the op) and
  arrives one wire latency later;
* a **classical op** is one job at the decoder or the archive -- `decode` a window,
  `record` an outcome, `update` a frame -- reserved on that place's calendar like any other
  op, so two decoding jobs never overlap.

**Decoding is an instruction.**  An op that measures leaves a *syndrome window* in its
place's readout buffer and nothing more; the `decode` instruction is what sends it.  Each
window's bits then leave the place that measured them, cross its syndrome wire, are decoded
in one job, and the frame update crosses the frame wire into the archive.  A logical outcome
is not a result until its window is decoded, so a guard that reads one waits for that -- and
is refused if the program never asked -- and a window still waiting when the program ends is
reported (G11).

**Classically controlled execution.** A guarded instruction (`if m: s q0`) is scheduled
exactly like an unguarded one -- same route, same place, same reserved duration, the worst
case -- and what depends on the bit is only whether the pulses fire: a pre-compiled branch
with the same port contract, which is how trapped-ion control systems run conditionals (AQT
M-ACTION, arXiv:2101.11390).  So the guard must be *decided and delivered* first: the
archive `resolve`s it after the outcomes it reads are recorded, sends it down the decision
wire to the place's `ctl` port, and the op may not start before it lands (G11).  The
schedule stays static; only the meaning of one op depends on a measurement.

Corrections are never applied to ions.  Every block carries a **Pauli frame** `X^ax Z^az`
whose two bits are parity expressions over logical outcomes, held in the archive; the
scheduler propagates it through the gates (a transversal CNOT copies it, the S̄ gadget
conjugates it), flips the outcomes it anticommutes with, and writes every change into the
archive as a frame row.  `qccd.gadget.logic.flat` then signs the schedule off against the
algorithm *up to exactly those frames*.

The output is a GIR with the same events and carries as the beta's (`synth.py`), marked
`"model": "places"`, which the page draws and `qccd.gadget.logic.flat` checks against the
algorithm's ideal circuit.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .algorithm import CLASSICAL, PPM_MAX, Algorithm, Instr, rotation_basis
from .library import LeafLibrary
from .model import Library
from .logic.decode import LATENCY
from .synth import Calendar, SynthError, flatten, ion_order
from .heat import op_heat, road_heat

__all__ = ["schedule", "CityError"]


# --------------------------------------------------------------------------- parity algebra
#
# A frame bit, an outcome and a guard are all the same thing: the parity of a constant and
# a set of logical outcomes, each named by the event that measured it and the flow of that
# gadget's own verified report which says which records the outcome is.  The sign-off
# resolves those names to records; nothing here needs to know what a record is.

ZERO = (0, frozenset())
ONE = (1, frozenset())


def xor(*es):
    c, r = 0, frozenset()
    for e in es:
        c ^= e[0]
        r = r ^ e[1]
    return (c, r)


def outcome_of(event_id: int, flow: str):
    return (0, frozenset({(event_id, flow)}))


def _source_key(x):
    # an event id, or (in a folded schedule, `qccd.gadget.fold`) a named symbol: a parameter's
    # frame on entry, an outer guard, an outcome of a call made inside
    return (0, x[0], x[1]) if isinstance(x[0], int) else (1, str(x[0]), x[1])


def expr_json(e) -> dict:
    return {"const": e[0], "sources": [list(x) for x in sorted(e[1], key=_source_key)]}


def expr_text(e, names=None) -> str:
    parts = ([] if not e[0] else ["1"]) + [
        (names or {}).get(x, f"m@{x[0]}") for x in sorted(e[1], key=_source_key)]
    return " ⊕ ".join(parts) if parts else "0"

GIR_SCHEMA = "qccd.gir/0.2"

#: under `park_round=False`, how long after its last syndrome round a parked block takes one
#: anyway: half the refresh period a resting block gets (fold.REFRESH_EVERY_US), so no block
#: waits past the half of the coherence budget G8 allows it at a call's edge
ROUND_DUE_US = 200_000.0


class CityError(SynthError):
    pass


#: an instruction pinned to no instance: depots, zones and roads are chosen by distance
_ANY = Instr(-1, 0, "", "any", [])


class Bundle:
    def __init__(self, name, gids, leaf, port, deps):
        self.name = name
        self.gids = list(gids)
        self.leaf = leaf               # the place it left, or the zone it rests in
        self.port = port
        self.deps = list(deps)         # departure times through `port`
        self.resting = False


@dataclass
class _Times:
    times_us: list


@dataclass
class _Train:
    op: str
    name: str
    each_us: float
    duration_us: float
    tin: _Times
    tout: _Times


class City:
    def __init__(self, alg: Algorithm, lib: Library, leaves: LeafLibrary, log=None,
                 parallel: bool = True, rounds_at: str = "clinic", free_at: str = "instruction",
                 prefetch: bool = False, park_round: bool = True, park_within: int = 1):
        self.alg, self.lib, self.leaves = alg, lib, leaves
        self.parallel = parallel
        #: when a block an instruction acted on is free for the next: "instruction" (once all
        #: of the instruction is over -- a CNOT's ancilla waits while its partner is stored)
        #: or "block" (once what it did to that block is over: the codesign choice of
        #: 2026-10-29, measured on Gidney 2025's carry chain)
        self.free_at = free_at
        #: a CNOT's operand resting in a memory starts out as early as its release takes, so it
        #: arrives with the other instead of after it (`bring`'s just-in-time rule still holds
        #: it back while the workshop is busy): the codesign choice of 2026-10-29
        self.prefetch = prefetch
        #: whether a block parked in a memory between uses takes a syndrome round there (`store`)
        #: or only rests (`hold`, where the memory has it), its rounds left to the refreshes its
        #: coherence budget asks for; an `se` always gets its rounds.  And how near a block's
        #: next use must be -- within `park_within` instructions that move ions -- for it to wait
        #: where it is instead of parking (1: only the very next).  2026-10-30, the user: "measure
        #: both, decide later"; the defaults are the rule as it was
        self.park_round, self.park_within = park_round, park_within
        #: block -> when its last syndrome round ended (`report`): under `hold` a parked block
        #: still takes its round once ROUND_DUE_US has passed since then
        self.last_round: dict[str, float] = {}
        #: where `se b k` runs its k syndrome rounds: "clinic" (a block goes to a syndrome
        #: clinic and back) or "memory" (the block rests in a memory and the memory runs the
        #: rounds in place, every block's at once -- the codesign choice of 2026-10-29: a
        #: merge's rounds were most of Gidney 2025's machine time, queued at one clinic)
        self.rounds_at = rounds_at
        #: block -> when the last instruction on it finished: the only barrier there is,
        #: when instructions run in parallel
        self.block_free: dict[str, float] = {}
        self.log = log or (lambda *a, **k: None)
        self.wires: dict = {}
        self.leaf, self.nets, self.n_residents = flatten(lib, leaves, wires=self.wires)
        self.next_gid = self.n_residents
        self.cal = {p: Calendar() for p in self.leaf}
        self.net_cal = {p: Calendar() for p in self.nets}
        self.events: list[list] = []
        self.carries: list[list] = []
        self.refused: list[list] = []
        #: [instruction, why]: instructions that had nothing to do (a decode under `hold`)
        self.noops: list[list] = []
        self.outcomes: list[dict] = []
        self.frames: list[dict] = []
        self.blocks: dict[str, Bundle] = {}
        self.block_info: dict[str, dict] = {}
        #: the places a block can rest in -- logical zones, and memories, which also correct
        #: what they hold -- and which block rests in each
        self.zone_holder = {p: None for p, li in self.leaf.items()
                            if li.master.family in ("zone", "memory")}
        #: memory -> when the last op on the block resting there ended
        self.mem_last: dict[str, float] = {}
        self.stock = {p: 0 for p, li in self.leaf.items() if li.master.family == "depot"}
        self.t_now = 0.0
        #: the streets carry one bundle at a time: the next trip starts when the last
        #: one has arrived, so no bundle ever queues in a channel shorter than itself (G4)
        self.road_free = 0.0
        self.next_use = alg.next_use()
        self.next_quantum = alg.next_quantum()
        self.messages: list[list] = []
        #: the syndrome windows, in the order they were measured: each waits in its place's
        #: readout buffer until a `decode` instruction sends it to the decoder
        self.windows: list[dict] = []
        #: a resource block an S̄ gadget consumed -> the block it was consumed into, so that
        #: `decode q` also takes the windows the |Ȳ⟩ block was measured in
        self.merged_into: dict[str, str] = {}
        #: a named outcome -> the window its measurement left
        self.cvar_window: dict[str, int] = {}
        #: block -> (ax, az): the Pauli frame the archive holds for it
        self.pf: dict[str, tuple] = {}
        #: (instruction, flow) -> the corrected logical outcome, as a parity expression
        #: (one instruction can measure more than once: the S̄ gadget measures twice)
        self.values: dict[tuple, tuple] = {}
        self.cvars: dict[tuple, str] = {}
        #: a named outcome: its parity expression, and when the archive had it
        self.cvar_value: dict[str, tuple] = {}
        self.cvar_time: dict[str, float] = {}
        #: instruction -> (when its guard was resolved, the archive event, per-place arrival)
        self.resolved: dict[int, tuple] = {}
        #: block -> the guards of the conditional frame writes it has taken
        self.pf_guarded: dict[str, list] = {}
        #: the outcomes some guard reads, and every one of them read through a frame that
        #: depends on a branch (see `certain`)
        self.guard_reads = set(alg.guard_cvars)
        self.branch_frames: list[dict] = []
        #: every ion's n̄ as the schedule is made (heat.py): an op replayed from the n̄ its
        #: ions arrive at, a road and a junction pass adding what their replay charges.  A
        #: trip that would take a bundle past the ion-loss limit goes the coolest way, and a
        #: bundle is sent only where it can arrive under it (G6).
        self.heat: dict[int, float] = {}
        from .checks import ION_LOSS_QUANTA
        self.heat_limit = float(ION_LOSS_QUANTA)
        self._entry_memo: dict = {}
        #: carry row -> the time its road may first load (a trip over two roads of one shared
        #: plane waits for the first to be done: store-and-forward); in the GIR as `road_holds`
        self._holds: dict = {}
        self._spans: dict = {}            # carry row -> (first load, last delivery), on a plane
        self._graph()
        self._control()
        self._share_planes()

    # -- shared planes (planes.py) --------------------------------------------------------------

    def _share_planes(self) -> None:
        """Devices a floorplan says share one plane of DACs, or one laser, take turns at
        WORK: each group of planes (`planes.resolve`) has an activity calendar beside the
        devices' own -- which also hold occupancy, a block resting in a zone, that plays no
        waveform.  An op at a member place, a junction's train and a road's carry each fit
        into it; within one trip, a member's activity starts once the trip's last one on the
        same plane is done (store-and-forward)."""
        from .planes import resolve
        fams = {p: li.master.family for p, li in self.leaf.items()}
        self.planes = resolve(self.lib, fams, self.nets)
        self._net_group, self._place_group = {}, {}
        kind = type(next(iter(self.cal.values()))) if self.cal else Calendar
        self.plane_cal = {}
        for name, pl in self.planes.items():
            g = pl["group"]
            self.plane_cal.setdefault(g, kind())
            for p in pl["places"]:
                self._place_group[p] = g
            for n in pl["nets"]:
                self._net_group[n] = g

    def _plane_fit(self, g, t: float, dur: float) -> float:
        """The earliest time from `t` at which plane group `g` is free for `dur`."""
        return t if g is None else self.plane_cal[g].earliest(t, dur)

    def _plane_take(self, g, t0: float, t1: float) -> None:
        if g is not None and t1 > t0:
            self.plane_cal[g].reserve(t0, t1)

    # -- the road network -----------------------------------------------------------------

    def family(self, path):
        return self.leaf[path].master.family

    def _graph(self):
        self.adj: dict = {}

        def edge(u, v, w, kind, info):
            self.adj.setdefault(u, []).append((v, w, kind, info))

        #: (place, door) -> the road in front of it (a junction's arms are not doors)
        self.door_net: dict[tuple, str] = {}
        for n in self.nets.values():
            w = self.leaves.road_timing(n.conveyor, 1)["transit_us"]
            edge(n.a, n.b, w, "net", n.path)
            edge(n.b, n.a, w, "net", n.path)
            for end in (n.a, n.b):
                if not self.leaf[end[0]].master.name.startswith("junc"):
                    self.door_net[end] = n.path
        for path, li in self.leaf.items():
            if li.master.name.startswith("junc"):
                for a in li.master.ports:
                    for b in li.master.ports:
                        if a.name != b.name:
                            op = li.master.ops[f"pass.{a.node}.{b.node}.1"]
                            edge((path, a.name), (path, b.name), 9 * op.duration_us, "junction",
                                 (path, a.name, b.name, f"pass.{a.node}.{b.node}"))

    def _dijkstra(self, src):
        dist = {src: (0.0, None)}
        pq = [(0.0, 0, src)]
        tie = 0
        while pq:
            d, _, u = heapq.heappop(pq)
            if d > dist[u][0]:
                continue
            if u != src and not self.leaf[u[0]].master.name.startswith("junc"):
                continue                       # places are endpoints, not thoroughfares
            for v, w, kind, info in self.adj.get(u, ()):
                nd = d + w
                if v not in dist or nd < dist[v][0]:
                    dist[v] = (nd, (u, kind, info))
                    tie += 1
                    heapq.heappush(pq, (nd, tie, v))
        return dist

    def _route(self, src, dst):
        dist = self._dijkstra(src)
        if dst not in dist:
            raise CityError(f"no road from {src[0]}.{src[1]} to {dst[0]}.{dst[1]}")
        hops, v = [], dst
        while dist[v][1] is not None:
            u, kind, info = dist[v][1]
            hops.append((kind, info, u, v))
            v = u
        return list(reversed(hops))

    def _distance(self, src, dst):
        d = self._dijkstra(src).get(dst)
        return d[0] if d else float("inf")

    # -- heat ---------------------------------------------------------------------------------

    def _heat_through(self, path, opname, bind) -> None:
        hs = op_heat(self.leaves, self.leaf[path].master, opname) if path in self.leaf else None
        if not hs:
            return
        for name, gid in bind:
            h = hs.get(name)
            if h is not None and isinstance(gid, int):
                self.heat[gid] = h.through(self.heat.get(gid, 0.0))[1]

    def _bundle_heat(self, gids) -> float:
        return max((self.heat.get(g, 0.0) for g in gids), default=0.0)

    def _hop_heat(self, kind, info, n: int) -> float:
        if kind == "net":
            return road_heat(self.leaves, self.nets[info].conveyor)
        jpath, _a, _b, opname = info
        hs = op_heat(self.leaves, self.leaf[jpath].master, f"{opname}.1")
        return max((h.pre for h in hs.values() if h.enters), default=0.0) if hs else 0.0

    def _entry_heat(self, dst) -> float:
        """The most any op of place `dst[0]` taking ions in at door `dst[1]` heats one before
        it cools it: what a bundle must still have room for when it arrives."""
        if dst not in self._entry_memo:
            li = self.leaf[dst[0]]
            worst = 0.0
            for name, O in li.master.ops.items():
                if not any(t.dir == "in" and t.port == dst[1] for t in O.transfers):
                    continue
                hs = op_heat(self.leaves, li.master, name)
                if hs:
                    worst = max([worst] + [h.pre for h in hs.values() if h.enters])
            self._entry_memo[dst] = worst
        return self._entry_memo[dst]

    def _route_heat(self, hops, n: int) -> float:
        return sum(self._hop_heat(kind, info, n) for kind, info, _u, _v in hops)

    def _coolest(self, src, dst, n: int):
        """(the route from `src` to door `dst` that heats a bundle of `n` least, its heat).
        A road's and a junction pass's heat are their replays': fixed, so each search from a
        door is made once."""
        memo = self.__dict__.setdefault("_coolest_memo", {})
        dist = memo.get((src, n))
        if dist is None:
            dist = {src: (0.0, None)}
            pq = [(0.0, 0, src)]
            tie = 0
            while pq:
                d, _, u = heapq.heappop(pq)
                if d > dist[u][0]:
                    continue
                if u != src and not self.leaf[u[0]].master.name.startswith("junc"):
                    continue
                for v, _w, kind, info in self.adj.get(u, ()):
                    nd = d + self._hop_heat(kind, info, n)
                    if v not in dist or nd < dist[v][0] - 1e-12:
                        dist[v] = (nd, (u, kind, info))
                        tie += 1
                        heapq.heappush(pq, (nd, tie, v))
            memo[(src, n)] = dist
        if dst not in dist:
            raise CityError(f"no road from {src[0]}.{src[1]} to {dst[0]}.{dst[1]}")
        hops, v = [], dst
        while dist[v][1] is not None:
            u, kind, info = dist[v][1]
            hops.append((kind, info, u, v))
            v = u
        return list(reversed(hops)), dist[dst][0]

    def _plan(self, gids, src, dst):
        """The route a bundle takes: the quickest, unless it would arrive too hot for the
        place to take it under the ion-loss limit; then the coolest."""
        hops = self._route(src, dst)
        room = self.heat_limit - self._entry_heat(dst) - self._bundle_heat(gids)
        if self._route_heat(hops, len(gids)) <= room + 1e-9:
            return hops
        cool, q = self._coolest(src, dst, len(gids))
        return cool if q < self._route_heat(hops, len(gids)) else hops

    def _reachable(self, cands, near, port, gids):
        """The candidates a bundle at `near` can reach under the ion-loss limit (by its coolest
        road), else the one it reaches coolest."""
        if near is None or not gids or len(cands) < 2:
            return cands
        start = self._bundle_heat(gids)
        cost = {}
        for p in cands:
            try:
                cost[p] = start + self._coolest(near, (p, port), len(gids))[1] + self._entry_heat((p, port))
            except CityError:
                continue
        ok = [p for p in cands if p in cost and cost[p] <= self.heat_limit + 1e-9]
        return ok or ([min(cost, key=cost.get)] if cost else cands)

    def _choose(self, category: str, ins: Instr, near=None, port="in", free=None,
                ready: float = 0.0, also=(), gids=None):
        """Which place of `category` an instruction uses.  Sequential: the nearest to `near`.
        Parallel: the one where the op could start soonest -- when the place is free, or when
        the blocks could be there, whichever is later -- counting the travel of the blocks
        coming from `near` (and from each `(source, door)` in `also`), so a second bridge or
        factory takes work while the first is busy."""
        cands = [p for p in self.leaf if self.family(p) == category]
        if ins.at:
            cands = [p for p in cands if p == ins.at or p.split(".")[-1] == ins.at]
        if free:
            cands = [p for p in cands if free(p)]
        if not cands:
            raise CityError(f"the design has no {'free ' if free else ''}{category} place"
                            + (f" named {ins.at}" if ins.at else ""))
        if gids is not None:
            cands = self._reachable(cands, near, port, gids)
        if len(cands) == 1:
            return cands[0]
        if not self.parallel:
            if near is None:
                return sorted(cands)[0]
            return min(sorted(cands), key=lambda p: self._distance(near, (p, port)))

        def score(p):
            travel = [self._distance(near, (p, port))] if near is not None else []
            travel += [self._distance(src, (p, door)) for src, door in also]
            arrive = ready + max(travel or [0.0])
            return (max(self.cal[p].end, arrive) + sum(travel), p)
        return min(cands, key=score)

    # -- the classical half ---------------------------------------------------------------

    def _control(self):
        """Find the control places and index the wires by the port they hang on."""
        def one(family):
            got = sorted(p for p, li in self.leaf.items() if li.master.family == family)
            return got[0] if got else None

        self.decoder = one("decoder")
        self.archive = one("archive")
        # a port may hang on several wires (the archive's decision port fans out to every
        # place that can be told something), so this is one list per port
        self.wire_of: dict[tuple, list] = {}
        for w in self.wires.values():
            self.wire_of.setdefault(w.a, []).append((w.path, w.b, w.latency_us))
            self.wire_of.setdefault(w.b, []).append((w.path, w.a, w.latency_us))

    def message(self, src, t0, *, bits, signal, ins, grp, label="", event=-1, to=None):
        """Bits leaving `src` (a `(leaf, port)`) at `t0`, arriving one latency later.

        With `to`, the wire to that place is used -- a port that fans out has several."""
        ws = self.wire_of.get(src) or []
        if to is not None:
            ws = [w for w in ws if w[1][0] == to]
        if not ws:
            raise CityError(f"no wire on {src[0]}.{src[1]}"
                            + (f" to {to}" if to else "")
                            + f": the design does not carry {signal} bits there")
        path, dst, lat = ws[0]
        t1 = t0 + lat
        self.messages.append([0, path, src[0], src[1], dst[0], dst[1], round(t0, 3),
                              round(t1, 3), int(bits), signal, ins, grp, label, int(event)])
        return t1, dst

    def classical(self, path, opname, t_min, ins, grp):
        """One job at a classical place; returns (start, end, row)."""
        O = self.leaf[path].master.ops.get(opname)
        if O is None:
            raise CityError(f"{self.leaf[path].master.name} has no op {opname}")
        s = self.cal[path].earliest(t_min, O.duration_us)
        row = self.event(path, opname, s, s + O.duration_us, ins, grp)
        return s, s + O.duration_us, row

    def last_measure_us(self, mname, opname) -> float:
        """When the op's last measurement happened, in its own clock: the moment its
        syndrome bits exist.  Read from the replayed times, not assumed."""
        key = (mname, opname)
        cache = getattr(self, "_lastm", None)
        if cache is None:
            cache = self._lastm = {}
        if key not in cache:
            data = self.leaves.data[mname]
            prog = data["programs"].get(opname)
            when = {row[0]: row[2] for row in data["times"].get(opname, [])}
            cache[key] = max([when.get(i["id"], 0.0) for i in (prog or {}).get("instructions", [])
                              if i["type"] == "measure"] or [0.0])
        return cache[key]

    def frame_row(self, t, block, kind, text, *, ins, event, ax=ZERO, az=ZERO, why="",
                  guard=None, dax=None, daz=None, conj=None, copy=None):
        """One line of the archive.

        `ax`/`az` are the frame after the write (what the archive shows); `dax`/`daz` and
        `conj` are what this line *changed*, so that several lines on one block compose in
        time order -- which is what a branch has to do when a conditional S̄ and a
        conditional Pauli both land on the same block.  With `guard`, the line is written
        only in the branches where that condition holds."""
        row = {"id": len(self.frames), "t": round(t, 3), "block": block, "kind": kind,
               "instruction": ins, "event": event, "ax": expr_json(ax), "az": expr_json(az),
               "text": text, "why": why}
        if dax is not None or daz is not None or conj is not None or copy is not None:
            row["delta"] = {"ax": expr_json(dax or ZERO), "az": expr_json(daz or ZERO),
                            "conjugate": conj, "copy": dict(copy or {})}
        if guard is not None:
            row["guard"] = dict(guard)
        self.frames.append(row)

    def report(self, path, opname, row, ins, grp, *, blocks=(), outcome=None, correct=(),
               parts=None):
        """Send an op's classical output where it goes: the syndrome to the decoder, whose
        frame update lands in the archive, and any logical outcome to the archive.

        `outcome` is `(flow name, parity expression of the frame flip, cvar)`.  `parts`, for a
        product measured in pieces (a cat chain): `(path, opname, row, flow)` for every bit
        whose parity it is; each place sends its own bit, and the archive records the
        product when the last one is in."""
        for b in blocks:
            self.last_round[b] = max(self.last_round.get(b, 0.0), row[2])
        li = self.leaf[path]
        t_meas = row[1] + self.last_measure_us(li.master.name, opname)
        ports = {p.signal: p for p in li.master.ports if p.kind == "classical"}
        window = None
        if "syndrome" in ports and self.decoder and self.archive:
            # the syndrome stays in the place's readout buffer until the program calls the
            # decoder: a window is left, and `decode` is what sends it
            window = len(self.windows)
            self.windows.append({"id": window, "place": path, "op": opname, "event": row[0],
                                 "instruction": ins, "measured_us": round(t_meas, 3),
                                 "bits": ports["syndrome"].bits, "blocks": list(blocks),
                                 "decoded_by": None})
        if outcome is not None and self.archive:
            flow, flip, cvar = outcome
            res = ports.get("outcome")
            if res is None:
                raise CityError(f"{li.master.name} has no outcome port to report {flow}")
            if parts:
                t1, raw = 0.0, ZERO
                for ppath, pop, prow, pflow in parts:
                    tm = prow[1] + self.last_measure_us(self.leaf[ppath].master.name, pop)
                    nb = next((q.bits for q in self.leaf[ppath].master.ports
                               if q.kind == "classical" and q.signal == "outcome"), 1)
                    ta, _ = self.message((ppath, "res"), tm, bits=nb, signal="outcome", ins=ins,
                                         grp=grp, label=f"{cvar or flow}: {pflow}", event=prow[0])
                    t1, t_meas = max(t1, ta), max(t_meas, tm)
                    raw = xor(raw, outcome_of(prow[0], pflow))
            else:
                t1, _ = self.message((path, "res"), t_meas, bits=1, signal="outcome", ins=ins,
                                     grp=grp, label=cvar or flow, event=row[0])
                raw = outcome_of(row[0], flow)
            _, t_rec, rec = self.classical(self.archive, "record", t1, ins, grp)
            value = xor(raw, flip)
            self.values[(ins, flow)] = value
            self.outcomes.append({"instruction": ins, "event": row, "cvar": cvar,
                                  "flow": flow, "blocks": list(blocks),
                                  "flip": expr_json(flip), "value": expr_json(value),
                                  "correct": [list(c) for c in correct],
                                  "parts": [(prow, pflow) for _, _, prow, pflow in parts or ()],
                                  "at": round(t_meas, 3),
                                  "recorded": rec[0], "recorded_us": round(t_rec, 3)})
            if cvar:
                if cvar in self.guard_reads:
                    for block, _, _ in correct:
                        self.certain(block, f"the outcome {cvar}, which a guard reads,")
                if window is not None:
                    self.cvar_window[cvar] = window
                self.cvars[(ins, cvar)] = cvar
                self.cvar_value[cvar] = value
                self.cvar_time[cvar] = t_rec
            self.frame_row(t_rec, blocks[0] if blocks else None, "outcome",
                           f"{cvar or flow} = {expr_text(value)}", ins=ins, event=rec[0],
                           why="the outcome as the archive keeps it: the records the "
                               "gadget's flow report names, corrected by the block's frame")
            return value
        return None

    def decode(self, ins: Instr, grp: str, t0: float) -> None:
        """Call the decoder: send the windows the named blocks left, decode each, and write
        the frame updates into the archive.

        One window at a time, in the order they were measured: its bits leave the place that
        measured them down that place's syndrome wire (no earlier than they exist, and no
        earlier than the instruction), the decoder runs one job on them, and the frame it
        decides goes down the frame wire to the archive."""
        if not (self.decoder and self.archive):
            raise CityError("this design has no decoder to call")
        if ins.at and ins.at != self.decoder:
            raise CityError(f"{ins.at} is not the decoder ({self.decoder})")
        want = set(ins.blocks)
        for y, q in self.merged_into.items():
            if q in want:
                want.add(y)
        todo = [w for w in self.windows if w["decoded_by"] is None
                and (not ins.blocks or want & set(w["blocks"]))]
        if not todo and (not self.park_round or self.park_within > 1):
            # (a block that waited without a round -- `hold`, or not parked at all -- has no
            # syndrome for a decode written for the round its store would have measured: nothing
            # happens, and the GIR says so)
            self.noops.append([ins.index, "nothing to decode: the parks it was written for took no round"])
            return
        if not todo:
            said = " ".join(ins.blocks) or "the program"
            raise CityError(f"nothing to decode: every syndrome {said} has left is decoded "
                            f"already")
        if self.parallel:
            # one call: the program reaches `decode` once every window it names exists, and
            # sends them together -- not each the moment it was measured
            t0 = max([t0] + [w["measured_us"] for w in todo])
        for w in todo:
            t_send = max(t0, w["measured_us"])
            t1, _ = self.message((w["place"], "syn"), t_send, bits=w["bits"],
                                 signal="syndrome", ins=ins.index, grp=grp, event=w["event"],
                                 label=f"{w['bits']} check outcomes from {w['op']}")
            _, t_dec, dec = self.classical(self.decoder, "decode", t1, ins.index, grp)
            t2, _ = self.message((self.decoder, "frame"), t_dec, bits=2, signal="frame",
                                 ins=ins.index, grp=grp, label="Pauli-frame update",
                                 event=dec[0])
            _, t_up, up = self.classical(self.archive, "update", t2, ins.index, grp)
            for b in w["blocks"]:
                self.frame_row(t_up, b, "window", f"window correction for {b}",
                               ins=ins.index, event=up[0],
                               why=f"the decoder's update for the round {w['op']} measured; "
                                   f"the gadget's own report declares it as a frame, and "
                                   f"the sign-off allows exactly that")
            w.update(decoded_by=ins.index, sent_us=round(t_send, 3), decode_event=dec[0],
                     decoded_us=round(t_dec, 3), written_us=round(t_up, 3))

    def decoded_by(self, name: str, what: str) -> float:
        """When the outcome `name` became a result: recorded, and its window decoded.

        A logical outcome of a surface code is a parity of measured ions that the decoder
        corrects, so nothing may act on it before the decoder has.  If the program has not
        called the decoder on it, that is refused here, with the line it needs."""
        t = self.cvar_time[name]
        w = self.cvar_window.get(name)
        if w is None:
            return t
        win = self.windows[w]
        if win["decoded_by"] is None:
            blocks = " ".join(win["blocks"])
            raise CityError(f"{what} reads {name}, whose syndrome has not been decoded: a "
                            f"logical outcome is not a result until the decoder has corrected "
                            f"it -- add `decode {blocks}` before this line")
        return max(t, win["written_us"])

    # -- guards: a classically controlled op --------------------------------------------------

    def guard_row(self, ins, ready_us: float) -> dict | None:
        """What an event row says about being classically controlled."""
        c = self.cond_json(ins)
        if c is None:
            return None
        return {"guard": ins.guard, "cond": c, "ready_us": round(ready_us, 3),
                "branches": ["run", "skip"],
                "note": "the place is reserved either way; the pulses fire only when the "
                        "condition holds (a pre-compiled branch with the same port contract)"}

    def cond_json(self, ins) -> dict | None:
        if ins.cond is None:
            return None
        return ins.cond.json()

    def guard_at(self, ins, grp, path) -> float:
        """When the op at `path` may start: its guard resolved in the archive and delivered.

        The archive reads the outcomes the condition names (one `resolve`), and the answer
        travels down the decision wire to the place.  Places with no `ctl` port are told
        nothing, so a guarded op there could not be gated -- that is refused."""
        if ins.cond is None:
            return 0.0
        if not self.archive:
            raise CityError("this design has no classical memory, so no guard can be resolved")
        names = ins.cond.names
        missing = [n for n in names if n not in self.cvar_time]
        if missing:
            raise CityError(f"the guard reads {missing[0]}, which nothing has measured yet")
        if ins.index not in self.resolved:
            t_ready = max([self.decoded_by(n, "the guard") for n in names] or [0.0])
            _, t_end, row = self.classical(self.archive, "resolve", t_ready, ins.index, grp)
            # the outcomes the condition reads, as the archive holds them (a conjunction's
            # value is not a parity: this names the outcomes, the sign-off evaluates it)
            expr = ZERO if not (len(ins.cond.parities) == 1 and ins.cond.parities[0][0]) else ONE
            for n in names:
                expr = xor(expr, self.cvar_value[n])
            self.frame_row(t_end, None, "guard", f"guard ({ins.guard}) = {expr_text(expr)}",
                           ins=ins.index, event=row[0], az=expr,
                           why="the condition of a classically controlled op, read out of the "
                               "archive as a parity of logical outcomes")
            self.resolved[ins.index] = (t_end, row, {})
        t_end, row, at_place = self.resolved[ins.index]
        if path not in at_place:
            try:
                self.leaf[path].master.port("ctl")
            except KeyError:
                raise CityError(
                    f"{self.leaf[path].master.name} has no ctl port: it cannot be told "
                    f"whether to run, so this instruction cannot be guarded there") from None
            t_at, _ = self.message((self.archive, "ctl"), t_end, bits=1, signal="decision",
                                   ins=ins.index, grp=grp, event=row[0], to=path,
                                   label=f"guard ({ins.guard})")
            at_place[path] = t_at
        return at_place[path]

    # -- the Pauli frame --------------------------------------------------------------------

    def frame(self, block):
        return self.pf.get(block, (ZERO, ZERO))

    def flip_for(self, terms, what: str = "a measurement") -> tuple:
        """The frame's effect on a measurement, as the archive can evaluate it now.

        `terms` are `(block, qx, qz)` for each logical operator measured.  P = X^ax Z^az and
        Q = X^qx Z^qz anticommute when ax·qz ⊕ az·qx = 1, so the raw outcome is the true one
        XOR that parity.  A conditional correction makes some of that branch-dependent, and
        the sign-off recomputes it per branch from the archive's own lines; what this
        returns is the value with the corrections that are certain."""
        out = ZERO
        for block, qx, qz in terms:
            ax, az = self.frame(block)
            if qz:
                out = xor(out, ax)
            if qx:
                out = xor(out, az)
        return out

    def write_frame(self, block, t, *, ins, grp, text, why, dax=ZERO, daz=ZERO, conj=None,
                    copy=None, guard=None):
        """Change a block's frame in the archive (an `update` job), and say so.

        The change is `X̄^dax Z̄^daz`, applied after conjugating the frame through `conj`
        (only `"S"` so far: S̄ takes X̄ to Ȳ, so `az` takes `ax`).  A guarded write does not
        move the block's own frame here -- it depends on a bit measured while the machine
        runs, so the sign-off folds it in branch by branch."""
        ax, az = self.frame(block)
        if conj == "S":
            az = xor(az, ax)
        elif conj == "SX":
            ax = xor(ax, az)
        elif conj == "H":
            ax, az = az, ax
        for part, other in (copy or {}).items():           # a CNOT copies the other's frame
            oax, oaz = self.frame(other)
            if part == "ax":
                ax = xor(ax, oax)
            else:
                az = xor(az, oaz)
        ax, az = xor(ax, dax), xor(az, daz)
        if guard is None:
            self.pf[block] = (ax, az)
        else:
            self.pf_guarded.setdefault(block, []).append(guard)
        if not self.archive:
            return t
        _, t_end, row = self.classical(self.archive, "update", t, ins, grp)
        self.frame_row(t_end, block, "pauli", text, ins=ins, event=row[0], ax=ax, az=az,
                       why=why, guard=guard, dax=dax, daz=daz, conj=conj, copy=copy)
        return t_end

    def certain(self, block, what: str) -> None:
        """Note a frame that is only known once a branch is taken.

        A guarded frame update makes the block's frame depend on a bit measured while the
        machine runs; an outcome read through it -- one a guard then reads -- is corrected
        by the archive at run time, by a frame that is a product of measured bits, not a
        parity.  The archive computes it (it is a classical memory and processor); this
        scheduler's own expressions are the frame's unconditional part.  Every such use is
        listed in the GIR's `branch_frames`, and the flat sign-off, which folds frames as
        parities, says it cannot follow them rather than check something else
        (docs/ARCHITECTURE.md §6: such programs are checked compositionally)."""
        if self.pf_guarded.get(block):
            self.branch_frames.append({"block": block, "what": what,
                                       "after": self.pf_guarded[block][-1].get("text")})

    # -- rows -----------------------------------------------------------------------------

    def event(self, path, op, t0, t1, ins, group="", bind=None, guard=None):
        self.cal[path].reserve(t0, t1)
        # the id is stable from here on: frame and outcome expressions name events by it,
        # and `gir()` renumbers both together
        row = [len(self.events), round(t0, 3), round(t1, 3), path, op, 1, ins, group,
               bind or [], []]
        self._heat_through(path, op, bind or [])
        if guard is not None:
            # element 10: this op is classically controlled.  The place is reserved either
            # way; `ready_us` is when the bit was at its door, which G11 checks.
            row.append(dict(guard))
        self.events.append(row)
        return row

    def carry(self, net, src, deps, tau, gids, role, ins, group, takes, hold=None):
        t0 = min(deps)
        row = [0, net, src[0], src[1], round(t0, 3), [round(d - t0, 3) for d in deps],
               round(tau, 3), list(gids), role, ins, group, round(max(takes), 3),
               [round(k - t0, 3) for k in takes]]
        q = road_heat(self.leaves, self.nets[net].conveyor)
        for g in gids:
            self.heat[g] = self.heat.get(g, 0.0) + q
        if hold is not None:
            self._holds[id(row)] = hold
        g = self._net_group.get(net)
        if g is not None:
            # the plane is the road's while it works: from its first load to its last
            # delivery (its own compiled program); kept as `road_spans` for the composition
            # check (fold.check_composition)
            first, last = self._road_span(net, list(deps), hold)
            self._plane_take(g, first, last)
            self._spans[id(row)] = (round(first, 3), round(last, 3))
        self.carries.append(row)
        self.net_cal[net].reserve(t0, max(takes))
        return row

    # -- the two moves: an op at a place, a trip between places ----------------------------

    def op(self, path, opname, inputs, t_min, ins, grp, resting=None, guard=None):
        """Run `opname` at `path` once every input bundle's ions are at its doors.

        `inputs` maps a port to `(gids, arrivals, open trip)`; `resting` names ions already
        inside that are not residents (a block in a zone).  Returns the start time,
        `{port: (gids, departures)}` for every door the op sends ions out of, and the row."""
        li = self.leaf[path]
        O = li.master.ops.get(opname)
        if O is None:
            raise CityError(f"{li.master.name} has no op {opname}")
        s = t_min
        for port, (gids, arr, _) in inputs.items():
            tin = O.transfer(port, "in")
            if tin is None or tin.count != len(gids):
                raise CityError(f"{li.master.name}.{opname} takes "
                                f"{tin.count if tin else 0} ions on {port}, not {len(gids)}")
            s = max(s, max(a - i for a, i in zip(arr, tin.times_us)))
        for _ in range(100):
            s0 = s
            # a door's stub is driven by one side at a time: no ion is taken off it while the
            # road delivers another onto it (the road's own program says when)
            for port, (gids, arr, trip) in inputs.items():
                if trip is not None:
                    s = self._door_start(s, arr, O.transfer(port, "in").times_us)
            if self.parallel:
                # a place serves its instructions in the order they are issued, and sends a
                # bundle out of a door only once the road in front of it is clear of the last
                for tr in O.transfers:
                    if tr.dir == "out" and (path, tr.port) in self.door_net:
                        s = max(s, self.net_cal[self.door_net[(path, tr.port)]].end - tr.t_first_us)
                s = self._start(path, s, O.duration_us)
            else:
                s = self.cal[path].earliest(s, O.duration_us)
            s = self._plane_fit(self._place_group.get(path), s, O.duration_us)
            if s <= s0 + 1e-9:
                break
        self._plane_take(self._place_group.get(path), s, s + O.duration_us)
        local = dict(li.ids)
        visitors = []
        for name, gid in (resting or {}).items():
            local[name] = gid
            visitors.append([name, gid])
        for port, (gids, arr, trip) in inputs.items():
            tin = O.transfer(port, "in")
            for name, gid in zip(tin.ions, gids):
                local[name] = gid
                visitors.append([name, gid])
            if trip is not None:
                net, src, deps, tau, *hold = trip
                self.carry(net, src, deps, tau, gids, "data", ins, grp, hold=(hold or [None])[0],
                           takes=[s + i for i in tin.times_us])
        outputs = {}
        for tr in O.transfers:
            if tr.dir != "out":
                continue
            gids = []
            for name in tr.ions:
                if name not in local:                  # a depot's freshly loaded ion
                    if li.master.family != "depot":
                        raise CityError(f"{li.master.name}.{opname} sends out {name}, an "
                                        f"ion it never received")
                    local[name] = self.next_gid
                    visitors.append([name, self.next_gid])
                    self.next_gid += 1
                gids.append(local[name])
            outputs[tr.port] = (gids, [s + x for x in tr.times_us])
        row = self.event(path, opname, s, s + O.duration_us, ins, grp, bind=visitors,
                         guard=guard)
        return s, outputs, row

    def _start(self, path, t: float, dur: float) -> float:
        """When an op of `dur` may start at `path`, no earlier than `t`.  A junction and the
        classical places fit it into any gap; every other place serves in issue order."""
        fam = self.leaf[path].master.family
        if path in self.junction_paths or fam in ("decoder", "archive"):
            return self.cal[path].earliest(t, dur)
        return max(t, self.cal[path].end)

    @property
    def junction_paths(self) -> set:
        js = getattr(self, "_junctions", None)
        if js is None:
            js = self._junctions = {p for p, li in self.leaf.items()
                                    if li.master.name.startswith("junc")}
        return js

    def _train(self, jpath, a, b, opname, n: int):
        """A bundle of `n` crossing junction `jpath` from arm `a` to arm `b`: one ion at a
        time, each the junction's lone-ion pass (`pass.A.B.1`, its own TSIR), back to back as
        the road in front delivers them -- the rest of the bundle waits on the road's sites,
        never stacked on the arm's stub.  Returned as one op over the whole crossing, with
        each ion's in and out time, which is what a trip schedules."""
        O1 = self.leaf[jpath].master.ops.get(f"{opname}.1")
        if O1 is None:
            raise CityError(f"junction {jpath} has no lone-ion pass {opname}.1")
        tin1, tout1 = O1.transfer(a, "in"), O1.transfer(b, "out")
        d1 = O1.duration_us
        return _Train(op=f"{opname}.1", name=tin1.ions[0], each_us=d1, duration_us=n * d1,
                      tin=_Times([k * d1 + tin1.times_us[0] for k in range(n)]),
                      tout=_Times([k * d1 + tout1.times_us[0] for k in range(n)]))

    def _passes(self, jpath, O, s: float, gids, ins, grp) -> None:
        """The train's events: one lone-ion pass per ion, back to back from `s`."""
        self._plane_take(self._place_group.get(jpath), s, s + O.duration_us)
        for k, g in enumerate(gids):
            t0 = s + k * O.each_us
            self.event(jpath, O.op, t0, t0 + O.each_us, ins, grp, bind=[[O.name, g]])

    # -- a road's own program ---------------------------------------------------------------

    def _road(self, net, deps, not_before=None) -> list:
        return self._road_hold(net, deps, not_before)[0]

    def _road_hold(self, net, deps, not_before=None) -> tuple:
        """(arrivals, the hold it took): `_road`, and the time the road could first load
        when it had to wait -- for this trip's last road on its plane, or for its plane to be
        free -- which the carry records (`road_holds`) so H1 compiles the very same program."""
        """When each ion of a bundle put on road `net`'s near stub at `deps` reaches its far
        stub: the road's own compiled program (`roads.compile_carry`), one class a cycle, the
        bundle's ions sharing the conveyor -- not one ion's transit added to each departure.
        The near side puts an ion on the stub at each `deps`; the road loads none while it
        does.  Memoized by shape."""
        from .roads import compile_carry, road_cycle_us
        L = self.nets[net].conveyor
        t0 = min(deps)
        rel = tuple(round(d - t0, 3) for d in deps)
        memo = self.__dict__.setdefault("_road_memo", {})
        g = self._net_group.get(net)
        for _ in range(1000):
            nb = None if not_before is None or not_before <= t0 else round(not_before - t0, 3)
            if (L, rel, nb) not in memo:
                put = road_cycle_us(L).get("port_in", 5.0)
                busy = [(d - put, d) for d in rel] + ([(-1e12, nb)] if nb is not None else [])
                run = compile_carry(L, list(rel), [float("inf")] * len(rel),
                                    near_busy=busy, verify_rules=False)
                memo[(L, rel, nb)] = (run.arrived, run.loaded[0])
            arrived, first = memo[(L, rel, nb)]
            if g is None:
                break
            # on a shared plane the road works only while the plane is free: wait for a gap
            a, z = t0 + first, t0 + max(arrived)
            free = self._plane_fit(g, a, z - a)
            if free <= a + 1e-9:
                break
            not_before = free
        eff = None if not_before is None or not_before <= t0 + 1e-9 else round(not_before, 3)
        return [t0 + a for a in arrived], eff

    def _road_span(self, net, deps, not_before=None) -> tuple:
        """(first load, last delivery) of a bundle on road `net`: what its plane holds."""
        arrived = self._road(net, deps, not_before)
        t0 = min(deps)
        L = self.nets[net].conveyor
        rel = tuple(round(d - t0, 3) for d in deps)
        nb = None if not_before is None or not_before <= t0 else round(not_before - t0, 3)
        hit = self._road_memo.get((L, rel, nb))
        first = t0 + hit[1] if hit else max([t0] + ([not_before] if not_before else []))
        return first, max(arrived)

    def _door_start(self, s: float, arrived, tin) -> float:
        """The earliest start no earlier than `s` at which an op taking a bundle in at
        `s + tin[k]` takes every ion only once it is on its stub, and never while the road is
        delivering another onto that stub: a door's stub is driven by one side at a time."""
        from .roads import road_cycle_us
        d = road_cycle_us(1).get("port_in", 5.0)
        for _ in range(1000):
            bump = s
            for k, i in enumerate(tin):
                take = s + i
                if arrived[k] > take + 1e-9:
                    bump = max(bump, arrived[k] - i)
                for j, a in enumerate(arrived):
                    if j != k and a - d < take + d - 1e-9 and take < a - 1e-9:
                        bump = max(bump, a - i)
            if bump <= s + 1e-9:
                return s
            s = bump
        raise CityError("a door's two sides never agree on when to hand a bundle over")

    def trip(self, gids, src, deps, dst, t_min, ins, grp):
        """Take a bundle leaving `src` at `deps` to door `dst`: junction passes are scheduled
        on the way; returns (arrival times at `dst`, the last channel, still open).  The open
        road is the plane's already (if it is on one), so the next trip planned -- before the
        op at `dst` takes this bundle in -- sees it."""
        t, trip = self._trip(gids, src, deps, dst, t_min, ins, grp)
        if trip is not None and trip[0] in self._net_group:
            first, last = self._road_span(trip[0], list(trip[2]), trip[4])
            self._plane_take(self._net_group[trip[0]], first, last)
        return t, trip

    def _trip(self, gids, src, deps, dst, t_min, ins, grp):
        if self.parallel:
            return self._wave(gids, src, deps, dst, t_min, ins, grp)
        t_min = max(t_min, self.road_free)
        t = list(deps)
        trip = None
        done: dict = {}           # a shared plane's group -> when this trip's last activity on it ends
        for kind, info, u, _v in self._plan(gids, src, dst):
            if kind == "net":
                tau = self.leaves.road_timing(self.nets[info].conveyor, 1)["transit_us"]
                g = self._net_group.get(info)
                deps0 = list(t)
                t, nb = self._road_hold(info, t, not_before=done.get(g))
                trip = (info, u, deps0, tau, nb)
                if g is not None:
                    done[g] = max(t)
            else:
                jpath, a, b, opname = info
                O = self._train(jpath, a, b, opname, len(gids))
                tin, tout = O.tin, O.tout
                s = max(t_min, max(x - i for x, i in zip(t, tin.times_us)))
                gj = self._place_group.get(jpath)
                if gj is not None and gj in done:
                    s = max(s, done[gj])
                for _ in range(100):
                    s0 = s
                    s = self._door_start(s, t, tin.times_us)
                    s = self.cal[jpath].earliest(s, O.duration_us)
                    s = self._plane_fit(gj, s, O.duration_us)
                    if s <= s0 + 1e-9:
                        break
                net, usrc, udeps, tau, nb = trip
                self.carry(net, usrc, udeps, tau, gids, "data", ins, grp,
                           takes=[s + i for i in tin.times_us], hold=nb)
                if gj is not None:
                    done[gj] = s + O.duration_us
                self._passes(jpath, O, s, gids, ins, grp)
                t = [s + x for x in tout.times_us]
                trip = None
        self.road_free = max([self.road_free] + list(t))
        return t, trip

    def _wave(self, gids, src, deps, dst, t_min, ins, grp):
        """A trip as a green wave.  The bundle leaves `src` onto the road in front of it and
        may wait there; from the first junction on it never stops until the road in front of
        `dst`, where it may wait again.  Every junction and road between is leased to it for
        exactly the time it is there; if any is taken, the whole wave starts later."""
        hops = self._plan(gids, src, dst)
        nets = [h for h in hops if h[0] == "net"]
        juncs = [h for h in hops if h[0] == "junction"]
        taus = [self.leaves.road_timing(self.nets[h[1]].conveyor, 1)["transit_us"] for h in nets]
        if not juncs:
            net = nets[0][1]
            arr, nb = self._road_hold(net, deps)
            return arr, (net, nets[0][2], list(deps), taus[0], nb)
        passes = []
        for h in juncs:
            jpath, a, b, opname = h[1]
            O = self._train(jpath, a, b, opname, len(gids))
            passes.append((jpath, O.op, O, O.tin, O.tout))
        arr0, hold0 = self._road_hold(nets[0][1], deps)
        s1 = max([t_min] + [x - i for x, i in zip(arr0, passes[0][3].times_us)])
        s1 = self._door_start(s1, arr0, passes[0][3].times_us)
        group = [self._net_group.get(h[1]) for h in nets]
        jgroup = [self._place_group.get(p[0]) for p in passes]

        def lay(s1):
            """Every junction's start, every road's hold, from the first junction's start:
            within this trip, an activity on a shared plane begins once the trip's last one on
            that plane is over (store-and-forward), and every take follows its delivery."""
            last: dict = {}
            if group[0] is not None:
                last[group[0]] = max(arr0)
            holds = [hold0]
            starts = []
            arr = arr0
            for k in range(len(passes)):
                tin_k = passes[k][3].times_us
                if k == 0:
                    sk = s1
                else:
                    outs = [starts[-1] + x for x in passes[k - 1][4].times_us]
                    arr, eff = self._road_hold(nets[k][1], outs, not_before=last.get(group[k]))
                    holds.append(eff)
                    if group[k] is not None:
                        last[group[k]] = max(arr)
                    sk = self._door_start(max(a - i for a, i in zip(arr, tin_k)), arr, tin_k)
                if jgroup[k] is not None and jgroup[k] in last:
                    sk = self._door_start(max(sk, last[jgroup[k]]), arr, tin_k)
                starts.append(sk)
                if jgroup[k] is not None:
                    last[jgroup[k]] = sk + passes[k][2].duration_us
            holds.append(last.get(group[len(passes)]))
            return starts, holds
        for _ in range(100000):
            starts, holds = lay(s1)
            shift = 0.0
            for k, (jpath, _opname, O, _tin, tout) in enumerate(passes):
                a = starts[k]
                got = self.cal[jpath].earliest(a, O.duration_us)
                got = max(got, self._plane_fit(jgroup[k], a, O.duration_us))
                shift = max(shift, got - a)
                if k + 1 < len(passes):
                    a = starts[k] + min(tout.times_us)
                    b = starts[k + 1] + max(passes[k + 1][3].times_us)
                    got = self.net_cal[nets[k + 1][1]].earliest(a, b - a)
                    shift = max(shift, got - a)
            # the road in front of `dst`: the bundle may wait on it, so nothing may be booked
            # on it after the bundle enters
            a = starts[-1] + min(passes[-1][4].times_us)
            shift = max(shift, self.net_cal[nets[-1][1]].end - a)
            if shift <= 1e-9:
                break
            s1 += shift
        else:
            raise CityError(f"no time at which the road from {src[0]} to {dst[0]} is clear")
        trip = (nets[0][1], nets[0][2], list(deps), taus[0], holds[0])
        t = list(deps)
        for k, (jpath, opname, O, tin, tout) in enumerate(passes):
            net, usrc, udeps, tau, nb = trip
            self.carry(net, usrc, udeps, tau, gids, "data", ins, grp,
                       takes=[starts[k] + i for i in tin.times_us], hold=nb)
            self._passes(jpath, O, starts[k], gids, ins, grp)
            outs = [starts[k] + x for x in tout.times_us]
            t, nb = self._road_hold(nets[k + 1][1], outs, not_before=holds[k + 1])
            trip = (nets[k + 1][1], nets[k + 1][2], outs, taus[k + 1], nb)
        return t, trip

    def store_op(self, Z: str, q: str, t: float, round_: bool | None = None) -> str:
        """How block `q` parks in memory `Z` at `t`: `store` (with a syndrome round) or, under
        `park_round=False`, `hold` -- unless its last round is ROUND_DUE_US behind it."""
        rounds = self.park_round if round_ is None else round_
        if not rounds and t - self.last_round.get(q, 0.0) >= ROUND_DUE_US:
            rounds = True
        return "store" if rounds or "hold" not in self.leaf[Z].master.ops else "hold"

    def _moves_between(self, a: int, b: int) -> int:
        """How many instructions that move ions come strictly between instructions a and b."""
        if not hasattr(self, "_qrank"):
            self._qrank, r = {}, 0
            for i in self.alg.instructions:
                self._qrank[i.index] = r
                if i.kind not in CLASSICAL:
                    r += 1
        return self._qrank[b] - self._qrank[a] - 1

    def _lead(self, q: str, t0: float) -> float:
        """When block `q` may start out for an op the other operands are ready for at `t0`:
        a block resting in a memory or a zone that early, less its release (`prefetch`)."""
        b = self.blocks[q]
        if not (self.parallel and b.resting):
            return t0
        out_op = "release" if self.family(b.leaf) == "memory" else "check_out"
        return max(self.block_free.get(q, 0.0), t0 - self.leaf[b.leaf].master.ops[out_op].duration_us)

    def bring(self, bundle: Bundle, dst, t_min, ins, grp, *, stop_ok: bool = True):
        """A block to door `dst`: out of its zone first if it rests in one.  If no road takes
        it there under the ion-loss limit, it is recooled on the way: stored in a free memory
        (whose lasers cool it) and released again -- a cooling stop (`_cooling_stop`)."""
        if bundle.resting:
            Z = bundle.leaf
            lease = getattr(bundle, "lease", None)
            if lease is not None:
                # the zone was the block's from check-in on, open-ended; it is the block's
                # until it checks out, and free after
                self.cal[Z].iv.remove(lease)
                bundle.lease = None
            out_op = "release" if self.family(Z) == "memory" else "check_out"
            O = self.leaf[Z].master.ops[out_op]
            names = O.transfer("out", "out").ions
            if self.parallel and self.family(dst[0]) not in ("zone", "memory"):
                # just in time: a block resting where it is corrected leaves no earlier than
                # the place it goes to can take it, less the trip -- waiting on a road, it
                # would be corrected by nobody
                trip_us = self._distance((Z, "out"), dst)
                busy = self.cal[dst[0]].end if dst[0] not in self.junction_paths else 0.0
                t_min = max(t_min, busy - trip_us - O.duration_us)
            s, outs, _ = self.op(Z, out_op, {}, max(t_min, self.mem_last.pop(Z, 0.0)), ins, grp,
                                 resting=dict(zip(names, bundle.gids)))
            if lease is not None:
                self.cal[Z].reserve(lease[0], s)
            bundle.deps = outs["out"][1]
            bundle.leaf, bundle.port = Z, "out"
            bundle.resting = False
            self.zone_holder[Z] = None
        if stop_ok and self._arrives_too_hot(bundle.gids, (bundle.leaf, bundle.port), dst):
            M = self._cooling_stop(bundle, dst)
            if M is not None and self.family(M) == "memory":
                self.park(bundle, t_min, ins, grp, at=M, stop_ok=False)
                return self.bring(bundle, dst, t_min, ins, grp, stop_ok=False)
            if M is not None:
                # a clinic: one syndrome round under its lasers, reported like any other
                gids, arr, trip = self.bring(bundle, (M, "in"), t_min, ins, grp, stop_ok=False)
                _s, outs, row = self.op(M, "se.1", {"in": (gids, arr, trip)}, t_min, ins, grp)
                self.report(M, "se.1", row, ins, grp, blocks=[bundle.name])
                bundle.gids, bundle.deps = outs["out"][0], outs["out"][1]
                bundle.leaf, bundle.port = M, "out"
                return self.bring(bundle, dst, t_min, ins, grp, stop_ok=False)
        arr, trip = self.trip(bundle.gids, (bundle.leaf, bundle.port), bundle.deps, dst,
                              t_min, ins, grp)
        return bundle.gids, arr, trip

    def _exit_heat(self, place, opname) -> float:
        hs = op_heat(self.leaves, self.leaf[place].master, opname)
        return max((h.out for h in hs.values() if h.leaves), default=0.0) if hs else 0.0

    def _arrives_too_hot(self, gids, src, dst) -> bool:
        hops = self._plan(gids, src, dst)
        return (self._bundle_heat(gids) + self._route_heat(hops, len(gids)) + self._entry_heat(dst)
                > self.heat_limit + 1e-9)

    def _cooling_stop(self, bundle: Bundle, dst):
        """Where to recool `bundle` on its way to `dst`: a free memory (stored and released,
        its lasers cool it) or a clinic (one syndrome round), that it reaches under the
        ion-loss limit and that sends it on to reach `dst` under it; the quickest such
        detour.  None if there is none (the trip then goes as it is, and G6 says so)."""
        src = (bundle.leaf, bundle.port)
        n, start = len(bundle.gids), self._bundle_heat(bundle.gids)
        best = None
        stops = [(M, "release") for M, holder in self.zone_holder.items()
                 if holder is None and self.family(M) == "memory" and M not in getattr(self, "no_rest", ())]
        stops += [(C, "se.1") for C, li in self.leaf.items()
                  if li.master.family == "se" and "se.1" in li.master.ops]
        for M, out_op in stops:
            try:
                there = start + self._coolest(src, (M, "in"), n)[1] + self._entry_heat((M, "in"))
                on = (self._exit_heat(M, out_op) + self._coolest((M, "out"), dst, n)[1]
                      + self._entry_heat(dst))
            except CityError:
                continue
            if there <= self.heat_limit + 1e-9 and on <= self.heat_limit + 1e-9:
                key = (self._distance(src, (M, "in")) + self._distance((M, "out"), dst), M)
                if best is None or key < best:
                    best = key
        return None if best is None else best[1]

    def park(self, bundle: Bundle, t_min, ins, grp, *, at=None, stop_ok: bool = True,
             round_: bool | None = None):
        """Rest a block until its next use: in a free memory if the design has one (it will
        be corrected while it rests), else in a free logical zone.  `at`: this memory (a
        cooling stop).  `round_`: whether storing it takes a syndrome round (None: `park_round`)."""
        near = (bundle.leaf, bundle.port)
        if at is not None:
            Z, kind = at, self.family(at)
        else:
            mems = [p for p, h in self.zone_holder.items()
                    if h is None and self.family(p) == "memory"]
            kind = "memory" if mems else "zone"
            Z = self._choose(kind, _ANY, near=near, ready=t_min,
                             free=lambda p: self.zone_holder[p] is None, gids=bundle.gids)
        in_op = "check_in"
        if kind == "memory":
            in_op = self.store_op(Z, bundle.name, t_min, round_)
        gids, arr, trip = self.bring(bundle, (Z, "in"), t_min, ins, grp, stop_ok=stop_ok)
        s, _, row = self.op(Z, in_op, {"in": (gids, arr, trip)}, t_min, ins, grp)
        if kind == "memory":
            self.mem_last[Z] = row[2]
            if in_op == "store":
                self.report(Z, in_op, row, ins, grp, blocks=[bundle.name])
        bundle.leaf, bundle.port, bundle.deps = Z, "in", []
        bundle.resting = True
        self.zone_holder[Z] = bundle.name
        if self.parallel:
            # the zone is the block's from now until it checks out: an open lease
            bundle.lease = (row[1], float("inf"))
            self.cal[Z].reserve(*bundle.lease)
        return row[2]

    # -- instructions ----------------------------------------------------------------------

    def run(self):
        for ins in self.alg.instructions:
            try:
                self.instruction(ins)
            except CityError as exc:
                self.refused.append([ins.index, str(exc)])
                self.log(f"  instruction {ins.index} refused: {exc}")
        return self.gir()

    def cat_merge(self, ins: Instr, grp: str, t0: float, produced: dict) -> None:
        """A product of one X̄ or Z̄ from each of many blocks, by cat states (the user's
        choice, 2026-10-29, for Gidney-Ekera's register-wide merges).

        Every block goes to a cat bench of its own, the nearest free one.  The cats are made in
        parallel, so the merge takes the same time over any number of blocks: every bench
        opens its probes in |+> (`cat.open`), and each pair of neighbouring benches is fused by
        a bundle of its own fresh links -- lent a copy of the left probes' Z (`cat.lend`), read
        against the right ones' (`cat.check`), then back to a depot.  The probes, once fused,
        take a controlled-P̄ with their own block and are read in X (`cat.couple.P`); the blocks
        travel while the cats are made.  The product is the parity of round 0's probe bits
        (rounds 1 and 2 agree with it unless a fault struck).  A check that read 1 left the
        probes to its right flipped by an X, which their couplings carried onto their blocks as
        a P̄: the archive writes it into those blocks' frames, and no one waits for it."""
        from .leaves.catbench import ROUNDS, check_flow, probe_flow
        blks, n = list(ins.blocks), len(ins.blocks)
        if ins.cond is not None:
            raise CityError("a product by cat states cannot be guarded (its benches have no "
                            "skip branch)")
        benches = [p for p in self.leaf if self.family(p) == "catbench"]
        if n < 2 or len(benches) < n:
            raise CityError(f"a product over {n} block{'s' if n != 1 else ''} needs "
                            + ("a Pauli-product bench" if n < 2 else
                               f"a Pauli-product bench (up to {PPM_MAX} blocks) or {n} cat "
                               f"benches; the design has {len(benches)}"))
        sinks = [p for p in self.leaf if self.family(p) == "depot"
                 and f"dump.{ROUNDS}" in self.leaf[p].master.ops]
        if not sinks:
            raise CityError(f"cat states take their links from, and give them back to, a depot "
                            f"that sends and takes {ROUNDS} ions (depot9x)")
        bench_of: dict[str, str] = {}
        for b in blks:
            bench_of[b] = self._choose("catbench", ins, near=(self.blocks[b].leaf, self.blocks[b].port),
                                       port="in_a", ready=t0,
                                       free=lambda p: p not in bench_of.values())
        # neighbours: each bench next to the nearest one not yet placed, from the first block's
        left = [bench_of[b] for b in blks]
        chain = [left.pop(0)]
        while left:
            nxt = min(left, key=lambda p: (self._distance((chain[-1], "out_l"), (p, "in_l")), p))
            left.remove(nxt)
            chain.append(nxt)
        # every bench opens, then every left bench lends, then every right bench checks: each
        # place serves in issue order, so issuing them in this order lets all pairs run at once
        for B in chain:
            self.op(B, "cat.open", {}, t0, ins.index, grp)
        lent = []
        for k in range(n - 1):
            L = chain[k]
            D, g, d = self._load(ROUNDS, (L, "in_l"), t0, ins.index, grp)
            arr, trip = self.trip(g, (D, "out"), d, (L, "in_l"), t0, ins.index, grp)
            _, outs, _ = self.op(L, "cat.lend", {"in_l": (g, arr, trip)}, t0, ins.index, grp)
            lent.append(outs["out_l"])
        z, t_bits = [], 0.0
        for k in range(n - 1):
            L, R = chain[k], chain[k + 1]
            g, d = lent[k]
            arr, trip = self.trip(g, (L, "out_l"), d, (R, "in_l"), t0, ins.index, grp)
            _, outs, row = self.op(R, "cat.check", {"in_l": (g, arr, trip)}, t0, ins.index, grp)
            z.append(xor(*(outcome_of(row[0], check_flow(r)) for r in range(ROUNDS))))
            # the parity bits go to the archive, which keeps the flips they mean
            tm = row[1] + self.last_measure_us(self.leaf[R].master.name, "cat.check")
            if self.archive:
                t1, _ = self.message((R, "res"), tm, bits=ROUNDS, signal="outcome", ins=ins.index,
                                     grp=grp, label=f"cat parity {k}", event=row[0])
                _, t_rec, _ = self.classical(self.archive, "record", t1, ins.index, grp)
                t_bits = max(t_bits, t_rec)
            g, d = outs["out_l"]
            Dx = self._choose("depot", _ANY, near=(R, "out_l"), port="in", gids=g,
                              free=lambda p: p in sinks)
            arr, trip = self.trip(g, (R, "out_l"), d, (Dx, "in"), t0, ins.index, grp)
            self.op(Dx, f"dump.{ROUNDS}", {"in": (g, arr, trip)}, t0, ins.index, grp)
        terms = [(b, 1 if p == "X" else 0, 1 if p == "Z" else 0) for b, p in zip(blks, ins.paulis)]
        flip = self.flip_for(terms)
        parts, ends = [], {}
        for b, p in zip(blks, ins.paulis):
            B = bench_of[b]
            gids, arr, trip = self.bring(self.blocks[b], (B, "in_a"), t0, ins.index, grp)
            opname = f"cat.couple.{p}"
            s, outs, row = self.op(B, opname, {"in_a": (gids, arr, trip)}, t0, ins.index, grp)
            produced[b] = Bundle(b, outs["out_a"][0], B, "out_a", outs["out_a"][1])
            parts.append((B, opname, row, probe_flow(0, p)))
            ends[b] = row[2]
        last = max(parts, key=lambda x: x[2][1])
        self.report(last[0], last[1], last[2], ins.index, grp, blocks=blks, correct=terms,
                    parts=parts,
                    outcome=(" ".join(f"{p}̄_{b}" for b, p in zip(blks, ins.paulis))
                             + " measured by cat states", flip, ins.cvar))
        # the flips the checks left: the bench at place j of the chain is flipped by the parity
        # of every check to its left, in every round, and its block takes that as a P̄
        acc = ZERO
        flipped = {}
        for j, B in enumerate(chain):
            flipped[B] = acc
            if j < n - 1:
                acc = xor(acc, z[j])
        for b, p in zip(blks, ins.paulis):
            x = flipped[bench_of[b]]
            if x == ZERO:
                continue
            self.write_frame(b, max(ends[b], t_bits), ins=ins.index, grp=grp,
                             dax=x if p == "X" else ZERO, daz=x if p == "Z" else ZERO,
                             text=f"{p}̄ on {b} where the cat checks to its left read odd",
                             why="a parity check that reads 1 fuses two cats up to an X on every "
                                 "probe to its right; the probe's controlled-P̄ carries it onto "
                                 "the block as P̄, a frame the archive keeps")

    def _load(self, k, near_door, t_min, ins, grp):
        D = self._choose("depot", _ANY, near=near_door, port="out",
                         free=lambda p: f"load.{k}" in self.leaf[p].master.ops)
        s, outs, row = self.op(D, f"load.{k}", {}, t_min, ins, grp)
        self.stock[D] += k
        gids, deps = outs["out"]
        return D, gids, deps

    #: the instructions a guard can gate: a place that can be told (`ctl`), or the archive
    GUARDABLE = ("cx", "zz", "xx", "ppm", "read", "s", "sx", "x", "y", "z", "ht")

    def instruction(self, ins: Instr):
        grp = f"i{ins.index}"
        # sequential: everything before has finished.  Parallel: the blocks this instruction
        # acts on are free; everything else is a resource, and its calendar says when.
        t0 = self.t_now if not self.parallel else max(
            [self.block_free.get(b, 0.0) for b in ins.blocks if ins.kind != "decode"] or [0.0])
        if ins.not_before is not None:
            t0 = max(t0, ins.not_before)
        before = len(self.events)
        before_c, before_m, before_f = len(self.carries), len(self.messages), len(self.frames)
        kind = ins.kind
        if ins.cond is not None and kind not in self.GUARDABLE:
            raise CityError(f"'{kind}' cannot be guarded: only {', '.join(self.GUARDABLE)} "
                            f"run at a place that can be told whether to fire (a ctl port) "
                            f"or in the archive")
        produced: dict[str, Bundle] = {}
        if kind == "prep" and ins.basis == "Y":
            # |Ȳ⟩ is made where S|+⟩ is injected: the pharmacy's own verified op (distance 1)
            q = ins.blocks[0]
            I = self._choose("injection", ins, ready=t0)
            D1, g1, d1 = self._load(1, (I, "magic"), t0, ins.index, grp)
            marr, mtrip = self.trip(g1, (D1, "out"), d1, (I, "magic"), t0, ins.index, grp)
            D2, g8, d8 = self._load(8, (I, "in"), t0, ins.index, grp)
            farr, ftrip = self.trip(g8, (D2, "out"), d8, (I, "in"), t0, ins.index, grp)
            s, outs, row = self.op(I, "inject.Y", {"magic": (g1, marr, mtrip),
                                                   "in": (g8, farr, ftrip)}, t0, ins.index, grp)
            produced[q] = Bundle(q, outs["out"][0], I, "out", outs["out"][1])
            self.block_info[q] = {"ions": outs["out"][0], "born": row[1], "born_at": I,
                                  "code": "surface_d3", "n": 9, "k": 1, "resource": "|Ȳ⟩"}
            self.pf[q] = (ZERO, ZERO)
            self.report(I, "inject.Y", row, ins.index, grp, blocks=[q])
        elif kind == "prep" and not rotation_basis(ins.basis):
            P = self._choose("prep", ins, ready=t0)
            D, gids, deps = self._load(9, (P, "in"), t0, ins.index, grp)
            arr, trip = self.trip(gids, (D, "out"), deps, (P, "in"), t0, ins.index, grp)
            s, outs, row = self.op(P, ins.op, {"in": (gids, arr, trip)}, t0, ins.index, grp)
            produced[ins.blocks[0]] = Bundle(ins.blocks[0], outs["out"][0], P, "out", outs["out"][1])
            self.block_info[ins.blocks[0]] = {"ions": outs["out"][0], "born": row[1],
                                              "born_at": P, "code": "surface_d3", "n": 9, "k": 1}
            self.pf[ins.blocks[0]] = (ZERO, ZERO)
            self.report(P, ins.op, row, ins.index, grp, blocks=[ins.blocks[0]])
        elif kind == "inject" or (kind == "prep" and rotation_basis(ins.basis)):
            # a magic block: one ion made in a state no Clifford makes -- T†|+⟩ by the factory's
            # distillation, or RZn|+⟩ by a rotation source -- and encoded by a pharmacy
            rot = rotation_basis(ins.basis) if kind == "prep" else None
            F = self._choose("rotation" if rot else "factory", ins, ready=t0)
            I = self._choose("injection", ins, ready=t0, near=(F, "out"), port="magic")
            D1, g1, d1 = self._load(1, (F, "in"), t0, ins.index, grp)
            arr, trip = self.trip(g1, (D1, "out"), d1, (F, "in"), t0, ins.index, grp)
            make = f"rz{rot[0]}{'dg' if rot[1] else ''}" if rot else "produce"
            s, outs, _ = self.op(F, make, {"in": (g1, arr, trip)}, t0, ins.index, grp)
            mg, md = outs["out"]
            marr, mtrip = self.trip(mg, (F, "out"), md, (I, "magic"), t0, ins.index, grp)
            D2, g8, d8 = self._load(8, (I, "in"), t0, ins.index, grp)
            farr, ftrip = self.trip(g8, (D2, "out"), d8, (I, "in"), t0, ins.index, grp)
            s, outs, row = self.op(I, "inject", {"magic": (mg, marr, mtrip),
                                                 "in": (g8, farr, ftrip)}, t0, ins.index, grp)
            produced[ins.blocks[0]] = Bundle(ins.blocks[0], outs["out"][0], I, "out", outs["out"][1])
            self.block_info[ins.blocks[0]] = {"ions": outs["out"][0], "born": row[1],
                                              "born_at": I, "code": "surface_d3", "n": 9, "k": 1,
                                              "magic_ion": mg[0]}
            if rot:
                self.block_info[ins.blocks[0]]["resource"] = f"RZ{rot[0]}{'†' if rot[1] else ''}|+⟩"
            self.pf[ins.blocks[0]] = (ZERO, ZERO)
            self.report(I, "inject", row, ins.index, grp, blocks=[ins.blocks[0]])
        elif kind == "autoccz":
            # one |AutoCCZ> state: nine fresh ions through the CCZ factory, then each of its
            # nine output ions encoded by a pharmacy into its own block
            F = self._choose("cczfactory", ins, ready=t0)
            D1, g9, d9 = self._load(9, (F, "in"), t0, ins.index, grp)
            arr, trip = self.trip(g9, (D1, "out"), d9, (F, "in"), t0, ins.index, grp)
            s, outs, frow = self.op(F, "produce", {"in": (g9, arr, trip)}, t0, ins.index, grp)
            mg, md = outs["out"]
            for j, q in enumerate(ins.blocks):
                I = self._choose("injection", ins, ready=t0, near=(F, "out"), port="magic")
                marr, mtrip = self.trip([mg[j]], (F, "out"), [md[j]], (I, "magic"), t0, ins.index, grp)
                D2, g8, d8 = self._load(8, (I, "in"), t0, ins.index, grp)
                farr, ftrip = self.trip(g8, (D2, "out"), d8, (I, "in"), t0, ins.index, grp)
                s, o2, row = self.op(I, "inject", {"magic": ([mg[j]], marr, mtrip),
                                                    "in": (g8, farr, ftrip)}, t0, ins.index, grp)
                produced[q] = Bundle(q, o2["out"][0], I, "out", o2["out"][1])
                self.block_info[q] = {"ions": o2["out"][0], "born": row[1], "born_at": I,
                                      "code": "surface_d3", "n": 9, "k": 1, "magic_ion": mg[j],
                                      "resource": f"|AutoCCZ> site {j}"}
                self.pf[q] = (ZERO, ZERO)
                self.report(I, "inject", row, ins.index, grp, blocks=[q])
        elif kind in ("se", "store"):
            q = ins.blocks[0]
            b = self.blocks[q]
            if kind == "store":
                if b.resting:
                    return
                end = self.park(b, t0, ins.index, grp)
                self.t_now = max(self.t_now, end)
                self.block_free[q] = max(self.block_free.get(q, 0.0), end)
                return
            if self.rounds_at == "memory":
                # every round in the block's memory: storing it there is the first round, and
                # each refresh one more (no trip to a clinic, no queue behind other blocks)
                n = ins.rounds
                if not (b.resting and self.family(b.leaf) == "memory"):
                    end = self.park(b, t0, ins.index, grp, round_=True)
                    self.block_free[q] = max(self.block_free.get(q, 0.0), end)
                    n -= 1
                    b = self.blocks[q]
                if not (b.resting and self.family(b.leaf) == "memory"):
                    raise CityError(f"no memory free for {q} to take its syndrome rounds in")
                for _ in range(n):
                    self.refresh_in_place(ins, grp, t0, q, b)
                return
            if b.resting and self.family(b.leaf) == "memory":
                self.refresh_in_place(ins, grp, t0, q, b)
                return
            if (not any(self.family(p) == "se" for p in self.leaf)
                    and any(h is None and self.family(p) == "memory"
                            for p, h in self.zone_holder.items())):
                # no clinic, but a free memory: storing a block there is a syndrome round,
                # and it rests there, corrected, until its next use
                end = self.park(b, t0, ins.index, grp, round_=True)
                self.block_free[q] = max(self.block_free.get(q, 0.0), end)
                return
            C = self._choose("se", ins, near=(b.leaf, b.port), ready=t0)
            gids, arr, trip = self.bring(b, (C, "in"), t0, ins.index, grp)
            s, outs, row = self.op(C, ins.op, {"in": (gids, arr, trip)}, t0, ins.index, grp)
            produced[q] = Bundle(q, outs["out"][0], C, "out", outs["out"][1])
            self.report(C, ins.op, row, ins.index, grp, blocks=[q])
        elif kind == "cx":
            c, t = ins.blocks
            W = self._choose("operation", ins, ready=t0,
                             also=[((self.blocks[b].leaf, self.blocks[b].port), d)
                                   for b, d in zip(ins.blocks, ("a_in", "b_in"))])
            gd = self.guard_at(ins, grp, W)
            tc0, tt0 = (self._lead(b, t0) for b in (c, t)) if self.prefetch else (t0, t0)
            gc, ac, tc = self.bring(self.blocks[c], (W, "a_in"), tc0, ins.index, grp)
            gt, at_, tt = self.bring(self.blocks[t], (W, "b_in"), tt0, ins.index, grp)
            s, outs, _ = self.op(W, "cx", {"a_in": (gc, ac, tc), "b_in": (gt, at_, tt)},
                                 max(t0, gd), ins.index, grp, guard=self.guard_row(ins, gd))
            produced[c] = Bundle(c, outs["a_out"][0], W, "a_out", outs["a_out"][1])
            produced[t] = Bundle(t, outs["b_out"][0], W, "b_out", outs["b_out"][1])
            # a transversal CNOT carries the frame with it: X̄ on the control also appears on
            # the target, Z̄ on the target also on the control (the frame is a logical Pauli)
            (cx_, cz_), (tx_, tz_) = self.frame(c), self.frame(t)
            end = max(row[2] for row in self.events[before:]) if self.events[before:] else t0
            if cx_ != ZERO or tz_ != ZERO:
                g = self.cond_json(ins)
                self.write_frame(t, end, ins=ins.index, grp=grp, guard=g,
                                 copy={"ax": c},
                                 text=f"frame of {t} takes X̄ from {c}",
                                 why="CNOT propagation: X̄_c → X̄_c X̄_t")
                self.write_frame(c, end, ins=ins.index, grp=grp, guard=g,
                                 copy={"az": t},
                                 text=f"frame of {c} takes Z̄ from {t}",
                                 why="CNOT propagation: Z̄_t → Z̄_c Z̄_t")
        elif kind in ("zz", "xx"):
            a, b_ = ins.blocks
            B = self._choose("surgery", ins, ready=t0,
                             also=[((self.blocks[b].leaf, self.blocks[b].port), d)
                                   for b, d in zip(ins.blocks, ("in_a", "in_b"))])
            gd = self.guard_at(ins, grp, B)
            ga, aa, ta = self.bring(self.blocks[a], (B, "in_a"), t0, ins.index, grp)
            gb, ab, tb = self.bring(self.blocks[b_], (B, "in_b"), t0, ins.index, grp)
            s, outs, row = self.op(B, kind, {"in_a": (ga, aa, ta), "in_b": (gb, ab, tb)},
                                   max(t0, gd), ins.index, grp,
                                   guard=self.guard_row(ins, gd))
            produced[a] = Bundle(a, outs["out_a"][0], B, "out_a", outs["out_a"][1])
            produced[b_] = Bundle(b_, outs["out_b"][0], B, "out_b", outs["out_b"][1])
            m = kind[0].upper()
            qx, qz = (1, 0) if m == "X" else (0, 1)
            terms = [(a, qx, qz), (b_, qx, qz)]
            flip = self.flip_for(terms)
            self.report(B, kind, row, ins.index, grp, blocks=[a, b_], correct=terms,
                        outcome=(f"{m}̄_A{m}̄_B measured", flip, ins.cvar))
        elif kind == "ppm" and (len(ins.blocks) > PPM_MAX
                                or not any(self.family(p) == "ppm" for p in self.leaf)):
            self.cat_merge(ins, grp, t0, produced)
        elif kind == "ppm":
            # a product of one X̄ or Z̄ from each block, at a Pauli-product bench: each block
            # through its own door, in the order the instruction names them
            tags = "abcd"
            blks = list(ins.blocks)
            B = self._choose("ppm", ins, ready=t0,
                             also=[((self.blocks[b].leaf, self.blocks[b].port), f"in_{tags[k]}")
                                   for k, b in enumerate(blks)])
            gd = self.guard_at(ins, grp, B)
            inputs = {}
            for k, b in enumerate(blks):
                inputs[f"in_{tags[k]}"] = self.bring(self.blocks[b], (B, f"in_{tags[k]}"), t0,
                                                     ins.index, grp)
            s, outs, row = self.op(B, ins.op, inputs, max(t0, gd), ins.index, grp,
                                   guard=self.guard_row(ins, gd))
            for k, b in enumerate(blks):
                port = f"out_{tags[k]}"
                produced[b] = Bundle(b, outs[port][0], B, port, outs[port][1])
            terms = [(b, 1 if p == "X" else 0, 1 if p == "Z" else 0)
                     for b, p in zip(blks, ins.paulis)]
            flip = self.flip_for(terms)
            name = "".join(f"{p}̄_{'ABCD'[k]}" for k, p in enumerate(ins.paulis)) + " measured"
            self.report(B, ins.op, row, ins.index, grp, blocks=blks, correct=terms,
                        outcome=(name, flip, ins.cvar))
        elif kind == "read":
            q = ins.blocks[0]
            R = self._choose("readout", ins, near=(self.blocks[q].leaf, self.blocks[q].port),
                             ready=t0)
            gd = self.guard_at(ins, grp, R)
            gids, arr, trip = self.bring(self.blocks[q], (R, "in"), t0, ins.index, grp)
            s, outs, row = self.op(R, ins.op, {"in": (gids, arr, trip)}, max(t0, gd),
                                   ins.index, grp, guard=self.guard_row(ins, gd))
            terms = [(q, 1, 0) if ins.basis == "X" else (q, 0, 1)]
            flip = self.flip_for(terms)
            self.report(R, ins.op, row, ins.index, grp, blocks=[q], correct=terms,
                        outcome=(f"{ins.basis}̄ measured", flip, ins.cvar))
            self.pf.pop(q, None)
            used, deps = outs["out"]
            D = self._choose("depot", _ANY, near=(R, "out"), port="in", gids=used)
            arr, trip = self.trip(used, (R, "out"), deps, (D, "in"), t0, ins.index, grp)
            self.op(D, "dump.9", {"in": (used, arr, trip)}, t0, ins.index, grp)
            del self.blocks[q]
            self.block_info[q]["read"] = row[1]
        elif kind in ("s", "sx"):
            self.pi4(ins, grp, t0, before, produced)
        elif kind == "ht":
            # a logical Hadamard at a Hadamard bay: H on every ion, the block sent out a quarter
            # turn round (qccd/gadget/leaves/hadamard.py); the frame's X̄ and Z̄ trade places
            q = ins.blocks[0]
            Hb = self._choose("hadamard", ins, near=(self.blocks[q].leaf, self.blocks[q].port), ready=t0)
            gd = self.guard_at(ins, grp, Hb)
            gids, arr, trip = self.bring(self.blocks[q], (Hb, "in"), t0, ins.index, grp)
            s, outs, row = self.op(Hb, "h", {"in": (gids, arr, trip)}, max(t0, gd), ins.index, grp,
                                   guard=self.guard_row(ins, gd))
            produced[q] = Bundle(q, outs["out"][0], Hb, "out", outs["out"][1])
            # the block's ions in code order from this op on: `ions` stays the order it was born
            # in, `turns` lists [start, order] each time a place hands it back relabelled (times,
            # like `born` and `read`: event ids are renumbered once the schedule is done)
            self.block_info[q].setdefault("turns", []).append([row[1], list(outs["out"][0])])
            if self.frame(q) != (ZERO, ZERO) or ins.cond is not None:
                self.write_frame(q, row[2], ins=ins.index, grp=grp, guard=self.cond_json(ins), conj="H",
                                 text=f"frame of {q} through H̄",
                                 why="H̄ X̄ H̄ = Z̄: the frame's X̄ and Z̄ parts trade places")
        elif kind == "decode":
            # no ion moves: the syndromes cross their wires and the decoder works
            self.decode(ins, grp, t0)
        elif kind == "bit":
            # a bit the archive computes from outcomes it holds: no ion, no place but the
            # archive.  An XOR of single outcomes stays a parity of records; a product is a
            # new symbol, the bit itself (no flat sign-off follows it: logic/flat.py)
            names = sorted({n for t in ins.formula for _, n in t})
            t = max([t0] + [self.decoded_by(n, "the bit") for n in names])
            _, t_rec, row = self.classical(self.archive, "record", t, ins.index, grp)
            if all(len(term) <= 1 for term in ins.formula):
                value = ZERO
                for term in ins.formula:
                    value = xor(value, ONE) if not term else \
                        xor(value, self.cvar_value[term[0][1]], ONE if term[0][0] else ZERO)
            else:
                value = (0, frozenset({(f"bit {ins.cvar}", "value")}))
            self.cvars[(ins.index, ins.cvar)] = ins.cvar
            self.cvar_value[ins.cvar] = value
            self.cvar_time[ins.cvar] = t_rec
        elif kind in ("x", "y", "z"):
            # A logical Pauli costs nothing on the ions: it is a line in the archive
            # (Pauli-frame tracking), and every later readout is corrected by it.
            q = ins.blocks[0]
            dax = ONE if kind in ("x", "y") else ZERO
            daz = ONE if kind in ("z", "y") else ZERO
            if ins.cond is not None:
                # a *conditional* Pauli: no ion is involved, so there is nothing to gate at
                # a place -- the archive folds the Pauli into the frame in the branches
                # where the condition holds, once the outcomes it reads are recorded
                t0 = max(t0, max([self.decoded_by(n, "the guard") for n in ins.cond.names]
                                 or [0.0]))
            self.write_frame(q, t0, ins=ins.index, grp=grp, dax=dax, daz=daz,
                             guard=self.cond_json(ins),
                             text=f"{kind.upper()}̄ on {q}, in software"
                                  + (f" when {ins.guard}" if ins.guard else ""),
                             why="a logical Pauli is a frame update: no ion is touched, and "
                                 "every outcome this frame anticommutes with is flipped "
                                 "when the archive reports it")
        else:
            raise CityError(f"no realisation for {kind}")

        for q, bundle in produced.items():
            self.blocks[q] = bundle
        # a block the very next instruction does not use rests in a zone ("next" skipping
        # any `decode`, which reads syndromes out of places and never touches a block)
        for q, bundle in produced.items():
            nxt = self.next_use.get((ins.index, q))
            if nxt is None or self._moves_between(ins.index, nxt) >= self.park_within:
                self.park(bundle, t0, ins.index, grp)
        if kind == "decode":
            # THE DECODER WORKS ALONGSIDE THE IONS.  A decode holds up nothing that does not
            # read what it decides: the next instruction starts when the ions are ready, and
            # a guard that reads a decoded outcome waits for it by itself (`decoded_by`).
            # Counting the decoder's microseconds into the barrier would make calling it
            # move every ion after it, which is a cost the hardware does not pay.
            return
        ends = ([e[2] for e in self.events[before:]] + [c[11] for c in self.carries]
                + [m[7] for m in self.messages])
        self.t_now = max([self.t_now] + ends)
        if self.parallel:
            # what this instruction did: its blocks are free when the last of it is over
            mine = ([e[2] for e in self.events[before:]] + [c[11] for c in self.carries[before_c:]]
                    + [m[7] for m in self.messages[before_m:]]
                    + [f["t"] for f in self.frames[before_f:]])
            done = max(mine + [t0])
            if self.free_at == "block":
                # each block is free when what this instruction did TO IT is over: the ancilla
                # a CNOT leaves at the workshop goes on while its partner is still being stored
                for q in list(ins.blocks) + list(produced):
                    gs = set(self.blocks[q].gids) if q in self.blocks else set()
                    if q in produced:
                        gs |= set(produced[q].gids)
                    own = ([e[2] for e in self.events[before:]
                            if any(isinstance(g, int) and g in gs for _, g in e[8])]
                           + [c[11] for c in self.carries[before_c:] if gs & set(c[7])]
                           + [f["t"] for f in self.frames[before_f:] if f["block"] == q])
                    self.block_free[q] = max(self.block_free.get(q, 0.0), max(own + [t0]))
                return
            for q in list(ins.blocks) + list(produced):
                self.block_free[q] = max(self.block_free.get(q, 0.0), done)

    def refresh_in_place(self, ins: Instr, grp: str, t0: float, q: str, b: Bundle) -> None:
        """One syndrome round on a block resting in a memory: no trip, no other place.  The
        memory is the block's alone while it rests (its lease), so the round runs whenever
        the program asks for it; its syndrome goes to the decoder at once, the way a memory
        keeps decoding what it measures."""
        M = b.leaf
        O = self.leaf[M].master.ops["refresh"]
        s = max(t0, self.mem_last.get(M, 0.0))
        names = self.leaf[M].master.ops["release"].transfer("out", "out").ions
        visitors = [[n, g] for n, g in self.leaf[M].ids.items()] + \
                   [[n, g] for n, g in zip(names, b.gids)]
        row = self.event(M, "refresh", s, s + O.duration_us, ins.index, grp, bind=visitors)
        self.mem_last[M] = row[2]
        self.report(M, "refresh", row, ins.index, grp, blocks=[q])
        w = self.windows[-1] if self.windows and self.windows[-1]["event"] == row[0] else None
        if w is not None:
            self._decode_window(w, ins.index, grp, t0)
        self.block_free[q] = max(self.block_free.get(q, 0.0), row[2])

    def _decode_window(self, w: dict, ins: int, grp: str, t0: float) -> None:
        """Send one window to the decoder now, and write its correction into the archive."""
        t_send = max(t0, w["measured_us"])
        t1, _ = self.message((w["place"], "syn"), t_send, bits=w["bits"], signal="syndrome",
                             ins=ins, grp=grp, event=w["event"],
                             label=f"{w['bits']} check outcomes from {w['op']}")
        _, t_dec, dec = self.classical(self.decoder, "decode", t1, ins, grp)
        t2, _ = self.message((self.decoder, "frame"), t_dec, bits=2, signal="frame",
                             ins=ins, grp=grp, label="Pauli-frame update", event=dec[0])
        _, t_up, up = self.classical(self.archive, "update", t2, ins, grp)
        for blk in w["blocks"]:
            self.frame_row(t_up, blk, "window", f"window correction for {blk}", ins=ins,
                           event=up[0],
                           why=f"the decoder's update for the round {w['op']} measured; the "
                               f"gadget's own report declares it as a frame, and the sign-off "
                               f"allows exactly that")
        w.update(decoded_by=ins, sent_us=round(t_send, 3), decode_event=dec[0],
                 decoded_us=round(t_dec, 3), written_us=round(t_up, 3))

    def pi4(self, ins: Instr, grp: str, t0: float, before: int, produced: dict) -> None:
        """A π/4 rotation by consuming a |Ȳ⟩ block (Litinski 2019, "Game of Surface Codes"):
        `s` measures Z̄Z̄ against it and reads it in X; `sx` measures X̄X̄ and reads it in Z.

        S̄ gadget: S̄ when the two outcomes agree, S̄† = Z̄S̄ when they do not, so the
        correction is Z̄^(m_zz ⊕ m_x); the frame conjugates through S̄ (X̄ → Ȳ: az ^= ax).
        √X̄ gadget: √X̄ when the outcomes DISAGREE, √X̄† = X̄√X̄ when they agree, so the
        correction is X̄^(1 ⊕ m_xx ⊕ m_z); the frame conjugates through √X̄ (Z̄ → Ȳ: ax ^= az).
        Both tables are the contract checker's (`qccd.gadget.logic.contract`), on the |Ȳ⟩ the
        pharmacy is verified to prepare.  Corrections are Paulis: the archive keeps them as
        frames and no ion ever feels them."""
        kind = ins.kind
        meas, read_basis = ("zz", "X") if kind == "s" else ("xx", "Z")
        P = "Z" if kind == "s" else "X"
        q = ins.blocks[0]
        I = self._choose("injection", ins, ready=t0)
        gd = self.guard_at(ins, grp, I)
        t0 = max(t0, gd)
        D1, g1, d1 = self._load(1, (I, "magic"), t0, ins.index, grp)
        marr, mtrip = self.trip(g1, (D1, "out"), d1, (I, "magic"), t0, ins.index, grp)
        D2, g8, d8 = self._load(8, (I, "in"), t0, ins.index, grp)
        farr, ftrip = self.trip(g8, (D2, "out"), d8, (I, "in"), t0, ins.index, grp)
        s, outs, row = self.op(I, "inject.Y", {"magic": (g1, marr, mtrip),
                                               "in": (g8, farr, ftrip)},
                               t0, ins.index, grp, guard=self.guard_row(ins, gd))
        y = f"{q}.Y{ins.index}"
        self.merged_into[y] = q
        ybundle = Bundle(y, outs["out"][0], I, "out", outs["out"][1])
        self.block_info[y] = {"ions": outs["out"][0], "born": row[1], "born_at": I,
                              "code": "surface_d3", "n": 9, "k": 1,
                              "resource": f"|Ȳ⟩ for the {'S̄' if kind == 's' else '√X̄'} gadget",
                              "consumed_by": q}
        self.pf[y] = (ZERO, ZERO)
        self.report(I, "inject.Y", row, ins.index, grp, blocks=[y])
        B = self._choose("surgery", _ANY, near=(ybundle.leaf, ybundle.port), port="in_b",
                         ready=row[2], also=[((self.blocks[q].leaf, self.blocks[q].port), "in_a")])
        gdB = self.guard_at(ins, grp, B)
        ga, aa, ta = self.bring(self.blocks[q], (B, "in_a"), t0, ins.index, grp)
        gb, ab, tb = self.bring(ybundle, (B, "in_b"), t0, ins.index, grp)
        s, outs, row = self.op(B, meas, {"in_a": (ga, aa, ta), "in_b": (gb, ab, tb)},
                               max(t0, gdB), ins.index, grp, guard=self.guard_row(ins, gdB))
        qx, qz = (0, 1) if P == "Z" else (1, 0)
        terms = [(q, qx, qz), (y, qx, qz)]
        flip = self.flip_for(terms)
        m_pp = self.report(B, meas, row, ins.index, grp, blocks=[q, y], correct=terms,
                           outcome=(f"{P}̄_A{P}̄_B measured", flip, None))
        produced[q] = Bundle(q, outs["out_a"][0], B, "out_a", outs["out_a"][1])
        ybundle = Bundle(y, outs["out_b"][0], B, "out_b", outs["out_b"][1])
        R = self._choose("readout", _ANY, near=(B, "out_b"), ready=row[2])
        gdR = self.guard_at(ins, grp, R)
        gids, arr, trip = self.bring(ybundle, (R, "in"), t0, ins.index, grp)
        s, outs, row = self.op(R, f"read.{read_basis}", {"in": (gids, arr, trip)},
                               max(t0, gdR), ins.index, grp, guard=self.guard_row(ins, gdR))
        rt = [(y, 1, 0)] if read_basis == "X" else [(y, 0, 1)]
        m_r = self.report(R, f"read.{read_basis}", row, ins.index, grp, blocks=[y], correct=rt,
                          outcome=(f"{read_basis}̄ measured", self.flip_for(rt), None))
        self.block_info[y]["read"] = row[1]
        self.pf.pop(y, None)
        used, deps = outs["out"]
        Dp = self._choose("depot", _ANY, near=(R, "out"), port="in", gids=used)
        uarr, utrip = self.trip(used, (R, "out"), deps, (Dp, "in"), t0, ins.index, grp)
        self.op(Dp, "dump.9", {"in": (used, uarr, utrip)}, t0, ins.index, grp)
        cond = xor(m_pp or ZERO, m_r or ZERO)
        end = max([e[2] for e in self.events[before:]] or [t0])
        if kind == "s":
            self.write_frame(q, end, ins=ins.index, grp=grp, guard=self.cond_json(ins),
                             conj="S", daz=cond,
                             text=f"S̄ on {q}: Z̄ if the two outcomes disagree",
                             why="the gadget applies S̄ up to Z̄^(m_zz ⊕ m_x); the frame "
                                 "also conjugates through S̄ (X̄ → Ȳ)")
        else:
            self.write_frame(q, end, ins=ins.index, grp=grp, guard=self.cond_json(ins),
                             conj="SX", dax=xor(cond, ONE),
                             text=f"√X̄ on {q}: X̄ if the two outcomes agree",
                             why="the gadget applies √X̄ up to X̄^(1 ⊕ m_xx ⊕ m_z); the frame "
                                 "also conjugates through √X̄ (Z̄ → Ȳ)")

    # -- output -----------------------------------------------------------------------------

    def gir(self) -> dict:
        self.events.sort(key=lambda e: (e[1], e[2], e[3]))
        remap = {}
        for i, e in enumerate(self.events):
            remap[e[0]] = i
            e[0] = i

        def fix(expr):
            return {"const": expr["const"],
                    "sources": [[remap[eid] if isinstance(eid, int) else eid, flow]
                                for eid, flow in expr["sources"]]}

        for o in self.outcomes:
            for key in ("flip", "value"):
                if o.get(key) is not None:
                    o[key] = fix(o[key])
            if o.get("recorded") is not None:
                o["recorded"] = remap[o["recorded"]]
        for m in self.messages:
            if m[13] >= 0:
                m[13] = remap[m[13]]
        for w in self.windows:
            w["event"] = remap[w["event"]]
            if w.get("decode_event") is not None:
                w["decode_event"] = remap[w["decode_event"]]
        for f in self.frames:
            f["event"] = remap[f["event"]]
            f["ax"], f["az"] = fix(f["ax"]), fix(f["az"])
            if "delta" in f:
                f["delta"]["ax"] = fix(f["delta"]["ax"])
                f["delta"]["az"] = fix(f["delta"]["az"])
        def fix_set(s):
            return frozenset((remap[e] if isinstance(e, int) else e, fl) for e, fl in s)
        self.pf = {b: ((ax[0], fix_set(ax[1])), (az[0], fix_set(az[1])))
                   for b, (ax, az) in self.pf.items()}
        holds = [[c[1], c[4], round(self._holds[id(c)], 3)] for c in self.carries if id(c) in self._holds]
        spans = [[c[1], c[4], *self._spans[id(c)]] for c in self.carries if id(c) in self._spans]
        self.carries.sort(key=lambda c: c[4])
        for i, c in enumerate(self.carries):
            c[0] = i
        self.messages.sort(key=lambda m: (m[6], m[7]))
        for i, m in enumerate(self.messages):
            m[0] = i
        horizon = max([e[2] for e in self.events] + [c[11] for c in self.carries]
                      + [m[7] for m in self.messages] + [0.0])
        outcomes = [{"instruction": o["instruction"], "event": o["event"][0], "cvar": o["cvar"],
                     "flow": o["flow"], "blocks": o["blocks"], "flip": o.get("flip"),
                     "value": o.get("value"), "recorded": o.get("recorded"),
                     "recorded_us": o.get("recorded_us"),
                     # what the archive corrects this outcome with, and when it was measured:
                     # in a branch where a conditional correction ran, the frame differs
                     "correct": o.get("correct", []), "at": o.get("at"),
                     # a product measured in parts (a cat chain): every (event, flow) whose
                     # records it is the parity of
                     **({"parts": [[r[0], f] for r, f in o["parts"]]} if o.get("parts") else {})}
                    for o in self.outcomes]
        return {
            "schema": GIR_SCHEMA,
            "model": "places",
            "top": self.lib.top,
            "units": "us",
            "makespan_us": round(horizon, 3),
            "n_ions": self.next_gid,
            "n_residents": self.n_residents,
            "ion_order": {m: ion_order(self.leaves, m) for m in
                          sorted({li.master.name for li in self.leaf.values()})},
            "leaves": {p: [li.master.name, li.x, li.y, li.first_id] for p, li in self.leaf.items()},
            # a bent road also names its shape (its conveyor `chan<shape>`); a straight one's
            # is its length
            "nets": {p: [n.a[0], n.a[1], n.b[0], n.b[1], n.length]
                     + ([n.shape] if n.shape and not n.shape.isdigit() else [])
                     for p, n in self.nets.items()},
            "blocks": self.block_info,
            "program": {"name": self.alg.name, "title": self.alg.title, "source": self.alg.source,
                        "language": "places",
                        "instructions": [i.to_json() for i in self.alg.instructions]},
            "events": self.events,
            "carries": self.carries,
            **({"planes": self.planes} if self.planes else {}),
            **({"road_holds": sorted(holds)} if holds else {}),
            **({"road_spans": sorted(spans)} if spans else {}),
            "wires": {w.path: [w.a[0], w.a[1], w.b[0], w.b[1], w.latency_us]
                      for w in self.wires.values()},
            "messages": self.messages,
            # every syndrome window, decoded or not: `decoded_by` is the `decode`
            # instruction that sent it, or None if the program never did (G11)
            "windows": self.windows,
            "frames": self.frames,
            "block_frames": {b: {"ax": expr_json(ax), "az": expr_json(az)}
                             for b, (ax, az) in sorted(self.pf.items())},
            "control": {"decoder": self.decoder, "archive": self.archive,
                        "latency_us": dict(LATENCY)},
            "refused": self.refused,
            **({"noops": self.noops} if self.noops else {}),
            "branch_frames": self.branch_frames,
            "outcomes": outcomes,
            "stock": dict(self.stock),
            "channels": self.leaves.channel_references(),
            "idle_us": {},
            "ppm_rounds": 1,
            "cycles_between": 0,
        }


def schedule(alg: Algorithm, lib: Library, leaves: LeafLibrary, *, log=None,
             parallel: bool = True) -> dict:
    """Schedule `alg` on the design `lib`.  `parallel=False` runs one instruction, and one
    road trip, at a time, as the place model first did."""
    gir = City(alg, lib, leaves, log=log, parallel=parallel).run()
    gir["policy"] = "parallel" if parallel else "sequential"
    return gir
