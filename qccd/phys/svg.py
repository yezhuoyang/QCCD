"""The same shape table, rendered for a person instead of for a fab tool.

qiskit-metal's actual thesis is that one design should feed many renderers.  This is that
claim reduced to something testable: `gds.py` and this file both consume
`Layout.flatten()` and nothing else, and `tests/test_gds.py` reads both back and asserts
they agree on polygon count, per-layer area and bounding box, exactly, over all nine
devices.  Two renderers, one table -- or the table was not the source of truth.

**Coordinates stay in integer nanometres.**  The scale lives in one `transform` on one
group, so a `<polygon points="...">` in the SVG carries the *same integers* the GDSII XY
record carries.  That is what makes the agreement checkable at tolerance zero rather than
to some number of decimal places -- and it also means the file can be reasoned about: a
coordinate in it is a nanometre.

**One isotropic transform.**  A single `scale(s, -s)`, the same `s` in both axes.  The
browser's `px()/py()` is anisotropic up to `K_ANISO = 12` and would shear a true-to-scale
rectangle into a lie; nothing here goes near it.  The `-s` flips y, because SVG's axis
points down and a trap's does not.

**Standalone.**  No script, no external stylesheet, no network.  It opens in a browser
from disk, and it is not the studio page -- that is a separate, un-taken decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .shapes import Layout
from .tech import Technology

__all__ = ["write_svg", "svg_text", "metal_view_model", "isotropic_fit",
           "PURPOSE_FILL", "PURPOSE_STROKE"]

#: Fill per layer *purpose*, not per layer name, so a technology that renames its metal
#: still renders.  Deliberately a local table: `qccd/viz/theme.py` belongs to the browser
#: page and this file must not depend on it.
PURPOSE_FILL: Mapping[str, str] = {
    # the driven electrodes, warm, because they are the dangerous ones
    "rf": "#c2410c",
    "dc": "#1d4ed8",       # segmented control
    "ground": "#334155",
    "shim": "#7c3aed",
    "outline": "none",
}
PURPOSE_STROKE: Mapping[str, str] = {
    "rf": "#7c2d12",
    "dc": "#1e3a8a",
    "ground": "#0f172a",
    "shim": "#4c1d95",
    "outline": "#94a3b8",
}


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def isotropic_fit(box: tuple[int, int, int, int], width: float, height: float,
                  pad: float) -> tuple[float, float, float]:
    """`(scale, tx, ty)` mapping an integer-nanometre bbox into a viewport, y flipped.

    One scale for both axes, chosen so the metal fits: a point `(x, y)` in nanometres
    lands at `(tx + s*x, ty - s*y)`.  Shared by the standalone SVG and by the browser
    page's metal underlay, so there is one fit and not two that can disagree.
    """
    x0, y0, x1, y1 = box
    span_x, span_y = max(x1 - x0, 1), max(y1 - y0, 1)
    scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
    return scale, pad - scale * x0, pad + scale * y1


def metal_view_model(layout: Layout, *, width: float, height: float,
                     pad: float = 8.0) -> dict | None:
    """The metal as plain JSON for the browser page, with the transform precomputed.

    **The scale is computed here, in Python, and the page only reads it.**  The page's own
    `px()/py()` is anisotropic -- up to `K_ANISO = 12` on a long thin device -- and
    pushing a true-to-scale rectangle through it would shear the electrodes into a shape
    no fab could make.  So the metal carries its own `transform` and never touches that
    mapping.

    A consequence worth stating rather than hiding: the underlay therefore does **not**
    register with the schematic above it.  Registering would require the page's `sx/sy` to
    equal the technology's `nm_per_unit_x / nm_per_unit_y`, and on `chain72` those are 1.0
    and 0.634.  One of the two views has to be a lie about proportion, and it is not going
    to be the one with nanometres in it -- so the payload carries a scale bar, and the page
    draws it.
    """
    polys = layout.flatten()
    box = layout.bbox()
    if not polys or box is None:
        return None
    tech: Technology = layout.tech
    scale, tx, ty = isotropic_fit(box, width, height, pad)

    layers = []
    for lay in tech.layers:
        group = [p for p in polys if p.layer == lay.name]
        if not group:
            continue
        layers.append({
            "name": lay.name,
            "purpose": lay.purpose,
            "fill": PURPOSE_FILL.get(lay.purpose, "#64748b"),
            "stroke": PURPOSE_STROKE.get(lay.purpose, "#334155"),
            "polys": [list(p.xy) for p in group],
        })

    # a round number of micrometres that is a sensible fraction of the die
    span_nm = max(box[2] - box[0], 1)
    bar_nm = 1000
    while bar_nm * 10 <= span_nm / 4:
        bar_nm *= 10
    x0, y0, x1, y1 = box
    return {
        "transform": f"translate({tx:.6f},{ty:.6f}) scale({scale:.9g},-{scale:.9g})",
        # the bar as a finished rectangle, in nanometres, so the page multiplies nothing
        "bar_rect_nm": {"x": x0, "y": y0 - bar_nm // 2,
                        "w": bar_nm, "h": max(bar_nm // 12, 1)},
        "scale_px_per_nm": scale,
        "nm_per_px": 1.0 / scale if scale else 0.0,
        "bbox_nm": list(box),
        "layers": layers,
        "n_polys": len(polys),
        "technology": tech.name,
        "bar_nm": bar_nm,
        "bar_label": (f"{bar_nm // 1000000} mm" if bar_nm >= 1000000
                      else f"{bar_nm // 1000} um"),
        "note": ("true to scale; the schematic above it is not, so the two do not "
                 "register"),
    }


def svg_text(layout: Layout, *, width_px: int = 1200, height_px: int = 700,
             pad_px: int = 24, background: str = "#f8fafc") -> str:
    """The layout as one standalone SVG document.

    Groups are emitted in the technology's layer order, so two runs of the same layout
    produce the same bytes.
    """
    tech: Technology = layout.tech
    polys = layout.flatten()
    box = layout.bbox()
    if box is None:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" '
                f'height="{height_px}" viewBox="0 0 {width_px} {height_px}">'
                f'<rect width="100%" height="100%" fill="{background}"/>'
                f'<text x="{pad_px}" y="{pad_px * 2}" font-family="monospace" '
                f'font-size="14">empty layout</text></svg>\n')

    x0, y0, x1, y1 = box
    span_x, span_y = max(x1 - x0, 1), max(y1 - y0, 1)
    scale, tx, ty = isotropic_fit(box, width_px, height_px, pad_px)

    by_layer: dict[str, list] = {}
    for p in polys:
        by_layer.setdefault(p.layer, []).append(p)

    nm_per_px = 1.0 / scale
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" '
        f'height="{height_px}" viewBox="0 0 {width_px} {height_px}">',
        f'<title>{_esc(tech.name)}</title>',
        f'<desc>{len(polys)} polygons, {span_x} x {span_y} nm; coordinates below are '
        f'integer nanometres and the scale is the single isotropic transform on '
        f'#metal ({nm_per_px:.1f} nm per pixel)</desc>',
        f'<rect width="100%" height="100%" fill="{background}"/>',
        f'<g id="metal" transform="translate({tx:.6f},{ty:.6f}) '
        f'scale({scale:.9g},-{scale:.9g})">',
    ]
    for lay in tech.layers:
        group = by_layer.get(lay.name)
        if not group:
            continue
        fill = PURPOSE_FILL.get(lay.purpose, "#64748b")
        stroke = PURPOSE_STROKE.get(lay.purpose, "#334155")
        out.append(f'<g data-layer="{_esc(lay.name)}" data-purpose="{lay.purpose}" '
                   f'data-gds="{lay.gds_layer}/{lay.gds_datatype}" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="{max(1.0 / scale, 1.0):.6g}" '
                   f'fill-opacity="0.85">')
        for p in group:
            pts = " ".join(f"{x},{y}" for x, y in p.points)
            out.append(f'<polygon points="{pts}"/>')
        out.append("</g>")
    out.append("</g>")

    legend_y = height_px - pad_px // 2
    parts = [f"{lay.name}:{len(by_layer.get(lay.name, ()))}" for lay in tech.layers
             if by_layer.get(lay.name)]
    out.append(f'<text x="{pad_px}" y="{legend_y}" font-family="monospace" '
               f'font-size="12" fill="#334155">{_esc(" ".join(parts))} | '
               f'{span_x / 1e6:.3f} x {span_y / 1e6:.3f} mm</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_svg(layout: Layout, path: str | Path, **kwargs) -> Path:
    p = Path(path)
    p.write_text(svg_text(layout, **kwargs), encoding="utf-8")
    return p
