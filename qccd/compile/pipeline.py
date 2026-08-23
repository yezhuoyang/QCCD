"""The compilation pipeline.  PLAN §7.

    code / QASM -> [ place | order | route | simd | opoint | cooling | schedule ] -> TSIR

Each pass is a named function with the same shape, so the pipeline is a list you can
reorder, replace or instrument, and every pass reports what it did. That matters more than
any individual pass being clever: PLAN §7.1 records Cyclone's finding that a *mismatched*
policy makes any architecture look bad, so being able to swap one pass and hold the rest
fixed is the difference between a comparison and an anecdote.

The router shipped here is the deck's own strategy -- fixed ancilla per check, six waves,
greedy minimum rotation -- so that the compiler's output can be checked against the
shipped 2672-hop schedule. A compiler with no oracle produces unfalsifiable numbers, which
is the same trap PLAN §10 puts M1 and M3 in place to avoid.

What is deliberately still crude: placement is the identity order, interaction order is
fixed-ancilla rather than dynamic, and SIMD aggregation is one class per cycle with no
lookahead. Each is a named pass with a `notes` field saying so, so the next improvement
has an obvious home and a baseline to beat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..arch import Architecture, OperatingPointPolicy
from ..codes.bb import BBCode, Check
from ..cost.models import CostModel, corrected_model
from ..ir.tsir import TSIR, Instruction, Participant, loop_shift
from ..verify import verify
from .cooling import CoolingPolicy, insert_cooling
from .order import bind_dynamic, bind_fixed_waves
from .place import (
    anneal,
    identity_seed,
    interleaved_seed,
    lower_bound_revolutions,
    refine,
)
from .place import window as _window
from .schedule import schedule_events

__all__ = ["CompilePolicy", "CompileResult", "compile_code", "PASSES"]


@dataclass
class CompilePolicy:
    """Every knob the pipeline exposes, in one object that can be swept."""

    placement: str = "anneal"            # identity | interleaved | anneal   (pass 1)
    anneal_iterations: int = 60_000
    refine_steps: int = 300              # hill-climb on the true sweep after annealing
    ancilla_binding: str = "dynamic"     # fixed | dynamic                    (pass 2)
    waves: int = 6                       # only used by `fixed`
    operating_point: str = "fastest"     # fastest | coolest | balanced       (pass 5)
    table: str = "qccdsim_jones"
    insert_cooling: bool = True          #                                    (pass 6)
    event_schedule: bool = True          # time-sliced instead of list order  (pass 7)
    spam: bool = True                    # measure + reset an ancilla when its check ends
    max_contacts_per_batch: int | None = None

    def describe(self) -> dict:
        return dict(self.__dict__)


@dataclass
class PassReport:
    name: str
    detail: str = ""
    notes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class CompileResult:
    program: TSIR
    passes: list[PassReport] = field(default_factory=list)
    policy: CompilePolicy = field(default_factory=CompilePolicy)
    placement: dict[int, str] = field(default_factory=dict)
    binding: dict[str, int] = field(default_factory=dict)
    hops: int = 0
    contacts: int = 0
    batches: int = 0
    revolutions: float = 0.0
    bound_revolutions: float = 0.0
    makespan_us: float = 0.0

    def report(self) -> str:
        out = [f"compiled {self.program.name}: {len(self.program)} instructions"]
        for p in self.passes:
            out.append(f"  {p.name:22s} {p.detail}")
            for n in p.notes:
                out.append(f"  {'':22s}   - {n}")
        return "\n".join(out)


# --------------------------------------------------------------------------- passes


def pass_place(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """1. PLACEMENT -- data qubit -> site, by simulated annealing.

    The objective is the sum of check *windows*, because on a rotating ring the window is
    what a check costs: its ancilla is busy from its first contact to its last, and the
    window is the spread of its members' slots. Annealing minimizes a surrogate, so the
    result is *selected* on the true objective -- the schedule the binder actually
    produces -- rather than assumed to be better.
    """
    loop_id = state["loop_id"]
    nodes = list(arch.device.loops[loop_id].nodes)
    cap = len(nodes)
    if cap < code.n:
        raise ValueError(
            f"{arch.name} has {cap} loop slots but the code needs {code.n}")
    docks = sorted(int(n.id[1:]) for n in arch.device.labelled("dock"))
    state["capacity"] = cap
    state["nodes"] = nodes

    seeds = {"identity": identity_seed(code), "interleaved": interleaved_seed(code)}
    notes: list[str] = []
    if policy.placement == "anneal":
        pl = anneal(code, cap, n_docks=max(1, len(docks)),
                    seed=seeds["interleaved"], iterations=policy.anneal_iterations)
        candidates = {"interleaved": seeds["interleaved"], "annealed": pl.slot_of}
        notes += pl.notes
    else:
        candidates = {policy.placement: seeds[policy.placement]}

    # select on the true objective: whichever placement the binder schedules shortest
    scored = {}
    for tag, slot_of in candidates.items():
        if docks:
            b = _bind(code, slot_of, docks, cap, policy)
            scored[tag] = (b.sweep, slot_of, b)
        else:
            scored[tag] = (0, slot_of, None)
    best_tag = min(scored, key=lambda k: scored[k][0])
    sweep, slot_of, binding = scored[best_tag]
    if len(scored) > 1:
        notes.append("selected on the true sweep, not the surrogate: "
                     + ", ".join(f"{k} {v[0]}" for k, v in sorted(scored.items()))
                     + f" -> {best_tag}")
    if docks and policy.refine_steps:
        slot_of, sweep, more = refine(
            slot_of, lambda sl: _bind(code, sl, docks, cap, policy).sweep,
            steps=policy.refine_steps)
        binding = _bind(code, slot_of, docks, cap, policy)
        notes += more

    ws = [_window([slot_of[q] for q in c.members], cap) for c in code.checks]
    state["slot_of"] = slot_of
    state["placement"] = {q: nodes[slot_of[q]] for q in range(code.n)}
    state["docks"] = docks
    state["binding_cache"] = binding
    bound = lower_bound_revolutions(ws, cap, max(1, len(docks)))
    state["bound_revolutions"] = bound
    return PassReport(
        "1 place", f"{code.n} qubits on {cap} slots, {best_tag}; window mean "
                   f"{sum(ws) / len(ws):.1f}, max {max(ws)}",
        notes + [f"packing bound {bound:.2f} revolutions -- no schedule can beat it"],
        {"mean_window": sum(ws) / len(ws), "bound_revolutions": bound},
    )


def _bind(code: BBCode, slot_of, docks, cap, policy: CompilePolicy):
    if policy.ancilla_binding == "dynamic":
        return bind_dynamic(code.checks, slot_of, docks, cap,
                            max_active=policy.max_contacts_per_batch)
    return bind_fixed_waves(code.checks, slot_of, docks, cap, waves=policy.waves)


def pass_order(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """2. INTERACTION ORDER -- which ancilla serves which check, and when.

    `dynamic` packs the 144 check-arcs onto the 24 docks with an event-driven sweep;
    `fixed` is the shipped six-wave rule, kept because PLAN §7.1 is explicit that a
    mismatched policy makes any architecture look bad, so the comparison has to run both.
    """
    docks = state["docks"]
    if not docks:
        raise ValueError(f"{arch.name} has no dock sites to bind ancillas to")
    binding = state.get("binding_cache")
    if binding is None or binding.strategy != policy.ancilla_binding:
        binding = _bind(code, state["slot_of"], docks, state["capacity"], policy)
    state["bind"] = binding
    bound = state.get("bound_revolutions", 0.0)
    over = binding.revolutions / bound if bound else float("nan")
    return PassReport(
        "2 order", f"{len(code.checks)} checks, {policy.ancilla_binding} binding, "
                   f"{binding.revolutions:.2f} revolutions",
        binding.notes + [f"{over:.2f}x the packing bound"],
        {"revolutions": binding.revolutions},
    )


def pass_route(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """3. ROUTING -- turn the binding into a monotone sweep of rotation offsets.

    Rigid rotation is the only movement, so the route *is* the offset sweep: advance to
    the next offset that has contacts, do them all, advance again. Monotone and one
    direction, so it is deadlock-free by construction -- the cycle-rotation machinery
    PLAN §7 wants for grids has nothing to do here.
    """
    binding = state["bind"]
    by_offset = binding.contacts_by_offset()
    offsets = sorted(by_offset)
    batches = []
    cursor = 0
    for off in offsets:
        batches.append({
            "offset": off, "hops": off - cursor, "direction": "cw",
            "contacts": [(a.check, q, a.dock) for a, q in by_offset[off]],
        })
        cursor = off
    state["batches"] = batches
    total_hops = sum(b["hops"] for b in batches)
    sizes: dict[int, int] = {}
    for b in batches:
        sizes[len(b["contacts"])] = sizes.get(len(b["contacts"]), 0) + 1
    return PassReport(
        "3 route", f"{len(batches)} batches, {total_hops} rotate hops, "
                   f"{sum(len(b['contacts']) for b in batches)} contacts",
        [f"batch sizes {dict(sorted(sizes.items()))}",
         "monotone one-direction sweep: deadlock-free by construction"],
        {"hops": total_hops, "batches": len(batches)},
    )


def pass_simd(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """4. SIMD AGGREGATION -- emit legal cycles.

    One class per cycle, variadic participation (R4), intra never mixed with inter (R4b).

    PLAN §7 wants maximum-independent-set on the conflict graph here, and the reason it is
    not needed is worth stating: pass 2 already resolved every conflict. At one rotation
    offset each dock holds exactly one ion, and distinct docks hold distinct ions, so the
    whole offset's contacts are pairwise compatible -- the batch *is* the maximum
    independent set, and it is found by construction rather than by search. Aggregation
    only gets hard once contacts can be deferred, which is a different binder.
    """
    loop_id = state["loop_id"]
    nodes = state["nodes"]
    prog = TSIR(name=f"{code.name}_esm", arch_spec=f"arch/{arch.name}.arch.json")
    ions = {q: f"d{q + 1}" for q in state["placement"]}
    plc = {ions[q]: node for q, node in state["placement"].items()}
    plc.update({f"a{s}": f"A{s}" for s in state["docks"]})
    prog.add(Instruction(type="init", id=prog.next_id(), placement=plc,
                         quanta={k: 0.0 for k in plc},
                         meta={"code": code.name, "policy": policy.describe()}))

    rail = tuple(sg.id for sg in arch.device.loop_segments(loop_id))
    done: dict[str, int] = {}
    n_spam = 0
    for i, b in enumerate(state["batches"]):
        common = {"batch": i}
        if b["hops"]:
            prog.add(Instruction(
                type="simd", id=prog.next_id(), cls="rotate_cw", mode="inter",
                template=loop_shift(loop_id, b["hops"]), holds=(loop_id,) + rail,
                meta=dict(common, kind="rotate", hops=b["hops"])))
        contacts = b["contacts"]
        if not contacts:
            continue
        prog.add(Instruction(
            type="simd", id=prog.next_id(), cls="dock", mode="inter",
            participants=tuple(
                Participant(ions[q], f"S{d}", f"A{d}", via=(f"V{d}",))
                for _, q, d in contacts),
            meta=dict(common, kind="dock", contacts=len(contacts))))
        prog.add(Instruction(
            type="gate", id=prog.next_id(), gate="CX", mode="intra",
            pairs=tuple((ions[q], f"a{d}") for _, q, d in contacts),
            sites=tuple(f"A{d}" for _, _, d in contacts),
            meta=dict(common, kind="contact", checks=[c.name for c, _, _ in contacts])))
        prog.add(Instruction(
            type="simd", id=prog.next_id(), cls="undock", mode="inter",
            participants=tuple(
                Participant(ions[q], f"A{d}", f"S{d}", via=(f"V{d}",))
                for _, q, d in contacts),
            meta=dict(common, kind="undock", contacts=len(contacts))))

        if policy.spam:
            # a check whose sixth contact just happened is ready to be read out; its
            # ancilla is then reset for whichever check the binder gave that dock next
            finished = []
            for c, _, d in contacts:
                done[c.name] = done.get(c.name, 0) + 1
                if done[c.name] == len(c.members):
                    finished.append(d)
            if finished:
                names = tuple(f"a{d}" for d in sorted(set(finished)))
                prog.add(Instruction(type="measure", id=prog.next_id(), ions=names,
                                     meta=dict(common, kind="readout")))
                prog.add(Instruction(type="reset", id=prog.next_id(), ions=names,
                                     meta=dict(common, kind="reset")))
                n_spam += 2

    state["program"] = prog
    n_contacts = sum(len(b["contacts"]) for b in state["batches"])
    util = n_contacts / max(1, len(state["batches"]))
    return PassReport(
        "4 simd", f"{len(prog)} instructions, batch utilization {util:.2f} of "
                  f"{len(state['docks'])}",
        [f"{n_spam} SPAM instructions: every check is measured and its ancilla reset",
         "the batch is the maximum independent set by construction -- pass 2 left no "
         "conflicts for a search to resolve"],
        {"utilization": util},
    )


def pass_opoint(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """5. OPERATING POINT -- pick a point on each primitive's (time, quanta) curve."""
    pol = OperatingPointPolicy(policy.table, policy.operating_point)
    state["model"] = corrected_model(policy.table)
    from dataclasses import replace as _replace

    state["model"] = _replace(state["model"], policy=pol)
    coverage = arch.primitives.table_coverage(policy.table)
    missing = [k for k, v in coverage.items() if not v]
    return PassReport(
        "5 opoint", f"table {policy.table}, objective {policy.operating_point}",
        [f"table is silent about {missing}; the policy falls back across tables there"]
        if missing else [],
    )


def pass_cooling(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """6. COOLING INSERTION -- satisfy R7/R7c at minimum time cost."""
    if not policy.insert_cooling:
        return PassReport("6 cooling", "skipped by policy",
                          ["the program is not R7c-legal without it"])
    r = insert_cooling(state["program"], arch, state["model"], policy=CoolingPolicy())
    state["program"] = r.program
    return PassReport(
        "6 cooling",
        f"{r.n_cools} global operations, {r.cooling_us / 1000:.1f} ms "
        f"({100 * r.cooling_share:.1f}% of runtime)",
        [f"R7 violations {r.r7_violations_before} -> {r.r7_violations_after}"],
        {"n_cools": r.n_cools, "cooling_us": r.cooling_us},
    )


def pass_schedule(arch: Architecture, code: BBCode, policy: CompilePolicy, state: dict):
    """7. EVENT SCHEDULING -- earliest resource-feasible start, then verify.

    List order runs everything back to back. Time-slicing lets instructions that share no
    resource overlap -- here, an ancilla readout under the rail's next rotation, because
    optics and the DC transport pathway are different hardware.
    """
    rep = verify(state["program"], arch, state["model"], check_metrics=False)
    res = rep.result
    notes: list[str] = []
    makespan = res.total_us
    if policy.event_schedule:
        sr = schedule_events(state["program"], arch, res)
        state["program"] = sr.program
        makespan = sr.makespan_us
        notes += sr.notes
        rep = verify(state["program"], arch, state["model"], check_metrics=False)
        res = rep.result

    state["program"].metrics = {
        "total_cost": res.total_cost,
        "total_steps": res.total_steps,
        "runtime_us": res.total_us,
        "total_quanta": res.total_quanta(),
        "peak_quanta": res.peak_quanta,
        "contacts": res.n_gate_pairs,
    }
    state["program"].meta["metrics_model"] = state["model"].name
    state["makespan_us"] = makespan
    rules = rep.rules.summary()
    return PassReport(
        "7 schedule",
        f"{makespan / 1000:.2f} ms, cost {res.total_cost:,.0f}, "
        f"steps {res.total_steps:,}",
        notes + ([f"RULES FAILED: {rules['failed']}"] if rules["failed"] else
                 [f"rules pass: {' '.join(rules['passed'])}"]),
        {"runtime_us": res.total_us, "makespan_us": makespan},
    )


PASSES: Sequence[Callable] = (
    pass_place, pass_order, pass_route, pass_simd, pass_opoint,
    pass_cooling, pass_schedule,
)


# --------------------------------------------------------------------------- driver


def compile_code(
    arch: Architecture,
    code: BBCode,
    *,
    policy: CompilePolicy | None = None,
    passes: Sequence[Callable] = PASSES,
    loop_id: str | None = None,
) -> CompileResult:
    """Run the pipeline: a code in, a verified TSIR program out."""
    policy = policy or CompilePolicy()
    loops = [lid for lid, lp in arch.device.loops.items() if lp.closed]
    if not loops:
        raise ValueError(f"{arch.name} has no closed loop; the ring router needs one")
    state: dict = {"loop_id": loop_id or loops[0]}
    reports: list[PassReport] = []
    for fn in passes:
        reports.append(fn(arch, code, policy, state))
    return CompileResult(
        program=state["program"], passes=reports, policy=policy,
        placement=state.get("placement", {}),
        binding={a.check.name: a.dock for a in state["bind"].assignments}
        if "bind" in state else {},
        hops=sum(b["hops"] for b in state.get("batches", [])),
        contacts=sum(len(b["contacts"]) for b in state.get("batches", [])),
        batches=len(state.get("batches", [])),
        revolutions=state["bind"].revolutions if "bind" in state else 0.0,
        bound_revolutions=state.get("bound_revolutions", 0.0),
        makespan_us=state.get("makespan_us", 0.0),
    )


def _circ(a: int, b: int, cap: int) -> int:
    """Fewest hops from offset `a` to offset `b` on a `cap`-slot loop."""
    d = (b - a) % cap
    return min(d, cap - d)


def _direction(a: int, b: int, cap: int) -> str:
    d = (b - a) % cap
    return "cw" if d <= cap - d else "ccw"
