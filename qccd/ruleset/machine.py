"""The machine level: a floorplan of verified places, and a program scheduled on it.

The device-level words (`vocabulary.py`) talk about sites, rails and ions inside one gadget.
A fault-tolerant machine is one level up: *places* (each a verified gadget), *roads* that
carry bundles of ions between their *doors*, *junctions* where roads meet, *wires* that
carry bits, and an execution of a logical program on them -- the GIR the scheduler writes
(`qccd/gadget/city.py`).  This module reads both into the same `Execution` the rule
language evaluates, with `kind = "machine"`, and defines the words a machine-level rule may
use.  The rules themselves (G1-G16, F1-F9) are text, in `library/machine.rules`.

The objects:

* every **activity** is an `Op` -- an op at a place (`op`), a junction pass (`pass`), a job
  at the decoder or the classical memory (`classical`), a bundle on a road (`carry`), bits on
  a wire (`message`) -- with its start and end in microseconds;
* **instants** are the moments the state changes -- an ion is loaded, leaves a place onto a
  road, or arrives from a road into a place, in that order at equal times (an arrival is
  counted before a departure at the same moment, so a place is judged at its fullest and a
  road at its emptiest, as the place model has always judged them) -- plus every moment an
  activity starts or ends, and every moment a bit is written or read;
* `occupancy(x, t)` counts the ions in place or road x, and `location(i, t)` is the place
  or road holding ion i.

A word that needs the program (`events`, `carries`, ...) on a floorplan alone raises
`NotApplicable`, so a rule about executions *does not apply* to a bare floorplan, and is
never reported as holding there.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .model import Entity, Execution, Hardware, Instant, Ion, Op, Timeline
from .vocabulary import RELATIONS, TERMS, Ctx, EvalError, NotApplicable, term

__all__ = ["Place", "Road", "Door", "WireE", "Block", "Instruction", "Window", "Intake",
           "Handoff", "FrameRow", "Outcome", "machine_execution", "CLASSICAL_FAMILIES",
           "SE_OPS", "MachineModel"]

#: places that hold no ions: their ops are modeled, not replayed (GADGETS.md §10)
CLASSICAL_FAMILIES = ("decoder", "archive")
#: ops that extract syndromes; between them a block only waits or moves (G8)
SE_OPS = ("prep.", "se.", "zz", "xx", "inject", "store", "refresh")
#: one junction master: a place whose family is "road"
JUNCTION_FAMILY = "road"


# ============================================================================ entities

class Place(Entity):
    __slots__ = ()
    kind = "place"

    def label(self) -> str:
        return str(self.id)


class Road(Entity):
    __slots__ = ()
    kind = "road"

    def label(self) -> str:
        return f"road {str(self.id).lstrip('/')}"


class Door(Entity):
    """A port of a placed gadget, "place.port"."""
    __slots__ = ()
    kind = "door"

    def label(self) -> str:
        return str(self.id)


class WireE(Entity):
    __slots__ = ()
    kind = "wire"

    def label(self) -> str:
        return f"wire {str(self.id).lstrip('/')}"


class Block(Entity):
    __slots__ = ()
    kind = "block"

    def label(self) -> str:
        return f"block {self.id}"


class Instruction(Entity):
    __slots__ = ()
    kind = "instruction"

    def label(self) -> str:
        return f"instruction {self.id}"


class Window(Entity):
    __slots__ = ()
    kind = "syndrome window"


class Intake(Entity):
    """One bundle one op takes in through one door: `(event index, port)`."""
    __slots__ = ()
    kind = "intake"


class Handoff(Entity):
    """One ion changing hands: loaded at a depot, leaving a place onto a road, or arriving
    from a road into a place.  By index into the ordered list of state changes."""
    __slots__ = ()
    kind = "handoff"


class FrameRow(Entity):
    __slots__ = ()
    kind = "frame row"


class Outcome(Entity):
    __slots__ = ()
    kind = "outcome"


# ============================================================================ the model

@dataclass
class MachineModel:
    """Everything a machine-level word reads, indexed once."""

    lib: Any
    leaves: Any
    leaf: dict                      # path -> LeafInst
    nets: dict                      # path -> Net
    wires: dict                     # path -> Wire
    places: list = field(default_factory=list)
    junction_places: list = field(default_factory=list)
    doors: dict = field(default_factory=dict)          # Door -> (path, Port)
    road_ends: dict = field(default_factory=dict)      # Road -> (Door, Door)
    wire_ends: dict = field(default_factory=dict)      # WireE -> (Door, Door)
    geometry: dict = field(default_factory=dict)       # floorplan facts (floorplan.py)
    regions: dict = field(default_factory=dict)        # place path -> region kind
    gir: dict | None = None
    # --- execution indexes (with a GIR)
    events: list = field(default_factory=list)
    carries: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    event_by_id: dict = field(default_factory=dict)
    intakes: dict = field(default_factory=dict)        # Intake -> facts
    taken: dict = field(default_factory=dict)          # id(carry) -> its ions as taken in
    handoffs: list = field(default_factory=list)       # (time, rank, gid, what, where, origin)
    handoff_instant: list = field(default_factory=list)
    residents: dict = field(default_factory=dict)      # Ion -> home Place
    loaded: list = field(default_factory=list)
    living: set = field(default_factory=set)
    instructions: list = field(default_factory=list)
    instr: dict = field(default_factory=dict)          # id -> program instruction json
    span: dict = field(default_factory=dict)           # id -> (t0, t1) over ion events
    realised: set = field(default_factory=set)
    refused: dict = field(default_factory=dict)
    next_block: dict = field(default_factory=dict)     # instruction id -> {next ids}
    blocks: dict = field(default_factory=dict)         # name -> info
    frames: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    syndrome_of: dict = field(default_factory=dict)    # decode job index -> message Op
    next_syndrome: dict = field(default_factory=dict)  # decode job index -> message Op
    time_instant: dict = field(default_factory=dict)   # time -> observation instant
    budget_us: float = 1e6


def _r(t: float) -> float:
    return round(float(t), 6)


def machine_execution(lib, leaves, gir: dict | None = None, *, name: str = "",
                      geometry: dict | None = None, regions: dict | None = None,
                      budget_us: float = 1e6) -> Execution:
    """A floorplan (`lib`, a composite of placed gadgets) and optionally an execution on it
    (`gir`, as `qccd.gadget.city` writes it), ready to be judged by machine-level rules."""
    from ..gadget.synth import flatten

    wires: dict = {}
    leaf, nets, _n = flatten(lib, leaves, wires=wires)
    M = MachineModel(lib=lib, leaves=leaves, leaf=leaf, nets=nets, wires=wires,
                     geometry=dict(geometry or {}), regions=dict(regions or {}),
                     budget_us=budget_us)
    for path, li in leaf.items():
        p = Place(path)
        (M.junction_places if li.master.family == JUNCTION_FAMILY else M.places).append(p)
        for port in li.master.ports:
            M.doors[Door(f"{path}.{port.name}")] = (path, port)
    for path, n in nets.items():
        M.road_ends[Road(path)] = (Door(f"{n.a[0]}.{n.a[1]}"), Door(f"{n.b[0]}.{n.b[1]}"))
    for path, w in wires.items():
        M.wire_ends[WireE(path)] = (Door(f"{w.a[0]}.{w.a[1]}"), Door(f"{w.b[0]}.{w.b[1]}"))

    capacity: dict = {}
    for p in M.places + M.junction_places:
        capacity[p] = int(leaf[p.id].master.capacity)
    for r in M.road_ends:
        capacity[r] = int(nets[r.id].length)
    ends: dict = {}
    for r, (a, b) in M.road_ends.items():
        ends[r] = (a, b)
    for w, (a, b) in M.wire_ends.items():
        ends[w] = (a, b)
    hw = Hardware(name=lib.top or "machine", sites=list(M.places), junctions=list(M.junction_places),
                  nodes=list(M.places) + list(M.junction_places), rails=list(M.road_ends),
                  capacity=capacity, degree={}, zone={}, abilities={}, ends=ends, rails_at={})

    if gir is None:
        exe = Execution(kind="machine", hardware=hw, ions=[], ops=[], instants=["the floorplan"],
                        instant_time=[0.0], occupancy=Timeline(0), position=Timeline(None),
                        name=name or (lib.top or "floorplan"))
        exe.settings["tol"] = 1e-6
        exe.facts["machine"] = M
        exe.facts["has_program"] = False
        return exe
    return _with_program(M, hw, gir, name)


def _with_program(M: MachineModel, hw: Hardware, gir: dict, name: str) -> Execution:
    lib = M.lib
    M.gir = gir
    masters = {p: lib[v[0]] for p, v in gir["leaves"].items()}

    def family(path):
        return masters[path].family

    # ------------------------------------------------------------------ activities
    ops: list[Op] = []
    for e in gir["events"]:
        fam = family(e[3])
        kind = ("classical" if fam in CLASSICAL_FAMILIES else
                "pass" if fam == JUNCTION_FAMILY else "op")
        o = Op(index=len(ops), kind=kind, ions=tuple(Ion(g) for _, g in e[8] if isinstance(g, int)),
               start=0, end=0, t0=float(e[1]), t1=float(e[2]),
               facts={"row": e, "place": Place(e[3]), "op": e[4], "instruction": e[6],
                      "guard": e[10] if len(e) > 10 else None},
               text=f"{e[4]} at {e[3]} (instruction {e[6]})")
        ops.append(o)
        M.events.append(o)
        M.event_by_id[e[0]] = o
    nets = gir["nets"]

    def dst_of(c):
        a, _, b, _, _ = nets[c[1]]
        return b if c[2] == a else a

    def dst_port(c):
        a, pa, b, pb, _ = nets[c[1]]
        return pb if c[2] == a else pa

    for c in gir["carries"]:
        takes = [float(c[4]) + float(k) for k in c[12]]
        o = Op(index=len(ops), kind="carry", ions=tuple(Ion(g) for g in c[7]), start=0, end=0,
               t0=float(c[4]), t1=float(c[11]),
               facts={"row": c, "road": Road(c[1]), "from": Place(c[2]), "from_port": c[3],
                      "to": Place(dst_of(c)), "to_port": dst_port(c), "instruction": c[9],
                      "deps": [float(c[4]) + float(d) for d in c[5]], "takes": takes,
                      "transit": float(c[6])},
               text=f"a bundle of {len(c[7])} on {str(c[1]).lstrip('/')} (instruction {c[9]})")
        ops.append(o)
        M.carries.append(o)
    kind_of = {i["id"]: i["kind"] for i in gir["program"]["instructions"]}
    for m in gir.get("messages") or []:
        src = M.event_by_id.get(m[13]) if len(m) > 13 and m[13] is not None and m[13] >= 0 else None
        o = Op(index=len(ops), kind="message", ions=(), start=0, end=0, t0=float(m[6]),
               t1=float(m[7]),
               facts={"row": m, "wire": WireE(m[1]), "from": Place(m[2]), "from_port": m[3],
                      "to": Place(m[4]), "to_port": m[5], "bits": m[8], "signal": m[9],
                      "instruction": m[10], "source": src,
                      "held": m[9] == "syndrome" and kind_of.get(m[10]) == "decode"},
               text=f"{m[9]} bits on {str(m[1]).lstrip('/')} (instruction {m[10]})")
        ops.append(o)
        M.messages.append(o)

    # ------------------------------------------------------------------ program
    for ins in gir["program"]["instructions"]:
        M.instr[ins["id"]] = ins
        M.instructions.append(Instruction(ins["id"]))
    M.refused = {i: why for i, why in gir["refused"]}
    for o in M.events:
        M.realised.add(o.facts["instruction"])
        if family(o.facts["place"].id) in CLASSICAL_FAMILIES:
            continue
        s = M.span.setdefault(o.facts["instruction"], [o.t0, o.t1])
        s[0], s[1] = min(s[0], o.t0), max(s[1], o.t1)
    last: dict[str, int] = {}
    for ins in gir["program"]["instructions"]:
        if ins["id"] not in M.span:
            continue
        for b in ins["blocks"]:
            if b in last:
                M.next_block.setdefault(last[b], set()).add(ins["id"])
            last[b] = ins["id"]
    M.blocks = dict(gir["blocks"])

    # ------------------------------------------------------------------ intakes (G1, G12)
    # ion by ion: an op takes each ion in at the time its replay says, and the carry that
    # brings it says when it was taken off the road.  A place's op takes a whole bundle; a
    # junction takes a bundle one ion at a time, each a lone pass of its own.
    by_ion: dict[tuple, list] = {}
    for c in M.carries:
        row = c.facts["row"]
        for k, g in enumerate(row[7]):
            by_ion.setdefault((c.facts["to"].id, c.facts["to_port"], c.facts["instruction"], g),
                              []).append((c, k))
    shares: dict = {}
    for o in M.events:
        e = o.facts["row"]
        op = masters[e[3]].ops.get(e[4])
        if op is None:
            continue
        bound = {nm: gid for nm, gid in e[8]}
        for tr in op.transfers:
            if tr.dir != "in":
                continue
            want = tuple(Ion(bound.get(nm)) for nm in tr.ions)
            carry, got = None, []
            for nm, t in zip(tr.ions, tr.times_us):
                g = bound.get(nm)
                for c, k in by_ion.get((e[3], tr.port, e[6], g), []):
                    if abs(c.t0 + c.facts["row"][12][k] - (e[1] + t)) < 1.0 and (carry is None or c is carry):
                        carry = c
                        got.append((k, Ion(g)))
                        break
            delivered = tuple(ion for _, ion in sorted(got, key=lambda x: x[0]))
            M.intakes[Intake((o.index, tr.port))] = {
                "event": o, "port": tr.port, "expected": want,
                "carry": carry, "delivered": delivered}
            if carry is not None:
                shares.setdefault(id(carry), []).append((min(k for k, _ in got), delivered))
    M.taken = {id(c): tuple(ion for _, part in sorted(shares.get(id(c), []), key=lambda x: x[0])
                            for ion in part) for c in M.carries}

    # ------------------------------------------------------------------ handoffs and state
    home: dict[int, str] = {}
    for path, (mname, _, _, first) in gir["leaves"].items():
        for k in range(len(gir["ion_order"][mname])):
            home[first + k] = path
    marks = []          # (time, rank, gid, what, where-after, origin-before)
    for o in M.events:
        e = o.facts["row"]
        if family(e[3]) == "depot" and e[4].startswith("load."):
            for _, gid in e[8]:
                marks.append((float(e[1]), -1, gid, "born", Place(e[3]), None))
    for c in M.carries:
        row = c.facts["row"]
        for gid, dep, take in zip(row[7], c.facts["deps"], c.facts["takes"]):
            marks.append((dep, 1, gid, "leave", c.facts["road"], c.facts["from"]))
            marks.append((take, 0, gid, "arrive", c.facts["to"], c.facts["road"]))
    marks.sort(key=lambda m: (m[0], m[1]))

    # every moment a rule may compare: activity bounds, and the classical half's times
    obs: set[float] = {0.0}
    for o in ops:
        obs.add(_r(o.t0))
        obs.add(_r(o.t1))
    for f in gir.get("frames") or []:
        obs.add(_r(f["t"]))
    for oc in gir.get("outcomes") or []:
        if oc.get("recorded_us") is not None:
            obs.add(_r(oc["recorded_us"]))
    for w in gir.get("windows") or []:
        for k in ("measured_us", "sent_us", "decoded_us", "written_us"):
            if w.get(k) is not None:
                obs.add(_r(w[k]))
    for o in M.events:
        g = o.facts["guard"]
        if g and g.get("ready_us") is not None:
            obs.add(_r(g["ready_us"]))
    # instants: the start, then (time, rank) points: a state change (rank = its kind) or an
    # observation (rank 2: after every change at that time)
    points = [(_r(m[0]), m[1], i) for i, m in enumerate(marks)] + [(t, 2, -1) for t in obs]
    points.sort()
    instants = ["the start"]
    instant_time = [0.0]
    mark_instant = [0] * len(marks)
    for t, rank, i in points:
        instants.append(f"{t:g} us" if rank == 2 else f"{t:g} us: ion {marks[i][2]} {marks[i][3]}")
        instant_time.append(t)
        k = len(instants) - 1
        if rank == 2:
            M.time_instant.setdefault(t, k)
        else:
            mark_instant[i] = k
    occ = Timeline(0)
    pos = Timeline(None)
    level: dict = defaultdict(int)
    for gid, path in home.items():
        level[Place(path)] += 1
        pos.set(Ion(gid), 0, Place(path))
        M.residents[Ion(gid)] = Place(path)
    for p in M.places + M.junction_places:
        occ.set(p, 0, level[p])
    for r in M.road_ends:
        occ.set(r, 0, 0)
    for i, (t, rank, gid, what, where, origin) in enumerate(marks):
        k = mark_instant[i]
        M.handoffs.append((t, rank, gid, what, where, origin))
        M.handoff_instant.append(k)
        if what == "born":
            M.loaded.append(Ion(gid))
        else:
            level[origin] -= 1
            occ.set(origin, k, level[origin])
        level[where] += 1
        occ.set(where, k, level[where])
        pos.set(Ion(gid), k, where)
    for b in gir["blocks"].values():
        if "read" not in b:
            M.living.update(Ion(g) for g in b["ions"])

    for o in ops:
        o.start = M.time_instant[_r(o.t0)]
        o.end = M.time_instant[_r(o.t1)]

    # ------------------------------------------------------------------ classical half
    M.frames = list(gir.get("frames") or [])
    M.outcomes = list(gir.get("outcomes") or [])
    M.windows = list(gir.get("windows") or [])
    decoder = (gir.get("control") or {}).get("decoder")
    jobs = sorted((o for o in M.events if o.facts["place"].id == decoder and o.facts["op"] == "decode"),
                  key=lambda o: o.t0)
    syn = sorted((m for m in M.messages if m.facts["signal"] == "syndrome"),
                 key=lambda m: (m.t1, m.facts["row"][0]))
    queue: dict = defaultdict(list)
    for m in syn:
        queue[m.facts["instruction"]].append(m)
    arrivals = [m.t1 for m in syn]
    for j in jobs:
        mine = queue.get(j.facts["instruction"])
        if not mine:
            continue
        m = mine.pop(0)
        M.syndrome_of[j.index] = m
        k = next((x for x in syn if x.t1 > m.t1 + 1e-6), None)
        if k is not None:
            M.next_syndrome[j.index] = k
    M.facts_decode_jobs = jobs                                  # type: ignore[attr-defined]

    exe = Execution(kind="machine", hardware=hw, ions=sorted({Ion(g) for g in home} |
                                                             {Ion(m[2]) for m in marks}),
                    ops=ops, instants=instants, instant_time=instant_time, occupancy=occ,
                    position=pos, name=name or gir["program"].get("name", "program"))
    exe.settings["tol"] = 1e-6
    exe.facts["machine"] = M
    exe.facts["has_program"] = True
    return exe


# ============================================================================ helpers

def _M(ctx: Ctx) -> MachineModel:
    M = ctx.exe.facts.get("machine")
    if M is None:
        raise NotApplicable("this is not a machine: it has no places")
    return M


def _prog(ctx: Ctx, what: str) -> MachineModel:
    M = _M(ctx)
    if M.gir is None:
        raise NotApplicable(f"{what} exist only once a program is scheduled on the floorplan")
    return M


def _at(ctx: Ctx, t: float) -> Instant:
    M = _M(ctx)
    k = M.time_instant.get(_r(t))
    if k is None:
        # a time no activity starts or ends at: the last instant at or before it
        times = ctx.exe.instant_time
        k = max(0, bisect_left(times, _r(t) + 1e-9) - 1)
    return Instant(k)


def _activity(x: Any, fn: str) -> Op:
    if isinstance(x, Op):
        return x
    raise EvalError(f"{fn} needs an activity, got {x!r}")


def _place_path(M: MachineModel, x: Any) -> str:
    if isinstance(x, Place):
        return x.id
    if isinstance(x, Door):
        return M.doors[x][0]
    if isinstance(x, Op) and "place" in x.facts:
        return x.facts["place"].id
    raise EvalError(f"{x!r} is not a place")


def _port(M: MachineModel, d: Any):
    if not isinstance(d, Door) or d not in M.doors:
        raise EvalError(f"{d!r} is not a door")
    return M.doors[d][1]


GROUP_F = "floorplan"
GROUP_M = "machine"
K = ("machine",)


# ============================================================================ floorplan words

@term("places", "a set of places", "Every placed gadget of the machine that is not a junction: "
      "the places blocks are brought to, the depots, the decoder and the classical memory.",
      GROUP_F, domain=True, kinds=K)
def _places(ctx: Ctx):
    return _M(ctx).places


@term("roads", "a set of roads", "Every conveyor road between two doors: a channel bundles of "
      "ions travel along.", GROUP_F, domain=True, kinds=K)
def _roads(ctx: Ctx):
    return list(_M(ctx).road_ends)


@term("wires", "a set of wires", "Every classical link: it carries bits from one door to "
      "another, never ions.", GROUP_F, domain=True, kinds=K)
def _wires(ctx: Ctx):
    return list(_M(ctx).wire_ends)


@term("doors", "a set of doors", "Every port of every placed gadget, junction arms included.",
      GROUP_F, domain=True, kinds=K)
def _doors(ctx: Ctx):
    return list(_M(ctx).doors)


@term("depots", "a set of places", "The ion depots: where fresh ions are loaded and used ones "
      "are dumped.", GROUP_F, domain=True, kinds=K)
def _depots(ctx: Ctx):
    M = _M(ctx)
    return [p for p in M.places if M.leaf[p.id].master.family == "depot"]


@term("family(x)", "a name", "What place x is (or the place door, or activity, x belongs to): "
      "\"prep\", \"se\", \"operation\", \"surgery\", \"readout\", \"zone\", \"factory\", "
      "\"injection\", \"depot\", \"decoder\", \"archive\", or \"road\" for a junction.",
      GROUP_F, kinds=K)
def _family(ctx: Ctx, x):
    M = _M(ctx)
    if x is None:
        return None
    return M.leaf[_place_path(M, x)].master.family


@term("classical(x)", "true or false", "Whether place x (or the place activity x happens at) "
      "holds no ions: the decoder and the classical memory, whose ops are modeled.",
      GROUP_F, kinds=K)
def _classical(ctx: Ctx, x):
    M = _M(ctx)
    return M.leaf[_place_path(M, x)].master.family in CLASSICAL_FAMILIES


@term("medium(d)", "a name", "What crosses door d: \"ions\" or \"bits\".", GROUP_F, kinds=K)
def _medium(ctx: Ctx, d):
    return "bits" if _port(_M(ctx), d).kind == "classical" else "ions"


@term("role(d)", "a name", "What the ions crossing door d are for: \"data\" (a block's data "
      "ions), \"magic\" (a resource state), \"fresh\", \"messenger\", or \"any\".", GROUP_F,
      kinds=K)
def _role(ctx: Ctx, d):
    return _port(_M(ctx), d).role


@term("signal(d)", "a name", "What the bits crossing door d mean: \"syndrome\", \"outcome\", "
      "\"frame\", \"decision\", or \"any\"; none for a door of ions.", GROUP_F, kinds=K)
def _signal(ctx: Ctx, d):
    p = _port(_M(ctx), d)
    return (p.signal or "any") if p.kind == "classical" else None


@term("width(d)", "a number", "How many ions cross door d in one bundle.", GROUP_F, kinds=K)
def _width(ctx: Ctx, d):
    return int(_port(_M(ctx), d).width)


@term("code(d)", "a name", "The code whose data ions door d carries, or none if it does not "
      "say.", GROUP_F, kinds=K)
def _code(ctx: Ctx, d):
    return _port(_M(ctx), d).code


@term("dir(d)", "a name", "Which way door d is crossed: \"in\", \"out\" or \"inout\".",
      GROUP_F, kinds=K)
def _dir(ctx: Ctx, d):
    return _port(_M(ctx), d).dir


@term("owner(d)", "a place", "The place (or junction) door d belongs to.", GROUP_F, kinds=K)
def _owner(ctx: Ctx, d):
    M = _M(ctx)
    if not isinstance(d, Door):
        raise EvalError(f"owner needs a door, got {d!r}")
    return Place(M.doors[d][0])


@term("length(r)", "a number", "How many sites road r has: its route length less one.",
      GROUP_F, kinds=K)
def _length(ctx: Ctx, r):
    M = _M(ctx)
    if isinstance(r, Road):
        return int(M.nets[r.id].length)
    raise EvalError(f"length needs a road, got {r!r}")


@term("latency(w)", "microseconds", "How long a bit takes to cross wire w.", GROUP_F, kinds=K)
def _latency(ctx: Ctx, w):
    M = _M(ctx)
    if isinstance(w, WireE) and w.id in M.wires:
        return float(M.wires[w.id].latency_us)
    raise EvalError(f"latency needs a wire, got {w!r}")


# ============================================================================ execution words

@term("events", "a set of activities", "Every op the machine runs at a place: a place's op, a "
      "junction passing a bundle, a job at the decoder or the classical memory.", GROUP_M,
      domain=True, kinds=K)
def _events(ctx: Ctx):
    return _prog(ctx, "events").events


@term("carries", "a set of activities", "Every bundle carried along a road: from the first "
      "ion leaving one door to the last taken in at the other end.", GROUP_M, domain=True,
      kinds=K)
def _carries(ctx: Ctx):
    return _prog(ctx, "carries").carries


@term("messages", "a set of activities", "Every burst of bits sent along a wire.", GROUP_M,
      domain=True, kinds=K)
def _messages(ctx: Ctx):
    return _prog(ctx, "messages").messages


@term("instructions", "a set of instructions", "The instructions of the logical program.",
      GROUP_M, domain=True, kinds=K)
def _instructions(ctx: Ctx):
    return _prog(ctx, "instructions").instructions


@term("blocks", "a set of blocks", "The logical blocks of the program: each a bundle of ions "
      "holding one encoded qubit.", GROUP_M, domain=True, kinds=K)
def _blocks(ctx: Ctx):
    return [Block(b) for b in _prog(ctx, "blocks").blocks]


@term("place(x)", "a place", "Where activity x happens (an op, a pass, a classical job).",
      GROUP_M, kinds=K)
def _place(ctx: Ctx, x):
    o = _activity(x, "place")
    if "place" not in o.facts:
        raise EvalError(f"{o.label()} does not happen at one place")
    return o.facts["place"]


@term("road(c)", "a road", "The road carry c travels along.", GROUP_M, kinds=K)
def _road(ctx: Ctx, c):
    o = _activity(c, "road")
    if "road" not in o.facts:
        raise EvalError(f"{o.label()} is not a carry")
    return o.facts["road"]


@term("origin(x)", "a place", "Where carry or message x starts: the place it leaves.", GROUP_M,
      kinds=K)
def _origin(ctx: Ctx, x):
    if isinstance(x, Handoff):
        M = _M(ctx)
        return M.handoffs[x.id][5]
    o = _activity(x, "origin")
    return o.facts["from"]


@term("destination(x)", "a place", "Where carry or message x ends: the place it reaches.",
      GROUP_M, kinds=K)
def _destination(ctx: Ctx, x):
    return _activity(x, "destination").facts["to"]


@term("instruction(x)", "an instruction", "The instruction activity x is part of.", GROUP_M,
      kinds=K)
def _instruction(ctx: Ctx, x):
    if isinstance(x, Op):
        return Instruction(x.facts["instruction"])
    raise EvalError(f"instruction needs an activity, got {x!r}")


@term("refused(i)", "true or false", "Whether the scheduler refused instruction i (and why is "
      "said with it).", GROUP_M, kinds=K)
def _refused(ctx: Ctx, i):
    return i.id in _prog(ctx, "instructions").refused


@term("realised(i)", "true or false", "Whether some event of the machine carries out "
      "instruction i (a Pauli kept in software is carried out by the classical memory).",
      GROUP_M, kinds=K)
def _realised(ctx: Ctx, i):
    return i.id in _prog(ctx, "instructions").realised


@term("begins(i)", "an instant", "When the first op on ions of instruction i starts.",
      GROUP_M, kinds=K)
def _begins(ctx: Ctx, i):
    M = _prog(ctx, "instructions")
    s = M.span.get(i.id)
    return None if s is None else _at(ctx, s[0])


@term("finishes(i)", "an instant", "When the last op on ions of instruction i ends.", GROUP_M,
      kinds=K)
def _finishes(ctx: Ctx, i):
    M = _prog(ctx, "instructions")
    s = M.span.get(i.id)
    return None if s is None else _at(ctx, s[1])


@term("next_on_block(i, j)", "true or false", "Whether j is the next instruction after i, in "
      "program order, that acts on the ions of a block i acts on (instructions that move no "
      "ion -- a decode, a Pauli kept in software -- are skipped).", GROUP_M, kinds=K)
def _next_on_block(ctx: Ctx, i, j):
    return j.id in _prog(ctx, "instructions").next_block.get(i.id, ())


RELATIONS["next_on_block"] = lambda ctx, a: [Instruction(x) for x in
                                             sorted(_prog(ctx, "instructions").next_block.get(a.id, ()))]


@term("intakes", "a set of intakes", "Every bundle an op takes in through one of its doors.",
      GROUP_M, domain=True, kinds=K)
def _intakes(ctx: Ctx):
    return list(_prog(ctx, "intakes").intakes)


@term("delivered_by(k)", "a carry", "The carry that brings intake k's bundle to its door "
      "(arriving when the op's own replay takes its first ion in), or none.", GROUP_M, kinds=K)
def _delivered_by(ctx: Ctx, k):
    return _prog(ctx, "intakes").intakes[k]["carry"]


@term("expected(k)", "a list of ions", "The ions intake k's op takes in, in the order its "
      "verified program takes them.", GROUP_M, kinds=K)
def _expected(ctx: Ctx, k):
    return _prog(ctx, "intakes").intakes[k]["expected"]


@term("delivered(k)", "a list of ions", "The ions intake k takes off its carry, each when "
      "the carry says it was taken, in the order the carry brings them: the whole bundle "
      "for a place's op, one ion for a junction's lone pass.", GROUP_M, kinds=K)
def _delivered(ctx: Ctx, k):
    return _prog(ctx, "intakes").intakes[k]["delivered"]


@term("taken(c)", "a list of ions", "The ions of carry c as the ops at its far end take "
      "them in, each op's share in turn: carry c's own order, if every ion is taken once.",
      GROUP_M, kinds=K)
def _taken(ctx: Ctx, c):
    o = _activity(c, "taken")
    return _prog(ctx, "intakes").taken.get(id(o), ())


@term("order(c)", "a list of ions", "The ions of carry c in the order they travel.", GROUP_M,
      kinds=K)
def _order(ctx: Ctx, c):
    o = _activity(c, "order")
    return tuple(o.ions)


@term("handoffs", "a set of handoffs", "Every time an ion changes hands: loaded at a depot, "
      "leaving a place onto a road, or arriving from a road into a place.", GROUP_M,
      domain=True, kinds=K)
def _handoffs(ctx: Ctx):
    return [Handoff(i) for i in range(len(_prog(ctx, "handoffs").handoffs))]


@term("ion(h)", "an ion", "The ion handoff h moves.", GROUP_M, kinds=K)
def _ion(ctx: Ctx, h):
    return Ion(_prog(ctx, "handoffs").handoffs[h.id][2])


@term("before(h)", "an instant", "The instant just before handoff h.", GROUP_M, kinds=K)
def _before(ctx: Ctx, h):
    return Instant(_prog(ctx, "handoffs").handoff_instant[h.id] - 1)


@term("location(i, t)", "a place or a road", "The place or road holding ion i at instant t; none "
      "before it is loaded.", GROUP_M, kinds=K)
def _location(ctx: Ctx, i, t):
    if not isinstance(i, Ion) or not isinstance(t, Instant):
        raise EvalError("location needs an ion and an instant")
    return ctx.exe.position.at(i, t.id)


@term("residents", "a set of ions", "The ions that live in places from the start (ancillas, a "
      "surgery seam, a factory's staff).", GROUP_M, domain=True, kinds=K)
def _residents(ctx: Ctx):
    return sorted(_prog(ctx, "residents").residents)


@term("home(i)", "a place", "Where resident ion i lives.", GROUP_M, kinds=K)
def _home(ctx: Ctx, i):
    return _prog(ctx, "residents").residents.get(i)


@term("loaded", "a set of ions", "The ions loaded at a depot while the program runs.",
      GROUP_M, domain=True, kinds=K)
def _loaded(ctx: Ctx):
    return _prog(ctx, "loaded ions").loaded


@term("living", "a set of ions", "The ions of the blocks still alive when the program ends "
      "(never read out).", GROUP_M, domain=True, kinds=K)
def _living(ctx: Ctx):
    return sorted(_prog(ctx, "blocks").living)


@term("longest_gap(b)", "microseconds", "The longest time block b spends, from its birth to "
      "its readout (or the end), with none of its ions in an op that extracts syndromes "
      "(preparation, a syndrome round, lattice surgery, injection).", GROUP_M, kinds=K)
def _longest_gap(ctx: Ctx, b):
    M = _prog(ctx, "blocks")
    info = M.blocks[b.id]
    ions = set(info["ions"])
    times = sorted((o.t0, o.t1) for o in M.events if o.facts["op"].startswith(SE_OPS)
                   and any(isinstance(g, int) and g in ions for _, g in o.facts["row"][8]))
    end = info.get("read", M.gir["makespan_us"])
    t = times[0][1] if times else info["born"]
    longest = 0.0
    for a, z in times[1:] + [(end, end)]:
        longest = max(longest, a - t)
        t = max(t, z)
    return longest


@term("status(e)", "a name", "The verification status of the op event e runs: \"verified\", "
      "\"certified\", \"modeled\" or \"failed\" (the reporting contract of the place library).",
      GROUP_M, kinds=K)
def _status(ctx: Ctx, e):
    M = _prog(ctx, "events")
    o = _activity(e, "status")
    op = M.leaf[o.facts["place"].id].master.ops.get(o.facts["op"])
    return None if op is None else op.status


@term("logic_failed(e)", "true or false", "Whether the logic check of the op event e runs "
      "failed (its flows or its fault distance).", GROUP_M, kinds=K)
def _logic_failed(ctx: Ctx, e):
    M = _prog(ctx, "events")
    o = _activity(e, "logic_failed")
    op = M.leaf[o.facts["place"].id].master.ops.get(o.facts["op"])
    return op is not None and (op.logic or {}).get("status") == "failed"


def _followed(M) -> dict:
    """Every ion's n̄ followed through the whole schedule (qccd/gadget/heat.py), once."""
    got = getattr(M, "_heat_followed", None)
    if got is None:
        from ..gadget.heat import follow
        got = follow(M.gir, M.lib, M.leaves) if M.leaves is not None else {"events": {}}
        M._heat_followed = got
    return got


@term("exit_heat(e)", "quanta", "The hottest a bundle leaves event e's place: the op replayed "
      "from the n̄ its ions really arrive at, after every op, road and junction before it "
      "(0 if no ion leaves).", GROUP_M, kinds=K)
def _exit_heat(ctx: Ctx, e):
    M = _prog(ctx, "events")
    o = _activity(e, "exit_heat")
    op = M.leaf[o.facts["place"].id].master.ops.get(o.facts["op"])
    if op is None:
        return 0.0
    cold = max([float(t.quanta) for t in op.transfers if t.dir == "out"] or [0.0])
    hot = _followed(M)["events"].get(o.facts["row"][0], {}).get("exit", 0.0)
    return max(cold, hot)


@term("peak_heat(e)", "quanta", "The hottest any ion gets inside the op event e runs: the op "
      "replayed from the n̄ its ions really arrive at (heat followed across the town).",
      GROUP_M, kinds=K)
def _peak_heat(ctx: Ctx, e):
    M = _prog(ctx, "events")
    o = _activity(e, "peak_heat")
    op = M.leaf[o.facts["place"].id].master.ops.get(o.facts["op"])
    if op is None:
        return 0.0
    hot = _followed(M)["events"].get(o.facts["row"][0], {}).get("peak", 0.0)
    return max(float(op.metrics.get("peak_quanta", 0.0)), hot)


@term("ion_loss_limit()", "quanta", "The hottest an ion may get before it is likely lost: about "
      "85 uncooled junction round trips (arXiv:1210.3655), at 3 quanta a transit.", GROUP_M,
      kinds=K)
def _ion_loss_limit(ctx: Ctx):
    from ..gadget.checks import ION_LOSS_QUANTA
    return float(ION_LOSS_QUANTA)


# --------------------------------------------------------------- the classical half

@term("wire(m)", "a wire", "The wire message m crosses, or none if the floorplan has no such "
      "wire.", GROUP_M, kinds=K)
def _wire(ctx: Ctx, m):
    M = _prog(ctx, "messages")
    w = _activity(m, "wire").facts["wire"]
    return w if w.id in M.wires else None


@term("connects(w, m)", "true or false", "Whether wire w runs between the two doors message m "
      "goes from and to.", GROUP_M, kinds=K)
def _connects(ctx: Ctx, w, m):
    M = _prog(ctx, "messages")
    o = _activity(m, "connects")
    a, b = M.wire_ends[w]
    want = {Door(f"{o.facts['from'].id}.{o.facts['from_port']}"),
            Door(f"{o.facts['to'].id}.{o.facts['to_port']}")}
    return {a, b} == want


@term("measured_by(m)", "an event", "The op whose measurement made message m's bits, or "
      "none.", GROUP_M, kinds=K)
def _measured_by(ctx: Ctx, m):
    return _activity(m, "measured_by").facts.get("source")


@term("held(m)", "true or false", "Whether message m is a syndrome a `decode` instruction sent "
      "out of the readout buffer it waited in (the one kind of bits that may leave after the "
      "op that measured them).", GROUP_M, kinds=K)
def _held(ctx: Ctx, m):
    return bool(_activity(m, "held").facts.get("held"))


@term("frame_rows", "a set of frame rows", "Every line written into the classical memory: an "
      "outcome recorded, a frame updated, a guard resolved.", GROUP_M, domain=True, kinds=K)
def _frame_rows(ctx: Ctx):
    return [FrameRow(i) for i in range(len(_prog(ctx, "frame rows").frames))]


@term("outcomes", "a set of outcomes", "Every logical measurement result the classical memory "
      "records.", GROUP_M, domain=True, kinds=K)
def _outcomes(ctx: Ctx):
    return [Outcome(i) for i in range(len(_prog(ctx, "outcomes").outcomes))]


@term("named(x)", "a set of events", "The events whose measurements frame row or outcome x is a "
      "parity of.", GROUP_M, domain=True, kinds=K)
def _named(ctx: Ctx, x):
    M = _prog(ctx, "frame rows")
    out = []
    if isinstance(x, FrameRow):
        f = M.frames[x.id]
        for key in ("ax", "az"):
            for eid, _flow in (f.get(key) or {}).get("sources") or []:
                out.append(M.event_by_id[int(eid)])
    elif isinstance(x, Outcome):
        o = M.outcomes[x.id]
        for eid, _flow in ((o.get("value") or {}).get("sources") or []):
            out.append(M.event_by_id[int(eid)])
    else:
        raise EvalError(f"named needs a frame row or an outcome, got {x!r}")
    return out


@term("at(x)", "an instant", "When frame row x was written, or outcome x recorded.", GROUP_M,
      kinds=K)
def _at_(ctx: Ctx, x):
    M = _prog(ctx, "frame rows")
    if isinstance(x, FrameRow):
        return _at(ctx, M.frames[x.id]["t"])
    if isinstance(x, Outcome):
        return _at(ctx, M.outcomes[x.id].get("recorded_us") or 0.0)
    raise EvalError(f"at needs a frame row or an outcome, got {x!r}")


@term("decoder_present()", "true or false", "Whether the floorplan has a decoder.", GROUP_M,
      kinds=K)
def _decoder_present(ctx: Ctx):
    M = _M(ctx)
    gir = M.gir or {}
    return bool((gir.get("control") or {}).get("decoder"))


@term("decode_jobs", "a set of events", "Every decoding job the decoder runs.", GROUP_M,
      domain=True, kinds=K)
def _decode_jobs(ctx: Ctx):
    return list(getattr(_prog(ctx, "decoding jobs"), "facts_decode_jobs", []))


@term("syndrome_of(j)", "a message", "The syndrome decoding job j works on: the earliest one "
      "its own instruction sent that no earlier job has taken.", GROUP_M, kinds=K)
def _syndrome_of(ctx: Ctx, j):
    return _prog(ctx, "decoding jobs").syndrome_of.get(_activity(j, "syndrome_of").index)


@term("next_syndrome(j)", "a message", "The first syndrome to reach the decoder after the one "
      "job j works on, or none.", GROUP_M, kinds=K)
def _next_syndrome(ctx: Ctx, j):
    return _prog(ctx, "decoding jobs").next_syndrome.get(_activity(j, "next_syndrome").index)


@term("guarded", "a set of events", "Every classically controlled op: it runs only when a "
      "condition on measured outcomes holds.", GROUP_M, domain=True, kinds=K)
def _guarded(ctx: Ctx):
    return [o for o in _prog(ctx, "events").events if o.facts.get("guard")]


@term("ready(e)", "an instant", "When the guard of classically controlled op e was resolved "
      "and at its place's door, as the scheduler says.", GROUP_M, kinds=K)
def _ready(ctx: Ctx, e):
    g = _activity(e, "ready").facts.get("guard") or {}
    return _at(ctx, float(g.get("ready_us", 0.0)))


@term("decisions(e)", "a set of messages", "The decision messages that tell classically "
      "controlled op e's place whether to run it.", GROUP_M, domain=True, kinds=K)
def _decisions(ctx: Ctx, e):
    M = _prog(ctx, "messages")
    o = _activity(e, "decisions")
    return [m for m in M.messages if m.facts["signal"] == "decision"
            and m.facts["to"] == o.facts["place"] and m.facts["instruction"] == o.facts["instruction"]]


@term("windows", "a set of windows", "Every syndrome window: the check outcomes one op measured, "
      "waiting in its place's readout buffer until a `decode` sends them.", GROUP_M,
      domain=True, kinds=K)
def _windows(ctx: Ctx):
    return [Window(i) for i in range(len(_prog(ctx, "windows").windows))]


@term("decoded(w)", "true or false", "Whether some `decode` instruction sent window w to the "
      "decoder.", GROUP_M, kinds=K)
def _decoded(ctx: Ctx, w):
    return _prog(ctx, "windows").windows[w.id].get("decoded_by") is not None


@term("sends(w)", "a set of messages", "The syndrome messages that carry window w from the "
      "place that measured it to the decoder.", GROUP_M, domain=True, kinds=K)
def _sends(ctx: Ctx, w):
    M = _prog(ctx, "windows")
    W = M.windows[w.id]
    return [m for m in M.messages if m.facts["signal"] == "syndrome"
            and m.facts["from"].id == W["place"] and m.facts["instruction"] == W.get("decoded_by")
            and m.facts["source"] is not None and m.facts["source"].facts["row"][0] == W["event"]]


@term("measured(w)", "an instant", "When window w's bits exist: the last measurement of the op "
      "that measured them.", GROUP_M, kinds=K)
def _measured(ctx: Ctx, w):
    return _at(ctx, _prog(ctx, "windows").windows[w.id]["measured_us"])


@term("written(w)", "an instant", "When the decoder's correction for window w was written into "
      "the classical memory; none if it never was.", GROUP_M, kinds=K)
def _written(ctx: Ctx, w):
    W = _prog(ctx, "windows").windows[w.id]
    return None if W.get("written_us") is None else _at(ctx, W["written_us"])


@term("reads(x)", "a set of windows", "The decoded windows holding the outcomes classically "
      "controlled op x's guard (or guarded frame row x's condition) reads.", GROUP_M,
      domain=True, kinds=K)
def _reads(ctx: Ctx, x):
    M = _prog(ctx, "windows")
    ready_of = {o["cvar"]: o.get("event") for o in M.outcomes if o.get("cvar")}
    by_event = {W["event"]: Window(i) for i, W in enumerate(M.windows)
                if W.get("decoded_by") is not None}
    if isinstance(x, Op):
        names = ((x.facts.get("guard") or {}).get("cond") or {}).get("cvars") or []
    elif isinstance(x, FrameRow):
        names = (M.frames[x.id].get("guard") or {}).get("cvars") or []
    else:
        raise EvalError(f"reads needs a guarded op or a frame row, got {x!r}")
    out = []
    for n in names:
        ev = ready_of.get(n)
        if ev in by_event:
            out.append(by_event[ev])
    return out


# --------------------------------------------------------------- blocks

def _ions_dispatch(orig):
    def fn(ctx: Ctx, o=None):
        if isinstance(o, Block):
            M = _prog(ctx, "blocks")
            return [Ion(g) for g in M.blocks[o.id]["ions"]]
        return orig(ctx, o)
    return fn


TERMS["ions"].fn = _ions_dispatch(TERMS["ions"].fn)
TERMS["ions"].meaning += "  At the machine level, ions(b) is block b's ions."


# ============================================================================ geometry
#
# Read off the top composite: a floorplan is flat (places and junctions placed directly in
# it), so every footprint and every road's route is in one frame.

def _geom(ctx: Ctx) -> dict:
    M = _M(ctx)

    def build():
        lib = M.lib
        top = lib[lib.top]
        foot, route = {}, {}
        for i in top.instances:
            m = lib[i.master]
            if m.kind != "leaf":
                raise NotApplicable("the floorplan is not flat: a composite inside it has its "
                                    "own frame")
            x0, y0, x1, y1 = m.bbox
            foot[Place(i.name)] = (i.x + x0, i.y + y0, i.x + x1, i.y + y1)
        for c in top.channels:
            if c.kind != "wire":
                route[Road(f"/{c.name}")] = [(float(x), float(y)) for x, y in c.points]
        return {"foot": foot, "route": route}
    return ctx.once(("machine", "geometry"), build)


def _boxes_overlap(a, b, eps=1e-9) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > eps) and (min(a[3], b[3]) - max(a[1], b[1]) > eps)


def _seg_box(p, q, box, eps=1e-6) -> bool:
    """Does segment pq pass through the open box (shrunk by eps)?"""
    x0, y0, x1, y1 = box[0] + eps, box[1] + eps, box[2] - eps, box[3] - eps
    if x0 >= x1 or y0 >= y1:
        return False
    # Liang-Barsky clipping
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for pp, qq in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if pp == 0:
            if qq <= 0:
                return False
        else:
            r = qq / pp
            if pp < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
    return t0 < t1


def _segs_meet(p1, p2, q1, q2, eps=1e-9) -> bool:
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < eps else (1 if v > 0 else -1)

    def on(a, b, c):
        return (min(a[0], b[0]) - eps <= c[0] <= max(a[0], b[0]) + eps and
                min(a[1], b[1]) - eps <= c[1] <= max(a[1], b[1]) + eps)
    o1, o2, o3, o4 = orient(p1, p2, q1), orient(p1, p2, q2), orient(q1, q2, p1), orient(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and on(p1, p2, q1)) or (o2 == 0 and on(p1, p2, q2)) or
            (o3 == 0 and on(q1, q2, p1)) or (o4 == 0 and on(q1, q2, p2)))


@term("placed", "a set of places", "Every placed gadget, junctions included.", GROUP_F,
      domain=True, kinds=K)
def _placed(ctx: Ctx):
    M = _M(ctx)
    return M.places + M.junction_places


@term("overlaps(p, q)", "true or false", "Whether the footprints of places p and q overlap "
      "(touching along an edge is not overlapping).", GROUP_F, kinds=K)
def _overlaps(ctx: Ctx, p, q):
    f = _geom(ctx)["foot"]
    return _boxes_overlap(f[p], f[q])


@term("owners(r)", "a set of places", "The places (or junctions) whose doors road r ends at.",
      GROUP_F, domain=True, kinds=K)
def _owners(ctx: Ctx, r):
    M = _M(ctx)
    return [Place(M.doors[d][0]) for d in M.road_ends[r]]


@term("passes_through(r, p)", "true or false", "Whether road r's route runs through the inside "
      "of place p's footprint.", GROUP_F, kinds=K)
def _passes_through(ctx: Ctx, r, p):
    g = _geom(ctx)
    pts = g["route"].get(r)
    if pts is None:
        raise EvalError(f"{r!r} has no route")
    box = g["foot"][p]
    return any(_seg_box(a, b, box) for a, b in zip(pts, pts[1:]))


@term("crosses(r1, r2)", "true or false", "Whether the routes of roads r1 and r2 meet anywhere: "
      "two roads may only meet at a junction, through its arms.", GROUP_F, kinds=K)
def _crosses(ctx: Ctx, r1, r2):
    g = _geom(ctx)
    a, b = g["route"][r1], g["route"][r2]
    return any(_segs_meet(p1, p2, q1, q2) for p1, p2 in zip(a, a[1:]) for q1, q2 in zip(b, b[1:]))


@term("reachable(a, b)", "true or false", "Whether a bundle leaving door a can reach door b "
      "along roads, crossing junctions (places are endpoints, never thoroughfares).", GROUP_F,
      kinds=K)
def _reachable(ctx: Ctx, a, b):
    M = _M(ctx)

    def build():
        adj: dict = defaultdict(set)
        for r, (x, y) in M.road_ends.items():
            adj[x].add(y)
            adj[y].add(x)
        arms: dict = defaultdict(list)
        for d, (path, _p) in M.doors.items():
            if M.leaf[path].master.family == JUNCTION_FAMILY:
                arms[path].append(d)
        for path, ds in arms.items():
            for x in ds:
                for y in ds:
                    if x != y:
                        adj[x].add(y)
        return adj
    adj = ctx.once(("machine", "adjacency"), build)
    seen = ctx.once(("machine", "reach", a), lambda: _bfs(adj, a))
    return b in seen


def _bfs(adj, src):
    seen = {src}
    todo = [src]
    while todo:
        u = todo.pop()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                todo.append(v)
    return seen


@term("region(p)", "a name", "The region the floorplan puts place p in: \"memory\", "
      "\"compute\", \"factory\", \"supply\", \"control\", or \"traffic\" for a junction.",
      GROUP_F, kinds=K)
def _region(ctx: Ctx, p):
    M = _M(ctx)
    regions = M.regions or (M.lib[M.lib.top].params.get("regions") or {})
    if not regions:
        raise NotApplicable("this floorplan declares no regions")
    return regions.get(_place_path(M, p), "traffic" if M.leaf[_place_path(M, p)].master.family
                       == JUNCTION_FAMILY else None)


@term("home_region(f)", "a name", "The region a place of family f belongs in: a zone in "
      "memory; preparation, syndrome extraction, transversal gates, lattice surgery and readout "
      "in compute; factories and injection in factory; depots in supply; the decoder and the "
      "classical memory in control; junctions in traffic.", GROUP_F, kinds=K)
def _home_region(ctx: Ctx, f):
    from ..gadget.floorplan import HOME_REGION
    return HOME_REGION.get(f)


# ============================================================================ traffic

@term("waits(c)", "true or false", "Whether some ion of carry c is taken in at the far end "
      "later than it gets there: the bundle waited on road c.", GROUP_M, kinds=K)
def _waits(ctx: Ctx, c):
    o = _activity(c, "waits")
    if o.kind != "carry":
        raise EvalError(f"{o.label()} is not a carry")
    tau = o.facts["transit"]
    return any(take - (dep + tau) > 1e-6 for dep, take in zip(o.facts["deps"], o.facts["takes"]))


@term("crowd(c)", "ions", "The most ions of carry c on its road at one moment: each is on "
      "it from when it leaves until it is taken in at the far end, travelling or stopped.",
      GROUP_M, kinds=K)
def _crowd(ctx: Ctx, c):
    o = _activity(c, "crowd")
    if o.kind != "carry":
        raise EvalError(f"{o.label()} is not a carry")
    # an ion taken in at t is off the road before one that leaves at t is on it
    edges = sorted([(dep, 1) for dep in o.facts["deps"]] + [(take, -1) for take in o.facts["takes"]])
    n = most = 0
    for _, d in edges:
        n += d
        most = max(most, n)
    return most


@term("wait(c)", "microseconds", "How long the last ion of carry c waits at the far end of its "
      "road before it is taken in.", GROUP_M, kinds=K)
def _wait(ctx: Ctx, c):
    o = _activity(c, "wait")
    tau = o.facts["transit"]
    return max([take - (dep + tau) for dep, take in zip(o.facts["deps"], o.facts["takes"])] + [0.0])


@term("ion_sites(c)", "a number", "How much shuttling carry c is: its ions times the sites of "
      "its road.", GROUP_M, kinds=K)
def _ion_sites(ctx: Ctx, c):
    o = _activity(c, "ion_sites")
    return len(o.ions) * int(_M(ctx).nets[o.facts["road"].id].length)


@term("traffic()", "a number", "How much shuttling the whole execution is: the sum of "
      "ion_sites over every carry.", GROUP_M, kinds=K)
def _traffic(ctx: Ctx):
    M = _prog(ctx, "carries")
    return sum(len(c.ions) * int(M.nets[c.facts["road"].id].length) for c in M.carries)
