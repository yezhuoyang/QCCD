"""Hardware instructions in QCCDCert's syntax, extended for the studio.

QCCDCert (the compiler of this site) writes target code like

    init q0@S,q1@G;
    transport q0:S->J->G[p0];
    pulse MS(pi/2) q0,q1 @G via c4;
    broadcast R(-pi/2,0) q0@G, R(-pi/2,0) q1@G via b0;
    readout q1 -> c0 @G via a0;
    pump q0 @G via p0;
    idle 1;   barrier q0,q1;   latch saved <- (c0) == 1;   when saved pump q1 @G via p0;

Every hardware instruction this site shows or reads is written the same way.  What the
studio needs beyond QCCDCert is an EXTENSION in the same syntax, never a different one:

    transport d0:S0->J0->S2[shuttle];   the bracket names the movement class (QCCDCert: the
                                         declared path); the route names the sites passed
    transport d0:S0->S2[shuttle] along E0,E1;   the rails, when the route is not by sites
    shuttle a0:S0->J0->S1->J1->S2;      a walk: one transport per trap-to-trap leg
    rotate L0 +13;                      a closed loop turned 13 slots (QCCDCert: 13 transports
                                         of every ion on the loop's mandatory channel)
    gate CX d0,a0 @S2;                  a named gate: the pulses QCCDCert's stage 4 emits for it
    pulse RZZ(pi/2) d0,a0 @S2;          the entangling pulse of the rzz gate set (`qccd.ir.gate_sets`;
                                         QCCDCert's own is MS)
    readout a0;   pump a0;              the site and the calibration are optional
    cool;   cool d0,a0;                 sympathetic cooling: every ion, or the ones named
    decode a0,a1;                       the classical decoder, on these outcomes
    fill L0 prefix d;   claim total_cost=12;   swap_or_stay NE toward d0,d1;   reorder_or_not d0;

Two directions:

* `call_text` / `parse_call` -- an authored statement (the studio's Write tab) and the call
  record `(method, args, kwargs)` the toolchain executes (`qccd.api.Program.apply_calls`);
  `parse_call(call_text(c)) == c` for every call this module prints;
* `instruction_text` -- one compiled TSIR instruction, as the design page's Hardware view,
  the evaluation page and the listings show it.

The old `p.shuttle(...)` statements are still accepted where text is read (the studio's
parser); they are no longer printed anywhere.
"""

from __future__ import annotations

import json
import math
import re

__all__ = ["fmt_angle", "call_text", "parse_call", "program_text", "instruction_text", "SyntaxError_",
           "KEYWORDS", "KEYWORD_OF"]


class SyntaxError_(ValueError):
    """A statement that is not in the language."""


#: statement keyword -> the call method it denotes
KEYWORDS = {"init": "init", "transport": "simd", "shuttle": "shuttle", "rotate": "rotate",
            "gate": "gate", "readout": "measure", "pump": "reset", "cool": "cool",
            "barrier": "barrier", "decode": "decode", "fill": "fill", "claim": "claim",
            "swap_or_stay": "swap_or_stay", "reorder_or_not": "reorder_or_not"}

#: call method -> the keyword its statement starts with (`move` and `simd` are both transport)
KEYWORD_OF = {**{m: k for k, m in KEYWORDS.items()}, "move": "transport"}

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.†'\-]*")

#: the physical pulses, printed `pulse NAME(angles) ions @site;` (`broadcast` for several): the
#: one-qubit pulse and the entangling pulse of either gate set (`qccd.ir.gate_sets`).  A frame
#: change is no pulse and prints as a `gate`.
PULSES = ("R", "MS", "RZZ")


# ------------------------------------------------------------------------------- angles
def fmt_angle(x: float) -> str:
    """An angle as QCCDCert prints it: an exact multiple of pi where it is one (denominators
    up to 8), else the decimal."""
    for d in (1, 2, 3, 4, 6, 8):
        k = x * d / math.pi
        n = round(k)
        if abs(k - n) < 1e-9:
            if n == 0:
                return "0"
            sign = "-" if n < 0 else ""
            n = abs(n)
            num = "pi" if n == 1 else f"{n}*pi"
            return sign + (num if d == 1 else f"{num}/{d}")
    r = round(float(x), 6)
    return repr(int(r)) if r == int(r) else repr(r)


def parse_angle(s: str) -> float:
    s = s.strip().replace("π", "pi")
    if not re.fullmatch(r"[0-9pi.+\-*/() eE]+", s):
        raise SyntaxError_(f"not an angle: {s!r}")
    return float(eval(s, {"__builtins__": {}}, {"pi": math.pi}))  # noqa: S307 -- digits, pi, + - * / ( ) only


# ------------------------------------------------------------------------- authored calls
def _move_text(mv, cls) -> str:
    """`ion:src->...->dst[cls]` for a move by sites `[ion, [src, ..., dst]]`, and
    `ion:src->dst[cls] along rails` for one by rails `[ion, src, dst, rails]`."""
    if len(mv) == 2 and isinstance(mv[1], (list, tuple)):
        text = f"{mv[0]}:" + "->".join(str(n) for n in mv[1])
        return text + (f"[{cls}]" if cls else "")
    ion, src, dst = mv[:3]
    via = mv[3] if len(mv) > 3 else None
    return f"{ion}:{src}->{dst}" + (f"[{cls}]" if cls else "") + (f" along ({','.join(via)})" if via else "")


def _meta(kwargs: dict, skip: tuple = ()) -> str:
    rest = {k: v for k, v in kwargs.items() if k not in skip}
    if not rest:
        return ""
    return " with " + ", ".join(f"{k}={_lit(v)}" for k, v in rest.items())


def _lit(v) -> str:
    if isinstance(v, str) and _NAME.fullmatch(v):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _ions(xs) -> str:
    return ",".join(str(x) for x in xs)


def call_text(method: str, args: list | tuple = (), kwargs: dict | None = None) -> str:
    """One authored statement, in the extended QCCDCert syntax."""
    args, kw = list(args), dict(kwargs or {})
    if method == "init":
        place = args[0] if args else kw.pop("placement")
        quanta = args[1] if len(args) > 1 else kw.pop("quanta", None)
        text = "init " + ", ".join(f"{q}@{s}" for q, s in place.items())
        if quanta:
            text += " | " + ", ".join(f"{q}={_lit(v)}" for q, v in quanta.items())
        return text + _meta(kw) + ";"
    if method in ("move", "simd"):
        if method == "move":
            ion, src, dst = args[:3]
            via = args[3] if len(args) > 3 else kw.pop("via", None)
            cls = args[4] if len(args) > 4 else kw.pop("cls", "shuttle")
            moves, mode = [[ion, src, dst, via]], kw.pop("mode", "inter")
        else:
            cls = args[0] if args else kw.pop("cls")
            moves = args[1] if len(args) > 1 else kw.pop("moves")
            mode = args[2] if len(args) > 2 else kw.pop("mode", "inter")
        # a cycle that moves no ion still names its class: `transport [shuttle];`
        text = "transport " + (", ".join(_move_text(mv, cls) for mv in moves) if moves else f"[{cls}]")
        if mode != "inter":
            text += f" mode {mode}"
        return text + _meta(kw) + ";"
    if method == "shuttle":
        ion = args[0] if args else kw.pop("ion")
        path = args[1] if len(args) > 1 else kw.pop("path")
        cls = args[2] if len(args) > 2 else kw.pop("cls", "shuttle")
        return f"shuttle {ion}:" + "->".join(path) + (f"[{cls}]" if cls != "shuttle" else "") + _meta(kw) + ";"
    if method == "rotate":
        delta = args[0] if args else kw.pop("delta")
        loop = args[1] if len(args) > 1 else kw.pop("loop", None)
        cls = args[2] if len(args) > 2 else kw.pop("cls", None)
        return ("rotate " + (f"{loop} " if loop else "") + f"{'+' if delta >= 0 else '-'}{abs(delta)}"
                + (f" [{cls}]" if cls else "") + _meta(kw) + ";")
    if method == "gate":
        name = args[0] if args else kw.pop("name")
        pairs = args[1] if len(args) > 1 else kw.pop("pairs")
        sites = args[2] if len(args) > 2 else kw.pop("sites", None)
        ops = []
        for i in range(max(len(pairs), len(sites or []))):
            who = _ions(pairs[i]) if i < len(pairs) else ""
            at = f"@{sites[i]}" if sites and i < len(sites) and sites[i] else ""
            ops.append(" ".join(x for x in (who, at) if x))
        return f"gate {name} " + " / ".join(ops) + _meta(kw) + ";"
    if method in ("measure", "reset", "decode"):
        word = {"measure": "readout", "reset": "pump", "decode": "decode"}[method]
        ions = args[0] if args else kw.pop("ions", None)
        return word + (f" {_ions(ions)}" if ions else "") + _meta(kw) + ";"
    if method == "cool":
        ions = args[0] if args else kw.pop("ions", None)
        return "cool" + (f" {_ions(ions)}" if ions else "") + _meta(kw) + ";"
    if method == "barrier":
        return "barrier" + _meta(kw) + ";"
    if method == "fill":
        loop = args[0] if args else kw.pop("loop", None)
        prefix = args[1] if len(args) > 1 else kw.pop("prefix", "d")
        return "fill" + (f" {loop}" if loop else "") + (f" prefix {prefix}" if prefix != "d" else "") + _meta(kw) + ";"
    if method == "claim":
        return "claim " + ", ".join(f"{k}={_lit(v)}" for k, v in kw.items()) + ";"
    if method == "swap_or_stay":
        diagonal = args[0] if args else kw.pop("diagonal", None)
        toward = args[1] if len(args) > 1 else kw.pop("toward", ())
        cb = kw.pop("checkerboard", None)
        return ("swap_or_stay" + (f" {diagonal}" if diagonal else "") + (f" toward {_ions(toward)}" if toward else "")
                + (f" checkerboard {_lit(cb)}" if cb is not None else "") + _meta(kw) + ";")
    if method == "reorder_or_not":
        rev = args[0] if args else kw.pop("reverse", ())
        return "reorder_or_not" + (f" {_ions(rev)}" if rev else "") + _meta(kw) + ";"
    raise SyntaxError_(f"no statement prints the call {method!r}")


def program_text(calls) -> str:
    return "".join(call_text(c["method"], c.get("args", ()), c.get("kwargs", {})) + "\n" for c in calls)


# --------------------------------------------------------------------------- the parser
def _split_top(s: str, sep: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def _unlit(s: str):
    s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        return s


def _take_meta(body: str) -> tuple[str, dict]:
    m = re.search(r"\swith\s+(.*)$", body)
    if not m:
        return body, {}
    kw = {}
    for item in _split_top(m.group(1), ","):
        k, _, v = item.partition("=")
        kw[k.strip()] = _unlit(v)
    return body[:m.start()], kw


def _parse_move(text: str) -> tuple[list, str | None]:
    """`ion:a->...->b[cls]` -> ([ion, [a, ..., b]], cls); `ion:a->b[cls] along r1,r2` ->
    ([ion, a, b, [r1, r2]], cls)."""
    m = re.fullmatch(r"\s*([^:\s]+)\s*:\s*([^\[\s]+?)\s*(?:\[\s*([^\]]+?)\s*\])?\s*(?:along\s+\(?([^)\s]+)\)?)?\s*", text)
    if not m:
        raise SyntaxError_(f"a move is `ion:site->site[class]`, not {text!r}")
    ion, route, cls, along = m.groups()
    nodes = [x.strip() for x in route.split("->")]
    if len(nodes) < 2:
        raise SyntaxError_(f"a route names at least two sites: {route!r}")
    if along:
        if len(nodes) != 2:
            raise SyntaxError_("a move `along` rails names only its two ends")
        return [ion, nodes[0], nodes[1], along.split(",")], cls
    return [ion, nodes], cls


def parse_call(stmt: str) -> tuple[str, list, dict]:
    """One statement -> (method, args, kwargs), the record `Program.apply_calls` runs."""
    s = stmt.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    word, _, rest = s.partition(" ")
    if word not in KEYWORDS:
        raise SyntaxError_(f"{word!r} is not a statement (have: {', '.join(sorted(KEYWORDS))})")
    rest, meta = _take_meta(rest)
    rest = rest.strip()
    if word == "init":
        place_txt, _, quanta_txt = rest.partition("|")
        place = {}
        for item in _split_top(place_txt, ","):
            q, _, site = item.partition("@")
            place[q.strip()] = site.strip()
        args = [place]
        if quanta_txt.strip():
            args.append({k.strip(): _unlit(v) for k, _, v in (it.partition("=") for it in _split_top(quanta_txt, ","))})
        return "init", args, meta
    if word == "transport":
        mode = "inter"
        m = re.search(r"\s+mode\s+(\S+)$", rest)
        if m:
            mode, rest = m.group(1), rest[:m.start()]
        empty = re.fullmatch(r"\[\s*(\S+?)\s*\]", rest.strip())
        if empty:
            kw = dict(meta)
            if mode != "inter":
                kw["mode"] = mode
            return "simd", [empty.group(1), []], kw
        parsed = [_parse_move(x) for x in _split_top(rest, ",")]
        classes = {c for _, c in parsed}
        if len(classes) > 1:
            raise SyntaxError_("one transport, one movement class (R4): " + ", ".join(sorted(map(str, classes))))
        cls = parsed[0][1] or "shuttle"
        out = [mv for mv, _ in parsed]
        kw = dict(meta)
        if mode != "inter":
            kw["mode"] = mode
        return "simd", [cls, out], kw
    if word == "shuttle":
        mv, cls = _parse_move(rest)
        if len(mv) != 2:
            raise SyntaxError_("a shuttle names its route by sites")
        ion, path = mv
        kw = dict(meta)
        if cls and cls != "shuttle":
            kw["cls"] = cls
        return "shuttle", [ion, path], kw
    if word == "rotate":
        m = re.fullmatch(r"(?:(\S+)\s+)?([+-]\d+)(?:\s*\[\s*(\S+?)\s*\])?", rest)
        if not m:
            raise SyntaxError_(f"`rotate [loop] +k` turns a loop k slots, not {rest!r}")
        loop, delta, cls = m.groups()
        args = [int(delta)] + ([loop] if loop or cls else [])
        if cls:
            if not loop:
                args.append(None)
            args.append(cls)
        return "rotate", args, meta
    if word == "gate":
        name, _, ops = rest.partition(" ")
        pairs, sites = [], []
        for op in [x.strip() for x in ops.split("/") if x.strip()]:
            who, _, at = op.partition("@")
            ions = [x.strip() for x in who.split(",") if x.strip()]
            if ions:
                pairs.append(ions)
            sites.append(at.strip() or None)
        args = [name, pairs] + ([sites] if any(sites) else [])
        return "gate", args, meta
    if word in ("readout", "pump", "decode", "cool"):
        method = KEYWORDS[word]
        ions = [x.strip() for x in rest.split(",") if x.strip()]
        return method, ([ions] if ions else []), meta
    if word == "barrier":
        return "barrier", [], meta
    if word == "fill":
        m = re.fullmatch(r"(?:(?!prefix\b)(\S+))?\s*(?:prefix\s+(\S+))?", rest)
        loop, prefix = m.groups() if m else (None, None)
        args = [loop] if loop else []
        if prefix:
            args = [loop, prefix]
        return "fill", args, meta
    if word == "claim":
        kw = {k.strip(): _unlit(v) for k, _, v in (it.partition("=") for it in _split_top(rest, ","))}
        return "claim", [], kw
    if word == "swap_or_stay":
        m = re.fullmatch(r"(?:(?!toward\b|checkerboard\b)(\S+))?\s*(?:toward\s+(\S+))?\s*(?:checkerboard\s+(.+))?", rest)
        diagonal, toward, cb = m.groups() if m else (None, None, None)
        args = [diagonal] + ([toward.split(",")] if toward else [])
        kw = dict(meta)
        if cb:
            kw["checkerboard"] = _unlit(cb)
        return "swap_or_stay", args, kw
    if word == "reorder_or_not":
        ions = [x.strip() for x in rest.split(",") if x.strip()]
        return "reorder_or_not", ([ions] if ions else []), meta
    raise SyntaxError_(word)


# ------------------------------------------------------------- compiled TSIR instructions
def _node_route(p, device) -> list[str]:
    """The sites a participant passes: its source, the far end of each rail of `via`."""
    src, dst = p.get("from") or p.get("src"), p.get("to") or p.get("dst")
    via = p.get("via") or []
    if device is None or not via:
        return [src, dst]
    out, node = [src], src
    for sid in via:
        seg = device.segments.get(sid)
        if seg is None:
            return [src, dst]
        node = seg.other(node)
        out.append(node)
    if out[-1] != dst:
        out.append(dst)
    return out


def instruction_text(ins, device=None) -> str:
    """One TSIR instruction (dict or `Instruction`) in the extended QCCDCert syntax.
    `device` (an `arch.device`) turns rails into the sites they pass."""
    d = ins if isinstance(ins, dict) else ins.to_json()
    t = d.get("type")
    if t == "init":
        return "init " + ", ".join(f"{q}@{s}" for q, s in (d.get("placement") or {}).items()) + ";"
    if t == "simd":
        tpl = d.get("template") or {}
        if tpl.get("kind") == "loop_shift":
            k = int(tpl.get("delta", 0))
            return f"rotate {tpl.get('loop')} {'+' if k >= 0 else '-'}{abs(k)};"
        parts = []
        for p in d.get("participants") or []:
            nodes = _node_route(p, device)
            text = f"{p['ion']}:" + "->".join(nodes) + f"[{d.get('class')}]"
            if device is None and p.get("via"):
                text += " along (" + ",".join(p["via"]) + ")"
            parts.append(text)
        if not parts:
            return f"transport [{d.get('class')}];"
        return "transport " + ", ".join(parts) + ";"
    if t == "gate":
        g = d.get("gate") or "?"
        params = d.get("params") or []
        sites = d.get("sites") or []
        pairs = d.get("pairs") or ([d["ions"]] if (d.get("arity") != 1 and len(d.get("ions") or []) == 2) else [])
        singles = [] if pairs else (d.get("ions") or [])

        def word(i):
            p = params[i] if i < len(params) else (params[0] if params else ())
            return f"{g}(" + ",".join(fmt_angle(x) for x in p) + ")" if p else g
        if g in PULSES and params:
            ops = [(f"{word(i)} {_ions(pr)}", sites[i] if i < len(sites) else (sites[0] if sites else None))
                   for i, pr in enumerate(pairs)] or \
                  [(f"{word(i)} {q}", sites[i] if i < len(sites) else (sites[0] if sites else None))
                   for i, q in enumerate(singles)]
            if len(ops) == 1:
                w, s = ops[0]
                return f"pulse {w}" + (f" @{s}" if s else "") + ";"
            return "broadcast " + ", ".join(w.replace(" ", " ", 1) + (f"@{s}" if s else "") for w, s in ops) + ";"
        ops = [_ions(pr) + (f" @{sites[i]}" if i < len(sites) and sites[i] else "") for i, pr in enumerate(pairs)] or \
              [f"{q}" + (f" @{sites[i]}" if i < len(sites) and sites[i] else "") for i, q in enumerate(singles)]
        head = word(0) if params else g
        return f"gate {head} " + " / ".join(ops) + ";"
    if t in ("measure", "reset", "decode"):
        word = {"measure": "readout", "reset": "pump", "decode": "decode"}[t]
        ions = d.get("ions") or []
        return word + (f" {_ions(ions)}" if ions else "") + ";"
    if t == "cool":
        ions = d.get("ions") or []
        return "cool" + ("" if d.get("broadcast") or not ions else f" {_ions(ions)}") + ";"
    if t == "barrier":
        note = (d.get("meta") or {}).get("note")
        if isinstance(note, str) and re.fullmatch(r"idle \d+", note):
            return note + ";"
        return "barrier;"
    return f"{t};"
