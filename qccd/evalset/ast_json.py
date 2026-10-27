"""An evaluation's parse tree as JSON: what a page carries so the browser can read the same text.

The browser does not parse the language: the Python parser does, once, and every page ships the
trees (`payload`).  `qccd/viz/js/evaluate.js` walks them over the engine's own replay with the
semantics of `qccd.evalset.interp`, and `tests/test_eval_parity.py` holds the two interpreters
to each other.  A node is a small list, its first item the node's kind:

    ["n", value]                 number          ["s", text]       string
    ["c", true|false|null]       constant        ["v", name]       name
    ["f", fn, [args]]            call            ["b", op, l, r]   binary operation
    ["!", arg]                   not             ["{", [items]]    set literal
    ["g", kind, expr, binders, where]           count / sum / max / min / set / all_different
    ["q", kind, binders, where, body]           for all / for some
    ["?", cond, then, else]                     if then else
    binders = [[[names], domain], ...]
"""

from __future__ import annotations

from typing import Any

from ..ruleset.syntax import BinOp, Call, Const, Gen, Name, Node, Not, Num, Quant, SetLit, Str
from .syntax import If

__all__ = ["node", "payload"]


def node(n: Node | None) -> Any:
    if n is None:
        return None
    if isinstance(n, Num):
        return ["n", n.value]
    if isinstance(n, Str):
        return ["s", n.value]
    if isinstance(n, Const):
        return ["c", n.value]
    if isinstance(n, Name):
        return ["v", n.id]
    if isinstance(n, Call):
        return ["f", n.fn, [node(a) for a in n.args]]
    if isinstance(n, BinOp):
        return ["b", n.op, node(n.left), node(n.right)]
    if isinstance(n, Not):
        return ["!", node(n.arg)]
    if isinstance(n, SetLit):
        return ["{", [node(a) for a in n.items]]
    if isinstance(n, If):
        return ["?", node(n.cond), node(n.then), node(n.other)]
    if isinstance(n, Gen):
        return ["g", n.kind, node(n.expr), [[list(b.names), node(b.domain)] for b in n.binders],
                node(n.where)]
    if isinstance(n, Quant):
        return ["q", n.kind, [[list(b.names), node(b.domain)] for b in n.binders], node(n.where),
                node(n.body)]
    raise TypeError(f"no JSON form for {n!r}")


def payload(ev) -> dict:
    """One resolved evaluation, everything an interpreter needs: its clock, params, defines,
    metrics, requirements and channels as trees, and its name and digest."""
    return {
        "name": ev.name, "title": ev.title, "clock": ev.clock, "digest": ev.digest(),
        "params": {k: node(v) for k, v in ev.params.items()},
        "defines": [{"name": d.name, "args": list(d.args), "body": node(d.body)} for d in ev.defines],
        "metrics": [{"name": m.name, "title": m.title, "unit": m.unit, "better": m.better,
                     "group": m.group, "clock": m.clock, "body": node(m.body)} for m in ev.metrics_],
        "requirements": [{"id": r.id, "title": r.title, "text": r.text, "formula": node(r.formula)}
                         for r in ev.requirements],
        "channels": [{"name": c.name, "title": c.title, "when": c.when,
                      "binders": [[list(b.names), node(b.domain)] for b in c.binders],
                      "where": node(c.where), "op": c.op, "prob": node(c.prob),
                      "targets": node(c.targets),
                      "counted": [[k, node(t)] for k, t in c.counted]} for c in ev.channels],
    }
