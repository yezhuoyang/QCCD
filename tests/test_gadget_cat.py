"""Lattice surgery over many blocks: cat states (`catbench_d3`, qccd/gadget/leaves/catbench.py).

The user, 2026-10-29, choosing how Gidney-Ekera's register-wide merges run on the machine: "Cat
chain" -- each block's bench keeps probes joined into cat states with the other benches', each
probe couples to its own block and is read; the product is the parity of the probes' bits.  "All
system call services (especially lattice surgery) must now be compiled in details", and "we
definitely need to support ... parallelism".  What is held here:

* the cat bench's five ops and the depot that sends and takes three links at a time are built,
  compiled and verified like every place, and every op's specification is complete;
* a `ppm` over five blocks runs on a machine with five cat benches: no instruction refused,
  every place check and machine rule holds, and the flat sign-off -- the whole schedule, ion by
  ion -- proves the archived outcome IS the product of the five logical operators;
* the cats are made in parallel (every neighbouring pair fused by its own links at once), so
  five blocks take about the time of two;
* the flips the parity checks leave are written into the blocks' frames, and the sign-off
  proves them right on a program whose readouts see them (X̄^5 measured on |0̄> blocks, then
  every Z̄_i Z̄_j read);
* both proofs can fail: with one probe's bit left out of the outcome, or the checks' frames
  left out of the archive, the schedule is wrong;
* circuit-level fault distance, which the user does not ask for, is reported as measured: 2 on
  the first program, the four-door bench's; 1 on the second -- a probe holds a whole logical
  operator of its block, so one X fault on it between fusion and coupling lands on the block
  as P̄ (Shor's construction gives every support ion a probe of its own).
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
    n = 5
    rows = [[("depot1", "depot9"), ("prep", "prep_d3"), ("clinic", "se_d3")]
            + [(f"cat{k}", "catbench_d3") for k in range(1, 4)],
            [(f"cat{k}", "catbench_d3") for k in range(4, n + 1)]
            + [("post1", "read_d3"), ("zone1", "zone_d3"), ("depot2", "depot9x")]
            + [(f"mem{k}", "mem_d3") for k in range(1, n + 1)]]
    fp = tmp_path_factory.mktemp("cat") / "cat5.floor"
    fp.write_text(write(town(leaves, name="cat5_d3", rows=rows), title="five cat benches"),
                  encoding="utf-8")
    return load_file(fp, leaves)


def _run(leaves, machine, letters, basis=None):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.refresh import schedule_with_refresh
    n, basis = len(letters), basis or letters
    src = "\n".join([f"prep q{i} {basis[i]}" for i in range(n)]
                    + ["ppm " + " ".join(f"{letters[i]} q{i}" for i in range(n)) + " -> m"]
                    + [f"read q{i} {basis[i]} -> r{i}" for i in range(n)]) + "\n"
    lib, fp = machine
    alg, gir = schedule_with_refresh(parse_algorithm(src), lib, leaves, log=None, parallel=True)
    return alg, gir


def _span(gir):
    rows = [e for e in gir["events"] if e[4].startswith("cat.")]
    return max(e[2] for e in rows) - min(e[1] for e in rows)


@pytest.fixture(scope="module")
def xzxzx(leaves, machine):
    return _run(leaves, machine, "XZXZX")


@pytest.fixture(scope="module")
def frames(leaves, machine):
    return _run(leaves, machine, "XXXXX", basis="ZZZZZ")


def test_the_cat_bench_and_its_depot_are_verified_and_complete(leaves):
    from qccd.gadget.leaves.catbench import ROUNDS
    cat = leaves.place("catbench_d3")
    assert set(cat.ops) == {"cat.open", "cat.lend", "cat.check", "cat.couple.X", "cat.couple.Z"}
    for k, op in cat.ops.items():
        assert op.status == "verified", k
        assert op.logic["flows"]["complete"], k
    assert cat.ops["cat.lend"].transfer("in_l", "in").count == ROUNDS
    assert cat.ops["cat.check"].transfer("out_l", "out").count == ROUNDS
    depot = leaves.place("depot9x")
    for k in ("load.1", "load.8", "load.9", "dump.9", f"load.{ROUNDS}", f"dump.{ROUNDS}"):
        assert depot.ops[k].status == "verified", k


def test_a_product_over_five_blocks_by_cat_states_is_signed_off(xzxzx, leaves, machine):
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.logic.flat import sign_off
    from qccd.gadget.place_checks import check_places
    alg, gir = xzxzx
    lib, fp = machine
    assert gir["refused"] == []
    ops = [e[4] for e in gir["events"]]
    assert ops.count("cat.open") == 5 and ops.count("cat.lend") == 4 and ops.count("cat.check") == 4
    assert ops.count("cat.couple.X") == 3 and ops.count("cat.couple.Z") == 2
    assert ops.count("dump.3") == 4
    assert len({e[3] for e in gir["events"] if e[4].startswith("cat.couple")}) == 5
    (o,) = [x for x in gir["outcomes"] if x.get("parts")]
    assert o["cvar"] == "m" and len(o["parts"]) == 5                # round 0's probe bits
    ck = check_places(gir, lib, leaves)
    assert not [r for r, v in ck.items() if isinstance(v, dict) and v.get("passed") is False]
    rules = judge_schedule(lib, leaves, gir, regions=fp.regions)
    assert not [k for k, v in rules.items() if isinstance(v, dict) and v.get("passed") is False]
    so = sign_off(alg, gir, lib, leaves)
    rel = {r["relation"]: r["ok"] for r in so["relations"]}
    assert rel.get("m=ppm = 0") is True and all(rel.values()), so["relations"]
    assert so["distance"]["distance"] == 2, so["distance"]          # the four-door bench's


def test_the_cats_are_made_in_parallel(xzxzx, leaves, machine):
    _, two = _run(leaves, machine, "XZ")
    assert _span(xzxzx[1]) < 1.5 * _span(two), (_span(xzxzx[1]), _span(two))


def test_the_checks_flips_are_frames_and_the_sign_off_proves_them(frames, leaves, machine):
    from qccd.gadget.logic.flat import sign_off
    alg, gir = frames
    lib, _ = machine
    assert gir["refused"] == []
    so = sign_off(alg, gir, lib, leaves)
    rel = {r["relation"]: r["ok"] for r in so["relations"]}
    assert {f"r0=Z̄[q0] ⊕ r{j}=Z̄[q{j}] = 0" for j in range(1, 5)} <= set(rel), so["relations"]
    assert all(rel.values()), so["relations"]
    assert so["distance"]["distance"] == 1, so["distance"]          # see the module's last point


def test_left_out_bits_or_frames_make_the_schedule_wrong(xzxzx, frames, leaves, machine):
    from qccd.gadget.logic.flat import sign_off
    lib, _ = machine
    alg, gir = xzxzx
    bad = copy.deepcopy(gir)
    (o,) = [x for x in bad["outcomes"] if x.get("parts")]
    o["parts"] = o["parts"][1:]
    so = sign_off(alg, bad, lib, leaves, distance=False)
    assert so["passed"] is False
    assert {r["relation"]: r["ok"] for r in so["relations"]}.get("m=ppm = 0") is not True
    alg, gir = frames
    ppm = next(i.index for i in alg.instructions if i.kind == "ppm")
    bad = copy.deepcopy(gir)
    kept = [f for f in bad["frames"] if not (f.get("kind") == "pauli" and f.get("instruction") == ppm)]
    assert len(kept) < len(bad["frames"])
    bad["frames"] = kept
    so = sign_off(alg, bad, lib, leaves, distance=False)
    assert so["passed"] is False and not all(r["ok"] for r in so["relations"])


def test_a_wide_product_needs_cat_benches(leaves):
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import schedule
    from qccd.gadget.floorplan import load_file
    lib, _ = load_file(REPO / "examples/gadgets/floorplans/fullstack.floor", leaves)
    src = "\n".join([f"prep q{i} Z" for i in range(5)] + ["ppm " + " ".join(f"Z q{i}" for i in range(5)) + " -> m"])
    gir = schedule(parse_algorithm(src + "\n"), lib, leaves, log=None, parallel=True)
    assert any("cat bench" in why for _, why in gir["refused"]), gir["refused"]
