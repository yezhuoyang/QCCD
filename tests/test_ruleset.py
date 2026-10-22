"""Rules as an input: the assertion language, the rule sets, and what they decide.

The language is the authority on what a rule says, so it is held to the verifier it
restates: on the compiled examples and on programmes built to break a rule, the two agree
rule by rule.  A paper's rule set is held to the timed checker the same way
(`scratchpad`-free: both run here on schedules built in the test).
"""

from __future__ import annotations

import json
import gzip
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.cost.models import corrected_model
from qccd.ruleset import (FAILS, HOLDS, NOT_APPLICABLE, RuleSyntaxError, TERMS, check, load,
                          names, problems)
from qccd.ruleset.programme import programme_execution
from qccd.ruleset.syntax import parse, parse_formula, show
from qccd.verify import verify

ROOT = Path(__file__).resolve().parents[1]


def _wise_schedule(d=3, kind="wise"):
    from qccd.repro.timed import TimedSchedule
    from qccd.repro.sched_jones import stim_program
    base = ROOT / "Reproduce/jones2025"
    with gzip.open(base / f"ours/grid_rot_d{d}_c2_{kind}.schedule.json.gz", "rt") as f:
        sched = TimedSchedule.from_json(json.load(f))
    return sched, stim_program(base / f"circuits/rotated_memory_z_d{d}_r1.stim")[0]


def _wise_rules(sched, circuit):
    from qccd.repro.models import jones_duration
    from qccd.ruleset import keeping_order
    from qccd.ruleset.schedule import schedule_execution
    exe = schedule_execution(sched, chain_order="physical", duration=jones_duration,
                             strict_clock=True, circuit=circuit)
    return {r.id: r for r in check(keeping_order(load("jones-wise")), exe).results}, exe


@pytest.mark.parametrize("d", [2, 3, 6, 12])
def test_jones_wise_rules_and_timed_applicability(d):
    res, _ = _wise_rules(*_wise_schedule(d))
    assert res["R4b"].verdict == res["R4d"].verdict == NOT_APPLICABLE
    assert "steps" in res["R4d"].reason
    assert all(r.verdict == HOLDS for key, r in res.items() if key not in ("R4b", "R4d"))
    assert res["R2"].verdict == res["R22"].verdict == HOLDS


def test_jones_wise_rejects_direct_wiring_schedule():
    res, exe = _wise_rules(*_wise_schedule(kind="ours"))
    assert res["W1"].verdict == FAILS
    assert not any("idle_sites" in o.facts for o in exe.ops)
    assert res["W3"].verdict == FAILS


def test_jones_wise_rejects_five_us_delayed_move():
    sched, circuit = _wise_schedule()
    move = next(e for e in sched.events if e.kind == "transit")
    bad = replace(move, t0=move.t0 + 5, t1=move.t1 + 5)
    sched = replace(sched, events=[bad if e.id == bad.id else e for e in sched.events])
    res, _ = _wise_rules(sched, circuit)
    assert res["W1"].verdict == FAILS


def test_jones_wise_geometry_rejects_uniform_false_cycle_without_changing_motion():
    sched, circuit = _wise_schedule()
    cycle = next(e for e in sched.events if e.kind == "split").t0
    sched = replace(sched, events=[replace(e, meta={**e.meta, "class": "split:S"})
                                   if e.kind == "split" and e.t0 == cycle else e for e in sched.events])
    res, exe = _wise_rules(sched, circuit)
    split = next(o for o in exe.ops if o.kind == "split")
    assert split.facts["motion"] == "split:S"  # John's existing input is preserved.
    assert split.facts["displacement"] == "split:N"
    assert res["R22"].verdict == HOLDS  # uniform strings alone do not detect this.
    assert res["W1"].verdict == FAILS


def test_jones_wise_opt_out_requires_each_idle_site_switch():
    sched, circuit = _wise_schedule()
    dev = deepcopy(sched.device)
    idle = next(t for t, ch in sched.chains.items() if not ch)
    dev.sites[idle].pop("broadcast_switch")
    res, _ = _wise_rules(replace(sched, device=dev), circuit)
    assert res["W3"].verdict == FAILS
    assert res["W1"].verdict == HOLDS


def test_jones_wise_transport_excludes_gates_on_idle_traps():
    from qccd.repro.timed import Event
    sched, circuit = _wise_schedule()
    ion = sched.qubits["1"]  # idle data qubit during the first layer
    trap = next(t for t, ch in sched.chains.items() if ion in ch)
    gate = Event(100000, "gate1", 60, 65, (ion,), at=trap,
                 meta={"op": "XRotation", "locks": [f"ion:{ion}", f"trap:{trap[1:]}"]})
    res, _ = _wise_rules(replace(sched, events=sched.events + [gate]), circuit)
    assert res["W2"].verdict == FAILS


def test_jones_wise_r2_still_rejects_two_ions_in_a_junction():
    sched, circuit = _wise_schedule()
    ion = next(e.ions[0] for e in sched.events if e.kind == "split")
    chains = deepcopy(sched.chains)
    next(ch for ch in chains.values() if ion in ch).append("probe")
    duplicated = [replace(e, id=e.id + 100000, ions=("probe",)) for e in sched.events
                  if e.kind in ("split", "transit", "j_enter", "j_exit", "merge")
                  and e.ions == (ion,) and e.meta["step"] == 1 and not e.meta["returning"]]
    res, _ = _wise_rules(replace(sched, chains=chains, events=sched.events + duplicated), circuit)
    assert res["R2"].verdict == FAILS


def judge(rs, m, prog, **kw):
    rs = load(rs) if isinstance(rs, str) else rs
    return {r.id: r for r in check(rs, programme_execution(prog, m.arch, corrected_model(), **kw)).results}


# ------------------------------------------------------------------------- the language

def test_a_rule_reads_as_written_and_prints_back():
    src = ('ruleset "t"\nparam k = 2\n'
           'rule X1 "a site holds k" \n'
           '  about "words"\n'
           '  for all s in sites, t in instants:\n'
           '    occupancy(s, t) <= k   # a comment\n')
    rs = parse(src)
    r = rs.rules[0]
    assert r.id == "X1" and r.about == "words"
    assert r.text == "for all s in sites, t in instants:\n  occupancy(s, t) <= k"
    assert show(r.formula) == "for all s in sites, t in instants: occupancy(s, t) <= k"
    assert show(r.formula, math=True) == "∀ s ∈ sites, t ∈ instants: occupancy(s, t) ≤ k"


@pytest.mark.parametrize("text, where", [
    ("for all s in sites occupancy(s, t)", "expected ':'"),
    ("for all s in : true", "expected a value"),
    ("count(x in ions where )", "expected a value"),
])
def test_a_mistake_names_its_place(text, where):
    with pytest.raises(RuleSyntaxError, match=where):
        parse_formula(text)


def test_an_unknown_word_or_name_is_refused_when_the_rule_set_is_read():
    with pytest.raises(RuleSyntaxError, match="capcity"):
        load('ruleset "x"\nrule X1 "t"\n  for all s in sites: capcity(s) > 0\n')
    with pytest.raises(RuleSyntaxError, match="'t'"):
        load('ruleset "x"\nrule X1 "t"\n  for all s in sites: occupancy(s, t) <= 2\n')


def test_core_rules_hold_in_every_rule_set_and_cannot_be_dropped():
    for n in names():
        assert {"R0", "R1", "R8", "R24"} <= set(load(n).ids), n
    with pytest.raises(RuleSyntaxError, match="core"):
        load('ruleset "x"\ninclude "standard"\ndrop R1\n')


def test_every_shipped_rule_set_uses_only_words_the_vocabulary_has():
    for n in names():
        assert problems(load(n)) == [], n


def test_a_resolved_rule_set_reads_back_as_itself():
    for n in names():
        rs = load(n)
        again = load(rs.text())
        assert again.ids == rs.ids and again.param_text == rs.param_text, n


def test_every_word_says_what_it_means():
    for t in TERMS.values():
        assert t.meaning and t.signature and t.group in ("hardware", "execution", "time",
                                                          "circuit", "model", "floorplan",
                                                          "machine"), t.name


# ------------------------------------------------------------------------- programmes

def test_the_compiled_examples_get_the_verifiers_verdicts():
    """The site's compiled examples: every rule both decide, decided the same way."""
    from qccd.arch import load as load_arch
    from qccd.ir.tsir import TSIR
    std = load("standard")
    base = ROOT / "qccd" / "site" / "compiled"
    if not base.exists():
        pytest.skip("the compiled examples are built with the site")
    n = 0
    for d in sorted(p for p in base.iterdir() if (p / "prog.cooled.tsir.json").exists()):
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        prog = TSIR.load(d / "prog.cooled.tsir.json")
        arch = load_arch(base / f"{meta['device']}.arch.json")
        old = set(verify(prog, arch, corrected_model()).rules.failed())
        new = check(std, programme_execution(prog, arch, corrected_model()))
        assert set(new.failed()) == old, d.name
        n += 1
    assert n >= 10


def test_capacity_and_its_counterexample():
    m = Machine.ring(3, 2, 2, name="ring6d")
    p = m.program("crowd", provenance="off")
    p.init({"d0": "S1", "d1": "S0", "d2": "S5"})
    p.move("d0", "S1", "S0")                   # S0 holds two, its capacity
    p.move("d2", "S5", "S0")                   # and now three
    res = judge("standard", m, p.build())
    assert res["R1"].verdict == FAILS and res["R1"].count >= 1
    assert "S0" in res["R1"].examples[0] and "3" in res["R1"].examples[0]


def test_a_rule_written_by_hand_is_checked_with_no_code():
    """A rule this website has never seen, about a named site: the language alone decides."""
    m = Machine.ring(3, 2, 2, name="ring6d")
    p = m.program("visit", provenance="off")
    p.init({"d0": "S1"})
    p.move("d0", "S1", "S2")
    rs = load('ruleset "mine"\n'
              'rule X1 "No ion ever stands in S2"\n'
              '  for all i in ions, t in instants: position(i, t) != "S2"\n')
    res = judge(rs, m, p.build())
    assert res["X1"].verdict == FAILS and "S2" in res["X1"].examples[0]
    assert res["R1"].verdict == HOLDS        # the core rules come with every rule set


def test_a_rule_that_needs_what_an_execution_lacks_does_not_apply():
    m = Machine.ring(3, 2, 2, name="ring6d")
    p = m.program("one", provenance="off")
    p.init({"d0": "S1"})
    p.move("d0", "S1", "S2")
    res = judge("standard", m, p.build())
    # no circuit was given: the circuit's rules do not apply, and say why
    assert res["R25"].verdict == NOT_APPLICABLE and "circuit" in res["R25"].reason
    assert res["R10"].verdict == NOT_APPLICABLE


# ------------------------------------------------------------------------- schedules

def _line_schedule(gates_on_one_trap_overlap: bool):
    from qccd.repro.timed import Event, PortGraph, TimedSchedule
    dev = PortGraph(sites={"T0": {"capacity": 3, "ports": {"s0": "R"}},
                           "T1": {"capacity": 3, "ports": {"s0": "L"}}},
                    segments={"s0": ("T0", "T1")})
    t = 50.0 if gates_on_one_trap_overlap else 100.0
    ev = [Event(0, "gate", 0.0, 100.0, ("a", "b"), at="T0"),
          Event(1, "gate", t, t + 100.0, ("a", "c"), at="T0")]
    return TimedSchedule("line", dev, {"T0": ["a", "b", "c"], "T1": []}, ev)


@pytest.mark.parametrize("overlap", [False, True])
def test_a_papers_rule_set_agrees_with_the_timed_checker(overlap):
    from qccd.repro import models
    from qccd.repro.timed import check as timed_check
    from qccd.ruleset import check_schedule
    sched = _line_schedule(overlap)
    circ = [("a", "b"), ("a", "c")]
    old = timed_check(sched, models.QCCDSIM, circuit=circ)
    new = {r.id: r for r in check_schedule("qccdsim", sched, circuit=circ).results}
    assert (old.checks["ion_serial"] == "failed") == (new["R8"].verdict == FAILS) == overlap
    assert (old.checks["trap_serial"] == "failed") == (new["R27"].verdict == FAILS) == overlap
    assert new["R24"].verdict == HOLDS
    # a gate that starts before the previous one on its qubit is over is out of order too
    assert (new["R25"].verdict == FAILS) == overlap
    assert new["R29"].verdict == NOT_APPLICABLE      # no clock was given


def test_two_ions_moving_opposite_ways_along_a_loop_in_one_step_break_r11():
    """R11 is decided once per step and loop (a shortcut for `for all a, b ... : shift(a) =
    shift(b)`); it must still see two ions on one loop going opposite ways."""
    m = Machine.ring(3, 2, 2, name="ring6d")
    p = m.program("both ways", provenance="off")
    p.init({"d0": "S1", "d1": "S4"})
    p.simd("shuttle", [["d0", "S1", "S2"], ["d1", "S4", "S3"]])
    res = judge("standard", m, p.build())
    old = set(verify(p.build(), m.arch, corrected_model()).rules.failed())
    assert res["R11"].verdict == FAILS and "R11" in old
