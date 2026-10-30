"""Fault-injection checks for the physical stage boundaries, not UI snapshots."""

from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qccd.arch import Architecture
from qccd.compile.physical_stages import (
    analyze_thermal, bind_operating_points, check_binding, check_cooling,
    check_schedule, finish_program, insert_required_cooling, schedule_serial,
)
from qccd.cost import corrected_model, deck_model
from qccd.ir import TSIR, Instruction, Participant
from qccd.verify.replay import replay
from qccd.verify import verify
from test_rules import tiny_ring


def architecture(*, rate=0.0, cool=True, broadcast=True):
    data = copy.deepcopy(tiny_ring().to_json(expanded=True))
    data["heating"]["anomalous_rate_quanta_per_ms"] = rate
    data["zone_types"]["gatezone"]["cool"] = cool
    data["primitives"]["cool"]["broadcastable"] = broadcast
    data["primitives"]["1q_gate"] = {"us": 5}
    return Architecture.from_json(data)


def program(*instructions, quanta=0.0, spectators=False):
    placement = {"q0": "A0", "q1": "A0"}
    if spectators:
        placement["q2"] = "S2"
    initial = Instruction(type="init", id=0, placement=placement,
                          quanta={ion: quanta for ion in placement})
    all_instructions = [initial, *instructions]
    return TSIR("physical-test", "inline", all_instructions,
                id_seq=max(i.id for i in all_instructions) + 1)


def gate(iid=1):
    return Instruction(type="gate", id=iid, gate="MS", pairs=(("q0", "q1"),),
                       sites=("A0",), meta={"op": [iid]})


def cooled_gate(*, rate=0.0):
    arch = architecture(rate=rate)
    prog = program(Instruction(type="cool", id=1, broadcast=True), gate(2))
    bound = bind_operating_points(prog, arch, corrected_model())
    return arch, prog, bound, schedule_serial(bound)


def codes(check):
    return {d["code"] for d in check["diagnostics"]}


def test_repair_rebinds_reschedules_and_preserves_inputs_and_instruction_ids():
    arch = architecture(rate=0.1)
    source = program(gate(), quanta=3.0)
    snapshot = copy.deepcopy(source.to_json())
    result = finish_program(source, arch, corrected_model())
    assert result.ok, result.error
    assert [r["id"] for r in result.stages] == [8, 9, 10, 11, 8, 9, 10]
    assert result.stages[2]["check"]["status"] == "failed"
    assert result.stages[-1]["check"]["status"] == "passed"
    assert all(r["check"]["method"] for r in result.stages)
    assert result.program.id_seq == 3
    assert [i.id for i in result.program] == [0, 2, 1]
    assert result.program.instructions[-1].t0 == 300
    assert source.to_json() == snapshot
    # Stored evidence is a snapshot, not a live alias of its output program.
    result.program.instructions[-1].meta["op"].append(999)
    assert result.stages[-1]["output"]["instructions"][-1]["meta"]["op"] == [1]
    assert source.to_json() == snapshot


def test_mandatory_cooling_is_checked_even_when_r7_is_safe():
    arch = architecture()
    source = program(gate())
    bound = bind_operating_points(source, arch, corrected_model())
    thermal = analyze_thermal(schedule_serial(bound), arch, corrected_model())
    assert codes(thermal["check"]) == {"R7c"}
    result = finish_program(source, arch, corrected_model())
    assert result.ok
    assert len(result.program.of_type("cool")) == 1


@pytest.mark.parametrize("field,value", [
    ("duration_us", 0.0), ("intrinsic_quanta", {"q0": 999.0}),
    ("architecture_sha256", "forged"), ("resources", []),
    ("anomalous_per_us", -1.0),
])
def test_binding_checker_rejects_forged_physical_annotations(field, value):
    arch, source, bound, _ = cooled_gate()
    changed = copy.deepcopy(bound)
    changed.instructions[-1].operating_point[field] = value
    assert not check_binding(source, changed, arch, corrected_model())["ok"]


def test_binding_is_pinned_to_the_architecture_and_selected_model():
    arch, source, bound, _ = cooled_gate()
    changed_model = corrected_model(include_anomalous=False)
    assert "binding_mismatch" in codes(check_binding(source, bound, arch, changed_model))
    changed_arch = architecture(rate=1.0)
    assert "binding_mismatch" in codes(check_binding(source, bound, changed_arch, corrected_model()))


def test_binding_checker_rejects_changed_gate_operands_and_parameters():
    arch, source, bound, _ = cooled_gate()
    changed = copy.deepcopy(bound)
    changed.instructions[-1] = replace(changed.instructions[-1], params=((0.3,),))
    assert "body_changed" in codes(check_binding(source, changed, arch, corrected_model()))


def test_occupancy_dependent_gate_duration_is_bound_from_replayed_state():
    data = architecture().to_json(expanded=True)
    data["primitives"]["ms_gate"]["us_vs_chain"] = "linear:2"
    data["zone_types"]["gatezone"]["capacity"] = 4
    arch = Architecture.from_json(data)
    source = program(gate())
    source.instructions[0] = replace(source.instructions[0],
                                     placement={"q0": "A0", "q1": "A0", "q2": "A0", "q3": "A0"})
    bound = bind_operating_points(source, arch, corrected_model())
    assert bound.instructions[-1].operating_point["duration_us"] == 50


def test_single_qubit_resources_include_the_ion_and_occupied_site():
    arch = architecture()
    source = program(Instruction(type="gate", id=1, gate="R", arity=1,
                                 ions=("q0",), params=((0.1, 0.2),)))
    bound = bind_operating_points(source, arch, corrected_model())
    resources = bound.instructions[-1].operating_point["resources"]
    assert "ion:q0" in resources and "A0" in resources


@pytest.mark.parametrize("change,expected", [
    ({"t0": 200.0, "t1": 225.0}, "unsupported_overlap"),
    ({"t0": 300.0, "t1": 301.0}, "duration_mismatch"),
    ({"t0": float("nan"), "t1": 325.0}, "invalid_time"),
    ({"t0": -1.0, "t1": 24.0}, "invalid_time"),
])
def test_schedule_checker_rejects_invalid_intervals(change, expected):
    arch, _, bound, timed = cooled_gate()
    timed.instructions[-1] = replace(timed.instructions[-1], **change)
    assert expected in codes(check_schedule(bound, timed, arch, corrected_model()))
    thermal = analyze_thermal(timed, arch, corrected_model())
    assert not thermal["check"]["ok"] and thermal["trace"] == []


def test_thermal_check_consumes_real_idle_gaps_which_list_replay_ignores():
    arch, _, bound, timed = cooled_gate(rate=0.2)
    timed.instructions[-1] = replace(timed.instructions[-1], t0=10300.0, t1=10325.0)
    assert check_schedule(bound, timed, arch, corrected_model())["ok"]
    analysis = analyze_thermal(timed, arch, corrected_model())
    assert "R7" in codes(analysis["check"])
    assert analysis["trace"][-1]["at_start"]["q0"] == pytest.approx(2.0)
    assert analysis["makespan_us"] == 10325
    assert replay(timed, arch, corrected_model(), check_rules=False).max_gate_quanta_seen == 0


def test_intrinsic_transport_heating_reaches_thermal_trace():
    arch = architecture()
    source = program(Instruction(type="simd", id=1, cls="nudge", mode="inter",
                                 participants=(Participant("q2", "S2", "S3"),)), spectators=True)
    result = finish_program(source, arch, corrected_model())
    assert result.ok
    assert result.stages[-1]["witness"]["final_quanta"]["q2"] == pytest.approx(0.1)
    assert not result.program.of_type("cool")  # no two-qubit program, no R7c trigger


def test_non_broadcastable_cooling_uses_explicit_local_scope():
    arch = architecture(broadcast=False)
    result = finish_program(program(gate(), quanta=3), arch, corrected_model())
    assert result.ok, result.error
    cooling = result.program.of_type("cool")[0]
    assert not cooling.broadcast and cooling.ions == ("q0", "q1")


def test_cooling_without_site_capability_fails_closed():
    result = finish_program(program(gate(), quanta=3), architecture(cool=False), corrected_model())
    assert not result.ok
    assert "cooling-capable" in result.error
    assert result.stages[-1]["id"] == 11


def test_unsupported_cooling_reset_model_is_not_assumed():
    arch = architecture()
    arch.primitives.scalars["cool"].pop("removes_quanta")
    result = finish_program(program(gate()), arch, corrected_model())
    assert not result.ok
    assert "removes_quanta" in result.error


def test_cooling_checker_catches_changed_survivor_or_stale_annotations():
    arch = architecture()
    timed = schedule_serial(bind_operating_points(program(gate(), quanta=3), arch, corrected_model()))
    thermal = analyze_thermal(timed, arch, corrected_model())
    cooled = insert_required_cooling(timed, thermal, arch, corrected_model())
    assert check_cooling(timed, cooled, arch, corrected_model())["ok"]
    assert all(i.t0 is None and i.operating_point is None for i in cooled)
    changed = copy.deepcopy(cooled)
    changed.instructions[-1] = replace(changed.instructions[-1], gate="SWAP", t0=5.0)
    check = check_cooling(timed, changed, arch, corrected_model())
    assert {"body_changed", "stale_annotation"} <= codes(check)


def test_cooling_checker_rejects_non_cooling_insertions_and_reused_ids():
    arch = architecture()
    source = program(gate())
    changed = copy.deepcopy(source)
    changed.instructions.insert(1, Instruction(type="reset", id=changed.next_id(), ions=("q0",)))
    assert "illegal_insertion" in codes(check_cooling(source, changed, arch, corrected_model()))
    changed.instructions.insert(1, Instruction(type="cool", id=1, broadcast=True))
    assert not check_cooling(source, changed, arch, corrected_model())["ok"]


def test_repair_bound_reports_unsatisfied_budgets_not_success():
    source = program(gate(), quanta=10)
    result = finish_program(source, architecture(), corrected_model(), max_iterations=0)
    assert not result.ok and result.iterations == 1
    assert "limit" in result.error
    assert [r["id"] for r in result.stages] == [8, 9, 10]


def test_no_time_model_and_nonfinite_initial_heat_fail_closed():
    source = program(gate())
    assert not finish_program(source, architecture(), deck_model()).ok
    source.instructions[0] = replace(source.instructions[0], quanta={"q0": float("nan")})
    result = finish_program(source, architecture(), corrected_model())
    assert not result.ok
    assert "finite" in result.error


def test_cooling_duration_heats_spectators_and_resets_targets_at_completion():
    arch = architecture(rate=1.0, broadcast=False)
    source = program(Instruction(type="cool", id=1, ions=("q0", "q1")),
                     gate(2), spectators=True)
    result = finish_program(source, arch, corrected_model())
    assert result.ok
    trace = result.stages[-1]["witness"]["trace"]
    assert trace[1]["at_end"]["q0"] == 0
    assert trace[1]["at_end"]["q2"] == pytest.approx(0.3)
    assert trace[2]["at_start"]["q2"] == pytest.approx(0.3)
    assert trace[2]["at_end"]["q0"] == pytest.approx(0.025)


def test_zero_duration_native_frame_instruction_is_preserved():
    data = architecture().to_json(expanded=True)
    data["primitives"]["z_gate"] = {"us": 0.0}
    arch = Architecture.from_json(data)
    source = program(Instruction(type="gate", id=1, gate="VZ", arity=1,
                                 ions=("q0",), params=((0.8,),)))
    result = finish_program(source, arch, corrected_model())
    assert result.ok
    assert result.program.instructions[-1].gate == "VZ"
    assert result.program.instructions[-1].params == ((0.8,),)
    assert result.program.instructions[-1].t0 == result.program.instructions[-1].t1 == 0


def test_ordinary_r9_replay_agrees_on_emitted_duration_cost_and_depth():
    arch = architecture(rate=0.1)
    source = program(Instruction(type="simd", id=1, cls="nudge", mode="inter",
                                 participants=(Participant("q2", "S2", "S3"),)),
                     gate(2), spectators=True)
    result = finish_program(source, arch, corrected_model())
    assert result.ok
    report = verify(result.program, arch, corrected_model(), only_rules=["R7", "R7c", "R9"])
    assert report.ok(), report.rules.violations
    # The legacy R9 checker explicitly delegates these new claims; binding's
    # independent checker covers both instead of relabeling legacy omissions.
    assert report.metrics["not_checked"] == ["quanta_delta", "operating_point"]
    assert check_binding(source, bind_operating_points(source, arch, corrected_model()),
                         arch, corrected_model())["ok"]


def test_cancellation_is_checked_between_stages_without_mutating_source():
    source = program(gate())
    snapshot = copy.deepcopy(source.to_json())
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) == 2

    result = finish_program(source, architecture(), corrected_model(), cancelled=cancelled)
    assert not result.ok and result.error == "compilation cancelled"
    assert [r["id"] for r in result.stages] == [8, 9]
    assert result.stages[-1]["witness"]["cancelled"]
    assert source.to_json() == snapshot
