"""The debugger for agents, without a page: `qccd_debug` (POST /api/debug).

The same `qccd.debug.Session` the website's Debug page runs in the browser, on a program of
the workspace: a run's compiled programme on its design (`run_id`, from qccd_run), or an
`.arch.json` and a `.tsir.json` the workspace holds (`arch`, `program`, paths inside it).  A run
brings its certificate too, so the circuit rules (R24, R25) are judged.  One call is one
action; the replay behind it is kept for the next calls on the same program.

Steps count from 1, as the Studio's status line does, so a step this tool names is the step
the person sees on the page.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .core import WorkspaceError

__all__ = ["ACTIONS", "debug_call"]

ACTIONS = ("summary", "check", "state", "step", "trace", "find", "value", "diff", "rules_at", "inspect", "closest")
_KEEP = 4
_SESSIONS: dict[str, Any] = {}
_ORDER: list[str] = []


def _inside(ws, rel: str, what: str) -> Path:
    p = (ws.root / str(rel)).resolve()
    try:
        p.relative_to(ws.root)
    except ValueError:
        raise WorkspaceError("bad_request", f"{what} must be a file inside the workspace: {rel}", status=422) from None
    if not p.is_file():
        raise WorkspaceError("not_found", f"no {what} file {rel} in the workspace", status=404)
    return p


def _source(ws, b: dict) -> tuple[str, str, str | None, str]:
    """(arch text, programme text, certificate text or None, a name) for what the call names."""
    if b.get("run_id"):
        rid = str(b["run_id"])
        run = ws.run(rid)                        # refuses a run that is not finished
        art = run.get("artifacts") or {}
        if not art.get("device") or not art.get("program"):
            raise WorkspaceError("not_found", f"run {rid} kept no machine and programme to debug", status=404)
        cert = ws.get_artifact(art["certificate"]).decode("utf-8") if art.get("certificate") else None
        name = f"run {rid}: {(run.get('program') or {}).get('name', 'programme')} on {(run.get('design') or {}).get('name', 'the design')}"
        return (ws.get_artifact(art["device"]).decode("utf-8"), ws.get_artifact(art["program"]).decode("utf-8"),
                cert, name)
    if b.get("arch") and b.get("program"):
        a = _inside(ws, b["arch"], "architecture")
        p = _inside(ws, b["program"], "programme")
        c = _inside(ws, b["certificate"], "certificate").read_text(encoding="utf-8") if b.get("certificate") else None
        return a.read_text(encoding="utf-8"), p.read_text(encoding="utf-8"), c, p.name
    raise WorkspaceError("bad_request", "name the program: run_id (a finished run), or arch and program "
                                        "(an .arch.json and a .tsir.json in the workspace)", status=422)


def _session(ws, b: dict):
    from ..debug import DebugError, open_documents
    arch, prog, cert, name = _source(ws, b)
    model = b.get("model") or None
    key = hashlib.sha256("\x00".join([arch, prog, cert or "", json.dumps(model, sort_keys=True)]).encode()).hexdigest()
    s = _SESSIONS.get(key)
    if s is None:
        try:
            s = open_documents(arch, prog, model, name=name, certificate=cert)
        except DebugError as exc:
            raise WorkspaceError("bad_request", str(exc), status=422) from None
        _SESSIONS[key] = s
        _ORDER.append(key)
        while len(_ORDER) > _KEEP:
            _SESSIONS.pop(_ORDER.pop(0), None)
    return s


def debug_call(ws, b: dict) -> dict:
    """One action of the debugger on the program the body names."""
    from ..debug import DebugError
    act = str(b.get("action") or "summary")
    if act not in ACTIONS:
        raise WorkspaceError("bad_request", f"action must be one of {', '.join(ACTIONS)}", status=422)
    s = _session(ws, b)
    try:
        if act == "summary":
            out = s.summary()
        elif act == "check":
            out = s.check(b.get("rules", "standard") if b.get("rules") != "none" else None, b.get("assertions"),
                          only=b.get("only"), limit=int(b.get("limit") or 30))
        elif act == "state":
            out = s.state(b.get("step", 1), watch=b.get("watch") or None)
        elif act == "step":
            out = s.step(b.get("step", 1))
        elif act == "trace":
            out = s.trace(str(b.get("entity") or ""), b.get("from", 1), b.get("to"), limit=int(b.get("limit") or 200))
        elif act == "find":
            out = s.find(str(b.get("condition") or ""), b.get("from", 1), backward=bool(b.get("backward")))
        elif act == "value":
            out = {"values": [s.value(x, b.get("step", 1)) for x in (b.get("exprs") or [])]}
        elif act in ("rules_at", "inspect", "closest"):
            rules = b.get("rules", "standard") if b.get("rules") != "none" else None
            if act == "rules_at":
                out = s.rules_at(b.get("step", 1), rules, b.get("assertions"))
            elif act == "inspect":
                out = s.inspect(str(b.get("rule") or ""), b.get("step", 1), rules, b.get("assertions"),
                                limit=int(b.get("limit") or 20))
            else:
                out = s.closest(str(b.get("rule") or ""), rules, b.get("assertions"))
        else:
            out = s.diff(b.get("from", 1), b.get("to", s.n))
    except DebugError as exc:
        raise WorkspaceError("bad_request", str(exc), status=422) from None
    out = dict(out)
    if "ok" in out:                                   # a check's own verdict
        out["all_hold"] = out.pop("ok")
    out["program"] = s.name
    out["page"] = ("the website's Debug page shows the same program step by step: /web/debug/ "
                   "(open a Studio page there, or the run's own page)")
    return out
