"""`python -m qccd.debug` -- debug a programme step by step from a terminal.

    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json summary
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json check [--rules standard] [--assert TEXT|@FILE]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json state 12 [--watch q0,C3]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json step 12
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json trace q0 [--from 0] [--to 99]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json find 'occupancy("C0", t) > 1' [--from 0] [--backward]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json value 'heat("q0", t)' 12
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json rules_at 12 [--rules standard] [--assert TEXT|@FILE]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json inspect R7 12 [--rules ...] [--assert ...]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json closest R13 [--rules ...] [--assert ...]
    python -m qccd.debug PROG.tsir.json --arch DEV.arch.json guide R7

Every command prints JSON (one object).  Steps count from 1, as the Studio's status line does
("Step 12 / 340" is step 12); rules, conditions and expressions are the rule language.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .session import DebugError, open_files


def _text(v: str | None) -> str | None:
    if v and v.startswith("@"):
        return Path(v[1:]).read_text(encoding="utf-8")
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.debug", description=__doc__.splitlines()[0])
    ap.add_argument("programme", help="a .tsir.json")
    ap.add_argument("--arch", required=True, help="the .arch.json it runs on")
    ap.add_argument("--model", default="corrected",
                    help="the clock: corrected (default) or deck, or @FILE: a page's own (its D.model, as JSON)")
    ap.add_argument("--cert", default=None, help="the compiler's .qcert.json: judges the circuit rules (R24, R25) too")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    c = sub.add_parser("check")
    c.add_argument("--rules", default="standard", help="a shipped rule set, a .rules file, or 'none'")
    c.add_argument("--assert", dest="assertions", default=None, help="your assertions, or @file")
    c.add_argument("--only", default=None, help="rule ids, comma separated")
    c.add_argument("--limit", type=int, default=50, help="located failures per rule")
    s = sub.add_parser("state")
    s.add_argument("step", type=int)
    s.add_argument("--watch", default="")
    k = sub.add_parser("step")
    k.add_argument("step", type=int)
    t = sub.add_parser("trace")
    t.add_argument("entity")
    t.add_argument("--from", dest="start", type=int, default=1)
    t.add_argument("--to", dest="end", type=int, default=None)
    f = sub.add_parser("find")
    f.add_argument("condition")
    f.add_argument("--from", dest="start", type=int, default=1)
    f.add_argument("--backward", action="store_true")
    v = sub.add_parser("value")
    v.add_argument("expr")
    v.add_argument("step", type=int)
    # a rule at work, step by step (the Debug page's Why tab)
    r = sub.add_parser("rules_at")
    r.add_argument("step", type=int)
    i = sub.add_parser("inspect")
    i.add_argument("rule")
    i.add_argument("step", type=int)
    i.add_argument("--limit", type=int, default=40, help="cases to show")
    n = sub.add_parser("closest")
    n.add_argument("rule")
    for p in (r, i, n):
        p.add_argument("--rules", default="standard", help="a shipped rule set, a .rules file, or 'none'")
        p.add_argument("--assert", dest="assertions", default=None, help="your assertions, or @file")
    g = sub.add_parser("guide")
    g.add_argument("rule")
    a = ap.parse_args(argv)
    if a.cmd == "guide":                           # the guide needs no programme
        from .guide import guide
        row = guide().get(a.rule)
        print(json.dumps({"rule": a.rule, **row} if row else {"ok": False, "error": f"the guide has no row for {a.rule!r}"}))
        return 0 if row else 2
    try:
        model = a.model
        if model.startswith("@"):
            try:
                model = json.loads(Path(model[1:]).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DebugError(f"the model file cannot be read: {exc}") from None
        sess = open_files(a.arch, a.programme, model, a.cert)
        if a.cmd in ("rules_at", "inspect", "closest"):
            rules = _text(a.rules)
            if rules and rules.endswith(".rules") and Path(rules).exists():
                rules = Path(rules).read_text(encoding="utf-8")
            rules = None if rules == "none" else rules
        if a.cmd == "summary":
            out = sess.summary()
        elif a.cmd == "check":
            rules = _text(a.rules)
            if rules and rules.endswith(".rules") and Path(rules).exists():
                rules = Path(rules).read_text(encoding="utf-8")
            out = sess.check(None if rules == "none" else rules, _text(a.assertions),
                             only=a.only.split(",") if a.only else None, limit=a.limit)
        elif a.cmd == "state":
            out = sess.state(a.step, watch=[w for w in a.watch.split(",") if w] or None)
        elif a.cmd == "step":
            out = sess.step(a.step)
        elif a.cmd == "trace":
            out = sess.trace(a.entity, a.start, a.end)
        elif a.cmd == "find":
            out = sess.find(a.condition, a.start, backward=a.backward)
        elif a.cmd == "rules_at":
            out = sess.rules_at(a.step, rules, _text(a.assertions))
        elif a.cmd == "inspect":
            out = sess.inspect(a.rule, a.step, rules, _text(a.assertions), limit=a.limit)
        elif a.cmd == "closest":
            out = sess.closest(a.rule, rules, _text(a.assertions))
        else:
            out = sess.value(a.expr, a.step)
    except DebugError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(out, indent=1, default=str))
    return 1 if a.cmd == "check" and not out.get("ok", True) else 0


if __name__ == "__main__":
    raise SystemExit(main())
