"""The book's chapters, checked by Lean as their readers will meet them.

Every live chapter is run twice through the checker package's Lean (`Compiler/lean/checker`,
the toolchain the proved checker is built with):

* SOLVED -- the chapter file as written, with the book's grader appended: no errors, no
  `sorry`, and every exercise rests only on the allowed axioms.  This is the proof that every
  exercise can be done.
* READER -- the chapter as the page hands it out, every exercise proof replaced by `sorry`,
  with the same grader: no errors, and every exercise reported OPEN (its axioms include
  `sorryAx`) while its statement still type-checks.  An exercise the reader's version already
  solves, or a statement the grader cannot restate, fails here.

The browser runs a newer Lean (the WebAssembly build); `tests/book.mjs` is the in-browser
counterpart.  This test needs `lake` on PATH and the checker package built
(`cd Compiler/lean/checker && lake build`); without them it skips, saying so.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.site import book

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "Compiler" / "lean" / "checker"
LIVE = [(pt["part"], c) for pt in json.loads((book.BOOK / "book.json").read_text(encoding="utf-8"))["parts"]
        for c in pt["lessons"] if c.get("status") == "live"]

def _lean_available() -> bool:
    return shutil.which("lake") is not None and (CHECKER / ".lake" / "build" / "lib" / "lean" / "QCCDC").is_dir()


SEVERITY = {"error": "error", "warning": "warning", "information": "info"}


def run_lean(text: str, tmp: Path) -> list[dict]:
    """Every message Lean prints for `text`, as {line, sev, text}.  `--json` because the plain
    output prints an information message (an `#eval` result, `#print axioms`) with no position."""
    f = tmp / "Chapter.lean"
    f.write_text(text, encoding="utf-8")
    r = subprocess.run(["lake", "env", "lean", "--json", str(f)], cwd=CHECKER, capture_output=True, timeout=900)
    msgs = []
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        m = json.loads(ln)
        msgs.append({"line": m["pos"]["line"], "sev": SEVERITY.get(m["severity"], m["severity"]), "text": m["data"]})
    if r.returncode not in (0, 1):
        raise RuntimeError(f"lean exited {r.returncode}: {r.stderr.decode('utf-8', 'replace')[:400]}")
    return msgs


def axioms_of(msgs: list[dict], name: str) -> list[str] | None:
    for m in msgs:
        t = m["text"]
        if t.startswith(f"'{name}' does not depend on any axioms"):
            return []
        mm = re.match(rf"^'{re.escape(name)}' depends on axioms: \[(.*?)\]", t, flags=re.S)
        if mm:
            return [a.strip() for a in mm.group(1).split(",") if a.strip()]
    return None


def grader_lines(text: str) -> int:
    """The 1-based line the grader starts on, when appended to `text`."""
    return text.count("\n") + 2


@pytest.fixture(scope="module")
def lean():
    if not _lean_available():
        pytest.skip("no `lake` on PATH or the checker package is not built (cd Compiler/lean/checker && lake build)")


@pytest.mark.parametrize("vol,c", LIVE, ids=[c["slug"] for _, c in LIVE])
def test_the_solved_chapter_passes_every_exercise(lean, vol, c, tmp_path):
    ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"))
    assert ch.exercises, "a live chapter without exercises"
    src = (book.BOOK / c["source"]).read_text(encoding="utf-8").replace("\r\n", "\n")
    msgs = run_lean(src + book.grader(ch), tmp_path)
    errors = [m for m in msgs if m["sev"] == "error"]
    assert not errors, "\n".join(f'{m["line"]}: {m["text"]}' for m in errors)
    sorries = [m for m in msgs if "declaration uses 'sorry'" in m["text"]]
    assert not sorries, sorries
    for e in ch.exercises:
        ax = axioms_of(msgs, e["name"])
        assert ax is not None, f'no axioms line for {e["name"]}'
        bad = [a for a in ax if a not in book.ALLOWED_AXIOMS]
        assert not bad, f'{e["name"]} rests on {bad}'


@pytest.mark.parametrize("vol,c", LIVE, ids=[c["slug"] for _, c in LIVE])
def test_the_readers_chapter_leaves_every_exercise_open(lean, vol, c, tmp_path):
    ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"))
    reader = "\n\n".join(book.reader_code(b) for b in ch.blocks if b.kind == "code") + "\n"
    msgs = run_lean(reader + book.grader(ch), tmp_path)
    start = grader_lines(reader)
    errors = [m for m in msgs if m["sev"] == "error"]
    assert not errors, "\n".join(f'{m["line"]}: {m["text"]}' for m in errors)
    for e in ch.exercises:
        ax = axioms_of(msgs, e["name"])
        assert ax is not None, f'no axioms line for {e["name"]}'
        assert "sorryAx" in ax, f'{e["name"]} is already solved in the reader\'s version: {ax}'
    # and the grader itself raised nothing (the statements restate cleanly)
    assert not [m for m in msgs if m["line"] >= start and m["sev"] == "error"]


def test_the_page_carries_the_lean_byte_for_byte(tmp_path):
    """The page reads its grader, prelude and certificate Lean out of a JSON <script>, whose text
    the browser never entity-decodes: an HTML-escaped `=>` (`=&gt;`) once made every restated
    statement with a `>` fail to parse, and an engine that dropped parse errors then counted the
    exercise solved with its statement unchecked."""
    book.build(tmp_path, studio=False)
    for vol, c in LIVE:
        ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"))
        page = (tmp_path / "book" / f'{c["slug"]}.html').read_text(encoding="utf-8")
        blob = re.search(r'<script type="application/json" id="bookdata">(.*?)</script>', page, re.S).group(1)
        data = json.loads(blob)
        assert data["grader"] == book.grader(ch)
        assert data["prelude"] == (book.ASSETS / "engine" / "prelude.lean").read_text(encoding="utf-8")
        for case in data["cases"]:
            assert "&gt;" not in case["lean"] and "&lt;" not in case["lean"] and "&amp;" not in case["lean"]


def test_the_figures_draw_what_lean_replays(lean, tmp_path):
    """Every figure in lesson F1 is Lean's replay of a named certificate
    (engine/figure.lean).  Run natively, the figures must say what the chapter's prose says
    about them: the Bell pair holds; the late move's CNOT finds q0 in C1; the teleport's move is
    no hop; the foreign witness is co-located but not the op's own ions."""
    vol, c = next((v, c) for v, c in LIVE if c["id"] == "F1")
    ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"))
    src = (book.BOOK / c["source"]).read_text(encoding="utf-8").replace("\r\n", "\n")
    names = re.findall(r"\{\{figure\s+([A-Za-z_][A-Za-z0-9_']*)", src)
    assert {"bell", "bellLate", "bellTeleport", "bellForeign"} <= set(names), names
    tail = (book.ASSETS / "engine" / "figure.lean").read_text(encoding="utf-8")
    evals = "".join(f"#eval IO.println (QCCDBook.figure {n}).compress\n" for n in dict.fromkeys(names))
    msgs = run_lean(src + book.grader(ch) + "\n" + tail + evals, tmp_path)
    assert not [m for m in msgs if m["sev"] == "error"]
    figs = [json.loads(m["text"]) for m in msgs if m["sev"] == "info" and m["text"].lstrip().startswith('{"')]
    by = {}
    for n, f in zip(dict.fromkeys(names), figs):
        by[n] = f
    assert by["bell"]["check"] is True and by["bell"]["arch"] == "chain4"
    assert [p for p in by["bell"]["pos"][4] if p[0] == "q0"] == [["q0", "C0"]]       # moved in cycle 3
    late_cx = [g for g in by["bellLate"]["gates"] if g[7] == "cx"][0]
    assert by["bellLate"]["check"] is False and late_cx[4] == ["C1", "C0"]
    assert by["bellTeleport"]["check"] is False and by["bellTeleport"]["moves"][0][4] is False
    # ... and the hop is ALL that is wrong with it: every witness finds its ions in its trap
    assert all(g[4] == [g[2]] * len(g[3]) for g in by["bellTeleport"]["gates"])
    foreign_cx = [g for g in by["bellForeign"]["gates"] if g[7] == "cx"][0]
    assert foreign_cx[4] == ["C0", "C0"] and foreign_cx[6] is False


def test_a_published_certificate_with_rotations_draws_them(lean, tmp_path):
    """The certificates panel's figures: a ring certificate's rotations come out as flat
    [t, loop, delta] rows (a Lean triple would print nested), and its replay holds."""
    case = [c for c in book.published_cases() if c["rots"]]
    assert case, "no published certificate with a rotation"
    c = case[0]
    text = ("import QCCDC.Cert.Sound\nopen QCCDC.Cert\n\n" + c["lean"].split("\n\ntheorem")[0] + "\n\n"
            + (book.ASSETS / "engine" / "figure.lean").read_text(encoding="utf-8")
            + f"#eval IO.println (QCCDBook.figure {c['ident']}).compress\n")
    msgs = run_lean(text, tmp_path)
    assert not [m for m in msgs if m["sev"] == "error"], msgs
    fig = [json.loads(m["text"]) for m in msgs if m["text"].lstrip().startswith('{"')][0]
    assert fig["check"] is True
    assert fig["rots"] and all(len(r) == 3 and isinstance(r[2], int) for r in fig["rots"]), fig["rots"]


def test_the_tutorials_lean_parts_keep_their_shape_and_their_progress_ids():
    """The Learn page (qccd/site/progress.py) reads tutorial_parts(); a reader's solved exercises
    are stored under these ids, so they never change."""
    parts = book.tutorial_parts()
    assert [p["part"] for p in parts] == [p["part"] for p in book.outline()["parts"] if p.get("status") != "hidden"]
    assert [p["part"] for p in parts][:5] == ["E", "F", "G", "H", "I"]
    f1 = [l for p in parts for l in p["lessons"] if l["id"] == "F1"][0]
    assert f1["href"] == "../book/2.1-certificates.html" and f1["live"] is True
    assert f1["exercises"] == [f"book:V2.1.ex{i}" for i in range(1, 9)]
    assert f1["stars"] == [1, 1, 1, 1, 2, 2, 3, 3] and f1["story"]
    for p in parts:
        assert set(p) == {"part", "title", "about", "lessons", "coming"}
        for l in p["lessons"]:
            assert set(l) == {"id", "title", "story", "href", "exercises", "stars", "live"}
            assert re.fullmatch(r"[A-Z][0-9]{1,2}", l["id"])


def test_a_lesson_page_reads_as_the_tutorial_not_a_book(tmp_path):
    """No reader-visible 'book', volume or chapter: the Lean lessons are part of the tutorial."""
    book.build(tmp_path, studio=False)
    for part, c in LIVE:
        page = (tmp_path / "book" / f'{c["slug"]}.html').read_text(encoding="utf-8")
        text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", page, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)                 # tags and their attributes (URLs) go
        bad = re.findall(r"(?i)\bbook\b|\bvolume\b|\bchapter\b", text)
        assert not bad, bad
        assert f"Part {part}" in text and c["id"] in text and "Tutorial" in text
    index = (tmp_path / "book" / "index.html").read_text(encoding="utf-8")
    assert "../learn/#part-E" in index and "refresh" in index      # the first part with a live lesson


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_studios_learn_tab_names_the_lean_parts_as_book_json_does():
    """The studio's Learn tab links to the Lean parts (tutorial.js `continues`): one link per
    part, in order, with the title the Learn page shows -- book.json's, for every part that is
    not hidden."""
    js = ("const fs = require('fs');"
          "const T = new Function(fs.readFileSync(process.argv[1], 'utf8') + ';return QCCD_TUTORIAL;')();"
          "process.stdout.write(JSON.stringify(T.continues));")
    out = subprocess.run(["node", "-e", js, str(ROOT / "qccd" / "viz" / "js" / "tutorial.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    cont = json.loads(out.stdout)
    want = [(p["part"], p["title"]) for p in book.outline()["parts"] if p.get("status") != "hidden"]
    assert [(p["id"], p["title"]) for p in cont["parts"]] == want
    assert cont["href"] == "https://qccd.academy/learn/"


def test_no_lesson_shows_a_repository_path():
    """The website says nothing about the repository it was built from: no live lesson's page
    shows a path into the tree or a source file's name, in its prose or in the code a reader is
    given (the same pattern as the site walk's rendered-page probe)."""
    bad = {}
    for _, c in LIVE:
        ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"), c.get("ids"))
        hits = book.reader_paths(ch)
        if hits:
            bad[c["id"]] = [h for _, h in hits]
    assert not bad, bad


def test_every_exercise_has_a_progress_id_the_server_accepts():
    """qccd-86's progress API widens to `^(?:[A-Z][0-9]{1,2}|[a-z]+:[A-Za-z0-9._-]{1,48})$`."""
    rx = re.compile(r"^(?:[A-Z][0-9]{1,2}|[a-z]+:[A-Za-z0-9._-]{1,48})$")
    for _, c in LIVE:
        ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"), c.get("ids"))
        for e in ch.exercises:
            assert rx.match(e["id"]), e["id"]


def test_a_published_input_is_the_value_the_checker_reads_past_ten_qubits(lean, tmp_path):
    """`lean_input` writes the value `Input.ofJson` reads from the same file.  Lean reads a JSON
    object's members in its keys' text order (`"10"` before `"2"`), which a numeric sort of the
    qubit map gets wrong from eleven qubits on: compare the two in Lean, by their printed form."""
    d = {"circuit": "c", "arch": "chain4", "n_qubits": 12,
         "map": {str(q): f"q{q}" for q in range(12)},
         "init": {f"q{q}": "C0" if q % 2 else "C1" for q in range(12)},
         "circuit_ops": [{"i": 0, "name": "h", "qubits": [10]}],
         "moves": [], "rotations": [], "gates": [{"dag": 0, "t": 0, "site": "C1", "ions": ["q10"]}],
         "unrealised": [], "gate_sites": ["C0", "C1"], "hops": [{"from": "C0", "to": "C1"}],
         "loops": {"L1": ["C0", "C1"], "L0": ["C1", "C0"]}}
    text = ("import QCCDC.Cert.Sound\nopen Lean QCCDC.Cert\n\n" + book.lean_input("given", d) + "\n\n"
            f"def parsed : Input := Input.ofJson ((Json.parse {json.dumps(json.dumps(d))}).toOption.get!)\n"
            "#eval toString (repr given) == toString (repr parsed)\n")
    msgs = run_lean(text, tmp_path)
    assert not [m for m in msgs if m["sev"] == "error"], msgs
    assert [m["text"].strip() for m in msgs if m["sev"] == "info"] == ["true"], msgs


def test_a_tampered_statement_is_caught_by_the_grader(lean, tmp_path):
    """Control: weaken one exercise's statement in the reader's text and give it a real proof;
    the grader's restatement must fail, so a changed statement is never counted as solved."""
    vol, c = LIVE[0]
    ch = book.parse_chapter(book.BOOK / c["source"], c["slug"], c.get("store"))
    e = ch.exercises[0]
    reader = "\n\n".join(book.reader_code(b) for b in ch.blocks if b.kind == "code") + "\n"
    tampered = reader.replace(e["statement"] + " by\n  sorry", f"theorem {e['name']} : True := by\n  trivial", 1)
    assert tampered != reader
    msgs = run_lean(tampered + book.grader(ch), tmp_path)
    start = grader_lines(tampered)
    assert [m for m in msgs if m["line"] >= start and m["sev"] == "error"], "the grader accepted a changed statement"
