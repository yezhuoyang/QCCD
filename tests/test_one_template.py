"""ONE DESIGN-PAGE TEMPLATE (the user, 2026-11-09).

Every page that shows a programme on a machine is `qccd.viz.render.render_html`, and nothing
else draws, rearranges or restyles one.  Until this date there were two layers: the template,
and an overlay the board scripts injected after rendering (the executing card on the stage, the
listings side by side, the study notes, ...).  Pages made without the overlay -- the RSA-2048
design pages, the gadget town, the official entries, the book -- kept the template's older
layout, with the program squeezed under a "now" box and, on the RSA pages, no circuit at all.
The overlay's features are the template's own now, and these tests keep it that way:

* every `render_html` call says which circuit stands behind its programme (`source=`), so a
  page cannot lose its Gates view by forgetting it;
* no file outside the template names the template's layout elements, except the add-ons
  listed below by name (the list may only shrink);
* the old layout and the overlay's names are gone from the template and the code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: where pages are made from; tests and docs only read pages
CODE = ["qccd", "Codesign/scripts", "goals", "tools", "Compiler/bridge"]
TEMPLATE = "qccd/viz/render.py"
#: the template's own scripts, inlined by it
OWN = (TEMPLATE, "qccd/viz/js/", "qccd/viz/engine.js")

#: the template's layout: the Program column and its listings, the stage card, the transport bar,
#: the rail's sections, the notes
LAYOUT_IDS = ("paneP pHw paneQ progcol stageNow snHw snQ pWin qWin pScroll qScroll pFoot qFoot pView "
              "pvhw pvgates pvboth pPin stagebar palBody palInspect notesPill").split()
#: names of the old layout and of the board overlay, which must not come back
GONE = ("pNow qNow bbtools bbrow bbnow bbnotes bbpill bbreopen bblab bbtip bbqlabels bbgrip bbhead "
        "BB_MARKS OVERLAY_CSS OVERLAY_JS HASH_JS").split()

#: ADD-ONS that still reach into the template's layout from outside, each with its reason.  The
#: test fails when a file not listed here does it, and when a listed file no longer does (delete
#: its line): fold an add-on into the template as a `render_html` parameter instead of growing this.
ADDONS = {
    "qccd/fullstack/web/stack.css": "the full stack's Stack view: a fourth view of the Program pane",
    "qccd/fullstack/web/stack.js": "the full stack's Stack view: a fourth view of the Program pane",
    "qccd/site/debugpage.py": "the site's Debug button, put in the transport bar",
    "qccd/workspace/compare_page.py": "the workspace's framed run view hides the Program column",
    "qccd/workspace/run_eval.py": "the workspace's live run view hides the rail's sections",
}


def _files(suffixes):
    for d in CODE:
        for f in sorted((ROOT / d).rglob("*")):
            if f.suffix in suffixes and "__pycache__" not in f.parts and f.is_file():
                yield f, f.relative_to(ROOT).as_posix()


def _word(names):
    return re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, names)) + r")(?![\w-])")


def test_every_render_html_call_says_which_circuit():
    """`source=` on every call: the circuit's source map, or `None` for a programme no circuit
    stands behind.  The RSA design pages were drawn without it and lost their Gates view."""
    missing = []
    for f, rel in _files({".py"}):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name == "render_html" and not any(k.arg == "source" for k in node.keywords):
                missing.append(f"{rel}:{node.lineno}")
    assert not missing, ("render_html called without saying source= (pass the circuit's "
                         "qccd.ir.source_map.build, or source=None when there is none): " + ", ".join(missing))


def test_nothing_outside_the_template_reaches_into_its_layout():
    pat = _word(LAYOUT_IDS)
    reach = {}
    for f, rel in _files({".py", ".js", ".mjs", ".css", ".html"}):
        if rel.startswith(OWN):
            continue
        hits = sorted({m.group(1) for m in pat.finditer(f.read_text(encoding="utf-8", errors="replace"))})
        if hits:
            reach[rel] = hits
    new = {rel: ids for rel, ids in reach.items() if rel not in ADDONS}
    assert not new, ("these files reach into the design-page template's layout from outside; make the "
                     f"feature a render_html parameter in {TEMPLATE} instead: {new}")
    stale = sorted(set(ADDONS) - set(reach))
    assert not stale, f"no longer reach into the layout: delete them from ADDONS: {stale}"


def test_the_old_layout_and_the_overlay_are_gone():
    pat = _word(GONE)
    back = {}
    for f, rel in _files({".py", ".js", ".mjs", ".css", ".html"}):
        hits = sorted({m.group(1) for m in pat.finditer(f.read_text(encoding="utf-8", errors="replace"))})
        if hits:
            back[rel] = hits
    assert not back, f"the old layout or the board overlay is back: {back}"


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    from qccd.arch import load
    from qccd.cost import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.verify import verify
    from qccd.viz.render import render_html
    arch = load(str(ROOT / "arch" / "chain.arch.json"))
    prog = TSIR(name="empty", arch_spec=arch.name)
    model = corrected_model()
    out = tmp_path_factory.mktemp("one_template") / "p.html"
    notes = {"title": "t", "index": "index.html", "facts": ["a fact"], "claim": "a claim",
             "marks": [{"frame": 0, "id": 0, "label": "the start", "note": "n", "kind": "note"}]}
    render_html(arch, prog, verify(prog, arch, model).result, model, out, source=None, notes=notes)
    return out.read_text(encoding="utf-8")


def test_the_template_carries_what_the_overlay_added(page):
    """The stage card, the q-labels box, the study notes as data, any speed, the deep links."""
    for needle in ('id="stageNow"', 'id="tQLabels"', '"notes":{"title":"t"', 'value="1.5"',
                   "function fromHash", "function embedMode", "function paintNotes"):
        assert needle in page, needle
    for gone in ('id="pNow"', 'id="qNow"', 'id="bbtools"'):
        assert gone not in page, gone


def test_the_deep_links_wait_for_the_editor():
    """#learn=B2 loads a lesson and #embed makes the page view-only, both through EDITOR, which
    the scripts AFTER the template's own define (editor.js, tutorial.js).  Called at the end of
    the template's script, the handler found no EDITOR: the site walk caught #learn=B2 opening no
    lesson (2026-11-09).  The one call must wait for DOMContentLoaded."""
    from qccd.viz.render import _TEMPLATE
    main = _TEMPLATE[:_TEMPLATE.index("__EDITOR__")]
    calls = [m.start() for m in re.finditer(r"(?<![\w.'])fromHash\(\)(?!\s*\{)", main)]
    assert len(calls) == 1, calls
    guard = main[calls[0] - 200:calls[0]]
    assert "addEventListener('DOMContentLoaded', fromHash)" in guard, guard
