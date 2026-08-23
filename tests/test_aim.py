"""Can you hit what you can see, and does the picture stay under the cursor?

Two properties, both measured by driving the emitted page rather than by reading the CSS.

**AIM.** The editor's own comments carry the numbers that motivated its hit test -- "only
48% of a cap-4 site's own drawn BAR selected it", "a cyclone segment grabbable on 3% of its
own length". Those were measured once by hand and the code moved underneath them; when I
re-measured, both were already 100%. That is the problem with a number in a comment. This
file makes the measurement repeatable, so the next change to `slop()` / `segBand()` /
`hitRadii()` is answerable instead of argued.

**ANCHOR.** `preserveAspectRatio="xMidYMid meet"` letterboxes whenever the element's
aspect differs from the viewBox's, which it always does now that the canvas is a constant
size. Every client-to-model mapping therefore has to subtract the margin. Forgetting it
has shipped THREE times: `toModel` (clicks landed beside the cursor), the wheel-zoom
anchor (the picture crept away as you zoomed), and the zoom's own fallback branch -- which
was the one the headless harness always took, because `tests/shim.mjs` supplies a `window`
that is not `globalThis`, so `window.EDITOR` was undefined and the guarded path never ran
in any test. Hence: drive it, and drive the fallback too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.compile.programs import build  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402

NODE = shutil.which("node")
AIM = Path(__file__).parent / "aim.mjs"
ARCH = ROOT / "arch"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

#: Sites and rails are what a user aims at. Below these, aiming is guesswork.
MIN_BAR_COVER = 90.0
MIN_SEG_COVER = 80.0


def _page(tmp_path, device, kind="rotate"):
    m = Machine.load(ARCH / f"{device}.arch.json")
    prog = m.program("t").fill().rotate(+1) if kind == "rotate" else build(m.arch, kind, 4)
    dest = tmp_path / f"{device}.html"
    m.render(prog, dest, model=corrected_model())
    return dest


def aim(page: Path, samples: int = 24) -> dict:
    out = subprocess.run([NODE, str(AIM), str(page), str(samples)],
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, f"aim.mjs failed: {out.stdout}\n{out.stderr[-2000:]}"
    return json.loads(out.stdout)


@pytest.mark.parametrize("device,kind", [
    ("cyclone_base", "rotate"), ("ring144_24v", "rotate"),
    ("grid9x9", "walk"), ("h2_racetrack", "rotate"), ("ladder_2x72", "walk"),
])
def test_you_can_click_what_you_can_see(tmp_path, device, kind):
    r = aim(_page(tmp_path, device, kind))
    assert r.get("error") is None, r["error"]
    assert r["bar_cover"] >= MIN_BAR_COVER, (
        f"{device}: only {r['bar_cover']}% of a site's own drawn bar selects it "
        f"(worst {r['bar_worst']}) -- aiming at the mark is guesswork")
    assert r["seg_cover"] >= MIN_SEG_COVER, (
        f"{device}: only {r['seg_cover']}% of a rail's own length selects it "
        f"(worst {r['seg_worst']})")


def test_the_aim_harness_notices_a_shrunken_hit_target(tmp_path):
    """A test that cannot fail is not a test. Shrink the hit radii in a copy of the page
    and confirm the coverage drops through the floor."""
    page = _page(tmp_path, "cyclone_base")
    assert aim(page)["bar_cover"] >= MIN_BAR_COVER

    html = page.read_text(encoding="utf-8")
    # `hitRadii()` only PUBLISHES the radii; `hit()` computes its own from `siteHalfAxis`
    # and `segBand`. Mutating the published table changed nothing, which is itself the
    # lesson: plant on the code the decision actually reads.
    bar = "if (Math.abs(u) <= G.len / 2 + 1e-9 && Math.abs(v) <= L.site_t / 2 + S) {"
    band = "function segBand(S) { return Math.max(L.sw_rail / 2, L.site_t / 2) + S; }"
    assert bar in html and band in html, "the hit test moved; update this mutation"
    broken = tmp_path / "broken.html"
    broken.write_text(
        html.replace(bar, "if (Math.abs(u) <= 1e-3 && Math.abs(v) <= 1e-3) {")
            .replace(band, "function segBand(S) { return 1e-6; }"), encoding="utf-8")
    r = aim(broken)
    assert r["bar_cover"] < MIN_BAR_COVER or r["seg_cover"] < MIN_SEG_COVER, (
        "the harness passed a page whose hit targets are a thousandth of a lattice step")


# --------------------------------------------------------------- the zoom anchor

_ZOOM = """
;globalThis.__Z = { V: window.VIEW, E: EDITOR };
"""


def _drift(page: Path, script: str) -> dict:
    js = tmp = Path(page).with_suffix(".probe.mjs")
    js.write_text(script, encoding="utf-8")
    out = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                         timeout=600)
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr[-2000:]}"
    return json.loads(out.stdout)


DRIFT_JS = """
import {{ loadPage }} from '{shim}';
loadPage(process.argv[2], ';globalThis.__V=window.VIEW; globalThis.__E=EDITOR;');
const V = globalThis.__V, E = globalThis.__E;
let worst = 0;
for (const [cx, cy, dy] of [[700,300,-240],[400,200,-240],[1100,420,240],[250,120,-600]]) {{
  const b = E.toModel(cx, cy);
  V.zoomAt(cx, cy, dy);
  const a = E.toModel(cx, cy);
  worst = Math.max(worst, Math.hypot(a.x-b.x, a.y-b.y));
}}
console.log(JSON.stringify({{ worst: worst }}));
"""


def test_zoom_keeps_the_point_under_the_cursor(tmp_path):
    """Zoom in, zoom out, at four different points. The model coordinate under the cursor
    must not move -- that is what "anchored at the pointer" means, and it is the property
    the letterbox correction exists to preserve."""
    page = _page(tmp_path, "ring144_24v")
    shim = (Path(__file__).parent / "shim.mjs").as_uri()
    r = _drift(page, DRIFT_JS.format(shim=shim))
    assert r["worst"] == 0, (
        f"the picture drifts {r['worst']:.4f} user units from the cursor while zooming; "
        f"the wheel anchor is not subtracting the letterbox margin")


def test_the_zoom_probe_notices_an_unanchored_wheel(tmp_path):
    """Plant the naive fraction -- the exact form this shipped with twice -- and confirm
    the drift check fails."""
    page = _page(tmp_path, "ring144_24v")
    html = page.read_text(encoding="utf-8")
    marker = "const m = (typeof EDITOR !== 'undefined' && EDITOR.toModel)"
    assert marker in html, "the zoom anchor moved; update this mutation"
    broken = tmp_path / "unanchored.html"
    broken.write_text(html.replace(marker, "const m = (false && EDITOR.toModel)"),
                      encoding="utf-8")
    shim = (Path(__file__).parent / "shim.mjs").as_uri()
    # the fallback is now ALSO letterbox-correct, so neutering the guard alone is not
    # enough -- break the fallback's margin too, which is the state that actually shipped
    html2 = broken.read_text(encoding="utf-8").replace(
        "y: VB.y + (clientY - fit.r.top  - fit.oy) / fit.s }",
        "y: VB.y + (clientY - fit.r.top) / Math.max(1, fit.r.height) * VB.h }")
    broken.write_text(html2, encoding="utf-8")
    r = _drift(broken, DRIFT_JS.format(shim=shim))
    assert r["worst"] > 1.0, (
        "the probe passed a page whose zoom is not anchored at the pointer")
