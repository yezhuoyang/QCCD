"""LIVE smoke test: a prompt sent from the Studio page reaches a real Codex session.

    QCCD_LIVE_CODEX=1 python tests/workspace_live_codex.py [--keep] [--effort medium]

NOT part of the default suite: it starts a real Codex app server, starts a NEW Codex
thread (disclosed as such), and runs one model turn on the user's own Codex account.
Everything else is real too: the workspace service, headless Chrome driving the stock
Studio page with the live layer, the pairing flow, the prompt composer, the delivery
worker, the Codex bridge (turn/start), the QCCD MCP server Codex starts for the thread,
and the agent's tool calls through it.

It prints one JSON object with the evidence: delivery states, the turn id, the change set
the agent committed, the reply in the thread, and what the browser ended up showing.
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


def main() -> int:
    if os.environ.get("QCCD_LIVE_CODEX") != "1":
        print(json.dumps({"skipped": "set QCCD_LIVE_CODEX=1 to run the live Codex smoke test"}))
        return 0
    keep = "--keep" in sys.argv
    effort = sys.argv[sys.argv.index("--effort") + 1] if "--effort" in sys.argv else "medium"
    tmp = Path(tempfile.mkdtemp(prefix="qccd-live-"))
    os.environ["QCCD_RUNTIME_DIR"] = str(tmp / "runtime")
    os.environ["PYTHONPATH"] = str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    root = tmp / "design"
    Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root)
    ev: dict = {"workspace": str(root)}
    try:
        con = service_request(info, "POST", "/api/sessions/codex/connect",
                              {"start_thread": True, "sandbox": "read-only", "approval_policy": "never",
                               "turn_overrides": {"effort": effort}, "label": "Codex (live test)"},
                              token="owner", timeout=120)
        ev["connect"] = {k: con[k] for k in ("thread_id", "new_thread", "url", "note")}
        sid = con["session"]["id"]
        code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
        url = f"http://127.0.0.1:{info['port']}/studio#pair={code}"
        text = ("Add one new trap site with id T9 at position (6, 1), connected to the selected site C3 by a "
                "segment. Use the qccd MCP tools: qccd_get_context, then qccd_manage_comment action=get on this "
                "prompt, then qccd_apply_change_set with mode apply and origin_prompt_id set to this prompt, then "
                "reply in the thread with qccd_manage_comment action=reply and work_state ready_for_review. "
                "Do not run shell commands.")
        shot = tmp / "live.png"
        spec = {"pages": {"a": url}, "steps": [
            {"wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().rev === 0 && QCCD_LIVE.state().connected",
             "timeout": 40000, "stopOnFail": True},
            {"eval": "EDITOR.select([{kind:'site', id:'C3'}]); QCCD_LIVE.selectionKeys()"},
            {"eval": "QCCD_LIVE.addAnchor({kind:'entity', key:'node:C3'}); QCCD_LIVE.state().target"},
            {"eval": f"QCCD_LIVE.send({{text: {json.dumps(text)}, mode: 'apply'}}).then(d => JSON.stringify(d))"},
            {"wait": "fetch('/api/prompts').then(r => r.json()).then(d => (d.prompts||[]).some(p => p.replies > 0 || p.work === 'ready_for_review'))",
             "timeout": 480000},
            {"wait": "QCCD_LIVE.state().rev >= 1 && !!(EDITOR.state().device.nodes['T9'])", "timeout": 30000},
            {"eval": "JSON.stringify({rev: QCCD_LIVE.state().rev, t9: EDITOR.state().device.nodes['T9'] ? true : false, "
                     "segs: Object.keys(EDITOR.state().device.segments).length})"},
            {"shot": str(shot)},
        ]}
        sp = tmp / "spec.json"
        sp.write_text(json.dumps(spec), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(sp)], capture_output=True,
                           text=True, timeout=900)
        try:
            ev["browser"] = json.loads(r.stdout)
        except ValueError:
            ev["browser"] = {"raw": r.stdout[-2000:], "stderr": r.stderr[-2000:]}
        try:
            prompts = service_request(info, "GET", "/api/prompts", token="owner")["prompts"]
            if prompts:
                p = service_request(info, "GET", f"/api/prompts/{prompts[0]['prompt_id']}", token="owner")
                ev["prompt"] = {"id": p["prompt_id"], "work": p["work"], "deliveries": p["deliveries"],
                                "replies": [x["body"]["text"] for x in p["replies"]], "links": p["links"]}
            ev["history"] = service_request(info, "GET", "/api/history", token="owner")["history"]
            ev["session"] = service_request(info, "GET", "/api/sessions", token="owner")["sessions"]
        except Exception as exc:
            ev["evidence_error"] = f"{type(exc).__name__}: {exc}"
            ev["service_log"] = (root / ".qccd" / "service.log").read_text(errors="replace")[-3000:]
        ev["screenshot"] = str(shot)
        ok = bool(ev.get("prompt", {}).get("replies")) and any(
            h["actor"].get("kind") == "agent" and h.get("origin_prompt_id") for h in ev.get("history", []))
        ev["passed"] = ok
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass
        time.sleep(2)
        if not keep and ev.get("connect", {}).get("thread_id"):
            ev["archived"] = _archive(ev["connect"]["thread_id"], root)
    print(json.dumps(ev, default=str))
    return 0 if ev.get("passed") else 1


def _archive(thread_id: str, cwd: Path):
    """Archive the test's Codex thread so it does not clutter the user's history."""
    from qccd.workspace.agents.codex import CodexBridge, find_codex, start_app_server
    from qccd.workspace.runtime import free_port
    port = free_port()
    proc = start_app_server(find_codex(), port, cwd, cwd / ".qccd" / "archive.log")
    br = CodexBridge(f"ws://127.0.0.1:{port}")
    br.server_process = proc
    try:
        t0 = time.time()
        while True:
            try:
                br.open()
                break
            except Exception:
                if time.time() - t0 > 30:
                    return "could not reach an app server to archive"
                time.sleep(0.5)
        br.request("thread/archive", {"threadId": thread_id})
        return True
    except Exception as exc:
        return f"not archived: {exc}"
    finally:
        br.close()


if __name__ == "__main__":
    sys.exit(main())
