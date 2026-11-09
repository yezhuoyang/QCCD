"""FormalRV's whole `.sysprog` read and walked at scale (docs/FULLSTACK_PLAN.md, slice S10 step 3).

`qccd.fullstack.sysprog` expands a small listing into every event.  Gidney 2025's has 5.2e14 of them, and
tables, `Σ`, `case`, `when` and `step` loops, so this reader keeps the listing folded: each expression
compiled once, each gadget a tree of statements, and a walk that visits a gadget once for each value of
the formals its structure depends on (the rest are addresses: `rv_anc`, `rv_qsyn`, ...).

FormalRV's VM stays the evaluator of record: `counts` must equal its opcode counts and dynamic
instruction count exactly (`vm_differences`).  Semantics as `sysprog.py` states them; beyond it:

  * `table T = [...]` and `T[i]`; `tablemax(T, n)` the largest of T[0 .. n] (as the VM's);
  * `Σ v < n . e`;
  * `when c[..]:` a guarded body.  The VM charges every guard taken, so does this reader;
  * `case e:` the arm of e's value (`skip` when none is written);
  * `for lo <= v < hi step p ns:` trip v runs at the sum of the earlier trips' p;
  * a table given as `bound[extent, lo, hi]` has unknown contents: read as hi, whose bits are all set --
    the VM's "all taken" (`Tanf`, `Tbar`);
  * a table in `ABSTRACT` (`Pm1`, `Pmod`, the primes) is read as `TOP`, as the VM reads them under
    FORMALRV_VM_ABSTRACT_TABLE_ARGS: a value absorbing arithmetic, and a loop bounded by it runs its one
    possible trip.  No index into either kind is structural.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..sysprog import GATES_1Q, GATES_2Q, split_top

__all__ = ["Listing", "read", "counts", "vm_differences", "read_inputs", "Stmt", "For", "When", "Case", "CallS"]

SCALES = {"ns": 1, "us": 1000, "ms": 1_000_000, "s": 1_000_000_000}
_TOKEN = re.compile(r"\d+|[A-Za-z_][A-Za-z_0-9!']*|Σ|[()\[\]+*/%^\-,.<]|\S")


def sub(a, b):
    return a - b if a > b else 0


def div(a, b):
    return a // b if b else 0


def mod(a, b):
    return a % b if b else a


class _Top:
    """A value known only to lie in its table's range: it absorbs arithmetic."""

    def _top(self, *_):
        return self

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _top
    __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = __pow__ = __rpow__ = _top

    def __gt__(self, other):
        return True

    def __lt__(self, other):
        return True

    def __bool__(self):
        return True

    def __repr__(self):
        return "TOP"


TOP = _Top()
ABSTRACT = ("Pm1", "Pmod")
#: the program's constant the VM charges all bits of: gLoop4's mask, `trunc` = 2^33
VM_ABSTRACT_PARAMS = ("trunc",)


class _TopTable:
    def __getitem__(self, i):
        return TOP


class _Expr:
    """Natural arithmetic compiled to Python.  Every name not bound by a Σ is looked up in `e`."""

    __slots__ = ("text", "fn", "names")

    def __init__(self, text: str, tables: dict):
        self.text = text
        tokens = _TOKEN.findall(text.replace("·", "*"))
        names: set = set()
        src = _Parser(tokens, tables, names).parse()
        self.names = frozenset(names)
        self.fn = eval(compile(f"lambda e: {src}", "<listing>", "eval"),
                       {"sub": sub, "div": div, "mod": mod, "T": tables, "sum": sum, "range": range, "max": max,
                        "min": min, "tablemax": _tablemax})

    def __call__(self, e: dict) -> int:
        return self.fn(e)


def _tablemax(t, n):
    return max(t[: n + 1], default=0)


class _Parser:
    PREC = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "^": 3}

    def __init__(self, tokens, tables, names):
        self.t, self.p, self.tables, self.names = tokens, 0, tables, names
        self.bound: list = []

    def parse(self) -> str:
        s = self.expr(0)
        if self.p != len(self.t):
            raise ValueError(f"unparsed {self.t[self.p:]}")
        return s

    def peek(self):
        return self.t[self.p] if self.p < len(self.t) else None

    def take(self, want=None):
        tok = self.peek()
        if want is not None and tok != want:
            raise ValueError(f"expected {want}, got {tok} in {' '.join(self.t)}")
        self.p += 1
        return tok

    def atom(self) -> str:
        tok = self.take()
        if tok == "(":
            s = self.expr(0)
            self.take(")")
            return f"({s})"
        if tok == "Σ":
            v = self.take()
            self.take("<")
            hi = self.expr(0)
            self.take(".")
            self.bound.append(v)
            body = self.expr(0)
            self.bound.pop()
            return f"sum(({body}) for _{v} in range({hi}))"
        if tok.isdecimal():
            return tok
        if tok in ("min", "max") and self.peek() == "(":
            self.take("(")
            args = [self.expr(0)]
            while self.peek() == ",":
                self.take(",")
                args.append(self.expr(0))
            self.take(")")
            return f"{tok}({', '.join(args)})"
        if tok == "tablemax":
            self.take("(")
            name = self.take()
            self.take(",")
            n = self.expr(0)
            self.take(")")
            return f"tablemax(T[{name!r}], {n})"
        if self.peek() == "[":
            self.take("[")
            opaque = isinstance(self.tables.get(tok), (_BoundTable, _TopTable))
            kept = set(self.names)
            i = self.expr(0)
            self.take("]")
            if opaque:              # the value does not depend on the index
                self.names.clear()
                self.names.update(kept)
                return f"T[{tok!r}][0]"
            return f"T[{tok!r}][{i}]"
        if tok in self.bound:
            return f"_{tok}"
        self.names.add(tok)
        return f"e[{tok!r}]"

    def expr(self, minimum: int) -> str:
        left = self.atom()
        while True:
            op = self.peek()
            if op not in self.PREC or self.PREC[op] < minimum:
                return left
            self.take()
            right = self.expr(self.PREC[op] if op == "^" else self.PREC[op] + 1)
            left = {"+": f"({left} + {right})", "*": f"({left} * {right})", "-": f"sub({left}, {right})",
                    "/": f"div({left}, {right})", "%": f"mod({left}, {right})", "^": f"({left} ** {right})"}[op]


class _BoundTable:
    """A table whose contents are given only as a range: every read gives the top of the range."""

    def __init__(self, hi: int):
        self.hi = hi

    def __getitem__(self, i):
        return self.hi


@dataclass
class Stmt:
    t: _Expr
    kind: str          # opcode
    text: str          # the statement after its time stamp
    line: int


@dataclass
class CallS:
    t: _Expr
    name: str
    args: list
    line: int


@dataclass
class For:
    v: str
    lo: _Expr
    hi: _Expr
    step: bool         # `step` (trip clocks add up), else `every`
    period: _Expr
    body: list
    line: int


@dataclass
class When:
    guard: str
    body: list
    line: int


@dataclass
class Case:
    e: _Expr
    arms: dict
    line: int


@dataclass
class Gadget:
    name: str
    formals: list
    span: _Expr
    body: list
    line: int
    relevant: frozenset = frozenset()   # formals its structure depends on


@dataclass
class Listing:
    path: str
    machine: dict
    params: dict
    tables: dict
    gadgets: dict
    body: list
    widths: str = ""
    order: list = field(default_factory=list)


def read_inputs(path) -> dict:
    """A FormalRV `.inputs` file: `name=int`, `name=[...]`, `name=bound[extent, lo, hi]`."""
    out: dict = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        k, v = ln.split("=", 1)
        v = v.strip()
        if k.strip() in ABSTRACT:
            out[k.strip()] = _TopTable()
        elif v.startswith("bound["):
            out[k.strip()] = _BoundTable(ast.literal_eval(v[5:])[2])
        elif v.startswith("["):
            out[k.strip()] = ast.literal_eval(v)
        else:
            out[k.strip()] = int(v)
    return out


_STAMP = re.compile(r"@(.+?)\s+(ns)\s+(.+)", re.S)
_FOR = re.compile(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?)\s+(every|step)\s+(.+?)\s+ns:")


def _opcode(body: str) -> str:
    """The opcode of a timed statement: its first word, or the word after the operands it reads."""
    first = body.split(None, 1)[0]
    if first in ("source",) or "[" in first:
        m = re.search(r"\b(request_magic|decode_syndrome|parity|pauli_frame_update)\b", body)
        if not m:
            raise ValueError(f"no opcode in {body[:120]}")
        return m[1]
    return first


def read(path, inputs: dict) -> Listing:
    """The listing at `path`, with the values of its params and tables (`read_inputs`)."""
    path = Path(path)
    rows = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        s = raw.rstrip()
        if s.strip():
            rows.append((len(s) - len(s.lstrip(" ")), s.strip(), i))
    tables = {k: v for k, v in inputs.items() if not isinstance(v, int)}
    params = {k: v for k, v in inputs.items() if isinstance(v, int)}
    machine, gadgets, order, body, widths = {}, {}, [], [], ""
    pos = 0

    def block(parent: int) -> list:
        nonlocal pos
        out = []
        while pos < len(rows) and rows[pos][0] > parent:
            depth, text, ln = rows[pos]
            pos += 1
            out.append(statement(depth, text, ln))
        return out

    def statement(depth, text, ln):
        nonlocal pos
        if text == "skip":
            return None
        m = _FOR.fullmatch(text)
        if m:
            return For(m[2], _Expr(m[1], tables), _Expr(m[3], tables), m[4] == "step", _Expr(m[5], tables),
                       [s for s in block(depth) if s], ln)
        if text.startswith("when "):
            return When(text[5:-1], [s for s in block(depth) if s], ln)
        if text.startswith("case "):
            arms = {}
            while pos < len(rows) and rows[pos][0] > depth:
                d2, t2, l2 = rows[pos]
                pos += 1
                arms[int(t2[:-1])] = [s for s in block(d2) if s]
            return Case(_Expr(text[5:-1], tables), arms, ln)
        m = _STAMP.fullmatch(text)
        if not m:
            raise ValueError(f"line {ln}: {text[:120]}")
        t, rest = _Expr(m[1], tables), m[3]
        c = re.fullmatch(r"call\s+([^\s(]+)\((.*)\)", rest)
        if c:
            return CallS(t, c[1], [_Expr(a, tables) for a in split_top(c[2])], ln)
        return Stmt(t, _opcode(rest), rest, ln)

    while pos < len(rows):
        depth, text, ln = rows[pos]
        pos += 1
        if text.startswith("machine {"):
            for item in split_top(text[text.index("{") + 1: text.rindex("}")]):
                k, v = item.split(":", 1)
                machine[k.strip()] = int(v.split()[0])
        elif text.startswith("table "):
            k, v = text[6:].split("=", 1)
            tables[k.strip()] = ast.literal_eval(v.strip())
        elif text.startswith("gadget "):
            m = re.fullmatch(r"gadget\s+(\S+?)\[(.*)\]\((.*?)\)\s+spans\s+(.+)\s+ns:", text)
            if not m:
                raise ValueError(f"line {ln}: malformed gadget")
            g = Gadget(m[1], [f.strip() for f in m[3].split(",") if f.strip()], _Expr(m[4], tables),
                       [s for s in block(0) if s], ln)
            gadgets[g.name] = g
            order.append(g.name)
        elif text.startswith("program["):
            widths = text[8:-2]
            body = [s for s in block(0) if s]
        elif depth == 0:
            while pos < len(rows) and rows[pos][0] > 0:
                pos += 1
    lst = Listing(str(path), machine, params, tables, gadgets, body, widths, order)
    _relevant(lst)
    return lst


def _walk_nodes(nodes):
    for n in nodes:
        yield n
        if isinstance(n, (For, When)):
            yield from _walk_nodes(n.body)
        elif isinstance(n, Case):
            for arm in n.arms.values():
                yield from _walk_nodes(arm)


def _relevant(lst: Listing) -> None:
    """For each gadget, the formals its statement structure depends on: those that reach a loop
    bound, a `case`, or a relevant formal of a callee, through the loop variables they define.
    Fixed point over the call graph; the listing's call graph has no cycles."""
    changed = True
    while changed:
        changed = False
        for g in lst.gadgets.values():
            need = set(g.relevant)

            def scan(nodes, bound: frozenset):
                for n in nodes:
                    if isinstance(n, For):
                        need.update((n.lo.names | n.hi.names) - bound)
                        if n.step:
                            need.update(n.period.names - bound)
                        scan(n.body, bound | {n.v})
                    elif isinstance(n, When):
                        scan(n.body, bound)
                    elif isinstance(n, Case):
                        need.update(n.e.names - bound)
                        for arm in n.arms.values():
                            scan(arm, bound)
                    elif isinstance(n, CallS):
                        callee = lst.gadgets[n.name]
                        for f, a in zip(callee.formals, n.args):
                            if f in callee.relevant:
                                need.update(a.names - bound)
            scan(g.body, frozenset())
            new = frozenset(f for f in g.formals if f in need)
            if new != g.relevant:
                g.relevant, changed = new, True


def _unit(kind: str, text: str) -> int:
    """How many of its opcode a statement counts: one, except a CNOT/CZ's pairs and a gate's range."""
    return 1


def counts(lst: Listing, upper: bool = True) -> dict:
    """{opcode: dynamic count}, walking each gadget once per value of its relevant formals.  A loop
    whose body does not read its variable structurally is one trip times the trip count.  With
    `upper`, as FormalRV's VM counts them: a loop over a `case` of more than one trip (`rv_word`, a
    rotation's words) charged its largest trip every trip, and `VM_ABSTRACT_PARAMS` as `TOP`; without,
    the words and the constants as they are (still every guard taken)."""
    memo: dict = {}
    params = {**lst.params, **({k: TOP for k in VM_ABSTRACT_PARAMS} if upper else {})}

    def add(a: dict, b: dict, k: int = 1) -> None:
        for x, v in b.items():
            a[x] = a.get(x, 0) + k * v

    def depends(nodes, v: str) -> bool:
        for n in nodes:
            if isinstance(n, For):
                if v in n.lo.names or v in n.hi.names or (n.step and v in n.period.names) or depends(n.body, v):
                    return True
            elif isinstance(n, When):
                if depends(n.body, v):
                    return True
            elif isinstance(n, Case):
                if v in n.e.names or any(depends(a, v) for a in n.arms.values()):
                    return True
            elif isinstance(n, CallS):
                callee = lst.gadgets[n.name]
                if any(f in callee.relevant and v in a.names for f, a in zip(callee.formals, n.args)):
                    return True
        return False

    def body(nodes, e: dict) -> dict:
        out: dict = {}
        for n in nodes:
            if isinstance(n, Stmt):
                out[n.kind] = out.get(n.kind, 0) + _unit(n.kind, n.text)
                out["instructions"] = out.get("instructions", 0) + 1
            elif isinstance(n, CallS):
                g = lst.gadgets[n.name]
                args = {}
                for f, a in zip(g.formals, n.args):
                    if f in g.relevant:
                        args[f] = a(e)
                key = (g.name, tuple(sorted(args.items())))
                got = memo.get(key)
                if got is None:
                    got = memo[key] = body(g.body, {**params, **args})
                add(out, got)
            elif isinstance(n, For):
                lo, hi = n.lo(e), n.hi(e)
                if hi is TOP:
                    hi = lo + 1
                if hi <= lo:
                    continue
                if not depends(n.body, n.v):
                    add(out, body(n.body, {**e, n.v: lo}), hi - lo)
                elif upper and hi - lo > 1 and isinstance(n.body[0], Case):     # the VM's envelope
                    top: dict = {}
                    for v in range(lo, hi):
                        for x, y in body(n.body, {**e, n.v: v}).items():
                            top[x] = max(top.get(x, 0), y)
                    add(out, top, hi - lo)
                else:
                    for v in range(lo, hi):
                        add(out, body(n.body, {**e, n.v: v}))
            elif isinstance(n, When):
                add(out, body(n.body, e))
            elif isinstance(n, Case):
                v = n.e(e)
                if v is TOP:        # any arm may run: each opcode at its largest over the arms
                    top: dict = {}
                    for arm in n.arms.values():
                        for x, y in body(arm, e).items():
                            top[x] = max(top.get(x, 0), y)
                    add(out, top)
                else:
                    add(out, body(n.arms.get(v, []), e))
        return out

    return body(lst.body, params)


def vm_differences(ours: dict, vm: dict) -> list:
    """Differences between `counts` and the VM's `--json` verdict (empty = they agree)."""
    loc = vm["location"]["opcode_counts"]
    diffs = []
    for k in (*GATES_1Q, *GATES_2Q, "measure", "request_ancilla", "release_ancilla", "route", "decode_syndrome",
              "request_magic", "pauli_frame_update", "parity"):
        if loc.get(k, 0) != ours.get(k, 0):
            diffs.append(f"{k}: VM {loc.get(k, 0)}, ours {ours.get(k, 0)}")
    if vm.get("dynamic_instruction_count") != ours.get("instructions"):
        diffs.append(f"instructions: VM {vm.get('dynamic_instruction_count')}, ours {ours.get('instructions')}")
    return diffs
