"""Machines built in layers (docs/PLAN-3d.md): planar machines one above another, joined
by risers.

What these tests hold:

* a layer is a `z`, a riser is a rail between two layers, and nothing declares either;
* layers may differ in shape; a riser is vertical between adjacent layers (R21);
* a riser is an ordinary shuttle: the same price as a rail (the user's decision, 2026-11-10);
* each layer has a broadcast of its own: no channel drives two layers, and R22 is one
  waveform a LAYER (the same decision);
* a planar machine is what it always was -- the same summary, the same page payload;
* the flat stage draws one panel a layer, and the 3-D stage puts every ion exactly where
  the flat stage's mark is within its panel (one position law);
* the vendored three.js is the file its manifest names, and has nothing in it a
  self-contained page may not carry.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd import Machine
from qccd.arch import DeviceBuilder
from qccd.arch.device import Device, ExpansionError
from qccd.arch.layers import stack
from qccd.cost.models import corrected_model
from qccd.verify.rules import (direction_label, geometry_violations, hop_label,
                               loop_node_set)
from qccd.viz import render as R

REPO = Path(__file__).resolve().parents[1]
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def by_rule(vs) -> dict:
    out: dict = {}
    for v in vs:
        out[v.rule] = out.get(v.rule, 0) + 1
    return out


@pytest.fixture(scope="module")
def grid_under_ring() -> Machine:
    """A 3x3 lattice under a ring of eight: two layers of different shape, one riser."""
    a = Machine.grid(3, 3, name="a")
    b = Machine.ring(8, 2, name="b", site_zone="trap")
    return Machine.layered([a, b], risers=[("A.J1_1", "B.S3")], name="grid_under_ring")


@pytest.fixture(scope="module")
def three_grids() -> Machine:
    """Three 3x3 lattices, the centre junctions joined: the middle one has six rails."""
    g = [Machine.grid(3, 3, name=f"g{i}") for i in range(3)]
    return Machine.layered(g, risers=[("A.J1_1", "B.J1_1"), ("B.J1_1", "C.J1_1")],
                           name="three_grids")


# ----------------------------------------------------------------------------- the model

def test_a_planar_machine_is_one_layer_and_says_nothing_new():
    m = Machine.grid(3, 3)
    d = m.arch.device
    assert d.dim == 2 and d.layers == (0.0,) and d.risers == ()
    assert all(d.layer_of(n) == 0 for n in d.nodes)
    assert "n_layers" not in d.summary() and "n_risers" not in d.summary()
    vm = R.build_view_model(m.arch, m.program("p").build(), m.run(m.program("p")).report.result,
                            corrected_model("qccdsim_jones"))
    assert "panels" not in vm["layout"] and "panels" not in vm["layout_true"]
    assert all("z" not in n for n in vm["arch"]["nodes"])
    assert all("riser" not in s for s in vm["arch"]["segments"])


def test_stack_puts_planar_machines_one_above_another(grid_under_ring):
    d = grid_under_ring.arch.device
    assert d.dim == 3 and d.layers == (0.0, 1.0)
    assert d.risers == ("R0",) and d.is_riser("R0") and not d.is_riser("A.E0_0h" if "A.E0_0h" in d.segments else next(s for s in d.segments if s != "R0"))
    # every id of a layer is prefixed with the layer's name, loops included
    assert all(n.startswith(("A.", "B.")) for n in d.nodes)
    assert set(d.loops) == {"B.L0"} and all(n.startswith("B.") for n in d.loops["B.L0"].nodes)
    assert {d.layer_of(n) for n in d.nodes if n.startswith("A.")} == {0}
    assert {d.layer_of(n) for n in d.nodes if n.startswith("B.")} == {1}
    # a riser is one rail more at each of its ends, and degree is what makes a junction (R18)
    assert d.degree("A.J1_1") == 5 and d.degree("B.S3") == 3
    assert d.summary()["n_layers"] == 2 and d.summary()["n_risers"] == 1


def test_layers_of_different_shape_are_aligned_on_their_first_riser(grid_under_ring):
    d = grid_under_ring.arch.device
    a, b = d.nodes["A.J1_1"].pos, d.nodes["B.S3"].pos
    assert a[:2] == b[:2] and (a[2], b[2]) == (0.0, 1.0)      # one above the other
    assert geometry_violations(grid_under_ring.arch) == []
    assert grid_under_ring.violations() == []


def test_the_document_round_trips(grid_under_ring, tmp_path):
    doc = grid_under_ring.arch.to_json()
    again = type(grid_under_ring.arch).from_json(doc)
    assert again.to_json() == doc
    grid_under_ring.save(tmp_path / "m.arch.json", expanded=True)
    loaded = Machine.load(tmp_path / "m.arch.json")
    assert loaded.arch.device.dim == 3 and loaded.arch.device.risers == ("R0",)


def test_a_planar_document_is_unchanged_on_a_round_trip():
    """Every shipped machine is planar, stays planar, and writes no third coordinate."""
    for path in sorted((REPO / "arch").glob("*.arch.json")):
        arch = Machine.load(path).arch
        assert arch.device.dim == 2 and arch.device.layers == (0.0,), path.name
        doc = arch.to_json(expanded=True)
        assert all(len(n["pos"]) == 2 for n in doc["geometry"]["nodes"]), path.name
        assert type(arch).from_json(doc).to_json(expanded=True) == doc, path.name


def test_a_machine_is_planar_or_layered_never_both():
    d = DeviceBuilder("explicit")
    d.site("S0", 0.0, 0.0, 1.0, zone="data").site("S1", 1.0, 0.0, zone="data")
    d.segment("E0", "S0", "S1")
    with pytest.raises(ExpansionError) as e:
        Machine.blank_device(d.build(), name="mix", zones=["data"])
    assert "not both" in str(e.value)


def test_one_coordinate_and_four_are_still_refused():
    for pos in ((0.0,), (0.0, 0.0, 0.0, 0.0)):
        with pytest.raises(ExpansionError) as e:
            DeviceBuilder("explicit").site("S0", *pos, zone="data")
        assert "two coordinates (x, y) or three (x, y, z)" in str(e.value)


def test_a_loop_stays_in_one_layer():
    d = DeviceBuilder("explicit")
    d.site("A", 0.0, 0.0, 0.0, zone="data").site("Bb", 1.0, 0.0, 0.0, zone="data")
    d.site("Cc", 1.0, 0.0, 1.0, zone="data")
    d.segment("E0", "A", "Bb").segment("E1", "Bb", "Cc")
    d.loop("L0", ["A", "Bb", "Cc"], closed=False, kind="path")
    with pytest.raises(ExpansionError) as e:
        Machine.blank_device(d.build(), name="lz", zones=["data"])
    assert "stays in one layer" in str(e.value)


def test_moving_a_node_keeps_it_in_its_layer(grid_under_ring):
    m = Machine(grid_under_ring.arch)
    m.move_site("A.T0_0h", 0.4, 0.0)
    assert m.arch.device.nodes["A.T0_0h"].pos == (0.4, 0.0, 0.0)


# ----------------------------------------------------------------------------- geometry

def test_an_x_junction_with_a_riser_up_and_one_down_has_six_rails(three_grids):
    d = three_grids.arch.device
    assert d.degree("B.J1_1") == 6 and d.degree("A.J1_1") == 5 and d.degree("C.J1_1") == 5
    assert d.summary()["degree_histogram"][6] == 1
    assert geometry_violations(three_grids.arch) == []          # R19, R20 and R21 all hold


def test_a_riser_stands_at_ninety_degrees_to_its_layer(three_grids):
    from qccd.verify.rules import rail_angle_deg
    d = three_grids.arch.device
    rails = d.incidence["B.J1_1"]
    risers = [s for s in rails if d.is_riser(s)]
    flat = [s for s in rails if not d.is_riser(s)]
    assert len(risers) == 2 and len(flat) == 4
    assert all(rail_angle_deg(d, "B.J1_1", r, f) == 90.0 for r in risers for f in flat)
    assert rail_angle_deg(d, "B.J1_1", risers[0], risers[1]) == 180.0


def test_a_riser_that_leans_is_refused_by_stack_and_reported_by_R21():
    a, b = Machine.grid(2, 2, name="a"), Machine.grid(2, 2, name="b")
    with pytest.raises(ExpansionError) as e:
        Machine.layered([a, b], risers=[("A.J0_0", "B.J0_0"), ("A.J1_1", "B.J0_1")])
    assert "not vertical" in str(e.value)
    m = Machine.layered([a, b], risers=[("A.J0_0", "B.J0_0"), ("A.J1_1", "B.J0_1")],
                        strict=False)
    v = [x for x in geometry_violations(m.arch) if x.rule == "R21"]
    assert len(v) == 1 and "not vertical" in v[0].message and "R1" in v[0].message


def test_a_riser_that_skips_a_layer_breaks_R21():
    g = [Machine.grid(2, 2, name=f"g{i}") for i in range(3)]
    with pytest.raises(ExpansionError):
        Machine.layered(g, risers=[("A.J0_0", "C.J0_0")])
    m = Machine.layered(g, risers=[("A.J0_0", "C.J0_0")], strict=False)
    v = [x for x in geometry_violations(m.arch) if x.rule == "R21"]
    assert len(v) == 1 and "not adjacent" in v[0].message


def test_one_layer_over_another_is_not_a_crossing():
    """Two identical lattices: every node of B is over a node of A, every rail over a rail.
    Projected to the plane that is hundreds of coincidences; in layers it is none."""
    a, b = Machine.grid(3, 3, name="a"), Machine.grid(3, 3, name="b")
    assert geometry_violations(Machine.layered([a, b], risers=[("A.J0_0", "B.J0_0")]).arch) == []
    # B shifted half a step: its nodes now stand over the MIDDLES of A's rails and its
    # rails run across A's.  None of that is a fault -- they are in different planes
    m = Machine.layered([a, b], risers=[], offsets=[(0, 0), (0.5, 0.0)])
    assert geometry_violations(m.arch) == []


def test_a_crossing_inside_a_layer_is_still_a_crossing():
    d = DeviceBuilder("explicit")
    for nid, x, y in (("p0", 0, 0), ("p1", 2, 2), ("p2", 0, 2), ("p3", 2, 0)):
        d.site(nid, float(x), float(y), 1.0, zone="data")
    d.site("q0", 0.0, 0.0, 0.0, zone="data").site("q1", 2.0, 0.0, 0.0, zone="data")
    d.segment("x0", "p0", "p1").segment("x1", "p2", "p3").segment("low", "q0", "q1")
    m = Machine.blank_device(d.build(), name="x", zones=["data"])
    v = by_rule(geometry_violations(m.arch))
    assert v.get("R21") == 1


def test_the_rule_language_agrees_on_a_layered_machine(three_grids):
    from qccd.ruleset import check_programme, load
    p = three_grids.program("nothing")
    p.init({"q0": "A.T0_0h"})
    rep = check_programme(load("standard"), p.build(), three_grids.arch, corrected_model())
    verdict = {r.id: r.verdict for r in rep.results}
    assert verdict["R19"] == verdict["R20"] == verdict["R21"] == "holds", verdict


# ----------------------------------------------------------------------------- a riser is a shuttle

def test_a_riser_hop_is_labelled_by_z(grid_under_ring):
    d = grid_under_ring.arch.device
    on = loop_node_set(d)
    assert hop_label(d, on, "A.J1_1", "B.S3", d.segments["R0"]) == "+z"
    assert hop_label(d, on, "B.S3", "A.J1_1", d.segments["R0"]) == "-z"
    assert direction_label(0.0, 0.0, 1.0) == "+z" and direction_label(0.0, 0.0, -2.0) == "-z"
    assert direction_label(1.0, 0.0) == "+x" and direction_label(1.0, 0.0, 0.0) == "+x"


def test_a_riser_is_priced_as_an_ordinary_shuttle():
    """Two chains of three, their middles joined.  A hop along a rail into a middle and a
    hop along the riser into the other middle enter nodes of the same degree: they cost
    the same, take the same time and give the same heat."""
    a, b = Machine.chain(3, name="a"), Machine.chain(3, name="b")
    m = Machine.layered([a, b], risers=[("A.C1", "B.C1")], name="pair")
    d = m.arch.device
    assert d.degree("A.C1") == 3 and d.degree("B.C1") == 3

    def one(src, dst):
        p = m.program("hop")
        p.init({"q": src})
        p.shuttle("q", [src, dst])
        r = m.run(p)
        return r.cost, r.runtime_ms, r.peak_quanta

    assert one("A.C0", "A.C1") == one("B.C1", "A.C1")


# ----------------------------------------------------------------------------- broadcast

def test_each_layer_has_a_broadcast_of_its_own(grid_under_ring):
    d = grid_under_ring.arch.device
    plane = grid_under_ring.arch.control_plane
    assert plane.declared
    for g in plane.groups:
        assert len({d.layer_of(s) for s in g.sites}) == 1, g.id
    names = {g.id.split(".", 1)[0] for g in plane.groups}
    assert names == {"A", "B"}
    # the declaration is instantiated once a layer: twice the channels of one layer alone
    one = Machine.ring(8, 2, site_zone="trap").arch.control_plane
    assert plane.n_shared_channels == 2 * one.n_shared_channels


def test_one_cycle_is_one_waveform_a_layer(grid_under_ring):
    m = grid_under_ring

    def r22(build) -> list[str]:
        p = m.program("p")
        build(p)
        return [v.message for v in m.run(p).violations if v.rule == "R22"]

    def two_layers(p):                      # A moves +x while B steps along its ring's rail
        p.init({"q0": "A.T0_0h", "r0": "B.S0"})
        with p.cycle("shuttle") as c:
            c.move("q0", "A.T0_0h", "A.J1_0")
            c.move("r0", "B.S0", "B.S1")

    def one_layer(p):                       # two unlike motions inside layer A
        p.init({"q0": "A.T0_0h", "q1": "A.T0_1v"})
        with p.cycle("shuttle") as c:
            c.move("q0", "A.T0_0h", "A.J1_0")
            c.move("q1", "A.T0_1v", "A.J0_1")

    def riser_and_rail(p):                  # an ion on the riser is in BOTH layers' cycles
        d = m.arch.device
        p.init({"q0": "A.T0_1h", "r0": "B.S5"})
        with p.cycle("shuttle") as c:
            c.move("q0", "A.T0_1h", "B.S3",
                   via=[d.segment_between("A.T0_1h", "A.J1_1").id, "R0"])
            c.move("r0", "B.S5", "B.S6")

    assert r22(two_layers) == []
    got = r22(one_layer)
    assert len(got) == 1 and got[0].startswith("in layer A, 2 ions move in 2 different ways")
    got = r22(riser_and_rail)
    assert len(got) == 1 and got[0].startswith("in layer B")


def test_a_programme_through_a_riser_passes_every_rule(grid_under_ring):
    m = grid_under_ring
    p = m.program("up_and_round")
    p.init({"q0": "A.T0_1h", "q1": "A.T1_0v", "r0": "B.S0", "r1": "B.S5"})
    p.shuttle("q0", ["A.T0_1h", "A.J1_1", "B.S3"])
    p.rotate(+2, loop="B.L0")
    p.rotate(-2, loop="B.L0")
    p.shuttle("q0", ["B.S3", "A.J1_1", "A.T1_1h"])
    r = m.run(p)
    assert r.rules_failed == [], [str(v) for v in r.violations]


# ----------------------------------------------------------------------------- the compiler

QCCDC = REPO / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"


@pytest.mark.skipif(not QCCDC.is_file(), reason="needs this checkout's compiler build")
def test_a_circuit_compiles_onto_two_layers_and_every_rule_holds(tmp_path):
    """Compilation is the same: a layered machine is a graph to the compiler.  Two lattices
    of different shape, two risers, the Steane round."""
    from qccd.ir.tsir import TSIR
    from qccd.ruleset.compile import compile_under

    a = Machine.grid(3, 3, name="a", template="grid9x9")
    b = Machine.grid(4, 2, name="b", template="grid9x9")
    m = Machine.layered([a, b], risers=[("A.J0_0", "B.J0_0"), ("A.J2_1", "B.J2_1")],
                        name="lattice_under_lattice")
    arch = tmp_path / "m.arch.json"
    m.save(arch, expanded=True)
    out = compile_under(REPO / "Compiler" / "examples" / "steane_esm.qasm", arch, "standard",
                        tmp_path / "out", qccdc=QCCDC, semantics=False)
    assert out["compiled"], out["log"][-600:]
    failing = [r.id for r in out["report"].results if r.verdict == "fails"]
    assert failing == [], failing
    prog = TSIR.load(out["programme"])
    d = m.arch.device
    used = {d.layer_of(x) for i in prog.instructions for p in (i.participants or ())
            for x in (p.src, p.dst)}
    assert used == {0, 1}, "the programme should use both layers"
    assert m.run(prog).rules_failed == []


# ----------------------------------------------------------------------------- the page

def _page(m: Machine, tmp_path, name="page.html", **kw) -> Path:
    p = m.program("up_and_round")
    p.init({"q0": "A.T0_1h", "q1": "A.T1_0v", "r0": "B.S0", "r1": "B.S5"})
    p.shuttle("q0", ["A.T0_1h", "A.J1_1", "B.S3"])
    p.rotate(+2, loop="B.L0")
    p.rotate(-2, loop="B.L0")
    p.shuttle("q0", ["B.S3", "A.J1_1", "A.T1_1h"])
    return m.render(p, tmp_path / name, **kw)


def test_the_flat_stage_draws_one_panel_a_layer(grid_under_ring):
    m = grid_under_ring
    p = m.program("p")
    p.init({"q0": "A.T0_1h"})
    vm = R.build_view_model(m.arch, p.build(), m.run(p).report.result,
                            corrected_model("qccdsim_jones"))
    panels = vm["layout"]["panels"]
    assert len(panels) == 2 and panels[0] == [0.0, 0.0] and panels[1] != [0.0, 0.0]
    assert vm["layout_true"]["panels"] == panels
    d = m.arch.device
    for n in vm["arch"]["nodes"]:
        true = d.nodes[n["id"]].pos
        assert n["z"] == d.layer_of(n["id"])
        assert (n["x"], n["y"]) == (true[0] + panels[n["z"]][0], true[1] + panels[n["z"]][1])
    # no two nodes are drawn at one place, though a riser's two ends share an (x, y)
    drawn = [(n["x"], n["y"]) for n in vm["arch"]["nodes"]]
    assert len(set(drawn)) == len(drawn)
    assert [s["id"] for s in vm["arch"]["segments"] if s.get("riser")] == ["R0"]


def test_a_page_carries_the_3d_stage_only_where_asked(grid_under_ring, tmp_path):
    layered = _page(grid_under_ring, tmp_path).read_text(encoding="utf-8")
    assert f'<meta name="qccd-page-3d" content="{R.stage3d_stamp()}">' in layered
    assert "three.js 0.187.0" in layered and "THE 3D STAGE" in layered
    flat = Machine.grid(3, 3)
    q = flat.program("p")
    q.init({"q0": "T0_0h"})
    plain = flat.render(q, tmp_path / "flat.html").read_text(encoding="utf-8")
    assert 'name="qccd-page-3d"' not in plain and "three.js 0.187.0" not in plain
    asked = flat.render(q, tmp_path / "flat3d.html", stage3d="always").read_text(encoding="utf-8")
    assert 'name="qccd-page-3d"' in asked and "three.js 0.187.0" in asked
    never = _page(grid_under_ring, tmp_path, "never.html", stage3d="never").read_text(encoding="utf-8")
    assert 'name="qccd-page-3d"' not in never
    assert len(layered) - len(never) < 700_000, "the 3-D stage costs about 0.6 MB a page"
    with pytest.raises(ValueError):
        _page(grid_under_ring, tmp_path, "bad.html", stage3d="sometimes")


def test_the_page_stamp_does_not_move_with_the_3d_files():
    """`page_stamp` covers the template and the six files every page inlines; the 3-D
    stage's two files have a stamp of their own, so changing them rebuilds only the pages
    that carry a 3-D stage."""
    assert set(R.STAGE3D_JS).isdisjoint(R.ENGINE_JS + R.EDITOR_JS)
    assert R.stage3d_stamp() != R.page_stamp()


# ----------------------------------------------------------------------------- one position law

@requires_node
def test_the_3d_stage_puts_every_ion_where_the_flat_stage_has_it(grid_under_ring, tmp_path):
    page = _page(grid_under_ring, tmp_path)
    r = subprocess.run(["node", str(REPO / "tests" / "stage3d.mjs"), str(page)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout)
    assert got["three"] and got["stage"]
    assert not got["available"], "the shim has no canvas: the renderer must not start here"
    d = got["describe"]
    # the census: every node is a site or a junction, every flat rail is drawn, every riser is
    assert d["layers"] == 2 and d["sites"] + d["juncs"] == d["nodes"]
    assert d["railIds"] == d["flat"] and d["risers"] == d["segments"] - d["flat"] == 1
    px, gap, lift = got["px"], got["gap"], got["lift"]
    assert gap > 0 and lift > 0 and len(got["checks"]) > 50
    on_riser = 0
    for c in got["checks"]:
        p = px[c["panel"]]
        # ONE POSITION LAW: in the stack, an ion's place in its layer is its SVG mark's
        # place in its panel -- the same numbers, with the panel slid back and y turned over
        assert c["at"][0] == c["cx"] - p["x"], c
        assert c["at"][1] == -(c["cy"] - p["y"]), c
        assert c["at"][2] == c["zf"] * gap + lift, c
        # and unfolded and flat, the 3-D stage IS the SVG picture
        assert c["flat"][0] == c["cx"] and c["flat"][1] == -c["cy"] and c["flat"][2] == lift, c
        if c["riser"]:
            on_riser += 1
            assert 0.0 <= c["zf"] <= 1.0
        else:
            assert c["zf"] == int(c["zf"])
        if c["phase"] == 1:
            # a frame that has finished leaves every ion in the layer of the node it is on
            assert c["zf"] == c["nodeLayer"], c
    assert on_riser >= 4, "the programme takes an ion up the riser and back: it must be seen there"
    between = [c["zf"] for c in got["checks"] if c["riser"] and 0.0 < c["zf"] < 1.0]
    assert between, "an ion part way up the riser is part way between the layers"


# ----------------------------------------------------------------------------- the vendored file

def test_the_vendored_three_js_is_the_file_its_manifest_names():
    vendor = REPO / "qccd" / "viz" / "vendor"
    man = json.loads((vendor / "three.qccd.json").read_text(encoding="utf-8"))
    raw = (vendor / man["file"]).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == man["sha256"], (
        "qccd/viz/vendor/three.qccd.min.js is not the file tools/vendor_three.mjs wrote; "
        "regenerate it with that script, never by hand")
    assert len(raw) == man["bytes"]
    text = raw.decode("ascii")                       # ASCII only: no encoding to get wrong
    assert f"three.js {man['three']}" in text[:200] and "SPDX-License-Identifier: MIT" in text[:300]
    assert "MIT License" in (vendor / "THREE.LICENSE").read_text(encoding="utf-8")
    # what a self-contained page may not carry, and what the workspace's content security
    # policy would refuse to run (`script-src 'unsafe-inline'`: no eval, no worker)
    for bad in R.FORBIDDEN + ("eval(", "new Function", "Worker(", "importScripts", "</script"):
        assert bad not in text, f"the vendored three.js contains {bad!r}"
    assert f"globalThis.{man['global']}" in text or f"{man['global']}=" in text


def test_the_3d_stage_has_nothing_a_page_may_not_carry():
    src = (REPO / "qccd" / "viz" / "js" / "stage3d.js").read_text(encoding="utf-8")
    for bad in R.FORBIDDEN + ("eval(", "new Function"):
        assert bad not in src, f"js/stage3d.js contains {bad!r}"


# ----------------------------------------------------------------------------- the browser mirror

_PANELS_JS = r"""
const fs = require('fs');
const [engine, casesPath] = process.argv.slice(2);
new Function(fs.readFileSync(engine, 'utf8') + '; globalThis.Q = QCCD;')();
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = cases.map((c) => {
  const d = Q.drawnOf(c.wire), lay = Q.layersOf(c.wire);
  return { panels: Q.panelOffsets(c.xs, c.ys, c.n), drawn: d.nodes, flat: d.segments.map((s) => s.id),
           riser: Object.keys(d.riser), layers: lay ? lay.z : null, layout: Q.layoutOf(c.wire) };
});
console.log(JSON.stringify(out));
"""


def _layered_zoo() -> list[Machine]:
    """Layered machines of unlike shapes: wide under square, square under wide, three deep."""
    g = lambda a, b, n: Machine.grid(a, b, name=n)
    ring = Machine.ring(8, 2, name="r", site_zone="trap")
    wide = Machine.ring(24, 2, name="w", site_zone="trap")
    return [
        Machine.layered([g(3, 3, "a"), ring], risers=[("A.J1_1", "B.S3")]),
        Machine.layered([wide, g(2, 2, "b")], risers=[("A.S5", "B.J0_0")]),
        Machine.layered([g(3, 3, "a"), g(4, 2, "b"), g(2, 5, "c")],
                        risers=[("A.J0_0", "B.J0_0"), ("B.J1_1", "C.J1_1")]),
        Machine.layered([Machine.chain(4, name="c"), Machine.chain(4, name="d")],
                        risers=[("A.C1", "B.C1")]),
    ]


@requires_node
def test_the_browser_lays_a_layered_machine_out_exactly_as_python_does(tmp_path):
    """`layout.panel_offsets` against `engine.js::panelOffsets`, the drawn nodes against
    `drawnOf`, and the whole layout against `layoutOf`: equal, not close."""
    from qccd.arch.edit import device_to_wire
    from qccd.viz.layout import compute_layout, panel_offsets

    cases, want = [], []
    for m in _layered_zoo():
        d = m.arch.device
        xs = [float(n.pos[0]) for n in d.nodes.values()]
        ys = [float(n.pos[1]) for n in d.nodes.values()]
        panels = panel_offsets(xs, ys, len(d.layers))
        drawn = [{"id": nid, "x": float(n.pos[0]) + panels[d.layer_of(nid)][0],
                  "y": float(n.pos[1]) + panels[d.layer_of(nid)][1], "z": d.layer_of(nid)}
                 for nid, n in d.nodes.items()]
        flat = [{"id": s.id, "a": s.ends[0], "b": s.ends[1]}
                for s in d.segments.values() if not d.is_riser(s.id)]
        layout = compute_layout(drawn, flat)
        layout["panels"] = panels
        cases.append({"wire": device_to_wire(d), "xs": xs, "ys": ys, "n": len(d.layers)})
        want.append({"panels": panels, "drawn": drawn, "flat": [s["id"] for s in flat],
                     "riser": list(d.risers), "layers": list(d.layers), "layout": layout})
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    (tmp_path / "run.js").write_text(_PANELS_JS, encoding="utf-8")
    r = subprocess.run(["node", str(tmp_path / "run.js"), str(REPO / "qccd" / "viz" / "engine.js"),
                        str(tmp_path / "cases.json")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout)
    assert len(got) == len(want) and any(len(w["panels"]) == 3 for w in want)
    for g_, w in zip(got, want):
        assert g_["panels"] == w["panels"]
        assert g_["drawn"] == w["drawn"]
        assert g_["flat"] == w["flat"] and g_["riser"] == w["riser"] and g_["layers"] == w["layers"]
        assert g_["layout"] == json.loads(json.dumps(w["layout"]))
    # and both ways of setting panels out are exercised: across the page, and down it
    ways = {("across" if w["panels"][1][1] == 0.0 else "down") for w in want}
    assert ways == {"across", "down"}, ways


def test_a_website_links_the_3d_stage_where_a_page_would_inline_it(grid_under_ring, tmp_path):
    """`link_stage3d`: a site serves the stage's two files once and every page loads them.
    A planar page gains two script tags and the line that shows the switch once they have
    loaded; a page that carries the stage, and one with no place for it, come back as they
    are; and the page's own code is untouched, so its stamp is the template's."""
    planar = Machine.grid(3, 3, name="flat")
    prog = planar.program("p").build() if hasattr(planar, "program") else None
    page = tmp_path / "flat.html"
    planar.render(prog, page, source=None)
    text = page.read_text(encoding="utf-8")
    assert '<meta name="qccd-page-3d"' not in text, "a planar page carries no stage of its own"
    linked = R.link_stage3d(text, "../static/3d/abc/")
    names = list(R.stage3d_files())
    assert names == ["three.qccd.min.js", "stage3d.js"]
    tags = [f'<script src="../static/3d/abc/{name}"></script>' for name in names]
    assert all(linked.count(tag) == 1 for tag in tags)
    assert text.rindex("</body>") <= linked.index(tags[0]) < linked.index(tags[1]) < linked.rindex("</body>")
    assert "if(globalThis.STAGE3D&&globalThis.QCCDTHREE)" in linked
    assert linked.replace(linked[text.rindex("</body>"):linked.rindex("</body>")], "") == text,         "nothing of the page changes: the link is added before </body> and that is all"
    assert f'content="{R.page_stamp()}"' in linked
    # a page that carries the stage already
    layered = tmp_path / "layered.html"
    grid_under_ring.render(grid_under_ring.program("p").build(), layered, source=None)
    inline = layered.read_text(encoding="utf-8")
    assert '<meta name="qccd-page-3d"' in inline and R.link_stage3d(inline, "x/") == inline
    # a page written before the template had a place for the stage
    old = text.replace('id="stage3d"', 'id="no-stage"')
    assert R.link_stage3d(old, "x/") == old

