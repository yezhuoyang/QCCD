"""The leaderboard's entry pages must run the engine this repository tests.

An entry page is not rendered by the site build.  `qccd/site/build.py` reads it out of a
seed directory (`BBResults/studio`, `SmallCode/<code>`) and copies it VERBATIM, wrapping it
in nav and the QEC layer -- so the `__ENGINE__` substitution that `render.py` performs for
every page it renders never happens here.  Whatever `engine.js` was on disk the day
`bb_studio.py` wrote the page is the engine a reader runs, for as long as nobody
regenerates it.

That froze on 2026-09-15 and was found on 2026-09-17, by which time eleven of the sixty-nine
live entry pages could not load their editor at all:

    5  unknown generator 'cylinder' (have: chain, dual_loop, grid, ladder, racetrack, ring)
    5  ring() got an unexpected keyword argument 'dock_offset'
    1  grid() got an unexpected keyword argument 'spacing'

Python had all three parameters and the whole `cylinder` generator; the pages were running
an engine from before they existed.  One of the eleven was the board's flagged best answer,
which is the page a reader is most likely to open.

`test_engine_parity.py::test_every_emitted_page_carries_the_engine_verbatim` already makes
this assertion -- and it scans `ROOT/out` only, so the seed directories fell straight
through it.  This is that test pointed at the other place pages live.  The two together say
that every page anyone can open runs the tested engine, which is the property the comment
above `ENGINE_JS` in `render.py` claims and until now only half-held.

WHEN THIS FAILS, the fix is to regenerate the pages, not to edit them:

    python Codesign/scripts/bb_studio.py          # the bb144 board
    python Codesign/scripts/small_codes.py --pages-only

Both re-render from the already-compiled TSIR -- no recompile -- at about ten seconds a
page on an idle machine.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.site.build import tasks  # noqa: E402
from qccd.viz.render import page_stamp  # noqa: E402

VIZ = ROOT / "qccd" / "viz"


def _shipped_js() -> tuple[str, ...]:
    """Every file `render.py` inlines into a page, asked of `render.py`.

    Derived rather than retyped.  This list used to be a literal beside a comment saying
    that a file added to `ENGINE_JS` without being added here "shows up as a gap in this
    docstring" -- a note, and a note cannot fail.  `js/transit.js` was added on 2026-09-19
    and the literal in `test_engine_parity.py` was not updated with it, so that scan
    checked three of five and a stale `transit.js` would have passed.

    Derivation cannot see a file REMOVED from those tuples, which would narrow the check
    silently, so the floor below is the positive control: the four that have always been
    there must still be, and the count must not shrink.
    """
    from qccd.viz.render import EDITOR_JS, ENGINE_JS
    names = tuple(ENGINE_JS) + tuple(EDITOR_JS)
    core = {"engine.js", "js/edit.js", "js/editor.js", "js/tutorial.js"}
    missing = sorted(core - set(names))
    assert not missing, (
        f"{missing} no longer appear in render.ENGINE_JS/EDITOR_JS. If a file really was "
        f"dropped, drop it from `core` here in the same change -- otherwise this check has "
        f"just been narrowed without anyone deciding to narrow it.")
    assert len(names) >= 5, f"only {len(names)} inlined file(s): {names}"
    return names


SHIPPED_JS = _shipped_js()


def _entry_pages() -> list[tuple[str, Path]]:
    """Every entry page the website would publish, as (task id, path)."""
    out: list[tuple[str, Path]] = []
    for t in tasks():
        seed = t["seed_dir"]
        for r in t["rows"]:
            if r.get("status") != "ok" or not r.get("page"):
                continue
            p = seed / r["page"]
            if p.exists():
                out.append((t["id"], p))
    return out


@pytest.fixture(scope="module")
def pages() -> list[tuple[str, Path]]:
    found = _entry_pages()
    if not found:
        pytest.skip("no board seed pages on disk; run Codesign/scripts/bb_studio.py")
    return found


def test_every_entry_page_carries_the_shipped_javascript_verbatim(pages):
    """The bytes on disk, in the page, unmodified.

    Byte equality rather than a feature probe on purpose: a probe tests the one thing it
    was written for, and the failure mode here is a snapshot that is stale in ways nobody
    has thought of yet.  `cylinder` was missing for three months before anyone looked for
    it; the next gap will be something else.
    """
    src = {name: (VIZ / name).read_text(encoding="utf-8") for name in SHIPPED_JS}
    stale: dict[str, list[str]] = {}
    for task, p in pages:
        html = p.read_text(encoding="utf-8")
        missing = [name for name, text in src.items() if text not in html]
        if missing:
            stale.setdefault(", ".join(missing), []).append(f"{task}/{p.name}")
    assert not stale, (
        "entry pages carry a STALE copy of the shipped JavaScript -- a reader runs the "
        "frozen snapshot, not the tested file:\n"
        + "\n".join(f"  {what}: {len(who)} page(s), e.g. {who[0]}"
                    for what, who in sorted(stale.items()))
        + "\nRegenerate them: `python Codesign/scripts/bb_studio.py` and "
          "`python Codesign/scripts/small_codes.py --pages-only`.")


def test_the_scan_actually_found_the_board(pages):
    """Anti-vacuity: a `tasks()` that returned nothing would make the check above pass.

    The board is five tasks, and the smallest of them carries a dozen entries.
    """
    assert len(pages) >= 60, f"only {len(pages)} entry page(s) found; the scan is broken"
    ids = {task for task, _ in pages}
    assert len(ids) >= 5, f"only these tasks were scanned: {sorted(ids)}"


def test_every_entry_page_was_built_by_the_current_page_code(pages):
    """The PAGE SCRIPT, which is not a file and so cannot be compared byte for byte.

    `_TEMPLATE` in `render.py` is the page's own markup and JavaScript.  A reader-visible
    change to it -- the provenance footer stopped naming a path inside this repository on
    2026-09-17 -- reaches every page `render.py` renders and NONE of the entry pages, which
    the site build copies whole.  The test above would not notice: the four .js files can
    be perfectly current in a page whose surrounding script is months old.

    `render.page_stamp()` digests `_TEMPLATE` and those four files together and the page
    carries it in its head, so "built by code that is no longer the code" is one string
    comparison.
    """
    want = page_stamp()
    got: dict[str, list[str]] = {}
    # a regex rather than an offset into the needle: counting the needle's length by hand
    # got it wrong by one, which read every stamp as the empty string and reported all 69
    # pages as stale when they were current -- a check that fails for its own reasons is
    # worth no more than one that passes for them
    stamp_re = re.compile(r'name="qccd-page"\s+content="([^"]*)"')
    for task, p in pages:
        m = stamp_re.search(p.read_text(encoding="utf-8"))
        stamp = m.group(1) if m else "(none -- built before the stamp existed)"
        if stamp != want:
            got.setdefault(stamp, []).append(f"{task}/{p.name}")
    assert not got, (
        f"entry pages were built by page code that is no longer current (want {want}):\n"
        + "\n".join(f"  {s}: {len(who)} page(s), e.g. {who[0]}"
                    for s, who in sorted(got.items()))
        + "\nRegenerate them: `python Codesign/scripts/bb_studio.py` and "
          "`python Codesign/scripts/small_codes.py --pages-only`.")


#: What a repository path looks like in a page's PAYLOAD, as opposed to on its screen.
#: `qccd/site/prose.py` strips these from the reference documents and
#: `tests/_rendered_paths.mjs` catches them in rendered text; this is the third place they
#: can hide, which is the JSON the page carries and a reader reaches with view-source.
#: The separator is written as an alternation rather than a character class on purpose: a
#: `[/\\]` class does not survive a shell heredoc, which silently ate one backslash and
#: left `[/\]` -- an unterminated class that fails at import, not at the assertion.
PAYLOAD_PATHS = re.compile(r'"(?:file|root)":\s*"(?:[A-Za-z]:)?(?:\.{0,2}(?:/|\\\\))?'
                           r'(?:qccd|Compiler|Codesign|tests|tools|arch|examples)'
                           r'(?:/|\\\\)')


def test_no_entry_page_carries_a_repository_path_in_its_payload(pages):
    """The strip that the page stamp CANNOT see, checked directly.

    `render.page_stamp()` digests `_TEMPLATE` and the four inlined JS files, so it catches
    a stale page script or a stale engine.  It cannot catch a stale PAYLOAD: on 2026-09-18
    `qccd/ir/provenance.py::thin()` stopped exporting each site's `file`, `line` and `text`
    -- a path into a tree the reader does not have, and a line of this project's Python --
    and `provenance.py` is neither the template nor one of the four files.  A page built
    before that change and a page built after carry the SAME stamp.

    The lesson is not "widen the digest".  Hashing the rendered output cannot work, because
    the output is mostly the device and the programme, so every page would hash differently
    and there would be no single value to compare against; and hashing every module that
    contributes would go red on an unrelated docstring and demand a forty-minute
    regeneration, which is how a check gets switched off.

    So check the PROPERTY instead of the provenance of the bytes.  What was wanted was
    never "built by current code" in the abstract -- it was "carries no path into this
    repository", and that is one regex over the payload, with no churn and nothing to
    remember to add.
    """
    guilty: dict[str, list[str]] = {}
    for task, p in pages:
        for m in set(PAYLOAD_PATHS.findall(p.read_text(encoding="utf-8"))):
            guilty.setdefault(m, []).append(f"{task}/{p.name}")
        # findall with no group returns the match; keep the key readable either way
    assert not guilty, (
        "entry pages carry a repository path in their payload -- not on screen, but a "
        "reader finds it with view-source:\n"
        + "\n".join(f"  {what!r}: {len(who)} page(s), e.g. {who[0]}"
                    for what, who in sorted(guilty.items()))
        + "\nRegenerate them: `python Codesign/scripts/bb_studio.py` and "
          "`python Codesign/scripts/small_codes.py --pages-only`.")


def test_the_payload_scan_would_notice_a_path(pages):
    """A positive control: the regex must match what it exists to catch.

    Every other check today that could only ever say "nothing" said it for the wrong
    reason at least once.  This one is handed the exact string that was in 188 published
    pages, and must not be quiet about it.
    """
    planted = '"prov":{"sites":[{"file":"qccd/compile/cooling.py","line":350}]}'
    assert PAYLOAD_PATHS.search(planted), "the payload scan cannot see the string it is for"
    assert not PAYLOAD_PATHS.search('"file":"demo.lq"'), (
        "a bare artifact name is what the reader is looking at, not the tree")


def test_the_inlined_javascript_is_clean_text():
    """No stray control bytes in the files `render.py` pastes into every page.

    `transit.js` carried two literal NUL bytes for part of 2026-09-19, from an edit that
    wrote the character instead of the escape.  It parsed, every test passed, and it was
    found only because `grep` began reporting the file as binary.  Those bytes would have
    been inlined raw into all 69 entry pages and every rendered page beside them.

    Tab, newline and carriage return are the only control characters a source file has any
    business containing.  Checked at the source rather than in the output, so it fails
    before a page is built rather than after one is published.
    """
    ALLOWED = {0x09, 0x0A, 0x0D}
    dirty: list[str] = []
    for name in SHIPPED_JS:
        raw = (VIZ / name).read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            dirty.append(f"  {name}: not valid UTF-8 -- {exc}")
            continue
        seen = sorted({b for b in raw if b < 0x20 and b not in ALLOWED} |
                      {b for b in raw if b == 0x7F})
        if seen:
            at = next(i for i, b in enumerate(raw) if b in set(seen))
            line = raw[:at].count(b"\n") + 1
            dirty.append(f"  {name}: control byte(s) "
                         + ", ".join(f"0x{b:02x}" for b in seen)
                         + f", first at line {line}")
    assert not dirty, (
        "the JavaScript inlined into every page carries control characters, which are "
        "pasted in raw and are invisible in an editor:\n" + "\n".join(dirty))
