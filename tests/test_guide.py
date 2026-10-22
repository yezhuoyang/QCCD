"""Every lesson can be finished by doing only what Hint shows.

Hint is a walk-through: the page dims, one control is ringed, a pointer makes the gesture
(qccd/viz/js/editor.js, "THE GUIDE").  `tests/guide.mjs` builds nothing; it opens the course
page in headless Chrome, presses the real Hint button, and then makes exactly the gesture
the guide shows -- a click, a right-click, a shift-drag, keystrokes -- with real input,
asking the page before every press whether that point reaches the ringed control.  A lesson
passes only when its own check passes at the end.  So a guide that points at the wrong
place, a ring a reader cannot press, or a lesson whose text and solution no longer match
what the page does (C4's runtime bound was such a lesson) fails here.
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
RUNNER = ROOT / "tests" / "guide.mjs"

node = shutil.which("node")
CHROME = [p for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome",
                      "/usr/bin/chromium-browser", "/usr/bin/chromium") if Path(p).exists()]
needs_browser = pytest.mark.skipif(node is None or not CHROME, reason="node and Chrome are needed")


@needs_browser
def test_every_lesson_is_finished_by_following_its_hint(tmp_path: Path):
    out = tmp_path / "tutorial"
    built = subprocess.run([sys.executable, "-m", "qccd", "tutorial", "-o", str(out)],
                           capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    assert built.returncode == 0, built.stderr[-2000:]
    assert (out / "micro_grid9x9.html").exists(), "the companion page D1 runs on was not built"
    r = subprocess.run([node, str(RUNNER), str(out / "studio.html")],
                       capture_output=True, text=True, timeout=3600, cwd=str(ROOT))
    lines = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    assert lines, r.stderr[-3000:]
    summary, each = lines[-1], [l for l in lines if "id" in l]
    failed = [l for l in each if not l.get("ok")]
    assert not failed and summary.get("ok") and r.returncode == 0, json.dumps(failed, indent=1)
    # every lesson in the course was followed, not just the first few
    assert len(each) == summary["lessons"] >= 31, summary
