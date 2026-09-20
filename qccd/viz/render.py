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

import hashlib
import json
import re
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
from ..ir.tsir import TSIR, iter_operands, iter_pairs
from ..verify.control import ControlTrace, control_trace
from ..verify.replay import ReplayResult
from ..verify.rules import rule_statements
from . import course as _course
from .layout import (H_MAX, H_MIN, ISO_ASPECT, K_ANISO, K_ION, K_REST, PAD_A, PAD_B,
                     PITCH_CAP, R_ION_MAX, R_ION_MIN, W_MAX, W_MIN, compute_layout)
# THE SCALE, and the only thing in this module that knows a technology exists.  Node
# positions are lattice units, which are not lengths; `qccd/viz/scale.py` is the one
# narrow seam that turns them into nanometres, and it says in full why it is a separate
# module rather than an import here.  The METAL still arrives as a parameter -- see the
# `metal` key below -- because that one IS a build this module must not pay for.
from .scale import DEFAULT_TECH, tech_view_model
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
               "R8", "R11", "R12", "R13", "R14", "R17", "R18", "R19", "R20",
               "R21", "R22")

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
    source: dict | None = None,
    tech=None,
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

    `source` is the fourth, and it is optional because only a COMPILED program has one:
    the circuit the program came from, its text, and which source operation each
    hardware instruction realises. A hand-written program answers "what is executing"
    with the instruction alone; a compiled one has a second answer -- which line of the
    user's QASM this pulse is discharging -- and that is the answer somebody debugging a
    compiler actually wants. `Compiler/bridge/animate.py` builds it from the certificate,
    so the join is one the Lean checker has already verified.
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
            # `iter_pairs` yields nothing for a one-qubit gate, which is what every
            # two-qubit RULE wants and leaves the picture unable to say which ion a
            # single-qubit pulse lands on -- the majority of gate frames.  `acts` is
            # every operand of either arity, so the renderer can light one ion for an R
            # and two for an MS instead of lighting none for either.
            f["acts"] = [list(t) for t in iter_operands(instr)]
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
    # EVERY ION IS AN ION.  This table used to mark an ion "ancilla" when it started on a
    # site whose zone was called `ancilla` -- a role a code assigns, read off a zone name
    # that no longer exists because it described no hardware.  What the stage still
    # distinguishes is what the MACHINE is doing to an ion: resting, flying, or held in a
    # gate, which it reads off the frame rather than off a name.
    ion_roles: dict[str, str] = {}

    # The scale, always: a page with no technology named still gets `DEFAULT_TECH`, so
    # every emitted page -- the studio, the board pages, the site's examples -- can be
    # measured in micrometres.
    tech_block = tech_view_model(tech)

    return {
        "kicker": kicker or "ROUTING SCHEME",
        "headline": headline or f"{arch.name} · {prog.name}",
        "lede": lede or (arch.description or ""),
        "roles": roles,
        "ion_roles": ion_roles,
        # TWO LAYOUTS, and the page picks between them with its "true scale" toggle.
        # `layout` is the FIT -- it may stretch one axis by up to K_ANISO to fill the
        # viewport, which is legible and lies about every angle.  `layout_true` forces
        # `sx:sy` to the technology's nm-per-unit ratio, so a pixel is the same number of
        # nanometres on both axes and an angle read off the screen is the physical angle.
        # Both are computed HERE rather than one of them in the browser, because
        # `compute_layout` is the tested implementation and `engine.js` is its mirror;
        # shipping the pair costs under 2 KB and keeps the first paint server-decided.
        "layout": compute_layout(nodes, segments),
        "layout_true": compute_layout(
            nodes, segments, true_scale=True,
            unit_nm=(tech_block["nm_per_unit_x"], tech_block["nm_per_unit_y"])),
        # THE SCALE ITSELF.  Node positions are lattice units; every physical length on
        # the page is this block times one of them.
        "tech": tech_block,
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
        "source": source,
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
        # the generator and parameters each template's device came from, so the start
        # gallery can OPEN a shipped device (`from_template` with its own geometry)
        # rather than only borrow its physics
        "template_devices": _template_devices(arch, template_stems),
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
        # All 27 rule statements, so the Report pane can name what it did NOT check
        # without hard-coding 27 sentences in JavaScript.
        "rule_statements": rule_statements(),
        # THE COURSE'S PREPARED CASES AND PYTHON'S VERDICTS ON THEM.  Five rules are not
        # re-implemented in the browser (R4d, R7b, R9, R10, R16); the lessons that teach
        # them rebuild the case here from the same generator call and records, and show
        # this verdict labelled as computed at build time.  `tests/test_tutorial.py`
        # recomputes it from `qccd.viz.course` and compares.
        "tutorial_cases": _course.cases_for_page(),
        "tutorial_verdicts": _course.verdicts(model),
        "tutorial_measured": _course.measured(model),
        # THE RULE HALF OF THE CHECKSUM.  Twenty-one integers: how many violations Python
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
#: `js/transit.js` is the occupancy law -- where an ion is drawn while the machine is
#: moving it, and what it does when another ion is in the way.  It is listed FIRST because
#: it depends on nothing (it is handed its geometry) and because the page's own script and
#: the gadget Design canvas both call it: `qccd/gadget/page.py` inlines the same bytes.
#: Two canvases, one rule -- see the header of that file for what having two of them cost.
ENGINE_JS = ("js/transit.js", "js/edit.js", "engine.js")
#: `tutorial.js` is data -- the course's lessons -- and it registers itself with the editor
#: at its last line, so it must come after `editor.js` and needs nothing else.
EDITOR_JS = ("js/editor.js", "js/tutorial.js")


def page_stamp() -> str:
    """A digest of the code every page inlines, as opposed to the data it carries.

    `_TEMPLATE` is the page's own markup and script; `ENGINE_JS` and `EDITOR_JS` are the
    four files pasted into it.  Together they are everything about a page that comes from
    this repository rather than from the architecture being shown, so two pages built from
    the same code agree and a page built before a change does not.

    It is written into the head as `<meta name="qccd-page">` and asserted by
    `tests/test_board_engine.py`, whose subject -- the board's entry pages -- are COPIED by
    the site build rather than rendered, and so keep whatever code was current the day they
    were generated.  That is how eleven live pages spent three months running an engine
    without the `cylinder` generator in it.
    """
    h = hashlib.sha256()
    h.update(_TEMPLATE.encode("utf-8"))
    for name in ENGINE_JS + EDITOR_JS:
        h.update((_JS_DIR / name).read_bytes())
    return h.hexdigest()[:16]


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
    from ..arch.listing import template_records

    return {stem: [dict(r) for r in template_records(a)]
            for stem, a in _template_archs(arch, stems).items()}


def _template_archs(arch: Architecture, stems: "Sequence[str] | str | None" = None) -> dict:
    """`{stem: Architecture}` for every template the page carries -- the one stem list
    behind `templates` and `template_devices`, so the two cannot name different sets."""
    from ..arch import load as _load_arch

    out: dict[str, Architecture] = {}
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
        out[stem] = a
    return out


def _template_devices(arch: Architecture, stems: "Sequence[str] | str | None" = None) -> dict:
    """`{stem: {"generator", "params"}}` -- the GEOMETRY a template's records drop.

    A template is a physics package: `template_records` keeps the zones, curves and
    control block and drops the device, which is right for `Machine.ring(..., template=)`.
    But the start gallery also wants to OPEN a shipped device -- `grid9x9` as the 9x9 grid
    it is, not as a package for some other shape -- and `from_template` needs the
    generator and its parameters for that.  Reflected off the same architectures, the same
    way `listing.py` prints them: the non-default parameters only.  An explicit-geometry
    template has no generator to expand and is left out, so the gallery cannot offer it.
    """
    from ..arch.listing import _nondefault_params

    out: dict[str, dict] = {}
    for stem, a in _template_archs(arch, stems).items():
        dev = a.device
        if dev.generator == "explicit" or not dev.nodes:
            continue
        out[stem] = {"generator": dev.generator, "params": _nondefault_params(dev)}
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

#: The top-level directories of this repository, as they appear inside a JSON string value.
#: A reader has none of them; a path into one is a coordinate into something they cannot
#: open.  The BARE FILE NAME is fine and deliberately not matched -- `micro_chain.tsir.json`
#: and `demo.lq` are artifacts the reader is looking at and can export, and it is the folder
#: in front of them that is the tree.
_REPO_DIRS = ("qccd", "Compiler", "Codesign", "tests", "tools", "arch", "examples",
              "BBResults", "SmallCode", "out")
#: Written as an alternation rather than a character class so it survives a shell round
#: trip -- `[/\\]` loses a backslash to a heredoc and becomes an unterminated class.
_PAYLOAD_PATH = re.compile(
    r'"(?:[A-Za-z]:)?(?:\.{0,2}(?:/|\\\\))?(?:' + "|".join(_REPO_DIRS) +
    r')(?:/|\\\\)[^"]{0,160}"')


def _refuse_repository_paths(blob: str) -> None:
    """Raise if the page's data would tell a reader where this repository keeps its files.

    Called on the JSON *before* it is escaped into the page, so the match is against data
    the page carries rather than against the source comments of the JavaScript it inlines.
    """
    hits = sorted({m.group(0) for m in _PAYLOAD_PATH.finditer(blob)})
    if hits:
        raise ValueError(
            "the page's data would carry " + str(len(hits)) + " path(s) into this "
            "repository, which a reader cannot open: " + ", ".join(hits[:4]) +
            (" ..." if len(hits) > 4 else "") +
            " -- emit the bare artifact name instead of its location in the tree")


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
<meta name="qccd-page" content="__STAMP__">
<title>__TITLE__</title>
<style>
:root{
__CSSVARS__
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;overflow:hidden;background:var(--bg);color:var(--ink);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
/* THE APP FRAME.  The page is one viewport-high column -- a 44 px head, then the row of
   rail | stage | dock -- and nothing scrolls but the rail, the panes and the stage's own
   viewBox.  It used to be a document: at 1366x768 the ring page's stage started 835 px
   below the fold and Play was at y=1279, because the head, the metric tiles and the wide
   regime's wrapped dock each took their turn first. */
main{max-width:none;margin:0;padding:0;height:100vh;display:flex;flex-direction:column}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px}
/* scoped to the frame: `.pane` is a `.card` too, and an unscoped rule would beat
   `.pane{display:none}` and show all six panes at once */
main>.card{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;border:0;
  border-radius:0;padding:0}
/* THE HEAD is one 44 px toolbar: the name, the two counters, the metric chips and the
   two panel handles, none of which may wrap. */
.head{flex:0 0 44px;display:flex;align-items:center;gap:12px;padding:0 10px;
  border-bottom:1px solid var(--line);background:var(--panel);min-width:0;overflow:hidden}
.ttl{display:flex;align-items:baseline;gap:8px;min-width:0;flex:0 0 auto}
.kicker{color:var(--accent);font-size:10.5px;font-weight:700;letter-spacing:.09em;
text-transform:uppercase;white-space:nowrap}
h1{margin:0;font-size:16px;line-height:1.2;color:var(--navy);letter-spacing:-.01em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px}
.lede{color:var(--muted);margin:0;font-size:12px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;min-width:0;flex:1 1 0}
.counters{display:flex;gap:12px;flex:0 0 auto;text-align:right}
.counter{display:flex;align-items:baseline;gap:4px}
.counter span{color:var(--muted);font-size:10px;letter-spacing:.09em;
text-transform:uppercase}
.counter b{font-size:15px;font-variant-numeric:tabular-nums;line-height:1.1}
.counter .now{color:var(--accent)}
.counter .of{color:var(--muted);font-size:12px}
/* metrics as inline chips: the block rules they had clipped them to one word each */
/* chips that do not fit wrap to a second row that the box's height hides, so a chip
   drops whole instead of being cut mid-word behind the handles */
.metrics{display:flex;flex-wrap:wrap;gap:5px;flex:0 1 auto;min-width:0;max-height:24px;
  overflow:hidden;align-content:flex-start;white-space:nowrap}
.m{background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:2px 7px;
  min-width:0;white-space:nowrap;font-size:11px;flex:0 0 auto}
.m span{display:inline;color:var(--muted);font-size:10px;letter-spacing:.04em;
text-transform:uppercase;margin-right:4px}
.m b{display:inline;font-size:12px;font-variant-numeric:tabular-nums}
.grips{display:flex;gap:6px;flex:0 0 auto;margin-left:auto}
/* the narrow regime's head keeps the name, the counters and the handles */
@media (max-width:899px){.metrics,.lede{display:none}}
/* THE ROW: rail | stage | dock, a grid that fills the rest of the viewport.  `align-
   items:stretch`, never flex-start: with flex-start the canvas collapsed to 134 px. */
.row{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;
  grid-template-rows:minmax(0,1fr);align-items:stretch;flex:1 1 auto;min-height:0;
  margin:0;gap:0}
/* THE PROGRAMME COLUMN: the hardware programme and the source circuit beside the
   animation, the whole height of the frame, so a long programme reads as a listing
   rather than a slot.  In the wide regime it is always there and the strip under the
   canvas keeps the other panes; in the tall regime it is pinned or folded from the
   pane's own header, and it never exists in the narrow regime. */
.progcol{grid-column:3;grid-row:1;width:360px;min-width:0;display:flex;
  flex-direction:column;min-height:0;border-left:1px solid var(--line);
  background:var(--panel);position:relative}
.progcol[data-collapsed="1"]{display:none}
.progcol .pane{display:flex}
.row[data-layout="wide"] .progcol{grid-row:1/3}
.row[data-layout="narrow"] .progcol{display:none}
@media (max-width:1500px){.progcol{width:330px}}

/* the two listings inside the Program pane, and the switch between them */
.pblock{display:flex;flex-direction:column;gap:8px;min-height:0;flex:1 1 0}
.pblock[data-off="1"]{display:none}
.pblock h4{margin:0;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted)}
#paneP[data-view="hw"] #paneQ{display:none}
#paneP[data-view="gates"] #pHw{display:none}
#paneP[data-view="both"] .lst{min-height:80px}
#paneP[data-view="both"] .pblock{flex:1 1 50%}
.stage{display:flex;flex-direction:column;min-width:0;min-height:0;position:relative;
  grid-column:2;grid-row:1}
.stage[data-drop="1"] svg{outline:2px dashed var(--accent);outline-offset:-4px}
/* THE WIDE REGIME: a long, thin device takes the whole width and the dock becomes a
   strip of panes side by side under it.  `data-dock="1"` on the row (written by
   `foldPanel`) drops the strip's track, so folding the dock gives the canvas its
   height back. */
.row[data-layout="wide"]{grid-template-rows:minmax(0,1fr) minmax(220px,40vh)}
.row[data-layout="wide"][data-dock="1"]{grid-template-rows:minmax(0,1fr)}
.row[data-layout="wide"] .rail{grid-row:1/3}
.row[data-layout="wide"] .dock{grid-column:2;grid-row:2;width:auto;max-width:none;
  border-left:0;border-top:1px solid var(--line)}
/* ONE PANE AT A TIME, in the strip as in the dock: the menu in the head picks it.  The
   strip used to show every pane side by side, which was the busiest thing on the page. */
.row[data-layout="wide"] .panes{display:flex;min-height:0}
/* a 30vh strip is 229 px at 768: the NOW strip is one line there and the list keeps to
   the pane, so the pane does not become a second scroller around the list */
.row[data-layout="wide"] .dock .now{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  min-height:0;padding:3px 9px}
.row[data-layout="wide"] .lst{min-height:80px}
/* THE PALETTE RAIL: a fixed-width column that scrolls by itself.  In the narrow regime
   it is a drawer over the stage, folded on the way in and opened from its handle. */
.rail{grid-column:1;grid-row:1;width:224px;height:100%;max-height:none;min-height:0;
  overflow:auto;overscroll-behavior:contain;display:flex;flex-direction:column;gap:8px;
  min-width:0;padding:8px;border-right:1px solid var(--line);background:var(--bg);
  position:relative}
.row[data-layout="narrow"] .rail{position:fixed;left:0;top:44px;bottom:0;height:auto;
  width:224px;z-index:5;box-shadow:2px 0 10px rgba(0,0,0,.12)}
/* folded is folded: the handles live in the head, so nothing of a folded panel shows */
.rail[data-collapsed="1"]{display:none}
.dock[data-collapsed="1"]{display:none}
/* THE HANDLES, in the head, always in the same place, saying which way they go. */
.grip{font:600 11.5px/1 ui-sans-serif,system-ui,sans-serif;color:var(--muted);
  background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 8px;
  cursor:pointer;white-space:nowrap}
.grip:hover{color:var(--accent);border-color:var(--accent)}
/* The source pane. `.ql` mirrors `.al` (the architecture listing) so the two read the
   same; `.qh` is the statement the executing instruction is discharging and `.qw` one it
   is still travelling towards, which is the distinction somebody debugging a router
   needs at a glance. */
.ql .n{color:var(--muted);text-align:right;min-width:34px;padding-right:8px;
  font-variant-numeric:tabular-nums}
.ql .a{white-space:pre;overflow:hidden;text-overflow:ellipsis}
.ql.qh{background:color-mix(in srgb,var(--active) 26%,transparent)}
.ql.qw{background:color-mix(in srgb,var(--active) 10%,transparent)}
.ql.qz .a{color:var(--muted)}
.tab.off{display:none}
.pal{border:1px solid var(--line);border-radius:9px;padding:7px;background:var(--panel)}
/* the Selection section stays at the foot of the rail, over whatever scrolled under it */
#palInspect{position:sticky;bottom:0;margin-top:auto;z-index:1}
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
/* the divider between the shapes and the element tiles in Sketch mode: a heading, not a
   section, so the tiles below it keep their own headings */
.palnext{margin:12px 0 5px;font-size:10px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);border-top:1px solid var(--line);padding-top:9px}
.pal-item{display:flex;gap:8px;align-items:center;width:100%;text-align:left;
  padding:3px 5px;border:1px solid var(--line);border-radius:8px;background:var(--panel);
  cursor:pointer}
.pal-item:hover,.pal-item:focus-visible,
.pal-item[aria-pressed="true"]{align-items:flex-start}
.pal-item:hover{border-color:var(--accent)}
.pal-item[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
.pal-item[aria-pressed="true"] .pal-why,
.pal-item[aria-pressed="true"] .pal-meta{color:#c8cfe6}
.pal-item[aria-pressed="true"] .pal-how{color:#fff}
.avatar{flex:0 0 auto;display:block;width:40px;height:24px;border-radius:6px;
  background:var(--soft);border:1px solid var(--line);overflow:hidden}
.avatar svg{display:block;width:40px;height:24px;background:transparent;border:0;
  border-radius:0;margin:0;min-height:0;flex:none}
.pal-text{display:flex;flex-direction:column;gap:1px;min-width:0}
.pal-text b{font-size:12px;font-weight:650}
.pal-why,.pal-how,.pal-meta{display:none}
/* a tile unfolds only while ARMED, where its gesture line is the instruction being
   followed; on hover the hint card says what it is, and an unfolding tile used to push
   the whole list down under the pointer */
.pal-item[aria-pressed="true"] .pal-how{display:block}
.pal-why{font-style:normal;font-size:10.5px;line-height:1.32;color:var(--muted)}
.pal-how{font-style:normal;font-size:10px;color:var(--accent);font-weight:600}
.pal-meta{font-style:normal;font-size:10px;color:var(--muted)}
/* BLOCKS ARE NOT TILES.  A budget is not dropped on a canvas and must not look droppable. */
.palgrp[data-kind="block"] .pal-item{border-style:dashed;background:var(--soft)}
.palgrp[data-kind="component"] .avatar{width:48px;height:30px;background:var(--soft)}
.palgrp[data-kind="component"] .avatar svg{width:48px;height:30px}
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
.card2{flex:1 1 88px;padding:5px 7px;font-size:11.5px;border-radius:7px;text-align:left;
  display:flex;flex-direction:column;gap:1px;min-width:0}
.card2 b{font-size:12px;font-weight:650}
.card2 .sub{color:var(--muted);font-size:10.5px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.card2:hover{border-color:var(--accent)}
#palStartBody h5{margin:6px 0 5px;font-size:10px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--muted)}
#palStartBody h5:first-child{margin-top:0}
/* THE EMPTY STATE over the canvas.  Pointer events pass through everywhere but the cards,
   so an armed site still lands where you click; the cards sit at the top so the middle
   of the stage -- where a first click goes -- is free. */
/* THE EMPTY STAGE IS EMPTY.  The start cards moved to the tools bar (Start), so a blank
   canvas shows nothing but itself and takes a click at once; the element stays in the
   DOM (the harness reads its display state) and `!important` beats the inline toggle. */
.stage-empty{display:none!important}
.stage-empty{position:absolute;left:0;right:0;top:0;padding:22px 26px 0;
  pointer-events:none;z-index:2}
.stage-empty h3{margin:0 0 8px;font-size:15px;font-weight:650}
.stage-empty .cards{max-width:720px}
.stage-empty .card2{flex:1 1 150px;max-width:220px;padding:8px 10px;background:var(--panel)}
.stage-empty .mut{max-width:720px;margin:10px 0 0;font-size:12px;color:var(--muted)}
/* the package <select>, the shipped-device picker and the two stamp buttons: the start
   controls the empty state carries, so nothing on the rail is needed to begin */
.stage-empty .startrow{display:flex;flex-wrap:wrap;align-items:center;
  gap:6px 10px;max-width:720px;margin:10px 0 0;font-size:12px}
/* ONLY THE CONTROLS TAKE THE POINTER, never the rows that hold them.  A row is a
   full-width flex box, so `pointer-events:auto` on the row made every gap between the
   buttons swallow the click: measured on the blank page at 1366x768, three armed clicks
   in the top 319 px of the canvas placed nothing at all and said nothing -- the exact
   spot the empty state's own sentence tells a new user to click. */
.stage-empty button,.stage-empty select,.stage-empty input,
.stage-empty label{pointer-events:auto}
.stage-empty .startrow label{display:inline-flex;align-items:center;gap:5px;
  color:var(--muted)}
.stage-empty .startrow .mut{margin:0;flex:0 1 auto}
/* the row of shape buttons is a row wherever it is used -- the Start popover shows it
   too, and `.stage-empty .startrow` above only styles the (currently hidden) canvas one */
.startrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.startrow select,.startrow button{font:12px/1.4 inherit;padding:3px 7px;
  border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);
  max-width:260px}
.startrow button{cursor:pointer;font-weight:600}
.startrow button:hover{border-color:var(--accent)}
.startrow button[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);
  color:#fff}
.fieldrow button{flex:0 0 auto;padding:2px 8px;font-size:11px;border-radius:5px}
.inert{color:var(--muted);font-style:italic}
.fieldrow{display:flex;align-items:center;gap:6px;margin:3px 0;font-size:11.5px}
.fieldrow label{flex:0 0 92px;color:var(--muted)}
.fieldrow input,.fieldrow select{flex:1 1 auto;min-width:0;border:1px solid var(--line);
  border-radius:5px;padding:2px 5px;font:11.5px/1.4 inherit;background:var(--bg);
  color:var(--ink)}
.badge.unchecked{background:transparent;border:1px dashed var(--muted);color:var(--muted)}
/* THE PICTURE fills whatever the frame leaves it -- no clamp, no cap.  Its height used
   to be `clamp(420px,62vh,760px)` plus a JS `maxHeight`, and the two fought the page. */
svg{flex:1 1 auto;width:100%;height:100%;min-height:0;display:block;margin:0;
background:var(--panel);border:0;border-radius:0;touch-action:none;
cursor:grab;user-select:none;-webkit-user-select:none}
/* THE LIVE CURSOR is written to `style` by the editor (`setCursor` / `EDITOR.panning`):
   grab over empty stage, move over an element, grabbing while dragging or panning.  A
   class cannot be read back in the headless harness, so cursor state written as a class
   is cursor state with no test; `svg{cursor:grab}` above is the static floor under it. */
/* ONE-LINE STRIPS between the picture and the toolbar: the step, its reason and the red
   banner, each one line and ellipsised, so the transport below never moves. */
.step,.why,.invalid{flex:0 0 auto;margin:0;padding:2px 10px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;min-width:0;background:var(--panel)}
/* THE STRIP holds four things: the step, the price, the edit count and the problems
   button.  The two counters keep their width -- the problems button is the stage's only
   door to the problem list, so it is never the thing that gives way -- and the two texts
   share the slack: both ellipsise, the price twice as readily as the step (a warning can
   grow it past 600 px after one drop), and the step keeps a 160 px floor so a 440 px
   stage still names the step.  `overflow:hidden` is the backstop: the strip is never
   wider than the stage, so the page never grows a horizontal scrollbar. */
.strip{flex:0 0 auto;display:flex;align-items:center;gap:10px;min-width:0;
  overflow:hidden;border-top:1px solid var(--line);background:var(--panel);
  padding-right:10px}
.strip .step{flex:1 1 auto;border-top:0;min-width:160px}
.strip .sub{white-space:nowrap;flex:0 0 auto}
.strip .sub.price{flex:0 2 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;
  font-size:11.5px}
.strip .tgl{flex:0 0 auto;padding:1px 8px}
.exportrow{flex:0 0 auto}
.step{font-size:12.5px;border-top:1px solid var(--line)}
.step b{color:var(--navy)}
.step code{background:var(--soft);padding:0 5px;border-radius:4px;font-size:11.5px}
.strip #stDrive{flex:0 0 auto;padding:1px 8px;font-size:11.5px;border-radius:5px;margin:0}
.why{color:var(--muted);font-size:11.5px;min-height:19px}
.invalid{padding:3px 10px;font-size:12px;border-top:1px solid var(--z);color:var(--z);
  background:var(--bad_bg)}
/* ONE TOOLBAR under the picture: the transport, Snap, Undo, Redo and `?` on one line.
   It fits a 782 px stage (1366 wide, rail and dock open) because only stage tools live
   here: the edit and problem counters sit on the status strip beside the price, and the
   export picker sits beside the text it formats, in the Architecture pane's Source
   view.  It also fits the 696 px stage of a 1280 window with the slider at its 60 px
   floor; below ~680 px (a 1024 window with both panels open: 440 px) the tail wraps to
   a second line, since `overflow-x:auto` instead clipped Snap..? off the stage edge.
   The row keeps its height while the transport is folded away, so nothing jumps when a
   programme arrives.  `.ctrl`/`.ebar` are `display:contents`: the markup keeps its two
   groups and the toolbar lays out their children as one run. */
.stagebar{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:3px 3px;
  padding:4px 8px;min-height:40px;border-top:1px solid var(--line);
  background:var(--panel);white-space:nowrap}
.ctrl,.ebar{display:contents;margin:0}
.ebar .etools{display:contents}
/* THE SEGMENTED CONTROLS -- Sketch|Parts, and the four shape tools -- read as one control
   rather than as loose buttons: no gap between the members, and the run keeps its own
   rounding.  `display:inline-flex`, not `contents`: these ARE a group. */
.stagebar .seg{display:inline-flex;align-items:center;gap:0;flex:0 0 auto;
  border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-right:3px}
.stagebar .seg button{border:0;border-radius:0;margin:0}
.stagebar .seg button + button{border-left:1px solid var(--line)}
.stagebar button{padding:5px 8px;font-size:12.5px;flex:0 0 auto}
.stagebar select{padding:4px 6px;font-size:12px;flex:0 0 auto}
/* THE SCALE BAR, at the right end of the toolbar: a bracket as long on screen as the
   distance it names, and the name beside it.  Not on the canvas -- see placeScaleBar. */
.scalebar{margin-left:auto;display:inline-flex;align-items:center;gap:7px;padding:0 6px;
  flex:0 0 auto;white-space:nowrap;font-size:12px;color:var(--muted)}
.scalebar i{display:block;height:7px;border:1.6px solid var(--ink);border-top:0;box-sizing:border-box}
.scalebar b{font-weight:650}
button{background:var(--panel);color:var(--ink);border:1px solid var(--line);
border-radius:6px;padding:7px 13px;font:inherit;cursor:pointer}
button.p{background:var(--navy);border-color:var(--navy);color:#fff;font-weight:600}
button:hover{border-color:var(--muted)}
select{border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit;
background:var(--panel)}
input[type=range]{flex:1 1 60px;min-width:60px;max-width:260px;accent-color:var(--navy)}
.track{flex:0 0 auto;height:10px;background:var(--soft);border-top:1px solid var(--line);
display:flex;overflow:hidden;margin:0}
.track i{display:block;height:100%}
/* THE TRANSPORT APPARATUS FOLDS AWAY WHILE THERE IS NOTHING TO TRANSPORT. */
body[data-noprog="1"] .ctrl,
body[data-noprog="1"] #track,
body[data-noprog="1"] #tl,
body[data-noprog="1"] .metrics,
body[data-noprog="1"] .counters,
body[data-noprog="1"] #legendFold{display:none}
/* the legend folds: one summary line by default, the swatches on demand */
.legendfold{flex:0 0 auto;border-top:1px solid var(--line);background:var(--panel);
  padding:1px 10px;font-size:11.5px;color:var(--muted)}
.legendfold>summary{cursor:pointer;list-style:none;font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;font-weight:600;padding:2px 0}
.legendfold>summary::-webkit-details-marker{display:none}
.legendfold>summary::before{content:"\25b8 ";color:var(--accent)}
.legendfold[open]>summary::before{content:"\25be "}
.legend{display:flex;gap:12px;flex-wrap:wrap;margin:2px 0 4px;color:var(--muted);
font-size:11.5px;align-items:center}
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
   removes `.on`, CSS decides whether that means a tab or a grid column.  The dock is a
   column of the frame; each pane scrolls by itself and its listing takes the height
   the pane leaves it (the fixed 420 px list is gone with the JS that wrote it). */
.dock{grid-column:4;grid-row:1;width:372px;min-width:0;max-width:none;display:flex;
  flex-direction:column;min-height:0;border-left:1px solid var(--line);
  background:var(--panel)}
/* THE TOOLS BAR between the head and the row: everything that used to crowd the rail,
   each as a button opening a popover, and a search box over every control on the page.
   The popovers ARE the rail's own sections (the folds `renderPalette` writes into
   #palBody, the Start fold, the Selection section), shown fixed under their button:
   nothing moves in the DOM, so the harnesses that walk #palBody see what they saw. */
.tools{position:relative;display:flex;align-items:center;gap:6px;flex-wrap:wrap;
  padding:5px 10px;border-bottom:1px solid var(--line);background:var(--bg);
  font-size:12.5px;z-index:70}
.tools .tb{background:var(--panel);border:1px solid var(--line);border-radius:6px;
  padding:4px 10px;cursor:pointer;color:var(--ink)}
.tools .tb[aria-expanded="true"]{background:var(--soft);border-color:var(--navy);color:var(--navy)}
.tools .sw{position:relative;margin-left:auto;display:flex;align-items:center;gap:6px}
#search{width:min(360px,40vw);padding:5px 9px;border:1px solid var(--line);border-radius:6px;
  font:12.5px ui-sans-serif,system-ui,sans-serif;background:var(--panel);color:var(--ink)}
#search:focus{outline:2px solid var(--navy);outline-offset:0}
.sres{position:absolute;right:0;top:100%;margin-top:4px;width:min(520px,80vw);max-height:60vh;
  overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;
  box-shadow:0 8px 28px rgba(0,0,0,.14);z-index:90;display:none}
.sres[data-open="1"]{display:block}
.sres .r{padding:7px 10px;border-bottom:1px solid var(--line);cursor:pointer}
.sres .r:hover,.sres .r[data-sel="1"]{background:var(--soft)}
.sres .r b{font-size:13px}
.sres .r .w{color:var(--muted);font-size:11.5px;margin-left:6px}
.sres .r .d{color:var(--muted);font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sres .none{padding:8px 10px;color:var(--muted)}
/* the rail's popover sections: hidden in the rail, fixed under the bar when open */
#palBody details.palfold,#palStart{display:none}
.pop-open{display:block!important;position:fixed;z-index:85;width:400px;max-width:90vw;
  max-height:72vh;overflow:auto;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;box-shadow:0 10px 32px rgba(0,0,0,.16);padding:10px 12px;margin:0}
details.pop-open>summary{display:none}
details.pop-open{display:block!important}
.flash{outline:3px solid var(--navy)!important;outline-offset:2px;transition:outline-color .8s}
#rail{overflow:auto}
/* THE MENU, in the head: one item per panel.  An item opens its panel (the dock, or the
   strip under a long device); the lit item closes it again.  Program and Circuit drive the
   programme column when the programme lives there. */
.tabs{display:flex;flex-wrap:wrap;gap:2px;flex:0 0 auto;margin-right:6px}
.panes{flex:1 1 auto;min-height:0;display:flex;min-width:0}
.pane{display:none;flex:1 1 auto;flex-direction:column;gap:8px;min-width:0;min-height:0;
  overflow:auto;padding:10px;border:0;border-radius:0;container-type:inline-size}
.pane.on{display:flex}
/* the Circuit listing is off unless the page carries a source circuit -- an attribute,
   so the wide regime (every pane shown) cannot reveal an empty one */
.row .dock .pane[data-off="1"]{display:none}
.tab{padding:4px 9px;font-size:11.5px;border-radius:6px}
.tab.on{background:var(--navy);border-color:var(--navy);color:#fff;font-weight:650}
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
.lst{position:relative;overflow-y:auto;overflow-x:hidden;flex:1 1 auto;height:auto;
min-height:120px;overflow-anchor:none;
border:1px solid var(--line);border-radius:7px;background:var(--panel)}
.lst .pad{position:relative;width:100%}
.lst .win{position:absolute;left:0;right:0;top:0;will-change:transform}
.ln,.al{padding:0 8px;height:22px;line-height:22px;overflow:hidden;white-space:nowrap;
cursor:pointer;border-top:1px solid transparent;display:grid;gap:6px;
font:12px/22px ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
.ln{grid-template-columns:28px 44px 56px minmax(80px,1fr) 44px 22px}
/* six cells stay in the row (the harness reads them by index); the class column is the
   one that goes when the pane is narrow */
@container (max-width:310px){
  .ln{grid-template-columns:28px 44px minmax(80px,1fr) 44px 22px}
  .ln .cl{display:none}
}
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
.wrapl{position:relative;flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.tl{position:relative;flex:0 0 auto;height:10px;margin:0;overflow:hidden;
background:var(--soft);border-top:1px solid var(--line);display:flex;cursor:pointer}
.tl i{display:block;height:100%}
.playhead{position:absolute;top:0;bottom:0;width:2px;background:var(--accent)}
.help{position:fixed;inset:0;background:rgba(8,12,20,.72);z-index:9;padding:40px;
overflow:auto;color:#fff;font-size:13px}
.help.off{display:none}
.help>div{max-width:760px;margin:0 auto;background:var(--panel);color:var(--ink);
border-radius:12px;padding:22px}
.help td{padding:2px 8px}
.help td:last-child{text-align:left;color:var(--ink)}

/* ---- the editor ------------------------------------------------------------ */
.hud{position:fixed;z-index:3;pointer-events:none;background:var(--navy);color:#fff;
 border-radius:6px;padding:3px 8px;white-space:pre-line;max-width:min(60vw,44ch);
 transform:translate(12px,-26px);
 font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.hud.off{display:none}
/* ---- the explain layer: one card, one caption per region, one attribute ------------
   The card is `position:fixed` at the pointer and takes no pointer events, so it can
   never sit between the pointer and the thing it explains.  The captions live inside
   their regions and appear only while <body data-explain="1">. */
.hint{position:fixed;z-index:8;max-width:320px;background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;
  padding:7px 10px;font-size:12px;line-height:1.4;box-shadow:0 4px 14px rgba(0,0,0,.14);
  pointer-events:none}
.hint b{display:block;font-size:12.5px;margin-bottom:2px}
.hint span{display:block}
.hint i{display:block;font-style:normal;color:var(--accent);font-size:11px;margin-top:3px}
.hint i:empty{display:none}
.cap{display:none;position:absolute;z-index:6;left:8px;top:6px;background:var(--navy);
  color:#fff;border-radius:6px;padding:4px 9px;font-size:11.5px;line-height:1.35;
  max-width:min(380px,85%);box-shadow:0 2px 8px rgba(0,0,0,.2);pointer-events:none}
.cap b{display:block;font-size:12px}
.cap span{display:block;opacity:.92}
body[data-explain="1"] .cap{display:block}
#capStage{left:12px;top:12px}
#capBar{left:12px;top:auto;bottom:10px}
.stagebar{position:relative}
.dock{position:relative}
.grip[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
/* THE HEAD KEEPS FOUR NUMBERS on a laptop -- cost, steps, runtime, DACs -- and the rest
   appear from 1600 px; the Report carries every one of them at every width. */
.m[data-tier="2"]{display:none}
@media (min-width:1600px){.m[data-tier="2"]{display:block}}
/* the one-line summary is for the empty canvas; a device page says it with numbers */
body:not([data-noprog="1"]) .lede{display:none}
/* the rail's folded sections: a title, a count, one click */
#palBody .palfold{margin:0 0 8px}
#palBody .palfold>summary{padding:3px 0}
#palBody .palfold[open]>summary{margin-bottom:5px}
/* the guide's glossary: the stage's own marks beside their plain sentence */
.gloss{display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center;
  margin:4px 0 12px;font-size:12.5px}
.gloss .avatar{width:56px;height:34px}
.gloss .avatar svg{width:56px;height:34px}
.steps{margin:4px 0 12px;padding-left:20px;font-size:12.5px}
.steps li{margin:5px 0}
/* ---- the course ---------------------------------------------------------------------- */
.learn{display:flex;flex-direction:column;gap:8px;font-size:12.5px;line-height:1.45}
.learn-nav{display:flex;gap:4px;align-items:center}
.learn-nav select{flex:1 1 auto;min-width:0;font:12px/1.4 inherit;padding:3px 6px;
  border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink)}
.learn-nav button{padding:3px 8px}
.learn-head{display:flex;align-items:baseline;gap:8px}
.learn-head b{font-size:14px}
.learn-stars{color:var(--gold,#d9a400);letter-spacing:.06em;font-size:13px;margin-left:auto}
.learn-story{color:var(--muted);font-style:italic}
.learn-p{margin:0}
.learn .term{border-bottom:1px dotted var(--accent);cursor:help}
.learn-ex{border:1px solid var(--line);border-left:3px solid var(--navy);border-radius:8px;
  padding:8px 10px;background:var(--soft)}
.learn-ex.extra{border-left-color:var(--gold,#d9a400)}
.learn-exh{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:3px}
.learn-btns{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.learn-btns button{padding:4px 10px;font-size:12px}
.learn-choices{display:flex;flex-direction:column;gap:4px;margin-top:6px}
.learn-fb{border-radius:8px;padding:8px 10px;border:1px solid var(--line)}
.learn-fb.ok{background:var(--ok_bg);color:var(--cold);border-color:transparent}
.learn-fb.bad{background:var(--warn_bg);color:var(--warn_ink);border-color:transparent}
.learn-verdict{border:1px solid var(--line);border-left:3px solid var(--muted);border-radius:8px;
  padding:8px 10px;background:var(--soft);font-size:12px}
.learn-verdict .vstate{font-weight:600;padding:1px 6px;border-radius:6px;margin-left:4px}
.learn-verdict .vstate.failed{background:var(--warn_bg);color:var(--warn_ink)}
.learn-verdict .vstate.passed{background:var(--ok_bg);color:var(--cold)}
.learn-verdict .vstate.skipped,.learn-verdict .vstate.partial{background:var(--line);color:var(--ink)}
.learn-verdict ul{margin:4px 0 0 16px;padding:0}
.learn-verdict li{margin:2px 0}
.learn-table{width:100%;border-collapse:collapse;font-size:11.5px;margin:4px 0}
.learn-table th,.learn-table td{padding:2px 6px;text-align:right;border-bottom:1px solid var(--line)}
.learn-table th:first-child,.learn-table td:first-child{text-align:left}
.learn-note{border:1px dashed var(--line);border-radius:8px;padding:8px 10px;background:var(--soft)}
.learn-fbstars{display:block;color:var(--gold,#d9a400);font-size:15px;letter-spacing:.08em;margin-bottom:2px}
.learn-hints{display:flex;flex-direction:column;gap:4px}
.learn-hint{display:flex;gap:8px;color:var(--muted)}
.learn-hint span{flex:0 0 auto;width:16px;height:16px;border-radius:99px;background:var(--navy);
  color:#fff;font-size:10px;line-height:16px;text-align:center}
.lesson-strip{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:5px 8px;
  border:1px solid var(--line);border-left:3px solid var(--navy);border-radius:7px;
  background:var(--soft);font-size:12px}
.lesson-strip span{flex:1 1 200px;min-width:0}
.lesson-strip button{padding:2px 8px;font-size:11.5px}
/* THE CHEER: a green sweep over the canvas when a check passes.  An attribute the
   stylesheet animates; the verdict itself never rides on it. */
@keyframes cheer{0%{box-shadow:inset 0 0 0 0 rgba(15,118,110,0)}
  30%{box-shadow:inset 0 0 0 6px rgba(15,118,110,.55)}
  100%{box-shadow:inset 0 0 0 0 rgba(15,118,110,0)}}
.canvas[data-cheer="1"]{animation:cheer 1.3s ease-out 1}
.hud.warn{background:var(--warn_ink)}
.hud.bad{background:var(--z)}
.canvas{position:relative;flex:1 1 auto;min-height:0;display:flex}
.toasts{position:absolute;left:12px;bottom:12px;z-index:4;display:flex;
 flex-direction:column;gap:6px;max-width:min(560px,86%);pointer-events:none}
.toast{background:var(--panel);border:1px solid var(--line);pointer-events:auto;
 border-left:3px solid var(--accent);border-radius:7px;padding:7px 10px;font-size:12.5px;
 box-shadow:0 2px 10px rgba(0,0,0,.16);cursor:pointer;white-space:pre-line}
/* A REFUSAL THAT IS NOT A TOAST.  A toast is dismissible and it fades; "this programme
   moves an ion where there is no rail" is a standing fact about the picture, so it stays
   on the canvas until the device or the programme changes.  `style.display`, never a
   class -- `classList` is a no-op in tests/shim.mjs. */
.norail{position:absolute;right:12px;top:12px;z-index:4;background:var(--z);color:#fff;
 border-radius:7px;padding:5px 9px;font-size:12px;font-weight:650;pointer-events:none;
 box-shadow:0 2px 10px rgba(0,0,0,.2)}
/* THE ELEMENT MENU, at the pointer: the one place per-element actions live now.  A
   disabled item keeps its REASON on the screen under it (.cmwhy) rather than only in a
   tooltip -- a greyed control with no explanation is the exact question this menu exists
   to answer.  The Modify panel replaces its body in place, anchored where it was. */
.ctxmenu{position:fixed;z-index:88;min-width:238px;max-width:330px;max-height:78vh;
 overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;
 box-shadow:0 12px 34px rgba(0,0,0,.2);padding:6px}
.ctxmenu .cmhead{font-weight:650;font-size:11.5px;color:var(--muted);padding:4px 8px 6px;
 border-bottom:1px solid var(--line);margin-bottom:4px}
.ctxmenu .cmitem{display:block;width:100%;text-align:left;border:0;background:none;
 color:var(--ink);padding:5px 8px;border-radius:6px;font-size:12.5px;cursor:pointer}
.ctxmenu .cmitem:hover:not([disabled]){background:var(--soft)}
.ctxmenu .cmitem[disabled]{color:var(--muted);cursor:default}
.ctxmenu .cmmore{font-weight:600}
.ctxmenu .cmsub{margin:2px 0 4px 10px;border-left:2px solid var(--line);padding-left:6px}
.ctxmenu .cmwhy{color:var(--muted);font-size:11px;line-height:1.35;padding:0 8px 6px}
.ctxmenu .cmerr{color:var(--z);font-size:11.5px;line-height:1.4;padding:3px 8px}
.ctxmenu .cmerr:empty{padding:0}
.ctxmenu .cmfoot{color:var(--muted);font-size:10.5px;padding:5px 8px 2px;
 border-top:1px solid var(--line);margin-top:4px}
.ctxmenu .fieldrow{padding:0 6px}
.ctxmenu .fieldrow label{flex:0 0 66px}
.ctxmenu .formbtns{padding:2px 6px}
.ctxmenu #mdlgExplode{margin:6px;width:calc(100% - 12px);white-space:normal}
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
/* in the pane the two textareas share the height (1:1) rather than each taking its
   floor and pushing the export text below the viewport */
.srcwrap textarea.src{flex:1 1 0;min-height:90px}
.srcerr{min-height:1.2em;color:var(--z);
 font:11.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
</style></head><body><main>
<div class="card">
  <!-- THE HEAD: one 44 px toolbar -- name, the one-line lede, the two counters, the
       metric chips and the two panel handles.  The handles live here rather than inside
       the panels they fold, so a folded panel can be opened from the same place. -->
  <div class="head">
    <div class="ttl">
      <div class="kicker" id="kicker"></div>
      <h1 id="title"></h1>
    </div>
    <p class="lede" id="lede"></p>
    <div class="counters">
      <div class="counter" data-hint="c:steps"><span>Steps</span>
        <b><i class="now" id="cSteps">0</i><i class="of" id="cStepsOf"></i></b></div>
      <div class="counter" data-hint="c:cost"><span>Cost</span>
        <b><i class="now" id="cCost">0</i><i class="of" id="cCostOf"></i></b></div>
    </div>
    <div class="metrics" id="metrics"></div>
    <div class="grips">
      <!-- EXPLAIN labels the regions; the state is `aria-pressed` here and `data-explain`
           on <body>, both attributes, so the harness and the stylesheet read one truth. -->
      <button class="grip" id="eExplain" aria-pressed="false" data-hint="explain"
              title="label the parts of the screen (h)">Explain</button>
      <button class="grip" id="railGrip" title="hide the element rail ([)" data-hint="gripRail"
              aria-expanded="true">&#9664; Elements</button>
      <nav class="tabs" id="tabs" data-prog="dock" aria-label="panels">
        <button class="tab" id="tabL" data-hint="tab:L">Learn</button>
        <button class="tab" id="tabP" data-hint="tab:P">Program</button>
        <button class="tab off" id="tabQ" data-hint="tab:Q">Circuit</button>
        <button class="tab" id="tabA" data-hint="tab:A">Device</button>
        <button class="tab" id="tabM" data-hint="tab:M">Machine</button>
        <button class="tab" id="tabW" data-hint="tab:W">Write</button>
        <button class="tab" id="tabR" data-hint="tab:R">Report</button>
      </nav>
    </div>
  </div>
  <!-- THE TOOLS BAR.  Each button shows one of the rail's sections as a popover; the
       search box finds any control, hint or lesson.  `#bbtools` is a marker only: the
       BBResults overlay (Codesign/scripts/bb_studio.py) builds its own bar unless an
       element by that id exists, and this page already has the native one. -->
  <div class="tools" id="tools" data-hint="region:tools">
    <div class="cap" id="capTools"></div>
    <button class="tb" id="tbStart" aria-expanded="false" data-pop="start" data-hint="tools:start">Start</button>
    <button class="tb" id="tbRows" aria-expanded="false" data-pop="row" data-hint="tools:rows">Append a row</button>
    <button class="tb" id="tbMachine" aria-expanded="false" data-pop="block" data-hint="tools:machine">Machine settings</button>
    <button class="tb" id="tbComponents" aria-expanded="false" data-pop="component" data-hint="tools:components">Components</button>
    <!-- NO "Selection" BUTTON.  Its popover showed `#palInspect`, which is the same DOM
         node the rail already carries, and `window.onInspector` popped it open on every
         selection -- the busiest thing on the page.  Per-element actions are the
         right-click menu's now; the inspector stays in the rail, where it is read. -->
    <div class="sw"><input id="search" type="search" placeholder="find any feature&hellip;  (Ctrl+K)"
         autocomplete="off" data-hint="search"><div class="sres" id="sres"></div></div>
    <span id="bbtools" hidden></span>
  </div>
  <div class="row" id="row" data-dock="1">
    <!-- THE PALETTE.  Generated from `D.schema` (every CLOSED object, field for field)
         union `D.consumers` (the OPEN maps the schema cannot describe, each field with
         WHO READS IT) union `D.defaults` (dataclass defaults by reflection).  Never a
         literal list: a hand-written palette is a second source of truth, and 27 of the
         65 open fields are read by nothing at all, which the palette has to say. -->
    <aside class="rail" id="rail" data-collapsed="0">
      <div class="cap" id="capRail"></div>
      <!-- THE START FOLD FIRST, compact, and closed until the user opens it: the stage's
           own empty state (#stageEmpty) is where a new device starts, so the element
           tiles below stay on screen at every laptop size.  `renderStart` fills it. -->
      <details class="pal palfold" id="palStart"><summary>Start &mdash; new or template device</summary>
        <div id="palStartBody"></div></details>
      <section class="pal" id="palElements"><h4>Elements</h4><div id="palBody"></div></section>
      <section class="pal" id="palInspect"><h4>Selection</h4><div id="palInsp"></div></section>
    </aside>
    <div class="stage">
      <!-- THE CANVAS BOX: the picture, the drag HUD and the toast strip together, so a
           refusal is drawn over the picture it refers to and not below the legend. -->
      <div class="canvas" id="canvas">
        <svg id="svg" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="cap" id="capStage"></div>
        <div class="cap" id="capBar"></div>
        <div class="hud off" id="hud"></div>
        <!-- THE LOUD FAILURE for a move with no rail under it.  `edgePoint` used to
             draw a straight chord between two nodes no segment joins, which is the page
             inventing hardware; it now refuses, parks the ion on its last real node and
             says so here. -->
        <div class="norail" id="norail" style="display:none"></div>
        <div class="toasts" id="toasts"></div>
        <!-- THE ELEMENT MENU and the Modify panel it opens: what a right-click on a part
             offers.  `position:fixed` at the pointer and filled entirely by `paintMenu()`
             in editor.js; its state is `data-open` plus `style.display`, never a class,
             because `classList` is a no-op in tests/shim.mjs. -->
        <div class="ctxmenu" id="ctxmenu" data-open="0" style="display:none"
             data-hint="ctxmenu"></div>
      </div>
      <!-- THE EMPTY STATE: the start cards, the physics package, the shipped devices
           and the two stamp buttons, drawn over the canvas while it has no node.  Shown
           and hidden through `style.display` from `paint()`, never removed and never a
           class -- the harness reads it back.  It takes no pointer events except on its
           controls, so the stage under it still places an armed site. -->
      <div class="stage-empty" id="stageEmpty" style="display:none"></div>
      <!-- the step, the price and the two counters share one line: the texts ellipsise
           (the price first), the counters keep their width, and the toolbar below stays
           a single row on a wide stage -->
      <div class="strip"><div class="step" id="status" data-hint="status"></div>
        <!-- ITS OWN FLEX ITEM, never inside the ellipsised sentence: on a 440 px stage
             the sentence gives way and the button stays whole and clickable.  Shown
             and hidden through `style.display` from `draw()` while there are no frames. -->
        <button id="stDrive" class="p" type="button" style="display:none" data-hint="testdrive">Test drive</button>
        <span class="sub price" id="ePrice" data-hint="price"></span>
        <span class="sub" id="eCount" data-hint="edits">0 edits</span>
        <button class="tgl" id="eProb" data-hint="problems">0 problems</button>
        <!-- THE SECOND, QUIETER REGISTER.  A note is an observation about what has NOT
             been drawn yet -- a zone type no site uses, a movement class whose orbit
             matches no loop -- and counting those as problems is what made a blank canvas
             open on "10 problems".  Hidden while there are none; `style.display` from
             `paint()`, never a class. -->
        <button class="tgl" id="eNotes" data-hint="notes" style="display:none">0 notes</button></div>
      <div class="why" id="why"></div>
      <!-- The loud banner for "the compiled programme is not a programme for this
           device any more".  Shown and hidden through `style.display`, NEVER through a
           class: `classList` is a no-op in tests/shim.mjs, so a class-driven banner is a
           banner no harness can read, and the freeze would ship untested. -->
      <div class="invalid" id="invalid" style="display:none"></div>
      <!-- ONE TOOLBAR: the transport and the editing tools on one line, always under the
           picture.  `.stagebar`, not `.bar` -- that class is the legend swatch. -->
      <div class="stagebar" id="stagebar">
      <div class="ctrl">
        <button class="p" id="play" data-hint="play">Play</button>
        <button id="step" title="next step (→)" data-hint="step">Step</button>
        <button id="glide" title="glide one step (Enter)" data-hint="glide">Glide</button>
        <button id="phase" title="next phase" data-hint="phase">Phase</button>
        <button id="reset" data-hint="reset">Reset</button>
        <button id="fit" data-hint="fit">Fit</button>
        <input type="range" id="slider" min="0" value="0" data-hint="slider">
        <select id="speed" data-hint="speed"><option value="1">1x</option>
        <option value="4" selected>4x</option>
        <option value="16">16x</option><option value="64">64x</option>
        <option value="256">256x</option></select>
        <select id="mode" title="colour the marks by role or by heating" data-hint="colour">
        <option value="role" selected>role</option>
        <option value="heat">heating</option></select>
      </div>
      <div class="ebar" id="ebar">
        <span class="etools" id="etools">
          <!-- TWO MODES.  Sketch draws the shape the ions travel on and fills it with
               trapping sites; Parts is the element-at-a-time tool, unchanged.  The
               choice is remembered (`qccd.studio.mode`), and a canvas with no device on
               it opens on Sketch.  Everything in `#etools` is hidden on the website's
               view-only embeds, so the shape tools cannot be reached there. -->
          <span class="seg" id="tMode" data-hint="mode">
            <button class="tgl on" id="tModeSketch" aria-pressed="true" data-hint="mode:sketch"
                    title="draw the shape first: a rectangle, a circle, a line or a polyline, filled with trapping sites on release (d)">Sketch</button>
            <button class="tgl" id="tModeParts" aria-pressed="false" data-hint="mode:parts"
                    title="place sites, junctions and rails one at a time (d)">Parts</button>
          </span>
          <!-- NO SHAPE BUTTONS HERE.  These four were pure duplicates of the rail's
               shape tiles, wired to the same `sketchTool` verb, and r/e/n/p were a third
               way to the same place.  The tiles say what each shape draws and what the
               gesture is; a toolbar toggle could only say "Rect". -->
          <button class="tgl" id="tSnap" aria-pressed="false" data-hint="snap"
                  title="land parts on the lattice (off: anywhere; shift for quarter steps; alt frees)">Snap</button>
          <button class="tgl on" id="tTrue" aria-pressed="true" data-hint="truescale"
                  title="one screen pixel is the same distance on both axes, so an angle on the screen is the angle on the die">True scale</button>
          <button class="tgl" id="tMeasure" aria-pressed="false" data-hint="measure"
                  title="measure a distance or an angle: click two points, then a third for the angle between them (M)">Measure</button>
          <button id="eUndo" title="undo (ctrl+z)" data-hint="undo">Undo</button>
          <button id="eRedo" title="redo (ctrl+shift+z)" data-hint="redo">Redo</button>
          <button id="eHelp" title="the guide: what everything is, and every gesture and key (?)" data-hint="help">?</button>
        </span>
      </div>
      <!-- THE SCALE BAR: in the toolbar, under the picture and never on it (placeScaleBar) -->
      <span class="scalebar" id="scaleBar" data-hint="scalebar"><i id="scaleLine"></i><b id="scaleTxt"></b></span>
      </div>
      <div class="track" id="track" title="timeline by operation class"></div>
      <div class="tl" id="tl" title="the program in order; click to seek"></div>
      <details class="legendfold" id="legendFold"><summary>Legend</summary>
        <div class="legend" id="legend"></div></details>
    </div>
    <!-- THE PROGRAMME COLUMN.  `placeProgram()` moves #paneP in here (wide regime, or
         pinned in the tall one) and back into the dock; the pane itself is one node. -->
    <div class="progcol" id="progcol" data-collapsed="1">
      <div class="cap" id="capProg"></div>
    </div>
    <!-- THE DOCK: closed by default, one pane at a time, opened from the menu in the head
         (or the 1/2/3 and ] keys).  Under a long device it is the strip below the canvas. -->
    <div class="dock" id="dock" data-collapsed="1" data-prog="dock">
      <div class="cap" id="capDock"></div>
      <div class="panes" id="panes">

        <!-- THE COURSE.  Rendered by `renderLearn()` from the lessons `tutorial.js` registers;
             every button is an adapter onto a lesson verb on the API. -->
        <section class="pane card" id="paneL">
          <header class="ph"><h3>Learn</h3><span class="grow"></span>
            <span class="sub" id="learnCount"></span></header>
          <div class="learn" id="learnBody"></div>
        </section>

        <!-- THE PROGRAM PANE: two listings, one switch.  The hardware programme and the
             source circuit it was compiled from (when the page carries one), shown one at
             a time or stacked, in lockstep with the animation.  `#paneQ` keeps its id and
             its `data-off` switch: the harnesses read both. -->
        <section class="pane card on" id="paneP" data-view="hw">
          <header class="ph"><h3>Program</h3>
            <span class="seg" id="pView" data-hint="progview" style="display:none">
              <button class="on" id="pvhw" data-view="hw" title="the hardware instructions">Hardware</button>
              <button id="pvgates" data-view="gates" title="the circuit statements">Gates</button>
              <button id="pvboth" data-view="both" title="both, stacked">Both</button>
            </span>
            <span class="grow"></span>
            <button class="grip" id="pPin" data-hint="progpin" aria-pressed="false"
                    title="show the programme beside the animation, or back among the panels">&#9664; Beside the animation</button>
          </header>
          <div class="pblock" id="pHw">
            <div class="ph"><h4>Hardware program</h4><span class="sub" id="pCount"></span>
              <span class="grow"></span>
              <input class="filter" id="pFilter" type="text" spellcheck="false"
                     placeholder="filter (/)" data-hint="filter">
              <button class="tgl on" id="pFollow" aria-pressed="true" data-hint="follow"
                      title="keep the executing instruction in view (f)">Follow</button>
            </div>
            <div class="now" id="pNow"></div>
            <div class="wrapl">
              <div class="lst" id="pScroll">
                <div class="pad" id="pPad"><div class="win" id="pWin"></div></div>
              </div>
              <button class="chip off" id="pChip"></button>
            </div>
            <footer class="pf" id="pFoot"></footer>
          </div>
          <div class="pblock" id="paneQ" data-off="1">
            <div class="ph"><h4>Source circuit</h4><span class="sub" id="qCount"></span>
              <span class="grow"></span>
              <button class="tgl on" id="qFollow" aria-pressed="true"
                      title="keep the statement being executed in view">Follow</button>
            </div>
            <div class="now" id="qNow"></div>
            <div class="wrapl">
              <div class="lst" id="qScroll">
                <div class="pad" id="qPad"><div class="win" id="qWin"></div></div>
              </div>
            </div>
            <footer class="pf" id="qFoot"></footer>
          </div>
        </section>

        <section class="pane card" id="paneA">
          <header class="ph"><h3>Device</h3>
            <span class="seg" data-hint="archview"><button class="on" id="avB">Program</button
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
            <!-- the export picker lives beside the text it formats, not on the toolbar -->
            <div class="ph exportrow"><span class="sub">export</span>
              <select id="eWhich"><option value="py" selected>as Python</option>
                <option value="json">as .arch.json</option>
                <option value="tsir">as .tsir.json</option>
                <option value="edits">as edit ops</option></select>
              <button id="eCopy">Copy</button></div>
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
          <header class="ph"><h3>Write a programme</h3>
            <span class="sub" id="pwCount"></span>
            <span class="grow"></span>
            <button id="pwDrive" title="write and play a programme that fits this device">Test drive</button>
            <button id="pwRun" class="p" data-hint="evaluate">Evaluate</button>
          </header>
          <!-- the lesson in one line, while one is open: the exercise and the two buttons
               that matter here, so a programming exercise never needs a tab switch -->
          <div class="lesson-strip" id="pwLesson" style="display:none"></div>
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
<!-- the guide (drawn marks, three steps) and the key tables are two hosts: the tables
     stay `innerHTML` so the harness census can read every row back, as it always has -->
<div class="help off" id="help"><div id="helpCard"><div id="guideBody"></div><div id="helpBody"></div></div></div>
<!-- THE HINT CARD: one element, filled by `EDITOR.hintShow` from HINTS, positioned at
     the pointer, hidden through `style.display`.  Text only, never markup. -->
<div class="hint" id="hint" style="display:none" data-on="0"><b id="hintT"></b><span id="hintD"></span><i id="hintK"></i></div>
</main>
<script id="data" type="application/json">__DATA__</script>
__ENGINE__
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const A = D.arch, P = D.program, PH = D.physics, L = D.layout;
const nodeById = {}; A.nodes.forEach(n => nodeById[n.id] = n);
const segById = {}; A.segments.forEach(s => segById[s.id] = s);

// ---------- THE SCALE.  A lattice unit is not a length; the technology says what is ----
// `D.tech` is the technology sidecar's numbers, in INTEGER NANOMETRES, and it is the ONLY
// place a physical length enters this page.  Every readout below -- the tooltips, the drag
// HUD, the scale bar, the ruler, and the DC electrode tiling -- is one of these times a
// lattice distance, and nothing converts units twice.  Every emitted page carries one
// (`render.py::DEFAULT_TECH`), so there is no unmeasurable-page branch to maintain; the
// literal below exists only so a page emitted before this block still draws.
const TECH = D.tech || {preset:'surface_default', nm_per_unit_x:464000,
  nm_per_unit_y:464000, w_rf:60000, w_dc:50000, l_dc:50000, g_dc:8000, g_rf:10000,
  n_dc_pairs:3, dc_pitch:58000};
const NM_X = TECH.nm_per_unit_x, NM_Y = TECH.nm_per_unit_y;
// LATTICE -> MICROMETRES, PER AXIS.  The two scales are separate numbers with separate
// sources: a device whose y-extent is 1.0 is not thereby one axial trap pitch tall, and a
// single global scale would be a drawing convention pretending to be a physical claim.
function toUm(x, y){ return { x: x*NM_X/1000, y: y*NM_Y/1000 }; }
// a lattice DISPLACEMENT as a physical length, in um.  Not `hypot(dx,dy)*k`: the two
// components scale by different numbers, so the hypotenuse has to be taken AFTER.
function distUm(dx, dy){ const a=dx*NM_X/1000, b=dy*NM_Y/1000; return Math.sqrt(a*a+b*b); }
// one number, in the unit that keeps it readable
function fmtUm(v){
  const a=Math.abs(v);
  if(a>=1000) return (v/1000).toFixed(3)+' mm';
  if(a>=1) return v.toFixed(1)+' um';
  return (v*1000).toFixed(0)+' nm';
}
const um1 = v => +v.toFixed(1);

// ---------- TRUE SCALE, on by default ----------
// On: `sx:sy` is the technology's nm-per-unit ratio, so one pixel is the same number of
// nanometres on both axes and an angle measured on the screen is the angle the metal
// makes.  Off: the fit may stretch one axis by up to K_ANISO to fill the viewport -- more
// legible on a 72:1 device, and wrong about every angle on it.  Remembered exactly as the
// Snap toggle is, and read HERE rather than in editor.js because `L` has to be right
// before the first mark is drawn.  `tests/studio.mjs` deliberately does not stub
// localStorage, so a harness always gets the default.
const TS_KEY = 'qccd.studio.truescale';
let TRUE_SCALE = true;
try {
  const _tsv = globalThis.localStorage && globalThis.localStorage.getItem(TS_KEY);
  if(_tsv !== null && _tsv !== undefined) TRUE_SCALE = _tsv === '1';
} catch(e){ /* no store: the default stands */ }
// `hold` is `(sx, sy, ox, oy)` an EDIT keeps, so the drawing does not move under the
// pointer; absent (null) everywhere the view is meant to re-fit: Fit, a new device,
// the true-scale toggle.
const layoutOpts = (hold) => ({ true_scale: TRUE_SCALE, unit_nm: [NM_X, NM_Y],
                                hold: hold || null });
// Python computed BOTH layouts; the toggle picks one.  `L` is mutated, never replaced --
// every closure below and in editor.js captured this exact object.
if(TRUE_SCALE && D.layout_true) Object.assign(L, D.layout_true);
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
  'ion_stroke','neutral','rotate_alt','beam_one','beam_two','beam_meas','beam_init',
  'zone_data','zone_trap',
  'zone_load','zone_register','zone_other']) C[k] = css(k);
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
//
// TRUE SCALE narrows that gap but does not close it.  With the toggle on, sx:sy IS the
// technology's nm-per-unit ratio, so the schematic no longer misstates any ANGLE -- but
// its overall magnification is still chosen to fill the viewport while the metal below is
// fitted to its own bounding box, so the two are similar rather than coincident.  The
// scale bar is what makes each of them readable on its own terms.
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
const SEGEL={}, SEGINFO={}, PAD_BY_SEG={}, PAD_BY_SITE={}, SITE_SPAN={},
      SEG_BY_PAIR={}, NODEEL={}, CAPTXT={};
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
// The drawn length of one segment: the chord, or the curve's own arc length when it is
// bowed.  Sampled once per segment in `buildStatic` and cached on the SEGINFO record,
// because `pointOnPath` needs it for EVERY ion on EVERY frame.
function bezLen(I){
  if(!I.cp) return I.len;
  let s=0, ax=I.ax, ay=I.ay;
  for(let i=1;i<=16;i++){ const q=bezPoint(I, i/16);
    s+=Math.hypot(q.x-ax, q.y-ay); ax=q.x; ay=q.y; }
  return s;
}
function edgeLen(aId, bId){
  const sid=SEG_BY_PAIR[aId+'>'+bId], I=(sid!==undefined)?SEGINFO[sid]:null;
  if(I) return I.alen || I.len || 0;
  const a=nodeById[aId], b=nodeById[bId];
  return (a&&b) ? Math.hypot(px(b)-px(a), py(b)-py(a)) : 0;
}

// NO RAIL, NO MOVE -- and no silent chord.
//
// `edgePoint` used to fall back to a straight line between the two nodes whenever no
// segment joined them.  That is the page DRAWING A RAIL THE DEVICE DOES NOT HAVE: the ion
// crosses bare substrate, over electrodes it is not riding, and the picture says the
// machine can do something it cannot.  The verifier now refuses such a programme outright
// (R21 forbids a rail through a foreign node or a crossing, and the replay raises when two
// consecutive path nodes have no segment), so this branch should be unreachable -- which
// is exactly why it must be loud rather than plausible when it is reached.  Once in the
// console, once on the canvas, and the ion stays on the last node it was really on.
const NORAIL = {};
function noRail(aId, bId){
  const k = aId+'>'+bId;
  if(NORAIL[k]) return;
  NORAIL[k] = 1;
  try { console.error('no rail between '+aId+' and '+bId+': this programme moves an ion '+
                      'where the device has no segment, so nothing is drawn between them'); }
  catch(e){ /* no console */ }
  const b = document.getElementById('norail');
  if(b){ b.textContent = 'no rail between '+aId+' and '+bId; b.style.display='block'; }
}
function clearNoRail(){
  for(const k in NORAIL) delete NORAIL[k];
  const b = document.getElementById('norail');
  if(b) b.style.display = 'none';
}
function edgePoint(aId, bId, t){
  const sid=SEG_BY_PAIR[aId+'>'+bId];
  if(sid===undefined){
    noRail(aId, bId);
    const a=nodeById[aId] || nodeById[bId];
    return a ? {x:px(a), y:py(a), norail:true} : null;
  }
  const I=SEGINFO[sid];
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
        SEG_BY_PAIR=S.reg.SEG_BY_PAIR, NODEEL=S.reg.NODEEL, CAPTXT=S.reg.CAPTXT,
        PAD_BY_SITE=S.reg.PAD_BY_SITE || {}, SITE_SPAN=S.reg.SITE_SPAN || {};
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
    I.alen = bezLen(I);          // the DRAWN length, arc included: pointOnPath needs it
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
    // THE LENGTH THAT IS A LENGTH, and the one that is a declaration.  `length` is a
    // number the architecture declares and only `length_scaling` cost models read; the
    // physical one is measured off the two endpoint positions through the technology, and
    // when the two disagree the tooltip is where you find out.
    stip.textContent = sg.id+' · '+sg.a+' → '+sg.b+' · '+role+
                       (sg.loop?' · loop '+sg.loop:' · no loop')+
                       ' · '+fmtUm(distUm(b.x-a.x, b.y-a.y))+
                       ' · declared length '+(+(sg.length===undefined?1:sg.length).toFixed(3))+
                       ' · cap '+(sg.cap===undefined?1:sg.cap);
    ln.append(stip);
    SEGEL[sg.id]=ln; gSeg.append(ln);
    pads += 2*clamp(1, Math.round(len/(L.pad_pitch||1)), 12);
  }
  // --- DC control electrodes: THE TECHNOLOGY'S OWN TILING, per SITE and per RAIL -----
  //
  // A reviewer of the hardware picture asked for two things this section did not do.
  // (1) EVERY TRAPPING SITE MUST SHOW ITS n PAIRS.  `n_dc_pairs` is a technology number
  // (3 by default, which is also the collaborator's minimum: one pair each side of the
  // well plus the pair under it, the smallest set that can both confine and shuttle).
  // The old code tiled SEGMENTS only, so a site had whatever pads its rail's rounding
  // happened to leave near it -- sometimes none at all.  (2) THE PITCH MUST BE THE REAL
  // ONE.  It was `0.34*g`, a fraction of the drawn nearest-neighbour distance, which is a
  // legibility constant and not a length; it is now `dc_pitch` NANOMETRES converted
  // through the layout, so two adjacent electrodes on the screen are `l_dc + g_dc` apart
  // on the die and counting them is a measurement.
  //
  // `nmPerPx(ux,uy)` is what makes that conversion honest under a stretched fit: one
  // pixel along a direction is a different number of nanometres on each axis, so the
  // conversion is taken along the direction the tiling actually runs.  Under true scale
  // it is the same number in every direction, which is the point of true scale.
  //
  // A site's pads and its rails' pads never overlap: a site reserves `n*pitch` centred on
  // itself, and the rail tiling runs between what its two ends reserved -- so on a device
  // dense enough that the two stacks meet, the rail simply gets no pads and dashes.
  const nmPerPx = (ux, uy) => {
    const a=ux/(L.sx||1)*NM_X, b=uy/(L.sy||1)*NM_Y;
    return Math.sqrt(a*a+b*b);
  };
  const NPAIR = clamp(1, Math.round(TECH.n_dc_pairs||3), 9);
  const PITCH_NM = TECH.dc_pitch || ((TECH.l_dc||50000) + (TECH.g_dc||8000));
  // what fraction of the pitch is metal: `l_dc / (l_dc + g_dc)`, from the technology
  const PAD_FRAC = Math.min(0.96, Math.max(0.3, (TECH.l_dc||50000)/(PITCH_NM||1)));
  // ALONG THE RAIL the electrode is true to scale: the pitch is `dc_pitch` and the metal
  // is `l_dc` of it, so a reader can measure either.  ACROSS the rail it is not: `pad_t`
  // and `pad_off` stay fractions of `g`, because that is the axis the schematic spends on
  // legibility -- the site bar, the ion and the rail all live in the same few pixels, and
  // a `w_dc` drawn to scale there would either vanish or collide depending on the device.
  // The view that IS true to scale in both directions is the derived metal underlay,
  // which `qccd phys --html` puts under this same picture from the same technology.
  const sitePitch = {}, span = {};
  for (const n of A.nodes){
    const isJ = (n.kind==='junction' || (n.cap||0)===0);
    const ax = AXIS[n.id] || {ux:1, uy:0};
    const nm = nmPerPx(ax.ux, ax.uy);
    const pit = nm>1e-12 ? PITCH_NM/nm : 0;
    sitePitch[n.id] = pit;
    // a junction holds no ions and gets no control electrodes; it still keeps the rail
    // tiling clear of its own square
    const sp = isJ ? L.r_junc : NPAIR*pit/2;
    // NOT CLIPPED to fit between the neighbours.  A site's stack is `n_dc_pairs` at the
    // technology's pitch or it is not a measurement, and squeezing it to fit would draw a
    // pitch no process has and let a reader measure it.  When the stack does not fit --
    // grid9x9 in eth_junction_2201.12579 is exactly saturated, three 75 um electrodes
    // against a 225 um lattice step -- the rail between two sites gets no room and falls
    // back to the dash pattern below, which is the picture saying so.  What it means
    // about the design is `qccd/phys/drc.py`'s answer, in words, not this module's.
    span[n.id] = sp;
    SITE_SPAN[n.id] = isJ ? 0 : sp;    // only a SITE lights its own pads
  }
  // how many pairs the rail between two ends carries, decided before anything is drawn
  // so that both tilings share one budget
  const railPlan = {};
  let nSitePairs = 0, nRailPairs = 0;
  for (const n of A.nodes) if(!(n.kind==='junction' || (n.cap||0)===0)) nSitePairs += NPAIR;
  for (const sg of A.segments){
    const I=SEGINFO[sg.id]; if(!I || I.len<1e-6) continue;
    const pit = PITCH_NM/Math.max(1e-12, nmPerPx(I.dx/I.len, I.dy/I.len));
    const ha=span[sg.a]||0, hb=span[sg.b]||0, run=I.len-ha-hb;
    // FLOOR, NOT ROUND, AND THE PITCH IS NOT RE-DERIVED.  The old tiling took
    // `k = round(len/pitch)` and then re-spaced at `len/k` so it closed exactly on both
    // nodes -- which is the right call when the pitch is a legibility constant and the
    // wrong one now that it is a length: it put the pads up to 9% off the technology's
    // spacing, and a reader measuring two adjacent electrodes would have measured that
    // error.  The pads sit at exactly `dc_pitch`, centred in what is left between the two
    // sites' own stacks, and the remainder shows as a slightly wider gap at each end --
    // which is where the remainder physically is.
    const k = (pit>1e-6 && run>=pit) ? Math.min(12, Math.floor(run/pit)) : 0;
    railPlan[sg.id] = {k, run, pit, off:(ha + (run - k*pit)/2)};
    nRailPairs += k;
  }
  // A PAIR IS TWO ELECTRODES, ONE EACH SIDE OF THE RAIL, AND IT IS NEVER DRAWN AS ONE.
  //
  // This line used to read `2*(nSitePairs+nRailPairs) > 4000 ? [1] : [1,-1]`: over a
  // budget it dropped the south side and drew half of every pair.  That is not a coarser
  // picture of the same hardware, it is a different and impossible one -- a surface trap
  // confines and shuttles with a control electrode on EACH side of the RF rail
  // (`qccd/phys/build.py::_segment_polys` emits the `north` and `south` bands at
  // +-(dc_setback .. dc_setback+dc_width) for exactly that reason), and a reader counting
  // pads on the screen would have counted a device that cannot trap an ion.  It fired on
  // `grid11x11` at junction pitch 2 -- 660 site pairs and 1,760 rail pairs, so 4,840 > 4,000
  // -- which is the buildable 11x11 lattice, the one worth demonstrating.
  //
  // The budget itself stays, spent where it can be spent honestly: on HOW MANY POSITIONS
  // are tiled, never on how many sides a position has.  Past 8,000 rectangles the rail
  // tiling becomes a dash pattern -- which still states the true pitch, because the dash
  // IS `l_dc` and the gap IS `g_dc` -- and site pads are drawn first, because they are
  // what a reader counts and the rail between two sites is a road rather than a readout.
  //
  // (The derived metal has a THIRD column, `centre`, between the two RF rails.  It is not
  // drawn here and that is deliberate: this schematic draws a rail as one stroked line, so
  // a centre pad would be hidden underneath it, and `qccd/phys/build.py::dc_pairs_by_site`
  // does not count it as a pair either.  `qccd phys --html` is the view that has it.)
  const sides = [1,-1];
  const nSite = 2*nSitePairs, nRail = 2*nRailPairs;
  const tileSite = nSite <= 8000, tileRail = (nSite+nRail) <= 8000;
  // ONE PAIR is the unit, because the pair is what is energized.  The old flat list let
  // "the three nearest pads" mean one side of one position and both sides of another.
  const padPair = (cx0, cy0, tx, ty, w, t, list) => {
    const nx=-ty, ny=tx, ang=Math.atan2(ty,tx)*180/Math.PI;
    const pair = {t, x:cx0, y:cy0, tx, ty, w, ang, pads:[]};
    for(const sign of sides){
      const cx=cx0+nx*L.pad_off*sign, cy=cy0+ny*L.pad_off*sign;
      // SQUARE CORNERS.  `rx` was half the height, which rounds a pad's ends into
      // semicircles and draws something no surface trap has: a DC electrode is a
      // rectangular metal pad.  The collaborator's note, and he is right.
      const r=el('rect',{x:cx-w/2, y:cy-L.pad_t/2, width:w, height:L.pad_t,
        transform:`rotate(${ang} ${cx} ${cy})`,
        fill:C.dc_idle, opacity:0.42});
      gElec.append(r); pair.pads.push({el:r, cx, cy, sign});
    }
    list.push(pair);
    return pair;
  };
  if(tileSite) for (const n of A.nodes){
    if(n.kind==='junction' || (n.cap||0)===0) continue;
    const ax=AXIS[n.id]||{ux:1,uy:0}, x=px(n), y=py(n);
    // the technology's pitch along the rail this site is drawn on, and nothing else
    const pit = sitePitch[n.id]||0;
    if(!(pit>1e-6)) continue;
    const list=[];
    for(let i=0;i<NPAIR;i++){
      const o=(i-(NPAIR-1)/2)*pit;
      padPair(x+ax.ux*o, y+ax.uy*o, ax.ux, ax.uy, PAD_FRAC*pit, i, list);
    }
    list.pitch=pit;
    PAD_BY_SITE[n.id]=list;
  }
  for (const sg of A.segments){
    const I=SEGINFO[sg.id], plan=railPlan[sg.id]; if(!I || !plan) continue;
    const k=plan.k;
    if(!tileRail || !k){
      // too many to draw, or no room left between the two ends: the tiling becomes the
      // rail's own dash pattern, at the technology's pitch
      const ln=SEGEL[sg.id], pit=plan.pit;
      if(ln && pit>0.4) ln.setAttribute('stroke-dasharray',
        (PAD_FRAC*pit)+' '+((1-PAD_FRAC)*pit));
      continue;
    }
    const list=[], pitch=plan.pit, w=PAD_FRAC*pitch;
    for(let i=0;i<k;i++){
      const tt=(plan.off+(i+0.5)*pitch)/I.len, q=bezPoint(I, tt);
      padPair(q.x, q.y, q.tx, q.ty, w, tt, list);
    }
    list.pitch=pitch;
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
    const _um = toUm(n.x, n.y);
    tip.textContent = n.id+' · '+(n.kind==='junction'?'junction':(n.zone||'no zone'))+
                      ' · cap '+n.cap+' · deg '+n.deg+(n.corner?' · bend':'')+
                      ' · at ('+(+n.x.toFixed(3))+', '+(+n.y.toFixed(3))+') = ('+
                      um1(_um.x)+' um, '+um1(_um.y)+' um)';
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
               reg:{SEGEL, SEGINFO, PAD_BY_SEG, PAD_BY_SITE, SITE_SPAN, SEG_BY_PAIR,
                    NODEEL, CAPTXT}};
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
// THE RUNNING TOTALS.  `cum` sums the cost Python stamped on each SHIPPED frame; an
// authored frame carries none, so the head counted 0 of a shipped total under a
// programme the user had just typed.  For an authored programme the numerators are the
// prefix sums of the engine's own per-frame price -- the array `priceFrames` already
// fills -- summed once per PRICE.  A prefix sum is not a second cost model.  Null while
// the price is refused, and the counter says so rather than printing 0.
let CUMA=null, CUMA_OF=null;
function cumAt(i){
  const ED = globalThis.EDITOR;
  if(!(ED && ED.authored && ED.authored())) return cum[i]||{cost:0,steps:0};
  const pr = ED.price();
  if(!pr || pr.blocked || !pr.perFrame) return null;
  if(CUMA_OF!==pr){
    CUMA_OF=pr; CUMA=[]; let cost=0, steps=0;
    for(const pf of pr.perFrame){ cost+=pf[0]; steps+=pf[1]; CUMA.push({cost, steps}); }
  }
  return CUMA[i]||{cost:0,steps:0};
}
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
//
// THE ARITHMETIC IS NOT HERE ANY MORE.  `js/transit.js` owns it -- arc-length walking of
// a path, slot order carried forward, and the detour an ion takes round one it has to get
// past -- because the gadget Design canvas needs exactly the same law and had grown its
// own, weaker one.  What stays here is the GEOMETRY: `edgeLen`/`edgePoint` know about
// bowed rails and `px`/`py` about the anisotropic fit, and neither belongs in an
// occupancy rule.  The six functions below are the whole adapter.
// Two sites dropped on the same model coordinate are ONE place to the eye, and this is a
// design tool: nothing stops a user stacking them.  Keyed on the MODEL position, not the
// drawn one, so the grouping does not change with the zoom.  No shipped architecture has
// a repeated position, so on all nine this is the identity.
let SITE_OF = {};
function rebuildSites(){
  SITE_OF = {}; const first = {};
  for(const n of A.nodes){ const k = n.x+','+n.y;
    if(!(k in first)) first[k] = n.id;
    SITE_OF[n.id] = first[k]; }
}
rebuildSites();
const TRANSIT = new QCCDTransit.Transit({
  pos: id => { const n=nodeById[id]; return n ? [px(n), py(n)] : null; },
  axis: id => { const a=AXIS[id]; return a ? [a.ux, a.uy] : [1,0]; },
  site: id => SITE_OF[id] || id,
  slotOffsets: (id, k) => slotOffsets(nodeById[id], k),
  // half the drawn site bar: how far from the node the trap itself still extends
  span: id => { const n=nodeById[id];
    return (n && (n.cap||0) > 0) ? siteLen(n.cap)/2 : 0; },
  // half the site bar's thickness: all the room there is ACROSS a trap
  across: id => { const n=nodeById[id];
    return (n && (n.cap||0) > 0) ? L.site_t/2 : 0; },
  // and half a rail's, which is all there is out between the traps
  rail: L.sw_rail/2,
  edgeLen: (a, b) => edgeLen(a, b),
  edgePoint: (a, b, u) => edgePoint(a, b, u),
  get bow(){ return L.swap_bow; },     // `L` is mutated in place by the true-scale toggle
});
function pointOnPath(path, t){ return TRANSIT.pointOnPath(path, t); }

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
// The walk itself is `transit.js::slotOrder`; what stays here is turning frames into the
// three plain tables it reads.
function deriveSlots(frames){
  SLOTS.length = 0;
  const steps=[];
  for(let i=0;i<frames.length;i++){
    steps.push({before: before[i]||{}, pos: states[i].pos,
                paths: pathsOf(frames[i]||{}, before[i]||{})});
  }
  const ord = TRANSIT.slotOrder(steps);
  for(let i=0;i<ord.length;i++) SLOTS[i]=ord[i];
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
    // `s` is the laser spot and `l` the beam reaching it.  Pooled with the rest: one
    // pair of elements per ion for the life of the page, hidden unless this frame is
    // doing something to that ion.  A parallel set created per frame would leak.
    p={ w: el('ellipse',{rx:L.well_rx, ry:L.well_ry, fill:C.anc, opacity:0.16}),
        s: el('circle',{'pointer-events':'none', display:'none'}),
        l: el('line',{'stroke-linecap':'round','pointer-events':'none', display:'none'}),
        k: el('line',{'stroke-linecap':'round','pointer-events':'none', display:'none'}),
        c: el('circle',{stroke:C.ion_stroke, 'stroke-width':L.sw_halo}),
        t: el('text',{'text-anchor':'middle','dominant-baseline':'central',
             fill:C.ion_stroke,'font-weight':700,'pointer-events':'none'}) };
    p.t.textContent = ion.replace(/^[da]/,'');
    gWell.append(p.w); gWell.append(p.s); gWell.append(p.k); gWell.append(p.l);
    gIon.append(p.c); gTop.append(p.t);
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
  // PHASE IS A FRACTION OF ONE FRAME, so it belongs in [0, 1] and this is where that is
  // made true.  Every reader downstream assumes it: the smoothstep below turns a phase of
  // -0.55 into t = 1.24, the swell term `4t(1-t)` then goes NEGATIVE, and every ion is
  // drawn with a negative radius -- 144 console errors per rotation on ring144_24v, and
  // the same arithmetic extrapolates positions PAST the ends of their rails.  The clock
  // can hand us an out-of-range value honestly: `tick` computes `(now - t0)/dur` from a
  // `t0` that a programme change can leave in the future, and a queued animation frame
  // can carry a timestamp from before the click that started it.  Clamping the clock
  // instead would be wrong -- it is measuring real time -- so the drawing clamps what it
  // draws, once, here.  (Reported 2026-09-17 by another session against the live page;
  // it predates the sketch and hold work, and reproduces on a page built on 09-16.)
  if(!(phase >= 0)) phase = 0;              // also catches NaN
  else if(phase > 1) phase = 1;
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
  //
  // THE LAW IS `js/transit.js`; the geometry is the adapter at `TRANSIT` above.  Every
  // number that used to be computed in the two hundred lines that stood here is computed
  // there now, and the gadget Design canvas computes it with the same code.
  const segLoad={};
  // The two slot orders come out of the precomputed table: end of the PREVIOUS frame for
  // the start state, this frame's for the end state, so the two agree at the seam AND
  // agree with each other.
  const ordEnd=SLOTS[frame]||{}, ordStart=(frame>0?SLOTS[frame-1]:null)||{};
  const PL = TRANSIT.place({before: before[frame]||{}, pos: stt.pos, paths: paths,
                            t: t, rest: !(phase<1), ordStart: ordStart, ordEnd: ordEnd});
  const live=PL.live, flying=PL.flying;
  const over={};
  for(const ion in flying){
    const q=flying[ion];
    if(q.a && q.b){ const sid=SEG_BY_PAIR[q.a+'>'+q.b];
      if(sid!==undefined) segLoad[sid]=(segLoad[sid]||0)+1; }
  }
  // R1 IS PER TRAP, NOT PER PLACE.  `PL.occEnd` groups by the coordinate a node is drawn
  // at, because that is what "two marks overlap" means; capacity is a property of the
  // metal, and two traps that happen to sit at one point are still two traps.  Counting
  // the groups would read four ions in a pair of co-located cap-2 sites as a violation
  // of a rule neither of them breaks.
  const occ={};
  for(const ion in stt.pos) (occ[stt.pos[ion]]||(occ[stt.pos[ion]]=[])).push(ion);
  for(const id in occ){ const n=nodeById[id]; if(!n) continue;
    if(occ[id].length > (n.cap||0)) over[id]=occ[id].length; }

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

  // --- energized DC pads: the SITE's own pairs while the ion is over the site, the
  // nearest rail pair while it is between two ------------------------------------
  //
  // This is the collaborator's second request made literal.  While an ion sits over a
  // trapping site, what holds it is that site's own `n_dc_pairs` -- all of them, because
  // a well is made by the whole stack and not by one electrode of it.  Once it is out on
  // the rail, what moves it is the pair it is passing, with its two neighbours ramping in
  // and out around it: the deck's p.4 picture of a well being handed from one electrode
  // to the next.  The lit set follows the ion continuously, so nothing flashes on a whole
  // segment at once.
  //
  // A PAIR at a time, never a rectangle at a time: the old flat list made "the three
  // nearest pads" mean one side of one position and both sides of the next.
  clearTransients();
  const litPair = (pair, peak) => {
    for(const q of pair.pads){
      const h=L.pad_t*(peak?1.45:1.2);
      q.el.setAttribute('fill',C.dc_hot);
      q.el.setAttribute('opacity', peak ? 0.95 : 0.55);     // the ramp has a peak
      q.el.setAttribute('y',q.cy-h/2); q.el.setAttribute('height',h);
      lastHot.push(q);
    }
  };
  for(const ion in flying){
    const q=flying[ion]; if(!q.a||!q.b) continue;
    // over a site, or between two?  Measured against the span that site's own electrodes
    // occupy, which is the same number the rail tiling was kept out of.
    const na=nodeById[q.a], nb=nodeById[q.b];
    const da = na ? Math.hypot(q.x-px(na), q.y-py(na)) : Infinity;
    const db = nb ? Math.hypot(q.x-px(nb), q.y-py(nb)) : Infinity;
    let site=null;
    if(da<=db && da<=(SITE_SPAN[q.a]||0) && PAD_BY_SITE[q.a]) site=q.a;
    else if(db<=(SITE_SPAN[q.b]||0) && PAD_BY_SITE[q.b]) site=q.b;
    if(site){ for(const pr of PAD_BY_SITE[site]) litPair(pr, true); continue; }
    const sid=SEG_BY_PAIR[q.a+'>'+q.b]; if(sid===undefined) continue;
    const list=PAD_BY_SEG[sid];
    if(!list || !list.length){
      // no electrodes BETWEEN these two sites: the two stacks meet, and the well is
      // handed straight from one to the other.  Light the one the ion is nearer, rather
      // than lighting nothing and leaving the ion apparently pushed by no electrode.
      const near2 = (da<=db ? q.a : q.b);
      if(PAD_BY_SITE[near2]) for(const pr of PAD_BY_SITE[near2]) litPair(pr, true);
      continue;
    }
    const u = (segById[sid].a===q.a) ? q.u : 1-q.u;
    let near=0, best=Infinity;
    for(let i=0;i<list.length;i++){
      const d=Math.abs(list[i].t-u);
      if(d<best){best=d; near=i;}
    }
    for(let i=Math.max(0,near-1); i<=Math.min(list.length-1, near+1); i++)
      litPair(list[i], i===near);
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
  for(const ion in IONP){ if(!(ion in live)){const p=IONP[ion]; hide(p.w);hide(p.c);hide(p.t);hide(p.s);hide(p.l);hide(p.k);} }
  for(const ion in live){
    const pt=live[ion], p=ionMarks(ion);
    // WHO IS BEING ACTED ON.  `acts` carries every operand tuple; `pairs` is kept as the
    // fallback for a page built before the frame carried them, where a one-qubit gate is
    // still invisible.
    const ops = (f.acts && f.acts.length) ? f.acts : (f.pairs||[]);
    const mine = ops.filter(q=>q.indexOf(ion)>=0);
    const act = mine.length>0;
    // A LASER IS DOING SOMETHING TO THIS ION, and the picture should say which.  One
    // beam on one ion is a single-qubit pulse; a beam on each of two co-located ions is
    // the entangler across both.  Readout and preparation are laser work too and used to
    // be drawn as nothing at all: blue for measurement, green for init and reset.
    let beam = null;
    if(f.type==='gate' && act) beam = mine.some(q=>q.length>1) ? C.beam_two : C.beam_one;
    else if(f.type==='measure' && (f.ions||[]).indexOf(ion)>=0) beam = C.beam_meas;
    else if(f.type==='reset' && (f.ions||[]).indexOf(ion)>=0) beam = C.beam_init;
    else if(f.type==='init' && f.place && Object.prototype.hasOwnProperty.call(f.place, ion)) beam = C.beam_init;
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
      // NEVER WIDER THAN THE GAP IT IS THREADING.  The two ends of the interpolation are
      // the stacks at either end of the walk, so an ion leaving a crowded trap for an
      // empty one grows to full size while it is still among its old neighbours -- and
      // covers them.  `transit.js` measures the real distance to the ions this one has to
      // get past and reports half of it as `room`.
      if(pt.room) r = Math.min(r, pt.room);
    } else {
      if(pt.pitch) r = Math.min(r, 0.44*pt.pitch);
      // A STANDING ION GIVES WAY TOO.  It is as much of a pass as the one moving, and
      // leaving it at full size while the mover shrank is how one mark came to cover
      // another that had made all the room for it.
      if(pt.room) r = Math.min(r, pt.room);
    }
    if(pt.fly && wells){
      p.w.setAttribute('cx',pt.x); p.w.setAttribute('cy',pt.y); show(p.w);
    } else hide(p.w);
    p.c.setAttribute('cx',pt.x); p.c.setAttribute('cy',pt.y);
    p.c.setAttribute('r', r);
    p.c.setAttribute('fill', ionColour(ion,f,stt));
    show(p.c);
    // THE LASER, on the ion it is actually addressing.  The spot sits under the ion so
    // the ion stays the mark the eye finds, and the beam comes in from up and left at a
    // fixed angle -- it is a diagram of "a laser is pointed here", not a ray trace.  An
    // ion nobody is addressing gets neither, so counting the spots counts the operands:
    // one for a single-qubit pulse, two for the entangler.
    if(beam){
      const rs = r*2.05, d = rs*2.6;
      p.s.setAttribute('cx',pt.x); p.s.setAttribute('cy',pt.y); p.s.setAttribute('r',rs);
      p.s.setAttribute('fill',beam); p.s.setAttribute('opacity',0.26); show(p.s);
      p.l.setAttribute('x1',pt.x-d); p.l.setAttribute('y1',pt.y-d);
      p.l.setAttribute('x2',pt.x-rs*0.62); p.l.setAttribute('y2',pt.y-rs*0.62);
      p.l.setAttribute('stroke',beam); p.l.setAttribute('stroke-width',Math.max(1.1, r*0.34));
      p.l.setAttribute('opacity',0.72); show(p.l);
    } else { hide(p.s); hide(p.l); }
    // ONE laser across TWO ions: tie the pair together, drawn once from the first of the
    // two.  Without this the only thing separating an entangler from a broadcast pulse on
    // two ions is the colour, and a broadcast R can light two dozen at once.
    const pair2 = (f.type==='gate') ? mine.find(q=>q.length>1) : null;
    if(pair2 && pair2[0]===ion && live[pair2[1]]){
      const o=live[pair2[1]];
      p.k.setAttribute('x1',pt.x); p.k.setAttribute('y1',pt.y);
      p.k.setAttribute('x2',o.x);  p.k.setAttribute('y2',o.y);
      p.k.setAttribute('stroke',C.beam_two);
      p.k.setAttribute('stroke-width',Math.max(1.3, r*0.55));
      p.k.setAttribute('opacity',0.5); show(p.k);
    } else hide(p.k);
    if(showLabels && r >= 6){
      const fs=Math.min(0.66*r, 1.5*r/Math.max(2, p.t.textContent.length));
      p.t.setAttribute('x',pt.x); p.t.setAttribute('y',pt.y);
      p.t.setAttribute('font-size',fs); show(p.t);
    } else hide(p.t);
  }

  const c=cumAt(frame);
  document.getElementById('cSteps').textContent=c?c.steps.toLocaleString():'—';
  document.getElementById('cCost').textContent=c?c.cost.toLocaleString():'—';

  // THE STANDING REFUSAL of an authored programme ('' while nothing is refused): the
  // editor's one sentence for it, so the strip, the toast and the Write pane agree
  const ED=globalThis.EDITOR, authored=!!(ED && ED.authored && ED.authored()),
        refused=(authored && ED.refusedLine) ? ED.refusedLine() : '';
  // the Test drive button is the strip's own flex item, shown while there are no frames
  document.getElementById('stDrive').style.display = P.frames.length ? 'none' : '';
  document.getElementById('status').innerHTML =
    // `Step 1 / 0 - undefined` is what this printed with no programme: `f` was `{}` and
    // `frame+1` counted a step that does not exist.  A design tool opens on exactly that
    // state, so it is the first sentence a new user reads.
    // and the same condition folds the transport controls away -- set BOTH ways, or
    // writing a programme would leave the page still hiding the controls for it
    // -- and a programme that WAS written, every statement of it refused, is not
    // "no programme yet": that sentence names the Write pane it is refused in.
    // Each sentence ends on "press": the Test drive button follows it in the strip.
    (P.frames.length === 0
      ? (document.body.setAttribute('data-noprog','1'),
         refused ? `<b>programme refused</b> &mdash; see the <b>Write</b> pane, or press`
         : authored ? `<b>no frames</b> &mdash; this programme moves nothing yet; write more in the <b>Write</b> pane, or press`
         : `<b>no programme yet</b> &mdash; write one in the <b>Write</b> pane, or press`)
      : (document.body.removeAttribute('data-noprog'),
         `<b>Step ${frame+1}</b> / ${P.frames.length} &mdash; <code>${f.type}</code>`)) +
    (f.cls?` <code>${f.cls}</code>`:'') +
    (f.hops?` &middot; ${f.hops} hop${f.hops>1?'s':''}`:'') +
    (f.check?` &middot; <b>${f.check}</b>`:'') +
    (f.batch!==undefined?` &middot; batch ${f.batch+1}`:'') +
    (f.cost!==undefined?` <span style="color:var(--muted)">[cost ${f.cost} &middot; steps ${f.steps}]</span>`:'');
  const nMoved=f.shift?Object.keys(stt.pos).length:(f.moves?f.moves.length:0);
  const why =
    f.type==='simd'
      ? `one class (${f.cls}), ${nMoved} ions moving together; the machine allows `
        + `${P.max_simd_classes} class per step, so nothing of a different class can join`
      : f.type==='gate'
        ? `${(f.pairs||[]).length} contact${(f.pairs||[]).length===1?'':'s'} in this batch`
        : f.type==='cool' ? 'global cooling: one operation, every ion'
        : f.type==='measure' ? 'readout' : (f.type==='reset'?'reset':'');
  // a refused statement stands on the stage, not only in the Write pane: an edit that
  // dropped statement 2 of an authored programme used to leave the strip reading
  // "Step 1 / 1" with nothing to say that the programme had been cut
  document.getElementById('why').textContent =
    refused ? refused + ' · see the Write pane' + (why ? ' · ' + why : '') : why;
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
  // the wide regime's row reads the dock's fold too: its second track is the dock's
  if(el.id === 'dock') document.getElementById('row').setAttribute('data-dock', now ? '1' : '0');
  const btn = document.getElementById(el.id === 'rail' ? 'railGrip' : 'dockGrip');
  if(btn){
    btn.setAttribute('aria-expanded', now ? 'false' : 'true');
    btn.innerHTML = el.id === 'rail' ? (now ? 'Elements &#9654;' : '&#9664; Elements')
                                     : (now ? '&#9664; Panels' : 'Panels &#9654;');
    btn.title = (now ? 'show ' : 'hide ') +
                (el.id === 'rail' ? 'the element rail ([)' : 'the side panels (])');
  }
  if(el.id === 'dock' && typeof syncMenu === 'function') syncMenu();
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
// THE REGIME FOLLOWS THE WINDOW, and the rail folds on the way INTO the narrow regime --
// only then: there it is a drawer over the stage, and a drawer that opened itself on
// every resize would cover the picture.  `applyLayout()` itself stays pure (the harness
// calls it at three widths in a row and must not be left with a folded rail).
let LAST_LAYOUT = null;
function relayout(){
  // the rect under a live drag is about to move: the gesture ends as Escape ends it.
  // `globalThis.EDITOR`, as `ruleBadges` explains: under the harness `window` is a plain
  // object, and a guard on it would make this the one branch no script could reach.
  const ED = globalThis.EDITOR || window.EDITOR;
  if(ED && ED.dragging && ED.dragging()) ED.cancelGesture();
  const m = applyLayout();
  if(m === 'narrow' && LAST_LAYOUT !== 'narrow') foldPanel(railEl, true);
  LAST_LAYOUT = m;
  placeProgram();
  sizeStage();
  return m;
}

// ---------- fit, zoom, pan: the viewBox is the only thing that moves ----------
let VB={x:0,y:0,w:L.W,h:L.H};

// ---------- THE SCALE BAR: the one mark on the page whose LENGTH means a distance ----
// Its length is a ROUND physical number (100 um, 1 mm) chosen to sit near a fifth of what
// is on screen, so the number is readable and the bar is comparable to the device.  It is
// recomputed from `applyVB` (zoom, pan, fit) and from `sizeStage` (resize), so it keeps its
// meaning whatever the view.
//
// IT LIVES IN THE TOOLBAR UNDER THE PICTURE, NEVER ON IT.  It used to be drawn inside the
// SVG at the view's bottom-left corner, on an opaque plate so its caption could be read
// over rails -- and wherever a mark on the canvas sits, a fit, a pan or a zoom can bring
// the device under it: the plate hid part of the chip (reported 2026-09-16).  Nothing can
// be under the toolbar, and the bar is still an exact screen length: the user units it
// spans divided by the user units per screen pixel, `vbPerPx()`.
//
// It measures along X.  Under true scale that is the whole story; with the fit stretched
// (up to K_ANISO) a vertical distance is a different number of nanometres per pixel, and
// the label says so rather than letting the bar be read as isotropic.
const SBAR = { box: document.getElementById('scaleBar'),
               line: document.getElementById('scaleLine'),
               txt: document.getElementById('scaleTxt') };
const BAR_UM = [0.1,0.2,0.5,1,2,5,10,20,50,100,200,500,1000,2000,5000,
                10000,20000,50000,100000,200000,500000];
// ONE SVG USER UNIT, IN SCREEN PIXELS -- inverted, so every number below can be written
// in the pixels a reader actually sees.  The bar and its caption are furniture: they must
// stay the same SIZE ON SCREEN whatever the zoom, and only their MEANING may change.
// Sizing them as a fraction of the viewBox instead put a 100 px caption across a
// zoomed-in device (the viewBox shrinks, the fraction does not).  `xMidYMid meet` fits the
// viewBox inside the element, so the factor is the smaller of the two ratios.
function vbPerPx(){
  const r = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
  const w = (r && r.width > 0) ? r.width : L.W, h = (r && r.height > 0) ? r.height : L.H;
  const k = Math.min(w/Math.max(1e-9, VB.w), h/Math.max(1e-9, VB.h));
  return (k > 0 && isFinite(k)) ? 1/k : 1;
}
function scaleBarChoice(){
  // user units per micrometre, along x, at the CURRENT layout
  const perUm = (L.sx||0)*1000/NM_X;
  if(!(perUm>0) || !isFinite(perUm)) return null;
  // about 150 screen px of bar, and never more than a quarter of what is on screen
  const want = Math.min(0.25*VB.w, 150*vbPerPx());
  let um = BAR_UM[0];
  for(const v of BAR_UM) if(v*perUm <= want) um = v;
  return { um, w: um*perUm, perUm };
}
function placeScaleBar(){
  const ch = scaleBarChoice();
  if(!SBAR.box || !SBAR.line || !SBAR.txt) return ch;
  if(!ch){ SBAR.box.style.display = 'none'; SBAR.txt.textContent = ''; return null; }
  SBAR.box.style.display = '';
  // the screen length the drawing gives `ch.w` user units at this zoom
  SBAR.line.style.width = Math.max(2, ch.w/vbPerPx()).toFixed(1) + 'px';
  // Say which axis the bar is for when the two scales disagree, because then the
  // picture has no single scale to state.  A RELATIVE tolerance, not an exact compare:
  // `sx` and `sy` are quantized to four decimals, so a true-scale pair at a non-1:1
  // technology ratio (225:355) agrees to about 1e-15 and never bit-exactly.
  const kx=(L.sx||0)/NM_X, ky=(L.sy||0)/NM_Y;
  const flat = Math.abs(kx-ky) <= 1e-6*Math.max(1e-12, Math.abs(kx));
  SBAR.txt.textContent = (ch.um>=1000 ? (ch.um/1000)+' mm' : ch.um+' um') +
                         ' · ' + TECH.preset + (flat ? '' : ' · x only');
  return ch;
}
const applyVB=()=>{
  svg.setAttribute('viewBox',`${VB.x} ${VB.y} ${VB.w} ${VB.h}`);
  placeScaleBar();
};
placeScaleBar();
// FIT never draws more than ~1.5 css px per user unit: a two-site canvas used to fill the
// whole box, 141x47 px per pill.  Past that the viewBox widens around the device's centre
// instead, and the device sits at a size a hand can still aim at.
const FIT_MAX_K = 1.5;
function fit(){
  // FIT IS WHERE THE DRAWING RE-FITS, and the only place: every edit holds the scale it
  // was drawn at, so the layout on screen can be older than the device it draws.  Ask the
  // editor for a fresh one first; a page with no editable state has nothing to ask.
  try {
    const ED = globalThis.EDITOR || window.EDITOR;
    if(ED && ED.refit) ED.refit();
  } catch(e){ /* not an editable page */ }
  VB={x:0,y:0,w:L.W,h:L.H};
  const r = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
  if(r && r.width > 0 && r.height > 0 && Math.min(r.width/L.W, r.height/L.H) > FIT_MAX_K){
    const w = r.width/FIT_MAX_K, h = r.height/FIT_MAX_K;
    VB = {x:(L.W-w)/2, y:(L.H-h)/2, w:w, h:h};
  }
  applyVB();
}
function sizeStage(){
  // THE CANVAS IS FURNITURE, NOT A FRAME AROUND THE CONTENT. It used to be capped at
  // `L.W` css px, so its SHAPE followed the device: a 1600x132 chain drew a 61 px strip
  // and a 900x900 grid drew a square, and the drawing area changed every time you opened
  // a different machine. The box is whatever the app frame leaves it (CSS; the JS
  // `maxHeight` that fought the frame is gone) and the device is fitted inside it by
  // `preserveAspectRatio="xMidYMid meet"`; zoom and pan reach the rest.
  sizeLists();
  const was=showLabels;
  const scale=(svg.clientWidth||L.W)/L.W;
  showLabels = (0.66*L.r_ion*scale) >= 8;
  // the bar and its caption are sized in SCREEN pixels, so a resize changes them even
  // though the viewBox has not moved
  placeScaleBar();
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
  // the outer bound admits where `fit()` put the box: on a small device that is wider
  // than 2.5 x the device, and the first wheel notch must not snap the view in
  const k=clamp(0.12*L.W, VB.w*Math.pow(1.0018, deltaY), Math.max(2.5*L.W, VB.w));
  const nh=k*(L.H/L.W);
  // keep `m` exactly under the cursor: its offset from the origin scales with the box
  VB.x = m.x - (m.x - VB.x) * (k / VB.w);
  VB.y = m.y - (m.y - VB.y) * (nh / VB.h);
  VB.w = k; VB.h = nh; applyVB();
  return { x: VB.x, y: VB.y, w: VB.w, h: VB.h };
}
svg.addEventListener('wheel', e=>{
  // A PLAIN WHEEL ZOOMS AT THE POINTER.  It used to need ctrl or shift and otherwise
  // scrolled the page out from under the stage, which nobody guessed; a wheel that only
  // reports deltaX (a tilted or horizontal wheel) zooms too.
  e.preventDefault();
  zoomAt(e.clientX, e.clientY, e.deltaY || e.deltaX);
}, {passive:false});
// published so a harness can zoom, and so the editor can fit to a selection
window.VIEW = { zoomAt: zoomAt, fit: fit, vb: () => ({x:VB.x,y:VB.y,w:VB.w,h:VB.h}),
  // the scale bar as NUMBERS, so a harness can assert what the page claims a distance is
  // rather than reading a rectangle back off the DOM
  bar: () => { const c=scaleBarChoice();
    return c ? {um:c.um, w:c.w, label:SBAR.txt.textContent} : null; },
  trueScale: () => TRUE_SCALE, tech: () => TECH };
let drag=null;
svg.addEventListener('pointerdown', e=>{
  // ONE ARBITER decides who owns a press, and it lives in the editor.  This handler had
  // no mode guard at all, so dragging a node panned the stage UNDERNEATH the node at the
  // same time -- both handlers ran, and the picture slid out from under the gesture.
  // `EDITOR.claimEvent` is the only rule; nothing here restates it.
  if (window.EDITOR && EDITOR.claimEvent && EDITOR.claimEvent(e) !== 'pan') return;
  drag={x:e.clientX,y:e.clientY,vx:VB.x,vy:VB.y};
  svg.setPointerCapture(e.pointerId);
  // the cursor is editor state written to `style`, never a class (unreadable in the shim)
  if (window.EDITOR && EDITOR.panning) EDITOR.panning(true);});
svg.addEventListener('pointermove', e=>{ if(!drag) return;
  // the same 4 px the editor's click threshold uses: a press that never travels is a
  // click (clear the selection, place the stamp), and it must not nudge the view either
  if(!drag.live){ if(Math.hypot(e.clientX-drag.x, e.clientY-drag.y) < 4) return; drag.live=true; }
  const r=svg.getBoundingClientRect();
  VB.x=drag.vx-(e.clientX-drag.x)*VB.w/r.width;
  VB.y=drag.vy-(e.clientY-drag.y)*VB.h/r.height; applyVB();});
const endDrag=()=>{ if(!drag) return; drag=null;
  if (window.EDITOR && EDITOR.panning) EDITOR.panning(false); };
svg.addEventListener('pointerup', endDrag); svg.addEventListener('pointercancel', endDrag);
// right-drag pans, so the browser's context menu must not open on top of the gesture
svg.addEventListener('contextmenu', e=>e.preventDefault());
// nor on top of the menu the stage just opened
document.getElementById('ctxmenu').addEventListener('contextmenu', e=>e.preventDefault());
// the status strip's Test drive button: one adapter, the verb and its toast are the editor's
document.getElementById('stDrive').addEventListener('click', ()=>{
  if (window.EDITOR && EDITOR.pressTestDrive) EDITOR.pressTestDrive(); });
let rt=null;
window.addEventListener('resize', ()=>{clearTimeout(rt); rt=setTimeout(relayout,120);});

// ---------- chrome ----------
const M=D.metrics, hw=A.hardware, rl=D.rules, SUM=A.summary||{};
const fmt=(x,d=0)=>(x==null)?'-':Number(x).toLocaleString(undefined,{maximumFractionDigits:d});
document.getElementById('kicker').textContent = D.kicker || 'ROUTING SCHEME';
document.getElementById('title').textContent = D.headline || (A.name+' · '+P.name);
document.getElementById('lede').textContent = D.lede || A.description || '';
// WHAT PYTHON SHIPPED for the head: the two denominators and the metric chips describe
// the shipped programme on the shipped device.  Kept, so `paintHead` can put them back.
// ONE CHIP, either way it is rendered: the label is its hint key, and the four a
// newcomer needs -- cost, steps, runtime, DACs -- are tier 1; the rest wait for a wide
// window (the Report prints every one of them at every width).
const TIER1 = ['cost','steps','runtime','DACs'];
const chip = (l,v) => `<div class="m" data-hint="m:${esc(l)}" data-tier="${TIER1.indexOf(l)>=0?1:2}"><span>${esc(l)}</span><b>${esc(v)}</b></div>`;
const HEAD_SHIPPED = {
  stepsOf: '/'+fmt(M.total_steps), costOf: '/'+fmt(M.total_cost),
  metrics: [['cost',fmt(M.total_cost)],['steps',fmt(M.total_steps)],
   M.runtime_us?['runtime',fmt(M.runtime_us/1000,2)+' ms']:null,
   M.total_quanta?['quanta',fmt(M.total_quanta)]:null,
   M.peak_quanta?['peak n̄',fmt(M.peak_quanta,1)]:null,
   ['contacts',fmt(M.n_gate_pairs)],
   M.n_cool?['cooling',fmt(M.cooling_us/1000,1)+' ms']:null,
   ['DACs',fmt(hw.dacs)],['junctions',fmt(hw.n_junctions)]]
  .filter(Boolean).map(([l,v])=>chip(l,v)).join('')
};
document.getElementById('cStepsOf').textContent = HEAD_SHIPPED.stepsOf;
document.getElementById('cCostOf').textContent = HEAD_SHIPPED.costOf;
document.getElementById('metrics').innerHTML = HEAD_SHIPPED.metrics;
// THE HEAD FOR AN AUTHORED PROGRAMME.  The denominators are the engine's totals and the
// chips are the rows the Report's Backed table prints (`EDITOR.metricRows`, one list for
// both), so the head never carries the shipped programme's 140 over a two-statement
// shuttle priced at 2.  Written only while a programme is authored, and the shipped
// strings restored the moment it is not -- a page nobody has changed is never rewritten.
let HEAD_AUTHORED = false;
function paintHead(){
  const ED = globalThis.EDITOR;
  const authored = !!(ED && ED.authored && ED.authored());
  if(!authored){
    if(HEAD_AUTHORED){
      document.getElementById('cStepsOf').textContent = HEAD_SHIPPED.stepsOf;
      document.getElementById('cCostOf').textContent = HEAD_SHIPPED.costOf;
      document.getElementById('metrics').innerHTML = HEAD_SHIPPED.metrics;
      HEAD_AUTHORED = false;
    }
    return;
  }
  HEAD_AUTHORED = true;
  const pr = ED.price();
  const ok = pr && !pr.blocked;
  document.getElementById('cStepsOf').textContent = ok ? '/'+fmt(pr.totals.steps) : '';
  document.getElementById('cCostOf').textContent = ok ? '/'+fmt(pr.totals.cost) : '';
  document.getElementById('metrics').innerHTML = (ED.metricRows ? ED.metricRows() : [])
    .map(([l,v])=>chip(l,v)).join('');
}

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
    : `<span data-hint="leg:segment"><i class="bar" style="background:var(--rail)"></i>shuttling segment</span>`)+
  (nLoops?`<span data-hint="leg:loop"><i class="bar" style="background:var(--anc);opacity:.35;height:9px"></i>transport loop</span>`:'')+
  [...zoneSet].slice(0,5).map(z=>
    `<span data-hint="leg:zone"><i class="bar" style="background:var(--zone_${['data','trap','load','register'].indexOf(z)>=0?z:'other'});height:7px;border-radius:4px"></i>${z} site</span>`).join('')+
  `<span data-hint="leg:junction"><i class="sq" style="background:var(--panel);border:2px solid var(--grid)"></i>junction (holds no ions)</span>`+
  `<span data-hint="leg:slot"><i class="dot" style="background:var(--panel);border:1.5px solid var(--muted)"></i>free capacity slot</span>`+
  `<span data-hint="leg:ion"><i class="dot" style="background:var(--data)"></i>ion</span>`+
  // the two gate colours describe the PROGRAMME, so they are named only when it carries
  // checks; a transport programme on a bare device has nothing to say about X and Z
  (P.frames.some(f=>f.check)
    ? `<span><i class="dot" style="background:var(--x)"></i>ion in an X check</span>`+
      `<span><i class="dot" style="background:var(--z)"></i>ion in a Z check</span>`
    : `<span data-hint="leg:gate"><i class="dot" style="background:var(--x)"></i>ion in a two-qubit gate</span>`)+
  `<span data-hint="leg:pad"><i class="bar" style="background:var(--dc_hot);height:4px"></i>energized DC electrode</span>`+
  `<span data-hint="leg:well"><i class="dot" style="background:var(--anc);opacity:.3"></i>moving potential well</span>`;

const capHist=SUM.capacity_histogram||{};
// The Machine pane is a FUNCTION, not a one-shot assignment, so the editor can re-render
// it after an edit rather than keeping a second copy of this markup.  `stale` is what an
// edit sets: the rule verdicts were computed by Python against the PRE-EDIT device, and a
// rule badge that still says "pass" after the design changed is worse than no badge at
// all.  Everything on the R1-R18 surface except the structural check needs a CycleView
// built from a program, so it genuinely cannot be re-run in the browser -- and saying so
// is the honest behaviour, not a limitation to paper over.
// THE VERDICT SURFACE.  `RULES_STALE` -- which struck all 27 verdicts through the moment
// anything was edited -- is GONE, not kept alongside: 21 of the 27 are now re-derived
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
    `<div>`+cov.map(c=>`<span class="badge ${cls[c.state]}" data-hint="rule:${esc(c.rule)}" title="${esc(c.statement||c.rule)}">`+
      `${c.rule}${c.count?' '+c.count:''}</span>`).join('')+`</div>`;
}
function renderSide(){
const evd = D.evidence || {replayed_cycles: P.frames.length};
document.getElementById('side').innerHTML =
  `<h3>Rules</h3>`+
  (evd.replayed_cycles === 0 && !P.frames.length
    ? `<div class="mut">no programme has been replayed, so none of the `+
      `${(evd.rules_all||[]).length||27} rules has been evaluated. Write one in the `+
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
  `<br>drag empty stage to pan, wheel to zoom, <b>Fit</b> to reset.`+
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
  // `lastFrame()`, never `P.frames.length-1`: a tick that lands after the programme was
  // emptied under it would otherwise write -1 into `frame`
  if(stride>1){                       // fast-forward: skip frames, do not slow the clock
    phase=1; frame=Math.min(lastFrame(), frame+stride); draw();
    if(frame>=lastFrame()){stop(); return;}
    raf=requestAnimationFrame(tick); return;
  }
  let dur=durOf(frame);
  phase=(now-t0)/dur;
  while(phase>=1){
    if(frame>=lastFrame()){ frame=lastFrame(); phase=1; draw(); stop(); return; }
    frame++; t0+=dur; dur=durOf(frame); phase=(now-t0)/dur;
  }
  draw();
  if(raf) raf=requestAnimationFrame(tick);
}
// a button that keeps focus after a click is re-fired by the space bar instead of
// play/pause; `blur` is feature-detected because the harness's elements have none
const unfocus=el=>{ if(el&&el.blur) el.blur(); };
// the last frame there is: `P.frames.length-1` is -1 on a device with no programme, and a
// seek clamped to it left `frame` at -1
const lastFrame=()=>Math.max(0, P.frames.length-1);
playBtn.onclick=()=>{
  // `.disabled` is presentation only and does NOT stop a programmatic call: the
  // space bar calls `playBtn.onclick()` directly, so the button needs a real guard.
  unfocus(playBtn);
  if(PROGRAM_STALE || !P.frames.length) return;
  if(raf) return stop();
  playBtn.textContent='Pause';
  if(phase>=1 && frame<P.frames.length-1){frame++; phase=0;}
  t0=performance.now()-phase*durOf(frame);
  raf=requestAnimationFrame(tick);
};
document.getElementById('step').onclick=()=>{stop(); unfocus(document.getElementById('step'));
  if(phase<1){phase=1;} else {frame=Math.min(lastFrame(),frame+1);phase=1;}
  draw();};
function runGlide(){
  const dur=Math.max(320, 260*Math.min(6, Math.sqrt(hopsIn(frame)))), g0=performance.now();
  const run=now=>{ phase=Math.min(1,(now-g0)/dur); draw();
    if(phase<1) requestAnimationFrame(run); };
  requestAnimationFrame(run);
}
document.getElementById('glide').onclick=()=>{stop(); unfocus(document.getElementById('glide'));
  if(frame<P.frames.length-1 && phase>=1){frame++;}
  phase=0; draw(); runGlide();};
document.getElementById('phase').onclick=()=>{stop(); unfocus(document.getElementById('phase'));
  const here=(P.frames[frame]||{}).batch;
  let i=frame+1; while(i<P.frames.length-1 && P.frames[i].batch===here) i++;
  frame=Math.min(i, lastFrame()); phase=1; draw();};
document.getElementById('reset').onclick=()=>{stop();frame=0;phase=1;fit();draw();
  unfocus(document.getElementById('reset'));};
document.getElementById('fit').onclick=()=>{fit(); unfocus(document.getElementById('fit'));};
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
// THE JOIN IS FOR THE SHIPPED PROGRAMME ONLY.  An authored programme allocates its frame
// ids from 0 as well, so `LROW[f.id]` answered for it too -- and the NOW strip printed
// "x4 · 105 us" from Python's disassembly of the walk under a two-statement shuttle the
// user had just typed.  Every reader of the listing goes through this one gate.
function listingRow(f){
  const ED = globalThis.EDITOR;
  return (ED && ED.authored && ED.authored()) ? undefined : LROW[f.id];
}

// ---------- the inverted "which instructions touch this object" index ----------
// A rigid rotation names 144 ions; expanding those into the index would build 56k
// entries for ring144 alone, so a template is indexed by its LOOP and resolved at
// query time.  Measured: ~8k entries for the deck program.
const TOUCH = {};
function touch(key, i){ (TOUCH[key] || (TOUCH[key] = [])).push(i); }
// re-indexed whenever the FRAMES change (`rebuildView` sees every such change), so an
// authored programme's chip counts its own instructions, not the shipped walk's
let TOUCH_OF = null;
function indexTouch(){
  if(TOUCH_OF===P.frames) return;
  TOUCH_OF = P.frames;
  for(const k in TOUCH) delete TOUCH[k];
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
}
indexTouch();

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
  // `rowCls` lets a list colour a row from state the row itself does not carry -- the
  // source pane marks whichever lines the CURRENT instruction is discharging, which
  // changes on every step while the rows do not.  Applied here rather than in `render`
  // because marking runs on every cursor move and rendering only on scroll.
  T.mark=()=>{ const base=opt.cls||'ln';
    for(const r of pool) r.className = base+(r._bnd?' bnd':'')
      +(r._i===cur?' cur':'')+(r._i===sel?' sel':'')
      +(opt.rowCls && r._i>=0 ? opt.rowCls(r._i) : ''); };
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
  const li=listingRow(f);
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
  const li=listingRow(f);
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
  const li=listingRow(f), bits=[f.type||'', f.cls||'', f.check||'', f.gate||''];
  bits.push(stripTags(argsHTML(f)));
  if(li!=null) bits.push(LST.ops[LST.op[li]], LST.detail[li]);
  if(f.call!=null && PROV && PROV.calls[f.call]){
    const c=PROV.calls[f.call], st=PROV.sites[c.site];
    // whatever you can READ you can find, and the footer now reads the pass name alone:
    // searching the path or the source text would match a row that shows neither
    bits.push(c.op); if(st) bits.push(st.func||'');
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
  const f = P.frames[i] || {}, li = listingRow(f);
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
  // NO INSTRUCTION is a sentence, not `#undefined`: a device with no programme, or a
  // frame index past the end, used to print the shipped listing's join of `undefined`.
  if(!P.frames.length || f.id===undefined)
    return '<span class="mut">no instruction — press Test drive or write a programme</span>';
  const li = listingRow(f);
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
  h += qInlineHTML(f);
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
  if(!P.frames.length) return '';       // nothing is executing, so nothing drove it
  const und = [];
  if(CTLD && f.ctl!=null && CTLD.records[f.ctl])
    for(const u of (CTLD.records[f.ctl].und||[])) und.push(CTLD.notes[u]);
  const note = und.length ? '<br><span class="mut">not determined: '+esc(und[0])+'</span>' : '';
  if(!PROV || f.call==null || !PROV.calls || !PROV.calls[f.call])
    return '<span class="mut">no source line recorded for this instruction</span>'+note;
  const c=PROV.calls[f.call], st=(PROV.sites||[])[c.site]||{};
  // THE PASS, never the file it lives in.  A reader has no clone of this repository, so
  // `qccd/compile/cooling.py:350` is a coordinate into something they cannot open, and
  // `st.text` is a line of our Python quoted at them.  `c.op` is the whole claim this
  // footer makes -- which pass put this instruction here -- and it reads as a name:
  // "from compile.insert_cooling".  `st.func` is the fallback for a payload that
  // predates `op`; the file, line and text stay in the payload for a local debugger and
  // are rendered nowhere.
  return 'from <b>'+esc(c.op||st.func||'?')+'</b>'+note;
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
  frame = clamp(0, i, lastFrame());
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
  indexTouch();
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
  if(QLIST){
    QMARK = qMarksFor(f);
    const ln = qFirstLine(QMARK);
    QLIST.setCursor(ln>0 ? ln-1 : -1);      // setCursor always re-marks, so qRowCls runs
    document.getElementById('qNow').innerHTML = qNowHTML(f);
  }
  updateChip();
  const ph=document.getElementById('playhead');
  if(ph) ph.style.left = (100*frame/Math.max(1,P.frames.length-1))+'%';
}

// ======================================================================
//  THE SOURCE CIRCUIT -- which QASM statement this pulse is discharging
//
//  A hand-written program answers "what is executing?" with the instruction and
//  nothing else.  A COMPILED one has a second answer, and it is the one somebody
//  debugging a compiler wants: which line of their circuit this pulse is for, and
//  -- while the machine is only shuttling -- which line it is travelling towards.
//
//  The join arrives in `D.source`, built from the compiler's certificate.  That
//  matters: the certificate is the artifact the Lean checker decides, so "this
//  instruction realises op 14" is a claim something has verified, not a label the
//  compiler attached to its own output for the benefit of a pretty page.
// ======================================================================
const SRC = D.source || null;
let QLIST = null, QMARK = null;

const QOP = {}, QOPLINE = {}, QINSTR = {}, FIDX = {};
if(SRC){
  for(const o of SRC.ops){ QOP[o.i]=o; (QOPLINE[o.line] || (QOPLINE[o.line]=[])).push(o.i); }
  for(const k in SRC.realises)
    for(const oi of SRC.realises[k]) (QINSTR[oi] || (QINSTR[oi]=[])).push(+k);
  for(let i=0;i<P.frames.length;i++) FIDX[P.frames[i].id] = i;
}

// THE CIRCUIT BELONGS TO THE SHIPPED PROGRAMME.  `SRC.realises` maps the compiled walk's
// frame ids to the statements they discharge; an authored frame carries an id from a
// different programme, and joining it here highlighted `h q[0]` under a shuttle the user
// wrote by hand.  Every reader of the map goes through this, the way the Program pane's
// `listingRow` gates the shipped listing: null while the programme is the user's own.
function circuitOf(){
  const ED = globalThis.EDITOR;     // `globalThis`, as `listingRow`: the harness's `window` is another object
  return (ED && ED.authored && ED.authored()) ? null : SRC;
}
// line -> 2 (a statement this instruction realises) | 1 (one it is moving towards)
function qMarksFor(f){
  const m = {}, S = circuitOf();
  if(!S) return m;
  for(const k of ['toward','after'])
    for(const oi of ((S[k]||{})[f.id] || [])){ const o=QOP[oi]; if(o && !m[o.line]) m[o.line]=1; }
  for(const oi of (S.realises[f.id] || [])){ const o=QOP[oi]; if(o) m[o.line]=2; }
  return m;
}
function qFirstLine(m){
  let best=-1, rank=0;
  for(const k in m){ const L=+k;
    if(m[k]>rank || (m[k]===rank && (best<0||L<best))){ rank=m[k]; best=L; } }
  return best;
}
function qOpHTML(oi){
  const o=QOP[oi];
  if(!o) return '';
  return '<b>'+esc(o.name)+'</b> '+o.q.map(q=>'q['+q+']').join(',')
    + (o.p && o.p.length ? '<i class="mut">('+o.p.map(x=>fmt(x,3)).join(',')+')</i>' : '')
    + ' <span class="mut">:'+o.line+'</span>';
}
function qListHTML(ids, cap){
  return ids.slice(0,cap).map(qOpHTML).join(' &middot; ')
    + (ids.length>cap ? ' <i class="mut">+'+(ids.length-cap)+' more</i>' : '');
}
function qNowHTML(f){
  const S = circuitOf();
  if(!S) return SRC ? '<i class="mut">no circuit &mdash; this programme was written here</i>' : '';
  const now = S.realises[f.id] || [], soon = S.toward[f.id] || [],
        past = (S.after||{})[f.id] || [];
  if(now.length) return '<b>executing</b> &nbsp;'+qListHTML(now,3);
  if(soon.length) return '<span class="mut">shuttling towards</span> &nbsp;'+qListHTML(soon,3);
  if(past.length) return '<span class="mut">clearing after</span> &nbsp;'+qListHTML(past,3);
  return '<i class="mut">no circuit statement &mdash; '+esc(f.type)
    + ' is the compiler&#39;s own bookkeeping</i>';
}
// the one-line version, for the hardware pane, so the answer is there without changing
// tabs -- which is the whole point of putting the two side by side
function qInlineHTML(f){
  const S = circuitOf();
  if(!S) return '';
  const now = S.realises[f.id] || [], soon = S.toward[f.id] || [],
        past = (S.after||{})[f.id] || [];
  if(now.length) return '<br><span class="mut">circuit &rarr;</span> '+qListHTML(now,2);
  if(soon.length) return '<br><span class="mut">circuit &rarr; towards</span> '+qListHTML(soon,2);
  if(past.length) return '<br><span class="mut">circuit &rarr; after</span> '+qListHTML(past,2);
  return '';
}
function qRowCls(row){
  const L=row+1, k=(QMARK||{})[L];
  return (k===2?' qh':(k===1?' qw':'')) + (QOPLINE[L]?'':' qz');
}
function renderSrcRow(r,row){
  const k=r._k;
  k[0].textContent = String(row+1);
  k[1].textContent = SRC.lines[row] || '';
  const ops = QOPLINE[row+1];
  r._ref = ops ? {kind:'op', id:ops[0]} : null;
}
// Click a statement, land on the instruction that discharges it.  The inverse of the
// cursor, and the direction a compiler bug is usually chased in: you know which gate
// looks wrong, you want to see what the machine did about it.
function pickSrc(row){
  const ops = QOPLINE[row+1];
  // `FIDX` indexes the shipped frames: no statement lands anywhere in an authored programme
  if(!ops || !circuitOf()) return;
  let best = -1;
  for(const oi of ops) for(const id of (QINSTR[oi] || [])){
    const fi = FIDX[id];
    if(fi!=null && (best<0 || fi<best)) best = fi;
  }
  if(best<0) return;
  stop(); frame=best; phase=1; slider.value=String(frame); draw();
}

// ---------- panes ----------
// ---------- the programme column ----------
// WHERE THE PROGRAM PANE LIVES is a layout decision, not a pane: beside the animation
// (the whole height, so a long programme reads as a listing and the next instructions
// are in view) or among the dock's panes.  The wide regime always puts it beside; the
// tall regime pins it there when the window has room (>= 1500 px) or when the user says
// so; the narrow regime never does.  One DOM node moves; nothing is rendered twice.
let PROG_COL = false, PROG_PIN = null, PROG_VIEW = 'hw';
function progPinDefault(){
  const w = (typeof window !== 'undefined' && window.innerWidth) || 1600;
  return w >= 1500;
}
function placeProgram(){
  const mode = document.getElementById('row').getAttribute('data-layout');
  if(PROG_PIN === null){
    let s = null; try { s = localStorage.getItem('qccd.studio.progpin'); } catch (e) { s = null; }
    PROG_PIN = s === null ? progPinDefault() : s === '1';
  }
  const col = mode === 'wide' || (mode === 'tall' && PROG_PIN);
  const pc = document.getElementById('progcol'), panes = document.getElementById('panes'),
        pp = document.getElementById('paneP'), dock = document.getElementById('dock');
  if(!pc || !pp) return false;
  if(col !== PROG_COL){ PROG_COL = col; (col ? pc : panes).appendChild(pp); }
  pc.setAttribute('data-collapsed', col ? '0' : '1');
  dock.setAttribute('data-prog', col ? 'col' : 'dock');
  const tabs = document.getElementById('tabs'); if(tabs) tabs.setAttribute('data-prog', col ? 'col' : 'dock');
  const pin = document.getElementById('pPin');
  if(pin){
    pin.style.display = mode === 'tall' ? '' : 'none';
    pin.innerHTML = col ? 'Into the panels &#9654;' : '&#9664; Beside the animation';
    pin.setAttribute('aria-pressed', col ? 'true' : 'false');
  }
  // the dock cannot show a pane that is not in it: fall through to the course
  setPane(col && PANE === 'P' ? 'L' : PANE);
  return col;
}
function progPinToggle(on){
  PROG_PIN = on === undefined ? !PROG_PIN : !!on;
  try { localStorage.setItem('qccd.studio.progpin', PROG_PIN ? '1' : '0'); } catch (e) { /* no store */ }
  const col = placeProgram();
  sizeStage();
  return col;
}
// WHAT THE PANE SHOWS: the hardware programme, the circuit statements, or both stacked.
// Without a source circuit there is nothing to switch and the switch is not shown.
function setProgView(v){
  if(!SRC) v = 'hw';
  if(['hw','gates','both'].indexOf(v) < 0) v = 'hw';
  PROG_VIEW = v;
  const pp = document.getElementById('paneP');
  if(pp) pp.setAttribute('data-view', v);
  for(const k of ['hw','gates','both']){
    const b = document.getElementById('pv'+k); if(b) b.className = v === k ? 'on' : '';
  }
  const seg = document.getElementById('pView'); if(seg) seg.style.display = SRC ? '' : 'none';
  try { localStorage.setItem('qccd.studio.progview', v); } catch (e) { /* no store */ }
  syncQ();
  sizeLists();
  return v;
}
// the Circuit listing's off switch is an attribute: a pane that shows every block must
// not show this one with nothing behind it
function syncQ(){
  const pq = document.getElementById('paneQ');
  if(pq){ if(SRC) pq.removeAttribute('data-off'); else pq.setAttribute('data-off','1'); }
  syncMenu();
}
// THE MENU'S LIGHTS, from one place: an item is lit when the thing it opens is showing.
// Program and Circuit read the column when the programme lives there, the dock otherwise;
// Circuit is `off` without a source circuit, which is what the harness reads.
function syncMenu(){
  const dk = document.getElementById('dock'), pc = document.getElementById('progcol');
  const open = !!dk && dk.getAttribute('data-collapsed') !== '1';
  const colOn = PROG_COL && !!pc && pc.getAttribute('data-collapsed') !== '1';
  for(const k of ['L','P','Q','A','M','W','R']){
    const t = document.getElementById('tab'+k); if(!t) continue;
    let on;
    if(k === 'P') on = PROG_COL ? colOn : (open && PANE === 'P');
    else if(k === 'Q') on = !!SRC && PROG_VIEW !== 'hw' && (PROG_COL ? colOn : (open && PANE === 'P'));
    else on = open && PANE === k;
    t.className = 'tab' + (on ? ' on' : '') + (k === 'Q' && !SRC ? ' off' : '');
  }
}
// WHAT A MENU ITEM DOES: open its panel, or close it if it is the one showing.  With the
// programme in its column, Program folds and unfolds the column and Circuit switches the
// column between the hardware listing and both listings.
function menuPick(k){
  const dk = document.getElementById('dock'), pc = document.getElementById('progcol');
  if(PROG_COL && (k === 'P' || k === 'Q')){
    const hidden = pc.getAttribute('data-collapsed') === '1';
    if(k === 'P'){ pc.setAttribute('data-collapsed', hidden ? '0' : '1'); }
    else { if(hidden) pc.setAttribute('data-collapsed', '0'); setProgView(PROG_VIEW === 'hw' ? 'both' : 'hw'); }
    syncMenu(); sizeStage();
    return { col: pc.getAttribute('data-collapsed') !== '1', view: PROG_VIEW };
  }
  const open = dk.getAttribute('data-collapsed') !== '1', target = k === 'Q' ? 'P' : k;
  if(open && PANE === target && (k !== 'Q' || PROG_VIEW !== 'hw')){ foldPanel(dk, true); return { pane: null }; }
  foldPanel(dk, false);
  setPane(k);
  return { pane: PANE, view: PROG_VIEW };
}
function setPane(which){
  if(which === 'Q'){ setProgView('gates'); which = 'P'; }
  // the programme beside the animation is not a dock pane: the dock keeps what it shows
  if(which === 'P' && PROG_COL){ syncQ(); sizeLists(); return; }
  PANE=which;
  for(const k of ['L','P','A','M','W','R']){
    const on = k === which || (k === 'P' && PROG_COL);
    document.getElementById('pane'+k).className = 'pane card'+(on?' on':'');
  }
  syncQ();
  // the wide strip scrolls sideways when its panes do not all fit: bring the pane in
  const panes=document.getElementById('panes'), pe=document.getElementById('pane'+which);
  if(panes && pe && typeof pe.offsetLeft==='number' &&
     document.getElementById('row').getAttribute('data-layout')==='wide')
    panes.scrollLeft = pe.offsetLeft - panes.offsetLeft;
  sizeLists();
}
for(const k of ['L','P','Q','A','M','W','R']){
  const t = document.getElementById('tab'+k); if(t) t.onclick = () => menuPick(k);
}
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
  // the list's flex wrapper folds with it, or the source view opens under a blank band
  if(host && host.parentNode && host.parentNode.style) host.parentNode.style.display = v==='src' ? 'none' : '';
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

// the lists take the height their pane leaves them (CSS); this only re-measures it
function sizeLists(){
  if(!PLIST) return;
  PLIST.measure(); ALIST.measure(); PLIST.paint(true); ALIST.paint(true);
  if(QLIST){ QLIST.measure(); QLIST.paint(true); }
}

// ---------- build ----------
PLIST = makeList('pScroll','pPad','pWin', {render:renderProgRow, onPick:pickProg,
  onFollow:v=>{ const b=document.getElementById('pFollow');
                b.className='tgl'+(v?' on':''); b.setAttribute('aria-pressed', v?'true':'false'); }});
ALIST = makeList('aScroll','aPad','aWin', {render:renderArchRow, onPick:pickArch,
  cells:['i','a'], cls:'al', userScrollBreaksFollow:false});
if(SRC){
  QLIST = makeList('qScroll','qPad','qWin', {render:renderSrcRow, onPick:pickSrc,
    cells:['n','a'], cls:'ql', rowCls:qRowCls,
    onFollow:v=>{ const b=document.getElementById('qFollow');
      b.className='tgl'+(v?' on':''); b.setAttribute('aria-pressed', v?'true':'false'); }});
  QLIST.setCount(SRC.lines.length);
  document.getElementById('tabQ').className = 'tab';
  document.getElementById('qCount').textContent =
    SRC.ops.length+' statements \u00b7 '+SRC.lines.length+' lines';
  document.getElementById('qFoot').innerHTML =
    '<i class="mut">'+esc(SRC.name)+' \u2014 click a statement to jump to the instruction '
    + 'that discharges it</i>';
  document.getElementById('qFollow').onclick=()=>{
    QLIST.setFollow(!QLIST.follow);
    if(QLIST.follow) syncCursor(true);
  };
}
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
// The editor's click path does this (`selectRef` from its `end` adapter), for every kind
// of element and at all times.  A second delegated listener used to live here and the
// two disagreed about what a click on a segment meant; one owner now.

// ---------- keyboard: ONE table, ONE dispatcher, ONE listener ----------
// The table is `EDITOR.keyGesture` (editor.js); it also renders the `?` overlay, so a
// binding cannot exist without its line of help.  This dispatcher is callable without an
// Event -- `tests/drive.mjs` presses keys through it -- and the listener below is a
// one-line adapter onto it.  Two listeners used to run on the same target: the editor's
// nudged the selection with the arrows while this one scrubbed the programme, so every
// nudge also moved the playhead; space panned there and played here.
// `mods` is `{ctrl, meta, shift, alt}`; `ctx.field` names the focused text field (id or
// tag) and `ctx.target` is the focused element, if any.
function clearPageSelection(){
  SEL=null; ALIST.setSelection(-1);
  document.getElementById('pChip').className='chip off';
}
function pressKey(key, mods, ctx){
  mods = mods || {}; ctx = ctx || {};
  // `globalThis`, not `window`: the harness's window is a plain object, the editor
  // publishes on globalThis, and in a browser the two are the same object
  const ED = globalThis.EDITOR || window.EDITOR;
  const verb = (ED && ED.keyGesture) ? ED.keyGesture(key, mods, ctx) : null;
  let result = null;
  if(!verb) return { verb: null, result: null };
  // a key never acts on a focused toolbar button: the space bar would re-fire it
  if(ctx.target && ctx.target.tagName==='BUTTON') unfocus(ctx.target);
  switch(verb){
    case 'blur': unfocus(ctx.target); break;
    case 'clear-filter': if(ctx.target) ctx.target.value=''; rebuildView('', null); rebuildArchView(''); unfocus(ctx.target); break;
    case 'save': if(ED.saveProject) ED.saveProject(); break;
    case 'undo': ED.undoGroup(); break;
    case 'redo': ED.redoGroup(); break;
    case 'toggle-play': playBtn.onclick(); result = !!raf; break;
    // ESCAPE DOES ONE THING PER PRESS, in the editor's order (drag, stamp, selection,
    // help); only when the editor had nothing to cancel does the page clear its own
    // listing selection
    case 'escape': result = ED.cancelGesture(); if(result===null){ clearPageSelection(); result='page'; } break;
    case 'nudge': result = ED.nudge(key, mods.shift ? 4 : 1); break;
    case 'remove': result = ED.removeSelected();
      if(result && !result.ok && result.problems.length && ED.toast) ED.toast('bad', result.problems[0].message); break;
    case 'reconcile': result = ED.reconcileLast(); break;
    case 'seek-next': seek(frame+1,{}); result=frame; break;
    case 'seek-prev': seek(frame-1,{}); result=frame; break;
    case 'seek-ahead': seek(frame+25,{}); result=frame; break;
    case 'seek-back': seek(frame-25,{}); result=frame; break;
    case 'seek-first': seek(0,{}); result=frame; break;
    case 'seek-last': seek(lastFrame(),{}); result=frame; break;
    case 'glide': seek(frame,{glide:true}); result=frame; break;
    case 'fit': fit(); break;
    case 'explain': result = ED.explainToggle ? ED.explainToggle() : null; break;
    case 'measure': result = ED.measureToggle ? ED.measureToggle() : null; break;
    // the sketch: the mode, the four shape tools, and the key that ends a polyline
    case 'design-mode': result = ED.setDesignMode(ED.designMode() === 'sketch' ? 'parts' : 'sketch'); break;
    case 'shape-rect': result = ED.sketchTool('rect'); break;
    case 'shape-ellipse': result = ED.sketchTool('ellipse'); break;
    case 'shape-line': result = ED.sketchTool('line'); break;
    case 'shape-poly': result = ED.sketchTool('poly'); break;
    case 'sketch-finish': result = ED.sketchFinish(false); break;
    case 'follow': document.getElementById('pFollow').onclick(); result=!!PLIST.follow; break;
    case 'filter': { const f=document.getElementById('pFilter'); if(f.focus) f.focus(); break; }
    case 'fold-rail': foldPanel(railEl); result=window.PANELS.state(); break;
    case 'fold-dock': foldPanel(dockEl); result=window.PANELS.state(); break;
    // fold both, which is what you want while drawing; unfold both when both are folded
    case 'fold-both': { const on = !(window.PANELS.state().rail && window.PANELS.state().dock);
      foldPanel(railEl, on); foldPanel(dockEl, on); result=window.PANELS.state(); break; }
    // a pane key with the dock folded would change PANE behind a closed door
    case 'pane-P': foldPanel(dockEl, false); setPane('P'); break;
    case 'pane-A': foldPanel(dockEl, false); setPane('A'); break;
    case 'pane-M': foldPanel(dockEl, false); setPane('M'); break;
    case 'help': result = ED.helpToggle(); break;
    // the Modify panel's own two keys, from the editor's one keymap
    case 'modify-apply': result = ED.modifyApply ? ED.modifyApply() : null; break;
    case 'modify-cancel': result = ED.modifyClose ? ED.modifyClose() : null; break;
  }
  return { verb: verb, result: result };
}
// ---------- the tools bar: popovers over the rail's sections, and the search ----------
// A popover is a rail section shown fixed under its button.  The three palette folds are
// re-created on every palette paint, so the open one is re-applied from the editor's
// `onPalette` hook; Start and Selection are static.  A selection opens its popover; Escape
// or a click elsewhere closes any.
const TOOLS = document.getElementById('tools');
let POP = null;
function popEl(key){
  if(key === 'start') return document.getElementById('palStart');
  const body = document.getElementById('palBody');
  if(!body) return null;
  for(const c of (body.children || [])) if(c.getAttribute && c.getAttribute('data-fold') === key) return c;
  return null;
}
// class toggles by string, not classList: the harness's shim has no classList behaviour
function setCls(el, c, on){ const parts = ((el.className || '') + '').split(/\s+/).filter(x => x && x !== c); if(on) parts.push(c); el.className = parts.join(' '); }
const POP_BTN = { start: 'tbStart', row: 'tbRows', block: 'tbMachine', component: 'tbComponents' };
const POP_KEYS = Object.keys(POP_BTN);
function popButton(key){ return document.getElementById(POP_BTN[key] || ''); }
function applyPops(){
  if(!TOOLS) return;
  for(const key of POP_KEYS){
    const el = popEl(key), b = popButton(key), on = POP === key;
    if(b) b.setAttribute('aria-expanded', on ? 'true' : 'false');
    if(!el) continue;
    if(on){
      setCls(el, 'pop-open', true);
      if(el.tagName === 'DETAILS') el.open = true;
      const br = b && b.getBoundingClientRect ? b.getBoundingClientRect() : null, tr = TOOLS.getBoundingClientRect ? TOOLS.getBoundingClientRect() : null;
      if(br && tr){ el.style.left = Math.max(4, br.left) + 'px'; el.style.top = (tr.bottom + 4) + 'px'; }
    } else setCls(el, 'pop-open', false);
  }
}
function openPop(key){
  // AN UNKNOWN KEY CHANGES NOTHING.  "selection" was one of these until the right-click
  // menu took its job; a caller that still asks for it must not leave POP naming a
  // popover no button opens and no rule shows.
  if(key && !POP_BTN[key]) return POP;
  POP = (key && POP === key) ? null : (key || null);
  // the popovers live in the rail's DOM: a folded rail must come back for one to show
  try { if(POP && railEl && railEl.getAttribute('data-collapsed') === '1') foldPanel(railEl, false); } catch(e){}
  applyPops();
  return POP;
}
window.onPalette = applyPops;
// NOTHING POPS ITSELF OPEN ANY MORE.  `window.onInspector` opened the Selection popover
// on every single selection, so the one gesture everybody makes constantly threw a panel
// over the canvas.  The editor still calls the hook if a page defines one; this page does
// not, and `renderInspector` skips it.
if(TOOLS){
  for(const key in POP_BTN){ const b = popButton(key); if(b) b.onclick = () => openPop(key); }
  document.addEventListener('pointerdown', e => {
    const t = e.target;
    // A CLICK ELSEWHERE CLOSES THE ELEMENT MENU -- in the SAME listener that already does
    // this for the popovers, rather than a second one that would race it.  A press on the
    // stage is the stage's own: `claim` arbitrates it and the editor closes the menu there.
    const cm = document.getElementById('ctxmenu');
    if(cm && cm.getAttribute('data-open') === '1' && !(cm.contains && cm.contains(t))
       && !(svg.contains && svg.contains(t))){
      if(window.EDITOR && EDITOR.menuClose) EDITOR.menuClose();
    }
    if(TOOLS.contains(t)) return;
    for(const key of POP_KEYS){ const el = popEl(key); if(el && el.contains && el.contains(t)) return; }
    if(SRES && SRES.contains && SRES.contains(t)) return;
    if(POP) openPop(null);
    const sr = document.getElementById('sres'); if(sr && !sr.contains(t)) sr.setAttribute('data-open', '0');
  }, true);
  document.addEventListener('keydown', e => {
    if(e.key === 'Escape'){ if(POP) openPop(null); const sr = document.getElementById('sres'); if(sr) sr.setAttribute('data-open', '0'); }
    if((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')){ e.preventDefault(); const s = document.getElementById('search'); if(s){ s.focus(); s.select(); } }
  }, true);
  applyPops();
}
// THE SEARCH: every control on the page with its hint text, the course's lessons.  A hit
// says where it lives; choosing it opens that pane or popover, scrolls to it, flashes it.
const SRES = document.getElementById('sres'), SINP = document.getElementById('search');
const PANE_NAMES = { L: 'Learn', P: 'Program', Q: 'Circuit', A: 'Device', M: 'Machine', W: 'Write', R: 'Report' };
const POP_NAMES = { start: 'Start', row: 'Append a row', block: 'Machine settings', component: 'Components' };
function sTextOf(el){
  if(el.classList && el.classList.contains('pal-item')){ const nb = el.querySelector('.pal-text b'); if(nb) return nb.textContent.trim(); const de = el.getAttribute('data-el'); if(de) return de.replace(/^cmp:/, '').replace(/_/g, ' '); }
  let t = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
  if(!t){ const av = el.querySelector && el.querySelector('.avatar'); let txt = (typeof el.innerText === 'string' && el.innerText) ? el.innerText : (el.textContent || ''); if(av && av.textContent) txt = txt.replace(av.textContent, ' '); t = txt; }
  return t.trim().replace(/\s+/g, ' ').slice(0, 70);
}
function sWhereOf(el){
  let e = el;
  while(e && e !== document.body){
    if(e.id === 'palStart') return { kind: 'pop', key: 'start', name: 'Start (tools bar)' };
    if(e.id === 'palInspect') return { kind: 'rail', name: 'Selection (left rail)' };
    if(e.classList && e.classList.contains('palfold') && e.getAttribute('data-fold')) return { kind: 'pop', key: e.getAttribute('data-fold'), name: (POP_NAMES[e.getAttribute('data-fold')] || 'panel') + ' (tools bar)' };
    if(e.id && /^pane[A-Z]$/.test(e.id)) return { kind: 'pane', key: e.id.slice(4), name: PANE_NAMES[e.id.slice(4)] || 'panel' };
    if(e.id === 'progcol') return { kind: 'pane', key: 'P', name: 'Program column' };
    if(e.id === 'rail') return { kind: 'rail', name: 'Elements (left rail)' };
    if(e.id === 'stagebar') return { kind: 'bar', name: 'transport bar' };
    if(e.id === 'tools') return { kind: 'tools', name: 'tools bar' };
    if(e.classList && e.classList.contains('head')) return { kind: 'head', name: 'header' };
    e = e.parentElement;
  }
  return { kind: 'page', name: 'page' };
}
function sIndex(){
  const out = [], seen = new Set();
  let els = []; try { els = document.querySelectorAll('button, [data-hint], .pal-item, summary, select, input[placeholder], label'); } catch(e){ els = []; }
  for(const el of els){
    if(SRES && SRES.contains(el)) continue;
    const label = sTextOf(el), key = el.getAttribute('data-hint');
    let h = null; try { h = (key && window.EDITOR && EDITOR.hintFor) ? EDITOR.hintFor(key) : null; } catch(e){}
    const lab = label || (h ? h.t : '');
    if(!lab) continue;
    const w = sWhereOf(el), sig = lab + '|' + (h ? h.t : '') + '|' + w.name;
    if(seen.has(sig)) continue; seen.add(sig);
    out.push({ label: lab, title: h ? h.t : (el.getAttribute('title') || ''), desc: h ? (h.d || '') + (h.k ? '  [' + h.k + ']' : '') : (el.getAttribute('title') || ''), where: w, el });
  }
  try { (EDITOR.lessonList() || []).forEach(l => out.push({ label: 'Lesson ' + l.id + ' \u00b7 ' + l.title, title: '', desc: 'the course, part ' + l.part, where: { kind: 'lesson', id: l.id, name: 'Learn' }, el: null })); } catch(e){}
  return out;
}
function sReveal(item){
  const w = item.where, el = item.el;
  openPop(null); if(SRES) SRES.setAttribute('data-open', '0');
  if(w.kind === 'lesson'){ try { EDITOR.lessonLoad(w.id); } catch(e){} foldPanel(document.getElementById('dock'), false); setPane('L'); return; }
  if(w.kind === 'pop') openPop(w.key);
  if(w.kind === 'pane'){ const dk = document.getElementById('dock'); if(w.key === 'P' && PROG_COL){ const pc = document.getElementById('progcol'); if(pc.getAttribute('data-collapsed') === '1') menuPick('P'); } else { foldPanel(dk, false); setPane(w.key); } }
  if(w.kind === 'rail'){ try { if(railEl.getAttribute('data-collapsed') === '1') foldPanel(railEl, false); } catch(e){} }
  if(!el) return;
  try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch(e){}
  try { el.focus({ preventScroll: true }); } catch(e){}
  setCls(el, 'flash', true); setTimeout(() => setCls(el, 'flash', false), 1800);
}
let SHITS = [], SSEL = 0;
function sSearch(q){
  if(!SRES) return [];
  q = (q || '').trim().toLowerCase();
  SRES.replaceChildren();
  if(!q){ SRES.setAttribute('data-open', '0'); return []; }
  const words = q.split(/\s+/), all = sIndex();
  SHITS = all.map(it => {
    const hay = (it.label + ' ' + it.title + ' ' + it.desc + ' ' + it.where.name).toLowerCase();
    if(!words.every(w => hay.indexOf(w) >= 0)) return null;
    const score = words.reduce((a, w) => a + (it.label.toLowerCase().indexOf(w) >= 0 ? 2 : 0) + (it.title.toLowerCase().indexOf(w) >= 0 ? 1 : 0), 0);
    return { it, score };
  }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 14).map(x => x.it);
  SSEL = 0;
  if(!SHITS.length){ const n = document.createElement('div'); n.className = 'none'; n.textContent = 'nothing matches \u201c' + q + '\u201d'; SRES.appendChild(n); }
  SHITS.forEach((it, i) => {
    const r = document.createElement('div'); r.className = 'r'; r.setAttribute('data-sel', i === 0 ? '1' : '0');
    const b = document.createElement('b'); b.textContent = it.label; r.appendChild(b);
    const w = document.createElement('span'); w.className = 'w'; w.textContent = 'in ' + it.where.name; r.appendChild(w);
    if(it.desc || it.title){ const d = document.createElement('div'); d.className = 'd'; d.textContent = (it.title && it.title !== it.label ? it.title + ' \u2014 ' : '') + it.desc; r.appendChild(d); }
    r.onclick = () => sReveal(it);
    SRES.appendChild(r);
  });
  SRES.setAttribute('data-open', '1');
  return SHITS.map(it => ({ label: it.label, where: it.where.name }));
}
if(SINP){
  SINP.addEventListener('input', () => sSearch(SINP.value));
  SINP.addEventListener('focus', () => { if(SINP.value) sSearch(SINP.value); });
  SINP.addEventListener('keydown', e => {
    if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){ e.preventDefault(); if(!SHITS.length) return; SSEL = (SSEL + (e.key === 'ArrowDown' ? 1 : SHITS.length - 1)) % SHITS.length; Array.from(SRES.children).forEach((r, i) => r.setAttribute('data-sel', i === SSEL ? '1' : '0')); }
    if(e.key === 'Enter'){ e.preventDefault(); if(SHITS[SSEL]) sReveal(SHITS[SSEL]); }
  });
}
window.TOOLSBAR = { open: openPop, current: () => POP, search: sSearch, reveal: (i) => { if(SHITS[i]) sReveal(SHITS[i]); return POP; } };
window.KEYS = { press: pressKey };
document.addEventListener('keydown', e=>{
  const tag = e.target && e.target.tagName;
  const field = (tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA') ? (e.target.id || tag) : null;
  // `null` is "not ours" -- ctrl+C, ctrl+F, alt+tab and typing in a field keep their
  // browser meaning; every verb we take is prevented, space included, so the page never
  // scrolls under a play/pause press
  if(pressKey(e.key, {ctrl:e.ctrlKey, meta:e.metaKey, shift:e.shiftKey, alt:e.altKey},
              {field, target:e.target}).verb) e.preventDefault();
});
if(AL) document.getElementById('aFoot').innerHTML =
  'round-trip ' + (AL.round_trip===true ? 'verified' :
                   (AL.round_trip===false ? 'FAILED' : 'not checked'))
  + ' \u00b7 ' + esc(AL.mode) + ' form';
rebuildArchView('');
rebuildView('', null);
sizeLists();
// the programme column and its view, after the lists exist
{
  const pin = document.getElementById('pPin');
  if(pin) pin.onclick = () => progPinToggle();
  for(const k of ['hw','gates','both']){
    const b = document.getElementById('pv'+k); if(b) b.onclick = () => setProgView(k);
  }
  let v = null; try { v = localStorage.getItem('qccd.studio.progview'); } catch (e) { v = null; }
  placeProgram();
  setProgView(v || (SRC ? 'both' : 'hw'));
  // the page opens on the canvas; a page built for the course opens on its Learn pane
  if(D.open_pane && document.getElementById('pane' + D.open_pane)){
    foldPanel(document.getElementById('dock'), false);
    setPane(D.open_pane);
  }
  syncMenu();
}

relayout();
fit();
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
    source: dict | None = None,
    open_pane: str | None = None,
    tech=None,
) -> Path:
    """Write the self-contained page.  Returns the path written.

    `open_pane` names the dock pane the page opens on (`"L"` for the course); the default
    is none -- the page opens on the canvas and the menu in the head opens a pane."""
    view = build_view_model(arch, prog, res, model, max_frames=max_frames,
                            kicker=kicker, headline=headline, lede=lede,
                            control=control, provenance=provenance,
                            template_stems=template_stems, metal=metal,
                            source=source, tech=tech)
    view["open_pane"] = open_pane
    html = _TEMPLATE.replace("__TITLE__", f"{arch.name} - {prog.name}")
    html = html.replace("__STAMP__", page_stamp())
    html = html.replace("__CSSVARS__", css_vars())
    blob = json.dumps(view, separators=(",", ":"))
    _refuse_repository_paths(blob)
    html = html.replace("__DATA__", _escape_blob(blob))
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
