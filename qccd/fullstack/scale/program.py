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
from ..qec2alg import _LOGICAL_PREP, NotLowered, ROT_BASE, _untable, logical_prepares, rounds_of
from ..sysprog import split_top
from ...gadget.algorithm import parse_bit
from .expr import Expr, ExprError

__all__ = ["Program", "Builder", "build", "Seg", "Loop", "Pred", "Let", "Call", "Rot", "Template",
           "expand", "count", "total", "duration_ns", "normalize_alg", "compare"]

_FACTOR = re.compile(r"([XYZ])\[(\w+)\[([^\[\]]+)\]\[0\]\]")
_LOOP = re.compile(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?):\s*$")
_BAND = re.compile(r"\bc\[([^\[\]]+)\)")
_SID = itertools.count(1)
_FAMILY = re.compile(r"\w+\[.*\]\s+(\w+)")

#: what a guarded call cannot gate: it runs either way (qccd.gadget.algorithm.UNGUARDABLE)
UNGUARDABLE = ("prep", "inject", "se", "store", "decode", "bit")


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
    ext: tuple           # (OutRef | _Rec | _MXor, ...) per e<i>
    #: per o<i>, the records it fills, (lo, hi) in its call's own numbering (FormalRV addresses
    #: a call's records at its `rv_recbase` plus that): later calls read them by address
    bands: tuple = ()
    #: records the archive writes and no instruction measures -- a copy, a conjunction, a
    #: source slot: (table, lo, hi, guard text over o<i>/e<i>), table "rec" or "src"
    writes: tuple = ()


@dataclass
class Loop:
    var: str
    lo: Expr
    hi: Expr
    body: Body
    qline: int = 0
    #: the iterations may run at once, on different districts: FormalRV says so (`__par`),
    #: or they touch disjoint blocks (`independent`)
    par: bool = False


@dataclass
class Pred:
    """A slot that runs only when `cond` > 0 -- a decision on data, not on an outcome: reserved
    either way.  `structural`: a decision on the program's own structure (no table read -- a
    loop variable against a bound, `if i ≥ 1`), so it is taken only where it holds, in every
    count; its body is lowered where it holds."""
    cond: Expr
    body: Body
    qline: int = 0
    structural: bool = False


@dataclass
class When:
    """A body that runs only when an outcome-made bit holds (`cond`, a formula over outcome
    references): a gadget call or a loop under FormalRV's `when` (Gidney 2025's lookups).  It
    reserves its places either way, as a guarded call does in the `.alg`; expanded, every
    instruction of it carries the guard."""
    cond: object
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


@dataclass
class Cat:
    """A Pauli product over a register, measured by cat states across districts
    (qccd/gadget/leaves/catbench.py, `ppm` over more blocks than a bench takes): the explicit
    `factors`, and `letter` on `fam[idx]` for every `var` in [lo, hi) where `cond` > 0 (a
    decision on data -- a constant's bit, a table's).  Lattice surgery in full: `rounds`
    syndrome rounds on every block of the merge.  The cats are made in parallel, a bench a
    block, so its time does not grow with the register.  Its outcome is outcome 0 of its node:
    an OutRef names it as it names a Seg's."""
    factors: tuple       # ((letter, family, Expr), ...)
    var: str
    lo: Expr
    hi: Expr
    cond: Expr
    letter: str
    fam: str
    idx: Expr
    rounds: int = 1
    qline: int = 0
    band: tuple = None   # (lo, hi): the records its outcome fills

    def blocks(self, env: dict) -> int:
        """The most blocks it names (every data slot taken)."""
        return len(self.factors) + max(0, self.hi(env) - self.lo(env))


# An OutRef is (body sid, node index, outcome index): outcome o<i> of the Seg (or Cat) at that
# index of that body, in the body's current run -- a frame reads a merge of its own gadget call.


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
            rep = {**self.params, **bind(call.skeys + call.args, env)}
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
    """An outcome of the open run, and the records it fills (texts, in its call's numbering)."""
    __slots__ = ("k", "band")

    def __init__(self, k, band=None):
        self.k, self.band = k, band


class _Rec:
    """A record read by its address, not by the merge that wrote it: one another call wrote
    (`table` "rec", `idx` in this call's own numbering -- its `rv_recbase` is added), or a
    source slot (`table` "src", `idx` absolute).  Resolved when the program is expanded."""
    __slots__ = ("table", "idx")

    def __init__(self, table: str, idx: Expr):
        self.table, self.idx = table, idx


class _MXor:
    """The parity of source slots `idx` for `var` < `n` where `cond` > 0 -- a mask of data
    (GE2021's masked phase parity, the mask a table's bits)."""
    __slots__ = ("var", "n", "idx", "cond")

    def __init__(self, var: str, n: Expr, idx: Expr, cond: Expr):
        self.var, self.n, self.idx, self.cond = var, n, idx, cond


# A classical bit the archive computes is an XOR of conjunctions of parities of outcomes: a
# list of terms, a term `((const, (atom, ...)), ...)` -- a parity holds when const plus its
# atoms is odd, a term when all its parities do.  One term is an `.alg` guard (`a ^ b & c`); a
# Pauli frame on several terms is that Pauli once per term (P^(t1 + t2) = P^t1 P^t2).  The
# atoms are outcome references while lowering, outcome names when expanding.

def _sop_norm(terms) -> list:
    """The terms that are one parity merged into one; what never holds dropped; a term twice
    cancelled."""
    c, ns, multi = 0, [], []
    for t in terms:
        t = tuple(p for p in t if p != (1, ()))            # a parity that always holds
        if any(p == (0, ()) for p in t):
            continue                                       # one that never does: the term is 0
        if not t:
            c ^= 1                                         # the term always holds
        elif len(t) == 1:
            c ^= t[0][0]
            for a in t[0][1]:
                ns.remove(a) if a in ns else ns.append(a)
        elif t in multi:
            multi.remove(t)
        else:
            multi.append(t)
    head = [((c, tuple(ns)),)] if ns else ([()] if c else [])
    return head + multi


def _sop_xor(*fs) -> list:
    return _sop_norm([t for f in fs for t in f])


def _sop_and(*fs) -> list:
    out = [()]
    for f in fs:
        out = [a + b for a in out for b in f]
    return _sop_norm(out)


def _sop_not(f) -> list:
    return _sop_xor(f, [()])


def _sop_literals(f) -> list:
    """The same bit as an XOR of conjunctions of atoms (each parity multiplied out): what an
    `.alg` `bit` line writes.  Each conjunction a tuple of atoms, `()` the one of none."""
    out: list = []
    for term in f:
        prods = [()]
        for c, atoms in term:
            alts = [(a,) for a in atoms] + ([()] if c else [])
            prods = [p + tuple(a for a in alt if a not in p) for p in prods for alt in alts]
        for p in prods:
            out.remove(p) if p in out else out.append(p)
    return out


def _bit_text(f, name) -> str:
    """`(a & b) ^ c ^ 1`, every atom written by `name`."""
    lits = _sop_literals(f)
    return " ^ ".join(("(" + " & ".join(name(a) for a in p) + ")") if p else "1" for p in lits) or "0"


class _F:
    """A classical bit while lowering: an XOR of conjunctions of parities of refs."""
    __slots__ = ("terms",)

    def __init__(self, terms):
        self.terms = list(terms)


def _formula(ref) -> _F:
    if isinstance(ref, _F):
        return ref
    if isinstance(ref, tuple) and len(ref) == 2 and ref[0] == "not":
        return _F(_sop_not(_formula(ref[1]).terms))
    return _F([((0, (ref,)),)])


def _fand(fs) -> _F:
    return _F(_sop_and(*[_formula(f).terms for f in fs]))


def _fxor(fs) -> _F:
    return _F(_sop_xor(*[_formula(f).terms for f in fs]))


class _State:
    """One body being lowered: the open run, the merges whose outcomes later frames read."""

    def __init__(self, scope: list):
        self.nodes = Body()
        self.writes: list = []           # [(table, lo text, hi text, ref)]: records the archive writes
        self.closed: dict = {}           # id(_Out) of a closed run -> its OutRef
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
        self.cur: list = []              # the gadgets being lowered, innermost last
        self.loops: list = [[]]          # per gadget being lowered, its loop variables in scope
        #: survey mode (`build(..., survey=True)`): a statement with no lowering is recorded
        #: here, by kind, and skipped -- never in a program anyone runs
        self.survey: dict | None = None

    def _structural(self) -> dict:
        """gadget -> its formals that set a loop's trip count, here or in what it calls."""
        uses, passes, guards, calls = {}, {}, {}, {}
        for name, g in self.gadgets.items():
            fs = set(self.prog.formals[name])
            b, p, gd, cl = set(), [], set(), set()

            def scan(body):
                for s in body:
                    raw = _untable(s.text.strip())
                    lp = _LOOP.fullmatch(raw)
                    if lp and not _is_pred_var(lp[2]):
                        b.update((Expr(lp[1]).vars | Expr(lp[3]).vars) & fs)
                    elif lp and not _reads_table(Expr(lp[3]).node):
                        gd.update(Expr(lp[3]).vars & fs)
                    call = re.fullmatch(r"([\w!]+)\((.*)\)", raw)
                    if call and call[1] in self.gadgets:
                        cl.add(call[1])
                        for f, a in zip(self.headers[call[1]], split_top(call[2], ",")):
                            if _family(f) is None:
                                p.append((call[1], f.strip(), Expr(a).vars & fs))
                    scan(s.children)
            scan(g.body)
            uses[name], passes[name], guards[name], calls[name] = b, p, gd, cl
        # A RECURSIVE gadget ends where a guard says so: the parameters its guards read (those
        # that read no table: a depth, not a constant's bit) set its structure too, or the
        # recursion would be one body calling itself
        for name in _recursive(calls):
            uses[name] |= guards[name]
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
            self.cur.append(gadget)
            self.loops.append([])
            try:
                self.prog.bodies[key] = self.body(self.gadgets[gadget].body, rep, famap, [], top=False)
            finally:
                self.cur.pop()
                self.loops.pop()

    def _holds(self, cond: Expr, rep: dict, known: set, reach: int = 64) -> dict:
        """`rep` moved, one variable at a time, to the nearest values at which `cond` >= 1 (the
        loop variables it reads, upward from where they stand); `rep` itself if it holds there or
        nowhere within `reach`."""
        if cond(rep) >= 1:
            return rep
        free = sorted(v for v in cond.vars - known if isinstance(rep.get(v), int))
        for v in free:
            for d in range(1, reach + 1):
                moved = {**rep, v: rep[v] + d}
                if cond(moved) >= 1:
                    return moved
        return rep

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
        if self.survey is None:
            return self._stmt(s, S, rep, famap, top, guards)
        # survey mode: a statement with no lowering is recorded, by what stopped it, and skipped
        try:
            return self._stmt(s, S, rep, famap, top, guards)
        except NotLowered as exc:
            why = re.sub(r"^\.qec line \d+: ", "", str(exc))
            self.survey.setdefault(re.sub(r"[\d']+.*$", "", why).strip()[:60] or why[:60], []).append(
                (s.line, s.text.strip()[:160]))

    def _stmt(self, s, S: _State, rep: dict, famap: dict, top: bool, guards: list) -> None:
        raw = _untable(s.text.strip())
        if raw == "skip":
            return
        lp = _LOOP.fullmatch(raw)
        if lp and guards:
            return self.when(s, S, rep, famap, top, guards)
        if lp:
            var, lo, hi = lp[2], Expr(lp[1]), Expr(lp[3])
            if _is_pred_var(var):
                if lo.text != "0":
                    raise NotLowered(f".qec line {s.line}: a guard loop from {lo.text}")
                # a decision on STRUCTURE (a recursion's depth, the program's fixed parameters) is
                # decided here, once per instance; only a decision on data is a slot
                known = set(self.prog.params) | (self.structural.get(self.cur[-1], set()) if self.cur else set())
                if hi.vars <= known:
                    if hi(rep) >= 1:
                        for c in s.children:
                            self.stmt(c, S, {**rep, var: 0}, famap, top, guards)
                    return
                self.close(S, top)
                if _reads_table(hi.node) or not hi.vars <= known | set(self.loops[-1]):
                    # (on a table's bits, or a classical argument's -- `if bit b of cst`: data)
                    S.nodes.append(Pred(hi, self.body(s.children, {**rep, var: 0}, famap, S.scope, top), s.line))
                    return
                # a decision on a loop variable (`for i: if i ≥ 1: interior(i) else: first`): taken
                # only where it holds, and lowered where it holds -- at the representative it may
                # not, and a body lowered where its own `requires` fails can merge two blocks
                # (`(i - 1)*3 + 2` and `i*3 + 2` are both 2 at i = 0)
                where = self._holds(hi, rep, known)
                S.nodes.append(Pred(hi, self.body(s.children, {**where, var: 0}, famap, S.scope, top), s.line,
                                    structural=True))
                return
            self.close(S, top)
            lo_v = lo(rep)
            if hi.text == f"{lo.text} + 1" or (not lo.vars and not hi.vars and hi(rep) - lo_v == 1):
                S.nodes.append(Let(var, lo, self.body(s.children, {**rep, var: lo_v}, famap, S.scope, top)))
                return
            self.loops[-1].append(var)
            try:
                inner = self.body(s.children, {**rep, var: lo_v}, famap, S.scope, top)
            finally:
                self.loops[-1].pop()
            S.nodes.append(Loop(var, lo, hi, inner, s.line, par=var.endswith("__par")))
            return
        if raw.startswith("when ") and raw.endswith(":"):
            out = self._read(raw, rep, S, s.line)
            if raw.startswith("when ¬") or raw.startswith("when !"):
                out = _formula(("not", out))       # the .alg's `if !m:`
            for c in s.children:
                self.stmt(c, S, rep, famap, top, guards + [out])
            return
        if raw.startswith("parity ") and "<-" in raw:
            # a classical bit computed from outcomes (and a table's bits): the archive's work, no
            # instruction -- a copy or a conjunction is folded into the guards that read it, and
            # written into the table of records for the calls after this one
            dst, src = raw.split("<-", 1)
            try:
                f = self._parity(src, rep, S, s.line)
            except NotLowered:
                if self.survey is None:
                    raise
                self.survey.setdefault("parity", []).append((s.line, raw[:160]))
                return
            band, (lo, hi) = self._band(dst, rep, s.line), self._band_text(dst, s.line)
            S.scope[-1].append((band[0], band[1], f))
            S.writes.append(("rec", lo, hi, f))
            return
        if raw.startswith("source ") and "<-" in raw:
            # a record copied into a source slot, which later calls read by its number
            dst, src = raw.split("<-", 1)
            k = self._band_text(dst, s.line)[0]
            S.writes.append(("src", k, f"({k}) + 1", self._read(src, rep, S, s.line)))
            return
        call = re.fullmatch(r"([\w!]+)\((.*)\)", raw)
        if call and call[1] in self.gadgets:
            if guards:
                return self.when(s, S, rep, famap, top, guards)
            self.close(S, top)
            S.nodes.append(self.call(call[1], split_top(call[2], ","), rep, famap, s.line))
            return
        self.leaf(s, raw, S, rep, famap, top, guards)

    def when(self, s, S: _State, rep: dict, famap: dict, top: bool, guards: list) -> None:
        """A call or a loop under guards: the run so far closes, and the statement becomes a
        body of its own under the conjunction of the guards."""
        f = _fand(guards)
        if len(f.terms) != 1:
            raise NotLowered(f".qec line {s.line}: a call or loop under a guard that is "
                             f"{'never true' if not f.terms else 'a parity of conjunctions'}")
        self.close(S, top)
        # (outcomes of the run just closed are named by their place in this body now)
        f = _F([tuple((c, tuple(S.closed.get(id(r), r) for r in rs)) for c, rs in t) for t in f.terms])
        S.nodes.append(When(f, self.body([s], rep, famap, S.scope, top), s.line))

    def call(self, name: str, args: list, rep: dict, famap: dict, line: int) -> Call:
        formals = self.headers[name]
        if len(formals) != len(args):
            raise NotLowered(f".qec line {line}: {name} takes {len(formals)} arguments, given {len(args)}")
        # (a gadget sees the program's parameters: FormalRV's are global -- Gidney 2025's
        # gadgets read `accBase` and the rest without being passed them)
        skeys, rest, fam2, rep2 = [], [], {}, dict(self.prog.params)
        for f, a in zip(formals, args):
            fam = _family(f)
            if fam is not None:
                fam2[fam] = famap.get(a.strip(), a.strip())
                continue
            e = Expr(a)
            structural = f.strip() in self.structural[name]
            try:
                rep2[f.strip()] = e(rep)
            except ExprError:
                if structural:
                    raise
                # data from a table known only by its bounds: the body is lowered without it
            (skeys if structural else rest).append((f.strip(), e))
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
            if new:
                # (a data block exists from the start, generation 0: a re-preparation is 1)
                self.gen[("d", v)] = self.gen.get(("d", v), 0) + 1
            return _Blk(fam, e, self.gen.get(("d", v), 0), v)
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
        if raw.startswith("requires "):
            return                       # a gadget's precondition: a contract, not an operation
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
            elif m[1] == "|AutoCCZ>":
                # one |AutoCCZ> state on nine new blocks, in FormalRV's site order: the CCZ
                # factory's (qccd/gadget/leaves/ccz.py, msccz)
                rng = re.search(r"->\s*\w+\[([^,\[\]]+),([^\[\]]+)\)", raw)
                lo, hi = Expr(rng[1]), Expr(rng[2])
                if hi(rep) - lo(rep) != 9:
                    raise NotLowered(f".qec line {q}: an |AutoCCZ> state is nine sites, not {hi(rep) - lo(rep)}")
                self.gen[b.val] = self.gen.get(b.val, 0) - 1          # (b was counted above)
                bs = [self.blk(m[2], f"{rng[1]} + {i}", rep, famap, new=True) for i in range(9)]
                S.run.append((g + ["autoccz"] + bs, q, None))
            elif self.survey is not None:
                self.survey.setdefault(f"magic {m[1]}", []).append((q, raw[:160]))
                S.run.append((g + ["inject", b], q, None))
            else:
                raise NotLowered(f".qec line {q}: no gadget prepares {m[1]} yet")
            return
        if kd == "extract":
            # lattice surgery in full: the merged code's rounds, r on every patch of the merge
            # (qec2alg, "extract")
            if S.current is None:
                raise NotLowered(f".qec line {q}: an extraction outside a merge group")
            mr = re.search(r"rounds=(\d+)", raw)
            if isinstance(S.current[0], Cat):
                S.current[0].rounds = int(mr[1]) if mr else 1      # every block's, in parallel
                return
            for b in S.current[1]:
                for k in rounds_of(int(mr[1]) if mr else 1):
                    S.run.append((g + ["se", b, str(k)], q, ("se", S.current[0])))
            return
        if kd == "prepare" and _LOGICAL_PREP.match(raw):
            # a logical block prepared in |+> or |0> (a school's preparation), as qec2alg does
            m = re.match(r"prepare\s+\|[+0]>\s*->\s*\w+\[(.*)$", raw.strip())
            rest = m[1]
            lo = split_top(rest[:-1], ",")[0] if rest.endswith(")") else rest[:-4]
            lo_v = Expr(lo)(rep)
            for (fam, v), basis in logical_prepares(raw, lambda e: Expr(e)(rep)):
                b = self.blk(fam, f"({lo}) + {v - lo_v}", rep, famap, new=True)
                S.run.append((g + ["prep", b, basis], q, None))
            return
        if kd in ("alloc", "prepare", "split"):
            return
        if kd == "merge":
            prod = raw.split("= merge", 1)[1].split(" with ")[0]
            if " PROD " in prod:
                # a product over a register (GE2021's masked preparation and unary lookup): up to
                # 2,049 blocks, measured at once by cat states across districts -- one node
                if guards:
                    raise NotLowered(f".qec line {q}: a product over a register under a guard")
                head, tail = prod.split(" * PROD ", 1)
                m = _PROD.fullmatch(tail.strip())
                if not m:
                    raise NotLowered(f".qec line {q}: a product over a register QCCD cannot read: "
                                     f"{tail[:100]!r}")
                fs = self.factors(head, rep, famap)
                band = self._band(raw.split("->")[-1], rep, q)
                lo_t, hi_t = self._band_text(raw.split("->")[-1], q)
                self.close(S, top)
                node = Cat(tuple((p, b.fam, b.idx) for p, b in fs), m["var"], Expr(m["lo"]),
                           Expr(m["hi"]), Expr(m["cond"]), m["letter"],
                           famap.get(m["fam"], m["fam"]), Expr(m["idx"]), qline=q,
                           band=(Expr(lo_t), Expr(hi_t)))
                S.scope[-1].append((band[0], band[1], (S.nodes.sid, len(S.nodes), 0)))
                S.nodes.append(node)
                S.current = (node, [], True)       # its rounds and decoding are its own
                return
            fs = self.factors(prod, rep, famap)
            band = self._band(raw.split("->")[-1], rep, q)
            o = _Out(len(S.outs), self._band_text(raw.split("->")[-1], q))
            S.outs.append(o)
            S.scope[-1].append((band[0], band[1], o))
            toks = g + ["ppm"] + [x for p, b in fs for x in (p, b)] + ["->", o]
            S.run.append((toks, q, ("single", fs[0][0], fs[0][1], o) if len(fs) == 1 else None))
            S.current = (o, [b for _, b in fs], False)
            return
        if raw.startswith("measure ") and "->" in raw:
            # a destructive measurement of one block (GE2021's unlookup and readout): a readout
            # at a post office, its outcome the band later frames read
            fs = self.factors(raw.split("->")[0], rep, famap)
            if len(fs) != 1:
                raise NotLowered(f".qec line {q}: a measurement of {len(fs)} blocks")
            band = self._band(raw.split("->")[-1], rep, q)
            o = _Out(len(S.outs), self._band_text(raw.split("->")[-1], q))
            S.outs.append(o)
            S.scope[-1].append((band[0], band[1], o))
            S.run.append((g + ["read", fs[0][1], fs[0][0], "->", o], q, None))
            S.current = (o, [fs[0][1]], False)
            return
        if kd == "decode":
            if S.current is None:
                raise NotLowered(f".qec line {q}: a decode outside a merge group")
            if not S.current[2]:
                S.run.append((g + ["decode"] + list(S.current[1]), q, None))
                S.current = (S.current[0], S.current[1], True)
            return
        if kd == "frame_on":
            head = raw[:_FACTOR.search(raw).start()]
            out = _fxor([self._read(part, rep, S, q) for part in split_top(head[len("frame on "):], ",")])
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
        if self.survey is not None:
            self.survey.setdefault(raw.split()[0] if raw.split() else raw, []).append((q, raw[:160]))
            return
        raise NotLowered(f".qec line {q}: no gadget lowering for {raw[:100]!r}")

    def _band(self, text: str, rep: dict, line: int) -> tuple:
        one = re.search(r"\bc\[([^\[\]]+)\]", text)      # one record: `c[i]` (its index may hold parentheses)
        if one and len(split_top(one[1], ",")) == 1:
            one = one
        else:
            one = None
        m = None if one else _BAND.search(text)
        if m:
            lo, hi = split_top(m[1], ",")
            return (Expr(lo)(rep), Expr(hi)(rep))
        if one:
            i = Expr(one[1])(rep)
            return (i, i + 1)
        raise NotLowered(f".qec line {line}: no c[..) band in {text[:80]!r}")

    def _band_text(self, text: str, line: int) -> tuple:
        """The first `c[lo,hi)` (or `c[i]`) of `text`, as index texts."""
        one = re.search(r"\bc\[([^\[\]]+)\]", text)
        if one and len(split_top(one[1], ",")) == 1:
            return one[1], f"({one[1]}) + 1"
        m = _BAND.search(text)
        if m:
            lo, hi = split_top(m[1], ",")
            return lo, hi
        raise NotLowered(f".qec line {line}: no c[..) band in {text[:80]!r}")

    def _read(self, text: str, rep: dict, S: _State, line: int):
        """The bit `text` names: a source slot, or a record -- this body's merge, or by its
        address one another call wrote."""
        if re.search(r"\bsource\s+c\[", text):
            return _Rec("src", Expr(self._band_text(text.split("source", 1)[1], line)[0]))
        return self._owner(self._band(text, rep, line), S, line, self._band_text(text, line)[0])

    def _parity(self, src: str, rep: dict, S: _State, line: int) -> _F:
        """`⊕ v < n . (t ∧ t ...)`: a copy, a conjunction, or a parity of source slots masked
        by data, as a formula."""
        m = re.fullmatch(r"\s*⊕\s*(\w+)\s*<\s*(.+?)\s*\.\s*\((.*)\)\s*", src)
        if not m:
            raise NotLowered(f".qec line {line}: a parity QCCD cannot read: {src[:100]!r}")
        var, terms = m[1], [t.strip() for t in split_top(m[3], "∧")]
        n = Expr(m[2])(rep)
        srcs = [t for t in terms if re.match(r"source\s+c\[", t)]
        if srcs:
            # ⊕ i < W . (source c[k + i] ∧ mask): the slots the data selects
            rest = [t for t in terms if t not in srcs]
            if len(srcs) != 1 or len(rest) != 1:
                raise NotLowered(f".qec line {line}: a parity of {len(terms)} terms over {n} values")
            k = self._band_text(srcs[0].split("source", 1)[1], line)[0]
            return _formula(_MXor(var, Expr(m[2]), Expr(k), Expr(rest[0])))
        if n > 64:
            raise NotLowered(f".qec line {line}: a parity over {n} values")
        fs = []
        for v in range(n):
            # one value of the loop: the conjunction of its records (a constant 0 drops it)
            refs, never = [], False
            for t in terms:
                if re.search(r"\bc\[", t):
                    # (the address with this value of the parity's own variable written in: that
                    # variable is bound nowhere when the record is read)
                    tv = re.sub(rf"\b{re.escape(var)}\b", f"({v})", t)
                    refs.append(self._read(tv, rep, S, line))
                    continue
                try:
                    never = never or Expr(t)({**rep, var: v}) == 0
                except ExprError:
                    raise NotLowered(f".qec line {line}: a parity term on data: {t!r}") from None
            if not never:
                fs.append(_fand(refs))
        return fs[0] if len(fs) == 1 else _fxor(fs)

    def _owner(self, band, S: _State, line: int, text: str | None = None):
        for frame in reversed(S.scope):
            for lo, hi, ref in reversed(frame):
                if lo <= band[0] and band[1] <= hi:
                    return ref
        if text is not None:
            # a record another gadget call wrote (GE2021's spacelike trips: trip k's outputs are
            # trip k+1's inputs): read by its address in the table of records
            return _Rec("rec", Expr(text))
        raise NotLowered(f".qec line {line}: no merge in scope wrote c{list(band)}")

    # ---- closing a run into a template ---------------------------------------------------
    def close(self, S: _State, top: bool) -> None:
        """The open run becomes a `Seg`: its lines with every name made a parameter."""
        if not S.run and not S.writes:
            return
        pos = len(S.nodes)
        made = set()
        for toks, _, _ in S.run:
            ops = [t for t in toks if not (isinstance(t, tuple) and t[0] == "guard")]
            if ops[0] in ("prep", "inject"):
                made.add(ops[1].key())
            elif ops[0] == "autoccz":
                made |= {t.key() for t in ops[1:]}
        # a single-factor measurement of a block nothing names after it is a destructive
        # readout -- inside a gadget only of a block the run made (qec2alg's `_Reads`)
        later: set = set()
        read: set = set()
        for i in range(len(S.run) - 1, -1, -1):
            toks, q, meta = S.run[i]
            if meta and meta[0] == "se":
                continue                 # a merge's own rounds are part of it, not a later use
            if meta and meta[0] == "single":
                _, letter, b, o = meta
                if b.key() not in later and (top or b.key() in made):
                    gs = [t for t in toks if isinstance(t, tuple) and t[0] == "guard"]
                    S.run[i] = (gs + ["read", b, letter, "->", o], q, None)
                    toks = S.run[i][0]
                    read.add(id(o))
            ops = [t for t in toks if not (isinstance(t, tuple) and t[0] == "guard")]
            if ops[0] != "decode":
                later |= {t.key() for t in toks if isinstance(t, _Blk)}
        # a block read out destructively is consumed: no rounds on it
        S.run = [it for it in S.run if not (it[2] and it[2][0] == "se" and id(it[2][1]) in read)]
        params, plist, locs, llist, ext, elist = {}, [], {}, [], {}, []
        outs = {id(o): k for k, o in enumerate(S.outs)}
        lines, qlines = [], []
        def tok(ref, q):
            if isinstance(ref, _Out):
                if id(ref) not in outs:
                    raise NotLowered(f".qec line {q}: an outcome of an unclosed run")
                return f"o{outs[id(ref)]}"
            if ref not in ext:
                ext[ref] = len(elist)
                elist.append(ref)
            return f"e{ext[ref]}"

        def term_text(term, q) -> str:
            """One conjunction as an `.alg` guard over o<i> / e<i>."""
            out = []
            for c, rs in term:
                names = [tok(r, q) for r in rs]
                out.append(f"!{names[0]}" if len(names) == 1 and c else " ^ ".join(names + (["1"] if c else [])))
            return " & ".join(out)

        def sop_text(f, q) -> str:
            # (a write's value: its terms, `;` between; "" never, "1" always)
            return " ; ".join(term_text(t, q) or "1" for t in _formula(f).terms)

        bits: dict = {}
        for toks, q, _ in S.run:
            gref = [t[1] for t in toks if isinstance(t, tuple) and t[0] == "guard"]
            terms = _fand(gref).terms if gref else [()]
            word = next(t for t in toks if not (isinstance(t, tuple) and t[0] == "guard"))
            if len(terms) != 1 and word not in ("x", "y", "z"):
                if not terms:
                    raise NotLowered(f".qec line {q}: '{word}' under a guard that is never true")
                # a guard that is an XOR of conjunctions (the spacelike adder's adaptive bases):
                # the archive computes it first, as a bit the line's guard then reads
                key = tuple(term_text(t, q) for t in terms)
                if key not in bits:
                    o = _Out(len(S.outs))
                    S.outs.append(o)
                    outs[id(o)] = o.k
                    bits[key] = o
                    bits[tuple(term_text(t, q) for t in _sop_not(terms))] = ("not", o)
                    lines.append(f"bit {_bit_text(terms, lambda r: tok(r, q))} -> o{o.k}")
                    qlines.append(q)
                ref = bits[key]
                terms = _formula(ref).terms
            for term in terms:
                self._line(toks, term_text(term, q), q, made, locs, llist, params, plist, outs,
                           lines, qlines)
        writes = tuple((table, Expr(lo), Expr(hi), sop_text(f, 0)) for table, lo, hi, f in S.writes)
        bands = tuple((Expr(o.band[0]), Expr(o.band[1])) if o.band else None for o in S.outs)
        self._close_rest(S, pos, outs, lines, qlines, plist, llist, elist, bands, writes)

    def _line(self, toks, guard, q, made, locs, llist, params, plist, outs, lines, qlines):
        """One `.alg` line of a template, every block a p<i> or l<i>."""
        body = []
        for t in toks:
            if isinstance(t, tuple) and t[0] == "guard":
                continue
            if isinstance(t, _Blk):
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
        lines.append(f"if {guard}: {text}" if guard else text)
        qlines.append(q)

    def _close_rest(self, S, pos, outs, lines, qlines, plist, llist, elist, bands, writes):
        tid = "t" + hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]
        if tid not in self.prog.templates:
            self.prog.templates[tid] = Template(tid, tuple(lines), tuple(qlines), len(plist), len(llist),
                                                len(S.outs), len(elist))
        S.nodes.append(Seg(tid, tuple(plist), tuple(llist), tuple(elist), bands, writes))
        # the run's outcomes, readable by later runs of this body and of those inside it
        here = lambda r: (S.nodes.sid, pos, outs[id(r)]) if isinstance(r, _Out) and id(r) in outs else r  # noqa: E731
        for o in S.outs:
            S.closed[id(o)] = here(o)
        for i, (lo, hi, ref) in enumerate(S.scope[-1]):
            if isinstance(ref, _F):
                S.scope[-1][i] = (lo, hi, _F([tuple((c, tuple(here(r) for r in rs)) for c, rs in t)
                                              for t in ref.terms]))
            else:
                S.scope[-1][i] = (lo, hi, here(ref))
        S.run, S.outs, S.current, S.writes = [], [], None, []


_PROD = re.compile(r"(?P<var>\w+) from (?P<lo>.+?) to (?P<hi>.+?) if (?P<cond>.+?) \. "
                   r"(?P<letter>[XZ])\[(?P<fam>\w+)\[(?P<idx>.+)\]\[0\]\]")


def _family(formal: str):
    """`Surface[m + rec] LogicData` -> 'LogicData' (a block family passed whole); else None."""
    m = _FAMILY.fullmatch(formal.strip())
    return m[1] if m else None


def bind(args, env: dict) -> dict:
    """A call's arguments that can be evaluated here: an argument that reads a table FormalRV
    gives only as bounds (data) stays unbound -- no count, time or structure depends on it."""
    out = {}
    for f, e in args:
        try:
            out[f] = e(env)
        except ExprError:
            pass
    return out


def _reads_table(node) -> bool:
    if node[0] == "idx":
        return True
    if node[0] in ("n", "v"):
        return False
    if node[0] in ("min", "max"):
        return any(_reads_table(a) for a in node[1])
    return _reads_table(node[1]) or _reads_table(node[2])


def _recursive(calls: dict) -> set:
    """The gadgets on a cycle of the call graph (a gadget that calls itself, directly or not)."""
    out = set()
    for start in calls:
        seen, todo = set(), list(calls[start])
        while todo:
            g = todo.pop()
            if g == start:
                out.add(start)
                break
            if g in seen or g not in calls:
                continue
            seen.add(g)
            todo.extend(calls[g])
    return out


def _is_pred_var(v: str) -> bool:
    """FormalRV writes `if c:` as `for 0 <= rv_g < [c]` and its else as `for 0 <= rv_e < ...`
    (a fan-out's guard: `rv_fan_g`)."""
    return bool(re.fullmatch(r"rv_(?:\w+_)?[ge]\d+", v))


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
        self.records: dict = {}            # record address -> its bit, as guard parities
        self.sources: dict = {}            # source slot -> its bit
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
                if c or (self.reserved and not node.structural):
                    self.body(node.body, env, guard)
            elif isinstance(node, Call):
                env2 = {**self.prog.params, **{f: e(env) for f, e in node.skeys + node.args}}
                self.body(self.prog.body_of(node, env), env2, guard)
            elif isinstance(node, Rot):
                fs = [(p, self.name(fam, idx, env)) for p, fam, idx in node.factors]
                self.rotation(fs, node.k(env), node.sign, guard)
            elif isinstance(node, Cat):
                self.cat(node, body.sid, i, env, guard)
            elif isinstance(node, When):
                f = self.formula(node.cond, env)
                if len(f) != 1:
                    raise ValueError(f"a guarded body (line {node.qline}) under {len(f)} terms")
                self.body(node.body, env, tuple(guard) + tuple(_guard_text(p) for p in f[0]))

    def cat(self, node: Cat, sid, pos, env, guard):
        """One `ppm` over every factor whose bit is 1 (every one, reserved), then the merge's
        rounds on each of its blocks -- as qec2alg lowers a merge."""
        fs = [(p, self.name(fam, idx, env)) for p, fam, idx in node.factors]
        for v in range(node.lo(env), node.hi(env)):
            e = {**env, node.var: v}
            if self.reserved or node.cond(e) > 0:
                fs.append((node.letter, self.name(node.fam, node.idx, e)))
        o = self.fresh()
        self.emit("ppm", [x for p, b in fs for x in (p, b)], o, guard)
        for _, b in fs:
            for k in rounds_of(node.rounds):
                self.emit("se", [b, str(k)], None, guard)
        self.outs[(sid, pos)] = [o]
        if node.band is not None:
            base = env.get("rv_recbase", 0)
            for a in range(base + node.band[0](env), base + node.band[1](env)):
                self.records[a] = [((0, (o,)),)]

    def resolve(self, ref, env) -> tuple:
        """An outcome a template reads from outside it, as guard parities over names."""
        if isinstance(ref, _Rec):
            table = self.sources if ref.table == "src" else self.records
            a = ref.idx(env) + (0 if ref.table == "src" else env.get("rv_recbase", 0))
            if a not in table:
                raise KeyError(f"{'source slot' if ref.table == 'src' else 'record'} {a} "
                               f"({ref.idx.text}) is read before anything wrote it")
            return table[a]
        if isinstance(ref, _MXor):
            fs = []
            for v in range(ref.n(env)):
                e = {**env, ref.var: v}
                if self.reserved or ref.cond(e) > 0:
                    fs.append(self.sources[ref.idx(e)])
            return _sop_xor(*fs)
        bsid, bpos, k = ref
        return [((0, (self.outs[(bsid, bpos)][k],)),)]

    def formula(self, f, env) -> list:
        """A lowering-time formula (over outcome references) as one over names here."""
        out = []
        for term in f.terms:
            out = _sop_xor(out, _sop_and(*[_sop_xor(*[self.resolve(r, env) for r in rs], [()] if c else [])
                                           for c, rs in term]))
        return out

    def guard_of(self, text: str, vals: dict) -> list:
        """A template's guard (or a write's value, terms `;` apart), its o<i> / e<i> replaced by
        what they are here: an XOR of conjunctions over names."""
        if ";" in text:
            return _sop_xor(*[self.guard_of(t, vals) for t in text.split(";")])
        text = text.strip()
        if not text:
            return []
        out = [()]
        for conj in text.split("&"):
            fs = []
            for t in conj.split("^"):
                t = t.strip()
                if t in ("0", "1"):
                    fs.append([()] if t == "1" else [])
                    continue
                if t.startswith("!"):
                    fs.append([()])
                    t = t[1:]
                fs.append(vals[t])
            out = _sop_and(out, _sop_xor(*fs))
        return out

    def seg(self, node: Seg, sid, pos, env, guard):
        t = self.prog.templates[node.tid]
        names = {f"p{i}": self.name(fam, idx, env) for i, (fam, idx) in enumerate(node.params)}
        locs = {f"l{i}": (fam, idx) for i, (fam, idx) in enumerate(node.locals)}
        outs = [self.fresh() for _ in range(t.n_out)]
        names.update({f"o{i}": o for i, o in enumerate(outs)})
        vals = {f"o{i}": [((0, (o,)),)] for i, o in enumerate(outs)}
        for i, ref in enumerate(node.ext):
            vals[f"e{i}"] = self.resolve(ref, env)
        for line in t.lines:
            if line.startswith("bit "):
                # the archive's bit: what its o<i> / e<i> are here, multiplied out over names
                text, o = line[4:].split("->")
                f = []
                for term in parse_bit(text):
                    f = _sop_xor(f, _sop_and(*[_sop_not(vals[n]) if neg else vals[n] for neg, n in term]))
                name = names[o.strip()]
                vals[o.strip()] = [((0, (name,)),)]
                self.emit("bit", [_bit_text(f, str)], name, ())
                continue
            terms = [()]
            if line.startswith("if "):
                cond, line = line[3:].split(": ", 1)
                terms = self.guard_of(cond, vals)
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
            if len(terms) != 1 and word not in ("x", "y", "z"):
                raise ValueError(f"'{line}' under a guard of {len(terms)} terms")
            for term in terms:                       # (a frame: once per term)
                self.emit(word, args, out, tuple(guard) + tuple(_guard_text(p) for p in term))
        self.outs[(sid, pos)] = outs
        base = env.get("rv_recbase", 0)
        for k, band in enumerate(node.bands):
            if band is not None:
                for a in range(base + band[0](env), base + band[1](env)):
                    self.records[a] = [((0, (outs[k],)),)]
        for table, lo, hi, text in node.writes:
            val = self.guard_of(text, vals)
            off = base if table == "rec" else 0
            for a in range(off + lo(env), off + hi(env)):
                (self.records if table == "rec" else self.sources)[a] = val

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


def _guard_text(p) -> str:
    """One guard parity as the `.alg` writes it: a name, `!name`, or `a ^ b ^ 1`."""
    c, ns = p
    if len(ns) == 1:
        return ("!" if c else "") + ns[0]
    return " ^ ".join(list(ns) + (["1"] if c else [])) or ("1" if c else "0")


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
        if re.fullmatch(r"[A-Z]+\d*(dg)?|[XYZ]|\d+", n):     # a basis, an angle, a round count
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


def duration_ns(prog: Program, span_ns: dict, rot_ns, *, parallel: bool = True, cat_ns=None) -> int:
    """The run's time on the machine: `span_ns[tid]` a template's compiled duration,
    `rot_ns(k, sign, n_factors)` a rotation chain's (every level reserved).  Every instance
    runs after the one before it, except that the iterations of a parallel loop (`Loop.par`)
    run at once, each on its own district -- the loop takes as long as its slowest iteration
    (`parallel=False`: one after another).  A product over a register takes
    `cat_ns(n_blocks, rounds)`.  Exact integers: RSA-2048 runs past 2^53 µs."""
    memo: dict = {}

    def body_ns(body: Body, env: dict) -> int:
        return sum(node_ns(n, env) for n in body)

    def node_ns(node, env) -> int:
        if isinstance(node, Seg):
            return span_ns[node.tid] if prog.templates[node.tid].lines else 0
        if isinstance(node, Pred) and node.structural:
            return body_ns(node.body, env) if node.cond(env) >= 1 else 0
        if isinstance(node, (Pred, When)):
            return body_ns(node.body, env)                 # a slot: reserved either way
        if isinstance(node, Let):
            # (bound only when the time depends on it: a binding of a block index is not)
            return body_ns(node.body, {**env, node.var: node.value(env)} if node.var in _deps(node.body) else env)
        if isinstance(node, Rot):
            return rot_ns(node.k(env), node.sign, len(node.factors))
        if isinstance(node, Cat):
            if cat_ns is None:
                raise ValueError(f"a product over a register (line {node.qline}) and no time for one")
            return cat_ns(node.blocks(env), node.rounds)
        if isinstance(node, Call):
            body = prog.body_of(node, env)
            # (keyed by the arguments the count reads too: a decision on structure may read one)
            dep = _deps(body)
            key = (node.gadget, tuple((f, e(env)) for f, e in node.skeys), node.fam,
                   tuple((f, e(env)) for f, e in node.args if f in dep))
            if key not in memo:
                memo[key] = body_ns(body, {**prog.params, **bind(node.skeys + node.args, env)})
            return memo[key]
        if isinstance(node, Loop):
            lo, hi = node.lo(env), node.hi(env)
            n = hi - lo
            if n <= 0:
                return 0
            at = lambda v: body_ns(node.body, {**env, node.var: v})  # noqa: E731
            dep = node.var in _deps(node.body)
            if node.par and parallel:
                if not dep:
                    return at(lo)
                if n <= 4:
                    return max(at(v) for v in range(lo, hi))
                a, b, mid = at(lo), at(hi - 1), at(lo + n // 2)
                d = at(lo + 1) - a
                if mid == a + d * (n // 2) and b == a + d * (n - 1):
                    return max(a, b)                       # affine: the largest is at an end
                return max(at(v) for v in range(lo, hi))
            if not dep:
                return n * at(lo)
            if n <= 4:
                return sum(at(v) for v in range(lo, hi))
            a, a1, mid, b = at(lo), at(lo + 1), at(lo + n // 2), at(hi - 1)
            d = a1 - a
            if mid == a + d * (n // 2) and b == a + d * (n - 1):
                return n * a + d * n * (n - 1) // 2
            return sum(at(v) for v in range(lo, hi))
        return 0
    return body_ns(prog.root, dict(prog.params))


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
        if isinstance(node, Pred) and node.structural:
            return body_count(node.body, env) if node.cond(env) >= 1 else Counter()
        if isinstance(node, (Pred, When)):
            return body_count(node.body, env)
        if isinstance(node, Let):
            return body_count(node.body, {**env, node.var: node.value(env)} if node.var in _deps(node.body) else env)
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
            body = prog.body_of(node, env)
            # (keyed by the arguments the count reads too: a decision on structure may read one)
            dep = _deps(body)
            key = (node.gadget, tuple((f, e(env)) for f, e in node.skeys), node.fam,
                   tuple((f, e(env)) for f, e in node.args if f in dep))
            if key not in memo:
                memo[key] = body_count(body, {**prog.params, **bind(node.skeys + node.args, env)})
            return memo[key]
        if isinstance(node, Rot):
            return rot_value(node.k(env), node.sign, [p for p, _, _ in node.factors])
        if isinstance(node, Cat):
            # one product by cat states, every slot taken: a bench per block, each block's rounds
            n = node.blocks(env)
            return Counter({"ppm": 1, "cat": 1, "cat_blocks": n, "se": n * len(rounds_of(node.rounds))})
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
        elif isinstance(n, Pred) and n.structural:
            out |= _deps(n.body) | n.cond.vars
        elif isinstance(n, (Pred, When)):
            out |= _deps(n.body)
        elif isinstance(n, Let):
            inner = _deps(n.body)
            out |= (inner - {n.var}) | (n.value.vars if n.var in inner else set())
        elif isinstance(n, Call):
            for _, e in n.skeys:
                out |= e.vars
        elif isinstance(n, Rot):
            out |= n.k.vars
        elif isinstance(n, Cat):
            out |= n.lo.vars | n.hi.vars
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
