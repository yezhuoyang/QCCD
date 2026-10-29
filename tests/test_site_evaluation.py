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


def test_the_recomputed_entry_agrees_with_its_report(page_html):
    """Every number the page sets beside the official report equals it.  The error budget read
    the page's side by channel name (ms, readout_z) while the circuit books each channel under
    the lines its `counted as` names (ms_base + ms_heating + ms_chain, measure), and showed
    0 against 0.0201."""
    assert "the error budget" in page_html.lower()
    assert 'class="bad"' not in page_html


def test_a_frame_change_row_without_noise_is_explained(page_html):
    """A shown VZ step writes S gates and, today, no noise on its own ions (gate_1q skips a
    frame change, memory skips the ions a step operates on); a reader asked why.  Whenever such
    a row is shown, the paragraph under the table says why."""
    rows = re.findall(r'<td class="l">gate VZ [^<]*</td>.*?<pre class="stim">(.*?)</pre>', page_html, re.S)
    quiet = []
    for pre in rows:
        lines = [ln.split("←") for ln in pre.splitlines()]
        own = {q for txt, who in lines if who.strip() == "translation" for q in txt.split()[1:]}
        if not any(own & set(txt.split()[1:]) for txt, who in lines if who.strip() != "translation"):
            quiet.append(pre)
    para = re.search(r'<p class="words">Reading it:(.*?)</p>', page_html, re.S)
    assert para
    assert ("frame change <code>VZ</code>" in para.group(1)) == bool(quiet)
