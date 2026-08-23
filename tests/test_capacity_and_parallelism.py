"""Per-site capacity, roadblocks, and how many ions may move at once.

Three questions with sharp answers, each of which the platform previously got wrong: a
site could not carry its own capacity, a full trap on a path blocked nothing, and a
machine that declared it could drive four movement classes could neither express that nor
be checked against it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.compile.programs import walk  # noqa: E402
from qccd.compile.schedule import schedule_events  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.verify import replay  # noqa: E402


# ------------------------------------------------------------ per-site capacity


def test_a_site_can_carry_its_own_capacity():
    m = Machine.ring(8, 2, 0, name="cap")
    assert m.capacity_of("S3") == 2
    m.set_site_capacity("S3", 7)
    assert m.capacity_of("S3") == 7
    assert m.capacity_of("S2") == 2


def test_a_per_site_capacity_survives_retuning_the_zone():
    """A trap deliberately made bigger must not silently shrink when its zone changes --
    that is the difference between an override and a default."""
    m = Machine.ring(8, 2, 0, name="cap").set_site_capacity("S3", 7)
    m.set_zone("data", capacity=5)
    assert m.capacity_of("S3") == 7, "the override stands"
    assert m.capacity_of("S2") == 5, "the rest follow the zone"


def test_a_per_site_capacity_round_trips_through_a_file(tmp_path):
    m = Machine.ring(8, 2, 0, name="cap").set_site_capacity(["S3", "S5"], 6)
    back = Machine.load(m.save(tmp_path / "c.arch.json", expanded=True))
    assert back.capacity_of("S3") == 6
    assert back.capacity_of("S5") == 6
    assert back.capacity_of("S4") == 2
    assert back.summary()["n_site_capacity_overrides"] == 2


def test_set_site_capacity_rejects_nonsense():
    m = Machine.ring(8, 2, 2, name="cap")
    with pytest.raises(ValueError, match="at least one ion"):
        m.set_site_capacity("S0", 0)
    with pytest.raises(ValueError, match="no such site"):
        m.set_site_capacity("nope", 3)


def test_capacity_is_reported_per_site():
    m = Machine.ring(8, 2, 0, name="cap").set_site_capacity("S3", 9)
    assert m.summary()["capacity_histogram"] == {2: 15, 9: 1}


# ------------------------------------------------------------------ roadblocks


def _through_full_trap(cap_of_s2: int):
    m = Machine.ring(8, 2, 0, name="block")
    m.set_site_capacity("S2", cap_of_s2)
    p = m.program("through").init({"a": "S2", "b": "S2", "mover": "S1"})
    with p.cycle("shuttle") as c:
        c.move("mover", "S1", "S3", via=["E1", "E2"])   # S1 -> S2 -> S3
    return m.run(p, model=deck_model(), check_metrics=False)


def test_a_full_trap_on_the_path_is_a_roadblock():
    """The README's central problem: "a filled trap can block the movement of another
    ion". Landing on a full site was already caught; passing THROUGH one was not."""
    r = _through_full_trap(2)          # two residents, capacity 2: no room to pass
    assert "R1" in r.rules_failed
    assert any("ROADBLOCK" in str(v) for v in r.violations)


def test_the_same_move_is_legal_once_the_trap_has_room():
    r = _through_full_trap(3)          # capacity 3: two residents plus the transit
    assert r.ok, r.rules_failed


def test_transiting_a_junction_is_not_a_roadblock():
    """A junction holds no ions by construction; passing through one is what it is for,
    and R2 -- not capacity -- governs it."""
    m = Machine.load(ROOT / "arch" / "grid9x9.arch.json")
    r = replay(walk(m.arch, 4), m.arch, corrected_model(), check_rules=True)
    assert r.rules.ok(), r.rules.summary()
    assert sum(r.junction_transits.values()) > 0, "it really does transit junctions"


def test_rotation_is_never_a_roadblock():
    """Every ion moves one segment at once, so nothing transits anything. This is
    Cyclone's roadblock-freedom, and it holds because of the geometry of the move."""
    m = Machine.load(ROOT / "arch" / "ring144_24v.arch.json")
    r = replay(m.program("r").fill().rotate(+7).build(), m.arch,
               corrected_model(), check_rules=True)
    assert r.rules.ok()


# --------------------------------------------------------------- parallelism


def test_one_cycle_moves_as_many_ions_as_there_are():
    """Participation is variadic: nothing bounds the number of ions in a cycle."""
    m = Machine.load(ROOT / "arch" / "ring144_24v.arch.json")
    r = replay(m.program("r").fill().rotate(+1).build(), m.arch,
               corrected_model(), check_rules=True)
    simd = [c for c in r.cycles if c.type == "simd"]
    assert len(simd) == 1
    assert simd[0].n_participants == 144
    assert r.total_steps == 1, "one step: they are simultaneous, not sequential"
    assert r.rules.ok()


def _two_rails(k: int):
    """A ladder whose two rails can be driven independently, with k control pathways."""
    m = Machine.ladder(12, rungs=4, highways=0, name="rails")
    m.declare_class("shift_right", type="shift", orbit="TOP", delta=1)
    m.declare_class("shift_left", type="shift", orbit="BOTTOM", delta=-1)
    ctl = dict(m.arch.control)
    ctl["max_simd_classes_per_cycle"] = k
    m._rebuild(control=ctl)
    place = {f"t{i}": f"T{i}" for i in range(12)}
    place.update({f"b{i}": f"B{i}" for i in range(12)})
    p = m.program("opposite").init(place)
    with p.cycle("shift_right") as c:
        for i in range(11):
            c.move(f"t{i}", f"T{i}", f"T{i + 1}")
    with p.cycle("shift_left") as c:
        for i in range(11, 0, -1):
            c.move(f"b{i}", f"B{i}", f"B{i - 1}")
    return m, p


def test_many_ions_at_different_sites_move_together_when_they_share_a_class():
    m, _ = _two_rails(1)
    place = {f"t{i}": f"T{i}" for i in range(12)}
    place.update({f"b{i}": f"B{i}" for i in range(12)})
    p = m.program("same").init(place)
    with p.cycle("shift_right") as c:
        for i in range(11):
            c.move(f"t{i}", f"T{i}", f"T{i + 1}")
        for i in range(11):
            c.move(f"b{i}", f"B{i}", f"B{i + 1}")
    r = replay(p.build(), m.arch, corrected_model(), check_rules=True)
    assert len([c for c in r.cycles if c.type == "simd"]) == 1
    assert r.rules.ok(), r.rules.summary()


def test_more_control_buys_real_overlap():
    """One class: the two rails serialize. Two classes: they run at once."""
    m1, p1 = _two_rails(1)
    m2, p2 = _two_rails(2)
    s1 = schedule_events(
        p1.build(), m1.arch,
        replay(p1.build(), m1.arch, corrected_model(), check_rules=False))
    s2 = schedule_events(
        p2.build(), m2.arch,
        replay(p2.build(), m2.arch, corrected_model(), check_rules=False))
    assert s1.n_overlapped == 0
    assert s2.n_overlapped == 1
    assert s2.makespan_us == pytest.approx(s1.makespan_us / 2)
    assert m1.run(s1.program, check_metrics=False).ok
    assert m2.run(s2.program, check_metrics=False).ok


def test_asking_for_more_parallelism_than_the_hardware_has_is_caught():
    m, p = _two_rails(1)
    forced = p.build()
    for n in (1, 2):
        forced.instructions[n] = forced.instructions[n].with_annotations(
            t0=0.0, t1=100.0, cost=0, steps=1)
    out = m.run(forced, check_metrics=False)
    assert "R4" in out.rules_failed
    assert any("movement classes active together" in str(v) for v in out.violations)


def test_an_unscheduled_program_reports_no_false_concurrency():
    """With no explicit times a program is a strict sequence, so the concurrency check is
    a no-op -- the honest answer for something that has not been scheduled, not a pass."""
    m, p = _two_rails(1)
    assert m.run(p, check_metrics=False).ok
