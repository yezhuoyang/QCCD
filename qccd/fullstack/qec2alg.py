"""QCCD's own lowering: a FormalRV `.qec` program -> a gadget program (`.alg`) on the town.

FormalRV's `.qec` says what happens to code blocks: allocate, prepare a magic state, merge and
split (a Pauli-product measurement), extract, decode, apply a Pauli frame -- inside loops,
gadgets and guards.  The gadget library says how a trapped-ion machine does each of those, at
verified places.  This module is the bridge, and it is QCCD's (FormalRV is input only).

    .qec statement                                  .alg                              place
    alloc LogicalAncilla[j] + prepare |Y+> -> it    prep aJ Y                         pharmacy (inject.Y)
    N magic |T> -> LogicalAncilla[j]                inject aJ ; x aJ                  factory + pharmacy
    alloc / prepare |+> / checks of a merge         (the bench's own probe ion)
    mergedK M = merge P with ... -> c[lo,hi)        ppm P -> mK                       Pauli-product bench
      ... single factor, block never used again     read B P -> mK                    post office
    extract M rounds=r                              (the bench's own rounds)
    decode ... / decode ...                         decode <the merge's blocks>       decoder
    ... = split M                                   (the bench lets the blocks go)
    frame on c[lo,hi) P                             if mK: <p> <block>  per factor    archive
    frame P                                         <p> <block>        per factor    archive
    for 0 <= v < E: ...                             unrolled (E evaluated)
    g(args, families)  (a gadget call)              gate g <blocks>: ... ; g <blocks>   -- one gate per
                                                      distinct body, so names and nesting survive
    when c[lo,hi): ...                              if mK: <Pauli> ...  or  if mK: <gate> <blocks>
    LogicData[i] (an input of the program)          prep qI Z  (marked input)         school

Indices are evaluated with the listing language's natural arithmetic (`sysprog.evaluate`).
Every `.alg` line carries the `.qec` statement it implements (`Lowered.qec_of`, by `.alg`
file line; an instruction expanded from a gate body has its body line as `Instr.src_line`).
A statement this lowering does not know is refused by name (`NotLowered`), never dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import layers, provenance
from .sysprog import ListingError, evaluate, split_top


class NotLowered(ValueError):
    pass


@dataclass
class Lowered:
    text: str                              # the .alg program
    lines: list                            # [(alg text, qec line or 0, role)], body only (no gates)
    qec_of: dict = field(default_factory=dict)       # alg file line -> qec line
    role_of: dict = field(default_factory=dict)      # alg file line -> 'input' | 'qec' | 'gate' | 'header'
    linked: dict = field(default_factory=dict)       # qec line -> [alg lines] (incl. absorbed)
    absorbed: dict = field(default_factory=dict)     # qec line -> why no instruction of its own
    blocks: dict = field(default_factory=dict)       # .alg block -> .qec block
    outcomes: dict = field(default_factory=dict)     # .alg outcome -> qec record band
    gates: dict = field(default_factory=dict)        # gate name -> (gadget, qec call line, n params)


@dataclass
class Item:
    kind: str                              # 'ins' | 'call' | 'guard'
    text: str = ""                         # 'ins': the .alg text; 'call': the gadget; 'guard': outcome
    qline: int = 0
    children: list = field(default_factory=list)
    role: str = "qec"
    #: a single-factor measurement this lowering may turn into a destructive readout
    single: tuple | None = None            # (letter, block, outcome)


_FACTOR = re.compile(r"([XYZ])\[(\w+)\[([^\]]+)\]\[0\]\]")
_ARITH = re.compile(r"^[\w\s+\-*/%^()·]+$")


class _Interp:
    def __init__(self, qec: layers.Stage):
        self.qec = qec
        self.gadgets = qec.gadgets()
        self.gen: dict = {}                # LogicalAncilla index -> its current generation
        self.k = 0                         # outcomes named so far
        self.out = Lowered(text="", lines=[])

    # ---- expressions and statements -----------------------------------------------------
    def ev(self, text: str, env: dict, line: int) -> int:
        try:
            return evaluate(text.replace("·", "*"), env, line)
        except ListingError as e:
            raise NotLowered(f".qec line {line}: {e}") from None

    def inst(self, text: str, env: dict, line: int) -> str:
        """Evaluate every bracketed index of `text` (innermost first)."""
        def one(m):
            inner, close = m.group(1), m.group(2)
            parts = split_top(inner, ",")
            if not all(_ARITH.match(p) for p in parts):
                return m.group(0)
            idents = {t for p in parts for t in re.findall(r"[A-Za-z_]\w*", p)}
            if not idents <= set(env):
                return m.group(0)
            return "[" + ",".join(str(self.ev(p, env, line)) for p in parts) + close
        prev = None
        while prev != text:
            prev = text
            text = re.sub(r"\[([^\[\]]*)(\]|\))", one, text)
        return text

    def block(self, fam: str, idx: int, famap: dict, new: bool = False) -> str:
        fam = famap.get(fam, fam)
        if fam == "LogicData":
            return f"q{idx}"
        if fam == "LogicalAncilla":
            if new:
                self.gen[idx] = self.gen.get(idx, -1) + 1
            g = self.gen.get(idx, 0)
            name = f"a{idx}" + (f"_{g}" if g else "")
            self.out.blocks[name] = f"LogicalAncilla[{idx}]"
            return name
        raise NotLowered(f"no block family {fam!r}")

    def factors(self, text: str, famap: dict) -> list:
        return [(p, self.block(fam, int(idx), famap)) for p, fam, idx in _FACTOR.findall(text)]

    # ---- walking ----------------------------------------------------------------------
    def walk(self, body: list, env: dict, famap: dict, scope: list) -> list:
        items: list[Item] = []
        current = None                     # the merge whose group we are in: (outcome, blocks)
        for s in body:
            raw = s.text.strip()
            if raw == "skip":
                continue
            loop = re.fullmatch(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?):", raw)
            if loop:
                lo, hi = self.ev(loop[1], env, s.line), self.ev(loop[3], env, s.line)
                got = []
                for v in range(lo, hi):
                    got += self.walk(s.children, {**env, loop[2]: v}, famap, scope)
                items += got
                if s.line not in self.out.absorbed:
                    empty = all(c.text.strip() == "skip" for c in s.children)
                    self.absorb(s, "an empty branch: nothing runs" if empty else
                                f"a loop, unrolled: {hi - lo} trip{'s' if hi - lo != 1 else ''} of the lines below")
                continue
            if raw.startswith("when ") and raw.endswith(":"):
                band = self.band(self.inst(raw, env, s.line), "c")
                name = self.owner(band, scope, s.line)
                items.append(Item("guard", name, s.line, self.walk(s.children, env, famap, scope)))
                self.absorb(s, f"a guard: the lines below run when {name} is 1")
                continue
            t = self.inst(raw, env, s.line)
            call = re.fullmatch(r"(\w+)\((.*)\)", t)
            if call and call[1] in self.gadgets:
                items.append(self.call(call[1], split_top(call[2], ","), env, famap, s.line))
                continue
            kd = provenance._qec_kind(s)
            if kd == "alloc_block":
                self.absorb(s, "a block is allocated where it is prepared")
            elif kd == "magic" or re.match(r"\d+\s+magic\s+\|", t):
                m = re.search(r"(\|[^>]+>)\s*->\s*(\w+)\[(\d+),\d+\)", t)
                if not m:
                    raise NotLowered(f".qec line {s.line}: {raw!r}")
                b = self.block(m[2], int(m[3]), famap, new=True)
                if m[1] == "|Y+>":
                    items.append(Item("ins", f"prep {b} Y", s.line))
                elif m[1] == "|T>":
                    # the factory makes T-dagger|+> (its verified output); X in software makes it T|+>
                    items.append(Item("ins", f"inject {b}", s.line))
                    items.append(Item("ins", f"x {b}", s.line))
                else:
                    raise NotLowered(f".qec line {s.line}: no gadget prepares {m[1]} yet")
            elif kd in ("alloc", "prepare"):
                self.absorb(s, "the merge's ancillas and their |+> are the bench's own probe ion")
            elif kd == "merge":
                fs = self.factors(t.split("= merge", 1)[1].split(" with ")[0], famap)
                band = self.band(t.split("->")[-1], "c")
                name = f"m{self.k}"
                self.k += 1
                scope[-1].append((band, name))
                self.out.outcomes[name] = band
                txt = "ppm " + " ".join(f"{p} {b}" for p, b in fs) + f" -> {name}"
                items.append(Item("ins", txt, s.line, single=(fs[0][0], fs[0][1], name) if len(fs) == 1 else None))
                current = (name, [b for _, b in fs], s.line)
            elif kd == "extract":
                self.absorb(s, "the merged patch's rounds are the bench's own rounds")
            elif kd == "decode":
                if current is None:
                    raise NotLowered(f".qec line {s.line}: a decode outside a merge group")
                if current[2] is not None:
                    items.append(Item("ins", "decode " + " ".join(current[1]), s.line))
                    current = (current[0], current[1], None)
                else:
                    self.absorb(s, "both decodes of a merge are one call of the decoder")
            elif kd == "split":
                self.absorb(s, "the bench lets the blocks go when it has measured")
            elif kd == "frame_on":
                name = self.owner(self.band(t, "c"), scope, s.line)
                for p, b in self.factors(t, famap):
                    items.append(Item("ins", f"if {name}: {p.lower()} {b}", s.line))
            elif kd == "frame":
                for p, b in self.factors(t, famap):
                    items.append(Item("ins", f"{p.lower()} {b}", s.line))
            elif t.startswith("ppmrot"):
                raise NotLowered(f".qec line {s.line}: rotation synthesis (ppmrot) has no gadget yet")
            else:
                raise NotLowered(f".qec line {s.line}: no gadget lowering for {raw[:100]!r}")
        return items

    def call(self, name: str, args: list, env: dict, famap: dict, line: int) -> Item:
        g = self.gadgets[name]
        m = re.match(r"gadget\s+\w+\[.*?\]\((.*)\):\s*$", g.header.text)
        formals = split_top(m[1], ",") if m else []
        if len(formals) != len(args):
            raise NotLowered(f".qec line {line}: {name} takes {len(formals)} arguments, given {len(args)}")
        env2, famap2 = {}, {}
        for f, a in zip(formals, args):
            parts = f.split()
            if len(parts) == 2:                # `Surface[2] LogicData`: a block family
                famap2[parts[1]] = famap.get(a.strip(), a.strip())
            else:
                env2[f.strip()] = self.ev(a, env, line)
        return Item("call", name, line, self.walk(g.body, env2, famap2, [[]]))

    def band(self, text: str, reg: str):
        m = re.search(rf"\b{reg}\[(\d+),(\d+)\)", text)
        if not m:
            raise NotLowered(f"no {reg}[..) band in {text!r}")
        return (int(m[1]), int(m[2]))

    def owner(self, band, scope, line) -> str:
        for frame in reversed(scope):
            for (lo, hi), name in frame:
                if lo <= band[0] and band[1] <= hi:
                    return name
        raise NotLowered(f".qec line {line}: no merge in scope wrote c{list(band)}")

    def absorb(self, s, why: str) -> None:
        self.out.absorbed[s.line] = why


# ----------------------------------------------------------------------------- writing .alg

_NAME = re.compile(r"\b([qa]\d+(?:_\d+)?|m\d+)\b")


def _flatten(items):
    for it in items:
        if it.kind == "ins":
            yield it
        else:
            yield from _flatten(it.children)


def _mark_reads(items) -> None:
    """A single-factor measurement of a block nothing names afterwards is a readout."""
    flat = list(_flatten(items))
    for i, it in enumerate(flat):
        if it.single:
            p, b, name = it.single
            # a decode reads the block's syndromes, not its ions: a read-out block may be decoded
            if not any(re.search(rf"\b{re.escape(b)}\b", later.text) for later in flat[i + 1:]
                       if not later.text.startswith("decode")):
                it.text = f"read {b} {p} -> {name}"


class _Writer:
    def __init__(self, out: Lowered):
        self.out = out
        self.defs: list = []               # [(gate name, [(line text, qline)])], definition order
        self.bodies: dict = {}             # canonical body -> gate name
        self.count: dict = {}

    def lines(self, items) -> list:
        """[(text, qline)] for `items`, gates defined as they are met (inner ones first)."""
        out = []
        for it in items:
            if it.kind == "ins":
                out.append((it.text, it.qline))
            elif it.kind == "call":
                out += self.gate_call(it.text, self.lines(it.children), it.qline, guard=None)
            elif it.kind == "guard":
                body = self.lines(it.children)
                if all(re.match(r"(if \w+: )?[xyz] \w+$", t) for t, _ in body):
                    for t, q in body:
                        out.append((self._guard(it.text, t), q))
                else:
                    out += self.gate_call(f"when_{it.text}", body, it.qline, guard=it.text)
        return out

    @staticmethod
    def _guard(name: str, text: str) -> str:
        m = re.match(r"if (.+?): (.*)$", text)
        return f"if {name} & {m[1]}: {m[2]}" if m else f"if {name}: {text}"

    def gate_call(self, gadget: str, body: list, qline: int, guard) -> list:
        created, defined, used = set(), set(), []
        for t, _ in body:
            w = t.split(":")[-1].split() if t.startswith("if ") else t.split()
            if w and w[0] in ("prep", "inject") and len(w) > 1:
                created.add(w[1])
            m = re.search(r"->\s*(\w+)", t)
            if m:
                defined.add(m[1])
            for n in _NAME.findall(t):
                if n not in used:
                    used.append(n)
        params = [n for n in used if not n.startswith("m") and n not in created]
        foreign = [n for n in used if n.startswith("m") and n not in defined]
        guard_names = re.findall(r"\b(m\d+)\b", guard or "")
        if not params or (foreign and not set(foreign) <= set(guard_names)):
            # no block to pass, or the body reads an outcome measured outside it: inline
            return [(self._guard(guard, t) if guard else t, q) for t, q in body]
        ren = {p: f"p{i}" for i, p in enumerate(params)}
        loc = [n for n in used if n not in ren]
        ren.update({n: (f"o{i}" if n.startswith("m") else f"l{i}") for i, n in enumerate(loc)})
        canon = tuple(_NAME.sub(lambda m: ren.get(m[1], m[1]), t) for t, _ in body)
        name = self.bodies.get((gadget, canon))
        if name is None:
            k = self.count.get(gadget, 0)
            self.count[gadget] = k + 1
            name = f"{gadget.lower()}" + (f"_{k + 1}" if k else "")
            self.bodies[(gadget, canon)] = name
            self.defs.append((name, [f"p{i}" for i in range(len(params))],
                              [(c, q) for c, (_, q) in zip(canon, body)]))
            self.out.gates[name] = (gadget, qline, len(params))
        call = f"{name} " + " ".join(params)
        return [(f"if {guard}: {call}" if guard else call, qline)]


def lower(qec: layers.Stage, *, title: str = "", floorplan: str = "fullstack",
          inputs: str = "Z") -> Lowered:
    prog = qec.program
    if prog is None:
        raise NotLowered("the .qec has no program")
    it = _Interp(qec)
    m = re.search(r"\w+\[(\d+)\]\s+LogicData", prog.header.text)
    n_data = int(m[1]) if m else 0
    items = [Item("ins", f"prep q{i} {inputs}", 0, role="input") for i in range(n_data)]
    for i in range(n_data):
        it.out.blocks[f"q{i}"] = f"LogicData[{i}]"
    items += it.walk(prog.body, {}, {}, [[]])
    _mark_reads(items)
    w = _Writer(it.out)
    body = w.lines(items)
    out = it.out
    head = [f"title {title or 'a FormalRV program on the gadget town'}", f'floorplan "{floorplan}"']
    text_lines: list = []
    roles: list = []

    def add(t, q, role):
        text_lines.append(t)
        roles.append((q, role))

    for t in head:
        add(t, 0, "header")
    for name, params, gbody in w.defs:
        gadget, cline, _ = out.gates[name]
        add(f"gate {name} {' '.join(params)}:", cline, "gate")
        for t, q in gbody:
            add("  " + t, q, "gate")
    inputs_set = {f"prep q{i} {inputs}" for i in range(n_data)}
    for t, q in body:
        role = "input" if (q == 0 and t in inputs_set) else "qec"
        add(t, q, role)
        out.lines.append((t, q, role))
    out.text = "\n".join(text_lines) + "\n"
    for n, (q, role) in enumerate(roles, start=1):
        out.role_of[n] = role
        if q:
            out.qec_of[n] = q
            out.linked.setdefault(q, []).append(n)
    # an absorbed statement is linked to the instruction its group became: the nearest
    # emitted line of the same template before it (its merge, or the decode above it)
    emitted = sorted({q: n for n, q in out.qec_of.items()}.items())
    for ql in out.absorbed:
        before = [n for q, n in emitted if q <= ql]
        after = [n for q, n in emitted if q >= ql]
        forward = out.absorbed[ql].startswith(("the merge's", "a block", "a loop", "a guard"))
        target = (after[0] if forward and after
                  else before[-1] if before else after[0] if after else None)
        if target is not None:
            out.linked.setdefault(ql, []).append(target)
    return out
