"""`qccd-noise@2` and the physics it is graded under (qccd/workspace/physics/qccd-physics@2.json).

What is established:

* every channel `@2` adds is a Pauli channel stim samples natively, and its documentation
  names one for each budget channel;
* the memory channels are the Helios numbers: r·Δt, and sin²(π·f·T) reproduces the paper's
  quadratic term at its 55 ms layer; the crosstalk split reproduces Helios's global average;
* `@2` refuses a device whose physics lacks its "quantinuum" table instead of silently pricing
  from another table, and every number of the `@2` physics carries a source;
* on a compiled surface code: the budget is exactly the circuit's noise, memory grows with time
  and only memory moves when its rate does, crosstalk lands only where it should, and `@1` is
  unchanged by any of it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.qec import noise as N  # noqa: E402

ARCH = ROOT / "arch"
PHYSICS2 = ROOT / "qccd" / "workspace" / "physics" / "qccd-physics@2.json"
PAULI = {"DEPOLARIZE1", "DEPOLARIZE2", "X_ERROR", "Z_ERROR"}
FIXED = ("primitives", "heating", "species", "budget")


def _with_physics2(name: str) -> dict:
    doc = json.loads((ARCH / f"{name}.arch.json").read_text(encoding="utf-8"))
    phys = json.loads(PHYSICS2.read_text(encoding="utf-8"))
    doc.update({k: phys[k] for k in FIXED})
    return doc


def test_the_channel_table_documents_every_budget_channel():
    m = N.get("qccd-noise@2")
    assert tuple(c["channel"] for c in m.channels()) == N.CHANNEL_NAMES_2 == m.channel_names()
    for c in m.channels():
        assert set(c) == {"channel", "where", "stim", "formula", "source"}
        assert all(isinstance(v, str) and v for v in c.values())
        assert any(p in c["stim"] for p in PAULI), c
    d = N.describe("qccd-noise@2")
    assert json.loads(json.dumps(d)) == d and d["id"] == "qccd-noise@2"


def test_memory_is_the_helios_rates():
    m = N.get("qccd-noise@2")
    assert m.memory_linear({}, 1e6) == pytest.approx(7.9e-3)          # one second
    assert m.memory_linear({}, 0.0) == 0.0 and m.memory_coherent({}, 0.0) == 0.0
    # the paper's quadratic memory parameter, 7(2)e-5 average infidelity per 55 ms layer, is
    # 2/3 of the Z probability of the twirled rotation
    avg = 2 / 3 * m.memory_coherent({}, 55e3)
    assert 5e-5 < avg < 9e-5
    # quadratic: twice the wait costs four times as much
    assert m.memory_coherent({}, 20e3) == pytest.approx(4 * m.memory_coherent({}, 10e3), rel=1e-3)


def test_crosstalk_reproduces_the_helios_average():
    m = N.get("qccd-noise@2")
    near, far = m.crosstalk_error({}, "measure"), m.crosstalk_global({}, "measure")
    assert near == 2.1e-4
    assert (3 * near + 94 * far) / 97 == pytest.approx(4.8e-5)
    assert m.crosstalk_error({}, "reset") == 0.0 and m.crosstalk_global({}, "reset") == 3.5e-6


def test_at_one_has_none_of_it():
    m = N.get("qccd-noise@1")
    assert (m.memory_linear({}, 1e6), m.memory_coherent({}, 1e6), m.crosstalk_error({}, "measure"),
            m.crosstalk_global({}, "measure"), m.p_initial({"pr": 0.1})) == (0, 0, 0, 0, 0)
    assert m.nbar_at_gate(0.3) == 0.3 and not m.per_ion_memory


def test_every_gate_starts_from_the_cooling_floor():
    arch = Architecture.from_json(_with_physics2("grid9x9"))
    m = N.get("qccd-noise@2")
    e = m.ms_error(corrected_model("quantinuum"), arch, 0.0, 2)
    assert e.heating == pytest.approx(9.5e-4 * N.COOLING_NBAR_FLOOR)
    assert e.base == pytest.approx(7.9e-4)


def test_physics2_cites_every_number_and_declares_its_table():
    phys = json.loads(PHYSICS2.read_text(encoding="utf-8"))
    assert phys["heating"]["source"] and phys["species"]["source"]
    for name, prim in phys["primitives"].items():
        if name == "gate_swap":
            continue                     # a logical identity, not a measurement
        points = prim.get("curve") or [p for ps in (prim.get("curve_by_degree") or {}).values() for p in ps]
        if points:
            for p in points:
                assert p["table"] == "quantinuum" and p["source"].startswith("arXiv:"), (name, p)
        else:
            assert prim["source"], name
    arch = Architecture.from_json(_with_physics2("grid9x9"))
    params = N.get("qccd-noise@2").resolve(arch)
    assert params["t2_s"] is None and params["ms"]["eps0"] == pytest.approx(7.9e-4)


def test_at_two_refuses_a_device_without_its_table():
    from qccd.arch import load

    with pytest.raises(ValueError, match="quantinuum"):
        N.get("qccd-noise@2").resolve(load(ARCH / "grid9x9.arch.json"))


# ------------------------------------------------------------------ on a compiled surface code

stim = pytest.importorskip("stim")
pytest.importorskip("pymatching")


@pytest.fixture(scope="module")
def surface3(tmp_path_factory):
    """(experiment, @2 device, cooled program) for a Z and an X memory on cyclone_base."""
    from qccd.compile.cooling import insert_cooling
    from qccd.ir.tsir import TSIR
    from qccd.qec import _toolchain, compile_with_qccdc, rotated_surface

    if _toolchain()[0] is None:
        pytest.skip("qccdc_cli is not built")
    out = {}
    doc = _with_physics2("cyclone_base")
    arch = Architecture.from_json(doc)
    for basis in ("Z", "X"):
        exp = rotated_surface(3, basis=basis)
        work = tmp_path_factory.mktemp(f"s3{basis}").resolve()
        prog, _ = compile_with_qccdc(exp.qasm, doc, work, cool=False, timeout=300)
        cooled = insert_cooling(TSIR.load(str(prog)), arch, corrected_model("quantinuum")).program
        out[basis] = (exp, arch, cooled)
    return out


def _noise_in(circuit) -> float:
    total = 0.0
    for inst in circuit.flattened():
        if inst.name in PAULI:
            total += inst.gate_args_copy()[0] * len(inst.targets_copy()) / (2 if inst.name == "DEPOLARIZE2" else 1)
    return total


def test_every_noise_line_is_pauli_and_the_budget_is_exactly_it(surface3):
    from qccd.qec import extract, noiseless_check

    exp, arch, prog = surface3["X"]
    ex = extract(prog, arch, exp, noise="qccd-noise@2")
    for inst in ex.circuit.flattened():
        gd = stim.gate_data(inst.name)
        if gd.produces_measurements:
            assert not inst.gate_args_copy(), inst      # readout error is its own X/Z_ERROR line
        elif gd.is_noisy_gate:
            assert inst.name in PAULI, inst
    assert set(ex.budget) == set(N.CHANNEL_NAMES_2)
    assert sum(ex.budget.values()) == pytest.approx(_noise_in(ex.circuit), rel=1e-9)
    assert ex.budget["memory"] > 0 and ex.budget["memory_coherent"] > 0 and ex.budget["crosstalk_global"] > 0
    assert noiseless_check(ex.circuit).ok


def test_only_memory_moves_with_its_rate(surface3):
    from qccd.qec import extract

    exp, arch, prog = surface3["X"]
    base = extract(prog, arch, exp, noise="qccd-noise@2").budget
    fast = extract(prog, arch, exp, noise=N.NoiseModel2(id="2r", memory_rate_per_s=2 * 7.9e-3)).budget
    assert fast["memory"] == pytest.approx(2 * base["memory"])
    assert {k: v for k, v in fast.items() if k != "memory"} == {k: v for k, v in base.items() if k != "memory"}


def test_memory_is_charged_for_the_whole_schedule(surface3):
    """Linear memory over all ions adds up to r × (ion-time not spent being operated on)."""
    from qccd.qec import extract

    exp, arch, prog = surface3["Z"]
    ex = extract(prog, arch, exp, noise="qccd-noise@2")
    n_ions, t_us = ex.stats["ions"], ex.stats["t_us"]
    assert 0 < ex.budget["memory"] <= 7.9e-3 * n_ions * t_us * 1e-6 * (1 + 1e-9)
    assert ex.budget["memory"] > 0.5 * 7.9e-3 * n_ions * t_us * 1e-6


def test_crosstalk_switches_only_their_own_channel(surface3):
    from qccd.qec import extract

    exp, arch, prog = surface3["Z"]
    base = extract(prog, arch, exp, noise="qccd-noise@2").budget
    quiet = extract(prog, arch, exp, noise=N.NoiseModel2(id="q", crosstalk_local=0.0, crosstalk_far=0.0,
                                                          crosstalk_reset=0.0)).budget
    assert quiet["crosstalk"] == 0.0 and quiet["crosstalk_global"] == 0.0
    assert {k: v for k, v in quiet.items() if not k.startswith("crosstalk")} == \
        {k: v for k, v in base.items() if not k.startswith("crosstalk")}


def test_x_memory_sees_dephasing_that_z_memory_cannot(surface3):
    """Z errors flip no Z logical: only the X-basis memory can rank a schedule's idle time."""
    from qccd.qec import evaluate_memory

    budget = {"max_shots": 100_000, "max_errors": 10**9}
    ler = {}
    for basis in ("Z", "X"):
        exp, arch, prog = surface3[basis]
        lo = evaluate_memory(prog, arch, exp, noise_id="qccd-noise@2", ler_budget=budget, seed=7)
        hi = evaluate_memory(prog, arch, exp, noise=N.NoiseModel2(id="r50", memory_rate_per_s=50 * 7.9e-3),
                             ler_budget=budget, seed=7)
        ler[basis] = (lo["ler"]["ler"], hi["ler"]["ler"])
    assert ler["X"][1] > 3 * ler["X"][0]
    assert ler["Z"][1] < 1.5 * ler["Z"][0] + 1e-4
