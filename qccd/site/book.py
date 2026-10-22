"""The book: chapters written as Lean files, read and run in the browser.

A chapter is ONE solved Lean file (`book/chapters/<slug>.lean`), in the style of Software
Foundations: prose in `/-! ... -/` blocks, Lean between them.  An exercise is a prose block
whose first line is a heading

    #### Exercise 3 (★★) `name`

followed by a code block holding the theorem `name` with its proof.  From the solved file this
module derives everything a reader sees:

* the page, prose rendered and every code block an editable cell;
* the reader's version of each exercise: the statement kept verbatim, the proof `sorry`;
* a hidden grader, appended after the reader's text when the page runs it, that for every
  exercise states the ORIGINAL statement again (`example : <statement> := @name`, so an edited
  statement fails) and prints the axioms the proof depends on (so `sorry`, or a compiler
  trusted through `native_decide`, is seen).

The solved file is checked by Lean as it is, so every exercise is known to be solvable, and
the reader's version is the same file with proofs removed, so it is known to be well formed
apart from them.  Nothing here runs Lean; the page does, in the reader's browser.

    python -m qccd.site.book OUT_DIR [--packs DIR]
        writes OUT_DIR/book/: index.html, the chapters, book-page.js, data/ (the certificates the
        chapters prove) and engine/ (the runtime scripts; with --packs, also engine/site/: the
        Lean engine and library packs that tools/book/build_packs.py writes -- 200 MB, never in git)
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "book"
HERE = Path(__file__).resolve().parent
ASSETS = HERE / "book"

#: axioms an exercise may rest on: propositional extensionality, quotient soundness and choice
#: are the three every Lean proof about logic may assume.  `sorryAx` (an unproved step) and the
#: auxiliary axioms `native_decide` adds (the compiler trusted) are not on the list.
ALLOWED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")

#: The website says nothing about the repository it was built from (qccd/site/prose.py): a
#: reader never sees a path into the tree or a source file's name.  The same pattern as the
#: rendered-page probe (tests/_rendered_paths.mjs, PROBE), so a lesson that passes here passes
#: the site walk.  A Lean module or declaration (`QCCDC.Cert.Check`, `check_sound`), the tool's
#: own commands (`qccd ...`, `qccdc ...`, `qcheck ...`) and the files a reader holds
#: (`prog.qcert.json`, `chain4.arch.json`) are not paths.
READER_PATH = re.compile(
    r"(?:(?:qccd|Compiler|tests|Codesign|tools|Knowledge|Library|examples|docs|arch|site|lean|ChainQ)"
    r"/[A-Za-z0-9_./*-]*[A-Za-z0-9_*/]"
    r"|[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:py|lean|ml|mli|mjs|olean|yaml|yml|js)\b)(?::\d+)?")


def reader_paths(ch: "Chapter") -> list[tuple[str, str]]:
    """Every path a reader of the lesson's page would see: in its prose, and in the code it is
    shown (exercises as the reader gets them; the import line and setup blocks are not shown)."""
    out = []
    for b in ch.blocks:
        if b.kind == "prose":
            text, where = b.text, "prose"
        elif b.hidden or b.setup:
            continue
        else:
            text, where = reader_code(b), "code"
        for m in READER_PATH.finditer(text):
            line = text[:m.start()].count("\n") + 1
            snippet = text.splitlines()[line - 1].strip()[:90]
            out.append((f"{where} block {ch.blocks.index(b)}, line {line}: {snippet}", m.group(0)))
    return out

EXERCISE_RE = re.compile(r"^#{2,4}\s*Exercise\s+(\d+)\s*\(([★]+)\)\s*`([A-Za-z_][A-Za-z0-9_'.]*)`\s*$")


# ----------------------------------------------------------------------------- parsing

@dataclass
class Block:
    kind: str                 # "prose" | "code"
    text: str                 # prose: the markdown; code: the Lean, solved
    exercise: dict | None = None   # on a code block that is an exercise's
    hidden: bool = False      # the import line: run natively, replaced by the browser's prelude
    setup: bool = False       # a `-- setup` block: definitions the lesson builds on, run, not shown


@dataclass
class Chapter:
    slug: str
    title: str
    blocks: list[Block] = field(default_factory=list)
    exercises: list[dict] = field(default_factory=list)


def split_blocks(src: str) -> list[Block]:
    """Prose is every `/-! ... -/` at the start of a line; everything else is Lean.  Nested
    block comments inside prose are not supported (the book never writes one)."""
    src = src.replace("\r\n", "\n")
    out: list[Block] = []
    i = 0
    for m in re.finditer(r"(?m)^/-!(.*?)-/[ \t]*\n?", src, flags=re.S):
        code = src[i:m.start()]
        if code.strip():
            out.append(Block("code", code.strip("\n")))
        out.append(Block("prose", _dedent(m.group(1))))
        i = m.end()
    tail = src[i:]
    if tail.strip():
        out.append(Block("code", tail.strip("\n")))
    return out


def _dedent(s: str) -> str:
    lines = s.strip("\n").splitlines()
    return "\n".join(l.rstrip() for l in lines).strip()


def _scan_to(s: str, i: int, stop: str) -> int:
    """Index of the first `stop` at bracket depth 0 at or after `i`, or -1.  `stop` may be
    two characters (`:=`)."""
    depth = 0
    opens, closes = "([{⟨", ")]}⟩"
    while i < len(s):
        c = s[i]
        if c in opens:
            depth += 1
        elif c in closes:
            depth -= 1
        elif depth == 0 and s.startswith(stop, i):
            # `:` must not be the start of `:=`
            if stop == ":" and s.startswith(":=", i):
                i += 2
                continue
            return i
        i += 1
    return -1


def exercise_parts(code: str, name: str) -> dict:
    """Split `theorem name BINDERS : TYPE := PROOF` into its pieces.  The statement is
    everything up to and including `:=`; the reader gets it verbatim followed by `by sorry`."""
    m = re.search(rf"(?m)^theorem\s+{re.escape(name)}\b", code)
    if not m:
        raise ValueError(f"exercise `{name}`: no `theorem {name}` in its code block")
    head_start, after_name = m.start(), m.end()
    assign = _scan_to(code, after_name, ":=")
    if assign < 0:
        raise ValueError(f"exercise `{name}`: no `:=` after the statement")
    colon = _scan_to(code, after_name, ":")
    if colon < 0 or colon > assign:
        raise ValueError(f"exercise `{name}`: no `:` separating binders from the type")
    binders = code[after_name:colon].strip()
    typ = code[colon + 1:assign].strip()
    before = code[:head_start]
    statement = code[head_start:assign + 2]
    proof = code[assign + 2:]
    after = ""
    # the proof runs to the end of the block, or to the first top-level command after it
    nxt = re.search(r"(?m)^(?:theorem|lemma|def|example|#\w+|structure|inductive|class|instance|abbrev|"
                    r"open|namespace|section|end|variable|set_option|attribute|@\[|/--|/-!)", proof)
    if nxt:
        proof, after = proof[:nxt.start()], proof[nxt.start():]
    expected = f"∀ {binders}, {typ}" if binders else typ
    return {"before": before, "statement": statement, "proof": proof.rstrip(),
            "after": after, "expected": " ".join(expected.split()), "binders": binders, "type": typ}


def parse_chapter(path: Path, slug: str, store: str | None = None, ids: str | None = None) -> Chapter:
    """Progress ids are `<ids>.ex<n>` when the lesson names its prefix (`book:E1`), else
    `book:V<store>.ex<n>` (F1's, kept as first published)."""
    blocks = split_blocks(path.read_text(encoding="utf-8"))
    title = ""
    ch = Chapter(slug=slug, title="")
    pending = None
    for b in blocks:
        if b.kind == "prose":
            first = b.text.splitlines()[0] if b.text else ""
            if not title and first.startswith("# "):
                title = first[2:].strip()
            for line in b.text.splitlines():
                m = EXERCISE_RE.match(line.strip())
                if m:
                    pending = {"n": int(m.group(1)), "stars": len(m.group(2)), "name": m.group(3)}
        elif pending is not None:
            parts = exercise_parts(b.text, pending["name"])
            ex = dict(pending, **{k: parts[k] for k in ("statement", "expected", "before", "after")},
                      solution=parts["proof"])
            ex["id"] = (f"{ids}.ex{ex['n']}" if ids else f"book:V{store or slug.split('-')[0]}.ex{ex['n']}")
            b.exercise = ex
            ch.exercises.append(ex)
            pending = None
        if b.kind == "code" and b.text.lstrip().startswith("import ") and not ch.blocks:
            b.hidden = True
        elif b.kind == "code" and b.text.lstrip().startswith("-- setup"):
            b.setup = True
        ch.blocks.append(b)
    if pending is not None:
        raise ValueError(f"exercise `{pending['name']}` has no code block after its heading")
    ch.title = title or slug
    names = [e["name"] for e in ch.exercises]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate exercise names in {path.name}: {names}")
    return ch


def reader_code(b: Block) -> str:
    """What the reader's cell starts as: an exercise's proof replaced by `sorry`."""
    if not b.exercise:
        return b.text
    e = b.exercise
    return (e["before"] + e["statement"] + " by\n  sorry\n" + (e["after"] if e["after"].strip() else "")).rstrip()


def grader(ch: Chapter) -> str:
    """The hidden block run after the reader's text.  One `example` and one `#print axioms`
    per exercise, each on its own line, so the page reads every verdict off its line."""
    out = ["", "-- the tutorial's grader: not part of the lesson"]
    for e in ch.exercises:
        # a restated statement names its binders and uses none of them: not the reader's lint
        out.append(f"set_option linter.unusedVariables false in example : {e['expected']} := @{e['name']}")
        out.append(f"#print axioms {e['name']}")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- prose

#: a code span, or a link whose text may hold code spans of its own; whichever starts first wins,
#: so `l[i](x)` stays code and [`bell.qcheck.json`](data/...) is a link
_TOKEN = re.compile(r"`([^`]+)`|\[((?:[^\]`]|`[^`]*`)+)\]\(([^)\s]+)\)")


def _inline(s: str) -> str:
    out, i = [], 0
    for m in _TOKEN.finditer(s):
        out.append(_emph(html.escape(s[i:m.start()], quote=False)))
        if m.group(1) is not None:
            out.append("<code>" + html.escape(m.group(1), quote=False) + "</code>")
        else:
            out.append(f'<a href="{html.escape(m.group(3))}">{_spans(m.group(2))}</a>')
        i = m.end()
    out.append(_emph(html.escape(s[i:], quote=False)))
    return "".join(out)


def _spans(s: str) -> str:
    out, i = [], 0
    for m in re.finditer(r"`([^`]+)`", s):
        out.append(_emph(html.escape(s[i:m.start()], quote=False)))
        out.append("<code>" + html.escape(m.group(1), quote=False) + "</code>")
        i = m.end()
    out.append(_emph(html.escape(s[i:], quote=False)))
    return "".join(out)


def _emph(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return dash(s)


def dash(s: str) -> str:
    """The book's sources write a spaced ` -- `; a reader sees the dash the rest of the site prints."""
    return s.replace(" -- ", " — ")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>", "", s).lower()).strip("-")


def prose_html(md: str, ex: dict | None = None) -> str:
    """The subset the book writes: headings, paragraphs, bullet and numbered lists, tables,
    fenced code.  Anything else is a paragraph."""
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        st = ln.strip()
        if not st:
            i += 1
            continue
        if st.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append('<pre class="lean static"><code>' + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", st)
        if m:
            level = len(m.group(1))
            em = EXERCISE_RE.match(st)
            if em:
                stars = '<span class="stars" aria-label="difficulty">' + em.group(2) + "</span>"
                out.append(f'<h4 class="exercise" id="ex-{em.group(3)}">Exercise {em.group(1)} {stars} '
                           f'<code>{html.escape(em.group(3))}</code> <span class="exstate" data-ex="{em.group(3)}">open</span></h4>')
            else:
                tag = "h1" if level == 1 else f"h{level}"
                text = _inline(m.group(2))
                out.append(f'<{tag} id="{_slug(text)}">{text}</{tag}>')
            i += 1
            continue
        if st.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            out.append("<table class=\"tbl\"><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
            continue
        if re.match(r"^([*-]|\d+\.)\s+", st):
            ordered = bool(re.match(r"^\d+\.", st))
            items = []
            while i < len(lines) and lines[i].strip():
                s2 = lines[i].strip()
                mm = re.match(r"^([*-]|\d+\.)\s+(.*)$", s2)
                if mm:
                    items.append(mm.group(2))
                elif items:
                    items[-1] += " " + s2           # a continuation line of the last item
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(t)}</li>" for t in items) + f"</{tag}>")
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|```|\||[*-]\s|\d+\.\s)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out)


# ----------------------------------------------------------------------------- published cases

COMPILED = HERE / "compiled"        # the Compilation page's examples (compile_examples.py)


def _s(x) -> str:
    return json.dumps(x, ensure_ascii=False)       # a Lean string literal is a JSON one here


def lean_input(ident: str, d: dict) -> str:
    """A qcheck input document as the Lean value `Input.ofJson` would read from it: the same
    fields in the same order, so a theorem about it is a theorem about the published file.
    `Input.ofJson` reads a JSON object's members in its keys' text order (`"10"` before `"2"`),
    so the map, the starting places and the loops are listed that way here too."""
    qmap = [(int(k), v) for k, v in sorted(d.get("map", {}).items()) if k.isdigit()]
    init = sorted(d.get("init", {}).items())
    loops = sorted((d.get("loops") or {}).items())
    strs = lambda xs: "[" + ", ".join(_s(x) for x in xs) + "]"
    nats = lambda xs: "[" + ", ".join(str(int(x)) for x in xs) + "]"
    return "\n".join([
        f"def {ident} : Input :=",
        f"  {{ circuit := {_s(d.get('circuit', '?'))}, arch := {_s(d.get('arch', '?'))}, nQubits := {int(d.get('n_qubits', 0))},",
        "    qmap := [" + ", ".join(f"({q}, {_s(i)})" for q, i in qmap) + "],",
        "    init := [" + ", ".join(f"({_s(k)}, {_s(v)})" for k, v in init) + "],",
        "    ops := [" + ", ".join(f"⟨{o['i']}, {_s(o['name'])}, {nats(o['qubits'])}⟩" for o in d.get("circuit_ops", [])) + "],",
        "    moves := [" + ", ".join(f"⟨{m['t']}, {_s(m['ion'])}, {_s(m['from'])}, {_s(m['to'])}⟩" for m in d.get("moves", [])) + "],",
        "    rots := [" + ", ".join(f"⟨{r['t']}, {_s(r['loop'])}, {int(r['delta'])}⟩" for r in d.get("rotations", [])) + "],",
        "    gates := [" + ", ".join(f"⟨{g['dag']}, {g['t']}, {_s(g['site'])}, {strs(g['ions'])}⟩" for g in d.get("gates", [])) + "],",
        f"    unrealised := {nats(d.get('unrealised', []))},",
        f"    gateSites := {strs(d.get('gate_sites', []))},",
        "    hops := [" + ", ".join(f"({_s(h['from'])}, {_s(h['to'])})" for h in d.get("hops", [])) + "],",
        "    loops := [" + ", ".join(f"({_s(k)}, {strs(v)})" for k, v in loops) + "] }",
    ])


def published_cases() -> list[dict]:
    """Every Compilation-page example with a checker input: what the page offers to prove."""
    out = []
    for qc in sorted(COMPILED.glob("*/prog.qcheck.json")):
        eid = qc.parent.name
        d = json.loads(qc.read_text(encoding="utf-8"))
        ident = "case_" + re.sub(r"[^A-Za-z0-9_]", "_", eid)
        out.append({"id": eid, "arch": d.get("arch"), "ops": len(d.get("circuit_ops", [])),
                    "moves": len(d.get("moves", [])), "rots": len(d.get("rotations", [])),
                    "witnesses": len(d.get("gates", [])), "ident": ident,
                    "lean": lean_input(ident, d) + f"\n\ntheorem {ident}_ok : Implements {ident} := check_sound (by decide +kernel)\n"
                            f"#print axioms {ident}_ok\n",
                    "files": {"qcheck": f"data/{eid}.qcheck.json", "qcert": f"data/{eid}.qcert.json"},
                    "_dir": qc.parent})
    return out


def devices() -> dict:
    """The geometry of every device a figure may draw, from its own document: node positions
    in model units, rails, loops, and which sites can gate."""
    out = {}
    for f in sorted(COMPILED.glob("*.arch.json")):
        if f.name.endswith(".expanded.json"):
            continue
        a = json.loads(f.read_text(encoding="utf-8"))
        g, zt = a["geometry"], a.get("zone_types", {})
        out[a["name"]] = {
            "nodes": [[n["id"], n["pos"][0], n["pos"][1], n["kind"],
                       bool(zt.get(n.get("zone_type") or "", {}).get("gate"))] for n in g["nodes"]],
            "segs": [s["ends"] for s in g.get("segments", [])],
            "loops": [{"id": l["id"], "nodes": l["nodes"], "closed": bool(l.get("closed"))} for l in g.get("loops", [])],
        }
    return out


FIGURE_RE = re.compile(r"^\{\{(figure|studio|gadget)\s+([A-Za-z_][A-Za-z0-9_'./-]*)((?:\s+[a-z]+=[^\s}]+)*)\s*\}\}$")
GADGET_RE = re.compile(r"(?m)^\{\{gadget\s+([A-Za-z_][A-Za-z0-9_'./-]*)")


def gadget_file(name: str) -> str:
    """Where a {{gadget NAME}} figure's page is written, beside the studio pages."""
    return "ex/gadget-" + re.sub(r"[^A-Za-z0-9_-]+", "-", name) + ".html"


def figure_html(name: str, params: str, kind: str = "figure") -> str:
    kv = dict(p.split("=", 1) for p in params.split())
    if kind == "gadget":
        # a design page of the gadget level (qccd.gadget.site.embed_page): no Lean value behind
        # it, so it is drawn as the page is built; `focus` and `at` (microseconds) go in its address
        frag = "&".join(f"{k}={v}" for k, v in (("f", kv.get("focus")), ("t", kv.get("at"))) if v)
        src = gadget_file(name) + ("#" + frag if frag else "")
        return (f'<figure class="leanfig gadgetfig" data-kind="gadget" data-name="{html.escape(name)}">'
                f'<div class="fighead"><b>{html.escape(name)}</b> in the design tool'
                + (f', at <code>{html.escape(kv["focus"])}</code>' if kv.get("focus") else "")
                + '</div><div class="studiobox"><iframe class="studio" loading="lazy" title="the design tool: '
                f'{html.escape(name)}" src="{html.escape(src)}"></iframe></div></figure>')
    attrs = "".join(f' data-{k}="{html.escape(v)}"' for k, v in kv.items() if k in ("at", "show"))
    if kind == "studio":
        attrs += ' data-kind="studio"'
    return (f'<figure class="leanfig" data-name="{html.escape(name)}"{attrs}>'
            '<div class="figwait">This figure is drawn from Lean\'s own replay of '
            f'<code>{html.escape(name)}</code>; it appears when Lean has checked the lesson.</div></figure>')


CERTS_PANEL = ('<div class="certs" id="certs"><div class="act"><button type="button" id="certsgo" disabled>'
               'Prove all of them</button><span id="certsum">Lean is loading.</span></div><div id="certrows"></div></div>')


# ----------------------------------------------------------------------------- pages

def lesson_nav(part: dict, prev: dict | None, nxt: dict | None) -> str:
    """The foot of a lesson: the lesson before and the lesson after, in the tutorial's order,
    and the way back to the tutorial's contents."""
    def link(c: dict | None, cls: str, arrow: str) -> str:
        if not c:
            return f'<span class="{cls}"></span>'
        text = f'{c["id"]} &middot; {html.escape(dash(c["title"]))}'
        return f'<a class="{cls}" href="{c["slug"]}.html">{arrow if cls == "prev" else ""}{text}{arrow if cls == "next" else ""}</a>'
    return ('<nav class="lessonnav">' + link(prev, "prev", "&lsaquo; ")
            + f'<a class="toc" href="../learn/#part-{part["part"]}">Tutorial contents</a>'
            + link(nxt, "next", " &rsaquo;") + "</nav>")


def chapter_body(ch: Chapter, part: dict, lesson: dict, prev: dict | None = None, nxt: dict | None = None) -> str:
    cells, k = [], 0
    for b in ch.blocks:
        if b.kind == "prose":
            figs, lines = [], []
            for line in b.text.replace("{{certificates}}", "\n\n@@CERTS@@\n\n").splitlines():
                m = FIGURE_RE.match(line.strip())
                if m:
                    figs.append(figure_html(m.group(2), m.group(3), m.group(1)))
                    lines += ["", f"@@FIG{len(figs) - 1}@@", ""]
                else:
                    lines.append(line)
            body = prose_html("\n".join(lines)).replace("<p>@@CERTS@@</p>", CERTS_PANEL)
            for i, f in enumerate(figs):
                body = body.replace(f"<p>@@FIG{i}@@</p>", f)
            cells.append('<div class="prose">' + body + "</div>")
            continue
        code = reader_code(b)
        attrs = f' data-cell="{k}"'
        if b.hidden:
            attrs += ' data-hidden="1"'
        if b.setup:
            attrs += ' data-setup="1"'
        if b.exercise:
            attrs += f' data-ex="{html.escape(b.exercise["name"])}"'
        cells.append(f'<div class="cell"{attrs}><textarea rows="1" spellcheck="false" autocapitalize="off" autocomplete="off"'
                     f' aria-label="Lean code">{html.escape(code)}</textarea><div class="msgs" aria-live="polite"></div></div>')
        k += 1
    data = {"slug": ch.slug, "part": part["part"], "lesson": lesson["id"], "title": ch.title,
            "exercises": [{"n": e["n"], "stars": e["stars"], "name": e["name"], "id": e["id"]} for e in ch.exercises],
            "grader": grader(ch), "allowed": list(ALLOWED_AXIOMS),
            # what the browser's Lean puts in front of the reader's text, in place of the
            # chapter's own `import` line (its engine compiles every file as a module)
            "prelude": (ASSETS / "engine" / "prelude.lean").read_text(encoding="utf-8"),
            # the figures: Lean's replay of a named certificate, drawn on its device
            "figureHelper": (ASSETS / "engine" / "figure.lean").read_text(encoding="utf-8"),
            # the tutorial's four small devices, and any other the lesson names (a place's device)
            "devices": {k: v for k, v in devices().items()
                        if k in ("chain4", "grid2x3", "race8", "ring6d")
                        or any(f'"{k}"' in b.text for b in ch.blocks if b.kind == "code")},
            "cases": ([{k: v for k, v in c.items() if not k.startswith("_")} for c in published_cases()]
                      if any("{{certificates}}" in b.text for b in ch.blocks if b.kind == "prose") else [])}
    head = (f'<p class="crumbs"><a href="../learn/#part-{part["part"]}">Tutorial</a> &rsaquo; '
            f'Part {part["part"]} &middot; {html.escape(part["title"])} &rsaquo; {lesson["id"]}</p>')
    bar = ('<div id="leanbar" role="status"><span class="dot"></span><span class="what">Lean is loading'
           '</span><span class="prog"></span><span class="score"></span></div>')
    return (head + bar + '<article id="chapter">' + "\n".join(cells) + "</article>"
            + lesson_nav(part, prev, nxt)
            + '<aside id="goals" hidden><div class="gh">Goals at the cursor</div><pre></pre></aside>'
            + ENGINE_NOTE
            + f'<script type="application/json" id="bookdata">{script_json(data)}</script>')


def script_json(data) -> str:
    """JSON for a `<script type="application/json">`, whose text the browser does NOT decode as
    HTML: entity-escaping it (`=>` as `=&gt;`) corrupts the Lean inside.  Only a closing tag or a
    comment opener can end the element early, so only those are escaped, as JSON escapes."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\u0021--")


ENGINE_NOTE = ('<p class="enginenote">The Lean on this page is Lean&nbsp;4 (Apache-2.0), compiled to '
               'WebAssembly by the <a href="https://github.com/cauli/lean4-wasm-in-browser">cauli/lean4</a> '
               'browser build (Lean 4.33, Apache-2.0), hosted here byte for byte. The proved checker it loads is '
               "the repository's <code>QCCDC.Cert</code>, carried to that Lean by a mechanical port: "
               'module syntax, and two tail-recursive functions each '
               "proved equal to the one it replaces; it gives the repository checker's verdict on every "
               'published certificate. The first time a lesson in this part opens, your browser downloads Lean '
               '(about 58&nbsp;MB, kept for next time); a desktop browser is needed.</p>')


def outline() -> dict:
    return json.loads((BOOK / "book.json").read_text(encoding="utf-8"))


def tutorial_parts() -> list[dict]:
    """The tutorial's Lean parts (E-I), for the Learn page's table of contents
    (qccd/site/progress.py): per part its letter, title and blurb, its live lessons with the
    progress ids of their exercises and each exercise's stars, and the titles still to come.

        [{"part": "F", "title": ..., "about": ...,
          "lessons": [{"id": "F1", "title": ..., "story": ..., "href": "../book/2.1-certificates.html",
                       "exercises": ["book:V2.1.ex1", ...], "stars": [1, 1, 1, 1, 2, 2, 3, 3], "live": True}],
          "coming": ["The specification, condition by condition", ...]}, ...]

    A part whose entry says `"status": "hidden"` is not listed at all, not even as coming: it is
    still being reviewed, and every build of the Learn page must agree on that."""
    out = []
    for part in outline()["parts"]:
        if part.get("status") == "hidden":
            continue
        lessons, coming = [], []
        for les in part["lessons"]:
            if les.get("status") != "live":
                coming.append(les["title"])
                continue
            ch = parse_chapter(BOOK / les["source"], les["slug"], les.get("store"), les.get("ids"))
            lessons.append({"id": les["id"], "title": les["title"], "story": les.get("story") or lesson_story(ch),
                            "href": f'../book/{les["slug"]}.html',
                            "exercises": [e["id"] for e in ch.exercises],
                            "stars": [e["stars"] for e in ch.exercises], "live": True})
        out.append({"part": part["part"], "title": part["title"], "about": part["about"],
                    "lessons": lessons, "coming": coming})
    return out


def lesson_story(ch: Chapter) -> str:
    """The lesson's first sentence of prose after its title and its italic note: the line the
    tutorial's cards show under a title."""
    for b in ch.blocks:
        if b.kind != "prose":
            continue
        for para in re.split(r"\n\s*\n", b.text):
            t = " ".join(para.split())
            if not t or t.startswith("#") or t.startswith("*"):
                continue
            # a card shows plain text: drop the prose's marks (`code`, **bold**, *italic*, links)
            t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
            t = re.sub(r"`([^`]*)`|\*\*([^*]+)\*\*|\*([^*]+)\*", lambda m: next(g for g in m.groups() if g is not None), t)
            m = re.match(r"(.+?[.!?])(\s|$)", t)
            return dash(m.group(1) if m else t)
    return ""


def redirect_html(target: str) -> str:
    """/book/ is the address of the tutorial's Lean lessons, not a page of its own."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Tutorial - QCCD studio</title>'
            f'<meta http-equiv="refresh" content="0; url={target}"><link rel="canonical" href="{target}">'
            f'</head><body><script>location.replace({json.dumps(target)})</script>'
            f'<p>The tutorial is on the <a href="{target}">Learn page</a>.</p></body></html>')


def studio_pages(dst: Path) -> list[Path]:
    """The studio pages the figures embed, made by the platform's own renderer -- the same
    engine, animation and transport as every studio page on the site -- in its embed mode:

    * ex/dev-<arch>.html: the device, no programme; a chapter figure loads into it the programme
      Lean's replay of a certificate describes;
    * ex/<id>.html: a published example's REAL compiled programme with its circuit through the
      checked source map, exactly as the Compilation page renders it (build_compiled_pages)."""
    from . import build as site
    from ..__main__ import main as qccd_main
    from ..arch import Architecture
    from ..cost import corrected_model
    from ..ir.source_map import build as build_source
    from ..ir.tsir import TSIR
    from ..verify import verify
    from ..viz.render import render_html
    ex = dst / "ex"
    ex.mkdir(parents=True, exist_ok=True)
    out = []

    def embed(p: Path) -> None:
        s = p.read_text(encoding="utf-8")
        i = s.rindex("</body>")
        p.write_text(s[:i] + site.HASH_JS + s[i:], encoding="utf-8")
        out.append(p)

    for f in sorted(COMPILED.glob("*.arch.json")):
        if f.name.endswith(".expanded.json"):
            continue
        arch = json.loads(f.read_text(encoding="utf-8"))["name"]
        p = ex / f"dev-{arch}.html"
        if qccd_main(["studio", "--seed", str(f), "--no-all-templates", "-o", str(p)]) not in (0, None):
            raise RuntimeError(f"qccd studio failed for {f.name}")
        embed(p)
    wanted = {c["id"] for c in published_cases()}
    model, archs = corrected_model(), {}
    for g in site.load_compiled() + site.load_algo():
        if g["id"] not in wanted:
            continue
        dev = g["meta"].get("device", "ring6d")
        if dev not in archs:
            archs[dev] = Architecture.from_json(json.loads((COMPILED / f"{dev}.arch.json").read_text(encoding="utf-8")))
        prog = TSIR.load(g["dir"] / "prog.cooled.tsir.json")
        source = build_source(prog, g["cert"], g["dir"] / "circuit.qasm")
        res = verify(prog, archs[dev], model, check_metrics=False).result
        p = ex / f'{g["id"]}.html'
        render_html(archs[dev], prog, res, model, p, kicker="COMPILED", headline=f'{g["meta"]["title"]} on {dev}',
                    lede=g["meta"]["note"], source=source, open_pane="Q")
        embed(p)
    return out


def build(out: Path, packs: Path | None = None, studio: bool = True, drafts: bool = False,
          only: list[str] | None = None, solutions: bool = False) -> list[Path]:
    """`drafts`: also lessons whose status is "draft" (an author's preview, never deployed);
    `only`: just these lesson ids; `solutions`: book/_solutions/<slug>.json, each exercise's solved
    code, for the browser harness (never deployed)."""
    from .build import PAGE, STYLE            # the site's own document page and stylesheet
    parts = outline()["parts"]
    css = (ASSETS / "book.css").read_text(encoding="utf-8")
    dst = out / "book"
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    first = next((pt["part"] for pt in parts if any(l.get("status") == "live" for l in pt["lessons"])), "A")
    (dst / "index.html").write_text(redirect_html(f"../learn/#part-{first}"), encoding="utf-8")
    written.append(dst / "index.html")

    def shown(c: dict) -> bool:               # a lesson this build writes a page for
        return ((c.get("status") == "live" or (drafts and c.get("status") == "draft"))
                and (BOOK / c.get("source", "-")).exists())
    # the lessons in the tutorial's order, for each page's previous / next links
    order = [(part, c) for part in parts for c in part["lessons"] if shown(c)]
    gadgets: set[str] = set()                 # the design pages the built lessons frame
    for k, (part, c) in enumerate(order):
        if only and c["id"] not in only:
            continue
        gadgets.update(GADGET_RE.findall((BOOK / c["source"]).read_text(encoding="utf-8")))
        ch = parse_chapter(BOOK / c["source"], c["slug"], c.get("store"), c.get("ids"))
        if solutions:
            (dst / "_solutions").mkdir(exist_ok=True)
            sol = {b.exercise["name"]: b.text for b in ch.blocks if b.exercise}
            (dst / "_solutions" / f'{c["slug"]}.json').write_text(json.dumps(sol, ensure_ascii=False), encoding="utf-8")
        body = chapter_body(ch, part, c, order[k - 1][1] if k else None,
                            order[k + 1][1] if k + 1 < len(order) else None)
        page = PAGE.format(title=f'{c["id"]} {html.escape(ch.title)} - QCCD studio', style=STYLE,
                           extra_css=css, body=body)
        # the Lean runtime first (it defines window.Book), the figures, then the page
        page = page.replace("</body>", '<script src="engine/book-runtime.js" defer></script>'
                                       '<script src="book-progress.js" defer></script>'
                                       '<script src="book-figure.js" defer></script>'
                                       '<script src="book-page.js" defer></script></body>')
        (dst / f'{c["slug"]}.html').write_text(page.replace("__FOOTER__", ""), encoding="utf-8")
        written.append(dst / f'{c["slug"]}.html')
    if gadgets:
        # the gadget level's own pages (qccd.gadget.site.embed_page): one self-contained file
        # each, served beside the studio pages so they share the lesson's isolation headers
        from ..gadget.site import embed_page
        (dst / "ex").mkdir(exist_ok=True)
        for name in sorted(gadgets):
            written.append(embed_page(name, dst / gadget_file(name)))
    for name in ("book-page.js", "book-figure.js", "book-progress.js"):
        (dst / name).write_bytes((ASSETS / name).read_bytes())
        written.append(dst / name)
    eng = dst / "engine"
    eng.mkdir(exist_ok=True)
    for name in ("book-runtime.js", "lean-worker.js"):
        (eng / name).write_bytes((ASSETS / "engine" / name).read_bytes())
        written.append(eng / name)
    if packs is not None:
        site = packs / "site" if (packs / "site" / "manifest.json").exists() else packs
        if not (site / "manifest.json").exists():
            raise FileNotFoundError(f"{packs}: no manifest.json (run tools/book/build_packs.py)")
        (eng / "site").mkdir(exist_ok=True)
        for f in sorted(site.iterdir()):
            if f.is_file():
                shutil.copyfile(f, eng / "site" / f.name)
                written.append(eng / "site" / f.name)
    if studio:
        written += studio_pages(dst)
    # the certificates the chapters prove, published beside them: what the page checks is a
    # file the reader can download and check again with the command-line checker
    (dst / "data").mkdir(exist_ok=True)
    for c in published_cases():
        for kind, fname in (("qcheck", "prog.qcheck.json"), ("qcert", "prog.qcert.json")):
            src = c["_dir"] / fname
            if src.exists():
                (dst / c["files"][kind]).write_bytes(src.read_bytes())
                written.append(dst / c["files"][kind])
    return written


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    packs = Path(argv[argv.index("--packs") + 1]) if "--packs" in argv else None
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
    written = build(Path(argv[0]), packs, studio="--no-studio" not in argv, drafts="--drafts" in argv,
                    only=only, solutions="--solutions" in argv)
    print(f"wrote {len(written)} files under {Path(argv[0]) / 'book'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
