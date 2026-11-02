"""The design-review skill (.claude/skills/qccd-design-review): its rule references are the code's.

The skill's `references/` are generated from the rule sources (`sync_references.py`), so a rule,
rule set, board or grading policy that changes without them being regenerated fails here -- and in
`.github/workflows/skills.yml` -- instead of an agent reviewing designs against rules that no longer
exist.  The review script itself is run on a device that breaks a geometry rule and on a programme
that breaks a heat rule, so a review that cannot fail is caught as well.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / ".claude" / "skills" / "qccd-design-review"
COMPILED = REPO / "qccd" / "site" / "compiled"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"qccd_design_review_{name}", SKILL / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def generated():
    return _script("sync_references").generate(REPO)


def test_the_references_are_the_rules_as_the_code_has_them(generated):
    stale = [n for n, text in generated.items()
             if not (SKILL / "references" / n).is_file()
             or (SKILL / "references" / n).read_text(encoding="utf-8") != text]
    assert not stale, ("the skill's references are stale (" + ", ".join(stale) + "); run "
                       "python .claude/skills/qccd-design-review/scripts/sync_references.py --write")


def test_every_rule_and_every_board_is_in_the_references(generated):
    from qccd.ruleset import catalogue, load, names
    from qccd.workspace.tasks import list_boards
    rules_md, machine_md, boards_md = generated["rules.md"], generated["machine-rules.md"], generated["boards.md"]
    for r in catalogue().rules:
        assert f"### {r.id}: " in rules_md, r.id
        assert f"https://qccd.academy/rules/#{r.id}" in rules_md, r.id
    for n in names("machine"):
        for r in load(n).rules:
            assert f"### {r.id}: " in machine_md, r.id
    for b in list_boards():
        assert f"`{b.id}`" in boards_md, b.id


def test_skill_md_is_a_skill_and_points_at_every_reference():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md needs YAML frontmatter"
    fm = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)
    assert fm.get("name") == "qccd-design-review"
    assert 0 < len(fm.get("description", "")) <= 1024
    assert len(text.splitlines()) < 500
    for p in sorted((SKILL / "references").glob("*.md")):
        assert f"references/{p.name}" in text, f"SKILL.md never points at references/{p.name}"


def _sharp_corner(tmp_path: Path) -> Path:
    """grid2x3 with one trap pulled toward a neighbour: two rails at J0_1 meet at 18.4 degrees."""
    doc = json.loads((COMPILED / "grid2x3.arch.json").read_text(encoding="utf-8"))
    g = doc["geometry"]
    g["generator"], g["params"] = "explicit", {}
    for n in g["nodes"]:
        n.pop("degree", None)
        n.pop("corner", None)
        if n["id"] == "T0_1h":
            n["pos"] = [0.25, 1.75]
    for s in g["segments"]:
        s.pop("corner_endpoints", None)
    doc["name"] = "sharp_corner"
    p = tmp_path / "sharp_corner.arch.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _run(tmp_path: Path, *args: str) -> tuple[int, dict, str]:
    out = tmp_path / "r"
    code = _script("review").main([*args, "--out", str(out)])
    return (code, json.loads((out / "rule-check.json").read_text(encoding="utf-8")),
            (out / "rule-check.md").read_text(encoding="utf-8"))


def test_a_device_review_finds_a_sharp_corner(tmp_path):
    code, res, _ = _run(tmp_path, "--device", str(_sharp_corner(tmp_path)), "--board", "steane@1")
    rows = {r["id"]: r for r in res["rules"]}
    assert code == 1 and res["device_only"]
    assert rows["R20"]["review"] == "FAIL" and rows["R20"]["verify"]["status"] == "failed"
    assert rows["R20"]["language"]["verdict"] == "fails"
    assert rows["R21"]["review"] == "pass"
    # a programme rule is not judged on a device alone, by either checker
    assert rows["R1"]["review"] == "needs a programme" and rows["R1"]["language"] is None
    # the board's 13-qubit circuit in 14 slots: it fits, with one to spare
    assert res["fit"]["circuit_qubits"] == 13 and res["fit"]["spare_slots"] == 1


def test_a_programme_review_judges_every_rule_of_the_board(tmp_path):
    from qccd.ruleset import load
    bell = COMPILED / "bell"
    code, res, md = _run(tmp_path, "--device", str(COMPILED / "chain4.arch.json"),
                         "--program", str(bell / "prog.cooled.tsir.json"),
                         "--certificate", str(bell / "prog.qcert.json"), "--board", "ghz4@1")
    rows = {r["id"]: r for r in res["rules"]}
    assert list(rows) == load("standard").ids
    # cooled for the template's physics, the programme is too hot for the board's (R7)
    assert code == 1 and rows["R7"]["review"] == "FAIL"
    # the rules the board's rules stage never runs are judged in the rule language, with the circuit
    for rid in ("R0", "R24", "R25"):
        assert rows[rid]["verify"]["status"] is None and rows[rid]["language"]["verdict"] == "holds", rid
    assert rows["R16"]["review"] == "pass (vacuous)" and rows["R7b"]["review"] == "allowed skip"
    # a rule with nothing to judge says so: the Bell programme splits no chain
    assert rows["R14"]["review"] == "pass (nothing to check)" and rows["R14"]["cases"][0]["cases"] == 0
    # a Bell certificate is not about the GHZ board's circuit
    assert res["certificate"] == {"certificate_qubits": 2, "board_qubits": 4, "matches": False, "certificate_ops": 4}
    assert "[R20](https://qccd.academy/rules/#R20)" in md


def test_loose_files_are_graded_as_a_bundle_and_the_heat_fix_is_checked(tmp_path):
    bell = COMPILED / "bell"
    code, res, _ = _run(tmp_path, "--device", str(COMPILED / "chain4.arch.json"),
                        "--program", str(bell / "prog.cooled.tsir.json"),
                        "--certificate", str(bell / "prog.qcert.json"),
                        "--certified", str(bell / "prog.tsir.json"), "--board", "ghz4@1")
    stages = {s["id"]: s["status"] for s in res["grades"]["local"]["stages"]}
    assert code == 1 and stages["bundle"] == "passed" and stages["rules"] == "failed"
    # re-cooling the certified programme under the board's physics clears R7, with one more cool
    rc = res["recool"]
    assert "R7" in rc["clears"] and rc["failed_after"] == [] and rc["cools_after"] == rc["cools_before"] + 1
    assert Path(rc["path"]).is_file()


def test_a_bundle_whose_rules_json_is_not_a_rule_set_is_reported_not_crashed_on(tmp_path):
    """The example folders carry a rules REPORT named rules.json; packed into a bundle, the grader
    refuses it (RULESET.INVALID) and so must the review, instead of raising."""
    from qccd.workspace.bundle import write_bundle
    from qccd.workspace.tasks import board_device, find_release
    bell = COMPILED / "bell"
    rel = find_release("ghz4@1")
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))
    write_bundle(tmp_path / "b", task_id=rel.id, task_digest=rel.digest,
                 device=board_device(load(COMPILED / "chain4.arch.json"), rel),
                 program=load(bell / "prog.cooled.tsir.json"), certified_program=load(bell / "prog.tsir.json"),
                 certificate=load(bell / "prog.qcert.json"), rules=load(bell / "rules.json"))
    code, res, _ = _run(tmp_path, "--bundle", str(tmp_path / "b"))
    stages = {s["id"]: s["status"] for s in res["grades"]["local"]["stages"]}
    assert code == 1 and res["declared_rules_error"] and stages["rules"] == "failed"
    assert "RULESET.INVALID" in res["verdict"]
