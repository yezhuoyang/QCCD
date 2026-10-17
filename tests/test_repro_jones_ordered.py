"""Exact source order under independent Jones transport, in both memory bases."""
from dataclasses import replace
import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest

from qccd.repro.importers import jones_device
from qccd.repro.models import JONES, jones_duration
from qccd.repro.sched_jones import stim_program
from qccd.repro.sched_jones_ordered import build
from qccd.repro.timed import check


def _build(d):
    base = Path(__file__).resolve().parents[1] / "Reproduce/jones2025"
    with gzip.open(base / f"artifact/grid_rot_d{d}_c2.schedule.json.gz", "rt") as f:
        raw = json.load(f)
    chains = {f"T{t}": [str(i) for i in ch] for t, ch in raw["initial_layout"].items()}
    circuit = base / f"circuits/rotated_memory_z_d{d}_r1.stim"
    return build(jones_device(raw["device"]), chains, raw["qubit_to_ion"], circuit), circuit


@pytest.mark.parametrize("d", [2, 3, 6, 12])
def test_ordered_grid_exact_sequence_and_both_bases(d, tmp_path):
    sched, circuit = _build(d)
    cx, _, _ = stim_program(circuit)
    report = check(sched, JONES, duration=jones_duration, circuit=cx, strict=True)
    assert report.ok, report.summary()
    assert report.checks["broadcast"].startswith("skipped:")
    assert check(sched, replace(JONES, circuit_order="strict"),
                 duration=jones_duration, circuit=cx, strict=True).ok
    assert report.metrics["last_start_us"] == 3830
    assert report.metrics["makespan_us"] == 3880
    assert not any("class" in e.meta for e in sched.events)
    assert all(e.meta.get("locks") for e in sched.events)
    # Non-broadcast freedom is exercised: a gate overlaps another ion's move
    # in the same middle step, and transports have different start times.
    gates = [e for e in sched.events if e.kind == "gate" and e.meta["step"] == 2]
    moves = [e for e in sched.events if e.kind in ("split", "merge", "j_enter", "j_exit")
             and e.meta["step"] == 2]
    assert any(g.t0 < m.t1 and m.t0 < g.t1 for g in gates for m in moves)
    assert len({e.t0 for e in moves if e.kind == "split" and not e.meta["returning"]}) > 1
    home = {i: t for t, ch in sched.chains.items() for i in ch}
    where = dict(home)
    for e in sched.events:
        if e.kind == "merge":
            where[e.ions[0]] = e.at
    assert where == home
    path = tmp_path / "ordered.schedule.json.gz"
    with gzip.open(path, "wt") as f:
        json.dump(sched.to_json(), f)
    for basis in ("z", "x"):
        source = circuit.with_name(f"rotated_memory_{basis}_d{d}_r1.stim")
        result = subprocess.run([sys.executable, str(circuit.parents[3] / "tools/order.py"),
                                 str(path), str(source)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "executed order matches the source on every qubit" in result.stdout
        assert f"executed: detectors deterministic; circuit distance = {d}" in result.stdout
        assert "RESULT: PASS" in result.stdout


def test_ordered_checker_finds_duration_lock_and_order_faults():
    sched, circuit = _build(3)
    cx, _, _ = stim_program(circuit)
    gate = next(e for e in sched.events if e.kind == "gate")
    longer = replace(gate, t1=gate.t1 + 1)
    broken = replace(sched, events=[longer if e.id == gate.id else e for e in sched.events])
    assert check(broken, JONES, duration=jones_duration, circuit=cx, strict=True).failed("durations")
    splits = [e for e in sched.events if e.kind == "split" and e.t0 == 60]
    bad = replace(splits[1], meta={**splits[1].meta, "locks": splits[0].meta["locks"]})
    broken = replace(sched, events=[bad if e.id == bad.id else e for e in sched.events])
    assert check(broken, JONES, duration=jones_duration, circuit=cx, strict=True).failed("declared_locks")
    partner = gate.ions[0]
    later = next(e for e in reversed(sched.events) if e.kind == "gate" and partner in e.ions)
    broken = replace(sched, events=[replace(e, t0=later.t0, t1=later.t1) if e.id == gate.id
                                   else replace(e, t0=gate.t0, t1=gate.t1) if e.id == later.id
                                   else e for e in sched.events])
    assert check(broken, replace(JONES, circuit_order="strict"),
                 duration=jones_duration, circuit=cx, strict=True).failed("circuit")
