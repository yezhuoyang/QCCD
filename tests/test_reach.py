"""A number that answers without a programme.

Every metric this platform produced before this module needed a compiled programme --
`t1_metrics`, `t2_metrics` and all twenty-three rules take a `ReplayResult`. So dragging a
site in the studio moved nothing unless a benchmark happened to be loaded, and what it did
move was a property of that benchmark rather than of the machine. `reach_report` answers
from the device alone, which is what qiskit-metal's `LOManalysis` does for a transmon.

The headline number is STRANDED: traps that cannot reach a cooling zone and return inside
R7's `ms_gate.max_quanta` budget. An ion parked there is past the gate limit before it can
get back, whatever programme you write -- so the position is structurally unusable, and
that is a fact about where the coolers are, not about anyone's schedule.

The round trip is the part worth getting right. Charging one leg would call a trap usable
that is only usable if the ion never comes back: on the rail below, the worst one-way
distance at every-16th cooling is 0.80 quanta against a budget of 1.0, which passes; the
round trip is 1.60, which does not, and ten traps are correctly condemned.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.analysis import distance_matrix, nearest, reach_report  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.arch.edit import apply_call  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402

ARCH = ROOT / "arch"


def rail(n: int, cooler_every: int):
    """A straight rail of `n` traps where every `cooler_every`-th one can cool."""
    prog = [{"method": "DeviceBuilder", "args": ["explicit"], "kwargs": {}}]
    for i in range(n):
        z = "trap" if (cooler_every and i % cooler_every == 0) else "data"
        prog.append({"method": "d.site", "args": [f"C{i}", float(i), 0.0],
                     "kwargs": {"zone": z, "capacity": 2}})
    for i in range(n - 1):
        prog.append({"method": "d.segment", "args": [f"e{i}", f"C{i}", f"C{i+1}"],
                     "kwargs": {}})
    prog += [
        {"method": "blank_device", "args": [], "kwargs": {"name": f"rail{n}", "zones": {
            "data": {"capacity": 2, "gate": True, "spam": True, "cool": False},
            "trap": {"capacity": 2, "gate": True, "spam": True, "cool": True}}}},
        {"method": "set_control", "args": [], "kwargs": {"model": "simd_classes"}},
        {"method": "set_curve", "args": ["shuttle_segment", [
            {"us": 5.0, "quanta": 0.1, "table": "qccdsim_jones",
             "source": "2510.23519"}]], "kwargs": {}},
        {"method": "set_primitive", "args": ["ms_gate"], "kwargs": {
            "us": 25, "fidelity_at_n0": 0.99816, "max_quanta": 1.0,
            "source": "2305.03828"}},
    ]
    state = None
    for c in prog:
        state = apply_call(state, c)
    machine, _ = state
    return machine.arch


def test_sparser_cooling_strands_more_traps():
    """The number that responds to moving a cooler."""
    got = {}
    for every in (1, 4, 8, 16):
        r = reach_report(rail(33, every), corrected_model())
        got[every] = len(r.stranded)
    assert got[1] == 0 and got[4] == 0 and got[8] == 0, got
    assert got[16] > 0, "cooling every sixteenth trap should strand the ones in between"
    # monotone: taking coolers away never rescues a trap
    seq = [got[e] for e in (1, 4, 8, 16)]
    assert seq == sorted(seq), seq


def test_no_cooling_at_all_strands_everything():
    r = reach_report(rail(20, 0), corrected_model())
    assert len(r.stranded) == 20
    assert all(not math.isfinite(v["cool"][0]) for v in r.nearest.values())


def test_the_budget_is_charged_as_a_round_trip():
    """One leg would call a trap usable that is only usable if the ion never returns."""
    arch = rail(33, 16)
    r = reach_report(arch, corrected_model())
    worst = max(v["cool"][0] for v in r.nearest.values())
    assert worst < r.budget, "the one-way distance alone is inside the budget"
    assert 2 * worst > r.budget, "the round trip is not"
    assert r.stranded, "so traps at that distance must be reported as stranded"


def test_distance_is_in_the_cost_models_own_units_not_hops():
    """A graph library gives hops; nobody's error budget is written in hops."""
    arch = rail(9, 1)
    q = distance_matrix(arch, corrected_model(), metric="quanta")["C0"]["C8"]
    us = distance_matrix(arch, corrected_model(), metric="us")["C0"]["C8"]
    assert q == pytest.approx(0.8), q          # 8 hops x 0.1 quanta
    assert us == pytest.approx(40.0), us       # 8 hops x 5 us
    assert q != us, "the two metrics must not be the same number"


def test_the_deck_model_gives_a_different_answer_than_the_corrected_one():
    """The report is a property of the device AND the model it is priced with, and says
    which model it used."""
    arch = load(ARCH / "ring144_24v.arch.json")
    a = reach_report(arch, corrected_model(), metric="cost")
    b = reach_report(arch, deck_model(), metric="cost")
    assert a.model != b.model
    assert a.diameter != b.diameter, (a.diameter, b.diameter)


@pytest.mark.parametrize("stem", [
    "ring144_24v", "cyclone_base", "grid9x9", "h2_racetrack", "ladder_2x72",
])
def test_every_shipped_device_is_connected_for_transport(stem):
    """A device whose gate-capable traps cannot reach each other is not a machine."""
    r = reach_report(load(ARCH / f"{stem}.arch.json"), corrected_model())
    assert r.unreachable_pairs == 0, r.notes
    assert r.diameter > 0
    assert r.stranded == (), r.notes


def test_nearest_reports_which_node_answered():
    """Not just how far -- an architect needs to know which cooler is the near one."""
    arch = rail(17, 8)
    near = nearest(arch, corrected_model(), "cool")
    d, who = near["C4"]
    assert who in ("C0", "C8"), who
    assert d == pytest.approx(0.4), d
