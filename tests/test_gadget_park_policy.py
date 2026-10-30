"""How a block waits between uses (`City.park_round`, `City.park_within`).

The user, 2026-10-30, on Gidney 2025's carry chain spending about a quarter of each adder step
parking blocks: "measure both, decide later".  Two opt-ins, the defaults the rule as it was:

* `park_round=False` -- a parked block rests in its memory without a syndrome round (`hold`, on
  `mem_d3f`); its rounds are left to the refreshes its coherence budget asks for.  A decode the
  program wrote for the round a store would have measured has nothing to read: the GIR lists it
  under `noops`, and G7 counts it realised;
* `park_within=K` -- a block whose next use is among the next K instructions that move ions
  waits where it is instead of parking (K = 1: the very next only, as before).

What is held here, on programs whose blocks park between uses:

* the defaults are unchanged;
* each policy schedules with every place check holding and the flat sign-off proving the same
  outcomes at fault distance 3 -- the parks' rounds are not what the distance rests on;
* `hold` takes no round: the memories store nothing, and the schedule is shorter -- until a
  round is due (ROUND_DUE_US since the block's last), when the park is a `store` again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"
PROGRAMS = {
    "interleaved": ["prep a Z", "prep b Z", "prep c X", "cx c a", "x b", "cx c b", "cx a b",
                    "read a Z -> r", "read b Z -> s", "read c X -> u"],
    "ghz": ["prep a X", "prep b Z", "prep c Z", "cx a b", "cx b c",
            "read a Z -> r", "read b Z -> s", "read c Z -> u"],
}
POLICIES = {"as before": {}, "hold": {"park_round": False}, "within 3": {"park_within": 3},
            "within 3, hold": {"park_round": False, "park_within": 3}}


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


@pytest.fixture(scope="module")
def machine(leaves, tmp_path_factory):
    from qccd.gadget.floorplan import load_file, write
    from qccd.gadget.town import town
    rows = [[("depot1", "depot9"), ("prep", "prep_d3f"), ("shop", "tcx_d3"), ("hb", "hbay_d3f"),
             ("post1", "read_d3")],
            [("zone1", "zone_d3"), ("mem1", "mem_d3f"), ("mem2", "mem_d3f"), ("mem3", "mem_d3f"),
             ("depot2", "depot9")]]
    fp = tmp_path_factory.mktemp("park") / "park.floor"
    fp.write_text(write(town(leaves, name="park_d3", rows=rows), title="door-aware places"),
                  encoding="utf-8")
    return load_file(fp, leaves)


@pytest.fixture(scope="module")
def runs(leaves, machine):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import City
    lib, _ = machine
    out = {}
    for name, src in PROGRAMS.items():
        alg = parse_algorithm("\n".join(src) + "\n")
        for pol, opts in POLICIES.items():
            out[(name, pol)] = (alg, City(alg, lib, leaves, parallel=True, rounds_at="memory",
                                          free_at="block", prefetch=True, **opts).run())
    return out


def test_the_defaults_are_the_rule_as_it_was(leaves, machine):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import City
    lib, _ = machine
    city = City(parse_algorithm("prep a Z\n"), lib, leaves)
    assert city.park_round is True and city.park_within == 1


@pytest.mark.parametrize("name", list(PROGRAMS))
@pytest.mark.parametrize("pol", list(POLICIES))
def test_each_policy_is_checked_and_signed_off_at_distance_3(runs, leaves, machine, name, pol):
    from qccd.gadget.logic.flat import sign_off
    from qccd.gadget.place_checks import check_places
    lib, _ = machine
    alg, gir = runs[(name, pol)]
    assert gir["refused"] == []
    ck = check_places(gir, lib, leaves)
    failed = [r for r, v in ck.items() if isinstance(v, dict) and v.get("passed") is False]
    assert failed in ([], ["G11"]), failed                    # (G11: these programs call no decoder)
    so = sign_off(alg, gir, lib, leaves)
    assert so["passed"], so["why"]
    assert so["distance"]["at_least"] == 3, so["distance"]


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_hold_takes_no_round_and_is_shorter(runs, name):
    _, before = runs[(name, "as before")]
    _, hold = runs[(name, "hold")]
    stores = lambda g: sum(1 for e in g["events"] if e[4] == "store")  # noqa: E731
    assert stores(before) > 0 and stores(hold) == 0
    assert any(e[4] == "hold" for e in hold["events"])
    assert hold["makespan_us"] < before["makespan_us"]


def test_under_hold_a_round_still_comes_when_it_is_due(leaves, machine):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import ROUND_DUE_US, City
    lib, _ = machine
    city = City(parse_algorithm("prep a Z\n"), lib, leaves, park_round=False)
    assert city.store_op("mem1", "a", 1000.0) == "hold"            # just corrected
    city.last_round["a"] = 5000.0
    assert city.store_op("mem1", "a", 5000.0 + ROUND_DUE_US) == "store"
    assert city.store_op("mem1", "a", 5000.0, round_=True) == "store"
    assert City(parse_algorithm("prep a Z\n"), lib, leaves).store_op("mem1", "a", 0.0) == "store"
