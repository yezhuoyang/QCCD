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
    #: a call lowered once for every call of the same shape: the shape (see `_Interp.call`)
    memo: tuple | None = None


_FACTOR = re.compile(r"([XYZ])\[(\w+)\[([^\]]+)\]\[0\]\]")
_ARITH = re.compile(r"^[\w\s+\-*/%^()·]+$")

#: the arguments FormalRV's `.qec` gadgets take only to place a call's own scratch -- where its
#: ancillas, logical ancillas, syndromes and records start.  Every other argument (the data
#: base, the gadget's own parameters, the block families) decides what the body does, so a
#: call is lowered once per value of those, and every call of that shape reuses it.
OFFSETS = ("rv_ancillabase", "rv_logicalancillabase", "rv_synbase", "rv_recbase")


#: a rotation's resource blocks are `a<ROT_BASE + i>`: a name the gates read as a block this
#: body makes, and no logical ancilla of FormalRV's has
ROT_BASE = 9_000_000

#: FormalRV lays out a rotation's synthesis stub by tables it leaves open (`tSynth`,
#: `synthBits`, `synthAnc`: how many magic states, records and ancillas a rotation takes).
#: QCCD's rotation (`_Interp.rotation`) names its own outcomes and blocks, so those offsets
#: only have to keep FormalRV's bands apart -- read as 0, they do (a stub's band is empty).
_TABLES = re.compile(r"\b(?:synthBits|synthAnc)\[[^\[\]]*\]")


def _untable(text: str) -> str:
    out, i = [], 0
    while True:
        j = text.find("tablemax(", i)
        if j < 0:
            out.append(text[i:])
            break
        depth, k = 0, j + len("tablemax")
        while k < len(text):
            depth += {"(": 1, ")": -1}.get(text[k], 0)
            k += 1
            if depth == 0:
                break
        out.append(text[i:j] + "0")
        i = k
    return _TABLES.sub("0", "".join(out))


class _NeedsExact(Exception):
    """A call's body names a logical ancilla it did not prepare: its offset is not scratch."""


class _Interp:
    def __init__(self, qec: layers.Stage):
        self.qec = qec
        self.gadgets = qec.gadgets()
        self.gen: dict = {}                # LogicalAncilla index -> its current generation
        self.k = 0                         # outcomes named so far
        self.out = Lowered(text="", lines=[])
        #: a call's shape -> its lowered body, shared by every call of that shape
        self.memo: dict = {}
        #: the logical ancillas each call being lowered once has prepared, innermost last
        self.made: list = []
        #: calls lowered, and calls that reused a body lowered before
        self.calls = self.reused = 0
        #: the rotations' own resource blocks, named apart from FormalRV's logical ancillas
        self.rot = 0

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
            if self.made:
                # lowered once for every call of its shape: the body may only name the
                # ancillas it prepares itself, whose indices are relative to its own offset
                if new:
                    self.made[-1].add(idx)
                elif idx not in self.made[-1]:
                    raise _NeedsExact(idx)
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
            raw = _untable(s.text.strip())
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
            call = re.fullmatch(r"([\w!]+)\((.*)\)", t)
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
                m = re.match(r"ppmrot\((-?)\s*pi\s*/\s*2\^.*?\s+with\s+tSynth\[([^\]]+)\]", t)
                if not m:
                    raise NotLowered(f".qec line {s.line}: a rotation QCCD cannot read: {raw[:100]!r}")
                fs = self.factors(t.split(" with ")[0], famap)
                if not fs or any(p not in "XZ" for p, _ in fs):
                    raise NotLowered(f".qec line {s.line}: a rotation of {fs}: the bench measures X̄ and Z̄")
                items += self.rotation(fs, self.ev(m[2], env, s.line), -1 if m[1] else 1, s.line)
            else:
                raise NotLowered(f".qec line {s.line}: no gadget lowering for {raw[:100]!r}")
        return items

    def rotation(self, fs: list, k: int, sign: int, line: int) -> list:
        """FormalRV's synthesis stub `ppmrot(±π/2^k) P`, as QCCD does it: exp(∓iπ/2^k P̄).

        Measure P̄ ⊗ Z̄ against a block in RZ(2φ)|+⟩ (φ = ±π/2^k) and read that block in X:
        the blocks have turned by exp(-iφP̄) up to P̄^r (a frame) when the product reads 0, and
        by exp(+iφP̄) when it reads 1 -- which the doubled angle, the same rotation at k − 1,
        puts right.  Repeat until success.  The resource is a |Ȳ⟩ at k = 2, the factory's T
        state at k = 3, a rotation source's RZ(k−1)|+⟩ beyond; k = 1 is the Pauli P̄ itself, a
        frame.  Exact in every branch (each sign, angle and outcome checked against
        exp(-iφP̄) as matrices before this was written), so the program's distribution and
        states are checked against the .prog's like everything else."""
        letters = " ".join(f"{p} {b}" for p, b in fs)
        blocks = " ".join(b for _, b in fs)

        def frame(guard=None):
            return [Item("ins", (f"if {guard}: " if guard else "") + f"{p.lower()} {b}", line)
                    for p, b in fs]
        if k <= 1:
            return frame()
        self.rot += 1
        a = f"a{ROT_BASE + self.rot}"
        if k == 2:
            res = [Item("ins", f"prep {a} Y", line)] + ([] if sign > 0 else [Item("ins", f"z {a}", line)])
        elif k == 3:
            # the factory's T†|+⟩; X in software makes it T|+⟩
            res = [Item("ins", f"inject {a}", line)] + ([Item("ins", f"x {a}", line)] if sign > 0 else [])
        else:
            res = [Item("ins", f"prep {a} R{k - 1}" + ("" if sign > 0 else "dg"), line)]
        s_, r_ = f"m{self.k}", f"m{self.k + 1}"
        self.k += 2
        return (res
                + [Item("ins", f"ppm {letters} Z {a} -> {s_}", line),
                   Item("ins", f"decode {blocks} {a}", line),
                   Item("ins", f"read {a} X -> {r_}", line),
                   Item("ins", f"decode {a}", line)]
                + frame(r_)
                + [Item("guard", s_, line, self.rotation(fs, k - 1, sign, line))])

    def call(self, name: str, args: list, env: dict, famap: dict, line: int) -> Item:
        """A gadget call.  Its body is lowered once per *shape* -- the gadget, the values of
        every argument but the scratch offsets (`OFFSETS`), and the block families -- with the
        offsets at zero, and every call of that shape shares it: a loop of a thousand adders
        is one adder, lowered once.  If the body names a logical ancilla it did not prepare,
        the offset was not scratch after all, and that call is lowered exactly."""
        g = self.gadgets[name]
        m = re.match(r"gadget\s+[\w!]+\[.*?\]\((.*)\):\s*$", g.header.text)
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
        self.calls += 1
        shape = (name, tuple(sorted((k, v) for k, v in env2.items() if k not in OFFSETS)),
                 tuple(sorted(famap2.items())))
        if self.memo.get(shape) is not None:
            self.reused += 1
            return Item("call", name, line, self.memo[shape], memo=shape)
        if shape not in self.memo and any(k in OFFSETS for k in env2):
            self.made.append(set())
            saved_gen, self.gen = self.gen, {}
            try:
                body = self.walk(g.body, {**env2, **{k: 0 for k in env2 if k in OFFSETS}}, famap2, [[]])
            except _NeedsExact:
                self.memo[shape] = None
            else:
                self.memo[shape] = body
                return Item("call", name, line, body, memo=shape)
            finally:
                self.made.pop()
                self.gen = saved_gen
        if self.made:
            # lowered exactly, inside a body lowered once: it may name that body's ancillas
            # and its own, and nothing else
            self.made.append(set(self.made[-1]))
            try:
                return Item("call", name, line, self.walk(g.body, env2, famap2, [[]]))
            finally:
                self.made.pop()
        return Item("call", name, line, self.walk(g.body, env2, famap2, [[]]))

    def band(self, text: str, reg: str):
        m = re.search(rf"\b{reg}\[(\d+),(\d+)\)", text)
        if not m and "tablemax(" in text:
            raise NotLowered("this gadget is laid out by FormalRV's rotation-synthesis tables "
                             "(tablemax(synthBits, ...)): it synthesizes a rotation (ppmrot), "
                             "which has no gadget yet")
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


def _seq(items):
    """`items` in order, through guards but not into calls: one body's own lines."""
    for it in items:
        if it.kind == "guard":
            yield from _seq(it.children)
        else:
            yield it


def _blocks_of(text: str) -> set:
    # a decode reads the block's syndromes, not its ions: a read-out block may be decoded
    return set() if text.startswith("decode") else set(re.findall(r"\b[qa]\d+(?:_\d+)?\b", text))


class _Reads:
    """A single-factor measurement of a block nothing names afterwards is a readout.

    Decided body by body, in one pass from the end: at the program's own level a call counts
    as naming the blocks it is passed; inside a call's body only a block the body prepared
    itself can be read out, since what was passed in may be named after the call returns.  A
    body shared by every call of its shape is decided once."""

    def __init__(self):
        self.done: set = set()
        self.uses: dict = {}

    def passed(self, it) -> set:
        """The blocks a call names that it did not prepare (its parameters)."""
        key = id(it.children)
        if key not in self.uses:
            made, named = set(), set()
            for x in _flatten(it.children):
                w = x.text.split(":")[-1].split() if x.text.startswith("if ") else x.text.split()
                if w and w[0] in ("prep", "inject") and len(w) > 1:
                    made.add(w[1])
                named |= set(re.findall(r"\b[qa]\d+(?:_\d+)?\b", x.text))
            self.uses[key] = named - made
        return self.uses[key]

    def body(self, items, top: bool) -> None:
        if id(items) in self.done:
            return
        self.done.add(id(items))
        seq = list(_seq(items))
        made = set()
        if not top:
            for x in seq:
                w = x.text.split(":")[-1].split() if x.text.startswith("if ") else x.text.split()
                if x.kind == "ins" and w and w[0] in ("prep", "inject") and len(w) > 1:
                    made.add(w[1])
        later: set = set()
        for it in reversed(seq):
            if it.kind == "call":
                self.body(it.children, top=False)
                later |= self.passed(it)
                continue
            if it.single:
                p, b, name = it.single
                if b not in later and (top or b in made):
                    it.text = f"read {b} {p} -> {name}"
            later |= _blocks_of(it.text)


def _mark_reads(items) -> None:
    _Reads().body(items, top=True)


class _Writer:
    def __init__(self, out: Lowered, fresh: int = 0):
        self.out = out
        self.defs: list = []               # [(gate name, [(line text, qline)])], definition order
        self.bodies: dict = {}             # canonical body -> gate name
        self.count: dict = {}
        #: a shared body -> what its calls are written as (the gate call, or its lines)
        self.shared: dict = {}
        self.fresh = fresh                 # names for the locals of a shared body written inline

    def lines(self, items) -> list:
        """[(text, qline)] for `items`, gates defined as they are met (inner ones first)."""
        out = []
        for it in items:
            if it.kind == "ins":
                out.append((it.text, it.qline))
            elif it.kind == "call" and it.memo is not None:
                key = id(it.children)
                if key not in self.shared:
                    self.shared[key] = self.gate_call(it.text, self.lines(it.children), it.qline, guard=None)
                got = self.shared[key]
                if len(got) == 1 and got[0][0].split()[0] in self.out.gates:
                    out.append((got[0][0], it.qline))
                else:
                    out += self.renamed(got)
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

    def renamed(self, lines) -> list:
        """A shared body that touches no block of its caller, written out once more: every
        block and outcome it names gets a fresh name, so no two calls share one."""
        ren: dict = {}

        def one(m):
            n = m[1]
            if n not in ren:
                self.fresh += 1
                ren[n] = f"{'m' if n.startswith('m') else 'a'}{self.fresh}"
            return ren[n]
        return [(_NAME.sub(one, t), q) for t, q in lines]

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
            # FormalRV names a controlled gadget `!ctl!g`; an .alg gate name is a word
            name = re.sub(r"\W+", "_", gadget).strip("_").lower() + (f"_{k + 1}" if k else "")
            self.bodies[(gadget, canon)] = name
            self.defs.append((name, [f"p{i}" for i in range(len(params))],
                              [(c, q) for c, (_, q) in zip(canon, body)]))
            self.out.gates[name] = (gadget, qline, len(params))
        call = f"{name} " + " ".join(params)
        return [(f"if {guard}: {call}" if guard else call, qline)]


def lower(qec: layers.Stage, *, title: str = "", floorplan: str = "fullstack",
          inputs: str = "Z", ones=()) -> Lowered:
    """`ones`: the data qubits the program is given as |1⟩ (its spec's `assumes`, a basis
    state): prepared in |0̄⟩ and flipped, by an X̄ kept in software, before it runs."""
    prog = qec.program
    if prog is None:
        raise NotLowered("the .qec has no program")
    it = _Interp(qec)
    m = re.search(r"\w+\[(\d+)\]\s+LogicData", prog.header.text)
    n_data = int(m[1]) if m else 0
    items = [Item("ins", f"prep q{i} {inputs}", 0, role="input") for i in range(n_data)]
    items += [Item("ins", f"x q{int(i)}", 0, role="input") for i in sorted(int(i) for i in ones)]
    for i in range(n_data):
        it.out.blocks[f"q{i}"] = f"LogicData[{i}]"
    items += it.walk(prog.body, {}, {}, [[]])
    _mark_reads(items)
    w = _Writer(it.out, fresh=10 ** 8)
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
    inputs_set = {f"prep q{i} {inputs}" for i in range(n_data)} | {f"x q{int(i)}" for i in ones}
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
