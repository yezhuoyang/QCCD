"""Design rules over the metal, and the one number that is deliberately not a rule.

Three things are proved here.

**The checks are live and they are not vacuous.**  Five of the nine shipped devices come
back clean, which on its own would be equally consistent with a checker that never fires.
So each rule also has a hand-built case that must fail, the number of net pairs actually
compared is asserted, and the two devices that do fail are pinned to exact counts.

**Overlap on one net is a merge.**  Perpendicular RF rails necessarily overlap at every
degree-4 node.  The union collapses them; the spacing check never sees them; and putting
the same two shapes on different nets makes it fire, so the skip is a decision rather than
an absence.

**Counted versus declared is printed, not judged.**  `control.wiring.electrodes_per_trap`
says 24 and the drawn metal says 5.89.  Neither is measured, so the disagreement lands in
`disclosures` and `clean` stays True.  A test asserts exactly that -- if this ever becomes
a violation, it will be because someone decided it should be, not because it drifted.

The last section is the discipline guard: nothing here may reach `RULE_STATEMENTS`,
`architecture_violations`, `BROWSER_SET` or `engine.js`.  Those are mirrored in JavaScript
and diffed at tolerance zero over every architecture file, so a Python-only rule firing
there is an automatic red harness.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.phys.build import build_layout  # noqa: E402
from qccd.phys.drc import RULES, Disclosure, DRCReport, check, checked  # noqa: E402
from qccd.phys.shapes import Cell, Inst, Layout, Poly  # noqa: E402
from qccd.phys.tech import (  # noqa: E402
    Technology,
    TechnologyError,
    load_technology,
)

ARCH = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH.glob("*.arch.json"))
BY_NAME = {load(ARCH / f"{s}.arch.json").name: s for s in DEVICES}
PRESET = "eth_junction_2201.12579"

SURFACE = "surface_default"

# Re-pinned when `keepout_half_width` was corrected: a pad tiled onto a crossing rail's own
# control column used to be kept, and every collision it caused counted here.  ring144_24v
# 66 -> 44 (its axial collisions were all of that kind; the transverse ones, the spurs
# running into the opposite rail, remain and are the real finding), cyclone_dual_loop
# 16 min_gap + 32 rf_dc -> 0 + 24.  `Codesign/findings/q06g`.
#
# Re-pinned again when the collaborator's two rules arrived.  `overlap` is new and fires
# wherever `rf_dc_clearance` already did on these two devices -- every one of those
# clearance failures turns out to be metal ACTUALLY SHARED, not a near miss, which is a
# sharper statement of the same finding.  `dc_pairs_per_site` is new and fires much more
# widely: under this preset a lattice unit is three control electrodes long, so a single
# pad dropped at a corner or a junction already puts the site next to it under three
# pairs.  Five devices that were clean are not any more, and what changed is the question
# being asked, not the metal -- `tests/test_build.py` pins the polygon counts unmoved.
def _counts(**kw):
    out = {"min_width": 0, "min_gap": 0, "rf_dc_clearance": 0, "overlap": 0,
           "dc_pairs_per_site": 0}
    out.update(kw)
    return out


CLEAN = ["chain72", "stationary_chain"]
DIRTY = {
    "ring144_24v": _counts(rf_dc_clearance=44, overlap=44, dc_pairs_per_site=98),
    "cyclone_dual_loop": _counts(rf_dc_clearance=32, overlap=32, dc_pairs_per_site=12),
    "cyclone_base": _counts(dc_pairs_per_site=8),
    "h2_racetrack": _counts(dc_pairs_per_site=8),
    "ladder_2x72": _counts(dc_pairs_per_site=140),
    "grid9x9": _counts(dc_pairs_per_site=144),
    "deck_unit_cell": _counts(dc_pairs_per_site=144),
}

#: the same nine devices under the collaborator's own defaults, where the numbers are
#: different for a reason: `surface_default` is isotropic and its lattice unit is eight
#: control electrodes long, so a dropped pad no longer starves the site beside it
SURFACE_DIRTY = {
    "ring144_24v": _counts(dc_pairs_per_site=46),
    "cyclone_dual_loop": _counts(min_gap=4, rf_dc_clearance=24, overlap=24,
                                 dc_pairs_per_site=4),
    "ladder_2x72": _counts(dc_pairs_per_site=44),
    "grid9x9": _counts(dc_pairs_per_site=144),
    "deck_unit_cell": _counts(dc_pairs_per_site=144),
}
SURFACE_CLEAN = ["chain72", "cyclone_base", "h2_racetrack", "stationary_chain"]


@pytest.fixture(scope="module")
def tech():
    return load_technology(PRESET)


@pytest.fixture(scope="module")
def surface():
    return load_technology(SURFACE)


def _all_reports(tech):
    out = {}
    for name, stem in BY_NAME.items():
        arch = load(ARCH / f"{stem}.arch.json")
        out[name] = check(build_layout(arch, tech), arch)
    return out


@pytest.fixture(scope="module")
def reports(tech):
    return _all_reports(tech)


@pytest.fixture(scope="module")
def surface_reports(surface):
    return _all_reports(surface)


def _rect(x0, y0, x1, y1, *, layer="RF", net="RF", role="rail", owner="o"):
    return Poly.rect(layer, x0, y0, x1, y1, role=role, net=net, owner=owner)


def _layout(tech, *polys, global_nets=("RF",)):
    return Layout(tech, {"c": Cell("c", polys)}, (Inst("c"),), global_nets=global_nets)


# ------------------------------------------------------------- the shipped devices

@pytest.mark.parametrize("name", CLEAN)
def test_the_devices_that_pass_pass_every_rule(reports, name):
    rep = reports[name]
    assert rep.clean, rep.text()
    assert rep.by_rule() == {r: 0 for r in RULES}


@pytest.mark.parametrize("name,counts", sorted(DIRTY.items()))
def test_the_devices_that_fail_fail_by_exactly_this_much(reports, name, counts):
    """Pinned, so that a change in either the builder or the checker is visible.

    `ring144_24v`'s 66 are its dock spurs running into the opposite rail's control column
    -- there is no room for a third trap axis between two rails that are already at the
    minimum pitch.  `cyclone_dual_loop`'s are its four segments crossing the data loop
    without a node.  Both are properties of the device, and both are explained at length
    in `tests/test_build.py`.
    """
    rep = reports[name]
    assert not rep.clean
    assert rep.by_rule() == counts, rep.text(limit=4)


def test_the_checker_actually_compared_something(reports):
    """Five clean devices would also be the output of a checker that does nothing."""
    for name in ("chain72", "ring144_24v", "ladder_2x72", "h2_racetrack"):
        assert reports[name].compared > 1000, (name, reports[name].compared)


def test_a_layout_with_no_control_electrodes_says_so_rather_than_passing_quietly(reports):
    """`grid9x9` compares zero pairs, and that is the loudest thing in its report.

    Every control electrode was dropped where a perpendicular rail crosses its column,
    which on a lattice this fine is everywhere.  So the spacing rules pass by vacuum, and a
    clean report would be actively misleading.
    """
    for name in ("grid9x9", "deck_unit_cell"):
        rep = reports[name]
        assert rep.compared == 0, "no pair of nets exists to compare"
        # and now it does not pass quietly either: every site is named, because
        # `dc_pairs_per_site` counts what a site GOT rather than what was compared
        assert not rep.clean
        assert rep.by_rule()["dc_pairs_per_site"] == 144
        assert {v.measured_nm for v in rep.violations} == {0}
        topics = [d.topic for d in rep.disclosures]
        assert "no control electrodes survived" in topics, topics
        text = next(d.statement for d in rep.disclosures
                    if d.topic == "no control electrodes survived")
        assert "passed by vacuum" in text


# --------------------------------------------------------------- each rule, alive

def test_min_width_fires_on_metal_thinner_than_the_process_allows(tech):
    lay = _layout(tech, _rect(0, 0, 3000, 90000, owner="thin"))
    got = check(lay).violations
    assert [(v.rule, v.measured_nm, v.required_nm) for v in got] == [("min_width", 3000,
                                                                      5000)]
    assert got[0].owners == ("thin",)


def test_min_gap_fires_between_two_nets_on_one_layer(tech):
    lay = _layout(tech,
                  _rect(0, 0, 100000, 50000, net="A", owner="a"),
                  _rect(102000, 0, 200000, 50000, net="B", owner="b"))
    got = [v for v in check(lay).violations if v.rule == "min_gap"]
    assert len(got) == 1 and got[0].measured_nm == 2000
    assert set(got[0].owners) == {"A", "B"}


def test_min_gap_does_not_fire_at_exactly_the_rule(tech):
    lay = _layout(tech,
                  _rect(0, 0, 100000, 50000, net="A", owner="a"),
                  _rect(105000, 0, 200000, 50000, net="B", owner="b"))
    assert check(lay).clean


def test_rf_to_dc_is_the_only_check_that_looks_across_layers(tech):
    """RF and DC are different masks, so `min_gap` never compares them -- but a short is
    the worst thing this layer can find, so a rule exists that does."""
    lay = _layout(tech,
                  _rect(0, 0, 100000, 50000, layer="RF", net="RF", owner="rail"),
                  _rect(100000, 0, 200000, 50000, layer="DC", net="DC:x:0",
                        role="dc_pad", owner="pad"))
    got = check(lay).violations
    assert [v.rule for v in got] == ["rf_dc_clearance"]
    assert got[0].measured_nm == 0, "touching metal is welded metal"
    assert got[0].layer == "RF/DC"


def test_rf_to_dc_clears_at_the_stricter_of_the_two_layer_rules(tech):
    lay = _layout(tech,
                  _rect(0, 0, 100000, 50000, layer="RF", net="RF", owner="rail"),
                  _rect(105000, 0, 200000, 50000, layer="DC", net="DC:x:0",
                        role="dc_pad", owner="pad"))
    assert check(lay).clean


# ------------------------------------------------------------------ union by net

def test_two_perpendicular_rails_on_one_net_are_a_merge_and_not_a_violation(tech):
    """The degree-4 case, and the anti-vacuity that makes the skip a decision."""
    across = _rect(-300000, 20750, 300000, 120250, net="RF", owner="ns")
    down = _rect(20750, -300000, 120250, 300000, net="RF", owner="ew")
    assert check(_layout(tech, across, down)).clean

    # the very same geometry on two nets is a short, so the checker was awake -- and it
    # is reported twice over, because two rails crossing at 0 nm are not merely too close
    # together, they are one piece of metal on two nets
    split = Poly(down.layer, down.xy, down.role, "OTHER", down.owner)
    got = check(_layout(tech, across, split)).violations
    assert sorted(v.rule for v in got) == ["min_gap", "overlap"]
    assert next(v for v in got if v.rule == "min_gap").measured_nm == 0
    assert next(v for v in got if v.rule == "overlap").measured_nm == 99500


def test_a_nets_own_union_slabs_are_never_reported_against_each_other(tech):
    """The subtle half of union-by-net, and the case no shipped device reaches yet.

    An L-shaped net comes back from the union as two rectangles that *touch* -- that is
    what a slab decomposition of one connected shape looks like.  Comparing them would
    report a zero-nanometre gap between a net and itself, which is not a defect, it is the
    union's own cut line.

    Nothing in `arch/` triggers this today: the RF layer carries one net, and every control
    net is a single rectangle, so the check is skipped before it can go wrong.  Which is
    exactly why the case is hand-built -- removing the same-net skip leaves all nine
    devices clean and the whole suite green.
    """
    ell = [_rect(0, 0, 100000, 50000, net="A", owner="a0"),
           _rect(0, 50000, 50000, 150000, net="A", owner="a1")]
    # B is 2 um away in x but 10 um away in y, so it clears the rule while still landing
    # inside the sweep window -- otherwise `compared` would be zero and the run vacuous
    near = _rect(102000, 60000, 200000, 100000, net="B", owner="b")
    lay = _layout(tech, *ell, near, global_nets=())
    assert len(lay.union_by_net("RF")["A"]) == 2, "the L must decompose into two slabs"
    rep = check(lay)
    assert rep.clean, [v.as_dict() for v in rep.violations]
    assert rep.compared > 0, "and a cross-net pair must actually have been compared"


def test_one_finding_per_net_pair_however_the_union_decomposes_it(tech):
    """A rail drawn in three pieces is one net, and its neighbour hears about it once.

    The union's output is a slab decomposition, so a long rail comes back as several
    rectangles and a pad near a seam is close to two of them.  Without collapsing to one
    finding per net pair the same fact would be reported twice.
    """
    rail = [_rect(k * 100000, 0, (k + 1) * 100000, 50000, net="RF", owner=f"r{k}")
            for k in range(3)]
    pad = _rect(0, 51000, 300000, 100000, layer="DC", net="DC:x:0", role="dc_pad",
                owner="pad")
    got = check(_layout(tech, *rail, pad)).violations
    assert len(got) == 1, [v.as_dict() for v in got]
    assert got[0].rule == "rf_dc_clearance" and got[0].measured_nm == 1000


# ------------------------------------------------- the disclosure that is not a verdict

def test_counted_versus_declared_is_a_disclosure_and_the_device_stays_clean(reports):
    """24 declared, 5.04 drawn, and the report refuses to call that a failure.

    The drawn figure fell from 5.89 when `keepout_half_width` was corrected: the pads it
    now drops are real electrodes the naive crossing costs, which is the second, countable
    price of that geometry the module docstring names.
    """
    rep = reports["ring144_24v"]
    d = next(x for x in rep.disclosures if x.topic == "electrodes per trap")
    assert d.declared == 24
    assert d.counted == pytest.approx(5.04, abs=0.01)
    assert "NEITHER NUMBER IS MEASURED" in d.statement
    assert "for the architect to judge" in d.statement
    # it prints the pitch each side implies, and stops
    assert "implies an axial trap pitch of 600000 nm" in d.statement
    assert "the technology says 225000 nm" in d.statement
    # and it is not a violation
    assert all(v.rule in RULES for v in rep.violations)
    assert not any("electrode" in v.rule for v in rep.violations)


def test_a_device_with_only_disclosures_is_clean(reports):
    """`clean` means the geometry meets the rules, not that nothing was said."""
    rep = reports["chain72"]
    assert rep.clean and rep.disclosures
    assert [d.topic for d in rep.disclosures] == ["electrodes per trap",
                                                  "waived technology minimums"]


def _through(tech, degree: int):
    """A rail `ab` drawn through a node `M` it is not joined to; `M` of the given degree."""
    from qccd.api import Machine
    from qccd.arch.device import Device, Node, Segment
    pos = {"A": (0.0, 0.0), "B": (2.0, 0.0), "M": (1.0, 0.0), "N": (1.0, 1.0), "P": (1.0, -1.0)}
    segs = [("ab", "A", "B"), ("mn", "M", "N")] + ([("mp", "M", "P")] if degree == 2 else [])
    nodes = {k: Node(id=k, pos=v, kind="site", zone_type="trap", labels=("trap",)) for k, v in pos.items()
             if degree == 2 or k != "P"}
    segments = {s: Segment(id=s, ends=(a, b), length=1.0, capacity=1, loop=None, labels=("rail",))
                for s, a, b in segs}
    dev = Device(nodes=nodes, segments=segments, loops={}, generator="explicit", params={})
    arch = Machine.from_device(dev, name=f"through{degree}").arch
    return check(build_layout(arch, tech), arch)


def test_the_crossing_disclosure_separates_the_two_kinds_by_degree(tech):
    """The shipped devices no longer cross (their two defects were repaired in the
    generators when R21 arrived), so the two wordings are exercised on hand-drawn ones."""
    one = next(d for d in _through(tech, 1).disclosures
               if d.topic == "segments crossing nodes")
    assert one.counted == 1 and "degree 1" in one.statement
    assert "a dock drawn ON a rail" in one.statement

    two = next(d for d in _through(tech, 2).disclosures
               if d.topic == "segments crossing nodes")
    assert two.counted == 1 and "degree 2" in two.statement
    assert "its degree is understated" in two.statement


def test_disclosures_need_an_architecture_and_are_simply_absent_without_one(tech):
    """`check(layout)` with no document says nothing ABOUT THE DOCUMENT.

    What survives is the technology's own disclosures -- the dimensions it authored and
    the minimums it waives -- because those are true of the technology whether or not
    anyone passed an architecture, and suppressing them would make the quieter call the
    less honest one.
    """
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    topics = [d.topic for d in check(lay).disclosures]
    assert topics == ["waived technology minimums"]
    for device_topic in ("electrodes per trap", "segments crossing nodes",
                         "no control electrodes survived"):
        assert device_topic not in topics
    assert check(lay).device == "(device)"


# ---------------------------------------------------------------------- the report

def test_the_report_serialises_and_reads(reports):
    rep = reports["ring144_24v"]
    d = rep.as_dict()
    assert d["by_rule"]["rf_dc_clearance"] == 44 and d["clean"] is False
    assert len(d["violations"]) == 44 + 44 + 98
    text = rep.text(limit=2)
    assert "rf_dc_clearance    44" in text and "and 184 more" in text
    assert "[disclosure]" in text


def test_checked_returns_a_layout_carrying_its_own_findings(tech):
    arch = load(ARCH / "ring144_24v.arch.json")
    lay = build_layout(arch, tech)
    assert lay.violations == ()
    out = checked(lay, arch)
    assert len(out.violations) == 44 + 44 + 98
    assert out.flatten() == lay.flatten(), "checking must not move any metal"
    assert lay.violations == (), "and must not mutate the layout it was given"


def test_violations_are_ordered_by_rule_then_position(reports):
    """A report that reorders between runs is a report nobody can diff."""
    rep = reports["ring144_24v"]
    order = {r: i for i, r in enumerate(RULES)}
    keys = [(order[v.rule], v.where, v.owners) for v in rep.violations]
    assert keys == sorted(keys)


# ------------------------------------------- the collaborator's technology rules

def test_the_second_preset_loads_and_is_the_collaborators_defaults(surface):
    """Six named variables, each with a minimum and a default, and this file is them."""
    assert surface.rule_table() == (
        ("n_dc_pairs", 3, 3, "pairs", 3),
        ("w_rf", 30_000, 60_000, "nm", 60_000),
        ("w_dc", 30_000, 50_000, "nm", 50_000),
        ("l_dc", 30_000, 50_000, "nm", 50_000),
        ("g_dc", 5_000, 8_000, "nm", 8_000),
        ("g_rf", 8_000, 10_000, "nm", 10_000),
    )
    assert surface.w_rf == 6 * surface.w_dc // 5, "w_rf = 1.2 * w_dc, as asked"
    assert surface.is_isotropic and surface.waived() == ()


def test_a_technology_under_a_minimum_is_refused_and_the_field_is_named(surface):
    """The refusal has to say WHICH number, or the file has to be read by eye."""
    doc = surface.to_json()
    doc["dims"]["gap"] = {"nm": 4000, "source": "test: under the 5 um minimum"}
    with pytest.raises(TechnologyError) as e:
        Technology.from_json(doc)
    msg = str(e.value)
    assert "g_dc" in msg and "4000" in msg and "5000" in msg
    assert "dims['gap']" in msg, "and where the value is read from"

    for field, key, bad in (("n_dc_pairs", None, 2), ("w_rf", "w_rf", 20_000),
                            ("w_dc", "dc_width", 20_000), ("g_rf", "g_rf", 6_000),
                            ("l_dc", "dc_pitch", 30_000)):
        doc = surface.to_json()
        if key is None:
            doc["n_dc_pairs"] = {"n": bad, "source": "test: under the minimum"}
        else:
            doc["dims"][key] = {"nm": bad, "source": "test: under the minimum"}
        with pytest.raises(TechnologyError, match=field):
            Technology.from_json(doc)


def test_the_one_waiver_that_ships_is_the_published_traps_own_gap(tech):
    """`eth_junction_2201.12579` reproduces a trap fabricated at a 5 um RF clearance.

    The project minimum is 8 um.  Raising it here would move `dc_setback` by 3 um and
    redraw every rail-to-column clearance of a published geometry, so the preset waives
    the rule in writing instead -- and the report says so, because a waiver nobody sees is
    the same as no rule.
    """
    assert tech.waived() == ("g_rf",) and tech.g_rf == 5000
    assert "REPRODUCES a published trap" in tech.waivers["g_rf"]
    arch = load(ARCH / "chain.arch.json")
    rep = check(build_layout(arch, tech), arch)
    d = next(x for x in rep.disclosures if x.topic == "waived technology minimums")
    assert d.counted == 1 and "g_rf" in d.statement


def test_a_waiver_must_be_needed_and_must_name_a_rule(surface):
    """A stale waiver is a claim nobody checked, so it is an error rather than noise."""
    doc = surface.to_json()
    doc["waivers"] = {"g_rf": "test: but surface_default already meets it"}
    with pytest.raises(TechnologyError, match="already meets"):
        Technology.from_json(doc)
    doc["waivers"] = {"w_bus": "test: not a rule at all"}
    with pytest.raises(TechnologyError, match="not a technology rule"):
        Technology.from_json(doc)
    doc["waivers"] = {"g_rf": "   "}
    with pytest.raises(TechnologyError, match="waived in writing"):
        Technology.from_json(doc)


# ------------------------------------------------------------------- overlap

def test_overlap_fires_on_two_electrodes_drawn_on_top_of_each_other(tech):
    lay = _layout(tech,
                  _rect(0, 0, 100000, 50000, net="A", owner="a"),
                  _rect(90000, 10000, 200000, 60000, net="B", owner="b"),
                  global_nets=())
    got = [v for v in check(lay).violations if v.rule == "overlap"]
    assert len(got) == 1, [v.as_dict() for v in got]
    assert set(got[0].owners) == {"A", "B"}, "the message names the two nets"
    assert got[0].measured_nm == 10000 and got[0].required_nm == 0


def test_overlap_crosses_layers_because_a_rail_over_a_pad_is_still_a_short(tech):
    lay = _layout(tech,
                  _rect(0, 0, 100000, 50000, layer="RF", net="RF", owner="rail"),
                  _rect(80000, 0, 200000, 50000, layer="DC", net="DC:x:0",
                        role="dc_pad", owner="pad"))
    got = [v for v in check(lay).violations if v.rule == "overlap"]
    assert len(got) == 1 and got[0].layer == "RF/DC"
    assert got[0].measured_nm == 20000


def test_overlap_does_not_fire_on_one_net_or_on_metal_that_merely_touches(tech):
    """Two perpendicular rails on the RF net are a merge; touching is a gap finding."""
    across = _rect(-300000, 20750, 300000, 120250, net="RF", owner="ns")
    down = _rect(20750, -300000, 120250, 300000, net="RF", owner="ew")
    assert not [v for v in check(_layout(tech, across, down)).violations
                if v.rule == "overlap"]
    touching = _layout(tech,
                       _rect(0, 0, 100000, 50000, layer="RF", net="RF", owner="rail"),
                       _rect(100000, 0, 200000, 50000, layer="DC", net="DC:x:0",
                             role="dc_pad", owner="pad"))
    rules = {v.rule for v in check(touching).violations}
    assert rules == {"rf_dc_clearance"}, "0 nm is a clearance finding, not an overlap"


def test_one_overlap_finding_per_pair_of_nets_however_it_is_decomposed(tech):
    rail = [_rect(k * 100000, 0, (k + 1) * 100000, 50000, net="RF", owner=f"r{k}")
            for k in range(3)]
    pad = _rect(0, 20000, 300000, 100000, layer="DC", net="DC:x:0", role="dc_pad",
                owner="pad")
    got = [v for v in check(_layout(tech, *rail, pad)).violations if v.rule == "overlap"]
    assert len(got) == 1 and got[0].measured_nm == 30000, [v.as_dict() for v in got]


@pytest.mark.parametrize("stem", ["chain", "ring144_24v"])
def test_the_generated_metal_of_the_two_named_devices_never_overlaps_under_the_defaults(
        surface, stem):
    """`surface_default` is a technology both of them FIT in, which is the point of it.

    `ring144_24v` does not fit `eth_junction_2201.12579`: 44 of its dock spurs share metal
    with the opposite rail's control column, and those 44 are reported by `overlap` as well
    as by `rf_dc_clearance` -- the clearance failures there are not near misses, they are
    metal actually shared.  Raise the lattice to the isotropic 464 um the collaborator's
    own clearances make of it, and the collisions are gone.
    """
    arch = load(ARCH / f"{stem}.arch.json")
    rep = check(build_layout(arch, surface), arch)
    assert rep.by_rule()["overlap"] == 0, rep.text(limit=4)
    assert rep.by_rule()["rf_dc_clearance"] == 0
    assert rep.compared > 1000, "and something really was compared"


def test_under_the_published_preset_the_rings_clearance_failures_are_real_overlaps(
        reports):
    """44 and 44, and they are the same 44 pairs of nets."""
    rep = reports["ring144_24v"]
    clear = {tuple(sorted(v.owners)) for v in rep.violations
             if v.rule == "rf_dc_clearance"}
    over = {tuple(sorted(v.owners)) for v in rep.violations if v.rule == "overlap"}
    assert len(clear) == len(over) == 44 and clear == over


# --------------------------------------------------------- dc pairs per site

@pytest.mark.parametrize("preset", [PRESET, SURFACE])
def test_every_site_of_a_plain_chain_has_its_three_pairs_under_both_presets(preset):
    """The rule the collaborator asked for, on the device that has nothing in its way."""
    arch = load(ARCH / "chain.arch.json")
    rep = check(build_layout(arch, load_technology(preset)), arch)
    assert rep.by_rule()["dc_pairs_per_site"] == 0, rep.text(limit=4)


def test_a_site_span_too_short_for_three_pads_fails_and_names_the_site(surface):
    """Two pitches to a lattice unit holds two pads, and every site is then short.

    The technology is legal -- every one of the six minimums still holds, because they are
    about the electrodes and not about how far apart the traps are.  What fails is the
    device drawn on it, which is the whole point of having the rule in the DRC as well as
    in the loader.
    """
    doc = surface.to_json()
    short = 2 * surface.nm("dc_pitch")
    for axis in ("nm_per_unit_x", "nm_per_unit_y"):
        doc[axis] = {"nm": short, "source": "test: two control electrodes to a trap"}
    tight = Technology.from_json(doc)
    arch = load(ARCH / "chain.arch.json")
    rep = check(build_layout(arch, tight), arch)
    got = [v for v in rep.violations if v.rule == "dc_pairs_per_site"]
    assert len(got) == 72, rep.text(limit=3)
    assert {v.measured_nm for v in got} == {2} and {v.required_nm for v in got} == {3}
    assert got[0].owners[0] in arch.device.nodes, "the finding names a site"
    assert "DC pairs against 3 required" in rep.text(limit=1)


def test_the_rule_reads_the_technologys_number_and_not_a_constant(surface):
    """Ask for nine pairs and the same chain that passed at three does not."""
    doc = surface.to_json()
    doc["n_dc_pairs"] = {"n": 9, "source": "test: more pairs than the pitch can hold"}
    arch = load(ARCH / "chain.arch.json")
    rep = check(build_layout(arch, Technology.from_json(doc)), arch)
    assert rep.by_rule()["dc_pairs_per_site"] == 72
    assert {v.required_nm for v in rep.violations} == {9}


@pytest.mark.parametrize("name,counts", sorted(SURFACE_DIRTY.items()))
def test_the_devices_that_fail_the_collaborators_defaults(surface_reports, name, counts):
    """What is left once the technology is one the devices fit.

    Only `dc_pairs_per_site` survives for most of them, and it is an ARCHITECTURAL
    finding: `ring144_24v`'s 24 docks hang half a lattice unit off the rail, inside the
    keep-out where a crossing rail's own control column sits, so they get one pair and no
    technology that keeps pads off that column can give them three.  `cyclone_dual_loop`
    still collides because four of its segments run through nodes they are not incident
    to, which is a property of the document.
    """
    rep = surface_reports[name]
    assert rep.by_rule() == counts, rep.text(limit=4)


@pytest.mark.parametrize("name", SURFACE_CLEAN)
def test_four_devices_pass_every_rule_under_the_collaborators_defaults(
        surface_reports, name):
    rep = surface_reports[name]
    assert rep.clean, rep.text(limit=6)


def test_the_two_presets_disagree_about_the_ring_and_that_is_the_finding(
        reports, surface_reports):
    """One device, two technologies, and the DRC is what tells them apart."""
    published, defaults = reports["ring144_24v"], surface_reports["ring144_24v"]
    assert published.by_rule()["overlap"] == 44
    assert defaults.by_rule()["overlap"] == 0
    assert published.n_polys < defaults.n_polys, "the second draws more, and finer"


def test_rf_to_dc_now_measures_against_g_rf_and_not_the_mask_rule(surface):
    """The reason `g_rf` exists: a 9 um clearance passed the 5 um mask rule and was wrong.

    Under `surface_default` the technology declares 10 um from driven metal to anything
    else while the process rule is 5 um, so 9 um is a violation here and was not one
    before.  The same layout under the published preset, whose g_rf IS 5 um, still passes.
    """
    lay = _layout(surface,
                  _rect(0, 0, 100000, 50000, layer="RF", net="RF", owner="rail"),
                  _rect(109000, 0, 200000, 50000, layer="DC", net="DC:x:0",
                        role="dc_pad", owner="pad"))
    got = [v for v in check(lay).violations if v.rule == "rf_dc_clearance"]
    assert len(got) == 1 and got[0].measured_nm == 9000 and got[0].required_nm == 10000
    wide = _layout(surface,
                   _rect(0, 0, 100000, 50000, layer="RF", net="RF", owner="rail"),
                   _rect(110000, 0, 200000, 50000, layer="DC", net="DC:x:0",
                         role="dc_pad", owner="pad"))
    assert check(wide).clean, "exactly g_rf is not a violation"


# ------------------------------------------------------- discipline: not a rule

def test_nothing_here_became_a_verifier_rule():
    """27 rules, mirrored in `engine.js` and diffed at tolerance zero over every arch file.

    A Python-only rule firing there is an automatic red harness, and a design rule is a
    different kind of claim anyway: it is about a technology's fabrication limits, which
    the document does not declare and the browser cannot know.
    """
    from qccd.verify import rules as R
    from qccd.viz.render import BROWSER_SET

    assert len(R.RULE_STATEMENTS) == 27 and len(R.RULE_SOURCES) == 27
    assert len(BROWSER_SET) == 21 and set(BROWSER_SET) <= set(R.RULE_STATEMENTS)
    for name in RULES:
        assert name not in R.RULE_STATEMENTS
        assert name not in R.RULE_SOURCES
        assert name not in BROWSER_SET


#: The DRC names distinctive enough to guard on.  `check`, `checked`, `RULES`,
#: `Violation` and `Layout` are deliberately absent: all five are ordinary words that
#: already appear in those files for unrelated reasons -- `engine.js` has 21 `check`s of
#: its own -- so asserting on them would guard the English, not the code.
DISTINCTIVE = ("DRCReport", "Disclosure", "min_width_violations", "min_gap_violations",
               "union_by_net", "rf_dc_clearance", "min_width", "min_gap", "drc")


def test_the_drc_does_not_reach_the_browser():
    for rel in ("qccd/viz/engine.js", "qccd/viz/js/edit.js", "qccd/viz/js/editor.js"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for name in DISTINCTIVE:
            assert not re.search(r"\b" + re.escape(name) + r"\b", src), (name, rel)
    # anti-vacuity: every guarded name must be something this package really exports,
    # or the guard is protecting a string nobody would have written anyway
    from qccd.phys import drc, shapes
    surface = (set(drc.__all__) | set(shapes.__all__) | set(RULES)
               | {n for n in dir(shapes.Layout) if not n.startswith("_")}
               | {drc.__name__.rsplit(".", 1)[-1]})
    missing = [n for n in DISTINCTIVE if n not in surface]
    assert not missing, f"guarding names nothing exports: {missing}"


def test_architecture_violations_is_untouched_by_any_of_this():
    """The rule pass over the nine shipped devices must be exactly what it was."""
    from qccd.verify.rules import architecture_violations
    total = 0
    for stem in DEVICES:
        total += len(architecture_violations(load(ARCH / f"{stem}.arch.json")))
    assert total == 0, "no shipped architecture violates a verifier rule, and none may"
