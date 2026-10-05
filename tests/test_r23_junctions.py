"""R23: a junction is crossed, not rested on.

Reported 2026-09-29 on a hexagon lattice a reader's agent designed: ions sat on the corners,
where three rails meet, with empty traps on every side, and no rule objected -- R2 only bounds
how many ions a junction holds at an instant.  R23 judges where an ion RESTS.  It names the
two cases that are not a violation, and each has a test here: a slot of a loop the programme
rotates, and a device whose traps off the junctions cannot hold the programme's ions.
"""

from __future__ import annotations

from qccd.api import Machine
from qccd.arch.builder import DeviceBuilder
from qccd.cost.models import corrected_model
from qccd.verify import rule_statements, verify


def ring():
    """Six loop slots S0..S5 with a dock off S0 and S3: those two are where three rails meet."""
    return Machine.ring(3, 2, 2, name="ring6d")


def r23(m, prog):
    return [v for v in verify(prog, m.arch, corrected_model(), only_rules=["R23"]).rules.violations if v.rule == "R23"]


def test_the_rule_is_stated_and_sourced():
    s = rule_statements()["R23"]
    assert "crossed" in s["statement"] and s["sources"]


def test_an_ion_that_stops_on_a_junction_with_room_elsewhere_is_refused():
    m = ring()
    assert m.arch.device.degree("S0") == 3
    p = m.program("stop", provenance="off")
    p.init({"d0": "S1"})
    p.move("d0", "S1", "S0")
    bad = r23(m, p.build())
    assert len(bad) == 1 and "d0" in bad[0].message and "S0" in bad[0].message


def test_crossing_the_same_junction_in_one_move_is_accepted():
    m = ring()
    dev = m.arch.device
    via = [dev.segment_between("S1", "S0").id, dev.segment_between("S0", "A0").id]
    p = m.program("cross", provenance="off")
    p.init({"d0": "S1"})
    p.simd("shuttle", [["d0", "S1", "A0", via]])
    assert r23(m, p.build()) == []


def test_a_stay_is_reported_once_however_long_it_lasts():
    m = ring()
    p = m.program("stay", provenance="off")
    p.init({"d0": "S1", "d1": "S4"})
    p.move("d0", "S1", "S0")
    p.move("d1", "S4", "S5")          # d0 is still standing on S0 through these
    p.move("d1", "S5", "S4")
    assert len(r23(m, p.build())) == 1


def test_an_ion_placed_on_a_junction_at_the_start_is_caught_too():
    m = ring()
    p = m.program("placed", provenance="off")
    p.init({"d0": "S0", "d1": "S4"})
    p.move("d1", "S4", "S5")
    bad = r23(m, p.build())
    assert len(bad) == 1 and "d0" in bad[0].message


def test_a_slot_of_a_loop_the_programme_rotates_is_a_conveyor_slot():
    """A rotation carries every ion on the loop through every slot, the dock slots included."""
    m = ring()
    p = m.program("conveyor", provenance="off")
    p.init({"d0": "S1", "d1": "S5"})   # S0 is between them: either direction lands one on it
    p.rotate(-1, loop="L0")
    prog = p.build()
    assert any(i.template and i.template.get("kind") == "loop_shift" for i in prog.instructions)
    assert r23(m, prog) == []


def test_a_device_with_no_room_off_its_junctions_is_not_judged():
    """A star: one hub where three rails meet, and three arms that hold one ion each.  Four
    ions cannot all stand off the hub, so one standing on it breaks nothing."""
    b = DeviceBuilder("explicit")
    b.site("H", 0.0, 0.0, zone="trap", capacity=1)
    for k, (x, y) in enumerate([(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0)]):
        b.site(f"A{k}", x, y, zone="trap", capacity=1)
        b.segment(f"E{k}", "H", f"A{k}")
    m = Machine.from_device(b.build(), name="star")
    p = m.program("full", provenance="off")
    p.init({"d0": "H", "d1": "A0", "d2": "A1", "d3": "A2"})
    p.cool()
    assert r23(m, p.build()) == []
    # ... and with three ions there is room, so the same hub is refused
    q = m.program("room", provenance="off")
    q.init({"d0": "H", "d1": "A0", "d2": "A1"})
    q.cool()
    assert len(r23(m, q.build())) == 1
