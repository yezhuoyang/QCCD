"""Replay recorded WISE global moves, including the artifact's circuit failures.

No artifact implementation or reconfiguration formula is included here.
The dumped operationTime is recorded, never an independent duration oracle.
"""
from dataclasses import replace
import gzip
import json
from pathlib import Path

import pytest

from qccd.repro.importers import import_jones, stim_cx
from qccd.repro import catalog
from qccd.repro.__main__ import check_run
from qccd.repro.models import JONES, JONES_WISE, JONES_WISE_ARTIFACT, QCCDSIM, jones_duration
from qccd.repro.timed import Event, check


BASE = Path(__file__).resolve().parents[1] / "Reproduce/jones2025"


def _load(distance, tmp_path):
    with gzip.open(BASE / f"artifact/wise_rot_d{distance}_c2.schedule.json.gz", "rt") as f:
        raw = json.load(f)
    path = tmp_path / "wise.json"
    path.write_text(json.dumps(raw))
    return import_jones(path), raw


@pytest.mark.parametrize("distance,last_start,makespan,ops,reconfig_us", [
    (2, 14280, 14680, 69, 3055),
    (3, 19975, 20025, 182, 4295),
    (6, 30270, 30320, 853, 6775),
])
def test_wise_artifact_matches_tool_without_hiding_circuit_failure(
        distance, last_start, makespan, ops, reconfig_us, tmp_path):
    sched, raw = _load(distance, tmp_path)
    circuit = stim_cx(BASE / f"circuits/rotated_memory_z_d{distance}_r1.stim")
    rep = check(sched, JONES_WISE_ARTIFACT, duration=jones_duration, circuit=circuit, strict=True)
    assert rep.metrics["last_start_us"] == last_start
    assert rep.metrics["makespan_us"] == makespan
    assert rep.metrics["events"] == ops == raw["metrics"]["process_circuit_results"]["Operations"]["Forwarding"]
    assert last_start == pytest.approx(raw["metrics"]["process_circuit_results"]["ElapsedTime"]["Forwarding"] * 1e6)
    assert rep.summary()["violations"] == {"circuit": 1}
    assert all(result == "passed" for name, result in rep.checks.items()
               if name not in ("circuit", "broadcast", "durations", "reconfigure_paths"))
    assert rep.checks["durations"].startswith("not checked:")
    assert rep.checks["reconfigure_paths"].startswith("not checked:")
    assert rep.checks["broadcast"].startswith("skipped:")
    global_moves = [e for e in sched.events if e.kind == "reconfigure"]
    assert len(global_moves) == 4
    for event in global_moves:
        assert event.duration == reconfig_us
        assert event.meta["recorded_duration_us"] == pytest.approx(reconfig_us)
        assert set(event.ions) == {i for ch in event.meta["chains_before"].values() for i in ch}
        assert set(event.ions) == {i for ch in event.meta["chains_after"].values() for i in ch}
        assert {f"ion:{i}" for i in event.ions} <= set(event.meta["locks"])
        assert {f"trap:{t[1:]}" for t in event.meta["chains_after"]} <= set(event.meta["locks"])
    # Reconfiguration alone preserves the recorded state; paths/timing are not certified.
    first = global_moves[0]
    prefix = replace(sched, events=[e for e in sched.events if e.t1 <= first.t1])
    physical = check(prefix, JONES_WISE_ARTIFACT, duration=jones_duration, strict=True)
    assert physical.ok, physical.summary()


def test_recorded_global_state_and_duration_faults_are_rejected(tmp_path):
    sched, _ = _load(2, tmp_path)
    event = next(e for e in sched.events if e.kind == "reconfigure")

    def report(bad):
        return check(replace(sched, events=[bad]), JONES_WISE_ARTIFACT, duration=jones_duration, strict=True)

    before = {t: list(ch) for t, ch in event.meta["chains_before"].items()}
    nonempty = [t for t in before if before[t]]
    before[nonempty[0]], before[nonempty[1]] = before[nonempty[1]], before[nonempty[0]]
    assert report(replace(event, meta={**event.meta, "chains_before": before})).failed("positions")
    after = {t: list(ch) for t, ch in event.meta["chains_after"].items()}
    after[next(t for t in after if after[t])].pop()
    assert report(replace(event, meta={**event.meta, "chains_after": after})).failed("structure")
    changed_time = report(replace(event, t1=event.t1 + 1))
    assert not changed_time.failed("durations")
    assert changed_time.checks["durations"].startswith("not checked:")
    after = {t: list(ch) for t, ch in event.meta["chains_after"].items()}
    full = [t for t, ch in after.items() if len(ch) == 2]
    after[full[0]].append(after[full[1]].pop())
    assert report(replace(event, meta={**event.meta, "chains_after": after})).failed("capacity")


def test_global_move_excludes_every_held_trap_and_ion(tmp_path):
    sched, _ = _load(2, tmp_path)
    event = next(e for e in sched.events if e.kind == "reconfigure")
    for trap, ions in event.meta["chains_after"].items():
        for ion in ions:
            gate = Event(10000, "gate1", event.t0 + 1, event.t0 + 6, (ion,), at=trap,
                         meta={"op": "XRotation", "locks": [f"trap:{trap[1:]}", f"ion:{ion}"]})
            rep = check(replace(sched, events=[event, gate]), JONES_WISE_ARTIFACT, duration=jones_duration, strict=True)
            assert rep.failed("declared_locks")
            assert rep.failed("trap_serial")
            assert rep.failed("ion_serial")


@pytest.mark.parametrize("distance", [2, 3, 6])
def test_wise_catalog_replay_has_exact_tool_comparison(distance):
    paper = catalog.paper("jones2025")
    run = next(r for r in paper["runs"] if r["id"] == f"wise_rot_d{distance}_c2")
    row = check_run(paper, run)
    assert row["compare"]["tool"]["last_start_us"]["verdict"] == "exact"
    assert row["violations"] == {"circuit": 1}


def test_zero_time_disconnected_reconfiguration_is_rejected():
    from qccd.repro.timed import PortGraph, TimedSchedule
    dev = PortGraph({"A": {"capacity": 1}, "B": {"capacity": 1}})
    event = Event(0, "reconfigure", 0, 0, ("i",), meta={
        "chains_before": {"A": ["i"], "B": []},
        "chains_after": {"A": [], "B": ["i"]}, "recorded_duration_us": 0})
    sched = TimedSchedule("disconnected", dev, {"A": ["i"], "B": []}, [event])
    rep = check(sched, JONES_WISE_ARTIFACT, duration=jones_duration)
    assert not rep.ok
    assert "positive" in rep.failed("structure")[0].message
    assert rep.checks["durations"].startswith("not checked:")
    assert rep.checks["reconfigure_paths"].startswith("not checked:")


@pytest.mark.parametrize("profile", [JONES, JONES_WISE, QCCDSIM])
@pytest.mark.parametrize("strict", [False, True])
def test_ordinary_profiles_reject_global_reconfiguration(profile, strict, tmp_path):
    sched, _ = _load(2, tmp_path)
    event = next(e for e in sched.events if e.kind == "reconfigure")
    rep = check(replace(sched, events=[event]), profile, duration=jones_duration, strict=strict)
    assert not rep.ok
    assert any("does not allow reconfigure" in v.message for v in rep.failed("structure"))


def _junction_overlap_schedule():
    from qccd.repro.timed import PortGraph, TimedSchedule
    dev = PortGraph({"A": {"capacity": 1, "ports": {"s": "R"}},
                     "B": {"capacity": 1, "ports": {"t": "L"}},
                     "C": {"capacity": 1}}, {"J": {}},
                    {"s": ("A", "J"), "t": ("J", "B")})
    events = [Event(0, "split", 0, 80, ("traveller",), at="A", seg="s"),
              Event(1, "reconfigure", 80, 200, ("held",), meta={
                  "chains_before": {"C": ["held"]}, "chains_after": {"C": ["held"]}}),
              Event(2, "j_enter", 85, 135, ("traveller",), src="s", via="J"),
              Event(3, "j_exit", 135, 185, ("traveller",), dst="t", via="J"),
              Event(4, "merge", 200, 280, ("traveller",), at="B", seg="t")]
    return TimedSchedule("global junction exclusion", dev,
                         {"A": ["traveller"], "B": [], "C": ["held"]}, events)


def test_global_hold_excludes_junction_crossing_by_an_unaffected_ion():
    sched = _junction_overlap_schedule()
    rep = check(sched, JONES_WISE_ARTIFACT, duration=jones_duration)
    assert rep.failed("global_hold")
    assert {v.events for v in rep.failed("global_hold")} == {(1, 2), (1, 3)}
    # Without the global hold this is an otherwise valid traversal; touching
    # boundaries at 80/200 us are allowed, and no shared-ion lock detects it.
    assert not rep.failed("ion_serial")
    assert not rep.failed("positions")
    assert check(replace(sched, events=[e for e in sched.events if e.kind != "reconfigure"]),
                 JONES, duration=jones_duration).ok
