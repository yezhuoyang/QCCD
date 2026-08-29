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
from qccd.phys.tech import load_technology  # noqa: E402

ARCH = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH.glob("*.arch.json"))
BY_NAME = {load(ARCH / f"{s}.arch.json").name: s for s in DEVICES}
PRESET = "eth_junction_2201.12579"

CLEAN = ["chain72", "cyclone_base", "h2_racetrack", "ladder_2x72", "stationary_chain",
         "grid9x9", "deck_unit_cell"]
DIRTY = {"ring144_24v": {"min_width": 0, "min_gap": 0, "rf_dc_clearance": 66},
         "cyclone_dual_loop": {"min_width": 0, "min_gap": 16, "rf_dc_clearance": 32}}


@pytest.fixture(scope="module")
def tech():
    return load_technology(PRESET)


@pytest.fixture(scope="module")
def reports(tech):
    out = {}
    for name, stem in BY_NAME.items():
        arch = load(ARCH / f"{stem}.arch.json")
        out[name] = check(build_layout(arch, tech), arch)
    return out


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
        assert rep.compared == 0 and rep.clean
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

    # the very same geometry on two nets is a short, so the checker was awake
    split = Poly(down.layer, down.xy, down.role, "OTHER", down.owner)
    got = check(_layout(tech, across, split)).violations
    assert [v.rule for v in got] == ["min_gap"] and got[0].measured_nm == 0


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
    """24 declared, 5.89 drawn, and the report refuses to call that a failure."""
    rep = reports["ring144_24v"]
    d = next(x for x in rep.disclosures if x.topic == "electrodes per trap")
    assert d.declared == 24
    assert d.counted == pytest.approx(5.89, abs=0.01)
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
    assert [d.topic for d in rep.disclosures] == ["electrodes per trap"]


def test_the_crossing_disclosure_separates_the_two_kinds_by_degree(reports):
    ring = next(d for d in reports["ring144_24v"].disclosures
                if d.topic == "segments crossing nodes")
    assert ring.counted == 2 and "degree 1" in ring.statement
    assert "a dock drawn ON a rail" in ring.statement

    dual = next(d for d in reports["cyclone_dual_loop"].disclosures
                if d.topic == "segments crossing nodes")
    assert dual.counted == 4 and "degree 2" in dual.statement
    assert "its degree is understated" in dual.statement


def test_disclosures_need_an_architecture_and_are_simply_absent_without_one(tech):
    """`check(layout)` with no document reports geometry only, and says nothing else."""
    lay = build_layout(load(ARCH / "chain.arch.json"), tech)
    assert check(lay).disclosures == ()
    assert check(lay).device == "(device)"


# ---------------------------------------------------------------------- the report

def test_the_report_serialises_and_reads(reports):
    rep = reports["ring144_24v"]
    d = rep.as_dict()
    assert d["by_rule"]["rf_dc_clearance"] == 66 and d["clean"] is False
    assert len(d["violations"]) == 66 and len(d["disclosures"]) == 2
    text = rep.text(limit=2)
    assert "rf_dc_clearance    66" in text and "and 64 more" in text
    assert "[disclosure]" in text


def test_checked_returns_a_layout_carrying_its_own_findings(tech):
    arch = load(ARCH / "ring144_24v.arch.json")
    lay = build_layout(arch, tech)
    assert lay.violations == ()
    out = checked(lay, arch)
    assert len(out.violations) == 66
    assert out.flatten() == lay.flatten(), "checking must not move any metal"
    assert lay.violations == (), "and must not mutate the layout it was given"


def test_violations_are_ordered_by_rule_then_position(reports):
    """A report that reorders between runs is a report nobody can diff."""
    rep = reports["ring144_24v"]
    order = {r: i for i, r in enumerate(RULES)}
    keys = [(order[v.rule], v.where, v.owners) for v in rep.violations]
    assert keys == sorted(keys)


# ------------------------------------------------------- discipline: not a rule

def test_nothing_here_became_a_verifier_rule():
    """25 rules, mirrored in `engine.js` and diffed at tolerance zero over every arch file.

    Was 23.  R19 (the electrode frame; an ARCHITECTURE rule, like R11(b)) and R4c took it
    to 25.  Neither is in `BROWSER_SET`, which is the per-cycle set the browser engine
    mirrors.

    A Python-only rule firing there is an automatic red harness, and a design rule is a
    different kind of claim anyway: it is about a technology's fabrication limits, which
    the document does not declare and the browser cannot know.
    """
    from qccd.verify import rules as R
    from qccd.viz.render import BROWSER_SET

    assert len(R.RULE_STATEMENTS) == 25 and len(R.RULE_SOURCES) == 25
    assert len(BROWSER_SET) == 17 and set(BROWSER_SET) <= set(R.RULE_STATEMENTS)
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
