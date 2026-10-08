"""The timed checker must find what is planted, on a toy machine and on a real schedule.

Every test that expects silence has a sibling that plants the fault and expects the checker
to name it: an assertion that cannot fail is not an assertion.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from qccd.repro.models import (QCCDSIM, QCCDSIM_PHYSICAL, TISCC, QCCDSimAnalyzer,
                               QCCDSimParams, qccdsim_duration, tiscc_duration)
from qccd.repro.timed import Event, PortGraph, Profile, TimedSchedule, check

ROOT = Path(__file__).resolve().parents[1]


def l3(cap: int = 4) -> PortGraph:
    """QCCDSim's `make_linear_machine(3, ...)`: T0 -S0- J0 -S1- T1 -S2- J1 -S3- T2."""
    return PortGraph(
        sites={"T0": {"capacity": cap, "ports": {"S0": "R"}},
               "T1": {"capacity": cap, "ports": {"S1": "L", "S2": "R"}},
               "T2": {"capacity": cap, "ports": {"S3": "L"}}},
        nodes={"J0": {}, "J1": {}},
        segments={"S0": ("T0", "J0"), "S1": ("T1", "J0"), "S2": ("T1", "J1"), "S3": ("T2", "J1")},
    )


def shuttle(eid: int, t: float, ion: str, src: str, s_out: str, via: str, s_in: str, dst: str,
            swap=None, split_us: float = 80) -> list[Event]:
    return [Event(eid, "split", t, t + split_us, (ion,), at=src, seg=s_out, swap=swap),
            Event(eid + 1, "move", t + split_us, t + split_us + 10, (ion,), src=s_out, dst=s_in, via=via),
            Event(eid + 2, "merge", t + split_us + 10, t + split_us + 90, (ion,), at=dst, seg=s_in)]


def base(cap: int = 4) -> TimedSchedule:
    # b sits at T0's right end, so it leaves without a reorder: 80 + 10 + 80 = 170 us
    ev = shuttle(0, 0, "b", "T0", "S0", "J0", "S1", "T1")
    ev.append(Event(3, "gate", 170, 270, ("b", "c"), at="T1"))   # FM at capacity 4: 100 us
    return TimedSchedule("toy", l3(cap), {"T0": ["a", "b"], "T1": ["c"], "T2": []}, ev)


LAW = qccdsim_duration(QCCDSimParams(gate="FM", junction_us={2: 5, 3: 100, 4: 120}))


def test_a_legal_schedule_passes_every_configured_check():
    r = check(base(), QCCDSIM, duration=LAW, circuit=[("b", "c")])
    assert r.ok, r.summary()
    assert r.metrics["makespan_us"] == 270
    assert r.checks.pop("declared_locks").startswith("skipped")   # QCCDSim states none
    assert r.checks.pop("broadcast").startswith("skipped")        # nor any broadcast
    assert all(v == "passed" for v in r.checks.values()), r.checks


def test_a_split_from_the_middle_of_the_chain_without_a_reorder_is_caught():
    s = base()
    s.chains["T0"] = ["b", "a"]          # b is now at the LEFT end; S0 leaves to the right
    r = check(s, QCCDSIM, duration=LAW)
    assert r.failed("chain_order"), r.summary()


def test_a_gate_swap_pays_for_the_split_and_the_law_prices_it():
    s = base()
    s.chains["T0"] = ["b", "a"]
    ev = shuttle(0, 0, "b", "T0", "S0", "J0", "S1", "T1",
                 swap={"kind": "gate", "with": "a", "hops": 1}, split_us=3 * 100 + 80)
    ev.append(Event(3, "gate", 470, 570, ("b", "c"), at="T1"))
    s.events = ev
    assert check(s, QCCDSIM, duration=LAW).ok
    # the same split charged as a plain one is too short for the law
    s.events = shuttle(0, 0, "b", "T0", "S0", "J0", "S1", "T1",
                       swap={"kind": "gate", "with": "a", "hops": 1}) + [
        Event(3, "gate", 170, 270, ("b", "c"), at="T1")]
    assert check(s, QCCDSIM, duration=LAW).failed("durations")


def test_the_two_gate_swap_conventions_leave_different_chains():
    # T0 = [x, a, b]; a leaves right with a gate swap.  QCCDSim keeps b at the end, the
    # physical swap puts b where a stood.  A second split of b to the right then needs no
    # reorder in QCCDSim's books but... b is at the end in both: use x's view instead.
    dev = l3()
    s = TimedSchedule("conv", dev, {"T0": ["x", "a", "b"], "T1": [], "T2": []},
                      shuttle(0, 0, "a", "T0", "S0", "J0", "S1", "T1",
                              swap={"kind": "gate", "with": "b", "hops": 1}, split_us=380))
    seen = {}

    def spy(e, ctx):
        seen.setdefault(e.id, ctx.chain)

    s.events.append(Event(9, "gate1", 1000, 1005, ("x",), at="T0"))
    check(s, QCCDSIM, observers=[spy])
    q = seen[9]
    seen.clear()
    check(s, QCCDSIM_PHYSICAL, observers=[spy])
    p = seen[9]
    assert q == ["x", "b"] and p == ["x", "b"]       # same here: a was adjacent to the end
    s.chains["T0"] = ["a", "x", "b"]                 # now a is two from the end
    s.events[0] = dataclasses.replace(s.events[0], swap={"kind": "gate", "with": "b", "hops": 2})
    seen.clear(); check(s, QCCDSIM, observers=[spy]); q = seen[9]
    seen.clear(); check(s, QCCDSIM_PHYSICAL, observers=[spy]); p = seen[9]
    assert q == ["x", "b"] and p == ["b", "x"]


def test_two_merges_into_one_trap_at_once_break_trap_seriality():
    s = base()
    s.chains["T2"] = ["d"]
    s.events += [Event(10, "split", 0, 80, ("d",), at="T2", seg="S3"),
                 Event(11, "move", 80, 90, ("d",), src="S3", dst="S2", via="J1"),
                 Event(12, "merge", 90, 170, ("d",), at="T1", seg="S2")]
    r = check(s, QCCDSIM)
    assert r.failed("trap_serial") and not r.failed("junction_mutex"), r.summary()
    # d arriving after b's merge and before the gate is fine
    s.events[-3:] = [Event(10, "split", 0, 80, ("d",), at="T2", seg="S3"),
                     Event(11, "move", 80, 90, ("d",), src="S3", dst="S2", via="J1"),
                     Event(12, "merge", 270, 350, ("d",), at="T1", seg="S2")]
    assert check(s, QCCDSIM).ok, check(s, QCCDSIM).summary()


def test_two_ions_crossing_one_junction_at_once_are_caught():
    s = base()
    s.chains["T1"] = ["c", "e"]
    # e leaves T1 to the LEFT? no: e is at T1's right end; c is at the left end, facing S1
    s.events = shuttle(0, 0, "b", "T0", "S0", "J0", "S1", "T1") + [
        Event(20, "split", 0, 80, ("c",), at="T1", seg="S1"),
        Event(21, "move", 85, 95, ("c",), src="S1", dst="S0", via="J0"),
    ]
    r = check(s, dataclasses.replace(QCCDSIM, trap_serial=()))
    assert r.failed("junction_mutex") and r.failed("segment_mutex"), r.summary()


def test_a_trap_over_capacity_is_caught_at_the_instant_it_overflows():
    r = check(base(cap=1), QCCDSIM)
    assert r.failed("capacity"), r.summary()
    assert check(base(cap=2), QCCDSIM).ok


def test_a_gate_on_ions_in_different_traps_is_caught():
    s = base()
    s.events[-1] = Event(3, "gate", 170, 270, ("a", "c"), at="T1")
    assert check(s, QCCDSIM).failed("positions")


def test_two_operations_in_one_trap_at_once_are_caught_only_when_the_profile_forbids_it():
    s = base()
    s.events.append(Event(4, "gate1", 200, 205, ("c",), at="T1"))
    s.events.append(Event(5, "gate", 180, 280, ("b", "c"), at="T1"))
    # ion c is in two events at once: always a violation
    assert check(s, QCCDSIM).failed("ion_serial")


def test_the_circuit_check_finds_a_missing_gate_and_a_wrong_order():
    s = base()
    assert check(s, QCCDSIM, circuit=[("b", "c")]).ok
    assert check(s, QCCDSIM, circuit=[("b", "c"), ("a", "b")]).failed("circuit")
    s.events.append(Event(4, "gate", 270, 370, ("b", "c"), at="T1"))
    s.events.append(Event(5, "gate", 370, 470, ("c", "b"), at="T1"))
    want = [("c", "b"), ("b", "c"), ("b", "c")]          # strict order differs
    assert check(s, QCCDSIM, circuit=want).failed("circuit")
    multiset = dataclasses.replace(QCCDSIM, circuit_order="multiset")
    assert check(s, multiset, circuit=want).ok


def test_the_commuting_order_allows_a_same_role_run_to_be_reordered():
    dev = PortGraph(sites={"T": {"capacity": 4, "ports": {}}})
    ev = [Event(0, "gate", 0, 1, ("a", "c"), at="T"), Event(1, "gate", 1, 2, ("a", "b"), at="T")]
    s = TimedSchedule("comm", dev, {"T": ["a", "b", "c"]}, ev)
    commuting = dataclasses.replace(QCCDSIM, circuit_order="commuting", chain_order=None)
    strict = dataclasses.replace(commuting, circuit_order="strict")
    circ = [("a", "b"), ("a", "c")]                      # shared control: they commute
    assert check(s, commuting, circuit=circ).ok
    assert check(s, strict, circuit=circ).failed("circuit")
    circ2 = [("a", "b"), ("c", "a")]                     # a is control then target: they do not
    s.events = [Event(0, "gate", 0, 1, ("c", "a"), at="T"), Event(1, "gate", 1, 2, ("a", "b"), at="T")]
    assert check(s, commuting, circuit=circ2).failed("circuit")


def test_the_qccdsim_analyzer_reproduces_its_own_arithmetic():
    # one gate in a 2-ion trap, no heating: F = 1 - tau/1e6 - A(2)*1, A(2) = max(1e-4, ...)
    s = base()
    s.chains = {"T0": ["a"], "T1": ["b", "c"], "T2": []}
    s.events = [Event(0, "gate", 0, 100, ("b", "c"), at="T1")]
    an = QCCDSimAnalyzer(QCCDSimParams(), s.chains, capacity=4)
    check(s, QCCDSIM, observers=[an])
    assert an.fidelity == pytest.approx(1 - 100 / 1e6 - 1e-4, rel=1e-12)


# ---------------------------------------------------------------- a real schedule

SPEC = ROOT / "Reproduce" / "leblond2023" / "artifact" / "idle_551.spec"


@pytest.mark.skipif(not SPEC.exists(), reason="the TISCC schedule is not in this tree")
def test_tisccs_d5_round_replays_to_its_own_9613_us_and_planted_faults_are_found():
    from qccd.repro.importers import import_tiscc

    s = import_tiscc(SPEC, nrows=6, ncols=6)
    r = check(s, TISCC, duration=tiscc_duration)
    assert r.ok, r.summary()
    assert r.metrics["makespan_us"] == pytest.approx(9613.0)
    assert r.metrics["counts"]["gate"] == 80 and r.metrics["counts"]["hop"] == 320

    # plant 1: a junction move started 10 us early collides with its neighbour's
    hops = [e for e in s.events if e.kind == "hop" and e.path]
    first = hops[0]
    twin = next(e for e in hops[1:] if e.path == first.path and e.t0 > first.t0)
    moved = dataclasses.replace(twin, t0=first.t0 + 1, t1=first.t0 + 1 + twin.duration)
    bad = dataclasses.replace(s, events=[moved if e.id == twin.id else e for e in s.events])
    assert check(bad, TISCC).failed("junction_mutex") or check(bad, TISCC).failed("ion_serial")

    # plant 2: a ZZ between two ions that are not neighbours
    zz = next(e for e in s.events if e.kind == "gate")
    far = next(i for i in s.chains if s.chains[i] and s.chains[i][0] not in zz.ions
               and i not in s.device.adjacent.get(str(zz.meta["sites"][0]), ()))
    wrong = dataclasses.replace(zz, ions=(zz.ions[0], s.chains[far][0]))
    bad = dataclasses.replace(s, events=[wrong if e.id == zz.id else e for e in s.events])
    assert check(bad, TISCC).failed("positions") or check(bad, TISCC).failed("ion_serial")


def test_a_broadcast_cycle_must_drive_one_class():
    dev = PortGraph(sites={"A": {"capacity": 1}, "B": {"capacity": 1}, "C": {"capacity": 1},
                           "D": {"capacity": 1}},
                    nodes={"J": {}},
                    segments={"a": ("A", "J"), "b": ("B", "J"), "c": ("C", "J"), "d": ("D", "J")})
    simd = Profile("simd", broadcast=("hop",))
    two = [Event(0, "hop", 0, 250, ("x",), src="A", dst="B", path=("J",), meta={"class": "LR"}),
           Event(1, "hop", 0, 250, ("y",), src="C", dst="D", path=("J",), meta={"class": "LR"})]
    s = TimedSchedule("simd", dev, {"A": ["x"], "C": ["y"], "B": [], "D": []}, two)
    assert check(s, simd).ok, check(s, simd).summary()
    s.events[1] = dataclasses.replace(two[1], meta={"class": "UD"})
    assert check(s, simd).failed("broadcast")
    # two cycles of one class that overlap without starting together: still two waveforms
    s.events[1] = dataclasses.replace(two[1], t0=100, t1=350)
    assert check(s, simd).failed("broadcast")


def test_a_hop_follows_the_device_and_holds_its_segments():
    dev = PortGraph(sites={"A": {"capacity": 1}, "B": {"capacity": 1}, "C": {"capacity": 1}},
                    nodes={"J": {}}, segments={"a": ("A", "J"), "b": ("B", "J"), "ab": ("A", "B")})
    prof = Profile("p", segment_mutex=True)
    ok = TimedSchedule("h", dev, {"A": ["x"], "B": [], "C": []},
                       [Event(0, "hop", 0, 10, ("x",), src="A", dst="B", path=("J",))])
    assert check(ok, prof).ok, check(ok, prof).summary()
    # C is joined to nothing: a hop there teleports
    tp = TimedSchedule("h", dev, {"A": ["x"], "B": [], "C": []},
                       [Event(0, "hop", 0, 10, ("x",), src="A", dst="C")])
    assert check(tp, prof).failed("structure")
    # a "path" through a trap is not a path through a junction
    tr = TimedSchedule("h", dev, {"A": ["x"], "B": [], "C": []},
                       [Event(0, "hop", 0, 10, ("x",), src="A", dst="B", path=("C",))])
    assert check(tr, prof).failed("structure")
    # two hops on the one direct segment at once, in opposite directions
    both = TimedSchedule("h", dev, {"A": ["x"], "B": ["y"], "C": []},
                         [Event(0, "hop", 0, 10, ("x",), src="A", dst="B"),
                          Event(1, "hop", 0, 10, ("y",), src="B", dst="A")])
    assert check(both, prof).failed("segment_mutex")


def test_strict_refuses_an_operation_the_law_does_not_price():
    """The loophole the scheduler agent found: under QCCDSim's law a zero-time hop or swap
    has no claimed duration, so it passed.  A schedule we write is checked strictly."""
    dev = PortGraph(sites={"A": {"capacity": 2}, "B": {"capacity": 2}},
                    segments={"s": ("A", "B")})
    law = lambda e, ctx: {"split": 80.0, "merge": 80.0, "move": 5.0, "gate": 100.0}.get(e.kind)
    s = TimedSchedule("z", dev, {"A": ["x"], "B": []},
                      [Event(0, "hop", 0, 0, ("x",), src="A", dst="B")])
    assert check(s, Profile("p"), duration=law).ok
    rep = check(s, Profile("p"), duration=law, strict=True)
    assert rep.failed("durations") and rep.metrics["duration_mismatches"] == 1
