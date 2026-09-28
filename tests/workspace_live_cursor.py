"""LIVE, opt-in: the Studio chat talks to a real, signed-in Cursor (cursor-agent).

    QCCD_LIVE_CURSOR=1 python tests/workspace_live_cursor.py [out_dir] [model]

NOT part of the default suite: it runs three real Cursor turns on the person's own Cursor
account (install the CLI with `curl https://cursor.com/install -fsS | bash`, then run
`cursor-agent login`).  No browser: the messages go in the way the chat sends them
(/api/prompts/send), and everything after that is real: the delivery worker, the Cursor bridge,
one `cursor-agent -p` run per message, the QCCD MCP server Cursor starts from the workspace's
`.cursor/mcp.json`, and the agent's tool calls through it.

It checks what the chat promises: a question is answered through the QCCD tools; a second
message goes on in the same chat; asked to run a shell command and to write a file, the agent is
refused both, and nothing is written; its tool calls are attributed to the chat's own session.
It prints one JSON summary and exits non-zero when a check fails.

First run, 2026-09-28, cursor-agent 2026.09.28, model Auto: R7 answered in 15 s, R8 (remembering
the R7 question) in 8 s, `ls` and the write both refused by the permissions configuration.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_CURSOR") != "1":
        print(json.dumps({"skipped": "set QCCD_LIVE_CURSOR=1 to run this live test (it uses real Cursor turns)"}))
        return 0
    out = Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else Path(tempfile.mkdtemp(prefix="qccd-live-cursor-"))
    model = sys.argv[2] if len(sys.argv) > 2 else ""
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(QCCD_RUNTIME_DIR=str(out / "runtime"), QCCD_CODEX="none", QCCD_CLAUDE="none")
    from qccd.workspace.agents.cursor import find_cursor
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    if not find_cursor():
        print(json.dumps({"error": "no cursor-agent: install Cursor's CLI, then run `cursor-agent login`"}))
        return 2

    root = out / "ws"
    if not root.exists():
        Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root, python=sys.executable)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=120)
    report: dict = {"out": str(out), "checks": {}}

    def ask(sid, text, timeout=300):
        t0 = time.time()
        pid = own("POST", "/api/prompts/send", {"body": {"text": text, "intent": "question", "mode": "ask"},
                                                "context": {"target_session": sid}})["prompt_id"]
        said = []
        while time.time() - t0 < timeout:
            conv = own("GET", "/api/conversation")
            said = [i for i in conv["items"] if i["prompt_id"] == pid and i["type"] in ("agent", "notice")]
            if said and conv["working"] == []:
                break
            time.sleep(1)
        steps = own("GET", f"/api/traces/{sid}?prompt={pid}")["steps"]
        chat = [x for x in own("GET", "/api/sessions")["sessions"] if x["id"] == sid][0]["bridge"]["chat"]
        return {"asked": text, "seconds": round(time.time() - t0, 1), "chat": chat,
                "said": [(i["type"], i["text"]) for i in said], "steps": steps}

    try:
        c = own("POST", "/api/sessions/cursor/connect", {"label": "Cursor", "model": model or None})
        sid = c["session"]["id"]
        r1 = ask(sid, "What does rule R7 say? Answer in one or two sentences.")
        r2 = ask(sid, "And rule R8? One sentence. Then tell me what I asked you just before this.")
        r3 = ask(sid, "Please try two things and report exactly what happened with each: (1) run the shell command "
                      "`ls` in this folder; (2) create a file named probe.txt in this folder containing the word hi.")
        tools = lambda r: [s["name"] for s in r["steps"] if s["source"] == "agent" and s["kind"] == "tool_call"]
        failed = {(s.get("data") or {}).get("id") for s in r3["steps"]
                  if s["kind"] == "tool_result" and (s.get("data") or {}).get("is_error")}
        touching = [s for s in r3["steps"] if s["source"] == "agent" and s["kind"] == "tool_call"
                    and s["name"] in ("shell", "edit", "write", "delete")]
        ch = report["checks"]
        ch["answered_through_the_qccd_tools"] = (bool(r1["said"]) and r1["said"][0][0] == "agent"
                                                 and any(t.startswith("mcp__qccd__") for t in tools(r1)))
        ch["the_same_chat_goes_on"] = bool(r1["chat"]) and r1["chat"] == r2["chat"] == r3["chat"]
        ch["no_file_written"] = not (root / "probe.txt").exists()
        # it tried (or it would prove nothing), and every attempt was refused
        ch["shell_and_write_refused"] = bool(touching) and all((s.get("data") or {}).get("id") in failed for s in touching)
        ch["calls_attributed_to_the_chat"] = any(s["source"] == "mcp" for s in r1["steps"])
        ch["no_other_session"] = [x["id"] for x in own("GET", "/api/sessions")["sessions"]] == [sid]
        for r in (r1, r2, r3):
            r["tools"] = tools(r)
            r["refused"] = [str((s.get("data") or {}).get("content"))[:200] for s in r["steps"]
                            if s["kind"] == "tool_result" and (s.get("data") or {}).get("is_error")]
            r["turn"] = next(({k: (s.get("data") or {}).get(k) for k in ("duration_ms", "usage")}
                              for s in r["steps"] if s["kind"] == "turn"), None)
            del r["steps"]
        report["messages"] = [r1, r2, r3]
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if report["checks"] and all(report["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
