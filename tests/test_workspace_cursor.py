"""Cursor as a chat agent (agents/cursor.py), with a stand-in `cursor-agent` executable.

The stand-in answers as cursor-agent 2026.09.26 does: `models` lists the account's models, and a
print-mode run (which starts a chat unless it resumes one) reads its prompt on stdin and streams
stream-json events (system/init, thinking, tool_call started/completed, assistant, result), or
says why on stderr and exits 1 without a result.  It records its command line, stdin, the
session in its environment, the settings folder it was pointed at and the workspace's
`.cursor/mcp.json`.  Covered: a message reaches it and its answer comes back as the
conversation; every message is a new run that resumes the SAME chat; each run is headless with
the QCCD tools allowed and the shell and file writes denied, whatever the person's own Cursor
allows; the model the person picks reaches the command line; a run that is not signed in says
how to sign in; Stop ends a run and the chat goes on; the turn's trace step by step; the
installer.  The real-Cursor path is tests/workspace_live_page.py with the chat set to Cursor.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.runtime import ensure_service, service_request  # noqa: E402

CHAT = "99999999-8888-4777-8666-555555555555"      # the chat the first run starts
FAKE = r'''
import json, os, sys, time
args = sys.argv[1:]
log = __LOG__
out = lambda ev: print(json.dumps(ev), flush=True)
if args[:1] == ["models"]:
    print("Available models\n\nauto - Auto (current, default)\ngpt-5 - GPT-5\n"
          "sonnet-4.5-thinking - Claude 4.5 Sonnet (Thinking)\n\nTip: use --model <id> to switch.")
    sys.exit(0)
text = sys.stdin.read()
cfg = os.environ.get("CURSOR_CONFIG_DIR")
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "pid": os.getpid(), "stdin": text, "cwd": os.getcwd(),
                        "session": os.environ.get("QCCD_SESSION_ID"), "config_dir": cfg,
                        "config": json.load(open(os.path.join(cfg, "cli-config.json"), encoding="utf-8")),
                        "mcp": json.load(open(os.path.join(os.getcwd(), ".cursor", "mcp.json"), encoding="utf-8"))}) + "\n")
if "SIGNED-OUT-PLEASE" in text:
    print("Error: Authentication required. Please run 'agent login' first, or set CURSOR_API_KEY environment "
          "variable.", file=sys.stderr, flush=True)
    sys.exit(1)
chat = args[args.index("--resume") + 1] if "--resume" in args else "__CHAT__"
out({"type": "system", "subtype": "init", "apiKeySource": "login", "cwd": os.getcwd(), "session_id": chat,
     "model": "GPT-5", "permissionMode": "default"})
out({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}, "session_id": chat})
if "SLOW-PLEASE" in text:
    time.sleep(120)
elif "TOOLS-PLEASE" in text:
    # one turn the way cursor-agent streams it: thinking, a QCCD call, a refused shell command, the answer
    out({"type": "thinking", "subtype": "delta", "text": "I should read ", "session_id": chat})
    out({"type": "thinking", "subtype": "delta", "text": "the context first.", "session_id": chat})
    out({"type": "thinking", "subtype": "completed", "session_id": chat})
    mcp = {"args": {"name": "project-0-ws-qccd-qccd_get_context", "toolName": "qccd_get_context",
                    "providerIdentifier": "project-0-ws-qccd", "args": {"detail": "summary"}, "toolCallId": "c1"}}
    out({"type": "tool_call", "subtype": "started", "call_id": "c1", "tool_call": {"mcpToolCall": mcp}, "session_id": chat})
    out({"type": "tool_call", "subtype": "completed", "call_id": "c1", "tool_call": {"mcpToolCall": dict(mcp, result={
        "success": {"content": [{"text": {"text": "ghz4@1 - main r0"}}], "isError": False}})}, "session_id": chat})
    sh = {"args": {"command": "ls -la", "workingDirectory": ""}}
    out({"type": "tool_call", "subtype": "started", "call_id": "c2", "tool_call": {"shellToolCall": sh}, "session_id": chat})
    out({"type": "tool_call", "subtype": "completed", "call_id": "c2", "tool_call": {"shellToolCall": dict(sh, result={
        "rejected": {"command": "ls -la", "reason": "denied by permissions"}})}, "session_id": chat})
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "The design is at r0."}]},
         "session_id": chat})
    out({"type": "result", "subtype": "success", "duration_ms": 4321, "duration_api_ms": 4321, "is_error": False,
         "result": "The design is at r0.", "session_id": chat, "request_id": "r1",
         "usage": {"inputTokens": 900, "outputTokens": 40, "cacheReadTokens": 5000, "cacheWriteTokens": 100}})
else:
    first = [l for l in text.splitlines() if l.startswith("> ")][0][2:]
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Fake Cursor heard: " + first}]},
         "session_id": chat})
    out({"type": "result", "subtype": "success", "duration_ms": 900, "duration_api_ms": 900, "is_error": False,
         "result": "Fake Cursor heard: " + first, "session_id": chat, "request_id": "r0"})
'''


def _fake(tmp_path) -> tuple:
    log = tmp_path / "cursor-calls.jsonl"
    fake = tmp_path / "fake_cursor.py"
    fake.write_text(FAKE.replace("__LOG__", repr(str(log))).replace("__CHAT__", CHAT), encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "cursor-agent.cmd"
        exe.write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "cursor-agent"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return exe, log


@pytest.fixture
def svc(tmp_path, monkeypatch):
    exe, log = _fake(tmp_path)
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")
    monkeypatch.setenv("QCCD_CURSOR", str(exe))
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


def _calls(log) -> list:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def _chat(info):
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)

    def ask(sid, text):
        pid = own("POST", "/api/prompts/send", {"body": {"text": text, "intent": "question", "mode": "ask"},
                                                "context": {"target_session": sid}})["prompt_id"]
        said = _until(lambda: [i for i in own("GET", "/api/conversation")["items"]
                               if i["prompt_id"] == pid and i["type"] in ("agent", "notice")])
        assert said, f"no answer to {text!r}"
        assert _until(lambda: own("GET", "/api/conversation")["working"] == [])
        return pid, said
    return own, ask


def test_cursor_answers_in_one_chat_headless_with_no_shell_and_no_writes(svc):
    info, log = svc
    own, ask = _chat(info)
    root = Path(info["root"])
    assert own("GET", "/api/agents") == {"codex_available": False, "claude_available": False,
                                         "cursor_available": True, "installed": []}
    # the person's own Cursor settings may allow anything: the chat's runs never read them
    c = own("POST", "/api/sessions/cursor/connect", {"label": "Cursor"})
    sid = c["session"]["id"]
    assert c["session"]["client"] == "cursor" and c["session"]["mode"] == "appserver" and c["new_thread"]
    assert "CURSOR_CONFIG_DIR" in c["attach"] and "cursor-agent --resume" in c["attach"]

    _, said = ask(sid, "What does rule R7 say?")
    assert said[0]["type"] == "agent" and said[0]["text"] == "Fake Cursor heard: What does rule R7 say?"
    _, said = ask(sid, "And R8?")
    assert said[0]["text"] == "Fake Cursor heard: And R8?"
    r1, r2 = _calls(log)
    a1, a2 = r1["args"], r2["args"]
    # one run per message, in one chat: the first run starts it and names it, the next resumes it
    assert "--resume" not in a1 and a2[a2.index("--resume") + 1] == CHAT and r1["pid"] != r2["pid"]
    s = [x for x in own("GET", "/api/sessions")["sessions"] if x["id"] == sid][0]
    assert s["capabilities"]["reattach"]["chat"] == CHAT and s["bridge"]["chat"] == CHAT
    # headless, in the workspace, trusted, its MCP servers loaded without asking; never --force
    assert a1[:3] == ["-p", "--output-format", "stream-json"] and "--trust" in a1 and "--approve-mcps" in a1
    assert Path(a1[a1.index("--workspace") + 1]) == root and Path(r1["cwd"]).resolve() == root.resolve()
    assert not {"-f", "--force", "--yolo", "--auto-review"} & set(a1)
    # the message goes on stdin (any length, never on a command line), with the chat's note
    assert "What does rule R7 say?" in r1["stdin"] and "This is a chat" in r1["stdin"]
    assert "nothing outlives it" in r1["stdin"] and not any("R7" in x for x in a1)
    # what a run may do: the QCCD tools; no shell command, no file write; anything else needing an
    # approval is refused by a headless run
    assert Path(r1["config_dir"]) == root / ".qccd" / "cursor"
    cfg = r1["config"]
    assert cfg["approvalMode"] == "allowlist" and cfg["sandbox"]["mode"] == "disabled"
    assert "Mcp(*-qccd:*)" in cfg["permissions"]["allow"]
    assert {"Shell(*)", "Shell(**)", "Write(**)"} <= set(cfg["permissions"]["deny"])
    assert not [x for x in cfg["permissions"]["allow"] if not x.startswith("Mcp(")]
    # the QCCD tools, attributed to this chat: Cursor gives an MCP server only the environment its
    # entry names, so the entry passes the session through and the run sets it
    entry = r1["mcp"]["mcpServers"]["qccd"]
    assert entry["env"]["QCCD_SESSION_ID"] == "${env:QCCD_SESSION_ID}" and r1["session"] == sid
    assert entry["args"][entry["args"].index("--client") + 1] == "cursor" and entry["env"]["QCCD_MANAGED"] == "1"

    # the model the person picks is on the next run's command line; a thinking level is the model's
    own("POST", f"/api/sessions/{sid}/settings", {"model": "gpt-5", "effort": ""})
    ask(sid, "And R9?")
    a3 = _calls(log)[-1]["args"]
    assert a3[a3.index("--model") + 1] == "gpt-5" and a3[a3.index("--resume") + 1] == CHAT
    with pytest.raises(Exception) as bad:
        own("POST", f"/api/sessions/{sid}/settings", {"model": "gpt-5", "effort": "high"})
    assert "422" in str(bad.value) or "part of its model" in str(bad.value)

    # not signed in: Cursor's words, and what to do
    _, said = ask(sid, "SIGNED-OUT-PLEASE")
    assert said[0]["type"] == "notice" and "Authentication required" in said[0]["text"]
    assert "cursor-agent login" in said[0]["text"]


def test_stop_ends_a_cursor_run_and_the_chat_goes_on(svc):
    info, log = svc
    own, ask = _chat(info)
    sid = own("POST", "/api/sessions/cursor/connect", {})["session"]["id"]
    own("POST", "/api/prompts/send", {"body": {"text": "SLOW-PLEASE", "intent": "question", "mode": "ask"},
                                      "context": {"target_session": sid}})
    running = lambda: [x for x in own("GET", "/api/sessions")["sessions"] if x["id"] == sid][0]["bridge"]["process"]
    assert _until(lambda: running() == "running" and _calls(log))
    pid = _calls(log)[-1]["pid"]
    r = own("POST", f"/api/sessions/{sid}/stop", {})
    assert r["interrupt"] == {"supported": True, "interrupted": True}
    assert _until(lambda: running() is None, timeout=15)
    assert _until(lambda: not _alive(pid), timeout=15), "the run outlived Stop"
    own("POST", f"/api/sessions/{sid}/resume", {})
    _, said = ask(sid, "Still there?")
    assert said[0]["text"] == "Fake Cursor heard: Still there?"
    a = _calls(log)[-1]["args"]
    assert a[a.index("--resume") + 1] == CHAT


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return f" {pid} " in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_a_cursor_turn_is_traced_step_by_step_and_reads_as_a_program(svc):
    info, log = svc
    own, ask = _chat(info)
    models = own("GET", "/api/agents/models")["cursor"]
    assert models["available"] and models["efforts"] == []
    assert [(m["id"], m["label"], m.get("note")) for m in models["models"]] == [
        ("", "Default", "what Cursor picks"), ("auto", "Auto", "Cursor's default"), ("gpt-5", "GPT-5", None),
        ("sonnet-4.5-thinking", "Claude 4.5 Sonnet (Thinking)", None)]
    sid = own("POST", "/api/sessions/cursor/connect", {"model": "sonnet-4.5-thinking"})["session"]["id"]
    p1, _ = ask(sid, "TOOLS-PLEASE what is the design?")
    a1 = _calls(log)[-1]["args"]
    assert a1[a1.index("--model") + 1] == "sonnet-4.5-thinking"
    steps = own("GET", f"/api/traces/{sid}?prompt={p1}")["steps"]
    kinds = [(s["source"], s["kind"], s["name"]) for s in steps if s["source"] != "mcp"]
    assert kinds == [("service", "delivery", "deliver"), ("agent", "session", "cursor"), ("agent", "thinking", None),
                     ("agent", "tool_call", "mcp__qccd__qccd_get_context"), ("agent", "tool_result", None),
                     ("agent", "tool_call", "shell"), ("agent", "tool_result", None),
                     ("agent", "message", None), ("agent", "turn", "success")], kinds
    assert steps[0]["data"]["request"] == "TOOLS-PLEASE what is the design?"
    assert steps[2]["data"]["text"] == "I should read the context first."
    assert steps[3]["data"] == {"id": "c1", "input": {"detail": "summary"}}
    assert steps[4]["data"] == {"id": "c1", "content": "ghz4@1 - main r0", "is_error": False}
    assert steps[6]["data"]["is_error"] and "denied" in steps[6]["data"]["content"]
    assert steps[8]["ms"] == 4321 and steps[8]["data"]["usage"]["cache_read_input_tokens"] == 5000
    # the same program as a Claude turn: the QCCD call is a QCCD call
    prog = own("GET", f"/api/traces/{sid}/program?prompt={p1}")
    calls = [x for x in prog["instructions"] if x["op"] == "call"]
    assert [(c["tool"], c["kind"], c["ok"]) for c in calls] == [("qccd_get_context", "qccd", True),
                                                                ("shell", "builtin", False)], calls
    one = [t for t in own("GET", "/api/traces")["traces"] if t["session"] == sid][0]
    assert one["client"] == "cursor" and one["prompts"][0]["tools"] == 2


def test_a_renamed_workspace_folder_starts_a_new_cursor_chat(tmp_path, monkeypatch):
    """Cursor keeps a chat under the folder it ran in (`chats/<md5 of the folder>`): from a renamed
    folder the bridge starts a new chat, and says so."""
    import types
    from qccd.workspace.agents.cursor import CursorBridge, reattach_cursor_sessions
    exe, log = _fake(tmp_path)
    monkeypatch.setenv("QCCD_CURSOR", str(exe))
    ws = Workspace.init(tmp_path / "Renamed designs")
    try:
        state = types.SimpleNamespace(ws=ws, traces=None, worker=None, bridges={})
        me = {"kind": "human", "id": "cli"}
        s1 = ws.register_session(me, client="cursor", mode="appserver", label="Cursor")["id"]
        moved = CursorBridge(state, s1, str(exe), "aaaaaaaa-1111-4111-8111-111111111111", folder=str(tmp_path / "Old name"))
        assert moved.deliver("> hello again\n", "d1")["state"] == "accepted"
        assert _until(lambda: moved.active_turn is None, timeout=20)
        assert moved.last_error is None, moved.last_error
        assert "--resume" not in _calls(log)[-1]["args"]                    # a new chat, here
        assert moved.chat == CHAT and moved.folder == str(ws.root)
        assert moved.deliver("> and again\n", "d2")["state"] == "accepted"   # which the next message goes on with
        assert _until(lambda: moved.active_turn is None, timeout=20)
        a = _calls(log)[-1]["args"]
        assert a[a.index("--resume") + 1] == CHAT
        said = [e["payload"]["text"] for e in ws.events_since(0, limit=100, types=["agent.message"])["events"]]
        assert any("renamed or moved" in t for t in said), said
        # after a restart of the service, the same chat goes on
        state2 = types.SimpleNamespace(ws=ws, traces=None, worker=None, bridges={})
        assert reattach_cursor_sessions(state2) == [{"session_id": s1, "reattached": True}]
        assert state2.bridges[s1].chat == CHAT and state2.bridges[s1].folder == str(ws.root)
    finally:
        ws.close()


def test_installing_cursor_writes_its_skill_tools_and_rule_and_removes_only_those(tmp_path):
    from qccd.workspace import installers
    ws = Workspace.init(tmp_path / "ws")
    root = ws.root
    ws.close()
    mine = {"mcpServers": {"github": {"command": "gh-mcp"}}}
    (root / ".cursor").mkdir()
    (root / ".cursor" / "mcp.json").write_text(json.dumps(mine), encoding="utf-8")
    lines = installers.install(root, "cursor")
    assert any(x.startswith("skill      .cursor/skills/qccd") for x in lines), lines
    assert (root / ".cursor" / "skills" / "qccd" / "SKILL.md").is_file()
    doc = json.loads((root / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert doc["mcpServers"]["github"] == {"command": "gh-mcp"}                    # the person's server stays
    q = doc["mcpServers"]["qccd"]
    assert q["args"][-2:] == ["--root", str(root)] and q["env"]["QCCD_SESSION_ID"] == "${env:QCCD_SESSION_ID}"
    rule = (root / ".cursor" / "rules" / "qccd.mdc").read_text(encoding="utf-8")
    assert rule.startswith("---\n") and "alwaysApply: true" in rule and "qccd_apply_change_set" in rule
    st = installers.status(root, "cursor")
    assert st["skill"]["current"] and st["mcp"]["managed"] and st["pointer"]
    assert installers.install(root, "cursor")                                      # idempotent (a repair)
    out = installers.uninstall(root, "cursor")
    assert "removed    .cursor/mcp.json 'qccd'" in out and "removed    .cursor/rules/qccd.mdc" in out
    assert json.loads((root / ".cursor" / "mcp.json").read_text(encoding="utf-8")) == mine
    assert not (root / ".cursor" / "skills" / "qccd").exists()
    # a 'qccd' server the person wrote is never overwritten
    (root / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"qccd": {"command": "theirs"}}}),
                                               encoding="utf-8")
    with pytest.raises(RuntimeError, match="qccd did not write"):
        installers.install(root, "cursor")


def test_the_chat_starts_cursor_when_this_workspace_was_set_up_for_it(svc):
    from qccd.workspace import installers
    info, log = svc
    own, _ = _chat(info)
    installers.install(Path(info["root"]), "cursor")
    assert own("GET", "/api/agents")["installed"] == ["cursor"]
    js = (Path(__file__).resolve().parents[1] / "qccd" / "workspace" / "web" / "cowork.js").read_text(encoding="utf-8")
    assert "if (inst.length === 1 && S[inst[0]]) return inst[0];" in js
    assert "'/api/sessions/cursor/connect'" in js and "New conversation with Cursor" in js


from chrome_path import CHROME  # noqa: E402

needs_chrome = pytest.mark.skipif(not (shutil.which("node") and CHROME), reason="needs node and Chrome")


@needs_chrome
def test_the_studio_chat_offers_cursor_and_talks_to_it(svc, tmp_path):
    """In the person's Studio, with only Cursor on the computer: the header says Cursor starts when
    they send, the ⋯ menu offers a new conversation with Cursor, and a message comes back as its answer."""
    from test_workspace_claude import _chrome
    info, log = svc
    st = _chrome(info, [
        {"wait": "/Cursor starts when you send/.test((document.getElementById('qcl-sub') || {}).textContent || '')",
         "timeout": 30000},
        {"eval": "document.getElementById('qcl-more').click(); document.getElementById('qcl-menu').textContent"},
        {"eval": "QCCD_LIVE.chatSend('What does rule R7 say?').then(function(d){ return !!d; })"},
        {"wait": "QCCD_LIVE.chat().items.some(function(i){ return i.type === 'agent' && "
                 "/Fake Cursor heard: What does rule R7 say/.test(i.text); })", "timeout": 30000},
        {"eval": "document.getElementById('qcl-who').textContent + ' | ' + QCCD_LIVE.chat().cursor"},
    ], tmp_path, "cursor")
    assert st and all(x.get("ok", "error" not in x) for x in st), st
    assert "New conversation with Cursor" in st[1]["value"] and "with Claude" not in st[1]["value"], st[1]
    assert st[4]["value"] == "Cursor | true", st[4]
    cursor = [s for s in service_request(info, "GET", "/api/sessions", token="owner")["sessions"] if s["client"] == "cursor"]
    assert len(cursor) == 1 and _calls(log)[0]["session"] == cursor[0]["id"]
