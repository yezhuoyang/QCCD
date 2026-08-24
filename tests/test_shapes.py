"""Shapes and technology: integers that stay integers, and numbers that keep their source.

Two things are proved here and they are the whole of S2.

**The arithmetic is exact.**  A layout has to survive a round trip through a foreign tool
and come back bit for bit, which is only possible if nothing rounds.  So `Poly` refuses a
float, the shoelace is an integer shoelace, quarter-turn placement is exact and its
four-fold composition is the identity *by equality, not by tolerance*, and the rectangle
union is checked against a brute-force grid oracle rather than against itself.

**Every number keeps its provenance, and the authored ones say so.**  `Dim` has no
one-argument form.  The shipped preset is checked dimension by dimension: each has a
non-empty source, each source beginning `declared:` is counted, and the count is asserted
to be **zero**.  Every dimension is either a page reference into 2201.12579 or a
derivation from ones that are.  The marker still has its own test, because a guard nothing
triggers is a guard nobody has tested.

The derived dimensions are checked against what they are derived *from*, so the preset
cannot drift internally: `dc_setback` must equal `w_g/2 + w_rf + gap`, `dc_centre_width`
must equal `w_g - 2*gap` (and 31.5 um, which the paper prints), `nm_per_unit_x` must equal
three `dc_pitch`es, `nm_per_unit_y` must equal `min_axis_pitch`, and `rail_end_extension`
must agree with the ion height `qccd/phys/field.py` computes from the same two RF widths.
"""

from __future__ import annotations

import io
import json
import random
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.phys.field import strip_null_height  # noqa: E402
from qccd.phys.shapes import (  # noqa: E402
    Cell,
    Inst,
    Layout,
    Poly,
    Refusal,
    Violation,
    min_gap_violations,
    min_width_violations,
    union_rects,
)
from qccd.phys.tech import (  # noqa: E402
    PURPOSES,
    Dim,
    Layer,
    Technology,
    load_technology,
    preset_names,
)

PRESET = "eth_junction_2201.12579"


@pytest.fixture(scope="module")
def tech() -> Technology:
    return load_technology(PRESET)


def _rect(x0, y0, x1, y1, *, layer="RF", role="rail", net="RF", owner="o"):
    return Poly.rect(layer, x0, y0, x1, y1, role=role, net=net, owner=owner)


# ------------------------------------------------------------- no floats, anywhere

@pytest.mark.parametrize("module", ["qccd/phys/shapes.py", "qccd/phys/tech.py"])
def test_no_float_literal_appears_in_the_integer_modules(module):
    """Tokenised, not grepped: a comment saying "0.83h" is fine, a `0.83` is not.

    This is a cheap guard on the property the whole GDS round trip rests on.  It also
    catches `1e3`, which a decimal-point grep would miss.
    """
    src = (ROOT / module).read_text(encoding="utf-8")
    bad = [(t.start[0], t.string)
           for t in tokenize.generate_tokens(io.StringIO(src).readline)
           if t.type == tokenize.NUMBER and ("." in t.string or "e" in t.string.lower())]
    assert not bad, f"{module} has float literals at {bad}"


@pytest.mark.parametrize("value", [1.0, 0.5, "5", None, True])
def test_a_polygon_refuses_anything_that_is_not_an_int(value):
    """`True` included: bool is an int subclass, and a coordinate of True is a bug."""
    with pytest.raises(TypeError, match="int in nanometres"):
        Poly.rect("RF", 0, 0, value, 10, role="rail", net="RF", owner="o")


# ------------------------------------------------------------------ exact geometry

def test_the_shoelace_is_an_integer_and_the_area_is_exact():
    p = _rect(-99500, -41500, 99500, 41500)
    assert isinstance(p.twice_signed_area_nm2(), int)
    assert p.twice_signed_area_nm2() > 0, "Poly.rect must wind counter-clockwise"
    assert p.area_nm2() == 199000 * 83000
    assert p.is_rect() and p.is_rectilinear()


def test_an_odd_doubled_area_is_refused_rather_than_halved():
    """A triangle on the integer grid has a half-integer area; rounding it would lie."""
    tri = Poly("RF", (0, 0, 3, 0, 0, 1), "probe", "RF", "t")
    assert tri.twice_signed_area_nm2() == 3
    with pytest.raises(ValueError, match="odd doubled area"):
        tri.area_nm2()


@pytest.mark.parametrize("quarter", [0, 1, 2, 3])
def test_a_quarter_turn_is_exact_and_area_preserving(quarter):
    p = _rect(20750, -300000, 120250, 300000)
    q = p.placed(7, -11, quarter)
    assert all(isinstance(v, int) for v in q.xy)
    assert q.area_nm2() == p.area_nm2()


def test_four_quarter_turns_are_the_identity_by_equality_not_by_tolerance():
    """The reason placement has no trigonometry: this must be `==`, not `approx`."""
    p = _rect(-3, -5, 7, 11)
    q = p
    for _ in range(4):
        q = q.placed(0, 0, 1)
    assert q.xy == p.xy


def test_placement_is_rotate_then_translate_in_that_order():
    p = _rect(0, 0, 10, 20)
    got = p.placed(100, 200, 1)
    # rotate by +90: (x, y) -> (-y, x); the rectangle 0..10 x 0..20 becomes -20..0 x 0..10
    assert got.bbox() == (100 - 20, 200 + 0, 100 + 0, 200 + 10)


@pytest.mark.parametrize("bad", [(10, 0, 0, 10), (0, 10, 10, 0), (0, 0, 0, 10)])
def test_a_reversed_or_zero_area_rectangle_is_a_builder_bug(bad):
    with pytest.raises(ValueError, match="builder bug"):
        _rect(*bad)


@pytest.mark.parametrize("quarter", [4, -1, 0.0, "1", True])
def test_only_the_four_quarter_turns_are_placeable(quarter):
    with pytest.raises(ValueError, match="quarter"):
        Inst("c", 0, 0, quarter)


# -------------------------------------------------------------- the rectangle union

def _grid_area(boxes):
    """Brute force: mark every unit cell of a compressed grid, then sum its area."""
    xs = sorted({v for b in boxes for v in (b[0], b[2])})
    ys = sorted({v for b in boxes for v in (b[1], b[3])})
    total = 0
    for i, (x0, x1) in enumerate(zip(xs, xs[1:])):
        for y0, y1 in zip(ys, ys[1:]):
            if any(b[0] <= x0 and b[2] >= x1 and b[1] <= y0 and b[3] >= y1 for b in boxes):
                total += (x1 - x0) * (y1 - y0)
    return total


def _disjoint(rects):
    for i in range(len(rects)):
        ax0, ay0, ax1, ay1 = rects[i]
        for j in range(i + 1, len(rects)):
            bx0, by0, bx1, by1 = rects[j]
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                return False
    return True


@pytest.mark.parametrize("boxes,area", [
    ([(0, 0, 10, 10)], 100),
    ([(0, 0, 10, 10), (0, 0, 10, 10)], 100),
    ([(0, 0, 10, 10), (5, 5, 15, 15)], 175),
    ([(0, 0, 10, 2), (0, 0, 2, 10)], 36),
    ([(0, 0, 10, 10), (20, 0, 30, 10)], 200),
    ([(0, 0, 10, 10), (10, 0, 20, 10)], 200),
])
def test_the_union_of_named_rectangles_is_exact(boxes, area):
    out = union_rects(boxes)
    assert sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in out) == area
    assert _disjoint(out)


def test_the_union_matches_a_brute_force_grid_over_random_cases():
    """A real differential: a sweep-line against a cell-marking oracle, on integers.

    Sixty random cases with heavy overlap, checked three ways -- same area, disjoint
    output, and the same covered cells as the oracle.  Nothing here is a tolerance.
    """
    rng = random.Random(20260823)
    for case in range(60):
        n = rng.randint(1, 7)
        boxes = []
        for _ in range(n):
            x0, y0 = rng.randint(-6, 6), rng.randint(-6, 6)
            boxes.append((x0, y0, x0 + rng.randint(1, 7), y0 + rng.randint(1, 7)))
        out = union_rects(boxes)
        assert _disjoint(out), (case, boxes, out)
        got = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in out)
        assert got == _grid_area(boxes), (case, boxes, out)
        # and the SAME cells, not merely the same total
        assert _grid_area(list(out)) == got
        for x0, y0, x1, y1 in out:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            assert any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


def test_the_union_coalesces_slabs_instead_of_returning_one_per_cut():
    """Without coalescing a plain rail comes back in pieces -- correct, and useless."""
    rail = [(0, 0, 100, 10), (100, 0, 200, 10), (200, 0, 300, 10)]
    assert union_rects(rail) == ((0, 0, 300, 10),)


def test_the_union_merges_intervals_that_only_touch():
    """Abutting shapes are one shape, and no area test can tell you that.

    Two rectangles stacked edge to edge have the same total area whether the union
    reports them merged or separate, and they stay disjoint either way -- so the random
    grid oracle above is blind to it.  Only the output shape says whether `y0 <= prev_y1`
    was written as `<`, and a DC pad column that abuts the next one is exactly the case a
    trap layout hits.
    """
    assert union_rects([(0, 0, 10, 5), (0, 5, 10, 10)]) == ((0, 0, 10, 10),)
    assert union_rects([(0, 0, 10, 5), (0, 5, 10, 10), (0, 10, 10, 30)]) == (
        (0, 0, 10, 30),)
    # a one-nanometre sliver between them is NOT a merge, and must survive as two
    assert union_rects([(0, 0, 10, 5), (0, 6, 10, 10)]) == ((0, 0, 10, 5), (0, 6, 10, 10))


def test_the_union_of_nothing_is_nothing():
    assert union_rects([]) == ()


# --------------------------------------------------------------------- design rules

def test_min_width_finds_a_pad_tiled_too_thin(tech):
    ok = _rect(0, 0, 49750, 70000, layer="DC", role="dc_pad", net="DC0", owner="wide")
    thin = _rect(0, 100000, 3000, 170000, layer="DC", role="dc_pad", net="DC1",
                 owner="thin")
    got = min_width_violations([ok, thin], tech)
    assert [v.owners for v in got] == [("thin",)]
    assert got[0].measured_nm == 3000 and got[0].required_nm == 5000


def test_min_gap_skips_the_same_net_because_overlap_there_is_a_merge(tech):
    """The degree-4 case: two perpendicular rails on the RF net, crossing."""
    across = _rect(-200000, 20750, 200000, 120250, net="RF", owner="ns")
    down = _rect(20750, -200000, 120250, 200000, net="RF", owner="ew")
    assert min_gap_violations([across, down], tech) == ()
    # anti-vacuity: the same two shapes on different nets are an overlap, and the gap
    # check must not report THAT as a gap either -- overlap is a separate finding
    other = Poly(down.layer, down.xy, down.role, "DC0", down.owner)
    assert min_gap_violations([across, other], tech) == ()
    # ...whereas two nearly-touching shapes on different nets do fire, so the checker
    # is live and the same-net skip above is a decision rather than an absence
    near = _rect(200001, 20750, 300000, 120250, net="DC0", owner="near")
    assert [v.owners for v in min_gap_violations([across, near], tech)] == [("ns", "near")]
    # and the union does the merging rather than the checker pretending it away
    lay = Layout(tech, {"c": Cell("c", (across, down))}, (Inst("c"),))
    merged = lay.union_by_net()["RF"]
    assert _disjoint(merged)
    assert sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in merged) < (
        across.area_nm2() + down.area_nm2()), "the crossing was counted twice"


def test_two_same_net_shapes_a_sliver_apart_are_skipped_by_default_and_found_on_request(
        tech):
    """The same-net skip has to be a *decision*, provable in both directions.

    The overlapping case above cannot show that: overlapping pairs are dropped by the
    overlap branch whether or not the net filter exists.  Two same-net rails 1 um apart
    are not overlapping, so they reach the distance test, and whether they are reported
    depends on the net filter alone.  Skipped by default -- a rail is allowed to be drawn
    in two pieces -- and found with `same_net=True`, which is how a fab-breaking sliver
    inside one net would surface.
    """
    a = _rect(0, 0, 100000, 10000, net="RF", owner="a")
    b = _rect(101000, 0, 200000, 10000, net="RF", owner="b")   # 1 um apart, one net
    assert min_gap_violations([a, b], tech) == ()
    hits = min_gap_violations([a, b], tech, same_net=True)
    assert [v.owners for v in hits] == [("a", "b")]
    assert hits[0].measured_nm == 1000 and hits[0].required_nm == 5000


@pytest.mark.parametrize("dx,dy,fires", [
    (3000, 4000, False),   # exactly 5000 nm corner to corner: meets the rule
    (2999, 4000, True),    # one nanometre inside it
    (3000, 3999, True),
    (5000, 0, False),      # side by side, exactly the rule
    (4999, 0, True),
    (4000, 4000, False),   # 5657 nm by Euclid and so clear; Chebyshev would call it 4000
    (3535, 3535, True),    # 4999 nm by Euclid: the other side of the same razor
])
def test_min_gap_measures_a_corner_by_euclid_and_never_takes_a_square_root(
        tech, dx, dy, fires):
    """The 3-4-5 razor: exactly 5000 nm apart passes, one nanometre closer does not.

    A Chebyshev check would call (3000, 4000) a 4000 nm gap and fire; a bounding-box
    check would call it 3000 and fire harder.  Only `dx*dx + dy*dy >= rule*rule` puts the
    boundary exactly on 5000, and being integers on both sides it lands on it exactly.
    """
    a = _rect(0, 0, 1000, 1000, net="RF", owner="a")
    b = _rect(1000 + dx, 1000 + dy, 3000 + dx, 3000 + dy, net="DC0", owner="b")
    assert bool(min_gap_violations([a, b], tech)) is fires


def test_min_gap_only_compares_shapes_on_the_same_layer(tech):
    """RF and DC are different masks; a rule between them is not this rule."""
    rf = _rect(0, 0, 1000, 1000, layer="RF", net="RF", owner="rf")
    dc = _rect(1001, 0, 2000, 1000, layer="DC", net="DC", owner="dc")
    assert min_gap_violations([rf, dc], tech) == ()


# ------------------------------------------------------------------------- layouts

def test_flatten_is_deterministic_and_reattributes_to_the_instance_owner(tech):
    cell = Cell("rail", (_rect(20750, -100000, 120250, 100000, owner="template"),))
    lay = Layout(tech, {"rail": cell},
                 (Inst("rail", 0, 0, 0, "segA"), Inst("rail", 0, 500000, 2, "segB")))
    a, b = lay.flatten()
    assert lay.flatten() == lay.flatten()
    assert (a.owner, b.owner) == ("segA", "segB"), "a placed shape names its own segment"
    assert lay.n_polys() == 2
    assert lay.area_nm2() == 2 * cell.polys[0].area_nm2()
    assert lay.area_nm2(role="nothing") == 0


def test_an_instance_of_a_missing_cell_is_refused_at_construction(tech):
    with pytest.raises(KeyError, match="no such cell"):
        Layout(tech, {}, (Inst("ghost"),))


def test_an_empty_layout_has_no_bounding_box_rather_than_a_zero_one(tech):
    assert Layout(tech).bbox() is None


def test_union_by_net_refuses_a_non_rectangle_rather_than_boxing_it(tech):
    ell = Poly("RF", (0, 0, 30, 0, 30, 10, 10, 10, 10, 30, 0, 30), "probe", "RF", "L")
    lay = Layout(tech, {"c": Cell("c", (ell,))}, (Inst("c"),))
    with pytest.raises(ValueError, match="more metal than was drawn"):
        lay.union_by_net()


def test_the_summary_reports_what_a_report_needs(tech):
    cell = Cell("c", (_rect(0, 0, 100, 200, role="rail"),
                      _rect(0, 300, 100, 400, layer="DC", role="dc_pad", net="DC0")))
    lay = Layout(tech, {"c": Cell("c", cell.polys)}, (Inst("c", owner="s0"),),
                 refused=(Refusal("segment", "sX", "not axis aligned"),),
                 notes=("hello",))
    s = lay.summary()
    assert s["technology"] == PRESET and s["n_polys"] == 2
    assert s["polys_by_role"] == {"dc_pad": 1, "rail": 1}
    assert s["polys_by_layer"] == {"DC": 1, "RF": 1}
    assert s["n_refused"] == 1 and s["notes"] == ["hello"]


# --------------------------------------------------------------------- technology

def test_a_dimension_cannot_be_written_without_a_source():
    with pytest.raises(TypeError):
        Dim(41500)  # type: ignore[call-arg]
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="non-empty source"):
            Dim(41500, blank)


@pytest.mark.parametrize("bad", [41500.0, "41500", True])
def test_a_dimension_is_an_integer_number_of_nanometres(bad):
    with pytest.raises(TypeError, match="int in nanometres"):
        Dim(bad, "test")


def test_an_unknown_dimension_names_the_ones_that_exist(tech):
    with pytest.raises(KeyError) as e:
        tech.dim("w_rff")
    msg = str(e.value)
    assert "w_rff" in msg and "w_rf" in msg and "dc_pitch" in msg


def test_layer_purposes_are_closed(tech):
    with pytest.raises(ValueError, match="not one of"):
        Layer("X", 9, 0, "antenna", 0, 1, "Au", 5000, 5000, "test")
    with pytest.raises(KeyError, match="not a layer purpose"):
        tech.layer("antenna")


def test_a_missing_purpose_refuses_instead_of_dropping_metal_from_the_sum(tech):
    with pytest.raises(KeyError, match="no 'shim' layer"):
        tech.layer("shim")
    assert not tech.has_purpose("shim")


def test_two_layers_with_one_purpose_is_ambiguous_not_first_match(tech):
    doc = tech.to_json()
    extra = dict(doc["layers"][0])
    extra.update({"name": "RF2", "gds_layer": 11})
    doc["layers"] = doc["layers"] + [extra]
    two = Technology.from_json(doc)
    with pytest.raises(KeyError, match="2 layers with purpose 'rf'"):
        two.layer("rf")


def test_two_layers_on_one_gds_number_cannot_be_told_apart(tech):
    doc = tech.to_json()
    clash = dict(doc["layers"][1])
    clash["name"] = "DC2"          # same gds_layer/datatype as DC
    doc["layers"] = doc["layers"] + [clash]
    with pytest.raises(ValueError, match="reuses GDS"):
        Technology.from_json(doc)


def test_an_unknown_key_in_a_technology_file_is_refused(tech):
    doc = tech.to_json()
    doc["nm_per_unit"] = {"nm": 1000, "source": "the old single-scale field"}
    with pytest.raises(KeyError, match="nm_per_unit"):
        Technology.from_json(doc)
    doc = tech.to_json()
    del doc["nm_per_unit_y"]
    with pytest.raises(KeyError, match="missing"):
        Technology.from_json(doc)


def test_the_technology_round_trips_through_json(tech):
    assert Technology.from_json(tech.to_json()) == tech
    text = json.dumps(tech.to_json())
    assert Technology.from_json(json.loads(text)) == tech


def test_a_stack_that_is_not_one_plane_refuses_rather_than_approximating(tech):
    doc = tech.to_json()
    doc["layers"][1]["z_nm"] = 2000
    raised = Technology.from_json(doc)
    with pytest.raises(ValueError, match="different heights"):
        raised.require_coplanar("rf", "dc")
    doc = tech.to_json()
    for lay in doc["layers"]:
        lay["z_nm"] = 2000
    with pytest.raises(ValueError, match="electrode plane must be z = 0"):
        Technology.from_json(doc).require_coplanar("rf")


def test_loading_an_unknown_technology_names_the_presets():
    with pytest.raises(FileNotFoundError) as e:
        load_technology("phoenix")
    assert PRESET in str(e.value)


# ----------------------------------------------------- the preset, number by number

def test_exactly_one_preset_ships():
    """One preset whose every number is a published input, per `d_one_honest_preset`."""
    assert preset_names() == (PRESET,)


def test_every_dimension_and_every_layer_of_the_preset_carries_a_source(tech):
    assert tech.source
    for name, d in tech.dims.items():
        assert len(d.source) > 20, f"{name} has a token source: {d.source!r}"
    for lay in tech.layers:
        assert len(lay.source) > 20, f"{lay.name} has a token source"
    for axis in (tech.nm_per_unit_x, tech.nm_per_unit_y):
        assert len(axis.source) > 20


def test_no_dimension_in_the_preset_was_chosen_rather_than_read(tech):
    """The preset's honesty as a number, and the number is zero.

    Every dimension is either a page reference into 2201.12579 or a derivation from ones
    that are.  `well_gap` was the last holdout -- it started as a `declared:` two-gap break
    marking each trapping site, and working the tiling arithmetic through showed that a
    break costs a control electrode and leaves two per trap pitch where the lattice scale's
    own derivation says three.  The paper fabricates a uniform tiling and 2305.03828's
    broadcast needs one, so `well_gap = gap` is what the sources actually say.  If this
    list ever grows, the growth was a decision.
    """
    assert tech.declared() == ()
    assert tech.nm("well_gap") == tech.nm("gap")
    assert "Wells are made by voltages, not by geometry" in tech.dim("well_gap").source


def test_the_declared_marker_still_works_even_though_the_preset_needs_none(tech):
    """A guard that nothing triggers is a guard nobody has tested."""
    from qccd.phys.tech import DECLARED
    assert not Dim(1, "2201.12579 ms.tex:283").is_declared
    assert Dim(1, DECLARED + " because I said so").is_declared
    doc = tech.to_json()
    doc["dims"]["invented"] = {"nm": 42, "source": "declared: no source for this at all"}
    doc["nm_per_unit_y"] = {"nm": 1, "source": "declared: also invented"}
    assert Technology.from_json(doc).declared() == ("invented", "nm_per_unit_y")


def test_the_published_dimensions_are_the_papers_tabulated_values(tech):
    assert tech.nm("w_rf") == 99500 and "tab:junction_linear" in tech.dim("w_rf").source
    assert tech.nm("w_g") == 41500
    assert tech.nm("gap") == 5000 and "ms.tex:368" in tech.dim("gap").source
    assert tech.nm("dc_pitch") == 75000 and "ms.tex:682" in tech.dim("dc_pitch").source
    assert tech.nm("dc_width") == 49750


def test_the_derived_dimensions_agree_with_what_they_are_derived_from(tech):
    """A preset can drift against itself; these make that a failure, not a surprise."""
    assert tech.nm("dc_setback") == tech.nm("w_g") // 2 + tech.nm("w_rf") + tech.nm("gap")
    assert tech.nm_per_unit_x.nm == 3 * tech.nm("dc_pitch"), (
        "one lattice unit ALONG a rail is three broadcast control electrodes (2305.03828)")
    assert tech.nm("min_axis_pitch") == (
        2 * (tech.nm("dc_setback") + tech.nm("dc_width")) + tech.nm("gap"))
    assert tech.nm_per_unit_y.nm == tech.nm("min_axis_pitch"), (
        "one lattice unit ACROSS the rails is the closest two trap axes can be drawn")
    assert tech.nm("dc_centre_width") == tech.nm("w_g") - 2 * tech.nm("gap") == 31500, (
        "the paper states the fabricated value directly, ms.tex:685")


def test_the_rail_extension_agrees_with_the_ion_height_the_field_kernel_computes(tech):
    """The one place the technology file and the field kernel have to meet.

    `rail_end_extension` is one nominal ion height, and the ion height is not in the file
    -- it comes out of `strip_null_height` on the same two RF widths.  So the preset is
    checked against the solver rather than against a number typed beside it.
    """
    h_nm = strip_null_height(tech.nm("w_g"), tech.nm("w_rf"))  # scale-free: nm in, nm out
    assert h_nm == pytest.approx(49951.85, abs=0.01)
    assert abs(tech.nm("rail_end_extension") - h_nm) / h_nm < 0.01


def test_the_two_lattice_scales_are_separate_knobs_and_measure_different_things(tech):
    """The preset is anisotropic, and not by preference -- by geometry.

    Along a rail a lattice unit is the axial trap pitch, set by how many control
    electrodes a well needs: three, so 225 um.  Across the rails it is the closest two
    trap axes can be drawn, set by how wide one axis's electrode stack is: 355 um.  These
    are different quantities and they do not agree, so a single global scale would have to
    be wrong about one of them.  An isotropic 225 um shorts every ring device --
    `test_build.py::test_an_isotropic_lattice_does_not_fit_two_rails_and_the_preset_says_so`
    is the executable form of that.
    """
    assert not tech.is_isotropic
    assert tech.nm_per_unit_x.nm == 225000 and tech.nm_per_unit_y.nm == 355000
    assert "is NOT the same quantity as nm_per_unit_x" in tech.nm_per_unit_y.source
    doc = tech.to_json()
    doc["nm_per_unit_y"] = {"nm": 900000, "source": "test: a wide racetrack"}
    anis = Technology.from_json(doc)
    assert anis.nm_per_unit_x.nm == 225000 and anis.nm_per_unit_y.nm == 900000


@pytest.mark.parametrize("purpose", PURPOSES)
def test_every_declared_purpose_is_a_purpose_the_technology_can_be_asked_about(purpose):
    """`has_purpose` must answer for all of them; only `layer` may refuse."""
    t = load_technology(PRESET)
    assert isinstance(t.has_purpose(purpose), bool)
