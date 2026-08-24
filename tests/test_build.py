"""Derivation: from an architecture document to metal, and from metal back to physics.

The headline is the last test in the first section.  `chain72` is a document nobody wrote
electrodes for; `build_layout` turns it into 781 integer-nanometre polygons; `union_by_net`
merges 142 rail rectangles into 2; and the field kernel finds the RF null at **49.948 um**
against the 49.9519 um that 2201.12579's closed form gives for the same two widths.  That
is the whole point of the package in one assertion: an architecture file now predicts an
ion height, and the prediction can be wrong.

Everything else here guards the derivation:

* nothing is authored -- the same document and technology always give the same polygons;
* nothing rounds -- every lattice coordinate lands on an exact nanometre for all nine
  shipped devices, and the residual is measured rather than assumed;
* nothing is guessed -- a segment that is not axis-aligned is refused by id;
* a degree>=3 node gets the paper's own counterexample, tagged as such;
* and the control electrodes clear the RF rails by exactly the fabrication gap, which is
  the rule that fixes `dc_setback` in the first place.

Two devices do not clear it, and both are findings this layer exists to produce rather
than defects in it.  They are asserted here so that S4 has a target and so that a change
in either is visible: see `test_the_two_devices_whose_metal_collides_are_named`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.arch.device import Device, Node, Segment  # noqa: E402
from qccd.phys.build import (  # noqa: E402
    NAIVE_CROSSING_SOURCE,
    build_layout,
    rects_for_field,
    unconnected_crossings,
)
from qccd.phys.field import rf_null, strip_null_height  # noqa: E402
from qccd.phys.tech import load_technology  # noqa: E402

ARCH = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH.glob("*.arch.json"))
PRESET = "eth_junction_2201.12579"

#: A document's declared `name` is not always its file stem -- `chain.arch.json` is
#: `chain72` -- and the tests below key on the name, so resolve it once.
BY_NAME = {load(ARCH / f"{stem}.arch.json").name: stem for stem in DEVICES}


def device(name: str):
    return load(ARCH / f"{BY_NAME[name]}.arch.json").device


@pytest.fixture(scope="module")
def tech():
    return load_technology(PRESET)


@pytest.fixture(scope="module")
def layouts(tech):
    return {name: build_layout(load(ARCH / f"{name}.arch.json"), tech)
            for name in DEVICES}


# ------------------------------------------------------ the derivation, end to end

def test_all_nine_shipped_devices_build_with_nothing_refused(layouts):
    assert len(layouts) == 9, DEVICES
    for name, lay in layouts.items():
        assert lay.refused == (), f"{name}: {[r.as_dict() for r in lay.refused]}"
        assert lay.n_polys() > 0, name


def test_no_lattice_coordinate_needed_rounding_on_any_shipped_device(layouts):
    """Measured, not assumed.  Every position is a dyadic rational times an integer."""
    for name, lay in layouts.items():
        assert any("nothing rounded" in n for n in lay.notes), (
            f"{name} rounded a coordinate: {lay.notes}")


def test_the_derivation_is_a_function_of_the_document_and_nothing_else(tech):
    """Two builds of the same pair are equal, polygon for polygon and owner for owner."""
    a = load(ARCH / "ring144_24v.arch.json")
    first, second = build_layout(a, tech), build_layout(a, tech)
    assert first.flatten() == second.flatten()
    assert build_layout(load(ARCH / "ring144_24v.arch.json"), tech).flatten() \
        == first.flatten()


def test_a_chains_derived_metal_puts_the_ion_where_the_paper_says(tech):
    """**The headline.**  Document -> polygons -> union -> field -> a published height.

    Nothing in `chain.arch.json` mentions an electrode.  The rails come out of the
    technology's `w_g` and `w_rf`, the 142 rectangles union to the 2 continuous rails they
    describe, and the gapless-plane solver puts the RF null at 49.948 um.  The target,
    49.9519 um, is `1/2 sqrt(w_g (w_g + 2 w_rf))` -- 2201.12579's own linear-section sizing
    rule inverted, and a different model from the one that produced the answer.

    The 3.9 nm shortfall is the finite-rail truncation, exactly as `test_field.py` measures
    it: the chain is 16 mm long at h ~ 50 um, so L/h ~ 320, and the error there is expected
    to be about 8e-5 relative.  It is 7.7e-5.
    """
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    rects = rects_for_field(lay)
    assert len(rects) == 2, "the two rails must union into two rectangles, not 142"

    box = lay.bbox()
    mid_x = (box[0] + box[2]) / 2 * 1e-9
    got = rf_null(rects, mid_x, (5e-6, 300e-6), y=0.0)
    assert got.found, got.reason
    assert got.residual < 1e-12

    target = strip_null_height(tech.nm("w_g") * 1e-9, tech.nm("w_rf") * 1e-9)
    assert got.z * 1e6 == pytest.approx(49.95, abs=0.05), "the paper's own fixed point"
    assert abs(got.z - target) / target == pytest.approx(7.7e-5, rel=0.20)


def test_the_field_conversion_is_the_only_place_nanometres_become_metres(tech):
    """`rects_for_field` unions first: a doubled overlap would be doubled metal."""
    lay = build_layout(load(ARCH / "ring144_24v.arch.json"), tech)
    rects = rects_for_field(lay)
    rf_polys = [p for p in lay.flatten() if p.layer == tech.layer("rf").name]
    assert 0 < len(rects) < len(rf_polys), (
        f"{len(rf_polys)} RF polygons should union to fewer rectangles, got {len(rects)}")
    for r in rects:
        assert all(abs(v) < 1.0 for v in (r.x0, r.y0, r.x1, r.y1)), "metres, not nm"
    total = sum(r.area for r in rects)
    assert total == pytest.approx(lay.union_area_nm2(tech.layer("rf").name) * 1e-18,
                                  rel=1e-9)


# ------------------------------------------------------------------- what is drawn

def test_a_segment_becomes_two_rails_and_three_columns_of_control_pads(tech):
    """A straight uniform chain: 2 rails and 3x3 pads per trap pitch, and no more."""
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    per_role: dict[str, int] = {}
    for p in lay.flatten():
        per_role[p.role] = per_role.get(p.role, 0) + 1
    n_seg = len(load(ARCH / "chain.arch.json").device.segments)
    assert per_role["rail"] == 2 * n_seg
    assert per_role["dc_pad"] == 9 * n_seg, (
        "three control columns times three electrodes per trap pitch -- the {a,b,c} "
        "broadcast group nm_per_unit_x is derived from")
    assert "naive_crossing" not in per_role, "a chain has no degree-3 node"


def _north_pads(lay):
    return [p for p in lay.flatten() if p.role == "dc_pad"
            and p.net.rsplit("/", 1)[-1].startswith("DC:north")]


def test_every_control_electrode_is_its_own_net(tech):
    """A cell placed 168 times is 168 electrodes, not one net round the whole ring.

    Cell reuse is what keeps a 168-segment device to seven cells, and the net names inside
    a cell are necessarily cell-local: every rail cell calls its first north pad
    `DC:north:0`.  If `flatten` did not qualify them by the placing instance, all 336 of
    them would be one net -- which would merge them in the union, silently skip every
    spacing check between neighbours, and make the DRC report five findings where there
    are 66.  That is exactly what happened before this was fixed.

    RF is the exception and has to be: one drive, one net, everywhere.
    """
    lay = build_layout(load(ARCH / "ring144_24v.arch.json"), tech)
    polys = lay.flatten()
    dc_nets = {p.net for p in polys if p.role == "dc_pad"}
    dc_polys = [p for p in polys if p.role == "dc_pad"]
    assert len(dc_nets) == len(dc_polys) == 990, "one net per drawn control electrode"
    assert all("/" in n for n in dc_nets), "each is qualified by its segment"
    rf_nets = {p.net for p in polys if p.layer == tech.layer("rf").name}
    assert rf_nets == {"RF"}, "the RF drive is one net over the whole device"


def test_the_control_tiling_is_the_three_electrodes_per_well_it_is_scaled_from(tech):
    """The lattice scale claims three electrodes per trap pitch.  Count them."""
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    pads = _north_pads(lay)
    xs = sorted(p.bbox()[0] for p in pads)
    pitch = tech.nm("dc_pitch")
    deltas = {b - a for a, b in zip(xs, xs[1:])}
    assert deltas == {pitch}, f"pads must tile at exactly dc_pitch, saw {sorted(deltas)}"
    assert len(pads) == 3 * len(lay.insts)


def test_consecutive_control_pads_are_separated_by_exactly_one_fabrication_gap(tech):
    """A pad is `dc_pitch - gap` long, so the tiling leaves the gap and no more.

    Nothing else here would notice a pad drawn a full pitch long: the count would be
    right, the pitch would be right, and every pad would be welded to its neighbour.  So
    the spacing is measured, and the whole DC layer is run through the design-rule checker
    as well -- consecutive pads are on different nets, so it does look at them.
    """
    from qccd.phys.shapes import min_gap_violations
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    pads = _north_pads(lay)
    spans = sorted(p.bbox()[0::2] for p in pads)
    gaps = {b[0] - a[1] for a, b in zip(spans, spans[1:])}
    assert gaps == {tech.nm("gap")}, f"pad spacing must be exactly the gap, saw {gaps}"
    dc_only = [p for p in lay.flatten() if p.layer == tech.layer("dc").name]
    assert min_gap_violations(dc_only, tech) == ()


def test_a_coordinate_that_does_not_land_on_a_nanometre_is_disclosed(tech):
    """The rounding note is measured, so it has to be able to say something else.

    Every shipped device is on dyadic positions and rounds by exactly zero, which means
    the "nothing rounded" note would also be emitted by a builder that rounded through a
    float and never looked.  A position of 0.1 is not dyadic: the exact residual is
    1.2e-12 nm, the rounded coordinate is 22500 either way, and only the exact path can
    tell you the difference existed.
    """
    dev = _hand_device([Node("A", (0.1, 0.0)), Node("B", (1.0, 0.0))],
                       [Segment("e", ("A", "B"))])
    lay = build_layout(dev, tech)
    note = next(n for n in lay.notes if "rounding" in n or "rounded" in n)
    assert "nothing rounded" not in note, note
    assert "largest coordinate rounding" in note
    assert lay.bbox()[0] == 22500 - tech.nm("rail_end_extension")


def test_a_site_break_is_a_knob_and_the_shipped_value_leaves_the_tiling_uniform(tech):
    """`well_gap == gap` is a decision: wells are made by voltages, not by geometry.

    Widening it must visibly break the tiling, or the mechanism is not really there.
    """
    doc = tech.to_json()
    doc["dims"]["well_gap"] = {"nm": 60000, "source": "test: a deliberately visible break"}
    from qccd.phys.tech import Technology
    wide = Technology.from_json(doc)
    tight = build_layout(load(ARCH / "chain.arch.json"), tech)
    broken = build_layout(load(ARCH / "chain.arch.json"), wide)
    n_tight = sum(1 for p in tight.flatten() if p.role == "dc_pad")
    n_broken = sum(1 for p in broken.flatten() if p.role == "dc_pad")
    assert n_broken < n_tight, "a wider well break must cost control electrodes"


@pytest.mark.parametrize("name,junctions,squares", [
    ("ring144_24v", 24, 46), ("grid9x9", 77, 252), ("deck_unit_cell", 77, 252),
    ("ladder_2x72", 46, 136), ("chain72", 0, 0), ("cyclone_base", 0, 0),
    ("h2_racetrack", 0, 0),
])
def test_every_degree_three_node_gets_the_papers_counterexample(
        layouts, name, junctions, squares):
    """One corner square per (x-arm, y-arm) pair -- so a T gets two and an X gets four.

    The totals are also derived from the graph rather than only pinned, because the
    pinned numbers are not the obvious ones: `ring144_24v` has 24 degree-3 nodes and 46
    squares rather than 48, since its two end-cap docks sit at ring corners where there is
    no second x-arm.  `grid9x9` is 28 tees and 49 crosses, 28*2 + 49*4 = 252 exactly.
    """
    dev = device(name)
    lay = layouts[BY_NAME[name]]
    polys = [p for p in lay.flatten() if p.role == "naive_crossing"]

    expected = 0
    for nid in dev.junction_nodes:
        xs = ys = 0
        for sid in dev.incidence[nid]:
            other = dev.segments[sid].other(nid)
            dx = dev.nodes[other].pos[0] - dev.nodes[nid].pos[0]
            dy = dev.nodes[other].pos[1] - dev.nodes[nid].pos[1]
            if dx and not dy:
                xs |= (1 if dx > 0 else 2)
            elif dy and not dx:
                ys |= (1 if dy > 0 else 2)
        expected += bin(xs).count("1") * bin(ys).count("1")

    assert len(dev.junction_nodes) == junctions
    assert expected == squares, "the graph and the pinned total must agree"
    assert len(polys) == squares


def test_the_crossing_squares_add_no_metal_because_the_rails_already_reach_them(layouts):
    """The squares carry a role and a citation; the union says they carry nothing else."""
    lay = layouts[BY_NAME["grid9x9"]]
    rf = lay.tech.layer("rf").name
    with_squares = lay.union_area_nm2(rf)
    from qccd.phys.shapes import union_rects
    rails = [p.bbox() for p in lay.flatten()
             if p.layer == rf and p.role != "naive_crossing"]
    without = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in union_rects(rails))
    assert with_squares == without, "a corner square must be covered by the rails it joins"


def test_the_crossing_carries_the_citation_that_it_is_a_counterexample(layouts):
    """A reader who does not know this shape is deliberately the bad one will think bug."""
    lay = layouts[BY_NAME["ring144_24v"]]
    assert any(NAIVE_CROSSING_SOURCE in n for n in lay.notes)
    assert "84 um" in NAIVE_CROSSING_SOURCE and "0.07" in NAIVE_CROSSING_SOURCE
    assert "2201.12579" in NAIVE_CROSSING_SOURCE


def test_the_crossing_is_on_the_rf_net_so_it_merges_instead_of_shorting(layouts, tech):
    """Perpendicular rails must overlap at a crossing; on one net that is a merge."""
    lay = layouts[BY_NAME["ring144_24v"]]
    crossings = [p for p in lay.flatten() if p.role == "naive_crossing"]
    assert crossings and {p.net for p in crossings} == {"RF"}
    rf = tech.layer("rf").name
    summed = lay.area_nm2(layer=rf)
    merged = lay.union_area_nm2(rf)
    assert merged < summed, "the crossings overlap the rails, and the union knows"


def test_a_rail_runs_past_a_dead_end_so_the_last_trap_is_not_a_field_edge(tech):
    """A degree-1 node gets `rail_end_extension`; an interior node does not."""
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    box = lay.bbox()
    dev = load(ARCH / "chain.arch.json").device
    span = (len(dev.segments)) * tech.nm_per_unit_x.nm
    assert box[2] - box[0] == span + 2 * tech.nm("rail_end_extension")


# ------------------------------------------------------------------- what is refused

def _hand_device(nodes, segments):
    return Device(nodes={n.id: n for n in nodes}, segments={s.id: s for s in segments})


def test_a_diagonal_segment_is_refused_by_id_and_not_approximated(tech):
    dev = _hand_device(
        [Node("A", (0.0, 0.0)), Node("B", (1.0, 1.0)), Node("C", (2.0, 1.0))],
        [Segment("diag", ("A", "B")), Segment("ok", ("B", "C"))])
    lay = build_layout(dev, tech)
    assert [r.owner for r in lay.refused] == ["diag"]
    assert "neither axis-aligned nor a point" in lay.refused[0].reason
    assert lay.refused[0].kind == "segment"
    assert lay.n_polys() > 0, "the axis-aligned segment still builds"


def test_a_zero_length_segment_is_refused_too(tech):
    dev = _hand_device(
        [Node("A", (0.0, 0.0)), Node("B", (0.0, 0.0)), Node("C", (1.0, 0.0))],
        [Segment("point", ("A", "B")), Segment("ok", ("A", "C"))])
    lay = build_layout(dev, tech)
    assert [r.owner for r in lay.refused] == ["point"]


def test_a_three_dimensional_node_is_refused_rather_than_projected(tech):
    dev = _hand_device(
        [Node("A", (0.0, 0.0, 5.0)), Node("B", (1.0, 0.0, 5.0))],
        [Segment("e", ("A", "B"))])
    lay = build_layout(dev, tech)
    kinds = {r.kind for r in lay.refused}
    assert kinds == {"node", "segment"}
    assert any("will not project" in r.reason for r in lay.refused)


def test_a_technology_whose_setback_does_not_clear_the_rails_is_refused(tech):
    """`dc_setback` is derived; a file that overrides it inconsistently draws nonsense."""
    doc = tech.to_json()
    doc["dims"]["dc_setback"] = {"nm": 60000, "source": "test: inside the rail"}
    from qccd.phys.tech import Technology
    with pytest.raises(ValueError, match="must clear the rails"):
        build_layout(load(ARCH / "chain.arch.json"), Technology.from_json(doc))


# ---------------------------------------------------- clearance, and the two findings

def _rf_dc_overlaps(lay, tech):
    """(pairs closer than the fabrication gap, closest clean separation squared).

    Touching counts.  Two pieces of metal that share a boundary point are one piece of
    metal, and RF welded to a control electrode is the worst failure this layer can find --
    so the predicate is the design rule, `dx*dx + dy*dy < gap*gap`, and not strict overlap.
    """
    limit = tech.nm("gap")
    limit2 = limit * limit
    rf = [p.bbox() for p in lay.flatten() if p.layer == tech.layer("rf").name]
    dc = [p.bbox() for p in lay.flatten() if p.layer == tech.layer("dc").name]
    n, closest = 0, None
    for ax0, ay0, ax1, ay1 in dc:
        for bx0, by0, bx1, by1 in rf:
            dx = max(bx0 - ax1, ax0 - bx1, 0)
            dy = max(by0 - ay1, ay0 - by1, 0)
            d2 = dx * dx + dy * dy
            if d2 < limit2:
                n += 1
                continue
            closest = d2 if closest is None else min(closest, d2)
    return n, closest


@pytest.mark.parametrize("name", ["chain72", "cyclone_base", "h2_racetrack",
                                  "ladder_2x72", "grid9x9", "deck_unit_cell",
                                  "stationary_chain"])
def test_the_control_electrodes_clear_the_rails_by_exactly_the_fabrication_gap(
        layouts, tech, name):
    """The keep-out is what `dc_setback` buys, and the margin comes out exact.

    A pad is dropped wherever a PERPENDICULAR rail crosses its column -- which is a
    question about direction, not degree, so a degree-2 bend needs it as much as a
    degree-4 crossing.  Keying it on `junction_nodes` put a short at every corner of every
    ring; this parametrisation is what caught that.
    """
    n, closest = _rf_dc_overlaps(layouts[BY_NAME[name]], tech)
    assert n == 0, f"{name}: {n} RF-to-DC shorts"
    if closest is not None:
        assert math.isqrt(closest) == tech.nm("gap"), (
            f"{name}: closest RF-to-DC approach is {math.isqrt(closest)} nm")


def test_the_two_devices_whose_metal_collides_are_named(layouts, tech):
    """Two devices do not fit, and it is the device that does not fit, not the builder.

    `ring144_24v` puts its 24 dock ancillas at the mid-line of a ring whose two rails are
    one `min_axis_pitch` apart -- the closest two trap axes can be drawn in this technology
    -- so there is no room for a third axis between them, and adjacent spurs from the two
    rows land one axial unit apart besides.  The exact threshold is measured in
    `test_the_lattice_scale_at_which_the_ring_becomes_fabricable_is_sharp`.

    `cyclone_dual_loop` collides for a different reason -- see the crossing test below.

    These are asserted rather than fixed because `qccd/phys/drc.py` is where findings are
    reported, and because the repair is the architect's.
    """
    for name in ("ring144_24v", "cyclone_dual_loop"):
        n, _ = _rf_dc_overlaps(layouts[BY_NAME[name]], tech)
        assert n > 0, f"{name} stopped colliding; if that was deliberate, update this test"


def _ring_shorts(tech, sx: int, sy: int) -> int:
    from qccd.phys.tech import Technology
    doc = tech.to_json()
    doc["nm_per_unit_x"] = {"nm": sx, "source": "test: axial scale sweep"}
    doc["nm_per_unit_y"] = {"nm": sy, "source": "test: transverse scale sweep"}
    scaled = Technology.from_json(doc)
    return _rf_dc_overlaps(build_layout(load(ARCH / "ring144_24v.arch.json"), scaled),
                           scaled)[0]


def test_the_lattice_scale_at_which_the_ring_becomes_fabricable_is_sharp(tech):
    """What it would take for `ring144_24v` to fit, to the nanometre.

    A test that only asserted "it collides" would be satisfied by a builder that always
    collides.  So the threshold is measured, and it is exact, and it is two different
    constraints in the two directions -- each derivable from the technology alone.

    **Along the rail**: the 24 dock spurs interleave -- `V6` rises from `(6, 0)` while
    `V138` descends from `(5, 1)` -- so two perpendicular trap axes end up one axial unit
    apart.  One spur's rail must clear the other's whole electrode stack, by a gap:

        x_min = (w_g/2 + w_rf) + (dc_setback + dc_width) + gap
              = 120250 + 175000 + 5000 = 300250 nm

    **Across it**: the dock sits at the half-unit, and its spur's rail overruns the dock by
    `rail_end_extension`, so half a unit must hold a stack plus that overrun plus a gap:

        y_min = 2 * (dc_setback + dc_width + rail_end_extension + gap)
              = 2 * 230000 = 460000 nm

    One nanometre under either and the violations come back -- 22 for the axial threshold,
    44 for the transverse one.

    The bite: the axial trap pitch derived from the control electrodes is 225 um -- three
    75 um segments per well, which is where `nm_per_unit_x` comes from -- and `x_min` is
    300 um.  They are incompatible.  So `ring144_24v` as drawn is not fabricable in this
    technology, and the repair is an architectural choice: stretch the trap pitch (to four
    control electrodes per well rather than three), space the docks further apart along the
    rail, or find a technology with a narrower electrode stack.  That is a sharper
    statement than PLAN section 0.5's heating argument and independent of it -- the 24
    verticals do not merely cost quanta, at these dimensions they do not fit.
    """
    stack = tech.nm("dc_setback") + tech.nm("dc_width")
    gap = tech.nm("gap")
    x_min = (tech.nm("w_g") // 2 + tech.nm("w_rf")) + stack + gap
    y_min = 2 * (stack + tech.nm("rail_end_extension") + gap)
    assert (x_min, y_min) == (300250, 460000)

    assert _ring_shorts(tech, x_min, y_min) == 0, "the derived thresholds are sufficient"
    assert _ring_shorts(tech, x_min - 1, y_min) == 22, "and the axial one is necessary"
    assert _ring_shorts(tech, x_min, y_min - 1) == 44, "as is the transverse one"

    # the shipped scale is under both, which is the finding
    assert tech.nm_per_unit_x.nm < x_min and tech.nm_per_unit_y.nm < y_min
    assert _ring_shorts(tech, tech.nm_per_unit_x.nm, tech.nm_per_unit_y.nm) == 66


def test_an_isotropic_lattice_does_not_fit_two_rails_and_the_preset_says_so(tech):
    """Why the technology carries two scales, as an executable statement.

    At 225 um in both directions -- one axial trap pitch -- the north control column of the
    bottom rail lands on the top rail.  Each axis needs `dc_setback + dc_width` = 175 um of
    room on each side, so two parallel axes need 355 um between them and cannot have 225.
    """
    from qccd.phys.tech import Technology
    doc = tech.to_json()
    doc["nm_per_unit_y"] = {"nm": tech.nm_per_unit_x.nm, "source": "test: isotropic"}
    iso = Technology.from_json(doc)
    lay = build_layout(load(ARCH / "cyclone_base.arch.json"), iso)
    n, _ = _rf_dc_overlaps(lay, iso)
    assert n > 0, "an isotropic lattice was expected to collide, and did not"
    assert build_layout(load(ARCH / "cyclone_base.arch.json"), tech)
    assert _rf_dc_overlaps(build_layout(load(ARCH / "cyclone_base.arch.json"), tech),
                           tech)[0] == 0


# ------------------------------------------------- the device defect this layer found

@pytest.mark.parametrize("name,pairs", [
    ("cyclone_dual_loop", 4), ("ring144_24v", 2), ("grid9x9", 0), ("chain72", 0),
    ("ladder_2x72", 0), ("h2_racetrack", 0), ("cyclone_base", 0),
])
def test_segments_running_through_nodes_they_do_not_touch_are_reported(name, pairs):
    """A planar trap has no overpass, so the graph and the plane must agree.  Twice they
    do not, and the crossed node's degree says which kind of disagreement it is.

    `ring144_24v`'s two are its end-cap docks, at degree 1 -- `docs/adl.md` already records
    that the ancilla for `S0 = (0,0)` sits at `(0, 0.5)`, on the segment from `S0` to
    `S143`.  A dock drawn ON a rail rather than beside it.

    `cyclone_dual_loop`'s four are degree **2**, which is the serious case: `EA35` runs
    from `(35,0)` to `(35,3)` straight through `DT35` and `DB35`, two nodes of the data
    loop.  The graph says the ancilla link and the data loop never meet; the plane says
    they share metal at two places.  If the crossing is real those nodes are degree 4, not
    2 -- they are junctions, they are not being charged as junctions, and the router is
    free to send two ions through the same electrode at once.

    Reported and not repaired: `arch/` is not this feature's to edit, and the two possible
    repairs say different things about the machine.
    """
    dev = device(name)
    got = unconnected_crossings(dev)
    assert len(got) == pairs, got
    if name == "ring144_24v":
        assert all(dev.degree(n) == 1 for _, n in got), "end-cap docks, degree 1"
    if name == "cyclone_dual_loop":
        assert all(dev.degree(n) == 2 for _, n in got), "two transport paths, degree 2"


def test_a_node_sitting_on_a_segments_endpoint_is_not_a_crossing():
    """"Through" means strictly between, and the boundary has to be the right one.

    `C` shares `B`'s position but is not on the segment `A-B`; it is *at* its end.  That is
    a different defect -- two traps at one place -- and reporting it here would mean
    `unconnected_crossings` no longer says what its name says.  With a `<=` the whole
    corpus still passes, because no shipped device has coincident nodes, which is exactly
    why this case is hand-built.
    """
    horizontal = _hand_device(
        [Node("A", (0.0, 0.0)), Node("B", (2.0, 0.0)), Node("C", (2.0, 0.0)),
         Node("D", (1.0, 0.0))],
        [Segment("ab", ("A", "B"))])
    assert [n for _, n in unconnected_crossings(horizontal)] == ["D"]

    # and the same on the other axis: the two branches are separate code
    vertical = _hand_device(
        [Node("A", (0.0, 0.0)), Node("B", (0.0, 2.0)), Node("C", (0.0, 2.0)),
         Node("D", (0.0, 1.0))],
        [Segment("ab", ("A", "B"))])
    assert [n for _, n in unconnected_crossings(vertical)] == ["D"]


def test_the_crossing_report_reaches_the_layout_notes(layouts):
    lay = layouts[BY_NAME["cyclone_dual_loop"]]
    note = next(n for n in lay.notes if "not incident to" in n)
    assert "EA35" in note and "deg 2" in note and "no overpass" in note


# ---------------------------------------------------------------------- discipline

def test_the_builder_never_reaches_the_browser_layout_engine():
    """Physical coordinates must not enter `compute_layout`.

    `qccd/viz/layout.py` refuses anything past `COORD_MAX = 1e6`, and a 20 mm die in
    nanometres is 2e7 -- so this would fail loudly rather than silently.  It is asserted
    anyway, because the failure would be at the far end of a call chain nobody expects to
    involve electrodes.
    """
    import re
    src = (ROOT / "qccd/phys/build.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, flags=re.M)
    assert not any("viz" in m for m in imports), imports
    assert "compute_layout" not in src


def test_placement_uses_quarter_turns_and_no_trigonometry():
    src = (ROOT / "qccd/phys/build.py").read_text(encoding="utf-8")
    for banned in ("math.cos", "math.sin", "math.atan", "radians", "degrees"):
        assert banned not in src, banned


def test_cells_are_reused_rather_than_one_per_segment(layouts):
    """GDSII's own unit of reuse, and the reason a 168-segment ring is seven cells."""
    for name, lay in layouts.items():
        if len(lay.insts) > 20:
            assert len(lay.cells) < len(lay.insts) // 5, (
                f"{name}: {len(lay.cells)} cells for {len(lay.insts)} instances")
