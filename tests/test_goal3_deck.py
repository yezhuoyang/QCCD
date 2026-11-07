"""Goal 3 on the transport deck ("QCCD Transport Architectures") and the Helios nominal noise model.

* the deck's example tables are the census functions' output, number for number;
* the noise text writes the model's Stim column, and each of its rules (idle window, heat excess);
* the three compilers run surface d=3, each keeping its family's rule set and the circuit's order
  (the certificate checker is the independent word on that);
* each new rule catches a programme forged to break it;
* a family's prices are the deck's, step by step.
"""

from __future__ import annotations

import copy
import json
import math
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "goals" / "goal3"
for p in (ROOT, GOAL):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
pytest.importorskip("stim")

from qccd.arch import Architecture, c2lr, cyclone, wise  # noqa: E402
from qccd.compile import c2lr_router, cyclone_router, wise_router  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction, Participant  # noqa: E402
from qccd.ruleset import FAILS, check, legacy_config, load as load_rules  # noqa: E402
from qccd.ruleset.compile import circuit_of_certificate  # noqa: E402
from qccd.ruleset.programme import programme_execution  # noqa: E402

PHYS = json.loads((ROOT / "qccd" / "workspace" / "physics" / "qccd-physics@3.json").read_text(encoding="utf-8"))
TABLE = "helios"


# ------------------------------------------------------------------ the deck's tables


@pytest.mark.parametrize("cx,cy,want", [
    (4, 4, {"cells": 16, "legs": 32, "laser_pairs": 32, "broadcast_electrodes": 384, "c2lr_electrodes": 64,
            "shim_electrodes": 64, "junction_electrodes": 64, "total_electrodes": 576, "transport_dacs": 27,
            "shim_dacs_max": 64, "total_dacs_max": 91, "total_dacs_demultiplexed": 28, "switches": 32}),
    (16, 16, {"cells": 256, "legs": 512, "laser_pairs": 512, "broadcast_electrodes": 6144, "c2lr_electrodes": 1024,
              "shim_electrodes": 1024, "junction_electrodes": 1024, "total_electrodes": 9216, "transport_dacs": 27,
              "shim_dacs_max": 1024, "total_dacs_max": 1051, "total_dacs_demultiplexed": 36, "switches": 512}),
    (22, 23, {"cells": 506, "legs": 1012, "laser_pairs": 1012, "broadcast_electrodes": 12144, "c2lr_electrodes": 2024,
              "shim_electrodes": 2024, "junction_electrodes": 2024, "total_electrodes": 18216, "transport_dacs": 27,
              "shim_dacs_max": 2024, "total_dacs_max": 2051, "total_dacs_demultiplexed": 43, "switches": 1012}),
])
def test_the_deck_s_c2lr_examples(cx, cy, want):
    got = c2lr.deck_census(cx, cy)
    assert {k: got[k] for k in want} == want
    assert round(got["active_area_mm2"], 1) == {16: 2.2, 256: 36.0, 506: 71.2}[got["cells"]]


@pytest.mark.parametrize("h,cx,cy,want", [
    (5, 9, 19, {"cells": 171, "zones_per_cell": 6, "qubits": 1026, "gate_zones": 855, "grid_m": 54, "grid_n": 19,
                "dynamic_electrodes": 11970, "shim_electrodes": 10260, "dynamic_dacs": 120, "shim_dacs": 81,
                "transmission_gates": 34200, "off_chip_connections": 211, "reconfiguration_steps": 222, "laser_pairs": 855}),
    (3, 13, 20, {"cells": 260, "zones_per_cell": 4, "qubits": 1040, "gate_zones": 780, "grid_m": 52, "grid_n": 20,
                 "dynamic_electrodes": 13000, "shim_electrodes": 10400, "dynamic_dacs": 120, "shim_dacs": 82,
                 "transmission_gates": 36400, "off_chip_connections": 212, "reconfiguration_steps": 184, "laser_pairs": 780}),
    (7, 8, 16, {"cells": 128, "zones_per_cell": 8, "qubits": 1024, "gate_zones": 896, "grid_m": 64, "grid_n": 16,
                "dynamic_electrodes": 11520, "shim_electrodes": 10240, "dynamic_dacs": 120, "shim_dacs": 81,
                "transmission_gates": 33280, "off_chip_connections": 211, "reconfiguration_steps": 256, "laser_pairs": 896}),
])
def test_the_deck_s_wise_examples(h, cx, cy, want):
    got = wise.census(h, cx, cy)
    assert {k: got[k] for k in want} == want
    areas = {5: (164, 86, 103), 3: (166, 91, 104), 7: (164, 83, 102)}[h]
    assert (round(got["area_active_mm2"]), round(got["area_switch_mm2"]), round(got["area_capacitor_mm2"])) == areas
    assert round(got["switch_word_us"]) == {5: 21, 3: 21, 7: 20}[h]
    assert round(got["reconfiguration_ms"]) == {5: 22, 3: 18, 7: 26}[h]


@pytest.mark.parametrize("data,stab,x,n,want", [
    (144, 144, 72, 3, {"ancillas": 72, "total_ions": 216, "smallest_ring_at_30": 8, "shuttling_paths": 72, "l_junctions": 4,
                       "dc_electrodes": 800, "dacs": 30, "moves_per_ancilla_per_round": 144, "laser_pairs": 216}),
    (225, 216, 108, 4, {"ancillas": 108, "total_ions": 333, "smallest_ring_at_30": 12, "shuttling_paths": 108, "l_junctions": 4,
                        "dc_electrodes": 1160, "dacs": 30, "moves_per_ancilla_per_round": 216, "laser_pairs": 432}),
    (625, 600, 300, 4, {"ancillas": 300, "total_ions": 925, "smallest_ring_at_30": 31, "shuttling_paths": 300, "l_junctions": 4,
                        "dc_electrodes": 3080, "dacs": 30, "moves_per_ancilla_per_round": 600, "laser_pairs": 1200}),
])
def test_the_deck_s_cyclone_examples(data, stab, x, n, want):
    got = cyclone.census(data, stab, x, n)
    assert {k: got[k] for k in want} == want
    assert cyclone.fits(data, stab // 2, x, n) and not cyclone.fits(data, stab // 2, x, n - 1)


def test_the_gate_time_law_is_flat_to_twelve_ions_and_scaled_to_the_noise_model():
    assert cyclone.chain_gate_us(2) == cyclone.chain_gate_us(11) == pytest.approx(70.0)
    assert cyclone.chain_gate_us(15) == pytest.approx(70 * (13.33 * 15 - 54) / 100)
    assert cyclone.chain_gate_us(30) > 3 * cyclone.chain_gate_us(12)


def test_the_families_are_the_deck_s_three_and_their_sizes_the_deck_s():
    import families
    assert families.FAMILIES == ("C2LR", "WISE", "Cyclone")
    assert set(families.RULES) == set(families.FAMILIES) == set(families.COLOURS)
    for code in ("surface3", "surface7", "color5", "bb72", "hgp58"):
        meta = json.loads((GOAL / "codes" / f"{code}_z" / "meta.json").read_text(encoding="utf-8"))
        ions, anc = meta["ions"], meta["n_ancilla"]
        for p in families.points_for("Cyclone", meta):
            x, n = p["params"]["x"], p["params"]["n"]
            assert x % 2 == 0 and n <= 30 and x * n >= ions and cyclone.fits(ions - anc, anc, x, n), p["name"]
        for p in families.points_for("WISE", meta):
            assert wise.gate_zones(**p["params"]) >= 1.1 * ions, p["name"]
        for p in families.points_for("C2LR", meta):
            assert 2 * p["params"]["a"] * p["params"]["b"] - p["params"]["a"] - p["params"]["b"] >= 1.1 * ions, p["name"]
            assert p["species"] == "Yb-Ba"


# ------------------------------------------------------------------ the three compilers


def _compile(family: str, tmp: Path):
    import boards
    import sweep
    exp = boards.experiment("surface3")
    point = {"C2LR": {"family": "C2LR", "name": "c2lr5x4", "generator": "grid", "params": {"a": 5, "b": 4}, "species": "Yb-Ba"},
             "WISE": {"family": "WISE", "name": "wise_h3_3x3", "generator": "wise", "params": {"h": 3, "cx": 3, "cy": 3}},
             "Cyclone": {"family": "Cyclone", "name": "cyclone8_n3", "generator": "cyclone", "params": {"x": 8, "n": 3}}}[family]
    work = tmp / family
    work.mkdir()
    arch_p = work / "device.arch.json"
    arch_p.write_text(json.dumps(sweep.device_doc(point, exp)), encoding="utf-8")
    cooled, cert_p = sweep.compile_point(point, exp, arch_p, work)
    arch = Architecture.from_json(json.loads(arch_p.read_text(encoding="utf-8")))
    return exp, arch, TSIR.load(str(cooled)), json.loads(cert_p.read_text(encoding="utf-8")), work


@pytest.fixture(scope="module", params=["C2LR", "WISE", "Cyclone"])
def compiled(request, tmp_path_factory):
    return (request.param, *_compile(request.param, tmp_path_factory.mktemp("deck")))


@pytest.fixture(scope="module")
def wise_prog(tmp_path_factory):
    return _compile("WISE", tmp_path_factory.mktemp("w"))


@pytest.fixture(scope="module")
def cyclone_prog(tmp_path_factory):
    return _compile("Cyclone", tmp_path_factory.mktemp("c"))


def _judge(rs_name, prog, arch, cert):
    rs = load_rules(rs_name)
    exe = programme_execution(prog, arch, corrected_model(TABLE), circuit=circuit_of_certificate(cert),
                              rule_config=legacy_config(rs) or None, check_metrics=False)
    return {x.id: x.count for x in check(rs, exe).results if x.verdict == FAILS}


def test_each_family_keeps_its_own_rule_set(compiled):
    import families
    family, _exp, arch, prog, cert, _work = compiled
    assert _judge(families.RULES[family], prog, arch, cert) == {}


def test_the_certificate_checker_finds_the_circuit_s_order_kept(compiled):
    """The independent word on what the compilers did: every op in its place on every qubit."""
    _family, _exp, _arch, _prog, _cert, work = compiled
    done = subprocess.run([sys.executable, str(ROOT / "Compiler" / "bridge" / "check_cert.py"), str(work / "prog"),
                           "--qasm", str(work / "circuit.qasm"), "--arch", str(work / "device.arch.json"), "--json"],
                          capture_output=True, text=True, cwd=ROOT)
    got = json.loads(done.stdout)
    assert got["order"] == "ok" and got["transport"] == "ok", done.stdout[-400:]


def test_the_noiseless_program_is_the_experiment(compiled):
    from qccd.qec import evaluate_memory
    _family, exp, _arch, _prog, cert, work = compiled
    rep = evaluate_memory(str(work / "prog.cooled.tsir.json"), str(work / "device.arch.json"), exp, noise_id="qccd-noise@3",
                          cert=cert, ler_budget={"decoder": "pymatching", "max_shots": 2000, "max_errors": 5}, seed=1)
    assert rep["check"]["ok"], rep["check"]


def test_a_readout_run_reads_together_but_gates_keep_their_layers():
    src = """OPENQASM 2.0;
qreg q[3];
creg c[2];
h q[0];
h q[1];
h q[2];
cx q[1],q[2];
cx q[0],q[1];
measure q[1] -> c[0];
reset q[1];
measure q[2] -> c[1];
reset q[2];
h q[0];
"""
    ops = c2lr_router.parse_qasm(src)[1]
    gates = lambda lays: [[o.name for o in lay if o.name not in ("measure", "reset")] for lay in lays]  # noqa: E731
    got = c2lr_router.layers_reading_together(ops)
    # the gates' layers are `layers`' own; only the readouts close up
    assert gates(got)[:3] == gates(c2lr_router.layers(ops))[:3] == [["h", "h", "h"], ["cx"], ["cx"]]
    run = c2lr_router.parse_qasm('OPENQASM 2.0;\nqreg q[3];\ncreg c[3];\n' + "".join(
        f"measure q[{q}] -> c[{q}];\nreset q[{q}];\n" for q in range(3)))[1]
    count = lambda lays: sum(any(o.name == "measure" for o in lay) for lay in lays)  # noqa: E731
    assert count(c2lr_router.layers(run)) == 3 and count(c2lr_router.layers_reading_together(run)) == 1
    # every qubit keeps its sequence, in any case
    for q in range(3):
        want = [o.i for o in ops if q in o.qubits]
        assert [o.i for lay in got for o in lay if q in o.qubits] == want


# ------------------------------------------------------------------ what each new rule catches


def _forged(prog: TSIR, change) -> TSIR:
    out = TSIR.from_json(copy.deepcopy(prog.to_json()))
    change(out)
    return out


def _first(prog: TSIR, pred):
    return next(i for i in prog.instructions if pred(i))


def test_w2_catches_a_step_outside_its_kind(wise_prog):
    _exp, arch, prog, cert, _work = wise_prog
    def change(p):
        ins = _first(p, lambda i: i.cls == "wise_h0_a")
        ins_i = p.instructions.index(ins)
        p.instructions[ins_i] = replace(ins, cls="wise_h1_a")
    assert "W2" in _judge("wise", _forged(prog, change), arch, cert)


def test_w10_catches_two_angles_in_one_layer(wise_prog):
    _exp, arch, prog, cert, _work = wise_prog
    def change(p):
        i = p.instructions.index(_first(p, lambda x: x.gate == "R" and len(x.ions) >= 2))
        ins = p.instructions[i]
        params = list(ins.params)
        params[0] = (params[0][0] + 0.1,) + tuple(params[0][1:])
        p.instructions[i] = replace(ins, params=tuple(params))
    assert "W10" in _judge("wise", _forged(prog, change), arch, cert)


def test_w11_catches_a_reconfiguration_with_no_shim_charge(wise_prog):
    _exp, arch, prog, cert, _work = wise_prog
    def change(p):
        i = p.instructions.index(_first(p, lambda x: (x.meta or {}).get("kind") == "shim_charge"))
        del p.instructions[i]
    assert "W11" in _judge("wise", _forged(prog, change), arch, cert)
    cheap = copy.deepcopy(arch.to_json())
    cheap["primitives"]["ms_gate"]["us"] = 70
    assert "W11" in _judge("wise", prog, Architecture.from_json(cheap), cert)


def test_w1_catches_a_pair_left_together(wise_prog):
    _exp, arch, prog, cert, _work = wise_prog
    def change(p):
        i = p.instructions.index(_first(p, lambda x: (x.meta or {}).get("kind") == "split_after_gate"))
        ins = p.instructions[i]
        p.instructions.insert(i, Instruction(id=p.next_id(), type="reset", ions=(ins.participants[0].ion,)))
    assert "W1" in _judge("wise", _forged(prog, change), arch, cert)


def test_y2_and_y3_catch_data_moved_and_an_ancilla_left_behind(cyclone_prog):
    _exp, arch, prog, cert, _work = cyclone_prog
    def move_data(p):
        i = p.instructions.index(_first(p, lambda x: x.type == "simd"))
        ins = p.instructions[i]
        d = p.meta["data_ions"][0]
        here = next(v for k, v in p.instructions[0].placement.items() if k == d)
        slots = list(arch.device.loop_slots(next(iter(arch.device.loops))))
        away = slots[(slots.index(here) + 1) % len(slots)]
        p.instructions[i] = replace(ins, participants=ins.participants + (Participant(d, here, away),))
    assert "Y2" in _judge("cyclone-deck", _forged(prog, move_data), arch, cert)
    def leave_one(p):
        i = p.instructions.index(_first(p, lambda x: x.type == "simd"))
        ins = p.instructions[i]
        p.instructions[i] = replace(ins, participants=ins.participants[1:])
    # what stays behind makes the next hop wrong as well, so the replay may refuse it: either way it is not a design
    from qccd.verify.replay import ReplayError
    try:
        assert "Y3" in _judge("cyclone-deck", _forged(prog, leave_one), arch, cert)
    except ReplayError:
        pass


def test_the_rule_words_run_in_the_debug_page_s_python():
    """The Debug page's bundle holds no compiler, so a word that needs one fails there on WISE alone."""
    from qccd.site.debugpage import PY_PACKAGES
    assert "compile" not in PY_PACKAGES and {"ruleset", "arch"} <= set(PY_PACKAGES)
    src = (ROOT / "qccd" / "ruleset" / "transport.py").read_text(encoding="utf-8")
    assert "..compile" not in src and "qccd.compile" not in src
    for mod in ("wise.py", "cyclone.py"):
        text = (ROOT / "qccd" / "arch" / mod).read_text(encoding="utf-8")
        assert "..compile" not in text, mod


def test_y1_and_y4_read_the_device():
    from qccd.api import Machine
    ring = Architecture.from_json(cyclone.device_doc(8, 4))
    grid = copy.deepcopy(Machine.grid(3, 3, name="g").arch.to_json())
    grid["geometry"]["params"]["family"] = "cyclone"
    for rid, doc, ok in (("Y1", ring.to_json(), True), ("Y1", grid, False)):
        prog = TSIR(name="x", arch_spec="a")
        arch = Architecture.from_json(doc)
        first = next(n for n, v in arch.device.nodes.items() if v.kind == "site")
        prog.add(Instruction(type="init", id=prog.next_id(), placement={"q0": first}, quanta={"q0": 0.0}))
        got = _judge("cyclone-deck", prog, arch, {"circuit_ops": [], "map": {"0": "q0"}})
        assert (rid not in got) == ok, (rid, doc["name"])
    big = copy.deepcopy(ring.to_json())
    big["zone_types"]["trap"]["capacity"] = 31
    for node in big["geometry"]["nodes"]:
        if node.get("kind") == "site":
            node["capacity"] = 31
    arch = Architecture.from_json(big)
    prog = TSIR(name="x", arch_spec="a")
    prog.add(Instruction(type="init", id=prog.next_id(), placement={"q0": "S0"}, quanta={"q0": 0.0}))
    assert "Y4" in _judge("cyclone-deck", prog, arch, {"circuit_ops": [], "map": {"0": "q0"}})
    with pytest.raises(ValueError):
        cyclone.device_doc(8, 31)
    with pytest.raises(ValueError):
        cyclone.device_doc(7, 4)


# ------------------------------------------------------------------ the prices


def _us(prog, arch, ins_pred):
    from qccd.verify.replay import replay
    res = replay(prog, arch, corrected_model(TABLE), check_rules=False, keep_cycles=True)
    by_id = {i.id: i for i in prog.instructions}
    return [c.t1 - c.t0 for c in res.cycles if ins_pred(by_id[c.instr_id])]


def test_wise_prices_are_the_deck_s(wise_prog):
    _exp, arch, prog, _cert, _work = wise_prog
    uniq = lambda pred: set(round(u, 6) for u in _us(prog, arch, pred))  # noqa: E731
    # T6: 385 us for a 1q layer, the noise model's 70 + 384 for a 2q layer; a shim layer has no pulse but the identity's
    assert uniq(lambda i: i.gate == "R") == {385.0}
    assert uniq(lambda i: i.gate == "MS") == {454.0}
    assert uniq(lambda i: i.gate == "VZ") == {0.0}
    assert uniq(lambda i: i.type == "measure") == {150.0 + 384}
    assert uniq(lambda i: i.type == "reset") == {20.0 + 384}
    assert uniq(lambda i: i.type == "cool") == {3000.0}
    # T1: t0 = 100 us a swap, 50 a half; across a junction zone 2 t0; between rows 3 t0 (S6)
    assert uniq(lambda i: str(i.cls).startswith(("wise_h0", "wise_h1"))) == {50.0}
    assert uniq(lambda i: str(i.cls).startswith("wise_x")) <= {100.0}
    assert uniq(lambda i: str(i.cls).startswith(("wise_vr", "wise_vl"))) <= {150.0}


def test_cyclone_prices_are_the_deck_s(cyclone_prog):
    _exp, arch, prog, _cert, _work = cyclone_prog
    from qccd.verify.replay import replay
    g = cyclone.chain_gate_us(3)
    assert g == pytest.approx(70.0)
    uniq = {round(u, 6) for u in _us(prog, arch, lambda i: i.type == "simd")}
    # s + t on the slowest hop, which lockstep waits for: split 80 + GateSwap 3g + merge 80 + move 10, and
    # round the ring's short side two degree-2 crossings of 10 each (T1, T2)
    assert uniq == {80 + 3 * g + 80 + 10 + 2 * 10}
    assert set(_us(prog, arch, lambda i: i.gate == "MS")) == {g}
    assert set(_us(prog, arch, lambda i: i.type == "cool")) == {0.0}
    res = replay(prog, arch, corrected_model(TABLE), check_rules=False, keep_cycles=False)
    assert res.max_gate_quanta_seen == 0.0     # S10: nothing heats


def test_c2lr_prices_are_the_deck_s():
    y, b = c2lr.C2LR_DECK["Yb-Ba"], c2lr.C2LR_DECK["Ba-Sr"]
    assert y["swap_or_stay"]["us"] == 400 and b["swap_or_stay"]["us"] == 312.5
    assert y["swap_or_stay"]["quanta_swap"] == pytest.approx(0.14) and y["swap_or_stay"]["quanta_stay"] == pytest.approx(0.035)
    assert b["swap_or_stay"]["quanta_swap"] == pytest.approx(0.135) and b["swap_or_stay"]["quanta_stay"] == pytest.approx(0.025)
    assert y["reorder_or_not"] == {**y["reorder_or_not"], "us": 93.0, "quanta": 0.007} and "reorder_or_not" not in b
    # the C2LR boards' own numbers are not these
    assert c2lr.C2LR_PHYSICS["Yb-Ba"]["swap_or_stay"]["quanta_stay"] == 0.165


def test_a_page_past_the_frame_cap_checks_the_frames_it_animates(wise_prog):
    """A page's self-check compares its own replay with the verifier's.  Past the cap it replays only its
    first frames, so the verifier's side must be those frames, not the whole programme's heat."""
    from qccd.verify.replay import replay
    from qccd.viz.render import build_view_model
    _exp, arch, prog, _cert, _work = wise_prog
    model = corrected_model(TABLE)
    res = replay(prog, arch, model)
    cap = 200
    capped = build_view_model(arch, prog, res, model, max_frames=cap)
    shown = TSIR(name=prog.name, arch_spec=prog.arch_spec)
    for ins in prog.instructions[:cap]:
        shown.add(ins)
    part = replay(shown, arch, model, check_rules=False, keep_cycles=False)
    comps = capped["checksum_components"]
    assert capped["program"]["truncated"]
    assert capped["checksum"] == {ion: round(sum(v.get(c, 0.0) for c in comps), 6) for ion, v in part.per_ion_quanta.items()}
    # the whole programme's heat is more, which is the false alarm this prevents
    whole = {ion: round(sum(v.get(c, 0.0) for c in comps), 6) for ion, v in res.per_ion_quanta.items()}
    assert max(whole.values()) > max(capped["checksum"].values())
    # a page under the cap is checked against the whole programme, as ever
    full = build_view_model(arch, prog, res, model)
    assert not full["program"]["truncated"] and full["checksum"] == whole


# ------------------------------------------------------------------ the noise model


def _stim(family_fixture) -> str:
    from qccd.qec.extract import extract
    _family, exp, arch, prog, cert, _work = family_fixture
    return str(extract(prog, arch, exp, noise="qccd-noise@3", cert=cert).circuit)


def test_the_physics_is_the_noise_model_s_numbers():
    from qccd.evalset import load
    p = PHYS["primitives"]
    assert (p["ms_gate"]["us"], p["measure"]["us"], p["reset"]["us"], p["cool"]["us"]) == (70, 150, 20, 3000)
    assert PHYS["heating"]["anomalous_rate_quanta_per_ms"] == 0.003
    assert p["z_gate"]["us"] == 0
    assert p["shuttle_segment"]["curve"][0]["quanta"] == 0.1 and p["split"]["curve"][0]["quanta"] == 2
    assert p["merge"]["curve"][0]["quanta"] == 0
    assert p["junction_cross"]["curve_by_degree"]["3"][0]["quanta"] == 0.014
    assert p["ms_gate"]["error_vs_quanta"] == "linear:9.5e-4"
    assert all(c["table"] == TABLE for k in ("split", "merge", "shuttle_segment") for c in p[k]["curve"])
    assert load("qccd-noise@3").clock == TABLE


def test_the_noise_text_writes_the_model_s_stim_column(compiled):
    text = _stim(compiled)
    nums = {float(x) for x in re.findall(r"(?:DEPOLARIZE[12]|X_ERROR|Z_ERROR)\(([0-9.e+-]+)\)", text)}
    close = lambda v: any(math.isclose(v, n, rel_tol=1e-6) for n in nums)  # noqa: E731
    assert close(5 / 4 * (5.5e-4 - 1.1e-5)), "2Q cooled, excluding cooling dephasing, ~6.74e-4"
    assert close(9e-5), "2Q leakage, folded"
    assert close(2.07e-5), "1Q computational"
    assert close(8.4e-6), "1Q leakage, folded"
    assert close(5e-5), "physical prep / reset"
    assert close(4.3e-4), "symmetric readout"


def test_only_wise_pays_for_the_residual_drive(compiled):
    from qccd.qec.extract import extract
    family, exp, arch, prog, cert, _work = compiled
    got = extract(prog, arch, exp, noise="qccd-noise@3", cert=cert).budget
    assert (got.get("residual_drive", 0.0) > 0) == (family == "WISE")
    if family == "WISE":
        # one layer of R on k of the 17 ions charges the other 17 - k at 1e-5, and the shim layers nothing
        pulses = [i for i in prog.instructions if i.gate == "R" and len(i.ions) > 1]
        assert got["residual_drive"] == pytest.approx(sum(17 - len(i.ions) for i in pulses) * 1e-5)


def test_the_idle_window_is_one_channel_over_the_whole_wait():
    from qccd.evalset import load
    ev = load("qccd-noise@3")
    chan = {c.name: c for c in ev.channels}
    assert "exp(0 - idle_s(i, o) / t_lin - pow(idle_s(i, o) / t_g, 2))" in chan["idle_dephasing"].text
    # the two-ion gate's excess reads the ledger, which counts from the last cooling
    assert "slope * gate_nbar(g)" in chan["ms"].text
