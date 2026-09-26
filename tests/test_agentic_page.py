"""The website's Agentic Design page stays true to the code it describes.

The page is generated from the workspace's own tables at build time; this test makes a tool,
operation, page action or reference section added WITHOUT its line on the page a failure, keeps
the page free of repository paths (the site's rule), and keeps the measured runs free of the ids
and folders a person never sees.
"""

from __future__ import annotations

import json
import re

import pytest

agentic = pytest.importorskip("qccd.site.agentic", reason="the site package is not in this tree")
from qccd.site import prose  # noqa: E402
from qccd.site.build import PAGE, STYLE  # noqa: E402
from qccd.workspace.mcp_server import _tools  # noqa: E402
from qccd.workspace.operations import describe_operations  # noqa: E402
from qccd.workspace.reference import SECTIONS  # noqa: E402
from qccd.workspace.service import PAGE_ACTIONS  # noqa: E402


@pytest.fixture(scope="module")
def page() -> str:
    return agentic.page(PAGE, STYLE)


def test_every_declared_thing_has_its_line(page):
    assert set(PAGE_ACTIONS) == set(agentic.PAGE_ACTION_ABOUT), "a page action without a description (or a stale one)"
    assert set(SECTIONS) == set(agentic.SECTION_ABOUT), "a reference section without a description (or a stale one)"
    for name, _, _ in _tools():
        assert f"<code>{name}</code>" in page, f"the tool {name} is missing from the page"
    for op in describe_operations():
        if not op["browser_only"]:
            assert f"<code>{op['type']}</code>" in page, f"the operation {op['type']} is missing"
    assert len(re.findall(r'<h2 id="', page)) >= 10


def test_the_page_shows_no_repository_path(page):
    assert prose.audit(page) == []


def test_the_measured_runs_carry_no_internal_ids_or_local_folders():
    meas = json.loads(agentic.MEASURED.read_text(encoding="utf-8"))
    s = json.dumps(meas, ensure_ascii=False)
    assert not re.search(r"cand/d[0-9a-f]{5,}|\bp_[0-9a-f]{12}\b|[A-Za-z]:\\\\Users|/home/|/Users/", s), \
        re.search(r"cand/d[0-9a-f]{5,}|\bp_[0-9a-f]{12}\b|[A-Za-z]:\\\\Users|/home/|/Users/", s).group(0)
    runs = meas["runs"]
    assert len(runs) >= 2 and all(r["wall_ms"] > 0 and r["model_calls"] > 0 for r in runs)
    ex = meas["excerpt"]["instructions"]
    assert ex[0]["op"] == "recv" and ex[-1]["op"] == "end"
    assert any(x["op"] == "call" and x.get("tool") == "qccd_submit_local" for x in ex)
