"""Shor at scale, layer five: the program written out concrete, as a folded `.alg`.

The user, 2026-10-30: "please do not cheat, and everything should be concrete hardware
instruction, there is no virtual qubit, virtual system calls".  The composed times
(`program.duration_ns`, `parallel.parallel_us`) are arithmetic over templates compiled one at a
time; here the program itself is written for the folded scheduler (`qccd.gadget.fold`), which
schedules every gate once per context -- its body, the homes of its blocks, the places taken
around it -- on ONE machine, every block in a memory of its own, and calls it as a box: a
repeated gadget call on the same blocks is the same schedule, exactly, not an estimate.

* **A gate per gadget call.**  A call's body becomes a gate over the blocks it is passed (in
  the order it first uses them); the blocks its own templates prepare are its locals.  Its
  templates are written in full, through the codesign rewrites (`templates`); every ancilla is
  a block of its own and every outcome an outcome of its own.  Gates with the same text are one
  gate.
* **Classical bits are concrete too.**  A source slot written in one call and read in another
  is a bit handed over: the writer's gate names it (`-> o`), every gate between passes it on,
  the reader's gate reads it (`<- c`) and its call waits until it is decoded.  Which slots a
  call hands on is liveness: the slots it writes that its caller, or a call after it, reads
  before writing them again (`summary`, backward over each body).
* **Decisions.**  A decision on a loop variable is structure: taken where it holds.  A guard on
  an outcome (`When`) is the archive's `bit` over the outcomes it reads, and the body runs under
  it.  A decision on data -- a table's bit (`Pred`, Gidney 2025's lookups and moduli) -- is a
  constant here, written with its value for the instance being written (`bit 1` or `bit 1 ^ 1`):
  a guarded body reserves its places either way, so the schedule is the same whatever the
  table says; only the classical controller's bits differ from one instance to the next.
* **Branches.**  A guarded run of instructions is a gate called under its guard (as qec2alg
  writes it); a Pauli frame stays where every outcome it reads is measured; a gadget call under
  a guard is called under it, never wrapped, so its inputs stay readable.

    write(prog, gadget, keyvals, env, templates)  ->  (`.alg` text, info)
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from . import program as P

__all__ = ["write", "WriteError"]

_TNAME = re.compile(r"\b([ploe])(\d+)\b")
_PAULI = re.compile(r"[xyz] \w+$")


class WriteError(ValueError):
    pass


def _src_slots(ref, env) -> list:
    """The source slots a classical reference reads (a parity of data-masked slots: every slot it
    may read -- the mask is data)."""
    if isinstance(ref, P._Rec):
        if ref.table != "src":
            raise WriteError("a record read by its address: not in Gidney 2025, not written here")
        return [ref.idx(env)]
    if isinstance(ref, P._MXor):
        return [ref.idx({**env, ref.var: v}) for v in range(ref.n(env))]
    return []


class _Items:
    """A body flattened in program order under env: loops unrolled, structural decisions taken,
    each item with the guards around it -- ("seg", node, env, guards, sid, pos),
    ("call", node, env, guards), ("when", node, env, guards, sid) opening a guard."""

    def __init__(self, prog, controller: bool = False):
        self.prog = prog
        self.unknown = 0          # data decisions on a table the inputs give no contents for
        #: every data decision the classical controller's (one hardware program for every
        #: instance -- Gidney 2025's primes differ only in their tables), not this instance's
        self.controller = controller

    def run(self, body, env, guards=()):
        out = []
        self._walk(body, env, tuple(guards), out)
        return out

    def _walk(self, body, env, guards, out):
        for pos, node in enumerate(body):
            if isinstance(node, P.Seg):
                out.append(("seg", node, env, guards, body.sid, pos))
            elif isinstance(node, P.Loop):
                for v in range(node.lo(env), node.hi(env)):
                    self._walk(node.body, {**env, node.var: v}, guards, out)
            elif isinstance(node, P.Let):
                self._walk(node.body, {**env, node.var: node.value(env)}, guards, out)
            elif isinstance(node, P.Pred):
                try:
                    c = 1 if self.controller and not node.structural else node.cond(env)
                except P.ExprError:
                    if node.structural:
                        raise
                    # a table whose contents the instance's inputs do not give (only its
                    # shape): the decision is written 1 -- the schedule reserves its body
                    # either way, so its time does not depend on it -- and counted
                    c, self.unknown = 1, self.unknown + 1
                if c > 1:
                    raise WriteError(f"a guard loop of {c} trips (line {node.qline})")
                if node.structural:
                    if c:
                        self._walk(node.body, env, guards, out)
                    continue
                # a decision on data: a constant for this instance, its body reserved either way
                g = ("data", id(node), len(out), int(c))
                out.append(("data", node, env, guards, g))
                self._walk(node.body, env, guards + (g,), out)
            elif isinstance(node, P.When):
                g = ("when", id(node), len(out))
                out.append(("when", node, env, guards, g))
                self._walk(node.body, env, guards + (g,), out)
            elif isinstance(node, P.Call):
                out.append(("call", node, env, guards))
            else:
                raise WriteError(f"{type(node).__name__} (line {getattr(node, 'qline', 0)}) is not "
                                 f"written concrete yet")


class _Gate:
    """One gate's body being written: its names, and the slots it holds."""

    def __init__(self):
        self.lines: list = []
        self.params: list = []            # concrete blocks passed in, in order
        self.bname: dict = {}             # concrete block -> its name here
        self.made: set = set()
        self.n = Counter()
        self.slot: dict = {}              # source slot -> the name of its bit here
        self.cin: list = []               # source slots read from outside, in order
        self.guard_name: dict = {}        # guard key -> the name of its bit here
        self.outs: dict = {}              # (sid, pos) -> [outcome names] of a seg in this body
        self.locals: set = set()          # the names of its templates' own blocks (`l<i>`)

    def fresh(self, p: str) -> str:
        self.n[p] += 1
        return f"{p}{self.n[p] - 1}"

    def block(self, q: str, new: bool = False) -> str:
        if new:
            if q in self.bname and q not in self.made:
                raise WriteError(f"block {q} is prepared where it was passed in")
            self.made.add(q)
            self.bname[q] = self.fresh("L")
            return self.bname[q]
        if q not in self.bname:
            self.params.append(q)
            self.bname[q] = self.fresh("P")
        return self.bname[q]

    def read_slot(self, a: int, ever=None) -> str:
        if a not in self.slot:
            if ever is not None and a not in ever:
                # no instruction has written it yet (program order): it holds 0
                self.slot[a] = self.fresh("Z")
                self.lines.append(f"bit 1 ^ 1 -> {self.slot[a]}")
                return self.slot[a]
            self.cin.append(a)
            self.slot[a] = self.fresh("C")
        return self.slot[a]


class Writer:
    def __init__(self, prog, templates: dict, *, controller: bool = False):
        self.prog = prog
        self.tpl = templates
        self.items = _Items(prog, controller)
        self.gates: dict = {}              # text hash -> (name, header words, body lines)
        self.by_text: dict = {}
        self.summ_memo: dict = {}
        self.stats = Counter()
        self.gens: dict = {}               # logical ancilla index -> its generation (program order)
        self.ever: set = set()             # source slots some instruction wrote (program order)
        self.use = Counter()               # concrete block -> the template instances that touch it
        self.reuse: set = set()            # gadgets every call of which is one gate (`write_program`)
        self.reused: dict = {}
        self.exp = P._Exp(prog)            # (its guard_of: a template's guard over names)

    # ---- names of blocks, as the expander gives them ---------------------------------------
    def _bname(self, fam, idx, env, gens: dict, new=False) -> str:
        v = idx(env)
        if fam == "LogicData":
            return f"q{v}"
        if new:
            gens[v] = gens.get(v, -1) + 1
        return f"a{v}_{gens.get(v, 0)}"

    # ---- summaries: the slots a call reads from outside, and writes ------------------------
    def summary(self, node: P.Call, env) -> tuple:
        env2 = {**self.prog.params, **{f: e(env) for f, e in node.skeys + node.args}}
        key = (node.gadget, tuple((f, env2.get(f)) for f, _ in node.skeys + node.args))
        got = self.summ_memo.get(key)
        if got is None:
            got = self._summary_items(self.items.run(self.prog.body_of(node, env), env2))
            self.summ_memo[key] = got
        return got

    def _item_rw(self, it) -> tuple:
        kind = it[0]
        if kind == "seg":
            node, env = it[1], it[2]
            r = [a for ref in node.ext for a in _src_slots(ref, env)]
            w = [a for table, lo, hi, _ in node.writes if table == "src" for a in range(lo(env), hi(env))]
            return set(r), set(w)
        if kind == "call":
            return self.summary(it[1], it[2])
        if kind == "when":
            node, env = it[1], it[2]
            return {a for term in node.cond.terms for _, rs in term for ref in rs
                    for a in _src_slots(ref, env)}, set()
        return set(), set()

    def _summary_items(self, items) -> tuple:
        R, W = set(), set()
        for it in items:
            r, w = self._item_rw(it)
            R |= r - W
            W |= w
        return frozenset(R), frozenset(W)

    # ---- one call, as a gate ----------------------------------------------------------------
    def gate(self, node: P.Call, env, live_out: frozenset) -> tuple:
        """(gate name, concrete blocks it is passed, slots it reads, slots it hands back)."""
        if node.gadget in self.reuse and node.gadget in self.reused:
            # (one gate for every call of it: checked to be the same text -- Gidney 2025's
            # primes differ only in the controller's table bits and their slots' addresses)
            self.stats[f"calls of {node.gadget} reused"] += 1
            return self.reused[node.gadget]
        env2 = {**self.prog.params, **{f: e(env) for f, e in node.skeys + node.args}}
        G, body, cout = self.body(self.items.run(self.prog.body_of(node, env), env2), live_out, node.gadget)
        head = [G.bname[q] for q in G.params] + (["<-"] + [G.slot[a] for a in G.cin] if G.cin else []) + \
            (["->"] + [G.slot[a] for a in cout] if cout else [])
        text = " ".join(head) + "\n" + "\n".join(body)
        h = hashlib.sha1(text.encode()).hexdigest()[:10]
        name = self.by_text.get(h)
        if name is None:
            name = f"{re.sub(r'[^a-z0-9]', '', node.gadget.lower())[:20]}_{h}"
            self.by_text[h] = name
            self.gates[name] = (head, body)
            self.stats["gates"] += 1
        self.stats["gate calls"] += 1
        out = name, list(G.params), list(G.cin), cout
        if node.gadget in self.reuse:
            self.reused[node.gadget] = out
        return out

    def body(self, items, live_out: frozenset, what: str) -> tuple:
        """(the gate's names, its emitted lines, the slots it hands back) for a body's items."""
        rw = [self._item_rw(it) for it in items]
        # liveness, backward: the slots read after each item before they are written again
        after = [None] * len(items)
        live = set(live_out)
        for k in range(len(items) - 1, -1, -1):
            after[k] = frozenset(live)
            r, w = rw[k]
            live = (live - w) | r
        G = _Gate()
        gens = self.gens
        for k, it in enumerate(items):
            kind, guards = it[0], it[3]
            gtxt = [G.guard_name[g] for g in guards]
            if kind == "data":
                if self.items.controller:
                    # (the classical controller's bit: this prime's table, the same hardware)
                    name = G.fresh("D")
                    G.lines.append(f"bit 1 -> {name}")
                else:
                    name = G.fresh("K")
                    G.lines.append(f"bit {'1' if it[4][3] else '1 ^ 1'} -> {name}")
                G.guard_name[it[4]] = name
                self.stats["data decisions"] += 1
            elif kind == "when":
                f = self._formula(it[1].cond, it[2], G)
                name = G.fresh("W")
                G.lines.append(f"bit {P._bit_text(f, str)} -> {name}")
                G.guard_name[it[4]] = name
            elif kind == "seg":
                self._seg(it, G, gens, gtxt)
            elif kind == "call":
                r, w = rw[k]
                child_live = frozenset(w & after[k])
                # (under a guard: the bits it hands back as they are before it -- kept where it
                # does not fire)
                olds = {a: self._old(a, G) for a in child_live} if gtxt else {}
                cname, blocks, cin, cout = self.gate(it[1], it[2], child_live)
                words = [G.block(q) for q in blocks]
                ins = [self._rs(G, a) for a in cin]
                outs = [G.fresh("S") for _ in cout]
                line = cname + " " + " ".join(words) + (" <- " + " ".join(ins) if ins else "") + \
                    (" -> " + " ".join(outs) if outs else "")
                G.lines.append((f"if {' & '.join(gtxt)}: " if gtxt else "") + line)
                if not gtxt:
                    for a, o in zip(cout, outs):
                        G.slot[a] = o
                else:
                    # a call under a guard writes a slot only when it fires: old ^ (g & (out ^ old))
                    self.stats["guarded call outputs"] += len(cout)
                    cond = P._sop_and(*[[((0, (g,)),)] for g in gtxt])
                    for a, o in zip(cout, outs):
                        old = olds[a]
                        nf = P._sop_xor(old, P._sop_and(cond, P._sop_xor([((0, (o,)),)], old)))
                        s = G.fresh("S")
                        G.lines.append(f"bit {P._bit_text(nf, str) if nf else '1 ^ 1'} -> {s}")
                        G.slot[a] = s
                self.stats["calls"] += 1
        cout = sorted(a for a in live_out if a in G.slot and a not in G.cin)
        missing = [a for a in live_out if a not in G.slot]
        if missing:
            raise WriteError(f"{what}: hands back slots it never wrote: {missing[:4]}")
        body = _emit(G.lines, set(G.bname.values()) | G.locals, known={G.slot[a] for a in G.cin})
        return G, body, cout

    def _rs(self, G: _Gate, a: int) -> str:
        """The name of slot `a`'s bit in gate `G`: its own, a constant 0 if nothing has written it
        yet, or an input."""
        return G.read_slot(a, self.ever)

    def _old(self, a: int, G: _Gate) -> list:
        """A slot's bit before a guarded write, as a formula: this gate's, or 0 -- a slot no
        instruction has written yet (in program order) holds 0."""
        if a in G.slot:
            return [((0, (G.slot[a],)),)]
        if a in self.ever:
            raise WriteError(f"slot {a} is written under a guard where its old bit, written "
                             f"elsewhere, is not passed in: not written concrete yet")
        return []

    def _formula(self, f, env, G: _Gate) -> list:
        out = []
        for term in f.terms:
            fs = []
            for c, rs in term:
                parts = [[()]] if c else []
                for ref in rs:
                    if isinstance(ref, tuple) and len(ref) == 3:
                        bsid, bpos, kk = ref
                        parts.append([((0, (G.outs[(bsid, bpos)][kk],)),)])
                    elif isinstance(ref, P._MXor) and self.items.controller:
                        # the mask is the controller's: each slot under a bit of its own
                        for v in range(ref.n(env)):
                            d = G.fresh("D")
                            G.lines.append(f"bit 1 -> {d}")
                            s = self._rs(G, ref.idx({**env, ref.var: v}))
                            parts.append([((0, (d,)), (0, (s,)))])
                    elif isinstance(ref, P._MXor):
                        e = env
                        for v in range(ref.n(e)):
                            ev = {**e, ref.var: v}
                            try:
                                bit = ref.cond(ev) > 0
                            except P.ExprError:
                                # (a mask the instance's inputs do not give: every slot, counted)
                                bit, self.items.unknown = True, self.items.unknown + 1
                            if bit:
                                parts.append([((0, (self._rs(G, ref.idx(ev)),)),)])
                            else:
                                self._rs(G, ref.idx(ev))      # (read either way: the mask is data)
                    else:
                        parts += [[((0, (self._rs(G, a),)),)] for a in _src_slots(ref, env)]
                fs.append(P._sop_xor(*parts))
            out = P._sop_xor(out, P._sop_and(*fs))
        return out

    def _seg(self, it, G: _Gate, gens: dict, gtxt: list):
        _, node, env, _, sid, pos = it
        t = self.tpl.get(node.tid, self.prog.templates[node.tid])
        self.stats["templates"] += 1
        ren = {}
        made_here = set()
        for ln in t.lines:
            w = (ln.split(": ", 1)[1] if ln.startswith("if ") else ln).split()
            if w[0] in ("prep", "inject") and w[1].startswith("p"):
                made_here.add(w[1])
            if w[0] == "autoccz":
                made_here.update(x for x in w[1:] if x.startswith("p"))
        for i, (fam, idx) in enumerate(node.params):
            new = f"p{i}" in made_here
            if new and fam == "LogicData":
                raise WriteError(f"template {node.tid} prepares data block {idx.text} again: "
                                 f"not written concrete yet")
            q = self._bname(fam, idx, env, gens, new=new)
            ren[f"p{i}"] = G.block(q, new=new)
            self.use[q] += 1
        for i in range(t.n_locals):
            ren[f"l{i}"] = G.fresh("T")
            G.locals.add(ren[f"l{i}"])
        outs = [G.fresh("O") for _ in range(t.n_out)]
        G.outs[(sid, pos)] = outs
        ren.update({f"o{i}": o for i, o in enumerate(outs)})
        vals = {f"o{i}": [((0, (o,)),)] for i, o in enumerate(outs)}
        for i, ref in enumerate(node.ext):
            slots = _src_slots(ref, env)
            if isinstance(ref, P._MXor):
                f = self._formula(P._F([((0, (ref,)),)]), env, G)
                name = G.fresh("S")
                G.lines.append(f"bit {P._bit_text(f, str) if f else '1 ^ 1'} -> {name}")
                ren[f"e{i}"] = name
            elif len(slots) == 1:
                ren[f"e{i}"] = self._rs(G, slots[0])
            else:
                raise WriteError(f"template {node.tid} reads e{i} as {ref!r}")
            vals[f"e{i}"] = [((0, (ren[f"e{i}"],)),)]
        pre = (f"if {' & '.join(gtxt)}: " if gtxt else "")
        for ln in t.lines:
            body = _TNAME.sub(lambda m: ren[m[0]], ln)
            if body.startswith("if ") and pre:
                cond, rest = body[3:].split(": ", 1)
                G.lines.append(f"if {' & '.join(gtxt)} & {cond}: {rest}")
            else:
                G.lines.append(pre + body)
        for table, lo, hi, text in node.writes:
            f = self.exp.guard_of(text, vals)
            if not gtxt:
                name = G.fresh("S")
                G.lines.append(f"bit {P._bit_text(f, str) if f else '1 ^ 1'} -> {name}")
                for a in range(lo(env), hi(env)):
                    G.slot[a] = name
                    self.ever.add(a)
                continue
            # a write under a guard: the new bit where the guard holds, the old one where not --
            # old ^ (guard & (new ^ old)), one archive bit per old bit
            self.stats[f"guarded write: {node.tid} under {'/'.join(g[0] for g in it[3])}"] += 1
            cond = P._sop_and(*[[((0, (g,)),)] for g in gtxt])
            by_old: dict = {}
            for a in range(lo(env), hi(env)):
                o = self._old(a, G)
                by_old.setdefault(o[0][0][1][0] if o else None, ([], o))[0].append(a)
            for _, (slots, o) in by_old.items():
                nf = P._sop_xor(o, P._sop_and(cond, P._sop_xor(f, o)))
                name = G.fresh("S")
                G.lines.append(f"bit {P._bit_text(nf, str) if nf else '1 ^ 1'} -> {name}")
                for a in slots:
                    G.slot[a] = name
                    self.ever.add(a)


# --------------------------------------------------------------------------- the body's text


def _parse(line: str):
    if line.startswith("if "):
        cond, body = line[3:].split(": ", 1)
        return [c.strip() for c in cond.split("&")], body
    return [], line


def _names(guards) -> set:
    return {t.strip().lstrip("!") for x in guards for t in x.split("^") if t.strip() not in ("0", "1")}


def _neg(g: str) -> str:
    return g[1:] if g.startswith("!") else "!" + g


_SUBGATES: dict = {}


def _emit(lines: list, blocks: set, known: set = frozenset()) -> list:
    """The body's lines with every guarded run of instructions a branch gate called under its
    guard (`br_<hash>`, kept in `_SUBGATES`), every frame where the outcomes it reads are
    measured, and every gadget call (a word that is not an instruction) called directly.
    `known`: the gate's classical inputs, decided before it starts."""
    from qccd.gadget.algorithm import PRIMITIVES

    def is_call(body):
        return body.split()[0] not in PRIMITIVES and body.split()[0] != "bit"

    top = [True]

    def emit(items):
        out, up, i = [], [], 0
        measured = set(known) if top[0] else set()
        top[0] = False
        while i < len(items):
            guards, body = items[i]
            if not guards or is_call(body):
                if guards:
                    if not _names(guards) <= measured:
                        up.append((guards, body))
                        raise WriteError(f"a call under ({' & '.join(guards)}) reads outcomes measured "
                                         f"inside a branch")
                    out.append(f"if {' & '.join(guards)}: {body}")
                else:
                    out.append(body)
                for m in re.findall(r"->\s*([\w ]+)$", body):
                    measured.update(m.split())
                i += 1
                continue
            g = guards[0]
            pair, j = (g, _neg(g)), i
            while j < len(items) and items[j][0] and items[j][0][0] in pair and not is_call(items[j][1]):
                j += 1
            region = items[i:j]
            for br in dict.fromkeys(gs[0] for gs, _ in region):
                kids = [(gs[1:], b) for gs, b in region if gs[0] == br]
                if all(_PAULI.match(b) for _, b in kids):
                    for gs, b in kids:
                        if _names([br] + gs) <= measured:
                            out.append(f"if {' & '.join([br] + gs)}: {b}")
                        else:
                            up.append(([br] + gs, b))
                    continue
                body_lines, hoist = emit(kids)
                made, used = set(), []
                for b in body_lines:
                    op = _parse(b)[1]
                    w = op.split()
                    if w[0] in ("prep", "inject"):
                        made.add(w[1])
                    if w[0] == "autoccz":
                        made.update(w[1:])
                    for x in op.split("->")[0].split()[1:]:
                        if x in blocks and x not in used:
                            used.append(x)
                acts = [b for b in body_lines if not _PAULI.match(_parse(b)[1])
                        and _parse(b)[1].split()[0] not in ("decode", "bit")]
                for gs, b in hoist:
                    blk = b.split()[1]
                    if any(blk in _parse(x)[1].split("->")[0].split()[1:] for x in acts):
                        raise WriteError(f"frame '{b}' under {gs} leaves a branch that acts on {blk}")
                params = [x for x in used if x not in made]
                ren = {x: f"b{k}" for k, x in enumerate(params)}
                sub_body = [re.sub(r"\b\w+\b", lambda mm: ren.get(mm[0], mm[0]), b) for b in body_lines]
                key = hashlib.sha1(("\n".join([str(len(params))] + sub_body)).encode()).hexdigest()[:10]
                sub = f"br_{key}"
                _SUBGATES[sub] = ([f"b{k}" for k in range(len(params))], sub_body)
                if not _names([br]) <= measured:
                    raise WriteError(f"a branch under {br} at a level that has not measured it")
                out.append(f"if {br}: {sub} {' '.join(params)}" if params else f"if {br}: {sub}")
                for gs, b in hoist:
                    gs = [br] + gs
                    if _names(gs) <= measured:
                        out.append(f"if {' & '.join(gs)}: {b}")
                    else:
                        up.append((gs, b))
            i = j
        return out, up

    body, rest = emit([_parse(ln) for ln in lines])
    if rest:
        raise WriteError(f"{len(rest)} frames read outcomes no level measured: {rest[:2]}")
    return body


def _text(w: Writer, title: str, floorplan: str) -> list:
    out = [f"title {title}", f'floorplan "{floorplan}"']
    for sub, (ps, body) in _SUBGATES.items():
        out.append(f"gate {sub} {' '.join(ps)}:" if ps else f"gate {sub}:")
        out += ["  " + ln for ln in body]
    for gname, (head, body) in w.gates.items():
        out.append(f"gate {gname} {' '.join(head)}:")
        out += ["  " + ln for ln in body]
    return out


def write_program(prog, templates: dict, *, floorplan: str, title: str = "the program",
                  reuse=("gOnePrime",), controller: bool = True) -> tuple:
    """The whole program written out concrete: the root's own instructions, a gate per distinct
    call, every block it uses prepared first.  `reuse`: gadgets every call of which is ONE gate --
    for Gidney 2025's primes, whose gates are the same text when the tables are the controller's
    (`controller`), checked prime by prime where it is claimed (tests/test_scale_concrete.py)."""
    _SUBGATES.clear()
    w = Writer(prog, templates, controller=controller)
    w.reuse = set(reuse)
    G, body, _ = w.body(w.items.run(prog.root, {**prog.params}), frozenset(), "the program")
    if G.cin:
        raise WriteError(f"the program reads {len(G.cin)} slots nothing wrote before it")
    out = _text(w, title, floorplan)
    out += [f"prep {G.bname[q]} Z" for q in G.params]
    out += body
    info = {"gates": len(w.gates), "branch_gates": len(_SUBGATES), "blocks": len(G.params),
            "stats": dict(w.stats), "data decisions on a table without contents": w.items.unknown,
            "home_order": [G.bname[q] for q in sorted(G.params, key=lambda q: -w.use[q])]}
    return "\n".join(out) + "\n", info


def write(prog, gadget: str, keyvals: tuple, env: dict, templates: dict, *, floorplan: str,
          title: str = "") -> tuple:
    """One gadget call written out concrete: its gates (and their branch gates), every block it
    is passed prepared first, and the call.  Slots it reads from outside are bits the program
    starts with (constants: this writes a call alone)."""
    _SUBGATES.clear()
    w = Writer(prog, templates)
    key = next(k for k in prog.bodies if k[0] == gadget and k[1] == keyvals)
    call = P.Call(gadget, tuple((f, P.Expr(str(v))) for f, v in keyvals),
                  tuple((f, P.Expr(str(env.get(f, 0)))) for f in prog.formals[gadget]
                        if f not in dict(keyvals)), key[2])
    name, blocks, cin, cout = w.gate(call, {**prog.params, **env}, frozenset())
    out = [f"title {title or gadget}", f'floorplan "{floorplan}"']
    for sub, (ps, body) in _SUBGATES.items():
        out.append(f"gate {sub} {' '.join(ps)}:" if ps else f"gate {sub}:")
        out += ["  " + ln for ln in body]
    for gname, (head, body) in w.gates.items():
        out.append(f"gate {gname} {' '.join(head)}:")
        out += ["  " + ln for ln in body]
    root = [f"q{k}" for k in range(len(blocks))]
    out += [f"prep {b} Z" for b in root]
    ins = []
    for k, _ in enumerate(cin):
        ins.append(f"c{k}")
        out.append(f"bit 1 -> c{k}")
    out.append(f"{name} {' '.join(root)}" + (" <- " + " ".join(ins) if ins else ""))
    info = {"gates": len(w.gates), "branch_gates": len(_SUBGATES), "blocks": len(blocks),
            "inputs": len(cin), "stats": dict(w.stats),
            "data decisions on a table without contents": w.items.unknown,
            # the blocks it passes, the most used first: the order to give them homes in
            # (`fold.schedule_folded(home_order=...)`)
            "home_order": [root[k] for k in sorted(range(len(blocks)), key=lambda k: -w.use[blocks[k]])]}
    return "\n".join(out) + "\n", info
