"""The website says nothing about the repository it was built from.

The reference pages are rendered from the project's own engineering documents, which name
source files freely -- `qccd/verify/rules.py`, `logic/decode.py`, a Lean module, a test.
Those names are true and useful *inside a clone*, and meaningless on a public page: a
reader there has no such file, cannot open it, and does not care which module holds the
check.  This module is the one seam where that vocabulary is removed, so the documents
stay as they are for the people who work on them.

Three mechanisms, in order, each narrower than the last:

1. ``DROP_SECTIONS`` -- a heading whose section exists only to point into the tree
   (``Where these came from``, ``Reproduce it``) takes its whole section with it.
2. ``REWRITES`` -- hand-written replacements, for sentences that carry a real statement
   around a file name and read badly if the name is merely cut out.
3. the automatic pass -- source-map code blocks, commands that run a file in the tree,
   parentheses that hold nothing but a citation, and lone spans.

Whatever the first two miss, the third removes; ``audit()`` then re-reads the result and
reports anything still standing, so a document edited later cannot leak a path quietly.
Artifact names a reader *does* meet -- ``.arch.json``, ``.tsir.json``, ``.qcert.json``,
``.gds`` -- are not paths and stay, with any directory in front of them dropped.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- what a path is

#: Top-level directories of the repository.  A token that starts with one of these is a
#: path into the tree, whatever it ends with.
_DIRS = ("qccd", "Compiler", "tests", "Codesign", "tools", "Knowledge", "Library",
         "examples", "docs", "arch", "site", "lean", "leaves", "logic", "viz", "ChainQ")
#: Extensions that only ever name source.
_SRC = ("py", "lean", "ml", "mli", "mjs", "olean", "yaml", "yml", "js")
#: Extensions a reader meets as a file they hold: these keep their name, lose their folder.
_ARTIFACT = ("arch.json", "tsir.json", "qcert.json", "studio.json", "gadget.json",
             "qasm", "gds", "svg", "alg", "lq", "stim", "dem")

#: The characters a path is written with, and the ones it may end on -- a token never ends
#: on the sentence's own punctuation, so `editor.js.` gives up its full stop.
_W = r"[A-Za-z0-9_*{}<>./+-]"
_WEND = r"[A-Za-z0-9_*}>/]"
PATH = re.compile(
    rf"(?:(?:{'|'.join(_DIRS)})/(?:{_W}*{_WEND})?"
    rf"|(?:{_W}*[A-Za-z0-9_]\.(?:{'|'.join(_SRC)}))\b)"
    rf"(?:::{_W}*{_WEND})?")
#: A folder in front of an artifact's name -- the tree's, or a scratch one a command wrote
#: it to -- is dropped with the same rule: the name is the reader's, the folder is not.
ARTIFACT = re.compile(rf"(?:(?:{'|'.join((*_DIRS, 'out', 'build'))})/)+({_W}*\.(?:{'|'.join(_ARTIFACT)}))\b")
#: A command line that runs something out of the tree (as opposed to the tool itself).
SCRIPT_CMD = re.compile(rf"^\s*(?:python\s+)?(?:{'|'.join(_DIRS)})/\S+|qccdc_cli|qcheck\.exe|_build/default")


def _is_path(tok: str) -> bool:
    return bool(PATH.fullmatch(tok.strip("`.,;:()[]")))


# --------------------------------------------------------------------------- 1. whole sections

#: ``document stem -> heading titles`` whose section is dropped.  Matching is on the
#: heading's text, lowercased, with backticks and punctuation removed.
DROP_SECTIONS: dict[str, tuple[str, ...]] = {
    # the provenance queries and the note about which directories are gitignored: true of
    # a clone, meaningless to a reader here
    "phys": ("where these came from",),
}


# --------------------------------------------------------------------------- 2. by hand

#: Exact replacements applied to the markdown before anything else.  Each one carries a
#: statement that survives the loss of the file name, so the sentence is rewritten rather
#: than cut.  A replacement of ``""`` deletes the text.
REWRITES: tuple[tuple[str, str], ...] = (
    # --- headings that are a path
    ("# `qccd/phys/` — the electrodes a device implies",
     "# The electrodes a device implies"),
    ("# `.arch.json` — the architecture description language",
     "# The architecture description language"),
    # --- adl
    ("See `docs/rules.md` for why this reading of the deck's",
     "See the rules reference for why this reading of the deck's"),
    ("The studio (`python -m qccd studio`) has two design modes",
     "The studio has two design modes"),
    # --- rules
    ("the statements are those in\n`Knowledge/notes/constraints.yaml` (`python Knowledge/kg/query.py rules`).",
     "the statements are those of the constraint notes this model is built from."),
    ("is also exercised in\n`tests/test_rules.py` against a program built to break it.",
     "is also exercised against a program built to break it."),
    ("`geometry_violations(arch)` (`qccd/verify/rules.py`) files one violation",
     "`geometry_violations(arch)` files one violation"),
    ("the browser mirrors them (`engine.js::geometryViolations`, a sibling of",
     "the browser mirrors them (`geometryViolations`, a sibling of"),
    ("## Cooling insertion\n\n`qccd/compile/cooling.py`. Cooling is global",
     "## Cooling insertion\n\nCooling is global"),
    ("all of them are now regression-tested in\n`tests/test_review_regressions.py`, named for the defect rather than for the fix.",
     "all of them are now regression-tested, each test named for the defect rather than for the fix."),
    # --- tsir
    ("the schedule. `tests/test_golden_24ancilla.py::test_corner_hops_is_the_only_geometry_change_between_the_models`\nasserts it:",
     "the schedule. One test asserts it, and says so in its name:"),
    # --- adl
    ("emits its statements in it, and `viz/layout.py`'s\n`_bows` sums a centroid",
     "emits its statements in it, and the layout's\n`_bows` sums a centroid"),
    ("the\n  step `qccd/viz/layout.py` measured, or 1 on an empty canvas",
     "the\n  step the layout measured, or 1 on an empty canvas"),
    ("declares a loop (`qccd/arch/edit.py`'s op table is `add_site`, `add_junction`,",
     "declares a loop (the edit op table is `add_site`, `add_junction`,"),
    # --- phys
    ("They are first-class fields of the technology — `qccd/phys/tech.py:TECH_RULES` —\nchecked when a file loads",
     "They are first-class fields of the technology, checked when a file loads"),
    ("is recorded in `Library/non_arxiv_ledger.csv`)", "is recorded in a ledger of non-arXiv sources)"),
    ("`tech.py` at load", "when the technology loads"),
    ("**DRC `rf_dc_clearance`**; `build.py`'s `dc_setback`", "**DRC `rf_dc_clearance`**; the layout's `dc_setback`"),
    ("What it is deliberately not: no `engine.js` edit", "What it is deliberately not: no browser edit"),
    ("Those are mirrored in `engine.js` and diffed", "Those are mirrored in the browser engine and diffed"),
    ("`shapes.union_by_net` is exact for rectangles only", "The union by net is exact for rectangles only"),
    ("in `drc.py` goes through that union", "in the design-rule check goes through that union"),
    ("polygon corner electrodes are recorded as the intended follow-up in\n`build.py`'s module docstring.",
     "polygon corner electrodes are recorded as the intended follow-up."),
    ("Each is pinned by a test and recorded in\n`Knowledge/notes/accumulated.yaml`.", "Each is pinned by a test."),
    ("its end-cap docks, which `docs/adl.md` already\nrecords.",
     "its end-cap docks, which the architecture reference already records."),
    ("Reported, never repaired: `arch/` is not this layer's to edit",
     "Reported, never repaired: the architecture is not this layer's to edit"),
    ("`render.py` never imports `qccd.phys`: the metal", "The renderer never imports this layer: the metal"),
    ("`qccd/cost/hardware.py` prices `electrodes_per_trap = 24`", "The hardware price list carries `electrodes_per_trap = 24`"),
    ("`shapes.py` and `tech.py` contain **no float literal**",
     "The shape table and the technology contain **no float literal**"),
    ("Placement is quarter-turns\nthrough `qccd/arch/component.py::translate_point`; four of them",
     "Placement is quarter-turns through one point transform; four of them"),
    ("so it is not written twice, and `tests/test_field.py` asserts that no\nsymbol of it reaches `engine.js`, `edit.js` or `editor.js`.",
     "so it is not written twice, and a test asserts that no symbol of it reaches the browser."),
    ("The offset costs nothing in the transport model; `Codesign/scripts/q06_campaign.py",
     "The offset costs nothing in the transport model; the dock sweep"),
    # --- gadgets
    ("reporting contract (`docs/rules.md`: *passed* only if the check ran):",
     "reporting contract (*passed* only if the check ran):"),
    ("the BB [[72,12,6]] round from `qccd/compile` ends", "the BB [[72,12,6]] round from the compiler ends"),
    ("disagree about what a program means (`qccd/gadget/codes.py`, `gf2.py`, tested against the",
     "disagree about what a program means (tested against the"),
    ("`synthesize(program, library, leaves) → GIR` (`qccd/gadget/synth.py`), deterministic:",
     "`synthesize(program, library, leaves) → GIR`, deterministic:"),
    ("`qccd/gadget/checks.py`, reported as the rule report is:", "the schedule checks, reported as the rule report is:"),
    ("as logic (below). Code: `qccd/gadget/{categories,surface,town,city,place_checks,verified,\nsvg}.py`, `leaves/{bay,places}.py`, `logic/`.",
     "as logic (below)."),
    ("`logic/` is pure Python, like the rest of the site build.",
     "The logical half is pure Python, like the rest of the site build."),
    ("Cross-checks (`tests/test_gadget_logic.py`,\n`test_gadget_places.py`): the tableau agrees",
     "Cross-checks: the tableau agrees"),
    ("**The checks.** `place_checks.py` re-derives G1–G9 for the place model",
     "**The checks.** The place checks re-derive G1–G9 for the place model"),
    ("dashed and dark, because a bit is not an ion. Code: `leaves/classical.py`,\n"
     "`logic/decode.py`, the classical parts of `model.py`, `town.py`, `city.py`,\n"
     "`place_checks.py`, `logic/flat.py`; the latencies and the decoder profiles come from\n"
     "`qccd/analysis/feedback.py`, which the studio and the leaderboard read too.",
     "dashed and dark, because a bit is not an ion. The latencies and the decoder profiles\n"
     "come from one table, which the studio and the leaderboard read too."),
    ("it is checked on the state itself\n(`tests/test_gadget_classical.py::test_the_t_gadget_correction_table_is_right`), while the",
     "it is checked on the state itself, while the"),
    ("  `qccd/site/build.py` (`qccd/site/qec_cycle.py::studio_block`), so the studio's own",
     "  the site build, so the studio's own"),
    ("Tests: `tests/test_gadget_frontend.py` (23), `test_gadget_schedule.py` (25, including a\n"
     "mutation for every G-rule), `test_gadget_page.py` (3: the page's JS replay of every leaf\n"
     "program ends on exactly the Python verifier's positions), `test_gadget_browser.py` (7:\n"
     "headless Chrome, every level, the editor round trip, a verified algorithm page, all\n"
     "thirteen silhouettes), `test_gadget_logic.py` (12), `test_gadget_places.py` (10),\n"
     "`test_gadget_algorithms.py` (18), `test_gadget_classical.py` (22: the decoder's table, a\n"
     "wire that refuses ions, causality and backlog, the frames the archive holds, the S̄ gadget,\n"
     "guards and their delivery, a sign-off per branch, and the mutations of a conditional\n"
     "correction that must fail) — 120 tests, about four minutes cold.",
     "Tests, 120 of them and about four minutes cold: the front end (23), the schedule (25,\n"
     "including a mutation for every G-rule), the page (3: its replay of every leaf program in\n"
     "the browser ends on exactly the verifier's positions), the browser (7: headless Chrome,\n"
     "every level, the editor round trip, a verified algorithm page, all thirteen silhouettes),\n"
     "the logic (12), the places (10), the algorithms (18), and the classical half (22: the\n"
     "decoder's table, a wire that refuses ions, causality and backlog, the frames the archive\n"
     "holds, the S̄ gadget, guards and their delivery, a sign-off per branch, and the mutations\n"
     "of a conditional correction that must fail)."),
    ("Each rule is also broken on purpose in `tests/test_gadget_schedule.py`.",
     "Each rule is also broken on purpose by a test that must fail."),
    ("`logic/flat.py` is the LVS that §9 of the plan promised", "The flattening is the LVS that §9 of the plan promised"),
    ("**The town** (`town.py`) is the design they run on", "**The town** is the design they run on"),
    ("(`city.py`) runs instructions one after another", "runs instructions one after another"),
    ("**The checks.** `place_checks.py` re-derives G1–G9", "**The checks.** The place model re-derives G1–G9"),
    ("**The bay.** Every ring place is one device family (`leaves/bay.py`):",
     "**The bay.** Every ring place is one device family:"),
    ("own silhouette, defined once (`categories.py`) and drawn identically", "own silhouette, defined once and drawn identically"),
    ("- **Stabilizer flows** (`spec.py`, `tableau.py`). A flow", "- **Stabilizer flows.** A flow"),
    ("- **Fault distance** (`experiment.py`, `faults.py`). The op sits", "- **Fault distance.** The op sits"),
    ("- **The factory** (`statevec.py`): a sparse state vector", "- **The factory**: a sparse state vector"),
    ("A program over blocks (`algorithm.py`), one instruction per line:", "A program over blocks, one instruction per line:"),
    ("**Latencies**, each from a measured system (`logic/decode.py::LATENCY`): 5 µs",
     "**Latencies**, each from a measured system: 5 µs"),
    ("**The decoder is verified, not asserted.** `logic/decode.py::lookup_table` builds its",
     "**The decoder is verified, not asserted.** The lookup table builds its"),
    ("`city.py` propagates them:", "The scheduler propagates them:"),
    ("so `logic/flat.py` signs it off once per branch:", "so the flattening signs it off once per branch:"),
    ("**The control floor.** `town.py` puts the decoder", "**The control floor.** The town puts the decoder"),
    ("one table (`qccd/analysis/feedback.py`: `LINK`, `DECODERS`, `cycle_report`, and the `cycle`",
     "one table (`LINK`, `DECODERS`, `cycle_report`, and the `cycle`"),
    ("`qccd/viz/theme.py`'s `PALETTE` and `GEOMETRY` and reproduces `qccd/viz/render.py`'s",
     "the studio's own `PALETTE` and `GEOMETRY` and reproduces its"),
    ("Change the palette in `theme.py` and both tools move together.",
     "Change the palette in one place and both tools move together."),
    ("- `n` and `k` from GF(2) rank; BB matrices as `ChainQ/BBCode/Basic.lean::Internal.bb`;",
     "- `n` and `k` from GF(2) rank; BB matrices as the Lean development defines them;"),
    ("(`leaves/factory.py::ring_circuit`) in which every gate leaves the ring",
     "in which every gate leaves the ring"),
    # --- a command whose argument is a file in the tree: the command stays, the path goes
    ("python -m qccd.gadget algorithm examples/gadgets/algorithms/teleport.alg   # schedule + sign-off",
     "python -m qccd.gadget algorithm PROGRAM.alg                 # schedule + sign-off"),
)


# --------------------------------------------------------------------------- 3. automatic

def _drop_sections(src: str, stem: str) -> str:
    titles = DROP_SECTIONS.get(stem, ())
    if not titles:
        return src
    want = {re.sub(r"[^a-z0-9 ]", "", t.lower()).strip() for t in titles}
    out, skip_to = [], 0
    for line in src.splitlines(keepends=True):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if m:
            level, text = len(m.group(1)), re.sub(r"[^a-z0-9 ]", "", m.group(2).lower()).strip()
            if skip_to and level <= skip_to:
                skip_to = 0
            if not skip_to and text in want:
                skip_to = level
                continue
        if not skip_to:
            out.append(line)
    return "".join(out)


def _block_is_source_map(lines: list[str]) -> bool:
    """A fenced block that lists where the code lives: most of its lines open with a path."""
    body = [x for x in lines if x.strip()]
    if not body:
        return False
    named = sum(1 for x in body if PATH.match(x.strip()) or SCRIPT_CMD.match(x))
    return named >= max(1, len(body) // 2)


def _fences(src: str):
    """Yield ``(is_fence, lines)`` runs, so a code block is handled as a unit."""
    run: list[str] = []
    fence = False
    for line in src.splitlines():
        if line.lstrip().startswith("```"):
            if fence:
                run.append(line)
                yield True, run
                run, fence = [], False
            else:
                if run:
                    yield False, run
                run, fence = [line], True
            continue
        run.append(line)
    if run:
        yield fence, run


_TIDY = ((r"\(\s*[,;]?\s*\)", ""), (r"\[\s*\]", ""), (r"\(\s*(?:and|or|see|in|at)\s*\)", ""),
         (r"\s+([,.;:])", r"\1"), (r"([,;:])\s*\1", r"\1"), (r",\s*\)", ")"), (r"\(\s*,", "("),
         (r"\s{2,}", " "), (r"^\s*[,;]\s*", ""))


def _tidy(text: str) -> str:
    for pat, rep in _TIDY:
        text = re.sub(pat, rep, text)
    return text.rstrip()


def _strip_line(line: str) -> str:
    """Remove path citations from one line of prose, keeping what it said."""
    line = ARTIFACT.sub(r"\1", line)                       # arch/x.arch.json -> x.arch.json
    if not PATH.search(line):
        return line

    def paren(m: re.Match) -> str:
        inner = m.group(1)
        rest = PATH.sub("", inner.replace("`", ""))
        rest = re.sub(r"[\s,;:§0-9.·and\-]+", "", rest)
        return "" if not rest else m.group(0)              # a parenthesis of citations only

    prev = None
    while prev != line:                                    # nested spans resolve by repetition
        prev = line
        line = re.sub(r"\(([^()]*)\)", paren, line)
    line = re.sub(r"`([^`]*)`", lambda m: "" if _is_path(m.group(1)) else m.group(0), line)
    line = PATH.sub("", line)
    return _tidy(line)


def strip(src: str, stem: str = "") -> str:
    """The document as the website shows it: same prose, no repository in it."""
    for a, b in REWRITES:
        src = src.replace(a, b)
    src = _drop_sections(src, stem)
    out: list[str] = []
    for is_fence, lines in _fences(src):
        if is_fence:
            body = lines[1:-1] if len(lines) > 1 and lines[-1].lstrip().startswith("```") else lines[1:]
            if _block_is_source_map(body):
                continue                                   # a block that is only file names
            keep = [x for x in body if not SCRIPT_CMD.match(x)]
            keep = [ARTIFACT.sub(r"\1", x) for x in keep]
            if not [x for x in keep if x.strip()]:
                continue
            out.append(lines[0])
            out.extend(keep)
            if len(lines) > 1 and lines[-1].lstrip().startswith("```"):
                out.append(lines[-1])
            continue
        for line in lines:
            got = _strip_line(line)
            if line.strip() and not got.strip():
                continue                                   # the line was the citation
            out.append(got)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- the gate

def audit(text: str) -> list[str]:
    """Every repository path still visible in a rendered page, as the scanner sees it."""
    import html as _html
    t = re.sub(r"<script\b.*?</script>", " ", text, flags=re.S | re.I)
    t = re.sub(r"<style\b.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return sorted({m.group(0) for m in PATH.finditer(t)})
