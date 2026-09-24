"""The trace: everything an agent did for a request, in order, to read back and replay.

One JSON-lines file per agent session, `.qccd/traces/<session>.jsonl`.  Each line is one
step, tagged with the request (prompt) and the agent turn it belongs to:

    source  agent | mcp | page | service
    kind    delivery (the text the agent received) | session (model, tools, skills at start)
            | message | thinking | tool_call | tool_result | skill | item (a Codex item)
            | page_action | turn (how the turn ended: time, cost, error) | error

`agent` steps come from the agent's own stream (Claude's stream-json, Codex's item events),
`mcp` steps from the QCCD MCP server (every tool call, with its time and result, for agents of
any kind), `page` steps from the service (each page action and what the page answered).
Values are capped (a long result keeps its head) and keys that name credentials are blanked;
nothing here leaves the computer.  Read it with `qccd trace`, `/trace` in the browser, or
`GET /api/traces`.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

__all__ = ["Traces"]

_SECRET_KEYS = ("token", "secret", "password", "cookie", "authorization", "api_key", "apikey", "csrf")
_MAX_STR = 12000
_MAX_ITEMS = 300
_MAX_LINE = 96 * 1024


def _cap(v, depth: int = 0):
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + f"… [{len(v) - _MAX_STR} more characters]"
    if depth > 12:
        return "…"
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:_MAX_ITEMS]:
            ks = str(k)
            out[ks] = "[hidden]" if any(s in ks.lower() for s in _SECRET_KEYS) and isinstance(x, str) else _cap(x, depth + 1)
        return out
    if isinstance(v, (list, tuple)):
        out = [_cap(x, depth + 1) for x in list(v)[:_MAX_ITEMS]]
        if len(v) > _MAX_ITEMS:
            out.append(f"… [{len(v) - _MAX_ITEMS} more]")
        return out
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)[:_MAX_STR]


class Traces:
    def __init__(self, root: Path, store=None, ws=None):
        self.dir = Path(root) / ".qccd" / "traces"
        self.store = store
        self.ws = ws
        self.lock = threading.Lock()
        self.current: dict = {}          # session id -> {"turn", "prompt_id", "delivery_id"}
        self.seq = 0

    # ------------------------------------------------------------------ writing
    def delivered(self, session_id: str, delivery_id: str, text: str, *, turn: str | None = None,
                  steer: bool = False, how: str = "") -> None:
        """The agent is handed a request (the full text it receives): its steps follow.  A
        steer joins the running turn, so the turn keeps its request."""
        pid = None
        if self.store is not None:
            row = self.store.one("SELECT prompt_id FROM deliveries WHERE id=?", (delivery_id,))
            pid = row["prompt_id"] if row else None
        words = None
        if pid and self.ws is not None:
            try:
                words = ((self.ws.prompt(pid).get("latest_sent") or {}).get("body") or {}).get("text")
            except Exception:
                words = None
        if not steer:
            self.current[session_id] = {"turn": turn, "prompt_id": pid, "delivery_id": delivery_id}
        self.record(session_id, "service", "delivery", "steer" if steer else "deliver",
                    {"request": words, "text": text, "delivery_id": delivery_id, "how": how}, prompt_id=pid)

    def turn_started(self, session_id: str, turn: str) -> None:
        cur = self.current.get(session_id)
        if cur is not None and not cur.get("turn"):
            cur["turn"] = turn

    def _context(self, session_id: str) -> dict:
        cur = self.current.get(session_id)
        if cur:
            return cur
        if self.store is not None:
            # an agent outside the workspace's bridges (a terminal session through MCP): the
            # request most recently handed to it
            row = self.store.one("SELECT id, prompt_id FROM deliveries WHERE session_id=? "
                                 "ORDER BY updated_at DESC LIMIT 1", (session_id,))
            if row:
                return {"turn": None, "prompt_id": row["prompt_id"], "delivery_id": row["id"]}
        return {"turn": None, "prompt_id": None, "delivery_id": None}

    def record(self, session_id: str | None, source: str, kind: str, name: str | None = None, data=None,
               *, turn: str | None = None, prompt_id: str | None = None, ms: float | None = None) -> dict:
        sid = session_id or "_none"
        ctx = self._context(sid)
        with self.lock:
            self.seq += 1
            step = {"at": round(time.time(), 3), "n": self.seq, "session": sid,
                    "prompt": prompt_id or ctx.get("prompt_id"), "turn": turn or ctx.get("turn"),
                    "source": source, "kind": kind, "name": name, "data": _cap(data)}
            if ms is not None:
                step["ms"] = round(float(ms), 1)
            line = json.dumps(step, ensure_ascii=False)
            if len(line) > _MAX_LINE:
                step["data"] = {"truncated": json.dumps(step["data"], ensure_ascii=False)[:_MAX_LINE // 2]}
                line = json.dumps(step, ensure_ascii=False)
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                with open(self.dir / f"{_safe(sid)}.jsonl", "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass                         # a trace never stops the work it records
        return step

    # ------------------------------------------------------------------ reading
    def steps(self, session_id: str, prompt_id: str | None = None) -> list:
        p = self.dir / f"{_safe(session_id)}.jsonl"
        if not p.is_file():
            return []
        out = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    s = json.loads(line)
                except ValueError:
                    continue
                if prompt_id and s.get("prompt") != prompt_id:
                    continue
                out.append(s)
        return out

    def index(self) -> list:
        """Every traced session: its requests, in order, with how many steps each has."""
        out = []
        if not self.dir.is_dir():
            return out
        for p in self.dir.glob("*.jsonl"):
            prompts: dict = {}
            first = last = None
            n = 0
            for s in self.steps(p.stem):
                n += 1
                first = first or s["at"]
                last = s["at"]
                key = s.get("prompt") or ""
                e = prompts.setdefault(key, {"prompt": s.get("prompt"), "first": s["at"], "last": s["at"], "steps": 0,
                                             "tools": 0, "text": None})
                e["steps"] += 1
                e["last"] = s["at"]
                if s["kind"] in ("tool_call", "skill", "page_action") or (s["kind"] == "item" and s.get("name") in
                                                                           ("mcpToolCall", "commandExecution")):
                    e["tools"] += 1
                if s["kind"] == "delivery" and not e["text"]:
                    e["text"] = str((s.get("data") or {}).get("request") or (s.get("data") or {}).get("text") or "")[:200]
            out.append({"session": p.stem, "steps": n, "first": first, "last": last,
                        "prompts": sorted(prompts.values(), key=lambda e: e["first"])})
        return sorted(out, key=lambda e: -(e["last"] or 0))


def render_text(steps: list, *, full: bool = False) -> str:
    """A request's steps as a readable transcript (`qccd trace`)."""
    if not steps:
        return "(nothing recorded)"
    t0 = steps[0]["at"]
    cut = (lambda s, n: s) if full else (lambda s, n: s if len(s) <= n else s[:n] + " …")
    ids = {s["data"].get("id") for s in steps if s["kind"] != "tool_result" and isinstance(s.get("data"), dict)}
    agent_calls = any(s["source"] == "agent" and s["kind"] in ("tool_call", "item") for s in steps)
    lines = []
    for s in steps:
        d = s.get("data") or {}
        k = s["kind"]
        if s["source"] == "mcp" and agent_calls and not full:
            continue                                  # the agent's own record of the call is shown
        if k == "tool_result" and d.get("id") in ids:
            continue                                  # shown under its call
        head = f"+{s['at'] - t0:7.1f}s  {s['source']:<7} "
        ms = f"  ({s['ms'] / 1000:.1f} s)" if s.get("ms") is not None else ""
        if k == "delivery":
            lines.append(head + f"HANDED TO THE AGENT ({s.get('name')}): {cut(str(d.get('request') or ''), 400)}")
            if full:
                lines.append("          full text:\n" + str(d.get("text") or ""))
        elif k == "session":
            lines.append(head + f"agent started: model {d.get('model')}; MCP "
                         + ", ".join(f"{m.get('name')} {m.get('status')}" for m in d.get("mcp_servers") or [])
                         + f"; {len(d.get('tools') or [])} tools")
        elif k == "message":
            lines.append(head + "SAID: " + cut(str(d.get("text") or ""), 600))
        elif k == "thinking":
            th = d.get("text") or " ".join(str(x) for x in (d.get("summary") or []))
            # Claude Code's stream carries a thinking block's signature, not its text
            lines.append(head + ("thought: " + cut(str(th), 400) if th else "thought (the text is not shared)"))
        elif k in ("tool_call", "skill"):
            inp = d.get("args") if s["source"] == "mcp" else d.get("input")
            lines.append(head + ("SKILL " if k == "skill" else "CALL ") + f"{s.get('name')} "
                         + cut(json.dumps(inp, ensure_ascii=False), 300) + ms)
            if s["source"] == "mcp":
                res = {"error": d["error"]} if d.get("error") else d.get("result")
                lines.append("            -> " + cut(json.dumps(res, ensure_ascii=False), 400))
            else:
                r = next((x for x in steps if x["kind"] == "tool_result" and (x.get("data") or {}).get("id") == d.get("id")), None)
                if r:
                    rd = r["data"]
                    lines.append("            -> " + ("FAILED " if rd.get("is_error") else "")
                                 + cut(str(rd.get("content") or ""), 400))
        elif k == "item":
            if d.get("phase") == "started" and any(x is not s and x["kind"] == "item" and (x.get("data") or {}).get("id") == d.get("id")
                                                   for x in steps):
                continue
            what = s.get("name")
            if what == "mcpToolCall":
                lines.append(head + f"CALL {d.get('server')}.{d.get('tool')} " + cut(json.dumps(d.get("arguments"), ensure_ascii=False), 300))
                lines.append("            -> " + cut(json.dumps(d.get("error") or d.get("result"), ensure_ascii=False), 400))
            elif what == "commandExecution":
                lines.append(head + f"RAN {cut(str(d.get('command')), 200)} -> exit {d.get('exitCode')}")
            else:
                lines.append(head + f"{what}: " + cut(json.dumps(d, ensure_ascii=False), 300))
        elif k == "page_action":
            lines.append(head + f"PAGE {s.get('name')} " + cut(json.dumps(d.get("args"), ensure_ascii=False), 200) + ms)
            lines.append("            -> " + ("ok " + cut(json.dumps(d.get("result"), ensure_ascii=False), 300) if d.get("ok")
                                             else "refused: " + str(d.get("error") or "no answer")))
        elif k == "turn":
            bits = [str(s.get("name"))]
            if d.get("duration_ms") is not None:
                bits.append(f"{d['duration_ms'] / 1000:.1f} s")
            if d.get("num_turns") is not None:
                bits.append(f"{d['num_turns']} model turns")
            if d.get("total_cost_usd") is not None:
                bits.append(f"${d['total_cost_usd']:.3f}")
            if d.get("error"):
                bits.append("error: " + cut(json.dumps(d["error"], ensure_ascii=False), 300))
            lines.append(head + "TURN ENDED: " + ", ".join(bits))
        elif k == "error":
            lines.append(head + "ERROR " + cut(json.dumps(d, ensure_ascii=False), 600))
        else:
            lines.append(head + f"{k} {s.get('name') or ''} " + cut(json.dumps(d, ensure_ascii=False), 300))
    return "\n".join(lines)


def _safe(sid: str) -> str:
    return "".join(c for c in str(sid) if c.isalnum() or c in "_-")[:80] or "_none"
