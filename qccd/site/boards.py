"""The Leaderboard's two sections, and the pages the Compiler section and the memory boards need.

    Architecture   a person designs a device; the reference compiler compiles each board's circuit
                   onto it; the grader checks and ranks it.  The syndrome-round boards (seed entries
                   and official submissions) and the MEMORY boards, which rank by the logical error
                   rate per round.
    Compiler       a person submits a compiler; it is run over every (circuit, device) pair of a
                   benchmark suite and ranked by how much faster its programs run than the
                   reference compiler's, over the pairs both compile.

Everything here is generated from the code that grades it, at build time: the memory boards from
the task releases (`qccd.workspace.tasks.list_boards`), the suite and the reference compiler's
results from `qccd.bench`, the noise model from `qccd.qec`.  So a new board, suite or noise model
shows up on the next build with nothing edited by hand.  Every control on these pages is a link or
a fold-out (declared by being what it is); the seed pages' chart controls, which had no
declaration, are declared here as they are copied (`declare_board_controls`).
"""

from __future__ import annotations

import html
import json
import re

__all__ = ["memory_boards", "architecture_intro", "memory_section", "compiler_section", "compiler_page",
           "noise_page", "declare_board_controls", "index_entries", "CSS"]

CSS = """
.lb-tabs{display:flex;gap:10px;margin:14px 0 6px;flex-wrap:wrap}
.lb-tabs a{display:inline-block;padding:7px 14px;border:1px solid var(--line,#e4e2db);border-radius:999px;
 text-decoration:none;color:#1a2540;font-weight:600;font-size:14px;background:#fff}
.lb-tabs a:hover{border-color:#1f5bb5}
.lb-sec{margin-top:34px;padding-top:6px;border-top:2px solid #1a2540}
.lb-sec>h2{font-size:28px;margin:10px 0 4px}
.lb-card{border:1px solid #e4e2db;border-radius:12px;padding:16px 18px;margin:14px 0;background:#fcfbf8}
.lb-card h3{margin:0 0 4px}
.lb-card p{margin:6px 0}
.lb-kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;font-size:14px;margin:8px 0}
.lb-kv b{color:#56545e;font-weight:600}
.lb-card .lb-meta{color:#6b6a66;font-size:13px;margin:2px 0 6px}
.lb-goaltag{font:700 10.5px/1 var(--sans,system-ui,sans-serif);letter-spacing:.06em;text-transform:uppercase;vertical-align:3px;
 margin-left:8px;padding:3px 7px;border-radius:999px;background:#eef3fb;color:#1f5bb5;text-decoration:none}
details.lb-more-d{margin:8px 0;font-size:13.5px;color:#56545e}details.lb-more-d summary{cursor:pointer;color:#1f5bb5}
.lb-t{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0}
.lb-t th,.lb-t td{text-align:left;padding:5px 8px;border-bottom:1px solid #eceae3;vertical-align:top}
.lb-t th{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:#56545e}
.lb-t td.n{font-variant-numeric:tabular-nums;text-align:right}
.lb-m td{text-align:center;font-size:12px;padding:4px 3px;white-space:nowrap}
.lb-m td.valid{background:#e7f4ec;color:#0b5d36}
.lb-m td.wrong{background:#fbe9e7;color:#9a3412}
.lb-m td.refused,.lb-m td.timeout,.lb-m td.crash{background:#f1f0ec;color:#8a8892}
.lb-m th.dev{writing-mode:vertical-rl;transform:rotate(180deg);font-size:11px;text-transform:none;letter-spacing:0}
.lb-note{color:#56545e;font-size:14px}
.lb-rank{margin:12px 0 4px}.lb-rkh{color:#52514e;font-size:13px;margin:0 0 8px}
.lb-bar{display:grid;grid-template-columns:22px minmax(110px,170px) 1fr auto;gap:10px;align-items:center;padding:5px 0;text-decoration:none;color:inherit}
.lb-bar:hover .lb-nm{text-decoration:underline}.lb-rk{font-weight:600;color:#52514e;text-align:center}
.lb-tr{position:relative;height:12px;background:#efeeeb;border-radius:6px}
.lb-fl{position:absolute;left:0;top:0;bottom:0;background:#2a78d6;border-radius:6px}
.lb-iv{position:absolute;top:5px;height:2px;background:#0b0b0b}
.lb-v{font-variant-numeric:tabular-nums;white-space:nowrap}.lb-v small{color:#6b6a66}
.lb-more{margin:8px 0 0;font-size:13px}
#qo-compiler table{border-collapse:collapse}
#qo-compiler th,#qo-compiler td{text-align:left;padding:6px 8px;border-bottom:1px solid #e6e5e1}
#qo-compiler th{font-weight:600;color:#52514e;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
#qo-compiler td.qo-n{font-variant-numeric:tabular-nums}
#qo-compiler .qo-empty,#qo-compiler .qo-wait{color:#6b6a66}
#qo-compiler .qo-err{color:#9a3412}
"""


def _e(s) -> str:
    return html.escape(str(s))


#: A compiler's refusal can name the scratch file it was handed (`C:\...\Temp\...\circuit.qasm:`,
#: `/var/folders/.../circuit.qasm:`): the page says "the circuit" instead of the grader's path.
_SCRATCH_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s:]*[\\/]circuit\.qasm")


def _reason(s) -> str:
    return _SCRATCH_PATH.sub("the circuit", str(s or ""))


def _board_block(task: str, title: str) -> str:
    from .leaderboard import board_block
    return board_block(task, title, seed_note=False)


# ---------------------------------------------------------------------- the data, from the grading code

def memory_boards() -> list:
    """The boards ranked by the logical error rate: task releases with a memory experiment."""
    try:
        from ..workspace.tasks import list_boards
    except Exception:                      # the workspace package is optional for a site build
        return []
    return [b for b in list_boards() if b.manifest.get("qec")]


def _goal1(b) -> bool:
    """A board on Goal 1's one ranking: a curated memory on the shared noise model."""
    return (b.manifest.get("qec") or {}).get("noise") == "qccd-noise@2"


def _suite():
    try:
        from ..bench.suite import find_suite
        return find_suite()
    except Exception:
        return None


def _noise(model: str = "qccd-noise@1") -> dict | None:
    try:
        from ..qec import describe_noise
        return describe_noise(model)
    except Exception:
        return None


# ---------------------------------------------------------------------- the Leaderboard page's parts

#: The two goals the leaderboard is for (John, Ke, Hartmut and Jens, 2026-10-21): what each asks,
#: and where the platform measures progress on it today.
GOALS = (
    ("Goal 1", "The best QEC design under 200 ions",
     "Lowest logical error rate per round per logical qubit, any curated code.",
     "goal1/", "The Goal 1 leaderboard"),
    ("Goal 2", "A verified resource estimate for Shor's algorithm",
     "Ions, time and machine, with every step compiled and checked.",
     "../gadgets/algorithms/shor15/", "Shor's algorithm for 15"),
)

GOALS_CSS = """
.lb-goals{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:12px 0 18px}
.lb-goal{border:1px solid var(--line,#e4e2db);border-left:3px solid var(--accent,#1f5bb5);border-radius:10px;
 background:var(--card,#fbfaf7);padding:14px 16px}
.lb-goal .k{font:700 10.5px/1 var(--sans);letter-spacing:.09em;text-transform:uppercase;color:var(--accent,#1f5bb5)}
.lb-goal h3{font-family:var(--serif);font-size:19px;font-weight:600;color:var(--head,#1a2540);margin:6px 0 6px}
.lb-goal p{margin:0 0 8px;color:var(--ink2,#56545e);font-size:14px;line-height:1.55}
.lb-goal a{font-size:13.5px;font-weight:600;color:var(--accent,#1f5bb5);text-decoration:none}
.lb-goal a:hover{text-decoration:underline}
@media (max-width:760px){.lb-goals{grid-template-columns:1fr}}
"""


def goals_section() -> str:
    """The two goals at the top of the Leaderboard page, each with where it is measured today."""
    cards = "".join(
        f'<div class="lb-goal"><span class="k">{_e(k)}</span><h3>{_e(title)}</h3><p>{_e(what)}</p>'
        f'<a href="{_e(href)}">{_e(label)} &rarr;</a></div>'
        for k, title, what, href, label in GOALS)
    return f'<section class="lb-goals" id="goals" aria-label="Goals">{cards}</section>'


def architecture_intro() -> str:
    return (
        '<nav class="lb-tabs"><a href="#architecture">Architecture</a><a href="#compiler">Compiler</a>'
        '<a href="noise/">The noise model</a></nav>')


def _ler_bars(t: dict) -> str:
    """One memory board's ranking, as the time boards show theirs: the three designs with the
    lowest logical error rate per round, each bar a link to its page (the ion animation and
    the stim program it was sampled from), and a link to the full ranking plot."""
    rows = [r for r in t.get("rows", []) if r.get("status") == "ok" and (r.get("numbers") or {}).get("ler_per_round") is not None]
    if not rows:
        return ""
    key = lambda r: (r["numbers"]["ler_per_round"], r["numbers"].get("ler_round_hi_1e4", 0.0), r["numbers"].get("T_jones", r["numbers"].get("T_quantinuum", 0.0)))  # noqa: E731
    rows.sort(key=key)
    shown = rows[:3]
    top = max((r["ler"]["per_round_hi"] for r in shown), default=0.0) or 1.0
    few = max(r["ler"]["errors"] for r in rows) < 20
    bars = []
    for i, r in enumerate(shown, 1):
        e = r["ler"]
        bars.append(
            f'<a class="lb-bar" href="{_e(t["id"])}/{_e(r["page"])}" title="{_e(r.get("title", ""))}">'
            f'<span class="lb-rk">{i}</span><span class="lb-nm">{_e(r.get("short") or r["key"])}</span>'
            f'<span class="lb-tr"><span class="lb-fl" style="width:{max(1.0, 100 * e["per_round"] / top):.1f}%"></span>'
            f'<span class="lb-iv" style="left:{100 * e["per_round_lo"] / top:.1f}%;width:{max(0.5, 100 * (e["per_round_hi"] - e["per_round_lo"]) / top):.1f}%"></span></span>'
            + (f'<span class="lb-v">0 errors in {e["shots"]:,} shots<small> (below {e["per_round_hi"]:.1e})</small></span></a>'
               if not e["errors"] else
               f'<span class="lb-v">{e["per_round"]:.2e}<small> ({e["per_round_lo"]:.1e} to {e["per_round_hi"]:.1e})</small></span></a>'))
    note = " (statistically tied at this budget)" if few else ""
    refused = sum(1 for r in t.get("rows", []) if r.get("status") != "ok" or r.get("refusal"))
    return (f'<div class="lb-rank"><p class="lb-rkh">The study&rsquo;s designs, with 95% intervals{note}</p>{"".join(bars)}'
            f'<p class="lb-more"><a href="{_e(t["id"])}/">All {len(rows)} ranked'
            f'{f", {refused} refused" if refused else ""} &rarr;</a></p></div>')


def memory_section(ts: list | None = None) -> str:
    """The memory boards: a design's logical error rate, not only its speed.  `ts` are the
    site's tasks; one whose `release` is a memory board carries that board's ranked designs.
    Goal 1's boards (the curated codes on qccd-noise@2) are not cards here: they are one table
    under the goals (leaderboard.goal_table) and one chart (board/goal1/)."""
    boards = [b for b in memory_boards() if not _goal1(b)]
    if not boards:
        return ""
    built = {t.get("release"): t for t in (ts or []) if t.get("rank_by") == "ler_per_round"}
    noise = _noise()
    cards = []
    for b in boards:
        q = b.manifest["qec"]
        goal = q["noise"] == "qccd-noise@2"
        cards.append(
            f'<div class="lb-card" id="{_e(b.manifest["task"])}"><h3>{_e(b.title)}'
            + (' <a class="lb-goaltag" href="goal1/">Goal 1</a>' if goal else "") + '</h3>'
            f'<p class="lb-meta">logical error rate per round, lower is better &middot; '
            f'<a href="noise/#{dict(NOISE_MODELS).get(q["noise"], "")}">{_e(q["noise"])}</a> &middot; up to '
            f'{int(q["ler"].get("max_shots", 0)):,} shots</p>'
            + (_ler_bars(built[b.id]) if b.id in built else "")
            + _board_block(b.manifest["task"], b.title) + '</div>')
    how = "" if not noise else '<p class="lb-note"><a href="noise/">How the number is made &rarr;</a></p>'
    return ('<h2 id="memory">Memory boards: the logical error rate</h2>' + "".join(cards) + how)


def compiler_section() -> str:
    """The Leaderboard's Compiler section: what is measured, the reference, how to take part."""
    s = _suite()
    if s is None:
        return ('<section class="lb-sec" id="compiler"><h2>Compiler</h2><p class="lb-note">The benchmark suite '
                'is not in this build.</p></section>')
    summ = s.summary()
    base = s.baseline()
    counts: dict = {}
    for v in base.values():
        counts[v.get("status")] = counts.get(v.get("status"), 0) + 1
    metrics = "".join(f'<tr><td><code>{_e(m["name"])}</code></td><td>{_e(m["better"])} is better</td>'
                      f'<td>{_e(m["about"])}</td></tr>' for m in s.manifest["metrics"])
    return (
        '<section class="lb-sec" id="compiler"><h2>Compiler</h2>'
        f'<p class="sub">Submit a compiler: it runs on {summ["pairs"]} circuit&ndash;device pairs, plus '
        f'{summ["hidden_pairs"]} hidden ones, ranked by speedup over the reference.</p>'
        f'<div class="lb-card"><h3>{_e(s.title)} <small>{_e(s.id)}</small></h3>'
        f'<p class="lb-meta">reference qccdc: {counts.get("valid", 0)} of {len(base)} pairs valid &middot; '
        f'{len(summ["circuits"])} circuits &middot; {len(summ["devices"])} devices &middot; one wrong program makes an '
        'entry ineligible</p>'
        '<p><a href="compiler/">The suite, pair by pair, and how to submit &rarr;</a></p>'
        f'<details class="lb-more-d"><summary>The measures</summary><table class="lb-t"><thead><tr><th>metric</th>'
        f'<th></th><th>what it measures</th></tr></thead><tbody>{metrics}</tbody></table></details></div>'
        '<h3>Official ranking</h3><div id="qo-compiler"><p class="qo-wait">Loading the official ranking...</p></div>'
        "</section>")


# ---------------------------------------------------------------------- the Compiler board's own page

def compiler_page(PAGE: str, STYLE: str) -> str:
    s = _suite()
    if s is None:
        body = "<h1>Compiler benchmark</h1><p>The benchmark suite is not in this build.</p>"
        return PAGE.format(title="Compiler benchmark - QCCD studio", style=STYLE, extra_css=CSS, body=body)
    from ..bench.contract import describe
    c = describe()
    base = s.baseline()
    circuits = [x["name"] for x in s.manifest["circuits"]]
    devices = [x["name"] for x in s.manifest["devices"]]
    kinds = {x["name"]: x["kind"] for x in s.manifest["circuits"]}
    qubits = {x["name"]: x["qubits"] for x in s.manifest["circuits"]}
    head = "".join(f'<th class="dev">{_e(d)}</th>' for d in devices)
    rows = []
    for cn in circuits:
        cells = []
        for d in devices:
            r = base.get(f"{cn}@{d}") or {}
            st = r.get("status", "-")
            t = (r.get("metrics") or {}).get("T_jones")
            txt = f"{t:.2f}" if st == "valid" and isinstance(t, (int, float)) else st
            tip = _e(_reason(r.get("reason")))
            cells.append(f'<td class="{_e(st)}" title="{tip}">{_e(txt)}</td>')
        rows.append(f'<tr><th>{_e(cn)} <small>{_e(kinds[cn])}, {qubits[cn]} qubits</small></th>{"".join(cells)}</tr>')
    ex = c["exit_codes"]
    body = [
        f"<h1>{_e(s.title)}</h1>",
        f'<p class="sub">{_e(s.manifest.get("description", ""))}</p>',
        '<nav class="lb-tabs"><a href="../#compiler">The ranking</a><a href="#contract">The contract</a>'
        '<a href="#submit">Submit</a><a href="../noise/">The noise model</a></nav>',
        '<h2 id="reference">The reference compiler, pair by pair</h2>',
        "<p>Each cell is one pair: the round time in milliseconds (T_jones) where qccdc's program is valid; "
        "<i>wrong</i> where it breaks one of the device's rules (hover for which); <i>refused</i> where it cannot "
        "compile the pair. Every one of these is a place another compiler can do better.</p>",
        f'<div style="overflow-x:auto"><table class="lb-t lb-m"><thead><tr><th>circuit</th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>',
        '<h2 id="contract">The contract</h2>',
        f"<p>A compiler is a directory with <code>{_e(c['manifest_file'])}</code>. The grader runs it once per pair, "
        "with no network, a time and memory limit, and nothing it can reach but the pair's files:</p>",
        f'<pre>{_e(c["invocation"])}</pre>',
        '<table class="lb-t"><tbody>'
        + "".join(f"<tr><td><code>{_e(k)}</code></td><td>{_e(v)}</td></tr>" for k, v in c["outputs"].items())
        + "".join(f"<tr><td>exit {_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in ex.items())
        + "</tbody></table>",
        # the manifest as `qccd bench init-compiler` writes it, with the script named in words (the site
        # shows no file of a source tree, the reader's included)
        f'<pre>{_e(json.dumps(dict(c["starter"], entry=["python3", "<your script>"]), indent=1))}</pre>',
        f"<p>Runtimes: {_e(', '.join(c['runtimes']))}. Cooling is the compiler's decision; the heating budget "
        "(rule R7) is checked like every other rule.</p>",
        '<h2 id="submit">Submit a compiler</h2>',
        "<pre>qccd bench init-compiler my-compiler      # a working one to start from: the reference\n"
        "qccd bench run --compiler my-compiler       # grade it on the public pairs, on your machine\n"
        "qccd bench publish my-compiler --server https://qccd.academy/official</pre>",
        "<p>The run grades every pair locally, exactly as the server will (the hidden pairs aside), and your "
        "agent can read the report (<code>qccd_get_bench</code>). Running a compiler runs your code, so it "
        "happens in your shell, never through the agent's tools. Publishing needs you at the terminal: it shows "
        "the compiler's digest and asks you to type it back. On the server the compiler runs in a separate sandbox "
        "container with no network and no secrets.</p>",
    ]
    return PAGE.format(title="Compiler benchmark - QCCD studio", style=STYLE, extra_css=CSS, body="\n".join(body))


# ---------------------------------------------------------------------- the noise model's page

#: The noise models a board can name, newest first, and the anchor each has on the noise page.
NOISE_MODELS = (("qccd-noise@2", "noise2"), ("qccd-noise@1", "noise1"))


def _physics2_rows() -> str:
    """qccd-physics@2, the physics the @2 boards are graded under: every number and its source."""
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "workspace" / "physics" / "qccd-physics@2.json"
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    rows = []

    def row(name, value, source, note=""):
        rows.append(f'<tr><td><code>{_e(name)}</code></td><td>{_e(value)}</td><td>{_e(source)}</td>'
                    f'<td>{_e(note)}</td></tr>')

    prim = d.get("primitives", {})
    for name in ("ms_gate", "1q_gate", "measure", "reset", "cool"):
        s = prim.get(name)
        if not s:
            continue
        bits = []
        if "fidelity_at_n0" in s:
            bits.append(f"error {1 - float(s['fidelity_at_n0']):.2g} at n̄ = 0")
        if "fidelity" in s:
            bits.append(f"error {1 - float(s['fidelity']):.2g}")
        if "error" in s:
            bits.append(f"error {float(s['error']):.2g}")
        if "error_vs_quanta" in s:
            bits.append(f"+{str(s['error_vs_quanta']).split(':')[-1]} per quantum")
        if "us" in s:
            bits.append(f"{s['us']} µs")
        row(name, ", ".join(bits), s.get("source", ""), s.get("note", ""))
    for name in ("shuttle_segment", "split", "merge", "ion_swap"):
        for pt in (prim.get(name) or {}).get("curve", []):
            row(name, f"{pt['us']} µs, {pt['quanta']} quanta", pt.get("source", ""), pt.get("label", ""))
    for deg, pts in sorted(((prim.get("junction_cross") or {}).get("curve_by_degree") or {}).items()):
        for pt in pts:
            row(f"junction_cross (degree {deg})", f"{pt['us']} µs, {pt['quanta']} quanta", pt.get("source", ""),
                pt.get("label", ""))
    h = d.get("heating") or {}
    if h:
        row("heating", f"{h.get('anomalous_rate_quanta_per_ms')} quanta/ms", h.get("source", ""), h.get("note", ""))
    sp = d.get("species") or {}
    if sp:
        row("species", f"{sp.get('qubit')} qubit, {sp.get('coolant')} coolant", sp.get("source", ""), sp.get("note", ""))
    return "".join(rows)


def noise_page(PAGE: str, STYLE: str) -> str:
    models = [(mid, anchor, _noise(mid)) for mid, anchor in NOISE_MODELS]
    models = [(mid, anchor, n) for mid, anchor, n in models if n is not None]
    if not models:
        body = "<h1>The noise model</h1><p>The noise model is not in this build.</p>"
        return PAGE.format(title="Noise model - QCCD studio", style=STYLE, extra_css=CSS, body=body)
    used: dict = {}
    for b in memory_boards():
        used.setdefault(b.manifest["qec"]["noise"], []).append(b)
    try:
        from ..qec import EXPERIMENTS, get_experiment
        exps = [get_experiment(k).summary() for k in EXPERIMENTS]
    except Exception:
        exps = []
    erows = "".join(f'<tr><td><code>{_e(e.get("experiment") or e.get("name"))}</code></td><td>{_e(e.get("code"))}</td>'
                    f'<td>{_e(e.get("basis"))}</td>'
                    f'<td class="n">{_e(e.get("distance"))}</td><td class="n">{_e(e.get("rounds"))}</td>'
                    f'<td class="n">{_e(e.get("n_data"))} + {_e(e.get("n_ancilla"))}</td></tr>' for e in exps)
    body = [
        "<h1>The noise models</h1>",
        '<p class="sub">How a compiled QCCD program becomes a logical error rate. The program is replayed on the '
        "device, which says where every ion is, how hot it is and how long everything takes. Each operation "
        "becomes a gate of a stim circuit followed by the error channels below, and each channel's probability "
        "comes from a parameter of the device. Every channel is a Pauli channel that stim samples directly. The "
        "circuit's detectors come from the memory experiment's circuit, so an experiment means the same thing "
        "whichever compiler compiled it. A board names its model; changing the physics makes a new model, never "
        "an edit to an old one.</p>",
        '<nav class="lb-tabs">' + "".join(f'<a href="#{a}">{_e(m)}</a>' for m, a, _ in models)
        + '<a href="../#memory">The memory boards</a><a href="../../physics/">Physics background</a></nav>',
    ]
    for mid, anchor, n in models:
        boards = used.get(mid, [])
        on = (" Used by " + ", ".join(f'<a href="../#{_e(b.manifest["task"])}">{_e(b.title)}</a>' for b in boards)
              + "." if boards else "")
        rows = "".join(f'<tr><td><code>{_e(c["channel"])}</code></td><td>{_e(c["where"])}</td>'
                       f'<td>{_e(c["formula"])}</td><td><code>{_e(c["stim"])}</code></td><td>{_e(c["source"])}</td></tr>'
                       for c in n["channels"])
        body.append(f'<h2 id="{anchor}">{_e(mid)}</h2>')
        if mid == "qccd-noise@2":
            body.append(
                "<p>It prices what the schedule does, not only the gates. An ion loses memory for every instruction "
                "that does not operate on it, whether it idles, rides a rotation or is shuttled, linearly in time "
                "and coherently over each idle stretch. A readout disturbs the ions beside it, and every gate starts "
                "from the motion cooling leaves behind. Every number is a published measurement on trapped-ion "
                "hardware. A per-shuttle error is left out because no experiment reports one: transport is charged "
                "through its time and its heating instead. Ion loss is left out too, because no vendor reports a "
                "number and it is not a Pauli channel. "
                "A Z-basis memory cannot see dephasing, so the boards under this model measure an X and a Z "
                f"memory together.{on}</p>")
        else:
            body.append("<p>Gates, readout, reset and a T₂ dephasing on every ion, priced from the device's own "
                        f"physics block.{on}</p>")
        body.append(f'<div style="overflow-x:auto"><table class="lb-t"><thead><tr><th>channel</th><th>where</th>'
                    f'<th>probability</th><th>in stim</th><th>from</th></tr></thead><tbody>{rows}</tbody></table></div>')
        if mid == "qccd-noise@2":
            prow = _physics2_rows()
            if prow:
                body.append(
                    '<h3 id="physics2">The physics it is graded under: qccd-physics@2</h3>'
                    "<p>A ¹³⁷Ba⁺ QCCD with ¹⁷¹Yb⁺ coolant. Its gate, readout and memory errors are Helios's "
                    "(Quantinuum, arXiv:2511.05465), and its transport times are Quantinuum's published primitives, "
                    "in the operating-point table <code>quantinuum</code>. One segment of a design is one zone "
                    "pitch, 375 µm. A design entered on a board with this physics takes it unless it set its own.</p>"
                    '<div style="overflow-x:auto"><table class="lb-t"><thead><tr><th>primitive</th><th>value</th>'
                    f'<th>source</th><th>note</th></tr></thead><tbody>{prow}</tbody></table></div>')
    body += [
        '<h2 id="check">What counts as running the experiment</h2>',
        "<p>Before any sampling, the noiseless circuit is checked. Every detector and observable must be "
        "deterministic, and must have the parity the source circuit gives it. The second half matters: a program "
        "that implements a gate with the opposite sign (an MS pulse of &minus;&pi;/2 for +&pi;/2) keeps every "
        "detector deterministic but flips its value, and is caught only there.</p>",
        '<h2 id="experiments">The memory experiments</h2>',
        '<table class="lb-t"><thead><tr><th>experiment</th><th>code</th><th>basis</th><th>distance</th><th>rounds</th>'
        f'<th>qubits (data + ancilla)</th></tr></thead><tbody>{erows}</tbody></table>',
        "<p>The data readout after the last round is added by the grader, where the ions are, with the device's "
        "measurement error; an XZ experiment reads each patch in its own basis. The reference compiler's rotation "
        "cannot measure an ion that rides the loop. This is an approximation: the readout's own transport is not "
        "charged.</p>",
    ]
    return PAGE.format(title="Noise models - QCCD studio", style=STYLE, extra_css=CSS, body="\n".join(body))


# ---------------------------------------------------------------------- declarations for the seed pages

#: the seed task pages' chart controls, which had no declaration (docs/agent-interface.md's debt list)
BOARD_HINTS = {
    "board:ysel": ("Rank by", "choose the metric the designs are ranked by (the chart's vertical axis)"),
    "board:xsel": ("Against", "choose what the ranking is plotted against: rank order, or another metric"),
    "board:log": ("Log scale", "show the ranked metric on a logarithmic axis"),
    "board:vchart": ("Chart view", "show the designs as a chart"),
    "board:vtable": ("Table view", "show the designs as a table"),
}


def declare_board_controls(page: str) -> str:
    """Give a seed task page's chart controls their declarations: a data-hint on each, and the
    page's QCCD_HINTS entry saying what it does.  Done as the page is copied, so the seed files
    (made by tools outside this branch) stay as they are."""
    for key in BOARD_HINTS:
        cid = key.split(":", 1)[1]
        page = page.replace(f'id="{cid}"', f'id="{cid}" data-hint="{key}"', 1)
    hints = {k: {"t": t, "d": d} for k, (t, d) in BOARD_HINTS.items()}
    script = ("<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, "
              + json.dumps(hints) + ");</script>")
    return page.replace("</body>", script + "</body>", 1) if "</body>" in page else page + script


def index_entries() -> list:
    out = [{"t": "Goal 1: the best QEC design under 200 ions",
            "d": "every implementation under 200 ions on one ranking, by logical error rate per logical qubit",
            "u": "board/goal1/", "k": "page"},
           {"t": "Compiler benchmark", "d": "the Compiler leaderboard's suite, pair by pair, and how to submit a compiler",
            "u": "board/compiler/", "k": "page"},
           {"t": "The noise model", "d": "how a compiled program becomes a logical error rate, channel by channel",
            "u": "board/noise/", "k": "page"},
           {"t": "Architecture leaderboard", "d": "devices ranked, each compiled by the reference compiler",
            "u": "board/#architecture", "k": "page"},
           {"t": "Compiler leaderboard", "d": "compilers ranked over every circuit and device of the suite",
            "u": "board/#compiler", "k": "page"}]
    for b in memory_boards():
        out.append({"t": b.title, "d": ("Goal 1 · ranked by the logical error rate per round per logical qubit"
                                        if _goal1(b) else "memory board · ranked by the logical error rate per round"),
                    "u": f"board/goal1/?code={b.id}" if _goal1(b) else f"board/#{b.manifest['task']}", "k": "task"})
    return out
