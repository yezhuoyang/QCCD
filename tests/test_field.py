"""The field kernel, checked against a paper that published the same model's answer.

Every physics risk in the physical-electrode layer is in two places: the four-corner
alternating sum in `rect_potential`/`rect_field`, and the bracketed solve in `rf_null`.
Nothing downstream -- shapes, technology, GDS, the analysis registry -- can be right if
those are wrong, and all of it is plumbing if they are right.  So they are checked here,
alone, before any of it exists.

**The oracle is not arithmetic.**  2201.12579 sizes its linear sections with the *two
dimensional* closed form for infinite strips -- `w_g = 0.83h`, `w_RF = (4h^2-w_g^2)/(2w_g)`,
tabulated as 41.5 um and 99.5 um for `h = 50` um (`ms.tex:283-288`).  Inverted, that says
`h = 1/2 sqrt(w_g (w_g + 2 w_RF))` = 49.9519 um.  This module feeds the solver *only* the
two widths and asks where the null is; the answer comes back from a three-dimensional sum
over finite rectangles with the height found by bisection on `E_z`.  Two different models,
one number.  A wrong sign or a wrong denominator in `rect_field` does not survive it.

**Tolerances are quoted, not chosen.**  2201.12579 measured its own model against FEM of
the fabricated trap: minimum positions within **5%**, worst-case pseudopotential ~**20%**
near the junction centre (`ms.tex:200-208`), FEM heights lower by ~2.7 um and, with 1 um
gaps, 0.1% in confinement at `h = 50` um (`ms.tex:378-383`).  The 5% band on a 50 um height
is +/- 2.5 um.  The assertion below is +/- 0.05 um, fifty times tighter, because it is
comparing two evaluations of one model rather than the model against reality.

**One measured departure from the design note.**  The build plan predicted the rectangle
sum would agree with the strip closed form "better than 1e-4 by L/h = 100".  It does not:
the error is `O(1/L^2)` with a larger constant, and at `L/h = 100` the null height is
7.9e-4 out, crossing 1e-4 at about `L/h = 282`.  The convergence itself is exactly as
predicted -- monotone, second order, and the `1/L^2` extrapolation from two lengths lands
on the closed form to 3e-8 -- so what moved is the length at which a threshold is met, not
the physics.  The tests below assert the *order* and extrapolate, which is the sharper
claim, and quote the measured errors rather than a rounder number.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.phys.field import (  # noqa: E402
    ATOMIC_MASS_KG,
    ELEMENTARY_CHARGE_C,
    Rect,
    pseudopotential,
    pseudopotential_hessian,
    rect_field,
    rect_potential,
    rf_field,
    rf_null,
    rf_potential,
    secular_frequencies,
    strip_field,
    strip_field_gradient,
    strip_null_height,
    strip_potential,
)

#: 2201.12579 tab:junction_linear, the optimal linear-arm RF geometry for h = 50 um.
W_G_M = 41.5e-6
W_RF_M = 99.5e-6
#: The height those two widths imply, in the same closed form the paper sized them with.
H_M = strip_null_height(W_G_M, W_RF_M)

#: 2201.12579 ms.tex:200-208 -- the model's own published error against FEM.
PAPER_POSITION_BAND = 0.05

#: A plausible drive for a surface trap of this size.  Nothing is asserted about these
#: two; they scale the pseudopotential and cancel out of every comparison below.
DRIVE_V = 200.0
OMEGA_RF = 2.0 * math.pi * 40e6
#: 138-Ba, the most abundant isotope; the mass is passed explicitly everywhere.
BA138_KG = 137.905247 * ATOMIC_MASS_KG


def rails(length_m: float, w_g: float = W_G_M, w_rf: float = W_RF_M
          ) -> tuple[Rect, Rect]:
    """The two RF rails of a linear section, flanking `x = 0`, centred on the origin.

    Six lines, deliberately here and not in `qccd/phys/`: when `build_layout` grows the
    same geometry out of integer nanometres it must be checked *against* this, and a
    shared helper would make that a tautology instead of a differential.
    """
    a, b = w_g / 2.0, w_g / 2.0 + w_rf
    return (Rect(a, -length_m / 2.0, b, length_m / 2.0),
            Rect(-b, -length_m / 2.0, -a, length_m / 2.0))


# ------------------------------------------------------- 1. the published fixed point

def test_the_papers_own_sizing_rule_reads_the_way_we_read_it():
    """Pin the arithmetic of `tab:junction_linear` before anything is built on it.

    `w_g = 0.83h` at `h = 50` um is 41.5 um exactly.  The companion expression
    `w_RF = (4h^2 - w_g^2)/(2 w_g)` then gives **99.73** um, while the table prints
    **99.5** (and the row's own approximation, `1.99h`).  That 0.23% is the paper's, not
    ours -- it says the two entries agree with Mokhberi 2017 "to within 99.5% with the
    slight discrepancy due to numerical truncation errors" (`ms.tex:283-288`).  We take the
    tabulated 99.5, because the tabulated pair is what the paper fabricated.
    """
    h = 50e-6
    assert 0.83 * h == pytest.approx(W_G_M, rel=1e-12)
    from_formula = (4.0 * h * h - W_G_M * W_G_M) / (2.0 * W_G_M)
    assert from_formula * 1e6 == pytest.approx(99.7319, abs=1e-4)
    assert abs(from_formula - W_RF_M) / W_RF_M == pytest.approx(2.33e-3, rel=0.05)


def test_the_closed_form_inverts_to_the_height_the_paper_designed_for():
    """`h = 1/2 sqrt(w_g (w_g + 2 w_RF))` on the tabulated widths, before any solver."""
    assert H_M * 1e6 == pytest.approx(49.9519, abs=1e-4)
    # and it really is a zero of the two-dimensional field, not a formula we like
    assert abs(strip_field(W_G_M, W_RF_M, 0.0, H_M)[1]) < 1e-9
    # within the paper's own 5% band of its stated design height of 50 um
    assert abs(H_M - 50e-6) / 50e-6 < 0.05


@pytest.mark.parametrize("length_over_h", [100, 200, 400])
def test_the_solver_finds_the_published_ion_height_from_the_two_widths_alone(
        length_over_h):
    """**The headline check.**  Two widths in, 49.95 um +/- 0.05 out.

    The solver is told 41.5 um and 99.5 um and nothing else.  It tiles two finite
    rectangles, sums the three-dimensional four-corner kernel over them, and bisects `E_z`.
    The target is the two-dimensional closed form the paper sized its trap with -- a
    different model, evaluated a different way.  Agreement to 0.05 um is fifty times
    inside the 5% position band the paper publishes for this model against FEM.
    """
    result = rf_null(rails(length_over_h * H_M), 0.0, (5e-6, 300e-6))
    assert result.found, result.reason
    assert result.z * 1e6 == pytest.approx(49.95, abs=PAPER_POSITION_BAND)


# --------------------------------------------------- 2. convergence, not tolerance

def _null_height(length_over_h: float) -> float:
    r = rf_null(rails(length_over_h * H_M), 0.0, (5e-6, 300e-6))
    assert r.found, r.reason
    assert r.z is not None
    return r.z


def _loglog_slope(xs, ys) -> float:
    n = len(xs)
    lx = [math.log(v) for v in xs]
    ly = [math.log(v) for v in ys]
    mx, my = sum(lx) / n, sum(ly) / n
    return (sum((lx[i] - mx) * (ly[i] - my) for i in range(n))
            / sum((lx[i] - mx) ** 2 for i in range(n)))


def test_the_rectangle_sum_converges_monotonically_to_the_strip_closed_form():
    """Lengthening the rails must walk the answer *towards* the infinite-strip result.

    Monotonicity is the part with teeth.  A kernel with a wrong corner term can still land
    near the right number at one length by luck; it cannot approach the closed form from
    one side at every length in a sweep.  The truncated rail is missing metal beyond each
    end, which lowers the null, so the sequence must increase and never overshoot.
    """
    lengths = list(range(10, 401, 10))
    heights = [_null_height(lo) for lo in lengths]
    for i in range(1, len(heights)):
        assert heights[i] > heights[i - 1], (
            f"L/h={lengths[i]} gave {heights[i] * 1e6:.6f} um, which is not above "
            f"L/h={lengths[i - 1]}'s {heights[i - 1] * 1e6:.6f} um")
    assert all(h < H_M for h in heights), "a finite rail cannot exceed the infinite strip"
    assert heights[-1] < H_M and H_M - heights[-1] < 3e-9


def test_the_truncation_error_is_second_order_in_the_rail_length():
    """The convergence *order*, which is what tests the corner terms.

    An absolute threshold at one length is a number that can be met by a wrong kernel with
    a compensating error.  A clean `1/L^2` over a decade cannot be: it is the signature of
    the leading finite-length correction to the four-corner sum, and a dropped or
    mis-signed term changes the exponent, not just the constant.
    """
    lengths = [25, 50, 100, 200, 400]
    errs = [abs(_null_height(lo) - H_M) / H_M for lo in lengths]
    slope = _loglog_slope(lengths, errs)
    assert slope == pytest.approx(-2.0, abs=0.05), (
        f"expected the truncation error to fall as 1/L^2; log-log slope was {slope}")
    # The measured errors, recorded so a change is visible rather than merely allowed.
    # The build plan predicted <1e-4 at L/h=100; it is 7.9e-4, crossing 1e-4 near L/h=282.
    assert errs[lengths.index(100)] == pytest.approx(7.95e-4, rel=0.02)
    assert errs[lengths.index(400)] == pytest.approx(4.98e-5, rel=0.02)
    assert errs[-1] < 1e-4


def test_extrapolating_the_truncation_away_lands_on_the_closed_form():
    """Two finite-rail solves, one Richardson step, and the 2D closed form falls out.

    If the error is `c/L^2` then `(4 h(2L) - h(L)) / 3` cancels it.  Doing that to two
    independent bisection results and recovering `1/2 sqrt(w_g(w_g + 2 w_RF))` to eight
    digits is the strongest statement available here that the three-dimensional kernel and
    the two-dimensional closed form are the same physics: nothing about the extrapolation
    knows the target.
    """
    z_200, z_400 = _null_height(200), _null_height(400)
    extrapolated = (4.0 * z_400 - z_200) / 3.0
    assert abs(extrapolated - H_M) / H_M < 1e-6
    assert abs(extrapolated - H_M) / H_M == pytest.approx(3.47e-8, rel=0.10)


@pytest.mark.parametrize("point,label", [
    ((0.0, 0.0, 0.5 * H_M), "on axis, below the null"),
    ((0.0, 0.0, 2.0 * H_M), "on axis, above the null"),
    ((0.5 * H_M, 0.0, H_M), "off axis, at the null height"),
])
def test_the_potential_converges_to_the_strip_everywhere_not_just_at_the_null(point, label):
    """The null is one point; the kernel has to be right on the whole plane."""
    x, y, z = point
    target = strip_potential(W_G_M, W_RF_M, x, z)
    errs = [abs(rf_potential(rails(lo * H_M), x, y, z) - target) / abs(target)
            for lo in (50, 100, 200, 400)]
    assert _loglog_slope([50, 100, 200, 400], errs) == pytest.approx(-2.0, abs=0.05), label
    assert errs[-1] < 1e-4, label


# ------------------------------------------------- the analytic gradient, and its order

def _gradient_order(field_fn, potential_fn, point, base_h=1e-6, n=5) -> float:
    """Log-log slope of |analytic - central difference| against the step size."""
    analytic = field_fn(*point)
    scale = max(abs(c) for c in analytic)
    steps, errs = [], []
    for k in range(n):
        h = base_h / (2 ** k)
        numeric = []
        for i in range(3):
            hi = list(point)
            hi[i] += h
            lo = list(point)
            lo[i] -= h
            numeric.append(-(potential_fn(*hi) - potential_fn(*lo)) / (2.0 * h))
        steps.append(h)
        errs.append(max(abs(numeric[i] - analytic[i]) for i in range(3)) / scale)
    return _loglog_slope(steps, errs)


def test_the_analytic_gradient_is_the_gradient_of_the_analytic_potential():
    """The only test that can catch a wrong term in `rect_field`.

    `rect_field` is hand-differentiated: four corners, three components, a denominator
    identity used to collapse `A^2 + B^2`.  Nothing else in this package would notice a
    sign slip in one of those twelve expressions -- the null height barely moves if `E_x`
    is wrong, because the null is found on `E_z`.  Differencing the potential does notice,
    and the *order* of the agreement is the assertion: central differences are `O(h^2)`
    against the true gradient and `O(1)` against a wrong one.
    """
    rect = Rect(-4e-5, -7e-5, 9e-5, 3e-5)
    point = (1.3e-5, -2.1e-5, 3.7e-5)
    slope = _gradient_order(lambda *p: rect_field(rect, *p),
                            lambda *p: rect_potential(rect, *p), point)
    assert slope == pytest.approx(2.0, abs=0.05), f"log-log slope was {slope}"


def test_the_gradient_order_holds_for_the_whole_rf_net_off_axis():
    """Same check on the summed net at a point where all three components are non-zero.

    The point has to be near a rail *end*: anywhere in the middle of a long rail `E_y` is
    zero to fifteen digits by translational symmetry, and a check on a component that is
    identically zero is a check on nothing.  At `y = 199 h` of a `200 h` half-length, all
    three are thousands of volts per metre.
    """
    rs = rails(400 * H_M)
    point = (0.6 * H_M, 199 * H_M, 0.9 * H_M)
    assert all(abs(c) > 1e3 for c in rf_field(rs, *point)), "the check would be vacuous"
    slope = _gradient_order(lambda *p: rf_field(rs, *p),
                            lambda *p: rf_potential(rs, *p), point)
    assert slope == pytest.approx(2.0, abs=0.05), f"log-log slope was {slope}"


@pytest.mark.parametrize("component,factor", [(0, -1.0), (1, 1.5), (2, 0.999)])
def test_the_order_fit_collapses_on_a_planted_error(component, factor):
    """The order test must fail for a wrong field, or it proves nothing.

    A flipped sign, a 50% scale error and a 0.1% scale error, one component at a time.
    All three must break the second-order fit; the 0.1% one is the interesting case,
    because it is small enough that a loose absolute tolerance would wave it through.
    """
    rect = Rect(-4e-5, -7e-5, 9e-5, 3e-5)
    point = (1.3e-5, -2.1e-5, 3.7e-5)

    def wrong(*p):
        e = list(rect_field(rect, *p))
        e[component] *= factor
        return tuple(e)

    slope = _gradient_order(wrong, lambda *p: rect_potential(rect, *p), point)
    assert abs(slope - 2.0) > 0.5, (
        f"scaling component {component} by {factor} still fit at order {slope}")


# ------------------------------------------------------------ identities, cheap and weak

def test_a_rectangle_that_covers_the_plane_subtends_the_whole_half_space():
    """`Phi -> 1` when the electrode is everything: the normalisation of `Omega / 2pi`.

    A finite plate leaves a residual of about `z / L`, which is the same truncation the
    rails show, so the plate is sized against the field height rather than in absolute
    metres.  At `z/L = 1e-14` the sum returns 1 to fourteen digits.
    """
    plane = Rect(-1e9, -1e9, 1e9, 1e9)
    for z in (1e-6, 1e-5):
        assert rect_potential(plane, 0.3 * z, -0.7 * z, z) == pytest.approx(1.0, rel=1e-12)
    # and the residual really is first order in z/L, not a coincidence of one scale
    far = 1.0 - rect_potential(plane, 0.0, 0.0, 1e-3)
    near = 1.0 - rect_potential(plane, 0.0, 0.0, 1e-4)
    assert far / near == pytest.approx(10.0, rel=0.05)


def test_superposition_under_partition_is_exact_but_is_not_the_oracle():
    """A decomposition check, included for what it is and labelled for what it is not.

    Splitting a rectangle into four and summing returns the original to the last bit.
    That is worth having -- it would catch a corner-indexing slip in the loop -- but it is
    **not** evidence the kernel is right: *any* four-corner alternating sum is identically
    additive under splitting, correct or not, because the interior edges appear twice with
    opposite signs.  The oracle is the closed-form comparison above.
    """
    whole = Rect(-3e-5, -2e-4, 8e-5, 5e-4)
    p = (1e-5, 3e-5, 4e-5)
    xs = (whole.x0, 1.7e-5, whole.x1)
    ys = (whole.y0, 1.1e-4, whole.y1)
    parts = [Rect(xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(2) for j in range(2)]

    assert rect_potential(whole, *p) == math.fsum(rect_potential(q, *p) for q in parts)
    ref = rect_field(whole, *p)
    got = [math.fsum(rect_field(q, *p)[k] for q in parts) for k in range(3)]
    for k in range(3):
        assert got[k] == pytest.approx(ref[k], rel=1e-12)


def test_the_potential_is_scale_invariant_and_the_field_carries_one_over_length():
    """`Phi` is dimensionless; `E` is not.  Getting this backwards would be a unit bug."""
    lam = 1024.0  # a power of two, so the scaling itself introduces no rounding
    rect = Rect(-4e-5, -7e-5, 9e-5, 3e-5)
    big = Rect(rect.x0 * lam, rect.y0 * lam, rect.x1 * lam, rect.y1 * lam)
    p = (1.3e-5, -2.1e-5, 3.7e-5)
    bp = tuple(c * lam for c in p)

    assert rect_potential(big, *bp) == rect_potential(rect, *p)
    small_e = rect_field(rect, *p)
    big_e = rect_field(big, *bp)
    for k in range(3):
        assert big_e[k] * lam == pytest.approx(small_e[k], rel=1e-12)


# --------------------------------------------------------------- 3. the null certificate

def test_the_null_on_axis_is_certified_by_a_vanishing_residual():
    """`found` is not a claim that bisection converged; it is a claim that `|E|` is zero."""
    r = rf_null(rails(400 * H_M), 0.0, (5e-6, 300e-6))
    assert r.found and r.reason == "ok"
    assert r.residual < 1e-12, r.as_dict()
    assert r.reference_v_per_m > 0.0
    assert r.iterations > 30, "a converged bisection over this bracket takes ~50 halvings"


@pytest.mark.parametrize("x_um,expect_z_um,expect_residual", [
    (2.0, 49.83, 3.41e-2),
    (5.0, 49.19, 8.41e-2),
    (10.0, 46.76, 1.62e-1),
])
def test_a_zero_of_ez_off_the_axis_is_refused_however_plausible_it_looks(
        x_um, expect_z_um, expect_residual):
    """The failure mode the certificate exists for, and it is not a contrived one.

    Two micrometres off the trap axis, `E_z` still changes sign, bisection still converges,
    and the height it returns is 49.83 um -- indistinguishable, in a report, from the
    49.95 um that is the real answer.  It is not an ion position: `E_x` does not vanish
    there, so the ion is pushed sideways.  Without the residual this module would hand back
    a number that is wrong by nothing you could see and by everything that matters.
    """
    r = rf_null(rails(400 * H_M), x_um * 1e-6, (5e-6, 300e-6))
    assert r.z is not None and r.z * 1e6 == pytest.approx(expect_z_um, abs=0.01)
    assert r.residual == pytest.approx(expect_residual, rel=0.02)
    assert not r.found
    assert "not an RF null" in r.reason


def test_a_bracket_with_no_sign_change_is_refused_and_says_so():
    """No root in the bracket, and no silent nearest-stationary-point fallback."""
    r = rf_null(rails(400 * H_M), 0.0, (60e-6, 300e-6))
    assert not r.found and r.z is None
    assert "does not change sign" in r.reason
    assert math.isnan(r.residual)


@pytest.mark.parametrize("bracket", [(0.0, 1e-4), (-1e-6, 1e-4), (2e-4, 1e-4),
                                     (1e-4, 1e-4)])
def test_a_malformed_bracket_raises_rather_than_being_repaired(bracket):
    with pytest.raises(ValueError, match="bracket"):
        rf_null(rails(100 * H_M), 0.0, bracket)


def test_asymmetric_rails_move_the_null_and_it_is_still_certified():
    """Widening one rail shifts the null sideways and up; the certificate follows it.

    The symmetric case can be passed by a kernel with an even-in-`x` error, because the
    two rails cancel it.  Here they do not.
    """
    a, b = W_G_M / 2.0, W_G_M / 2.0 + W_RF_M
    half = 200 * H_M
    asym = (Rect(a, -half, b, half), Rect(-b - 3e-5, -half, -a, half))

    # find the transverse position where E_x vanishes, then certify the null over it
    lo, hi = -4e-5, 4e-5
    sign0 = None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        z = rf_null(asym, mid, (5e-6, 300e-6)).z
        assert z is not None
        ex = rf_field(asym, mid, 0.0, z)[0]
        if sign0 is None:
            sign0 = ex > 0.0
        if (ex > 0.0) == sign0:
            lo = mid
        else:
            hi = mid
    x_null = 0.5 * (lo + hi)

    r = rf_null(asym, x_null, (5e-6, 300e-6))
    assert r.found, r.reason
    assert r.residual < 1e-12
    assert x_null * 1e6 == pytest.approx(2.718, abs=0.01), "the null moved off axis"
    assert r.z * 1e6 == pytest.approx(52.52, abs=0.01), "and up, as more metal implies"


# ------------------------------------------------- the transverse solve, and its refusals

def test_the_transverse_solve_finds_the_same_null_the_bisection_does():
    """On a symmetric rail the two agree, which is the only place they should be compared.

    `rf_null` is right wherever symmetry already puts the null on the given axis; the
    transverse solve is for everywhere else.  Where both apply they must not disagree.
    """
    from qccd.phys.field import transverse_null
    rs = rails(400 * H_M)
    one_d = rf_null(rs, 0.0, (5e-6, 300e-6))
    two_d = transverse_null(rs, "y", 0.0, (0.0, H_M))
    assert two_d.found and one_d.found
    assert two_d.z == pytest.approx(one_d.z, rel=1e-9)
    assert two_d.y == pytest.approx(0.0, abs=1e-12), "symmetry puts it on the axis"
    assert two_d.iterations < 10, "Newton, not a nested bisection"


def test_the_transverse_solve_converges_from_a_guess_well_below_the_null():
    from qccd.phys.field import transverse_null
    rs = rails(400 * H_M)
    assert transverse_null(rs, "y", 0.0, (0.0, 5e-6)).z == pytest.approx(
        _null_height(400), rel=1e-9)


def test_a_single_driven_rail_has_no_null_and_the_solve_says_so():
    """The certificate's reason for existing: Newton always returns *something*.

    One electrode at one potential has no RF null anywhere above it.  Newton does not know
    that -- it wanders off to 7.5 mm and reports a converged answer -- and only the
    residual turns that into a refusal.  Without the check this would be an ion height.
    """
    from qccd.phys.field import transverse_null
    one = (Rect(-1e-4, -5e-3, 1e-4, 5e-3),)
    got = transverse_null(one, "y", 0.0, (0.0, 50e-6))
    assert not got.found
    assert "transverse field does not vanish" in got.reason
    assert got.z > 1e-3, "and it had wandered a long way to get there"


def test_a_guess_outside_the_trap_runs_away_and_is_refused():
    from qccd.phys.field import transverse_null
    rs = rails(400 * H_M)
    got = transverse_null(rs, "y", 0.0, (400e-6, H_M))
    assert not got.found and got.residual > 1e-6


def test_the_transverse_solve_refuses_an_axis_it_does_not_know():
    from qccd.phys.field import transverse_null
    with pytest.raises(ValueError, match="runs along 'x' or 'y'"):
        transverse_null(rails(100 * H_M), "z", 0.0, (0.0, H_M))


# --------------------------------------------------------------------- 4. the curvature

def _predicted_curvature() -> float:
    """`H_xx = H_zz = (q V |E'|)^2 / (2 m Omega^2)` for the two-strip quadrupole.

    Near the null of an infinite two-strip trap the field is `E = |E'| (dx, 0, -dz)`, so
    `|E|^2 = |E'|^2 (dx^2 + dz^2)` and the pseudopotential is an isotropic radial
    parabola with no axial term at all.
    """
    e_prime = DRIVE_V * strip_field_gradient(W_G_M, W_RF_M)
    return (ELEMENTARY_CHARGE_C * e_prime) ** 2 / (2.0 * BA138_KG * OMEGA_RF ** 2)


def test_the_hessian_reproduces_the_analytic_quadrupole():
    """Curvature, against the closed form -- and the two things it must *not* find.

    `H_xx = H_zz` says the pseudopotential of a two-strip trap is radially isotropic, which
    is the whole reason a linear surface trap needs DC electrodes to break the degeneracy.
    `H_yy = 0` says an infinite rail confines nothing along its own axis -- so any axial
    frequency this platform ever reports has to come from somewhere else, which is exactly
    why axial confinement is out of scope here.
    """
    rs = rails(400 * H_M)
    z = _null_height(400)
    h = pseudopotential_hessian(rs, 0.0, 0.0, z, voltage_v=DRIVE_V,
                                omega_rf_rad_s=OMEGA_RF, mass_kg=BA138_KG)
    predicted = _predicted_curvature()

    assert h.matrix[0][0] == pytest.approx(predicted, rel=1e-3)
    assert h.matrix[2][2] == pytest.approx(predicted, rel=1e-3)
    assert h.matrix[0][0] == pytest.approx(h.matrix[2][2], rel=1e-6), "radially isotropic"
    assert abs(h.matrix[1][1]) < 1e-8 * predicted, "an infinite rail has no axial trap"
    for i in range(3):
        for j in range(3):
            if i != j:
                assert abs(h.matrix[i][j]) < 1e-8 * predicted, "principal axes are x, y, z"


@pytest.mark.parametrize("divisor", [50, 100, 500, 1000])
def test_the_remaining_hessian_error_is_the_finite_rail_and_not_the_step(divisor):
    """Where the 3e-4 comes from -- and that it is physics, not differencing.

    The relative error against the closed form is flat at 2.99e-4 across four decades of
    step size, so the numerical second derivative contributes nothing measurable to it.
    Lengthening the rails moves it, and moves it as `1/L^2`, so it is the same finite-rail
    truncation the null height shows.  This is what distinguishes "the extrapolation is
    fine" from "the step happens to be lucky".
    """
    rs = rails(400 * H_M)
    z = _null_height(400)
    h = pseudopotential_hessian(rs, 0.0, 0.0, z, voltage_v=DRIVE_V,
                                omega_rf_rad_s=OMEGA_RF, mass_kg=BA138_KG,
                                step_m=z / divisor)
    predicted = _predicted_curvature()
    assert abs(h.matrix[0][0] - predicted) / predicted == pytest.approx(2.99e-4, rel=0.05)
    # Richardson's own correction, meanwhile, falls as the square of the step
    assert h.richardson_delta == pytest.approx(5.25e-4 * (100.0 / divisor) ** 2, rel=0.05)


def test_lengthening_the_rails_removes_the_hessian_error_at_second_order():
    rs_and_z = [(rails(lo * H_M), _null_height(lo)) for lo in (400, 1000, 2000)]
    predicted = _predicted_curvature()
    errs = []
    for rs, z in rs_and_z:
        h = pseudopotential_hessian(rs, 0.0, 0.0, z, voltage_v=DRIVE_V,
                                    omega_rf_rad_s=OMEGA_RF, mass_kg=BA138_KG,
                                    step_m=z / 1000)
        errs.append(abs(h.matrix[0][0] - predicted) / predicted)
    assert _loglog_slope([400, 1000, 2000], errs) == pytest.approx(-2.0, abs=0.05)


def test_the_secular_frequency_matches_the_textbook_expression():
    """`omega = q V |E'| / (sqrt(2) m Omega)`, from the Hessian rather than from itself."""
    rs = rails(400 * H_M)
    z = _null_height(400)
    h = pseudopotential_hessian(rs, 0.0, 0.0, z, voltage_v=DRIVE_V,
                                omega_rf_rad_s=OMEGA_RF, mass_kg=BA138_KG)
    w_axial, w_r1, w_r2 = secular_frequencies(h.matrix, BA138_KG)

    e_prime = DRIVE_V * strip_field_gradient(W_G_M, W_RF_M)
    predicted = ELEMENTARY_CHARGE_C * e_prime / (math.sqrt(2.0) * BA138_KG * OMEGA_RF)
    assert w_r1 == pytest.approx(predicted, rel=1e-3)
    assert w_r2 == pytest.approx(predicted, rel=1e-3)
    assert w_axial < 1e-3 * w_r1, "no axial confinement from RF alone"
    assert predicted / (2.0 * math.pi) / 1e6 == pytest.approx(7.993, abs=0.01)

    # The Mathieu q this implies, which is the reason S6 needs a validity gate: the
    # pseudopotential approximation is quoted for q well below this.
    assert 2.0 * math.sqrt(2.0) * w_r2 / OMEGA_RF == pytest.approx(0.565, abs=0.005)


def test_an_unconfined_direction_reports_nan_and_not_zero():
    """A saddle is not a trap, and 0.0 rad/s reads as "no confinement needed"."""
    saddle = ((1e-9, 0.0, 0.0), (0.0, -4e-10, 0.0), (0.0, 0.0, 1e-9))
    got = secular_frequencies(saddle, BA138_KG)
    assert math.isnan(got[0])
    assert all(math.isfinite(v) and v > 0.0 for v in got[1:])


def test_the_eigen_solver_handles_a_rotated_degenerate_pair():
    """Two equal eigenvalues in a rotated basis -- the normal case for a radial trap."""
    c, s = math.cos(0.7), math.sin(0.7)
    lam = (3.0, 3.0, 8.0)
    rot = ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
    m = [[sum(rot[i][k] * lam[k] * rot[j][k] for k in range(3)) for j in range(3)]
         for i in range(3)]
    got = secular_frequencies(m, 1.0)
    assert [v * v for v in got] == pytest.approx([3.0, 3.0, 8.0], rel=1e-12)


# ------------------------------------------------------------------------ 5. refusals

def test_the_mass_has_no_default():
    """Every shipped architecture declares `"qubit": "Ba+"` with no mass anywhere."""
    with pytest.raises(TypeError, match="mass_kg"):
        pseudopotential(rails(100 * H_M), 0.0, 0.0, H_M,  # type: ignore[call-arg]
                        voltage_v=DRIVE_V, omega_rf_rad_s=OMEGA_RF)


@pytest.mark.parametrize("bad", [0.0, -1e-6, -1.0])
def test_the_electrode_plane_itself_is_refused(bad):
    """On `z = 0` the potential is the boundary condition and the field is singular."""
    r = Rect(-1e-5, -1e-5, 1e-5, 1e-5)
    for fn in (rect_potential, rect_field):
        with pytest.raises(ValueError, match="above the electrode plane"):
            fn(r, 0.0, 0.0, bad)


@pytest.mark.parametrize("bounds", [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 1.0, 0.0),
                                    (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.0)])
def test_a_reversed_or_zero_area_rectangle_is_a_bug_and_raises(bounds):
    with pytest.raises(ValueError, match="builder bug"):
        Rect(*bounds)


@pytest.mark.parametrize("kwargs", [{"mass_kg": 0.0}, {"mass_kg": -1.0},
                                    {"omega_rf_rad_s": 0.0}])
def test_a_nonsense_drive_or_mass_raises(kwargs):
    call = {"voltage_v": DRIVE_V, "omega_rf_rad_s": OMEGA_RF, "mass_kg": BA138_KG}
    call.update(kwargs)
    with pytest.raises(ValueError, match="must be positive"):
        pseudopotential(rails(100 * H_M), 0.0, 0.0, H_M, **call)


def test_the_pseudopotential_scales_the_way_its_definition_says():
    """`Psi ~ q^2 V^2 / (m Omega^2)`, checked rather than assumed from the docstring."""
    rs = rails(100 * H_M)
    base = dict(voltage_v=DRIVE_V, omega_rf_rad_s=OMEGA_RF, mass_kg=BA138_KG)
    p = (0.4 * H_M, 0.0, 1.2 * H_M)
    ref = pseudopotential(rs, *p, **base)
    assert pseudopotential(rs, *p, **{**base, "voltage_v": 2 * DRIVE_V}) == \
        pytest.approx(4.0 * ref, rel=1e-12)
    assert pseudopotential(rs, *p, **{**base, "omega_rf_rad_s": 2 * OMEGA_RF}) == \
        pytest.approx(0.25 * ref, rel=1e-12)
    assert pseudopotential(rs, *p, **{**base, "mass_kg": 2 * BA138_KG}) == \
        pytest.approx(0.5 * ref, rel=1e-12)


# ------------------------------------------------- 6. discipline 1, asserted permanently

#: Every `cases.<name>` the parity harness reads.  Frozen: the field kernel must never
#: acquire a mirror, so it must never acquire a differential bucket either.
PARITY_CASE_KEYS = frozenset({
    "browser_set", "build", "build_vocabulary", "classes", "edit_js", "engine",
    "generators", "hardware", "json", "layouts", "lint", "marks", "mutate", "pricing",
    "prog", "prog_frame_fields", "program_methods", "programs", "progtext", "refusals",
    "reprs", "rules", "schema", "schema_blob", "schema_version", "sources", "strings",
    "template_default", "templates",
})

BROWSER_FILES = ("qccd/viz/engine.js", "qccd/viz/js/edit.js", "qccd/viz/js/editor.js")


def test_no_symbol_of_the_field_kernel_reaches_the_browser():
    """A test that a second implementation was *not* written.

    This project has already paid for one quantity computed on both sides of the
    Python/JavaScript line: an operand renderer written twice disagreed on 3,830 of 3,830
    rows.  The rule since is that a shared quantity is proved by an exhaustive differential
    at tolerance zero or it is not written twice -- and a transcendental field solver
    cannot be differentialled at tolerance zero, because `atan2` is not required to agree
    to the ulp between CPython and V8.  So the kernel is Python-only, permanently, and the
    cheapest way to keep it that way is to assert it.
    """
    from qccd.phys import field as kernel

    # positive control: a guard that cannot fire is not a guard.  `Rect` must not match
    # `getBoundingClientRect` (it does not -- the leading word boundary fails), and must
    # match a real mirror.
    assert len(kernel.__all__) > 10
    planted = "function rf_field(rects, x, y, z) { return pseudopotential(rects); }"
    assert any(re.search(r"\b" + re.escape(n) + r"\b", planted) for n in kernel.__all__)
    assert not re.search(r"\bRect\b", "el.getBoundingClientRect().width")

    for rel in BROWSER_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        for name in kernel.__all__:
            assert not re.search(r"\b" + re.escape(name) + r"\b", src), (
                f"{name!r} from qccd/phys/field.py appears in {rel}; the field kernel has "
                f"grown a mirror that nothing can diff at tolerance zero")


def test_the_engine_has_no_trigonometry_to_build_a_field_solver_out_of():
    """`engine.js` is the mirrored half.  A field kernel needs `atan2`; it has none.

    Placement in this project is quarter-turns on integers for the same reason: one of the
    24 measured cos/sin values differs by 1 ulp between CPython and V8, and 2-5 ulp flips a
    segment's bow.  `editor.js` is exempt -- it is the un-mirrored studio shell and already
    uses `atan2` for pointer geometry, which no Python code recomputes.
    """
    src = (ROOT / "qccd/viz/engine.js").read_text(encoding="utf-8")
    for banned in ("Math.atan2", "Math.atan", "Math.cos", "Math.sin", "Math.tan"):
        assert banned not in src, (
            f"{banned} appeared in engine.js; the mirrored half has acquired the "
            f"transcendental arithmetic the field kernel is kept out of it to avoid")


def test_the_parity_harness_did_not_grow_a_bucket_for_the_field_kernel():
    """A new `cases.<name>` in parity.mjs would mean a JS mirror exists to diff."""
    src = (ROOT / "tests/parity.mjs").read_text(encoding="utf-8")
    found = frozenset(re.findall(r"\bcases\.([A-Za-z_][A-Za-z0-9_]*)", src))
    assert found == PARITY_CASE_KEYS, (
        f"parity buckets changed: added {sorted(found - PARITY_CASE_KEYS)}, "
        f"removed {sorted(PARITY_CASE_KEYS - found)}")


def test_the_field_kernel_imports_nothing_outside_the_standard_library():
    """No `shapely`, no `gdstk`, no `numpy` -- and `qccd/phys/` depends on no `qccd/`."""
    src = (ROOT / "qccd/phys/field.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+([\w.]+)", src, flags=re.M)
    assert set(imports) <= {"__future__", "math", "dataclasses", "typing"}, imports
