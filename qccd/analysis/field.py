"""Where the ion sits, how hard it is held, and what the junction costs -- from geometry.

Every other analysis here answers from the graph.  This one answers from the *metal*:
`qccd/phys/build.py` derives electrode polygons from `(Device, Technology)`, and the
gapless-plane solver reads them.  So an architect dragging a site changes a number that a
measurement could contradict, which is the whole point of the physical layer.

**Three things it reports that nothing else in this project could.**

*The ion is not at its design height.*  The technology sizes its rails for 49.95 um, and
that is what an isolated rail gives -- `chain72` returns 49.948.  But no shipped device is
an isolated rail: the opposite row, the dock spurs and the junction metal all pull on the
null, and the answer ranges from +1.2% (`cyclone_base`) to +15.2% (`ladder_2x72`), with
`ring144_24v` at +12.7%.  Four of the nine are outside the 5% band 2201.12579 publishes
for this model against FEM, so the deviation is physics rather than numerics.  The null is
also pushed up to 5 um **off** the trap axis, which a search constrained to the axis
reports as no null at all.

*The junction is the paper's counterexample, and it is as bad as the paper says.*  A
degree>=3 node is drawn as the naive crossing -- two linear sections crossed, gaps kept
clear, which is 2201.12579 fig. 7(a).  Solving it reproduces their 84 um path height and
0.07 meV/um^2 confinement.  That turns PLAN section 0.5's *prose* about RF barriers into
two checked numbers.

*The Mathieu q says when to stop believing the rest.*  The pseudopotential is an
approximation valid for small `q`, and at a plausible drive this geometry runs at
`q ~ 0.5`.  Above `q_max` every derived frequency is NaN and the `q` itself is reported,
because a secular frequency quoted outside the approximation's range is worse than no
number: it looks like an answer.

**`mass_u` has no default.**  Every architecture this project ships declares its qubit as
a bare string -- `"qubit": "Ba+"` -- with no mass anywhere.  `_check_keys` exists so that a
knob cannot silently keep a default nobody chose, and an ion mass is exactly such a knob:
the secular frequency is inversely proportional to it.  The key is declared so it is not
"unknown", and it is `None`, and running without it refuses by name.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from ..arch import load
from ..phys.build import build_layout, rects_for_field
from ..phys.drc import check as drc_check
from ..phys.field import (
    ATOMIC_MASS_KG,
    ELEMENTARY_CHARGE_C,
    Rect,
    pseudopotential,
    pseudopotential_hessian,
    secular_frequencies,
    strip_null_height,
    transverse_null,
)
from ..phys.shapes import union_rects
from ..phys.tech import Technology, load_technology
from .base import QCCDAnalysis

__all__ = ["PhysicalAnalysis", "naive_crossing_rects", "confinement_meV_per_um2"]

ARCH_DIR = Path(__file__).resolve().parents[2] / "arch"


def confinement_meV_per_um2(hessian) -> float:
    """`grad^2 phi_PP` in the units 2201.12579 quotes it in.

    The trace of the pseudopotential Hessian, with the pseudopotential in eV.  The paper
    calls it the "total pseudo-potential confinement" and it is the one quantity a static
    potential cannot redistribute away -- `grad^2 phi_total = grad^2 phi_PP`, so the three
    secular frequencies are jointly bounded by it however the DC electrodes are set
    (`ms.tex:185-195`).

    The reading is checked against the paper's own arithmetic: at 0.07 meV/um^2 for
    40-Ca+, pinning one mode at 1.5 MHz leaves 1.008 MHz for the other two against the
    printed 1.06 -- agreement to the rounding of the quoted figure.
    """
    trace = sum(hessian[i][i] for i in range(3))
    return trace / ELEMENTARY_CHARGE_C * 1e-12 * 1e3


def naive_crossing_rects(w_g_nm: int, w_rf_nm: int, arm_nm: int) -> tuple[Rect, ...]:
    """2201.12579 fig. 7(a): two linear sections crossed, each keeping the other's gap.

    Four L-shaped quadrant electrodes.  The obvious alternative -- running both rail pairs
    straight through each other -- puts RF metal across both trap axes, and solving *that*
    finds no confined ion position anywhere near the junction, because the ion would be
    directly over driven metal.  The paper plots a path with a finite height, so the paper
    cannot mean that; and this geometry reproduces its numbers to 2%.
    """
    a, b = w_g_nm // 2, w_g_nm // 2 + w_rf_nm
    boxes = []
    for sx in (1, -1):
        for sy in (1, -1):
            x0, x1 = sorted((sx * a, sx * arm_nm))
            y0, y1 = sorted((sy * a, sy * b))
            boxes.append((x0, y0, x1, y1))
            x0, x1 = sorted((sx * a, sx * b))
            y0, y1 = sorted((sy * a, sy * arm_nm))
            boxes.append((x0, y0, x1, y1))
    return tuple(Rect(x0 * 1e-9, y0 * 1e-9, x1 * 1e-9, y1 * 1e-9)
                 for x0, y0, x1, y1 in union_rects(boxes))


def _is_axis_aligned(device, seg) -> bool:
    p, q = device.nodes[seg.ends[0]].pos, device.nodes[seg.ends[1]].pos
    return p != q and (p[0] == q[0] or p[1] == q[1])


def _metal_extent(layout, axis: int) -> float:
    """How far the drawn metal reaches along one axis, in metres.

    An UPPER bound on the rail length the ion actually sees -- the metal may be
    interrupted between here and the far end -- but the right order of magnitude, and the
    quantity Wesenberg's "the extent of the trap is much larger than d" is about.  The
    segment's own length is the wrong thing: rails run continuously through node
    boundaries, so a 225 um segment sits on a 16 mm rail.
    """
    box = layout.bbox()
    return 0.0 if box is None else (box[axis + 2] - box[axis]) * 1e-9


def _local_min_z(psi, x: float, y: float, lo: float, hi: float, samples: int):
    """The pseudopotential minimum *nearest the surface*, or None if there is not one.

    `Psi -> 0` at infinity, so a global minimisation walks off to the sky and reports the
    top of whatever bracket it was given.  A trapping position is a **local** minimum or it
    is nothing, and saying "nothing" is the informative answer where the RF barrier is.
    """
    step = (hi - lo) / samples
    zs = [lo + step * i for i in range(samples + 1)]
    vals = [psi(x, y, z) for z in zs]
    for i in range(1, samples):
        if vals[i] <= vals[i - 1] and vals[i] <= vals[i + 1]:
            left, right = zs[i - 1], zs[i + 1]
            for _ in range(120):
                m1, m2 = left + (right - left) / 3, right - (right - left) / 3
                if psi(x, y, m1) < psi(x, y, m2):
                    right = m2
                else:
                    left = m1
            z = 0.5 * (left + right)
            return z, psi(x, y, z)
    return None, None


class PhysicalAnalysis(QCCDAnalysis):
    """Ion height, secular frequency and the naive-junction cost, from the drawn metal."""

    summary = ("where the ion sits and how hard it is held, solved from electrodes "
               "derived from the device and a technology")

    default_setup = {
        "device": "ring144_24v",
        "tech": "eth_junction_2201.12579",
        #: which segment to evaluate over; None takes the first axis-aligned one
        "segment": None,
        # 2201.12579's own drive conditions (ms.tex:371), so the shipped defaults are
        # the ones its published numbers were computed at
        "rf": {"voltage_v": 40.0, "frequency_mhz": 40.0,
               #: None means "take it from the technology"; a number overrides it, which
               #: is how an architect sweeps geometry without editing a preset
               "w_rf_nm": None, "w_g_nm": None},
        "dc": {"pitch_nm": None, "setback_nm": None},
        "nm_per_unit": None,
        #: NO DEFAULT.  Declared so it is not an unknown key, and refused when unset.
        "mass_u": None,
        "rail_length_over_h": 400.0,
        "null_residual_max": 1e-6,
        #: the Mathieu q past which the pseudopotential approximation is not quoted
        "q_max": 0.4,
    }

    data_labels = (
        "ion_height_um",
        "null_residual",
        "omega_radial_mhz",
        "mathieu_q",
        "naive_junction_path_height_um",
        "naive_junction_confinement_meV_per_um2",
        "electrode_bbox_mm2",
        "n_polys",
        "n_refused",
        "notes",
    )

    # ------------------------------------------------------------------ the inputs

    def _technology(self) -> Technology:
        s = self._setup
        tech = load_technology(s["tech"])
        doc = tech.to_json()
        for key, dim in (("w_rf_nm", "w_rf"), ("w_g_nm", "w_g")):
            if s["rf"][key] is not None:
                doc["dims"][dim] = {"nm": int(s["rf"][key]),
                                    "source": f"overridden by the field analysis: {key}"}
        for key, dim in (("pitch_nm", "dc_pitch"), ("setback_nm", "dc_setback")):
            if s["dc"][key] is not None:
                doc["dims"][dim] = {"nm": int(s["dc"][key]),
                                    "source": f"overridden by the field analysis: {key}"}
        if s["nm_per_unit"] is not None:
            for axis in ("nm_per_unit_x", "nm_per_unit_y"):
                doc[axis] = {"nm": int(s["nm_per_unit"]),
                             "source": "overridden by the field analysis: nm_per_unit"}
        if s["rf"]["w_rf_nm"] is not None or s["rf"]["w_g_nm"] is not None:
            # dc_setback is derived from the RF geometry, so an override of one without
            # the other would leave the builder drawing a trap that is not self-consistent
            w_g, w_rf = doc["dims"]["w_g"]["nm"], doc["dims"]["w_rf"]["nm"]
            gap = doc["dims"]["gap"]["nm"]
            if s["dc"]["setback_nm"] is None:
                doc["dims"]["dc_setback"] = {
                    "nm": w_g // 2 + w_rf + gap,
                    "source": "re-derived: w_g/2 + w_rf + gap, after an RF override"}
        return Technology.from_json(doc)

    def _mass_kg(self) -> float:
        mass_u = self._setup["mass_u"]
        if mass_u is None:
            raise ValueError(
                "mass_u has no default and none was given. Every architecture this "
                "project ships declares its qubit as a bare string -- \"qubit\": \"Ba+\" "
                "-- with no mass anywhere, so there is nothing to infer it from and a "
                "default here would be a number nobody chose deciding the answer: the "
                "secular frequency goes as 1/m. Pass e.g. mass_u=137.905247 for 138-Ba "
                "or 39.9626 for 40-Ca.")
        if not (float(mass_u) > 0.0):
            raise ValueError(f"mass_u must be positive, got {mass_u!r}")
        return float(mass_u) * ATOMIC_MASS_KG

    # -------------------------------------------------------------------- the run

    def _run(self) -> dict:
        s = self._setup
        tech = self._technology()
        mass = self._mass_kg()
        omega_rf = 2.0 * math.pi * float(s["rf"]["frequency_mhz"]) * 1e6
        volts = float(s["rf"]["voltage_v"])
        notes: list[str] = []

        path = s["device"]
        arch = load(path if str(path).endswith(".json")
                    else ARCH_DIR / f"{path}.arch.json")
        layout = build_layout(arch, tech)
        device = arch.device
        rects = rects_for_field(layout)

        # -- which segment, and where along it -------------------------------------
        seg_id = s["segment"]
        if seg_id is None:
            # the MIDDLE axis-aligned segment, not the first: the first is an end of the
            # device, where the rail stops and the ion height is a truncation artefact
            usable = [sid for sid, seg in device.segments.items()
                      if _is_axis_aligned(device, seg)]
            seg_id = usable[len(usable) // 2] if usable else None
        if seg_id is None or seg_id not in device.segments:
            known = ", ".join(list(device.segments)[:6])
            raise KeyError(
                f"segment {s['segment']!r} is not an axis-aligned segment of "
                f"{arch.name!r}; some of the ones that are: {known}")
        seg = device.segments[seg_id]
        p = device.nodes[seg.ends[0]].pos
        q = device.nodes[seg.ends[1]].pos
        sx, sy = tech.nm_per_unit_x.nm, tech.nm_per_unit_y.nm
        along = "x" if p[1] == q[1] else "y"
        if along == "x":
            at = (p[0] + q[0]) / 2 * sx * 1e-9
            across = p[1] * sy * 1e-9
            rail_len = _metal_extent(layout, 0)
        else:
            at = (p[1] + q[1]) / 2 * sy * 1e-9
            across = p[0] * sx * 1e-9
            rail_len = _metal_extent(layout, 1)

        # -- the ion --------------------------------------------------------------
        h_ideal = strip_null_height(tech.nm("w_g"), tech.nm("w_rf")) * 1e-9
        null = transverse_null(rects, along, at, (across, h_ideal),
                               residual_max=float(s["null_residual_max"]))
        notes.append(f"segment {seg_id} runs along {along}; the layout has "
                     f"{len(rects)} unioned RF rectangles")
        if not null.found:
            notes.append("NO CERTIFIED RF NULL: " + null.reason)
            height_um = float("nan")
            omega_mhz = mathieu_q = float("nan")
        else:
            height_um = null.z * 1e6
            notes.append(
                f"the null sits {(null.y - across) * 1e6:+.3f} um off the trap axis and "
                f"{(null.z / h_ideal - 1) * 100:+.1f}% from the {h_ideal * 1e6:.3f} um "
                f"an isolated rail of this geometry would give")
            if abs(null.z / h_ideal - 1) > 0.05:
                notes.append(
                    "that is outside the 5% band 2201.12579 publishes for this model "
                    "against FEM (ms.tex:200-208), so it is a property of the geometry "
                    "rather than of the model -- either neighbouring metal or, if the "
                    "rail-length note below fires, finite-length truncation")
            hess = pseudopotential_hessian(
                rects, *(( at, null.y, null.z) if along == "x"
                         else (null.y, at, null.z)),
                voltage_v=volts, omega_rf_rad_s=omega_rf, mass_kg=mass)
            freqs = secular_frequencies(hess.matrix, mass)
            radial = [f for f in freqs if math.isfinite(f)]
            omega = max(radial) if radial else float("nan")
            mathieu_q = 2.0 * math.sqrt(2.0) * omega / omega_rf
            omega_mhz = omega / (2.0 * math.pi) / 1e6
            if math.isfinite(mathieu_q) and mathieu_q > float(s["q_max"]):
                notes.append(
                    f"mathieu q = {mathieu_q:.3f} exceeds q_max = {s['q_max']}; the "
                    f"pseudopotential approximation is not quoted here, so every derived "
                    f"frequency is NaN. Lower the drive or raise the RF frequency.")
                omega_mhz = float("nan")

        # a rail shorter than the model's own validity condition is worth saying
        if rail_len and null.z and rail_len / null.z < float(s["rail_length_over_h"]):
            notes.append(
                f"this segment is {rail_len / null.z:.0f} ion heights long against a "
                f"rail_length_over_h of {s['rail_length_over_h']:.0f}; Wesenberg "
                f"(0808.1623) gives 'the extent of the trap is much larger than d' as a "
                f"validity condition of the gapless-plane model, and the finite-length "
                f"truncation falls only as 1/L^2")

        # -- the naive junction ----------------------------------------------------
        arm_nm = int(round(float(s["rail_length_over_h"]) * h_ideal * 1e9))
        crossing = naive_crossing_rects(tech.nm("w_g"), tech.nm("w_rf"), arm_nm)

        def psi(px, py, pz):
            return pseudopotential(crossing, px, py, pz, voltage_v=volts,
                                   omega_rf_rad_s=omega_rf, mass_kg=mass)

        best_h, worst_conf = 0.0, float("inf")
        n_blocked = 0
        for i in range(41):
            px = i * 0.1 * h_ideal
            z, _ = _local_min_z(psi, px, 0.0, 0.2 * h_ideal, 8.0 * h_ideal, 200)
            if z is None:
                n_blocked += 1
                continue
            best_h = max(best_h, z)
            hess = pseudopotential_hessian(crossing, px, 0.0, z, voltage_v=volts,
                                           omega_rf_rad_s=omega_rf, mass_kg=mass)
            worst_conf = min(worst_conf, confinement_meV_per_um2(hess.matrix))
        if n_blocked:
            notes.append(f"{n_blocked} of 41 points on the junction transport path have "
                         f"no confined ion position at all")
        notes.append(
            "the junction is drawn as 2201.12579's own counterexample (ms.tex:589, "
            "596-604): two linear sections simply crossed, which it measures at a 84 um "
            "path height and 0.07 meV/um^2 confinement, about 30% of its optimized "
            "geometry")

        box = layout.bbox()
        area_mm2 = (((box[2] - box[0]) * (box[3] - box[1])) * 1e-12
                    if box else 0.0)
        report = drc_check(layout, arch)
        if report.violations:
            notes.append(f"the drawn metal has {len(report.violations)} design-rule "
                         f"violations; run `qccd phys` for them")

        return {
            "ion_height_um": height_um,
            "null_residual": null.residual,
            "omega_radial_mhz": omega_mhz,
            "mathieu_q": mathieu_q,
            "naive_junction_path_height_um": best_h * 1e6,
            "naive_junction_confinement_meV_per_um2": (
                worst_conf if math.isfinite(worst_conf) else float("nan")),
            "electrode_bbox_mm2": area_mm2,
            "n_polys": layout.n_polys(),
            "n_refused": len(layout.refused),
            "notes": notes,
        }
