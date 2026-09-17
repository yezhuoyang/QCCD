"""`python -m qccd.gadget` -- the logical-gadget toolchain.  GADGETS.md §10.

    python -m qccd.gadget places                         the verified places, and what was checked
    python -m qccd.gadget algorithm PROGRAM.alg [-o DIR] a logical algorithm on the town, signed off
    python -m qccd.gadget build PROGRAM.lq [-o DIR] [--rows R --pairs P] [--studio]   (beta)
    python -m qccd.gadget library [--code bb72]          characterize the beta leaves, print them
    python -m qccd.gadget showcase [--rows 8 --pairs 6]  the ~10^4-ion beta demonstration
    python -m qccd.gadget parse PROGRAM.lq               what the LogicQ front end understood
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_build(a) -> int:
    from .build import build
    out = a.out or f"out/gadgets/{Path(a.program).stem}"
    res = build(a.program, out_dir=out, rows=a.rows, pairs=a.pairs, cache_dir=a.cache,
                rebuild=a.rebuild, ppm_rounds=a.ppm_rounds, cycles_between=a.cycles_between,
                studio=a.studio, design=a.design)
    return 0 if not res["checks"]["failed"] else 1


def _cmd_places(a) -> int:
    from .library import LeafLibrary
    leaves = LeafLibrary(a.cache, rebuild=a.rebuild)
    bad = 0
    for name, m in leaves.places().items():
        print(f"\n{name}  [{m.family}]  {m.title}")
        for op in m.ops.values():
            lg = op.logic or {}
            fl, di, ck = lg.get("flows"), lg.get("distance"), lg.get("check")
            parts = []
            if fl:
                parts.append(f"flows {sum(f['ok'] for f in fl['flows'])}/{len(fl['flows'])} "
                             f"rank {fl['rank']}/{fl['needed']}")
            if di:
                parts.append("d>=3" if di.get("at_least") else f"d={di.get('distance')}"
                             + ("" if di.get("expect", "ft") == "ft" else " (by design)"))
            if ck:
                parts.append(ck.get("claim", ""))
            if m.family == "road" and op.name.count(".") > 2:
                continue
            print(f"    {op.name:12s} {op.status:9s} logic={lg.get('status', 'none'):8s} "
                  f"{op.duration_us / 1000:8.2f} ms  {'; '.join(parts)}")
            from .place_checks import CLASSICAL_FAMILIES
            want = ("verified",) + (("modeled",) if m.family in CLASSICAL_FAMILIES else ())
            bad += op.status not in want or lg.get("status") == "failed"
    return 1 if bad else 0


def _cmd_algorithm(a) -> int:
    from .verified import build_algorithm
    out = a.out or f"out/gadgets/algorithms/{Path(a.program).stem}"
    res = build_algorithm(a.program, out_dir=out, cache_dir=a.cache, rebuild=a.rebuild,
                          studio_dir=Path(out) / "studio" if a.studio else None)
    so = res["signoff"]
    for r in so.get("relations", []):
        print(f"  {'ok ' if r['ok'] else 'BAD'} {r['relation']}")
    for f in so.get("flows", []):
        print(f"  {'ok ' if f['ok'] else 'BAD'} {f['flow']}")
    d = so.get("distance", {})
    print(f"  distance: {d.get('skipped') or ('>= 3' if d.get('passed') else d)}")
    print(f"  wrote {Path(out) / 'index.html'}")
    return 0 if so.get("passed") and not res["checks"]["failed"] else 1


def _cmd_parse(a) -> int:
    from .logicq import ParseError, parse_file
    try:
        prog = parse_file(a.program)
    except ParseError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(json.dumps(prog.summary(), indent=1))
    for ins in prog.instructions:
        print(f"  {ins.line:4d}  {ins.kind:12s} {ins.op:6s} {', '.join(ins.blocks)}")
    return 0


def _cmd_library(a) -> int:
    from .codes import TUTORIAL_BB, bivariate_bicycle, parse_poly
    from .designs import tile, pair, Designer
    from .library import LeafLibrary
    l, m, A, B, _ = TUTORIAL_BB[a.code]
    code = bivariate_bicycle(None, l, m, parse_poly(A, l, m), parse_poly(B, l, m))
    leaves = LeafLibrary(a.cache, rebuild=a.rebuild)
    d = Designer(leaves)
    pair(d, code)
    for name, master in d.lib.masters.items():
        print(f"\n{name}  [{master.kind} {master.family}]  {master.title}")
        for p in master.ports:
            print(f"    port {p.name:6s} {p.dir:5s} {p.role:9s} x{p.width}")
        for op in master.ops.values():
            print(f"    op   {op.name:12s} {op.status:9s} {op.duration_us / 1000:9.3f} ms  "
                  f"2q={op.metrics.get('gates_2q', 0):4d}  peak n̄={op.metrics.get('peak_quanta', 0)}")
        if master.kind == "composite":
            for i in master.instances:
                print(f"    inst {i.name:6s} {i.master}")
            for c in master.channels:
                print(f"    chan {c.name:6s} {c.a} -> {c.b}  ({c.length} sites)")
    return 0


def _cmd_showcase(a) -> int:
    from .build import build
    from .programs import showcase
    blocks = a.rows * a.pairs * 2
    src = showcase(blocks, code=a.code)
    path = Path(a.out or "out/gadgets/showcase")
    path.mkdir(parents=True, exist_ok=True)
    (path / "showcase.lq").write_text(src, encoding="utf-8")
    from .logicq import parse
    res = build(parse(src, name="showcase"), out_dir=path, rows=a.rows, pairs=a.pairs,
                cache_dir=a.cache, rebuild=a.rebuild, ppm_rounds=a.ppm_rounds,
                cycles_between=a.cycles_between, studio=a.studio)
    print(json.dumps(res["stats"], indent=1))
    return 0 if not res["checks"]["failed"] else 1


def _cmd_site(a) -> int:
    """Only the website's gadgets/ section, wrapped as `python -m qccd site` wraps it."""
    from .site import index_entries, standalone
    idx = None
    if a.index_from:
        import re
        text = Path(a.index_from).read_text(encoding="utf-8")
        m = re.search(r"INDEX = (\[.*?\]);\n", text, re.S)
        if not m:
            print(f"no search index in {a.index_from}", file=sys.stderr)
            return 2
        live = json.loads(m.group(1).replace("<\\/", "</"))
        # this section's own entries are authoritative: drop what the live site says about
        # gadgets/ (an earlier deploy's titles and anchors) and carry the rest unchanged
        from .site import SECTION
        live = [row for row in live if not row["u"].startswith(f"{SECTION}/")]
        idx = json.dumps(live + index_entries(), separators=(",", ":"), ensure_ascii=False)
    res = standalone(Path(a.out), idx, cache_dir=a.cache)
    ok_algs = all(r["signoff"].get("passed") and not r["checks"]["failed"]
                  for r in res["algorithms"].values())
    ok_beta = all(not r["checks"]["failed"] or k == "demo" for k, r in res["beta"].items())
    return 0 if ok_algs and ok_beta else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.gadget", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--cycles-between", type=int, default=1,
                       help="extraction cycles a block runs after each logical op (default 1)")
        p.add_argument("--cache", default="out/gadgets/cache",
                       help="where characterized leaves are kept (default: %(default)s)")
        p.add_argument("--rebuild", action="store_true",
                       help="characterize every leaf again instead of reading the cache")

    b = sub.add_parser("build", help="compile a LogicQ program to the design tool")
    b.add_argument("program")
    b.add_argument("-o", "--out")
    b.add_argument("--rows", type=int)
    b.add_argument("--pairs", type=int)
    b.add_argument("--ppm-rounds", type=int, default=1)
    b.add_argument("--studio", action="store_true",
                   help="also write the full studio page for every leaf op used")
    b.add_argument("--design", help="a design exported by the page's editor (*.gadget.json) "
                   "instead of the generated processor")
    common(b)
    pl = sub.add_parser("places", help="characterize and verify the place library, print it")
    common(pl)
    al = sub.add_parser("algorithm", help="schedule a logical algorithm on the town, sign it off")
    al.add_argument("program", help="an .alg file (qccd/gadget/algorithm.py)")
    al.add_argument("-o", "--out")
    al.add_argument("--studio", action="store_true",
                    help="also write the full studio page for every op used")
    common(al)
    p = sub.add_parser("parse", help="show what the LogicQ front end understood")
    p.add_argument("program")
    lb = sub.add_parser("library", help="characterize and print the leaf and pair masters")
    lb.add_argument("--code", default="bb72")
    common(lb)
    s = sub.add_parser("showcase", help="build the ~10^4-ion demonstration")
    s.add_argument("--rows", type=int, default=8)
    s.add_argument("--pairs", type=int, default=6)
    s.add_argument("--code", default="bb72")
    s.add_argument("--ppm-rounds", type=int, default=1)
    s.add_argument("--studio", action="store_true")
    s.add_argument("-o", "--out")
    common(s)
    st = sub.add_parser("site", help="build the website's gadgets/ section alone")
    st.add_argument("-o", "--out", required=True, help="the section lands in OUT/gadgets/")
    st.add_argument("--index-from", help="a site page whose search index to carry (e.g. the "
                    "live site's index.html), so search from these pages finds the whole site")
    common(st)
    a = ap.parse_args(argv)
    return {"build": _cmd_build, "parse": _cmd_parse, "library": _cmd_library,
            "showcase": _cmd_showcase, "site": _cmd_site, "places": _cmd_places,
            "algorithm": _cmd_algorithm}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
