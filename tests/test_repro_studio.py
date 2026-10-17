"""A paper's schedule opened in the Studio names its ions as the circuit names its qubits, and
joins each circuit statement to the operations that really run it.

Two reader reports on the reproduced Jones et al. pages: the circuit pane said ``CX 12 6 1 7``
while the animation numbered the same ions 42, 34, 20 and 28 (the artifact's own ion ids), and
the Hadamards were never shown as run, because the artifact runs an H as two pulses, an RY
then an RX.  `studio.named_after_qubits` gives each ion its qubit's name; `studio.pulse_join`
joins a pulse to the statement it is part of, and only where the schedule has the same gap,
between the same two gates, holding the same pulses -- a reordered ion's pulses stay unjoined.

A third (T37, the Cyclone page): "why all MS gates, shouldn't they be CNOT?"  Where the paper's
tool runs each circuit CNOT as one whole gate, with no pulses dressing it (Cyclone, the QCCDSim
family), the replay now names it CX, as the tool does; where the tool schedules native pulses
(Jones, TISCC) it stays MS.  `studio.gate_label` decides from the schedule, and the name is all
that changes: our language prices a CX as one `ms_gate`, so time, cost and verdicts are equal.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from qccd.repro import catalog, importers
from qccd.repro.__main__ import ROOT, _circuit, _open
from qccd.repro.importers import JONES_NATIVE
from qccd.repro.studio import (gate_label, named_after_qubits, pulse_join, renamed, source_payload,
                               studio_arch_doc, to_tsir)
from qccd.repro.timed import Event, PortGraph, TimedSchedule


def _stim_ops(path, sched) -> list[dict]:
    """The statements of a stim memory circuit, as the reproduce page reads them."""
    to_ion = lambda q: str((sched.qubits or {}).get(q, q))
    kinds = {"R": "reset", "H": "gate", "MR": "measure", "M": "measure"}
    ops = []
    for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        t = ln.split("#")[0].split()
        if not t:
            continue
        nm, tg = t[0].split("(")[0].upper(), [x for x in t[1:] if x.isdigit()]
        if nm == "CX":
            ops += [{"name": "cx", "ions": (to_ion(a), to_ion(b)), "q": [int(a), int(b)], "line": n}
                    for a, b in zip(tg[::2], tg[1::2])]
        elif nm in kinds:
            ops += [{"name": nm.lower(), "ions": (to_ion(a),), "q": [int(a)], "line": n,
                     "kind": kinds[nm]} for a in tg]
    return ops


def _jones(d: int = 2):
    base = ROOT / "jones2025"
    s = named_after_qubits(importers.import_jones(
        _open(base / "artifact" / f"grid_rot_d{d}_c2.schedule.json.gz")))
    stim = base / "circuits" / f"rotated_memory_z_d{d}_r1.stim"
    prog = to_tsir(s, name=f"d{d}", arch="x")
    ops = _stim_ops(stim, s)
    return s, prog, ops, stim.read_text(encoding="utf-8").splitlines()


def _payload(s, prog, ops, lines, pulses=True):
    return source_payload(prog, "c", lines, ops,
                          pulses=pulse_join(s, ops, JONES_NATIVE) if pulses else None)


def test_ions_carry_the_circuits_qubit_names():
    s, prog, ops, lines = _jones(2)
    assert set(prog.instructions[0].placement) == {"1", "2", "3", "6", "7", "8", "12"}
    assert s.qubits == {q: q for q in s.qubits}
    for o in ops:
        assert list(o["ions"]) == [str(q) for q in o["q"]]
    # the row a reader pinned, "CX 12 6 1 7": its gates run on the ions named 12, 6 and 1, 7
    src = _payload(s, prog, ops, lines)
    row = [o for o in src["ops"] if lines[o["line"] - 1] == "CX 12 6 1 7"]
    assert [o["q"] for o in row] == [[12, 6], [1, 7]]
    for o in row:
        gates = [ins for ins in prog.instructions
                 if ins.pairs and o["i"] in src["realises"].get(str(ins.id), ())]
        assert len(gates) == 1 and {str(q) for q in o["q"]} in [set(p) for p in gates[0].pairs]


def test_names_that_already_agree_are_left_alone():
    s = TimedSchedule(name="x", device=PortGraph(sites={"T0": {"capacity": 2, "ports": {}}}, nodes={},
                                                 segments={}),
                      chains={"T0": ["0", "1"]}, events=[], qubits={"0": "0", "q[1]": "1"})
    assert named_after_qubits(s) is s


def test_a_renaming_that_merges_two_ions_is_refused():
    s, *_ = _jones(2)
    with pytest.raises(ValueError):
        renamed(s, {"1": "2"})


def test_every_hadamard_is_its_ions_ry_then_rx():
    for d in (2, 3):
        s, prog, ops, lines = _jones(d)
        src = _payload(s, prog, ops, lines)
        realised = {i for v in src["realises"].values() for i in v}
        assert realised == set(range(len(ops))), d                  # every statement, H included
        by_id = {str(ins.id): ins for ins in prog.instructions}
        hs = [o for o in src["ops"] if o["name"] == "h"]
        assert len(hs) == {2: 4, 3: 8}[d]
        for h in hs:
            ion = str(h["q"][0])
            ins = [by_id[k] for k, v in src["realises"].items() if h["i"] in v]
            assert all(i.type == "gate" and not i.pairs and ion in i.ions for i in ins)
            t = sorted(i.meta["t0_us"] for i in ins)
            pulses = [e.meta["op"] for e in sorted(s.events, key=lambda e: e.t0)
                      if e.kind == "gate1" and e.ions == (ion,) and e.t0 in t]
            assert pulses == ["YRotation", "XRotation"], (d, h)
        # a transport is never "towards" an H that has already run
        for k, v in src["toward"].items():
            for i in v:
                if src["ops"][i]["name"] == "h":
                    assert all(int(k) < int(j) for j, w in src["realises"].items() if i in w)


def test_without_the_pulses_no_hadamard_is_joined():
    s, prog, ops, lines = _jones(2)
    src = _payload(s, prog, ops, lines, pulses=False)
    realised = {i for v in src["realises"].values() for i in v}
    assert {o["i"] for o in src["ops"] if o["name"] == "h"}.isdisjoint(realised)


def test_a_reordered_ions_pulses_are_left_unjoined():
    # stim qubit 6 runs CX 12 6 before CX 6 7, the reverse of the circuit: which CNOT its
    # rotations serve is a guess, so none of them is joined; the ancillas keep their order
    s, prog, ops, lines = _jones(2)
    pj = pulse_join(s, ops, JONES_NATIVE)
    assert pj["6"]["gate1"] == [None] * 4
    for ion in ("2", "7", "12"):
        assert None not in pj[ion]["gate1"], ion


def test_a_pulse_out_of_place_is_not_joined():
    # plant: swap the times of the first H's two pulses on ion 2 -- RX then RY is no H
    s, prog, ops, lines = _jones(2)
    ry, rx = [e for e in sorted(s.events, key=lambda e: e.t0)
              if e.kind == "gate1" and e.ions == ("2",)][:2]
    assert (ry.meta["op"], rx.meta["op"]) == ("YRotation", "XRotation")
    bad = dataclasses.replace(s, events=[
        dataclasses.replace(e, t0=rx.t0, t1=rx.t1) if e.id == ry.id else
        dataclasses.replace(e, t0=ry.t0, t1=ry.t1) if e.id == rx.id else e for e in s.events])
    h = next(o for o in ops if o["name"] == "h" and o["ions"] == ("2",))
    first = pulse_join(bad, ops, JONES_NATIVE)["2"]["gate1"]
    assert ops.index(h) not in first[:4]


def test_the_reset_that_ends_an_mr_realises_it():
    s, prog, ops, lines = _jones(2)
    src = _payload(s, prog, ops, lines)
    resets = [ins for ins in prog.instructions if ins.type == "reset"]
    names = {ins.id: {src["ops"][i]["name"] for i in src["realises"].get(str(ins.id), ())}
             for ins in resets}
    assert names[resets[0].id] == {"r"}
    assert all(names[ins.id] == {"mr"} for ins in resets[1:])


def test_a_toy_cnot_and_its_rotations():
    # control a, target b: RY a, RX a, RX b, MS, RY a; then an H on b as RY, RX
    ev = [("gate1", ("a",), "YRotation"), ("gate1", ("a",), "XRotation"), ("gate1", ("b",), "XRotation"),
          ("gate", ("a", "b"), None), ("gate1", ("a",), "YRotation"),
          ("gate1", ("b",), "YRotation"), ("gate1", ("b",), "XRotation")]
    events = [Event(id=k, kind=kind, t0=10.0 * k, t1=10.0 * k + 5, ions=ions, at="T0",
                    meta={"op": op} if op else {}) for k, (kind, ions, op) in enumerate(ev)]
    s = TimedSchedule(name="toy", device=PortGraph(sites={"T0": {"capacity": 2, "ports": {}}},
                                                   nodes={}, segments={}),
                      chains={"T0": ["a", "b"]}, events=events)
    ops = [{"name": "cx", "ions": ("a", "b"), "line": 1}, {"name": "h", "ions": ("b",), "line": 2}]
    pj = pulse_join(s, ops, JONES_NATIVE)
    assert pj["a"]["gate1"] == [0, 0, 0] and pj["b"]["gate1"] == [0, 1, 1]
    src = source_payload(to_tsir(s, name="toy", arch="x"), "c", ["cx a, b;", "h b;"], ops,
                         pulses=pj)
    assert sorted({i for v in src["realises"].values() for i in v}) == [0, 1]
    # a statement the table does not know is a wall: nothing on its ion is joined across it
    pj = pulse_join(s, [{"name": "cx", "ions": ("a", "b"), "line": 1},
                        {"name": "s", "ions": ("b",), "line": 2},
                        {"name": "h", "ions": ("b",), "line": 3}], JONES_NATIVE)
    assert pj["b"]["gate1"] == [0, None, None]


# ----------------------------------------------------------------- CX or MS (T37)


def _run(key: str, run_id: str):
    """A paper run's schedule and its circuit's statements, as the reproduce page reads them."""
    p = catalog.paper(key)
    run = next(r for r in p["runs"] if r["id"] == run_id)
    path = _open(ROOT / key / "artifact" / run["file"])
    s = (importers.import_tiscc(path, **run.get("importer_args", {})) if run["importer"] == "tiscc"
         else getattr(importers, f"import_{run['importer']}")(path))
    c = run["circuit"]
    if c["kind"] == "stim":
        ops = _stim_ops(ROOT / key / "circuits" / c["file"], s)
    else:
        g = "zz" if ".zz." in c["file"] else "cx"
        ops = [{"name": g, "ions": tuple(pq), "line": k + 1}
               for k, pq in enumerate(_circuit(ROOT / key, c))]
    return p, run, s, ops


@pytest.mark.parametrize("key, run_id, label", [
    ("khan2026cyclone", "bb72_cyclone", "CX"),              # CX layers, each one gcost
    ("khan2026cyclone", "hgp225_cyclone", "CX"),            # the published Z-check bug too
    ("khan2026cyclone", "bb72_baseline_Gx9_c5", "CX"),      # QCCDSim, one gate time per cx
    ("murali2020", "bv64_L6_c14_FM_GS_Greedy", "CX"),
    ("saki2022", "mts-qaoa-seed0", "CX"),                   # MTS, through the same reader
    ("khan2025moveless", "SC9_L8_Moveless_1m", "CX"),
    ("bach2025", "qft20_G2x3_c5_FM_GS_Greedy", "CX"),
    ("jones2025", "grid_rot_d2_c2", "MS"),                  # RY, RX around each MS
    ("jones2025", "linear_rep_d3_c3", "MS"),
    ("leblond2023", "idle_551", "MS"),                      # TISCC's native ZZ and rotations
])
def test_a_tool_that_runs_whole_cnots_is_shown_running_cx(key, run_id, label):
    p, run, s, ops = _run(key, run_id)
    assert gate_label(s, ops) == label


def test_a_dressing_pulse_or_a_missing_gate_keeps_the_native_name():
    _, _, s, ops = _run("khan2026cyclone", "bb72_cyclone")
    g = next(e for e in s.events if e.kind == "gate")
    pulse = Event(id=10**6, kind="gate1", t0=g.t0, t1=g.t0, ions=(g.ions[0],), at=g.at,
                  meta={"op": "YRotation"})
    assert gate_label(dataclasses.replace(s, events=s.events + [pulse]), ops) == "MS"
    assert gate_label(dataclasses.replace(s, events=[e for e in s.events if e is not g]), ops) == "MS"
    assert gate_label(s, [dict(o, name="zz") for o in ops]) == "MS"
    assert gate_label(s, []) == "MS"                        # no CNOT, nothing to call CX


def _page_inputs(key: str, run_id: str):
    """The machine and the schedule a reproduce page is drawn from (its `studio_pages` path)."""
    from qccd.api import Machine
    from qccd.arch import Architecture
    from qccd.repro.devices import layout

    p, run, s, ops = _run(key, run_id)
    s = named_after_qubits(s)
    fam, kw = ("ring", {}) if run["importer"] == "cyclone" else ("qccdsim_linear", {})
    cap = max(int(a["capacity"]) for a in s.device.sites.values())
    doc = studio_arch_doc(s, layout(s.device, fam, **kw), name="t", table=key,
                          gate_us=float(int(max(100.0, 13.33 * cap - 54))), note="test")
    return Machine(Architecture.from_json(doc)), s, ops


@pytest.mark.parametrize("key, run_id", [("khan2026cyclone", "bb72_cyclone"),
                                         ("murali2020", "bv64_L6_c14_FM_GS_Greedy")])
def test_calling_the_gate_cx_changes_nothing_but_its_name(key, run_id):
    from qccd.cost.models import corrected_model

    m, s, ops = _page_inputs(key, run_id)
    model = corrected_model("local")
    ms, cx = (to_tsir(s, name="t", arch="t", gate=g) for g in ("MS", "CX"))
    names = lambda prog: [i.gate for i in prog.instructions if i.type == "gate" and i.pairs]
    assert set(names(ms)) <= {"MS", "SWAP"} and "MS" in names(ms)   # a paid reorder stays a SWAP
    assert names(cx) == [{"MS": "CX"}.get(g, g) for g in names(ms)]
    # the same programme, instruction for instruction, but for the name
    as_ms = lambda prog: [json.dumps(dataclasses.asdict(i), sort_keys=True, default=str)
                          .replace('"gate": "CX"', '"gate": "MS"') for i in prog.instructions]
    assert as_ms(cx) == as_ms(ms)
    a, b = m.run(ms, model=model), m.run(cx, model=model)
    assert (a.cost, a.steps, a.runtime_ms) == (b.cost, b.steps, b.runtime_ms)
    assert a.runtime_ms > 0
    assert (a.rules_passed, a.rules_failed, len(a.violations)) == \
        (b.rules_passed, b.rules_failed, len(b.violations))
    lines = [f"l{k}" for k in range(len(ops))]
    ja, jb = (source_payload(prog, "c", lines, ops) for prog in (ms, cx))
    assert ja["realises"] == jb["realises"] and ja["toward"] == jb["toward"]
    assert len({i for v in jb["realises"].values() for i in v}) > 0
