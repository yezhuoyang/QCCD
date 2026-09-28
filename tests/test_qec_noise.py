"""`qccd-noise@1`: the device's physics, read the way the replay reads it.

The MS error is split into base / heating / chain for the error budget by evaluating the
cost model's own `gate_error` at three settings, so the parts always sum to the number R16
prices.  The first test pins the base to the architecture's own `fidelity_at_n0`, written
out rather than imported.  Tests that need a compiled program are in test_qec_extract.py.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.qec.noise import (CHANNEL_NAMES, CHANNELS, MODELS, NoiseModel,  # noqa: E402
                            describe, get)

ARCH = ROOT / "arch"


@pytest.fixture(scope="module")
def grid():
    return load(ARCH / "grid9x9.arch.json")


def _doc(name: str) -> dict:
    return json.loads((ARCH / f"{name}.arch.json").read_text())


@pytest.mark.parametrize("name", ["grid9x9", "cyclone_base", "ring144_24v"])
def test_base_ms_error_is_one_minus_fidelity_at_n0(name):
    arch = load(ARCH / f"{name}.arch.json")
    e = NoiseModel().ms_error(corrected_model(), arch, 0.0, 2)
    f0 = float(_doc(name)["primitives"]["ms_gate"]["fidelity_at_n0"])
    assert e.base == pytest.approx(1.0 - f0, abs=1e-15)
    assert e.heating == 0.0 and e.chain == 0.0 and e.total == pytest.approx(1.0 - f0)


def test_heating_and_chain_parts_sum_to_the_models_error(grid):
    model = corrected_model()
    e = NoiseModel().ms_error(model, grid, 3.0, 6)
    slope = float(_doc("grid9x9")["primitives"]["ms_gate"]["error_vs_quanta"].split(":")[1])
    assert e.heating == pytest.approx(slope * 3.0)
    assert e.chain > 0.0
    assert e.base + e.heating + e.chain == pytest.approx(model.gate_error(grid, 3.0, 6))
    assert e.total == pytest.approx(model.gate_error(grid, 3.0, 6))


def test_switches_turn_off_their_own_part_only(grid):
    model = corrected_model()
    no_heat = NoiseModel(heating=False).ms_error(model, grid, 3.0, 6)
    no_chain = NoiseModel(chain=False).ms_error(model, grid, 3.0, 6)
    full = NoiseModel().ms_error(model, grid, 3.0, 6)
    assert no_heat.heating == 0.0 and no_heat.base == full.base
    assert no_chain.chain == 0.0 and no_chain.heating == pytest.approx(full.heating)


def test_a_capped_total_shrinks_its_parts_in_proportion(grid):
    e = NoiseModel(scale=1e4).ms_error(corrected_model(), grid, 50.0, 8)
    assert e.total == pytest.approx(15 / 16)
    assert e.base + e.heating + e.chain == pytest.approx(e.total)


def test_idle_follows_t2_and_the_override(grid):
    params = NoiseModel().resolve(grid)
    t2 = float(_doc("grid9x9")["species"]["T_coh_s"])
    assert params["t2_s"] == t2 and params["t2_source"] == "species.T_coh_s"
    pz = NoiseModel().p_idle(params, 100.0)
    assert pz == pytest.approx(0.5 * (1 - math.exp(-100e-6 / t2)))
    fast = NoiseModel(t2_s=1.0)
    assert fast.p_idle(fast.resolve(grid), 100.0) == pytest.approx(0.5 * (1 - math.exp(-1e-4)))
    assert NoiseModel(idle=False).p_idle(params, 100.0) == 0.0


def test_resolve_reads_the_spam_primitives(grid):
    doc = _doc("grid9x9")["primitives"]
    p = NoiseModel().resolve(grid)
    assert p["p1"] == pytest.approx(1 - doc["1q_gate"]["fidelity"])
    assert p["pm"] == pytest.approx(1 - doc["measure"]["fidelity"])
    assert p["pr"] == pytest.approx(doc["reset"]["error"])
    json.dumps(p)


def test_the_channel_table_documents_every_budget_channel():
    assert tuple(c["channel"] for c in CHANNELS) == CHANNEL_NAMES
    for c in CHANNELS:
        assert set(c) == {"channel", "where", "stim", "formula", "source"}
        assert all(isinstance(v, str) and v for v in c.values())
    d = describe("qccd-noise@1")
    assert json.loads(json.dumps(d)) == d
    assert d["id"] == "qccd-noise@1" and get("qccd-noise@1") is MODELS["qccd-noise@1"]
    with pytest.raises(KeyError):
        get("qccd-noise@0")
