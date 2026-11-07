"""The debugger as the Debug page calls it: JSON text in, JSON text out.

The page runs this module in the browser (Pyodide, in a worker) on the device and programme
its Studio exports, so the verdicts it draws are the grader's own.  Every function returns
`{"ok": true, ...}` or `{"ok": false, "error": "..."}` -- an exception never crosses into the
page, because a page that shows a Python traceback has told the reader nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .session import DebugError, Session, open_documents

__all__ = ["open_session", "call", "rulesets", "render_page"]

_SESSIONS: dict[str, Session] = {}
_ORDER: list[str] = []
_KEEP = 3


def _answer(fn: Callable[[], Any]) -> str:
    try:
        out = fn()
        if isinstance(out, dict):
            out = dict(out)
            if "ok" in out:                       # a check's own verdict: every rule that applies holds
                out["all_hold"] = out.pop("ok")
            out = {"ok": True, **out}
        else:
            out = {"ok": True, "result": out}
    except DebugError as exc:
        out = {"ok": False, "error": str(exc)}
    except Exception as exc:                          # the reader sees the reason, never a traceback
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(out, default=str)


def open_session(arch: str, program: str, model: str = "null", name: str = "") -> str:
    """Replay a programme once and keep it; returns its key and summary."""
    def go():
        m = json.loads(model) if model else None
        key = hashlib.sha256("\x00".join([arch, program, json.dumps(m, sort_keys=True)]).encode()).hexdigest()[:16]
        s = _SESSIONS.get(key)
        if s is None:
            s = open_documents(arch, program, m, name=name or None)
            _SESSIONS[key] = s
            _ORDER.append(key)
            while len(_ORDER) > _KEEP:
                _SESSIONS.pop(_ORDER.pop(0), None)
        return {"key": key, "summary": s.summary(),
                "instants": [{"step": p + 1, "phase": ph, "label": s.labels[i]}
                             for i, (p, ph) in enumerate(s.instant_at)]}
    return _answer(go)


_VERBS = ("check", "state", "step", "trace", "find", "value", "diff", "summary", "rules_at", "inspect", "closest")


def call(key: str, verb: str, args: str = "{}") -> str:
    """One verb of a kept session: check, state, step, trace, find, value, diff, summary, and a
    rule's work at one step -- rules_at (every rule), inspect (one rule, case by case), closest
    (the case of a rule that holds with the least room)."""
    def go():
        s = _SESSIONS.get(key)
        if s is None:
            raise DebugError("that programme is no longer open; open it again")
        if verb not in _VERBS:
            raise DebugError(f"no verb {verb!r}: {', '.join(_VERBS)}")
        a = json.loads(args or "{}")
        if verb == "value" and isinstance(a.get("exprs"), list):
            return {"values": [s.value(x, a.get("step", 0), phase=a.get("phase", "after")) for x in a["exprs"]]}
        return getattr(s, verb)(**a)
    return _answer(go)


def rulesets() -> str:
    """Every rule set the website ships, written out whole."""
    def go():
        from ..ruleset import load, names
        out = []
        for n in names():
            rs = load(n)
            out.append({"name": n, "title": rs.title, "about": rs.about, "rules": rs.ids, "text": rs.text()})
        return {"rulesets": out}
    return _answer(go)


def render_page(arch: str, program: str, model: str = "null") -> str:
    """A Studio page for an uploaded device and programme, as the site would draw it."""
    def go():
        import tempfile
        from pathlib import Path
        from ..arch.device import Architecture
        from ..ir.tsir import TSIR
        from ..verify.replay import replay
        from ..viz.render import render_html
        from .session import model_from
        A = Architecture.from_json(json.loads(arch))
        P = TSIR.from_json(json.loads(program))
        M = model_from(json.loads(model) if model else None)
        res = replay(P, A, M, check_rules=True, keep_cycles=True)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "page.html"
            render_html(A, P, res, M, out)
            return {"html": out.read_text(encoding="utf-8")}
    return _answer(go)
