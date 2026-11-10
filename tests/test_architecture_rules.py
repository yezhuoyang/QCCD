"""Architecture profiles bind human rule lists to physical machines and executable checks."""

from __future__ import annotations

import json
from pathlib import Path

from qccd.api import Machine
from qccd.arch import wise
from qccd.cost.models import corrected_model
from qccd.ir.tsir import Instruction, TSIR, swap_or_stay
from qccd.ruleset import FAILS, check, load
from qccd.ruleset.programme import programme_execution

ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / "qccd/workspace/web/rules-profiles.json").read_text(encoding="utf-8"))


def _verdicts(ruleset: str, machine: Machine, program: TSIR):
    report = check(load(ruleset),
                   programme_execution(program, machine.arch, corrected_model()))
    return {result.id: result.verdict for result in report.results}


def test_profiles_point_to_the_pulled_physical_architectures():
    c2lr = Machine.load(ROOT / "MemoryBoard/c2lr_rep5_mem_basr/c2lr3x3.arch.json")
    assert len(c2lr.arch.device.nodes) == 21
    assert c2lr.arch.control["model"] == "c2lr"
    assert c2lr.arch.control["c2lr"]["shared_signals"] == 27
    assert c2lr.arch.control["c2lr"]["inputs_per_leg"] == 1
    assert c2lr.arch.control["c2lr"]["switches_per_leg"] == 1

    wise_doc = wise.device_doc(3, 3, 3)
    assert len(wise_doc["geometry"]["nodes"]) == 36
    assert wise_doc["geometry"]["params"]["family"] == "wise"
    assert wise_doc["control"]["wiring"]["scheme"] == "wise"
    assert wise_doc["control"]["wiring"]["dacs_dynamic"] == 120

    cyclone = Machine.load(ROOT / "arch/cyclone_base.arch.json")
    assert len(cyclone.arch.device.nodes) == 72
    assert len(cyclone.arch.device.loops) == 1
    assert len(next(iter(cyclone.arch.device.loops.values())).nodes) == 72
    assert cyclone.arch.control["wiring"]["dacs_dynamic"] == 3

    conveyor = Machine.load(ROOT / "arch/h2_racetrack.arch.json")
    assert len(conveyor.arch.device.nodes) == 40
    assert len(conveyor.arch.device.loops) == 1
    assert all(conveyor.arch.device.degree(node) == 2 for node in conveyor.arch.device.nodes)
    assert conveyor.arch.control["wiring"]["dacs_dynamic"] == 3


def test_every_profile_rule_is_in_the_rule_set_the_website_names():
    expected = {
        "c2lr": ("c2lr", {f"C{i}" for i in range(1, 11)}),
        "wise": ("wise", {"W1", "W2", "W4", "W10", "W11"}),
        "cyclone": ("cyclone-deck", {"Y1", "Y2", "Y3", "Y4"}),
        "conveyor": ("conveyor", {"R3", "R4", "R4d", "R5", "R11", "R14", "R22", "R23"}),
    }
    for profile in PROFILES:
        ruleset, focus = expected[profile["id"]]
        assert profile["ruleset"] == ruleset
        assert set(profile["rule_ids"]) == focus
        assert focus <= set(load(ruleset).ids)
    assert load("conveyor").ids == load("standard").ids


def test_c2lr_rules_accept_a_physical_swap_or_stay_step():
    machine = Machine.load(ROOT / "MemoryBoard/c2lr_rep5_mem_basr/c2lr3x3.arch.json")
    program = TSIR("physical C2LR step", str(machine.name))
    program.add(Instruction(
        type="init", id=program.next_id(),
        placement={"q0": "T0_1h", "q1": "T1_1v"},
    ))
    program.add(Instruction(
        type="simd", id=program.next_id(), cls="swap_or_stay", mode="inter",
        template=swap_or_stay("WS", ["q0", "q1"]),
    ))
    verdicts = _verdicts("c2lr", machine, program)
    assert all(verdicts[f"C{i}"] != FAILS for i in range(1, 11)), verdicts


def test_conveyor_keeps_the_general_rules_and_catches_counter_rotation():
    machine = Machine.load(ROOT / "arch/h2_racetrack.arch.json")

    good = machine.program("rigid shift", provenance="off").fill().rotate(+1).build()
    good_verdicts = _verdicts("conveyor", machine, good)
    assert all(good_verdicts[rid] != FAILS
               for rid in ("R3", "R4", "R4d", "R5", "R11", "R14", "R22", "R23"))

    bad = machine.program("counter rotation", provenance="off")
    bad.init({"a": "S0", "b": "S20"})
    bad.simd("shuttle", [["a", "S0", "S1"], ["b", "S20", "S19"]])
    bad_verdicts = _verdicts("conveyor", machine, bad.build())
    assert {rid for rid, verdict in bad_verdicts.items() if verdict == FAILS} >= {
        "R4d", "R11", "R22",
    }
