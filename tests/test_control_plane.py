"""The control plane: control resource and control effect, without voltages.

PLAN §2 puts waveform synthesis out of scope, and that boundary holds. What sits just
above it -- which electrodes share a channel, and therefore which zones must do the same
thing -- is general to every QCCD and is what every architectural claim in the corpus
rests on. So it is modelled structurally: the DAC count is *counted* from the channel map
rather than computed from a formula, and R4 becomes a consequence of the wiring instead
of a label the program asserts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.arch.control import build_control_plane  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.cost.hardware import deck_unit_cell_report, hardware_report  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402

ARCH = ROOT / "arch"


def arch(n):
    return load(ARCH / f"{n}.arch.json")


# ------------------------------------------------------------ counted, not assumed


def test_the_dac_count_is_counted_from_the_channel_map():
    a = arch("deck_unit_cell")
    plane = a.control_plane
    assert plane.declared
    assert plane.n_shared_channels == len(plane.groups)
    assert hardware_report(a).dacs == plane.n_channels


def test_the_decks_shared_channel_count_reproduces():
    """Deck p.20: 12 linear x 2 = 24 DACs, 4 junction x 2 = 8, both constant in N."""
    plane = arch("deck_unit_cell").control_plane
    assert plane.n_shared_channels == (6 + 6) * 2 + 4 * 2 == 32
    per_role = {}
    for g in plane.groups:
        per_role[g.role] = per_role.get(g.role, 0) + 1
    assert per_role == {"linear_h": 12, "linear_v": 12, "junction": 8}
    assert deck_unit_cell_report(9, 9)["dacs_linear"] == 24
    assert deck_unit_cell_report(9, 9)["dacs_junction"] == 8


def test_broadcast_channels_do_not_grow_with_the_array():
    small = build_control_plane(Machine.ring(8, 2, 0).device,
                                arch("ring144_24v").control)
    big = build_control_plane(Machine.ring(400, 2, 0).device,
                              arch("ring144_24v").control)
    assert big.n_sites > 25 * small.n_sites
    assert big.n_shared_channels == small.n_shared_channels == 32


def test_direct_wiring_pays_one_channel_per_site():
    a = arch("grid9x9")
    plane = a.control_plane
    assert plane.grouping == "direct"
    assert plane.n_shared_channels == plane.n_sites * 32
    # identical geometry, opposite wiring: the whole WISE claim, both sides counted
    b = arch("deck_unit_cell")
    assert a.device.summary()["n_sites"] == b.device.summary()["n_sites"]
    assert hardware_report(a).dacs > 100 * hardware_report(b).dacs


def test_a_channel_names_the_sites_it_forces_to_agree():
    plane = arch("ring144_24v").control_plane
    mates = plane.sites_sharing_with("S0")
    assert "S6" in mates and "S71" in mates
    assert len(plane.channels_of("S0")) == 32


# ---------------------------------------------------------------- drivability


def test_one_channel_cannot_drive_two_different_movements():
    """One channel carries one waveform, so its zones cannot do different things."""
    plane = arch("ring144_24v").control_plane
    ok, why = plane.drivable({"S0": ("L0", +1), "S6": ("L0", +1)})
    assert ok, why
    ok, why = plane.drivable({"S0": ("L0", +1), "S6": ("L0", -1)})
    assert not ok
    assert "different things" in why[0]


def test_a_switch_is_what_makes_participation_variadic():
    """R4 says participation is variadic. That is not free -- it is the per-site switch
    (deck p.19's 48N two-way switches; arXiv:2403.00756's one digital input per site)."""
    a = arch("ring144_24v")
    with_switch = a.control_plane
    ok, _ = with_switch.drivable({"S0": ("L0", +1)})     # one site moves, 167 idle
    assert ok, "a switch lets a zone opt out"

    doc = a.to_json(expanded=True)
    doc["control"]["channels"]["switch_per_site"] = False
    without = load.__wrapped__ if False else None       # noqa: F841
    from qccd.arch import Architecture

    b = Architecture.from_json(doc)
    ok, why = b.control_plane.drivable({"S0": ("L0", +1)})
    assert not ok
    assert "all-or-nothing" in why[0]


def test_a_rigid_rotation_is_drivable_because_it_is_one_waveform():
    """Every ion advances one slot along the loop -- the same action in path terms even
    where the loop bends, which is exactly what a conveyor broadcast can produce."""
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    r = replay(m.program("r").fill().rotate(+1).build(), m.arch,
               corrected_model(), check_rules=True)
    assert r.rules.ok(), r.rules.summary()
    assert "R4d" not in r.rules.summary()["failed"]


def test_asking_one_broadcast_channel_for_two_directions_is_caught():
    """The check that makes R4 a consequence of the wiring rather than a declaration."""
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    p = m.program("both_ways").init({"a": "S10", "b": "S20"})
    with p.cycle("shuttle") as c:
        c.move("a", "S10", "S11")     # forward along the loop
        c.move("b", "S20", "S19")     # backward, on the same broadcast channel
    out = m.run(p, check_metrics=False)
    assert "R4" in out.rules_failed
    assert any("one waveform" in str(v) for v in out.violations)


def test_a_device_that_declares_no_channels_is_not_judged():
    """Silence is not a pass: an architecture that has not said how it is wired gets no
    verdict on what it can drive, and the report says the count came from a formula."""
    m = Machine.ring(8, 2, 0, name="silent")
    ctl = dict(m.arch.control)
    ctl.pop("channels", None)
    m._rebuild(control=ctl)
    assert not m.arch.control_plane.declared
    assert any("aggregate wiring fields" in n for n in m.resources().notes)

    # the same two-direction move that a declared broadcast plane refuses
    p = m.program("both").init({"a": "S1", "b": "S5"})
    with p.cycle("shuttle") as c:
        c.move("a", "S1", "S2")
        c.move("b", "S5", "S4")
    out = m.run(p, check_metrics=False)
    # R11 still catches it -- shuttling is unidirectional per path -- but the
    # DRIVABILITY check stays silent, because this device has not said how it is wired
    assert "R11" in out.rules_failed
    assert not any("one waveform" in str(v) for v in out.violations)


def test_row_wiring_lets_lines_differ_but_not_sites_within_a_line():
    """A grid wired by row: two rows may do different things, two traps in one row
    may not. No aggregate DAC count can express that."""
    m = Machine.ladder(6, rungs=2, highways=0, name="rows")
    ctl = dict(m.arch.control)
    ctl["channels"] = {"grouping": "row", "roles": {"linear": 1},
                       "switch_per_site": True}
    m._rebuild(control=ctl)
    plane = m.arch.control_plane
    assert plane.n_shared_channels == 2, "one channel per rail"
    ok, _ = plane.drivable({"T0": "right", "T1": "right", "B0": "left", "B1": "left"})
    assert ok, "the two rails may differ"
    ok, why = plane.drivable({"T0": "right", "T1": "left"})
    assert not ok, "two traps on one rail may not"
    assert "different things" in why[0]


def test_the_plane_reports_what_it_does_not_model():
    """The scope boundary should be visible in the object, not only in a docstring."""
    import qccd.arch.control as mod

    doc = mod.__doc__ or ""
    for absent in ("voltages", "waveform shapes", "sample rate"):
        assert absent in doc
