"""The Codex adapter: a client of the Codex app-server protocol.

Verified against the installed Codex CLI (0.154.0-alpha.6.2): the method names and
fields below are those of `codex app-server generate-json-schema` for that version and of
the app-server documentation -- `initialize`/`initialized`, `thread/start`,
`thread/resume`, `thread/turns/list`, `turn/start` (with `clientUserMessageId`),
`turn/steer` (with the `expectedTurnId` precondition), `turn/interrupt`, and the
notifications `turn/started`, `turn/completed`, `thread/status/changed`,
`item/agentMessage/delta`, `item/completed`.  Frames are JSON-RPC without the
`"jsonrpc"` member, one JSON object per WebSocket message.

The EXISTING-SESSION mode is the documented one: an app server listening on loopback
(`codex app-server --listen ws://127.0.0.1:PORT`), the user's terminal attached to it
(`codex --remote ws://127.0.0.1:PORT`), and this bridge attached as a second client to
the same thread.  A prompt sent from Studio then starts a turn in the conversation the
user is watching.  If the bridge starts the thread itself (`thread/start`), it says so:
that is a new conversation, not the user's existing one.

Delivery semantics, stated plainly: `turn/start` answering with a turn id makes the
delivery `accepted` (the runtime took the input and started work); it is not evidence the
model read or finished it.  A delivery whose outcome is unknown (the connection dropped
mid-request) is `uncertain` and is reconciled by listing the thread's recent turns and
looking for the delivery id, before anything is sent again.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import logging
from pathlib import Path
from typing import Any, Callable

__all__ = ["CodexBridge", "connect_codex", "find_codex", "CAPABILITIES"]

log = logging.getLogger("qccd.codex")

CAPABILITIES = {
    "deliver": "turn/start", "steer": "turn/steer (expectedTurnId precondition)",
    "interrupt": "turn/interrupt", "observe": "turn/*, item/*, thread/status/changed notifications",
    "reconcile": "thread/turns/list", "acknowledgement": "turn id from turn/start (runtime acceptance)",
    "existing_session": "yes, when the user's codex --remote attaches to the same app server",
}


def find_codex() -> str | None:
    """The codex executable: $QCCD_CODEX, PATH, or the desktop app's bundled CLI.
    `QCCD_CODEX=none` means there is none (Studio then never starts one by itself)."""
    env = os.environ.get("QCCD_CODEX")
    if env and env.strip().lower() == "none":
        return None
    if env and Path(env).is_file():
        return env
    w = shutil.which("codex")
    if w:
        return w
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    if base.is_dir():
        cands = sorted(base.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            return str(cands[0])
    return None


class RpcError(Exception):
    def __init__(self, error: dict):
        super().__init__(error.get("message") if isinstance(error, dict) else str(error))
        self.error = error


class CodexBridge:
    """One WebSocket connection to one Codex app server, bound to one thread."""

    def __init__(self, url: str, *, token: str | None = None, on_event: Callable[[str, dict], None] | None = None,
                 client_version: str = "1"):
        self.url = url
        self.token = token
        self.on_event = on_event or (lambda m, p: None)
        self.client_version = client_version
        self.conn = None
        self.ids = itertools.count(1)
        self.pending: dict = {}
        self.lock = threading.Lock()
        self.thread_id: str | None = None
        self.active_turn: str | None = None
        self.thread_status: str = "unknown"
        self.connected = False
        self.last_error: str | None = None
        self.messages: dict = {}          # item id -> accumulated agent text
        self.turn_deliveries: dict = {}   # turn id -> delivery id
        self.reader: threading.Thread | None = None
        self.server_process: subprocess.Popen | None = None
        #: per-turn overrides the user chose at connect time (e.g. {"effort": "medium"})
        self.turn_overrides: dict = {}
        #: an app server this workspace started earlier and re-attached to after a restart
        self.owned_pid: int | None = None
        #: (session id, Traces): the turn's items go into the session's trace (trace.py)
        self.tracer: tuple | None = None

    # ------------------------------------------------------------------ transport

    def open(self, timeout: float = 15.0) -> dict:
        from websockets.sync.client import connect
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        self.conn = connect(self.url, additional_headers=headers, open_timeout=timeout, max_size=64 * 1024 * 1024)
        self.connected = True
        self.reader = threading.Thread(target=self._read, name="codex-bridge", daemon=True)
        self.reader.start()
        init = self.request("initialize", {"clientInfo": {"name": "qccd_workspace", "title": "QCCD Studio",
                                                          "version": self.client_version}})
        self.notify("initialized", {})
        return init

    def close(self) -> None:
        """Disconnect, and stop the app server if this workspace started it."""
        self.connected = False
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        if self.server_process is not None:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=10)      # released before anyone resumes the thread
            except Exception:
                pass
        elif self.owned_pid and _is_codex_process(int(self.owned_pid)):
            # a pid recorded before a restart: stop it only while it is still a Codex binary --
            # a pid the OS has since given to another program is left alone
            try:
                os.kill(int(self.owned_pid), 15)      # TerminateProcess on Windows
            except OSError:
                pass

    def _read(self) -> None:
        try:
            for raw in self.conn:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
                    q = self.pending.pop(msg["id"], None)
                    if q is not None:
                        q.put(msg)
                elif "method" in msg and "id" in msg:
                    self._server_request(msg)
                elif "method" in msg:
                    self._notification(msg["method"], msg.get("params") or {})
        except Exception as exc:
            if self.connected:                  # not our own close(): worth a traceback
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("codex bridge reader stopped")
        finally:
            self.connected = False
            for q in list(self.pending.values()):
                q.put({"error": {"message": "connection closed"}})
            self.pending.clear()
            self.on_event("bridge/closed", {"error": self.last_error})

    def _send(self, obj: dict) -> None:
        with self.lock:
            self.conn.send(json.dumps(obj))

    def request(self, method: str, params: dict, timeout: float = 60.0) -> Any:
        if not self.connected:
            raise RpcError({"message": "not connected"})
        rid = next(self.ids)
        q: queue.Queue = queue.Queue()
        self.pending[rid] = q
        self._send({"id": rid, "method": method, "params": params})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            self.pending.pop(rid, None)
            raise RpcError({"message": f"{method} timed out", "uncertain": True}) from None
        if "error" in msg:
            raise RpcError(msg["error"])
        return msg.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def _server_request(self, msg: dict) -> None:
        # Approval requests belong to the person at the Codex terminal, not to Studio.
        # The bridge does not answer them on the user's behalf; it reports them.
        self.on_event("codex/server_request", {"method": msg.get("method"), "id": msg.get("id"),
                                               "params": _brief(msg.get("params"))})

    def _notification(self, method: str, p: dict) -> None:
        tid = p.get("threadId") or (p.get("thread") or {}).get("id")
        if self.thread_id and tid and tid != self.thread_id:
            return
        self._trace(method, p)
        if method == "turn/started":
            self.active_turn = (p.get("turn") or {}).get("id")
            self.thread_status = "active"
        elif method == "turn/completed":
            t = p.get("turn") or {}
            if t.get("id") == self.active_turn:
                self.active_turn = None
            self.thread_status = "idle"
        elif method == "thread/status/changed":
            st = (p.get("status") or {}).get("type")
            self.thread_status = st or self.thread_status
            if st == "idle":
                self.active_turn = None
        elif method == "item/agentMessage/delta":
            iid = p.get("itemId")
            if iid:
                self.messages[iid] = self.messages.get(iid, "") + str(p.get("delta") or p.get("text") or "")
            return
        elif method == "item/completed":
            item = p.get("item") or {}
            if item.get("type") == "agentMessage":
                p = {"turnId": p.get("turnId"), "text": item.get("text") or self.messages.pop(item.get("id"), "")}
                method = "agent/message"
            else:
                return
        self.on_event(method, p)

    _TOOLISH = ("mcpToolCall", "commandExecution", "webSearch", "fileChange", "dynamicToolCall", "collabAgentToolCall")

    def _trace(self, method: str, p: dict) -> None:
        if self.tracer is None:
            return
        sid, tr = self.tracer
        turn = p.get("turnId") or (p.get("turn") or {}).get("id")
        try:
            if method == "turn/started":
                tr.turn_started(sid, turn)
            elif method == "turn/completed":
                t = p.get("turn") or {}
                tr.record(sid, "agent", "turn", t.get("status"), {"status": t.get("status"), "error": t.get("error")},
                          turn=turn)
            elif method in ("item/started", "item/completed"):
                item = p.get("item") or {}
                typ = item.get("type")
                if typ == "userMessage" or (method == "item/started" and typ not in self._TOOLISH):
                    return
                kind = {"agentMessage": "message", "reasoning": "thinking"}.get(typ, "item")
                ms = item.get("durationMs") if isinstance(item.get("durationMs"), (int, float)) else None
                tr.record(sid, "agent", kind, typ, dict(item, phase=method.split("/")[1]), turn=turn, ms=ms)
        except Exception:
            log.exception("tracing a Codex event failed")

    # ------------------------------------------------------------------ the contract

    def bind(self, *, thread_id: str | None, cwd: str, start: bool, developer_instructions: str | None = None,
             config: dict | None = None, sandbox: str | None = None, approval_policy: str | None = None) -> dict:
        """Attach to an existing thread (`thread/resume`) or start a NEW one.

        `config` is the app server's per-thread config override; the workspace passes the
        QCCD MCP server through it, so the thread has the tools without editing the user's
        global Codex config or requiring the project to be trusted."""
        extra: dict = {}
        if config:
            extra["config"] = config
        if sandbox:
            extra["sandbox"] = sandbox
        if approval_policy:
            extra["approvalPolicy"] = approval_policy
        if thread_id:
            r = self.request("thread/resume", {"threadId": thread_id, "excludeTurns": True, **extra})
            self.thread_id = ((r or {}).get("thread") or {}).get("id") or thread_id
            return {"thread_id": self.thread_id, "started": False}
        if not start:
            raise RpcError({"message": "no thread named and start=false"})
        params: dict = {"cwd": cwd, "serviceName": "qccd", **extra}
        if developer_instructions:
            params["developerInstructions"] = developer_instructions
        r = self.request("thread/start", params)
        self.thread_id = ((r or {}).get("thread") or {}).get("id")
        return {"thread_id": self.thread_id, "started": True}

    def loaded_threads(self) -> list:
        r = self.request("thread/loaded/list", {})
        return list((r or {}).get("data") or [])

    def deliver(self, text: str, delivery_id: str, *, steer: bool = False) -> dict:
        """`accepted` with the turn id, `busy` (left queued), or raises RpcError."""
        inp = [{"type": "text", "text": text}]
        how = "turn/start"
        if self.active_turn:
            if not steer:
                return {"state": "busy", "active_turn": self.active_turn}
            expected = self.active_turn
            try:
                if self.tracer is not None:
                    self.tracer[1].delivered(self.tracer[0], delivery_id, text, how="turn/steer", steer=True)
                r = self.request("turn/steer", {"threadId": self.thread_id, "expectedTurnId": expected,
                                                "input": inp, "clientUserMessageId": delivery_id})
                tid = (r or {}).get("turnId") or expected
                self.turn_deliveries[tid] = delivery_id
                return {"state": "accepted", "turn_id": tid, "how": "turn/steer"}
            except RpcError:
                # THE RACE: the turn finished before the correction arrived.  Only then is
                # a new turn right; a steer refused while that turn still runs is an error.
                time.sleep(0.3)
                if self.active_turn == expected:
                    raise
                how = "turn/start (the turn to steer had finished)"
        if self.tracer is not None:
            self.tracer[1].delivered(self.tracer[0], delivery_id, text, how="turn/start",
                                     steer=False)
        r = self.request("turn/start", {"threadId": self.thread_id, "input": inp,
                                        "clientUserMessageId": delivery_id, **self.turn_overrides})
        turn = (r or {}).get("turn") or {}
        tid = turn.get("id")
        if tid:
            self.active_turn = tid if turn.get("status") == "inProgress" else self.active_turn
            self.turn_deliveries[tid] = delivery_id
        return {"state": "accepted", "turn_id": tid, "how": how}

    def reconcile(self, delivery_id: str) -> bool | None:
        """Did an earlier, uncertain attempt reach the thread?  True/False, None if unknown."""
        try:
            r = self.request("thread/turns/list", {"threadId": self.thread_id, "limit": 30, "itemsView": "full"})
        except RpcError:
            return None
        blob = json.dumps(r or {})
        return delivery_id in blob

    def interrupt(self) -> dict:
        if not self.connected:
            return {"supported": True, "interrupted": False, "message": "the Codex bridge is disconnected"}
        if not self.active_turn:
            return {"supported": True, "interrupted": False, "message": "no turn is running"}
        try:
            self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.active_turn}, timeout=20)
            return {"supported": True, "interrupted": True, "turn_id": self.active_turn}
        except RpcError as exc:
            return {"supported": True, "interrupted": False, "message": str(exc)}

    def status(self) -> dict:
        return {"transport": "websocket", "url": self.url, "connected": self.connected,
                "thread_id": self.thread_id, "thread_status": self.thread_status,
                "active_turn": self.active_turn, "last_error": self.last_error,
                "capabilities": CAPABILITIES}


def start_app_server(codex: str, port: int, cwd: Path, log: Path) -> subprocess.Popen:
    """A loopback app server the user's terminal can attach to with `codex --remote`."""
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": open(log, "ab"), "stderr": subprocess.STDOUT,
                "cwd": str(cwd)}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000
    return subprocess.Popen([codex, "app-server", "--listen", f"ws://127.0.0.1:{port}"], **kw)


def connect_codex(state, body: dict) -> dict:
    """Bind a Codex thread to this workspace as an agent session (called for the user).

    body: `url` (attach to a running app server) or none (start one on loopback);
          `thread_id` (attach to that existing conversation) or `start_thread: true`
          (a NEW conversation, disclosed as such); optional `label`.
    """
    from ..app import WorkspaceError, new_id
    from ..installers import mcp_server_config
    from ..runtime import free_port
    ws = state.ws
    url = body.get("url")
    # the session id is fixed BEFORE the thread exists, so the MCP server Codex starts for the
    # thread can attribute its tool calls to this session from its first call
    sid = body.get("session_id") or new_id("s")
    proc = None
    if url:
        if not str(url).startswith("ws://127.0.0.1:") and not str(url).startswith("ws://localhost:"):
            raise WorkspaceError("bad_request", "only a loopback app server may be attached", status=422)
    else:
        codex = find_codex()
        if not codex:
            raise WorkspaceError("codex_missing", "no codex executable found (set QCCD_CODEX)", status=424)
        port = free_port()
        proc = start_app_server(codex, port, ws.root, ws.root / ".qccd" / "codex-app-server.log")
        url = f"ws://127.0.0.1:{port}"
    holder: dict = {}

    def on_event(method, params):
        sid = holder.get("sid")
        if sid:
            _on_codex_event(state, sid, method, params)

    br = CodexBridge(url, on_event=on_event)
    br.server_process = proc
    t0 = time.time()
    while True:
        try:
            br.open()
            break
        except Exception as exc:
            if time.time() - t0 > 20:
                br.close()
                raise WorkspaceError("codex_unreachable", f"could not reach the app server at {url}: {exc}",
                                     status=502) from None
            time.sleep(0.5)
    thread_id = body.get("thread_id")
    start = bool(body.get("start_thread")) and not thread_id
    if not thread_id and not start:
        br.close()
        raise WorkspaceError("bad_request", "name thread_id (an existing conversation) or set start_thread "
                             "(a new one)", status=422)
    cfg = dict(body.get("config") or {})
    if body.get("attach_tools", True):
        servers = dict(cfg.get("mcp_servers") or {})
        entry = mcp_server_config(ws.root, "codex", session_id=sid)
        # Codex asks before every MCP tool call unless the server says otherwise; with an
        # approval policy of "never" an unapproved call is simply DECLINED (measured on
        # 0.154).  The QCCD tools are authorised by the workspace service itself (scoped
        # token, protection, no publication), so the default pre-approves them; pass
        # tool_approval="prompt" to have the user approve each call in Codex instead.
        entry["default_tools_approval_mode"] = body.get("tool_approval", "approve")
        servers["qccd"] = entry
        cfg["mcp_servers"] = servers
    ov = body.get("turn_overrides") or {}
    br.turn_overrides = {k: v for k, v in ov.items() if k in ("effort", "model", "summary", "serviceTier")}
    try:
        bound = br.bind(thread_id=thread_id, cwd=str(ws.root), start=start,
                        developer_instructions=body.get("developer_instructions"),
                        config=cfg or None, sandbox=body.get("sandbox"),
                        approval_policy=body.get("approval_policy"))
    except RpcError as exc:
        br.close()
        raise WorkspaceError("codex_bind_failed", f"could not bind the thread: {exc}", status=502) from None
    caps = dict(CAPABILITIES)
    caps["new_thread"] = bool(bound["started"])
    caps["settings"] = {"model": br.turn_overrides.get("model") or "", "effort": br.turn_overrides.get("effort") or ""}
    # enough to re-attach after a service restart: the same thread, the same tools
    caps["reattach"] = {"url": url, "thread_id": bound["thread_id"], "owned": proc is not None,
                        "app_server_pid": proc.pid if proc is not None else None,
                        "config": cfg or None, "sandbox": body.get("sandbox"),
                        "approval_policy": body.get("approval_policy"), "turn_overrides": br.turn_overrides}
    s = ws.register_session({"kind": "human", "id": "cli"}, client="codex", mode="appserver",
                            label=body.get("label") or ("Codex (new thread)" if bound["started"] else "Codex"),
                            runtime_ref=bound["thread_id"], capabilities=caps,
                            session_id=sid)
    holder["sid"] = s["id"]
    if getattr(state, "traces", None) is not None:
        br.tracer = (s["id"], state.traces)
    old = state.bridges.pop(s["id"], None)
    if old:
        old.close()
    state.bridges[s["id"]] = br
    return {"session": s, "url": url, "thread_id": bound["thread_id"], "new_thread": bound["started"],
            "attach": f"codex --remote {url}   (then open thread {bound['thread_id']})",
            "note": ("a NEW Codex conversation was started for this workspace" if bound["started"] else
                     "prompts from Studio start turns in the existing conversation " + str(bound["thread_id"]))}


def reattach_codex_sessions(state) -> list:
    """After a service (re)start: bind every Codex session that was connected before
    to its SAME thread again -- through its app server if that still runs, otherwise
    through a new one (threads persist in the user's Codex home, so `thread/resume` works
    on a fresh server).  Deliveries left `uncertain` by the restart are then reconciled
    by the delivery worker before anything is resent."""
    from ..runtime import free_port
    ws = state.ws
    out = []
    for s in ws.sessions():
        if s["client"] != "codex" or s["mode"] != "appserver" or s["id"] in state.bridges:
            continue
        ra = (s.get("capabilities") or {}).get("reattach")
        if not ra or not ra.get("thread_id"):
            continue
        holder = {"sid": s["id"]}

        def on_event(method, params, _h=holder):
            _on_codex_event(state, _h["sid"], method, params)
        url, proc = ra.get("url"), None
        br = CodexBridge(url, on_event=on_event)
        try:
            br.open(timeout=5)
        except Exception:
            codex = find_codex()
            if not codex or not ra.get("owned"):
                out.append({"session_id": s["id"], "reattached": False,
                            "why": "its app server is gone and it was not started by this workspace"})
                continue
            port = free_port()
            proc = start_app_server(codex, port, ws.root, ws.root / ".qccd" / "codex-app-server.log")
            url = f"ws://127.0.0.1:{port}"
            br = CodexBridge(url, on_event=on_event)
            t0 = time.time()
            while True:
                try:
                    br.open()
                    break
                except Exception:
                    if time.time() - t0 > 20:
                        out.append({"session_id": s["id"], "reattached": False, "why": "no app server"})
                        br = None
                        break
                    time.sleep(0.5)
            if br is None:
                continue
        br.server_process = proc
        if proc is None and ra.get("owned"):
            br.owned_pid = ra.get("app_server_pid")
        br.turn_overrides = dict(ra.get("turn_overrides") or {})
        if getattr(state, "traces", None) is not None:
            br.tracer = (s["id"], state.traces)
        try:
            br.bind(thread_id=ra["thread_id"], cwd=str(ws.root), start=False, config=ra.get("config"),
                    sandbox=ra.get("sandbox"), approval_policy=ra.get("approval_policy"))
        except RpcError as exc:
            br.close()
            out.append({"session_id": s["id"], "reattached": False, "why": str(exc)})
            continue
        caps = dict(s.get("capabilities") or {})
        caps["reattach"] = dict(ra, url=url, owned=bool(ra.get("owned")),
                                app_server_pid=proc.pid if proc is not None else ra.get("app_server_pid"))
        state.bridges[s["id"]] = br
        ws.session_status(s["id"], "connected", detail=f"re-attached to thread {ra['thread_id']} after a restart",
                          capabilities=caps)
        out.append({"session_id": s["id"], "reattached": True, "url": url})
    if state.worker:
        state.worker.kick()
    return out


def _is_codex_process(pid: int) -> bool:
    """Is `pid` running a Codex executable right now?  False when it cannot be told."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                buf = ctypes.create_unicode_buffer(32768)
                n = wintypes.DWORD(len(buf))
                if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    return False
                image = buf.value
            finally:
                k32.CloseHandle(h)
        else:
            image = os.readlink(f"/proc/{pid}/exe")
    except (OSError, AttributeError, ValueError):
        return False
    return Path(image).name.lower() in ("codex", "codex.exe")


def _on_codex_event(state, sid: str, method: str, params: dict) -> None:
    ws = state.ws
    try:
        if method == "turn/started":
            ws.session_status(sid, "connected", detail=f"turn {(params.get('turn') or {}).get('id')} running")
        elif method == "turn/completed":
            t = params.get("turn") or {}
            br = state.bridges.get(sid)
            did = br.turn_deliveries.get(t.get("id")) if br else None
            if not did and t.get("id"):
                # a bridge re-attached after a restart has an empty in-memory map; the
                # delivery row recorded the turn id when turn/start accepted it
                row = ws.store.one("SELECT id FROM deliveries WHERE session_id=? AND external_ref=?",
                                   (sid, str(t["id"])))
                did = row["id"] if row else None
            err = t.get("error")
            if t.get("status") not in (None, "completed"):
                # say WHY in the log: a failed turn is otherwise just "failed"
                log.warning("Codex turn %s %s: %s", t.get("id"), t.get("status"), json.dumps(err)[:2000])
            ws.emit("agent.turn", {"session_id": sid, "turn_id": t.get("id"), "status": t.get("status"),
                                   "delivery_id": did, "error": err if isinstance(err, (dict, str)) else None})
            if did:
                d = ws.store.one("SELECT prompt_id FROM deliveries WHERE id=?", (did,))
                if d:
                    w = ws.store.one("SELECT state FROM work WHERE prompt_id=?", (d["prompt_id"],))
                    if w and w["state"] in ("pending", "working"):
                        why = (err.get("message") if isinstance(err, dict) else err) or ""
                        ws.set_work_state(d["prompt_id"], "ready_for_review" if t.get("status") == "completed"
                                          else "waiting_input", f"Codex turn {t.get('status')}"
                                          + (f": {why}" if why and t.get("status") != "completed" else ""))
            if state.worker:
                state.worker.kick()
        elif method == "agent/message":
            text = str(params.get("text", ""))[:4000]
            ws.emit("agent.message", {"session_id": sid, "turn_id": params.get("turnId"), "text": text})
            _store_message(state, sid, params.get("turnId"), text)
        elif method == "bridge/closed":
            ws.session_status(sid, "disconnected", detail=str(params.get("error") or "connection closed"))
        elif method == "codex/server_request":
            ws.emit("agent.approval_requested", {"session_id": sid, **params})
    except Exception:
        log.exception("handling Codex event %s failed", method)


def _store_message(state, sid: str, turn_id, text: str) -> None:
    """Keep what Codex said in a Studio-started turn as part of that request's conversation."""
    ws = state.ws
    if not text.strip() or not turn_id:
        return
    br = state.bridges.get(sid)
    did = br.turn_deliveries.get(turn_id) if br else None
    row = (ws.store.one("SELECT prompt_id FROM deliveries WHERE id=?", (did,)) if did else
           ws.store.one("SELECT prompt_id FROM deliveries WHERE session_id=? AND external_ref=? "
                        "ORDER BY created_at LIMIT 1", (sid, str(turn_id))))
    if not row:
        return                                   # a turn the person started in Codex itself
    s = ws.session(sid)
    actor = {"kind": "agent", "id": f"agent:{sid}", "session": sid, "label": s.get("label") or s["client"]}
    try:
        ws.reply(actor, row["prompt_id"], text, via="session")
    except Exception:
        log.exception("keeping a Codex message in the conversation failed")


def _brief(p: Any) -> Any:
    s = json.dumps(p)[:600] if p is not None else ""
    try:
        return json.loads(s)
    except ValueError:
        return s
