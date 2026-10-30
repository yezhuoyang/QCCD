"""When a block is free for its next instruction (`City.free_at`), and when a CNOT's resting
operand starts out (`City.prefetch`).

Codesign, 2026-10-29, measured on Gidney 2025's carry chain: the scheduler freed every block an
instruction acted on only when ALL of the instruction was over, so the ancilla a transversal
CNOT leaves at the workshop waited while its partner went home and took its syndrome round
(24 ms of `store`), and a product measured by one ancilla took 159 ms where its ops need under
100.  `free_at="block"` frees each block when what the instruction did to IT is over.  G7 says
"each block's instructions run in program order", and now compares each block's own stretch of
an instruction, not the whole instruction's.  `prefetch` starts a CNOT's operand that rests in
a memory as early as its release takes, so it arrives with the other.  What is held here:

* the defaults are unchanged;
* per block, and with prefetch, the same programs are shorter, every place check holds, and the flat sign-off
  proves the same outcomes at fault distance 3;
* G7 still fails a schedule in which a block's next instruction starts before what the last
  one did to that block is over.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"
SRC = ["prep a X", "prep b Z", "prep c Z", "cx a b", "cx b c",
       "read a Z -> r", "read b Z -> s", "read c Z -> u"]


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


@pytest.fixture(scope="module")
def machine(leaves, tmp_path_factory):
    from qccd.gadget.floorplan import load_file, write
    from qccd.gadget.town import town
    rows = [[("depot1", "depot9"), ("prep", "prep_d3"), ("shop", "tcx_d3"), ("hb", "hbay_d3"),
             ("post1", "read_d3")],
            [("zone1", "zone_d3"), ("mem1", "mem_d3"), ("mem2", "mem_d3"), ("mem3", "mem_d3"),
             ("depot2", "depot9")]]
    fp = tmp_path_factory.mktemp("fb") / "fb.floor"
    fp.write_text(write(town(leaves, name="fb_d3", rows=rows), title="a transversal workshop"),
                  encoding="utf-8")
    return load_file(fp, leaves)


@pytest.fixture(scope="module")
def runs(leaves, machine):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import City
    lib, _ = machine
    alg = parse_algorithm("\n".join(SRC) + "\n")
    return alg, {"instruction": City(alg, lib, leaves, parallel=True).run(),
                 "block": City(alg, lib, leaves, parallel=True, free_at="block").run(),
                 "prefetch": City(alg, lib, leaves, parallel=True, free_at="block", prefetch=True).run()}


def test_the_default_frees_a_block_with_its_whole_instruction(leaves, machine):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import City
    lib, _ = machine
    city = City(parse_algorithm("prep a Z\n"), lib, leaves)
    assert city.free_at == "instruction" and city.prefetch is False


def test_per_block_is_shorter_and_signed_off(runs, leaves, machine):
    from qccd.gadget.logic.flat import sign_off
    from qccd.gadget.place_checks import check_places
    alg, g = runs
    lib, _ = machine
    assert g["prefetch"]["makespan_us"] <= g["block"]["makespan_us"] < g["instruction"]["makespan_us"]
    for free_at, gir in g.items():
        assert gir["refused"] == []
        ck = check_places(gir, lib, leaves)
        # (G11: these programs call no decoder)
        failed = [r for r, v in ck.items() if isinstance(v, dict) and v.get("passed") is False]
        assert failed in ([], ["G11"]), (free_at, failed)
        so = sign_off(alg, gir, lib, leaves)
        assert so["passed"], (free_at, so["why"])
        assert so["distance"]["at_least"] == 3, (free_at, so["distance"])


@pytest.mark.parametrize("free", ["block", "prefetch"])
def test_g7_still_holds_each_block_to_program_order(runs, leaves, machine, free):
    from qccd.gadget.place_checks import check_places
    _, g = runs
    lib, _ = machine
    bad = copy.deepcopy(g[free])
    ions = set(bad["blocks"]["b"]["ions"])
    ins = {i["text"]: i["id"] for i in bad["program"]["instructions"]}
    on_b = lambda e: any(isinstance(x, int) and x in ions for _, x in e[8])  # noqa: E731
    first_end = max(e[2] for e in bad["events"] if e[6] == ins["cx a b"] and on_b(e))
    second = min((e for e in bad["events"] if e[6] == ins["cx b c"] and on_b(e)), key=lambda e: e[1])
    second[1] = first_end - 100.0                  # b's second CNOT begins before its first ends
    assert "G7" in check_places(bad, lib, leaves)["failed"]
