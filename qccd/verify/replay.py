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

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..arch import Architecture
from ..cost.models import QUANTA_COMPONENTS, Charge, CostModel
from ..ir.tsir import TSIR, Instruction, Participant, iter_pairs
from .rules import CycleView, ResolvedMove, RuleReport, check_cycle

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
    n_gate_pairs: int = 0
    n_measure: int = 0
    n_reset: int = 0
    gate_error_sum: float = 0.0
    gate_quanta_sum: float = 0.0
    max_gate_quanta_seen: float = 0.0
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

    pos: dict[str, str] = {}
    current: dict[str, float] = {}
    lifetime: dict[str, dict[str, float]] = {}
    occ: Counter[str] = Counter()
    t = 0.0
    anomalous_per_us = model.anomalous_per_us(arch)
    rule_config = dict(rule_config or {})

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

        # ---------------------------------------------------------- transport
        if instr.type == "simd":
            entails = arch.entails(instr.cls) if instr.cls else ()
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
                    segs = _segments_for(arch, p)
                    charge = Charge()
                    node = p.src
                    # `entails` (split at the source, merge at the destination) belongs
                    # to the movement class, not to each segment of the route: one dock
                    # is one split plus one merge however long the spur is.  Charge it
                    # once, on the first segment.
                    for i, seg in enumerate(segs):
                        nxt = seg.other(node)
                        charge = charge.then(
                            model.move(
                                arch, seg, node, nxt,
                                entails=entails if i == 0 else (),
                            )
                        )
                        moves.append(
                            ResolvedMove(p.ion, node, nxt, seg, tuple(entails))
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
            charge = model.gate(arch, instr.gate or "MS", n_pairs)
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
                res.gate_error_sum += model.gate_error(arch, nbar)
                res.gate_quanta_sum += nbar
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
