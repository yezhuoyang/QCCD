"""Resizing, zooming and re-fitting must not change what the picture SAYS.

The stage is drawn once in SVG user units and shown through a viewBox, so a resize or a
zoom is a transform rather than a re-layout -- which is exactly why it is worth asserting:
that is a property of how the page is built, not a law of nature, and a change that started
recomputing marks per view would break it silently.  Two things are measured across every
view:

  * the ions still do not overlap, and every DC pair still has both its electrodes;
  * the SLOT each ion holds is byte-identical across all of them.

The second is the one that catches an occupancy rule secretly reading pixels.  If which
slot an ion held depended on the drawn distance rather than on the device, an ion would
change places with its trap-mate when you made the window narrower -- and the only way to
see that is to make the window narrower and look.

The views walked are four window sizes (which move the page between its narrow, tall and
wide panel regimes), two zoom levels, and the true-scale toggle -- the one control that
genuinely re-fits the drawing, since it replaces the anisotropic fit with the technology's
own nm-per-unit ratio and so moves every mark and every electrode.
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
from qccd.compile.programs import build as build_program  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402

HARNESS = Path(__file__).parent / "stage_scale.mjs"
NODE = shutil.which("node")
ARCH = ROOT / "arch"

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the emitted page cannot be executed"
)

#: One device with two ions to a trap (the ring's docks) and one lattice, because the two
#: stack their slots along different axes.
DEVICES = ["ring144_24v", "grid9x9"]


def _page(tmp_path: Path, name: str) -> Path:
    m = Machine.load(ARCH / f"{name}.arch.json")
    prog = (m.program("t").fill().rotate(+1) if name == "ring144_24v"
            else build_program(m.arch, "walk", 4))
    dest = tmp_path / f"{name}.html"
    m.render(prog, dest, model=corrected_model())
    return dest


def _scale(page: Path) -> dict:
    out = subprocess.run([NODE, str(HARNESS), str(page), "24", "4"],
                         capture_output=True, text=True, timeout=900)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    return json.loads(out.stdout)


@pytest.fixture(scope="module", params=DEVICES)
def views(request, tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("stage_scale")
    return _scale(_page(tmp, request.param))


def test_no_view_draws_one_ion_through_another(views):
    for rec in views["sizes"]:
        assert rec["overlap_samples"] == 0, (
            f"{views['page']} at {rec['label']}: {rec['overlap_samples']} of "
            f"{rec['samples']} instants overlap, worst {rec['worst_pair']}")


def test_every_view_draws_both_electrodes_of_every_dc_pair(views):
    for rec in views["sizes"]:
        assert rec["pairs_with_one_pad"] == 0, f"{rec['label']}: a half-drawn pair"
        assert rec["pads_north"] == rec["pads_south"] > 0, (
            f"{rec['label']}: north {rec['pads_north']}, south {rec['pads_south']}")


def test_the_slot_an_ion_holds_does_not_depend_on_the_drawn_size(views):
    assert views["slots_identical_across_sizes"], (
        "the slot assignment changed with the view: an ion swaps places with its "
        "trap-mate when the window is resized")


def test_a_resize_moves_the_panels_and_not_the_marks(views):
    """Resizing is a viewBox change, so the drawing itself must be untouched.

    If this ever fails it is not necessarily a bug -- it means the page started re-fitting
    on resize -- but then the overlap assertion above becomes the load-bearing one and
    this test should be rewritten rather than deleted.
    """
    sizes = [r for r in views["sizes"] if r["label"][0].isdigit()]
    regimes = {r["data_layout"] for r in sizes}
    assert len(regimes) > 1, (
        f"all four window sizes landed in the same panel regime ({regimes}), so this "
        f"proves nothing about resizing")
    for rec in sizes[1:]:
        assert rec["g"] == sizes[0]["g"]
        assert rec["similarity"]["spread"] == 0, (
            f"{rec['label']} is not the same picture as {sizes[0]['label']}")
        assert not rec.get("ion_order_changed")


def test_the_true_scale_toggle_refits_the_drawing_and_keeps_both_properties(views):
    """The one control that really does move every mark.

    It swaps the fit that fills the viewport for one whose sx:sy is the technology's
    nm-per-unit ratio, so the picture is a DIFFERENT similarity -- which is the point, and
    is why `spread` is not asserted to be zero here.  What must survive is the drawing's
    two invariants and the slot assignment.
    """
    assert views["true_scale_available"], "the true-scale control was not reachable"
    refit = [r for r in views["sizes"] if r["label"].startswith("true scale")]
    assert refit, "the toggle produced no view"
    if all(r["g"] == views["sizes"][0]["g"] for r in refit):
        # An isotropic device under an isotropic technology is fitted identically either
        # way -- `grid9x9` is square and `surface_default` is 464 x 464 um per unit -- so
        # there is no second fit to check.  Not a failure, and not something to assert
        # around: the ring in the same parametrisation does move.
        pytest.skip("true scale and the viewport fit agree on this device")
    for rec in refit:
        assert rec["overlap_samples"] == 0, rec["worst_pair"]
        assert rec["pads_north"] == rec["pads_south"] > 0
