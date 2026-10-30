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

An outcome measured BEFORE the template (`e<i>`: an earlier merge, a record another call wrote)
is decided by the time it starts, and a gate can read only its own outcomes; so here each is
taken as decided -- a guard term on it is dropped, and in a `bit` the archive computes it is a
constant.  The time is the same either way (a guarded group reserves its places in every
branch, and the archive has the bit before the template begins); which branch fires is the
symbolic program's business, checked where it is expanded.

What a template promises the rest of the run is its PORTS (`Compiled["ports"]`), read off its
compiled schedule, relative to its start: when it first and last touches each block it is
passed (a block only Pauli frames touch has none -- the archive keeps those), when it records
each outcome, and when it first needs each outcome measured before it for something physical
(a guarded group, a `bit`; a frame waits for nothing).  The run's parallel time
(`qccd.fullstack.scale.parallel`) starts each instance as soon as its ports allow.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .program import Template

__all__ = ["template_alg", "compile_template", "Compiled"]

_PAULI = re.compile(r"[xyz] [pl]\d+$")
_EXT = re.compile(r"!?\be\d+\b")


def _decided(line: str) -> str | None:
    """`line` with its outcomes measured before the template taken as decided (`None`: the
    guard can never hold -- not written by the builder)."""
    if line.startswith("bit "):
        text, out = line[4:].split("->")
        terms = []
        for t in text.split("^"):
            lits = [x for x in t.strip().strip("()").split("&") if not _EXT.fullmatch(x.strip())]
            terms.append("(" + " & ".join(x.strip() for x in lits) + ")" if lits else "1")
        return f"bit {' ^ '.join(terms)} -> {out.strip()}"
    if not line.startswith("if "):
        return line
    cond, body = line[3:].split(": ", 1)
    if _PAULI.match(body):
        # a Pauli frame is a line in the archive: no ion, no time on the machine; its guard
        # decides only whether the archive applies it (checked where the program is expanded),
        # and may read an outcome a guarded group measured, which a gate cannot see
        return body
    keep = []
    for conj in cond.split("&"):
        names = [n.strip() for n in conj.split("^") if not _EXT.fullmatch(n.strip())]
        if names and names != ["1"]:
            keep.append(" ^ ".join(names))
    return f"if {' & '.join(keep)}: {body}" if keep else body
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

    cover: list = []                       # per top-level body line: template lines it is

    def emit(nodes, prefix) -> list:
        top = nodes is tree
        lines = []
        for n in nodes:
            if n[0] == "line":
                lines.append(n[1])
                if top:
                    cover.append(1)
                continue
            g, kids = n[1], n[2]
            flat = _flat(kids)
            if all(_PAULI.match(b) for _, b in flat):
                # a frame under the guard: written guarded, in place
                for gs, b in flat:
                    lines.append(f"if {' & '.join([g] + gs)}: {b}")
                    if top:
                        cover.append(1)
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
                read |= {n.strip().lstrip("!") for x in gs for n in x.split("^")
                         if n.strip() not in ("0", "1")}
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
            if top:
                cover.append(len(flat))
        return lines

    # (a guarded group is one gate called under its guard, as qec2alg writes it: what a
    # guarded call cannot gate -- a preparation, a decode -- runs either way inside it)
    items = [_parse(_decided(ln)) for ln in t.lines]
    tree = _tree(items)
    body = emit(tree, name)
    template_alg.cover = cover
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


def _ports(t: Template, top, macros: dict, cover: list) -> dict:
    """The template's ports, relative to its start (see the module's docstring)."""
    # when each top-level instruction of its gate starts: its own events, or its call's
    start: dict = {}
    for e in top.gir["events"]:
        start[e[6]] = min(start.get(e[6], e[1]), e[1])
    ends: dict = {}
    for c in top.calls:
        start[c["ins"]] = min(start.get(c["ins"], c["t0"]), c["t0"])
        sub = next((m for m in macros.values() if m.id == c["m"]), None)
        ends[c["ins"]] = c["t0"] + (sub.span if sub is not None else 0.0)
    at, k = [], 0
    for ins, n in enumerate(cover):
        at += [start.get(ins)] * n
        k += n
    first, last = [], []
    for i, h in enumerate(top.homes):
        ts = [e[1] for e in top.gir["events"] if e[3] == h]
        first.append(round(min(ts), 3) if ts else None)
        last.append(round(top.free[i], 3) if ts else None)
    rec = {o["cvar"].rsplit("_", 1)[-1]: o.get("recorded_us") for o in top.gir["outcomes"]}
    out = []
    for k in range(t.n_out):
        line = next((i for i, ln in enumerate(t.lines) if re.search(rf"->\s*o{k}$", ln)), None)
        got = rec.get(f"o{k}")
        if got is None and line is not None:
            # measured inside a guarded group: when its call is over
            ins = next((j for j, n in enumerate(_prefix(cover)) if n > line), None)
            got = ends.get(ins)
        out.append(round(got if got is not None else top.span, 3))
    need: dict = {}
    for i, ln in enumerate(t.lines):
        word = (ln.split(": ", 1)[1] if ln.startswith("if ") else ln).split()[0]
        if word in ("x", "y", "z") or at[i] is None:
            continue                      # a frame waits for nothing; the archive applies it
        reads = ln.split("->")[0] if word == "bit" else (ln[3:].split(": ", 1)[0] if ln.startswith("if ") else "")
        for e in re.findall(r"\be(\d+)\b", reads):
            need[int(e)] = round(min(need.get(int(e), at[i]), at[i]), 3)
    return {"first": first, "last": last, "out": out, "need": need, "span": round(top.span, 3)}


def _prefix(cover: list) -> list:
    """The number of template lines up to and including each top-level body line."""
    out, s = [], 0
    for n in cover:
        s += n
        out.append(s)
    return out


class Compiled(dict):
    """One template's hardware program: `span_us` (the call's duration), `gir` (GIR 0.3:
    the call's macro and the macros of its guarded groups), `checks`, `seconds`."""


def compile_template(t: Template, floorplan: Path, leaves, *, name: str = "tpl", check: bool = True,
                     rounds_at: str = "clinic") -> Compiled:
    from qccd.gadget.floorplan import load_file
    from qccd.gadget.fold import check_folded, parse_folded, schedule_folded
    t0 = time.time()
    src = template_alg(t, name=name, floorplan=Path(floorplan).stem)
    alg = parse_folded(src, name=t.tid)
    lib, fp = load_file(floorplan, leaves)
    keep: dict = {}
    # (a template may make a block the next template of its gadget reads: an ancilla prepared
    # before a register-wide merge splits the run; it rests in its memory when this one ends)
    g = schedule_folded(alg, lib, leaves, keep=keep, outlive=True, rounds_at=rounds_at)
    top = next(m for m in keep["folder"].macros.values() if m.gate == name)
    out = Compiled(tid=t.tid, source=src, span_us=round(top.span, 3), macro=top.id,
                   macros=g["folded"]["macros"], instructions=top.instructions,
                   own=top.own, homes=top.homes, gir=g, seconds={"schedule": round(time.time() - t0, 2)})
    out["ports"] = _ports(t, top, keep["folder"].macros, list(template_alg.cover))
    if check:
        t1 = time.time()
        ck = check_folded(g, lib, leaves)
        out["checks"] = {"passed": ck["passed"], "failed": ck["failed"], "views": ck["metrics"]["views"]}
        out["seconds"]["checks"] = round(time.time() - t1, 2)
    return out
