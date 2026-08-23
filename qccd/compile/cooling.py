"""Cooling insertion.  PLAN §7 pass 6, milestone M2.

The shipped schedule contains **zero** cooling operations, and PLAN §0.4 is that this is
not a refinement but a missing first-order component: about 1700 quanta per data ion per
round have to go somewhere, against an MS-gate budget of 1-2.  R7c makes it mandatory --
without cooling, a broadcast-wired machine cannot pass a logical error rate of 1e-4
(2510.23519, 2606.06455).

Cooling is **global**.  Doppler sheet beams cover the whole trap, so one cooling
operation cools every ion: it costs schedule time but does not serialize per ion.  That
single physical fact is what keeps the answer tractable -- a per-ion schedule for 864
contacts would be 864 serialized 300 us operations, while a global one needs only as many
as there are moments at which *some* gate is about to be too hot.

Two triggers
------------
**R7 (mandatory).**  A 2Q gate needs both its ions at n-bar <= the budget.  This one
provably converges in a single pass: a global cool zeroes every ion, so inserting one
strictly lowers n-bar everywhere downstream; a gate that satisfied R7 before still does,
and a gate that violated it now starts from zero.  The pass asserts that on a re-replay
rather than assuming it.

**Ion loss (optional, off by default).**  An ion survives only ~85 uncooled junction round
trips before it is *lost*, not merely dephased (arXiv:1210.3655).  That is a cap on
accumulated n-bar between cools regardless of when the next gate is.  It is iterated to a
fixed point; when one single instruction jumps the cap on its own, no cooling schedule can
fix it, and the pass says which instruction rather than padding the program with cools
that cannot help.

The n-bar between cools is sampled **at every instruction**, not only at the cools -- the
maximum of an accumulation is reached at its end, and the tail after the last cool has no
cool to be sampled at.  A diagnostic that reads "safe" precisely in the uncooled case it
exists to flag would be worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..arch import Architecture
from ..cost.models import CostModel
from ..ir import provenance as prov
from ..ir.tsir import TSIR, Instruction
from ..verify.replay import replay

__all__ = ["CoolingPolicy", "CoolingResult", "insert_cooling", "ION_LOSS_ROUND_TRIPS"]

#: Measured uncooled survival limit, in junction round trips (arXiv:1210.3655): >98% at
#: ~65, ion loss beyond ~85.
ION_LOSS_ROUND_TRIPS = 85


@dataclass(frozen=True)
class CoolingPolicy:
    """When to spend 300 us removing every quantum in the trap."""

    #: R7's budget.  `None` reads `ms_gate.max_quanta` off the architecture.
    max_gate_quanta: float | None = None
    #: Optional cap on accumulated n-bar between cools, for the ion-loss limit rather
    #: than the fidelity limit.  `None` disables the trigger (it is then reported as a
    #: diagnostic only).
    max_quanta_between_cools: float | None = None
    #: Global Doppler sheet.  With `False`, each cool names the ions it acts on.
    broadcast: bool = True
    #: Guard on the ion-loss fixed point.
    max_iterations: int = 12


@dataclass
class CoolingResult:
    program: TSIR
    n_cools: int = 0
    cooling_us: float = 0.0
    cooling_share: float = 0.0
    runtime_us_before: float = 0.0
    runtime_us_after: float = 0.0
    r7_violations_before: int = 0
    r7_violations_after: int = 0
    peak_quanta_before: float = 0.0
    peak_quanta_after: float = 0.0
    peak_quanta_between_cools: float = 0.0
    equivalent_junction_transits_between_cools: float = 0.0
    ion_loss_limit_transits: int = ION_LOSS_ROUND_TRIPS
    gates_needing_cooling: int = 0
    gates_total: int = 0
    budget: float = 0.0
    triggers: dict[str, int] = field(default_factory=dict)
    iterations: int = 1
    converged: bool = True
    unfixable: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "n_cools": self.n_cools,
            "cooling_us": self.cooling_us,
            "cooling_ms": self.cooling_us / 1000.0,
            "cooling_share_of_runtime": self.cooling_share,
            "runtime_ms_before": self.runtime_us_before / 1000.0,
            "runtime_ms_after": self.runtime_us_after / 1000.0,
            "r7_violations_before": self.r7_violations_before,
            "r7_violations_after": self.r7_violations_after,
            "peak_quanta_before": self.peak_quanta_before,
            "peak_quanta_after": self.peak_quanta_after,
            "peak_quanta_between_cools": self.peak_quanta_between_cools,
            "equivalent_junction_transits_between_cools": (
                self.equivalent_junction_transits_between_cools
            ),
            "ion_loss_limit_transits": self.ion_loss_limit_transits,
            "gates_needing_cooling": self.gates_needing_cooling,
            "gates_total": self.gates_total,
            "budget": self.budget,
            "triggers": dict(self.triggers),
            "iterations": self.iterations,
            "converged": self.converged,
            "unfixable": list(self.unfixable),
            "notes": list(self.notes),
        }


def _cool(
    instr_id: int, policy: CoolingPolicy, why: str, meta: Mapping, ions: tuple[str, ...]
) -> Instruction:
    return Instruction(
        type="cool",
        id=instr_id,
        broadcast=policy.broadcast,
        # a non-broadcast cool has to name the ions it acts on, or the replay -- rightly
        # -- refuses to execute it
        ions=() if policy.broadcast else ions,
        meta=dict(meta, kind="cool", trigger=why),
    )


def _rebuild(
    prog: TSIR,
    arch_spec: str,
    marks: Mapping[int, str],
    policy: CoolingPolicy,
    name: str,
    meta: Mapping,
    ions: tuple[str, ...],
) -> TSIR:
    """Insert a cool before every marked instruction.  IT DOES NOT RENUMBER.

    It used to.  `Instruction.id` is the handle every join in the system keys on, so a
    pass that re-labels wholesale silently re-targets every one of them: measured on the
    deck program, 1,576 of the 1,578 ids `prov.index_by_line` reported for
    `import_deck.py:162` pointed at a different instruction after one pass, with no
    exception and no diagnostic.  `meta.src_id` existed only to paper over that, and it
    survived exactly one rebuild by construction.  Now the id IS the src_id: surviving
    instructions keep both their identity and their object, and the cools this pass
    inserts draw fresh ids from the program's allocator, so `before_instruction` and
    `CoolingResult.unfixable` name instructions of the program the caller receives.
    """
    out: list[Instruction] = []
    seq = prog.id_seq
    for instr in prog.instructions:
        why = marks.get(instr.id)
        if why is not None:
            out.append(
                _cool(
                    seq,
                    policy,
                    why,
                    {"batch": instr.meta.get("batch"),
                     "before_instruction": instr.id},
                    ions,
                )
            )
            seq += 1
        out.append(instr)
    return TSIR(
        name=name,
        arch_spec=arch_spec,
        instructions=out,
        metrics=dict(prog.metrics),
        meta=dict(meta),
        id_seq=seq,
    )


def insert_cooling(
    prog: TSIR,
    arch: Architecture,
    model: CostModel,
    *,
    policy: CoolingPolicy = CoolingPolicy(),
) -> CoolingResult:
    """Return a copy of `prog` made legal under R7/R7c, plus the cooling accounting."""
    if not model.models_heating:
        raise ValueError(
            f"cost model {model.name!r} does not model heating, so there is nothing for "
            f"a cooling pass to schedule"
        )

    budget = policy.max_gate_quanta
    if budget is None:
        budget = float(arch.primitives.scalar("ms_gate").get("max_quanta", float("inf")))
    # the budget is a policy knob, so it has to reach the rule that enforces it rather
    # than only the metadata of the emitted program
    rule_config = {"max_gate_quanta": budget}

    base_meta = dict(prog.meta)
    base_meta["derived_from"] = prog.name
    base_meta["cooling_policy"] = {
        "max_gate_quanta": budget,
        "max_quanta_between_cools": policy.max_quanta_between_cools,
        "broadcast": policy.broadcast,
    }
    name = f"{prog.name}+cooling"

    before = replay(
        prog, arch, model, check_rules=True, only_rules=["R7"], keep_cycles=False,
        rule_config=rule_config,
    )
    r7_before = sum(1 for v in before.rules.violations if v.rule == "R7")
    offenders = {v.instr_id: "R7_gate" for v in before.rules.violations if v.rule == "R7"}
    gates_total = sum(1 for i in prog.instructions if i.type == "gate")
    all_ions = tuple(sorted(before.per_ion_quanta))

    cooled = _rebuild(prog, prog.arch_spec, offenders, policy, name, base_meta, all_ions)
    triggers = {"R7_gate": len(offenders), "ion_loss": 0}
    iterations = 1
    converged = True
    unfixable: set[int] = set()

    # ---- optional ion-loss fixed point ------------------------------------
    cap = policy.max_quanta_between_cools
    if cap is not None:
        for iterations in range(1, policy.max_iterations + 1):
            # blame the instruction whose EXECUTION crossed the cap, not the next one.
            # The probe fires before each instruction, so a pre-value above the cap
            # means the *previous* instruction is the one that pushed it there, and a
            # cool inserted after that instruction would not undo the excursion.
            blame: list[int] = []
            state = {"prev": None}
            preceded_by_cool: set[int] = set()
            prev_was_cool = True  # the start of the program is as cool as a cool
            for instr in cooled.instructions:
                if instr.type not in ("cool", "init") and prev_was_cool:
                    preceded_by_cool.add(instr.id)
                prev_was_cool = instr.type in ("cool", "init")

            def _probe(instr: Instruction, current: Mapping[str, float]) -> None:
                if max(current.values(), default=0.0) > cap and state["prev"] is not None:
                    blame.append(state["prev"])
                state["prev"] = instr.id

            pass_res = replay(
                cooled, arch, model, check_rules=False, keep_cycles=False, probe=_probe
            )
            # `peak_quanta` is the running maximum of the live n-bar, and a cool zeroes
            # it, so it covers the tail after the last instruction that no probe sees
            if pass_res.peak_quanta <= cap:
                break
            if not blame and state["prev"] is not None:
                blame.append(state["prev"])  # the excursion is in the final stretch

            marks: dict[int, str] = {}
            for iid in blame:
                # `iid` is durable across `_rebuild` now, so there is no second identity
                # to translate through: the id an earlier iteration gave up on is the
                # same id this one sees.
                if iid in unfixable:
                    continue
                if iid in preceded_by_cool:
                    # a cool already sits immediately before it and the cap is still
                    # exceeded by the time it finishes, so this instruction jumps the
                    # cap on its own: no cooling schedule can fix it
                    unfixable.add(iid)
                    continue
                marks[iid] = "ion_loss"
            if not marks:
                break  # everything left is unfixable; reported, not looped on
            n_before = sum(1 for i in cooled.instructions if i.type == "cool")
            cooled = _rebuild(
                cooled, prog.arch_spec, marks, policy, name, base_meta, all_ions
            )
            triggers["ion_loss"] += (
                sum(1 for i in cooled.instructions if i.type == "cool") - n_before
            )
        else:
            # the loop ran out of iterations without ever reaching a clean pass
            converged = False

    # ---- verify and account ------------------------------------------------
    after = replay(
        cooled, arch, model, check_rules=True, only_rules=["R7"], keep_cycles=False,
        rule_config=rule_config,
    )
    r7_after = sum(1 for v in after.rules.violations if v.rule == "R7")
    n_cools = sum(1 for i in cooled.instructions if i.type == "cool")
    cooling_us = after.us_by_type.get("cool", 0.0)
    # `peak_quanta` is the running maximum of the live n-bar and a cool zeroes it, so
    # this is the peak accumulation between two cools -- including the stretch before
    # the first one and the tail after the last, which sampling only at cools would miss
    # entirely (and would report as a reassuring 0.0 for a program with no cools at all)
    peak_between = after.peak_quanta

    junction_q = _junction_quanta(arch, model)
    equiv = peak_between / junction_q if junction_q else 0.0

    notes: list[str] = []
    if r7_after:
        notes.append(
            f"{r7_after} R7 violations survive: one global cool immediately before the "
            f"gate is not enough, so heating accrues inside the docking sequence itself"
        )
    else:
        notes.append(
            "one global cool per offending gate suffices, and is minimal for this "
            "trigger set: cooling only lowers n-bar, so no new violation can appear"
        )
    if after.total_us:
        notes.append(
            f"cooling is {100 * cooling_us / after.total_us:.1f}% of the cooled runtime "
            f"({cooling_us / 1000:.1f} ms of {after.total_us / 1000:.1f} ms)"
        )
    else:
        notes.append("the program has zero elapsed time, so cooling has no share of it")
    notes.append(
        f"peak n-bar between cools is {peak_between:.1f} quanta, about "
        f"{equiv:.0f} degree-3 junction transits against a measured uncooled survival "
        f"limit of ~{ION_LOSS_ROUND_TRIPS} round trips"
    )
    if cap is not None:
        if unfixable:
            notes.append(
                f"{len(unfixable)} instruction(s) exceed the {cap}-quanta cap on their "
                f"own even with a cool immediately before them, so no cooling schedule "
                f"can satisfy it there: {sorted(unfixable)[:8]}"
            )
        if not converged:
            notes.append(
                f"the ion-loss cap of {cap} quanta did not reach a fixed point in "
                f"{policy.max_iterations} iterations"
            )
        if peak_between > cap and not unfixable and converged:
            notes.append(
                f"WARNING: peak n-bar between cools ({peak_between:.3f}) still exceeds "
                f"the cap ({cap})"
            )

    # The cools this pass inserted are the only instructions in the program nobody
    # claims: they were not written by the user and not imported from the artifact, so
    # 396 of the flagship page's 1,975 listing rows read "no source line recorded".
    # They do have a source -- this pass, and the policy it ran under.
    prov.stamp(cooled, "compile.insert_cooling", only_untagged=True,
               max_gate_quanta=budget, broadcast=policy.broadcast,
               max_quanta_between_cools=policy.max_quanta_between_cools)

    return CoolingResult(
        program=cooled,
        n_cools=n_cools,
        cooling_us=cooling_us,
        cooling_share=cooling_us / after.total_us if after.total_us else 0.0,
        runtime_us_before=before.total_us,
        runtime_us_after=after.total_us,
        r7_violations_before=r7_before,
        r7_violations_after=r7_after,
        peak_quanta_before=before.peak_quanta,
        peak_quanta_after=after.peak_quanta,
        peak_quanta_between_cools=peak_between,
        equivalent_junction_transits_between_cools=equiv,
        gates_needing_cooling=len(offenders),
        gates_total=gates_total,
        budget=budget,
        triggers=triggers,
        iterations=iterations,
        converged=converged,
        unfixable=sorted(unfixable),
        notes=notes,
    )


def _junction_quanta(arch: Architecture, model: CostModel) -> float:
    """Quanta per degree-3 junction transit under the model in use, for the ion-loss
    conversion.  Zero if the model charges none."""
    getter = getattr(model, "junction_point", None)
    if getter is None:
        return 0.0
    try:
        point = getter(arch, 3)
    except KeyError:
        return 0.0
    return point.quanta if point else 0.0
