"""The course on the Learn page: one card per lesson, and how far the reader has got.

The course itself lives in the studio (`qccd/viz/js/tutorial.js`, run by `editor.js`), and it
records what it awards in the browser: `localStorage['qccd.studio.tutorial'].stars`, one to
three stars per lesson (done, the boundary exercise, the challenge).  This module adds two
things around it and touches neither file:

* **The Learn page's course section** (`course_section`): a progress panel at the top (the
  share of exercises done as a ring, the stars, the level the reader has reached and what the
  next one takes, a Continue button to the next lesson, the seven badges), then each part with
  one card per lesson on its own line -- a small diagram of what the lesson is about (drawn
  the way the studio draws a device), the title, the lesson's own first line, and its state
  (done with its stars, next up, or not started).  The cards are
  written by the build; the page's script (`LEARN_JS`) only sets their state.

* **The account** (`SYNC_JS`): on the Learn page and on the studio pages a signed-in reader's
  stars are also kept by the accounts service (`comments_api.py`, `/api/progress`), so the
  progress follows the account and not the browser.  On every visit the page asks the
  account, sends up any star this browser has that the account lacks, and writes the
  account's stars back into this browser, so the studio's own Learn tab shows them too.  A
  count only ever goes up, on both sides.  Stars that another account left in this browser
  are not given to the reader who signs in next: the browser remembers which account it last
  matched (`qccd.course.account`), and on a change it takes the new account's stars instead.

Nothing here runs inside an iframe, under `#embed`, on file:// or under the test shim, and a
page served without the accounts service shows the browser's own progress.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

TUTORIAL_JS = Path(__file__).resolve().parents[2] / "qccd" / "viz" / "js" / "tutorial.js"

#: What finishing each part makes the reader.  The levels are these names, reached at the
#: number of lessons the parts before have in all; a lesson done out of order still counts.
BADGES = {"A": "Map reader", "B": "Ion programmer", "R": "Rule keeper",
          "C": "Circuit scheduler", "D": "QCCD architect"}

#: The pictures are drawn in the studio's own vocabulary (qccd/viz/theme.py): a site is a
#: rounded bar coloured by its zone with one ring per ion it holds, a rail is a pink line, a
#: junction a white square with a gold edge; moves are orange arrows, a two-qubit gate a
#: magenta beam, a measurement blue, cooling a snowflake; a broken rule a red cross.
ZONE = {"trap": ("#d3eeea", "#0f766e"), "data": ("#e6e9ef", "#667085"), "load": ("#fdf0c8", "#c9971a")}
D0, D1, D2, CHK, DATA = "#2a78d6", "#e8890c", "#1baf7a", "#7c5bfa", "#475467"
ARROW, GATE, MEAS, COOL, BAD, GOOD = "#ed7d31", "#c2308a", "#2a78d6", "#0e9aa7", "#d92d20", "#12a150"
MUTED, NAVY = "#667085", "#1e2761"
SANS = "ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
MONO = "ui-monospace,'Cascadia Mono',Menlo,Consolas,monospace"


def _n(v: float) -> str:
    return f"{v:g}"


def rail(x1, y1, x2, y2) -> str:
    return f'<path d="M{_n(x1)} {_n(y1)}L{_n(x2)} {_n(y2)}" stroke="#f2b8b5" stroke-width="4.5" stroke-linecap="round"/>'


def curve(d: str) -> str:
    """A rail that bends (the end of a loop)."""
    return f'<path d="{d}" stroke="#f2b8b5" stroke-width="4.5" fill="none" stroke-linecap="round"/>'


def slots(x, y, n=1, v=False, gap=9.0) -> list[tuple[float, float]]:
    """Where the rings of a site are: along its long axis, centred on it."""
    return [(x, y + (i - (n - 1) / 2) * gap) if v else (x + (i - (n - 1) / 2) * gap, y) for i in range(n)]


def site(x, y, zone="trap", n=1, v=False, gap=9.0) -> str:
    f, s = ZONE[zone]
    length = 8 + gap * n
    w, h = (11, length) if v else (length, 11)
    out = (f'<rect x="{_n(x - w / 2)}" y="{_n(y - h / 2)}" width="{_n(w)}" height="{_n(h)}" rx="5.5" '
           f'fill="{f}" stroke="{s}" stroke-width="1.5"/>')
    return out + "".join(f'<circle cx="{_n(a)}" cy="{_n(b)}" r="2.5" fill="#fff" stroke="{s}" stroke-width="1"/>'
                         for a, b in slots(x, y, n, v, gap))


def junction(x, y) -> str:
    return (f'<rect x="{_n(x - 6)}" y="{_n(y - 6)}" width="12" height="12" rx="1.5" fill="#fff" '
            f'stroke="#e8b84b" stroke-width="2.3"/>')


def ion(x, y, c=DATA, ghost=False, hot=False) -> str:
    glow = (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="7.5" fill="{BAD}" opacity=".22"/>' if hot else "")
    if ghost:
        return (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="4.2" fill="#fff" stroke="{c}" stroke-width="1.3" '
                f'stroke-dasharray="2 1.6" opacity=".9"/>')
    return glow + f'<circle cx="{_n(x)}" cy="{_n(y)}" r="4.2" fill="{c}" stroke="#fff" stroke-width="1.3"/>'


def arrow(d: str, c=ARROW, dash=False, w=2.0) -> str:
    mk = {ARROW: "o", BAD: "r", GOOD: "g", CHK: "p", MUTED: "m"}.get(c, "o")
    return (f'<path d="{d}" stroke="{c}" stroke-width="{w}" fill="none" stroke-linecap="round" '
            + ('stroke-dasharray="3 2.4" ' if dash else "") + f'marker-end="url(#@-{mk})"/>')


def text(x, y, s, size=7.5, c=MUTED, anchor="middle", weight=600, mono=False) -> str:
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" font-weight="{weight}" fill="{c}" text-anchor="{anchor}" '
            f'font-family="{MONO if mono else SANS}">{html.escape(s)}</text>')


def bad(x, y, r=6) -> str:
    k = r * .45
    return (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{r}" fill="{BAD}" stroke="#fff" stroke-width="1.2"/>'
            f'<path d="M{_n(x - k)} {_n(y - k)}l{_n(2 * k)} {_n(2 * k)}M{_n(x + k)} {_n(y - k)}l{_n(-2 * k)} {_n(2 * k)}" '
            f'stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>')


def good(x, y, r=6) -> str:
    return (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{r}" fill="{GOOD}" stroke="#fff" stroke-width="1.2"/>'
            f'<path d="M{_n(x - r * .45)} {_n(y + r * .02)}l{_n(r * .32)} {_n(r * .34)} {_n(r * .58)}-{_n(r * .66)}" '
            f'stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')


def beam(x, y1, y2, c=GATE) -> str:
    """A laser beam onto a site, from above (y1 < y2)."""
    return (f'<path d="M{_n(x)} {_n(y1)}V{_n(y2)}" stroke="{c}" stroke-width="6" opacity=".2" stroke-linecap="round"/>'
            f'<path d="M{_n(x)} {_n(y1)}V{_n(y2)}" stroke="{c}" stroke-width="2" stroke-linecap="round"/>')


def snow(x, y, r=5.0, c=COOL) -> str:
    import math
    arms = "".join(f'M{_n(x - r * math.cos(a))} {_n(y - r * math.sin(a))}L{_n(x + r * math.cos(a))} {_n(y + r * math.sin(a))}'
                   for a in (math.pi / 2, math.pi / 6, -math.pi / 6))
    return f'<path d="{arms}" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/><circle cx="{_n(x)}" cy="{_n(y)}" r="1.4" fill="{c}"/>'


def heat(x, y) -> str:
    return (f'<path d="M{_n(x - 3)} {_n(y)}q-2.2-2.6 0-5.2t0-5.2M{_n(x + 3)} {_n(y)}q-2.2-2.6 0-5.2t0-5.2" '
            f'stroke="{BAD}" stroke-width="1.5" fill="none" stroke-linecap="round"/>')


def pill(x, y, s, c, w=19) -> str:
    return (f'<rect x="{_n(x - w / 2)}" y="{_n(y - 5.5)}" width="{w}" height="11" rx="5.5" fill="{c}"/>'
            + text(x, y + 2.6, s, 7, "#fff", weight=700))


def ring6(top=22, bot=58, zone="trap", xs=(30, 60, 90)) -> tuple[str, list[tuple[float, float]]]:
    """A racetrack of six one-ion sites, S0..S5 clockwise from the top left; the sites'
    centres come back so the ions can be put in them."""
    mid = (top + bot) / 2
    r = (bot - top) / 2
    left, right = xs[0] - 8.5, xs[-1] + 8.5
    g = (rail(xs[0], top, xs[-1], top) + rail(xs[0], bot, xs[-1], bot)
         + curve(f"M{_n(right)} {_n(top)}A{_n(r)} {_n(r)} 0 0 1 {_n(right)} {_n(bot)}")
         + curve(f"M{_n(left)} {_n(bot)}A{_n(r)} {_n(r)} 0 0 1 {_n(left)} {_n(top)}"))
    pos = [(xs[0], top), (xs[1], top), (xs[2], top), (xs[2], bot), (xs[1], bot), (xs[0], bot)]
    return g + "".join(site(a, b, zone) for a, b in pos), pos


def _line5(y=46, zone="trap") -> tuple[str, list[float]]:
    xs = [13, 36.5, 60, 83.5, 107]
    return "".join(rail(xs[i], y, xs[i + 1], y) for i in range(4)) + "".join(site(x, y, zone) for x in xs), xs


def _cross(cx=60, cy=40, arm=36, n=1) -> tuple[str, dict]:
    """A junction with a site on each of its four arms (C3, R2)."""
    pos = {"L": (cx - arm, cy), "R": (cx + arm, cy), "U": (cx, cy - arm * .78), "D": (cx, cy + arm * .78)}
    g = "".join(rail(cx, cy, *p) for p in pos.values()) + junction(cx, cy)
    g += site(*pos["L"], n=n) + site(*pos["R"], n=n) + site(*pos["U"], n=n, v=True) + site(*pos["D"], n=n, v=True)
    return g, pos


def _diagrams() -> dict[str, str]:
    d = {}
    # ---- A: the map --------------------------------------------------------------------
    d["A1"] = (rail(14, 30, 44, 30) + rail(44, 30, 74, 30) + rail(74, 30, 104, 30) + rail(44, 30, 44, 60)
               + junction(44, 30) + site(14, 30) + site(74, 30) + site(104, 30) + site(44, 61, v=True)
               + ion(74, 30, D0) + ion(44, 61, D1)
               + text(14, 18, "site") + text(44, 18, "junction") + text(89, 45, "rail") + text(62, 64, "spur", anchor="start"))
    d["A2"] = (rail(22, 38, 58, 38) + rail(58, 38, 96, 38)
               + site(22, 38, "trap", 2) + site(58, 38, "data", 2) + site(96, 38, "load", 4, gap=5.5)
               + ion(17.5, 38, D0) + ion(62.5, 38, D1)
               + beam(22, 9, 30) + snow(58, 20) + arrow("M96 10V27", MUTED)
               + text(22, 60, "trap · 2", 7.5, ZONE["trap"][1]) + text(58, 60, "data · 2", 7.5, ZONE["data"][1])
               + text(96, 60, "load · 8", 7.5, ZONE["load"][1]) + text(22, 71, "gate", 6.5) + text(58, 71, "cool", 6.5)
               + text(96, 71, "new ions", 6.5))
    d["A3"] = (f'<rect x="12" y="10" width="96" height="60" rx="14" fill="none" stroke="#2e8b57" stroke-width="1.6" stroke-dasharray="4 3"/>'
               + rail(30, 24, 90, 24) + rail(30, 56, 90, 56) + rail(30, 24, 30, 56) + rail(90, 24, 90, 56)
               + site(30, 24) + site(90, 24) + site(30, 56) + site(90, 56)
               + text(60, 43, "loop", 8, "#2e8b57")
               + '<path d="M97 60l0 13 3.4-3.2 2.4 5.2 2.2-1-2.4-5.1 4.6-.2z" fill="#fff" stroke="#182230" stroke-width="1.2" stroke-linejoin="round"/>')
    d["A4"] = (rail(22, 50, 98, 50) + site(22, 50) + site(98, 50)
               + ion(22, 50, D0, ghost=True) + ion(98, 50, D0, hot=True)
               + arrow("M30 38Q60 26 88 38") + heat(98, 37)
               + f'<circle cx="38" cy="15" r="6" fill="#fff" stroke="#182230" stroke-width="1.4"/>'
               + f'<path d="M38 15v-3.4M38 15l2.4 1.4" stroke="{BAD}" stroke-width="1.4" stroke-linecap="round"/>'
               + text(47, 18, "2 µs", 8, "#182230", "start") + text(98, 24, "+0.2 n̄", 7.5, BAD) + text(60, 70, "one push = time + heat", 7))
    els = "".join(f'<rect x="{_n(x - 3)}" y="31" width="6" height="5" rx="1" fill="#c3cbd8"/>'
                  f'<path d="M{_n(x)} 36L60 56" stroke="#98a2b3" stroke-width=".9"/>' for x in range(18, 106, 12))
    d["A5"] = (rail(10, 22, 110, 22) + site(22, 22) + site(98, 22) + els
               + f'<rect x="42" y="56" width="36" height="14" rx="3" fill="{NAVY}"/>' + text(60, 65.6, "1 DAC", 7.5, "#fff", weight=700)
               + text(4, 53, "8 electrodes", 6.5, anchor="start") + text(116, 53, "one channel", 6.5, anchor="end"))
    d["A6"] = ('<rect x="8" y="9" width="104" height="62" rx="5" fill="#fff" stroke="#d0d5dd" stroke-width="1.2"/>'
               '<path d="M8 19h104" stroke="#d0d5dd"/><rect x="8" y="9" width="104" height="10" rx="5" fill="#eef2f6"/>'
               + "".join(f'<rect x="{x}" y="11.5" width="11" height="5" rx="1.5" fill="{c}"/>'
                         for x, c in ((62, "#182230"), (75, "#c3cbd8"), (88, "#c3cbd8"), (101, "#c3cbd8")))
               + '<rect x="8" y="19" width="16" height="52" fill="#f7f8fb"/>'
               + "".join(f'<rect x="11" y="{y}" width="10" height="5" rx="1.5" fill="#e4e7ec"/>' for y in (23, 31, 39, 47))
               + '<rect x="86" y="19" width="26" height="52" fill="#f7f8fb"/>'
               + "".join(f'<rect x="89" y="{y}" width="{w}" height="3" rx="1.5" fill="#d0d5dd"/>'
                         for y, w in ((24, 18), (30, 20), (36, 14), (42, 19), (48, 12)))
               + rail(40, 34, 70, 34) + rail(40, 56, 70, 56) + rail(40, 34, 40, 56) + rail(70, 34, 70, 56)
               + site(40, 34) + site(70, 34) + site(40, 56) + site(70, 56) + ion(70, 34, D0) + ion(40, 56, D1))
    # ---- B: programming by hand -----------------------------------------------------------
    d["B1"] = (rail(20, 24, 100, 24) + site(20, 24) + site(60, 24) + site(100, 24)
               + ion(20, 24, D0, ghost=True) + ion(100, 24, D0) + arrow("M26 14Q60 2 92 14")
               + '<rect x="6" y="38" width="108" height="34" rx="5" fill="#fff" stroke="#d0d5dd" stroke-width="1.2"/>'
               + text(12, 52, 'p.init({"d0": "S0"})', 7, "#182230", "start", 500, True)
               + text(12, 64, 'p.shuttle("d0", […])', 7, "#182230", "start", 500, True))
    d["B2"] = (rail(18, 36, 102, 36) + site(18, 36, n=2) + site(60, 36, n=2) + site(102, 36, n=2)
               + ion(13.5, 36, D0) + ion(22.5, 36, D1) + ion(102, 36, D1, ghost=True)
               + arrow("M98 47Q60 72 27 47") + beam(18, 6, 28) + text(28, 13, "CX", 8, GATE, "start", 700))
    d["B3"] = (f'<path d="M22 28H110M22 54H110" stroke="#182230" stroke-width="1.4"/>'
               + text(14, 31, "d0", 7, anchor="middle") + text(14, 57, "d1", 7, anchor="middle")
               + f'<rect x="31" y="20" width="16" height="16" rx="2.5" fill="{D1}"/>' + text(39, 31, "H", 9, "#fff", weight=800)
               + f'<path d="M64 28V60" stroke="{GATE}" stroke-width="1.8"/><circle cx="64" cy="28" r="3.6" fill="{GATE}"/>'
               + f'<circle cx="64" cy="54" r="6.5" fill="#fff" stroke="{GATE}" stroke-width="1.8"/><path d="M57.5 54h13" stroke="{GATE}" stroke-width="1.8"/>'
               + "".join(f'<rect x="84" y="{y - 8}" width="18" height="16" rx="2.5" fill="#fff" stroke="{MEAS}" stroke-width="1.6"/>'
                         f'<path d="M87.5 {y + 3.5}a5.5 5.5 0 0 1 11 0M93 {y + 3.5}l3.4-6" stroke="{MEAS}" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
                         for y in (28, 54)) + text(64, 75, "a Bell pair", 7))
    d["B4"] = (rail(20, 30, 100, 30) + site(20, 30) + site(60, 30, "data", 2) + site(100, 30)
               + ion(55.5, 30, D0) + ion(64.5, 30, D1) + beam(60, 4, 22) + bad(74, 12)
               + text(60, 47, "data: no gates", 6.5)
               + pill(24, 63, "R1", GOOD) + pill(47, 63, "R6", BAD) + pill(70, 63, "R7", GOOD) + pill(95, 63, "R11", GOOD, 22))
    g, pos = ring6()
    d["B5"] = (g + "".join(ion(a, b, c) for (a, b), c in zip(pos, (D0, D1, D2, CHK, DATA, "#c2308a")))
               + arrow("M50.5 34.5A11 11 0 1 1 50.5 45.5", ARROW, w=2.2) + text(60, 43, "+2", 8, ARROW, weight=800))
    g, pos = ring6()
    d["B6"] = (g + ion(*pos[0], D0) + ion(*pos[2], D1) + ion(*pos[4], D2)
               + arrow("M34 12H54") + arrow("M110 32Q114 40 110 48") + arrow("M56 68H36")
               + text(60, 39, "p.simd", 7, "#182230", weight=700, mono=True) + text(60, 48, "1 step", 7, ARROW, weight=700))
    g, pos = ring6()
    d["B7"] = (g + ion(*pos[0], D0) + ion(*pos[3], D1) + arrow("M34 12H54") + arrow("M110 50Q114 42 110 34", BAD)
               + bad(60, 40) + text(60, 76, "one channel, one direction", 6.5))
    g, pos = ring6(32, 52, "data")
    d["B8"] = (g + rail(30, 32, 30, 12) + rail(90, 52, 90, 70) + site(30, 11, "trap") + site(90, 71, "trap")
               + ion(30, 11, D0) + ion(*pos[2], D1) + arrow("M66 36A8 8 0 1 1 54 36", ARROW, w=1.8)
               + text(42, 14, "dock", 7, ZONE["trap"][1], "start") + text(78, 74, "dock", 7, ZONE["trap"][1], "end"))
    # ---- R: the rules ------------------------------------------------------------------------
    d["R1"] = (rail(36, 46, 92, 46) + site(36, 46, n=2) + site(92, 46, n=2)
               + ion(31.5, 46, D0) + ion(40.5, 46, D1) + ion(36, 24, D2) + arrow("M36 30V36", BAD, w=1.6) + bad(22, 22)
               + ion(87.5, 46, D2, ghost=True) + arrow("M44 22Q72 10 86 36", GOOD, dash=True, w=1.6) + good(104, 30)
               + text(36, 64, "holds 2", 7) + text(92, 64, "room", 7))
    g, pos = _cross()
    d["R2"] = (g + ion(38, 40, D0) + ion(60, 24, D1) + arrow("M38 32H50") + arrow("M70 22V30") + bad(74, 54)
               + text(4, 74, "one at a time", 6.5, anchor="start"))
    d["R3"] = (rail(22, 36, 98, 36) + site(22, 36, n=2) + site(98, 36, n=2)
               + ion(48, 36, D0) + ion(72, 36, D1) + arrow("M44 26Q60 16 76 26") + arrow("M76 46Q60 56 44 46")
               + bad(60, 36) + text(60, 70, "no passing on one rail", 7))
    d["R4"] = ('<rect x="6" y="12" width="108" height="42" rx="7" fill="none" stroke="#98a2b3" stroke-width="1.3" stroke-dasharray="4 3"/>'
               + text(12, 9, "one step", 7, anchor="start")
               + rail(18, 38, 102, 38) + site(18, 38) + site(46, 38) + site(74, 38) + site(102, 38)
               + ion(18, 38, D0) + ion(74, 38, D1) + arrow("M23 28H40") + arrow("M79 28H96")
               + pill(32, 47, "shuttle", GOOD, 30) + pill(88, 47, "shuttle", GOOD, 30)
               + pill(60, 68, "teleport", BAD, 38) + f'<path d="M40 68h40" stroke="#fff" stroke-width="1.4"/>'
               + bad(87, 68, 5))
    g, pos = ring6(16, 44)
    d["R5"] = (g + "".join(f'<path d="M{_n(a)} {_n(b + 5)}L60 64" stroke="#98a2b3" stroke-width=".8"/>' for a, b in pos)
               + f'<rect x="42" y="62" width="36" height="13" rx="3" fill="{NAVY}"/>' + text(60, 70.8, "1 channel", 7, "#fff", weight=700)
               + ion(*pos[0], D0) + ion(*pos[3], D1) + arrow("M36 7H54") + arrow("M110 38Q114 30 110 22", BAD) + bad(60, 30))
    d["R6"] = (rail(20, 48, 100, 48) + site(20, 48) + site(60, 48, "data") + site(100, 48)
               + ion(60, 48, D0) + ion(100, 48, D1)
               + "".join(f'<rect x="{x - 7}" y="8" width="14" height="10" rx="2.5" fill="{MEAS}"/><circle cx="{x}" cy="13" r="3" fill="#fff"/>'
                         f'<path d="M{x} 19V40" stroke="{MEAS}" stroke-width="1.5" stroke-dasharray="2.5 2"/>' for x in (60, 100))
               + bad(73, 24) + good(113, 24) + text(60, 66, "data", 7, ZONE["data"][1]) + text(100, 66, "trap", 7, ZONE["trap"][1])
               + text(20, 66, "trap", 7, ZONE["trap"][1]))
    d["R7"] = (rail(12, 22, 44, 22) + rail(44, 22, 78, 22) + junction(44, 22) + site(12, 22) + site(78, 22)
               + ion(12, 22, D0, ghost=True) + ion(78, 22, D0, hot=True) + arrow("M18 12Q44 2 70 12") + heat(78, 10)
               + text(96, 25, "3.2 q", 8, BAD, "start", 700)
               + snow(26, 58, 6) + arrow("M36 58H54") + site(76, 58, n=2) + ion(71.5, 58, D0) + ion(80.5, 58, D1)
               + beam(76, 38, 50) + good(98, 58))
    d["R8"] = (rail(42, 44, 98, 44) + site(42, 44, "data", 3) + site(98, 44)
               + ion(33, 44, D0) + ion(42, 44, D1) + ion(51, 44, D2)
               + arrow("M42 37V20", BAD) + bad(54, 18) + arrow("M58 34Q78 26 92 36", GOOD) + good(106, 26)
               + text(42, 64, "a chain of three", 7) + text(42, 74, "only an end ion splits", 6.5))
    card = '<rect x="{x}" y="12" width="46" height="54" rx="5" fill="#fff" stroke="#d0d5dd" stroke-width="1.2"/>'
    d["R9"] = (card.format(x=6) + card.format(x=68)
               + text(29, 26, "claim", 7.5, MUTED) + text(29, 42, "cost 2", 8, "#182230") + text(29, 56, "steps 3", 8, "#182230")
               + text(91, 26, "replay", 7.5, MUTED) + text(91, 42, "cost 3", 8, BAD, weight=800) + text(91, 56, "steps 3", 8, "#182230")
               + text(60, 46, "≠", 14, BAD, weight=800) + text(60, 76, "every claim is checked", 6.5))
    # ---- C: circuits by hand -------------------------------------------------------------------
    g, xs = _line5()
    arcs = ((0, 1, 20), (2, 1, 26), (2, 3, 20), (4, 3, 26))
    d["C1"] = (g + "".join(ion(x, 46, c) for x, c in zip(xs, (DATA, CHK, DATA, CHK, DATA)))
               + "".join(f'<path d="M{_n(xs[a])} 39Q{_n((xs[a] + xs[b]) / 2)} {h} {_n(xs[b])} 39" stroke="{GATE}" stroke-width="1.5" fill="none"/>'
                         for a, b, h in arcs)
               + "".join(text(x, 64, s, 7, c) for x, s, c in zip(xs, ("d0", "c0", "d1", "c1", "d2"), (DATA, CHK, DATA, CHK, DATA)))
               + f'<path d="M36.5 70v5M83.5 70v5" stroke="{MEAS}" stroke-width="2" stroke-linecap="round"/>'
               + text(60, 76, "measure", 6.5, MEAS))
    g, xs = _line5(50)
    d["C2"] = (g + "".join(ion(x, 50, c) for x, c in zip(xs, (DATA, CHK, DATA, CHK, DATA)))
               + arrow("M37 38Q48 30 57 38", ARROW) + arrow("M84 38Q95 30 104 38", ARROW)
               + beam(13, 8, 42) + beam(60, 8, 42) + text(36.5, 16, "same", 7, GATE, weight=700) + text(84, 16, "step", 7, GATE, weight=700)
               + text(60, 72, "two traps, one instruction", 6.5))
    g, pos = _cross(60, 40, 38)
    d["C3"] = (g + ion(pos["L"][0], 40, DATA) + ion(pos["R"][0], 40, DATA) + ion(60, pos["U"][1], DATA) + ion(60, pos["D"][1], DATA)
               + ion(pos["L"][0] + 9, 31, CHK)
               + arrow("M32 28Q38 12 52 12", CHK, dash=True, w=1.6) + arrow("M68 12Q84 14 88 28", CHK, dash=True, w=1.6)
               + arrow("M88 52Q84 66 70 68", CHK, dash=True, w=1.6)
               + text(4, 66, "a0 visits", 6.5, CHK, "start") + text(4, 75, "all four", 6.5, CHK, "start"))
    g, pos = _cross(36, 40, 26)
    d["C4"] = (g + ion(pos["L"][0], 40, DATA) + ion(pos["R"][0], 40, DATA) + ion(36, pos["U"][1], DATA) + ion(36, pos["D"][1], DATA)
               + snow(50, 26, 4) + snow(50, 54, 4) + snow(22, 26, 4)
               + text(92, 26, "× 2", 13, "#182230", weight=800)
               + f'<circle cx="74" cy="50" r="6.5" fill="#fff" stroke="#182230" stroke-width="1.4"/>'
               + f'<path d="M74 50v-3.6M74 50l2.8 1.5" stroke="{BAD}" stroke-width="1.4" stroke-linecap="round"/>'
               + text(83, 53, "< 3.5 ms", 7.5, "#182230", "start", 700) + text(88, 70, "reset, round again", 6.5))
    # ---- D: the compiler ------------------------------------------------------------------------
    gear = "".join(f'<rect x="58" y="29.5" width="4" height="5" rx="1" transform="rotate({k * 45} 60 40)" fill="#8a8985"/>' for k in range(8))
    d["D1"] = ('<rect x="2" y="12" width="50" height="56" rx="5" fill="#fff" stroke="#d0d5dd" stroke-width="1.2"/>'
               '<rect x="68" y="12" width="50" height="56" rx="5" fill="#fff" stroke="#d0d5dd" stroke-width="1.2"/>'
               + text(27, 9, "circuit", 6.5) + text(93, 9, "programme", 6.5)
               + f'<rect x="4" y="31" width="46" height="10" rx="2" fill="{GATE}" opacity=".15"/>'
               + text(6, 26, "h q[0]", 6, "#182230", "start", 500, True) + text(6, 38.5, "cx q[0],q[1]", 6, GATE, "start", 700, True)
               + text(6, 51, "measure", 6, "#182230", "start", 500, True)
               + f'<rect x="70" y="44" width="46" height="10" rx="2" fill="{GATE}" opacity=".15"/>'
               + "".join(text(73, y, s, 6, GATE if s == "gate CX" else "#182230", "start", 700 if s == "gate CX" else 500, True)
                         for y, s in ((26, "shuttle"), (38.5, "cool"), (51.5, "gate CX"), (63, "measure")))
               + gear + '<circle cx="60" cy="40" r="6" fill="#8a8985"/><circle cx="60" cy="40" r="2.4" fill="#fff"/>'
               + arrow("M50 39Q60 60 67.5 50.5", GATE, dash=True, w=1.3) + text(60, 76, "certificate", 6.5, GATE))
    def ring_glyph(x, y):
        return (f'<rect x="{x - 9}" y="{y - 6}" width="18" height="12" rx="6" fill="none" stroke="#f2b8b5" stroke-width="2.6"/>'
                + "".join(f'<circle cx="{x + dx}" cy="{y + dy}" r="1.8" fill="{ZONE["trap"][1]}"/>' for dx, dy in ((-4, -6), (4, -6), (-4, 6), (4, 6))))

    def grid_glyph(x, y):
        return (f'<path d="M{x - 8} {y - 6}h16M{x - 8} {y}h16M{x - 8} {y + 6}h16M{x - 8} {y - 6}v12M{x} {y - 6}v12M{x + 8} {y - 6}v12" stroke="#f2b8b5" stroke-width="2"/>'
                + "".join(f'<rect x="{x + dx - 1.8}" y="{y + dy - 1.8}" width="3.6" height="3.6" fill="#fff" stroke="#e8b84b" stroke-width="1"/>'
                          for dx in (-8, 0, 8) for dy in (-6, 6)))

    def ladder_glyph(x, y):
        return (f'<path d="M{x - 9} {y - 5}h18M{x - 9} {y + 5}h18M{x - 6} {y - 5}v10M{x} {y - 5}v10M{x + 6} {y - 5}v10" stroke="#f2b8b5" stroke-width="2"/>'
                + "".join(f'<circle cx="{x + dx}" cy="{y + dy}" r="1.6" fill="{ZONE["trap"][1]}"/>' for dx in (-6, 0, 6) for dy in (-5, 5)))
    d["D2"] = ('<path d="M6 32H26M6 48H26" stroke="#182230" stroke-width="1.2"/>'
               + f'<path d="M16 32V51" stroke="{GATE}" stroke-width="1.4"/><circle cx="16" cy="32" r="2.6" fill="{GATE}"/>'
               + f'<circle cx="16" cy="48" r="4.4" fill="#fff" stroke="{GATE}" stroke-width="1.4"/><path d="M11.6 48h8.8" stroke="{GATE}" stroke-width="1.4"/>'
               + arrow("M30 36L40 18", MUTED, w=1.2) + arrow("M30 40H40", MUTED, w=1.2) + arrow("M30 44L40 62", MUTED, w=1.2)
               + ring_glyph(52, 14) + grid_glyph(52, 40) + ladder_glyph(52, 66)
               + f'<rect x="68" y="10" width="44" height="8" rx="2" fill="{BAD}" opacity=".75"/>'
               + f'<rect x="68" y="36" width="16" height="8" rx="2" fill="{GOOD}" opacity=".85"/>'
               + f'<rect x="68" y="62" width="27" height="8" rx="2" fill="{D1}" opacity=".85"/>' + text(90, 50, "cost", 6.5))
    left = "".join(f'<rect x="{x - 2.5}" y="12" width="5" height="6" rx="1" fill="#c3cbd8"/><path d="M{x} 18V40" stroke="#98a2b3" stroke-width=".9"/>'
                   f'<rect x="{x - 2.6}" y="40" width="5.2" height="8" rx="1" fill="{NAVY}"/>' for x in range(11, 56, 8))
    right = "".join(f'<rect x="{x - 2.5}" y="12" width="5" height="6" rx="1" fill="#c3cbd8"/><path d="M{x} 18L91 40" stroke="#98a2b3" stroke-width=".9"/>'
                    for x in range(71, 116, 8))
    d["D3"] = (left + right + f'<rect x="81" y="40" width="20" height="9" rx="2" fill="{NAVY}"/>'
               + '<path d="M60 8V72" stroke="#e4e7ec" stroke-width="1.2"/>'
               + text(31, 62, "direct", 7.5, "#182230") + text(91, 62, "broadcast", 7.5, "#182230")
               + text(31, 73, "5,000+ DACs", 6.8, BAD, weight=700) + text(91, 73, "44 DACs", 6.8, GOOD, weight=700))
    d["D4"] = ('<rect x="5" y="9" width="110" height="62" rx="6" fill="#1f2430"/>'
               '<circle cx="13" cy="16" r="2" fill="#ff5f57"/><circle cx="20" cy="16" r="2" fill="#febc2e"/><circle cx="27" cy="16" r="2" fill="#28c840"/>'
               + text(11, 32, "$ python -m qccd studio", 6.6, "#7ee787", "start", 600, True)
               + text(11, 43, "   --tsir x.tsir.json", 6.6, "#e6edf3", "start", 500, True)
               + text(11, 54, "   --qasm x.qasm -o x.html", 6.6, "#e6edf3", "start", 500, True)
               + '<rect x="11" y="59" width="5" height="8" fill="#7ee787"/>')
    # ---- F: certificates and the proved checker ---------------------------------------------
    d["F1"] = (rail(10, 54, 52, 54) + site(10, 54) + site(48, 54, n=2) + ion(10, 54, D1, ghost=True)
               + ion(43.5, 54, D0) + ion(52.5, 54, D1) + arrow("M14 44Q28 34 40 44") + beam(48, 22, 46)
               + text(30, 74, "a Bell pair", 6.5)
               + '<rect x="66" y="8" width="48" height="64" rx="5" fill="#fff" stroke="#d0d5dd" stroke-width="1.2"/>'
               + text(90, 20, "certificate", 6.5)
               + "".join(f'<rect x="72" y="{y}" width="{w}" height="3.4" rx="1.7" fill="#d0d5dd"/>'
                         for y, w in ((26, 34), (33, 28), (40, 31), (47, 22)))
               + good(102, 61, 7) + text(88, 64, "Lean", 8, GOOD, "end", 800))
    # ---- E: the machine as data (Lean) --------------------------------------------------------
    def jsq(x, y, real=True):
        """A node where rails meet: a true junction (three or more rails) grey, as the studio
        draws it; a bend (two) faded gold."""
        if real:
            return f'<rect x="{_n(x - 6)}" y="{_n(y - 6)}" width="12" height="12" rx="1.5" fill="#fff" stroke="#8a94a6" stroke-width="2.3"/>'
        return (f'<rect x="{_n(x - 5)}" y="{_n(y - 5)}" width="10" height="10" rx="1.5" fill="#fff" stroke="#f6c34a" '
                f'stroke-width="2" opacity=".55"/>')

    # grid2x3 upright: junction columns at x 34 / 86, rows at y 10 / 40 / 70
    gx, gy = (34, 86), (10, 40, 70)
    grid = "".join(rail(gx[0], y, gx[1], y) for y in gy) + "".join(rail(x, gy[0], x, gy[2]) for x in gx)
    grid += "".join(jsq(x, y, real=(y == 40)) for x in gx for y in gy)
    grid += "".join(site(60, y, n=2, gap=7) for y in gy) + "".join(site(x, y, n=2, v=True, gap=5) for x in gx for y in (25, 55))
    d["E1"] = (grid + ion(56.5, 40, D0)
               + arrow("M54 35Q44 31 39 27", ARROW, w=1.5) + arrow("M54 45Q44 49 39 53", ARROW, w=1.5)
               + arrow("M66 35Q76 31 81 27", ARROW, w=1.5) + arrow("M66 45Q76 49 81 53", ARROW, w=1.5)
               + text(23, 43, "junction", 6, "#475467", "end") + text(97, 43, "junction", 6, "#475467", "start")
               + text(23, 12.5, "bend", 6, "#b8902a", "end") + text(97, 72.5, "bend", 6, "#b8902a", "start"))

    # chain4, compact: four two-ion sites
    def chain4(y, xs=(15, 45, 75, 105), ions=()):
        g = "".join(rail(xs[i], y, xs[i + 1], y) for i in range(3)) + "".join(site(x, y, n=2, gap=7) for x in xs)
        for slot_x, colour in ions:
            g += ion(slot_x, y, colour)
        return g

    steps = (("H", (("C1", D0), ("C0", D1))), ("C1→C0", (("C0", D0), ("C0", D1))), ("cool", (("C0", D0), ("C0", D1))),
             ("CX", (("C0", D0), ("C0", D1))), ("M", (("C0", D0), ("C0", D1))))
    strip = ""
    for k, (label, state) in enumerate(steps):
        x0 = 6 + k * 22.4
        strip += (f'<rect x="{_n(x0)}" y="38" width="19" height="12" rx="3" fill="#fff" stroke="#c3cbd8" stroke-width="1.1"/>'
                  + text(x0 + 9.5, 46.4, label, 5.6 if len(label) > 3 else 6.5, "#182230", weight=700))
        strip += f'<path d="M{_n(x0 + 1)} 60H{_n(x0 + 18)}" stroke="#f2b8b5" stroke-width="1.6" stroke-linecap="round"/>'
        strip += "".join(f'<rect x="{_n(x0 + 0.6 + i * 4.8)}" y="57" width="4" height="6" rx="1.6" fill="#d3eeea"/>'
                         for i in range(4))
        together = len({w for w, _ in state}) == 1
        for j, (where, colour) in enumerate(state):
            xi = x0 + 2.6 + {"C0": 0, "C1": 1}[where] * 4.8 + ((j - 0.5) * 1.5 if together else 0)
            strip += f'<circle cx="{_n(xi)}" cy="60" r="1.45" fill="{colour}"/>'
        if k < 4:
            strip += f'<path d="M{_n(x0 + 19.4)} 44h2.6" stroke="#98a2b3" stroke-width="1"/>'
    d["E2"] = (chain4(16, ions=((40.5, D0), (11.5, D1))) + text(45, 30, "q0", 6, D0, weight=700) + text(15, 30, "q1", 6, D1, weight=700)
               + strip + text(60, 76, "replay: the state after each statement", 6.2))

    def mini2(x0, y, q0_at_c0):
        """C0 and C1 of chain4 at the CX: q1 always in C0, q0 in C0 or still in C1."""
        g = rail(x0, y, x0 + 26, y) + rail(x0 + 26, y, x0 + 40, y) + site(x0, y, n=2, gap=7) + site(x0 + 26, y, n=2, gap=7)
        g += ion(x0 - 3.5, y, D1) + (ion(x0 + 3.5, y, D0) if q0_at_c0 else ion(x0 + 26 - 3.5, y, D0))
        return g + beam(x0, y - 26, y - 8)

    d["E3"] = (mini2(16, 38, True) + mini2(76, 38, False)
               + text(30, 52, "bell", 7, "#182230", weight=700) + text(90, 52, "early", 7, "#182230", weight=700)
               + good(48, 16) + pill(103, 16, "R6b", BAD, 20)
               + '<path d="M60 6V74" stroke="#e4e7ec" stroke-width="1.2"/>'
               + pill(11, 67, "R1", GOOD, 12) + pill(24, 67, "R2", GOOD, 12) + pill(37, 67, "R6", GOOD, 12) + pill(51, 67, "R6b", GOOD, 15)
               + pill(71, 67, "R1", GOOD, 12) + pill(84, 67, "R2", GOOD, 12) + pill(97, 67, "R6", GOOD, 12) + pill(111, 67, "R6b", BAD, 15))

    # ring6d's loop (data sites: no gates) with an ion under a one-ion gate at S1
    loop = ('<rect x="8" y="18" width="48" height="42" rx="14" fill="none" stroke="#f2b8b5" stroke-width="4.5"/>'
            + "".join(site(x, y, "data") for x, y in ((19, 18), (32, 18), (45, 18), (45, 60), (32, 60), (19, 60)))
            + ion(32, 18, D0) + beam(32, 2, 10, D1) + text(32, 41, "S1", 7, "#667085", weight=700))
    card = lambda y, title, rules, ok: (
        f'<rect x="64" y="{y}" width="52" height="30" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
        + text(68, y + 10, title, 6.8, "#182230", "start", 700) + text(68, y + 22, rules, 5.6, "#667085", "start", 600, True)
        + (good(108, y + 9, 5.5) if ok else bad(108, y + 9, 5.5)))
    d["E4"] = loop + card(8, "standard", "R1 R2 … R6 …", False) + card(44, "cyclone", "R1 R2 … no R6", True)

    # ---- F: certificates and the proved checker ---------------------------------------------
    cert = lambda x, y, w=34, h=30: (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
        + "".join(f'<rect x="{x + 5}" y="{y + 6 + i * 6}" width="{w - 10 - (i % 2) * 6}" height="2.8" rx="1.4" fill="#d0d5dd"/>'
                  for i in range((h - 8) // 6)))
    d["F2"] = (chain4(16, ions=((11.5, D0), (18.5, D1))) + beam(15, 1, 9)
               + "".join(f'<rect x="{_n(4.5 + i * 10.2)}" y="30" width="9" height="11" rx="2.5" fill="{BAD if i == 4 else GOOD}"/>'
                         + text(9 + i * 10.2, 37.8, str(i + 1), 5.6, "#fff", weight=800) for i in range(11))
               + cert(6, 48, 26, 26) + text(38, 59, "Implements:", 6.6, "#182230", "start", 700)
               + text(38, 69, "11 conditions, 1 broken", 6, "#667085", "start"))
    box = lambda x, y, w, h, label, c="#182230", size=6.5, mono=True, fill="#fff": (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3.5" fill="{fill}" stroke="#c3cbd8" stroke-width="1.1"/>'
        + text(x + w / 2, y + h / 2 + size * .36, label, size, c, weight=700, mono=mono))
    d["F3"] = (box(3, 6, 50, 14, "Implements bell", size=6, mono=False) + arrow("M55 13H62", MUTED, w=1.2)
               + box(64, 6, 26, 14, "decide", c="#6a4fd0", size=6) + arrow("M92 13H99", MUTED, w=1.2)
               + pill(108, 13, "true", GOOD, 17)
               + box(3, 28, 114, 14, "", fill="#f2f4f7") + text(8, 37.5, "#eval", 6.2, "#667085", "start", 700, True)
               + text(40, 37.5, "runs it, proves nothing", 6, "#667085", "start")
               + box(3, 45, 114, 14, "", fill="#e7f5ec") + text(8, 54.5, "decide +kernel", 6.2, GOOD, "start", 700, True)
               + text(62, 54.5, "kernel computes it", 5.8, "#182230", "start")
               + box(3, 62, 114, 14, "", fill="#fdf3d9") + text(8, 71.5, "native_decide", 6.2, "#8a6d1f", "start", 700, True)
               + text(58, 71.5, "trusts the compiler", 5.8, "#8a6d1f", "start"))
    d["F4"] = (f'<rect x="4" y="24" width="40" height="32" rx="4" fill="#fff" stroke="#6a4fd0" stroke-width="1.4"/>'
               + text(24, 37, "check c", 6.6, "#182230", weight=700, mono=True) + text(24, 46, "= true", 6.6, "#182230", weight=700, mono=True)
               + text(24, 64, "a Bool", 6, "#667085")
               + f'<rect x="76" y="24" width="40" height="32" rx="4" fill="#fff" stroke="#0f766e" stroke-width="1.4"/>'
               + text(96, 37, "Implements", 6.2, "#182230", weight=700, mono=True) + text(96, 46, "c", 6.6, "#182230", weight=700, mono=True)
               + text(96, 64, "a Prop", 6, "#667085")
               + arrow("M46 32H73", GOOD, w=1.6) + arrow("M74 48H47", CHK, w=1.6)
               + text(60, 16, "check_sound", 6.2, GOOD, weight=700, mono=True) + text(60, 76, "check_complete", 6.2, CHK, weight=700, mono=True)
               + '<path d="M60 18V28M60 52V68" stroke="#e4e7ec" stroke-width="1"/>')

    def tinychain(y, ions, mark, label, note=None):
        g = (f'<path d="M8 {y}H44" stroke="#f2b8b5" stroke-width="2.6" stroke-linecap="round"/>'
             + "".join(f'<rect x="{_n(6 + i * 11)}" y="{_n(y - 3.5)}" width="8" height="7" rx="3" fill="#d3eeea" stroke="#0f766e" stroke-width=".9"/>'
                       for i in range(4)))
        g += "".join(f'<circle cx="{_n(10 + p * 11 + dx)}" cy="{y}" r="2.1" fill="{c}"/>' for p, dx, c in ions)
        g += text(52, y + 2.2, label, 6, "#182230", "start", 600, True)
        g += (good(112, y, 4.6) if mark else bad(112, y, 4.6))
        if note:
            g += text(52, y + 9.5, note, 5.4, GOOD, "start", 600)
        return g
    ghz = ((0, -1.6, D0), (0, 1.6, D1), (1, 0, D2), (2, 0, CHK))
    d["F5"] = (tinychain(10, ghz, True, "the GHZ chain") + '<path d="M6 18H114" stroke="#e4e7ec" stroke-width="1"/>'
               + tinychain(27, ((0, -1.6, D0), (3, 0, D1), (1, 0, D2), (2, 0, CHK)), False, "teleport")
               + tinychain(40, ghz, False, "shifted witness")
               + tinychain(53, ghz + ((3, 0, "#98a2b3"),), False, "foreign ions")
               + tinychain(66, ((0, -1.6, D1), (0, 1.6, D0), (1, 0, D2), (2, 0, CHK)), True, "swap_operands",
                           "the pulses refuse it"))
    layers = (("Lean kernel + propext", "proved", GOOD), ("parser Input.ofJson", "believed", "#c9971a"),
              ("device facts (hops)", "believed", "#c9971a"), ("check_cert's semantics", "computed", MEAS))
    d["F6"] = "".join(f'<rect x="6" y="{6 + i * 18}" width="108" height="15" rx="3" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
                      + text(11, 16 + i * 18, name, 6, "#182230", "start", 600)
                      + pill(96, 13.5 + i * 18, tag, colour, 34)
                      for i, (name, tag, colour) in enumerate(layers))

    # ---- H: verify the website yourself -------------------------------------------------------
    chips = "".join(f'<rect x="{_n(7 + (i % 10) * 10.8)}" y="{6 + (i // 10) * 11}" width="8.4" height="8.4" rx="2" '
                    f'fill="{GOOD}" opacity="{1 if i == 12 else .55}"' + (' stroke="#1e2761" stroke-width="1.6"' if i == 12 else "") + "/>"
                    for i in range(19))
    d["H1"] = (chips + text(116, 33, "19 certificates", 5.8, "#667085", "end")
               + arrow("M33 27V34", MUTED, w=1.1)
               + chain4(56, xs=(18, 46, 74, 102), ions=((14.5, D0), (21.5, D1), (49.5, D2), (77.5, CHK)))
               + "".join(f'<path d="M{a} 46Q{(a + b) / 2} 38 {b} 46" stroke="{GATE}" stroke-width="1.4" fill="none"/>'
                         for a, b in ((14, 22), (22, 46), (46, 74), (74, 102)))
               + text(60, 74, "the GHZ chain: four CNOT meetings", 6))
    d["H2"] = (f'<rect x="4" y="6" width="84" height="16" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
               + '<circle cx="13" cy="14" r="5.5" fill="#0b7a4b"/>' + text(13, 16.5, "1", 6.5, "#fff", weight=800)
               + text(22, 16.6, "rep9", 6.8, "#182230", "start", 700) + '<rect x="40" y="11" width="24" height="5.5" rx="2.5" fill="#e4e7ec"/>'
               + pill(77, 14, "R10", GOOD, 17)
               + '<circle cx="80" cy="16" r="10" fill="none" stroke="#1e2761" stroke-width="2"/>'
               + '<path d="M87 23l8 8" stroke="#1e2761" stroke-width="3.4" stroke-linecap="round"/>'
               + rail(12, 54, 84, 54) + site(26, 54, n=9, gap=3.6) + site(70, 54, n=8, gap=3.6)
               + "".join(f'<circle cx="{_n(26 + (i - 4) * 3.6)}" cy="54" r="1.6" fill="{(D0, D1, D2, CHK)[i % 4]}"/>' for i in range(9))
               + "".join(f'<circle cx="{_n(70 + (i - 3.5) * 3.6)}" cy="54" r="1.6" fill="{(D1, D2, CHK, D0)[i % 4]}"/>' for i in range(8))
               + text(26, 68, "C0", 6, "#0f766e", weight=700) + text(70, 68, "C1", 6, "#0f766e", weight=700)
               + text(48, 77, "stationary_chain, 17 ions", 5.8) + good(104, 54, 7) + text(104, 70, "proved", 5.8, GOOD, weight=700))
    stages = ("bundle", "device", "physics_lock", "program", "rules", "correspondence", "certificate_binding",
              "lean_certificate", "semantics", "metrics")
    d["H3"] = ('<path d="M10 30H110" stroke="#c3cbd8" stroke-width="1.2"/>'
               + "".join(f'<rect x="{_n(5.5 + i * 11.1)}" y="25.5" width="9" height="9" rx="2" '
                         f'fill="{GATE if s in ("lean_certificate", "semantics") else "#fff"}" stroke="{GATE if s in ("lean_certificate", "semantics") else "#98a2b3"}" stroke-width="1.1"/>'
                         + text(10 + i * 11.1, 32.4, str(i + 1), 5.2, "#fff" if s in ("lean_certificate", "semantics") else "#667085", weight=700)
                         for i, s in enumerate(stages))
               + text(10, 46, "bundle", 5.6, "#667085", "start") + text(112, 46, "metrics", 5.6, "#667085", "end")
               + '<path d="M83 22V18H103V22" stroke="' + GATE + '" stroke-width="1.2" fill="none"/>' + text(93, 15, "R10", 6.6, GATE, weight=800)
               + text(114, 60, "lean_certificate", 6.2, GATE, "end", 700, True) + text(114, 70, "semantics", 6.2, GATE, "end", 700, True)
               + text(10, 66, "R10 needs", 6, "#667085", "start") + text(10, 74, "these two", 6, "#667085", "start")
               + text(10, 15, "ten stages", 5.8, "#667085", "start"))
    flow = ("circuit.qasm + device", "qccdc compile", "insert_cooling", "mk_qcheck_input", "check_cert --qcheck")
    d["H4"] = ('<rect x="3" y="3" width="114" height="74" rx="5" fill="#1f2430"/>'
               + "".join(text(9, 15 + i * 12.2, ("$ " if i else "  ") + s, 6.2, "#e6edf3" if i else "#9fb3c8", "start", 600, True)
                         for i, s in enumerate(flow))
               + text(9, 73, "  R10 passed", 6.6, "#7ee787", "start", 800, True) + good(76, 70.8, 4.6))
    # ---- G: which gate the pulses make (exact numbers, matrices, tableaux) ---------------------
    def tile(x, y, w, n, label=None, c="#6a4fd0", fill="#f1edfd", lsize=5.6, h=None):
        """A pulse as the exact matrix it is: an n x n grid (h: drawn taller, spanning two ions)."""
        h = w if h is None else h
        g = f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="2" fill="{fill}" stroke="{c}" stroke-width="1.2"/>'
        g += "".join(f'<path d="M{_n(x + w * k / n)} {_n(y)}V{_n(y + h)}M{_n(x)} {_n(y + h * k / n)}H{_n(x + w)}" stroke="{c}" stroke-width=".5" opacity=".6"/>'
                     for k in range(1, n))
        if label:
            g += text(x + w / 2, y + h / 2 + lsize * .36, label, lsize, c, weight=800)
        return g

    import math
    pts = [(31 + 25 * math.cos(-k * math.pi / 4), 40 + 25 * math.sin(-k * math.pi / 4)) for k in range(8)]
    d["G1"] = ('<circle cx="31" cy="40" r="25" fill="none" stroke="#e4e7ec" stroke-width="1"/>'
               '<path d="M4 40H58M31 13V67" stroke="#e4e7ec" stroke-width="1"/>'
               + f'<path d="M{" L".join(f"{_n(x)} {_n(y)}" for x, y in pts)}Z" fill="none" stroke="#c2308a" stroke-width="1" opacity=".5"/>'
               + "".join(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{3.2 if k == 1 else 2.2}" fill="{"#c2308a" if k == 1 else "#1e2761"}"/>'
                         for k, (x, y) in enumerate(pts))
               + text(60, 43, "1", 6.5, "#1e2761", "start", 700) + text(31, 10.5, "i", 6.5, "#1e2761", weight=700)
               + text(7, 32, "−1", 6.5, "#1e2761", weight=700) + text(31, 75, "−i", 6.5, "#1e2761", weight=700)
               + text(52, 18, "ω", 8, "#c2308a", "start", 800)
               + text(90, 22, "⟨a, b, c, d, k⟩", 6.4, "#182230", weight=700, mono=True)
               + text(90, 38, "(a + b·i +", 6.2, "#475467") + text(90, 48, "(c + d·i)·√2)", 6.2, "#475467")
               + text(90, 58, "÷ 2ᵏ", 6.2, "#475467") + text(90, 72, "one spelling each", 5.8, "#667085"))

    # two ion lines, q0 above q1, and the pulses on them in order
    def lines(y0, y1, x0=8, x1=112):
        return (f'<path d="M{x0} {y0}H{x1}M{x0} {y1}H{x1}" stroke="#98a2b3" stroke-width="1"/>'
                + text(x0 - 3, y0 + 2, "q0", 5.4, D0, "end", 700) + text(x0 - 3, y1 + 2, "q1", 5.4, D1, "end", 700))
    d["G2"] = (chain4(12, ions=((41.5, D0), (11.5, D1)))
               + lines(38, 58, x0=14)
               + tile(18, 33, 10, 2) + tile(31, 33, 10, 2, c="#e8890c", fill="#fdf0e2")
               + tile(46, 33, 10, 2) + tile(59, 33, 14, 4, c="#c2308a", fill="#fbe7f2", h=30)
               + tile(77, 33, 10, 2) + tile(77, 53, 10, 2) + tile(90, 33, 10, 2)
               + text(29.5, 76, "H: R, VZ", 5.8, "#667085") + text(80, 76, "CX: R, MS, R, R, R", 5.8, "#667085"))
    d["G3"] = (tile(8, 10, 22, 2) + text(19, 42, "VZ(φ+λ)", 5.8, "#6a4fd0", weight=700)
               + tile(44, 10, 22, 2) + text(55, 42, "R(θ, π/2−λ)", 5.8, "#6a4fd0", weight=700)
               + arrow("M32 21Q60 6 84 16", MUTED, w=1.1) + arrow("M68 21H84", MUTED, w=1.1)
               + tile(88, 10, 22, 2, c="#0f766e", fill="#e3f4f1") + text(99, 42, "u3(θ,φ,λ)", 5.8, "#0f766e", weight=800)
               + '<path d="M6 52H114" stroke="#e4e7ec" stroke-width="1"/>'
               + text(60, 66, "H = VZ(π) · R(π/2, −π/2)", 6.2, "#182230", weight=700, mono=True)
               + text(60, 76, "as the h certificate's witness has it", 5.6, "#667085"))
    d["G4"] = (lines(24, 50, x0=14, x1=72)
               + tile(16, 19, 10, 2) + tile(29, 19, 16, 4, c="#c2308a", fill="#fbe7f2", h=36)
               + tile(48, 19, 10, 2) + tile(48, 45, 10, 2) + tile(61, 19, 10, 2)
               + text(37, 64, "MS(π/2)", 5.6, "#c2308a", weight=800)
               + text(80, 40, "=", 11, "#182230", weight=700)
               + f'<path d="M92 24H112M92 50H112M102 24V56" stroke="#182230" stroke-width="1.3"/>'
               + f'<circle cx="102" cy="24" r="3" fill="#182230"/><circle cx="102" cy="50" r="6" fill="#fff" stroke="#182230" stroke-width="1.3"/>'
               + '<path d="M96 50h12M102 44v12" stroke="#182230" stroke-width="1.3"/>'
               + '<circle cx="104" cy="70" r="6" fill="#fff" stroke="#98a2b3" stroke-width="1"/>'
               + '<path d="M104 70L108.2 74.2" stroke="#c2308a" stroke-width="1.6" stroke-linecap="round"/>'
               + text(96, 73, "e^(−iπ/4)", 5.4, "#667085", "end", 600))
    rows = (("X₀", "Z⊗1"), ("Z₀", "X⊗X"), ("X₁", "1⊗X"), ("Z₁", "Z⊗Z"))      # the lesson's labels: qubits from 0
    table = lambda x, head: (f'<rect x="{x}" y="8" width="48" height="64" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
                             + text(x + 24, 19, head, 6.2, "#667085", weight=700)
                             + f'<path d="M{x + 4} 23H{x + 44}" stroke="#e4e7ec" stroke-width="1"/>'
                             + "".join(text(x + 24, 34 + i * 11, f"{a} ↦ {b}", 6.2, "#182230", weight=600, mono=True) for i, (a, b) in enumerate(rows)))
    d["G5"] = table(4, "the circuit") + text(60, 44, "=", 11, GOOD, weight=800) + table(68, "the pulses")
    d["G6"] = ('<path d="M10 16H54M10 32H54" stroke="#98a2b3" stroke-width="1"/>'
               + text(8, 18, "q", 5.6, D0, "end", 700) + text(8, 34, "|0⟩", 5.2, "#667085", "end", 600)
               + f'<path d="M24 16V35" stroke="#182230" stroke-width="1.2"/><circle cx="24" cy="16" r="2.4" fill="#182230"/>'
               + '<circle cx="24" cy="32" r="4.4" fill="#fff" stroke="#182230" stroke-width="1.2"/><path d="M19.6 32h8.8M24 27.6v8.8" stroke="#182230" stroke-width="1.2"/>'
               + f'<rect x="40" y="26" width="12" height="11" rx="2" fill="#fff" stroke="{MEAS}" stroke-width="1.2"/>'
               + f'<path d="M42.5 34.5a3.5 3.5 0 0 1 7 0M46 34.5l2.2-3.8" stroke="{MEAS}" stroke-width="1" fill="none"/>'
               + text(32, 46, "a readout is a CNOT", 5.4, "#667085")
               + '<path d="M10 56H54M10 70H54" stroke="#98a2b3" stroke-width="1"/>'
               + text(8, 58, "q", 5.6, D0, "end", 700) + text(8, 72, "|0⟩", 5.2, "#667085", "end", 600)
               + '<path d="M30 56V70M27 53l6 6M33 53l-6 6M27 67l6 6M33 67l-6 6" stroke="#182230" stroke-width="1.3"/>'
               + text(44, 63, "reset", 5.6, "#667085", "start")
               + '<path d="M62 6V74" stroke="#e4e7ec" stroke-width="1.2"/>'
               + pill(77, 24, "CX", GATE, 16) + pill(96, 24, "M", MEAS, 12) + good(112, 24, 4.6)
               + pill(77, 52, "M", MEAS, 12) + pill(96, 52, "CX", GATE, 16) + bad(112, 52, 4.6)
               + text(90, 38, "in order", 5.6, "#667085") + text(90, 66, "moved early", 5.6, BAD, weight=700))
    d["G7"] = (rail(14, 14, 40, 14) + rail(40, 14, 56, 14) + site(14, 14, n=2, gap=7) + site(40, 14, n=2, gap=7)
               + ion(36.5, 14, D0) + ion(43.5, 14, D1, ghost=True) + ion(17.5, 14, D1) + arrow("M40 5Q30 1 21 6", ARROW, w=1.2)
               + tile(8, 30, 24, 4, c="#c2308a", fill="#fbe7f2")
               + text(37, 39, "= CX·(H⊗1)", 6.2, "#182230", "start", 700, True) + text(37, 49, "up to a phase", 5.6, "#667085", "start")
               + text(38, 68, "two witnesses, composed", 5.4, "#667085")
               + '<path d="M76 6V74" stroke="#e4e7ec" stroke-width="1.2"/>'
               + text(98, 14, "swap_operands", 5.2, "#182230", weight=700, mono=True)
               + good(98, 30, 5.5) + text(98, 44, "checker", 5.8, GOOD, weight=700)
               + bad(98, 56, 5.5) + text(98, 70, "semantics", 5.8, BAD, weight=700))
    # ---- I: build your own verified compiler ---------------------------------------------------
    def strip(x, y, w=28, faded=1.0):
        """A circuit: two wires, h, two cx, a measurement."""
        g = (f'<g opacity="{faded}"><rect x="{_n(x)}" y="{_n(y)}" width="{_n(w + 4)}" height="18" rx="2.5" fill="#fff" stroke="#d0d5dd" stroke-width="1"/>'
             f'<path d="M{_n(x + 2)} {_n(y + 6)}H{_n(x + w + 2)}M{_n(x + 2)} {_n(y + 13)}H{_n(x + w + 2)}" stroke="#98a2b3" stroke-width=".8"/>'
             f'<rect x="{_n(x + 4)}" y="{_n(y + 3.5)}" width="5" height="5" rx="1" fill="{D1}"/>')
        for cx_ in (x + 13, x + 19):
            g += (f'<path d="M{_n(cx_)} {_n(y + 6)}V{_n(y + 15)}" stroke="{GATE}" stroke-width=".9"/><circle cx="{_n(cx_)}" cy="{_n(y + 6)}" r="1.3" fill="{GATE}"/>'
                  f'<circle cx="{_n(cx_)}" cy="{_n(y + 13)}" r="2.2" fill="#fff" stroke="{GATE}" stroke-width=".9"/>')
        g += f'<rect x="{_n(x + 24)}" y="{_n(y + 3.5)}" width="5" height="12" rx="1" fill="#fff" stroke="{MEAS}" stroke-width=".9"/></g>'
        return g

    compile_box = lambda x, y, w=24, h=18: (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3.5" fill="#1e2761"/>'
                                            + text(x + w / 2, y + h / 2 + 2.2, "compile", 6, "#fff", weight=700))
    d["I1"] = (strip(3, 8) + arrow("M37 17H41", MUTED, w=1.1) + compile_box(43, 8)
               + arrow("M68 17H76", MUTED, w=1.1) + arrow("M55 27V40", MUTED, w=1.1)
               + rail(12, 56, 72, 56) + "".join(site(x, 56) for x in (12, 32, 52, 72))
               + ion(12, 56, D0) + ion(32, 56, D1) + arrow("M16 47Q22 41 28 47", ARROW, w=1.3) + arrow("M28 65Q22 71 16 65", ARROW, w=1.3)
               + '<rect x="80" y="6" width="36" height="46" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
               + "".join(text(84, 17 + i * 11, s, 5.6, "#475467", "start", 600) for i, s in enumerate(("map", "moves", "witnesses")))
               + good(98, 64, 6) + text(98, 76, "checker", 5.8, GOOD, weight=700))
    d["I2"] = (strip(9, 4, faded=.25) + strip(6, 9, faded=.5) + strip(3, 14)
               + text(19, 42, "every circuit", 5.8, "#667085")
               + arrow("M37 23H41", MUTED, w=1.1) + compile_box(43, 14) + arrow("M68 23H72", MUTED, w=1.1)
               + '<rect x="74" y="4" width="42" height="44" rx="4" fill="#fffaf0" stroke="#c9971a" stroke-width="1.3"/>'
               + text(95, 15, "Implements", 6, "#182230", weight=700, mono=True) + text(95, 24, "(compile", 5.6, "#182230", weight=600, mono=True)
               + text(95, 32, "home c)", 5.6, "#182230", weight=600, mono=True)
               + '<circle cx="95" cy="41" r="4.5" fill="#e8b84b"/><path d="M92.8 41.2l1.6 1.6 2.8-3.2" stroke="#fff" stroke-width="1.3" fill="none" stroke-linecap="round"/>'
               + '<path d="M6 52H114" stroke="#e4e7ec" stroke-width="1"/>'
               + text(8, 63, "I1: checked once per output", 6, "#667085", "start")
               + text(8, 74, "I2: proved once, for every output", 6, "#c9971a", "start", 700))
    langs = ("Python", "OCaml", "Rust")
    d["I3"] = ("".join(f'<rect x="3" y="{6 + i * 24}" width="30" height="16" rx="3" fill="#1e2761"/>'
                       + text(18, 16.4 + i * 24, s, 6, "#fff", weight=700) + arrow(f"M34 {14 + i * 24}L44 40", MUTED, w=1)
                       for i, s in enumerate(langs))
               + '<path d="M46 31h11l4 4v14h-15z" fill="#fff" stroke="#475467" stroke-width="1.2" stroke-linejoin="round"/>'
               + text(53.5, 44, "{ }", 6.4, "#475467", weight=800, mono=True) + text(53.5, 58, "JSON", 5.4, "#667085", weight=700)
               + arrow("M62 40H64", MUTED, w=1.1)
               + '<rect x="66" y="30" width="22" height="20" rx="3.5" fill="#fff" stroke="#6a4fd0" stroke-width="1.4"/>'
               + text(77, 39, "checker", 5.2, "#6a4fd0", weight=800) + text(77, 46.5, "proved", 4.8, "#6a4fd0", weight=600)
               + arrow("M89 36L93 27", GOOD, w=1) + arrow("M89 44L93 53", BAD, w=1)
               + f'<rect x="90" y="16" width="28" height="9" rx="4.5" fill="{GOOD}"/>' + text(104, 22.2, "ACCEPTED", 4.8, "#fff", weight=800)
               + f'<rect x="90" y="55" width="28" height="9" rx="4.5" fill="{BAD}"/>' + text(104, 61.2, "REJECTED", 4.8, "#fff", weight=800))
    slots = ("circuit ops", "qubit map", "init", "moves", "rotations", "witnesses")
    d["I4"] = ('<rect x="3" y="3" width="56" height="74" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
               + "".join(f'<rect x="7" y="{7 + i * 11.6}" width="48" height="9" rx="2" fill="#f2f4f7"/>'
                         + text(10, 13.4 + i * 11.6, s, 5.4, "#182230", "start", 600) for i, s in enumerate(slots))
               + '<path d="M63 6V74" stroke="#e4e7ec" stroke-width="1"/>'
               + text(90, 9, "from the device", 5.4, "#667085", weight=700)
               + site(77, 24, n=2, gap=6) + beam(77, 13, 19) + text(117, 26, "gate sites", 5.2, "#475467", "end")
               + rail(67, 45, 89, 45) + jsq(79, 45) + site(68, 45) + arrow("M70 38Q79 32 88 38", ARROW, w=1)
               + text(117, 47, "a hop", 5.2, "#475467", "end")
               + '<rect x="67" y="60" width="22" height="13" rx="6.5" fill="none" stroke="#f2b8b5" stroke-width="2.6"/>'
               + "".join(f'<circle cx="{x}" cy="{y}" r="2" fill="#0f766e"/>' for x, y in ((73, 60), (83, 60), (83, 73), (73, 73)))
               + arrow("M74 55Q78 52 82 55", ARROW, w=1) + text(117, 69, "loop order", 5.2, "#475467", "end"))
    pairs = "".join(f'<rect x="{_n(6 + (i % 12) * 6.4)}" y="{_n(48 + (i // 12) * 6.4)}" width="5" height="5" rx="1" '
                    f'fill="{BAD if i == 20 else GOOD}" opacity="{1 if i == 20 else .7}"/>' for i in range(36))
    d["I5"] = ('<rect x="3" y="4" width="114" height="16" rx="4" fill="#fff" stroke="#d0d5dd" stroke-width="1.1"/>'
               + '<circle cx="12" cy="12" r="5" fill="#0b7a4b"/>' + text(12, 14.4, "1", 6.2, "#fff", weight=800)
               + text(20, 14.6, "your compiler", 6, "#182230", "start", 700, True) + text(112, 14.6, "vs the reference", 5.4, "#667085", "end")
               + text(6, 31, "speedup, geometric mean", 5.4, "#667085", "start")
               + '<rect x="6" y="34" width="70" height="6" rx="3" fill="#e4e7ec"/><rect x="6" y="34" width="49" height="6" rx="3" fill="#1f7fa8"/>'
               + text(80, 39.5, "1.4×", 6.6, "#1f7fa8", "start", 800)
               + pairs + text(6, 76, "every circuit × device pair", 5.2, "#667085", "start")
               + f'<rect x="90" y="47" width="18" height="18" rx="4" fill="{BAD}"/>'
               + '<path d="M94.5 51.5l9 9M103.5 51.5l-9 9" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'
               + text(99, 74, "not eligible", 5, BAD, weight=700))

    # ---- J: gadgets and fault-tolerant architecture, in the gadget page's own vocabulary --------
    try:
        from ..gadget.categories import CATEGORIES
        from ..gadget.svg import shape_d, shape_detail
    except ImportError:              # a tree without the gadget tool: Part J's cards fall back
        return d

    def place(cat, x, y, w, h, glyph=None, sw=1.3):
        c = CATEGORIES[cat]
        g = f'<path d="{shape_d(c["shape"], x, y, w, h)}" fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="{sw}"/>'
        dd = shape_detail(c["shape"], x, y, w, h)
        if dd:
            g += f'<path d="{dd}" fill="none" stroke="{c["stroke"]}" stroke-width="1"/>'
        if glyph:
            g += text(x + w / 2, y + h * .62 + 2, glyph, min(6.4, h * .32), c["stroke"], weight=800)
        return g

    def bundle(x, y, n=3, c="#475467", op=1.0):
        return "".join(f'<circle cx="{_n(x + (i - (n - 1) / 2) * 3.6)}" cy="{_n(y)}" r="1.6" fill="{c}" opacity="{op}"/>' for i in range(n))

    GOLD, FRESH, BUS = "#e8b84b", "#98a2b3", CATEGORIES["wire"]["stroke"]
    door = lambda x, y: f'<rect x="{_n(x - 1.6)}" y="{_n(y - 4)}" width="3.2" height="8" rx="1" fill="#fff" stroke="#1c2a4a" stroke-width="1"/>'
    d["J1"] = (place("factory", 34, 16, 52, 40, "MSF", 1.6) + door(34, 44) + door(86, 44)
               + arrow("M6 44H30", MUTED, w=1.3) + ion(14, 44, FRESH) + text(16, 38, "in", 6, "#667085", weight=700)
               + arrow("M90 44H114", MUTED, w=1.3) + f'<circle cx="104" cy="44" r="4.2" fill="{GOLD}" stroke="#fff" stroke-width="1.3"/>'
               + text(102, 38, "out", 6, "#667085", weight=700) + good(84, 16, 6)
               + text(60, 72, "verified once, used as a box", 6.2))
    lanes = (("factory", 8, 30), ("surgery", 22, 48), ("memory", 4, 40))
    d["J2"] = (place("factory", 6, 6, 30, 20) + place("surgery", 45, 6, 30, 20) + place("memory", 84, 6, 30, 20)
               + "".join(f'<rect x="{_n(6 + s)}" y="{32 + k * 6}" width="{e}" height="4.4" rx="2" fill="{CATEGORIES[c]["stroke"]}" opacity=".75"/>'
                         for k, (c, s, e) in enumerate(lanes))
               + '<path d="M6 51H114" stroke="#c3cbd8" stroke-width="1"/>' + text(114, 49, "time", 5.4, "#98a2b3", "end")
               + rail(28, 66, 66, 66) + bundle(47, 66) + bundle(17, 66, op=.4) + text(17, 76, "waits", 5.6, "#667085")
               + text(47, 76, "one bundle per road", 5.6, "#667085", "start") + text(92, 69, "at once", 6.4, GOOD, weight=700))
    jx = (19, 45, 71, 97)
    street = "".join(rail(jx[i], 24, jx[i + 1], 24) for i in (0, 1)) + "".join(rail(x, 24, x, 42) for x in jx)
    d["J3"] = (street
               + f'<path d="M71 24H97" stroke="#f2b8b5" stroke-width="4.5" stroke-dasharray="2 4" opacity=".7"/>'
               + "".join(jsq(x, 24) for x in jx)
               + place("factory", 8, 42, 22, 18) + place("surgery", 34, 42, 22, 18) + place("memory", 60, 42, 22, 18)
               + place("readout", 86, 42, 22, 18)
               + f'<path d="M19 42V24H71V42" stroke="{ARROW}" stroke-width="2.4" fill="none" stroke-linejoin="round" opacity=".9"/>'
               + bad(84, 24, 4.4)
               + '<rect x="84" y="8" width="32" height="58" rx="6" fill="none" stroke="#d92d20" stroke-width="1.3" stroke-dasharray="3 2.4"/>'
               + text(100, 74, "closed", 6, BAD, weight=700) + text(45, 74, "door to door", 6, ARROW, weight=700))
    d["J4"] = (place("factory", 3, 12, 22, 18) + arrow("M26 21H30", MUTED, w=1.1)
               + place("injection", 32, 12, 22, 18) + arrow("M55 21H58", MUTED, w=1.1)
               + place("surgery", 60, 12, 24, 18) + place("memory", 63, 34, 18, 13)
               + text(60, 43, "data", 5.2, CATEGORIES["memory"]["stroke"], "end", 700) + arrow("M85 21H89", MUTED, w=1.1)
               + place("readout", 91, 12, 26, 18)
               + f'<path d="M104 30V60H88" stroke="{BUS}" stroke-width="1.3" stroke-dasharray="3 2" fill="none"/>'
               + place("archive", 58, 54, 30, 14) + text(52, 63, "if a: S̄", 6, "#182230", "end", 700, True)
               + pill(16, 42, "x m", "#667085", 18) + text(16, 53, "software Pauli", 5.2, "#667085"))
    prog = (("h q", False), ("t q", True), ("cx q r", False))
    d["J5"] = ("".join(f'<rect x="4" y="{14 + i * 13}" width="32" height="10" rx="2" fill="{"#fde8e1" if hot else "#fff"}" stroke="{"#c2410c" if hot else "#d0d5dd"}" stroke-width="1"/>'
                       + text(8, 21 + i * 13, s, 6, "#182230", "start", 700 if hot else 500, True) for i, (s, hot) in enumerate(prog))
               + '<path d="M37 32L46 8M37 32L46 72" stroke="#c2410c" stroke-width=".9" opacity=".7"/>'
               + "".join(f'<rect x="48" y="{8 + i * 9.4}" width="{30 - (i * 7) % 12}" height="5.2" rx="2" fill="#e4e7ec"/>' for i in range(7))
               + text(86, 44, "=", 11, GOOD, weight=800)
               + place("operation", 94, 30, 22, 20, "T") + good(105, 62, 5) + text(105, 76, "agree", 5.8, GOOD, weight=700))
    row1 = (("memory", 4), ("factory", 22), ("surgery", 40), ("readout", 58))
    row2 = (("injection", 4), ("memory", 22), ("operation", 40), ("memory", 58))
    d["J6"] = ("".join(place(c, x, 4, 15, 12, sw=1) for c, x in row1) + rail(4, 21, 73, 21)
               + "".join(place(c, x, 26, 15, 12, sw=1) for c, x in row2) + rail(4, 43, 73, 43)
               + "".join(rail(x + 7.5, 16, x + 7.5, 26) for _, x in row1)
               + f'<path d="M38 43V56" stroke="{BUS}" stroke-width="1.3" stroke-dasharray="3 2"/>'
               + place("decoder", 22, 56, 32, 16, "DEC")
               + '<path d="M80 6V74" stroke="#e4e7ec" stroke-width="1.2"/>'
               + text(99, 22, "order of", 6.2, "#667085") + text(99, 33, "7 mod 15", 7, "#182230", weight=700, mono=True)
               + text(99, 45, "= 4", 9, GOOD, weight=800) + text(99, 66, "15 = 3 × 5", 7, "#182230", weight=800))
    return d


DIAGRAMS = _diagrams()

#: a Lean chapter without a picture of its own: a proof, checked
PROOF = (text(46, 54, "\u22a2", 34, "#1e2761", weight=400) + text(74, 50, "proved", 9, "#1e2761", "start", 700)
         + good(88, 30, 9))


def diagram(lid: str, part: str, lean: bool = False) -> str:
    """The lesson's picture: what its exercise is about, drawn the way the studio draws it.
    A lesson with no picture of its own gets its id; a Lean chapter, a checked proof."""
    body = DIAGRAMS.get(lid)
    if body is None:
        body = PROOF if lean else text(60, 46, lid, 18, "#98a2b3", weight=800)
    marks = "".join(f'<marker id="{lid}-{k}" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="4.2" markerHeight="4.2" orient="auto">'
                    f'<path d="M0 0L10 5L0 10z" fill="{c}"/></marker>'
                    for k, c in (("o", ARROW), ("r", BAD), ("g", GOOD), ("p", CHK), ("m", MUTED)))
    return (f'<svg class="dg" viewBox="0 0 120 80" role="img" aria-label="{lid}">'
            f'<defs>{marks}</defs><rect x=".5" y=".5" width="119" height="79" rx="9" fill="#f7f8fb" stroke="#e3e7ee"/>'
            + body.replace("url(#@-", f"url(#{lid}-") + "</svg>")


def badge_svg(kind: str, colour: str, glyph: str) -> str:
    """A medal on a ribbon; `glyph` is a letter, or 'trophy' / 'star'."""
    if glyph == "trophy":
        mark = ('<path d="M17 13h14v6a7 7 0 0 1-14 0z" fill="#fff"/><path d="M17 15h-3.5a3.5 3.5 0 0 0 3.5 5M31 15h3.5a3.5 3.5 0 0 1-3.5 5" '
                'stroke="#fff" stroke-width="2" fill="none"/><path d="M24 26v3M19.5 31h9" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>')
    elif glyph == "star":
        mark = '<path d="M24 11.5l3.6 7.4 8.1 1.2-5.9 5.7 1.4 8.1-7.2-3.8-7.2 3.8 1.4-8.1-5.9-5.7 8.1-1.2z" fill="#fff"/>'
    else:
        mark = (f'<text x="24" y="28.6" text-anchor="middle" font-size="15" font-weight="800" fill="#fff" '
                f'font-family="ui-sans-serif,system-ui,sans-serif">{html.escape(glyph)}</text>')
    return (f'<svg viewBox="0 0 48 56" aria-hidden="true">'
            f'<path d="M14 34l-5 20 8-4 5 6 4-18zM34 34l5 20-8-4-5 6-4-18z" fill="{colour}" opacity=".75"/>'
            f'<circle cx="24" cy="23" r="19" fill="{colour}"/>'
            f'<circle cx="24" cy="23" r="15" fill="none" stroke="#fff" stroke-width="1.6" opacity=".55"/>'
            f'{mark}</svg>')


# ------------------------------------------------------------------------- the data

def plain(s: str) -> str:
    """A lesson's line as text: the course's marks (**b**, `c`, [[term|key]]) taken off."""
    s = s.replace("\\'", "'").replace('\\"', '"')
    s = re.sub(r"\[\[([^|\]]+)\|[^\]]*\]\]", r"\1", s)
    return s.replace("**", "").replace("`", "")


def lesson_meta(src: str) -> dict[str, dict]:
    """Per lesson, read out of `tutorial.js`: its first line (`story`) and the most stars it
    awards (one, plus one for a `boundary` exercise and one for a `challenge`; editor.js's
    `starsMax`).  Only the lesson's own keys count, at the lesson's indentation."""
    heads = list(re.finditer(r"\{ id: '([A-Z]\d+)', part: '([A-Z])', title: ", src))
    out = {}
    for k, m in enumerate(heads):
        body = src[m.start(): heads[k + 1].start() if k + 1 < len(heads) else len(src)]
        story = re.search(r"\n {6}story: '((?:[^'\\]|\\.)*)'", body)
        extra = len(re.findall(r"\n {6}(?:boundary|challenge): \{", body))
        out[m.group(1)] = {"story": plain(story.group(1)) if story else "", "max": 1 + extra}
    return out


def milestones(src: str) -> dict[str, str]:
    """What the course says when a part is finished (the part's `milestone`)."""
    return {a: plain(b) for a, b in
            re.findall(r"\{ id: '([A-Z])', title: '(?:[^'\\]|\\.)*',\s*milestone: '((?:[^'\\]|\\.)*)'", src)}


# ------------------------------------------------------------------------- the Lean parts

#: Parts E-I of the tutorial are the verified compiler, in Lean, run in the reader's browser
#: (qccd/site/book.py writes their pages).  A lesson there is a chapter, and its exercises are
#: what a reader solves: each counts as one exercise, and earns its own difficulty in stars.
LEAN_BADGES = {"E": "Modeller", "F": "Proof checker", "G": "Semanticist", "H": "Auditor", "I": "Compiler builder",
               "J": "Fault-tolerant architect"}
LEAN_COLOR = {"E": "#0e7c7b", "F": "#6a4fd0", "G": "#c2308a", "H": "#1f7fa8", "I": "#b5471b", "J": "#3f6b2a"}


def _ic(colour: str, body: str) -> str:
    return f'<svg class="ic" viewBox="0 0 64 64" aria-hidden="true">{body.replace("@", colour)}</svg>'


LEAN_ART = {
    # the machine as data: a device graph, as a term
    "E": _ic(LEAN_COLOR["E"], '<path d="M14 44L32 16L50 44Z" fill="none" stroke="@" stroke-width="3" stroke-linejoin="round"/>'
             '<circle cx="14" cy="44" r="6" fill="@"/><circle cx="32" cy="16" r="6" fill="@"/><circle cx="50" cy="44" r="6" fill="@"/>'
             '<path d="M8 56h48" stroke="#1c2a4a" stroke-width="3" stroke-linecap="round"/>'),
    # certificates and the proved checker: a certificate, ticked
    "F": _ic(LEAN_COLOR["F"], '<rect x="10" y="8" width="34" height="44" rx="4" fill="none" stroke="@" stroke-width="3"/>'
             '<path d="M17 20h20M17 28h20M17 36h12" stroke="@" stroke-width="3" stroke-linecap="round"/>'
             '<circle cx="46" cy="46" r="11" fill="@"/><path d="M40.5 46l4 4 7-8" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'),
    # semantics: the gate as a matrix
    "G": _ic(LEAN_COLOR["G"], '<path d="M16 12h-6v40h6M48 12h6v40h-6" fill="none" stroke="@" stroke-width="3" stroke-linecap="round"/>'
             '<circle cx="24" cy="24" r="4" fill="@"/><circle cx="40" cy="24" r="4" fill="#1c2a4a"/>'
             '<circle cx="24" cy="40" r="4" fill="#1c2a4a"/><circle cx="40" cy="40" r="4" fill="@"/>'),
    # verify the website yourself: a page under a magnifier
    "H": _ic(LEAN_COLOR["H"], '<rect x="8" y="8" width="32" height="42" rx="4" fill="none" stroke="#1c2a4a" stroke-width="3"/>'
             '<path d="M15 19h18M15 27h14" stroke="#1c2a4a" stroke-width="3" stroke-linecap="round"/>'
             '<circle cx="38" cy="38" r="11" fill="#fff" stroke="@" stroke-width="3.5"/><path d="M46 46l9 9" stroke="@" stroke-width="5" stroke-linecap="round"/>'),
    # build your own compiler: a gear
    "I": _ic(LEAN_COLOR["I"], "".join(f'<rect x="29" y="6" width="6" height="10" rx="1.5" transform="rotate({k * 45} 32 32)" fill="@"/>'
                                      for k in range(8))
             + '<circle cx="32" cy="32" r="16" fill="@"/><circle cx="32" cy="32" r="6.5" fill="#fff"/>'),
    # gadgets and fault-tolerant architecture: places with doors, on a bus
    "J": _ic(LEAN_COLOR["J"], '<path d="M6 34H58" stroke="#f2b8b5" stroke-width="5" stroke-linecap="round"/>'
             '<rect x="8" y="10" width="20" height="18" rx="3" fill="#fff" stroke="@" stroke-width="3"/>'
             '<rect x="36" y="10" width="20" height="18" rx="3" fill="@"/>'
             '<rect x="22" y="40" width="20" height="16" rx="3" fill="#fff" stroke="@" stroke-width="3"/>'
             '<path d="M18 28v6M46 28v6M32 34v6" stroke="#1c2a4a" stroke-width="3" stroke-linecap="round"/>'),
}


def lean_parts() -> list[dict]:
    """The tutorial's Lean parts, as qccd/site/book.py lays them out (`tutorial_parts`):
    [{part, title, about, lessons: [{id, title, story, href, exercises, stars, live}], coming}].
    A tree without them builds a tutorial of the studio's parts alone; an error inside them
    is not hidden."""
    try:
        from .book import dash, tutorial_parts
    except ImportError:
        return []
    # the sources write a spaced ` -- `; a reader sees the dash the rest of the site prints
    return [dict(p, title=dash(p["title"]), about=dash(p["about"]), coming=[dash(t) for t in p.get("coming", ())],
                 lessons=[dict(L, title=dash(L["title"]), story=reader_line(dash(L.get("story", "")))) for L in p["lessons"]])
            for p in tutorial_parts()]


def reader_line(s: str) -> str:
    """A lesson's line as a reader sees it: its marks taken off, and never a path into the
    repository (the site's rule; `qccd.site.prose.PATH` decides what a path is).  An aside in
    brackets that names one is dropped -- "(check_cert.py calls them NATIVE: ...)" -- and a
    line that would still name one is not shown at all."""
    from .prose import PATH
    s = plain(s)
    s = re.sub(r"\s*\([^()]*\)", lambda m: "" if PATH.search(m.group(0)) else m.group(0), s)
    if PATH.search(s):
        return ""
    s = s.strip().rstrip(" :;,")
    return s + "." if s and s[-1] not in ".!?" else s


# ------------------------------------------------------------------------- the page

def course_section(parts: list[dict], less: list[dict], part_art: dict, part_color: dict,
                   lean: list[dict] | None = None) -> str:
    """The Learn page's tutorial: the progress panel, then every part with its lesson cards --
    the studio's parts (A-D: a lesson is an exercise in the design tool) and the Lean parts
    (E-I: a lesson is a chapter, its exercises solved in the browser) in one list, one ladder,
    one count."""
    src = TUTORIAL_JS.read_text(encoding="utf-8") if TUTORIAL_JS.exists() else ""
    meta, ms = lesson_meta(src), milestones(src)
    lessons = [dict(L, **meta.get(L["id"], {"story": "", "max": 1})) for L in less]
    lean = lean_parts() if lean is None else lean
    chapters = [dict(L, part=p["part"]) for p in lean for L in p["lessons"] if L.get("live", True)]
    n_ex = sum(len(L["exercises"]) for L in chapters)
    total = len(lessons) + n_ex
    stars = sum(L["max"] for L in lessons) + sum(sum(L["stars"]) for L in chapters)
    # what a part holds: a studio part its lessons, a Lean part its chapters' exercises
    units = [(p["id"], sum(1 for L in lessons if L["part"] == p["id"]), BADGES.get(p["id"], f"Part {p['id']}"),
              part_color.get(p["id"], "#2a78d6"), f"Finish Part {p['id']}") for p in parts]
    units += [(p["part"], sum(len(L["exercises"]) for L in chapters if L["part"] == p["part"]),
               LEAN_BADGES.get(p["part"], f"Part {p['part']}"), LEAN_COLOR.get(p["part"], "#6a4fd0"),
               f"Solve Part {p['part']}'s exercises") for p in lean]
    # the ladder: Apprentice at the first exercise, then each part's badge at the number of
    # exercises up to and including that part
    levels, upto = [[0, "Newcomer"], [1, "Apprentice"]], 0
    for pid, n, name, _, _ in units:
        upto += n
        if n and upto > 1:
            levels.append([upto, name])
    badges = [{"id": pid, "name": name, "colour": colour, "need": need} for pid, n, name, colour, need in units if n]
    badges += [{"id": "all", "name": "Tutorial complete", "colour": "#0b7a4b", "need": f"Finish all {total} exercises"},
               {"id": "stars", "name": "Every star", "colour": "#d59a00", "need": f"Earn all {stars} stars"}]
    first = lessons[0] if lessons else None

    out = ['<section class="you" id="you" data-state="local">',
           '<div class="you-ring"><svg viewBox="0 0 120 120" aria-hidden="true">'
           '<circle cx="60" cy="60" r="52" fill="none" stroke="#e9e7f1" stroke-width="12"/>'
           '<circle id="you-arc" cx="60" cy="60" r="52" fill="none" stroke="url(#you-grad)" stroke-width="12" stroke-linecap="round" '
           'stroke-dasharray="326.73" stroke-dashoffset="326.73" transform="rotate(-90 60 60)"/>'
           '<defs><linearGradient id="you-grad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2a78d6"/>'
           '<stop offset="1" stop-color="#1baf7a"/></linearGradient></defs></svg>'
           '<div class="you-pct"><b id="you-pct">0%</b><span>of the tutorial</span></div></div>',
           '<div class="you-main">',
           '<div class="you-who"><span class="you-face" id="you-face" hidden></span><span id="you-who">Your progress</span></div>',
           '<div class="you-level">Level <b id="you-lvn">1</b> &middot; <span id="you-lv">Newcomer</span></div>',
           f'<div class="you-nums"><span><b id="you-done">0</b> of {total} exercises</span>'
           f'<span><b id="you-stars">0</b> of {stars} stars</span>'
           f'<span><b id="you-badges">0</b> of {len(badges)} badges</span></div>',
           '<div class="you-goal"><div class="you-gbar"><span id="you-gfill"></span></div><span id="you-goal">'
           'Finish your first exercise to become an Apprentice.</span></div>',
           '<div class="you-acts">'
           + (f'<a class="you-go" id="you-go" href="../studio.html#learn={first["id"]}">Start with {first["id"]}: '
              f'{html.escape(first["title"])} &rarr;</a>' if first else "")
           + '<button type="button" class="you-sign" id="you-signin" data-hint="learn:signin" hidden>Sign in to save your progress</button></div>',
           '<div class="you-acct" id="you-acct">Your progress is kept in this browser.</div>',
           '<div class="you-new" id="you-new" hidden></div>',
           '</div>',
           f'<div class="you-bdg" id="you-bdg" style="--nb:{len(badges)}">']
    for b in badges:
        glyph = "trophy" if b["id"] == "all" else "star" if b["id"] == "stars" else b["id"]
        out.append(f'<div class="bdg" data-badge="{b["id"]}" title="{html.escape(b["need"])}">'
                   f'{badge_svg(b["id"], b["colour"], glyph)}<b>{html.escape(b["name"])}</b>'
                   f'<small data-need="{html.escape(b["need"])}">{html.escape(b["need"])}</small></div>')
    out.append('</div></section>')

    for p in parts:
        mine = [L for L in lessons if L["part"] == p["id"]]
        blurb, art = part_art.get(p["id"], ("", ""))
        colour = part_color.get(p["id"], "#2a78d6")
        out.append(f'<section class="part" id="part-{p["id"]}" style="--pc:{colour}">'
                   f'<div class="ph">{art}<div class="phx"><div class="kick">Part {p["id"]} &middot; {len(mine)} lessons &middot; in the design tool</div>'
                   f'<h3>{html.escape(p["title"])}</h3><p class="sum">{blurb}</p>'
                   f'<div class="pmeta"><span class="pbar"><span class="pfill" data-part="{p["id"]}"></span></span>'
                   f'<span class="prog" data-part="{p["id"]}" data-unit="done">0 of {len(mine)} done</span>'
                   f'<span class="pbadge" data-part="{p["id"]}">Badge: {html.escape(BADGES.get(p["id"], "Part " + p["id"]))}</span></div></div></div>'
                   + (f'<p class="pdone" data-part="{p["id"]}" hidden>{html.escape(ms[p["id"]])}</p>' if p["id"] in ms else "")
                   + '<div class="cards2">')
        for L in mine:
            out.append(
                f'<a class="lesson" id="lesson-{L["id"]}" data-lesson="{L["id"]}" data-max="{L["max"]}" '
                f'href="../studio.html#learn={L["id"]}">'
                f'<span class="av">{diagram(L["id"], L["part"])}</span>'
                f'<span class="lx"><span class="lh"><span class="lid">{L["id"]}</span>'
                f'<span class="lt">{html.escape(L["title"])}</span></span>'
                f'<span class="ls">{html.escape(L["story"])}</span></span>'
                f'<span class="lr"><span class="lst">Not started</span>'
                f'<span class="lstars" title="{L["max"]} star{"s" if L["max"] > 1 else ""} to earn">{"&#9734;" * L["max"]}</span>'
                f'<span class="lgo">Start</span></span></a>')
        out.append("</div></section>")

    for p in lean:
        mine = [L for L in chapters if L["part"] == p["part"]]
        n = sum(len(L["exercises"]) for L in mine)
        pid, colour = p["part"], LEAN_COLOR.get(p["part"], "#6a4fd0")
        what = (f'{len(mine)} chapter{"s" if len(mine) != 1 else ""} live' if mine else "coming soon")
        out.append(f'<section class="part lean" id="part-{pid}" style="--pc:{colour}">'
                   f'<div class="ph">{LEAN_ART.get(pid, "")}<div class="phx"><div class="kick">Part {pid} &middot; {what} &middot; in Lean, in this browser</div>'
                   f'<h3>{html.escape(p["title"])}</h3><p class="sum">{html.escape(p["about"])}</p>'
                   + (f'<div class="pmeta"><span class="pbar"><span class="pfill" data-part="{pid}"></span></span>'
                      f'<span class="prog" data-part="{pid}" data-unit="exercises">0 of {n} exercises</span>'
                      f'<span class="pbadge" data-part="{pid}">Badge: {html.escape(LEAN_BADGES.get(pid, "Part " + pid))}</span></div>' if n else "")
                   + '</div></div>')
        if mine:
            out.append('<div class="cards2">')
            for L in mine:
                k = len(L["exercises"])
                out.append(
                    f'<a class="lesson chap" id="lesson-{L["id"]}" data-chapter="{L["id"]}" href="{html.escape(L["href"])}">'
                    f'<span class="av">{diagram(L["id"], pid, lean=True)}</span>'
                    f'<span class="lx"><span class="lh"><span class="lid">{L["id"]}</span>'
                    f'<span class="lt">{html.escape(L["title"])}</span></span>'
                    f'<span class="ls">{html.escape(L.get("story", ""))}</span></span>'
                    f'<span class="lr"><span class="lst">Not started</span>'
                    f'<span class="lex">0 of {k} exercise{"s" if k != 1 else ""}</span>'
                    f'<span class="lgo">Start</span></span></a>')
            out.append("</div>")
        if p.get("coming"):
            out.append(f'<p class="coming"><b>Coming next in this part:</b> '
                       + " &middot; ".join(html.escape(t) for t in p["coming"]) + "</p>")
        out.append("</section>")

    course = {"lessons": [{"id": L["id"], "part": L["part"], "title": L["title"], "max": L["max"]} for L in lessons],
              "chapters": [{"id": L["id"], "part": L["part"], "title": L["title"], "href": L["href"],
                            "ex": L["exercises"], "st": L["stars"]} for L in chapters],
              "parts": [{"id": pid, "n": n} for pid, n, _, _, _ in units],
              "levels": levels, "badges": [b["id"] for b in badges], "total": total, "stars": stars}
    hints = {"learn:signin": {"t": "Sign in to save your progress",
                              "d": "Opens the site's sign-in dialog. Signed in, the lessons and exercises you finish "
                                   "are kept on your account and shown here on any device."}}
    out.append("<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, "
               + json.dumps(hints) + ");</script>")
    out.append(sync_script())
    if chapters:
        # the Lean parts' exercises keep their own store (qccd/site/book/book-progress.js)
        out.append('<script src="../book/book-progress.js"></script>')
    out.append("<script>" + LEARN_JS.replace("__COURSE__", json.dumps(course, separators=(",", ":"))) + "</script>")
    return "\n".join(out)


def sync_script() -> str:
    """The account sync, for the Learn page and every page the course runs on."""
    return "<script>" + SYNC_JS + "</script>"


LEARN_CSS = r"""
/* learn: the progress panel */
.you{display:grid;grid-template-columns:136px 1fr;gap:6px 26px;align-items:center;margin:14px 0 30px;padding:24px 26px 20px;
 border-radius:18px;font-family:var(--sans);background:linear-gradient(135deg,#f3f7ff 0%,#f5fbf6 100%);border:1px solid #dfe7f3;
 box-shadow:0 1px 2px rgba(18,16,28,.04),0 10px 26px rgba(31,91,181,.07)}
.you-ring{position:relative;width:136px;height:136px}
.you-ring svg{width:136px;height:136px;display:block;background:transparent}
#you-arc{transition:stroke-dashoffset 1.1s cubic-bezier(.2,.8,.2,1)}
.you-pct{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1.1}
.you-pct b{font-size:30px;color:var(--head);font-variant-numeric:tabular-nums}
.you-pct span{font-size:11px;color:var(--ink3);text-transform:uppercase;letter-spacing:.07em;margin-top:3px}
.you-who{font-size:20px;font-weight:700;color:var(--head);display:flex;align-items:center;gap:10px;line-height:1.25}
.you-face{width:30px;height:30px;border-radius:50%;color:#fff;font-size:12.5px;font-weight:700;display:inline-flex;
 align-items:center;justify-content:center;flex:0 0 auto}
.you-face[hidden]{display:none}
.you-level{font-size:13.5px;color:var(--ink2);margin:3px 0 8px} .you-level b{color:var(--head)}
.you-level #you-lv{display:inline-block;padding:1px 10px;border-radius:999px;background:#1c2a4a;color:#fff;font-weight:600;font-size:12.5px}
.you-nums{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13.5px;color:var(--ink2)}
.you-nums b{font-size:17px;color:var(--head);font-variant-numeric:tabular-nums}
.you-goal{margin:10px 0 4px;font-size:13px;color:var(--ink2)}
.you-gbar{height:8px;border-radius:5px;background:#e3e6ef;overflow:hidden;margin:0 0 5px;max-width:420px}
.you-gbar span{display:block;height:100%;width:0;border-radius:5px;background:linear-gradient(90deg,#f4b400,#eb6834);transition:width .9s ease}
.you-acts{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 6px;align-items:center}
.you-go{display:inline-block;background:var(--accent);color:#fff;font-weight:600;font-size:14px;padding:9px 16px;border-radius:10px;
 box-shadow:0 2px 6px rgba(31,91,181,.25)}
.you-go:hover{background:#174a96;text-decoration:none}
.you-sign{font:600 13.5px var(--sans);background:#fff;color:var(--accent);border:1px solid #b9cbe8;border-radius:10px;padding:8px 14px;cursor:pointer}
.you-sign:hover{border-color:var(--accent)}
.you-sign[hidden]{display:none}
.you-acct{font-size:12.5px;color:var(--ink3)} .you-acct.ok{color:#0b7a4b}
.you-new{margin-top:8px;font-size:13px;background:#fff7dc;border:1px solid #f1dc95;color:#6d5300;border-radius:9px;padding:7px 11px}
.you-new[hidden]{display:none}
.you-bdg{grid-column:1/-1;display:grid;grid-template-columns:repeat(var(--nb,7),minmax(0,1fr));gap:8px;margin-top:14px;padding-top:14px;border-top:1px dashed #d5dcea}
.bdg{text-align:center;font-size:11.5px;line-height:1.25;color:var(--ink2)}
.bdg svg{width:42px;height:49px;display:block;margin:0 auto 3px;background:transparent;transition:transform .3s}
.bdg b{display:block;color:var(--head);font-size:11.5px} .bdg small{display:block;color:var(--ink3);font-size:10.5px}
.bdg:not(.on) svg{filter:grayscale(1);opacity:.32} .bdg:not(.on) b{color:var(--ink3)}
.bdg.on svg{filter:drop-shadow(0 2px 4px rgba(0,0,0,.18))} .bdg.on:hover svg{transform:scale(1.08) rotate(-4deg)}
.bdg.fresh svg{animation:bdgpop 1s ease 2}
@keyframes bdgpop{0%,100%{transform:scale(1)}40%{transform:scale(1.25) rotate(-8deg)}}
/* learn: a part, then one card per lesson, each on its own line */
.part{margin:30px 0 0}
.ph{display:grid;grid-template-columns:64px 1fr;gap:18px;align-items:start;font-family:var(--sans)}
.ph .ic{width:56px;height:56px;display:block;margin-top:4px}
.ph h3{margin:0 0 2px;font-size:19px}
.ph .kick{font-size:11px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;color:var(--pc)}
.ph .sum{color:var(--ink2);margin:0 0 8px;font-size:13.5px;line-height:1.5}
.pmeta{display:flex;align-items:center;gap:12px;font-size:12px;color:var(--ink3);flex-wrap:wrap}
.pbar{width:160px;height:7px;border-radius:4px;background:#ebe9e3;overflow:hidden;display:inline-block}
.pfill{display:block;height:100%;width:0;background:var(--pc);border-radius:4px;transition:width .8s ease}
.pbadge{padding:1px 8px;border-radius:999px;border:1px solid var(--line);color:var(--ink3)}
.pbadge.on{border-color:var(--pc);color:#fff;background:var(--pc);font-weight:600}
.pdone{margin:10px 0 0 82px;font:13px/1.5 var(--sans);color:#0b5e3b;background:#eaf7f0;border:1px solid #bfe5cf;border-radius:9px;padding:8px 12px}
.pdone[hidden]{display:none}
.cards2{display:flex;flex-direction:column;gap:10px;margin:14px 0 0 82px}
.lesson{display:grid;grid-template-columns:144px 1fr auto;gap:16px;align-items:center;padding:12px 16px 12px 12px;
 background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:14px;color:inherit;
 font-family:var(--sans);transition:border-color .2s,box-shadow .2s,transform .2s}
.lesson:hover{text-decoration:none;border-color:var(--pc);box-shadow:0 4px 14px rgba(18,16,28,.08);transform:translateY(-1px)}
.lesson .av{width:144px;height:96px;display:block;position:relative}
.lesson .dg{width:144px;height:96px;display:block;background:transparent}
.lesson.done .av::after{content:"\2713";position:absolute;right:-6px;top:-6px;width:20px;height:20px;border-radius:50%;
 background:#12a150;color:#fff;font:700 12px/20px var(--sans);text-align:center;box-shadow:0 0 0 2px #fff}
.lesson .lx{min-width:0}
.lesson .lh{display:flex;gap:8px;align-items:baseline}
.lesson .lid{font-size:12px;font-weight:700;color:var(--pc);font-variant-numeric:tabular-nums;flex:0 0 auto}
.lesson .lt{font-size:15.5px;font-weight:600;color:var(--head);line-height:1.3}
.lesson .ls{display:block;font-size:13px;color:var(--ink2);line-height:1.45;margin-top:3px}
.lesson .lr{display:flex;flex-direction:column;align-items:flex-end;gap:4px;min-width:104px}
.lesson .lst{font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--ink3);white-space:nowrap}
.lesson .lstars{font-size:16px;letter-spacing:1px;color:#cfccc4;white-space:nowrap;line-height:1}
.lesson .lstars i{font-style:normal;color:#f0a500}
.lesson .lex{font-size:12px;color:var(--ink2);white-space:nowrap}
.lesson.done .lex{color:#0b7a4b;font-weight:600}
.coming{margin:12px 0 0 82px;font:13px/1.5 var(--sans);color:var(--ink3)} .coming b{color:var(--ink2);font-weight:600}
.lesson .lgo{font-size:12.5px;font-weight:600;color:var(--accent);border:1px solid #c6d4ec;border-radius:8px;padding:3px 11px;background:#fff;white-space:nowrap}
.lesson.done{border-left-color:#1baf7a;background:linear-gradient(90deg,#f1faf5,var(--card) 40%)}
.lesson.done .lst{color:#0b7a4b}
.lesson.done .lgo{color:#0b7a4b;border-color:#bfe5cf}
.lesson.next{border-color:#9fbbe6;border-left-color:var(--accent);background:#f5f8ff;box-shadow:0 0 0 3px rgba(31,91,181,.09)}
.lesson.next .lst{color:var(--accent)}
.lesson.next .lgo{background:var(--accent);color:#fff;border-color:var(--accent)}
.lesson.next .dg rect:first-of-type{stroke:#9fbbe6}
.lesson.new{position:relative}
.lesson.new::after{content:"New";position:absolute;top:-8px;right:14px;background:#f4b400;color:#3b2b00;font-size:10.5px;font-weight:800;
 letter-spacing:.06em;text-transform:uppercase;padding:1px 8px;border-radius:999px;box-shadow:0 1px 3px rgba(0,0,0,.15)}
@media (prefers-reduced-motion:reduce){.bdg.fresh svg{animation:none}#you-arc,.pfill,.you-gbar span{transition:none}}
@media (max-width:760px){.you{grid-template-columns:1fr;justify-items:start}.you-bdg{grid-template-columns:repeat(4,1fr)}
 .cards2,.pdone,.coming{margin-left:0}.lesson{grid-template-columns:90px 1fr;gap:12px}.lesson .av,.lesson .dg{width:90px;height:60px}
 .lesson .lr{grid-column:2;flex-direction:row;align-items:center;justify-content:flex-start;gap:10px;min-width:0}}
"""


# The account sync.  window.QCCDPROGRESS = {state(), sync(), subscribe(fn)}.
SYNC_JS = r"""
(function(){
  if(typeof window === 'undefined' || typeof location === 'undefined' || typeof document === 'undefined') return;
  if(window.QCCDPROGRESS) return;
  var KEY = 'qccd.studio.tutorial', OWNER = 'qccd.course.account';
  var live = /^https?:$/.test(location.protocol) && !!window.fetch;
  try { if(window.top !== window.self) live = false; } catch(e){ live = false; }
  if(/(^|&)embed(&|$)/.test((location.hash || '').replace(/^#/, ''))) live = false;
  var S = { status: live ? 'loading' : 'local', user: null, stars: {}, done: {}, updated: null, synced: null };
  var subs = [], busy = false, again = false;
  function has(o, k){ return Object.prototype.hasOwnProperty.call(o, k); }
  function course(){ var p = null; try { p = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch(e){ p = null; }
                     return (p && typeof p === 'object') ? p : {}; }
  function clean(s){ var o = {}, k, n; s = (s && typeof s === 'object') ? s : {};
    for(k in s) if(has(s, k) && /^[A-Z][0-9]{1,2}$/.test(k)){ n = Math.floor(+s[k]); if(n > 0) o[k] = Math.min(n, 3); }
    return o; }
  function localStars(){ return clean(course().stars); }
  function save(p){ try { localStorage.setItem(KEY, JSON.stringify(p)); } catch(e){} }
  function owner(){ try { return localStorage.getItem(OWNER); } catch(e){ return null; } }
  function setOwner(id){ try { localStorage.setItem(OWNER, String(id)); } catch(e){} }
  function snapshot(){ var p = course();
    return { status: S.status, user: S.user, stars: S.user ? S.stars : localStars(), done: S.user ? S.done : {},
             updated: S.updated, synced: S.synced, current: p.current || null }; }
  function emit(){ var s = snapshot(); for(var i = 0; i < subs.length; i++){ try { subs[i](s); } catch(e){} } }
  function api(method, body){
    var o = { method: method, credentials: 'same-origin', headers: {} };
    if(body){ o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch('/api/progress', o).then(function(r){ return r.json().then(function(d){
      if(!r.ok){ var e = new Error((d && d.error) || ('HTTP ' + r.status)); e.status = r.status; throw e; } return d; }); });
  }
  function take(p){ p = p || {}; S.stars = clean(p.stars); S.done = p.done || {}; S.updated = p.updated || null; S.synced = Date.now(); }
  // the studio's own Learn tab reads the stars again when this browser's copy changed
  function refreshStudio(){ try { if(window.EDITOR && EDITOR.lessonsReady && window.QCCD_TUTORIAL) EDITOR.lessonsReady(window.QCCD_TUTORIAL); } catch(e){} }
  // what this browser holds beyond the account goes up; the account only ever raises a count
  function push(){
    var loc = localStars(), up = {}, any = false, k;
    for(k in loc) if(has(loc, k) && loc[k] > (S.stars[k] || 0)){ up[k] = loc[k]; any = true; }
    if(!any) return Promise.resolve(false);
    return api('POST', { stars: up }).then(function(d){ take(d.progress); return true; });
  }
  // and the account's stars come down into this browser, lesson by lesson, the higher count kept
  function adopt(replace){
    var p = course(), st = replace ? {} : ((p.stars && typeof p.stars === 'object') ? p.stars : {}), changed = !!replace, k;
    for(k in S.stars) if(has(S.stars, k) && (st[k] || 0) < S.stars[k]){ st[k] = S.stars[k]; changed = true; }
    // another account's open lesson and best prices go with its stars
    if(replace){ delete p.current; p.best = {}; }
    if(changed){ p.stars = st; if(!p.best) p.best = {}; save(p); refreshStudio(); }
  }
  function sync(){
    if(!live){ S.status = 'local'; emit(); return Promise.resolve(); }
    if(busy){ again = true; return Promise.resolve(); }
    busy = true;
    return api('GET').then(function(d){
      if(!d || !d.user){ S.user = null; S.stars = {}; S.done = {}; S.updated = null; S.status = 'anon'; return; }
      S.user = d.user; take(d.progress);
      var o = owner();
      if(o !== null && o !== String(d.user.id)){
        // this browser's stars were another account's: this reader gets their own
        setOwner(d.user.id); adopt(true); S.status = 'synced'; return;
      }
      setOwner(d.user.id);
      return push().then(function(){ adopt(false); S.status = 'synced'; });
    }).catch(function(e){ S.status = (e && e.status) ? 'error' : 'offline'; })
      .then(function(){ busy = false; emit(); if(again){ again = false; sync(); } });
  }
  function tick(){
    if(!live || busy) return;
    // a sign-in or sign-out through the site bar's dialog: the bar's layer knows who is
    // signed in once it shows its Sign in control or the reader's name; until then, nobody
    var me;
    try { var q = window.QCCOMMENTS && QCCOMMENTS.state();
          if(q && q.me) me = q.me.id; else if(q && document.getElementById('qc-signin')) me = null; } catch(e){}
    if(me !== undefined && (S.status === 'anon' || S.status === 'synced') && me !== (S.user ? S.user.id : null)){ sync(); return; }
    // a star the course has just awarded in this browser
    if(S.user && S.status === 'synced'){
      var loc = localStars(), k;
      for(k in loc) if(has(loc, k) && loc[k] > (S.stars[k] || 0)){
        busy = true;
        push().catch(function(){}).then(function(){ busy = false; emit(); });
        return;
      }
    }
  }
  window.QCCDPROGRESS = { state: snapshot, sync: sync, subscribe: function(f){ subs.push(f); } };
  function start(){
    sync();
    if(!live) return;
    setInterval(tick, 2000);
    window.addEventListener('storage', function(ev){ if(!ev || ev.key === KEY) emit(); });
    document.addEventListener('visibilitychange', function(){ if(document.visibilityState === 'visible') sync(); });
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
"""


# The Learn page: the cards' states, the panel and the badges.  The studio's lessons come from
# QCCDPROGRESS (the course's stars); the Lean parts' exercises from QCCDBOOK (their own store,
# book-progress.js).  Both count in the one panel.
LEARN_JS = r"""
(function(){
  var C = __COURSE__, SEEN = 'qccd.course.seen';
  function $(id){ return document.getElementById(id); }
  function stateNow(){
    if(window.QCCDPROGRESS) return QCCDPROGRESS.state();
    var p = null; try { p = JSON.parse(localStorage.getItem('qccd.studio.tutorial') || 'null'); } catch(e){}
    return { status: 'local', user: null, stars: (p && p.stars) || {}, done: {}, current: (p && p.current) || null };
  }
  function solved(){
    if(window.QCCDBOOK){ try { return QCCDBOOK.state().done || {}; } catch(e){} }
    var p = null; try { p = JSON.parse(localStorage.getItem('qccd.book.progress') || 'null'); } catch(e){}
    return (p && p.done) || {};
  }
  function day(t){ try { return new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); } catch(e){ return ''; } }
  function starsHTML(n, max){ var s = ''; for(var i = 1; i <= max; i++) s += i <= n ? '<i>★</i>' : '☆'; return s; }
  function initials(name){ var w = String(name || '?').trim().split(/\s+/); return ((w[0] || '?').charAt(0) + (w.length > 1 ? w[w.length - 1].charAt(0) : '')).toUpperCase(); }
  var COLOURS = ['#2a78d6','#d64545','#1baf7a','#eb6834','#4a3aa7','#c2308a','#0e9aa7','#8a6d1e','#5b8c1e','#b5471b','#6b4fbb','#2f7f6e'];
  var celebrated = false, FRESH = {};
  function render(st){
    st = st || stateNow();
    var stars = st.stars || {}, bd = solved(), done = 0, got = 0, next = null, bonus = null, parts = {}, doneIds = [], names = {}, i, j, L;
    for(i = 0; i < C.lessons.length; i++){
      L = C.lessons[i]; var n = Math.min(stars[L.id] || 0, L.max), p = parts[L.part] || (parts[L.part] = { done: 0, n: 0 });
      p.n++; got += n;
      if(n){ done++; p.done++; doneIds.push(L.id); if(n < L.max && !bonus) bonus = L; }
      else if(!next) next = L;
    }
    // the Lean parts: a chapter's exercises, each one exercise and its own stars
    var chapNext = null, byId = {};
    for(i = 0; i < C.chapters.length; i++){
      var H = C.chapters[i], q = parts[H.part] || (parts[H.part] = { done: 0, n: 0 }), k2 = 0;
      byId[H.id] = H;
      for(j = 0; j < H.ex.length; j++){
        names[H.ex[j]] = H.id + ' exercise ' + (j + 1); q.n++;
        if(bd[H.ex[j]]){ k2++; done++; q.done++; got += H.st[j]; doneIds.push(H.ex[j]); }
      }
      H.k = k2;
      if(k2 < H.ex.length && !chapNext) chapNext = H;
    }
    // where the reader is: the lesson they left open, if it is not done, else the first one not
    // done -- the design tool's parts first, then the Lean parts
    var cur = null;
    for(i = 0; i < C.lessons.length; i++) if(C.lessons[i].id === st.current && !(stars[st.current] > 0)) cur = C.lessons[i];
    var go = cur || next, goChap = go ? null : chapNext;
    // what is new since this page last saw this reader's progress (kept for the whole visit)
    var who = st.user ? 'u' + st.user.id : 'local', seen = null, fresh = FRESH;
    try { seen = JSON.parse(localStorage.getItem(SEEN) || 'null'); } catch(e){ seen = null; }
    if(seen && seen.who === who && seen.ids && st.status !== 'loading')
      for(i = 0; i < doneIds.length; i++) if(seen.ids.indexOf(doneIds[i]) < 0) fresh[doneIds[i]] = 1;
    // the cards: the design tool's lessons
    var cards = document.querySelectorAll('.lesson[data-lesson]');
    for(i = 0; i < cards.length; i++){
      var c = cards[i], id = c.getAttribute('data-lesson'), max = +c.getAttribute('data-max') || 1, k = Math.min(stars[id] || 0, max);
      var isNext = !k && go && go.id === id;
      c.classList.toggle('done', !!k); c.classList.toggle('next', !!isNext); c.classList.toggle('new', !!fresh[id]);
      var when = k && st.done && st.done[id] ? ' · ' + day(st.done[id]) : '';
      c.querySelector('.lst').textContent = k ? 'Done' + when : isNext ? (cur && cur.id === id ? 'In progress' : 'Next up') : 'Not started';
      c.querySelector('.lstars').innerHTML = starsHTML(k, max);
      c.querySelector('.lstars').title = k >= max ? 'Every star earned' : k ? (max - k) + ' bonus star' + (max - k > 1 ? 's' : '') + ' still to earn' : max + ' star' + (max > 1 ? 's' : '') + ' to earn';
      c.querySelector('.lgo').textContent = k ? (k < max ? 'Bonus ★' : 'Again') : isNext ? (cur && cur.id === id ? 'Continue' : 'Start') : 'Start';
    }
    // ... and the Lean chapters
    var chs = document.querySelectorAll('.lesson[data-chapter]');
    for(i = 0; i < chs.length; i++){
      var e = chs[i], H2 = byId[e.getAttribute('data-chapter')];
      if(!H2) continue;
      var all = H2.ex.length, kk = H2.k, nextC = !kk && goChap && goChap.id === H2.id, isNew = false;
      for(j = 0; j < all; j++) if(fresh[H2.ex[j]]) isNew = true;
      e.classList.toggle('done', all > 0 && kk >= all); e.classList.toggle('next', !!nextC); e.classList.toggle('new', isNew);
      e.querySelector('.lst').textContent = kk >= all ? 'Done' : kk ? 'In progress' : nextC ? 'Next up' : 'Not started';
      e.querySelector('.lex').textContent = kk + ' of ' + all + ' exercise' + (all > 1 ? 's' : '');
      e.querySelector('.lgo').textContent = kk >= all ? 'Again' : kk ? 'Continue' : 'Start';
    }
    // the parts
    var badges = 0, on = {};
    for(i = 0; i < C.parts.length; i++){
      var P = C.parts[i], pp = parts[P.id] || { done: 0, n: P.n }, full = pp.n > 0 && pp.done >= pp.n;
      var f = document.querySelector('.pfill[data-part="' + P.id + '"]'); if(f) f.style.width = (pp.n ? 100 * pp.done / pp.n : 0) + '%';
      var g = document.querySelector('.prog[data-part="' + P.id + '"]');
      if(g){ var unit = g.getAttribute('data-unit') === 'exercises' ? ' exercises' : ' done';
             g.textContent = full ? 'All ' + pp.n + unit : pp.done + ' of ' + pp.n + unit; }
      var b = document.querySelector('.pbadge[data-part="' + P.id + '"]'); if(b) b.classList.toggle('on', full);
      var m = document.querySelector('.pdone[data-part="' + P.id + '"]'); if(m) m.hidden = !full;
      if(full){ badges++; on[P.id] = 1; }
    }
    if(done >= C.total && C.total){ badges++; on.all = 1; }
    if(got >= C.stars && C.stars){ badges++; on.stars = 1; }
    var bs = document.querySelectorAll('.bdg[data-badge]');
    for(i = 0; i < bs.length; i++){
      var bid = bs[i].getAttribute('data-badge'), sm = bs[i].querySelector('small');
      bs[i].classList.toggle('on', !!on[bid]);
      if(sm) sm.textContent = on[bid] ? 'Earned' : sm.getAttribute('data-need');
    }
    // the ring, the numbers, the level
    var pct = C.total ? Math.round(100 * done / C.total) : 0;
    $('you-pct').textContent = pct + '%';
    $('you-arc').setAttribute('stroke-dashoffset', String(326.73 * (1 - (C.total ? done / C.total : 0))));
    $('you-done').textContent = done; $('you-stars').textContent = got; $('you-badges').textContent = badges;
    var lv = 0; for(i = 0; i < C.levels.length; i++) if(done >= C.levels[i][0]) lv = i;
    $('you-lvn').textContent = lv + 1; $('you-lv').textContent = C.levels[lv][1];
    var goal, gfill;
    if(lv + 1 < C.levels.length){
      var nx = C.levels[lv + 1], base = C.levels[lv][0], left = nx[0] - done;
      goal = !done ? 'Finish your first exercise to become an ' + nx[1] + '.'
           : left + ' more exercise' + (left > 1 ? 's' : '') + ' to reach level ' + (lv + 2) + ': ' + nx[1] + '.';
      gfill = 100 * (done - base) / Math.max(1, nx[0] - base);
    } else if(got < C.stars){
      goal = 'Tutorial complete! ' + (C.stars - got) + ' bonus star' + (C.stars - got > 1 ? 's' : '') + ' left: the boundary and challenge exercises.';
      gfill = 100 * got / C.stars;
    } else { goal = 'Every exercise and every star. Nothing left but the leaderboard.'; gfill = 100; }
    $('you-goal').textContent = goal; $('you-gfill').style.width = gfill + '%';
    var a = $('you-go');
    if(a){
      if(go){ a.href = '../studio.html#learn=' + go.id;
        a.textContent = (done || cur ? 'Continue with ' : 'Start with ') + go.id + ': ' + go.title + ' →'; }
      else if(goChap){ a.href = goChap.href;
        a.textContent = (goChap.k ? 'Continue with ' : 'On to ') + goChap.id + ': ' + goChap.title + ' →'; }
      else if(bonus){ a.href = '../studio.html#learn=' + bonus.id;
        a.textContent = 'Earn a bonus star in ' + bonus.id + ': ' + bonus.title + ' →'; }
      else { a.href = '../board/'; a.textContent = 'On to the leaderboard →'; }
    }
    // who is reading, and where the progress is kept
    var face = $('you-face'), acct = $('you-acct'), sign = $('you-signin');
    var canSign = !!document.getElementById('qc-signin');
    if(st.user){
      face.hidden = false; face.textContent = initials(st.user.name);
      face.style.background = COLOURS[(st.user.color || 0) % COLOURS.length];
      $('you-who').textContent = (done ? 'Welcome back, ' : 'Welcome, ') + st.user.name;
    } else { face.hidden = true; $('you-who').textContent = done ? 'Your progress' : 'Start the tutorial'; }
    acct.className = 'you-acct';
    if(st.user && st.status === 'synced'){
      acct.className = 'you-acct ok';
      acct.textContent = '✓ Saved to your account' + (st.updated ? ': last lesson finished ' + day(st.updated) + '.'
                         : done ? '.' : '. Finish an exercise and it is kept here, on every device.');
    } else if(st.user){ acct.textContent = 'Your account could not be reached just now; your progress is kept in this browser and saved next time.'; }
    else if(st.status === 'anon'){ acct.textContent = 'Kept in this browser only. Sign in and every exercise you finish is saved to your account, on any device.'; }
    else if(st.status === 'loading'){ acct.textContent = 'Checking your account…'; }
    else { acct.textContent = 'Your progress is kept in this browser.'; }
    sign.hidden = !(st.status === 'anon' && canSign);
    // celebrate what is new, once per visit, then remember it
    var newIds = Object.keys(fresh);
    if(newIds.length && !celebrated){
      celebrated = true;
      var nb = $('you-new'); nb.hidden = false;
      nb.textContent = '✨ New since your last visit: ' + newIds.map(function(x){ return names[x] || x; }).join(', ') + '. '
                     + (lv + 1 < C.levels.length ? 'Keep going!' : 'Well done!');
      for(i = 0; i < bs.length; i++) if(on[bs[i].getAttribute('data-badge')] && seen && seen.badges && seen.badges.indexOf(bs[i].getAttribute('data-badge')) < 0) bs[i].classList.add('fresh');
    }
    if(st.status !== 'loading'){
      try { localStorage.setItem(SEEN, JSON.stringify({ who: who, ids: doneIds, badges: Object.keys(on) })); } catch(e){}
    }
  }
  if(window.QCCDPROGRESS) QCCDPROGRESS.subscribe(render);
  if(window.QCCDBOOK) QCCDBOOK.subscribe(function(){ render(); });
  var sb = $('you-signin');
  if(sb) sb.addEventListener('click', function(){ var s = document.getElementById('qc-signin'); if(s) s.click(); });
  render();
  // the comment layer puts its Sign in control in the bar once it has asked the account
  setTimeout(function(){ render(); }, 1500);
})();
"""
