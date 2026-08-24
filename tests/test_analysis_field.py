"""The physical analysis: a number an architect can act on, and a number a paper can refute.

**The junction check is the point of the whole package, and it is deliberately loose.**
2201.12579 measures its own naive crossing at an 84 um transport-path height and
0.07 meV/um^2 of confinement, using the same gapless model implemented here.  Fed only the
two RF widths and its drive conditions, this analysis returns 86.51 um and
0.0607 meV/um^2 -- **+2.99%** and **-13.3%**.

Both land inside the bands the paper publishes for this model (5% on positions, 20% on
pseudopotential, `ms.tex:200-208`), and the assertions below are written to those bands and
no tighter.  A tight assertion would be dishonest twice over: the 84 um is read off a
figure, and tuning anything to hit it exactly would turn a check into a fit.  The measured
residuals are asserted to a wide tolerance so a real change shows up, and recorded in
`Knowledge/notes/accumulated.yaml:fd_naive_crossing_reproduced`.

**The other headline is that no shipped device sits at its design height.**  The technology
sizes its rails for 49.95 um; an isolated rail returns 49.948; and every device with
neighbouring metal is somewhere else, up to +15% on `ladder_2x72`.  Four of the nine are
outside the model's own 5% band, so it is the geometry rather than the numerics -- and the
null is up to 5 um off the trap axis, where a search constrained to the axis finds nothing.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.analysis import ANALYSES, get_analysis  # noqa: E402
from qccd.analysis.field import (  # noqa: E402
    PhysicalAnalysis,
    confinement_meV_per_um2,
    naive_crossing_rects,
)
from qccd.phys.field import (  # noqa: E402
    ATOMIC_MASS_KG,
    ELEMENTARY_CHARGE_C,
    pseudopotential,
    strip_null_height,
)
from qccd.phys.tech import load_technology  # noqa: E402

#: 2201.12579 ms.tex:371 -- the conditions every pseudopotential figure in it uses
PAPER = {"rf": {"voltage_v": 40.0, "frequency_mhz": 40.0}, "mass_u": 39.9626}
#: ms.tex:200-208 -- the model's published error against FEM of the fabricated trap
POSITION_BAND = 0.05
PSEUDOPOTENTIAL_BAND = 0.20
#: ms.tex:596-604
PAPER_PATH_HEIGHT_UM = 84.0
PAPER_CONFINEMENT = 0.07

BA138 = 137.905247


@pytest.fixture(scope="module")
def paper_run():
    a = PhysicalAnalysis(device="chain", **PAPER)
    return a, a.run()


# ------------------------------------------------- the check the package exists for

def test_the_naive_junction_reproduces_the_papers_path_height(paper_run):
    """**Check 3.**  Two RF widths in; the paper's own 84 um out, to 3%.

    Nothing is fitted.  The geometry is 2201.12579 fig. 7(a) -- two linear sections
    crossed, each keeping the other's gap -- and the drive is the one stated at
    `ms.tex:371`.  The assertion is the paper's 5% position band and not a nanometre
    tighter, because the 84 um is read off a figure and tuning to it would make this a fit
    rather than a check.
    """
    _, data = paper_run
    got = data["naive_junction_path_height_um"]
    rel = (got - PAPER_PATH_HEIGHT_UM) / PAPER_PATH_HEIGHT_UM
    assert abs(rel) < POSITION_BAND, f"{got:.3f} um against {PAPER_PATH_HEIGHT_UM}"
    # recorded, so a change is visible even though the band is wide
    assert rel == pytest.approx(0.0299, abs=0.01)


def test_the_naive_junction_reproduces_the_papers_confinement(paper_run):
    """0.0607 against 0.07 meV/um^2, inside the 20% band the same table publishes."""
    _, data = paper_run
    got = data["naive_junction_confinement_meV_per_um2"]
    rel = (got - PAPER_CONFINEMENT) / PAPER_CONFINEMENT
    assert abs(rel) < PSEUDOPOTENTIAL_BAND, f"{got:.4f} against {PAPER_CONFINEMENT}"
    assert rel == pytest.approx(-0.133, abs=0.03)


def test_the_naive_crossing_reduces_to_a_linear_trap_far_from_the_centre():
    """The calibration that makes the junction number mean anything.

    Far out along an arm the geometry *is* the linear section, so the ion height there has
    to be the closed form it was sized from.  If it were not, the crossing would be some
    other trap and agreeing with the paper at the centre would be luck.
    """
    tech = load_technology("eth_junction_2201.12579")
    h = strip_null_height(tech.nm("w_g"), tech.nm("w_rf")) * 1e-9
    rects = naive_crossing_rects(tech.nm("w_g"), tech.nm("w_rf"),
                                 int(round(400 * h * 1e9)))
    mass = 39.9626 * ATOMIC_MASS_KG
    omega = 2 * math.pi * 40e6

    def height_at(x):
        def psi(z):
            return pseudopotential(rects, x, 0.0, z, voltage_v=40.0,
                                   omega_rf_rad_s=omega, mass_kg=mass)
        lo, hi = 30e-6, 80e-6
        for _ in range(200):
            m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
            lo, hi = (lo, m2) if psi(m1) < psi(m2) else (m1, hi)
        return 0.5 * (lo + hi)

    errs = [abs(height_at(k * h) / h - 1) for k in (10, 20, 50, 100)]
    assert errs == sorted(errs, reverse=True), f"must approach the strip, got {errs}"
    assert errs[-1] < 1e-3, f"100 ion heights out it is still {errs[-1]:.1e} off"


def test_both_trap_axes_are_clear_of_metal_in_the_naive_crossing():
    """The geometry decision this rests on, stated as a test.

    Running both rail pairs straight through each other -- the obvious reading of
    "crossing two linear traps" -- lays RF over both axes, and solving that finds no
    confined position anywhere near the junction: the ion would be above driven metal.
    The paper plots a path with a finite height, so that cannot be its geometry.
    """
    tech = load_technology("eth_junction_2201.12579")
    a = tech.nm("w_g") / 2 * 1e-9
    rects = naive_crossing_rects(tech.nm("w_g"), tech.nm("w_rf"), 5_000_000)
    for r in rects:
        assert not (r.x0 < 0.0 < r.x1 and r.y0 < 0.0 < r.y1), "metal over the centre"
        # no rectangle may straddle either channel
        assert not (r.x0 < -a and r.x1 > a), "metal across the y-trap's gap"
        assert not (r.y0 < -a and r.y1 > a), "metal across the x-trap's gap"


# --------------------------------------------------------- the device-level answer

def test_an_isolated_rail_returns_the_height_its_widths_were_sized_for():
    a = PhysicalAnalysis(device="chain", mass_u=BA138)
    data = a.run()
    tech = load_technology("eth_junction_2201.12579")
    h = strip_null_height(tech.nm("w_g"), tech.nm("w_rf")) / 1000
    assert data["ion_height_um"] == pytest.approx(h, rel=1e-3)
    assert data["null_residual"] < 1e-12


@pytest.mark.parametrize("device,expected_um,off_axis", [
    ("chain", 49.948, 0.0),
    ("cyclone_base", 50.566, 3.278),
    ("ring144_24v", 56.279, 4.234),
    ("ladder_2x72", 57.535, 4.793),
    ("grid9x9", 52.884, -5.768),
])
def test_no_device_with_neighbouring_metal_sits_at_its_design_height(
        device, expected_um, off_axis):
    """The result the platform could not previously state.

    A rail is sized for 49.95 um in isolation.  Put a second row 355 um away, or 24 dock
    spurs along it, and the ion moves -- by 12.7% on `ring144_24v` and 15.2% on
    `ladder_2x72`, both far outside the 5% band the model itself is good to.  It also
    moves sideways, up to 4.8 um off the trap axis, which is why the height is solved in
    the transverse plane rather than in `z` alone.
    """
    data = PhysicalAnalysis(device=device, mass_u=BA138).run()
    assert data["ion_height_um"] == pytest.approx(expected_um, abs=0.01)
    note = next(n for n in data["notes"] if "off the trap axis" in n)
    stated = float(note.split(" um off")[0].split()[-1])
    assert stated == pytest.approx(off_axis, abs=0.01), note
    tech = load_technology("eth_junction_2201.12579")
    ideal = strip_null_height(tech.nm("w_g"), tech.nm("w_rf")) / 1000
    outside = abs(data["ion_height_um"] / ideal - 1) > 0.05
    assert outside == any("outside the 5% band" in n for n in data["notes"])


def test_sweeping_the_rf_width_tracks_the_closed_form():
    """The knob an architect actually turns, checked against the analytic answer.

    `h = 1/2 sqrt(w_g (w_g + 2 w_rf))`, so widening the rails raises the ion.  The
    analysis is not told that; it re-derives the metal, re-solves the field, and the
    curve comes out on the formula.
    """
    tech = load_technology("eth_junction_2201.12579")
    a = PhysicalAnalysis(device="chain", mass_u=BA138)
    sweep = a.sweep("rf.w_rf_nm", [60_000, 99_500, 140_000])
    assert not sweep.failures, sweep.failures
    got = [v for _k, v in sweep.series("ion_height_um")]
    want = [strip_null_height(tech.nm("w_g"), w) / 1000
            for w in (60_000, 99_500, 140_000)]
    assert got == sorted(got), "a wider rail must raise the ion, monotonically"
    for g, w in zip(got, want):
        assert g == pytest.approx(w, rel=2e-3)


def test_the_run_reports_the_metal_it_solved(paper_run):
    a, data = paper_run
    assert data["n_polys"] == 781 and data["n_refused"] == 0
    assert data["electrode_bbox_mm2"] == pytest.approx(5.626, abs=0.01)
    assert a.run_args["tech"] == "eth_junction_2201.12579"
    assert a.run_args["mass_u"] == 39.9626


# -------------------------------------------------------------- refusing to guess

def test_the_ion_mass_has_no_default_and_the_refusal_names_it():
    """Every shipped file says `"qubit": "Ba+"` and no file says what that weighs."""
    assert PhysicalAnalysis.default_setup["mass_u"] is None
    with pytest.raises(ValueError) as e:
        PhysicalAnalysis(device="chain").run()
    msg = str(e.value)
    assert "mass_u" in msg and "no default" in msg
    assert "Ba+" in msg and "137.905247" in msg


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_nonsense_mass_is_refused(bad):
    with pytest.raises(ValueError, match="must be positive"):
        PhysicalAnalysis(device="chain", mass_u=bad).run()


def test_an_unknown_knob_is_refused_rather_than_ignored():
    with pytest.raises(KeyError, match="mass_amu"):
        PhysicalAnalysis(device="chain", mass_amu=138)
    with pytest.raises(KeyError, match="volts"):
        PhysicalAnalysis(device="chain", rf={"volts": 40.0})


def test_an_unknown_segment_names_some_that_exist():
    with pytest.raises(KeyError) as e:
        PhysicalAnalysis(device="chain", mass_u=BA138, segment="nope").run()
    assert "nope" in str(e.value) and "chain72" in str(e.value)


def test_above_q_max_the_frequency_is_nan_and_the_q_is_reported():
    """A secular frequency quoted outside the pseudopotential's range looks like an answer.

    At 400 V the Mathieu q of this geometry is past 0.4, where the pseudopotential is not
    a good approximation to the Mathieu solution.  The analysis reports the q -- which is
    the actionable number, because it tells the architect to lower the drive -- and NaN
    for everything derived from it.
    """
    data = PhysicalAnalysis(device="chain", mass_u=BA138,
                            rf={"voltage_v": 400.0}).run()
    assert data["mathieu_q"] > 0.4
    assert math.isnan(data["omega_radial_mhz"])
    assert any("exceeds q_max" in n for n in data["notes"])
    # and just under it, a real number comes back
    ok = PhysicalAnalysis(device="chain", mass_u=BA138,
                          rf={"voltage_v": 40.0}).run()
    assert ok["mathieu_q"] < 0.4 and ok["omega_radial_mhz"] > 0.0


def test_raising_q_max_lets_the_number_through_because_it_is_a_declared_limit():
    data = PhysicalAnalysis(device="chain", mass_u=BA138,
                            rf={"voltage_v": 400.0}, q_max=2.0).run()
    assert math.isfinite(data["omega_radial_mhz"]) and data["omega_radial_mhz"] > 0.0


# ------------------------------------------------------------------ the contract

def test_it_is_registered_and_describable_without_knowing_what_it_is():
    assert ANALYSES["field"] is PhysicalAnalysis
    assert get_analysis("field") is PhysicalAnalysis
    d = PhysicalAnalysis.describe()
    assert d["name"] == "PhysicalAnalysis" and d["summary"]
    assert set(d["data_labels"]) == set(PhysicalAnalysis.data_labels)
    for key in ("device", "tech", "mass_u", "rail_length_over_h", "q_max"):
        assert key in d["setup"]


def test_every_declared_output_is_produced(paper_run):
    _, data = paper_run
    assert set(data) == set(PhysicalAnalysis.data_labels)


def test_the_same_setup_gives_the_same_numbers():
    one = PhysicalAnalysis(device="chain", mass_u=BA138).run()
    two = PhysicalAnalysis(device="chain", mass_u=BA138).run()
    for label in PhysicalAnalysis.data_labels:
        if label == "notes":
            assert one[label] == two[label]
        else:
            assert one[label] == two[label] or (math.isnan(one[label])
                                                and math.isnan(two[label]))


def test_registering_it_did_not_disturb_the_analyses_that_were_there():
    assert sorted(ANALYSES) == ["budget", "field", "reach"]
    for key in ("reach", "budget"):
        d = ANALYSES[key].describe()
        assert d["data_labels"] and d["summary"]


# ------------------------------------------------------- the confinement convention

def test_the_confinement_unit_matches_the_papers_own_arithmetic():
    """`sum_i m omega_i^2 = q grad^2 phi_PP`, checked against a sentence in the paper.

    2201.12579 says that at 0.07 meV/um^2, pinning one mode at 1.5 MHz allows 1.06 MHz on
    the other two.  Doing that sum here gives 1.008 MHz -- the difference is the rounding
    of the printed 0.07, which needs to be 0.0735 to give exactly 1.06.  Agreement to the
    precision of the quoted figure is what confirms the reading of the unit, and the unit
    is what `confinement_meV_per_um2` converts to.
    """
    mass = 39.9626 * ATOMIC_MASS_KG
    total = PAPER_CONFINEMENT * 1e-3 * ELEMENTARY_CHARGE_C / (1e-6 ** 2)
    diag = ((total / 3, 0.0, 0.0), (0.0, total / 3, 0.0), (0.0, 0.0, total / 3))
    assert confinement_meV_per_um2(diag) == pytest.approx(PAPER_CONFINEMENT, rel=1e-12)

    w1 = 2 * math.pi * 1.5e6
    rest = (total / mass - w1 * w1) / 2
    assert math.sqrt(rest) / (2 * math.pi) / 1e6 == pytest.approx(1.008, abs=0.005)
