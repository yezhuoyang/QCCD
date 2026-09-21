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

THEN IT WAS REPORTED AGAIN, from the same place and about the other half: "when ions
swap, they shouldn't jump outside the site".  The licence a trap has is ALONG its bar,
which is where the stack is; across the bar there is none, and the detour that lets two
ions get past each other was spending 0.21 g on a bar whose half-thickness is 0.10 g.  So
`on_metal.mjs` asks the question with no licence in it at all -- is every mark inside the
union of the trap capsules and the rails, the metal that is actually drawn? -- and
`test_no_ion_is_drawn_off_the_metal_at_all` is the standing check.  Three things had to
change for it to read zero, and they are in `qccd/viz/js/transit.js`: the detour is capped
by the metal under the ion (half a bar's thickness, half a rail's), a slot offset is taken
up over an arc that depends on the angle between the rail and the bar, and a mark is held
to half the space it is in whether it is the ion moving or the ion being got past.
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
METAL = Path(__file__).parent / "on_metal.mjs"
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


def _probe(page: Path, frames: int = 60, harness: Path = HARNESS) -> dict:
    out = subprocess.run([NODE, str(harness), str(page), str(frames), "8"],
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
def rendered(request, tmp_path_factory) -> Path:
    name, kind = request.param
    return _page(tmp_path_factory.mktemp("on_rail"), name, kind)


@pytest.fixture(scope="module")
def probed(rendered) -> dict:
    return _probe(rendered)


@pytest.fixture(scope="module")
def on_metal(rendered) -> dict:
    return _probe(rendered, harness=METAL)


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


def test_no_ion_is_drawn_off_the_metal_at_all(on_metal):
    """THE SECOND REPORT, and the whole rule in one number.

    `worst_on_open_rail_g` above allows a mark inside a trap to sit off the rail's centre
    line, because a trap has a stack to arrange ALONG its bar.  That licence is along the
    bar; across it there is none, and the second thing the reader saw was a swapping ion
    stepping sideways out of its own site -- 0.21 g across a bar whose half-thickness is
    0.10 g -- to get round the ion it was exchanging with.

    So this asks the question with no licence in it: is every mark inside the union of
    the trap capsules and the rails, the metal that is actually drawn?  Where the room to
    pass is not there, it is the MARKS that give way and not the confinement: a mark is
    capped at half the gap it is threading, so the exchange is still drawn as an
    exchange, with the two ions on opposite sides and in each other's slots at the end.
    """
    w = on_metal["worst_at"]
    assert on_metal["worst_outside_metal_g"] <= on_metal["tolerance_g"], (
        f"{on_metal['page']}: {on_metal['marks_off_metal']} of "
        f"{on_metal['marks']} marks are drawn off the metal, the worst "
        f"{on_metal['worst_outside_metal_g']} g outside it -- ion {w and w['ion']} at "
        f"frame {w and w['frame']} phase {w and w['phase']}, nearest bar "
        f"{w and w['nearest_bar']}. An ion is in a trap or on a rail; there is nothing "
        f"else to hold one.")


def test_the_measurement_saw_ions_move(probed, on_metal):
    """A page that drew nothing would pass both assertions above."""
    assert probed["marks"] > 300, f"only {probed['marks']} marks sampled"
    assert probed["frames"] > 1, probed["frames"]
    assert on_metal["marks"] == probed["marks"], "the two probes saw different pictures"


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
    # the full `swap_bow` amplitude, with no reference to what is or is not in the way,
    # and free to leave the metal (`lim`, the confinement, is what stops it now)
    # (the detour is `need` per pair, then `lift` per ion: every flier is given one, of
    # the mid-flight bow, and the cap is taken off)
    gate = "for (io3 in need) if (base[io3].fly) order.push(io3);"
    amp = "h3 = need[ion3]"
    cap = "if (lim3 > 0 && h3 > 0.5 * lim3) h3 = 0.5 * lim3;"
    for needle in (gate, amp, cap):
        assert needle in html, f"the detour moved; update this mutation ({needle!r})"
    broken = tmp_path / f"{name}_broken.html"
    broken.write_text(html.replace(gate, "for (io3 in base) if (base[io3].fly) order.push(io3);")
                          .replace(amp, "h3 = bow0 * 4 * t * (1 - t)")
                          .replace(cap, ";"),
                      encoding="utf-8")
    r = _probe(broken, frames=40)
    assert r["worst_on_open_rail_g"] > OPEN_RAIL_TOL, (
        "the probe passed a page whose ions leave the rail on every walk -- it would not "
        "have caught the defect it exists to catch")
    m = _probe(broken, frames=40, harness=METAL)
    assert m["worst_outside_metal_g"] > m["tolerance_g"], (
        "the same page, and the off-metal probe did not see it either")


@pytest.mark.parametrize("name,kind", [("cyclone_base", "oddeven")])
def test_the_probe_would_catch_a_slot_taken_up_out_on_the_rail(tmp_path, name, kind):
    """The OTHER half, and the one `on_rail.mjs` cannot see.

    A slot offset lies along its own trap's bar, so adding it never changes how far the
    ion is from the bar's centre line -- that distance is the rail's own.  Take the angle
    between rail and bar out of the ramp, so the offset is always taken up over the whole
    bar, and an ion turning into a trap whose bar lies ACROSS the rail it arrives on cuts
    the corner of the T.  It stays within a trap's own scale of the rail centre line the
    whole time, so the open-rail assertion above never sees it; this one must.
    """
    page = _page(tmp_path, name, kind)
    html = page.read_text(encoding="utf-8")
    needle = "var r = sin > 1e-6 ? a / sin : (sp || a);"
    assert needle in html, "the slot ramp moved; update this mutation"
    broken = tmp_path / f"{name}_corner.html"
    broken.write_text(html.replace(needle, "var r = (sp || a);"), encoding="utf-8")
    m = _probe(broken, frames=40, harness=METAL)
    assert m["worst_outside_metal_g"] > m["tolerance_g"], (
        "an ion took up its slot a whole bar-length out along the rail and the probe "
        "called it on the metal")
