"""M0 -- the architecture description language: parse, expand, round-trip.

The acceptance criterion is stated structurally, not numerically, because the whole point
of the layer is that downstream code reads degrees off the expanded graph rather than
being told what to charge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import (  # noqa: E402
    Architecture,
    ExpansionError,
    OperatingPointPolicy,
    ValidationError,
    chain,
    check,
    devices_equal,
    grid,
    load,
    loads,
    ring,
    validate_document,
)

ARCH_DIR = ROOT / "arch"
FILES = sorted(ARCH_DIR.glob("*.arch.json"))


def test_there_are_reference_architectures():
    names = {p.name for p in FILES}
    assert {"ring144_24v.arch.json", "cyclone_base.arch.json", "chain.arch.json"} <= names


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_parses_and_expands(path):
    arch = load(path)
    assert arch.device.nodes
    assert arch.device.segments
    assert not arch.device.check_structure()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_round_trips_expanded_and_compact(path):
    arch = load(path)
    expanded = Architecture.from_json(arch.to_json(expanded=True))
    assert not devices_equal(arch.device, expanded.device)
    compact = Architecture.from_json(arch.to_json(expanded=False))
    assert not devices_equal(arch.device, compact.device)
    # and the expanded form is stable under a second trip through JSON text
    twice = loads(json.dumps(expanded.to_json(expanded=True)))
    assert not devices_equal(expanded.device, twice.device)


def _fuzzed_devices():
    """A few generator devices beside the shipped nine, because the two that lost node
    order (`grid9x9`, `deck_unit_cell`) are exactly the ones whose generator interleaves
    junctions with sites -- a corpus of rings would report this fixed when it is not."""
    out = [("ring8", ring(8)), ("ring12v", ring(12, verticals=2)), ("chain5", chain(5))]
    for a, b in ((3, 3), (4, 2), (5, 4)):
        out.append((f"grid{a}x{b}", grid(a, b)))
    return out


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_expanded_geometry_round_trips_node_order(path):
    """Node order is load-bearing and the JSON has to carry it.

    The old `sites` + `junctions` split was a lossy encoding of an ordered dict:
    `from_json` rebuilt as `sites + junctions`, so `grid9x9` and `deck_unit_cell` came
    back as `T0_0h,...` where they went in as `J0_0,...`.  Two things ride on the order.
    `architecture_listing(mode="explicit")` emits statements in it -- the same
    architecture differed by 166 lines before and after a reload -- and `layout._bows`
    sums a centroid over `pos.values()` in it, where after a drag the two orders differ
    by 2-5 ulp, which is enough to flip the collinear tie-break and a segment's bow sign.

    The FIRST assertion is the one that matters: it catches a "fix" that canonicalises by
    sorting, which the round-trip assertion alone would not (both sides would sort).
    """
    from qccd.arch.device import Device

    dev = load(path).device
    doc = dev.to_json()
    assert [n["id"] for n in doc["nodes"]] == list(dev.nodes), "the writer is not faithful"
    assert list(Device.from_json(doc)[0].nodes) == list(dev.nodes), \
        "the reader is not faithful"
    full = load(path).to_json(expanded=True)
    assert (json.dumps(full)
            == json.dumps(Architecture.from_json(full).to_json(expanded=True)))


@pytest.mark.parametrize("name,dev", _fuzzed_devices(), ids=lambda v: v if isinstance(v, str) else "")
def test_generator_devices_round_trip_node_order(name, dev):
    from qccd.arch.device import Device

    doc = dev.to_json()
    assert [n["id"] for n in doc["nodes"]] == list(dev.nodes)
    assert list(Device.from_json(doc)[0].nodes) == list(dev.nodes)


def test_the_node_order_check_catches_the_split_that_lost_it(tmp_path):
    """MUTATION GUARD.  Plant the encoding this change removed and confirm the property
    test's first assertion is what fails -- on `grid`, which interleaves, and not on a
    ring, which is all sites and so cannot show the defect at all."""
    from qccd.arch.device import Device

    dev = grid(3, 3)
    order = list(dev.nodes)
    assert [n for n in order if n.startswith("J")], "no junctions; nothing to interleave"
    assert order != sorted(order, key=lambda n: (dev.nodes[n].kind != "site", n)), \
        "this generator does not interleave, so the guard would prove nothing"

    doc = dev.to_json()
    split = {k: v for k, v in doc.items() if k != "nodes"}
    split["sites"] = [n for n in doc["nodes"] if n["kind"] == "site"]
    split["junctions"] = [n for n in doc["nodes"] if n["kind"] != "site"]
    # the legacy reader still loads it -- it just cannot recover an order never written
    assert list(Device.from_json(split)[0].nodes) != order

    # and a "fix" that canonicalises by sorting round-trips while still losing the order
    canon = dict(doc, nodes=sorted(doc["nodes"], key=lambda n: n["id"]))
    assert list(Device.from_json(canon)[0].nodes) == [n["id"] for n in canon["nodes"]]
    assert [n["id"] for n in canon["nodes"]] != order


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_declared_derived_fields_match_the_graph(path):
    """A hand-edited `degree` or `corner` in a file must not be able to lie."""
    arch = load(path)
    doc = arch.to_json(expanded=True)
    nodes = doc["geometry"]["nodes"]
    nodes[0]["degree"] = nodes[0]["degree"] + 7
    with pytest.raises(ExpansionError, match="degree"):
        Architecture.from_json(doc)


# --------------------------------------------------------------------------- ring


def test_shipped_ring_structure():
    arch = load(ARCH_DIR / "ring144_24v.arch.json")
    dev = arch.device
    s = dev.summary()

    assert s["n_nodes"] == 168  # 144 rail slots + 24 ancilla sites
    assert s["n_segments"] == 168  # 144 rail segments + 24 spurs
    assert len([n for n in dev.nodes if n.startswith("S")]) == 144

    assert len([n for n in dev.nodes.values() if n.has("top")]) == 72
    assert len([n for n in dev.nodes.values() if n.has("bottom")]) == 72

    # 24 degree-3 dock nodes
    assert s["n_junction_nodes"] == 24
    assert s["n_docks"] == 24
    assert sorted(dev.junction_nodes) == sorted(n.id for n in dev.labelled("dock"))
    assert sorted(int(n.id[1:]) for n in dev.labelled("dock")) == list(range(0, 144, 6))

    # 4 corners, 2 of which are also docks
    assert s["n_corners"] == 4
    assert s["corner_ids"] == sorted(["S0", "S71", "S72", "S143"])
    assert s["n_dock_corners"] == 2
    docks = {n.id for n in dev.labelled("dock")}
    assert docks & dev.all_corners == {"S0", "S72"}

    assert s["degree_histogram"] == {1: 24, 2: 120, 3: 24}


def test_ring_slot_order_matches_the_visualizer():
    dev = ring(72, 2, 24)
    assert dev.nodes["S0"].pos == (0.0, 0.0)
    assert dev.nodes["S71"].pos == (71.0, 0.0)
    assert dev.nodes["S72"].pos == (71.0, 1.0)
    assert dev.nodes["S143"].pos == (0.0, 1.0)
    assert dev.nodes["S0"].has("top") and dev.nodes["S72"].has("bottom")


def test_corner_detection_survives_a_spur_collinear_with_the_rail():
    """S0's spur points the same way as the rail segment to S143.

    A purely geometric "did the direction change here" test over all incident segments
    would misclassify that; corners are found by walking the loop instead.
    """
    dev = ring(72, 2, 24)
    assert dev.nodes["A0"].pos == (0.0, 0.5)  # on the segment S0 -> S143
    assert dev.degree("S0") == 3
    assert "S0" in dev.corners("L0")
    assert "S6" not in dev.corners("L0")  # a T-junction that does not turn


def test_ring_verticals_change_the_degree_histogram():
    assert ring(72, 2, 0).summary()["degree_histogram"] == {2: 144}
    assert ring(72, 2, 24).summary()["n_junction_nodes"] == 24
    assert ring(72, 2, 12).summary()["n_junction_nodes"] == 12
    assert ring(72, 2, 144).summary()["n_junction_nodes"] == 144


def test_ring_rejects_uneven_verticals():
    with pytest.raises(ExpansionError, match="do not divide"):
        ring(72, 2, 5)


def test_ring_shift_map_is_the_rotation_template():
    dev = ring(72, 2, 24)
    fwd = dev.shift_map("L0", 1)
    assert fwd["S0"] == "S1" and fwd["S143"] == "S0"
    back = dev.shift_map("L0", -1)
    assert back["S0"] == "S143"
    assert len(fwd) == 144


def test_taller_ring_still_has_four_corners():
    dev = ring(10, 4, 0)
    assert len(dev.nodes) == 2 * 10 + 2 * 4 - 4 == 24
    assert len(dev.all_corners) == 4
    # with H > 2 no single segment contains a whole turn
    assert sum(1 for v in dev.corner_endpoints.values() if v == 2) == 0


# --------------------------------------------------------------------------- others


def test_cyclone_base_has_no_junction_on_the_rotation_path():
    arch = load(ARCH_DIR / "cyclone_base.arch.json")
    s = arch.device.summary()
    assert s["n_sites"] == 72  # m/2 traps for BB [[144,12,12]]
    assert s["n_junction_nodes"] == 0
    assert s["n_corners"] == 4
    assert s["degree_histogram"] == {2: 72}
    assert arch.device.total_capacity() == 72 * 4


def test_chain_has_no_loop_and_two_open_ends():
    arch = load(ARCH_DIR / "chain.arch.json")
    s = arch.device.summary()
    assert s["degree_histogram"] == {1: 2, 2: 70}
    assert s["n_junction_nodes"] == 0
    assert s["n_corners"] == 0
    assert arch.device.loops["P0"].closed is False
    with pytest.raises(ValueError, match="open"):
        arch.device.shift_map("P0", 1)


def test_grid_degrees_come_from_the_lattice():
    dev = grid(9, 9)
    s = dev.summary()
    assert s["n_sites"] == 2 * 9 * 9 - 9 - 9 == 144
    assert len([n for n in dev.nodes.values() if n.kind == "junction"]) == 81
    assert s["degree_histogram"][4] == 49  # interior X-junctions
    assert s["degree_histogram"][3] == 28  # boundary T-junctions
    # the 4 lattice corners are degree-2 bends and are found geometrically, with no loop
    assert dev.loops == {}
    assert dev.all_corners == {"J0_0", "J0_8", "J8_0", "J8_8"}


def test_chain_of_one_is_the_stationary_baseline():
    dev = chain(1)
    assert len(dev.nodes) == 1 and not dev.segments and not dev.loops


# --------------------------------------------------------------------------- schema


def test_schema_rejects_a_bad_document():
    doc = json.loads((ARCH_DIR / "ring144_24v.arch.json").read_text(encoding="utf-8"))
    doc["primitives"]["shuttle_segment"]["curve"][0]["table"] = "not_a_table"
    errors = validate_document(doc)
    assert any("not_a_table" in e for e in errors)
    with pytest.raises(ValidationError):
        check(doc)


def test_schema_rejects_an_unknown_key():
    doc = json.loads((ARCH_DIR / "chain.arch.json").read_text(encoding="utf-8"))
    doc["geomtery"] = doc.pop("geometry")
    errors = validate_document(doc)
    assert any("geomtery" in e for e in errors)


def test_schema_rejects_a_wrong_version():
    doc = json.loads((ARCH_DIR / "chain.arch.json").read_text(encoding="utf-8"))
    doc["schema_version"] = "0.1"
    assert any("schema_version" in e for e in validate_document(doc))


def test_dangling_segment_endpoint_is_caught():
    doc = json.loads((ARCH_DIR / "chain.arch.json").read_text(encoding="utf-8"))
    arch = Architecture.from_json(doc)
    expanded = arch.to_json(expanded=True)
    expanded["geometry"]["segments"][0]["ends"][1] = "nope"
    with pytest.raises(ExpansionError):
        Architecture.from_json(expanded)


# --------------------------------------------------------------------------- curves


def test_primitives_carry_curves_not_scalars():
    arch = load(ARCH_DIR / "ring144_24v.arch.json")
    curve = arch.primitives.curve("shuttle_segment")
    assert len(curve) >= 3
    assert {"qccdsim_jones", "transport_excitation"} <= set(curve.tables())
    fast = curve.pick(OperatingPointPolicy("qccdsim_jones", "fastest"))
    assert (fast.us, fast.quanta) == (5.0, 0.1)
    cool = curve.pick(OperatingPointPolicy("transport_excitation", "coolest"))
    assert (cool.us, cool.quanta) == (14.0, 0.1)


def test_junction_curve_is_keyed_by_degree():
    arch = load(ARCH_DIR / "ring144_24v.arch.json")
    dc = arch.primitives.degree_curve("junction_cross")
    assert dc.degrees() == (2, 3, 4)
    p = dc.get(3).pick(OperatingPointPolicy("qccdsim_jones", "fastest"))
    assert (p.us, p.quanta) == (100.0, 3.0)


def test_table_coverage_is_reported_not_hidden():
    """The policy falls back across tables; a two-table comparison needs to know."""
    arch = load(ARCH_DIR / "ring144_24v.arch.json")
    cov = arch.primitives.table_coverage("transport_excitation")
    assert cov["shuttle_segment"] is True
    assert cov["junction_cross"] is False  # that table has no junction figure


def test_dock_class_entails_split_and_merge_but_rotation_does_not():
    arch = load(ARCH_DIR / "ring144_24v.arch.json")
    assert arch.entails("dock") == ("split", "merge")
    assert arch.entails("undock") == ("split", "merge")
    assert arch.entails("rotate_cw") == ()


# ======================================================================================
# THE ELEMENT DOCS
# ======================================================================================
#
# `export_schema()` emits only type / enum / min / max / pattern / required -- there is no
# field documentation anywhere in the schema.  So the element menu's prose has to live
# SOMEWHERE, and the only honest home is beside the definitions it describes: a sentence
# written in JavaScript is a sentence that outlives the field it describes.
#
# The palette is generated in the browser from `export_schema()` + `export_consumers()`,
# so its key set is derivable here without a browser: four geometry stamps, one named
# record, one row, and one block per top-level group the consumer table names.


def _palette_types() -> set:
    from qccd.arch.schema import export_consumers
    heads = {f["path"].split(".")[0] for f in export_consumers()["fields"]}
    heads.discard("zone_types")                       # closed; covered by `zone_type`
    return {"site", "junction", "segment", "loop", "zone_type", "curve_point"} | heads


def test_element_docs_cover_palette():
    from qccd.arch.schema import ELEMENT_DOC, export_element_docs

    assert set(ELEMENT_DOC) == _palette_types(), (
        "an element the palette generates with no name and no blurb renders a blank line "
        "in the menu; one that no longer exists leaves a stale description behind")
    docs = export_element_docs()
    assert set(docs) == set(ELEMENT_DOC)
    for k, v in docs.items():
        assert v["name"] and v["name"][0].isupper(), k
        # a blurb has to say what the thing IS, not repeat its identifier
        assert len(v["blurb"]) > 40 and v["blurb"].endswith("."), k
