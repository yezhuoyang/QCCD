"""The door-aware memory (`mem_d3f`, qccd/gadget/leaves/doors.py).

Codesign, 2026-10-29 (the user: "optimize the gadget geometry ... even reconfigure gadget
layout").  Inside Gidney 2025's carry chain a block goes back to its memory between nearby uses,
and `mem_d3`'s doors cost more than its round: the ring turns each ion's home to the door, and
`place()` chose the homes for the round alone.  `mem_d3f` is the same ring, docks, ancillas and
programs with the ions resting where the doors are short too.  What is held here:

* it is built, compiled and verified like every place, every op's specification complete;
* each of its ops is faster than `mem_d3`'s: store, refresh and release;
* its placement is what `search` finds, and costs fewer slots a visit than `mem_d3`'s;
* a block resting in it through syndrome rounds is signed off: outcome and distance 3.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


def test_the_door_aware_memory_is_verified_and_faster(leaves):
    old, new = leaves.place("mem_d3"), leaves.place("mem_d3f")
    assert set(new.ops) == set(old.ops) == {"store", "refresh", "release"}
    for k, op in new.ops.items():
        assert op.status == "verified", k
        assert op.logic["flows"]["complete"], k
        assert op.duration_us < old.ops[k].duration_us, (k, op.duration_us, old.ops[k].duration_us)


def test_its_placement_is_searched_and_cheaper():
    from qccd.gadget.leaves.bay import place
    from qccd.gadget.leaves.doors import DATA, DOCKS, N, RING_DOCKS, door_cost, search
    from qccd.gadget.surface import patch
    p = patch(3, 3, name="surface_d3")
    assert search(p) == (DATA, DOCKS)
    ds, ad, _ = place(p, N, RING_DOCKS)
    assert sum(door_cost(p, DATA, DOCKS)) < 0.6 * sum(door_cost(p, ds, ad))


def test_a_block_resting_in_it_is_signed_off(leaves, tmp_path):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import City
    from qccd.gadget.floorplan import load_file, write
    from qccd.gadget.logic.flat import sign_off
    from qccd.gadget.place_checks import check_places
    from qccd.gadget.town import town
    rows = [[("depot1", "depot9"), ("prep", "prep_d3"), ("post1", "read_d3"), ("zone1", "zone_d3")],
            [("mem1", "mem_d3f"), ("mem2", "mem_d3f"), ("depot2", "depot9")]]
    fp = tmp_path / "doors.floor"
    fp.write_text(write(town(leaves, name="doors_d3", rows=rows), title="door-aware memories"),
                  encoding="utf-8")
    lib, _ = load_file(fp, leaves)
    alg = parse_algorithm("prep q X\nse q 3\nread q X -> m\n")
    gir = City(alg, lib, leaves, parallel=True, rounds_at="memory").run()
    assert gir["refused"] == []
    assert {e[3] for e in gir["events"] if e[4] in ("store", "refresh")} <= {"mem1", "mem2"}
    assert any(e[4] == "refresh" for e in gir["events"])
    ck = check_places(gir, lib, leaves)
    failed = [r for r, v in ck.items() if isinstance(v, dict) and v.get("passed") is False]
    assert failed in ([], ["G11"]), failed                    # (G11: no decoder is called)
    so = sign_off(alg, gir, lib, leaves)
    assert so["passed"], so["why"]
    assert so["distance"]["at_least"] == 3, so["distance"]
