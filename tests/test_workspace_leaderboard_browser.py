"""The Studio's Leaderboard panel in REAL Chrome, against a real official server on a loopback port.

A person opens the Studio on one board (`/studio?board=`, the website's "Try your own design"), tests
the design there, signs in (the site's Allow is the grant the site would sign, made by a helper
thread standing in for the person on qccd.academy), and submits; the official ranking in the panel
then lists it with the person's name.  Then the agent asks to submit another design: the chat card
waits for the person, and only their click uploads it.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
from qccd.official.accounts import sign_grant  # noqa: E402
from qccd.official.service import OfficialService, create_app  # noqa: E402
from qccd.official.worker import process_one  # noqa: E402
from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.evaluator import Toolchain  # noqa: E402
from qccd.workspace.runtime import ensure_service, service_request  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
from chrome_path import CHROME  # noqa: E402
TC = Toolchain.discover()
pytestmark = pytest.mark.skipif(not (shutil.which("node") and CHROME and Path(CHROME).exists()
                                     and TC.qccdc is not None and TC.qcheck is not None),
                                reason="needs node, Chrome and the toolchain")
SECRET = "b" * 40


@pytest.fixture
def world(tmp_path, monkeypatch):
    import uvicorn
    for k in ("QCCD_CODEX", "QCCD_CLAUDE", "QCCD_CURSOR"):
        monkeypatch.setenv(k, "none")
    monkeypatch.setenv("QCCD_WEB", "0")
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_ACCOUNT_SECRET", SECRET)
    monkeypatch.setenv("QCCD_CREDENTIALS", str(tmp_path / "home" / "credentials.json"))
    svc = OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    monkeypatch.setenv("QCCD_OFFICIAL_URL", f"http://127.0.0.1:{port}")
    # an earlier entry by someone else, so the ranking has a row that is not ours
    now = time.time()
    svc.db.execute("INSERT INTO submissions(id, uploader, task, task_digest, bundle_digest, archive_sha256, visibility, "
                   "display_name, status, created_at, credit) VALUES('os_x','u_x','ghz4@1',?,'sha256:x','x','public',"
                   "'Racetrack 8','eligible',?, 'Grace Hopper')", (svc.releases["ghz4@1"].digest, now - 86400))
    svc.db.execute("INSERT INTO reports(id, submission, evaluator_version, evaluator_policy, report_digest, report, eligible, "
                   "rank_value, created_at) VALUES('or_x','os_x','x',?,'d','{}',1,1.95,?)", (svc.evaluator_policy, now))
    stop = threading.Event()

    def site_and_worker():
        # the person pressing Allow on qccd.academy, and the server's worker grading what arrives
        while not stop.is_set():
            for r in svc.db.all("SELECT code FROM links WHERE status='pending'"):
                t = time.time()
                svc.approve_link(r["code"], sign_grant({"v": 1, "aud": "qccd-official", "sub": "3", "name": "Ada Lovelace",
                                                        "purpose": "link", "code": r["code"], "iat": t, "exp": t + 300,
                                                        "jti": "b"}, SECRET.encode()))
            try:
                process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
            except Exception:
                pass
            time.sleep(0.5)
    threading.Thread(target=site_and_worker, daemon=True).start()
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    yield {"svc": svc, "info": info, "tmp": tmp_path}
    stop.set()
    try:
        service_request(info, "POST", "/api/shutdown", {}, token="owner")
    except Exception:
        pass
    server.should_exit = True


def test_the_leaderboard_panel_tests_signs_in_submits_and_credits(world):
    info, svc = world["info"], world["svc"]
    base = f"http://127.0.0.1:{info['port']}"
    code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
    tok = info["agent_token"]
    agent = ("(function(method, path, body){ return fetch(path, {method: method, headers: {'Content-Type': "
             "'application/json', 'Authorization': 'Bearer " + tok + "'}, body: body ? JSON.stringify(body) : undefined})"
             ".then(function(r){ return r.json().then(function(d){ return {status: r.status, data: d}; }); }); })")
    ready = "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && QCCD_LIVE.state().rev !== null"
    bd = "QCCD_LIVE.board().data"
    steps = [
        {"wait": ready + " && QCCD_LIVE.board().open && " + bd + " && " + bd + ".board && " + bd + ".official", "timeout": 60000,
         "stopOnFail": True},                                                                                   # 0
        {"eval": "JSON.stringify({btn: document.getElementById('qcl-lbbtn').textContent, "
                 "private: document.getElementById('qcl-lbbtn').hasAttribute('data-qccd-private') && "
                 "document.getElementById('qcl-board').hasAttribute('data-qccd-private'), board: " + bd + ".board.title, "
                 "rows: " + bd + ".official.rows.map(function(r){ return [r.display_name, r.by]; }), "
                 "disabled: document.getElementById('qcl-bsubmit').disabled, signed: " + bd + ".account.signed_in})"},  # 1
        {"eval": "QCCD_LIVE.testOnBoard(); 'testing'"},                                                         # 2
        {"wait": bd + ".test && " + bd + ".test.eligible === true", "timeout": 180000, "stopOnFail": True},    # 3
        {"eval": "JSON.stringify({line: document.getElementById('qcl-btest').textContent, "
                 "disabled: document.getElementById('qcl-bsubmit').disabled})"},                               # 4
        {"eval": "QCCD_LIVE.signIn(); 'signing in'"},                                                           # 5
        {"wait": bd + ".account.signed_in === true", "timeout": 60000, "stopOnFail": True},                    # 6
        {"eval": "document.getElementById('qcl-bname').value = 'Four in a row'; "
                 "document.getElementById('qcl-bname').dispatchEvent(new Event('input')); "
                 "document.getElementById('qcl-bsubmit').textContent"},                                         # 7
        {"eval": "document.getElementById('qcl-bsubmit').click(); 'submitted'"},                                # 8
        {"wait": "(" + bd + ".publications || []).some(function(p){ return p.official && p.official.rank; })",
         "timeout": 120000, "stopOnFail": True},                                                                # 9
        {"wait": bd + ".official.rows.length === 2", "timeout": 30000},                                         # 10
        {"eval": "JSON.stringify({rows: " + bd + ".official.rows.map(function(r){ return [r.display_name, r.by]; }), "
                 "mine: document.querySelectorAll('.qcl-btable tr.mine').length, "
                 "button: document.getElementById('qcl-bsubmit').textContent, "
                 "pub: document.querySelector('.qcl-bpub').textContent})"},                                     # 11
        # the agent asks, during a request of the person's: its card waits for them (its design is the
        # starting chain under another name -- a different bundle, which passes)
        {"eval": "QCCD_LIVE.chatSend('Make a second design and submit it').then(function(d){ window.__p = d.prompt_id; return d.prompt_id; })"},
        {"eval": agent + "('POST', '/api/branches', {title: 'Agent try', source: 'main'}).then(function(r){ return " + agent +
                 "('POST', '/api/change-sets', {branch: r.data.name, expected_revision: r.data.head, request_id: 'ag1', mode: 'apply', "
                 "operations: [{type: 'construct', generator: 'chain', params: {n: 4}, name: 'agentchain'}]}); }).then(function(){ return " + agent +
                 "('POST', '/api/publications', {design: 'Agent try', board: 'GHZ', origin_prompt_id: window.__p}); })"
                 ".then(function(r){ return JSON.stringify([r.status, r.data.state, r.data.requested_by]); })"},  # 13
        {"wait": "(function(){ var c = document.querySelector('.qcl-card.pub'); return c && /Submit as Ada Lovelace/.test(c.textContent); })()",
         "timeout": 180000, "stopOnFail": True},                                                               # 14
        {"eval": "document.querySelector('.qcl-card.pub').textContent"},                                        # 15
        # the agent cannot press it for them: the route is the person's
        {"eval": "(function(){ var id = document.querySelector('.qcl-card.pub').getAttribute('data-publication'); return " + agent +
                 "('POST', '/api/publications/' + id + '/confirm', {}).then(function(r){ return String(r.status); }); })()"},  # 16
        {"eval": "[].filter.call(document.querySelectorAll('.qcl-card.pub button'), function(b){ return /Submit as/.test(b.textContent); })[0].click(); 'pressed'"},
        {"wait": "QCCD_LIVE.chat().items.some(function(i){ return i.type === 'publication' && i.state === 'uploaded'; })",
         "timeout": 60000},                                                                                     # 18
    ]
    spec = {"pages": {"a": f"{base}/studio?board=ghz4#pair={code}"}, "steps": steps}
    p = world["tmp"] / "spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(p)], capture_output=True, timeout=900, cwd=REPO)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    out = json.loads(r.stdout.decode("utf-8"))
    assert not out.get("fatal"), out["fatal"]
    s = out["steps"]
    failed = [(i, x.get("error")) for i, x in enumerate(s) if x.get("ok") is False]
    assert not failed, failed
    first = json.loads(s[1]["value"])
    assert first["btn"] == "Submit to Leaderboard" and first["private"]
    assert first["board"].startswith("GHZ") and first["rows"] == [["Racetrack 8", "Grace Hopper"]]
    assert first["disabled"] is True and first["signed"] is False           # nothing to submit before the test
    tested = json.loads(s[4]["value"])
    assert "Passed every check" in tested["line"] and "#1 of 2" in tested["line"] and tested["disabled"] is True
    assert s[7]["value"] == "Submit as Ada Lovelace"
    after = json.loads(s[11]["value"])
    assert after["rows"][0] == ["Four in a row", "Ada Lovelace"] and after["mine"] == 1
    assert after["button"] == "Submitted" and "#1 of 2" in after["pub"]
    assert json.loads(s[13]["value"]) == [200, "testing", "agent"]
    assert "Agent try" in s[15]["value"] and "as Ada Lovelace" in s[15]["value"]
    assert s[16]["value"] == "403"
    assert svc.db.one("SELECT COUNT(*) AS n FROM submissions WHERE credit='Ada Lovelace'")["n"] == 2
    assert not any(out.get("logs", {}).get("a", [])), out["logs"]
