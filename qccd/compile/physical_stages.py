"""Checked physical compilation stages 8--11.

The first backend schedules instructions serially, preserving SIMD *within* an
instruction.  This is deliberate: the existing replay kernel executes list order
and cannot certify a concurrent schedule merely by ignoring its timestamps.
The thermal checker below consumes the actual intervals (including idle gaps)
and rejects cross-instruction overlap.  More aggressive schedulers can be added
behind a checker that actually supports their event semantics.

These are executable checks under the declared discrete physical model, not Lean
proofs, calibration validation, or a proof of quantum gate semantics.  The cost
model and existing position replay are part of the trusted computing base.  No
optimizer-private state is used by a checker; annotations are recomputed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping

from ..arch import Architecture
from ..cost.models import CostModel
from ..ir.tsir import TSIR, Instruction, iter_operands, iter_pairs, validate_program
from ..verify.replay import replay
from .schedule import resources_of

__all__ = [
    "PhysicalResult", "bind_operating_points", "check_binding", "schedule_serial",
    "check_schedule", "analyze_thermal", "insert_required_cooling", "check_cooling",
    "finish_program",
]

SCHEMA = "qccd.physical-binding@1"
ANNOTATIONS = ("t0", "t1", "cost", "steps", "quanta_delta", "operating_point")
SERIAL_LIMIT = (
    "Serial between instructions; SIMD inside an instruction is retained. "
    "Cross-instruction concurrent thermal execution is unsupported and rejected."
)


@dataclass
class PhysicalResult:
    program: TSIR
    ok: bool
    stages: list[dict] = field(default_factory=list)
    iterations: int = 0
    error: str | None = None


def _snapshot(program: TSIR) -> dict:
    return copy.deepcopy(program.to_json())


def _copy(program: TSIR, instructions=None) -> TSIR:
    out = TSIR.from_json(_snapshot(program))
    if instructions is not None:
        out.instructions = copy.deepcopy(list(instructions))
    return out


def _clear(instruction: Instruction) -> Instruction:
    return replace(instruction, **{key: None for key in ANNOTATIONS})


def _bare(program: TSIR) -> TSIR:
    out = _copy(program, [_clear(i) for i in program.instructions])
    out.metrics = {}  # these claims were made before binding / cooling
    return out


def _body(instruction: Instruction) -> dict:
    return _clear(instruction).to_json()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _diag(code: str, message: str, iid: int | None = None) -> dict:
    result = {"code": code, "message": message}
    if iid is not None:
        result["instruction_id"] = iid
    return result


def _checked(diagnostics, coverage, *, method=None, limitations=(), assumptions=()) -> dict:
    return {"ok": not diagnostics, "status": "failed" if diagnostics else "passed",
            "coverage": list(coverage), "diagnostics": list(diagnostics),
            "limitations": list(limitations), "assumptions": list(assumptions),
            "method": method or "physical_stages.precondition"}


def _finite(value, label: str, *, nonnegative=True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return number


class _WithoutElapsedHeating:
    """Use precisely the supplied physical methods, but separate idle heat.

    Intrinsic deposits and occupancy-dependent durations are reconstructed by
    the position replay. Elapsed heating belongs to the *timed* thermal stage.
    """
    def __init__(self, model: CostModel):
        self.model = model

    def __getattr__(self, name):
        return getattr(self.model, name)

    def anomalous_per_us(self, arch):
        return 0.0


def _physical_evidence(program: TSIR, arch: Architecture, model: CostModel) -> dict:
    """Reconstruct charges and positions from instruction bodies, never claims."""
    errors = validate_program(program)
    if errors:
        raise ValueError("; ".join(errors))
    if not program.instructions or program.instructions[0].type != "init":
        raise ValueError("physical compilation requires an initial placement")
    if sum(i.type == "init" for i in program.instructions) != 1:
        raise ValueError("this physical backend supports one initial placement only")
    if not model.models_time or not model.models_heating:
        raise ValueError("physical compilation requires a time-and-heating cost model")
    rate = _finite(model.anomalous_per_us(arch), "anomalous heating rate")
    bare = _bare(program)
    starts: dict[int, dict] = {}
    positions: dict[int, dict] = {}
    ends: dict[int, dict] = {}
    before_positions = dict(bare.instructions[0].placement)
    for ion, quanta in bare.instructions[0].quanta.items():
        if ion not in before_positions:
            raise ValueError(f"initial quanta names unplaced ion {ion}")
        _finite(quanta, f"initial quanta for {ion}")

    def probe(instruction, current):
        starts[instruction.id] = dict(current)

    def on_cycle(view):
        positions.setdefault(view.instr.id, dict(view.pos_before))
        ends[view.instr.id] = dict(view.pos_after)

    result = replay(bare, arch, _WithoutElapsedHeating(model), check_rules=False,
                    probe=probe, on_cycle=on_cycle)
    totals: dict[int, dict] = {}
    for cycle in result.cycles:
        charge = totals.setdefault(cycle.instr_id, {"duration_us": 0.0,
                                                  "cost": 0.0, "steps": 0})
        charge["duration_us"] += _finite(cycle.t1 - cycle.t0, "cycle duration")
        charge["cost"] += _finite(cycle.cost, "cycle cost")
        _finite(cycle.depth, "cycle depth")
        charge["steps"] += cycle.depth
    identity = {
        "architecture_sha256": _digest(arch.to_json(expanded=True)),
        "model": copy.deepcopy(model.describe()),
        "model_class": f"{type(model).__module__}.{type(model).__qualname__}",
    }
    cool_spec = dict(arch.primitives.scalars.get("cool", {}))
    bindings: dict[int, dict] = {}
    cool_concurrent = bool(cool_spec.get("concurrent_with_transport", False))
    for index, instruction in enumerate(bare.instructions):
        iid = instruction.id
        positions.setdefault(iid, dict(before_positions))
        if instruction.type == "init":
            positions[iid] = dict(instruction.placement)
            ends[iid] = dict(instruction.placement)
        before_positions = ends.get(iid, positions[iid])
        ends.setdefault(iid, dict(before_positions))
        before_heat = starts[iid]
        after_heat = (starts[bare.instructions[index + 1].id]
                      if index + 1 < len(bare.instructions) else result.final_quanta)
        if instruction.type == "init":
            heat = {}
        elif instruction.type == "cool":
            targets = instruction.ions or tuple(positions[iid])
            amount = sum(_finite(q, "cooling-associated heat")
                         for q in model.cool(arch).quanta.values())
            heat = {ion: amount for ion in targets if amount}
        else:
            heat = {}
            for ion in after_heat:
                delta = after_heat[ion] - before_heat.get(ion, 0.0)
                if delta < -1e-9:
                    raise ValueError(f"instruction {iid} has unsupported negative heat")
                if delta > 0:
                    heat[ion] = _finite(delta, "intrinsic heat")
        resources, ions = resources_of(arch, instruction, cool_concurrent)
        # Include singles and occupied sites, absent from the legacy scheduler's
        # pair-only resource helper. Rigid templates conservatively hold all ions.
        ions.update(i for operands in iter_operands(instruction) for i in operands)
        if any(i.startswith("*") for i in ions) or instruction.type == "init":
            ions = set(positions[iid])
        for ion in ions:
            if ion in positions[iid]:
                resources.add(positions[iid][ion])
        resources.update("ion:" + ion for ion in ions)
        resources.add("@program-order")
        bindings[iid] = {
            "schema": SCHEMA, **identity, **totals[iid],
            "intrinsic_quanta": heat, "anomalous_per_us": rate,
            "resources": sorted(resources),
        }
    return {"bindings": bindings, "positions": positions, "ends": ends,
            "rate": rate, "identity": identity}


def bind_operating_points(program: TSIR, arch: Architecture, model: CostModel) -> TSIR:
    """Stage 8: bind concrete physical charges; invalidate prior times/metrics."""
    evidence = _physical_evidence(program, arch, model)
    out = _bare(program)
    out.instructions = [replace(i, operating_point=copy.deepcopy(evidence["bindings"][i.id]),
                                cost=evidence["bindings"][i.id]["cost"],
                                steps=evidence["bindings"][i.id]["steps"],
                                quanta_delta=copy.deepcopy(
                                    evidence["bindings"][i.id]["intrinsic_quanta"]))
                        for i in out.instructions]
    return out


def check_binding(before: TSIR, after: TSIR, arch: Architecture, model: CostModel) -> dict:
    """Reprice instruction bodies against the pinned architecture and model."""
    diagnostics = []
    try:
        expected = _physical_evidence(before, arch, model)["bindings"]
        if (before.name, before.arch_spec, before.id_seq, before.meta) != (
                after.name, after.arch_spec, after.id_seq, after.meta):
            diagnostics.append(_diag("program_changed", "binding changed program identity or metadata"))
        if [_body(i) for i in before] != [_body(i) for i in after]:
            diagnostics.append(_diag("body_changed", "binding changed instruction bodies, order, or IDs"))
        for instruction in after:
            binding = expected.get(instruction.id)
            if binding is None or instruction.operating_point != binding:
                diagnostics.append(_diag("binding_mismatch", "physical annotations differ from reconstruction",
                                         instruction.id))
                continue
            if instruction.cost != binding["cost"] or instruction.steps != binding["steps"] \
                    or dict(instruction.quanta_delta or {}) != binding["intrinsic_quanta"]:
                diagnostics.append(_diag("charge_mismatch", "cost, depth, or heat claim differs",
                                         instruction.id))
    except (ValueError, TypeError, KeyError, RuntimeError, OverflowError) as exc:
        diagnostics.append(_diag("binding_error", str(exc)))
    return _checked(diagnostics, ["instruction preservation", "occupancy-sensitive durations",
                                 "model and architecture identity", "intrinsic heat and resources"],
                    method="physical_stages.check_binding",
                    assumptions=["Model and position replay are trusted; physical calibration is not proved."])


def schedule_serial(bound: TSIR) -> TSIR:
    """Stage 9 safe backend: preserve every list-order dependency."""
    out = _copy(bound)
    out.metrics = {}
    time = 0.0
    instructions = []
    for instruction in out:
        binding = instruction.operating_point or {}
        if binding.get("schema") != SCHEMA:
            raise ValueError(f"instruction {instruction.id} lacks a physical binding")
        duration = _finite(binding.get("duration_us"), "bound duration")
        end = time + duration
        instructions.append(replace(instruction, t0=time, t1=end))
        time = end
    out.instructions = instructions
    return out


def check_schedule(bound: TSIR, timed: TSIR, arch: Architecture, model: CostModel) -> dict:
    diagnostics = list(check_binding(bound, timed, arch, model)["diagnostics"])
    unchanged_binding = lambda i: replace(i, t0=None, t1=None).to_json()
    if [unchanged_binding(i) for i in bound] != [unchanged_binding(i) for i in timed]:
        diagnostics.append(_diag("binding_changed", "scheduling may change timestamps only"))
    # The serial backend checks a stronger condition than resource exclusion:
    # every interval follows its predecessor, including barriers and zero-time
    # decoder events. Consequently all per-resource dependencies are preserved.
    prior_end = 0.0
    for index, instruction in enumerate(timed):
        try:
            start = _finite(instruction.t0, "start time")
            end = _finite(instruction.t1, "end time")
            duration = _finite((instruction.operating_point or {}).get("duration_us"),
                               "bound duration")
            if end < start or not math.isclose(end - start, duration,
                                               rel_tol=1e-10, abs_tol=1e-6):
                diagnostics.append(_diag("duration_mismatch", "scheduled duration differs from model",
                                         instruction.id))
            if start < prior_end - 1e-7:
                diagnostics.append(_diag("unsupported_overlap", "interval violates serial dependency/resource order",
                                         instruction.id))
            if index == 0 and (start != 0 or end != 0):
                diagnostics.append(_diag("initial_time", "initial placement must occur at time zero",
                                         instruction.id))
            prior_end = end
        except (ValueError, TypeError) as exc:
            diagnostics.append(_diag("invalid_time", str(exc), instruction.id))
    return _checked(diagnostics, ["actual intervals", "duration correspondence",
                                 "program-order dependencies", "exclusive resource use"],
                    method="physical_stages.check_schedule", assumptions=[SERIAL_LIMIT])


def _cooling_diagnostics(instruction: Instruction, pos: Mapping, arch: Architecture) -> list[dict]:
    if instruction.type != "cool":
        return []
    problems = []
    spec = arch.primitives.scalars.get("cool", {})
    if spec.get("removes_quanta") != "all":
        problems.append(_diag("unsupported_cooling_model",
                              "cooling must explicitly declare removes_quanta='all'", instruction.id))
    if instruction.broadcast and not spec.get("broadcastable", False):
        problems.append(_diag("cooling_not_broadcastable", "hardware does not permit broadcast cooling",
                              instruction.id))
    if instruction.broadcast and instruction.ions:
        problems.append(_diag("ambiguous_cooling", "broadcast cooling must not also name a subset",
                              instruction.id))
    targets = tuple(pos) if instruction.broadcast else instruction.ions
    if not targets:
        problems.append(_diag("empty_cooling", "cooling has no targets", instruction.id))
    for ion in targets:
        if ion not in pos or not arch.can(pos[ion], "cool"):
            problems.append(_diag("cooling_capability", f"ion {ion} is not at a cooling-capable site",
                                  instruction.id))
    return problems


def analyze_thermal(timed: TSIR, arch: Architecture, model: CostModel) -> dict:
    """Stage 10: integrate elapsed heat on actual times and check gate-entry R7.

    The declared cooling primitive resets named ions at its *completion*. All
    ions accrue anomalous heat during operations and gaps. Intrinsic instruction
    heat is deposited at completion; no intermediate observation is permitted
    inside a SIMD instruction. This is the existing discrete physical model.
    """
    schedule_check = check_schedule(timed, timed, arch, model)
    if not schedule_check["ok"]:
        return {"check": _checked(schedule_check["diagnostics"], ["schedule precondition"],
                                  method="physical_stages.analyze_thermal",
                                  assumptions=[SERIAL_LIMIT]), "trace": [], "violations": [],
                "peak_quanta": None, "final_quanta": {}, "makespan_us": None}
    evidence = _physical_evidence(timed, arch, model)
    diagnostics, violations, trace = [], [], []
    current: dict[str, float] = {}
    now = 0.0
    peak = 0.0
    raw_budget = arch.primitives.scalar("ms_gate").get("max_quanta")
    try:
        budget = _finite(raw_budget, "declared two-qubit gate thermal budget")
    except (ValueError, TypeError) as exc:
        return {"check": _checked([_diag("missing_thermal_budget", str(exc))], ["thermal budget"],
                                  method="physical_stages.analyze_thermal"),
                "trace": [], "violations": [], "peak_quanta": None, "final_quanta": {},
                "makespan_us": None}

    def advance(end):
        nonlocal now, peak
        increment = _finite(evidence["rate"] * (end - now), "elapsed heating deposit")
        for ion in current:
            current[ion] = _finite(current[ion] + increment, f"accumulated heat for {ion}")
        now = end
        peak = max(peak, max(current.values(), default=0.0))

    for instruction in timed:
        if instruction.type == "init":
            current = {ion: float(instruction.quanta.get(ion, 0.0))
                       for ion in instruction.placement}
            peak = max(peak, max(current.values(), default=0.0))
            trace.append({"instruction_id": instruction.id, "t0": 0.0, "t1": 0.0,
                          "at_start": dict(current), "at_end": dict(current)})
            continue
        advance(instruction.t0)
        start_heat = dict(current)
        for pair in iter_pairs(instruction) if instruction.type == "gate" else ():
            for ion in pair:
                if ion not in current:
                    diagnostics.append(_diag("unknown_ion", f"thermal gate references {ion}", instruction.id))
                elif current[ion] > budget:
                    violation = {"instruction_id": instruction.id, "ion": ion,
                                 "quanta": current[ion], "budget": budget}
                    violations.append(violation)
                    diagnostics.append(_diag("R7", f"{ion} enters gate at {current[ion]:.9g} > {budget:g}",
                                             instruction.id))
        advance(instruction.t1)
        for ion, delta in evidence["bindings"][instruction.id]["intrinsic_quanta"].items():
            current[ion] = _finite(current[ion] + delta, f"accumulated heat for {ion}")
        peak = max(peak, max(current.values(), default=0.0))
        if instruction.type == "cool":
            problems = _cooling_diagnostics(instruction, evidence["positions"][instruction.id], arch)
            diagnostics.extend(problems)
            if not problems:
                for ion in (tuple(current) if instruction.broadcast else instruction.ions):
                    current[ion] = 0.0
        trace.append({"instruction_id": instruction.id, "t0": instruction.t0,
                      "t1": instruction.t1, "at_start": start_heat, "at_end": dict(current)})
    # Match the existing program-level R7c obligation exactly: any program with
    # a two-qubit gate under a heating model must contain cooling even if its
    # particular short run stays below R7. This is a declared compiler policy,
    # not a derivation of a logical-error threshold.
    first_pair_gate = next((i for i in timed if i.type == "gate" and list(iter_pairs(i))), None)
    if first_pair_gate is not None and not any(i.type == "cool" for i in timed):
        diagnostics.append(_diag("R7c", "two-qubit program requires at least one cooling operation",
                                 first_pair_gate.id))
        violations.extend({"instruction_id": first_pair_gate.id, "ion": ion,
                           "reason": "R7c"}
                          for pair in iter_pairs(first_pair_gate) for ion in pair)
    return {"check": _checked(diagnostics, ["R7 gate-entry budgets", "R7c mandatory cooling",
                                           "actual elapsed-time heating",
                                           "intrinsic transport/gate heat", "cooling scope and capability"],
                               method="physical_stages.analyze_thermal",
                               assumptions=[SERIAL_LIMIT,
                                            "R7c follows the declared mandatory-cooling policy, not a logical-error theorem.",
                                            "The discrete model defines instantaneous completion deposits and cooling reset."]),
            "trace": trace, "violations": violations, "peak_quanta": peak,
            "final_quanta": current, "makespan_us": now, "budget": budget}


def insert_required_cooling(timed: TSIR, analysis: Mapping,
                           arch: Architecture, model: CostModel) -> TSIR:
    """Stage 11: insert legal cooling immediately before offending gates.

    Broadcast cooling is selected only if every occupied site permits it;
    otherwise a local operation names precisely the offending ions. All old
    bindings and timestamps are invalidated, including on unchanged commands.
    """
    evidence = _physical_evidence(timed, arch, model)
    marks: dict[int, set[str]] = {}
    for violation in analysis.get("violations", ()):
        marks.setdefault(violation["instruction_id"], set()).add(violation["ion"])
    out = _bare(timed)
    instructions = []
    cool_spec = arch.primitives.scalar("cool")
    for instruction in out:
        if instruction.id in marks:
            pos = evidence["positions"][instruction.id]
            broadcast = bool(cool_spec.get("broadcastable", False)) and all(
                arch.can(site, "cool") for site in pos.values())
            cooling = Instruction(type="cool", id=out.next_id(), broadcast=broadcast,
                                  ions=() if broadcast else tuple(sorted(marks[instruction.id])),
                                  meta={"stage": "cooling_insertion", "trigger": "R7/R7c",
                                        "before_instruction": instruction.id})
            problems = _cooling_diagnostics(cooling, pos, arch)
            if problems:
                raise ValueError("; ".join(d["message"] for d in problems))
            instructions.append(cooling)
        instructions.append(instruction)
    out.instructions = instructions
    return out


def check_cooling(before: TSIR, after: TSIR, arch: Architecture, model: CostModel) -> dict:
    """Check exact surviving bodies/IDs, legal inserted cools, and invalidation."""
    diagnostics = []
    try:
        errors = validate_program(after)
        diagnostics.extend(_diag("invalid_program", e) for e in errors)
        old_ids = {i.id for i in before}
        survivors = [i for i in after if i.id in old_ids]
        if [_body(i) for i in survivors] != [_body(i) for i in before]:
            diagnostics.append(_diag("body_changed", "cooling changed, removed, or reordered existing instructions"))
        if (before.name, before.arch_spec, before.meta) != (after.name, after.arch_spec, after.meta):
            diagnostics.append(_diag("program_changed", "cooling changed program identity or metadata"))
        evidence = _physical_evidence(after, arch, model)
        for instruction in after:
            if any(getattr(instruction, key) is not None for key in ANNOTATIONS):
                diagnostics.append(_diag("stale_annotation", "cooling must invalidate all physical bindings and times",
                                         instruction.id))
            if instruction.id not in old_ids:
                if instruction.type != "cool" or instruction.id < before.id_seq:
                    diagnostics.append(_diag("illegal_insertion", "only fresh cooling instructions may be inserted",
                                             instruction.id))
                else:
                    diagnostics.extend(_cooling_diagnostics(instruction,
                        evidence["positions"][instruction.id], arch))
        if after.metrics:
            diagnostics.append(_diag("stale_metrics", "cooling must invalidate cached program metrics"))
    except (ValueError, TypeError, KeyError, RuntimeError, OverflowError) as exc:
        diagnostics.append(_diag("cooling_error", str(exc)))
    return _checked(diagnostics, ["exact original instruction preservation", "fresh cooling IDs",
                                 "cooling capability and scope", "timing/binding invalidation"],
                    method="physical_stages.check_cooling",
                    assumptions=["Ideal logical preservation assumes the declared cooling primitive does not alter qubits."])


def _record(stage_id, name, before, after, witness, check):
    return {"id": stage_id, "name": name, "input": _snapshot(before),
            "output": _snapshot(after), "witness": copy.deepcopy(witness),
            "check": copy.deepcopy(check)}


def finish_program(program: TSIR, arch: Architecture, model: CostModel,
                   *, max_iterations: int = 4,
                   cancelled: Callable[[], bool] | None = None) -> PhysicalResult:
    """Run 8 -> 9 -> 10, and repeat after independently checked stage 11.

    ``max_iterations`` bounds cooling repair rounds, so at most that many plus
    one thermal analyses run. A failed result never represents a verified
    executable. Inputs are deep-copied, including nested annotation dictionaries.
    ``cancelled`` (for example ``event.is_set``) is polled between stages.
    """
    current = _copy(program)
    records: list[dict] = []
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 0:
        return PhysicalResult(current, False, error="max_iterations must be a nonnegative integer")

    def stop_if_cancelled(stage_id, name, iteration):
        if cancelled is None or not cancelled():
            return None
        check = _checked([_diag("cancelled", "compilation cancelled before this stage")],
                         [], method="physical_stages.cancellation")
        records.append(_record(stage_id, name, current, current,
                               {"iteration": iteration, "cancelled": True}, check))
        return PhysicalResult(current, False, records, iteration, "compilation cancelled")

    for iteration in range(max_iterations + 1):
        stage_id, name = 8, "Operating-Point Binding"
        before = current
        try:
            stopped = stop_if_cancelled(stage_id, name, iteration)
            if stopped is not None:
                return stopped
            current = bind_operating_points(before, arch, model)
            check = check_binding(before, current, arch, model)
            records.append(_record(8, name, before, current,
                                   {"iteration": iteration, "binding_schema": SCHEMA}, check))
            if not check["ok"]:
                return PhysicalResult(current, False, records, iteration + 1, "operating-point binding failed")
            stage_id, name, before = 9, "Event Scheduling", current
            stopped = stop_if_cancelled(stage_id, name, iteration)
            if stopped is not None:
                return stopped
            current = schedule_serial(before)
            check = check_schedule(before, current, arch, model)
            records.append(_record(9, name, before, current,
                                   {"iteration": iteration, "backend": "serial", "policy": SERIAL_LIMIT}, check))
            if not check["ok"]:
                return PhysicalResult(current, False, records, iteration + 1, "event scheduling failed")
            stage_id, name, before = 10, "Thermal Analysis", current
            stopped = stop_if_cancelled(stage_id, name, iteration)
            if stopped is not None:
                return stopped
            thermal = analyze_thermal(current, arch, model)
            records.append(_record(10, name, before, current,
                                   {"iteration": iteration, **{k: v for k, v in thermal.items() if k != "check"}},
                                   thermal["check"]))
            if thermal["check"]["ok"]:
                return PhysicalResult(current, True, records, iteration + 1)
            other = [d for d in thermal["check"]["diagnostics"] if d["code"] not in ("R7", "R7c")]
            if other or not thermal["violations"]:
                return PhysicalResult(current, False, records, iteration + 1, "thermal analysis failed")
            if iteration == max_iterations:
                return PhysicalResult(current, False, records, iteration + 1,
                                      "cooling repair limit reached; thermal budget remains unsatisfied")
            stage_id, name, before = 11, "Cooling Insertion", current
            stopped = stop_if_cancelled(stage_id, name, iteration)
            if stopped is not None:
                return stopped
            current = insert_required_cooling(before, thermal, arch, model)
            check = check_cooling(before, current, arch, model)
            records.append(_record(11, name, before, current,
                                   {"iteration": iteration, "invalidated_stages": [8, 9, 10],
                                    "inserted_ids": [i.id for i in current if i.id not in {j.id for j in before}]},
                                   check))
            if not check["ok"]:
                return PhysicalResult(current, False, records, iteration + 1, "cooling insertion failed")
        except (ValueError, TypeError, KeyError, RuntimeError, OverflowError) as exc:
            check = _checked([_diag("stage_error", str(exc))], [name])
            records.append(_record(stage_id, name, before, current, {"iteration": iteration}, check))
            return PhysicalResult(current, False, records, iteration + 1, f"{name}: {exc}")
    raise AssertionError("bounded physical pipeline must return")
