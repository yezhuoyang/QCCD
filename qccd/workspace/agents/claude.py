"""Claude Code as a chat agent: one long-lived headless `claude -p` process per conversation.

The Codex bridge (`codex.py`) talks to a long-lived app server.  Claude Code has no such
server, but its print mode takes its messages as a stream (`--input-format stream-json`): one
process, kept alive, takes each message the person sends from Studio or a website page as the
next turn of the SAME conversation, with the QCCD tools attached.  Starting a process costs
about 5.5 s of Claude Code's own start-up plus the QCCD tool server's (measured 2026-09-26);
a kept-alive process answers a short message in about a second.  The conversation's id is fixed
up front (`--session-id` for a new one, `--resume` when a process has to be started again: after
a restart of the service, a change of model or thinking level, an interrupt, or 20 idle minutes).
The bridge has the interface the delivery worker uses for Codex (deliver / busy / interrupt /
status), so sessions of both kinds are `appserver` sessions.

Each message: the delivery text as a stream-json user message, `--output-format stream-json`, the QCCD MCP server
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
               "nothing outlives it: never promise to report back later. Follow a job with "
               "qccd_get_job(job_id, wait_s=50) (on a submission it waits through the grade); if it is still "
               "running after that, say where its result will appear (a submission's card in this chat, or the "
               "Studio's Results) and stop.")
#: a kept-alive process with nothing to do for this long is stopped (the next message starts one again)
IDLE_S = 1200
#: Claude Code's own tools this agent is given: reading files, and waiting for the QCCD tools while they
#: connect.  Its other built-ins (web search, worktrees, to-do lists, ...) and its bundled skills were only
#: ever a detour: two live runs lost ~9 s each to an accidental `deep-research` skill (2026-09-26)
BUILTIN_TOOLS = "Read,Grep,Glob,WaitForMcpServers"
#: the thinking level when the person picked none: measured on the two-triangle BB request, `medium`
#: halved each model call (4.2 s against 8.3 s at Claude Code's default) with the same design and result
DEFAULT_EFFORT = "medium"
CAPABILITIES = {"deliver": "claude -p, one kept-alive process per conversation, one message per turn",
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
                 effort: str | None = None, folder: str | None = None):
        self.state = state
        self.sid = session_id
        self.exe = exe
        self.conversation = conversation          # the Claude session id (a uuid)
        self.started = started                   # the conversation exists: --resume, else --session-id
        self.model = model                       # --model (an alias such as opus), None: Claude Code's default
        self.effort = effort                     # --effort (low ... max), None: the model's default
        # the folder the conversation lives in: Claude Code files conversations by the folder it ran
        # in, so a workspace folder renamed or moved (the person may call it anything, and change
        # their mind) cannot `--resume` the old one; it starts a new conversation there instead
        self.folder = folder or str(state.ws.root)
        self.connected = True
        self.proc: subprocess.Popen | None = None
        self._proc_key: tuple | None = None       # what the running process was started with
        self._spoken = False                     # the running process has been sent a message
        self._idle_since = time.monotonic()      # when the process last finished something
        self.active_turn: str | None = None
        self.turn_deliveries: dict = {}
        self._said: dict = {}
        self.last_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the worker's interface
    def warm(self) -> None:
        """Start the process before the first message: that message then skips Claude Code's start-up."""
        with self._lock:
            if not self.connected or self.active_turn is not None:
                return
            if self.started and _same_folder(self.folder, self.state.ws.root) is False:
                return            # a renamed folder: the next message starts a new conversation here, and says so
            self._arm_idle(self._ensure_proc())

    def deliver(self, text: str, delivery_id: str, steer: bool = False) -> dict:
        with self._lock:
            if self.active_turn is not None and self.proc is not None and self.proc.poll() is None:
                return {"state": "busy", "active_turn": self.active_turn}
            turn = "ct_" + secrets.token_hex(6)
            ws = self.state.ws
            if self.started and _same_folder(self.folder, ws.root) is False:
                self.conversation, self.started, self.folder = str(uuid.uuid4()), False, str(ws.root)
                ws.emit("agent.message", {"session_id": self.sid, "turn_id": turn,
                                          "text": "(This workspace folder was renamed or moved, so Claude starts a "
                                                  "new conversation here. The earlier one stays with the old folder.)"})
            fresh = not (self.proc is not None and self.proc.poll() is None and self._proc_key == self._key())
            proc = self._ensure_proc()
            self.active_turn = turn
            self.turn_deliveries[turn] = delivery_id
            self._said[turn] = False
            self.started = self._spoken = True
            self._remember()
            tr = getattr(self.state, "traces", None)
            if tr is not None:
                tr.delivered(self.sid, delivery_id, text, turn=turn,
                             how="claude -p, a new process" if fresh else "claude -p, the kept-alive process")
            line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (OSError, ValueError):
                # the process ended between messages: start it again on the same conversation, once
                self._stop_proc()
                proc = self._ensure_proc()
                proc.stdin.write(line)
                proc.stdin.flush()
        return {"state": "accepted", "turn_id": turn,
                "how": "a new claude -p process" if fresh else "the kept-alive claude -p process"}

    def _key(self) -> tuple:
        return (self.conversation, self.model, self.effort, str(self.state.ws.root))

    def _ensure_proc(self) -> subprocess.Popen:
        """The running process, if it was started with what the conversation needs now; else a new one.
        Called with the lock held."""
        if self.proc is not None and self.proc.poll() is None and self._proc_key == self._key():
            return self.proc
        if self.proc is not None and self.proc.poll() is None and not self._spoken and not self.started:
            # warmed but never spoken to: its conversation id was never used, and a second process
            # may not claim the same id, so this one gets a fresh id
            self._stop_proc()
            self.conversation = str(uuid.uuid4())
        self._stop_proc()
        ws = self.state.ws
        cfg = ws.root / ".qccd" / "claude-mcp.json"
        from ..installers import mcp_server_config
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"mcpServers": {"qccd": mcp_server_config(ws.root, "claude", session_id=self.sid)}}),
                       encoding="utf-8")
        args = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", BUILTIN_TOOLS, "--disable-slash-commands",
                "--permission-mode", "dontAsk", "--allowedTools", ALLOWED, "--disallowedTools", DENIED]
        args += ["--resume", self.conversation] if self.started else ["--session-id", self.conversation]
        args += ["--append-system-prompt", SYSTEM_NOTE]
        if self.model:
            args += ["--model", self.model]
        args += ["--effort", self.effort or DEFAULT_EFFORT]
        # with tool search on, `claude -p` starts before the QCCD server connects and the agent
        # spends its first turns looking the tools up; off, it waits and has all of them at
        # once (measured on Claude Code 2.1.199: init "pending" with 0 tools vs "connected", 20)
        env = dict(os.environ, QCCD_SESSION_ID=self.sid, ENABLE_TOOL_SEARCH="false")
        kw: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                    "cwd": str(ws.root), "env": env, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000                      # no console window
        self.proc = subprocess.Popen(args, **kw)
        self._proc_key = self._key()
        self._spoken = False
        threading.Thread(target=self._read_loop, args=(self.proc,), name=f"claude-{self.sid}", daemon=True).start()
        return self.proc

    def _stop_proc(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()                    # a stream-json process ends when its input does
            except (OSError, ValueError):
                pass
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _kill_tree(p)

    def reconcile(self, delivery_id: str):
        return None                               # a run either started (accepted) or did not

    def interrupt(self) -> dict:
        p = self.proc
        if p is None or p.poll() is not None or self.active_turn is None:
            return {"supported": True, "interrupted": False, "message": "nothing is running"}
        _kill_tree(p)                              # the next message starts the process again (--resume)
        return {"supported": True, "interrupted": True}

    def status(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None and self.active_turn is not None
        return {"connected": self.connected, "active_turn": self.active_turn if running else None,
                "conversation": self.conversation, "last_error": self.last_error,
                "process": "kept alive" if self.proc is not None and self.proc.poll() is None else None}

    def close(self) -> None:
        self.connected = False
        p = self.proc
        if p is not None and p.poll() is None:
            _kill_tree(p)

    # ------------------------------------------------------------------ one run
    def _remember(self) -> None:
        """The session keeps what a restarted service needs to go on with the same conversation."""
        caps = dict(CAPABILITIES)
        caps["reattach"] = {"conversation": self.conversation, "started": self.started, "model": self.model,
                            "effort": self.effort, "folder": self.folder}
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
                msg = ev.get("message") or {}
                if isinstance(msg.get("usage"), dict):
                    # one model call's cost, per event (a call's blocks arrive as several events that
                    # share its id): the context it read is the agent's memory at that instruction
                    rec("model", msg.get("id"), {"usage": msg["usage"], "model": msg.get("model"),
                                                 "stop_reason": msg.get("stop_reason")})
                for b in (msg.get("content") or []):
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

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """Every event of one process, turn after turn; a turn ends with its `result` event."""
        from .codex import _store_message
        ws = self.state.ws
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                turn = self.active_turn
                if turn is None:
                    continue                          # nothing asked: nothing to attribute
                kind = ev.get("type")
                self._trace(ev, turn)
                if kind == "system" and ev.get("subtype") == "init":
                    ws.session_status(self.sid, "connected", detail=f"turn {turn} running")
                elif kind == "assistant":
                    for block in ((ev.get("message") or {}).get("content") or []):
                        if block.get("type") == "text" and str(block.get("text") or "").strip():
                            text = str(block["text"])[:4000]
                            ws.emit("agent.message", {"session_id": self.sid, "turn_id": turn, "text": text})
                            _store_message(self.state, self.sid, turn, text)
                            self._said[turn] = True
                elif kind == "result":
                    failure = None
                    if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                        failure = str(ev.get("result") or ev.get("subtype") or "the run failed")[:1500]
                    elif not self._said.get(turn) and str(ev.get("result") or "").strip():
                        _store_message(self.state, self.sid, turn, str(ev["result"])[:4000])
                    self._finish(turn, failure, None)
        except Exception as exc:
            turn = self.active_turn
            if turn is not None:
                self._finish(turn, f"{type(exc).__name__}: {exc}", None)
        rc = proc.wait()
        turn = self.active_turn
        if turn is not None and (self.proc is proc or self.proc is None):
            # the process ended in the middle of a turn: an interrupt, a crash, or a refusal to start
            err = (proc.stderr.read() if proc.stderr else "")[-1500:]
            self._finish(turn, err.strip() or f"claude exited with code {rc}", rc)

    def _arm_idle(self, proc: subprocess.Popen) -> None:
        """From now, the process is idle: stop it once it has been idle for IDLE_S."""
        self._idle_since = time.monotonic()
        t = threading.Timer(IDLE_S, self._idle_stop, args=(proc,))
        t.daemon = True
        t.start()

    def _idle_stop(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self.proc is not proc or self.active_turn is not None or proc.poll() is not None:
                return
            idle = time.monotonic() - self._idle_since
            if idle < IDLE_S - 0.05:               # it worked since this timer was set: wait out the rest
                t = threading.Timer(IDLE_S - idle, self._idle_stop, args=(proc,))
                t.daemon = True
                t.start()
                return
            self._stop_proc()

    def _finish(self, turn: str, failure: str | None, rc) -> None:
        """A turn ended: say so to the chat, the trace and the delivery worker."""
        ws = self.state.ws
        with self._lock:
            if self.active_turn != turn:
                return
            self.active_turn = None
            proc = self.proc
        self._said.pop(turn, None)
        if proc is not None and proc.poll() is None:
            self._arm_idle(proc)
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
    br = ClaudeBridge(state, s["id"], exe, conversation, started=False, model=model, effort=effort)
    state.bridges[s["id"]] = br
    # started now, so the first message does not wait for Claude Code to start
    threading.Thread(target=br.warm, name=f"claude-warm-{s['id']}", daemon=True).start()
    return {"session": s, "conversation": conversation, "new_thread": True,
            "attach": f"claude --resume {conversation}   (in the workspace folder, to go on in a terminal)",
            "note": "a NEW Claude Code conversation was started for this workspace"}


def _kill_tree(p: subprocess.Popen) -> None:
    """End a process and the ones it started: a `claude.cmd` (an npm install) is a cmd.exe whose
    node would outlive a plain kill, still holding the conversation."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=10,
                           creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError):
            pass
    if p.poll() is None:
        p.kill()


def _same_folder(a, b) -> bool | None:
    """Whether two paths name one folder (None when that cannot be told)."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, TypeError, ValueError):
        return None


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
                                              model=ra.get("model"), effort=ra.get("effort"),
                                              # a session from before folders were recorded: assume it is here
                                              folder=ra.get("folder") or str(state.ws.root))
        state.ws.session_status(s["id"], "connected", detail="the same Claude conversation goes on")
        out.append({"session_id": s["id"], "reattached": True})
    return out
