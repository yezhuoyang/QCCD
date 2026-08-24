"""The RF pseudopotential of a planar electrode set, in closed form.

Everything this platform calls an "electrode" today is a number somebody typed.
`control.wiring.electrodes_per_trap = 24` is an integer in a document; the pill tiling in
the browser derives its count from *drawn pixel length*.  Neither is falsifiable, because
neither is attached to a shape with coordinates.  This module is the bottom of the fix:
given rectangles of metal in a plane, it returns the field they make, and from that the
height at which an ion sits and how hard it is held there.  Those are numbers a
measurement can contradict.

**The model.**  Planar electrodes in the z = 0 plane, ion in z > 0.  Two assumptions,
taken verbatim from the paper this module is checked against:

    1. that there is no gap between electrodes;
    2. that the entire plane is covered by conducting electrodes.

    -- Library/papers/2201.12579__optimization-and-implementation-of/source/ms.tex:200-208

Under those assumptions the boundary-value problem has a closed-form solution per
rectangle, so no mesh, no BEM and no external solver appear anywhere in this package.  The
basis function is the solid angle the electrode subtends at the field point, over 2*pi:

    Phi_gamma(r) = V_gamma * Omega_gamma(r) / 2*pi

    -- Wesenberg, *Electrostatics of surface-electrode ion traps*, Phys. Rev. A **78**,
       063410 (2008) = arXiv:0808.1623, eq. `biotsavartpot`, in-corpus at
       `Library/papers/0808.1623__electrostatics-of-surfaceelectrode/source/`

The companion analytic treatment is House, *Analytic model for electrostatic fields in
surface-electrode ion traps*, Phys. Rev. A **78**, 033402 (2008), DOI
`10.1103/PhysRevA.78.033402` -- no arXiv preprint, recorded in
`Library/non_arxiv_ledger.csv` -- which is what 2201.12579 sizes its linear sections with
(`ms.tex:283-288`, citing `House2008`, in-corpus at `ms.bbl:325`).

**Wesenberg also states when the model is allowed to be used**, and one of the three
conditions is the reason `rail_length_over_h` is a knob rather than a constant:

    "necessary conditions for the gapless plane approximation to be valid include that
    gaps between electrodes are much smaller than d, that **the extent of the trap is
    much larger than d**, and that the distance from ion to other conducting surfaces is
    much larger than d"   -- `surfaceelectrodes.tex:325-329`, with `d` the ion height

A finite rail violates the second condition in proportion to `(h/L)^2`, and that is
exactly the convergence `tests/test_field.py` measures.

**What the model costs, in the paper's own numbers.**  2201.12579 built the same model,
then checked it against FEM of the fabricated trap:

    positions of pseudo-potential minima agree to within  5%
    worst-case pseudo-potential discrepancy near the junction centre  ~20%
                                                       -- ms.tex:200-208
    FEM ion heights lower by ~2.7 um; with 1 um gaps, 0.1% in confinement at h = 50 um
                                                       -- ms.tex:378-383

Those are the tolerances every test in `tests/test_field.py` is written against.  A
tolerance in this package is quoted from that table or it is not used.

**Gaps are declared, not modelled.**  Assumption 1 is false of any real trap -- 2201.12579
fabricates 5 um gaps, and the 5% / 20% above is what that costs.  A technology file may
declare a gap for design-rule checking; this kernel never sees it.

**Units.**  Metres, volts, kilograms, radians per second, and volts per metre out.  The
potential `Phi` is dimensionless per volt of electrode drive and is *scale invariant* --
scaling every rectangle and the field point by the same factor leaves it unchanged -- so a
potential-only check may use any consistent length unit.  A field may not: it carries 1/L.

**One implementation, in Python, forever.**  This project has been bitten once by the same
quantity computed on both sides of the Python/JavaScript line: an operand renderer written
twice disagreed on 3,830 of 3,830 rows.  Where both halves must agree it is now proved by
an exhaustive differential at tolerance zero, or it is not written twice.  A field solver
cannot be differentialled at tolerance zero -- transcendental functions do not agree to the
ulp across runtimes -- so it is not written twice.  `tests/test_field.py` asserts that no
symbol from this package reaches the browser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

__all__ = [
    "ELEMENTARY_CHARGE_C", "ATOMIC_MASS_KG",
    "Rect", "NullResult", "HessianResult",
    "rect_potential", "rect_field",
    "rf_potential", "rf_field",
    "pseudopotential", "pseudopotential_hessian",
    "rf_null", "transverse_null", "secular_frequencies",
    "strip_potential", "strip_field", "strip_null_height", "strip_field_gradient",
]

#: Exact by the 2019 SI redefinition of the ampere.  CODATA.
ELEMENTARY_CHARGE_C = 1.602176634e-19
#: Atomic mass constant, CODATA 2018 recommended value, 1.66053906660(50)e-27 kg.
ATOMIC_MASS_KG = 1.66053906660e-27

_TWO_PI = 2.0 * math.pi


# --------------------------------------------------------------------------- geometry

@dataclass(frozen=True)
class Rect:
    """One axis-aligned rectangle of metal in the z = 0 plane, in metres.

    Held at unit potential; the whole rest of the plane is grounded.  A drive voltage
    multiplies the result, because the problem is linear.

    Reversed or degenerate bounds raise rather than being silently normalised: a builder
    that emits `x1 < x0` has a bug, and quietly swapping them would hide it behind a
    plausible field.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if not (self.x1 > self.x0 and self.y1 > self.y0):
            raise ValueError(
                f"Rect needs x1 > x0 and y1 > y0, got "
                f"x=({self.x0!r}, {self.x1!r}) y=({self.y0!r}, {self.y1!r}); a reversed "
                f"or zero-area rectangle is a builder bug, not a shape.")

    @classmethod
    def centred(cls, cx: float, cy: float, width: float, height: float) -> "Rect":
        """The rectangle of `width` x `height` about `(cx, cy)`."""
        return cls(cx - width / 2.0, cy - height / 2.0,
                   cx + width / 2.0, cy + height / 2.0)

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)


def _check_z(z: float) -> None:
    if not (z > 0.0):
        raise ValueError(
            f"the gapless-plane solution is defined strictly above the electrode plane; "
            f"got z={z!r}. On the plane itself the potential is the boundary condition "
            f"and the field is singular at every electrode edge.")


# ------------------------------------------------------------------- the basis function

def rect_potential(rect: Rect, x: float, y: float, z: float) -> float:
    """Potential at `(x, y, z)` of one rectangle at unit potential, grounded elsewhere.

    `Phi = Omega / 2*pi`, where `Omega` is the solid angle the rectangle subtends at the
    field point, written as the four-corner alternating sum

        Omega = sum_ij (-1)^(i+j) atan2( X_i Y_j , z sqrt(X_i^2 + Y_j^2 + z^2) )

    with `X_i = x_i - x` and `Y_j = y_j - y` over the rectangle's two x- and two y-edges.
    A rectangle grown to cover the whole plane subtends `2*pi` and returns exactly 1.
    """
    _check_z(z)
    zz = z * z
    total = 0.0
    for xi, si in ((rect.x0, -1.0), (rect.x1, 1.0)):
        big_x = xi - x
        for yj, sj in ((rect.y0, -1.0), (rect.y1, 1.0)):
            big_y = yj - y
            r = math.sqrt(big_x * big_x + big_y * big_y + zz)
            total += si * sj * math.atan2(big_x * big_y, z * r)
    return total / _TWO_PI


def rect_field(rect: Rect, x: float, y: float, z: float) -> tuple[float, float, float]:
    """`E = -grad Phi` for one rectangle, analytically, in volts per metre per volt.

    Differentiating the four-corner sum term by term and using the identity

        A^2 + B^2 = (X_i^2 + z^2)(Y_j^2 + z^2)     with A = X_i Y_j, B = z R

    collapses every denominator, leaving three sums with no cancellation beyond the
    alternating sign the potential already carries:

        E_x = 1/2pi sum_ij s_ij  z Y_j / ( R (X_i^2 + z^2) )
        E_y = 1/2pi sum_ij s_ij  z X_i / ( R (Y_j^2 + z^2) )
        E_z = 1/2pi sum_ij s_ij  X_i Y_j (R^2 + z^2) / ( R (X_i^2 + z^2)(Y_j^2 + z^2) )

    This is the only place in the package where a derivative is taken by hand, so it is
    the only place a sign error would be invisible.  `tests/test_field.py` fits the
    convergence order of central differences against it and asserts the slope is 2; a
    wrong term gives slope 0.
    """
    _check_z(z)
    zz = z * z
    ex = ey = ez = 0.0
    for xi, si in ((rect.x0, -1.0), (rect.x1, 1.0)):
        big_x = xi - x
        dx = big_x * big_x + zz
        for yj, sj in ((rect.y0, -1.0), (rect.y1, 1.0)):
            big_y = yj - y
            dy = big_y * big_y + zz
            r = math.sqrt(big_x * big_x + big_y * big_y + zz)
            s = si * sj
            ex += s * z * big_y / (r * dx)
            ey += s * z * big_x / (r * dy)
            ez += s * big_x * big_y * (r * r + zz) / (r * dx * dy)
    return (ex / _TWO_PI, ey / _TWO_PI, ez / _TWO_PI)


# ------------------------------------------------------------------------ the RF net

def rf_potential(rects: Sequence[Rect], x: float, y: float, z: float,
                 *, voltage_v: float = 1.0) -> float:
    """Potential of every rectangle on the RF net, all driven together."""
    return voltage_v * math.fsum(rect_potential(r, x, y, z) for r in rects)


def rf_field(rects: Sequence[Rect], x: float, y: float, z: float,
             *, voltage_v: float = 1.0) -> tuple[float, float, float]:
    """`E` of every rectangle on the RF net, all driven together, in V/m.

    Summed with `fsum`, because the four-corner terms of a distant rectangle nearly
    cancel and a rail is tiled from many of them.
    """
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for r in rects:
        ex, ey, ez = rect_field(r, x, y, z)
        xs.append(ex)
        ys.append(ey)
        zs.append(ez)
    return (voltage_v * math.fsum(xs), voltage_v * math.fsum(ys),
            voltage_v * math.fsum(zs))


def pseudopotential(rects: Sequence[Rect], x: float, y: float, z: float, *,
                    voltage_v: float, omega_rf_rad_s: float, mass_kg: float,
                    charge_c: float = ELEMENTARY_CHARGE_C) -> float:
    """The ponderomotive pseudopotential, in joules.

        Psi = q^2 |E_rf|^2 / (4 m Omega^2)

    `mass_kg` has no default on purpose.  Every architecture file this platform ships
    declares its qubit as a bare string -- `"qubit": "Ba+"` -- with no mass anywhere, so a
    default here would be a number nobody chose silently deciding the answer.
    """
    if not (mass_kg > 0.0):
        raise ValueError(f"mass_kg must be positive, got {mass_kg!r}")
    if not (omega_rf_rad_s > 0.0):
        raise ValueError(f"omega_rf_rad_s must be positive, got {omega_rf_rad_s!r}")
    ex, ey, ez = rf_field(rects, x, y, z, voltage_v=voltage_v)
    e2 = ex * ex + ey * ey + ez * ez
    return charge_c * charge_c * e2 / (4.0 * mass_kg * omega_rf_rad_s * omega_rf_rad_s)


# ------------------------------------------------------------------------- the RF null

@dataclass(frozen=True)
class NullResult:
    """Where the RF null is, and the evidence that it is one."""

    found: bool
    z: float | None
    #: |E| at the located height divided by |E| at half that height
    residual: float
    field_v_per_m: tuple[float, float, float]
    reference_v_per_m: float
    iterations: int
    reason: str
    #: the transverse position the null was found at; `rf_null` reports the `y` it was
    #: given, `transverse_null` reports the one it solved for
    y: float | None = None

    def as_dict(self) -> dict:
        return {"found": self.found, "z": self.z, "y": self.y,
                "residual": self.residual,
                "field_v_per_m": list(self.field_v_per_m),
                "reference_v_per_m": self.reference_v_per_m,
                "iterations": self.iterations, "reason": self.reason}


def rf_null(rects: Sequence[Rect], x: float, bracket: tuple[float, float], *,
            y: float = 0.0, residual_max: float = 1e-6,
            rel_tol: float = 1e-13, max_iter: int = 200) -> NullResult:
    """Locate the RF null above `(x, y)` by bisection on `E_z`, and certify it.

    The bracket is required, not searched for.  A trap has more than one stationary
    surface and an unbracketed root finder walking off a rail returns a number that looks
    like an ion height and is not; making the caller state where it believes the null is
    turns that into a refusal.  An even number of nulls inside the bracket shows no sign
    change and is likewise refused rather than split.

    **The certificate.**  Bisection on `E_z` finds a zero of `E_z`, which off the symmetry
    axis is not a zero of `E`.  So the result is checked: `|E|` at the located height,
    divided by `|E|` at half that height, must be below `residual_max`.  The reference is
    taken *from the same geometry* rather than from an absolute threshold in V/m, so it
    scales with the drive and with the trap.  A local minimum of `|E_z|` that is not a
    null fails this and returns `found=False` -- never a nearby stationary point dressed
    as an ion position.
    """
    lo, hi = bracket
    if not (0.0 < lo < hi):
        raise ValueError(f"bracket must satisfy 0 < lo < hi, got {bracket!r}")
    if not (residual_max > 0.0):
        raise ValueError(f"residual_max must be positive, got {residual_max!r}")

    def ez_at(z: float) -> float:
        return rf_field(rects, x, y, z)[2]

    f_lo, f_hi = ez_at(lo), ez_at(hi)
    iterations = 0
    if f_lo == 0.0:
        z_star = lo
    elif f_hi == 0.0:
        z_star = hi
    elif (f_lo > 0.0) == (f_hi > 0.0):
        return NullResult(
            False, None, float("nan"), (float("nan"),) * 3, float("nan"), 0,
            f"E_z does not change sign across the bracket "
            f"({lo!r}, {hi!r}): E_z(lo)={f_lo!r}, E_z(hi)={f_hi!r}. Either there is no "
            f"null in it, or there is an even number of them.")
    else:
        while iterations < max_iter:
            mid = 0.5 * (lo + hi)
            if hi - lo <= rel_tol * mid:
                break
            f_mid = ez_at(mid)
            iterations += 1
            if f_mid == 0.0:
                lo = hi = mid
                break
            if (f_mid > 0.0) == (f_lo > 0.0):
                lo, f_lo = mid, f_mid
            else:
                hi, f_hi = mid, f_mid
        z_star = 0.5 * (lo + hi)

    field = rf_field(rects, x, y, z_star)
    mag = math.sqrt(sum(c * c for c in field))
    ref_field = rf_field(rects, x, y, 0.5 * z_star)
    reference = math.sqrt(sum(c * c for c in ref_field))
    if reference == 0.0:
        return NullResult(
            False, z_star, float("nan"), field, reference, iterations,
            "|E| at half the located height is zero, so the residual cannot be "
            "normalised and the point cannot be certified as a null.", y)
    residual = mag / reference
    if residual > residual_max:
        return NullResult(
            False, z_star, residual, field, reference, iterations,
            f"E_z vanishes at z={z_star!r} but |E| does not: residual {residual:.3e} "
            f"exceeds {residual_max:.3e}. This is a stationary point of E_z, not an RF "
            f"null; the transverse field does not vanish there.", y)
    return NullResult(True, z_star, residual, field, reference, iterations, "ok", y)


def transverse_null(rects: Sequence[Rect], along: str, at: float,
                    guess: tuple[float, float], *, residual_max: float = 1e-6,
                    max_iter: int = 60, tol: float = 1e-13) -> NullResult:
    """Solve `E_y = E_z = 0` in the transverse plane at fixed `x`, and certify it.

    `rf_null` bisects in `z` alone, which is right only where symmetry already puts the
    null on the given `y` -- an isolated rail, or the axis of a symmetric junction.  It is
    wrong wherever other metal breaks that symmetry, and in this project that is the normal
    case rather than the exception: in `ring144_24v` the opposite rail and the 24 dock
    spurs push the null about 5 um **off** the trap axis and 13% higher than the linear
    section, and a search constrained to `y = 0` reports neither.

    Damped Newton on the two-component residual, with the Jacobian by central differences
    on the *analytic* field.  Newton rather than a nested bisection because the nested form
    costs thousands of field sums per point and this one costs a few dozen -- and Newton is
    safe here precisely because the answer is certified afterwards rather than trusted:
    a step that lands somewhere that is not a null refuses.

    **The certificate is transverse, and only transverse.**  `rf_null` normalises the full
    `|E|`, which is right for it: on a symmetry axis all three components vanish together.
    Here only two are being solved, and `E_x` -- the field along the rail -- is left over
    by construction.  It is not an error: a real trap has axial structure and the DC
    electrodes are what cancel it, which this package does not model at all.  So the
    residual is `sqrt(E_y^2 + E_z^2)` against the same at half the height, and `E_x` is
    reported in `field_v_per_m` for the caller to look at rather than folded into a
    verdict.  Normalising the full `|E|` here rejected every device but the isolated chain.
    """
    if along not in ("x", "y"):
        raise ValueError(f"a segment runs along 'x' or 'y', not {along!r}")
    #: the transverse in-plane axis: across a rail that runs along x, and vice versa
    free = 1 if along == "x" else 0
    y, z = guess
    _check_z(z)
    if not (residual_max > 0.0):
        raise ValueError(f"residual_max must be positive, got {residual_max!r}")

    def _point(pt: float, pz: float) -> tuple[float, float, float]:
        return (at, pt, pz) if along == "x" else (pt, at, pz)

    def residual_at(pt: float, pz: float) -> tuple[float, float]:
        e = rf_field(rects, *_point(pt, pz))
        return e[free], e[2]

    iterations = 0
    for iterations in range(1, max_iter + 1):
        ey, ez = residual_at(y, z)
        h = max(abs(z) * 1e-6, 1e-12)
        ey_y, ez_y = residual_at(y + h, z)
        ey_yb, ez_yb = residual_at(y - h, z)
        ey_z, ez_z = residual_at(y, z + h)
        ey_zb, ez_zb = residual_at(y, z - h)
        j11 = (ey_y - ey_yb) / (2 * h)
        j12 = (ey_z - ey_zb) / (2 * h)
        j21 = (ez_y - ez_yb) / (2 * h)
        j22 = (ez_z - ez_zb) / (2 * h)
        det = j11 * j22 - j12 * j21
        if det == 0.0:
            return NullResult(
                False, z, float("nan"), (float("nan"),) * 3, float("nan"), iterations,
                f"the transverse Jacobian is singular at y={y!r}, z={z!r}; there is no "
                f"isolated null here to converge to.", y)
        dy = -(j22 * ey - j12 * ez) / det
        dz = -(-j21 * ey + j11 * ez) / det
        # damp so a wild first step cannot walk through the electrode plane
        limit = 0.5 * abs(z)
        scale = min(1.0, limit / max(abs(dy), abs(dz))) if max(abs(dy), abs(dz)) else 1.0
        y, z = y + scale * dy, z + scale * dz
        if not (z > 0.0):
            return NullResult(
                False, None, float("nan"), (float("nan"),) * 3, float("nan"), iterations,
                "the search left the half-space above the electrodes; there is no null "
                "above this point.", y)
        if abs(scale * dy) <= tol * abs(z) and abs(scale * dz) <= tol * abs(z):
            break

    field = rf_field(rects, *_point(y, z))
    mag = math.hypot(field[free], field[2])
    below = rf_field(rects, *_point(y, 0.5 * z))
    ref = math.hypot(below[free], below[2])
    if ref == 0.0:
        return NullResult(False, z, float("nan"), field, ref, iterations,
                          "the transverse |E| at half the located height is zero, so the "
                          "residual cannot be normalised.", y)
    residual = mag / ref
    if residual > residual_max:
        return NullResult(
            False, z, residual, field, ref, iterations,
            f"Newton settled at y={y!r}, z={z!r} but the transverse field does not vanish "
            f"there: residual {residual:.3e} exceeds {residual_max:.3e}. Not an RF null.",
            y)
    return NullResult(True, z, residual, field, ref, iterations, "ok", y)


# --------------------------------------------------------------------------- curvature

@dataclass(frozen=True)
class HessianResult:
    """The pseudopotential Hessian, and how far the extrapolation still moved."""

    #: 3x3 symmetric, in J/m^2, row-major (x, y, z)
    matrix: tuple[tuple[float, float, float], ...]
    step_m: float
    #: largest |R - D(h)| / |R| over the entries; how much Richardson bought
    richardson_delta: float

    def as_dict(self) -> dict:
        return {"matrix": [list(r) for r in self.matrix], "step_m": self.step_m,
                "richardson_delta": self.richardson_delta}


def _second_derivatives(f: Callable[[float, float, float], float],
                        x: float, y: float, z: float, h: float
                        ) -> tuple[tuple[float, float, float], ...]:
    """Central-difference Hessian of `f` at one step size."""
    p = (x, y, z)

    def at(shift: Sequence[float]) -> float:
        return f(p[0] + shift[0], p[1] + shift[1], p[2] + shift[2])

    centre = at((0.0, 0.0, 0.0))
    m = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        plus = [0.0] * 3
        plus[i] = h
        minus = [0.0] * 3
        minus[i] = -h
        m[i][i] = (at(plus) - 2.0 * centre + at(minus)) / (h * h)
    for i in range(3):
        for j in range(i + 1, 3):
            acc = 0.0
            for si in (1.0, -1.0):
                for sj in (1.0, -1.0):
                    shift = [0.0] * 3
                    shift[i] = si * h
                    shift[j] = sj * h
                    acc += si * sj * at(shift)
            m[i][j] = m[j][i] = acc / (4.0 * h * h)
    return tuple(tuple(row) for row in m)


def pseudopotential_hessian(rects: Sequence[Rect], x: float, y: float, z: float, *,
                            voltage_v: float, omega_rf_rad_s: float, mass_kg: float,
                            charge_c: float = ELEMENTARY_CHARGE_C,
                            step_m: float | None = None) -> HessianResult:
    """Curvature of the pseudopotential, by Richardson-extrapolated central differences.

    The *field* is analytic; only the second derivative of `|E|^2` is numerical, and it is
    taken at `h` and `h/2` and combined as `(4 D(h/2) - D(h)) / 3`, which cancels the
    leading `O(h^2)` term and leaves `O(h^4)`.  The difference between the extrapolated
    and the coarse estimate is returned, so a caller can see how much that was worth
    instead of trusting the step size.

    `step_m` defaults to a hundredth of the height, which is well inside the region where
    the pseudopotential is quadratic and well outside the region where subtracting nearly
    equal `|E|^2` loses the answer to rounding.
    """
    h = z / 100.0 if step_m is None else step_m
    if not (h > 0.0):
        raise ValueError(f"step_m must be positive, got {step_m!r}")

    def psi(px: float, py: float, pz: float) -> float:
        return pseudopotential(rects, px, py, pz, voltage_v=voltage_v,
                               omega_rf_rad_s=omega_rf_rad_s, mass_kg=mass_kg,
                               charge_c=charge_c)

    coarse = _second_derivatives(psi, x, y, z, h)
    fine = _second_derivatives(psi, x, y, z, 0.5 * h)
    out = tuple(tuple((4.0 * fine[i][j] - coarse[i][j]) / 3.0 for j in range(3))
                for i in range(3))
    scale = max(abs(out[i][j]) for i in range(3) for j in range(3)) or 1.0
    delta = max(abs(out[i][j] - coarse[i][j]) for i in range(3) for j in range(3)) / scale
    return HessianResult(out, h, delta)


def _jacobi_eigenvalues(m: Sequence[Sequence[float]], *, sweeps: int = 100,
                        tol: float = 1e-30) -> tuple[float, float, float]:
    """Eigenvalues of a real symmetric 3x3, by cyclic Jacobi rotation, ascending.

    Jacobi rather than the closed-form trigonometric root: the closed form is ill
    conditioned when two eigenvalues are close, which for a radially symmetric trap is the
    normal case, not the corner case.
    """
    a = [[float(m[i][j]) for j in range(3)] for i in range(3)]
    for _ in range(sweeps):
        off = sum(a[i][j] * a[i][j] for i in range(3) for j in range(3) if i != j)
        diag = sum(a[i][i] * a[i][i] for i in range(3))
        if off <= tol * (diag or 1.0):
            break
        for p in range(2):
            for q in range(p + 1, 3):
                apq = a[p][q]
                if apq == 0.0:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * apq)
                t = (math.copysign(1.0, theta)
                     / (abs(theta) + math.sqrt(theta * theta + 1.0)))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                rot = [[1.0 if r == k else 0.0 for k in range(3)] for r in range(3)]
                rot[p][p] = c
                rot[q][q] = c
                rot[p][q] = s
                rot[q][p] = -s
                a = [[math.fsum(rot[k][r] * math.fsum(a[k][l] * rot[l][col]
                                                      for l in range(3))
                                for k in range(3))
                      for col in range(3)] for r in range(3)]
    return tuple(sorted(a[i][i] for i in range(3)))  # type: ignore[return-value]


def secular_frequencies(hessian: Sequence[Sequence[float]], mass_kg: float
                        ) -> tuple[float, float, float]:
    """Secular angular frequencies from a pseudopotential Hessian, ascending, rad/s.

    `omega_i = sqrt(lambda_i / m)` for the eigenvalues of the Hessian.  A negative
    eigenvalue is a direction in which the pseudopotential falls away -- the ion is not
    confined along it -- and returns NaN rather than a frequency, because there is no real
    frequency there and reporting zero would read as "no confinement needed".
    """
    if not (mass_kg > 0.0):
        raise ValueError(f"mass_kg must be positive, got {mass_kg!r}")
    out = []
    for lam in _jacobi_eigenvalues(hessian):
        out.append(math.sqrt(lam / mass_kg) if lam > 0.0 else float("nan"))
    return tuple(out)  # type: ignore[return-value]


# ------------------------------------------- the two-dimensional limit, in closed form

def strip_potential(w_g: float, w_rf: float, x: float, z: float) -> float:
    """Potential of two infinite RF strips, per volt.  Independent oracle, not the kernel.

    Rails occupy `w_g/2 <= |x| <= w_g/2 + w_rf`, infinite in y, grounded elsewhere:

        Phi = 1/pi sum_rails [ atan((x2 - x)/z) - atan((x1 - x)/z) ]

    This is a *different model* from `rect_potential` -- two dimensions, infinite
    electrodes -- not a second implementation of the same one.  The rectangle sum
    converges to it as the rails lengthen, and that convergence is the kernel's test.
    """
    _check_z(z)
    a, b = w_g / 2.0, w_g / 2.0 + w_rf
    total = 0.0
    for x1, x2 in ((a, b), (-b, -a)):
        total += math.atan2(x2 - x, z) - math.atan2(x1 - x, z)
    return total / math.pi


def strip_field(w_g: float, w_rf: float, x: float, z: float) -> tuple[float, float]:
    """`(E_x, E_z)` of two infinite RF strips, per volt.  `E_y` is identically zero."""
    _check_z(z)
    a, b = w_g / 2.0, w_g / 2.0 + w_rf
    ex = ez = 0.0
    for x1, x2 in ((a, b), (-b, -a)):
        big_1, big_2 = x1 - x, x2 - x
        d1, d2 = big_1 * big_1 + z * z, big_2 * big_2 + z * z
        ex += (z / d2 - z / d1) / math.pi
        ez += (big_2 / d2 - big_1 / d1) / math.pi
    return (ex, ez)


def strip_null_height(w_g: float, w_rf: float) -> float:
    """Exact RF null height of the two-strip trap: `h = 1/2 sqrt(w_g (w_g + 2 w_rf))`.

    On `x = 0` the two rails contribute equally and `E_z` vanishes where
    `a/(a^2+z^2) = b/(b^2+z^2)` with `a = w_g/2`, `b = a + w_rf`, i.e. `z^2 = a b`.  The
    same relation inverted is the sizing rule 2201.12579 uses for its linear sections:
    `w_g = 0.83 h`, `w_RF = (4h^2 - w_g^2)/(2 w_g)`, tabulated as 41.5 um and 99.5 um for
    `h = 50` um (`ms.tex:283-288`, `tab:junction_linear`).
    """
    return 0.5 * math.sqrt(w_g * (w_g + 2.0 * w_rf))


def strip_field_gradient(w_g: float, w_rf: float) -> float:
    """`|dE_z/dz|` at the two-strip null, per volt, in V/m^2.

        |E'| = 4 (b - a) / ( pi (a + b)^2 sqrt(a b) )

    The trap's whole strength in one number: the secular frequency of an ion of charge `q`
    and mass `m` under drive `V` at `Omega` is `q V |E'| / (sqrt(2) m Omega)`.
    """
    a, b = w_g / 2.0, w_g / 2.0 + w_rf
    return 4.0 * (b - a) / (math.pi * (a + b) ** 2 * math.sqrt(a * b))
