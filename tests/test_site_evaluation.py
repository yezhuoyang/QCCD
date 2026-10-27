"""The Evaluation page defines everything the rest of the site links to it.

Every studio page's Report links each metric to `/evaluation/#m-<name>` and each budget line
to `/evaluation/#c-<channel>`, the leaderboard links each measure the same way, and the page's
own highlighted texts link every param, define and metric to its declaration.  A link that
lands nowhere is a definition the reader was promised and not given: the Goal 1 table linked
three size metrics that had no card, and eleven params and defines had no anchor.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("qccd.site.evaluation", reason="the site package is not in this tree")


@pytest.fixture(scope="module")
def page_html() -> str:
    from qccd.site import build as B
    from qccd.site.evaluation import page
    return page(B.PAGE, B.STYLE, device_svg=B.device_svg)


def test_every_metric_and_channel_a_page_can_show_has_a_card(page_html):
    from qccd.evalset import for_page

    ids = set(re.findall(r'\bid="([^"]+)"', page_html))
    for clock in ("quantinuum", "qccdsim_jones"):
        ev = for_page(clock)
        assert not [m.name for m in ev.metrics_ if f"m-{m.name}" not in ids], clock
        assert not [c.name for c in ev.channels if f"c-{c.name}" not in ids], clock


def test_no_link_on_the_page_lands_nowhere(page_html):
    ids = re.findall(r'\bid="([^"]+)"', page_html)
    assert not sorted({x for x in ids if ids.count(x) > 1}), "an id is used twice"
    missing = sorted(set(re.findall(r'href="#([^"]+)"', page_html)) - set(ids))
    assert not missing, missing


def test_the_leaderboard_links_only_to_definitions_that_exist(page_html):
    from qccd.site import leaderboard

    block = re.search(r"var DEF = \{(.*?)\};", open(leaderboard.__file__, encoding="utf-8").read(), re.S)
    assert block, "the leaderboard's measure -> metric table"
    names = re.findall(r":\s*'([A-Za-z0-9_]+)'", block.group(1))
    ids = set(re.findall(r'\bid="([^"]+)"', page_html))
    assert names and not [n for n in names if f"m-{n}" not in ids]
