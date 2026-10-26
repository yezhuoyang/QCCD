"""What an evaluation means: its terms evaluated on a run, and its channels written as stim.

Three things are computed from (device, programme, evaluation) and nothing else:

* every **metric** -- a term, evaluated on the run;
* every **requirement** -- a formula, which holds or fails on the run;
* the **noisy circuit** -- the programme's stabilizer translation with the evaluation's noise
  channels put where they say.

The circuit is written step by step.  The preparation comes first (every ion reset to |0⟩),
then every step of the programme in order, then the experiment's appended readout and its
detectors.  Inside one step:

    1. every `before` channel bound to the step itself, in the order the channels are written;
    2. every `before` channel bound to an operation of the step -- channel by channel, and
       within a channel operation by operation;
    3. for each operation in order: its translation (`TRANSLATION`), then every `after`
       channel bound to it;
    4. every `after` channel bound to the step itself.

A channel whose probability is 0, or whose ions are none, writes nothing.  Ions keep their
qubits through every move, so a transport or a cooling translates to nothing on the qubits:
it reaches the circuit only through the noise its duration and its heat put there.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from ..ruleset.syntax import BinOp, Binder, Call, Const, Gen, Name, Node, Not, Num, Quant, SetLit, Str
from .model import Instant, Op, Run, Step, build_run
from .syntax import Channel, Define, If, Metric, Requirement, show
from .words import WORDS, Absent, Ctx, Hop, WordError

__all__ = ["Evaluator", "EvalError", "Circuit", "emit_circuit", "TRANSLATION"]

#: Calls whose name is also a domain: `ions(o)` is the ions of o, `ions` every ion.
CALL_ALIAS = {"ions": "ions_of", "move": "move_of"}


class EvalError(Exception):
    """A term could not be evaluated: the message says which and why."""


def _free(n: Node, bound: frozenset = frozenset()) -> frozenset:
    """The names a term reads that it does not bind itself."""
    out: set[str] = set()

    def go(x, b):
        if isinstance(x, Name):
            if x.id not in b:
                out.add(x.id)
        elif isinstance(x, Call):
            for a in x.args:
                go(a, b)
        elif isinstance(x, If):
            go(x.cond, b)
            go(x.then, b)
            go(x.other, b)
        elif isinstance(x, BinOp):
            go(x.left, b)
            go(x.right, b)
        elif isinstance(x, Not):
            go(x.arg, b)
        elif isinstance(x, SetLit):
            for a in x.items:
                go(a, b)
        elif isinstance(x, (Quant, Gen)):
            inner = b
            for bd in x.binders:
                go(bd.domain, inner)
                inner = inner | frozenset(bd.names)
            if x.where is not None:
                go(x.where, inner)
            if isinstance(x, Quant):
                go(x.body, inner)
            elif x.expr is not None:
                go(x.expr, inner)

    go(n, bound)
    return frozenset(out)


class Evaluator:
    """Evaluates the terms of one evaluation on one run."""

    def __init__(self, ev, run: Run, sample=None, checks: Mapping | None = None):
        self.ev_ = ev
        self.run = run
        self.ctx = Ctx(run, sample, dict(checks or {}))
        self.params = dict(ev.params)
        self.defines = {d.name: d for d in ev.defines}
        self.metrics = {m.name: m for m in ev.metrics_}
        self._vals: dict[str, Any] = {}
        self._busy: set[str] = set()
        self._memo: dict[tuple, Any] = {}
        self._free: dict[int, frozenset] = {}
        self._domains: dict[int, list] = {}

    # ------------------------------------------------------------------ names
    def value(self, name: str) -> Any:
        """A parameter, a define without arguments, or a metric, by name (each evaluated once)."""
        if name in self._vals:
            return self._vals[name]
        if name in self._busy:
            raise EvalError(f"{name} is defined in terms of itself")
        self._busy.add(name)
        try:
            if name in self.params:
                v = self.term(self.params[name], {})
            elif name in self.defines and not self.defines[name].args:
                v = self.term(self.defines[name].body, {})
            elif name in self.metrics:
                v = self.term(self.metrics[name].body, {})
            else:
                raise KeyError(name)
        finally:
            self._busy.discard(name)
        self._vals[name] = v
        return v

    def known(self, name: str) -> bool:
        return (name in self.params or name in self.metrics
                or (name in self.defines and not self.defines[name].args))

    def lookup(self, name: str, env: dict) -> Any:
        if name in env:
            return env[name]
        if self.known(name):
            return self.value(name)
        w = WORDS.get(name)
        if w is not None:
            key = ("word", name)
            if key not in self._memo:
                self._memo[key] = w.fn(self.ctx)
            return self._memo[key]
        raise EvalError(f"{name!r} is neither a variable, a parameter, a define, a metric nor a word")

    # ------------------------------------------------------------------ terms
    def term(self, n: Node, env: dict) -> Any:
        if isinstance(n, Num):
            return n.value
        if isinstance(n, Str):
            return n.value
        if isinstance(n, Const):
            return n.value
        if isinstance(n, Name):
            return self.lookup(n.id, env)
        if isinstance(n, If):
            return self.term(n.then if self.truth(n.cond, env) else n.other, env)
        if isinstance(n, Call):
            return self.call(n, env)
        if isinstance(n, BinOp):
            op = n.op
            if op == "and":
                return self.truth(n.left, env) and self.truth(n.right, env)
            if op == "or":
                return self.truth(n.left, env) or self.truth(n.right, env)
            if op == "implies":
                return (not self.truth(n.left, env)) or self.truth(n.right, env)
            return self.binop(op, self.term(n.left, env), self.term(n.right, env), n)
        if isinstance(n, Not):
            return not self.truth(n.arg, env)
        if isinstance(n, SetLit):
            return frozenset(self.term(a, env) for a in n.items)
        if isinstance(n, (Gen, Quant)):
            return self.memo(n, env)
        raise EvalError(f"cannot evaluate {n!r}")

    def truth(self, n: Node, env: dict) -> bool:
        return bool(self.term(n, env))

    def memo(self, n, env: dict) -> Any:
        """An aggregate or a quantifier depends only on the values of its free names, so its
        value is kept per binding of them: `{position(j, start(k)) for j in operated(k)}` is
        computed once per step, not once per ion the outer set asks about."""
        fr = self._free.get(id(n))
        if fr is None:
            fr = self._free[id(n)] = _free(n)
        try:
            key = (id(n),) + tuple((x, env[x]) for x in sorted(fr) if x in env)
            hash(key)
        except TypeError:
            key = None
        if key is not None and key in self._memo:
            return self._memo[key]
        v = self.aggregate(n, env) if isinstance(n, Gen) else self.quant(n, env)
        if key is not None:
            self._memo[key] = v
        return v

    def call(self, n: Call, env: dict) -> Any:
        fn = n.fn
        d = self.defines.get(fn)
        if d is not None:
            if len(d.args) != len(n.args):
                raise EvalError(f"{fn} takes {len(d.args)} argument(s), given {len(n.args)}")
            args = [self.term(a, env) for a in n.args]
            if fn in self._busy:
                raise EvalError(f"{fn} is defined in terms of itself")
            self._busy.add(fn)
            try:
                return self.term(d.body, dict(zip(d.args, args)))
            finally:
                self._busy.discard(fn)
        if fn == "count" and len(n.args) == 1:
            return len(self.term(n.args[0], env))
        w = WORDS.get(CALL_ALIAS.get(fn, fn))
        if w is None:
            raise EvalError(f"the vocabulary has no word {fn!r}")
        args = [self.term(a, env) for a in n.args]
        try:
            return w.fn(self.ctx, *args)
        except (WordError, Absent):
            raise
        except TypeError as exc:
            raise EvalError(f"{fn}: {exc}") from None

    def binop(self, op: str, a: Any, b: Any, n: Node) -> Any:
        if op == "=":
            return _same(a, b)
        if op == "!=":
            return not _same(a, b)
        if op in ("in", "notin"):
            r = (a in b) if b is not None else False
            return r if op == "in" else not r
        if a is None or b is None:
            raise EvalError(f"{show(n)}: a value is none")
        if op == "+" and isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return list(a) + [x for x in b if x not in a]
        if isinstance(a, Instant) and isinstance(b, Instant):
            a, b = (a.step.index, a.edge == "end"), (b.step.index, b.edge == "end")
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
            raise EvalError(f"{show(n)}: cannot combine {a!r} and {b!r}") from None
        raise EvalError(f"unknown operator {op}")

    # ------------------------------------------------------------------ binders
    def domain(self, node: Node, env: dict, bound: set) -> list:
        fr = self._free.get(id(node))
        if fr is None:
            fr = self._free[id(node)] = _free(node)
        if not (fr & (bound | set(env))):
            v = self._domains.get(id(node))
            if v is None:
                v = self.term(node, {})
                v = list(v) if v is not None else []
                self._domains[id(node)] = v
            return v
        v = self.term(node, env)
        return list(v) if v is not None else []

    def bindings(self, binders: tuple[Binder, ...], where: Node | None, env: dict) -> Iterator[dict]:
        vars_ = [(nm, b.domain) for b in binders for nm in b.names]

        def walk(i: int, e: dict) -> Iterator[dict]:
            if i == len(vars_):
                if where is None or self.truth(where, e):
                    yield e
                return
            nm, dom = vars_[i]
            for x in self.domain(dom, e, {v for v, _ in vars_}):
                e2 = dict(e)
                e2[nm] = x
                yield from walk(i + 1, e2)

        yield from walk(0, dict(env))

    def aggregate(self, n: Gen, env: dict) -> Any:
        if n.kind == "count":
            return sum(1 for _ in self.bindings(n.binders, n.where, env))
        vals = [self.term(n.expr, e) for e in self.bindings(n.binders, n.where, env)]
        if n.kind == "set":
            return frozenset(vals)
        if n.kind == "sum":
            total = 0
            for v in vals:
                total = total + v
            return total
        if n.kind in ("max", "min"):
            if not vals:
                raise EvalError(f"{n.kind} of nothing ({show(n)})")
            return max(vals) if n.kind == "max" else min(vals)
        if n.kind == "all_different":
            return len(set(vals)) == len(vals)
        raise EvalError(f"unknown aggregate {n.kind}")

    def quant(self, q: Quant, env: dict) -> bool:
        for e in self.bindings(q.binders, q.where, env):
            ok = self.truth(q.body, e)
            if q.kind == "all" and not ok:
                return False
            if q.kind == "some" and ok:
                return True
        return q.kind == "all"

    def counterexamples(self, q: Quant, env: dict, limit: int = 8) -> list[str]:
        out = []
        for e in self.bindings(q.binders, q.where, env):
            if not self.truth(q.body, e):
                out.append(", ".join(f"{k} = {_label(v)}" for k, v in e.items() if k not in env))
                if len(out) >= limit:
                    break
        return out


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return a == b
        except TypeError:
            return False
    return a == b


def _label(v) -> str:
    return v.label() if hasattr(v, "label") else repr(v) if not isinstance(v, str) else v


# ============================================================================ the circuit

def _translate(c, op: Op, sq: dict[str, int]) -> None:
    """`TRANSLATION`: one operation as stabilizer operations on its ions' qubits."""
    from ..qec.native import emit_abstract, emit_ms, emit_r, emit_vz, canonical_abstract

    if op.kind == "prepare":
        c.append("R", sorted(sq[i] for i in op.ions))
    elif op.kind == "gate2":
        a, b = (sq[i] for i in op.ions)
        if op.gate == "MS":
            if not op.params:
                raise EvalError(f"{op.label()}: MS carries no angle")
            emit_ms(c, a, b, op.params[0])
        else:
            emit_abstract(c, canonical_abstract(op.gate), [a, b])
    elif op.kind == "gate1":
        q = sq[op.ions[0]]
        if op.gate == "R":
            if len(op.params) < 2:
                raise EvalError(f"{op.label()}: R needs two angles")
            emit_r(c, q, op.params[0], op.params[1])
        elif op.gate == "VZ":
            if not op.params:
                raise EvalError(f"{op.label()}: VZ carries no angle")
            emit_vz(c, q, op.params[0])
        else:
            emit_abstract(c, canonical_abstract(op.gate), [q])
    elif op.kind == "measure":
        c.append("M" if op.basis == "Z" else "MX", [sq[op.ions[0]]])
    elif op.kind == "reset":
        c.append("R", [sq[op.ions[0]]])
    # move, cool: an ion keeps its qubit wherever it is carried; nothing on the qubits


#: What every operation becomes, for the page (the rows `_translate` implements).
TRANSLATION = (
    ("the preparation", "R on every qubit", "every ion starts in |0⟩"),
    ("gate MS(θ) on a, b", "SQRT_XX a b for θ = π/2, SQRT_XX_DAG for −π/2, X a and X b for π, "
     "nothing for 0", "MS(θ) = exp(−iθ/2 X⊗X)"),
    ("gate R(θ, φ) on a", "by quarter turns of θ about the axis φ: X or Y for a half turn, "
     "SQRT_X / SQRT_X_DAG / SQRT_Y / SQRT_Y_DAG for a quarter, nothing for none",
     "R(θ, φ) = exp(−iθ/2 (cos φ X + sin φ Y))"),
    ("gate VZ(λ) on a", "S, λ/(π/2) times (mod 4)", "VZ(λ) = diag(1, e^{iλ})"),
    ("gate CX / CZ / SWAP / H / S / ...", "the stim gate of that name", "the gadget layer's Clifford names"),
    ("measure a", "M a (MX a for an X-basis readout)", "its record is the classical bit it writes"),
    ("reset a", "R a", ""),
    ("transport, cool, init, barrier", "nothing", "an ion keeps its qubit wherever it is carried"),
)


@dataclass
class Circuit:
    """The noisy circuit, where every line came from, and its expected faults per channel."""

    lines: list[str]
    budget: dict[str, float]
    trace: list[dict] = field(default_factory=list)      # {step, label, a, b, why}
    #: for every line, what wrote it: "translation", a channel's name, or "detectors"
    origin: list[str] = field(default_factory=list)
    qubit_of_ion: dict[str, int] = field(default_factory=dict)
    measurements: int = 0

    def stim(self):
        import stim
        return stim.Circuit("\n".join(self.lines))


def emit_circuit(evaluator: Evaluator, channels: list[Channel]) -> Circuit:
    """Write the run's noisy circuit (see the module docstring for the order)."""
    from ..qec.stimtext import StimText, declare

    run = evaluator.run
    exp = run.experiment
    if exp is None:
        raise EvalError("a circuit is read against an experiment: none was given")
    src = run.facts["source"]
    bits_of = src.bits_by_qubit()

    # -- qubits: circuit qubit q is stim qubit q; ions holding no circuit qubit come after
    sq: dict[str, int] = {ion: q for q, ion in run.qubit_map.items()}
    missing = sorted(q for q in range(src.n_qubits) if q not in run.qubit_map)
    if missing:
        raise EvalError(f"no ion holds circuit qubit(s) {missing[:8]}")
    unplaced = sorted(i for i in sq if i not in run.ions)
    if unplaced:
        raise EvalError(f"the qubit map names ion(s) {unplaced[:8]} the programme never places")
    for ion in run.ions:
        if ion not in sq:
            sq[ion] = src.n_qubits + sum(1 for i in sq if sq[i] >= src.n_qubits)
    qubit_of = {ion: q for ion, q in sq.items() if q < src.n_qubits}

    # -- every channel's bindings, grouped by what they are bound to
    step_before: dict[int, list] = defaultdict(list)
    op_before: dict[int, list] = defaultdict(list)
    op_after: dict[int, list] = defaultdict(list)
    step_after: dict[int, list] = defaultdict(list)
    for ci, ch in enumerate(channels):
        for e in evaluator.bindings(ch.binders, ch.where, {}):
            anchor = e[ch.binders[0].names[0]]
            p = evaluator.term(ch.prob, e)
            if p is None or p <= 0:
                continue
            tg = evaluator.term(ch.targets, e)
            targets = [tg] if isinstance(tg, str) else list(tg or ())
            if not targets:
                continue
            item = (ci, ch, float(p), targets, e)
            if isinstance(anchor, Op):
                (op_before if ch.when == "before" else op_after)[anchor.index].append(item)
            elif isinstance(anchor, Step):
                key = (anchor.kind, anchor.index)
                (step_before if ch.when == "before" else step_after)[key].append(item)
            else:
                raise EvalError(f"channel {ch.name} is bound to {anchor!r}: a channel is bound to "
                                f"an operation or a step")

    c = StimText()
    origin: list[str] = []
    budget = {ch.name: 0.0 for ch in channels}
    trace: list[dict] = []
    meas_of_bit: dict[int, int] = {}
    count: dict[int, int] = defaultdict(int)
    n_meas = 0

    def write(items, why: list[str]) -> None:
        for _, ch, p, targets, e in items:
            qs = sorted(sq[i] for i in targets)
            if ch.op == "DEPOLARIZE2":
                qs = [sq[i] for i in targets]
                if len(qs) != 2:
                    raise EvalError(f"channel {ch.name}: DEPOLARIZE2 on {len(qs)} ion(s)")
                budget[ch.name] += p
            else:
                budget[ch.name] += p * len(qs)
            c.append(ch.op, qs, p)
            origin.append(ch.name)
            why.append(f"{ch.name}: {ch.op}({p:.3g}) on {len(qs)} ion(s)")

    for st in run.all_steps:
        a = len(c.lines)
        why: list[str] = []
        key = (st.kind, st.index)
        write(sorted(step_before.get(key, ()), key=lambda t: t[0]), why)
        ops_ = st.ops
        pre = [it for o in ops_ for it in op_before.get(o.index, ())]
        write(sorted(pre, key=lambda t: t[0]), why)      # channel by channel; stable within
        for o in ops_:
            for ion in o.ions:
                if ion not in sq:
                    raise EvalError(f"{o.label()} names an ion the programme never places")
            n0 = len(c.lines)
            _translate(c, o, sq)
            origin.extend(["translation"] * (len(c.lines) - n0))
            if o.kind == "measure":
                ion = o.ions[0]
                if ion not in qubit_of:
                    raise EvalError(f"{o.label()} measures ion {ion}, which holds no circuit qubit")
                q = qubit_of[ion]
                if st.kind == "readout":
                    b = next(b for qq, b, _ in exp.readout_plan() if qq == q)
                    if b in meas_of_bit:
                        raise EvalError(f"the source circuit itself writes the readout bit {b}")
                else:
                    k = count[q]
                    count[q] += 1
                    bits = bits_of.get(q, [])
                    if k >= len(bits):
                        raise EvalError(f"the programme measures circuit qubit {q} (ion {ion}) more "
                                        f"than the {len(bits)} time(s) the source circuit does")
                    b = bits[k]
                meas_of_bit[b] = n_meas
                n_meas += 1
            write(sorted(op_after.get(o.index, ()), key=lambda t: t[0]), why)
        write(sorted(step_after.get(key, ()), key=lambda t: t[0]), why)
        trace.append({"step": st, "label": st.label(), "a": a, "b": len(c.lines), "why": why})
    short = [f"qubit {q}: {count[q]} of {len(b)}" for q, b in sorted(bits_of.items()) if count[q] != len(b)]
    if short:
        raise EvalError("the programme measures some circuit qubits fewer times than the source "
                        "circuit does (" + "; ".join(short[:6]) + ")")
    a = len(c.lines)
    try:
        declare(c, exp.detectors, exp.observables, meas_of_bit, n_meas)
    except KeyError as exc:
        raise EvalError(str(exc)) from None
    origin.extend(["detectors"] * (len(c.lines) - a))
    trace.append({"step": None, "label": "the detectors", "a": a, "b": len(c.lines),
                  "why": ["the experiment's detectors and observables, over the measurement records"]})
    return Circuit(lines=list(c.lines), budget=budget, trace=trace, qubit_of_ion=dict(sq),
                   measurements=n_meas, origin=origin)
