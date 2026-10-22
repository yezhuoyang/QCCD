"""The replay engine: execute a TSIR program against an architecture and a cost model.

This is the piece everything else is measured against.  Its contract is narrow on
purpose:

* it owns **no** cost knowledge -- every number comes from the `CostModel` handed to it,
  which is why the identical program can be replayed under the deck's model (M1) and
  under the corrected one (M2) with every difference attributable to the model;
* it owns **no** schedule knowledge -- instructions execute in list order, each
  atomically, and `t0`/`t1` are computed, so a claimed annotation is always a *claim*
  that R9 gets to falsify;
* it carries per-ion `n-bar` **by component** -- shuttle, junction, split/merge, gate,
  anomalous -- because a single opaque total cannot be checked against a published
  budget, and PLAN §0.4's budget is stated as three named components.

Aggregation inside one cycle: `cost` sums over participants, `depth` and duration take
the maximum, and heating accrues per ion.  That is the deck's own rule ("one rigid hop
takes the maximum primitive edge depth used by any moving ion in that hop") and it is
also what a broadcast-wired machine physically does.
"""

from __future__ import annotations

import inspect
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..arch import Architecture
from ..cost.models import QUANTA_COMPONENTS, Charge, CostModel
from ..ir.tsir import TSIR, Instruction, Participant, iter_operands, iter_pairs
from .rules import CycleView, ResolvedMove, RuleReport, check_cycle, conveyor_nodes, rails_to_loop

__all__ = ["ReplayResult", "CycleRecord", "replay", "ReplayError"]


class ReplayError(RuntimeError):
    """The program is not executable at all (unknown ion, no such segment, ...)."""


@dataclass(slots=True)
class CycleRecord:
    """A compact per-cycle trace entry.  ~40 bytes of state, not a full snapshot."""

    instr_id: int
    type: str
    cls: str | None
    t0: float
    t1: float
    cost: float
    depth: int
    n_participants: int
    batch: object = None


@dataclass
class ReplayResult:
    arch_name: str
    program_name: str
    model: Mapping
    total_cost: float = 0.0
    total_steps: int = 0
    total_us: float = 0.0
    quanta_components: dict[str, float] = field(default_factory=dict)
    per_ion_quanta: dict[str, dict[str, float]] = field(default_factory=dict)
    junction_transits: Counter = field(default_factory=Counter)
    junction_transits_by_node: Counter = field(default_factory=Counter)
    moves_per_ion: Counter = field(default_factory=Counter)
    gates_per_ion: Counter = field(default_factory=Counter)
    n_gates: int = 0
    #: single-qubit gates, counted apart from `n_gate_pairs` because they are priced
    #: from a different primitive (`1q_gate`, not `ms_gate`)
    n_1q_gates: int = 0
    n_gate_pairs: int = 0
    n_measure: int = 0
    n_reset: int = 0
    gate_error_sum: float = 0.0
    gate_quanta_sum: float = 0.0
    max_gate_quanta_seen: float = 0.0
    #: G1: how many ions shared the trap at each two-qubit gate -- the same quantity R13
    #: caps at 15.  Histogram rather than a mean, because the mean of a distribution that
    #: is 99 % twos hides the tail that the chain-length term is about.
    chain_len_at_gate: Counter = field(default_factory=Counter)
    us_by_type: dict[str, float] = field(default_factory=dict)
    us_by_class: dict[str, float] = field(default_factory=dict)
    cost_by_class: dict[str, float] = field(default_factory=dict)
    steps_by_class: dict[str, int] = field(default_factory=dict)
    hops_by_class: dict[str, int] = field(default_factory=dict)
    cycles: list[CycleRecord] = field(default_factory=list)
    per_batch: dict[object, dict[str, float]] = field(default_factory=dict)
    rules: RuleReport = field(default_factory=RuleReport)
    peak_quanta: float = 0.0
    peak_quanta_ion: str | None = None
    final_positions: dict[str, str] = field(default_factory=dict)
    #: per-ion n-bar as the program ends -- what a cool zeroed stays zeroed here,
    #: unlike `per_ion_quanta`, which is the lifetime deposit and never comes down
    final_quanta: dict[str, float] = field(default_factory=dict)

    # ---------------------------------------------------------------- views

    def total_quanta(self) -> float:
        return sum(self.quanta_components.values())

    def mean_quanta_per_ion(self, ions: Sequence[str] | None = None) -> dict[str, float]:
        names = list(ions) if ions is not None else list(self.per_ion_quanta)
        if not names:
            return {}
        out: dict[str, float] = {c: 0.0 for c in QUANTA_COMPONENTS}
        for ion in names:
            for c, val in self.per_ion_quanta.get(ion, {}).items():
                out[c] = out.get(c, 0.0) + val
        return {c: v / len(names) for c, v in out.items()}

    def metrics(self) -> dict:
        return {
            "total_cost": self.total_cost,
            "total_steps": self.total_steps,
            "runtime_us": self.total_us,
            "total_quanta": self.total_quanta(),
            "peak_quanta": self.peak_quanta,
            "n_cycles": len(self.cycles),
            "n_gate_pairs": self.n_gate_pairs,
            "gate_error_sum": self.gate_error_sum,
            "max_gate_quanta_seen": self.max_gate_quanta_seen,
            "chain_len_at_gate": {str(k): v for k, v in sorted(self.chain_len_at_gate.items())},
        }


def _resolve_participants(
    arch: Architecture, instr: Instruction, pos: Mapping[str, str]
) -> list[list[Participant]]:
    """Return the sub-cycles of an instruction, each a list of participants.

    A `loop_shift` template with `|delta| > 1` decomposes into `|delta|` unit sub-cycles;
    each is a real machine cycle with its own depth, duration and rule check.
    """
    if instr.template:
        kind = instr.template.get("kind")
        if kind != "loop_shift":
            raise ReplayError(f"unknown movement template kind {kind!r}")
        loop_id = str(instr.template["loop"])
        delta = int(instr.template["delta"])
        if loop_id not in arch.device.loops:
            raise ReplayError(f"template references unknown loop {loop_id!r}")
        if delta == 0:
            # a zero-hop rotation is a real (if degenerate) cycle: record it with no
            # participants so its cost/step claims are still exposed to R9 rather than
            # being waved through by an instruction that produced no trace at all
            return [[]]
        step = 1 if delta >= 0 else -1
        sub: list[list[Participant]] = []
        live = dict(pos)
        for _ in range(abs(delta)):
            shift = arch.device.shift_map(loop_id, step)
            here: dict[str, str] = {}
            for ion, node in live.items():
                if node in shift:
                    here[ion] = shift[node]
            sub.append(
                [
                    Participant(ion=ion, src=live[ion], dst=dst)
                    for ion, dst in sorted(here.items())
                ]
            )
            live.update(here)
        return sub
    return [list(instr.participants)]


def _segments_for(arch: Architecture, p: Participant) -> list:
    if p.via:
        return [arch.device.segments[s] for s in p.via]
    try:
        return [arch.device.segment_between(p.src, p.dst)]
    except KeyError as exc:
        raise ReplayError(f"ion {p.ion}: {exc}") from None


#: the order the derived primitives are charged in, whatever produced them
_ENTAIL_ORDER = ("split", "merge")


def move_entails(declared: Sequence[str], sub: Sequence[Participant], occ: Mapping[str, int],
                 routes: Sequence[Sequence], conveyor,
                 rails: Mapping[str, int]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """What each move of one cycle splits and merges -- from what it DOES, not what its class
    is called (#16, #17).

    The class's `entails` was the only source, so a move the class did not declare paid for
    nothing: the router's `shuttle` took an ion out of a chain and into another and charged
    neither, and a device that deleted its `dock`/`undock` classes docked through `shuttle`
    and dropped the split, the merge and the cooling they force.  Three sources now, and a
    move pays each primitive at most once:

    - the class, as before: a declared dock still costs what it always has;
    - the path: a hop across a spur off a CONVEYOR -- a rail on no named path from a node of a
      closed loop (`conveyor_nodes`) to a dead end, a node with exactly one rail to it
      (`rails`; a chord is a translation here as in R22, #18) -- lifts an ion out of the
      conveyor's moving potential into a separate trap, which is a dock -- split and merge --
      whatever the class is called (`Architecture.entails` explains why that is a property of
      the motion, even for a lone ion).  Off an OPEN path it is not: a road is not a moving
      potential, and a lone ion stepping from it into a stub (a gadget's port) is transport,
      priced by the chains like any other move (qccd-12's channel leaf paid 320 us for it);
    - the chains: an ion leaves a site where ions stay behind or go elsewhere (a split), or
      arrives where ions stay or others arrive from elsewhere (a merge).  Ions that leave
      one site for one destination together are one chain moving whole, which is why a
      rotation -- every slot moving with the potential into a slot vacated in the same
      cycle -- still costs neither.

    `occ` is the occupancy BEFORE the cycle; `routes[k]` is the route of `sub[k]`.  Returns,
    aligned with `sub`, `(priced, judged)`: what the move is charged, and what R14 judges --
    the CLASS's declaration alone, exactly as before #16.  R14 asks whether the ion was at the
    end of its chain, and the replay does not track chain order: its test is "a chain of more
    than two", sound only where the declared docks go into traps of capacity <= 2.  A split
    read off the path or the chains is charged, but judged by that test it refuses legal
    moves: a gadget door holds a bundle of nine and feeds the ring one ion at a time from the
    end facing it.  Judging every split honestly needs chain order (#22).
    """
    leaving = Counter(p.src for p in sub)
    arriving = Counter(p.dst for p in sub)
    together = Counter((p.src, p.dst) for p in sub)
    out: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def ordered(need: set) -> tuple[str, ...]:
        return tuple(w for w in declared if w in need) + tuple(
            w for w in _ENTAIL_ORDER if w in need and w not in declared)

    judged = tuple(declared)
    for p, route in zip(sub, routes):
        need = set(declared)
        if p.src != p.dst:
            n = together[(p.src, p.dst)]
            if occ.get(p.src, 0) > n:
                need.add("split")
            if occ.get(p.dst, 0) - leaving.get(p.dst, 0) > 0 or arriving[p.dst] > n:
                need.add("merge")
            for s in route:
                a, b = s.ends
                if s.loop is None and (a in conveyor) != (b in conveyor) \
                        and rails.get(b if a in conveyor else a, 0) == 1:
                    need.update(_ENTAIL_ORDER)
                    break
        out.append((ordered(need), judged))
    return out


def _accepts_chain(fn) -> bool:
    """Does this cost-model method take G1's `n_chain` argument?

    `False` for anything written against the pre-G1 signature, and `False` when the
    signature cannot be read at all (a C extension, a callable object) -- because the
    safe default is the argument the method definitely has, not the one it might.
    A `**kwargs` catch-all counts as accepting it.
    """
    if fn is None:
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):          # pragma: no cover - unreadable signature
        return False
    return "n_chain" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD or p.kind is inspect.Parameter.VAR_POSITIONAL
        for p in params.values()
    )


def conveyed_paths(prog: TSIR, arch: Architecture) -> frozenset[str]:
    """The named loops and open paths this programme moves as a conveyor (R23's exemption).

    A conveyor carries every ion on it through every slot, so a slot where three rails meet
    is not a place a router chose to park an ion.  Two spellings of a conveyor step: the
    rotation template (`loop_shift`), and a transport cycle that moves EVERY ion standing on
    one named path along it, with a shift class the device declares for a path (its
    `orbit` names a loop or "rail", not "any" or "spurs") -- the conveyor rails of a
    transversal-CNOT station, say, whose sites each have a rung to a gate trap and so meet
    three rails.  One ion shuttled along a loop past others is not a conveyor step, and
    neither is an odd-even sort, which moves pairs.
    """
    dev = arch.device
    out: set[str] = set()
    pos: dict[str, str] = {}
    for ins in prog.instructions:
        if ins.type == "init":
            pos.update(ins.placement)
            continue
        if ins.type != "simd":
            continue
        tpl = ins.template or {}
        if tpl.get("kind") == "loop_shift":
            loop = str(tpl.get("loop"))
            out.add(loop)
            delta = int(tpl.get("delta", 0) or 0)
            if loop in dev.loops and delta:
                step = 1 if delta > 0 else -1
                for _ in range(abs(delta)):
                    shift = dev.shift_map(loop, step)
                    pos = {i: shift.get(n, n) for i, n in pos.items()}
            continue
        parts = ins.participants
        if parts and ins.cls:
            try:
                spec = arch.simd_class(ins.cls)
            except KeyError:
                spec = {}
            if spec.get("type") == "shift" and spec.get("orbit") not in (None, "any", "spurs"):
                loops: set = set()
                for p in parts:
                    try:
                        loops.update(seg.loop for seg in _segments_for(arch, p))
                    except (KeyError, ReplayError):
                        loops.add(None)
                if len(loops) == 1 and None not in loops:
                    loop = str(next(iter(loops)))
                    if loop in dev.loops:
                        on = set(dev.loops[loop].nodes)
                        riders = {i for i, n in pos.items() if n in on}
                        if riders <= {p.ion for p in parts}:
                            out.add(loop)
        for p in parts:
            pos[p.ion] = p.dst
    return frozenset(out)


def replay(
    prog: TSIR,
    arch: Architecture,
    model: CostModel,
    *,
    check_rules: bool = True,
    only_rules: Sequence[str] | None = None,
    keep_cycles: bool = True,
    probe: Callable[[Instruction, Mapping[str, float]], None] | None = None,
    rule_config: Mapping[str, object] | None = None,
    on_cycle: Callable[["CycleView"], None] | None = None,
) -> ReplayResult:
    """Execute `prog` and return everything the objective and the rules need.

    `probe(instruction, current_quanta)` is called immediately before each
    instruction, which is how the cooling pass reads the n-bar a cool is about to
    remove without the replay having to store a trace for every cycle.

    `on_cycle(view)` is called with the SAME `CycleView` the rule pass judges, before
    it is judged.  That is the whole point: anything derived from it -- the control
    record the page shows, say -- cannot disagree with the verifier, because it is
    looking at the same object.  The view is built for the rules anyway, so collecting
    is free and no second replay is needed.
    """
    res = ReplayResult(
        arch_name=arch.name, program_name=prog.name, model=model.describe()
    )
    res.quanta_components = {c: 0.0 for c in QUANTA_COMPONENTS}

    # G1 widened `gate` and `gate_error` with an `n_chain` argument.  A model written
    # before that -- and any subclass overriding the old three-argument `gate` -- must
    # keep working unchanged, so ask each method whether it accepts the argument rather
    # than assuming it does.  Passing it blind is what broke `test_review_regressions`'s
    # `HotGate`, and a user's own cost model would have broken the same way with no test
    # to catch it.  Probed once per replay, not once per gate.
    takes_chain = {name: _accepts_chain(getattr(model, name, None))
                   for name in ("gate", "gate_error")}

    pos: dict[str, str] = {}
    current: dict[str, float] = {}
    lifetime: dict[str, dict[str, float]] = {}
    occ: Counter[str] = Counter()
    conveyor = conveyor_nodes(arch.device)
    # how many rails join each node off a conveyor to one: 1 is a dead end, a spur (`move_entails`)
    rails = {n: rails_to_loop(arch.device, conveyor, n) for n in arch.device.nodes if n not in conveyor}
    t = 0.0
    anomalous_per_us = model.anomalous_per_us(arch)
    rule_config = dict(rule_config or {})
    # R23 exempts the slots of a loop the programme rotates, which is a fact about the whole
    # programme and has to be known before its first cycle is judged.
    rule_config.setdefault("rotated_loops", conveyed_paths(prog, arch))

    def _bump(ion: str, component: str, value: float) -> None:
        if value == 0.0:
            return
        slot = lifetime.setdefault(ion, {})
        slot[component] = slot.get(component, 0.0) + value
        now = current.get(ion, 0.0) + value
        current[ion] = now
        if now > res.peak_quanta:
            res.peak_quanta = now
            res.peak_quanta_ion = ion
        res.quanta_components[component] = res.quanta_components.get(component, 0.0) + value

    for instr in prog.instructions:
        t0 = t
        if probe is not None:
            probe(instr, current)

        # ---------------------------------------------------------- init
        if instr.type == "init":
            for ion, node in instr.placement.items():
                if node not in arch.device.nodes:
                    raise ReplayError(f"init places {ion} at unknown node {node!r}")
                # a later `init` that re-places an ion must vacate the node it was on,
                # or the occupancy counters leak a phantom ion that no later cycle can
                # ever clear and R1/R2/R13 start reading corrupted occupancies
                previous = pos.get(ion)
                if previous is not None:
                    occ[previous] -= 1
                    if occ[previous] <= 0:
                        del occ[previous]
                pos[ion] = node
                occ[node] += 1
                current[ion] = float(instr.quanta.get(ion, 0.0))
                lifetime.setdefault(ion, {c: 0.0 for c in QUANTA_COMPONENTS})
            res.cycles.append(
                CycleRecord(instr.id, "init", None, t0, t0, 0.0, 0, len(instr.placement),
                            instr.meta.get("batch"))
            )
            continue

        if instr.type == "barrier":
            res.cycles.append(
                CycleRecord(instr.id, "barrier", None, t0, t0, 0.0, 0, 0,
                            instr.meta.get("batch"))
            )
            continue

        if instr.type == "decode":
            # THE DECODER WORKS ALONGSIDE THE IONS.  A decode sends measured outcomes to the
            # classical processor and moves no ion, heats none, occupies no site and holds
            # no rail, so on the machine's clock it takes no time: the next instruction
            # starts when the ions are ready, exactly as if the line were not there.  Its
            # classical latency is real and is drawn and budgeted by the QEC-cycle layer
            # (`qccd.analysis.feedback`); charging it here would move every board's
            # runtime for a cost the ions never pay.  No rule sees it, because there is
            # nothing on the device for a rule to judge.
            res.cycles.append(
                CycleRecord(instr.id, "decode", None, t0, t0, 0.0, 0, 0,
                            instr.meta.get("batch"))
            )
            continue

        # ---------------------------------------------------------- transport
        if instr.type == "simd":
            declared = arch.entails(instr.cls) if instr.cls else ()
            for sub in _resolve_participants(arch, instr, pos):
                pos_before = dict(pos)
                occ_before = Counter(occ)
                moves: list[ResolvedMove] = []
                transits: Counter[str] = Counter()
                cycle_cost = 0.0
                cycle_depth = 0
                cycle_us = 0.0
                per_ion_charge: dict[str, Charge] = {}
                for p in sub:
                    if pos.get(p.ion) != p.src:
                        raise ReplayError(
                            f"instruction {instr.id}: ion {p.ion} declared at {p.src} "
                            f"but is at {pos.get(p.ion)}"
                        )
                routes = [_segments_for(arch, p) for p in sub]
                implied = move_entails(declared, sub, occ, routes, conveyor, rails)
                for p, segs, (entails, judged) in zip(sub, routes, implied):
                    charge = Charge()
                    node = p.src
                    # `entails` (split at the source, merge at the destination; see
                    # `move_entails` for where they come from) belongs to the MOVE, not to
                    # each segment of the route: one dock is one split plus one merge
                    # however long the spur is.  Charge it once, on the first segment.
                    for i, seg in enumerate(segs):
                        nxt = seg.other(node)
                        charge = charge.then(
                            model.move(
                                arch, seg, node, nxt,
                                entails=entails if i == 0 else (),
                            )
                        )
                        moves.append(
                            ResolvedMove(p.ion, node, nxt, seg, judged)
                        )
                        if arch.device.degree(nxt) >= 3:
                            res.junction_transits[p.ion] += 1
                            res.junction_transits_by_node[nxt] += 1
                        node = nxt
                    if node != p.dst:
                        raise ReplayError(
                            f"instruction {instr.id}: ion {p.ion} `via` ends at {node}, "
                            f"not the declared {p.dst}"
                        )
                    # every node the path entered except the last is passed THROUGH,
                    # and a site with no room to spare there is a roadblock (R1)
                    for m in moves[-len(segs):][:-1]:
                        transits[m.dst] += 1
                    per_ion_charge[p.ion] = charge
                    cycle_cost += charge.cost
                    cycle_depth = max(cycle_depth, charge.depth)
                    cycle_us = max(cycle_us, charge.us)
                    res.moves_per_ion[p.ion] += 1

                # keyed by ion: a program that lists one ion twice in a cycle is
                # illegal (R8 reports it), but it must not be able to corrupt the
                # occupancy counters on the way to being reported
                final = {p.ion: p.dst for p in sub}
                for ion, dst in final.items():
                    here = pos[ion]
                    occ[here] -= 1
                    if occ[here] <= 0:
                        del occ[here]
                    pos[ion] = dst
                for dst in final.values():
                    occ[dst] += 1

                for ion, charge in per_ion_charge.items():
                    for comp, val in charge.quanta.items():
                        _bump(ion, comp, val)
                if anomalous_per_us and cycle_us:
                    for ion in pos:
                        _bump(ion, "anomalous", anomalous_per_us * cycle_us)

                res.total_cost += cycle_cost
                res.total_steps += cycle_depth
                res.total_us += cycle_us
                key = instr.cls or "simd"
                res.cost_by_class[key] = res.cost_by_class.get(key, 0.0) + cycle_cost
                res.steps_by_class[key] = res.steps_by_class.get(key, 0) + cycle_depth
                res.hops_by_class[key] = res.hops_by_class.get(key, 0) + 1
                res.us_by_class[key] = res.us_by_class.get(key, 0.0) + cycle_us
                res.us_by_type[instr.type] = res.us_by_type.get(instr.type, 0.0) + cycle_us
                t1 = t0 + cycle_us
                if check_rules or on_cycle is not None:
                    view = CycleView(
                        arch=arch,
                        instr=instr,
                        moves=tuple(moves),
                        pos_before=pos_before,
                        pos_after=dict(pos),
                        occ_before=dict(occ_before),
                        occ_after=dict(occ),
                        quanta=dict(current),
                        duration_us=cycle_us,
                        config=rule_config,
                        transits=dict(transits),
                    )
                    if on_cycle is not None:
                        on_cycle(view)
                    if check_rules:
                        res.rules.extend(check_cycle(view, only_rules))
                if keep_cycles:
                    res.cycles.append(
                        CycleRecord(instr.id, "simd", instr.cls, t0, t1, cycle_cost,
                                    cycle_depth, len(sub), instr.meta.get("batch"))
                    )
                _accumulate_batch(res, instr, cycle_cost, cycle_depth, cycle_us, 0)
                t0 = t1
                t = t1
            continue

        # ---------------------------------------------------------- non-transport
        pos_before = dict(pos)
        occ_before = Counter(occ)
        charge = Charge()
        n_pairs = 0
        if instr.type == "gate":
            pairs = list(iter_pairs(instr))
            n_pairs = len(pairs)
            # A gate instruction is either two-qubit (pairs) or single-qubit (one ion).
            # They are priced from different primitives, so they are counted separately
            # rather than folded together -- see `CostModel.gate_1q`.
            singles = [o[0] for o in iter_operands(instr) if len(o) == 1]
            if singles:
                charge = model.gate_1q(arch, instr.gate or "R", len(singles))
                res.n_1q_gates += len(singles)
                for a in singles:
                    res.gates_per_ion[a] += 1
            else:
                # G1: how many ions share the trap, which is what R13 caps at 15 and what
                # the chain-length term of the gate model reads.  A gate instruction can
                # hold several pairs in several traps; its DURATION is set by the longest
                # chain involved, because the cycle ends when its slowest gate does.
                chains = [occ_before.get(pos_before[a], 0) for a, b in pairs]
                longest = max(chains) if chains else None
                charge = (model.gate(arch, instr.gate or "MS", n_pairs, longest)
                          if takes_chain["gate"]
                          else model.gate(arch, instr.gate or "MS", n_pairs))
            res.n_gates += 1
            res.n_gate_pairs += n_pairs
            for a, b in pairs:
                res.gates_per_ion[a] += 1
                res.gates_per_ion[b] += 1
                # R16: gate error is a function of the accumulated quanta of the
                # participating ions at gate time, not a constant.  The hotter of the
                # two sets the pair's error, since both share the motional mode.  This
                # is the same n-bar R7 tests, by construction -- see `quanta_at_start`.
                nbar = max(current.get(a, 0.0), current.get(b, 0.0))
                # R6b puts both ions of a pair in ONE gate zone, so either one names the
                # chain.  Read it the way R13 does -- occupancy of the site before the
                # cycle -- so the error model and the rule that caps it cannot disagree.
                n_chain = occ_before.get(pos_before[a], 0)
                res.gate_error_sum += (
                    model.gate_error(arch, nbar, n_chain) if takes_chain["gate_error"]
                    else model.gate_error(arch, nbar)
                )
                res.gate_quanta_sum += nbar
                res.chain_len_at_gate[n_chain] += 1
                if nbar > res.max_gate_quanta_seen:
                    res.max_gate_quanta_seen = nbar
        elif instr.type in ("cool", "measure", "reset"):
            # named ions must exist, exactly as a transport participant must be where it
            # says it is -- otherwise a one-character typo surfaces as a bare KeyError
            # out of a rule instead of as an unexecutable program
            unknown = [i for i in instr.ions if i not in pos]
            if unknown:
                raise ReplayError(
                    f"instruction {instr.id}: {instr.type} names unplaced ion(s) "
                    f"{unknown[:5]}"
                )
            charge = {
                "cool": model.cool,
                "measure": model.measure,
                "reset": model.reset,
            }[instr.type](arch)
            if instr.type == "measure":
                res.n_measure += len(instr.ions)
            elif instr.type == "reset":
                res.n_reset += len(instr.ions)
        else:
            raise ReplayError(f"instruction {instr.id}: unknown type {instr.type!r}")

        cycle_us = charge.us
        # the n-bar a rule sees for this instruction is the one at its START: R7 asks
        # what the ions are at *gate time*, and R16 evaluates the same gate's error, so
        # both read this snapshot rather than one before and one after the gate's own
        # anomalous heating
        quanta_at_start = dict(current)
        if charge.quanta:
            # `Charge.quanta` is keyed by QUANTA_COMPONENTS, never by ion.  A gate,
            # cool, measure or reset heats the ions it acts on (or every ion, for a
            # broadcast operation), so the component value is deposited on each of them.
            targets = list(instr.ions) or (
                [i for pair in iter_pairs(instr) for i in pair]
                if instr.type == "gate"
                else list(pos)
            )
            for component, val in charge.quanta.items():
                for ion in targets:
                    _bump(ion, component, val)
        if anomalous_per_us and cycle_us:
            for ion in pos:
                _bump(ion, "anomalous", anomalous_per_us * cycle_us)

        if instr.type == "cool":
            cooled = instr.ions if instr.ions else (tuple(pos) if instr.broadcast else ())
            if not cooled:
                raise ReplayError(
                    f"instruction {instr.id}: cool names no ions and is not broadcast"
                )
            for ion in cooled:
                current[ion] = 0.0

        res.total_cost += charge.cost
        res.total_steps += charge.depth
        res.total_us += cycle_us
        res.us_by_type[instr.type] = res.us_by_type.get(instr.type, 0.0) + cycle_us
        res.us_by_class[instr.type] = res.us_by_class.get(instr.type, 0.0) + cycle_us
        t1 = t0 + cycle_us
        if check_rules or on_cycle is not None:
            view = CycleView(
                arch=arch,
                instr=instr,
                moves=(),
                pos_before=pos_before,
                pos_after=dict(pos),
                occ_before=dict(occ_before),
                occ_after=dict(occ),
                quanta=quanta_at_start,
                duration_us=cycle_us,
                config=rule_config,
            )
            if on_cycle is not None:
                on_cycle(view)
            if check_rules:
                res.rules.extend(check_cycle(view, only_rules))
        if keep_cycles:
            res.cycles.append(
                CycleRecord(instr.id, instr.type, instr.cls, t0, t1, charge.cost,
                            charge.depth, n_pairs, instr.meta.get("batch"))
            )
        _accumulate_batch(res, instr, charge.cost, charge.depth, cycle_us, n_pairs)
        t = t1

    res.per_ion_quanta = lifetime
    res.final_positions = dict(pos)
    res.final_quanta = dict(current)
    return res


def _accumulate_batch(
    res: ReplayResult, instr: Instruction, cost: float, depth: int, us: float, pairs: int
) -> None:
    batch = instr.meta.get("batch")
    if batch is None:
        return
    d = res.per_batch.setdefault(
        batch, {"cost": 0.0, "steps": 0, "us": 0.0, "contacts": 0}
    )
    d["cost"] += cost
    d["steps"] += depth
    d["us"] += us
    d["contacts"] += pairs
