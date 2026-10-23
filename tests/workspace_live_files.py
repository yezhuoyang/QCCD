"""LIVE, opt-in: what Codex in the Studio chat may write, with the person's switch off and on.

    QCCD_LIVE_CODEX=1 python tests/workspace_live_files.py [out_dir]

A correspondent (2026-10-23): "the current Codex session in Studio reports read-only permissions
and cannot export the new files".  A fresh workspace; Codex is started the way the Studio chat
starts it (`files: "workspace"`, approval `never`), and is asked, in one conversation:

  1. switch OFF -- to write hello.txt with its shell: the sandbox refuses (read-only);
  2. switch OFF -- to save the current design as files: `qccd_export` writes exports/<name>/;
  3. switch ON  -- to write hello.txt with its shell again: the file is there.

It prints one JSON summary (each turn's reply, what is on disk) and archives the Codex thread it
created.  It uses real Codex turns (your Codex plan).  Not part of the pytest suite.
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

WRITE = ("Using your own shell (not the QCCD tools), create a file named hello.txt in the current folder "
         "containing the word hi. Then tell me in one sentence exactly what happened.")
SAVE = "Save the current design as files I can open on my computer, and tell me where they are."


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_CODEX") != "1":
        print("set QCCD_LIVE_CODEX=1 to run this live test (it uses real Codex turns)")
        return 2
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="qccd-live-files-"))
    out.mkdir(parents=True, exist_ok=True)
    os.environ["QCCD_RUNTIME_DIR"] = str(out / "runtime")
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request

    root = out / "ws"
    Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=180)
    view = own("POST", "/api/views", {"label": "live files"})
    con = own("POST", "/api/sessions/codex/connect", {"start_thread": True, "label": "Codex", "approval_policy": "never",
                                                       "files": "workspace"})
    sid = con["session"]["id"]
    own("PATCH", f"/api/views/{view['id']}", {"target_session": sid})
    report: dict = {"workspace": str(root), "thread": con["thread_id"],
                    "sandbox_at_start": (con["session"]["capabilities"].get("reattach") or {}).get("sandbox")}

    def turn(text: str) -> dict:
        p = own("POST", "/api/prompts/send", {"body": {"text": text, "intent": "apply_change", "mode": "apply"},
                                              "view_id": view["id"], "context": {"target_session": sid}})
        t0 = time.time()
        while time.time() - t0 < 300:
            pr = own("GET", f"/api/prompts/{p['prompt_id']}")
            if (pr.get("work") or {}).get("state") in ("ready_for_review", "waiting_input", "done", "declined"):
                break
            time.sleep(2)
        pr = own("GET", f"/api/prompts/{p['prompt_id']}")
        return {"asked": text[:60], "work": (pr.get("work") or {}).get("state"), "seconds": round(time.time() - t0),
                "replies": [r["body"].get("text", "")[:600] for r in pr.get("replies", [])]}

    report["off_write"] = turn(WRITE)
    report["off_write"]["hello_exists"] = (root / "hello.txt").exists()
    report["off_save"] = turn(SAVE)
    ex = root / "exports"
    report["off_save"]["exports"] = sorted(str(p.relative_to(root)).replace("\\", "/")
                                           for p in ex.rglob("*") if p.is_file()) if ex.exists() else []
    own("POST", "/api/settings", {"agent_files": "write"})
    report["on_write"] = turn(WRITE)
    report["on_write"]["hello_exists"] = (root / "hello.txt").exists()
    if report["on_write"]["hello_exists"]:
        report["on_write"]["hello"] = (root / "hello.txt").read_text(encoding="utf-8", errors="replace")[:40]
    report["verdict"] = {"read_only_refused": not report["off_write"]["hello_exists"],
                         "export_saved": bool(report["off_save"]["exports"]),
                         "switch_on_wrote": report["on_write"]["hello_exists"]}
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
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if all(report["verdict"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
