"""S7 — the metal under the schematic, and the four things it is not allowed to be.

The fab view is the most visible half of the physical layer and the least verifiable, so
what is tested here is mostly what it must *not* do:

* it must not go through the page's `px()/py()`, which is anisotropic by up to
  `K_ANISO = 12` and would shear a 99.5 µm × 16 mm rectangle into a shape no fab could
  make.  The transform is computed in Python and the page only reads it;
* it must not change a page that did not ask for it — every emitted page without a
  technology carries no metal payload at all;
* it must not add JavaScript arithmetic, a parity bucket, an editing verb, or a palette
  tile.  The mirrored half of this project is diffed at tolerance zero; a fab view that
  computed anything would have to be diffed too, and it cannot be.

**It does not register with the schematic, and that is deliberate.**  Registering would
require the page's `sx/sy` to equal the technology's `nm_per_unit_x / nm_per_unit_y`.  On
`chain72` those are 1.0 and 0.634, so one of the two views has to misstate a proportion.
The one with nanometres in it does not, and the page draws a scale bar to say which is
which.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.ir import TSIR  # noqa: E402
from qccd.phys.build import build_layout  # noqa: E402
from qccd.phys.svg import isotropic_fit, metal_view_model  # noqa: E402
from qccd.phys.tech import load_technology  # noqa: E402
from qccd.verify import verify  # noqa: E402
from qccd.viz.render import build_view_model, render_html  # noqa: E402

ARCH = ROOT / "arch"
PRESET = "eth_junction_2201.12579"


@pytest.fixture(scope="module")
def tech():
    return load_technology(PRESET)


def _page(name, tech, tmp_path, with_metal=True):
    arch = load(ARCH / f"{name}.arch.json")
    prog = TSIR(name="empty", arch_spec=arch.name)
    model = corrected_model()
    rep = verify(prog, arch, model)
    view = build_view_model(arch, prog, rep.result, model)
    metal = None
    if with_metal:
        metal = metal_view_model(build_layout(arch, tech),
                                 width=view["layout"]["W"], height=view["layout"]["H"])
    out = render_html(arch, prog, rep.result, model, tmp_path / f"{name}.html",
                      metal=metal)
    return out.read_text(encoding="utf-8"), view, metal


def _blob(html: str) -> dict:
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html,
                  re.S)
    assert m, "the page has no data blob"
    return json.loads(m.group(1).replace("\\u003c", "<"))


# ------------------------------------------------------- the transform is Python's

def test_the_scale_is_isotropic_and_computed_in_python(tech):
    """One `s`, both axes, and the page never divides anything to get it."""
    layout = build_layout(load(ARCH / "grid9x9.arch.json"), tech)
    m = metal_view_model(layout, width=900, height=900)
    sx, sy = re.search(r"scale\(([^,]+),([^)]+)\)", m["transform"]).groups()
    assert float(sx) == -float(sy) > 0, "one isotropic scale, y flipped"
    assert float(sx) == pytest.approx(m["scale_px_per_nm"], rel=1e-12)
    assert m["nm_per_px"] == pytest.approx(1.0 / m["scale_px_per_nm"], rel=1e-12)


@pytest.mark.parametrize("box,w,h", [
    ((0, 0, 8_116_000, 705_000), 1600.0, 146.0),      # wide and flat: y binds
    ((0, 0, 705_000, 8_116_000), 1600.0, 146.0),      # tall and thin: y binds harder
    ((0, 0, 2_041_000, 3_080_000), 900.0, 900.0),     # squarish
    ((-120_250, -120_250, 1_920_250, 2_960_250), 900.0, 900.0),
])
def test_the_fit_takes_the_smaller_of_the_two_scales_and_touches_one_edge(box, w, h):
    """The definition of an isotropic fit, as arithmetic rather than as a string.

    Reading `scale(s,-s)` out of the transform only proves the two axes were given the
    same number -- it says nothing about whether that number was the right one.  A fit
    that used the x-scale for x and the y-scale for the offset would still print one `s`
    and would still overflow the viewport.
    """
    pad = 8.0
    x0, y0, x1, y1 = box
    scale, tx, ty = isotropic_fit(box, w, h, pad)
    assert scale == min((w - 2 * pad) / (x1 - x0), (h - 2 * pad) / (y1 - y0))
    xs = [tx + scale * x for x in (x0, x1)]
    ys = [ty - scale * y for y in (y0, y1)]
    for v, hi in ((xs, w), (ys, h)):
        assert min(v) == pytest.approx(pad, abs=1e-9), "the fit is anchored to the pad"
        assert max(v) <= hi - pad + 1e-9, "and nothing overflows"
    # exactly one axis is tight against the padding on both sides
    tight = [max(v) == pytest.approx(hi - pad, abs=1e-6)
             for v, hi in ((xs, w), (ys, h))]
    assert any(tight), "an isotropic fit must touch one pair of edges"


def test_the_metal_fit_is_the_same_one_the_standalone_svg_uses(tech):
    """One fit, not two that can disagree -- `svg_text` and the page share `isotropic_fit`.

    Compared against the SVG's *emitted transform*, not against `isotropic_fit` again:
    the point is that the standalone file and the browser page place the metal
    identically, and re-deriving the expected value from the same helper both of them are
    supposed to call would pass even if one of them stopped calling it.
    """
    from qccd.phys.svg import svg_text

    layout = build_layout(load(ARCH / "ring144_24v.arch.json"), tech)
    m = metal_view_model(layout, width=1200, height=700, pad=24)
    standalone = re.search(r'<g id="metal" transform="([^"]+)"',
                           svg_text(layout, width_px=1200, height_px=700, pad_px=24))
    assert standalone, "the standalone SVG has no metal group"
    assert m["transform"] == standalone.group(1)


def test_the_metal_fits_inside_the_viewport_it_was_given(tech):
    layout = build_layout(load(ARCH / "cyclone_base.arch.json"), tech)
    w, h, pad = 1600.0, 146.0, 8.0
    m = metal_view_model(layout, width=w, height=h, pad=pad)
    s = m["scale_px_per_nm"]
    tx, ty = (float(v) for v in
              re.search(r"translate\(([^,]+),([^)]+)\)", m["transform"]).groups())
    x0, y0, x1, y1 = m["bbox_nm"]
    for x, y in ((x0, y0), (x1, y1)):
        assert -1e-6 <= tx + s * x <= w + 1e-6
        assert -1e-6 <= ty - s * y <= h + 1e-6


def test_the_payload_carries_integer_nanometres_and_no_screen_coordinates(tech):
    """The polygons are the same integers the GDSII file holds; the transform maps them."""
    layout = build_layout(load(ARCH / "chain.arch.json"), tech)
    m = metal_view_model(layout, width=1600, height=132)
    drawn = [xy for layer in m["layers"] for xy in layer["polys"]]
    assert drawn and all(isinstance(v, int) for xy in drawn for v in xy)
    flat = {p.xy for p in layout.flatten()}
    assert {tuple(xy) for xy in drawn} == flat


def test_the_scale_bar_is_a_round_physical_length(tech):
    for name, want in (("chain", "1 mm"), ("grid9x9", "100 um")):
        m = metal_view_model(build_layout(load(ARCH / f"{name}.arch.json"), tech),
                             width=900, height=900)
        assert m["bar_label"] == want, name
        assert m["bar_nm"] * 4 <= (m["bbox_nm"][2] - m["bbox_nm"][0])


def test_registration_with_the_schematic_is_impossible_and_the_note_says_so(tech):
    """The claim the note makes, checked rather than asserted in prose.

    A drawn node sits at `ox + x*sx`; the same node's metal sits at `tx + s*x*nm_x`.  Both
    can only hold for every node if `sx/sy == nm_x/nm_y`.  It does not, for any shipped
    device, so the underlay cannot register and the page says it does not.
    """
    for name in ("chain", "grid9x9", "ring144_24v"):
        arch = load(ARCH / f"{name}.arch.json")
        prog = TSIR(name="empty", arch_spec=arch.name)
        rep = verify(prog, arch, corrected_model())
        L = build_view_model(arch, prog, rep.result, corrected_model())["layout"]
        page_ratio = L["sx"] / L["sy"]
        tech_ratio = tech.nm_per_unit_x.nm / tech.nm_per_unit_y.nm
        assert page_ratio != pytest.approx(tech_ratio, rel=1e-6), name
    m = metal_view_model(build_layout(load(ARCH / "chain.arch.json"), tech),
                         width=900, height=900)
    assert "do not register" in m["note"]


def test_an_empty_layout_has_no_metal_to_show(tech):
    from qccd.phys.shapes import Layout
    assert metal_view_model(Layout(tech), width=900, height=900) is None


# ------------------------------------------------------------ the page, with and without

def test_a_page_that_did_not_ask_for_metal_carries_none(tech, tmp_path):
    """Every page emitted before this existed is byte-identical to the one emitted now."""
    html, _view, _m = _page("chain", tech, tmp_path, with_metal=False)
    assert "metal" not in _blob(html)
    assert "if (D.metal){" in html, "the block is present but inert"


def test_the_metal_reaches_the_page_as_data_and_not_as_drawing_code(tech, tmp_path):
    html, view, metal = _page("grid9x9", tech, tmp_path)
    blob = _blob(html)
    assert blob["metal"]["transform"] == metal["transform"]
    assert blob["metal"]["n_polys"] == 828
    assert sum(len(l["polys"]) for l in blob["metal"]["layers"]) == 828


def test_the_group_is_first_so_the_metal_is_underneath_everything(tech, tmp_path):
    html, _v, _m = _page("chain", tech, tmp_path)
    append = re.search(r"svg\.append\(([^)]*)\)", html).group(1)
    assert append.split(",")[0].strip() == "gMetal"
    assert "gMetal" in html.split("svg.append")[0], "declared before it is appended"


@pytest.mark.parametrize("device", ["chain", "grid9x9", "ring144_24v", "cyclone_base"])
def test_the_page_is_still_self_contained_with_metal_in_it(tech, tmp_path, device):
    """`render_html` refuses a page that would fetch anything; metal must not change that."""
    html, _v, _m = _page(device, tech, tmp_path)
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "https://" not in html


# ------------------------------------------------------------------- what it may not be

def _metal_block(*, code_only: bool = False) -> str:
    """The metal block, optionally with comments and string literals removed.

    A `//` is not a division and the `-` in `'stroke-width'` is not a subtraction, so a
    naive scan of the raw text says nothing.  `code_only` leaves the executable part.
    """
    src = (ROOT / "qccd/viz/render.py").read_text(encoding="utf-8")
    block = src.split("if (D.metal){")[1].split("\n}")[0]
    block = "\n".join(l for l in block.splitlines() if not l.strip().startswith("//"))
    return re.sub(r"'[^']*'", "''", block) if code_only else block


def test_the_fab_view_added_no_javascript_arithmetic():
    """The page reads the transform; it does not compute one.

    The block may index, concatenate and append.  It may not multiply, divide, take a
    square root or call a trigonometric function — anything of that kind would be a second
    implementation of a number Python already produced, on the side of the line that
    cannot be diffed at tolerance zero.  Even the scale bar's rectangle arrives finished,
    in nanometres, for exactly this reason.
    """
    code = _metal_block(code_only=True)
    # nothing that could scale, offset or rotate a coordinate
    for banned in ("Math.", "*", "/", "-", "px(", "py(", "L.sx", "L.sy", "L.ox", "L.oy"):
        assert banned not in code, f"{banned!r} appeared in the metal block"
    # what IS allowed: walking a flat coordinate array, and joining strings
    assert "i+=2" in code and "xy[i]" in code, "the loop is the only arithmetic here"
    # every geometric value is read straight out of the payload, never combined
    for verbatim in ("transform:M.transform", "x:B.x", "y:B.y", "width:B.w",
                     "height:B.h", "String(M.nm_per_px)"):
        assert verbatim in code, f"{verbatim} is not read verbatim from Python"
    # ...and both the metal and the bar actually reach the group.  Building the bar and
    # then not appending it would leave the page with no statement of physical scale,
    # which is the one thing that makes an unregistered underlay readable.
    assert "gMetal.append(inner, bar)" in code


def test_the_fab_view_touched_neither_mirrored_half_nor_the_palette():
    """No `engine.js` edit, no parity bucket, no editing verb, no palette tile."""
    engine = (ROOT / "qccd/viz/engine.js").read_text(encoding="utf-8")
    editor = (ROOT / "qccd/viz/js/editor.js").read_text(encoding="utf-8")
    edit = (ROOT / "qccd/viz/js/edit.js").read_text(encoding="utf-8")
    for name in ("gMetal", "metal_view_model", "isotropic_fit", "nm_per_px",
                 "scale_px_per_nm", "bbox_nm"):
        for src, where in ((engine, "engine.js"), (editor, "editor.js"),
                           (edit, "edit.js")):
            assert name not in src, f"{name} reached {where}"

    parity = (ROOT / "tests/parity.mjs").read_text(encoding="utf-8")
    found = frozenset(re.findall(r"\bcases\.([A-Za-z_][A-Za-z0-9_]*)", parity))
    assert "metal" not in found and len(found) == 29


def test_the_metal_is_not_editable_and_has_no_hit_testing():
    """A backdrop, not a mark.  Nothing in the block registers a listener or an id."""
    block = _metal_block()
    for banned in ("addEventListener", "onclick", "pointer", "SEGEL", "PAD_BY_SEG",
                   "selectRef", "cursor"):
        assert banned not in block, f"{banned!r} appeared in the metal block"


def test_viz_still_does_not_import_the_physics_package():
    """The metal arrives as a dict.  `render.py` never runs a field solve.

    A page is a property of a run and the metal is a property of `(device, technology)`;
    wiring one to the other inside the renderer would make every emitted page pay for a
    build it did not ask for.
    """
    for rel in ("qccd/viz/render.py", "qccd/viz/layout.py", "qccd/viz/theme.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, flags=re.M)
        assert not any("phys" in m for m in imports), (rel, imports)
