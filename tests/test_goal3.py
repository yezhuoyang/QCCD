"""Goal 3 (goals/goal3/README.md): one code at a time, every architecture.

* its six releases are the codes' Z-then-X memories, exactly what goals/goal3/boards.py builds,
  and they stay off Goal 1's ranking, which keeps every release that names no goal;
* every family's device is read back as that family (the official board sorts entries by it);
* the official board gives each entry its family, its time per round over both memories' rounds
  and its logical error rate per round;
* the site's Leaderboard keeps the Goal 3 boards off its Goal 1 table and memory-board cards, and
  board/goal3/ carries every code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "goals" / "goal3"
for p in (ROOT, GOAL):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
pytest.importorskip("stim")

from qccd.official.service import OfficialService  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR, find_release, list_boards  # noqa: E402

CODES = ("surface3", "surface5", "surface7", "color3", "color5", "color7")


@pytest.fixture
def svc(tmp_path):
    return OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")


def test_goal_3_releases_are_the_codes_z_then_x_memories():
    import boards
    for code in CODES:
        rel = find_release(f"goal3_{code}_mem@1")
        exp = boards.experiment(code)
        assert rel.manifest["goal"] == "goal3" and rel.manifest["qec"]["noise"] == "qccd-noise@2"
        assert rel.circuit_text() == exp.qasm, f"{code}: the release is not what boards.py builds"
        spec = rel.detectors()
        assert (spec["basis"], spec["rounds"], spec["code"]) == ("XZ", exp.rounds, exp.code)
        assert rel.manifest["qec"]["n_ions"] == exp.n_data + exp.n_ancilla
        assert "time_limit_s" not in rel.manifest["qec"]["ler"]     # a time limit is machine-dependent


def test_goal_3_releases_stay_off_goal_1(svc):
    g1 = {r.id for r in svc.goal_releases("goal1")}
    g3 = {r.id for r in svc.goal_releases("goal3")}
    assert g3 == {f"goal3_{c}_mem@1" for c in CODES} and not g1 & g3
    # Goal 1 is every noise@2 memory that names no goal, as before Goal 3 existed
    assert g1 == {r.id for r in svc.releases.values() if not svc.is_suite(r.id)
                  and (r.manifest.get("qec") or {}).get("noise") == "qccd-noise@2" and "goal" not in r.manifest}
    assert "surface5xz_mem@1" in g1


def test_every_family_s_device_is_read_back_as_that_family():
    import families
    import sweep
    rel = find_release("goal3_surface3_mem@1")
    meta = json.loads((GOAL / "codes" / "surface3_z" / "meta.json").read_text(encoding="utf-8"))
    for fam in families.FAMILIES:
        for p in families.points_for(fam, meta)[:2]:
            assert OfficialService.architecture_family(sweep.device_doc(p, rel)) == fam, p["name"]
    assert OfficialService.architecture_family({"geometry": {"generator": "chain"}}) == "Other"
    assert OfficialService.architecture_family({"nodes": []}) == "Other"


def test_the_goal_3_board_gives_family_time_per_round_and_ler(svc, monkeypatch):
    def rows(task):
        return ([{"id": f"os_{task[:14]}", "display_name": "d", "by": "Ada", "created_at": 0, "rank_value": 2e-4,
                  "metrics": {"T_quantinuum": 120.0, "ler_per_round": 2e-4},
                  "ler": {"per_round": 2e-4, "per_round_lo": 1e-4, "per_round_hi": 3e-4}}], {})
    monkeypatch.setattr(svc, "_ranked_rows", rows)
    monkeypatch.setattr(svc, "_entry_family", lambda sid: ("Conveyor", "conveyor24_12d"))
    g = svc.goal_board("goal3")
    assert g["goal"] == "goal3" and g["families"][:5] == ["Grid", "WISE", "C2LR", "Cyclone", "Conveyor"]
    assert [r["id"] for r in g["releases"]] == [f"goal3_{c}_mem@1" for c in CODES]
    s5 = next(r for r in g["releases"] if r["id"] == "goal3_surface5_mem@1")
    assert s5["rounds"] == 10 and s5["ions"] == 49          # five rounds of Z, then five of X
    e = s5["entries"][0]
    assert e["family"] == "Conveyor" and e["device"] == "conveyor24_12d" and e["by"] == "Ada"
    assert e["T_round_ms"] == pytest.approx(12.0) and e["ler_per_round"] == 2e-4


def test_the_site_keeps_goal_3_boards_together():
    lb = pytest.importorskip("qccd.site.boards", reason="the site package is not in this tree")
    from qccd.site import goal3, leaderboard
    mine = [b for b in list_boards() if b.manifest.get("goal") == "goal3"]
    assert len(mine) == len(CODES)
    assert not any(lb._goal1(b) for b in mine) and all(lb._goal3(b) for b in mine)
    g1 = leaderboard.goal_info("goal1")
    assert g1 is not None and not any(t.startswith("goal3_") for t, _, _ in g1["boards"])
    assert any(k == "Goal 3" and href == "goal3/" for k, _, _, href, _ in lb.GOALS)
    d = goal3.data()
    assert [c["task"] for c in d["codes"]] == [f"goal3_{c}_mem" for c in CODES]
    from qccd.site import build
    page = goal3.page(build.PAGE, build.STYLE)
    assert "Goal 3: one code at a time" in page and "/official/v1" in page
    for c in CODES:
        assert f'data-task="goal3_{c}_mem"' in page
