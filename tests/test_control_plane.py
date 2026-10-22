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
    # DRIVABILITY check stays silent, because this device has not said how it is wired.
    # That check is R4d, and it reports under "R4" with a message naming the channel
    # ("channel 'linear_h.all.0' drives 16 sites with one waveform, ..."): the declared
    # ring with the same cycle gets 32 of them.  The test used to look for "one waveform"
    # anywhere, which was R4d's fingerprint until R22 arrived.
    assert "R11" in out.rules_failed
    drivability = [str(v) for v in out.violations if v.rule == "R4"]
    assert not drivability, drivability
    # R22 DOES judge this cycle, and that is not a verdict on the wiring: it asks whether
    # one cycle is one waveform under the control MODEL (skipped only for
    # `control.model == "direct"`), and this device still declares broadcast SIMD
    # classes -- it is silent only about WHICH sites share a channel.
    assert "R22" in out.rules_failed
    assert m.arch.control.get("model", "simd_classes") != "direct"


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


# ------------------------------------------------- R22: what one waveform is, hop by hop
#
# Two ways the hop label used to be coarser than the motion, both found by the hillclimb
# agents (issues #18 and #19).  A label that is too coarse lets R22 put two different
# motions on one broadcast waveform, and the router, which labels exactly as R22 does,
# then compiles them into one cycle.


def _cycle_r22(m, moves):
    """The R22 violations of one broadcast cycle: `moves` are `(ion, src, dst, via)`."""
    p = m.program("one_cycle").init({ion: src for ion, src, _, _ in moves})
    p.simd("shuttle", [list(mv) for mv in moves])
    return [v for v in m.run(p, check_metrics=False).violations if v.rule == "R22"]


def _fan():
    """Three separate rails leaving three sites: one at 30 degrees and two at 60.  The
    60-degree rails are stored the way a generator stores them, rounded to 6 places, so
    their angles are 60.000005 and 59.999997 degrees and not 60."""
    from qccd.arch.builder import DeviceBuilder

    b = DeviceBuilder("explicit")
    for sid, (x, y), (dx, dy) in (("R30", (0.0, 0.0), (1.732051, 1.0)),
                                  ("R60", (5.0, 0.0), (1.0, 1.732051)),
                                  ("R60b", (10.0, 0.0), (0.5, 0.866025))):
        b.site(sid + ".o", x, y, zone="trap")
        b.site(sid + ".e", x + dx, y + dy, zone="trap")
        b.segment(sid, sid + ".o", sid + ".e")
    return Machine.from_device(b.build(), name="fan")


def test_a_hop_off_the_axes_is_labelled_by_its_angle():
    """`+x+y` was every hop up and to the right.  The sign prefix stays, so an axis-aligned
    label is what it always was; the angle is whole degrees in [0, 360)."""
    from qccd.verify.rules import direction_label

    assert direction_label(1.0, 0.0) == "+x" and direction_label(0.0, -2.5) == "-y"
    assert direction_label(0.0, 0.0) == "0"
    assert direction_label(1.732051, 1.0) == "+x+y@30"
    assert direction_label(1.0, 1.732051) == direction_label(0.5, 0.866025) == "+x+y@60"
    assert direction_label(1.0, 1.0) == "+x+y@45"
    assert direction_label(-1.0, -1.732051) == "-x-y@240"
    assert direction_label(1.732051, -1.0) == "+x-y@330"
    # a hop just below the x axis by more than RAIL_EPS keeps its sign and rounds to 0
    assert direction_label(1000.0, -2e-6) == "+x-y@0"


def test_hops_at_30_and_60_degrees_in_one_broadcast_cycle_are_two_waveforms():
    """Issue #19: both hops read `+x+y` under the sign-only label, so R22 passed a cycle
    that one broadcast waveform cannot play."""
    bad = _cycle_r22(_fan(), [("a", "R30.o", "R30.e", ["R30"]), ("b", "R60.o", "R60.e", ["R60"])])
    assert len(bad) == 1, bad
    assert "shuttle +x+y@30 and shuttle +x+y@60" in bad[0].message


def test_two_parallel_slanted_hops_share_one_waveform():
    """...and the angle is rounded, so two rails a generator stored a few 1e-6 apart in
    angle are still parallel, and one waveform still drives both."""
    assert _cycle_r22(_fan(), [("b", "R60.o", "R60.e", ["R60"]), ("c", "R60b.o", "R60b.e", ["R60b"])]) == []


def _chords():
    """A closed loop L0 round a 4 x 4 square, two chords across it (J1-T1-J2 at y = 1 and
    J3-T3-J4 at y = 3, on no loop), and two real docks: D off M at the top, a two-trap spur
    E-E2 off B at the bottom.  Every node is a site, so R4d's spur test applies too."""
    from qccd.arch.builder import DeviceBuilder

    b = DeviceBuilder("explicit")
    ring = [("C1", 0, 0), ("J1", 0, 1), ("J3", 0, 3), ("C2", 0, 4), ("M", 2, 4), ("C3", 4, 4),
            ("J4", 4, 3), ("J2", 4, 1), ("C4", 4, 0), ("B", 2, 0)]
    for nid, x, y in ring + [("T1", 2, 1), ("T3", 2, 3), ("D", 2, 5), ("E", 2, -1), ("E2", 2, -2)]:
        b.site(nid, float(x), float(y), zone="trap")
    for i, (nid, _, _) in enumerate(ring):
        b.segment(f"L{i}", nid, ring[(i + 1) % len(ring)][0], loop="L0")
    b.loop("L0", [nid for nid, _, _ in ring], closed=True)
    for sid, u, v in (("c1a", "J1", "T1"), ("c1b", "T1", "J2"), ("c3a", "J3", "T3"),
                      ("c3b", "T3", "J4"), ("dk", "M", "D"), ("sp", "B", "E"), ("sp2", "E", "E2")):
        b.segment(sid, u, v)
    return Machine.from_device(b.build(), name="chords")


def test_a_chord_between_two_loop_nodes_is_not_a_spur():
    """Issue #18.  T1 has a rail to J1 AND one to J2, both on the loop, so an ion crossing
    it is translated.  Labelled as a spur, the trip J1->T1->J2 and the trip back were both
    `spur:inward,spur:outward`: opposite motions on one waveform."""
    from qccd.verify.rules import hop_label, loop_node_set, rails_to_loop

    dev = _chords().arch.device
    on = loop_node_set(dev)

    def trip(*path):
        return ",".join(hop_label(dev, on, u, v, dev.segment_between(u, v))
                        for u, v in zip(path, path[1:]))

    assert rails_to_loop(dev, on, "T1") == 2
    assert trip("J1", "T1", "J2") == "+x,+x"
    assert trip("J2", "T1", "J1") == "-x,-x"
    # a dock is a dead end, and a longer spur's first trap is one too: both keep the spur
    # labels, so the docks above and below a rail still share one waveform
    assert rails_to_loop(dev, on, "D") == rails_to_loop(dev, on, "E") == 1
    assert trip("M", "D") == trip("B", "E") == "spur:inward"
    assert trip("D", "M") == trip("E", "B") == "spur:outward"
    assert trip("E", "E2") == "-y"


def test_opposite_moves_across_two_chords_are_two_waveforms():
    """The same cycle the bb144 one-loop torus relied on: two ions leave two chords in
    opposite directions.  Both were `spur:outward` before."""
    m = _chords()
    bad = _cycle_r22(m, [("a", "T1", "J2", ["c1b"]), ("b", "T3", "J3", ["c3a"])])
    assert len(bad) == 1 and "shuttle +x and shuttle -x" in bad[0].message, bad
    # the docks are unchanged: above the rail and below it, one waveform
    assert _cycle_r22(m, [("a", "M", "D", ["dk"]), ("b", "B", "E", ["sp"])]) == []


def test_r4d_and_r22_agree_on_what_a_spur_is():
    """R4d's spur clause judges the sites that HAVE a spur; a chord's ends are not among
    them, so a chord move is judged as the lab-frame translation it is."""
    from types import SimpleNamespace

    from qccd.verify.rules import spur_actions

    _, spur_sites = spur_actions(SimpleNamespace(arch=_chords().arch, moves=[]))
    assert spur_sites == {"M", "D", "B", "E"}


def _torus(loops):
    """`Machine.grid(3, 3, periodic=True)` -- nine junctions, a trap on each of the 18
    rails -- with its rails split into two edge-disjoint Hamiltonian cycles, A and B.
    `loops=1` declares A alone as the closed loop L0, which leaves every B trap a chord
    between two loop junctions (the bb144 design of issue #18); `loops=2` declares B too,
    as L1 (the honest version of the same torus)."""
    from qccd.arch import Architecture

    cycles = {
        "L0": ["J0_0", "T0_0h", "J1_0", "T1_0h", "J2_0", "T2_0v", "J2_1", "T2_1h", "J0_1",
               "T0_1h", "J1_1", "T1_1v", "J1_2", "T1_2h", "J2_2", "T2_2h", "J0_2", "T0_2v"],
        "L1": ["J0_0", "T0_0v", "J0_1", "T0_1v", "J0_2", "T0_2h", "J1_2", "T1_2v", "J1_0",
               "T1_0v", "J1_1", "T1_1h", "J2_1", "T2_1v", "J2_2", "T2_2v", "J2_0", "T2_0h"],
    }
    doc = Machine.grid(3, 3, periodic=True, name="torus").arch.to_json(expanded=True)
    g = doc["geometry"]
    g["generator"], g["params"] = "explicit", {}
    for n in g["nodes"]:
        n.pop("corner", None)
        n.pop("degree", None)
    on = {}
    for lid in list(cycles)[:loops]:
        seq = cycles[lid]
        for u, v in zip(seq, seq[1:] + seq[:1]):
            on[frozenset((u, v))] = lid
        g.setdefault("loops", []).append({"id": lid, "kind": "ring", "nodes": seq, "closed": True})
    for s in g["segments"]:
        s.pop("corner_endpoints", None)
        s["loop"] = on.get(frozenset(s["ends"]))
    assert sum(s["loop"] is not None for s in g["segments"]) == 18 * loops
    return Machine(Architecture.from_json(doc))


def test_the_one_loop_and_the_two_loop_torus_get_the_same_r22_verdict():
    """The test issue #18 asks for.  Two ions cross two B rails in opposite directions
    along B.  Declared as L1 that is `L1:+1,L1:+1` against `L1:-1,L1:-1`; left off every
    loop it is now `+y,+y` against `-x,-y` -- two waveforms either way.  With the chord
    read as a spur, the one-loop torus said `spur:outward,spur:inward` for both."""
    moves = [("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),     # forward along B
             ("b", "T1_1h", "T1_0v", ["T1_1h.a", "T1_0v.b"])]     # backward along B
    one, two = _cycle_r22(_torus(1), moves), _cycle_r22(_torus(2), moves)
    assert len(one) == len(two) == 1, (one, two)
    assert "shuttle +y,+y" in one[0].message and "shuttle -x,-y" in one[0].message
    assert "shuttle L1:+1,L1:+1" in two[0].message and "shuttle L1:-1,L1:-1" in two[0].message


def test_the_plane_reports_what_it_does_not_model():
    """The scope boundary should be visible in the object, not only in a docstring."""
    import qccd.arch.control as mod

    doc = mod.__doc__ or ""
    for absent in ("voltages", "waveform shapes", "sample rate"):
        assert absent in doc
