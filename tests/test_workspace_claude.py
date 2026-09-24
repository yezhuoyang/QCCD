"""Claude Code as a chat agent (agents/claude.py), with a stand-in `claude` executable.

The stand-in reads the delivery on stdin, records its command line, and answers in Claude
Code's stream-json.  Covered: the chat's message reaches it and its answer comes back as the
conversation; the second message RESUMES the same conversation; each run gets only the QCCD
tools and read-only file tools, with no permission prompt; a failed run says why in the chat.
The real-Claude path is tests/workspace_live_page.py with the chat set to Claude.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.runtime import ensure_service, service_request  # noqa: E402

FAKE = r'''
import json, sys
args = sys.argv[1:]
text = sys.stdin.read()
log = __LOG__
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "stdin": text[:4000]}) + "\n")
sid = args[args.index("--resume") + 1] if "--resume" in args else args[args.index("--session-id") + 1]
out = lambda ev: print(json.dumps(ev), flush=True)
out({"type": "system", "subtype": "init", "session_id": sid, "tools": ["mcp__qccd__qccd_page_read"]})
if "FAIL-PLEASE" in text:
    out({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached", "session_id": sid})
else:
    first = [l for l in text.splitlines() if l.startswith("> ")][0][2:]
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Fake Claude heard: " + first}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "Fake Claude heard: " + first, "session_id": sid})
'''


@pytest.fixture
def svc(tmp_path, monkeypatch):
    log = tmp_path / "claude-calls.jsonl"
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE.replace("__LOG__", repr(str(log))), encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "claude.cmd"
        exe.write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", str(exe))
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    yield info, log
    try:
        service_request(info, "POST", "/api/shutdown", {}, token="owner")
    except Exception:
        pass


def _until(fn, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return None


def test_claude_answers_in_one_conversation_and_says_why_it_stopped(svc):
    info, log = svc
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
    assert own("GET", "/api/agents") == {"codex_available": False, "claude_available": True}
    c = own("POST", "/api/sessions/claude/connect", {"label": "Claude"})
    sid, conv = c["session"]["id"], c["conversation"]
    assert c["session"]["client"] == "claude" and c["session"]["mode"] == "appserver"

    def ask(text):
        return own("POST", "/api/prompts/send", {"body": {"text": text, "intent": "question", "mode": "ask"},
                                                 "context": {"target_session": sid}})["prompt_id"]

    def agent_says(pid):
        items = own("GET", "/api/conversation")["items"]
        return [i for i in items if i["prompt_id"] == pid and i["type"] in ("agent", "notice")]

    p1 = ask("What does rule R7 say?")
    said = _until(lambda: agent_says(p1))
    assert said and said[0]["type"] == "agent" and said[0]["text"] == "Fake Claude heard: What does rule R7 say?"
    assert _until(lambda: own("GET", "/api/conversation")["working"] == [])
    p2 = ask("And R8?")
    assert _until(lambda: agent_says(p2))
    calls = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    a1, a2 = calls[0]["args"], calls[1]["args"]
    assert a1[a1.index("--session-id") + 1] == conv and "--resume" not in a1          # one conversation...
    assert a2[a2.index("--resume") + 1] == conv and "--session-id" not in a2          # ...resumed
    assert a1[a1.index("--permission-mode") + 1] == "dontAsk"                         # nobody is asked anything
    assert a1[a1.index("--allowedTools") + 1] == "mcp__qccd Read Grep Glob"
    assert "Bash" in a1[a1.index("--disallowedTools") + 1] and "--strict-mcp-config" in a1
    assert "address them directly" in a1[a1.index("--append-system-prompt") + 1]
    assert "qccd_" in calls[0]["stdin"] and "This is a chat" in calls[0]["stdin"]      # the delivery, on stdin
    p3 = ask("FAIL-PLEASE")
    note = _until(lambda: agent_says(p3))
    assert note and note[0]["type"] == "notice" and "Claude AI usage limit reached" in note[0]["text"]
