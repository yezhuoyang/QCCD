"""Claude Code as a chat agent: one headless `claude -p` run per message, one conversation.

The Codex bridge (`codex.py`) talks to a long-lived app server.  Claude Code has no such
server, but its print mode resumes a conversation by id: the workspace fixes the id up
front (`--session-id` on the first run, `--resume` after), so every message the person sends
from Studio or a website page is the next turn of the SAME Claude conversation, with the
QCCD tools attached.  The bridge has the interface the delivery worker uses for Codex
(deliver / busy / interrupt / status), so sessions of both kinds are `appserver` sessions.

Each run: the delivery text on stdin, `--output-format stream-json`, the QCCD MCP server
(attributed to this session by QCCD_SESSION_ID), `--permission-mode dontAsk` with only the
QCCD tools and read-only file tools allowed -- nobody can answer a permission prompt from
Studio, so there is none; like Codex here, it has no shell and edits nothing on disk.  What
Claude writes is kept as the request's replies; a run that fails says why in the chat.
`QCCD_CLAUDE=none` turns the bridge off; `QCCD_CLAUDE=<path>` names the executable.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("qccd.claude")

__all__ = ["find_claude", "connect_claude", "reattach_claude_sessions", "ClaudeBridge"]

ALLOWED = "mcp__qccd Read Grep Glob"
#: a run is one `claude -p` process that exits when the turn ends, so nothing it schedules outlives the
#: turn: a live agent told the person it had "set a check every 4 minutes" to report a grade -- a
#: session-only cron that died with the process (2026-09-24).  Waiting tools that cannot work here go too.
DENIED = ("Bash Edit Write NotebookEdit CronCreate CronDelete CronList ScheduleWakeup Monitor RemoteTrigger "
          "PushNotification Agent Task")
#: every text Claude writes in a run is shown in the person's chat as it comes
SYSTEM_NOTE = ("You are chatting with the person in QCCD Studio, on their computer. Everything you write appears "
               "in their chat as you write it: address them directly (you, not 'the user'), keep in-between "
               "notes to a short line, and end with the answer itself. Your turn ends when you stop, and "
               "nothing outlives it: follow a job to its end with qccd_get_job(job_id, wait_s=50), and if you "
               "stop before it finishes, say where its result will appear (the Studio's Results) -- never "
               "promise to report back later.")
CAPABILITIES = {"deliver": "claude -p, one run per message, resuming one conversation",
                "steer": None, "interrupt": "stops the running message",
                "observe": "stream-json events of each run", "acknowledgement": "the run starting"}


def find_claude() -> str | None:
    v = os.environ.get("QCCD_CLAUDE")
    if v == "none":
        return None
    if v:
        return v if Path(v).is_file() else None
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if cand.is_file():
            return str(cand)
    return None


class ClaudeBridge:
    def __init__(self, state, session_id: str, exe: str, conversation: str, started: bool, model: str | None = None,
                 effort: str | None = None):
        self.state = state
        self.sid = session_id
        self.exe = exe
        self.conversation = conversation          # the Claude session id (a uuid)
        self.started = started                   # the conversation exists: --resume, else --session-id
        self.model = model                       # --model (an alias such as opus), None: Claude Code's default
        self.effort = effort                     # --effort (low ... max), None: the model's default
        self.connected = True
        self.proc: subprocess.Popen | None = None
        self.active_turn: str | None = None
        self.turn_deliveries: dict = {}
        self.last_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the worker's interface
    def deliver(self, text: str, delivery_id: str, steer: bool = False) -> dict:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return {"state": "busy", "active_turn": self.active_turn}
            turn = "ct_" + secrets.token_hex(6)
            self.active_turn = turn
            self.turn_deliveries[turn] = delivery_id
            ws = self.state.ws
            cfg = ws.root / ".qccd" / "claude-mcp.json"
            from ..installers import mcp_server_config
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(json.dumps({"mcpServers": {"qccd": mcp_server_config(ws.root, "claude", session_id=self.sid)}}),
                           encoding="utf-8")
            args = [self.exe, "-p", "--output-format", "stream-json", "--verbose",
                    "--mcp-config", str(cfg), "--strict-mcp-config",
                    "--permission-mode", "dontAsk", "--allowedTools", ALLOWED, "--disallowedTools", DENIED]
            args += ["--resume", self.conversation] if self.started else ["--session-id", self.conversation]
            args += ["--append-system-prompt", SYSTEM_NOTE]
            if self.model:
                args += ["--model", self.model]
            if self.effort:
                args += ["--effort", self.effort]
            # with tool search on, `claude -p` starts before the QCCD server connects and the agent
            # spends its first turns looking the tools up; off, it waits and has all of them at
            # once (measured on Claude Code 2.1.199: init "pending" with 0 tools vs "connected", 20)
            env = dict(os.environ, QCCD_SESSION_ID=self.sid, ENABLE_TOOL_SEARCH="false")
            kw: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                        "cwd": str(ws.root), "env": env, "text": True, "encoding": "utf-8", "errors": "replace"}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000                      # no console window
            self.proc = subprocess.Popen(args, **kw)
            self.started = True
            self._remember()
            proc = self.proc
            tr = getattr(self.state, "traces", None)
            if tr is not None:
                tr.delivered(self.sid, delivery_id, text, turn=turn, how="claude -p")
        proc.stdin.write(text)
        proc.stdin.close()
        threading.Thread(target=self._read, args=(proc, turn), name=f"claude-{turn}", daemon=True).start()
        return {"state": "accepted", "turn_id": turn, "how": "a claude -p run"}

    def reconcile(self, delivery_id: str):
        return None                               # a run either started (accepted) or did not

    def interrupt(self) -> dict:
        p = self.proc
        if p is None or p.poll() is not None:
            return {"supported": True, "interrupted": False, "message": "nothing is running"}
        p.kill()
        return {"supported": True, "interrupted": True}

    def status(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None
        return {"connected": self.connected, "active_turn": self.active_turn if running else None,
                "conversation": self.conversation, "last_error": self.last_error}

    def close(self) -> None:
        self.interrupt()
        self.connected = False

    # ------------------------------------------------------------------ one run
    def _remember(self) -> None:
        """The session keeps what a restarted service needs to go on with the same conversation."""
        caps = dict(CAPABILITIES)
        caps["reattach"] = {"conversation": self.conversation, "started": self.started, "model": self.model,
                            "effort": self.effort}
        caps["settings"] = {"model": self.model or "", "effort": self.effort or ""}
        try:
            self.state.ws.session_status(self.sid, "connected", detail="Claude Code (headless)", capabilities=caps)
        except Exception:
            log.exception("recording the Claude conversation failed")

    def _trace(self, ev: dict, turn: str) -> None:
        """One stream-json event as trace steps: what Claude had, thought, called and got back."""
        tr = getattr(self.state, "traces", None)
        if tr is None:
            return
        rec = lambda kind, name, data, **kw: tr.record(self.sid, "agent", kind, name, data, turn=turn, **kw)
        kind = ev.get("type")
        try:
            if kind == "system" and ev.get("subtype") == "init":
                rec("session", "claude", {k: ev.get(k) for k in ("model", "permissionMode", "claude_code_version", "tools",
                                                                 "mcp_servers", "skills", "slash_commands", "agents",
                                                                 "output_style") if ev.get(k) is not None})
            elif kind == "assistant":
                for b in ((ev.get("message") or {}).get("content") or []):
                    t = b.get("type")
                    if t == "text":
                        rec("message", None, {"text": b.get("text")})
                    elif t in ("thinking", "redacted_thinking"):
                        rec("thinking", None, {"text": b.get("thinking") or ("[redacted]" if t == "redacted_thinking" else "")})
                    elif t == "tool_use":
                        rec("skill" if b.get("name") == "Skill" else "tool_call", b.get("name"),
                            {"id": b.get("id"), "input": b.get("input")})
            elif kind == "user":
                for b in ((ev.get("message") or {}).get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        c = b.get("content")
                        if isinstance(c, list):        # text parts as text; others (a tool_reference...) named
                            c = "\n".join((str(x.get("text") or "") if x.get("type") == "text" else
                                           f"[{x.get('type')}: {x.get('tool_name') or x.get('name') or ''}]".replace(": ]", "]"))
                                          if isinstance(x, dict) else str(x) for x in c)
                        rec("tool_result", None, {"id": b.get("tool_use_id"), "content": c, "is_error": bool(b.get("is_error"))})
            elif kind == "result":
                rec("turn", ev.get("subtype"), {k: ev.get(k) for k in ("is_error", "duration_ms", "duration_api_ms", "num_turns",
                                                                       "total_cost_usd", "usage", "result")
                                                if ev.get(k) is not None},
                    ms=ev.get("duration_ms") if isinstance(ev.get("duration_ms"), (int, float)) else None)
        except Exception:
            log.exception("tracing a Claude event failed")

    def _read(self, proc: subprocess.Popen, turn: str) -> None:
        from .codex import _store_message
        ws = self.state.ws
        ws.session_status(self.sid, "connected", detail=f"turn {turn} running")
        failure = None
        said = False
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("type")
                self._trace(ev, turn)
                if kind == "assistant":
                    for block in ((ev.get("message") or {}).get("content") or []):
                        if block.get("type") == "text" and str(block.get("text") or "").strip():
                            text = str(block["text"])[:4000]
                            ws.emit("agent.message", {"session_id": self.sid, "turn_id": turn, "text": text})
                            _store_message(self.state, self.sid, turn, text)
                            said = True
                elif kind == "result":
                    if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                        failure = str(ev.get("result") or ev.get("subtype") or "the run failed")[:1500]
                    elif not said and str(ev.get("result") or "").strip():
                        _store_message(self.state, self.sid, turn, str(ev["result"])[:4000])
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        rc = proc.wait()
        if rc not in (0, None) and failure is None:
            err = (proc.stderr.read() if proc.stderr else "")[-1500:]
            failure = err.strip() or f"claude exited with code {rc}"
        self.last_error = failure
        did = self.turn_deliveries.get(turn)
        ws.emit("agent.turn", {"session_id": self.sid, "turn_id": turn, "status": "failed" if failure else "completed",
                               "delivery_id": did, "error": failure})
        if failure:
            log.warning("Claude run %s failed: %s", turn, failure[:500])
            tr = getattr(self.state, "traces", None)
            if tr is not None:
                tr.record(self.sid, "agent", "error", "run failed", {"error": failure, "exit_code": rc}, turn=turn)
        if did:
            d = ws.store.one("SELECT prompt_id FROM deliveries WHERE id=?", (did,))
            if d:
                w = ws.store.one("SELECT state FROM work WHERE prompt_id=?", (d["prompt_id"],))
                if w and w["state"] in ("pending", "working"):
                    ws.set_work_state(d["prompt_id"], "waiting_input" if failure else "ready_for_review",
                                      f"Claude stopped: {failure}" if failure else "Claude answered")
        if self.state.worker:
            self.state.worker.kick()


def connect_claude(state, body: dict) -> dict:
    """A NEW Claude Code conversation bound to this workspace (called for the person)."""
    from ..app import WorkspaceError, new_id
    ws = state.ws
    exe = find_claude()
    if not exe:
        raise WorkspaceError("claude_missing", "no claude executable found (install Claude Code, or set QCCD_CLAUDE)",
                             status=424)
    sid = body.get("session_id") or new_id("s")
    conversation = str(uuid.uuid4())
    caps = dict(CAPABILITIES)
    model, effort = body.get("model") or None, body.get("effort") or None
    from .models import CLAUDE_EFFORTS
    if effort not in (None, *CLAUDE_EFFORTS):
        raise WorkspaceError("bad_request", f"Claude's thinking levels are {', '.join(CLAUDE_EFFORTS)}", status=422)
    caps["reattach"] = {"conversation": conversation, "started": False, "model": model, "effort": effort}
    caps["settings"] = {"model": model or "", "effort": effort or ""}
    s = ws.register_session({"kind": "human", "id": "cli"}, client="claude", mode="appserver",
                            label=body.get("label") or "Claude", runtime_ref=conversation, capabilities=caps,
                            session_id=sid)
    old = state.bridges.pop(s["id"], None)
    if old:
        old.close()
    state.bridges[s["id"]] = ClaudeBridge(state, s["id"], exe, conversation, started=False, model=model, effort=effort)
    return {"session": s, "conversation": conversation, "new_thread": True,
            "attach": f"claude --resume {conversation}   (in the workspace folder, to go on in a terminal)",
            "note": "a NEW Claude Code conversation was started for this workspace"}


def reattach_claude_sessions(state) -> list:
    """After a service restart: the same conversations go on (each run resumes by id)."""
    out = []
    exe = find_claude()
    for s in state.ws.sessions():
        if s["client"] != "claude" or s["mode"] != "appserver" or s["id"] in state.bridges:
            continue
        ra = (s.get("capabilities") or {}).get("reattach") or {}
        if not exe or not ra.get("conversation"):
            out.append({"session_id": s["id"], "reattached": False, "why": "no claude executable or conversation"})
            continue
        state.bridges[s["id"]] = ClaudeBridge(state, s["id"], exe, ra["conversation"], bool(ra.get("started")),
                                              model=ra.get("model"), effort=ra.get("effort"))
        state.ws.session_status(s["id"], "connected", detail="the same Claude conversation goes on")
        out.append({"session_id": s["id"], "reattached": True})
    return out
