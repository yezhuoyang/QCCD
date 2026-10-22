"""The course, proved: every lesson loads, its check fails before the work and passes after
the shipped solution, and what the solution leaves on the stage is something Python accepts.

A tutorial that celebrates a check the page did not run, or ships a solution that does not
pass its own exercise, is worse than no tutorial.  So the lessons are data
(`qccd/viz/js/tutorial.js`) and this file walks every one of them through the same harness
the editor tests use -- `tests/editor.mjs` over `tests/drive.mjs` -- with no shortcut.
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

OUT = ROOT / "out"
PAGE = OUT / "studio.html"
HARNESS = ROOT / "tests" / "editor.mjs"

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")


TUTORIAL_DIR = OUT / "tutorial"


def drive(script: list, tmp_path: Path, page: Path | None = None) -> dict:
    """Run the editor harness on the blank studio page (or `page`) with a lesson script."""
    page = page or PAGE
    if not page.exists():
        pytest.skip(f"run `python -m qccd studio -o {page}`")
    sp = tmp_path / "script.json"
    sp.write_text(json.dumps(script), encoding="utf-8")
    out = subprocess.run([node, str(HARNESS), str(page), str(sp)], capture_output=True,
                         text=True, timeout=600, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


def page_for(lesson: dict) -> Path | None:
    """The page a lesson is checked on: the blank studio page, or -- for a lesson that
    runs on a compiled page -- the companion page `qccd tutorial` builds.  None when
    that companion is not built (the lesson is then reported, never silently passed)."""
    if not lesson.get("page"):
        return PAGE
    p = TUTORIAL_DIR / f"{lesson['page']}.html"
    return p if p.exists() else None


def lessons(tmp_path: Path) -> list[dict]:
    r = drive([{"do": "lessons"}], tmp_path)
    ids = r["log"][0]["result"]
    assert ids, "the page registered no lessons"
    return ids


@requires_node
def test_the_tutorial_command_builds_the_studio_and_its_companion(tmp_path):
    """`qccd tutorial` writes the course page and the compiled page D1 steps through."""
    cli = subprocess.run([sys.executable, "-m", "qccd", "tutorial", "-o", str(TUTORIAL_DIR)],
                         capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    assert cli.returncode == 0, cli.stderr[-2000:]
    assert (TUTORIAL_DIR / "studio.html").exists()
    micro = TUTORIAL_DIR / "micro_grid9x9.html"
    if not micro.exists():
        pytest.skip("the compiled micro artifact is not built, so the companion page is not")
    # the companion carries the course too, and its Circuit pane is wired to the certificate
    r = drive([{"do": "lessons"}, {"do": "lesson", "id": "D1"}], tmp_path, micro)
    assert r["log"][0]["result"], "the companion page carries no course"
    assert r["log"][1]["result"]["ok"]


@requires_node
def test_the_course_registers_and_every_lesson_has_a_part(tmp_path):
    ls = lessons(tmp_path)
    parts = {l["part"] for l in ls}
    assert "A" in parts
    assert all(l["id"] and l["title"] for l in ls)
    # the blank page's snapshot carries the count, so a page that lost the course says so
    r = drive([], tmp_path)
    assert r["steps"][0]["lessons"] == len(ls)


@requires_node
def test_every_lesson_fails_before_and_passes_after_its_solution(tmp_path):
    """The two halves of an honest exercise.

    `lessonCheck` right after `lessonLoad` must NOT pass: an exercise the starting state
    already satisfies teaches nothing and would hand out a star for free.  After
    `lessonSolution` it must pass -- the solution is the tutorial's own claim that the
    exercise can be done, and this is where that claim is checked.
    """
    bad = []
    for l in lessons(tmp_path):
        page = page_for(l)
        if page is None:
            bad.append(f"{l['id']}: runs on a companion page that is not built; run "
                       f"`python -m qccd tutorial -o out/tutorial`")
            continue
        r = drive([{"do": "lesson", "id": l["id"]},
                   {"do": "lessonCheck"},
                   {"do": "lessonSolution"}], tmp_path, page)
        # `log` holds what each verb returned; `steps[k+1]` the snapshot taken after it
        load, first, solved = r["log"][0], r["log"][1], r["log"][2]
        state_after = r["steps"][3]["lesson"]
        if not (load["result"] or {}).get("ok"):
            bad.append(f"{l['id']}: setup refused: {load['result']}")
            continue
        if first["result"].get("ok"):
            bad.append(f"{l['id']}: the check passes on the starting state")
        if not (solved["result"] or {}).get("ok"):
            bad.append(f"{l['id']}: the solution does not pass: {solved['result']} / "
                       f"{(state_after.get('feedback') or {}).get('text')}")
        elif not state_after["passed"]:
            bad.append(f"{l['id']}: solution applied but the lesson is not marked passed")
    assert not bad, "\n".join(bad)


@requires_node
def test_a_lesson_s_device_loads_back_into_python(tmp_path):
    """What a lesson leaves on the stage is a real document: `qccd open` accepts it."""
    for l in lessons(tmp_path):
        page = page_for(l)
        if page is None:
            continue
        r = drive([{"do": "lesson", "id": l["id"]}, {"do": "lessonSolution"},
                   {"do": "snapshot"}], tmp_path, page)
        snap = r["log"][2]["snap"]
        path = tmp_path / f"{l['id']}.studio.json"
        path.write_text(json.dumps(snap), encoding="utf-8")
        cli = subprocess.run([sys.executable, "-m", "qccd", "open", str(path)],
                             capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        # a device with no programme prints its shape and exits 1 on the (absent) rules;
        # a refusal is a traceback or "could not" -- neither may appear
        assert "Traceback" not in cli.stderr, f"{l['id']}: {cli.stderr[-1500:]}"
        assert "nodes " in cli.stdout, f"{l['id']}: {cli.stdout[-800:]} {cli.stderr[-800:]}"


@requires_node
def test_hint_is_free_and_the_third_miss_starts_the_walkthrough(tmp_path):
    """Three misses in a row start the Hint walk-through on their own -- nobody should have
    to ask.  Starting it is what `lessonState().hints` counts, and it costs no star."""
    l = lessons(tmp_path)[0]
    r = drive([{"do": "lesson", "id": l["id"]},
               {"do": "lessonCheck"}, {"do": "lessonCheck"}, {"do": "lessonCheck"}], tmp_path)
    st = r["steps"][4]["lesson"]
    assert st["attempts"] == 3
    assert st["hints"] >= 1, "the third miss did not start the walk-through"
    assert st["stars"] == 0
    # and a nudge was said each time, never the same line twice in a row
    texts = [r["log"][i]["result"]["feedback"] for i in (1, 2, 3)]
    assert all(texts), texts
    assert texts[0] != texts[1] or texts[1] != texts[2], texts


@requires_node
def test_rule_lessons_break_what_they_say_they_break(tmp_path):
    """A rule lesson opens on a programme that breaks the rule it teaches -- live in the
    browser for the rules it checks, by Python's shipped verdict for the ones it cannot.

    Each stage may carry `breaks`: check specs that must hold the moment the stage is
    entered.  The walk solves one stage at a time (`lessonSolution` with `one`) so that
    every stage's own setup runs and is judged, not only the first.
    """
    bad, seen = [], 0
    for l in lessons(tmp_path):
        stages = l.get("stageBreaks") or [l.get("breaks")]
        if not any(stages):
            continue
        seen += 1
        script = [{"do": "lesson", "id": l["id"]}]
        for k, br in enumerate(stages):
            for spec in (br or []):
                script.append({"do": "check", "spec": spec})
            if k < len(stages) - 1:
                script.append({"do": "lessonSolution", "one": True})
        r = drive(script, tmp_path)
        for k, step in enumerate(script):
            res = r["log"][k]["result"]
            if step["do"] == "check" and not res.get("ok"):
                bad.append(f"{l['id']}: {step['spec']} does not hold: {res}")
            if step["do"] == "lessonSolution" and not res.get("ok"):
                bad.append(f"{l['id']}: a stage's solution failed: {res}")
    assert seen, "no lesson declares what it breaks -- Part R is missing"
    assert not bad, "\n".join(bad)


@requires_node
def test_shipped_verdicts_match_a_fresh_python_run(tmp_path):
    """The verdicts the page carries for the rules it cannot judge are recomputed here
    from `qccd.viz.course` under the page's own cost model, and must agree."""
    from qccd.cost.models import corrected_model, deck_model
    from qccd.viz import course

    blob = drive([{"do": "blob"}], tmp_path)["log"][0]["result"]
    assert blob["verdicts"], "the page shipped no tutorial verdicts"
    assert set(blob["cases"]) == set(course.CASES)
    name = (blob["model"] or {}).get("name")
    model = deck_model() if name == "deck" else corrected_model(blob["table"] or "qccdsim_jones")
    fresh = course.verdicts(model)
    for cid, pair in fresh.items():
        for which in ("loaded", "fixed"):
            want, got = pair[which], blob["verdicts"][cid][which]
            if want is None:
                assert got is None
                continue
            assert got["focus"]["rule"] == want["focus"]["rule"], cid
            assert got["focus"]["state"] == want["focus"]["state"], (cid, which)
            assert [m["message"] for m in got["focus"]["messages"]] == \
                   [m["message"] for m in want["focus"]["messages"]], (cid, which)
            assert sorted(got["rules"]["failed"]) == sorted(want["rules"]["failed"]), (cid, which)
            for k, v in want["metrics"].items():
                assert got["metrics"][k] == pytest.approx(v), (cid, which, k)
    # and every focus rule is one the browser does not check itself
    for cid, pair in fresh.items():
        assert pair["loaded"]["focus"]["rule"] in {"R4d", "R7b", "R9", "R10", "R16"}, cid


# ---------------------------------------------------------------------------------------
# THE COURSE SAYS WHAT THE PAGE SHOWS.  On 2026-09-16 a reader could not do lesson A2's
# second step: it said "open the **Architecture** panel", and the tab strip had been
# renamed to Device -- the hover card, the key help and the pane's own heading still said
# Architecture.  The step also asked to change a `capacity=8` in Source that is not there
# (the load zone comes from the template).  Every lesson test above applies the SOLUTION
# through the API, so none of them could see either.  The tests below read what a reader
# reads (the course's own strings, the hover cards, the pane headings) and do what a
# reader does.

TUTORIAL_JS = ROOT / "qccd" / "viz" / "js" / "tutorial.js"
EDITOR_JS = ROOT / "qccd" / "viz" / "js" / "editor.js"
RENDER_PY = ROOT / "qccd" / "viz" / "render.py"

#: panes whose heading is a phrase about the pane rather than its name
PANE_HEADINGS = {"W": "Write a programme", "R": "Evaluation"}


def _tab_labels() -> dict[str, str]:
    import re
    src = RENDER_PY.read_text(encoding="utf-8")
    labels = dict(re.findall(r'<button class="tab[^"]*" id="tab([A-Z])"[^>]*>([^<]+)</button>', src))
    assert {"L", "P", "A", "M", "W", "R"} <= set(labels), labels
    return labels


def _course() -> dict:
    """The course as the page reads it: `tutorial.js` evaluated by node, as data."""
    js = ("const fs = require('fs');"
          "const T = new Function(fs.readFileSync(process.argv[1], 'utf8') + ';return QCCD_TUTORIAL;')();"
          "process.stdout.write(JSON.stringify(T));")
    out = subprocess.run([node, "-e", js, str(TUTORIAL_JS)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def _reader_strings(course: dict) -> list[tuple[str, str]]:
    """Every sentence a reader is shown, with where it lives: the story and paragraphs, the
    exercises (stage, boundary, challenge), the choices, the nudges and the praise, the
    milestones, and the sentences the Hint walk-through says (`guide` steps)."""
    out = []
    for p in course["parts"]:
        out.append((f"part {p['id']} milestone", p.get("milestone") or ""))

    def guide_says(where, spec):
        for step in spec or []:
            for x in step[1:]:
                # a step's sentence; selectors, ids and typed code carry no full stop at the end
                if isinstance(x, str) and " " in x and x.rstrip().endswith("."):
                    out.append((where + " guide", x))

    for L in course["lessons"]:
        lid = L["id"]
        out.append((f"{lid} story", L.get("story") or ""))
        out += [(f"{lid} text", t) for t in L.get("text") or []]
        if L.get("exercise"):
            out.append((f"{lid} exercise", L["exercise"]))
        guide_says(lid, L.get("guide"))
        for k, s in enumerate(L.get("stages") or []):
            if s.get("exercise"):
                out.append((f"{lid} step {k + 1}", s["exercise"]))
            guide_says(f"{lid} step {k + 1}", s.get("guide"))
        for extra in ("boundary", "challenge"):
            if L.get(extra):
                out.append((f"{lid} {extra}", L[extra].get("exercise") or ""))
                if L[extra].get("praise"):
                    out.append((f"{lid} {extra} praise", L[extra]["praise"]))
        out += [(f"{lid} choice {c['id']}", c["text"]) for c in L.get("choices") or []]
        for pred, pool in (L.get("nudges") or {}).items():
            out += [(f"{lid} nudge {pred}", t) for t in pool]
        if L.get("praise"):
            out.append((f"{lid} praise", L["praise"]))
    return out


@requires_node
def test_lessons_name_the_tabs_the_page_shows():
    """Every name the course gives a pane -- in its text, its exercises, its nudges and
    praise, and the sentences its Hint walk-through says -- is the label on the tab that
    opens it (or a toolbar popover's own name)."""
    import re
    labels = _tab_labels()
    render = RENDER_PY.read_text(encoding="utf-8")
    pops = set(re.findall(r"'([A-Z][A-Za-z ]+)'", re.search(r"const POP_NAMES = \{([^}]*)\}", render).group(1)))
    known = set(labels.values()) | pops
    strings = _reader_strings(_course())
    assert len(strings) > 300, f"only {len(strings)} reader strings found; the course scan is broken"
    bad = []
    for where, text in strings:
        for term, key in re.findall(r"\[\[([^\]|]+)\|tab:([A-Z])\]\]", text):
            if labels.get(key) != term:
                bad.append(f"{where}: [[{term}|tab:{key}]] but that tab reads {labels.get(key)!r}")
        # "**Device** tab", "the Program panel", "[[Write|tab:W]] panel" (the term's own check is above)
        for name, what in re.findall(r"(?:\*\*|\b)([A-Z][a-z]+)(?:\*\*)? (panel|pane|tab)\b", text):
            if name not in known:
                bad.append(f"{where}: '{name} {what}' -- no tab or toolbar popover is called {name!r}")
    assert not bad, "\n".join(bad)


@requires_node
def test_no_lesson_ships_text_nobody_is_shown():
    """Hint is the walk-through now; the paragraph `hints` a lesson used to carry are shown
    nowhere, so none may ship, and no sentence may send the reader to one ("the hint knows
    which corner is missing").  The button is always written Hint."""
    import re
    course = _course()
    stale = [L["id"] for L in course["lessons"] if "hints" in L]
    assert not stale, f"lessons carry paragraph hints nothing shows: {stale}"
    bad = [f"{where}: {text}" for where, text in _reader_strings(course)
           if re.search(r"\b(the|a|first|second|third|next) hints?\b", text)]
    assert not bad, "\n".join(bad)


def test_hover_cards_key_help_and_pane_headings_name_the_tabs_the_page_shows():
    """The hover cards, the key help and the pane headings name each pane by the label on
    the tab that opens it."""
    import re
    labels = _tab_labels()
    render = RENDER_PY.read_text(encoding="utf-8")
    bad = []
    editor = EDITOR_JS.read_text(encoding="utf-8")
    for key, title in re.findall(r"'tab:([A-Z])': \{ t: '([^']+)'", editor):
        if key in labels and labels[key] != title:
            bad.append(f"hover card tab:{key} is titled {title!r}; the tab reads {labels[key]!r}")
    for key, doc in re.findall(r"verb: 'pane-([A-Z])'[^}]*doc: '([^']+) pane'", editor):
        if labels.get(key) != doc:
            bad.append(f"key help calls pane-{key} the {doc!r} pane; the tab reads {labels.get(key)!r}")
    for key, heading in re.findall(r'id="pane([A-Z])">\s*<header class="ph"><h3>([^<]+)</h3>', render):
        want = PANE_HEADINGS.get(key, labels.get(key))
        if heading != want:
            bad.append(f"pane{key} is headed {heading!r}; its tab reads {labels.get(key)!r}")
    assert not bad, "\n".join(bad)


@requires_node
def test_a2_the_zone_capacity_step_works_the_way_the_lesson_says(tmp_path):
    """Lesson A2, step 2, done as the text says: Source has no line for the load zone,
    so the reader adds `m.set_zone("load", capacity=4)` as a new last line.  The check
    passes, and the zone keeps everything else it allowed."""
    base = [{"do": "lesson", "id": "A2"}, {"do": "lessonSolution", "one": True}, {"do": "lessonCheck"}]
    r = drive(base, tmp_path)
    at = r["log"][2]
    assert at["lesson"]["stage"] == 1 and not at["result"]["ok"], at
    zones = json.loads(r["arch_json"]) if isinstance(r["arch_json"], str) else r["arch_json"]
    before = dict(zones["zone_types"]["load"])
    assert before["capacity"] == 8, before
    src = r["source"]
    # the lesson's reason for ADDING a line rather than editing one
    assert 'set_zone("load"' not in src, "Source now declares the load zone: reword lesson A2's step 2"

    typed = src + ("" if src.endswith("\n") else "\n") + 'm.set_zone("load", capacity=4)\n'
    r = drive(base + [{"do": "source", "src": typed}, {"do": "lessonCheck"}], tmp_path)
    applied, checked = r["log"][3]["result"], r["log"][4]
    assert applied["ok"], applied
    assert checked["result"]["ok"] and checked["lesson"]["passed"], checked
    zones = json.loads(r["arch_json"]) if isinstance(r["arch_json"], str) else r["arch_json"]
    after = zones["zone_types"]["load"]
    assert after == dict(before, capacity=4), (before, after)
    # and the device the reader built is still there: applying the text used to empty it,
    # and this step's check, which reads only the zone, passed anyway
    geometry = zones.get("geometry") or {}
    assert len(geometry.get("nodes") or []) == 6 and len(geometry.get("segments") or []) == 4, geometry
