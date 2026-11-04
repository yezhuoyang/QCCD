"""Agent review of a graded submission (qccd/official/review.py).

The decision is the grade's and the rule check's, never the agent's: a review that fails, runs too
long or says anything at all leaves it standing.  The agent runs inside its CLI's sandbox with a
minimal environment (no server token, no database URL, no other key), a person's own CLI sign-in
is honoured only on a development server, the pages escape whatever an agent writes, and the
review console answers only its own page on this computer.

A stand-in agent plays both CLIs here (it answers `login status` / `auth status`, writes the
review, and prints each CLI's JSON events), so nothing reaches a model.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from qccd.official import review as rv
from qccd.official.review_pages import EXAMPLES, example_archive, review_page
from qccd.official.service import OfficialError, OfficialService, create_app
from qccd.official.worker import process_one
from qccd.workspace.tasks import RELEASES_DIR

FAKE = r'''#!{python}
import json, os, sys, time, pathlib
here = pathlib.Path(__file__).resolve().parent
mode = (here / "mode").read_text().strip() if (here / "mode").exists() else "ok"
a = sys.argv[1:]
if a[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "dev@example.com", "subscriptionType": "max"}))
    sys.exit(0)
(here / "argv.json").write_text(json.dumps({"argv": a, "env": sorted(os.environ)}))
if a == ["login"] or a == ["auth", "login"]:
    sys.exit(0)
job = pathlib.Path(a[a.index("--cd") + 1]) if "--cd" in a else pathlib.Path.cwd()
text = {"ok": "# Review\n\n**Verdict:** not accepted: fails R7.\n\n" + "The first gate starts too hot.\n" * 8,
        "xss": "# Review\n\n<script>alert(1)</script>\n\n[click](javascript:alert(2))\n\n" + "padding.\n" * 40}.get(mode)
if mode == "slow":
    time.sleep(60)
claude = "-p" in a
def emit(e):
    print(json.dumps(e), flush=True)
if claude:
    emit({"type": "system", "subtype": "init", "model": "claude-opus-5-5"})
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "cat rule-check/rule-check.md"}}]}})
else:
    emit({"type": "thread.started", "thread_id": "t"})
    emit({"type": "item.started", "item": {"type": "command_execution", "command": "/bin/zsh -lc 'cat rule-check/rule-check.md'"}})
if text:
    (job / "out" / "review.md").write_text(text)
if claude:
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "DONE", "num_turns": 3,
          "duration_ms": 1200, "usage": {"input_tokens": 900, "output_tokens": 100}})
else:
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 900, "output_tokens": 100}})
'''


@pytest.fixture
def agents(tmp_path, monkeypatch):
    """Both reviewers are the stand-in, signed in locally, on a development server."""
    d = tmp_path / "bin"
    d.mkdir()
    p = d / "fake-agent"
    p.write_text(FAKE.replace("{python}", sys.executable), encoding="utf-8")
    p.chmod(0o755)
    for k in ("QCCD_REVIEW_CLAUDE_BIN", "QCCD_REVIEW_CODEX_BIN"):
        monkeypatch.setenv(k, str(p))
    for k, v in (("QCCD_OFFICIAL_DEV", "1"), ("QCCD_REVIEW", "1"), ("QCCD_REVIEW_LOCAL_LOGINS", "1"),
                 ("QCCD_OFFICIAL_DEV_TOKEN", "dev-secret-token"), ("QCCD_OFFICIAL_DB", "postgresql://user:pw@db/x"),
                 ("HOME", str(tmp_path / "home"))):     # no ~/.codex/config.toml: this machine's choices stay out
        monkeypatch.setenv(k, v)
    for k in ("QCCD_REVIEW_ANTHROPIC_API_KEY", "QCCD_REVIEW_OPENAI_API_KEY", "QCCD_REVIEW_CODEX_MODEL",
              "QCCD_REVIEW_CODEX_EFFORT", "QCCD_REVIEW_CODEX_HOME", "QCCD_REVIEW_CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)
    rv._STATUS.clear()

    def mode(m: str) -> None:
        (d / "mode").write_text(m, encoding="utf-8")
    mode("ok")
    yield {"mode": mode, "seen": lambda: json.loads((d / "argv.json").read_text(encoding="utf-8"))}
    rv._STATUS.clear()


@pytest.fixture
def svc(tmp_path):
    return OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")


def _graded(svc, tmp_path, example: str, reviewer: str = "codex") -> str:
    """Submit an example as the development uploader, asking `reviewer` to review it; grade it."""
    task, archive = example_archive(example)
    sid = svc.submit(svc.dev_uploader(), archive, task=task, visibility="private", reviewer=reviewer)["submission_id"]
    while process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR):
        pass
    return sid


def _report(stages: dict, eligible: bool) -> dict:
    return {"eligibility": {"eligible": eligible, "reasons": [f"{k}: {v}" for k, v in stages.items() if v != "passed"]},
            "stages": [{"id": k, "required": True, "status": v} for k, v in stages.items()]}


ALL_PASS = {"bundle": "passed", "rules": "passed", "certificate_binding": "passed", "lean_certificate": "passed"}


def test_the_decision_is_the_grades_and_the_rule_checks():
    row = {"in_board_set": True, "in_declared_set": None}
    ok = {"rules": [{**row, "id": "R7", "title": "cool enough", "review": "pass", "grader": "pass"}]}
    assert rv.decide(_report(ALL_PASS, True), ok)["decision"] == "accepted"
    # a rule only the rule language fails (the rules stage does not run R24 on a standard board): held
    r24 = {"rules": [{**row, "id": "R24", "title": "every gate", "review": "FAIL", "grader": "not judged by the rules stage"}]}
    held = rv.decide(_report(ALL_PASS, True), r24)
    assert held["decision"] == "held" and "R24" in held["reasons"][0]
    # the grader's failure decides, and what this server could not run is listed apart
    hot = {"rules": [{**row, "id": "R7", "title": "cool enough", "review": "FAIL", "grader": "FAIL"}]}
    no = rv.decide(_report({**ALL_PASS, "rules": "failed", "lean_certificate": "unsupported"}, False), hot)
    assert no["decision"] == "not_accepted" and no["reasons"][0].startswith("breaks R7")
    assert no["not_run"] == ["lean_certificate (unsupported)"]
    # nothing failed, but a tool is missing here: not decided on this server
    und = rv.decide(_report({**ALL_PASS, "lean_certificate": "unsupported"}, False), ok)
    assert und["decision"] == "undetermined" and und["not_run"] == ["lean_certificate (unsupported)"]
    # ... unless the rule check already knows a stage must fail: a certificate for another circuit
    wrong = {**ok, "certificate": {"matches": False, "certificate_qubits": 2, "board_qubits": 4}}
    rep = _report({**ALL_PASS, "certificate_binding": "unsupported", "lean_certificate": "skipped"}, False)
    assert rv.decide(rep, wrong)["decision"] == "not_accepted"
    assert "2-qubit circuit" in rv.decide(rep, wrong)["reasons"][0]


def test_each_clis_events_become_progress_and_usage():
    claude = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                             "input": {"command": "python3 review.py --bundle b"}},
                                                            {"type": "text", "text": "R7 fails at the first gate.\nMore."}]}},
              {"type": "result", "is_error": False, "result": "DONE", "num_turns": 4, "usage": {"input_tokens": 10, "output_tokens": 5}},
              {"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}]
    got = [rv.parse_event("claude", json.dumps(e)) for e in claude]
    assert got[0]["progress"] == ["Running python3 review.py --bundle b", "R7 fails at the first gate."]
    assert got[1]["final"] == "DONE" and got[1]["usage"]["tokens"] == 15 and "error" not in got[1]
    assert got[2]["error"].startswith("Not logged in")
    codex = [{"type": "item.started", "item": {"type": "command_execution", "command": "/bin/zsh -lc 'ls -la .'"}},
             {"type": "item.completed", "item": {"type": "agent_message", "text": "Writing the review."}},
             {"type": "item.completed", "item": {"type": "error", "message": "Codex is ignoring 1 unrecognized setting"}},
             {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 7}}]
    got = [rv.parse_event("codex", json.dumps(e)) for e in codex]
    assert got[0] == {"progress": ["Running ls -la ."]} and got[1]["message"] == "Writing the review."
    assert got[2] is None                         # a configuration warning is not a failure
    assert got[3] == {"usage": {"tokens": 107}}
    assert rv.parse_event("codex", "not json") is None


def test_the_agent_runs_sandboxed_with_a_minimal_environment(agents, tmp_path, monkeypatch):
    job = tmp_path / "job"
    argv, env = rv.agent_command("claude", job, "review it", mode="local")
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["sandbox"]["enabled"] and settings["sandbox"]["network"]["allowedDomains"] == []
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False and "~/.codex" in settings["sandbox"]["filesystem"]["denyRead"]
    assert "WebFetch" in settings["permissions"]["deny"] and "Write(./out/**)" in settings["permissions"]["allow"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk" and "--strict-mcp-config" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    argv, env2 = rv.agent_command("codex", job, "review it", mode="local")
    assert argv[argv.index("--sandbox") + 1] == "workspace-write" and "sandbox_workspace_write.network_access=false" in argv
    assert "--ephemeral" in argv and argv[argv.index("--cd") + 1] == str(job) and argv[-1] == "review it"
    # none of the person's own Codex: no config.toml, no exec-policy rules, no memories read or written
    assert {"--ignore-user-config", "--ignore-rules", "memories.use_memories=false",
            "memories.generate_memories=false"} <= set(argv)
    for e in (env, env2):
        assert not any(k.startswith("QCCD_") for k in e) and "PATH" in e
    # a review home of its own: the CLI's home is that folder, not the person's
    monkeypatch.setenv("QCCD_REVIEW_CODEX_HOME", str(tmp_path / "agents" / "codex"))
    monkeypatch.setenv("QCCD_REVIEW_CLAUDE_CONFIG_DIR", str(tmp_path / "agents" / "claude"))
    assert rv.agent_command("codex", job, "x", mode="local")[1]["CODEX_HOME"] == str(tmp_path / "agents" / "codex")
    assert rv.agent_command("claude", job, "x", mode="local")[1]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "agents" / "claude")
    assert rv.reviewer_status("codex", fresh=True)["signin"] == "python -m qccd.official.devstack login codex"
    # the server's own key, given to that agent's process and nothing else
    monkeypatch.setenv("QCCD_REVIEW_ANTHROPIC_API_KEY", "sk-ant-test")
    _, env3 = rv.agent_command("claude", job, "x", mode="api_key")
    assert env3["ANTHROPIC_API_KEY"] == "sk-ant-test" and "QCCD_REVIEW_ANTHROPIC_API_KEY" not in env3


def test_a_persons_own_cli_sign_in_counts_only_on_a_development_server(agents, monkeypatch):
    st = rv.reviewer_status("claude", fresh=True)
    assert st["available"] and st["mode"] == "local" and "dev@example.com" in st["account"]
    assert rv.reviewer_status("codex", fresh=True)["account"] == "ChatGPT"
    monkeypatch.delenv("QCCD_OFFICIAL_DEV")
    st = rv.reviewer_status("claude", fresh=True)
    assert not st["available"] and "API key" in st["why"]
    monkeypatch.setenv("QCCD_REVIEW_ANTHROPIC_API_KEY", "sk-ant-test")
    assert rv.reviewer_status("claude", fresh=True)["mode"] == "api_key"
    monkeypatch.delenv("QCCD_REVIEW")
    assert "off" in rv.reviewer_status("claude", fresh=True)["why"]


@pytest.mark.parametrize("reviewer", ["codex", "claude"])
def test_a_review_runs_after_the_grade_and_explains_the_decision(agents, svc, tmp_path, reviewer):
    sid = _graded(svc, tmp_path, "ghz-chain4-hot", reviewer)
    up = svc.dev_uploader()
    assert svc.review(sid, up)["status"] == "queued"
    rv.run_reviewer(svc, tmp_path / "reviews", once=True)
    r = svc.review(sid, up)
    assert r["status"] == "done" and r["decision"] == "not_accepted"
    assert any(x.startswith("breaks R7") for x in r["reasons"]) and r["review_md"].startswith("# Review")
    assert any("cat rule-check/rule-check.md" in p["text"] for p in r["progress"]) and r["usage"]["tokens"] == 1000
    assert r["rule_check"]["rules"] and r["rule_check_md"].startswith("# Rule check")
    seen = agents["seen"]()
    assert not any(k.startswith("QCCD_") for k in seen["env"])          # no token, no database URL
    assert svc.submission(sid, up)["review"]["decision"] == "not_accepted"
    assert not list((tmp_path / "reviews").iterdir())                   # the job folder is gone


def test_without_an_agent_the_decision_and_the_fix_still_come(agents, svc, tmp_path):
    """'No agent': the rule check decides and every rule is listed, no model is asked; the re-cooled
    programme the rule check tested is kept for the person to download."""
    from starlette.testclient import TestClient
    sid = _graded(svc, tmp_path, "ghz-chain4-hot", reviewer="none")
    rv.run_reviewer(svc, tmp_path / "reviews", once=True)
    r = svc.review(sid, svc.dev_uploader())
    assert r["status"] == "done" and r["decision"] == "not_accepted" and r["review_md"] is None
    assert r["reviewer_title"] == "No agent" and r["rule_check"]["rules"]
    assert [f["name"] for f in r["files"]] == ["recooled.tsir.json"]
    got = TestClient(create_app(svc), base_url="http://127.0.0.1:8300", client=("127.0.0.1", 50000)).get(r["files"][0]["url"])
    assert got.status_code == 200 and "attachment" in got.headers["content-disposition"]
    assert sum(1 for i in got.json()["instructions"] if i["type"] == "cool") > 0      # the fix: cooling put back
    assert "To download" in review_page(r, "n0nce")


def test_a_review_that_fails_or_runs_too_long_leaves_the_decision(agents, svc, tmp_path, monkeypatch):
    agents["mode"]("silent")
    sid = _graded(svc, tmp_path, "bell-on-ghz")
    rv.run_reviewer(svc, tmp_path / "reviews", once=True)
    r = svc.review(sid, svc.dev_uploader())
    assert r["status"] == "failed" and "wrote no review" in r["error"]
    assert r["decision"] == "not_accepted" and "2-qubit circuit" in r["reasons"][0]
    agents["mode"]("slow")
    sid = _graded(svc, tmp_path, "ghz-chain4")
    row = rv.claim_review(svc)
    assert rv.process_review(svc, row, tmp_path / "reviews", timeout=2) == "failed"
    r = svc.review(sid, svc.dev_uploader())
    assert "stopped" in r["error"] and r["decision"]


def test_the_page_escapes_what_the_agent_wrote(agents, svc, tmp_path):
    agents["mode"]("xss")
    sid = _graded(svc, tmp_path, "ghz-chain4")
    rv.run_reviewer(svc, tmp_path / "reviews", once=True)
    page = review_page(svc.review(sid, svc.dev_uploader()), "n0nce")
    assert "<script>alert(1)" not in page and 'href="javascript:' not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_only_the_submitter_asks_and_reviews_can_be_off(agents, svc, tmp_path, monkeypatch):
    sid = _graded(svc, tmp_path, "ghz-chain4", reviewer="")
    other = {**svc.dev_uploader(), "id": "u_other", "kind": None}
    with pytest.raises(OfficialError) as e:
        svc.request_review(sid, other, "codex")
    assert e.value.code == "not_found"
    first = svc.request_review(sid, svc.dev_uploader(), "codex")
    assert svc.request_review(sid, svc.dev_uploader(), "codex")["review_id"] == first["review_id"]   # one at a time
    monkeypatch.delenv("QCCD_REVIEW")
    with pytest.raises(OfficialError) as e:
        svc.request_review(sid, svc.dev_uploader(), "claude")
    assert e.value.code == "reviews_off"


def test_the_console_and_the_review_api_over_http(agents, svc, monkeypatch):
    from starlette.testclient import TestClient
    app = create_app(svc)
    local = TestClient(app, base_url="http://127.0.0.1:8300", client=("127.0.0.1", 50000))
    assert {r["id"]: r["available"] for r in local.get("/v1/reviewers").json()["reviewers"]} == {"claude": True, "codex": True}
    assert local.get("/review").status_code == 200
    body = {"example": "ghz-chain4"}
    assert local.post("/review/submit", json=body, headers={"X-QCCD-Reviewer": "codex"}).status_code == 403
    assert local.post("/review/submit", json=body, headers={"X-QCCD-Console": "1", "X-QCCD-Reviewer": "codex",
                                                             "Origin": "https://elsewhere.example"}).status_code == 403
    r = local.post("/review/submit", json=body, headers={"X-QCCD-Console": "1", "X-QCCD-Reviewer": "codex"})
    assert r.status_code == 202 and r.json()["page"].startswith("/review/os_")
    page = local.get(r.json()["page"])
    assert page.status_code == 200 and "nonce-" in page.headers["content-security-policy"]
    assert local.get(f"/v1/submissions/{r.json()['submission_id']}/review?brief=1").json()["status"] == "queued"
    # elsewhere than this computer, or on a server that is not a development one: no console
    assert TestClient(app).get("/review").status_code == 404
    # a page that rebinds its own domain to 127.0.0.1 still names itself in Host: no console either
    assert local.get("/review", headers={"Host": "evil.example"}).status_code == 404
    monkeypatch.delenv("QCCD_OFFICIAL_DEV")
    assert local.get("/review").status_code == 404
    assert local.get(r.json()["page"]).status_code == 404          # a private submission, and nobody signed in


def test_the_studio_sends_the_reviewer_and_shows_the_review(tmp_path, monkeypatch):
    """The Studio's Submit carries the person's reviewer to the server (X-QCCD-Reviewer), and the
    publication shows the server's review, with where to read it, as the server reports it."""
    import qccd.workspace.account as account
    from qccd.workspace.app import Workspace, WorkspaceError
    server = "http://127.0.0.1:9"
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({server: {"token": "qccdk_x", "account": {"id": "7", "name": "Ada"}}}), encoding="utf-8")
    monkeypatch.setenv("QCCD_CREDENTIALS", str(creds))
    monkeypatch.setenv("QCCD_OFFICIAL_URL", server)
    sent: list = []

    def official(method, path, *, raw=None, token=None, server=None, headers=None, timeout=None, **kw):
        sent.append((method, path, dict(headers or {})))
        if method == "POST" and path == "/v1/submissions":
            return {"submission_id": "os_y", "by": "Ada", "status": "queued"}
        if path == "/v1/submissions/os_y":
            return {"submission_id": "os_y", "status": "ineligible", "by": "Ada",
                    "report": {"eligible": False, "eligibility": {"reasons": ["rules: failed"]}},
                    "review": {"id": "rv_1", "reviewer": "codex", "model": "gpt-6.1-sol", "status": "reviewing",
                               "decision": "not_accepted", "page": "/review/os_y"}}
        if path == "/v1/reviewers":
            return {"enabled": True, "reviewers": [{"id": "codex", "title": "ChatGPT", "available": True}]}
        return {"tasks": [], "rows": []}
    monkeypatch.setattr(account, "official_call", official)
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    try:
        with pytest.raises(WorkspaceError):
            ws.request_publication({"kind": "human", "id": "x"}, board="ghz4", reviewer="gemini")
        now = time.time()
        with ws.store.tx() as db:
            db.execute("INSERT INTO publications(id, branch, board, submission_id, display_name, visibility, state, "
                       "requested_by, approved_by, server, created_at, updated_at, reviewer) "
                       "VALUES('pb_1','main','ghz4@1','sub_1','GHZ','public','uploading',?,?,?,?,?, 'codex')",
                       (json.dumps({"kind": "human"}), json.dumps({"kind": "human"}), server, now, now))
        monkeypatch.setattr(ws, "prepare_publish", lambda *a, **k: {"bundle_digest": "sha256:x"})
        monkeypatch.setattr(ws, "approve_publish", lambda *a, **k: {"approval_id": "ap_1"})
        monkeypatch.setattr(ws, "publish", lambda aid, up: {"server": up(b"PK", {"task": "ghz4@1", "visibility": "public"})})
        ws._upload("pb_1")
        post = next(h for m, p, h in sent if m == "POST")
        assert post["X-QCCD-Reviewer"] == "codex"
        p = ws._pub_json(ws._fetch_official(ws._pub_row("pb_1")))
        assert p["reviewer"] == "codex" and p["official"]["review"]["status"] == "reviewing"
        assert p["official"]["review"]["page"] == f"{server}/review/os_y"
        assert ws.board_view(None)["reviewers"]["reviewers"][0]["id"] == "codex"
    finally:
        ws.close()


def test_a_reviewer_signs_in_to_a_home_of_its_own(agents, tmp_path):
    """`devstack login` runs the CLI's own sign-in with its home set to the review home: a review
    then never sees the person's own instructions or memories, and their usual sign-in stays."""
    from qccd.official import devstack
    root = tmp_path / "stack"
    assert devstack.login("codex", root) == 0
    seen = agents["seen"]()
    assert seen["argv"] == ["login"] and "CODEX_HOME" in seen["env"]
    assert devstack.login("claude", root) == 0
    assert agents["seen"]()["argv"] == ["auth", "login"] and "CLAUDE_CONFIG_DIR" in agents["seen"]()["env"]
    assert (root / "agents" / "codex").is_dir() and (root / "agents" / "claude").is_dir()
    assert devstack.login("gemini", root) == 2


def test_an_upload_stands_when_its_review_cannot_run(agents, svc, monkeypatch):
    """A Studio that names a reviewer this server cannot run still uploads: the review's problem is
    said beside the submission, never in its place."""
    monkeypatch.delenv("QCCD_REVIEW")
    task, archive = example_archive("ghz-chain4")
    out = svc.submit(svc.dev_uploader(), archive, task=task, visibility="private", reviewer="claude")
    assert out["status"] == "queued" and out["review_error"]["code"] == "reviews_off" and "review" not in out


def test_every_example_is_a_bundle_for_its_board():
    for name, ex in EXAMPLES.items():
        task, archive = example_archive(name)
        assert task == ex["board"] and archive[:2] == b"PK", name
