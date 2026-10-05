"""Compiled programs as noisy circuits: qccdc output in, a checked LER out.

Every test here compiles with the real toolchain (qccdc + insert_cooling) and skips
cleanly without it.  What is established:

* rep3 and surface3 compile, extract and pass the noiseless check on grid9x9 and
  cyclone_base (the router) and on ring(8, 2, 8) (rigid rotation, every rule passing),
  with the appended data readout the registry uses;
* the LER separates a cooled program from an uncooled one by an order of magnitude;
* a flipped MS sign, a dropped MS and a dropped pulse are reported as a failed check,
  never raised -- and a dropped measurement is a failed extraction, also reported;
* the error budget is exactly the noise in the circuit, channel by channel;
* measurement pinning needs no certificate, and gadget-style abstract gates extract too.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

stim = pytest.importorskip("stim")
pytest.importorskip("pymatching")
pytest.importorskip("ldpc")

from qccd.qec import (CompileFailed, NoiseModel, compile_with_qccdc,  # noqa: E402
                      evaluate_memory, extract, get_experiment, load_arch, noiseless_check,
                      rotated_surface)
from qccd.qec.decode import estimate_ler  # noqa: E402

ARCH = ROOT / "arch"
NOISE_GATES = {"DEPOLARIZE1": 1, "DEPOLARIZE2": 2, "X_ERROR": 1, "Z_ERROR": 1}
SMALL = {"max_shots": 20_000, "max_errors": 10**9}


def _arch(name: str):
    if name == "ring8_2_8":
        from qccd.api import Machine

        return Machine.ring(8, 2, 8).arch
    return ARCH / f"{name}.arch.json"


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    """(key, arch, mode, cool, readout) -> (experiment, arch, program doc, certificate)."""
    from qccd.qec import _toolchain

    if _toolchain()[0] is None:
        pytest.skip("qccdc_cli is not built")
    cache: dict = {}

    def get(key: str, arch: str, mode: str = "compile", cool: bool = True,
            readout: str = "appended"):
        k = (key, arch, mode, cool, readout)
        if k not in cache:
            exp = (rotated_surface(3, readout=readout) if (key, readout) == ("surface3", "program")
                   else get_experiment(key))
            work = tmp_path_factory.mktemp(f"{key}_{arch}_{mode}_{int(cool)}")
            a = _arch(arch)
            prog, cert = compile_with_qccdc(exp.qasm, a, work, cool=cool, mode=mode,
                                            timeout=300)
            cache[k] = (exp, load_arch(a), json.loads(prog.read_text()),
                        json.loads(cert.read_text()))
        return cache[k]

    return get


CASES = [("rep3", "grid9x9", "compile"), ("rep3", "cyclone_base", "compile"),
         ("surface3", "grid9x9", "compile"), ("surface3", "cyclone_base", "compile"),
         ("rep3", "ring8_2_8", "rotate"), ("surface3", "ring8_2_8", "rotate")]


@pytest.mark.parametrize("key,arch,mode", CASES)
def test_compiles_extracts_and_checks(compiled, key, arch, mode):
    exp, a, prog, cert = compiled(key, arch, mode)
    rep = evaluate_memory(prog, a, exp, cert=cert, seed=3, ler_budget=SMALL)
    assert json.loads(json.dumps(rep)) == rep
    assert rep["kind"] == "qccd.ler_report" and rep["check"]["ok"], rep["check"]
    assert rep["ler"] is not None and 0.0 <= rep["ler"]["ler"] < 0.05
    assert rep["ler"]["decoder"] == "pymatching"
    st = rep["stats"]
    assert st["cert_crosscheck"] == "agrees"
    assert st["n_readout_appended"] == exp.n_data
    if mode == "rotate":
        assert st["rules_failed"] == [], st["rule_violations"]
    assert set(rep["tools"]) == {"stim", "pymatching", "ldpc"}


def test_uncooled_grid_is_an_order_of_magnitude_worse(compiled):
    reports = {}
    for cool in (True, False):
        exp, a, prog, cert = compiled("surface3", "grid9x9", "compile", cool)
        reports[cool] = evaluate_memory(prog, a, exp, seed=5, ler_budget=SMALL)
    hot, cold = reports[False], reports[True]
    assert hot["stats"]["nbar_max"] > 10 * max(cold["stats"]["nbar_max"], 1.0)
    assert hot["budget"]["ms_heating"] > 10 * cold["budget"]["ms_heating"]
    assert hot["ler"]["ler"] > 10 * cold["ler"]["ler"] > 0
    assert "R7" in hot["stats"]["rules_failed"] and "R7" not in cold["stats"]["rules_failed"]


def _spread(xs: list, k: int = 3) -> list:
    return sorted({xs[0], xs[len(xs) // 2], xs[-1]})[:k] if xs else []


@pytest.mark.parametrize("key,arch,mode", [("surface3", "ring8_2_8", "rotate"),
                                           ("surface3", "cyclone_base", "compile"),
                                           ("rep3", "grid9x9", "compile")])
def test_wrong_programs_are_reported_not_raised(compiled, key, arch, mode):
    exp, a, prog, cert = compiled(key, arch, mode)
    ins = prog["instructions"]
    ms = [i for i, x in enumerate(ins) if x.get("gate") == "MS"]
    anc = {f"q{q}" for q in range(exp.n_data, exp.n_data + exp.n_ancilla)}
    # a pulse on a data qubit can be invisible to a memory experiment (an Rx(-pi/2) on a
    # CX's control is a Z rotation of a qubit sitting in a Z eigenstate); an ancilla's
    # pulses always reach a detector
    r_anc = [i for i, x in enumerate(ins) if x.get("gate") == "R" and set(x["ions"]) <= anc]
    mutants = []
    for i in _spread(ms):
        m = copy.deepcopy(prog)
        m["instructions"][i]["params"][0][0] *= -1
        mutants.append(("MS sign flipped", m))
        m = copy.deepcopy(prog)
        del m["instructions"][i]
        mutants.append(("MS dropped", m))
    for i in _spread(r_anc):
        m = copy.deepcopy(prog)
        del m["instructions"][i]
        mutants.append(("R dropped", m))
    assert len(mutants) >= 7
    for what, m in mutants:
        rep = evaluate_memory(m, a, exp, seed=1, ler_budget=SMALL)
        assert rep["check"]["ok"] is False, what
        assert rep["check"]["stage"] == "noiseless" and rep["ler"] is None, what

    meas = next(i for i, x in enumerate(ins) if x["type"] == "measure")
    m = copy.deepcopy(prog)
    del m["instructions"][meas]
    rep = evaluate_memory(m, a, exp)
    assert rep["check"]["ok"] is False and rep["check"]["stage"] == "extract"
    assert "fewer times" in rep["check"]["reason"]


def test_budget_is_exactly_the_circuits_noise(compiled):
    exp, a, prog, _ = compiled("surface3", "cyclone_base", "compile")
    ex = extract(prog, a, exp)
    total = 0.0
    for inst in ex.circuit.flattened():
        if inst.name in NOISE_GATES:
            total += inst.gate_args_copy()[0] * len(inst.targets_copy()) / NOISE_GATES[inst.name]
    assert sum(ex.budget.values()) == pytest.approx(total, rel=1e-9)
    assert ex.budget["ms_base"] > 0 and ex.budget["measure"] > 0 and ex.budget["idle"] > 0
    assert ex.stats["n_ms"] > 0 and ex.stats["t_us"] > 0


def test_t2_override_changes_only_the_idle_channel(compiled):
    exp, a, prog, _ = compiled("surface3", "cyclone_base", "compile")
    base = extract(prog, a, exp).budget
    fast = extract(prog, a, exp, noise=NoiseModel(id="t2=1s", t2_s=1.0)).budget
    assert fast["idle"] > 100 * base["idle"]
    assert {k: v for k, v in fast.items() if k != "idle"} == \
        {k: v for k, v in base.items() if k != "idle"}


def test_scale_zero_is_noiseless(compiled):
    exp, a, prog, _ = compiled("surface3", "cyclone_base", "compile")
    rep = evaluate_memory(prog, a, exp, noise=NoiseModel(id="off", scale=0.0))
    assert rep["check"]["ok"] and rep["ler"]["ler"] == 0.0 and rep["budget"]["total"] == 0.0


def test_pinning_needs_no_certificate(compiled):
    exp, a, prog, cert = compiled("surface3", "grid9x9", "compile")
    with_cert = extract(prog, a, exp, cert=cert)
    without = extract(prog, a, exp)
    assert str(with_cert.circuit) == str(without.circuit)
    assert without.stats["qubit_map"] == "identity"
    via_meta = dict(prog, meta={**prog.get("meta", {}), "qubit_map": cert["map"]})
    assert extract(via_meta, a, exp).stats["qubit_map"] == "meta.qubit_map"


def test_program_readout_still_extracts(compiled):
    exp, a, prog, cert = compiled("surface3", "cyclone_base", "compile", readout="program")
    assert exp.readout == "program"
    ex = extract(prog, a, exp, cert=cert)
    assert ex.stats["n_readout_appended"] == 0 and noiseless_check(ex.circuit).ok


def test_abstract_gates_extract(compiled):
    """The gadget layer's spelling: CX on (control, target), no native pulses.

    Built from a compiled rep3 program by replacing each MS with the CX it realises (read
    off the certificate) and dropping every R and VZ; the transport is untouched, so it
    replays, and it must read as the same circuit."""
    exp, a, prog, cert = compiled("rep3", "grid9x9", "compile")
    ops = {o["i"]: o for o in cert["circuit_ops"]}
    out = []
    for x in prog["instructions"]:
        if x.get("gate") in ("R", "VZ"):
            continue
        if x.get("gate") == "MS":
            x = dict(x, gate="CX", params=[],
                     pairs=[[cert["map"][str(q)] for q in ops[o]["qubits"]]
                            for o in x["meta"]["op"]])
            assert all(ops[o]["name"] == "cx" for o in x["meta"]["op"])
        out.append(x)
    ex = extract(dict(prog, instructions=out), a, exp)
    assert ex.stats["n_ms"] == 2 * (exp.distance - 1) * exp.rounds and ex.stats["n_1q"] == 0
    assert noiseless_check(ex.circuit).ok
    assert ex.budget["ms_base"] > 0 and ex.budget["gate_1q"] == 0


def test_decoders_agree_on_a_compiled_surface_code(compiled):
    exp, a, prog, _ = compiled("surface3", "cyclone_base", "compile")
    c = extract(prog, a, exp).circuit
    mw = estimate_ler(c, decoder="pymatching", seed=9, rounds=3, **SMALL)
    bp = estimate_ler(c, decoder="bposd", seed=9, rounds=3, **SMALL)
    assert mw.shots == bp.shots and mw.errors > 5 and bp.errors > 5
    assert 1 / 3 < bp.ler / mw.ler < 3


def test_a_non_matchable_code_is_decoded_by_bposd(compiled):
    try:
        exp, a, prog, cert = compiled("steane", "cyclone_base", "compile")
    except CompileFailed as exc:          # the router is the only mode that takes flags
        pytest.skip(f"steane did not compile: {exc}")
    rep = evaluate_memory(prog, a, exp, cert=cert, seed=2,
                          ler_budget={"max_shots": 5000, "max_errors": 10**9})
    assert rep["check"]["ok"] and rep["ler"]["decoder"] == "bposd"
    assert rep["experiment"]["matchable"] is False


def test_the_trace_says_which_stim_lines_each_instruction_became(compiled):
    """A page shows a reader how each hardware instruction became noise (the memory boards'
    Noise panel), so the trace must BE the circuit: its blocks tile the stim text in order
    with nothing left over, that text is the circuit that is sampled, every program
    instruction has exactly one block, and a block that carries noise says what priced it."""
    exp, arch, doc, cert = compiled("surface3", "ring8_2_8", "auto")
    ex = extract(doc, arch, exp, cert=cert)
    assert ex.trace[0]["type"] == "prepare" and ex.trace[-1]["type"] == "detectors"
    at = 0
    for t in ex.trace:
        assert t["a"] == at and t["b"] >= t["a"]
        at = t["b"]
    assert at == len(ex.lines)
    assert stim.Circuit("\n".join(ex.lines)) == ex.circuit
    ids = [t["id"] for t in ex.trace if t["id"] is not None]
    assert ids == [i["id"] for i in doc["instructions"]]
    for t in ex.trace:
        noisy = [ln for ln in ex.lines[t["a"]:t["b"]] if ln.split("(")[0] in NOISE_GATES]
        assert bool(noisy) <= bool(t["why"]), t
    ms = [w for t in ex.trace for w in t["why"] if w.startswith("MS ")]
    assert len(ms) == ex.stats["n_ms"] and all("n-bar" in w and "chain of" in w for w in ms)
