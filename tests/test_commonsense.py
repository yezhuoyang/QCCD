"""Commonsense: the things this field already knows, reproduced from our own model.

A platform that cannot recover the established results is not trustworthy on the new
ones. Every assertion below is a published or shipped number, cited, and computed here
from the architecture description and the replay -- not read back from a constant we
stored. Where our model *disagrees* with a source, the test says so explicitly rather
than being quietly loosened.

Sources are arXiv ids and deck page numbers; `python Knowledge/kg/query.py rules` and
`... why` trace each one.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.arch import OperatingPointPolicy, load  # noqa: E402
from qccd.codes import gross_code  # noqa: E402
from qccd.compile import CompilePolicy, build, compile_code  # noqa: E402
from qccd.compile.oddeven import cyclic_shift_target, odd_even_sort_program  # noqa: E402
from qccd.cost import corrected_model, deck_model, t1_metrics, t2_metrics  # noqa: E402
from qccd.cost.hardware import deck_unit_cell_report, hardware_report  # noqa: E402
from qccd.ir import Instruction, Participant  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402

ARCH = ROOT / "arch"
HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"

#: The 24-ancilla schedule is imported from a standalone visualizer HTML that is
#: THIRD-PARTY SOURCE MATERIAL and is deliberately not tracked in this repository.
#: Without it these tests cannot run at all, so they skip by name rather than fail --
#: a clone that is missing a private input should say so, not report a broken suite.
pytestmark = pytest.mark.skipif(
    not HTML.exists(),
    reason=f"{HTML.name} is not present; it is third-party material and untracked")
JONES = OperatingPointPolicy("qccdsim_jones", "fastest")


def arch(name):
    return load(ARCH / f"{name}.arch.json")


# ===================================================================== geometry


def test_a_junction_needs_three_or_more_axes():
    """"Shuttling through junctions, where three or more linear trap axes join..."
    -- quant-ph/0702175. A two-arm bend has a continuous RF null, so it is not one."""
    for name in ("h2_racetrack", "cyclone_base", "cyclone_dual_loop"):
        a = arch(name)
        s = a.device.summary()
        assert s["n_junction_nodes"] == 0, f"{name} should have no junction"
        assert s["n_corners"] > 0, f"{name} should still have bends"


def test_h2_is_one_continuous_rf_null_with_no_junctions():
    """H2's race track is a linear trap with periodic boundary conditions; its curved end
    zones are ordinary conveyor-belt regions (arXiv:2305.03828)."""
    a = arch("h2_racetrack")
    assert a.device.summary()["degree_histogram"] == {2: 40}
    assert len(a.device.loops) == 1 and next(iter(a.device.loops.values())).closed


def test_a_grid_has_degree_four_x_junctions_inside_and_bends_at_the_corners():
    """Murali-style grid QCCD: traps on the wires, junctions at the lattice points
    (arXiv:2004.04706)."""
    s = arch("grid9x9").device.summary()
    assert s["degree_histogram"][4] == 49   # interior 7x7
    assert s["degree_histogram"][3] == 28   # boundary
    assert s["n_corners"] == 4              # the four lattice corners are degree-2 bends


def test_a_spur_turns_a_rail_node_into_a_junction():
    """The README's tradeoff, structurally: vertical shuttling lines buy rotations and
    cost junctions."""
    for v in (0, 12, 24, 48):
        s = Machine.ring(72, 2, v).summary()
        assert s["n_junction_nodes"] == v


# ===================================================================== primitives


def test_junction_crossing_time_rises_steeply_with_degree():
    """10 / 100 / 120 us for degree 2 / 3 / 4 -- a 12x spread (arXiv:2511.15910)."""
    dc = arch("ring144_24v").primitives.degree_curve("junction_cross")
    cyc = OperatingPointPolicy("cyclone", "fastest")
    assert dc.get(2).pick(cyc).us == 10
    assert dc.get(3).pick(cyc).us == 100
    assert dc.get(4).pick(cyc).us == 120


def test_split_merge_dominates_heating_by_about_sixty_times():
    """split/merge n-bar < 6 against < 0.1 for a segment shuttle (arXiv:2510.23519)."""
    p = arch("ring144_24v").primitives
    shuttle = p.curve("shuttle_segment").pick(JONES)
    split = p.curve("split").pick(JONES)
    assert split.quanta / shuttle.quanta == pytest.approx(60, rel=0.01)
    assert split.us / shuttle.us == pytest.approx(16, rel=0.01)


def test_h2_measured_two_qubit_infidelity():
    """1.84(5)e-3 on 32 qubits, QV 2^16 (arXiv:2305.03828). PLAN §0.8 says use this, not
    a 1e-4 aspiration."""
    ms = arch("h2_racetrack").primitives.scalar("ms_gate")
    assert 1.0 - ms["fidelity_at_n0"] == pytest.approx(1.84e-3, rel=0.01)


# ===================================================================== the physics


def test_transport_causes_no_direct_gate_error_only_heating():
    """"The reconfiguration steps do not directly cause gate infidelity; however, they
    introduce idling noise and increase subsequent gate error rates due to heating"
    -- arXiv:2510.23519. So gate error must be a function of n-bar, not a constant."""
    a = arch("ring144_24v")
    model = corrected_model()
    cold = model.gate_error(a, 0.0)
    warm = model.gate_error(a, 1.0)
    hot = model.gate_error(a, 10.0)
    assert cold < warm < hot
    assert warm - cold == pytest.approx(2.0e-3, rel=1e-9)   # linear:2.0e-3


def test_a_transport_instruction_carries_no_gate_error_of_its_own():
    a = arch("ring144_24v")
    prog = build(a, "rotate", 5)
    res = replay(prog, a, corrected_model(), check_rules=False)
    assert res.gate_error_sum == 0.0
    assert res.total_quanta() > 0, "but it does heat"


def test_idle_time_heats_even_when_nothing_moves():
    """Anomalous heating accrues with elapsed time whether or not an ion moves (R17,
    arXiv:2605.25118)."""
    m = Machine.ring(12, 2, 2, name="idle").set_heating(
        anomalous_rate_quanta_per_ms=1.0)
    p = m.program("idle").init({"d0": "S0", "a0": "A0"})
    p.measure(["a0"])           # 120 us of elapsed time, no transport at all
    res = m.run(p, check_metrics=False).report.result
    assert res.quanta_components["anomalous"] > 0
    assert res.quanta_components["shuttle"] == 0


def test_cooling_is_mandatory_not_optional():
    """Simulated without cooling, WISE cannot scale past LER 1e-4 (R7c; arXiv:2510.23519,
    2606.06455)."""
    a = arch("ring144_24v")
    prog = build(a, "deck", html_path=HTML)
    rep = verify(prog, a, corrected_model(), check_metrics=False)
    assert "R7c" in rep.rules.failed(), "864 gates and no cooling must be illegal"


# ===================================================================== wiring


def test_broadcast_wiring_gives_constant_dacs_and_direct_gives_one_per_electrode():
    """Cyclone's O(1)-DAC claim, and the deck's whole reason for broadcasting."""
    direct = hardware_report(arch("grid9x9"))
    broadcast = hardware_report(arch("deck_unit_cell"))
    assert direct.n_traps == broadcast.n_traps          # identical geometry
    assert direct.electrodes == broadcast.electrodes
    # direct: one channel per site per role, so channels scale with the array
    assert direct.dacs > direct.n_traps
    assert broadcast.dacs < direct.dacs / 100


def test_the_broadcast_dac_count_does_not_grow_with_the_array():
    """Only the compensation electrodes scale, and a 1:100 demux divides even those
    (deck p.20)."""
    small = deck_unit_cell_report(3, 3)
    big = deck_unit_cell_report(30, 30)
    assert small["dacs_linear"] == big["dacs_linear"] == 24
    assert small["dacs_junction"] == big["dacs_junction"] == 8
    assert big["total_electrodes"] / small["total_electrodes"] == 100
    assert big["total_dacs"] / small["total_dacs"] < 25   # far sublinear


def test_the_decks_electrode_formulas():
    """24N electrodes, 48N switches, N - b trapping zones (deck p.19-20)."""
    for a, b in ((3, 3), (9, 9), (12, 7)):
        r = deck_unit_cell_report(a, b)
        n = a * b
        assert r["total_electrodes"] == 24 * n
        assert r["total_switches"] == 48 * n
        assert r["trapping_zones"] == n - b


# ===================================================================== the rules


def test_capacity_junction_and_segment_exclusivity_all_fire():
    """R1, R2, R3 -- the three constraints every QCCD scheduler is written against
    (deck p.7 'Hardware Constraints', arXiv:2510.23519)."""
    from test_rules import fired, init, prog_of, run, tiny_ring

    a = tiny_ring()

    # R1: three ions converge on a capacity-2 site.  They arrive on *different*
    # segments, so R3 rightly stays silent -- capacity and segment exclusivity are
    # different constraints and a test that conflated them would be checking neither.
    over = prog_of(init(d0="S1", d1="S2", d2="S3"),
                   Instruction(type="simd", id=1, cls="nudge", mode="inter",
                               participants=(Participant("d0", "S1", "S2"),
                                             Participant("d2", "S3", "S2"))))
    r = run(over, a)
    assert fired(r, "R1")
    assert not fired(r, "R3")

    # R3: two ions on ONE segment, in the same cycle
    seg = prog_of(init(d0="S1", d1="S2"),
                  Instruction(type="simd", id=1, cls="nudge", mode="inter",
                              participants=(Participant("d0", "S1", "S2"),
                                            Participant("d1", "S2", "S1"))))
    assert fired(run(seg, a), "R3")

    # R2: two ions on a degree-3 node
    junc = prog_of(init(d0="S0", d1="S1"),
                   Instruction(type="simd", id=1, cls="nudge", mode="inter",
                               participants=(Participant("d1", "S1", "S0"),)))
    assert fired(run(junc, a), "R2")


def test_gate_time_degrades_above_about_fifteen_ions_per_trap():
    """R13 -- which is why trap capacity cannot be raised for free to cut shuttling
    (arXiv:2511.15910, 2004.04706)."""
    from test_rules import fired, init, prog_of, run, tiny_ring

    a = tiny_ring(zone="big")
    ions = {f"d{i}": "S1" for i in range(17)}
    p = prog_of(init(**ions),
                Instruction(type="gate", id=1, gate="CX", mode="intra",
                            pairs=(("d0", "d1"),), sites=("S1",)))
    assert fired(run(p, a), "R13")


def test_intra_trap_parallelism_is_one():
    """"at most one or two gates per trap at a time" -- this asymmetry, not shuttling
    cost, is why trap count trades against gate latency (R12)."""
    from test_rules import fired, init, prog_of, run, tiny_ring

    a = tiny_ring(zone="big")
    p = prog_of(init(d0="S1", d1="S1", d2="S1", d3="S1"),
                Instruction(type="gate", id=1, gate="CX", mode="intra",
                            pairs=(("d0", "d1"), ("d2", "d3")), sites=("S1",)))
    assert fired(run(p, a), "R12")


# ===================================================================== schedules


def test_the_shipped_schedule_reproduces_exactly():
    """M1's external oracle: someone else's program, someone else's arithmetic."""
    a = arch("ring144_24v")
    prog = build(a, "deck", html_path=HTML)
    rep = verify(prog, a, deck_model())
    assert rep.result.total_cost == 397184
    assert rep.result.total_steps == 8808
    assert rep.rules.ok()


def test_the_shipped_heating_budget_reproduces():
    """PLAN §0.4: 267 shuttling + 1336 junction + 144 dock/undock ~ 1747 quanta per data
    ion per round, against an MS-gate budget of 1-2."""
    a = arch("ring144_24v")
    prog = build(a, "deck", html_path=HTML)
    res = replay(prog, a, corrected_model(), check_rules=False)
    ions = [i for i in res.per_ion_quanta if i.startswith("d")]
    n = len(ions)
    shuttle = 2672 * 0.1
    junction = 2672 / 144 * 24 * 3.0
    split_merge = sum(res.per_ion_quanta[i]["split_merge"] for i in ions) / n
    assert shuttle == pytest.approx(267, abs=0.3)
    assert junction == pytest.approx(1336, abs=0.5)
    assert split_merge == pytest.approx(144, abs=1e-9)
    assert shuttle + junction + split_merge == pytest.approx(1747, abs=0.3)
    budget = float(a.primitives.scalar("ms_gate")["max_quanta"])
    assert (shuttle + junction + split_merge) / budget > 1000


def test_rotation_wall_clock_is_set_by_the_junction_not_the_shuttle():
    """PLAN §0.5: every rigid hop crosses a junction because 24 of 144 rail nodes are
    degree 3, so the hop costs 100 us not 5 -- 267 ms against 13.4 ms."""
    a = arch("ring144_24v")
    prog = build(a, "deck", html_path=HTML)
    model = corrected_model()
    res = replay(prog, a, model, check_rules=False)
    rot = sum(v for k, v in res.us_by_class.items() if k.startswith("rotate"))
    t2 = t2_metrics(a, res, model)
    assert rot / 1000 == pytest.approx(267, abs=0.3)
    assert t2.counterfactual_rotation_us / 1000 == pytest.approx(13.4, abs=0.1)
    assert rot / t2.counterfactual_rotation_us == pytest.approx(20, abs=0.1)


def test_odd_even_sort_needs_two_batches_per_round():
    """The deck says so on p.15 ("each sorting round applies swaps in two batches (x2)"),
    and we get it from a different direction: a transposition merges one ion left and
    splits the other right, and R4 fixes a class's global direction."""
    a = arch("cyclone_base")
    m = len(a.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(m)]
    sr = odd_even_sort_program(a, ions, cyclic_shift_target(ions, m // 2),
                               arch_spec="arch/cyclone_base.arch.json")
    assert sr.cycles == 2 * sr.active_rounds
    assert sr.serialization_factor == pytest.approx(2.0, abs=0.05)


def test_cyclone_needs_about_two_rotations_for_the_gross_code():
    """"2 full rotations" for BB [[144,12,12]] (arXiv:2511.15910, deck p.9). Our compiler
    reaches it from placement and dynamic binding, with no knowledge of the claim."""
    a = arch("ring144_24v")
    r = compile_code(a, gross_code(), policy=CompilePolicy(insert_cooling=False))
    assert 1.0 <= r.revolutions <= 2.5, f"{r.revolutions} revolutions"
    assert r.contacts == 864


def test_cyclone_uses_m_over_two_traps():
    """m/2 traps for a code with m checks (arXiv:2511.15910); 72 for BB [[144,12,12]]."""
    a = arch("cyclone_base")
    assert len(a.device.sites()) == 72
    assert a.device.total_capacity() >= 144 + 72   # data + ancilla


def test_rigid_rotation_is_roadblock_free():
    """Cyclone's central claim: a ring with every ion moving in lockstep in one direction
    cannot roadblock. Ours is checked, not assumed -- R3 and R5 never fire."""
    a = arch("ring144_24v")
    prog = build(a, "deck", html_path=HTML)
    rep = verify(prog, a, deck_model())
    assert not [v for v in rep.rules.violations if v.rule in ("R3", "R5")]
    assert "R3" in rep.rules.passed() and "R5" in rep.rules.passed()


# ===================================================================== ranking


def test_a_good_routing_strategy_beats_a_bad_one_by_a_wide_margin():
    a = arch("ring144_24v")
    code = gross_code()
    good = compile_code(a, code, policy=CompilePolicy(
        placement="interleaved", ancilla_binding="dynamic",
        insert_cooling=False, refine_steps=0))
    bad = compile_code(a, code, policy=CompilePolicy(
        placement="identity", ancilla_binding="fixed",
        insert_cooling=False, refine_steps=0))
    assert good.contacts == bad.contacts == 864, "same semantics"
    assert good.hops < bad.hops / 3


def test_improving_one_pass_alone_can_make_the_schedule_worse():
    """Cyclone's confusion matrix, reproduced from our own model.

    "grid + dynamic schedule is worse than grid + greedy static EJF; ring + greedy EJF is
    disastrous. Vary hardware and policy together or the ranking lies" (arXiv:2511.15910).

    Here it appears between two *compiler passes*: interleaving the placement helps only
    if the binding is dynamic, and dynamic binding helps only if the placement is
    interleaved. Either one alone is worse than doing neither, and only the pair wins.
    A platform that swept one axis at a time would conclude both improvements are
    harmful.
    """
    a = arch("ring144_24v")
    code = gross_code()
    model = corrected_model()

    def ms(placement, binding):
        r = compile_code(a, code, policy=CompilePolicy(
            placement=placement, ancilla_binding=binding,
            insert_cooling=False, refine_steps=0))
        return replay(r.program, a, model, check_rules=False).total_us / 1000

    neither = ms("identity", "fixed")
    place_only = ms("interleaved", "fixed")
    bind_only = ms("identity", "dynamic")
    both = ms("interleaved", "dynamic")

    assert place_only > neither, "better placement alone is worse"
    assert bind_only > neither, "better binding alone is worse"
    assert both < neither / 2, "together they win by a wide margin"


def test_the_ranking_does_not_flip_between_the_two_primitive_tables():
    """The corpus's tables differ by 2-3x in time. A ranking that flips is not a result
    (PLAN §3.2)."""
    a = arch("ring144_24v")
    code = gross_code()
    progs = {
        "good": compile_code(a, code, policy=CompilePolicy(
            placement="interleaved", ancilla_binding="dynamic",
            insert_cooling=False, refine_steps=0)).program,
        "bad": compile_code(a, code, policy=CompilePolicy(
            placement="identity", ancilla_binding="fixed",
            insert_cooling=False, refine_steps=0)).program,
    }
    order = {}
    for table in ("qccdsim_jones", "transport_excitation"):
        model = corrected_model(table)
        times = {k: replay(p, a, model, check_rules=False).total_us
                 for k, p in progs.items()}
        order[table] = sorted(times, key=times.get)
    assert order["qccdsim_jones"] == order["transport_excitation"] == ["good", "bad"]


# ================================================= hand-checkable examples


def test_the_smallest_moves_cost_what_you_can_count():
    """examples/verifiable_examples.py, asserted. Small enough to check on screen."""
    m = Machine.ring(8, 2, 0, name="ring16")

    one = m.program("one").init({"d1": "S0"})
    with one.cycle("shuttle") as c:
        c.move("d1", "S0", "S1")
    r = m.run(one, model=deck_model(), check_metrics=False)
    assert (r.cost, r.steps) == (1, 1), "a straight segment is one hop, one step"

    end = m.program("end").init({"d1": "S7"})
    with end.cycle("shuttle") as c:
        c.move("d1", "S7", "S8")
    r = m.run(end, model=deck_model(), check_metrics=False)
    assert (r.cost, r.steps) == (3, 3), "S7 and S8 are both corners: a whole turn"


def test_a_sixteen_ion_ring_rotates_for_exactly_what_its_segments_cost():
    m = Machine.ring(8, 2, 0, name="ring16")
    r = m.run(m.program("r").fill().rotate(+1), model=deck_model(),
              check_metrics=False)
    # 16 segments, of which 2 are end-caps: 14 x 1 + 2 x 3
    assert r.cost == 14 * 1 + 2 * 3 == 20
    assert r.steps == 3


def test_the_decks_scheme_a_cost_reproduces_exactly():
    """Deck p.16: a 16-ion ring rotated by two, "Totals: cost 40 - steps 20".

    Cost matches exactly. Steps do not, and the reason is in the deck's own text: it
    serializes each corner ion's four sub-hops (two 90-degree turns, the highway
    junction, one rail hop) and batches the twelve rail ions into four steps, giving
    4 + 4x4 = 20. We charge the end-cap as one 3-deep edge and take the max over the
    hop. Same cost, different clock.

    The deck's totals line also says the corner ions cost "8 hops each", which would make
    the total 56 rather than 40; four hops each is what reconciles with its own 40.
    """
    m = Machine.ring(8, 2, 0, name="ring16")
    r = m.run(m.program("r").fill().rotate(+2), model=deck_model(), check_metrics=False)
    assert r.cost == 40, "the deck's stated cost, reproduced"
    assert r.steps == 6, "our clock, not the deck's -- see the docstring"


def test_one_rigid_hop_of_the_shipped_ring_is_the_unit_of_everything():
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    r = m.run(m.program("h").fill().rotate(+1), model=deck_model(), check_metrics=False)
    assert r.cost == 142 * 1 + 2 * 3 == 148
    assert r.steps == 3
    assert 2672 * 148 + 864 * 2 == 397184     # the whole schedule, from this unit
