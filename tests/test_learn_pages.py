"""The Learn pages' shared furniture (qccd/site/learn.py): the glossary, the story strip, the
summary and recap boxes, the posters.

Static checks that need no site build: the marker marks what it should and leaves alone
what it must, every glossary word points at an anchor that the page builders really write,
and the physics markers render.  With `QCCD_SITE_DIR` set to a built site, the built pages
are checked too: every glossary link resolves to a file and an id, every page carries the
strip, the summary and the recap, and every poster names a page that exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

learn = pytest.importorskip("qccd.site.learn", reason="the site package is not in this tree")

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "qccd" / "site"
PAGES = [s[1] for s in learn.STORY]


# ----------------------------------------------------------------------------- the marker

def test_the_first_mention_in_each_section_is_marked_and_the_rest_left_alone():
    body = ('<h2 id="a">A</h2><p>A trap holds an ion; a trap is a trap.</p>'
            '<h3 id="b">B</h3><p>The trap again, and <code>trap</code> in code.</p>')
    out, used = learn.mark(body, "compilation/", 1)
    assert out.count('data-gl="trap"') == 2, out       # once per section, not once per page
    assert out.count('data-gl="ion"') == 1
    assert "<code>trap</code>" in out                  # code is never marked
    assert set(used) == {"trap", "ion"}


def test_nothing_is_marked_inside_links_headings_code_drawings_or_the_studio_cards():
    body = ('<h2 id="x">The trap</h2><a href="#">trap</a><pre>trap</pre><svg><text>trap</text></svg>'
            '<span class="h" data-t="t">trap</span><div class="eq">trap</div><p class="sub">trap</p>')
    out, used = learn.mark(body, "rules/", 1)
    assert out.count('class="gl"') == 1 and out.endswith('<p class="sub"><a class="gl" data-gl="trap" href="../machine/#def-node">trap</a></p>')


def test_a_word_defined_on_this_page_links_to_a_bare_fragment_and_others_to_the_page():
    out, _ = learn.mark("<p>a hop and a rule</p>", "compilation/", 1)
    assert 'href="#def-trap-graph">hop</a>' in out
    assert 'href="../rules/#language">rule</a>' in out
    out2, _ = learn.mark("<p>a hop</p>", "board/goal1/", 2)
    assert 'href="../../compilation/#def-trap-graph">hop</a>' in out2


def test_spellings_with_capitals_are_matched_exactly_and_plurals_are_taken():
    out, used = learn.mark("<p>two junctions; an ms is not the MS gate; R22 and r22</p>", "physics/", 1)
    assert ">junctions</a>" in out and "ms" in used and out.count('data-gl="ms"') == 1
    assert out.count('data-gl="R22"') == 1 and "r22</p>" in out


def test_a_mark_is_opaque_to_the_words_marked_after_it():
    """The first version searched the whole text node again after each mark, and the word
    "rule" then matched the "rules/" inside the href of the mark made just before it."""
    out, _ = learn.mark("<p>the rule checker, and the rules</p>", "compilation/", 1)
    assert out.count("<a ") == 2 and out.count("</a>") == 2
    assert 'href="../rules/#language">rule checker</a>' in out
    assert '<a class="gl" data-gl="rule" href="../rules/#language">rules</a>' in out
    assert 'href="../<a' not in out and '">rule checker' not in out.replace('href="../rules/#language">rule checker', "")


def test_the_card_data_carries_only_the_words_the_page_uses():
    blob = learn.card_data(["hop", "R7"], "compilation/", 1)
    assert blob.startswith('<script type="application/json" id="glossary">')
    assert '"hop"' in blob and '"R7"' in blob and '"nbar"' not in blob
    assert '"w":"on this page"' in blob and '"w":"on the Rules page"' in blob


# ---------------------------------------------------------------------------- the glossary

def test_every_glossary_word_is_spelled_once_and_points_into_the_story():
    seen: dict[str, str] = {}
    for key, g in learn.GLOSSARY.items():
        assert g["t"] and g["d"] and g["u"] and g["a"], key
        assert "<" not in g["d"], f"{key}: the explanation is plain text"
        assert any(g["u"].startswith(p) for p in PAGES + ["board/"]), f"{key}: {g['u']}"
        for a in g["a"]:
            assert a not in seen, f"{a!r} is claimed by both {seen[a]} and {key}"
            seen[a] = key


def _physics_anchors() -> set[str]:
    from qccd.site.md import slug
    text = (SITE / "physics.md").read_text(encoding="utf-8")
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*?)\s*$", text, re.M)}


def _ids_in(*files: Path) -> set[str]:
    ids: set[str] = set()
    for f in files:
        ids |= set(re.findall(r'id=\\?"([A-Za-z][\w-]*)\\?"', f.read_text(encoding="utf-8")))
    return ids


def test_every_glossary_anchor_is_one_the_page_builders_write():
    from qccd.site.algorithm import IDS
    from qccd.site.evaluation import HEADINGS as EVAL
    from qccd.site.examples import VERBS
    from qccd.site import machinepage
    from qccd.ruleset import catalogue
    known = {
        "physics/": _physics_anchors(),
        "machine/": set(machinepage.IDS.values()) | {a for a, _ in machinepage.HEADINGS}
                    | {f"m-{k}" for k, _, _, _ in machinepage.MACHINES},
        "compilation/": _ids_in(SITE / "algorithm.py", SITE / "build.py") | set(IDS.values()),
        "evaluation/": {a for a, _ in EVAL} | _ids_in(SITE / "evaluation.py"),
        "language/": {v["verb"] for v in VERBS} | _ids_in(SITE / "build.py"),
        "rules/": {r.id for r in catalogue().rules} | _ids_in(SITE / "rulespage.py"),
    }
    missing = []
    for key, g in learn.GLOSSARY.items():
        page, _, frag = g["u"].partition("#")
        if not frag:
            continue
        if frag not in known.get(page, set()):
            missing.append(f"{key}: {g['u']}")
    assert not missing, "glossary words whose definition anchor no builder writes:\n  " + "\n  ".join(missing)


# ------------------------------------------------------------------------- the physics markers

def test_the_physics_markers_render_as_the_furniture():
    from qccd.site.md import Renderer
    r = Renderer({}, lambda t: t)
    html = r.render("## A\n\n!!ask Why? || Because.\n\n!!sum this and that\n\n!!bridge and on\n\nA paragraph.\n")
    assert '<div class="ask"><p class="q">Why?</p><p class="a">Because.</p></div>' in html
    assert '<div class="sum sec"><b>In this section</b> this and that</div>' in html
    assert '<p class="bridge">and on</p>' in html
    assert "<p>A paragraph.</p>" in html
    assert "!!" not in html


def test_physics_md_opens_every_section_with_a_lead_in():
    text = (SITE / "physics.md").read_text(encoding="utf-8")
    heads = [m.start() for m in re.finditer(r"^##\s", text, re.M)]
    sums = [m.start() for m in re.finditer(r"^!!sum\s", text, re.M)]
    assert len(sums) >= len(heads) - 1, (len(heads), len(sums))


# ------------------------------------------------------------------------------ built pages

SITE_DIR = os.environ.get("QCCD_SITE_DIR")
built = pytest.mark.skipif(not SITE_DIR or not Path(SITE_DIR).exists(),
                           reason="set QCCD_SITE_DIR to a built site to check its pages")


@built
@pytest.mark.parametrize("page", PAGES)
def test_a_built_learn_page_carries_the_strip_the_summary_and_the_recap(page):
    html = (Path(SITE_DIR) / page / "index.html").read_text(encoding="utf-8")
    main = html[html.index("<main"):html.index("</main>")]
    assert main.count('<nav class="story"') == 1
    assert main.count('aria-current="page"') == 1
    first_h2 = main.find("<h2")
    assert 0 < main.find('<div class="sum">') < first_h2, "the summary comes before the first section"
    assert '<div class="recap">' in main and main.rfind('<div class="recap">') > main.rfind("<h2")
    assert 'id="glossary"' in main and "a.gl" not in main[:200]


@built
@pytest.mark.parametrize("page", PAGES)
def test_every_glossary_link_and_every_poster_of_a_built_page_resolves(page):
    here = Path(SITE_DIR) / page
    html = (here / "index.html").read_text(encoding="utf-8")
    links = re.findall(r'<a class="gl" data-gl="[^"]+" href="([^"]+)"', html)
    assert links, "no glossary marks on the page"
    bad = []
    for href in sorted(set(links)):
        path, _, frag = href.partition("#")
        target = (here / path / "index.html") if path.endswith("/") else (here / path if path else here / "index.html")
        if not target.exists():
            # a partial build (the Learn pages alone) has no board; a link into a section the
            # build did not write at all is not a broken link
            section = Path(SITE_DIR) / path.replace("../", "").split("/")[0]
            if path.startswith("../") and not section.exists():
                continue
            bad.append(f"{href}: no file {target}")
        elif frag and f'id="{frag}"' not in target.read_text(encoding="utf-8"):
            bad.append(f"{href}: no id {frag} in {target.name}")
    assert not bad, "\n".join(bad)
    posters = re.findall(r'<div class="runbox[^"]*" data-src="([^"]+)"', html)
    assert posters or page == "evaluation/", "no posters on the page"     # Evaluation has no animations
    for src in posters:
        assert (here / src).exists(), f"poster names a missing page {src}"
    assert "<iframe" not in html, "a Learn page no longer loads Studio pages until asked"
