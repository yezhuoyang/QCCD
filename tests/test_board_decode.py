"""A `decode` on a leaderboard page lights the wires to the decoder (docs/tsir.md).

The user looked at `board/bb144/22_planar12_own_a72` and saw a decoder, dashed wires and a
classical memory drawn under the device -- and nothing ever used them: the programme had no
instruction that sent an outcome anywhere.  Now every leaderboard programme calls the
decoder once per syndrome window (`qccd.compile.decode`), and the site's QEC-cycle layer
(`qccd/site/qec_cycle.js`) lights, on that frame, the wire from every site where one of its
ions was MEASURED down to the bus, along it to the decoder, and on to the memory.

`tests/board_decode_browser.mjs` measures the picture rather than the page's own counts:
lit-colour pixels on the rasterised stage the frame before and the frame of the decode,
every ring against its site's own mark before and after a zoom, and what playing at the
default speed shows.  A mutation that switches the highlight off must turn it red.  The
stage now FRAMES the classical floor (`fit()` reads `STAGE_EXTENT`), or the outcomes would
be seen leaving for somewhere off the edge of the picture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "board_decode_browser.mjs"
CHROME = os.environ.get("CHROME") or next((p for p in (
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium") if Path(p).exists()), None)
NODE = shutil.which("node")
SCRIPTS = ROOT / "Codesign" / "scripts"

pytestmark = [pytest.mark.skipif(NODE is None or CHROME is None,
                                 reason="node and Chrome are needed to look at the page"),
              pytest.mark.skipif(not (SCRIPTS / "bb_studio.py").exists(),
                                 reason="the leaderboard page builder is not in this tree")]


def _entry():
    """The smallest compiled small-code entry with a few sites, so the build is quick."""
    best = None
    for fam in ("steane", "five_qubit", "surface17", "repetition"):
        man = ROOT / "SmallCode" / fam / "manifest.json"
        if not man.exists():
            continue
        for e in json.loads(man.read_text(encoding="utf-8")):
            if e.get("status") != "ok" or not e.get("prefix") or (e.get("sites") or 0) < 4:
                continue
            if not Path(str(e["prefix"]) + ".cooled.tsir.json").exists():
                continue
            if best is None or e.get("instructions", 1e9) < best.get("instructions", 1e9):
                best = e
    return best


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    entry = _entry()
    if entry is None:
        pytest.skip("no compiled small-code programme in this tree")
    sys.path.insert(0, str(SCRIPTS))
    import bb_studio as B
    from qccd.site import qec_cycle
    from qccd.site.build import HASH_JS, with_nav
    out = tmp_path_factory.mktemp("board_decode")
    res = B.build_one(dict(entry), out, max_frames=20000)
    assert res["status"] == "ok", res
    p = out / res["page"]
    # the classical layer, exactly as the site build gives an entry page it
    p.write_text(with_nav(p.read_text(encoding="utf-8"), 2, "board", "[]", app=True,
                          extra=HASH_JS + qec_cycle.studio_block()),
                 encoding="utf-8", newline="")
    return p


def probe(p: Path, shot: Path | None = None) -> dict:
    args = [NODE, str(PROBE), str(p)] + ([str(shot)] if shot else [])
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def seen(page):
    return probe(page, page.with_suffix(".decode.png"))


def test_the_programme_calls_the_decoder_and_says_so(seen):
    assert seen["errors"] == [] and "fatal" not in seen, seen.get("fatal")
    assert seen["decodes"], "the leaderboard programme has no decode frame"
    for d in seen["decodes"]:
        assert d["ions"] >= 1 and d["sites"] >= 1, d
    assert seen["listing"] == "DECODE"


def test_the_wires_to_the_decoder_light_on_the_decode_frame_and_only_there(seen):
    px = seen["pixels"]
    assert px["lit_elements_before"] == 0, px
    assert px["lit_elements_during"] > 0, px
    lit_gain = px["during"][0] - px["before"][0]
    assert lit_gain > 200 and px["during"][0] >= 5 * max(1, px["before"][0]), px


def test_every_ring_sits_on_its_site_through_a_zoom(seen):
    reg = seen["registration"]
    for when in ("before_zoom", "after_zoom"):
        r = reg[when]
        assert r and r["rings"] == r["sites"] == r["measured"] and r["rings"] >= 1, r
        assert r["worst_px"] < 0.5, (when, r)


def test_playing_lands_on_the_decode_frame_and_lights_it(seen):
    pb = seen["playback"]
    assert pb["samples_on_decode"] >= 1, pb
    assert pb["lit_while_on"] == pb["samples_on_decode"], pb
    assert pb["lit_while_off"] == 0, pb
    # and goes on -- unless the decode is the programme's last frame, where play stops
    assert pb["passed_it"] or pb["decode_frame"] == pb["last_frame"], pb


def test_the_probe_would_see_a_highlight_that_never_comes_on(page, tmp_path):
    """A test that cannot fail is not a test: switch the lit layer off and the pixel
    assertion above must go red."""
    html = page.read_text(encoding="utf-8")
    needle = 'el.innerHTML = key ? buildLit(decodeSites(at, at.fi), geo) : "";'
    assert needle in html, "the lit layer moved; update this mutation"
    broken = tmp_path / page.name
    broken.write_text(html.replace(needle, 'el.innerHTML = "";'), encoding="utf-8", newline="")
    off = probe(broken)
    px = off["pixels"]
    assert not (px["during"][0] - px["before"][0] > 200
                and px["during"][0] >= 5 * max(1, px["before"][0])), px
