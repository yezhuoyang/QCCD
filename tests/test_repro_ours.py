"""Our own schedules on the reproduction page are checked like the papers' (qccd.repro
__main__.check_ours): strict durations, the compared run's law and circuit, the same device.
These tests plant the faults that must keep one of ours off the page."""

import dataclasses

from qccd.repro import catalog, importers, models
from qccd.repro.__main__ import ROOT, _open, check_ours
from qccd.repro.timed import TimedSchedule, check


def test_checks_may_sit_on_any_ancilla_but_must_be_the_true_rows():
    true = [("a4", "d0"), ("a4", "d1"), ("a5", "d2"), ("a5", "d3"),
            ("d0", "a4"), ("d2", "a4"), ("d1", "a5"), ("d3", "a5")]
    # the schedule measures the first X check on a5 and the second on a4: a relabelling
    swapped = [("a5", "d0"), ("a5", "d1"), ("a4", "d2"), ("a4", "d3"),
               ("d0", "a4"), ("d2", "a4"), ("d1", "a5"), ("d3", "a5")]
    circ, why = importers.checks_as_scheduled(true, swapped)
    assert not why and sorted(circ) == sorted(swapped)
    # an ancilla that touches a set of data no true check has
    wrong = [("a5", "d0"), ("a5", "d2"), ("a4", "d1"), ("a4", "d3"), *swapped[4:]]
    _, why = importers.checks_as_scheduled(true, wrong)
    assert why and why[0].startswith("X phase")


def _bb72():
    p = catalog.paper("khan2026cyclone")
    o = next(x for x in catalog.OURS["khan2026cyclone"]["runs"] if x["id"] == "bb72_ours")
    return p, o


def test_one_of_ours_passes_on_the_papers_own_terms():
    p, o = _bb72()
    row = check_ours(p, o, {})
    assert row["ok"] and row["same_device"] and row["circuit_rows"] == "true", row
    assert row["measured"]["makespan_us"] == 17150.0


def test_a_gate_on_the_wrong_data_or_a_free_operation_keeps_it_off_the_page():
    p, o = _bb72()
    base = ROOT / p["key"]
    s = TimedSchedule.load(_open(base / o["file"]))
    spec = next(r for r in p["runs"] if r["id"] == o["against"])
    true = [tuple(x) for x in __import__("json").loads(
        (base / "circuits" / spec["circuit"]["file"]).read_text())]
    gates = [e for e in s.events if e.kind == "gate"]

    def verdict(sched):
        circ, why = importers.checks_as_scheduled(
            true, [tuple(e.ions) for e in sched.events if e.kind == "gate"])
        rep = check(sched, models.CYCLONE, duration=models.cyclone_duration, circuit=circ, strict=True)
        return rep.ok and not why

    assert verdict(s)
    # one gate moved to another data qubit of the same trap: no longer the code's check
    g = gates[0]
    a, d = g.ions
    other = next(i for e in gates if e.at == g.at for i in e.ions if i.startswith("d") and i != d)
    bad = dataclasses.replace(s, events=[dataclasses.replace(e, ions=(a, other)) if e.id == g.id else e
                                         for e in s.events])
    assert not verdict(bad)
    # a split made free: Cyclone's law prices a split at 80 us plus its swap
    sp = next(e for e in s.events if e.kind == "split")
    free = dataclasses.replace(s, events=[dataclasses.replace(e, t1=e.t0) if e.id == sp.id else e
                                          for e in s.events])
    assert not verdict(free)
