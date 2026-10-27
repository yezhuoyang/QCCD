"""Shared planes (qccd/gadget/planes.py, rule H2): devices that share one plane of DACs, or one
laser, play one waveform at a time -- the same rule a single device's plane has always had."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from qccd.fullstack import layers

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"
ALGS = REPO / "examples" / "gadgets" / "algorithms"


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(CACHE, log=None)


def _town(leaves, extra: str, floor: str = "town"):
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load, parse_floorplan
    fp = parse_floorplan((FLOORPLANS_DIR / f"{floor}.floor").read_text(encoding="utf-8") + extra, name="pl")
    return load(fp, leaves), fp


def test_a_floorplan_says_which_devices_share_a_plane(leaves):
    from qccd.gadget.floorplan import FloorplanError, write
    lib, fp = _town(leaves, "plane mems dacs zone1 zone2 zone3\nplane beam lasers post bridge\n")
    assert lib[lib.top].params["planes"] == [["mems", "dacs", ["zone1", "zone2", "zone3"]],
                                             ["beam", "lasers", ["post", "bridge"]]]
    assert "plane mems dacs zone1 zone2 zone3" in write(lib)
    with pytest.raises(FloorplanError):
        _town(leaves, "plane bad wires zone1\n")
    from qccd.gadget.city import City
    from qccd.gadget.algorithm import parse_algorithm_file
    lib2, _ = _town(leaves, "plane odd dacs nowhere\n")
    with pytest.raises(ValueError, match="nowhere"):
        City(parse_algorithm_file(ALGS / "bell_cnot.alg"), lib2, leaves)


@pytest.mark.parametrize("extra, saves", [
    ("plane mems dacs zone1 zone2 zone3\n", "dacs"),
    ("plane beam lasers post bridge clinic\n", "beams"),
    ("plane bus dacs roads\n", "dacs"),
])
def test_devices_on_a_plane_take_turns_and_every_rule_holds(leaves, extra, saves):
    from qccd.gadget.algorithm import parse_algorithm_file
    from qccd.gadget.city import schedule
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.place_checks import check_places
    plain_lib, _ = _town(leaves, "")
    lib, fp = _town(leaves, extra)
    for stem in ("bell_cnot", "teleport"):
        alg = parse_algorithm_file(ALGS / f"{stem}.alg")
        gir = schedule(alg, lib, leaves)
        pc = check_places(gir, lib, leaves)
        mr = judge_schedule(lib, leaves, gir, regions=fp.regions)
        assert pc["failed"] == [] and mr["failed"] == [], (stem, pc["failed"], mr["failed"])
        assert "H2" in pc["passed"] and "H2" in mr["passed"]
        hw = pc["metrics"]["hardware"]
        plain = check_places(schedule(alg, plain_lib, leaves), plain_lib, leaves)["metrics"]["hardware"]
        if saves == "dacs":
            assert hw["dacs"] < plain["dacs"] and hw["dacs_shared_away"] > 0
        else:
            assert hw["beams"] < plain["beams"]
    if "bus" in extra:
        # a trip over two roads of one plane waits between them: the holds say so
        assert gir.get("road_holds")


def test_h2_catches_devices_that_did_not_take_turns(leaves):
    """The same schedule, made without knowing the roads share a plane: two roads work at
    once, and both the place check and the machine rule say where."""
    from qccd.gadget.algorithm import parse_algorithm_file
    from qccd.gadget.city import schedule
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.place_checks import check_places
    plain_lib, _ = _town(leaves, "")
    gir = schedule(parse_algorithm_file(ALGS / "teleport.alg"), plain_lib, leaves)
    lib, fp = _town(leaves, "plane bus dacs roads\n")
    bad = copy.deepcopy(gir)
    bad["planes"] = {"bus": {"kind": "dacs", "places": [], "nets": sorted(gir["nets"]), "group": 0}}
    pc = check_places(bad, lib, leaves)
    assert "H2" in pc["failed"] and "plane bus" in pc["violations"]["H2"][0]
    assert "H2" in judge_schedule(lib, leaves, bad, regions=fp.regions)["failed"]


def test_a_folded_run_on_shared_planes_holds_per_view_and_by_composition(leaves):
    """Shor-15 folded on the full-stack floor with every road on one bus and every memory on
    one plane: the gates' plane footprints keep their calls apart, a resting block's refresh
    rounds each take their turn, and every check holds -- per view, by composition, expanded."""
    from qccd.fullstack import qec2alg
    from qccd.gadget.fold import check_composition, check_folded, expand, parse_folded, schedule_folded
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.place_checks import check_places
    src = qec2alg.lower(layers.read(REPO / "tests" / "fixtures" / "fullstack" / "shor15" / "shor15.qec",
                                    "qec")).text
    lib, fp = _town(leaves, "plane bus dacs roads\nplane mems dacs memory\n", floor="fullstack")
    keep = {}
    g = schedule_folded(parse_folded(src, name="shor15"), lib, leaves, keep=keep)
    assert check_folded(g, lib, leaves)["failed"] == []
    assert check_composition(g, keep["folder"]) == []
    x = expand(g)
    assert check_places(x, lib, leaves)["failed"] == []
    assert judge_schedule(lib, leaves, x, regions=fp.regions)["failed"] == []
    assert any(("plane", 0) in m.footprint or ("plane", 1) in m.footprint
               for m in keep["folder"].macros.values())
