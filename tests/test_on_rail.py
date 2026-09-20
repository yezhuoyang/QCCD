"""An ion is drawn on the metal that carries it, or it is not drawn honestly.

A QCCD machine moves an ion by ramping the well under it from one electrode to the next.
There is nothing beside the rail to hold an ion, so a picture that puts one there is not a
coarser drawing of the same motion -- it is a drawing of a motion the hardware cannot make.
At a junction it is worse than that: the middle of a two-hop walk lands exactly on the
corner, so an ion drawn wide at mid-flight reads as jumping OVER the junction square
instead of running into it and turning.

That is what happened, and it was reported from the published site against
`board/bb144/22_planar12_own_a72`.  The cause was the detour that keeps two ions from
being drawn through each other: its amplitude peaked at mid-flight regardless of whether
anything was there to avoid, so every two-hop walk swung `swap_bow` -- 0.62 g, most of a
lattice step -- wide of the junction in the middle of it.  Measured on that page before
the fix: 290 marks more than 0.12 g off the rail, the worst of them 0.62 g out beside
J0_6.  After: nothing at all out on the open rail.

The detour could not simply be deleted; without it, 213 frames of `cyclone_base`'s
odd-even sort draw one ion through another.  It is now derived from the geometry instead
of shaped: an ion already clear of everything it has to pass is drawn on the rail, and one
that is closer is lifted by exactly the perpendicular leg that restores the clearance,
which is zero once it is a slot pitch away.  Nothing rests at a junction -- a junction has
no capacity -- so a crossing has nothing to pass and is drawn on the metal.

Two assertions, because the rule has two halves.  A TRAP has length and a stack to arrange
inside it, so a mark may sit off the rail's centre line by the trap's own slot pitch.
OPEN RAIL has no such licence: out between the traps, and at every junction, an ion is on
the metal.
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
from qccd.cost import corrected_model, deck_model  # noqa: E402

HARNESS = Path(__file__).parent / "on_rail.mjs"
NODE = shutil.which("node")
ARCH = ROOT / "arch"

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the emitted page cannot be executed"
)

#: Out on the rail an ion is on the metal.  The tolerance is a twelfth of a lattice step,
#: which is thinner than the rail is drawn -- it is here for floating point, not for slack.
OPEN_RAIL_TOL = 0.02

#: Inside a trap the stack is arranged along the bar, and getting past a trap-mate needs
#: about one mark's width across it.  A slot pitch is the trap's own scale.
IN_TRAP_TOL = 0.40


def _probe(page: Path, frames: int = 60) -> dict:
    out = subprocess.run([NODE, str(HARNESS), str(page), str(frames), "8"],
                         capture_output=True, text=True, timeout=900)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    return json.loads(out.stdout)


def _page(tmp_path: Path, name: str, kind: str) -> Path:
    """A device and a programme that actually moves ions through junctions."""
    m = Machine.load(ARCH / f"{name}.arch.json")
    if kind == "rotate":
        prog, model = m.program("t").fill().rotate(+1), corrected_model()
    elif kind == "oddeven":
        prog, model = build_program(m.arch, "oddeven"), corrected_model()
    else:
        prog, model = build_program(m.arch, "walk", 4), corrected_model()
    dest = tmp_path / f"{name}_{kind}.html"
    m.render(prog, dest, model=model)
    return dest


#: `grid9x9` and `deck_unit_cell` are the lattices -- every transport on them crosses a
#: junction, which is the case the bug was reported on.  `cyclone_base`'s odd-even sort is
#: the one programme that genuinely needs the detour, so it is the case that proves the
#: fix did not simply switch it off.
CASES = [
    ("grid9x9", "walk"),
    ("deck_unit_cell", "walk"),
    ("ladder_2x72", "walk"),
    ("cyclone_base", "oddeven"),
    ("ring144_24v", "rotate"),
]


@pytest.fixture(scope="module", params=CASES, ids=lambda c: f"{c[0]}_{c[1]}")
def probed(request, tmp_path_factory) -> dict:
    name, kind = request.param
    tmp = tmp_path_factory.mktemp("on_rail")
    return _probe(_page(tmp, name, kind))


def test_no_ion_is_drawn_off_the_rail_out_between_the_traps(probed):
    """THE REPORTED BUG.  Away from every trap there is only metal and junctions, and an
    ion is on the metal or it is nowhere the machine could put it."""
    w = probed["worst_on_open_rail_at"]
    assert probed["worst_on_open_rail_g"] <= OPEN_RAIL_TOL, (
        f"{probed['page']}: an ion is drawn {probed['worst_on_open_rail_g']} g off the "
        f"rail while {w and w['clear_of_trap_g']} g clear of every trap bar -- "
        f"{w and w['junction_away_g']} g from junction {w and w['nearest_junction']}, "
        f"frame {w and w['frame']} phase {w and w['phase']}. Ions follow the rail into a "
        f"junction and turn; they do not cut across it.")


def test_no_ion_strays_further_than_its_own_trap_is_wide(probed):
    """Inside a trap there is room to arrange a stack, but not unlimited room."""
    w = probed["worst_at"]
    assert probed["worst_off_rail_g"] <= IN_TRAP_TOL, (
        f"{probed['page']}: worst stray {probed['worst_off_rail_g']} g at {w}")


def test_the_measurement_saw_ions_move(probed):
    """A page that drew nothing would pass both assertions above."""
    assert probed["marks"] > 300, f"only {probed['marks']} marks sampled"
    assert probed["frames"] > 1, probed["frames"]


@pytest.mark.parametrize("name,kind", [("grid9x9", "walk"), ("cyclone_base", "oddeven")])
def test_the_probe_would_catch_a_detour_that_ignores_the_geometry(tmp_path, name, kind):
    """A test that cannot fail is not a test.

    Put the old unconditional detour back -- amplitude peaking at mid-flight whatever is
    or is not there -- and the open-rail assertion must go red.  This is the exact shape
    of the defect that shipped.
    """
    page = _page(tmp_path, name, kind)
    html = page.read_text(encoding="utf-8")
    # put back the detour as it was: applied to every flier, peaking at mid-flight, at
    # the full `swap_bow` amplitude, with no reference to what is or is not in the way
    gate = "if (me.fly && mates && mates.length) {"
    amp = "        var worst = 0;"
    loop = "for (var mi = 0; mi < mates.length; mi++) {"
    gap = "for (var mj = 0; mj < mates.length; mj++) {"
    for needle in (gate, amp, loop, gap):
        assert needle in html, f"the detour moved; update this mutation ({needle!r})"
    broken = tmp_path / f"{name}_broken.html"
    safe = "(mates ? mates.length : 0)"
    broken.write_text(html.replace(gate, "if (me.fly) {")
                          .replace(amp, "        var worst = bow0 * 4 * t * (1 - t);")
                          .replace(loop, f"for (var mi = 0; mi < {safe}; mi++) {{")
                          .replace(gap, f"for (var mj = 0; mj < {safe}; mj++) {{"),
                      encoding="utf-8")
    r = _probe(broken, frames=40)
    assert r["worst_on_open_rail_g"] > OPEN_RAIL_TOL, (
        "the probe passed a page whose ions leave the rail on every walk -- it would not "
        "have caught the defect it exists to catch")
