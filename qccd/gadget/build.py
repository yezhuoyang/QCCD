"""One call from a LogicQ program to the design tool's files.  GADGETS.md §10, §12.

    build("prog.lq", out_dir="out/gadgets/prog")
      -> out/gadgets/prog/index.html      the tool
         out/gadgets/prog/gir.json        the gadget-level program
         out/gadgets/prog/library.json    every master, characterized
         out/gadgets/prog/checks.json     G1-G9
         out/gadgets/prog/leaf/<m>.js     one master's device and programs, loaded on demand
         out/gadgets/prog/studio/<m>.<op>.html   (with studio=True) the full studio page

The design is the beta processor sized to the program: enough pairs for every declared
block, six pairs to a row.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .checks import check
from .designs import processor
from .library import LeafLibrary
from .logicq import LogicalProgram, parse, parse_file
from .synth import synthesize

__all__ = ["build", "design_for"]


def design_for(prog: LogicalProgram, rows: int | None = None,
               pairs: int | None = None) -> tuple[int, int]:
    blocks = max(2, len(prog.declarations))
    need = math.ceil(blocks / 2)
    if pairs is None:
        pairs = min(6, need) if rows is None else math.ceil(need / rows)
    if rows is None:
        rows = math.ceil(need / pairs)
    if rows * pairs * 2 < blocks:
        raise ValueError(f"{rows} x {pairs} pairs hold {rows * pairs * 2} blocks; the program "
                         f"declares {blocks}")
    return rows, pairs


def build(program, *, out_dir, rows: int | None = None, pairs: int | None = None,
          cache_dir="out/gadgets/cache", rebuild: bool = False, ppm_rounds: int = 1,
          cycles_between: int = 1, studio: bool = False, design=None, studio_dir=None,
          log=print) -> dict:
    log = log or (lambda *a, **k: None)
    t_start = time.time()
    prog = program if isinstance(program, LogicalProgram) else (
        parse_file(program) if Path(str(program)).exists() else parse(str(program)))
    codes = {d.code.name for d in prog.declarations.values()}
    if not prog.declarations:
        raise ValueError("the program declares no code block")
    if len(codes) != 1:
        raise ValueError(f"the beta processor holds one code family; the program declares "
                         f"{sorted(codes)}")
    code = next(iter(prog.declarations.values())).code
    leaves = LeafLibrary(cache_dir, rebuild=rebuild, log=log)
    t0 = time.time()
    if design is not None:
        from .designs import load_design
        lib = load_design(design, leaves, code)
        log(f"{prog.name}: {len(prog.declarations)} blocks of {code.name}, "
            f"{len(prog.instructions)} instructions -> {lib.top} (from {design})")
    else:
        rows, pairs = design_for(prog, rows, pairs)
        log(f"{prog.name}: {len(prog.declarations)} blocks of {code.name}, "
            f"{len(prog.instructions)} instructions -> proc_{code.name}_{rows}x{pairs}")
        lib = processor(leaves, code, rows, pairs)
    t_lib = time.time() - t0
    t0 = time.time()
    gir = synthesize(prog, lib, leaves, ppm_rounds=ppm_rounds, cycles_between=cycles_between,
                     log=log)
    t_synth = time.time() - t0
    t0 = time.time()
    verdict = check(gir, lib, leaves)
    t_check = time.time() - t0

    out = Path(out_dir)
    (out / "leaf").mkdir(parents=True, exist_ok=True)
    stats = {
        "ions": gir["n_ions"], "blocks": len(prog.declarations),
        "logical_qubits": sum(d.code.k for d in prog.declarations.values()),
        "instructions": len(prog.instructions), "refused": len(gir["refused"]),
        "events": len(gir["events"]), "carries": len(gir["carries"]),
        "makespan_ms": round(gir["makespan_us"] / 1000, 3),
        "leaf_masters": len(lib.leaf_masters()),
        "seconds": {"library": round(t_lib, 2), "synth": round(t_synth, 2),
                    "check": round(t_check, 2)},
    }
    gir["stats"] = stats
    (out / "gir.json").write_text(json.dumps(gir), encoding="utf-8")
    lib.save(out / "library.json")
    (out / "checks.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    leaf_files = {}
    for name in lib.leaf_masters():
        payload = json.dumps(leaves.data[name]).replace("</", "<\\/")
        (out / "leaf" / f"{name}.js").write_text(
            f"window.__gadgetLeaf&&window.__gadgetLeaf({payload});\n", encoding="utf-8")
        leaf_files[name] = f"leaf/{name}.js"

    studio_pages = {}
    if studio:
        studio_pages = _studio_pages(lib, leaves, gir, out, log,
                                     Path(studio_dir) if studio_dir else out / "studio")

    from .page import render_page
    render_page(out / "index.html", lib=lib, gir=gir, checks=verdict, leaf_files=leaf_files,
                studio_pages=studio_pages, rules=_rules())
    stats["seconds"]["total"] = round(time.time() - t_start, 2)
    log(f"  {stats['ions']} ions, {stats['events']} events, {stats['carries']} carries, "
        f"makespan {stats['makespan_ms']} ms; G passed {verdict['passed']} "
        f"failed {verdict['failed']}")
    log(f"  wrote {out / 'index.html'}")
    return {"out": str(out), "stats": stats, "checks": verdict}


def _rules() -> dict:
    from .checks import RULES
    return RULES


def _studio_pages(lib, leaves: LeafLibrary, gir: dict, out: Path, log, where: Path) -> dict:
    """The full studio page for every leaf op the schedule uses (GADGETS.md §10).

    Rebuilt from the leaf data alone -- the architecture document and the op's TSIR -- so a
    cached leaf needs no recompilation; the page is `qccd.viz.render_html`, the same studio
    every other program in the repository is debugged in, with all 27 rules.  Several builds
    may share one `where` (the website does): a page already there for the same master and
    op is the same program, and is linked rather than rendered again.
    """
    import os
    from ..arch.device import Architecture
    from ..cost.models import corrected_model
    from ..ir.tsir import TSIR
    from ..verify import verify
    from ..viz import render_html

    # every op the schedule uses -- except road junction passes, which move ions through one
    # junction and have nothing to debug that the tool's own replay does not show
    from .place_checks import CLASSICAL_FAMILIES
    # road junctions have nothing to debug that the tool's own replay does not show, and a
    # classical place has no TSIR at all
    used = sorted({(gir["leaves"][e[3]][0], e[4]) for e in gir["events"]
                   if e[4] in lib[gir["leaves"][e[3]][0]].ops
                   and lib[gir["leaves"][e[3]][0]].family != "road"
                   and lib[gir["leaves"][e[3]][0]].family not in CLASSICAL_FAMILIES})
    pages = {}
    where.mkdir(parents=True, exist_ok=True)
    model = corrected_model("qccdsim_jones")
    archs: dict = {}
    for mname, op in used:
        data = leaves.data[mname]
        if mname not in archs:
            archs[mname] = Architecture.from_json(data["arch"])
        arch = archs[mname]
        target = where / f"{mname}.{op}.html"
        if not target.exists():
            prog = TSIR.from_json(data["programs"][op])
            res = verify(prog, arch, model, check_metrics=False).result
            render_html(arch, prog, res, model, target, kicker="logical gadget",
                        headline=f"{mname} · {op}", lede=lib[mname].ops[op].title)
            log(f"  studio page {target.name}")
        pages[f"{mname}.{op}"] = os.path.relpath(target, out).replace(os.sep, "/")
    return pages
