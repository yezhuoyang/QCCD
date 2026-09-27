"""The Studio verbs an agent is handed do what the person's gestures do.

`addSite` / `addSegment` used to write post-seal topology edits on every device.  On a device
with a builder -- a lesson's canvas -- the builder never saw those nodes, so the loop closed over
them next was refused with "unknown node", and an agent following the verb list could not finish
lesson A3 by hand (found on 2026-09-27, walking the course through the website's chat).  They now
take the path the click and the shift-drag take (`placeStamp`, `joinNodes`); a generator device,
which has no builder, still gets topology edits.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

DRIVE = r"""
import { loadPage } from './shim.mjs';
loadPage(process.argv[2], ';globalThis.__E=EDITOR;');
const E = globalThis.__E, out = {};
E.lessonLoad('A3');
const sites = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([x, y]) => E.addSite(x, y));
const ids = sites.map((r) => r.id);
const segs = ids.map((a, i) => E.addSegment(a, ids[(i + 1) % 4]));
out.lesson = { sites: sites.map((r) => r.ok), segs: segs.map((r) => r.ok),
               loop: E.closeLoop('L0', ids, true, 'ring'), check: E.lessonCheck(),
               source_sites: (E.source().match(/d\.site\(/g) || []).length };
console.log(JSON.stringify(out));
"""


def test_an_agent_can_finish_lesson_a3_with_the_declared_verbs(tmp_path):
    page = tmp_path / "studio.html"
    r = subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    drv = ROOT / "tests" / "_agent_verbs_drive.mjs"
    drv.write_text(DRIVE, encoding="utf-8")
    try:
        r = subprocess.run([NODE, str(drv), str(page)], cwd=ROOT / "tests", capture_output=True, text=True, timeout=300)
    finally:
        drv.unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])["lesson"]
    assert all(got["sites"]) and all(got["segs"]), got
    assert got["source_sites"] == 4, "the sites must be the builder's, or a loop cannot name them"
    assert got["loop"]["ok"], got["loop"]
    assert got["check"]["passed"], got["check"]
