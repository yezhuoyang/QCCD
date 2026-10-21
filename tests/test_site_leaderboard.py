"""The leaderboard's site side (qccd/site/leaderboard.py): a board's ranking page takes the
official entries into its chart, and every control it adds is declared.  The server half (the
page, files and bundle an entry opens) is tests/test_official.py."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.site import leaderboard

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "MemoryBoard" / "surface5xz_mem" / "index.html"


@pytest.mark.skipif(not PAGE.exists(), reason="the memory board's pages are not built")
def test_a_ranking_page_takes_the_official_entries_into_its_chart():
    src = PAGE.read_text(encoding="utf-8")
    out = leaderboard.with_submissions(src, "surface5xz_mem", "Surface code memory")
    # "Try your own design" sits right under the detail panel, before the disqualified list
    i_detail, i_try = out.index('id="detail"'), out.index('data-hint="board:try"')
    assert i_detail < i_try < out.index("</main>")
    if 'id="disqualified"' in out:
        assert i_try < out.index('id="disqualified"')
    assert '"id": "surface5xz_mem@1"' in out and "/official/v1" in out
    # the button's popover links the set-up steps from two levels down
    assert 'data-root="../../"' in out
    # every control the layer adds is declared (links are, by construction)
    for key in ("board:try", "board:try-close", "board:rank"):
        assert f'"{key}"' in out, key
    # a board the official server does not rank keeps its page as it is
    assert leaderboard.with_submissions(src, "no_such_board", "x") == src


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_layer_scripts_parse(tmp_path):
    js = [leaderboard.SCRIPT.replace("__BOARDS__", "{}").replace("__LOCAL__", '""').replace("__CHARTS__", "[]"),
          leaderboard.CHART_SCRIPT.replace("__BOARD__", "null").replace("__FILES__", "[]")]
    for i, s in enumerate(js):
        f = tmp_path / f"s{i}.js"
        f.write_text(s, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_every_bundle_file_the_page_offers_is_one_the_server_serves():
    from qccd.official.service import OfficialService
    assert {f for f, _ in leaderboard.BUNDLE_FILES} <= set(OfficialService.READABLE)
    assert not re.search(r"report", " ".join(f for f, _ in leaderboard.BUNDLE_FILES))


@pytest.mark.skipif(not PAGE.exists(), reason="the memory board's pages are not built")
def test_goal_1_is_one_chart_of_every_codes_official_entries():
    g = leaderboard.goal_info("goal1")
    assert g and any(t == "surface5xz_mem" for t, _, _ in g["boards"])
    out = leaderboard.goal_page(PAGE.read_text(encoding="utf-8"), "goal1")
    assert "var DATA = [];" in out                      # the study's designs are not entries
    assert 'id="disqualified"' not in out
    assert "<h1>Goal 1: The best QEC design under 200 ions" in out
    assert '"goal": "goal1"' in out and "/goals/" in out
    assert out.index('id="detail"') < out.index('data-hint="board:try"') < out.index("</main>")
