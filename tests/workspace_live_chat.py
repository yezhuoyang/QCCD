"""LIVE, opt-in: the Studio chat with a real Codex, on the two requests the chat is for.

    QCCD_LIVE_CODEX=1 python tests/workspace_live_chat.py [out_dir]

A fresh workspace gets three designs: the main design (a 2x72 ring with 24 docks), draft A
(a copy of it) and draft B (the same ring with 36 docks).  Real Chrome opens the Studio,
and the chat -- with no agent connected -- is sent:

  1. "Compile and run the BB code on the current design, evaluate the performance, and
     summarize the bottleneck."   The chat starts Codex by itself; Codex runs the program
     through the QCCD MCP tools and answers from the performance report.
  2. "Run the BB code on design A and design B, and show me the evaluation side by side."
     Codex runs it on both drafts and compares them; the side-by-side page opens with both
     animations.

It prints one JSON summary (the conversation, the runs, the comparison) and leaves
screenshots in out_dir.  It uses real Codex turns (your Codex plan), and archives the
Codex thread it created.  Not part of the pytest suite.
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

MSG1 = ("Compile and run the BB code on the current design, evaluate the performance, "
        "and summarize the bottleneck.")
MSG2 = "Run the BB code on design A and design B, and show me the evaluation side by side."


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_CODEX") != "1":
        print("set QCCD_LIVE_CODEX=1 to run this live test (it uses real Codex turns)")
        return 2
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="qccd-live-chat-"))
    out.mkdir(parents=True, exist_ok=True)
    os.environ["QCCD_RUNTIME_DIR"] = str(out / "runtime")
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request

    root = out / "ws"
    Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=120)
    ring = lambda v, n: [{"type": "construct", "generator": "ring",
                          "params": {"width": 72, "height": 2, "verticals": v}, "name": n}]
    own("POST", "/api/change-sets", {"expected_revision": 0, "request_id": "main", "mode": "apply",
                                     "operations": ring(24, "ring24")})
    own("POST", "/api/branches", {"name": "A", "source": "main"})
    own("POST", "/api/branches", {"name": "B", "source": "main"})
    own("POST", "/api/change-sets", {"branch": "cand/B", "expected_revision": 0, "request_id": "b", "mode": "apply",
                                     "operations": ring(36, "ring36")})
    code = own("POST", "/api/pair-code", {})["code"]
    base = f"http://127.0.0.1:{info['port']}"
    idle = ("(function(){ var c = QCCD_LIVE.chat(); return !c.pending && c.working.length === 0 && "
            "c.items.some(function(i){ return i.type === 'agent'; }) && c.items.filter(function(i){ return i.type === 'user'; }).length === %d; })()")
    conv = "JSON.stringify(QCCD_LIVE.chat())"
    steps = [
        {"page": "a", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && "
                              "QCCD_LIVE.state().rev !== null && QCCD_LIVE.chat().codex !== null", "timeout": 60000,
         "stopOnFail": True},
        {"page": "a", "eval": f"QCCD_LIVE.chatSend({json.dumps(MSG1)}).then(function(d){{ return d && d.prompt_id; }})"},
        {"page": "a", "wait": idle % 1, "timeout": 900000, "stopOnFail": True},
        {"page": "a", "sleep": 2000},
        {"page": "a", "eval": conv},
        {"page": "a", "shot": str(out / "chat_1.png")},
        {"page": "a", "eval": f"QCCD_LIVE.chatSend({json.dumps(MSG2)}).then(function(d){{ return d && d.prompt_id; }})"},
        {"page": "a", "wait": idle % 2, "timeout": 900000, "stopOnFail": True},
        {"page": "a", "sleep": 2000},
        {"page": "a", "eval": conv},
        {"page": "a", "shot": str(out / "chat_2.png")},
    ]
    spec = out / "spec.json"
    spec.write_text(json.dumps({"pages": {"a": f"{base}/studio#pair={code}"}, "steps": steps}), encoding="utf-8")
    t0 = time.time()
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                       timeout=2400, cwd=REPO)
    drive = json.loads(r.stdout.decode("utf-8") or "{}")
    report = {"seconds": round(time.time() - t0), "steps": [{k: v for k, v in s.items() if k != "value"}
                                                            for s in drive.get("steps", [])],
              "console": drive.get("logs"), "fatal": drive.get("fatal")}
    vals = [s.get("value") for s in drive.get("steps", [])]
    last = json.loads(vals[9]) if len(vals) > 9 and vals[9] else (json.loads(vals[4]) if len(vals) > 4 and vals[4] else {})
    report["conversation"] = [{k: i.get(k) for k in ("type", "text", "author", "context", "program", "draft",
                                                        "total_ms", "status", "summary", "verdict", "view")
                               if i.get(k) is not None} for i in last.get("items", [])]
    cmp = [i for i in last.get("items", []) if i.get("type") == "compare"]
    if cmp:                                  # open the side-by-side page the chat links to
        code2 = own("POST", "/api/pair-code", {})["code"]
        spec2 = out / "spec2.json"
        spec2.write_text(json.dumps({"pages": {"a": f"{base}/studio#pair={code2}"}, "steps": [
            {"page": "a", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired", "timeout": 60000, "stopOnFail": True},
            {"open": "b", "url": base + cmp[-1]["view"]},
            {"page": "b", "wait": "(function(){var f=document.querySelectorAll('iframe'); return f.length===2 && "
                                  "[0,1].every(function(k){var w=f[k].contentWindow; return w && w.QRUN && w.QRUN.frames()>0;});})()",
             "timeout": 240000, "stopOnFail": True},
            {"page": "b", "eval": "JSON.stringify(QCMP.seekMs(300))"},
            {"page": "b", "eval": "document.querySelector('.verdict').textContent"},
            {"page": "b", "sleep": 1000},
            {"page": "b", "shot": str(out / "side_by_side.png")}]}), encoding="utf-8")
        r2 = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec2)], capture_output=True,
                            timeout=900, cwd=REPO)
        d2 = json.loads(r2.stdout.decode("utf-8") or "{}")
        report["side_by_side"] = [{k: s.get(k) for k in ("ok", "value", "error") if s.get(k) is not None}
                                  for s in d2.get("steps", [])]
    report["runs"] = own("GET", "/api/runs")["runs"]
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
    report["out"] = str(out)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
