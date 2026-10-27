"""Deciding a rule on an execution.

A rule's formula is evaluated directly: `for all x in D where C: B` walks D and tests B.
Walking every pair of operations of a long programme would take hours, so the walk is
*planned*, and the plan never changes the answer -- only which bindings are visited:

* a condition `f(a) = g(b)` in the `where` clause joins through an index on `g` instead of
  testing every pair; `simultaneous(a, b)` joins through the steps (a programme) or a sorted
  interval list (a schedule); `next_on_qubit` and `next_run` enumerate their pairs directly;
* when the body of a `for all` can only fail for pairs that share something -- `site(a) !=
  site(b)`, `disjoint(ions(a), ions(b))` -- only those pairs are visited, since every other
  pair satisfies it;
* `for all a, b in D where simultaneous(a, b): f(a) = f(b)` on a programme is the statement
  that within a step `f` takes one value, which is checked once per step;
* `for all x in X, t in instants: ...occupancy(x, t)...` visits, at each instant, only the
  x whose occupancy (or position) changed there: at every other (x, t) the value is the one
  already judged at the previous instant.

Each rule gets a verdict -- holds, fails (with how many counterexamples and the first few,
each saying which values made it false), does not apply (and why), or could not be checked
(and why) -- and a rule never "holds" by not being evaluated.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .model import CircuitOp, Entity, Execution, Hop, Instant, Ion, Op, Site, Step
from .syntax import (
    BinOp, Binder, Call, Const, Gen, Name, Node, Not, Num, Quant, Rule, SetLit, Str, free_names,
    show,
)
from .vocabulary import RELATIONS, TERMS, Ctx, EvalError, NotApplicable

__all__ = ["RuleResult", "Report", "Evaluator", "HOLDS", "FAILS", "NOT_APPLICABLE", "UNCHECKED"]

HOLDS = "holds"
FAILS = "fails"
NOT_APPLICABLE = "does not apply"
UNCHECKED = "could not be checked"

EXAMPLES = 8


@dataclass
class RuleResult:
    id: str
    title: str
    verdict: str
    core: bool = False
    count: int = 0                    # counterexamples found
    examples: list[str] = field(default_factory=list)
    reason: str = ""                  # why it does not apply / could not be checked
    text: str = ""
    about: str = ""
    source: str = ""
    origin: str = ""

    def to_json(self) -> dict:
        d = {"id": self.id, "title": self.title, "verdict": self.verdict, "core": self.core}
        if self.count:
            d["count"] = self.count
        if self.examples:
            d["examples"] = self.examples
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class Report:
    ruleset: str
    title: str
    execution: str
    kind: str
    results: list[RuleResult]

    def by_verdict(self, verdict: str) -> list[RuleResult]:
        return [r for r in self.results if r.verdict == verdict]

    @property
    def ok(self) -> bool:
        """Every rule that applies holds, and none could not be checked."""
        return not any(r.verdict in (FAILS, UNCHECKED) for r in self.results)

    def failed(self) -> list[str]:
        return [r.id for r in self.results if r.verdict == FAILS]

    def summary(self) -> dict:
        return {"ruleset": self.ruleset, "execution": self.execution, "kind": self.kind,
                "ok": self.ok,
                "holds": [r.id for r in self.by_verdict(HOLDS)],
                "fails": {r.id: r.count for r in self.by_verdict(FAILS)},
                "does_not_apply": {r.id: r.reason for r in self.by_verdict(NOT_APPLICABLE)},
                "could_not_check": {r.id: r.reason for r in self.by_verdict(UNCHECKED)}}

    def to_json(self) -> dict:
        return {"ruleset": self.ruleset, "title": self.title, "execution": self.execution,
                "kind": self.kind, "ok": self.ok, "rules": [r.to_json() for r in self.results]}


class _Stop(Exception):
    pass


def _split_and(n: Node | None) -> list[Node]:
    if n is None:
        return []
    if isinstance(n, BinOp) and n.op == "and":
        return _split_and(n.left) + _split_and(n.right)
    return [n]


def _rename(n: Node, m: dict[str, str]) -> Node:
    """A copy of `n` with variables renamed (to compare `f(a)` with `f(b)`)."""
    if isinstance(n, Name):
        return Name(m.get(n.id, n.id), n.line)
    if isinstance(n, Call):
        return Call(n.fn, tuple(_rename(a, m) for a in n.args), n.line)
    if isinstance(n, BinOp):
        return BinOp(n.op, _rename(n.left, m), _rename(n.right, m), n.line)
    if isinstance(n, Not):
        return Not(_rename(n.arg, m), n.line)
    if isinstance(n, SetLit):
        return SetLit(tuple(_rename(a, m) for a in n.items), n.line)
    return n  # literals; quantifiers inside a join key are not renamed (never joined on)


class Evaluator:
    """Evaluates formulas of one rule set on one execution."""

    def __init__(self, exe: Execution, params: dict[str, Node] | None = None):
        self.exe = exe
        self.ctx = Ctx(exe)
        self._param_nodes = dict(params or {})
        self._param_vals: dict[str, Any] = {}
        self._sets: dict[int, tuple] = {}
        self._index: dict[tuple, dict] = {}
        self._domain_cache: dict[int, list] = {}
        self._sim_index: dict[int, Any] = {}

    # ------------------------------------------------------------------ params and names
    def param(self, name: str) -> Any:
        if name not in self._param_vals:
            node = self._param_nodes[name]
            self._param_vals[name] = None   # a cycle reads as none rather than recursing
            self._param_vals[name] = self.ev(node, {})
        return self._param_vals[name]

    def lookup(self, name: str, env: dict) -> Any:
        if name in env:
            return env[name]
        if name in self._param_nodes:
            return self.param(name)
        t = TERMS.get(name)
        if t is not None:
            if name in self.ctx.cache:
                return self.ctx.cache[name]
            v = t.fn(self.ctx)
            if t.domain and not isinstance(v, (list, tuple, set, frozenset)):
                v = list(v)
            self.ctx.cache[name] = v
            return v
        raise EvalError(f"the rule uses {name!r}, which is neither a variable, a parameter "
                        "nor a word of the vocabulary")

    # ------------------------------------------------------------------ evaluation
    def ev(self, n: Node, env: dict) -> Any:
        if isinstance(n, Name):
            return self.lookup(n.id, env)
        if isinstance(n, Call):
            t = TERMS.get(n.fn)
            if t is None:
                if n.fn == "count" and len(n.args) == 1:
                    return len(self.ev(n.args[0], env))
                raise EvalError(f"the vocabulary has no word {n.fn!r}")
            args = [self.ev(a, env) for a in n.args]
            return t.fn(self.ctx, *args)
        if isinstance(n, BinOp):
            op = n.op
            if op == "and":
                return bool(self.ev(n.left, env)) and bool(self.ev(n.right, env))
            if op == "or":
                return bool(self.ev(n.left, env)) or bool(self.ev(n.right, env))
            if op == "implies":
                return (not self.ev(n.left, env)) or bool(self.ev(n.right, env))
            a = self.ev(n.left, env)
            b = self.ev(n.right, env)
            return self.binop(op, a, b, n)
        if isinstance(n, Num):
            return n.value
        if isinstance(n, Str):
            return n.value
        if isinstance(n, Const):
            return n.value
        if isinstance(n, Not):
            return not self.ev(n.arg, env)
        if isinstance(n, SetLit):
            return frozenset(self.ev(a, env) for a in n.items)
        if isinstance(n, Quant):
            if n.kind == "all":
                for _ in self.counterexamples(n, env):
                    return False
                return True
            for e in self.bindings(n.binders, n.where, env):
                if self.ev(n.body, e):
                    return True
            return False
        if isinstance(n, Gen):
            return self.aggregate(n, env)
        raise TypeError(n)

    def binop(self, op: str, a: Any, b: Any, n: Node) -> Any:
        if op == "=":
            return _same(a, b)
        if op == "!=":
            return not _same(a, b)
        if op in ("in", "notin"):
            if b is None:
                r = False
            else:
                coll = self.as_set(b)
                r = a in coll or (isinstance(a, Entity) and str(a.id) in coll)
            return r if op == "in" else not r
        if a is None or b is None:
            raise EvalError(f"{show(n)}: a value is none ({_brief(self, a)} {op} {_brief(self, b)})")
        if isinstance(a, Instant) and isinstance(b, Instant):
            if self.exe.kind != "programme":
                # times, compared to the schedule's rounding: an operation that ends at 100 us
                # and one that starts at 99.9999999 us do not overlap
                ta, tb = self.exe.instant_time[a.id], self.exe.instant_time[b.id]
                big = max([1.0] + [abs(x) for x in (ta, tb) if not math.isinf(x)])
                # a machine's times are its scheduler's, kept to the nanosecond: the bits of
                # the classical half are tenths of a microsecond apart in a program seconds long
                tol = self.exe.settings.get("tol", 1e-6) * (1.0 if self.exe.kind == "machine" else big)
                if op in ("<=", ">=") and abs(ta - tb) <= tol:
                    return True
                if op in ("<", ">") and abs(ta - tb) <= tol:
                    return False
                a, b = ta, tb
            else:
                a, b = a.id, b.id
        try:
            if op == "<":
                return a < b
            if op == "<=":
                return a <= b
            if op == ">":
                return a > b
            if op == ">=":
                return a >= b
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return a / b if b else math.inf
        except TypeError:
            raise EvalError(f"{show(n)}: cannot compare {_brief(self, a)} with {_brief(self, b)}") from None
        raise EvalError(f"unknown operator {op}")

    def as_set(self, v: Any) -> Any:
        if isinstance(v, (set, frozenset)):
            return v
        if isinstance(v, (list, tuple)):
            if len(v) <= 32:
                return v
            # a long list is a domain, built once: keep its set, and the list itself, so
            # the id cannot be reused by another list while the entry exists
            hit = self._sets.get(id(v))
            if hit is not None and hit[0] is v:
                return hit[1]
            s = frozenset(v)
            self._sets[id(v)] = (v, s)
            return s
        raise EvalError(f"{_brief(self, v)} is not a set")

    def aggregate(self, n: Gen, env: dict) -> Any:
        if n.kind == "count":
            return sum(1 for _ in self.bindings(n.binders, n.where, env))
        vals = (self.ev(n.expr, e) for e in self.bindings(n.binders, n.where, env))
        if n.kind == "set":
            return frozenset(vals)
        if n.kind == "sum":
            return sum(vals)
        if n.kind in ("max", "min"):
            xs = list(vals)
            if not xs:
                raise EvalError(f"{n.kind} of nothing ({show(n)})")
            return max(xs) if n.kind == "max" else min(xs)
        if n.kind == "all_different":
            seen = set()
            for v in vals:
                if v in seen:
                    return False
                seen.add(v)
            return True
        raise EvalError(f"unknown aggregate {n.kind}")

    # ------------------------------------------------------------------ quantifiers
    def domain(self, node: Node, env: dict, bound: set) -> list:
        """A variable's domain; cached when it does not depend on another variable -- one of
        its own quantifier's, or one an enclosing quantifier has bound (`for some m in
        decisions(e)` inside `for all e in ...`)."""
        if not (free_names(node) & (bound | set(env))):
            key = id(node)
            v = self._domain_cache.get(key)
            if v is None:
                v = self.ev(node, {})
                v = list(v) if not isinstance(v, list) else v
                self._domain_cache[key] = v
            return v
        v = self.ev(node, env)
        if v is None:
            return []
        return list(v) if not isinstance(v, list) else v

    def bindings(self, binders: tuple[Binder, ...], where: Node | None, env: dict,
                 body: Node | None = None, refute: bool = False) -> Iterator[dict]:
        """Every extension of `env` by the binders that satisfies `where`.  With `refute`,
        bindings at which `body` certainly holds may be skipped (a `for all` only cares about
        the others)."""
        vars_: list[tuple[str, Node]] = [(nm, b.domain) for b in binders for nm in b.names]
        names = [v for v, _ in vars_]
        nameset = set(names)
        conj = _split_and(where)
        # which conjuncts can be tested once a given variable is bound
        at: dict[int, list[Node]] = {}
        pre: list[Node] = []
        for c in conj:
            used = free_names(c) & nameset
            if not used:
                pre.append(c)
            else:
                at.setdefault(max(names.index(u) for u in used), []).append(c)
        for c in pre:
            if not self.ev(c, env):
                return
        plan = self._plan(vars_, at, body if refute else None, env)
        yield from self._walk(vars_, 0, dict(env), at, plan, nameset)

    def _walk(self, vars_, i, env, at, plan, nameset) -> Iterator[dict]:
        if i == len(vars_):
            yield env
            return
        special = plan.get(("pair", i))
        if special is not None:
            # the instants pruning: (x, t) pairs generated together
            j = special["other"]
            for xv, tv in special["gen"](env):
                env[vars_[i][0]] = xv if special["first_is_x"] else tv
                env[vars_[j][0]] = tv if special["first_is_x"] else xv
                if all(self._test(c, env) for c in at.get(i, ())) and \
                        all(self._test(c, env) for c in at.get(j, ())):
                    yield from self._walk(vars_, j + 1, env, at, plan, nameset)
            env.pop(vars_[i][0], None)
            env.pop(vars_[j][0], None)
            return
        name, dom = vars_[i]
        gen = plan.get(i)
        if gen is not None:
            cands = gen(env)
        else:
            cands = self.domain(dom, env, nameset)
        tests = at.get(i, ())
        for v in cands:
            env[name] = v
            if all(self._test(c, env) for c in tests):
                yield from self._walk(vars_, i + 1, env, at, plan, nameset)
        env.pop(name, None)

    def _test(self, c: Node, env: dict) -> bool:
        return bool(self.ev(c, env))

    # ------------------------------------------------------------------ the planner
    def _plan(self, vars_, at, body, env) -> dict:
        plan: dict = {}
        names = [v for v, _ in vars_]
        nameset = set(names)
        # (x, t in instants) pairs, pruned to what changed
        pair = self._instants_pair(vars_, at, body, nameset)
        if pair is not None:
            plan[("pair", pair["first"])] = pair
            return plan
        for i, (name, dom) in enumerate(vars_):
            earlier = set(names[:i])
            if free_names(dom) & nameset:
                continue          # the domain depends on another variable: walk it as is
            joins = []
            for c in at.get(i, ()):
                j = self._join(c, name, earlier, dom, nameset)
                if j is not None:
                    joins.append(j)
            if body is not None and i == len(vars_) - 1:
                j = self._body_join(body, name, earlier, dom, nameset)
                if j is not None:
                    joins.append(j)
            if not joins:
                continue
            sims = [j for j in joins if j[0] == "sim"]
            keyed = [j for j in joins if j[0] in ("eq", "elem")]
            if sims and keyed:
                # both: the operations sharing the key, and among those only the overlapping
                k = keyed[0]
                plan[i] = self._keyed_sim_gen(k[0], k[1], k[2], sims[0][1], name, dom, nameset)
                continue
            rel = [j for j in joins if j[0] == "rel"]
            if rel:
                plan[i] = rel[0][1]
            elif sims and self.exe.kind != "programme":
                plan[i] = self._sim_gen(sims[0][1], dom, nameset)
            elif keyed:
                k = keyed[0]
                plan[i] = (self._eq_gen if k[0] == "eq" else self._elem_gen)(k[1], k[2], name, dom, nameset)
            else:
                plan[i] = self._sim_gen(sims[0][1], dom, nameset)
        return plan

    def _instants_pair(self, vars_, at, body, nameset):
        if len(vars_) != 2:
            return None
        names = [v for v, _ in vars_]
        doms = [d for _, d in vars_]
        ti = next((k for k, d in enumerate(doms) if isinstance(d, Name) and d.id == "instants"), None)
        if ti is None:
            return None
        xi = 1 - ti
        t, x = names[ti], names[xi]
        if free_names(doms[xi]) & nameset:
            return None
        formulas = [c for cs in at.values() for c in cs] + ([body] if body is not None else [])
        if not formulas:
            return None
        timelines: set[str] = set()
        ok = True

        def scan(n: Node) -> None:
            nonlocal ok
            if isinstance(n, Name) and n.id == t:
                ok = False
            elif isinstance(n, Call):
                if n.fn in ("occupancy", "position") and len(n.args) == 2 and \
                        isinstance(n.args[1], Name) and n.args[1].id == t:
                    if isinstance(n.args[0], Name) and n.args[0].id == x:
                        timelines.add(n.fn)
                        return
                    ok = False
                    return
                for a in n.args:
                    scan(a)
            elif isinstance(n, BinOp):
                scan(n.left)
                scan(n.right)
            elif isinstance(n, Not):
                scan(n.arg)
            elif isinstance(n, SetLit):
                for a in n.items:
                    scan(a)
            elif isinstance(n, (Quant, Gen)):
                for b in n.binders:
                    scan(b.domain)
                if n.where is not None:
                    scan(n.where)
                if isinstance(n, Quant):
                    scan(n.body)
                elif n.expr is not None:
                    scan(n.expr)

        for f in formulas:
            scan(f)
        if not ok or not timelines:
            return None
        exe = self.exe
        lines = [exe.occupancy if tl == "occupancy" else exe.position for tl in timelines]

        def gen(env):
            xs = self.domain(doms[xi], env, nameset)
            xset = frozenset(xs)
            insts = self.lookup("instants", {})
            for k, tv in enumerate(insts):
                if k == 0:
                    # the first instant judges every x; after it, only what changed
                    for xv in xs:
                        yield xv, tv
                    continue
                changed = set()
                for line in lines:
                    changed |= line.changed(k)
                for xv in sorted(changed & xset, key=_order):
                    yield xv, tv

        first = min(ti, xi)
        return {"first": first, "other": max(ti, xi), "first_is_x": first == xi, "gen": gen}

    def _join(self, c: Node, name: str, earlier: set, dom: Node, nameset: set):
        """A candidate generator for variable `name` from where-condition `c`, or None."""
        # simultaneous(x, name)
        if isinstance(c, Call) and c.fn == "simultaneous" and len(c.args) == 2:
            a, b = c.args
            other = None
            if isinstance(b, Name) and b.id == name and isinstance(a, Name) and a.id in earlier:
                other = a.id
            elif isinstance(a, Name) and a.id == name and isinstance(b, Name) and b.id in earlier:
                other = b.id
            if other is not None:
                return ("sim", other)
        # relation(x, name)
        if isinstance(c, Call) and c.fn in RELATIONS and len(c.args) == 2:
            a, b = c.args
            if isinstance(b, Name) and b.id == name and not (free_names(a) - earlier - _globals(self)):
                rel = RELATIONS[c.fn]
                return ("rel", lambda env, a=a, rel=rel: sorted(rel(self.ctx, self.ev(a, env)), key=_order))
        # f(earlier) = g(name)
        if isinstance(c, BinOp) and c.op == "=":
            for key_side, probe_side in ((c.right, c.left), (c.left, c.right)):
                kv = free_names(key_side) & nameset
                pv = free_names(probe_side) & nameset
                if kv == {name} and pv <= earlier:
                    return ("eq", key_side, probe_side)
        return None

    def _body_join(self, body: Node, name: str, earlier: set, dom: Node, nameset: set):
        """For a `for all`: the only bindings at which `body` can fail."""
        b = body
        # `not simultaneous(x, name)` can fail only where the two overlap
        if isinstance(b, Not) and isinstance(b.arg, Call) and b.arg.fn == "simultaneous" and \
                len(b.arg.args) == 2:
            names_ = [a.id if isinstance(a, Name) else None for a in b.arg.args]
            if name in names_:
                other = names_[1 - names_.index(name)]
                if other in earlier:
                    return ("sim", other)
        if isinstance(b, Not) and isinstance(b.arg, BinOp) and b.arg.op == "=":
            b = BinOp("!=", b.arg.left, b.arg.right, b.line)
        if isinstance(b, BinOp) and b.op == "!=":
            for key_side, probe_side in ((b.right, b.left), (b.left, b.right)):
                kv = free_names(key_side) & nameset
                pv = free_names(probe_side) & nameset
                if kv == {name} and pv <= earlier:
                    return ("eq", key_side, probe_side)
        if isinstance(b, Call) and b.fn == "disjoint" and len(b.args) == 2:
            for key_side, probe_side in ((b.args[1], b.args[0]), (b.args[0], b.args[1])):
                kv = free_names(key_side) & nameset
                pv = free_names(probe_side) & nameset
                if kv == {name} and pv <= earlier:
                    return ("elem", key_side, probe_side)
        return None

    def _keyed_sim_gen(self, kind: str, key_side: Node, probe_side: Node, other: str, name: str,
                       dom: Node, nameset: set):
        """Candidates that share a key with `other` (one value, or any element) AND happen at
        the same time: grouped by key, then by step (a programme) or by a sorted interval
        list per group (a schedule)."""
        exe = self.exe
        ikey = ("ksim", kind, id(dom), show(_rename(key_side, {name: "_"})))
        tol = exe.settings.get("tol", 1e-6)

        def build(env):
            groups: dict = {}
            for v in self.domain(dom, env, nameset):
                o = v.op if isinstance(v, Hop) else v
                if not isinstance(o, Op):
                    continue
                try:
                    k = self.ev(key_side, {name: v})
                except (EvalError, NotApplicable):
                    continue
                for kk in ([_hashable(k)] if kind == "eq" else list(k or ())):
                    if exe.kind == "programme":
                        groups.setdefault((kk, o.step), []).append(v)
                    else:
                        groups.setdefault(kk, []).append((o.t0, o.t1, v))
            if exe.kind != "programme":
                for kk, items in groups.items():
                    items.sort(key=lambda x: (x[0], x[1]))
                    groups[kk] = (items, [x[0] for x in items],
                                  max((x[1] - x[0] for x in items), default=0.0))
            return groups

        def gen(env):
            groups = self._index.get(ikey)
            if groups is None:
                groups = self._index[ikey] = build(env)
            a = env[other]
            a_op = a.op if isinstance(a, Hop) else a
            try:
                pk = self.ev(probe_side, env)
            except EvalError:
                return []
            probes = [_hashable(pk)] if kind == "eq" else list(pk or ())
            out: dict = {}
            for kk in probes:
                if exe.kind == "programme":
                    for v in groups.get((kk, a_op.step), ()):
                        out[id(v)] = v
                    continue
                g = groups.get(kk)
                if g is None:
                    continue
                items, starts, longest = g
                lo = bisect.bisect_left(starts, a_op.t0 - longest - tol)
                hi = bisect.bisect_left(starts, a_op.t1 - tol)
                for t0, t1, v in items[lo:hi]:
                    if a_op.t0 < t1 - tol:
                        out[id(v)] = v
            return sorted(out.values(), key=_order)
        return gen

    def _eq_gen(self, key_side: Node, probe_side: Node, name: str, dom: Node, nameset: set):
        ikey = ("eq", id(dom), show(_rename(key_side, {name: "_"})))

        def gen(env):
            idx = self._index.get(ikey)
            if idx is None:
                idx = {}
                for v in self.domain(dom, env, nameset):
                    try:
                        k = self.ev(key_side, {name: v})
                    except (EvalError, NotApplicable):
                        continue
                    idx.setdefault(_hashable(k), []).append(v)
                self._index[ikey] = idx
            try:
                probe = self.ev(probe_side, env)
            except EvalError:
                return []
            return idx.get(_hashable(probe), [])
        return gen

    def _elem_gen(self, key_side: Node, probe_side: Node, name: str, dom: Node, nameset: set):
        ikey = ("elem", id(dom), show(_rename(key_side, {name: "_"})))

        def gen(env):
            idx = self._index.get(ikey)
            if idx is None:
                idx = {}
                for v in self.domain(dom, env, nameset):
                    for k in self.ev(key_side, {name: v}) or ():
                        idx.setdefault(k, []).append(v)
                self._index[ikey] = idx
            out: dict = {}
            for k in self.ev(probe_side, env) or ():
                for v in idx.get(k, ()):
                    out[id(v)] = v
            return sorted(out.values(), key=_order)
        return gen

    def _sim_gen(self, other: str, dom: Node, nameset: set):
        exe = self.exe

        def gen(env):
            a = env[other]
            a_op = a.op if isinstance(a, Hop) else a
            key = id(dom)
            if exe.kind == "programme":
                idx = self._sim_index.get(key)
                if idx is None:
                    idx = {}
                    for v in self.domain(dom, env, nameset):
                        o = v.op if isinstance(v, Hop) else v
                        if isinstance(o, Op):
                            idx.setdefault(o.step, []).append(v)
                    self._sim_index[key] = idx
                return idx.get(a_op.step, [])
            idx = self._sim_index.get(key)
            if idx is None:
                items = []
                for v in self.domain(dom, env, nameset):
                    o = v.op if isinstance(v, Hop) else v
                    if isinstance(o, Op):
                        items.append((o.t0, o.t1, v))
                items.sort(key=lambda x: (x[0], x[1]))
                starts = [x[0] for x in items]
                longest = max((x[1] - x[0] for x in items), default=0.0)
                idx = (items, starts, longest)
                self._sim_index[key] = idx
            items, starts, longest = idx
            tol = exe.settings.get("tol", 1e-6)
            lo = bisect.bisect_left(starts, a_op.t0 - longest - tol)
            hi = bisect.bisect_left(starts, a_op.t1 - tol)
            return [v for t0, t1, v in items[lo:hi] if a_op.t0 < t1 - tol]
        return gen

    # ------------------------------------------------------------------ counterexamples
    def counterexamples(self, q: Quant, env: dict) -> Iterator[dict]:
        """Every binding at which a `for all` fails."""
        short = self._same_value_groups(q, env)
        if short is not None:
            yield from short
            return
        for e in self.bindings(q.binders, q.where, env, body=q.body, refute=True):
            if not self.ev(q.body, e):
                yield dict(e)

    def _same_value_groups(self, q: Quant, env: dict):
        """`for all a, b in D where simultaneous(a, b) [and g(a) = g(b)]: f(a) = f(b)` on a
        programme: within each group f takes one value.  None when the shape is different."""
        if self.exe.kind != "programme":
            return None
        vs = [(nm, b.domain) for b in q.binders for nm in b.names]
        if len(vs) != 2 or show(vs[0][1]) != show(vs[1][1]):
            return None
        a, b = vs[0][0], vs[1][0]
        if free_names(vs[0][1]) & {a, b}:
            return None
        keys: list[Node] = []
        only: dict[str, list[Node]] = {a: [], b: []}   # conditions on one of the pair
        sim = False
        for c in _split_and(q.where):
            if isinstance(c, BinOp) and c.op == "!=" and {show(c.left), show(c.right)} == {a, b}:
                continue
            used = free_names(c) & {a, b}
            if len(used) == 1:
                only[next(iter(used))].append(c)
                continue
            if isinstance(c, Call) and c.fn == "simultaneous" and \
                    {show(x) for x in c.args} == {a, b}:
                sim = True
                continue
            if isinstance(c, BinOp) and c.op == "=":
                l, r = c.left, c.right
                if free_names(l) & {a, b} == {b}:
                    l, r = r, l
                if free_names(l) & {a, b} == {a} and free_names(r) & {a, b} == {b} and \
                        show(_rename(l, {a: "_"})) == show(_rename(r, {b: "_"})):
                    keys.append(l)
                    continue
            return None
        eqs = []
        for c in _split_and(q.body):
            if isinstance(c, BinOp) and c.op == "=":
                l, r = c.left, c.right
                if free_names(l) & {a, b} == {b}:
                    l, r = r, l
                if free_names(l) & {a, b} == {a} and free_names(r) & {a, b} == {b} and \
                        show(_rename(l, {a: "_"})) == show(_rename(r, {b: "_"})):
                    eqs.append(l)
                    continue
            return None
        if not sim and not keys:
            return None

        def gen():
            groups: dict = {}
            for v in self.domain(vs[0][1], env, {a, b}):
                o = v.op if isinstance(v, Hop) else v
                k = [o.step] if sim else []
                e = {**env, a: v}
                try:
                    k += [_hashable(self.ev(x, e)) for x in keys]
                except (EvalError, NotApplicable):
                    continue
                groups.setdefault(tuple(k), []).append(v)
            for k in sorted(groups, key=lambda k: tuple(map(str, k))):
                members = groups[k]
                side_a = [v for v in members if all(self.ev(c, {**env, a: v}) for c in only[a])]
                side_b = [v for v in members if all(self.ev(c, {**env, b: v}) for c in only[b])]
                if not side_a or not side_b:
                    continue
                for f in eqs:
                    va: dict = {}
                    for v in side_a:
                        va.setdefault(_hashable(self.ev(f, {**env, a: v})), v)
                    vb: dict = {}
                    for v in side_b:
                        vb.setdefault(_hashable(self.ev(f, {**env, a: v})), v)
                    pair = next(((x, y) for kx, x in va.items() for ky, y in vb.items()
                                 if kx != ky), None)
                    if pair is not None:
                        yield {**env, a: pair[0], b: pair[1]}
                        break
        return gen()

    # ------------------------------------------------------------------ one rule
    def run(self, rule: Rule) -> RuleResult:
        res = RuleResult(rule.id, rule.title, HOLDS, core=rule.core, text=rule.text,
                         about=rule.about, source=rule.source, origin=rule.origin)
        try:
            if rule.when is not None and not self.ev(rule.when, {}):
                res.verdict = NOT_APPLICABLE
                res.reason = f"it applies only when {rule.when_text or show(rule.when)}"
                return res
            count, examples = self.explain(rule.formula, {})
            if count:
                res.verdict = FAILS
                res.count = count
                res.examples = examples
        except NotApplicable as e:
            res.verdict = NOT_APPLICABLE
            res.reason = str(e)
        except EvalError as e:
            res.verdict = UNCHECKED
            res.reason = str(e)
        return res

    def explain(self, n: Node, env: dict) -> tuple[int, list[str]]:
        """(number of counterexamples, the first few in words); (0, []) when n holds."""
        if isinstance(n, Quant) and n.kind == "all":
            count, ex = 0, []
            for e in self.counterexamples(n, env):
                count += 1
                if len(ex) < EXAMPLES:
                    ex.append(self.describe(n, e))
            return count, ex
        if isinstance(n, BinOp) and n.op == "and":
            c1, e1 = self.explain(n.left, env)
            c2, e2 = self.explain(n.right, env)
            return c1 + c2, (e1 + e2)[:EXAMPLES]
        if isinstance(n, BinOp) and n.op == "or":
            if self.ev(n.left, env) or self.ev(n.right, env):
                return 0, []
            side = n.right if _has_quant(n.right) or not _has_quant(n.left) else n.left
            return self.explain(side, env)
        if isinstance(n, BinOp) and n.op == "implies":
            if not self.ev(n.left, env):
                return 0, []
            return self.explain(n.right, env)
        if self.ev(n, env):
            return 0, []
        return 1, [self.why(n, env)]

    def describe(self, q: Quant, env: dict) -> str:
        names = [nm for b in q.binders for nm in b.names]
        who = ", ".join(f"{nm} = {_brief(self, env[nm])}" for nm in names if nm in env)
        return f"{who}: {self.why(q.body, env)}"

    def why(self, n: Node, env: dict) -> str:
        """The false atom of a formula, with its values."""
        try:
            if isinstance(n, BinOp) and n.op == "and":
                return self.why(n.left if not self.ev(n.left, env) else n.right, env)
            if isinstance(n, BinOp) and n.op == "implies":
                return self.why(n.right, env)
            if isinstance(n, BinOp) and n.op == "or":
                return f"{self.why(n.left, env)}, and {self.why(n.right, env)}"
            if isinstance(n, Quant) and n.kind == "all":
                for e in self.counterexamples(n, env):
                    return self.describe(n, e)
            if isinstance(n, BinOp) and n.op not in ("and", "or", "implies"):
                a = self.ev(n.left, env)
                b = self.ev(n.right, env)
                sym = {"<=": "≤", ">=": "≥", "!=": "≠", "in": "∈",
                       "notin": "∉"}.get(n.op, n.op)
                neg = {"<=": ">", ">=": "<", "<": "≥", ">": "≤", "=": "≠",
                       "!=": "=", "in": "∉", "notin": "∈"}.get(n.op, "not " + sym)
                return (f"{show(n.left)} is {_brief(self, a)} and {show(n.right)} is "
                        f"{_brief(self, b)}" if not _simple(n.left) or not _simple(n.right)
                        else f"{_brief(self, a)} {neg} {_brief(self, b)}")
            if isinstance(n, Not):
                return f"{show(n.arg)} holds"
            if isinstance(n, Call) and n.fn == "approx" and len(n.args) == 2:
                a = self.ev(n.args[0], env)
                b = self.ev(n.args[1], env)
                return (f"{show(n.args[0])} is {_brief(self, a)} and {show(n.args[1])} is "
                        f"{_brief(self, b)}")
        except (EvalError, NotApplicable):
            pass
        return f"{show(n)} is false"


def _same(a: Any, b: Any) -> bool:
    """Equality, where a name written in a rule ("C3") stands for the site, ion or rail of
    that name: `site(g) = "C3"` means what it says."""
    if isinstance(a, Entity) and isinstance(b, str):
        return str(a.id) == b
    if isinstance(b, Entity) and isinstance(a, str):
        return str(b.id) == a
    return a == b


def _has_quant(n: Node) -> bool:
    if isinstance(n, Quant):
        return True
    if isinstance(n, BinOp):
        return _has_quant(n.left) or _has_quant(n.right)
    if isinstance(n, Not):
        return _has_quant(n.arg)
    return False


def _simple(n: Node) -> bool:
    return isinstance(n, (Num, Str, Const, Name))


def _globals(ev: Evaluator) -> set:
    return set(ev._param_nodes) | set(TERMS)


def _hashable(v: Any) -> Any:
    if isinstance(v, list):
        return tuple(v)
    if isinstance(v, set):
        return frozenset(v)
    return v


def _order(v: Any) -> Any:
    if isinstance(v, (Op, Hop)):
        return (0, v.index, "")
    if isinstance(v, Entity):
        return (1, 0, str(v.id))
    return (2, 0, str(v))


def _brief(ev: Evaluator, v: Any) -> str:
    exe = ev.exe
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isinf(v):
            return "unlimited"
        return f"{v:.6g}"
    if isinstance(v, Instant):
        return exe.instants[v.id] if 0 <= v.id < len(exe.instants) else f"instant {v.id}"
    if isinstance(v, Step):
        st = exe.steps[v.id] if 0 <= v.id < len(exe.steps) else {}
        return f"step {v.id}" + (f" (instruction #{st['instr']})" if st.get("instr") is not None else "")
    if isinstance(v, CircuitOp):
        c = exe.circuit[v.id] if exe.circuit and 0 <= v.id < len(exe.circuit) else None
        return f"circuit operation {v.id}" + (f" ({c.text})" if c is not None and c.text else "")
    if isinstance(v, (Op, Hop)):
        return v.label()
    if isinstance(v, Entity):
        return v.label()
    if isinstance(v, (list, tuple, set, frozenset)):
        items = sorted(v, key=_order) if not isinstance(v, (list, tuple)) else list(v)
        s = ", ".join(_brief(ev, x) for x in items[:6])
        return "{" + s + (", ..." if len(items) > 6 else "") + "}"
    if callable(getattr(v, "label", None)):
        return v.label()          # a claim, or any other thing the vocabulary names in words
    return str(v)
