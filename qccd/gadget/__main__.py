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
                          studio_dir=Path(out) / "studio" if a.studio else None,
                          floorplan=a.floorplan, parallel=not a.sequential)
    so = res["signoff"]
    st = res["stats"]
    tr = st["traffic"]
    print(f"  {st['policy']}: makespan {st['makespan_ms']} ms; traffic {tr['ion_sites']} ion-sites "
          f"in {tr['carries']} carries, {tr['junction_passes']} junction passes, bundles waited "
          f"{tr['wait_us'] / 1000:.1f} ms; up to {tr['peak_parallel_ops']} places at work at once")
    rl = res["rules"]
    print(f"  machine rules: {len(rl['passed'])} hold" + (f", FAIL {', '.join(rl['failed'])}"
                                                          if rl["failed"] else ""))
    for r in rl["rules"]:
        if r["verdict"] == "fails":
            print(f"    {r['id']}: {r.get('count', 0)} counterexample(s), e.g. "
                  f"{'; '.join(r.get('examples', [])[:2])}")
    for r in so.get("relations", []):
        print(f"  {'ok ' if r['ok'] else 'BAD'} {r['relation']}")
    for f in so.get("flows", []):
        print(f"  {'ok ' if f['ok'] else 'BAD'} {f['flow']}")
    d = so.get("distance", {})
    if so.get("skipped"):
        print(f"  flat sign-off: not run -- {so['skipped']}")
    else:
        print(f"  distance: {d.get('skipped') or ('>= 3' if d.get('passed') else d)}")
    cs = res["compositional"]
    print(f"  compositional sign-off: {'passed' if cs['passed'] else 'NOT passed'}")
    for part, row in cs["parts"].items():
        extra = ""
        if part == "gates":
            extra = ", ".join(f"{g['gate']}={g['contract']} ({len(g.get('branches', []))} branch"
                              f"{'es' if len(g.get('branches', [])) != 1 else ''})"
                              for g in row["rows"])
        elif part == "semantics":
            extra = "; ".join(f"{' '.join(f'{k}={v}' for k, v in r['outcomes'].items())}: {r['p']:g}"
                              for r in row["distribution"][:8])
        elif part == "composition":
            extra = f"{len(row['held'])} machine rules hold" + (f", FAIL {row['failed']}" if row["failed"] else "")
        print(f"    {'ok ' if row['passed'] else 'BAD'} {part}{': ' + extra if extra else ''}")
    if st.get("refreshes"):
        print(f"  {st['refreshes']} syndrome round(s) added so no block waits past its budget")
    print(f"  wrote {Path(out) / 'index.html'}")
    signed = so.get("passed") or res["compositional"]["passed"]
    return 0 if signed and not res["checks"]["failed"] and not rl["failed"] else 1


def _cmd_floorplan(a) -> int:
    """Read a floorplan, judge it by the floorplan rules, print the verdicts."""
    from .floorplan import FloorplanError, load_file
    from .judge import judge_floorplan
    from .library import LeafLibrary
    leaves = LeafLibrary(a.cache, rebuild=a.rebuild)
    try:
        lib, fp = load_file(a.floorplan, leaves)
    except FloorplanError as exc:
        print(exc, file=sys.stderr)
        return 2
    top = lib[lib.top]
    n_j = sum(1 for i in top.instances if i.master == "junc9")
    print(f"{fp.name}: {len(top.instances) - n_j} places, {n_j} junctions, "
          f"{sum(1 for c in top.channels if c.kind != 'wire')} roads, "
          f"{sum(1 for c in top.channels if c.kind == 'wire')} wires")
    rep = judge_floorplan(lib, leaves, regions=fp.regions)
    for r in rep["rules"]:
        mark = {"holds": "ok  ", "fails": "FAIL"}.get(r["verdict"], "--  ")
        extra = (f": {r.get('count', 0)} counterexample(s), e.g. "
                 f"{'; '.join(r.get('examples', [])[:3])}" if r["verdict"] == "fails" else
                 (f": {r.get('reason')}" if r.get("reason") else ""))
        print(f"  {mark} {r['id']} {r['title']}{extra}")
    return 0 if rep["ok"] else 1


def _cmd_optimize(a) -> int:
    """Search the floorplans of the town family for one that runs a program with less
    traffic (or a shorter makespan), each candidate scheduled; write the best as text."""
    from pathlib import Path as _P
    from .algorithm import parse_algorithm_file
    from .floorplan import FLOORPLANS_DIR, load_file
    from .library import LeafLibrary
    from .optimize import floorplan_text, optimise, rows_of
    leaves = LeafLibrary(a.cache, rebuild=a.rebuild)
    alg = parse_algorithm_file(a.program)
    base = a.floorplan or (FLOORPLANS_DIR / f"{alg.floorplan}.floor" if alg.floorplan else None)
    if base is None:
        print("name a floorplan to start from (--floorplan F)", file=sys.stderr)
        return 2
    lib, fp = load_file(base, leaves)
    res = optimise(alg, rows_of(lib), leaves, objective=a.objective, budget=a.budget,
                   seed=a.seed, log=print)
    s0, s1 = res["start"], res["best"]
    print(f"  {res['seconds']} s, {a.budget} candidates scheduled: traffic {s0['traffic']:,} -> "
          f"{s1['traffic']:,} ion-sites ({100 * (s1['traffic'] - s0['traffic']) / s0['traffic']:+.1f}%), "
          f"makespan {s0['makespan_us'] / 1e6:.2f} -> {s1['makespan_us'] / 1e6:.2f} s")
    out = _P(a.out or _P(base).with_name(_P(base).stem + f"_{a.objective}.floor"))
    # the note is part of the file, and the file is shown on the design page: site words only
    note = (f"found by the floorplan optimiser from the floorplan {_P(base).stem}, for {alg.title}: "
            f"objective {a.objective},\n{a.budget} candidates (seed {a.seed}), each scheduled;\n"
            f"traffic {s0['traffic']:,} -> {s1['traffic']:,} ion-sites (before refresh rounds), makespan "
            f"{s0['makespan_us'] / 1e6:.2f} -> {s1['makespan_us'] / 1e6:.2f} s")
    from .town import town as _town
    res["lib"] = _town(leaves, name=f"{fp.name}_{a.objective}", rows=res["rows"])
    out.write_text(floorplan_text(res, title=f"{fp.title} (less {a.objective})", comment=note),
                   encoding="utf-8", newline="\n")
    print(f"  wrote {out}")
    return 0


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
    al = sub.add_parser("algorithm", help="schedule a logical algorithm on a floorplan, sign it off")
    al.add_argument("program", help="an .alg file (qccd/gadget/algorithm.py)")
    al.add_argument("-o", "--out")
    al.add_argument("--floorplan", help="a .floor file to run on (default: the town)")
    al.add_argument("--sequential", action="store_true",
                    help="one instruction, and one road trip, at a time")
    al.add_argument("--studio", action="store_true",
                    help="also write the full studio page for every op used")
    common(al)
    op = sub.add_parser("optimize", help="search floorplans for less traffic, each candidate scheduled")
    op.add_argument("program", help="an .alg file")
    op.add_argument("--floorplan", help="the floorplan to start from (default: the one the program names)")
    op.add_argument("--objective", choices=("traffic", "makespan", "both"), default="traffic")
    op.add_argument("--budget", type=int, default=40, help="candidates to schedule (default 40)")
    op.add_argument("--seed", type=int, default=1)
    op.add_argument("-o", "--out")
    common(op)
    fl = sub.add_parser("floorplan", help="read a floorplan and judge it by the floorplan rules")
    fl.add_argument("floorplan", help="a .floor file (qccd/gadget/floorplan.py)")
    common(fl)
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
            "algorithm": _cmd_algorithm, "floorplan": _cmd_floorplan,
            "optimize": _cmd_optimize}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
