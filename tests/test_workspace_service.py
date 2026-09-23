"""The local workspace service: its security boundary, its event stream, and the MCP
adapter over real stdio (including the Claude Code channel push, as a contract test --
a real Claude Code session is not started here)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from starlette.testclient import TestClient  # noqa: E402

from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.service import ServiceState, create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def svc(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    info = {"port": PORT, "agent_token": "agent-tok", "owner_token": "owner-tok", "pid": os.getpid()}
    state = ServiceState(ws, info)
    client = TestClient(create_app(state), base_url=BASE)
    yield ws, state, client
    ws.close()


AGENT = {"Authorization": "Bearer agent-tok"}
OWNER = {"Authorization": "Bearer owner-tok"}


def _paired(state, client):
    code = state.new_pair_code()
    r = client.post("/api/pair", json={"code": code})
    assert r.status_code == 200
    assert client.post("/api/pair", json={"code": code}).status_code == 403          # one use
    return r.json()["csrf"]


def test_host_and_origin_are_checked(svc):
    ws, state, c = svc
    assert c.get("/api/health", headers={"Host": "evil.example:8765"}).status_code == 421
    assert c.get("/api/health", headers={"Host": "127.0.0.1:9999"}).status_code == 421
    assert c.get("/api/context", headers={**AGENT, "Origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/health").json()["workspace_id"] == ws.id
    r = c.get("/api/health")
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    assert r.headers["x-frame-options"] == "DENY"


def test_nothing_without_credentials(svc):
    ws, state, c = svc
    assert c.get("/api/context").status_code == 401
    assert c.get("/api/context", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/events").status_code == 401


def test_browser_writes_need_the_csrf_header(svc):
    ws, state, c = svc
    csrf = _paired(state, c)
    body = {"expected_revision": 0, "request_id": "b1", "mode": "apply",
            "operations": [{"type": "move_site", "id": "C0", "pos": [0, 1]}]}
    assert c.post("/api/change-sets", json=body).status_code == 403                  # cookie alone
    assert c.post("/api/change-sets", json=body, headers={"X-QCCD-CSRF": "wrong"}).status_code == 403
    r = c.post("/api/change-sets", json=body, headers={"X-QCCD-CSRF": csrf})
    assert r.status_code == 200 and r.json()["attribution"]["actor"]["kind"] == "human"
    assert c.get("/api/whoami").json()["csrf"] == csrf                                  # same-origin read


def test_the_agent_token_cannot_act_as_the_person(svc):
    ws, state, c = svc
    for method, path, body in [
        ("POST", "/api/prompts/send", {"body": {"text": "hi"}}),
        ("POST", "/api/publish/approve", {"submission_id": "x", "bundle_digest": "y", "params": {}}),
        ("POST", "/api/pair-code", {}),
        ("POST", "/api/shutdown", {}),
        ("POST", "/api/views", {}),
        ("POST", "/api/sessions/s_x/stop", {}),
        ("POST", "/api/sessions/codex/connect", {"start_thread": True}),
    ]:
        r = c.request(method, path, json=body, headers=AGENT)
        assert r.status_code == 403, (path, r.status_code, r.text)
    r = c.post("/api/change-sets", headers=AGENT, json={
        "expected_revision": 0, "request_id": "u", "mode": "apply",
        "operations": [{"type": "protect", "keys": ["node:C0"]}]})
    assert r.status_code == 200
    r = c.post("/api/change-sets", headers=OWNER, json={
        "expected_revision": 1, "request_id": "v", "mode": "apply",
        "operations": [{"type": "protect", "keys": ["node:C1"]}]})
    r = c.post("/api/change-sets", headers=AGENT, json={
        "expected_revision": 2, "request_id": "w", "mode": "apply",
        "operations": [{"type": "unprotect", "keys": ["node:C1"]}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


def test_malformed_and_oversized_bodies(svc):
    ws, state, c = svc
    r = c.post("/api/change-sets", headers={**AGENT, "Content-Type": "application/json"},
               content=b'{"expected_revision": 0, "expected_revision": 1}')
    assert r.status_code == 422 and r.json()["error"]["code"] == "duplicate_key"
    r = c.post("/api/change-sets", headers={**AGENT, "Content-Type": "application/json"},
               content=b'{"expected_revision": NaN}')
    assert r.status_code == 422
    r = c.post("/api/change-sets", headers={**AGENT, "Content-Length": str(64 * 1024 * 1024)}, content=b"{}")
    assert r.status_code in (413, 400)


def test_imports_cannot_escape_the_workspace(svc, tmp_path):
    ws, state, c = svc
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    r = c.post("/api/import", headers=AGENT, json={"path": str(outside)})
    assert r.status_code == 403 and r.json()["error"]["code"] == "path_escape"
    r = c.post("/api/import", headers=AGENT, json={"path": "../outside.json"})
    assert r.status_code == 403


def test_hostile_labels_are_data(svc):
    ws, state, c = svc
    csrf = _paired(state, c)
    v = c.post("/api/views", json={"label": "<img src=x onerror=alert(1)>"}, headers={"X-QCCD-CSRF": csrf}).json()
    text = "</script><script>alert(1)</script>"
    r = c.post("/api/prompts/send", json={"body": {"text": text}, "view_id": v["id"]},
               headers={"X-QCCD-CSRF": csrf, "X-QCCD-View": v["id"]})
    p = c.get(f"/api/prompts/{r.json()['prompt_id']}", headers=AGENT).json()
    assert p["latest"]["body"]["text"] == text                 # stored as data, rendered with textContent
    page = c.get("/studio").text
    assert page.count('<script id="qccd-live-config"') == 1 and "alert(1)" not in page


# ---------------------------------------------------------------------- MCP over real stdio

def _mcp_python() -> str | None:
    if importlib.util.find_spec("mcp") is not None:
        return sys.executable
    for cand in (REPO / ".venv" / "Scripts" / "python.exe", REPO / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return None


MCP_PY = _mcp_python()
needs_mcp = pytest.mark.skipif(MCP_PY is None, reason="the MCP SDK is not installed (requirements-agent.txt)")


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A real service process for the workspace, found through the runtime directory."""
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    from qccd.workspace.runtime import ensure_service, service_request
    info = ensure_service(tmp_path / "ws", python=MCP_PY or sys.executable)
    yield tmp_path / "ws", info
    try:
        service_request(info, "POST", "/api/shutdown", {}, token="owner")
    except Exception:
        pass


def _run_mcp(root, script, *args, timeout=180):
    sp = Path(root).parent / f"mcp-{time.time_ns()}.json"
    sp.write_text(json.dumps(script))
    return subprocess.Popen([MCP_PY, str(REPO / "tests" / "workspace_mcp_client.py"), str(root), str(sp), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))


@needs_mcp
def test_mcp_tools_share_the_one_service(live):
    root, info = live
    from qccd.workspace.runtime import service_request
    p = _run_mcp(root, [
        {"tool": "qccd_get_context", "args": {}},
        {"tool": "qccd_apply_change_set", "args": {"expected_revision": 0, "request_id": "m1", "mode": "apply",
                                                   "operations": [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"}]}},
        {"tool": "qccd_apply_change_set", "args": {"expected_revision": 0, "request_id": "m2", "mode": "apply",
                                                   "operations": [{"type": "move_site", "id": "T9", "pos": [2, 3]}]}},
        {"tool": "qccd_read_reference", "args": {"section": "operations"}},
    ])
    out, err = p.communicate(timeout=180)
    res = json.loads(out)
    assert "qccd_apply_change_set" in res["tools"] and "run_shell" not in json.dumps(res["tools"])
    assert res["results"][0]["structured"]["revision"] == 0
    assert res["results"][1]["structured"]["status"] == "committed"
    assert res["results"][2]["is_error"] and res["results"][2]["structured"]["error"]["code"] == "conflict"
    ctx = service_request(info, "GET", "/api/context", token="owner")
    assert ctx["revision"] == 1                                   # the same design the CLI sees


@needs_mcp
def test_claude_channel_pushes_studio_prompts_and_reading_acknowledges(live):
    root, info = live
    from qccd.workspace.runtime import service_request
    p = _run_mcp(root, [{"tool": "qccd_get_context", "args": {}}, {"sleep": 8},
                        {"tool": "qccd_get_context", "args": {}}], "--client", "claude", "--channel")
    sid = None
    t0 = time.time()
    while sid is None and time.time() - t0 < 60:
        for s in service_request(info, "GET", "/api/sessions", token="owner")["sessions"]:
            if s["mode"] == "channel":
                sid = s["id"]
        time.sleep(0.3)
    assert sid, "the channel adapter never registered"
    time.sleep(1.5)
    sent = service_request(info, "POST", "/api/prompts/send",
                           {"body": {"text": "keep C1, shorten the rest"}, "context": {"target_session": sid}},
                           token="owner")
    t0 = time.time()
    state = None
    while time.time() - t0 < 20:
        state = service_request(info, "GET", f"/api/prompts/{sent['prompt_id']}", token="owner")["deliveries"][0]["state"]
        if state != "queued" and state != "sending":
            break
        time.sleep(0.3)
    assert state == "uncertain"            # pushed; Claude Code does not acknowledge
    out, err = p.communicate(timeout=120)
    res = json.loads(out)
    assert res["capabilities"]["experimental"] == {"claude/channel": {}}
    pushes = [n for n in res["notifications"] if n.get("method") == "notifications/claude/channel"]
    assert len(pushes) == 1
    params = pushes[0]["params"]
    assert "keep C1, shorten the rest" in params["content"]
    assert params["meta"]["prompt_id"] == sent["prompt_id"] and all(k.isidentifier() for k in params["meta"])
    assert res["results"][-1]["structured"]["unread_prompts"][0]["prompt_id"] == sent["prompt_id"]
    d = service_request(info, "GET", f"/api/prompts/{sent['prompt_id']}", token="owner")["deliveries"][0]
    assert d["state"] == "accepted"         # reading it through a tool is the acknowledgement


def test_events_stream_replays_from_a_cursor_and_reports_gaps(live):
    """Against the real service process: Starlette's TestClient buffers a whole response,
    so an endless event stream can only be tested over a real socket."""
    import http.client
    root, info = live
    from qccd.workspace.runtime import service_request
    service_request(info, "POST", "/api/change-sets", {"expected_revision": 0, "request_id": "e1", "mode": "apply",
                    "operations": [{"type": "move_site", "id": "C0", "pos": [0, 1]}]}, token="owner")

    def read_events(cursor, want, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=20)
        conn.request("GET", f"/api/events?cursor={cursor}", headers={"Authorization": f"Bearer {info['agent_token']}",
                                                                     **(headers or {})})
        r = conn.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        got, ids = [], []
        while want not in got:
            line = r.fp.readline().decode().strip()
            if line.startswith("event: "):
                got.append(line[7:])
            if line.startswith("id: "):
                ids.append(int(line[4:]))
        conn.close()
        return got, ids
    got, ids = read_events(0, "design.committed")
    assert got[0] == "hello" and ids == sorted(ids)
    # a reconnect resumes from Last-Event-ID, not from the start
    got2, ids2 = read_events(0, "hello", headers={"Last-Event-ID": str(ids[-1])})
    assert got2 == ["hello"]
    import sqlite3                        # never a second Workspace on a live database (it would recover())
    db = sqlite3.connect(str(root / ".qccd" / "workspace.db"))
    db.execute("DELETE FROM events")
    db.commit()
    db.close()
    service_request(info, "POST", "/api/change-sets", {"expected_revision": 1, "request_id": "e2", "mode": "apply",
                    "operations": [{"type": "move_site", "id": "C0", "pos": [0, 2]}]}, token="owner")
    got3, _ = read_events(1, "hello")
    assert got3[0] == "gap"


def _kill_hard(pid: int) -> None:
    """TerminateProcess / SIGKILL: no shutdown handler runs, as when something kills the service."""
    import signal
    os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
    from qccd.workspace.core import _pid_alive
    t0 = time.time()
    while _pid_alive(pid) and time.time() - t0 < 20:
        time.sleep(0.1)
    assert not _pid_alive(pid)


def test_a_killed_service_comes_back_on_its_port_and_keeps_its_pairings(live):
    """A service killed without warning leaves a stale runtime file.  The next caller starts
    a new one, which listens on the SAME port and honours the browser pairings the first one
    issued -- so an open Studio page reconnects by itself -- and resumes the event log."""
    import http.client
    root, info = live
    from qccd.workspace.runtime import ensure_service, read_sticky, service_request

    def call(method, path, body=None, headers=None, port=None):
        conn = http.client.HTTPConnection("127.0.0.1", port or info["port"], timeout=20)
        conn.request(method, path, body=None if body is None else json.dumps(body),
                     headers={"Content-Type": "application/json", **(headers or {})})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r.getheader("Set-Cookie"), (json.loads(data) if data else None)

    code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
    status, set_cookie, body = call("POST", "/api/pair", {"code": code})
    assert status == 200
    cookie = set_cookie.split(";")[0]
    csrf = body["csrf"]
    page = {"Cookie": cookie, "X-QCCD-CSRF": csrf}
    edit = {"expected_revision": 0, "request_id": "before", "mode": "apply",
            "operations": [{"type": "move_site", "id": "C0", "pos": [0, 1]}]}
    assert call("POST", "/api/change-sets", edit, page)[0] == 200
    sticky = read_sticky(info["workspace_id"])
    assert sticky["port"] == info["port"] and len(sticky["pairings"]) == 1
    assert cookie.split("=", 1)[1] not in json.dumps(sticky)            # only the cookie's hash is kept

    _kill_hard(info["pid"])
    info2 = ensure_service(root, python=MCP_PY or sys.executable)
    assert info2["pid"] != info["pid"]
    assert info2["port"] == info["port"]                                # same origin for the open page
    assert info2["agent_token"] != info["agent_token"]                  # bearer tokens are per process
    status, _, who = call("GET", "/api/whoami", headers={"Cookie": cookie})
    assert status == 200 and who["csrf"] == csrf                       # the page is still paired
    edit2 = dict(edit, expected_revision=1, request_id="after",
                 operations=[{"type": "move_site", "id": "C0", "pos": [0, 2]}])
    status, _, res = call("POST", "/api/change-sets", edit2, page)
    assert status == 200 and res["revision"] == 2
    assert call("POST", "/api/change-sets", dict(edit2, request_id="x", expected_revision=2),
                {"Cookie": cookie, "X-QCCD-CSRF": "wrong"})[0] == 403    # CSRF still enforced
    info.update(info2)                                                  # the fixture shuts down the new one


def test_keep_alive_restarts_a_killed_service_but_not_a_stopped_one(live):
    """`qccd studio --keep-alive`: a service that was killed (runtime file left, pid dead) is
    started again on its port; one shut down on purpose (runtime file removed) is not."""
    root, info = live
    from qccd.workspace.runtime import keep_alive, read_runtime, service_request
    events, stop = [], threading.Event()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("why", keep_alive(
        root, stop, interval=0.5, on_event=lambda k, d: events.append((k, d)))), daemon=True)
    t.start()
    _kill_hard(info["pid"])
    t0 = time.time()
    while not events and time.time() - t0 < 60:
        time.sleep(0.2)
    kind, d = events[0]
    assert kind == "restarted" and d["old_pid"] == info["pid"] and d["same_port"]
    info2 = read_runtime(info["workspace_id"])
    assert info2 and info2["pid"] == d["pid"] and info2["port"] == info["port"]
    service_request(info2, "POST", "/api/shutdown", {}, token="owner")      # on purpose
    t.join(timeout=30)
    assert not t.is_alive() and out["why"] == "stopped"
    assert [k for k, _ in events] == ["restarted", "stopped"]
    time.sleep(1.0)
    assert read_runtime(info["workspace_id"]) is None                     # and it stayed stopped
    info.update(info2)


def test_a_studio_page_that_cannot_be_built_says_why(svc, monkeypatch):
    """A failure while building the page is an HTML page with the error and what to run,
    not a bare 500 (a person following the website's steps saw only the status code)."""
    import qccd.workspace.service as service_mod
    ws, state, client = svc

    def broken(*a, **k):
        raise ModuleNotFoundError("No module named 'qccd.viz.scale'")
    monkeypatch.setattr(service_mod, "_render", broken)
    r = client.get("/studio")
    assert r.status_code == 500 and r.headers["content-type"].startswith("text/html")
    assert "could not be built" in r.text and "ModuleNotFoundError" in r.text and "qccd stop" in r.text
    state.info["code"] = "code-from-before-an-update"          # the service predates the files on disk
    assert "changed after this service started" in client.get("/studio").text


def test_studio_restarts_a_service_running_older_code(live):
    """After `git pull`, a running service still runs the code it started with; `qccd
    studio` must not hand the person that service (it served the old 500 again).  Only the
    person's command restarts it -- an agent's adapter reuses whatever runs."""
    import json as _json
    root, info = live
    from qccd.workspace.runtime import code_identity, ensure_service, read_runtime, runtime_path
    assert info["code"] == code_identity()
    assert ensure_service(root, restart_stale=True)["pid"] == info["pid"]     # current: reused
    p = runtime_path(info["workspace_id"])
    rec = _json.loads(p.read_text(encoding="utf-8"))
    rec.pop("code")                                   # as written by a service from before the update
    p.write_text(_json.dumps(rec), encoding="utf-8")
    assert ensure_service(root)["pid"] == info["pid"]                           # an adapter: reused
    fresh = ensure_service(root, restart_stale=True, python=MCP_PY or sys.executable)
    assert fresh.get("restarted_stale") is True
    assert fresh["pid"] != info["pid"] and fresh["port"] == info["port"]         # same port, new code
    assert read_runtime(info["workspace_id"])["code"] == code_identity()
    from qccd.workspace.core import _pid_alive
    assert not _pid_alive(info["pid"])
    info.update(fresh)

