"""`python -m qccd` -- the toolchain front end.

A programmable design tool for trapped-ion QCCD: describe a device declaratively, load or
generate a program for it, replay and rule-check it, get a resource report, and render the
whole thing to one self-contained HTML page.

    python -m qccd devices                     what architectures exist, and their cost
    python -m qccd show ring144_24v            one device in detail
    python -m qccd arch ring144_24v            the ARCHITECTURE as a program you can read
    python -m qccd listing ring144_24v --program deck --depth 0
                                               the HARDWARE PROGRAM, disassembled
    python -m qccd run ring144_24v --html out/ring.html
    python -m qccd demo                        every device, every program, one index page

The device / program / verify / report / view split is deliberate: a device description is
independent of the program, a program is independent of the cost model, and the verifier
judges both. Swapping any one of the three is a flag, not a code change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.api import DeviceBuilder, Machine  # noqa: E402
from qccd.arch import Architecture, load  # noqa: E402
from qccd.compile import CoolingPolicy, build, insert_cooling  # noqa: E402
from qccd.cost import corrected_model, deck_model, t1_metrics, t2_metrics  # noqa: E402
from qccd.cost.hardware import hardware_report  # noqa: E402
from qccd.ir import TSIR  # noqa: E402
from qccd.arch.listing import architecture_listing, round_trip_check  # noqa: E402
from qccd.ir.listing import disassemble, quanta_trace, render  # noqa: E402
from qccd.verify.control import control_trace  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402
from qccd.verify.replay import ReplayError  # noqa: E402
from qccd.viz import render_html  # noqa: E402

ARCH_DIR = ROOT / "arch"
OUT = ROOT / "out"
HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"


# --------------------------------------------------------------------------- programs
#
# All program construction lives in `qccd.compile.programs` so the CLI, the examples,
# the tests and the compiler build movement the same way. Two implementations of "rotate
# the loop" is how a platform starts reporting two numbers for the same thing.


def program_for(arch: Architecture, kind: str, spec: str = "") -> TSIR:
    """Build a program by name.  See `qccd.compile.programs` for what each one is."""
    if kind == "deck":
        return build(arch, "deck", html_path=HTML)
    k = int(spec) if spec else None
    if kind == "walk":
        return build(arch, "walk", k or 4)
    return build(arch, kind, k)


# --------------------------------------------------------------------------- commands


def cmd_devices(args) -> int:
    print(f"{'device':22s} {'generator':11s} {'nodes':>6s} {'traps':>6s} {'junc':>5s} "
          f"{'corner':>6s} {'ions':>6s} {'DACs':>6s} {'electrodes':>10s}  wiring")
    print("-" * 104)
    for p in sorted(ARCH_DIR.glob("*.arch.json")):
        arch = load(p)
        s = arch.device.summary()
        hw = hardware_report(arch)
        print(f"{arch.name:22s} {s['generator']:11s} {s['n_nodes']:6d} {hw.n_traps:6d} "
              f"{s['n_junction_nodes']:5d} {s['n_corners']:6d} {hw.total_capacity:6d} "
              f"{hw.dacs:6d} {hw.electrodes:10d}  {hw.scheme}")
    print()
    print("DAC count is the headline: a broadcast scheme keeps it flat in array size,")
    print("a direct scheme pays one per electrode.  `show <device>` for the breakdown.")
    return 0


def cmd_show(args) -> int:
    arch = _load(args.device)
    dev = arch.device
    s = dev.summary()
    hw = hardware_report(arch)
    print(f"{arch.name}")
    if arch.description:
        print(f"  {arch.description}")
    print()
    print(f"  generator        {s['generator']}{dict(s['params'])}")
    print(f"  nodes            {s['n_nodes']}  ({s['n_sites']} sites)")
    print(f"  segments         {s['n_segments']}")
    print(f"  loops            {s['n_loops']}  {list(dev.loops)}")
    print(f"  degree histogram {s['degree_histogram']}")
    print(f"  junctions (>=3)  {s['n_junction_nodes']}")
    print(f"  corners / bends  {s['n_corners']} / {s['n_bends']}")
    print(f"  ion capacity     {hw.total_capacity}")
    print()
    print("  hardware")
    for k in ("scheme", "electrodes", "switches", "dacs", "dacs_broadcast",
              "dacs_compensation"):
        print(f"    {k:20s} {getattr(hw, k)}")
    print(f"    {'dacs per trap':20s} {hw.dacs_per_trap:.4f}")
    for n in hw.notes:
        print(f"    - {n}")
    if hw.over_budget:
        print("    OVER BUDGET:", "; ".join(hw.over_budget))
    print()
    print("  primitive tables ", list(arch.primitives.tables()))
    print("  simd classes     ", list(arch.simd_classes))
    return 0


def cmd_arch(args) -> int:
    """Print the architecture as the Python program that rebuilds it."""
    arch = _load(args.device)
    model = deck_model() if args.model == "deck" else corrected_model(args.table)
    listing = architecture_listing(
        arch, mode=args.mode, policy=None if args.no_policy
        else getattr(model, "policy", None), verify=args.verify,
        max_channel_lines=args.max_channels)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(listing.to_json(), indent=1, default=str),
                                   encoding="utf-8")
        print(f"wrote {args.json}")
        return 0
    if args.python:
        print(listing.python())
    else:
        print(listing.text(notes=not args.no_notes, numbers=args.numbers,
                           width=args.width))
    if args.verify:
        ok = listing.round_trip
        print(f"# round-trip {'verified' if ok else 'FAILED'}"
              + ("" if ok else ": " + "; ".join(listing.round_trip_diff[:4])))
        return 0 if ok else 1
    if args.at:
        rows = listing.lines_for(args.at)
        print(f"# {args.at} is declared or referenced at record(s) "
              + (", ".join(str(r) for r in rows) if rows else "(none)"))
    return 0


def cmd_run(args) -> int:
    arch = _load(args.device)
    # A `.tsir.json` written by the browser, or one of the built-in builders.  Without
    # this the escape hatch the design tool prints -- "download both and run this" -- is a
    # promise the CLI cannot keep: `run` had no way to load a program from a file at all.
    prog = (TSIR.load(args.tsir) if getattr(args, "tsir", None)
            else program_for(arch, args.program, args.k or ""))
    model = deck_model() if args.model == "deck" else corrected_model(args.table)

    if args.cool and model.models_heating:
        result = insert_cooling(prog, arch, model, policy=CoolingPolicy())
        prog = result.program
        print(f"cooling: inserted {result.n_cools} global operations, "
              f"{result.cooling_us / 1000:.1f} ms, R7 "
              f"{result.r7_violations_before} -> {result.r7_violations_after}")

    try:
        report = verify(prog, arch, model)
    except ReplayError as exc:
        print(f"{arch.name}  x  {prog.name}: the replay stopped")
        print(f"  {exc}")
        return 1
    res = report.result
    t1 = t1_metrics(prog, arch, res)
    rules = report.rules.summary()

    print(f"{arch.name}  x  {prog.name}   [{model.name} model]")
    print(f"  instructions   {len(prog)}   cycles {len(res.cycles)}")
    print(f"  total_cost     {res.total_cost:,.0f}")
    print(f"  total_steps    {res.total_steps:,}")
    if model.models_time:
        t2 = t2_metrics(arch, res, model)
        print(f"  runtime        {res.total_us / 1000:,.2f} ms")
        print(f"  quanta/ion     {t2.quanta_per_data_ion_total:,.1f}  "
              f"peak {res.peak_quanta:,.1f}")
        if t2.cooling_us:
            print(f"  cooling        {t2.cooling_us / 1000:,.2f} ms "
                  f"({100 * t2.cooling_us / res.total_us:.1f}%)")
    print(f"  contacts       {res.n_gate_pairs}")
    if t1.contact_batch_limit:
        print(f"  batch util     {t1.contact_batch_utilization:.2f} / "
              f"{t1.contact_batch_limit}")
    print(f"  templates      {t1.n_movement_templates}  {t1.movement_templates}")
    print(f"  rules passed   {' '.join(rules['passed']) or '(none)'}")
    print(f"  rules failed   {' '.join(rules['failed']) or '(none)'}")
    if rules["failed"]:
        for v in report.rules.violations[:3]:
            print(f"    {v}")

    if args.html:
        path = render_html(arch, prog, res, model, args.html,
                           max_frames=args.max_frames)
        print(f"  wrote          {path}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(report.summary(), indent=2, default=str), encoding="utf-8")
        print(f"  wrote          {args.json}")
    return 0 if report.ok() else 1


def cmd_listing(args) -> int:
    """Disassemble the hardware program: one line per instruction, folded by round."""
    arch = _load(args.device)
    prog = program_for(arch, args.program, args.k or "")
    model = deck_model() if args.model == "deck" else corrected_model(args.table)
    if args.cool and model.models_heating:
        prog = insert_cooling(prog, arch, model, policy=CoolingPolicy()).program

    res = quanta = None
    if not args.claims:
        res = verify(prog, arch, model, check_metrics=False).result
        if model.models_heating:
            quanta = quanta_trace(prog, arch, model)

    ctl = None
    if args.control:
        trace = control_trace(prog, arch, model)
        ctl = trace.by_id()
        print(f"control: {len(trace.records)} instructions -> "
              f"{len(trace.table)} distinct control states"
              + (f"; hops varied on {len(trace.varies)}" if trace.varies else ""))
        for r in trace.table:
            print(f"  {r.type:8s} {str(r.cls or '-'):12s} {r.driver:11s} "
                  f"{r.action:14s} {r.channels_engaged:4d}/{r.channels_total:<5d} ch  "
                  f"{r.sites_acting:4d}/{r.sites_total:<4d} moving  "
                  f"{r.sites_held:4d} held  "
                  f"{100 * r.duty:5.1f}% duty  "
                  f"{'drivable' if r.feasible else ('NOT DRIVABLE' if r.feasible is False else 'not judged')}")
        print()

    listing = disassemble(prog, arch, res=res, model=model, quanta=quanta,
                          fold=args.fold, with_control=args.control, control=ctl)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(listing.to_json(), indent=1,
                                              default=str), encoding="utf-8")
        print(f"wrote {args.json}")
        return 0
    print(render(listing, depth=args.depth, cursor_id=args.at,
                 limit=args.limit, header=not args.no_header))
    return 0


DEMO = [
    ("ring144_24v", "deck", "corrected", True,
     "the shipped 24-ancilla schedule under the corrected physics, with cooling"),
    ("ring144_24v", "deck", "deck", False,
     "the same schedule under the deck's own model -- reproduces 397184 / 8808"),
    ("cyclone_base", "rotate", "corrected", False,
     "base Cyclone: rigid rotation, no junction on the loop"),
    ("cyclone_base", "oddeven", "corrected", False,
     "the same realignment by odd-even sort, for contrast"),
    ("h2_racetrack", "rotate", "corrected", False,
     "Quantinuum H2: a linear trap with periodic boundary conditions"),
    ("ladder_2x72", "walk", "corrected", False,
     "the deck's rails-and-highways ladder (p.5): rungs are the computing region"),
    ("cyclone_dual_loop", "rotate", "corrected", False,
     "deck p.12: data loop still, ancilla loop rotating"),
    ("grid9x9", "walk", "corrected", False,
     "baseline grid QCCD: traps on the wires, X-junctions at the lattice points"),
    ("deck_unit_cell", "walk", "corrected", False,
     "the deck's own unit-cell array -- same geometry as grid9x9, broadcast wiring"),
    ("stationary_chain", "walk", "corrected", False,
     "one trap, no transport: the breakeven baseline"),
]


def cmd_verify(args) -> int:
    """The hand-checkable examples: small enough to confirm by counting on screen."""
    sys.path.insert(0, str(ROOT / "examples"))
    import verifiable_examples

    return verifiable_examples.main()


def cmd_studio(args) -> int:
    """`qccd studio` -- the design tool, as ONE self-contained page.

    Not a second page kind.  `render_html` is already a renderer over `(arch, prog)`, and
    `(empty arch, empty prog)` became a legal pair the moment `Architecture.from_json`
    started testing for the PRESENCE of `nodes` rather than its truthiness.  What the
    studio changes is two PARAMETERS: it starts from an empty explicit device, and it
    carries every `arch/*.arch.json` as a template so the start gallery has nine physics
    packages to offer rather than a pair.
    """
    model = deck_model() if args.model == "deck" else corrected_model(args.table)
    if args.seed:
        arch = _load(args.seed)
        prog = program_for(arch, args.program, args.k or "") if args.program else \
            TSIR(name="empty", arch_spec=str(args.seed))
    else:
        # A REAL control block, not an empty one.  `Machine.blank_device` declares
        # `primitives: {}` and `control: {}`, and a device with no `shuttle_segment` curve
        # cannot be priced at all -- the first `run()` dies with
        # `KeyError: architecture declares no 'shuttle_segment' curve`.  The empty canvas
        # therefore seeds from a TEMPLATE, so capability 4 is reachable from capability 1.
        m = Machine.from_device(DeviceBuilder("explicit").build(),
                                name=args.name, template=args.template)
        arch = m.arch
        prog = TSIR(name="empty", arch_spec=args.name)
    report = verify(prog, arch, model)
    path = render_html(arch, prog, report.result, model, args.out,
                       kicker="QCCD STUDIO",
                       headline=f"{arch.name} - design",
                       lede="an empty canvas: build a device, write a test programme, "
                            "and see what can and cannot be checked here",
                       template_stems="*" if args.all_templates else None,
                       max_frames=args.max_frames)
    print(f"wrote {path}  ({path.stat().st_size:,} bytes, "
          f"{len(arch.device.nodes)} nodes, {len(prog)} instructions)")
    return 0


def cmd_open(args) -> int:
    """`qccd open` -- THE RETURN LEG.

    A design leaves for the browser as `<name>.arch.json` (+ `<name>.tsir.json`); this
    brings it back, runs the FULL 23-rule verifier over it, and prints the verdicts the
    page said it could not compute.  Without it `qccd studio` is a one-way door and the
    Report pane's grey "not checked here" register never clears.
    """
    src = Path(args.file)
    doc = json.loads(src.read_text(encoding="utf-8"))
    prog_path = args.tsir
    studio_calls = None
    if str(doc.get("kind", "")) == "qccd.studio":
        # a studio artifact: the architecture and the programme travel together, and the
        # programme travels as RECORDS rather than as compiled TSIR -- so re-running it
        # here goes through `Program.apply_calls`, the whitelist dispatcher, and exercises
        # the same authoring verbs the browser used rather than a second reader.
        arch = Architecture.from_json(doc["arch"])
        studio_calls = (doc.get("program") or {}).get("calls") or None
    else:
        arch = Architecture.from_json(doc)
    model = deck_model() if args.model == "deck" else corrected_model(args.table)
    if prog_path:
        prog = TSIR.load(prog_path)
    elif studio_calls:
        m = Machine(arch)
        prog = m.program(src.stem, provenance="off").apply_calls(studio_calls).build()
    else:
        prog = program_for(arch, args.program, args.k or "")
    # A programme a HUMAN wrote can be unexecutable, not merely illegal -- an ion declared
    # at a site it is not on stops the replay dead.  Print it as the refusal it is; a
    # traceback is not a diagnosis, and this is the return leg of a design tool.
    try:
        report = verify(prog, arch, model)
    except ReplayError as exc:
        print(f"{arch.name}  x  {prog.name}: the replay stopped")
        print(f"  {exc}")
        return 1
    rules = report.rules.summary()
    print(f"{arch.name}  x  {prog.name}   [{model.name} model]")
    print(f"  nodes {len(arch.device.nodes)}  segments {len(arch.device.segments)}  "
          f"loops {len(arch.device.loops)}")
    print(f"  instructions   {len(prog)}   cycles {len(report.result.cycles)}")
    print(f"  total_cost     {report.result.total_cost:,.0f}")
    print(f"  total_steps    {report.result.total_steps:,}")
    print(f"  runtime        {report.result.total_us / 1000:,.2f} ms")
    print(f"  rules passed   {' '.join(rules['passed']) or '(none)'}")
    print(f"  rules failed   {' '.join(rules['failed']) or '(none)'}")
    print(f"  rules skipped  {' '.join(sorted(rules['skipped'])) or '(none)'}")
    for v in report.rules.violations[:10]:
        print(f"    {v}")
    if args.html:
        path = render_html(arch, prog, report.result, model, args.html,
                           max_frames=args.max_frames)
        print(f"  wrote          {path}")
    return 0 if report.ok() else 1


def cmd_reach(args) -> int:
    """What the machine can do, before anyone writes a programme for it."""
    from .analysis import reach_report

    arch = load(ARCH_DIR / f"{args.device}.arch.json")
    model = deck_model() if args.model == "deck" else corrected_model()
    r = reach_report(arch, model, metric=args.metric)
    print(f"{arch.name}  [{r.model} model, distances in {r.metric}]")
    print(f"  {r.n_sites} traps   R7 gate budget {r.budget:g} quanta")
    if r.diameter_pair:
        print(f"  widest separation between gate-capable traps: {r.diameter:.3f} "
              f"({r.diameter_pair[0]} to {r.diameter_pair[1]})")
    worst = sorted(((v["cool"][0], k, v["cool"][1]) for k, v in r.nearest.items()),
                   reverse=True)[:6]
    print("  furthest from a cooler:")
    for d, site, via in worst:
        d_s = "unreachable" if d == float("inf") else f"{d:.3f}"
        print(f"    {site:<12} {d_s:>12}   nearest {via or '-'}")
    if r.stranded:
        print(f"  STRANDED ({len(r.stranded)}): {', '.join(r.stranded[:12])}"
              + (" ..." if len(r.stranded) > 12 else ""))
    for n in r.notes:
        print(f"  {n}")
    if args.json:
        import json as _json
        Path(args.json).write_text(_json.dumps(r.as_dict(), indent=1), encoding="utf-8")
        print(f"  wrote {args.json}")
    return 0


def cmd_analyses(args) -> int:
    """What the tool can run, and what each one's knobs are."""
    from .analysis import ANALYSES

    for key in sorted(ANALYSES):
        d = ANALYSES[key].describe()
        print(f"{key}  ({d['name']})")
        print(f"  {d['summary']}")
        print(f"  knobs:   {', '.join(_flat_keys(d['setup']))}")
        print(f"  outputs: {', '.join(d['data_labels'])}")
        print()
    return 0


def _flat_keys(setup, prefix=""):
    out = []
    for k, v in setup.items():
        if isinstance(v, dict) and v:
            out += _flat_keys(v, f"{prefix}{k}.")
        else:
            out.append(f"{prefix}{k}={v!r}" if not prefix else f"{prefix}{k}")
    return out


def _parse_values(raw: str):
    """`0:1:0.25` is a range; otherwise a comma list. Numbers where they parse."""
    def num(x):
        try:
            return int(x) if x.strip().lstrip("-").isdigit() else float(x)
        except ValueError:
            return x.strip()

    if ":" in raw:
        lo, hi, step = (float(x) for x in raw.split(":"))
        n = int(round((hi - lo) / step))
        # ROUND TO THE STEP'S OWN PRECISION. `0 + 3*0.2` is 0.6000000000000001, which is
        # the correct float and a terrible axis label; the sweep key is printed verbatim.
        places = max(len(x.split(".")[1]) if "." in x else 0 for x in raw.split(":"))
        return [round(lo + i * step, places + 2) for i in range(n + 1)]
    return [num(x) for x in raw.split(",")]


def cmd_sweep(args) -> int:
    """Vary one knob and print the curve -- the question an architect actually asks."""
    from .analysis import get_analysis

    cls = get_analysis(args.analysis)
    setup = {"device": str(ARCH_DIR / f"{args.device}.arch.json")}
    if args.model:
        setup["model"] = args.model
    for pair in args.set or ():
        k, _, v = pair.partition("=")
        node = setup
        parts = k.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _parse_values(v)[0]
    a = cls(**setup)

    values = _parse_values(args.values)
    r = a.sweep(args.key, values)
    print(f"{args.analysis} on {args.device}: {args.key} over "
          f"{len(values)} settings")
    print(r.table())
    for f in r.failures:
        print(f"  {args.key}={f.value!r} failed: {f.error}")
    if args.json:
        import json as _json
        Path(args.json).write_text(
            _json.dumps(r.as_dict(), indent=1, default=str), encoding="utf-8")
        print(f"  wrote {args.json}")
    return 0


def cmd_regen(args) -> int:
    """Every emitted page, in one command.

    There are three page-producing entry points -- `demo`, `verify` and `studio` -- and a
    change to the shared template or to `editor.js` invalidates all of them at once. Every
    time I regenerated two of the three, a test that reads the third failed and looked
    like a defect: three separate false alarms in one afternoon, each costing more than
    this command did. `test_the_page_inlines_this_exact_engine` compares the shipped
    bytes precisely so that staleness cannot hide, which makes it exactly the test that
    goes red when you forget one.
    """
    rc = cmd_demo(args)
    if rc:
        return rc
    rc = cmd_verify(args)
    if rc:
        return rc
    # THE STUDIO'S OWN DEFAULTS, FROM THE STUDIO'S OWN PARSER. This used to restate them
    # in a literal list, and two were wrong: `all_templates` became None where the parser
    # says True, and `table` became None where the parser says 'qccdsim_jones'. So the
    # command whose entire job is "no page is left stale or divergent" was itself emitting
    # a studio 49 KB smaller than `qccd studio` -- a different start gallery and a
    # different cost model -- and the page it wrote is the one the tests read.
    ns = build_parser().parse_args(
        ["studio", "-o", str(OUT / "studio.html"),
         "--max-frames", str(getattr(args, "max_frames", 20000))])
    return cmd_studio(ns)


def cmd_demo(args) -> int:
    OUT.mkdir(exist_ok=True)
    # Filenames are content-derived, so renaming a device or a model orphans its page
    # next to the new one and the index would link one and not the other -- hence the
    # sweep. But it must only sweep WHAT THIS COMMAND OWNS: an unconditional glob also
    # deleted `out/studio.html`, so `qccd demo` silently threw away the design tool and
    # every later `studio` run looked like it was needed for some other reason.
    mine = {f"{d}__{k}__{m}.html" for d, k, m, _, _ in DEMO} | {"index.html"}
    for stale in OUT.glob("*.html"):
        if stale.name in mine or "__" in stale.name:
            stale.unlink()
    rows = []
    for device, kind, model_name, cool, blurb in DEMO:
        path = ARCH_DIR / f"{device}.arch.json"
        if not path.exists():
            print(f"  skip {device}: no {path.name}")
            continue
        arch = load(path)
        try:
            prog = program_for(arch, kind, "")
        except SystemExit as exc:
            print(f"  skip {device}/{kind}: {exc}")
            continue
        model = deck_model() if model_name == "deck" else corrected_model()
        note = ""
        if cool and model.models_heating:
            r = insert_cooling(prog, arch, model, policy=CoolingPolicy())
            prog, note = r.program, f"{r.n_cools} cools, {r.cooling_us / 1000:.0f} ms"
        # verify() rather than replay(): replay leaves Ledger.checked empty, so
        # summary()['passed'] came back [] and every page printed an empty Rules panel
        res = verify(prog, arch, model, check_metrics=False).result
        out = OUT / f"{device}__{kind}__{model_name}.html"
        render_html(arch, prog, res, model, out, max_frames=args.max_frames)
        rules = res.rules.summary()
        rows.append({
            "device": device, "program": prog.name, "model": model_name,
            "blurb": blurb, "file": out.name, "note": note,
            "nodes": arch.device.summary()["n_nodes"],
            "junctions": arch.device.summary()["n_junction_nodes"],
            "dacs": hardware_report(arch).dacs,
            "cost": res.total_cost, "steps": res.total_steps,
            "ms": res.total_us / 1000.0,
            "failed": rules["failed"],
        })
        print(f"  {device:20s} {kind:8s} {model_name:10s} -> {out.name}"
              + (f"   ({note})" if note else "")
              + (f"   RULES FAILED: {rules['failed']}" if rules["failed"] else ""))
    _write_index(rows)
    print()
    print(f"open {OUT / 'index.html'}")
    return 0


def _write_index(rows: list[dict]) -> None:
    cells = "".join(
        f"<a class='card' href='{r['file']}'><h3>{r['device']}</h3>"
        f"<p>{r['blurb']}</p>"
        f"<table>"
        f"<tr><td>program</td><td><code>{r['program']}</code></td></tr>"
        f"<tr><td>model</td><td>{r['model']}</td></tr>"
        f"<tr><td>nodes / junctions</td><td>{r['nodes']} / {r['junctions']}</td></tr>"
        f"<tr><td>DACs</td><td>{r['dacs']}</td></tr>"
        f"<tr><td>cost / steps</td><td>{r['cost']:,.0f} / {r['steps']:,}</td></tr>"
        f"<tr><td>runtime</td><td>{r['ms']:,.2f} ms</td></tr>"
        + (f"<tr><td>cooling</td><td>{r['note']}</td></tr>" if r["note"] else "")
        + (f"<tr><td>rules</td><td class='bad'>{' '.join(r['failed'])} failed</td></tr>"
           if r["failed"] else "<tr><td>rules</td><td class='ok'>all pass</td></tr>")
        + "</table></a>"
        for r in rows
    )
    (OUT / "index.html").write_text(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>QCCD design tool - demo</title><style>
body{{margin:0;background:#0f1420;color:#e6ecf7;
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px}}
h1{{margin:0 0 4px;font-size:24px}} .sub{{color:#8b9ab5;margin-bottom:22px;max-width:70ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.card{{display:block;background:#161d2c;border:1px solid #2a3550;border-radius:10px;
padding:14px;text-decoration:none;color:inherit}}
.card:hover{{border-color:#5b7cfa}}
.card h3{{margin:0 0 4px;font-size:15px;color:#5b7cfa}}
.card p{{margin:0 0 10px;color:#8b9ab5;font-size:12.5px;min-height:34px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
td{{padding:2px 0;border-bottom:1px solid #1e2739}}
td:last-child{{text-align:right;color:#8b9ab5;font-variant-numeric:tabular-nums}}
code{{background:#0b0f18;padding:1px 5px;border-radius:4px}}
.ok{{color:#3fb950!important}} .bad{{color:#ff6b6e!important}}
</style></head><body><main>
<h1>QCCD design tool</h1>
<div class="sub">One declarative device description, one control IR, one verifier, one
renderer. Each card below is the same toolchain pointed at a different architecture &mdash;
nothing about the geometry is special-cased. Click one to step through its program with
per-ion heating, or run <code>python -m qccd devices</code> for the resource table.</div>
<div class="grid">{cells}</div></main></body></html>""",
        encoding="utf-8",
    )


def _load(name: str) -> Architecture:
    p = Path(name)
    if not p.exists():
        p = ARCH_DIR / (name if name.endswith(".arch.json") else f"{name}.arch.json")
    if not p.exists():
        avail = ", ".join(sorted(x.stem.replace(".arch", "")
                                 for x in ARCH_DIR.glob("*.arch.json")))
        raise SystemExit(f"no such device {name!r}; have: {avail}")
    return load(p)


def build_parser() -> argparse.ArgumentParser:
    """The parser, as a value.

    `cmd_regen` needs a studio invocation identical to the one `qccd studio` builds, and
    the only way to be sure of that is to ASK THE PARSER rather than to restate its
    defaults by hand -- which is what it used to do, and it got two of them wrong.
    """
    ap = argparse.ArgumentParser(prog="qccd", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="list architectures with their resource cost")

    p_show = sub.add_parser("show", help="one device in detail")
    p_show.add_argument("device")

    p_arch = sub.add_parser("arch", aliases=["source"],
                            help="the architecture as the program that rebuilds it")
    p_arch.add_argument("device")
    p_arch.add_argument("--mode", default="full",
                        choices=["auto", "full", "generator", "patch", "explicit"],
                        help="'full' is self-contained; 'auto' emits against a template")
    p_arch.add_argument("--python", action="store_true",
                        help="only the executable statements, no annotations")
    p_arch.add_argument("--no-notes", action="store_true")
    p_arch.add_argument("--numbers", action="store_true", help="number physical lines")
    p_arch.add_argument("--width", type=int, default=100,
                        help="column the derived annotations start at")
    p_arch.add_argument("--model", default="corrected", choices=["deck", "corrected"])
    p_arch.add_argument("--table", default="qccdsim_jones")
    p_arch.add_argument("--no-policy", action="store_true",
                        help="do not resolve each curve to the point the model picks")
    p_arch.add_argument("--max-channels", type=int, default=24,
                        help="summarize a role with more distinct memberships than this")
    p_arch.add_argument("--verify", action="store_true",
                        help="exec the emitted Python and diff it against the original")
    p_arch.add_argument("--at", default=None, metavar="ID",
                        help="report which records declare or mention this object id")
    p_arch.add_argument("--json", default=None)

    p_run = sub.add_parser("run", help="replay a program on a device and report")
    p_run.add_argument("device")
    p_run.add_argument("--tsir", default=None, metavar="PATH",
                       help="a .tsir.json (e.g. one the browser exported) to run "
                            "instead of a built-in program")
    p_run.add_argument("--program", default="rotate",
                       choices=["deck", "rotate", "oddeven", "walk"])
    p_run.add_argument("--k", default="", help="shift amount for rotate/oddeven")
    p_run.add_argument("--model", default="corrected", choices=["deck", "corrected"])
    p_run.add_argument("--table", default="qccdsim_jones",
                       help="primitive table (PLAN 3.2)")
    p_run.add_argument("--cool", action="store_true", help="insert cooling for R7/R7c")
    p_run.add_argument("--html", default=None, help="write a self-contained page here")
    p_run.add_argument("--json", default=None, help="write the report here")
    p_run.add_argument("--max-frames", type=int, default=20000)

    p_list = sub.add_parser("listing", aliases=["disasm"],
                            help="disassemble a program: one line per instruction")
    p_list.add_argument("device")
    p_list.add_argument("--program", default="rotate",
                        choices=["deck", "rotate", "oddeven", "walk"])
    p_list.add_argument("--k", default="")
    p_list.add_argument("--model", default="corrected", choices=["deck", "corrected"])
    p_list.add_argument("--table", default="qccdsim_jones")
    p_list.add_argument("--cool", action="store_true")
    p_list.add_argument("--fold", default=None,
                        choices=["round", "group", "batch", "phase", "check", "flat"],
                        help="which meta key to fold on (default: the coarsest present)")
    p_list.add_argument("--depth", type=int, default=1,
                        help="0 = section summaries only, 1 = lines, 2 = sub-folds")
    p_list.add_argument("--at", type=int, default=None,
                        help="mark this instruction id with a cursor")
    p_list.add_argument("--limit", type=int, default=None,
                        help="stop after N sections")
    p_list.add_argument("--claims", action="store_true",
                        help="show the program's own cost claims, not the replay's")
    p_list.add_argument("--control", action="store_true",
                        help="what the control plane is doing on every machine cycle")
    p_list.add_argument("--no-header", action="store_true")
    p_list.add_argument("--json", default=None, help="write the structured listing here")

    sub.add_parser("verify", help="render the hand-checkable examples to out/verify/")

    p_demo = sub.add_parser("demo", help="render every device to out/index.html")
    p_demo.add_argument("--max-frames", type=int, default=20000)

    # one command for all three, because a template change invalidates all three
    p_regen = sub.add_parser("regen", help="regenerate every page: demo, verify, studio")
    p_regen.add_argument("--max-frames", type=int, default=20000)

    p_reach = sub.add_parser("reach", help="what the device can do, with no programme")
    p_reach.add_argument("device")
    p_reach.add_argument("--model", choices=["deck", "corrected"], default="corrected")
    p_reach.add_argument("--metric", choices=["quanta", "us", "cost"], default="quanta")
    p_reach.add_argument("--json", default=None)

    sub.add_parser("analyses", help="what analyses exist, and their knobs")

    p_sweep = sub.add_parser("sweep", help="vary one design knob and print the curve")
    p_sweep.add_argument("analysis", help="reach | budget (see `qccd analyses`)")
    p_sweep.add_argument("device")
    p_sweep.add_argument("key", help="the knob, dotted for nested (scale.junction)")
    p_sweep.add_argument("values",
                         help="comma list, or lo:hi:step for a range")
    p_sweep.add_argument("--model", choices=["deck", "corrected"], default=None)
    p_sweep.add_argument("--set", action="append", metavar="KEY=VALUE",
                         help="fix another knob for the whole sweep; repeatable")
    p_sweep.add_argument("--json", default=None)

    p_studio = sub.add_parser("studio", help="the browser design tool, as one page")
    p_studio.add_argument("-o", "--out", default=str(OUT / "studio.html"))
    p_studio.add_argument("--seed", default=None,
                          help="start from this device instead of an empty canvas")
    p_studio.add_argument("--name", default="studio")
    p_studio.add_argument("--template", default=None,
                          help="the physics package an empty canvas borrows "
                               "(default: the built-in default template)")
    p_studio.add_argument("--program", default=None,
                          help="with --seed, a built-in program to open on")
    p_studio.add_argument("--k", default="")
    p_studio.add_argument("--model", default="corrected", choices=["deck", "corrected"])
    p_studio.add_argument("--table", default="qccdsim_jones")
    p_studio.add_argument("--all-templates", dest="all_templates",
                          action="store_true", default=True,
                          help="ship every arch/*.arch.json as a start template "
                               "(default; about +46 KB)")
    p_studio.add_argument("--no-all-templates", dest="all_templates",
                          action="store_false")
    p_studio.add_argument("--max-frames", type=int, default=20000)

    p_open = sub.add_parser("open", help="verify a design the browser produced")
    p_open.add_argument("file", help="a .arch.json or a .qccd.json studio artifact")
    p_open.add_argument("--tsir", default=None, help="the programme to run on it")
    p_open.add_argument("--program", default="rotate")
    p_open.add_argument("--k", default="")
    p_open.add_argument("--model", default="corrected", choices=["deck", "corrected"])
    p_open.add_argument("--table", default="qccdsim_jones")
    p_open.add_argument("--html", default=None)
    p_open.add_argument("--max-frames", type=int, default=20000)

    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return {"devices": cmd_devices, "show": cmd_show, "run": cmd_run,
            "demo": cmd_demo, "verify": cmd_verify, "regen": cmd_regen,
            "reach": cmd_reach, "sweep": cmd_sweep,
            "analyses": cmd_analyses,
            "arch": cmd_arch,
            "source": cmd_arch, "studio": cmd_studio, "open": cmd_open,
            "listing": cmd_listing, "disasm": cmd_listing}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
