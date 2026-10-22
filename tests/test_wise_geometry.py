"""Check the physical meaning of WISE waveform labels independently of scheduling.

Gap in timed.check: broadcast validates equal class strings, equal cycle bounds,
and nonoverlap, but does not derive a class's direction from device geometry.
Uniformly mislabeling an entire cycle therefore passes JONES_WISE. The negative
tests demonstrate that gap without modifying timed.py or the saved schedules.

N/S mean increasing/decreasing device y; E/W mean increasing/decreasing x.
An ion's current directed leg is established by split or j_exit and retained
through transit, j_enter, and merge; it is never inferred from class metadata.
"""
from dataclasses import replace
import gzip
import json
import math
from pathlib import Path

import pytest

from qccd.repro.models import JONES_WISE, jones_duration
from qccd.repro.sched_jones import stim_program
from qccd.repro.timed import TimedSchedule, check


TRANSPORT = {"split", "transit", "j_enter", "j_exit", "merge"}


def _assert_geometric_classes(sched):
    """Replay directed legs using endpoints/coordinates, then compare whole labels."""
    dev = sched.device
    positions = {node: record["pos"] for node, record in {**dev.sites, **dev.nodes}.items()}
    initial = {i: ("site", t) for t, ions in sched.chains.items() for i in ions}
    location = dict(initial)
    legs = {}
    checked = {}

    def far_end(segment, near):
        a, b = dev.segments[segment]
        assert near in (a, b), f"{segment} does not meet {near}"
        return b if near == a else a

    def direction(leg):
        a, b = (positions[node] for node in leg)
        dx, dy = b[0] - a[0], b[1] - a[1]
        x_zero, y_zero = math.isclose(dx, 0, abs_tol=1e-9), math.isclose(dy, 0, abs_tol=1e-9)
        assert x_zero != y_zero, f"non-axial or zero-length leg {leg}: {a} -> {b}"
        return ("E" if dx > 0 else "W") if y_zero else ("N" if dy > 0 else "S")

    for event in sorted(sched.events, key=lambda e: (e.t0, e.id)):
        if event.kind not in JONES_WISE.broadcast:
            continue
        # These four return-home archives need no swaps; fail explicitly if a
        # new transport primitive appears without a geometric interpretation.
        assert event.kind in TRANSPORT, f"unsupported transport kind {event.kind}"
        assert len(event.ions) == 1
        ion, = event.ions
        if event.kind == "split":
            assert location[ion] == ("site", event.at)
            legs[ion] = (event.at, far_end(event.seg, event.at))
            location[ion] = ("seg", event.seg)
        elif event.kind == "j_exit":
            assert location[ion] == ("node", event.via)
            legs[ion] = (event.via, far_end(event.dst, event.via))
            location[ion] = ("seg", event.dst)
        else:
            segment = event.src if event.kind == "j_enter" else event.seg
            assert location[ion] == ("seg", segment)
            assert set(legs[ion]) == set(dev.segments[segment])
            if event.kind == "j_enter":
                assert legs[ion][1] == event.via
                location[ion] = ("node", event.via)
            elif event.kind == "merge":
                assert legs[ion][1] == event.at
                location[ion] = ("site", event.at)
        expected = f"{event.kind}:{direction(legs[ion])}"
        actual = event.meta.get("class")
        assert actual == expected, (
            f"event {event.id} geometric class mismatch: {actual!r}, expected {expected!r} "
            f"from leg {legs[ion]}"
        )
        checked[event.id] = expected
        if event.kind == "merge":
            del legs[ion]
    assert location == initial
    assert not legs
    return checked


@pytest.fixture(scope="module", params=(2, 3, 6, 12), ids=lambda d: f"d{d}-split")
def wise_archive(request):
    distance = request.param
    base = Path(__file__).resolve().parents[1] / "Reproduce/jones2025"
    with gzip.open(base / f"ours/grid_rot_d{distance}_c2_wise.schedule.json.gz", "rt") as f:
        sched = TimedSchedule.from_json(json.load(f))
    circuit, _, _ = stim_program(base / f"circuits/rotated_memory_z_d{distance}_r1.stim")
    return sched, circuit


def test_all_transport_labels_match_coordinates(wise_archive):
    sched, circuit = wise_archive
    checked = _assert_geometric_classes(sched)
    transport = [e for e in sched.events if e.kind in JONES_WISE.broadcast]
    assert set(checked) == {e.id for e in transport}
    assert {e.kind for e in transport} == TRANSPORT
    assert {label.split(":")[1] for label in checked.values()} == {"N", "S", "E", "W"}
    report = check(sched, JONES_WISE, duration=jones_duration, circuit=circuit, strict=True)
    assert report.ok, report.summary()
    assert report.checks["broadcast"] == "passed"


@pytest.mark.parametrize("cycle_index", range(12))
@pytest.mark.xfail(strict=True, reason="timed.check broadcast trusts meta['class']; it does not validate direction from geometry")
def test_uniformly_wrong_cycle_should_fail_timed_geometry(wise_archive, cycle_index):
    """First pass: all six outbound and all six return cycles, in every archive."""
    sched, circuit = wise_archive
    transport = [e for e in sched.events if e.kind in JONES_WISE.broadcast]
    cycle_start = sorted({e.t0 for e in transport})[cycle_index]
    cycle = [e for e in transport if e.t0 == cycle_start]
    labels = {e.meta["class"] for e in cycle}
    assert len(labels) == 1
    primitive, direction = next(iter(labels)).split(":")
    opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}[direction]
    wrong = f"{primitive}:{opposite}"
    ids = {e.id for e in cycle}
    broken = replace(sched, events=[
        replace(e, meta={**e.meta, "class": wrong}) if e.id in ids else e
        for e in sched.events
    ])
    assert {e.meta["class"] for e in broken.events if e.id in ids} == {wrong}
    # Only the labels changed: timings, positions, locks, circuit and class
    # uniformity are still legal, so the core checker cannot catch this fault.
    report = check(broken, JONES_WISE, duration=jones_duration, circuit=circuit, strict=True)
    with pytest.raises(AssertionError, match="geometric class mismatch"):
        _assert_geometric_classes(broken)
    # Desired behavior is rejection. Until timed.check derives direction,
    # this assertion fails and the strict xfail records the known gap.
    assert not report.ok, "timed.check accepted a uniformly false geometric class"
