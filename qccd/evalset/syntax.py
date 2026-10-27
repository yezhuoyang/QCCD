"""The evaluation language: its grammar, its parser, and how an item is printed.

An evaluation is a text file that says how a design and its programme are judged.  It uses
the rule language's formulas and terms (`qccd.ruleset.syntax`) unchanged, and adds the items
an evaluation needs besides assertions: named numbers (`metric`), noise (`channel`), and
the assertions a design must meet to be ranked (`require`).

    evaluation "goal1"
    title "Goal 1: the best QEC memory under 200 ions"
    include "metrics"
    include "qccd-noise@2"
    param max_ions = 200

    metric ions "Ions" unit "ions" better low
      = count(i in ions)

    require E1 "At most 200 ions"
      ions <= max_ions

    channel ms "Two-ion gate error"
      after g in two_qubit_gates:
        DEPOLARIZE2(min(15 / 16, ms_error(g))) on ions(g)

Grammar (EBNF, extending the rule language's; `#` starts a comment):

    file      = { item } ;
    item      = "evaluation" STRING | "title" STRING | "about" STRING
              | "include" STRING | "clock" STRING | "rules" STRING | "rank" NAME
              | "param" NAME "=" expr
              | "define" NAME [ "(" NAME { "," NAME } ")" ] "=" expr
              | metric | requirement | channel ;
    metric    = "metric" NAME STRING { "unit" STRING | "better" ( "low" | "high" ) | "clock" STRING
                                     | "group" STRING | "about" STRING | "source" STRING }
                "=" expr ;
    requirement = "require" ID STRING { "about" STRING | "source" STRING } formula ;
    channel   = "channel" NAME STRING { "about" STRING | "source" STRING }
                ( "before" | "after" ) binders [ "where" formula ] ":"
                NOISE "(" expr ")" "on" expr [ "counted" "as" budget ] ;
    budget    = NAME | NAME "=" expr { "," NAME "=" expr } ;
    NOISE     = "X_ERROR" | "Y_ERROR" | "Z_ERROR" | "DEPOLARIZE1" | "DEPOLARIZE2" ;
    primary   = ... the rule language's primaries ...
              | "if" formula "then" expr "else" expr ;

A `define` names a term, with or without arguments; it may use earlier params, defines and
metrics, never itself.  A `channel` is read as: at every binding of its variables that
satisfies `where`, put the noise operation with that probability on those ions, just
before (or just after) what the binding names.  Its expected faults -- the probability, per
ion (per pair for DEPOLARIZE2) -- are booked under the channel's name, or under `counted as`
a key; with several `key = term` they are shared out in proportion to the terms.  A metric
with a `clock` of its own is evaluated on a replay under that operating-point table.  `qccd.evalset.interp` gives every item its
meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ruleset.syntax import (
    Binder, Const, Node, RuleSyntaxError, _Parser, show as _show_rule,
)

__all__ = ["EvalSyntaxError", "If", "Define", "Metric", "Requirement", "Channel",
           "EvaluationSource", "parse", "parse_term", "show", "NOISE_OPS", "ITEM_NAMES"]


class EvalSyntaxError(RuleSyntaxError):
    """An evaluation that does not parse; the message names the line and what was expected."""


#: The noise operations a channel may write: stim's Pauli channels, by their stim names.
NOISE_OPS = ("X_ERROR", "Y_ERROR", "Z_ERROR", "DEPOLARIZE1", "DEPOLARIZE2")
#: Words that begin an item when they start a line.  They are names to the rule language's
#: tokenizer, so a formula ends where one of them begins a line.
ITEM_NAMES = frozenset({"evaluation", "title", "about", "include", "clock", "rules", "rank",
                        "param", "define", "metric", "require", "channel"})


@dataclass(eq=False, slots=True)
class If(Node):
    cond: Node
    then: Node
    other: Node
    line: int = 0


@dataclass
class Define:
    name: str
    args: tuple[str, ...]
    body: Node
    text: str = ""
    line: int = 0
    origin: str = ""


@dataclass
class Metric:
    name: str
    title: str
    body: Node
    unit: str = ""
    better: str = ""          # "low" | "high" | ""
    group: str = ""
    about: str = ""
    source: str = ""
    clock: str = ""           # an operating-point table of its own, else the evaluation's
    text: str = ""
    line: int = 0
    origin: str = ""


@dataclass
class Requirement:
    id: str
    title: str
    formula: Node
    about: str = ""
    source: str = ""
    text: str = ""
    line: int = 0
    origin: str = ""


@dataclass
class Channel:
    name: str
    title: str
    when: str                 # "before" | "after"
    binders: tuple[Binder, ...]
    where: Node | None
    op: str                   # one of NOISE_OPS
    prob: Node
    targets: Node
    about: str = ""
    source: str = ""
    text: str = ""            # the clause as written, from before/after to the end
    line: int = 0
    origin: str = ""
    #: `counted as`: the budget line(s) the channel's expected faults are booked under -- one
    #: key, or several keys each with a term, the probability shared out in proportion
    counted: tuple[tuple[str, Node | None], ...] = ()


@dataclass
class EvaluationSource:
    """One file, parsed but not resolved: `include` is applied by `qccd.evalset.load`."""

    name: str = ""
    title: str = ""
    about: str = ""
    includes: list[str] = field(default_factory=list)
    clock: str = ""
    rules: str = ""
    rank: str = ""
    params: dict[str, Node] = field(default_factory=dict)
    param_text: dict[str, str] = field(default_factory=dict)
    defines: list[Define] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)


def _table(s: str, line: int) -> str:
    """A clock is an operating-point table the device format declares (`qccd.arch.curves.
    TABLES`): a misspelt one would price every step on some other table, silently."""
    from ..arch.curves import TABLES
    if s not in TABLES:
        raise EvalSyntaxError(f"line {line}: clock {s!r} is not an operating-point table "
                              f"(one of {', '.join(TABLES)})")
    return s


class _EvalParser(_Parser):
    """The rule parser, with the evaluation's items and the conditional term."""

    def at_name(self, text: str) -> bool:
        t = self.tok
        return t.kind == "name" and t.text == text

    def expect_name(self, text: str, what: str = "") -> None:
        if not self.at_name(text):
            self.fail(f"expected {text!r}{' ' + what if what else ''}")
        self.next()

    def item_start(self) -> bool:
        t = self.tok
        return t.kind == "eof" or (t.bol and (
            (t.kind == "name" and t.text in ITEM_NAMES) or (t.kind == "kw" and t.text in ITEM_NAMES)))

    def fail(self, msg: str):
        t = self.tok
        shown = t.text or "end of file"
        raise EvalSyntaxError(f"line {t.line}, column {t.col}: {msg}, found {shown!r}")

    # -- the conditional term
    def primary(self) -> Node:
        if self.at_name("if"):
            line = self.next().line
            cond = self.formula()
            self.expect_name("then", "after the condition of `if`")
            then = self.formula()
            self.expect_name("else", "after the `then` branch")
            other = self.formula()
            return If(cond, then, other, line)
        return super().primary()

    # -- the file
    def evaluation(self) -> EvaluationSource:
        ev = EvaluationSource()
        while self.tok.kind != "eof":
            t = self.tok
            if not t.bol:
                self.fail("expected a new item at the start of a line")
            word = t.text
            if word in ("evaluation", "title", "include", "clock", "rules"):
                self.next()
                s = self.string()
                if word == "evaluation":
                    ev.name = s
                elif word == "title":
                    ev.title = s
                elif word == "include":
                    ev.includes.append(s)
                elif word == "clock":
                    ev.clock = _table(s, t.line)
                else:
                    ev.rules = s
            elif word == "about":
                self.next()
                ev.about = self.string()
            elif word == "rank":
                self.next()
                ev.rank = self.ident()
            elif word == "param":
                self.next()
                name = self.ident()
                self.expect("=")
                a = self.i
                ev.params[name] = self.formula()
                ev.param_text[name] = self.source_between(a, self.i)
            elif word == "define":
                ev.defines.append(self.define())
            elif word == "metric":
                ev.metrics.append(self.metric())
            elif word == "require":
                ev.requirements.append(self.requirement())
            elif word == "channel":
                ev.channels.append(self.channel())
            else:
                self.fail("expected evaluation, title, about, include, clock, rules, rank, param, "
                          "define, metric, require or channel")
            if not self.item_start():
                self.fail("the item should end here")
        for kind, items in (("metric", [(m.name, m.line) for m in ev.metrics]),
                            ("requirement", [(r.id, r.line) for r in ev.requirements]),
                            ("channel", [(c.name, c.line) for c in ev.channels]),
                            ("define", [(d.name, d.line) for d in ev.defines])):
            seen: dict[str, int] = {}
            for name, line in items:
                if name in seen:
                    raise EvalSyntaxError(f"line {line}: {kind} {name} is defined twice "
                                          f"(first on line {seen[name]})")
                seen[name] = line
        return ev

    def _notes(self, item) -> None:
        """`about` / `source` (and a metric's `unit`, `better`, `group`) in any order."""
        while True:
            if self.at("about"):
                self.next()
                item.about = self.string()
            elif self.at("source"):
                self.next()
                item.source = self.string()
            elif isinstance(item, Metric) and self.at_name("unit"):
                self.next()
                item.unit = self.string()
            elif isinstance(item, Metric) and self.at_name("group"):
                self.next()
                item.group = self.string()
            elif isinstance(item, Metric) and self.at_name("clock"):
                line = self.next().line
                item.clock = _table(self.string(), line)
            elif isinstance(item, Metric) and self.at_name("better"):
                self.next()
                b = self.ident()
                if b not in ("low", "high"):
                    self.fail("a metric is better `low` or `high`")
                item.better = b
            else:
                return

    def define(self) -> Define:
        line = self.next().line
        name = self.ident()
        args: list[str] = []
        if self.at("("):
            self.next()
            if not self.at(")"):
                args.append(self.ident())
                while self.at(","):
                    self.next()
                    args.append(self.ident())
            self.expect(")", f"to close the arguments of {name}")
        self.expect("=", f"after define {name}")
        a = self.i
        body = self.formula()
        return Define(name, tuple(args), body, self.source_between(a, self.i), line)

    def metric(self) -> Metric:
        line = self.next().line
        name = self.ident()
        title = self.string()
        m = Metric(name, title, Const(None), line=line)
        self._notes(m)
        self.expect("=", f"before the definition of metric {name}")
        a = self.i
        m.body = self.formula()
        m.text = self.source_between(a, self.i)
        return m

    def requirement(self) -> Requirement:
        line = self.next().line
        rid = self.ident()
        title = self.string()
        r = Requirement(rid, title, Const(True), line=line)
        self._notes(r)
        if self.item_start():
            self.fail(f"requirement {rid} has no formula")
        a = self.i
        r.formula = self.formula()
        r.text = self.source_between(a, self.i)
        return r

    def channel(self) -> Channel:
        line = self.next().line
        name = self.ident()
        title = self.string()
        c = Channel(name, title, "after", (), None, "", Const(0), Const(None), line=line)
        self._notes(c)
        a = self.i
        if self.at_name("before") or self.at_name("after"):
            c.when = self.next().text
        else:
            self.fail(f"channel {name}: expected `before` or `after`")
        c.binders = self.binders()
        if self.at("where"):
            self.next()
            c.where = self.formula()
        self.expect(":", f"before channel {name}'s noise")
        if self.tok.kind != "name" or self.tok.text not in NOISE_OPS:
            self.fail(f"channel {name}: expected a noise operation ({', '.join(NOISE_OPS)})")
        c.op = self.next().text
        self.expect("(", f"after {c.op}")
        c.prob = self.formula()
        self.expect(")", f"to close {c.op}'s probability")
        self.expect_name("on", f"after {c.op}(...)")
        c.targets = self.sum()
        if self.at_name("counted"):
            self.next()
            self.expect_name("as", "after `counted`")
            parts = []
            while True:
                key = self.ident()
                term = None
                if self.at("="):
                    self.next()
                    term = self.sum()
                parts.append((key, term))
                if not self.at(","):
                    break
                self.next()
            if len(parts) > 1 and any(t is None for _, t in parts):
                self.fail(f"channel {name}: several budget lines each need a term (`key = term`)")
            c.counted = tuple(parts)
        c.text = self.source_between(a, self.i)
        return c


def parse(text: str) -> EvaluationSource:
    """Parse one evaluation file.  Raises `EvalSyntaxError` with the line on any mistake."""
    try:
        return _EvalParser(text).evaluation()
    except EvalSyntaxError:
        raise
    except RuleSyntaxError as exc:   # the tokenizer's own errors
        raise EvalSyntaxError(str(exc)) from None


def parse_term(text: str) -> Node:
    """Parse a single term or formula (the tests' and the page's entry point)."""
    p = _EvalParser(text)
    f = p.formula()
    if p.tok.kind != "eof":
        p.fail("the term should end here")
    return f


def show(n: Node, math: bool = False) -> str:
    """One line for a term; the conditional is printed `if C then a else b`."""
    if isinstance(n, If):
        return f"if {show(n.cond, math)} then {show(n.then, math)} else {show(n.other, math)}"
    try:
        return _show_rule(n, math)
    except TypeError:
        return repr(n)


def words_in(n: Node) -> set[str]:
    """Every function or name a term calls or reads (for the page's links)."""
    from ..ruleset.syntax import BinOp, Call, Gen, Name, Not, Quant, SetLit

    out: set[str] = set()

    def go(x) -> None:
        if isinstance(x, Name):
            out.add(x.id)
        elif isinstance(x, Call):
            out.add(x.fn)
            for a in x.args:
                go(a)
        elif isinstance(x, If):
            go(x.cond)
            go(x.then)
            go(x.other)
        elif isinstance(x, BinOp):
            go(x.left)
            go(x.right)
        elif isinstance(x, Not):
            go(x.arg)
        elif isinstance(x, SetLit):
            for a in x.items:
                go(a)
        elif isinstance(x, (Quant, Gen)):
            for b in x.binders:
                go(b.domain)
            if x.where is not None:
                go(x.where)
            if isinstance(x, Quant):
                go(x.body)
            elif x.expr is not None:
                go(x.expr)

    go(n)
    return out
