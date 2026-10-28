"""The full-stack page: the Studio playing the compiled program, plus the stack of layers.

The page is the plain Studio render of the hardware program, joined to its circuit exactly as a
leaderboard entry is (`qccd.ir.source_map`, checked against the certificate's witnesses).  The
full stack rides in that join as `source.stack`, and one script (`web/stack.js`) adds it to the
Program pane -- a fourth view, Stack, beside Hardware / Gates / Both, in which every layer stands in
a full-height column of its own (and the Studio's hardware listing beside them, on request), its
words coloured and explained by the gadget pages' own tables (`qccd/gadget/web/langs.js`).
Nothing floats over the canvas; the engine and template are untouched, so no other page changes
(docs/FULLSTACK_PLAN.md §2.6, §2.6a).

At every instruction the reader sees, each with its line and how the link was made:

    Logical     the .prog line                         e.g. CX(0, 1)
    PPM         the .qppm Pauli-product measurement     e.g. measure Z q[0] X q[1] Z anc[0] -> m[0]
    QEC         the .qec operation on code blocks       e.g. extract LogicDataMerged0 rounds=3 ...
    Clifford    the .sysprog statement, operands bound  e.g. CNOT data[3]=q3, ancilla[193]=q21  (surfaceRound, rv_r26=0)
    Hardware    the instruction itself (the engine's own NOW line)
"""
from __future__ import annotations

import json
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
LANGS_JS = Path(__file__).resolve().parents[1] / "gadget" / "web" / "langs.js"

TITLES = (("prog", "Logical"), ("qppm", "PPM"), ("qec", "QEC"), ("sys", "Clifford"))
HOW = {
    "only": "the program has one statement",
    "name": "a gadget call keeps its name across the layers",
    "pauli": "the merge names the same Pauli product as this measurement",
    "band": "the same record band is written, read or decoded",
    "group": "allocation, preparation, extraction, decode or split around an anchored merge",
    "order": "the k-th preparation or unconditional frame, in order",
    "kind": "the magic request, patch request or |+> preparation of the group",
    "count": "the ancilla allocation of the same size",
    "operands": "the split's measurement of the merge ancilla",
    "inserted": "a release FormalRV's system compiler inserted after the block's last use",
}


def _display(stage: dict, fallback: str) -> dict:
    """A layer's text for the page, named by file only (no repository paths on a page)."""
    name = Path(stage["path"]).name or fallback
    return {"file": name, "members": stage["members"], "lines": stage["lines"]}


def stack_payload(trace: dict) -> dict:
    L = trace["layers"]
    links = trace["links"]
    sys_q = {int(k): v for k, v in links["sys"].items()}
    qec_p = {int(k): v for k, v in links["qec"].items()}
    qppm_g = {int(k): v for k, v in links["qppm"].items()}
    events = []
    last_route: dict = {}
    route_of: dict = {}
    order = sorted(trace["events"], key=lambda e: (e[2], e[0]))
    for uid, kind, start, dur, line, frames, operands, phys in order:
        if kind == "route":
            for v in operands:
                last_route[v] = (uid, set(operands))
        elif kind in ("CNOT", "CZ", "CY") and operands:
            r = last_route.get(operands[0])
            if r and set(operands) <= r[1]:
                route_of[uid] = r[0]
    for uid, kind, start, dur, line, frames, operands, phys in trace["events"]:
        qec = None
        for cand in [line] + [f[1] for f in reversed(frames)]:
            if cand in sys_q:
                qec = sys_q[cand]
                break
        qppm = qec_p.get(qec[0]) if qec else None
        prog = qppm_g.get(qppm[0]) if qppm else None
        ctx = " / ".join(f[2] for f in frames)
        ops = ", ".join(f"{v}=q{p}" if p is not None else v for v, p in zip(operands, phys))
        events.append([kind, line, ctx, ops,
                       qec[0] if qec else 0, qec[1] if qec else "",
                       qppm[0] if qppm else 0, qppm[1] if qppm else "",
                       prog[0] if prog else 0, prog[1] if prog else ""])
    return {
        "v": 1,
        "layers": {"prog": _display(L["prog"], "program.prog"), "qppm": _display(L["qppm"], "program.qppm"),
                   "qec": _display(L["qec"], "program.qec"), "sys": _display(L["sysprog"], "program.sysprog")},
        "titles": dict(TITLES),
        "events": events,
        "op_event": [c[2] for c in trace["circuit"]],
        "op_role": [c[3] for c in trace["circuit"]],
        "route": route_of,
        "how": HOW,
        "formalrv": trace["formalrv"]["formalrv_commit"][:10],
        "abstract": trace["abstract"], "concrete": trace["concrete"],
        "factory": trace["factory"], "code": trace["code"]["name"], "distance": trace["distance"],
    }


def hints() -> dict:
    return {
        "fullstack-view": {"t": "Stack view",
                           "d": "Show every layer of the full stack at the executing instruction: the logical "
                                "program line, the PPM, the QEC operation, the Clifford circuit statement and "
                                "the hardware instruction, each with how the link between layers was made."},
        "fullstack-line": {"t": "A line of one layer",
                           "d": "Jump the animation to the first hardware instruction that executes this line."},
        "fullstack-col": {"t": "Show or hide a layer's column",
                          "d": "Each layer of the stack -- the logical program, the PPMs, the QEC operations, the "
                               "Clifford circuit -- and the hardware listing stands in a full-height column of its "
                               "own; this button shows or hides one."},
        "fullstack-close": {"t": "Hide this column", "d": "Hides this layer's column; its button above brings it back."},
        "fullstack-wrap": {"t": "Wrap long lines",
                           "d": "Long lines wrap inside their column (on), or run on and scroll sideways (off)."},
    }


def render(run_dir: Path, out: Path) -> Path:
    from ..arch.device import Architecture
    from ..cost.models import corrected_model
    from ..ir.source_map import build as build_source
    from ..ir.tsir import TSIR
    from ..phys.tech import load_technology
    from ..verify import verify
    from ..viz.render import render_html
    from ..viz.scale import DEFAULT_TECH
    from .pipeline import PHYSICS

    run_dir = Path(run_dir)
    trace = json.loads((run_dir / "trace.json").read_text(encoding="utf-8"))
    cw = run_dir / "hw"
    arch = Architecture.from_json(json.loads((cw / "device.arch.json").read_text(encoding="utf-8")))
    prog = TSIR.load(cw / "prog.cooled.tsir.json")
    cert = json.loads((cw / "prog.qcert.json").read_text(encoding="utf-8"))
    source = build_source(prog, cert, cw / "circuit.qasm")
    source["stack"] = stack_payload(trace)
    model = corrected_model(PHYSICS[trace["physics"]][1])
    report = verify(prog, arch, model)
    a, c = trace["abstract"], trace["concrete"]
    page = render_html(
        arch, prog, report.result, model, out, tech=load_technology(DEFAULT_TECH),
        kicker="FULL STACK",
        headline=f"{trace['program']}: every layer, on {arch.name}",
        lede=(f"FormalRV's .prog, .qppm, .qec and .sysprog, devirtualized onto {c['ions']} ions: "
              f"{c['instructions']} hardware instructions, {c['runtime_us'] / 1e3:.1f} ms "
              f"(FormalRV's VM, on the abstract machine: {a['runtime_ns'] / 1e6:.1f} ms)"),
        source=source, open_pane="Q")
    html = Path(page).read_text(encoding="utf-8")
    css = (WEB / "stack.css").read_text(encoding="utf-8")
    js = (WEB / "stack.js").read_text(encoding="utf-8")
    # the languages' words and notes: the gadget pages' own tables, so the two tools colour and
    # explain every layer alike
    langs = LANGS_JS.read_text(encoding="utf-8")
    inject = (f"<style>{css}</style>\n<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {{}}, "
              f"{json.dumps(hints())});</script>\n<script>{langs}</script>\n<script>{js}</script>\n")
    if html.count("</body>") != 1:
        raise ValueError("the Studio page has no single </body> to add the stack before")
    html = html.replace("</body>", inject + "</body>")
    Path(page).write_text(html, encoding="utf-8", newline="")
    return Path(page)
