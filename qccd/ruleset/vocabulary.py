"""The words a rule may use, what each one means, and how it is read off an execution.

Every term has a definition in words -- the one the Rules page prints -- and an
implementation per kind of execution.  A term that does not exist for a kind (a programme's
`steps` in a paper's timed schedule, say) raises `NotApplicable`, and a rule that needs it
is reported as not applying to that execution: never as passing.

Adding a word is adding one `@term` here; adding a rule needs no code at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from .model import (
    GATE_KINDS, TRANSPORT_KINDS, CircuitOp, Execution, Hop, Instant, Ion, Op, Rail, Site, Step,
)

__all__ = ["Term", "TERMS", "NotApplicable", "EvalError", "Ctx", "term"]


class NotApplicable(Exception):
    """The execution has no such thing (a schedule has no steps; no circuit was given)."""


class EvalError(Exception):
    """A value the rule needs is missing or of the wrong kind."""


@dataclass
class Term:
    name: str
    signature: str          # how it is written, e.g. "occupancy(x, t)"
    returns: str            # "a number", "a set of sites", ...
    meaning: str            # the definition, in words
    group: str              # hardware | execution | time | circuit | model
    fn: Callable
    domain: bool = False    # a set a variable may range over
    kinds: tuple[str, ...] = ("programme", "schedule")
    programme: str = ""     # how it is read off a programme, when that needs saying
    schedule: str = ""      # how it is read off a timed schedule


TERMS: dict[str, Term] = {}


def term(signature: str, returns: str, meaning: str, group: str, *, domain: bool = False,
         kinds: tuple[str, ...] = ("programme", "schedule"), programme: str = "",
         schedule: str = "", name: str | None = None):
    def deco(fn: Callable) -> Callable:
        nm = name or signature.split("(")[0]
        TERMS[nm] = Term(nm, signature, returns, meaning, group, fn, domain, kinds,
                         programme, schedule)
        return fn
    return deco


class Ctx:
    """What a term can see: the execution, the rule set's parameters, and caches."""

    def __init__(self, exe: Execution, params: dict[str, Any] | None = None):
        self.exe = exe
        self.hw = exe.hardware
        self.params = params or {}
        self.cache: dict[Any, Any] = {}

    def need(self, kind: str, what: str) -> None:
        if self.exe.kind != kind:
            other = "a timed schedule" if self.exe.kind == "schedule" else "a programme"
            raise NotApplicable(f"{what} exist only in a {kind}, and this is {other}")

    def once(self, key: Any, make: Callable[[], Any]) -> Any:
        v = self.cache.get(key)
        if v is None:
            v = make()
            self.cache[key] = v
        return v


def _op(x: Any, fn: str) -> Op:
    if isinstance(x, Op):
        return x
    if isinstance(x, Hop):
        return x.op
    raise EvalError(f"{fn} needs an operation, got {_show(x)}")


def _show(x: Any) -> str:
    if hasattr(x, "label"):
        return x.label()
    return repr(x)


def _instant(ctx: Ctx, t: Any, fn: str) -> int:
    if isinstance(t, Instant):
        return t.id
    raise EvalError(f"{fn} needs an instant, got {_show(t)}")


def _ops(ctx: Ctx, kinds, k=None) -> list:
    if k is not None:
        ctx.need("programme", "steps")
        if not isinstance(k, Step):
            raise EvalError(f"expected a step, got {_show(k)}")
        return ctx.exe.ops_of(kinds, step=k.id)
    return ctx.exe.ops_of(kinds)


# ============================================================================ hardware

@term("sites", "a set of sites", "Every place an ion can stand: the traps, the docks, the slots "
      "of a loop.  A node where rails cross only to pass through it is not a site.",
      "hardware", domain=True)
def _sites(ctx: Ctx):
    return ctx.hw.sites


@term("junctions", "a set of nodes", "The nodes where three or more rails meet, whatever the "
      "device calls them.  An ion crosses a junction; whether it may stop there is a rule.",
      "hardware", domain=True)
def _junctions(ctx: Ctx):
    return ctx.hw.junctions


@term("nodes", "a set of nodes", "Every node of the device: its sites and its junctions.",
      "hardware", domain=True)
def _nodes(ctx: Ctx):
    return ctx.hw.nodes


@term("rails", "a set of rails", "The rails (segments) along which ions are carried between "
      "nodes.", "hardware", domain=True)
def _rails(ctx: Ctx):
    return ctx.hw.rails


@term("capacity(x)", "a number", "How many ions the site or rail x may hold at once, as the "
      "device declares it.", "hardware")
def _capacity(ctx: Ctx, x):
    try:
        return ctx.hw.capacity[x]
    except KeyError:
        raise EvalError(f"the device declares no capacity for {_show(x)}") from None


@term("degree(n)", "a number", "How many rails meet at node n.", "hardware")
def _degree(ctx: Ctx, n):
    return ctx.hw.degree.get(n, 0)


@term("zone(s)", "a name", "The zone type of site s (for example \"gate\", \"storage\", \"O\").",
      "hardware")
def _zone(ctx: Ctx, s):
    return ctx.hw.zone.get(s)


@term("can(s, a)", "true or false", "Whether site s is equipped for ability a: \"gate\" (a laser "
      "gate), \"spam\" (preparation and measurement) or \"cool\" (cooling).", "hardware",
      schedule="A paper's device that declares no abilities is taken to allow everything; its "
               "own rules then say where each operation may run.")
def _can(ctx: Ctx, s, a):
    ab = ctx.hw.abilities.get(s)
    if ab is None:
        return True
    return a in ab


@term("neighbours(s1, s2)", "true or false", "Whether sites s1 and s2 are neighbouring zones "
      "that a two-ion gate may span (some devices gate across two zones of one rail).",
      "hardware")
def _neighbours(ctx: Ctx, a, b):
    return b in ctx.hw.neighbours.get(a, ()) or a in ctx.hw.neighbours.get(b, ())


@term("rails_at(n)", "a set of rails", "The rails that end at node n.", "hardware", domain=True)
def _rails_at(ctx: Ctx, n):
    return list(ctx.hw.rails_at.get(n, ()))


@term("ends(r)", "a set of nodes", "The two nodes rail r joins.", "hardware", domain=True)
def _ends(ctx: Ctx, r):
    return list(ctx.hw.ends[r])


@term("angle(n, r1, r2)", "degrees", "The angle at node n between rails r1 and r2, as drawn.",
      "hardware", kinds=("programme",))
def _angle(ctx: Ctx, n, r1, r2):
    from ..verify.rules import rail_angle_deg
    # rounded so that a drawn 60 degrees is 60, not 59.999999999
    return round(rail_angle_deg(ctx.hw.arch.device, n.id, r1.id, r2.id), 6)


@term("lies_on(n, r)", "true or false", "Whether node n lies on rail r's line without being one "
      "of its ends -- a rail drawn straight through a node it does not stop at.", "hardware",
      kinds=("programme",))
def _lies_on(ctx: Ctx, n, r):
    from ..verify.rules import _point_on_segment, _xy
    dev = ctx.hw.arch.device
    a, b = ctx.hw.ends[r]
    if n in (a, b):
        return False
    return _point_on_segment(_xy(dev.nodes[n.id].pos), _xy(dev.nodes[a.id].pos), _xy(dev.nodes[b.id].pos))


@term("cross(r1, r2)", "true or false", "Whether rails r1 and r2 cross each other at a point "
      "that is not a node they share.", "hardware", kinds=("programme",))
def _cross(ctx: Ctx, r1, r2):
    from ..verify.rules import _segments_cross, _xy
    dev = ctx.hw.arch.device
    a1, b1 = ctx.hw.ends[r1]
    a2, b2 = ctx.hw.ends[r2]
    if {a1, b1} & {a2, b2}:
        return False
    p = lambda s: _xy(dev.nodes[s.id].pos)
    return _segments_cross(p(a1), p(b1), p(a2), p(b2))


@term("max_junction_degree()", "a number", "The most rails a junction of this device may join, "
      "as its document declares (6 when it says nothing: a junction of a trap built in more "
      "than one layer joins up to six).", "hardware", kinds=("programme",))
def _max_junction_degree(ctx: Ctx):
    return ctx.hw.numbers.get("max_junction_degree", 6)


@term("min_rail_angle()", "degrees", "The smallest angle two rails meeting at a node may make, as "
      "the device's document declares (60 degrees when it says nothing).", "hardware",
      kinds=("programme",))
def _min_rail_angle(ctx: Ctx):
    return ctx.hw.numbers.get("min_rail_angle_deg", 60.0)


@term("max_classes()", "a number", "How many different motions the device's control system can "
      "drive in one step.", "hardware", kinds=("programme",))
def _max_classes(ctx: Ctx):
    return ctx.hw.numbers.get("max_simd_classes", 1)


@term("control()", "a name", "How the device's electrodes are driven: \"direct\" (every "
      "electrode its own signal) or a broadcast scheme where many share one waveform.",
      "hardware", kinds=("programme",))
def _control(ctx: Ctx):
    return ctx.hw.numbers.get("control_model", "simd_classes")


@term("priced(j)", "true or false", "Whether the device declares the time it takes to cross "
      "junction j, at j's number of rails.", "hardware", kinds=("programme",))
def _priced(ctx: Ctx, j):
    return ctx.hw.degree.get(j, 0) in ctx.hw.numbers.get("priced_degrees", ())


@term("heat_budget()", "quanta", "The hottest an ion may be when a two-ion gate starts (its "
      "motional quanta n̄), as the device declares it; no limit when it declares none.",
      "hardware", kinds=("programme",))
def _heat_budget(ctx: Ctx):
    return ctx.exe.facts.get("heat_budget", math.inf)


@term("rotated(n)", "true or false", "Whether node n is a slot of a loop or rail that this "
      "programme moves as a whole (a conveyor), which carries every ion through every slot.",
      "hardware", kinds=("programme",))
def _rotated(ctx: Ctx, n):
    return n in ctx.exe.facts.get("rotated_nodes", frozenset())


# ------------------------------------------------------------------- C2LR (docs/c2lr.md)

@term("legs", "a set of sites", "The legs of a C2LR lattice: the trap at the centre of every "
      "wire between two junctions, each holding one two-ion crystal (one ion of this model).  "
      "Empty on any other device.", "hardware", domain=True, kinds=("programme",))
def _legs(ctx: Ctx):
    return ctx.hw.numbers.get("c2lr_legs", [])


@term("c2lr_device()", "true or false", "Whether the device is a C2LR swap-or-stay lattice: it "
      "declares C2LR control, and its drawing is a lattice of junctions with one leg on every "
      "wire (arXiv:2403.00756).", "hardware", kinds=("programme",))
def _c2lr_device(ctx: Ctx):
    return bool(ctx.hw.numbers.get("c2lr_device"))


@term("c2lr_wiring()", "true or false", "Whether a device that declares C2LR control is driven "
      "as C2LR is: a fixed number of shared analog signals, plus one digital input and one "
      "switch per leg, over a lattice of legs.", "hardware", kinds=("programme",))
def _c2lr_wiring(ctx: Ctx):
    return not ctx.hw.numbers.get("c2lr_wiring_problems")


@term("com_frequency_held()", "true or false", "Whether the device's swap-or-stay step records "
      "the centre-of-mass frequency it holds a crystal at during a shift.  A property of the "
      "waveform, recorded, not decided from a programme; a device that records none leaves the "
      "rule unchecked.", "model", kinds=("programme",))
def _com_held(ctx: Ctx):
    com = ctx.hw.numbers.get("c2lr_com_mhz")
    if com is None:
        raise NotApplicable("the device's swap_or_stay primitive records no COM frequency")
    return True


# ============================================================================ execution

@term("ions", "a set of ions", "Every ion of the execution.", "execution", domain=True)
def _ions(ctx: Ctx, o=None):
    if o is not None:
        return list(_op(o, "ions").ions)
    return ctx.exe.ions


@term("operations", "a set of operations", "Every operation of the execution, of any kind.",
      "execution", domain=True)
def _operations(ctx: Ctx, k=None):
    return _ops(ctx, None, k)


@term("gates", "a set of operations", "Every laser gate, on one ion or on two.", "execution",
      domain=True)
def _gates(ctx: Ctx, k=None):
    return _ops(ctx, GATE_KINDS, k)


@term("two_qubit_gates", "a set of operations", "Every gate that acts on two ions together.",
      "execution", domain=True)
def _gates2(ctx: Ctx, k=None):
    return _ops(ctx, ("gate2",), k)


@term("single_qubit_gates", "a set of operations", "Every gate that acts on one ion.",
      "execution", domain=True)
def _gates1(ctx: Ctx, k=None):
    return _ops(ctx, ("gate1",), k)


@term("measurements", "a set of operations", "Every measurement of an ion.", "execution",
      domain=True)
def _measurements(ctx: Ctx, k=None):
    return _ops(ctx, ("measure",), k)


@term("resets", "a set of operations", "Every reset (preparation) of an ion.", "execution",
      domain=True)
def _resets(ctx: Ctx, k=None):
    return _ops(ctx, ("reset",), k)


@term("coolings", "a set of operations", "Every cooling operation.", "execution", domain=True)
def _coolings(ctx: Ctx, k=None):
    return _ops(ctx, ("cool",), k)


@term("swaps", "a set of operations", "Every reordering of two ions inside one chain.",
      "execution", domain=True)
def _swaps(ctx: Ctx, k=None):
    return _ops(ctx, ("swap",), k)


@term("transport", "a set of operations", "Every operation that carries an ion along the rails.",
      "execution", domain=True,
      programme="One operation per moving ion per step, from where it stood to where it stops.",
      schedule="Each split, merge, rail crossing, junction entry and exit, or hop the paper "
               "records.")
def _transport(ctx: Ctx, k=None):
    return _ops(ctx, TRANSPORT_KINDS, k)


@term("splits", "a set of operations", "Every operation that lifts an ion out of the chain it "
      "stood in.", "execution", domain=True,
      programme="A move whose motion includes a split (the device's movement class says so).")
def _splits(ctx: Ctx, k=None):
    if ctx.exe.kind == "programme":
        return [o for o in _ops(ctx, ("move",), k) if o.facts.get("split")]
    return _ops(ctx, ("split",), k)


@term("hops", "a set of hops", "Every rail crossed by an ion: one transport operation over three "
      "rails is three hops.", "execution", domain=True, kinds=("programme",))
def _hops(ctx: Ctx, k=None):
    ctx.need("programme", "hops")
    if k is not None:
        return ctx.exe.hops_of(k.id)
    return ctx.exe.hops


@term("steps", "a set of steps", "The steps of a programme: one instruction each, run one after "
      "another; everything in one step happens together.", "execution", domain=True,
      kinds=("programme",))
def _steps(ctx: Ctx):
    ctx.need("programme", "steps")
    return ctx.once("steps", lambda: [Step(i) for i in range(len(ctx.exe.steps))])


@term("kind(o)", "a name", "What operation o is: \"gate2\", \"gate1\", \"measure\", \"reset\", "
      "\"cool\", \"swap\", or a transport kind (\"move\", \"split\", \"merge\", \"shuttle\", "
      "\"junction_enter\", \"junction_exit\", \"hop\").", "execution")
def _kind(ctx: Ctx, o):
    return _op(o, "kind").kind


@term("ions(o)", "a set of ions", "The ions operation o acts on or carries.", "execution",
      domain=True, name="ions(o)")
def _ions_of(ctx: Ctx, o):
    return list(_op(o, "ions").ions)


@term("site(o)", "a site", "Where operation o happens.  For a gate, the site its ions stand in "
      "when it starts; none for transport and for operations on every ion at once.",
      "execution")
def _site(ctx: Ctx, o):
    return _op(o, "site").site


@term("declared_sites(o)", "a set of sites", "The sites the programme itself names for "
      "operation o, if it names any (a claim the replay checks).", "execution", domain=True,
      schedule="Empty: where a paper's operation says it happens is checked with its ions "
               "(found).")
def _declared_sites(ctx: Ctx, o):
    return [Site(s) for s in _op(o, "declared_sites").facts.get("declared_sites", ())]


@term("start(o)", "an instant", "The instant operation o begins.", "execution",
      programme="The instant before o's step.")
def _start(ctx: Ctx, o):
    return Instant(_op(o, "start").start)


@term("end(o)", "an instant", "The instant operation o is over.", "execution",
      programme="The instant after o's step.")
def _end(ctx: Ctx, o):
    return Instant(_op(o, "end").end)


@term("duration(o)", "microseconds", "How long operation o lasts.", "execution",
      programme="Its step's duration under the device's clock (the slowest operation of the "
                "step).")
def _duration(ctx: Ctx, o):
    o = _op(o, "duration")
    return o.t1 - o.t0


@term("step(o)", "a step", "The step operation o belongs to.", "execution", kinds=("programme",))
def _step(ctx: Ctx, o):
    ctx.need("programme", "steps")
    return Step(_op(o, "step").step)


@term("from(o)", "a node", "Where transport operation o (or hop o) picks its ion up.",
      "execution")
def _from(ctx: Ctx, o):
    if isinstance(o, Hop):
        return o.src
    o = _op(o, "from")
    return o.path[0] if o.path else None


@term("to(o)", "a node", "Where transport operation o (or hop o) leaves its ion.", "execution")
def _to(ctx: Ctx, o):
    if isinstance(o, Hop):
        return o.dst
    o = _op(o, "to")
    return o.path[-1] if o.path else None


@term("path(o)", "a list of nodes", "The nodes transport operation o carries its ion through, "
      "first to last.", "execution", domain=True)
def _path(ctx: Ctx, o):
    return list(_op(o, "path").path)


@term("passes(o)", "a set of nodes", "The nodes transport operation o carries its ion through "
      "without stopping: its path without the two ends.", "execution", domain=True)
def _passes(ctx: Ctx, o):
    p = _op(o, "passes").path
    return list(p[1:-1])


@term("crossed(o)", "a set of junctions", "The junctions transport operation o carries its ion "
      "into, on the way or to stop.", "execution", domain=True)
def _crossed(ctx: Ctx, o):
    o = _op(o, "crossed")
    js = ctx.once("junction_set", lambda: frozenset(ctx.hw.junctions))
    return [n for n in o.path[1:] if n in js] + [n for n in o.facts.get("crossed", ()) if n in js]


@term("rail(h)", "a rail", "The rail hop h crosses.", "execution", kinds=("programme",))
def _rail(ctx: Ctx, h):
    if not isinstance(h, Hop):
        raise EvalError(f"rail needs a hop, got {_show(h)}")
    return h.rail


@term("rails_used(o)", "a set of rails", "The rails transport operation o uses.", "execution",
      domain=True)
def _rails_used(ctx: Ctx, o):
    return list(_op(o, "rails_used").rails)


@term("loop(h)", "a loop name", "The closed loop hop h runs along, or none.", "execution",
      kinds=("programme",))
def _loop(ctx: Ctx, h):
    if not isinstance(h, Hop):
        raise EvalError(f"loop needs a hop, got {_show(h)}")
    return h.loop


@term("shift(h)", "a number", "How many slots along its loop hop h moves its ion, with a sign "
      "for the direction.", "execution", kinds=("programme",))
def _shift(ctx: Ctx, h):
    if not isinstance(h, Hop):
        raise EvalError(f"shift needs a hop, got {_show(h)}")
    return h.shift


@term("motion(o)", "a name", "The waveform transport operation o needs: its movement class and, "
      "hop by hop, its direction -- along a loop as \"+1\"/\"-1\", on a spur as inward/outward, "
      "elsewhere as the lab-frame axis.  Two ions with the same motion can ride one waveform.",
      "execution",
      schedule="The class the paper's schedule records for the operation.")
def _motion(ctx: Ctx, o):
    return _op(o, "motion").facts.get("motion")


@term("simultaneous(a, b)", "true or false", "Whether operations a and b happen at the same "
      "time.", "execution",
      programme="In the same step.",
      schedule="Their time intervals overlap (an interval ends where the next may begin).")
def _simultaneous(ctx: Ctx, a, b):
    a, b = _op(a, "simultaneous"), _op(b, "simultaneous")
    if ctx.exe.kind == "programme":
        return a.step == b.step
    tol = ctx.exe.settings.get("tol", 1e-6)
    return a.t0 < b.t1 - tol and b.t0 < a.t1 - tol


@term("disjoint(A, B)", "true or false", "Whether sets A and B have nothing in common.",
      "execution")
def _disjoint(ctx: Ctx, a, b):
    return not (set(a) & set(b))


@term("size(A)", "a number", "How many things set A holds.", "execution")
def _size(ctx: Ctx, a):
    return len(a)


@term("min(x, y)", "a number", "The smaller of two numbers.", "execution")
def _min2(ctx: Ctx, *xs):
    return min(xs)


@term("max(x, y)", "a number", "The larger of two numbers.", "execution")
def _max2(ctx: Ctx, *xs):
    return max(xs)


@term("abs(x)", "a number", "The size of a number, without its sign.", "execution")
def _abs(ctx: Ctx, x):
    return abs(x)


@term("approx(x, y)", "true or false", "Whether two durations agree to within the execution's "
      "rounding (one part in a million).", "execution")
def _approx(ctx: Ctx, x, y):
    if x is None or y is None:
        return x is None and y is None
    tol = ctx.exe.settings.get("tol", 1e-6)
    return abs(x - y) <= tol * max(1.0, abs(x), abs(y))


# ------------------------------------------------------------------- steps (programmes)

def _stepfact(ctx: Ctx, k, key, fn):
    ctx.need("programme", "steps")
    if not isinstance(k, Step):
        raise EvalError(f"{fn} needs a step, got {_show(k)}")
    return ctx.exe.steps[k.id].get(key)


@term("is_transport(k)", "true or false", "Whether step k moves ions.", "execution",
      kinds=("programme",))
def _is_transport(ctx: Ctx, k):
    return _stepfact(ctx, k, "type", "is_transport") == "simd"


@term("carries_gates(k)", "true or false", "Whether step k also names gates.", "execution",
      kinds=("programme",))
def _carries_gates(ctx: Ctx, k):
    return bool(_stepfact(ctx, k, "carries_gates", "carries_gates"))


@term("mode(k)", "a name", "Whether transport step k moves ions inside traps (\"intra\") or "
      "between them (\"inter\").", "execution", kinds=("programme",))
def _mode(ctx: Ctx, k):
    return _stepfact(ctx, k, "mode", "mode")


@term("class_declared(k)", "true or false", "Whether transport step k's movement class is one "
      "the device declares (or the device declares none to check against).", "execution",
      kinds=("programme",))
def _class_declared(ctx: Ctx, k):
    return bool(_stepfact(ctx, k, "class_declared", "class_declared"))


@term("drivable(k)", "true or false", "Whether the device's control channels can drive step k: "
      "one channel carries one waveform, so every ion a channel moves must move the same way, "
      "and a site may sit a step out only if it has its own switch.", "execution",
      kinds=("programme",))
def _drivable(ctx: Ctx, k):
    return bool(_stepfact(ctx, k, "drivable", "drivable"))


@term("exchange(x)", "true or false", "Whether x -- a step, an instant, an operation or a hop -- "
      "belongs to a C2LR swap-or-stay step, whose crystals exchange through the junctions by "
      "the device's matching: the routes are derived from the device, never read from the "
      "programme, so a programme cannot claim this for a move it wrote itself.", "execution",
      programme="An instant is the one during such a step; an operation or hop is one of its "
                "moves.",
      schedule="Never: a paper's timed schedule has no swap-or-stay steps, so R2, R3, R5 and "
               "R22 judge all of it.")
def _exchange(ctx: Ctx, x):
    if ctx.exe.kind != "programme":
        return False
    if isinstance(x, Step):
        return bool(ctx.exe.steps[x.id].get("exchange"))
    if isinstance(x, Instant):
        return x.id in ctx.exe.facts.get("exchange_instants", frozenset())
    if isinstance(x, (Op, Hop)):
        return bool(_op(x, "exchange").facts.get("exchange"))
    raise EvalError(f"exchange needs a step, an instant, an operation or a hop, got {_show(x)}")


@term("c2lr_step(k)", "true or false", "Whether step k is a C2LR template step: a swap-or-stay "
      "or a reorder-or-not, played by the device's shared waveforms.", "execution",
      kinds=("programme",))
def _c2lr_step(ctx: Ctx, k):
    return bool(_stepfact(ctx, k, "c2lr_template", "c2lr_step"))


@term("form(k)", "a name", "How swap-or-stay step k pairs the legs: \"diagonal\" (every junction "
      "pairs the same two of its legs, as demonstrated) or \"checkerboard\" (two pairs at the "
      "junctions of one colour, the proposal's, assumed).", "execution", kinds=("programme",))
def _form(ctx: Ctx, k):
    return _stepfact(ctx, k, "c2lr_form", "form")


@term("matched(k)", "true or false", "Whether swap-or-stay step k is the device's matching: every "
      "crystal it shifts toward a junction has a partner leg there under the step's variation, "
      "and the device offers that variation.", "execution", kinds=("programme",))
def _matched(ctx: Ctx, k):
    return not _stepfact(ctx, k, "c6", "matched")


@term("exits_as_entered(k)", "true or false", "Whether every crystal that leaves, in step k, a leg "
      "holding two under C1's exception is the one that entered it, leaving by the step type it "
      "entered by -- and whether every crystal step k shifts toward a junction could reach it.",
      "execution", kinds=("programme",))
def _exits_as_entered(ctx: Ctx, k):
    return not _stepfact(ctx, k, "c1", "exits_as_entered")


@term("bits_only(k)", "true or false", "Whether transport step k, if it is of a C2LR class, "
      "chooses nothing per leg but the direction bit: it is the device's template, not routes "
      "the programme wrote.", "execution", kinds=("programme",))
def _bits_only(ctx: Ctx, k):
    return not _stepfact(ctx, k, "c2", "bits_only")


@term("independent_axes(k)", "true or false", "Whether, in swap-or-stay step k, a crystal on a "
      "horizontal leg and one on a vertical leg set their bits independently: true of the "
      "template, which carries one bit per crystal and no field per axis.", "execution",
      kinds=("programme",))
def _independent_axes(ctx: Ctx, k):
    return bool(_stepfact(ctx, k, "exchange", "independent_axes"))


@term("one_waveform(k)", "true or false", "Whether swap-or-stay step k is one shared waveform: "
      "the replay prices it as one primitive, whose time is the step's however many crystals "
      "move.", "execution", kinds=("programme",))
def _one_waveform(ctx: Ctx, k):
    return bool(_stepfact(ctx, k, "exchange", "one_waveform"))


@term("heats_every_crystal(k)", "true or false", "Whether C2LR step k heats every crystal on the "
      "device -- the ones that move and the ones that stay -- as every waveform plays on every "
      "leg.", "execution", kinds=("programme",))
def _heats_every(ctx: Ctx, k):
    return not _stepfact(ctx, k, "c9", "heats_every_crystal")


@term("swap_paid(o)", "true or false", "Whether the reordering that brings split o's ion to the "
      "end of its chain was paid for.", "execution",
      programme="The step records the gate swaps it pays.",
      schedule="The split carries a reorder the paper's model charges.")
def _swap_paid(ctx: Ctx, o):
    return bool(_op(o, "swap_paid").facts.get("swap_paid"))


@term("at_chain_end(o)", "true or false", "Whether split o takes the ion at the end of its chain "
      "that faces the rail it leaves by.", "execution",
      programme="A programme does not record the order inside a chain, so only a chain of at "
                "most two ions is known to have every ion at an end.",
      schedule="Read from the chain order, tracked event by event as the paper's model does.")
def _at_chain_end(ctx: Ctx, o):
    o = _op(o, "at_chain_end")
    return bool(o.facts.get("at_chain_end"))


# ------------------------------------------------------------------- time and state

@term("instants", "a set of instants", "Every moment at which the state of the machine is "
      "judged.", "time", domain=True,
      programme="The moment before each step, the moment during each step that moves ions, and "
                "the end.",
      schedule="The start, and every time an operation begins or ends.")
def _instants(ctx: Ctx):
    return ctx.once("instants", lambda: [Instant(i) for i in range(len(ctx.exe.instants))])


@term("occupancy(x, t)", "a number", "How many ions are in site, junction or rail x at instant "
      "t.", "time",
      programme="During a step that moves ions, a site counts the ions that rest there before or "
                "after the step, whichever is more, plus every ion that passes through it: the "
                "programme does not order what happens inside a step, so nothing may be "
                "assumed to leave first.  A rail counts the ions crossing it.",
      schedule="An ion counts in a trap from the moment it starts to arrive until the moment it "
               "has left, and on a rail or in a junction while it is there.")
def _occupancy(ctx: Ctx, x, t):
    return ctx.exe.occupancy.at(x, _instant(ctx, t, "occupancy"))


@term("entered(s, t)", "true or false", "Whether leg s holds two crystals at instant t under "
      "C1's exception: one of them entered it by a swap-or-stay enter -- moved toward the "
      "junction into a leg whose crystal stayed -- and has not left.", "time",
      kinds=("programme",),
      programme="During a step, it holds if it held before the step or holds after it.")
def _entered(ctx: Ctx, s, t):
    line = ctx.exe.timelines.get("entered")
    return bool(line.at(s, _instant(ctx, t, "entered"))) if line is not None else False


@term("final()", "an instant", "The last instant of the execution, when everything is over.",
      "time")
def _final(ctx: Ctx):
    return Instant(len(ctx.exe.instants) - 1)


@term("position(i, t)", "a site", "The site where ion i stands at instant t; none while it is in "
      "motion.", "time")
def _position(ctx: Ctx, i, t):
    if not isinstance(i, Ion):
        raise EvalError(f"position needs an ion, got {_show(i)}")
    return ctx.exe.position.at(i, _instant(ctx, t, "position"))


@term("heat(i, t)", "quanta", "Ion i's motional energy n̄ (in quanta) at instant t, under "
      "the device's heating model.  Known at the start of every two-ion gate.", "time",
      kinds=("programme",))
def _heat(ctx: Ctx, i, t):
    ctx.need("programme", "heat figures")
    rec = ctx.exe.heat.get(i, {})
    k = _instant(ctx, t, "heat")
    if k not in rec:
        raise EvalError(f"the heat of {_show(i)} is recorded only where a gate starts")
    return rec[k]


# ------------------------------------------------------------------- the paper's model

@term("found(o)", "true or false", "Whether operation o finds its ions where it says they are "
      "and leaves them where the next operation expects them.", "execution",
      programme="The replay refuses a programme that does not, so this always holds once it "
                "runs.",
      schedule="Replayed event by event.")
def _found(ctx: Ctx, o):
    return not _op(o, "found").facts.get("misplaced")


@term("setup_ok()", "true or false", "Whether the execution's starting placement puts every ion "
      "in a site that exists, once.", "execution")
def _setup_ok(ctx: Ctx):
    return not ctx.exe.facts.get("malformed_start")


@term("well_formed(o)", "true or false", "Whether operation o names ions, sites and rails that "
      "exist and ends no earlier than it starts.", "execution")
def _well_formed(ctx: Ctx, o):
    return not _op(o, "well_formed").facts.get("malformed")


@term("clock(o)", "microseconds", "How long the execution's clock says operation o takes: the "
      "paper's own duration law; none where an operation is not one the model has.",
      "execution", kinds=("schedule",))
def _clock(ctx: Ctx, o):
    ctx.need("schedule", "a paper's clock")
    if not ctx.exe.facts.get("has_clock"):
        raise NotApplicable("no clock was given for this schedule")
    return _op(o, "clock").facts.get("clock")


@term("clocked(o)", "true or false", "Whether the clock makes a claim about operation o.  For a "
      "schedule this website wrote itself, every operation must be one the paper's model "
      "times; a paper's own schedule may contain operations its law does not time.",
      "execution", kinds=("schedule",))
def _clocked(ctx: Ctx, o):
    ctx.need("schedule", "a paper's clock")
    if not ctx.exe.facts.get("has_clock"):
        raise NotApplicable("no clock was given for this schedule")
    return "clock" in _op(o, "clocked").facts


@term("locks(o)", "a set of names", "The components the schedule itself says operation o holds "
      "while it runs.", "execution", domain=True, kinds=("schedule",))
def _locks(ctx: Ctx, o):
    ctx.need("schedule", "declared locks")
    return list(_op(o, "locks").facts.get("locks", ()))


# ------------------------------------------------------------------- claims (programmes)

@term("claims", "a set of claims", "Every figure the programme states about itself: its total "
      "time, cost and steps, and per-instruction annotations.", "execution", domain=True,
      kinds=("programme",))
def _claims(ctx: Ctx):
    ctx.need("programme", "a programme's claims")
    if ctx.exe.facts.get("claims_skipped"):
        raise NotApplicable(ctx.exe.facts["claims_skipped"])
    return ctx.exe.facts.get("claims", [])


@term("claimed(c)", "a number", "The value claim c states.", "execution", kinds=("programme",))
def _claimed(ctx: Ctx, c):
    return c.claimed


@term("replayed(c)", "a number", "The value the replay computes for what claim c is about.",
      "execution", kinds=("programme",))
def _replayed(ctx: Ctx, c):
    return c.replayed


# ------------------------------------------------------------------- the model's own facts

@term("models_heating()", "true or false", "Whether the device's clock tracks how hot each ion "
      "gets.", "model", kinds=("programme",))
def _models_heating(ctx: Ctx):
    return bool(ctx.exe.facts.get("models_heating"))


@term("gate_error_reads_heat()", "true or false", "Whether the gate error the clock reports "
      "depends on the ions' heat when the gate starts, rather than being a constant.", "model",
      kinds=("programme",))
def _gate_error_reads_heat(ctx: Ctx):
    return bool(ctx.exe.facts.get("models_heating"))


@term("heat_accrues_with_time()", "true or false", "Whether the clock heats every ion for "
      "elapsed time, moving or not.", "model", kinds=("programme",))
def _heat_accrues(ctx: Ctx):
    return bool(ctx.exe.facts.get("models_heating"))


@term("heat_is_upper_bound()", "true or false", "Whether the heat figures the clock reports "
      "are upper bounds: heat from successive motions is added, and the interference that can "
      "make it smaller is not modelled.", "model", kinds=("programme",))
def _heat_upper(ctx: Ctx):
    return True


@term("declares_duty_budget()", "true or false", "Whether the device declares how much heat a "
      "gate zone may absorb over time (a duty-cycle budget).", "model", kinds=("programme",))
def _declares_duty(ctx: Ctx):
    return bool(ctx.hw.numbers.get("duty_budget"))


@term("computes_circuit()", "true or false", "Whether the programme computes the input circuit: "
      "every gate of the circuit is performed once, in order, by its own ions, together in a "
      "trap that can gate, and the pulses fired compose to the circuit's operation.", "circuit",
      kinds=("programme",))
def _computes(ctx: Ctx):
    v = ctx.exe.facts.get("computes_circuit")
    if v is None:
        why = ctx.exe.facts.get("computes_reason")
        if why:
            raise EvalError(why)
        raise NotApplicable("no proof of what the programme computes was given with it")
    return bool(v)


# ============================================================================ circuit

def _circuit(ctx: Ctx):
    if ctx.exe.circuit is None:
        raise NotApplicable("no input circuit was given with this execution")
    return ctx.exe.circuit


@term("circuit_ops", "a set of circuit operations", "The operations of the input circuit that "
      "the execution is judged against.", "circuit", domain=True,
      programme="Every operation of the circuit.",
      schedule="Its two-qubit gates: the only operations a paper's schedule names ions for.")
def _circuit_ops(ctx: Ctx):
    return ctx.once("circuit_ops", lambda: [CircuitOp(c.index) for c in _circuit(ctx)])


def _performers(ctx: Ctx) -> dict[int, list[Op]]:
    def make():
        by: dict[int, list[Op]] = {}
        for o in ctx.exe.ops:
            for c in o.serves:
                by.setdefault(c, []).append(o)
        for v in by.values():
            v.sort(key=lambda o: (o.start, o.index))
        return by
    _circuit(ctx)
    return ctx.once("performers", make)


@term("performers(c)", "a set of operations", "The operations of the execution that carry out "
      "circuit operation c.", "circuit", domain=True,
      programme="Every instruction the compiler stamped with c.",
      schedule="The two-qubit gate matched to c: the k-th gate on a pair of ions is the circuit's "
               "k-th gate on that pair.")
def _performers_of(ctx: Ctx, c):
    return _performers(ctx).get(c.id, [])


@term("first(c)", "an operation", "The first operation that carries out circuit operation c.",
      "circuit")
def _first(ctx: Ctx, c):
    v = _performers(ctx).get(c.id)
    return v[0] if v else None


@term("last(c)", "an operation", "The last operation that carries out circuit operation c.",
      "circuit")
def _last(ctx: Ctx, c):
    v = _performers(ctx).get(c.id)
    if not v:
        return None
    return max(v, key=lambda o: (o.end, o.index))


@term("performed(c)", "true or false", "Whether some operation of the execution carries out "
      "circuit operation c.", "circuit")
def _performed(ctx: Ctx, c):
    return bool(_performers(ctx).get(c.id))


@term("gate_count(c)", "a number", "How many two-qubit gates of the execution carry out circuit "
      "operation c.", "circuit")
def _gate_count(ctx: Ctx, c):
    return sum(1 for o in _performers(ctx).get(c.id, []) if o.kind == "gate2")


@term("needs_gate(c)", "true or false", "Whether circuit operation c is a two-qubit gate, which "
      "the execution must perform once.", "circuit")
def _needs_gate(ctx: Ctx, c):
    return len(_circuit(ctx)[c.id].qubits) == 2 and _circuit(ctx)[c.id].name not in ("barrier",)


@term("serves_circuit(o)", "true or false", "Whether two-qubit gate o carries out some gate of "
      "the circuit.", "circuit")
def _serves_circuit(ctx: Ctx, o):
    _circuit(ctx)
    return bool(_op(o, "serves_circuit").serves)


def _qubit_seqs(ctx: Ctx) -> dict[str, list[int]]:
    def make():
        seq: dict[str, list[int]] = {}
        for c in _circuit(ctx):
            for q in c.qubits:
                seq.setdefault(q, []).append(c.index)
        return seq
    return ctx.once("qubit_seqs", make)


def _next_rel(ctx: Ctx) -> dict[int, set[int]]:
    def make():
        nxt: dict[int, set[int]] = {}
        for q, s in _qubit_seqs(ctx).items():
            for a, b in zip(s, s[1:]):
                nxt.setdefault(a, set()).add(b)
        return nxt
    return ctx.once("next_rel", make)


def _run_rel(ctx: Ctx) -> dict[int, set[int]]:
    """c1 -> every c2 in the next run on a shared qubit.  A run is a stretch of consecutive
    two-qubit gates in which the qubit plays the same role (all control, or all target)."""
    def make():
        circ = _circuit(ctx)
        rel: dict[int, set[int]] = {}
        for q, s in _qubit_seqs(ctx).items():
            runs: list[list[int]] = []
            role_prev = None
            for ci in s:
                c = circ[ci]
                role = None
                if c.roles and q in c.qubits:
                    role = c.roles[c.qubits.index(q)]
                if role is not None and runs and role == role_prev:
                    runs[-1].append(ci)
                else:
                    runs.append([ci])
                role_prev = role
            for r1, r2 in zip(runs, runs[1:]):
                for a in r1:
                    rel.setdefault(a, set()).update(r2)
        return rel
    return ctx.once("run_rel", make)


@term("next_on_qubit(c1, c2)", "true or false", "Whether c2 is the operation that comes right "
      "after c1 on one of their qubits, in the circuit as written.", "circuit")
def _next_on_qubit(ctx: Ctx, a, b):
    return b.id in _next_rel(ctx).get(a.id, ())


@term("next_run(c1, c2)", "true or false", "Whether c2 belongs to the run of gates that follows "
      "c1's run on a qubit they share.  A run is a stretch of consecutive CNOTs in which that "
      "qubit plays the same role -- always the control, or always the target -- and such gates "
      "commute with each other.", "circuit")
def _next_run(ctx: Ctx, a, b):
    return b.id in _run_rel(ctx).get(a.id, ())


#: Relations a quantifier can enumerate directly instead of testing every pair.
RELATIONS: dict[str, Callable[[Ctx, Any], set]] = {
    "next_on_qubit": lambda ctx, a: {CircuitOp(i) for i in _next_rel(ctx).get(a.id, ())},
    "next_run": lambda ctx, a: {CircuitOp(i) for i in _run_rel(ctx).get(a.id, ())},
}
