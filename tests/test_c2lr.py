"""C2LR swap-or-stay transport, phase 1: the Python core (docs/c2lr.md, arXiv:2403.00756).

A C2LR lattice moves its crystals by direction bits and one shared junction waveform, so a
step's routes are the DEVICE's matching, never the programme's.  Held here, on hand-written
programmes on a 3 x 3 lattice of legs:

* the matching is read off the device (a matching under every variation), and a device that
  is not a lattice of legs is refused, saying why;
* the exchange rule: swap, stay, enter, move, exit -- and C1's two-crystal exception, which
  only the crystal that entered may end, by the step type it entered by;
* the price: one primitive per step however many crystals move, every crystal heated (the
  ones that stay by `quanta_stay`), merge + MS + split for a gate in one leg;
* the exemption: the same exchange written as plain moves fails R2, R3, R5 and R22, the
  template does not;
* a forged or ill-formed template is refused, with the reason;
* the legacy verifier and the rule language agree, rule by rule, on every programme here,
  under `c2lr` and `c2lr-demonstrated`;
* the certificate writes movers as plain moves and the transport check expands the template
  with the device -- and, where the Lean checker is built, it accepts them.

On the 3 x 3 lattice the W-S pairs are T0_0h-T1_0v (at J1_0), T0_1h-T1_1v (J1_1), T1_0h-T2_0v
(J2_0) and T1_1h-T2_1v (J2_1); T0_0v, T0_1v, T0_2h and T1_2h are unpaired (the edge).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.arch.c2lr import DIAGONALS, NotC2LR, Variation, geometry
from qccd.cost.models import corrected_model
from qccd.ir.tsir import (TSIR, Instruction, Participant, reorder_or_not, swap_or_stay,
                          validate_program)
from qccd.ruleset import FAILS, check, load
from qccd.ruleset.programme import programme_execution
from qccd.verify import ReplayError, verify

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "Compiler" / "bridge"

BA_SR = Machine.c2lr_grid(3, 3)
YB_BA = Machine.c2lr_grid(3, 3, species="Yb-Ba")
PLACE = {"q0": "T0_1h", "q1": "T1_1v", "q2": "T0_0h", "q3": "T2_1v", "q4": "T0_0v"}


def sos(diagonal=None, toward=(), **kw):
    return dict(type="simd", cls="swap_or_stay", mode="inter",
                template=swap_or_stay(diagonal, toward, **kw))


def build(steps, place=None) -> TSIR:
    prog = TSIR("c2lr test", "c2lr3x3")
    prog.add(Instruction(type="init", id=prog.next_id(), placement=dict(place or PLACE)))
    for s in steps:
        prog.add(Instruction(id=prog.next_id(), **s))
    return prog


#: swap, stay, enter + gate + exit, a move into an empty leg, and the cooling R7c asks for
TOUR = [
    sos("WS", ["q0", "q1"]),                             # 1  swap at J1_1
    sos("WS", ["q0"]),                                   # 2  q0 enters T0_1h, where q1 stayed
    dict(type="gate", gate="MS", mode="intra", pairs=(("q0", "q1"),)),   # 3  in one leg
    sos("WS", ["q0"]),                                   # 4  q0 exits the way it came
    sos("WS", ["q2"]),                                   # 5  q2 moves into the empty T1_0v
    dict(type="cool", broadcast=True),
]


def both(prog, m=BA_SR, ruleset="c2lr", config=None):
    """(legacy failed rules, rule-language failed rules, legacy report, language report)."""
    old = verify(prog, m.arch, corrected_model(), rule_config=config).rules
    new = check(load(ruleset), programme_execution(prog, m.arch, corrected_model(),
                                                   rule_config=config))
    return set(old.failed()), set(new.failed()), old, new


# ------------------------------------------------------------------------- the device

def test_the_lattice_and_its_matchings_are_read_off_the_device():
    g = geometry(BA_SR.arch)
    assert len(g.legs) == 12 and BA_SR.arch.device.nodes["T0_1h"].capacity == 2
    assert g.arm["J1_1"] == {"W": "T0_1h", "E": "T1_1h", "N": "T1_0v", "S": "T1_1v"}
    variations = [Variation("diagonal", diagonal=d) for d in DIAGONALS] + [
        Variation("checkerboard", parity=p, pairing=q) for p in (0, 1) for q in ("P1", "P2")]
    for var in variations:
        pairs = g.pairs(var)
        for leg, (j, partner) in pairs.items():          # a matching, at a shared junction
            assert pairs[partner] == (j, leg) and leg != partner
            assert j in g.ends[leg] and j in g.ends[partner]
    ws = g.pairs(Variation("diagonal", diagonal="WS"))
    assert ws["T0_1h"] == ("J1_1", "T1_1v") and ws["T0_0h"] == ("J1_0", "T1_0v")
    assert {"T0_0v", "T0_1v", "T0_2h", "T1_2h"}.isdisjoint(ws)


def test_a_device_that_is_not_a_c2lr_lattice_is_refused_with_its_reason():
    with pytest.raises(NotC2LR, match="does not declare C2LR control"):
        geometry(Machine.grid(3, 3).arch)
    ring = Machine.ring(3, 2, 2)
    doc = ring.arch.to_json()
    doc["control"] = {"model": "c2lr"}
    from qccd.arch import Architecture
    with pytest.raises(NotC2LR, match="leg"):
        geometry(Architecture.from_json(doc))
    # and a template on a device that does not declare C2LR control does not replay at all
    with pytest.raises(ReplayError, match="needs a C2LR device"):
        verify(build([sos("WS", ["q0"])], {"q0": "T0_1h"}), Machine.grid(3, 3).arch,
               corrected_model())


# ------------------------------------------------------------------------- the step

def test_swap_stay_enter_gate_exit_and_move_pass_both_verifiers():
    prog = build(TOUR)
    assert validate_program(prog) == []
    for rs, cfg in (("c2lr", None), ("c2lr-demonstrated", {"c2lr_forms": ("diagonal",)})):
        old, new, rep, lang = both(prog, ruleset=rs, config=cfg)
        assert old == new == set(), (rs, rep.violations, lang.failed())
        assert {"C1", "C6", "R2", "R3", "R5", "R22"} <= set(rep.passed())
    res = verify(prog, BA_SR.arch, corrected_model()).result
    assert res.final_positions == {"q0": "T1_1v", "q1": "T0_1h", "q2": "T1_0v",
                                   "q3": "T2_1v", "q4": "T0_0v"}
    assert prog.templates() == {"swap_or_stay:WS": 4}


def test_one_step_takes_the_primitives_time_and_heats_every_crystal():
    spec = BA_SR.arch.primitives.scalar("swap_or_stay")
    assert (spec["us"], spec["quanta_swap"], spec["quanta_stay"]) == (312.5, 0.14, 0.035)
    res = verify(build(TOUR), BA_SR.arch, corrected_model()).result
    steps = [c for c in res.cycles if c.type == "simd"]
    assert [c.t1 - c.t0 for c in steps] == [312.5] * 4
    c2lr = {i: q.get("c2lr", 0.0) for i, q in res.per_ion_quanta.items()}
    sw, st = 0.14, 0.035
    want = {"q0": 3 * sw + st, "q1": sw + 3 * st, "q2": sw + 3 * st, "q3": 4 * st, "q4": 4 * st}
    assert c2lr == pytest.approx(want)                   # the stayers are not cold (C9)
    # nothing moving is still one waveform: its full time, and every crystal heated
    idle = verify(build([sos("WS", [])]), BA_SR.arch, corrected_model()).result
    assert idle.total_us == 312.5
    assert {i: q["c2lr"] for i, q in idle.per_ion_quanta.items()} == pytest.approx(
        {i: st for i in PLACE})
    # Yb-Ba's step, and reorder-or-not, which the paper prices for Yb-Ba only
    yb = verify(build([sos("WS", ["q0", "q1"]),
                       dict(type="simd", cls="reorder_or_not", mode="inter",
                            template=reorder_or_not(["q3"]))]),
                YB_BA.arch, corrected_model()).result
    assert [c.t1 - c.t0 for c in yb.cycles if c.type == "simd"] == [400.0, 93.0]
    assert yb.per_ion_quanta["q4"]["c2lr"] == pytest.approx(0.165 + 0.0195)
    assert yb.final_positions == {**PLACE, "q0": "T1_1v", "q1": "T0_1h"}
    with pytest.raises(ReplayError, match="prices no reorder_or_not"):
        verify(build([dict(type="simd", cls="reorder_or_not", mode="inter",
                           template=reorder_or_not(["q3"]))]), BA_SR.arch, corrected_model())


def test_a_gate_in_one_leg_is_merge_ms_split_and_r7_sees_the_merge():
    res = verify(build(TOUR), BA_SR.arch, corrected_model()).result
    gate = next(c for c in res.cycles if c.type == "gate")
    ms = float(BA_SR.arch.primitives.scalar("ms_gate")["us"])
    assert gate.t1 - gate.t0 == pytest.approx(40.0 + ms + 40.0)
    assert res.per_ion_quanta["q0"]["split_merge"] == pytest.approx(0.2)
    assert res.per_ion_quanta["q3"].get("split_merge", 0.0) == 0.0
    # q0 at the MS gate: two swap-or-stay moves, the anomalous heat of 625 us, and the merge
    anomalous = BA_SR.arch.anomalous_rate() / 1000.0 * 625.0
    assert res.max_gate_quanta_seen == pytest.approx(2 * 0.14 + anomalous + 0.1)


# ------------------------------------------------------------------------- the exemption

PLAIN_SWAP = (Participant("q0", "T0_1h", "T1_1v", ("T0_1h.b", "T1_1v.a")),
              Participant("q1", "T1_1v", "T0_1h", ("T1_1v.a", "T0_1h.b")))


@pytest.mark.parametrize("cls", ["swap_or_stay", "shuttle"])
def test_the_same_exchange_as_plain_moves_breaks_r2_r3_r5_r22(cls):
    """The template's exemption is earned by where its routes come from: the identical
    exchange written by the programme -- even under the class `swap_or_stay` -- is judged in
    full, and the class it borrows is C2's to refuse."""
    forged = build([dict(type="simd", cls=cls, mode="inter", participants=PLAIN_SWAP)])
    old, new, rep, _ = both(forged)
    assert {"R2", "R3", "R5", "R22"} <= old and old == new
    assert ("C2" in old) == (cls == "swap_or_stay") and ("R4" in old) == (cls == "shuttle")
    assert verify(forged, BA_SR.arch, corrected_model()).result.final_positions == \
        verify(build([sos("WS", ["q0", "q1"])]), BA_SR.arch, corrected_model()).result.final_positions
    old, new, *_ = both(build([sos("WS", ["q0", "q1"])]))
    assert old == new == set()


# ------------------------------------------------------------------------- refusals

@pytest.mark.parametrize("template, why", [
    ({"kind": "swap_or_stay", "checkerboard": {"parity": 2, "pairing": "P1"}, "toward": []},
     "parity 2 is not 0 or 1"),
    ({"kind": "swap_or_stay", "diagonal": "WE", "toward": []}, "diagonal 'WE'"),
    ({"kind": "swap_or_stay", "diagonal": "WS", "checkerboard": {"parity": 0, "pairing": "P1"},
      "toward": []}, "exactly one of"),
    ({"kind": "swap_or_stay", "diagonal": "WS", "toward": ["q0"], "routes": {"q0": "T1_1v"}},
     "only per-crystal choice"),
    ({"kind": "swap_or_stay", "diagonal": "WS", "toward": ["q0", "q0"]}, "twice"),
    ({"kind": "swap_or_stay", "diagonal": "WS", "toward": ["q9"]}, "not placed"),
    ({"kind": "swap_or_shift", "diagonal": "WS"}, "unknown movement template"),
])
def test_an_ill_formed_template_is_refused_with_its_reason(template, why):
    prog = build([dict(type="simd", cls="swap_or_stay", mode="inter", template=template)])
    with pytest.raises(ReplayError, match=why):
        verify(prog, BA_SR.arch, corrected_model())
    with pytest.raises(ReplayError, match=why):
        programme_execution(prog, BA_SR.arch, corrected_model())


def test_a_template_that_names_its_own_routes_is_refused():
    prog = build([dict(type="simd", cls="swap_or_stay", mode="inter",
                       template=swap_or_stay("WS", ["q0", "q1"]), participants=PLAIN_SWAP)])
    assert any("own participants" in e for e in validate_program(prog))
    with pytest.raises(ReplayError, match="its routes are the device's"):
        verify(prog, BA_SR.arch, corrected_model())


def test_a_bit_on_a_leg_with_no_partner_breaks_c6_and_the_crystal_stays():
    prog = build([sos("WS", ["q4"])])                     # T0_0v: J0_0 has no W leg
    old, new, rep, lang = both(prog)
    assert old == new == {"C6"}
    assert "no partner under WS" in next(v.message for v in rep.violations if v.rule == "C6")
    assert verify(prog, BA_SR.arch, corrected_model()).result.final_positions == PLACE


def test_the_checkerboard_is_assumed_and_c2lr_demonstrated_refuses_it():
    step = sos(checkerboard={"parity": 0, "pairing": "P1"}, toward=["q0", "q1"])
    prog = build([step])
    old, new, rep, lang = both(prog)
    assert old == new == set()
    assert rep.summary()["assumed"] and lang.summary()["assumed"]
    assert "not demonstrated" in lang.assumed[0]
    old, new, rep, _ = both(prog, ruleset="c2lr-demonstrated", config={"c2lr_forms": ("diagonal",)})
    assert old == new == {"C6"}
    # a device that does not offer the checkerboard fails C6 under either rule set
    no_cb = Machine.c2lr_grid(3, 3, checkerboard=False)
    old, new, *_ = both(prog, m=no_cb)
    assert old == new == {"C6"}
    # and a diagonal step is never marked assumed
    assert "assumed" not in verify(build(TOUR), BA_SR.arch, corrected_model()).rules.summary()


# ------------------------------------------------------------------------- C1

ENTER = [sos("WS", ["q0", "q1"]), sos("WS", ["q0"])]      # q0 shares T0_1h with q1


@pytest.mark.parametrize("last, broken", [
    (sos("WS", ["q0"]), set()),                           # exit the way it came
    (sos("WN", ["q0"]), {"C1"}),                          # exit by another diagonal
    (sos("ES", ["q0"]), {"C1"}),                          # q1, not q0, is nearer J0_1
    (sos("ES", ["q1"]), {"C1"}),                          # the resident leaves
    (dict(type="simd", cls="swap_or_stay", mode="inter",
          participants=(Participant("q0", "T0_1h", "T1_1v", ("T0_1h.b", "T1_1v.a")),)),
     {"C1", "C2"}),                                       # a plain move out
])
def test_only_the_crystal_that_entered_may_exit_and_by_the_same_step_type(last, broken):
    old, new, rep, lang = both(build(ENTER + [last]))
    assert old == new == broken, (rep.violations, lang.failed())


def test_two_crystals_in_a_leg_without_an_enter_break_c1():
    # a plain move into an occupied leg: two crystals, but no enter
    plain = build([dict(type="simd", cls="swap_or_stay", mode="inter",
                        participants=(Participant("q2", "T0_0h", "T0_1h", ("T0_0h.a", "T0_0v.a",
                                                                         "T0_0v.b", "T0_1h.a")),))])
    old, new, rep, _ = both(plain)
    assert "C1" in old and old == new
    # placed two to a leg from the start
    old, new, *_ = both(build([sos("WS", [])], {"q0": "T0_1h", "q1": "T0_1h"}))
    assert old == new == {"C1"}
    # a third crystal into a leg that holds two: C1, and the leg's capacity (R1)
    third = build(ENTER + [sos("WS", ["q5"])], {**PLACE, "q5": "T1_1v"})
    old, new, *_ = both(third)
    assert {"C1", "R1"} <= old and old == new


def test_a_crystal_resting_off_the_legs_breaks_c5_and_c7():
    off = build([dict(type="simd", cls="swap_or_stay", mode="inter",
                      participants=(Participant("q0", "T0_1h", "J1_1", ("T0_1h.b",)),))])
    old, new, *_ = both(off)
    # R1 apart: the legacy R1 also holds a junction node to its capacity 0, the rule
    # language's only the sites -- a disagreement older than C2LR, not this file's subject
    assert {"C5", "C7"} <= new and old - {"R1"} == new


def test_the_c_rules_are_silent_on_every_other_device():
    m = Machine.ring(3, 2, 2, name="ring6d")
    p = m.program("one", provenance="off")
    p.init({"d0": "S1"})
    p.move("d0", "S1", "S2")
    rep = verify(p.build(), m.arch, corrected_model()).rules
    assert not [r for r in rep.checked | set(rep.passed()) if r.startswith("C")]
    lang = check(load("c2lr"), programme_execution(p.build(), m.arch, corrected_model()))
    assert all(r.verdict != FAILS for r in lang.results if r.id.startswith("C"))
    assert "assumed" not in rep.summary() and "assumed" not in lang.summary()


# ------------------------------------------------------------------------- certificate

def _cc():
    spec = importlib.util.spec_from_file_location("check_cert_c2lr", BRIDGE / "check_cert.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BRIDGE))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(BRIDGE))
    return mod


def _cert(moves) -> dict:
    return {"version": 1, "circuit": "c2lr", "arch": BA_SR.arch.name, "n_qubits": 2,
            "map": {"0": "q0", "1": "q1"}, "init": dict(PLACE),
            "circuit_ops": [{"i": 0, "name": "cx", "qubits": [0, 1], "params": [], "line": 4}],
            "moves": moves, "rotations": [], "unrealised": [],
            "gates": [{"dag": 0, "instr": 3, "t": 2, "site": "T0_1h", "ions": ["q0", "q1"],
                       "pulses": ["MS(1.5708)@q0,q1"]}]}


#: the tour's movers, written as plain moves at each step's time; the stayers write nothing
TOUR_MOVES = [
    {"t": 0, "ion": "q0", "from": "T0_1h", "to": "T1_1v", "via": ["T0_1h.b", "T1_1v.a"]},
    {"t": 0, "ion": "q1", "from": "T1_1v", "to": "T0_1h", "via": ["T1_1v.a", "T0_1h.b"]},
    {"t": 1, "ion": "q0", "from": "T1_1v", "to": "T0_1h", "via": ["T1_1v.a", "T0_1h.b"]},
    {"t": 3, "ion": "q0", "from": "T0_1h", "to": "T1_1v", "via": ["T0_1h.b", "T1_1v.a"]},
    {"t": 4, "ion": "q2", "from": "T0_0h", "to": "T1_0v", "via": ["T0_0h.b", "T1_0v.a"]},
]


def test_the_certificate_writes_movers_as_plain_moves_and_the_check_expands_the_template():
    cc = _cc()
    prog, cert = build(TOUR), _cert(TOUR_MOVES)
    assert cc.check_program_transport(cert, prog, BA_SR.arch) == []
    assert cc.check_o1(cert, BA_SR.arch) == []
    assert "only given the device" in cc.check_program_transport(cert, prog)[0]
    # a certificate that moves a crystal the step kept still, or drops a mover, disagrees
    stayer = _cert(TOUR_MOVES + [{"t": 4, "ion": "q3", "from": "T2_1v", "to": "T1_1h"}])
    assert any("q3" in p for p in cc.check_program_transport(stayer, prog, BA_SR.arch))
    dropped = _cert(TOUR_MOVES[:-1])
    assert any("q2" in p for p in cc.check_program_transport(dropped, prog, BA_SR.arch))


def _qcheck() -> Path | None:
    env = os.environ.get("QCCD_QCHECK")
    if env and Path(env).is_file():
        return Path(env)
    for base in (ROOT, ROOT.parents[2] if len(ROOT.parents) > 2 else ROOT):
        for d in (base / "Compiler" / "lean" / "checker" / ".lake" / "build" / "bin",
                  base / "Compiler" / "lean" / ".lake" / "build" / "bin"):
            for name in ("qcheck.exe", "qcheck"):
                if (d / name).is_file():
                    return d / name
    return None


def test_the_proved_checker_accepts_a_swap_written_as_two_moves_at_one_time(tmp_path):
    """No change to the Lean checker: it reads each move from the start-of-cycle state, and
    the hop table (`mk_qcheck_input.trap_hops`) already holds leg -> junction -> leg."""
    exe = _qcheck()
    if exe is None:
        pytest.skip("the Lean checker (qcheck) is not built here")

    def bridge(name):
        spec = importlib.util.spec_from_file_location(f"{name}_c2lr", BRIDGE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    arch = bridge("export_arch").expand(BA_SR.arch)
    hops = {(h["from"], h["to"]) for h in bridge("mk_qcheck_input").trap_hops(arch)}
    assert {(m["from"], m["to"]) for m in TOUR_MOVES} <= hops
    good = tmp_path / "good.json"
    good.write_text(json.dumps(bridge("mk_qcheck_input").build(_cert(TOUR_MOVES), arch)))
    out = subprocess.run([str(exe), str(good)], capture_output=True, text=True, timeout=300)
    assert "ACCEPTED" in out.stdout, out.stdout + out.stderr
    # and it still refuses a gate witness the moves do not support (q0's enter left out)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bridge("mk_qcheck_input").build(
        _cert([m for m in TOUR_MOVES if m["t"] not in (1, 3)]), arch)))
    out = subprocess.run([str(exe), str(bad)], capture_output=True, text=True, timeout=300)
    assert "ACCEPTED" not in out.stdout


# ------------------------------------------------------------------------- the other readers

def test_the_text_form_listing_and_page_read_a_c2lr_programme():
    from qccd.ir.listing import disassemble
    from qccd.ir.tsir_text import parse_program, program_text
    from qccd.viz.render import build_view_model
    prog = build(TOUR + [dict(type="simd", cls="reorder_or_not", mode="inter",
                              template=reorder_or_not(["q3"]))])
    again = parse_program(program_text(prog))
    assert [i.template for i in again.instructions] == [i.template for i in prog.instructions]
    lines = disassemble(prog, BA_SR.arch).lines
    assert lines[1].detail.startswith("swap-or-stay WS: toward q0, q1")
    assert lines[-1].detail == "reorder-or-not reorder: reverse q3"
    priced = disassemble(build(TOUR), BA_SR.arch, model=corrected_model()).lines
    assert [p.primitive for p in priced[1].opoints] == ["swap_or_stay"]
    assert priced[1].opoints[0].us == 312.5
    rep = verify(build(TOUR), BA_SR.arch, corrected_model())
    vm = build_view_model(BA_SR.arch, build(TOUR), rep.result, corrected_model(),
                          include_listing=False, include_control=False, provenance="off")
    swap = next(f for f in vm["program"]["frames"] if f.get("c2lr"))
    assert sorted(m[0] for m in swap["moves"]) == ["q0", "q1"]
    assert swap["moves"][0][1] in (["T0_1h", "J1_1", "T1_1v"], ["T1_1v", "J1_1", "T0_1h"])


def test_the_control_panel_reads_a_c2lr_step_off_the_census():
    """A C2LR device declares the paper's census, not a channel map: its steps are transport
    on every shared signal, its gates optical, its init idle -- never "undeclared"."""
    from qccd.verify.control import control_trace
    trace = control_trace(build(TOUR), BA_SR.arch, corrected_model())
    got = [(r.type, r.driver, r.action) for r in trace.records]
    assert got == [("init", "idle", "init"),
                   ("simd", "transport", "swap_or_stay WS"),
                   ("simd", "transport", "swap_or_stay WS"),
                   ("gate", "optical", "MS"),
                   ("simd", "transport", "swap_or_stay WS"),
                   ("simd", "transport", "swap_or_stay WS"),
                   ("cool", "optical", "COOL (broadcast)")]
    swap = trace.records[1]
    assert swap.channels_engaged == swap.channels_total == 27
    assert swap.sites_acting == 2 and swap.sites_held == 0 and swap.switch_elements_total == 0
    assert swap.feasible is None                   # the C rules judge it, not R4d
    assert not any("declares no control.channels" in n for n in trace.notes)
    assert any("27 shared analog signals" in n for n in trace.notes)
