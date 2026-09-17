"""Scheduling a logical algorithm on a design of verified places.  GADGETS.md §9.

A design is a composite of place instances (prep, clinic, workshop, bridge, readout,
injection, factory, zones, depots) and road junctions, joined by channels.  A logical block
is a bundle of nine ions that travels between places; every instruction of the algorithm
(`qccd.gadget.algorithm`) is one op at one place, plus the trips that bring its blocks
there.  Only characterized abstracts are read: op durations, per-ion door crossing times,
junction passes, and channel transits replayed from the conveyor reference.

The policy is deliberately simple, because what this layer has to prove is correctness,
not throughput:

* instructions run **one after another**: an instruction starts once everything the
  previous one scheduled has finished;
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

from .algorithm import Algorithm, Instr
from .library import LeafLibrary
from .model import Library
from .logic.decode import LATENCY
from .synth import Calendar, SynthError, flatten, ion_order

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


def expr_json(e) -> dict:
    return {"const": e[0], "sources": sorted([list(x) for x in e[1]])}


def expr_text(e, names=None) -> str:
    parts = ([] if not e[0] else ["1"]) + [
        (names or {}).get(x, f"m@{x[0]}") for x in sorted(e[1])]
    return " ⊕ ".join(parts) if parts else "0"

GIR_SCHEMA = "qccd.gir/0.2"


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


class City:
    def __init__(self, alg: Algorithm, lib: Library, leaves: LeafLibrary, log=None):
        self.alg, self.lib, self.leaves = alg, lib, leaves
        self.log = log or (lambda *a, **k: None)
        self.wires: dict = {}
        self.leaf, self.nets, self.n_residents = flatten(lib, leaves, wires=self.wires)
        self.next_gid = self.n_residents
        self.cal = {p: Calendar() for p in self.leaf}
        self.net_cal = {p: Calendar() for p in self.nets}
        self.events: list[list] = []
        self.carries: list[list] = []
        self.refused: list[list] = []
        self.outcomes: list[dict] = []
        self.frames: list[dict] = []
        self.blocks: dict[str, Bundle] = {}
        self.block_info: dict[str, dict] = {}
        self.zone_holder = {p: None for p, li in self.leaf.items() if li.master.family == "zone"}
        self.stock = {p: 0 for p, li in self.leaf.items() if li.master.family == "depot"}
        self.t_now = 0.0
        #: the streets carry one bundle at a time: the next trip starts when the last
        #: one has arrived, so no bundle ever queues in a channel shorter than itself (G4)
        self.road_free = 0.0
        self.next_use = alg.next_use()
        self.messages: list[list] = []
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
        #: the outcomes some guard reads: those may not themselves depend on a branch
        self.guard_reads = set(alg.guard_cvars)
        self._graph()
        self._control()

    # -- the road network -----------------------------------------------------------------

    def family(self, path):
        return self.leaf[path].master.family

    def _graph(self):
        self.adj: dict = {}

        def edge(u, v, w, kind, info):
            self.adj.setdefault(u, []).append((v, w, kind, info))

        for n in self.nets.values():
            w = self.leaves.channel_timing(n.length, 1)["transit_us"]
            edge(n.a, n.b, w, "net", n.path)
            edge(n.b, n.a, w, "net", n.path)
        for path, li in self.leaf.items():
            if li.master.name.startswith("junc"):
                for a in li.master.ports:
                    for b in li.master.ports:
                        if a.name != b.name:
                            op = li.master.ops[f"pass.{a.node}.{b.node}"]
                            edge((path, a.name), (path, b.name), op.duration_us, "junction",
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

    def _choose(self, category: str, ins: Instr, near=None, port="in", free=None):
        cands = [p for p in self.leaf if self.family(p) == category]
        if ins.at:
            cands = [p for p in cands if p == ins.at or p.split(".")[-1] == ins.at]
        if free:
            cands = [p for p in cands if free(p)]
        if not cands:
            raise CityError(f"the design has no {'free ' if free else ''}{category} place"
                            + (f" named {ins.at}" if ins.at else ""))
        if near is None or len(cands) == 1:
            return sorted(cands)[0]
        return min(sorted(cands), key=lambda p: self._distance(near, (p, port)))

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

    def report(self, path, opname, row, ins, grp, *, blocks=(), outcome=None, correct=()):
        """Send an op's classical output where it goes: the syndrome to the decoder, whose
        frame update lands in the archive, and any logical outcome to the archive.

        `outcome` is `(flow name, parity expression of the frame flip, cvar)`."""
        li = self.leaf[path]
        t_meas = row[1] + self.last_measure_us(li.master.name, opname)
        ports = {p.signal: p for p in li.master.ports if p.kind == "classical"}
        if "syndrome" in ports and self.decoder and self.archive:
            t1, _ = self.message((path, "syn"), t_meas, bits=ports["syndrome"].bits,
                                 signal="syndrome", ins=ins, grp=grp, event=row[0],
                                 label=f"{ports['syndrome'].bits} check outcomes")
            _, t_dec, dec = self.classical(self.decoder, "decode", t1, ins, grp)
            t2, _ = self.message((self.decoder, "frame"), t_dec, bits=2, signal="frame",
                                 ins=ins, grp=grp, label="Pauli-frame update", event=dec[0])
            _, t_up, up = self.classical(self.archive, "update", t2, ins, grp)
            for b in blocks:
                self.frame_row(t_up, b, "window", f"window correction for {b}", ins=ins,
                               event=up[0], why="the decoder's update for this round; the "
                               "gadget's own report declares it as a frame, and the "
                               "sign-off allows exactly that")
        if outcome is not None and self.archive:
            flow, flip, cvar = outcome
            res = ports.get("outcome")
            if res is None:
                raise CityError(f"{li.master.name} has no outcome port to report {flow}")
            t1, _ = self.message((path, "res"), t_meas, bits=1, signal="outcome", ins=ins,
                                 grp=grp, label=cvar or flow, event=row[0])
            _, t_rec, rec = self.classical(self.archive, "record", t1, ins, grp)
            value = xor(outcome_of(row[0], flow), flip)
            self.values[(ins, flow)] = value
            self.outcomes.append({"instruction": ins, "event": row, "cvar": cvar,
                                  "flow": flow, "blocks": list(blocks),
                                  "flip": expr_json(flip), "value": expr_json(value),
                                  "correct": [list(c) for c in correct],
                                  "at": round(t_meas, 3),
                                  "recorded": rec[0], "recorded_us": round(t_rec, 3)})
            if cvar:
                if cvar in self.guard_reads:
                    for block, _, _ in correct:
                        self.certain(block, f"the outcome {cvar}, which a guard reads,")
                self.cvars[(ins, cvar)] = cvar
                self.cvar_value[cvar] = value
                self.cvar_time[cvar] = t_rec
            self.frame_row(t_rec, blocks[0] if blocks else None, "outcome",
                           f"{cvar or flow} = {expr_text(value)}", ins=ins, event=rec[0],
                           why="the outcome as the archive keeps it: the records the "
                               "gadget's flow report names, corrected by the block's frame")
            return value
        return None

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
        return {"const": ins.cond[0], "cvars": list(ins.cond[1]), "text": ins.guard}

    def guard_at(self, ins, grp, path) -> float:
        """When the op at `path` may start: its guard resolved in the archive and delivered.

        The archive reads the outcomes the condition names (one `resolve`), and the answer
        travels down the decision wire to the place.  Places with no `ctl` port are told
        nothing, so a guarded op there could not be gated -- that is refused."""
        if ins.cond is None:
            return 0.0
        if not self.archive:
            raise CityError("this design has no classical memory, so no guard can be resolved")
        const, names = ins.cond
        missing = [n for n in names if n not in self.cvar_time]
        if missing:
            raise CityError(f"the guard reads {missing[0]}, which nothing has measured yet")
        if ins.index not in self.resolved:
            t_ready = max([self.cvar_time[n] for n in names] or [0.0])
            _, t_end, row = self.classical(self.archive, "resolve", t_ready, ins.index, grp)
            expr = ZERO if not const else ONE
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
        """Refuse to use a frame that is only known once a branch is taken.

        A guarded frame update makes the block's frame depend on a bit measured while the
        machine runs; anything that has to *read* the frame -- correcting an outcome,
        propagating it through a CNOT -- would then be branch-dependent too, which this
        scheduler does not express.  Say so rather than quietly pick one branch."""
        if self.pf_guarded.get(block):
            raise CityError(
                f"{what} on {block} needs its Pauli frame, but a conditional correction "
                f"({self.pf_guarded[block][-1].get('text')}) left that frame depending on a "
                f"measurement; put the conditional operation after this one")

    # -- rows -----------------------------------------------------------------------------

    def event(self, path, op, t0, t1, ins, group="", bind=None, guard=None):
        self.cal[path].reserve(t0, t1)
        # the id is stable from here on: frame and outcome expressions name events by it,
        # and `gir()` renumbers both together
        row = [len(self.events), round(t0, 3), round(t1, 3), path, op, 1, ins, group,
               bind or [], []]
        if guard is not None:
            # element 10: this op is classically controlled.  The place is reserved either
            # way; `ready_us` is when the bit was at its door, which G11 checks.
            row.append(dict(guard))
        self.events.append(row)
        return row

    def carry(self, net, src, deps, tau, gids, role, ins, group, takes):
        t0 = min(deps)
        row = [0, net, src[0], src[1], round(t0, 3), [round(d - t0, 3) for d in deps],
               round(tau, 3), list(gids), role, ins, group, round(max(takes), 3),
               [round(k - t0, 3) for k in takes]]
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
        s = self.cal[path].earliest(s, O.duration_us)
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
                net, src, deps, tau = trip
                self.carry(net, src, deps, tau, gids, "data", ins, grp,
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

    def trip(self, gids, src, deps, dst, t_min, ins, grp):
        """Take a bundle leaving `src` at `deps` to door `dst`: junction passes are scheduled
        on the way; returns (arrival times at `dst`, the last channel, still open)."""
        t_min = max(t_min, self.road_free)
        t = list(deps)
        trip = None
        for kind, info, u, _v in self._route(src, dst):
            if kind == "net":
                tau = self.leaves.channel_timing(self.nets[info].length, 1)["transit_us"]
                trip = (info, u, list(t), tau)
                t = [d + tau for d in t]
            else:
                jpath, a, b, opname = info
                if len(gids) != 9:
                    opname = f"{opname}.{len(gids)}"
                O = self.leaf[jpath].master.ops.get(opname)
                if O is None:
                    raise CityError(f"junction {jpath} cannot pass a bundle of {len(gids)}")
                tin, tout = O.transfer(a, "in"), O.transfer(b, "out")
                s = max(t_min, max(x - i for x, i in zip(t, tin.times_us)))
                s = self.cal[jpath].earliest(s, O.duration_us)
                net, usrc, udeps, tau = trip
                self.carry(net, usrc, udeps, tau, gids, "data", ins, grp,
                           takes=[s + i for i in tin.times_us])
                self.event(jpath, opname, s, s + O.duration_us, ins, grp,
                           bind=[[nm, g] for nm, g in zip(tin.ions, gids)])
                t = [s + x for x in tout.times_us]
                trip = None
        self.road_free = max([self.road_free] + list(t))
        return t, trip

    def bring(self, bundle: Bundle, dst, t_min, ins, grp):
        """A block to door `dst`: out of its zone first if it rests in one."""
        if bundle.resting:
            Z = bundle.leaf
            O = self.leaf[Z].master.ops["check_out"]
            names = O.transfer("out", "out").ions
            s, outs, _ = self.op(Z, "check_out", {}, t_min, ins, grp,
                                 resting=dict(zip(names, bundle.gids)))
            bundle.deps = outs["out"][1]
            bundle.leaf, bundle.port = Z, "out"
            bundle.resting = False
            self.zone_holder[Z] = None
        arr, trip = self.trip(bundle.gids, (bundle.leaf, bundle.port), bundle.deps, dst,
                              t_min, ins, grp)
        return bundle.gids, arr, trip

    def park(self, bundle: Bundle, t_min, ins, grp):
        near = (bundle.leaf, bundle.port)
        Z = self._choose("zone", _ANY, near=near,
                         free=lambda p: self.zone_holder[p] is None)
        gids, arr, trip = self.bring(bundle, (Z, "in"), t_min, ins, grp)
        s, _, row = self.op(Z, "check_in", {"in": (gids, arr, trip)}, t_min, ins, grp)
        bundle.leaf, bundle.port, bundle.deps = Z, "in", []
        bundle.resting = True
        self.zone_holder[Z] = bundle.name
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

    def _load(self, k, near_door, t_min, ins, grp):
        D = self._choose("depot", _ANY, near=near_door, port="out")
        s, outs, row = self.op(D, f"load.{k}", {}, t_min, ins, grp)
        self.stock[D] += k
        gids, deps = outs["out"]
        return D, gids, deps

    #: the instructions a guard can gate: a place that can be told (`ctl`), or the archive
    GUARDABLE = ("cx", "zz", "xx", "read", "s", "x", "y", "z")

    def instruction(self, ins: Instr):
        grp = f"i{ins.index}"
        t0 = self.t_now
        before = len(self.events)
        kind = ins.kind
        if ins.cond is not None and kind not in self.GUARDABLE:
            raise CityError(f"'{kind}' cannot be guarded: only {', '.join(self.GUARDABLE)} "
                            f"run at a place that can be told whether to fire (a ctl port) "
                            f"or in the archive")
        produced: dict[str, Bundle] = {}
        if kind == "prep":
            P = self._choose("prep", ins)
            D, gids, deps = self._load(9, (P, "in"), t0, ins.index, grp)
            arr, trip = self.trip(gids, (D, "out"), deps, (P, "in"), t0, ins.index, grp)
            s, outs, row = self.op(P, ins.op, {"in": (gids, arr, trip)}, t0, ins.index, grp)
            produced[ins.blocks[0]] = Bundle(ins.blocks[0], outs["out"][0], P, "out", outs["out"][1])
            self.block_info[ins.blocks[0]] = {"ions": outs["out"][0], "born": row[1],
                                              "born_at": P, "code": "surface_d3", "n": 9, "k": 1}
            self.pf[ins.blocks[0]] = (ZERO, ZERO)
            self.report(P, ins.op, row, ins.index, grp, blocks=[ins.blocks[0]])
        elif kind == "inject":
            F = self._choose("factory", ins)
            I = self._choose("injection", ins)
            D1, g1, d1 = self._load(1, (F, "in"), t0, ins.index, grp)
            arr, trip = self.trip(g1, (D1, "out"), d1, (F, "in"), t0, ins.index, grp)
            s, outs, _ = self.op(F, "produce", {"in": (g1, arr, trip)}, t0, ins.index, grp)
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
            self.pf[ins.blocks[0]] = (ZERO, ZERO)
            self.report(I, "inject", row, ins.index, grp, blocks=[ins.blocks[0]])
        elif kind in ("se", "store"):
            q = ins.blocks[0]
            b = self.blocks[q]
            if kind == "store":
                if b.resting:
                    return
                end = self.park(b, t0, ins.index, grp)
                self.t_now = max(self.t_now, end)
                return
            C = self._choose("se", ins, near=(b.leaf, b.port))
            gids, arr, trip = self.bring(b, (C, "in"), t0, ins.index, grp)
            s, outs, row = self.op(C, ins.op, {"in": (gids, arr, trip)}, t0, ins.index, grp)
            produced[q] = Bundle(q, outs["out"][0], C, "out", outs["out"][1])
            self.report(C, ins.op, row, ins.index, grp, blocks=[q])
        elif kind == "cx":
            c, t = ins.blocks
            W = self._choose("operation", ins)
            gd = self.guard_at(ins, grp, W)
            gc, ac, tc = self.bring(self.blocks[c], (W, "a_in"), t0, ins.index, grp)
            gt, at_, tt = self.bring(self.blocks[t], (W, "b_in"), t0, ins.index, grp)
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
            B = self._choose("surgery", ins)
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
        elif kind == "read":
            q = ins.blocks[0]
            R = self._choose("readout", ins, near=(self.blocks[q].leaf, self.blocks[q].port))
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
            D = self._choose("depot", _ANY, near=(R, "out"), port="in")
            arr, trip = self.trip(used, (R, "out"), deps, (D, "in"), t0, ins.index, grp)
            self.op(D, "dump.9", {"in": (used, arr, trip)}, t0, ins.index, grp)
            del self.blocks[q]
            self.block_info[q]["read"] = row[1]
        elif kind == "s":
            # The logical S̄ gadget: a |Ȳ⟩ block is prepared at the pharmacy, measured
            # against the data block by Z̄Z̄ lattice surgery, and read out in X.  The result
            # is S̄ on the data block when the two outcomes agree and S̄† when they do not,
            # so the correction is Z̄ with condition m_zz ⊕ m_x -- a Pauli, which the archive
            # keeps as a frame and no ion ever feels (Litinski 2019, "Game of Surface Codes").
            q = ins.blocks[0]
            I = self._choose("injection", ins)
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
            ybundle = Bundle(y, outs["out"][0], I, "out", outs["out"][1])
            self.block_info[y] = {"ions": outs["out"][0], "born": row[1], "born_at": I,
                                  "code": "surface_d3", "n": 9, "k": 1,
                                  "resource": "|Ȳ⟩ for the S̄ gadget", "consumed_by": q}
            self.pf[y] = (ZERO, ZERO)
            self.report(I, "inject.Y", row, ins.index, grp, blocks=[y])
            B = self._choose("surgery", _ANY, near=(ybundle.leaf, ybundle.port))
            gdB = self.guard_at(ins, grp, B)
            ga, aa, ta = self.bring(self.blocks[q], (B, "in_a"), t0, ins.index, grp)
            gb, ab, tb = self.bring(ybundle, (B, "in_b"), t0, ins.index, grp)
            s, outs, row = self.op(B, "zz", {"in_a": (ga, aa, ta), "in_b": (gb, ab, tb)},
                                   max(t0, gdB), ins.index, grp,
                                   guard=self.guard_row(ins, gdB))
            terms = [(q, 0, 1), (y, 0, 1)]
            flip = self.flip_for(terms)
            m_zz = self.report(B, "zz", row, ins.index, grp, blocks=[q, y], correct=terms,
                               outcome=("Z̄_AZ̄_B measured", flip, None))
            produced[q] = Bundle(q, outs["out_a"][0], B, "out_a", outs["out_a"][1])
            ybundle = Bundle(y, outs["out_b"][0], B, "out_b", outs["out_b"][1])
            R = self._choose("readout", _ANY, near=(B, "out_b"))
            gdR = self.guard_at(ins, grp, R)
            gids, arr, trip = self.bring(ybundle, (R, "in"), t0, ins.index, grp)
            s, outs, row = self.op(R, "read.X", {"in": (gids, arr, trip)}, max(t0, gdR),
                                   ins.index, grp, guard=self.guard_row(ins, gdR))
            m_x = self.report(R, "read.X", row, ins.index, grp, blocks=[y],
                              correct=[(y, 1, 0)],
                              outcome=("X̄ measured", self.flip_for([(y, 1, 0)]), None))
            self.block_info[y]["read"] = row[1]
            self.pf.pop(y, None)
            used, deps = outs["out"]
            Dp = self._choose("depot", _ANY, near=(R, "out"), port="in")
            uarr, utrip = self.trip(used, (R, "out"), deps, (Dp, "in"), t0, ins.index, grp)
            self.op(Dp, "dump.9", {"in": (used, uarr, utrip)}, t0, ins.index, grp)
            cond = xor(m_zz or ZERO, m_x or ZERO)
            end = max([e[2] for e in self.events[before:]] or [t0])
            # S̄ conjugates the frame (X̄ → Ȳ, so az takes ax) and the correction adds Z̄
            self.write_frame(q, end, ins=ins.index, grp=grp, guard=self.cond_json(ins),
                             conj="S", daz=cond,
                             text=f"S̄ on {q}: Z̄ if the two outcomes disagree",
                             why="the gadget applies S̄ up to Z̄^(m_zz ⊕ m_x); the frame "
                                 "also conjugates through S̄ (X̄ → Ȳ)")
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
                t0 = max(t0, max([self.cvar_time.get(n, 0.0) for n in ins.cond[1]] or [0.0]))
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
        # a block the very next instruction does not use rests in a zone
        for q, bundle in produced.items():
            nxt = self.next_use.get((ins.index, q))
            if nxt != ins.index + 1:
                self.park(bundle, t0, ins.index, grp)
        ends = ([e[2] for e in self.events[before:]] + [c[11] for c in self.carries]
                + [m[7] for m in self.messages])
        self.t_now = max([self.t_now] + ends)

    # -- output -----------------------------------------------------------------------------

    def gir(self) -> dict:
        self.events.sort(key=lambda e: (e[1], e[2], e[3]))
        remap = {}
        for i, e in enumerate(self.events):
            remap[e[0]] = i
            e[0] = i

        def fix(expr):
            return {"const": expr["const"],
                    "sources": [[remap[int(eid)], flow] for eid, flow in expr["sources"]]}

        for o in self.outcomes:
            for key in ("flip", "value"):
                if o.get(key) is not None:
                    o[key] = fix(o[key])
            if o.get("recorded") is not None:
                o["recorded"] = remap[o["recorded"]]
        for m in self.messages:
            if m[13] >= 0:
                m[13] = remap[m[13]]
        for f in self.frames:
            f["event"] = remap[f["event"]]
            f["ax"], f["az"] = fix(f["ax"]), fix(f["az"])
            if "delta" in f:
                f["delta"]["ax"] = fix(f["delta"]["ax"])
                f["delta"]["az"] = fix(f["delta"]["az"])
        self.pf = {b: (( ax[0], frozenset((remap[int(e)], fl) for e, fl in ax[1])),
                       ( az[0], frozenset((remap[int(e)], fl) for e, fl in az[1])))
                   for b, (ax, az) in self.pf.items()}
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
                     "correct": o.get("correct", []), "at": o.get("at")}
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
            "nets": {p: [n.a[0], n.a[1], n.b[0], n.b[1], n.length] for p, n in self.nets.items()},
            "blocks": self.block_info,
            "program": {"name": self.alg.name, "title": self.alg.title, "source": self.alg.source,
                        "language": "places",
                        "instructions": [i.to_json() for i in self.alg.instructions]},
            "events": self.events,
            "carries": self.carries,
            "wires": {w.path: [w.a[0], w.a[1], w.b[0], w.b[1], w.latency_us]
                      for w in self.wires.values()},
            "messages": self.messages,
            "frames": self.frames,
            "block_frames": {b: {"ax": expr_json(ax), "az": expr_json(az)}
                             for b, (ax, az) in sorted(self.pf.items())},
            "control": {"decoder": self.decoder, "archive": self.archive,
                        "latency_us": dict(LATENCY)},
            "refused": self.refused,
            "outcomes": outcomes,
            "stock": dict(self.stock),
            "channels": self.leaves.channel_references(),
            "idle_us": {},
            "ppm_rounds": 1,
            "cycles_between": 0,
        }


def schedule(alg: Algorithm, lib: Library, leaves: LeafLibrary, *, log=None) -> dict:
    return City(alg, lib, leaves, log=log).run()
