"""One figure per term and per rule: the legal case beside the illegal one.

NOT a second renderer, for the same reason `make_gif.py` is not.  Geometry comes from
`qccd.viz.layout.compute_layout`, frames from `qccd.viz.render.build_view_model`, and the
stage is advanced by `make_gif.Clip` -- the same object the gallery clips use.  This file
adds three things on top and nothing else: node labels, a highlight ring, and a **verdict
band whose text is the verifier's own violation string**.

That last point is the whole design.  A caption is not written by hand anywhere here: it
is `str(Violation)` out of `verify()`.  So a figure cannot claim a rule fired when it did
not, and cannot claim a program is legal when the verifier disagrees -- `figure()` asserts
the observed rule set equals the declared one and refuses to emit otherwise.

    python tools/make_rule_figs.py --all              every figure -> docs/img/rules/
    python tools/make_rule_figs.py --only R3 R5       just these
    python tools/make_rule_figs.py --list             what would be built
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from qccd.arch import Architecture, SCHEMA_VERSION, load  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.ir import TSIR, Instruction, Participant  # noqa: E402
from qccd.verify import verify  # noqa: E402

from make_gif import SS, Clip, font, rgb, save_gif  # noqa: E402

OUT = ROOT / "docs" / "img" / "rules"

STAGE_W = 330          # px per panel, before supersampling
GAP = 10


# --------------------------------------------------------------------- devices
#
# Deliberately tiny.  A rule figure has to be readable at 700 px, and the shipped
# `ring144_24v` draws 168 nodes at three pixels each.  Every device here is the smallest
# graph on which the rule in question can actually fire.

PRIMS = {
    "shuttle_segment": {"curve": [{"us": 5, "quanta": 0.1, "table": "qccdsim_jones"}]},
    "junction_cross": {"curve_by_degree": {
        "3": [{"us": 100, "quanta": 3.0, "table": "qccdsim_jones"}],
        "4": [{"us": 120, "quanta": 3.0, "table": "qccdsim_jones"}]}},
    "split": {"curve": [{"us": 80, "quanta": 6.0, "table": "qccdsim_jones"}]},
    "merge": {"curve": [{"us": 80, "quanta": 6.0, "table": "qccdsim_jones"}]},
    "ms_gate": {"us": 25, "fidelity_at_n0": 0.99816,
                "error_vs_quanta": "linear:2.0e-3", "max_quanta": 1.0},
    "1q_gate": {"us": 5}, "gate_swap": {"gates": 3},
    "measure": {"us": 120}, "reset": {"us": 50},
    "cool": {"us": 300, "removes_quanta": "all", "broadcastable": True},
}

ZONES = {
    "trap": {"capacity": 2, "gate": True, "spam": True, "cool": True},
    "gatezone": {"capacity": 2, "gate": True, "spam": True, "cool": True},
    "quiet": {"capacity": 2, "gate": False, "spam": False, "cool": True},
    "register": {"capacity": 32, "gate": True, "spam": True, "cool": True},
    "wide": {"capacity": 4, "gate": True, "spam": True, "cool": True},
}

CLASSES = [
    {"id": "rotate_cw", "type": "shift", "orbit": "L0", "delta": 1},
    {"id": "rotate_ccw", "type": "shift", "orbit": "L0", "delta": -1},
    {"id": "nudge", "type": "shift", "orbit": "L0"},
    {"id": "dock", "type": "shift", "orbit": "spurs", "entails": ["split", "merge"]},
    {"id": "undock", "type": "shift", "orbit": "spurs", "entails": ["split", "merge"]},
]


def _arch(name: str, geometry: dict, zones=None, heating=0.0, **control) -> Architecture:
    ctl = {"model": "simd_classes", "max_simd_classes_per_cycle": 1,
           "classes": {"extra": list(CLASSES)}}
    ctl.update(control)
    return Architecture.from_json({
        "name": name, "schema_version": SCHEMA_VERSION, "geometry": geometry,
        "zone_types": zones or ZONES, "primitives": PRIMS, "control": ctl,
        "heating": {"anomalous_rate_quanta_per_ms": heating},
        "species": {"T_coh_s": 600},
    })


def ring(zone: str = "gatezone", verticals: int = 2) -> Architecture:
    """8 rail slots S0..S7 in one closed loop, plus `verticals` dock spurs.

    Docks sit at S0 and S4 and are degree 3 -- a rail node with a spur hanging off it is
    what MAKES a junction (R18), and nothing declared it one.
    """
    return _arch(f"ring8_{verticals}v_{zone}", {
        "generator": "ring",
        "params": {"width": 4, "height": 2, "verticals": verticals,
                   "site_zone": zone, "ancilla_zone": "gatezone"}})


def grid() -> Architecture:
    """A 3x3 lattice: a trap in the middle of every wire, junctions where wires meet.

    Junctions here are `kind="junction"` with capacity 0, and the device has NO loops --
    which is what makes it the right stage for R2's degree-4 clause.
    """
    return _arch("grid3x3", {"generator": "grid", "params": {"a": 3, "b": 3}})


def chain(n: int = 5, zone: str = "gatezone") -> Architecture:
    """`n` traps in a line.  No loop, no junction, two degree-1 ends."""
    return _arch(f"chain{n}_{zone}", {
        "generator": "chain", "params": {"n": n, "site_zone": zone}})


def two_traps(seg_capacity: int = 1, zone: str = "gatezone") -> Architecture:
    """Two traps and one segment, whose capacity is the point.

    At capacity 1 an exchange is two ions on one segment and R3 rejects it; at capacity 2
    R3 is satisfied and only R5 stands between the schedule and two ions passing through
    each other inside a 1D channel.

    It is also the smallest stage with **no loop and no junction**, which is what lets a
    figure isolate a rule that would otherwise drag R11 or R2 in with it.
    """
    cap = ZONES[zone]["capacity"]
    return _arch(f"two_traps_cap{seg_capacity}_{zone}", {
        "generator": "explicit",
        "nodes": [{"id": "T0", "kind": "site", "pos": [0, 0], "capacity": cap,
                   "zone_type": zone},
                  {"id": "T1", "kind": "site", "pos": [2, 0], "capacity": cap,
                   "zone_type": zone}],
        "segments": [{"id": "E0", "ends": ["T0", "T1"], "length": 1,
                      "capacity": seg_capacity}]})


def wired_ring(switch_per_site: bool = True) -> Architecture:
    """The 8-slot ring, but with its control wiring DECLARED.

    Without `control.channels` R4d has nothing to judge and reports *not judged*, which is
    not a pass -- so the R4d figure needs a device that says how its electrodes are tied.
    Three `linear_h` channels driving every rail slot is H2's `{a,b,c}` conveyor tiling
    (2305.03828) at eight slots instead of forty.
    """
    return _arch("ring8_wired", {
        "generator": "ring",
        "params": {"width": 4, "height": 2, "verticals": 2,
                   "site_zone": "gatezone", "ancilla_zone": "gatezone"}},
        channels={"grouping": "broadcast", "roles": {"linear_h": 3, "junction": 2},
                  "switch_per_site": switch_per_site})


def lab_ring() -> Architecture:
    """The same eight slots, wired the way a LAB-FRAME machine has to be.

    `wired_ring` is H2's reading: `frame` defaults to `"path"`, the conveyor follows the
    trap axis, and one waveform advances the whole loop, bends included (2305.03828).
    This is the other reading and the one the target machine takes: the electrode tiling
    is fixed to the chip axes, so `+x` and `-x` are different waveforms and the corner
    needs its own.  `Device.shift_directions("L0", +1)` says which four those are, and
    the channel map is CUT FROM THAT -- one `explicit` group per axis direction, sizes
    3/3/1/1 here and 71/71/1/1 on the shipped `ring144_24v`.  Constant in array size,
    which is the whole broadcast argument, and R4d and R19 both pass on it.

    Only the `+1` shift is declared.  A four-group map cut for `+1` cannot drive `-1`
    (every site's direction changes), and R19 says so -- correctly, and loudly enough to
    drown out the rule this device exists to isolate.
    """
    base = _arch("ring8_labframe_base", {
        "generator": "ring",
        "params": {"width": 4, "height": 2, "verticals": 2,
                   "site_zone": "gatezone", "ancilla_zone": "gatezone"}})
    labels, oblique = base.device.shift_directions("L0", 1)
    assert not oblique, oblique
    by: dict[str, list[str]] = {}
    for site, lab in labels.items():
        by.setdefault(lab, []).append(site)
    return _arch("ring8_labframe", {
        "generator": "ring",
        "params": {"width": 4, "height": 2, "verticals": 2,
                   "site_zone": "gatezone", "ancilla_zone": "gatezone"}},
        classes={"extra": [c for c in CLASSES if c["id"] != "rotate_ccw"]},
        channels={"grouping": "explicit", "frame": "lab", "switch_per_site": True,
                  "explicit": [
                      {"id": f"lab{lab}",
                       "role": "linear_h" if "x" in lab else "linear_v",
                       "drives": sorted(v)} for lab, v in sorted(by.items())]})


def lab_ring_broadcast() -> Architecture:
    """Lab-frame tiling, but wired the way every shipped device is wired.

    `grouping: "broadcast"` puts every site on every channel, so the whole loop is ONE
    independently driven group -- against the four directions a rectangular path turns
    into.  This is the combination R19 exists to reject, and it is the shipped
    `ring144_24v` map with one field changed.
    """
    return _arch("ring8_lab_broadcast", {
        "generator": "ring",
        "params": {"width": 4, "height": 2, "verticals": 2,
                   "site_zone": "gatezone", "ancilla_zone": "gatezone"}},
        classes={"extra": [c for c in CLASSES if c["id"] != "rotate_ccw"]},
        channels={"grouping": "broadcast", "frame": "lab", "switch_per_site": True,
                  "roles": {"linear_h": 3, "junction": 2}})


def lab_ring_both_ways() -> Architecture:
    """`lab_ring`'s four-group map, asked to turn the loop BOTH ways.

    The sharpest consequence of the lab frame, and the one that is not obvious: `+1` and
    `-1` do not induce the same partition of the sites.  They are offset by one at the
    corners, so the site that goes `+y` under `+1` is not the site that goes `-y` under
    `-1`, and a map cut for one direction asks one of its channels for two waveforms
    under the other.  A device that must rotate both ways needs the COMMON REFINEMENT of
    the two partitions -- 6 groups here and on `ring144_24v` (70/70/1/1/1/1), still
    constant in array size.
    """
    base = lab_ring()
    doc = base.to_json(expanded=False)
    doc["name"] = "ring8_lab_bothways"
    doc["control"]["classes"] = {"extra": list(CLASSES)}      # rotate_ccw restored
    return Architecture.from_json(doc)


# ------------------------------------------------------------------- programmes


def init(placement: dict, quanta: dict | None = None) -> Instruction:
    return Instruction(type="init", id=0, placement=dict(placement),
                       quanta=dict(quanta or {k: 0.0 for k in placement}))


def move(*movers, cls="nudge", mode="inter", id=1, **kw) -> Instruction:
    """One transport cycle.

    A mover is `(ion, src, dst)`, or `(ion, src, dst, (seg, seg, ...))` to route it
    explicitly -- which is the only way to express a hop that passes THROUGH a trap
    rather than stopping in it, and therefore the only way to reach R1's roadblock clause.
    """
    return Instruction(type="simd", id=id, cls=cls, mode=mode, participants=tuple(
        Participant(m[0], m[1], m[2], via=tuple(m[3]) if len(m) > 3 else ())
        for m in movers), **kw)


def rotate(loop="L0", delta=1, cls="rotate_cw", id=1, **kw) -> Instruction:
    """The rigid-rotation template as ONE instruction -- no participants, by design."""
    from qccd.ir.tsir import loop_shift
    return Instruction(type="simd", id=id, cls=cls, mode="inter",
                       template=loop_shift(loop, delta), **kw)


def prog(*instrs: Instruction, name="fig") -> TSIR:
    return TSIR(name=name, arch_spec="inline", instructions=list(instrs))


# ----------------------------------------------------------------- the drawing


def _labelled(clip: Clip, highlight: Sequence[str] = (), labels: bool = True):
    """`Clip.board()` plus node ids and a highlight ring.

    Ids are what make a rule figure readable as an argument rather than as decoration:
    the verdict band says "segment E1 carries 2 ions" and the reader has to be able to
    find E1.
    """
    img = clip.board()
    d = ImageDraw.Draw(img, "RGBA")
    f = font(int(11 * SS))
    r = max(2 * SS, int(round(clip.L["r_ion"] * 0.92 * clip.k)))

    for nid in highlight:
        if nid in clip.nodes:
            x, y = clip.xy(nid)
            rr = r * 2.6
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=rgb("accent"),
                      width=max(2, SS))
        for s in clip.segments:                       # a highlighted SEGMENT id
            if s["id"] == nid and s["a"] in clip.nodes and s["b"] in clip.nodes:
                d.line([*clip.xy(s["a"]), *clip.xy(s["b"])], fill=rgb("accent"),
                       width=max(2, int(round(clip.L["sw_rail"] * clip.k * 0.5))))

    if labels:
        for nid in clip.nodes:
            x, y = clip.xy(nid)
            w = d.textlength(nid, font=f)
            d.text((x - w / 2, y + r * 1.5), nid, font=f, fill=rgb("muted"))
    return img


def _band(width: int, lines: Sequence[tuple[str, str]], pad=7 * SS) -> Image.Image:
    """A verdict strip: `(colour_role, text)` per line, wrapped to `width`."""
    f = font(int(12 * SS), mono=True)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    rows: list[tuple[str, str]] = []
    for role, text in lines:
        words, cur = text.split(), ""
        for w in words:
            trial = (cur + " " + w).strip()
            if probe.textlength(trial, font=f) > width - 2 * pad and cur:
                rows.append((role, cur))
                cur = w
            else:
                cur = trial
        rows.append((role, cur))
    lh = int(f.size * 1.45)
    img = Image.new("RGB", (width, lh * len(rows) + 2 * pad), rgb("panel"))
    d = ImageDraw.Draw(img)
    d.line([0, 0, width, 0], fill=rgb("line"), width=SS)
    for i, (role, text) in enumerate(rows):
        d.text((pad, pad + i * lh), text, font=f, fill=rgb(role))
    return img


def _title(width: int, head: str, sub: str) -> Image.Image:
    fb = font(int(17 * SS))
    fs = font(int(12 * SS))
    pad = 9 * SS
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    rows, cur = [], ""
    for w in sub.split():
        trial = (cur + " " + w).strip()
        if probe.textlength(trial, font=fs) > width - 2 * pad and cur:
            rows.append(cur)
            cur = w
        else:
            cur = trial
    rows.append(cur)
    h = pad * 2 + int(fb.size * 1.4) + int(fs.size * 1.4) * len(rows)
    img = Image.new("RGB", (width, h), rgb("panel"))
    d = ImageDraw.Draw(img)
    d.text((pad, pad), head, font=fb, fill=rgb("navy"))
    for i, row in enumerate(rows):
        d.text((pad, pad + int(fb.size * 1.4) + i * int(fs.size * 1.4)), row,
               font=fs, fill=rgb("muted"))
    d.line([0, h - SS, width, h - SS], fill=rgb("line"), width=SS)
    return img


# ------------------------------------------------------------------- one panel


@dataclass
class Case:
    """One side of a figure: a device, a programme, and what must happen to it."""

    arch: Architecture
    program: TSIR
    label: str                       # "LEGAL" / "VIOLATION" / free text
    expect: tuple[str, ...] = ()     # the rule set that MUST fire, exactly
    model: str = "deck"
    highlight: tuple[str, ...] = ()
    #: one extra line under the verdict.  A callable receives the VerificationReport, so
    #: a figure can quote a replayed number (quanta, cost) rather than a written-down one.
    note: str | Callable = ""
    labels: bool = True
    metrics: bool = False            # let R9 run: the program carries claims

    def run(self):
        m = deck_model() if self.model == "deck" else corrected_model()
        rep = verify(self.program, self.arch, m, check_metrics=self.metrics)
        got = tuple(sorted({v.rule for v in rep.rules.violations}))
        if got != tuple(sorted(self.expect)):
            raise AssertionError(
                f"{self.label}: expected rules {sorted(self.expect)} to fire, got "
                f"{list(got)}\n  " + "\n  ".join(str(v) for v in rep.rules.violations))
        return rep, m

    def verdict(self, rep) -> list[tuple[str, str]]:
        if not self.expect:
            out = [("loop", "PASS  no rule fires")]
        else:
            seen, out = set(), []
            for v in rep.rules.violations:
                if v.rule in seen:
                    continue
                seen.add(v.rule)
                out.append(("accent", f"FAIL  {v}"))
        note = self.note(rep) if callable(self.note) else self.note
        if note:
            out.append(("muted", note))
        return out


def _arrows(img: Image.Image, clip: Clip, i: int) -> Image.Image:
    """Draw each participant's hop as an arrow, src -> dst.

    Without this a still frame of "one ion leaves S1" and one of "both ions leave S1" are
    very nearly the same picture, and the figure stops being an argument.  The arrows come
    from `clip.paths[i]` -- the replayed route, not a redrawing of the instruction.
    """
    d = ImageDraw.Draw(img, "RGBA")
    for path in clip.paths[i].values():
        if len(path) < 2:
            continue
        for a, b in zip(path, path[1:]):
            (x0, y0), (x1, y1) = clip.xy(a), clip.xy(b)
            x0, y0, x1, y1 = x0 / SS, y0 / SS, x1 / SS, y1 / SS
            dx, dy = x1 - x0, y1 - y0
            n = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / n, dy / n
            px, py = -uy, ux                       # unit normal: offset off the rail
            ox, oy = px * 9, py * 9
            hx, hy = x1 - ux * 13 + ox, y1 - uy * 13 + oy
            d.line([x0 + ux * 13 + ox, y0 + uy * 13 + oy, hx, hy],
                   fill=(*rgb("arrow"), 235), width=3)
            d.polygon([(x1 - ux * 4 + ox, y1 - uy * 4 + oy),
                       (hx + px * 4.5, hy + py * 4.5),
                       (hx - px * 4.5, hy - py * 4.5)], fill=(*rgb("arrow"), 235))
    return img


#: How many movers/operands a listing row spells out before it summarises the rest.  A
#: 144-ion rotation is not debuggable as 144 comma-separated pairs, and it is not
#: debuggable as "144 movers" either -- the first few plus a count is what lets a reader
#: check the pattern and the arithmetic at once.
LIST_HEAD = 4


def instruction_text(instr) -> str:
    """One TSIR instruction, spelled out exactly as the program carries it.

    Rendered from the `Instruction` dataclass, NOT from the view model's frame -- the
    frame is a drawing instruction and this is meant to be the hardware program, so that
    a reader checking whether the figure shows what it claims is reading the same object
    `verify()` read.
    """
    def few(items, fmt):
        shown = ", ".join(fmt(x) for x in items[:LIST_HEAD])
        rest = len(items) - LIST_HEAD
        return shown + (f", (+{rest} more)" if rest > 0 else "")

    t = instr.type
    if t == "init":
        pl = sorted(instr.placement.items())
        hot = {k: v for k, v in (instr.quanta or {}).items() if v}
        s = "init      " + few(pl, lambda kv: f"{kv[0]}@{kv[1]}")
        if hot:
            s += "  quanta=" + few(sorted(hot.items()), lambda kv: f"{kv[0]}:{kv[1]:g}")
        return s
    if t == "simd":
        head = f"simd      cls={instr.cls} mode={instr.mode}"
        if instr.template:
            tm = instr.template
            return (f"{head}  template=loop_shift(loop={tm.get('loop')}, "
                    f"delta={int(tm.get('delta', 0)):+d})")
        ps = list(instr.participants)
        body = few(ps, lambda p: f"{p.ion}: {p.src}->{p.dst}"
                                 + (f" via{list(p.via)}" if p.via else ""))
        head += f"  [{len(ps)} mover{'s' if len(ps) != 1 else ''}]"
        if instr.pairs:                       # illegal, but the figure has to show it
            head += "  pairs=" + few(list(instr.pairs), lambda q: f"({q[0]},{q[1]})")
        return f"{head}  {body}"
    if t == "gate":
        s = f"gate {instr.gate or 'MS':<4}"
        if instr.pairs:
            s += " pairs=" + few(list(instr.pairs), lambda q: f"({q[0]},{q[1]})")
        if instr.ions:
            s += " ions=" + few(list(instr.ions), str)
        if instr.sites:
            s += "  sites=" + few(list(instr.sites), str)
        return s
    if t == "cool":
        return ("cool      broadcast=True (every ion in the trap)" if instr.broadcast
                else "cool      ions=" + few(list(instr.ions), str))
    if t in ("measure", "reset"):
        return f"{t:<9} ions=" + few(list(instr.ions), str)
    return t


def _listing(width: int, program, current_id: int | None, rows_font=None):
    """The whole hardware program, with a cursor on the instruction being executed.

    The user's ask, and the right one: an animation that shows ions moving but not the
    instruction that moved them cannot be used to check the instruction.  These programs
    are two to five instructions long, so the WHOLE program fits and the reader can see
    both what is running and what it sits between.
    """
    f = rows_font or font(int(11 * SS), mono=True)
    fh = font(int(10 * SS))
    pad, lh = 7 * SS, int((rows_font or f).size * 1.5)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    rows: list[tuple[bool, str]] = []
    # A program-level CLAIM is not an instruction, so without this the two panels of the
    # R9 figure carry identical listings and the figure cannot show what R9 falsifies.
    entries = [(False, "metrics= " + ", ".join(f"{k}={v:g}" for k, v in
                                               sorted(program.metrics.items())))] \
        if getattr(program, "metrics", None) else []
    entries += [(ins.id == current_id, f"#{ins.id} {instruction_text(ins)}")
                for ins in program.instructions]

    for cur, text in entries:
        # wrap on width, continuation rows indented under the id.  `.strip()` here would
        # eat that indent on the next word, which is what made the wrapped half of a
        # two-mover row look like a second instruction.
        indent = "      "
        line = ""
        for w in text.split(" "):
            trial = f"{line} {w}" if line else w
            if probe.textlength("> " + trial, font=f) > width - 2 * pad and line:
                rows.append((cur, line))
                line = indent + w
            else:
                line = trial
        rows.append((cur, line))

    img = Image.new("RGB", (width, pad * 2 + int(fh.size * 1.6) + lh * len(rows)),
                    rgb("soft"))
    d = ImageDraw.Draw(img)
    d.line([0, 0, width, 0], fill=rgb("line"), width=SS)
    d.text((pad, pad), "hardware program (TSIR), cursor on the executing step",
           font=fh, fill=rgb("muted"))
    y = pad + int(fh.size * 1.6)
    for i, (cur, text) in enumerate(rows):
        if cur:
            d.rectangle([pad // 2, y + i * lh - 2 * SS, width - pad // 2,
                         y + (i + 1) * lh - 2 * SS], fill=rgb("card"))
        d.text((pad, y + i * lh), ("> " if cur and not text.startswith("   ") else "  ")
               + text, font=f, fill=rgb("navy") if cur else rgb("muted"))
    return img


def _panel_frames(case: Case, width: int, sub: int):
    """Every rendered instant of one case, plus its verdict band."""
    rep, model = case.run()
    clip = Clip(case.arch, case.program, rep.result, model, width=width)
    base = _labelled(clip, case.highlight, case.labels)
    fs = font(int(13 * SS * width / 560))
    fm = font(int(12 * SS * width / 560), mono=True)

    # The `init` frame carries no motion and no verdict, and it is what a reader sees in
    # a still preview of the GIF.  Skip it -- unless it is the whole programme.
    live = [i for i, f in enumerate(clip.frames) if f["type"] != "init"] or \
        list(range(len(clip.frames)))

    # One listing per instruction id, not per frame: the cursor is the only thing that
    # moves, so a frame just indexes into this.
    listings = {}
    for f in clip.frames:
        if f["id"] not in listings:
            im = _listing(imgs_w := width * SS, case.program, f["id"])
            listings[f["id"]] = im.resize((imgs_w // SS, im.height // SS), Image.LANCZOS)

    imgs, lists = [], []
    for i in live:
        hops = max((len(p) - 1 for p in clip.paths[i].values()), default=0)
        steps = min(max(1, hops * sub), 24)
        for s in range(steps):
            imgs.append(_arrows(
                clip.draw(base, i, (s + 1) / steps, fs, fm,
                          caption=(case.arch.name, case.label,
                                   f"step {i + 1}/{len(clip.frames)}")),
                clip, i))
            lists.append(listings[clip.frames[i]["id"]])
    band = _band(imgs[0].width * SS, case.verdict(rep))
    return imgs, lists, band.resize((band.width // SS, band.height // SS), Image.LANCZOS)


# ------------------------------------------------------------------ the figure


#: R15, R16 and R17 constrain the COST MODEL, not a program -- there is no illegal
#: schedule to draw, and a stage would be a lie.  What they constrain is a curve, so the
#: figure is the curve, plotted from the shipped architecture and model rather than drawn.


def chart(width: int, height: int, series, *, xlabel: str, ylabel: str,
          xlim=None, ylim=None, marks=(), shade=None, logy=False) -> Image.Image:
    """A minimal line plot in the gallery palette.

    `series`  -- `(label, colour_role, [(x, y), ...], dashed)` tuples
    `marks`   -- `(x, colour_role, text)` vertical rules, e.g. R7's budget
    `shade`   -- `(x0, x1, text)` a region the model is not valid on
    """
    import math

    W, H = width * SS, height * SS
    # `pad_t` leaves room for the y-axis name and the shade caption above the plot, and
    # `pad_b` for the x-axis name plus one legend row per series -- text that overlapped
    # the curves when it was drawn inside the frame.
    pad_l, pad_r, pad_t = 58 * SS, 16 * SS, 30 * SS
    pad_b = int((46 + 15 * len(series)) * SS)
    img = Image.new("RGB", (W, H), rgb("bg"))
    d = ImageDraw.Draw(img, "RGBA")
    f = font(int(11 * SS), mono=True)
    fl = font(int(12 * SS))

    pts = [p for _, _, ps, *_ in series for p in ps]
    x0, x1 = xlim or (min(p[0] for p in pts), max(p[0] for p in pts))
    y0, y1 = ylim or (min(p[1] for p in pts), max(p[1] for p in pts))
    tf = (lambda v: math.log10(max(v, 1e-12))) if logy else (lambda v: v)
    ty0, ty1 = tf(y0), tf(y1)

    def px(x):
        return pad_l + (x - x0) / ((x1 - x0) or 1) * (W - pad_l - pad_r)

    def py(y):
        return H - pad_b - (tf(y) - ty0) / ((ty1 - ty0) or 1) * (H - pad_t - pad_b)

    if shade:
        sx0, sx1, text = shade
        d.rectangle([px(sx0), pad_t, px(sx1), H - pad_b], fill=(*rgb("accent"), 26))
        d.text((min(px(sx0) + 5 * SS, W - pad_r - d.textlength(text, font=f)),
                pad_t - f.size * 1.5), text, font=f, fill=rgb("accent"))

    d.line([pad_l, pad_t, pad_l, H - pad_b], fill=rgb("line"), width=SS)
    d.line([pad_l, H - pad_b, W - pad_r, H - pad_b], fill=rgb("line"), width=SS)
    for frac in (0.0, 0.5, 1.0):
        yv = y0 + (y1 - y0) * frac if not logy else 10 ** (ty0 + (ty1 - ty0) * frac)
        xv = x0 + (x1 - x0) * frac
        d.line([pad_l - 4 * SS, py(yv), pad_l, py(yv)], fill=rgb("line"), width=SS)
        lab = f"{yv:.3g}"
        d.text((pad_l - 8 * SS - d.textlength(lab, font=f), py(yv) - f.size * 0.6),
               lab, font=f, fill=rgb("muted"))
        d.line([px(xv), H - pad_b, px(xv), H - pad_b + 4 * SS], fill=rgb("line"), width=SS)
        lab = f"{xv:.3g}"
        d.text((px(xv) - d.textlength(lab, font=f) / 2, H - pad_b + 7 * SS),
               lab, font=f, fill=rgb("muted"))

    # Vertical rules are labelled at STAGGERED heights: two marks a few percent apart on
    # the x-axis -- which is exactly the interesting case, R7's budget beside the point
    # where heating equals the floor -- otherwise print on top of each other.
    for k, (x, role, text) in enumerate(marks):
        d.line([px(x), pad_t, px(x), H - pad_b], fill=rgb(role), width=SS)
        tw = d.textlength(text, font=f)
        tx = px(x) + 4 * SS
        if tx + tw > W - pad_r:
            tx = px(x) - 4 * SS - tw
        d.text((tx, pad_t + 6 * SS + k * f.size * 1.7), text, font=f, fill=rgb(role))

    for i, (label, role, ps, *rest) in enumerate(series):
        dashed = rest[0] if rest else False
        xy = [(px(a), py(b)) for a, b in ps]
        if dashed:
            for j in range(0, len(xy) - 1, 2):
                d.line([xy[j], xy[j + 1]], fill=rgb(role), width=2 * SS)
        else:
            d.line(xy, fill=rgb(role), width=2 * SS, joint="curve")
        ly = H - pad_b + (30 + 15 * i) * SS       # a key row, below the axis name
        d.line([pad_l, ly + fl.size * 0.55, pad_l + 22 * SS, ly + fl.size * 0.55],
               fill=rgb(role), width=2 * SS)
        d.text((pad_l + 28 * SS, ly), label, font=fl, fill=rgb(role))

    d.text((W / 2 - d.textlength(xlabel, font=fl) / 2, H - pad_b + 13 * SS),
           xlabel, font=fl, fill=rgb("navy"))
    d.text((6 * SS, 6 * SS), ylabel, font=fl, fill=rgb("navy"))
    return img.resize((width, height), Image.LANCZOS)


@dataclass
class Fig:
    key: str
    head: str
    sub: str
    cases: list[Case] = field(default_factory=list)
    static: bool = False             # emit a PNG rather than a GIF
    ms: int = 180
    hold: int = 1400
    width: int = STAGE_W
    #: a chart figure: `(width, height) -> Image`, plus the verdict lines under it
    chart: Callable | None = None
    chart_note: list = field(default_factory=list)


def build(fig: Fig, out_dir: Path) -> Path:
    if fig.chart is not None:
        return _build_chart(fig, out_dir)
    panels = [_panel_frames(c, fig.width, sub=1 if fig.static else 3)
              for c in fig.cases]
    n = max(len(p[0]) for p in panels)
    pw = max(p[0][0].width for p in panels)
    ph = max(p[0][0].height for p in panels)
    lh = max(im.height for _, lists, _ in panels for im in lists)
    bh = max(p[2].height for p in panels)
    total_w = pw * len(panels) + GAP * (len(panels) - 1)

    title = _title(total_w * SS, fig.head, fig.sub)
    title = title.resize((total_w, title.height // SS), Image.LANCZOS)

    out_frames = []
    for k in range(1 if fig.static else n):
        canvas = Image.new("RGB", (total_w, title.height + ph + lh + bh), rgb("bg"))
        canvas.paste(title, (0, 0))
        for j, (imgs, lists, band) in enumerate(panels):
            x = j * (pw + GAP)
            idx = min(k, len(imgs) - 1) if not fig.static else len(imgs) - 1
            canvas.paste(imgs[idx], (x, title.height))
            canvas.paste(lists[idx], (x, title.height + ph))
            canvas.paste(band, (x, title.height + ph + lh))
        out_frames.append(canvas)

    out_dir.mkdir(parents=True, exist_ok=True)
    if fig.static:
        path = out_dir / f"{fig.key}.png"
        out_frames[0].save(path, optimize=True)
        print(f"{path.name:26s}  static      {out_frames[0].width}x"
              f"{out_frames[0].height} {path.stat().st_size / 1024:8.1f} KB")
        return path
    durs = [fig.ms] * len(out_frames)
    durs[-1] = fig.hold
    return save_gif(out_frames, durs, out_dir / f"{fig.key}.gif", 64,
                    f"({len(fig.cases)} panels)")


def _build_chart(fig: Fig, out_dir: Path) -> Path:
    w = STAGE_W * 2 + GAP
    body = fig.chart(w, int(w * 0.52))
    title = _title(w * SS, fig.head, fig.sub)
    title = title.resize((w, title.height // SS), Image.LANCZOS)
    band = _band(w * SS, fig.chart_note)
    band = band.resize((w, band.height // SS), Image.LANCZOS)
    canvas = Image.new("RGB", (w, title.height + body.height + band.height), rgb("bg"))
    canvas.paste(title, (0, 0))
    canvas.paste(body, (0, title.height))
    canvas.paste(band, (0, title.height + body.height))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fig.key}.png"
    canvas.save(path, optimize=True)
    print(f"{path.name:26s}  chart       {canvas.width}x{canvas.height} "
          f"{path.stat().st_size / 1024:8.1f} KB")
    return path


# --------------------------------------------------------------------- the set

def figures() -> list[Fig]:
    from rule_figs_spec import SPECS     # noqa: E402  -- the table, kept separate
    return SPECS()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-o", "--out", default=str(OUT))
    a = ap.parse_args(argv)

    figs = figures()
    if a.list:
        for f in figs:
            kind = "CHART" if f.chart else ("PNG" if f.static else "GIF")
            print(f"{f.key:26s} {kind:5s} {len(f.cases)}p  {f.head}")
        return 0
    if a.only:
        want = {k.lower() for k in a.only}
        figs = [f for f in figs if f.key.lower() in want
                or f.key.split("_")[0].lower() in want]
    if not figs:
        print("nothing selected")
        return 1
    bad = 0
    for f in figs:
        try:
            build(f, Path(a.out))
        except AssertionError as e:
            bad += 1
            print(f"{f.key:26s}  REFUSED: {e}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
