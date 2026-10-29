"""A FormalRV program as QCCD runs it, kept as small as FormalRV wrote it: the symbolic program.

FormalRV's `.qec` for Shor is the same twelve thousand lines whatever the key size: its
gadgets keep their loops and parameters.  QCCD's lowering (`qccd.fullstack.qec2alg`) unrolls
every loop and lowers every call once per value of its arguments, so its output grows with
the program's *execution* -- 20 thousand lines at 3 bits, 65 thousand at 5, and some 10^13
at 2048.  This module lowers the same `.qec` without unrolling anything:

* **a loop stays a loop** (`Loop`), its body lowered once;
* **a gadget is lowered once per structure** (`Call` -> `Program.bodies`): its *structural*
  parameters (those that set a loop's trip count, here or in what it calls) are the key; its
  block indices and its data stay expressions, evaluated only when an instance is looked at;
* **a decision on data is a slot** (`Pred`): FormalRV writes `if bit b of cst` as a loop of
  0 or 1 trips (`for 0 <= rv_g < cst/2^b%2`); here it is a predicate over the data, and the
  body is lowered once, as if taken.  The machine reserves the slot either way and fires
  the pulses only when it holds, so a gadget takes the same time whatever its constant
  (QCCD's guards already work so: a guarded instruction reserves its place either way);
* **straight-line statements are templates** (`Seg` -> `Program.templates`): a run of
  merges, decodes, preparations and frames between two control statements is lowered by the
  rules of `qec2alg` and written with its blocks as parameters (`p0`, `p1`, ...) and its own
  blocks and outcomes as locals; two runs that lower to the same text are one template,
  stored once (MAJ at bit 7 and MAJ at bit 1900 are the same lines);
* **a rotation is one node** (`Rot`): FormalRV's `ppmrot(±π/2^k) P` is QCCD's
  repeat-until-success chain, k levels deep (`qec2alg._Interp.rotation`); the node keeps k
  as an expression.

So the program's size is the size of its gadgets, not of its run.  `expand` turns it back
into the instruction list at small sizes -- the differential test against `qec2alg.lower`
(`compare`) -- and `count` totals any instance's instructions without expanding it.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from collections import Counter
from dataclasses import dataclass, field

from .. import layers
from ..provenance import _qec_kind
from ..qec2alg import NotLowered, ROT_BASE, _untable
from ..sysprog import split_top
from .expr import Expr, ExprError

__all__ = ["Program", "Builder", "build", "Seg", "Loop", "Pred", "Let", "Call", "Rot", "Template",
           "expand", "count", "total", "duration_ns", "normalize_alg", "compare"]

_FACTOR = re.compile(r"([XYZ])\[(\w+)\[([^\[\]]+)\]\[0\]\]")
_LOOP = re.compile(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?):\s*$")
_BAND = re.compile(r"\bc\[([^\[\]]+)\)")
_SID = itertools.count(1)
_FAMILY = re.compile(r"\w+\[.*\]\s+(\w+)")

#: what a guarded call cannot gate: it runs either way (qccd.gadget.algorithm.UNGUARDABLE)
UNGUARDABLE = ("prep", "inject", "se", "store", "decode")


# --------------------------------------------------------------------------- the tree


class Body(list):
    """A body's nodes, with an identity that outcome references name."""

    def __init__(self, nodes=()):
        super().__init__(nodes)
        self.sid = next(_SID)

    def __hash__(self):
        return self.sid

    def __eq__(self, other):
        return self is other


@dataclass(frozen=True)
class Template:
    """Straight-line `.alg` lines with every name a parameter or a local: `p<i>` a block
    passed in (`Seg.params`), `l<i>` a block the run prepares (`Seg.locals`), `o<i>` an
    outcome it measures, `e<i>` an outcome measured before it (`Seg.ext`)."""
    tid: str
    lines: tuple
    qlines: tuple
    n_params: int
    n_locals: int
    n_out: int
    n_ext: int

    @property
    def kinds(self) -> Counter:
        return Counter((t.split(": ", 1)[1] if t.startswith("if ") else t).split()[0] for t in self.lines)


@dataclass
class Seg:
    tid: str
    params: tuple        # ((family, Expr), ...) per p<i>
    locals: tuple        # ((family, Expr), ...) per l<i>
    ext: tuple           # (OutRef, ...) per e<i>


@dataclass
class Loop:
    var: str
    lo: Expr
    hi: Expr
    body: Body
    qline: int = 0


@dataclass
class Pred:
    """A slot that runs only when `cond` > 0 -- a decision on data, not on an outcome."""
    cond: Expr
    body: Body
    qline: int = 0


@dataclass
class Let:
    var: str
    value: Expr
    body: Body


@dataclass
class Call:
    gadget: str
    skeys: tuple         # ((formal, Expr), ...): the structural arguments, the instance's key
    args: tuple          # ((formal, Expr), ...): every other argument
    fam: tuple           # ((formal family, actual family), ...)
    qline: int = 0


@dataclass
class Rot:
    """exp(∓iπ/2^k P̄) as QCCD does it: a repeat-until-success chain k levels deep."""
    factors: tuple       # ((letter, family, Expr), ...)
    k: Expr
    sign: int
    qline: int = 0


# An OutRef is (body sid, node index, outcome index): outcome o<i> of the Seg at that index
# of that body, in the body's current run -- a frame reads a merge of its own gadget call.


@dataclass
class Program:
    templates: dict = field(default_factory=dict)      # tid -> Template
    bodies: dict = field(default_factory=dict)         # (gadget, keyvals, fam) -> Body
    formals: dict = field(default_factory=dict)        # gadget -> [formal names] (non-family)
    root: Body = field(default_factory=Body)
    n_data: int = 0
    #: the program's own parameters (FormalRV's `params`, e.g. m, bits, N, a), when the
    #: `.qec` was not specialized: the root's environment
    params: dict = field(default_factory=dict)
    builder: object = None

    def body_of(self, call: Call, env: dict) -> Body:
        keyvals = tuple((f, e(env)) for f, e in call.skeys)
        key = (call.gadget, keyvals, call.fam)
        b = self.bodies.get(key)
        if b is None:
            rep = {f: e(env) for f, e in call.skeys + call.args}
            self.builder.instance(call.gadget, keyvals, dict(call.fam), rep)
            b = self.bodies[key]
        return b

    def size(self) -> dict:
        nodes = 0

        def walk(body):
            nonlocal nodes
            for n in body:
                nodes += 1
                if hasattr(n, "body"):
                    walk(n.body)
        walk(self.root)
        for b in self.bodies.values():
            walk(b)
        return {"templates": len(self.templates),
                "template_lines": sum(len(t.lines) for t in self.templates.values()),
                "gadget_instances": len(self.bodies), "nodes": nodes}


# --------------------------------------------------------------------------- building


class _Blk:
    """A block reference: its family, its index (an expression), its value where it was
    lowered, and for a logical ancilla its generation there (a re-prepared index is a new
    block)."""
    __slots__ = ("fam", "idx", "gen", "val")

    def __init__(self, fam, idx: Expr, gen: int, val: int):
        self.fam, self.idx, self.gen, self.val = fam, idx, gen, val

    def key(self):
        return (self.fam, self.val, self.gen)


class _Out:
    """An outcome of the open run."""
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k


class _State:
    """One body being lowered: the open run, the merges whose outcomes later frames read."""

    def __init__(self, scope: list):
        self.nodes = Body()
        self.run: list = []              # [(tokens, qline, single)]
        self.outs: list = []             # the open run's outcomes
        self.scope = scope + [[]]        # per enclosing body: [(lo, hi, ref)]; ref an _Out or an OutRef
        self.current = None              # (out, [blocks], decoded?)


class Builder:
    def __init__(self, qec: layers.Stage):
        self.qec = qec
        self.gadgets = qec.gadgets()
        self.prog = Program(builder=self)
        self.headers = {}
        for name, g in self.gadgets.items():
            m = re.match(r"gadget\s+[\w!]+\[.*?\]\((.*)\):\s*$", g.header.text)
            formals = split_top(m[1], ",") if m else []
            self.headers[name] = formals
            self.prog.formals[name] = [f.strip() for f in formals if _family(f) is None]
        self.structural = self._structural()
        self.gen: dict = {}

    def _structural(self) -> dict:
        """gadget -> its formals that set a loop's trip count, here or in what it calls."""
        uses, passes = {}, {}
        for name, g in self.gadgets.items():
            fs = set(self.prog.formals[name])
            b, p = set(), []

            def scan(body):
                for s in body:
                    raw = _untable(s.text.strip())
                    lp = _LOOP.fullmatch(raw)
                    if lp and not _is_pred_var(lp[2]):
                        b.update((Expr(lp[1]).vars | Expr(lp[3]).vars) & fs)
                    call = re.fullmatch(r"([\w!]+)\((.*)\)", raw)
                    if call and call[1] in self.gadgets:
                        for f, a in zip(self.headers[call[1]], split_top(call[2], ",")):
                            if _family(f) is None:
                                p.append((call[1], f.strip(), Expr(a).vars & fs))
                    scan(s.children)
            scan(g.body)
            uses[name], passes[name] = b, p
        struct = {n: set(v) for n, v in uses.items()}
        changed = True
        while changed:
            changed = False
            for n, plist in passes.items():
                for callee, f, vs in plist:
                    if f in struct[callee] and not vs <= struct[n]:
                        struct[n] |= vs
                        changed = True
        return struct

    def build(self, params: dict | None = None) -> Program:
        """`params`: values for the program's parameters, when the `.qec` is FormalRV's
        generic one (symbolic in them) rather than one it specialized."""
        p = self.qec.program
        if p is None:
            raise NotLowered("the .qec has no program")
        self.prog.params = dict(params or {})
        m = re.search(r"Surface\[([^\[\]]+)\]\s+LogicData", p.header.text)
        self.prog.n_data = Expr(m[1])(self.prog.params) if m else 0
        self.prog.root = self.body(p.body, dict(self.prog.params), {}, [], top=True)
        return self.prog

    def instance(self, gadget: str, keyvals: tuple, famap: dict, rep: dict) -> None:
        key = (gadget, keyvals, tuple(sorted(famap.items())))
        if key not in self.prog.bodies:
            self.prog.bodies[key] = Body()             # (a recursion guard)
            self.prog.bodies[key] = self.body(self.gadgets[gadget].body, rep, famap, [], top=False)

    # ---- a body ------------------------------------------------------------------------
    def body(self, stmts: list, rep: dict, famap: dict, scope: list, top: bool) -> Body:
        """Lower `stmts` at `rep` -- a value for every variable in scope.  What `rep`
        settles (which merge a frame reads, which ancilla is re-prepared, which blocks are
        one block) must be the same for every instance; `compare` checks that against
        qec2alg wherever the whole program can still be expanded."""
        S = _State(scope)
        for s in stmts:
            self.stmt(s, S, rep, famap, top, [])
        self.close(S, top)
        return S.nodes

    def stmt(self, s, S: _State, rep: dict, famap: dict, top: bool, guards: list) -> None:
        raw = _untable(s.text.strip())
        if raw == "skip":
            return
        lp = _LOOP.fullmatch(raw)
        if lp:
            if guards:
                raise NotLowered(f".qec line {s.line}: a loop under a guard")
            var, lo, hi = lp[2], Expr(lp[1]), Expr(lp[3])
            self.close(S, top)
            if _is_pred_var(var):
                if lo.text != "0":
                    raise NotLowered(f".qec line {s.line}: a guard loop from {lo.text}")
                S.nodes.append(Pred(hi, self.body(s.children, {**rep, var: 0}, famap, S.scope, top), s.line))
                return
            lo_v = lo(rep)
            if hi.text == f"{lo.text} + 1" or (not lo.vars and not hi.vars and hi(rep) - lo_v == 1):
                S.nodes.append(Let(var, lo, self.body(s.children, {**rep, var: lo_v}, famap, S.scope, top)))
                return
            S.nodes.append(Loop(var, lo, hi, self.body(s.children, {**rep, var: lo_v}, famap, S.scope, top), s.line))
            return
        if raw.startswith("when ") and raw.endswith(":"):
            out = self._owner(self._band(raw, rep, s.line), S, s.line)
            for c in s.children:
                self.stmt(c, S, rep, famap, top, guards + [out])
            return
        call = re.fullmatch(r"([\w!]+)\((.*)\)", raw)
        if call and call[1] in self.gadgets:
            if guards:
                raise NotLowered(f".qec line {s.line}: a call under a guard")
            self.close(S, top)
            S.nodes.append(self.call(call[1], split_top(call[2], ","), rep, famap, s.line))
            return
        self.leaf(s, raw, S, rep, famap, top, guards)

    def call(self, name: str, args: list, rep: dict, famap: dict, line: int) -> Call:
        formals = self.headers[name]
        if len(formals) != len(args):
            raise NotLowered(f".qec line {line}: {name} takes {len(formals)} arguments, given {len(args)}")
        skeys, rest, fam2, rep2 = [], [], {}, {}
        for f, a in zip(formals, args):
            fam = _family(f)
            if fam is not None:
                fam2[fam] = famap.get(a.strip(), a.strip())
                continue
            e = Expr(a)
            rep2[f.strip()] = e(rep)
            (skeys if f.strip() in self.structural[name] else rest).append((f.strip(), e))
        keyvals = tuple((f, rep2[f]) for f, _ in skeys)
        self.instance(name, keyvals, fam2, rep2)
        return Call(name, tuple(skeys), tuple(rest), tuple(sorted(fam2.items())), line)

    # ---- the statements of a straight run ----------------------------------------------
    def blk(self, fam: str, idx_text: str, rep: dict, famap: dict, new: bool = False) -> _Blk:
        fam = famap.get(fam, fam)
        e = Expr(idx_text)
        try:
            v = e(rep)
        except ExprError as err:
            raise NotLowered(f"block index {idx_text!r}: {err}") from None
        if fam == "LogicData":
            return _Blk(fam, e, 0, v)
        if fam == "LogicalAncilla":
            if new:
                self.gen[v] = self.gen.get(v, -1) + 1
            return _Blk(fam, e, self.gen.get(v, 0), v)
        raise NotLowered(f"no block family {fam!r}")

    def factors(self, text: str, rep: dict, famap: dict) -> list:
        return [(p, self.blk(fam, idx, rep, famap)) for p, fam, idx in _FACTOR.findall(text)]

    def leaf(self, s, raw: str, S: _State, rep: dict, famap: dict, top: bool, guards: list) -> None:
        kd = _qec_kind(s)
        q = s.line
        g = [("guard", o) for o in guards]
        if kd == "alloc_block":
            return
        if kd == "magic" or re.match(r"\d+\s+magic\s+\|", raw):
            m = re.search(r"(\|[^>]+>)\s*->\s*(\w+)\[([^,\[\]]+),", raw)
            if not m:
                raise NotLowered(f".qec line {q}: {raw!r}")
            b = self.blk(m[2], m[3], rep, famap, new=True)
            if m[1] == "|Y+>":
                S.run.append((g + ["prep", b, "Y"], q, None))
            elif m[1] == "|T>":
                # the factory makes T-dagger|+> (its verified output); X in software makes it T|+>
                S.run.append((g + ["inject", b], q, None))
                S.run.append((g + ["x", b], q, None))
            else:
                raise NotLowered(f".qec line {q}: no gadget prepares {m[1]} yet")
            return
        if kd in ("alloc", "prepare", "extract", "split"):
            return
        if kd == "merge":
            fs = self.factors(raw.split("= merge", 1)[1].split(" with ")[0], rep, famap)
            band = self._band(raw.split("->")[-1], rep, q)
            o = _Out(len(S.outs))
            S.outs.append(o)
            S.scope[-1].append((band[0], band[1], o))
            toks = g + ["ppm"] + [x for p, b in fs for x in (p, b)] + ["->", o]
            S.run.append((toks, q, (fs[0][0], fs[0][1], o) if len(fs) == 1 else None))
            S.current = (o, [b for _, b in fs], False)
            return
        if kd == "decode":
            if S.current is None:
                raise NotLowered(f".qec line {q}: a decode outside a merge group")
            if not S.current[2]:
                S.run.append((g + ["decode"] + list(S.current[1]), q, None))
                S.current = (S.current[0], S.current[1], True)
            return
        if kd == "frame_on":
            out = self._owner(self._band(raw, rep, q), S, q)
            for p, b in self.factors(raw, rep, famap):
                S.run.append((g + [("guard", out), p.lower(), b], q, None))
            return
        if kd == "frame":
            for p, b in self.factors(raw, rep, famap):
                S.run.append((g + [p.lower(), b], q, None))
            return
        if raw.startswith("ppmrot"):
            if guards:
                raise NotLowered(f".qec line {q}: a rotation under a guard")
            m = re.match(r"ppmrot\((-?)\s*pi\s*/\s*2\^.*?\s+with\s+tSynth\[([^\]]+)\]", raw)
            if not m:
                raise NotLowered(f".qec line {q}: a rotation QCCD cannot read: {raw[:100]!r}")
            fs = self.factors(raw.split(" with ")[0], rep, famap)
            if not fs or any(p not in "XZ" for p, _ in fs):
                raise NotLowered(f".qec line {q}: a rotation of {fs}")
            self.close(S, top)
            S.nodes.append(Rot(tuple((p, b.fam, b.idx) for p, b in fs), Expr(m[2]), -1 if m[1] else 1, q))
            return
        raise NotLowered(f".qec line {q}: no gadget lowering for {raw[:100]!r}")

    def _band(self, text: str, rep: dict, line: int) -> tuple:
        m = _BAND.search(text)
        if not m:
            raise NotLowered(f".qec line {line}: no c[..) band in {text[:80]!r}")
        lo, hi = split_top(m[1], ",")
        return (Expr(lo)(rep), Expr(hi)(rep))

    def _owner(self, band, S: _State, line: int):
        for frame in reversed(S.scope):
            for lo, hi, ref in reversed(frame):
                if lo <= band[0] and band[1] <= hi:
                    return ref
        raise NotLowered(f".qec line {line}: no merge in scope wrote c{list(band)}")

    # ---- closing a run into a template ---------------------------------------------------
    def close(self, S: _State, top: bool) -> None:
        """The open run becomes a `Seg`: its lines with every name made a parameter."""
        if not S.run:
            return
        pos = len(S.nodes)
        made = set()
        for toks, _, _ in S.run:
            ops = [t for t in toks if not (isinstance(t, tuple) and t[0] == "guard")]
            if ops[0] in ("prep", "inject"):
                made.add(ops[1].key())
        # a single-factor measurement of a block nothing names after it is a destructive
        # readout -- inside a gadget only of a block the run made (qec2alg's `_Reads`)
        later: set = set()
        for i in range(len(S.run) - 1, -1, -1):
            toks, q, single = S.run[i]
            if single:
                letter, b, o = single
                if b.key() not in later and (top or b.key() in made):
                    gs = [t for t in toks if isinstance(t, tuple) and t[0] == "guard"]
                    S.run[i] = (gs + ["read", b, letter, "->", o], q, None)
                    toks = S.run[i][0]
            ops = [t for t in toks if not (isinstance(t, tuple) and t[0] == "guard")]
            if ops[0] != "decode":
                later |= {t.key() for t in toks if isinstance(t, _Blk)}
        params, plist, locs, llist, ext, elist = {}, [], {}, [], {}, []
        outs = {id(o): k for k, o in enumerate(S.outs)}
        lines, qlines = [], []
        for toks, q, _ in S.run:
            gs, body = [], []
            for t in toks:
                if isinstance(t, tuple) and t[0] == "guard":
                    ref = t[1]
                    if isinstance(ref, _Out):
                        if id(ref) not in outs:
                            raise NotLowered(f".qec line {q}: an outcome of an unclosed run")
                        gs.append(f"o{outs[id(ref)]}")
                    else:
                        if ref not in ext:
                            ext[ref] = len(elist)
                            elist.append(ref)
                        gs.append(f"e{ext[ref]}")
                elif isinstance(t, _Blk):
                    if t.key() in made:
                        if t.key() not in locs:
                            locs[t.key()] = len(llist)
                            llist.append((t.fam, t.idx))
                        body.append(f"l{locs[t.key()]}")
                    else:
                        if t.key() not in params:
                            params[t.key()] = len(plist)
                            plist.append((t.fam, t.idx))
                        body.append(f"p{params[t.key()]}")
                elif isinstance(t, _Out):
                    body.append(f"o{outs[id(t)]}")
                else:
                    body.append(t)
            text = " ".join(body)
            lines.append(f"if {' & '.join(gs)}: {text}" if gs else text)
            qlines.append(q)
        tid = "t" + hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]
        if tid not in self.prog.templates:
            self.prog.templates[tid] = Template(tid, tuple(lines), tuple(qlines), len(plist), len(llist),
                                                len(S.outs), len(elist))
        S.nodes.append(Seg(tid, tuple(plist), tuple(llist), tuple(elist)))
        # the run's outcomes, readable by later runs of this body and of those inside it
        for i, (lo, hi, ref) in enumerate(S.scope[-1]):
            if isinstance(ref, _Out) and id(ref) in outs:
                S.scope[-1][i] = (lo, hi, (S.nodes.sid, pos, outs[id(ref)]))
        S.run, S.outs, S.current = [], [], None


def _family(formal: str):
    """`Surface[m + rec] LogicData` -> 'LogicData' (a block family passed whole); else None."""
    m = _FAMILY.fullmatch(formal.strip())
    return m[1] if m else None


def _is_pred_var(v: str) -> bool:
    """FormalRV writes `if c:` as `for 0 <= rv_g < [c]` and its else as `for 0 <= rv_e < ...`."""
    return bool(re.fullmatch(r"rv_[ge]\d+", v))


def build(qec: layers.Stage, params: dict | None = None) -> Program:
    return Builder(qec).build(params)


# --------------------------------------------------------------------------- expanding


class _Exp:
    """Every instruction of an instance, in order, as qccd.gadget.algorithm would parse the
    flattened program: (word, args, out, guard), names as the run gives them."""

    def __init__(self, prog: Program, data_ones=(), reserved: bool = False):
        self.prog = prog
        self.reserved = reserved           # every slot taken (the schedule's view)
        self.gen: dict = {}
        self.n = 0
        self.rot = 0
        self.outs: dict = {}               # (body sid, node index) -> [outcome names]
        self.out: list = []
        self.data_ones = data_ones

    def fresh(self, p="m") -> str:
        self.n += 1
        return f"{p}{self.n}"

    def name(self, fam, idx, env, new=False) -> str:
        v = idx(env)
        if fam == "LogicData":
            return f"q{v}"
        if new:
            self.gen[v] = self.gen.get(v, -1) + 1
        g = self.gen.get(v, 0)
        return f"a{v}" + (f"_{g}" if g else "")

    def emit(self, word, args, out, guard: tuple):
        if word in UNGUARDABLE:
            guard = ()
        self.out.append((word, tuple(args), out, tuple(sorted(set(guard)))))

    def run(self) -> list:
        n = self.prog.n_data
        for i in range(n):
            self.emit("prep", [f"q{i}", "Z"], None, ())
        for i in sorted(self.data_ones):
            self.emit("x", [f"q{i}"], None, ())
        self.body(self.prog.root, dict(self.prog.params), ())
        return self.out

    def body(self, body: Body, env: dict, guard: tuple):
        for i, node in enumerate(body):
            if isinstance(node, Seg):
                self.seg(node, body.sid, i, env, guard)
            elif isinstance(node, Loop):
                for v in range(node.lo(env), node.hi(env)):
                    self.body(node.body, {**env, node.var: v}, guard)
            elif isinstance(node, Let):
                self.body(node.body, {**env, node.var: node.value(env)}, guard)
            elif isinstance(node, Pred):
                c = node.cond(env)
                if c > 1:
                    raise ValueError(f"a guard loop of {c} trips (line {node.qline})")
                if c or self.reserved:
                    self.body(node.body, env, guard)
            elif isinstance(node, Call):
                env2 = {f: e(env) for f, e in node.skeys + node.args}
                self.body(self.prog.body_of(node, env), env2, guard)
            elif isinstance(node, Rot):
                fs = [(p, self.name(fam, idx, env)) for p, fam, idx in node.factors]
                self.rotation(fs, node.k(env), node.sign, guard)

    def seg(self, node: Seg, sid, pos, env, guard):
        t = self.prog.templates[node.tid]
        names = {f"p{i}": self.name(fam, idx, env) for i, (fam, idx) in enumerate(node.params)}
        locs = {f"l{i}": (fam, idx) for i, (fam, idx) in enumerate(node.locals)}
        outs = [self.fresh() for _ in range(t.n_out)]
        names.update({f"o{i}": o for i, o in enumerate(outs)})
        for i, (bsid, bpos, k) in enumerate(node.ext):
            names[f"e{i}"] = self.outs[(bsid, bpos)][k]
        for line in t.lines:
            g = list(guard)
            if line.startswith("if "):
                cond, line = line[3:].split(": ", 1)
                g += [names[c.strip()] for c in cond.split("&")]
            w = line.split()
            word = w[0]
            if word in ("prep", "inject") and w[1] in locs:
                fam, idx = locs[w[1]]
                names[w[1]] = self.name(fam, idx, env, new=True)
            args = [names.get(a, a) for a in w[1:] if a != "->"]
            out = None
            if "->" in w:
                out = names[w[-1]]
                args = args[:-1]
            self.emit(word, args, out, tuple(g))
        self.outs[(sid, pos)] = outs

    def rotation(self, fs, k, sign, guard):
        """qec2alg._Interp.rotation, instruction for instruction."""
        def frame(g):
            for p, b in fs:
                self.emit(p.lower(), [b], None, g)
        if k <= 1:
            frame(guard)
            return
        self.rot += 1
        a = f"a{ROT_BASE + self.rot}"
        if k == 2:
            self.emit("prep", [a, "Y"], None, guard)
            if sign < 0:
                self.emit("z", [a], None, guard)
        elif k == 3:
            self.emit("inject", [a], None, guard)
            if sign > 0:
                self.emit("x", [a], None, guard)
        else:
            self.emit("prep", [a, f"R{k - 1}" + ("" if sign > 0 else "dg")], None, guard)
        s_, r_ = self.fresh(), self.fresh()
        letters = [x for p, b in fs for x in (p, b)]
        self.emit("ppm", letters + ["Z", a], s_, guard)
        self.emit("decode", [b for _, b in fs] + [a], None, guard)
        self.emit("read", [a, "X"], r_, guard)
        self.emit("decode", [a], None, guard)
        frame(guard + (r_,))
        self.rotation(fs, k - 1, sign, guard + (s_,))


def expand(prog: Program, data_ones=(), reserved: bool = False) -> list:
    return _Exp(prog, data_ones, reserved).run()


# --------------------------------------------------------------------------- comparing


def normalize_alg(alg) -> list:
    """qccd.gadget.algorithm's instructions as `expand` writes them."""
    out = []
    for ins in alg.instructions:
        w = ins.text.split(": ", 1)[1] if ins.text.startswith("if ") else ins.text
        w = w.split("->")[0].split()
        word, args = w[0], list(ins.blocks) if False else w[1:]
        guard = tuple(sorted(set(ins.cond.names))) if ins.cond is not None else ()
        if ins.cond is not None and any(c != 0 or len(ns) != 1 for c, ns in ins.cond.parities):
            guard = ("?",) + guard
        out.append((word, tuple(args), ins.cvar, guard))
    return out


def canonical(seq: list) -> list:
    """Rename every block and outcome by first appearance, so two runs compare as text."""
    ren: dict = {}

    def r(n):
        if n is None:
            return None
        if re.fullmatch(r"[A-Z]+\d*(dg)?|[XYZ]", n):
            return n
        if n not in ren:
            ren[n] = f"n{len(ren)}"
        return ren[n]
    return [(w, tuple(r(a) for a in args), r(o), tuple(sorted(r(g) for g in gd))) for w, args, o, gd in seq]


def compare(prog: Program, alg, data_ones=()) -> dict:
    """Is `prog` expanded the same instruction list as qec2alg's lowering `alg` (parsed)?"""
    mine = canonical(expand(prog, data_ones))
    theirs = canonical(normalize_alg(alg))
    first = next((i for i, (a, b) in enumerate(zip(mine, theirs)) if a != b), None)
    if first is None and len(mine) != len(theirs):
        first = min(len(mine), len(theirs))
    return {"same": first is None, "mine": len(mine), "theirs": len(theirs), "first_difference": first,
            "at": (mine[first:first + 6], theirs[first:first + 6]) if first is not None else None}


# --------------------------------------------------------------------------- counting


def count(prog: Program) -> Counter:
    """Every instruction the run executes, by word, without expanding it.  A slot is
    counted as reserved: the schedule's view, every predicate taken."""
    return total(prog, lambda tid: prog.templates[tid].kinds,
                 lambda k, sign, letters: _rot_count(k, sign, letters))


def duration_ns(prog: Program, span_ns: dict, rot_ns) -> int:
    """The run's time on the machine when every instance runs after the one before it:
    `span_ns[tid]` a template's compiled duration, `rot_ns(k, sign, n_factors)` a rotation
    chain's (every level reserved).  Exact integers: RSA-2048 runs past 2^53 microseconds."""
    return total(prog, lambda tid: Counter(ns=span_ns[tid]),
                 lambda k, sign, letters: Counter(ns=rot_ns(k, sign, len(letters))))["ns"]


def total(prog: Program, seg_value, rot_value) -> Counter:
    """A sum over every instance the run executes, without expanding it: `seg_value(tid)`
    for a template's instance, `rot_value(k, sign, letters)` for a rotation chain.  A body is
    summed once per distinct value of what its sum depends on (`_deps`); a loop whose body's
    sum does not depend on its variable multiplies, and one whose sum is affine in it (a
    rotation chain as deep as the distance between two qubits) is an arithmetic series
    (checked at four points, else summed term by term)."""
    memo: dict = {}

    def body_count(body: Body, env: dict) -> Counter:
        total = Counter()
        for node in body:
            total.update(node_count(node, env))
        return total

    def node_count(node, env) -> Counter:
        if isinstance(node, Seg):
            return seg_value(node.tid)
        if isinstance(node, Pred):
            return body_count(node.body, env)
        if isinstance(node, Let):
            return body_count(node.body, {**env, node.var: node.value(env)})
        if isinstance(node, Loop):
            lo, hi = node.lo(env), node.hi(env)
            n = hi - lo
            if n <= 0:
                return Counter()
            at = lambda v: body_count(node.body, {**env, node.var: v})  # noqa: E731
            if node.var not in _deps(node.body):
                return _scale(at(lo), n)
            if n <= 4:
                total = Counter()
                for v in range(lo, hi):
                    total.update(at(v))
                return total
            c0, c1, cm, cl = at(lo), at(lo + 1), at(lo + n // 2), at(hi - 1)
            keys = set(c0) | set(c1) | set(cm) | set(cl)
            d = {k: c1[k] - c0[k] for k in keys}
            if all(cm[k] == c0[k] + d[k] * (n // 2) and cl[k] == c0[k] + d[k] * (n - 1) for k in keys):
                return Counter({k: n * c0[k] + d[k] * n * (n - 1) // 2 for k in keys})
            total = Counter()
            for v in range(lo, hi):
                total.update(at(v))
            return total
        if isinstance(node, Call):
            key = (node.gadget, tuple((f, e(env)) for f, e in node.skeys), node.fam)
            if key not in memo:
                memo[key] = body_count(prog.body_of(node, env), {f: e(env) for f, e in node.skeys})
            return memo[key]
        if isinstance(node, Rot):
            return rot_value(node.k(env), node.sign, [p for p, _, _ in node.factors])
        return Counter()
    return body_count(prog.root, dict(prog.params))


def _scale(c: Counter, n: int) -> Counter:
    return Counter({k: v * n for k, v in c.items()})


_DEPS: dict = {}


def _deps(body: Body) -> frozenset:
    """The variables a body's instruction count depends on (loop bounds, structural keys,
    rotation depths)."""
    if body.sid in _DEPS:
        return _DEPS[body.sid]
    out = set()
    for n in body:
        if isinstance(n, Loop):
            out |= n.lo.vars | n.hi.vars | (_deps(n.body) - {n.var})
        elif isinstance(n, Pred):
            out |= _deps(n.body)
        elif isinstance(n, Let):
            inner = _deps(n.body)
            out |= (inner - {n.var}) | (n.value.vars if n.var in inner else set())
        elif isinstance(n, Call):
            for _, e in n.skeys:
                out |= e.vars
        elif isinstance(n, Rot):
            out |= n.k.vars
    _DEPS[body.sid] = frozenset(out)
    return _DEPS[body.sid]


def _rot_count(k: int, sign: int, letters: list) -> Counter:
    """`_Exp.rotation`'s instructions, every level reserved, in closed form."""
    c = Counter()
    if k > 1:
        levels = k - 1                                  # k, k-1, ..., 2
        c["ppm"] += levels
        c["read"] += levels
        c["decode"] += 2 * levels
        c["inject"] += 1 if k >= 3 else 0
        c["x"] += 1 if (k >= 3 and sign > 0) else 0
        c["z"] += 1 if sign < 0 else 0
        c["prep"] += 1 + max(0, k - 3)                  # Y at 2, R(j-1) for every j >= 4
    for p in letters:                                   # a frame per level, and the last
        c[p.lower()] += (k - 1 if k > 1 else 0) + 1
    return c
