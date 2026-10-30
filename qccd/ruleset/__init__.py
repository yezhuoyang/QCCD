"""Rules as an input: rule sets written in an assertion language over hardware and execution.

A *rule* is one assertion -- `for all s in sites, t in instants: occupancy(s, t) <=
capacity(s)` -- in the language of `syntax.py`, over the words of `vocabulary.py`.  A *rule
set* picks the rules a machine (or a paper) assumes and sets their parameters.  The same
rule set is what the compiler is given and what its output is checked against; every
paper on the Reproduce pages is judged by its own.

    from qccd.ruleset import load, check_programme, check_schedule
    rs = load("standard")                  # or a path, or the text of a .rules file
    report = check_programme(rs, prog, arch, model)
    report.summary()

The rules marked `core` in the catalogue hold in every rule set and cannot be dropped:
every operation finds its ions where it says (R0), a site holds at most its capacity (R1),
an ion does one thing at a time (R8), and every two-qubit gate of the circuit is performed
(R24).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .evaluate import FAILS, HOLDS, NOT_APPLICABLE, UNCHECKED, Evaluator, Report, RuleResult
from .model import Execution
from .syntax import Node, Rule, RuleSetSource, RuleSyntaxError, parse, show
from .vocabulary import TERMS
from . import machine as _machine  # noqa: F401  (registers the machine-level words)

__all__ = [
    "RuleSet", "RuleSyntaxError", "load", "names", "catalogue", "check", "check_programme",
    "check_schedule", "Report", "RuleResult", "HOLDS", "FAILS", "NOT_APPLICABLE", "UNCHECKED",
    "TERMS", "rule_order", "keeping_order", "problems",
]

LIBRARY = Path(__file__).parent / "library"


def rule_order(rid: str) -> tuple:
    """R1 < R4 < R4b < R4d < R7 < R7b < R10 ...: the number first, then the letter."""
    m = re.match(r"([A-Za-z]*)(\d*)(.*)", rid)
    return (m.group(1), int(m.group(2) or 0), m.group(3)) if m else (rid, 0, "")


@dataclass
class RuleSet:
    """A resolved rule set: its rules in order, its parameters, and its own text."""

    name: str
    title: str
    about: str
    rules: list[Rule]
    params: dict[str, Node] = field(default_factory=dict)
    param_text: dict[str, str] = field(default_factory=dict)
    path: Path | None = None
    level: str = "device"

    def rule(self, rid: str) -> Rule:
        for r in self.rules:
            if r.id == rid:
                return r
        raise KeyError(f"rule set {self.name!r} has no rule {rid}")

    @property
    def ids(self) -> list[str]:
        return [r.id for r in self.rules]

    def text(self) -> str:
        """The rule set as one self-contained file: every rule written out, every parameter
        set.  This is what the compiler is given, and what a page offers for download."""
        out = [f'ruleset "{self.name}"']
        if self.title:
            out.append(f'title "{_esc(self.title)}"')
        if self.about:
            out.append(f'about "{_esc(self.about)}"')
        out.append("")
        for k, v in self.param_text.items():
            out.append(f"param {k} = {v}")
        for r in self.rules:
            out.append("")
            out.append(f'rule {r.id} "{_esc(r.title)}"' + (" core" if r.core else ""))
            if r.about:
                out.append(f'  about "{_esc(r.about)}"')
            if r.source:
                out.append(f'  source "{_esc(r.source)}"')
            if r.when is not None:
                out.append(f"  when {r.when_text or show(r.when)}:")
            body = r.text or show(r.formula)
            out.extend("  " + ln for ln in body.split("\n"))
        return "\n".join(out) + "\n"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


_CATALOGUE: RuleSetSource | None = None


def catalogue() -> RuleSetSource:
    """Every rule the website knows, parsed once."""
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = parse((LIBRARY / "catalogue.rules").read_text(encoding="utf-8"))
        for r in _CATALOGUE.rules:
            r.origin = "catalogue"
    return _CATALOGUE


def names(level: str = "device") -> list[str]:
    """The rule sets the website ships, by name: those about one device (the default), or
    with `level="machine"` those about a floorplan of places and a program scheduled on it."""
    out = []
    for p in LIBRARY.glob("*.rules"):
        if p.stem == "catalogue":
            continue
        head = re.search(r'^level\s+"(\w+)"', p.read_text(encoding="utf-8"), re.M)
        if (head.group(1) if head else "device") == level:
            out.append(p.stem)
    return sorted(out)


def _source(spec: str | Path) -> tuple[RuleSetSource, Path | None]:
    if isinstance(spec, Path) or (isinstance(spec, str) and "\n" not in spec and
                                  (spec.endswith(".rules") or "/" in spec or "\\" in spec)):
        p = Path(spec)
        return parse(p.read_text(encoding="utf-8")), p
    if isinstance(spec, str) and "\n" in spec:
        return parse(spec), None
    p = LIBRARY / f"{spec}.rules"
    if not p.exists():
        raise KeyError(f"no rule set named {spec!r}; the shipped ones are {', '.join(names())}")
    return parse(p.read_text(encoding="utf-8")), p


def load(spec: str | Path, _seen: frozenset = frozenset()) -> RuleSet:
    """A rule set by name (a shipped one), by path, or from its text."""
    src, path = _source(spec)
    if src.name in _seen:
        raise RuleSyntaxError(f"rule set {src.name!r} includes itself")
    cat = catalogue()
    device = src.level == "device"
    # the catalogue is the device level's: a machine-level rule set brings its own rules
    known: dict[str, Rule] = {r.id: r for r in cat.rules} if device else {}
    params: dict[str, Node] = dict(cat.params) if device else {}
    ptext: dict[str, str] = dict(cat.param_text) if device else {}
    chosen: dict[str, Rule] = {}
    inherited: set[str] = set()      # parameters an included rule set travels with
    for inc in src.includes:
        sub = load(inc, _seen | {src.name})
        inherited |= set(sub.params)
        for r in sub.rules:
            chosen[r.id] = r
            known.setdefault(r.id, r)
        params.update(sub.params)
        ptext.update(sub.param_text)
    for r in src.rules:
        r.origin = src.name or "this rule set"
        known[r.id] = r
    for rid in src.uses:
        if rid not in known:
            raise RuleSyntaxError(f"rule set {src.name!r} uses {rid}, which no rule set defines")
        chosen[rid] = known[rid]
    for r in src.rules:
        chosen[r.id] = r
    for rid in src.drops:
        k = known.get(rid)
        if k is not None and k.core:
            raise RuleSyntaxError(f"rule {rid} is a core rule and holds in every rule set; it "
                                  "cannot be dropped")
        chosen.pop(rid, None)
    for r in (cat.rules if device else src.rules):
        if r.core:
            chosen.setdefault(r.id, r)
    params.update(src.params)
    ptext.update(src.param_text)
    # only the parameters a chosen rule (or another parameter) reads travel with the set
    from .syntax import free_names
    need: set[str] = set()
    for r in chosen.values():
        need |= free_names(r.formula)
        if r.when is not None:
            need |= free_names(r.when)
    for k in list(params):
        if k in src.params or k in inherited:
            need.add(k)
    changed = True
    while changed:
        changed = False
        for k in list(need):
            if k in params:
                more = free_names(params[k]) - need
                if more:
                    need |= more
                    changed = True
    params = {k: v for k, v in params.items() if k in need}
    ptext = {k: v for k, v in ptext.items() if k in params}
    rules = sorted(chosen.values(), key=lambda r: rule_order(r.id))
    rs = RuleSet(name=src.name or (path.stem if path else "rules"), title=src.title,
                 about=src.about, rules=rules, params=params, param_text=ptext, path=path,
                 level=src.level)
    bad = problems(rs)
    if bad:
        raise RuleSyntaxError("; ".join(bad))
    return rs


def problems(rs: RuleSet) -> list[str]:
    """Words a rule uses that the vocabulary does not have, caught when the rule set is
    read rather than when a rule is first evaluated."""
    from .syntax import Call, Gen, Name, Quant, walk
    out: list[str] = []
    for r in rs.rules:
        for f in (r.formula, r.when):
            if f is None:
                continue
            for n in walk(f):
                if isinstance(n, Call) and n.fn not in TERMS and n.fn != "count":
                    out.append(f"rule {r.id} uses {n.fn}(...), which is not a word of the "
                               f"vocabulary")
            for nm in sorted(_free(f)):
                if nm not in rs.params and nm not in TERMS:
                    out.append(f"rule {r.id} uses {nm!r}, which is neither a variable it binds, "
                               "a parameter of the rule set, nor a word of the vocabulary")
    # a rule that cannot apply (R7b reads words no device has yet) is still well formed when
    # its `when` keeps it from ever being evaluated
    return [p for p in out if not _guarded(rs, p)]


def _free(f) -> set[str]:
    from .syntax import free_names
    return free_names(f)


def _guarded(rs: RuleSet, problem: str) -> bool:
    rid = problem.split()[1]
    try:
        r = rs.rule(rid)
    except KeyError:
        return False
    return r.when is not None and "declares_" in (r.when_text or "")


def keeping_order(rs: RuleSet) -> RuleSet:
    """The same rule set with each qubit's gates in the circuit's exact order (R25 in place of
    R26, and of no order rule at all): what this website's compilers are held to under any
    paper's rules, since they may not reorder a circuit even where its gates commute."""
    cat = {r.id: r for r in catalogue().rules}
    rules = [r for r in rs.rules if r.id != "R26"]
    if "R25" not in [r.id for r in rules]:
        rules.append(cat["R25"])
    rules.sort(key=lambda r: rule_order(r.id))
    return RuleSet(name=rs.name, title=rs.title, about=rs.about, rules=rules,
                   params=dict(rs.params), param_text=dict(rs.param_text), path=rs.path)


def check(ruleset: RuleSet, exe: Execution, *, only: Iterable[str] | None = None) -> Report:
    """Judge one execution by every rule of a rule set."""
    ev = Evaluator(exe, ruleset.params)
    want = set(only) if only is not None else None
    results = [ev.run(r) for r in ruleset.rules if want is None or r.id in want]
    return Report(ruleset=ruleset.name, title=ruleset.title, execution=exe.name, kind=exe.kind,
                  results=results, assumed=list(exe.facts.get("assumed", ())))


def check_programme(ruleset: RuleSet | str, prog, arch, model, **kw) -> Report:
    """Replay a programme on its device and judge it by a rule set."""
    from .programme import programme_execution
    rs = load(ruleset) if not isinstance(ruleset, RuleSet) else ruleset
    exe = programme_execution(prog, arch, model, **kw)
    return check(rs, exe)


def check_schedule(ruleset: RuleSet | str, sched, **kw) -> Report:
    """Replay a paper's timed schedule and judge it by a rule set.  The rule set's
    `chain_order` parameter says how the chain is tracked through a reorder."""
    from .schedule import schedule_execution
    rs = load(ruleset) if not isinstance(ruleset, RuleSet) else ruleset
    chain = None
    if "chain_order" in rs.params:
        node = rs.params["chain_order"]
        from .syntax import Str as _Str, Const as _Const
        chain = node.value if isinstance(node, (_Str, _Const)) else None
    exe = schedule_execution(sched, chain_order=kw.pop("chain_order", chain), **kw)
    return check(rs, exe)
