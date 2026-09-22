"""Two ions that trade places in one site are SEEN to (tests/swap_visible.mjs).

A collaborator, via the user: when a pair of ions swap, "show they are swapped -- not just
go through each other, but slightly off the middle"; and the user again, "one goes left,
the other goes right ... make sure this is enforced everywhere, including all leaderboard
circuits".  The occupancy law (`qccd/viz/js/transit.js`) steps both of a pair aside, to
opposite sides, by half the bar's half-thickness at the moment they are side by side.

`swap_visible.mjs` does not ask the law whether it did that.  It finds the swaps in the
DRAWING -- two ions whose order along one trap's bar reverses over a step while they meet
inside it -- and reads, at their crossing, how far each is from that bar's centre line and
on which side.  A pass the law never recognised, or one it sent to the wrong side, is
exactly what it would see: on the leaderboard's `24_cylinder12_own_a72`, 482 passes whose
alternating side contradicted the tilt of the bar were drawn crossing each other, and on
`stationary_chain` a crowded trap's pair stepped aside 0.029 g against 0.050 elsewhere.
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

PROBE = Path(__file__).parent / "swap_visible.mjs"
NODE = shutil.which("node")
ARCH = ROOT / "arch"

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the emitted page cannot be executed")

#: half the bar's half-thickness, in lattice steps, on the shipped layout: the amplitude
#: every swap reaches at its crossing (sampled, so a little short of it)
AMPLITUDE = 0.0498


def _page(tmp_path: Path, name: str, kind: str) -> Path:
    m = Machine.load(ARCH / f"{name}.arch.json")
    if kind == "oddeven":
        prog = build_program(m.arch, "oddeven")
    else:
        prog = build_program(m.arch, "walk", 4)
    dest = tmp_path / f"{name}_{kind}.html"
    m.render(prog, dest, model=corrected_model())
    return dest


def _probe(page: Path, frames: int = 80) -> dict:
    out = subprocess.run([NODE, str(PROBE), str(page), str(frames)],
                         capture_output=True, text=True, timeout=1800)
    assert out.returncode == 0, f"probe failed:\n{out.stdout}\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


#: `cyclone_base`'s odd-even sort swaps trap-mates hundreds of times; `grid9x9`'s walk
#: passes ions through occupied traps on a lattice
CASES = [("cyclone_base", "oddeven"), ("grid9x9", "walk")]


@pytest.fixture(scope="module", params=CASES, ids=lambda c: f"{c[0]}_{c[1]}")
def page(request, tmp_path_factory) -> Path:
    return _page(tmp_path_factory.mktemp("swap"), *request.param)


@pytest.fixture(scope="module")
def seen(page) -> dict:
    return _probe(page)


def test_every_swap_is_drawn_as_a_swap(seen):
    """Both off the line, on opposite sides; never one alone, never both the same way,
    never straight through, and the marks never overlap."""
    bad = {k: seen[k] for k in ("one", "same", "none", "overlap") if seen[k]}
    assert not bad, f"{seen['page']}: {bad} of {seen['swaps']} swaps; worst {seen['worst']}"


def test_the_cyclone_sort_is_all_swaps_at_one_size(page, seen):
    """The page that swaps most, so the rule is exercised and not merely unviolated."""
    if "cyclone" not in page.name:
        pytest.skip("the odd-even sort is the case with trap-mates swapping")
    assert seen["swaps"] >= 100, seen
    assert seen["both"] == seen["swaps"], seen
    assert seen["min_dev_g"] >= 0.9 * AMPLITUDE, seen


def test_the_probe_sees_a_swap_the_waiting_ion_does_not_make(tmp_path):
    """A test that cannot fail is not a test: stop the ion being passed from stepping
    aside, and every swap must turn into `one`."""
    page = _page(tmp_path, "cyclone_base", "oddeven")
    html = page.read_text(encoding="utf-8")
    needle = "for (io3 in need) if (!base[io3].fly) order.push(io3);"
    assert needle in html, "the resters' lift moved; update this mutation"
    broken = tmp_path / "broken.html"
    broken.write_text(html.replace(needle, ";"), encoding="utf-8")
    r = _probe(broken, frames=20)
    assert r["one"] > 0 and r["both"] < r["swaps"], r
