"""What an evaluation talks about: one programme, replayed once on its device under a clock.

The replay is the verifier's own (`qccd.verify.replay`) -- the operational semantics of the
hardware language -- read through its per-cycle views, so every number here is the number
the rules and the grader see.  The run is plain data:

    steps        one per instruction of the programme, in order.  Instructions run one after
                 another; `init`, `barrier` and `decode` take no time.
    prepare      the preparation before the first step: every ion starts in |0>.
    readout      the experiment's own data readout after the last step, when its programme
                 stops before reading the data (an "appended" readout).
    operations   per step: one `move` per ion a transport step carries; one `gate2` per pair
                 and one `gate1` per ion of a gate step; one `measure` / `reset` per ion; one
                 `cool` per cooling step; one `prepare` for the preparation.
    instants     start(k) and end(k) of every step; the end of one step is the start of the
                 next, except that a cooling takes effect at the boundary (see `nbar`).

The state at an instant: where every ion is, how many ions every node holds, every ion's
motional n-bar, and the clock.  n-bar at end(k) is after the step's own heating and BEFORE a
cooling resets it; n-bar at start(k+1) is after the reset.  So the hottest an ion ever gets
is a maximum over end instants, and the n-bar a gate meets is its value at start(g).
"""

from __future__ import annotations

from collections.abc import Mapping as _AbcMapping
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = ["Step", "Op", "Instant", "Run", "Heat", "build_run", "OPERATED"]

#: The operation kinds that act on an ion's qubit (a gate pulse, a frame change, a readout,
#: a preparation), as opposed to carrying it or cooling it.
OPERATED = ("gate1", "gate2", "measure", "reset")


class Step:
    __slots__ = ("index", "instr", "kind", "cls", "t0", "t1", "dt", "ops", "pos0", "pos1", "occ0",
                 "occ1", "nbar0", "nbar1", "moves", "cost", "depth", "cycles", "in_programme")
    #: `depth`: the machine steps the instruction takes -- per cycle, the longest route (one per
    #: rail crossed, one per split or merge it is charged), summed over its cycles; 1 for any
    #: other instruction that takes time, 0 for init and barrier

    def __init__(self, index: int, instr, kind: str):
        self.index = index
        self.instr = instr
        self.kind = kind
        self.cls = getattr(instr, "cls", None)
        self.t0 = self.t1 = self.dt = 0.0
        self.ops: list[Op] = []
        self.pos0: dict[str, str] = {}
        self.pos1: dict[str, str] = {}
        self.occ0: dict[str, int] = {}
        self.occ1: dict[str, int] = {}
        self.nbar0: dict[str, float] = {}
        self.nbar1: dict[str, float] = {}
        self.moves: list = []            # ResolvedMove, every rail crossed this step
        self.cost = 0.0
        self.depth = 0
        self.cycles = 0
        self.in_programme = True

    @property
    def duration(self) -> float:
        return self.dt

    def label(self) -> str:
        if self.kind == "prepare":
            return "the preparation"
        if self.kind == "readout":
            return "the appended readout"
        iid = getattr(self.instr, "id", self.index)
        return f"#{iid} {self.kind}" + (f" {self.cls}" if self.cls else "")

    def __repr__(self) -> str:
        return f"step {self.label()}"

    def __lt__(self, other: "Step") -> bool:
        return self.index < other.index


class Op:
    __slots__ = ("index", "step", "kind", "ions", "site", "gate", "params", "basis", "path", "entails")

    def __init__(self, index: int, step: Step, kind: str, ions: Sequence[str], site=None,
                 gate=None, params=(), basis="Z", path=()):
        self.index = index
        self.step = step
        self.kind = kind
        self.ions = tuple(ions)
        self.site = site
        self.gate = gate
        self.params = tuple(params)
        self.basis = basis
        self.path = tuple(path)
        self.entails: tuple[str, ...] = ()

    def label(self) -> str:
        what = self.gate or self.kind
        at = f" at {self.site}" if self.site else ""
        return f"{what} {','.join(self.ions)}{at} ({self.step.label()})"

    def __repr__(self) -> str:
        return self.label()

    def __lt__(self, other: "Op") -> bool:
        return self.index < other.index


class Instant:
    __slots__ = ("step", "edge")

    def __init__(self, step: Step, edge: str):
        self.step = step
        self.edge = edge                 # "start" | "end"

    @property
    def time(self) -> float:
        return self.step.t0 if self.edge == "start" else self.step.t1

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Instant) and other.step is self.step and other.edge == self.edge

    def __hash__(self) -> int:
        return hash((id(self.step), self.edge))

    def __lt__(self, other: "Instant") -> bool:
        return (self.step.index, self.edge == "end") < (other.step.index, other.edge == "end")

    def label(self) -> str:
        return f"the {self.edge} of {self.step.label()}"

    def __repr__(self) -> str:
        return self.label()


@dataclass
class Run:
    """One programme on one device under one clock, replayed."""

    arch: Any
    prog: Any
    clock: str
    cost_model: Any
    result: Any                          # the replay's ReplayResult
    ions: list[str]
    steps: list[Step]                    # the programme's steps
    prepare: Step
    readout: Step | None
    ops: list[Op]                        # every operation, the preparation and readout included
    anomalous_per_us: float = 0.0
    experiment: Any = None
    qubit_map: dict[int, str] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def all_steps(self) -> list[Step]:
        return [self.prepare] + self.steps + ([self.readout] if self.readout else [])

    def final(self) -> Instant:
        return Instant(self.steps[-1] if self.steps else self.prepare, "end")


def _gate_ops(ins) -> list[tuple[str, tuple[str, ...], tuple]]:
    """(kind, ions, params) for every operand of a gate instruction, read as the stim
    translation reads it (`qccd.qec.extract`): MS and the two-ion abstract gates by pair,
    R, VZ and the one-ion abstract gates by ion."""
    from ..qec.native import ABSTRACT_2Q, NotClifford, canonical_abstract

    g = str(ins.gate)
    two = g == "MS"
    if not two and g not in ("R", "VZ"):
        try:
            two = canonical_abstract(g) in ABSTRACT_2Q
        except NotClifford:
            two = bool(ins.pairs) or (len(ins.ions) == 2 and ins.arity != 1)
    ps = list(ins.params or ())
    out = []
    if two:
        pairs = [tuple(p) for p in ins.pairs] if ins.pairs else (
            [(ins.ions[0], ins.ions[1])] if len(ins.ions) == 2 else [])
        for k, pr in enumerate(pairs):
            p = ps[k] if len(ps) == len(pairs) else (ps[0] if len(ps) == 1 else ())
            out.append(("gate2", pr, tuple(p)))
    else:
        for k, ion in enumerate(ins.ions):
            p = ps[k] if len(ps) == len(ins.ions) else (ps[0] if len(ps) == 1 else ())
            out.append(("gate1", (ion,), tuple(p)))
    return out


class Heat(_AbcMapping):
    """Every ion's n-bar at one instant: one double per ion of the run, in the order the
    programme places them, behind a mapping (`heat.get(ion, 0.0)`).  A dict per step was the
    largest thing a run held (a 72,000-step programme kept 7 million floats as objects); the
    values are the replay's own, copied when they are read."""

    __slots__ = ("_ix", "_a")

    def __init__(self, ix: dict, values):
        from array import array
        self._ix = ix
        self._a = values if isinstance(values, array) else array("d", values)

    def __getitem__(self, ion):
        return self._a[self._ix[ion]]

    def get(self, ion, default=None):
        i = self._ix.get(ion)
        return default if i is None else self._a[i]

    def __iter__(self):
        return iter(self._ix)

    def __len__(self) -> int:
        return len(self._ix)

    def __eq__(self, other) -> bool:
        if isinstance(other, Heat) and other._ix is self._ix:
            return self._a == other._a
        return isinstance(other, _AbcMapping) and dict(self.items()) == dict(other.items())

    __hash__ = None


def _move_tables(arch):
    """The replay's tables for `move_entails`: the conveyor nodes and each spur's rails."""
    from ..verify.rules import conveyor_nodes, rails_to_loop

    dev = arch.device
    conveyor = conveyor_nodes(dev)
    return conveyor, {n: rails_to_loop(dev, conveyor, n) for n in dev.nodes if n not in conveyor}


def _view_entails(arch, v, tables) -> dict:
    """ion -> the splits and merges the clock charges that ion's move in cycle `v`,
    recomputed with the replay's own rule (`qccd.verify.replay.move_entails`): the class's
    declaration, a spur off a conveyor, and the chains it leaves and joins."""
    from types import SimpleNamespace

    from ..verify.replay import move_entails

    if v.instr.type != "simd" or not v.moves:
        return {}
    by_ion: dict[str, list] = {}
    for m in v.moves:
        by_ion.setdefault(m.ion, []).append(m)
    sub = [SimpleNamespace(ion=i, src=ms[0].src, dst=ms[-1].dst) for i, ms in by_ion.items()]
    routes = [[m.seg for m in ms] for ms in by_ion.values()]
    declared = arch.entails(v.instr.cls) if v.instr.cls else ()
    conveyor, rails = tables
    return {p.ion: tuple(charged)
            for p, (charged, _) in zip(sub, move_entails(declared, sub, v.occ_before, routes, conveyor, rails))}


def _cycle_depth(moves, charged: dict) -> int:
    """One transport cycle's depth: its longest route, a rail and each split or merge one deep."""
    routes: dict[int, int] = {}
    last: dict[str, tuple[int, str]] = {}
    for m in moves:
        prev = last.get(m.ion)
        if prev is not None and prev[1] == m.src:
            routes[prev[0]] += 1
            last[m.ion] = (prev[0], m.dst)
        else:
            k = len(routes)
            routes[k] = 1 + len(charged.get(m.ion, ()))
            last[m.ion] = (k, m.dst)
    return max(routes.values(), default=0)


class _Cycles:
    """What one instruction's cycles leave the run: its first and last state, each cycle's
    duration, every rail crossed, how deep it went, and the splits and merges each ion was
    charged.  Made as the replay goes, so no cycle's view outlives its cycle."""

    __slots__ = ("pos0", "occ0", "pos1", "occ1", "durs", "moves", "cycles", "depth", "entails")

    def __init__(self, pos0, occ0):
        self.pos0, self.occ0 = pos0, occ0
        self.pos1, self.occ1 = pos0, occ0
        self.durs: list[float] = []
        self.moves: list = []
        self.cycles = 0
        self.depth = 0
        self.entails: dict[str, list] = {}


def build_run(prog, arch, clock: str = "qccdsim_jones", *, experiment=None, qasm: str | None = None,
              qubit_map=None, cert: Mapping | None = None, cost_model=None, rules: bool = False) -> Run:
    """Replay `prog` on `arch` under the clock (an operating-point table, at its fastest
    points) and build the run.  With an experiment, the run also carries its appended
    readout and the qubit -> ion map the circuit is read through."""
    from ..cost.models import corrected_model
    from ..qec.extract import load_arch, load_program, resolve_qubit_map
    from ..verify.replay import replay

    prog = load_program(prog)
    arch = load_arch(arch)
    model = cost_model or corrected_model(clock)
    # THE RUN IS COLLECTED AS THE REPLAY GOES.  Each cycle's view is read the moment it is
    # made -- its moves, its duration, the splits and merges it charges, how deep it goes --
    # and dropped; a state equal to the last one kept IS that one (the views hand over a copy
    # of their own each cycle).  Keeping every view until the end held every cycle's four
    # state dicts at once: 2.7 GB on a 72,000-instruction programme, under a 1 GB grader.
    ion_order: list[str] = []
    ion_ix: dict[str, int] = {}
    for ins in prog.instructions:
        if ins.type == "init":
            for ion in ins.placement:
                if ion not in ion_ix:
                    ion_ix[ion] = len(ion_order)
                    ion_order.append(ion)
    tables = _move_tables(arch)
    cyc: dict[int, _Cycles] = {}
    starts: dict[int, Heat] = {}
    kept = {"pos": {}, "occ": {}}

    def keep(d: dict, kind: str) -> dict:
        prev = kept[kind]
        if d is prev or d == prev:
            return prev
        kept[kind] = d
        return d

    def on_cycle(v) -> None:
        c = cyc.get(v.instr.id)
        if c is None:
            c = cyc[v.instr.id] = _Cycles(keep(v.pos_before, "pos"), keep(v.occ_before, "occ"))
        c.pos1, c.occ1 = keep(v.pos_after, "pos"), keep(v.occ_after, "occ")
        c.durs.append(float(v.duration_us))
        c.moves.extend(v.moves)
        c.cycles += 1
        if v.instr.type == "simd":
            charged = _view_entails(arch, v, tables)
            for ion, ch in charged.items():
                c.entails.setdefault(ion, []).extend(ch)
            c.depth += _cycle_depth(v.moves, charged)
        else:
            c.depth += 1

    def probe(instr, current) -> None:
        if instr.id not in starts:
            starts[instr.id] = Heat(ion_ix, [float(current.get(i, 0.0)) for i in ion_order])

    verification = None
    if rules:
        # the rule verdicts too, from this same replay (what `qccd.verify.verify` reports)
        from ..verify import verify
        verification = verify(prog, arch, model, on_cycle=on_cycle, probe=probe)
        res = verification.result
    else:
        res = replay(prog, arch, model, check_rules=False, keep_cycles=False, probe=probe,
                     on_cycle=on_cycle)
    gamma = float(model.anomalous_per_us(arch))

    steps: list[Step] = []
    ops: list[Op] = []
    ions: list[str] = []
    prep = Step(-1, None, "prepare")
    prep.in_programme = False
    # A STATE IS NEVER COPIED, ONLY SHARED.  The views and the probe hand over dicts of their
    # own, nothing writes to a step's state after this loop, and a state equal to the one
    # before it is the same object -- so a 20,000-step programme keeps one dict per change of
    # state rather than six per step (half a gigabyte, under the grader's memory limit).
    pos: dict[str, str] = {}
    occ: dict[str, int] = {}
    last_nbar: Mapping = {}

    def same(d: dict, prev: dict) -> dict:
        return prev if d == prev else d

    t = 0.0
    for k, ins in enumerate(prog.instructions):
        st = Step(k, ins, ins.type)
        c = cyc.pop(ins.id, None)
        st.nbar0 = last_nbar = same(starts.pop(ins.id, {}), last_nbar)
        st.t0 = t
        if ins.type == "init":
            pos, occ = dict(pos), dict(occ)      # the steps before keep theirs
            for ion, node in ins.placement.items():
                if ion not in ions:
                    ions.append(ion)
                prev = pos.get(ion)
                if prev is not None:
                    occ[prev] -= 1
                    if occ[prev] <= 0:
                        del occ[prev]
                pos[ion] = node
                occ[node] = occ.get(node, 0) + 1
        if c is not None:
            before_p, before_o = (steps[-1].pos1, steps[-1].occ1) if steps else ({}, {})
            st.pos0 = same(c.pos0, before_p)
            st.occ0 = same(c.occ0, before_o)
            st.pos1 = same(c.pos1, st.pos0)
            st.occ1 = same(c.occ1, st.occ0)
            pos, occ = st.pos1, st.occ1
            # the clock adds cycle by cycle, as the replay's total does; the step's own
            # duration is the sum of its cycles, as the noise reads it
            t1, dt = t, 0.0
            for d in c.durs:
                t1 += d
                dt += d
            st.t1, st.dt = t1, dt
            st.moves = c.moves
            st.cycles = c.cycles
            st.depth = c.depth
        else:
            if ins.type == "init":
                st.pos0, st.occ0 = (steps[-1].pos1, steps[-1].occ1) if steps else ({}, {})
            else:
                st.pos0, st.occ0 = pos, occ
            st.pos1, st.occ1 = pos, occ
            st.t1 = t
        t = st.t1
        # -- the operations of the step
        if ins.type == "simd":
            by_ion: dict[str, list] = {}
            for m in st.moves:
                by_ion.setdefault(m.ion, []).append(m)
            for ion, ms in by_ion.items():
                path = [ms[0].src] + [m.dst for m in ms]
                ops.append(Op(len(ops), st, "move", (ion,), None, ins.cls, (), "Z", path))
                ops[-1].entails = tuple(c.entails.get(ion, ())) if c is not None else ()
                st.ops.append(ops[-1])
        elif ins.type == "gate":
            for kind, who, params in _gate_ops(ins):
                ops.append(Op(len(ops), st, kind, who, st.pos0.get(who[0]), str(ins.gate), params))
                st.ops.append(ops[-1])
        elif ins.type in ("measure", "reset"):
            for ion in ins.ions:
                ops.append(Op(len(ops), st, ins.type, (ion,), st.pos0.get(ion)))
                st.ops.append(ops[-1])
        elif ins.type == "cool":
            cooled = tuple(ins.ions) if ins.ions else (tuple(st.pos0) if ins.broadcast else ())
            ops.append(Op(len(ops), st, "cool", cooled))
            st.ops.append(ops[-1])
        steps.append(st)

    # n-bar at the end of a step: the start of the next one, except across a cooling, whose
    # reset takes effect at the boundary -- its end is the heat the step itself added
    for k, st in enumerate(steps):
        nxt = steps[k + 1].nbar0 if k + 1 < len(steps) else dict(res.final_quanta)
        if st.kind == "cool":
            st.nbar1 = Heat(ion_ix, [st.nbar0.get(i, 0.0) + gamma * st.duration if i in st.pos0 else 0.0
                                     for i in ion_order])
        else:
            st.nbar1 = nxt

    prep.pos0 = prep.pos1 = steps[0].pos1 if steps and steps[0].kind == "init" else {}
    prep.occ0 = prep.occ1 = steps[0].occ1 if steps and steps[0].kind == "init" else {}
    prep.nbar0 = prep.nbar1 = {i: 0.0 for i in ions}
    prep_op = Op(len(ops), prep, "prepare", tuple(ions))
    prep.ops.append(prep_op)
    ops.append(prep_op)

    run = Run(arch=arch, prog=prog, clock=clock, cost_model=model, result=res, ions=ions,
              steps=steps, prepare=prep, readout=None, ops=ops, anomalous_per_us=gamma,
              experiment=experiment)
    if verification is not None:
        run.facts["verification"] = verification

    if experiment is not None:
        from ..qec.qasm import parse_qasm
        text = qasm if qasm is not None else experiment.qasm
        src = parse_qasm(text)
        qmap, origin = resolve_qubit_map(prog, qubit_map, cert, src.n_qubits)
        run.qubit_map = {q: ion for q, ion in qmap.items() if q < src.n_qubits}
        run.facts["qubit_map_origin"] = origin
        run.facts["source"] = src
        if experiment.readout == "appended":
            last = steps[-1] if steps else prep
            ro = Step(len(steps), None, "readout")
            ro.in_programme = False
            ro.t0 = last.t1
            ro.dt = float(arch.primitives.scalar("measure").get("us", 0.0))
            ro.t1 = last.t1 + ro.dt
            ro.pos0 = ro.pos1 = last.pos1
            ro.occ0 = ro.occ1 = last.occ1
            ro.nbar0 = ro.nbar1 = last.nbar1
            for q, b, basis in experiment.readout_plan():
                ion = run.qubit_map.get(q)
                if ion is None:
                    continue
                o = Op(len(ops), ro, "measure", (ion,), ro.pos0.get(ion), None, (), basis)
                ro.ops.append(o)
                ops.append(o)
            run.readout = ro
    return run
