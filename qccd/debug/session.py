"""A programme, step by step: what the debugger reads.

One `Session` is one programme replayed once on one device under one clock.  It answers the
questions a person (or an agent) debugging a compiler or a hand-written programme asks:

    what is step k, and what did it do?             step(k)
    where is everything after step k?               state(k, watch=...)
    what changed between step a and step b?         diff(a, b)
    what did this ion / site / junction / rail do?  trace(entity)
    which rules fail, at which step, and where?     check(rules, assertions)
    when does this condition first hold?            find(condition, start)
    what is this expression at step k?              value(expr, k)

**Steps are numbered from 1, the way the Studio's status line numbers them** ("Step 7 / 11"
is step 7): step k is the k-th instruction of the programme, and frame k-1 of the Studio's
animation.  A failure this module locates at step 7 is drawn when the page says Step 7.  The
grader's own sentences number the replay's steps differently (from 0, without the inits);
every sentence this module returns is renumbered to the page's steps.

**Rules are the rule language's** (`qccd.ruleset`): the same parser, vocabulary and
evaluator the grader uses, so a verdict here is the grader's verdict.  What this module adds
is *where*: every counterexample is turned into the step it happens at, the steps it lasts
through, and the places on the device it names (sites, junctions, rails, ions), so the page
can put a red cross on them.

**Conditions and expressions are the rule language too.**  A breakpoint is a formula over
the instant `t` (`occupancy("C3", t) > 1`); a watch expression is a term
(`heat("q0", t)`).  Nothing new to learn: the Rules page is the manual.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..ruleset import RuleSet, RuleSyntaxError, catalogue
from ..ruleset import load as load_rules
from ..ruleset.evaluate import (FAILS, HOLDS, NOT_APPLICABLE, UNCHECKED, Evaluator, _brief, _has_quant,
                                _split_and)
from ..ruleset.model import CircuitOp, Entity, Hop, Instant, Ion, Loop, Op, Rail, Site
from ..ruleset.model import Step as StepEntity
from ..ruleset.programme import programme_execution
from ..ruleset.syntax import BinOp, Call, Not, Quant, Rule, Str, free_names, parse, parse_formula, show, walk
from ..ruleset.vocabulary import TERMS, EvalError, NotApplicable

__all__ = ["Session", "Failure", "model_from", "open_files", "open_documents", "assertion_rules",
           "DebugError"]

_INSTANT = re.compile(r"^(during|after) step (\d+)$")
_STEP_WORD = re.compile(r"\bstep (\d+)\b")

#: located failures kept per rule (the count is always the full count)
FAILURES_PER_RULE = 400
#: evaluations a rule may spend measuring how long its failures last
SPAN_BUDGET = 60_000
#: bindings a rule may try at one step (`rules_at`, `inspect`)
CASES_PER_STEP = 20_000
#: bindings `closest` may try over the whole programme
CLOSEST_BUDGET = 200_000
_TRANSPORT = ("move", "split", "merge", "shuttle", "hop", "junction_enter", "junction_exit")
_COMPARE = ("<", "<=", ">", ">=", "=", "!=", "in", "notin")


class DebugError(ValueError):
    """Something the person asked for cannot be done, said in their words."""


class _OutOfCases(Exception):
    """A walk over a rule's cases used up its budget."""


# ------------------------------------------------------------------ inputs

def model_from(desc: Any = None):
    """A clock from what a page or a run says about it: a `CostModel`, a name (`corrected`,
    `deck`), or the replay's own description (`{"name": "corrected", "table": ...}`, which is
    what every Studio page carries as `D.model`)."""
    from ..cost.models import CostModel, corrected_model, deck_model
    if desc is None:
        return corrected_model()
    if isinstance(desc, CostModel):
        return desc
    if isinstance(desc, str):
        desc = {"name": desc}
    d = dict(desc)
    name = str(d.get("name") or "corrected")
    if name == "deck":
        return deck_model()
    if name != "corrected":
        raise DebugError(f"unknown clock {name!r}: a page's model is 'corrected' or 'deck'")
    kw = {k: d[k] for k in ("corner_hops", "length_scaling", "pitch", "shuttle_speed_m_s", "pitch_um",
                            "junction_min_degree", "include_anomalous") if d.get(k) is not None}
    return corrected_model(str(d.get("table") or "qccdsim_jones"),
                           objective=str(d.get("objective") or "fastest"), **kw)


def _doc(x: Any, what: str) -> dict:
    if isinstance(x, (str, bytes)):
        try:
            return json.loads(x)
        except json.JSONDecodeError as exc:
            raise DebugError(f"the {what} is not JSON: {exc}") from None
    if isinstance(x, Mapping):
        return dict(x)
    raise DebugError(f"the {what} must be JSON text or an object")


def open_documents(arch: Any, program: Any, model: Any = None, *, name: str | None = None,
                   certificate: Any = None) -> "Session":
    """A session from an `.arch.json` and a `.tsir.json`, as text or parsed (what a Studio
    page's Download -> 'device and programme' gives, and what the compiler writes).  With the
    compiler's certificate (`.qcert.json`) the circuit it compiled comes along, so the rules
    about the circuit (R24, R25, R26) are judged instead of not applying."""
    from ..arch.device import Architecture
    from ..ir.tsir import TSIR
    a, p = _doc(arch, "architecture"), _doc(program, "programme")
    try:
        A = Architecture.from_json(a)
    except Exception as exc:                       # the document's own refusal, in its words
        raise DebugError(f"that architecture document cannot be read: {exc}") from None
    try:
        P = TSIR.from_json(p)
    except Exception as exc:
        raise DebugError(f"that programme cannot be read: {exc}") from None
    circuit = None
    if certificate is not None:
        from ..ruleset.compile import circuit_of_certificate
        try:
            circuit = circuit_of_certificate(_doc(certificate, "certificate"))
        except (KeyError, TypeError, ValueError) as exc:
            raise DebugError(f"that certificate cannot be read: {exc}") from None
    return Session(A, P, model, name=name, circuit=circuit)


def open_files(arch: str | Path, program: str | Path, model: Any = None,
               certificate: str | Path | None = None) -> "Session":
    return open_documents(Path(arch).read_text(encoding="utf-8"),
                          Path(program).read_text(encoding="utf-8"), model,
                          name=Path(program).name,
                          certificate=Path(certificate).read_text(encoding="utf-8") if certificate else None)


def assertion_rules(text: str, prefix: str = "A") -> str:
    """Your own assertions as rule-language text.

    Accepts a rule file (`rule A1 "..." ...`), or bare formulas one per paragraph (blank
    line between them), each optionally led by `assert "<title>":` on its own line.  Bare
    formulas are numbered A1, A2, ...  The result parses with `qccd.ruleset.syntax.parse`."""
    text = (text or "").replace("\r\n", "\n").strip("\n")
    if not text.strip():
        return ""
    if re.search(r"^\s*rule\s", text, re.M):
        return text + "\n"
    out, n = [], 0
    for para in re.split(r"\n\s*\n", text):
        lines = [ln for ln in para.split("\n") if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            continue
        n += 1
        title = f"assertion {n}"
        m = re.match(r'^\s*assert(?:\s+"((?:[^"\\]|\\.)*)")?\s*:?\s*$', lines[0])
        if m:
            title = m.group(1) or title
            lines = lines[1:]
        if not lines:
            continue
        body = "\n".join("  " + ln.strip() for ln in lines)
        out.append(f'rule {prefix}{n} "{title}"\n{body}\n')
    return "\n".join(out)


# ------------------------------------------------------------------ results

@dataclass
class Failure:
    """One counterexample of one rule, located (steps numbered from 1)."""

    rule: str
    step: int | None            # None: about the device itself, so at every step
    until: int | None           # the last step it still fails at (a state that stays bad)
    phase: str | None           # start | during | after | at
    instant: str | None         # the moment, in the page's step numbers
    where: list[dict]           # the places it names: site, junction, rail, ion, loop
    message: str                # the grader's own sentence, renumbered
    values: dict[str, str] = field(default_factory=dict)
    gauge: dict | None = None   # the number the rule compares with its limit, when it compares one

    def to_json(self) -> dict:
        d = {"rule": self.rule, "step": self.step, "until": self.until, "phase": self.phase,
             "instant": self.instant, "where": self.where, "message": self.message}
        if self.values:
            d["values"] = self.values
        if self.gauge:
            d["gauge"] = self.gauge
        return d


def _instr_text(ins) -> str:
    """One instruction in a line: what it does, in the words the Studio's listing uses."""
    t = ins.type
    if t == "init":
        pl = list((ins.placement or {}).items())
        s = ", ".join(f"{i}@{n}" for i, n in pl[:6])
        return f"init {len(pl)} ions" + (f": {s}" + (", ..." if len(pl) > 6 else "") if pl else "")
    if t == "simd":
        tpl = ins.template or {}
        head = f"{ins.cls or 'transport'}"
        if tpl.get("kind") == "loop_shift":
            return f"{head}: rotate loop {tpl.get('loop')} by {tpl.get('delta')}"
        if tpl.get("kind"):
            return f"{head}: {tpl.get('kind')}"
        mv = [f"{p.ion} {p.src}→{p.dst}" for p in ins.participants]
        return f"{head}: " + ", ".join(mv[:6]) + (", ..." if len(mv) > 6 else "")
    if t == "gate":
        g = ins.gate or "gate"
        ops = [",".join(p) for p in ins.pairs] or list(ins.ions)
        return f"{g} " + " ".join(ops[:8]) + (" ..." if len(ops) > 8 else "")
    if t in ("measure", "reset"):
        return f"{t} " + ",".join(ins.ions[:8]) + (" ..." if len(ins.ions) > 8 else "")
    if t == "cool":
        return "cool " + (",".join(ins.ions[:8]) if ins.ions else "all")
    return t


# ------------------------------------------------------------------ the session

class Session:
    """One programme on one device under one clock, replayed once.  Public step numbers
    count from 1 (the Studio's "Step k / N"); `_p` names an internal 0-based position."""

    def __init__(self, arch, prog, model: Any = None, *, name: str | None = None, circuit=None):
        self.arch = arch
        self.prog = prog
        self.model = model_from(model)
        self.name = name or getattr(prog, "name", "") or "programme"
        self.views: list = []
        self.exe = programme_execution(prog, arch, self.model, views=self.views, circuit=circuit)
        hw = self.exe.hardware
        self._junctions = {str(s.id) for s in hw.junctions}
        self._sites = {str(s.id) for s in hw.sites}
        self._rail_ends = {str(r.id): (str(a.id), str(b.id)) for r, (a, b) in hw.ends.items()}
        ins = prog.instructions
        self.n = len(ins)
        pos_of_id: dict[int, int] = {}
        for k, i in enumerate(ins):
            pos_of_id.setdefault(i.id, k)
        #: the position of each of the replay's steps (one per non-init instruction)
        self.step_pos = [pos_of_id.get(s["instr"], 0) for s in self.exe.steps]
        first = self.step_pos[0] if self.step_pos else self.n
        start = max(0, first - 1) if self.n else 0
        #: each instant as (position, phase), and its name in the page's step numbers
        self.instant_at: list[tuple[int, str]] = []
        self.labels: list[str] = []
        for lbl in self.exe.instants:
            m = _INSTANT.match(lbl)
            if m:
                p = self.step_pos[int(m.group(2))]
                self.instant_at.append((p, m.group(1)))
                self.labels.append(f"{m.group(1)} step {p + 1}")
            else:
                self.instant_at.append((start, "start"))
                self.labels.append(lbl)
        #: the instant holding the state after each position
        self.after: list[int] = [0] * self.n
        last: dict[int, int] = {}
        for i, (p, ph) in enumerate(self.instant_at):
            if ph in ("start", "after"):
                last[p] = i
        cur = 0
        for p in range(self.n):
            cur = last.get(p, cur)
            self.after[p] = cur
        #: the replay steps of each position (usually one; none for an init)
        self.steps_of: dict[int, list[int]] = {}
        for k, p in enumerate(self.step_pos):
            self.steps_of.setdefault(p, []).append(k)
        self._ion_ids = {str(x.id) for x in self.exe.ions}
        self._ev: Evaluator | None = None
        self._at_cache: dict[tuple, tuple] = {}

    # ---------------------------------------------------------------- numbering

    def _in(self, k: Any) -> int:
        """A step number (from 1; -1 is the last) as a position."""
        try:
            k = int(k)
        except (TypeError, ValueError):
            raise DebugError(f"a step is a number from 1 to {self.n}") from None
        if self.n == 0:
            raise DebugError("this programme has no instructions")
        p = k + self.n if k < 0 else k - 1
        if not 0 <= p < self.n:
            raise DebugError(f"there is no step {k}: this programme has steps 1 to {self.n}")
        return p

    def renumber(self, text: str) -> str:
        """The grader's sentence with its replay-step numbers as the page's step numbers."""
        def sub(m: re.Match) -> str:
            k = int(m.group(1))
            return f"step {self.step_pos[k] + 1}" if 0 <= k < len(self.step_pos) else m.group(0)
        return _STEP_WORD.sub(sub, text)

    def _brief(self, ev: Evaluator, v: Any) -> str:
        if isinstance(v, Instant) and 0 <= v.id < len(self.labels):
            return self.labels[v.id]
        return self.renumber(_brief(ev, v))

    def _jsonable(self, v: Any, ev: Evaluator) -> Any:
        if v is None or isinstance(v, (bool, int, str)):
            return v
        if isinstance(v, float):
            return v if v == v and v not in (float("inf"), float("-inf")) else self._brief(ev, v)
        if isinstance(v, Entity) and not isinstance(v, (Instant, StepEntity)):
            return str(v.id)
        if isinstance(v, (list, tuple, set, frozenset)):
            items = list(v)
            if isinstance(v, (set, frozenset)):
                items.sort(key=lambda x: str(getattr(x, "id", x)))
            return [self._jsonable(x, ev) for x in items[:200]]
        return self._brief(ev, v)

    # ---------------------------------------------------------------- reading

    @property
    def ev(self) -> Evaluator:
        if self._ev is None:
            self._ev = Evaluator(self.exe, dict(catalogue().params))
        return self._ev

    def kind_of(self, nid: Any) -> str:
        nid = str(nid)
        if nid in self._junctions:
            return "junction"
        if nid in self._sites:
            return "site"
        if nid in self._rail_ends:
            return "rail"
        return "node"

    def summary(self) -> dict:
        hw = self.exe.hardware
        return {"name": self.name, "device": hw.name, "steps": self.n,
                "numbering": "steps count from 1, as the Studio's status line does (Step k / N)",
                "instants": len(self.exe.instants), "ions": [str(i.id) for i in self.exe.ions],
                "sites": len(hw.sites), "junctions": len(hw.junctions), "rails": len(hw.rails),
                "model": getattr(self.model, "name", "corrected"),
                "duration_us": round(self.exe.instant_time[-1], 6) if self.exe.instant_time else 0.0}

    def _ops(self, p: int) -> list:
        out = []
        for rs in self.steps_of.get(p, ()):
            out.extend(self.exe.ops_of(step=rs))
        return out

    def _op_json(self, o: Op) -> dict:
        d: dict[str, Any] = {"kind": o.kind, "ions": [str(i.id) for i in o.ions], "text": o.text or o.kind}
        if o.site is not None:
            d["site"] = str(o.site.id)
        if o.path:
            d["path"] = [str(s.id) for s in o.path]
        if o.rails:
            d["rails"] = [str(r.id) for r in o.rails]
        return d

    def step(self, k: Any) -> dict:
        """What step k is and what it did."""
        p = self._in(k)
        ins = self.prog.instructions[p]
        rec: dict[str, Any] = {"step": p + 1, "instruction": ins.id, "type": ins.type, "class": ins.cls,
                               "text": _instr_text(ins),
                               "ops": [self._op_json(o) for o in self._ops(p)]}
        rs = self.steps_of.get(p)
        if rs:
            rec["duration_us"] = round(float(sum(self.views[r].duration_us or 0.0 for r in rs)), 6)
            mv = [{"ion": m.ion, "from": m.src, "to": m.dst, "rail": m.seg.id}
                  for r in rs for m in self.views[r].moves]
            if mv:
                rec["moves"] = mv
        ts = [self.exe.instant_time[i] for i, (q, _) in enumerate(self.instant_at) if q == p]
        if ts:
            rec["ends_at_us"] = round(max(ts), 6)
        return rec

    def _positions(self, i: int) -> dict[str, str | None]:
        out = {}
        for ion in self.exe.ions:
            s = self.exe.position.at(ion, i)
            out[str(ion.id)] = str(s.id) if s is not None else None
        return out

    def _nbar(self, p: int) -> dict[str, float]:
        """The n-bar each ion carried into the last replayed step at or before position p."""
        for q in range(p, -1, -1):
            rs = self.steps_of.get(q)
            if rs:
                return {str(i): round(float(x), 6) for i, x in self.views[rs[-1]].quanta.items()}
        return {}

    def state(self, k: Any, *, watch: Iterable[str] | None = None) -> dict:
        """Where every ion is after step k, who is in every occupied place, and what changed.
        With `watch`, the same for just those ions, sites, junctions and rails."""
        p = self._in(k)
        i = self.after[p]
        pos = self._positions(i)
        prev = self._positions(self.after[p - 1]) if p > 0 else {}
        occ: dict[str, list[str]] = {}
        for ion, s in pos.items():
            if s is not None:
                occ.setdefault(s, []).append(ion)
        cap = {str(e.id): c for e, c in self.exe.hardware.capacity.items()}
        places = {s: {"kind": self.kind_of(s), "ions": sorted(v), "capacity": cap.get(s)}
                  for s, v in sorted(occ.items())}
        moved = sorted(x for x in pos if prev.get(x) != pos[x]) if p > 0 else []
        out: dict[str, Any] = {"step": p + 1, "instant": self.labels[i], "step_info": self.step(p + 1),
                               "positions": pos, "occupied": places, "moved": moved,
                               "nbar": self._nbar(p)}
        over = [s for s, x in places.items() if x["capacity"] is not None and len(x["ions"]) > x["capacity"]]
        if over:
            out["over_capacity"] = over
        if watch:
            out["watch"] = {str(w): self._look(str(w), p) for w in watch}
        return out

    def look(self, ent: str, k: Any) -> dict:
        """One ion, site, junction or rail after step k."""
        return self._look(str(ent), self._in(k))

    def _look(self, ent: str, p: int) -> dict:
        i = self.after[p]
        if ent in self._ion_ids:
            s = self.exe.position.at(Ion(ent), i)
            ops = [o for o in self._ops(p) if any(str(x.id) == ent for x in o.ions)]
            return {"kind": "ion", "at": str(s.id) if s is not None else None,
                    "at_kind": self.kind_of(s.id) if s is not None else None,
                    "nbar": self._nbar(p).get(ent), "this_step": [self.renumber(o.text or o.kind) for o in ops]}
        kind = self.kind_of(ent)
        if kind in ("site", "junction", "node"):
            here = sorted(x for x, s in self._positions(i).items() if s == ent)
            rec: dict[str, Any] = {"kind": kind, "ions": here,
                                   "capacity": self.exe.hardware.capacity.get(Site(ent))}
            during = [j for j, (q, ph) in enumerate(self.instant_at) if q == p and ph == "during"]
            if during:
                rec["during_step"] = max(self.exe.occupancy.at(Site(ent), j) for j in during)
            return rec
        if kind == "rail":
            hops = [h for rs in self.steps_of.get(p, ()) for h in self.exe.hops_of(rs) if str(h.rail.id) == ent]
            return {"kind": "rail", "ends": list(self._rail_ends[ent]),
                    "crossing": [f"{h.ion.id} {h.src.id}→{h.dst.id}" for h in hops],
                    "capacity": self.exe.hardware.capacity.get(Rail(ent))}
        raise DebugError(f"{ent!r} is not an ion, site, junction or rail of this programme")

    def diff(self, a: Any, b: Any) -> dict:
        pa_, pb_ = self._in(a), self._in(b)
        pa, pb = self._positions(self.after[pa_]), self._positions(self.after[pb_])
        moved = {i: [pa.get(i), pb.get(i)] for i in sorted(set(pa) | set(pb)) if pa.get(i) != pb.get(i)}
        return {"from": pa_ + 1, "to": pb_ + 1, "moved": moved}

    def trace(self, entity: str, start: Any = 1, end: Any = None, *, limit: int = 500) -> dict:
        """Everything that happened to one ion, site, junction or rail, from step `start` to
        `end`: only the steps where something did (and the first)."""
        ent = str(entity)
        p0 = self._in(start)
        p1 = self.n - 1 if end is None else self._in(end)
        if ent not in self._ion_ids and self.kind_of(ent) == "node" and ent not in {str(n.id) for n in self.exe.hardware.nodes}:
            raise DebugError(f"{ent!r} is not an ion, site, junction or rail of this programme")
        events, last = [], None
        for p in range(p0, p1 + 1):
            cur = self._look(ent, p)
            acted = bool(cur.get("this_step") or cur.get("crossing") or cur.get("during_step"))
            key = json.dumps({x: cur.get(x) for x in ("at", "ions")}, sort_keys=True)
            if p == p0 or acted or key != last:
                events.append({"step": p + 1, "instruction": _instr_text(self.prog.instructions[p]), **cur})
                if len(events) >= limit:
                    return {"entity": ent, "events": events, "truncated": True}
            last = key
        return {"entity": ent, "events": events, "truncated": False}

    # ---------------------------------------------------------------- expressions

    def entity(self, name: str):
        """The ion, site, junction or rail a quoted name stands for, or None: the grader's own
        reading of a quoted name (`Evaluator.named`)."""
        e = self.ev.named(str(name))
        return None if isinstance(e, str) else e

    def _formula(self, text: str, what: str):
        try:
            node = parse_formula(text)
        except RuleSyntaxError as exc:
            raise DebugError(f"that {what} does not parse: {exc}") from None
        _known_words(node, what)
        free = free_names(node) - set(self.ev._param_nodes) - set(TERMS) - {"t"}
        if free:
            raise DebugError(f"that {what} uses {', '.join(sorted(free))}, which is neither the instant t, "
                             "a parameter, nor a word of the vocabulary (the Rules page lists them)")
        return node

    def value(self, expr: str, step: Any, *, phase: str = "after") -> dict:
        """A term of the rule language at a step, with `t` the instant after it (or during it,
        for a step that moves ions, with phase='during')."""
        p = self._in(step)
        node = self._formula(expr, "expression")
        i = self.after[p]
        if phase == "during":
            i = next((j for j, (q, ph) in enumerate(self.instant_at) if q == p and ph == "during"), i)
        try:
            v = self.ev.ev(node, {"t": Instant(i)})
        except NotApplicable as exc:
            return {"expr": expr, "step": p + 1, "error": f"does not apply: {exc}"}
        except EvalError as exc:
            return {"expr": expr, "step": p + 1, "error": str(exc)}
        return {"expr": expr, "step": p + 1, "instant": self.labels[i],
                "value": self._jsonable(v, self.ev), "text": self._brief(self.ev, v)}

    def find(self, condition: str, start: Any = 1, *, backward: bool = False, inclusive: bool = False) -> dict:
        """The first instant after step `start` (before it, backward) at which a condition
        holds: a conditional breakpoint.  `condition` is a formula over the instant `t`."""
        node = self._formula(condition, "condition")
        p0 = self._in(start)
        order = range(len(self.instant_at) - 1, -1, -1) if backward else range(len(self.instant_at))
        for i in order:
            p, ph = self.instant_at[i]
            if not backward and (p < p0 or (p == p0 and not inclusive)):
                continue
            if backward and (p > p0 or (p == p0 and not inclusive)):
                continue
            try:
                if self.ev.ev(node, {"t": Instant(i)}):
                    return {"condition": condition, "found": True, "step": p + 1, "phase": ph,
                            "instant": self.labels[i]}
            except (EvalError, NotApplicable) as exc:
                return {"condition": condition, "found": False, "error": str(exc)}
        return {"condition": condition, "found": False,
                "note": f"it holds at no instant {'before' if backward else 'after'} step {p0 + 1}"}

    # ---------------------------------------------------------------- rules

    def _rules(self, rules: Any, assertions: str | None, only: Iterable[str] | None = None
               ) -> tuple[RuleSet | None, Evaluator, list[Rule]]:
        """(the rule set, an evaluator with its parameters, the rules to judge): a rule set the
        website ships by name (`standard`, `cyclone`, ...), the text of one, or None; and your
        own assertions (`assertion_rules`)."""
        rs: RuleSet | None = None
        if rules:
            spec = rules if isinstance(rules, RuleSet) else str(rules)
            if isinstance(spec, str) and "\n" not in spec and re.search(r"\s", spec.strip()):
                spec += "\n"                      # one rule written on one line is text, not a name
            try:
                rs = spec if isinstance(spec, RuleSet) else load_rules(spec)
            except (RuleSyntaxError, KeyError) as exc:
                raise DebugError(f"those rules cannot be read: {exc}") from None
        params = dict(catalogue().params)
        if rs is not None:
            params.update(rs.params)
        ev = Evaluator(self.exe, params)
        want = set(only) if only else None
        todo: list[Rule] = [r for r in (rs.rules if rs else []) if want is None or r.id in want]
        own = assertion_rules(assertions or "")
        if own:
            try:
                src = parse(own)
            except RuleSyntaxError as exc:
                raise DebugError(f"your assertions do not parse: {exc}") from None
            for r in src.rules:
                r.origin = "yours"
                for f in (r.formula, r.when):
                    if f is not None:
                        _known_words(f, f"assertion {r.id}")
                bad = sorted({n for f in (r.formula, r.when) if f is not None
                              for n in free_names(f) if n not in params and n not in TERMS})
                if bad:
                    raise DebugError(f"assertion {r.id} uses {', '.join(bad)}, which is neither a variable it "
                                     "binds, a parameter, nor a word of the vocabulary")
                if want is None or r.id in want:
                    todo.append(r)
        return rs, ev, todo

    def _rule(self, rule: str, rules: Any, assertions: str | None) -> tuple[Evaluator, Rule]:
        rs, ev, todo = self._rules(rules, assertions, only=[str(rule)])
        if not todo:
            where = f"the rule set {rs.name!r}" if rs is not None else "your assertions"
            raise DebugError(f"{rule!r} is not a rule of {where}")
        return ev, todo[0]

    def check(self, rules: Any = "standard", assertions: str | None = None, *,
              only: Iterable[str] | None = None, limit: int = FAILURES_PER_RULE) -> dict:
        """Judge the programme by a rule set and your own assertions, and locate every failure.

        `rules`: a rule set the website ships, by name (`standard`, `cyclone`, ...), the text of
        a rule set, or None for none.  `assertions`: your own rules (`assertion_rules`)."""
        out: dict[str, Any] = {"programme": self.name, "steps": self.n, "rules": []}
        rs, ev, todo = self._rules(rules, assertions, only)
        if rs is not None:
            out["ruleset"] = rs.name
            out["title"] = rs.title
        for r in todo:
            out["rules"].append(self._judge(ev, r, limit))
        out["ok"] = not any(x["verdict"] in (FAILS, UNCHECKED) for x in out["rules"])
        out["failing"] = [x["id"] for x in out["rules"] if x["verdict"] == FAILS]
        return out

    # ---------------------------------------------------------------- one step, case by case
    #
    # `check` walks only the cases that can fail (the evaluator skips the rest), which is what a
    # verdict needs.  To show a rule at work at one step -- every case it judges there, each with
    # the values it compares -- these walk every binding, with the variables that range over
    # time (instants, operations, steps) held to that step.

    def rules_at(self, step: Any, rules: Any = "standard", assertions: str | None = None, *,
                 budget: int = CASES_PER_STEP) -> dict:
        """Every rule at step k: how many cases it judges there and how many of them fail.  A rule
        whose cases name no moment (a corner of the device, a rail) judges them at every step."""
        p = self._in(step)
        rs, ev, todo = self._rules(rules, assertions)
        out = {"step": p + 1, "instruction": _instr_text(self.prog.instructions[p]), "rules": []}
        if rs is not None:
            out["ruleset"] = rs.name
        for r in todo:
            rec: dict[str, Any] = {"id": r.id, "title": r.title}
            try:
                if r.when is not None and not ev.ev(r.when, {}):
                    rec["here"] = NOT_APPLICABLE
                    out["rules"].append(rec)
                    continue
                n = bad = 0
                timed = False
                left = [budget]
                for e, body in self._cases(ev, r.formula, {}, p, left):
                    n += 1
                    timed = timed or any(self._pos(v) is not None for v in e.values())
                    if not ev.ev(body, e):
                        bad += 1
                rec.update(cases=n, failing=bad, every_step=bool(n) and not timed,
                           here=FAILS if bad else (HOLDS if n else "nothing to judge"))
            except _OutOfCases:
                rec.update(here="too many cases", note=f"more than {budget} cases at this step")
            except NotApplicable as exc:
                rec.update(here=NOT_APPLICABLE, note=self.renumber(str(exc)))
            except EvalError as exc:
                rec.update(here=UNCHECKED, note=self.renumber(str(exc)))
            out["rules"].append(rec)
        return out

    def inspect(self, rule: str, step: Any, rules: Any = "standard", assertions: str | None = None, *,
                limit: int = 40, budget: int = CASES_PER_STEP) -> dict:
        """One rule at step k: every case it judges there (failing first, then the closest to
        failing), each with its variables, the places it names and the value of every part of
        the rule's body -- the comparisons with both sides and how much room is left."""
        p = self._in(step)
        ev, r = self._rule(rule, rules, assertions)
        out: dict[str, Any] = {"rule": r.id, "title": r.title, "text": r.text or show(r.formula),
                               "step": p + 1, "instruction": _instr_text(self.prog.instructions[p])}
        if r.when is not None:
            out["when"] = r.when_text or show(r.when)
            if not ev.ev(r.when, {}):
                out.update(here=NOT_APPLICABLE, cases=0, shown=[])
                return out
        cases: list[dict] = []
        n = bad = 0
        try:
            for e, body in self._cases(ev, r.formula, {}, p, [budget]):
                n += 1
                holds = bool(ev.ev(body, e))
                bad += not holds
                cases.append({"env": dict(e), "body": body, "holds": holds,
                              "slack": None if not holds else self._slack(ev, body, e)})
        except _OutOfCases:
            out["truncated"] = f"more than {budget} cases at this step: the first {budget} are shown"
        except NotApplicable as exc:
            out.update(here=NOT_APPLICABLE, note=self.renumber(str(exc)), cases=0, shown=[])
            return out
        except EvalError as exc:
            out.update(here=UNCHECKED, note=self.renumber(str(exc)), cases=0, shown=[])
            return out
        cases.sort(key=lambda c: (c["holds"], c["slack"] if c["slack"] is not None else float("inf")))
        out.update(cases=n, failing=bad, here=FAILS if bad else (HOLDS if n else "nothing to judge"),
                   shown=[self._case(ev, c["env"], c["body"], c["holds"]) for c in cases[:limit]])
        out["every_step"] = bool(n) and not any(self._pos(v) is not None for c in cases for v in c["env"].values())
        if not n:
            out["note"] = "this rule judges nothing at this step"
        return out

    def closest(self, rule: str, rules: Any = "standard", assertions: str | None = None, *,
                budget: int = CLOSEST_BUDGET) -> dict:
        """Of every case a rule judges, the one that holds with the least room: the hottest gate
        under the heat budget, the fullest site under its capacity.  Only the rule's numeric
        comparisons have a room; the search stops after `budget` cases and says so."""
        ev, r = self._rule(rule, rules, assertions)
        out: dict[str, Any] = {"rule": r.id, "title": r.title}
        if r.when is not None and not ev.ev(r.when, {}):
            out.update(found=False, note=f"it applies only when {r.when_text or show(r.when)}")
            return out
        best = None
        n = bad = 0
        try:
            for e, body in self._cases(ev, r.formula, {}, None, [budget]):
                n += 1
                if not ev.ev(body, e):
                    bad += 1
                    continue
                s = self._slack(ev, body, e)
                if s is not None and (best is None or s < best[0]):
                    best = (s, dict(e), body)
            out["complete"] = True
        except _OutOfCases:
            out["complete"] = False
        except (NotApplicable, EvalError) as exc:
            out.update(found=False, note=self.renumber(str(exc)))
            return out
        out.update(searched=n, failing=bad)
        if not out["complete"]:
            out["note"] = f"searched the first {n} cases of a rule with more"
        if best is None:
            out.update(found=False, note=out.get("note") or "no case compares a number with a limit")
            return out
        out.update(found=True, room=_num(best[0]), case=self._case(ev, best[1], best[2], True))
        return out

    def _pos(self, v: Any) -> int | None:
        """The position (0-based) a value belongs to, or None for one that names no moment."""
        if isinstance(v, Instant):
            return self.instant_at[v.id][0] if 0 <= v.id < len(self.instant_at) else None
        o = v.op if isinstance(v, Hop) else v
        if isinstance(o, Op):
            return self.step_pos[o.step] if o.step is not None and 0 <= o.step < len(self.step_pos) else None
        if isinstance(v, StepEntity):
            return self.step_pos[v.id] if 0 <= v.id < len(self.step_pos) else None
        return None

    def _cases(self, ev: Evaluator, n, env: dict, p: int | None, left: list) -> Iterator[tuple[dict, Any]]:
        """Every (binding, body) a formula judges -- through its `for all`s, its `and`s and an
        `implies` whose condition holds -- with the time-like variables held to position p
        (every position, for None).  `left` counts down the bindings it may still try."""
        if isinstance(n, BinOp) and n.op == "and":
            yield from self._cases(ev, n.left, env, p, left)
            yield from self._cases(ev, n.right, env, p, left)
        elif isinstance(n, BinOp) and n.op == "implies" and _has_quant(n.right):
            if ev.ev(n.left, env):
                yield from self._cases(ev, n.right, env, p, left)
        elif isinstance(n, Quant) and n.kind == "all":
            for e in self._bindings(ev, n, env, p, left):
                if _has_quant(n.body) and not (isinstance(n.body, BinOp) and n.body.op == "or"):
                    yield from self._cases(ev, n.body, e, p, left)
                else:
                    yield e, n.body
        else:
            yield dict(env), n

    def _bindings(self, ev: Evaluator, q: Quant, env: dict, p: int | None, left: list) -> Iterator[dict]:
        """`Evaluator.bindings` without its pruning (a case that holds is wanted too), and with
        every time-like value outside position p left out."""
        vars_ = [(nm, b.domain) for b in q.binders for nm in b.names]
        names = [v for v, _ in vars_]
        nameset = set(names)
        at: dict[int, list] = {}
        for c in _split_and(q.where):
            used = free_names(c) & nameset
            if not used:
                if not ev.ev(c, env):
                    return
            else:
                at.setdefault(max(names.index(u) for u in used), []).append(c)
        e = dict(env)

        def walk(i: int) -> Iterator[dict]:
            if i == len(vars_):
                yield dict(e)
                return
            name, dom = vars_[i]
            for v in self._domain_at(ev, dom, e, nameset, p):
                left[0] -= 1
                if left[0] < 0:
                    raise _OutOfCases
                e[name] = v
                if all(ev.ev(c, e) for c in at.get(i, ())):
                    yield from walk(i + 1)
            e.pop(name, None)

        yield from walk(0)

    def _domain_at(self, ev: Evaluator, dom, env: dict, bound: set, p: int | None) -> list:
        full = ev.domain(dom, env, bound)
        if p is None:
            return full
        if free_names(dom) & (bound | set(env)):
            return [v for v in full if self._pos(v) in (None, p)]
        key = (id(dom), p)
        hit = self._at_cache.get(key)
        if hit is None or hit[0] is not full:
            hit = (full, [v for v in full if self._pos(v) in (None, p)])
            self._at_cache[key] = hit
        return hit[1]

    def _slack(self, ev: Evaluator, n, env: dict) -> float | None:
        """How much room a body that holds has: the least, over the numeric comparisons that
        decide it, of the distance to their limit.  None when no number decides it."""
        try:
            if isinstance(n, BinOp) and n.op == "and":
                xs = [x for x in (self._slack(ev, n.left, env), self._slack(ev, n.right, env)) if x is not None]
                return min(xs) if xs else None
            if isinstance(n, BinOp) and n.op == "or":
                xs = [self._slack(ev, s, env) for s in (n.left, n.right) if ev.ev(s, env)]
                xs = [x for x in xs if x is not None]
                return max(xs) if xs else None
            if isinstance(n, BinOp) and n.op == "implies":
                return self._slack(ev, n.right, env) if ev.ev(n.left, env) else None
            if isinstance(n, BinOp) and n.op in ("<", "<=", ">", ">="):
                a, b = ev.ev(n.left, env), ev.ev(n.right, env)
                if _numeric(a) and _numeric(b):
                    return float(b - a) if n.op in ("<", "<=") else float(a - b)
        except (EvalError, NotApplicable):
            return None
        return None

    def _case(self, ev: Evaluator, e: dict, body, holds: bool) -> dict:
        """One case as the page shows it: its variables, when and where, and the body's values."""
        p, phase, inst, _ = self._when(e)
        rec: dict[str, Any] = {"holds": holds, "values": {nm: self._brief(ev, v) for nm, v in e.items()},
                               "step": None if p is None else p + 1, "phase": phase,
                               "where": self._where(e, inst), "why": self._explain(ev, body, e)}
        g = _gauge(rec["why"])
        if g is not None:
            rec["gauge"] = g
        if not holds:
            rec["message"] = self.renumber(ev.why(body, e))
        return rec

    def _explain(self, ev: Evaluator, n, env: dict, depth: int = 0) -> dict:
        """The value of every part of a formula under one binding: a tree the page lays out as
        the rule's body with each part green or red."""
        rec: dict[str, Any] = {"text": show(n)}
        try:
            if isinstance(n, BinOp) and n.op in ("and", "or", "implies"):
                left = self._explain(ev, n.left, env, depth + 1)
                rec["op"] = n.op
                if n.op == "implies" and left.get("value") is False:
                    rec.update(value=True, parts=[left], vacuous=True)
                    return rec
                right = self._explain(ev, n.right, env, depth + 1)
                rec["parts"] = [left, right]
                rec["value"] = bool(ev.ev(n, env))
                return rec
            if isinstance(n, BinOp) and n.op in _COMPARE:
                a, b = ev.ev(n.left, env), ev.ev(n.right, env)
                v = bool(ev.binop(n.op, a, b, n))
                rec.update(op=n.op, value=v, left={"text": show(n.left), "value": self._brief(ev, a)},
                           right={"text": show(n.right), "value": self._brief(ev, b)})
                if n.op in ("<", "<=", ">", ">=") and _numeric(a) and _numeric(b):
                    rec["left"]["number"], rec["right"]["number"] = _num(a), _num(b)
                    rec["room"] = _num(b - a if n.op in ("<", "<=") else a - b)
                return rec
            if isinstance(n, Not):
                inner = self._explain(ev, n.arg, env, depth + 1)
                rec.update(op="not", parts=[inner], value=None if inner.get("value") is None else not inner["value"])
                return rec
            if isinstance(n, Quant):
                rec["value"] = bool(ev.ev(n, env))
                if n.kind == "all" and not rec["value"] and depth < 4:
                    first = next(iter(ev.counterexamples(n, env)), None)
                    if first is not None:
                        rec["fails_at"] = self.renumber(ev.describe(n, first))
                return rec
            v = ev.ev(n, env)
            rec["value"] = v if isinstance(v, bool) else self._brief(ev, v)
        except NotApplicable as exc:
            rec.update(value=None, error="does not apply: " + self.renumber(str(exc)))
        except EvalError as exc:
            rec.update(value=None, error=self.renumber(str(exc)))
        return rec

    def _judge(self, ev: Evaluator, rule: Rule, limit: int) -> dict:
        rec: dict[str, Any] = {"id": rule.id, "title": rule.title, "verdict": HOLDS, "core": rule.core,
                               "origin": rule.origin, "text": rule.text or show(rule.formula)}
        if rule.when is not None:
            rec["when"] = rule.when_text or show(rule.when)
        try:
            if rule.when is not None and not ev.ev(rule.when, {}):
                rec["verdict"] = NOT_APPLICABLE
                rec["reason"] = f"it applies only when {rule.when_text or show(rule.when)}"
                return rec
            count, fails = 0, []
            budget = [SPAN_BUDGET]
            for q, e in self._counterexamples(ev, rule.formula, {}):
                count += 1
                if len(fails) < limit:
                    fails.append(self._locate(ev, rule, q, e, budget))
            if count:
                rec["verdict"] = FAILS
                rec["count"] = count
                rec["failures"] = [f.to_json() for f in _merge(fails)]
                if count > len(fails):
                    rec["truncated"] = True
        except NotApplicable as exc:
            rec["verdict"] = NOT_APPLICABLE
            rec["reason"] = self.renumber(str(exc))
        except EvalError as exc:
            rec["verdict"] = UNCHECKED
            rec["reason"] = self.renumber(str(exc))
        return rec

    def _counterexamples(self, ev: Evaluator, n, env: dict) -> Iterator[tuple[Quant | None, dict]]:
        """Every (quantifier, binding) at which a formula fails: `Evaluator.explain`'s walk,
        yielding the bindings instead of counting them."""
        if isinstance(n, Quant) and n.kind == "all":
            for e in ev.counterexamples(n, env):
                yield n, e
            return
        if isinstance(n, BinOp) and n.op == "and":
            yield from self._counterexamples(ev, n.left, env)
            yield from self._counterexamples(ev, n.right, env)
            return
        if isinstance(n, BinOp) and n.op == "or":
            if ev.ev(n.left, env) or ev.ev(n.right, env):
                return
            side = n.right if _has_quant(n.right) or not _has_quant(n.left) else n.left
            yield from self._counterexamples(ev, side, env)
            return
        if isinstance(n, BinOp) and n.op == "implies":
            if not ev.ev(n.left, env):
                return
            yield from self._counterexamples(ev, n.right, env)
            return
        if not ev.ev(n, env):
            yield None, dict(env)

    def _locate(self, ev: Evaluator, rule: Rule, q: Quant | None, e: dict, budget: list) -> Failure:
        msg = ev.describe(q, e) if q is not None else ev.why(rule.formula, e)
        # a nested `for all` names its own culprits: take the first, for where it happened
        full = dict(e)
        if q is not None and isinstance(q.body, Quant) and q.body.kind == "all":
            try:
                inner = next(iter(ev.counterexamples(q.body, e)), None)
            except (EvalError, NotApplicable):
                inner = None
            if inner:
                full.update(inner)
        p, phase, inst, tvar = self._when(full)
        where = self._where(full, inst)
        if not where:
            # `occupancy("C0", t) <= 1` binds only t: the place is the one the formula names
            named = [e for e in (self.entity(x) for x in _names_in(rule.formula)) if e is not None]
            where = self._where({f"_{k}": e for k, e in enumerate(named)}, inst)
        last = p
        if tvar is not None and q is not None and inst is not None:
            last = self._span(ev, q, e, tvar, inst, budget)
        values = {nm: self._brief(ev, v) for nm, v in full.items()}
        body = rule.formula if q is None else q.body
        if q is not None and isinstance(q.body, Quant) and q.body.kind == "all" and len(full) > len(e):
            body = q.body.body                   # the nested culprit's own body
        return Failure(rule.id, None if p is None else p + 1, None if last is None else last + 1, phase,
                       self.labels[inst] if inst is not None and 0 <= inst < len(self.labels) else None,
                       where, self.renumber(msg), values, _gauge(self._explain(ev, body, full)))

    def _when(self, full: dict) -> tuple[int | None, str | None, int | None, str | None]:
        """(position, phase, instant, the variable holding it) a binding happens at: its instant,
        else its operation's step, else its step; all None for one that names no moment."""
        for nm, v in full.items():
            if isinstance(v, Instant) and 0 <= v.id < len(self.instant_at):
                p, phase = self.instant_at[v.id]
                return p, phase, v.id, nm
        for v in full.values():
            o = v.op if isinstance(v, Hop) else v
            if isinstance(o, Op) and o.step is not None and o.step < len(self.step_pos):
                return self.step_pos[o.step], ("during" if o.kind in _TRANSPORT else "at"), o.start, None
            if isinstance(v, StepEntity) and 0 <= v.id < len(self.step_pos):
                return self.step_pos[v.id], "at", None, None
        return None, None, None, None

    def _span(self, ev: Evaluator, q: Quant, e: dict, tvar: str, i0: int, budget: list) -> int:
        """The last position a failure at instant i0 still fails at.  The rule language reports
        a state that goes bad once, at the instant it goes bad; the page crosses it out for as
        long as it stays bad."""
        last = i0
        for j in range(i0 + 1, len(self.instant_at)):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            env = {**e, tvar: Instant(j)}
            try:
                if q.where is not None and not ev.ev(q.where, env):
                    break
                if ev.ev(q.body, env):
                    break
            except (EvalError, NotApplicable):
                break
            last = j
        return self.instant_at[last][0]

    def _where(self, full: dict, inst: int | None) -> list[dict]:
        out: list[dict] = []
        seen: set = set()

        def add(kind: str, ident: Any, **extra):
            key = (kind, str(ident))
            if key in seen:
                return
            seen.add(key)
            out.append({"kind": kind, "id": str(ident), **extra})

        def ion(i: Ion, at_inst: int | None):
            s = self.exe.position.at(i, at_inst) if at_inst is not None else None
            if s is not None:
                add("ion", i.id, at=str(s.id))
            else:
                add("ion", i.id)

        def rail(r: Rail):
            add("rail", r.id, ends=list(self._rail_ends.get(str(r.id), ())))

        for v in full.values():
            if isinstance(v, Site):
                add(self.kind_of(v.id), v.id)
            elif isinstance(v, Rail):
                rail(v)
            elif isinstance(v, Ion):
                ion(v, inst)
            elif isinstance(v, Hop):
                rail(v.rail)
                add(self.kind_of(v.dst.id), v.dst.id)
                ion(v.ion, v.op.start)
            elif isinstance(v, Op):
                if v.site is not None:
                    add(self.kind_of(v.site.id), v.site.id)
                for r in v.rails:
                    rail(r)
                if v.path:
                    add(self.kind_of(v.path[-1].id), v.path[-1].id)
                for i in v.ions:
                    ion(i, v.start)
            elif isinstance(v, Loop):
                add("loop", v.id, nodes=[str(n.id) for n in self.exe.hardware.loops.get(str(v.id), ())])
            elif isinstance(v, CircuitOp):
                c = self.exe.circuit[v.id] if self.exe.circuit and 0 <= v.id < len(self.exe.circuit) else None
                add("circuit", v.id, text=str(getattr(c, "text", "") or ""))
            elif isinstance(v, (list, tuple, set, frozenset)):
                for x in list(v)[:12]:
                    if isinstance(x, Site):
                        add(self.kind_of(x.id), x.id)
                    elif isinstance(x, Ion):
                        ion(x, inst)
                    elif isinstance(x, Rail):
                        rail(x)
        return out


def _known_words(n, what: str) -> None:
    """Refuse a word the vocabulary does not have, before anything is evaluated (as `load`
    refuses one in a rule set): evaluated, it would only come back as 'could not be checked'."""
    bad = sorted({x.fn for x in walk(n) if isinstance(x, Call) and x.fn not in TERMS and x.fn != "count"})
    if bad:
        raise DebugError(f"{what} uses {', '.join(w + '(...)' for w in bad)}, which is not a word of the "
                         "vocabulary (the Rules page lists every word)")


def _numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _num(v: Any) -> Any:
    """A number as the page shows it: six significant figures, an integer as an integer."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if isinstance(v, int) or float(v).is_integer() and abs(v) < 1e15:
        return int(v)
    return float(f"{v:.6g}")


def _gauge(tree: dict) -> dict | None:
    """The comparison that decides a case, for the page's gauge: the false one if the case
    fails, else the one with the least room.  None when no number is compared."""
    best = None

    def visit(t: dict) -> None:
        nonlocal best
        if "room" in t:
            key = (t.get("value") is not False, t["room"])
            if best is None or key < best[0]:
                best = (key, t)
        for c in t.get("parts", ()):
            visit(c)

    visit(tree)
    if best is None:
        return None
    t = best[1]
    return {"text": t["text"], "op": t["op"], "value": t["left"]["number"], "limit": t["right"]["number"],
            "left": t["left"]["text"], "right": t["right"]["text"], "holds": bool(t["value"]), "room": t["room"]}


def _names_in(n) -> list[str]:
    """The quoted names a formula gives to words (`occupancy("C0", t)`), in order."""
    return [a.value for x in walk(n) if isinstance(x, Call) for a in x.args if type(a) is Str]


def _merge(fails: Sequence[Failure]) -> list[Failure]:
    """Failures of one rule that are the same culprit at consecutive steps, as one span."""
    out: list[Failure] = []
    last: dict[str, Failure] = {}
    for f in fails:
        key = json.dumps([f.where, {k: v for k, v in f.values.items()
                                    if not v.startswith(("the start", "during step", "after step"))}],
                         sort_keys=True)
        prev = last.get(key)
        if (prev is not None and prev.step is not None and f.step is not None and prev.until is not None
                and f.step <= prev.until + 1):
            prev.until = max(prev.until, f.until if f.until is not None else f.step)
            continue
        out.append(f)
        last[key] = f
    return out
