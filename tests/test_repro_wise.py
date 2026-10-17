"""WISE must enable the existing broadcast checker without relaxing Jones rules."""
from dataclasses import replace
import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest

from qccd.repro.importers import jones_device
from qccd.repro.models import JONES, JONES_WISE, jones_duration
from qccd.repro.sched_jones import stim_program
from qccd.repro.sched_wise import build
from qccd.repro.timed import Event, PortGraph, TimedSchedule, check


def test_wise_profile_and_broadcast_faults():
    assert replace(JONES_WISE, name=JONES.name, broadcast=()) == JONES
    dev = PortGraph(
        sites={f"T{k}": {"capacity": 2, "ports": {f"C{k}": "R"}} for k in range(2)},
        nodes={f"J{k}": {} for k in range(2)},
        segments={f"C{k}": (f"T{k}", f"J{k}") for k in range(2)},
    )
    events = [Event(k, "split", 0, 80, (str(k),), at=f"T{k}", seg=f"C{k}",
                    meta={"class": "split:R", "locks":
                          [f"ManipulationTrap:{k}", f"Junction:{k}", f"Crossing:{k}"]})
              for k in range(2)]
    returns = [replace(e, id=e.id + 2, kind="merge", t0=100, t1=180,
                       meta={**e.meta, "class": "merge:R"}) for e in events]
    sched = TimedSchedule("broadcast probe", dev, {"T0": ["0"], "T1": ["1"]}, events + returns)
    report = check(sched, JONES_WISE, duration=jones_duration, strict=True)
    assert report.ok, report.summary()
    assert report.checks["broadcast"] == "passed"
    for bad in (
        replace(events[1], meta={**events[1].meta, "class": "split:L"}),
        replace(events[1], t1=81),
        replace(events[1], t0=1, t1=81),
        replace(events[1], meta={"locks": events[1].meta["locks"]}),
    ):
        broken = replace(sched, events=[events[0], bad] + returns)
        assert check(broken, JONES_WISE, duration=jones_duration, strict=True).failed("broadcast")


def _source(distance=3):
    root = Path(__file__).resolve().parents[1] / "Reproduce/jones2025"
    with gzip.open(root / f"artifact/grid_rot_d{distance}_c2.schedule.json.gz", "rt") as f:
        raw = json.load(f)
    chains = {f"T{t}": [str(i) for i in ch] for t, ch in raw["initial_layout"].items()}
    return jones_device(raw["device"]), chains, raw["qubit_to_ion"], root / f"circuits/rotated_memory_z_d{distance}_r1.stim"


def test_wise_first_step_and_planted_faults():
    dev, chains, qubits, stim = _source()
    sched = build(dev, chains, qubits, stim, steps=1)
    cx, _, _ = stim_program(stim)
    report = check(sched, JONES_WISE, duration=jones_duration, circuit=cx[:6], strict=True)
    assert report.ok, report.summary()
    assert report.checks["broadcast"] == "passed"
    assert report.metrics["makespan_us"] == 660
    assert sched.claims["broadcast_cycles"] == 12
    assert sched.claims["transport_us_total"] == 540
    event = next(e for e in sched.events if e.kind == "j_exit")
    bad = replace(event, meta={**event.meta, "class": "j_exit:W"})
    broken = replace(sched, events=[bad if e.id == event.id else e for e in sched.events])
    assert check(broken, JONES_WISE, duration=jones_duration, circuit=cx[:6], strict=True).failed("broadcast")
    dropped = replace(sched, events=[e for e in sched.events if e.kind != "gate"])
    assert check(dropped, JONES_WISE, duration=jones_duration, circuit=cx[:6], strict=True).failed("circuit")
    with pytest.raises(ValueError, match="occupied zones would sit out"):
        build(dev, chains, qubits, stim, steps=1, zone_opt_out=False)


@pytest.mark.parametrize("mode,cycles,transport,makespan", [
    ("split", 72, [540, 1080, 1080, 540], 4120),
    ("shared_shape", 48, [540, 540, 540, 540], 2920),
])
def test_wise_round_and_order(mode, cycles, transport, makespan, tmp_path):
    dev, chains, qubits, stim = _source()
    sched = build(dev, chains, qubits, stim, middle_steps=mode)
    cx, _, _ = stim_program(stim)
    report = check(sched, JONES_WISE, duration=jones_duration, circuit=cx, strict=True)
    assert report.ok, report.summary()
    assert report.checks["broadcast"] == "passed"
    assert sched.claims["broadcast_cycles"] == cycles
    assert sched.claims["transport_us_per_step"] == transport
    assert report.metrics["makespan_us"] == makespan
    assert report.metrics["last_start_us"] == makespan - 50
    assert sched.qubits == {q: str(i) for q, i in qubits.items()}
    assert {t: ch for t, ch in sched.chains.items() if ch} == chains
    assert all(e.meta.get("locks") for e in sched.events)
    assert all(e.meta["class"].startswith(e.kind + ":") for e in sched.events
               if e.kind in JONES_WISE.broadcast)
    assert report.metrics["counts"] == {
        "reset": 25, "gate1": 112, "gate": 24, "measure": 17, "split": 48,
        "merge": 48, "transit": 96, "j_enter": 48, "j_exit": 48,
    }
    # Every last return goes home, so the next pass starts from the source layout.
    where = {i: t for t, ch in chains.items() for i in ch}
    for e in sched.events:
        if e.kind == "merge":
            where[e.ions[0]] = e.at
    assert where == {i: t for t, ch in chains.items() for i in ch}
    path = tmp_path / "wise.schedule.json.gz"
    with gzip.open(path, "wt") as f:
        json.dump(sched.to_json(), f)
    result = subprocess.run([sys.executable, str(stim.parents[3] / "tools/order.py"),
                             str(path), str(stim)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "executed: detectors deterministic; circuit distance = 3" in result.stdout
    assert "RESULT: PASS" in result.stdout
    if mode == "split":
        assert "executed order matches the source on every qubit" in result.stdout
    else:
        assert "qubit 9" in result.stdout and "qubit 18" in result.stdout


def test_wise_combined_junction_class_choice():
    dev, chains, qubits, stim = _source()
    sched = build(dev, chains, qubits, stim, steps=1, separate_junction_classes=False)
    assert {e.meta["class"].split(":")[0] for e in sched.events
            if e.kind in ("j_enter", "j_exit")} == {"junction"}
    assert sched.claims["broadcast_cycles"] == 12


@pytest.mark.parametrize("mode,suffix", [("split", ""), ("shared_shape", "_shared_shape")])
def test_wise_d3_output_unchanged(mode, suffix):
    dev, chains, qubits, stim = _source()
    sched = build(dev, chains, qubits, stim, middle_steps=mode)
    with gzip.open(stim.parents[1] / f"ours/grid_rot_d3_c2_wise{suffix}.schedule.json.gz", "rt") as f:
        assert sched.to_json() == json.load(f)


@pytest.mark.parametrize("distance", [2, 6, 12])
@pytest.mark.parametrize("mode,suffix", [("split", ""), ("shared_shape", "_shared_shape")])
def test_wise_other_grid_artifacts(distance, mode, suffix):
    dev, chains, qubits, stim = _source(distance)
    sched = build(dev, chains, qubits, stim, middle_steps=mode)
    cx, _, _ = stim_program(stim)
    report = check(sched, JONES_WISE, duration=jones_duration, circuit=cx, strict=True)
    assert report.ok, report.summary()
    assert report.checks["broadcast"] == "passed"
    assert not report.violations
    assert sched.device.to_json() == dev.to_json()
    assert sched.qubits == {q: str(i) for q, i in qubits.items()}
    assert {t: ch for t, ch in sched.chains.items() if ch} == chains
    assert len([e for e in sched.events if e.kind == "gate"]) == len(cx)
    assert report.metrics["last_start_us"] == (4070 if mode == "split" else 2870)
    assert sched.claims["broadcast_cycles"] == (72 if mode == "split" else 48)
    with gzip.open(stim.parents[1] / f"ours/grid_rot_d{distance}_c2_wise{suffix}.schedule.json.gz", "rt") as f:
        assert sched.to_json() == json.load(f)
