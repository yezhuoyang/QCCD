"""Goal 3 (goals/goal3/README.md): one code at a time, every architecture.

* its releases are the codes' Z-then-X memories, exactly what goals/goal3/boards.py builds,
  and they stay off Goal 1's ranking, which keeps every release that names no goal;
* the HGP builder (ours, goals/goal3/hgp_code.py) still builds the committed HGP circuits;
* every family's device is read back as that family (the official board sorts entries by it);
* the official board gives each entry its family, its time per round over both memories' rounds
  and its logical error rate per round;
* the site's Leaderboard keeps the Goal 3 boards off its Goal 1 table and memory-board cards, and
  board/goal3/ carries every code;
* board/goal3/'s table of rules judges each submission by its own rule set: every compiled point by
  its family's (goals/goal3/rules.json), and each column exactly that set's rules.
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

CODES = ("surface3", "surface5", "surface7", "color3", "color5", "color7", "bb72", "hgp58")


@pytest.fixture
def svc(tmp_path):
    return OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")


def test_goal_3_releases_are_the_codes_z_then_x_memories():
    import boards
    for code in CODES:
        rel = find_release(boards.release_id(code))
        exp = boards.experiment(code)
        assert rel.manifest["goal"] == "goal3" and rel.manifest["qec"]["noise"] == "qccd-noise@2"
        assert rel.circuit_text() == exp.qasm, f"{code}: the release is not what boards.py builds"
        spec = rel.detectors()
        assert (spec["basis"], spec["rounds"], spec["code"]) == ("XZ", exp.rounds, exp.code)
        assert rel.manifest["qec"]["n_ions"] == exp.n_data + exp.n_ancilla
        assert "time_limit_s" not in rel.manifest["qec"]["ler"]     # a time limit is machine-dependent


def test_the_hgp_builder_still_builds_the_committed_circuits():
    pytest.importorskip("qldpc")
    from qldpc.objects import Pauli

    import hgp_code
    for b, P in (("z", Pauli.Z), ("x", Pauli.X)):
        meta = json.loads((GOAL / "codes" / f"hgp58_{b}" / "meta.json").read_text(encoding="utf-8"))
        src = (GOAL / "codes" / f"hgp58_{b}" / "source.stim").read_text(encoding="utf-8")
        assert str(hgp_code.build_circuit(3, meta["rounds"], P)) + "\n" == src.replace("\r\n", "\n")


def test_goal_3_releases_stay_off_goal_1(svc):
    import boards
    g1 = {r.id for r in svc.goal_releases("goal1")}
    g3 = {r.id for r in svc.goal_releases("goal3")}
    # each code's newest release; an older one keeps its board but is not on the goal
    assert g3 == {boards.release_id(c) for c in CODES} and not g1 & g3
    assert "goal3_bb72_mem@1" in svc.releases and "goal3_bb72_mem@1" not in g3
    # Goal 1 is every noise@2 memory that names no goal, as before Goal 3 existed
    assert g1 == {r.id for r in svc.releases.values() if not svc.is_suite(r.id)
                  and (r.manifest.get("qec") or {}).get("noise") == "qccd-noise@2" and "goal" not in r.manifest}
    assert "surface5xz_mem@1" in g1


def test_every_family_s_device_is_read_back_as_that_family():
    import boards
    import families
    import sweep
    exp = boards.experiment("surface3")
    meta = json.loads((GOAL / "codes" / "surface3_z" / "meta.json").read_text(encoding="utf-8"))
    for fam in families.FAMILIES:
        for p in families.points_for(fam, meta)[:2]:
            assert OfficialService.architecture_family(sweep.device_doc(p, exp)) == fam, p["name"]
    assert OfficialService.architecture_family({"geometry": {"generator": "chain"}}) == "Other"
    assert OfficialService.architecture_family({"nodes": []}) == "Other"


def test_cyclone_s_rings_have_junction_corners():
    """Cyclone's rings are built as the site's rings are since 2026-11-01, and as the deck's HW2 says:
    four L-shaped junctions at the corners and x traps on the sides."""
    import families
    from qccd.arch import Architecture, cyclone
    for code in CODES:
        meta = json.loads((GOAL / "codes" / f"{code}_z" / "meta.json").read_text(encoding="utf-8"))
        for p in families.points_for("Cyclone", meta):
            dev = Architecture.from_json(cyclone.device_doc(p["params"]["x"], p["params"]["n"])).device
            assert len(dev.loop_slots("L0")) == p["params"]["x"], p["name"]
            bends = [n for n in dev.nodes if dev.nodes[n].kind == "junction"]
            assert len(bends) == 4 and all(dev.degree(n) == 2 for n in bends), p["name"]


def test_bb72_release_2_is_release_1_with_the_junction_starter():
    """A release is never rewritten: BB72's release 2 changes only the starter (its twin with
    junction corners) and the release number."""
    one, two = find_release("goal3_bb72_mem@1").manifest, find_release("goal3_bb72_mem@2").manifest
    same = {k for k in one if k not in ("release", "starter")}
    assert {k: one[k] for k in same} == {k: two[k] for k in same} and set(one) == set(two)
    assert one["starter"]["params"].get("corners") is None
    assert two["starter"]["params"]["corners"] == "junction"
    assert two["starter"]["params"]["width"] == one["starter"]["params"]["width"] + 2


def test_the_goal_3_board_gives_family_time_per_round_and_ler(svc, monkeypatch):
    def rows(task):
        return ([{"id": f"os_{task[:14]}", "display_name": "d", "by": "Ada", "created_at": 0, "rank_value": 2e-4,
                  "metrics": {"T_quantinuum": 120.0, "ler_per_round": 2e-4},
                  "ler": {"per_round": 2e-4, "per_round_lo": 1e-4, "per_round_hi": 3e-4}}], {})
    monkeypatch.setattr(svc, "_ranked_rows", rows)
    monkeypatch.setattr(svc, "_entry_family", lambda sid: ("Cyclone", "cyclone24_n2"))
    g = svc.goal_board("goal3")
    assert g["goal"] == "goal3" and g["families"] == ["C2LR", "WISE", "Cyclone", "Other"]
    import boards
    assert [r["id"] for r in g["releases"]] == [boards.release_id(c) for c in CODES]
    s5 = next(r for r in g["releases"] if r["id"] == "goal3_surface5_mem@1")
    assert s5["rounds"] == 10 and s5["ions"] == 49          # five rounds of Z, then five of X
    e = s5["entries"][0]
    assert e["family"] == "Cyclone" and e["device"] == "cyclone24_n2" and e["by"] == "Ada"
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


def test_board_goal3_judges_each_submission_by_its_own_rule_set():
    pytest.importorskip("qccd.site.boards", reason="the site package is not in this tree")
    import os

    import rules as judged_rules
    from families import RULES
    from qccd.ruleset import load
    from qccd.site import goal3
    judged = json.loads((GOAL / "rules.json").read_text(encoding="utf-8"))
    # every compiled point of every code, judged by its family's own set, and no other
    for code in sorted(p.stem for p in (GOAL / "results").glob("*.jsonl")):
        got = judged["codes"][code]
        want: dict = {}
        for r in judged_rules.compiled(code):
            want.setdefault(r["family"], set()).add(r["point"])
        assert {f: set(d["points"]) for f, d in got.items()} == want, code
        assert all(d["set"] == RULES[f] for f, d in got.items()), code
    assert {n: s["ids"] for n, s in judged["sets"].items()} == {n: load(n).ids for n in set(RULES.values())}
    # a point's work folder lives outside the repository; where one is at hand, judge it again
    roots = [Path(p) for p in os.environ.get("QCCD_GOAL3_WORK", "").split(os.pathsep) if p]
    rows = {(r["family"], r["point"]): r for r in judged_rules.compiled("surface3")}
    for fam in RULES:
        name = min(p for (f, p) in rows if f == fam)
        if any((r / "surface3" / name / "prog.cooled.tsir.json").is_file() for r in roots):
            v = judged_rules.judge("surface3", rows[(fam, name)], roots)
            mine = judged["codes"]["surface3"][fam]["points"][name]
            assert v["fails"] == mine.get("fails", {}) and sorted(v["other"]) == mine.get("skip", []), (fam, name)
    # the page: each family's column holds exactly its own set's rules, a ✗ exactly the sizes
    # rules.json says fail, and a rule no program can be judged by is marked so, not passed
    d = goal3.data()
    for c in d["codes"]:
        R, code = c["rules"], c["task"].removeprefix("goal3_").removesuffix("_mem")
        assert R["board"] == "standard"
        for fam, F in R["fams"].items():
            own, pts = load(RULES[fam]).ids, judged["codes"][code][fam]["points"]
            assert F["set"] == RULES[fam] and sorted(F["cells"]) == sorted(own), (code, fam)
            assert set(own) <= set(R["ids"])
            for rid in own:
                bad = sorted(p for p, v in pts.items() if rid in v.get("fails", {}))
                cell = F["cells"][rid]
                assert (sorted(cell[1]) if cell[0] == "bad" else []) == bad, (code, fam, rid)
            for rid in ("R7b", "R9", "R10", "R29"):
                if rid in own:
                    assert F["cells"][rid][0] == "skip", (code, fam, rid)
            assert ({i for i in own if i.startswith("C")} != set()) == (fam == "C2LR")
    assert set(d["rules"]["never"]) <= {"R7b", "R9", "R29"}
    # the compilers keep each qubit's gates in the circuit's order, and the transport stamped with a
    # gate (the race-track split after a layer) does not carry it out: no order rule fails anywhere
    assert not [(code, fam, p) for code, c in judged["codes"].items() for fam, d in c.items()
                for p, v in d["points"].items() if {"R25", "R26"} & set(v.get("fails", {}))]

