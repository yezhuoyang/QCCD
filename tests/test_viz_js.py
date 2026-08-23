"""Execute the emitted page and measure what it actually draws.

`tests/test_viz_layout.py` tests `qccd.viz.layout` -- the Python that decides where marks
go. That is necessary and not sufficient: an adversarial review mutated the *JavaScript*
ion radius to `2.5 * L.r_ion`, reinstating gross ion-on-ion overlap on all nine devices,
and every one of the 107 existing viz tests still passed. The drawing layer had no test.

So this file runs the page. `tests/census.mjs` stubs just enough DOM for the template to
build its SVG, drives `frame`/`phase` directly, and reads position and radius back off the
ion marks. Two properties are then measured over real frames of real programs:

**No two ions ever overlap.** Not "the node lattice has room" -- the marks themselves, at
every sampled phase. The distinction is not academic: the lattice was clean while 40% of
the shipped deck program's frames drew a docking ion straight on top of the ancilla
waiting for it, because two ions in ONE site are invisible to a lattice measurement.

**No ion jumps at a frame boundary.** The end of frame *n* must be exactly the start of
frame *n+1*. A 3.27 px seam on all 144 ions twice per frame is what "the ions do not move
continuously" actually looked like, and it survived precisely because nothing measured it.

Both are regression tests for defects that were live in the shipped pages.
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
from qccd.cost import corrected_model, deck_model  # noqa: E402

#: `.rotate()` needs one rotation loop; a grid or a ladder has none, so those walk a few
#: ions across the fabric instead -- the same programs `python -m qccd demo` renders.
ROTATES = {"ring144_24v", "cyclone_base", "cyclone_dual_loop", "h2_racetrack"}


def program_for(m, name):
    if name in ROTATES:
        return m.program("t").fill().rotate(+1)
    return build(m.arch, "walk", 4)

HARNESS = Path(__file__).parent / "census.mjs"
NODE = shutil.which("node")
ARCH = ROOT / "arch"

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the emitted page cannot be executed"
)


def census(page: Path, frames: int = 100_000) -> dict:
    """Run the page and report what it drew."""
    out = subprocess.run(
        [NODE, str(HARNESS), str(page), str(frames)],
        capture_output=True, text=True, timeout=600,
    )
    assert out.returncode == 0, f"harness failed: {out.stdout}\n{out.stderr}"
    return json.loads(out.stdout)


def _page(tmp_path, name, make, model=None):
    m = Machine.load(ARCH / f"{name}.arch.json")
    prog = make(m)
    dest = tmp_path / f"{name}.html"
    m.render(prog, dest, model=model or corrected_model())
    return dest


# --------------------------------------------------------------- the two properties


@pytest.mark.parametrize("name", [
    "ring144_24v", "ladder_2x72", "grid9x9", "deck_unit_cell",
    "cyclone_base", "cyclone_dual_loop", "h2_racetrack",
])
def test_no_two_ions_ever_overlap(tmp_path, name):
    page = _page(tmp_path, name, lambda m: program_for(m, name))
    r = census(page)          # every frame, not a prefix
    assert r.get("probe_error") is None, r["probe_error"]
    assert r["probed"] > 0, "the harness drew nothing"
    assert r["overlap_frames"] == 0, (
        f"{name}: {r['overlap_pct']}% of frames draw overlapping ions, "
        f"worst {r['worst_overlap_px']} px -- {r['worst_pair']}")


@pytest.mark.parametrize("name", [
    "ring144_24v", "ladder_2x72", "grid9x9", "cyclone_base",
])
def test_no_ion_jumps_at_a_frame_boundary(tmp_path, name):
    page = _page(tmp_path, name, lambda m: program_for(m, name))
    r = census(page)
    assert r["worst_boundary_snap_px"] == 0, (
        f"{name}: an ion moves {r['worst_boundary_snap_px']} px between the end of one "
        f"frame and the start of the next ({r['snap_at']}) -- motion is discontinuous")


# ------------------------------------------- the cases a rigid rotation cannot reach


def test_docking_into_an_occupied_site_does_not_draw_one_ion_over_another(tmp_path):
    """The defect a `fill().rotate(1)` program structurally cannot expose.

    A rotation never puts two ions in one site, so it never exercises the slot logic at
    all. Docking does: the data ion joins the ancilla already sitting in the trap. That
    is where 40% of the shipped program's frames were drawing one circle inside another.
    """
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    p = m.program("contact").fill()
    p.rotate(+1)
    with p.cycle("dock") as c:
        c.move("d143", "S0", "A0", via=["V0"])
    p.gate("CX", [("d143", "a0")], sites=["A0"])
    with p.cycle("undock") as c:
        c.move("d143", "A0", "S0", via=["V0"])
    dest = tmp_path / "contact.html"
    m.render(p, dest, model=deck_model())
    r = census(dest)
    assert r["overlap_frames"] == 0, (
        f"a dock/undock draws overlapping ions in {r['overlap_pct']}% of frames, "
        f"worst {r['worst_overlap_px']} px -- {r['worst_pair']}")
    assert r["worst_boundary_snap_px"] == 0, r["snap_at"]


def test_two_ions_leaving_one_trap_do_not_pass_through_each_other(tmp_path):
    """Cyclone's odd-even sort splits and merges pairs inside cap-4 traps, so ions
    genuinely have to exchange order. That is a swap, and a swap is not free -- the page
    draws the detour rather than a straight line through the neighbour, and either way
    the two marks must never intersect."""
    m = Machine.load(ARCH / "cyclone_base.arch.json")
    p = build(m.arch, "oddeven")
    dest = tmp_path / "oddeven.html"
    m.render(p, dest, model=corrected_model())
    r = census(dest)
    assert r["overlap_frames"] == 0, (
        f"odd-even sort draws overlapping ions in {r['overlap_pct']}% of frames, "
        f"worst {r['worst_overlap_px']} px -- {r['worst_pair']}")


def test_a_one_dimensional_trap_is_drawn_at_a_readable_scale(tmp_path):
    """The page the user opened. A degenerate y axis used to contribute the literal 1 to
    a min-over-pixels-per-unit, collapsing 12 traps onto 11 pixels."""
    m = Machine.chain(12, name="linear_trap")
    p = m.program("shuttle").init({"d1": "C0"})
    p.shuttle("d1", [f"C{i}" for i in range(12)])
    dest = tmp_path / "chain.html"
    m.render(p, dest)
    r = census(dest)
    assert r["overlap_frames"] == 0, r["worst_pair"]
    assert r["worst_boundary_snap_px"] == 0, r["snap_at"]


# ------------------------------------------------------------------ the harness itself


def test_the_harness_actually_catches_an_oversized_ion(tmp_path):
    """A test that cannot fail is not a test. Mutate the drawn radius in the emitted page
    and confirm the census reports the overlap -- this is the exact mutation that slipped
    past all 107 layout tests."""
    page = _page(tmp_path, "ring144_24v", lambda m: m.program("t").fill().rotate(+1))
    assert census(page)["overlap_frames"] == 0

    html = page.read_text(encoding="utf-8")
    marker = "let r = (pt.fly||act) ? L.r_ion : L.r_rest;"
    assert marker in html, "the radius line moved; update this mutation"
    broken = tmp_path / "broken.html"
    # 2.5x does NOT overlap any more -- the marks now carry that much clearance, which
    # is exactly the margin the rebuild bought. Breach it properly: at 4x, 2*r_rest
    # exceeds the lattice gap and neighbouring ions must intersect.
    broken.write_text(html.replace(marker, marker + " r *= 4.0;"), encoding="utf-8")

    r = census(broken)
    assert r["overlap_frames"] > 0, (
        "the census passed a page drawing ions at 4x radius -- it would not have "
        "caught the defect it exists to catch")


# ------------------------------------------------- the dock: what the panels render

PANELS = Path(__file__).parent / "panels.mjs"


def panels(page: Path, frames: int = 40) -> dict:
    """Run the page under the same shim and report what the two listings drew."""
    out = subprocess.run(
        [NODE, str(PANELS), str(page), str(frames)],
        capture_output=True, text=True, timeout=600,
    )
    assert out.returncode == 0, f"panel harness failed: {out.stdout}\n{out.stderr}"
    return json.loads(out.stdout)


def test_the_program_panel_has_a_row_for_every_frame_and_follows_the_cursor(tmp_path):
    """The brief, executed: the user sees the hardware program, the instruction being
    animated is highlighted, and it stays in view as the animation plays.

    This runs the real emitted page.  It is also the smoke test for the whole dock --
    the listing work is called synchronously from `draw()`, so anything that throws
    surfaces here rather than in a browser nobody opened.
    """
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    p = m.program("contact").fill()
    p.rotate(+1)
    with p.cycle("dock") as c:
        c.move("d143", "S0", "A0", via=["V0"])
    p.gate("CX", [("d143", "a0")], sites=["A0"])
    with p.cycle("undock") as c:
        c.move("d143", "A0", "S0", via=["V0"])
    dest = tmp_path / "dock.html"
    m.render(p, dest, model=corrected_model())

    r = panels(dest)
    assert r.get("probe_error") is None, r["probe_error"]
    assert r["listingRows"] == r["frames"], "one listing row per animated instruction"
    assert r["ids"] == r["frameIds"], "row ids ARE instruction ids, in program order"
    assert r["cursor_wrong"] == 0, "the cursor must track `frame` exactly"
    assert r["rows_missing"] == 0, "the executing row must be in the rendered window"
    assert r["control_lines"] == r["probed"], "every frame says what is driving it"
    # the pool is bounded: this is windowing, not 2000 rows of DOM
    assert r["pool"] <= 96 and r["arch_pool"] <= 96


def test_the_architecture_statement_that_authorised_the_instruction_is_lit(tmp_path):
    """The join between the two panels: an instruction's SIMD class is an architecture
    id, so the page can point at the `declare_class` line that permitted the cycle."""
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    p = m.program("t").fill().rotate(+2)
    with p.cycle("dock") as c:
        c.move("d142", "S0", "A0", via=["V0"])
    dest = tmp_path / "arch.html"
    m.render(p, dest, model=corrected_model())

    r = panels(dest, frames=3)
    assert r.get("probe_error") is None, r["probe_error"]
    assert r["archLines"] > 40 and r["archIndexKeys"] > 100
    # frame 0 is `init`, which no movement class authorises; 1 and 2 do have one
    assert r["arch_lit"] >= 2, r["samples"]
    rot, dock = r["samples"][1], r["samples"][2]
    assert rot["archCursor"] >= 0 and dock["archCursor"] >= 0
    assert rot["archCursor"] != dock["archCursor"], (
        "a rotation and a dock are authorised by different statements")


def test_the_panels_survive_a_two_thousand_instruction_program(tmp_path):
    """The shipped schedule: 1,579 instructions, virtualised.  The row pool stays
    constant, and the deduplicated control table stays small enough to embed."""
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    prog = build(m.arch, "deck")
    dest = tmp_path / "deck.html"
    m.render(prog, dest, model=corrected_model())

    # 120, not 30. The row pool is 36 wide, so probing only the first 30 frames never
    # leaves the initially-rendered window and the scroll path is never entered at all:
    # deleting the scroll call outright still passed. At 120 the cursor must actually be
    # scrolled into view, and deleting the scroll gives rows_missing = 84.
    r = panels(dest, frames=120)
    assert r.get("probe_error") is None, r["probe_error"]
    assert r["frames"] > 1500 and r["listingRows"] == r["frames"]
    assert r["pool"] <= 96, "windowing: the DOM does not grow with the program"
    assert r["ctlRecords"] <= 20, "distinct control states, not one per instruction"
    assert r["ctlIndex"] == r["frames"]
    assert r["cursor_wrong"] == 0
    assert r["rows_missing"] == 0, (
        "the executing instruction left the rendered window and was not scrolled back "
        "into view -- 'keep the current instruction visible' is the headline requirement")


def test_the_panel_harness_catches_a_listing_that_stops_following(tmp_path):
    """A test that cannot fail is not a test -- the same discipline as the ion-radius
    mutation above, applied to the panel.  Neuter the scroll-into-view call in the
    emitted page and confirm the harness notices."""
    m = Machine.load(ARCH / "ring144_24v.arch.json")
    dest = tmp_path / "deck.html"
    m.render(build(m.arch, "deck"), dest, model=corrected_model())
    assert panels(dest, frames=120)["rows_missing"] == 0

    html = dest.read_text(encoding="utf-8")
    marker = "if(T.follow && out) T.scrollToRow(i,CENTRE); else T.mark();"
    assert marker in html, "the follow line moved; update this mutation"
    broken = tmp_path / "broken.html"
    broken.write_text(html.replace(marker, "T.mark();"), encoding="utf-8")

    assert panels(broken, frames=120)["rows_missing"] > 0, (
        "the harness passed a page whose listing never scrolls -- it would not have "
        "caught the defect it exists to catch")
