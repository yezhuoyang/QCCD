"""A machine specification written as QCCDCert hardware declarations.

QCCDCert's hardware description is a list of declarations, each a keyword followed by
fields, ended by `;`:

    hardware Primer; site S[1]; site J[1]; site G[2];
    path p0:S->J->G channel 0 waveform 0 duration 2;
    calibration c4:MS(pi/2) @G channel 7 duration 4;

A studio machine is written the same way.  `site` and `hardware` are QCCDCert's own
declarations, extended with where a site is drawn and which zone it belongs to; the rest
extend the list with what a studio machine also states (rails, loops, zones, movement
classes, the primitive curves the replay prices, the control plane):

    hardware ring144_24v from ring width 72 verticals 24 zones [data, trap, load];
    geometry explicit;
    site S0[2] at (0.0, 0.0) zone trap;
    junction J0 at (1.0, 0.0);
    rail E0:S0--J0 length 0.5;
    loop L0:S0,S1,S2 closed true kind ring;
    hardware lab from geometry zones [data, trap];
    move S3 to (2.0, 1.5);
    zone trap capacity 2 gate true spam true cool true;
    capacity [S0, S6] 4;
    length [E0, E1] 0.5;
    class rotate_cw type shift orbit L0 delta 1;
    control max_simd_classes_per_cycle 1;
    curve split [
        {us: 80.0, quanta: 6.0, table: qccdsim_jones, source: "2510.23519"},
        {us: 30.0, quanta: 1.0, table: transport_excitation, source: "2605.25118"}
    ];
    curve junction_cross degree 3 [
        {us: 100.0, quanta: 3.0, table: cyclone, source: "2511.15910"}
    ];
    describe "A ring of 144 slots" deck "ion_transport_deck_v3.pptx.pdf";

A field is `name value`.  A value is a number, `true`, `false`, `none`, a bare name, a
"quoted string", a list `[v, ...]` or a record `{name: v, ...}`.  `//` begins a comment.

Each declaration is one call record `{method, args, kwargs}`, the same record
`qccd.arch.edit.apply_call` runs through its method whitelist, so the text is never
executed.  `engine.js` (`hwStatementText`, `parseHardwareStatement`) is the browser's copy
of this module; `tests/test_hardware_text.py` holds the two to the same text.  The old
`m.set_zone(...)` call form is still read (`engine.js::_parseStatement`); it is no longer
printed.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

__all__ = ["KEYWORD_OF", "KEYWORDS", "statement_text", "parse_statement", "parse",
           "value_text", "HardwareSyntaxError"]

#: method -> the keyword it is written with
KEYWORD_OF = {
    "blank": "hardware", "from_template": "hardware", "blank_device": "hardware",
    "from_device": "hardware", "DeviceBuilder": "geometry", "d.site": "site",
    "d.junction": "junction", "d.segment": "rail", "d.loop": "loop", "move_site": "move",
    "describe": "describe", "set_zone": "zone", "set_site_capacity": "capacity",
    "set_segment_length": "length", "set_curve": "curve", "set_degree_curve": "curve",
    "set_primitive": "primitive", "declare_class": "class", "set_control": "control",
    "set_wiring": "wiring", "set_heating": "heating", "set_species": "species",
    "set_budget": "budget",
}

#: the keywords of the plain `keyword positional... field value...;` form, with their
#: method and how many positional values come before the fields
_PLAIN = {
    "describe": ("describe", 1), "zone": ("set_zone", 1), "capacity": ("set_site_capacity", 2),
    "length": ("set_segment_length", 2), "primitive": ("set_primitive", 1),
    "class": ("declare_class", 1), "control": ("set_control", 0), "wiring": ("set_wiring", 0),
    "heating": ("set_heating", 0), "species": ("set_species", 0), "budget": ("set_budget", 0),
}
_PLAIN_OF = {m: (k, n) for k, (m, n) in _PLAIN.items()}

KEYWORDS = frozenset(KEYWORD_OF.values())

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_RESERVED = frozenset({"true", "false", "none"})


class HardwareSyntaxError(ValueError):
    def __init__(self, message: str, col: int = 1):
        super().__init__(message)
        self.col = col


# --------------------------------------------------------------------------- printing


def _name(s: str) -> str:
    """A name in name position: bare when it is one, quoted otherwise."""
    s = str(s)
    return s if _IDENT.match(s) and s not in _RESERVED else json.dumps(s, ensure_ascii=False)


def value_text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "none"
    if isinstance(v, str):
        return _name(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(value_text(x) for x in v) + "]"
    if isinstance(v, Mapping):
        return "{" + ", ".join(f"{_name(k)}: {value_text(x)}" for k, x in v.items()) + "}"
    return repr(v)


def _fields(kw: Mapping, skip=()) -> str:
    return "".join(f" {_name(k)} {value_text(v)}" for k, v in kw.items() if k not in skip)


def _pos(p) -> str:
    return "(" + ", ".join(value_text(c) for c in p) + ")"


def _points(pts) -> str:
    if not pts:
        return "[]"
    return "[\n" + ",\n".join("    " + value_text(p) for p in pts) + "\n]"


def statement_text(call: Mapping) -> str | None:
    """One call record as a declaration, or None when the record has a shape the
    declaration form does not write (it is then shown in the call form)."""
    method = str(call.get("method", ""))
    args = list(call.get("args") or [])
    kw = dict(call.get("kwargs") or {})
    if method in ("blank", "from_template"):
        if len(args) != 1 or (method == "from_template") != ("template" in kw) \
                or str(args[0]) == "geometry":
            return None
        head = f"hardware {_name(kw['name'])} " if "name" in kw else "hardware "
        return f"{head}from {_name(args[0])}{_fields(kw, ('name',))};"
    if method in ("blank_device", "from_device"):
        if args or (method == "from_device") != ("template" in kw):
            return None
        head = f"hardware {_name(kw['name'])} " if "name" in kw else "hardware "
        return f"{head}from geometry{_fields(kw, ('name',))};"
    if method == "DeviceBuilder":
        if len(args) > 1:
            return None
        return f"geometry {_name(args[0] if args else 'explicit')}{_fields(kw)};"
    if method == "d.site":
        if len(args) < 2 or "capacity" in kw and not isinstance(kw["capacity"], int):
            return None
        cap = f"[{kw['capacity']}]" if kw.get("capacity") else ""
        rest = {k: v for k, v in kw.items() if k != "capacity" or not v}
        return f"site {_name(args[0])}{cap} at {_pos(args[1:])}{_fields(rest)};"
    if method == "d.junction":
        if len(args) < 2:
            return None
        return f"junction {_name(args[0])} at {_pos(args[1:])}{_fields(kw)};"
    if method == "d.segment":
        if len(args) != 3:
            return None
        return f"rail {_name(args[0])}:{_name(args[1])}--{_name(args[2])}{_fields(kw)};"
    if method == "d.loop":
        if len(args) != 2 or not isinstance(args[1], (list, tuple)):
            return None
        return f"loop {_name(args[0])}:{','.join(_name(n) for n in args[1])}{_fields(kw)};"
    if method == "move_site":
        if len(args) < 2 or kw:
            return None
        return f"move {_name(args[0])} to {_pos(args[1:])};"
    if method == "set_curve":
        if len(args) != 2 or not isinstance(args[1], (list, tuple)):
            return None
        return f"curve {_name(args[0])} {_points(args[1])}{_fields(kw)};"
    if method == "set_degree_curve":
        if len(args) != 3 or not isinstance(args[1], int) or not isinstance(args[2], (list, tuple)):
            return None
        return f"curve {_name(args[0])} degree {args[1]} {_points(args[2])}{_fields(kw)};"
    if method in _PLAIN_OF:
        word, n = _PLAIN_OF[method]
        if len(args) != n:
            return None
        return word + "".join(" " + value_text(a) for a in args) + _fields(kw) + ";"
    return None


# ---------------------------------------------------------------------------- parsing

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<op>--|->|[()\[\]{},:;@])
""", re.X)


def _tokens(text: str) -> list[tuple[str, Any, int]]:
    out, i = [], 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m:
            raise HardwareSyntaxError(f"unexpected character {text[i]!r}", i + 1)
        kind = m.lastgroup
        if kind == "str":
            out.append(("str", json.loads(m.group()), i + 1))
        elif kind == "num":
            s = m.group()
            out.append(("num", float(s) if re.search(r"[.eE]", s) else int(s), i + 1))
        elif kind in ("name", "op"):
            out.append((kind, m.group(), i + 1))
        i = m.end()
    return out


class _P:
    def __init__(self, text: str):
        self.t = _tokens(text)
        self.i = 0

    def peek(self, k: int = 0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("end", None, 0)

    def col(self) -> int:
        return self.peek()[2] or (self.t[-1][2] + 1 if self.t else 1)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def is_op(self, v: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok[0] == "op" and tok[1] == v

    def is_word(self, v: str) -> bool:
        tok = self.peek()
        return tok[0] == "name" and tok[1] == v

    def op(self, v: str) -> None:
        if not self.is_op(v):
            raise HardwareSyntaxError(f"expected {v!r}", self.col())
        self.i += 1

    def word(self, v: str) -> None:
        if not self.is_word(v):
            raise HardwareSyntaxError(f"expected {v!r}", self.col())
        self.i += 1

    def name(self) -> str:
        kind, v, col = self.peek()
        if kind == "str" or (kind == "name" and v not in _RESERVED):
            self.i += 1
            return v
        raise HardwareSyntaxError("expected a name", col or self.col())

    def value(self) -> Any:
        kind, v, col = self.peek()
        if kind == "num" or kind == "str":
            self.i += 1
            return v
        if kind == "name":
            self.i += 1
            return {"true": True, "false": False, "none": None}.get(v, v)
        if self.is_op("["):
            self.i += 1
            out = []
            while not self.is_op("]"):
                out.append(self.value())
                if not self.is_op(","):
                    break
                self.i += 1
            self.op("]")
            return out
        if self.is_op("{"):
            self.i += 1
            rec = {}
            while not self.is_op("}"):
                k = self.name()
                self.op(":")
                rec[k] = self.value()
                if not self.is_op(","):
                    break
                self.i += 1
            self.op("}")
            return rec
        raise HardwareSyntaxError("expected a value", col or self.col())

    def pos(self) -> list:
        self.op("(")
        out = [self.value()]
        while self.is_op(","):
            self.i += 1
            out.append(self.value())
        self.op(")")
        return out

    def fields(self) -> dict:
        kw = {}
        while not self.is_op(";") and self.peek()[0] != "end":
            k = self.name()
            if k in kw:
                raise HardwareSyntaxError(f"field {k!r} given twice", self.col())
            kw[k] = self.value()
        return kw

    def end(self) -> None:
        self.op(";")
        if self.peek()[0] != "end":
            raise HardwareSyntaxError("one declaration per line: trailing input after ';'",
                                      self.col())


def parse_statement(text: str) -> dict:
    """One declaration -> its call record."""
    p = _P(text)
    kind, word, col = p.peek()
    if kind != "name" or word not in KEYWORDS:
        raise HardwareSyntaxError(f"{word!r} is not a hardware declaration", col or 1)
    p.take()
    args: list = []
    if word == "hardware":
        name = None if p.is_word("from") else p.name()
        p.word("from")
        gen = p.name()
        kw = p.fields()
        if name is not None:
            kw = {"name": name, **kw}
        if gen == "geometry":
            method = "from_device" if "template" in kw else "blank_device"
        else:
            method, args = ("from_template" if "template" in kw else "blank"), [gen]
            if name is not None:   # the record keeps `name` after the generator parameters
                kw = {k: v for k, v in kw.items() if k != "name"} | {"name": name}
                if method == "blank" and "zones" in kw:
                    kw["zones"] = kw.pop("zones")
                if method == "from_template":
                    kw["template"] = kw.pop("template")
    elif word == "geometry":
        method, args = "DeviceBuilder", [p.name()]
        kw = p.fields()
    elif word in ("site", "junction"):
        method = "d." + word
        sid = p.name()
        cap = None
        if word == "site" and p.is_op("["):
            p.take()
            c = p.value()
            if not isinstance(c, int) or isinstance(c, bool):
                raise HardwareSyntaxError("a site's capacity is a whole number", p.col())
            cap = c
            p.op("]")
        p.word("at")
        args = [sid, *p.pos()]
        kw = p.fields()
        if cap is not None:
            kw["capacity"] = cap
    elif word == "rail":
        method = "d.segment"
        sid = p.name()
        p.op(":")
        a = p.name()
        p.op("--")
        args = [sid, a, p.name()]
        kw = p.fields()
    elif word == "loop":
        method = "d.loop"
        lid = p.name()
        p.op(":")
        nodes = [p.name()]
        while p.is_op(","):
            p.take()
            nodes.append(p.name())
        args = [lid, nodes]
        kw = p.fields()
    elif word == "move":
        method = "move_site"
        sid = p.name()
        p.word("to")
        args = [sid, *p.pos()]
        kw = {}
    elif word == "curve":
        prim = p.name()
        if p.is_word("degree"):
            p.take()
            deg = p.value()
            if not isinstance(deg, int) or isinstance(deg, bool):
                raise HardwareSyntaxError("a degree is a whole number", p.col())
            method, args = "set_degree_curve", [prim, deg, p.value()]
        else:
            method, args = "set_curve", [prim, p.value()]
        kw = p.fields()
    else:
        method, n = _PLAIN[word]
        args = [p.value() for _ in range(n)]
        kw = p.fields()
    p.end()
    return {"method": method, "args": args, "kwargs": kw}


def _statements(text: str):
    """(first line, text) of each declaration: a line continues while a bracket is open."""
    buf, depth, start = None, 0, 0
    for i, line in enumerate(text.split("\n")):
        if buf is None:
            if not line.strip() or line.lstrip().startswith(("//", "#")):
                continue
            buf, start = line, i + 1
        else:
            buf += "\n" + line
        depth += _depth(line)
        if depth <= 0:
            yield start, buf
            buf, depth = None, 0
    if buf is not None:
        yield start, buf


def _depth(line: str) -> int:
    d, q, i = 0, False, 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                i += 1
            elif c == '"':
                q = False
        elif c == '"':
            q = True
        elif line.startswith("//", i):
            break
        elif c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
        i += 1
    return d


def parse(text: str) -> list[dict]:
    """A whole machine specification -> its call records, in order."""
    out = []
    for line, stmt in _statements(text):
        try:
            out.append(parse_statement(stmt))
        except HardwareSyntaxError as exc:
            raise HardwareSyntaxError(f"line {line}: {exc}", exc.col) from None
    return out
