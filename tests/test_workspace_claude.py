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
import json, os, sys
args = sys.argv[1:]
text = sys.stdin.read()
log = __LOG__
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "stdin": text[:4000], "tool_search": os.environ.get("ENABLE_TOOL_SEARCH")}) + "\n")
sid = args[args.index("--resume") + 1] if "--resume" in args else args[args.index("--session-id") + 1]
out = lambda ev: print(json.dumps(ev), flush=True)
out({"type": "system", "subtype": "init", "session_id": sid, "tools": ["mcp__qccd__qccd_page_read"]})
if "FAIL-PLEASE" in text:
    out({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached", "session_id": sid})
elif "TOOLS-PLEASE" in text:
    # one turn the way Claude Code streams it: thinking, a Skill call, an MCP tool call, the results, the answer
    out({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "I should read the context first."}]}})
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_0", "name": "ToolSearch", "input": {"query": "qccd"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_0", "content": [
        {"type": "tool_reference", "tool_name": "mcp__qccd__qccd_get_context"}]}]}})
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_1", "name": "Skill", "input": {"skill": "qccd"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "Launching skill: qccd"}]}})
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_2", "name": "mcp__qccd__qccd_get_context", "input": {"detail": "summary"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_2", "content": [{"type": "text", "text": "ghz4@1 - main r0"}], "is_error": False}]}})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "The design is at r0."}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "The design is at r0.", "session_id": sid,
         "duration_ms": 4321, "num_turns": 3, "total_cost_usd": 0.0123})
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
    # nothing a run schedules outlives it (a live agent promised a cron check that died with it)
    assert {"CronCreate", "ScheduleWakeup", "Monitor", "Agent"} <= set(a1[a1.index("--disallowedTools") + 1].split())
    assert "address them directly" in a1[a1.index("--append-system-prompt") + 1]
    assert "never promise to report back later" in a1[a1.index("--append-system-prompt") + 1]
    assert "qccd_" in calls[0]["stdin"] and "This is a chat" in calls[0]["stdin"]      # the delivery, on stdin
    assert calls[0]["tool_search"] == "false"             # all QCCD tools from the first turn, no lookups
    p3 = ask("FAIL-PLEASE")
    note = _until(lambda: agent_says(p3))
    assert note and note[0]["type"] == "notice" and "Claude AI usage limit reached" in note[0]["text"]


def test_a_codex_turn_is_traced_from_its_item_events(tmp_path):
    """The Codex bridge's side of the trace, fed the app server's own event shapes (ThreadItem in
    `codex app-server generate-json-schema`); a live Codex turn is tests/workspace_live_codex.py."""
    from qccd.workspace.agents.codex import CodexBridge
    from qccd.workspace.trace import Traces, render_text
    tr = Traces(tmp_path)
    br = CodexBridge("ws://127.0.0.1:1")
    br.thread_id, br.tracer = "th_1", ("s_codex", tr)
    tr.current["s_codex"] = {"turn": None, "prompt_id": "p_1", "delivery_id": "d_1"}
    ev = lambda m, p: br._notification(m, dict(p, threadId="th_1"))
    ev("turn/started", {"turn": {"id": "tu_9", "status": "inProgress"}})
    ev("item/started", {"turnId": "tu_9", "item": {"type": "userMessage", "id": "i0", "content": []}})
    ev("item/completed", {"turnId": "tu_9", "item": {"type": "reasoning", "id": "i1", "summary": ["Read the context."], "content": []}})
    call = {"type": "mcpToolCall", "id": "i2", "server": "qccd", "tool": "qccd_get_context", "arguments": {"detail": "summary"},
            "status": "inProgress"}
    ev("item/started", {"turnId": "tu_9", "item": call})
    ev("item/completed", {"turnId": "tu_9", "item": dict(call, status="completed", durationMs=85,
                                                           result={"content": [{"type": "text", "text": "r0"}]})})
    ev("item/completed", {"turnId": "tu_9", "item": {"type": "agentMessage", "id": "i3", "text": "The design is at r0."}})
    ev("item/completed", {"turnId": "tu_9", "item": {"type": "userMessage", "id": "i4", "content": []}})
    ev("turn/completed", {"turn": {"id": "tu_9", "status": "completed", "error": None}})
    steps = tr.steps("s_codex")
    assert [(s["kind"], s["name"], (s["data"] or {}).get("phase")) for s in steps] == [
        ("thinking", "reasoning", "completed"), ("item", "mcpToolCall", "started"), ("item", "mcpToolCall", "completed"),
        ("message", "agentMessage", "completed"), ("turn", "completed", None)]
    assert all(s["turn"] == "tu_9" and s["prompt"] == "p_1" for s in steps) and steps[2]["ms"] == 85
    text = render_text(steps)
    assert "CALL qccd.qccd_get_context" in text and "TURN ENDED: completed" in text and text.count("CALL") == 1


def test_a_claude_turn_is_traced_step_by_step_on_the_model_the_person_chose(svc):
    info, log = svc
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
    models = own("GET", "/api/agents/models")
    assert models["claude"]["available"] and [m["id"] for m in models["claude"]["models"]] == ["", "fable", "opus",
                                                                                               "sonnet", "haiku"]
    assert models["claude"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert models["codex"] == {"available": False, "models": [], "efforts": []}          # QCCD_CODEX=none
    c = own("POST", "/api/sessions/claude/connect", {"label": "Claude", "model": "haiku", "effort": "low"})
    sid = c["session"]["id"]
    assert c["session"]["capabilities"]["settings"] == {"model": "haiku", "effort": "low"}

    def ask(text):
        pid = own("POST", "/api/prompts/send", {"body": {"text": text, "intent": "question", "mode": "ask"},
                                                "context": {"target_session": sid}})["prompt_id"]
        assert _until(lambda: [i for i in own("GET", "/api/conversation")["items"]
                               if i["prompt_id"] == pid and i["type"] in ("agent", "notice")])
        assert _until(lambda: own("GET", "/api/conversation")["working"] == [])
        return pid

    p1 = ask("TOOLS-PLEASE what is the design?")
    a1 = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()][-1]["args"]
    assert a1[a1.index("--model") + 1] == "haiku" and a1[a1.index("--effort") + 1] == "low"
    # the person picks another model and thinking level in the chat: the next message runs on it
    s = own("POST", f"/api/sessions/{sid}/settings", {"model": "sonnet", "effort": "high"})["session"]
    assert s["capabilities"]["settings"] == {"model": "sonnet", "effort": "high"}
    assert s["capabilities"]["reattach"]["model"] == "sonnet" and s["capabilities"]["reattach"]["effort"] == "high"
    with pytest.raises(Exception) as bad:
        own("POST", f"/api/sessions/{sid}/settings", {"effort": "turbo"})
    assert "422" in str(bad.value) or "thinking levels" in str(bad.value)
    with pytest.raises(Exception) as agent:
        service_request(info, "POST", f"/api/sessions/{sid}/settings", {"model": "opus"}, session=sid)
    assert "403" in str(agent.value) or "person" in str(agent.value)
    ask("And R8?")
    a2 = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()][-1]["args"]
    assert a2[a2.index("--model") + 1] == "sonnet" and a2[a2.index("--effort") + 1] == "high"
    own("POST", f"/api/sessions/{sid}/settings", {"model": "", "effort": ""})           # back to the defaults
    ask("And R9?")
    a3 = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()][-1]["args"]
    assert "--model" not in a3 and "--effort" not in a3

    # the trace of the first request: what Claude was handed, thought, called and got back, how it ended
    steps = own("GET", f"/api/traces/{sid}?prompt={p1}")["steps"]
    kinds = [(s["source"], s["kind"], s["name"]) for s in steps]
    assert kinds == [("service", "delivery", "deliver"), ("agent", "session", "claude"),
                     ("agent", "thinking", None), ("agent", "tool_call", "ToolSearch"), ("agent", "tool_result", None),
                     ("agent", "skill", "Skill"), ("agent", "tool_result", None),
                     ("agent", "tool_call", "mcp__qccd__qccd_get_context"), ("agent", "tool_result", None),
                     ("agent", "message", None), ("agent", "turn", "success")], kinds
    assert steps[0]["data"]["request"] == "TOOLS-PLEASE what is the design?" and "qccd_" in steps[0]["data"]["text"]
    assert steps[2]["data"]["text"] == "I should read the context first."
    assert steps[4]["data"]["content"] == "[tool_reference: mcp__qccd__qccd_get_context]"    # a loaded tool, named
    assert steps[5]["data"]["input"] == {"skill": "qccd"}
    assert steps[8]["data"] == {"id": "tu_2", "content": "ghz4@1 - main r0", "is_error": False}
    assert steps[10]["data"]["total_cost_usd"] == 0.0123 and steps[10]["ms"] == 4321
    assert len({s["turn"] for s in steps}) == 1 and all(s["prompt"] == p1 for s in steps)
    idx = own("GET", "/api/traces")["traces"]
    one = [t for t in idx if t["session"] == sid][0]
    assert one["client"] == "claude" and one["prompts"][0]["prompt"] == p1
    assert one["prompts"][0]["text"] == "TOOLS-PLEASE what is the design?" and one["prompts"][0]["tools"] == 3
    assert len(one["prompts"]) == 3
