"""Shor at scale, layer three: every template compiled once, on a district of the machine.

A template (`program.Template`) is a straight run of `.alg` lines with its blocks as
parameters.  Here it becomes a gate, is scheduled ONCE by the town's own scheduler (the folded
scheduler, `qccd.gadget.fold`) on a district -- a machine with a memory for every block it is
passed, a Pauli-product bench, a pharmacy, a factory, a readout, a decoder and an archive --
and is checked there once by the place checks every gadget page runs.  The result is the
template's hardware program (its events, carries and TSIR), its duration, and its verdict.

A template's guarded groups that measure, prepare or decode (a T gate's S correction runs only
when the T injection read 1) are written the `.alg` way: a gate called under the guard, which
reserves its places either way and fires only when the guard holds.  So every template takes
the same time in every branch, and is compiled once for all of them.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .program import Template

__all__ = ["template_alg", "compile_template", "Compiled"]

_PAULI = re.compile(r"[xyz] [pl]\d+$")
_BLOCK = re.compile(r"\b[pl]\d+\b")
_OUT = re.compile(r"\bo\d+\b")


def _parse(line: str) -> tuple:
    if line.startswith("if "):
        cond, body = line[3:].split(": ", 1)
        return [c.strip() for c in cond.split("&")], body
    return [], line


def _tree(items: list) -> list:
    """Consecutive lines that share their first guard become one group under it."""
    out, i = [], 0
    while i < len(items):
        guards, body = items[i]
        if not guards:
            out.append(("line", body))
            i += 1
            continue
        g = guards[0]
        j = i
        inner = []
        while j < len(items) and items[j][0] and items[j][0][0] == g:
            inner.append((items[j][0][1:], items[j][1]))
            j += 1
        out.append(("guard", g, _tree(inner)))
        i = j
    return out


def _flat(nodes, guards=()) -> list:
    out = []
    for n in nodes:
        if n[0] == "line":
            out.append((list(guards), n[1]))
        else:
            out += _flat(n[2], guards + (n[1],))
    return out


def template_alg(t: Template, name: str = "tpl", floorplan: str = "fullstack") -> str:
    """The template as a `.alg` program: its gate (and the gates of its guarded groups) and
    one call of it on fresh blocks, one per parameter."""
    defs: list = []
    count = [0]

    def emit(nodes, prefix) -> list:
        lines = []
        for n in nodes:
            if n[0] == "line":
                lines.append(n[1])
                continue
            g, kids = n[1], n[2]
            flat = _flat(kids)
            if all(_PAULI.match(b) for _, b in flat):
                # a frame under the guard: written guarded, in place
                for gs, b in flat:
                    lines.append(f"if {' & '.join([g] + gs)}: {b}")
                continue
            body = emit(kids, prefix)
            made, used, named, read = set(), [], set(), set()
            for b in body:
                gs, op = _parse(b)
                w = op.split()
                if w[0] in ("prep", "inject"):
                    made.add(w[1])
                for x in _BLOCK.findall(op):
                    if x not in used:
                        used.append(x)
                m = re.search(r"->\s*(o\d+)", op)
                if m:
                    named.add(m[1])
                read |= {x.lstrip("!") for x in gs}
            params = [x for x in used if x not in made]
            foreign = read - named
            if foreign:
                raise ValueError(f"template group under {g} reads outcomes measured outside it: {sorted(foreign)}")
            count[0] += 1
            sub = f"{prefix}_w{count[0]}"
            ren = {x: f"b{i}" for i, x in enumerate(params)}
            defs.append((sub, [ren[x] for x in params],
                         [_BLOCK.sub(lambda mm: ren.get(mm[0], mm[0]), b) for b in body]))
            lines.append(f"if {g}: {sub} {' '.join(params)}" if params else f"if {g}: {sub}")
        return lines

    # (a guarded group is one gate called under its guard, as qec2alg writes it: what a
    # guarded call cannot gate -- a preparation, a decode -- runs either way inside it)
    items = [_parse(ln) for ln in t.lines]
    body = emit(_tree(items), name)
    params = [f"p{i}" for i in range(t.n_params)]
    out = [f"title template {t.tid}", f'floorplan "{floorplan}"']
    for sub, ps, lines in defs:
        out.append(f"gate {sub} {' '.join(ps)}:")
        out += ["  " + ln for ln in lines]
    out.append(f"gate {name} {' '.join(params)}:")
    out += ["  " + ln for ln in body]
    blocks = [f"q{i}" for i in range(t.n_params)]
    out += [f"prep {b} Z" for b in blocks]
    out.append(f"{name} {' '.join(blocks)}")
    return "\n".join(out) + "\n"


class Compiled(dict):
    """One template's hardware program: `span_us` (the call's duration), `gir` (GIR 0.3:
    the call's macro and the macros of its guarded groups), `checks`, `seconds`."""


def compile_template(t: Template, floorplan: Path, leaves, *, name: str = "tpl", check: bool = True) -> Compiled:
    from qccd.gadget.floorplan import load_file
    from qccd.gadget.fold import check_folded, parse_folded, schedule_folded
    t0 = time.time()
    src = template_alg(t, name=name, floorplan=Path(floorplan).stem)
    alg = parse_folded(src, name=t.tid)
    lib, fp = load_file(floorplan, leaves)
    keep: dict = {}
    g = schedule_folded(alg, lib, leaves, keep=keep)
    top = next(m for m in keep["folder"].macros.values() if m.gate == name)
    out = Compiled(tid=t.tid, source=src, span_us=round(top.span, 3), macro=top.id,
                   macros=g["folded"]["macros"], instructions=top.instructions,
                   own=top.own, homes=top.homes, gir=g, seconds={"schedule": round(time.time() - t0, 2)})
    if check:
        t1 = time.time()
        ck = check_folded(g, lib, leaves)
        out["checks"] = {"passed": ck["passed"], "failed": ck["failed"], "views": ck["metrics"]["views"]}
        out["seconds"]["checks"] = round(time.time() - t1, 2)
    return out
