"""An entry may declare the rules it keeps; the leaderboard ranks the board's rules first.

The user, 2026-10-29: designs were refused because a rule failed, though the rule language
already lets a rule set be anything.  So a bundle may carry its own rule set (`rules.json`): a
shipped one, or one written for the design (the two-layer trap that started it drops the rules
that assume a flat surface trap).  The grader judges the entry by it, and still says which of
the board's rules it keeps (`conformance`).  A ranking lists the entries that keep the board's
rules with the board's physics; `rules=all` lists every entry, each tagged.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from qccd.official.service import OfficialError, OfficialService, create_app
from qccd.ruleset import load
from qccd.ruleset.declared import DeclaredRulesError, changes, load_declared, rules_doc
from qccd.workspace.tasks import RELEASES_DIR

HUMAN = {"kind": "human", "id": "cli"}

TWO_LAYER = """ruleset "two-layer"
title "Two stacked junction layers"
include "standard"
drop R19, R20, R21
rule L19 "A junction joins at most six rails"
  for all n in nodes: degree(n) <= 6
"""


# ---------------------------------------------------------------------- reading a declared set

def test_a_declared_set_is_written_out_whole_and_says_what_it_changes():
    doc = rules_doc(TWO_LAYER)
    assert doc["kind"] == "qccd.rules" and doc["name"] == "two-layer" and doc["source"] == TWO_LAYER
    assert "include" not in doc["text"]                      # self-contained: the grader reads no library
    rs = load_declared(doc["text"])
    assert changes(load("standard"), rs) == {"drops": ["R19", "R20", "R21"], "replaces": [], "adds": ["L19"],
                                             "params": {}}
    assert changes(load("standard"), load_declared(rules_doc("standard")["text"])) == \
        {"drops": [], "replaces": [], "adds": [], "params": {}}


@pytest.mark.parametrize("text, why", [
    ('include "../../etc/passwd"\n', "ships"),                       # never a path on the grader's disk
    ('ruleset "x"\ninclude "C:/secret.rules"\n', "ships"),
    ('ruleset "x"\ninclude "standard"\ndrop R1\n', "core"),          # the core rules hold in every set ...
    ('ruleset "x"\nrule R8 "anything" core\n  true\n', "core"),      # ... as the catalogue writes them
    ('ruleset "x"\nlevel "machine"\n', "device"),
    ('ruleset "x"\nrule X "y"\n  for all n in nodes: frobnicate(n)\n', "vocabulary"),
    ("x" * 70_000, "KB"),
    ("", "empty"),
], ids=["path", "drive-path", "drop-core", "rewrite-core", "machine-level", "unknown-word", "too-long", "empty"])
def test_a_declared_set_the_grader_should_not_read_is_refused(text, why):
    with pytest.raises(DeclaredRulesError) as e:
        load_declared(text)
    assert why in str(e.value)


# ---------------------------------------------------------------------- the ranking's two views

def _conf(board: bool, **rules) -> dict:
    return {"board": board, "rules": {"set": rules.get("set", "standard"), "declared": not board,
                                      "board_set": "standard", "breaks": rules.get("breaks", []),
                                      "drops": rules.get("drops", []), "replaces": [], "adds": rules.get("adds", []),
                                      "params": {}},
            "physics": {"board": True, "added": [], "ignored": []}}


def _entry(svc, sid: str, task: str, value: float, conformance: dict | None) -> None:
    rel = svc.releases[task]
    rep = {"kind": "qccd.evaluation_report", "eligibility": {"eligible": True},
           "metrics": {rel.manifest["rank_by"]: {"value": value, "unit": "us"}}}
    if conformance is not None:
        rep["conformance"] = conformance
    svc.db.execute("INSERT INTO submissions(id, uploader, task, task_digest, bundle_digest, archive_sha256, visibility, "
                   "display_name, status, created_at) VALUES(?,?,?,?,?,?, 'public', ?, 'eligible', ?)",
                   (sid, "u", task, rel.digest, "sha256:" + sid, "x" + sid, sid, time.time()))
    svc.db.execute("INSERT INTO reports(id, submission, evaluator_version, evaluator_policy, report_digest, report, "
                   "eligible, rank_value, created_at) VALUES(?,?,?,?,?,?,1,?,?)",
                   ("or_" + sid, sid, "1.0.0", svc.evaluator_policy, "d" + sid, json.dumps(rep), value, time.time()))


@pytest.fixture
def svc(tmp_path):
    s = OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")
    _entry(s, "os_old", "ghz4@1", 30.0, None)                               # graded before conformance existed
    _entry(s, "os_keeps", "ghz4@1", 20.0, _conf(True))
    _entry(s, "os_own", "ghz4@1", 10.0, _conf(False, set="two-layer", breaks=["R20", "R21"], drops=["R20", "R21"]))
    return s


def test_a_ranking_lists_the_entries_that_keep_the_boards_rules_unless_asked_for_all(svc):
    lb = svc.leaderboard("ghz4@1")
    assert [r["id"] for r in lb["rows"]] == ["os_keeps", "os_old"]          # the board's rules: today's ranking
    assert lb["rules"] == "board" and lb["board_rules"] == "standard"
    assert lb["counts"] == {"board": 2, "all": 3}                            # "1 more under other rules"
    every = svc.leaderboard("ghz4@1", rules="all")
    assert [r["id"] for r in every["rows"]] == ["os_own", "os_keeps", "os_old"]
    own = every["rows"][0]["conformance"]
    assert own["board"] is False and own["rules"]["set"] == "two-layer" and own["rules"]["drops"] == ["R20", "R21"]
    assert every["rows"][2]["conformance"]["board"] is True                  # an old report kept every board rule
    with pytest.raises(OfficialError) as e:
        svc.leaderboard("ghz4@1", rules="mine")
    assert e.value.status == 422


def test_the_http_api_takes_the_view_as_a_query(svc):
    from fastapi.testclient import TestClient
    c = TestClient(create_app(svc))
    assert [r["id"] for r in c.get("/v1/leaderboard/ghz4@1").json()["rows"]] == ["os_keeps", "os_old"]
    assert len(c.get("/v1/leaderboard/ghz4@1?rules=all").json()["rows"]) == 3
    assert c.get("/v1/leaderboard/ghz4@1?rules=nope").status_code == 422
    assert c.get("/v1/goals/goal1?rules=all").json()["rules"] == "all"


def test_goal_1_ranks_the_boards_rules_first_too(svc, monkeypatch):
    def rows(task):
        exp = {"ions": 34, "k": 1}
        return ([{"id": "os_a", "display_name": "a", "by": "x", "created_at": 0, "rank_value": None,
                  "metrics": {"ler_per_round": 2e-4}, "experiment": exp, "conformance": _conf(True)},
                 {"id": "os_b", "display_name": "b", "by": "x", "created_at": 0, "rank_value": None,
                  "metrics": {"ler_per_round": 1e-4}, "experiment": exp, "conformance": _conf(False)}]
                if task.startswith("surface3xz") else [], {})
    monkeypatch.setattr(svc, "_ranked_rows", rows)
    assert [r["id"] for r in svc.goal_board("goal1")["rows"]] == ["os_a"]
    g = svc.goal_board("goal1", rules="all")
    assert [r["id"] for r in g["rows"]] == ["os_b", "os_a"] and g["counts"] == {"board": 1, "all": 2}


def test_goal_3_lists_the_boards_rules_first_code_by_code(svc, monkeypatch):
    def rows(task):
        m = {"T_quantinuum": 120.0, "ler_per_round": 2e-4}
        return ([{"id": "os_a", "display_name": "a", "by": "x", "created_at": 0, "metrics": m, "conformance": _conf(True)},
                 {"id": "os_b", "display_name": "b", "by": "x", "created_at": 0, "metrics": m, "conformance": _conf(False)}]
                if task == "goal3_surface3_mem@1" else [], {})
    monkeypatch.setattr(svc, "_ranked_rows", rows)
    monkeypatch.setattr(svc, "_entry_family", lambda sid: ("Grid", None))

    def s3(g):
        return [e["id"] for e in next(r for r in g["releases"] if r["id"] == "goal3_surface3_mem@1")["entries"]]
    g = svc.goal_board("goal3")
    assert s3(g) == ["os_a"] and g["rules"] == "board" and g["counts"] == {"board": 1, "all": 2}
    every = svc.goal_board("goal3", rules="all")
    assert s3(every) == ["os_a", "os_b"]
    assert next(r for r in every["releases"] if r["id"] == "goal3_surface3_mem@1")["entries"][1]["conformance"]["board"] is False


def test_a_track_board_keeps_its_own_set_and_lists_the_boards_rules_first(svc):
    rel = "c2lr_rep5_mem_basr@1"
    _entry(svc, "os_c2", rel, 5.0, _conf(True, set="c2lr"))
    _entry(svc, "os_c2own", rel, 4.0, _conf(False, set="c2lr-lax", drops=["C6"]))
    t = svc.track_board("c2lr")
    b = next(b for b in t["boards"] if b["release"] == rel)
    assert b["rules"] == "c2lr" and b["board_rules"] == "c2lr"           # the set the release declares
    assert [r["id"] for r in b["rows"]] == ["os_c2"] and t["rules"] == "board"
    assert t["counts"] == {"board": 1, "all": 2}
    every = svc.track_board("c2lr", rules="all")
    assert [r["id"] for r in next(b for b in every["boards"] if b["release"] == rel)["rows"]] == ["os_c2own", "os_c2"]
    from fastapi.testclient import TestClient
    assert TestClient(create_app(svc)).get("/v1/tracks/c2lr?rules=all").json()["counts"]["all"] == 2


def test_an_entry_on_a_c2lr_board_may_start_from_the_boards_set():
    rs = load_declared('ruleset "c2lr-lax"\ninclude "c2lr"\ndrop C6\n')
    ch = changes(load("c2lr"), rs)
    assert ch["drops"] == ["C6"] and not ch["adds"] and not ch["replaces"]


# ---------------------------------------------------------------------- graded for real

def _toolchain():
    from qccd.workspace.evaluator import Toolchain
    tc = Toolchain.discover()
    if tc.qccdc is None or tc.qcheck is None:
        pytest.skip("toolchain not built")
    return tc


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    _toolchain()
    from qccd.workspace.app import Workspace
    w = Workspace.init(tmp_path_factory.mktemp("d") / "ws", "ghz4@1")

    def wait(jid):
        while w.job(jid)["status"] not in ("succeeded", "failed", "internal_error", "cancelled", "timeout"):
            time.sleep(0.3)
        return w.job(jid)
    w.wait = wait
    j = wait(w.start_job(HUMAN, "compile", {})["job_id"])
    w.apply_change_set({"expected_revision": 0, "request_id": "a", "mode": "apply",
                        "operations": [j["result"]["adopt_with"]]}, HUMAN)
    yield w
    w.close()


def _graded(ws, **kw) -> dict:
    s = ws.submit_local(HUMAN, profile="reference", **kw)
    ws.wait(s["job_id"])
    return ws.submission(s["submission_id"])


def test_an_entry_under_a_laxer_set_that_keeps_every_board_rule_conforms(ws):
    (ws.root / "rules").mkdir(exist_ok=True)
    (ws.root / "rules" / "lax.rules").write_text('ruleset "lax"\ninclude "standard"\ndrop R21\n', encoding="utf-8")
    assert [o["name"] for o in ws.rules_catalog()["workspace"]] == ["lax"]
    sub = _graded(ws, rules="rules/lax.rules")
    rep = sub["report"]
    assert rep["eligibility"]["eligible"], rep["eligibility"]
    c = rep["conformance"]
    assert c["board"] is True and c["rules"]["declared"] and c["rules"]["drops"] == ["R21"]
    assert sub["rules"]["name"] == "lax"
    plain = _graded(ws)
    assert plain["rules"] is None and plain["report"]["conformance"]["rules"]["declared"] is False


def test_an_entry_is_held_to_the_rules_it_adds(ws):
    strict = 'ruleset "strict"\ninclude "standard"\nrule S1 "No site holds an ion"\n  for all s in sites: capacity(s) < 1\n'
    rep = _graded(ws, rules=strict)["report"]
    assert not rep["eligibility"]["eligible"]
    assert any(d["id"] == "RULESET.S1" for d in rep["diagnostics"])
    from qccd.workspace.core import WorkspaceError
    with pytest.raises(WorkspaceError) as e:
        ws.submit_local(HUMAN, rules='ruleset "x"\ninclude "standard"\ndrop R8\n')
    assert e.value.code == "bad_rules"


def test_a_device_that_breaks_a_board_rule_is_eligible_under_rules_that_drop_it(ws, tmp_path):
    """The two-layer case in miniature: the same program, on a device drawn with a sharp bend (R20
    fails); refused under the board's rules, eligible under a set without R20 and R21, and tagged
    as not keeping the board's rules -- so the board's own ranking leaves it out."""
    from qccd.workspace.bundle import read_bundle, write_bundle
    from qccd.workspace.evaluator import Toolchain, grade
    tc = _toolchain()
    sub = _graded(ws)
    b = read_bundle(ws.root / sub["snapshot"]["dir"] / "bundle")
    rel = ws.board("ghz4@1")
    dev = copy.deepcopy(b.docs["device"])
    nodes = {n["id"]: n for n in dev["geometry"]["nodes"]}
    seg = next(s for s in dev["geometry"]["segments"]
               if sum(1 for t in dev["geometry"]["segments"] if s["ends"][1] in t["ends"]) == 2)
    mid = nodes[seg["ends"][1]]                         # a node with two rails: pull it far off their line
    mid["pos"] = [mid["pos"][0] + 0.05, mid["pos"][1] + 40.0]
    for x in dev["geometry"]["nodes"] + dev["geometry"]["segments"]:
        x.pop("corner", None)                           # derived from the drawing: the loader recomputes them
        x.pop("corner_endpoints", None)
    out = {}
    for name, rules in (("board", None), ("own", rules_doc('ruleset "bent"\ninclude "standard"\ndrop R20, R21\n'))):
        nb = write_bundle(tmp_path / name, task_id=rel.id, task_digest=rel.digest, device=dev,
                          program=b.docs["program"], certified_program=b.docs["certified_program"],
                          certificate=b.docs["certificate"], rules=rules)
        out[name] = grade(rel, nb.root, "reference", workdir=tmp_path / (name + "_w"), toolchain=tc)
    stages = {s["id"]: s for s in out["board"]["stages"]}
    assert "R20" in stages["rules"]["detail"].get("failed", []),         [(k, v["status"], v["coverage"][:80]) for k, v in stages.items()] + out["board"]["diagnostics"][:3]
    assert not out["board"]["eligibility"]["eligible"]
    assert out["own"]["eligibility"]["eligible"], out["own"]["eligibility"]
    c = out["own"]["conformance"]
    assert c["board"] is False and "R20" in c["rules"]["breaks"] and c["rules"]["drops"] == ["R20", "R21"]
    warn = [d for d in out["own"]["diagnostics"] if d["id"] == "RULE.R20"]
    assert warn and all(d["severity"] == "warning" for d in warn)


def test_the_noise_model_prices_a_number_the_device_brought_and_nothing_else_without_its_table():
    """qccd-noise@2 prices transport from the task's table only, and refuses a curve without a point
    there -- except a number the device brought where the task has none (the physics lock lists it),
    which is the entry's own and is listed as such."""
    from qccd.arch.device import Architecture
    from qccd.qec.noise import NoiseModel2
    from qccd.workspace.tasks import find_release, scored_device
    grid = json.loads((Path(__file__).resolve().parents[1] / "arch" / "grid9x9.arch.json").read_text(encoding="utf-8"))
    grid["primitives"]["junction_cross"]["curve_by_degree"]["5"] = [{"us": 120.0, "quanta": 3.0, "table": "cyclone"}]
    doc, note = scored_device(grid, find_release("surface5xz_mem@1"))
    assert note["added"] == ["primitives.junction_cross.curve_by_degree.5"]
    arch = Architecture.from_json(doc)
    with pytest.raises(ValueError, match=r"junction_cross\[degree 5\]"):
        NoiseModel2().resolve(arch)
    NoiseModel2(own=tuple(note["added"])).resolve(arch)
    assert "own" not in NoiseModel2().to_json()
    assert list(NoiseModel2(own=tuple(note["added"])).to_json()["own"]) == note["added"]
