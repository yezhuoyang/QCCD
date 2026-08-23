"""Where every mark goes -- computed in Python so it can be tested, not guessed in JS.

The old page fitted every device into a fixed `1000x460` box with one isotropic scale and
then sized every mark from the *node count* (`300/sqrt(n+1)`, clamped to 13). Those two
numbers are unrelated, so a 71:1 racetrack was drawn as a 912x13 px hairline with 21 px
ions on a 12.8 px pitch -- ions overlapping their neighbours by 66%, rails wider than the
gap between them, and a 1-D chain collapsing to *one pixel per trap* because a degenerate
axis contributed the literal `1` to a `Math.min` over pixels-per-unit.

This module replaces both with three measured quantities and one rule:

* **the lattice** -- `ux`, `uy`, the minimum positive gap between distinct x (resp. y)
  coordinates, and `gd`, the true minimum nearest-neighbour distance in data units;
* **the fit** -- a per-axis scale, because 10 of the 12 shipped devices carry 12-72
  distinct x coordinates against 1-4 distinct y coordinates, and an isotropic fit hands
  the entire vertical budget to a one-unit-tall band. Anisotropy is safe here and only
  here: every segment in every shipped device is axis-aligned, so nothing is skewed. The
  moment a diagonal segment appears, or the bounding box is squarer than 2:1, the fit
  locks back to isotropic;
* **`g`, the minimum nearest-neighbour distance in *drawn pixels*** -- measured after the
  map, so it is exact under anisotropy too.

Then: **every drawn dimension is a fixed fraction of `g`.** `2*r_ion = 0.68*g < g` is a
strict inequality in units of `g`, so it holds at any scale -- which is what makes overlap
structurally impossible rather than accidentally absent. A count-derived radius can never
promise that.

`compute_layout` returns ~40 scalars (under 1 KB of JSON against a 420 KB page). The page
reads them and does no layout arithmetic of its own.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

from .theme import GEOMETRY as _G

__all__ = [
    "compute_layout",
    "COORD_MAX",
    "min_nearest_neighbour",
    "pad_tiling",
    "site_length",
    "W_MAX",
    "W_MIN",
    "H_MAX",
    "H_MIN",
    "PITCH_CAP",
    "K_ANISO",
    "ISO_ASPECT",
]

# ---- the canvas ---------------------------------------------------------------
W_MAX = 1600.0     # viewBox width ceiling, user units
W_MIN = 280.0
H_MAX = 900.0      # viewBox height ceiling
H_MIN = 132.0
PAD_A, PAD_B = 2.2, 10.0    # margin = PAD_A*r_ion + PAD_B, a real reserved margin

# ---- the fit ------------------------------------------------------------------
PITCH_CAP = 72.0   # px per lattice step: never zoom in past this, so a 2-node device
#                    does not become a full-screen pair of blobs
K_ANISO = 12.0     # hard ceiling on sy/sx (and sx/sy)
ISO_ASPECT = 2.0   # a bbox within [1/2, 2] is square enough that stretching it lies

# ---- marks, all as fractions of g ---------------------------------------------
# The shape ratios come from `theme.GEOMETRY`, measured off the deck's vector slides, so
# there is ONE home for them.  They used to be re-guessed here, which is how the ions
# ended up half again the size the deck draws them at.
#
# Ion radius is the one place we knowingly exceed the deck: its own figures put a plain
# ion at 0.13 of the pitch in DIAMETER, which is a 1.4 px dot on the shipped ring.  We
# take the deck's ratio as a radius instead -- the same proportion between resting and
# active ions, roughly 1.8x the deck's absolute size, still comfortably clear of the
# 0.5*g that overlap would require.
K_ION = _G["ION_D_FRAC_ACTIVE"] + 0.065   # 0.240 -- in flight, or in the current gate
K_REST = _G["ION_D_FRAC"]                 # 0.130 -- a resting spectator, a bead on the rail
R_ION_MAX = 26.0
R_ION_MIN = 3.0

_EPS = 1e-9

# --------------------------------------------------------------- portable arithmetic
#
# This module is MIRRORED IN JAVASCRIPT (`qccd/viz/engine.js`) so the page can re-lay-out
# a dragged device without a server, and `tests/test_engine_parity.py` asserts the two
# agree BIT FOR BIT -- tolerance zero, not 1e-9.  That bar is only reachable because the
# three idioms below replaced three that CPython and V8 disagree about.  Each was
# measured, not guessed; each broke a real device.  Do not put `math.hypot`, the builtin
# `sum()` over floats, or `round()` back into this file: `test_engine_parity.py` walks
# this module's AST looking for exactly those three calls, with the reason in the
# assertion message, so the mistake fails at the point it is made rather than three
# commits later when someone happens to run the fuzz.

_P10 = tuple(10.0 ** i for i in range(13))


def _hyp(dx: float, dy: float) -> float:
    """`sqrt(dx*dx + dy*dy)`, deliberately NOT `math.hypot`.

    `*`, `+` and `sqrt` are correctly rounded and identically specified by IEEE-754 in
    CPython and in V8, so this form is bit-reproducible in the browser.  `math.hypot` is
    not: CPython's and V8's implementations are both under 1 ulp but miss in opposite
    directions, and that ulp flows through `min_nearest_neighbour`'s `d < best` into `g`
    -- from which every mark on the page is a fraction.  Measured: mirroring `math.hypot`
    with `Math.hypot` was a WORSE mirror than this (6,579 divergent scalars against
    3,569) on a 2,644-device corpus.

    The sqrt form overflows above ~1e154 where `hypot` would not; `compute_layout`
    rejects coordinates above 1e6 on ingest, which is nine orders clear.
    """
    return math.sqrt(dx * dx + dy * dy)


def _fsum(vals) -> float:
    """Left-to-right accumulation, deliberately NOT the builtin `sum()`.

    CPython >= 3.12 gives `sum()` Neumaier compensation and no JS `for` loop reproduces
    it.  On a 160-node ring the `_bows` centroid came out `sum(xs) = 128000.0` against a
    naive loop's `127999.99999999999`; the collinear tie-break `((mx-cx)*nx + (my-cy)*ny)
    >= 0` then saw `-0.0` in Python and `-1.14e-13` in JS and bowed segment V28 the wrong
    way, +13.241 against -13.241.  A sign flip, not an ulp -- the spur bowed INTO the loop
    it exists to route around.
    """
    a = 0.0
    for v in vals:
        a += v
    return a


def _q(v: float, nd: int | None = None) -> float:
    """`floor(v * 10**nd + 0.5) / 10**nd` -- the quantizer `pad_tiling` already used.

    Python's `round()` is half-to-EVEN on the exact binary value and JS has no equivalent
    (`Number(v.toFixed(nd))` is half-away-from-zero, `Math.round` is half-up and differs
    from `round()` on negatives too).  Ties are reachable and did occur unprompted:
    `round(24.28125, 4)` is 24.2812 but `(24.28125).toFixed(4)` is "24.2813", on `sx`,
    `sy` and `g`.  This form is expressible identically in both languages.
    """
    if nd is None:
        return math.floor(v + 0.5)
    s = _P10[nd]
    return math.floor(v * s + 0.5) / s


#: Coordinates outside this are refused on ingest.  `_hyp` is exact for everything a
#: device can legitimately contain and overflows only above ~1e154; a drag editor,
#: however, accepts whatever a text field contains.  Refusing loudly at the boundary is
#: better than returning `inf` from a layout.
COORD_MAX = 1e6


def _coord(v, what: str) -> float:
    """Ingest one coordinate: finite, and inside the range `_hyp` is exact over."""
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{what} is not a finite number: {v!r}")
    if abs(f) > COORD_MAX:
        raise ValueError(
            f"{what} = {f!r} is outside +/-{COORD_MAX:g}; the layout measures distances "
            f"as sqrt(dx*dx+dy*dy) so it can be reproduced exactly in the browser, and "
            f"that form is only exact well inside this range"
        )
    return f




# --------------------------------------------------------------- measurement


def min_nearest_neighbour(pts: Sequence[tuple[float, float]]) -> float:
    """Exact minimum nearest-neighbour distance over `pts`; 0.0 if fewer than two.

    Sweep by x with the standard early break: once `pts[j].x - pts[i].x` exceeds the best
    distance found so far, no later `j` can beat it. Coincident points are skipped rather
    than returning 0 -- two nodes at one position are a device-authoring question, not a
    layout one, and returning 0 would make every derived size collapse.
    """
    n = len(pts)
    if n < 2:
        return 0.0
    order = sorted(pts)
    best = math.inf
    for a in range(n):
        xi, yi = order[a]
        for b in range(a + 1, n):
            dx = order[b][0] - xi
            if dx >= best:
                break
            d = _hyp(dx, order[b][1] - yi)
            if _EPS < d < best:
                best = d
    return 0.0 if math.isinf(best) else best


def _lattice_step(vals: Iterable[float]) -> float:
    """Minimum positive gap between distinct coordinates: the lattice pitch on one axis."""
    uniq = sorted({_q(v, 9) for v in vals})
    if len(uniq) < 2:
        return 0.0
    return min(uniq[i + 1] - uniq[i] for i in range(len(uniq) - 1))


def _sample(pts: Sequence[tuple[float, float]], cap: int = 6000):
    """Subsample deterministically for the nearest-neighbour scan on huge devices."""
    if len(pts) <= cap:
        return pts
    stride = len(pts) / cap
    return [pts[int(i * stride)] for i in range(cap)]


# --------------------------------------------------------------- the fit


def _fit(dx: float, dy: float, ux: float, uy: float, pad: float,
         iso: bool) -> tuple[float, float]:
    """px per data unit on each axis, given a margin."""
    avail_w = max(W_MAX - 2 * pad, 80.0)
    avail_h = max(H_MAX - 2 * pad, 80.0)
    sx = avail_w / dx if dx > _EPS else math.inf
    sy = avail_h / dy if dy > _EPS else math.inf
    # never zoom in past one lattice step = PITCH_CAP pixels
    if ux > _EPS:
        sx = min(sx, PITCH_CAP / ux)
    if uy > _EPS:
        sy = min(sy, PITCH_CAP / uy)
    if not math.isfinite(sx) and not math.isfinite(sy):
        sx = sy = PITCH_CAP           # a single node: any scale will do
    elif not math.isfinite(sx):
        sx = sy                       # degenerate x: never contribute a constant
    elif not math.isfinite(sy):
        sy = sx                       # degenerate y: ditto (this is the chain(n) bug)
    if iso:
        sx = sy = min(sx, sy)
    else:
        sy = min(sy, K_ANISO * sx)
        sx = min(sx, K_ANISO * sy)
    return sx, sy


def site_length(cap: int, g: float) -> float:
    """Length of a trapping site's bar along the trap axis, from its ion capacity.

    A high-capacity trap is drawn as a visibly *longer* well -- that is the encoding a
    reader gets for free, before counting anything. Capped at 0.88*g so two adjacent
    sites never touch.
    """
    m = max(1, min(int(cap or 0), 6))
    return min(0.88 * g, (0.30 + 0.15 * m) * g)


def pad_tiling(length: float, g: float) -> tuple[int, float, float]:
    """`(k, pitch, pad_len)` for the DC electrode tiling along one segment.

    Pad count comes from the segment's *drawn length*, never from the node count: the old
    page put three pads on a one-pixel segment and one pad on a 46-pixel one. The pitch is
    re-derived as `length/k` so the tiling closes exactly on both nodes, and the pad fills
    72% of it, leaving the 28% gap that is what actually reads as discrete.
    """
    if length <= _EPS or g <= _EPS:
        return 0, 0.0, 0.0
    # floor(x + 0.5), not Python's round(): the page uses Math.round, which
    # rounds a tie up where Python rounds it to even
    k = max(1, min(12, int(math.floor(length / (0.50 * g) + 0.5))))
    pitch = length / k
    return k, pitch, 0.72 * pitch



def _point_segment(p, a, b):
    """`(distance, signed offset along the segment normal)` from `p` to segment `a-b`."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return _hyp(p[0] - a[0], p[1] - a[1]), 0.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    qx, qy = a[0] + t * dx, a[1] + t * dy
    h = math.sqrt(l2)
    nx, ny = -dy / h, dx / h
    return _hyp(p[0] - qx, p[1] - qy), (p[0] - a[0]) * nx + (p[1] - a[1]) * ny


def _bows(nodes, segments, pos, g, pad, clearance=0.0, need=0.0):
    """Segments that would otherwise be drawn straight *through* a node they do not touch.

    The shipped ring's two corner docks sit exactly on the end caps (`A0` on `E143`,
    `A72` on `E71`) and the dual-loop Cyclone's A-loop end caps are declared length 1 but
    are three units long, so drawn as chords they pass through four D-loop nodes. Drawing
    that literally says the loops intersect, which they do not. Those segments are bowed
    outward into the margin as a quadratic instead -- which also makes the deck's "a
    corner segment costs three hops" visible as a visibly longer path.

    Returns `{segment id: signed midpoint offset in px}`, empty for every device that does
    not need it (seven of the nine shipped ones).
    """
    if g <= _EPS or not pos:
        return {}
    cx = _fsum(p[0] for p in pos.values()) / len(pos)
    cy = _fsum(p[1] for p in pos.values()) / len(pos)
    # Reserve room for what rides the bowed curve -- the ion and the DC pads -- or the
    # apex pushes them outside the viewBox.  Measured before this: an ion drawn 17 px
    # off the edge of `verify/05`, and four pad rectangles past the right margin.
    # at least what it takes to clear an ion sitting at the apex, never more than the
    # margin can hold -- the fixed point above sizes the margin so both can be true
    amount = min(0.9 * g, max(need, pad - clearance))
    out: dict[str, float] = {}
    for s in segments:
        sid = s.get("id")
        a, b = pos.get(s["a"]), pos.get(s["b"])
        if sid is None or a is None or b is None:
            continue
        if _hyp(b[0] - a[0], b[1] - a[1]) <= _EPS:
            continue
        worst = None
        for n in nodes:
            if n["id"] == s["a"] or n["id"] == s["b"]:
                continue
            d, side = _point_segment(pos[n["id"]], a, b)
            if d < 0.55 * g and (worst is None or d < worst[0]):
                worst = (d, side)
        if worst is None:
            continue
        if abs(worst[1]) > 1e-6:
            sign = -1.0 if worst[1] > 0 else 1.0     # bow away from the node in the way
        else:                                        # exactly collinear: bow outward
            h = _hyp(b[0] - a[0], b[1] - a[1])
            nx, ny = -(b[1] - a[1]) / h, (b[0] - a[0]) / h
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            sign = 1.0 if ((mx - cx) * nx + (my - cy) * ny) >= 0 else -1.0
        out[str(sid)] = _q(sign * amount, 3)
    return out


# --------------------------------------------------------------- the engine


def compute_layout(nodes: Sequence[Mapping], segments: Sequence[Mapping] = (),
                   *, raw: bool = False) -> dict:
    """Everything the page needs to place a mark, as ~40 plain scalars.

    `raw=True` skips the OUTPUT quantizer only -- the internal ones (`_lattice_step`'s
    nd=9, `_bows`'s nd=3) stay, because they are load-bearing rather than cosmetic: `ux`
    feeds `PITCH_CAP / ux` and therefore `sx`, and every mark is a fraction of the `g` that
    comes out of it.  It exists for `tests/test_engine_parity.py`, which diffs the
    unquantized intermediates as well as the shipped scalars: rounding to 3-4 decimals is a
    very effective mask, so a round-only harness would shrug at drift that is one edit away
    from being visible.  `qccd/viz/engine.js` takes the same flag and means the same thing
    by it -- a `raw` that differed between the two would make the harness compare two
    different questions.
    """
    R = (lambda v, nd=None: v) if raw else _q
    pts = [(_coord(n["x"], f"node {n.get('id', '?')} x"),
            _coord(n["y"], f"node {n.get('id', '?')} y")) for n in nodes]
    if not pts:
        pts = [(0.0, 0.0)]
    x0 = min(p[0] for p in pts)
    x1 = max(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    y1 = max(p[1] for p in pts)
    dx, dy = x1 - x0, y1 - y0
    ux = _lattice_step(p[0] for p in pts)
    uy = _lattice_step(p[1] for p in pts)
    sample = _sample(pts)
    gd = min_nearest_neighbour(sample)
    if gd <= _EPS:
        gd = max(dx, dy) or 1.0

    # anisotropy is only ever applied to an axis-aligned device: nothing to skew
    axis_aligned = True
    by_id = {n["id"]: (float(n["x"]), float(n["y"])) for n in nodes}  # validated above
    for s in segments:
        a, b = by_id.get(s["a"]), by_id.get(s["b"])
        if a is None or b is None:
            continue
        if abs(a[0] - b[0]) > _EPS and abs(a[1] - b[1]) > _EPS:
            axis_aligned = False
            break
    iso = (not axis_aligned) or dy <= _EPS or dx <= _EPS or \
        (1.0 / ISO_ASPECT) <= (dx / dy) <= ISO_ASPECT

    # ---- fixed point: the margin depends on the ion radius, which depends on the
    # scale, which depends on the margin. The loop gain is well under 1 (it is
    # 2*PAD_A*K_ION*ux/dx <= 0.1 for every shipped device, and exactly 0 whenever the
    # pitch cap binds), so this settles in two passes; four are free.
    pad = 24.0
    sx = sy = g = 0.0
    for _ in range(5):
        sx, sy = _fit(dx, dy, ux, uy, pad, iso)
        g = min_nearest_neighbour([(p[0] * sx, p[1] * sy) for p in sample])
        if g <= _EPS:
            g = gd * max(sx, sy)
        r = min(R_ION_MAX, max(min(R_ION_MIN, 0.45 * g), K_ION * g))
        rr = min(r, max(min(1.6, 0.30 * g), K_REST * g))
        # A bow exists to route a segment AROUND a node it does not touch -- the shipped
        # ring's end caps pass straight over its two corner docks. To be worth drawing it
        # has to clear the ion resting at that node, so the margin has to hold the bow,
        # the flying ion and the DC pads that ride the curve. Sizing the margin from the
        # ion alone left the bow clamped to 18 px where it needed 30, and the two circles
        # went through each other at the apex.
        bow_need = r + rr + 3.0
        pad_need = max(0.30 * g + 0.10 * g, r) + bow_need
        pad = math.ceil(max(PAD_A * r + PAD_B, pad_need))
    sx, sy = _fit(dx, dy, ux, uy, pad, iso)
    g = min_nearest_neighbour([(p[0] * sx, p[1] * sy) for p in sample])
    if g <= _EPS:
        g = gd * max(sx, sy)

    W = min(W_MAX, max(W_MIN, _q(dx * sx + 2 * pad)))
    H = min(H_MAX, max(H_MIN, _q(dy * sy + 2 * pad)))
    # centre the REAL extent, not the padded span
    ox = (W - dx * sx) / 2.0 - x0 * sx
    oy = (H - dy * sy) / 2.0 - y0 * sy

    # The legibility floor must never beat the no-overlap guarantee: on a dense device
    # (chain(288) gives g = 5.5 px) an unconditional 3 px floor put 2*r_ion above g and
    # reinstated exactly the overlap this module exists to make impossible.  Clamp the
    # floor against the measured gap; the page offers zoom when it bites.
    r_ion = min(R_ION_MAX, max(min(R_ION_MIN, 0.45 * g), K_ION * g))
    r_rest = min(r_ion, max(min(1.6, 0.30 * g), K_REST * g))
    site_t = _G["RAIL_W_FRAC"] * 2.4 * g          # deck rail thickness, scaled to a bar
    pos = {n["id"]: (ox + float(n["x"]) * sx, oy + float(n["y"]) * sy) for n in nodes}

    lay = {
        # -- the map -------------------------------------------------------
        "sx": R(sx, 4), "sy": R(sy, 4),
        "ox": R(ox, 4), "oy": R(oy, 4),
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "W": int(W), "H": int(H), "pad": int(pad),
        "wide": bool(H > 0 and W / H >= 3.0),
        # -- what was measured ---------------------------------------------
        "g": R(g, 4), "gd": R(gd, 6),
        "ux": R(ux, 6), "uy": R(uy, 6),
        "iso": bool(iso), "axis_aligned": bool(axis_aligned),
        "n": len(nodes),
        # -- every mark, as a fraction of g --------------------------------
        "r_ion": R(r_ion, 3),          # in flight / in the current gate
        "r_rest": R(r_rest, 3),        # a resting spectator ion
        # How far an ion detours sideways to get PAST a trap-mate. Sized to clear a
        # resting neighbour outright, so the two circles never touch. It is deliberately
        # a visible detour: two ions exchanging order inside one trap is a swap, and a
        # swap is not free -- drawing it as a straight line would misreport the physics.
        "swap_bow": R(min(0.62 * g, 1.9 * (r_ion + r_rest)), 3),
        "r_junc": R(0.30 * g, 3),      # half-side of the junction square
        "r_active": R(0.46 * g, 3),    # 'site in play' highlight
        "site_t": R(site_t, 3),        # trapping-site bar thickness
        "site_max": R(0.88 * g, 3),
        "slot_r": R(0.085 * g, 3),   # one ring per ion the site holds
        # the deck's own rail and rung widths, rather than a guess: with the ions now at
        # the deck's proportions too, a 0.15*g rail read as a slab the ions sat *in*
        # rather than a wire they are beaded onto (invariant I5)
        "sw_rail": R(max(1.2, _G["RAIL_W_FRAC"] * g), 3),
        "sw_thin": R(max(1.0, _G["RUNG_W_FRAC"] * g), 3),
        "sw_halo": R(max(0.7, 0.055 * g), 3),
        "sw_node": R(max(1.0, 0.05 * g), 3),
        "sw_loop": R(max(3.0, 0.44 * g), 3),
        "well_rx": R(0.44 * g, 3),
        "well_ry": R(0.30 * g, 3),
        # 0.34 rather than 0.50 so a ONE-lattice-step segment carries three pads per
        # side instead of two -- a travelling ramp needs a pad ahead, one under and one
        # behind to read as travelling at all.
        "pad_pitch": R(0.34 * g, 3),   # DC electrode tiling pitch
        "pad_t": R(0.20 * g, 3),       # pad thickness across the axis
        "pad_off": R(0.30 * g, 3),     # pad offset normal to the trap axis
        "labels": bool(r_ion >= 11.0),     # fallback; the page re-decides on css size
        # the apex must reserve room for whatever rides the curve -- the ion and the
        # DC pads -- or it pushes them outside the viewBox (measured: an ion drawn 17 px
        # off the edge of verify/05, and four pad rects past the right margin)
        "bows": _bows(nodes, segments, pos, g, pad,
                      clearance=max(r_ion, 0.30 * g + 0.10 * g) + 2.0,
                      need=r_ion + r_rest + 3.0),
    }
    return lay
