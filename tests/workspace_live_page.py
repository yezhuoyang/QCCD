"""LIVE, opt-in: a real Codex answers a question asked on a page of the website.

    QCCD_LIVE_CODEX=1 python tests/workspace_live_page.py [out_dir] [page] [question] [codex|claude]

A fresh workspace starts its service; real Chrome is paired through /open-web and opens a
page of qccd.academy through the workspace's website mirror (default: rules/).  The chat on
that page -- with no agent connected -- is sent the question (default: "What does rule R7
say? Show me its failing example on this page.").  The chat starts Codex by itself; Codex
reads the page and shows things on it through the QCCD page tools.

It prints one JSON summary (the conversation, the page actions Codex took and their
answers, the page's console) and leaves screenshots in out_dir.  It uses real Codex turns
(your Codex plan) and archives the Codex thread it created.  It needs the internet.  Not
part of the pytest suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

QUESTION = "What does rule R7 say? Show me its failing example on this page."


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_CODEX") != "1":
        print("set QCCD_LIVE_CODEX=1 to run this live test (it uses real Codex turns)")
        return 2
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="qccd-live-page-"))
    page = sys.argv[2] if len(sys.argv) > 2 else "rules/"
    question = sys.argv[3] if len(sys.argv) > 3 else QUESTION
    agent = sys.argv[4] if len(sys.argv) > 4 else "codex"
    if agent == "claude":
        os.environ["QCCD_CODEX"] = "none"          # the chat then starts Claude Code
    out.mkdir(parents=True, exist_ok=True)
    os.environ["QCCD_RUNTIME_DIR"] = str(out / "runtime")
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request

    root = out / "ws"
    Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=120)
    base = f"http://127.0.0.1:{info['port']}"
    code = own("POST", "/api/pair-code", {})["code"]
    idle = ("(function(){ var c = QCCD_LIVE.chat(); return !c.pending && c.working.length === 0 && "
            "c.items.some(function(i){ return i.type === 'agent' || i.type === 'notice'; }); })()")
    steps = [
        {"wait": f"location.port === '{info['web_port']}' && !!document.getElementById('qccd-chat')", "timeout": 90000,
         "stopOnFail": True},
        {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && "
         "!!QCCD_LIVE.state().view && QCCD_LIVE.chat().codex !== null && !!(QCCD_LIVE.page() || {}).title",
         "timeout": 90000, "stopOnFail": True},
        {"frame": "/chatframe", "eval": f"localStorage.setItem('qccd.agent', {json.dumps(agent)}); 1"},
        {"frame": "/chatframe", "wait": "true",
         "timeout": 90000, "stopOnFail": True},
        {"sleep": 3000},
        {"frame": "/chatframe", "eval": f"QCCD_LIVE.chatSend({json.dumps(question)}).then(function(d){{ return d && d.prompt_id; }})"},
        {"frame": "/chatframe", "wait": idle, "timeout": 900000, "stopOnFail": True},
        {"sleep": 2500},
        {"shot": str(out / "page_after.png")},
        {"eval": "JSON.stringify({url: location.pathname + location.hash, scrollY: Math.round(scrollY), "
                 "notes: Array.prototype.map.call(document.querySelectorAll('[data-qccd-private]'), function(e){ "
                 "return e.textContent; }).filter(Boolean)})"},
        {"frame": "/chatframe", "eval": "JSON.stringify(QCCD_LIVE.chat())"},
    ]
    spec = out / "spec.json"
    spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/{page}"}, "steps": steps}),
                    encoding="utf-8")
    t0 = time.time()
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                       timeout=2400, cwd=REPO)
    drive = json.loads(r.stdout.decode("utf-8") or "{}")
    st = drive.get("steps", [])
    report = {"seconds": round(time.time() - t0), "question": question, "page": page, "agent": agent,
              "steps": [{k: v for k, v in s.items() if k not in ("value", "step")} for s in st],
              "console": drive.get("logs"), "fatal": drive.get("fatal")}
    if len(st) > 9 and st[9].get("value"):
        report["page_after"] = json.loads(st[9]["value"])
    if len(st) > 10 and st[10].get("value"):
        chat = json.loads(st[10]["value"])
        report["conversation"] = [{k: i.get(k) for k in ("type", "text", "author", "context", "page") if i.get(k)}
                                  for i in chat.get("items", [])]
    # tidy: archive the Codex thread this test created, then stop the service
    try:
        from qccd.workspace.agents.codex import CodexBridge
        for s in own("GET", "/api/sessions")["sessions"]:
            ra = ((s.get("capabilities") or {}).get("reattach") or {})
            if ra.get("url") and ra.get("thread_id"):
                br = CodexBridge(ra["url"], on_event=lambda *a: None)
                br.open()
                report.setdefault("archived", []).append(br.request("thread/archive", {"threadId": ra["thread_id"]}) == {})
                br.connected = False
                br.conn.close()
    except Exception as exc:
        report["archive_error"] = str(exc)
    try:
        own("POST", "/api/shutdown", {})
    except Exception:
        pass
    time.sleep(2)
    # the page actions Codex took, from the workspace's own event log (the service is stopped)
    ws = Workspace(root)
    try:
        report["page_actions"] = [{"action": e["payload"]["action"], "args": e["payload"]["args"],
                                   "actor": e["payload"]["actor"]}
                                  for e in ws.events_since(0, limit=1000, types=["page.action"])["events"]]
    finally:
        ws.close()
    report["out"] = str(out)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
