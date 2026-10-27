"""TSIR modules: a program on a given architecture, in which every hardware instruction says
which gadget it belongs to.  docs/tsir.md, "Modules"; docs/FULLSTACK_PLAN.md, slice S6.

TSIR is one concurrent cycle of one device per instruction.  A whole run is too large to be
one TSIR program (FormalRV's Shor is 577,245 logical instructions and three hours), and most
of it repeats.  The modules say it once:

    architecture fullstack_d3                   the floor, instanced
      place bench1 ppm_d3 48.0 12.0 16          an instance of a place master
      road /door_bench1_in_a j0_6.s -> bench1.in_a chan9   a channel; its gadget is chan9.carry
      wire /wire_bench1_syn bench1.syn -> decoder.syn 5.0  a classical wire, latency (µs)

    program shor15                              the program's own body
    gadget m3 ctl_maj_3                         one composite gadget: a gate, in one context
      ins 7 3 "ppm Z arg0 Z arg1 Z l3 -> o4" ; kind=ppm op=ppm.ZZZ ...   a logical instruction
        run 45 88000.0 88640.0 ppm_d3.ppm.ZZZ @bench1 p0:126 d0:1000000000 ...
        carry 12 87105.0 chan9.carry /door_bench1_in_a j0_6.s 1000000000..1000000008 ; ...
        msg 3 121035.0 121040.0 /wire_bench1_syn bench1.syn -> decoder.syn 3 syndrome
        call c0 89875.0 m0 zzy q0 q1 ; gid=153 ...        a composite gadget, called
        hold 0 1300.0 413230.0 x4 mem_d3.refresh @mem13 arg1 aX0:18 ...   rounds while it rests

    leaf ppm_d3.ppm.ZZZ                         a leaf gadget: one op of one master, its TSIR
      tsir ppm_d3.ppm.ZZZ+cooling
      0 init ...
      65 gate CX p0:e6 @A45 ; op=ppm round=0

* **Every hardware instruction is in exactly one gadget.**  A `run` row names a leaf gadget
  (`master.op`) and the place instance it runs at; the leaf module is that op's TSIR, so the
  instruction `#65` of the run is line `65 ...` of `leaf ppm_d3.ppm.ZZZ`.  A `carry` names its
  road's gadget (`chan<L>.carry`, the conveyor every road is priced from); a `hold` names the
  memory's `refresh`.  Only the decoder's and the archive's rows are classical: a modeled
  latency, named, not hardware.
* **Modular.**  A leaf gadget is written once per master op; a composite gadget once per
  context (the folded schedule's macro, `fold.py`); a call is one line.
* **Scalable.**  The text is the size of the folded schedule, one file a gadget.
* **The same program.**  `read_modules(write_modules(g))` gives back `g` exactly
  (`tests/test_modules.py`), so every check runs on what the reader reads.

A row's numbers are as the schedule has them: times in µs, a carry's departures and takes
relative to its start, ions by global id (`a..b` a run of ids).  After ` ; ` come the row's
other fields as `key=value`, a value a bare word or JSON.  Every row under an `ins` header
belongs to that instruction.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..ir.tsir import TSIR
from ..ir.tsir_text import _tokens, _unval, _val, parse_program, program_text
from .build import published

__all__ = ["write_modules", "read_modules", "parse_modules", "modules_text", "Modules",
           "ModuleError", "MODULES_VERSION"]

MODULES_VERSION = 1
GIR_FOLDED = "qccd.gir/0.3"

#: the lists every body carries (a macro also `uses`)
_LISTS = ("events", "carries", "messages", "windows", "frames", "outcomes", "refused", "holds", "calls")
#: root keys that are the architecture as instanced, written in its section
_ARCH = ("top", "model", "units", "leaves", "nets", "wires", "channels", "control", "ion_order")
_RECORDS = ("window", "frame", "outcome")


class ModuleError(ValueError):
    pass


# --------------------------------------------------------------------------- values


def _v(x) -> str:
    return _val(x)


_NAME = re.compile(r"[A-Za-z_][\w!]*")


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


class _Names:
    """A body's ions as the text names them.  An ion is its global id, except one of a
    gadget's parameter blocks, whose ids are symbols its caller binds: `arg0.3` is the fourth
    ion of `arg0`.  A list is runs of ids (`126..134,140`), and a list that is exactly one
    block's ions, in order, is that block's name (`q0`)."""

    def __init__(self, blocks: dict | None = None):
        self.whole: dict = {}
        self.sym: dict = {}
        self.blocks: dict = {}
        for name, b in (blocks or {}).items():
            ions = b.get("ions") or []
            if not _NAME.fullmatch(name) or not ions or not all(_is_int(i) for i in ions):
                continue
            self.blocks[name] = ions
            self.whole.setdefault(tuple(ions), name)
            if "param" in b:
                for k, gid in enumerate(ions):
                    self.sym[gid] = f"{name}.{k}"

    def ion(self, gid) -> str:
        return self.sym.get(gid) if _is_int(gid) and gid in self.sym else _v(gid)

    def union(self, s: str):
        if "." in s and not s[0].isdigit() and s[0] != "-":
            name, _, k = s.rpartition(".")
            if name in self.blocks:
                return self.blocks[name][int(k)]
        return _unval(s)

    def ions(self, ids) -> str:
        if not ids:
            return "-"
        if not all(_is_int(i) for i in ids):
            return json.dumps(ids, separators=(",", ":"))
        if tuple(ids) in self.whole:
            return self.whole[tuple(ids)]
        parts, a = [], 0
        while a < len(ids):
            if ids[a] in self.sym:
                parts.append(self.sym[ids[a]])
                a += 1
                continue
            b = a
            while b + 1 < len(ids) and ids[b + 1] == ids[b] + 1 and ids[b + 1] not in self.sym:
                b += 1
            parts.append(str(ids[a]) if a == b else f"{ids[a]}..{ids[b]}")
            a = b + 1
        return ",".join(parts)

    def unions(self, s: str) -> list:
        if s == "-":
            return []
        if s.startswith("["):
            return json.loads(s)
        if s in self.blocks:
            return list(self.blocks[s])
        out: list = []
        for p in s.split(","):
            if ".." in p and p.split("..")[0].lstrip("-").isdigit():
                a, b = p.split("..")
                out.extend(range(int(a), int(b) + 1))
            else:
                out.append(self.union(p))
        return out

    def binds(self, bind) -> list[str]:
        return [f"{n}:{self.ion(g)}" for n, g in bind]

    def unbind(self, toks) -> list:
        out = []
        for t in toks:
            n, _, g = t.rpartition(":")
            out.append([n, self.union(g)])
        return out


_PLAIN = _Names()


def _end(place: str, port: str) -> str:
    if "." in place:
        raise ModuleError(f"a place name with a dot cannot be written: {place!r}")
    return f"{place}.{port}"


def _unend(s: str) -> tuple:
    a, _, b = s.partition(".")
    return a, b


def _notes(d: dict, skip=()) -> list[str]:
    out = []
    for k, x in d.items():
        if k in skip:
            continue
        if not k or not (k[0].isalpha() or k[0] == "_") or any(c in k for c in " =;\"[]{}"):
            raise ModuleError(f"a key {k!r} cannot be written")
        out.append(f"{k}={_v(x)}")
    return out


def _line(words: list, notes: list) -> str:
    return " ".join(str(w) for w in words) + (" ; " + " ".join(notes) if notes else "")


def _split(line: str) -> tuple[list, dict]:
    toks = _tokens(line)
    if ";" in toks:
        k = toks.index(";")
        pos, ann = toks[:k], toks[k + 1:]
    else:
        pos, ann = toks, []
    notes = {}
    for t in ann:
        key, eq, val = t.partition("=")
        if not eq:
            raise ModuleError(f"{t!r} is not key=value")
        notes[key] = _unval(val)
    return pos, notes


def _num(s: str):
    return json.loads(s)


# --------------------------------------------------------------------------- the writer


@dataclass
class Modules:
    """What the reader gives back: the folded schedule (GIR 0.3), the leaf gadgets' TSIR
    (`(master, op) -> TSIR`, or `None` for a classical one), and the sources they name."""
    gir: dict
    leaves: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    #: (module, kind, id) -> (file, line): where each row is written; `module` is None for
    #: the program, a macro id for a gadget, `("leaf", master, op)` for a leaf
    lines: dict = field(default_factory=dict)


class _Writer:
    def __init__(self, g: dict):
        if g.get("schema") != GIR_FOLDED:
            raise ModuleError(f"modules are written from a folded schedule ({GIR_FOLDED}), "
                              f"not {g.get('schema')}")
        self.g = g
        self.places = {p: v[0] for p, v in (g.get("leaves") or {}).items()}
        self.road = {n: v[4] for n, v in (g.get("nets") or {}).items() if len(v) >= 5}

    # -- the architecture -------------------------------------------------------------------
    def architecture(self) -> list[str]:
        g = self.g
        L = ["tsir modules 1", "", f"architecture {_v(g['top'])}"]
        for k in ("model", "units"):
            if k in g:
                L.append(f"  let {k} {_v(g[k])}")
        for p, v in g["leaves"].items():
            L.append("  " + _line(["place", _v(p), _v(v[0])] + [_v(x) for x in v[1:]], []))
        for n, v in g["nets"].items():
            if len(v) == 5:
                L.append(f"  road {_v(n)} {_end(v[0], v[1])} -> {_end(v[2], v[3])} chan{v[4]}")
            else:
                L.append(f"  road {_v(n)} ; raw={_v(v)}")
        for w, v in g["wires"].items():
            L.append(f"  wire {_v(w)} {_end(v[0], v[1])} -> {_end(v[2], v[3])} {_v(v[4])}")
        for m, names in g["ion_order"].items():
            L.append("  " + " ".join(["order", _v(m)] + [_v(x) for x in names]))
        for c, v in g["channels"].items():
            L.append("  " + _line(["channel", _v(c)], _notes(v)))
        L.append("  " + _line(["control"], _notes(g["control"])))
        return L

    # -- a body: the program's own, or a gadget's -----------------------------------------
    def body(self, body: dict, head: str, *, skip=(), source_file: str | None = None) -> list[str]:
        L = ["", head]
        prog = body["program"]
        about = {k: v for k, v in prog.items() if k != "instructions"}
        if source_file is not None and about.get("source"):
            L.append(f"  source {_v(source_file)}")
            about.pop("source")
        L.append("  " + _line(["about"], _notes(about)))
        for k, v in body.items():
            if k in skip or k in _LISTS or k in ("program", "blocks", "uses", "macros", "schema"):
                continue
            L.append(f"  let {k} {_v(v)}")
        for name, b in body["blocks"].items():
            L.append("  " + _line(["block", _v(name), _PLAIN.ions(b.get("ions"))], _notes(b, skip=("ions",))))
        self.nm = _Names(body["blocks"])
        for k, u in (body.get("uses") or {}).items():
            L.append(f"  use {_v(k)} {_v(u[0])} {_v(u[1])}")
        # the rows, under the instruction they carry out, in time order
        rows: dict = defaultdict(list)
        for e in body["events"]:
            rows[e[6]].append((e[1], 0, e[0], self.run(e)))
        for c in body["carries"]:
            rows[c[9]].append((c[4], 1, c[0], self.carry(c)))
        for m in body["messages"]:
            rows[m[10]].append((m[6], 2, m[0], self.msg(m)))
        for k, c in enumerate(body["calls"]):
            rows[c["ins"]].append((c["t0"], 3, k, self.call(k, c)))
        for k, h in enumerate(body["holds"]):
            rows[h["ins"]].append((h["from"], 4, k, self.hold(k, h)))
        for ins in prog["instructions"]:
            L.append("  " + _line(["ins", _v(ins["id"]), _v(ins.get("line")), _v(ins.get("text"))],
                                  _notes(ins, skip=("id", "line", "text"))))
            for row in sorted(rows.pop(ins["id"], []), key=_order):
                L.append("    " + row[3])
        for key in sorted(rows, key=lambda k: (k is None, str(k))):
            L.append(f"  rows {_v(key)}")
            for row in sorted(rows[key], key=_order):
                L.append("    " + row[3])
        for kind, key in (("window", "windows"), ("frame", "frames"), ("outcome", "outcomes")):
            for r in body[key]:
                L.append("  " + _line([kind], _notes(r)))
        for r in body["refused"]:
            L.append("  " + _line(["refused", _v(r[0]), _v(r[1])], []))
        return L

    def run(self, e) -> str:
        eid, t0, t1, place, op, rep, _ins, grp, bind, warp = e[:10]
        master = self.places.get(place)
        gadget = f"{master}.{op}" if master else op
        notes = []
        if rep != 1:
            notes.append(f"x={_v(rep)}")
        if grp != f"i{_ins}":
            notes.append(f"grp={_v(grp)}")
        if warp:
            notes.append(f"warp={_v(warp)}")
        if len(e) > 10:
            # classically controlled: the bit it waits for, and when it was at the door
            notes.append(f"guard={_v(e[10])}")
        if len(e) > 11:
            raise ModuleError(f"event {eid} has {len(e)} fields; a run row writes 11")
        return _line(["run", eid, _v(t0), _v(t1), _v(gadget), "@" + place] + self.nm.binds(bind), notes)

    def carry(self, c) -> str:
        cid, net, leaf, port, t0, dep, transit, ions, role, _ins, grp, last, taken = c
        gadget = f"chan{self.road[net]}.carry" if net in self.road else "-"
        notes = [f"role={_v(role)}", f"dep={_v(dep)}", f"transit={_v(transit)}",
                 f"last={_v(last)}", f"taken={_v(taken)}"]
        if grp != f"i{_ins}":
            notes.append(f"grp={_v(grp)}")
        return _line(["carry", cid, _v(t0), gadget, _v(net), _end(leaf, port), self.nm.ions(ions)], notes)

    def msg(self, m) -> str:
        mid, wire, a, ap, b, bp, t0, t1, bits, signal, _ins, grp, label, src = m
        notes = [f"label={_v(label)}", f"src={_v(src)}"]
        if grp != f"i{_ins}":
            notes.append(f"grp={_v(grp)}")
        return _line(["msg", mid, _v(t0), _v(t1), _v(wire), _end(a, ap), "->", _end(b, bp),
                      _v(bits), _v(signal)], notes)

    def call(self, k: int, c: dict) -> str:
        if c["id"] != f"c{k}":
            raise ModuleError(f"call {c['id']} is number {k} in its body")
        return _line(["call", c["id"], _v(c["t0"]), _v(c["m"]), _v(c["gate"])] + [_v(a) for a in c["args"]],
                     _notes(c, skip=("id", "t0", "m", "gate", "args", "ins")))

    def hold(self, k: int, h: dict) -> str:
        master = self.places.get(h["place"])
        gadget = f"{master}.refresh" if master else "refresh"
        return _line(["hold", k, _v(h["from"]), _v(h["every"]), f"x{h['n']}", gadget, "@" + h["place"],
                      _v(h["block"])] + self.nm.binds(h["bind"]),
                     _notes(h, skip=("from", "every", "n", "place", "block", "bind", "ins")))

    # -- the leaf gadgets ---------------------------------------------------------------------
    def used_leaves(self) -> list[tuple]:
        """(master, op) of every leaf gadget a row names, in first-use order."""
        seen: dict = {}
        g = self.g
        for body in [g] + list(g["macros"].values()):
            for e in body["events"]:
                m = self.places.get(e[3])
                if m:
                    seen.setdefault((m, e[4]), None)
            for c in body["carries"]:
                if c[1] in self.road:
                    seen.setdefault((f"chan{self.road[c[1]]}", "carry"), None)
            for h in body["holds"]:
                m = self.places.get(h["place"])
                if m:
                    seen.setdefault((m, "refresh"), None)
        return list(seen)


def _order(row):
    return (row[0] if row[0] is not None else 0.0, row[1], row[2])


def _leaf_program(leaves, master: str, op: str):
    """The TSIR of a leaf gadget, from the library; a road's from its reference conveyor;
    None for a classical place (no program: a modeled latency)."""
    if leaves is None:
        return None
    d = leaves.data.get(master)
    if d is None and master.startswith("chan") and op == "carry":
        from .leaves.transport import channel
        return TSIR.from_json(published(channel(int(master[4:]), 1).programs["carry"].to_json()))
    progs = (d or {}).get("programs") or {}
    if op in progs:
        return TSIR.from_json(published(progs[op]))
    return None


def _leaf_lines(master: str, op: str, prog, source: dict | None) -> list[str]:
    L = ["", f"leaf {master}.{op}" + ("" if prog is not None else " classical")]
    if source:
        L.append(f"  source {_v(source['name'])}")
    if prog is not None:
        L += ["  " + x for x in program_text(prog).splitlines()]
    return L


def modules_text(g: dict, leaves=None) -> str:
    """The whole run as one text: architecture, program, every gadget, every leaf it names."""
    w = _Writer(g)
    L = w.architecture()
    L += w.body(g, f"program {_v(g['program'].get('name') or 'program')}", skip=_ARCH)
    for mid, m in g["macros"].items():
        L += w.body(m, f"gadget {_v(mid)} {_v(m.get('gate'))}", skip=("gate",))
    for master, op in (w.used_leaves() if leaves is not None else []):
        src = ((leaves.data.get(master) or {}).get("sources") or {}).get(op)
        L += _leaf_lines(master, op, _leaf_program(leaves, master, op), src)
    return "\n".join(L) + "\n"


def write_modules(g: dict, out_dir, *, leaves=None, scripts: bool = False) -> dict:
    """Write the run as modules under `out_dir`: `program.tsir` (the architecture and the
    program's own body), `gadget/<id>.tsir` (each composite gadget), `leaf/<master>.tsir`
    (each master's ops the run names), `source/` (the program's source, the protocols the
    leaves compile).  With `scripts`, each module also as `<file>.js`, a script handing its
    text to the page (`window.__gadgetModule`), which loads it only when it is looked at,
    from `file://` as well.  Returns {"files": [...], "lines": {(module, kind, id): (file, line)}}."""
    out = Path(out_dir)
    (out / "gadget").mkdir(parents=True, exist_ok=True)
    (out / "leaf").mkdir(parents=True, exist_ok=True)
    (out / "source").mkdir(parents=True, exist_ok=True)
    w = _Writer(g)
    files: list = []
    lines: dict = {}

    def put(rel: str, L: list[str], module) -> None:
        text = "\n".join(L) + "\n"
        (out / rel).write_text(text, encoding="utf-8")
        files.append(rel)
        _index_lines(L, rel, module, lines)
        if scripts:
            body = json.dumps(text, ensure_ascii=False).replace("</", "<\\/")
            (out / (rel[:-5] + ".js")).write_text(
                f"window.__gadgetModule&&window.__gadgetModule({json.dumps(rel)},{body});\n", encoding="utf-8")

    name = g["program"].get("name") or "program"
    src_name = None
    if g["program"].get("source"):
        src_name = f"source/{name}.alg"
        (out / src_name).write_text(g["program"]["source"], encoding="utf-8")
        files.append(src_name)
    L = w.architecture()
    L += w.body(g, f"program {_v(name)}", skip=_ARCH, source_file=src_name)
    L += ["", "uses " + " ".join(_v(m) for m in g["macros"])] if g["macros"] else []
    put("program.tsir", L, None)
    for mid, m in g["macros"].items():
        put(f"gadget/{mid}.tsir", ["tsir modules 1"] + w.body(m, f"gadget {_v(mid)} {_v(m.get('gate'))}",
                                                             skip=("gate",)), mid)
    by_master: dict = defaultdict(list)
    for master, op in (w.used_leaves() if leaves is not None else []):
        by_master[master].append(op)
    for master, ops in by_master.items():
        L = ["tsir modules 1"]
        for op in ops:
            src = ((leaves.data.get(master) or {}).get("sources") or {}).get(op)
            if src:
                (out / "source" / src["name"]).write_text(src["text"], encoding="utf-8")
                if f"source/{src['name']}" not in files:
                    files.append(f"source/{src['name']}")
            L += _leaf_lines(master, op, _leaf_program(leaves, master, op), src)
        put(f"leaf/{master}.tsir", L, ("leaf", master))
    return {"files": files, "lines": lines}


def _index_lines(L: list[str], rel: str, module, lines: dict) -> None:
    """Where each row was written: (module, kind, id) -> (file, line number)."""
    leaf_op = None
    for n, raw in enumerate(L, start=1):
        s = raw.strip()
        if not s:
            continue
        word, _, rest = s.partition(" ")
        if word == "leaf":
            leaf_op = rest.split()[0]
            continue
        if isinstance(module, tuple) and leaf_op is not None:
            head = word
            if head.isdigit():
                lines[("leaf", leaf_op, int(head))] = (rel, n)
            continue
        if word in ("run", "carry", "msg"):
            lines[(module, word, int(rest.split()[0]))] = (rel, n)
        elif word in ("call", "hold"):
            k = rest.split()[0]
            lines[(module, word, int(k[1:]) if word == "call" else int(k))] = (rel, n)
        elif word == "ins":
            lines[(module, "ins", _unval(rest.split()[0]))] = (rel, n)


# --------------------------------------------------------------------------- the reader


def _empty_body() -> dict:
    b = {k: [] for k in _LISTS}
    b["blocks"] = {}
    b["program"] = {"instructions": []}
    return b


class _Reader:
    def __init__(self):
        self.arch: dict = {}
        self.root: dict | None = None
        self.macros: dict = {}
        self.leaves: dict = {}
        self.leaf_text: dict = {}
        self.sources: dict = {}
        self.lines: dict = {}
        self.places: dict = {}
        self.uses: list = []
        self._names, self._names_key = _PLAIN, None

    def feed(self, text: str, rel: str, resolve) -> None:
        mod = None            # ("arch",) | ("program", body) | ("gadget", mid, body) | ("leaf", key)
        body = None
        ins_id = None
        for n, raw in enumerate(text.splitlines(), start=1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            try:
                word, _, rest = s.partition(" ")
                if word == "tsir" and rest.startswith("modules"):
                    if mod is None or mod[0] != "leaf":
                        v = int(rest.split()[1])
                        if v != MODULES_VERSION:
                            raise ModuleError(f"tsir modules {v}: this reader reads {MODULES_VERSION}")
                        continue
                if word == "architecture":
                    mod, body = ("arch",), None
                    self.arch.update(top=_unval(rest), leaves={}, nets={}, wires={}, ion_order={},
                                     channels={})
                    continue
                if word in ("program", "gadget"):
                    body = _empty_body()
                    if word == "program":
                        self.root = body
                        mod = ("program", body)
                    else:
                        toks = _tokens(rest)
                        mid = _unval(toks[0])
                        body["gate"] = _unval(toks[1])
                        body["uses"] = {}
                        self.macros[mid] = body
                        mod = ("gadget", mid, body)
                    ins_id = None
                    continue
                if word == "leaf":
                    toks = rest.split()
                    master, _, op = toks[0].partition(".")
                    key = (master, op)
                    self.leaf_text[key] = []
                    self.leaves[key] = None if (len(toks) > 1 and toks[1] == "classical") else []
                    mod, body = ("leaf", key), None
                    continue
                if word == "uses":
                    self.uses += [_unval(t) for t in rest.split()]
                    continue
                if mod is None:
                    raise ModuleError(f"{word!r} outside a module")
                if mod[0] == "leaf":
                    if word == "source":
                        self.leaf_text.setdefault(("source", mod[1]), []).append(_unval(rest))
                    else:
                        self.leaf_text[mod[1]].append(s)
                        if word.isdigit():
                            self.lines[("leaf", f"{mod[1][0]}.{mod[1][1]}", int(word))] = (rel, n)
                    continue
                if mod[0] == "arch":
                    self.arch_line(word, rest)
                    continue
                module = None if mod[0] == "program" else mod[1]
                ins_id = self.body_line(body, word, rest, ins_id, resolve, module, rel, n)
            except ModuleError as e:
                raise ModuleError(f"{rel}:{n}: {e}") from None
            except (ValueError, IndexError, KeyError) as e:
                raise ModuleError(f"{rel}:{n}: cannot read {s[:80]!r}: {e}") from None

    def arch_line(self, word, rest):
        A = self.arch
        pos, notes = _split(rest)
        if word == "let":
            A[pos[0]] = _unval(pos[1])
        elif word == "place":
            A["leaves"][_unval(pos[0])] = [_unval(pos[1])] + [_unval(x) for x in pos[2:]]
            self.places[_unval(pos[0])] = _unval(pos[1])
        elif word == "road":
            if "raw" in notes:
                A["nets"][_unval(pos[0])] = notes["raw"]
            else:
                a, ap = _unend(pos[1])
                b, bp = _unend(pos[3])
                if not pos[4].startswith("chan"):
                    raise ModuleError(f"a road's gadget is chan<L>, not {pos[4]!r}")
                A["nets"][_unval(pos[0])] = [a, ap, b, bp, int(pos[4][4:])]
        elif word == "wire":
            a, ap = _unend(pos[1])
            b, bp = _unend(pos[3])
            A["wires"][_unval(pos[0])] = [a, ap, b, bp, _num(pos[4])]
        elif word == "order":
            A["ion_order"][_unval(pos[0])] = [_unval(x) for x in pos[1:]]
        elif word == "channel":
            A["channels"][_unval(pos[0])] = notes
        elif word == "control":
            A["control"] = notes
        else:
            raise ModuleError(f"{word!r} is not a line of an architecture")

    def names(self, body) -> _Names:
        key = (id(body), len(body["blocks"]))
        if self._names_key != key:
            self._names, self._names_key = _Names(body["blocks"]), key
        return self._names

    def body_line(self, body, word, rest, ins_id, resolve, module, rel, n):
        if word == "let":
            k, _, v = rest.partition(" ")
            body[k] = _unval(v)
            return ins_id
        if word == "source":
            body["program"]["source"] = resolve(_unval(rest))
            return ins_id
        pos, notes = _split(rest)
        if word == "about":
            body["program"].update(notes)
        elif word == "block":
            b = {"ions": _PLAIN.unions(pos[1])}
            b.update(notes)
            body["blocks"][_unval(pos[0])] = b
        elif word == "use":
            body["uses"][_unval(pos[0])] = [_num(pos[1]), _num(pos[2])]
        elif word == "ins":
            d = {"id": _unval(pos[0]), "line": _unval(pos[1]), "text": _unval(pos[2])}
            d.update(notes)
            body["program"]["instructions"].append(d)
            self.lines[(module, "ins", d["id"])] = (rel, n)
            return d["id"]
        elif word == "rows":
            return _unval(pos[0])
        elif word == "run":
            eid, t0, t1, gadget, place = int(pos[0]), _num(pos[1]), _num(pos[2]), _unval(pos[3]), pos[4][1:]
            master = self.places.get(place)
            if master:
                if not gadget.startswith(master + "."):
                    raise ModuleError(f"{place} is a {master}; it cannot run {gadget}")
                op = gadget[len(master) + 1:]
            else:
                op = gadget
            row = [eid, t0, t1, place, op, notes.get("x", 1), ins_id, notes.get("grp", f"i{ins_id}"),
                   self.names(body).unbind(pos[5:]), notes.get("warp", [])]
            if "guard" in notes:
                row.append(notes["guard"])
            body["events"].append(row)
            self.lines[(module, "run", eid)] = (rel, n)
        elif word == "carry":
            cid, t0, gadget, net = int(pos[0]), _num(pos[1]), pos[2], _unval(pos[3])
            leaf, port = _unend(pos[4])
            road = self.arch.get("nets", {}).get(net)
            if gadget != "-" and road is not None and len(road) == 5 and gadget != f"chan{road[4]}.carry":
                raise ModuleError(f"{net} is chan{road[4]}; a carry on it is chan{road[4]}.carry, not {gadget}")
            body["carries"].append([cid, net, leaf, port, t0, notes["dep"], notes["transit"], self.names(body).unions(pos[5]),
                                    notes["role"], ins_id, notes.get("grp", f"i{ins_id}"), notes["last"],
                                    notes["taken"]])
            self.lines[(module, "carry", cid)] = (rel, n)
        elif word == "msg":
            mid = int(pos[0])
            a, ap = _unend(pos[4])
            b, bp = _unend(pos[6])
            body["messages"].append([mid, _unval(pos[3]), a, ap, b, bp, _num(pos[1]), _num(pos[2]),
                                     _unval(pos[7]), _unval(pos[8]), ins_id, notes.get("grp", f"i{ins_id}"),
                                     notes["label"], notes["src"]])
            self.lines[(module, "msg", mid)] = (rel, n)
        elif word == "call":
            c = {"id": _unval(pos[0]), "m": _unval(pos[2]), "t0": _num(pos[1]),
                 "args": [_unval(x) for x in pos[4:]], "ins": ins_id, "gate": _unval(pos[3])}
            c.update(notes)
            body["calls"].append(c)
            self.lines[(module, "call", int(c["id"][1:]))] = (rel, n)
        elif word == "hold":
            k = int(pos[0])
            place = pos[5][1:]
            master = self.places.get(place)
            if master and pos[4] != f"{master}.refresh":
                raise ModuleError(f"{place} is a {master}; a hold there is {master}.refresh")
            h = {"block": _unval(pos[6]), "place": place, "from": _num(pos[1]), "every": _num(pos[2]),
                 "n": int(pos[3][1:])}
            h.update(notes)
            h["ins"] = ins_id
            h["bind"] = self.names(body).unbind(pos[7:])
            body["holds"].append((k, h))
            self.lines[(module, "hold", k)] = (rel, n)
        elif word in _RECORDS:
            body[word + "s"].append(notes)
        elif word == "refused":
            body["refused"].append([_unval(pos[0]), _unval(pos[1])])
        else:
            raise ModuleError(f"{word!r} is not a line of a gadget")
        return ins_id

    def result(self) -> Modules:
        if self.root is None:
            raise ModuleError("no program module")
        missing = [m for m in self.uses if m not in self.macros]
        if missing:
            raise ModuleError(f"the program uses gadgets no module defines: {', '.join(map(str, missing[:5]))}")
        g = {"schema": GIR_FOLDED}
        g.update(self.arch)
        root = self.root
        for b in [root] + list(self.macros.values()):
            b["events"].sort(key=lambda e: e[0])
            b["carries"].sort(key=lambda c: c[0])
            b["messages"].sort(key=lambda m: m[0])
            b["calls"].sort(key=lambda c: int(c["id"][1:]))
            # the holds were numbered where they were written
            b["holds"] = [h for _, h in sorted(b["holds"], key=lambda kh: kh[0])]
            for c in b["calls"]:
                if c["m"] not in self.macros:
                    raise ModuleError(f"call {c['id']} names gadget {c['m']}, which no module defines")
        g.update(root)
        g["macros"] = self.macros
        leaves = {}
        for key, rows in self.leaf_text.items():
            if key[0] == "source":
                continue
            if self.leaves.get(key) is None:
                leaves[key] = None
            else:
                leaves[key] = parse_program("\n".join(rows) + "\n")
        sources = {k[1]: v[0] for k, v in self.leaf_text.items() if k[0] == "source"}
        return Modules(gir=g, leaves=leaves, sources=sources, lines=self.lines)


def parse_modules(texts, resolve=lambda name: "") -> Modules:
    """Read modules from `texts`: one text, or `[(file name, text), ...]`.  `resolve(name)`
    returns the text of a `source` the program names."""
    r = _Reader()
    if isinstance(texts, str):
        texts = [("modules.tsir", texts)]
    for rel, text in texts:
        r.feed(text, rel, resolve)
    return r.result()


def read_modules(path) -> Modules:
    """Read a directory `write_modules` wrote (or one file of modules)."""
    p = Path(path)
    if p.is_file():
        return parse_modules([(p.name, p.read_text(encoding="utf-8"))],
                             resolve=lambda n: (p.parent / n).read_text(encoding="utf-8"))
    files = [("program.tsir", (p / "program.tsir").read_text(encoding="utf-8"))]
    for sub in ("gadget", "leaf"):
        for f in sorted((p / sub).glob("*.tsir")):
            files.append((f"{sub}/{f.name}", f.read_text(encoding="utf-8")))
    return parse_modules(files, resolve=lambda n: (p / n).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- checking modules


def check_modules(path, *, cache="out/gadgets/cache", floorplan: str | None = None, log=None,
                  expand_limit: int = 30_000) -> dict:
    """Read modules and check what was read: every leaf module is the library's characterized
    op (the hardware a row names is the hardware that was verified), and the place checks
    hold on the schedule the modules say, per view (and on the whole expansion when it is
    small enough).  What it cannot re-derive from text it says: a gadget's footprint is
    written as a hull per resource (`use`), so the composition check is the scheduler's."""
    import re as _re
    from .floorplan import FLOORPLANS_DIR, load_file
    from .fold import check_folded, expand
    from .library import LeafLibrary
    from .place_checks import check_places
    m = read_modules(path)
    leaves = LeafLibrary(cache, log=log)
    if floorplan is None:
        hit = _re.search(r'^floorplan\s+"?([\w.-]+)"?', m.gir["program"].get("source") or "", _re.M)
        floorplan = hit.group(1) if hit else "fullstack"
    lib, _ = load_file(FLOORPLANS_DIR / f"{floorplan}.floor", leaves)
    differ = []
    for (master, op), prog in m.leaves.items():
        want = _leaf_program(leaves, master, op)
        if (want is None) != (prog is None) or (prog is not None and prog.to_json() != want.to_json()):
            differ.append(f"{master}.{op}")
    checks = check_folded(m.gir, lib, leaves, log=log)
    n = m.gir.get("folded", {}).get("instructions", 0)
    whole = None
    if n <= expand_limit:
        whole = check_places(expand(m.gir), lib, leaves)
    return {"floorplan": floorplan, "gadgets": len(m.gir["macros"]), "rows": len(m.lines),
            "leaf_gadgets": len(m.leaves), "classical": sorted(f"{a}.{b}" for (a, b), p in m.leaves.items() if p is None),
            "leaves_differ": differ, "per_view": {"passed": checks["passed"], "failed": checks["failed"]},
            "expanded": ({"passed": whole["passed"], "failed": whole["failed"]} if whole else None),
            "violations": {k: v[:3] for k, v in checks["violations"].items() if v},
            "composition": "not re-derived from text: a gadget's footprint is written as one hull per "
                           "resource; the scheduler checked it when the run was built",
            "passed": not differ and not checks["failed"] and not (whole and whole["failed"])}
