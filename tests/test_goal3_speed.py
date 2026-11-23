"""The shuttling speed (qccd/arch/speed.py): a rail's time and heat from metres per second.

The team asked how the shuttling speed is counted (Ke, 2026-11-23); it was not: a rail cost a fixed
time whatever its length.  These pin the law, the sources' numbers it rests on, and that a machine
priced without a speed is left exactly as it was.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "goals" / "goal3"
for p in (str(ROOT), str(GOAL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from qccd.arch import speed  # noqa: E402

PHYSICS = json.loads((ROOT / "qccd" / "workspace" / "physics" / "qccd-physics@3.json").read_text(encoding="utf-8"))


def test_a_rail_takes_its_length_over_the_speed():
    assert speed.rail_us(375.0, 4.0) == pytest.approx(93.75)          # 1 m/s is 1 um/us
    assert speed.pitch_um() == pytest.approx(464.0)                    # the default technology's trap pitch
    # what the fixed times mean on that pitch: the noise model's 5 us, a Cyclone move, half of WISE's t0
    assert round(speed.implied_m_s(5.0)) == 93
    assert round(speed.implied_m_s(10.0)) == 46
    assert round(speed.implied_m_s(50.0), 1) == 9.3
    with pytest.raises(ValueError):
        speed.rail_us(464.0, 0.0)


def test_the_heat_law_is_anchored_on_what_was_measured():
    # Sterk et al. (arXiv:2201.07358): 35 m/s, at most 0.36 +- 0.08 quanta
    assert speed.rail_quanta(35.0) == pytest.approx(0.36)
    # Burton et al. (arXiv:2206.11888): a Ba-Yb crystal at 4 m/s gains at most 0.030 quanta over a round trip
    # of four 375 um legs, of which the background (29 quanta/s over the trip's 375 us) is 0.011
    trip_us = 4 * speed.rail_us(375.0, 4.0)
    background = 29.0 * trip_us * 1e-6
    assert background == pytest.approx(0.0109, abs=2e-4)
    assert speed.rail_quanta(4.0) == pytest.approx(0.0047, abs=1e-4)
    assert 4 * speed.rail_quanta(4.0) + background <= 0.030 + 1e-9
    # the noise model's 0.1 quanta a shuttle is this law at about 18 m/s
    assert math.sqrt(0.1 / speed.KICK_QUANTA) * speed.KICK_M_S == pytest.approx(18.4, abs=0.1)
    # twice the speed, four times the heat and half the time
    assert speed.rail_quanta(8.0) == pytest.approx(4 * speed.rail_quanta(4.0))
    assert speed.rail_us(464.0, 8.0) == pytest.approx(speed.rail_us(464.0, 4.0) / 2)


def test_at_speed_rewrites_the_rail_and_nothing_else():
    base = PHYSICS["primitives"]
    before = json.dumps(base, sort_keys=True)
    out = speed.at_speed(base, 4.0)
    assert json.dumps(base, sort_keys=True) == before                  # the input is not touched
    point = out["shuttle_segment"]["curve"][0]
    assert point["us"] == pytest.approx(116.0) and point["quanta"] == pytest.approx(speed.rail_quanta(4.0))
    assert point["table"] == base["shuttle_segment"]["curve"][0]["table"]
    assert "4 m/s" in point["label"] and "2201.07358" in point["source"]
    for key in base:
        if key != "shuttle_segment":
            assert out[key] == base[key], key                          # junctions, splits, merges, gates: their own


def test_a_wise_swap_between_neighbours_is_two_rails():
    from qccd.arch import wise
    prims = wise.primitives(PHYSICS["primitives"])
    out = speed.at_speed(prims, 9.28)
    assert out["wise_swap"]["us_by_kind"]["h"] == pytest.approx(2 * out["shuttle_segment"]["curve"][0]["us"])
    assert out["wise_swap"]["us_by_kind"]["h"] == pytest.approx(wise.SWAP_US, rel=1e-3)      # the deck's t0 is 9.28 m/s
    # a junction step keeps C2LR's time
    assert out["wise_swap"]["us_by_kind"]["x"] == prims["wise_swap"]["us_by_kind"]["x"]


def test_the_sweep_s_machine_is_unchanged_without_a_speed_and_slower_with_a_slow_one():
    import boards
    import sweep
    exp = boards.experiment("surface3")
    point = {"family": "Cyclone", "name": "cyclone8_n3", "generator": "cyclone", "params": {"x": 8, "n": 3}}
    plain = sweep.device_doc(point, exp)
    assert plain["primitives"]["shuttle_segment"]["curve"][0]["us"] == 10.0             # the deck's move
    slow = sweep.device_doc({**point, "speed_m_s": 1.0}, exp)
    assert slow["primitives"]["shuttle_segment"]["curve"][0]["us"] == pytest.approx(464.0)
    assert {k: v for k, v in slow.items() if k != "primitives"} == {k: v for k, v in plain.items() if k != "primitives"}
