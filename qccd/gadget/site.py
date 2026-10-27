"""The gadget tool as a section of the website: qccd.academy/gadgets/.  GADGETS.md §10.

The section's theme is THE FULL STACK (docs/FULLSTACK_PLAN.md): a program written in FormalRV's
language, taken through FormalRV's own compiler and QCCD's lowering to gadgets, scheduled on a
machine of places and roads, down to the hardware program and the ions.

    gadgets/                   the full stack: its runs, its layers, its machine
    gadgets/fullstack/<key>/   one run: the design tool with the Stack, every layer at every instant
    gadgets/places/            the verified places, category by category, and what was checked
    gadgets/algorithms/        Logical Algorithm: the town, and algorithms verified end to end
    gadgets/algorithms/<name>/ the design tool running one algorithm on the town
    gadgets/studio/            the full studio page for every op a build uses
    gadgets/showcase/          beta at scale: 96 BB [[72,12,6]] blocks, 9,720 ions (hardware only)
    gadgets/demo/              beta: every realised LogicQ instruction, one refusal
    gadgets/plan/              docs/GADGETS.md, rendered like the site's other reference docs

`python -m qccd site` calls `index_entries()` before any page is written (every page carries
the search index) and `build_section(out, put)` with its own `put`, so each page here gets
the site's bar, search and comment layer like any other.  `python -m qccd.gadget site`
builds this section alone, for a deploy that must not rebuild the rest of the site.

`embed_page(name, dst)` writes one page as a figure for the tutorial: no bar, no file beside
it, nothing fetched.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .build import build
from .categories import CATEGORIES, ORDER
from .logicq import parse, parse_file
from .programs import showcase

__all__ = ["index_entries", "build_section", "SECTION", "standalone", "embed_page",
           "export_place_arch"]

SECTION = "gadgets"
ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "GADGETS.md"
DEMO = ROOT / "examples" / "gadgets" / "demo.lq"
ALGORITHMS = ROOT / "examples" / "gadgets" / "algorithms"

#: the beta showcase: 8 rows of 6 pairs of tiles
SHOWCASE = (8, 6)

#: THE FULL STACK's runs (docs/FULLSTACK_PLAN.md S2-S7c): each a program in FormalRV's language,
#: through every layer to the machine's ions.  `prog` is a path in this repository or, when it
#: starts "FormalRV/", in the pinned FormalRV clone (`qccd.fullstack.formalrv`); a `big` run is
#: left out of a quick build (`python -m qccd.gadget site --fullstack small`).
FULLSTACK = (
    {"key": "cnot", "title": "One logical CNOT", "prog": "examples/fullstack/cnot.prog",
     "specialize": {"n": "2"}, "machine": "examples/fullstack/s1.machine",
     "sysspec": "examples/fullstack/cnot.sysspec",
     "about": "CX(0, 1): the smallest program that goes through every layer"},
    {"key": "dj", "title": "Deutsch–Jozsa", "prog": "FormalRV/Examples/DJ/DJ.prog",
     "specialize": {"n": "1", "a": "1"},
     "about": "FormalRV's own example: loops, a branch, and an oracle that is a gadget called by name"},
    {"key": "shor15", "title": "Shor's algorithm for 15", "prog": "examples/fullstack/shor15.prog",
     "specialize": {"n": "7"},
     "about": "the order of 7 mod 15, written in FormalRV's language: two Toffolis and a "
              "semiclassical inverse QFT, with 15 T states"},
    {"key": "shor", "title": "FormalRV's Shor example", "prog": "FormalRV/Examples/Shor/Shor.prog",
     "fold": True, "floorplan": "fullstack_wide", "inputs": {6: 1}, "big": True,
     "about": "FormalRV's own Shor, unmodified, at its witness N = 3, a = 2, on a machine with 32 "
              "memories; every gate is scheduled once per context and called"},
)


def _render_plan():
    from ..site.md import Renderer, hints
    from ..site.build import DOC_NAMES, REPO

    def link(target: str) -> str:
        if re.match(r"^(https?:|#|mailto:)", target):
            return target
        path, _, frag = target.partition("#")
        frag = f"#{frag}" if frag else ""
        stem = Path(path).name[:-3] if path.endswith(".md") else None
        if stem in DOC_NAMES:
            return f"../../docs/{stem}/{frag}"
        return f"{REPO}/tree/compiler/{path}{frag}"

    r = Renderer(hints(), link)
    from ..site import prose
    body = r.render(prose.strip(PLAN.read_text(encoding="utf-8"), PLAN.stem))
    return body, r.headings


def _algorithm_titles() -> list[tuple[str, str]]:
    out = []
    for f in sorted(ALGORITHMS.glob("*.alg")) if ALGORITHMS.exists() else []:
        title = f.stem
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("title "):
                title = line[6:].strip()
                break
        out.append((f.stem, title))
    return out


def index_entries() -> list[dict]:
    """What the site's search finds in this section."""
    rows = [
        {"t": "The full stack: a program down to ions", "d": "FormalRV programs through "
         "Pauli-product measurements, QEC, gadgets and the hardware program to ions, every layer "
         "linked and checked", "u": f"{SECTION}/", "k": "page"},
    ]
    rows += [{"t": f"{r['title']} (full stack)", "d": r["about"], "u": f"{SECTION}/fullstack/{r['key']}/",
              "k": "gadgets"} for r in FULLSTACK]
    rows += [
        {"t": "Logical gadgets: verified places", "d": "syndrome extraction, preparation, "
         "readout, transversal CNOT, lattice surgery, magic-state factory and injection, each "
         "checked as hardware and as logic", "u": f"{SECTION}/places/", "k": "page"},
        {"t": "Logical Algorithm", "d": "algorithms composed from verified places on a town of "
         "streets, each signed off end to end against its ideal circuit",
         "u": f"{SECTION}/algorithms/", "k": "page"},
        {"t": "Gadget showcase (beta): 9,720 ions", "d": "96 BB[[72,12,6]] blocks, hardware-"
         "verified only", "u": f"{SECTION}/showcase/", "k": "gadgets"},
        {"t": "Gadget demo (beta)", "d": "every realised LogicQ instruction, hardware-verified "
         "only", "u": f"{SECTION}/demo/", "k": "gadgets"},
    ]
    for key in ORDER:
        c = CATEGORIES[key]
        rows.append({"t": f"{c['title']} ({c['place'].lower()})", "d": c["job"],
                     "u": f"{SECTION}/places/#cat-{key}", "k": "gadgets"})
    rows.append({"t": "The classical half: decoder, memory, wires", "d": "syndromes to the "
                 "decoder, its Pauli-frame update into the classical memory, guards back to the "
                 "places; latencies, reaction time, causality checks",
                 "u": f"{SECTION}/places/#classical", "k": "gadgets"})
    rows.append({"t": "Classically controlled execution (dynamic circuits)", "d": "if m: s q0 "
                 "— a guarded operation, its condition as a parity of measured outcomes, the "
                 "decision wire, worst-case reservation, and a sign-off per branch",
                 "u": f"{SECTION}/places/#classical", "k": "gadgets"})
    for stem, title in _algorithm_titles():
        rows.append({"t": title, "d": "a logical algorithm on verified gadgets, signed off end "
                     "to end", "u": f"{SECTION}/algorithms/{stem}/", "k": "gadgets"})
    if PLAN.exists():
        _, heads = _render_plan()
        rows += [{"t": html.unescape(re.sub(r"<[^>]+>", "", text)), "d": "Logical gadgets · design",
                  "u": f"{SECTION}/plan/#{sid}", "k": "doc"} for lvl, sid, text in heads if lvl == 2]
    return rows


def build_section(out: Path, put, *, cache_dir="out/gadgets/cache", log=print,
                  fullstack: str = "all") -> dict:
    """Write `gadgets/` under the site directory `out` through the site's `put`.  `fullstack`:
    "all" runs every program of FULLSTACK, "small" leaves out the big ones, "none" none."""
    from ..site.build import HASH_JS, PAGE, STYLE
    from .floorplan import FLOORPLANS_DIR, load_file
    from .library import LeafLibrary
    from .verified import build_algorithm

    base = Path(out) / SECTION
    studio = base / "studio"
    leaves = LeafLibrary(cache_dir, log=log)

    # -- the full stack: FormalRV programs, every layer, on the machine ----------------------
    runs = _build_fullstack(base, put, leaves=leaves, studio=studio, cache_dir=cache_dir, log=log,
                            which=fullstack)
    machines = {name: load_file(FLOORPLANS_DIR / f"{name}.floor", leaves)[0]
                for name in ("fullstack", "fullstack_wide")}

    # -- Logical Algorithm: every example, on the town, signed off ---------------------------
    algs = {}
    for f in sorted(ALGORITHMS.glob("*.alg")):
        where = base / "algorithms" / f.stem
        res = build_algorithm(f, out_dir=where, leaves=leaves, studio_dir=studio, log=log)
        page = (where / "index.html").read_text(encoding="utf-8")
        put(f"{SECTION}/algorithms/{f.stem}/index.html", page, 3, SECTION, app=True)
        algs[f.stem] = res

    # -- the beta builds, at the addresses they have always had ------------------------------
    beta = {}
    rows, pairs = SHOWCASE
    runs = [("showcase", parse(showcase(rows * pairs * 2), name="showcase"),
             dict(rows=rows, pairs=pairs))]
    if DEMO.exists():
        runs.append(("demo", parse_file(DEMO), {}))
    for key, prog, size in runs:
        where = base / key
        res = build(prog, out_dir=where, cache_dir=cache_dir, studio=True, studio_dir=studio,
                    log=log, **size)
        (where / f"{prog.name}.lq").write_text(prog.source, encoding="utf-8")
        page = (where / "index.html").read_text(encoding="utf-8")
        put(f"{SECTION}/{key}/index.html", page, 2, SECTION, app=True)
        beta[key] = res

    # the studio pages are app pages: the bar, the comment layer and the hash handler, once each
    for page_path in sorted(studio.glob("*.html")) if studio.exists() else []:
        page = page_path.read_text(encoding="utf-8")
        if 'id="sitenav"' not in page:
            put(f"{SECTION}/studio/{page_path.name}", page, 2, SECTION, app=True, extra=HASH_JS)

    town_lib = next(iter(algs.values()))["library"] if algs else None
    put(f"{SECTION}/index.html",
        PAGE.format(title="Gadgets: the full stack - QCCD studio", style=STYLE, extra_css=EXTRA_CSS,
                    body=_landing(runs, machines, town_lib, algs, beta)), 1, SECTION)
    put(f"{SECTION}/places/index.html",
        PAGE.format(title="Logical gadgets: the places - QCCD studio", style=STYLE, extra_css=EXTRA_CSS,
                    body=_places_page(town_lib, algs, beta, studio, more=machines.values())), 2, SECTION)
    if algs:
        put(f"{SECTION}/algorithms/index.html",
            PAGE.format(title="Logical Algorithm - QCCD studio", style=STYLE, extra_css=EXTRA_CSS,
                        body=_algorithms_page(town_lib, algs)), 2, SECTION)
    if PLAN.exists():
        body, heads = _render_plan()
        toc = "".join(f'<li><a href="#{sid}">{text}</a></li>' for lvl, sid, text in heads if lvl == 2)
        put(f"{SECTION}/plan/index.html",
            PAGE.format(title="Logical gadgets: the design - QCCD studio", style=STYLE, extra_css="",
                        body=f'<p class="sub"><a href="../">Logical gadgets</a> &rsaquo; the design'
                             f'</p><ul class="toc">{toc}</ul>{body}'),
            2, SECTION)
    n_studio = len(list(studio.glob("*.html"))) if studio.exists() else 0
    log(f"  gadgets      {len(runs)} full-stack runs, {len(algs)} verified algorithms, beta "
        f"showcase {beta['showcase']['stats']['ions']} ions, {n_studio} studio pages")
    return {"fullstack": runs, "algorithms": algs, "beta": beta}


def _build_fullstack(base: Path, put, *, leaves, studio: Path, cache_dir, log, which: str) -> dict:
    """Every run of FULLSTACK into `gadgets/fullstack/<key>/`: FormalRV's stages at its pinned
    commit, QCCD's lowering and links, the schedule on the machine and every check, and the
    page with its Stack.  The stages' own files are made in a scratch folder, not published."""
    import shutil
    import tempfile
    from ..fullstack import formalrv, town

    runs: dict = {}
    if which == "none":
        return runs
    try:
        formalrv.check_pin()
    except formalrv.ToolchainError as e:
        log(f"  full stack   skipped: {e}")
        return runs
    for spec in FULLSTACK:
        if spec.get("big") and which != "all":
            continue
        prog = (formalrv.root() / spec["prog"] if spec["prog"].startswith("FormalRV/")
                else ROOT / spec["prog"])
        where = base / "fullstack" / spec["key"]
        work = Path(tempfile.mkdtemp(prefix=f"qccd-fullstack-{spec['key']}-"))
        try:
            res = town.run(prog, out=work, specialize=spec.get("specialize"),
                           machine=ROOT / spec["machine"] if spec.get("machine") else None,
                           sysspec=ROOT / spec["sysspec"] if spec.get("sysspec") else None,
                           title=spec["title"], fold=spec.get("fold", False),
                           floorplan=spec.get("floorplan", "fullstack"), inputs=spec.get("inputs"),
                           leaves=leaves, cache_dir=cache_dir, studio_dir=studio, site_dir=where)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        page = (where / "index.html").read_text(encoding="utf-8")
        put(f"{SECTION}/fullstack/{spec['key']}/index.html", page, 3, SECTION, app=True)
        runs[spec["key"]] = {**spec, "result": res}
        c = res["concrete"]
        log(f"  full stack   {spec['key']}: {c['instructions']:,} instructions, "
            f"{_fmt_time(c['makespan_ms'])} on the machine, built in {res['total_s']} s")
    return runs


def stack_ok(res: dict) -> bool:
    """A full-stack run holds: what it computes is the .prog's, nothing refused, every check."""
    ck = res["checks"]
    return bool(res["contract"].get("passed") and not res["concrete"]["refused"]
                and not ck["places"].get("failed") and not (ck.get("machine_rules") or {}).get("failed")
                and not ck.get("composition_problems"))


EXTRA_CSS = """
.gstat{display:flex;gap:22px;flex-wrap:wrap;margin:10px 0 0}
.gstat div{font-size:12px;color:var(--ink3)} .gstat b{display:block;font-size:19px;color:var(--ink);font-weight:600}
.ok{color:#067647;font-weight:600} .bad{color:#b42318;font-weight:600} .muted{color:var(--ink3)}
.steps{padding-left:18px} .steps li{margin:5px 0}
svg.design{width:100%;height:auto;background:#fbfbfd;border:1px solid var(--line);border-radius:10px;margin:14px 0 4px}
.shp{vertical-align:-3px;margin-right:6px}
.cats{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 4px;font-size:13px}
.cats a{color:var(--ink);white-space:nowrap}
.cathead{display:flex;align-items:baseline;gap:8px;margin:36px 0 4px;padding-top:12px;border-top:1px solid var(--line)}
.cathead h3{margin:0;font-size:20px} .cathead .place{color:var(--ink3);font-size:14px}
.place-card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.place-card h4{margin:0 0 2px;font-size:15px} .place-card .pdoc{color:var(--ink2);font-size:13px;margin:0 0 8px}
.place-card table{font-size:12.5px;margin:6px 0} .place-card td,.place-card th{padding:3px 10px 3px 0;vertical-align:top;text-align:left}
.place-card details{margin:6px 0 0;font-size:12.5px} .place-card summary{cursor:pointer;color:var(--ink2)}
.flows{columns:2 320px;margin:6px 0;padding-left:16px} .flows li{break-inside:avoid;margin:1px 0}
.pill{display:inline-block;font-size:11.5px;padding:0 7px;border-radius:9px;font-weight:600;white-space:nowrap}
.pill.ok{background:#dcfae6;color:#067647} .pill.bad{background:#fee4e2;color:#b42318} .pill.na{background:#f2f4f7;color:#475467}
.algo{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.algo h3{margin:0 0 4px;font-size:16px} .algo pre{font-size:12px;background:#f8f9fb;border-radius:6px;padding:8px 10px;margin:8px 0;overflow-x:auto}
.algo ul{margin:6px 0;padding-left:18px;font-size:13px}
.beta{background:#fffaeb;border:1px solid #fedf89;border-radius:10px;padding:12px 16px}
"""


def _fmt_ms(us: float) -> str:
    if us < 1000:                      # the classical half works in microseconds
        return f"{us:g} µs"
    return f"{us / 1000:.1f} ms" if us < 1e6 else f"{us / 1e6:.2f} s"


def _pill(ok, yes="✓", no="✗", na=None) -> str:
    if ok is None:
        return f'<span class="pill na">{na or "—"}</span>'
    return f'<span class="pill {"ok" if ok else "bad"}">{yes if ok else no}</span>'


def _logic_cells(op) -> str:
    lg = op.logic or {}
    ck = lg.get("check")
    if ck and ck.get("kind") == "lookup":
        # the decoder: its function is a table, and the proof is that the table is one
        return (f'<td>{_pill(ck.get("passed"))} {ck["entries"]} entries, '
                f'{ck["syndrome_bits"]} syndrome bits</td>'
                f'<td>{_pill(ck.get("passed"))} corrects every one of the '
                f'{ck["single_faults"]:,} single faults</td>')
    if not lg or lg.get("status") == "none":
        return '<td><span class="pill na">no quantum content</span></td><td></td>'
    parts = []
    fl = lg.get("flows")
    if fl:
        n_ok = sum(1 for f in fl["flows"] if f["ok"])
        parts.append(f'{_pill(fl["passed"])} {n_ok}/{len(fl["flows"])} flows, rank '
                     f'{fl["rank"]}/{fl["needed"]}')
    ck = lg.get("check")
    if ck:
        parts.append(f'{_pill(ck.get("passed"))} state vector: '
                     f'{html.escape(ck.get("output") or "")} exact')
    dist = lg.get("distance")
    if dist:
        if dist.get("at_least"):
            d = f'{_pill(True)} d ≥ 3 <span class="muted">({dist["faults"]:,} faults)</span>'
        elif dist.get("distance"):
            expected = dist.get("expect", "ft") != "ft"
            d = (f'{_pill(None, na="by design")} d = {dist["distance"]}' if expected
                 else f'{_pill(False)} d = {dist["distance"]}')
        else:
            d = _pill(False) + " " + html.escape(dist.get("error", ""))
    elif ck and ck.get("undetected_3") is not None:
        # a distillation: the 15-to-1 factory's T-error counts
        d = (f'{_pill(ck.get("passed"))} 1&ndash;2 T errors caught; '
             f'{ck["undetected_3"]} triples: 35p³')
    elif ck:
        # one physical ion in a state (the rotation source): nothing encoded, nothing distilled
        d = f'{_pill(None, na="by design")} one ion, not encoded'
    else:
        d = '<span class="muted">&mdash;</span>'
    return f"<td>{'<br>'.join(parts)}</td><td>{d}</td>"


def _details(op) -> str:
    lg = op.logic or {}
    bits = []
    fl = lg.get("flows")
    if fl:
        items = []
        specs = fl.get("spec", {}).get("flows", [])
        for f, spec in zip(fl["flows"], specs):
            recs = f.get("records") or []
            where = f" ⊕ {len(recs)} record{'s' if len(recs) != 1 else ''}" if recs else ""
            items.append(f'<li>{"✓" if f["ok"] else "✗"} <b>{html.escape(f["name"])}</b> '
                         f'<span class="muted">({spec.get("kind", f["kind"])}{where})</span></li>')
        gates = ", ".join(f"{v} {k}" for k, v in (fl.get("gates") or {}).items())
        bits.append(f"<p>The circuit read off the hardware program ({gates}) was run once on a "
                    f"Choi state &mdash; every ion entangled with a reference &mdash; with every "
                    f"measurement outcome kept symbolic. {len(fl['flows'])} stabilizer flows hold, "
                    f"and their rank {fl['rank']} is the {fl['needed']} a complete specification "
                    f"needs, so no other channel satisfies them.</p>"
                    f'<ul class="flows">{"".join(items)}</ul>')
    dist = lg.get("distance")
    if dist and not dist.get("error"):
        if dist.get("at_least"):
            verdict = ("no single fault and no pair of faults flips a logical observable without "
                       "a detector firing")
        else:
            verdict = (f"{dist['distance']} fault(s) suffice: "
                       + html.escape(" + ".join(dist.get("witness", []))))
        bits.append(f"<p>Fault distance: the op between ideal encoders and decoders, "
                    f"{dist['faults']:,} fault locations, {dist['detectors']} detectors, "
                    f"{dist['observables']} observables &mdash; {verdict}."
                    + (f" {html.escape(dist['expect'])}." if dist.get("expect", "ft") != "ft" else "")
                    + "</p>")
    ck = lg.get("check")
    if ck:
        bits.append(f"<p>{html.escape(ck.get('claim', ''))}.</p>")
    if op.notes:
        bits.append("<ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in op.notes) + "</ul>")
    if not bits:
        return ""
    return (f"<details><summary>what was checked for <code>{html.escape(op.name)}</code>"
            f"</summary>{''.join(bits)}</details>")


def _uses(algs: dict) -> dict:
    """(master, op) -> (algorithm, instance path, time) of its first use, for a watch link."""
    out = {}
    for stem, res in algs.items():
        gir = res["gir"]
        for e in gir["events"]:
            key = (gir["leaves"][e[3]][0], e[4])
            if key not in out:
                out[key] = (stem, e[3], e[1] + (e[2] - e[1]) * 0.35)
    return out


def _all_masters(libs) -> list:
    """Every place master the given designs use, once each by name."""
    seen: dict = {}
    for lib in libs:
        for m in (lib.masters.values() if lib is not None else []):
            if m.kind == "leaf":
                seen.setdefault(m.name, m)
    return list(seen.values())


def _places_page(lib, algs: dict, beta: dict, studio: Path, more=()) -> str:
    """The places every machine is built from (the town and the full stack's machines, `more`),
    category by category, and how each is checked (this was the section's landing before the
    full stack became its theme)."""
    from .svg import design_svg, icon

    pool = _all_masters([lib, *more])

    body = [
        '<p class="sub"><a href="../">Gadgets</a> &rsaquo; the places</p>',
        "<h1>The places</h1>",
        '<p class="sub">A fault-tolerant quantum computer on trapped ions is a town. Its places are '
        "where many ions go to get one job done: a logical block is born at a "
        "<b>preparation</b> place, rests in a <b>logical zone</b>, goes for a check-up at "
        "<b>syndrome extraction</b>, meets another block at a <b>lattice-surgery</b> bridge or a "
        "transversal-gate workshop, and ends at a <b>readout</b>; magic states come from a "
        "<b>factory</b> through an <b>injection</b> place. Ions travel between places on roads, "
        "the way people travel between a school, a hotel and a hospital.</p>",
        '<p class="sub">Every place below is verified twice. Its hardware program replays with no '
        'hardware <a href="../../rules/">rule</a> failing and ends where it promised. And the quantum '
        "circuit that program executes &mdash; read off its own gate, measurement and reset "
        "instructions &mdash; is checked, for every input state and every measurement outcome at "
        "once, to compute exactly what the place promises, and with fault distance 3 wherever "
        "the place promises that too.</p>",
    ]
    if lib is not None:
        body.append(design_svg(lib))
        body.append('<p class="muted" style="font-size:12.5px;margin:0">The town the algorithms run '
                    'on: every verified place, two rows between three streets. '
                    '<a href="../algorithms/">Logical Algorithm &rarr;</a></p>')
        leaves = pool
        ops = [op for m in leaves for op in m.ops.values()]
        n_ok = sum(1 for op in ops if op.status == "verified")
        n_logic = sum(1 for op in ops if (op.logic or {}).get("status") == "verified")
        n_alg = sum(1 for r in algs.values() if _signed(r))
        body.append(f'<div class="gstat"><div><b>{len([m for m in leaves if m.family != "road"])}</b>places</div>'
                    f'<div><b>{n_ok}/{len(ops)}</b>ops pass the hardware rules</div>'
                    f'<div><b>{n_logic}</b>ops verified as logic</div>'
                    f'<div><b>{n_alg}/{len(algs)}</b>algorithms signed off end to end</div></div>')

    body.append('<h2 id="places">The places</h2><p class="sub">Each category has its own colour and '
                'its own outline &mdash; on this page, in the tool, and in every drawing of a '
                'design.</p><div class="cats">' + "".join(
                    f'<a href="#cat-{k}">{icon(k, 13)}{html.escape(CATEGORIES[k]["title"])}</a>'
                    for k in ORDER) + "</div>")
    uses = _uses(algs)
    for key in ORDER:
        c = CATEGORIES[key]
        masters = sorted((m for m in pool if m.family == key), key=lambda m: m.name)
        body.append(f'<div class="cathead" id="cat-{key}">{icon(key, 20)}<h3>{html.escape(c["title"])}</h3>'
                    f'<span class="place">the {html.escape(c["place"].lower())}</span></div>'
                    f'<p class="sub" style="margin:2px 0 6px">{html.escape(c["job"])}</p>')
        for m in masters:
            rows = []
            shown = [op for op in m.ops.values()]
            if m.family == "road":
                shown = [op for op in shown if op.name.count(".") == 2][:3]
            for op in shown:
                use = uses.get((m.name, op.name))
                links = []
                if use:
                    stem, path, t = use
                    links.append(f'<a href="../algorithms/{stem}/#p={path}&amp;t={int(t)}">watch</a>')
                if (studio / f"{m.name}.{op.name}.html").exists():
                    links.append(f'<a href="../studio/{m.name}.{op.name}.html">studio</a>')
                rules = len(op.rules.get("passed", []))
                hw = (f'<span class="pill na">modeled</span> latency from the literature'
                      if op.status == "modeled" else
                      f"{_pill(op.status == 'verified')} {rules} rules")
                rows.append(f"<tr><td><code>{html.escape(op.name)}</code><br><span class=\"muted\">"
                            f"{html.escape(op.title)}</span></td>"
                            f"<td>{hw}</td>"
                            f"{_logic_cells(op)}"
                            f"<td class=\"muted\">{_fmt_ms(op.duration_us)}"
                            + ("" if op.status == "modeled" else
                               f"<br>{op.metrics.get('gates_2q', 0)} two-qubit") + "</td>"
                            f"<td>{' &middot; '.join(links)}</td></tr>")
            if m.family == "road":
                rows.append(f'<tr><td colspan="6" class="muted">&hellip; and {len(m.ops) - len(shown)} '
                            f'more passes: every pair of arms, bundles of 1, 8 and 9 ions</td></tr>')
            details = "".join(_details(op) for op in m.ops.values()) if m.family != "road" else ""
            body.append(f'<div class="place-card"><h4>{html.escape(m.title)} <span class="muted">'
                        f'&middot; <code>{html.escape(m.name)}</code></span></h4>'
                        f'<p class="pdoc">{html.escape(m.doc)}</p><div class="tw"><table><thead><tr>'
                        f"<th>op</th><th>hardware</th><th>logic</th><th>fault distance</th>"
                        f"<th>time</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
                        f"{details}</div>")

    body.append(
        '<h2 id="classical">The classical half</h2><p class="sub">A fault-tolerant machine is '
        'half classical, and that half is just as finite: a decoder sitting somewhere, wired to '
        'every place that measures, and a memory holding what it decides. Both are gadgets on '
        'the same map &mdash; a control floor below the last street &mdash; and the wires '
        'between them are drawn dashed and dark, because bits are not ions.</p>'
        '<ul class="steps">'
        "<li><b>Syndromes travel on wires.</b> A place's classical port sends its check "
        "outcomes to the decoder 5&nbsp;µs later (the measurement-to-branch latency measured on "
        "a trapped-ion machine, AQT M-ACTION, arXiv:2101.11390); the decoder's lookup takes "
        "1&nbsp;µs and the write into the memory 0.1&nbsp;µs, so a decision is ready 6.1&nbsp;µs "
        "after the last measurement it depends on &mdash; the <i>reaction time</i>. Every "
        "message is checked for causality (G10) and the decoder for keeping up (G11).</li>"
        "<li><b>The decoder is verified, not asserted.</b> Its lookup table is built from the "
        "syndrome-extraction gadget's own fault experiment: every single fault is grouped by the "
        "syndrome it produces, and the table is a function exactly when no two faults with the "
        "same syndrome differ in their logical effect. That is the proof that it corrects every "
        "single fault.</li>"
        "<li><b>Corrections are kept in software.</b> Every block carries a Pauli frame "
        "X&#772;<sup>a</sup> Z&#772;<sup>b</sup> whose bits are parities of logical outcomes. A "
        "logical Pauli is a line in the archive and touches no ion; a transversal CNOT copies "
        "the frame across; the S&#772; gadget conjugates it and adds its own correction. Every "
        "readout the frame anticommutes with is flipped when the archive reports it.</li>"
        "<li><b>And the sign-off allows exactly those.</b> The flat check reads the frames the "
        "archive holds and nothing else: drop one outcome from a correction, or empty the "
        "archive, and the schedule stops matching the algorithm, and a test says so.</li>"
        "<li><b>Classically controlled execution.</b> <code>if m: s q0</code> is a dynamic "
        "circuit: the ions travel and the place reserves the op either way &mdash; the worst "
        "case, so the schedule stays static &mdash; and only the pulses depend on a bit "
        "measured while the machine runs, delivered to that place's <code>ctl</code> port "
        "before it may start. A condition is exact: the parity of a constant and some "
        "measured outcomes. Each branch of the program is signed off on its own, and a guard "
        "on a place that cannot be told is refused with that reason.</li>"
        "<li><b>Why it has to exist.</b> A T gate by teleportation needs an S&#772; "
        "correction, and S&#772; is Clifford, not Pauli: no frame can hold it, so the "
        "S&#772; gadget itself must run &mdash; and only when the surgery outcome says so "
        "(<code>t_gate</code>). Applying a correction rather than recording it also hands "
        "the next instruction a known state instead of a note: <code>teleport_fix</code> "
        "reads out <code>r = 0</code> in <i>every</i> branch, where the same schedule "
        "without the correction only promises <code>r &oplus; m&#8322; = 0</code>.</li>"
        "</ul>")

    if algs:
        body.append('<h2 id="algorithms">Logical Algorithm</h2><p class="sub">Places compose into '
                    'algorithms: a program of logical instructions is scheduled on the town, blocks '
                    'travel from door to door, and the whole schedule &mdash; every ion, every '
                    'gate &mdash; is flattened and checked against the algorithm\'s ideal circuit. '
                    '<a href="../algorithms/">All algorithms, and how to write one &rarr;</a></p>'
                    '<div class="cards">')
        for stem, res in algs.items():
            body.append(_algo_card(stem, res, prefix="../algorithms/"))
        body.append("</div>")

    body.append(
        '<h2 id="how">How the checks work</h2><ul class="steps">'
        "<li><b>Stabilizer flows.</b> A place's promise is a list of flows: <i>the value P had "
        "on the ions before equals the value Q has after, up to named measurement records</i>. "
        "The circuit runs once on a stabilizer tableau whose signs are symbolic in the "
        "measurement record, starting from a Choi state (every ion Bell-paired with a "
        "reference), so one run covers every input and every outcome. A spec must also be "
        "complete &mdash; as many independent flows as the channel has degrees of freedom &mdash; "
        "so nothing but the intended channel can satisfy it.</li>"
        "<li><b>Fault distance.</b> The op is embedded between ideal encoders and decoders. A "
        "record parity is a detector if it involves no logical measurement and an observable if "
        "it does, which the reference qubits make canonical. Every single fault and every pair "
        "of faults of the standard circuit-level model is pushed through at once, as the bits "
        "of Pauli frames.</li>"
        "<li><b>The T factory</b> is the one non-Clifford place: a sparse state vector shows it "
        "accepts with probability 1 and outputs T†|+⟩ exactly, and Pauli frames show that every "
        "one or two T-gate errors are caught while exactly 35 triples are not (output error "
        "35p³).</li>"
        "<li><b>Composition.</b> An algorithm's schedule is flattened ion by ion and simulated "
        "once. Its decoded outcomes must satisfy every relation of the ideal circuit and carry "
        "the same number of random bits, every input must reach every surviving block, and no "
        "one or two faults anywhere in the schedule may flip a result unseen.</li>"
        "<li><b>Cross-checked.</b> The tableau agrees with stim on thousands of random flows, "
        "every place's fault distance agrees with stim's own search, and mutated programs "
        "&mdash; a CNOT turned around, a hook-unsafe order, a missing reset &mdash; fail.</li></ul>")
    body.append(
        '<h2>What is established, and what is not</h2><ul class="steps">'
        "<li>Established: every place op above passes the hardware rules; every quantum op "
        "computes exactly its specification; every error-correcting op has fault distance 3 on "
        "the d = 3 surface code. Injection and the rotation source are not fault-tolerant by "
        "design, and neither is the Pauli-product bench, whose distance is not computed. Every algorithm in "
        "the Logical Algorithm section computes its ideal circuit, and the Clifford ones keep "
        "distance 3 as a whole schedule.</li>"
        "<li>Not established: logical error <i>rates</i> (no noise is sampled; distance is "
        "counted), decoding (the records are shown to determine the answer; no decoder is run), "
        "Clifford errors inside the factory (the protocol assumes them rare), crosstalk from "
        "measuring one ion beside another, and throughput &mdash; the town runs one instruction "
        "at a time.</li></ul>")
    if beta:
        sc = beta["showcase"]["stats"]
        body.append(
            f'<h2 id="beta">Beta at scale: hardware only</h2><div class="beta"><p>The first version '
            f'of this tool composed bivariate-bicycle blocks at scale: '
            f'<a href="../showcase/">{sc["ions"]:,} ions in {sc["blocks"]} blocks of [[72,12,6]]</a>, '
            f'and a <a href="../demo/">four-block demo</a>. Their operations pass the hardware rules, '
            f"but their logic is <b>not</b> verified: the syndrome rounds are contact schedules "
            f"without H layers or CNOT orientation, a parity is taken once by one bare ancilla, "
            f"and the T-gate correction is tracked classically. They stay to show the scale the "
            f"hierarchy handles, not as correct programs.</p>"
            f"<p>The demo reports <b>G7 failed</b>, and that is the demo working: its programme asks "
            f"for a transversal H on a bivariate-bicycle block, which does not preserve that code's "
            f"stabilizers, so the synthesizer refuses the instruction &mdash; for the same reason "
            f"LogicQ's own checker refuses it &mdash; and G7 reports that one instruction of the "
            f"programme was never realised. A refused instruction is meant to be visible.</p></div>")
    body.append('<p class="muted" style="margin-top:28px"><a href="../plan/">The design document</a>'
                " &middot; every place in this library is checked twice, as hardware and as logic.</p>")
    return "".join(body)


def _fmt_time(ms: float) -> str:
    """A machine time, in the unit a reader thinks of it in."""
    if ms < 1000:
        return f"{ms:.1f} ms"
    if ms < 120_000:
        return f"{ms / 1000:.2f} s"
    if ms < 7_200_000:
        return f"{ms / 60_000:.1f} min"
    return f"{ms / 3_600_000:.2f} h"


def _computes(sem: dict) -> str:
    """What a run's gadget program was shown to compute, against its .prog."""
    if sem.get("passed") and sem.get("kind") == "distribution":
        return (f"the <code>.prog</code>'s results with their probabilities, and result by result "
                f"its state (fidelity {sem['worst_state_fidelity']:g})")
    if sem.get("passed") and sem.get("kind") == "outcome":
        return f"<code>{html.escape(sem['spec'])}</code> in every branch"
    if sem.get("passed"):
        return f"exactly {html.escape(sem['contract'])} in all {sem['branches']} branches"
    return f'<span class="bad">not shown: {html.escape(str(sem.get("why") or "failed"))}</span>'


def _stack_row(run: dict) -> str:
    r = run["result"]
    c, ck = r["concrete"], r["checks"]
    mr = ck.get("machine_rules")
    if mr:
        checks = f"{ck['places']['passed']} place checks, {mr['passed']}/{mr['total']} machine rules"
    else:
        checks = (f"{ck['places']['passed']} place checks in each of {ck.get('views') or 0:,} views, "
                  f"and their composition")
    return (f'<tr><td><a href="fullstack/{run["key"]}/"><b>{html.escape(run["title"])}</b></a><br>'
            f'<span class="muted">{html.escape(run["about"])}</span></td>'
            f'<td>{r["linked"]:,} of {r["qec_statements"]:,}</td>'
            f'<td>{c["instructions"]:,}</td>'
            f'<td>{_fmt_time(c["makespan_ms"])}</td>'
            f'<td>{_computes(r["contract"])}</td>'
            f'<td>{_pill(stack_ok(r))} {checks}</td></tr>')


def _counts(lib) -> dict:
    from collections import Counter
    top = lib[lib.top]
    fam = Counter(lib[i.master].family for i in top.instances)
    roads = sum(1 for ch in top.channels if getattr(ch, "kind", "road") != "wire")
    return {"places": sum(n for f, n in fam.items() if f not in ("road", "decoder", "archive")),
            "junctions": fam.get("road", 0), "roads": roads, "fam": fam}


def _landing(runs: dict, machines: dict, town_lib, algs: dict, beta: dict) -> str:
    """The section's front page: the full stack, its runs, its layers and its machine."""
    from .svg import design_svg

    body = [
        "<h1>The full stack</h1>",
        '<p class="sub">A fault-tolerant program on trapped ions, from the language it is written '
        "in to the ions that run it. Every layer is a program, every line of a layer is linked to "
        "the line it comes from, and every layer is checked.</p>",
    ]
    if runs:
        body.append(
            '<div class="tw"><table class="runs"><thead><tr><th>program</th>'
            '<th><code>.qec</code> lines linked</th><th>gadget instructions</th>'
            "<th>machine time</th><th>computes, against the <code>.prog</code></th><th>checks</th>"
            "</tr></thead><tbody>" + "".join(_stack_row(r) for r in runs.values())
            + "</tbody></table></div>")
        cx = (runs.get("cnot") or {}).get("result") or {}
        ab = cx.get("abstract") or {}
        if ab.get("runtime_ns"):
            body.append(
                f'<p class="muted" style="font-size:13px">FormalRV\'s own VM puts the CNOT at '
                f'{ab["runtime_ns"] / 1e6:.1f} ms on its abstract machine, where every qubit is '
                f'virtual; here it takes {_fmt_time(cx["concrete"]["makespan_ms"])}, with every '
                f'ion moved.</p>')
    else:
        body.append('<p class="muted">No run was built with this page: the FormalRV toolchain was '
                    "not available.</p>")
    body.append(
        "<p>Open a run. Its page is the design tool, playing the program on the machine, ion by "
        "ion. Pause anywhere and the <b>Stack</b> above the program names what is happening at "
        "that instant, one line per layer: the line of the algorithm, its Pauli-product "
        "measurement, the QEC statement, the gadget instruction, the Clifford step and the "
        "hardware instruction, each with its file and line. Click a line of any layer to jump to "
        "the first instruction that carries it.</p>")

    body.append(
        '<h2 id="layers">The layers</h2><ol class="steps">'
        "<li><b>The algorithm</b> (<code>.prog</code>), in FormalRV's language, with its "
        "specification: gates, loops, controlled gadgets, and the input its specification "
        "assumes.</li>"
        "<li><b>Pauli-product measurements</b> (<code>.qppm</code>) and <b>operations on code "
        "blocks</b> (<code>.qec</code>, the distance-3 surface code), made by FormalRV's own "
        "compiler at a pinned commit, unchanged. Every <code>.qec</code> statement is linked to "
        "the <code>.qppm</code> statement it comes from, and that to its <code>.prog</code> line; "
        "each link says how it was made.</li>"
        "<li><b>The gadget program</b>: QCCD lowers the <code>.qec</code> to instructions of the "
        "machine's places: prepare a block, measure a Pauli product at a bench, take a magic "
        "state from a factory or a rotation source, read out, decode. A FormalRV gadget becomes "
        "a gate, defined once for every shape it is called with. The gadget program is checked "
        "against the <code>.prog</code>: the same operation in every branch, or the same results "
        "with the same probabilities and, result by result, the same state.</li>"
        "<li><b>The schedule</b>: the program runs on a machine of places joined by roads and "
        "junctions. Blocks travel between places and rest in memories that keep correcting "
        "them. The machine's rules judge the schedule. A program too large to unroll is "
        "scheduled one gate at a time, once per context in which it is called, and checked "
        "view by view and as a composition.</li>"
        "<li><b>The hardware program</b> (TSIR): every instruction belongs to one gadget, either "
        "a place's verified op on its own device or a road's carry, compiled with its whole "
        "bundle onto its conveyor. Every one passes the same hardware "
        '<a href="../rules/">rules</a>, R1&ndash;R23. Devices that share DACs or a laser take '
        "turns.</li>"
        "<li><b>The ions</b>: the page draws each one where that program puts it, at the time on "
        "screen.</li></ol>")

    std, wide = machines.get("fullstack"), machines.get("fullstack_wide")
    if std is not None:
        a = _counts(std)
        b = _counts(wide) if wide is not None else None
        body.append(
            '<h2 id="machine">The machine</h2><p class="sub">The first three programs run on this '
            f'machine: {a["places"]} places for ions, {a["junctions"]} junctions and '
            f'{a["roads"]} roads, with a decoder and an archive on a control floor wired to every '
            "place that measures.</p>" + design_svg(std)
            + '<p class="muted" style="font-size:12.5px;margin:0">'
            + (f'FormalRV\'s Shor runs on a wider one: {b["places"]} places '
               f'({b["fam"].get("memory", 0)} memories, {b["fam"].get("ppm", 0)} benches, '
               f'{b["fam"].get("factory", 0)} factories), {b["junctions"]} junctions and '
               f'{b["roads"]} roads. ' if b else "")
            + 'How a machine is built and judged: <a href="plan/">the design</a>.</p>')

    body.append(
        '<h2 id="checked">What is checked, and what is not</h2><ul class="steps">'
        "<li>Checked: every link between two layers, by what both sides must share; what the "
        "gadget program computes, against the <code>.prog</code>; the machine's rules on the "
        "schedule; the hardware rules on every place op and on every road's own program; and "
        'every place, as hardware and as logic (<a href="places/">the places</a>).</li>'
        "<li>Not checked: logical error rates, since no noise is sampled; decoding, since the "
        "decoder and the archive are classical places with latencies, not run; and the fault "
        "distance of a whole run, which this stack does not require. The places that correct "
        "errors have distance 3; the Pauli-product bench, by design, does not.</li></ul>")

    leaves = _all_masters([town_lib, *machines.values()])
    ops = [op for m in leaves for op in m.ops.values()]
    rows = [
        ("places/", "The places", f"every gadget the machines are built from, each verified as "
         f"hardware and as logic: {len([m for m in leaves if m.family != 'road'])} places, "
         f"{len(ops)} ops"),
        ("algorithms/", "Logical Algorithm", f"{len(algs)} programs written directly as gadget "
         "instructions, on the town, each signed off end to end"),
        ("plan/", "The design", "the gadget system: places, roads and junctions, heat, the "
         "machine's rules, shared planes"),
    ]
    if beta:
        sc = beta["showcase"]["stats"]
        rows.append(("showcase/", "Beta at scale", f"{sc['ions']:,} ions in {sc['blocks']} "
                     "bivariate-bicycle blocks, and a demo: hardware only, logic not verified"))
    body.append('<h2 id="below">Underneath</h2><div class="tw"><table><tbody>' + "".join(
        f'<tr><td><a href="{u}">{html.escape(t)}</a></td><td class="muted">{d}</td></tr>'
        for u, t, d in rows) + "</tbody></table></div>")
    return "".join(body)


def _signed(res: dict) -> bool:
    """Signed off end to end: flat, or -- for a program too large to flatten -- in parts."""
    return bool(res["signoff"].get("passed")) or (
        res["signoff"].get("passed") is None and bool((res.get("compositional") or {}).get("passed")))


def _algo_card(stem: str, res: dict, *, prefix: str) -> str:
    alg, so, st, ck = res["algorithm"], res["signoff"], res["stats"], res["checks"]
    lines = []
    cs = res.get("compositional") or {}
    if so.get("passed") is None and cs.get("parts"):
        parts = cs["parts"]
        lines.append(f'<li class="muted">{len(alg.instructions)} instructions, '
                     f'{sum(1 for i in alg.instructions if i.kind == "inject")} magic states: '
                     f'signed off in parts, as a program this size must be</li>')
        g = parts.get("gates", {})
        if g.get("rows"):
            names = ", ".join(r["gate"] for r in g["rows"])
            lines.append(f'<li>{"✓" if g["passed"] else "✗"} every gate it calls is its contract in '
                         f'every branch: {html.escape(names)}</li>')
        c = parts.get("composition", {})
        lines.append(f'<li>{"✓" if c.get("passed") else "✗"} the floorplan and the schedule obey '
                     f'every machine rule ({len(c.get("held", []))})</li>')
        d = parts.get("semantics", {}).get("distribution") or []
        if d:
            lines.append('<li>✓ with every call as its contract, the outcomes: ' + html.escape("; ".join(
                " ".join(f"{k}={v}" for k, v in r["outcomes"].items()) + f" with probability {r['p']:g}"
                for r in d[:8])) + '</li>')
    for r in so.get("relations", []):
        lines.append(f'<li>{"✓" if r["ok"] else "✗"} {html.escape(r["relation"])}</li>')
    for f in so.get("flows", []):
        lines.append(f'<li>{"✓" if f["ok"] else "✗"} {html.escape(f["flow"])}</li>')
    d = so.get("distance", {})
    if d.get("faults"):
        lines.append(f'<li>{"✓" if d.get("passed") else "✗"} no one or two of '
                     f'{d["faults"]:,} fault locations flip a result unseen</li>')
    elif d.get("skipped"):
        lines.append(f'<li class="muted">distance: {html.escape(d["skipped"])}</li>')
    body = [l for l in alg.source.splitlines() if l.strip() and not l.startswith("title")]
    about = " ".join(l.lstrip("# ").strip() for l in body if l.lstrip().startswith("#"))
    src = "\n".join(l for l in body if not l.lstrip().startswith("#"))
    return (f'<div class="algo"><h3><a href="{prefix}{stem}/">{html.escape(alg.title)}</a> '
            f'{_pill(_signed(res), "signed off", "not signed off")}</h3>'
            + (f'<p class="muted" style="font-size:13px;margin:2px 0">{html.escape(about)}</p>'
               if about else "")
            + f'<pre>{html.escape(src)}</pre><ul>{"".join(lines)}</ul>'
            f'<div class="gstat"><div><b>{st["blocks"]}</b>blocks</div>'
            f'<div><b>{_fmt_ms(st["makespan_ms"] * 1000)}</b>machine time</div>'
            f'<div><b>{st["events"]}</b>gadget ops</div>'
            f'<div><b class="{"ok" if not (res.get("rules") or {}).get("failed") else "bad"}">'
            f'{len((res.get("rules") or {}).get("passed", []))}/{len((res.get("rules") or {}).get("rules", []))}</b>'
            f'machine rules</div></div></div>')


def _algorithms_page(lib, algs: dict) -> str:
    from .svg import design_svg

    body = [
        '<p class="sub"><a href="../">Gadgets</a> &rsaquo; Logical Algorithm</p>',
        "<h1>Logical Algorithm</h1>",
        '<p class="sub">A logical algorithm is a program over blocks. Each instruction names the '
        "kind of place it needs; the scheduler takes the blocks there along the roads of a "
        "floorplan -- the town below, unless the program names its own -- runs the place's "
        "verified op, and rests any block the next instruction does not need, in a logical zone or "
        "in a memory that corrects it while it waits. Instructions run as early as their blocks "
        "and places allow, so a factory produces while a lattice surgery runs elsewhere. Every "
        "schedule is judged by the machine's rules, and then signed off: flattened to one circuit "
        "over every ion, simulated once symbolically, and compared with the algorithm's ideal "
        "circuit; or, for a program too large to flatten (Shor's algorithm), in parts -- every "
        "place op verified, every gate it calls proved to be its contract in every branch, every "
        "machine rule holding, and the program's outcomes computed with each call as its "
        "contract.</p>",
        design_svg(lib),
        '<h2>The algorithms</h2><div class="cards">',
    ]
    for stem, res in algs.items():
        body.append(_algo_card(stem, res, prefix=""))
    body.append("</div>")
    body.append(
        "<h2>Write one</h2><p>One instruction per line; <code>#</code> starts a comment; any "
        "instruction may end in <code>@ name</code> to pin it to one instance of the design.</p>"
        '<div class="tw"><table><thead><tr><th>instruction</th><th>meaning</th><th>place</th></tr>'
        "</thead><tbody>"
        "<tr><td><code>prep q Z</code> &middot; <code>prep q X</code></td><td>a fresh block in |0̄⟩ or |+̄⟩</td><td>Logical Preparation</td></tr>"
        "<tr><td><code>se q 1</code> &middot; <code>se q 3</code></td><td>rounds of syndrome extraction</td><td>Syndrome Extraction</td></tr>"
        "<tr><td><code>cx c t</code></td><td>transversal CNOT, c controls</td><td>Logical Operation</td></tr>"
        "<tr><td><code>zz a b -&gt; m</code> &middot; <code>xx a b -&gt; m</code></td><td>measure Z̄Z̄ or X̄X̄ by lattice surgery</td><td>Lattice Surgery</td></tr>"
        "<tr><td><code>read q Z -&gt; m</code></td><td>destructive readout in Z or X</td><td>Logical Readout</td></tr>"
        "<tr><td><code>inject t</code></td><td>a block holding the factory's magic state</td><td>Magic State Factory, then Injection</td></tr>"
        "<tr><td><code>s q</code> &middot; <code>sx q</code></td><td>S̄ or √X̄, by consuming a |Ȳ⟩ block "
        "in a lattice surgery</td><td>Injection, Lattice Surgery, Readout</td></tr>"
        "<tr><td><code>x q</code> &middot; <code>y q</code> &middot; <code>z q</code></td><td>a logical "
        "Pauli, kept in the classical memory: no ion moves</td><td>Classical Memory</td></tr>"
        "<tr><td><code>store q</code></td><td>rest the block</td><td>Logical Zone or Logical Memory</td></tr>"
        "<tr><td><code>decode q</code> &middot; <code>decode</code></td><td>call the decoder: the "
        "syndromes the block left (or every one waiting) go down their wires to it, and the "
        "frame it decides to the classical memory. An outcome is a result only once decoded, "
        "and a program must decode everything it measured</td><td>Decoder</td></tr>"
        "</tbody></table></div>"
        "<p>Any instruction may be <b>guarded</b>, <code>if m: s q</code>, to run only when a "
        "measured outcome says so (<code>m ^ k</code> and <code>m &amp; k</code> combine outcomes). "
        "A program may define <b>gates</b> from these instructions and call them like instructions:"
        "</p><pre>gate t q implements T:\n  inject m\n  x m\n  zz q m -&gt; a\n  read m X -&gt; b\n"
        "  decode q m\n  if a: s q\n  if b: z q</pre>"
        "<p><code>include \"standard\"</code> brings the standard ones -- <code>sdg</code>, "
        "<code>h</code>, <code>t</code>, <code>tdg</code>, <code>cz</code>, <code>ccx</code>, "
        "<code>cswap</code> -- each proved to be the gate it says it implements, and "
        "<code>floorplan \"shor\"</code> names the machine a program runs on. In the design "
        "page, <b>Edit</b> composes places into a new design and <b>Export floorplan</b> writes "
        "it as a floorplan of your own.</p>")
    return "".join(body)


def standalone(out: Path, index_json: str | None = None, *, cache_dir="out/gadgets/cache",
               log=print, fullstack: str = "all") -> dict:
    """Build only this section into `out/gadgets/`, wrapped exactly as the site wraps it.

    `index_json` is the search index every page carries; give the live site's (plus
    `index_entries()`) so search from these pages still finds the rest of the site.
    """
    from ..site.build import FOOTER, with_nav

    out = Path(out)
    idx = index_json if index_json is not None else json.dumps(index_entries())
    idx = idx.replace("</", "<\\/")

    def put(rel: str, page: str, depth: int, active, app: bool = False, extra: str = "") -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        page = with_nav(page, depth, active, idx, app=app, extra=extra)
        page = page.replace("__FOOTER__", FOOTER).replace("__SITEROOT__", "../" * depth)
        p.write_text(page, encoding="utf-8", newline="")

    return build_section(out, put, cache_dir=cache_dir, log=log, fullstack=fullstack)


# -- figures ---------------------------------------------------------------------------------

#: the program a bare place is shown in: the shortest example on the town, which holds every place
PLACE_PROGRAM = "t_gate"
_EMBED_LEAVES: dict = {}


def _leaves(cache_dir, log):
    from .library import LeafLibrary
    key = str(Path(cache_dir).resolve())
    if key not in _EMBED_LEAVES:
        _EMBED_LEAVES[key] = LeafLibrary(cache_dir, log=log or (lambda *a, **k: None))
    return _EMBED_LEAVES[key]


def export_place_arch(m: str, dst: Path, *, cache_dir="out/gadgets/cache", log=None) -> Path:
    """Write the device of place `m` -- the architecture document its ops are compiled for and
    verified on -- to `dst`, as the studio loads any device.  Its `name` is the place's own
    ("msf15", "ls_d3"): unique among the places, and none is the name of another device the
    site ships."""
    from .leaves.places import PLACES
    if m not in PLACES:
        raise KeyError(f"no place {m!r} with a device: {', '.join(sorted(PLACES))}")
    leaves = _leaves(cache_dir, log)
    leaves.place(m)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(leaves.data[m]["arch"], indent=1) + "\n", encoding="utf-8")
    return dst


def embed_page(name: str, dst: Path, *, focus: str | None = None,
               cache_dir="out/gadgets/cache", log=None) -> Path:
    """Write ONE self-contained page for `name` to `dst`: every script and all its data inline,
    nothing fetched, no navigation of the page that holds it -- a figure the tutorial frames.

        "algorithms/<stem>"   the design page running that example (examples/gadgets/algorithms)
        "<place>"             a place by its kind ("msf15", "mem_d3"): the town, that place selected
        "<place>.<op>"        one op of a place on the studio's stage ("msf15.produce")

    The design pages carry `window.GADGET = {ready(), focus(id), seek(us)}`: `focus` takes a
    place by its path or kind, or an instruction ("i5", or its line "L13"); the same two come
    in the address as `#f=ID&t=US`.  `focus` here is what the page shows first.
    """
    import shutil
    import tempfile

    from .leaves.classical import CLASSICAL
    from .leaves.places import PLACES
    from .verified import build_algorithm

    log = log or (lambda *a, **k: None)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    leaves = _leaves(cache_dir, log)

    stem = name[len("algorithms/"):] if name.startswith("algorithms/") else None
    if stem is None and (name in PLACES or name in CLASSICAL):
        stem, focus = PLACE_PROGRAM, focus or name
    if stem is not None:
        src = ALGORITHMS / f"{stem}.alg"
        if not src.exists():
            raise KeyError(f"no example algorithm {stem!r}: "
                           + ", ".join(sorted(f.stem for f in ALGORITHMS.glob("*.alg"))))
        with tempfile.TemporaryDirectory(prefix="gadget-embed-") as tmp:
            build_algorithm(src, out_dir=tmp, leaves=leaves, log=log, inline=True, focus=focus)
            shutil.copyfile(Path(tmp) / "index.html", dst)
        return dst

    master, _, op = name.partition(".")
    if master in PLACES and op:
        from ..arch.device import Architecture
        from ..cost.models import corrected_model
        from ..ir.tsir import TSIR
        from ..verify import verify
        from ..viz import render_html
        m = leaves.place(master)
        data = leaves.data[master]
        if op not in data["programs"]:
            raise KeyError(f"{master} has no op {op!r}: " + ", ".join(sorted(data["programs"])))
        arch = Architecture.from_json(data["arch"])
        prog = TSIR.from_json(data["programs"][op])
        model = corrected_model("qccdsim_jones")
        res = verify(prog, arch, model, check_metrics=False).result
        render_html(arch, prog, res, model, dst, kicker="logical gadget",
                    headline=f"{master} · {op}", lede=m.ops[op].title if op in m.ops else op)
        return dst
    raise KeyError(f"no gadget page {name!r}: 'algorithms/<example>', a place "
                   f"({', '.join(sorted({*PLACES, *CLASSICAL}))}), or '<place>.<op>'")
