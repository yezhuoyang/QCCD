"""Which model an agent runs on, and how hard it thinks: the chat's model picker.

Claude Code takes `--model <alias>` and `--effort <level>` per run; Codex takes `model` and
`effort` per turn (`turn/start`), and its app server lists the models this account can use
(`model/list`, each with its supported reasoning efforts).  The person's choice is kept on
the session (`capabilities.settings`, and in what a restarted service re-attaches with), so
it holds for every later message until they change it.
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("qccd.models")

__all__ = ["catalogue", "apply_settings", "CLAUDE_MODELS", "CLAUDE_EFFORTS"]

#: the aliases `claude --model` accepts (each follows the newest model of its family)
CLAUDE_MODELS = [{"id": "", "label": "Default", "note": "what Claude Code picks"},
                 {"id": "fable", "label": "Fable"},
                 {"id": "opus", "label": "Opus"},
                 {"id": "sonnet", "label": "Sonnet"},
                 {"id": "haiku", "label": "Haiku"}]
#: `claude --effort`
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

_NAME = re.compile(r"^[A-Za-z0-9._:\-\[\]]{0,80}$")
_codex_cache: dict = {}
_codex_lock = threading.Lock()


def _codex_list(state) -> dict:
    """Codex's own model list: from a connected session's app server, else a short-lived one."""
    from .codex import CodexBridge, RpcError, find_codex, start_app_server
    with _codex_lock:
        hit = _codex_cache.get("v")
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        raw = None
        for br in list(state.bridges.values()):
            if isinstance(br, CodexBridge) and br.connected:
                try:
                    raw = br.request("model/list", {}, timeout=20)
                    break
                except RpcError:
                    continue
        if raw is None:
            exe = find_codex()
            if not exe:
                return {"available": False, "models": [], "efforts": []}
            from ..runtime import free_port
            tmp = Path(tempfile.mkdtemp(prefix="qccd-codex-models-"))
            port = free_port()
            proc = start_app_server(exe, port, tmp, tmp / "app-server.log")
            br = CodexBridge(f"ws://127.0.0.1:{port}")
            br.server_process = proc
            try:
                t0 = time.time()
                while True:
                    try:
                        br.open()
                        break
                    except Exception:
                        if time.time() - t0 > 20:
                            raise
                        time.sleep(0.5)
                raw = br.request("model/list", {}, timeout=20)
            except Exception as exc:
                log.warning("listing the Codex models failed: %s", exc)
                return {"available": True, "models": [], "efforts": [], "error": str(exc)[:300]}
            finally:
                br.close()
        models = [{"id": "", "label": "Default", "note": "what Codex picks"}]
        efforts: list = []
        for m in (raw or {}).get("data") or []:
            if m.get("hidden"):
                continue
            eff = [e.get("reasoningEffort") for e in (m.get("supportedReasoningEfforts") or []) if e.get("reasoningEffort")]
            models.append({"id": m.get("id") or m.get("model"), "label": m.get("displayName") or m.get("id"),
                           "note": "Codex's default" if m.get("isDefault") else None, "efforts": eff,
                           "default_effort": m.get("defaultReasoningEffort")})
            efforts += [e for e in eff if e not in efforts]
        out = {"available": True, "models": models, "efforts": efforts}
        _codex_cache["v"] = (time.time(), out)
        return out


def catalogue(state) -> dict:
    from .claude import find_claude
    return {"claude": {"available": find_claude() is not None, "models": CLAUDE_MODELS, "efforts": CLAUDE_EFFORTS},
            "codex": _codex_list(state)}


def apply_settings(state, sid: str, body: dict) -> dict:
    """The person picked a model or a thinking level for this agent: it applies from the next message."""
    from ..app import WorkspaceError
    from .claude import ClaudeBridge
    from .codex import CodexBridge
    ws = state.ws
    s = ws.session(sid)
    model = str(body.get("model") or "")
    effort = str(body.get("effort") or "")
    if not _NAME.match(model) or not re.match(r"^[a-z]{0,12}$", effort):
        raise WorkspaceError("bad_request", "model: an alias or model id; effort: a level such as low or high",
                             status=422)
    br = state.bridges.get(sid)
    caps = dict(s.get("capabilities") or {})
    if isinstance(br, ClaudeBridge):
        if effort and effort not in CLAUDE_EFFORTS:
            raise WorkspaceError("bad_request", f"Claude's thinking levels are {', '.join(CLAUDE_EFFORTS)}", status=422)
        br.model, br.effort = model or None, effort or None
        br._remember()
        return {"session": ws.session(sid), "applies": "from the next message"}
    if isinstance(br, CodexBridge):
        ov = {k: v for k, v in br.turn_overrides.items() if k not in ("model", "effort")}
        if model:
            ov["model"] = model
        if effort:
            ov["effort"] = effort
        br.turn_overrides = ov
        caps["reattach"] = dict(caps.get("reattach") or {}, turn_overrides=ov)
        caps["settings"] = {"model": model, "effort": effort}
        s = ws.session_status(sid, s["status"], detail="model settings changed", capabilities=caps)
        return {"session": s, "applies": "from the next turn"}
    raise WorkspaceError("not_connected", f"session {sid} has no running bridge here (it was started elsewhere)",
                         status=409)
