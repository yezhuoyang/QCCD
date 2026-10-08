"""TrapSIMD (Ruan et al., arXiv 2504.17886) in the timed checker.

The paper's two worked examples must replay to the paper's own numbers with every check
passing, and every rule the module adds must catch a fault planted for it.  Our schedules
on the paper's smallest configuration must pass the same checks.
"""

from __future__ import annotations

import dataclasses

import pytest

from qccd.repro import trapsimd as ts
from qccd.repro.timed import Event, check

EXAMPLES = [
    (ts.w1_eager, ts.w1_program, 982.0),
    (ts.w1_batched, ts.w1_program, 732.0),
    (ts.w2_depth, ts.w2_program, 823.0),
    (ts.w2_sliced, ts.w2_program, 545.0),
]


def checked(sched, prog=None, **kw):
    return ts.check_trapsimd(sched, program=prog, **kw)


def configured_all_pass(rep) -> bool:
    return rep.ok and all(v == "passed" or v.startswith("skipped") for v in rep.checks.values())


# ---------------------------------------------------------------- the worked examples


@pytest.mark.parametrize("build, prog, makespan", EXAMPLES, ids=lambda x: getattr(x, "__name__", str(x)))
def test_the_worked_examples_replay_to_the_papers_numbers(build, prog, makespan):
    rep = checked(build(), prog())
    assert configured_all_pass(rep), rep.summary()
    assert rep.metrics["makespan_us"] == makespan
    skipped = {k for k, v in rep.checks.items() if v.startswith("skipped")}
    # what is skipped is skipped by the model, not for want of a check
    assert skipped == {"segment_mutex", "trap_serial", "chain_order", "declared_locks"}, rep.checks
    for name in ("broadcast", "durations", "circuit", "intra", "inter", "modes", "zones", "program"):
        assert rep.checks[name] == "passed"


def test_a_saved_example_reloads_and_rechecks_from_the_file_alone(tmp_path):
    from qccd.repro.timed import TimedSchedule

    path = ts.w1_batched().save(tmp_path / "fig6.schedule.json")
    rep = checked(TimedSchedule.load(path), ts.w1_program())
    assert rep.ok and rep.metrics["makespan_us"] == 732.0


def test_the_examples_need_the_effective_141_us_gate_not_table_1s_25():
    for build, prog, _ in EXAMPLES:
        rep = checked(build(), prog(), tg=ts.TRAPSIMD_US["gate2_table"])
        assert rep.failed("durations"), rep.summary()


def test_fig6_batches_two_ions_into_one_dr_cycle():
    s = ts.w1_batched()
    hops = [e for e in s.events if e.kind == "hop"]
    assert len(hops) == 2 and {e.t0 for e in hops} == {341.0}
    assert {e.meta["class"] for e in hops} == {"D->R"}           # the paper's DR-SH
    eager = [e for e in ts.w1_eager().events if e.kind == "hop"]
    assert sorted(e.t0 for e in eager) == [141.0, 591.0]         # two size-1 cycles


def test_fig7_time_stamps_are_the_papers():
    s = ts.w2_sliced()
    starts = sorted({e.t0 for e in s.events if e.kind in ("gate", "gate1")} |
                    {e.t0 for e in s.events if e.kind == "split"})
    assert starts == [0.0, 5.0, 63.0, 204.0, 404.0]


# ------------------------------------------------------------------- planted faults


def replace(sched, eid, **kw):
    return dataclasses.replace(sched, events=[dataclasses.replace(e, **kw) if e.id == eid else e
                                              for e in sched.events])


def test_two_classes_in_one_cycle_are_caught():
    s = ts.w1_batched()
    q4 = next(e for e in s.events if e.kind == "hop" and e.ions == ("Q4",))
    # Q4 goes straight down into the lower-right trap instead: class D->D in a D->R cycle
    bad = replace(s, q4.id, dst="S2.0", meta={**q4.meta, "class": "D->D"})
    rep = checked(bad, ts.w1_program())
    assert rep.failed("broadcast"), rep.summary()


def test_a_mislabelled_class_is_caught():
    s = ts.w1_batched()
    hop = next(e for e in s.events if e.kind == "hop")
    rep = checked(replace(s, hop.id, meta={**hop.meta, "class": "U->R"}), ts.w1_program())
    assert rep.failed("inter"), rep.summary()


def test_a_gate_during_an_inter_trap_cycle_is_caught():
    s = ts.w1_eager()
    gate = next(e for e in s.events if e.kind == "gate" and e.ions == ("Q4", "Q5"))
    # start TG(Q4, Q5) while Q1's cycle (591-841) is under way
    rep = checked(replace(s, gate.id, t0=600.0, t1=741.0), ts.w1_program())
    assert rep.failed("modes"), rep.summary()


def test_overlapping_cycles_are_caught():
    s = ts.w1_eager()
    late = max((e for e in s.events if e.kind == "hop"), key=lambda e: e.t0)
    rep = checked(replace(s, late.id, t0=300.0, t1=550.0), ts.w1_program(), )
    assert rep.failed("modes") or rep.failed("positions"), rep.summary()


def test_a_hop_that_skips_positions_is_caught():
    s = ts.w1_batched()
    hop = next(e for e in s.events if e.kind == "hop" and e.ions == ("Q4",))
    rep = checked(replace(s, hop.id, dst="E2.1"), ts.w1_program())     # E2.1 is not the edge
    assert rep.failed("inter"), rep.summary()


def test_a_head_on_crossing_dressed_as_shifts_is_caught():
    # Q1 at T.1 and Q2 at T.2 'shift' past each other: the core checker sees two legal
    # half-steps, the intra rule sees two ions on one segment that are not one swap
    s = ts.w2_sliced()
    ev = [e for e in s.events if not (e.kind in ("split", "merge") and e.meta.get("op") == "swap"
                                      and e.ions[0] in ("Q1", "Q2"))]
    for e in s.events:
        if e.kind in ("split", "merge") and e.ions[0] in ("Q1", "Q2") and e.meta.get("op") == "swap":
            ev.append(dataclasses.replace(e, meta={**e.meta, "op": "shift"}, t1=e.t0 + 29.0)
                      if e.kind == "split" else
                      dataclasses.replace(e, meta={**e.meta, "op": "shift"}, t0=29.0, t1=58.0))
    bad = dataclasses.replace(s, events=ev)
    rep = checked(bad, ts.w2_program())
    assert rep.failed("intra"), rep.summary()


def test_a_follower_entering_before_its_leader_left_is_caught():
    s = ts.w2_sliced()
    # the S3 group (Q4: 5->4, Q5: 6->5) at t=5: start Q5's half-steps before Q4's
    ev = []
    for e in s.events:
        if e.ions == ("Q5",) and e.kind in ("split", "merge") and e.t0 < 63:
            ev.append(dataclasses.replace(e, t0=e.t0 - 5, t1=e.t1 - 5))
        else:
            ev.append(e)
    rep = checked(dataclasses.replace(s, events=ev), ts.w2_program())
    assert rep.failed("capacity"), rep.summary()


def test_a_gate_away_from_a_gate_zone_is_caught():
    s = ts.w1_batched()
    gate = next(e for e in s.events if e.kind == "gate" and e.ions == ("Q4", "Q5"))
    rep = checked(replace(s, gate.id, at="E2.0"), ts.w1_program())
    assert rep.failed("zones"), rep.summary()


def test_a_swap_with_neither_ion_in_a_gate_zone_is_caught_only_under_that_rule():
    dev = ts.w2_device()
    r = ts.Recorder(dev)
    r.swap("T", "A", 5, "B", 6, 0.0)          # 5 is a gate zone: legal
    r.swap("T", "C", 3, "D", 4, 0.0)          # 3 is a gate zone too
    occ = {"T.5": "A", "T.6": "B", "T.3": "C", "T.4": "D"}
    chains = {s: [occ[s]] if s in occ else [] for s in dev.portgraph().sites}
    sched = ts.TimedSchedule("swaps", dev.portgraph(), chains, r.events)
    assert checked(sched).ok
    dev8 = ts.grid_device(2, 8)               # zones at 2 and 5: positions 6 and 7 are not
    r = ts.Recorder(dev8)
    r.swap("H00", "A", 6, "B", 7, 0.0)
    occ = {"H00.6": "A", "H00.7": "B"}
    chains = {s: [occ[s]] if s in occ else [] for s in dev8.portgraph().sites}
    sched = ts.TimedSchedule("swaps8", dev8.portgraph(), chains, r.events)
    assert checked(sched).failed("zones")
    assert checked(sched, rules=ts.Rules(swap_gz=False)).ok


def test_two_gates_in_one_trap_are_caught_only_under_one_gate_per_trap():
    dev = ts.grid_device(2, 8)
    r = ts.Recorder(dev)
    r.gate("A", "B", "H00", 2, 0.0, 141.0)
    r.gate("C", "D", "H00", 5, 0.0, 141.0)
    occ = {"H00.1": "A", "H00.2": "B", "H00.5": "C", "H00.6": "D"}
    chains = {s: [occ[s]] if s in occ else [] for s in dev.portgraph().sites}
    sched = ts.TimedSchedule("two", dev.portgraph(), chains, r.events)
    assert checked(sched).ok
    assert checked(sched, rules=ts.Rules(one_gate_per_trap=True)).failed("zones")


def test_the_program_rule_catches_an_order_the_circuit_forbids():
    s = ts.w2_sliced()
    # H(Q4) must precede CX(Q3, Q4): run the H after the CX
    h = next(e for e in s.events if e.kind == "gate1")
    rep = checked(replace(s, h.id, t0=250.0, t1=255.0), ts.w2_program())
    assert rep.failed("program"), rep.summary()


# ------------------------------------------------- what the core checker cannot say


def test_the_core_checker_gaps_and_what_covers_them():
    """MODEL.md sec. 8: five things qccd.repro.timed could not express, on three positions in
    a row, and what covers each.  Gaps 3 and 4 were closed in the core checker on 2026-10-07
    (a hop follows segments; broadcast cycles never overlap)."""
    from qccd.repro.timed import PortGraph, Profile, TimedSchedule

    row = PortGraph(sites={f"P{i}": {"capacity": 1} for i in range(3)},
                    segments={"a": ("P0", "P1"), "b": ("P1", "P2")})
    probe = Profile("probe", broadcast=("hop",))
    two = {"P0": ["a"], "P1": ["b"], "P2": []}

    def core(events, chains=two, dev=row):
        return check(TimedSchedule("probe", dev, chains, events), probe)

    # 1. a lockstep group shift as hops: the follower enters a position its leader holds
    r = core([Event(0, "hop", 0, 58, ("b",), src="P1", dst="P2", meta={"class": "s"}),
              Event(1, "hop", 0, 58, ("a",), src="P0", dst="P1", meta={"class": "s"})])
    assert r.failed("capacity")
    # ... as half-steps it is legal (Fig. 7's S3 group passes every check)
    assert checked(ts.w2_sliced(), ts.w2_program()).ok
    # 2. two ions exchanging two positions: neither crossing hops nor a swap event
    r = core([Event(0, "hop", 0, 200, ("a",), src="P0", dst="P1", meta={"class": "s"}),
              Event(1, "hop", 0, 200, ("b",), src="P1", dst="P0", meta={"class": "s"})])
    assert r.failed("capacity")
    r = core([Event(0, "swap", 0, 200, ("a", "b"), at="P0"),
              Event(1, "hop", 200, 258, ("a",), src="P1", dst="P2", meta={"class": "s"})])
    assert r.failed("positions")
    # 3. a hop that teleports past a position: the core checker now refuses it (no segment
    # joins P0 and P2), and `simd_rules` also catches it (test_a_hop_that_skips_positions_is_caught)
    r = core([Event(0, "hop", 0, 58, ("a",), src="P0", dst="P2", meta={"class": "s"})],
             {"P0": ["a"], "P1": [], "P2": []})
    assert r.failed("structure")
    # 4. two cycles of different classes that overlap but start apart
    dev4 = PortGraph(sites={k: {"capacity": 1} for k in "ABCD"}, nodes={"J": {}, "K": {}},
                     segments={"a": ("A", "J"), "b": ("B", "J"), "c": ("C", "K"), "d": ("D", "K")})
    s4 = TimedSchedule("overlap", dev4, {"A": ["x"], "B": [], "C": ["y"], "D": []},
                       [Event(0, "hop", 0, 250, ("x",), src="A", dst="B", path=("J",), meta={"class": "D->R"}),
                        Event(1, "hop", 100, 350, ("y",), src="C", dst="D", path=("K",), meta={"class": "U->L"})])
    assert check(s4, probe).failed("broadcast")
    assert ts.check_trapsimd(s4).failed("modes")
    # 5. a gate inside an inter-trap cycle
    s = ts.w1_eager()
    gate = next(e for e in s.events if e.kind == "gate" and e.ions == ("Q4", "Q5"))
    inside = replace(s, gate.id, t0=600.0, t1=741.0)
    assert check(inside, ts.TRAPSIMD).ok
    assert checked(inside, ts.w1_program()).failed("modes")


# ------------------------------------------------------------------------ the device


def test_the_grid_has_the_papers_trap_counts_and_gate_zone_layouts():
    for D, traps in ((2, 12), (3, 24), (4, 40), (5, 60)):
        dev = ts.grid_device(D, 8)
        assert len(dev.traps) == traps
        assert len(dev.arms) == (D + 1) ** 2
    assert ts.gate_zones(3, 1) == (1,)            # Fig. 6's traps
    assert ts.gate_zones(7, 3) == (1, 3, 5)       # Fig. 7's trap
    assert ts.gate_zones(8, 2) == (2, 5)
    assert ts.gate_zones(14, 2) == (4, 9)
    dev = ts.grid_device(4, 8)
    assert dev.klass("V01", "J11", "H11") == "D->R"
    assert dev.klass("H10", "J11", "H11") == "R->R"
    assert len(ts.SHIFT_CLASSES) == 12


# ---------------------------------------------------------------------- our schedules


def test_the_line_networks_meet_every_pair_once_in_the_dependency_depth():
    for n in (5, 12, 20):
        q = ts.all_pairs_rounds(ts.qaoa_complete(n))
        v = ts.all_pairs_rounds(ts.vqe(n))
        assert len(q) == n and len(v) == 2 * n - 3
        for rounds, prog in ((q, ts.qaoa_complete(n)), (v, ts.vqe(n))):
            ops = [a[2] for r in rounds for a in r]
            assert sorted(ops) == sorted(k for k, o in enumerate(prog.ops) if len(o.qubits) == 2)


def test_a_sparse_commuting_program_runs_on_the_network_with_plain_exchanges():
    prog = ts.qaoa_regular(12, seed=3)
    rounds = ts.all_pairs_rounds(prog, partial=True)
    gates = sorted(a[2] for r in rounds for a in r if a[0] == "2")
    assert gates == sorted(k for k, o in enumerate(prog.ops) if len(o.qubits) == 2)
    assert any(a[0] == "x" for r in rounds for a in r)
    with pytest.raises(ValueError):
        ts.all_pairs_rounds(prog)                     # not every pair: refused without partial
    sched = ts.schedule_line(ts.grid_device(2, 8), prog, partial=True)
    assert checked(sched, prog).ok


def test_the_a60_trail_needs_five_classes():
    dev = ts.grid_device(4, 8)
    trail = ts.trap_trail(dev, 15)
    ex = ts.LineExecutor(dev, ts.vqe(60), trail)
    classes = {dev.klass(a, j, b) for a, j, b in zip(ex.trail, ex.junc, ex.trail[1:])}
    assert len(classes) == 5


@pytest.mark.parametrize("bench", ["VQE", "QAOA", "BV", "RCA"])
def test_our_a20_schedules_pass_every_check_and_beat_table_3(bench):
    prog = {"VQE": ts.vqe, "QAOA": ts.qaoa_complete, "BV": ts.bv, "RCA": ts.rca}[bench](20)
    sched, rep = ts.schedule_ours(ts.grid_device(2, 8), prog)
    assert configured_all_pass(rep), rep.summary()
    assert rep.metrics["counts"]["gate"] == prog.n2q
    assert rep.metrics["makespan_us"] < ts.TABLE3[(8, bench, 20)][1]


def test_vqe_on_the_line_holds_in_strict_program_order():
    prog = ts.vqe(20)
    sched = ts.schedule_line(ts.grid_device(2, 8), prog)
    rep = checked(sched, dataclasses.replace(prog, order="strict"))
    assert rep.ok, rep.summary()


def test_a_planted_fault_in_our_schedule_is_caught():
    prog = ts.qaoa_complete(20)
    sched = ts.schedule_line(ts.grid_device(2, 8), prog)
    hops = [e for e in sched.events if e.kind == "hop"]
    # 1. a gate dropped: the circuit and the program rule both see it
    g = next(e for e in sched.events if e.kind == "gate")
    rep = checked(dataclasses.replace(sched, events=[e for e in sched.events if e.id != g.id]), prog)
    assert rep.failed("circuit") and rep.failed("program")
    # 2. a cycle shortened: the duration law and the broadcast rule see it
    rep = checked(replace(sched, hops[0].id, t1=hops[0].t1 - 50.0), prog)
    assert rep.failed("durations")
