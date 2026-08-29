"""R19 -- the electrode frame, and the channel count a lab-frame rotation needs.

`control.channels.frame` is the one thing about a conveyor that the expanded graph
cannot decide and no device could previously say:

    "path"   the tiling FOLLOWS the trap axis.  One waveform means "forward one slot"
             everywhere on the path, bends included -- H2's curved end zones are
             ordinary conveyor regions on the same {a,b,c} tiling as the straights
             (2305.03828, docs/PLAN.md:132, qccd/arch/generators.py:404-406).
    "lab"    the tiling is FIXED TO THE CHIP AXES.  "+x" and "-x" are two waveforms and
             a bend needs its own, so a closed path costs one channel group per
             direction it turns into.

The default is "path", so no shipped device changes verdict without an edit; the last
test in this file is the regression that pins that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from qccd.arch import Architecture, load                                  # noqa: E402
from qccd.arch.control import FRAMES                                      # noqa: E402
from qccd.arch.device import AXIS_LABELS, axis_label                      # noqa: E402
from qccd.compile.programs import rotate                                  # noqa: E402
from qccd.cost import corrected_model                                     # noqa: E402
from qccd.verify import verify                                            # noqa: E402
from qccd.verify.rules import (                                           # noqa: E402
    RULE_SOURCES, RULE_STATEMENTS, _independent_blocks, architecture_violations,
    r19_lab_frame_channels, r19_scope,
)

RING = ROOT / "arch" / "ring144_24v.arch.json"
ARCH_FILES = sorted((ROOT / "arch").glob("*.arch.json"))


def _doc(path=RING) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reframe(doc: dict, frame: str, explicit=None) -> Architecture:
    ch = dict(doc["control"]["channels"])
    ch["frame"] = frame
    if explicit is not None:
        ch = {"grouping": "explicit", "switch_per_site": True, "frame": frame,
              "explicit": explicit}
    doc = json.loads(json.dumps(doc))
    doc["control"]["channels"] = ch
    return Architecture.from_json(doc)


def _errors(arch) -> list:
    return [v for v in r19_lab_frame_channels(arch) if v.severity == "error"]


# ------------------------------------------------------------------ the entitlement
#
# C2: `pos` is NOT a lab frame.  What a rule is entitled to conclude from it, and what
# it is not.


def test_only_the_four_axis_rays_survive_the_anisotropic_lattice():
    """`qccd/phys/build.py:176,203` maps a node to nanometres as
    `(pos.x * nm_per_unit_x, pos.y * nm_per_unit_y)`, and the shipped technology gives
    those as 225000 and 355000 with a source saying they cannot be the same quantity.
    So lattice -> lab is a POSITIVE DIAGONAL map.  It fixes each axis ray as a label and
    mangles every other angle, which is the whole reason R19 counts only four things.
    """
    from qccd.phys.tech import load_technology

    tech = load_technology("eth_junction_2201.12579")
    sx, sy = tech.nm_per_unit_x.nm, tech.nm_per_unit_y.nm
    assert sx != sy, "the anisotropy this test is about"

    def scaled(p):
        return (p[0] * sx, p[1] * sy)

    for d, name in AXIS_LABELS.items():
        # the label is the same computed in lattice units and in nanometres
        assert axis_label((0.0, 0.0), d) == name
        assert axis_label(scaled((0.0, 0.0)), scaled(d)) == name

    # an oblique hop's ANGLE is not preserved, so `axis_label` refuses to name it
    import math
    lattice = math.atan2(1.0, 1.0)
    lab = math.atan2(1.0 * sy, 1.0 * sx)
    assert abs(lattice - lab) > 1e-3, "the drawing and the metal disagree by 12.6 deg"
    assert axis_label((0.0, 0.0), (1.0, 1.0)) is None


def test_the_direction_count_is_len_corners_on_every_closed_loop_the_fleet_ships():
    """C3 -- the quantity R19 needs is already a device property, cached, and printed by
    `python -m qccd show`.  Rediscovering it per instruction would put a device fact on
    the program."""
    seen = 0
    for path in ARCH_FILES:
        dev = load(path).device
        for lid, loop in dev.loops.items():
            if not loop.closed:
                continue
            seen += 1
            for step in (+1, -1):
                labels, oblique = dev.shift_directions(lid, step)
                assert not oblique, (path.name, lid)
                assert len(set(labels.values())) == len(dev.corners(lid))
    assert seen == 5, f"the fleet ships five closed loops, found {seen}"


def test_a_closed_rigid_shift_always_needs_at_least_three_directions():
    """C4 -- displacements telescope to zero, so this is a THEOREM, not a check.  It is
    why R19 is conditioned on `frame` and on a declared shift class rather than fired on
    every rotation of every closed loop."""
    for path in ARCH_FILES:
        dev = load(path).device
        for lid, loop in dev.loops.items():
            if loop.closed:
                labels, _ = dev.shift_directions(lid, 1)
                assert len(set(labels.values())) >= 3


# ------------------------------------------------------------------------- it fires


def test_r19_fires_on_the_flagship_when_it_declares_the_lab_frame():
    arch = _reframe(_doc(), "lab")
    errs = _errors(arch)
    # one per distinct per-site assignment: rotate_cw/sort_split (+1) and
    # rotate_ccw/sort_merge (-1) are two different waveform layouts, not one
    assert len(errs) == 2, [str(e) for e in errs]
    msg = str(errs[0])
    assert "'L0'" in msg and "4 axis directions" in msg
    assert "+x, +y, -x, -y" in msg
    # NOT "one per corner": directions and corners agree on every loop the fleet ships
    # but are different quantities (an L-shaped closed loop has 6 corners, 4 directions),
    # so the message reports both and equates neither.
    assert "one per corner" not in msg
    assert "the path has 4 corner(s)" in msg
    assert "can drive 1 independent group(s)" in msg
    assert all(e.rule == "R19" and e.instr_id == -1 for e in errs)


def test_r19_reaches_verify_and_architecture_violations():
    arch = _reframe(_doc(), "lab")
    assert [v.rule for v in architecture_violations(arch)] == ["R19", "R19"]
    rep = verify(rotate(arch, 1), arch, corrected_model())
    assert "R19" in rep.rules.failed()
    assert "R19" in rep.rules.checked and "R19" not in rep.rules.skipped
    assert rep.rules.by_rule()["R19"] == 2
    assert r19_scope(arch) is None


def test_r4d_reads_the_same_cycle_in_the_same_frame():
    """The per-cycle rule and the architecture rule must not disagree about the metal.
    Under the path frame the shipped broadcast map drives a rotation; under the lab
    frame the identical cycle is 4 waveforms on channels that carry 1."""
    doc = _doc()
    ok = _reframe(doc, "path")
    bad = _reframe(doc, "lab")
    model = corrected_model()
    assert verify(rotate(ok, 1), ok, model).rules.ok()
    rep = verify(rotate(bad, 1), bad, model)
    assert rep.rules.by_rule()["R4"] == 32       # r4_drivable, one per broadcast channel
    assert rep.rules.by_rule()["R19"] == 2


# --------------------------------------------------------------------- it does not


def _direction_groups(dev, *steps):
    """The coarsest channel map that drives every one of `steps`: the common refinement
    of their per-site direction assignments."""
    rail = list(dev.loops["L0"].nodes)
    assigns = [dev.shift_directions("L0", s)[0] for s in steps]
    buckets: dict[tuple, list[str]] = {}
    for site in rail:
        buckets.setdefault(tuple(a[site] for a in assigns), []).append(site)
    groups = [{"id": "g" + "".join(k), "role": "linear", "drives": sorted(v)}
              for k, v in sorted(buckets.items())]
    spurs = sorted(n.id for n in dev.nodes.values()
                   if n.kind == "site" and n.id not in set(rail))
    return groups + [{"id": "spur", "role": "junction", "drives": spurs}]


def test_four_groups_drive_one_rotation_direction_and_not_the_other():
    """The smallest edit is NOT four groups.  Four -- sizes 71/71/1/1 -- drive `+1` with
    drivable() clean, and drive `-1` nowhere, because the corner sites change groups
    when the belt reverses.  ring144_24v declares BOTH directions, so R19 still fires."""
    dev = load(RING).device
    arch = _reframe(_doc(), "lab", explicit=_direction_groups(dev, +1))
    plane = arch.control_plane
    rail = list(dev.loops["L0"].nodes)
    assert sorted((len(g.sites) for g in plane.groups), reverse=True) == [71, 71, 24, 1, 1]
    assert _independent_blocks(plane, rail) == 4
    assert plane.drivable(dev.shift_directions("L0", +1)[0])[0] is True
    assert plane.drivable(dev.shift_directions("L0", -1)[0])[0] is False
    errs = _errors(arch)
    assert len(errs) == 1 and "(-1)" in str(errs[0])


def test_six_groups_are_the_smallest_edit_that_clears_r19_on_the_flagship():
    dev = load(RING).device
    arch = _reframe(_doc(), "lab", explicit=_direction_groups(dev, +1, -1))
    plane = arch.control_plane
    rail = list(dev.loops["L0"].nodes)
    assert sorted((len(g.sites) for g in plane.groups), reverse=True) == [
        70, 70, 24, 1, 1, 1, 1]
    assert _independent_blocks(plane, rail) == 6
    assert _errors(arch) == []
    rep = verify(rotate(arch, 1), arch, corrected_model())
    assert rep.rules.ok(), rep.rules.summary()
    assert rep.result.total_steps == 1 and rep.result.total_us == 100.0


def test_r19_does_not_judge_a_device_that_declares_no_rotation():
    """Conditioned on a DECLARATION twice over.  Strip the closed-loop shift classes and
    a lab-frame broadcast device is not judged, because it never claimed it could
    rotate."""
    doc = _doc()
    doc["control"]["classes"]["extra"] = [
        e for e in doc["control"]["classes"]["extra"] if e.get("orbit") != "L0"]
    assert _errors(_reframe(doc, "lab")) == []


def test_an_unknown_frame_is_refused_when_the_plane_is_built():
    """Same discipline and the same moment as an unknown `grouping`: `control.channels`
    is an OPEN map in the schema (`{"type": "map", "values": {"type": "any"}}`), so the
    validator cannot enum it and `build_control_plane` is the one place that can."""
    arch = _reframe(_doc(), "chip")
    with pytest.raises(ValueError, match="unknown electrode frame"):
        arch.control_plane                                          # noqa: B018
    doc = _doc()
    doc["control"]["channels"]["grouping"] = "spiral"                # sanity: same shape
    with pytest.raises(ValueError, match="unknown channel grouping"):
        Architecture.from_json(doc).control_plane                    # noqa: B018
    assert FRAMES == ("path", "lab")


# ------------------------------------------------------------------ the ADL delta


def test_every_shipped_device_reads_the_path_frame():
    """The regression that makes this edit safe: every shipped plane reads "path" and
    R19 is silent on the whole fleet.

    Eight of the nine get it by DEFAULT (the key is absent).  `h2_racetrack` is the one
    that says it out loud, because it is the device whose cited source makes the claim:
    a single continuous RF null whose curved end zones run the same {a,b,c} conveyor
    tiling as the straights (2305.03828).  A rule whose discriminating parameter no
    shipped architecture carries is a rule nothing exercises, so one device carries it.
    """
    declared = []
    for path in ARCH_FILES:
        arch = load(path)
        ch = arch.control.get("channels") or {}
        if "frame" in ch:
            declared.append(path.stem.replace(".arch", ""))
            assert ch["frame"] == "path"
        assert arch.control_plane.frame == "path", path.name
        assert r19_lab_frame_channels(arch) == [], path.name
        # and it is SKIPPED with a reason, never reported as passed: "R19 passed" must
        # not be able to mean "this device is not lab-frame"
        assert r19_scope(arch) is not None, path.name
    assert declared == ["h2_racetrack"], declared


def test_r19_is_skipped_with_a_reason_and_never_silently_passed():
    arch = load(RING)
    rep = verify(rotate(arch, 1), arch, corrected_model())
    assert "R19" in rep.rules.skipped and "R19" not in rep.rules.checked
    assert "frame='path'" in rep.rules.skipped["R19"]
    assert "R19" not in rep.rules.passed()

    # the two undeclared-channel devices get the other reason
    chain = load(ROOT / "arch" / "chain.arch.json")
    assert "declares no control.channels" in r19_scope(chain)

    # a lab-frame device that never claims it can rotate gets the third
    doc = _doc()
    doc["control"]["classes"]["extra"] = [
        e for e in doc["control"]["classes"]["extra"] if e.get("orbit") != "L0"]
    assert "no shift class on a closed path" in r19_scope(_reframe(doc, "lab"))


def test_the_frame_field_round_trips_and_nothing_else_moves():
    """from_json -> to_json -> from_json, both frames, expanded and compact."""
    for frame in FRAMES:
        doc = _doc()
        doc["control"]["channels"]["frame"] = frame
        a1 = Architecture.from_json(doc)
        for expanded in (True, False):
            out = a1.to_json(expanded=expanded)
            assert out["control"]["channels"]["frame"] == frame
            a2 = Architecture.from_json(out)
            assert a2.control_plane.frame == frame
            # nothing else moved: the whole document is byte-identical on the second lap
            assert json.dumps(a2.to_json(expanded=expanded), sort_keys=True) == \
                   json.dumps(out, sort_keys=True)
            # and the device is the same device
            assert len(a2.device.nodes) == len(a1.device.nodes)
            assert a2.device.corners("L0") == a1.device.corners("L0")
            assert a2.control_plane.summary() == a1.control_plane.summary()


def test_omitting_frame_round_trips_as_the_default_and_not_as_a_new_key():
    """A device that never mentions the frame must not grow the key on a save: an ADL
    field that appears by itself makes every shipped file diff."""
    a1 = load(RING)
    for expanded in (True, False):
        out = a1.to_json(expanded=expanded)
        assert "frame" not in out["control"]["channels"]
        assert Architecture.from_json(out).control_plane.frame == "path"


def test_the_new_field_is_in_the_consumer_table_with_a_reader():
    """`export_consumers` is what the browser palette is generated from; a field missing
    from it is invisible to the editor, and one with `reader: None` is greyed as inert.
    R19 reads this one."""
    from qccd.arch.schema import export_consumers

    row = [f for f in export_consumers()["fields"]
           if f["path"] == "control.channels.frame"]
    assert len(row) == 1
    assert row[0]["default"] == "path"
    assert row[0]["reader"] == "qccd.verify.rules.r19_lab_frame_channels"


def test_r19_has_a_statement_and_a_source():
    assert RULE_STATEMENTS["R19"] and RULE_SOURCES["R19"]
    assert "2305.03828" in RULE_SOURCES["R19"]
