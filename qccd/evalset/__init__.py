"""Evaluation as an input: metrics, requirements and noise written in a language.

An *evaluation* says how a design and its programme are judged.  It is a text file in the
language of `syntax.py` -- the rule language's terms and formulas, plus `metric`, `require`
and `channel` items -- over the words of `words.py`, and its meaning is `interp.py`:

    from qccd.evalset import load
    ev = load("goal1")                       # or a path, or the text of a .eval file
    run = ev.run(prog, arch, experiment=exp, cert=cert)
    ev.metrics(run)                          # every metric that can be computed, by name
    circuit = ev.circuit(run)                # the noisy stim circuit, line by line

Every number but the logical error rate is a function of three texts: the device, the
programme and the evaluation.  The logical error rate is a sample of the circuit those
three texts determine (`ev.sample`), reported with its 95 % interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .interp import Circuit, EvalError, Evaluator, emit_circuit
from .model import Run, build_run
from .syntax import (Channel, Define, EvalSyntaxError, EvaluationSource, Metric, Requirement,
                     parse, show)
from .words import GROUPS, WORDS, Absent, WordError

__all__ = ["Evaluation", "load", "names", "LIBRARY", "EvalSyntaxError", "EvalError", "Absent",
           "WORDS", "GROUPS", "Circuit", "Run", "show"]

LIBRARY = Path(__file__).parent / "library"


@dataclass
class Evaluation:
    """A resolved evaluation: every item of the file and of what it includes."""

    name: str
    title: str
    about: str
    clock: str
    rules: str
    rank: str
    params: dict = field(default_factory=dict)
    param_text: dict = field(default_factory=dict)
    defines: list[Define] = field(default_factory=list)
    metrics_: list[Metric] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    path: Path | None = None
    text_: str = ""

    def metric(self, name: str) -> Metric:
        for m in self.metrics_:
            if m.name == name:
                return m
        raise KeyError(f"evaluation {self.name!r} has no metric {name}")

    def channel(self, name: str) -> Channel:
        for c in self.channels:
            if c.name == name:
                return c
        raise KeyError(f"evaluation {self.name!r} has no channel {name}")

    # ------------------------------------------------------------------ running it
    def run(self, prog, arch, *, experiment=None, qasm: str | None = None, qubit_map=None,
            cert: Mapping | None = None, rules: bool = False, cost_model=None) -> Run:
        """Replay the programme on the device under this evaluation's clock (with `rules`, the
        verifier's rule verdicts from the same replay, in `run.facts["verification"]`).
        `cost_model` replaces the clock's own model -- a studio page priced by another model
        (`deck`) is evaluated on the replay it shows; the grader never passes one."""
        return build_run(prog, arch, self.clock or "qccdsim_jones", experiment=experiment,
                         qasm=qasm, qubit_map=qubit_map, cert=cert, rules=rules,
                         cost_model=cost_model)

    def evaluator(self, run: Run, sample=None, checks: Mapping | None = None) -> Evaluator:
        return Evaluator(self, run, sample=sample, checks=checks)

    def metrics(self, run: Run, sample=None, checks: Mapping | None = None,
                only: list[str] | None = None) -> dict[str, Any]:
        """Every metric's value; a metric that needs what the run does not have (an experiment,
        a sample) is left out, and `missing` (on the returned dict's `__missing__` key) says why."""
        ev = self.evaluator(run, sample, checks)
        out: dict[str, Any] = {}
        why: dict[str, str] = {}
        for m in self.metrics_:
            if only is not None and m.name not in only:
                continue
            try:
                out[m.name] = ev.value(m.name)
            except Absent as exc:
                why[m.name] = str(exc)
        if why:
            out["__missing__"] = why
        return out

    def requirements_check(self, run: Run, sample=None, checks: Mapping | None = None) -> list[dict]:
        """Every requirement: holds, fails (with the values that make it so) or could not be
        checked (with the missing thing)."""
        from ..ruleset.syntax import Quant

        ev = self.evaluator(run, sample, checks)
        out = []
        for r in self.requirements:
            row = {"id": r.id, "title": r.title, "text": r.text}
            try:
                ok = ev.truth(r.formula, {})
                row["verdict"] = "holds" if ok else "fails"
                if not ok and isinstance(r.formula, Quant) and r.formula.kind == "all":
                    row["examples"] = ev.counterexamples(r.formula, {})
            except Absent as exc:
                row["verdict"] = "could not be checked"
                row["reason"] = str(exc)
            out.append(row)
        return out

    def circuit(self, run: Run) -> Circuit:
        """The run's noisy circuit under this evaluation's channels."""
        return emit_circuit(self.evaluator(run), self.channels)

    def text(self) -> str:
        """The evaluation as written (its own file; includes are named, not inlined)."""
        return self.text_

    def with_params(self, values: Mapping[str, Any]) -> "Evaluation":
        """A copy with some parameters set to other values (a sweep, a model's knobs).  Only
        parameters the evaluation has may be set: a misspelt knob is refused, not ignored."""
        import copy
        from ..ruleset.syntax import Const, Num, Str
        unknown = sorted(set(values) - set(self.params))
        if unknown:
            raise KeyError(f"evaluation {self.name!r} has no parameter(s) {unknown}")
        out = copy.copy(self)
        out.params = dict(self.params)
        out.param_text = dict(self.param_text)
        for k, v in values.items():
            if isinstance(v, bool) or v is None:
                node, txt = Const(v), {True: "true", False: "false", None: "none"}[v]
            elif isinstance(v, (int, float)):
                node, txt = Num(v), repr(v)
            else:
                node, txt = Str(str(v)), '"' + str(v) + '"'
            out.params[k] = node
            out.param_text[k] = txt
        return out

    def with_clock(self, clock: str) -> "Evaluation":
        """The same evaluation on another operating-point table; a name the device format does
        not declare is refused (it would price every step on some other table, silently)."""
        import copy
        from ..arch.curves import TABLES
        if clock not in TABLES:
            raise ValueError(f"clock {clock!r} is not an operating-point table (one of {', '.join(TABLES)})")
        out = copy.copy(self)
        out.clock = clock
        return out

    def digest(self) -> str:
        """sha256 of what this evaluation is: every parameter, define, metric, requirement and
        channel as resolved (includes inlined), and its clock -- what a report names it by."""
        import hashlib
        parts = [f"clock {self.clock}", f"rules {self.rules}"]
        parts += [f"param {k} = {self.param_text.get(k, '')}" for k in sorted(self.params)]
        parts += [f"define {d.name}({','.join(d.args)}) = {d.text}" for d in self.defines]
        parts += [f"metric {m.name} [{m.clock}] = {m.text}" for m in self.metrics_]
        parts += [f"require {r.id} {r.text}" for r in self.requirements]
        parts += [f"channel {c.name} {c.text}" for c in self.channels]
        return "sha256:" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def noise_for_clock(clock: str) -> str:
    """The noise model a design priced on this operating-point table is judged under: Goal 1's
    cited physics (`quantinuum`) by qccd-noise@2, anything else by qccd-noise@1."""
    return "qccd-noise@2" if clock == "quantinuum" else "qccd-noise@1"


def for_page(clock: str) -> "Evaluation":
    """What a studio page evaluates: every board metric, the hardware budget, and the noise
    model of its clock, all on the page's own clock."""
    return load(f'evaluation "studio"\ntitle "What the studio reports"\ninclude "board"\n'
                f'include "{noise_for_clock(clock)}"\nclock "{clock}"\n')


def names() -> list[str]:
    """The evaluations the website ships, by name."""
    return sorted(p.stem for p in LIBRARY.glob("*.eval"))


def _source(spec: str) -> tuple[str, Path | None]:
    p = LIBRARY / f"{spec}.eval"
    if p.exists():
        return p.read_text(encoding="utf-8"), p
    q = Path(spec)
    if q.suffix == ".eval" and q.exists():
        return q.read_text(encoding="utf-8"), q
    if "\n" in spec or spec.lstrip().startswith("evaluation"):
        return spec, None
    raise FileNotFoundError(f"no evaluation {spec!r} (shipped: {', '.join(names())})")


def load(spec: str) -> Evaluation:
    """An evaluation by name (`goal1`, `metrics`, `qccd-noise@2`), path or text, with every
    `include` resolved.  An item a file defines replaces an included item of the same name;
    a clock or rule set it names replaces an included one."""
    text, path = _source(spec)
    src = parse(text)
    for d in src.defines:
        d.origin = src.name
    for m in src.metrics:
        m.origin = src.name
    for r in src.requirements:
        r.origin = src.name
    for c in src.channels:
        c.origin = src.name
    out = Evaluation(name=src.name, title=src.title, about=src.about, clock="", rules="", rank="",
                     includes=list(src.includes), path=path, text_=text)
    seen_clock: list[tuple[str, str]] = []
    for inc in src.includes:
        sub = load(inc)
        out.params.update(sub.params)
        out.param_text.update(sub.param_text)
        _merge(out.defines, sub.defines, lambda x: x.name)
        _merge(out.metrics_, sub.metrics_, lambda x: x.name)
        _merge(out.requirements, sub.requirements, lambda x: x.id)
        _merge(out.channels, sub.channels, lambda x: x.name)
        if sub.clock:
            seen_clock.append((inc, sub.clock))
        out.rules = out.rules or sub.rules
        out.rank = out.rank or sub.rank
    out.params.update(src.params)
    out.param_text.update(src.param_text)
    _merge(out.defines, src.defines, lambda x: x.name)
    _merge(out.metrics_, src.metrics, lambda x: x.name)
    _merge(out.requirements, src.requirements, lambda x: x.id)
    _merge(out.channels, src.channels, lambda x: x.name)
    if src.clock:
        out.clock = src.clock
    else:
        clocks = {c for _, c in seen_clock}
        if len(clocks) > 1:
            raise EvalSyntaxError(f"evaluation {src.name!r} includes files with different clocks "
                                  f"({', '.join(f'{i}: {c}' for i, c in seen_clock)}): name one")
        out.clock = next(iter(clocks), "")
    out.rules = src.rules or out.rules
    out.rank = src.rank or out.rank
    if out.rank and out.rank not in {m.name for m in out.metrics_}:
        raise EvalSyntaxError(f"evaluation {src.name!r} ranks by {out.rank}, which is not a metric")
    return out


def _merge(into: list, new: list, key) -> None:
    pos = {key(x): i for i, x in enumerate(into)}
    for x in new:
        if key(x) in pos:
            into[pos[key(x)]] = x
        else:
            pos[key(x)] = len(into)
            into.append(x)

