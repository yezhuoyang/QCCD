"""The rule language: its grammar, its parser, and how a rule is printed.

A rule set is a text file.  Every rule in it is one assertion about the hardware and the
execution -- a formula of first-order logic over finite domains (the sites of the device,
the operations of the programme, the instants of its execution, ...), written in words a
reader can check by eye:

    ruleset "standard"
    title "The rules of this website's machine"

    param max_chain = 15

    rule R1 "A site holds at most its capacity" core
      about "An ion standing in a site, or passing through it, takes one of its places."
      for all s in sites, t in instants:
        occupancy(s, t) <= capacity(s)

Grammar (EBNF; `#` starts a comment that runs to the end of the line):

    file      = { item } ;
    item      = "ruleset" STRING | "title" STRING | "about" STRING | "level" STRING
              | "include" STRING | "use" ID { "," ID } | "drop" ID { "," ID }
              | "param" NAME "=" expr
              | rule ;
    rule      = "rule" ID STRING [ "core" ]
                { "about" STRING | "source" STRING | "when" formula ":" }
                formula ;
    formula   = quantified | implication ;
    quantified= ( "for" "all" | "for" "some" ) binders [ "where" formula ] ":" formula ;
    binders   = binder { "," binder } ;
    binder    = NAME { "," NAME } "in" expr ;
    implication = disjunction [ "implies" formula ] ;
    disjunction = conjunction { "or" conjunction } ;
    conjunction = negation { "and" negation } ;
    negation  = "not" negation | quantified | comparison ;
    comparison= sum [ ( "=" | "!=" | "<" | "<=" | ">" | ">=" | "in" | "not" "in" ) sum ] ;
    sum       = product { ( "+" | "-" ) product } ;
    product   = unary { ( "*" | "/" ) unary } ;
    unary     = "-" unary | primary ;
    primary   = NUMBER | STRING | "true" | "false" | "none" | NAME
              | NAME "(" [ expr { "," expr } ] ")"
              | AGG "(" expr "for" binders [ "where" formula ] ")"
              | "count" "(" binders [ "where" formula ] ")"
              | "{" expr "for" binders [ "where" formula ] "}"
              | "{" [ expr { "," expr } ] "}"
              | "(" formula ")" ;
    AGG       = "sum" | "max" | "min" | "all_different" ;

`for all x in D where C: B` holds when B holds for every x in D that satisfies C; `for
some` asks for one.  A rule's `when` clause is its precondition: when it is false the rule
does not apply to this execution, which is reported as such and never as a pass.

The parser knows no vocabulary: which names are domains and functions (`sites`,
`occupancy`, ...) is the evaluator's business (`vocabulary.py`), so a new rule needs no
change here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

__all__ = [
    "RuleSyntaxError",
    "Node", "Num", "Str", "Const", "Name", "Call", "BinOp", "Not", "Quant", "Binder",
    "Gen", "SetLit",
    "Rule", "RuleSetSource",
    "parse", "parse_formula", "show", "free_names",
]


class RuleSyntaxError(ValueError):
    """A rule set that does not parse; the message names the line and what was expected."""


# ----------------------------------------------------------------------------- tokens

_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r]+)
  | (?P<nl>\n)
  | (?P<comment>\#[^\n]*)
  | (?P<num>\d+\.\d*(?:[eE][-+]?\d+)?|\d*\.\d+(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?)
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|!=|[=<>+\-*/(){},:])
""", re.VERBOSE)

KEYWORDS = frozenset({
    "for", "all", "some", "in", "where", "and", "or", "not", "implies", "true", "false",
    "none", "rule", "ruleset", "title", "about", "source", "core", "param", "include",
    "drop", "when", "count", "use", "level",
})
#: Words that start a top-level item: a rule's formula ends where one of these begins a line.
ITEM_WORDS = frozenset({"rule", "ruleset", "title", "include", "drop", "param", "use", "level"})
AGGREGATES = frozenset({"sum", "max", "min", "all_different", "count"})


@dataclass(frozen=True, slots=True)
class Tok:
    kind: str      # num | str | name | kw | op | eof
    text: str
    line: int
    col: int
    bol: bool      # first token on its line


def tokenize(text: str) -> list[Tok]:
    out: list[Tok] = []
    line, col0, pos, bol = 1, 0, 0, True
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise RuleSyntaxError(f"line {line}: unexpected character {text[pos]!r}")
        kind = m.lastgroup
        s = m.group()
        if kind == "nl":
            line += 1
            col0 = m.end()
            bol = True
        elif kind not in ("ws", "comment"):
            if kind == "name" and s in KEYWORDS:
                kind = "kw"
            out.append(Tok(kind, s, line, pos - col0 + 1, bol))
            bol = False
        pos = m.end()
    out.append(Tok("eof", "", line, 1, True))
    return out


# ----------------------------------------------------------------------------- the tree


class Node:
    """A formula or a term.  `line` is where it starts in the rule set's text."""

    __slots__ = ()


@dataclass(eq=False, slots=True)
class Num(Node):
    value: float
    line: int = 0


@dataclass(eq=False, slots=True)
class Str(Node):
    value: str
    line: int = 0


@dataclass(eq=False, slots=True)
class Const(Node):
    value: object      # True, False or None
    line: int = 0


@dataclass(eq=False, slots=True)
class Name(Node):
    id: str
    line: int = 0


@dataclass(eq=False, slots=True)
class Call(Node):
    fn: str
    args: tuple
    line: int = 0


@dataclass(eq=False, slots=True)
class BinOp(Node):
    op: str            # and or implies = != < <= > >= in notin + - * /
    left: Node
    right: Node
    line: int = 0


@dataclass(eq=False, slots=True)
class Not(Node):
    arg: Node
    line: int = 0


@dataclass(eq=False, slots=True)
class Binder:
    names: tuple[str, ...]
    domain: Node


@dataclass(eq=False, slots=True)
class Quant(Node):
    kind: str          # "all" | "some"
    binders: tuple[Binder, ...]
    where: Node | None
    body: Node
    line: int = 0


@dataclass(eq=False, slots=True)
class Gen(Node):
    """An aggregate over a generator: `count(x in D where C)`, `sum(e for x in D)`,
    `{e for x in D where C}` (kind "set")."""
    kind: str          # count | sum | max | min | all_different | set
    expr: Node | None
    binders: tuple[Binder, ...]
    where: Node | None
    line: int = 0


@dataclass(eq=False, slots=True)
class SetLit(Node):
    items: tuple
    line: int = 0


@dataclass
class Rule:
    id: str
    title: str
    formula: Node
    core: bool = False
    about: str = ""
    source: str = ""
    when: Node | None = None
    text: str = ""        # the formula exactly as written, for the page
    when_text: str = ""
    line: int = 0
    origin: str = ""      # the rule set the rule was written in


@dataclass
class RuleSetSource:
    """One file, parsed but not resolved: includes and drops are applied by `load`."""

    name: str = ""
    title: str = ""
    about: str = ""
    #: what the rules are about: "device" (one QCCD device and an execution on it, the
    #: default) or "machine" (a floorplan of verified places and a program scheduled on it)
    level: str = "device"
    includes: list[str] = field(default_factory=list)
    drops: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    params: dict[str, Node] = field(default_factory=dict)
    param_text: dict[str, str] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)


# ----------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.lines = text.split("\n")
        self.toks = tokenize(text)
        self.i = 0

    # -- token helpers
    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, text: str, kind: str | None = None) -> bool:
        t = self.tok
        return t.text == text and (kind is None or t.kind == kind) and t.kind != "str"

    def next(self) -> Tok:
        t = self.tok
        self.i += 1
        return t

    def expect(self, text: str, what: str = "") -> Tok:
        if not self.at(text):
            self.fail(f"expected {text!r}{' ' + what if what else ''}")
        return self.next()

    def fail(self, msg: str):
        t = self.tok
        shown = t.text or "end of file"
        raise RuleSyntaxError(f"line {t.line}, column {t.col}: {msg}, found {shown!r}")

    def string(self) -> str:
        if self.tok.kind != "str":
            self.fail("expected a quoted string")
        s = self.next().text[1:-1]
        return re.sub(r"\\(.)", r"\1", s)

    def ident(self) -> str:
        if self.tok.kind != "name":
            self.fail("expected a name")
        return self.next().text

    def item_start(self) -> bool:
        """True where a new top-level item begins: a rule's formula stops there."""
        t = self.tok
        return t.kind == "eof" or (t.bol and t.kind == "kw" and t.text in ITEM_WORDS)

    def source_between(self, a: int, b: int) -> str:
        """The text of tokens a..b-1, as written (whitespace normalised per line)."""
        if b <= a:
            return ""
        first, last = self.toks[a], self.toks[b - 1]
        if first.line == last.line:
            ln = self.lines[first.line - 1]
            return ln[first.col - 1:last.col - 1 + len(last.text)].strip()
        out = [self.lines[first.line - 1][first.col - 1:].rstrip()]
        for ln in range(first.line + 1, last.line):
            out.append(self.lines[ln - 1].rstrip())
        out.append(self.lines[last.line - 1][:last.col - 1 + len(last.text)].rstrip())
        # drop comments (not a `#` inside a string) and blank lines; keep the author's
        # indentation relative to the line the formula starts on
        out = [re.sub(r'("(?:[^"\\]|\\.)*")|\s*#.*$', lambda m: m.group(1) or "", s) for s in out]
        out = [s for s in out if s.strip()]
        base = first.col - 1
        return "\n".join([out[0].strip()] + [s[min(base, len(s) - len(s.lstrip())):] for s in out[1:]])

    # -- file
    def file(self) -> RuleSetSource:
        rs = RuleSetSource()
        while self.tok.kind != "eof":
            t = self.tok
            if not t.bol:
                self.fail("expected a new item at the start of a line")
            if self.at("ruleset"):
                self.next()
                rs.name = self.string()
            elif self.at("title"):
                self.next()
                rs.title = self.string()
            elif self.at("about"):
                self.next()
                rs.about = self.string()
            elif self.at("level"):
                self.next()
                rs.level = self.string()
                if rs.level not in ("device", "machine"):
                    self.fail("a rule set's level is \"device\" or \"machine\"")
            elif self.at("include"):
                self.next()
                rs.includes.append(self.string())
            elif self.at("drop"):
                self.next()
                rs.drops.append(self.ident())
                while self.at(","):
                    self.next()
                    rs.drops.append(self.ident())
            elif self.at("use"):
                self.next()
                rs.uses.append(self.ident())
                while self.at(","):
                    self.next()
                    rs.uses.append(self.ident())
            elif self.at("param"):
                self.next()
                name = self.ident()
                self.expect("=")
                a = self.i
                rs.params[name] = self.formula()
                rs.param_text[name] = self.source_between(a, self.i)
            elif self.at("rule"):
                rs.rules.append(self.rule())
            else:
                self.fail("expected ruleset, title, about, level, include, use, drop, param or rule")
        ids = [r.id for r in rs.rules]
        dup = sorted({x for x in ids if ids.count(x) > 1})
        if dup:
            raise RuleSyntaxError(f"rule {dup[0]} is defined twice")
        return rs

    def rule(self) -> Rule:
        line = self.next().line
        rid = self.ident()
        title = self.string()
        r = Rule(id=rid, title=title, formula=Const(True), line=line)
        if self.at("core"):
            self.next()
            r.core = True
        while True:
            if self.at("about"):
                self.next()
                r.about = self.string()
            elif self.at("source"):
                self.next()
                r.source = self.string()
            elif self.at("when"):
                self.next()
                a = self.i
                r.when = self.formula()
                r.when_text = self.source_between(a, self.i)
                self.expect(":", "after the rule's `when` condition")
            else:
                break
        if self.item_start():
            self.fail(f"rule {rid} has no formula")
        a = self.i
        r.formula = self.formula()
        r.text = self.source_between(a, self.i)
        if not self.item_start():
            self.fail(f"rule {rid}: the formula should end here")
        return r

    # -- formulas
    def formula(self) -> Node:
        if self.at("for"):
            return self.quantified()
        return self.implication()

    def quantified(self) -> Quant:
        line = self.next().line
        if self.at("all"):
            kind = "all"
        elif self.at("some"):
            kind = "some"
        else:
            self.fail("expected `all` or `some` after `for`")
        self.next()
        binders = self.binders()
        where = None
        if self.at("where"):
            self.next()
            where = self.formula()
        self.expect(":", "before the body of the quantifier")
        body = self.formula()
        return Quant(kind, binders, where, body, line)

    def binders(self) -> tuple[Binder, ...]:
        out = []
        while True:
            names = [self.ident()]
            while self.at(",") and self.peek().kind == "name" and self._names_then_in():
                self.next()
                names.append(self.ident())
            self.expect("in", "after the variable name(s)")
            out.append(Binder(tuple(names), self.sum()))
            if self.at(",") and self.peek().kind == "name" and self._names_then_in():
                self.next()
                continue
            break
        return tuple(out)

    def _names_then_in(self) -> bool:
        """After a comma: is this `name {, name} in`?  (Else the comma ends the binders.)"""
        k = 1
        while self.peek(k).kind == "name":
            nxt = self.peek(k + 1)
            if nxt.text == "in" and nxt.kind == "kw":
                return True
            if nxt.text != ",":
                return False
            k += 2
        return False

    def implication(self) -> Node:
        left = self.disjunction()
        if self.at("implies"):
            line = self.next().line
            return BinOp("implies", left, self.formula(), line)
        return left

    def disjunction(self) -> Node:
        left = self.conjunction()
        while self.at("or"):
            line = self.next().line
            left = BinOp("or", left, self.conjunction(), line)
        return left

    def conjunction(self) -> Node:
        left = self.negation()
        while self.at("and"):
            line = self.next().line
            left = BinOp("and", left, self.negation(), line)
        return left

    def negation(self) -> Node:
        if self.at("not") and not (self.peek().text == "in"):
            line = self.next().line
            return Not(self.negation(), line)
        if self.at("for"):
            return self.quantified()
        return self.comparison()

    def comparison(self) -> Node:
        left = self.sum()
        t = self.tok
        if t.kind == "op" and t.text in ("=", "!=", "<", "<=", ">", ">="):
            self.next()
            return BinOp(t.text, left, self.sum(), t.line)
        if self.at("in"):
            self.next()
            return BinOp("in", left, self.sum(), t.line)
        if self.at("not") and self.peek().text == "in":
            self.next()
            self.next()
            return BinOp("notin", left, self.sum(), t.line)
        return left

    def sum(self) -> Node:
        left = self.product()
        while self.tok.kind == "op" and self.tok.text in ("+", "-"):
            t = self.next()
            left = BinOp(t.text, left, self.product(), t.line)
        return left

    def product(self) -> Node:
        left = self.unary()
        while self.tok.kind == "op" and self.tok.text in ("*", "/"):
            t = self.next()
            left = BinOp(t.text, left, self.unary(), t.line)
        return left

    def unary(self) -> Node:
        if self.at("-", "op"):
            line = self.next().line
            return BinOp("-", Num(0, line), self.unary(), line)
        return self.primary()

    def primary(self) -> Node:
        t = self.tok
        if t.kind == "num":
            self.next()
            v = float(t.text)
            return Num(int(v) if v.is_integer() and "." not in t.text and "e" not in t.text.lower() else v, t.line)
        if t.kind == "str":
            return Str(self.string(), t.line)
        if t.kind == "kw" and t.text in ("true", "false", "none"):
            self.next()
            return Const({"true": True, "false": False, "none": None}[t.text], t.line)
        if t.kind == "kw" and t.text == "count":
            self.next()
            self.expect("(")
            if self.tok.kind == "name" and self._names_then_in_here():
                binders = self.binders()
                where = None
                if self.at("where"):
                    self.next()
                    where = self.formula()
                self.expect(")")
                return Gen("count", None, binders, where, t.line)
            arg = self.formula()
            self.expect(")")
            return Call("count", (arg,), t.line)
        if t.kind == "name":
            self.next()
            if self.at("("):
                self.next()
                if self.at(")"):
                    self.next()
                    return Call(t.text, (), t.line)
                first = self.formula()
                if t.text in AGGREGATES and self.at("for"):
                    self.next()
                    binders = self.binders()
                    where = None
                    if self.at("where"):
                        self.next()
                        where = self.formula()
                    self.expect(")")
                    return Gen(t.text, first, binders, where, t.line)
                args = [first]
                while self.at(","):
                    self.next()
                    args.append(self.formula())
                self.expect(")", f"to close the arguments of {t.text}")
                return Call(t.text, tuple(args), t.line)
            return Name(t.text, t.line)
        if self.at("{"):
            self.next()
            if self.at("}"):
                self.next()
                return SetLit((), t.line)
            first = self.formula()
            if self.at("for"):
                self.next()
                binders = self.binders()
                where = None
                if self.at("where"):
                    self.next()
                    where = self.formula()
                self.expect("}")
                return Gen("set", first, binders, where, t.line)
            items = [first]
            while self.at(","):
                self.next()
                items.append(self.formula())
            self.expect("}")
            return SetLit(tuple(items), t.line)
        if self.at("("):
            self.next()
            f = self.formula()
            self.expect(")")
            return f
        self.fail("expected a value, a name or a parenthesised formula")

    def _names_then_in_here(self) -> bool:
        """At a name: is this `name {, name} in` (a generator) rather than an expression?"""
        k = 0
        while self.peek(k).kind == "name":
            nxt = self.peek(k + 1)
            if nxt.text == "in" and nxt.kind == "kw":
                return True
            if nxt.text != ",":
                return False
            k += 2
        return False


def parse(text: str) -> RuleSetSource:
    """Parse one rule-set file.  Raises `RuleSyntaxError` with the line on any mistake."""
    return _Parser(text).file()


def parse_formula(text: str) -> Node:
    """Parse a single formula (the Studio's and the tests' entry point)."""
    p = _Parser(text)
    f = p.formula()
    if p.tok.kind != "eof":
        p.fail("the formula should end here")
    return f


# ----------------------------------------------------------------------------- printing

_SYM = {"<=": "≤", ">=": "≥", "!=": "≠", "in": "∈", "notin": "∉",
        "and": "and", "or": "or", "implies": "implies"}
_PREC = {"implies": 1, "or": 2, "and": 3, "=": 5, "!=": 5, "<": 5, "<=": 5, ">": 5, ">=": 5,
         "in": 5, "notin": 5, "+": 6, "-": 6, "*": 7, "/": 7}


def show(n: Node, math: bool = False) -> str:
    """One line of text for a formula; `math=True` uses the symbols the pages print."""

    member = "∈" if math else "in"

    def b(x: Binder) -> str:
        return f"{', '.join(x.names)} {member} {go(x.domain, 0)}"

    def go(n: Node, ctx: int) -> str:
        if isinstance(n, Num):
            return repr(n.value) if isinstance(n.value, float) else str(n.value)
        if isinstance(n, Str):
            return '"' + n.value.replace('"', r'\"') + '"'
        if isinstance(n, Const):
            return {True: "true", False: "false", None: "none"}[n.value]
        if isinstance(n, Name):
            return n.id
        if isinstance(n, Call):
            return f"{n.fn}({', '.join(go(a, 0) for a in n.args)})"
        if isinstance(n, SetLit):
            return "{" + ", ".join(go(a, 0) for a in n.items) + "}"
        if isinstance(n, Not):
            return f"not {go(n.arg, 4)}"
        if isinstance(n, Gen):
            gen = ", ".join(b(x) for x in n.binders)
            w = f" where {go(n.where, 0)}" if n.where is not None else ""
            if n.kind == "set":
                return "{" + f"{go(n.expr, 0)} for {gen}{w}" + "}"
            if n.kind == "count":
                return f"count({gen}{w})"
            return f"{n.kind}({go(n.expr, 0)} for {gen}{w})"
        if isinstance(n, Quant):
            q = ("∀" if n.kind == "all" else "∃") if math else f"for {n.kind}"
            w = f" where {go(n.where, 0)}" if n.where is not None else ""
            s = f"{q} {', '.join(b(x) for x in n.binders)}{w}: {go(n.body, 0)}"
            return f"({s})" if ctx > 0 else s
        if isinstance(n, BinOp):
            p = _PREC[n.op]
            op = _SYM.get(n.op, n.op) if math else ("not in" if n.op == "notin" else n.op)
            s = f"{go(n.left, p)} {op} {go(n.right, p + (0 if n.op == 'implies' else 1))}"
            return f"({s})" if p < ctx else s
        raise TypeError(n)

    return go(n, 0)


def free_names(n: Node, bound: frozenset = frozenset()) -> set[str]:
    """Names a formula reads that it does not bind itself (variables, params, nullary terms)."""
    out: set[str] = set()

    def go(n: Node, bound: frozenset) -> None:
        if isinstance(n, Name):
            if n.id not in bound:
                out.add(n.id)
        elif isinstance(n, Call):
            for a in n.args:
                go(a, bound)
        elif isinstance(n, SetLit):
            for a in n.items:
                go(a, bound)
        elif isinstance(n, Not):
            go(n.arg, bound)
        elif isinstance(n, BinOp):
            go(n.left, bound)
            go(n.right, bound)
        elif isinstance(n, (Quant, Gen)):
            inner = bound
            for x in n.binders:
                go(x.domain, inner)
                inner = inner | frozenset(x.names)
            if n.where is not None:
                go(n.where, inner)
            if isinstance(n, Quant):
                go(n.body, inner)
            elif n.expr is not None:
                go(n.expr, inner)

    go(n, bound)
    return out


def walk(n: Node) -> Iterator[Node]:
    """Every node of a formula, outermost first."""
    yield n
    if isinstance(n, Call):
        for a in n.args:
            yield from walk(a)
    elif isinstance(n, SetLit):
        for a in n.items:
            yield from walk(a)
    elif isinstance(n, Not):
        yield from walk(n.arg)
    elif isinstance(n, BinOp):
        yield from walk(n.left)
        yield from walk(n.right)
    elif isinstance(n, (Quant, Gen)):
        for x in n.binders:
            yield from walk(x.domain)
        if n.where is not None:
            yield from walk(n.where)
        if isinstance(n, Quant):
            yield from walk(n.body)
        elif n.expr is not None:
            yield from walk(n.expr)
