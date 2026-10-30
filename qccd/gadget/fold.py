"""A folded schedule: a gate is scheduled once per context, and every call of it reuses that.
docs/FULLSTACK_PLAN.md, slice S5b.

A program FormalRV compiles keeps its gadgets and loops; lowered (`qccd.fullstack.qec2alg`)
it is a `.alg` whose gates are FormalRV's gadget bodies, and a Shor at small width already
makes half a million instructions once expanded.  Nothing here expands.  A gate call is a
**macro**:

* **homes.**  Every block a call is passed lives in one memory (its home) for its whole
  life; a call finds it there and returns it there.  So a gate's schedule depends only on its
  body, the homes of its blocks, the shape of the guard on the call, and which rest places are
  taken -- the macro's *context*.
* **once per context.**  The body is scheduled by the town's own scheduler (`City`), alone on
  the machine, from time 0, its blocks resting at their homes, its outer guard already
  decided.  Its calls are macros in turn.  The result is the macro's own events, carries,
  messages, windows, frames and outcomes, its calls, and its **footprint**: every interval
  it holds on a place, a road, a junction, the decoder and the archive (its blocks' homes
  aside, which are theirs).
* **a call is a box.**  The caller starts it at the first time every interval of the
  footprint is free on its own calendars, and books them.  So no two macros ever share a
  place, road or junction at the same time, by construction.
* **frames.**  A block enters a macro with a frame that is a symbol (`in:i`, its X and Z
  parts); the macro's outcomes and its blocks' frames on return are parities over those
  symbols and its own outcomes, and the caller substitutes its own (`subst`).  An outcome of
  a call is named by the call (`I/e`), so nothing is ever renamed by hand.
* **decoding.**  A macro decodes every syndrome it has left before it returns (`decode` at
  its end, if anything is waiting): nothing it measured is the caller's to decode.

The result is GIR 0.3: the root's own rows (as GIR 0.2), the macros, and the calls.  `expand`
turns it back into GIR 0.2 -- every instruction, every ion -- which the existing checks read;
on small programs that is the differential test, and on large ones the page expands only what
is on screen.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .algorithm import Algorithm, AlgorithmError, Instr, _Parser, parse_guard
from .city import City, Bundle, CityError, _ANY, _source_key
from .synth import Calendar

__all__ = ["parse_folded", "schedule_folded", "expand", "FoldError", "GIR_FOLDED"]

GIR_FOLDED = "qccd.gir/0.3"

#: the ions of a macro's blocks are the caller's: inside, they are named from here on
PARAM_GID = 1_000_000_000

#: a footprint keeps at most this many intervals per resource (gaps merged, smallest first):
#: a box may hold a little more than it uses, never less
MAX_INTERVALS = 256

#: a block resting in a memory gets a syndrome round at least this often (G8's budget is
#: COHERENCE_BUDGET_US; a rest that spans a call is refreshed on both sides of the call's
#: start, so each side keeps under half of 80 % of it)
REFRESH_EVERY_US = 0.4 * 1e6


class FoldError(CityError):
    pass


# --------------------------------------------------------------------------- parsing


class _FoldParser(_Parser):
    """The `.alg` parser, except that a gate call stays one instruction (`kind="call"`)."""

    def __init__(self, alg: Algorithm, born=(), cvars=()):
        super().__init__(alg)
        self.born |= set(born)
        self.cvars |= set(cvars)

    def emit(self, word, args, cvar, at, guard, written, lineno, raw, call):
        if word not in self.alg.gates:
            return self.primitive(word, args, cvar, at, guard, written, lineno, raw, call)
        gate = self.alg.gates[word]
        if cvar:
            raise AlgorithmError(lineno, raw, f"gate {gate.name} names no outcome")
        if len(args) != len(gate.params):
            raise AlgorithmError(lineno, raw, f"gate {gate.name} takes {len(gate.params)} "
                                 f"block(s): {' '.join(gate.params)}")
        if len(set(args)) != len(args):
            raise AlgorithmError(lineno, raw, f"gate {gate.name} needs distinct blocks")
        for b in args:
            if b not in self.born:
                raise AlgorithmError(lineno, raw, f"block {b} is used before prep or inject")
            if b in self.gone:
                raise AlgorithmError(lineno, raw, f"block {b} was already read out")
        if guard is not None:
            unknown = [n for n in guard.names if n not in self.cvars]
            if unknown:
                raise AlgorithmError(lineno, raw, f"the condition reads {unknown[0]}, which no "
                                     f"earlier instruction measured")
        ins = Instr(index=len(self.alg.instructions), line=lineno, text=raw, kind="call",
                    blocks=list(args), at=at, call=list(call), gate=gate.name)
        if call and call[-1].get("body") is not None:
            ins.src_line = call[-1]["body"]
        if guard is not None:
            ins.cond, ins.guard = guard, guard.text
        self.alg.instructions.append(ins)


def parse_folded(source: str, name: str = "algorithm") -> Algorithm:
    """A program whose gate calls are kept: `kind == "call"`, `gate` the gate's name."""
    alg = Algorithm(name=name, title=name, source=source)
    _FoldParser(alg).read(source)
    if not alg.instructions:
        raise AlgorithmError(0, "", "no instructions")
    return alg


def body_algorithm(top: Algorithm, gate: str, n: int, guard: str | None, ext: list) -> Algorithm:
    """One call of `gate`, expanded one level: its blocks are `arg0..`, its outer guard reads
    `ext` (outcomes measured before the call), and the calls in its body stay calls."""
    g = top.gates[gate]
    alg = Algorithm(name=gate, title=f"gate {gate}", source="")
    alg.gates = top.gates
    alg.floorplan = top.floorplan
    args = [f"arg{i}" for i in range(n)]
    p = _FoldParser(alg, born=args, cvars=ext)
    p.expand(g, args, None, None, parse_guard(guard) if guard else None, g.line,
             f"{gate} {' '.join(args)}", [])
    # a macro decodes what it has left before it returns (skipped if nothing is waiting)
    alg.instructions.append(Instr(index=len(alg.instructions), line=0, text="decode",
                                  kind="decode", blocks=[], refresh=True))
    return alg


# --------------------------------------------------------------------------- calendars


class _IV(list):
    """A calendar's intervals; removing one (an open lease being closed) resets its end."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def remove(self, x):
        super().remove(x)
        self.owner._end = None


class FastCalendar(Calendar):
    """A calendar whose end is kept, not recomputed: a root calendar holds every box."""

    def __init__(self):
        self.iv = _IV(self)
        self._end = 0.0

    def reserve(self, t0: float, t1: float) -> None:
        super().reserve(t0, t1)
        if self._end is not None:
            self._end = max(self._end, t1)

    @property
    def end(self) -> float:
        if self._end is None:
            self._end = max((b for _, b in self.iv), default=0.0)
        return self._end


def _merge(iv: list) -> list:
    """The union of `iv`, then its smallest gaps closed until at most MAX_INTERVALS remain."""
    out: list = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    while len(out) > MAX_INTERVALS:
        gaps = sorted(range(len(out) - 1), key=lambda i: out[i + 1][0] - out[i][1])
        drop = set(gaps[:len(out) - MAX_INTERVALS])
        merged = []
        for i, iv_ in enumerate(out):
            if merged and (i - 1) in drop:
                merged[-1][1] = iv_[1]
            else:
                merged.append(list(iv_))
        out = merged
    return [(a, b) for a, b in out]


# --------------------------------------------------------------------------- macros


@dataclass
class Macro:
    id: str
    gate: str
    homes: list
    guard: str | None
    ext: list
    gir: dict
    calls: list
    footprint: dict                        # ("leaf"|"net", path) -> [(a, b)], relative
    span: float
    free: list                             # per block: when it is free again, relative
    mem_last: list                         # per block: when its home's last op ends
    out_pf: list                           # per block: its frame on return, over in:/ext:/I/e
    guarded: list                          # per block: a guarded frame write landed on it
    fresh: int                             # ions it loads, its calls' included
    instructions: int                      # instructions it runs, its calls' included
    own: int                               # instructions of its own body
    #: per block: the gate read it out (a destructive readout) -- it does not come back
    gone: list = field(default_factory=list)


@dataclass
class Folder:
    """What every macro of one program shares: the program, the machine, the homes, the
    macros made so far."""
    top: Algorithm
    lib: object
    leaves: object
    homes: dict = field(default_factory=dict)          # block -> its home (the root's blocks)
    macros: dict = field(default_factory=dict)         # context -> Macro
    patterns: dict = field(default_factory=dict)       # memory -> one hold round's classical half
    log: object = None
    made: int = 0
    reused: int = 0
    #: a gate may leave a block it made alive, resting in its memory when it returns (the
    #: scale compile harness: a template whose ancilla the NEXT template of its gadget reads);
    #: off, such a call is refused
    outlive: bool = False
    #: where a merge's syndrome rounds run (`City.rounds_at`)
    rounds_at: str = "clinic"

    @property
    def home_places(self) -> set:
        return set(self.homes.values())

    def macro(self, gate, homes, guard, ext, taken, depth) -> Macro:
        key = (gate, tuple(homes), guard, tuple(sorted(taken)))
        m = self.macros.get(key)
        if m is not None:
            self.reused += 1
            return m
        if depth > 64:
            raise FoldError(f"gate {gate} calls itself")
        alg = body_algorithm(self.top, gate, len(homes), guard, ext)
        city = MacroCity(alg, self.lib, self.leaves, folder=self, params=list(homes),
                         ext=ext, taken=taken, depth=depth)
        m = city.macro("", gate, list(homes), guard, ext)
        # numbered when finished: the calls inside it were finished, and numbered, first
        m.id = f"m{len(self.macros)}"
        self.macros[key] = m
        self.made += 1
        return m


class MacroCity(City):
    """The town's scheduler on one body: calls are boxes, blocks live in homes."""

    def __init__(self, alg, lib, leaves, *, folder: Folder, params=(), ext=(), taken=(),
                 depth: int = 0, homes: dict | None = None):
        super().__init__(alg, lib, leaves, log=folder.log, parallel=True, rounds_at=folder.rounds_at)
        self.folder, self.depth = folder, depth
        self.cal = {p: FastCalendar() for p in self.leaf}
        self.net_cal = {p: FastCalendar() for p in self.nets}
        self._share_planes()              # (the calendars are new: share them again)
        #: every block of this body that has a home, and the homes of the whole program,
        #: which no block of this body may rest in
        self.home_of: dict = dict(homes or {})
        self.no_rest = set(folder.home_places) | set(params) | set(taken)
        self.calls: list = []
        self.n_instr = 0
        #: refresh rounds this body added: (the instruction they go before, or None, the round)
        self.extra: list = []
        self.next_index = len(alg.instructions)
        #: block resting in a memory -> when its last syndrome round there ended
        self.rest_since: dict = {}
        self.current = None
        self.skipped: set = set()
        self.holds: list = []
        # a block is never kept at a door for a call: a call finds its blocks at home
        for k, v in list(self.next_use.items()):
            if v is not None and alg.instructions[v].kind == "call":
                self.next_use[k] = None
        self.params = []
        for i, home in enumerate(params):
            name = f"arg{i}"
            gids = [PARAM_GID + 9 * i + j for j in range(9)]
            b = Bundle(name, gids, home, "in", [])
            b.resting, b.lease = True, (0.0, math.inf)
            self.cal[home].reserve(0.0, math.inf)
            self.blocks[name] = b
            self.zone_holder[home] = name
            self.home_of[name] = home
            self.block_info[name] = {"ions": gids, "born": 0.0, "born_at": home, "code": "surface_d3",
                                     "n": 9, "k": 1, "param": i}
            self.pf[name] = ((0, frozenset({(f"in:{i}", "x")})), (0, frozenset({(f"in:{i}", "z")})))
            self.params.append(name)
            if self.family(home) == "memory":
                self.rest_since[name] = 0.0
        for k, x in enumerate(ext):
            # the outer guard: decided before the call starts
            self.cvar_time[x] = 0.0
            self.cvar_value[x] = (0, frozenset({(f"ext:{k}", "guard")}))

    # -- resting: a block with a home rests there, any other in a free place no home is ----

    def park(self, bundle: Bundle, t_min, ins, grp, *, at=None, stop_ok: bool = True):
        Z = at if at is not None else self.home_of.get(bundle.name)
        if at is not None:
            pass                                     # a cooling stop on the way (City.bring)
        elif Z is None:
            free = [p for p, h in self.zone_holder.items() if h is None and p not in self.no_rest]
            if not free:
                raise FoldError(f"no free place for {bundle.name} to rest in: every memory and "
                                f"zone is a block's home or taken")
            kind = "memory" if any(self.family(p) == "memory" for p in free) else "zone"
            Z = self._choose(kind, _ANY, near=(bundle.leaf, bundle.port), ready=t_min,
                             free=lambda p: p in free, gids=bundle.gids)
        elif self.zone_holder.get(Z) not in (None, bundle.name):
            raise FoldError(f"{bundle.name}'s home {Z} holds {self.zone_holder[Z]}")
        kind = self.family(Z)
        in_op = "store" if kind == "memory" else "check_in"
        gids, arr, trip = self.bring(bundle, (Z, "in"), t_min, ins, grp, stop_ok=stop_ok)
        s, _, row = self.op(Z, in_op, {"in": (gids, arr, trip)}, t_min, ins, grp)
        if kind == "memory":
            self.mem_last[Z] = row[2]
            self.report(Z, in_op, row, ins, grp, blocks=[bundle.name])
        bundle.leaf, bundle.port, bundle.deps = Z, "in", []
        bundle.resting = True
        self.zone_holder[Z] = bundle.name
        bundle.lease = (row[1], math.inf)
        self.cal[Z].reserve(*bundle.lease)
        if kind == "memory":
            self.rest_since[bundle.name] = row[2]
        return row[2]

    # -- refresh: a block resting in a memory is held there, a syndrome round as often as G8 asks

    def cover(self, q: str, until: float, before) -> None:
        """Hold `q` in its memory from its last round there until `until`: a syndrome round
        every REFRESH_EVERY_US.  One row, a **hold**, says so (block, memory, first, period,
        rounds) and one `se` instruction placed before `before` stands for all its rounds:
        a block resting through a three-hour call is a few hundred bytes, not a million rows.

        The memory is the block's alone while it rests (its lease), so its rounds take nothing
        anyone else could; each round's syndrome is decoded in the decoder's free time (a
        decode is a microsecond, a round 13 ms), which `expand` places, and checks with every
        other row."""
        since = self.rest_since.get(q)
        if since is None:
            return
        b = self.blocks[q]
        M = b.leaf
        O = self.leaf[M].master.ops["refresh"]
        every = REFRESH_EVERY_US + O.duration_us
        g = self._place_group.get(M)
        if g is not None:
            # a memory on a shared plane (planes.py): each round takes its turn on the plane --
            # the first gap once REFRESH_EVERY_US has passed since the last ended -- and is a
            # hold of one round, so no two members ever refresh over each other (H2)
            rounds, t = [], since
            while True:
                s = self._plane_fit(g, t + REFRESH_EVERY_US, O.duration_us)
                if s + O.duration_us > until + 1e-9:
                    break
                self._plane_take(g, s, s + O.duration_us)
                rounds.append(s)
                t = s + O.duration_us
            if rounds:
                names = self.leaf[M].master.ops["release"].transfer("out", "out").ions
                ins = Instr(index=self.next_index, line=before.line if before else 0,
                            text=f"se {q} 1    # refresh: {len(rounds)} round"
                                 f"{'s' if len(rounds) > 1 else ''} in {M}, each when its plane is free",
                            kind="se", blocks=[q], rounds=1,
                            not_before=round(rounds[0], 3), refresh=True)
                self.next_index += 1
                self.extra.append((before.index if before else None, ins))
                bind = ([[nm, gg] for nm, gg in self.leaf[M].ids.items()]
                        + [[nm, gg] for nm, gg in zip(names, b.gids)])
                for s in rounds:
                    self.holds.append({"block": q, "place": M,
                                       "from": round(s + O.duration_us - every, 3),
                                       "every": round(every, 3), "n": 1, "round_us": O.duration_us,
                                       "ins": ins.index, "bind": bind})
                self.hold_pattern(M)
                since = t
                self.mem_last[M] = max(self.mem_last.get(M, 0.0), since)
            self.rest_since[q] = since
            return
        n = int((until - since) // every)
        if n >= 1:
            names = self.leaf[M].master.ops["release"].transfer("out", "out").ions
            ins = Instr(index=self.next_index, line=before.line if before else 0,
                        text=f"se {q} 1    # refresh: {n} round{'s' if n > 1 else ''} in {M}, "
                             f"one every {every / 1000:.0f} ms",
                        kind="se", blocks=[q], rounds=1,
                        not_before=round(since + REFRESH_EVERY_US, 3), refresh=True)
            self.next_index += 1
            self.extra.append((before.index if before else None, ins))
            self.holds.append({"block": q, "place": M, "from": round(since, 3), "every": round(every, 3),
                               "n": n, "round_us": O.duration_us, "ins": ins.index,
                               "bind": [[nm, g] for nm, g in self.leaf[M].ids.items()]
                               + [[nm, g] for nm, g in zip(names, b.gids)]})
            self.hold_pattern(M)
            since += n * every
            self.mem_last[M] = max(self.mem_last.get(M, 0.0), since)
        self.rest_since[q] = since

    def hold_pattern(self, M: str) -> dict:
        """One round's classical half at memory `M`: when its syndrome exists, the wire to the
        decoder, the decode, the wire to the archive, the write."""
        pats = self.folder.patterns
        if M not in pats:
            mname = self.leaf[M].master.name
            syn = (self.wire_of.get((M, "syn")) or [None])[0]
            frame = (self.wire_of.get((self.decoder, "frame")) or [None])[0]
            ports = {p.signal: p for p in self.leaf[M].master.ports if p.kind == "classical"}
            pats[M] = {"meas_us": self.last_measure_us(mname, "refresh"),
                       "bits": ports["syndrome"].bits if "syndrome" in ports else 0,
                       "syn": list(syn) if syn else None, "frame": list(frame) if frame else None,
                       "decoder": self.decoder, "archive": self.archive,
                       "decode_us": self.leaf[self.decoder].master.ops["decode"].duration_us,
                       "update_us": self.leaf[self.archive].master.ops["update"].duration_us}
        return pats[M]

    def bring(self, bundle: Bundle, dst, t_min, ins, grp, *, stop_ok: bool = True):
        if bundle.resting and bundle.name in self.rest_since:
            Z = bundle.leaf
            t = t_min
            if self.family(dst[0]) not in ("zone", "memory"):
                out_op = self.leaf[Z].master.ops["release"]
                busy = self.cal[dst[0]].end if dst[0] not in self.junction_paths else 0.0
                t = max(t, busy - self._distance((Z, "out"), dst) - out_op.duration_us)
            self.cover(bundle.name, max(t, self.mem_last.get(Z, 0.0)), self.current)
            del self.rest_since[bundle.name]
        return super().bring(bundle, dst, t_min, ins, grp, stop_ok=stop_ok)

    # -- instructions ---------------------------------------------------------------------

    def run(self):
        for ins in self.alg.instructions:
            try:
                self.instruction(ins)
            except CityError as exc:
                self.refused.append([ins.index, str(exc)])
                self.log(f"  instruction {ins.index} refused: {exc}")
        return self

    def instruction(self, ins: Instr):
        self.current = ins
        if ins.kind == "call":
            return self.place_call(ins)
        if ins.kind == "decode" and ins.refresh and ins.line == 0:
            # the closing decode: only if something is still waiting
            if not any(w["decoded_by"] is None for w in self.windows):
                self.skipped.add(ins.index)
                return
        self.n_instr += 1
        return super().instruction(ins)

    def place_call(self, ins: Instr):
        grp = f"i{ins.index}"
        args = list(ins.blocks)
        for b in args:
            bundle = self.blocks[b]
            if not bundle.resting:
                self.park(bundle, self.block_free.get(b, 0.0), ins.index, grp)
            if b not in self.home_of:
                # a block of this body passed on: where it rests is its home for the call
                self.home_of[b] = self.blocks[b].leaf
        homes = [self.blocks[b].leaf for b in args]
        ext, guard = [], None
        if ins.cond is not None:
            names = list(ins.cond.names)
            ext = [f"x{k}" for k in range(len(names))]
            guard = ins.cond.rename(dict(zip(names, ext))).text
        taken = sorted(p for p, h in self.zone_holder.items()
                       if h is not None and p not in homes and p not in self.folder.home_places)
        m = self.folder.macro(ins.gate, homes, guard, ext, taken, self.depth + 1)
        t = max([self.block_free.get(b, 0.0) for b in args]
                + [self.mem_last.get(h, 0.0) for h in homes] + [0.0])
        if ins.cond is not None:
            t = max([t] + [self.decoded_by(n, "the call's guard") for n in ins.cond.names])
        t0 = self.fit(m, t)
        for (kind, path), ivs in m.footprint.items():
            cal = self._resource(kind, path)
            for a, b in ivs:
                cal.reserve(t0 + a, t0 + b)
        for b in args:
            # its rest here ends where the call's begins: the call refreshes it from there
            self.cover(b, t0, ins)
            self.rest_since.pop(b, None)
        inst = f"c{len(self.calls)}"
        names = list(ins.cond.names) if ins.cond is not None else []
        self.calls.append({"id": inst, "m": m.id, "t0": round(t0, 3), "args": args,
                           "gid": self.next_gid, "ins": ins.index, "line": ins.line,
                           "guard": ins.guard, "gate": ins.gate,
                           # what the call's symbols stand for: its blocks' frames and its
                           # guard's outcomes, as this body held them when it started
                           "pf_in": [self.frame(b) for b in args],
                           "ext_in": [self.cvar_value[n] for n in names],
                           # and the names its guard's outcomes have here (`x0`, ... inside)
                           "ext_names": names})
        self.next_gid += m.fresh
        self.n_instr += m.instructions
        for i, b in enumerate(args):
            h = homes[i]
            self.block_free[b] = max(self.block_free.get(b, 0.0), t0 + m.free[i])
            self.mem_last[h] = max(self.mem_last.get(h, 0.0), t0 + m.mem_last[i])
            if m.gone and m.gone[i] and ins.cond is not None:
                # a guarded call may not fire: the block stays for the branch after it that
                # reads it out the other way (an adaptive basis); both reserve their places
                if self.family(h) == "memory":
                    self.rest_since[b] = t0 + m.mem_last[i]
                continue
            if m.gone and m.gone[i]:
                # read out inside: the block is no more, and its home is empty again
                for d in (self.blocks, self.pf, self.rest_since, self.home_of):
                    d.pop(b, None)
                if self.zone_holder.get(h) == b:
                    self.zone_holder[h] = None
                if b in self.block_info:
                    # (read inside the call: its own view follows its ions to the depot)
                    self.block_info[b]["read_in"] = inst
                continue
            if self.family(h) == "memory":
                self.rest_since[b] = t0 + m.mem_last[i]
            ax, az = m.out_pf[i]
            self.pf[b] = (self.subst(ax, inst, args, names), self.subst(az, inst, args, names))
            if m.guarded[i]:
                self.pf_guarded.setdefault(b, []).append({"text": f"inside {ins.gate}"})
        self.t_now = max(self.t_now, t0 + m.span)

    def _resource(self, kind, path):
        """A footprint's calendar: a place's or junction's, a road's, or a shared plane's."""
        if kind == "leaf":
            return self.cal[path]
        if kind == "net":
            return self.net_cal[path]
        return self.plane_cal[path]

    def fit(self, m: Macro, t: float) -> float:
        """The first start, no earlier than `t`, at which every interval of the footprint is
        free on this body's calendars."""
        items = [(self._resource(k, p), p, a, b)
                 for (k, p), ivs in m.footprint.items() for a, b in ivs]
        for _ in range(100_000):
            shift = 0.0
            for cal, p, a, b in items:
                got = cal.earliest(t + a, b - a)
                if got == math.inf:
                    raise FoldError(f"{m.gate} needs {p}, which a resting block holds")
                # A CONFLICT IS ONE PAST THE TOLERANCE.  A rounding difference (1e-11 us) used
                # to end the scan here, and the test below then took it for "no shift" and
                # returned a start the rest of the footprint had not been checked at: the
                # composition check (`check_composition`) found 15 such boxes in FormalRV's Shor.
                if got - (t + a) > 1e-9:
                    shift = got - (t + a)
                    break
            if shift <= 1e-9:
                return t
            t += shift
        raise FoldError(f"no time at which {m.gate}'s places are free")

    def subst(self, expr, inst, args, names):
        """A macro's parity, in this body's terms: its inputs' frames and its guard's outcomes
        replaced by this body's, its own outcomes named by the call."""
        c, out = expr[0], frozenset()
        for src, flow in expr[1]:
            if isinstance(src, str) and src.startswith("in:"):
                ax, az = self.frame(args[int(src[3:])])
                part = ax if flow == "x" else az
                c ^= part[0]
                out = out ^ part[1]
            elif isinstance(src, str) and src.startswith("ext:"):
                v = self.cvar_value[names[int(src[4:])]]
                c ^= v[0]
                out = out ^ v[1]
            else:
                out = out ^ frozenset({(f"{inst}/{src}", flow)})
        return (c, out)

    # -- the result -----------------------------------------------------------------------

    def gir(self) -> dict:
        """The town's GIR, the refresh rounds in program order (each before the instruction
        that needed its block next), and the calls' symbols renumbered with the events."""
        before = [(e, e[0]) for e in self.events]
        g = super().gir()
        remap = {old: e[0] for e, old in before}
        extra: dict = {}
        for anchor, ins in self.extra:
            extra.setdefault(anchor, []).append(ins.to_json())
        prog = []
        for d in g["program"]["instructions"]:
            if d["id"] in self.skipped:
                continue
            prog += extra.pop(d["id"], [])
            prog.append(d)
        for rows in extra.values():
            prog += rows
        g["program"] = dict(g["program"], instructions=prog)
        g["refreshes"] = sum(h["n"] for h in self.holds)
        g["holds"] = self.holds

        def js(expr):
            return {"const": expr[0],
                    "sources": [[remap[s] if isinstance(s, int) else s, f]
                                for s, f in sorted(expr[1], key=_source_key)]}
        for c in self.calls:
            c["pf_in"] = [[js(ax), js(az)] for ax, az in c["pf_in"]]
            c["ext_in"] = [js(v) for v in c["ext_in"]]
        return g

    def macro(self, mid, gate, homes, guard, ext) -> Macro:
        self.run()
        if self.refused:
            i, why = self.refused[0]
            raise FoldError(f"in gate {gate}, instruction {i} "
                            f"({self.alg.instructions[i].text.strip()}): {why}")
        alive = [b for b in self.blocks if b not in self.params]
        if alive and not self.folder.outlive:
            raise FoldError(f"gate {gate} leaves {', '.join(sorted(alive))} alive: a call may "
                            f"not outlive the blocks it makes")
        gir = self.gir()
        homes_set = set(homes)
        fp = {}
        for p, cal in self.cal.items():
            ivs = [(a, b) for a, b in cal.iv if b != math.inf]
            if p in homes_set or not ivs:
                continue
            fp[("leaf", p)] = _merge(ivs)
        for p, cal in self.net_cal.items():
            if cal.iv:
                fp[("net", p)] = _merge(list(cal.iv))
        # what the gate's work holds of each shared plane (a group of them): its calls never
        # overlap another's there, nor the caller's own (planes.py)
        for grp, cal in self.plane_cal.items():
            ivs = [(a, b) for a, b in cal.iv if b != math.inf]
            if ivs:
                fp[("plane", grp)] = _merge(ivs)
        span = max([b for ivs in fp.values() for _, b in ivs]
                   + [gir["makespan_us"]] + [self.block_free.get(b, 0.0) for b in self.params]
                   + [self.mem_last.get(h, 0.0) for h in homes] + [0.0])
        own = sum(1 for i in self.alg.instructions if i.kind != "call")
        return Macro(id=mid, gate=gate, homes=list(homes), guard=guard, ext=list(ext), gir=gir,
                     calls=self.calls, footprint=fp, span=span,
                     free=[self.block_free.get(b, 0.0) for b in self.params],
                     mem_last=[self.mem_last.get(h, 0.0) for h in homes],
                     out_pf=[self.pf.get(b) for b in self.params],
                     guarded=[bool(self.pf_guarded.get(b)) for b in self.params],
                     gone=[b not in self.blocks for b in self.params],
                     fresh=self.next_gid - self.n_residents, instructions=self.n_instr, own=own)


# --------------------------------------------------------------------------- the program


def _call_blocks(alg: Algorithm) -> list:
    out: list = []
    for ins in alg.instructions:
        if ins.kind == "call":
            for b in ins.blocks:
                if b not in out:
                    out.append(b)
    return out


def schedule_folded(alg: Algorithm, lib, leaves, *, log=None, keep: dict | None = None,
                    outlive: bool = False, rounds_at: str = "clinic") -> dict:
    """Schedule a folded program (`parse_folded`).  Returns GIR 0.3.  With `keep`, the
    macros themselves (their footprints, which GIR 0.3 does not carry) are left in
    `keep["folder"]`, for `check_composition`.  `outlive`: see `Folder.outlive`."""
    folder = Folder(alg, lib, leaves, log=log or (lambda *a, **k: None), outlive=outlive,
                    rounds_at=rounds_at)
    if keep is not None:
        keep["folder"] = folder
    probe = MacroCity(alg, lib, leaves, folder=folder)
    rest = sorted((p for p, h in probe.zone_holder.items() if probe.family(p) == "memory"),
                  key=lambda p: (len(p), p))
    need = _call_blocks(alg)
    if len(need) > len(rest):
        raise FoldError(f"the program passes {len(need)} blocks to its gates, and each lives in a "
                        f"memory of its own; the machine has {len(rest)}")
    folder.homes = dict(zip(need, rest))
    # the program decodes what it has left before it ends (skipped if nothing is waiting)
    alg.instructions.append(Instr(index=len(alg.instructions), line=0, text="decode",
                                  kind="decode", blocks=[], refresh=True))
    root = MacroCity(alg, lib, leaves, folder=folder, homes=folder.homes)
    root.run()
    # a block nothing uses again rests, refreshed, until the program ends
    end = max([e[2] for e in root.events]
              + [c["t0"] + _span(folder, c["m"]) for c in root.calls] + [0.0])
    for q in list(root.rest_since):
        root.cover(q, end, None)
    gir = root.gir()
    makespan = max([gir["makespan_us"]]
                   + [c["t0"] + _span(folder, c["m"]) for c in root.calls])
    gir.update({
        "schema": GIR_FOLDED,
        "makespan_us": round(makespan, 3),
        "calls": root.calls,
        "homes": folder.homes,
        "hold_patterns": folder.patterns,
        "macros": {m.id: _macro_json(m) for m in folder.macros.values()},
        "folded": {"macros": folder.made, "reused": folder.reused,
                   "instructions": root.n_instr,
                   "own": sum(m.own for m in folder.macros.values())
                   + sum(1 for i in alg.instructions if i.kind != "call")},
        "n_ions": root.next_gid,
    })
    return gir


def _span(folder: Folder, mid: str) -> float:
    for m in folder.macros.values():
        if m.id == mid:
            return m.span
    return 0.0


def _macro_json(m: Macro) -> dict:
    g = m.gir
    return {"gate": m.gate, "homes": m.homes, "guard": m.guard, "ext": m.ext,
            "span_us": round(m.span, 3), "fresh": m.fresh, "instructions": m.instructions,
            "own": m.own, "free": [round(t, 3) for t in m.free],
            "mem_last": [round(t, 3) for t in m.mem_last],
            "program": g["program"], "events": g["events"], "carries": g["carries"],
            "messages": g["messages"], "windows": g["windows"], "frames": g["frames"],
            "outcomes": g["outcomes"], "blocks": g["blocks"], "refused": g["refused"],
            "holds": g["holds"], "calls": m.calls,
            # a road's hold and its span on a shared plane (planes.py), when it has them
            **{k: g[k] for k in ("road_holds", "road_spans") if g.get(k)},
            # what the macro holds, per resource, as one hull: the page's "busy" bars
            "uses": {f"{k}:{p}": [round(ivs[0][0], 3), round(ivs[-1][1], 3)]
                     for (k, p), ivs in m.footprint.items()}}


# --------------------------------------------------------------------------- the page


def line_first(g: dict) -> dict:
    """Program line -> the first time an instruction written on it starts (a gate body's
    line, for what a gate ran), over the whole run: the page's "click a line, land there"
    for a run it never holds whole.  Per macro once, relative, then shifted up the calls."""
    memo: dict = {}

    def own(body):
        start: dict = {}
        for e in body["events"]:
            start[e[6]] = min(start.get(e[6], math.inf), e[1])
        out: dict = {}
        for ins in body["program"]["instructions"]:
            if ins["kind"] == "call" or ins["id"] not in start:
                continue
            key = ins.get("src") or ins["line"]
            out[key] = min(out.get(key, math.inf), start[ins["id"]])
        return out

    def rel(mid):
        if mid not in memo:
            m = g["macros"][mid]
            out = own(m)
            for c in m["calls"]:
                for k, t in rel(c["m"]).items():
                    out[k] = min(out.get(k, math.inf), c["t0"] + t)
            memo[mid] = out
        return memo[mid]
    out = own(g)
    for c in g["calls"]:
        for k, t in rel(c["m"]).items():
            out[k] = min(out.get(k, math.inf), c["t0"] + t)
    return {k: round(t, 3) for k, t in out.items()}


def build_folded(alg, *, out_dir, cache_dir="out/gadgets/cache", leaves=None, log=print,
                 floorplan=None, stack: dict | None = None, expand_limit: int = 30_000) -> dict:
    """Schedule a program folded and write its page (`index.html`, `leaf/`, `macro/`).

    A run of at most `expand_limit` instructions is also expanded, and every place check and
    machine rule runs on the expansion; a larger one is not expanded here, and the page says
    so."""
    import json as _json
    import time as _time
    from pathlib import Path as _Path
    from .build import published
    from .categories import CATEGORIES, ORDER
    from .floorplan import FLOORPLANS_DIR, load_file
    from .judge import judge_schedule
    from .library import LeafLibrary
    from .page import render_page
    from .place_checks import PLACE_RULES, check_places
    log = log or (lambda *a, **k: None)
    t_start = _time.time()
    p = _Path(str(alg))
    if not isinstance(alg, Algorithm):
        alg = parse_folded(p.read_text(encoding="utf-8"), name=p.stem)
    leaves = leaves or LeafLibrary(cache_dir, log=log)
    floorplan = floorplan or FLOORPLANS_DIR / f"{alg.floorplan or 'fullstack'}.floor"
    lib, fp = load_file(floorplan, leaves)
    t0 = _time.time()
    keep: dict = {}
    g = schedule_folded(alg, lib, leaves, log=log, keep=keep)
    t_sched = _time.time() - t0
    n = g["folded"]["instructions"]
    rules, expanded = None, None
    t0 = _time.time()
    # every body once, in its own view, and the composition from the rows and footprints
    checks = check_folded(g, lib, leaves, log=log)
    comp = check_composition(g, keep["folder"])
    if comp:
        if "G5" in checks["passed"]:
            checks["passed"].remove("G5")
            checks["failed"].append("G5")
        checks["violations"].setdefault("G5", []).extend(f"composition: {p}" for p in comp)
    checks["metrics"]["composition_problems"] = len(comp)
    checks["metrics"]["checked"] = "per view"
    if n <= expand_limit:
        # small enough to expand as well: the whole schedule, every check and machine rule,
        # and the per-view verdict must say the same
        expanded = expand(g)
        whole = check_places(expanded, lib, leaves)
        rules = judge_schedule(lib, leaves, expanded, regions=fp.regions)
        whole["metrics"].update(expanded_instructions=len(expanded["program"]["instructions"]),
                                checked="expanded and per view",
                                per_view_agrees=sorted(whole["failed"]) == sorted(checks["failed"]),
                                views=checks["metrics"]["views"], composition_problems=len(comp))
        checks = whole
    t_check = _time.time() - t0
    out = _Path(out_dir)
    (out / "leaf").mkdir(parents=True, exist_ok=True)
    g["line_first"] = line_first(g)
    # every carry's compiled road program (the one H1 judged), for the page to replay
    from .roads import attach_runs
    attach_runs(g, lib, leaves)
    root, files = page_data(g, out)
    # the run as TSIR modules beside the page (modules.py): what the Stack cites, file:line
    from .modules import write_modules
    hw = write_modules(g, out / "hw", leaves=leaves, scripts=True)
    root["hw"] = {"dir": "hw/", "program": "program.tsir",
                  "gadgets": {mid: f"gadget/{mid}.tsir" for mid in g["macros"]},
                  "leaves": {f[5:-5]: f for f in hw["files"] if f.startswith("leaf/")}}
    root["stats"] = {
        "ions": g["n_ions"], "instructions": n, "makespan_ms": round(g["makespan_us"] / 1000, 3),
        "macros": g["folded"]["macros"], "reused": g["folded"]["reused"],
        "refused": len(g["refused"]), "refreshes": g.get("refreshes", 0),
        "seconds": {"schedule": round(t_sched, 2), "checks": round(t_check, 2)},
        "rules": ({"passed": len(rules["passed"]), "failed": rules["failed"], "total": len(rules["rules"])}
                  if rules else None),
    }
    from .build import write_leaf_files
    leaf_files, _ = write_leaf_files(out, lib, leaves, g)
    (out / f"{alg.name}.alg").write_text(alg.source, encoding="utf-8")
    render_page(out / "index.html", lib=lib, gir=root, checks=checks, leaf_files=leaf_files,
                rules=PLACE_RULES, machine_rules=rules,
                floorplan=_Path(floorplan).read_text(encoding="utf-8"),
                title=f"{alg.title} — QCCD verified gadgets (folded)",
                categories=CATEGORIES, category_order=ORDER, stack=stack, macro_files=files)
    root["stats"]["seconds"]["total"] = round(_time.time() - t_start, 2)
    log(f"  {alg.name}: {n:,} instructions as {g['folded']['macros']} macros, makespan "
        f"{g['makespan_us'] / 1e6:.2f} s, scheduled in {t_sched:.1f} s"
        + (f"; G {len(checks['passed'])} passed, rules {len(rules['passed'])}/{len(rules['rules'])}"
           if rules else "; not expanded"))
    return {"out": str(out), "gir": g, "stats": root["stats"], "checks": checks, "rules": rules,
            "expanded": expanded, "library": lib, "modules": hw["files"]}


def page_data(g: dict, out_dir) -> tuple[dict, dict]:
    """(the root as the page carries it inline, {macro id: its file}).  Each macro is written
    to `macro/<id>.js` beside the page, a script that hands it to the page
    (`window.__gadgetMacro`), so the page loads a macro only when the time on screen needs
    it, from `file://` as well as over HTTP."""
    import json as _json
    from pathlib import Path as _Path
    d = _Path(out_dir) / "macro"
    d.mkdir(parents=True, exist_ok=True)
    files = {}
    for mid, m in g["macros"].items():
        body = _json.dumps(m, separators=(",", ":")).replace("</", "<\\/")
        (d / f"{mid}.js").write_text(f"window.__gadgetMacro&&window.__gadgetMacro({_json.dumps(mid)},{body});\n",
                                     encoding="utf-8")
        files[mid] = f"macro/{mid}.js"
    root = {k: v for k, v in g.items() if k != "macros"}
    root["spans"] = {mid: m["span_us"] for mid, m in g["macros"].items()}
    root["gates"] = {mid: m["gate"] for mid, m in g["macros"].items()}
    return root, files


# --------------------------------------------------------------------------- expansion


_WORD = re.compile(r"\b([A-Za-z_][\w.]*)\b")


class _Expander:
    """GIR 0.3 -> GIR 0.2, in two passes: instructions in program order (a call's body where
    the call was), then every body's rows at its time, on its ions, its symbols replaced by
    what its caller held when it called."""

    def __init__(self, g: dict, *, own: bool = False):
        self.g = g
        self.macros = g["macros"]
        self.n_res = g["n_residents"]
        #: one body's own rows only: its calls stay instructions, nothing is expanded (`view`)
        self.own = own
        self.ins_out: list = []
        self.imaps: dict = {}              # body path -> {its instruction id: the program's}
        self.ev, self.ca, self.me, self.wi, self.fr, self.oc = [], [], [], [], [], []
        self.blocks: dict = {}
        self.holds: list = []

    # -- pass 1: instructions --------------------------------------------------------------
    def number(self, prog, calls, names, cv, path, chain=()):
        """`chain`: the calls this body is inside, outermost first: `{gate, call, line}`,
        `line` the call's own line in the program file."""
        imap = self.imaps.setdefault(path, {})
        by_ins = {c["ins"]: c for c in calls}
        for ins in prog["instructions"]:
            if ins["kind"] == "call" and not self.own:
                c = by_ins[ins["id"]]
                m = self.macros[c["m"]]
                cid = f"{path}{c['id']}"
                nm, ncv = self._names(c, names, cid, cv)
                frame = {"gate": c["gate"], "call": cid, "line": ins.get("src") or ins["line"],
                         "guard": c.get("guard")}
                self.number(m["program"], m["calls"], nm, ncv, cid + "/", chain + (frame,))
                continue
            k = len(self.ins_out)
            imap[ins["id"]] = k
            own = set(ins["blocks"])
            outs = set((ins.get("cond") or {}).get("cvars") or []) | ({ins["cvar"]} if ins.get("cvar") else set())

            def ren(text, own=own, outs=outs, names=names, cv=cv):
                return _WORD.sub(lambda mm: names(mm[1]) if mm[1] in own else
                                 cv(mm[1]) if mm[1] in outs else mm[1], text)
            new = dict(ins, id=k, blocks=[names(b) for b in ins["blocks"]])
            new["text"] = ren(ins["text"])
            if ins.get("cvar"):
                new["cvar"] = cv(ins["cvar"])
            if ins.get("guard"):
                new["guard"] = ren(ins["guard"])
            if ins.get("cond"):
                c = dict(ins["cond"], cvars=[cv(n) for n in ins["cond"]["cvars"]],
                         text=ren(ins["cond"].get("text") or ""))
                if "all" in c:
                    c["all"] = [dict(p, cvars=[cv(n) for n in p["cvars"]]) for p in c["all"]]
                new["cond"] = c
            if chain:
                # as the flat parser writes it: the program line lit is the outermost call's,
                # and each frame says where in its gate's body the next thing is
                new["fold"] = path.rstrip("/")
                new["line"] = chain[0]["line"]
                bodies = [f["line"] for f in chain[1:]] + [ins.get("src") or ins["line"]]
                new["call"] = [dict(f, body=b) for f, b in zip(chain, bodies)]
                new["src"] = bodies[-1]
            self.ins_out.append(new)

    @staticmethod
    def _names(c, names, cid, cv):
        """A call's names in the program: its blocks are its caller's (`arg0`, ...), its
        outer guard's outcomes are its caller's (`x0`, ...), and its own are `cid.name`."""
        args = [names(a) for a in c["args"]]
        ext = {f"x{k}": cv(n) for k, n in enumerate(c.get("ext_names") or [])}

        def nm(n):
            if n.startswith("arg") and n[3:].isdigit():
                return args[int(n[3:])]
            return f"{cid}.{n}"
        return nm, (lambda n: ext.get(n) or f"{cid}.{n}")

    # -- pass 2: rows ---------------------------------------------------------------------
    def rows(self, body, calls, t0, gidmap, names, cv, path, env):
        imap = self.imaps[path]
        # a road's hold (store-and-forward on a shared plane), at program time
        for net, c0, nb in body.get("road_holds") or []:
            self.rh.append([net, round(c0 + t0, 3), round(nb + t0, 3)])

        def ii(i):
            return imap.get(i, i)

        def key(s):
            # a source of a parity, as one key across the whole program
            if isinstance(s, int):
                return (path, s)
            head, _, tail = s.rpartition("/")
            return (path + head + "/", int(tail))

        def fx(expr):
            const, srcs = expr["const"], set()
            for s, f in expr["sources"]:
                if isinstance(s, str) and (s.startswith("in:") or s.startswith("ext:")) and s not in env:
                    srcs ^= {(s, f)}           # a body's own view: its caller's, as a symbol
                elif isinstance(s, str) and (s.startswith("in:") or s.startswith("ext:")):
                    v = env[s][f] if s.startswith("in:") else env[s]
                    const ^= v[0]
                    srcs ^= v[1]
                else:
                    srcs ^= {(key(s), f)}
            return (const, frozenset(srcs))

        for e in body["events"]:
            ne = list(e)
            ne[0] = (path, e[0])
            ne[1], ne[2] = round(e[1] + t0, 3), round(e[2] + t0, 3)
            ne[6] = ii(e[6])
            ne[7] = f"i{ne[6]}"
            ne[8] = [[n, gidmap(x)] for n, x in e[8]]
            if len(ne) > 10 and isinstance(ne[10], dict) and "ready_us" in ne[10]:
                ne[10] = dict(ne[10], ready_us=round(ne[10]["ready_us"] + t0, 3))
            self.ev.append(ne)
        for c_ in body["carries"]:
            nc = list(c_)
            nc[4] = round(c_[4] + t0, 3)
            nc[7] = [gidmap(x) for x in c_[7]]
            nc[9] = ii(c_[9])
            nc[10] = f"i{nc[9]}"
            nc[11] = round(c_[11] + t0, 3)
            self.ca.append(nc)
        for m_ in body["messages"]:
            nm_ = list(m_)
            nm_[6], nm_[7] = round(m_[6] + t0, 3), round(m_[7] + t0, 3)
            nm_[10] = ii(m_[10])
            nm_[11] = f"i{nm_[10]}"
            nm_[13] = (path, m_[13]) if m_[13] >= 0 else -1
            self.me.append(nm_)
        for w in body["windows"]:
            nw = dict(w, event=(path, w["event"]), instruction=ii(w["instruction"]),
                      blocks=[names(b) for b in w["blocks"]])
            for k in ("measured_us", "sent_us", "decoded_us", "written_us"):
                if nw.get(k) is not None:
                    nw[k] = round(nw[k] + t0, 3)
            if nw.get("decode_event") is not None:
                nw["decode_event"] = (path, nw["decode_event"])
            if nw.get("decoded_by") is not None:
                nw["decoded_by"] = ii(nw["decoded_by"])
            self.wi.append(nw)
        for f in body["frames"]:
            nf = dict(f, t=round(f["t"] + t0, 3), event=(path, f["event"]),
                      instruction=ii(f["instruction"]),
                      block=names(f["block"]) if f.get("block") else f.get("block"),
                      ax=fx(f["ax"]), az=fx(f["az"]))
            if "delta" in f:
                nf["delta"] = dict(f["delta"], ax=fx(f["delta"]["ax"]), az=fx(f["delta"]["az"]))
            self.fr.append(nf)
        for o in body["outcomes"]:
            no = dict(o, event=(path, o["event"]), instruction=ii(o["instruction"]),
                      blocks=[names(b) for b in o["blocks"]],
                      cvar=cv(o["cvar"]) if o.get("cvar") else o.get("cvar"),
                      recorded=(path, o["recorded"]) if o.get("recorded") is not None else None)
            for k in ("flip", "value"):
                if o.get(k) is not None:
                    no[k] = fx(o[k])
            if o.get("parts"):
                no["parts"] = [[(path, e), f] for e, f in o["parts"]]
            for k in ("recorded_us", "at"):
                if no.get(k) is not None:
                    no[k] = round(no[k] + t0, 3)
            self.oc.append(no)
        for h in body.get("holds") or []:
            self.holds.append(dict(h, t0=t0, ins=ii(h["ins"]), block=names(h["block"]),
                                   bind=[[n, gidmap(x)] for n, x in h["bind"]]))
        for c in ([] if self.own else calls):
            m = self.macros[c["m"]]
            cid = f"{path}{c['id']}"
            nm, ncv = self._names(c, names, cid, cv)
            arg_gids = {}
            for i, a in enumerate(c["args"]):
                for j, gid in enumerate(body["blocks"][a]["ions"]):
                    arg_gids[PARAM_GID + 9 * i + j] = gidmap(gid)
            base = gidmap(c["gid"]) if c["gid"] >= self.n_res else c["gid"]

            def gm(gid, base=base, arg_gids=arg_gids):
                if gid >= PARAM_GID:
                    return arg_gids[gid]
                return gid if gid < self.n_res else base + gid - self.n_res

            sub_env = {f"in:{i}": {"x": fx(ax), "z": fx(az)} for i, (ax, az) in enumerate(c["pf_in"])}
            sub_env.update({f"ext:{k}": fx(v) for k, v in enumerate(c["ext_in"])})
            for b, info in m["blocks"].items():
                if not b.startswith("arg"):
                    self.blocks[nm(b)] = dict(info, ions=[gm(x) for x in info["ions"]],
                                              born=round(info.get("born", 0.0) + t0 + c["t0"], 3))
            self.rows(m, m["calls"], t0 + c["t0"], gm, nm, ncv, cid + "/", sub_env)

    def unroll_holds(self):
        """Every round of every hold: the memory's refresh, its window, and the window's
        decode and frame write, each fitted into the decoder's and the archive's free time
        (their own jobs are all here by now)."""
        pats = self.g.get("hold_patterns") or {}
        cals: dict = {}

        def cal(path):
            if path not in cals:
                c = Calendar()
                c.iv = sorted((e[1], e[2]) for e in self.ev if e[3] == path)
                cals[path] = c
            return cals[path]
        rounds = []
        for i, h in enumerate(self.holds):
            for k in range(1, h["n"] + 1):
                end = h["t0"] + h["from"] + k * h["every"]
                rounds.append((end - h["round_us"], i, k))
        rounds.sort()
        for s, i, k in rounds:
            h = self.holds[i]
            p = pats[h["place"]]
            ins, grp, M, blk = h["ins"], f"i{h['ins']}", h["place"], h["block"]
            key = ("hold", i, k)
            self.ev.append([key, round(s, 3), round(s + h["round_us"], 3), M, "refresh", 1, ins, grp,
                            h["bind"], []])
            t_meas = s + p["meas_us"]
            w = {"id": len(self.wi), "place": M, "op": "refresh", "event": key, "instruction": ins,
                 "measured_us": round(t_meas, 3), "bits": p["bits"], "blocks": [blk],
                 "decoded_by": ins, "sent_us": round(t_meas, 3)}
            path, (dleaf, dport), lat = p["syn"]
            self.me.append([0, path, M, "syn", dleaf, dport, round(t_meas, 3), round(t_meas + lat, 3),
                            p["bits"], "syndrome", ins, grp, f"{p['bits']} check outcomes from refresh", key])
            dc = cal(p["decoder"])
            ds = dc.earliest(t_meas + lat, p["decode_us"])
            dc.reserve(ds, ds + p["decode_us"])
            dkey = ("hold-decode", i, k)
            self.ev.append([dkey, round(ds, 3), round(ds + p["decode_us"], 3), p["decoder"], "decode", 1,
                            ins, grp, [], []])
            fpath, (aleaf, aport), flat = p["frame"]
            de = ds + p["decode_us"]
            self.me.append([0, fpath, p["decoder"], "frame", aleaf, aport, round(de, 3), round(de + flat, 3),
                            2, "frame", ins, grp, "Pauli-frame update", dkey])
            ac = cal(p["archive"])
            us = ac.earliest(de + flat, p["update_us"])
            ac.reserve(us, us + p["update_us"])
            ukey = ("hold-update", i, k)
            self.ev.append([ukey, round(us, 3), round(us + p["update_us"], 3), p["archive"], "update", 1,
                            ins, grp, [], []])
            self.fr.append({"id": 0, "t": round(us + p["update_us"], 3), "block": blk, "kind": "window",
                            "instruction": ins, "event": ukey, "ax": (0, frozenset()), "az": (0, frozenset()),
                            "text": f"window correction for {blk}",
                            "why": "the decoder's update for a refresh round of the memory holding it"})
            w.update(decode_event=dkey, decoded_us=round(de, 3), written_us=round(us + p["update_us"], 3))
            self.wi.append(w)

    # -- the result -------------------------------------------------------------------------
    def run(self) -> dict:
        g = self.g
        out = {k: v for k, v in g.items() if k not in ("macros", "calls", "homes", "folded")}
        out["schema"] = "qccd.gir/0.2"
        ident = (lambda n: n)
        self.number(g["program"], g["calls"], ident, ident, "")
        self.blocks.update(g["blocks"])
        self.rh: list = []
        self.rows(g, g["calls"], 0.0, ident, ident, ident, "", {})
        out.pop("road_holds", None)
        if self.rh:
            out["road_holds"] = sorted(self.rh)
        self.unroll_holds()
        self.ev.sort(key=lambda e: (e[1], e[2], e[3]))
        eid = {e[0]: i for i, e in enumerate(self.ev)}
        for e in self.ev:
            e[0] = eid[e[0]]

        def fe(x):
            if x is None or x == -1:
                return x
            if x in eid:
                return eid[x]
            # an own view's symbol: a caller's frame, or an outcome of a call it makes
            return x if isinstance(x, str) else f"{x[0]}{x[1]}"

        def fexpr(expr):
            return {"const": expr[0],
                    "sources": sorted(([fe(k), f] for k, f in expr[1]), key=lambda s: (1, s[0], s[1]) if isinstance(s[0], str) else (0, s[0], s[1]))}
        for m_ in self.me:
            m_[13] = fe(m_[13])
        self.me.sort(key=lambda m_: (m_[6], m_[7]))
        for i, m_ in enumerate(self.me):
            m_[0] = i
        self.ca.sort(key=lambda c_: c_[4])
        for i, c_ in enumerate(self.ca):
            c_[0] = i
        for w in self.wi:
            w["event"] = fe(w["event"])
            if w.get("decode_event") is not None:
                w["decode_event"] = fe(w["decode_event"])
        for f in self.fr:
            f["event"] = fe(f["event"])
            f["ax"], f["az"] = fexpr(f["ax"]), fexpr(f["az"])
            if "delta" in f:
                f["delta"]["ax"], f["delta"]["az"] = fexpr(f["delta"]["ax"]), fexpr(f["delta"]["az"])
        for o in self.oc:
            o["event"] = fe(o["event"])
            o["recorded"] = fe(o.get("recorded"))
            if o.get("parts"):
                o["parts"] = [[fe(e), f] for e, f in o["parts"]]
            for k in ("flip", "value"):
                if o.get(k) is not None:
                    o[k] = fexpr(o[k])
        self.fr.sort(key=lambda f: f["t"])
        for i, f in enumerate(self.fr):
            f["id"] = i
        out.update(events=self.ev, carries=self.ca, messages=self.me, windows=self.wi,
                   frames=self.fr, outcomes=self.oc, blocks=self.blocks)
        out["program"] = dict(g["program"], instructions=self.ins_out)
        return out


def expand(g: dict) -> dict:
    """GIR 0.3 -> GIR 0.2: every call replaced by its macro, at its time, on its ions.  A
    call's blocks are named by the caller and its own blocks `c3.name` (`c3/c0.name` inside
    a call it makes); every instruction a call ran says which (`fold`)."""
    return _Expander(g).run()


# --------------------------------------------------------------------------- checking, folded
#
# A folded run is checked the way it is scheduled: each body once, and the composition by
# construction.  A body's own VIEW is its rows as a schedule of their own: its holds unrolled,
# the blocks it was passed resting at their homes when it starts (`initial`), and each call it
# makes a BOX, the stretch each of its blocks is the call's.  Every place check runs on every
# view (`check_places` reads `initial`, `boxes` and `until`); what no view can see -- that two
# calls, or a call and its caller's own rows, never share a place, road or junction at once --
# is `check_composition`, from the rows and the macros' footprints, not from the calendars that
# placed them.


def view(g: dict, mid: str | None = None) -> dict:
    """The own view of the root (`mid` None) or of macro `mid`, as GIR 0.2.  Its
    instructions are the body's, in order, calls included (the k-th is the body's k-th)."""
    body = g if mid is None else g["macros"][mid]
    sub = {k: v for k, v in g.items() if k not in ("macros", "calls", "holds", "folded")}
    sub.update({k: body[k] for k in ("events", "carries", "messages", "windows", "frames",
                                     "outcomes", "blocks", "program")})
    sub["road_holds"] = body.get("road_holds") or []
    sub["holds"] = body.get("holds") or []
    sub["calls"] = body.get("calls") or []
    sub["macros"] = g["macros"]
    x = _Expander(sub, own=True).run()
    x["makespan_us"] = g["makespan_us"] if mid is None else body["span_us"]
    new_id = {ins["id"]: k for k, ins in enumerate(body["program"]["instructions"])}
    boxes = []
    for c in sub["calls"]:
        m = g["macros"][c["m"]]
        for i, b in enumerate(c["args"]):
            boxes.append({"ins": new_id[c["ins"]], "block": b, "t0": c["t0"],
                          "t1": c["t0"] + m["free"][i], "covered": c["t0"] + m["mem_last"][i]})
    x["boxes"] = boxes
    if mid is not None:
        initial = {}
        for b, info in body["blocks"].items():
            if info.get("param") is not None:
                for gid in info["ions"]:
                    initial[gid] = body["homes"][info["param"]]
                x["blocks"][b] = dict(x["blocks"][b], until=body["mem_last"][info["param"]])
        x["initial"] = initial
    return x


def check_folded(g: dict, lib, leaves, *, log=None) -> dict:
    """Every place check on every view; a rule holds when it holds in all of them."""
    from .place_checks import PLACE_RULES, check_places
    log = log or (lambda *a, **k: None)
    out = {"passed": [], "failed": [], "skipped": {}, "violations": {}, "metrics": {"views": 0}}
    failed: dict = {}
    skipped: dict = {}
    for mid in [None] + list(g["macros"]):
        r = check_places(view(g, mid), lib, leaves)
        out["metrics"]["views"] += 1
        who = "the program" if mid is None else f"{g['macros'][mid]['gate']} ({mid})"
        for rule in r["failed"]:
            failed.setdefault(rule, []).extend(f"{who}: {v}" for v in r["violations"].get(rule, [])[:5])
        for rule, why in r["skipped"].items():
            skipped.setdefault(rule, why)
        for k, v in r["metrics"].items():
            if isinstance(v, (int, float)) and k != "views":
                out["metrics"][k] = max(out["metrics"].get(k, v), v)
    for rule in PLACE_RULES:
        if rule in failed:
            out["failed"].append(rule)
            out["violations"][rule] = failed[rule][:50]
        elif rule in skipped:
            out["skipped"][rule] = skipped[rule]
        else:
            out["passed"].append(rule)
    log(f"  checked {out['metrics']['views']} views: {len(out['passed'])} rules hold"
        + (f", {out['failed']} fail" if out["failed"] else ""))
    return out


def check_composition(g: dict, folder: Folder) -> list:
    """No two calls of one body, and no call and that body's own rows, hold one place, road
    or junction at the same time.  From the rows each body wrote and the footprint each
    macro was placed by -- not from the calendars the scheduler kept -- body by body.
    Returns what it found (nothing, when the composition holds)."""
    by_id = {m.id: m for m in folder.macros.values()}
    problems = []
    for mid in [None] + list(g["macros"]):
        body = g if mid is None else g["macros"][mid]
        per: dict = {}
        for e in body["events"]:
            if e[2] > e[1]:
                per.setdefault(("leaf", e[3]), []).append((e[1], e[2], "own"))
        for c in body["carries"]:
            per.setdefault(("net", c[1]), []).append((c[4], c[11], "own"))
        # a shared plane: the body's own work on its members (an op's span, a road's from its
        # first load to its last delivery: `road_spans`), against its calls' plane footprints
        planes = g.get("planes") or {}
        if planes:
            pgrp = {p: pl["group"] for pl in planes.values() for p in pl["places"]}
            ngrp = {n: pl["group"] for pl in planes.values() for n in pl["nets"]}
            for e in body["events"]:
                if e[2] > e[1] and e[3] in pgrp:
                    per.setdefault(("plane", pgrp[e[3]]), []).append((e[1], e[2], "own"))
            for net, _c0, first, last in body.get("road_spans") or []:
                if net in ngrp:
                    per.setdefault(("plane", ngrp[net]), []).append((first, last, "own"))
        for c in body.get("calls") or []:
            for res, ivs in by_id[c["m"]].footprint.items():
                for a, b in ivs:
                    per.setdefault(res, []).append((c["t0"] + a, c["t0"] + b, c["id"]))
        for res, ivs in per.items():
            ivs.sort()
            reach, who = -math.inf, None
            for a, b, w in ivs:
                # two of the body's own rows are the place checks' business (G5, G13)
                if a < reach - 1e-6 and w != who:
                    problems.append(f"{'the program' if mid is None else mid}: {res[1]} is held by "
                                    f"{who} and {w} at once ({a:.1f} us)")
                    if len(problems) >= 50:
                        return problems
                if b > reach:
                    reach, who = b, w
    return problems
