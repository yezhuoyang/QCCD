"""THE COMMENT LAYER IN A REAL BROWSER (`qccd/site/comments.html` over `comments_api.py`).

`tests/comments.mjs` serves one page the site build wrote -- here a small document page
made through the same `with_nav` every site page passes through -- with `/api/` proxied to
the real API on a temporary database, and drives headless Chrome through the story: the
admin invites a reader and copies the link, she follows it and the link makes her account,
she pins a note to a paragraph with a real click, the pin stands on that paragraph through
scrolling, a reload and a narrower window, she replies and deletes; she gets the note out
of the way of the words -- minimised to its pin, and dragged clear by its header, which
survives a reload and still follows the paragraph -- and signs out; the admin then deletes
it all and closes her account.  Skipped without node or Chrome.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "comments.mjs"
CHROME = os.environ.get("CHROME") or next((p for p in (
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium") if Path(p).exists()), None)
node = shutil.which("node")

pytestmark = [pytest.mark.skipif(node is None, reason="node is not on PATH"),
              pytest.mark.skipif(CHROME is None, reason="no Chrome; set CHROME")]

PARAS = [
    "The trap is a set of electrodes; the ions sit where the potential is lowest.",
    "A shuttle moves an ion along a segment by ramping the DAC waveforms of its electrodes.",
    "Two ions in one zone can be entangled with a Molmer-Sorensen gate; the gate takes longer when the chain is hot.",
    "Cooling brings the motional quanta down again, at the cost of time and of a second species in the zone.",
] * 6


def fixture_page(tmp_path: Path) -> Path:
    from qccd.site.build import PAGE, STYLE, with_nav
    body = "<h1>Fixture</h1>" + "".join(f'<p id="p{i + 1}">{t}</p>' for i, t in enumerate(PARAS))
    page = with_nav(PAGE.format(title="Fixture - QCCD studio", style=STYLE, extra_css="", body=body), 0, "physics", "[]")
    page = page.replace("__FOOTER__", "").replace("__SITEROOT__", "")
    out = tmp_path / "site"
    out.mkdir()
    (out / "fixture.html").write_text(page, encoding="utf-8")
    return out


def test_pin_a_comment_and_manage_it(tmp_path: Path):
    site = fixture_page(tmp_path)
    r = subprocess.run([node, str(RUNNER), str(site), "fixture.html", "--target", "#p3", "--python", sys.executable],
                       capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    lines = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    assert lines, r.stderr
    summary = lines[-1]
    steps = [l for l in lines if "step" in l]
    failed = [s for s in steps if not s["ok"]]
    assert summary.get("ok") and not failed and r.returncode == 0, json.dumps(failed, indent=1) + "\n" + r.stderr
    # the story's load-bearing steps ran, not just an early exit
    names = [s["step"] for s in steps]
    for want in ("admin: invited the reader, and the link came back to copy",
                 "signed out: the dialog offers no way to make an account",
                 "the link opened the invitation form for that address, and left the URL",
                 "the invitation made her account and signed her in",
                 "the link is spent once it has made an account",
                 "minimised from its header: the note leaves the page and the pin stays",
                 "dragged by its header: the note moved with the mouse",
                 "a moved note still belongs to its paragraph",
                 "the move survives a reload", "double-clicking the header puts it back",
                 "the pin stands on that spot", "scrolled: the pin moved with the text, not with the window",
                 "reloaded: signed in, the pin is back on the spot", "narrower window: the pin follows the reflowed paragraph",
                 "admin: deleted the thread; the API has none left",
                 "a closed account is refused at the door", "no exceptions or console errors"):
        assert want in names, names
