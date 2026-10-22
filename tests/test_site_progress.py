"""THE COURSE ON THE LEARN PAGE, AND THE READER'S PROGRESS (`qccd/site/progress.py`).

The Learn page shows one card per lesson, each on its own line with a diagram of what the
lesson is about, and a panel saying how far the reader has got.  A signed-in reader's stars
are kept on their account by the accounts API (`/api/progress` in `comments_api.py`), so the
progress follows the account rather than the browser.

The fast tests read the course out of `tutorial.js` and check that every lesson has its own
diagram, that every diagram is well-formed SVG, and that every card is written.  The browser
story (`tests/progress.mjs`, real API on a temporary database, headless Chrome) checks the
layout -- never two lessons on one line -- and the sync: what a browser earned before signing
in reaches the account, the account's stars come down into a fresh browser, a stale browser
takes nothing away, lessons finished elsewhere are marked new once, a star earned in the
studio reaches the account, another reader on the same browser does not inherit the last
one's stars, and nothing is sent when nobody is signed in.  Skipped without node or Chrome.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip("qccd.site.progress", reason="the site package is not in this tree")

from qccd.site import progress  # noqa: E402
from qccd.site.build import PAGE, PART_ART, PART_COLOR, STYLE, lessons, with_nav  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "progress.mjs"
CHROME = os.environ.get("CHROME") or next((p for p in (
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium") if Path(p).exists()), None)
node = shutil.which("node")

parts, less = lessons()
needs_course = pytest.mark.skipif(not less, reason="the course (tutorial.js) is not in this tree")


@needs_course
def test_every_lesson_has_its_own_diagram_and_it_is_well_formed():
    ids = [L["id"] for L in less]
    missing = [i for i in ids if i not in progress.DIAGRAMS]
    assert not missing, f"lessons without a diagram (add one to progress._diagrams): {missing}"
    for i in ids:
        svg = progress.diagram(i, i[0])
        ET.fromstring(svg)                                  # well-formed, or this raises
        # its markers are its own, so two diagrams on one page never share an id
        assert all(m.startswith(f"{i}-") for m in re.findall(r'\bid="([^"]+)"', svg)), i
        assert "url(#@-" not in svg, i


@needs_course
def test_the_course_is_read_out_of_tutorial_js():
    meta = progress.lesson_meta(progress.TUTORIAL_JS.read_text(encoding="utf-8"))
    assert [L["id"] for L in less] == list(meta)
    for i, m in meta.items():
        assert m["story"], f"{i} has no first line"
        assert 1 <= m["max"] <= 3, i
    # a boundary and a challenge are stars of their own; a nested `challenge:` (a solution's) is not
    src = progress.TUTORIAL_JS.read_text(encoding="utf-8")
    for i, m in meta.items():
        block = src[src.index(f"{{ id: '{i}', part: "):]
        nxt = re.search(r"\n    \{ id: '[A-Z]\d+', part: ", block[10:])
        block = block[:nxt.start() + 10] if nxt else block
        assert m["max"] == 1 + len(re.findall(r"\n {6}(?:boundary|challenge): \{", block)), i


@needs_course
def test_the_section_writes_one_card_per_lesson_and_declares_its_control():
    html = progress.course_section(parts, less, PART_ART, PART_COLOR)
    cards = re.findall(r'<a class="lesson" id="lesson-([A-Z]\d+)"', html)
    assert cards == [L["id"] for L in less]
    assert len(re.findall(r'<section class="part" id="part-', html)) == len(parts)
    # the one button on the page is declared for agents (docs/agent-interface.md)
    assert 'data-hint="learn:signin"' in html and "'learn:signin'" not in html and '"learn:signin"' in html
    assert "window.QCCDPROGRESS" in html                      # the sync rides along


@needs_course
def test_the_lean_parts_are_the_tutorial_and_nothing_a_reader_reads_calls_it_a_book():
    """The verified compiler's chapters are Parts E-I of the one tutorial: cards like A-D's, in
    the same panel and count -- not a separate "book" beside it (the user, 2026-10-20)."""
    import html as h
    import re
    lean = progress.lean_parts()
    if not lean:
        pytest.skip("this tree has no Lean parts")
    page = progress.course_section(parts, less, PART_ART, PART_COLOR, lean=lean)
    live = [L for p in lean for L in p["lessons"] if L.get("live", True)]
    # the Lean parts follow the studio's, in order from E (R is the studio's rules part)
    letters = [p["part"] for p in lean]
    assert letters == [chr(ord("E") + i) for i in range(len(letters))] and "R" not in letters, letters
    assert all(p["part"] in progress.LEAN_BADGES and p["part"] in progress.LEAN_ART for p in lean), letters
    for p in lean:
        assert page.count(f'id="part-{p["part"]}"') == 1, p["part"]
    # the studio's own parts are not the Lean parts (tutorial.js links on to E-J in `continues`)
    assert not {q["id"] for q in parts} & set(letters), [q["id"] for q in parts]
    assert re.findall(r'<a class="lesson chap" id="lesson-([A-Z]\d+)"', page) == [L["id"] for L in live]
    # one count: the studio's lessons and every live chapter's exercises
    total = len(less) + sum(len(L["exercises"]) for L in live)
    assert f'<b id="you-done">0</b> of {total} exercises' in page
    # what a reader reads: the text outside tags, scripts and styles
    text = h.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)))
    assert "book" not in text.lower(), re.search(r".{40}book.{40}", text, re.I | re.S).group(0)
    assert " -- " not in text
    # a lesson's line is prose, not its source: no markdown marks left in it, and no path into
    # the repository (G2's first sentence named check_cert.py, 2026-10-20)
    from qccd.site.prose import PATH
    assert PATH.search("compose (check_cert.py calls them NATIVE)") and not progress.reader_line(
        "the compiler emits nothing else (check_cert.py calls them NATIVE: the pulses):").count("check_cert")
    for line in re.findall(r'<span class="ls">(.*?)</span>', page):
        assert "`" not in line and "**" not in line, line
        assert not PATH.search(h.unescape(line)), line


def test_every_studio_page_sends_its_stars_and_no_other_page_does():
    """A lesson can be done on any page that carries the course -- the studio, a leaderboard
    entry, a gadget's studio -- so `with_nav` gives each of them the sync, once; a document page,
    or an app page without the course (a gadget viewer), gets none."""
    shell = '<!doctype html><html><head><title>t</title></head><body><main>{}</main></body></html>'
    course = shell.format('<script>var QCCD_TUTORIAL = { lessons: [] };</script>')
    marker = "window.QCCDPROGRESS = {"
    assert with_nav(course, 2, None, "[]", app=True, extra="<script>/*hash*/</script>").count(marker) == 1
    assert with_nav(shell.format("<p>viewer</p>"), 2, None, "[]", app=True).count(marker) == 0
    assert with_nav(shell.format("<p>words</p>"), 1, "learn", "[]").count(marker) == 0
    # it comes last, after the page's own extras, just before </body>
    page = with_nav(course, 0, None, "[]", app=True, extra="<script>/*hash*/</script>")
    assert page.index("/*hash*/") < page.index(marker) < page.rindex("</body>")


def fixture_site(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    (out / "learn").mkdir(parents=True)
    body = "<h1>Learn</h1><h2>The course</h2>" + progress.course_section(parts, less, PART_ART, PART_COLOR)
    page = with_nav(PAGE.format(title="Learn - QCCD studio", style=STYLE, extra_css=progress.LEARN_CSS, body=body),
                    1, "learn", "[]")
    (out / "learn" / "index.html").write_text(page.replace("__FOOTER__", "").replace("__SITEROOT__", "../"), encoding="utf-8")
    # the studio, as far as the sync can tell: the course's global and an EDITOR that counts
    # how often it is asked to read the stars again
    studio = ('<!doctype html><html><head><meta charset="utf-8"><title>studio</title></head><body><main><p>studio</p></main>'
              '<script>var QCCD_TUTORIAL = { lessons: [] };'
              'window.EDITOR = { lessonsReady: function(){ window.__refreshed = (window.__refreshed || 0) + 1; return 0; } };</script>'
              '</body></html>')
    page = with_nav(studio, 0, None, "[]", app=True)         # the course is on it, so the sync is added
    assert page.count("window.QCCDPROGRESS = {") == 1
    (out / "studio.html").write_text(page.replace("__FOOTER__", "").replace("__SITEROOT__", ""), encoding="utf-8")
    # the book keeps its own exercises on the same account (qccd/site/book/book-progress.js);
    # a book page opened between two readers must not vouch for the course's stars
    book_js = ROOT / "qccd" / "site" / "book" / "book-progress.js"
    if book_js.exists():
        # the Learn page loads it by URL for the Lean parts' exercises
        (out / "book").mkdir(exist_ok=True)
        shutil.copyfile(book_js, out / "book" / "book-progress.js")
        book = ('<!doctype html><html><head><meta charset="utf-8"><title>book</title></head><body><main><p>a book page</p></main>'
                "<script>" + book_js.read_text(encoding="utf-8") + "</script></body></html>")
        page = with_nav(book, 0, None, "[]")
        (out / "book.html").write_text(page.replace("__FOOTER__", "").replace("__SITEROOT__", ""), encoding="utf-8")
    return out


@needs_course
@pytest.mark.skipif(node is None, reason="node is not on PATH")
@pytest.mark.skipif(CHROME is None, reason="no Chrome; set CHROME")
def test_the_learn_page_and_the_account_in_a_real_browser(tmp_path: Path):
    site = fixture_site(tmp_path)
    r = subprocess.run([node, str(RUNNER), str(site), "--python", sys.executable],
                       capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    lines = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    assert lines, r.stderr
    steps = [l for l in lines if "step" in l]
    failed = [s for s in steps if not s["ok"]]
    assert lines[-1].get("ok") and not failed and r.returncode == 0, json.dumps(failed, indent=1) + "\n" + r.stderr
    names = [s["step"] for s in steps]
    for want in ("one card per lesson, each on its own line",
                 "signing in from the Learn page saves what this browser earned to the account",
                 "a browser with nothing gets the account's stars",
                 "a stale browser takes nothing away and is brought up to date",
                 "a star earned in the studio reaches the account within a few seconds",
                 "another reader on this browser does not inherit the last reader's stars",
                 "signed out: nothing is sent, and the page shows this browser"):
        assert any(n.startswith(want) for n in names), f"the story never reached: {want}"
