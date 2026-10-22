"""`qccd.repro.opt_cyclone`: Cyclone's round, re-planned under Cyclone's own model, in the
circuit's own order.

The writer must reproduce the artifact's numbers from the artifact's own plan, run by the
artifact's own loop, before any number of ours means anything: 124,140 us for [[225,9,6]]
with the Z checks the code really has, and the printed 123,140 us from the plan the
unpatched compiler ran (every Z check a copy of the last X check) -- which the checker and
the stim experiment both reject.

Our plans run in strict order: every ancilla runs its check's CX gates in the order the
circuit lists them, and every data qubit meets its checks in the order the circuit lists
them (the user's rule of 2026-10-19: a compiler never reorders the input circuit, not even
gates that commute).  The artifact's loop does not, and the checker says so.
"""

from __future__ import annotations

import dataclasses
import gzip
import importlib.util
import json
import os
import random
from pathlib import Path

import pytest

from qccd.repro import opt_cyclone as oc
from qccd.repro.models import CYCLONE, cyclone_duration
from qccd.repro.timed import TimedSchedule, check

HAS_SAT = importlib.util.find_spec("pysat") is not None

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

ROOT = Path(__file__).resolve().parents[1] / "Reproduce" / "khan2026cyclone"


def _roundtrip(sched: TimedSchedule, tmp_path: Path) -> TimedSchedule:
    f = sched.save(tmp_path / f"{sched.name}.json")
    return TimedSchedule.load(f)


def _per_qubit(pairs) -> dict[str, list[tuple[str, str]]]:
    """Each qubit's two-qubit gates, in order, as (role, partner)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for c, t in pairs:
        out.setdefault(c, []).append(("c", t))
        out.setdefault(t, []).append(("t", c))
    return out


def _executed(sched: TimedSchedule):
    return [tuple(e.ions) for e in sorted(sched.events, key=lambda e: (e.t0, e.id)) if e.kind == "gate"]


def _small_plan(name="hamming"):
    """A strict plan for HGP(H7, H7) without a solver: ancilla k starts in trap -k and the
    data sit in index order (the writer's propagation makes any layout a legal round)."""
    hx, hz = oc.hgp(H7, H7)
    n = 1 + max(max(r) for r in hx + hz)
    x = len(hx)
    pos = [(d * x) // n for d in range(n)]
    plan = oc.Plan(name=name, n=n, x=x, capacity=5, hx=hx, hz=hz, data_trap=pos,
                   anc_start=[(-k) % x for k in range(x)], x_rows=list(range(x)),
                   z_rows=list(range(x)))
    return n, hx, hz, plan


# ------------------------------------------------------------- the artifact's own loop


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
    assert plan.order == "artifact"
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


def test_the_artifacts_loop_reorders_the_circuit():
    """Its round computes the right syndrome, but no qubit keeps the circuit's order."""
    hx, hz = oc.hgp(H12, H12)
    plan = oc.naive_plan("hgp225_naive", 225, hx, hz, x=108, capacity=5)
    s = oc.write(plan)
    strict = dataclasses.replace(CYCLONE, circuit_order="strict")
    rep = check(s, strict, duration=cyclone_duration, circuit=oc.circuit_for(plan, hx, hz))
    assert rep.checks["circuit"] == "failed"
    # ... and the same layout run in the circuit's order is a legal, slower round
    mine = dataclasses.replace(plan, order="strict")
    v = oc.verify(oc.write(mine), mine, hx, hz, stim_shots=4)
    assert v["ok"], v["report"].summary()
    assert v["makespan_us"] > 124140


# ------------------------------------------------------------------- the strict order


def test_a_strict_round_keeps_every_qubits_input_order(tmp_path):
    n, hx, hz, plan = _small_plan()
    s = _roundtrip(oc.write(plan), tmp_path)
    want = _per_qubit(oc.circuit_for(plan, hx, hz))
    assert _per_qubit(_executed(s)) == want                 # independent of the checker
    v = oc.verify(s, plan, hx, hz)
    assert v["ok"], (v["report"].summary(), v["stim"])
    assert v["report"].checks["circuit"] == "passed"
    # the bill is the model's: two H layers and, per step, one gate layer per CX the busiest
    # trap does (at least one) + the 3g swap + split 80 + move 5 + merge 80
    c = oc.plan_cost(plan)
    steps = c["steps_X"] + c["steps_Z"]
    assert v["makespan_us"] == c["time_us"] == 200 + 465 * steps + 100 * (c["sum_P_X"] + c["sum_P_Z"])


def test_strict_steps_wait_a_rotation_rather_than_reorder():
    # one ancilla per trap on a ring of 3 (ancilla k starts in trap k, moving +1 a step);
    # check 0 lists d0 (trap 1) then d1 (trap 0): ancilla 0 passes d1 first, may not touch
    # it yet, does d0 at step 1 and comes round to d1 at step 3
    steps = oc._strict_steps(3, [1, 0, 2], [[0, 1], [2], [2]], [0, 1, 2])
    times = {(k, d): t for t, now in enumerate(steps) for k, ds in enumerate(now) for d in ds}
    assert times[(0, 0)] == 1 and times[(0, 1)] == 3
    # d2 (trap 2) sits where ancilla 2 starts, but must meet check 1 first (step 1), so
    # ancilla 2 does it when it is back, at step 3; the artifact's loop would do it at 0
    assert times[(1, 2)] == 1 and times[(2, 2)] == 3
    assert len(steps) == 4


def test_a_strict_plan_keeps_each_check_on_its_ancilla():
    n, hx, hz, plan = _small_plan()
    p = dataclasses.replace(plan, x_rows=[1, 0] + list(range(2, plan.x)))
    with pytest.raises(ValueError):
        oc.write(p)
    # the artifact's loop may carry its own assignment (it replays the artifact's plan)
    oc.write(dataclasses.replace(p, order="artifact", z_rows=list(p.x_rows)))


def test_a_plan_survives_json(tmp_path):
    plan = _small_plan()[3]
    plan.save(tmp_path / "p.json")
    back = oc.Plan.load(tmp_path / "p.json")
    assert back == plan and back.order == "strict"


def test_tampering_is_caught():
    _, hx, hz, plan = _small_plan()
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
    # two gates of one data qubit run the other way round: the strict circuit check refuses
    # it although the round still measures every stabilizer
    xs = [e for e in gates if e.meta["phase"] == "X"]
    d = next(e.ions[1] for e in xs if sum(f.ions[1] == e.ions[1] for f in xs) >= 2)
    mine = [e for e in xs if e.ions[1] == d][:2]
    a, b = mine
    swapped = dataclasses.replace(s, events=[
        dataclasses.replace(e, ions=(b.ions[0], d)) if e is a else
        dataclasses.replace(e, ions=(a.ions[0], d)) if e is b else e for e in s.events])
    assert oc.verify(swapped, plan, hx, hz, stim_shots=4)["report"].checks["circuit"] == "failed"
    # a data qubit placed where its trap is already full
    full = dataclasses.replace(plan, capacity=2)
    with pytest.raises(ValueError):
        oc.write(full)


def test_polish_only_improves_and_prices_like_the_writer():
    _, hx, hz, plan = _small_plan()
    before = oc.plan_cost(plan)["time_us"]
    better = oc.polish(plan, moves=3000, log=lambda m: None)
    after = oc.plan_cost(better)
    assert after["time_us"] <= before
    assert better.order == "strict" and better.x_rows == plan.x_rows
    v = oc.verify(oc.write(better), better, hx, hz, stim_shots=4)
    assert v["ok"] and v["makespan_us"] == after["time_us"]


# ------------------------------------------------------------------------ the solver


@pytest.mark.skipif(not HAS_SAT, reason="python-sat is not installed")
def test_sat_agrees_with_the_propagation_on_small_rings():
    """Every layout the solver returns runs in the windows it was asked for, and a layout the
    propagation runs in exactly L steps is found at L and refused at L - 1."""
    rng = random.Random(5)
    seen = 0
    for _ in range(400):
        x = rng.randint(3, 5)
        n = rng.randint(x, 2 * x)
        hx = [rng.sample(range(n), rng.randint(1, 3)) for _ in range(x)]
        hz = [rng.sample(range(n), rng.randint(1, 3)) for _ in range(x)]
        if sorted({d for r in hx + hz for d in r}) != list(range(n)):
            continue
        pos = [rng.randrange(x) for _ in range(n)]
        if max(pos.count(t) for t in range(x)) > 3:
            continue
        st = rng.sample(range(x), x)
        plan = oc.Plan("r", n, x, 4, hx, hz, pos, st, list(range(x)), list(range(x)))
        c = oc.plan_cost(plan)
        kx, kz = c["steps_X"], c["steps_Z"]
        if kx > 3 * x or kz > 3 * x:
            continue
        # this layout fits (kx, kz), so the solver must find one that does, with these starts
        ok, lay = oc.sat_layout(n, hx, hz, 4, kx, kz, starts=st)
        assert ok, (hx, hz, pos, st)
        assert oc.plan_cost(dataclasses.replace(plan, data_trap=list(lay[0])))["steps_X"] <= kx
        # and with the starts free too
        ok, lay = oc.sat_layout(n, hx, hz, 4, kx, kz, x_only=True)
        assert ok
        got = oc.plan_cost(dataclasses.replace(plan, data_trap=list(lay[0]), anc_start=list(lay[1])))
        assert got["steps_X"] <= kx
        seen += 1
    assert seen > 20


@pytest.mark.skipif(not HAS_SAT, reason="python-sat is not installed")
def test_sat_refuses_what_no_layout_can_do():
    # three checks on one data qubit: it meets them one per step, in order, whatever the
    # layout, so the X phase needs 3 steps
    hx = [[0], [0], [0]]
    hz = [[0], [0], [0]]
    assert oc.sat_layout(1, hx, hz, 3, 2, 3, x_only=True)[0] is False
    ok, (pos, st) = oc.sat_layout(1, hx, hz, 3, 3, 3, x_only=True)
    assert ok
    plan = oc.Plan("t", 1, 3, 3, hx, hz, list(pos), list(st), [0, 1, 2], [0, 1, 2])
    assert oc.plan_cost(plan)["steps_X"] == 3
    # with ancilla k fixed in trap k (moving +1 a step) they reach any trap in the wrong
    # order (2 before 1 before 0 ... ), so a rotation is lost: the best trap, 0, needs 5
    assert oc.sat_layout(1, hx, hz, 3, 4, 4, starts=[0, 1, 2], x_only=True)[0] is False
    ok, (pos, _) = oc.sat_layout(1, hx, hz, 3, 5, 5, starts=[0, 1, 2], x_only=True)
    assert ok and pos == [0]
    assert oc.plan_cost(dataclasses.replace(plan, data_trap=list(pos), anc_start=[0, 1, 2]))["steps_X"] == 5


@pytest.mark.skipif(not HAS_SAT, reason="python-sat is not installed")
def test_optimize_returns_a_strict_round_that_passes_every_check():
    hx, hz = oc.hgp(H7, H7)
    n = 1 + max(max(r) for r in hx + hz)
    plan = oc.optimize("hamming", n, hx, hz, capacity=5, starts=["reversed"], time_limit=60,
                       kx_grid=(1.0, 1.5), polish_moves=2000, log=lambda m: None)
    assert plan.order == "strict" and plan.x_rows == list(range(len(hx)))
    naive = _small_plan()[3]
    assert oc.plan_cost(plan)["time_us"] <= oc.plan_cost(naive)["time_us"]
    s = oc.write(plan)
    v = oc.verify(s, plan, hx, hz)
    assert v["ok"], (v["report"].summary(), v["stim"])
    assert _per_qubit(_executed(s)) == _per_qubit(oc.circuit_for(plan, hx, hz))


# ------------------------------------------------------------------ the published runs


@pytest.mark.parametrize("code", ["hgp225", "hgp375", "hgp625", "bb72", "bb90", "bb144"])
def test_every_published_round_of_ours_keeps_the_circuits_order(code):
    pairs = json.loads((ROOT / "circuits" / f"{code}_true_round.json").read_text(encoding="utf-8"))
    pairs = [tuple(p) for p in (pairs["pairs"] if isinstance(pairs, dict) else pairs)]
    s = TimedSchedule.from_json(json.loads(gzip.decompress(
        (ROOT / "ours" / f"{code}_ours.schedule.json.gz").read_bytes())))
    assert s.claims.get("order") == "strict"
    assert _per_qubit(_executed(s)) == _per_qubit(pairs)
    plan = oc.Plan.load(ROOT / "ours" / f"{code}_ours.plan.json")
    n, hx, hz = oc.true_rows(pairs)
    assert (plan.hx, plan.hz) == (hx, hz)
    assert oc.write(plan, name=s.name).to_json() == s.to_json()     # the plan IS the schedule
    strict = dataclasses.replace(CYCLONE, circuit_order="strict")
    rep = check(s, strict, duration=cyclone_duration, circuit=pairs, strict=True)
    assert rep.ok, rep.summary()


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
