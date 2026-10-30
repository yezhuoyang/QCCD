"""Classical inputs and outputs of a gate (`gate g b <- c -> o:`), folded and flat.

FormalRV's programs pass classical bits between gadget calls: Gidney 2025's `mxDescending`
measures a register and the calls after it branch on the bits (about 215,000 reads in one
prime), and a few calls read bits a sibling's subtree measured.  A gate read only its own
outcomes, so a folded schedule could not hold them (2026-10-30, "everything concrete"):

* an output (`gate mx b -> o:`, called `mx a -> r`) is the caller's outcome `r` from when the
  call decoded it;
* an input (`gate fix b d <- c:`, called `fix q d <- r`) is read inside like a guard's outcome:
  the call waits until it is decoded, and a classically controlled op on it waits for it there;
* expanded, the program's guard names the very outcome the other call measured.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"

PROGRAM = """\
gate mx b -> o:
  read b X -> o
  decode b
gate fix b d <- c:
  if c: cx b d
prep a X
prep q Z
prep d Z
mx a -> r
fix q d <- r
read q Z -> u
decode q
read d Z -> v
decode d
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
            [("mem1", "mem_d3"), ("mem2", "mem_d3"), ("mem3", "mem_d3"), ("depot2", "depot9")]]
    fp = tmp_path_factory.mktemp("classical") / "classical.floor"
    fp.write_text(write(town(leaves, name="classical_d3", rows=rows), title="three memories"), encoding="utf-8")
    return load_file(fp, leaves)


@pytest.fixture(scope="module")
def run(leaves, machine):
    from qccd.gadget.fold import parse_folded, schedule_folded
    lib, _ = machine
    return schedule_folded(parse_folded(PROGRAM, name="classical"), lib, leaves, rounds_at="memory")


def test_the_flat_program_reads_the_output_by_the_callers_name():
    from qccd.gadget.algorithm import parse_algorithm
    alg = parse_algorithm(PROGRAM)
    cx = next(i for i in alg.instructions if i.kind == "cx")
    read = next(i for i in alg.instructions if i.kind == "read" and i.blocks == ["a"])
    assert read.cvar == "r" and cx.guard == "r"


def test_bad_calls_are_refused():
    from qccd.gadget.algorithm import AlgorithmError, parse_algorithm
    head = "gate mx b -> o:\n  read b X -> o\n  decode b\nprep a X\nprep c X\nread c X -> m\ndecode c\n"
    for bad in ("mx a\n", "mx a -> r s\n", "if m: mx a -> r\n"):
        with pytest.raises(AlgorithmError):
            parse_algorithm(head + bad)
    with pytest.raises(AlgorithmError):
        parse_algorithm("gate fix b <- c:\n  if c: z b\nprep q Z\nfix q <- nowhere\n")


def test_folded_every_place_check_holds(run, leaves, machine):
    from qccd.gadget.fold import check_folded
    lib, _ = machine
    assert run["refused"] == []
    ck = check_folded(run, lib, leaves)
    assert ck["failed"] == [], ck["violations"]


def test_the_input_is_decoded_before_the_call_that_reads_it(run):
    mx = next(c for c in run["calls"] if c["gate"] == "mx")
    fix = next(c for c in run["calls"] if c["gate"] == "fix")
    m = run["macros"][mx["m"]]
    assert mx["cout"] == [["o", "r"]] and fix["ext_names"] == ["r"]
    assert fix["t0"] >= mx["t0"] + m["outputs"]["o"] - 1e-6


def test_expanded_the_guard_names_the_outcome_the_other_call_measured(run, leaves, machine):
    from qccd.gadget.fold import expand
    from qccd.gadget.place_checks import check_places
    lib, _ = machine
    x = expand(run)
    ins = x["program"]["instructions"]
    read = next(i for i in ins if i["kind"] == "read" and i.get("cvar", "").endswith(".o"))
    cx = next(i for i in ins if i["kind"] == "cx")
    assert cx["guard"] == read["cvar"], (cx["guard"], read["cvar"])
    assert check_places(x, lib, leaves)["failed"] == []
