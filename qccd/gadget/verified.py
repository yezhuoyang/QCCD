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
                    leaves: LeafLibrary | None = None, studio_dir=None, log=print) -> dict:
    """Schedule, check and sign off one algorithm on the town, and write its page.  With
    `studio_dir`, the studio page of every op the schedule uses is rendered there (shared by
    several builds) and linked from the tool's inspector."""
    log = log or (lambda *a, **k: None)
    t_start = time.time()
    alg = _load(alg)
    leaves = leaves or LeafLibrary(cache_dir, rebuild=rebuild, log=log)
    t0 = time.time()
    lib = town(leaves)
    t_lib = time.time() - t0
    t0 = time.time()
    gir = schedule(alg, lib, leaves, log=log)
    t_sched = time.time() - t0
    t0 = time.time()
    checks = check_places(gir, lib, leaves)
    t_check = time.time() - t0
    t0 = time.time()
    signoff = sign_off(alg, gir, lib, leaves) if not gir["refused"] else {
        "passed": False, "why": ["instructions were refused"], "relations": [], "flows": [],
        "circuit": {"qubits": 0, "ops": {}, "records": 0}}
    t_sign = time.time() - t0
    stats = {
        "ions": gir["n_ions"], "blocks": len(gir["blocks"]),
        "instructions": len(alg.instructions), "refused": len(gir["refused"]),
        "events": len(gir["events"]), "carries": len(gir["carries"]),
        "makespan_ms": round(gir["makespan_us"] / 1000, 3),
        "places": sum(1 for v in gir["leaves"].values() if lib[v[0]].family != "road"),
        "junctions": sum(1 for v in gir["leaves"].values() if lib[v[0]].family == "road"),
        "seconds": {"library": round(t_lib, 2), "schedule": round(t_sched, 2),
                    "checks": round(t_check, 2), "signoff": round(t_sign, 2)},
    }
    gir["stats"] = stats
    out = Path(out_dir)
    (out / "leaf").mkdir(parents=True, exist_ok=True)
    (out / "gir.json").write_text(json.dumps(gir), encoding="utf-8")
    lib.save(out / "library.json")
    (out / "checks.json").write_text(json.dumps(checks, indent=1), encoding="utf-8")
    (out / "signoff.json").write_text(json.dumps(signoff, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    (out / f"{alg.name}.alg").write_text(alg.source, encoding="utf-8")
    leaf_files = {}
    for name in lib.leaf_masters():
        payload = json.dumps(leaves.data[name]).replace("</", "<\\/")
        (out / "leaf" / f"{name}.js").write_text(
            f"window.__gadgetLeaf&&window.__gadgetLeaf({payload});\n", encoding="utf-8")
        leaf_files[name] = f"leaf/{name}.js"
    studio_pages = {}
    if studio_dir is not None:
        from .build import _studio_pages
        studio_pages = _studio_pages(lib, leaves, gir, out, log, Path(studio_dir))
    from .page import render_page
    render_page(out / "index.html", lib=lib, gir=gir, checks=checks, leaf_files=leaf_files,
                studio_pages=studio_pages, rules=PLACE_RULES,
                title=f"{alg.title} — QCCD verified gadgets", signoff=signoff,
                categories=CATEGORIES, category_order=ORDER)
    stats["seconds"]["total"] = round(time.time() - t_start, 2)
    log(f"  {alg.name}: {stats['ions']} ions, {stats['events']} events, makespan "
        f"{stats['makespan_ms']} ms; G passed {len(checks['passed'])}/"
        f"{len(PLACE_RULES)}; sign-off "
        f"{'passed' if signoff.get('passed') else 'FAILED'}")
    return {"out": str(out), "stats": stats, "checks": checks, "signoff": signoff,
            "algorithm": alg, "gir": gir, "library": lib}
