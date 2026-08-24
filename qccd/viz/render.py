"""Render `(architecture, program)` to one self-contained HTML file.  PLAN §9.

Keeps the shape of the shipped artifact -- one file, no server, no CDN -- but makes it a
renderer over `(arch, tsir)` rather than over one hard-coded ring, so a grid renders as a
grid and a racetrack as a racetrack from the same code path. That is the difference
between a demo and a design tool.

What it adds over the shipped viewer, in PLAN §9's priority order:

1. **per-ion n-bar heat colouring** with the R7 gate threshold marked, because the heating
   budget is the design's biggest problem and it should be visible in one glance;
2. **cooling drawn as a named track** on the timeline, so its share of runtime is legible;
3. a rule badge per step;
4. a *"why is this cycle alone?"* line naming the SIMD class and mode that formed the
   batch -- which turns the viewer into a debugger for the 9.1% utilization problem.

How the frames stay small
-------------------------
A naive trace of the shipped ring is 2672 cycles x 144 moves. Instead a frame is either a
**template** (`shift loop by delta`) or an explicit move list, mirroring the IR exactly --
so a rigid rotation is one frame entry however many ions it moves. The page replays them,
accumulating n-bar with the architecture's own curve constants, and **checks its own
result against the Python replay**: the exported `checksum` is the final per-ion n-bar,
and the page says so out loud if it disagrees. A viewer that can silently drift from the
verifier is worse than no viewer.

Where the picture comes from
----------------------------
Nothing on the stage is sized by a heuristic any more. `qccd.viz.layout.compute_layout`
measures the device -- its lattice, its bounding box, and `g`, the minimum
nearest-neighbour distance *in drawn pixels* -- and every mark is a fixed fraction of `g`.
`2*r_ion = 0.68*g < g` is a strict inequality in units of `g`, so ions cannot overlap at
any scale, on any device. The layout travels in the JSON blob as ~40 scalars, which is
what makes it testable from Python (`tests/test_viz_layout.py`) instead of only visible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ..arch import Architecture
from ..arch.edit import arch_fingerprint, device_to_wire
from ..arch.generators import GENERATORS
from ..arch.listing import architecture_listing
from ..arch.schema import (
    export_consumers,
    export_defaults,
    export_element_docs,
    export_schema,
)
from ..api import DEFAULT_TEMPLATE
from ..cost.hardware import hardware_report
from ..cost.models import CostModel
from ..ir.listing import disassemble, to_page_model
from ..ir.provenance import log_of, thin
from ..ir.tsir import TSIR, iter_pairs
from ..verify.control import ControlTrace, control_trace
from ..verify.replay import ReplayResult
from ..verify.rules import rule_statements
from .layout import (H_MAX, H_MIN, ISO_ASPECT, K_ANISO, K_ION, K_REST, PAD_A, PAD_B,
                     PITCH_CAP, R_ION_MAX, R_ION_MIN, W_MAX, W_MIN, compute_layout)
from .theme import GEOMETRY, PALETTE, SEGMENT_ROLE, css_vars

__all__ = ["render_html", "build_view_model"]

#: THE BROWSER SET: the rules `qccd/viz/engine.js::checkFrames` re-derives client-side.
#:
#: Not a hand list on either side -- Python asserts this equals `QCCD.mirroredRules()`
#: (`tests/test_engine_parity.py`), which is itself derived from the JS dispatcher.  A rule
#: that is advertised-but-undispatchable, or dispatchable-but-unadvertised, is impossible
#: rather than merely tested against.
#:
#: `rule_checksum` ships one INTEGER per entry -- how many violations Python found -- and
#: the page diffs its own counts against them before the user touches anything.  Counts,
#: not verdicts: `architectureViolations` once reported 2 where Python reported 77 and the
#: verdict agreed both times, which is exactly why a verdict-only comparison called that
#: agreement.
BROWSER_SET = ("R1", "R2", "R3", "R4", "R4b", "R5", "R6", "R6b", "R7", "R7c",
               "R8", "R11", "R12", "R13", "R14", "R17", "R18")

#: A page that cannot resolve a click to a channel id is better than a page that ships
#: 100 KB to do it.  `direct` wiring puts 4608 channels on 144 sites; above this budget
#: the site -> channel map degrades to counts only and the panel says so.
CHANNEL_MAP_BUDGET = 64_000


def _node_path(arch: Architecture, participant) -> list[str]:
    """The node sequence a participant traverses, `[src, ..., dst]`.

    A move across a grid goes trap -> junction -> trap, and the junction is charged on
    entry, so the page has to see the intermediate nodes.  Exporting only the destination
    would make the animation under-count exactly the term R18 exists to price.
    """
    if not participant.via:
        return [participant.src, participant.dst]
    path = [participant.src]
    node = participant.src
    for sid in participant.via:
        node = arch.device.segments[sid].other(node)
        path.append(node)
    return path


def segment_role(labels) -> str:
    """The deck's role colour for a segment: data region, highway, or computing."""
    for label, role in SEGMENT_ROLE:
        if label in labels:
            return role
    return "rail"


def build_view_model(
    arch: Architecture,
    prog: TSIR,
    res: ReplayResult,
    model: CostModel,
    *,
    max_frames: int = 20000,
    kicker: str | None = None,
    headline: str | None = None,
    lede: str | None = None,
    control: ControlTrace | None = None,
    include_listing: bool = True,
    include_control: bool = True,
    provenance: str = "sites",
    template_stems: Sequence[str] | str | None = None,
    metal: dict | None = None,
) -> dict:
    """Everything the page needs, as plain JSON.

    Three panels' worth of structure travels here alongside the animation frames, and
    all of it is keyed by `Instruction.id` / namespaced object id rather than by
    position, so a future editor can map a click back to the object:

    * `listing` -- the hardware program, disassembled by `qccd.ir.listing` and
      compressed columnar (~119 KB for the 1,579-instruction deck program);
    * `arch.listing` -- the architecture as the Python that rebuilds it, from
      `qccd.arch.listing`, with the same `class:`/`loop:`/`site:` id namespace the
      program listing joins on;
    * `control` -- one deduplicated control-plane record per instruction from
      `qccd.verify.control`, plus one integer per frame pointing into the table.
    """
    dev = arch.device
    corners = dev.all_corners

    nodes = []
    for nid, node in dev.nodes.items():
        nodes.append(
            {
                "id": nid,
                "x": float(node.pos[0]),
                "y": float(node.pos[1]) if len(node.pos) > 1 else 0.0,
                "kind": node.kind,
                "zone": node.zone_type,
                "cap": node.capacity,
                "deg": dev.degree(nid),
                "corner": nid in corners,
                "labels": list(node.labels),
                # `capacity_explicit` decides whether a site follows its zone when the
                # zone is retuned.  Without it the browser's `resolve_capacities` cannot
                # tell an inherited capacity from a deliberate one and silently shrinks a
                # trap the designer made bigger.
                "cap_explicit": bool(node.capacity_explicit),
            }
        )
    segments = [
        {
            "id": s.id,
            "a": s.ends[0],
            "b": s.ends[1],
            "loop": s.loop,
            "labels": list(s.labels),
            "cap": s.capacity,
            # declared length, which `length_scaling` cost models read.  A drag editor
            # that could not see it could not tell the user that the geometry it just
            # changed disagrees with the number the model is charging.
            "len": float(s.length),
            "corner_endpoints": dev.corner_endpoints[s.id],
        }
        for s in dev.segments.values()
    ]
    loops = {lid: list(loop.nodes) for lid, loop in dev.loops.items()}

    # what each instruction actually cost, so the page's counters tick with the replay
    by_instr: dict[int, list] = {}
    for c in res.cycles:
        slot = by_instr.setdefault(c.instr_id, [0.0, 0])
        slot[0] += c.cost
        slot[1] += c.depth
    by_instr = {k: (round(v[0], 6), v[1]) for k, v in by_instr.items()}

    # ---- frames: one entry per instruction, template-compressed --------------
    frames = []
    truncated = False
    for instr in prog.instructions:
        if len(frames) >= max_frames:
            truncated = True
            break
        f: dict = {"id": instr.id, "type": instr.type, "cls": instr.cls,
                   "mode": instr.mode}
        if instr.type == "init":
            f["place"] = dict(instr.placement)
        elif instr.type == "simd":
            if instr.template and instr.template.get("kind") == "loop_shift":
                f["shift"] = [str(instr.template["loop"]), int(instr.template["delta"])]
            else:
                f["moves"] = [
                    [p.ion, _node_path(arch, p)] for p in instr.participants
                ]
                f["entails"] = list(arch.entails(instr.cls) if instr.cls else ())
        elif instr.type == "gate":
            f["pairs"] = [list(p) for p in iter_pairs(instr)]
            f["sites"] = list(instr.sites)
        elif instr.type == "cool":
            f["broadcast"] = bool(instr.broadcast)
            f["ions"] = list(instr.ions)
        elif instr.type in ("measure", "reset"):
            f["ions"] = list(instr.ions)
        if instr.gate:
            f["gate"] = instr.gate
        meta = instr.meta or {}
        for key in ("batch", "check", "kind", "round", "group", "phase", "trigger",
                    "hops"):
            if key in meta:
                f[key] = meta[key]
        if isinstance(meta.get("call"), int) and not isinstance(meta.get("call"), bool):
            f["call"] = meta["call"]
        rec = by_instr.get(instr.id)
        if rec:
            f["cost"], f["steps"] = rec
        frames.append(f)

    # ---- the constants the page needs to reproduce the replay ----------------
    def _pt(name):
        try:
            p = arch.primitives.curve(name).pick(getattr(model, "policy", None)) \
                if getattr(model, "policy", None) else None
            return {"us": p.us, "quanta": p.quanta} if p else None
        except Exception:
            return None

    junction_q: dict[str, dict] = {}
    min_degree = int(getattr(model, "junction_min_degree", 3))
    try:
        dc = arch.primitives.degree_curve("junction_cross")
        pol = getattr(model, "policy", None)
        for d in dc.degrees():
            if pol is not None and d >= min_degree:
                p = dc.get(d).pick(pol)
                junction_q[str(d)] = {"us": p.us, "quanta": p.quanta}
    except Exception:
        pass

    hw = hardware_report(arch)
    threshold = float(arch.primitives.scalar("ms_gate").get("max_quanta", 0) or 0) \
        if "ms_gate" in arch.primitives.scalars else 0.0

    # The page reproduces the TRANSPORT accounting only -- it has no per-frame duration,
    # so it cannot accumulate R17's elapsed-time term.  The checksum therefore covers
    # exactly the components the page models, and the page says which those are; a
    # checksum over terms the page never computes would fail every time and mean nothing.
    checked_components = ("shuttle", "junction", "split_merge")
    checksum = {
        ion: round(sum(v.get(c, 0.0) for c in checked_components), 6)
        for ion, v in res.per_ion_quanta.items()
    }
    exact = getattr(model, "corner_hops", 1) == 1

    # ---- the control plane, per instruction ---------------------------------
    trace = control
    if trace is None and include_control:
        # the record must see byte-identical state to R4d, so it is collected from the
        # same CycleView the rules judge -- a second replay, but the cheap one
        # (no rules, no cycle records)
        trace = control_trace(prog, arch, model)
    ctl: dict | None = None
    if include_control and trace is not None:
        ctl = trace.to_json()
        ctl["plane"] = arch.control_plane.summary()
        ctl["spec"] = dict(arch.control.get("channels", {}) or {})
        ctl["notes_plane"] = list(arch.control_plane.notes)
        ctl["channels_by_site"] = _channel_map(arch)
        for i, f in enumerate(frames):
            if i < len(trace.index):
                f["ctl"] = trace.index[i]

    # ---- the hardware program, disassembled ---------------------------------
    prog_listing = None
    if include_listing:
        by_id = {r.instr_id: r for r in (trace.records if trace else ())}
        prog_listing = to_page_model(
            disassemble(prog, arch, res=res, model=model, control=by_id or None))

    roles = {sg.id: segment_role(sg.labels) for sg in dev.segments.values()}
    ion_roles = {}
    for instr in prog.instructions:
        if instr.type == "init":
            for ion, node in instr.placement.items():
                n = dev.nodes.get(node)
                ion_roles[ion] = (
                    "ancilla" if (n is not None and n.zone_type == "ancilla") else "data")
            break

    return {
        "kicker": kicker or "ROUTING SCHEME",
        "headline": headline or f"{arch.name} · {prog.name}",
        "lede": lede or (arch.description or ""),
        "roles": roles,
        "ion_roles": ion_roles,
        "layout": compute_layout(nodes, segments),
        "arch": {
            "name": arch.name,
            "description": arch.description or "",
            "generator": dev.generator,
            "params": dict(dev.params),
            "nodes": nodes,
            "segments": segments,
            "loops": loops,
            "summary": dev.summary(),
            "hardware": hw.as_dict(),
            "zone_types": {k: dict(v) for k, v in arch.zone_types.items()},
            # the architecture AS A PROGRAM: one record per statement, each carrying
            # the namespaced id of what it declares, so the page can light the line
            # that authorised the instruction currently executing
            "listing": architecture_listing(
                arch, mode="full", policy=getattr(model, "policy", None),
                verify=False).to_json(refs=False),
        },
        "program": {
            "name": prog.name,
            "n_instructions": len(prog),
            "templates": prog.templates(),
            "frames": frames,
            "truncated": truncated,
            "max_simd_classes": arch.max_simd_classes(),
        },
        "listing": prog_listing,
        "control": ctl,
        # ---- everything the CLIENT-SIDE EDITOR needs, and nothing it does not --------
        #
        # The drawing shape above is LOSSY on purpose -- it drops `Loop.closed`, `kind`
        # and `note`, and `closed` is not recoverable in general (an open path whose ends
        # happen to carry a segment is indistinguishable from a closed loop; guessing it
        # on `stationary_chain` called both its nodes corners and charged its one segment
        # two corner endpoints, neither of which is true).  So the editor gets the
        # LOSSLESS wire form as well, from the one function that defines how a Device
        # crosses into JS.
        "device_wire": device_to_wire(dev),
        # the movement classes, keyed by id.  Frames bake `entails` at render time, so
        # after `declare_class("dock", entails=[])` the baked list is a lie -- the engine
        # reads entails from here instead, keyed by `f.cls`.
        "classes": {cid: dict(arch.simd_class(cid) or {}) for cid in arch.simd_classes},
        "fingerprint": arch_fingerprint(arch),
        "generator_defaults": _generator_defaults(),
        # `generator_defaults` drops every REQUIRED positional, so a start gallery built
        # from it alone cannot construct a legal call for four of the six generators.
        "generator_signatures": _generator_signatures(),
        # The open maps the schema cannot describe, and WHO READS each field.  27 of the
        # 65 carry `reader: null` -- declared, printed, round-tripped, and computed with
        # by nothing.  The palette says so at the control rather than rendering an inert
        # field like a live one.
        "consumers": export_consumers(),
        # Dataclass defaults by reflection.  Nothing about a default is written in JS.
        "defaults": export_defaults(),
        # THE SCHEMA ITSELF, straight out of `qccd/arch/schema.py`.  Same reasoning as
        # `generator_defaults` above: the browser must refuse what Python's loader will
        # refuse, and the only way to guarantee that without a second source of truth is
        # to ship the constraints rather than restate them.  `engine.js::validateDocument`
        # walks this and knows no enum, bound or pattern of its own.
        "schema": export_schema(),
        # ONE NAME AND ONE SENTENCE PER PALETTE ELEMENT, from `qccd/arch/schema.py`.
        # The schema itself carries no field documentation, so the element menu would
        # otherwise have to write its prose in JavaScript -- a description that outlives
        # the field it describes.
        "element_docs": export_element_docs(),
        # THE PALETTE AS DATA, not only as CSS custom properties.  `C[k]` is a round trip
        # of this table through `--<k>`, and `getComputedStyle` is a stub in the headless
        # harness -- so until this shipped, no test could assert that a mark is drawn in
        # the zone's colour, or that the palette avatar is drawn in the SAME colour as the
        # stage.  Read order in the page is `D.palette[k] || css(k)`, so a browser sees no
        # change at all.
        "palette": dict(PALETTE),
        # THE SEGMENT ROLE TABLE, so the browser colours a segment the user just created
        # by the same rule Python coloured the shipped ones by.  `roles` below is a
        # snapshot keyed by segment id and cannot answer for a segment that did not exist
        # when the page was emitted.
        "segment_roles": [list(pair) for pair in SEGMENT_ROLE],
        # THE TEMPLATE REGISTRY.  `Machine.ring(..., template="ring144_24v")` reads
        # `arch/<stem>.arch.json` off disk; a browser has no filesystem, so each template
        # travels as the RECORDS THAT DECLARE IT and `engine.js::_applyTemplate` replays
        # them.  Same reasoning as `schema` above -- ship the thing, do not restate it --
        # and it costs nothing to express, because a template is already a program.
        "templates": _template_registry(arch, template_stems),
        "components": _component_registry(),
        # what an un-named `template=` resolves to, straight off `api.DEFAULT_TEMPLATE`
        "template_default": DEFAULT_TEMPLATE,
        # the source line that emitted each instruction.  Exported at level "sites":
        # `root` is an absolute path on the machine that built the page and `args`
        # duplicates the rendered call more verbosely, so neither ships.
        "prov": thin(log_of(prog), provenance),
        "model": dict(res.model),
        "physics": {
            "shuttle": _pt("shuttle_segment"),
            "split": _pt("split"),
            "merge": _pt("merge"),
            "junction_by_degree": junction_q,
            "corner_hops": getattr(model, "corner_hops", 1),
            "junction_min_degree": min_degree,
            "gate_threshold": threshold,
            "anomalous_per_ms": arch.anomalous_rate(),
            # The WHOLE curves, not only the already-picked point: `set_curve` and
            # `set_degree_curve` are two of the twelve editable methods, and shipping one
            # resolved operating point would make both of them unrepriceable in the
            # browser.  The policy travels with them so the point can be re-picked.
            "curves": {n: [pt.to_json() for pt in c.points]
                       for n, c in arch.primitives.curves.items()},
            "degree_curves": {n: {str(d): [pt.to_json() for pt in dc.get(d).points]
                                  for d in dc.degrees()}
                              for n, dc in arch.primitives.degree_curves.items()},
            "scalars": {n: dict(v) for n, v in arch.primitives.scalars.items()},
            "policy": {"table": getattr(getattr(model, "policy", None), "table", None),
                       "objective": getattr(getattr(model, "policy", None),
                                            "objective", "fastest")},
        },
        # The layout constants live in `layout.py` and in `theme.GEOMETRY`; hard-coding
        # them a third time in JS would be a mirror the parity test could not protect,
        # because a constant changed in Python and forgotten in JS looks like an algorithm
        # difference at the far end of a fixed point.  They ship, and the engine asserts
        # against them at load.
        "layout_consts": {
            "W_MAX": W_MAX, "W_MIN": W_MIN, "H_MAX": H_MAX, "H_MIN": H_MIN,
            "PAD_A": PAD_A, "PAD_B": PAD_B, "PITCH_CAP": PITCH_CAP,
            "K_ANISO": K_ANISO, "ISO_ASPECT": ISO_ASPECT,
            "K_ION": K_ION, "K_REST": K_REST,
            "R_ION_MAX": R_ION_MAX, "R_ION_MIN": R_ION_MIN,
            "ION_D_FRAC": GEOMETRY["ION_D_FRAC"],
            "ION_D_FRAC_ACTIVE": GEOMETRY["ION_D_FRAC_ACTIVE"],
            "RAIL_W_FRAC": GEOMETRY["RAIL_W_FRAC"],
            "RUNG_W_FRAC": GEOMETRY["RUNG_W_FRAC"],
        },
        "metrics": {
            **res.metrics(),
            "quanta_components": dict(res.quanta_components),
            "us_by_class": dict(res.us_by_class),
            "us_by_type": dict(res.us_by_type),
            "cost_by_class": dict(res.cost_by_class),
            "n_cool": sum(1 for c in res.cycles if c.type == "cool"),
            "cooling_us": res.us_by_type.get("cool", 0.0),
        },
        "rules": res.rules.summary(),
        # THE EVIDENCE SETS, so the page can key its verdicts on the SIZE of the evidence
        # rather than on the presence of a key.  With `checksum == {}` the self-check's
        # `for (const ion in D.checksum)` loop never ran, `drift` stayed 0, and the page
        # printed "agrees with the Python verifier to 0.0e+0 quanta per ion" -- a green
        # tick for a check that did not happen, in the one panel that asserts the page is
        # trustworthy.  All three counts are non-zero on every shipped page, so the
        # existing nine take an identical code path.
        "evidence": {
            "self_check_ions": len(checksum),
            "replayed_cycles": len(res.cycles),
            "rules_evaluated": sorted(res.rules.checked),
            "rules_all": sorted(rule_statements()),
        },
        # All 23 rule statements, so the Report pane can name what it did NOT check
        # without hard-coding 23 sentences in JavaScript.
        "rule_statements": rule_statements(),
        # THE RULE HALF OF THE CHECKSUM.  Seventeen integers: how many violations Python
        # found for each rule the browser can also check.  COUNTS, not verdicts --
        # `architectureViolations` once reported 2 where Python reported 77 and the
        # verdict agreed both times, which is exactly why a verdict-only comparison called
        # that agreement.
        "rule_checksum": {r: res.rules.by_rule().get(r, 0) for r in BROWSER_SET},
        "rule_checksum_set": list(BROWSER_SET),
        "checksum": checksum,
        "checksum_components": list(checked_components),
        "checksum_exact": exact,
        # THE DERIVED ELECTRODES, or absent.  Built by `qccd.phys.svg.metal_view_model`
        # and passed in -- this module does not import `qccd.phys`, because the metal is
        # a property of `(device, technology)` and a page is a property of a run, and
        # wiring the one to the other here would make every page pay for a field solve it
        # did not ask for.  Absent by default, so every page emitted before this existed
        # is byte-identical to the one emitted now.
        **({"metal": metal} if metal else {}),
    }


#: The JavaScript the page inlines VERBATIM, in two stages.
#:
#: `__ENGINE__` sits BEFORE the page's own script: `edit.js` first, because `engine.js`
#: delegates degree / corner / corner_endpoints to it rather than keeping a third copy of
#: code that already has a differential test.  `__EDITOR__` sits AFTER it, because the
#: editor drives the page's own globals (`svg`, `L`, `A`, `draw`, `selectRef`).
#:
#: READ FROM DISK, never pasted.  `tests/test_engine_parity.py` asserts each file's bytes
#: appear byte-for-byte in every emitted page, so the tested copy and the shipped copy are
#: one thing.  If a future change here forked the engine, the parity test would keep
#: passing while the page ran the fork -- the exact failure this design exists to prevent.
_JS_DIR = Path(__file__).parent
ENGINE_JS = ("js/edit.js", "engine.js")
EDITOR_JS = ("js/editor.js",)


def _js_block(names) -> str:
    out = []
    for name in names:
        src = (_JS_DIR / name).read_text(encoding="utf-8")
        rule = "-" * max(3, 66 - len(name))
        out.append(f"<script>\n// ==== {name} {rule}\n{src}\n</script>")
    return "\n".join(out)


def _generator_defaults() -> dict:
    """Every generator's keyword defaults, by reflection.

    `inspect.signature` is the single source of truth for these, so they SHIP as data
    rather than being re-declared in JS.  A default changed in Python then reaches the
    browser automatically and there is no second copy to drift.
    """
    import inspect
    out: dict[str, dict] = {}
    for name, fn in GENERATORS.items():
        params = {}
        for pname, param in inspect.signature(fn).parameters.items():
            if param.default is not inspect.Parameter.empty:
                params[pname] = param.default
        out[name] = params
    return out


def _generator_signatures() -> dict:
    """Every generator's REQUIRED positionals as well as its defaults.

    `_generator_defaults` reflects only parameters that HAVE a default, so `ring(width)`,
    `grid(a, b)`, `chain(n)` and `racetrack(straight)` -- the first argument of four of
    the six -- are absent from what the page ships.  A gallery built from the defaults
    alone therefore constructs an illegal call for four of six generators.  Same
    reflection, one more field.
    """
    import inspect
    out: dict[str, dict] = {}
    for name, fn in GENERATORS.items():
        req, dflt = [], {}
        for pname, param in inspect.signature(fn).parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            if param.default is inspect.Parameter.empty:
                req.append(pname)
            else:
                dflt[pname] = param.default
        out[name] = {"required": req, "defaults": dflt}
    return out


def _template_registry(arch: Architecture, stems: "Sequence[str] | str | None" = None) -> dict:
    """`{stem: [call records]}` for the templates this page can be asked to replay.

    By default two stems: the page's own architecture -- which is what its own listing
    names when emitted with `template=<stem>` -- and `ring144_24v`, which is what an
    un-named `template=` resolves to.  A template the page does not carry is refused by
    name, which is a fact about the data; the alternative was refusing the VERB, which is
    what `'from_template' is not an editable method` claimed while `methods()` listed it.

    `stems="*"` ships every `arch/*.arch.json`, which is what the studio's start gallery
    needs: nine physics packages to choose between rather than a pair.  Measured at 61,171
    bytes for all nine against ~15 KB for the pair, i.e. about +12% on a 362-420 KB page.
    A PARAMETER, never a branch -- two page kinds would be two implementations of one page.
    """
    from ..arch import load as _load_arch
    from ..arch.listing import template_records

    out: dict[str, list] = {}
    cache = {arch.name: arch}
    root = Path(__file__).resolve().parents[2] / "arch"
    if stems == "*" or (stems is not None and "*" in tuple(stems)):
        want = [arch.name, DEFAULT_TEMPLATE]
        want += sorted(f.name[: -len(".arch.json")] for f in root.glob("*.arch.json"))
    elif stems:
        want = [arch.name, DEFAULT_TEMPLATE, *stems]
    else:
        want = [arch.name, DEFAULT_TEMPLATE]
    for stem in want:
        if stem in out:
            continue
        a = cache.get(stem)
        if a is None:
            path = root / f"{stem}.arch.json"
            if not path.exists():
                continue
            a = _load_arch(path)
        out[stem] = [dict(r) for r in template_records(a)]
    return out


def _component_registry() -> dict:
    """The shipped component catalogue, as the records that build each one.

    Exactly the shape `_template_registry` uses, and for the same reason: a component
    travels as the PROGRAM THAT DECLARES IT, in the one language both halves already
    agree on, rather than as a second document format the browser would have to learn.
    `engine.js` replays these through the interpreter it already has, so shipping the
    catalogue adds no verb and no parity surface.

    THE PARAMETERS ARE LIVE, and the tables that make them live are DERIVED FROM THE
    FACTORIES AND CHECKED AGAINST THEM HERE.  `arch/variants.py` recovers, by building
    each factory at probe values and differencing the results, what every parameter does
    to every leaf -- one of three things: a multiply by a shipped coefficient, a
    substitution, or one interpolation into a fixed template.  Anything a parameter does
    that is none of those raises `VariantError` and this function does not return, so a
    page carrying a table that disagrees with the factory it came from cannot be built.
    That is the property a hand-written JavaScript mirror could never have, and it is why
    there is no such mirror: what ships is Python's own output plus three operations.

    `name`, `blurb`, `params`, `pins`, `requires` and `records` stay byte-identical to
    what shipped before -- the default records remain the witness the tables are checked
    against, for free.
    """
    from ..arch import variants as _variants
    from ..arch.library import CATALOG, catalog_json
    from ..arch.component import GEOMETRY_METHODS

    meta = {c["name"]: c for c in catalog_json()}
    out: dict[str, dict] = {}
    for name, factory in sorted(CATALOG.items()):
        comp = factory()
        block = _variants.variant_block(name)
        # THE GATE. Raises rather than emitting a table the factory disagrees with.
        _variants.check_variants(name, "spine", block=block)
        out[name] = {
            "name": name,
            "blurb": comp.blurb,
            "params": meta[name]["params"],
            "pins": [dict(p) for p in comp.pins],
            "requires": dict(comp.requires),
            "records": [dict(r) for r in comp.records],
            "var": block,
        }
        for r in out[name]["records"]:
            assert str(r["method"]) in GEOMETRY_METHODS, (name, r["method"])
    return out


#: Substrings that would make the page reach the network, forbidden anywhere in the
#: emitted file -- `tests/test_viz_and_devices.py` asserts on exactly this list.
FORBIDDEN = ("<script src=", "<link ", "@import", "fetch(", "XMLHttpRequest",
             "<img src=", 'href="http')


def _escape_blob(blob: str) -> str:
    r"""Make the JSON data block incapable of looking like markup or a network call.

    Two problems, one fix.  `json.dumps` does not escape `<`, so an architecture
    description containing `</script>` would end the data block early and break the page
    -- latent before provenance and the architecture listing started putting user prose
    and source text into the blob, reachable now.  And the self-containment test forbids
    seven substrings *anywhere in the file*, including inside a JSON string where they
    are completely inert.

    So: escape `< > &` always, then escape the first character of any forbidden token
    that still survives.  `@import` is valid JSON, `JSON.parse` hands back exactly
    `@import`, and the file no longer contains a substring that looks like a stylesheet
    import.  Nothing is lost and nothing is misrepresented.
    """
    blob = (blob.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026"))
    for bad in FORBIDDEN:
        if bad in blob:
            blob = blob.replace(bad, "\\u%04x" % ord(bad[0]) + bad[1:])
    return blob


def _channel_map(arch: Architecture) -> dict:
    """site -> channel ids, dedupe-compressed, or counts only when that is too big.

    Broadcast wiring gives every site the identical 32-id list, which interns to one
    entry plus 168 pointers -- about 1 KB.  A `direct` plane's 4608 channels do not
    compress at all, so above `CHANNEL_MAP_BUDGET` the map degrades to per-site counts
    and the page says click-to-channel is unavailable rather than shipping 100 KB.
    """
    plane = arch.control_plane
    if not plane.declared or not plane.groups:
        return {"lists": [], "of": {}, "counts": {}, "elided": False}
    lists: list[list[str]] = []
    key_ix: dict[tuple, int] = {}
    of: dict[str, int] = {}
    counts: dict[str, int] = {}
    for node in arch.device.nodes.values():
        if node.kind != "site":
            continue
        ids = plane.channels_of(node.id)
        counts[node.id] = len(ids)
        ix = key_ix.get(ids)
        if ix is None:
            ix = key_ix[ids] = len(lists)
            lists.append(list(ids))
        of[node.id] = ix
    size = sum(sum(len(i) + 3 for i in l) for l in lists) + 8 * len(of)
    if size > CHANNEL_MAP_BUDGET:
        return {"lists": [], "of": {}, "counts": counts, "elided": True}
    return {"lists": lists, "of": of, "counts": counts, "elided": False}


_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
__CSSVARS__
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1680px;margin:0 auto;padding:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px}
.kicker{color:var(--accent);font-size:11.5px;font-weight:700;letter-spacing:.09em;
text-transform:uppercase;margin-bottom:2px}
h1{margin:0 0 6px;font-size:27px;line-height:1.15;color:var(--navy);letter-spacing:-.01em}
.lede{color:var(--muted);max-width:82ch;margin:0 0 4px;font-size:13.5px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}
.counters{display:flex;gap:22px;flex:0 0 auto;text-align:right}
.counter span{display:block;color:var(--muted);font-size:10.5px;letter-spacing:.09em;
text-transform:uppercase}
.counter b{display:block;font-size:23px;font-variant-numeric:tabular-nums;line-height:1.1}
.counter .now{color:var(--accent)}
.counter .of{color:var(--muted);font-size:14px}
.metrics{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.m{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:7px 11px;
min-width:98px}
.m span{display:block;color:var(--muted);font-size:10.5px;letter-spacing:.05em;
text-transform:uppercase}
.m b{display:block;font-size:16px;margin-top:1px;font-variant-numeric:tabular-nums}
.row{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap;margin-top:14px}
.stage{flex:5 1 720px;min-width:0;position:relative}
.stage[data-drop="1"] svg{outline:2px dashed var(--accent);outline-offset:-4px}
.row[data-layout="wide"] .stage{flex:1 1 100%}
/* THE PALETTE RAIL.  Collapsible, and hidden entirely in the narrow regime, where it
   becomes a sixth dock tab rather than a squeezed column. */
.rail{flex:0 0 224px;display:flex;flex-direction:column;gap:9px;max-height:78vh;
  overflow:auto;min-width:0}
.rail[data-collapsed="1"]{flex-basis:34px;overflow:hidden}
.rail[data-collapsed="1"] .pal,.rail[data-collapsed="1"] .palfold{display:none}
.dock[data-collapsed="1"]{flex:0 0 34px;min-width:34px;max-width:34px;overflow:hidden}
.dock[data-collapsed="1"] > *:not(.grip){display:none}
/* THE HANDLE. Small, always in the same place, and it says which way it will go. The
   collapse itself was already in this stylesheet with nothing to set the attribute. */
.grip{align-self:flex-start;font:600 12px/1 var(--mono,ui-monospace,monospace);
  color:var(--muted);background:var(--panel);border:1px solid var(--line);
  border-radius:6px;padding:5px 7px;cursor:pointer;margin-bottom:2px}
.grip:hover{color:var(--accent);border-color:var(--accent)}
.rail{position:relative}
.pal{border:1px solid var(--line);border-radius:9px;padding:9px;background:var(--panel)}
.palfold>summary{list-style:none;cursor:pointer;font-size:11.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);font-weight:600;padding:1px 0}
.palfold>summary::-webkit-details-marker{display:none}
.palfold>summary::before{content:"\25b8 ";color:var(--accent)}
.palfold[open]>summary::before{content:"\25be "}
.pal h4{margin:0 0 6px;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted)}
.tool{display:block;width:100%;text-align:left;margin:2px 0;padding:4px 8px;
  font-size:12px;border-radius:6px}
.tool[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
.tool[disabled]{opacity:.45}
/* THE ELEMENT MENU.  A tile carries the element's AVATAR -- the same marks the stage
   draws, at the same proportions -- beside its name, what it is for, and the gesture
   that places it.  The four kinds are three different actions and must not look alike:
   a stamp is armable and framed, a block is edited in place and is deliberately NOT a
   tile, because a budget is not something you drop on a canvas. */
.palgrp{margin:0 0 10px}
.palgrp h5{margin:0 0 5px;font-size:10px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);font-weight:700}
.palgrid{display:grid;gap:5px}
.pal-item{display:flex;gap:8px;align-items:center;width:100%;text-align:left;
  padding:4px 6px;border:1px solid var(--line);border-radius:8px;background:var(--panel);
  cursor:pointer}
.pal-item:hover,.pal-item:focus-visible,
.pal-item[aria-pressed="true"]{align-items:flex-start}
.pal-item:hover{border-color:var(--accent)}
.pal-item[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
.pal-item[aria-pressed="true"] .pal-why,
.pal-item[aria-pressed="true"] .pal-meta{color:#c8cfe6}
.pal-item[aria-pressed="true"] .pal-how{color:#fff}
.avatar{flex:0 0 auto;display:block;width:52px;height:32px;border-radius:6px;
  background:var(--soft);border:1px solid var(--line);overflow:hidden}
.avatar svg{display:block;width:52px;height:32px;background:transparent;border:0;
  border-radius:0;margin:0}
.pal-text{display:flex;flex-direction:column;gap:1px;min-width:0}
.pal-text b{font-size:12px;font-weight:650}
.pal-why,.pal-how,.pal-meta{display:none}
.pal-item:hover .pal-why,.pal-item:hover .pal-how,.pal-item:hover .pal-meta,
.pal-item:focus-visible .pal-why,.pal-item:focus-visible .pal-how,
.pal-item:focus-visible .pal-meta,
.pal-item[aria-pressed="true"] .pal-why,.pal-item[aria-pressed="true"] .pal-how,
.pal-item[aria-pressed="true"] .pal-meta{display:block}
.pal-why{font-style:normal;font-size:10.5px;line-height:1.32;color:var(--muted)}
.pal-how{font-style:normal;font-size:10px;color:var(--accent);font-weight:600}
.pal-meta{font-style:normal;font-size:10px;color:var(--muted)}
/* BLOCKS ARE NOT TILES.  A budget is not dropped on a canvas and must not look droppable. */
.palgrp[data-kind="block"] .pal-item{border-style:dashed;background:var(--soft)}
.palgrp[data-kind="component"] .avatar{width:60px;height:38px;background:var(--soft)}
.palgrp[data-kind="component"] .avatar svg{width:60px;height:38px}
.pal-item[data-blocked]{opacity:.55}
.cmp-tile{display:block}
.cmp-form{display:grid;grid-template-columns:auto 1fr;gap:3px 6px;align-items:center;
  margin:2px 0 9px 6px;padding:5px 6px;border-left:2px solid var(--line);
  font-size:10px}
.cmp-row{display:contents}
.cmp-k{color:var(--muted);letter-spacing:.03em}
.cmp-v{width:100%;min-width:0;box-sizing:border-box;font:inherit;font-size:10px;
  padding:1px 4px;border:1px solid var(--line);border-radius:3px;
  background:var(--panel);color:var(--ink)}
.cmp-v:disabled{opacity:.5}
.cmp-row[data-kind="inert"] .cmp-k{text-decoration:line-through}
.cmp-why{grid-column:2;color:var(--muted);font-size:9px}
.pal-item[data-blocked] .pal-how{color:var(--bad, #b4433a);font-weight:600}
.palgrp[data-kind="block"] .avatar{background:transparent}
.palgrp[data-kind="block"] .pal-how{color:var(--muted);font-weight:400}
.zonestrip{display:flex;flex-wrap:wrap;gap:4px}
.zonechip{display:flex;flex-direction:column;align-items:center;gap:2px;padding:3px 4px;
  border:1px solid var(--line);border-radius:7px;background:var(--panel);font-size:10px;
  cursor:pointer}
.zonechip .avatar{width:52px;height:26px}
.zonechip svg{width:52px;height:26px}
.zonechip[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
.formbtns{display:flex;gap:5px;margin-top:6px}
.formbtns button{padding:3px 9px;font-size:11px}
.rowlist{max-height:190px;overflow:auto}
.cards{display:flex;flex-wrap:wrap;gap:5px}
.card2{flex:1 1 88px;padding:5px 7px;font-size:11.5px;border-radius:7px;text-align:left}
.inert{color:var(--muted);font-style:italic}
.fieldrow{display:flex;align-items:center;gap:6px;margin:3px 0;font-size:11.5px}
.fieldrow label{flex:0 0 92px;color:var(--muted)}
.fieldrow input,.fieldrow select{flex:1 1 auto;min-width:0;border:1px solid var(--line);
  border-radius:5px;padding:2px 5px;font:11.5px/1.4 inherit;background:var(--bg);
  color:var(--ink)}
.badge.unchecked{background:transparent;border:1px dashed var(--muted);color:var(--muted)}
svg{width:100%;height:clamp(420px, 62vh, 760px);display:block;margin:0 auto;
background:var(--panel);border:1px solid var(--line);border-radius:8px;touch-action:none;
cursor:grab}
svg.drag{cursor:grabbing}
/* THE EDIT CURSOR.  `svg.editing{cursor:default}` used to sit here, which is the rule
   that made every element on the stage look inert in the one mode where all of them are
   live.  The live cursor is written to `style` by the editor -- a class cannot be read
   back in the headless harness, so cursor state written as a class is cursor state with
   no test -- and this is the static floor under it. */
svg[data-mode="edit"]{cursor:crosshair}
.step{margin-top:11px;font-size:13.5px}
.step b{color:var(--navy)}
.step code{background:var(--soft);padding:1px 6px;border-radius:4px;font-size:12.5px}
.why{margin-top:5px;color:var(--muted);font-size:12.5px}
.invalid{margin-top:9px;padding:8px 11px;border-radius:7px;font-size:12.5px;
  border:1px solid var(--z);color:var(--z);background:var(--bad_bg)}
.ctrl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
button{background:var(--panel);color:var(--ink);border:1px solid var(--line);
border-radius:6px;padding:7px 13px;font:inherit;cursor:pointer}
button.p{background:var(--navy);border-color:var(--navy);color:#fff;font-weight:600}
button:hover{border-color:var(--muted)}
select{border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit;
background:var(--panel)}
input[type=range]{flex:1 1 240px;min-width:170px;accent-color:var(--navy)}
.track{height:14px;border-radius:4px;background:var(--soft);border:1px solid var(--line);
display:flex;overflow:hidden;margin-top:9px}
.track i{display:block;height:100%}
/* THE TRANSPORT APPARATUS FOLDS AWAY WHILE THERE IS NOTHING TO TRANSPORT. */
body[data-noprog="1"] .ctrl,
body[data-noprog="1"] #track,
body[data-noprog="1"] #tl,
body[data-noprog="1"] #legend{display:none}
.legend{display:flex;gap:15px;flex-wrap:wrap;margin-top:12px;color:var(--muted);
font-size:12.5px;align-items:center}
.dot{width:10px;height:10px;border-radius:99px;display:inline-block;vertical-align:-1px;
margin-right:5px}
.sq{width:10px;height:10px;display:inline-block;vertical-align:-1px;margin-right:5px}
.bar{width:22px;height:5px;border-radius:3px;display:inline-block;vertical-align:2px;
margin-right:5px}
.badge{display:inline-block;border-radius:5px;padding:1px 6px;font-size:11px;
font-weight:650;margin:0 4px 4px 0}
.ok{background:var(--ok_bg);color:var(--cold)} .bad{background:var(--bad_bg);color:var(--z)}
.warn{background:var(--warn_bg);color:var(--warn_ink)}
h3{margin:0 0 6px;font-size:12px;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td{padding:3px 0;border-bottom:1px solid var(--soft)}
td:last-child{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
code{background:var(--soft);padding:1px 5px;border-radius:4px;font-size:12px}
.note{color:var(--muted);font-size:12px;margin-top:8px;line-height:1.5}
/* ---- the dock: Program / Architecture / Machine ---------------------------
   Two regimes, one markup, zero inline display writes from JS: JS only adds and
   removes `.on`, CSS decides whether that means a tab or a grid column. */
.dock{flex:1 1 320px;min-width:280px;max-width:400px;display:flex;
  flex-direction:column;gap:9px}
.row[data-layout="wide"] .dock{flex:1 1 100%;min-width:0}
.row[data-layout="narrow"] .rail{display:none}
.panes{display:flex;flex-direction:column;gap:10px;min-width:0}
.pane{display:none;flex-direction:column;gap:8px;min-width:0;padding:12px}
.pane.on{display:flex}
.tabs{display:flex;gap:6px}
.tab{padding:5px 12px;font-size:12.5px;border-radius:7px 7px 0 0}
.tab.on{background:var(--navy);border-color:var(--navy);color:#fff;font-weight:650}
@media (min-width:1180px){
  .row[data-layout="wide"] .tabs{display:none}
  /* auto-fit, not three hand-tuned columns: the dock went from three panes to five and a
     fixed three-column template overflows.  This lays out 3+2 at 1180px and five across
     on an ultrawide without a fourth set of hand-tuned widths. */
  .row[data-layout="wide"] .panes{display:grid;gap:12px;
    grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
  .row[data-layout="wide"] .pane{display:flex}
}
.ph{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ph h3{margin:0}
.grow{flex:1 1 auto}
.sub{color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.filter{border:1px solid var(--line);border-radius:6px;padding:3px 8px;font:12px/1.4
ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
background:var(--panel);color:var(--ink);max-width:150px;min-width:70px}
.tgl{padding:3px 9px;font-size:11.5px;border-radius:6px}
.tgl.on{background:var(--navy);border-color:var(--navy);color:#fff;font-weight:650}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:0;border-radius:0;padding:3px 9px;font-size:11.5px}
.seg button.on{background:var(--navy);color:#fff;font-weight:650}
.now{background:var(--soft);border:1px solid var(--line);border-radius:7px;padding:7px 9px;
font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
min-height:34px;overflow-x:auto}
.now b{color:var(--navy)} .now .mut{color:var(--muted);font-style:normal}
.now .bank{color:var(--muted)} .now .no{color:var(--z);font-weight:650}
.now .yes{color:var(--cold);font-weight:650}
.pf{color:var(--muted);font-size:11.5px;min-height:16px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.pf code{font-size:11px}
/* fixed row height is what makes index<->pixel arithmetic exact; nothing measures a row */
.lst{position:relative;overflow-y:auto;overflow-x:hidden;height:420px;overflow-anchor:none;
border:1px solid var(--line);border-radius:7px;background:var(--panel)}
.lst .pad{position:relative;width:100%}
.lst .win{position:absolute;left:0;right:0;top:0;will-change:transform}
.ln,.al{padding:0 8px;height:22px;line-height:22px;overflow:hidden;white-space:nowrap;
cursor:pointer;border-top:1px solid transparent;display:grid;gap:6px;
font:12px/22px ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
.ln{grid-template-columns:40px 52px 68px 1fr 56px 26px}
.al{grid-template-columns:30px 1fr}
.ln>i,.al>i{font-style:normal;min-width:0;overflow:hidden;text-overflow:ellipsis}
.ln:hover,.al:hover{background:var(--soft)}
.ln.cur,.al.cur{background:var(--soft);box-shadow:inset 3px 0 0 var(--accent)}
.ln.sel,.al.sel{outline:1px solid var(--accent);outline-offset:-1px}
.ln.bnd,.al.bnd{border-top-color:var(--line)}
.ln .i,.al .i{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}
.ln .op{height:15px;line-height:15px;margin-top:3px;border-radius:4px;text-align:center;
color:#fff;font-size:9.5px;font-weight:700;letter-spacing:.02em}
.ln .num{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
.ln .cl{color:var(--muted)}
.ln .a b,.al .a b{font-weight:600;color:var(--navy);cursor:pointer}
.ln .a b:hover,.al .a b:hover{text-decoration:underline}
.ln .a .mut,.al .a .mut{color:var(--muted)}
.ln .a .bad{color:var(--z);font-weight:700}
.al .cmt{color:var(--muted)} .al .hdr{color:var(--muted);font-weight:600}
.al .kw{color:var(--accent)}
.chip{position:absolute;right:16px;bottom:16px;z-index:2;border-radius:99px;
padding:4px 11px;font-size:11.5px;font-weight:650;background:var(--navy);color:#fff;
border:0;box-shadow:0 2px 8px rgba(0,0,0,.18)}
.chip.off{display:none}
.wrapl{position:relative}
.tl{position:relative;height:12px;margin-top:6px;border-radius:4px;overflow:hidden;
background:var(--soft);border:1px solid var(--line);display:flex;cursor:pointer}
.tl i{display:block;height:100%}
.playhead{position:absolute;top:0;bottom:0;width:2px;background:var(--accent)}
.help{position:fixed;inset:0;background:rgba(8,12,20,.72);z-index:9;padding:40px;
overflow:auto;color:#fff;font-size:13px}
.help.off{display:none}
.help div{max-width:640px;margin:0 auto;background:var(--panel);color:var(--ink);
border-radius:12px;padding:22px}
.help td{padding:2px 8px}

/* ---- the editor ------------------------------------------------------------ */
.stage{position:relative}
.ebar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
.etools{display:none;gap:8px;align-items:center;flex-wrap:wrap}
.ebar.edit .etools{display:flex}
.hud{position:absolute;z-index:3;pointer-events:none;background:var(--navy);color:#fff;
 border-radius:6px;padding:3px 8px;white-space:nowrap;transform:translate(12px,-26px);
 font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.hud.off{display:none}
.hud.warn{background:var(--warn_ink)}
.hud.bad{background:var(--z)}
.toasts{position:absolute;left:12px;bottom:12px;z-index:4;display:flex;
 flex-direction:column;gap:6px;max-width:min(560px,86%)}
.toast{background:var(--panel);border:1px solid var(--line);
 border-left:3px solid var(--accent);border-radius:7px;padding:7px 10px;font-size:12.5px;
 box-shadow:0 2px 10px rgba(0,0,0,.16);cursor:pointer}
.toast.bad{border-left-color:var(--z)}
.toast.warn{border-left-color:var(--warn_ink)}
.toast.ok{border-left-color:var(--teal)}
.srcwrap{display:flex;flex-direction:column;gap:6px;flex:1;min-height:0}
.srcwrap.off{display:none}
textarea.src{flex:1;min-height:180px;resize:vertical;width:100%;box-sizing:border-box;
 font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:7px;
 padding:8px}
textarea.src.out{min-height:120px;opacity:.85}
.srcerr{min-height:1.2em;color:var(--z);
 font:11.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
</style></head><body><main>
<div class="card">
  <div class="head">
    <div>
      <div class="kicker" id="kicker"></div>
      <h1 id="title"></h1>
      <p class="lede" id="lede"></p>
    </div>
    <div class="counters">
      <div class="counter"><span>Steps</span>
        <b><i class="now" id="cSteps">0</i><i class="of" id="cStepsOf"></i></b></div>
      <div class="counter"><span>Cost</span>
        <b><i class="now" id="cCost">0</i><i class="of" id="cCostOf"></i></b></div>
    </div>
  </div>
  <div class="metrics" id="metrics"></div>
  <div class="row" id="row">
    <!-- THE PALETTE.  Generated from `D.schema` (every CLOSED object, field for field)
         union `D.consumers` (the OPEN maps the schema cannot describe, each field with
         WHO READS IT) union `D.defaults` (dataclass defaults by reflection).  Never a
         literal list: a hand-written palette is a second source of truth, and 27 of the
         65 open fields are read by nothing at all, which the palette has to say. -->
    <aside class="rail" id="rail" data-collapsed="0">
      <button class="grip" id="railGrip" title="collapse the element rail ([)"
              aria-expanded="true">&#9664;</button>
      <section class="pal" id="palElements"><h4>Elements</h4><div id="palBody"></div></section>
      <details class="pal palfold" id="palStart"><summary>Start &mdash; new or template device</summary>
        <div id="palStartBody"></div></details>
      <section class="pal" id="palInspect"><h4>Selection</h4><div id="palInsp"></div></section>
    </aside>
    <div class="stage">
      <svg id="svg" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="step" id="status"></div>
      <div class="why" id="why"></div>
      <!-- The loud banner for "the compiled programme is not a programme for this
           device any more".  Shown and hidden through `style.display`, NEVER through a
           class: `classList` is a no-op in tests/shim.mjs, so a class-driven banner is a
           banner no harness can read, and the freeze would ship untested. -->
      <div class="invalid" id="invalid" style="display:none"></div>
      <div class="ctrl">
        <button class="p" id="play">Play</button>
        <button id="step">Next step</button>
        <button id="glide">Glide one step</button>
        <button id="phase">Next phase</button>
        <button id="reset">Reset</button>
        <button id="fit">Fit</button>
        <input type="range" id="slider" min="0" value="0">
        <select id="speed"><option value="1">1x</option>
        <option value="4" selected>4x</option>
        <option value="16">16x</option><option value="64">64x</option>
        <option value="256">256x</option></select>
        <select id="mode"><option value="role" selected>colour: role</option>
        <option value="heat">colour: heating</option></select>
      </div>
      <div class="ebar" id="ebar">
        <span class="seg"><button class="on" id="mPlay">Play</button
          ><button id="mEdit" title="edit the architecture (e)">Edit</button></span>
        <span class="etools" id="etools">
          <button class="tgl on" id="tSnap" aria-pressed="true"
                  title="snap to the lattice (hold alt to free, shift for quarter steps)">Snap</button>
          <button id="eUndo" title="undo (ctrl+z)">Undo</button>
          <button id="eRedo" title="redo (ctrl+shift+z)">Redo</button>
          <span class="sub" id="eCount">0 edits</span>
          <button class="tgl" id="eProb">0 problems</button>
          <select id="eWhich"><option value="py" selected>as Python</option>
            <option value="json">as .arch.json</option>
            <option value="tsir">as .tsir.json</option>
            <option value="edits">as edit ops</option></select>
          <button id="eCopy">Copy</button>
        </span>
        <span class="grow"></span><span class="sub" id="ePrice"></span>
      </div>
      <div class="hud off" id="hud"></div>
      <div class="toasts" id="toasts"></div>
      <div class="track" id="track" title="timeline by operation class"></div>
      <div class="tl" id="tl" title="the program in order; click to seek"></div>
      <div class="legend" id="legend"></div>
    </div>
    <div class="dock" id="dock" data-collapsed="0">
      <button class="grip" id="dockGrip" title="collapse the side panels (])"
              aria-expanded="true">&#9654;</button>
      <div class="tabs" id="tabs">
        <button class="tab on" id="tabP">Program</button>
        <button class="tab" id="tabA">Architecture</button>
        <button class="tab" id="tabM">Machine</button>
        <button class="tab" id="tabW">Write</button>
        <button class="tab" id="tabR">Report</button>
      </div>
      <div class="panes" id="panes">

        <section class="pane card on" id="paneP">
          <header class="ph"><h3>Hardware program</h3><span class="sub" id="pCount"></span>
            <span class="grow"></span>
            <input class="filter" id="pFilter" type="text" spellcheck="false"
                   placeholder="filter (/)">
            <button class="tgl on" id="pFollow" aria-pressed="true"
                    title="keep the executing instruction in view (f)">Follow</button>
          </header>
          <div class="now" id="pNow"></div>
          <div class="wrapl">
            <div class="lst" id="pScroll">
              <div class="pad" id="pPad"><div class="win" id="pWin"></div></div>
            </div>
            <button class="chip off" id="pChip"></button>
          </div>
          <footer class="pf" id="pFoot"></footer>
        </section>

        <section class="pane card" id="paneA">
          <header class="ph"><h3>Architecture</h3>
            <span class="seg"><button class="on" id="avB">Program</button
              ><button id="avD">Device</button><button id="avS"
              title="edit the architecture as source">Source</button></span>
            <span class="grow"></span>
            <input class="filter" id="aFilter" type="text" spellcheck="false"
                   placeholder="filter">
          </header>
          <div class="now" id="aNow"></div>
          <div class="wrapl">
            <div class="lst" id="aScroll">
              <div class="pad" id="aPad"><div class="win" id="aWin"></div></div>
            </div>
          </div>
          <div class="srcwrap off" id="aSrcWrap">
            <textarea class="src" id="eSrc" spellcheck="false"
              aria-label="the architecture as Python; edit it and the stage re-renders"></textarea>
            <div class="srcerr" id="eSrcErr"></div>
            <textarea class="src out" id="eOut" readonly spellcheck="false"
              aria-label="the edited architecture, ready to copy"></textarea>
          </div>
          <footer class="pf" id="aFoot"></footer>
        </section>

        <section class="pane card" id="paneM"><div id="side"></div></section>

        <!-- WRITE: the test programme, as the same Python subset the architecture lane
             already speaks.  `p = m.program(...)` and `p.<verb>(...)` are TWO new grammar
             productions and no new value form; there is deliberately no `with` and no
             indented suite, because `logicalLines` joins physical lines on bracket depth
             alone and a second competing rule would break the byte round trip. -->
        <section class="pane card" id="paneW">
          <header class="ph"><h3>Program</h3>
            <span class="sub" id="pwCount"></span>
            <span class="grow"></span>
            <button id="pwRun" class="p">Evaluate</button>
          </header>
          <textarea class="src" id="pwText" spellcheck="false"
            aria-label="the test program as Python; edit it and the stage re-renders"></textarea>
          <div class="srcerr" id="pwErr"></div>
          <footer class="pf" id="pwFoot"></footer>
        </section>

        <!-- REPORT: three registers and never a fourth -- BACKED, REFUSED, and NOT
             CHECKED HERE.  The header counts rather than saying "all". -->
        <section class="pane card" id="paneR">
          <header class="ph"><h3>Evaluation</h3>
            <span class="grow"></span>
            <span class="sub" id="rScope"></span>
          </header>
          <div id="report"></div>
        </section>
      </div>
    </div>
  </div>
</div>
<div class="help off" id="help"><div id="helpBody"></div></div>
</main>
<script id="data" type="application/json">__DATA__</script>
__ENGINE__
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const A = D.arch, P = D.program, PH = D.physics, L = D.layout;
const nodeById = {}; A.nodes.forEach(n => nodeById[n.id] = n);
const segById = {}; A.segments.forEach(s => segById[s.id] = s);
// THE PALETTE, shipped as data AND as CSS custom properties -- read as data first.
// `--<k>` is a round trip of `theme.PALETTE` through the stylesheet, and
// `getComputedStyle` is a stub in the headless harness that answers '#000000' for every
// property.  So a test could not tell a site drawn in its zone's colour from one drawn in
// black, and "the palette avatar is drawn in the same colour as the stage" was not a
// question any harness could ask.  A browser sees no change: the two tables are one table.
const PAL = D.palette || {};
const css = k => (PAL[k] !== undefined && PAL[k] !== null && PAL[k] !== '') ? PAL[k]
  : getComputedStyle(document.documentElement).getPropertyValue('--'+k).trim();
// One style resolution at load, not one per mark per frame: getComputedStyle forces a
// style recalc, and the old page called it ~460 times per draw().
const C = {};
for (const k of ['bg','panel','ink','muted','line','soft','data','x','z','anc','active',
  'rail','highway','compute','accent','arrow','navy','junction','corner','gold','loop',
  'teal','merge','grid','grid_faint','hot','cold','dc_idle','dc_hot','dc_well',
  'ion_stroke','neutral','rotate_alt','zone_data','zone_ancilla','zone_trap',
  'zone_tfactory','zone_load','zone_register','zone_other']) C[k] = css(k);
const zoneColour = z => C['zone_'+(z||'other')] || C.zone_other;
const clamp = (lo,v,hi) => Math.max(lo, Math.min(hi, v));

// ---------- layout: measured in Python, read here, never recomputed ----------
// px()/py() are the whole map.  L.sx and L.sy differ on a device whose bounding box is
// flatter than 2:1, because an isotropic fit would hand the entire vertical budget to a
// one-unit-tall band; every segment in every shipped device is axis-aligned, so nothing
// is skewed by that.  L.g is the minimum nearest-neighbour distance IN DRAWN PIXELS and
// every mark below is a fixed fraction of it.
const px = n => L.ox + n.x*L.sx, py = n => L.oy + n.y*L.sy;
const svg=document.getElementById('svg'), NS='http://www.w3.org/2000/svg';
const el=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
const gMetal=el('g',{}), gLoop=el('g',{}), gSeg=el('g',{}), gElec=el('g',{}),
      gHilite=el('g',{}), gNode=el('g',{}), gWell=el('g',{}), gIon=el('g',{}),
      gTop=el('g',{});
// gHilite sits BELOW gNode so the active-site disc never paints over the marker it is
// highlighting; gIon sits above everything structural.  gMetal is first and therefore
// underneath everything: it is a backdrop, not a mark, and it stays empty unless a
// technology was named.
svg.append(gMetal,gLoop,gSeg,gElec,gHilite,gNode,gWell,gIon,gTop);

// ---------- the metal, when a technology was named ----------
// DERIVED ELECTRODES, TRUE TO SCALE.  Every number below -- the transform, the fit, the
// scale bar -- was computed in Python by `qccd.phys.svg.metal_view_model`; this block
// reads them and does no arithmetic of its own.  It must not: `px()/py()` above is
// anisotropic by up to K_ANISO, and pushing a rectangle that is 99.5 um by 16 mm through
// it would draw a shape no fab could make.
//
// The consequence, said out loud rather than hidden: the underlay does NOT register with
// the schematic on top of it.  Registering would need this page's sx/sy to equal the
// technology's nm_per_unit_x / nm_per_unit_y, and on chain72 those are 1.0 and 0.634.
// One of the two views has to misstate a proportion, and it is not the one in nanometres.
if (D.metal){
  const M = D.metal;
  const inner = el('g',{transform:M.transform, opacity:0.5});
  for (const layer of M.layers){
    const gl = el('g',{fill:layer.fill, stroke:layer.stroke,
                       'stroke-width':String(M.nm_per_px),
                       'data-layer':layer.name, 'data-purpose':layer.purpose});
    for (const xy of layer.polys){
      const pts=[]; for(let i=0;i<xy.length;i+=2) pts.push(xy[i]+','+xy[i+1]);
      gl.append(el('polygon',{points:pts.join(' ')}));
    }
    inner.append(gl);
  }
  // The scale bar is what makes the mismatch legible instead of looking like a bug: it
  // is drawn through the SAME transform, so it is the one thing on the page whose length
  // means a physical distance.
  const B = M.bar_rect_nm, bar = el('g',{transform:M.transform});
  bar.append(el('rect',{x:B.x, y:B.y, width:B.w, height:B.h, fill:C.ink||'#334155'}));
  gMetal.append(inner, bar);
  const cap=el('text',{x:8, y:14, 'font-size':11, fill:C.muted||'#64748b'});
  cap.textContent = M.n_polys+' electrodes · '+M.technology+' · bar '+M.bar_label+
                    ' · '+M.note;
  gTop.append(cap);
}
svg.setAttribute('viewBox', `0 0 ${L.W} ${L.H}`);
// THE LAYOUT REGIME IS AN ATTRIBUTE, NOT A CLASS, and it is re-applied rather than set
// once.  `classList.add('wide')` ran ONLY at load, so a device that BECAME long-and-thin
// while you drew it never got the wide layout and one that stopped being wide kept it --
// and `classList` is a no-op in `tests/shim.mjs`, which made the layout regime the one
// piece of page state no harness could read.  `setAttribute` is stubbed, so this converts
// an untestable class into a testable attribute, the same trade the `#invalid` banner
// already made when it chose `style.display` over a class.
function applyLayout(){
  const w = (typeof window !== 'undefined' && window.innerWidth) || 1600;
  const mode = w < 900 ? 'narrow' : (L.wide && w >= 1180 ? 'wide' : 'tall');
  document.getElementById('row').setAttribute('data-layout', mode);
  return mode;
}
applyLayout();

// the trap axis at each node: the incident arm direction that most of its arms agree with
const AXIS = {};
{
  const arms = {};
  for (const sg of A.segments){
    const a=nodeById[sg.a], b=nodeById[sg.b]; if(!a||!b) continue;
    const dx=px(b)-px(a), dy=py(b)-py(a), h=Math.hypot(dx,dy); if(h<1e-9) continue;
    let ux=dx/h, uy=dy/h; if(ux<-1e-12 || (Math.abs(ux)<1e-12 && uy<0)){ux=-ux;uy=-uy;}
    (arms[sg.a]||(arms[sg.a]=[])).push([ux,uy]);
    (arms[sg.b]||(arms[sg.b]=[])).push([ux,uy]);
  }
  for (const n of A.nodes){
    const v = arms[n.id];
    if(!v || !v.length){ AXIS[n.id]={ux:1,uy:0}; continue; }
    let best=v[0], bs=-1;
    for(const c of v){ let s=0; for(const o of v) s+=Math.abs(c[0]*o[0]+c[1]*o[1]);
      if(s>bs){bs=s;best=c;} }
    AXIS[n.id]={ux:best[0], uy:best[1]};
  }
}

// ---------- the static picture: built once, never rebuilt per frame ----------
const SEGEL={}, SEGINFO={}, PAD_BY_SEG={}, SEG_BY_PAIR={},
      NODEEL={}, CAPTXT={};
// A SEGMENT'S ROLE COLOUR IS DERIVED FROM ITS LABELS, here as in Python.  `D.roles`
// is a snapshot keyed by segment id, taken when the page was emitted, and it has no
// answer for a segment the user creates -- every one of those drew as 'rail' whatever it
// was labelled, and a palette avatar promising a highway colour would have been promising
// something the stage would not produce.  `D.segment_roles` is `theme.SEGMENT_ROLE`
// itself, first match wins, exactly as `render.py::segment_role` reads it.
const SEGROLE = D.segment_roles || [];
function roleOf(sg){
  const ls = (sg && sg.labels) || [];
  for (let i=0;i<SEGROLE.length;i++) if (ls.indexOf(SEGROLE[i][0]) >= 0) return SEGROLE[i][1];
  return 'rail';
}
const ROLE = {};
function computeRoles(){
  for (const k in ROLE) delete ROLE[k];
  for (const sg of A.segments) ROLE[sg.id] = roleOf(sg);
}
computeRoles();

// THE SITE BAR'S LENGTH AND SLOT COUNT, taking the layout explicitly.  A palette avatar
// lays its own micro-device out, so a rule that closed over the stage's `L` could not be
// asked what a site looks like anywhere else -- and answering that question a second time
// is exactly how a menu picture drifts from the thing it depicts.
const _siteLen = (cap, L) => Math.min(L.site_max, (0.30 + 0.15*clamp(1, cap||1, 6))*L.g);
const _slots   = cap      => clamp(1, Math.min(cap||1, 6), 6);
const siteLen = cap => _siteLen(cap, L);
const slots   = cap => _slots(cap);

// A segment is normally a chord.  Where drawing it straight would put it through a node
// it does not touch -- the shipped ring's two corner docks sit exactly ON the end caps,
// and the dual-loop Cyclone's A-loop end caps would cross the D loop -- the layout
// engine hands back a midpoint offset and the segment becomes a quadratic instead.
// Everything that rides a segment (pads, and the ion itself) is evaluated on the same
// curve, so an ion never leaves its rail.
function bezPoint(I, t){
  if(I.cp){
    const u=t, v=1-u, bx=I.ax+I.dx, by=I.ay+I.dy;
    let tx=2*v*(I.cp.x-I.ax)+2*u*(bx-I.cp.x), ty=2*v*(I.cp.y-I.ay)+2*u*(by-I.cp.y);
    const h=Math.hypot(tx,ty)||1;
    return {x:v*v*I.ax+2*u*v*I.cp.x+u*u*bx, y:v*v*I.ay+2*u*v*I.cp.y+u*u*by,
            tx:tx/h, ty:ty/h};
  }
  const h=I.len||1;
  return {x:I.ax+I.dx*t, y:I.ay+I.dy*t, tx:I.dx/h, ty:I.dy/h};
}
function edgePoint(aId, bId, t){
  const sid=SEG_BY_PAIR[aId+'>'+bId], I=(sid!==undefined)?SEGINFO[sid]:null;
  if(I && I.cp){ const q=bezPoint(I, segById[sid].a===aId ? t : 1-t);
    return {x:q.x, y:q.y}; }
  const a=nodeById[aId], b=nodeById[bId]; if(!a||!b) return null;
  return {x:px(a)+(px(b)-px(a))*t, y:py(a)+(py(b)-py(a))*t};
}

// THE SCENE, not the page.  `buildStatic` used to close over the stage's globals, which
// made it the ONLY thing that could draw a site -- and therefore made a palette avatar a
// SECOND implementation of the site bar.  It now takes WHAT to draw, WHERE to put it and
// WHICH registries to fill, so the stage and a 64x40 avatar are the same call twice and
// the menu picture cannot drift from the canvas picture by construction.
//
// The body below is unchanged: this header is the whole difference.
function buildStatic(S){
  const A=S.A, L=S.L, AXIS=S.AXIS, ROLE=S.role, px=S.px, py=S.py, nodeById=S.byId;
  const gLoop=S.into.loop, gSeg=S.into.seg, gElec=S.into.elec, gNode=S.into.node;
  const SEGEL=S.reg.SEGEL, SEGINFO=S.reg.SEGINFO, PAD_BY_SEG=S.reg.PAD_BY_SEG,
        SEG_BY_PAIR=S.reg.SEG_BY_PAIR, NODEEL=S.reg.NODEEL, CAPTXT=S.reg.CAPTXT;
  const siteLen=c=>_siteLen(c,L), slots=c=>_slots(c);
  // --- loops: which segments form one closed orbit, and which way it is indexed -----
  const LOOPC=[C.anc, C.teal, C.accent, C.rotate_alt]; let li=0;
  for (const lid in (A.loops||{})){
    const seq=A.loops[lid]; if(!seq || seq.length<3) continue;
    const pts=[]; let cx=0, cy=0;
    for(const id of seq){const n=nodeById[id]; if(!n) continue;
      pts.push(px(n)+','+py(n)); cx+=px(n); cy+=py(n);}
    if(pts.length<3) continue;
    gLoop.append(el('polyline',{points:pts.join(' ')+' '+pts[0], fill:'none',
      stroke:LOOPC[li%4], 'stroke-width':L.sw_loop, opacity:0.15,
      'stroke-linejoin':'round','stroke-linecap':'round'}));
    const fs=Math.max(11, L.g*0.34);
    const t=el('text',{x:cx/pts.length, y:cy/pts.length + (li-0.5)*1.25*fs,
      'text-anchor':'middle',
      'dominant-baseline':'central', fill:LOOPC[li%4], 'fill-opacity':0.55,
      'font-size':fs, 'font-weight':700, 'pointer-events':'none'});
    t.textContent=lid; gLoop.append(t); li++;
  }

  // --- segments: the RF null the ions ride, in the deck's role colours -------------
  // butt caps, not round: 144 collinear round caps overlap by half a stroke each and
  // fuse the whole rail into one slab.
  let pads=0;
  const BOW=L.bows||{};
  for (const sg of A.segments){
    const a=nodeById[sg.a], b=nodeById[sg.b]; if(!a||!b) continue;
    const ax=px(a), ay=py(a), bx=px(b), by=py(b);
    const dx=bx-ax, dy=by-ay, len=Math.hypot(dx,dy);
    const I={ax,ay,dx,dy,len,cp:null};
    const bw=BOW[sg.id];
    if(bw && len>1e-6) I.cp={x:(ax+bx)/2 - (dy/len)*2*bw, y:(ay+by)/2 + (dx/len)*2*bw};
    SEGINFO[sg.id]=I;
    SEG_BY_PAIR[sg.a+'>'+sg.b]=sg.id; SEG_BY_PAIR[sg.b+'>'+sg.a]=sg.id;
    if(len<1e-6) continue;
    const role=ROLE[sg.id]||'rail', thin=role==='compute';
    const attr={stroke:C[role]||C.rail, fill:'none',
      'stroke-width': thin?L.sw_thin:L.sw_rail, 'stroke-linecap':'butt',
      opacity: thin?0.9:1};
    let ln;
    if(I.cp){ attr.d=`M ${ax} ${ay} Q ${I.cp.x} ${I.cp.y} ${bx} ${by}`; ln=el('path',attr); }
    else { attr.x1=ax; attr.y1=ay; attr.x2=bx; attr.y2=by; ln=el('line',attr); }
    // A SEGMENT NEEDS A TOOLTIP TOO. Nodes had one and rails did not, so hovering the
    // thing an ion actually travels along told you nothing -- not its id, not which loop
    // it belongs to, not the declared length the cost model reads.
    const stip=el('title',{});
    stip.textContent = sg.id+' · '+sg.a+' → '+sg.b+' · '+role+
                       (sg.loop?' · loop '+sg.loop:' · no loop')+
                       ' · length '+(+(sg.length===undefined?1:sg.length).toFixed(3))+
                       ' · cap '+(sg.cap===undefined?1:sg.cap);
    ln.append(stip);
    SEGEL[sg.id]=ln; gSeg.append(ln);
    pads += 2*clamp(1, Math.round(len/(L.pad_pitch||1)), 12);
  }
  // --- DC control electrodes (deck p.19/p.22): a stadium pill tiled along the trap
  // axis, mirrored across the RF null.  Shuttling IS these pads being ramped in
  // sequence, so the count comes from the segment's drawn LENGTH, never from the node
  // count -- the old page put three pads on a one-pixel segment.
  const sides = pads>4000 ? [1] : [1,-1];
  const tile  = pads<=8000;
  for (const sg of A.segments){
    const I=SEGINFO[sg.id]; if(!I || I.len<1e-6) continue;
    const k=clamp(1, Math.round(I.len/(L.pad_pitch||1)), 12), pitch=I.len/k;
    if(!tile){
      // too many to draw: the tiling becomes a dash pattern on the rail itself
      const ln=SEGEL[sg.id];
      if(ln) ln.setAttribute('stroke-dasharray', (0.72*pitch)+' '+(0.28*pitch));
      continue;
    }
    const list=[], w=0.72*pitch;
    for(let i=0;i<k;i++){
      const tt=(i+0.5)/k, q=bezPoint(I, tt);
      const nx=-q.ty, ny=q.tx, ang=Math.atan2(q.ty,q.tx)*180/Math.PI;
      for(const sign of sides){
        const cx=q.x+nx*L.pad_off*sign, cy=q.y+ny*L.pad_off*sign;
        const r=el('rect',{x:cx-w/2, y:cy-L.pad_t/2, width:w, height:L.pad_t,
          rx:L.pad_t/2, transform:`rotate(${ang} ${cx} ${cy})`,
          fill:C.dc_idle, opacity:0.42});
        gElec.append(r); list.push({el:r, cx, cy, t:tt});
      }
    }
    PAD_BY_SEG[sg.id]=list;
  }

  // --- nodes.  A junction is a SQUARE and holds no ions (the deck never draws one as
  // a circle -- a coloured circle means "ion" in this vocabulary).  A trapping site is
  // a bar along the trap axis whose LENGTH is its ion capacity, carrying one slot ring
  // per ion it can hold; a resting ion is drawn into a slot, so an empty slot stays
  // visible next to a full one and capacity is countable on the stage.
  for (const n of A.nodes){
    const x=px(n), y=py(n);
    const tip=el('title',{});
    // WHAT IT IS, WHAT IT HOLDS, AND WHERE IT IS. The position was missing and it is the
    // one field an editor needs constantly -- you cannot check a drag landed without it.
    tip.textContent = n.id+' · '+(n.kind==='junction'?'junction':(n.zone||'no zone'))+
                      ' · cap '+n.cap+' · deg '+n.deg+(n.corner?' · bend':'')+
                      ' · at ('+(+n.x.toFixed(3))+', '+(+n.y.toFixed(3))+')';
    if(n.kind==='junction' || (n.cap||0)===0){
      const h=L.r_junc;
      const r=el('rect',{x:x-h, y:y-h, width:2*h, height:2*h, fill:C.panel,
        stroke:(n.corner?C.corner:C.grid), 'stroke-width':L.sw_node});
      r.append(tip); r._nid=n.id; gNode.append(r); NODEEL[n.id]={kind:'junction', el:r};
      continue;
    }
    const m=slots(n.cap), len=siteLen(n.cap), ax=AXIS[n.id];
    const ang=Math.atan2(ax.uy, ax.ux)*180/Math.PI;
    const grp=el('g',{transform:`rotate(${ang} ${x} ${y})`});
    grp.append(tip);                       // <title> must be the group's first child
    const zc=zoneColour(n.zone), dock=n.deg>=3;
    const bar=el('rect',{x:x-len/2, y:y-L.site_t/2, width:len, height:L.site_t,
      rx:L.site_t/2, fill:zc, 'fill-opacity':0.16,
      stroke: dock?C.gold:(n.corner?C.corner:zc),
      'stroke-opacity': (dock||n.corner)?0.95:0.55,
      'stroke-width': (dock||n.corner)?L.sw_node*1.7:L.sw_node});
    grp.append(bar);
    const sr=Math.min(L.slot_r, 0.36*len/m);
    for(let i=0;i<m;i++){
      grp.append(el('circle',{cx:x+((i+0.5)/m-0.5)*len, cy:y, r:sr, fill:'none',
        stroke:zc, 'stroke-width':Math.max(0.7, L.sw_node*0.8), opacity:0.55}));
    }
    grp._nid=n.id; bar._nid=n.id;
    gNode.append(grp);
    NODEEL[n.id]={kind:'site', el:bar, grp, len, m, ang, ax};
    if((n.cap||0) > 6){
      // too many slots to count: say it in figures instead, beyond the bar's own end
      const d=len/2+L.g*0.12;
      const t=el('text',{x:x+ax.ux*d, y:y+ax.uy*d, 'text-anchor':'start',
        'dominant-baseline':'central', fill:zc, 'font-size':Math.max(8, L.g*0.20),
        'font-weight':650, 'pointer-events':'none'});
      gNode.append(t); CAPTXT[n.id]=t;
    }
  }
}

// The stage's own scene.  `A` and `L` are MUTATED IN PLACE by the editor's `rebuild()`
// and never reassigned, so one object literal stays live across every edit -- which is
// why nothing here is destructured at construction.
const STAGE = {A, L, AXIS, role:ROLE, px, py, byId:nodeById,
               into:{loop:gLoop, seg:gSeg, elec:gElec, node:gNode},
               reg:{SEGEL, SEGINFO, PAD_BY_SEG, SEG_BY_PAIR, NODEEL, CAPTXT}};
buildStatic(STAGE);

// slot offsets, in px along the trap axis, for k ions resting on one site
function slotOffsets(n, k){
  const m=slots(n.cap), len=siteLen(n.cap), pitch=len/m;
  const step=Math.min(pitch, 0.86*L.g/Math.max(k,1));
  const out=[];
  // CENTRED on the node, not left-packed into the bar: a lone ion in a cap-2 site was
  // being drawn half a pitch off its own node, which is both wrong to look at and the
  // reason an arrival appeared to shove its neighbour sideways.
  if(k<=m){ for(let j=0;j<k;j++) out.push((j-(k-1)/2)*pitch); }
  else { for(let j=0;j<k;j++) out.push((j-(k-1)/2)*step); }
  return {off:out, pitch:Math.min(pitch, step)};
}

// ---------- replay in the page ----------
const shuttleQ = PH.shuttle ? PH.shuttle.quanta : 0;
const splitQ = PH.split ? PH.split.quanta : 0;
const mergeQ = PH.merge ? PH.merge.quanta : 0;
const junctionQ = d => { const e = PH.junction_by_degree[String(d)]; return e ? e.quanta : 0; };

function applyFrame(st, f) {
  if (f.type==='init'){ for(const k in f.place){ st.pos[k]=f.place[k]; st.q[k]=0; } return; }
  if (f.type==='cool'){ const ions=(f.ions&&f.ions.length)?f.ions:Object.keys(st.pos);
    for(const i of ions) st.q[i]=0; return; }
  if (f.type!=='simd') return;
  if (f.shift){
    const [loop,delta]=f.shift, seq=A.loops[loop]; if(!seq) return;
    const k=seq.length, step=delta>=0?1:-1, idx={}; seq.forEach((n,i)=>idx[n]=i);
    for(let h=0;h<Math.abs(delta);h++){
      const moved={};
      for(const ion in st.pos){const i=idx[st.pos[ion]]; if(i!==undefined) moved[ion]=seq[((i+step)%k+k)%k];}
      for(const ion in moved){st.pos[ion]=moved[ion];
        st.q[ion]=(st.q[ion]||0)+shuttleQ+junctionQ((nodeById[moved[ion]]||{deg:0}).deg);}
    }
  } else if (f.moves){
    const ent=f.entails||[];
    for(const [ion,path] of f.moves){
      st.pos[ion]=path[path.length-1];
      // A node the programme names may not exist on the device CURRENTLY on the stage --
      // that is precisely the state `PROGRAM_STALE` freezes the animation on, and the
      // freeze happens after this table is built.  Charging zero for a hop across a node
      // that is gone is not a claim about anything: the frame it feeds is never drawn.
      let dq=0; for(let i=1;i<path.length;i++) dq+=shuttleQ+junctionQ((nodeById[path[i]]||{deg:0}).deg);
      if(ent.includes('split')) dq+=splitQ;
      if(ent.includes('merge')) dq+=mergeQ;
      st.q[ion]=(st.q[ion]||0)+dq;
    }
  }
}
// THE FOUR STAGE TABLES, and they are a FUNCTION of the frames rather than four consts.
// They have to be: a programme written in the browser produces a different frame list, and
// four page-scope constants computed once against the frames the page was emitted for
// cannot follow it.  `let`, and one function that reassigns all four together -- deriving
// three of them and forgetting the fourth is exactly the shape of change that produced the
// 14.68 px overlap defect, and `tests/census.mjs --program` is what would see it.
let states=[], before=[], cum=[], SLOTS=[], FINAL={pos:{},q:{}};
function deriveStage(frames){
  const st={pos:{},q:{}}, running={cost:0,steps:0};
  states=[]; before=[]; cum=[]; SLOTS=[];
  for(const f of frames){
    before.push(Object.assign({},st.pos));   // where everything was when the frame began
    applyFrame(st,f);
    running.cost += (f.cost||0); running.steps += (f.steps||0);
    states.push({pos:Object.assign({},st.pos), q:Object.assign({},st.q)});
    cum.push({cost:running.cost, steps:running.steps});
  }
  deriveSlots(frames);
  FINAL = st;
  return {states, before, SLOTS, cum, final: st};
}

// ---------- where an ion is PART WAY through a frame ----------
// A shuttle is a continuous translation, not a jump: the well is ramped from one
// electrode group to the next and the ion rides it. `u` in [0,1] is the fraction of the
// frame elapsed, and an ion in flight sits at the matching point along its own path.
// Every return carries x/y: a length-1 path (a loop_shift with delta 0) used to return an
// object with neither, and the caller wrote cx="undefined" onto a circle.
function pointOnPath(path, t){
  if(!path || !path.length) return null;
  if(path.length===1){ const n=nodeById[path[0]];
    return n ? {x:px(n), y:py(n), a:null, b:null, u:0} : null; }
  const span=(path.length-1)*Math.min(Math.max(t,0),1);
  const i=Math.min(Math.floor(span), path.length-2), local=span-i;
  const q=edgePoint(path[i], path[i+1], local);
  if(!q){ const n=nodeById[path[i]]||nodeById[path[i+1]];
    return n ? {x:px(n), y:py(n), a:null, b:null, u:0} : null; }
  return {x:q.x, y:q.y, a:path[i], b:path[i+1], u:local};
}

function pathsOf(f, prev){
  // ion -> the node sequence it walks during this frame
  const out={};
  if(!f) return out;
  if(f.shift){
    const [loop,delta]=f.shift, seq=A.loops[loop]; if(!seq) return out;
    const k=seq.length, step=delta>=0?1:-1, idx={}; seq.forEach((n,i)=>idx[n]=i);
    for(const ion in prev){
      const i0=idx[prev[ion]]; if(i0===undefined) continue;
      const path=[]; for(let h=0;h<=Math.abs(delta);h++) path.push(seq[((i0+step*h)%k+k)%k]);
      out[ion]=path;
    }
  } else if(f.moves){
    for(const [ion,path] of f.moves) out[ion]=path;
  }
  return out;
}
// ---------- slot order, carried forward across frames ----------
// Re-deriving the order inside each frame cannot satisfy both invariants at once: WITHIN
// a frame the order must not change (or two ions cross straight through each other), and
// ACROSS a boundary it must not change either (or an ion jumps a whole slot pitch). Both
// were measured on the shipped deck program -- 40% of frames overlapping, or a 6.5 px
// seam at every dock. So the order is computed ONCE, forward, exactly as a real trap
// evolves: departures leave, arrivals join at the end they approach from, and everyone
// already in the trap keeps their place.
function deriveSlots(frames){
  SLOTS.length = 0;
  let cur={};
  for(let i=0;i<frames.length;i++){
    const ps=pathsOf(frames[i]||{}, before[i]||{}), pos=states[i].pos, next={};
    for(const site in cur){
      const keep=cur[site].filter(ion => pos[ion]===site);
      if(keep.length) next[site]=keep;
    }
    for(const ion in pos){
      const site=pos[ion], n=nodeById[site]; if(!n) continue;
      const list=next[site]||(next[site]=[]);
      if(list.indexOf(ion)>=0) continue;
      const from=(ps[ion]&&ps[ion][0]) || (before[i]||{})[ion] || site;
      const o=nodeById[from], ax=AXIS[site];
      const d=(o&&ax) ? (px(o)-px(n))*ax.ux+(py(o)-py(n))*ax.uy : 0;
      if(d<0) list.unshift(ion); else list.push(ion);   // join at the end you arrive from
    }
    SLOTS[i]=next; cur=next;
  }
}
deriveStage(P.frames);

// THE SELF-CHECK IS THREE-VALUED, keyed on the SIZE of the evidence set.
//
// `D.checksum` is per-ion final n-bar as PYTHON computed it.  With no ions in it -- an
// empty canvas, or any page whose programme Python never replayed -- `for (const ion in
// D.checksum)` never ran, `drift` stayed 0, and the page printed
//
//     "self-check  ...  agrees with the Python verifier to 0.0e+0 quanta per ion."
//
// A green tick for a check that did not happen, in the one panel that asserts the page is
// trustworthy.  `evidence.self_check_ions` is what distinguishes "agreed" from "there was
// nothing to agree with", and it is non-zero on every shipped page, so the existing nine
// take an identical code path.
const EV = D.evidence || {self_check_ions: Object.keys(D.checksum||{}).length,
                          replayed_cycles: (P.frames||[]).length,
                          rules_evaluated: [], rules_all: []};
let drift=0, driftIon=null;
for(const ion in D.checksum){const d=Math.abs((FINAL.q[ion]||0)-D.checksum[ion]);
  if(d>drift){drift=d;driftIon=ion;}}

// ---------- is the compiled programme still a programme for THIS device? ----------
// `states`, `before`, `SLOTS` and `cum` are computed ONCE, above, against the device this
// page was emitted for.  `pathsOf`, `nodeById` and `AXIS` are re-derived by the editor
// against the device the user now has.  When the two describe different machines every
// ion on the stage is drawn from a MIXTURE of the two and none of them is where the
// programme says -- measured on cyclone_base with S5 removed: 1 ion parked on a site that
// no longer exists, 18 more up to 48.4 px from their compiled site, and a 14.68 px
// ion-on-ion overlap the unedited page does not have.
//
// A per-ion existence check cannot find that: 18 of the 19 wrong ions sit on nodes that
// still exist.  The damage is per-PROGRAMME -- a rigid rotation over a 71-node loop is a
// different programme from one over a 72-node loop, for every ion at once -- so there is
// no honest subset to keep drawing.
//
// null while the programme still fits the device.  Otherwise the SAME array that blocks
// the price: one derivation, two surfaces, so the page cannot refuse to show a number
// while continuing to show a picture.
let PROGRAM_STALE = null;

// ---------- draw ----------
const MODE=document.getElementById('mode');
const thr = PH.gate_threshold || 1;
function heat(q){ if(!q) return C.cold;
  const t=Math.min(1,Math.log10(1+q/thr)/2.2);
  return `rgb(${Math.round(15+t*165)},${Math.round(118-t*83)},${Math.round(110-t*86)})`; }

function ionColour(ion, f, stt){
  if (MODE.value === 'heat') return heat(stt.q[ion]||0);
  const role = (D.ion_roles||{})[ion];
  if (role === 'ancilla') return C.anc;
  const act = (f.pairs||[]).some(p => p[0]===ion || p[1]===ion);
  if (act) return f.check && f.check[0]==='Z' ? C.z : C.x;
  return C.data;
}

let frame=0, phase=1;   // phase: 0 = frame just started, 1 = frame complete
const slider=document.getElementById('slider');
slider.max=String(Math.max(0,P.frames.length-1));

// pooled marks: created on first sight of an ion, thereafter only mutated
const IONP={}, HALO=[]; let lastHot=[], lastSegHot=[], showLabels=L.labels;
function ionMarks(ion){
  let p=IONP[ion];
  if(!p){
    p={ w: el('ellipse',{rx:L.well_rx, ry:L.well_ry, fill:C.anc, opacity:0.16}),
        c: el('circle',{stroke:C.ion_stroke, 'stroke-width':L.sw_halo}),
        t: el('text',{'text-anchor':'middle','dominant-baseline':'central',
             fill:C.ion_stroke,'font-weight':700,'pointer-events':'none'}) };
    p.t.textContent = ion.replace(/^[da]/,'');
    gWell.append(p.w); gIon.append(p.c); gTop.append(p.t);
    IONP[ion]=p;
  }
  return p;
}
const hide=e=>e.setAttribute('display','none'), show=e=>e.removeAttribute('display');
let lastMark=[];
function markSite(id, act, over){
  const e=NODEEL[id]; if(!e || e.kind!=='site') return;
  const n=nodeById[id], zc=zoneColour(n.zone), dock=n.deg>=3, big=dock||n.corner||act;
  e.el.setAttribute('stroke', over?C.z:(act?C.active:(dock?C.gold:(n.corner?C.corner:zc))));
  e.el.setAttribute('stroke-opacity', big||over ? 0.95 : 0.55);
  e.el.setAttribute('stroke-width', (over?2.4:1)*(big?L.sw_node*1.7:L.sw_node));
}

let pathFrame=-1, pathCache={};
function pathsFor(i){
  if(pathFrame!==i){ pathCache=pathsOf(P.frames[i]||{}, before[i]||{}); pathFrame=i; }
  return pathCache;
}
function hopsIn(i){ let m=1; const ps=pathsFor(i);
  for(const k in ps) m=Math.max(m, ps[k].length-1); return m; }

// Undo the LAST frame's transient marks: the DC-pad ramp, the over-capacity segment
// restroke and the in-play site marks.  These three passes used to live inline in
// `draw()`, three sections apart.  `drawInvalid()` needs the identical three, and a
// second copy of "undo the last frame" is exactly the kind of duplication that lets one
// copy keep a pad lit while the other believes the stage is clear.
function clearTransients(){
  for(const p of lastHot){ p.el.setAttribute('fill',C.dc_idle);
    p.el.setAttribute('opacity',0.42); p.el.setAttribute('y',p.cy-L.pad_t/2);
    p.el.setAttribute('height',L.pad_t); }
  lastHot=[];
  for(const sid of lastSegHot){
    const e=SEGEL[sid]; if(!e) continue;
    const role=ROLE[sid]||'rail', thin=role==='compute';
    e.setAttribute('stroke', C[role]||C.rail);
    e.setAttribute('stroke-width', thin?L.sw_thin:L.sw_rail);
  }
  lastSegHot=[];
  for(const id of lastMark) markSite(id, false, false);
  lastMark=[];
}

// The programme does not fit the device, so nothing derived from the programme is drawn.
//
// The DEVICE keeps being drawn, at the new layout: it is real, and it is the thing the
// user is manipulating.  What is withdrawn is the ion layer, the occupancy readout and
// the cost counters -- every one of which is a claim about a programme that no longer
// exists.  Nothing is frozen in its last pose either: a pose is itself a claim about
// where an ion is, and there is no pose that is simultaneously true of the compiled
// programme and drawable on the current device.
function drawInvalid(){
  clearTransients();
  for(const ion in IONP){ const p=IONP[ion]; hide(p.w); hide(p.c); hide(p.t); }
  for(const c of HALO) hide(c);
  for(const id in CAPTXT) CAPTXT[id].textContent =
    '–/' + ((nodeById[id]||{}).cap ?? '?');
  document.getElementById('cSteps').textContent = '—';
  document.getElementById('cCost').textContent  = '—';
  document.getElementById('status').innerHTML =
    `<b>programme invalid</b> &mdash; ${esc(PROGRAM_STALE.why)}`;
  document.getElementById('why').textContent =
    `These ${P.frames.length} frames were compiled against the device as it was before `
    + `this edit; nothing on the stage would be where the programme says. Undo, or `
    + `recompile: python -m qccd run ${A.name} --program ${P.name}`;
}

function draw(){
  // THE ONLY CORRECT PLACE FOR THIS GUARD.  `census.mjs`, `panels.mjs` and `editor.mjs`
  // all drive the stage by setting `frame`/`phase` and calling `draw()` directly, and
  // inside the page `sizeStage()`, `MODE.onchange`, `runGlide()`, `slider.oninput` and
  // `seek()` all reach it too.  A guard anywhere else leaves a path that paints a stale
  // programme, which is how the 14.68 px overlap got onto the stage in the first place.
  if(PROGRAM_STALE){ drawInvalid(); return; }
  const stt=states[frame]||{pos:{},q:{}}, f=P.frames[frame]||{};
  const paths=pathsFor(frame);
  const hops=hopsIn(frame);
  const t = hops>1 ? phase : phase*phase*(3-2*phase);   // ramp a single hop, not a rotation

  // --- where every ion is, and which SLOT of which site it occupies ----------------
  // A flying ion departs from its source slot and arrives in its destination slot.
  // Interpolating to the bare node centre instead did two visible kinds of damage: an
  // ion converging on an occupied site was drawn straight on top of the sibling resting
  // there (40% of frames on the shipped deck program, every dock and every undock), and
  // EVERY ion jumped by one slot offset at each frame boundary, because `pointOnPath`
  // starts and ends at the node while a resting ion is drawn off it. Both are the same
  // bug: flight and rest disagreed about where an ion in a site actually sits.
  const live={}, segLoad={}, flying={};
  const natural=(a,b)=>a.localeCompare(b,undefined,{numeric:true,sensitivity:'base'});
  const srcOf={}, dstOf={}, occS={}, occ={};
  for(const ion in stt.pos){
    const path=paths[ion];
    const a=path?path[0]:stt.pos[ion], b=path?path[path.length-1]:stt.pos[ion];
    if(!nodeById[a] || !nodeById[b]) continue;
    srcOf[ion]=a; dstOf[ion]=b;
    (occS[a]||(occS[a]=[])).push(ion);
    (occ[b]||(occ[b]=[])).push(ion);      // occupancy at the END of the frame
  }
  // Slots are ordered ALONG THE SITE'S OWN AXIS by where each ion comes from (arrivals)
  // or goes to (departures), so no ion has to cross a sibling to reach its slot. Ordering
  // by id instead handed a docking ion the far slot and sent it straight through the
  // ancilla waiting in the near one. Natural order breaks ties -- and it has to be
  // natural, because plain .sort() puts 'd10' before 'd2' and the slot an ion holds then
  // changes from frame to frame as the population around it changes.
  const proj=(id, other)=>{
    const n=nodeById[id], o=nodeById[other], ax=AXIS[id];
    if(!n || !o || !ax) return 0;
    return (px(o)-px(n))*ax.ux + (py(o)-py(n))*ax.uy;
  };
  // ONE key for both lists -- where the ion starts this frame. Using different keys for
  // the two ends lets the order invert mid-frame, and two ions swapping slots pass
  // straight through each other (measured: 350 frames with centres exactly coincident).
  // With a single key the order is fixed for the whole frame, so an ion arriving from
  // the precomputed order: end of the previous frame for the start state, this frame's
  // for the end state, so the two agree at the seam AND agree with each other
  const ordEnd=SLOTS[frame]||{}, ordStart=(frame>0?SLOTS[frame-1]:null)||{};
  // an ion must pass a trap-mate when someone stays behind on the side it exits towards
  const mustPass = (ion, A, B) => {
    if(A.node === B.node) return false;
    const side = (here, there) => {
      const n=nodeById[here], o=nodeById[there], ax=AXIS[here];
      return (n&&o&&ax) ? (px(o)-px(n))*ax.ux+(py(o)-py(n))*ax.uy : 0;
    };
    const blocked = (list, ion2, dir, resident) => {
      const i=list.indexOf(ion2);
      if(i<0 || list.length<2 || !dir) return false;
      for(let k=0;k<list.length;k++){
        if(k===i) continue;
        if((dir>0 ? k>i : k<i) && resident.indexOf(list[k])>=0) return true;
      }
      return false;
    };
    // leaving: does anyone STAY BEHIND on the side it exits towards?
    if(blocked(ordStart[A.node]||[], ion, side(A.node,B.node), ordEnd[A.node]||[]))
      return true;
    // arriving: is anyone ALREADY THERE between the end it enters by and its own slot?
    return blocked(ordEnd[B.node]||[], ion, side(B.node,A.node), ordStart[B.node]||[]);
  };
  const orderBy = (ord,id) => (a,b) => {
    const L=ord[id]||[], ia=L.indexOf(a), ib=L.indexOf(b);
    return ((ia<0?1e9:ia)-(ib<0?1e9:ib)) || natural(a,b);
  };
  for(const id in occS) occS[id].sort(orderBy(ordStart,id));
  for(const id in occ)  occ[id].sort(orderBy(ordEnd,id));
  const slotAt=(id, list, ion)=>{
    const n=nodeById[id], k=list.length, j=Math.max(0, list.indexOf(ion));
    const s=slotOffsets(n, k), ax=AXIS[id], o=s.off[j];
    return {ox:ax.ux*o, oy:ax.uy*o, pitch:k>1?s.pitch:0, node:id};
  };

  const over={};
  for(const ion in srcOf){
    const path=paths[ion];
    const A=slotAt(srcOf[ion], occS[srcOf[ion]], ion);
    const B=slotAt(dstOf[ion], occ[dstOf[ion]], ion);
    if(path && phase<1){
      const q=pointOnPath(path, t); if(!q || !isFinite(q.x)) continue;
      // Does this ion have to get PAST a trap-mate to leave? Two ions exchanging order
      // inside one trap is a real, expensive event -- it is what the corpus calls a swap,
      // and it is not free. Drawing it as a straight line through the neighbour would
      // both overlap and quietly misrepresent the physics, so it is drawn going around.
      const bow = mustPass(ion, A, B) ? L.swap_bow*4*t*(1-t) : 0;
      let bx=0, by=0;
      if(bow){ const ax=AXIS[A.node]; bx=-ax.uy*bow; by=ax.ux*bow; }
      // an ion moving into or out of an OCCUPIED trap gets no flourish -- it needs the
      // room, not the bulk. Approach can be perpendicular to the slot axis (a corner
      // site stacks its slots across the rail), where no detour applies but the swell
      // alone is still enough to put two circles through each other.
      const tight = ((ordStart[A.node]||[]).length>1) || ((ordEnd[B.node]||[]).length>1);
      live[ion]={x:q.x+A.ox+(B.ox-A.ox)*t+bx, y:q.y+A.oy+(B.oy-A.oy)*t+by,
                 fly:true, tt:t, pitchA:A.pitch, pitchB:B.pitch,
                 swap:!!bow, tight:tight};
      flying[ion]=q;
      if(q.a && q.b){ const sid=SEG_BY_PAIR[q.a+'>'+q.b];
        if(sid!==undefined) segLoad[sid]=(segLoad[sid]||0)+1; }
    } else {
      // A resting ion interpolates its slot too. Its site's POPULATION changes during
      // the frame -- an ion leaving frees a slot and the one staying behind shifts into
      // the middle -- so pinning it to the end-state slot made it jump the moment the
      // frame began, straight into the ion still departing. Source slot to destination
      // slot, same rule as a flier: every ion on the stage obeys one law.
      const a=nodeById[A.node], b=nodeById[B.node];
      const x0=px(a)+A.ox, y0=py(a)+A.oy, x1=px(b)+B.ox, y1=py(b)+B.oy;
      const u = phase<1 ? t : 1;
      live[ion]={x:x0+(x1-x0)*u, y:y0+(y1-y0)*u, fly:false, node:B.node,
                 pitch:(A.pitch&&B.pitch) ? A.pitch+(B.pitch-A.pitch)*u
                                          : (B.pitch||A.pitch)};
    }
  }
  for(const id in occ){ const k=occ[id].length;
    if(k > (nodeById[id].cap||0)) over[id]=k; }

  // --- the sites in play, under the node markers they are highlighting ------------
  const sites=f.sites||[];
  for(let i=0;i<Math.max(sites.length, HALO.length);i++){
    if(i>=HALO.length){ const c=el('circle',{fill:C.active, opacity:0.5});
      HALO.push(c); gHilite.append(c); }
    const c=HALO[i];
    if(i<sites.length && nodeById[sites[i]]){
      const n=nodeById[sites[i]];
      c.setAttribute('cx',px(n)); c.setAttribute('cy',py(n));
      c.setAttribute('r',L.r_active); show(c);
    } else hide(c);
  }

  // --- energized DC pads: a travelling ramp, not a whole segment flashing ---------
  clearTransients();
  for(const ion in flying){
    const q=flying[ion]; if(!q.a||!q.b) continue;
    const sid=SEG_BY_PAIR[q.a+'>'+q.b]; if(sid===undefined) continue;
    const I=SEGINFO[sid], list=PAD_BY_SEG[sid]; if(!I||!list) continue;
    const u = (segById[sid].a===q.a) ? q.u : 1-q.u;
    // light the pad under the ion and its immediate neighbours -- the deck's p.4
    // picture of a well being handed from one electrode to the next.  Comparing a
    // normalised parameter against a pixel distance lit every pad on the segment at
    // once, which is what made a rail read as a solid braid rather than a tiling.
    let near=-1, best=Infinity;
    for(let i=0;i<list.length;i++){
      const d=Math.abs(list[i].t-u);
      if(d<best){best=d; near=i;}
    }
    for(let i=0;i<list.length;i++){
      if(Math.abs(i-near)<=1){
        const p=list[i];
        const h=L.pad_t*(i===near?1.45:1.2);
        p.el.setAttribute('fill',C.dc_hot);
        p.el.setAttribute('opacity', i===near ? 0.95 : 0.55);   // the ramp has a peak
        p.el.setAttribute('y',p.cy-h/2); p.el.setAttribute('height',h);
        lastHot.push(p);
      }
    }
  }

  // --- R3: at most `segment.capacity` ions on one shuttling segment.  The number is
  // exported and the rule is live, so a violation should be as loud on the stage as it
  // is in the verifier's badge.
  for(const sid in segLoad){
    const sg=segById[sid], e=SEGEL[sid];
    if(!sg || !e || segLoad[sid] <= (sg.cap==null?1:sg.cap)) continue;
    e.setAttribute('stroke', C.z);
    e.setAttribute('stroke-width', L.sw_rail+2);
    lastSegHot.push(sid);
  }

  // --- a site in play, or one over its capacity, restrokes its own bar ------------
  // Only the marks whose state changed are written; the other 287 stay untouched.
  for(const id of sites) if(NODEEL[id] && NODEEL[id].kind==='site'){
    markSite(id, true, over[id]!==undefined); lastMark.push(id); }
  for(const id in over) if(NODEEL[id] && NODEEL[id].kind==='site' && lastMark.indexOf(id)<0){
    markSite(id, false, true); lastMark.push(id); }
  for(const id in CAPTXT) CAPTXT[id].textContent=((occ[id]||[]).length)+'/'+nodeById[id].cap;

  // --- ions ----------------------------------------------------------------------
  const nMoving=Object.keys(flying).length;
  const wells = nMoving<=40;   // a 144-ion rigid rotation must not become one indigo slab
  for(const ion in IONP){ if(!(ion in live)){const p=IONP[ion]; hide(p.w);hide(p.c);hide(p.t);} }
  for(const ion in live){
    const pt=live[ion], p=ionMarks(ion);
    const act = (f.pairs||[]).some(q=>q[0]===ion||q[1]===ion);
    // an ion in flight, or one taking part in this gate, is the mark the eye should
    // find: it gets the full radius.  A spectator at rest is a bead on the rail, and
    // shrinks further when it has to share a site with another ion.
    let r = (pt.fly||act) ? L.r_ion : L.r_rest;
    if(pt.fly){
      // resting size in the slot it leaves, full radius mid-flight (the mark the eye
      // should find), resting size again in the slot it arrives at -- so it settles
      // beside a sibling rather than on top of one, and neither position nor radius
      // jumps at the frame boundary
      const rA = Math.min(L.r_rest, pt.pitchA ? 0.44*pt.pitchA : L.r_rest);
      const rB = Math.min(L.r_rest, pt.pitchB ? 0.44*pt.pitchB : L.r_rest);
      // no swelling while threading past a trap-mate: it needs the room, not the bulk
      const bulge = (pt.swap||pt.tight) ? 0 : 4*pt.tt*(1-pt.tt);  // 0 at ends, 1 mid-flight
      r = rA + (rB-rA)*pt.tt + (L.r_ion - Math.max(rA,rB))*bulge;
    } else if(pt.pitch) r = Math.min(r, 0.44*pt.pitch);
    if(pt.fly && wells){
      p.w.setAttribute('cx',pt.x); p.w.setAttribute('cy',pt.y); show(p.w);
    } else hide(p.w);
    p.c.setAttribute('cx',pt.x); p.c.setAttribute('cy',pt.y);
    p.c.setAttribute('r', r);
    p.c.setAttribute('fill', ionColour(ion,f,stt));
    show(p.c);
    if(showLabels && r >= 6){
      const fs=Math.min(0.66*r, 1.5*r/Math.max(2, p.t.textContent.length));
      p.t.setAttribute('x',pt.x); p.t.setAttribute('y',pt.y);
      p.t.setAttribute('font-size',fs); show(p.t);
    } else hide(p.t);
  }

  const c=cum[frame]||{cost:0,steps:0};
  document.getElementById('cSteps').textContent=c.steps.toLocaleString();
  document.getElementById('cCost').textContent=c.cost.toLocaleString();

  document.getElementById('status').innerHTML =
    // `Step 1 / 0 - undefined` is what this printed with no programme: `f` was `{}` and
    // `frame+1` counted a step that does not exist.  A design tool opens on exactly that
    // state, so it is the first sentence a new user reads.
    // and the same condition folds the transport controls away -- set BOTH ways, or
    // writing a programme would leave the page still hiding the controls for it
    (P.frames.length === 0
      ? (document.body.setAttribute('data-noprog','1'),
         `<b>no programme</b> &mdash; write one in the <b>Write</b> pane, or open a device `)+
        `that carries one`
      : (document.body.removeAttribute('data-noprog'),
         `<b>Step ${frame+1}</b> / ${P.frames.length} &mdash; <code>${f.type}</code>`)) +
    (f.cls?` <code>${f.cls}</code>`:'') +
    (f.hops?` &middot; ${f.hops} hop${f.hops>1?'s':''}`:'') +
    (f.check?` &middot; <b>${f.check}</b>`:'') +
    (f.batch!==undefined?` &middot; batch ${f.batch+1}`:'') +
    (f.cost!==undefined?` <span style="color:var(--muted)">[cost ${f.cost} &middot; steps ${f.steps}]</span>`:'');
  const nMoved=f.shift?Object.keys(stt.pos).length:(f.moves?f.moves.length:0);
  document.getElementById('why').textContent =
    f.type==='simd'
      ? `one class (${f.cls}), ${nMoved} ions moving together; the machine allows `
        + `${P.max_simd_classes} class per step, so nothing of a different class can join`
      : f.type==='gate'
        ? `${(f.pairs||[]).length} contact${(f.pairs||[]).length===1?'':'s'} in this batch`
        : f.type==='cool' ? 'global cooling: one operation, every ion'
        : f.type==='measure' ? 'ancilla readout' : (f.type==='reset'?'ancilla reset':'');
  slider.value=String(frame);
  syncCursor();
}

// ---------- drop a design onto the page ----------
// The file is the authoritative artefact, so getting one IN has to be as easy as dragging
// it onto the picture. `importText` validates the shape, backs up the current state and
// restores it if anything throws -- import refuses exactly what export refuses to write.
(function(){
  const stage = document.querySelector('.stage');
  if(!stage || !stage.addEventListener) return;
  const stop = e => { e.preventDefault(); e.stopPropagation(); };
  stage.addEventListener('dragover', e => { stop(e); stage.setAttribute('data-drop','1'); });
  stage.addEventListener('dragleave', e => { stop(e); stage.removeAttribute('data-drop'); });
  stage.addEventListener('drop', e => {
    stop(e); stage.removeAttribute('data-drop');
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if(!f || !window.EDITOR || !EDITOR.importText) return;
    const fr = new FileReader();
    fr.onload = () => {
      const r = EDITOR.importText(String(fr.result));
      if(!r.ok && EDITOR.toast) EDITOR.toast('bad', (r.problems[0]||{}).message || 'refused');
    };
    fr.readAsText(f);
  });
})();

// ---------- panel collapse: give the canvas its width back ----------
// The rail's collapsed CSS shipped with nothing to set the attribute, so the affordance
// existed and could not be reached -- which is most of "the two menu bars take too much
// space". `[` and `]` mirror the two handles; `\` collapses both, which is the one people
// actually want while drawing.
function foldPanel(el, on){
  if(!el) return;
  const now = on === undefined ? el.getAttribute('data-collapsed') !== '1' : !!on;
  el.setAttribute('data-collapsed', now ? '1' : '0');
  const g = el.querySelector ? null : null;
  const btn = document.getElementById(el.id === 'rail' ? 'railGrip' : 'dockGrip');
  if(btn){
    btn.setAttribute('aria-expanded', now ? 'false' : 'true');
    btn.innerHTML = el.id === 'rail' ? (now ? '&#9654;' : '&#9664;')
                                     : (now ? '&#9664;' : '&#9654;');
    btn.title = (now ? 'show ' : 'collapse ') +
                (el.id === 'rail' ? 'the element rail ([)' : 'the side panels (])');
  }
  sizeStage();
}
const railEl = document.getElementById('rail'), dockEl = document.getElementById('dock');
const railGrip = document.getElementById('railGrip');
const dockGrip = document.getElementById('dockGrip');
if(railGrip) railGrip.onclick = () => foldPanel(railEl);
if(dockGrip) dockGrip.onclick = () => foldPanel(dockEl);
window.PANELS = { fold: foldPanel, rail: railEl, dock: dockEl,
  state: () => ({ rail: railEl && railEl.getAttribute('data-collapsed') === '1',
                  dock: dockEl && dockEl.getAttribute('data-collapsed') === '1' }) };

// ---------- fit, zoom, pan: the viewBox is the only thing that moves ----------
let VB={x:0,y:0,w:L.W,h:L.H};
const applyVB=()=>svg.setAttribute('viewBox',`${VB.x} ${VB.y} ${VB.w} ${VB.h}`);
function fit(){ VB={x:0,y:0,w:L.W,h:L.H}; applyVB(); }
function sizeStage(){
  // never render larger than 1 css px per user unit, and never taller than 78% of the
  // viewport, so a 900x900 grid does not push the controls below the fold
  // a wide device puts the dock BELOW the stage, so the stage may not own 78% of the
  // viewport or the listing starts under the fold and "keep the cursor in view" is
  // invisible
  // THE CANVAS IS FURNITURE, NOT A FRAME AROUND THE CONTENT. It used to be capped at
  // `L.W` css px, so its SHAPE followed the device: a 1600x132 chain drew a 61 px strip
  // and a 900x900 grid drew a square, and the drawing area changed every time you opened
  // a different machine. The box is now a constant set in CSS and the device is fitted
  // inside it by `preserveAspectRatio="xMidYMid meet"`; zoom and pan reach the rest.
  const wide = document.getElementById('row').getAttribute('data-layout') === 'wide';
  svg.style.maxHeight = Math.round((wide ? 0.52 : 0.72)*window.innerHeight) + 'px';
  sizeLists();
  const was=showLabels;
  const scale=(svg.clientWidth||L.W)/L.W;
  showLabels = (0.66*L.r_ion*scale) >= 8;
  if(was!==showLabels) draw();
}
// THE ZOOM ITSELF, callable without an Event -- the same rule the editor's gestures
// follow, and for the same reason: `tests/shim.mjs` cannot synthesize a wheel event, so a
// zoom that lives only inside a listener is a zoom no harness can check. The listener
// below is a three-line adapter onto this.
function zoomAt(clientX, clientY, deltaY){
  // ANCHOR ON THE MODEL POINT UNDER THE CURSOR, not on a fraction of the element box.
  // `(clientX-r.left)/r.width` is the viewBox fraction only while the SVG FILLS its
  // element. Since the canvas became a constant size it letterboxes, so that fraction is
  // wrong by the margin and the picture crept away from the pointer as you zoomed --
  // the same defect, in a second place, as the click misalignment.
  // `typeof EDITOR`, not `window.EDITOR`: the two are the same object in a browser but
  // NOT under the test shim, whose `window` is a plain stand-in. Reaching through
  // `window` silently took the fallback in every headless run, which is exactly where the
  // bug would have gone unmeasured.
  //
  // The fallback carries the same letterbox correction rather than the naive fraction --
  // a wrong answer on the path nobody watches is how this defect got in twice already.
  const fit = (function(){
    const r = svg.getBoundingClientRect();
    const w = Math.max(1, r.width), h = Math.max(1, r.height);
    const s = Math.min(w / Math.max(1e-9, VB.w), h / Math.max(1e-9, VB.h));
    return { r: r, s: s, ox: (w - VB.w * s) / 2, oy: (h - VB.h * s) / 2 };
  })();
  const m = (typeof EDITOR !== 'undefined' && EDITOR.toModel)
    ? EDITOR.toModel(clientX, clientY)
    : { x: VB.x + (clientX - fit.r.left - fit.ox) / fit.s,
        y: VB.y + (clientY - fit.r.top  - fit.oy) / fit.s };
  const k=clamp(0.12*L.W, VB.w*Math.pow(1.0018, deltaY), 2.5*L.W);
  const nh=k*(L.H/L.W);
  // keep `m` exactly under the cursor: its offset from the origin scales with the box
  VB.x = m.x - (m.x - VB.x) * (k / VB.w);
  VB.y = m.y - (m.y - VB.y) * (nh / VB.h);
  VB.w = k; VB.h = nh; applyVB();
  return { x: VB.x, y: VB.y, w: VB.w, h: VB.h };
}
svg.addEventListener('wheel', e=>{
  if(!(e.ctrlKey||e.metaKey||e.shiftKey)) return;   // plain wheel still scrolls the page
  e.preventDefault();
  zoomAt(e.clientX, e.clientY, e.deltaY);
}, {passive:false});
// published so a harness can zoom, and so the editor can fit to a selection
window.VIEW = { zoomAt: zoomAt, fit: fit, vb: () => ({x:VB.x,y:VB.y,w:VB.w,h:VB.h}) };
let drag=null;
svg.addEventListener('pointerdown', e=>{
  // ONE ARBITER decides who owns a press, and it lives in the editor.  This handler had
  // no mode guard at all, so dragging a node panned the stage UNDERNEATH the node at the
  // same time -- both handlers ran, and the picture slid out from under the gesture.
  // `EDITOR.claimEvent` is the only rule; nothing here restates it.
  if (window.EDITOR && EDITOR.claimEvent && EDITOR.claimEvent(e) !== 'pan') return;
  drag={x:e.clientX,y:e.clientY,vx:VB.x,vy:VB.y};
  svg.setPointerCapture(e.pointerId); svg.classList.add('drag');});
svg.addEventListener('pointermove', e=>{ if(!drag) return;
  const r=svg.getBoundingClientRect();
  VB.x=drag.vx-(e.clientX-drag.x)*VB.w/r.width;
  VB.y=drag.vy-(e.clientY-drag.y)*VB.h/r.height; applyVB();});
const endDrag=()=>{drag=null; svg.classList.remove('drag');};
svg.addEventListener('pointerup', endDrag); svg.addEventListener('pointercancel', endDrag);
let rt=null;
window.addEventListener('resize', ()=>{clearTimeout(rt); rt=setTimeout(sizeStage,120);});

// ---------- chrome ----------
const M=D.metrics, hw=A.hardware, rl=D.rules, SUM=A.summary||{};
const fmt=(x,d=0)=>(x==null)?'-':Number(x).toLocaleString(undefined,{maximumFractionDigits:d});
document.getElementById('kicker').textContent = D.kicker || 'ROUTING SCHEME';
document.getElementById('title').textContent = D.headline || (A.name+' · '+P.name);
document.getElementById('lede').textContent = D.lede || A.description || '';
document.getElementById('cStepsOf').textContent = '/'+fmt(M.total_steps);
document.getElementById('cCostOf').textContent = '/'+fmt(M.total_cost);
document.getElementById('metrics').innerHTML =
  [['cost',fmt(M.total_cost)],['steps',fmt(M.total_steps)],
   M.runtime_us?['runtime',fmt(M.runtime_us/1000,2)+' ms']:null,
   M.total_quanta?['quanta',fmt(M.total_quanta)]:null,
   M.peak_quanta?['peak n̄',fmt(M.peak_quanta,1)]:null,
   ['contacts',fmt(M.n_gate_pairs)],
   M.n_cool?['cooling',fmt(M.cooling_us/1000,1)+' ms']:null,
   ['DACs',fmt(hw.dacs)],['junctions',fmt(hw.n_junctions)]]
  .filter(Boolean).map(([l,v])=>`<div class="m"><span>${l}</span><b>${v}</b></div>`).join('');

const classColour={rotate_cw:C.anc,rotate_ccw:C.rotate_alt,dock:C.active,
  undock:C.accent,gate:C.x,cool:C.highway,measure:C.data,reset:C.neutral,
  sort_merge:C.anc,sort_split:C.rotate_alt,shuttle:C.rail,simd:C.anc};
const byClass={}; for(const f of P.frames){const k=f.cls||f.type; byClass[k]=(byClass[k]||0)+1;}
document.getElementById('track').innerHTML=Object.entries(byClass).map(([k,v])=>
  `<i style="width:${100*v/P.frames.length}%;background:${classColour[k]||C.line}" title="${k}: ${v} cycles"></i>`).join('');

// the legend only ever claims a distinction the picture actually makes
const roleSet=new Set(Object.values(ROLE));
const zoneSet=new Set(A.nodes.filter(n=>n.kind!=='junction').map(n=>n.zone||'other'));
const nLoops=Object.keys(A.loops||{}).length;
document.getElementById('legend').innerHTML =
  (roleSet.size>1
    ? [['rail','data region'],['highway','highway'],['compute','computing region']]
        .filter(([k])=>roleSet.has(k))
        .map(([k,t])=>`<span><i class="bar" style="background:var(--${k})"></i>${t}</span>`).join('')
    : `<span><i class="bar" style="background:var(--rail)"></i>shuttling segment</span>`)+
  (nLoops?`<span><i class="bar" style="background:var(--anc);opacity:.35;height:9px"></i>transport loop</span>`:'')+
  [...zoneSet].slice(0,5).map(z=>
    `<span><i class="bar" style="background:var(--zone_${['data','ancilla','trap','tfactory','load','register'].indexOf(z)>=0?z:'other'});height:7px;border-radius:4px"></i>${z} site</span>`).join('')+
  `<span><i class="sq" style="background:var(--panel);border:2px solid var(--grid)"></i>junction (holds no ions)</span>`+
  `<span><i class="dot" style="background:var(--panel);border:1.5px solid var(--muted)"></i>free capacity slot</span>`+
  `<span><i class="dot" style="background:var(--data)"></i>data ion</span>`+
  `<span><i class="dot" style="background:var(--anc)"></i>ancilla</span>`+
  `<span><i class="dot" style="background:var(--x)"></i>X stabilizer</span>`+
  `<span><i class="dot" style="background:var(--z)"></i>Z stabilizer</span>`+
  `<span><i class="bar" style="background:var(--dc_hot);height:4px"></i>energized DC electrode</span>`+
  `<span><i class="dot" style="background:var(--anc);opacity:.3"></i>moving potential well</span>`;

const capHist=SUM.capacity_histogram||{};
// The Machine pane is a FUNCTION, not a one-shot assignment, so the editor can re-render
// it after an edit rather than keeping a second copy of this markup.  `stale` is what an
// edit sets: the rule verdicts were computed by Python against the PRE-EDIT device, and a
// rule badge that still says "pass" after the design changed is worse than no badge at
// all.  Everything on the R1-R18 surface except the structural check needs a CycleView
// built from a program, so it genuinely cannot be re-run in the browser -- and saying so
// is the honest behaviour, not a limitation to paper over.
// THE VERDICT SURFACE.  `RULES_STALE` -- which struck all 23 verdicts through the moment
// anything was edited -- is GONE, not kept alongside: 17 of the 23 are now re-derived
// client-side off the same walk that prices the programme, and only the other 6 go grey.
// Two mechanisms would give the page two answers about the same rule.
//
// THREE STATES, and the heading COUNTS rather than saying "all": checked here, failed
// here, and NOT CHECKED HERE -- the last enumerated by name with its reason, never absent
// and never green.
function ruleBadges(){
  // `globalThis.EDITOR`, never the bare identifier: `editor.js` publishes the API through
  // `globalThis` from INSIDE its own IIFE, and `var EDITOR = (function(){...})()` has not
  // completed at that moment.  In a browser each script is its own top-level program so
  // the two spellings agree; under `tests/shim.mjs` the whole page is ONE function body,
  // where `var EDITOR` is a local that is still undefined -- which is exactly the shape of
  // difference that makes a panel testable in a browser and untestable in the harness.
  const ED = globalThis.EDITOR;
  if(!ED || !ED.ruleCoverage) return '';
  const cov = ED.ruleCoverage();
  if(!cov.length) return '';
  const n = cov.filter(c=>c.state==='checked'||c.state==='failed').length;
  const bad = cov.filter(c=>c.state==='failed');
  const cls = {checked:'ok', failed:'bad', partial:'warn', unchecked:'unchecked'};
  return `<div class="mut" style="margin-bottom:6px">${n} of ${cov.length} rules `+
    `checked in this browser`+
    (bad.length ? ` &middot; <b>${bad.length} failing</b>` : ` &middot; no violation in the `+
      `${n} rules this page can check`)+`</div>`+
    `<div>`+cov.map(c=>`<span class="badge ${cls[c.state]}" title="${esc(c.statement||c.rule)}">`+
      `${c.rule}${c.count?' '+c.count:''}</span>`).join('')+`</div>`;
}
function renderSide(){
const evd = D.evidence || {replayed_cycles: P.frames.length};
document.getElementById('side').innerHTML =
  `<h3>Rules</h3>`+
  (evd.replayed_cycles === 0 && !P.frames.length
    ? `<div class="mut">no programme has been replayed, so none of the `+
      `${(evd.rules_all||[]).length||23} rules has been evaluated. Write one in the `+
      `<b>Write</b> pane.</div>`
    : (ruleBadges() ||
       `<div>`+
       rl.passed.map(r=>`<span class="badge ok">${r}</span>`).join('')+
       rl.failed.map(r=>`<span class="badge bad">${r}</span>`).join('')+
       Object.keys(rl.skipped).map(r=>`<span class="badge warn">${r}</span>`).join('')+
       `</div>`))+
  `<h3 style="margin-top:16px">Hardware</h3><table>`+
  [['scheme',hw.scheme],['trapping zones',fmt(hw.trapping_zones)],
   ['ion capacity',fmt(hw.total_capacity)],['junctions',fmt(hw.n_junctions)],
   ['electrodes',fmt(hw.electrodes)],['switches',fmt(hw.switches)],
   ['DACs',fmt(hw.dacs)],['DACs / trap',fmt(hw.dacs_per_trap,3)]]
  .map(([k,v])=>`<tr><td>${k}</td><td>${v}</td></tr>`).join('')+`</table>`+
  (Object.keys(capHist).length?
   `<h3 style="margin-top:16px">Site capacity</h3><table>`+
   Object.entries(capHist).sort((a,b)=>Number(a[0])-Number(b[0]))
     .map(([k,v])=>`<tr><td>${k} ion${k==='1'?'':'s'} per site</td><td>${fmt(v)} sites</td></tr>`)
     .join('')+`</table>`:'')+
  `<h3 style="margin-top:16px">Movement templates</h3><table>`+
  Object.entries(P.templates).map(([k,v])=>`<tr><td><code>${k}</code></td><td>${fmt(v)}</td></tr>`).join('')+
  `</table>`+
  (Object.keys(M.quanta_components||{}).some(k=>M.quanta_components[k])?
   `<h3 style="margin-top:16px">Quanta by component</h3><table>`+
   Object.entries(M.quanta_components).filter(([,v])=>v)
     .map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td></tr>`).join('')+`</table>`:'')+
  `<div class="note">`+
  (drift<1e-6
    ? (EV.self_check_ions === 0
        ? `<span class="badge unchecked">no self-check</span> no Python replay accompanies `+
          `this page, so there is nothing to check this one against. The cost figures come `+
          `from the browser's own <code>priceFrames</code>, which is parity-tested against `+
          `Python but has not been run against <b>this</b> programme in Python.`
        : `<span class="badge ok">self-check</span> this page's own replay of `+
          `${D.checksum_components.join(' + ')} agrees with the Python verifier to `+
          `${drift.toExponential(1)} quanta per ion, over ${EV.self_check_ions} ion(s).`)
    : `<span class="badge bad">self-check FAILED</span> the page disagrees with the `+
      `verifier by ${drift.toFixed(4)} quanta on <code>${driftIon}</code>. Trust the `+
      `Python numbers, not this animation.`)+
  (P.truncated?`<br><span class="badge warn">truncated</span> only the first ${P.frames.length} instructions are animated.`:'')+
  `<br>scale ${L.sx.toFixed(1)}&times;${L.sy.toFixed(1)} px/unit &middot; nearest sites `+
  `${L.g.toFixed(1)} px apart &middot; ion radius ${L.r_ion.toFixed(1)} px`+
  `<br>drag the stage to pan, ctrl (or shift) + wheel to zoom, <b>Fit</b> to reset.`+
  `</div>`;
}
renderSide();

// ---------- controls ----------
// One wall-clock rAF loop: speed changes the RATE, never the continuity.  The old page
// pinned phase=1 above 4x -- which is the default -- so `pointOnPath` was never called
// and every ion teleported.
const MS={1:900, 4:225, 16:56, 64:40, 256:40};
const STRIDE={1:1, 4:1, 16:1, 64:4, 256:16};
let raf=null, t0=0;
const playBtn=document.getElementById('play');
const speedSel=document.getElementById('speed');
const stop=()=>{ if(raf){cancelAnimationFrame(raf); raf=null;} playBtn.textContent='Play'; };

// The flag's ONLY side-effect channel, called by the editor whenever the programme's
// validity changes.  Transport is a promise that pressing Play shows you the programme
// running; while the programme does not fit the device there is nothing to run, so the
// promise is WITHDRAWN rather than silently broken.  `frame` and `phase` are not reset,
// so undo restores the exact pose.  `fit` and the colour `mode` select stay enabled --
// pure viewport, no claim about the programme.
function onProgramValidity(){
  const bad = !!PROGRAM_STALE;
  if(bad) stop();
  for(const id of ['play','step','glide','phase','reset','slider','speed']){
    const e=document.getElementById(id); if(e) e.disabled = bad;
  }
  const b=document.getElementById('invalid');
  // `textContent`, not `innerHTML`: `A.name` and `P.name` come out of the JSON data
  // block, and an architecture named with markup must not be able to escape the banner.
  b.style.display = bad ? '' : 'none';
  b.textContent = bad
    ? 'programme invalid — ' + PROGRAM_STALE.why
      + ' · the animation is stopped · undo, or recompile: '
      + 'python -m qccd run ' + A.name + ' --program ' + P.name
    : '';
  draw();
}
// A 46-hop rigid rotation should take visibly longer than a one-hop shuttle, but not
// 46x longer: sqrt, capped, keeps both readable.
const durOf=i=>{ const k=parseInt(speedSel.value,10)||4;
  return Math.max(40, (MS[k]||225)*Math.min(6, Math.sqrt(hopsIn(i)))); };
function tick(now){
  const k=parseInt(speedSel.value,10)||4, stride=STRIDE[k]||1;
  if(stride>1){                       // fast-forward: skip frames, do not slow the clock
    phase=1; frame=Math.min(P.frames.length-1, frame+stride); draw();
    if(frame>=P.frames.length-1){stop(); return;}
    raf=requestAnimationFrame(tick); return;
  }
  let dur=durOf(frame);
  phase=(now-t0)/dur;
  while(phase>=1){
    if(frame>=P.frames.length-1){ frame=P.frames.length-1; phase=1; draw(); stop(); return; }
    frame++; t0+=dur; dur=durOf(frame); phase=(now-t0)/dur;
  }
  draw();
  if(raf) raf=requestAnimationFrame(tick);
}
playBtn.onclick=()=>{
  // `.disabled` is presentation only and does NOT stop a programmatic call: the
  // space bar calls `playBtn.onclick()` directly, so the button needs a real guard.
  if(PROGRAM_STALE) return;
  if(raf) return stop();
  playBtn.textContent='Pause';
  if(phase>=1 && frame<P.frames.length-1){frame++; phase=0;}
  t0=performance.now()-phase*durOf(frame);
  raf=requestAnimationFrame(tick);
};
document.getElementById('step').onclick=()=>{stop();
  if(phase<1){phase=1;} else {frame=Math.min(P.frames.length-1,frame+1);phase=1;}
  draw();};
function runGlide(){
  const dur=Math.max(320, 260*Math.min(6, Math.sqrt(hopsIn(frame)))), g0=performance.now();
  const run=now=>{ phase=Math.min(1,(now-g0)/dur); draw();
    if(phase<1) requestAnimationFrame(run); };
  requestAnimationFrame(run);
}
document.getElementById('glide').onclick=()=>{stop();
  if(frame<P.frames.length-1 && phase>=1){frame++;}
  phase=0; draw(); runGlide();};
document.getElementById('phase').onclick=()=>{stop();
  const here=(P.frames[frame]||{}).batch;
  let i=frame+1; while(i<P.frames.length-1 && P.frames[i].batch===here) i++;
  frame=i; phase=1; draw();};
document.getElementById('reset').onclick=()=>{stop();frame=0;phase=1;fit();draw();};
document.getElementById('fit').onclick=()=>fit();
slider.oninput=e=>{stop();frame=parseInt(e.target.value,10);phase=1;draw();};
MODE.onchange=draw;

// ======================================================================
//  THE DOCK -- the architecture as a program, and the hardware program
//  with the executing instruction highlighted and kept in view.
//
//  Everything here is keyed by a STABLE ID, never by position: a listing row
//  carries `Instruction.id`, an architecture row carries the namespaced id of
//  what its statement declares (`class:dock`, `site:S0`, `loop:L0`), and the two
//  panels join on that one namespace.  A future editor maps a click to an object
//  through the same ids -- nothing round-trips through rendered text.
// ======================================================================
// `let`, not `const`: an AUTHORED programme carries its own provenance -- the statement
// the user typed -- and the source click-through, the listing join and the NOW strip all
// read `PROV`.  `LST` is Python's disassembly and has no client-side counterpart, so it
// stays null for an authored programme and every reader already guards on that.
let LST = D.listing || null, PROV = D.prov || null;
const CTLD = D.control || null, AL = A.listing || null;
const ROW_H = 22, OVERSCAN = 8, MAX_POOL = 96, EDGE_ROWS = 3, CENTRE = 0.33;
let PLIST = null, ALIST = null, SEL = null, lastCur = -1, ARCHVIEW = 'prog';
let VIEW = [], VIEWPOS = [], PANE = 'P';

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;')
  .replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function ref(k,id){ return '<b data-k="'+k+'" data-id="'+esc(id)+'">'+esc(id)+'</b>'; }

// id -> listing row.  Shipped as a flat `ids` array and inverted here: the map is
// 16 KB of "0":0,"1":1,... on a program this size, and one pass builds it.
const LROW = {};
if(LST) for(let i=0;i<LST.ids.length;i++) LROW[LST.ids[i]] = i;

// ---------- the inverted "which instructions touch this object" index ----------
// A rigid rotation names 144 ions; expanding those into the index would build 56k
// entries for ring144 alone, so a template is indexed by its LOOP and resolved at
// query time.  Measured: ~8k entries for the deck program.
const TOUCH = {};
function touch(key, i){ (TOUCH[key] || (TOUCH[key] = [])).push(i); }
for(let i=0;i<P.frames.length;i++){
  const f = P.frames[i];
  if(f.shift) touch('loop:'+f.shift[0], i);
  if(f.moves) for(const m of f.moves){ touch('ion:'+m[0], i);
    touch('site:'+m[1][0], i); touch('site:'+m[1][m[1].length-1], i); }
  if(f.pairs) for(const p of f.pairs){ touch('ion:'+p[0], i); touch('ion:'+p[1], i); }
  if(f.sites) for(const st of f.sites) touch('site:'+st, i);
  if(f.cls) touch('class:'+f.cls, i);
  if(f.call != null) touch('src:'+f.call, i);
}

// ---------- the list widget: one implementation, two instances ----------
// Fixed 22 px rows, a pool of at most 96, and no call to getBoundingClientRect --
// which the test harness fakes as a constant 1600x400, and which would force a
// style recalc on every scroll even where it is real.
function makeList(scId, padId, winId, opt){
  const sc=document.getElementById(scId), pad=document.getElementById(padId),
        win=document.getElementById(winId), pool=[];
  const cells = opt.cells || ['i','op','cl','a','num','num'];
  let n=0, start=-1, count=0, setTop=-1, cur=-1, sel=-1;
  const T={ follow:true, height:opt.height||420 };
  function ensure(k){
    while(pool.length<k){
      const r=document.createElement('div'); r.className=opt.cls||'ln'; r._k=[]; r._i=-1;
      for(const c of cells){ const e=document.createElement('i'); e.className=c;
        r.appendChild(e); r._k.push(e); }
      r.addEventListener('click', ev=>{ if(r._i>=0) opt.onPick(r._i, ev, r); });
      win.appendChild(r); pool.push(r);
    }
  }
  T.measure=()=>{ T.height = sc.clientHeight || opt.height || 420;
    count = Math.min(MAX_POOL, Math.ceil(T.height/ROW_H)+2*OVERSCAN);
    ensure(count); start=-1; };
  T.setCount=(rows)=>{
    n=rows; pad.style.height=(n*ROW_H)+'px';
    // Own the clamp. Shrinking the pad below the current scrollTop makes the BROWSER
    // correct it and fire `scroll`, which the ownership check then reads as the user
    // scrolling away -- so applying a filter silently switched Follow off and the
    // cursor was never tracked again.
    const max=Math.max(0, n*ROW_H - T.height);
    if((sc.scrollTop||0) > max){ setTop = sc.scrollTop = max; }
    start=-1; T.paint(true);
  };
  T.rows=()=>n;
  T.paint=(force)=>{
    const top = sc.scrollTop || 0;
    const s = Math.max(0, Math.min(Math.floor(top/ROW_H)-OVERSCAN, Math.max(0,n-count)));
    if(!force && s===start){ T.mark(); return; }
    start=s; win.style.transform='translateY('+(s*ROW_H)+'px)';
    for(let k=0;k<count;k++){
      const r=pool[k], i=s+k;
      if(i>=n){ r._i=-1; r.style.display='none'; continue; }
      r.style.display=''; r._i=i; r._bnd=false; r._ref=null;
      r.setAttribute('data-row', i);     // a future editor maps click -> row -> object
      opt.render(r, i);
    }
    T.mark();
  };
  T.mark=()=>{ const base=opt.cls||'ln';
    for(const r of pool) r.className = base+(r._bnd?' bnd':'')
      +(r._i===cur?' cur':'')+(r._i===sel?' sel':''); };
  T.scrollToRow=(i,where)=>{
    const max=Math.max(0, n*ROW_H - T.height);
    const t=Math.max(0, Math.min(max,
      i*ROW_H - Math.round(T.height*(where==null?CENTRE:where))));
    setTop=t; sc.scrollTop=t; T.paint(false);
  };
  // The comfort band: while the cursor is inside the body inset by EDGE_ROWS top and
  // bottom, NOTHING scrolls -- not a pixel.  Only when it crosses out does one jump
  // happen, putting it a third of the way down with the rest as lookahead, so forward
  // play scrolls about once every ten instructions rather than on every one.
  T.setCursor=(i)=>{
    cur=i;
    if(i<0){ T.mark(); return; }
    const top=sc.scrollTop||0, band=EDGE_ROWS*ROW_H;
    const out = (i*ROW_H < top+band) || ((i+1)*ROW_H > top + T.height - band);
    if(T.follow && out) T.scrollToRow(i,CENTRE); else T.mark();
  };
  T.cursor=()=>cur;
  T.top=()=>Math.floor((sc.scrollTop||0)/ROW_H);
  T.visible=()=>Math.max(1, Math.floor(T.height/ROW_H));
  T.setSelection=(i)=>{ sel=i; T.mark(); };
  T.setFollow=(v)=>{ T.follow=!!v; if(opt.onFollow) opt.onFollow(T.follow); };
  // Ownership of a scroll, statelessly: every programmatic scroll records what it
  // wrote; anything else is the user, and the user always wins.
  sc.addEventListener('scroll', ()=>{
    const t=sc.scrollTop||0;
    if(Math.abs(t-setTop)>1.5 && opt.userScrollBreaksFollow!==false) T.setFollow(false);
    T.paint(false); updateChip();
  });
  return T;
}

// ---------- program rows ----------
const TYPECOL = {gate:'x', cool:'highway', measure:'data', reset:'neutral',
                 init:'neutral', barrier:'line'};
function opColour(f){
  if(f.type==='simd') return classColour[f.cls] || C.anc;
  return C[TYPECOL[f.type]] || C.neutral;
}
function opText(f){
  const li=LROW[f.id];
  if(li!=null) return LST.ops[LST.op[li]];
  return (f.type||'').toUpperCase().slice(0,6);
}
function argsHTML(f){
  if(f.shift){
    const loop=f.shift[0], d=f.shift[1];
    return ref('loop',loop)+' &times; '+(d>=0?'+':'')+d
      +' <i class="mut">'+Math.abs(d)+' hop'+(Math.abs(d)===1?'':'s')+'</i>';
  }
  if(f.moves){
    const n=f.moves.length, out=[];
    for(let j=0;j<Math.min(2,n);j++){
      const ion=f.moves[j][0], pa=f.moves[j][1];
      out.push(ref('ion',ion)+' '+ref('site',pa[0])+'&rarr;'+ref('site',pa[pa.length-1]));
    }
    return out.join(', ')+(n>2?' <i class="mut">+'+(n-2)+'</i>':'')
      +((f.entails&&f.entails.length)
          ? ' <i class="mut">['+f.entails.map(esc).join('+')+']</i>' : '');
  }
  if(f.pairs){
    const n=f.pairs.length, out=[];
    for(let j=0;j<Math.min(2,n);j++)
      out.push(ref('ion',f.pairs[j][0])+'&middot;'+ref('ion',f.pairs[j][1]));
    return (f.gate?esc(f.gate)+' ':'')+out.join(', ')
      +(n>2?' <i class="mut">+'+(n-2)+'</i>':'')
      +((f.sites&&f.sites.length)?' @'+ref('site',f.sites[0]):'');
  }
  if(f.type==='cool')
    return (f.broadcast?'<i class="mut">broadcast, every ion</i>'
                       :(f.ions||[]).slice(0,2).map(i=>ref('ion',i)).join(', '))
      +(f.trigger?' <i class="mut">trigger='+esc(f.trigger)+'</i>':'');
  if(f.ions && f.ions.length){
    const n=f.ions.length;
    return f.ions.slice(0,2).map(i=>ref('ion',i)).join(', ')
      +(n>2?' <i class="mut">+'+(n-2)+'</i>':'');
  }
  if(f.place) return '<i class="mut">place '+Object.keys(f.place).length+' ions</i>';
  const li=LROW[f.id];
  return li!=null ? '<i class="mut">'+esc(LST.detail[li])+'</i>' : '';
}
const stripTags = h => h.replace(/<[^>]*>/g,' ')
                        .replace(/&rarr;/g,'->').replace(/&middot;/g,'.')
                        .replace(/&times;/g,'x').replace(/&amp;/g,'&');
function rowText(f){        // what the filter matches against
  // BOTH renderings. The page draws `argsHTML` and Python computes `LST.detail`, and
  // searching only the latter meant a filter could match 390 rows and be visible on
  // none of them: "cw" matched every rotation while the rows all read "L0 x +13".
  // Searching the union means whatever you can read, you can find.
  const li=LROW[f.id], bits=[f.type||'', f.cls||'', f.check||'', f.gate||''];
  bits.push(stripTags(argsHTML(f)));
  if(li!=null) bits.push(LST.ops[LST.op[li]], LST.detail[li]);
  if(f.call!=null && PROV && PROV.calls[f.call]){
    const c=PROV.calls[f.call], st=PROV.sites[c.site];
    bits.push(c.op); if(st) bits.push(st.file, st.text||'');
  }
  return bits.join(' ').toLowerCase();
}
function renderProgRow(r, row){
  const i = VIEW[row], k = r._k;
  if(i < 0){                                   // the truncation sentinel
    k[0].textContent=''; k[1].textContent=''; k[1].style.background='transparent';
    k[2].textContent=''; k[4].textContent=''; k[5].textContent='';
    k[3].innerHTML='<i class="mut">'+(P.n_instructions-P.frames.length)
      +' further instructions are not animated</i>';
    r._ref=null; return;
  }
  const f = P.frames[i] || {}, li = LROW[f.id];
  r._ref = {kind:'instr', id:f.id, i:i};
  const prev = row>0 ? P.frames[VIEW[row-1]] : null;
  r._bnd = !!(prev && prev.batch !== f.batch);
  // POSITION, not identity.  `f.id` is a durable handle allocated once and never
  // reassigned, so after the cooling pass the ids read 0,1,2,1579,3,4,... -- correct as
  // a handle, unreadable as an address in a list a human scrolls.  The identity is still
  // what `_ref`, `LROW`, `TOUCH` and the NOW strip's `#id` join on.
  k[0].textContent = String(i);
  k[1].textContent = opText(f);
  k[1].style.background = opColour(f);
  k[2].textContent = f.cls || f.gate || (f.mode||'');
  k[3].innerHTML = argsHTML(f)
    + (li!=null && LST.rule_sets[LST.rules[li]]
        ? ' <b class="bad">'+esc(LST.rule_sets[LST.rules[li]])+'</b>' : '');
  k[4].textContent = (f.cost!=null) ? fmt(f.cost) : '.';
  k[5].textContent = (f.steps!=null) ? String(f.steps) : '.';
}

// ---------- architecture rows ----------
// One record may render over several physical lines (a curve with four operating
// points is ONE statement), so the rows are physical lines carrying the record
// ordinal -- which is what `ArchListing.line_map()` is in Python.
const ARCHROWS=[], AROW_OF_N={};
if(AL) for(const ln of AL.lines){
  const parts = String(ln.text).split('\n');
  for(let j=0;j<parts.length;j++){
    if(AROW_OF_N[ln.n]===undefined) AROW_OF_N[ln.n]=ARCHROWS.length;
    ARCHROWS.push({n:ln.n, t:parts[j], kind:ln.kind, section:ln.section,
                   target:(j===0?ln.target:null), note:(j===0?ln.note:null)});
  }
}
const DEVROWS=[];
for(const nd of A.nodes) DEVROWS.push({kind:'site', id:nd.id,
  t:nd.id+'   '+(nd.kind==='junction'?'junction':(nd.zone||'-'))
    +'   cap '+nd.cap+'   deg '+nd.deg+(nd.corner?'   bend':'')});
for(const sg of A.segments) DEVROWS.push({kind:'segment', id:sg.id,
  t:sg.id+'   '+sg.a+' - '+sg.b+'   '+(sg.loop||'-')+'   cap '+sg.cap});
for(const lid in (A.loops||{})) DEVROWS.push({kind:'loop', id:lid,
  t:lid+'   '+A.loops[lid].length+' nodes'});
let AVIEW = [];
function archRows(){ return ARCHVIEW==='dev' ? DEVROWS : ARCHROWS; }
function renderArchRow(r, row){
  const i = AVIEW[row], k = r._k, src = archRows()[i] || {};
  k[0].textContent = String(i+1);
  if(ARCHVIEW==='prog'){
    r._ref = src.target ? refOf(src.target) : null;
    const cls = src.kind==='call' ? '' : (src.kind==='header' ? 'hdr' : 'cmt');
    k[1].innerHTML = '<span class="'+cls+'">'+esc(src.t)+'</span>'
      + (src.note ? ' <span class="mut">&nbsp;# '+esc(src.note)+'</span>' : '');
  } else {
    r._ref = {kind:src.kind, id:src.id};
    k[1].innerHTML = esc(src.t);
  }
  r._bnd = ARCHVIEW==='prog' && src.kind==='comment';
}
function refOf(target){
  const c = String(target).indexOf(':');
  if(c<0) return {kind:target, id:target};
  return {kind:String(target).slice(0,c), id:String(target).slice(c+1)};
}
// which architecture statement AUTHORISED the instruction now executing
function archRowFor(f){
  if(!AL || ARCHVIEW!=='prog') return -1;
  const ix = AL.index||{};
  let key = null;
  if(f.cls) key = 'class:'+f.cls;
  else if(f.type==='gate') key = 'primitive:ms_gate';
  else if(f.type==='cool') key = 'primitive:cool';
  else if(f.type==='measure') key = 'primitive:measure';
  else if(f.type==='reset') key = 'primitive:reset';
  if(key && ix[key] && ix[key].length){
    const r = AROW_OF_N[ix[key][0]];
    if(r!==undefined) return AVIEW.indexOf(r);
  }
  return -1;
}
function archRowOf(kind, id){
  if(ARCHVIEW!=='prog'){
    const rows=DEVROWS;
    for(let i=0;i<rows.length;i++) if(rows[i].id===id && rows[i].kind===kind)
      return AVIEW.indexOf(i);
    return -1;
  }
  const ix=(AL&&AL.index)||{}, key=(kind==='ion')?null:(kind+':'+id);
  if(key && ix[key] && ix[key].length){
    const r=AROW_OF_N[ix[key][0]];
    if(r!==undefined) return AVIEW.indexOf(r);
  }
  return -1;
}

// ---------- the NOW strip: what is executing, and what is driving it ----------
function nowHTML(f){
  const li = LROW[f.id];
  const where = [];
  if(f.group!==undefined) where.push('group '+f.group);
  if(f.batch!==undefined) where.push('batch '+f.batch);
  if(f.check) where.push('check <b>'+esc(f.check)+'</b>');
  let h = '<b>#'+f.id+'</b> &nbsp;'+esc(f.type)+(f.cls?' &middot; '+esc(f.cls):'')
    +(f.mode?' &middot; '+esc(f.mode):'')
    +(where.length?' <span class="mut">&nbsp;&nbsp;'+where.join(' &middot; ')+'</span>':'');
  h += '<br>' + argsHTML(f);
  if(li!=null){
    const u=LST.us[li], q=LST.dnbar[li], pt=LST.pts[LST.point[li]];
    h += ' <span class="mut">&nbsp;x'+LST.width[li]
      +(f.cost!=null?' &middot; cost '+fmt(f.cost)+' &middot; '+f.steps+' steps':'')
      +(u!=null?' &middot; '+fmt(u,1)+' us':'')
      +(q!=null?' &middot; &Delta;n&#772; '+(q>=0?'+':'')+fmt(q,1):'')
      +(pt?' &middot; '+esc(pt):'')+'</span>';
  }
  h += ctlHTML(f);
  return h;
}
function ctlHTML(f){
  if(!CTLD || f.ctl==null) return '';
  const r = CTLD.records[f.ctl];
  if(!r) return '';
  const ch=r.channels||[0,0], si=r.sites||[0,0,0], sw=r.switch||[0,0,0];
  let h = '<br><span class="mut">control:</span> <b>'+esc(r.driver)+'</b>'
    + ' &middot; ' + esc(r.action);
  if(r.driver==='transport'){
    h += ' &middot; '+ch[0]+'/'+ch[1]+' channels driven'
      + (CTLD.spec && CTLD.spec.grouping ? ' ('+esc(CTLD.spec.grouping)+')' : '')
      + ' &middot; '+si[0]+'/'+si[2]+' sites moving, '+si[1]
      + ' held out by their switch'
      + ' &middot; '+fmt(sw[1])+'/'+fmt(sw[2])+' switch elements';
    for(const b of (r.banks||[])) h += '<br><span class="bank">&nbsp;&nbsp;'
      + esc(b[0])+' &times;'+b[1]+' &nbsp;each drives '+b[2]+' sites &rarr; '+b[3]
      + ' follow &nbsp;<b>'+esc(b[4])+'</b>'+(b[5]?'':' <i>(not verifiable)</i>')
      + '</span>';
    h += '<br>&nbsp;&nbsp;' + (r.ok===true ? '<span class="yes">drivable (R4d)</span>'
      : (r.ok===false ? '<span class="no">NOT DRIVABLE (R4d)</span>'
                      : '<span class="mut">drivability not judged &mdash; which is not a pass</span>'));
    for(const pb of (r.problems||[]).slice(0,2))
      h += '<br><span class="no">&nbsp;&nbsp;! '+esc(pb)+'</span>';
    if((r.problems||[]).length>2)
      h += '<br><span class="no">&nbsp;&nbsp;... and '+(r.problems.length-2)
        + ' more channels the same way</span>';
  } else if(r.driver==='optical'){
    h += ' &middot; '+si[0]+'/'+si[2]+' zones lit; every transport channel is quiet';
  }
  return h;
}
function srcHTML(f){
  const und = [];
  if(CTLD && f.ctl!=null && CTLD.records[f.ctl])
    for(const u of (CTLD.records[f.ctl].und||[])) und.push(CTLD.notes[u]);
  const note = und.length ? '<br><span class="mut">not determined: '+esc(und[0])+'</span>' : '';
  if(!PROV || f.call==null || !PROV.calls || !PROV.calls[f.call])
    return '<span class="mut">no source line recorded for this instruction</span>'+note;
  const c=PROV.calls[f.call], st=(PROV.sites||[])[c.site]||{};
  return 'from <b>'+esc(st.file||'?')+':'+(st.line||0)+'</b> &nbsp;<code>'
    + esc(st.text || (c.op+'(...)')) + '</code>'+note;
}
function archNowHTML(){
  if(!AL) return '';
  let h = '<b>'+esc(AL.name)+'</b> <span class="mut">&middot; '+esc(AL.mode)
    + ' form &middot; '+AL.lines.length+' statements</span>';
  if(SEL) h += '<br><span class="mut">selected</span> '+esc(SEL.kind)+' <b>'
    + esc(SEL.id)+'</b>';
  return h;
}

// ---------- selection: one bus for the stage, both listings and the filter ----------
function selectRef(kind, id){
  SEL = {kind:kind, id:id};
  ALIST.setSelection(archRowOf(kind, id));
  const hits = TOUCH[kind+':'+id];
  const chip=document.getElementById('pChip');
  chip.textContent = hits ? (hits.length+' instructions touch '+id+' \u00b7 show') : '';
  chip.className = hits ? 'chip' : 'chip off';
  document.getElementById('aNow').innerHTML = archNowHTML();
  document.getElementById('aFoot').innerHTML = selFootHTML(kind, id);
}
function selFootHTML(kind, id){
  if(kind==='site' && CTLD && CTLD.channels_by_site){
    const m=CTLD.channels_by_site;
    if(m.elided) return esc(id)+' is on '+(m.counts[id]||0)
      +' channel(s); the map is too large to ship, so no ids';
    const ix=m.of[id];
    if(ix!==undefined){
      const ids=m.lists[ix]||[];
      return esc(id)+' is driven by '+ids.length+' channel(s): '
        + ids.slice(0,4).map(esc).join(', ')+(ids.length>4?' ...':'');
    }
  }
  if(kind==='class' && AL && AL.index['class:'+id])
    return 'declared at architecture statement '+AL.index['class:'+id][0];
  return '';
}

// ---------- seek ----------
function seek(i, o){
  // The one funnel every transport entry point goes through -- the timeline click,
  // every listing-row click via `pickProg`, and all nine keyboard shortcuts.  A new
  // entry point must be added HERE rather than wired straight to `frame=...; draw()`.
  if(PROGRAM_STALE) return;
  o = o || {};
  stop();
  frame = clamp(0, i, P.frames.length-1);
  phase = o.glide ? 0 : 1;
  draw();
  // Recentre on the row WITHOUT re-arming Follow. Re-arming here meant every arrow key,
  // row click and timeline click switched Follow back on, so the toggle the UI
  // advertises (and its aria-pressed state) could never actually be held off.
  if(VIEWPOS[frame]>=0) PLIST.scrollToRow(VIEWPOS[frame], CENTRE);
  if(o.glide) runGlide(); else if(o.play) playBtn.onclick();
}
function pickProg(row, ev, r){
  const t = ev && ev.target;
  const id = t && t.getAttribute && t.getAttribute('data-id');
  if(id){ selectRef(t.getAttribute('data-k'), id); return; }
  if(VIEW[row] < 0) return;                    // the truncation sentinel
  seek(VIEW[row], {play: !!(ev&&ev.shiftKey), glide: !!(ev&&ev.detail===2)});
}
function pickArch(row, ev, r){
  ALIST.setSelection(row);
  if(r && r._ref) selectRef(r._ref.kind, r._ref.id);
}

// ---------- filtering: VIEW/VIEWPOS only.  P.frames is NEVER touched ----------
function rebuildView(text, only){
  const q=(text||'').trim().toLowerCase();
  VIEW=[]; VIEWPOS=new Array(P.frames.length).fill(-1);
  for(let i=0;i<P.frames.length;i++){
    if(only && only.indexOf(i)<0) continue;
    if(q && rowText(P.frames[i]).indexOf(q)<0) continue;
    VIEWPOS[i]=VIEW.length; VIEW.push(i);
  }
  if(P.truncated && !q && !only) VIEW.push(-1);
  document.getElementById('pCount').textContent =
    VIEW.length + ' / ' + P.n_instructions + ' instructions';
  PLIST.setCount(VIEW.length);
  syncCursor(true);
}
function rebuildArchView(text){
  const q=(text||'').trim().toLowerCase(), rows=archRows();
  AVIEW=[];
  for(let i=0;i<rows.length;i++){
    if(q && String(rows[i].t).toLowerCase().indexOf(q)<0) continue;
    AVIEW.push(i);
  }
  ALIST.setCount(AVIEW.length);
}

// ---------- the off-screen chip ----------
function updateChip(){
  if(!PLIST) return;
  const chip=document.getElementById('pChip');
  if(SEL && chip.className==='chip') return;      // showing a selection instead
  const row=VIEWPOS[frame];
  if(PLIST.follow || row<0){ if(!SEL){ chip.className='chip off'; } return; }
  const top=PLIST.top(), vis=PLIST.visible();
  if(row<top) chip.textContent='\u2191 executing \u00b7 '+(top-row)+' above';
  else if(row>=top+vis) chip.textContent='\u2193 executing \u00b7 '+(row-top-vis+1)+' below';
  else { chip.className='chip off'; return; }
  chip.className='chip';
}

// ---------- the one hook into the animation ----------
// draw() runs once per animation frame AND nine times per instruction under the
// census harness; the guard reduces the listing work to once per instruction change.
// Deliberately synchronous: requestAnimationFrame is stubbed to never fire under
// node, so a deferred sync would give the new code no smoke test at all.
function syncCursor(force){
  if(!PLIST) return;
  if(frame===lastCur && !force) return;
  lastCur = frame;
  const f = P.frames[frame] || {};
  PLIST.setCursor(VIEWPOS[frame]);
  ALIST.setCursor(archRowFor(f));
  document.getElementById('pNow').innerHTML = nowHTML(f);
  document.getElementById('pFoot').innerHTML = srcHTML(f);
  updateChip();
  const ph=document.getElementById('playhead');
  if(ph) ph.style.left = (100*frame/Math.max(1,P.frames.length-1))+'%';
}

// ---------- panes ----------
function setPane(which){
  PANE=which;
  for(const k of ['P','A','M','W','R']){
    document.getElementById('pane'+k).className = 'pane card'+(k===which?' on':'');
    document.getElementById('tab'+k).className = 'tab'+(k===which?' on':'');
  }
  sizeLists();
}
document.getElementById('tabP').onclick=()=>setPane('P');
document.getElementById('tabA').onclick=()=>setPane('A');
document.getElementById('tabM').onclick=()=>setPane('M');
document.getElementById('tabW').onclick=()=>setPane('W');
document.getElementById('tabR').onclick=()=>setPane('R');
// Three views of one object.  Program and Device are read-only renderings; SOURCE is the
// same object as text you can type into, and it writes through the same applier the drag
// does -- so the two lanes cannot disagree, because there is only one applier.
function setArchView(v){
  ARCHVIEW=v;
  document.getElementById('avB').className = v==='prog' ? 'on' : '';
  document.getElementById('avD').className = v==='dev' ? 'on' : '';
  const bs = document.getElementById('avS');
  if(bs) bs.className = v==='src' ? 'on' : '';
  const wrap = document.getElementById('aSrcWrap'), host = document.getElementById('aScroll');
  if(wrap) wrap.className = v==='src' ? 'srcwrap' : 'srcwrap off';
  if(host) host.style.display = v==='src' ? 'none' : '';
  if(v==='src'){
    // typing source IS editing, so the stage goes into edit mode with it rather than
    // leaving the user typing into a page that is still animating
    if(globalThis.EDITOR) globalThis.EDITOR.setMode('edit');
    return;
  }
  rebuildArchView(document.getElementById('aFilter').value);
  ALIST.setCursor(archRowFor(P.frames[frame]||{}));
}
document.getElementById('avB').onclick=()=>setArchView('prog');
document.getElementById('avD').onclick=()=>setArchView('dev');
{ const bs=document.getElementById('avS'); if(bs) bs.onclick=()=>setArchView('src'); }

function sizeLists(){
  if(!PLIST) return;
  const h = document.getElementById('row').getAttribute('data-layout') === 'wide'
    ? clamp(240, Math.round(0.34*window.innerHeight), 520)
                   : clamp(230, Math.round(0.78*window.innerHeight)-200, 620);
  document.getElementById('pScroll').style.height = h+'px';
  document.getElementById('aScroll').style.height = h+'px';
  PLIST.measure(); ALIST.measure(); PLIST.paint(true); ALIST.paint(true);
}

// ---------- build ----------
PLIST = makeList('pScroll','pPad','pWin', {render:renderProgRow, onPick:pickProg,
  onFollow:v=>{ const b=document.getElementById('pFollow');
                b.className='tgl'+(v?' on':''); b.setAttribute('aria-pressed', v?'true':'false'); }});
ALIST = makeList('aScroll','aPad','aWin', {render:renderArchRow, onPick:pickArch,
  cells:['i','a'], cls:'al', userScrollBreaksFollow:false});
document.getElementById('pFollow').onclick=()=>{
  PLIST.setFollow(!PLIST.follow);
  if(PLIST.follow && VIEWPOS[frame]>=0) PLIST.scrollToRow(VIEWPOS[frame], CENTRE);
  updateChip();
};
document.getElementById('pChip').onclick=()=>{
  if(SEL){ const hits=TOUCH[SEL.kind+':'+SEL.id];
    SEL=null; rebuildView('', hits); document.getElementById('pChip').className='chip off';
    return; }
  PLIST.setFollow(true);
  if(VIEWPOS[frame]>=0) PLIST.scrollToRow(VIEWPOS[frame], CENTRE);
  updateChip();
};
document.getElementById('pFilter').oninput=e=>rebuildView(e.target.value, null);
document.getElementById('aFilter').oninput=e=>rebuildArchView(e.target.value);

// ---------- the ordered timeline, with a playhead ----------
{
  const tl=document.getElementById('tl');
  if(tl){
    const B=Math.min(400, P.frames.length), out=[];
    for(let b=0;b<B;b++){
      const i=Math.floor(b*P.frames.length/B), f=P.frames[i]||{};
      out.push('<i style="width:'+(100/B)+'%;background:'
        +(classColour[f.cls||f.type]||C.line)+'"></i>');
    }
    tl.innerHTML=out.join('')+'<span class="playhead" id="playhead"></span>';
    tl.onclick=e=>{ const r=tl.getBoundingClientRect();
      seek(Math.round((e.clientX-r.left)/Math.max(1,r.width)*(P.frames.length-1)), {}); };
  }
}

// ---------- click the stage, land in the listings ----------
// One delegated listener, not 288: a click is a pointerup that travelled under 3 px,
// so the existing pan drag is untouched.
{
  let dn=null;
  svg.addEventListener('pointerdown', e=>{ dn={x:e.clientX,y:e.clientY}; });
  svg.addEventListener('pointerup', e=>{
    if(!dn) return;
    const moved=Math.hypot(e.clientX-dn.x, e.clientY-dn.y); dn=null;
    if(moved>=3) return;
    const t=e.target;
    const nid = t && (t._nid || (t.parentNode && t.parentNode._nid));
    if(nid) selectRef('site', nid);
  });
}

// ---------- keyboard ----------
let HELPON=false;
function nextBatch(dir){
  const here=(P.frames[frame]||{}).batch;
  let i=frame+dir;
  while(i>0 && i<P.frames.length-1 && (P.frames[i]||{}).batch===here) i+=dir;
  seek(i, {});
}
document.addEventListener('keydown', e=>{
  const tag = e.target && e.target.tagName;
  if(tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA'){
    if(e.key==='Escape'){ e.target.value=''; rebuildView('', null); rebuildArchView(''); }
    return;
  }
  // CTRL+S SAVES THE DESIGN. It goes before the modifier guard below, which returns
  // early on every accelerator -- so the page used to swallow the one shortcut a design
  // tool must have and let the browser offer to save the HTML instead.
  if((e.ctrlKey||e.metaKey) && !e.altKey && (e.key==='s'||e.key==='S')){
    if(window.EDITOR && EDITOR.saveProject){ e.preventDefault(); EDITOR.saveProject(); }
    return;
  }
  if(e.ctrlKey||e.metaKey||e.altKey) return;
  const k=e.key;
  // SPACE IS HELD-TO-PAN IN EDIT MODE, and it was also scrubbing the programme:
  // both listeners fired, so holding space to pan advanced the frame underneath you.
  // The editor owns the key while it is on; this handler yields rather than competing.
  if(k===' '){ if(window.EDITOR && EDITOR.mode()==='edit') return;
               e.preventDefault(); playBtn.onclick(); return; }
  // fold the side panels away. `\\` does both, which is what you want while drawing.
  if(k==='['){ foldPanel(railEl); return; }
  if(k===']'){ foldPanel(dockEl); return; }
  if(k==='\\\\'){ const on = !(PANELS.state().rail && PANELS.state().dock);
                 foldPanel(railEl, on); foldPanel(dockEl, on); return; }
  if(k==='ArrowRight'||k==='.'||k==='j'||k==='ArrowDown'){ seek(frame+1,{}); return; }
  if(k==='ArrowLeft'||k===','||k==='k'||k==='ArrowUp'){ seek(frame-1,{}); return; }
  if(k==='PageDown'){ seek(frame+25,{}); return; }
  if(k==='PageUp'){ seek(frame-25,{}); return; }
  if(k==='Home'){ seek(0,{}); return; }
  if(k==='End'){ seek(P.frames.length-1,{}); return; }
  if(k==='Enter'){ seek(frame,{glide:true}); return; }
  if(k==='f'){ document.getElementById('pFollow').onclick(); return; }
  if(k==='/'){ e.preventDefault(); document.getElementById('pFilter').focus(); return; }
  // ESCAPE CANCELS A LIVE DRAG FIRST.  The editor's own handler runs on the same
  // target; this one still clears the programme filter afterwards, which is what it has
  // always done, so neither selection model is left holding a stale answer.
  if(k==='Escape'){ SEL=null; ALIST.setSelection(-1);
    document.getElementById('pChip').className='chip off'; return; }
  if(k==='0'){ fit(); return; }
  if(k==='1'){ setPane('P'); return; }
  if(k==='2'){ setPane('A'); return; }
  if(k==='3'){ setPane('M'); return; }
  if(k==='?'){ const h=document.getElementById('help');
    h.className = HELPON ? 'help off' : 'help'; HELPON=!HELPON; return; }
});
document.getElementById('helpBody').innerHTML =
  '<h3>Keys</h3><table>'
  + [['space','play / pause'],['&larr; &rarr;','previous / next instruction'],
     ['PgUp PgDn','&plusmn;25 instructions'],['Home End','first / last'],
     ['enter','glide the current instruction'],['f','toggle Follow'],
     ['/','filter the program listing'],['esc','clear filter and selection'],
     ['0','fit the stage'],['1 2 3','Program / Architecture / Machine pane'],
     ['e','edit mode / play mode'],
     ['drag an element','move it; alt frees the snap, shift snaps to quarter steps'],
     ['drag empty stage','marquee-select everything inside the rectangle'],
     ['shift-drag','rubber-band a new segment from one node to another'],
     ['click (armed)','place the armed palette element where you click'],
     ['double-click','place a site on empty stage with nothing armed'],
     ['middle / right drag','pan &middot; so does space+drag'],
     ['esc','cancel the drag in progress, else clear the selection'],
     ['del','remove the selection (a site takes its segments with it)'],
     ['&larr;&uarr;&rarr;&darr;','nudge the selection one lattice step (shift: four)'],
     ['L','set the incident segment lengths to match the drawing'],
     ['ctrl+Z','undo &middot; ctrl+shift+Z redo'],
     ['space (held)','pan even when the cursor is over a site'],
     ['?','close this']]
    .map(r=>'<tr><td><code>'+r[0]+'</code></td><td>'+r[1]+'</td></tr>').join('')
  + '</table>';
if(AL) document.getElementById('aFoot').innerHTML =
  'round-trip ' + (AL.round_trip===true ? 'verified' :
                   (AL.round_trip===false ? 'FAILED' : 'not checked'))
  + ' \u00b7 ' + esc(AL.mode) + ' form';
rebuildArchView('');
rebuildView('', null);
sizeLists();
setPane('P');

sizeStage();
draw();
</script>
__EDITOR__
</body></html>
'''


def render_html(
    arch: Architecture,
    prog: TSIR,
    res: ReplayResult,
    model: CostModel,
    path: str | Path,
    *,
    max_frames: int = 20000,
    kicker: str | None = None,
    headline: str | None = None,
    lede: str | None = None,
    control: ControlTrace | None = None,
    provenance: str = "sites",
    template_stems: "Sequence[str] | str | None" = None,
    metal: dict | None = None,
) -> Path:
    """Write the self-contained page.  Returns the path written."""
    view = build_view_model(arch, prog, res, model, max_frames=max_frames,
                            kicker=kicker, headline=headline, lede=lede,
                            control=control, provenance=provenance,
                            template_stems=template_stems, metal=metal)
    html = _TEMPLATE.replace("__TITLE__", f"{arch.name} - {prog.name}")
    html = html.replace("__CSSVARS__", css_vars())
    html = html.replace("__DATA__", _escape_blob(json.dumps(view, separators=(",", ":"))))
    html = html.replace("__ENGINE__", _js_block(ENGINE_JS))
    html = html.replace("__EDITOR__", _js_block(EDITOR_JS))
    for bad in FORBIDDEN:
        if bad in html:
            at = html.index(bad)
            raise ValueError(
                f"page would not be self-contained: {bad!r} at {at}: "
                f"...{html[max(0, at - 60):at + 80]}...")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the page is byte-identical on every platform
    p.write_text(html, encoding="utf-8", newline="")
    return p
