"""Our scheduler's schedules replay clean under both QCCDSim chain books -- and the checker
still finds what we plant in them.

Every schedule here comes out of `qccd.repro.sched`; every verdict comes from `timed.check`
with the paper's duration law and the circuit.  Each test that expects silence plants a fault
in the same schedule and expects the checker to name it.
"""

from __future__ import annotations

import dataclasses

import pytest

from qccd.repro import sched
from qccd.repro.models import QCCDSIM, QCCDSIM_PHYSICAL, QCCDSimParams, qccdsim_duration, qccdsim_gate_us
from qccd.repro.sched import ALLOWED_KINDS, Knobs, SchedError, _ranks, build, schedule, verify
from qccd.repro.timed import Event, PortGraph, TimedSchedule, check

LAW = QCCDSimParams(gate="FM", swap="GateSwap", junction_us={2: 5, 3: 100, 4: 120})


def linear(n: int, cap: int, fill: int | None = None) -> PortGraph:
    """QCCDSim's `make_linear_machine(n, ...)`: T_i -S(2i)- J_i -S(2i+1)- T_(i+1)."""
    sites = {f"T{i}": {"capacity": cap, "fill": fill or cap, "ports": {}} for i in range(n)}
    segments, nodes = {}, {}
    for i in range(n - 1):
        nodes[f"J{i}"] = {}
        segments[f"S{2 * i}"] = (f"T{i}", f"J{i}")
        segments[f"S{2 * i + 1}"] = (f"T{i + 1}", f"J{i}")
        sites[f"T{i}"]["ports"][f"S{2 * i}"] = "R"
        sites[f"T{i + 1}"]["ports"][f"S{2 * i + 1}"] = "L"
    return PortGraph(sites=sites, nodes=nodes, segments=segments)


def g2x3(cap: int, fill: int | None = None) -> PortGraph:
    """QCCDSim's `test_trap_2x3`, named as `importers.qccdsim_device` names it."""
    ports = {"T0": ("S0", "R"), "T1": ("S1", "R"), "T2": ("S2", "R"),
             "T3": ("S3", "L"), "T4": ("S4", "L"), "T5": ("S5", "L")}
    return PortGraph(
        sites={t: {"capacity": cap, "fill": fill or cap, "ports": {s: side}} for t, (s, side) in ports.items()},
        nodes={"J0": {}, "J1": {}, "J2": {}},
        segments={"S0": ("T0", "J0"), "S1": ("T1", "J1"), "S2": ("T2", "J2"), "S3": ("T3", "J2"),
                  "S4": ("T4", "J1"), "S5": ("T5", "J0"), "S6@J0-J1": ("J0", "J1"), "S6@J1-J2": ("J1", "J2")})


def bv(n: int) -> list[tuple[str, str]]:
    return [(str(i), str(n)) for i in range(n)]


def qft(n: int) -> list[tuple[str, str]]:
    return [(str(k), str(j)) for j in range(n) for k in range(j + 1, n) for _ in range(2)]


def ladder(n: int, layers: int) -> list[tuple[str, str]]:
    return [(str(i), str(i + 1)) for _ in range(layers) for i in range(n - 1)]


def both_ok(s: TimedSchedule, circ) -> dict:
    reps = verify(s, circ, LAW)
    for name, r in reps.items():
        assert r.ok, (name, r.summary())
    assert {e.kind for e in s.events} <= set(ALLOWED_KINDS)
    return reps


def test_bv_tours_the_ancilla_and_is_legal_under_both_books():
    dev, circ = linear(4, cap=8, fill=6), bv(16)
    s = build(circ, dev, law=LAW, knobs=Knobs(mapping="po", alpha=2, decay=0.9))
    reps = both_ok(s, circ)
    g = qccdsim_gate_us(LAW, 8, None, "", "")
    ms = reps["qccdsim"].metrics["makespan_us"]
    assert ms >= len(circ) * g                      # 16 gates on one ancilla are serial
    assert reps["qccdsim"].metrics["counts"]["move"] <= 6
    # plant: start the first gate on the ancilla 1 us before its previous op ends
    gates = sorted((e for e in s.events if e.kind == "gate"), key=lambda e: e.t0)
    second = gates[1]
    early = dataclasses.replace(second, t0=second.t0 - 1, t1=second.t1 - 1)
    bad = dataclasses.replace(s, events=[early if e.id == second.id else e for e in s.events])
    r = check(bad, QCCDSIM, duration=qccdsim_duration(LAW), circuit=circ)
    assert r.failed("ion_serial") or r.failed("trap_serial"), r.summary()


def _driven(dev: PortGraph, chains, moves, gates, knobs=None) -> TimedSchedule:
    """Drive the builder's movement layer by hand: ``moves`` are (ion, trap) walks, then
    ``gates`` run where their ions stand."""
    run = sched._Run(gates, sched._Dev(dev, LAW), chains, knobs or Knobs())
    for ion, dst in moves:
        assert run.go(ion, dst, set()) is not None, (ion, dst)
    for a, b in gates:
        run.st.gate(a, b)
    ions = [q for c in chains.values() for q in c]
    return TimedSchedule("driven", dev, {t: list(c) for t, c in chains.items()},
                         run.st.events(), qubits={q: q for q in ions})


def test_a_reorder_is_charged_and_satisfies_both_books():
    # T0 has one port, on its right; a stands one from that end, so it leaves with a gate swap
    dev = linear(2, cap=4)
    chains = {"T0": ["a", "b"], "T1": ["c"]}
    s = _driven(dev, chains, [("a", "T1")], [("a", "c")])
    both_ok(s, [("a", "c")])
    sp = next(e for e in s.events if e.kind == "split")
    assert sp.swap == {"kind": "gate", "with": "b", "hops": 1}
    g = qccdsim_gate_us(LAW, 4, None, "", "")
    assert sp.duration == 3 * g + LAW.split_merge_us
    # plant: drop the reorder; both books must notice that a is not at the end
    plain = dataclasses.replace(sp, swap=None, t1=sp.t0 + LAW.split_merge_us)
    bad = dataclasses.replace(s, events=[plain if e.id == sp.id else e for e in s.events])
    for prof in (QCCDSIM, QCCDSIM_PHYSICAL):
        assert check(bad, prof, duration=qccdsim_duration(LAW)).failed("chain_order")


def test_a_crossing_is_healed_so_the_books_never_part():
    """An ion that enters a trap at one end and leaves at the other pays a gate swap with
    the far end ion; the physical book then has that end ion at the near end, QCCDSim's book
    does not.  The builder sends it out through the near end at once (and back in), which
    makes the books agree again -- so every later split in that trap is legal under both."""
    dev = linear(3, cap=6)
    chains = {"T0": [], "T1": ["a", "p", "q", "r"], "T2": ["z"]}
    gates = [("a", "z"), ("q", "z")]
    s = _driven(dev, chains, [("a", "T2"), ("q", "T2")], gates)
    both_ok(s, gates)
    cross = [e for e in s.events if e.kind == "split" and e.swap and e.swap["hops"] >= 2]
    heals = [e for e in s.events if e.kind == "split" and e.meta.get("why") == "heal"]
    assert [e.ions for e in cross][:1] == [("a",)] and heals and heals[0].ions == ("r",)
    # plant: remove the heal and r's return -- the books part, and q's later plain split from
    # the right end of T1 is legal in the physical book but not in QCCDSim's
    bad = dataclasses.replace(s, events=[e for e in s.events if e.meta.get("why") != "heal"])
    rq = check(bad, QCCDSIM, duration=qccdsim_duration(LAW), circuit=gates)
    rp = check(bad, QCCDSIM_PHYSICAL, duration=qccdsim_duration(LAW), circuit=gates)
    assert rp.ok and rq.failed("chain_order"), (rq.summary(), rp.summary())


def test_the_sweep_runs_qft_on_the_grid_and_the_line():
    circ = qft(10)
    assert _ranks(circ, [str(i) for i in range(10)]) is not None
    kn = Knobs(mapping="rank", policy="sweep", order="travel", traps_used=6)
    s = build(circ, g2x3(cap=4), law=LAW, knobs=kn)
    both_ok(s, circ)
    s = build(circ, linear(4, cap=8, fill=6), law=LAW,
              knobs=Knobs(mapping="rank", policy="sweep", order="travel", traps_used=4))
    both_ok(s, circ)
    # plant: swap the order of two gates on one qubit -- the circuit check must object
    gates = sorted((e for e in s.events if e.kind == "gate"), key=lambda e: e.t0)
    g0 = next(e for e in gates if "0" in e.ions)
    g1 = next(e for e in gates if "0" in e.ions and e.ions != g0.ions)
    bad = dataclasses.replace(s, events=[
        dataclasses.replace(e, ions=g1.ions) if e.id == g0.id else
        dataclasses.replace(e, ions=g0.ions) if e.id == g1.id else e for e in s.events])
    r = check(bad, QCCDSIM, duration=qccdsim_duration(LAW), circuit=circ)
    assert not r.ok, r.summary()


def test_the_ranks_of_a_monotone_circuit():
    assert _ranks(qft(6), [str(i) for i in range(6)]) == {str(i): i for i in range(6)}
    rev = [(str(5 - a_), str(5 - b_)) for a_, b_ in ((int(a), int(b)) for a, b in qft(6))]
    assert _ranks(rev, [str(i) for i in range(6)]) == {str(5 - i): i for i in range(6)}
    assert _ranks(ladder(6, 2), [str(i) for i in range(6)]) is None


def test_a_full_trap_is_relieved_and_capacity_holds_at_every_instant():
    dev = linear(3, cap=4, fill=4)
    chains = {"T0": ["a", "b", "c", "d"], "T1": ["e", "f", "g", "h"], "T2": []}
    circ = [("a", "e"), ("d", "h"), ("b", "g")]
    s = build(circ, dev, law=LAW, chains=chains)
    reps = both_ok(s, circ)
    assert max(reps["qccdsim"].metrics["peak_occupancy"].values()) <= 4
    # plant: the same schedule on traps one ion smaller overflows somewhere
    small = PortGraph(sites={k: {**v, "capacity": 3} for k, v in dev.sites.items()},
                      nodes=dev.nodes, segments=dev.segments)
    r = check(dataclasses.replace(s, device=small), QCCDSIM)
    assert r.failed("capacity"), r.summary()


def test_schedule_returns_only_what_the_checker_accepts():
    dev, circ = linear(4, cap=8, fill=6), ladder(16, 3)
    s = schedule(circ, dev, law=LAW, time_budget_s=3, seed=1)
    both_ok(s, circ)
    assert s.source["search"]["tried"] >= 1


def test_an_impossible_fill_is_refused_not_patched():
    with pytest.raises(SchedError):
        build(bv(20), linear(2, cap=8, fill=6), law=LAW)
