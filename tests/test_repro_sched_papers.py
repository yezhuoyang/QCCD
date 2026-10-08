"""Our schedules for Moveless, Jones et al. and TISCC replay clean under each paper's own
rules -- and the checker still finds what we plant in them.

Every verdict comes from `timed.check` with the paper's profile, its duration law (strict:
an operation the law does not price is refused) and the circuit; each test that expects
silence also plants a fault and expects the checker to name it.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from qccd.repro import catalog, importers, models, sched_jones, sched_moveless, sched_tiscc
from qccd.repro.__main__ import ROOT, _circuit, _open, check_ours
from qccd.repro.timed import TimedSchedule, check


def _replace(s: TimedSchedule, eid: int, **kw) -> TimedSchedule:
    return dataclasses.replace(s, events=[dataclasses.replace(e, **kw) if e.id == eid else e
                                          for e in s.events])


# ------------------------------------------------------------------------------ Moveless


def _moveless(code: str = "SC9_L8"):
    base = ROOT / "khan2025moveless"
    run = f"{code}_Moveless_1m"
    path = _open(base / "artifact" / f"{run}.schedule.json.gz")
    raw = json.loads(path.read_text(encoding="utf-8"))
    law = importers.qccdsim_params(raw["config"], raw["device"])
    spec = next(r for r in catalog.paper("khan2025moveless")["runs"] if r["id"] == run)
    circ = _circuit(base, spec["circuit"])
    return importers.import_qccdsim(path), law, circ


def _moveless_check(s, law, circ):
    return check(s, models.QCCDSIM_GATESET, duration=models.qccdsim_duration(law), circuit=circ,
                 strict=True)


def test_moveless_one_ancilla_tours_the_line_once():
    ref, law, circ = _moveless("SC25_L24")
    s = sched_moveless.build(circ, ref.device, law=law)
    rep = _moveless_check(s, law, circ)
    assert rep.ok, rep.summary()
    # 80 CNOTs of 100 us in a row, the first move 170 us, three more with a gate swap (470 us)
    assert rep.metrics["makespan_us"] == 80 * 100 + 170 + 3 * 470
    # every trap starts at the paper's fill of 5 ions
    assert max(len(c) for c in s.chains.values()) <= 5
    swaps = [e for e in s.events if e.kind == "split" and e.swap]
    assert len(swaps) == 3 and {e.ions[0] for e in swaps} == {"25"}


def test_moveless_planted_faults_are_found():
    ref, law, circ = _moveless("SC9_L8")
    s = sched_moveless.build(circ, ref.device, law=law)
    assert _moveless_check(s, law, circ).ok
    # a CNOT dropped: the set of CNOTs is no longer the benchmark's
    g = next(e for e in s.events if e.kind == "gate")
    dropped = dataclasses.replace(s, events=[e for e in s.events if e.id != g.id])
    assert _moveless_check(dropped, law, circ).failed("circuit")
    # the ancilla's move made 10 us shorter than QCCDSim's law
    mv = next(e for e in s.events if e.kind == "move")
    assert _moveless_check(_replace(s, mv.id, t1=mv.t1 - 5), law, circ).failed("durations")


# --------------------------------------------------------------------------------- Jones


def _jones(d: int = 2):
    base = ROOT / "jones2025"
    ref = importers.import_jones(_open(base / "artifact" / f"grid_rot_d{d}_c2.schedule.json.gz"))
    stim = base / "circuits" / f"rotated_memory_z_d{d}_r1.stim"
    return ref, stim, importers.stim_cx(stim)


def _jones_check(s, circ):
    return check(s, models.JONES, duration=models.jones_duration, circuit=circ, strict=True)


def test_jones_tours_pass_the_circuit_check_the_artifact_fails():
    ref, stim, circ = _jones(3)
    theirs = check(ref, models.JONES, duration=models.jones_duration, circuit=circ)
    assert theirs.failed("circuit")                     # the artifact's own grid schedule
    s = sched_jones.build(ref.device, ref.chains, ref.qubits, stim)
    rep = _jones_check(s, circ)
    assert rep.ok, rep.summary()
    assert rep.metrics["last_start_us"] < theirs.metrics["last_start_us"]
    # the same operations as the paper's: every reset, rotation, MS and measurement
    for kind in ("reset", "gate1", "gate", "measure"):
        assert rep.metrics["counts"][kind] == theirs.metrics["counts"][kind], kind
    # no gate swap anywhere; every event says what it holds; every ion ends where it began
    assert "swap" not in rep.metrics["counts"]
    assert all(e.meta.get("locks") for e in s.events)
    where = {i: t for t, c in s.chains.items() for i in c}
    for e in sorted(s.events, key=lambda e: e.t0):
        if e.kind == "merge":
            where[e.ions[0]] = e.at
    assert where == {i: t for t, c in s.chains.items() for i in c}


def test_jones_planted_faults_are_found():
    ref, stim, circ = _jones(2)
    s = sched_jones.build(ref.device, ref.chains, ref.qubits, stim)
    assert _jones_check(s, circ).ok
    # a data qubit sees two CNOTs of different roles in the wrong order: swap the partners of
    # its first and last CNOT
    data = ref.qubits["3"]
    mine = sorted((e for e in s.events if e.kind == "gate" and data in e.ions), key=lambda e: e.t0)
    first, last = mine[0], mine[-1]
    bad = dataclasses.replace(s, events=[
        dataclasses.replace(e, ions=last.ions) if e.id == first.id else
        dataclasses.replace(e, ions=first.ions) if e.id == last.id else e for e in s.events])
    assert not _jones_check(bad, circ).ok
    # an operation that also claims a junction another one holds at the same time
    sp = next(e for e in s.events if e.kind == "split")
    held = next(l for l in sp.meta["locks"] if l.startswith("junction:"))
    other = next(e for e in s.events if e.kind in ("gate1", "reset") and e.t0 < sp.t1 and e.t1 > sp.t0)
    clash = _replace(s, other.id, meta={**other.meta, "locks": [*other.meta["locks"], held]})
    assert _jones_check(clash, circ).failed("declared_locks")
    # a junction entry made free
    je = next(e for e in s.events if e.kind == "j_enter")
    assert _jones_check(_replace(s, je.id, t1=je.t0), circ).failed("durations")


# --------------------------------------------------------------------------------- TISCC


def _tiscc():
    spec = ROOT / "leblond2023" / "artifact" / "idle_551.spec"
    theirs = importers.import_tiscc(spec, nrows=6, ncols=6)
    circ = _circuit(ROOT / "leblond2023", {"kind": "pairs", "file": "idle_551.zz.json"})
    return theirs, circ


def test_tiscc_round_keeps_every_ions_operations_and_zones():
    theirs, circ = _tiscc()
    prog, zz, home = sched_tiscc.programs(theirs)
    measure = {i for i, p in prog.items() if any(x[0] == "op" and x[2] == "prepare" for x in p)}
    plan = sched_tiscc._parity_plan(zz, home, measure, "m")
    s = sched_tiscc.simulate(theirs, plan)
    rep = sched_tiscc.verify(s, circ)
    assert rep.ok, rep.summary()
    assert rep.metrics["makespan_us"] < check(theirs, models.TISCC, duration=models.tiscc_duration
                                              ).metrics["makespan_us"]
    # each ion runs exactly TISCC's operations, in TISCC's order
    ours, _, _ = sched_tiscc.programs(s)
    norm = lambda p: [x if x[0] == "op" else ("zz", tuple(sorted(zz[x[1]]["ions"]))) for x in p]
    zz2 = sched_tiscc.programs(s)[1]
    norm2 = lambda p: [x if x[0] == "op" else ("zz", tuple(sorted(zz2[x[1]]["ions"]))) for x in p]
    assert {i: norm(p) for i, p in prog.items()} == {i: norm2(p) for i, p in ours.items()}
    # single-ion operations only in operation zones; every ion ends in its starting zone
    at = {c[0]: z for z, c in s.chains.items() if c}
    for e in sorted(s.events, key=lambda e: (e.t0, e.id)):
        if e.kind == "hop":
            at[e.ions[0]] = e.dst
        elif e.kind in ("gate1", "prepare", "measure"):
            assert s.device.sites[e.at]["zone"] == "O", e
    assert at == {c[0]: z for z, c in s.chains.items() if c}


def test_tiscc_planted_faults_are_found():
    theirs, circ = _tiscc()
    prog, zz, home = sched_tiscc.programs(theirs)
    measure = {i for i, p in prog.items() if any(x[0] == "op" and x[2] == "prepare" for x in p)}
    s = sched_tiscc.simulate(theirs, sched_tiscc._parity_plan(zz, home, measure, "m"))
    assert sched_tiscc.verify(s, circ).ok
    # a ZZ dropped: the round no longer measures every stabilizer
    g = next(e for e in s.events if e.kind == "gate")
    assert sched_tiscc.verify(dataclasses.replace(s, events=[e for e in s.events if e.id != g.id]),
                              circ).failed("circuit")
    # two crossings of one junction made to overlap
    jx = [e for e in s.events if e.kind == "hop" and e.path]
    a = jx[0]
    b = next(e for e in jx if e.path == a.path and e.id != a.id)
    moved = _replace(s, b.id, t0=a.t0 + 1, t1=a.t0 + 1 + (b.t1 - b.t0))
    assert sched_tiscc.verify(moved, circ).failed("junction_mutex")


# ------------------------------------------------------------------------- on the page


@pytest.mark.parametrize("key,run_id", [("khan2025moveless", "SC9_L8_ours"),
                                        ("jones2025", "grid_rot_d2_c2_ours"),
                                        ("leblond2023", "idle_551_ours")])
def test_ours_on_the_page_pass_on_the_papers_terms(key, run_id):
    p = catalog.paper(key)
    o = next(x for x in catalog.OURS[key]["runs"] if x["id"] == run_id)
    row = check_ours(p, o, {})
    assert row["ok"] and row["same_device"] and row["circuit_rows"] == "true", row
