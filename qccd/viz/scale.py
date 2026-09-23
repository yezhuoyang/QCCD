"""The technology a page measures in -- and nothing else.

A node position is a LATTICE UNIT.  It is not a length, and for as long as the only thing
a page carried was lattice units there was no distance on the picture anybody could put a
number to, and no angle either: the fit is free to stretch one axis by up to `K_ANISO`, so
a right angle in the device is not a right angle on the screen.  A reviewer of the
hardware picture asked for both -- measure any two points in micrometres, measure the
angle between any two rails -- and both need one thing the renderer did not have: the
nanometres per lattice unit, which live in the technology sidecar (`qccd/phys/tech.py`).

**WHY THIS IS ITS OWN MODULE.**  `tests/test_phys_view.py::
test_viz_still_does_not_import_the_physics_package` asserts that `render.py`, `layout.py`
and `theme.py` do not import `qccd.phys`, and it is right to: the METAL is a property of
`(device, technology)`, a page is a property of a run, and wiring one to the other inside
the renderer would make every emitted page pay for a polygon build and a field solve it
did not ask for.  Reading nine integers out of a JSON sidecar is not that build.  So the
dependency is real, narrow and worth stating out loud rather than buried in a 4,000-line
renderer -- this module is the whole of it, it is the only part of `qccd.viz` that knows
`qccd.phys` exists, and it imports `load_technology` LAZILY so that importing the renderer
still costs nothing.

Nothing here computes a polygon, a field, or a design rule.  If a future change needs one
of those in a page, it does not belong here either: `qccd.phys.svg.metal_view_model`
already ships the metal as a dict, and `render.py` takes it as a parameter for exactly the
reason above.
"""

from __future__ import annotations

from functools import lru_cache

__all__ = ["DEFAULT_TECH", "tech_view_model", "load_preset"]

#: THE TECHNOLOGY EVERY PAGE FALLS BACK TO.  A page with no technology named used to be a
#: page on which no distance could be measured at all, so the fallback is a real preset
#: rather than `None`.  `surface_default` is the reference process built from the
#: collaborator's own technology-rule defaults, and it is ISOTROPIC -- one lattice unit is
#: the same length in both directions -- so a page that says nothing about its process
#: still says something true about its proportions.
DEFAULT_TECH = "surface_default"


@lru_cache(maxsize=8)
def load_preset(name: str):
    """`qccd.phys.tech.load_technology`, memoised.

    `qccd demo` writes twenty pages from one sidecar and `qccd site` writes more; parsing
    and rule-checking the same file once per page is pure waste.  Memoised on the NAME,
    so a caller that hands over a `Technology` object never reaches this at all.
    """
    from ..phys.tech import load_technology
    return load_technology(name)


def tech_view_model(tech=None) -> dict:
    """The scale block a page carries: nanometres per lattice unit, and the electrodes.

    Everything is in INTEGER NANOMETRES, exactly as the technology stores it, so the page
    divides once and no unit conversion happens twice.  `tech` is a
    `qccd.phys.tech.Technology`, a preset name, or `None` for `DEFAULT_TECH`.

    The six named fields are the collaborator's technology rules under the names they
    asked for; `qccd/phys/tech.py::TECH_RULES` is where each one's minimum, default and
    source live, and `Technology` reads three of them off dimensions it already had rather
    than storing second copies that could drift.
    """
    from ..phys.tech import Technology
    t = tech if isinstance(tech, Technology) else load_preset(tech or DEFAULT_TECH)
    return {
        "preset": t.name,
        # The two scales are separate numbers with separate sources.  A single global
        # scale would quietly assert that a device whose y-extent is 1.0 is one axial trap
        # pitch tall, and that is a drawing convention, not a physical claim.
        "nm_per_unit_x": t.nm_per_unit_x.nm,
        "nm_per_unit_y": t.nm_per_unit_y.nm,
        "w_rf": t.w_rf,
        "w_dc": t.w_dc,
        "l_dc": t.l_dc,
        "g_dc": t.g_dc,
        "g_rf": t.g_rf,
        "n_dc_pairs": t.n_dc_pairs,
        # `l_dc + g_dc` by construction -- shipped rather than re-derived in the browser
        # because it is what the control tiling is spaced at, and a tiling pitch that the
        # page recomputes is a tiling pitch that can disagree with the one the DRC checks.
        "dc_pitch": t.l_dc + t.g_dc,
    }
