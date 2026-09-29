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
#qo-compiler table{border-collapse:collapse}
#qo-compiler th,#qo-compiler td{text-align:left;padding:6px 8px;border-bottom:1px solid #e6e5e1}
#qo-compiler th{font-weight:600;color:#52514e;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
#qo-compiler td.qo-n{font-variant-numeric:tabular-nums}
#qo-compiler .qo-empty,#qo-compiler .qo-wait{color:#6b6a66}
#qo-compiler .qo-err{color:#9a3412}
"""


def _e(s) -> str:
    return html.escape(str(s))


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

def architecture_intro() -> str:
    return (
        '<p class="sub">Two leaderboards. <b>Architecture</b> ranks devices: you design the machine, and the '
        "reference compiler compiles each board's circuit onto it. <b>Compiler</b> ranks compilers: you write "
        "the compiler, and it is run on every circuit and device of a benchmark suite. Both are graded by the "
        "same reference checker, locally and on the official server.</p>"
        '<nav class="lb-tabs"><a href="#architecture">Architecture</a><a href="#compiler">Compiler</a>'
        '<a href="noise/">The noise model</a></nav>')


def memory_section() -> str:
    """The memory boards: a design's logical error rate, not only its speed."""
    boards = memory_boards()
    if not boards:
        return ""
    noise = _noise()
    cards = []
    for b in boards:
        q = b.manifest["qec"]
        start = b.manifest.get("starter") or {}
        sp = ", ".join(f"{k} {v}" for k, v in (start.get("params") or {}).items())
        cards.append(
            f'<div class="lb-card" id="{_e(b.manifest["task"])}"><h3>{_e(b.title)}</h3>'
            f'<p>{_e(b.manifest.get("description", ""))}</p><div class="lb-kv">'
            f'<b>ranked by</b><span>logical error rate per round (lower is better), with its 95% interval</span>'
            f'<b>noise model</b><span><a href="noise/">{_e(q["noise"])}</a>, built from the device\'s own physics</span>'
            f'<b>sampling</b><span>up to {int(q["ler"].get("max_shots", 0)):,} shots or {int(q["ler"].get("max_errors", 0))} '
            f'logical errors, decoder {_e(q["ler"].get("decoder", "auto"))}, fixed seed</span>'
            f'<b>a start that passes</b><span>{_e(start.get("generator", ""))} ({_e(sp)})</span>'
            f'<b>submit</b><span>Try your own design below, or ask your agent to submit a design to it</span></div>'
            + _board_block(b.manifest["task"], b.title) + '</div>')
    how = ("" if not noise else
           f'<p class="lb-note">How the number is made: the board\'s memory experiment is compiled onto the design, '
           f'the compiled program is turned into a noisy stim circuit ({len(noise["channels"])} error channels, each '
           f'from a parameter of the device), checked to run the experiment (every detector deterministic with the '
           f'circuit\'s parity), and sampled and decoded. <a href="noise/">The noise model, channel by channel '
           f'&rarr;</a></p>')
    return ('<h2 id="memory">Memory boards: the logical error rate</h2>'
            "<p>A syndrome round's time is a proxy. These boards measure what it stands for: how often the "
            "encoded qubit is lost. Heating from transport raises the two-qubit gate error, longer chains raise it "
            "further, and idle time dephases; a design that moves less, or cools better, keeps the logical qubit "
            "longer.</p>" + "".join(cards) + how)


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
        f'<p class="sub">Submit a compiler, not a design. It is run on every pair of {len(summ["circuits"])} circuits '
        f'and {len(summ["devices"])} devices ({summ["pairs"]} public pairs, and {summ["hidden_pairs"]} more that only '
        "the official server generates, so a compiler tuned to the public ones is still measured on circuits and "
        "devices it has never seen). Each program it writes is checked without trusting it: every rule of the "
        "device, then whether it computes the circuit (the stabilizer tableau, the exact unitary, or for a QEC "
        "memory experiment its detectors). Then it is timed, and a memory experiment's logical error rate is "
        "measured.</p>"
        f'<div class="lb-card"><h3>{_e(s.title)} <small>{_e(s.id)}</small></h3><div class="lb-kv">'
        f'<b>ranked by</b><span>speedup over the reference compiler, over the pairs both compile; one wrong '
        f'program makes an entry ineligible</span>'
        f'<b>the reference</b><span>qccdc with cooling: {counts.get("valid", 0)} of {len(base)} pairs valid, '
        f'{counts.get("wrong", 0)} breaking a rule, {counts.get("refused", 0)} refused, '
        f'{counts.get("timeout", 0)} out of time</span>'
        f'<b>circuits</b><span>{_e(", ".join(summ["circuits"]))}</span>'
        f'<b>devices</b><span>{_e(", ".join(summ["devices"]))}</span></div>'
        '<p><a href="compiler/">The suite pair by pair, the contract a compiler follows, and how to submit '
        '&rarr;</a></p></div>'
        f'<table class="lb-t"><thead><tr><th>metric</th><th></th><th>what it measures</th></tr></thead>'
        f'<tbody>{metrics}</tbody></table>'
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
            tip = _e(r.get("reason") or "")
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

def noise_page(PAGE: str, STYLE: str) -> str:
    n = _noise()
    if n is None:
        body = "<h1>The noise model</h1><p>The noise model is not in this build.</p>"
        return PAGE.format(title="Noise model - QCCD studio", style=STYLE, extra_css=CSS, body=body)
    rows = "".join(f'<tr><td><code>{_e(c["channel"])}</code></td><td>{_e(c["where"])}</td>'
                   f'<td>{_e(c["formula"])}</td><td><code>{_e(c["stim"])}</code></td><td>{_e(c["source"])}</td></tr>'
                   for c in n["channels"])
    try:
        from ..qec import EXPERIMENTS, get_experiment
        exps = [get_experiment(k).summary() for k in EXPERIMENTS]
    except Exception:
        exps = []
    erows = "".join(f'<tr><td><code>{_e(e.get("name"))}</code></td><td>{_e(e.get("code"))}</td>'
                    f'<td class="n">{_e(e.get("distance"))}</td><td class="n">{_e(e.get("rounds"))}</td>'
                    f'<td class="n">{_e(e.get("n_data"))} + {_e(e.get("n_ancilla"))}</td></tr>' for e in exps)
    body = [
        f"<h1>The noise model <small>{_e(n['id'])}</small></h1>",
        '<p class="sub">How a compiled QCCD program becomes a logical error rate. The program is replayed on the '
        "device, which says where every ion is, how hot it is and how long everything takes; each operation "
        "becomes a gate of a stim circuit followed by the error channels below, each with a probability computed "
        "from a parameter of the device itself. The circuit's detectors come from the memory experiment's "
        "circuit, so the same experiment means the same thing whichever compiler compiled it.</p>",
        '<nav class="lb-tabs"><a href="../#memory">The memory boards</a><a href="../compiler/">The compiler '
        'benchmark</a><a href="../../physics/">Physics background</a></nav>',
        '<h2 id="channels">The channels</h2>',
        f'<div style="overflow-x:auto"><table class="lb-t"><thead><tr><th>channel</th><th>where</th><th>probability</th>'
        f'<th>in stim</th><th>from the device</th></tr></thead><tbody>{rows}</tbody></table></div>',
        '<h2 id="check">What counts as running the experiment</h2>',
        "<p>Before any sampling, the noiseless circuit is checked. Every detector and observable must be "
        "deterministic, and must have the parity the source circuit gives it. The second half matters: a program "
        "that implements a gate with the opposite sign (an MS pulse of &minus;&pi;/2 for +&pi;/2) keeps every "
        "detector deterministic but flips its value, and is caught only there.</p>",
        '<h2 id="experiments">The memory experiments</h2>',
        '<table class="lb-t"><thead><tr><th>experiment</th><th>code</th><th>distance</th><th>rounds</th>'
        f'<th>qubits (data + ancilla)</th></tr></thead><tbody>{erows}</tbody></table>',
        "<p>The data readout after the last round is added by the grader, where the ions are, with the device's "
        "measurement error. The reference compiler's rotation cannot measure an ion that rides the loop. This is "
        "an approximation, and the next version of the model revisits it together with the rest: idle time per ion "
        "from the schedule, dephasing from transport, crosstalk, ion loss and imperfect cooling.</p>",
    ]
    return PAGE.format(title="Noise model - QCCD studio", style=STYLE, extra_css=CSS, body="\n".join(body))


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
    out = [{"t": "Compiler benchmark", "d": "the Compiler leaderboard's suite, pair by pair, and how to submit a compiler",
            "u": "board/compiler/", "k": "page"},
           {"t": "The noise model", "d": "how a compiled program becomes a logical error rate, channel by channel",
            "u": "board/noise/", "k": "page"},
           {"t": "Architecture leaderboard", "d": "devices ranked, each compiled by the reference compiler",
            "u": "board/#architecture", "k": "page"},
           {"t": "Compiler leaderboard", "d": "compilers ranked over every circuit and device of the suite",
            "u": "board/#compiler", "k": "page"}]
    for b in memory_boards():
        out.append({"t": b.title, "d": "memory board · ranked by the logical error rate per round",
                    "u": f"board/#{b.manifest['task']}", "k": "task"})
    return out
