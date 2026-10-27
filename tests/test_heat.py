"""Heat across the town (qccd/gadget/heat.py): every ion's n̄ followed through every op, road
and junction, as the replay follows it inside a place -- and a schedule that keeps it under
the ion-loss limit."""
from __future__ import annotations

from pathlib import Path

import pytest

from qccd.fullstack import layers

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"
SHOR = REPO / "tests" / "fixtures" / "fullstack" / "shor15"


@pytest.fixture(scope="module")
def town():
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.library import LeafLibrary
    leaves = LeafLibrary(CACHE, log=None)
    lib, _ = load_file(FLOORPLANS_DIR / "fullstack.floor", leaves)
    return lib, leaves


def test_an_op_carries_or_cools_what_an_ion_arrives_with(town):
    """One cold replay says what any arrival does: a junction pass cools nobody, so it adds
    its heat to what came in; a memory's store cools, so its ions leave at its own n̄.  A
    bundle stacked on the arm's stub would heat each ion more than a lone pass does: which is
    why the town passes a bundle one ion at a time (`City._train`)."""
    from qccd.gadget.heat import op_heat
    lib, leaves = town
    jp = op_heat(leaves, lib["junc9"], "pass.W.E")
    lone = op_heat(leaves, lib["junc9"], "pass.W.E.1")["i0"]
    assert all(not h.cooled and h.pre >= lone.pre > 0 for h in jp.values())
    last = jp["i8"]
    assert last.through(100.0)[1] == pytest.approx(100.0 + last.out)
    store = op_heat(leaves, lib["mem_d3"], "store")
    d0 = store["d0"]
    assert d0.cooled and d0.enters
    assert store["d0"].through(300.0)[1] == store["d0"].through(0.0)[1]   # cooled: arrival forgotten
    assert d0.through(300.0)[0] == pytest.approx(max(300.0 + d0.pre, d0.post))
    # a readout recools what it measured before sending it on (it used to add ~100 to it)
    rd = op_heat(leaves, lib["read_d3"], "read.Z")
    assert all(h.cooled for h in rd.values() if h.leaves)
    assert max(h.out for h in rd.values() if h.leaves) < 60


def test_heat_is_followed_across_the_town_and_kept_under_the_limit(town):
    """Shor-15 on the full-stack town: ions arrive at places far hotter than an op alone
    assumes (they cross a junction at every door), and the scheduler keeps every one under
    the ion-loss limit -- routing the coolest way and sending used ions to a depot they can
    reach.  Place check G6 and machine rule G6 both read the followed heat."""
    from qccd.fullstack import qec2alg
    from qccd.gadget.fold import expand, parse_folded, schedule_folded
    from qccd.gadget.heat import follow
    from qccd.gadget.place_checks import check_places
    lib, leaves = town
    alg = parse_folded(qec2alg.lower(layers.read(SHOR / "shor15.qec", "qec")).text, name="shor15")
    x = expand(schedule_folded(alg, lib, leaves))
    r = follow(x, lib, leaves)
    m = r["metrics"]
    assert not r["violations"] and not r["hot_gates"]
    assert m["arrival_quanta_median"] > 50 and m["hottest_ion_quanta"] <= m["ion_loss_limit"]
    assert m["most_junction_passes_uncooled"] >= 10
    pc = check_places(x, lib, leaves)
    assert "G6" in pc["passed"] and pc["metrics"]["heat"]["hottest_ion_quanta"] == m["hottest_ion_quanta"]
    # the same run with ions made hotter on the way is caught, with the trip that did it
    hot = follow(x, lib, leaves, limit=m["hottest_ion_quanta"] - 1)
    assert hot["violations"] and "junction passes" in hot["violations"][0]


def test_the_city_routes_the_coolest_way_when_the_quickest_is_too_hot(town):
    from qccd.gadget.city import City
    from qccd.gadget.fold import parse_folded
    lib, leaves = town
    alg = parse_folded('title t\nfloorplan "fullstack"\nprep q0 Z\nread q0 Z -> r\n', name="t")
    city = City(alg, lib, leaves)
    src, dst = ("post2", "out"), ("depot1", "in")
    quick = city._route(src, dst)
    cool, q = city._coolest(src, dst, 9)
    assert q <= city._route_heat(quick, 9) + 1e-9
    gids = list(range(10_000, 10_009))
    for g in gids:
        city.heat[g] = city.heat_limit - q / 2           # too hot for any route there
    assert city._plan(gids, src, dst) == cool or city._route_heat(quick, 9) == q
    # a bundle sent to a depot goes where it can arrive under the limit, if one exists
    for g in gids:
        city.heat[g] = 0.0
    depots = [p for p in city.leaf if city.family(p) == "depot"]
    assert city._reachable(depots, src, "in", gids)


def test_a_bundle_crosses_a_junction_one_ion_at_a_time(town):
    """The queue waits on the road's sites; each ion is a lone pass of its own, back to back,
    and the carry in front is taken in ion by ion -- G1 and G12 read it that way."""
    from qccd.gadget.algorithm import parse_algorithm_file
    from qccd.gadget.city import schedule
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.place_checks import check_places
    _, leaves = town
    alg = parse_algorithm_file(REPO / "examples" / "gadgets" / "algorithms" / "bell_cnot.alg")
    lib, fp = load_file(FLOORPLANS_DIR / f"{alg.floorplan or 'town'}.floor", leaves)
    gir = schedule(alg, lib, leaves)
    passes = [e for e in gir["events"] if lib[gir["leaves"][e[3]][0]].family == "road"]
    assert passes and all(e[4].endswith(".1") and len(e[8]) == 1 for e in passes)
    pc = check_places(gir, lib, leaves)
    mr = judge_schedule(lib, leaves, gir, regions=fp.regions)
    assert "G1" in pc["passed"] and not mr["failed"], (pc["failed"], mr["failed"])
    # a carry nobody takes in at all still fails G12
    bad = dict(gir, events=[e for e in gir["events"] if e not in passes[:1]])
    assert "G12" in judge_schedule(lib, leaves, bad, regions=fp.regions)["failed"]
