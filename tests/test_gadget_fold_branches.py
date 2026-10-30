"""A block read out one way or the other (an adaptive basis), inside a gate's own body.

FormalRV's Toffoli reads each routing pair of its |AutoCCZ> state in Z or in X by an earlier
outcome: `if m: <read in Z>` then `if !m: <read in X>`, two guarded calls.  Either may not fire,
so after the first the block stays; after the second, one of them fired whatever `m` was.
Found writing Gidney 2025's whole 32-bit adder as one program (2026-10-30), where its ancillas
rest in the memories its body shares:

* the block is no more once both branches have read it out, and its memory is free for the
  next block (it was kept for ever: six blocks a Toffoli, until no memory was left);
* the memory's lease ends where the call is done with it (it stayed open-ended: the next block
  to rest there could never check in);
* the body's own view counts the ions gone (G3) and stops answering for the block (G8);
* blocks passed into a call do not have their rounds in step (`REFRESH_STAGGER_US`): resting
  from one instant, ninety-five blocks sent the one decoder their syndromes at once (G11).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"

PROGRAM = """\
gate rz b:
  read b Z -> r
  decode b
gate rx b:
  read b X -> s
  decode b
gate main p:
  prep c X
  read c X -> m
  decode c
  prep a Z
  cx p a
  if m: rz a
  if !m: rx a
  prep d Z
  prep e Z
  cx p e
  read e Z -> v
  decode e
  cx p d
  read d Z -> u
  decode d
prep q Z
main q
"""


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


@pytest.fixture(scope="module")
def machine(leaves, tmp_path_factory):
    from qccd.gadget.floorplan import load_file, write
    from qccd.gadget.town import town
    rows = [[("depot1", "depot9"), ("prep", "prep_d3"), ("shop", "tcx_d3"), ("post1", "read_d3")],
            [("mem1", "mem_d3"), ("mem2", "mem_d3"), ("depot2", "depot9")]]
    fp = tmp_path_factory.mktemp("branches") / "branches.floor"
    fp.write_text(write(town(leaves, name="branches_d3", rows=rows), title="two memories"), encoding="utf-8")
    return load_file(fp, leaves)


@pytest.fixture(scope="module")
def run(leaves, machine):
    from qccd.gadget.fold import parse_folded, schedule_folded
    lib, _ = machine
    alg = parse_folded(PROGRAM, name="branches")
    return schedule_folded(alg, lib, leaves, outlive=True, rounds_at="memory")


def test_a_block_read_out_both_ways_frees_its_memory(run):
    # one memory is q's home; `a` and then `d` rest in the other -- `d` only once `a` is gone
    assert run["refused"] == [], run["refused"]
    main = next(m for m in run["macros"].values() if m["gate"] == "main")
    assert main["refused"] == []
    calls = {c["gate"]: c for c in main["calls"]}
    assert "gone" not in calls["rz"] and calls["rx"]["gone"] == ["main1_a"]
    assert main["blocks"]["main1_a"]["until"] >= calls["rx"]["t0"]
    # `d` waits while `e` is used: it rests in the memory `a` left, after `a` left it
    a_in = [e for e in main["events"] if e[3] == "mem2" and e[4] == "store"]
    assert len(a_in) == 2 and calls["rx"]["t0"] < a_in[1][1] < main["blocks"]["main1_a"]["until"], a_in
    assert len(calls["rx"]["left"][0]) == 9 and a_in[1][1] >= max(calls["rx"]["left"][0]), calls["rx"]["left"]


def test_every_place_check_holds(run, leaves, machine):
    from qccd.gadget.fold import check_folded
    lib, _ = machine
    ck = check_folded(run, lib, leaves)
    assert ck["failed"] == [], ck["violations"]


def test_blocks_passed_in_rest_out_of_step(leaves, machine):
    from qccd.gadget.fold import REFRESH_STAGGER_US, Folder, MacroCity, parse_folded
    lib, _ = machine
    alg = parse_folded("gate g x y:\n  cx x y\nprep a Z\nprep b Z\ng a b\n", name="stagger")
    folder = Folder(alg, lib, leaves, log=lambda *a, **k: None)
    city = MacroCity(alg, lib, leaves, folder=folder, params=["mem1", "mem2"])
    assert city.rest_since == {"arg0": 0.0, "arg1": -REFRESH_STAGGER_US}
