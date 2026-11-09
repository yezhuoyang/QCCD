"""Goal 3's cost terms, as one table: every term of the error model with its time and error, then each
architecture's own terms (the team's feedback F1, Ke, 2026-11-07).

Each row is the term as the feedback writes it, and beside it what the Goal 3 sweep charges.  The
"Goal 3 charges" figures are read from what the sweep runs on -- qccd-physics@3, qccd-noise@3's
parameters, and the prices in `qccd.arch.c2lr`, `qccd.arch.wise` and `qccd.arch.cyclone` -- so the
table cannot say one thing while the points do another.  Where the feedback marks a value for the
noise-model team to verify, the sweep keeps the noise model's own (its slide 6, Q3).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

__all__ = ["section"]

_ROOT = Path(__file__).resolve().parents[2]
PENDING = "to be verified by the noise-model team"


def _e(s: str) -> str:
    return html.escape(str(s), quote=False)


def _g(x: float) -> str:
    """A number as the tables print it: 2.07e-5 as 2.07 × 10⁻⁵, 400.0 as 400."""
    import math
    if x == 0:
        return "0"
    if abs(x) >= 0.01:
        return f"{x:g}"
    e = math.floor(math.log10(abs(x)))
    m = f"{x / 10 ** e:.3g}"
    if m == "10":                     # 9.996e-5 rounds up to 10 x 10^-5: write 1 x 10^-4
        m, e = "1", e + 1
    sup = str(e).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
    return f"{m} × 10{sup}"


def _arith(text: str) -> float:
    """A parameter's text, which is a number or arithmetic on numbers (`5.5e-4 - 1.1e-5`)."""
    import ast
    import operator
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        raise ValueError(f"not arithmetic: {text!r}")
    return ev(ast.parse(text.split("#")[0].strip(), mode="eval").body)


def _numbers() -> dict:
    """What the sweep runs on, read where it is defined."""
    from ..arch import c2lr, cyclone, wise
    from ..evalset import load
    phys = json.loads((_ROOT / "qccd/workspace/physics/qccd-physics@3.json").read_text(encoding="utf-8"))
    p = phys["primitives"]
    curve = lambda k: p[k]["curve"][0]                       # noqa: E731
    ev = load("qccd-noise@3")
    par = {k: _arith(ev.param_text[k]) for k in ("r_1q", "r_2q", "leak_1q", "leak_2q", "leak_fold",
                                                 "p_reset", "p_readout", "p_crosstalk", "t_lin", "t_g",
                                                 "slope", "leak_transport_per_s")}
    step = c2lr.C2LR_DECK["Yb-Ba"]["swap_or_stay"]
    return {
        "us_1q": float(p["1q_gate"]["us"]), "us_ms": float(p["ms_gate"]["us"]), "us_measure": float(p["measure"]["us"]),
        "us_reset": float(p["reset"]["us"]), "us_cool": float(p["cool"]["us"]),
        "budget": float(p["ms_gate"]["max_quanta"]),
        "anomalous_per_s": float(phys["heating"]["anomalous_rate_quanta_per_ms"]) * 1000.0,
        "rail_us": float(curve("shuttle_segment")["us"]), "rail_q": float(curve("shuttle_segment")["quanta"]),
        "split_us": float(curve("split")["us"]), "split_q": float(curve("split")["quanta"]),
        "merge_us": float(curve("merge")["us"]), "merge_q": float(curve("merge")["quanta"]),
        "junction_q": float(p["junction_cross"]["curve_by_degree"]["3"][0]["quanta"]),
        "junction_us": float(p["junction_cross"]["curve_by_degree"]["3"][0]["us"]),
        "c2lr_us": float(step["us"]), "c2lr_swap_q": float(step["quanta_swap"]), "c2lr_stay_q": float(step["quanta_stay"]),
        "reorder_us": float(c2lr.C2LR_DECK["Yb-Ba"]["reorder_or_not"]["us"]),
        "reorder_q": float(c2lr.C2LR_DECK["Yb-Ba"]["reorder_or_not"]["quanta"]),
        "t0": wise.SWAP_US, "shim": wise.SHIM_US, "wise_1q": wise.PULSE_1Q_US, "residual": wise.RESIDUAL_1Q,
        "cy_split": cyclone.SPLIT_US, "cy_move": cyclone.MOVE_US, "cy_merge": cyclone.MERGE_US,
        **par,
    }


def _table(head: list[str], rows: list[list[str]], caption: str = "") -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    cap = f"<p>{caption}</p>" if caption else ""
    return f'{cap}<div class="tw"><table class="g3-costs"><tr>{th}</tr>{body}</table></div>'


def section() -> str:
    """The `<details>` block for board/goal3/: three tables and what they mean."""
    n = _numbers()
    pend = f"<i>{_e(PENDING)}</i>"
    p1, p2 = 1.5 * n["r_1q"], 1.25 * n["r_2q"]
    shared = [
        ["one-qubit gate", f"{_g(n['us_1q'])} µs (WISE {_g(n['wise_1q'])} + {_g(n['shim'])} µs)",
         f"r = {_g(n['r_1q'])} → DEPOLARIZE1, p = {_g(p1)}", "10⁻⁶ to 10⁻⁵ a gate", "as written"],
        ["two-qubit gate", f"{_g(n['us_ms'])} µs (Cyclone: grows with the trap, deck T4; WISE + {_g(n['shim'])} µs)",
         f"r = 5.5 × 10⁻⁴ → DEPOLARIZE2, p = 6.875 × 10⁻⁴; {_g(n['r_2q'])} without cooling dephasing",
         "10⁻⁵ to 10⁻⁴ a gate",
         f"p = {_g(p2)}: the r without cooling dephasing, since cooling counts in the idle window"],
        ["two-qubit heating penalty", "—", f"+{_g(n['slope'])} per quantum of gate-mode n̄ above 0.05, inside "
         "DEPOLARIZE2", "—", "as written"],
        ["one-qubit leakage", "—", f"{_g(n['leak_1q'])} an ion → DEPOLARIZE1, p = {_g(n['leak_fold'] * n['leak_1q'])}",
         "—", "as written"],
        ["two-qubit leakage", "—", f"{_g(n['leak_2q'])} a pair → DEPOLARIZE1, p = "
         f"{_g(n['leak_fold'] * n['leak_2q'] / 2)} on each", "—", "as written"],
        ["preparation, reset", f"{_g(n['us_reset'])} µs", f"{_g(n['p_reset'])}: R, then X_ERROR", "—", "as written"],
        ["readout", f"{_g(n['us_measure'])} µs", f"{_g(n['p_readout'])}, symmetric (M or MR)", "below 10⁻⁴ a shot",
         "as written"],
        ["measurement crosstalk", "—", f"{_g(n['p_crosstalk'])} a spectator, DEPOLARIZE1 (the same for MR)", "—",
         "every other ion on the chip, once for each ion read out with it: a simulation assumption (weak, "
         "independent scattering, first order)"],
        ["idle dephasing", "the idle window", f"Z_ERROR, p = ½[1 − exp(−t/T_lin − (t/T_g)²)], "
         f"T_lin = {n['t_lin']:.0f} s, T_g = {n['t_g']:.2f} s", "below 10⁻⁶ an hour",
         "as written, once over each window"],
        ["idle leakage", "the idle window", "the transport-layer rate × 0.75", "—",
         f"0.75 × 4.0 × 10⁻⁴ an ion per 55 ms, Jaewon's report's rate"],
    ]
    motion = [
        ["ground-state cooling", f"{_g(n['us_cool'] / 1000)} ms (Helios)", "the gate mode back to 0.05",
         "in the idle window; its 1.1 × 10⁻⁵ is inside the two-qubit r",
         f"{_g(n['us_cool'] / 1000)} ms, every family"],
        ["Doppler cooling", "—", "5.3 reference", "—", "not used"],
        ["anomalous heating", "per second", f"{_g(n['anomalous_per_s'])} a second, all three families",
         "through the two-qubit heating penalty", "as written"],
        ["shuttle, one rail", f"{_g(n['rail_us'])} µs (Jaewon: 10 to 100 µs)", f"{_g(n['rail_q'])}",
         "10⁻⁵ to 10⁻⁴ a shuttle; loss 10⁻⁶",
         f"{_g(n['rail_q'])} a rail; time from each family's own prices; no error a shuttle"],
        ["split", f"{_g(n['split_us'])} µs", f"6 (Helios: 2 or 4.16) {pend}", pend,
         f"{_g(n['split_q'])}, the noise model's lower figure"],
        ["merge", f"{_g(n['merge_us'])} µs", f"6 (Helios: grouped with the split) {pend}", pend,
         f"{_g(n['merge_q'])}, grouped with the split"],
        ["junction crossing", "100 µs, three rails (Jaewon: 50 to 200 µs)", "3 (Helios proxy: 0.014)",
         "10⁻³ to 10⁻² a junction, cool after; loss 10⁻⁵",
         f"{_g(n['junction_q'])}, the noise model's proxy; no error a crossing"],
        ["when to cool", "—", "R7: one broadcast cool before a batch, if any pair is over the heat budget",
         "the same rule for all three families (Q2)",
         f"as written, a budget of {_g(n['budget'])} quantum, counted from the last cool"],
    ]
    own = [
        ["junction step",
         f"swap-or-stay, one of 4 types a step (F3): {_g(n['c2lr_us'])} µs (Yb-Ba; Ba-Sr 312.5 µs). Two swaps 0.56 / "
         f"0.31, two stays 0.53 / 0.10. No fidelity measured; loss 10⁻⁵ a swap, 10⁻⁶ a stay"
         f"<br><b>Goal 3:</b> {_g(n['c2lr_us'])} µs; {_g(n['c2lr_swap_q'])} a swap, {_g(n['c2lr_stay_q'])} a stay "
         "(the stretch mode less the cooled residue, halved); no error",
         f"the same time and cost as C2LR: {_g(n['c2lr_us'])} µs, the same heat and loss"
         f"<br><b>Goal 3:</b> {_g(n['c2lr_us'])} µs; the heat of its rails, crossings, split and merge (2.2 to 2.3), "
         "not C2LR's", "a degree-2 corner, 10 µs; heat from the shared junction row"
         f"<br><b>Goal 3:</b> 10 µs; a corner's two rails, {_g(2 * n['rail_q'])}"],
        ["waveform overheads", "10 µs interpolation a step; 20 µs crystal rotation (Yb-Ba)"
         "<br><b>Goal 3:</b> inside the 400 µs (deck T2 to T4)",
         f"switch word 21 µs; shims 128 × 3 µs = {_g(n['shim'])} µs before each layer; shim drift 25 mV/m a "
         f"reconfiguration<br><b>Goal 3:</b> {_g(n['shim'])} µs in every layer's price; the word under t0; no drift "
         "error", "—"],
        ["reorder", f"reorder-or-not: {_g(n['reorder_us'])} µs, +19.5 / 7 mq (Helios: {_g(n['reorder_q'])}); loss 10⁻⁶"
         "<br><b>Goal 3:</b> priced; the router has no use for it", "—", "—"],
        ["ion swap", "done by swap-or-stay",
         f"zone to zone, one step (F4). Error ~5 × 10⁻³ (two-ion swap, Jaewon); loss ~10⁻⁶. Heat: split + merge = 12. "
         f"Time: t0 = {_g(n['t0'])} µs, split + merge = 160 µs, two-ion swap 42 to 120 µs (Jaewon). {pend}"
         f"<br><b>Goal 3:</b> one step, t0 = {_g(n['t0'])} µs; {_g(n['split_q'] + n['merge_q'] + n['rail_q'])} "
         "(split, merge, rail); no error",
         "GateSwap = 3 CX: 3 × 70 µs and 3 × the two-qubit error"
         "<br><b>Goal 3:</b> as written, on the ancilla and the ion it changes places with, where the chain order "
         "says one stands between it and its exit"],
        ["bulk transport", "—",
         "a reconfiguration, (2m + kn) × t0, at worst 22 ms, serial (F5)"
         "<br><b>Goal 3:</b> the router's real steps; the census shows the worst case",
         f"a ring hop, split / move / merge {_g(n['cy_split'])} / {_g(n['cy_move'])} / {_g(n['cy_merge'])} µs; "
         "about 3 rotations a round (Q1)<br><b>Goal 3:</b> as written"],
        ["broadcast", "a step reaches one chain position (F2): a leg's two crystals one position at a time",
         f"the same (F2); a layer's pulse leaves the zones it skips a residual {_g(n['residual'])} (deck T9)",
         "the same (F2), with each trap's laser switched by itself: one position in every trap a step plays in"],
    ]
    return (
        '<details id="g3-costs"><summary>What every family pays: the cost terms</summary>'
        "<p>Every term of the error model with its time and error, then each architecture&rsquo;s own, as the "
        "team&rsquo;s feedback lists them (F1). <b>Goal 3 charges</b> is what the points on this page were run "
        "with, read from the physics, the noise model and the prices the sweep uses. Where the feedback marks a "
        "value for the noise-model team to verify, the sweep keeps the noise model&rsquo;s own until it is. Ion "
        "loss is listed as Jaewon&rsquo;s report gives it; nothing here charges it.</p>"
        + _table(["term", "time", "error, and the Stim channel", "ion loss (Jaewon)", "Goal 3 charges"], shared,
                 "<b>The error model, the same for every family</b> (qccd-noise@3)")
        + _table(["term", "time", "heating, gate mode (quanta)", "error and ion loss (Jaewon)", "Goal 3 charges"], motion,
                 "<b>Cooling, motion and transport, the same for every family</b> (qccd-physics@3)")
        + _table(["term", "C2LR", "WISE", "Cyclone"], own, "<b>Each architecture&rsquo;s own</b>")
        + "</details>"
    )
