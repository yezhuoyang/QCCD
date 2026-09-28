"""Cursor as a chat agent: one headless `cursor-agent -p` run per message, all in one chat.

Cursor's agent CLI (`cursor-agent`, checked on 2026.09.26) has a print mode that streams Claude
Code's kind of events (`--output-format stream-json`: system/init, assistant, thinking, tool_call
started/completed, result) but, unlike Claude Code, takes no stream of messages: a process takes
one prompt (on stdin, so any length and never on a command line) and exits when the turn ends.  So
each message is one run that `--resume`s the conversation's chat.  The first run starts the chat,
and its `system/init` event names it (a session's id is its chat's id).  The bridge has the interface the delivery worker uses for Codex and Claude (deliver / busy / interrupt /
status), so sessions of all three kinds are `appserver` sessions.

What a run may do is QCCD's choice, not the person's own Cursor settings': the runs read their
settings from the workspace's `.qccd/cursor/` (`CURSOR_CONFIG_DIR`), whose `cli-config.json`
allows the QCCD tools and denies every shell command and file write, and a headless run without
`--force` refuses whatever else needs an approval (nobody can answer one from Studio).  Like Codex
and Claude here, it has no shell and edits nothing on disk.  Its chats are kept in that folder too
(`chats/<md5 of the workspace folder>/<id>`), which is why a renamed or moved folder starts a new
chat.  The QCCD tools come from the workspace's `.cursor/mcp.json` (the entry `qccd agent install
--client cursor` writes; `--approve-mcps` loads it without asking): Cursor starts an MCP server
with only the environment that entry names, so it passes `${env:QCCD_SESSION_ID}` through and each
run sets it.  `QCCD_CURSOR=none` turns the bridge off; `QCCD_CURSOR=<path>` names the executable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
from pathlib import Path

from .claude import _kill_tree, _same_folder

log = logging.getLogger("qccd.cursor")

__all__ = ["find_cursor", "connect_cursor", "reattach_cursor_sessions", "CursorBridge", "prepare", "cursor_env"]

#: what every run may do: the QCCD tools (Cursor names a project's server `project-<n>-<folder>-qccd`),
#: and never a shell command or a file write, whatever the person allowed their own Cursor
PERMISSIONS = {"allow": ["Mcp(*-qccd:*)", "Mcp(qccd:*)"], "deny": ["Shell(*)", "Shell(**)", "Write(**)"]}
#: a run ends with its turn, so nothing it starts outlives it; the rest of Claude's SYSTEM_NOTE, which Cursor's
#: CLI has no flag for, goes with each message
CHAT_NOTE = ("(QCCD Studio: everything you write appears in the person's chat as you write it, so address them "
             "directly, keep in-between notes to a short line, and end with the answer itself. Your turn ends when "
             "you stop and nothing outlives it: never promise to report back later. Follow a job with "
             "qccd_get_job(job_id, wait_s=50); if it is still running after that, say where its result will appear "
             "and stop. You have no shell and cannot edit files: change the design only with the QCCD tools.)")
CAPABILITIES = {"deliver": "cursor-agent -p, one run per message, all in one chat (--resume)",
                "steer": None, "interrupt": "stops the running message",
                "observe": "stream-json events of each run", "acknowledgement": "the run starting"}
#: a chat id (a UUID today); anything else in `session_id` is not taken for one
_CHAT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{5,127}$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def find_cursor() -> str | None:
    """Cursor's agent CLI: $QCCD_CURSOR, PATH, or where its installer puts it (`~/.local/bin` on macOS
    and Linux, `%LOCALAPPDATA%\\cursor-agent` on Windows), which a service started by a desktop app
    does not have on its PATH.  The installer also names it `agent`, too common a name to look for."""
    v = os.environ.get("QCCD_CURSOR")
    if v and v.strip().lower() == "none":
        return None
    if v:
        return v if Path(v).is_file() else None
    found = shutil.which("cursor-agent")
    if found:
        return found
    home = Path.home()
    cands = [home / ".local" / "bin" / "cursor-agent", Path("/opt/homebrew/bin/cursor-agent"),
             Path("/usr/local/bin/cursor-agent")]
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent"
        cands = [base / "cursor-agent.exe", base / "cursor-agent.cmd"]
    for cand in cands:
        if cand.is_file():
            return str(cand)
    return None


#: how to get Cursor's CLI, which Cursor the editor does not include
INSTALL = ("curl https://cursor.com/install -fsS | bash" if os.name != "nt" else
           "irm 'https://cursor.com/install?win32=true' | iex")


def cli_state(root) -> dict:
    """Whether the chat can start Cursor here: its CLI found, and signed in (`cursor-agent status`).
    `state` is ready, missing, signed_out or unknown, and `next` the command to run when not ready."""
    exe = find_cursor()
    if not exe:
        # the installer puts it in ~/.local/bin, which a terminal may not have on its PATH yet
        login = "cursor-agent login" if os.name == "nt" else "~/.local/bin/cursor-agent login"
        return {"state": "missing", "next": f"install Cursor's CLI: {INSTALL}   then sign it in: {login}"}
    env = dict(os.environ, CURSOR_CONFIG_DIR=str(Path(root) / ".qccd" / "cursor"), NO_COLOR="1")
    try:
        r = subprocess.run([exe, "status", "--format", "json"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, env=env, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        got = json.loads(r.stdout[r.stdout.index("{"):]) if "{" in r.stdout else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        got = {}
    if got.get("isAuthenticated") is True:
        return {"state": "ready", "cli": exe, "account": (got.get("userInfo") or {}).get("email")}
    if got.get("isAuthenticated") is False:
        return {"state": "signed_out", "cli": exe, "next": f"sign Cursor's CLI in once: {exe} login"}
    return {"state": "unknown", "cli": exe, "next": f"check that Cursor's CLI is signed in: {exe} status"}


def config_dir(ws) -> Path:
    return Path(ws.root) / ".qccd" / "cursor"


def prepare(ws) -> Path:
    """What every run reads: its settings folder, and the QCCD server in the workspace's `.cursor/mcp.json`.
    Keys Cursor keeps in the settings file itself (its account's details) are left as they are."""
    from ..installers import write_cursor_mcp
    d = config_dir(ws)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "cli-config.json"
    try:
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        cur = {}
    cur = cur if isinstance(cur, dict) else {}
    # `allowlist`: anything not allowed above needs an approval, which a headless run refuses; the sandbox
    # off, so no command runs in one without asking
    want = dict(cur, version=1, permissions=PERMISSIONS, approvalMode="allowlist", autoAcceptWebSearch=False,
                sandbox=dict(cur.get("sandbox") if isinstance(cur.get("sandbox"), dict) else {}, mode="disabled"))
    if want != cur:
        p.write_text(json.dumps(want, indent=2) + "\n", encoding="utf-8")
    write_cursor_mcp(ws.root)
    return d


def cursor_env(ws, session_id: str | None = None) -> dict:
    env = dict(os.environ, CURSOR_CONFIG_DIR=str(config_dir(ws)), NO_COLOR="1")
    env.pop("QCCD_SESSION_ID", None)
    if session_id:
        env["QCCD_SESSION_ID"] = session_id
    if os.name != "nt":
        # Cursor keeps its login in the macOS keychain, found by user name (as Claude Code does)
        import getpass
        env.setdefault("USER", getpass.getuser())
        env.setdefault("LOGNAME", env["USER"])
    return env


def _popen_kw(ws, env: dict) -> dict:
    kw: dict = {"cwd": str(ws.root), "env": env, "text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000                      # no console window
    else:
        kw["start_new_session"] = True                        # its MCP servers go with it (_kill_tree)
    return kw


class CursorBridge:
    def __init__(self, state, session_id: str, exe: str, chat: str | None, model: str | None = None,
                 folder: str | None = None):
        self.state = state
        self.sid = session_id
        self.exe = exe
        self.chat = chat                         # the Cursor chat id, None until the first run names it
        self.model = model                       # --model <id>, None: Cursor's default
        # Cursor keeps a chat under the folder it ran in: a renamed or moved workspace starts a new one
        self.folder = folder or str(state.ws.root)
        self.connected = True
        self.proc: subprocess.Popen | None = None
        self.active_turn: str | None = None
        self.turn_deliveries: dict = {}
        self._said: dict = {}
        self._thinking: dict = {}
        self._stopped: set = set()
        self.last_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the worker's interface
    def deliver(self, text: str, delivery_id: str, steer: bool = False) -> dict:
        with self._lock:
            if self.active_turn is not None and self.proc is not None and self.proc.poll() is None:
                return {"state": "busy", "active_turn": self.active_turn}
            turn = "cu_" + secrets.token_hex(6)
            ws = self.state.ws
            if _same_folder(self.folder, ws.root) is False:
                if self.chat:
                    ws.emit("agent.message", {"session_id": self.sid, "turn_id": turn,
                                              "text": "(This workspace folder was renamed or moved, so Cursor starts "
                                                      "a new chat here. The earlier one stays with the old folder.)"})
                self.chat, self.folder = None, str(ws.root)          # this run starts one, and names it
            prompt = text + "\n\n" + CHAT_NOTE
            self.active_turn = turn
            self.turn_deliveries[turn] = delivery_id
            self._said[turn] = False
            tr = getattr(self.state, "traces", None)
            if tr is not None:
                tr.delivered(self.sid, delivery_id, prompt, turn=turn, how="cursor-agent -p, one run")
            try:
                self.proc = self._start(prompt, turn)
            except Exception:
                self.active_turn = None
                raise
            self._remember()
        return {"state": "accepted", "turn_id": turn, "how": "a cursor-agent -p run"}

    def _start(self, prompt: str, turn: str) -> subprocess.Popen:
        ws = self.state.ws
        prepare(ws)
        args = [self.exe, "-p", "--output-format", "stream-json", "--trust", "--approve-mcps",
                "--workspace", str(ws.root)]
        if self.chat:
            args += ["--resume", self.chat]
        if self.model:
            args += ["--model", self.model]
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             **_popen_kw(ws, cursor_env(ws, self.sid)))
        err: list = []

        def drain():                             # a full stderr pipe would stop the run
            for line in p.stderr:
                err.append(line)
                del err[:-40]
        threading.Thread(target=drain, name=f"cursor-err-{self.sid}", daemon=True).start()
        threading.Thread(target=self._read_loop, args=(p, turn, err), name=f"cursor-{self.sid}", daemon=True).start()
        try:
            p.stdin.write(prompt)
            p.stdin.close()
        except (OSError, ValueError):
            pass                                  # it ended already: the read loop says why
        return p

    def reconcile(self, delivery_id: str):
        return None                               # a run either started (accepted) or did not

    def interrupt(self) -> dict:
        p, turn = self.proc, self.active_turn
        if p is None or p.poll() is not None or turn is None:
            return {"supported": True, "interrupted": False, "message": "nothing is running"}
        self._stopped.add(turn)
        _kill_tree(p)                              # the next message resumes the same chat
        return {"supported": True, "interrupted": True}

    def status(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None and self.active_turn is not None
        return {"connected": self.connected, "active_turn": self.active_turn if running else None,
                "chat": self.chat, "last_error": self.last_error, "process": "running" if running else None}

    def close(self) -> None:
        self.connected = False
        p = self.proc
        if p is not None and p.poll() is None:
            _kill_tree(p)

    # ------------------------------------------------------------------ one run
    def _remember(self) -> None:
        """The session keeps what a restarted service needs to go on with the same chat."""
        caps = dict(CAPABILITIES)
        caps["reattach"] = {"chat": self.chat, "model": self.model, "folder": self.folder}
        caps["settings"] = {"model": self.model or "", "effort": ""}
        try:
            self.state.ws.session_status(self.sid, "connected", detail="Cursor (headless)", capabilities=caps)
        except Exception:
            log.exception("recording the Cursor chat failed")

    def _trace(self, ev: dict, turn: str) -> None:
        """One stream-json event as trace steps, named as Claude's are, so the trace and the program
        (atir.py) read both the same way."""
        tr = getattr(self.state, "traces", None)
        if tr is None:
            return
        rec = lambda kind, name, data, **kw: tr.record(self.sid, "agent", kind, name, data, turn=turn, **kw)
        kind, sub = ev.get("type"), ev.get("subtype")
        try:
            if kind == "system" and sub == "init":
                rec("session", "cursor", {k: ev.get(k) for k in ("model", "permissionMode", "apiKeySource", "cwd",
                                                                 "session_id") if ev.get(k) is not None})
            elif kind == "thinking":
                if sub == "delta":
                    self._thinking[turn] = self._thinking.get(turn, "") + str(ev.get("text") or "")
                elif sub == "completed":
                    rec("thinking", None, {"text": self._thinking.pop(turn, "")})
            elif kind == "assistant":
                text = _text(ev)
                if text.strip():
                    rec("message", None, {"text": text})
            elif kind == "tool_call":
                name, inp, res = _tool(ev.get("tool_call"))
                if sub == "started":
                    rec("tool_call", name, {"id": ev.get("call_id"), "input": inp})
                elif sub == "completed":
                    content, failed = _result(res)
                    rec("tool_result", None, {"id": ev.get("call_id"), "content": content, "is_error": failed})
            elif kind == "result":
                u = ev.get("usage") or {}
                data = {k: ev.get(k) for k in ("is_error", "duration_ms", "duration_api_ms", "result") if ev.get(k) is not None}
                if u:
                    data["usage"] = {"input_tokens": u.get("inputTokens"), "output_tokens": u.get("outputTokens"),
                                     "cache_read_input_tokens": u.get("cacheReadTokens"),
                                     "cache_creation_input_tokens": u.get("cacheWriteTokens")}
                rec("turn", sub, data, ms=ev.get("duration_ms") if isinstance(ev.get("duration_ms"), (int, float)) else None)
        except Exception:
            log.exception("tracing a Cursor event failed")

    def _read_loop(self, proc: subprocess.Popen, turn: str, err: list) -> None:
        """Every event of one run; the run's turn ends with its `result` event, or with the process."""
        from .codex import _store_message
        ws = self.state.ws
        ended = False
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                self._trace(ev, turn)
                kind = ev.get("type")
                if kind == "system" and ev.get("subtype") == "init":
                    cid = str(ev.get("session_id") or "")
                    if _CHAT_ID.match(cid) and cid != self.chat:
                        self.chat = cid                   # the chat this run is in: the next one resumes it
                        self._remember()
                    ws.session_status(self.sid, "connected", detail=f"turn {turn} running")
                elif kind == "assistant":
                    text = _text(ev)
                    if text.strip():
                        text = text[:4000]
                        ws.emit("agent.message", {"session_id": self.sid, "turn_id": turn, "text": text})
                        _store_message(self.state, self.sid, turn, text)
                        self._said[turn] = True
                elif kind == "result":
                    ended = True
                    failure = None
                    if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                        failure = str(ev.get("result") or ev.get("subtype") or "the run failed")[:1500]
                    elif not self._said.get(turn) and str(ev.get("result") or "").strip():
                        _store_message(self.state, self.sid, turn, str(ev["result"])[:4000])
                    self._finish(turn, failure, None)
        except Exception as exc:
            ended = True
            self._finish(turn, f"{type(exc).__name__}: {exc}", None)
        rc = proc.wait()
        if not ended and turn in self._stopped:
            self._finish(turn, None, rc, status="interrupted")      # the person stopped it: nothing failed
        elif not ended:
            # no result: Cursor says why on stderr (not signed in, a usage limit)
            self._finish(turn, _ANSI.sub("", "".join(err)).strip()[-1500:] or f"cursor-agent exited with code {rc}", rc)
        self._stopped.discard(turn)

    def _finish(self, turn: str, failure: str | None, rc, status: str | None = None) -> None:
        """A turn ended: say so to the chat, the trace and the delivery worker."""
        failure = explain_failure(failure)
        ws = self.state.ws
        with self._lock:
            if self.active_turn != turn:
                return
            self.active_turn = None
        self._said.pop(turn, None)
        self._thinking.pop(turn, None)
        self.last_error = failure
        did = self.turn_deliveries.get(turn)
        ws.emit("agent.turn", {"session_id": self.sid, "turn_id": turn,
                               "status": status or ("failed" if failure else "completed"),
                               "delivery_id": did, "error": failure})
        if failure:
            log.warning("Cursor run %s failed: %s", turn, failure[:500])
            tr = getattr(self.state, "traces", None)
            if tr is not None:
                tr.record(self.sid, "agent", "error", "run failed", {"error": failure, "exit_code": rc}, turn=turn)
        if did and status != "interrupted":           # a stop already marked the request (stop_session)
            d = ws.store.one("SELECT prompt_id FROM deliveries WHERE id=?", (did,))
            if d:
                w = ws.store.one("SELECT state FROM work WHERE prompt_id=?", (d["prompt_id"],))
                if w and w["state"] in ("pending", "working"):
                    ws.set_work_state(d["prompt_id"], "waiting_input" if failure else "ready_for_review",
                                      f"Cursor stopped: {failure}" if failure else "Cursor answered")
        if self.state.worker:
            self.state.worker.kick()


def _text(ev: dict) -> str:
    return "".join(str(b.get("text") or "") for b in ((ev.get("message") or {}).get("content") or [])
                   if isinstance(b, dict) and b.get("type") == "text")


def _tool(tc) -> tuple:
    """(name, input, result) of one Cursor tool call, `{"<kind>ToolCall": {"args": ..., "result": ...}}`.
    An MCP call is named `mcp__<server>__<tool>` as Claude Code names it; the QCCD server, which Cursor
    calls `project-<n>-<folder>-qccd`, is `qccd`."""
    if not isinstance(tc, dict) or not tc:
        return "tool", None, None
    case, v = next(iter(tc.items()))
    v = v if isinstance(v, dict) else {}
    args = v.get("args")
    if case == "mcpToolCall":
        a = args if isinstance(args, dict) else {}
        server = str(a.get("providerIdentifier") or "").split("::mcpScope:")[0]
        server = "qccd" if server == "qccd" or server.endswith("-qccd") else server
        inp = a.get("args")
        if isinstance(inp, str):
            try:
                inp = json.loads(inp)
            except ValueError:
                pass
        return f"mcp__{server}__{a.get('toolName') or a.get('name') or ''}", inp, v.get("result")
    if case == "function":
        inp = v.get("arguments")
        try:
            inp = json.loads(inp) if isinstance(inp, str) else inp
        except ValueError:
            pass
        return str(v.get("name") or "function"), inp, v.get("result")
    return (case[:-len("ToolCall")] if case.endswith("ToolCall") else case), args, v.get("result")


def _result(res) -> tuple:
    """A tool result as (text, failed): `{"success": {...}}`, or an error or a refusal."""
    if res is None:
        return "", False
    if isinstance(res, dict) and len(res) == 1:
        (k, body), = res.items()
        failed = k != "success"
        if isinstance(body, dict):
            items = body.get("content")
            if isinstance(items, list):
                parts = []
                for it in items:
                    t = it.get("text") if isinstance(it, dict) else None
                    t = t.get("text") if isinstance(t, dict) else t
                    parts.append(str(t) if t is not None else json.dumps(it)[:500])
                return "\n".join(parts), failed or bool(body.get("isError"))
            for key in ("error", "reason", "message", "content", "output"):
                if isinstance(body.get(key), str):
                    return body[key], failed
        return json.dumps(body)[:12000], failed
    return json.dumps(res)[:12000], False


def explain_failure(failure: str | None) -> str | None:
    """Cursor's own words, plus what the person does about them."""
    if not failure:
        return failure
    low = failure.lower()
    if "authentication required" in low or "not logged in" in low or " login" in low:
        return (failure.rstrip(". ") + ". Cursor's CLI is not signed in on this computer: in a terminal run "
                "`cursor-agent login` (or give the service a CURSOR_API_KEY), then send your message again.")
    return failure


def connect_cursor(state, body: dict) -> dict:
    """A NEW Cursor chat bound to this workspace (called for the person)."""
    from ..app import WorkspaceError, new_id
    ws = state.ws
    exe = find_cursor()
    if not exe:
        raise WorkspaceError("cursor_missing", "no cursor-agent executable found (install Cursor's CLI: "
                             "curl https://cursor.com/install -fsS | bash, or set QCCD_CURSOR)", status=424)
    if body.get("effort"):
        raise WorkspaceError("bad_request", "Cursor's thinking level is part of its model: pick a model", status=422)
    try:
        prepare(ws)
    except (RuntimeError, OSError, ValueError) as exc:
        raise WorkspaceError("cursor_config", str(exc), status=409) from None
    sid = body.get("session_id") or new_id("s")
    model = body.get("model") or None
    caps = dict(CAPABILITIES)
    caps["reattach"] = {"chat": None, "model": model, "folder": str(ws.root)}
    caps["settings"] = {"model": model or "", "effort": ""}
    s = ws.register_session({"kind": "human", "id": "cli"}, client="cursor", mode="appserver",
                            label=body.get("label") or "Cursor", capabilities=caps, session_id=sid)
    old = state.bridges.pop(s["id"], None)
    if old:
        old.close()
    state.bridges[s["id"]] = CursorBridge(state, s["id"], exe, None, model=model)
    here = str(config_dir(ws))
    attach = (f'$env:CURSOR_CONFIG_DIR="{here}"; cursor-agent --resume <chat>' if os.name == "nt" else
              f'CURSOR_CONFIG_DIR="{here}" cursor-agent --resume <chat>')
    return {"session": s, "new_thread": True,
            "attach": attach + "   (in the workspace folder, to go on in a terminal; after the first message, "
                               "the session names the chat)",
            "note": "a NEW Cursor chat: the first message starts it in this workspace"}


def reattach_cursor_sessions(state) -> list:
    """After a service restart: the same chats go on (each run resumes by id)."""
    out = []
    exe = find_cursor()
    for s in state.ws.sessions():
        if s["client"] != "cursor" or s["mode"] != "appserver" or s["id"] in state.bridges:
            continue
        if not exe:
            out.append({"session_id": s["id"], "reattached": False, "why": "no cursor-agent executable"})
            continue
        ra = (s.get("capabilities") or {}).get("reattach") or {}
        state.bridges[s["id"]] = CursorBridge(state, s["id"], exe, ra.get("chat"), model=ra.get("model"),
                                              folder=ra.get("folder") or str(state.ws.root))
        state.ws.session_status(s["id"], "connected", detail="the same Cursor chat goes on")
        out.append({"session_id": s["id"], "reattached": True})
    return out
