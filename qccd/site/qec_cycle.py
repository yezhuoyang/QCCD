"""The QEC clock cycle on the leaderboard: the round, and the classical loop around it.

Every board already ranks designs by one number -- the wall-clock of a syndrome-extraction
round, replayed against the 27 rules.  That is the *quantum* half of a QEC cycle.  This
module adds the other half, because a machine is only as fast as the loop it closes:

    ancillas measured  ->  outcomes cross a wire  ->  a decoder says what to correct
                                 |
                   either it stops in the classical memory as a Pauli-frame
                   update (no ion waits for it), or it comes back as the guard
                   of a classically controlled operation (an ion does wait)

So a board row grows two verdicts that the round time alone cannot give:

* **does the decoder keep up?**  One round's syndrome has to be decoded before the next
  arrives, or the undecoded backlog grows without bound.  The margin is the round time
  divided by the decoding time, and on a QCCD machine it is enormous -- which is worth
  seeing next to the superconducting yardstick, where the same decoder is the bottleneck.
* **what does acting on a result cost?**  The reaction time -- last measurement to a usable
  decision -- as a fraction of a cycle.

The numbers come from `qccd.analysis.feedback`, which is also what the gadget layer's
places and the studio's panel read, so the three cannot disagree.  Nothing here invents a
round time: it is given the board's own replayed one.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from ..analysis.feedback import DECODERS, LINK, MODES, ROUND_REFERENCE, cycle_report

__all__ = ["CSS", "board_block", "board_section", "loop_svg", "studio_payload",
           "studio_block", "SHOWN_DECODERS"]

HERE = Path(__file__).resolve().parent

#: the decoders the board compares, in the order it shows them
SHOWN_DECODERS = ("lut", "fpga_bp", "gpu_bp")

CSS = """
.qec { margin: 6px 0 0; font-size: 12.5px; color: var(--muted); }
.qec b { color: var(--ink); font-weight: 600; }
.qec .ok { color: #0b7a4b; }
.qec .bad { color: #b42318; }
section.cycle { margin: 26px 0 8px; }
section.cycle table { border-collapse: collapse; width: 100%; font-size: 13px; }
section.cycle th, section.cycle td { border-bottom: 1px solid var(--line); padding: 6px 8px;
  text-align: right; vertical-align: bottom; }
section.cycle thead small { font-weight: 400; opacity: .75; }
section.cycle th:first-child, section.cycle td:first-child { text-align: left; }
section.cycle thead th { color: var(--muted); font-weight: 600; font-size: 12px; }
section.cycle tr.ref td { color: var(--muted); font-style: italic; }
section.cycle .loop { margin: 14px 0 10px; max-width: 980px; }
section.cycle .why { font-size: 12.5px; color: var(--muted); margin: 4px 0 0; }
"""


def _fmt(us: float) -> str:
    if us == 0:
        return "0"
    if us < 1:
        return f"{us * 1000:.0f} ns"
    if us < 1000:
        return f"{us:.3g} &micro;s"
    if us < 1e6:
        return f"{us / 1000:.3g} ms"
    return f"{us / 1e6:.3g} s"


def _margin(x: float) -> str:
    if x >= 1000:
        return f"{x:,.0f}&times;"
    if x >= 10:
        return f"{x:.0f}&times;"
    return f"{x:.2g}&times;"


def board_block(round_us: float | None, *, decoder: str = "lut") -> str:
    """The one line a board row gains: the cycle, the margin and the cost of feedback."""
    if not round_us:
        return ""
    store = cycle_report(round_us, decoder=decoder, mode="store")
    react = cycle_report(round_us, decoder=decoder, mode="react")
    keeps = store["keeps_up"]
    return (
        f'<p class="qec">QEC clock: <b>{_fmt(store["round_us"])}</b> a round '
        f'&middot; <b>{store["cycles_per_s"]:,.0f}</b> cycles/s '
        f'&middot; decoder margin <b class="{"ok" if keeps else "bad"}">'
        f'{_margin(store["decoder_margin"])}</b> '
        f'with a {html.escape(DECODERS[decoder]["title"].lower())} '
        f'&middot; feedback <b>{100 * react["reaction_fraction"]:.2g}%</b> of a cycle '
        f'<a href="#cycle">what this means &rarr;</a></p>')


def loop_svg(round_us: float, *, decoder: str = "lut", width: float = 940.0) -> str:
    """The loop as a drawing: the quantum round, the classical path, and the two exits.

    The stages differ by four orders of magnitude, so the widths go by the square root of
    the time -- a linear axis would make the round the whole picture and a log one would
    make the round look comparable to a wire.  Every box carries its own number, and the
    legend underneath repeats them in order, so the drawing is never the only statement.
    """
    r = cycle_report(round_us, decoder=decoder, mode="react")
    stages = r["stages"]
    h, pad, top = 126.0, 10.0, 30.0
    qw = (width - 2 * pad) * 0.30
    cw = (width - 2 * pad) - qw
    roots = [s["us"] ** 0.5 for s in stages[1:]]
    tot = sum(roots) or 1.0
    parts = [f'<svg class="loop" viewBox="0 0 {width:.0f} {h:.0f}" role="img" '
             f'aria-label="the QEC cycle and its classical feedback loop">']
    x = pad
    boxes = []
    for i, s in enumerate(stages):
        w = qw if i == 0 else max(46.0, cw * roots[i - 1] / tot)
        boxes.append((x, w, s))
        x += w
    for bx, bw, s in boxes:
        quantum = s["kind"] == "quantum"
        fill, stroke = ("#e0e7ff", "#3730a3") if quantum else ("#ecfdf5", "#065f46")
        dash = "" if quantum else ' stroke-dasharray="5 3"'
        parts.append(f'<rect x="{bx:.1f}" y="{top:.0f}" width="{bw:.1f}" height="32" rx="5" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.3"{dash}/>')
        parts.append(f'<text x="{bx + bw / 2:.1f}" y="{top + 20:.0f}" text-anchor="middle" '
                     f'font-size="11" fill="#667085">{_fmt(s["us"])}</text>')
    x0, x1 = pad, x
    parts.append(f'<path d="M{x1:.1f} {top + 32:.0f} V96 H{x0 + qw / 2:.1f} V{top + 36:.0f}" '
                 f'fill="none" stroke="#065f46" stroke-width="1.3" stroke-dasharray="5 3"/>')
    parts.append(f'<path d="M{x0 + qw / 2 - 4:.1f} {top + 42:.0f} L{x0 + qw / 2:.1f} '
                 f'{top + 34:.0f} L{x0 + qw / 2 + 4:.1f} {top + 42:.0f} Z" fill="#065f46"/>')
    parts.append(f'<text x="{x0:.1f}" y="20" font-size="11" fill="#3730a3">'
                 f'one syndrome-extraction round (quantum)</text>')
    parts.append(f'<text x="{x0 + qw + 8:.1f}" y="20" font-size="11" fill="#065f46">'
                 f'the classical path: {_fmt(r["reaction_us"])} in all</text>')
    parts.append(f'<text x="{x0:.1f}" y="112" font-size="11" fill="#065f46">'
                 f'&#8627; react: the guard is back at the place, and only then may its '
                 f'operation start</text>')
    parts.append(f'<text x="{x1:.1f}" y="{top + 20:.0f}" font-size="11" fill="#065f46">'
                 f'&nbsp;&#8594; or it stops in the memory</text>')
    parts.append("</svg>")
    legend = " &middot; ".join(
        f'<b>{html.escape(s["stage"])}</b> {_fmt(s["us"])}' for s in stages)
    return "".join(parts) + f'<p class="why">{legend}</p>'


def board_section(tasks: list[dict]) -> str:
    """The section that compares the QEC clock cycle of every board, loop included.

    `tasks` are `{"id", "title", "round_us", "n_data"}` -- the board's own fastest verified
    round per task."""
    have = [t for t in tasks if t.get("round_us")]
    if not have:
        return ""
    # one header row: each decoder's column IS its margin, so the word goes in the head
    head = "".join(f'<th title="{html.escape(DECODERS[d]["note"])}">'
                   f'{html.escape(DECODERS[d]["title"])}<br><small>margin</small></th>'
                   for d in SHOWN_DECODERS)
    rows = []
    for t in have:
        r = cycle_report(t["round_us"], decoder="lut", mode="store")
        react = cycle_report(t["round_us"], decoder="lut", mode="react")
        margins = []
        for d in SHOWN_DECODERS:
            rr = cycle_report(t["round_us"], decoder=d, mode="store")
            cls = "ok" if rr["keeps_up"] else "bad"
            margins.append(f'<td class="{cls}">{_margin(rr["decoder_margin"])}</td>'
                           if rr["decoder_margin"] != float("inf") else "<td>&ndash;</td>")
        frac = 100 * react["reaction_fraction"]
        rows.append(
            f'<tr><td><a href="#{t["id"]}">{html.escape(t["title"])}</a></td>'
            f'<td>{_fmt(r["round_us"])}</td><td>{r["cycles_per_s"]:,.0f}</td>'
            f'{"".join(margins)}'
            f'<td>{_fmt(react["reaction_us"])}</td>'
            f'<td class="{"bad" if frac > 10 else "ok"}">{frac:.2g}%</td></tr>')
    for ref in ROUND_REFERENCE:
        if "superconducting" not in ref["platform"]:
            continue
        margins = []
        for d in SHOWN_DECODERS:
            rr = cycle_report(ref["round_us"], decoder=d, mode="store")
            margins.append(f'<td class="{"ok" if rr["keeps_up"] else "bad"}">'
                           f'{_margin(rr["decoder_margin"])}</td>')
        react = cycle_report(ref["round_us"], decoder="lut", mode="react")
        rows.append(
            f'<tr class="ref"><td>for scale: {html.escape(ref["platform"])} '
            f'({html.escape(ref["source"])})</td><td>{_fmt(ref["round_us"])}</td>'
            f'<td>{1e6 / ref["round_us"]:,.0f}</td>{"".join(margins)}'
            f'<td>{_fmt(react["reaction_us"])}</td>'
            f'<td class="bad">{100 * react["reaction_fraction"]:.0f}%</td></tr>')

    biggest = max(have, key=lambda t: t["round_us"])
    return (
        '<section class="cycle" id="cycle"><h2>The QEC clock cycle</h2>'
        '<p class="sub">A board ranks the quantum half of a cycle: one syndrome-extraction '
        'round. A machine has to close the loop as well &mdash; the outcomes cross a wire to '
        'a decoder, and what it decides either <b>stops in the classical memory</b> as a '
        'Pauli-frame update, which no ion waits for, or <b>comes back</b> as the guard of a '
        'classically controlled operation, which one does. Both are drawn here for the '
        f'slowest board ({html.escape(biggest["title"])}), then tabulated for all of them.</p>'
        + loop_svg(biggest["round_us"], decoder="lut") +
        '<table><thead><tr><th>board</th><th>a round</th><th>cycles/s</th>'
        f'{head}<th>reaction</th><th>of a cycle</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        '<p class="why"><b>Margin</b> is the round time divided by the decoding time: above '
        '1&times; the decoder keeps up and the undecoded backlog never grows; below it, the '
        'backlog grows every round and no amount of buffering fixes it. That is why the '
        'trapped-ion rows are comfortable with a decoder that the superconducting row cannot '
        'afford &mdash; the ion transport that makes a QCCD round slow also makes its '
        'classical loop nearly free.</p>'
        '<p class="why"><b>Reaction</b> is the last measurement to a usable decision: '
        f'{_fmt(LINK["readout_to_control_us"])} on the wire to the control system '
        f'(AQT M-ACTION, arXiv:2101.11390), the decode, '
        f'{_fmt(LINK["frame_write_us"])} to write the frame, and for a guarded operation '
        f'{_fmt(LINK["resolve_us"] + LINK["decision_us"])} to resolve it and reach the place. '
        'It is what a non-Clifford gate waits for, and it is checked in the schedule '
        '(G10, G11) of every algorithm under '
        '<a href="../gadgets/algorithms/">Logical Algorithm</a>.</p>'
        f'<p class="why">{html.escape(MODES["store"])} &mdash; and, in the other mode, '
        f'{html.escape(MODES["react"])}</p></section>')


def studio_payload() -> dict:
    """What the studio's QEC-cycle panel needs, so its JS hardcodes no latency.

    `places` carries the decoder, the classical memory and the wire as the gadget library
    defines them -- title, silhouette and colours -- so the decoder drawn over the studio's
    device is the same place, in the same shape and the same green, as the decoder in the
    gadget tool.  Retyping those three colours here is how two pictures of one thing start.
    """
    from ..gadget.categories import CATEGORIES
    places = {k: {"title": CATEGORIES[k]["title"], "shape": CATEGORIES[k]["shape"],
                  "stroke": CATEGORIES[k]["stroke"], "fill": CATEGORIES[k]["fill"]}
              for k in ("decoder", "archive", "wire") if k in CATEGORIES}
    # a wire CARRYING bits right now: lit while a `decode` instruction runs -- the same two
    # colours the gadget canvas lights its wires with, from the same entry
    if "wire" in places:
        for key in ("lit", "glow"):
            if key in CATEGORIES["wire"]:
                places["wire"][key] = CATEGORIES["wire"][key]
    return {"link": LINK, "decoders": DECODERS, "modes": MODES, "places": places,
            "reference": ROUND_REFERENCE, "shown": list(SHOWN_DECODERS)}


#: the panel's own styling, injected with it (the studio's CSS variables are in scope)
STUDIO_CSS = """
#tools #qcBtn.on { background: var(--navy, #1e2761); color: #fff; border-color: var(--navy, #1e2761); }
#tools #qcShow { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px;
  border: 1px solid var(--line, #d0d5dd); border-radius: 6px; background: var(--panel, #fff);
  color: var(--ink, #182230); cursor: pointer; user-select: none; white-space: nowrap; }
#tools #qcShow input { margin: 0; }
.qc-box { position: fixed; z-index: 60; width: 452px; max-height: 76vh; overflow: auto;
  background: #fff; border: 1px solid var(--line, #d0d5dd); border-radius: 8px;
  box-shadow: 0 10px 28px rgba(16,24,40,.16); padding: 10px 12px 12px;
  font: 12.5px/1.45 ui-sans-serif, system-ui, sans-serif; color: var(--ink, #182230); }
.qc-box .qc-head { display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 6px; }
.qc-box .qc-head label { font-size: 11.5px; color: var(--muted, #667085); }
.qc-box select { font: inherit; font-size: 11.5px; margin-left: 4px; }
.qc-box table.qc-t { width: 100%; border-collapse: collapse; margin: 4px 0 8px; }
.qc-box table.qc-t th { text-align: left; font-weight: 600; padding: 3px 0; width: 104px; }
.qc-box table.qc-t td { padding: 3px 0; }
.qc-box table.qc-t td.qc-w { color: var(--muted, #667085); font-size: 11.5px; text-align: right; }
.qc-box .qc-v { border-left: 3px solid #0b7a4b; background: #f2fbf6; padding: 5px 8px;
  margin: 0 0 6px; border-radius: 3px; }
.qc-box .qc-v.bad { border-left-color: #b42318; background: #fef3f2; }
.qc-box .qc-note { color: var(--muted, #667085); font-size: 11.5px; margin: 5px 0 0; }
.qc-box .qc-note i { font-style: normal; opacity: .85; }
"""


def studio_block() -> str:
    """The Design tab's QEC-cycle panel, as one self-contained style + script block.

    It is *injected* into `studio.html` by the site build rather than built into the studio
    itself: the panel reads the page's own priced runtime as the round and adds the
    classical path to it, so it needs nothing from the studio's modules and cannot drift
    from `qccd.analysis.feedback` -- the payload below IS that table.
    """
    js = (HERE / "qec_cycle.js").read_text(encoding="utf-8")
    payload = json.dumps(studio_payload(), separators=(",", ":"), ensure_ascii=False)
    js = js.replace("__PAYLOAD__", payload)
    return f"<style>{STUDIO_CSS}</style>\n<script>{js}</script>\n"
