"""R19-R22: the geometry rules and the one-waveform rule, each against a device or a
programme built to break it (docs/rules.md: a check that cannot fail is not a check)."""

from __future__ import annotations

from collections import Counter

import pytest

from qccd.api import Machine
from qccd.arch.device import Device, Node, Segment
from qccd.cost import corrected_model
from qccd.verify import verify
from qccd.verify.rules import (
    RULE_SOURCES, RULE_STATEMENTS, geometry_limits, geometry_violations, motion_signature,
    rail_angle_deg,
)


def explicit(nodes: dict, segments: list, **budget) -> Machine:
    ns = {nid: Node(id=nid, pos=(float(x), float(y)), kind="site", zone_type="trap", labels=("trap",))
          for nid, (x, y) in nodes.items()}
    ss = {sid: Segment(id=sid, ends=(a, b), length=1.0, capacity=1, loop=None, labels=("rail",))
          for sid, a, b in segments}
    dev = Device(nodes=ns, segments=ss, loops={}, generator="explicit", params={})
    kw = {"budget": budget} if budget else {}
    return Machine.from_device(dev, name="t", **kw)


def by_rule(viols) -> Counter:
    return Counter(v.rule for v in viols)


def test_the_new_rules_have_statements_and_sources():
    for r in ("R19", "R20", "R21", "R22"):
        assert RULE_STATEMENTS[r] and RULE_SOURCES[r]


def test_defaults_are_the_surface_trap_limits():
    m = explicit({"A": (0, 0), "B": (1, 0)}, [("r", "A", "B")])
    assert geometry_limits(m.arch) == (4, 60.0)
    m = explicit({"A": (0, 0), "B": (1, 0)}, [("r", "A", "B")], max_junction_degree=6, min_rail_angle_deg=45)
    assert geometry_limits(m.arch) == (6, 45.0)


def test_r19_refuses_a_fifth_rail_and_accepts_an_x_junction():
    star = explicit({"J": (0, 0), "N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0), "NE": (1, 1)},
                    [("rN", "J", "N"), ("rS", "J", "S"), ("rE", "J", "E"), ("rW", "J", "W"), ("rNE", "J", "NE")])
    c = by_rule(geometry_violations(star.arch))
    assert c["R19"] == 1
    assert any("degree 5" in v.message and "at most 4" in v.message for v in geometry_violations(star.arch))
    cross = explicit({"J": (0, 0), "N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)},
                     [("rN", "J", "N"), ("rS", "J", "S"), ("rE", "J", "E"), ("rW", "J", "W")])
    assert by_rule(geometry_violations(cross.arch))["R19"] == 0
    # a 3D trap may declare 6
    star6 = explicit(star.arch.device.nodes and {n: (p.pos[0], p.pos[1]) for n, p in star.arch.device.nodes.items()},
                     [(s.id, s.ends[0], s.ends[1]) for s in star.arch.device.segments.values()], max_junction_degree=6)
    assert by_rule(geometry_violations(star6.arch))["R19"] == 0


def test_r20_measures_the_angle_between_rails():
    fork = explicit({"J": (0, 0), "W": (-1, 0), "A": (2, 0), "B": (1.732, 1)},
                    [("rW", "J", "W"), ("rA", "J", "A"), ("rB", "J", "B")])
    assert abs(rail_angle_deg(fork.arch.device, "J", "rA", "rB") - 30.0) < 0.1
    assert abs(rail_angle_deg(fork.arch.device, "J", "rW", "rA") - 180.0) < 1e-9
    v = geometry_violations(fork.arch)
    assert by_rule(v)["R20"] == 1
    assert "30.0 degrees" in v[0].message and "60 degrees" in v[0].message
    # declaring a smaller minimum accepts it
    fork25 = explicit({"J": (0, 0), "W": (-1, 0), "A": (2, 0), "B": (1.732, 1)},
                      [("rW", "J", "W"), ("rA", "J", "A"), ("rB", "J", "B")], min_rail_angle_deg=25)
    assert by_rule(geometry_violations(fork25.arch))["R20"] == 0


def test_r21_refuses_a_crossing_and_a_rail_through_a_node():
    crossing = explicit({"A": (-1, 0), "B": (1, 0), "C": (0, -1), "D": (0, 1)},
                        [("rAB", "A", "B"), ("rCD", "C", "D")])
    v = geometry_violations(crossing.arch)
    assert by_rule(v)["R21"] == 1 and "cross without a junction" in v[0].message
    through = explicit({"A": (0, 0), "B": (2, 0), "M": (1, 0), "N": (1, 1)},
                       [("rAB", "A", "B"), ("rMN", "M", "N")])
    v = geometry_violations(through.arch)
    assert by_rule(v)["R21"] == 1 and "passes through node M" in v[0].message
    planar = explicit({"A": (-1, 0), "B": (1, 0), "C": (0, -1), "D": (0, 1), "J": (0, 0)},
                      [("rA", "A", "J"), ("rB", "J", "B"), ("rC", "C", "J"), ("rD", "J", "D")])
    assert not geometry_violations(planar.arch)


def test_every_shipped_generator_device_passes_the_geometry_rules():
    for m in (Machine.ring(3, 2, 2), Machine.ring(72, 2, 24), Machine.chain(4), Machine.racetrack(4),
              Machine.grid(2, 3), Machine.grid(9, 9), Machine.dual_loop(3, couplings=[1]), Machine.ladder(3)):
        assert not geometry_violations(m.arch), m.name


def test_a_periodic_grid_is_not_planar():
    torus = Machine.grid(4, 4, periodic=True)
    c = by_rule(geometry_violations(torus.arch))
    assert c["R21"] > 0 and c["R20"] > 0 and c["R19"] == 0


def test_the_ring_corner_docks_run_outward():
    m = Machine.ring(72, 2, 24)
    dev = m.arch.device
    # the two corner docks: the spur is perpendicular to the row, not along the end cap
    for s in dev.segments.values():
        if "spur" not in s.labels:
            continue
        rail, dock = s.ends
        rx, ry = dev.nodes[rail].pos
        dx, dy = dev.nodes[dock].pos
        assert rx == dx and dy != ry, (rail, dock)


def _grid_prog(m: Machine, second):
    p = m.program("t", provenance="off")
    p.init({"d0": "T0_0v", "d1": second[0]})
    p.simd("shuttle", [["d0", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]], second[1]])
    return p.build()


def test_r22_refuses_two_motions_in_one_cycle_and_accepts_one():
    m = Machine.grid(2, 3)
    model = corrected_model()
    mixed = _grid_prog(m, ("T0_1h", ["d1", "T0_1h", "T1_1v", ["T0_1h.b", "T1_1v.a"]]))
    rep = verify(mixed, m.arch, model, check_metrics=False)
    assert "R22" in rep.rules.summary()["failed"]
    msg = next(v.message for v in rep.rules.violations if v.rule == "R22")
    assert "2 ions move in 2 different ways" in msg and "shuttle +x,+y" in msg and "shuttle +y,+y" in msg
    same = _grid_prog(m, ("T1_0v", ["d1", "T1_0v", "T1_1v", ["T1_0v.b", "T1_1v.a"]]))
    rep = verify(same, m.arch, model, check_metrics=False)
    assert "R22" not in rep.rules.summary()["failed"] and "R22" in rep.rules.checked


def test_r22_is_uniform_for_a_rotation_and_a_dock_batch():
    m = Machine.ring(3, 2, 2)
    p = m.program("t", provenance="off")
    p.fill()
    p.rotate(2)
    rep = verify(p.build(), m.arch, corrected_model(), check_metrics=False)
    assert "R22" not in rep.rules.summary()["failed"]


def test_r22_is_skipped_under_direct_control():
    m = Machine.grid(2, 3)
    doc = m.arch.to_json()
    doc.setdefault("control", {})["model"] = "direct"
    from qccd.arch import Architecture
    arch = Architecture.from_json(doc)
    mixed = _grid_prog(m, ("T0_1h", ["d1", "T0_1h", "T1_1v", ["T0_1h.b", "T1_1v.a"]]))
    rep = verify(mixed, arch, corrected_model(), check_metrics=False)
    assert "R22" not in rep.rules.summary()["failed"]


def test_only_rules_can_ask_for_a_geometry_rule_alone():
    torus = Machine.grid(4, 4, periodic=True)
    p = torus.program("t", provenance="off")
    p.init({"d0": "T0_0h"})
    rep = verify(p.build(), torus.arch, corrected_model(), only_rules=["R21"], check_metrics=False)
    s = rep.rules.summary()
    assert "R21" in s["failed"] and "R21" in rep.rules.checked and "R20" not in rep.rules.checked


def test_r20_forgives_a_corner_stored_a_hair_under_the_limit():
    # a generator stores computed positions: a corner drawn at 60 degrees reads 59.99998
    import math
    a = math.radians(59.99998)
    near = explicit({"J": (0, 0), "A": (1, 0), "B": (math.cos(a), math.sin(a))},
                    [("rA", "J", "A"), ("rB", "J", "B")])
    assert abs(rail_angle_deg(near.arch.device, "J", "rA", "rB") - 59.99998) < 1e-6
    assert by_rule(geometry_violations(near.arch))["R20"] == 0
    b = math.radians(59.9)
    under = explicit({"J": (0, 0), "A": (1, 0), "B": (math.cos(b), math.sin(b))},
                     [("rA", "J", "A"), ("rB", "J", "B")])
    assert by_rule(geometry_violations(under.arch))["R20"] == 1


def test_verify_judges_the_geometry_rules_without_being_asked():
    # the grader calls verify() with no `only_rules`; before #21 it never ran R19-R21, so a
    # device whose rails meet at 30 degrees was eligible on the official board
    fork = explicit({"J": (0, 0), "W": (-1, 0), "A": (2, 0), "B": (1.732, 1)},
                    [("rW", "J", "W"), ("rA", "J", "A"), ("rB", "J", "B")])
    p = fork.program("t", provenance="off")
    p.init({"d0": "W"})
    rep = verify(p.build(), fork.arch, corrected_model(), check_metrics=False)
    s = rep.rules.summary()
    assert "R20" in s["failed"]
    assert {"R19", "R20", "R21"} <= rep.rules.checked
    assert by_rule(fork.violations())["R20"] == 1
