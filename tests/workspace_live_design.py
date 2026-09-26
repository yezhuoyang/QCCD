"""LIVE, opt-in: a real agent designs the shape the person asks for and submits it to a board.

    QCCD_LIVE_AGENT=1 python tests/workspace_live_design.py [out_dir] [claude|codex] [message] [effort] [model] [typing_s]

effort and model are what the chat's model menu sets (e.g. `medium`, `sonnet`); empty keeps the default.
typing_s: seconds spent typing before the message is sent (default 0: sent at once).

The person's own request (2026-09-24) is the default message: "I want to make a new design,
which has a large triangle and a smaller triangle, and then submit this design to BBCode".
A fresh workspace (general: no board pinned) starts its service; real Chrome opens the
Studio and the chat, with no agent connected, is sent the message.  The chat starts the agent
by itself.  The agent is expected to make a new design under a name, draw the shape in the
Studio, fill in what the board's circuit needs until a run passes every rule, and submit it
to the BB board.

After the agent's turn this waits for every job to finish (a BB reference grade took 667 s before
the checker computed its replay once, 12 s after; the chat's submission card shows its verdict) and prints one JSON summary: the conversation, the designs by name, what
the agent drew (page actions and change sets), the runs, the submissions with their board
and status, and the page's console.  Screenshots land in out_dir.  It uses real agent turns
(your Claude or Codex plan).  Not part of the pytest suite.
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

MESSAGE = ("I want to make a new design, which has a large triangle and a smaller triangle, "
           "and then submit this design to BBCode")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_AGENT") != "1":
        print("set QCCD_LIVE_AGENT=1 to run this live test (it uses real agent turns)")
        return 2
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="qccd-live-design-"))
    agent = sys.argv[2] if len(sys.argv) > 2 else "claude"
    message = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else MESSAGE
    effort = sys.argv[4] if len(sys.argv) > 4 else ""
    model = sys.argv[5] if len(sys.argv) > 5 else ""
    # seconds a person spends typing the message before sending it (0: sent at once, as before
    # 2026-09-26); the chat starts the agent at the first keystroke, so typing hides its start-up
    typing = float(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6] else 0.0
    out.mkdir(parents=True, exist_ok=True)
    os.environ["QCCD_RUNTIME_DIR"] = str(out / "runtime")
    if agent == "claude":
        os.environ["QCCD_CODEX"] = "none"          # the chat then starts Claude Code
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request

    root = out / "ws"
    Workspace.init(root).close()                  # a general workspace: every board, no pin
    info = ensure_service(root)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=120)
    base = f"http://127.0.0.1:{info['port']}"
    code = own("POST", "/api/pair-code", {})["code"]
    idle = ("(function(){ var c = QCCD_LIVE.chat(); return !c.pending && c.working.length === 0 && "
            "c.items.some(function(i){ return i.type === 'agent'; }); })()")
    steps = [
        {"wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && "
                 "QCCD_LIVE.state().rev !== null", "timeout": 90000, "stopOnFail": True},
        {"eval": f"localStorage.setItem('qccd.agent', {json.dumps(agent)}); "
                 f"localStorage.setItem('qccd.model.{agent}', {json.dumps(json.dumps({'model': model, 'effort': effort}))}); 1"},
        {"sleep": 2000},
        {"eval": "var t = document.getElementById('qcl-text'); t.value = " + json.dumps(message[:1]) + "; "
                 "t.dispatchEvent(new Event('input')); 1" if typing else "1"},
        {"sleep": int(typing * 1000)},
        {"eval": f"QCCD_LIVE.chatSend({json.dumps(message)}).then(function(d){{ return d && d.prompt_id; }})"},
        {"wait": idle, "timeout": 3000000, "stopOnFail": True},
        {"shot": str(out / "studio_after.png")},
        {"eval": "JSON.stringify({branch: QCCD_LIVE.chat().branch, rev: QCCD_LIVE.state().rev, "
                 "nodes: Object.keys(EDITOR.state().device.nodes).length, "
                 "loops: Object.keys(EDITOR.state().device.loops).map(function(k){ var l = EDITOR.state().device.loops[k]; "
                 "return {id: k, n: l.nodes.length, closed: !!l.closed}; }), problems: EDITOR.problems()})"},
        {"eval": "JSON.stringify(QCCD_LIVE.chat())"},
        # the chat's submission card follows the grade to its verdict with nobody waiting on it
        {"wait": "!QCCD_LIVE.chat().items.some(function(i){ return i.type === 'submission' && i.state === 'running'; })",
         "timeout": 2400000},
        {"sleep": 1500},
        {"shot": str(out / "studio_verdict.png")},
        {"eval": "JSON.stringify(QCCD_LIVE.chat())"},
    ]
    spec = out / "spec.json"
    spec.write_text(json.dumps({"pages": {"a": f"{base}/studio#pair={code}"}, "steps": steps}), encoding="utf-8")
    t0 = time.time()
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                       timeout=3600, cwd=REPO)
    drive = json.loads(r.stdout.decode("utf-8") or "{}")
    st = drive.get("steps", [])
    report = {"seconds_agent": round(time.time() - t0), "message": message, "agent": agent, "effort": effort, "model": model,
              "typing_s": typing,
              "steps": [{k: v for k, v in s.items() if k not in ("value", "step")} for s in st],
              "console": drive.get("logs"), "fatal": drive.get("fatal")}
    if len(st) > 8 and st[8].get("value"):
        report["studio_after"] = json.loads(st[8]["value"])
    if len(st) > 13 and st[13].get("value"):
        chat = json.loads(st[13]["value"])
        report["conversation"] = [{k: i.get(k) for k in ("type", "text", "author", "summary", "status", "program",
                                                          "draft", "design", "total_ms", "view", "state", "board",
                                                          "metric", "progress", "reasons")
                                   if i.get(k) is not None} for i in chat.get("items", [])]
    # the agent may end its turn while the grade still runs: wait for every job
    t1 = time.time()
    while time.time() - t1 < 1800:
        jobs = own("GET", "/api/jobs").get("jobs", [])
        if not any(j.get("status") in ("queued", "running") for j in jobs):
            break
        time.sleep(5)
    report["seconds_jobs_after_turn"] = round(time.time() - t1)
    report["designs"] = [{"name": b.get("display"), "id": b.get("name"), "head": b.get("head")}
                         for b in own("GET", "/api/branches")["branches"]]
    report["jobs"] = [{k: j.get(k) for k in ("id", "kind", "status", "error")}
                      for j in own("GET", "/api/jobs").get("jobs", [])]
    report["runs"] = own("GET", "/api/runs")["runs"]
    try:
        own("POST", "/api/shutdown", {})
    except Exception:
        pass
    time.sleep(2)
    ws = Workspace(root)
    try:
        report["submissions"] = [{k: s.get(k) for k in ("design", "board", "status", "metrics", "eligibility")}
                                 for s in ws.submissions()]
        report["change_sets"] = [{"actor": e["payload"].get("actor"), "summary": e["payload"].get("summary"),
                                  "design": e.get("branch"), "revision": e.get("revision")}
                                 for e in ws.events_since(0, limit=1000, types=["design.committed"])["events"]]
        report["page_actions"] = [{"action": e["payload"]["action"], "args": str(e["payload"]["args"])[:300]}
                                  for e in ws.events_since(0, limit=1000, types=["page.action"])["events"]]
    finally:
        ws.close()
    report["out"] = str(out)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
