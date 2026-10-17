"""Replay recorded WISE global moves, including the artifact's circuit failures.

No artifact implementation or reconfiguration formula is included here.
The dumped operationTime is the duration oracle for these opaque operations.
"""
from dataclasses import replace
import gzip
import json
from pathlib import Path

import pytest

from qccd.repro.importers import import_jones, stim_cx
from qccd.repro.models import JONES, jones_duration
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
    rep = check(sched, JONES, duration=jones_duration, circuit=circuit, strict=True)
    assert rep.metrics["last_start_us"] == last_start
    assert rep.metrics["makespan_us"] == makespan
    assert rep.metrics["events"] == ops == raw["metrics"]["process_circuit_results"]["Operations"]["Forwarding"]
    assert last_start == pytest.approx(raw["metrics"]["process_circuit_results"]["ElapsedTime"]["Forwarding"] * 1e6)
    assert rep.summary()["violations"] == {"circuit": 1}
    assert all(result == "passed" for name, result in rep.checks.items()
               if name not in ("circuit", "broadcast"))
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
    # Reconfiguration alone is a faithful, physically valid state transition.
    first = global_moves[0]
    prefix = replace(sched, events=[e for e in sched.events if e.t1 <= first.t1])
    physical = check(prefix, JONES, duration=jones_duration, strict=True)
    assert physical.ok, physical.summary()


def test_recorded_global_state_and_duration_faults_are_rejected(tmp_path):
    sched, _ = _load(2, tmp_path)
    event = next(e for e in sched.events if e.kind == "reconfigure")

    def report(bad):
        return check(replace(sched, events=[bad]), JONES, duration=jones_duration, strict=True)

    before = {t: list(ch) for t, ch in event.meta["chains_before"].items()}
    nonempty = [t for t in before if before[t]]
    before[nonempty[0]], before[nonempty[1]] = before[nonempty[1]], before[nonempty[0]]
    assert report(replace(event, meta={**event.meta, "chains_before": before})).failed("positions")
    after = {t: list(ch) for t, ch in event.meta["chains_after"].items()}
    after[next(t for t in after if after[t])].pop()
    assert report(replace(event, meta={**event.meta, "chains_after": after})).failed("structure")
    assert report(replace(event, t1=event.t1 + 1)).failed("durations")
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
            rep = check(replace(sched, events=[event, gate]), JONES, duration=jones_duration, strict=True)
            assert rep.failed("declared_locks")
            assert rep.failed("trap_serial")
            assert rep.failed("ion_serial")
