"""Render a device + programme to an animated GIF -- the README's moving pictures.

The point of this tool is that it is NOT a second renderer.  Geometry comes from
`qccd.viz.layout.compute_layout`, the frame list comes from `qccd.viz.render`'s view
model, and the stage is advanced by the same `applyFrame` semantics the emitted HTML page
uses -- so a GIF checked into `docs/img/` shows what `python -m qccd run --html` shows,
only smaller and without a browser.

    python tools/make_gif.py --all                      every gallery clip
    python tools/make_gif.py -d grid9x9 -p walk -o docs/img/grid9x9.gif
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from qccd.arch import load  # noqa: E402
from qccd.compile import build  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.verify import verify  # noqa: E402
from qccd.viz.render import build_view_model  # noqa: E402
from qccd.viz.theme import PALETTE, SEGMENT_ROLE  # noqa: E402

ARCH = ROOT / "arch"
HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"
IMG = ROOT / "docs" / "img"

SS = 2  # supersample factor: draw big, downscale, get anti-aliasing for free


# ----------------------------------------------------------------- small helpers


def rgb(name: str) -> tuple[int, int, int]:
    h = PALETTE[name].lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def font(size: int, mono: bool = False):
    names = (["consola.ttf", "DejaVuSansMono.ttf"] if mono else
             ["segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"])
    for n in names:
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/", ""):
            try:
                return ImageFont.truetype(base + n, size)
            except OSError:
                continue
    return ImageFont.load_default()


#: every colour that means something rather than merely shades something.  `save_gif`
#: pins these into the GIF palette so quantization cannot collapse two roles into one.
ROLE_COLOURS = ("bg", "panel", "line", "muted", "navy", "rail", "highway", "compute",
                "junction", "grid_faint", "data", "anc", "accent", "arrow", "active",
                "ion_stroke")


def segment_role(labels) -> str:
    for key, role in SEGMENT_ROLE:
        if key in labels:
            return role
    return "rail"


# ----------------------------------------------------------------- the stage
#
# `applyFrame` / `pathsOf`, ported from the page script in `qccd/viz/render.py`.  Same two
# frame shapes -- a `shift` template that moves every ion on a loop, or an explicit
# `moves` list -- so the GIF and the page agree about where an ion is.


def paths_of(frame, prev, loops):
    out = {}
    if frame.get("shift"):
        loop, delta = frame["shift"]
        seq = loops.get(loop)
        if not seq:
            return out
        k, step = len(seq), (1 if delta >= 0 else -1)
        idx = {n: i for i, n in enumerate(seq)}
        for ion, at in prev.items():
            i0 = idx.get(at)
            if i0 is None:
                continue
            out[ion] = [seq[(i0 + step * h) % k] for h in range(abs(delta) + 1)]
    elif frame.get("moves"):
        for ion, path in frame["moves"]:
            out[ion] = list(path)
    return out


def derive_stage(frames, loops):
    """`(before, after, paths)` per frame: ion -> node, and the walk between them."""
    pos, before, after, paths = {}, [], [], []
    for f in frames:
        before.append(dict(pos))
        p = paths_of(f, pos, loops)
        if f["type"] == "init":
            pos.update(f.get("place", {}))
        else:
            for ion, path in p.items():
                pos[ion] = path[-1]
        paths.append(p)
        after.append(dict(pos))
    return before, after, paths


# ----------------------------------------------------------------- the drawing


def load_pair(arch_name, program, model_name="corrected", max_frames=4000):
    """`arch/<name>.arch.json` x a builder name, the way the CLI resolves them."""
    arch = load(ARCH / f"{arch_name}.arch.json")
    kind, _, spec = program.partition(":")
    model = deck_model() if model_name == "deck" else corrected_model()
    prog = (build(arch, "deck", html_path=HTML) if kind == "deck"
            else build(arch, kind, int(spec)) if spec else build(arch, kind))
    res = verify(prog, arch, model, check_metrics=False).result
    return arch, prog, res, model, max_frames


class Clip:
    """One device x one programme, ready to be sampled at any (frame, phase).

    `layout` overrides the fit computed for this device, which is what lets a sweep over
    a design parameter animate: six variants of one ring drawn against ONE map, so the
    spurs appear rather than the whole picture rescaling under them.
    """

    def __init__(self, arch, prog, res, model, max_frames=4000, width=560,
                 layout=None, source=None, rows=9, stage_h=None):
        vm = build_view_model(arch, prog, res, model, max_frames=max_frames,
                              include_listing=False, include_control=False)

        self.arch, self.prog, self.res, self.vm = arch, prog, res, vm
        self.nodes = {n["id"]: n for n in vm["arch"]["nodes"]}
        self.segments = vm["arch"]["segments"]
        self.loops = vm["arch"]["loops"]
        self.L = layout or vm["layout"]
        self.frames = vm["program"]["frames"]
        self.ion_roles = vm["ion_roles"]
        self.before, self.after, self.paths = derive_stage(self.frames, self.loops)

        # A COMPILED programme carries the circuit it came from, and the clip then has
        # two things to say per step rather than one: the hardware instruction, and the
        # QASM statement it is discharging.  That is a band under the stage, not a wider
        # caption -- the caption is one line and the second answer is a line of source.
        self.source = source
        self.src_line = {}
        self.hw = []
        self.circ = []
        if source:
            for o in source["ops"]:
                self.src_line[o["i"]] = o
            # BOTH listings are built once, in full: the clip is tracking two fixed
            # programs and the whole point is that the reader can see they are fixed.
            # Only the window moves.
            self.hw = [(str(i + 1), *self._hw_row(f))
                       for i, f in enumerate(self.frames)]
            for o in source["ops"]:
                text = (source["lines"][o["line"] - 1].strip()
                        if 0 < o["line"] <= len(source["lines"]) else "")
                self.circ.append((str(o["line"]), text or o["name"], ""))

        # A TWELVE-trap device fitted to the full width draws its junctions at 120 px and
        # its ions at 60, which is not a diagram of a machine, it is a diagram of four
        # squares.  Capping the stage height caps the scale, and the stage is then centred
        # in the full width rather than the page shrinking to it.
        self.k = (width * SS) / self.L["W"]
        if stage_h:
            self.k = min(self.k, (stage_h * SS) / self.L["H"])
        self.W = int(round(width * SS))
        self.ox = (self.W - self.L["W"] * self.k) / 2
        self.head = int(round(30 * SS * width / 560))
        self.rows = min(rows, max(len(self.hw), len(self.circ))) if source else 0
        self.row_h = int(round(15 * SS * width / 560))
        self.foot = (int(round(20 * SS * width / 560)) + (self.rows + 1) * self.row_h
                     if source else 0)
        self.H = int(round(self.L["H"] * self.k)) + self.head + self.foot
        self.axis = self._axes()

    # -- geometry ---------------------------------------------------------
    def xy(self, nid):
        n = self.nodes[nid]
        return (self.ox + (self.L["ox"] + n["x"] * self.L["sx"]) * self.k,
                (self.L["oy"] + n["y"] * self.L["sy"]) * self.k + self.head)

    def _axes(self):
        """A unit vector along each node's trap axis -- where extra ions stack."""
        acc = {}
        for s in self.segments:
            for a, b in ((s["a"], s["b"]), (s["b"], s["a"])):
                if a in self.nodes and b in self.nodes and a not in acc:
                    dx = self.nodes[b]["x"] - self.nodes[a]["x"]
                    dy = self.nodes[b]["y"] - self.nodes[a]["y"]
                    d = math.hypot(dx, dy) or 1.0
                    acc[a] = (dx / d, dy / d)
        return acc

    def point(self, path, t):
        """Where an ion sits `t` of the way along its walk for this frame."""
        if not path:
            return None
        if len(path) == 1:
            return self.xy(path[0])
        span = (len(path) - 1) * min(max(t, 0.0), 1.0)
        i = min(int(span), len(path) - 2)
        u = span - i
        (x0, y0), (x1, y1) = self.xy(path[i]), self.xy(path[i + 1])
        return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u

    # -- the static picture, built once -----------------------------------
    def board(self):
        img = Image.new("RGB", (self.W, self.H), rgb("bg"))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, self.W, self.head], fill=rgb("panel"))
        d.line([0, self.head, self.W, self.head], fill=rgb("line"), width=SS)
        if self.foot:
            top = self.H - self.foot
            d.rectangle([0, top, self.W, self.H], fill=rgb("panel"))
            d.line([0, top, self.W, top], fill=rgb("line"), width=SS)
            d.line([self.W // 2, top, self.W // 2, self.H], fill=rgb("line"), width=SS)

        w_rail = max(SS, int(round(self.L["sw_rail"] * self.k)))
        for s in self.segments:
            if s["a"] not in self.nodes or s["b"] not in self.nodes:
                continue
            col = {"highway": rgb("highway"), "compute": rgb("compute")}.get(
                segment_role(s["labels"]), rgb("rail"))
            d.line([*self.xy(s["a"]), *self.xy(s["b"])], fill=col,
                   width=w_rail, joint="curve")

        # A junction is the unit of cost this whole tool exists to count, so it is drawn
        # LARGER than the ion that rests on it and the ion is drawn on top -- a gold frame
        # round the trap.  Sized off the layout's own `r_junc` alone it lands at two
        # pixels on a 180-node ring, and the headline number becomes invisible.
        r_i = max(2 * SS, int(round(self.L["r_ion"] * 0.92 * self.k)))
        r_j = max(int(round(r_i * 1.8)), int(round(self.L["r_junc"] * 0.6 * self.k)))
        r_s = max(SS, int(round(self.L["r_rest"] * 0.62 * self.k)))
        for nid, n in self.nodes.items():
            x, y = self.xy(nid)
            if n["deg"] >= 3 or n["kind"] == "junction":
                d.rectangle([x - r_j, y - r_j, x + r_j, y + r_j],
                            fill=rgb("junction"), outline=rgb("panel"), width=SS)
            else:
                d.ellipse([x - r_s, y - r_s, x + r_s, y + r_s], fill=rgb("grid_faint"))
        return img

    # -- one rendered instant ---------------------------------------------
    def draw(self, base, i, t, f_small, f_mono, caption=None):
        img = base.copy()
        d = ImageDraw.Draw(img, "RGBA")
        f = self.frames[i]
        walking = self.paths[i]
        pos = self.before[i] if walking else self.after[i]
        r = max(2 * SS, int(round(self.L["r_ion"] * 0.92 * self.k)))
        slot = r * 1.15

        here, stack = {}, {}
        for ion, at in pos.items():
            p = (self.point(walking[ion], t) if ion in walking
                 else self.xy(at) if at in self.nodes else None)
            if p is None:
                continue
            stack.setdefault(walking[ion][-1] if ion in walking else at, []).append(ion)
            here[ion] = p

        if f["type"] == "gate":
            for site in f.get("sites", ()):
                if site in self.nodes:
                    x, y = self.xy(site)
                    d.ellipse([x - r * 2.1, y - r * 2.1, x + r * 2.1, y + r * 2.1],
                              fill=(*rgb("active"), 120))

        flagged = set(f.get("ions", ())) | {i for p in f.get("pairs", ()) for i in p}
        # The motion halo marks the ions the machine singled out this step.  Under a
        # rigid rotation that is EVERY ion, and a halo on everything says nothing -- so
        # it is drawn only when the movement is selective.
        halo = walking and len(walking) < 0.6 * max(len(pos), 1)
        for site, ions in stack.items():
            ax, ay = self.axis.get(site, (1.0, 0.0))
            for j, ion in enumerate(sorted(ions)):
                x, y = here[ion]
                if len(ions) > 1:
                    off = (j - (len(ions) - 1) / 2) * slot
                    x, y = x + ax * off, y + ay * off
                moving = ion in walking and len(walking[ion]) > 1
                col = (rgb("accent") if ion in flagged else
                       rgb("anc") if self.ion_roles.get(ion) == "ancilla"
                       else rgb("data"))
                rr = r * (1.18 if moving else 1.0)
                if moving and halo:
                    d.ellipse([x - rr * 1.9, y - rr * 1.9, x + rr * 1.9, y + rr * 1.9],
                              fill=(*rgb("arrow"), 60))
                d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col,
                          outline=rgb("ion_stroke"), width=max(1, SS // 2))

        self.caption(d, i, f_small, f_mono, caption)
        if self.foot:
            self.footer(d, i, f_small, f_mono)
        return img.resize((self.W // SS, self.H // SS), Image.LANCZOS)

    # -- the two programmes, as listings ----------------------------------
    #
    # A caption saying "executing cx q[1],q[2]" is a fact with no context: it does not
    # show that the compiler is walking two FIXED programs, one derived from the other,
    # and that is the thing worth seeing.  So both are drawn as listings with their own
    # indices -- the hardware program by step number, the circuit by source line -- and
    # the cursor moves through them while the text stays put.

    def _hw_row(self, f):
        """One row of the hardware listing: what it is, and to whom."""
        if f["type"] == "gate":
            who = (", ".join("\u00b7".join(p) for p in (f.get("pairs") or ())[:2])
                   or ", ".join((f.get("ions") or ())[:3]))
            n = len(f.get("pairs") or ()) or len(f.get("ions") or ())
            return f"gate {f.get('gate') or ''}".strip(), who + (f"  x{n}" if n > 3 else "")
        if f["type"] == "simd":
            if f.get("shift"):
                return "simd rotate", f"{f['shift'][0]} {f['shift'][1]:+d}"
            mv = f.get("moves") or ()
            return f"simd {f.get('cls') or 'shuttle'}", ", ".join(m[0] for m in mv[:3])
        return f["type"], ", ".join((f.get("ions") or ())[:3])

    def _window(self, rows, cur):
        """`self.rows` entries centred on `cur`, clamped to the ends of the listing."""
        n = len(rows)
        if n <= self.rows:
            return 0, n
        lo = max(0, min(cur - self.rows // 2, n - self.rows))
        return lo, lo + self.rows

    def _pane(self, d, x0, x1, title, rows, cur, f_small, f_mono, tag="",
              hi_col=None):
        pad = int(9 * SS * self.W / (560 * SS))
        top = self.H - self.foot + int(6 * SS * self.W / (560 * SS))
        d.text((x0 + pad, top), title, font=f_small, fill=rgb("muted"))
        # NOT `hi`: the window unpack below binds that, and a colour that became
        # an integer row index failed loudly here rather than drawing something odd
        hi_col = hi_col or rgb("accent")
        if tag:
            d.text((x1 - pad - d.textlength(tag, font=f_small), top), tag,
                   font=f_small, fill=hi_col)
        lo, hi = self._window(rows, cur if cur >= 0 else 0)
        y = top + int(self.row_h * 1.25)
        for r in range(lo, hi):
            idx, op, args = rows[r]
            on = r == cur
            if on:
                d.rectangle([x0 + pad // 2, y - self.row_h * 0.12,
                             x1 - pad // 2, y + self.row_h * 0.92],
                            fill=(*hi_col, 55))
            col = hi_col if on else rgb("navy")
            d.text((x0 + pad, y), ("\u25b8" if on else " "), font=f_mono, fill=col)
            w_i = d.textlength("000", font=f_mono)
            d.text((x0 + pad + self.row_h, y),
                   idx.rjust(3), font=f_mono, fill=col if on else rgb("muted"))
            d.text((x0 + pad + self.row_h + w_i + self.row_h * 0.5, y), op,
                   font=f_mono, fill=col)
            if args:
                d.text((x0 + pad + self.row_h + w_i + self.row_h * 0.5
                        + d.textlength("simd shuttle  ", font=f_mono), y),
                       args, font=f_mono, fill=col if on else rgb("muted"))
            y += self.row_h

    def state_of(self, f):
        """What this instruction is doing about the circuit, and to which statement."""
        src, k = self.source, str(f["id"])
        for state, ids in (("executing", src["realises"].get(k)),
                           ("shuttling towards", src["toward"].get(k)),
                           ("clearing after", src.get("after", {}).get(k))):
            if ids:
                return state, ids
        return "", []

    def caption(self, d, i, f_small, f_mono, override=None):
        f = self.frames[i]
        pad, top = 9 * SS, 7 * SS
        if override:
            left, mid, right = override
        else:
            left = self.arch.name
            mid = {"simd": f.get("cls") or "shuttle", "gate": f.get("gate") or "gate",
                   "init": "load", "cool": "cool", "measure": "measure",
                   "reset": "reset"}.get(f["type"], f["type"])
            if f.get("shift"):
                mid = f"{mid}  {f['shift'][0]} {f['shift'][1]:+d}"
            cost = sum(fr.get("cost", 0) for fr in self.frames[:i + 1])
            right = f"step {i + 1}/{len(self.frames)}   cost {cost:,.0f}"
        d.text((pad, top), left, font=f_small, fill=rgb("navy"))
        mx = max(self.W * 0.32, pad * 2 + d.textlength(left, font=f_small))
        d.text((mx, top), mid, font=f_mono, fill=rgb("accent"))
        d.text((self.W - pad - d.textlength(right, font=f_mono), top), right,
               font=f_mono, fill=rgb("muted"))

    def footer(self, d, i, f_small, f_mono):
        f = self.frames[i]
        state, ids = self.state_of(f)
        cur_op = -1
        if ids:
            want = self.src_line.get(ids[0], {}).get("line")
            for r, (idx, _, _) in enumerate(self.circ):
                if idx == str(want):
                    cur_op = r
                    break
        # RUNNING a statement and merely fetching ions for it are different events, and
        # a reader watching the cursor should not have to read the label to tell them
        # apart.  `accent` and `arrow` are two oranges and did not: the pair has to be
        # two HUES.  Orange is the gate firing; teal is the machine still travelling.
        hi = rgb("accent") if state == "executing" else rgb("teal")
        self._pane(d, 0, self.W // 2, "hardware program", self.hw, i,
                   f_small, f_mono, tag=f"{i + 1}/{len(self.hw)}", hi_col=hi)
        self._pane(d, self.W // 2, self.W, "circuit", self.circ, cur_op,
                   f_small, f_mono, tag=state, hi_col=hi)

# ----------------------------------------------------------------- writing a GIF


def save_gif(imgs, durs, out, colors, note="", pin=()):
    """One shared palette, `disposal=1`, and let the encoder store only what moved.

    A per-frame ADAPTIVE palette makes every frame a full re-encode against a palette of
    its own: 733 KB for 28 frames of the grid, against 84 KB for the same 28 frames here.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # THE PALETTE IS BUILT, NOT SAMPLED.  Median cut allocates entries by pixel
    # population, and every colour that carries meaning here is a few hundred pixels
    # against a full-frame background: the gold of a junction square merged into the pink
    # of the rail beside it, and the green of a spur into the grey of the panel.  So the
    # role colours go in verbatim and median cut gets what is left -- which is only ever
    # the anti-aliasing.  The strip it runs on spans the whole clip rather than frame 0,
    # for the same reason: the design sweep OPENS on a ring with no spurs at all.
    probe = [imgs[int(i * (len(imgs) - 1) / 11)] for i in range(min(12, len(imgs)))]
    strip = Image.new("RGB", (imgs[0].width, imgs[0].height * len(probe)))
    for i, im in enumerate(probe):
        strip.paste(im, (0, i * imgs[0].height))
    fixed = [rgb(k) for k in ROLE_COLOURS] + list(pin)
    rest = max(8, colors - len(fixed))
    pal = strip.quantize(colors=rest, method=Image.MEDIANCUT).getpalette()[:3 * rest]
    master = Image.new("P", (1, 1))
    master.putpalette(pal + [c for col in fixed for c in col])
    frames = [im.quantize(palette=master, dither=Image.NONE) for im in imgs]
    frames[0].save(out, save_all=True, append_images=frames[1:], loop=0,
                   duration=durs, optimize=True, disposal=1)
    print(f"{out.name:26s} {len(frames):4d} frames  "
          f"{imgs[0].width:4d}x{imgs[0].height:<4d} {out.stat().st_size / 1024:8.1f} KB"
          f"   {note}")
    return out


def render(arch_name, program, out, *, model="corrected", width=560, start=0,
           n=40, sub=3, ms=90, hold=800, colors=64):
    clip = Clip(*load_pair(arch_name, program, model), width=width)
    lo = max(0, min(start, len(clip.frames) - 1))
    hi = min(len(clip.frames), lo + n)
    base = clip.board()
    f_small = font(int(13 * SS * width / 560))
    f_mono = font(int(12 * SS * width / 560), mono=True)

    imgs, durs = [], []
    for i in range(lo, hi):
        # sub-steps per HOP, not per instruction: one `rotate` instruction is a single
        # frame that moves every ion a quarter of the way round the loop, and three
        # samples of it is a jump-cut rather than a shuttle.
        hops = max((len(p) - 1 for p in clip.paths[i].values()), default=0)
        steps = min(max(1, hops * sub), 36)
        for s in range(steps):
            imgs.append(clip.draw(base, i, (s + 1) / steps, f_small, f_mono))
            durs.append(ms if steps > 1 else int(ms * 2.4))
    durs[-1] = hold
    return save_gif(imgs, durs, out, colors,
                    f"({arch_name} x {program}, {len(clip.frames)} instructions)")


def render_compiled(tsir, qasm, out, *, arch_name=None, arch_path=None, cert=None,
                    model="corrected", width=880, start=0, n=14, sub=3, ms=110,
                    hold=1500, colors=64, max_frames=20000, rows=9, stage_h=None):
    """The README's compiler figure: a compiled programme, and the circuit beside it.

    Not a mock-up and not a second renderer.  The frames are `build_view_model`'s, the
    same ones the emitted HTML page animates, and the circuit join is the same payload
    the page's Circuit pane reads -- built by `qccd.ir.source_map`, which refuses if the
    compiler's per-instruction attribution disagrees with its own certificate.  So this
    clip cannot show a correspondence the page would not show.
    """
    import json

    from qccd.ir.source_map import build as build_source
    from qccd.ir.tsir import TSIR

    tsir, qasm = Path(tsir), Path(qasm)
    prog = TSIR.load(tsir)
    stem = arch_name or Path(str(prog.arch_spec)).name.split(".")[0]
    # a demo device is generated into `Compiler/build/`, not checked into `arch/`, so the
    # path can be given outright rather than resolved from a name
    arch = load(Path(arch_path)) if arch_path else load(ARCH / f"{stem}.arch.json")
    cert = Path(cert) if cert else _cert_beside(tsir)
    source = build_source(prog, json.loads(cert.read_text(encoding="utf-8")), qasm)

    m = deck_model() if model == "deck" else corrected_model()
    res = verify(prog, arch, m, check_metrics=False).result
    # the whole programme, not the default 4 000: the caption prints `step i/N`, and a
    # truncated N is a wrong number on the face of the clip
    clip = Clip(arch, prog, res, m, width=width, source=source,
                max_frames=max_frames, rows=rows, stage_h=stage_h)

    lo = max(0, min(start, len(clip.frames) - 1))
    hi = min(len(clip.frames), lo + n)
    base = clip.board()
    f_small = font(int(13 * SS * width / 560))
    f_mono = font(int(12 * SS * width / 560), mono=True)

    imgs, durs = [], []
    for i in range(lo, hi):
        hops = max((len(p) - 1 for p in clip.paths[i].values()), default=0)
        steps = min(max(1, hops * sub), 36)
        for s in range(steps):
            imgs.append(clip.draw(base, i, (s + 1) / steps, f_small, f_mono))
            # a step that DISCHARGES a statement is the one worth reading, so it is held
            # about twice as long as a shuttle: the clip is a document, not a stopwatch
            slow = clip.frames[i]["type"] != "simd"
            durs.append((ms if steps > 1 else int(ms * 2.4)) * (2 if slow else 1))
    durs[-1] = hold
    return save_gif(imgs, durs, out, colors,
                    f"({stem} x {prog.name}, {len(clip.frames)} instructions, "
                    f"{len(source['ops'])} circuit statements)")


def _cert_beside(tsir: Path) -> Path:
    for suffix in (".cooled.tsir.json", ".tsir.json"):
        if tsir.name.endswith(suffix):
            c = tsir.with_name(tsir.name[: -len(suffix)] + ".qcert.json")
            if c.exists():
                return c
    raise FileNotFoundError(
        f"no certificate beside {tsir}; compile it first, or pass --cert")


# ------------------------------------------------------- designing, as a clip
#
# The README's "design" figure.  Not a mock-up: each variant is a real
# `Machine.ring(...)` device, priced by the real hardware report, and the loop it draws
# is the one `rotate` would turn.


def render_design(out, *, width=880, colors=48, ms=1400):
    from qccd.api import Machine
    from qccd.cost.hardware import hardware_report

    verticals = [0, 4, 8, 12, 18, 24, 36]
    clips, layout = [], None
    for v in reversed(verticals):          # busiest first: its fit maps them all
        m = Machine.ring(width=72, height=2, verticals=v, name=f"ring144_{v}v")
        prog = build(m.arch, "rotate", 1)
        model = corrected_model()
        res = verify(prog, m.arch, model, check_metrics=False).result
        clip = Clip(m.arch, prog, res, model, width=width, layout=layout)
        layout = layout or clip.L
        hw = hardware_report(m.arch)
        s = m.arch.device.summary()
        clips.append((v, clip, s, hw))
    clips.reverse()

    f_small = font(int(13 * SS * width / 560))
    f_mono = font(int(12 * SS * width / 560), mono=True)
    imgs, durs = [], []
    for v, clip, s, hw in clips:
        base = clip.board()
        cap = (f"Machine.ring(72, 2, verticals={v})",
               f"{s['n_junction_nodes']} junctions",
               f"{v} ancillas   {s['n_nodes']} nodes   {hw.dacs} DACs")
        imgs.append(clip.draw(base, len(clip.frames) - 1, 1.0, f_small, f_mono, cap))
        durs.append(ms)
    durs[-1] = ms + 600
    return save_gif(imgs, durs, out, colors, "(Machine.ring, verticals 0 -> 36)")


# ------------------------------------------------------ evaluating, as a clip
#
# The README's "evaluate" figure is a real terminal session: every line below is
# captured by RUNNING the command at render time, so a figure that disagrees with the
# tool is not a thing that can be committed.

TERM = {"bg": (15, 20, 32), "dim": (110, 126, 155), "text": (208, 217, 233),
        "cmd": (255, 255, 255), "prompt": (91, 124, 250), "ok": (61, 176, 118),
        "bad": (231, 106, 96), "num": (246, 195, 74)}

EVALUATE = [
    "python -m qccd devices",
    "python -m qccd run ring144_24v --program deck --model deck",
]


def capture(cmd):
    import subprocess
    r = subprocess.run([sys.executable, *cmd.split()[1:]], cwd=ROOT,
                       capture_output=True, text=True)
    return (r.stdout + r.stderr).rstrip("\n").split("\n")


def line_colour(s):
    if s.startswith("  rules failed") and "(none)" not in s:
        return TERM["bad"]
    if s.startswith("  rules passed") or "(none)" in s:
        return TERM["ok"]
    if s.startswith("---") or s.startswith("DAC count") or s.startswith("a broadcast"):
        return TERM["dim"]
    return TERM["text"]


def render_terminal(out, *, width=900, colors=32, cps=4, ms=55, lines_per_frame=2):
    session = [(c, capture(c)) for c in EVALUATE]
    body = sum(len(o) for _, o in session) + 2 * len(session) + 1

    f = font(int(12.5 * SS * width / 900), mono=True)
    lh = int(round(f.getbbox("Ag")[3] * 1.52)) or 16 * SS
    pad = 14 * SS
    W = width * SS
    H = pad * 2 + lh * body

    def canvas(done, typing):
        img = Image.new("RGB", (W, H), TERM["bg"])
        d = ImageDraw.Draw(img)
        y = pad
        for kind, text in done:
            if kind == "cmd":
                d.text((pad, y), "$", font=f, fill=TERM["prompt"])
                d.text((pad + 2 * f.getlength("$"), y), text, font=f, fill=TERM["cmd"])
            elif kind == "out":
                d.text((pad, y), text, font=f, fill=line_colour(text))
            y += lh
        if typing is not None:
            d.text((pad, y), "$", font=f, fill=TERM["prompt"])
            d.text((pad + 2 * f.getlength("$"), y), typing + "█", font=f,
                   fill=TERM["cmd"])
        return img.resize((W // SS, H // SS), Image.LANCZOS)

    done, imgs, durs = [], [], []
    for cmd, output in session:
        for i in range(0, len(cmd) + 1, cps):
            imgs.append(canvas(done, cmd[:i]))
            durs.append(ms)
        imgs.append(canvas(done, cmd))
        durs.append(360)
        done.append(("cmd", cmd))
        for i in range(0, len(output), lines_per_frame):
            done.extend(("out", ln) for ln in output[i:i + lines_per_frame])
            imgs.append(canvas(done, None))
            durs.append(150)
        done.append(("gap", ""))
        imgs.append(canvas(done, None))
        durs.append(600)
    durs[-1] = 2600
    return save_gif(imgs, durs, out, colors, "(a real terminal session)",
                    pin=tuple(TERM.values()))


#: the README gallery -- one clip per architecture in `arch/`, each cut to the window
#: that shows what makes that design different.  `stem` is the `arch/*.arch.json` file;
#: `out` is the GIF name the README links to.
GALLERY = [
    dict(stem="ring144_24v", program="deck", model="deck", start=4, n=20,
         sub=3, width=880),
    dict(stem="cyclone_base", program="rotate", n=2, sub=2, width=880),
    dict(stem="cyclone_base", program="oddeven", out="cyclone_oddeven", n=26,
         sub=6, width=880),
    dict(stem="cyclone_dual_loop", program="rotate", n=2, sub=2, width=880),
    dict(stem="h2_racetrack", program="rotate", n=2, sub=3, width=760),
    dict(stem="ladder_2x72", program="walk:20", n=18, sub=3, width=880),
    dict(stem="grid9x9", program="walk:8", n=16, sub=3, width=520),
    dict(stem="deck_unit_cell", program="walk:8", n=16, sub=3, width=520),
    dict(stem="chain", program="walk:12", n=18, sub=3, width=880),
    dict(stem="stationary_chain", program="walk", n=4, sub=4, width=360, ms=160),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-d", "--device")
    ap.add_argument("-p", "--program", default="walk")
    ap.add_argument("-m", "--model", default="corrected")
    ap.add_argument("-o", "--out")
    ap.add_argument("--width", type=int, default=560)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("-n", "--frames", type=int, default=40)
    ap.add_argument("--sub", type=int, default=3)
    ap.add_argument("--ms", type=int, default=90)
    ap.add_argument("--colors", type=int, default=48)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--design", action="store_true",
                    help="the design-parameter sweep clip only")
    ap.add_argument("--evaluate", action="store_true",
                    help="the terminal-session clip only")
    # The compiler clips are NOT part of `--all`: they need a compiled programme, which
    # needs the OCaml toolchain, and `Compiler/bridge/micro_demo.py --gif` is where that
    # compilation already happens.  This flag renders one from artifacts that exist.
    ap.add_argument("--compiled", action="store_true",
                    help="one compiled programme, with the circuit statement each "
                         "instruction is discharging (needs --tsir and --qasm)")
    ap.add_argument("--tsir")
    ap.add_argument("--qasm")
    ap.add_argument("--cert", default=None)
    ap.add_argument("--arch-file", default=None,
                    help="the device, if it is not in arch/")
    ap.add_argument("--stage", type=int, default=None,
                    help="cap the stage at this many pixels tall")
    ap.add_argument("--rows", type=int, default=9,
                    help="listing rows in each of the two panes")
    a = ap.parse_args(argv)

    if a.all or a.design:
        render_design(IMG / "design.gif", colors=a.colors)
    if a.all or a.evaluate:
        render_terminal(IMG / "evaluate.gif")
    if a.compiled:
        if not (a.tsir and a.qasm):
            ap.error("--compiled needs --tsir and --qasm")
        render_compiled(a.tsir, a.qasm, a.out or IMG / "compiled.gif", cert=a.cert,
                        arch_path=a.arch_file, start=a.start,
                        n=a.frames, width=a.width, colors=a.colors,
                        stage_h=a.stage, rows=a.rows)
    if (a.design or a.evaluate or a.compiled) and not a.all:
        return 0
    if a.all or not a.device:
        for spec in GALLERY:
            spec = dict(spec)
            stem, out = spec.pop("stem"), spec.pop("out", None)
            try:
                render(stem, spec.pop("program"), IMG / f"{out or stem}.gif",
                       colors=a.colors, **spec)
            except FileNotFoundError as exc:
                # the deck clip is built from the standalone visualizer artifact, which
                # is third-party source material and not in the repo: a clone without it
                # should skip that one clip, not fail the whole gallery
                print(f"{out or stem:26s} skipped: {exc}")
        return 0
    render(a.device, a.program, a.out or IMG / f"{a.device}.gif", model=a.model,
           width=a.width, start=a.start, n=a.frames, sub=a.sub, ms=a.ms,
           colors=a.colors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
