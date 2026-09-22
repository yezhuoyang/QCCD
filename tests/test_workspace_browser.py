"""The live Studio page in REAL headless Chrome, against a real workspace service.

One scripted session (tests/workspace_browser.mjs drives Chrome over the DevTools
protocol) covers: pairing, a human edit synced as a change set, an agent edit rendered
without a reload, a lasso + arrow prompt whose meaning is frozen at Send, protection
refusing a human drag, presentation with Follow on and off, two tabs as two views, and a
dropped event stream that catches up on reconnect.  The "agent" edits here are HTTP calls
with the agent token -- the same door the MCP adapter uses; the real-agent path is
tests/workspace_live_codex.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.runtime import ensure_service, service_request  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("CHROME") or "C:/Program Files/Google/Chrome/Application/chrome.exe"
pytestmark = pytest.mark.skipif(not (shutil.which("node") and Path(CHROME).exists()),
                                reason="needs node and Chrome")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    yield tmp_path / "ws", info
    try:
        service_request(info, "POST", "/api/shutdown", {}, token="owner")
    except Exception:
        pass


def test_live_studio_end_to_end(live, tmp_path):
    root, info = live
    base = f"http://127.0.0.1:{info['port']}"
    code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
    agent = {"Authorization": f"Bearer {info['agent_token']}", "Content-Type": "application/json"}

    def http(body, path="/api/change-sets", method="POST"):
        return {"http": {"method": method, "url": base + path, "headers": agent, "body": body}}

    def cs(rev, rid, ops):
        return http({"expected_revision": rev, "request_id": rid, "mode": "apply", "operations": ops})
    ready = "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && QCCD_LIVE.state().rev !== null"
    steps = [
        {"wait": ready + " && QCCD_LIVE.state().rev === 0", "timeout": 40000, "stopOnFail": True},        # 0
        {"eval": "window.__marker = 42; JSON.stringify(window.VIEW.vb())"},                                  # 1
        {"eval": "JSON.stringify(EDITOR.addSite(1.5, 1, null, {zone:'trap'}))"},                              # 2 human edit
        {"wait": "QCCD_LIVE.state().rev === 1", "timeout": 15000},                                            # 3
        cs(1, "agent-t9", [{"type": "add_site", "id": "T9", "pos": [2.5, 1], "zone": "trap"},
                           {"type": "add_segment", "a": "C2", "b": "T9"}]),                                  # 4 agent edit
        {"wait": "QCCD_LIVE.state().rev === 2 && !!EDITOR.state().device.nodes['T9']", "timeout": 15000},  # 5
        {"eval": "JSON.stringify({marker: window.__marker, vb: window.VIEW.vb(), synced: QCCD_LIVE.state().synced_edits})"},  # 6
        # a lasso around C0, C1 and an arrow; frozen at Send
        {"eval": "QCCD_LIVE.addLasso([[-0.5,-0.5],[1.5,-0.5],[1.5,0.5],[-0.5,0.5]]); "
                 "QCCD_LIVE.addSketch('arrow', [[0,1.5],[3,1.5]]); QCCD_LIVE.state().anchors.length"},        # 7
        {"eval": "QCCD_LIVE.send({text: 'Use this structure here, but preserve these gate zones', mode: 'propose'})"
                 ".then(d => JSON.stringify(d))"},                                                            # 8
        {"eval": "EDITOR.select([{kind:'site', id:'C3'}]); window.VIEW.zoomAt(700, 400, 300); 'moved'"},     # 9
        # protection refuses a human edit in the UI
        {"eval": "QCCD_LIVE.protect(['node:C2']); 'ok'"},                                                     # 10
        {"wait": "QCCD_LIVE.state().protected.indexOf('node:C2') >= 0 && QCCD_LIVE.state().rev === 3", "timeout": 15000},  # 11
        {"eval": "JSON.stringify(EDITOR.emit({method:'move_site', args:['C2', 2.0, 3.0], kwargs:{}}))"},     # 12
        {"wait": "(function(){var n=EDITOR.state().device.nodes['C2']; return !QCCD_LIVE.state().inflight && "
                 "+QCCD.unbox(n.pos[1]) === 0 && document.getElementById('qcl-notice') && "
                 "document.getElementById('qcl-notice').textContent.indexOf('protected') >= 0})()", "timeout": 15000},  # 13
        # presentation: Follow on, then off
        {"eval": "QCCD_LIVE.state().view"},                                                                   # 14
    ]
    spec = {"pages": {"a": f"{base}/studio#pair={code}"}, "steps": steps}

    # phase 1
    out1 = _drive(spec, tmp_path / "p1.json")
    s = out1["steps"]
    for i, st in enumerate(s):
        assert not st.get("error"), (i, st)
        if "ok" in st:
            assert st["ok"], (i, st)
    assert json.loads(s[6]["value"])["marker"] == 42                    # no reload
    assert json.loads(s[6]["value"])["vb"] == json.loads(s[1]["value"])  # the view held (append path)
    sent = json.loads(s[8]["value"])
    assert sent["design_revision"] == 2 and sent["delivery"] is None     # no agent connected: queued nowhere
    ctx = service_request(info, "GET", f"/api/context-snapshots/{sent['context_snapshot_id']}", token="owner")
    region = next(a for a in ctx["anchors"] if a["kind"] == "region")
    assert set(region["entities_inside"]) == {"node:C0", "node:C1", "segment:E0"}
    assert region["resolved"]["node:C0"]["pos"] == [0.0, 0.0] and region["viewport"]["vb"]
    assert ctx["sketches"][0]["kind"] == "arrow" and ctx["sketches"][0]["points"] == [[0, 1.5], [3, 1.5]]
    assert ctx["selection"] == [] and ctx["design_revision"] == 2
    # the later selection and zoom did not change what was sent
    again = service_request(info, "GET", f"/api/context-snapshots/{sent['context_snapshot_id']}", token="owner")
    assert again == ctx
    assert service_request(info, "GET", "/api/design", token="owner")["qccd_workspace"]["revision"] == 3
    view_a = s[14]["value"]
    assert out1["logs"]["a"] == [], out1["logs"]


def test_two_tabs_are_two_views(live, tmp_path):
    root, info = live
    base = f"http://127.0.0.1:{info['port']}"
    code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
    ready = "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && QCCD_LIVE.state().rev !== null"
    out = _drive({"pages": {"a": f"{base}/studio#pair={code}"}, "steps": [
        {"page": "a", "wait": ready, "timeout": 40000, "stopOnFail": True},
        {"open": "b", "url": f"{base}/studio"},                       # the cookie pairs it; no code in this URL
        {"page": "b", "wait": ready, "timeout": 40000, "stopOnFail": True},
        {"page": "a", "eval": "QCCD_LIVE.state().view"},
        {"page": "b", "eval": "QCCD_LIVE.state().view"},
    ]}, tmp_path / "tabs.json")
    va, vb = out["steps"][3]["value"], out["steps"][4]["value"]
    assert va and vb and va != vb
    views = {v["id"] for v in service_request(info, "GET", "/api/views", token="owner")["views"]}
    assert {va, vb} <= views


def test_presentation_follow_and_reconnect(live, tmp_path):
    """Present to THIS tab's view with Follow on (it moves) and off (it only notifies); then
    drop the event stream, change the design, and reconnect: the tab catches up."""
    root, info = live
    base = f"http://127.0.0.1:{info['port']}"
    code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
    tok = info["agent_token"]
    ready = "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && QCCD_LIVE.state().rev !== null"
    present = ("(function(key, note){ return fetch('/api/present', {method:'POST', headers:{'Content-Type':"
               "'application/json','Authorization':'Bearer " + tok + "'}, body: JSON.stringify("
               "{action:'highlight', target:{keys:[key]}, view_id: QCCD_LIVE.state().view, note: note})})"
               ".then(r => r.status); })")
    change = ("(function(rid, id){ return fetch('/api/change-sets', {method:'POST', headers:{'Content-Type':"
              "'application/json','Authorization':'Bearer " + tok + "'}, body: JSON.stringify("
              "{expected_revision: QCCD_LIVE.state().rev, request_id: rid, mode:'apply', operations:[{type:'add_site',"
              " id: id, pos:[1.5,-1], zone:'trap'}]})}).then(r => r.status); })")
    out = _drive({"pages": {"a": f"{base}/studio#pair={code}"}, "steps": [
        {"wait": ready, "timeout": 40000, "stopOnFail": True},                                        # 0
        {"eval": f"{present}('node:C1', 'follow on')"},                                               # 1
        {"wait": "QCCD_LIVE.state().highlight.indexOf('node:C1') >= 0", "timeout": 10000},            # 2
        {"eval": "QCCD_LIVE.setFollow(false).then(() => 'off')"},                                     # 3
        {"sleep": 4500},                                                                              # 4
        {"eval": f"{present}('node:C3', 'follow off')"},                                              # 5
        {"wait": "document.getElementById('qcl-notice') && document.getElementById('qcl-notice').textContent"
                 ".indexOf('wants to show you') >= 0 && QCCD_LIVE.state().highlight.indexOf('node:C3') < 0",
         "timeout": 10000},                                                                           # 6
        {"eval": "QCCD_LIVE.disconnect(); 'dropped'"},                                                # 7
        {"eval": f"{change}('while-away', 'TA')"},                                                    # 8
        {"sleep": 1500},                                                                              # 9
        {"eval": "!!EDITOR.state().device.nodes['TA']"},                                              # 10
        {"eval": "QCCD_LIVE.reconnect(); 'back'"},                                                    # 11
        {"wait": "!!EDITOR.state().device.nodes['TA'] && QCCD_LIVE.state().rev === 1", "timeout": 15000},  # 12
    ]}, tmp_path / "present.json")
    s = out["steps"]
    for i, st in enumerate(s):
        assert not st.get("error"), (i, st)
        if "ok" in st:
            assert st["ok"], (i, st)
    assert s[1]["value"] == 200 and s[5]["value"] == 200 and s[8]["value"] == 200
    assert s[10]["value"] is False                    # nothing arrived while the stream was down
    outcomes = [e["payload"]["outcome"] for e in _events(root) if e["type"] == "presented"]
    assert outcomes == ["displayed", "notified"]


def test_a_page_survives_a_service_restart(live, tmp_path):
    """Kill the service with no warning while a Studio page is open.  The next caller starts
    a new one on the same port with the same pairings; the page's event stream comes back by
    itself, shows an agent edit made after the restart, and its own next edit is accepted --
    with no reload (a marker set before the kill survives)."""
    import signal
    import time
    from qccd.workspace.core import _pid_alive
    root, info = live
    base = f"http://127.0.0.1:{info['port']}"
    code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
    ready = "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && QCCD_LIVE.state().rev === 0"
    spec = {"pages": {"a": f"{base}/studio#pair={code}"}, "steps": [
        {"wait": ready, "timeout": 40000, "stopOnFail": True},                                        # 0
        {"eval": "window.__marker = 7; 'set'"},                                                       # 1
        {"wait": "!QCCD_LIVE.state().connected", "timeout": 60000, "stopOnFail": True},               # 2 killed
        {"wait": "QCCD_LIVE.state().connected && !!EDITOR.state().device.nodes['TR'] && QCCD_LIVE.state().rev === 1",
         "timeout": 90000, "stopOnFail": True},                                                        # 3 back
        {"eval": "JSON.stringify(EDITOR.addSite(2.5, 1, null, {zone:'trap'}))"},                       # 4
        {"wait": "!QCCD_LIVE.state().inflight && QCCD_LIVE.state().rev === 2", "timeout": 20000},     # 5
        {"eval": "window.__marker"},                                                                  # 6
    ]}
    path = tmp_path / "restart.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    drv = subprocess.Popen(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO)
    try:
        t0 = time.time()                  # the page is up once it reports what it rendered
        while not any(e["type"] == "view.updated" for e in _events(root)):
            assert time.time() - t0 < 60 and drv.poll() is None, "the page never opened"
            time.sleep(0.3)
        time.sleep(1.5)
        os.kill(info["pid"], signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        while _pid_alive(info["pid"]):
            time.sleep(0.1)
        time.sleep(3)                     # the page notices while nothing listens
        info2 = ensure_service(root, python=sys.executable)
        assert info2["port"] == info["port"] and info2["pid"] != info["pid"]
        info.update(info2)                # the fixture shuts down the new one
        service_request(info2, "POST", "/api/change-sets", {
            "expected_revision": 0, "request_id": "after-restart", "mode": "apply",
            "operations": [{"type": "add_site", "id": "TR", "pos": [1.5, -1], "zone": "trap"}]})
        out_b, err_b = drv.communicate(timeout=300)
    finally:
        if drv.poll() is None:
            drv.kill()
    assert drv.returncode == 0, err_b.decode("utf-8", "replace")[-2000:]
    out = json.loads(out_b.decode("utf-8"))
    assert not out.get("fatal"), out["fatal"]
    s = out["steps"]
    for i, st in enumerate(s):
        assert not st.get("error"), (i, st)
        if "ok" in st:
            assert st["ok"], (i, st)
    assert s[6]["value"] == 7                                    # the same page, never reloaded
    commits = [e["payload"] for e in _events(root) if e["type"] == "design.committed"]
    assert [c["actor"]["kind"] for c in commits][-2:] == ["agent", "human"]


def _events(root: Path) -> list:
    """Read the event log without opening a second Workspace on a live database."""
    import sqlite3
    db = sqlite3.connect(f"file:{root / '.qccd' / 'workspace.db'}?mode=ro", uri=True)
    rows = db.execute("SELECT type, payload FROM events ORDER BY seq").fetchall()
    db.close()
    return [{"type": t, "payload": json.loads(p)} for t, p in rows]


def _drive(spec, path: Path) -> dict:
    path.write_text(json.dumps(spec), encoding="utf-8")
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(path)], capture_output=True,
                       timeout=600, cwd=REPO)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    out = json.loads(r.stdout.decode("utf-8"))
    assert not out.get("fatal"), out["fatal"]
    return out
