"""Static drawings of places and designs, for pages that are read rather than run.

The silhouettes are the page's own (`web/app.js::shapeD`), transcribed so the landing page,
the library and the tool draw every category the same way.
"""

from __future__ import annotations

import html

from .categories import CATEGORIES

__all__ = ["shape_d", "shape_detail", "icon", "design_svg"]


def _f(v: float) -> str:
    s = f"{v:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def shape_d(shape: str, x: float, y: float, w: float, h: float) -> str:
    m = min(w, h)
    f = _f
    if shape == "octagon":
        c = m * 0.3
        return (f"M{f(x + c)} {f(y)}H{f(x + w - c)}L{f(x + w)} {f(y + c)}V{f(y + h - c)}"
                f"L{f(x + w - c)} {f(y + h)}H{f(x + c)}L{f(x)} {f(y + h - c)}V{f(y + c)}Z")
    if shape == "house":
        r = min(h * 0.38, w * 0.32)
        return f"M{f(x)} {f(y + r)}L{f(x + w / 2)} {f(y)}L{f(x + w)} {f(y + r)}V{f(y + h)}H{f(x)}Z"
    if shape == "stadium":
        r = m / 2
        return (f"M{f(x + r)} {f(y)}H{f(x + w - r)}A{f(r)} {f(r)} 0 0 1 {f(x + w - r)} {f(y + h)}"
                f"H{f(x + r)}A{f(r)} {f(r)} 0 0 1 {f(x + r)} {f(y)}Z")
    if shape == "hexagon":
        q = min(w * 0.2, h * 0.5)
        return (f"M{f(x + q)} {f(y)}H{f(x + w - q)}L{f(x + w)} {f(y + h / 2)}L{f(x + w - q)} {f(y + h)}"
                f"H{f(x + q)}L{f(x)} {f(y + h / 2)}Z")
    if shape == "bridge":
        n = 2 if w > 2.2 * h else 1
        d = f"M{f(x)} {f(y)}H{f(x + w)}V{f(y + h)}"
        for i in range(n, 0, -1):
            cx = x + w * i / (n + 1)
            aw = min(w / (n + 1) * 0.62, h * 1.1)
            ah = min(h * 0.42, aw / 2)
            d += f"H{f(cx + aw / 2)}A{f(aw / 2)} {f(ah)} 0 0 0 {f(cx - aw / 2)} {f(y + h)}"
        return d + f"H{f(x)}Z"
    if shape == "sawtooth":
        n = max(2, min(6, round(w / max(1e-6, h) * 2)))
        tw = w / n
        th = min(h * 0.28, tw * 0.7)
        d = f"M{f(x)} {f(y + h)}V{f(y + th)}"
        for i in range(n):
            d += f"L{f(x + (i + 1) * tw)} {f(y)}V{f(y + th)}"
        return d + f"V{f(y + h)}Z"
    if shape == "chevron":
        q = min(w * 0.18, h * 0.5)
        return (f"M{f(x)} {f(y)}H{f(x + w - q)}L{f(x + w)} {f(y + h / 2)}L{f(x + w - q)} {f(y + h)}"
                f"H{f(x)}L{f(x + q)} {f(y + h / 2)}Z")
    if shape == "garage":
        q = min(w * 0.14, h * 0.45)
        return f"M{f(x + q)} {f(y)}H{f(x + w - q)}L{f(x + w)} {f(y + h)}H{f(x)}Z"
    if shape == "circle":
        r, cx, cy = m / 2, x + w / 2, y + h / 2
        return (f"M{f(cx - r)} {f(cy)}A{f(r)} {f(r)} 0 1 0 {f(cx + r)} {f(cy)}"
                f"A{f(r)} {f(r)} 0 1 0 {f(cx - r)} {f(cy)}Z")
    if shape == "vault":
        return f"M{f(x)} {f(y)}H{f(x + w)}V{f(y + h)}H{f(x)}Z"
    if shape == "chip":                        # a package with pins down both sides
        n = max(2, min(6, int(h // max(1e-6, m * 0.3))))
        pin = min(w * 0.12, h / (2 * n + 1))
        d = f"M{f(x + pin)} {f(y)}H{f(x + w - pin)}V{f(y + h)}H{f(x + pin)}Z"
        for i in range(n):
            cy = y + h * (i + 0.5) / n
            d += (f"M{f(x + pin)} {f(cy - pin / 2)}H{f(x)}V{f(cy + pin / 2)}H{f(x + pin)}Z"
                  f"M{f(x + w - pin)} {f(cy - pin / 2)}H{f(x + w)}V{f(cy + pin / 2)}"
                  f"H{f(x + w - pin)}Z")
        return d
    if shape == "drum":                        # a cylinder on its side: a store of data
        r = min(w * 0.16, h / 2)
        return (f"M{f(x + r)} {f(y)}H{f(x + w - r)}A{f(r)} {f(h / 2)} 0 0 1 {f(x + w - r)} "
                f"{f(y + h)}H{f(x + r)}A{f(r)} {f(h / 2)} 0 0 1 {f(x + r)} {f(y)}Z"
                f"M{f(x + w - r)} {f(y)}A{f(r)} {f(h / 2)} 0 0 0 {f(x + w - r)} {f(y + h)}")
        # the second arc is the near rim, drawn as part of the same path
    r = min(8, w / 8, h / 8)
    return (f"M{f(x + r)} {f(y)}H{f(x + w - r)}Q{f(x + w)} {f(y)} {f(x + w)} {f(y + r)}"
            f"V{f(y + h - r)}Q{f(x + w)} {f(y + h)} {f(x + w - r)} {f(y + h)}H{f(x + r)}"
            f"Q{f(x)} {f(y + h)} {f(x)} {f(y + h - r)}V{f(y + r)}Q{f(x)} {f(y)} {f(x + r)} {f(y)}Z")


def shape_detail(shape: str, x: float, y: float, w: float, h: float) -> str:
    f = _f
    if shape == "vault":
        i = min(w, h) * 0.09
        return f"M{f(x + i)} {f(y + i)}H{f(x + w - i)}V{f(y + h - i)}H{f(x + i)}Z"
    if shape == "drum":                        # the rows of cells inside
        out = ""
        for k in (1, 2):
            yy = y + h * k / 3
            out += f"M{f(x + w * 0.2)} {f(yy)}H{f(x + w * 0.86)}"
        return out
    if shape == "chip":                        # the die inside the package
        ix, iy = w * 0.3, h * 0.28
        return (f"M{f(x + ix)} {f(y + iy)}H{f(x + w - ix)}V{f(y + h - iy)}"
                f"H{f(x + ix)}Z")
    return ""


def icon(category: str, size: float = 16.0) -> str:
    c = CATEGORIES.get(category)
    if not c:
        return ""
    w, h = size * 1.5, size
    d = shape_d(c["shape"], 1, 1, w - 2, h - 2)
    dd = shape_detail(c["shape"], 1, 1, w - 2, h - 2)
    extra = f'<path d="{dd}" fill="none" stroke="{c["stroke"]}" stroke-width="1"/>' if dd else ""
    return (f'<svg class="shp" width="{_f(w)}" height="{_f(h)}" viewBox="0 0 {_f(w)} {_f(h)}" '
            f'aria-hidden="true"><path d="{d}" fill="{c["fill"]}" stroke="{c["stroke"]}" '
            f'stroke-width="1.4"/>{extra}</svg>')


def design_svg(lib, *, scale: float = 7.0, pad: float = 8.0, highlight=None) -> str:
    """The top level of a composite design: places as their category's silhouette, channels
    as streets, junctions as dots, names under small places."""
    top = lib[lib.top]
    x0, y0, x1, y1 = top.bbox
    W = (x1 - x0 + 2 * pad) * scale
    H = (y1 - y0 + 2 * pad) * scale + 10

    def X(v):
        return (v - x0 + pad) * scale

    def Y(v):
        return (v - y0 + pad) * scale

    parts = [f'<svg class="design" viewBox="0 0 {_f(W)} {_f(H)}" role="img" '
             f'aria-label="{html.escape(top.title)}">']
    for ch in top.channels:
        pts = " ".join(f"{_f(X(p[0]))},{_f(Y(p[1]))}" for p in ch.points)
        if getattr(ch, "kind", "road") == "wire":
            parts.append(f'<polyline points="{pts}" fill="none" '
                         f'stroke="{CATEGORIES["wire"]["stroke"]}" stroke-width="1.3" '
                         f'stroke-dasharray="5 3" stroke-linejoin="round"/>')
            continue
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#e7a3a0" '
                     f'stroke-width="{_f(max(1.5, scale * 0.3))}" stroke-linejoin="round"/>')
    labels = []
    for inst in top.instances:
        m = lib[inst.master]
        c = CATEGORIES.get(m.family, CATEGORIES["road"])
        bx0, by0, bx1, by1 = m.bbox
        if m.family == "road":
            cx, cy = X(inst.x + (bx0 + bx1) / 2), Y(inst.y + (by0 + by1) / 2)
            parts.append(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(max(2.5, scale * 0.55))}" '
                         f'fill="#fff" stroke="{c["stroke"]}" stroke-width="1.3"/>')
            continue
        h = by1 - by0
        pad_y = max(1.0, (5 - h) / 2)
        pad_x = 0.6
        if c["shape"] == "stadium":            # a pill, however square the device
            pad_x = max(pad_x, ((h + 2 * pad_y) * 1.7 - (bx1 - bx0)) / 2)
        rx, ry = X(inst.x + bx0 - pad_x), Y(inst.y + by0 - pad_y)
        rw, rh = (bx1 - bx0 + 2 * pad_x) * scale, (h + 2 * pad_y) * scale
        hl = highlight and inst.name in highlight
        parts.append(f'<path d="{shape_d(c["shape"], rx, ry, rw, rh)}" fill="{c["fill"]}" '
                     f'stroke="{c["stroke"]}" stroke-width="{2.6 if hl else 1.6}"/>')
        dd = shape_detail(c["shape"], rx, ry, rw, rh)
        if dd:
            parts.append(f'<path d="{dd}" fill="none" stroke="{c["stroke"]}" stroke-width="1"/>')
        if c["glyph"] and rw > 60:
            inset = 6 + (min(rw * 0.2, rh * 0.5) if c["shape"] in ("hexagon", "chevron") else 0)
            parts.append(f'<text x="{_f(rx + rw - inset)}" y="{_f(ry + rh - 6)}" text-anchor="end" '
                         f'font-size="9" font-weight="700" fill="{c["stroke"]}">{c["glyph"]}</text>')
        labels.append(f'<text x="{_f(rx + rw / 2)}" y="{_f(ry + rh + 13)}" text-anchor="middle" '
                      f'font-size="11.5" fill="#1e2761" font-weight="600">{html.escape(inst.name)}</text>')
    parts += labels
    parts.append("</svg>")
    return "".join(parts)
