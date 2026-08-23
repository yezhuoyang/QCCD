"""Negative tests for the rules.  A check that cannot fail is not a check.

Every rule the verifier reports as *passing* on the shipped schedule is exercised here
against a program built to break it, so that a green R-something means the check ran and
found nothing rather than that it was never able to fire.

The programs are tiny and hand-written on purpose: a rule violation should be obvious
from reading the fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture, SCHEMA_VERSION  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.ir import TSIR, Instruction, Participant, loop_shift, validate_program  # noqa: E402
from qccd.verify import ReplayError, replay, rule_statements, verify  # noqa: E402

# --------------------------------------------------------------------------- fixture

PRIMS = {
    "shuttle_segment": {"curve": [{"us": 5, "quanta": 0.1, "table": "qccdsim_jones"}]},
    "junction_cross": {
        "curve_by_degree": {
            "3": [{"us": 100, "quanta": 3.0, "table": "qccdsim_jones"}],
            "4": [{"us": 120, "quanta": 3.0, "table": "qccdsim_jones"}],
        }
    },
    "split": {"curve": [{"us": 80, "quanta": 6.0, "table": "qccdsim_jones"}]},
    "merge": {"curve": [{"us": 80, "quanta": 6.0, "table": "qccdsim_jones"}]},
    "ms_gate": {"us": 25, "fidelity_at_n0": 0.998, "error_vs_quanta": "linear:2.0e-3",
                "max_quanta": 1.0},
    "gate_swap": {"gates": 3},
    "measure": {"us": 120},
    "reset": {"us": 50},
    "cool": {"us": 300, "removes_quanta": "all", "broadcastable": True},
}

ZONES = {
    "gatezone": {"capacity": 2, "gate": True, "spam": True, "cool": True},
    "quiet": {"capacity": 2, "gate": False, "spam": False, "cool": True},
    "big": {"capacity": 32, "gate": True, "spam": True, "cool": True},
}


def tiny_ring(verticals: int = 2, zone: str = "gatezone") -> Architecture:
    """An 8-slot ring with `verticals` dock spurs.  Docks are S0 and S4."""
    return Architecture.from_json(
        {
            "name": "tiny_ring",
            "schema_version": SCHEMA_VERSION,
            "geometry": {
                "generator": "ring",
                "params": {"width": 4, "height": 2, "verticals": verticals,
                           "site_zone": zone, "ancilla_zone": "gatezone"},
            },
            "zone_types": ZONES,
            "primitives": PRIMS,
            "control": {
                "model": "simd_classes",
                "max_simd_classes_per_cycle": 1,
                "classes": {
                    "generator": "x_junction_grid",
                    "count": 18,
                    "extra": [
                        {"id": "rotate_cw", "type": "shift", "orbit": "L0", "delta": 1},
                        {"id": "rotate_ccw", "type": "shift", "orbit": "L0", "delta": -1},
                        {"id": "dock", "type": "shift", "orbit": "spurs",
                         "entails": ["split", "merge"]},
                        {"id": "undock", "type": "shift", "orbit": "spurs",
                         "entails": ["split", "merge"]},
                        {"id": "nudge", "type": "shift", "orbit": "L0"},
                    ],
                },
            },
            "heating": {"anomalous_rate_quanta_per_ms": 0.0},
            "species": {"T_coh_s": 600},
        }
    )


def prog_of(*instrs: Instruction, name: str = "t") -> TSIR:
    return TSIR(name=name, arch_spec="inline", instructions=list(instrs))


def init(**placement) -> Instruction:
    return Instruction(type="init", id=0, placement=placement,
                       quanta={k: 0.0 for k in placement})


def fired(report, rule: str) -> list[str]:
    return [str(v) for v in report.rules.violations if v.rule == rule]


def run(prog, arch, model=None):
    return verify(prog, arch, model or deck_model(), check_metrics=False)


# --------------------------------------------------------------------------- sanity


def test_a_legal_program_fires_nothing():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1)),
    )
    report = run(prog, arch)
    assert report.rules.ok(), report.rules.summary()


def test_every_rule_the_verifier_claims_has_a_statement_and_a_source():
    stmts = rule_statements()
    for rule in ("R1", "R2", "R3", "R4", "R4b", "R5", "R6", "R6b", "R7", "R7b",
                 "R7c", "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15",
                 "R16", "R17", "R18"):
        assert stmts[rule]["statement"]
        assert stmts[rule]["sources"]


# --------------------------------------------------------------------------- R1


def test_r1_fires_when_a_site_overflows():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2", d2="S3"),
        # three ions converge on S2: capacity is 2
        Instruction(
            type="simd", id=1, cls="nudge", mode="inter",
            participants=(
                Participant("d0", "S1", "S2"),
                Participant("d2", "S3", "S2"),
            ),
        ),
    )
    report = run(prog, arch)
    assert fired(report, "R1")


# --------------------------------------------------------------------------- R2


def test_r2_fires_when_two_ions_share_a_junction():
    arch = tiny_ring()
    assert arch.device.degree("S0") == 3
    prog = prog_of(
        init(d0="S0", d1="S1"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d1", "S1", "S0"),)),
    )
    report = run(prog, arch)
    assert fired(report, "R2")


def test_r2_fires_when_two_ions_cross_one_junction_in_a_cycle():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="A0"),
        Instruction(
            type="simd", id=1, cls="nudge", mode="inter",
            participants=(
                Participant("d0", "S1", "S0"),
                Participant("d1", "A0", "S0"),
            ),
        ),
    )
    report = run(prog, arch)
    assert fired(report, "R2")


# --------------------------------------------------------------------------- R3


def test_r3_fires_when_a_segment_carries_two_ions():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(
            type="simd", id=1, cls="nudge", mode="inter",
            participants=(
                Participant("d0", "S1", "S2"),
                Participant("d1", "S2", "S1"),
            ),
        ),
    )
    report = run(prog, arch)
    assert fired(report, "R3")


# --------------------------------------------------------------------------- R4


def test_r4_fires_on_an_undeclared_movement_class():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="teleport", mode="inter",
                    participants=(Participant("d0", "S1", "S2"),)),
    )
    report = run(prog, arch)
    assert fired(report, "R4")


# --------------------------------------------------------------------------- R4b


def test_r4b_fires_when_a_cycle_mixes_transport_and_gates():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter", gate="CX",
                    pairs=(("d0", "d1"),),
                    participants=(Participant("d0", "S1", "S0"),)),
    )
    report = run(prog, arch)
    assert fired(report, "R4b")


def test_r4b_fires_on_a_missing_mode():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="nudge",
                    participants=(Participant("d0", "S1", "S2"),)),
    )
    report = run(prog, arch)
    assert fired(report, "R4b")


# --------------------------------------------------------------------------- R5


def test_r5_fires_when_two_ions_exchange_across_one_segment():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(
            type="simd", id=1, cls="nudge", mode="inter",
            participants=(
                Participant("d0", "S1", "S2"),
                Participant("d1", "S2", "S1"),
            ),
        ),
    )
    report = run(prog, arch)
    assert fired(report, "R5")


# --------------------------------------------------------------------------- R6


def test_r6_fires_on_a_gate_in_a_zone_that_cannot_gate():
    arch = tiny_ring(zone="quiet")
    prog = prog_of(
        init(d0="S1", d1="S1"),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "d1"),), sites=("S1",)),
    )
    report = run(prog, arch)
    assert fired(report, "R6")


# --------------------------------------------------------------------------- R6b


def test_r6b_fires_when_the_pair_is_not_co_located():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "d1"),), sites=("S1",)),
    )
    report = run(prog, arch)
    assert fired(report, "R6b")


# --------------------------------------------------------------------------- R7


def test_r7_fires_when_a_gate_is_too_hot_and_cooling_clears_it():
    arch = tiny_ring()
    model = corrected_model()
    hot = prog_of(
        init(d0="S1", d1="S2"),
        # one junction crossing is 3.0 quanta against a 1.0 budget
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S1", "S0"),)),
        Instruction(type="simd", id=2, cls="nudge", mode="inter",
                    participants=(Participant("d1", "S2", "S1"),)),
        Instruction(type="simd", id=3, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S0", "S1"),)),
        Instruction(type="gate", id=4, gate="CX", mode="intra",
                    pairs=(("d0", "d1"),), sites=("S1",)),
    )
    report = verify(hot, arch, model, check_metrics=False)
    assert fired(report, "R7")

    cooled = prog_of(*hot.instructions[:-1],
                     Instruction(type="cool", id=90, broadcast=True),
                     hot.instructions[-1])
    report2 = verify(cooled, arch, model, check_metrics=False)
    assert not fired(report2, "R7")


def test_r7_does_not_fire_under_a_model_that_tracks_no_heating():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S1", "S0"),)),
        Instruction(type="simd", id=2, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S0", "S1"),)),
        Instruction(type="gate", id=3, gate="CX", mode="intra",
                    pairs=(("d0", "d1"),), sites=("S1",)),
    )
    report = run(prog, arch)
    assert not fired(report, "R7")
    assert "R7" in report.rules.passed()


# --------------------------------------------------------------------------- R8


def test_r8_fires_when_one_ion_participates_twice_in_a_cycle():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(
            type="simd", id=1, cls="nudge", mode="inter",
            participants=(
                Participant("d0", "S1", "S2"),
                Participant("d0", "S1", "S0"),
            ),
        ),
    )
    report = run(prog, arch)
    assert fired(report, "R8")
    # and the state stays coherent while the rule reports it: no ion is lost, no
    # occupancy counter goes negative
    res = replay(prog, arch, deck_model(), check_rules=False)
    assert set(res.final_positions) == {"d0"}


# --------------------------------------------------------------------------- R9


def test_r9_fires_on_a_false_claim():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1), cost=999.0, steps=42),
    )
    prog.metrics = {"total_cost": 12345, "total_steps": 7}
    report = verify(prog, arch, deck_model())
    msgs = fired(report, "R9")
    assert any("total_cost" in m for m in msgs)
    assert any("total_steps" in m for m in msgs)
    assert any("cost: claimed 999" in m for m in msgs)


def test_r9_is_skipped_rather_than_failed_under_a_different_model():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1)),
    )
    prog.metrics = {"total_cost": 1.0}
    prog.meta = {"metrics_model": "deck"}
    assert verify(prog, arch, deck_model()).rules.ok()
    corrected = verify(prog, arch, corrected_model())
    assert not fired(corrected, "R9")
    assert "R9" in corrected.rules.skipped


# --------------------------------------------------------------------------- R11


def test_r11_fires_when_a_cycle_moves_both_ways_along_one_loop():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1", d1="S5"),
        Instruction(
            type="simd", id=1, cls="nudge", mode="inter",
            participants=(
                Participant("d0", "S1", "S2"),
                Participant("d1", "S5", "S4"),
            ),
        ),
    )
    report = run(prog, arch)
    assert fired(report, "R11")


def test_r11_fires_when_the_architecture_cannot_price_a_degree():
    """A degree-4 node with no degree-4 junction curve is an unpriceable junction."""
    prims = {k: v for k, v in PRIMS.items()}
    prims["junction_cross"] = {
        "curve_by_degree": {"3": [{"us": 100, "quanta": 3.0, "table": "qccdsim_jones"}]}
    }
    doc = {
        "name": "unpriced",
        "schema_version": SCHEMA_VERSION,
        "geometry": {"generator": "grid", "params": {"a": 3, "b": 3, "site_zone": "gatezone"}},
        "zone_types": ZONES,
        "primitives": prims,
    }
    arch = Architecture.from_json(doc)
    assert 4 in {arch.device.degree(n) for n in arch.device.junction_nodes}
    prog = prog_of(init(d0="T0_0h"))
    report = run(prog, arch)
    assert any("degree 4" in m for m in fired(report, "R11"))


# --------------------------------------------------------------------------- R12


def test_r12_fires_on_two_gates_in_one_trap():
    arch = tiny_ring(zone="big")
    prog = prog_of(
        init(d0="S1", d1="S1", d2="S1", d3="S1"),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "d1"), ("d2", "d3")), sites=("S1",)),
    )
    report = run(prog, arch)
    assert fired(report, "R12")


# --------------------------------------------------------------------------- R13


def test_r13_fires_on_a_gate_in_a_long_chain():
    arch = tiny_ring(zone="big")
    ions = {f"d{i}": "S1" for i in range(17)}
    prog = prog_of(
        init(**ions),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "d1"),), sites=("S1",)),
    )
    report = run(prog, arch)
    assert fired(report, "R13")


# --------------------------------------------------------------------------- R14


def test_r14_fires_when_an_ion_splits_out_of_a_crowded_trap():
    arch = tiny_ring(zone="big")
    prog = prog_of(
        init(d0="S0", d1="S0", d2="S0"),
        Instruction(type="simd", id=1, cls="dock", mode="inter",
                    participants=(Participant("d0", "S0", "A0", via=("V0",)),)),
    )
    report = run(prog, arch)
    assert fired(report, "R14")


def test_r14_is_silent_at_capacity_two_where_every_ion_is_at_an_edge():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S0", a0="A0"),
        Instruction(type="simd", id=1, cls="dock", mode="inter",
                    participants=(Participant("d0", "S0", "A0", via=("V0",)),)),
    )
    report = run(prog, arch)
    assert not fired(report, "R14")


# --------------------------------------------------------------------------- misc


def test_replay_rejects_an_ion_that_is_not_where_the_instruction_says():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S3", "S4"),)),
    )
    with pytest.raises(ReplayError, match="declared at"):
        replay(prog, arch, deck_model())


def test_replay_rejects_a_via_that_does_not_reach_the_declared_destination():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S0"),
        Instruction(type="simd", id=1, cls="dock", mode="inter",
                    participants=(Participant("d0", "S0", "S1", via=("V0",)),)),
    )
    with pytest.raises(ReplayError, match="via"):
        replay(prog, arch, deck_model())


def test_program_validation_catches_shape_errors():
    prog = prog_of(
        Instruction(type="simd", id=0, participants=()),
        Instruction(type="gate", id=0, ions=("a",)),
    )
    errors = validate_program(prog)
    assert any("must open with `init`" in e for e in errors)
    assert any("duplicate instruction id" in e for e in errors)
    assert any("carries no class" in e for e in errors)
    assert any("neither participants nor a template" in e for e in errors)
    assert any("`pairs` or exactly two `ions`" in e for e in errors)


def test_unknown_template_kind_is_rejected():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template={"kind": "wiggle", "loop": "L0", "delta": 1}),
    )
    with pytest.raises(ReplayError, match="template kind"):
        replay(prog, arch, deck_model())


def test_multi_hop_template_decomposes_into_unit_cycles():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S0"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 3)),
    )
    res = replay(prog, arch, deck_model(), check_rules=False)
    assert res.final_positions["d0"] == "S3"
    assert res.hops_by_class["rotate_cw"] == 3
    assert len([c for c in res.cycles if c.type == "simd"]) == 3
