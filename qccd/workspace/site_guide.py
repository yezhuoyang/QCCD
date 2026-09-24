"""What the website is and how to work it: the agent's map of qccd.academy and its Studio.

`qccd_read_reference(section='site')` -- the pages and the course, read from the LIVE site's
own search index (every page carries it, `nav.html` INDEX), so the map is the deployed site's,
not a copy that drifts; plus how to do the common things visibly, as a person would.
`section='site:studio'` -- every control, panel, metric and program verb of the Studio, in the
Studio's own words: the explain layer's HINTS table (`qccd/viz/js/editor.js`), the same
sentences the hover cards, the Explain captions and the guide show the person.
`query` searches both.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

__all__ = ["site_guide", "studio_hints", "HOW_TO"]

_EDITOR_JS = Path(__file__).resolve().parents[1] / "viz" / "js" / "editor.js"
_cache: dict = {}
_lock = threading.Lock()

#: how to do the common things on the site so the person sees them happen
HOW_TO = [
    {"task": "find a page", "how": "navigate to its url (below) or search: qccd_page_act navigate path='/web/<url>'. "
                                   "Every page carries the same search index (this list)."},
    {"task": "walk someone through a lesson",
     "how": "navigate to /web/studio.html, then open_lesson lesson='A1'. qccd_page_read shows the lesson "
            "(app.lesson, and the Learn panel's text). Do the exercise the way the lesson asks -- click the "
            "tiles and the canvas, or studio verbs on the page's own Studio -- with wait between steps and a "
            "line in the chat for each; then click the Check button (hint learn:check) and read the verdict "
            "(studio verb lessonState). studio verbs lessonHint and lessonSolution are the course's own hints "
            "and solution."},
    {"task": "show an animation", "how": "step with play=true (or step=<n>, delta=<k>) on a studio page or an "
                                        "embedded example ({frame: 'fN'}); wait while it plays; pause."},
    {"task": "test a program on the person's design (their workspace Studio)",
     "how": "qccd_run_program: their Studio shows the program while it compiles, then their tab opens the "
            "run's page -- press Play there (step play=true), wait, then report from the run's result."},
    {"task": "try a program on a website Studio page (no workspace)",
     "how": "open the Write panel (click the control with hint tab:W), fill its text field with the program "
            "(p.init(...), p.shuttle(...) -- section site:studio lists the verbs), click Evaluate (hint "
            "evaluate), then play."},
    {"task": "change the person's design", "how": "qccd_apply_change_set (preview, then apply) in small steps; each "
                                                  "commit appears in their Studio with your cursor. Page studio verbs "
                                                  "only READ their workspace Studio."},
    {"task": "check a page for scientific mistakes",
     "how": "read it by section; compare every claim, number and example with the rules "
            "(qccd_read_reference section=rules), the physics (docs:phys) and the page's own embedded examples "
            "(step them; read their transport and verdicts). Highlight each doubtful passage with a note as "
            "you go; comment only when the person asked for comments."},
    {"task": "leave comments as the person", "how": "comments (read the threads first, do not duplicate one), then "
                                                    "comment target=<the element> text=<one point, with the "
                                                    "evidence>; reply / resolve on threads. They must be signed in; "
                                                    "each comment is signed 'via <you>'."},
]


def _index(mirror) -> list:
    """The live site's search index: [{t, d, u, k}], k in part|page|lesson|doc|rule|task|entry|..."""
    with _lock:
        hit = _cache.get("index")
        if hit and time.time() - hit[0] < 600:
            return hit[1]
    page = mirror.get("index.html")
    text = page.body.decode("utf-8", errors="replace") if page.status == 200 else ""
    i = text.find("INDEX = ")
    if i < 0:
        raise ValueError("the site's pages carry no search index")
    idx, _ = json.JSONDecoder().raw_decode(text, i + len("INDEX = "))
    with _lock:
        _cache["index"] = (time.time(), idx)
    return idx


def _unjs(s: str) -> str:
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


_HINT = re.compile(r"^\s*'((?:[^'\\]|\\.)+)'\s*:\s*\{\s*t:\s*'((?:[^'\\]|\\.)*)'\s*,\s*d:\s*'((?:[^'\\]|\\.)*)'"
                   r"(?:\s*,\s*k:\s*'((?:[^'\\]|\\.)*)')?\s*\}", re.M)
_GROUPS = (("screen", ("region:", "tools:", "search")),
           ("panels", ("tab:", "menu", "learn:", "follow", "filter", "evaluate", "archview", "progview", "progpin", "qec:")),
           ("parts of a device", ("el:", "zone:", "explode", "ctxmenu", "menu:")),
           ("program verbs (the Write panel)", ("p:",)),
           ("numbers in the head", ("m:", "c:")),
           ("legend", ("leg:",)))


def studio_hints() -> list:
    """Every entry of the Studio's HINTS table: [{hint, name, what, keys, group}]."""
    with _lock:
        hit = _cache.get("hints")
        if hit:
            return hit
    src = _EDITOR_JS.read_text(encoding="utf-8")
    a = src.find("var HINTS = {")
    b = src.find("\n};", a)
    out = []
    for m in _HINT.finditer(src[a:b]):
        key = _unjs(m.group(1))
        group = next((g for g, pre in _GROUPS if any(key.startswith(p) for p in pre)), "toolbar and canvas")
        e = {"hint": key, "name": _unjs(m.group(2)), "what": _unjs(m.group(3)), "group": group}
        if m.group(4):
            e["keys"] = _unjs(m.group(4))
        out.append(e)
    with _lock:
        _cache["hints"] = out
    return out


def site_guide(mirror, section: str = "site", query: str | None = None) -> dict:
    hints = studio_hints()
    note_hints = ("In a page read, a Studio control carries hint=<key> and what=<this sentence>; target it with "
                  "selector '[data-hint=\"<key>\"]'.")
    if section == "site:studio":
        items = hints
        if query:
            ql = query.lower()
            items = [h for h in hints if ql in (h["hint"] + " " + h["name"] + " " + h["what"]).lower()]
        return {"section": section, "about": "the Studio, control by control, in its own words", "note": note_hints,
                "controls": items}
    try:
        idx = _index(mirror)
        err = None
    except Exception as exc:                      # offline: the Studio map still helps
        idx, err = [], f"the site's page index could not be read ({exc}); navigate and read pages directly"
    web = lambda u: "/web/" + u.lstrip("/")
    if query:
        ql = query.lower()
        pages = [{"title": e["t"], "about": e.get("d"), "url": web(e["u"]), "kind": e.get("k")}
                 for e in idx if ql in (e["t"] + " " + (e.get("d") or "")).lower()][:60]
        return {"section": section, "query": query, "pages": pages,
                "studio_controls": [h for h in hints if ql in (h["hint"] + " " + h["name"] + " " + h["what"]).lower()][:30],
                "error": err}
    kinds: dict = {}
    for e in idx:
        kinds[e.get("k")] = kinds.get(e.get("k"), 0) + 1
    main = [{"title": e["t"], "about": e.get("d"), "url": web(e["u"])} for e in idx
            if e.get("k") == "part" or (e.get("k") == "page" and "#" not in e["u"])]
    # pages the index knows only by their entries (rules/#R7, language/#init, docs/adl/#...)
    roots: dict = {}
    for e in idx:
        u = e["u"]
        if "#" not in u or e.get("k") in ("part", "page", "lesson"):
            continue
        root = u.split("#")[0]
        if root and root not in roots and not any(m["url"] == web(root) for m in main):
            roots[root] = {"title": (e.get("d") or "").split(" · ")[0] if e.get("k") == "doc" else root.strip("/"),
                           "about": f"{sum(1 for x in idx if x['u'].startswith(root + '#'))} {e.get('k')} entries, "
                                    f"e.g. {e['t'][:60]}", "url": web(root)}
    main += list(roots.values())
    return {
        "section": section,
        "about": ("qccd.academy: a course and a design studio for trapped-ion QCCD machines, the rules a design "
                  "must obey, the compiler, the physics, and leaderboards of designs. Through the workspace the "
                  "person sees every page at /web/<path> with you beside it; their own design is /studio."),
        "main_pages": main,
        "lessons": [{"id": e["t"].split(" ")[1] if e["t"].startswith("Lesson ") else e["t"], "title": e["t"],
                     "url": web(e["u"])} for e in idx if e.get("k") == "lesson"],
        "tasks": [{"title": e["t"], "about": e.get("d"), "url": web(e["u"])} for e in idx if e.get("k") == "task"],
        "also_indexed": {k: n for k, n in kinds.items() if k not in ("part", "page", "lesson", "task")},
        "how_to": HOW_TO,
        "studio": {"note": note_hints, "controls": len(hints),
                   "read": "qccd_read_reference section='site:studio' (optionally query=...) for every control"},
        "search": "query=<words> searches the pages (rules, docs sections, syntax, papers, people, entries) and the "
                  "Studio's controls",
        "error": err,
    }
