"""What agents cost, measured from recorded runs: the numbers the website's Agentic Design page shows.

    python tools/agent_costs.py OUT.json LABEL=TRACE.jsonl [LABEL=TRACE.jsonl ...] [--excerpt LABEL]

Each TRACE is a workspace trace file (.qccd/traces/<session>.jsonl) holding one request.  It is
compiled into a program (qccd.workspace.atir) and summarised: time by machine, model calls,
tool calls, memory (the context each model call read, exact when the runtime reported it),
cost, and per tool the in-tool time and the size of what it returned.  --excerpt keeps that
run's program (to the end of its turn) for the page, with the workspace's internal ids replaced
by names and nothing that names a folder on this computer.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qccd.workspace.atir import compile_trace  # noqa: E402


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _p(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def _clean(v, names):
    """Ids a person never sees, replaced by what they call things; no local paths."""
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    for iid, title in names.items():
        s = s.replace(iid, title)
    s = re.sub(r"\bp_[0-9a-f]{12}\b", "<the request>", s)
    s = re.sub(r"\b(cs|job|sub|snap|ws|s|v|d|pr|ct)_[0-9a-f]{8,}\b", lambda m: m.group(1) + "_…", s)
    s = re.sub(r"[A-Za-z]:\\\\[^\"\s]*|[A-Za-z]:\\[^\"\s]*|/(?:home|Users|tmp)/[^\"\s]*", "<a folder>", s)
    return s if isinstance(v, str) else json.loads(s)


def summarise(label: str, path: Path) -> tuple:
    steps = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    prompt = next(s.get("prompt") for s in steps if s["kind"] == "delivery")
    prog = compile_trace(steps, prompt=prompt)
    ins = prog["instructions"]
    names = {}
    for x in ins:
        for e in x.get("effects") or []:
            if e.get("kind") == "design" and e.get("design") and e.get("title"):
                names[e["design"]] = e["title"]
    thinks = [x for x in ins if x["op"] == "think"]
    s = prog["summary"]
    boot = next((x for x in ins if x["op"] == "boot"), {})
    run = {"label": label, "date": prog.get("t0"), "request": prog.get("request"), "model": prog.get("model"),
           "wall_ms": s["wall_ms"], "by_machine_ms": s["by_machine_ms"], "model_calls": s["model_calls"],
           "calls": s["calls"], "polls": s["polls"], "commits": s["commits"], "cost_usd": s.get("cost_usd"),
           "memory_first": s.get("memory_first"), "memory_peak": s.get("memory_peak"),
           "tokens_total": s.get("tokens_total"), "start_ms": boot.get("ms"),
           "think_ms": {"median": _med([x["ms"] for x in thinks]), "p90": _p([x["ms"] for x in thinks], 0.9),
                        "max": max((x["ms"] for x in thinks), default=None)},
           "slowest": s.get("slowest")}
    # the design's own time: up to the submission (after it, the agent only waits for the grade)
    sub = next((x for x in ins if x["op"] == "call" and x["tool"] == "qccd_submit_local"), None)
    if sub is not None:
        before = [x for x in ins if x["i"] < sub["i"]]
        run["to_submission"] = {"s": sub["t"], "model_calls": sum(1 for x in before if x["op"] == "think"),
                                "model_ms": sum(x["ms"] or 0 for x in before if x["op"] == "think"),
                                "tool_ms": sum(x["ms"] or 0 for x in before if x["op"] == "call" and x["machine"] == "T"),
                                "page_ms": sum(x["ms"] or 0 for x in before if x["op"] == "call" and x["machine"] == "P")}
    tools: dict = {}
    for x in ins:
        if x["op"] != "call":
            continue
        t = tools.setdefault(x["tool"], {"n": 0, "exec": [], "ms": [], "chars": [], "page": []})
        t["n"] += 1
        t["exec"].append(x.get("exec_ms"))
        t["ms"].append(x.get("ms"))
        t["chars"].append(x.get("result_chars"))
        if x.get("page"):
            t["page"].append((x["page"].get("action"), x["page"].get("verb"), x["page"].get("ms")))
    return prog, run, tools, names


def main(argv: list) -> int:
    out = Path(argv[0])
    excerpt_of = None
    if "--excerpt" in argv:
        excerpt_of = argv[argv.index("--excerpt") + 1]
    pairs = [a.split("=", 1) for a in argv[1:] if "=" in a and not a.startswith("--")]
    runs, per_tool, page, excerpt = [], {}, {}, None
    for label, p in pairs:
        prog, run, tools, names = summarise(label, Path(p))
        runs.append(run)
        for k, t in tools.items():
            agg = per_tool.setdefault(k, {"n": 0, "exec": [], "ms": [], "chars": []})
            agg["n"] += t["n"]
            for f in ("exec", "ms", "chars"):
                agg[f] += t[f]
            for action, verb, ms in t["page"]:
                pg = page.setdefault(f"{action}{' ' + verb if verb else ''}", [])
                pg.append(ms)
        if label == excerpt_of:
            keep = []
            for x in prog["instructions"]:
                y = {k: x.get(k) for k in ("i", "op", "t", "ms", "machine", "memory", "tool", "args", "text", "request",
                                          "blocks", "ok", "exec_ms", "result_chars", "effects", "page", "model",
                                          "tools", "qccd_tools", "status", "duration_ms", "api_ms", "model_calls",
                                          "cost_usd") if x.get(k) is not None}
                keep.append(_clean(y, names))
            excerpt = {"label": label, "summary": _clean(prog["summary"], names), "instructions": keep,
                       "harness": _clean(next((json.loads(line)["data"].get("text") for line in
                                               Path(p).read_text(encoding="utf-8").splitlines()
                                               if '"kind": "delivery"' in line), ""), names)}
    doc = {"runs": runs,
           "tools": {k: {"n": v["n"], "exec_ms_median": _med(v["exec"]), "exec_ms_max": max((x for x in v["exec"] if x is not None), default=None),
                         "ms_median": _med(v["ms"]), "result_chars_median": _med(v["chars"]),
                         "result_tokens_est_median": (round(_med(v["chars"]) / 4) if _med(v["chars"]) is not None else None)}
                     for k, v in sorted(per_tool.items())},
           "page": {k: {"n": len(v), "ms_median": _med(v)} for k, v in sorted(page.items())},
           "excerpt": excerpt}
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8", newline="")
    print(f"wrote {out}: {len(runs)} runs, {len(doc['tools'])} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
