"""The tutorial's gadget figures: `qccd.gadget.site.embed_page`.

A figure is one page and nothing beside it -- every script and all its data inline -- that a
cross-origin-isolated page can hold in an <iframe>: it fetches nothing, never navigates the
page around it, shows no repository path, and answers to `window.GADGET = {ready, focus,
seek}` and to `#f=ID&t=US`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.gadget.site import embed_page

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "out" / "gadgets" / "cache"


@pytest.fixture(scope="module")
def figures(tmp_path_factory):
    d = tmp_path_factory.mktemp("figures")
    names = {"algorithms/t_gate": "algo.html", "msf15": "place.html", "msf15.produce": "op.html"}
    return d, {n: embed_page(n, d / f, cache_dir=CACHE, log=None) for n, f in names.items()}


def test_a_figure_is_one_file_and_names_nothing_outside_it(figures):
    d, pages = figures
    assert sorted(p.name for p in d.iterdir()) == ["algo.html", "op.html", "place.html"]
    for name, p in pages.items():
        html = p.read_text(encoding="utf-8")
        # no subresource at all: every <script>, <link>, <img> and <iframe> is inline or absent
        assert not re.search(r"<(script|link|img|iframe|source|video|audio)\b[^>]*\b(src|href)\s*=",
                             html, re.I), name
        assert not re.search(r"@import|url\(\s*['\"]?https?:", html), name
        assert "window.top" not in html and "window.opener" not in html, name
        # every link the page itself writes opens elsewhere
        for a in re.findall(r"<a [^>]*href=[^>]*>", html):
            assert 'href="#"' in a or "target=" in a, (name, a)


def test_the_names_a_figure_may_have(tmp_path):
    for bad, says in [("algorithms/nope", "no example algorithm"), ("nowhere", "no gadget page"),
                      ("msf15.nope", "has no op")]:
        with pytest.raises(KeyError, match=says):
            embed_page(bad, tmp_path / "x.html", cache_dir=CACHE, log=None)


def test_a_figure_in_an_isolated_frame_fetches_nothing_and_answers_its_handle(figures):
    sys.path.insert(0, str(ROOT / "tests"))
    from chrome_path import CHROME
    node = shutil.which("node")
    if node is None or CHROME is None:
        pytest.skip("node and Chrome are needed to look at the page")
    d, _ = figures
    r = subprocess.run([node, str(ROOT / "tests" / "gadget_embed_browser.mjs"), str(d),
                        "algo.html", "op.html"], capture_output=True, text=True, encoding="utf-8",
                       timeout=300, env={**__import__("os").environ, "FOCUS": "i3,msf15,L999"})
    assert r.returncode == 0, r.stderr
    algo, op = (json.loads(l) for l in r.stdout.strip().splitlines()[-2:])
    for seen in (algo, op):
        assert seen["isolated"], seen          # else the frame proves nothing
        assert seen["errors"] == [] and seen["requests"] == [], seen
        assert seen["topKept"], seen
        # no repository path in any text a reader can reach -- and the probe can see one
        assert seen["paths"] == [] and seen["planted"], seen
    assert algo["handle"] == ["ready", "focus", "seek"] and algo["ready"]
    # `#f=i3&t=1000` on load: instruction 3 shown, the clock where the address put it
    assert algo["atLoad"]["sel"] == {"kind": "ins", "id": 3} and algo["atLoad"]["t"] == 1000
    f = algo["focus"]
    assert f["i3"]["ok"] and f["i3"]["t"] > 0 and f["i3"]["inspector"].startswith("instruction 3")
    assert f["msf15"]["ok"] and f["msf15"]["sel"] == {"kind": "inst", "path": "factory"}
    assert f["L999"]["ok"] is False and algo["missing"] is False
    assert algo["seek"] == 1234


def test_a_place_figure_opens_on_that_place(figures):
    _, pages = figures
    html = pages["msf15"].read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="gdata" type="application/json">(.*?)</script>',
                                html, re.S).group(1))
    assert data["focus"] == "msf15"
    assert data["leafFiles"] and all(v == "" for v in data["leafFiles"].values())
    assert set(data["leafInline"]) == set(data["leafFiles"])


def test_a_place_device_goes_to_the_studio_as_any_device(tmp_path):
    from qccd.arch.device import Architecture
    from qccd.gadget.site import export_place_arch
    p = export_place_arch("msf15", tmp_path / "place-msf15.arch.json", cache_dir=CACHE)
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["name"] == "msf15"
    Architecture.from_json(doc)                  # loads as any device does
    with pytest.raises(KeyError, match="no place"):
        export_place_arch("dec_d3", tmp_path / "x.json", cache_dir=CACHE)   # holds no ions
