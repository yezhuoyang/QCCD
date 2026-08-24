"""The physical-electrode layer: metal, in nanometres, and the field it makes.

Everything here is derived.  Nothing in this package is authored in an architecture file,
and no field is added to `Node` or `Segment` for it -- the metal is a pure function of
`(Device, Technology)`, so there is nothing for the browser to edit or the serialization
to lose.

    field    the gapless-plane RF pseudopotential, in closed form.  Metres and volts.
    tech     the technology sidecar: every dimension in integer nanometres, with a source.
    shapes   the metal: integer-nanometre polygons, cells, placements and design rules.
    build    the derivation, `(Device, Technology) -> Layout`.
    drc      design rules over the drawn metal, plus disclosures that are not verdicts.
    gds      a hand-written GDSII stream writer, and an independent reader.
    svg      the same shape table, rendered for a person.

`field` is the only module that sees a float, and `shapes` is the only one that sees the
lattice.  They meet in exactly one function -- `build.rects_for_field` -- so the exact half
stays exact.
"""

from __future__ import annotations

from .field import (
    ATOMIC_MASS_KG,
    ELEMENTARY_CHARGE_C,
    HessianResult,
    NullResult,
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
from .shapes import (
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
from .build import (
    NAIVE_CROSSING_SOURCE,
    build_layout,
    rects_for_field,
    unconnected_crossings,
)
from .drc import DRCReport, Disclosure, check, checked
from .gds import GdsBoundary, GdsLibrary, read_gds, write_gds
from .svg import svg_text, write_svg
from .tech import PURPOSES, Dim, Layer, Technology, load_technology, preset_names

__all__ = [
    # field
    "ATOMIC_MASS_KG", "ELEMENTARY_CHARGE_C", "HessianResult", "NullResult", "Rect",
    "pseudopotential", "pseudopotential_hessian", "rect_field", "rect_potential",
    "rf_field", "rf_null", "rf_potential", "secular_frequencies", "strip_field",
    "strip_field_gradient", "strip_null_height", "strip_potential",
    # shapes
    "Cell", "Inst", "Layout", "Poly", "Refusal", "Violation", "min_gap_violations",
    "min_width_violations", "union_rects",
    # tech
    "PURPOSES", "Dim", "Layer", "Technology", "load_technology", "preset_names",
    # build
    "NAIVE_CROSSING_SOURCE", "build_layout", "rects_for_field", "unconnected_crossings",
    # drc
    "DRCReport", "Disclosure", "check", "checked",
    # renderers
    "GdsBoundary", "GdsLibrary", "read_gds", "write_gds", "svg_text", "write_svg",
]
