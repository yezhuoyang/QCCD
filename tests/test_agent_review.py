"""An agent review on the person's own computer (qccd/review, qccd/workspace/reviews.py): the Debug
page's "Run agent review".

Docker is a stand-in here (tests/fake_docker.py): it plays the CLIs' own sign-ins and an agent that
writes its report, so what is tested is everything QCCD does around them -- the rule check (the real
evaluator, on the Bell pair that fails R7 at step 7), the container's command, the egress log, the
token kept and never shown, what is kept of the report, and who may start what.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from qccd.review import agents, container, engine

REPO = Path(__file__).resolve().parents[1]
C = REPO / "qccd" / "site" / "compiled"
REQUEST = {"title": "Bell pair, before cooling", "arch": (C / "chain4.arch.json").read_text(encoding="utf-8"),
           "program": (C / "bell" / "prog.tsir.json").read_text(encoding="utf-8"), "model": None, "rules": "standard",
           "assertions": "", "steps": 11, "link": "http://127.0.0.1:47100/web/debug/?src=compiled%2Fbell.html",
           "source": {"kind": "page", "path": "compiled/bell.html"}}


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """The stand-in docker on QCCD_DOCKER, a home of the test's own, and the stand-in's log."""
    script = REPO / "tests" / "fake_docker.py"
    log = tmp_path / "docker.log"
    if os.name == "nt":
        exe = tmp_path / "docker.cmd"
        exe.write_text(f'@set "FAKE_DOCKER_LOG={log}"\r\n@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "docker"
        exe.write_text(f"#!{sys.executable}\nFAKE_LOG = {str(log)!r}\n" + script.read_text(encoding="utf-8"), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("QCCD_DOCKER", str(exe))
    monkeypatch.setenv("QCCD_HOME", str(tmp_path / "home"))
    container._READY.clear()
    yield log
    container._READY.clear()


def calls(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []


def wait(pred, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


# ---------------------------------------------------------------------- the pieces

def test_the_egress_proxy_lets_through_only_the_named_hosts():
    spec = importlib.util.spec_from_file_location("egress_proxy", REPO / "qccd" / "review" / "image" / "egress_proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    allow = list(agents.REVIEWERS["claude"]["hosts"])
    assert proxy.allowed("api.anthropic.com", "443", allow, {"443"})
    assert proxy.allowed("API.Anthropic.com.", "443", allow, {"443"})
    assert not proxy.allowed("api.anthropic.com", "80", allow, {"443"})
    assert not proxy.allowed("anthropic.com.evil.example", "443", allow, {"443"})
    assert not proxy.allowed("chatgpt.com", "443", allow, {"443"})


def test_a_claude_token_is_kept_for_this_user_and_never_shown(tmp_path, monkeypatch):
    monkeypatch.setenv("QCCD_HOME", str(tmp_path / "home"))
    assert agents.signed_in("claude") == (False, None)
    with pytest.raises(ValueError):
        agents.save_claude_token("not a token")
    tok = "sk-ant-oat01-" + "x" * 30
    agents.save_claude_token(tok + "\n")
    assert agents.claude_token() == tok
    assert agents.signed_in("claude")[0]
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "home" / "reviewers" / "claude" / "oauth-token").stat().st_mode) == 0o600
    assert tok not in agents.scrub(f"here: {tok} there")
    agents.sign_out("claude")
    assert agents.claude_token() is None


def test_a_terminal_screen_reads_as_text():
    assert agents.clean_output("\x1b[1CWelcome\x1b[1Cto\x1b[2CClaude\r\nnext") == " Welcome to  Claude\nnext"


def test_the_notes_kept_are_steps_of_the_programme(tmp_path):
    p = tmp_path / "notes.json"
    p.write_text(json.dumps([{"step": 3, "rule": "R7", "text": "hot"}, {"step": 0, "text": "x"}, {"step": 12, "text": "x"},
                             {"step": True, "text": "x"}, {"step": 2, "text": " "}, "nonsense"]))
    assert engine.read_notes(p, 11) == [{"step": 3, "rule": "R7", "text": "hot"}]


def test_what_looks_like_a_credential_is_removed():
    text, n = engine.redact("a sk-ant-oat01-" + "z" * 30 + " b ghp_" + "y" * 30 + ' c "refresh_token": "' + "w" * 30 + '"')
    assert n == 3 and "zzz" not in text and "yyy" not in text and "www" not in text


# ---------------------------------------------------------------------- signing in, in Docker

def test_claude_signs_in_in_docker_and_its_token_is_kept_not_shown(docker):
    s = agents.SigninSession("claude").start()
    v = wait(lambda: (lambda x: x if x["url"] and x["needs_code"] else None)(s.view()))
    assert v["url"].startswith("https://claude.com/cai/oauth/authorize?") and v["state"] == "waiting"
    s.send("good-code#state")
    wait(lambda: s.view()["state"] == "signed_in")
    assert agents.claude_token() == "sk-ant-oat01-" + "T" * 40
    assert "TTTT" not in json.dumps(s.view()) and "TTTT" not in s.out
    run = [c for c in calls(docker) if c["argv"][:1] == ["run"]][0]["argv"]
    assert "-i" in run and "script" in run and any("claude setup-token" in a for a in run)
    assert "--cap-drop" in run and "no-new-privileges" in run


def test_a_wrong_code_fails_the_sign_in_and_says_so(docker):
    s = agents.SigninSession("claude").start()
    wait(lambda: s.view()["needs_code"])
    s.send("bad-code")
    v = wait(lambda: (lambda x: x if x["state"] == "failed" else None)(s.view()))
    assert "Invalid code" in v["error"] and agents.claude_token() is None


def test_chatgpt_signs_in_with_a_device_code_in_docker(docker):
    s = agents.SigninSession("codex").start()
    wait(lambda: s.view()["state"] == "signed_in")
    v = s.view()
    assert v["url"] == "https://auth.openai.com/codex/device" and v["code"] == "ABCD-12345"
    assert agents.signed_in("codex") == (True, "person@example.org")
    run = [c for c in calls(docker) if c["argv"][:1] == ["run"]][0]["argv"]
    assert any("--device-auth" in a for a in run) and any(a.endswith("dst=/profile") for a in run)


# ---------------------------------------------------------------------- one review

def test_a_review_checks_every_rule_then_the_agent_explains_in_docker(docker, tmp_path):
    agents.save_claude_token("sk-ant-oat01-" + "k" * 30)
    said = []
    res = engine.run_review(job=tmp_path / "job", request=REQUEST, reviewer="claude", on_progress=said.extend)
    assert res["status"] == "done", res["error"]
    assert res["check"]["failing"] == ["R7"] and res["summary"]["steps"] == 11
    r7 = [r for r in res["check"]["rules"] if r["id"] == "R7"][0]
    assert r7["failures"][0]["step"] == 7
    assert "step 7" in res["review_md"] and res["model"] == "claude-test-model"
    assert "TTTT" not in res["review_md"] and res["redacted"] == 1
    assert res["notes"] == [{"step": 7, "rule": "R7", "text": "q0 is hot here"}]
    assert res["egress"] == {"allowed": ["api.anthropic.com:443"], "refused": ["example.com:443"]}
    assert res["usage"]["tokens"] == 150 and any("Rule check: 31 rules" in s for s in said)
    assert not (tmp_path / "job").exists()                       # the folder is temporary
    log = calls(docker)
    agent = [c for c in log if c["argv"][:1] == ["run"] and "--network" in c["argv"] and "-d" not in c["argv"]][0]
    a = agent["argv"]
    # the token goes by name, from the client's environment: never in an argv
    assert agent["by_name"] == {"CLAUDE_CODE_OAUTH_TOKEN": True} and not any("kkkk" in x for x in a)
    assert "--read-only" in a and "ALL" in a and "no-new-privileges" in a
    assert any(x.endswith("dst=/opt/qccd/qccd,readonly") for x in a)
    net = a[a.index("--network") + 1]
    assert any(c["argv"][:3] == ["network", "create", "--internal"] and net in c["argv"] for c in log)
    prompt = a[a.index("-p") + 1]
    assert "guide.md" in prompt and "check.json" in prompt and "failing: R7" in prompt
    assert any(c["argv"][:2] == ["network", "rm"] for c in log)  # torn down


def test_codex_reviews_the_same_way(docker, tmp_path):
    (tmp_path / "home" / "reviewers" / "codex").mkdir(parents=True)
    (tmp_path / "home" / "reviewers" / "codex" / "auth.json").write_text(json.dumps({"tokens": {"id_token": "a.e30.c"}}))
    res = engine.run_review(job=tmp_path / "job", request=REQUEST, reviewer="codex", model="gpt-test")
    assert res["status"] == "done" and res["model"] == "gpt-test" and res["usage"]["tokens"] == 100
    a = [c for c in calls(docker) if c["argv"][:1] == ["run"] and "--network" in c["argv"] and "-d" not in c["argv"]][0]["argv"]
    assert "exec" in a and "--ignore-user-config" in a and a[a.index("-m") + 1] == "gpt-test"
    assert "CODEX_HOME=/profile" in a


def test_a_programme_the_debugger_refuses_fails_the_review_with_its_reason(docker, tmp_path):
    agents.save_claude_token("sk-ant-oat01-" + "k" * 30)
    res = engine.run_review(job=tmp_path / "job", request={**REQUEST, "program": '{"not": "a programme"}'}, reviewer="claude")
    assert res["status"] == "failed" and "debugger" in res["error"]
    assert not [c for c in calls(docker) if c["argv"][:1] == ["run"]]       # no agent was started


# ---------------------------------------------------------------------- in the workspace

fastapi = pytest.importorskip("fastapi")
from starlette.testclient import TestClient  # noqa: E402

from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.reviews import clean_request  # noqa: E402
from qccd.workspace.service import ServiceState, create_app  # noqa: E402

BASE = "http://127.0.0.1:8765"
AGENT = {"Authorization": "Bearer agent-tok"}
OWNER = {"Authorization": "Bearer owner-tok"}


@pytest.fixture
def svc(tmp_path, docker):
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    state = ServiceState(ws, {"port": 8765, "agent_token": "agent-tok", "owner_token": "owner-tok", "pid": os.getpid(),
                              "web_port": 47100})
    yield ws, TestClient(create_app(state), base_url=BASE)
    ws.close()


def test_only_the_person_starts_a_review_or_signs_in(svc):
    ws, c = svc
    assert c.post("/api/reviews", json={"request": REQUEST, "reviewer": "claude"}, headers=AGENT).status_code == 403
    assert c.post("/api/reviewers/claude/signin", json={}, headers=AGENT).status_code == 403
    r = c.post("/api/reviews", json={"request": REQUEST, "reviewer": "claude"}, headers=OWNER)
    assert r.status_code == 409 and r.json()["error"]["code"] == "needs_signin"
    assert c.get("/api/reviews", headers=AGENT).json() == {"reviews": []}      # an agent reads them


def test_a_review_runs_in_the_workspace_and_its_page_shows_it(svc):
    ws, c = svc
    agents.save_claude_token("sk-ant-oat01-" + "k" * 30)
    r = c.post("/api/reviews", json={"request": REQUEST, "reviewer": "claude", "model": "opus"}, headers=OWNER)
    assert r.status_code == 200, r.text
    rid = r.json()["review_id"]
    v = wait(lambda: (lambda x: x if x["status"] not in ("queued", "checking", "reviewing") else None)(
        c.get(f"/api/reviews/{rid}", headers=AGENT).json()))
    assert v["status"] == "done" and v["verdict"]["failing"] == ["R7"] and v["notes"][0]["step"] == 7
    assert "step 7" in v["review_md"] and v["link"] == REQUEST["link"]
    d = ws.root / ".qccd" / "reviews" / rid
    assert json.loads((d / "request.json").read_text())["model_choice"] == "opus"
    assert (d / "input" / "program.tsir.json").read_text() == REQUEST["program"]
    page = c.get(f"/reviewview/{rid}", headers=OWNER)
    assert page.status_code == 200 and '"review_id": "' + rid in page.text and "default-src 'none'" in page.headers["content-security-policy"]
    new = c.get("/review/new", headers=OWNER)
    assert new.status_code == 200 and "http://127.0.0.1:47100" in new.text           # the mirror may hand one over
    assert c.get("/reviewview/rv_000000000000", headers=OWNER).status_code == 404
    assert c.get("/reviewview/../etc", headers=OWNER).status_code == 404
    # the same programme again, by the other agent, from its own default model
    (Path(os.environ["QCCD_HOME"]) / "reviewers" / "codex").mkdir(parents=True)
    (Path(os.environ["QCCD_HOME"]) / "reviewers" / "codex" / "auth.json").write_text('{"tokens": {"id_token": "a.e30.c"}}')
    again = c.post(f"/api/reviews/{rid}/again", json={"reviewer": "codex"}, headers=OWNER).json()
    d2 = ws.root / ".qccd" / "reviews" / again["review_id"]
    assert json.loads((d2 / "request.json").read_text())["model_choice"] is None


def test_a_sign_in_from_the_page(svc):
    ws, c = svc
    s = c.post("/api/reviewers/claude/signin", json={}, headers=OWNER).json()
    v = wait(lambda: (lambda x: x if x["needs_code"] else None)(c.get(f"/api/signins/{s['signin_id']}", headers=OWNER).json()))
    assert v["url"].startswith("https://claude.com/")
    assert c.post(f"/api/signins/{s['signin_id']}/code", json={"code": "good-code#state"}, headers=AGENT).status_code == 403
    assert c.post(f"/api/signins/{s['signin_id']}/code", json={"code": "good-code#state"}, headers=OWNER).status_code == 200
    wait(lambda: c.get(f"/api/signins/{s['signin_id']}", headers=OWNER).json()["state"] == "signed_in")
    who = c.get("/api/reviewers", headers=OWNER).json()
    assert [r["signed_in"] for r in who["reviewers"]] == [True, False] and who["docker"]["ready"]
    assert "TTTT" not in json.dumps(who)


def test_a_review_whose_service_stopped_says_so(svc):
    ws, c = svc
    d = ws.root / ".qccd" / "reviews" / "rv_0123456789ab"
    d.mkdir(parents=True)
    (d / "request.json").write_text(json.dumps({"title": "t", "reviewer": "claude"}))
    (d / "state.json").write_text(json.dumps({"status": "reviewing", "pid": 999999999, "created_at": time.time()}))
    v = c.get("/api/reviews/rv_0123456789ab", headers=OWNER).json()
    assert v["status"] == "failed" and "stopped" in v["error"]


def test_a_request_is_checked_before_anything_runs():
    from qccd.workspace.core import WorkspaceError
    for bad in ({**REQUEST, "arch": "not json"}, {**REQUEST, "program": ""}, {**REQUEST, "rules": "mine"},
                {**REQUEST, "model": "deck"}, {**REQUEST, "assertions": "x" * 70000}):
        with pytest.raises(WorkspaceError):
            clean_request(bad)
    assert clean_request({**REQUEST, "link": "javascript:alert(1)"})["link"] is None
    assert clean_request({**REQUEST, "link": "https://evil.example/debug/"})["link"] is None
    assert clean_request({**REQUEST, "link": "https://qccd.academy/debug/?src=x"})["link"] == "https://qccd.academy/debug/?src=x"
