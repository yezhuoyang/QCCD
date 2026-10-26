"""The words an evaluation may use, what each one means, and how it is read off a run.

Every word has a definition in words -- the one the Evaluation page prints -- and one
implementation, over a `Run` (`model.py`).  Words that the rule language also has
(`sites`, `capacity(x)`, `occupancy(x, t)`, ...) mean the same thing here; the instants are
the evaluation's own (`start(k)` and `end(k)` of every step), because a number like the
hottest an ion gets needs the state at the end of a step, before a cooling resets it.

Adding a word is one `@word(...)` here; adding a metric or a noise channel needs no code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from .model import OPERATED, Instant, Op, Run, Step

__all__ = ["Word", "WORDS", "GROUPS", "WordError", "Absent", "Hop", "word"]


class WordError(Exception):
    """A word was given something it does not take (an ion where a step was needed...)."""


class Absent(Exception):
    """The run has no such thing: no experiment was given, no sample was drawn."""


@dataclass
class Word:
    name: str
    signature: str
    returns: str
    meaning: str
    group: str
    fn: Callable
    domain: bool = False


WORDS: dict[str, Word] = {}

#: The groups the page lists the words under, in order.
GROUPS = (("device", "The device"), ("wiring", "The device's wiring"),
          ("execution", "The programme"), ("state", "Instants and the state"),
          ("law", "The device's laws"), ("experiment", "The experiment"),
          ("sample", "The sample"), ("math", "Arithmetic"))


def word(signature: str, returns: str, meaning: str, group: str, *, domain: bool = False,
         name: str | None = None):
    def deco(fn: Callable) -> Callable:
        nm = name or signature.split("(")[0]
        WORDS[nm] = Word(nm, signature, returns, meaning, group, fn, domain)
        return fn
    return deco


class Hop:
    """One rail crossed by one ion in one step."""

    __slots__ = ("index", "op", "src", "dst", "seg")

    def __init__(self, index: int, op: Op, src: str, dst: str, seg):
        self.index, self.op, self.src, self.dst, self.seg = index, op, src, dst, seg

    def label(self) -> str:
        return f"{self.op.ions[0]} {self.src}→{self.dst} ({self.op.step.label()})"

    def __repr__(self) -> str:
        return self.label()

    def __lt__(self, other: "Hop") -> bool:
        return self.index < other.index


class Ctx:
    """What a word can see: the run, the evaluation's sample (if one was drawn), caches."""

    def __init__(self, run: Run, sample: Any = None, checks: dict | None = None):
        self.run = run
        self.arch = run.arch
        self.dev = run.arch.device
        self.sample = sample
        self.checks = checks or {}
        self.cache: dict[Any, Any] = {}

    def once(self, key, make):
        v = self.cache.get(key)
        if v is None:
            v = make()
            self.cache[key] = v
        return v


def _op(x, fn: str) -> Op:
    if isinstance(x, Op):
        return x
    raise WordError(f"{fn} needs an operation, got {_show(x)}")


def _step(x, fn: str) -> Step:
    if isinstance(x, Step):
        return x
    if isinstance(x, Op):
        return x.step
    raise WordError(f"{fn} needs a step, got {_show(x)}")


def _inst(x, fn: str) -> Instant:
    if isinstance(x, Instant):
        return x
    raise WordError(f"{fn} needs an instant, got {_show(x)}")


def _show(x) -> str:
    return x.label() if hasattr(x, "label") else repr(x)


# ============================================================================ the device

@word("sites", "a set of sites", "Every place an ion can stand: the traps, the docks, the slots of "
      "a loop (the nodes the device declares as sites).", "device", domain=True)
def _sites(c: Ctx):
    return c.once("sites", lambda: [n for n, nd in c.dev.nodes.items() if nd.kind == "site"])


@word("junctions", "a set of nodes", "The nodes where three or more rails meet, whatever the device "
      "calls them.", "device", domain=True)
def _junctions(c: Ctx):
    return c.once("junctions", lambda: list(c.dev.junction_nodes))


@word("nodes", "a set of nodes", "Every node of the device: its sites and its junctions.", "device",
      domain=True)
def _nodes(c: Ctx):
    return list(c.dev.nodes)


@word("rails", "a set of rails", "The rails (segments) along which ions are carried between nodes.",
      "device", domain=True)
def _rails(c: Ctx):
    return list(c.dev.segments)


@word("capacity(x)", "a number", "How many ions the node or rail x may hold at once, as the device "
      "declares it.", "device")
def _capacity(c: Ctx, x):
    if x in c.dev.nodes:
        return int(c.dev.nodes[x].capacity)
    if x in c.dev.segments:
        return int(c.dev.segments[x].capacity)
    raise WordError(f"capacity: {x!r} is neither a node nor a rail")


@word("degree(n)", "a number", "How many rails meet at node n.", "device")
def _degree(c: Ctx, n):
    return c.dev.degree(n)


@word("zone(s)", "a name", "The zone type of site s.", "device")
def _zone(c: Ctx, s):
    return c.dev.nodes[s].zone_type


@word("can(s, a)", "true or false", "Whether site s is equipped for ability a: \"gate\", \"spam\" "
      "(preparation and measurement) or \"cool\".", "device")
def _can(c: Ctx, s, a):
    zt = c.dev.nodes[s].zone_type
    if zt is None:
        return False
    try:
        return bool(c.arch.zone(zt).get(a, False))
    except KeyError:
        return False


# ============================================================================ wiring

@word("wiring(key, d)", "a number or a name", "What the device's wiring block declares for key "
      "(electrodes_per_trap, electrodes_per_junction, compensation_electrodes_per_trap, shim_per_dac, "
      "dacs_dynamic, scheme), or d when it declares nothing.", "wiring")
def _wiring(c: Ctx, key, default=None):
    w = dict(c.arch.control.get("wiring", {}) or {})
    v = w.get(key)
    return default if v is None else v


@word("budget(key, d)", "a number", "The device budget's value for key (max_dacs, max_junctions, "
      "junction_electrode_multiplier), or d when it has none.", "wiring")
def _budget(c: Ctx, key, default=None):
    b = c.arch.budget if isinstance(c.arch.budget, dict) else dict(c.arch.budget or {})
    v = b.get(key)
    return default if v is None else v


@word("channel_map()", "true or false", "Whether the device declares a channel map: which control "
      "channel drives each electrode role of each site.", "wiring")
def _channel_map(c: Ctx):
    return bool(c.arch.control_plane.declared)


@word("grouping()", "a name", "How the declared map groups its channels: \"broadcast\" (one channel "
      "per electrode role for every site), \"row\", \"column\", \"row_column\", \"direct\" (every site its own) "
      "or \"explicit\"; none without a map.", "wiring")
def _grouping(c: Ctx):
    plane = c.arch.control_plane
    return str(plane.grouping) if plane.declared else None


@word("shared_channels()", "a number", "The channels of the declared map that drive more than one "
      "site, counted from the map: one per role and bank for a broadcast plane, one per row or column "
      "for a row or column plane, one per site for a direct one, one per entry for an explicit list.",
      "wiring")
def _shared(c: Ctx):
    return int(c.arch.control_plane.n_shared_channels)


@word("compensation_channels()", "a number", "The compensation (shim) DACs of the declared map: "
      "the sites times their shim electrodes, divided by the demultiplexer, rounded up.", "wiring")
def _comp(c: Ctx):
    return int(c.arch.control_plane.n_compensation_channels)


@word("map_electrodes()", "a number", "The electrodes of the declared map: every site's role and "
      "shim electrodes and every junction's.", "wiring")
def _map_electrodes(c: Ctx):
    return int(c.arch.control_plane.n_electrodes)


@word("map_switches()", "a number", "The switches of the declared map: two per electrode when each "
      "site has its own switch, none otherwise.", "wiring")
def _map_switches(c: Ctx):
    return int(c.arch.control_plane.n_switches)


@word("driven(s)", "a number", "How many channels of the declared map drive site s (0 without a map).",
      "wiring")
def _driven(c: Ctx, s):
    plane = c.arch.control_plane
    return len(plane.channels_of(s)) if plane.declared else 0


# ============================================================================ the programme

def _hops(c: Ctx) -> list[Hop]:
    def make():
        out: list[Hop] = []
        for st in c.run.steps:
            by_ion: dict[str, Op] = {o.ions[0]: o for o in st.ops if o.kind == "move"}
            for m in st.moves:
                out.append(Hop(len(out), by_ion[m.ion], m.src, m.dst, m.seg))
        return out
    return c.once("hops", make)


@word("steps", "a set of steps", "The steps of the programme: one instruction each, run one after "
      "another.  The preparation and the appended readout are not steps of the programme.",
      "execution", domain=True)
def _steps(c: Ctx):
    return c.run.steps


@word("operations", "a set of operations", "Every operation of the programme, of any kind.",
      "execution", domain=True)
def _operations(c: Ctx):
    return [o for o in c.run.ops if o.step.in_programme]


def _kind_domain(*kinds):
    def fn(c: Ctx):
        return c.once(("kinds",) + kinds, lambda: [o for o in c.run.ops if o.kind in kinds])
    return fn


for _nm, _sig, _kinds, _txt in (
        ("two_qubit_gates", "two_qubit_gates", ("gate2",), "Every gate on two ions together: one per pair of a gate step."),
        ("single_qubit_gates", "single_qubit_gates", ("gate1",), "Every gate on one ion: one per ion of a gate step, the frame change VZ included."),
        ("measurements", "measurements", ("measure",), "Every measurement of an ion: the programme's, and the experiment's appended readout."),
        ("resets", "resets", ("reset",), "Every reset of an ion to |0⟩."),
        ("coolings", "coolings", ("cool",), "Every cooling operation."),
        ("moves", "moves", ("move",), "Every transport of one ion in one step, from where it stood to where it stops."),
        ("preparations", "preparations", ("prepare",), "The preparation of every ion in |0⟩ before the first step: one operation.")):
    word(_sig, "a set of operations", _txt, "execution", domain=True, name=_nm)(_kind_domain(*_kinds))


@word("qubit_operations", "a set of operations", "Every operation that acts on an ion's qubit: the "
      "gates (a frame change included), the measurements (the appended readout's too) and the resets.",
      "execution", domain=True)
def _qubit_ops(c: Ctx):
    return c.once("qubit_ops", lambda: [o for o in c.run.ops if o.kind in OPERATED])


@word("hops", "a set of hops", "Every rail crossed by an ion: one move over three rails is three hops.",
      "execution", domain=True)
def _hops_w(c: Ctx):
    return _hops(c)


@word("readout", "a set of steps", "The experiment's appended readout, as one step after the last "
      "(empty when the programme reads the data itself).", "execution", domain=True)
def _readout(c: Ctx):
    return [c.run.readout] if c.run.readout is not None else []


@word("ions", "a set of ions", "Every ion the programme places.", "execution", domain=True)
def _ions(c: Ctx):
    return c.run.ions


@word("kind(x)", "a name", "What operation x is (\"gate2\", \"gate1\", \"measure\", \"reset\", "
      "\"cool\", \"move\", \"prepare\"), or what instruction step x is (\"init\", \"simd\", \"gate\", "
      "\"measure\", \"reset\", \"cool\", \"barrier\", and \"readout\" for the appended readout).",
      "execution")
def _kind(c: Ctx, x):
    if isinstance(x, (Op, Step)):
        return x.kind
    raise WordError(f"kind needs an operation or a step, got {_show(x)}")


@word("ions(x)", "a set of ions", "The ions operation x acts on or carries, in order (control first "
      "for a pair); for a step, the ions of its operations.", "execution", name="ions_of")
def _ions_of(c: Ctx, x):
    if isinstance(x, Op):
        return x.ions
    if isinstance(x, Step):
        return tuple(dict.fromkeys(i for o in x.ops for i in o.ions))
    raise WordError(f"ions needs an operation or a step, got {_show(x)}")


@word("operated(k)", "a set of ions", "The ions whose qubit step k acts on: named by its gates (a "
      "frame change included), measurements or resets.  Carrying an ion or cooling it does not "
      "operate on its qubit.", "execution")
def _operated(c: Ctx, k):
    st = _step(k, "operated")
    return c.once(("operated", st.index, st.kind),
                  lambda: frozenset(i for o in st.ops if o.kind in OPERATED for i in o.ions))


@word("gate(o)", "a name", "The gate operation o fires: \"MS\", \"R\", \"VZ\", or an abstract "
      "Clifford (\"CX\", \"H\", ...).", "execution")
def _gate(c: Ctx, o):
    return _op(o, "gate").gate


@word("basis(m)", "a name", "The basis measurement m reads in: \"Z\" for the programme's, the "
      "experiment's own basis for its appended readout.", "execution")
def _basis(c: Ctx, m):
    return _op(m, "basis").basis


@word("site(o)", "a site", "Where operation o happens: for a gate, measurement or reset, the site "
      "its (first) ion stands in when it starts; none for transport and cooling.", "execution")
def _site(c: Ctx, o):
    return _op(o, "site").site


@word("step(o)", "a step", "The step operation o belongs to.", "execution")
def _step_w(c: Ctx, o):
    return _op(o, "step").step


@word("cls(k)", "a name", "The movement class transport step k drives (none for other steps).",
      "execution")
def _cls(c: Ctx, k):
    return _step(k, "cls").cls


@word("from(h)", "a node", "Where hop h picks its ion up.", "execution", name="from")
def _from(c: Ctx, h):
    if isinstance(h, Hop):
        return h.src
    o = _op(h, "from")
    return o.path[0] if o.path else None


@word("to(h)", "a node", "Where hop h (or move h) leaves its ion.", "execution", name="to")
def _to(c: Ctx, h):
    if isinstance(h, Hop):
        return h.dst
    o = _op(h, "to")
    return o.path[-1] if o.path else None


@word("path(o)", "a list of nodes", "The nodes move o carries its ion through, first to last.",
      "execution")
def _path(c: Ctx, o):
    return list(_op(o, "path").path)


@word("crossed(o)", "a list of junctions", "The junctions move o carries its ion into, on the way or to "
      "stop, once for every time it enters one.", "execution")
def _crossed(c: Ctx, o):
    o = _op(o, "crossed")
    return [n for n in o.path[1:] if c.dev.degree(n) >= 3]


@word("move(h)", "an operation", "The move hop h is part of.", "execution", name="move_of")
def _move_of(c: Ctx, h):
    if isinstance(h, Hop):
        return h.op
    raise WordError(f"move needs a hop, got {_show(h)}")


@word("entails(o)", "a list of names", "The splits and merges move o is charged: a split where it "
      "leaves a site other ions stay in or leave for elsewhere, a merge where it arrives where ions "
      "stay or others arrive from elsewhere, and both for a dock -- a class that declares them, or a "
      "hop onto a spur off a closed loop.  Once per move in each cycle, however many rails it "
      "crosses; a rotation, every slot moving into a slot vacated in the same cycle, has neither.",
      "execution")
def _entails(c: Ctx, o):
    return _op(o, "entails").entails


@word("cycles(k)", "a number", "How many machine cycles step k drives: |delta| for a rotation by "
      "delta slots, 1 for any other step that takes time, 0 for init and barrier.", "execution")
def _cycles(c: Ctx, k):
    return _step(k, "cycles").cycles


@word("movers(k)", "a number", "How many ions step k carries.", "execution")
def _movers(c: Ctx, k):
    st = _step(k, "movers")
    return len({m.ion for m in st.moves})


@word("rotation(k)", "true or false", "Whether step k turns a loop as a whole: its movement class is "
      "one of the device's rotations (named rotate...).", "execution")
def _rotation(c: Ctx, k):
    st = _step(k, "rotation")
    return bool(st.kind == "simd" and st.cls and str(st.cls).startswith("rotate"))


@word("loop_riders(k)", "a number", "How many ions a transport step k counts as moving, as the "
      "leaderboard counts it: for a rotation of a closed loop, every ion the programme placed on a "
      "closed loop at its start; for any other transport step, its participants.", "execution")
def _riders(c: Ctx, k):
    st = _step(k, "loop_riders")
    ins = st.instr
    if ins is None or ins.type != "simd":
        return 0
    if ins.cls and ins.cls.startswith("rotate"):
        def riders():
            loop_nodes = set()
            for lp in c.dev.loops.values():
                if lp.closed:
                    loop_nodes.update(lp.nodes)
            init = next((i for i in c.run.prog.instructions if i.type == "init"), None)
            return sum(1 for s in (init.placement.values() if init else ()) if s in loop_nodes)
        return c.once("riders", riders)
    return len(ins.participants)


# ============================================================================ instants and the state

@word("instants", "a set of instants", "The start and the end of every step, the preparation's and "
      "the readout's included.", "state", domain=True)
def _instants(c: Ctx):
    return c.once("instants", lambda: [Instant(s, e) for s in c.run.all_steps for e in ("start", "end")])


@word("start(x)", "an instant", "The instant step x (or operation x's step) begins.", "state")
def _start(c: Ctx, x):
    return Instant(_step(x, "start"), "start")


@word("end(x)", "an instant", "The instant step x (or operation x's step) is over.", "state")
def _end(c: Ctx, x):
    return Instant(_step(x, "end"), "end")


@word("final()", "an instant", "The end of the programme's last step.", "state")
def _final(c: Ctx):
    return c.run.final()


@word("time(t)", "microseconds", "The clock at instant t: the durations of every step before it, "
      "added up.", "state")
def _time(c: Ctx, t):
    return _inst(t, "time").time


@word("duration(k)", "microseconds", "How long step k (or operation k's step) takes on the clock: "
      "the sum of its cycles, each as long as its slowest participant.", "state")
def _duration(c: Ctx, k):
    return _step(k, "duration").duration


@word("position(i, t)", "a site", "The node where ion i stands at instant t.", "state")
def _position(c: Ctx, i, t):
    t = _inst(t, "position")
    return (t.step.pos0 if t.edge == "start" else t.step.pos1).get(i)


@word("occupancy(x, t)", "a number", "How many ions stand in node x at instant t.", "state")
def _occupancy(c: Ctx, x, t):
    t = _inst(t, "occupancy")
    return int((t.step.occ0 if t.edge == "start" else t.step.occ1).get(x, 0))


@word("nbar(i, t)", "quanta", "Ion i's motional energy n̄ at instant t, as the replay accrues it: "
      "every rail, junction, split and merge it is carried through, and every microsecond for "
      "every ion, since it was last cooled.  At the end of a cooling step it is the heat before "
      "the reset; at the start of the next step, after it.", "state")
def _nbar(c: Ctx, i, t):
    t = _inst(t, "nbar")
    return float((t.step.nbar0 if t.edge == "start" else t.step.nbar1).get(i, 0.0))


@word("waited(i, o)", "microseconds", "How long ion i has waited, when operation o starts, since a "
      "step last operated on it (or since the start): the durations of the steps between, which "
      "carried it, cooled it or left it alone.", "state")
def _waited(c: Ctx, i, o):
    o = _op(o, "waited")
    def table():
        last: dict[str, float] = {}
        out: dict[tuple, float] = {}
        acc: dict[str, float] = {ion: 0.0 for ion in c.run.ions}
        for st in c.run.all_steps:
            for x in st.ops:
                if x.kind in OPERATED:
                    for ion in x.ions:
                        out[(ion, st.index, st.kind)] = acc.get(ion, 0.0)
            ops_ = {ion for x in st.ops if x.kind in OPERATED for ion in x.ions} if st.kind != "readout" else set()
            for ion in acc:
                if ion in ops_:
                    acc[ion] = 0.0
                elif st.in_programme and st.duration > 0:
                    acc[ion] += st.duration
            for ion in ops_:
                acc[ion] = 0.0
        return out
    tab = c.once("waited", table)
    return tab.get((i, o.step.index, o.step.kind), 0.0)


# ============================================================================ the device's laws

def _prim(c: Ctx, p: str) -> dict:
    try:
        return dict(c.arch.primitives.scalar(p))
    except KeyError:
        raise WordError(f"the device declares no primitive {p!r}") from None


@word("primitive(p, f)", "a number", "Field f of the device's primitive p, as its physics declares "
      "it: primitive(\"measure\", \"fidelity\"), primitive(\"ms_gate\", \"us\"), ...", "law")
def _primitive(c: Ctx, p, f):
    v = _prim(c, p).get(f)
    if v is None:
        raise WordError(f"the primitive {p!r} declares no {f!r}")
    return float(v) if isinstance(v, (int, float)) else v


@word("slope(p)", "per quantum", "The k of primitive p's error_vs_quanta law \"linear:k\": how much its "
      "error grows per quantum of n̄.", "law")
def _slope(c: Ctx, p):
    s = str(_prim(c, p).get("error_vs_quanta", "linear:0"))
    return float(s.split(":", 1)[1]) if s.startswith("linear:") else 0.0


@word("chain_reference(p)", "ions", "The N_ref of primitive p's error_vs_chain law \"murali:N_ref\": the "
      "chain length its error is calibrated at; none when it declares no chain law.", "law")
def _chain_ref(c: Ctx, p):
    s = _prim(c, p).get("error_vs_chain")
    if not s or not str(s).startswith("murali:"):
        return None
    return float(str(s).split(":", 1)[1])


@word("coherence_time()", "seconds", "The qubit's coherence time T₂ as the device's species block "
      "declares it (T_coh_s); none when it declares none.", "law")
def _t2(c: Ctx):
    v = c.arch.species.get("T_coh_s")
    return float(v) if v else None


@word("heating_rate()", "quanta per microsecond", "The anomalous heating every ion accrues per "
      "microsecond, moving or not (the physics' quanta per millisecond, over 1000).", "law")
def _heating(c: Ctx):
    return c.run.anomalous_per_us


# ============================================================================ the experiment

def _exp(c: Ctx):
    if c.run.experiment is None:
        raise Absent("no experiment was given with the programme")
    return c.run.experiment


@word("rounds()", "a number", "How many rounds of syndrome extraction the experiment runs.", "experiment")
def _rounds(c: Ctx):
    return int(_exp(c).rounds)


@word("logical_qubits()", "a number", "How many logical qubits the experiment protects: its observables, "
      "halved when it measures both bases (one Z and one X observable per logical qubit).", "experiment")
def _logical(c: Ctx):
    return int(_exp(c).logical_qubits)


@word("observables()", "a number", "How many logical observables the experiment declares.", "experiment")
def _observables(c: Ctx):
    return len(_exp(c).observables)


@word("distance()", "a number", "The code distance the experiment's circuit is built to.", "experiment")
def _distance(c: Ctx):
    return int(_exp(c).distance)


# ============================================================================ the sample

def _sample(c: Ctx):
    if c.sample is None:
        raise Absent("no sample was drawn: the logical error rate is estimated by the sampler")
    return c.sample


@word("shots()", "a number", "How many times the sampler ran the noisy circuit.", "sample")
def _shots(c: Ctx):
    return int(_sample(c).shots)


@word("failures()", "a number", "In how many of those shots the decoder's correction left some "
      "logical observable wrong.", "sample")
def _failures(c: Ctx):
    return int(_sample(c).errors)


@word("noiseless_ok()", "true or false", "Whether the circuit, with its noise taken out, gives every "
      "detector and observable a fixed value, and that value is 0.", "sample")
def _noiseless(c: Ctx):
    v = c.checks.get("noiseless")
    if v is None:
        raise Absent("the noiseless check was not run")
    return bool(v)


@word("rules_hold()", "true or false", "Whether every rule of the evaluation's rule set holds on the "
      "programme (the Rules page's verdict).", "sample")
def _rules_hold(c: Ctx):
    v = c.checks.get("rules")
    if v is None:
        raise Absent("the rules were not checked")
    return bool(v)


# ============================================================================ arithmetic

def _num(fn):
    def go(c: Ctx, *xs):
        if any(x is None for x in xs):
            raise WordError(f"{fn.__name__} of none")
        return fn(*xs)
    return go


for _nm, _sig, _f, _txt in (
        ("ln", "ln(x)", math.log, "The natural logarithm."),
        ("exp", "exp(x)", math.exp, "e to the power x."),
        ("sqrt", "sqrt(x)", math.sqrt, "The square root."),
        ("sin", "sin(x)", math.sin, "The sine, of an angle in radians."),
        ("pow", "pow(x, y)", math.pow, "x to the power y."),
        ("ceil", "ceil(x)", lambda x: int(math.ceil(x)), "The least whole number not below x."),
        ("abs", "abs(x)", abs, "The size of x, without its sign.")):
    word(_sig, "a number", _txt, "math", name=_nm)(_num(_f))


@word("min(x, y, ...)", "a number", "The smallest of its arguments.", "math")
def _min(c: Ctx, *xs):
    return min(xs)


@word("max(x, y, ...)", "a number", "The largest of its arguments.", "math")
def _max(c: Ctx, *xs):
    return max(xs)


@word("pi", "a number", "π.", "math")
def _pi(c: Ctx):
    return math.pi


@word("size(A)", "a number", "How many things set A holds.", "math")
def _size(c: Ctx, a):
    return len(a)
