"""Building the verified library and its logical algorithms.  GADGETS.md §9, §10.

    build_algorithm("examples/gadgets/algorithms/bell_cnot.alg", out_dir=...)
      -> index.html      the design tool on the town, running this algorithm
         gir.json        the schedule
         library.json    every master, with its logic reports
         checks.json     G1-G9 for the place model
         signoff.json    the flat sign-off against the algorithm's ideal circuit
         leaf/<m>.js     each place's device and programs, loaded on demand
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .algorithm import Algorithm, parse_algorithm, parse_algorithm_file
from .categories import CATEGORIES, ORDER
from .city import schedule
from .library import LeafLibrary
from .logic.flat import sign_off
from .place_checks import PLACE_RULES, check_places
from .town import town

__all__ = ["build_algorithm", "ALGORITHMS_DIR"]

ALGORITHMS_DIR = Path(__file__).resolve().parents[2] / "examples" / "gadgets" / "algorithms"


def _load(alg) -> Algorithm:
    if isinstance(alg, Algorithm):
        return alg
    p = Path(str(alg))
    if p.suffix == ".alg" and p.exists():
        return parse_algorithm_file(p)
    return parse_algorithm(str(alg))


def build_algorithm(alg, *, out_dir, cache_dir="out/gadgets/cache", rebuild: bool = False,
                    leaves: LeafLibrary | None = None, studio_dir=None, log=print,
                    floorplan=None, parallel: bool = True, refresh: bool = True,
                    inline: bool = False, focus: str | None = None,
                    stack: dict | None = None, semantics: dict | None = None) -> dict:
    """Schedule, check and sign off one algorithm on a floorplan (the town unless `floorplan`
    names a `.floor` file), and write its page.  With `studio_dir`, the studio page of every
    op the schedule uses is rendered there (shared by several builds) and linked from the
    tool's inspector.  `parallel=False` schedules one instruction at a time; `refresh`
    adds syndrome rounds wherever a block would otherwise wait past its coherence budget
    (`qccd.gadget.refresh`).  A program the flat sign-off cannot follow -- too many branches,
    or frames that are products of bits -- is signed off compositionally
    (`qccd.gadget.logic.compose`); every build writes both reports.  `inline` puts every
    leaf's data in the page itself, so `index.html` is the whole of it (`embed_page`), and
    `focus` is what the page shows first (a place, a kind of place, or an instruction)."""
    log = log or (lambda *a, **k: None)
    t_start = time.time()
    alg = _load(alg)
    leaves = leaves or LeafLibrary(cache_dir, rebuild=rebuild, log=log)
    t0 = time.time()
    regions = None
    if floorplan is None and alg.floorplan:
        from .floorplan import FLOORPLANS_DIR
        floorplan = FLOORPLANS_DIR / f"{alg.floorplan}.floor"
    if floorplan is None:
        lib = town(leaves)
    else:
        from .floorplan import load_file
        lib, fp = load_file(floorplan, leaves)
        regions = fp.regions
    t_lib = time.time() - t0
    t0 = time.time()
    if refresh:
        from .refresh import schedule_with_refresh
        alg, gir = schedule_with_refresh(alg, lib, leaves, log=log, parallel=parallel)
    else:
        gir = schedule(alg, lib, leaves, log=log, parallel=parallel)
    t_sched = time.time() - t0
    t0 = time.time()
    checks = check_places(gir, lib, leaves)
    t_check = time.time() - t0
    t0 = time.time()
    from .judge import judge_schedule, traffic
    rules = judge_schedule(lib, leaves, gir, regions=regions)
    t_rules = time.time() - t0
    t0 = time.time()
    signoff = sign_off(alg, gir, lib, leaves) if not gir["refused"] else {
        "passed": False, "why": ["instructions were refused"], "relations": [], "flows": [],
        "circuit": {"qubits": 0, "ops": {}, "records": 0}}
    t_sign = time.time() - t0
    from .logic.compose import compositional_sign_off
    compositional = compositional_sign_off(alg, gir, rules, flat=signoff, semantics=semantics)
    stats = {
        "ions": gir["n_ions"], "blocks": len(gir["blocks"]),
        "instructions": len(alg.instructions), "refused": len(gir["refused"]),
        "events": len(gir["events"]), "carries": len(gir["carries"]),
        "makespan_ms": round(gir["makespan_us"] / 1000, 3),
        "places": sum(1 for v in gir["leaves"].values() if lib[v[0]].family != "road"),
        "junctions": sum(1 for v in gir["leaves"].values() if lib[v[0]].family == "road"),
        "seconds": {"library": round(t_lib, 2), "schedule": round(t_sched, 2),
                    "checks": round(t_check, 2), "rules": round(t_rules, 2),
                    "signoff": round(t_sign, 2)},
        "policy": gir.get("policy"),
        "traffic": traffic(gir, lib),
        "rules": {"passed": len(rules["passed"]), "failed": rules["failed"],
                  "total": len(rules["rules"])},
        "refreshes": int(gir.get("refreshes") or 0),
        "signed_off": ("flat" if signoff.get("passed") else
                       "compositional" if compositional["passed"] else None),
    }
    gir["stats"] = stats
    out = Path(out_dir)
    (out / "leaf").mkdir(parents=True, exist_ok=True)
    (out / "gir.json").write_text(json.dumps(gir), encoding="utf-8")
    lib.save(out / "library.json")
    (out / "checks.json").write_text(json.dumps(checks, indent=1), encoding="utf-8")
    (out / "rules.json").write_text(json.dumps(rules, indent=1, ensure_ascii=False),
                                    encoding="utf-8")
    (out / "compositional.json").write_text(json.dumps(compositional, indent=1,
                                                       ensure_ascii=False, default=str),
                                            encoding="utf-8")
    (out / "signoff.json").write_text(json.dumps(signoff, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    (out / f"{alg.name}.alg").write_text(alg.source, encoding="utf-8")
    # the leaves go on the website as written: provenance thinned, as build.py's are
    # (inline: no file beside the page, the page alone is the figure)
    from .build import write_leaf_files
    from .roads import attach_runs
    attach_runs(gir, lib, leaves)                 # every carry's road program, for the page
    leaf_files, inline_leaves = write_leaf_files(out, lib, leaves, gir, inline=inline, texts=True)
    studio_pages = {}
    if studio_dir is not None:
        from .build import _studio_pages
        studio_pages = _studio_pages(lib, leaves, gir, out, log, Path(studio_dir))
    from .page import render_page
    render_page(out / "index.html", lib=lib, gir=gir, checks=checks, leaf_files=leaf_files,
                inline_leaves=inline_leaves, focus=focus, leaf_text=not inline,
                studio_pages=studio_pages, rules=PLACE_RULES,
                machine_rules=rules, compositional=compositional, traffic=stats["traffic"],
                floorplan=(Path(floorplan).read_text(encoding="utf-8") if floorplan else None),
                title=f"{alg.title} — QCCD verified gadgets", signoff=signoff,
                categories=CATEGORIES, category_order=ORDER, stack=stack)
    stats["seconds"]["total"] = round(time.time() - t_start, 2)
    log(f"  {alg.name}: {stats['ions']} ions, {stats['events']} events, makespan "
        f"{stats['makespan_ms']} ms; G passed {len(checks['passed'])}/"
        f"{len(PLACE_RULES)}; sign-off "
        + ("passed (flat)" if signoff.get("passed") else
           "passed (compositional; the flat check does not apply)"
           if compositional["passed"] and signoff.get("passed") is None else "FAILED"))
    return {"out": str(out), "stats": stats, "checks": checks, "signoff": signoff, "rules": rules,
            "compositional": compositional,
            "algorithm": alg, "gir": gir, "library": lib}
