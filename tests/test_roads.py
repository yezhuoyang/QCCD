"""The hardware between gadgets obeys the basic rules (qccd/gadget/roads.py, rule H1): every
carry is one program on its road's own device, judged by R1-R23 like any op inside a place."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"


@pytest.fixture(scope="module")
def town():
    from qccd.gadget.algorithm import parse_algorithm_file
    from qccd.gadget.city import schedule
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.library import LeafLibrary
    leaves = LeafLibrary(CACHE, log=None)
    alg = parse_algorithm_file(REPO / "examples" / "gadgets" / "algorithms" / "teleport.alg")
    lib, fp = load_file(FLOORPLANS_DIR / f"{alg.floorplan or 'town'}.floor", leaves)
    return schedule(alg, lib, leaves), lib, leaves, fp


def test_a_lone_ion_is_the_conveyors_own_program():
    from qccd.gadget.roads import compile_carry, road_cycle_us
    run = compile_carry(9, [0.0], [1e9])
    d = road_cycle_us(9)
    assert run.failed == [] and not run.violations
    assert run.arrived == [pytest.approx(d["port_in"] + 8 * d["conveyor"] + d["port_out"])]
    assert [i.cls for i in run.prog.instructions[1:]] == ["port_in"] + ["conveyor"] * 8 + ["port_out"]


def test_one_cycle_is_one_waveform_on_a_road_too():
    """The pretence the schedule used to make -- each ion its own one-ion program -- asks a
    road's electrodes for a load and a shift in the same cycle when two ions overlap on it.
    The verifier refuses that on a road exactly as it would inside a place (R22)."""
    from qccd.cost.models import corrected_model
    from qccd.gadget.leaves.common import TrackedProgram
    from qccd.gadget.roads import conveyor_machine
    from qccd.verify import verify
    m = conveyor_machine(5, 2)
    tp = TrackedProgram(m, "two", {"i0": "XW", "i1": "XW"})
    tp.move("port_in", [("i0", "C0")])
    tp.move("conveyor", [("i0", "C1"), ("i1", "C0")])     # i1 loads while i0 shifts: two motions
    rep = verify(tp.prog, m.arch, corrected_model("qccdsim_jones"), check_metrics=False)
    assert "R22" in rep.rules.summary()["failed"]


def test_a_bundle_shares_its_road_one_class_a_cycle():
    """Two ions a cycle apart on a long road: each load holds the rail one cycle, so the one
    behind arrives later than its own transit -- and the program passes every basic rule."""
    from qccd.gadget.roads import compile_carry, road_cycle_us
    d = road_cycle_us(30)
    lone = d["port_in"] + 29 * d["conveyor"] + d["port_out"]
    run = compile_carry(30, [0.0, 20.0], [1e9, 1e9])
    assert run.failed == []
    assert run.arrived[0] > lone - 1e-9 and run.arrived[1] >= 20.0 + lone - 1e-9
    assert any(len(i.participants) == 2 for i in run.prog.instructions if i.cls == "conveyor")


def test_a_doors_stub_is_driven_by_one_side_at_a_time():
    """While the op at the far door takes an ion off its stub, the road delivers nothing onto
    it: the delivery waits, and if the schedule's take came too early, that is a violation."""
    from qccd.gadget.roads import compile_carry
    free = compile_carry(5, [0.0], [1e9])
    t = free.arrived[0]
    held = compile_carry(5, [0.0], [1e9], far_busy=[(t - 3.0, t + 40.0)])
    assert held.arrived[0] >= t + 40.0
    late = compile_carry(5, [0.0], [t + 10.0], far_busy=[(t - 3.0, t + 40.0)])
    assert late.late == [0] and "after the far side takes it" in late.violations[0]


def test_every_carry_of_a_schedule_is_a_road_program_that_obeys_the_rules(town):
    import copy
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.place_checks import check_places
    from qccd.gadget.roads import check_roads
    gir, lib, leaves, fp = town
    r = check_roads(gir, lib, leaves)
    assert not r["violations"] and r["metrics"]["late_deliveries"] == 0
    assert r["metrics"]["carries"] == len(gir["carries"]) > r["metrics"]["programs"]
    pc = check_places(gir, lib, leaves)
    mr = judge_schedule(lib, leaves, gir, regions=fp.regions)
    assert "H1" in pc["passed"] and "H1" in mr["passed"]
    # a schedule that takes an ion off a long road before the road's own program can bring it
    bad = copy.deepcopy(gir)
    c = next(c for c in bad["carries"] if bad["nets"][c[1]][4] >= 20 and len(c[7]) > 1)
    c[12] = [k - 10.0 for k in c[12]]
    assert check_roads(bad, lib, leaves)["violations"]
    assert "H1" in judge_schedule(lib, leaves, bad, regions=fp.regions)["failed"]


def test_the_machine_counts_the_dacs_of_its_roads_and_junctions(town):
    from qccd.gadget.roads import machine_census
    gir, lib, leaves, _ = town
    c = machine_census(gir, lib, leaves)
    fam = c["by_family"]
    assert fam["road"]["devices"] == len(gir["nets"])
    assert c["dacs"] == sum(v["dacs"] for v in fam.values()) > 0
    assert c["electrodes"] == sum(v["electrodes"] for v in fam.values())
    # every device drives its own plane: the roads and junctions are most of the machine's DACs
    assert fam["road"]["dacs"] + fam["junction"]["dacs"] > c["dacs"] / 2


# ------------------------------------------------------------------------- a road's real geometry


def test_a_bent_road_is_its_own_conveyor_with_a_site_at_every_corner():
    from qccd.cost.models import corrected_model
    from qccd.gadget.leaves.transport import parse_shape, road, shape_of, shape_points
    from qccd.verify import verify
    assert shape_of([[0, 0], [10, 0]]) == "9"                       # straight: chan9, as before
    assert shape_of([[17, 1], [14, 1], [14, 11]]) == "3L10" or shape_of([[17, 1], [14, 1], [14, 11]]) == "3R10"
    assert shape_of([[0, 0], [3, 0], [7, 0]]) == "6"               # collinear pieces are one leg
    L, legs = parse_shape("3R10")
    assert L == 12 and [n for n, _ in legs] == [3, 10]
    pts = shape_points("3R10")
    assert len(pts) == L + 2 and pts[3] == (3.0, 0.0) and pts[4][0] == 3.0   # the corner is a site
    b = road("3R10", 2)
    assert b.master.name == "chan3R10"
    rep = verify(b.programs["carry"], b.machine.arch, corrected_model("qccdsim_jones"), check_metrics=False)
    assert rep.rules.summary()["failed"] == []


def _bent_town(leaves):
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load, parse_floorplan
    text = (FLOORPLANS_DIR / "town.floor").read_text(encoding="utf-8")
    # the prep's entrance junction three units along the street: its door road turns a corner
    fp = parse_floorplan(text.replace("junction j0_2 at 14 0", "junction j0_2 at 17 0"), name="bent")
    return load(fp, leaves), fp


def test_a_schedule_on_a_bent_road_obeys_every_rule_and_round_trips():
    import json
    from qccd.gadget.algorithm import parse_algorithm_file
    from qccd.gadget.city import schedule
    from qccd.gadget.judge import judge_floorplan, judge_schedule
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.modules import modules_text, parse_modules
    from qccd.gadget.place_checks import check_places
    leaves = LeafLibrary(CACHE, log=None)
    lib, fp = _bent_town(leaves)
    assert judge_floorplan(lib, leaves, regions=fp.regions)["failed"] == []
    gir = schedule(parse_algorithm_file(REPO / "examples" / "gadgets" / "algorithms" / "bell_cnot.alg"),
                   lib, leaves)
    bent = {n: v for n, v in gir["nets"].items() if len(v) > 5}
    assert list(bent) == ["/door_prep_in"] and bent["/door_prep_in"][5] in ("3L10", "3R10")
    assert any(c[1] == "/door_prep_in" for c in gir["carries"])
    pc = check_places(gir, lib, leaves)
    mr = judge_schedule(lib, leaves, gir, regions=fp.regions)
    assert pc["failed"] == [] and mr["failed"] == [] and "H1" in mr["passed"]
    # the module language names the bent road's own conveyor, and reads it back exactly
    from qccd.gadget.fold import parse_folded, schedule_folded
    src = (REPO / "examples" / "gadgets" / "algorithms" / "bell_cnot.alg").read_text(encoding="utf-8")
    g = schedule_folded(parse_folded(src, name="bell_cnot"), lib, leaves)
    text = modules_text(g, leaves)
    assert "chan3R10" in text or "chan3L10" in text
    assert parse_modules(text).gir == json.loads(json.dumps(g))


def test_the_page_gets_every_carrys_compiled_program():
    from qccd.gadget.algorithm import parse_algorithm_file
    from qccd.gadget.city import schedule
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.roads import attach_runs
    leaves = LeafLibrary(CACHE, log=None)
    lib, _ = _bent_town(leaves)
    gir = schedule(parse_algorithm_file(REPO / "examples" / "gadgets" / "algorithms" / "bell_cnot.alg"),
                   lib, leaves)
    attach_runs(gir, lib, leaves)
    assert set(gir["road_runs"]) == {c[0] for c in gir["carries"]}
    progs = gir["road_programs"]
    c = next(c for c in gir["carries"] if c[1] == "/door_prep_in")
    run = progs[gir["road_runs"][c[0]]]
    assert run["n"] == len(c[7]) and run["cycles"][0][2] == "port_in"
    # every ion ends on the far stub (node L + 1), one class a cycle
    L = gir["nets"]["/door_prep_in"][4]
    last = {}
    for t0, t1, cls, moves in run["cycles"]:
        assert cls in ("port_in", "conveyor", "port_out")
        for ion, a, b in moves:
            last[ion] = b
    assert set(last.values()) == {L + 1}
