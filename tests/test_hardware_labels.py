"""Hardware labels: the names a hardware programme is written in, on the picture.

The user, 2026-10-23, writing a programme in the tutorial: `p.shuttle("d0", ["S0", ...])` names
sites and ions the canvas did not show unless the pointer rested on one.  Every node's id now
sits beside it and every ion's whole name beside the ion, on by default, and `#tLabels` hides
them.  (A label too small to read at the current zoom steps aside; that needs a real browser
to measure and is not asserted here.)
"""

from __future__ import annotations

import json
import subprocess

from test_studio import ROOT, STUDIO, _url, node, requires_node, requires_studio

_DESIGN = [
    {"do": "canvas", "opts": {"name": "tee", "template": "ring144_24v"}},
    {"do": "node", "x": 0.0, "y": 0.0, "opts": {"id": "S0", "zone": "data"}},
    {"do": "node", "x": 1.0, "y": 0.0, "opts": {"id": "S1", "zone": "data"}},
    {"do": "node", "x": 2.0, "y": 0.0, "opts": {"id": "J0", "kind": "junction"}},
    {"do": "node", "x": 3.0, "y": 0.0, "opts": {"id": "A0", "zone": "trap"}},
    {"do": "join", "a": "S0", "b": "S1", "opts": {"id": "E0"}},
    {"do": "join", "a": "S1", "b": "J0", "opts": {"id": "E1"}},
    {"do": "join", "a": "J0", "b": "A0", "opts": {"id": "E2"}},
    {"do": "prog", "src": 'p.init({"d0": "S0", "a0": "A0"})\np.shuttle("d0", ["S0", "S1", "J0", "A0"])\n'},
]


def _run(tmp_path):
    probe = tmp_path / "hwl.mjs"
    probe.write_text(
        "import { loadPage } from " + _url(ROOT / "tests" / "shim.mjs") + ";\n"
        "import { PAGE_HOOK, applyStep } from " + _url(ROOT / "tests" / "drive.mjs") + ";\n"
        "globalThis.__QCCD_SYNC = true;\n"
        "loadPage(" + json.dumps(str(STUDIO)) + ", PAGE_HOOK);\n"
        "const ED = globalThis.EDITOR, PG = globalThis.__page;\n"
        "for (const s of " + json.dumps(_DESIGN) + ") applyStep(ED, PG, s);\n"
        "PG.drawAt(0, 1);\n"
        "const b = () => { const e = document.getElementById('tLabels');\n"
        "  return [e.className, e.getAttribute('aria-pressed')]; };\n"
        "const out = { built: PG.hwLabels(), button: b() };\n"
        "out.off = PG.setHwLabels(false); out.offState = PG.hwLabels(); out.offButton = b();\n"
        "out.back = PG.setHwLabels(); out.backState = PG.hwLabels();\n"
        "process.stdout.write(JSON.stringify(out));\n", encoding="utf-8")
    r = subprocess.run([node, str(probe)], capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout)


@requires_node
@requires_studio
def test_every_site_junction_and_ion_is_named_on_the_canvas_by_default(tmp_path):
    out = _run(tmp_path)
    b = out["built"]
    assert b["on"] is True and b["shown"] is True
    # a node's label is its id; an ion's is its whole name (the number inside the ion was
    # the name with `d`/`a` cut off, which is not what a programme writes)
    assert {s[0]: s[1:] for s in b["sites"]} == {k: [k, True] for k in ("S0", "S1", "J0", "A0")}
    assert {i[0]: i[1:] for i in b["ions"]} == {k: [k, True] for k in ("d0", "a0")}
    assert out["button"] == ["tgl on", "true"]           # its hint is markup, which the shim does not read


@requires_node
@requires_studio
def test_the_labels_button_hides_them_and_brings_them_back(tmp_path):
    out = _run(tmp_path)
    assert out["off"] == {"on": False, "shown": False}
    assert not any(s[2] for s in out["offState"]["sites"]) and not any(i[2] for i in out["offState"]["ions"])
    assert out["offButton"] == ["tgl", "false"]
    assert out["back"] == {"on": True, "shown": True}         # no argument: it flips
    assert all(s[2] for s in out["backState"]["sites"]) and all(i[2] for i in out["backState"]["ions"])


def test_the_label_control_is_explained():
    """A control ships with its hover text (the HINTS table) -- the agent interface reads it too."""
    js = (ROOT / "qccd" / "viz" / "js" / "editor.js").read_text(encoding="utf-8")
    page = (ROOT / "qccd" / "viz" / "render.py").read_text(encoding="utf-8")
    assert "'hwlabels': { t: 'Hardware labels'" in js
    assert 'id="tLabels"' in page and 'data-hint="hwlabels"' in page
