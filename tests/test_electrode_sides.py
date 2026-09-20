"""A DC control electrode pair is two electrodes, one each side of the RF rail.

`qccd/phys/build.py::_segment_polys` builds the real metal that way -- a `north` band at
`dc_setback .. dc_setback + dc_width` from the axis and a `south` band mirroring it -- and
it is not a stylistic choice: a surface trap confines and shuttles with electrodes on both
sides, and there is no process that makes the one-sided version.

The schematic on the studio page used to disagree with that above a drawing budget:

    const sides = 2*(nSitePairs+nRailPairs) > 4000 ? [1] : [1,-1];

Over 4,000 rectangles it dropped the south side and drew half of every pair.  That is not a
coarser picture of the same hardware, it is a picture of different and impossible hardware,
and a reader counting pads on the screen would have counted a device that cannot trap an
ion.  It fired on the two biggest shipped-shape devices -- `grid11x11` at junction pitch 2
(660 site pairs and 1,760 rail pairs) and `ladder_2x72` -- and `grid11x11` at pitch 2 is
the buildable 11x11 lattice, the one worth demonstrating.

The budget itself is still there.  It is spent on how many POSITIONS are tiled, and past
8,000 rectangles the rail falls back to a dash pattern that states the same pitch.  What it
may never do is halve a pair.
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

from qccd.arch import load  # noqa: E402
from qccd.arch.generators import grid  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.viz.render import render_html  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.verify import verify  # noqa: E402

HARNESS = Path(__file__).parent / "editor.mjs"
NODE = shutil.which("node")
ARCH = ROOT / "arch"

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the emitted page cannot be executed"
)


def _pads(page: Path, tmp_path: Path) -> dict:
    script = tmp_path / "steps.json"
    script.write_text("[]", encoding="utf-8")
    out = subprocess.run([NODE, str(HARNESS), str(page), str(script)],
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    r = json.loads(out.stdout)
    pads = (r.get("steps") or [{}])[0].get("pads")
    assert pads is not None, "the electrode probe did not run"
    return pads


def _page(tmp_path: Path, arch, name: str) -> Path:
    prog = TSIR(name="empty", arch_spec=name)
    res = verify(prog, arch, corrected_model()).result
    dest = tmp_path / f"{name}.html"
    return Path(render_html(arch, prog, res, corrected_model(), dest))


def _grid_arch(a: int, b: int, spacing: int, name: str):
    """An `a x b` lattice, wrapped in the machine the renderer wants.

    The 11x11 lattice is not a committed `arch/*.arch.json` -- the study builds it -- so it
    is generated here from the same `qccd.arch.generators.grid` the study calls.
    """
    from qccd import Machine
    dev = grid(a, b, spacing=spacing)
    return Machine.from_device(dev, name=name, template="grid9x9").arch


# --------------------------------------------------------- the 11x11 demonstration


@pytest.mark.parametrize("spacing", [1, 2])
def test_the_11x11_grid_draws_both_sides_of_every_dc_pair(tmp_path, spacing):
    """The demonstration device, at the pitch that cannot be built and the one that can.

    `spacing=2` is the case that regressed: 660 site pairs plus 1,760 rail pairs is 4,840
    rectangles, over the old 4,000 guard, so the whole south side vanished.
    """
    arch = _grid_arch(11, 11, spacing, f"grid11x11_s{spacing}")
    pads = _pads(_page(tmp_path, arch, f"grid11x11_s{spacing}"), tmp_path)
    assert pads["sites"] == 220, pads          # 2ab - a - b
    assert pads["pairs_with_one_pad"] == 0, (
        f"{pads['pairs_with_one_pad']} of {pads['pairs_total']} pairs are drawn as a "
        f"single electrode: that is not a trap")
    assert pads["pads_north"] == pads["pads_south"] > 0, (
        f"the two sides are not drawn equally: north {pads['pads_north']}, "
        f"south {pads['pads_south']}")
    assert pads["worst_offset_gap"] < 1e-6, (
        "the two electrodes of a pair are not mirror-symmetric about the rail")


def test_the_buildable_11x11_is_the_case_that_used_to_lose_a_side(tmp_path):
    """The arithmetic of the old guard, stated so the regression cannot come back quietly.

    If this count ever drops back under the old threshold the test above stops proving
    anything, and this is what says so.
    """
    arch = _grid_arch(11, 11, 2, "grid11x11_s2")
    pads = _pads(_page(tmp_path, arch, "grid11x11_s2"), tmp_path)
    assert pads["rects"] > 4000, (
        f"only {pads['rects']} pad rectangles: this device no longer exercises the "
        f"budget path that used to halve every pair")
    assert pads["pads_north"] == pads["pads_south"]


# ------------------------------------------------------- and everywhere else it is used


@pytest.mark.parametrize("name", [
    "ring144_24v", "ladder_2x72", "grid9x9", "deck_unit_cell",
    "cyclone_base", "cyclone_dual_loop", "h2_racetrack", "chain",
])
def test_every_shipped_device_draws_both_sides_of_every_dc_pair(tmp_path, name):
    """`ladder_2x72` is the other device the old guard fired on."""
    pads = _pads(_page(tmp_path, load(ARCH / f"{name}.arch.json"), name), tmp_path)
    if pads["sites_with_pads"] == 0:
        assert pads["dashed"] > 0
        return
    assert pads["pairs_with_one_pad"] == 0, pads
    assert pads["pads_north"] == pads["pads_south"] > 0, pads
    assert pads["worst_offset_gap"] < 1e-6, pads


def test_the_schematic_and_the_derived_metal_agree_that_a_pair_has_two_sides(tmp_path):
    """The schematic is not the authority on this; `qccd.phys` is.

    Tying the two together means the drawing cannot be 'fixed' by changing the picture
    while the metal underneath keeps saying something else.
    """
    from qccd.phys.build import build_layout
    from qccd.phys.tech import load_technology

    arch = load(ARCH / "chain.arch.json")
    layout = build_layout(arch, load_technology("surface_default"))
    bands = {p.net.split(":")[-2] for p in layout.flatten()
             if p.role == "dc_pad" and "DC:" in p.net}
    assert {"north", "south"} <= bands, (
        f"the derived metal has bands {sorted(bands)}: the schematic's two sides are "
        f"supposed to be these two")
    pads = _pads(_page(tmp_path, arch, "chain"), tmp_path)
    assert pads["pads_north"] == pads["pads_south"] > 0
