"""The deck's visual language, in one place.

Two palettes are in play and the renderer adopts both rather than inventing a third:

* the **shipped visualizer**'s `:root` block, verbatim -- `--data`, `--x`, `--z`,
  `--anc`, `--active` and the neutral greys. Anyone who has looked at the 24-ancilla page
  recognises a teal dot as an X stabilizer and a red one as a Z. That page is screenshotted
  on deck p.17, so these values are load-bearing and are never re-derived.
* the **deck's own diagram system** (pp. 11-24). These values are *measured from the PDF*:
  the colours below were read out of the vector content streams of pp. 12, 19 and 22 and
  cross-checked against native-resolution pixels of the routing card on p.18. They are not
  the same as the repo's older `out/*.html`, which predate the deck and use a darker
  variant (`#ef9a95` / `#a3d9a5` / `#1a2b4a`); where the two disagree, the PDF wins,
  because matching the deck is the point.

Colour means *role*, not value, which is how the deck uses it: transport hardware is
coloured by what it is for -- data region, highway, computing region -- never by
temperature, occupancy or cost. Per-ion heating is a *toggle* rather than the default,
because a page that recolours the rails by temperature stops being the diagram the reader
already knows how to read.

The geometry block at the bottom is the other half of "same visual language": the deck's
shapes have measurable proportions (a site is a bar 8:1 along the trap axis, an ion is a
filled circle 1.3x the bar's thickness, a junction is a *square*, a DC electrode is a
stadium pill 2.63:1 flanking the axis) and encoding them here is what makes them
reproducible instead of re-guessed at each call site.
"""

from __future__ import annotations

__all__ = ["PALETTE", "SEGMENT_ROLE", "GEOMETRY", "css_vars"]

PALETTE = {
    # ---- shipped visualizer, verbatim (deck p.17) -----------------------
    "bg": "#f7f8fb",
    "panel": "#ffffff",
    "ink": "#182230",
    "muted": "#667085",
    "line": "#d0d5dd",
    "soft": "#eef2f6",
    "data": "#475467",     # a spectator data ion
    "x": "#0f766e",        # X stabilizer
    "z": "#b42318",        # Z stabilizer
    "anc": "#4338ca",      # ancilla
    "active": "#f6c34a",   # the site being contacted right now
    # ---- the deck's own diagram system, measured from pp. 12/18/19/22 ---
    "rail": "#f2b8b5",     # "data region"
    "highway": "#9fd3e6",  # "highway"
    "compute": "#9cce9c",  # "computing region"
    "accent": "#e4572e",   # eyebrow, cost counter
    "arrow": "#ed7d31",    # transport arrows only: lighter than accent
    "navy": "#1e2761",     # headings, spectator ion
    "junction": "#e8b84b",  # the deck draws a junction as a SQUARE, never a red dot
    "corner": "#f6c34a",   # a bend
    "gold": "#e8b84b",     # RF rail / trap axis
    "loop": "#2e8b57",     # ancilla loop outline
    "teal": "#0e7c7b",     # stabilizer group B
    "merge": "#ff0000",    # the dashed shared-well ellipse (deck p.14)
    "card": "#eef3fb",
    "card_line": "#cadcfc",
    "deck_ink": "#1c2333",
    "deck_muted": "#5b6472",
    "grid": "#8a94a6",       # structural lattice
    "grid_faint": "#d5dce8",  # background lattice
    "hot": "#b42318",
    "cold": "#0f766e",
    # ---- DC electrode vocabulary (deck p.19 legend, p.4 energization) ---
    "dc_h": "#9fd3e6",       # linear, horizontal
    "dc_v": "#9cce9c",       # linear, vertical
    "dc_junction": "#f2b8b5",
    "dc_comp": "#7c7a76",    # compensation
    "dc_idle": "#c3cbd8",
    "dc_hot": "#eb161a",     # energized  (deck p.4: the red triple)
    "dc_well": "#8d080b",    # the well floor of the energized triple
    # ---- zone types, so a t-factory is not a spectator trap -------------
    "zone_data": "#475467",
    "zone_ancilla": "#4338ca",
    "zone_trap": "#0f766e",
    "zone_tfactory": "#e4572e",
    "zone_load": "#f6c34a",
    "zone_register": "#7c5bfa",
    "zone_other": "#98a2b3",
    # ---- page chrome that used to be hard-coded in the template ---------
    "ion_stroke": "#ffffff",
    "neutral": "#98a2b3",
    "rotate_alt": "#7c5bfa",
    "ok_bg": "#e7f5ec",
    "bad_bg": "#fdeceb",
    "warn_bg": "#fdf3d9",
    "warn_ink": "#8a6d1f",
}

#: segment label -> the deck's role colour.  First match wins.
SEGMENT_ROLE = (
    ("highway", "highway"),
    ("onramp", "highway"),
    ("rung", "compute"),
    ("compute", "compute"),
    ("spur", "compute"),
    ("coupling", "compute"),
    ("rail", "rail"),
)

#: The deck's shape proportions, measured off the PDF's vector slides.  Ratios, so they
#: are scale-independent; `qccd.viz.layout` turns them into pixels.
GEOMETRY = {
    "SITE_ASPECT": 8.1,      # p.12 site bar: length : thickness
    "SITE_FRAC": 0.757,      # bar length / column pitch
    "ROW_PITCH_FRAC": 0.834,  # row pitch / column pitch
    "ION_D_FRAC": 0.13,      # plain ion diameter / pitch (0.175 when it carries a numeral)
    "ION_D_FRAC_ACTIVE": 0.175,  # ... an ion in flight or in the current gate
    "JUNCTION_SIDE": 0.50,   # square side / site length -- a SQUARE, not a circle
    "ZONE_RADIUS_FRAC": 0.19,  # rounded-square zone corner radius / side
    "DC_PILL_ASPECT": 2.63,  # fully-rounded stadium: radius = height/2
    "DC_PILL_GAP": 0.19,     # inter-pill gap / pill length
    "DC_AXIS_OFFSET": 0.75,  # pill edge to trap axis / pill height
    "RAIL_W_FRAC": 0.083,    # rail & highway thickness / pitch
    "RUNG_W_FRAC": 0.065,    # computing-region rung thickness / pitch
    "ARROW_W_FRAC": 0.033,   # transport-arrow stroke / pitch
    "ARROW_HEAD_LEN": 4.55,  # x stroke width
    "ARROW_HEAD_W": 3.30,    # x stroke width
    "ARROW_OFFSET": 0.102,   # arrow offset beside the axis / pitch
    "GHOST_DASH": (4, 3),    # destination-circle dash, pt at 960x540
}


def css_vars() -> str:
    return "\n".join(f"  --{k}:{v};" for k, v in PALETTE.items())
