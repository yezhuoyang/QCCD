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
DENIED = "Bash Edit Write NotebookEdit"
#: every text Claude writes in a run is shown in the person's chat as it comes
SYSTEM_NOTE = ("You are chatting with the person in QCCD Studio, on their computer. Everything you write appears "
               "in their chat as you write it: address them directly (you, not 'the user'), keep in-between "
               "notes to a short line, and end with the answer itself.")
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
    def __init__(self, state, session_id: str, exe: str, conversation: str, started: bool, model: str | None = None):
        self.state = state
        self.sid = session_id
        self.exe = exe
        self.conversation = conversation          # the Claude session id (a uuid)
        self.started = started                   # the conversation exists: --resume, else --session-id
        self.model = model
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
            env = dict(os.environ, QCCD_SESSION_ID=self.sid)
            kw: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                        "cwd": str(ws.root), "env": env, "text": True, "encoding": "utf-8", "errors": "replace"}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000                      # no console window
            self.proc = subprocess.Popen(args, **kw)
            self.started = True
            self._remember()
            proc = self.proc
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
        caps["reattach"] = {"conversation": self.conversation, "started": self.started, "model": self.model}
        try:
            self.state.ws.session_status(self.sid, "connected", detail="Claude Code (headless)", capabilities=caps)
        except Exception:
            log.exception("recording the Claude conversation failed")

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
    caps["reattach"] = {"conversation": conversation, "started": False, "model": body.get("model")}
    s = ws.register_session({"kind": "human", "id": "cli"}, client="claude", mode="appserver",
                            label=body.get("label") or "Claude", runtime_ref=conversation, capabilities=caps,
                            session_id=sid)
    old = state.bridges.pop(s["id"], None)
    if old:
        old.close()
    state.bridges[s["id"]] = ClaudeBridge(state, s["id"], exe, conversation, started=False, model=body.get("model"))
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
                                              model=ra.get("model"))
        state.ws.session_status(s["id"], "connected", detail="the same Claude conversation goes on")
        out.append({"session_id": s["id"], "reattached": True})
    return out
