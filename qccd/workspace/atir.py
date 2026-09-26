"""ATIR: what an agent did for a request, read as a program.

A request an agent served is compiled from its trace (`.qccd/traces/<session>.jsonl`) into a
sequence of INSTRUCTIONS, the way a compiled QCCD programme is a sequence of hardware
instructions: each says what ran, on which machine, with which operands, what it cost and
what it changed.  The same listing-beside-the-picture view the leaderboard pages give a
programme, `/trace` gives an agent's run.

Machines
    M   the model (one call reads the whole context and writes blocks: thinking, text, calls)
    T   the workspace's tools (the QCCD MCP server)
    P   the person's page (a page action: the agent's visible hands)
    J   jobs (compile, run, grade: asynchronous, started by a call, polled by later calls)

Instructions (the listing form; `#i  t  ms  memory  op  operands  -> result  {effects}`)
    recv   "<the person's words>"             the request arrives (with the harness around it)
    boot   <model>, <n> tools                 the agent process starts
    think  -> say*, call*                     one model call (M); its cost is its latency and its
                                              tokens; the context it read is the agent's MEMORY
    say    "<text>"                           text the person reads (written by the think before it)
    call   <tool>(<args>) -> ok|error <size>  a tool call (T, or P for a page action, or the agent
                                              runtime's own tools); cost: time, and the tokens its
                                              result adds to memory
    end    ok|error, <wall>, <model calls>, <cost>

Effects (what a call changed, recorded so a run can be audited and replayed)
    commit <design> r<N> (change set id)   design <name> created   job <kind> <id> started
    poll <job> <status>   present <what>   page <action> ok|refused (the page's own time)

Semantics.  The state is <memory, designs, page, jobs>.  `think` reads memory and appends its
own output; `call` appends its result to memory and applies its effects to designs, page and
jobs; `say` changes nothing but what the person has read.  Time is spent by exactly one of
the four machines at every moment of a turn except where calls run in parallel.

What is exact and what is estimated.  Times are wall-clock from the trace (a model call's
latency runs from the moment it could start, the previous result, to its last block).  Tokens
and memory are exact when the agent runtime reports each model call's usage (Claude Code does;
recorded from 2026-09-26); otherwise the only exact tokens are the turn's totals, and a
result's contribution to memory is estimated as characters / 4, marked `~`.

`check()` verifies a program: well-formed (every call answered once, time never runs
backwards, the parts add up) and, given the workspace, faithful (every design commit the trace
claims is in the workspace's own change-set log with that revision, and replays).
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["compile_trace", "check", "listing", "ATIR_VERSION"]

ATIR_VERSION = 1
_PREFIX = "mcp__qccd__"
_PREVIEW = 400


def _parse(v):
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "{[":
            try:
                return json.loads(s)
            except ValueError:
                return v
    return v


def _find(v, pred, depth=0):
    """Every dict inside v (depth-first) that satisfies pred."""
    out = []
    if depth > 8:
        return out
    if isinstance(v, dict):
        if pred(v):
            out.append(v)
        for x in v.values():
            out.extend(_find(x, pred, depth + 1))
    elif isinstance(v, list):
        for x in v:
            out.extend(_find(x, pred, depth + 1))
    return out


def _chars(v) -> int:
    if v is None:
        return 0
    return len(v) if isinstance(v, str) else len(json.dumps(v, ensure_ascii=False))


def _short(tool: str) -> str:
    return tool[len(_PREFIX):] if tool.startswith(_PREFIX) else tool


def _preview(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= _PREVIEW else s[:_PREVIEW] + " …"


def _effects(tool: str, args: dict, result) -> list:
    """What a call changed, read from its (structured) result."""
    out = []
    res = _parse(result)
    for cs in _find(res, lambda d: "change_set_id" in d and "revision" in d):
        if (cs.get("status") or "committed") == "committed" and cs.get("mode", "apply") != "preview":
            # the design is what the result names; never guessed (a page action's result named none
            # before 2026-09-26, and the checker reads it from the workspace's log by change set)
            out.append({"kind": "commit", "change_set": cs.get("change_set_id"), "revision": cs.get("revision"),
                        "design": cs.get("branch") or cs.get("design") or (args or {}).get("branch")})
    if tool == "qccd_manage_branch" and isinstance(res, dict) and res.get("name") and (args or {}).get("action") == "create":
        out.append({"kind": "design", "design": res.get("name"), "title": res.get("title") or (args or {}).get("title")})
    if tool == "qccd_get_job" and isinstance(res, dict) and res.get("id"):
        out.append({"kind": "poll", "job": res.get("id"), "status": res.get("status"),
                    "stage": (res.get("progress") or {}).get("stage")})
    elif tool in ("qccd_start_job", "qccd_run_program", "qccd_submit_local") and isinstance(res, dict):
        jid = res.get("job_id") or res.get("run_id") or (res.get("run") or {}).get("run_id")
        if jid:
            out.append({"kind": "job", "job": jid, "tool": tool, "status": res.get("status")})
    if tool == "qccd_present" and isinstance(res, dict):
        out.append({"kind": "present", "action": (args or {}).get("action"), "status": res.get("status")})
    # a commit can come back twice (a preview then the apply share nothing; one call's result may
    # mention the same change set in two places): keep each once
    seen, uniq = set(), []
    for e in out:
        k = json.dumps(e, sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def compile_trace(steps: list, *, prompt: str | None = None) -> dict:
    """The raw steps of one request (Traces.steps) as an ATIR program."""
    steps = sorted([s for s in steps if prompt is None or s.get("prompt") == prompt], key=lambda s: s.get("n", 0))
    if not steps:
        return {"atir": ATIR_VERSION, "instructions": [], "summary": {"empty": True}}
    t0 = steps[0]["at"]
    rel = lambda t: round(t - t0, 3)
    ins: list = []
    add = lambda d: ins.append({"i": len(ins), **d}) or ins[-1]

    mcp = [s for s in steps if s["source"] == "mcp" and s["kind"] == "tool_call"]
    pages = [s for s in steps if s["source"] == "page" and s["kind"] == "page_action"]
    used_mcp: set = set()
    used_page: set = set()
    results = {(s.get("data") or {}).get("id"): s for s in steps if s["source"] == "agent" and s["kind"] == "tool_result"}
    usage_by_msg: dict = {}
    for s in steps:
        if s["source"] == "agent" and s["kind"] == "model":
            usage_by_msg[s.get("name")] = s          # the last event of a call has its final output count

    head = {"session": steps[0].get("session"), "prompt": prompt or steps[0].get("prompt"), "t0": t0,
            "client": None, "model": None, "request": None}
    ready = t0                                   # when the model could start its next call
    think = None
    msg_seen: set = set()
    codex_open: dict = {}

    def open_think(at, msg_id=None):
        nonlocal think
        think = add({"op": "think", "machine": "M", "t": rel(ready), "ms": 0, "blocks": {"thinking": 0, "say": 0, "call": 0},
                     "messages": [], "_first": at})
        return think

    def close_think(at):
        # a model call ends with its last block (thinking, text or call), not when a result returns
        nonlocal think
        if think is not None:
            first = think.pop("_first")
            end = think.pop("_last", None) or first or at
            think["ms"] = round((end - (t0 + think["t"])) * 1000)
            think = None

    for s in steps:
        d = s.get("data") or {}
        k, src = s["kind"], s["source"]
        if k == "delivery":
            head["request"] = d.get("request")
            add({"op": "recv", "machine": "-", "t": rel(s["at"]), "ms": 0, "request": d.get("request"),
                 "harness_chars": _chars(d.get("text")), "how": d.get("how")})
            ready = s["at"]
        elif k == "session":
            head["client"] = s.get("name")
            head["model"] = d.get("model")
            tools = d.get("tools") or []
            add({"op": "boot", "machine": "-", "t": rel(ready), "ms": round((s["at"] - ready) * 1000),
                 "model": d.get("model"), "tools": len(tools),
                 "qccd_tools": sum(1 for x in tools if str(x).startswith(_PREFIX)),
                 "builtin_tools": [x for x in tools if not str(x).startswith("mcp__")],
                 "mcp": [(m.get("name"), m.get("status")) for m in d.get("mcp_servers") or []]})
            ready = s["at"]
        elif src == "agent" and k == "model":
            mid = s.get("name")
            if think is None:
                open_think(s["at"])
            if mid not in msg_seen:
                msg_seen.add(mid)
                think["messages"].append(mid)
        elif src == "agent" and k in ("thinking", "message", "tool_call", "skill"):
            if think is None:
                open_think(s["at"])
            think["_last"] = s["at"]
            if k == "thinking":
                think["blocks"]["thinking"] += 1
            elif k == "message":
                think["blocks"]["say"] += 1
                add({"op": "say", "machine": "-", "t": rel(s["at"]), "ms": 0, "text": d.get("text"), "by": think["i"]})
            else:
                think["blocks"]["call"] += 1
                name = s.get("name") or ""
                tool = _short(name)
                kind = "qccd" if name.startswith(_PREFIX) else ("skill" if k == "skill" else
                                                                 ("mcp" if name.startswith("mcp__") else "builtin"))
                r = results.get(d.get("id"))
                c = add({"op": "call", "machine": "P" if tool == "qccd_page_act" else ("T" if kind in ("qccd", "mcp") else "-"),
                         "t": rel(s["at"]), "ms": round((r["at"] - s["at"]) * 1000) if r else None,
                         "tool": tool, "kind": kind, "args": d.get("input"), "by": think["i"],
                         "ok": (not (r.get("data") or {}).get("is_error")) if r else None, "_id": d.get("id")})
                content = (r.get("data") or {}).get("content") if r else None
                c["result_chars"] = _chars(content)
                c["result_tokens"] = {"est": (c["result_chars"] + 3) // 4}
                c["result"] = _preview(content) if content is not None else None
                structured = content
                if kind == "qccd":
                    m = next((x for x in mcp if id(x) not in used_mcp and x.get("name") == tool
                              and x["at"] >= s["at"] - 0.05 and (r is None or x["at"] <= r["at"] + 0.05)), None)
                    if m is not None:
                        used_mcp.add(id(m))
                        c["exec_ms"] = m.get("ms")
                        md = m.get("data") or {}
                        structured = md.get("result") if md.get("result") is not None else {"error": md.get("error")}
                if tool == "qccd_page_act":
                    p = next((x for x in pages if id(x) not in used_page and x["at"] >= s["at"] - 0.05
                              and (r is None or x["at"] <= r["at"] + 0.05)), None)
                    if p is not None:
                        used_page.add(id(p))
                        pd = p.get("data") or {}
                        c["page"] = {"action": p.get("name"), "ms": p.get("ms"), "ok": pd.get("ok"),
                                     "verb": (pd.get("args") or {}).get("verb")}
                c["effects"] = _effects(tool, d.get("input") or {}, structured)
        elif src == "agent" and k == "tool_result":
            # the model's next call can start when the last outstanding result is back
            close_think(s["at"])
            ready = max(ready, s["at"])
        elif src == "agent" and k == "item":
            # Codex: items (not checked against a real Codex trace yet: its usage limit held until 2026-09-28)
            name = s.get("name")
            if d.get("phase") == "started":
                codex_open[d.get("id")] = s
                continue
            st = codex_open.pop(d.get("id"), s)
            if name == "agentMessage":
                add({"op": "say", "machine": "-", "t": rel(s["at"]), "ms": 0, "text": d.get("text")})
            elif name == "reasoning":
                add({"op": "think", "machine": "M", "t": rel(st["at"]), "ms": round((s["at"] - st["at"]) * 1000),
                     "blocks": {"thinking": 1, "say": 0, "call": 0}, "messages": []})
            elif name in ("mcpToolCall", "commandExecution"):
                tool = str(d.get("tool") or d.get("command") or name)
                res = d.get("result") if d.get("error") is None else {"error": d.get("error")}
                add({"op": "call", "machine": "T", "t": rel(st["at"]), "ms": round((s["at"] - st["at"]) * 1000),
                     "tool": tool, "kind": "qccd" if d.get("server") == "qccd" else "builtin", "args": d.get("arguments"),
                     "ok": d.get("error") is None, "result_chars": _chars(res), "result_tokens": {"est": (_chars(res) + 3) // 4},
                     "result": _preview(res), "effects": _effects(tool, d.get("arguments") or {}, res)})
        elif k == "turn":
            close_think(s["at"])
            add({"op": "end", "machine": "-", "t": rel(s["at"]), "ms": 0, "status": s.get("name"),
                 "error": d.get("is_error") or d.get("error"), "duration_ms": d.get("duration_ms"),
                 "api_ms": d.get("duration_api_ms"), "model_calls": d.get("num_turns"),
                 "cost_usd": d.get("total_cost_usd"), "usage": d.get("usage")})
        elif k == "error":
            add({"op": "error", "machine": "-", "t": rel(s["at"]), "ms": 0, "error": d})
    close_think(steps[-1]["at"])

    # tokens and memory per model call, when the runtime reported them
    for x in ins:
        if x["op"] != "think" or not x.get("messages"):
            continue
        tok = {"in": 0, "cache_read": 0, "cache_write": 0, "out": 0}
        for mid in x["messages"]:
            u = ((usage_by_msg.get(mid) or {}).get("data") or {}).get("usage") or {}
            tok["in"] += int(u.get("input_tokens") or 0)
            tok["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
            tok["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
            # Claude Code reports a call's usage as it STARTS: the context it read is exact, its output
            # count is final only once the call has stopped (stop_reason set) -- otherwise unknown
            final = ((usage_by_msg.get(mid) or {}).get("data") or {}).get("stop_reason")
            tok["out"] = None if tok["out"] is None or not final else tok["out"] + int(u.get("output_tokens") or 0)
        x["tokens"] = tok
        x["memory"] = tok["in"] + tok["cache_read"] + tok["cache_write"]
    for x in ins:
        x.pop("_id", None)
        x.pop("_last", None)
    return {"atir": ATIR_VERSION, **head, "instructions": ins, "summary": _summary(ins)}


def _summary(ins: list) -> dict:
    end = next((x for x in reversed(ins) if x["op"] == "end"), None)
    wall = end["duration_ms"] if end and end.get("duration_ms") else (round(ins[-1]["t"] * 1000) if ins else 0)
    by = {"model": 0, "tools": 0, "page": 0, "boot": 0}
    calls: dict = {}
    for x in ins:
        if x["op"] == "think":
            by["model"] += x["ms"] or 0
        elif x["op"] == "boot":
            by["boot"] += x["ms"] or 0
        elif x["op"] == "call":
            by["page" if x["machine"] == "P" else "tools"] += x["ms"] or 0
            e = calls.setdefault(x["tool"], {"n": 0, "ms": 0, "exec_ms": 0, "result_chars": 0})
            e["n"] += 1
            e["ms"] += x["ms"] or 0
            e["exec_ms"] += x.get("exec_ms") or 0
            e["result_chars"] += x.get("result_chars") or 0
    thinks = [x for x in ins if x["op"] == "think"]
    mem = [x["memory"] for x in thinks if x.get("memory")]
    out = {"wall_ms": wall, "by_machine_ms": by, "other_ms": max(0, wall - sum(by.values())),
           "model_calls": len(thinks), "calls": sum(v["n"] for v in calls.values()),
           "polls": sum(1 for x in ins if x["op"] == "call" and x["tool"] == "qccd_get_job"),
           "by_tool": dict(sorted(calls.items(), key=lambda kv: -kv[1]["ms"])),
           "commits": sum(1 for x in ins if x["op"] == "call" for e in x.get("effects") or [] if e["kind"] == "commit"),
           "memory_peak": max(mem) if mem else None, "memory_first": mem[0] if mem else None,
           "tokens_out": (sum(x["tokens"]["out"] for x in thinks if x.get("tokens")) if mem and all(
               (x.get("tokens") or {}).get("out") is not None for x in thinks if x.get("tokens")) else None),
           "slowest": [{"i": x["i"], "op": x["op"], "ms": x["ms"], "what": x.get("tool") or x["op"]}
                       for x in sorted(ins, key=lambda x: -(x["ms"] or 0))[:6]]}
    if end:
        out.update({"cost_usd": end.get("cost_usd"), "reported_model_calls": end.get("model_calls"),
                    "api_ms": end.get("api_ms"), "status": end.get("status")})
        u = end.get("usage") or {}
        if u:
            out["tokens_total"] = {"in": u.get("input_tokens"), "cache_read": u.get("cache_read_input_tokens"),
                                   "cache_write": u.get("cache_creation_input_tokens"), "out": u.get("output_tokens")}
    return out


def check(program: dict, ws=None) -> list:
    """Findings, empty when the program is well-formed and (given ws) faithful to the workspace."""
    f = []
    ins = program.get("instructions") or []
    for n, x in enumerate(ins):
        if x.get("i") != n:
            f.append({"i": n, "rule": "index", "message": "instructions are not numbered 0..n-1"})
    last = -1.0
    for x in ins:
        if x["t"] + 1e-6 < last and x["op"] not in ("say", "call"):
            f.append({"i": x["i"], "rule": "time", "message": f"starts at {x['t']} s, before the previous one ({last} s)"})
        last = max(last, x["t"])
        if x["op"] == "call":
            if x.get("ok") is None:
                f.append({"i": x["i"], "rule": "answered", "message": f"{x['tool']} has no result in the trace"})
            if x.get("by") is not None and ins[x["by"]]["op"] != "think":
                f.append({"i": x["i"], "rule": "caller", "message": "a call not written by a think"})
    if ins and ins[0]["op"] != "recv":
        f.append({"i": 0, "rule": "recv", "message": "a program starts with the request it served"})
    if not any(x["op"] == "end" for x in ins):
        f.append({"i": len(ins), "rule": "end", "message": "the turn's end is not in the trace (still running, or cut off)"})
    s = program.get("summary") or {}
    if s.get("reported_model_calls") and s.get("model_calls") and abs(s["reported_model_calls"] - s["model_calls"]) > max(2, 0.1 * s["reported_model_calls"]):
        f.append({"i": None, "rule": "model-calls", "message": f"{s['model_calls']} thinks, but the runtime reported "
                  f"{s['reported_model_calls']} model calls"})
    if ws is not None:
        for x in ins:
            for e in x.get("effects") or []:
                if e["kind"] != "commit":
                    continue
                row = ws.store.one("SELECT * FROM change_sets WHERE id=?", (e["change_set"],))
                if row is None:
                    f.append({"i": x["i"], "rule": "commit-logged", "message": f"change set {e['change_set']} is not in the workspace's log"})
                    continue
                branch = row["branch"]
                if e.get("design"):
                    try:
                        branch = ws.resolve_draft(e["design"])
                    except Exception:
                        branch = e["design"]
                if row["branch"] != branch or row["revision"] != e["revision"]:
                    f.append({"i": x["i"], "rule": "commit-logged", "message": f"the log has {e['change_set']} as "
                              f"{row['branch']} r{row['revision']}, the trace says {branch} r{e['revision']}"})
                try:
                    if not ws.replayed(row["branch"], row["revision"]).ok:
                        f.append({"i": x["i"], "rule": "replays", "message": f"{row['branch']} r{row['revision']} does not replay"})
                except Exception as exc:
                    f.append({"i": x["i"], "rule": "replays", "message": f"{row['branch']} r{row['revision']}: {exc}"})
    return f


def _t(ms) -> str:
    if ms is None:
        return "-"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def listing(program: dict, *, width: int = 110) -> str:
    """The program as text: one line per instruction, costs in columns, effects after."""
    ins = program.get("instructions") or []
    lines = [f"ATIR v{program.get('atir')}  {program.get('client') or ''} {program.get('model') or ''}".rstrip(),
             f"request: {program.get('request') or ''}"[:width], "",
             f"{'#':>4} {'t':>7} {'cost':>7} {'memory':>8}  {'op':<6} operands"]
    for x in ins:
        mem = x.get("memory")
        memo = f"{mem / 1000:.1f}k" if mem else (f"+~{x['result_tokens']['est'] / 1000:.1f}k" if x["op"] == "call"
                                                 and x.get("result_tokens") else "")
        op = x["op"]
        if op == "recv":
            what = json.dumps(x.get("request") or "", ensure_ascii=False)
        elif op == "boot":
            what = f"{x.get('model')}, {x.get('tools')} tools ({x.get('qccd_tools')} QCCD)"
        elif op == "think":
            b = x.get("blocks") or {}
            tok = x.get("tokens") or {}
            what = "-> " + ", ".join(f"{b[k]} {k}" for k in ("thinking", "say", "call") if b.get(k)) + \
                   (f"   [{tok.get('out')} tokens out]" if tok.get("out") else "")
        elif op == "say":
            what = json.dumps(x.get("text") or "", ensure_ascii=False)
        elif op == "call":
            args = json.dumps(x.get("args") or {}, ensure_ascii=False)
            what = f"{x['tool']}({args}) -> {'ok' if x.get('ok') else 'ERROR' if x.get('ok') is False else '?'} {x.get('result_chars', 0)} chars"
            if x.get("exec_ms") is not None:
                what += f", tool {_t(x['exec_ms'])}"
            if x.get("page"):
                what += f", page {x['page'].get('action')} {_t(x['page'].get('ms'))}"
            for e in x.get("effects") or []:
                what += "  {" + " ".join(str(v) for k, v in e.items() if v is not None) + "}"
        elif op == "end":
            what = (f"{x.get('status')}  wall {_t(x.get('duration_ms'))}, model {_t(x.get('api_ms'))}, "
                    f"{x.get('model_calls')} model calls" + (f", ${x['cost_usd']:.2f}" if x.get("cost_usd") is not None else ""))
        else:
            what = json.dumps(x, ensure_ascii=False)
        line = f"{x['i']:>4} {x['t']:>6.1f}s {_t(x.get('ms')):>7} {memo:>8}  {op:<6} {what}"
        lines.append(line if len(line) <= width else line[:width - 1] + "…")
    s = program.get("summary") or {}
    if s.get("wall_ms"):
        by = s["by_machine_ms"]
        tot = s["wall_ms"]
        pc = lambda v: f"{100 * v / tot:.0f}%" if tot else "-"
        lines += ["", f"wall {_t(tot)}: model {_t(by['model'])} ({pc(by['model'])}), tools {_t(by['tools'])} "
                      f"({pc(by['tools'])}), page {_t(by['page'])} ({pc(by['page'])}), start {_t(by['boot'])}, "
                      f"other {_t(s['other_ms'])}",
                  f"{s['model_calls']} model calls, {s['calls']} tool calls ({s['polls']} job polls), {s['commits']} commits"
                  + (f", ${s['cost_usd']:.2f}" if s.get("cost_usd") is not None else "")
                  + (f"; memory {s['memory_first'] / 1000:.0f}k -> {s['memory_peak'] / 1000:.0f}k tokens"
                     if s.get("memory_peak") else "")]
    return "\n".join(lines)
