"""`qccd.repro.opt_cyclone`: Cyclone's round, re-planned under Cyclone's own model.

The writer must reproduce the artifact's numbers from the artifact's own plan before any
optimized number means anything: 124,140 us for [[225,9,6]] with the Z checks the code
really has, and the printed 123,140 us from the plan the unpatched compiler ran (every Z
check a copy of the last X check) -- which the checker and the stim experiment both reject.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
from pathlib import Path

import pytest

from qccd.repro import opt_cyclone as oc
from qccd.repro.timed import TimedSchedule

# The 9 x 12 parity-check matrix the artifact builds [[225,9,6]] from (QUITS
# `n=12_dv=3_dc=4_dist=6.txt`); the code is HgpCode(H12, H12).
H12 = [[int(c) for c in row.split()] for row in """
0 0 1 1 0 1 0 0 0 0 1 0
0 1 0 1 0 0 1 0 0 0 0 1
0 0 0 0 0 0 0 1 1 0 1 1
0 0 0 0 1 0 1 0 0 1 1 0
1 0 0 0 0 1 0 0 0 1 0 1
0 1 1 0 0 0 0 0 1 1 0 0
1 0 0 1 1 0 0 0 1 0 0 0
0 1 0 0 1 1 0 1 0 0 0 0
1 0 1 0 0 0 1 1 0 0 0 0
""".strip().splitlines()]

# the [7,4] Hamming code: HGP(H7, H7) is a 58-qubit code with 21 X and 21 Z checks
H7 = [[1, 0, 1, 0, 1, 0, 1], [0, 1, 1, 0, 0, 1, 1], [0, 0, 0, 1, 1, 1, 1]]


def _roundtrip(sched: TimedSchedule, tmp_path: Path) -> TimedSchedule:
    f = sched.save(tmp_path / f"{sched.name}.json")
    return TimedSchedule.load(f)


def test_hgp_rows_are_a_css_code_with_the_artifacts_shape():
    hx, hz = oc.hgp(H12, H12)
    assert len(hx) == len(hz) == 108
    assert {len(r) for r in hx} == {len(r) for r in hz} == {7}
    assert max(max(r) for r in hx + hz) == 224
    for a in hx:
        for b in hz:
            assert len(set(a) & set(b)) % 2 == 0


def test_the_writer_replays_the_artifacts_225_round_to_124140_us(tmp_path):
    hx, hz = oc.hgp(H12, H12)
    plan = oc.naive_plan("hgp225_naive", 225, hx, hz, x=108, capacity=5)
    s = _roundtrip(oc.write(plan), tmp_path)
    assert (s.claims["steps_X"], s.claims["steps_Z"]) == (108, 108)
    assert (s.claims["sum_P_X"], s.claims["sum_P_Z"]) == (117, 118)
    v = oc.verify(s, plan, hx, hz, stim_shots=4)
    assert v["report"].ok, v["report"].summary()
    assert v["stim"]["ok"], v["stim"]
    assert v["makespan_us"] == 124140
    assert oc.plan_cost(plan)["time_us"] == 124140


def test_the_printed_123140_us_is_the_z_check_bug_and_both_checks_reject_it(tmp_path):
    hx, hz = oc.hgp(H12, H12)
    plan = oc.naive_plan("hgp225_printed", 225, hx, hz, x=108, capacity=5,
                         hz_executed=[list(hx[-1]) for _ in hz])
    s = _roundtrip(oc.write(plan), tmp_path)
    v = oc.verify(s, plan, hx, hz, stim_shots=4)
    assert v["makespan_us"] == 123140
    assert v["report"].checks["circuit"] == "failed"
    assert v["report"].checks["durations"] == "passed"      # the clock is right; the circuit is not
    assert not v["stim"]["ok"]


@pytest.fixture(scope="module")
def small():
    hx, hz = oc.hgp(H7, H7)
    n = 1 + max(max(r) for r in hx + hz)
    plan = oc.optimize("hamming_hgp", n, hx, hz, capacity=5, seed=3, budget_s=20,
                       moves=20_000, bill_moves=20_000, log=lambda m: None)
    return n, hx, hz, plan


def test_an_optimized_round_is_shorter_and_passes_every_check(small, tmp_path):
    n, hx, hz, plan = small
    naive = oc.plan_cost(oc.naive_plan("hamming_naive", n, hx, hz, x=len(hx), capacity=5))
    got = oc.plan_cost(plan)
    assert got["steps_X"] < len(hx) and got["steps_Z"] < len(hz)
    assert got["time_us"] < naive["time_us"]
    s = _roundtrip(oc.write(plan), tmp_path)
    v = oc.verify(s, plan, hx, hz)
    assert v["report"].ok, v["report"].summary()
    assert v["stim"]["ok"], v["stim"]
    # the bill is the model's: two H layers and, per step, one gate layer per CX the busiest
    # trap does (at least one) + the 3g swap + split 80 + move 5 + merge 80
    steps = got["steps_X"] + got["steps_Z"]
    assert v["makespan_us"] == got["time_us"] == 200 + 465 * steps + 100 * (got["sum_P_X"] + got["sum_P_Z"])
    # every check went to exactly one ancilla, and the circuit is the code's true checks
    assert sorted(plan.x_rows) == list(range(len(hx))) and sorted(plan.z_rows) == list(range(len(hz)))


def test_a_plan_survives_json(small, tmp_path):
    plan = small[3]
    plan.save(tmp_path / "p.json")
    back = oc.Plan.load(tmp_path / "p.json")
    assert back == plan


def test_tampering_is_caught(small):
    _, hx, hz, plan = small
    s = oc.write(plan)
    gates = [e for e in s.events if e.kind == "gate"]
    # a CX dropped: the circuit check and the stim experiment both notice
    dropped = dataclasses.replace(s, events=[e for e in s.events if e is not gates[5]])
    v = oc.verify(dropped, plan, hx, hz, stim_shots=4)
    assert v["report"].checks["circuit"] == "failed" and not v["stim"]["ok"]
    # a CX said to run in a trap its ions are not in
    g = gates[0]
    other = f"T{(int(g.at[1:]) + 1) % plan.x}"
    moved = dataclasses.replace(s, events=[dataclasses.replace(e, at=other) if e is g else e
                                           for e in s.events])
    assert oc.verify(moved, plan, hx, hz, stim_shots=4)["report"].checks["positions"] == "failed"
    # a data qubit placed where its trap is already full
    full = dataclasses.replace(plan, capacity=2)
    with pytest.raises(ValueError):
        oc.write(full)


def test_exact_starts_is_the_shortest_window_with_one_start_per_trap(small):
    n, hx, _, plan = small
    x = plan.x
    k, starts = oc.exact_starts(plan.data_trap, hx, x)
    assert sorted(starts) == list(range(x))
    for r, s in zip(hx, starts):
        assert all((plan.data_trap[d] - s) % x < k for d in r)
    assert k <= oc.plan_cost(plan)["steps_X"]
    with pytest.raises(RuntimeError):
        oc.exact_starts([0] * n, hx, x, k_min=x + 1)


def _bb72() -> tuple[int, list[list[int]], list[list[int]]]:
    """[[72,12,6]]: A = x^3 + y + y^2, B = y^3 + x + x^2 on Z6 x Z6, in the labelling of
    IBM's circuit: L(i, j) = 6i + j, R(i, j) = 36 + 6i + j, X check (i, j) is row 6i + j."""
    L = lambda i, j: 6 * (i % 6) + j % 6
    R = lambda i, j: 36 + L(i, j)
    hx = [sorted([L(i + 3, j), L(i, j + 1), L(i, j + 2), R(i, j + 3), R(i + 1, j), R(i + 2, j)])
          for i in range(6) for j in range(6)]
    hz = [sorted([L(i, j - 3), L(i - 1, j), L(i - 2, j), R(i - 3, j), R(i, j - 1), R(i, j - 2)])
          for i in range(6) for j in range(6)]
    return 72, hx, hz


def test_a_bb_round_on_its_torus_layout_is_shorter_and_passes_every_check(tmp_path):
    n, hx, hz = _bb72()
    for a in hx:
        for b in hz:
            assert len(set(a) & set(b)) % 2 == 0
    pos = oc.bb_layout(6, 6, 36, a=1, b=1, di=0, dj=2, i_major=False)
    kx, sx = oc.exact_starts(pos, hx, 36)
    kz, sz = oc.exact_starts(pos, hz, 36)
    plan = oc.plan_from_layout("bb72_torus", n, hx, hz, 3, pos, sx, sz)
    got = oc.plan_cost(plan)
    assert (got["steps_X"], got["steps_Z"]) == (kx, kz) and kx + kz <= 42   # the artifact: 36 + 36
    s = _roundtrip(oc.write(plan), tmp_path)
    v = oc.verify(s, plan, hx, hz)
    assert v["report"].ok and v["stim"]["ok"], (v["report"].summary(), v["stim"])
    assert v["makespan_us"] == got["time_us"] < 42280


@pytest.mark.skipif(importlib.util.find_spec("pysat") is None, reason="python-sat is not installed")
def test_sat_finds_a_layout_at_least_as_short_as_annealing_and_refutes_an_impossible_one(small):
    n, hx, hz, plan = small
    got = oc.plan_cost(plan)
    ok, lay = oc.sat_layout(n, hx, hz, 5, got["steps_X"], got["steps_Z"],
                            hint=oc.layout_of(plan), time_limit=120)
    assert ok is True
    p = oc.plan_from_layout("hamming_sat", n, hx, hz, 5, *lay)
    c = oc.plan_cost(p)
    assert c["steps_X"] <= got["steps_X"] and c["steps_Z"] <= got["steps_Z"]
    # a 1-step X phase needs each X check's whole support (5 to 7 data) in one trap of 4
    ok, _ = oc.sat_layout(n, hx, hz, 5, 1, got["steps_Z"], time_limit=120)
    assert ok is False


OUT = os.environ.get("CYCLONE_OUT")


@pytest.mark.skipif(not OUT or not (Path(OUT) / "hgp225_cyclone_zfix" / "schedule.json").exists(),
                    reason="set CYCLONE_OUT to the Cyclone run's out/ directory")
def test_the_writer_matches_import_cyclone_event_for_event():
    from collections import Counter

    from qccd.repro.importers import import_cyclone

    run = Path(OUT) / "hgp225_cyclone_zfix"
    n, hx, hz = oc.load_hgp_checks(run)
    assert (hx, hz) == oc.hgp(H12, H12)
    plan = oc.artifact_plan(run / "schedule.json", hx, hz)
    assert plan.data_trap == oc.naive_plan("n", n, hx, hz, 108, 5).data_trap
    mine, theirs = oc.write(plan), import_cyclone(run / "schedule.json")
    key = lambda e: (e.kind, e.t0, e.t1, e.ions, e.at, e.seg, e.src, e.dst, e.via)
    assert Counter(key(e) for e in mine.events if e.kind != "gate") == \
        Counter(key(e) for e in theirs.events if e.kind != "gate")
    # gates: the same pairs in the same trap and the same layer slots; within a trap and
    # step the artifact may order an ancilla's gates differently
    pairs = lambda s: Counter((frozenset(e.ions), e.at) for e in s.events if e.kind == "gate")
    slots = lambda s: Counter((e.at, e.t0, e.t1) for e in s.events if e.kind == "gate")
    assert pairs(mine) == pairs(theirs) and slots(mine) == slots(theirs)
    assert json.loads(json.dumps(mine.to_json()))["claims"]["time_us"] == 124140
