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
