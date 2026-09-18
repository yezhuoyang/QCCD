"""Every second and third star in the course must be reachable.

A lesson can carry a `boundary` and a `challenge` beyond its stages -- twelve of them across
the thirty-one lessons -- and until 2026-09-18 **not one had ever been executed by a test.**

The reason is structural rather than an oversight.  `test_tutorial.py` proves a lesson by
calling `lessonSolution`, and `lessonSolution` sets `LSTATE.shown`; the branch in
`editor.js` that evaluates a boundary or a challenge requires `!shown` (a star says "you did
it", not "you were shown").  So the route the existing test takes can never reach one, and
no amount of running it would have.

What that cost: R22 landed on 2026-09-17 and made B2's challenge impossible -- it asked two
ions to approach each other in a single `p.simd`, which is one waveform pushing two ways --
and nothing went red.  A reader working through the course found it, followed the nudge that
recommended the very instruction R22 refuses, and wrote in.  Zhuangzhuang Chen, 2026-09-18.

So this solves each lesson the way a reader does: apply the stage answers directly, leaving
`shown` false, earn the first star honestly, then apply the extra's answer and check.  The
answers live HERE rather than in `tutorial.js` because they are the test's claim that the
exercise can be done, and because the course is data the site publishes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NODE = shutil.which("node")
TUTORIAL_JS = ROOT / "qccd" / "viz" / "js" / "tutorial.js"
DRIVER = Path(__file__).parent / "course_extras.mjs"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

#: A single-quoted JavaScript string, escapes intact.
QUOTED = r"'((?:[^'\\]|\\.)*)'"

#: One entry per extra: the stage answers that earn the first star, then the extra's own.
#: `stages` is indexed by stage number.  A lesson whose stages are device GESTURES rather
#: than a programme is named in `NOT_PROGRAMMES` below instead, with the reason.
ANSWERS: dict[tuple[str, str], dict] = {
    ("B1", "boundary"): {
        "stages": ['p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\n'],
        "answer": 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2", "S1", "S0"])\n',
        "why": "a round trip: visit S2 and come back to S0",
    },
    ("B2", "challenge"): {
        "stages": ['p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\n'
                   'p.gate("CX", [["d0", "d1"]])\n',
                   'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\n'
                   'p.cool()\np.gate("CX", [["d0", "d1"]])\n'],
        "answer": 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d0", ["S0", "S1"])\n'
                  'p.shuttle("d1", ["S2", "S1"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n',
        "why": "meet at S1 -- TWO shuttles, because one waveform cannot push two ways (R22)",
    },
    ("B3", "boundary"): {
        "stages": ['p.init({"d0": "S0", "d1": "S2"})\np.gate("H", [], ["S0"])\n'
                   'p.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\n'
                   'p.gate("CX", [["d0", "d1"]])\np.measure(["d0", "d1"])\n'],
        "answer": 'p.init({"d0": "S0", "d1": "S2"})\np.gate("H", [], ["S0"])\n'
                  'p.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\n'
                  'p.gate("CX", [["d0", "d1"]])\np.measure(["d0", "d1"])\n'
                  'p.reset(["d0", "d1"])\np.gate("H", [], ["S0"])\np.cool()\n'
                  'p.gate("CX", [["d0", "d1"]])\np.measure(["d0", "d1"])\n',
        "why": "two full rounds with a reset between them",
    },
    ("B5", "challenge"): {
        "stages": ["p.fill()\np.rotate(2)\n"],
        "answer": "p.fill()\np.rotate(-2)\n",
        "why": "four forward is two back: the shorter way round is the cheaper one",
    },
    ("B6", "boundary"): {
        "stages": ['p.init({"d0": "S0", "d1": "S2", "d2": "S4"})\n'
                   'p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S2", "S3"], '
                   '["d2", "S4", "S5"]])\n'],
        "answer": 'p.init({"d0": "S0", "d1": "S2", "d2": "S4"})\n'
                  'p.simd("shuttle", [["d0", "S0", "S5"], ["d1", "S2", "S1"], '
                  '["d2", "S4", "S3"]])\n',
        "why": "the same three ions the other way round: still one direction, still one step",
    },
    ("B7", "boundary"): {
        "stages": ['p.init({"d0": "S0", "d1": "S3"})\n'
                   'p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S3", "S2"]])\n',
                   'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d1", "S3", "S2"]])\n'
                   'p.simd("shuttle", [["d0", "S0", "S1"]])\n'],
        "answer": 'p.init({"d0": "S0", "d1": "S3"})\n'
                  'p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S3", "S4"]])\n',
        "why": "both forward by one: legal in one instruction because it is one direction",
    },
    ("B8", "boundary"): {
        "stages": ['p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d0", ["S0", "A0"])\n'
                   'p.rotate(2)\np.shuttle("d0", ["A0", "S0"])\n'],
        "answer": 'p.init({"d0": "S0", "d1": "S1"})\n'
                  'p.shuttle("d0", ["S0", "A0"], "dock")\np.rotate(2)\n'
                  'p.shuttle("d0", ["A0", "S0"], "undock")\n',
        "why": "name the spur moves dock and undock and read the price difference",
    },
    ("R1", "boundary"): {
        "stages": None,          # taken from the lesson's own stages, which are long
        "answer": 'p.init({"q0": "S0", "q1": "S0"})\np.cool()\n'
                  'p.gate("CX", [["q0", "q1"]])\n',
        "why": "two ions in one site is legal; it is the third that is not",
    },
    ("R3", "boundary"): {
        "stages": None,
        "answer": 'p.init({"d0": "S1", "d1": "S0"})\n'
                  'p.simd("shuttle", [["d0", "S1", "S2"], ["d1", "S0", "S1"]])\n',
        "why": "a convoy: same direction, different rails",
    },
    ("R4", "boundary"): {
        "stages": None,
        "answer": 'p.init({"d0": "A0", "d1": "A0", "d2": "A3", "d3": "A3"})\np.cool()\n'
                  'p.gate("CX", [["d0", "d1"], ["d2", "d3"]])\n',
        "why": "two gates in one instruction IS legal, in two different traps",
    },
    ("C4", "challenge"): {
        "stages": None,
        "answer": None,          # the lesson's programme with the broadcast cool targeted
        "why": "cool only the ancilla that crossed, not every ion",
    },
}

#: Extras whose lesson is solved by device GESTURES rather than by a programme.  Named
#: rather than skipped silently: the count below is asserted, so one appearing here without
#: a reason is a failure and not an omission.
NOT_PROGRAMMES = {
    ("A3", "challenge"): "the lesson is built by placing sites and joining them, so its "
                         "stages are canvas gestures; `editsAtMost` counts those gestures "
                         "and a programme cannot earn this star",
}


def _lessons() -> list[dict]:
    """Every lesson in the course, with its extras and its own stage answers."""
    src = TUTORIAL_JS.read_text(encoding="utf-8", newline="")
    ids = [(m.start(), m.group(1)) for m in
           re.finditer(r"\{ id: '([A-Za-z0-9]+)', part: '[A-Z]',", src)]
    ends = [p for p, _ in ids[1:]] + [len(src)]
    out = []
    for (a, name), b in zip(ids, ends):
        body = src[a:b]
        rec = {"id": name, "extras": [k for k in ("boundary", "challenge")
                                      if re.search(r"\n\s+" + k + r": \{", body)]}
        rec["stages"] = _stage_answers(body)
        out.append(rec)
    return out


def _stage_answers(body: str) -> list[str] | None:
    """The lesson's own per-stage answers, in order, or None if they are not programmes."""
    m = re.search(r"\n\s+solution: \{", body)
    if not m:
        return None
    sol = body[m.start():]
    p = re.search(r"solution: \{ program: " + QUOTED, sol)
    if p:
        return [p.group(1).encode().decode("unicode_escape")]
    st = re.search(r"stages: \[(.*)", sol, re.S)
    if not st:
        return None
    depth, buf, groups = 0, "", []
    for ch in st.group(1):
        if ch == "[":
            depth += 1
            if depth == 1:
                buf = ""
                continue
        if ch == "]":
            depth -= 1
            if depth == 0:
                groups.append(buf)
                continue
            if depth < 0:
                break
        if depth >= 1:
            buf += ch
    stages = []
    for g in groups:
        srcs = re.findall(r"applyProgramSource', " + QUOTED, g)
        if not srcs:
            return None
        stages.append(srcs[-1].encode().decode("unicode_escape"))
    return stages or None


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("extras") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(out)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return out


def _plan() -> list[dict]:
    """One driver record per extra, with the stage answers filled in from the lesson."""
    lessons = {l["id"]: l for l in _lessons()}
    plan = []
    for (lid, kind), spec in ANSWERS.items():
        L = lessons.get(lid)
        assert L, f"{lid} is not a lesson any more; {kind} answer is stale"
        assert kind in L["extras"], f"{lid} no longer carries a {kind}"
        stages = spec["stages"] or L["stages"]
        assert stages, f"{lid}: no stage answers, and none in ANSWERS"
        answer = spec["answer"]
        if answer is None:                       # C4: the lesson's own, cooling only a0
            answer = stages[0].replace("p.cool()\n", 'p.cool(["a0"])\n')
        plan.append({"lesson": lid, "kind": kind, "stages": stages, "answer": answer})
    return plan


def test_every_extra_star_has_an_answer():
    """Anti-vacuity, and the guard that makes the run below mean something.

    A new boundary or challenge with no entry here fails LOUDLY rather than going
    unexercised, which is the state the whole course was in until today.
    """
    have = {(l["id"], k) for l in _lessons() for k in l["extras"]}
    covered = set(ANSWERS) | set(NOT_PROGRAMMES)
    missing = sorted(have - covered)
    assert not missing, (
        f"{len(missing)} extra star(s) with no answer: {missing}. Add one to ANSWERS in "
        f"this file, or to NOT_PROGRAMMES with the reason a programme cannot earn it.")
    stale = sorted(covered - have)
    assert not stale, f"answers for extras that no longer exist: {stale}"
    assert len(have) >= 12, f"only {len(have)} extras found; the course scan is broken"


def test_every_extra_star_can_actually_be_earned(page, tmp_path):
    """The exercises, run.

    Each lesson is solved stage by stage with `applyProgramSource` -- never
    `lessonSolution`, which would set `shown` and put the star permanently out of reach --
    and then the extra's answer is applied and checked.  A failure here is either an
    exercise nobody can complete or an answer that has gone stale, and the feedback text
    says which: a nudge is the page refusing a real attempt, an error is the harness.
    """
    plan = _plan()
    spec = tmp_path / "answers.json"
    spec.write_text(json.dumps(plan), encoding="utf-8")
    r = subprocess.run([NODE, str(DRIVER), str(page), str(spec)],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=900, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    rows = json.loads(r.stdout)
    bad = []
    for row in rows:
        if row.get("ok"):
            continue
        why = row.get("feedback") or row.get("error") or row.get("threw") or "?"
        bad.append(f"  {row['lesson']} {row['kind']}: {why}")
    assert not bad, ("these extra stars could not be earned:\n" + "\n".join(bad)
                     + "\n(a nudge means the exercise refused a real attempt; an error "
                       "means the answer in ANSWERS no longer applies)")
    assert len(rows) == len(ANSWERS), f"{len(rows)} of {len(ANSWERS)} extras were driven"
    # and every one of them awarded the star it exists to award
    thin = [r_["lesson"] for r_ in rows if (r_.get("stars_after") or 0) < 2]
    assert not thin, f"the check passed but no second star was awarded: {thin}"
