"""A logical Hadamard H̄ at a Hadamard bay (`hbay_d3`, qccd/gadget/leaves/hadamard.py).

Codesign, 2026-10-29 (the user: "you don't necessarily need to follow how we design gadget ...
Always optimize the bottleneck first").  Gidney 2025's carry chain measured mixed products by
lattice surgery, 25 rounds a merge, and wrote H̄ as three Y-state merges.  On the rotated d=3
surface code a transversal H is H̄ up to a quarter turn of the patch, which a ring pays for in
the order the ions leave.  What is held here:

* the bay is built, compiled and verified like every place: one op, `h`, specified by its 18
  single-ion flows, its logical flows X̄ -> Z̄ and Z̄ -> X̄ checked on the block as it leaves;
* the order it sends the ions out in is the patch turned a quarter turn;
* `ht q` in a `.alg` (the standard gate `h` stays S̄·√X̄·S̄) runs there: no instruction
  refused, every place check and machine rule
  holds, and the flat sign-off proves the outcome and a circuit-level fault distance of 3;
* a Pauli frame crosses it with its X̄ and Z̄ parts exchanged;
* the flat check follows the block's ions in their turned order (`turns` on the GIR's block):
  read in the order it was born in, the same schedule is distance 1.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


@pytest.fixture(scope="module")
def machine(leaves, tmp_path_factory):
    from qccd.gadget.floorplan import load_file, write
    from qccd.gadget.town import town
    rows = [[("depot1", "depot9"), ("prep", "prep_d3"), ("clinic", "se_d3"), ("hb", "hbay_d3"),
             ("post1", "read_d3")],
            [("zone1", "zone_d3"), ("mem1", "mem_d3"), ("mem2", "mem_d3"), ("bench1", "ppm_d3"),
             ("depot2", "depot9")]]
    fp = tmp_path_factory.mktemp("hb") / "hb.floor"
    fp.write_text(write(town(leaves, name="hb_d3", rows=rows), title="a Hadamard bay"),
                  encoding="utf-8")
    return load_file(fp, leaves)


def _run(leaves, machine, src):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.refresh import schedule_with_refresh
    lib, _ = machine
    return schedule_with_refresh(parse_algorithm(src), lib, leaves, log=None, parallel=True)


@pytest.fixture(scope="module")
def plus(leaves, machine):
    return _run(leaves, machine, "prep q X\nht q\nread q Z -> m\n")


def test_the_bay_is_verified_and_complete(leaves):
    hb = leaves.place("hbay_d3")
    assert set(hb.ops) == {"h"}
    op = hb.ops["h"]
    assert op.status == "verified"
    assert op.logic["flows"]["complete"]


def test_the_bay_sends_the_patch_out_a_quarter_turn_round():
    from qccd.gadget.leaves.hadamard import ROTATE
    # the ion at row r, column c of the 3x3 patch leaves at row c, column 2 - r
    assert ROTATE == tuple(3 * (q % 3) + (2 - q // 3) for q in range(9))
    assert sorted(ROTATE) == list(range(9))


def test_h_runs_at_the_bay_and_is_signed_off(plus, leaves, machine):
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.logic.flat import sign_off
    from qccd.gadget.place_checks import check_places
    alg, gir = plus
    lib, fp = machine
    assert gir["refused"] == []
    assert [e[3] for e in gir["events"] if e[4] == "h"] == ["hb"]
    (turn,) = gir["blocks"]["q"]["turns"]
    born = gir["blocks"]["q"]["ions"]
    from qccd.gadget.leaves.hadamard import ROTATE
    assert turn[1] == [born[ROTATE.index(p)] for p in range(9)]
    ck = check_places(gir, lib, leaves)
    assert not [r for r, v in ck.items() if isinstance(v, dict) and v.get("passed") is False]
    rules = judge_schedule(lib, leaves, gir, regions=fp.regions)
    assert not [k for k, v in rules.items() if isinstance(v, dict) and v.get("passed") is False]
    so = sign_off(alg, gir, lib, leaves)
    assert so["passed"], so["why"]
    assert {r["relation"]: r["ok"] for r in so["relations"]} == {"m=Z̄[q] = 0": True}
    assert so["distance"]["at_least"] == 3, so["distance"]


def test_a_frame_crosses_the_bay_with_x_and_z_exchanged(leaves, machine):
    from qccd.gadget.logic.flat import sign_off
    lib, _ = machine
    alg, gir = _run(leaves, machine, "prep q Z\nx q\nht q\nread q X -> m\n")
    so = sign_off(alg, gir, lib, leaves)
    assert so["passed"], so["why"]
    assert {r["relation"]: r["ok"] for r in so["relations"]} == {"m=X̄[q] = 1": True}


def test_the_flat_check_needs_the_turned_order(plus, leaves, machine):
    from qccd.gadget.logic.flat import sign_off
    alg, gir = plus
    lib, _ = machine
    bad = copy.deepcopy(gir)
    del bad["blocks"]["q"]["turns"]
    so = sign_off(alg, bad, lib, leaves)
    assert not so["passed"]
    assert so["distance"]["distance"] == 1, so["distance"]
