"""`python -m qccd.ruleset` -- rule sets from the command line.

    list                                   the rule sets this repository ships
    show <set>                             one rule set, resolved: every rule written out
    check <prog.tsir.json> --arch <doc> [--rules <set or file>]
                                           replay a programme and judge it by every rule
    compile <circuit.qasm> --arch <doc> -o <dir> [--rules <set or file>]
                                           compile under a rule set, then judge the result

A rule set is a name (`standard`, `qccdsim`, ...) or a path to a `.rules` file.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import FAILS, HOLDS, load, names


def _print(report) -> int:
    width = max((len(r.id) for r in report.results), default=3)
    for r in report.results:
        line = f"  {r.id:<{width}}  {r.verdict:<20} {r.title}"
        if r.verdict == FAILS:
            line += f"  ({r.count})"
        print(line)
        for e in r.examples[:3]:
            print(f"  {'':<{width}}    {e}")
        if r.reason:
            print(f"  {'':<{width}}    {r.reason}")
    n_hold = sum(1 for r in report.results if r.verdict == HOLDS)
    print(f"{report.ruleset}: {n_hold} of {len(report.results)} rules hold"
          + ("" if report.ok else f"; failing: {', '.join(report.failed()) or 'none'}"))
    return 0 if report.ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.ruleset", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("ruleset")
    c = sub.add_parser("check")
    c.add_argument("programme")
    c.add_argument("--arch", required=True)
    c.add_argument("--rules", default="standard")
    c.add_argument("--json", action="store_true")
    k = sub.add_parser("compile")
    k.add_argument("circuit")
    k.add_argument("--arch", required=True)
    k.add_argument("-o", "--out", required=True)
    k.add_argument("--rules", default="standard")
    k.add_argument("--qccdc", default=None, help="the compiler to run (default: QCCD_QCCDC, else this checkout's build)")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for n in names():
            rs = load(n)
            print(f"{n:24} {len(rs.rules):3} rules  {rs.title}")
        return 0
    if a.cmd == "show":
        sys.stdout.write(load(a.ruleset).text())
        return 0
    if a.cmd == "check":
        from ..arch import load as load_arch
        from ..cost.models import corrected_model
        from ..ir.tsir import TSIR
        from . import check_programme
        rep = check_programme(load(a.rules), TSIR.load(a.programme), load_arch(a.arch), corrected_model())
        if a.json:
            print(json.dumps(rep.to_json(), indent=1))
            return 0 if rep.ok else 1
        return _print(rep)
    if a.cmd == "compile":
        from .compile import compile_under
        out = compile_under(a.circuit, a.arch, load(a.rules), a.out, qccdc=a.qccdc)
        if not out.get("compiled"):
            print(out.get("log", "")[-2000:])
            print("the compiler wrote no programme")
            return 2
        print(f"programme: {out['programme']}")
        return _print(out["report"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
