"""THE PARITY GUARANTEE for `qccd/viz/engine.js`.

`qccd/viz/layout.py` + `qccd/arch/generators.py` + `qccd/cost/hardware.py` + the `Machine`
setters on one side, and `qccd/viz/engine.js` on the other, are two implementations of one
truth.  Layout was deliberately moved OUT of JS into Python so it could be tested; putting
a mirror back is only defensible with a mechanical guarantee that fails loudly when the two
drift, because this codebase has already been bitten hard by exactly that shape of bug --
a JS operand renderer and a Python one that disagreed, and a filter that searched text the
user could not see, giving 3,830 of 3,830 rows wrong.

THE GUARANTEE IS A DIFFERENTIAL RUN, NOT A GOLDEN FILE.  Every reference number below is
recomputed by live Python at test time and handed to node in the same process; nothing is
stored between runs, so there is nothing to refresh and no way for the corpus to go stale.

TOLERANCE IS ZERO.  Not 1e-9 -- zero, on both the quantized scalars the page ships and,
under `raw`, the unquantized intermediates.  The justification is structural rather than
empirical luck: after the portable-arithmetic patch (`layout.py`'s `_hyp` / `_fsum` / `_q`)
`compute_layout` uses only `+ - * /`, `sqrt`, `min`, `max`, `floor`, `ceil`, comparisons
and a total-order sort.  Every one of those is correctly rounded and identically specified
by IEEE-754 binary64 in CPython and V8, and neither runtime contracts to FMA nor uses x87
extended precision on x86-64, so the set of reachable answers is a single bit pattern.  Any
epsilon would hide a defect rather than absorb noise, and NEEDING one is itself the alarm
that a non-portable idiom crept back in.

WHY BOTH MODES.  Quantization is a very effective mask: rounding to 3-4 decimals hides the
overwhelming majority of real divergences, so a round-only harness would shrug at drift
that is one edit away from becoming visible.  `--raw` diffs the unrounded intermediates.

WHY ORDERED STRUCTURES.  Generator parity is exact equality over the ORDERED node/segment/
loop lists, field by field, not counts and not sets.  Order is load-bearing: `_bows`'s
centroid is an order-dependent float sum whose winner is "the first strict minimum in
declaration order", and `Device.to_json` emits dict-insertion order into a byte-compared
file.  A set comparison would pass a device that then lays out differently.

WHY A MUTATION GUARD.  A parity test with no mutation guard is a golden vector wearing a
disguise -- it can rot into a no-op and nobody notices.  `test_the_parity_harness_catches_*`
publishes deliberately broken engines and asserts the harness reports a mismatch for each,
following the discipline `test_the_harness_actually_catches_an_oversized_ion` and
`test_the_panel_harness_catches_a_listing_that_stops_following` already set here.

Fuzz size and seed come from `QCCD_FUZZ` / `QCCD_SEED`.  The seed defaults to 0 so a local
run reproduces exactly; CI can rotate it, because a golden vector goes stale silently but a
seeded fuzz whose seed moves does not.
"""

from __future__ import annotations

import ast
import copy
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.arch import GENERATORS, TABLES, load
from qccd.arch.device import ExpansionError
from qccd.arch.edit import BUILD as BUILD_METHODS
from qccd.arch.edit import SEED as SEED_METHODS
from qccd.arch.edit import (EditError, apply_program, arch_fingerprint, canonical,
                            statement_vocabulary)
from qccd.arch.generators import expand_generator
from qccd.arch.listing import (architecture_diff, architecture_listing,
                              class_participants, template_records)
from qccd.api import DEFAULT_TEMPLATE, PROGRAM_METHODS
from qccd.arch.schema import SCHEMA_VERSION, export_schema, validate_document
from qccd.cost.hardware import hardware_report
from qccd.cost.models import corrected_model, deck_model
from qccd.verify import replay, verify
from qccd.viz.layout import compute_layout, pad_tiling, site_length
from qccd.viz.render import BROWSER_SET, build_view_model

ROOT = Path(__file__).resolve().parent.parent
ARCH_DIR = ROOT / "arch"
ENGINE = ROOT / "qccd" / "viz" / "engine.js"
EDIT_JS = ROOT / "qccd" / "viz" / "js" / "edit.js"
OUT_DIR = ROOT / "out"
RUNNER = Path(__file__).resolve().parent / "parity.mjs"

SEED = int(os.environ.get("QCCD_SEED", "0"))
N_FUZZ = int(os.environ.get("QCCD_FUZZ", "220"))
N_DRAG = int(os.environ.get("QCCD_DRAG", "60"))

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")

ARCH_FILES = sorted(ARCH_DIR.glob("*.arch.json"))


# --------------------------------------------------------------------------- helpers


def _wire(dev) -> dict:
    """The engine's device shape, straight off a Python `Device`."""
    from qccd.arch.edit import device_to_wire
    return device_to_wire(dev)


def _draw_shape(dev):
    """Exactly what `render.py` hands `compute_layout`: no adapter, nothing to get wrong."""
    nodes = [{"id": nid, "x": float(n.pos[0]), "y": float(n.pos[1])}
             for nid, n in dev.nodes.items()]
    segments = [{"id": sid, "a": s.ends[0], "b": s.ends[1]}
                for sid, s in dev.segments.items()]
    return nodes, segments


def _fuzz_params(rng) -> list[tuple[str, dict]]:
    """Seeded random parameter sets over ALL SIX generators, legal and illegal.

    Illegal ones matter as much as legal ones: a browser that silently accepts what the
    toolchain refuses would price an architecture that cannot be built.
    """
    out: list[tuple[str, dict]] = []
    for _ in range(N_FUZZ):
        which = rng.choice(["ring", "grid", "chain", "ladder", "racetrack", "dual_loop"])
        if which == "ring":
            w = rng.randint(2, 60)
            h = rng.randint(2, 12)
            cap = 2 * w + 2 * h - 4
            divisors = [d for d in range(1, cap + 1) if cap % d == 0]
            v = rng.choice(divisors + [0, 0, rng.randint(0, 40)])
            out.append(("ring", {"width": w, "height": h, "verticals": v}))
        elif which == "grid":
            out.append(("grid", {"a": rng.randint(1, 12), "b": rng.randint(1, 12)}))
        elif which == "chain":
            out.append(("chain", {"n": rng.randint(0, 300)}))
        elif which == "ladder":
            rungs = rng.choice([None, rng.randint(1, 6),
                                sorted({rng.randint(0, 20) for _ in range(4)})])
            out.append(("ladder", {"width": rng.randint(2, 80), "rungs": rungs,
                                   "highways": rng.choice([0, 1, 2, 3])}))
        elif which == "racetrack":
            out.append(("racetrack", {"straight": rng.randint(2, 90)}))
        else:
            out.append(("dual_loop", {"width": rng.randint(2, 60),
                                      "couplings": rng.choice([None, 0, rng.randint(1, 8)])}))
    # Adversarial: `isinstance(True, int)` is True in Python but `typeof true` is
    # 'boolean' in JS, so these two take DIFFERENT branches unless the port says so.
    out.append(("ladder", {"width": 8, "rungs": True, "highways": 1}))
    out.append(("dual_loop", {"width": 8, "couplings": True}))
    out.append(("ladder", {"width": 8, "rungs": False, "highways": 0}))
    # every precondition failure, by name
    out += [("ring", {"width": 1}), ("ring", {"width": 5, "height": 1}),
            ("ring", {"width": 6, "verticals": -1}), ("ring", {"width": 80, "verticals": 24}),
            ("grid", {"a": 1, "b": 4}), ("chain", {"n": 0}),
            ("ladder", {"width": 1}), ("ladder", {"width": 6, "highways": 3}),
            ("ladder", {"width": 6, "rungs": 0}), ("ladder", {"width": 4, "rungs": [9]}),
            ("dual_loop", {"width": 1}), ("nope", {"width": 4}),
            ("ring", {"width": 6, "nosuch": 1})]
    return out


def _adversarial_layouts(rng) -> list[dict]:
    """Devices designed to reach the branches a mirror silently diverges in.

    Dyadic lattices `i * 2**-e` force exact decimal ties, which is where Python's
    half-to-even `round()` and JS's half-away-from-zero `toFixed` part company.
    """
    out = []
    for e in range(4, 14):
        step = 2.0 ** -e
        nodes = [{"id": f"N{i}", "x": i * step, "y": (i % 3) * step} for i in range(24)]
        segs = [{"id": f"E{i}", "a": f"N{i}", "b": f"N{i+1}"} for i in range(23)]
        out.append({"name": f"adv:dyadic:2^-{e}", "nodes": nodes, "segments": segs})
    # single node; two coincident nodes; a diagonal (the isotropic fallback); exactly at
    # ISO_ASPECT; a degenerate y axis (the chain(n) bug); the PITCH_CAP binding
    out.append({"name": "adv:one", "nodes": [{"id": "A", "x": 0.0, "y": 0.0}], "segments": []})
    out.append({"name": "adv:coincident",
                "nodes": [{"id": "A", "x": 0.0, "y": 0.0}, {"id": "B", "x": 0.0, "y": 0.0},
                          {"id": "C", "x": 3.0, "y": 0.0}],
                "segments": [{"id": "E", "a": "A", "b": "C"}]})
    out.append({"name": "adv:diagonal",
                "nodes": [{"id": "A", "x": 0.0, "y": 0.0}, {"id": "B", "x": 4.0, "y": 1.0}],
                "segments": [{"id": "E", "a": "A", "b": "B"}]})
    out.append({"name": "adv:iso_aspect_exact",
                "nodes": [{"id": "A", "x": 0.0, "y": 0.0}, {"id": "B", "x": 2.0, "y": 0.0},
                          {"id": "C", "x": 0.0, "y": 1.0}, {"id": "D", "x": 2.0, "y": 1.0}],
                "segments": [{"id": "E0", "a": "A", "b": "B"}, {"id": "E1", "a": "C", "b": "D"}]})
    out.append({"name": "adv:flat_y",
                "nodes": [{"id": f"C{i}", "x": float(i), "y": 0.0} for i in range(9)],
                "segments": [{"id": f"E{i}", "a": f"C{i}", "b": f"C{i+1}"} for i in range(8)]})
    # a bow: a node sitting exactly ON a segment it does not touch, which is the
    # collinear branch whose tie-break a naive `sum()` port flipped the sign of
    out.append({"name": "adv:collinear_bow",
                "nodes": [{"id": "A", "x": 0.0, "y": 0.0}, {"id": "B", "x": 4.0, "y": 0.0},
                          {"id": "M", "x": 2.0, "y": 0.0}, {"id": "Z", "x": 2.0, "y": 4.0}],
                "segments": [{"id": "AB", "a": "A", "b": "B"}, {"id": "MZ", "a": "M", "b": "Z"}]})
    # over the 6,000-point `_sample` cap, where `g` becomes an approximation whose
    # reproducibility depends entirely on node order matching.  Keep this permanently.
    big = [{"id": f"B{i}", "x": (i % 97) + rng.random() * 0.4,
            "y": (i // 97) + rng.random() * 0.4} for i in range(6001)]
    out.append({"name": "adv:sample_cap:6001", "nodes": big, "segments": []})
    return out


def _drag_layouts(rng) -> list[dict]:
    """A shipped device with some nodes displaced by arbitrary doubles.

    This is what a DRAG actually produces -- not lattice points.  It is also the bucket
    that exercises `_bows`, which returns {} for seven of the nine shipped devices and is
    therefore barely covered by the shipped corpus alone.  Do not trim it.
    """
    out = []
    archs = [load(f) for f in ARCH_FILES]
    for k in range(N_DRAG):
        arch = archs[k % len(archs)]
        nodes, segments = _draw_shape(arch.device)
        nodes = [dict(n) for n in nodes]
        howmany = rng.randint(1, max(1, len(nodes) // 4))
        for _ in range(howmany):
            n = nodes[rng.randrange(len(nodes))]
            n["x"] = n["x"] + rng.uniform(-1.5, 1.5)
            n["y"] = n["y"] + rng.uniform(-1.5, 1.5)
        out.append({"name": f"drag:{arch.name}:{k}", "nodes": nodes, "segments": segments})
    return out


def _repr_cases(rng) -> list[dict]:
    """Random BIT PATTERNS, not pretty numbers.

    `String(v)` disagrees with `repr(v)` on about a tenth of all doubles, always in the
    framing rather than the digits.  Testing on round numbers would find none of it.
    """
    vals = [0.0, -0.0, 1.0, -1.0, 0.1, 0.1 + 0.2, 1e-7, 1e-5, 1e16, 1e17, 1e21, 1e-323,
            5e-324, 1.7976931348623157e308, 2.2250738585072014e-308, 1 / 3, 2 / 3,
            123456789012345.6, 1234567890123456.0, 0.0001, 0.00001, -2.5, 2.5, 1e15]
    for _ in range(4000):
        which = rng.randrange(4)
        if which == 0:
            v = rng.uniform(-1e3, 1e3)
        elif which == 1:
            v = rng.uniform(-1.5, 1.5) * 10 ** rng.randint(-20, 20)
        elif which == 2:
            v = float(rng.randint(-10 ** 6, 10 ** 6))
        else:
            import struct
            b = rng.getrandbits(64)
            v = struct.unpack("<d", struct.pack("<Q", b))[0]
            if not math.isfinite(v):
                v = rng.uniform(-1, 1)
        vals.append(v)
    return [{"v": v, "repr": repr(v)} for v in vals]


def _mark_cases(rng) -> list[dict]:
    """`site_length` and `pad_tiling` -- the two mirrors that were ALREADY in `render.py`
    and were covered by nothing.  The page computed `siteLen` inline with no `int()` where
    `site_length` does `int(cap or 0)`, and it tiled pads at `0.34*g` while `pad_tiling`
    derives `k` from `0.50*g`.  Folding them in is what makes this harness cover the parts
    that had already drifted."""
    out = []
    caps = [0, 1, 2, 3, 6, 7, 12, -1, None, 2.5, 1.9, 5.999, -0.5, 6.5, 3.0]
    for i in range(90):
        g = rng.choice([21.9155, 72.0, 5.5, 0.0, 1e-12, rng.uniform(0.5, 90.0)])
        cap = caps[i % len(caps)]
        length = rng.choice([0.0, 1e-12, g * 0.5, g, g * 7.5, rng.uniform(0, 400)])
        out.append({"name": f"marks:{i}", "cap": cap, "g": g, "length": length,
                    "site_length": site_length(cap, g),
                    "pad_tiling": list(pad_tiling(length, g))})
    return out


def _hardware_cases(rng) -> list[dict]:
    """The DAC / electrode / switch recount, on every shipped device and under every
    grouping.  Integer arithmetic, so this is exact equality with no float question."""
    out = []
    for f in ARCH_FILES:
        arch = load(f)
        rep = hardware_report(arch).as_dict()
        rep.pop("notes", None)
        rep.pop("name", None)
        out.append({"name": f"hw:{arch.name}", "device": _wire(arch.device),
                    "control": json.loads(json.dumps(arch.control)),
                    "budget": json.loads(json.dumps(arch.budget)), "report": rep})
        # and the same device re-wired every way, because `set_wiring` /`set_control` are
        # editable and the whole point of the WISE argument is the contrast between them
        from qccd.api import Machine
        for grouping in ("broadcast", "direct", "row", "column", "row_column"):
            m = Machine.load(f)
            if not m.arch.control.get("channels"):
                continue
            m.set_control(channels={"grouping": grouping})
            r2 = hardware_report(m.arch).as_dict()
            r2.pop("notes", None)
            r2.pop("name", None)
            out.append({"name": f"hw:{arch.name}:{grouping}", "device": _wire(m.arch.device),
                        "control": json.loads(json.dumps(m.arch.control)),
                        "budget": json.loads(json.dumps(m.arch.budget)), "report": r2})
    return out


#: The FOUR shapes `architecture_listing` emits, not the one.  `_program_cases` and
#: `_source_cases` used to build only `(mode="auto", template=None)`, which is the single
#: shape that already worked -- so `from_template`, `from_device` and the `d.build()` seal
#: were emitted by Python, replayed by Python, and never once handed to the JS.  Measured
#: on the shipped engine: 45 of 72 comparisons over these four shapes failed.
_LISTING_SHAPES = (
    ("full", {"mode": "full"}),
    ("template", {"mode": "auto"}),                  # `template` filled in per arch
    ("explicit", {"mode": "explicit"}),
    ("explicit+template", {"mode": "explicit"}),     # ditto
)


def _listing_shapes(arch, stem: str):
    """`(label, ArchListing)` for each of the four shapes, for one architecture."""
    for label, kw in _LISTING_SHAPES:
        opts = dict(kw)
        if "template" in label:
            opts["template"] = stem
        yield label, architecture_listing(arch, verify=False, **opts)


def _template_cases() -> dict:
    """`{stem: [call records]}` -- what `render.py` puts in the page's `D.templates`.

    A template is expressed as the PROGRAM that declares it, so the browser replays the
    identical records `apply_program` does rather than deserializing a second document
    format it would then have to be tested against.
    """
    out = {}
    for f in ARCH_FILES:
        stem = f.stem.replace(".arch", "")
        out[stem] = [json.loads(json.dumps(dict(r)))
                     for r in template_records(load(f))]
    return out


def _program_cases() -> list[dict]:
    """Layer L2: replay each shipped listing's `call` records through BOTH interpreters.

    Harvested from `ArchLine.call` ALONE, never reading `target` -- which is exactly the
    assertion that catches a geometry statement whose generator name lives only in
    `target`, the defect this editor was built on top of.
    """
    out = []
    for f in ARCH_FILES:
        arch = load(f)
        stem = f.stem.replace(".arch", "")
        for label, listing in _listing_shapes(arch, stem):
            calls = [json.loads(json.dumps(dict(l.call)))
                     for l in listing.lines if l.kind == "call" and l.call]
            machine = apply_program(calls)
            out.append({"name": f"prog:{arch.name}:{label}", "calls": calls,
                        "doc": json.loads(json.dumps(machine.arch.to_json(expanded=True)))})
    return out


def _source_cases() -> list[dict]:
    """Layer L4: `render(parse(x)) == x`, byte for byte, on every shipped listing.

    One assertion, and the editor's language can never drift from `_lit` / `_kwd`.
    """
    out = []
    for f in ARCH_FILES:
        arch = load(f)
        stem = f.stem.replace(".arch", "")
        for label, listing in _listing_shapes(arch, stem):
            out.append({"name": f"src:{arch.name}:{label}", "src": listing.python()})
    # every escape `_lit` can emit, through a real `describe()` on a real architecture, so
    # the COMPOSITION is covered and not only the two halves -- the writer and the reader
    # named different escape sets and `render(parse(x)) === x` was the assertion that
    # would have caught it
    arch = load(ARCH_FILES[0])
    for ch, tag in _ESCAPE_CHARS:
        from qccd.api import Machine

        m = Machine(load(ARCH_FILES[0]))
        m.describe(f"A{ch}B", note=f"x{ch}y")
        out.append({"name": f"src:esc:{tag}",
                    "src": architecture_listing(m.arch, verify=False).python()})
    return out


#: Every character `json.dumps` treats specially, plus the corpus's real non-ASCII and an
#: astral pair.  `\b` and `\f` are the two the JS writer had no short form for and the JS
#: reader silently mistranslated; `\x00`-`\x1f` cover the `\uXXXX` fallback either side.
_ESCAPE_CHARS = tuple(
    [(chr(c), f"u{c:04x}") for c in range(0x00, 0x21)]
    + [("\x7f", "u007f"), ('"', "quote"), ("'", "apos"), ("\\", "backslash"),
       ("/", "solidus"), ("§", "section"), ("–", "endash"),
       ("é", "eacute"), ("\U0001F300", "astral")]
)


def _string_cases() -> list[dict]:
    """Bucket 12: the writer IS `json.dumps`, the reader undoes the writer, and the
    re-emission is byte-exact.  All three edges, which is what makes it a round trip
    rather than two independent checks that can agree on a wrong answer."""
    out = []
    for ch, tag in _ESCAPE_CHARS:
        for pos, tmpl in (("mid", "A%sB"), ("only", "%s"), ("spaced", "x %s y")):
            s = tmpl % ch
            out.append({"name": f"str:{tag}:{pos}", "s": s,
                        "lit": json.dumps(s, ensure_ascii=False)})
    return out


def _class_cases() -> list[dict]:
    """Bucket 11: `class_participants`, compared ELEMENT BY ELEMENT IN ORDER.

    The existing `lint` bucket cannot cover this: it diffs `architectureViolations`
    (R11 only), and `lint()` itself reads only `classParticipants(...).length`, so the
    ORDER is compared nowhere and `Q.classParticipants` was exported and never called.

    The shipped corpus only reaches three of the five orbit branches (loop, "any", a
    segment label), so the probe classes are what make the bucket non-vacuous -- and two
    of the four defects here were CARDINALITY, not order, which means `lint()` already
    disagreed with Python's `Machine._orbit_warnings`.
    """
    out = []
    for f in ARCH_FILES:
        arch = load(f)
        calls = _calls_of(arch)
        for cid in sorted(arch.simd_classes):
            out.append({"name": f"cls:{arch.name}:{cid}", "calls": calls, "cls": cid,
                        "participants": list(class_participants(arch, cid))})
        dev = arch.device
        seg_labels = sorted({l for s in dev.segments.values() for l in s.labels})
        node_labels = sorted({l for n in dev.nodes.values() for l in n.labels})
        probes: list[tuple[str, dict]] = [("any", {"orbit": "any"})]
        if dev.loops:
            probes.append(("loop", {"orbit": sorted(dev.loops)[0]}))
        if seg_labels:
            probes.append(("seg", {"orbit": seg_labels[0]}))
            probes.append(("seg_plural", {"orbit": seg_labels[0] + "s"}))
        if node_labels:
            probes.append(("node", {"orbit": node_labels[0]}))
            probes.append(("node_plural", {"orbit": node_labels[0] + "s"}))
        probes.append(("nonsense", {"orbit": "zzz_no_such_label"}))
        probes.append(("none", {"orbit": None}))     # str(None) == "None": matches nothing
        probes.append(("missing", {}))               # spec.get("orbit", "any") == every site
        for tag, kw in probes:
            extra = calls + [{"method": "declare_class", "args": ["probe"],
                              "kwargs": dict(kw, type="shift")}]
            machine = apply_program(extra)
            out.append({"name": f"cls:{arch.name}:probe:{tag}", "calls": extra,
                        "cls": "probe",
                        "participants": list(class_participants(machine.arch, "probe"))})
    return out



def _calls_of(arch) -> list[dict]:
    """The architecture as the `{method, args, kwargs}` records both interpreters replay."""
    listing = architecture_listing(arch, verify=False)
    return [json.loads(json.dumps(dict(l.call)))
            for l in listing.lines if l.kind == "call" and l.call]


def _pricing_cases() -> list[dict]:
    """Layer L5: THE RE-PRICER.

    Coverage showed the whole pricing mirror -- `priceFrames`, `makeModel`, `pickPoint`
    and sixteen helpers -- was never executed by this harness, and two real defects had
    already shipped through that hole: steps 5,048 against Python's 5,444 (composition
    flattened from two levels to one) and peak n-bar 1,805 against 55.17 (lifetime deposit
    reported where the running accumulator was meant).  BOTH totals looked plausible.

    So compare every total, every quanta component, the transit count, the peak, and the
    PER-ION deposit -- the last because an error that cancels across ions is invisible in
    any sum.
    """
    from qccd.compile.programs import build as build_prog
    from qccd.cost import corrected_model, deck_model
    from qccd.verify import replay

    out = []
    combos = [
        ("ring144_24v", "rotate", corrected_model()),
        ("ring144_24v", "deck", deck_model()),
        # The corrected model over a programme WITH GATES. Coverage showed `scalar` was
        # called 0 times while `nonTransport` was called 792: every case took the
        # `kind === 'deck'` early return, so the corrected model's gate / cool / measure /
        # reset pricing had NEVER been checked against Python despite the comment saying
        # it was. The deck programme carries 396 gates, so this is the case that reaches it.
        ("ring144_24v", "deck", corrected_model()),
        ("cyclone_base", "oddeven", corrected_model()),
        ("ladder_2x72", "walk", corrected_model()),
        ("grid9x9", "walk", corrected_model()),
        ("h2_racetrack", "rotate", corrected_model()),
        ("deck_unit_cell", "walk", corrected_model()),
    ]
    for device, kind, model in combos:
        arch = load(ARCH_DIR / f"{device}.arch.json")
        prog = build_prog(arch, kind, 4) if kind == "walk" else build_prog(arch, kind)
        res = replay(prog, arch, model, check_rules=False)
        vm = build_view_model(arch, prog, res, model)
        out.append({
            "name": f"price:{device}:{kind}:{model.name}",
            "calls": _calls_of(arch),
            "frames": vm["program"]["frames"],
            "loops": vm["arch"]["loops"],
            "classes": vm.get("classes") or {},
            "model": {"kind": model.name,
                      "corner_hops": int(getattr(model, "corner_hops", 1)),
                      "junction_min_degree": int(getattr(model, "junction_min_degree", 3)),
                      "length_scaling": bool(getattr(model, "length_scaling", False)),
                      "pitch": float(getattr(model, "pitch", 1.0)),
                      # the anomalous-heating term the editor passes and this harness
                      # first omitted -- without it the mirror scored 0 where Python
                      # scored 25.92, which looked like mirror drift and was harness drift
                      "include_anomalous": bool(getattr(model, "include_anomalous", True)),
                      "anomalous_per_ms": float(vm["physics"].get("anomalous_per_ms", 0.0)),
                      "policy": {"table": getattr(getattr(model, "policy", None), "table",
                                                  "qccdsim_jones"),
                                 "objective": getattr(getattr(model, "policy", None),
                                                      "objective", "fastest")}},
            "totals": {"cost": res.total_cost, "steps": res.total_steps,
                       "us": res.total_us},
            "transits": sum(res.junction_transits.values()),
            "peak": res.peak_quanta,
            "comp": {k: float(v) for k, v in res.quanta_components.items()},
            # per ion, summed over components: the JS side tracks one lifetime
            # deposit per ion, Python keeps it split by component
            "per_ion": {k: float(sum(v.values()))
                        for k, v in res.per_ion_quanta.items()},
        })
    return out


#: The (architecture, program) pairs the pricing bucket already covers, reused so the rule
#: bucket judges the same replays the price bucket prices.
_RULE_COMBOS = [
    ("ring144_24v", "corrected", "rotate"),
    ("ring144_24v", "deck", "deck"),
    ("cyclone_base", "corrected", "oddeven"),
    ("ladder_2x72", "corrected", "walk"),
    ("grid9x9", "corrected", "walk"),
    ("h2_racetrack", "corrected", "rotate"),
    ("deck_unit_cell", "corrected", "walk"),
]


def program_for_arch(arch, builder):
    from qccd.compile.programs import build as build_prog
    return build_prog(arch, builder, 4) if builder == "walk" else build_prog(arch, builder)


def _mutate_cases(rng) -> list[dict]:
    """Layer L6: THE DRAG, and the two retunes beside it.

    `move_site` is literally the drag operation; `set_site_capacity` and
    `set_segment_length` are what an editor offers next to it.  All three were unexecuted.
    Diff the whole document, the layout (a drag exists to move geometry, and `g` sets every
    drawn mark) and the hardware recount.
    """
    out = []
    for f in ARCH_FILES:
        arch = load(f)
        calls = _calls_of(arch)
        sites = [n.id for n in arch.device.nodes.values() if n.kind == "site"]
        segs = [s.id for s in arch.device.segments.values()]
        if not sites or not segs:
            continue
        a, b = sites[0], sites[min(len(sites) - 1, 3)]
        seg = segs[0]
        # a drag lands on an arbitrary double, never a lattice point
        dx = round(rng.uniform(-1.5, 1.5), 12)
        dy = round(rng.uniform(-1.5, 1.5), 12)
        pos = arch.device.nodes[a].pos
        edits = [
            {"method": "move_site", "args": [a, float(pos[0]) + dx,
                                             float(pos[1] if len(pos) > 1 else 0.0) + dy],
             "kwargs": {}},
            {"method": "set_site_capacity", "args": [b, 5], "kwargs": {}},
            {"method": "set_segment_length", "args": [seg, 2.5], "kwargs": {}},
        ]
        rec = {"name": f"mut:{arch.name}", "calls": calls, "edits": edits}
        try:
            m = _replay(calls, edits)
        except Exception as exc:                       # noqa: BLE001
            rec["error"] = {"index": 0, "message": str(exc)}
            out.append(rec)
            continue
        rec["doc"] = json.loads(json.dumps(m.arch.to_json(expanded=True)))
        nodes, segments = _draw_shape(m.arch.device)
        rec["layout"] = compute_layout(nodes, segments)
        rec["hardware"] = _hw_dict(hardware_report(m.arch))
        out.append(rec)

        # and the refusals: an editor that accepts an illegal edit is worse than one that
        # cannot edit at all, so the two implementations must refuse the SAME things
        for bad, why in ((
            {"method": "set_site_capacity", "args": [b, 0], "kwargs": {}}, "capacity 0"),
            ({"method": "move_site", "args": ["NO_SUCH_NODE", 1.0, 1.0], "kwargs": {}},
             "unknown node"),
        ):
            try:
                _replay(calls, [bad])
            except Exception as exc:                   # noqa: BLE001
                out.append({"name": f"mut:{arch.name}:refuse:{why}", "calls": calls,
                            "edits": [bad],
                            "error": {"index": 0, "message": str(exc)}})
            else:
                # Python ACCEPTED it. That is itself a fact the mirror must match, so the
                # case still ships -- with no `error`, meaning both must accept.
                m2 = _replay(calls, [bad])
                out.append({"name": f"mut:{arch.name}:allows:{why}", "calls": calls,
                            "edits": [bad],
                            "doc": json.loads(json.dumps(
                                m2.arch.to_json(expanded=True)))})
    return out


def _replay(calls, edits=()):
    """Replay a command list and then some edits, returning the Machine.

    `apply_call` threads a `(machine, builder)` PAIR, not a Machine -- so the edits have
    to continue the same state the setup produced, or a `set_site_capacity` arrives with
    no geometry behind it.
    """
    from qccd.arch.edit import apply_call

    state = None
    for c in list(calls) + list(edits):
        state = apply_call(state, c)
    machine, _ = state
    return machine


def _hw_dict(rep) -> dict:
    return {"dacs": rep.dacs, "electrodes": rep.electrodes, "switches": rep.switches,
            "n_traps": rep.n_traps, "n_junctions": rep.n_junctions,
            "total_capacity": rep.total_capacity}


def _lint_cases() -> list[dict]:
    """Layer L7: the editor's only state-free legality check.

    Compare the whole ENUMERATION, not the verdict.  Reporting one violation where Python
    reports 28 still says "illegal", so a verdict-only comparison calls that agreement --
    and that exact drift is live today (R11 is emitted per junction NODE by Python and
    deduped by DEGREE in the mirror).
    """
    from qccd.verify.rules import architecture_violations

    out = []
    for f in ARCH_FILES:
        arch = load(f)
        base = _calls_of(arch)
        viol = architecture_violations(arch)
        out.append({
            "name": f"lint:{arch.name}",
            "calls": base,
            "violations": [{"rule": v.rule, "message": v.message} for v in viol],
        })

        # A HEALTHY architecture lints clean on both sides, so comparing only those is
        # vacuous agreement -- two empty lists.  The drift this bucket exists to catch
        # only appears once something is actually WRONG, so break each architecture in a
        # way the editor can genuinely produce and compare the full enumeration there.
        # Dropping the junction_cross degree curve makes every junction unpriceable, which
        # is exactly the R11 structural check.
        broken = [c for c in base
                  if not (c.get("method") == "set_degree_curve"
                          and c.get("args", [None])[0] == "junction_cross")]
        if len(broken) != len(base):
            try:
                m = _replay(broken)
            except Exception:                          # noqa: BLE001
                continue
            bviol = architecture_violations(m.arch)
            out.append({
                "name": f"lint:{arch.name}:no-junction-curve",
                "calls": broken,
                "violations": [{"rule": v.rule, "message": v.message} for v in bviol],
            })
    return out


# --------------------------------------------------------------------- build cases


#: The six named generators, so `DeviceBuilder("<gen>")` is exercised with a REAL name and
#: not only with the "explicit" default.  Measured: with only the fuzz corpus, planting
#: `args[0] === undefined ? 'explicit' : String(args[0])` -> `'explicit'` scored ZERO
#: divergences, because the fuzzer always named "explicit".  A corpus that never varies an
#: argument cannot test what reading it is for.
_GEN_NAMES = ("ring", "grid", "chain", "ladder", "racetrack", "dual_loop")

#: The zone types `DEFAULT_TEMPLATE` declares.  A device sealed with `from_device` takes
#: the template's zone types wholesale -- there is no `zones=` parameter -- so a fuzz
#: device that invents a zone name would be refused for a reason the fuzzer did not intend.
_TMPL_ZONES = ("data", "ancilla", "trap", "tfactory", "load")

N_BUILD_FUZZ = int(os.environ.get("QCCD_BUILD_FUZZ", "400"))

#: The twenty one-gesture mistakes a design tool actually produces.  Every one of these was
#: measured to be ACCEPTED by the browser and REFUSED by Python before the seal guard
#: landed; this is the only family that can ever see a missing refusal.
_BAD_FAMILIES = (
    "bad_node_id", "bad_seg_id", "dup_node_id", "dup_seg_id", "dangling_end",
    "loop_shuffle", "loop_unknown_node", "loop_kind", "self_loop", "parallel_seg",
    "neg_capacity", "zero_seg_capacity", "junction_zone", "junction_capacity",
    "no_zone_no_capacity", "bad_generator", "bad_name", "one_node_loop",
    "unknown_loop_ref", "loop_repeat",
)


def _C(m, *a, **k):
    return {"method": m, "args": list(a), "kwargs": dict(k)}


def _B(gen="explicit", **p):
    return _C("DeviceBuilder", gen, **p)


def _hand_build_shapes() -> list[tuple[str, list]]:
    """One named shape per STRUCTURAL IDEA, plus every default an optional argument has.

    The default-argument family is not decoration.  On the fuzz corpus alone, planting
    `d.loop`'s `closed` default and `DeviceBuilder`'s generator both scored 0 divergences,
    because the fuzzer always passed `closed=` explicitly and always named "explicit".
    Nine hand cases took them to 1 and 6.
    """
    out: list[tuple[str, list]] = []

    def case(name, calls):
        out.append((name, calls))

    case("one-site", [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
                      _C("blank_device", name="one", zones=["data"])])
    case("one-junction", [_B(), _C("d.junction", "J0", 0.0, 0.0),
                          _C("blank_device", name="onej")])
    case("two-site-path", [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
                           _C("d.site", "S1", 1.0, 0.0, zone="data"),
                           _C("d.segment", "E0", "S0", "S1", loop="L0", length=1.0),
                           _C("d.loop", "L0", ["S0", "S1"], closed=False, kind="path"),
                           _C("blank_device", name="chain2",
                              zones={"data": {"capacity": 2}})])
    case("three-site-path", [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
                             _C("d.site", "S1", 1.0, 0.0, zone="data"),
                             _C("d.site", "S2", 2.0, 0.0, zone="data"),
                             _C("d.segment", "E0", "S0", "S1", loop="L0"),
                             _C("d.segment", "E1", "S1", "S2", loop="L0"),
                             _C("d.loop", "L0", ["S0", "S1", "S2"], closed=False,
                                kind="path"),
                             _C("blank_device", name="chain3", zones=["data"])])
    tri = [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
           _C("d.site", "Bb", 1.0, 0.0, zone="data"),
           _C("d.site", "Cc", 0.5, 1.0, zone="data"),
           _C("d.segment", "E0", "A", "Bb", loop="L0"),
           _C("d.segment", "E1", "Bb", "Cc", loop="L0"),
           _C("d.segment", "E2", "Cc", "A", loop="L0")]
    case("triangle-ring", tri + [_C("d.loop", "L0", ["A", "Bb", "Cc"], closed=True,
                                    kind="ring"),
                                 _C("blank_device", name="tri", zones=["data"])])
    # `closed` and `kind` OMITTED: the defaults are True and "ring" and nothing else in
    # the corpus leaves them out.
    case("default-closed-triangle", tri + [_C("d.loop", "L0", ["A", "Bb", "Cc"]),
                                           _C("blank_device", name="tridef",
                                              zones=["data"])])
    case("default-kind-two-node",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", loop="L0"),
          _C("d.loop", "L0", ["A", "Bb"], closed=False),
          _C("blank_device", name="kdef", zones=["data"])])

    sq = [_B()]
    for i, (x, y) in enumerate([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]):
        sq.append(_C("d.site", "S%d" % i, x, y, zone="data"))
    for i in range(4):
        sq.append(_C("d.segment", "E%d" % i, "S%d" % i, "S%d" % ((i + 1) % 4), loop="L0"))
    sq.append(_C("d.loop", "L0", ["S0", "S1", "S2", "S3"], closed=True, kind="ring"))
    case("square-ring", sq + [_C("blank_device", name="sq", zones=["data"])])
    case("square-ring-inferred-zones", sq + [_C("blank_device", name="sqi")])

    case("ring-with-junction",
         [_B(), _C("d.junction", "J0", 0.0, 0.0),
          _C("d.site", "S1", 1.0, 0.0, zone="data"),
          _C("d.site", "S2", 1.0, 1.0, zone="data"),
          _C("d.site", "S3", 0.0, 1.0, zone="data"),
          _C("d.segment", "E0", "J0", "S1", loop="L0"),
          _C("d.segment", "E1", "S1", "S2", loop="L0"),
          _C("d.segment", "E2", "S2", "S3", loop="L0"),
          _C("d.segment", "E3", "S3", "J0", loop="L0"),
          _C("d.loop", "L0", ["J0", "S1", "S2", "S3"], closed=True, kind="ring"),
          _C("blank_device", name="ringj", zones=["data"])])
    case("two-islands",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.site", "Cc", 5.0, 0.0, zone="data"),
          _C("d.site", "Dd", 6.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb"), _C("d.segment", "E1", "Cc", "Dd"),
          _C("blank_device", name="islands", zones=["data"])])
    case("spur-off-ring",
         sq + [_C("d.site", "SP", 2.0, 0.5, zone="ancilla"),
               _C("d.segment", "SPUR", "S1", "SP", labels=["spur"]),
               _C("blank_device", name="spur",
                  zones={"data": {"capacity": 1}, "ancilla": {"capacity": 4}})])
    case("figure-eight-shared-node",
         [_B(),
          _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.site", "Cc", 2.0, 0.0, zone="data"),
          _C("d.site", "Dd", 1.0, 1.0, zone="data"),
          _C("d.site", "Ee", 1.0, -1.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", loop="L0"),
          _C("d.segment", "E1", "Bb", "Dd", loop="L0"),
          _C("d.segment", "E2", "Dd", "A", loop="L0"),
          _C("d.segment", "E3", "Bb", "Cc", loop="L1"),
          _C("d.segment", "E4", "Cc", "Ee", loop="L1"),
          _C("d.segment", "E5", "Ee", "Bb", loop="L1"),
          _C("d.loop", "L0", ["A", "Bb", "Dd"], closed=True, kind="ring"),
          _C("d.loop", "L1", ["Bb", "Cc", "Ee"], closed=True, kind="ring"),
          _C("blank_device", name="fig8", zones=["data"])])

    lad = [_B()]
    for i in range(3):
        lad.append(_C("d.site", "T%d" % i, float(i), 0.0, zone="data"))
    for i in range(3):
        lad.append(_C("d.site", "Bo%d" % i, float(i), 1.0, zone="data"))
    for i in range(2):
        lad.append(_C("d.segment", "TE%d" % i, "T%d" % i, "T%d" % (i + 1), loop="LT"))
        lad.append(_C("d.segment", "BE%d" % i, "Bo%d" % i, "Bo%d" % (i + 1), loop="LB"))
    for i in range(3):
        lad.append(_C("d.segment", "R%d" % i, "T%d" % i, "Bo%d" % i, labels=["rung"]))
    lad.append(_C("d.loop", "LT", ["T0", "T1", "T2"], closed=False, kind="path"))
    lad.append(_C("d.loop", "LB", ["Bo0", "Bo1", "Bo2"], closed=False, kind="path"))
    case("ladder-2x3", lad + [_C("blank_device", name="lad", zones=["data"])])

    case("explicit-capacities",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data", capacity=8),
          _C("d.site", "S1", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1"),
          _C("blank_device", name="caps", zones={"data": {"capacity": 2}})])
    # capacity=0 means INHERIT, not "explicitly zero" -- `capacity_explicit=bool(capacity)`
    case("capacity-zero-explicit-kw",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data", capacity=0),
          _C("d.site", "S1", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1"),
          _C("blank_device", name="capz", zones={"data": {"capacity": 3}})])
    # capacity OMITTED entirely -- the default is 0, i.e. inherit
    case("capacity-omitted",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("d.site", "S1", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1"),
          _C("blank_device", name="capo", zones={"data": {"capacity": 7}})])
    case("labels-and-lengths",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data", labels=["rail", "hot"]),
          _C("d.site", "S1", 3.5, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1", length=3.5, capacity=2, labels=["long"]),
          _C("blank_device", name="lab", zones=["data"])])
    case("loop-note",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", loop="L0"),
          _C("d.loop", "L0", ["A", "Bb"], closed=False, kind="path", note="the only rail"),
          _C("blank_device", name="ln", zones=["data"])])
    case("builder-params-provenance",
         [_B("explicit", straight=20, note="hand"),
          _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("blank_device", name="prov", zones=["data"])])
    for g in _GEN_NAMES:
        case("named-generator-" + g,
             [_B(g), _C("d.site", "S0", 0.0, 0.0, zone="data"),
              _C("d.site", "S1", 1.0, 0.0, zone="data"),
              _C("d.segment", "E0", "S0", "S1"),
              _C("blank_device", name="gen_" + g, zones=["data"])])
    case("multi-zone",
         [_B(), _C("d.site", "D0", 0.0, 0.0, zone="data"),
          _C("d.site", "A0", 1.0, 0.0, zone="ancilla"),
          _C("d.site", "L0s", 2.0, 0.0, zone="load"),
          _C("d.segment", "E0", "D0", "A0"), _C("d.segment", "E1", "A0", "L0s"),
          _C("blank_device", name="mz",
             zones={"data": {"capacity": 1}, "ancilla": {"capacity": 2},
                    "load": {"capacity": 16}})])
    case("negative-and-float-positions",
         [_B(), _C("d.site", "S0", -1.5, -2.25, zone="data"),
          _C("d.site", "S1", 0.0, -0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1", length=2.75),
          _C("blank_device", name="neg", zones=["data"])])
    case("zones-as-list",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("blank_device", name="zl", zones=["data", "ancilla"])])
    case("build-then-retune",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("d.site", "S1", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1", loop="L0"),
          _C("d.loop", "L0", ["S0", "S1"], closed=False, kind="path"),
          _C("blank_device", name="rt", zones=["data"]),
          _C("set_zone", "data", capacity=4),
          _C("set_site_capacity", "S0", 9),
          _C("set_segment_length", "E0", 2.5),
          _C("move_site", "S1", 3.0, 0.5),
          _C("describe", "a hand built rail", source="test")])
    case("from_device-template",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("d.site", "S1", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1", loop="L0"),
          _C("d.loop", "L0", ["S0", "S1"], closed=False, kind="path"),
          _C("from_device", name="fd", template=DEFAULT_TEMPLATE)])
    case("from_device-default-template",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("from_device", name="fdd")])
    case("rebuild-builder-twice",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _B(), _C("d.site", "T0", 5.0, 5.0, zone="data"),
          _C("blank_device", name="second", zones=["data"])])
    case("seed-then-more-builder-then-seed-again",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("blank_device", name="first", zones=["data"]),
          _C("d.site", "S1", 1.0, 0.0, zone="data"), _C("d.segment", "E0", "S0", "S1"),
          _C("blank_device", name="secnd", zones=["data"])])

    # -- the refusals a user WILL hit, by hand ------------------------------------
    case("FAIL-empty-device", [_B(), _C("blank_device", name="empty")])
    case("FAIL-blank_device-with-no-builder", [_C("blank_device", name="nobuilder")])
    case("FAIL-d.site-with-no-builder", [_C("d.site", "S0", 0.0, 0.0, zone="data")])
    case("FAIL-dup-node-id",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("d.site", "S0", 5.0, 5.0, zone="data"),
          _C("d.segment", "E0", "S0", "S1"),
          _C("blank_device", name="dup", zones=["data"])])
    case("FAIL-dup-segment-id",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.site", "Cc", 2.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb"), _C("d.segment", "E0", "Bb", "Cc"),
          _C("d.loop", "L0", ["A", "Bb", "Cc"], closed=False, kind="path"),
          _C("blank_device", name="dups", zones=["data"])])
    case("FAIL-segment-unknown-node",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("d.segment", "E0", "S0", "NOPE"),
          _C("blank_device", name="dangle", zones=["data"])])
    case("FAIL-loop-out-of-adjacency-order",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.site", "Cc", 2.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb"), _C("d.segment", "E1", "Bb", "Cc"),
          _C("d.loop", "L0", ["A", "Cc", "Bb"], closed=False, kind="path"),
          _C("blank_device", name="ord2", zones=["data"])])
    case("FAIL-unclosed-ring",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.site", "Cc", 2.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb"), _C("d.segment", "E1", "Bb", "Cc"),
          _C("d.loop", "L0", ["A", "Bb", "Cc"], closed=True, kind="ring"),
          _C("blank_device", name="unc", zones=["data"])])
    case("FAIL-loop-unknown-node",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb"),
          _C("d.loop", "L0", ["A", "Bb", "ZZ"], closed=False, kind="path"),
          _C("blank_device", name="lun", zones=["data"])])
    case("FAIL-one-node-loop",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.loop", "L0", ["A"], closed=False, kind="path"),
          _C("blank_device", name="l1", zones=["data"])])
    case("FAIL-loop-kind-chain",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", loop="L0"),
          _C("d.loop", "L0", ["A", "Bb"], closed=False, kind="chain"),
          _C("blank_device", name="lk", zones=["data"])])
    case("FAIL-loop-repeats-node",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", loop="L0"),
          _C("d.loop", "L0", ["A", "Bb", "A"], closed=False, kind="path"),
          _C("blank_device", name="lr", zones=["data"])])
    case("FAIL-segment-unknown-loop",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", loop="LX"),
          _C("blank_device", name="sl", zones=["data"])])
    case("FAIL-parallel-segments",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb"), _C("d.segment", "E1", "Bb", "A"),
          _C("blank_device", name="par", zones=["data"])])
    case("FAIL-self-loop-segment",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "A"),
          _C("blank_device", name="self", zones=["data"])])
    case("FAIL-site-no-zone-no-capacity",
         [_B(), _C("d.site", "S0", 0.0, 0.0), _C("blank_device", name="nz")])
    case("FAIL-zone-not-declared",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data", capacity=2),
          _C("blank_device", name="zn", zones=["ancilla"])])
    case("FAIL-zone-not-declared-from_device",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="qubit_farm", capacity=2),
          _C("from_device", name="zf", template=DEFAULT_TEMPLATE)])
    case("FAIL-bad-node-id",
         [_B(), _C("d.site", "0S", 0.0, 0.0, zone="data"),
          _C("blank_device", name="bid", zones=["data"])])
    case("FAIL-bad-segment-id",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0+x", "A", "Bb"),
          _C("blank_device", name="bsid", zones=["data"])])
    case("FAIL-bad-machine-name",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("blank_device", name="my device", zones=["data"])])
    case("FAIL-junction-with-capacity",
         [_B(), _C("d.junction", "J0", 0.0, 0.0, capacity=4),
          _C("blank_device", name="jc")])
    case("FAIL-junction-with-zone",
         [_B(), _C("d.junction", "J0", 0.0, 0.0, zone="data"),
          _C("blank_device", name="jz", zones=["data"])])
    case("FAIL-generator-not-in-enum",
         [_B("mygen"), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("blank_device", name="gen", zones=["data"])])
    case("FAIL-3d-position",
         [_B(), _C("d.site", "S0", 0.0, 0.0, 3.0, zone="data"),
          _C("blank_device", name="d3", zones=["data"])])
    case("FAIL-1d-position",
         [_B(), _C("d.site", "S0", 0.0, zone="data"),
          _C("blank_device", name="d1", zones=["data"])])
    case("FAIL-negative-capacity",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data", capacity=-2),
          _C("blank_device", name="negc", zones=["data"])])
    case("FAIL-zero-capacity-segment",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", capacity=0),
          _C("blank_device", name="zcap", zones=["data"])])
    case("FAIL-mutate-before-seed",
         [_B(), _C("d.site", "S0", 0.0, 0.0, zone="data"),
          _C("set_zone", "data", capacity=4)])
    case("FAIL-unknown-method", [_B(), _C("d.rail", "R0", 0.0, 0.0)])
    # accepted by BOTH, and worth pinning: the schema's segment length minimum is 0.0
    case("zero-length-segment",
         [_B(), _C("d.site", "A", 0.0, 0.0, zone="data"),
          _C("d.site", "Bb", 1.0, 0.0, zone="data"),
          _C("d.segment", "E0", "A", "Bb", length=0.0),
          _C("blank_device", name="zlen", zones=["data"])])
    return out


def _fuzz_build_shape(rng, i: int) -> list:
    """One random VALID device: a spanning tree, some chords, and a loop that is a walk.

    Valid BY CONSTRUCTION -- the loop is read off the tree so every consecutive pair has a
    real segment -- which is what makes the `bad:` family, which plants exactly one
    mutation into one of these, mean something.
    """
    n = rng.randint(1, 14)
    seal = rng.choice(("blank_device", "from_device"))
    zones = list(_TMPL_ZONES) if seal == "from_device" else \
        rng.sample(list(_TMPL_ZONES) + ["spine", "farm"], rng.randint(1, 3))
    calls = [_B(rng.choice(("explicit",) * 5 + _GEN_NAMES))]
    kinds, ids = [], []
    for k in range(n):
        nid = "N%d" % k
        ids.append(nid)
        junction = k > 0 and rng.random() < 0.20
        kinds.append("junction" if junction else "site")
        x = float(rng.randint(-6, 6))
        y = float(rng.randint(-6, 6)) + (0.5 if rng.random() < 0.3 else 0.0)
        kw: dict = {}
        if junction:
            if rng.random() < 0.3:
                kw["labels"] = ["j"]
            calls.append(_C("d.junction", nid, x + k * 0.001, y, **kw))
            continue
        kw["zone"] = rng.choice(zones)
        if rng.random() < 0.35:
            kw["capacity"] = rng.randint(1, 9)
        if rng.random() < 0.25:
            kw["labels"] = ["z%d" % rng.randint(0, 3)]
        calls.append(_C("d.site", nid, x + k * 0.001, y, **kw))
    # a spanning tree: node k attaches to a uniformly chosen earlier node
    parent = {}
    edges = []
    for k in range(1, n):
        j = rng.randrange(k)
        parent[ids[k]] = ids[j]
        edges.append((ids[k], ids[j]))
    pairs = {frozenset(e) for e in edges}
    for _ in range(rng.randint(0, 3)):
        if n < 3:
            break
        a, b = rng.sample(ids, 2)
        if frozenset((a, b)) in pairs:
            continue
        pairs.add(frozenset((a, b)))
        edges.append((a, b))
    # the loop: the tree path from the deepest node up to the root, which is a real walk
    loop_nodes: list[str] = []
    if n >= 2:
        cur = ids[n - 1]
        while cur is not None:
            loop_nodes.append(cur)
            cur = parent.get(cur)
        loop_nodes = loop_nodes[:rng.randint(2, len(loop_nodes))]
    loop_id = "L0" if loop_nodes else None
    on_loop = set()
    if loop_id:
        for a, b in zip(loop_nodes, loop_nodes[1:]):
            on_loop.add(frozenset((a, b)))
    for e, (a, b) in enumerate(edges):
        kw = {}
        if loop_id and frozenset((a, b)) in on_loop:
            kw["loop"] = loop_id
        if rng.random() < 0.4:
            kw["length"] = round(rng.uniform(0.25, 4.0), 6)
        if rng.random() < 0.2:
            kw["capacity"] = rng.randint(1, 4)
        if rng.random() < 0.2:
            kw["labels"] = ["e"]
        calls.append(_C("d.segment", "X%d" % e, a, b, **kw))
    if loop_id:
        kw = {"closed": False, "kind": "path"}
        if rng.random() < 0.25:
            kw["note"] = "fuzz %d" % i
        calls.append(_C("d.loop", loop_id, loop_nodes, **kw))
    if seal == "from_device":
        calls.append(_C("from_device", name="fz%d" % i, template=DEFAULT_TEMPLATE))
    else:
        zb = {z: {"capacity": rng.randint(1, 6)} for z in zones}
        calls.append(_C("blank_device", name="fz%d" % i,
                        **({"zones": zb} if rng.random() < 0.7 else {})))
    return calls


def _plant_bad(rng, calls: list, family: str) -> list | None:
    """Exactly one mutation, of one named family, into an otherwise valid shape."""
    out = [copy.deepcopy(c) for c in calls]
    nodes = [c for c in out if c["method"] in ("d.site", "d.junction")]
    sites = [c for c in out if c["method"] == "d.site"]
    juncs = [c for c in out if c["method"] == "d.junction"]
    segs = [c for c in out if c["method"] == "d.segment"]
    loops = [c for c in out if c["method"] == "d.loop"]
    seed = out[-1]

    if family == "bad_node_id" and nodes:
        rng.choice(nodes)["args"][0] = "0" + str(rng.randint(0, 9))
    elif family == "bad_seg_id" and segs:
        rng.choice(segs)["args"][0] = "E+" + str(rng.randint(0, 9))
    elif family == "dup_node_id" and len(nodes) >= 2:
        a, b = rng.sample(nodes, 2)
        b["args"][0] = a["args"][0]
    elif family == "dup_seg_id" and len(segs) >= 2:
        a, b = rng.sample(segs, 2)
        b["args"][0] = a["args"][0]
    elif family == "dangling_end" and segs:
        rng.choice(segs)["args"][rng.choice((1, 2))] = "NOPE"
    elif family == "loop_shuffle" and loops and len(loops[0]["args"][1]) >= 3:
        ns = loops[0]["args"][1]
        ns[0], ns[-1] = ns[-1], ns[0]
    elif family == "loop_unknown_node" and loops:
        loops[0]["args"][1].append("ZZ")
    elif family == "loop_kind" and loops:
        loops[0]["kwargs"]["kind"] = "chain"
    elif family == "self_loop" and segs:
        s = rng.choice(segs)
        s["args"][2] = s["args"][1]
    elif family == "parallel_seg" and segs:
        s = rng.choice(segs)
        out.insert(out.index(seed), _C("d.segment", "PAR", s["args"][2], s["args"][1]))
    elif family == "neg_capacity" and sites:
        rng.choice(sites)["kwargs"]["capacity"] = -rng.randint(1, 4)
    elif family == "zero_seg_capacity" and segs:
        rng.choice(segs)["kwargs"]["capacity"] = 0
    elif family == "junction_zone" and juncs:
        rng.choice(juncs)["kwargs"]["zone"] = "data"
    elif family == "junction_capacity" and juncs:
        rng.choice(juncs)["kwargs"]["capacity"] = 3
    elif family == "no_zone_no_capacity" and sites:
        s = rng.choice(sites)
        s["kwargs"].pop("zone", None)
        s["kwargs"].pop("capacity", None)
    elif family == "bad_generator":
        out[0]["args"] = ["mygen"]
    elif family == "bad_name":
        seed["kwargs"]["name"] = "my device"
    elif family == "one_node_loop" and loops:
        loops[0]["args"][1] = loops[0]["args"][1][:1]
    elif family == "unknown_loop_ref" and segs:
        rng.choice(segs)["kwargs"]["loop"] = "LX"
    elif family == "loop_repeat" and loops and loops[0]["args"][1]:
        loops[0]["args"][1].append(loops[0]["args"][1][0])
    else:
        return None
    return out


def _build_cases(rng) -> list[dict]:
    """Layer L8: the eight from-scratch builder verbs, under parity for the first time.

    Three families, and the third is the point.  `hand:` and `fuzz:` are devices Python
    ACCEPTS, and the browser already reproduced all 409 of them byte for byte -- so on
    their own this bucket would read as a clean pass over a surface with no checks in it
    at all.  `bad:` is 400 devices Python REFUSES, of which the browser built 400 before
    the seal guard landed.  A bucket that only compared accepted documents cannot see a
    missing refusal, and a missing refusal is how an unloadable file leaves the page.
    """
    cases: list[dict] = []
    for name, calls in _hand_build_shapes():
        cases.append({"name": "hand:" + name, "calls": calls})
    valid: list[list] = []
    for i in range(N_BUILD_FUZZ):
        calls = _fuzz_build_shape(rng, i)
        cases.append({"name": "fuzz:%d" % i, "calls": calls})
        valid.append(calls)
    per = max(1, N_BUILD_FUZZ // len(_BAD_FAMILIES))
    k = 0
    for family in _BAD_FAMILIES:
        made = 0
        tries = 0
        while made < per and tries < per * 12:
            tries += 1
            # rename BEFORE planting: the `bad_name` family plants an illegal machine
            # name, and renaming afterwards would silently repair it -- which is exactly
            # how a mutation family goes quietly vacuous.
            base = copy.deepcopy(valid[rng.randrange(len(valid))])
            base[-1]["kwargs"]["name"] = "bd%d" % k
            planted = _plant_bad(rng, base, family)
            if planted is None:
                continue
            cases.append({"name": "bad:%s:%d" % (family, made), "calls": planted})
            made += 1
            k += 1

    out = []
    for c in cases:
        rec = {"name": c["name"],
               "calls": json.loads(json.dumps(c["calls"]))}
        try:
            m = apply_program(rec["calls"])
            rec["doc"] = json.loads(json.dumps(m.arch.to_json(expanded=True)))
            rec["error"] = None
        except EditError as exc:
            rec["doc"] = None
            rec["error"] = {"code": exc.code, "method": exc.method,
                            "index": exc.index, "message": str(exc)}
        except Exception as exc:                       # noqa: BLE001
            rec["doc"] = None
            rec["error"] = {"code": type(exc).__name__, "method": "?",
                            "index": None, "message": str(exc)}
        out.append(rec)
    return out


# ---------------------------------------------------------------------- rule cases


#: Single-field architecture edits a designer makes in the existing forms, and the rule
#: each one is supposed to trip.  MEASURED yields on ring144_24v x deck:
#:   cap1     -> R1 = 1728        nogate  -> R6 = 864
#:   nocool   -> 0                segcap1 -> 0            bigchain -> 0
#: The zero rows are not filler: they are what proves the mirror does not INVENT a
#: violation, which is the other half of the same honesty claim.
_RULE_MUTATIONS = ("clean", "cap1", "nogate", "nocool", "segcap1", "bigchain")


def _mutate_arch(arch, kind):
    """Return call records that rebuild `arch` with one field changed."""
    calls = _calls_of(arch)
    if kind == "clean":
        return calls
    zones = sorted(arch.zone_types)
    sites = [n.id for n in arch.device.nodes.values() if n.kind == "site"]
    segs = [s.id for s in arch.device.segments.values()]
    extra = []
    if kind == "cap1":
        extra += [{"method": "set_zone", "args": [z], "kwargs": {"capacity": 1}}
                  for z in zones]
        extra += [{"method": "set_site_capacity", "args": [s, 1], "kwargs": {}}
                  for s in sites]
    elif kind == "nogate":
        extra += [{"method": "set_zone", "args": [z], "kwargs": {"gate": False}}
                  for z in zones]
    elif kind == "nocool":
        extra += [{"method": "set_zone", "args": [z], "kwargs": {"cool": False}}
                  for z in zones]
    elif kind == "segcap1":
        extra += [{"method": "set_segment_length", "args": [s, 1.0], "kwargs": {}}
                  for s in segs[:1]]
    elif kind == "bigchain":
        extra += [{"method": "set_zone", "args": [z], "kwargs": {"capacity": 40}}
                  for z in zones]
        extra += [{"method": "set_site_capacity", "args": [s, 40], "kwargs": {}}
                  for s in sites]
    return calls + extra


def _rule_hand_cases() -> list[dict]:
    """Four programmes written to FAIL, so the bucket cannot pass by comparing two empty
    lists.

    The shipped corpus trips R1 and R6 under an architecture edit and nothing else, so
    without these R7's quanta phase -- the one place a mirror can be wrong by exactly one
    cycle's background heating and stay invisible in every total -- would have no case at
    all.  Each is a real refusal with a real number: the worked example reaches a 2Q gate
    at n-bar 15.414 against a 1.0 budget.
    """
    from qccd.api import Machine

    out = []

    def case(name, mutate, build):
        m = Machine.ring(width=12, height=2, verticals=2, name="mini_ring",
                         template=DEFAULT_TEMPLATE)
        calls = _calls_of(m.arch) + list(mutate)
        arch = _replay(calls).arch
        mm = Machine(arch)
        p = mm.program("hand", provenance="off")
        build(p)
        model = corrected_model("qccdsim_jones")
        rep = verify(p.build(), arch, model)
        vm = build_view_model(arch, p.build(), rep.result, model, include_listing=False,
                              include_control=False, provenance="off")
        out.append({
            "name": f"rules:hand:{name}",
            "calls": calls,
            "frames": vm["program"]["frames"],
            "loops": vm["arch"]["loops"],
            "classes": vm["classes"],
            "physics": vm["physics"],
            "model": vm["model"],
            "zone_types": vm["arch"]["zone_types"],
            "max_simd": vm["program"]["max_simd_classes"],
            "models_heating": bool(model.models_heating),
            "browser_set": list(BROWSER_SET),
            "python": {r: n for r, n in rep.rules.by_rule().items() if r in BROWSER_SET},
            "messages": sorted(v.message for v in rep.rules.violations
                               if v.rule in BROWSER_SET),
        })

    # R7: a gate reached at n-bar 15.414 against ms_gate.max_quanta = 1.0
    def r7(p):
        p.init({"d0": "S3", "d1": "S6", "a0": "A0"})
        p.rotate(-3)
        p.simd("dock", [["d0", "S0", "A0", ["V0"]]])
        p.gate("MS", [["d0", "a0"]], sites=["A0"])
        p.cool()
        p.measure(["d0", "a0"])
    case("r7-hot-gate", [], r7)

    # the same programme with a cool BEFORE the gate: R7 must go silent, which is what
    # proves the rule is reading state and not the shape of the programme
    def r7ok(p):
        p.init({"d0": "S3", "d1": "S6", "a0": "A0"})
        p.rotate(-3)
        p.simd("dock", [["d0", "S0", "A0", ["V0"]]])
        p.cool()
        p.gate("MS", [["d0", "a0"]], sites=["A0"])
        p.measure(["d0", "a0"])
    case("r7-cooled-gate", [], r7ok)

    # R6: a gate on a zone whose `gate` flag is off
    def r6(p):
        p.init({"d0": "S0", "d1": "S1"})
        p.gate("MS", [["d0", "d1"]], sites=["S0"])
    case("r6-gate-off",
         [{"method": "set_zone", "args": ["data"], "kwargs": {"gate": False}},
          {"method": "set_zone", "args": ["ancilla"], "kwargs": {"gate": False}}], r6)

    # R6b + R12: two pairs in one trap, and a pair that is not co-located
    def r6b(p):
        p.init({"d0": "S0", "d1": "S1", "a0": "A0"})
        p.gate("MS", [["d0", "d1"]], sites=["S0"])
    case("r6b-not-colocated", [], r6b)

    # R7c: gates and no cooling anywhere
    def r7c(p):
        p.init({"d0": "A0", "a0": "A0"})
        p.gate("MS", [["d0", "a0"]], sites=["A0"])
    case("r7c-no-cooling", [], r7c)
    return out


def _rule_cases() -> list[dict]:
    """Layer L9: THE VERDICTS.

    `priceFrames` already replays the programme; this bucket asserts that the rule pass
    riding on that walk agrees with `qccd/verify/rules.py` -- not on the VERDICT but on
    the COUNT per rule AND the sorted multiset of message strings.

    Verdict-only comparison is exactly how `architectureViolations` shipped reporting 2
    where Python reported 77: both said "illegal", so a verdict comparison called it
    agreement.  Two independent prototypes of this mirror reported 864 where Python
    reported 1,728, both with the verdict identical -- once by checking only transport
    cycles, once by enumerating only the nodes an ion arrived at.  Counts see both.
    """
    from qccd.verify.rules import CYCLE_RULES

    out = []
    for f, kind, builder in _RULE_COMBOS:
        arch0 = load(ARCH_DIR / f"{f}.arch.json")
        for mut in _RULE_MUTATIONS:
            calls = _mutate_arch(arch0, mut)
            try:
                arch = _replay(calls).arch
            except Exception:                          # noqa: BLE001
                continue
            prog = program_for_arch(arch, builder)
            model = corrected_model("qccdsim_jones") if kind == "corrected" else deck_model()
            try:
                rep = verify(prog, arch, model)
            except Exception:                          # noqa: BLE001
                continue
            vm = build_view_model(arch, prog, rep.result, model, include_listing=False,
                                  include_control=False, provenance="off")
            want = {r: n for r, n in rep.rules.by_rule().items() if r in BROWSER_SET}
            msgs = sorted(v.message for v in rep.rules.violations if v.rule in BROWSER_SET)
            out.append({
                "name": f"rules:{arch0.name}:{builder}:{mut}",
                "calls": calls,
                "frames": vm["program"]["frames"],
                "loops": vm["arch"]["loops"],
                "classes": vm["classes"],
                "physics": vm["physics"],
                "model": vm["model"],
                "zone_types": vm["arch"]["zone_types"],
                "max_simd": vm["program"]["max_simd_classes"],
                "models_heating": bool(model.models_heating),
                "browser_set": list(BROWSER_SET),
                "python": want,
                "messages": msgs,
            })
    return out + _rule_hand_cases()

# ---------------------------------------------------------------------- program cases


def _P(m, *a, **k):
    return {"method": m, "args": list(a), "kwargs": dict(k)}


def _prog_scripts(arch):
    """One record list per structural idea, over whatever this device actually has.

    Every one of `qccd.api.PROGRAM_METHODS` appears at least once across the set, and
    `test_every_program_verb_is_exercised` asserts exactly that -- a verb the corpus never
    reaches is a verb whose mirror is untested however green the bucket looks.
    """
    dev = arch.device
    sites = [n.id for n in dev.nodes.values() if n.kind == "site"]
    loops = list(dev.loops)
    closed = [lid for lid, lp in dev.loops.items() if lp.closed]
    out: list[tuple[str, list]] = []
    if not sites:
        return out
    a, b = sites[0], sites[min(1, len(sites) - 1)]

    out.append(("init-gate-cool", [
        _P("init", {"d0": a, "d1": b}),
        _P("gate", "MS", [["d0", "d1"]], sites=[a]),
        _P("cool"),
        _P("measure", ["d0"]),
        _P("reset", ["d0"]),
        _P("barrier"),
        _P("claim", cost=0, steps=0),
    ]))
    if closed:
        lid = closed[0]
        out.append(("fill-rotate", [
            _P("fill", lid),
            _P("rotate", 1),
            _P("rotate", -2, lid),
            _P("rotate", 0, lid),
            _P("cool"),
        ]))
        out.append(("fill-prefix-and-named-class", [
            _P("fill", lid, "q"),
            _P("rotate", 3, lid, "rotate_cw"),
            _P("barrier"),
        ]))
        # the KEYWORD forms, so their defaults are testable: a corpus that always passes
        # an optional argument positionally cannot test the keyword branch, exactly as a
        # corpus that never omits one cannot test its default
        out.append(("fill-keyword-prefix", [
            _P("fill", loop=lid, prefix="anc"),
            _P("rotate", delta=-1, loop=lid),
            _P("cool"),
            _P("barrier"),
        ]))
    if loops and not closed:
        out.append(("fill-open-loop", [_P("fill", loops[0]), _P("cool")]))

    # a two-hop shuttle through whatever the device joins, plus the flat cycle form
    walk = _walk_of(dev, a, 3)
    if len(walk) >= 3:
        stops = [n for n in walk if dev.nodes[n].kind == "site"]
        if len(stops) >= 2:
            out.append(("shuttle-and-simd", [
                _P("init", {"d0": walk[0]}),
                _P("shuttle", "d0", walk),
                _P("cool"),
            ]))
    seg = next(iter(dev.segments.values()), None)
    if seg is not None:
        u, v = seg.ends
        out.append(("move-and-simd", [
            _P("init", {"d0": u}),
            _P("move", "d0", u, v, via=[seg.id]),
            _P("simd", "shuttle", [["d0", v, u, [seg.id]]]),
            _P("simd", "shuttle", [["d0", u, v]], "inter"),
            _P("cool"),
        ]))
    return out


def _walk_of(dev, start, k):
    """A short node walk out of `start`, following whatever segments exist."""
    inc: dict[str, list] = {}
    for s in dev.segments.values():
        inc.setdefault(s.ends[0], []).append(s)
        inc.setdefault(s.ends[1], []).append(s)
    walk, seen = [start], {start}
    node = start
    for _ in range(k):
        nxt = None
        for s in inc.get(node, ()):
            other = s.other(node)
            if other not in seen:
                nxt = other
                break
        if nxt is None:
            break
        walk.append(nxt)
        seen.add(nxt)
        node = nxt
    return walk


#: Tier-1 refusals: what `Program.<verb>` raises BEFORE any replay.  The browser must
#: reproduce the message text, because the page's standard is that its error strip says
#: what `python -m qccd` would say.
def _prog_refusals(arch):
    dev = arch.device
    sites = [n.id for n in dev.nodes.values() if n.kind == "site"]
    loops = list(dev.loops)
    a = sites[0]
    far = sorted(sites)[-1]
    out = [
        ("init-unknown-node", [_P("init", {"d0": "S99_NOPE"})]),
        ("rotate-unknown-loop", [_P("init", {"d0": a}), _P("rotate", 1, "NOPE_LOOP")]),
        ("fill-unknown-loop", [_P("fill", "NOPE_LOOP")]),
        ("bad-mode", [_P("init", {"d0": a}),
                      _P("simd", "shuttle", [["d0", a, a]], "sideways")]),
        ("empty-cycle", [_P("init", {"d0": a}), _P("simd", "shuttle", [])]),
    ]
    if len(sites) > 3:
        out.append(("shuttle-no-segment",
                    [_P("init", {"d0": a}), _P("shuttle", "d0", [a, far])]))
    open_loops = [lid for lid, lp in dev.loops.items() if not lp.closed]
    if open_loops:
        out.append(("rotate-open-loop",
                    [_P("fill", open_loops[0]), _P("rotate", 1, open_loops[0])]))
    return out


#: The frame fields an AUTHORED programme is responsible for.  `cost`, `steps` and `ctl`
#: come from Python's replay and a browser-authored programme has none of them -- that is
#: what `frameChecked === 0` means and the page says so rather than reporting drift zero.
PROG_FRAME_FIELDS = ["type", "cls", "mode", "place", "shift", "moves", "pairs", "sites",
                     "ions", "gate", "entails", "broadcast", "kind", "hops", "id"]


def _prog_cases() -> list[dict]:
    """Layer L10: THE PROGRAMME LANE.

    Diff FRAMES FIELD BY FIELD, not totals.  Measured: planting
    `cls: kw.cls || "rotate_cw"` in the rotate branch leaves every total, every per-ion
    quantum and `validateProgram` completely blind -- `rotate_cw` and `rotate_ccw` both
    declare `entails: ()` -- while Python's `MNEMONIC_BY_CLASS`, `prog.templates()` and
    `Instruction.cls` all differ.  A totals-only bucket would call that agreement.
    """
    from qccd.api import Machine

    out = []
    for stem in ("ring144_24v", "grid9x9", "cyclone_base", "stationary_chain"):
        f = ARCH_DIR / f"{stem}.arch.json"
        if not f.exists():
            continue
        arch = load(f)
        calls = _calls_of(arch)
        model = corrected_model("qccdsim_jones")
        dev_loops = {lid: list(lp.nodes) for lid, lp in arch.device.loops.items()}
        classes = {cid: dict(arch.simd_class(cid) or {}) for cid in arch.simd_classes}
        for name, records in _prog_scripts(arch):
            m = Machine(arch)
            p = m.program("bucket", provenance="off")
            try:
                p.apply_calls(records)
            except Exception as exc:                   # noqa: BLE001
                out.append({"name": f"prog:{stem}:{name}", "calls": calls,
                            "records": records, "loops": dev_loops, "classes": classes,
                            "arch_name": arch.name, "frames": None, "tsir": None,
                            "error": str(exc)})
                continue
            prog = p.build()
            res = replay(prog, arch, model, check_rules=False)
            vm = build_view_model(arch, prog, res, model, include_listing=False,
                                  include_control=False, provenance="off")
            out.append({
                "name": f"prog:{stem}:{name}",
                "calls": calls, "records": records, "loops": dev_loops,
                "classes": classes, "arch_name": arch.name,
                "frames": [{k: fr[k] for k in PROG_FRAME_FIELDS if k in fr}
                           for fr in vm["program"]["frames"]],
                "tsir": [i.to_json() for i in prog.instructions],
                "error": None,
            })
        for name, records in _prog_refusals(arch):
            m = Machine(arch)
            p = m.program("bucket", provenance="off")
            try:
                p.apply_calls(records)
            except Exception as exc:                   # noqa: BLE001
                msg = str(exc)
                # `apply_calls` prefixes the statement; the browser reports the statement
                # index separately, so compare the underlying sentence
                if "): " in msg:
                    msg = msg.split("): ", 1)[1]
                out.append({"name": f"prog:{stem}:FAIL-{name}", "calls": calls,
                            "records": records, "loops": dev_loops, "classes": classes,
                            "arch_name": arch.name, "frames": None, "tsir": None,
                            "error": msg})
            else:
                out.append({"name": f"prog:{stem}:ALLOW-{name}", "calls": calls,
                            "records": records, "loops": dev_loops, "classes": classes,
                            "arch_name": arch.name, "frames": None, "tsir": None,
                            "error": None, "accepted": True})
    return out


def _prog_text_cases() -> list[dict]:
    """`renderProgramSource(parse(src).prog) === src`, byte for byte.

    The same property the architecture lane already holds, over the SAME `lit`/`kwd`.  A
    programme that renders back differently is a programme the user cannot round-trip
    through the text pane, and the text pane is the only authoring surface there is.
    """
    from qccd.api import Machine

    out = []
    arch = load(ARCH_DIR / "ring144_24v.arch.json")
    for name, records in _prog_scripts(arch) + _prog_refusals(arch):
        out.append({"name": f"progtext:{name}", "records": records})
    return out

def build_cases(seed: int = SEED, engine: Path = ENGINE) -> dict:
    rng = random.Random(seed)
    cases: dict = {"engine": str(engine), "edit_js": str(EDIT_JS),
                   "generators": [], "layouts": []}

    # -- shipped devices, taken exactly as render.py builds the graph -------------------
    for f in ARCH_FILES:
        arch = load(f)
        nodes, segments = _draw_shape(arch.device)
        cases["layouts"].append({
            "name": f"ship:{arch.name}", "nodes": nodes, "segments": segments,
            "layout": compute_layout(nodes, segments),
        })

    # -- fuzzed generator parameters ---------------------------------------------------
    for i, (name, params) in enumerate(_fuzz_params(rng)):
        rec = {"name": f"fuzz:{name}:{i}", "generator": name, "params": params}
        try:
            dev = expand_generator(name, params, None)
        except ExpansionError as exc:
            rec["error"] = str(exc)
            rec["device"] = None
            cases["generators"].append(rec)
            continue
        rec["device"] = json.loads(json.dumps(_wire(dev)))
        nodes, segments = _draw_shape(dev)
        rec["layout"] = compute_layout(nodes, segments)
        rec["layout_raw"] = None       # raw mode is exercised on the explicit bucket
        cases["generators"].append(rec)

    # -- drags and adversarial lattices, both modes -------------------------------------
    for rec in _drag_layouts(rng) + _adversarial_layouts(rng):
        rec["layout"] = compute_layout(rec["nodes"], rec["segments"])
        cases["layouts"].append(rec)

    cases["marks"] = _mark_cases(rng)
    cases["reprs"] = _repr_cases(rng)
    cases["hardware"] = _hardware_cases(rng)
    cases["programs"] = _program_cases()
    cases["sources"] = _source_cases()
    # the surface a DRAG exercises -- absent until coverage showed 39 of engine.js's 155
    # functions were never executed by this harness
    cases["pricing"] = _pricing_cases()
    cases["mutate"] = _mutate_cases(rng)
    cases["lint"] = _lint_cases()
    cases["refusals"] = [
        # Every one of these is text a user could type, and each must be REFUSED
        # rather than silently reinterpreted.  `render()` provably emits only the
        # closed escape set `json.dumps` produces, so anything outside it is a
        # mistake -- and saying so beats guessing.  This bucket exists because V8
        # coverage showed `_perr`, the tokenizer's entire refusal path across 19
        # throw sites, was executed ZERO times: the round-trip bucket proved valid
        # text survives and nothing proved malformed text is caught.  That is the
        # half of the b/f-escape defect that actually bit -- an unknown escape used
        # to fall through to `out += e` and yield 'AbB' with no error at all.
        {'name': 'unknown-escape', 'src': 'm.describe("a\\qb")'},
        {'name': 'short-unicode', 'src': 'm.describe("a\\u12b")'},
        {'name': 'bad-unicode', 'src': 'm.describe("a\\uZZZZb")'},
        {'name': 'unterminated', 'src': 'm.describe("abc'},
        {'name': 'trailing-comma-args', 'src': 'm.set_zone("data",,)'},
        {'name': 'unclosed-paren', 'src': 'm.set_zone("data"'},
        {'name': 'not-a-verb', 'src': 'm.definitely_not_a_method(1)'},
        {'name': 'bare-word', 'src': 'hello world'},
    ]
    # the eight from-scratch builder verbs -- the largest never-executed surface
    # in engine.js until this bucket existed
    cases["build"] = _build_cases(rng)
    cases["build_vocabulary"] = {"build": sorted(BUILD_METHODS),
                                 "seed": sorted(SEED_METHODS)}
    # the verdicts: 17 of the 23 rules, re-derived client-side off the pricing walk
    cases["rules"] = _rule_cases()
    cases["browser_set"] = list(BROWSER_SET)
    # the programme lane: the twelve authoring verbs, lowered to frames and to TSIR
    cases["prog"] = _prog_cases()
    cases["progtext"] = _prog_text_cases()
    cases["prog_frame_fields"] = list(PROG_FRAME_FIELDS)
    cases["program_methods"] = list(PROGRAM_METHODS)
    # The schema travels with the corpus, straight out of `schema.py::export_schema()` --
    # the same call `render.py` puts in the page's data blob.  The engine has no copy.
    cases["schema_blob"] = export_schema()
    cases["schema_version"] = SCHEMA_VERSION
    cases["schema"] = _schema_cases(rng)
    # the template registry travels with the corpus, exactly as `render.py` puts it in the
    # page: `from_template` / `from_device` / `d.build()` are unexecutable without it
    cases["templates"] = _template_cases()
    cases["template_default"] = DEFAULT_TEMPLATE
    cases["classes"] = _class_cases()
    cases["strings"] = _string_cases()
    return cases


# ---------------------------------------------------------------------- schema cases

#: The ten families of one-token slip a hand-edited or browser-produced document actually
#: suffers.  Every family is applied at a RANDOM REACHABLE PLACE rather than at a listed
#: path, so the corpus exercises schema nodes nobody thought to name -- which is the whole
#: point of a walker mirror as opposed to a list of field checks.
_SCHEMA_MUTATIONS = ("bad_enum", "short_array", "wrong_type", "unknown_key",
                     "drop_required", "neg_number", "tiny_number", "big_number",
                     "bad_id", "bad_mapkey", "null_hole", "version")

N_SCHEMA_MUTANTS = 220


def _schema_slots(doc):
    """Every (container, key) a mutation can be planted at, with its JSON path."""
    out = []

    def rec(v, path, parent, key):
        out.append((parent, key, path, v))
        if isinstance(v, dict):
            for k, x in v.items():
                rec(x, f"{path}.{k}", v, k)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                rec(x, f"{path}[{i}]", v, i)

    rec(doc, "$", None, None)
    return out


def _schema_mutate(doc: dict, rng: random.Random):
    d = copy.deepcopy(doc)
    kind = rng.choice(_SCHEMA_MUTATIONS)
    slots = _schema_slots(d)

    def pick(pred):
        c = [t for t in slots if t[0] is not None and pred(t[3])]
        return rng.choice(c) if c else None

    if kind == "bad_enum":
        t = pick(lambda v: isinstance(v, str))
        if not t:
            return None, None
        t[0][t[1]] = "mytable"                       # the exact string DEFECT 3 used
    elif kind == "short_array":
        t = pick(lambda v: isinstance(v, list) and len(v) >= 1)
        if not t:
            return None, None
        t[0][t[1]] = t[0][t[1]][:1]                  # the exact shape DEFECT 2 produced
    elif kind == "wrong_type":
        t = pick(lambda v: isinstance(v, (int, float, str)) and not isinstance(v, bool))
        if not t:
            return None, None
        t[0][t[1]] = True if rng.random() < 0.5 else ["x"]
    elif kind == "unknown_key":
        t = pick(lambda v: isinstance(v, dict))
        tgt = d if t is None else t[0][t[1]]
        tgt["wobble"] = 1
    elif kind == "drop_required":
        t = pick(lambda v: isinstance(v, dict) and v)
        tgt = d if t is None else t[0][t[1]]
        del tgt[rng.choice(list(tgt))]
    elif kind == "neg_number":
        t = pick(lambda v: isinstance(v, (int, float)) and not isinstance(v, bool))
        if not t:
            return None, None
        t[0][t[1]] = -abs(t[0][t[1]]) - 1
    elif kind in ("tiny_number", "big_number"):
        # Both plant an out-of-range value in a slot that ALREADY holds a non-integral
        # float, which is how they stay in a `{"type": "number"}` slot.  Planting them in
        # an `{"type": "integer"}` slot would compare the two sides on a question JSON
        # cannot ask: Python distinguishes `3` from `3.0` and a JS mirror provably cannot,
        # so `-1.23e+20` reads "expected integer, got number" in Python and
        # "below the minimum 0" in JS -- a difference in the CORPUS, not in the mirror.
        # See `test_the_browser_never_writes_a_fraction_into_an_integer_slot` for the
        # containment that makes that asymmetry unreachable in practice.
        #
        # `tiny_number` is small enough that Python's `repr` uses a two-digit exponent
        # (`-1e-07`) where JS's `String` uses one (`-1e-7`); `big_number` is above `1e16`,
        # where `str(int(f))` and `repr(f)` disagree and `String` follows the wrong one.
        # Between them they are what makes the shared number formatter falsifiable.
        t = pick(lambda v: isinstance(v, float) and not v.is_integer())
        if not t:
            return None, None
        t[0][t[1]] = -1e-7 if kind == "tiny_number" else -1.2345678901234568e+20
    elif kind == "bad_id":
        t = pick(lambda v: isinstance(v, str))
        if not t:
            return None, None
        t[0][t[1]] = "9 bad id!"
    elif kind == "bad_mapkey":
        t = pick(lambda v: isinstance(v, dict) and v)
        tgt = d if t is None else t[0][t[1]]
        tgt["%%"] = rng.choice(list(tgt.values()))
    elif kind == "null_hole":
        t = pick(lambda v: True)
        if not t:
            return None, None
        t[0][t[1]] = None
    else:
        d["schema_version"] = "9.9"
    return d, kind


def _schema_cases(rng: random.Random) -> list[dict]:
    """Documents plus PYTHON'S verdict on each, computed live on every run.

    Both the shipped `.arch.json` files and their EXPANDED forms -- the expanded one is
    what the browser exports, so it is the shape the defect actually appeared in.
    """
    docs = []
    for f in ARCH_FILES:
        docs.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
        docs.append((f"{f.name}:expanded", load(f).to_json(expanded=True)))
    out = []
    for name, doc in docs:
        out.append({"name": name, "doc": doc, "errors": validate_document(doc)})
        for i in range(N_SCHEMA_MUTANTS):
            m, kind = _schema_mutate(doc, rng)
            if m is None:
                continue
            out.append({"name": f"{name}#mut{i}:{kind}", "doc": m,
                        "errors": validate_document(m)})
    return out


def _raw_variant(cases: dict) -> dict:
    """Add the UNQUANTIZED reference to every explicit layout case."""
    for rec in cases["layouts"]:
        rec["layout_raw"] = _raw_layout(rec["nodes"], rec["segments"])
    return cases


def _raw_layout(nodes, segments):
    """`compute_layout` with the OUTPUT quantizer bypassed -- and only that one.

    The internal quantizers (`_lattice_step`'s nd=9, `_bows`'s nd=3) stay, because they are
    load-bearing rather than cosmetic: `ux` feeds `PITCH_CAP / ux` and therefore `sx`, from
    which every mark descends.  The engine's `{raw: true}` means exactly the same thing; a
    `raw` that differed between the two sides would have the harness comparing two
    different questions, which is how this very assertion first failed.
    """
    return compute_layout(nodes, segments, raw=True)


def run_parity(cases: dict, tmp_path: Path, tag: str = "cases") -> dict:
    path = tmp_path / f"{tag}.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    out = subprocess.run([node, str(RUNNER), str(path)],
                         capture_output=True, text=True, timeout=900,
                         cwd=str(ROOT))
    assert out.returncode == 0, f"parity.mjs failed: {out.stderr[-3000:]}"
    line = out.stdout.strip().splitlines()[-1]
    return json.loads(line)


def _report(r: dict) -> str:
    """A failure message that localises the drift instead of dumping a blob."""
    rows = [f"{r['mismatched']} of {r['compared']} scalars differ; "
            f"tolerance is ZERO and that is the correct number.",
            f"worst (by ulp): {json.dumps(r.get('worst'), default=str)}",
            "shapes: " + ", ".join(f"{k} x{n}" for k, n in r.get("shapes", []))]
    for s in r.get("sample", []):
        rows.append(f"  {s['kind']:10s} {s['case']:28s} {s['key']:28s} "
                    f"py={s['py']} js={s['js']}")
    return chr(10).join(rows)


# --------------------------------------------------------------------------- the tests


@requires_node
def test_the_engine_is_bit_identical_to_python(tmp_path):
    """Every layout scalar, every generator field, every count: exact, tolerance ZERO."""
    cases = build_cases()
    # anti-vacuity, PER BUCKET: a bucket that silently stopped emitting cases reads as a
    # pass, and the global `compared` floor is far too coarse to notice one going empty.
    assert len(cases["programs"]) == 4 * len(ARCH_FILES), (
        "the four listing shapes are the point of this corpus")
    assert len(cases["templates"]) == len(ARCH_FILES)
    assert len(cases["classes"]) > 50 and len(cases["strings"]) > 100
    assert any(":probe:node_plural" in c["name"] for c in cases["classes"]), (
        "the node-label singular probe is gone; a whole orbit branch is uncovered")
    r = run_parity(cases, tmp_path)
    assert r.get("mismatched") == 0, _report(r)
    # anti-vacuity: a harness that compared nothing would also report 0 mismatches
    assert r["compared"] > 300000, r


@requires_node
def test_the_unquantized_intermediates_agree_too(tmp_path):
    """Quantization is a mask: rounding to 3-4 dp hides the great majority of real
    divergence, so a round-only harness would shrug at drift one edit away from visible."""
    cases = _raw_variant(build_cases())
    r = run_parity(cases, tmp_path, "raw")
    assert r.get("mismatched") == 0, _report(r)
    assert r["compared"] > 20000, r


#: Each entry is a plausible one-token slip, and the harness MUST report a mismatch for
#: every one.  Note what is deliberately absent: swapping the two `K_ANISO` clamp lines.
#: That looked like an obvious mutation, and it is provably a no-op -- once one clamp binds,
#: the other cannot -- so including it would have been a mutation guard that proves nothing.
#: Checked over 200,000 random (sx, sy) pairs before it was dropped.
@pytest.mark.parametrize("mutation,why", [
    ("0.55 * g", "the `_bows` clearance radius"),
    ("K_ION = GEOM.ION_D_FRAC_ACTIVE + 0.065", "the ion radius constant"),
    ("Math.floor(v * s + 0.5) / s", "the decimal quantizer"),
    ("set.add(_q(vals[i], 9))", "the internal lattice quantizer that `sx` descends from"),
    ("Math.min(Math.trunc(cap || 0), 6)",
     "the `int(cap or 0)` truncation in site_length, which render.py had already dropped"),
    ("segments['E' + prefix + x] = _seg('E' + prefix + x, prefix + x, prefix + (x + 1),\n"
     "                                        1.0, segCap, prefix, ['highway']);",
     "the ladder highway lane's `loop` field"),
])
@requires_node
def test_the_parity_harness_catches_a_planted_drift(tmp_path, mutation, why):
    """A parity test with no mutation guard is a golden vector wearing a disguise.

    Each mutation is a plausible one-token slip -- the exact shape of error that let a JS
    ion radius survive being multiplied by 2.5 -- and the harness must report a mismatch
    for every one of them.  If any of these passes, the harness has rotted into a no-op
    and the mirror is unguarded.
    """
    src = ENGINE.read_text(encoding="utf-8")
    assert mutation in src, f"the mutation anchor moved: {why}"
    if mutation == "0.55 * g":
        broken = src.replace("0.55 * g", "0.56 * g", 1)
    elif mutation.startswith("K_ION ="):
        broken = src.replace(mutation, "K_ION = GEOM.ION_D_FRAC_ACTIVE + 0.066", 1)
    elif mutation.startswith("Math.floor(v * s"):
        broken = src.replace(mutation, "Number(v.toFixed(nd))", 1)
    elif mutation.startswith("set.add("):
        broken = src.replace(mutation, "set.add(vals[i])", 1)
    elif mutation.startswith("Math.min(Math.trunc"):
        broken = src.replace(mutation, "Math.min(cap || 0, 6)", 1)
    else:
        broken = src.replace(mutation, mutation.replace(", prefix, ['highway']", ", null, ['highway']"), 1)
    assert broken != src
    hacked = tmp_path / "engine_broken.js"
    hacked.write_text(broken, encoding="utf-8")

    # BOTH modes, and the guard fires if EITHER does.  This is not belt-and-braces: one of
    # these mutations (dropping the nd=9 lattice quantizer) moves `sx` by about 1e-9, which
    # `round(sx, 4)` erases completely.  The quantized corpus reports a clean zero for it.
    # That is the concrete demonstration of why the raw mode exists -- a round-only harness
    # would certify an engine whose lattice arithmetic had silently stopped matching.
    caught = {}
    for tag, cases in (("quantized", build_cases(engine=hacked)),
                       ("raw", _raw_variant(build_cases(engine=hacked)))):
        cases["engine"] = str(hacked)
        r = run_parity(cases, tmp_path, "mut_" + tag)
        caught[tag] = r.get("mismatched", 0)
    assert caught["quantized"] > 0 or caught["raw"] > 0, (
        f"neither the quantized nor the unquantized corpus noticed a planted change to "
        f"{why}; the harness is not guarding anything. counts={caught}")


#: One-token slips in the three surfaces the classes and strings buckets exist to guard.
#: Each must be caught, and each is caught only because those buckets exist -- the
#: pre-existing corpus reports a clean zero for every one of them.
@pytest.mark.parametrize("anchor,replacement,why", [
    ("    return out.sort(_cmpStr);\n  }",
     "    return out;\n  }",
     "the sort on classParticipants' `any` branch (bucket 11)"),
    ("  var labels = [orbit, /s$/.test(orbit) ? orbit.slice(0, -1) : orbit];",
     "  var labels = [orbit, orbit];",
     "the singular spelling never reaching the NODE probe (bucket 11)"),
    ("  var orbit = own(cls, 'orbit') ? _pyStrOf(cls.orbit) : 'any';",
     "  var orbit = cls.orbit ? _pyStrOf(cls.orbit) : '';",
     "a class with no orbit key resolving to every site (bucket 11)"),
    ("'b': '\\b', 'f': '\\f',", "'f': '\\f',",
     "the reader's `\\b` escape (bucket 12, the READ half)"),
    ("    else if (c === '\\f') out += '\\\\f';\n", "",
     "the writer's `\\f` escape (bucket 12, the WRITE half)"),
    ("  if (method === 'from_template') {", "  if (false) {",
     "the from_template branch of renderStmt (bucket: text/listings)"),
])
@requires_node
def test_the_new_buckets_catch_a_planted_drift(tmp_path, anchor, replacement, why):
    """MUTATION GUARD for buckets 11, 12 and the four-shape listing corpus.

    Each of these anchors is a line that had a real defect in it, and the differential
    corpus as it stood before this change reported ZERO for every one -- which is why the
    buckets had to be added rather than the existing ones relied on.  A guard that only
    proved "some bucket fired" would not distinguish them, so the assertion also requires
    the mismatch to land in a bucket that did not exist before.
    """
    src = ENGINE.read_text(encoding="utf-8")
    assert src.count(anchor) == 1, f"the mutation anchor moved: {why}"
    hacked = tmp_path / "engine_broken.js"
    hacked.write_text(src.replace(anchor, replacement, 1), encoding="utf-8")

    cases = build_cases(engine=hacked)
    cases["engine"] = str(hacked)
    r = run_parity(cases, tmp_path, "newmut")
    assert r.get("mismatched", 0) > 0, (
        f"the corpus did not notice a planted change to {why}; that bucket is not "
        f"guarding anything")



# --------------------------------------------------------- the build bucket + its guard


def build_only_cases(engine: Path = ENGINE, edit_js: Path = EDIT_JS,
                     seed: int = SEED) -> dict:
    """Just bucket 14, so the mutation guard costs one corpus rather than fourteen."""
    rng = random.Random(seed)
    return {
        "engine": str(engine),
        "edit_js": str(edit_js),
        "schema_blob": export_schema(),
        "schema_version": SCHEMA_VERSION,
        "templates": _template_cases(),
        "template_default": DEFAULT_TEMPLATE,
        "build": _build_cases(rng),
        "build_vocabulary": {"build": sorted(BUILD_METHODS),
                             "seed": sorted(SEED_METHODS)},
    }


def _bucket_hits(r: dict, kind: str) -> int:
    """How many mismatches landed in one bucket.  `shapes` is `[kind|key, count]`."""
    return sum(n for sig, n in r.get("shapes", []) if sig.split("|", 1)[0] == kind)


@requires_node
def test_the_build_bucket_is_not_vacuous(tmp_path):
    """ONE BUCKET, BOTH SIGNS.  A build corpus that quietly stopped emitting refusals --
    or stopped emitting acceptances -- would read as a clean pass, and the refusal half is
    the ONLY half that can ever see a missing refusal."""
    cases = build_only_cases()
    built = [c for c in cases["build"] if c["error"] is None]
    refused = [c for c in cases["build"] if c["error"]]
    assert len(built) > 380, f"only {len(built)} devices Python accepts"
    assert len(refused) > 380, f"only {len(refused)} devices Python refuses"
    # every planted family must actually have planted something Python refuses; a family
    # whose mutation silently did not apply would shrink the refusal half without notice
    for family in _BAD_FAMILIES:
        got = [c for c in refused if c["name"].startswith("bad:" + family + ":")]
        assert got, f"the {family!r} mutation family produced nothing Python refuses"
    r = run_parity(cases, tmp_path, "build")
    assert r.get("mismatched") == 0, _report(r)
    assert r["compared"] > 2000, r


def test_every_build_verb_is_actually_exercised():
    """The eight from-scratch verbs, named, so a corpus that stopped reaching one says so."""
    rng = random.Random(SEED)
    used = {c["method"] for case in _build_cases(rng) for c in case["calls"]}
    missing = set(BUILD_METHODS) - used
    assert not missing, sorted(missing)
    assert {"blank_device", "from_device"} <= used


@requires_node
def test_the_js_advertises_the_python_build_vocabulary(tmp_path):
    """Derived from the dispatcher on BOTH sides, so drift is impossible by construction."""
    assert set(_js_eval(tmp_path, "QCCD.BUILD_METHODS")) == set(BUILD_METHODS)
    assert set(_js_eval(tmp_path, "QCCD.SEED_METHODS")) == set(SEED_METHODS)


#: THE MUTATION GUARD for bucket 14.  Six one-token slips on the ACCEPTED half and one on
#: the REFUSED half.  Two of the six -- `d.loop`'s `closed` default and `DeviceBuilder`'s
#: generator -- scored ZERO divergences across 400 random devices before the hand family
#: was added, because the fuzzer always passed `closed=` explicitly and always named
#: "explicit".  A corpus that never omits an optional argument cannot test its default,
#: which is why the default-argument and named-generator families are part of the spec and
#: not decoration.  The seventh is the one that would have caught the state this bucket
#: was written for: with the seal deleted the browser builds every device Python refuses,
#: and every numeric bucket stays green.
_BUILD_MUTATIONS = [
    ("kw.length === undefined ? 1.0 : _finite(kw.length, 'a segment length', 'd.segment')",
     "Math.trunc(kw.length === undefined ? 1.0 "
     ": _finite(kw.length, 'a segment length', 'd.segment'))",
     "d.segment's declared length"),
    ("kw.closed === undefined ? true : !!kw.closed", "false",
     "d.loop's `closed` DEFAULT (invisible to a corpus that always passes it)"),
    ("kw.capacity === undefined ? 0\n"
     "                  : Math.trunc(_finite(kw.capacity, 'a site capacity', 'd.' + kind))",
     "kw.capacity === undefined ? 1\n"
     "                  : Math.trunc(_finite(kw.capacity, 'a site capacity', 'd.' + kind))",
     "d.site's `capacity` default, i.e. inherit-from-zone"),
    ("args[0] === undefined ? 'explicit' : String(args[0])", "'explicit'",
     "DeviceBuilder's generator name (invisible to a corpus that always names one)"),
    ("_zoneBlock(kw.zones || _zonesOfDevice(st.builder))", "_zoneBlock(kw.zones || [])",
     "blank_device's zone inference from the device"),
    ("kw.kind === undefined ? 'ring' : String(kw.kind)", "'ring'",
     "d.loop's `kind`"),
]


@pytest.mark.parametrize("anchor,replacement,why", _BUILD_MUTATIONS)
@requires_node
def test_the_build_bucket_catches_a_planted_drift(tmp_path, anchor, replacement, why):
    """Each mutation must be caught, and caught BY BUCKET 14 rather than by a neighbour."""
    src = ENGINE.read_text(encoding="utf-8")
    assert src.count(anchor) == 1, f"the mutation anchor moved: {why}"
    hacked = tmp_path / "engine_broken.js"
    hacked.write_text(src.replace(anchor, replacement, 1), encoding="utf-8")
    r = run_parity(build_only_cases(engine=hacked), tmp_path, "buildmut")
    assert r.get("mismatched", 0) > 0, (
        f"the build corpus did not notice a planted change to {why}; it is not guarding it")
    assert _bucket_hits(r, "build") > 0, (
        f"something mismatched but not in the build bucket, so the guard proves nothing "
        f"about {why}: {r.get('shapes')}")


@requires_node
def test_deleting_the_seal_makes_the_refusal_half_go_red(tmp_path):
    """THE GUARD THAT WOULD HAVE CAUGHT TODAY'S STATE.

    Before `_sealDevice` existed, `blank_device` and `from_device` ran no schema check, no
    incidence check and no `check_structure` -- so the browser built 403 of 403 devices
    Python refuses, and every numeric bucket stayed green.  Delete the seal and the `bad:`
    family must go from zero divergences to hundreds; without this a future refactor could
    quietly drop it again and nothing would notice.
    """
    src = ENGINE.read_text(encoding="utf-8")
    anchor = """    return _sealDevice(resolveCapacities(fresh),
                       kw.name === undefined ? 'custom' : String(kw.name));"""
    assert src.count(anchor) == 2, "the two seal call sites moved"
    hacked = tmp_path / "engine_unsealed.js"
    hacked.write_text(src.replace(anchor, "    return resolveCapacities(fresh);"),
                      encoding="utf-8")
    r = run_parity(build_only_cases(engine=hacked), tmp_path, "unsealed")
    # MEASURED: 334 of the 382 refusals go dark.  The other 48 are refused before the seal
    # ever runs -- by `d.junction`'s kwarg check, by the coordinate-arity check, by
    # `no_builder`, by the seeded-state check and by the unknown-verb dispatcher -- so the
    # floor is stated as what the seal is actually responsible for, not as the whole half.
    assert _bucket_hits(r, "build") > 300, (
        f"deleting the seal cost only {_bucket_hits(r, 'build')} divergences; the refusal "
        f"half of the build bucket is not doing its job: {r.get('shapes')}")


def _js_eval(tmp_path: Path, expr: str):
    """Ask the engine directly, the way `test_advertised_methods_are_dispatchable` does."""
    script = tmp_path / "probe.mjs"
    script.write_text(
        "import fs from 'fs';\n"
        f"new Function(fs.readFileSync({json.dumps(str(EDIT_JS))}, 'utf8'))();\n"
        f"new Function(fs.readFileSync({json.dumps(str(ENGINE))}, 'utf8'))();\n"
        f"process.stdout.write(JSON.stringify({expr}));\n", encoding="utf-8")
    out = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=120,
                         cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


# --------------------------------------------------------- the rules bucket + its guard


def rules_only_cases(engine: Path = ENGINE, edit_js: Path = EDIT_JS) -> dict:
    """Just bucket 15, so the mutation guard costs one corpus rather than fifteen."""
    return {
        "engine": str(engine),
        "edit_js": str(edit_js),
        "schema_blob": export_schema(),
        "schema_version": SCHEMA_VERSION,
        "templates": _template_cases(),
        "template_default": DEFAULT_TEMPLATE,
        "rules": _rule_cases(),
        "browser_set": list(BROWSER_SET),
    }


@requires_node
def test_the_rules_bucket_is_not_vacuous(tmp_path):
    """A corpus of clean architectures compares two empty lists and calls that agreement.

    Every rule the browser mirrors must have at least one case where PYTHON reports a
    violation, or that rule is untested however green the bucket looks.
    """
    cases = rules_only_cases()
    tot: dict[str, int] = {}
    for c in cases["rules"]:
        for r, n in c["python"].items():
            tot[r] = tot.get(r, 0) + n
    for rule in ("R1", "R6", "R6b", "R7", "R7c"):
        assert tot.get(rule, 0) > 0, f"no case in the corpus trips {rule}: {tot}"
    assert sum(tot.values()) > 3000, tot
    assert any(not c["python"] for c in cases["rules"]), (
        "every case fails something; a mirror that reported a violation for EVERY cycle "
        "would pass, and the clean cases are what refuse that")
    r = run_parity(cases, tmp_path, "rules")
    assert r.get("mismatched") == 0, _report(r)
    assert r["compared"] > 3000, r


#: THE MUTATION GUARDS for bucket 15.  The first two are the defects a prototype of this
#: mirror actually had -- BOTH reported 864 where Python reported 1,728, and BOTH kept the
#: verdict identical, which is why the bucket compares counts and message multisets.
_RULE_MUTATIONS_JS = [
    ("    if (!onCycle) return;\n    w.f = f; w.id = fid;",
     "    if (!onCycle || type !== 'simd') return;\n    w.f = f; w.id = fid;",
     "judging TRANSPORT CYCLES ONLY -- Python builds a CycleView for every instruction, "
     "so a gate-only programme would report 'R1 passed'"),
    ("w.over = nOver ? Object.keys(over) : NONE;",
     "w.over = nOver ? Object.keys(over).filter(function (n) { "
     "return (occBefore[n] || 0) < (occ[n] || 0); }) : NONE;",
     "enumerating only the nodes that GAINED an ion this cycle -- Python re-reports a "
     "node every cycle it stays over capacity, whether or not anything moved there"),
    ("if (v > c) { if (!was) { over[n] = true; nOver++; } }",
     "if (v >= c) { if (!was) { over[n] = true; nOver++; } }",
     "R1's comparison, `>` for `>=`"),
    ("qStart === null ? q : qStart",
     "q",
     "R7's QUANTA PHASE -- reading `q` after this cycle's own anomalous term instead of "
     "the snapshot taken before it"),
    ("  return !!z[capability];",
     "  return true;",
     "R6's capability lookup"),
    ("if (n > budget) {",
     "if (n > budget * 1000) {",
     "R7's budget comparison"),
]


@pytest.mark.parametrize("anchor,replacement,why", _RULE_MUTATIONS_JS)
@requires_node
def test_the_rules_bucket_catches_a_planted_drift(tmp_path, anchor, replacement, why):
    src = ENGINE.read_text(encoding="utf-8")
    assert src.count(anchor) == 1, f"the mutation anchor moved: {why}"
    hacked = tmp_path / "engine_broken.js"
    hacked.write_text(src.replace(anchor, replacement, 1), encoding="utf-8")
    r = run_parity(rules_only_cases(engine=hacked), tmp_path, "rulesmut")
    assert r.get("mismatched", 0) > 0, (
        f"the rules corpus did not notice a planted change to {why}; it is not guarding it")
    assert _bucket_hits(r, "rules") > 0, (
        f"something mismatched but not in the rules bucket, so the guard proves nothing "
        f"about {why}: {r.get('shapes')}")


@requires_node
def test_the_browser_rule_set_is_derived_on_both_sides(tmp_path):
    """`BROWSER_SET` in Python and `mirroredRules()` in JS must be the same 17 rules.

    The JS side is derived from `RULE_FNS` plus the three checked-by-construction rules,
    so a rule that is advertised-but-undispatchable is impossible there; this is what
    stops the PYTHON side shipping a `rule_checksum` entry for a rule nothing checks.
    """
    assert set(_js_eval(tmp_path, "QCCD.MIRRORED_RULES")) == set(BROWSER_SET)
    from qccd.verify.rules import RULE_STATEMENTS
    assert set(BROWSER_SET) <= set(RULE_STATEMENTS), (
        "the browser claims a rule qccd.verify.rules does not define")


# ------------------------------------------------------- the prog bucket + its guard


def prog_only_cases(engine: Path = ENGINE, edit_js: Path = EDIT_JS) -> dict:
    """Just bucket 16, so the mutation guard costs one corpus rather than sixteen."""
    return {
        "engine": str(engine),
        "edit_js": str(edit_js),
        "schema_blob": export_schema(),
        "schema_version": SCHEMA_VERSION,
        "templates": _template_cases(),
        "template_default": DEFAULT_TEMPLATE,
        "prog": _prog_cases(),
        "progtext": _prog_text_cases(),
        "prog_frame_fields": list(PROG_FRAME_FIELDS),
        "program_methods": list(PROGRAM_METHODS),
    }


def test_every_program_verb_is_exercised():
    """All twelve authoring verbs, named, so a corpus that stopped reaching one says so."""
    used = {r["method"] for c in _prog_cases() for r in c["records"]}
    missing = set(PROGRAM_METHODS) - used
    assert not missing, sorted(missing)


@requires_node
def test_the_prog_bucket_is_not_vacuous(tmp_path):
    """BOTH SIGNS.  Programmes Python lowers AND programmes Python refuses before any
    replay -- the second half is where the browser's error strip either says what
    `python -m qccd` says or says something else."""
    cases = prog_only_cases()
    built = [c for c in cases["prog"] if c["error"] is None]
    refused = [c for c in cases["prog"] if c["error"]]
    assert len(built) >= 12, len(built)
    assert len(refused) >= 15, len(refused)
    n_frames = sum(len(c["frames"] or ()) for c in built)
    assert n_frames > 80, f"the corpus lowers almost nothing ({n_frames} frames)"
    r = run_parity(cases, tmp_path, "prog")
    assert r.get("mismatched") == 0, _report(r)
    assert r["compared"] > 600, r


#: THE MUTATION GUARD for bucket 16.  The first is the one that matters most and the one
#: that motivates diffing frames rather than totals: planting it was MEASURED to leave
#: cost, steps, us, every per-ion quantum AND `validateProgram` completely unchanged,
#: because `rotate_cw` and `rotate_ccw` both declare `entails: ()`.
_PROG_MUTATIONS = [
    ("cls = (cls === null || cls === undefined) ? (delta >= 0 ? 'rotate_cw' : 'rotate_ccw')",
     "cls = (cls === null || cls === undefined) ? 'rotate_cw'",
     "the rotation DIRECTION, which lives in the class name and nowhere else -- invisible "
     "to every total and to validateProgram"),
    ("broadcast: !list.length", "broadcast: false",
     "`cool()` with no ions is a BROADCAST (R7c reads it)"),
    ("_emitFrame(ctx, { type: 'gate', cls: null, mode: 'intra', pairs: pp,",
     "_emitFrame(ctx, { type: 'gate', cls: null, mode: null, pairs: pp,",
     "a gate's mode, which R4b judges"),
    ("for (var i = 0; i < nodes.length; i++) place[prefix + i] = nodes[i];",
     "for (var i = 0; i < nodes.length; i++) place['d' + i] = nodes[i];",
     "`fill(loop, prefix)`'s prefix"),
    ("if (s.a === node) node = s.b;", "if (false) node = s.b;",
     "the `via` -> node-path walk, which is what the cost model charges"),
    ("kw.prefix === undefined ? 'd' : String(kw.prefix)", "'d'",
     "fill's keyword prefix"),
]


@pytest.mark.parametrize("anchor,replacement,why", _PROG_MUTATIONS)
@requires_node
def test_the_prog_bucket_catches_a_planted_drift(tmp_path, anchor, replacement, why):
    src = ENGINE.read_text(encoding="utf-8")
    assert src.count(anchor) == 1, f"the mutation anchor moved: {why}"
    hacked = tmp_path / "engine_broken.js"
    hacked.write_text(src.replace(anchor, replacement, 1), encoding="utf-8")
    r = run_parity(prog_only_cases(engine=hacked), tmp_path, "progmut")
    assert r.get("mismatched", 0) > 0, (
        f"the prog corpus did not notice a planted change to {why}")
    assert _bucket_hits(r, "prog") + _bucket_hits(r, "progtext") > 0, (
        f"something mismatched but not in the programme bucket: {r.get('shapes')}")


@requires_node
def test_deleting_the_program_receiver_breaks_the_text_lane(tmp_path):
    """THE TEXT-LANE GUARD.  Remove the `p.` production from `_parseStatement` and every
    emitted programme listing stops parsing -- which the round trip must report as a PARSE
    ERROR rather than as a byte diff between two empty statement lists."""
    src = ENGINE.read_text(encoding="utf-8")
    anchor = "  if (_isName(p, 'p') && _peekOp(p, 1) === '.') {"
    assert src.count(anchor) == 1, "the `p.` production moved"
    hacked = tmp_path / "engine_noprog.js"
    hacked.write_text(src.replace(anchor, "  if (false) {", 1), encoding="utf-8")
    r = run_parity(prog_only_cases(engine=hacked), tmp_path, "progtextmut")
    assert _bucket_hits(r, "progtext") > 0, (
        f"the text lane parsed a programme with no `p.` receiver: {r.get('shapes')}")

@requires_node
def test_advertised_methods_are_dispatchable(tmp_path):
    """ADVERTISED IMPLIES DISPATCHABLE, probed directly rather than diffed.

    `methods()` feeds the "(have: ...)" text of both refusal messages, and it used to be a
    hand-written array beside the `CALLS` object it claimed to describe.  They disagreed:
    the engine refused `from_template` with a message that listed `from_template`.

    THIS MUST BE A DIRECT PROBE.  Measured: re-hard-coding `methods()` as a list that
    happens to match `CALLS` -- the precise shape of the shipped defect -- is invisible to
    every differential bucket (0, 0, 0).  Only asking the dispatcher catches it.
    """
    script = (
        "globalThis.QCCDEdit=require('./qccd/viz/js/edit.js');"
        "const Q=require('./qccd/viz/engine.js');"
        "const bad=[];"
        "for (const m of Q.methods()) {"
        "  try { Q.apply(null, {method:m, args:[], kwargs:{}}); }"
        "  catch (e) { if (e.code === 'unknown_method') bad.push(m); }"
        "}"
        "console.log(JSON.stringify({bad, methods: Q.methods(),"
        " seed: Q.SEED_METHODS, mutate: Q.MUTATE_METHODS, build: Q.BUILD_METHODS}));")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         cwd=str(ROOT), timeout=120)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert not got["bad"], f"advertised-but-not-dispatchable: {got['bad']}"

    from qccd.arch.edit import BUILD, MUTATE, SEED
    assert set(got["methods"]) == set(SEED | MUTATE | BUILD), (
        f"only in js: {sorted(set(got['methods']) - set(SEED | MUTATE | BUILD))}; "
        f"only in python: {sorted(set(SEED | MUTATE | BUILD) - set(got['methods']))}")
    # and the three kind lists are a partition of what dispatches, not three more lists
    assert set(got["seed"]) == set(SEED)
    assert set(got["build"]) == set(BUILD)
    assert set(got["mutate"]) == set(MUTATE)


@requires_node
def test_the_js_covers_every_emitted_statement(tmp_path):
    """L0: the vocabulary is COMPUTED from the shipped listings, never hand-listed.

    Add a thirteenth method to `listing.py` and this goes red naming the method, before
    any numeric diff runs.  Costs zero maintenance.

    The vocabulary is harvested from ALL FOUR listing shapes.  Built from `template=None`
    alone it never contained `from_template` at all, so the method the engine advertised
    and could not dispatch was invisible here too.
    """
    archs = [load(f) for f in ARCH_FILES]
    vocab = statement_vocabulary(archs)
    for f in ARCH_FILES:
        arch = load(f)
        stem = f.stem.replace(".arch", "")
        for _label, listing in _listing_shapes(arch, stem):
            vocab |= {str(l.call["method"]) for l in listing.lines
                      if l.kind == "call" and l.call}
    # plus the statements only an EDITED architecture emits, pulled in by editing rather
    # than by anyone typing them
    from qccd.api import Machine
    m = Machine.load(ARCH_FILES[0])
    m.move_site(next(iter(m.arch.device.nodes)), 0.5, 0.25)
    vocab |= statement_vocabulary([m.arch])
    assert {"from_template", "from_device", "blank_device"} <= vocab, (
        "the four listing shapes no longer emit the seed verbs, so this test would pass "
        "for the wrong reason")
    out = subprocess.run(
        [node, "-e",
         "globalThis.QCCDEdit=require('./qccd/viz/js/edit.js');"
         "const Q=require('./qccd/viz/engine.js');"
         "console.log(JSON.stringify({m:Q.methods(),g:Q.generators()}));"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    missing = vocab - set(got["m"])
    assert not missing, f"the JS interpreter implements none of: {sorted(missing)}"
    assert set(got["g"]) == set(GENERATORS), (
        f"generator sets differ: python={sorted(GENERATORS)} js={sorted(got['g'])}")


@requires_node
def test_the_page_inlines_this_exact_engine():
    """The bytes under test must be the bytes that ship.

    Same file in the page and in the harness makes a second copy structurally impossible
    rather than conventionally avoided.  Without this, a `render.py` change that pasted a
    fork of the engine would leave the parity test passing while the page ran the fork.
    """
    # a VISUALIZATION page, identified by its data block -- `out/index.html` is the demo
    # index and carries no view model, so it is not one
    pages = [p for p in sorted((ROOT / "out").rglob("*.html"))
             if '<script id="data"' in p.read_text(encoding="utf-8")]
    if not pages:
        pytest.skip("no emitted pages in out/; run `python -m qccd demo` first")
    engine = ENGINE.read_text(encoding="utf-8")
    edit = EDIT_JS.read_text(encoding="utf-8")
    editor = (ROOT / "qccd" / "viz" / "js" / "editor.js").read_text(encoding="utf-8")
    for p in pages:
        html = p.read_text(encoding="utf-8")
        assert engine in html, f"{p.name} does not contain qccd/viz/engine.js verbatim"
        assert edit in html, f"{p.name} does not contain qccd/viz/js/edit.js verbatim"
        assert editor in html, f"{p.name} does not contain qccd/viz/js/editor.js verbatim"
    assert len(pages) >= 9, f"only {len(pages)} page(s) checked; the demo emits ten"


def test_layout_py_uses_only_portable_arithmetic():
    """`math.hypot`, the builtin `sum()` over floats and `round()` must stay OUT.

    All three have natural JS translations that are WRONG, and each broke a real device:
    `math.hypot` and `Math.hypot` miss by an ulp in opposite directions and that ulp
    reaches `g`, from which every mark is a fraction; CPython >= 3.12 compensates `sum()`
    and no JS loop reproduces it, which flipped the SIGN of a bow; and `round()` is
    half-to-even where every JS idiom is not.

    An AST walk rather than a grep, so the docstrings that EXPLAIN the ban do not trip it.
    This fails at the point of the mistake instead of three commits later when someone
    happens to run the fuzz.
    """
    src = (ROOT / "qccd" / "viz" / "layout.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node_ in ast.walk(tree):
        if not isinstance(node_, ast.Call):
            continue
        f = node_.func
        if isinstance(f, ast.Attribute) and f.attr == "hypot":
            bad.append((node_.lineno, "math.hypot -> use _hyp"))
        if isinstance(f, ast.Name) and f.id == "sum":
            bad.append((node_.lineno, "sum() -> use _fsum"))
        if isinstance(f, ast.Name) and f.id == "round":
            bad.append((node_.lineno, "round() -> use _q"))
    assert not bad, (
        "qccd/viz/layout.py is mirrored in qccd/viz/engine.js and the two are asserted "
        "BIT-IDENTICAL. These calls have no bit-identical JS translation:\n  "
        + "\n  ".join(f"line {ln}: {msg}" for ln, msg in bad))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sum() gained Neumaier compensation in 3.12")
def test_the_sum_trap_is_real_on_this_interpreter():
    """Documents the failure `_fsum` guards against, and proves it is live here.

    On CPython < 3.12 the compensation does not exist and a developer would see a green
    parity run for the wrong reason, so this states the version dependency out loud.
    """
    xs = [800.0] * 160
    acc = 0.0
    for v in xs:
        acc += v
    # they agree on this easy case; the point is that the guard is version-conditional
    assert sum(xs) == acc
    hard = [0.1] * 10 + [1e16, -1e16]
    naive = 0.0
    for v in hard:
        naive += v
    assert sum(hard) != naive, (
        "the builtin sum() is NOT compensated on this interpreter; _fsum is still "
        "required, but this test cannot demonstrate why")


# --------------------------------------------------------------- the schema mirror


#: Plausible one-token slips in `engine.js::_schemaWalk`.  Each is the shape of mistake
#: that would let one CLASS of unloadable document out of the browser while every other
#: class stayed caught -- which is exactly how DEFECT 3 shipped: `set_curve` validated
#: nothing at all, and nothing noticed because nothing was looking.
_SCHEMA_MUTATIONS_JS = [
    ("spec.enum.indexOf(value) < 0",
     "the enum check itself -- the DEFECT 3 hole, verbatim"),
    ("own(spec, 'min') && value.length < spec.min",
     "the array minimum -- the DEFECT 2 hole, verbatim"),
    ("if (!own(value, req[i])) errors.push",
     "the required-key check"),
    ("!_schemaMatch(spec.pattern, value)",
     "the id-pattern check"),
    # NOT LISTED: the `'^(?:' + pat + ')'` start anchor.  Every pattern in ARCH_SCHEMA
    # already carries its own `^`, so removing the wrapper is a no-op against the schema
    # that actually ships and the guard would pass for the wrong reason.  It stays in
    # engine.js because a future pattern without `^` must not silently change meaning;
    # what guards it is `!_schemaMatch(spec.pattern, value)` above, which IS falsifiable.
    ("if (Number.isInteger(x) && Math.abs(x) < 1e16) return String(x);",
     "the shared number formatter that keeps the two messages byte-identical"),
]


@requires_node
@pytest.mark.parametrize("mutation, why", _SCHEMA_MUTATIONS_JS,
                         ids=[m[0][:28] for m in _SCHEMA_MUTATIONS_JS])
def test_the_schema_bucket_catches_a_planted_drift(tmp_path, mutation, why):
    """The mutation guard for the eleventh bucket.

    The schema VALUES cannot drift -- they are shipped, not copied.  The WALKER can, and a
    walker that skipped one node type would be indistinguishable from a correct one on
    every valid document.  Break each check in turn; the bucket must notice every time.
    """
    src = ENGINE.read_text(encoding="utf-8")
    assert mutation in src, f"the mutation anchor moved: {why}"
    if mutation == "spec.enum.indexOf(value) < 0":
        broken = src.replace(mutation, "false", 1)
    elif mutation == "own(spec, 'min') && value.length < spec.min":
        broken = src.replace(mutation, "false", 1)
    elif mutation == "if (!own(value, req[i])) errors.push":
        broken = src.replace(mutation, "if (false) errors.push", 1)
    elif mutation == "!_schemaMatch(spec.pattern, value)":
        broken = src.replace(mutation, "false", 1)
    elif mutation == "'^(?:' + pat + ')'":
        broken = src.replace(mutation, "'(?:' + pat + ')'", 1)
    else:
        broken = src.replace(mutation, "return String(x);", 1)
    assert broken != src

    hacked = tmp_path / "engine_broken.js"
    hacked.write_text(broken, encoding="utf-8")
    cases = build_cases(engine=hacked)
    cases["engine"] = str(hacked)
    r = run_parity(cases, tmp_path, "schema_mut")
    shapes = dict(r.get("shapes", []))
    assert r.get("mismatched", 0) > 0, (
        f"the schema bucket did not notice a planted change to {why}; it is guarding "
        f"nothing. report={_report(r)}")
    assert any(k.startswith("schema|") for k in shapes), (
        f"something mismatched but not in the schema bucket, so the guard proves nothing "
        f"about the walker. shapes={shapes}")


@requires_node
def test_the_engine_carries_no_schema_constant_of_its_own(tmp_path):
    """The structural half of the anti-drift argument, as an assertion.

    A hand-copied enum is a second source of truth and this codebase has been bitten by it
    repeatedly -- `var SCHEMA_VERSION = '0.2'` was the last survivor.  The engine must now
    be UNABLE to answer a schema question without the shipped blob, so the failure mode of
    a page emitted without one is a loud refusal at boot rather than a validator that
    quietly passes everything.
    """
    import re
    # strip comments first: this file EXPLAINS the constant it deleted, and a scan that
    # could not tell an explanation from a declaration would forbid saying why.
    src = ENGINE.read_text(encoding="utf-8")
    code = re.sub(r"//.*", "", src)
    assert not re.search(r"var\s+SCHEMA_VERSION\s*=", code), (
        "the hand-copied schema version is back; it must come from D.schema")
    # `qccdsim_jones` and `local` are the DEFAULT arguments of `CurvePoint.table` and
    # `Machine.set_curve(table=...)` -- mirrors of a Python signature, not of the enum, and
    # the same category as `_generator_defaults()`.  They are named here so that adding a
    # third exemption is a deliberate edit rather than a widening of the rule.
    SIGNATURE_DEFAULTS = {"qccdsim_jones", "local"}
    for table in TABLES:
        if table in SIGNATURE_DEFAULTS:
            continue
        assert f"'{table}'" not in code, (
            f"the engine names the table {table!r} as a literal; the enum ships as data")
    # ...and no ARCHITECTURE STEM either.  `Machine.ring(...)` falls back to
    # `api.DEFAULT_TEMPLATE` when no template is named; the engine gets that through
    # `setTemplates(map, default)` off the data blob rather than writing the stem down,
    # which is the same category of second-source-of-truth as the schema version was.
    assert DEFAULT_TEMPLATE not in GENERATORS, (
        "the default template shares a name with a generator, so this scan would be "
        "checking the wrong literal")
    for quoted in (f"'{DEFAULT_TEMPLATE}'", f'"{DEFAULT_TEMPLATE}"'):
        assert quoted not in code, (
            f"the engine names {DEFAULT_TEMPLATE!r} as a literal; the default template "
            f"ships as data (D.template_default), off api.DEFAULT_TEMPLATE")

    script = (
        "const fs=require('fs');"
        "new Function(fs.readFileSync(%r,'utf8'))();"
        "new Function(fs.readFileSync(%r,'utf8'))();"
        "const Q=globalThis.QCCD;"
        "let threw=0;"
        "try{Q.validateDocument({});}catch(e){threw++;}"
        "try{Q.schemaVersion();}catch(e){threw++;}"
        "console.log(threw);"
    ) % (str(EDIT_JS).replace("\\", "/"), str(ENGINE).replace("\\", "/"))
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=120, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "2", (
        "an engine with no schema loaded answered a schema question anyway, which means "
        "it is carrying a copy: " + out.stdout)


def test_every_emitted_page_carries_the_live_schema():
    """The page's blob must hold the schema THIS build validates with.

    Shipping the schema is only worth anything if the shipped copy is the LIVE one; a
    stale blob would be a hand-copied enum with extra steps.  The failure message says
    "rebuild the pages" because that is genuinely the whole fix -- there is nothing to
    edit by hand any more, which is the point.
    """
    import re
    if not OUT_DIR.exists():
        pytest.skip("no emitted pages to check")
    pages = [p for p in sorted(OUT_DIR.rglob("*.html")) if p.name != "index.html"]
    if not pages:
        pytest.skip("no emitted pages to check")
    want = json.dumps(export_schema(), separators=(",", ":"), sort_keys=True)
    for p in pages:
        html = p.read_text(encoding="utf-8")
        m = re.search(r'<script id="data"[^>]*>(.*?)</script>', html, re.S)
        assert m, f"{p.name}: no data blob"
        blob = json.loads(m.group(1))
        assert "schema" in blob, f"{p.name}: emitted without the schema"
        got = json.dumps(blob["schema"], separators=(",", ":"), sort_keys=True)
        assert got == want, (
            f"{p.name} ships a schema this build does not validate with; "
            f"rebuild the pages")


@pytest.mark.parametrize("path", ARCH_FILES, ids=lambda p: p.stem)
def test_the_listing_replays_through_the_whitelist(path):
    """The Python half of L2, as its own assertion: `apply_program` on `call` records
    ALONE reproduces every shipped architecture with a zero structural diff."""
    arch = load(path)
    listing = architecture_listing(arch, verify=False)
    calls = [dict(l.call) for l in listing.lines if l.kind == "call" and l.call]
    assert calls, "a listing with no calls would make this vacuous"
    rebuilt = apply_program(calls).arch
    assert architecture_diff(arch, rebuilt) == []
    assert canonical(rebuilt.to_json(expanded=True)) == canonical(arch.to_json(expanded=True))
    assert arch_fingerprint(rebuilt) == arch_fingerprint(arch)


def test_a_call_record_never_needs_its_target_to_replay():
    """The regression guard for the defect this editor was built on.

    `listing.py` emitted the geometry statement with `args: []`, so the generator name
    survived only in `target="gen:ring"` and replaying the structured form raised
    `TypeError: Machine.blank() missing 1 required positional argument` on 9 of 9 shipped
    architectures.  The rendered text and the structured form disagreed, in the exact
    record an editor reads.
    """
    for path in ARCH_FILES:
        arch = load(path)
        for line in architecture_listing(arch, verify=False).lines:
            if line.kind == "call" and line.call and line.call["method"] in ("blank", "from_template"):
                assert line.call["args"], (
                    f"{path.name}: the geometry statement carries no generator in `args`; "
                    f"an editor replaying `call` alone cannot reconstruct it")
                assert line.call["args"][0] == arch.device.generator
                break
        else:
            pytest.fail(f"{path.name} emitted no geometry seed statement")
