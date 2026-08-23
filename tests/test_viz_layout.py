"""The stage has to be legible before anything drawn on it can be believed.

The old renderer fitted every device into a fixed 1000x460 box with one isotropic scale
and then sized every mark from the *node count* (`300/sqrt(n+1)`, clamped to 13). Node
count and pixel spacing are unrelated, so the numbers came out like this:

    device        px between nearest sites    ion radius drawn     overlap
    chain72                    1.0                  10.66            21x
    stationary_chain           1.0                  10.66            21x
    ring144_24v                6.4                  10.66           3.3x
    ladder_2x72               12.8                  10.66          1.66x

Three of the nine shipped architectures drew every ion on top of its neighbours, and two
of them drew the whole device as a 71-pixel smear in the middle of a blank canvas. None
of that was catchable by a test, because the geometry only existed inside a JavaScript
string.

`qccd.viz.layout` moves it into Python and ships it in the page's JSON blob, so these
tests can do the thing that actually matters: **parse the emitted HTML back, recompute the
minimum nearest-neighbour distance in drawn pixels from the coordinates and the scale the
page will really use, and require that two ion discs cannot touch.** The brute-force gap
below is deliberately written from scratch rather than calling the layout module's own
sweep, so a bug in that sweep cannot hide behind itself.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.compile import build  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.verify import replay  # noqa: E402
from qccd.viz import render_html  # noqa: E402
from qccd.viz.layout import (  # noqa: E402
    ISO_ASPECT,
    PITCH_CAP,
    W_MAX,
    pad_tiling,
    site_length,
)

ARCH_DIR = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH_DIR.glob("*.arch.json"))

#: below this the picture is not worth drawing, whatever the device
GAP_FLOOR = 18.0
ION_FLOOR = 5.0


@pytest.fixture(scope="session")
def page(tmp_path_factory):
    """`device -> (html text, parsed data blob)`, each device rendered once."""
    out = tmp_path_factory.mktemp("viz_layout")
    cache: dict[str, tuple[str, dict]] = {}

    def get(device: str) -> tuple[str, dict]:
        if device not in cache:
            arch = load(ARCH_DIR / f"{device}.arch.json")
            prog = build(arch, "walk")
            res = replay(prog, arch, corrected_model(), check_rules=False)
            path = render_html(arch, prog, res, corrected_model(),
                               out / f"{device}.html")
            text = path.read_text(encoding="utf-8")
            blob = re.search(
                r'<script id="data" type="application/json">(.*?)</script>', text, re.S)
            assert blob, f"{device}: no data block in the emitted page"
            cache[device] = (text, json.loads(blob.group(1)))
        return cache[device]

    return get


# --------------------------------------------------------------- helpers


def screen_points(view: dict) -> list[tuple[float, float]]:
    """Every node where the page will actually put it, in viewBox units."""
    lay = view["layout"]
    return [(lay["ox"] + n["x"] * lay["sx"], lay["oy"] + n["y"] * lay["sy"])
            for n in view["arch"]["nodes"]]


def brute_force_gap(pts) -> float:
    """Minimum nearest-neighbour distance, the obvious way. O(n^2), n <= 288 here."""
    best = math.inf
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if 1e-9 < d < best:
                best = d
    return best


def point_to_segment(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


# --------------------------------------------------------------- the headline check


@pytest.mark.parametrize("device", DEVICES)
def test_no_device_can_draw_two_ions_on_top_of_each_other(device, page):
    """Two ion discs on the nearest pair of sites must not touch.  Every device."""
    _, view = page(device)
    lay = view["layout"]
    gap = brute_force_gap(screen_points(view))
    r = lay["r_ion"]
    assert 2 * r <= gap + 1e-9, (
        f"{device}: ion radius {r:.2f} on a {gap:.2f} px nearest-neighbour gap "
        f"-- the discs overlap by {2 * r - gap:.2f} px")
    # the page's own measurement has to agree with what we just recomputed
    assert lay["g"] == pytest.approx(gap, rel=1e-6)


@pytest.mark.parametrize("device", DEVICES)
def test_every_device_is_drawn_large_enough_to_read(device, page):
    _, view = page(device)
    lay = view["layout"]
    assert lay["g"] >= GAP_FLOOR, f"{device}: only {lay['g']:.1f} px between sites"
    assert lay["r_ion"] >= ION_FLOOR, f"{device}: ion radius {lay['r_ion']:.1f} px"
    assert lay["sx"] > 1.0 and lay["sy"] > 1.0, f"{device}: degenerate scale {lay}"


# --------------------------------------------------------------- the three shapes


def test_a_flat_one_dimensional_chain_is_not_one_pixel_per_trap(page):
    """The old fit gave a degenerate y axis the literal `1` -- as a pixels-per-unit
    scale -- and `Math.min` then adopted it for both axes.  chain72 drew its 72 traps
    71 pixels apart with a 10.66 px ion radius."""
    text, view = page("chain")
    lay = view["layout"]
    assert lay["sx"] > 1.0, "a flat device must not fall back to 1 px per data unit"
    assert lay["sx"] >= 15.0
    assert lay["sy"] == pytest.approx(lay["sx"]), "no y extent: nothing to stretch"
    gap = brute_force_gap(screen_points(view))
    assert 2 * lay["r_ion"] <= gap
    assert lay["W"] == int(W_MAX), "a 71:1 strip should use the full canvas width"
    assert 'viewBox="0 0 1000 460"' not in text


def test_the_71_to_1_ring_uses_the_canvas_it_is_given(page):
    """ring144_24v is 71 x 1 data units.  An isotropic fit draws it as a 912 x 13 px
    hairline; the rails then sit 12.8 px apart with a 9.75 px stroke between them."""
    _, view = page("ring144_24v")
    lay = view["layout"]
    assert lay["iso"] is False and lay["axis_aligned"] is True
    assert lay["sy"] > lay["sx"], "the flat axis is the one that needs the room"
    assert lay["sy"] / lay["sx"] <= 12.0 + 1e-9, "anisotropy stays capped"
    gap = brute_force_gap(screen_points(view))
    assert 2 * lay["r_ion"] <= gap
    # the two rails are one data unit apart; they must end up far enough apart to see
    rails = lay["sy"] * 1.0
    assert rails >= 4 * lay["sw_rail"], (rails, lay["sw_rail"])
    # and the ancillas at y = 0.5 must clear both of them
    assert 0.5 * lay["sy"] >= 2 * lay["r_ion"]


def test_the_square_grid_stays_square(page):
    """dx == dy on a 0.5 lattice: stretching it would be a lie, and the old fixed
    1000x460 box wasted 63% of its width on a 1:1 device."""
    _, view = page("grid9x9")
    lay = view["layout"]
    assert lay["iso"] is True
    assert lay["sx"] == pytest.approx(lay["sy"])
    assert lay["W"] == lay["H"], (lay["W"], lay["H"])
    gap = brute_force_gap(screen_points(view))
    assert 2 * lay["r_ion"] <= gap
    span_x = (lay["x1"] - lay["x0"]) * lay["sx"]
    span_y = (lay["y1"] - lay["y0"]) * lay["sy"]
    fill = (span_x * span_y) / (lay["W"] * lay["H"])
    assert fill >= 0.70, f"content fills only {fill:.0%} of the canvas"


def test_identical_geometry_lays_out_identically(page):
    """grid9x9 and deck_unit_cell are the same 225 nodes wired two ways."""
    _, a = page("grid9x9")
    _, b = page("deck_unit_cell")
    assert a["layout"] == b["layout"]


@pytest.mark.parametrize("device", DEVICES)
def test_the_canvas_is_sized_from_the_device_not_hard_coded(device, page):
    text, view = page(device)
    lay = view["layout"]
    assert 'viewBox="0 0 1000 460"' not in text
    assert f'`0 0 ${{L.W}} ${{L.H}}`' in text, "the viewBox must come from the layout"
    assert 240 <= lay["W"] <= W_MAX and 120 <= lay["H"] <= 900
    # a real reserved margin, big enough for the largest thing drawn outside the bbox
    assert lay["pad"] >= lay["r_active"], (lay["pad"], lay["r_active"])


# --------------------------------------------------------------- the invariants


@pytest.mark.parametrize("device", DEVICES)
def test_the_invariants_that_make_overlap_structurally_impossible(device, page):
    """Each of these is a strict inequality in units of `g`, so it holds at any scale.
    A count-derived radius can never promise that; that is the whole point."""
    _, view = page(device)
    L = view["layout"]
    g = L["g"]
    # I1  two ions on adjacent sites
    assert 2 * L["r_ion"] < g
    # I2  an in-flight ion is visibly bigger than one at rest
    assert L["r_ion"] > L["r_rest"] * 1.3
    # I3  two junction squares
    assert 2 * L["r_junc"] < g
    # I4  an ion beside a junction: the junction stays visible
    assert L["r_ion"] + L["r_junc"] < g
    # I5  the rail reads as a wire the ions are beaded onto, not a slab
    assert L["sw_rail"] <= 0.24 * (2 * L["r_ion"]) + 1e-9
    # I6  the pads sit clear of the rail stroke, and are thicker than it
    assert L["sw_rail"] < L["pad_t"]
    assert L["pad_off"] - L["pad_t"] / 2 - L["sw_rail"] / 2 > 0
    # I7  a travelling well never reaches the site it is heading for
    assert L["well_rx"] < 0.5 * g
    # I8  the active-site highlight stays inside its own half cell
    assert L["r_active"] < 0.5 * g
    # I9  nothing clips at the canvas edge
    assert L["pad"] > L["r_active"]
    # I10 two ions mid-flight on perpendicular arms of one junction are g/sqrt(2) apart
    assert 2 * L["r_ion"] < g / math.sqrt(2)
    # a site's own bar never reaches its neighbour's
    assert L["site_max"] < g


@pytest.mark.parametrize("device", DEVICES)
def test_the_electrode_tiling_is_discrete_at_every_scale(device, page):
    """`k` comes from the segment's drawn length, not the node count.  The old page put
    three pads on a one-pixel segment (three 8 px rects exactly on top of each other)
    and one pad on a 46 px one."""
    _, view = page(device)
    lay = view["layout"]
    pos = {n["id"]: (lay["ox"] + n["x"] * lay["sx"], lay["oy"] + n["y"] * lay["sy"])
           for n in view["arch"]["nodes"]}
    seen = 0
    for s in view["arch"]["segments"]:
        a, b = pos[s["a"]], pos[s["b"]]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= 1e-6:
            continue
        k, pitch, pad_len = pad_tiling(length, lay["g"])
        seen += 1
        assert k >= 1
        assert pitch == pytest.approx(length / k)
        gap = pitch - pad_len
        assert 0.20 <= gap / pitch <= 0.36, (s["id"], gap / pitch)
        assert pad_len < pitch, "two pads on one rail must not overlap"
    assert seen == len(view["arch"]["segments"])


# --------------------------------------------------------------- site capacity


@pytest.mark.parametrize("device", DEVICES)
def test_site_capacity_is_actually_drawn(device, page):
    """`cap` used to be exported and read exactly zero times: the only capacity mark on
    the whole page was one aggregate integer in the hardware table."""
    text, view = page(device)
    assert "n.cap" in text, "the page must read the per-node capacity"
    assert "siteLen" in text and "slotOffsets" in text
    assert "capacity_histogram" in text, "the side panel must break capacity down"
    assert all("cap" in n for n in view["arch"]["nodes"])
    assert all("cap" in s for s in view["arch"]["segments"])


@pytest.mark.parametrize("device", DEVICES)
def test_a_bigger_trap_is_drawn_as_a_longer_well(device, page):
    """Capacity is encoded as bar length plus one slot mark per ion the site can hold,
    and two ions sharing a site are drawn in different slots rather than on top of
    each other."""
    _, view = page(device)
    g = view["layout"]["g"]
    r_rest = view["layout"]["r_rest"]
    caps = sorted({n["cap"] for n in view["arch"]["nodes"] if n["kind"] != "junction"})
    for cap in caps:
        if cap == 0:
            continue
        length = site_length(cap, g)
        assert length <= 0.88 * g + 1e-9, "a site bar never reaches its neighbour"
        slots = max(1, min(cap, 6))
        pitch = length / slots
        # what the page draws for two ions sharing one site
        r = min(r_rest, 0.44 * pitch)
        assert 2 * r <= pitch + 1e-9, (cap, r, pitch)
        assert r > 0.6, (cap, r)
    assert site_length(4, g) > site_length(2, g) or site_length(2, g) == 0.88 * g


# --------------------------------------------------------------- honest geometry


@pytest.mark.parametrize("device", DEVICES)
def test_a_segment_never_runs_straight_through_a_node_it_does_not_touch(device, page):
    """The shipped ring's two corner docks sit exactly on the end caps, and the dual-loop
    Cyclone's A-loop end caps are three units long and cross four D-loop nodes.  Drawn as
    chords, those say the loops intersect.  They are bowed instead."""
    _, view = page(device)
    lay = view["layout"]
    pos = {n["id"]: (lay["ox"] + n["x"] * lay["sx"], lay["oy"] + n["y"] * lay["sy"])
           for n in view["arch"]["nodes"]}
    bows = lay["bows"]
    for s in view["arch"]["segments"]:
        if s["id"] in bows:
            continue
        a, b = pos[s["a"]], pos[s["b"]]
        for n in view["arch"]["nodes"]:
            if n["id"] in (s["a"], s["b"]):
                continue
            d = point_to_segment(pos[n["id"]], a, b)
            assert d >= 0.55 * lay["g"], (
                f"{device}: node {n['id']} is {d:.1f} px from segment {s['id']}, "
                f"which is not bowed")
    for offset in bows.values():
        assert abs(offset) >= 0.4 * lay["g"], "a bow that small would not clear anything"


def test_only_the_devices_that_need_a_bow_get_one(page):
    """Seven of the nine shipped devices are drawn with straight chords throughout."""
    bowed = {d: sorted(page(d)[1]["layout"]["bows"]) for d in DEVICES}
    assert bowed["ring144_24v"] == ["E143", "E71"]
    assert bowed["cyclone_dual_loop"] == ["EA35", "EA71"]
    assert [d for d, b in bowed.items() if b] == ["cyclone_dual_loop", "ring144_24v"]


# --------------------------------------------------------------- detail levels


def test_labels_are_decided_by_size_not_by_node_count(page):
    """`showNums = A.nodes.length <= 60` was a third count heuristic, orthogonal to
    whether a glyph fits: it labelled a 2-node device at a 1 px pitch and refused to
    label a 225-node grid at a 50 px one."""
    _, grid = page("grid9x9")
    _, ring = page("ring144_24v")
    assert len(grid["arch"]["nodes"]) > len(ring["arch"]["nodes"])
    assert grid["layout"]["labels"] is True
    assert ring["layout"]["labels"] is False


def test_the_scale_is_capped_so_a_two_node_device_is_not_two_blobs(page):
    _, view = page("stationary_chain")
    lay = view["layout"]
    assert lay["sx"] <= PITCH_CAP + 1e-9
    assert lay["r_ion"] <= 26.0
    assert lay["W"] < 700, "a 2-node device does not need a billboard"


def test_a_square_bounding_box_is_never_stretched(page):
    for device in DEVICES:
        _, view = page(device)
        lay = view["layout"]
        dx, dy = lay["x1"] - lay["x0"], lay["y1"] - lay["y0"]
        if dy > 0 and 1.0 / ISO_ASPECT <= dx / dy <= ISO_ASPECT:
            assert lay["sx"] == pytest.approx(lay["sy"]), device
