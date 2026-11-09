"""A rule set an entry declares: the rules a leaderboard entry says it keeps.

A design entered on a board is judged by the board's rule set (`standard`) unless its bundle
says otherwise.  It may (the user's decision of 2026-10-29): a bundle can carry its own rule
set -- a shipped one, or one written for the design, say the board's minus the rules that
assume a flat surface trap -- and the grader judges it by that.  The leaderboard still knows
which of the board's rules each entry keeps, and ranks by the board's rules unless a reader
asks for every entry.

The text comes from whoever made the bundle, so it is read here and nowhere else:

* it is the rule language and nothing more (`syntax.parse`), at most `MAX_TEXT` bytes and
  `MAX_RULES` rules, about one machine (`level "device"`);
* `include` names a rule set this website ships -- never a path, which `load` would read
  from the grader's disk;
* the core rules (R0 R1 R8 R24) hold in every set, as the catalogue writes them: a set that
  drops one or writes its own `rule R1` is refused, not read.

    rs = load_declared(text)                 # or raises DeclaredRulesError
    rules_doc("two-layer.rules", root=ws)    # what a bundle carries: {"kind": "qccd.rules", ...}
    changes(load("standard"), rs)            # what it drops, replaces and adds
"""

from __future__ import annotations

from pathlib import Path

from . import RuleSet, RuleSyntaxError, catalogue, load, names
from .syntax import parse, show

__all__ = ["DeclaredRulesError", "load_declared", "rules_doc", "changes", "RULES_KIND", "MAX_TEXT", "MAX_RULES"]

RULES_KIND = "qccd.rules"
MAX_TEXT = 64 * 1024
MAX_RULES = 200


class DeclaredRulesError(ValueError):
    """A declared rule set that cannot be an entry's: the message says why, in the person's words."""


def _check_includes(text: str) -> None:
    src = parse(text)
    shipped = set(names("device"))
    for inc in src.includes:
        if inc not in shipped:
            raise DeclaredRulesError(f"include {inc!r}: a rule set may include one this website ships "
                                     f"({', '.join(sorted(shipped))}), by name")


def load_declared(text: str) -> RuleSet:
    """Read an entry's rule set, refusing anything the grader should not read (module docstring)."""
    if not isinstance(text, str) or not text.strip():
        raise DeclaredRulesError("the rule set is empty")
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise DeclaredRulesError(f"the rule set is longer than {MAX_TEXT // 1024} KB")
    if not text.endswith("\n"):
        text += "\n"            # `load` reads a one-line string as a NAME (or a path): never that
    try:
        src = parse(text)
        if src.level != "device":
            raise DeclaredRulesError(f"a leaderboard entry's rules are about its machine, not level {src.level!r}")
        _check_includes(text)
        rs = load(text)
    except RuleSyntaxError as exc:
        raise DeclaredRulesError(str(exc)) from None
    except KeyError as exc:
        raise DeclaredRulesError(str(exc).strip("'\"")) from None
    if len(rs.rules) > MAX_RULES:
        raise DeclaredRulesError(f"the rule set has {len(rs.rules)} rules; at most {MAX_RULES}")
    core = {r.id: r for r in catalogue().rules if r.core}
    for r in rs.rules:
        c = core.get(r.id)
        if c is not None and _key(r) != _key(c):
            raise DeclaredRulesError(f"rule {r.id} is a core rule and holds in every rule set as the catalogue "
                                     "writes it; give your own rule another name")
    return rs


def _key(r) -> tuple:
    return (show(r.formula), show(r.when) if r.when is not None else None)


def rules_doc(spec: str | Path, root: Path | None = None) -> dict:
    """What a bundle carries for a declared rule set: the set written out whole (`RuleSet.text`,
    no include), and the text the person wrote when it was not a shipped set's name.

    `spec` is a shipped set's name, a `.rules` file (inside `root` when one is given), or the
    text itself."""
    s = str(spec)
    source = None
    if "\n" in s:
        source = s
    elif s.endswith(".rules") or "/" in s or "\\" in s:
        p = Path(s)
        if root is not None:
            base = Path(root).resolve()
            p = (base / p).resolve() if not p.is_absolute() else p.resolve()
            if base not in p.parents and p != base:
                raise DeclaredRulesError(f"{s}: a rule set file must be inside the workspace")
        if not p.is_file():
            raise DeclaredRulesError(f"no rule set file {s}")
        source = p.read_text(encoding="utf-8")
    elif s not in names("device"):
        raise DeclaredRulesError(f"no rule set named {s!r}; the shipped ones are {', '.join(names('device'))}, "
                                 "or give a .rules file")
    from . import LIBRARY
    rs = load_declared(source if source is not None else (LIBRARY / f"{s}.rules").read_text(encoding="utf-8"))
    doc = {"kind": RULES_KIND, "version": 1, "name": rs.name, "title": rs.title, "text": rs.text()}
    if source is not None:
        doc["source"] = source
    return doc


def changes(board: RuleSet, declared: RuleSet) -> dict:
    """How a declared set differs from the board's: the board's rules it leaves out, the ones it
    writes differently (same id, other assertion), its own rules, and the parameters it sets
    otherwise (name -> [the board's, its own])."""
    b = {r.id: r for r in board.rules}
    d = {r.id: r for r in declared.rules}
    params = {k: [board.param_text.get(k), declared.param_text.get(k)]
              for k in sorted(set(board.param_text) | set(declared.param_text))
              if k in board.param_text and k in declared.param_text
              and board.param_text.get(k) != declared.param_text.get(k)}
    return {"drops": [i for i in b if i not in d],
            "replaces": [i for i in b if i in d and _key(b[i]) != _key(d[i])],
            "adds": [i for i in d if i not in b],
            "params": params}
