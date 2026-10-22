"""#16, #17: split and merge are charged for what a move does to the chains, not for what its
class is called.  Each test is a programme that used to dodge the charge."""

from __future__ import annotations

import json
from pathlib import Path

from qccd.api import Machine
from qccd.arch import Architecture
from qccd.cost import corrected_model
from qccd.ir.tsir import TSIR
from qccd.verify import verify

COMPILED = Path(__file__).resolve().parents[1] / "qccd" / "site" / "compiled"


def _replay(arch, prog):
    rep = verify(prog, arch, corrected_model(), check_metrics=False)
    return rep.result, rep.rules.summary()


def _point(arch, which):
    return arch.primitives.curve(which).pick(corrected_model().policy)


def _chain_sites(m):
    return sorted((n for n, v in m.arch.device.nodes.items() if v.kind == "site"),
                  key=lambda s: m.arch.device.nodes[s].pos)


def test_a_shuttle_out_of_a_chain_splits_and_into_one_merges():
    m = Machine.chain(4)
    s = _chain_sites(m)
    split, merge = _point(m.arch, "split"), _point(m.arch, "merge")
    alone = m.program("alone", provenance="off")
    alone.init({"a": s[0]})
    alone.simd("shuttle", [["a", s[0], s[1]]])
    alone.simd("shuttle", [["a", s[1], s[2]]])
    out = m.program("out", provenance="off")
    out.init({"a": s[0], "b": s[0], "c": s[2]})
    out.simd("shuttle", [["a", s[0], s[1]]])          # leaves b behind: a split
    out.simd("shuttle", [["a", s[1], s[2]]])          # joins c: a merge
    r0, _ = _replay(m.arch, alone.build())
    r1, _ = _replay(m.arch, out.build())
    assert r0.quanta_components.get("split_merge", 0.0) == 0.0
    assert r1.total_us == r0.total_us + split.us + merge.us
    assert r1.quanta_components["split_merge"] == split.quanta + merge.quanta


def test_a_chain_moving_whole_is_neither_split_nor_merged():
    m = Machine.chain(4)
    s = _chain_sites(m)
    p = m.program("whole", provenance="off")
    p.init({"a": s[0], "b": s[0]})
    p.simd("shuttle", [["a", s[0], s[1]], ["b", s[0], s[1]]])
    r, _ = _replay(m.arch, p.build())
    assert r.quanta_components.get("split_merge", 0.0) == 0.0


def test_a_rotation_still_costs_neither():
    m = Machine.ring(3, 2, 2)
    p = m.program("t", provenance="off")
    p.fill()
    p.rotate(2)
    r, s = _replay(m.arch, p.build())
    assert r.quanta_components.get("split_merge", 0.0) == 0.0 and not s.get("failed")


def test_deleting_the_dock_classes_buys_nothing():
    # four hillclimb agents found that a device without its `dock`/`undock` classes docked
    # through the entail-free `shuttle` and dropped the split, the merge and their heat
    doc = json.loads((COMPILED / "ring6d.arch.json").read_text(encoding="utf-8"))
    prog = TSIR.from_json(json.loads((COMPILED / "algo_rot" / "prog.cooled.tsir.json").read_text(encoding="utf-8")))
    honest = Architecture.from_json(doc)
    assert honest.entails("dock") == ("split", "merge")
    for e in doc["control"]["classes"]["extra"]:
        if e.get("id") in ("dock", "undock"):
            e["entails"] = []
    stripped = Architecture.from_json(doc)
    assert stripped.entails("dock") == ()
    r0, _ = _replay(honest, prog)
    r1, _ = _replay(stripped, prog)
    assert r0.quanta_components["split_merge"] > 0
    assert (r1.total_us, r1.quanta_components["split_merge"]) == (r0.total_us, r0.quanta_components["split_merge"])


def _chords() -> Machine:
    """A closed loop round a square, a chord across it (J1-T1-J2, on no loop) and a dock D off
    M: `tests/test_control_plane.py::_chords`, trimmed."""
    from qccd.arch.builder import DeviceBuilder

    b = DeviceBuilder("explicit")
    ring = [("C1", 0, 0), ("J1", 0, 1), ("C2", 0, 4), ("M", 2, 4), ("C3", 4, 4), ("J2", 4, 1), ("C4", 4, 0)]
    for nid, x, y in ring + [("T1", 2, 1), ("D", 2, 5)]:
        b.site(nid, float(x), float(y), zone="trap")
    for i, (nid, _, _) in enumerate(ring):
        b.segment(f"L{i}", nid, ring[(i + 1) % len(ring)][0], loop="L0")
    b.loop("L0", [nid for nid, _, _ in ring], closed=True)
    for sid, u, v in (("c1a", "J1", "T1"), ("c1b", "T1", "J2"), ("dk", "M", "D")):
        b.segment(sid, u, v)
    return Machine.from_device(b.build(), name="chords")


def test_a_chord_is_a_translation_and_a_spur_is_a_dock():
    # R22 (#18) and the price agree on what a spur is: a dead end, one rail to the loop
    m = _chords()
    chord = m.program("chord", provenance="off")
    chord.init({"a": "J1"})
    chord.simd("shuttle", [["a", "J1", "T1", ["c1a"]]])
    dock = m.program("dock", provenance="off")
    dock.init({"a": "M"})
    dock.simd("shuttle", [["a", "M", "D", ["dk"]]])
    r0, _ = _replay(m.arch, chord.build())
    r1, _ = _replay(m.arch, dock.build())
    assert r0.quanta_components.get("split_merge", 0.0) == 0.0
    assert r1.quanta_components["split_merge"] == _point(m.arch, "split").quanta + _point(m.arch, "merge").quanta


def test_a_stub_off_an_open_road_is_transport_and_off_a_conveyor_a_dock():
    # a road (an open path) is not a moving potential: a lone ion stepping from it into a
    # dead-end stub pays nothing extra; off a closed loop, the same hop is a dock (qccd-12)
    from qccd.arch.builder import DeviceBuilder
    from qccd.verify.rules import conveyor_nodes

    def device(closed: bool) -> Machine:
        b = DeviceBuilder("explicit")
        road = [("R0", 0, 0), ("R1", 1, 0), ("R2", 2, 0), ("R3", 2, 1)]
        for nid, x, y in road + [("P", 1, -1)]:
            b.site(nid, float(x), float(y), zone="trap")
        links = list(zip(road, road[1:])) + ([(road[-1], road[0])] if closed else [])
        for i, ((u, _, _), (v, _, _)) in enumerate(links):
            b.segment(f"r{i}", u, v, loop="L0")
        b.loop("L0", [n for n, _, _ in road], closed=closed)
        b.segment("stub", "R1", "P")
        return Machine.from_device(b.build(), name="road")

    for closed, expect_dock in ((False, False), (True, True)):
        m = device(closed)
        assert bool(conveyor_nodes(m.arch.device)) is closed
        p = m.program("p", provenance="off")
        p.init({"a": "R1"})
        p.simd("shuttle", [["a", "R1", "P", ["stub"]]])
        r, _ = _replay(m.arch, p.build())
        charged = r.quanta_components.get("split_merge", 0.0)
        assert (charged > 0) is expect_dock, (closed, charged)
