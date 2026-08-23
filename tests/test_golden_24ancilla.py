"""The golden test: M1's external oracle, locked.

`visualizer_24_ancillas_24_junctions_standalone.html` was produced by a different program
with a different implementation of the same physics.  If our replay reproduces its
397 184 / 8 808 *and* the position of every one of its 864 contacts, the replay engine is
validated against something outside this repository.  Nothing downstream is trusted until
this passes (PLAN §10).

The numbers below are hard-coded on purpose.  They are not "what our code currently
computes"; they are what the artifact says, and a change that moves them is a regression
until proved otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture, load  # noqa: E402
from qccd.compile import CoolingPolicy, insert_cooling  # noqa: E402
from qccd.cost import corrected_model, deck_model, t1_metrics, t2_metrics  # noqa: E402
from qccd.ir import TSIR, completeness_report, extract_inline_data, import_schedule  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402

HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"
ARCH = ROOT / "arch" / "ring144_24v.arch.json"

#: The 24-ancilla schedule is imported from a standalone visualizer HTML that is
#: THIRD-PARTY SOURCE MATERIAL and is deliberately not tracked in this repository.
#: Without it these tests cannot run at all, so they skip by name rather than fail --
#: a clone that is missing a private input should say so, not report a broken suite.
pytestmark = pytest.mark.skipif(
    not HTML.exists(),
    reason=f"{HTML.name} is not present; it is third-party material and untracked")

# ---- the artifact's own figures, quoted ------------------------------------
GOLDEN_COST = 397184
GOLDEN_STEPS = 8808
GOLDEN_HOPS = 2672
GOLDEN_BATCHES = 396
GOLDEN_CONTACTS = 864
GOLDEN_CHECKS = 144
GOLDEN_MEMBERS_PER_CHECK = 6
GOLDEN_CONTACT_LIMIT = 24
HOP_COST = 148
HOP_STEPS = 3
DOCK_COST_PER_CONTACT = 2
DOCK_STEPS_PER_BATCH = 2


@pytest.fixture(scope="module")
def arch() -> Architecture:
    return load(ARCH)


@pytest.fixture(scope="module")
def data() -> dict:
    return extract_inline_data(HTML)


@pytest.fixture(scope="module")
def prog(arch, data) -> TSIR:
    return import_schedule(arch, data)


@pytest.fixture(scope="module")
def deck_run(prog, arch):
    return verify(prog, arch, deck_model())


# --------------------------------------------------------------------------- M1


def test_inline_data_is_where_the_plan_says(data):
    assert data["_provenance"]["line"] == 344
    assert data["geometries"][0]["label"] == "W72_H2"


def test_hop_cost_is_derived_from_the_graph_not_hardcoded(arch):
    """148 and 3 must fall out of the expanded ring, not out of a constant."""
    dev = arch.device
    loop = next(iter(dev.loops))
    segs = dev.loop_segments(loop)
    ce = dev.corner_endpoints
    per_hop = [3 if ce[s.id] == 2 else 1 for s in segs]
    assert len(segs) == 144
    assert sum(1 for s in segs if ce[s.id] == 2) == 2, "the two end-caps are the corner segments"
    assert sum(per_hop) == HOP_COST
    assert max(per_hop) == HOP_STEPS


def test_totals_reproduce_exactly(deck_run):
    res = deck_run.result
    assert res.total_cost == GOLDEN_COST
    assert res.total_steps == GOLDEN_STEPS


def test_totals_decompose_the_way_the_deck_says(deck_run, prog):
    res = deck_run.result
    hops = sum(v for k, v in res.hops_by_class.items() if k.startswith("rotate"))
    assert hops == GOLDEN_HOPS
    assert res.n_gate_pairs == GOLDEN_CONTACTS
    assert hops * HOP_COST + GOLDEN_CONTACTS * DOCK_COST_PER_CONTACT == GOLDEN_COST
    assert hops * HOP_STEPS + GOLDEN_BATCHES * DOCK_STEPS_PER_BATCH == GOLDEN_STEPS


def test_batch_and_contact_counts(deck_run, prog, arch):
    t1 = t1_metrics(prog, arch, deck_run.result)
    assert t1.n_batches == GOLDEN_BATCHES
    assert t1.n_contacts == GOLDEN_CONTACTS
    assert t1.batch_size_histogram == {1: 144, 2: 144, 4: 108}


def test_contact_batch_utilization(deck_run, prog, arch):
    t1 = t1_metrics(prog, arch, deck_run.result)
    assert t1.contact_batch_limit == GOLDEN_CONTACT_LIMIT
    assert t1.contact_batch_utilization == pytest.approx(2.18, abs=0.005)
    pct = 100 * t1.contact_batch_utilization / t1.contact_batch_limit
    assert pct == pytest.approx(9.1, abs=0.05)


def test_every_check_gets_all_six_members(prog):
    comp = completeness_report(prog)
    assert comp["checks_declared"] == GOLDEN_CHECKS
    assert comp["checks_complete_6_of_6"] == GOLDEN_CHECKS
    assert comp["contacts_total"] == GOLDEN_CONTACTS
    assert comp["contacts_per_data_ion"] == [GOLDEN_MEMBERS_PER_CHECK]
    assert comp["complete"]


def test_every_contact_happens_where_the_artifact_says(prog, arch, data):
    """The strong half of the oracle.

    Totals can agree by coincidence; 864 recomputed positions cannot.  Positions are
    re-derived here from the initial order and the rotation history alone -- no replay,
    no importer -- and compared against the dock slot the artifact recorded for every
    contact.  The replay then has to agree with both, since it raises if a docking
    participant is not where the instruction says it is.
    """
    geom = data["geometries"][0]
    capacity = geom["capacity"]
    slot_of = {label: i for i, label in enumerate(geom["initial_order"])}
    rotation = 0
    checked = 0
    for op in geom["operations"]:
        rotation += op["hops"] if op["direction"] == "cw" else -op["hops"]
        for c in op["contacts"]:
            assert (slot_of[c["member"]] + rotation) % capacity == c["dock_slot"], (
                f"{c['member']} is not at dock slot {c['dock_slot']} for {c['check']}"
            )
            checked += 1
    assert checked == GOLDEN_CONTACTS

    res = replay(prog, arch, deck_model(), check_rules=False)
    assert res.n_gate_pairs == GOLDEN_CONTACTS
    # and the ions end where a pure rigid rotation puts them
    for label, slot in slot_of.items():
        assert res.final_positions[f"d{label}"] == f"S{(slot + rotation) % capacity}"


def test_rules_r1_to_r14_pass_under_the_deck_model(deck_run):
    should_pass = ["R1", "R2", "R3", "R4", "R4b", "R5", "R6", "R6b",
                   "R7", "R8", "R9", "R11", "R12", "R13", "R14"]
    passed = set(deck_run.rules.passed())
    missing = [r for r in should_pass if r not in passed]
    assert not missing, f"{missing} did not pass: {deck_run.rules.by_rule()}"
    assert deck_run.rules.ok()


def test_r9_checks_every_batch_and_every_annotation(deck_run):
    assert deck_run.metrics["batches_checked"] == GOLDEN_BATCHES
    assert deck_run.metrics["batches_mismatched"] == 0
    assert deck_run.metrics["instructions_mismatched"] == 0


def test_tsir_round_trips(prog, tmp_path):
    path = prog.save(tmp_path / "deck24.tsir.json")
    again = TSIR.load(path)
    assert len(again) == len(prog)
    assert again.metrics == prog.metrics
    assert [i.to_json() for i in again] == [i.to_json() for i in prog]


def test_the_schedule_uses_four_movement_templates(prog):
    """PLAN §1's quantity.  Rigid rotation needs one template per direction, plus dock
    and undock -- four in total, independent of code size.  An odd-even sort needs many.
    """
    t = prog.templates()
    assert set(t) == {"loop_shift:L0:+1", "loop_shift:L0:-1", "class:dock", "class:undock"}
    assert t["loop_shift:L0:+1"] + t["loop_shift:L0:-1"] == GOLDEN_HOPS


# --------------------------------------------------------------------------- M2

PLAN_TRANSITS = 445
PLAN_SHUTTLE = 267
PLAN_JUNCTION = 1336
PLAN_DOCKING = 144
PLAN_TOTAL = 1747
PLAN_ROTATION_MS = 267
PLAN_COUNTERFACTUAL_MS = 13.4


@pytest.fixture(scope="module")
def corrected_run(prog, arch):
    return replay(prog, arch, corrected_model(), check_rules=False)


def _data_ions(res):
    return sorted(i for i in res.per_ion_quanta if i.startswith("d"))


def test_junction_transits_per_data_ion(corrected_run, prog, arch):
    res = corrected_run
    ions = _data_ions(res)
    dev = arch.device
    undock = sum(
        1
        for instr in prog.instructions
        if instr.type == "simd" and instr.cls == "undock"
        for p in instr.participants
        if dev.degree(p.dst) >= 3
    ) / len(ions)
    total = sum(res.junction_transits[i] for i in ions) / len(ions)
    rotation_only = total - undock
    # 2672 / 144 = 18.56 revolutions x 24 verticals
    assert rotation_only == pytest.approx(GOLDEN_HOPS / 144 * 24, rel=1e-12)
    assert round(rotation_only) == PLAN_TRANSITS
    assert undock == GOLDEN_MEMBERS_PER_CHECK  # one per contact, on the way back out


def test_quanta_budget_components(corrected_run, arch):
    res = corrected_run
    ions = _data_ions(res)
    n = len(ions)
    shuttle_q = arch.primitives.curve("shuttle_segment").pick(
        corrected_model().policy
    ).quanta
    junction_q = corrected_model().junction_point(arch, 3).quanta

    rot_shuttle = GOLDEN_HOPS * shuttle_q
    rot_junction = GOLDEN_HOPS / 144 * 24 * junction_q
    split_merge = sum(res.per_ion_quanta[i]["split_merge"] for i in ions) / n

    assert rot_shuttle == pytest.approx(PLAN_SHUTTLE, abs=0.3)
    assert rot_junction == pytest.approx(PLAN_JUNCTION, abs=0.5)
    assert split_merge == pytest.approx(PLAN_DOCKING, abs=1e-9)
    assert rot_shuttle + rot_junction + split_merge == pytest.approx(PLAN_TOTAL, abs=0.3)


def test_the_two_terms_plan_omits(corrected_run, arch):
    """PLAN §0.4's breakdown carries neither the spur shuttles nor the undock transit."""
    res = corrected_run
    ions = _data_ions(res)
    n = len(ions)
    shuttle = sum(res.per_ion_quanta[i]["shuttle"] for i in ions) / n
    junction = sum(res.per_ion_quanta[i]["junction"] for i in ions) / n
    assert shuttle - GOLDEN_HOPS * 0.1 == pytest.approx(1.2, abs=1e-9)  # 12 spur moves
    assert junction - GOLDEN_HOPS / 144 * 24 * 3.0 == pytest.approx(18.0, abs=1e-9)


def test_rotation_wall_clock_and_counterfactual(corrected_run, arch):
    res = corrected_run
    rot_us = sum(v for k, v in res.us_by_class.items() if k.startswith("rotate"))
    assert rot_us / 1000 == pytest.approx(PLAN_ROTATION_MS, abs=0.3)
    t2 = t2_metrics(arch, res, corrected_model())
    assert t2.counterfactual_rotation_us / 1000 == pytest.approx(
        PLAN_COUNTERFACTUAL_MS, abs=0.1
    )
    assert rot_us / t2.counterfactual_rotation_us == pytest.approx(20.0, abs=0.1)


def test_corner_hops_is_the_only_geometry_change_between_the_models(prog, arch):
    """M1 -> M2 must be a model swap, not a program change."""
    deck = replay(prog, arch, deck_model(), check_rules=False)
    corr = replay(prog, arch, corrected_model(), check_rules=False)
    assert deck.total_cost != corr.total_cost
    # same program, same movement, same contacts
    assert deck.n_gate_pairs == corr.n_gate_pairs
    assert deck.moves_per_ion == corr.moves_per_ion
    assert deck.junction_transits == corr.junction_transits


def test_cooling_makes_the_program_r7_legal(prog, arch):
    model = corrected_model()
    result = insert_cooling(prog, arch, model, policy=CoolingPolicy())
    assert result.r7_violations_before > 0
    assert result.r7_violations_after == 0
    assert result.n_cools == GOLDEN_BATCHES  # one global cool per contact batch
    assert result.cooling_us == pytest.approx(
        GOLDEN_BATCHES * float(arch.primitives.scalar("cool")["us"])
    )
    assert result.runtime_us_after > result.runtime_us_before
    rep = verify(result.program, arch, model)
    assert rep.rules.ok(), rep.rules.summary()


def test_cooled_program_still_reproduces_the_oracle_under_the_deck_model(prog, arch):
    """A global cool costs the deck's model nothing, so M1's oracle must survive it."""
    result = insert_cooling(prog, arch, corrected_model(), policy=CoolingPolicy())
    rep = verify(result.program, arch, deck_model())
    assert rep.result.total_cost == GOLDEN_COST
    assert rep.result.total_steps == GOLDEN_STEPS
    assert rep.rules.ok(), rep.rules.summary()
