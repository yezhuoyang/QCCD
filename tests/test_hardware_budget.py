"""#15: the hardware budget is part of the task, and a device cannot buy its way under it by
declaring cheap wiring.  `hardware_report(..., floors=WIRING_FLOORS)` is what the grader
counts with; each test below is a device built to cheat one number, and the honest devices
must count exactly as they always have."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.arch import Architecture
from qccd.cost.hardware import WIRING_FLOORS, hardware_report


def _with_control(m: Machine, control: dict) -> Architecture:
    doc = copy.deepcopy(m.arch.to_json())
    doc["control"] = {**doc.get("control", {}), **control}
    if "channels" not in control:
        doc["control"].pop("channels", None)
    return Architecture.from_json(doc)


def _counted(arch: Architecture, **kw):
    return hardware_report(arch, floors=WIRING_FLOORS, **kw)


@pytest.mark.parametrize("m", [Machine.chain(4), Machine.ring(72, 2, 24), Machine.grid(9, 9),
                               Machine.ladder(3)], ids=lambda m: m.name)
def test_an_honest_device_counts_exactly_as_before(m):
    a, b = hardware_report(m.arch), _counted(m.arch)
    assert (a.dacs, a.dacs_broadcast, a.dacs_compensation) == (b.dacs, b.dacs_broadcast, b.dacs_compensation)
    assert not [n for n in b.notes if "counted at" in n or "count as" in n]


LIBRARY = sorted((Path(__file__).resolve().parents[1] / "arch").glob("*.arch.json"))


@pytest.mark.parametrize("path", LIBRARY, ids=lambda p: p.name.split(".")[0])
def test_no_shipped_library_device_is_floored(path):
    # including h2_racetrack, which honestly trades shims for no demultiplexer
    arch = Architecture.from_json(json.loads(path.read_text(encoding="utf-8")))
    assert hardware_report(arch).dacs == _counted(arch).dacs


def test_direct_wiring_is_counted_at_24_electrodes_a_trap_and_48_a_junction():
    m = Machine.grid(2, 3)
    cheap = _with_control(m, {"wiring": {"scheme": "direct", "electrodes_per_trap": 1,
                                         "electrodes_per_junction": 0}})
    hw = _counted(cheap)
    assert hardware_report(cheap).dacs == hw.n_traps * 1
    assert hw.dacs == hw.n_traps * 24 + hw.n_junctions * 48
    assert any("electrodes per trap: the device declares 1" in n for n in hw.notes)
    # declaring MORE is counted at the device's own number
    rich = _with_control(m, {"wiring": {"scheme": "direct", "electrodes_per_trap": 30}})
    assert _counted(rich).dacs == hw.n_traps * 30 + hw.n_junctions * 60


def test_broadcast_without_a_channel_map_needs_32_waveforms_and_the_1_to_100_demux():
    m = Machine.ring(72, 2, 24)
    cheap = _with_control(m, {"wiring": {"scheme": "wise", "dacs_dynamic": 1, "shim_per_dac": 10 ** 6,
                                         "compensation_electrodes_per_trap": 0}})
    assert hardware_report(cheap).dacs == 1 + 0
    hw = _counted(cheap)
    assert hw.dacs_broadcast == 32
    assert hw.dacs_compensation == -(-hw.n_traps * 8 // 100)
    assert hw.dacs == 32 + -(-hw.n_traps * 8 // 100)


def test_a_channel_map_of_one_waveform_counts_as_a_cell_s_worth():
    m = Machine.ring(72, 2, 24)
    one = _with_control(m, {"wiring": {"scheme": "wise", "shim_per_dac": 100,
                                       "compensation_electrodes_per_trap": 8},
                            "channels": {"grouping": "broadcast", "roles": ["linear"], "differential": 1}})
    raw = hardware_report(one)
    assert raw.dacs_broadcast == 1
    hw = _counted(one)
    assert hw.dacs_broadcast == 32 and hw.dacs == 32 + hw.dacs_compensation
    assert any("count as 32" in n for n in hw.notes)
    # one channel per site under direct grouping is 32 per site
    per_site = _with_control(m, {"wiring": {"scheme": "direct", "shim_per_dac": 1},
                                 "channels": {"grouping": "direct", "roles": ["linear"], "differential": 1}})
    n_sites = len([n for n in per_site.device.nodes.values() if n.kind == "site"])
    assert hardware_report(per_site).dacs_broadcast == n_sites
    assert _counted(per_site).dacs_broadcast == 32 * n_sites


def test_the_budget_judged_is_the_one_passed_in():
    m = Machine.grid(9, 9)
    assert not _counted(m.arch).over_budget
    hw = _counted(m.arch, budget={"max_dacs": 10, "max_junctions": 1})
    assert any(o.startswith("dacs ") for o in hw.over_budget)
    assert any(o.startswith("junctions ") for o in hw.over_budget)
    direct = _with_control(m, {"wiring": {"scheme": "direct"}})
    assert _counted(direct, budget={"max_dacs": 128}).over_budget
