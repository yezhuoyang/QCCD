"""The design-review skill (.claude/skills/qccd-design-review): its rule references are the code's.

The skill's `references/` are generated from the rule sources (`sync_references.py`), so a rule,
rule set, board or grading policy that changes without them being regenerated fails here -- and in
`.github/workflows/skills.yml` -- instead of an agent reviewing designs against rules that no longer
exist.  The hand-written guide must have a row for every rule of every shipped set, so a new rule
fails here until someone writes down what its failure means.  The review script itself is run on
a device that breaks a geometry rule, a programme that breaks a heat rule, an entry with its own
rule set, a C2LR entry on a C2LR board, and a floorplan that breaks a floorplan rule, so a review
that cannot fail is caught as well.
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
BELL = COMPILED / "bell"
GADGETS = REPO / "examples" / "gadgets"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"qccd_design_review_{name}", SKILL / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def generated():
    return _script("sync_references").generate(REPO)


def test_the_references_are_the_rules_as_the_code_has_them(generated):
    stale = [n for n, text in generated.items()
             if not (SKILL / "references" / n).is_file()
             or (SKILL / "references" / n).read_text(encoding="utf-8") != text]
    assert not stale, ("the skill's references are stale (" + ", ".join(stale) + "); run "
                       "python .claude/skills/qccd-design-review/scripts/sync_references.py --write")


def test_every_rule_set_and_board_is_in_the_references(generated):
    from qccd.ruleset import catalogue, load, names
    from qccd.workspace.tasks import list_boards
    rules_md, machine_md, boards_md = generated["rules.md"], generated["machine-rules.md"], generated["boards.md"]
    for r in catalogue().rules:
        assert f"### {r.id}: " in rules_md, r.id
        assert f"https://qccd.academy/rules/#{r.id}" in rules_md, r.id
    for n in names("device"):
        assert f'<a id="set-{n}"></a>' in generated["rule-sets.md"], n
        for rid in load(n).ids:
            assert f"### {rid}: " in rules_md, (n, rid)
    for n in names("machine"):
        for r in load(n).rules:
            assert f"### {r.id}: " in machine_md, r.id
    for b in list_boards():
        assert f"`{b.id}`" in boards_md, b.id
    import qccd.evalset as evalset
    for n in evalset.names():
        for r in evalset.load(n).requirements:
            assert f"| {r.id} | " in generated["grader.md"], (n, r.id)


def test_the_guide_says_what_every_rule_of_every_set_means():
    """review-guide.md is hand-written: a rule added to the library fails here until it has a row
    (what its failure usually means, and the fix)."""
    from qccd.ruleset import catalogue, load, names
    guide = (SKILL / "references" / "review-guide.md").read_text(encoding="utf-8")
    ids = [r.id for r in catalogue().rules] + [i for n in names("device") + names("machine") for i in load(n).ids]
    missing = sorted({i for i in ids if not re.search(rf"^\| {re.escape(i)} \|", guide, re.M)})
    assert not missing, ("references/review-guide.md has no row for " + ", ".join(missing)
                         + ": add one to its section 2 (rule | usually means | fix)")


def test_every_rule_the_scripts_name_still_exists():
    """The review script singles out a few rules by id (R10's stages, R24/R25's circuit, ...): a
    rename upstream must not leave it special-casing a rule that is gone."""
    from qccd.ruleset import catalogue
    from qccd.workspace.evaluator import ALLOWED_PARTIAL, ALLOWED_SKIPS
    review = _script("review")
    known = {r.id for r in catalogue().rules}
    text = (SKILL / "scripts" / "review.py").read_text(encoding="utf-8")
    named = set(re.findall(r'"((?:R|C)\d+[a-z]?)"', text)) | set(review.BY_DEFINITION)
    assert named and named <= known, sorted(named - known)
    assert set(ALLOWED_SKIPS) | set(ALLOWED_PARTIAL) <= known


def test_skill_md_is_a_skill_and_points_at_every_reference():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md needs YAML frontmatter"
    fm = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)
    assert fm.get("name") == "qccd-design-review"
    assert 0 < len(fm.get("description", "")) <= 1024
    # a plain YAML scalar ends at ": " or " #": the frontmatter would not parse (no PyYAML in CI)
    assert ": " not in fm["description"] and " #" not in fm["description"], "quote the description, or reword it"
    assert len(text.splitlines()) < 500
    for p in sorted((SKILL / "references").glob("*.md")):
        assert f"references/{p.name}" in text, f"SKILL.md never points at references/{p.name}"


def _sharp_corner(tmp_path: Path) -> Path:
    """grid2x3 with one trap pulled toward a neighbour: two rails at J0_1 meet at 18.4 degrees."""
    doc = _load(COMPILED / "grid2x3.arch.json")
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


def _uncooled(tmp_path: Path) -> Path:
    """The Bell example's certified programme with every `cool` taken out: too hot for any board
    (R7) and cooled nowhere (R7c), whatever the example's own cooling is today."""
    doc = _load(BELL / "prog.tsir.json")
    doc["instructions"] = [i for i in doc["instructions"] if i["type"] != "cool"]
    p = tmp_path / "bell.uncooled.tsir.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _run(tmp_path: Path, *args: str, out: str = "r") -> tuple[int, dict, str]:
    o = tmp_path / out
    code = _script("review").main([*args, "--out", str(o)])
    return code, _load(o / "rule-check.json"), (o / "rule-check.md").read_text(encoding="utf-8")


def test_a_device_review_finds_a_sharp_corner(tmp_path):
    code, res, _ = _run(tmp_path, "--device", str(_sharp_corner(tmp_path)), "--board", "steane@1")
    rows = {r["id"]: r for r in res["rules"]}
    assert code == 1 and res["device_only"]
    assert rows["R20"]["review"] == "FAIL" and rows["R20"]["verify"]["status"] == "failed"
    assert rows["R20"]["language"]["verdict"] == "fails"
    assert rows["R21"]["review"] == "pass"
    # a programme rule is not judged on a device alone, by either checker
    assert rows["R1"]["review"] == "needs a programme" and rows["R1"]["language"] is None
    # what a device alone decides is read off the words each rule uses
    assert rows["R11"]["review"] == "pass (device part)" and rows["R11"]["reads"] == "part"
    assert rows["R16"]["review"] == "pass (vacuous)" and rows["R16"]["reads"] == "model"
    # the board's 13-qubit circuit in 14 slots: it fits, with one to spare
    assert res["fit"]["circuit_qubits"] == 13 and res["fit"]["spare_slots"] == 1


def test_goal_1_counts_the_ions_a_programme_places():
    """goal1.eval's G2 lists a memory board's entries up to 200 ions; a board of no goal-1 memory
    experiment has no such limit."""
    from types import SimpleNamespace
    from qccd.workspace.tasks import find_release
    goal_ions = _script("review").goal_ions
    over = goal_ions(find_release("surface3xz_mem@1"), SimpleNamespace(ions=list(range(201))))
    assert over == {"goal": "goal1", "programme_ions": 201, "max_ions": 200, "experiment_ions": 34}
    assert goal_ions(find_release("ghz4@1"), SimpleNamespace(ions=[0, 1])) is None
    assert goal_ions(find_release("goal3_surface3_mem@1"), SimpleNamespace(ions=[0])) is None


def test_a_programme_review_judges_every_rule_of_the_board(tmp_path):
    from qccd.ruleset import load
    code, res, md = _run(tmp_path, "--device", str(COMPILED / "chain4.arch.json"),
                         "--program", str(_uncooled(tmp_path)),
                         "--certificate", str(BELL / "prog.qcert.json"), "--board", "ghz4@1")
    rows = {r["id"]: r for r in res["rules"]}
    assert list(rows) == load("standard").ids
    assert code == 1 and rows["R7"]["review"] == "FAIL" and rows["R7c"]["review"] == "FAIL"
    assert "R7" in res["verdict"] and "R7c" in res["verdict"]
    # the rules the board's rules stage never runs are judged in the rule language, with the circuit
    for rid in ("R0", "R24", "R25"):
        assert rows[rid]["verify"]["status"] is None and rows[rid]["language"]["verdict"] == "holds", rid
    assert rows["R16"]["review"] == "pass (vacuous)" and rows["R7b"]["review"] == "allowed skip"
    # a rule with nothing to judge says so: the Bell programme splits no chain
    assert rows["R14"]["review"] == "pass (nothing to check)" and rows["R14"]["cases"][0]["cases"] == 0
    # a passing rule that compares with a limit gives its closest case: chains of 2 against max_chain 15
    assert rows["R13"]["closest"]["bound"] == 15 and rows["R13"]["closest"]["slack"] == 13
    assert any(n.startswith("closest case: ") for n in rows["R13"]["notes"])
    # a Bell certificate is not about the GHZ board's circuit
    assert res["certificate"] == {"certificate_qubits": 2, "board_qubits": 4, "matches": False, "certificate_ops": 4}
    assert "[R20](https://qccd.academy/rules/#R20)" in md


def test_loose_files_are_graded_as_a_bundle_and_the_heat_fix_is_checked(tmp_path):
    prog = str(_uncooled(tmp_path))
    code, res, _ = _run(tmp_path, "--device", str(COMPILED / "chain4.arch.json"), "--program", prog,
                        "--certificate", str(BELL / "prog.qcert.json"), "--certified", prog, "--board", "ghz4@1")
    stages = {s["id"]: s["status"] for s in res["grades"]["local"]["stages"]}
    assert code == 1 and stages["bundle"] == "passed" and stages["rules"] == "failed"
    assert stages["correspondence"] == "passed"
    # re-cooling the certified programme under the board's physics clears both heat rules
    rc = res["recool"]
    assert {"R7", "R7c"} <= set(rc["clears"]) and rc["failed_after"] == [] and rc["cools_after"] > rc["cools_before"] == 0
    assert Path(rc["path"]).is_file()


def test_an_entrys_own_rule_set_decides_its_rules_stage(tmp_path):
    """`jones` (a paper's set) adds R26, R27, R29 and R30 and drops most board rules: its R27 fails
    the stage, its schedule-only rules do not apply, and the board's broken heat rules cost only
    the board's ranking."""
    code, res, md = _run(tmp_path, "--device", str(COMPILED / "chain4.arch.json"),
                         "--program", str(_uncooled(tmp_path)),
                         "--certificate", str(BELL / "prog.qcert.json"), "--board", "ghz4@1", "--rules", "jones")
    rows = {r["id"]: r for r in res["rules"]}
    assert res["rule_sets"] == {"board": "standard", "declared": "jones"}
    assert rows["R27"]["review"] == "FAIL" and rows["R27"]["grader"] == "FAIL" and not rows["R27"]["in_board_set"]
    assert rows["R26"]["review"] in ("pass", "pass (nothing to check)") and rows["R26"]["grader"] == "holds"
    assert rows["R29"]["review"] == "does not apply" and rows["R30"]["review"] == "does not apply"
    # jones drops R7: the stage does not judge it, the board's ranking does
    assert rows["R7"]["grader"] == "not in the entry's own set" and rows["R7"]["board"] == "FAIL"
    assert code == 1 and "fails its own rule set `jones`: R27" in res["verdict"]
    assert "breaks the board's R7" in res["verdict"] and "Board's set (ranking)" in md


def test_a_report_alone_is_read_with_the_entrys_own_rule_set(tmp_path):
    """An official /report records the entry's own set in its rules stage: the review reads it."""
    from qccd.ruleset.declared import rules_doc
    from qccd.workspace.bundle import write_bundle
    from qccd.workspace.evaluator import grade
    from qccd.workspace.tasks import board_device, find_release
    rel = find_release("ghz4@1")
    write_bundle(tmp_path / "b", task_id=rel.id, task_digest=rel.digest,
                 device=board_device(_load(COMPILED / "chain4.arch.json"), rel),
                 program=_load(BELL / "prog.cooled.tsir.json"), certified_program=_load(BELL / "prog.tsir.json"),
                 certificate=_load(BELL / "prog.qcert.json"), rules=rules_doc("jones"))
    report = grade(rel, tmp_path / "b", "reference", workdir=tmp_path / "w")
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    code, res, _ = _run(tmp_path, "--report", str(tmp_path / "report.json"))
    rows = {r["id"]: r for r in res["rules"]}
    assert res["rule_sets"]["declared"] == "jones" and code == 1
    assert rows["R27"]["review"] == "FAIL" and rows["R26"]["review"] == "pass"
    # the board's rules are read from the report's own rules stage, not left unchecked
    assert rows["R1"]["review"] == "pass" and rows["R1"]["board"] == "pass"


@pytest.fixture(scope="module")
def c2lr_entry(tmp_path_factory):
    """The C2LR router's programme for the C2LR repetition-code board, on a 5 x 2 Ba-Sr lattice."""
    from qccd.api import Machine
    from qccd.compile.c2lr_router import compile_with_router
    from qccd.workspace.tasks import find_release
    d = tmp_path_factory.mktemp("c2lr")
    rel = find_release("c2lr_rep5_mem_basr@1")
    dev = d / "dev.arch.json"
    dev.write_text(json.dumps(Machine.c2lr_grid(5, 2, species="Ba-Sr").arch.to_json()), encoding="utf-8")
    final, cert = compile_with_router(rel.circuit_text(), dev, d / "w",
                                      table=rel.manifest["physics"].get("rank_table", "qccdsim_jones"))
    return {"board": rel.id, "device": dev, "program": final, "certificate": cert, "certified": d / "w" / "prog.tsir.json"}


def test_a_c2lr_entry_is_judged_by_every_rule_of_its_board(tmp_path, c2lr_entry):
    from qccd.ruleset import load
    e = c2lr_entry
    _, res, _ = _run(tmp_path, "--device", str(e["device"]), "--program", str(e["program"]),
                     "--certificate", str(e["certificate"]), "--board", e["board"], "--no-phys")
    rows = {r["id"]: r for r in res["rules"]}
    assert list(rows) == load("c2lr").ids
    for i in range(1, 11):
        assert rows[f"C{i}"]["review"] in ("pass", "allowed partial"), (i, rows[f"C{i}"])
    # the stage runs `c2lr` without the circuit: R24 and R25 do not apply there; with it, they hold
    for rid in ("R24", "R25"):
        assert rows[rid]["grader"].startswith("not judged") and rows[rid]["review"] == "pass", rid
        assert any("without the circuit" in n for n in rows[rid]["notes"])


def test_a_c2lr_device_alone_decides_its_wiring(tmp_path, c2lr_entry):
    code, res, _ = _run(tmp_path, "--device", str(c2lr_entry["device"]), "--board", c2lr_entry["board"], "--no-phys")
    rows = {r["id"]: r for r in res["rules"]}
    assert code == 0 and rows["C10"]["review"] == "pass" and rows["C8"]["review"] == "allowed partial"
    assert rows["C1"]["review"] == "needs a programme"


@pytest.fixture(scope="module")
def gadget_cache(tmp_path_factory):
    pytest.importorskip("numpy")            # the gadget leaves need it (qccd/gadget/leaves/ccz.py)
    return str(tmp_path_factory.mktemp("gadget-cache"))


def test_a_floorplan_review_judges_every_floorplan_rule(tmp_path, gadget_cache):
    from qccd.ruleset import load
    town = GADGETS / "floorplans" / "town.floor"
    code, res, _ = _run(tmp_path, "--floorplan", str(town), "--gadget-cache", gadget_cache)
    assert code == 0 and [r["id"] for r in res["rules"]] == load("floorplan").ids
    assert all(r["review"] == "pass" for r in res["rules"]), res["rules"]
    # one place moved onto another (tests/test_gadget_architecture.py's F3 mutation)
    bad = tmp_path / "overlap.floor"
    bad.write_text(town.read_text(encoding="utf-8").replace("place zone1     zone_d3 at 73 10",
                                                            "place zone1     zone_d3 at 64 10"), encoding="utf-8")
    code, res, md = _run(tmp_path, "--floorplan", str(bad), "--gadget-cache", gadget_cache, out="bad")
    rows = {r["id"]: r for r in res["rules"]}
    assert code == 1 and rows["F3"]["review"] == "FAIL" and "F3" in res["verdict"]
    assert "[F3](https://qccd.academy/rules/#F3)" in md
    # every counterexample the rule language kept: the move breaks F4 eight ways
    assert rows["F4"]["review"] == "FAIL" and len(rows["F4"]["language"]["examples"]) == rows["F4"]["language"]["count"] == 8


def test_an_algorithm_review_judges_every_machine_rule(tmp_path, gadget_cache):
    from qccd.ruleset import load
    code, res, _ = _run(tmp_path, "--algorithm", str(GADGETS / "algorithms" / "bell_cnot.alg"),
                        "--gadget-cache", gadget_cache)
    rows = {r["id"]: r for r in res["rules"]}
    assert list(rows) == load("floorplan").ids + load("machine").ids
    assert code == 0 and res["signoff"]["flat"] is True
    # the place checks are a second checker of the G and H rules they cover
    assert rows["G1"]["place_checks"] == "passed" and rows["G12"]["place_checks"] is None
    assert all(r["review"] in ("pass", "pass (nothing to check)", "does not apply") for r in rows.values())


def test_a_bundle_whose_rules_json_is_not_a_rule_set_is_reported_not_crashed_on(tmp_path):
    """The example folders carry a rules REPORT named rules.json; packed into a bundle, the grader
    refuses it (RULESET.INVALID) and so must the review, instead of raising."""
    from qccd.workspace.bundle import write_bundle
    from qccd.workspace.tasks import board_device, find_release
    rel = find_release("ghz4@1")
    write_bundle(tmp_path / "b", task_id=rel.id, task_digest=rel.digest,
                 device=board_device(_load(COMPILED / "chain4.arch.json"), rel),
                 program=_load(BELL / "prog.cooled.tsir.json"), certified_program=_load(BELL / "prog.tsir.json"),
                 certificate=_load(BELL / "prog.qcert.json"), rules=_load(BELL / "rules.json"))
    code, res, _ = _run(tmp_path, "--bundle", str(tmp_path / "b"))
    stages = {s["id"]: s["status"] for s in res["grades"]["local"]["stages"]}
    assert code == 1 and res["declared_rules_error"] and stages["rules"] == "failed"
    assert "RULESET.INVALID" in res["verdict"]
