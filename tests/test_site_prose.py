"""The website never shows a path into the repository it was built from.

The reference pages are rendered from the project's own engineering documents, which name
source files freely; `qccd.site.prose` is the seam that takes them out.  These tests hold
that seam: what a path is, that the strip leaves the prose standing, and that the pages
the build actually writes carry none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The website itself lives on its own branch, so a checkout of this one may carry no
#: `qccd.site` at all; then there are no pages to hold and nothing to test.
prose = pytest.importorskip("qccd.site.prose", reason="the site package is not in this tree")

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "docs" / f"{n}.md" for n in ("adl", "tsir", "rules", "phys")]
DOCS += [ROOT / "docs" / "GADGETS.md", ROOT / "qccd" / "site" / "physics.md"]


def test_a_path_is_recognised_and_a_sentence_is_not():
    for path in ("qccd/verify/rules.py", "logic/decode.py", "tests/test_rules.py",
                 "engine.js", "Compiler/lean/QCCDC/Pulse/Decompose.lean", "tech.py",
                 "Knowledge/notes/constraints.yaml", "qccd/gadget/"):
        assert prose.PATH.search(path), path
    for text in ("the verifier replays every cycle", "R4d (drivability)", "5 µs to control",
                 "arXiv:2101.11390", "a ring of 144 sites", "0.83 s"):
        assert not prose.PATH.search(text), text


def test_an_artifact_keeps_its_name_and_loses_its_folder():
    got = prose.strip('arch = load("arch/ring144_24v.arch.json")')
    assert "ring144_24v.arch.json" in got
    assert "arch/ring144_24v" not in got


def test_a_call_keeps_its_parentheses_where_a_citation_loses_its_own():
    got = prose.strip("Rule H2 (`plane_conflicts() = 0`) holds (`qccd/gadget/planes.py`), "
                      "as `road_ok()` does (`qccd/gadget/roads.py`).")
    assert "`plane_conflicts() = 0`" in got and "`road_ok()`" in got
    assert "qccd/" not in got and "planes.py" not in got


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_rendered_document_loses_the_repository(doc: Path):
    got = prose.strip(doc.read_text(encoding="utf-8"), doc.stem)
    assert prose.audit(got) == []


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_the_prose_survives_the_strip(doc: Path):
    """A strip that ate the document would also pass the audit: hold the size, and hold
    the shape -- no dangling parenthesis, no doubled comma, no empty code span."""
    src = doc.read_text(encoding="utf-8")
    got = prose.strip(src, doc.stem)
    assert len(got) > 0.85 * len(src), "the strip removed more than a seventh of the text"
    for i, line in enumerate(got.splitlines(), 1):
        if line.lstrip().startswith("```") or line.startswith("|"):
            continue
        for damage in ("``", ",,", "( ,", "()."):
            assert damage not in line, f"{doc.name}:{i}: {line}"


def test_the_documents_themselves_are_untouched():
    """The strip happens on the way to the page; a clone still reads its own paths."""
    assert "qccd/arch/schema.py" in (ROOT / "docs" / "adl.md").read_text(encoding="utf-8")


def test_audit_sees_through_markup_and_ignores_scripts():
    assert prose.audit("<p>the checks live in <code>qccd/verify/rules.py</code></p>") == ["qccd/verify/rules.py"]
    assert prose.audit("<script>// qccd/verify/rules.py</script>") == []


#: An absolute path from the machine that built the site, in a published record's own
#: spelling or JSON-escaped (two backslashes): a drive letter and a Users folder, or a home.
ABSOLUTE = r"[A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)|/home/[^\"/]+/"


def test_a_board_record_is_published_without_the_paths_of_the_machine_that_built_it():
    r"""The leaderboard publishes each task's rows as `board/<task>/manifest.json` (and
    `rows.json`).  A row names its artifacts -- `arch`, `prefix`, `qasm` -- by where they were
    on the disk that compiled them, `C:\Users\<name>\...`; the published copy names the file
    and nothing above it."""
    import json
    import re
    build = pytest.importorskip("qccd.site.build", reason="the site package is not in this tree")
    row = {"key": "01_ring", "page": "01_ring.html", "numbers": {"T_jones": 1.5},
           "arch": r"C:\Users\someone\QCCD\arch\ring144_24v.arch.json",
           "prefix": "/home/someone/QCCD/SmallCode/steane/chain_steane",
           "qasm": ROOT / "SmallCode" / "steane" / "steane.qasm",
           "note": "arch/ring144_24v.arch.json is the device"}
    got = build.public_paths([row])[0]
    assert (got["arch"], got["prefix"], got["qasm"]) == ("ring144_24v.arch.json", "chain_steane", "steane.qasm")
    assert {k: got[k] for k in ("key", "page", "numbers", "note")} == {k: row[k] for k in ("key", "page", "numbers", "note")}
    # the pattern sees a planted path, so its silence below means something
    assert re.search(ABSOLUTE, json.dumps(row, default=str))
    # a seed written since `qccd.repo_paths` names its files relative to the repository;
    # published, those are file names too
    rel = build.public_paths([{"arch": "arch/ring144_24v.arch.json", "cooled": "SmallCode/steane/x.cooled.tsir.json",
                               "note": "arch/ring144_24v.arch.json is the device"}])[0]
    assert rel == {"arch": "ring144_24v.arch.json", "cooled": "x.cooled.tsir.json",
                   "note": "arch/ring144_24v.arch.json is the device"}
    # and the records the build publishes today carry no path above a file name
    for t in build.tasks():
        pub = build.public_paths(t["rows"])
        assert not re.search(ABSOLUTE, json.dumps(pub, default=str)), t["id"]
        for r in pub:
            for k in ("arch", "prefix", "qasm", "cooled"):
                assert not re.search(r"[\\/]", str(r.get(k) or "")), (t["id"], k, r.get(k))
