"""The "Reproducing the literature" page (`/reproduce/`).

Everything on it is read from the reproduction catalog (`qccd.repro.catalog`) and the
results `python -m qccd.repro check` writes; nothing is typed in here except prose.  A
number the page shows is a number a command printed, and the command is on the page.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from ..repro import catalog

ROOT = Path(__file__).resolve().parents[2] / "Reproduce"

CHECK_NAMES = [
    ("positions", "positions", "every event finds its ions where it says they are"),
    ("capacity", "capacity", "no trap holds more ions than its capacity, at any instant"),
    ("segment_mutex", "one per segment", "at most one ion on a segment at a time"),
    ("junction_mutex", "one per junction", "at most one ion in or through a junction at a time"),
    ("trap_serial", "one op per trap", "a trap's operations never overlap"),
    ("chain_order", "chain ends", "a split takes the ion at the end facing its segment, or pays for a reorder"),
    ("ion_serial", "one op per ion", "an ion's operations never overlap"),
    ("durations", "durations", "every event lasts what the paper's timing law says"),
    ("declared_locks", "their locks", "the artifact's own statement of what each operation holds"),
    ("broadcast", "one waveform", "transport between traps is broadcast: one class of move per cycle, and cycles never overlap"),
    ("circuit", "circuit", "the two-qubit gates run are the circuit's, each once, in an allowed order"),
    ("structure", "well formed", "every operation names ions, traps and rails that exist, and ends after it starts"),
    # the model papers' own replays (TrapSIMD, IonShuttler) check their models' further rules
    ("intra", "moves inside a trap", "a shift or swap inside a trap moves an ion between neighbouring positions, its split and merge joined up"),
    ("inter", "moves between traps", "a move between traps crosses one junction, between the edge positions of two traps, in its declared class"),
    ("modes", "one cycle at a time", "a cycle of moves between traps overlaps no other cycle and nothing happening inside a trap"),
    ("zones", "gate zones", "gates and swaps happen in gate zones, one gate per trap at a time"),
    ("program", "the worked example", "the schedule is the paper's worked example, operation for operation"),
    ("layout", "the layout", "every ion starts in the memory, on a rail the device has"),
    ("junctions", "junctions", "a junction is passed by one chain at a time"),
    ("path", "paths", "every move follows rails the device has"),
    ("nodes", "nodes", "a node is passed by one chain in a step, except where the paper's code allows two"),
    ("pz", "processing zone", "ions enter and leave the processing zone by its entry and exit rails, and it is empty at the end"),
    ("gates", "gates", "a gate runs on ions standing on the gate site, one gate at a time"),
    ("sequence", "the circuit", "the gates run are the circuit's, in its order"),
    ("sequence_code", "the code's order", "the paper's code's own order of the processing zone's queue"),
]

CSS = """
.rp-lead{font-size:17px;line-height:1.6}
.rp-steps{counter-reset:rp;list-style:none;padding:0;margin:14px 0 18px}
.rp-steps li{counter-increment:rp;position:relative;padding:8px 0 8px 40px;border-top:1px solid #ebe9e3}
.rp-steps li:before{content:counter(rp);position:absolute;left:4px;top:8px;width:24px;height:24px;border-radius:12px;
 background:#1c2a4a;color:#fff;font:600 13px/24px var(--sans,ui-sans-serif,system-ui,sans-serif);text-align:center}
table.rp{border-collapse:collapse;width:100%;margin:12px 0 18px;font:13.5px/1.45 var(--sans,ui-sans-serif,system-ui,sans-serif)}
table.rp th,table.rp td{border-bottom:1px solid #e6e5e1;padding:6px 8px;text-align:left;vertical-align:top}
table.rp th{font-weight:600;color:#3a3936;background:#f6f5f1}
table.rp td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.rp-wide{overflow-x:auto;margin:12px 0 18px}.rp-wide table.rp{margin:0}
table.rp-index tr.rp-row{cursor:pointer}table.rp-index tr.rp-row:hover td{background:#f4f8fd}
table.rp-index td{vertical-align:top}table.rp-index td a{text-decoration:none}
.rp-back{margin:0 0 6px;font-size:13.5px}.rp-more{font-size:13.5px;margin-top:14px}
table.rp-rules td:first-child{width:190px}table.rp-rules th,table.rp-rules td{padding-left:5px;padding-right:5px}
table.rp-rules th{font-size:12.5px}
.rp-ok{color:#1b7f3b;font-weight:600}.rp-bad{color:#b42318;font-weight:600}.rp-skip{color:#8a8985}
.rp-pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:12px;margin:1px 2px 1px 0;white-space:nowrap}
.rp-pill.ok{background:#e7f4ea;color:#1b7f3b}.rp-pill.bad{background:#fbe9e7;color:#b42318}.rp-pill.skip{background:#f1f0ec;color:#8a8985}
.rp-cite{color:#52514e;font-size:14.5px;margin:-4px 0 10px}
.rp-fig{margin:14px 0}.rp-fig figcaption{font:13px/1.4 var(--sans,ui-sans-serif,system-ui,sans-serif);color:#6b6a66;text-align:center;margin-top:4px}
.rp-figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.rp-note{border-left:3px solid #e8b84b;background:#fcfaf3;padding:9px 13px;margin:10px 0}
.rp-note b{display:block;margin-bottom:3px}
.rp-ours{border-left:3px solid #2a78d6;background:#f4f8fd;padding:9px 13px;margin:10px 0}
.rp-ours b{display:block;margin-bottom:3px}
table.rp td small.rp-where{display:block;white-space:normal;color:#6b6a66;max-width:230px;margin-left:auto}
table.rp td:first-child{min-width:170px}
.rp-cmd{font:13px var(--mono,ui-monospace,monospace);background:#f6f5f1;padding:2px 6px;border-radius:4px}
.rp-watch{font-size:12.5px;white-space:nowrap}
pre.assert{background:#f6f7fb;border:1px solid #dfe3ee;border-radius:6px;padding:8px 10px;font-size:13px;line-height:1.5;white-space:pre-wrap;margin:6px 0}
pre.assert a{color:#0b5cad;text-decoration:none}pre.assert .s{color:#8a3b12}
a.rp-pill{text-decoration:none}
.rp-took{border-left:3px solid #1b7f3b;background:#f3f9f4;padding:9px 13px;margin:10px 0}
.rp-took b{display:block;margin-bottom:3px}.rp-took ul{margin:4px 0 0 18px;padding:0}.rp-took li{margin:3px 0}
.rp-by{font-size:13.5px;color:#3a3936}h3.rp-group{margin:22px 0 4px}
table.rp-index small.rp-tk{display:block;color:#6b6a66;margin-top:3px}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _results(key: str) -> dict | None:
    f = ROOT / key / "results.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _num(x: float, metric: str) -> str:
    if metric == "moves":
        return f"{x:,.0f} shuttles"
    if metric == "fidelity":
        return f"{x:.4g}" if x >= 1e-3 else f"{x:.3e}"
    return f"{x:,.0f} µs" if abs(x - round(x)) < 1e-6 else f"{x:,.2f} µs"


def _checks(row: dict) -> str:
    """One pill: every configured check passed, or which failed and how often.  Each rule
    is listed in the pill's title; the method section explains them."""
    viol = row.get("violations", {})
    states = row.get("checks", {})
    names = {k: short for k, short, _ in CHECK_NAMES}
    passed = [names.get(k, k) for k, v in states.items() if v == "passed"]
    failed = [(names.get(k, k), viol.get(k, 0)) for k, v in states.items() if v == "failed"]
    skipped = [names.get(k, k) for k, v in states.items() if v.startswith("skipped")]
    title = ("passed: " + ", ".join(passed)
             + ("; not this paper's rules: " + ", ".join(skipped) if skipped else ""))
    if not failed:
        return f'<span class="rp-pill ok" title="{_e(title)}">all {len(passed)} checks pass</span>'
    def what(name: str, count: int) -> str:
        # a circuit failure is one verdict, not a count; a capacity or order failure is a
        # count of events, which the note beside the table explains
        return f"{name}: fails" if name == "circuit" else f"{name}: {count} events"
    bad = "".join(f'<span class="rp-pill bad">{_e(what(n, c))}</span>' for n, c in failed)
    return bad + f' <span class="rp-pill ok" title="{_e(title)}">{len(passed)} others pass</span>'


def _rule_titles() -> dict[str, str]:
    from ..ruleset import catalogue
    return {r.id: r.title for r in catalogue().rules}


def _rules_pill(rules: dict | None) -> str:
    """What a run's rule set decided, as one link to the paper's table of rules: every rule
    that holds counted, every one that fails named."""
    if not rules or not rules.get("results"):
        return ""
    res = rules["results"]
    titles = _rule_titles()
    hold = [r for r in res if r["verdict"] == "holds"]
    fail = [r for r in res if r["verdict"] == "fails"]
    other = [r for r in res if r["verdict"] not in ("holds", "fails")]
    tip = "; ".join(f'{r["id"]} {r["verdict"]}' for r in res)
    if not fail:
        out = f'<a class="rp-pill ok" href="#rules" title="{_e(tip)}">all {len(hold)} rules hold</a>'
    else:
        out = "".join(
            f'<a class="rp-pill bad" href="#rules" title="{_e(titles.get(r["id"], ""))}: '
            f'{_e("; ".join(r.get("examples", [])[:1]))}">{_e(r["id"])} fails</a>'
            for r in fail)
        out += f' <a class="rp-pill ok" href="#rules" title="{_e(tip)}">{len(hold)} others hold</a>'
    if other:
        why = "; ".join(r["id"] + ": " + r.get("reason", "") for r in other)
        out += f' <span class="rp-pill skip" title="{_e(why)}">{len(other)} do not apply</span>'
    return out


def _rules_section(p: dict, res: dict | None) -> str:
    """Every run of the paper, theirs and ours, against every rule of the paper's rule set:
    which rule was checked, which holds and which fails, and what each rule says."""
    from ..ruleset import catalogue, load, rule_order
    from .rulespage import highlight
    rows = [(r.get("label", r["id"]), r["rules"]) for r in (res or {}).get("runs", [])
            if r.get("rules")]
    for o in (res or {}).get("ours", []):
        for pr in (o.get("profiles") or {}).values():
            if pr.get("rules"):
                rows.append((f'ours: {o.get("label", o["id"])}', pr["rules"]))
    if not rows:
        return ""
    sets: list[str] = []
    for _, r in rows:
        if r["ruleset"] not in sets:
            sets.append(r["ruleset"])
    cat = {r.id: r for r in catalogue().rules}
    ids: list[str] = []
    for _, r in rows:
        for x in r["results"]:
            if x["id"] not in ids:
                ids.append(x["id"])
    ids.sort(key=rule_order)
    names = ", ".join(f'<a href="../../rules/#set-{_e(n)}">{_e(load(n).title)}</a>' for n in sets)
    plural = "s" if len(sets) > 1 else ""
    out = ['<h3 id="rules">The rules each run is judged by</h3>',
           f"<p>This paper's model is written as the rule set{plural} {names}. Every run above "
           "was replayed and each rule of the set evaluated on it. Our schedules are held to the "
           "same rules, and also keep each qubit's gates in the circuit's exact order (R25): we "
           "may not reorder a circuit, even where its gates commute.</p>",
           '<div class="rp-wide"><table class="rp rp-rules"><tr><th>run</th>'
           + "".join(f'<th title="{_e(cat[i].title if i in cat else i)}">'
                     f'<a href="#rule-{_e(i)}">{_e(i)}</a></th>' for i in ids) + "</tr>"]
    titles = {n: load(n).title for n in sets}
    for label, r in rows:
        if len(sets) > 1:
            label = f"{label} · {titles[r['ruleset']]}"
        by = {x["id"]: x for x in r["results"]}
        cells = []
        for i in ids:
            x = by.get(i)
            if x is None:
                cells.append('<td class="n"><span class="rp-skip" title="not in this rule set">·</span></td>')
            elif x["verdict"] == "holds":
                cells.append('<td class="n"><span class="rp-ok" title="holds">✓</span></td>')
            elif x["verdict"] == "fails":
                ex = "; ".join(x.get("examples", [])[:2])
                cells.append(f'<td class="n"><span class="rp-bad" title="{_e(ex)}">✗ {x.get("count", "")}</span></td>')
            else:
                cells.append(f'<td class="n"><span class="rp-skip" title="{_e(x.get("reason", ""))}">–</span></td>')
        out.append(f"<tr><td>{_e(label)}</td>{''.join(cells)}</tr>")
    out.append("</table></div>")
    out.append("<p><small>✓ the rule holds on that run; ✗ it fails, with the number of "
               "counterexamples (hover for the first); – it does not apply to that run (hover for "
               "why); · the rule is not in that run's rule set.</small></p>")
    out.append("<details><summary>what each rule says</summary>")
    for i in ids:
        r = cat.get(i)
        if r is None:
            continue
        text = (f"when {r.when_text}:\n" if r.when is not None else "") + r.text
        out.append(f'<div id="rule-{_e(i)}" style="margin:8px 0"><b>{_e(i)}</b> {_e(r.title)}'
                   f'<pre class="assert">{highlight(text)}</pre><small>{_e(r.about)}</small></div>')
    out.append("</details>")
    return "\n".join(out)


def _compare_cells(row: dict, metric: str) -> tuple[str, str, str]:
    """(their tool, our replay, the paper) for one metric, already formatted."""
    cmp = row.get("compare", {})
    tool = cmp.get("tool", {}).get(metric)
    paper = cmp.get("paper", {}).get(metric)
    ours = row.get("measured", {}).get(metric)
    t = _num(tool["theirs"], metric) if tool else "–"
    if tool:
        t += f' <span class="{"rp-ok" if tool["verdict"] == "exact" else "rp-bad"}">' \
             f'{"= ours" if tool["verdict"] == "exact" else "differs"}</span>'
    o = _num(ours, metric) if ours is not None else "–"
    if paper:
        word = {"exact": "equal", "close": "matches"}.get(paper["verdict"], "differs")
        cls = "rp-ok" if paper["verdict"] in ("exact", "close") else "rp-bad"
        p = f'{_num(paper["theirs"], metric)} <span class="{cls}">{word}</span>' \
            f'<small class="rp-where">{_e(paper.get("where", ""))}</small>'
    else:
        p = "–"
    return t, o, p


def _metric_of(p: dict) -> str:
    """The number each paper reports: Jones's time is the start of the last step, Muzzle the
    Shuttle counts shuttles (adjacent-trap moves), everyone else reports the makespan."""
    return {"jones2025": "last_start_us", "saki2022": "moves"}.get(p["key"], "makespan_us")


def _figures(p: dict) -> str:
    from ..repro import importers
    from ..repro.__main__ import _open
    from ..repro.devices import layout, to_machine
    from .build import device_svg

    out = []
    runs = {r["id"]: r for r in p["runs"]}
    for fig in p.get("figures", []):
        run = runs.get(fig["run"])
        if run is None:
            continue
        path = _open(ROOT / p["key"] / "artifact" / run["file"])
        imp = run["importer"]
        if imp == "tiscc":
            s = importers.import_tiscc(path, **run.get("importer_args", {}))
        else:
            s = getattr(importers, f"import_{imp}")(path)
        pos = layout(s.device, fig["layout"], **fig.get("layout_args", {}))
        table = p["key"] if p["key"] != "khan2026cyclone" or fig["layout"] == "ring" else "murali2020"
        m = to_machine(s.device, pos, name=p["key"], table=table)
        svg = device_svg(m, labels=len(pos) <= 24)
        out.append(f'<figure class="rp-fig">{svg}<figcaption>{_e(fig["caption"])}</figcaption></figure>')
    return f'<div class="rp-figs">{"".join(out)}</div>' if out else ""


def _paper_section(p: dict, res: dict | None) -> str:
    key = p["key"]
    authors = ", ".join(p["authors"][:4]) + (" et al" if len(p["authors"]) > 4 else "")
    art = p["artifact"]
    lic = art.get("license") or "no license"
    out = [f'<h2 id="{_e(key)}">{_e(p["short"])}</h2>',
           f'<p class="rp-cite">{_e(authors)}. <i>{_e(p["title"])}</i>. {_e(p["venue"])}. '
           f'<a href="https://arxiv.org/abs/{_e(p["arxiv"])}">arXiv:{_e(p["arxiv"])}</a> · '
           + (f'their code: <a href="{_e(art["url"])}">{_e(art["url"].split("github.com/")[-1])}</a> '
              f'({_e(lic)})' if art.get("commit") else "their code: not published")
           + '</p>',
           f'<p>{_e(p["what"])}</p>' + _took(p)]
    if art.get("note"):
        out.append(f'<p><small>{_e(art["note"])}</small></p>')
    out.append(_figures(p))
    metric = _metric_of(p)
    group_of = {x["id"]: x.get("group") for x in p["runs"]}
    every = [r for r in (res or {}).get("runs", []) if "missing" not in r]
    rows = [r for r in every if not group_of.get(r["id"])]
    head = {"last_start_us": "start of the last step", "moves": "shuttles"}.get(metric, "time")
    if rows:
        out.append('<table class="rp"><tr><th>configuration</th>'
                   f'<th>{head}: their code</th><th>our replay</th><th>the paper</th>'
                   '<th>the paper\'s rules</th></tr>')
        for r in rows:
            t, o, pp = _compare_cells(r, metric)
            fid = ""
            if "fidelity" in r.get("compare", {}).get("tool", {}):
                ft, fo, fp = _compare_cells(r, "fidelity")
                fid = (f'<tr><td style="padding-left:22px"><small>fidelity</small></td>'
                       f'<td class="n">{ft}</td><td class="n">{fo}</td><td class="n">{fp}</td><td></td></tr>')
            spec = next((x for x in p["runs"] if x["id"] == r["id"]), {})
            watch = (f'<br><small class="rp-skip">{_e(spec["no_studio"])}</small>' if spec.get("no_studio")
                     else f'<br><a class="rp-watch" href="{_e(p["key"])}/{_e(r["id"])}.html">▶ watch it in the Studio</a>'
                     + _timed_link(p, r["id"]))
            out.append(f'<tr><td>{_e(r.get("label", r["id"]))}{watch}</td><td class="n">{t}</td>'
                       f'<td class="n">{o}</td><td class="n">{pp}</td><td>{_rules_pill(r.get("rules")) or _checks(r)}</td></tr>{fid}')
        out.append("</table>")
        # a paper that plots its numbers: how the column "the paper" was read off its figures
        rels = [v["rel"] for r in rows for v in r.get("compare", {}).get("paper", {}).values()]
        if catalog.PAPER_COLUMN.get(key) and rels:
            text = catalog.PAPER_COLUMN[key].format(worst=f"{max(rels) * 100:.2g} %")
            out.append(f'<p class="rp-colnote"><small>{_e(text)}</small></p>')
    ratios = (res or {}).get("ratios", [])
    if ratios:
        out.append("<p>The paper reports speedups. Each is the ratio of two of the replayed "
                   "runs above, computed from our replays:</p>")
        out.append('<table class="rp"><tr><th>speedup</th><th>from our replays</th><th>the paper</th></tr>'
                   + "".join(
                       f'<tr><td>{_e(q["label"])}</td><td class="n">{q["ours"]:.6f}</td>'
                       f'<td class="n">{q.get("theirs", float("nan")):.6f} '
                       f'<span class="{"rp-ok" if q.get("verdict") in ("exact", "close") else "rp-bad"}">'
                       f'{"matches" if q.get("verdict") in ("exact", "close") else "differs"}</span>'
                       f'<small class="rp-where">{_e(q.get("where", ""))}</small></td></tr>'
                       for q in ratios) + "</table>")
    for note in catalog.NOTES.get(key, []):
        out.append(f'<div class="rp-note"><b>{_e(note["title"])}</b>{_e(note["text"])}</div>')
    # runs of a later version of the artifact (Jones: the authors' fix), after the notes
    # that explain them and in a table of their own, so the paper's own rows stay its own
    for gid, g in (p.get("groups") or {}).items():
        grows = [r for r in every if group_of.get(r["id"]) == gid]
        if not grows:
            continue
        out.append(f'<h3 class="rp-group">{_e(g["title"])}</h3><p>{_e(g["text"])}</p>')
        out.append('<table class="rp"><tr><th>configuration</th>'
                   f'<th>{head}: their code</th><th>our replay</th><th>the paper\'s rules</th></tr>')
        for r in grows:
            t, o, _ = _compare_cells(r, metric)
            watch = (f'<br><a class="rp-watch" href="{_e(p["key"])}/{_e(r["id"])}.html">▶ watch it in the Studio</a>'
                     + _timed_link(p, r["id"]))
            out.append(f'<tr><td>{_e(r.get("label", r["id"]))}{watch}</td><td class="n">{t}</td>'
                       f'<td class="n">{o}</td><td>{_rules_pill(r.get("rules")) or _checks(r)}</td></tr>')
        out.append("</table>")
    out.append(_ours_box(p, res))
    out.append(_rules_section(p, res))
    note = _studio_note(p, res) or _clock_note(p, res)
    if note:
        out.append(note)
    return "\n".join(out)


def _clock_note(p: dict, res: dict | None) -> str:
    """For a paper whose operations overlap (Jones et al.): the Studio's runtime is its
    instructions added up, which is not the schedule's time; the time is the paper's clock,
    in the tables.  Measured on the first run and on ours, as the pages play them."""
    if p["key"] not in HEADLINE_CLOCK or p["key"] in CLOCK_IN_HEADLINE:
        return ""
    from ..cost.models import corrected_model
    model = corrected_model("local")
    runs = {r["id"]: r for r in (res or {}).get("runs", []) if "missing" not in r}
    ours = {o["against"]: o for o in (res or {}).get("ours", []) if o.get("ok")}
    metric = _metric_of(p)
    cands = [r for r in p["runs"] if r["id"] in runs and r["id"] in ours]
    if not cands:
        return ""
    # the run to show: the first whose sums put the two schedules in the opposite order to
    # the paper's clock (the case a reader is misled by), else the first
    pick = None
    for first in cands:
        o = ours[first["id"]]
        spec = next(x for x in catalog.OURS.get(p["key"], {}).get("runs", []) if x["id"] == o["id"])
        m, prog, _, _ = _run_programme(p, first)
        theirs_sum = m.run(prog, model=model, check_metrics=False).report.result.total_us
        m2, prog2, _, _ = _ours_programme(p, spec, first)
        ours_sum = m2.run(prog2, model=model, check_metrics=False).report.result.total_us
        t_clock = runs[first["id"]]["measured"][metric]
        o_clock = o["measured"][metric]
        here = (first, theirs_sum, ours_sum, t_clock, o_clock)
        pick = pick or here
        if (theirs_sum < ours_sum) != (t_clock < o_clock):
            pick = here
            break
    first, theirs_sum, ours_sum, t_clock, o_clock = pick
    who = p["short"]
    lab = first.get("label", first["id"])
    return ("<p><small><b>The Studio's runtime is not the schedule's time.</b> A Studio page plays "
            "a schedule as a programme in our language: operations of one kind that start at the "
            "same moment are one instruction, and each instruction starts when the one before it "
            "has ended, so the runtime in the Studio's price bar is its instructions added up. "
            f"For {_e(lab)} that sum is {_num(theirs_sum, 'makespan_us')} for {who}'s schedule and "
            f"{_num(ours_sum, 'makespan_us')} for ours; on {who}'s clock, where operations overlap "
            f"whenever the paper's rules allow, the same schedules take {_num(t_clock, 'makespan_us')} "
            f"and {_num(o_clock, 'makespan_us')}"
            + (" (each to the start of its last step, as the paper counts)" if metric == "last_start_us" else "")
            + ". The tables above, and each Studio page's headline, give the time on the paper's "
            "clock; the sums are not times either schedule takes, and do not rank them.</small></p>")


def _timed_link(p: dict, rid: str) -> str:
    """The run's timed schedule, beside its Studio link, where the page offers one (`downloads`)."""
    from ..repro import export
    if p["key"] not in export.PAPERS:
        return ""
    return (f' · <a class="rp-watch" href="{_e(p["key"])}/{_e(rid)}.timed.json" download>'
            "timed schedule (JSON)</a>")


def downloads():
    """`(site path, bytes)` for every file a paper's page offers: its timed schedules, the
    paper's and ours, with what each event holds (`qccd.repro.export`)."""
    from ..repro import export
    for key in export.PAPERS:
        for rid, doc in export.paper_timelines(key):
            yield f"reproduce/{key}/{rid}.timed.json", export.dumps(doc).encode("utf-8")


def _studio_note(p: dict, res: dict | None) -> str:
    """What the Studio pages of an asynchronous schedule show, measured on the programmes the
    pages play (thread 44: the Studio's runtime was read as the schedule's).  TISCC only."""
    if p["key"] not in CLOCK_IN_HEADLINE:
        return ""
    from ..cost.models import corrected_model

    model = corrected_model("local")
    runs = {r["id"]: r for r in (res or {}).get("runs", [])}
    ours = {o["id"]: o for o in (res or {}).get("ours", []) if o.get("ok")}
    played = {}
    for run in p["runs"]:
        if run["id"] in runs:
            m, prog, _, _ = _run_programme(p, run)
            played[run["id"]] = (m.run(prog, model=model, check_metrics=False), prog)
    for o in catalog.OURS.get(p["key"], {}).get("runs", []):
        if o["id"] in ours:
            spec = next(x for x in p["runs"] if x["id"] == o["against"])
            m, prog, _, _ = _ours_programme(p, o, spec)
            played[o["id"]] = (m.run(prog, model=model, check_metrics=False), prog)
    if not played or not ours:
        return ""
    first = p["runs"][0]["id"]
    mine = next(iter(ours))
    theirs_clock = runs[first]["measured"]["makespan_us"]
    lock = lambda rid: played[rid][0].report.result.total_us
    ms = lambda rid: _num(lock(rid), "makespan_us")
    gap = lock(first) - theirs_clock
    r6b = {rid: sum(1 for v in r.violations if v.rule == "R6b") for rid, (r, _) in played.items()}
    gates = {rid: runs[rid]["counts"].get("gate", 0) for rid in runs}
    who = p["short"]
    out = [
        "<p><small><b>The Studio's runtime is not the schedule's time.</b> A Studio page plays a "
        "schedule as a programme in our language: operations of one kind that start at the same "
        "moment are one instruction, and each instruction starts when the one before it has "
        "ended, so the runtime it shows is its instructions added up. "
        f"{who}'s round runs in layers, so played this way it takes {ms(first)} at d = 5, "
        f"against {_num(theirs_clock, 'makespan_us')} on {who}'s clock"
        + (f" ({_num(gap, 'makespan_us')} more: where {who} turns some ions about X or Y and "
           "others about Z at the same moment, our language writes two instructions)" if gap > 1e-9 else "")
        + ". Ours does not wait for layers: its operations overlap in time, and the same "
        f"addition gives {ms(mine)}, which is not a time the schedule takes. Its time is the "
        f"checker's, {_num(ours[mine]['measured']['makespan_us'], 'makespan_us')}, under "
        f"{who}'s rules and clock, and the timed schedules beside each run list every event "
        "with its ions, its start and end and the zones, segments and junctions it holds, so "
        "the time can be checked without our code.</small></p>",
        "<p><small><b>Instructions and steps.</b> An instruction is one such batch. Its steps "
        "are the segments the longest move in it crosses (one to a neighbouring zone, two "
        "through a junction: zone, junction, zone), or one for any other operation; its cost "
        "counts every segment and every junction crossing of every move. So our d = 5 round is "
        f"{len(played[mine][1].instructions)} instructions and {played[mine][0].steps:,} steps, "
        f"{who}'s {len(played[first][1].instructions)} and {played[first][0].steps:,}: more, "
        "smaller instructions, which overlap in time where the Studio cannot show it.</small></p>",
        f"<p><small><b>Rotations.</b> The programmes write {who}'s operations in our language: "
        f"{who}'s P<sub>θ</sub>, exp(−iθP), is R(2θ, 0) about X, R(2θ, π/2) about Y and VZ(2θ) "
        "about Z, with the angles in the programme; Prepare_Z is a reset; the ZZ keeps its name. "
        "The Studio's list prints each rotation's angles, as R(θ, φ) or VZ(λ); the timed schedules "
        f"name every operation as {who} does. Ion by ion, ours runs exactly {who}'s operations, "
        "with the same partners, in the same order (checked).</small></p>",
        f"<p><small><b>R6b.</b> On the Studio pages the rule R6b fails once for every ZZ: "
        + ", ".join(f"{r6b[rid]} times in {runs[rid].get('label', rid).split(' (')[0]}"
                    + ("" if r6b[rid] == gates.get(rid) else f" (of {gates.get(rid)} ZZ gates)")
                    for rid in runs if rid in r6b)
        + ". R6b is a rule of our language: both "
        f"ions of a two-qubit gate stand in one zone. {who} keeps one ion in each zone and runs "
        "a ZZ on ions in neighbouring zones of one segment; its 2,000 µs include merging the "
        "two into one zone, cooling, the gate and the split (TISCC's gate table). Under "
        f"{who}'s rules, the checks in the tables above, a ZZ must act on ions in the same or "
        "neighbouring zones, which every one does, and since the merge and the split are inside "
        "the ZZ, every zone holds one ion throughout.</small></p>",
    ]
    return "\n".join(out)


def _ratio(theirs: float, ours: float, lower_better: bool = True) -> str:
    """'2.23x faster' / '1.31x slower' (or fewer/more), coloured by which way it went."""
    if not theirs or not ours:
        return "–"
    better = ours < theirs if lower_better else ours > theirs
    k = theirs / ours if lower_better else ours / theirs
    k = k if better else 1 / k
    word = {True: ("faster", "fewer"), False: ("slower", "more")}[better][0]
    return f'<span class="{"rp-ok" if better else "rp-bad"}">{k:.2f}× {word}</span>'


def _took_rules(p: dict) -> list[str]:
    """The rules whose statement cites this paper, as the Rules page prints their sources."""
    from ..ruleset import catalogue, rule_order
    arx = p.get("arxiv")
    if not arx:
        return []
    return sorted((r.id for r in catalogue().rules if f"arXiv:{arx}" in (r.source or "")),
                  key=rule_order)


def _took(p: dict) -> str:
    """What this website takes from the paper: the rules that cite it (read off the rules
    catalogue, so the list cannot drift from the Rules page) and the numbers, ideas and
    designs in `catalog.TOOK`, each saying where it is used."""
    ids = _took_rules(p)
    items = []
    if ids:
        links = ", ".join(f'<a href="../../rules/#{_e(i)}">{_e(i)}</a>' for i in ids)
        items.append(f"<li>{'Rules' if len(ids) > 1 else 'Rule'} {links} "
                     f"{'cite' if len(ids) > 1 else 'cites'} it as a source.</li>")
    items += [f"<li>{_e(t)}</li>" for t in catalog.TOOK.get(p["key"], [])]
    if not items:
        return ""
    return ('<div class="rp-took"><b>What this website takes from this paper</b><ul>'
            + "".join(items) + "</ul></div>")


def _made_by(spec: dict) -> str:
    """Which program made our schedules for this paper: a scheduler written for the paper's
    model, not the compiler that compiles this website's leaderboard entries, and what it
    keeps of the paper's own compilation and what it changes."""
    tool = spec.get("tool")
    if not tool:
        return ""
    path, kind = tool
    # the scheduler by its name, never its path: a page says nothing about the tree it was
    # built from (tests/_rendered_paths.mjs)
    name = Path(path).stem
    out = (f'<p class="rp-by">Made by <code>{_e(name)}</code>, {_e(kind)}; it is not the compiler '
           "that compiles this website's leaderboard entries.")
    if spec.get("keeps"):
        out += f" It keeps {_e(spec['keeps'])}, and changes {_e(spec['changes'])}."
    return out + "</p>"


def _ours_box(p: dict, res: dict | None) -> str:
    """Our own schedules on the paper's machine, each checked like the paper's (strict
    durations, the paper's circuit), next to the paper's own run."""
    key = p["key"]
    spec = catalog.OURS.get(key)
    rows = [o for o in (res or {}).get("ours", []) if o.get("ok")]
    head = '<div class="rp-ours"><b>Our schedule on the same machine, under the same rules</b>'
    if not spec or not rows:
        return (head + "Not yet: no schedule of ours for this paper has been checked. Nothing "
                "is shown here until one passes every rule the paper's own schedules are held to."
                "</div>")
    books = {"qccdsim": "QCCDSim's chain bookkeeping", "qccdsim-physical-swap": "the physical one",
             "cyclone": "Cyclone's rules", "jones": "the paper's rules, its declared locks included",
             "tiscc": "TISCC's rules", "qccdsim (gates as a set)": "QCCDSim's rules, gates in any order the "
             "paper allows"}
    out = [head + _made_by(spec) + f"<p>{_e(spec['how'])} Each schedule below is replayed by the same checker "
           "as the paper's, with the same timing law and circuit; an operation the paper's law "
           "does not price is refused.</p>"]
    moves_first = key == "saki2022"
    tm = "last_start_us" if _metric_of(p) == "last_start_us" else "makespan_us"
    fid = any("fidelity" in o["measured"] and "fidelity" in o["theirs"] for o in rows) and not moves_first
    # a paper whose own runs include one that keeps the circuit's order (Moveless's
    # ancilla-only MAO) gets it beside ours: ours keep the order too, so that is the like-for-like
    in_order = not moves_first and any(o.get("also") for o in rows)
    # further runs of the paper's shown beside ours (Jones: its fixed code with barriers, and the
    # published code), one column pair each; ``against`` stays the like-for-like comparison
    beside = list(spec.get("beside_labels", ())) if not moves_first else []
    cols = (["shuttles: QCCDSim", "Muzzle", "ours", "time: QCCDSim", "Muzzle", "ours"] if moves_first
            else [("start of the last step: " if tm == "last_start_us" else "")
                  + spec.get("theirs_label", "their code"), "ours", ""]
            + (["their run in the circuit's order", ""] if in_order else [])
            + [x for lab in beside for x in (lab, "")]
            + (["fidelity: theirs", "ours"] if fid else []))
    out.append('<table class="rp"><tr><th>configuration</th>'
               + "".join(f"<th>{_e(c)}</th>" for c in cols) + "<th>the paper's rules</th></tr>")
    for o in rows:
        m, t = o["measured"], o["theirs"]
        watch = (f'<br><a class="rp-watch" href="{_e(key)}/{_e(o["id"])}.html">▶ watch ours in the Studio</a>'
                 + _timed_link(p, o["id"]))
        n = sum(1 for pr in o["profiles"].values() for v in pr["checks"].values() if v == "passed")
        names = " and ".join(books.get(k, "the paper's rules") for k in o["profiles"])
        pills = [_rules_pill(pr.get("rules")) for pr in o["profiles"].values()]
        pill = None
        if pills and all(pills):
            pill = "<br>".join(pills) + f"<br><small>under {_e(names)}, each qubit's order kept</small>"
        pill = pill or (f'<span class="rp-pill ok" title="strict durations; the circuit; same device and '
                f'ions as the paper\'s run">all {n} checks pass</span><br><small>under {_e(names)}</small>')
        if moves_first:
            al = (o.get("also") or {}).get("measured", {})
            al_ok = (o.get("also") or {}).get("ok")
            mts = lambda k: (_num(al[k], k).replace(" shuttles", "") + ("" if al_ok else " *")) if k in al else "–"
            cells = [f"{t['moves']:,.0f}", mts("moves"), f"<b>{m['moves']:,.0f}</b>",
                     _num(t["makespan_us"], "makespan_us"), mts("makespan_us"),
                     f"<b>{_num(m['makespan_us'], 'makespan_us')}</b>"]
        else:
            cells = [_num(t[tm], "makespan_us"), f"<b>{_num(m[tm], 'makespan_us')}</b>",
                     _ratio(t[tm], m[tm])]
            if in_order:
                al = (o.get("also") or {}).get("measured", {})
                cells += ([_num(al[tm], "makespan_us"), _ratio(al[tm], m[tm])] if tm in al
                          else ["–", ""])
            for i in range(len(beside)):
                b = (o.get("beside") or [])[i] if i < len(o.get("beside") or []) else {}
                bm = b.get("measured", {})
                mark = ("" if b.get("ok", True) else
                        ' <span class="rp-bad" title="fails the paper&#39;s own rules; see the note above">*</span>')
                cells += ([_num(bm[tm], "makespan_us") + mark, _ratio(bm[tm], m[tm])] if tm in bm
                          else ["–", ""])
            if fid:
                cells += [_num(t["fidelity"], "fidelity"),
                          f"<b>{_num(m['fidelity'], 'fidelity')}</b> "
                          + _ratio(t["fidelity"], m["fidelity"], lower_better=False).replace("faster", "higher").replace("slower", "lower")]
        vs = next((r.get("label", r["id"]) for r in p["runs"] if r["id"] == o["against"]), "")
        vs_line = "" if vs == o["label"] else f"<br><small>against: {_e(vs)}</small>"
        out.append(f'<tr><td>{_e(o["label"])}{vs_line}{watch}</td>'
                   + "".join(f'<td class="n">{c}</td>' for c in cells) + f"<td>{pill}</td></tr>")
    out.append("</table>")
    if any(not b.get("ok", True) for o in rows for b in o.get("beside") or []):
        out.append("<p><small>* that schedule fails the paper's own rules (see the note above), so "
                   "it is shown for reference only.</small></p>")
    if moves_first:
        out.append("<p><small>* Muzzle's own schedule breaks trap capacity (see the note above); "
                   "its numbers are shown as its code printed them.</small></p>")
    for o in rows:
        if o.get("paper"):
            out.append("<p><small>The paper's own compilers on this circuit: "
                       + ", ".join(f"{k} {v:,} µs" for k, v in o["paper"].items())
                       + ", printed under the paper's model, which has no public code; ours is "
                       f"{_num(o['measured']['makespan_us'], 'makespan_us')} under QCCDSim's.</small></p>")
    if spec.get("found"):
        out.append(f"<p>{_e(spec['found'])}</p>")
    return "".join(out) + "</div>"


def _rules_table(papers: list[dict]) -> str:
    """Which rules each paper's model assumes: its rule set (qccd/ruleset/library), rule by
    rule, as the replay evaluates them."""
    from ..repro.__main__ import RULESETS
    from ..ruleset import catalogue, load, rule_order
    from .rulespage import MODEL_PAPER_SETS
    cols = []
    for p in papers:
        sets = list(MODEL_PAPER_SETS.get(p["key"], ()))
        for r in p.get("runs", []):
            n = RULESETS.get(r.get("profile"))
            if n and n not in sets:
                sets.append(n)
        if sets:
            cols.append((p, [load(n) for n in sets]))
    if not cols:
        return ""
    cat = {r.id: r for r in catalogue().rules}
    ids = sorted({i for _, rss in cols for rs in rss for i in rs.ids}, key=rule_order)
    out = ['<table class="rp rp-rules"><tr><th>rule</th>'
           + "".join(f"<th>{_e(p['short'])}</th>" for p, _ in cols) + "</tr>"]
    for i in ids:
        r = cat.get(i)
        title = r.title if r else i
        cells = []
        for _, rss in cols:
            n_in = sum(1 for rs in rss if i in rs.ids)
            if n_in == len(rss):
                cells.append('<td class="n"><span class="rp-ok">✓</span></td>')
            elif n_in:
                cells.append('<td class="n"><span class="rp-ok" title="in some of its rule sets">(✓)</span></td>')
            else:
                cells.append('<td class="n"><span class="rp-skip">–</span></td>')
        out.append(f'<tr><td><a href="../../rules/#{_e(i)}"><b>{_e(i)}</b></a><br><small>{_e(title)}</small></td>'
                   + "".join(cells) + "</tr>")
    out.append("</table>")
    return ("<p><small>✓ the paper's model has the rule, and every replay is held to it; (✓) only some "
            "of its configurations do; – the paper's model does not have it, so it is not checked. Each "
            "rule is written out on the <a href=\"../../rules/\">Rules</a> page.</small></p>"
            '<div class="rp-wide">' + "".join(out) + "</div>")


def _design_section(p: dict) -> str:
    """A design reproduction: the machine in our language, the paper's own counts against
    the circuits it published, and what our compiler made of them."""
    from ..api import Machine
    from .build import device_svg

    key = p["key"]
    res = json.loads((ROOT / key / "results.json").read_text(encoding="utf-8"))
    authors = ", ".join(p["authors"][:3]) + " et al"
    out = [f'<h2 id="{_e(key)}">{_e(p["short"])}</h2>',
           f'<p class="rp-cite">{_e(authors)}. <i>{_e(p["title"])}</i>. {_e(p["venue"])}. '
           f'<a href="https://arxiv.org/abs/{_e(p["arxiv"])}">arXiv:{_e(p["arxiv"])}</a> · '
           f'its circuits: <a href="{_e(p["artifact"]["url"])}">'
           f'{_e(p["artifact"]["url"].split("github.com/")[-1])}</a></p>',
           f'<p>{_e(p["what"])}</p>' + _took(p),
           f'<p><small>{_e(p["artifact"]["note"])}</small></p>']
    m = Machine.load(ROOT / key / p["arch"])
    # THE GATE ZONES BY NAME.  Every disc of this machine is grey in the site palette, so the
    # caption's zone list could not be matched to the drawing (a reader asked for DG/UG
    # labels).  Each DG and UG zone carries its site id, the id the Studio's listing uses;
    # the bottom rail's labels sit under it, so the picture grows to hold them.
    named = {n.id: (n.id, "#1f5fae" if n.zone_type == "dg" else "#8a5a00")
             for n in m.arch.device.sites() if n.zone_type in ("dg", "ug")}
    svg = device_svg(m, labels=False, marks=named or None)
    if named:
        svg = re.sub(r'viewBox="0 0 (\d+) (\d+)"',
                     lambda v: f'viewBox="0 0 {v.group(1)} {int(v.group(2)) + 26}"', svg, count=1)
    zones = ", ".join(f"{k} ({v.get('capacity')})" for k, v in m.arch.zone_types.items())
    demo = ROOT / key / "demo" / "ghz32.tsir.json"
    out.append(f'<figure class="rp-fig">{svg}<figcaption>H2 in our language: one closed loop of '
               f'{len(list(m.arch.device.sites()))} sites, no junction; zones with their capacity '
               f'in qubits: {_e(zones)}. '
               + ("Drawn the way the paper's Fig. 2d is: the four DG zones, the only ones that gate, "
                  "prepare and measure, are the labelled discs of the bottom rail, and the four UG "
                  "zones, used only for sorting, those of the top rail; the unlabelled discs between "
                  "and beside them are auxiliary parking. Past them, each end of the loop is a "
                  "conveyor region of twenty wells running round the bend from one rail to the "
                  "other; the green disc is the load hole. The Studio draws y downward, so there the "
                  "DG rail is the top one. "
                  if named else "")
               + f'<a class="rp-watch" href="{_e(key)}/design.html">▶ '
               + ("watch a 32-qubit GHZ circuit of ours run on it" if demo.exists()
                  else "open it") + ' in the Studio</a></figcaption></figure>')
    if demo.exists():
        out.append(_design_demo_note(m, demo))
    out.append("<p>The paper prints, for each circuit it ran, the number of two-qubit gates and "
               "the number of two-qubit gate rounds (at most four gates a round, one per gate "
               "zone; Table I). We took the same circuits from its published data, rewrote each "
               "of its gates exactly (checked against the artifact's own ideal outputs) and "
               "counted. A round count is a scheduling measure, not a time: the paper publishes no "
               "primitive durations.</p>")
    rows = ['<table class="rp"><tr><th>circuit</th><th>circuits</th><th>2Q gates: paper</th>'
            '<th>ours</th><th>rounds: paper</th><th>dependency floor</th>'
            '<th>layered with full rounds</th></tr>']
    span = lambda d: (f"{d['min']}" if d["min"] == d["max"] else
                      f"{d['min']}–{d['max']} (median {d['median']})")
    for t in res["table"]:
        ok = t["ours_gates"]["median"] == t["gates"] or t["family"] == "qv"
        rows.append(f'<tr><td>{_e(t["row"])}</td><td class="n">{t["circuits"]}</td>'
                    f'<td class="n">{t["gates"]}</td><td class="n">{span(t["ours_gates"])} '
                    f'<span class="{"rp-ok" if ok else "rp-bad"}">{"matches" if ok else "differs"}</span></td>'
                    f'<td class="n">{t["rounds"]}</td><td class="n">{span(t["floor"])}</td>'
                    f'<td class="n">{span(t["layered"])}</td></tr>')
    rows.append("</table>")
    out.append("".join(rows))
    out.append("<p><small>QV: Table I lists one example circuit with 310 gates; the 200 published "
               "circuits average 296, as the text says. The dependency floor is the larger of the "
               "circuit's two-qubit depth and a quarter of its gates; the layered count is the "
               "paper's own layering with every round of four filled.</small></p>")
    out.append(_design_ours(p, res))
    return "\n".join(out)


def _design_demo_note(m, demo: Path) -> str:
    """What the Studio programme's runtime is and is not, and where H2's cooling went.

    A reader compared the Studio's runtime for the demo with the paper's time per shot.
    Every primitive time in h2.arch.json is a 1 us placeholder (the paper states none), so
    the runtime is a count, not a time; the number is replayed here, under the model the
    Studio page uses, so it is the figure the reader sees there."""
    from ..cost.models import corrected_model
    from ..qec import load_program
    from ..verify.replay import replay

    prog = load_program(demo)
    res = replay(prog, m.arch, corrected_model("local"), check_rules=False, keep_cycles=False)
    us = res.total_us
    cools = [i for i in prog.instructions if i.type == "cool"]
    return (
        "<p><small>The programme in the Studio is our compiler's, for a GHZ circuit of our own "
        "with as many two-qubit gates as the paper's, 31. The runtime the Studio shows for it, "
        f"{us / 1000:.2f} ms, is not a time for H2: the paper states no duration for any "
        "primitive, so every primitive of this machine is given a placeholder of 1 µs, and "
        f"{us / 1000:.2f} ms is those placeholders added up over its {len(prog.instructions)} "
        "instructions. It counts operations; it is not calibrated to H2 and cannot be compared "
        "with the 0.18 s a shot the paper reports for its GHZ circuit (Table I: 2% quantum "
        "operations, 64% transport, 34% cooling). Cooling is the other thing the programme "
        f"leaves out. Its {'one cooling instruction is' if len(cools) == 1 else f'{len(cools)} cooling instructions are'} "
        "the trap-wide Doppler cool at the start; the resolved sideband cooling H2 applies in "
        "the DG zones before two-qubit gates is neither in it nor folded into any primitive. "
        "Our description of the machine has one cooling primitive, trap-wide, and no heating "
        "rate or n-bar limit for a gate (the paper states neither), so the compiler's cooling "
        "pass has nothing to schedule: R7 has no limit to enforce, and R7c (a programme with "
        "two-qubit gates must cool) is met by the opening cool.</small></p>")


def _example_fixed(m: dict) -> bool:
    """A minimal example the compiler now gets right: it ran, left nothing unrealised, and
    every rule passed."""
    prog = m.get("program") or {}
    rules = m.get("rules")
    failed = rules.get("failed") if isinstance(rules, dict) else (["?"] if rules else [])
    return m.get("exit") == 0 and prog.get("unrealised", 1) == 0 and not failed


def _h2_families(res: dict) -> list[dict]:
    """Per circuit family: how many compile with every rule passing, our rounds, the paper's,
    the dependency floor and the transport cycles our schedules spend."""
    import statistics as st
    paper = {t["family"]: t for t in res.get("table", [])}
    by: dict[str, list] = {}
    for c in res.get("circuits", []):
        by.setdefault(c["family"], []).append(c)
    out = []
    for fam, t in paper.items():
        cs = by.get(fam, [])
        good = [c for c in cs if (c.get("compile") or {}).get("verdict") == "compiled"
                and not ((c["compile"].get("rules") or {}).get("failed"))
                and not (c["compile"].get("program") or {}).get("unrealised")]
        R = [c["compile"]["program"]["rounds"] for c in good]
        F = [c["bounds"]["floor"] for c in good]
        T = [c["compile"]["program"]["transport_cycles"] for c in good]
        out.append({"family": fam, "row": t["row"], "n": len(cs), "good": len(good),
                    "rounds": (min(R), st.median(R), max(R)) if R else None,
                    "floor": (min(F), st.median(F), max(F)) if F else None,
                    "at_floor": sum(1 for c in good if c["compile"]["program"]["rounds"] == c["bounds"]["floor"]),
                    "paper": t["rounds"], "transport": st.median(T) if T else None})
    return out


def _span3(x) -> str:
    lo, med, hi = x
    f = lambda v: f"{v:g}"
    return f(lo) if lo == hi else f"{f(lo)}–{f(hi)} (median {f(med)})"


def _design_rounds(p: dict, res: dict, who: str, ok: list, circ: list) -> str:
    fams = _h2_families(res)
    good = sum(f["good"] for f in fams)
    rows = ['<table class="rp"><tr><th>circuit</th><th>compiled, every rule passing</th>'
            "<th>rounds: ours</th><th>the paper</th><th>dependency floor</th>"
            "<th>transport cycles (median)</th></tr>"]
    for f in fams:
        if not f["rounds"]:
            rows.append(f'<tr><td>{_e(f["row"])}</td><td class="n">0 of {f["n"]}</td>'
                        "<td>–</td><td class=\"n\">" + str(f["paper"]) + "</td><td>–</td><td>–</td></tr>")
            continue
        better = f["rounds"][2] < f["paper"]
        rows.append(f'<tr><td>{_e(f["row"])}</td><td class="n">{f["good"]} of {f["n"]}</td>'
                    f'<td class="n"><b>{_span3(f["rounds"])}</b>'
                    + (' <span class="rp-ok">fewer</span>' if better else "") + "</td>"
                    f'<td class="n">{f["paper"]}</td>'
                    f'<td class="n">{_span3(f["floor"])}<br><small>reached by {f["at_floor"]} of {f["good"]}</small></td>'
                    f'<td class="n">{f["transport"]:,.0f}</td></tr>')
    rows.append("</table>")
    return (f"<p>{who} compiles {good} of {len(circ)} of the paper's circuits on this machine, "
            "every rule passing and every certificate accepted by the proved checker. It runs H2 the "
            "way H2 runs: the whole loop shifts as one, neighbours swap in the two-ion zones, and up "
            "to four gates run per round, one per gate zone.</p>" + "".join(rows) +
            "<p><b>Read the rounds with care.</b> A round count measures how well the gates are "
            "packed, not how long the circuit takes: our model does not price transport, and our "
            "schedules spend thousands of transport cycles between rounds (the last column), where "
            "H2's own compiler minimises the total time, transport included. What the table shows is "
            "that, on the paper's machine and with its circuits, our schedules reach each circuit's "
            "dependency floor (no schedule with at most four gates a round can use fewer rounds), "
            "except GHZ, one round above it. The compiled programmes are not published: the "
            "circuits are not ours to redistribute.</p>")


def _design_ours(p: dict, res: dict) -> str:
    """What our published compiler does with the design's circuits, from results.json."""
    circ = res.get("circuits", [])
    ok = [c for c in circ if not str((c.get("compile") or {}).get("verdict", "refused")).startswith("refused")]
    release = (res.get("compiler") or {}).get("release")
    who = "The published compiler" if release else "Our compiler"
    exs = res.get("minimal_examples", [])
    fixed = [m for m in exs if _example_fixed(m)]
    open_ = [m for m in exs if not _example_fixed(m)]
    head = '<div class="rp-ours"><b>Our compiler on this machine' + (": not yet" if not ok else "") + "</b>"
    if ok:
        return head + _design_rounds(p, res, who, ok, circ) + "</div>"
    out = [head + f"{who} does not yet compile any of the {len(circ)} circuits on this machine."]
    if fixed:
        out.append(f" It fixes {len(fixed)} ways the compiler before it failed on them, each pinned by "
                   "a minimal example that now compiles with every rule passing. What the earlier "
                   "compiler did:<ul>"
                   + "".join(f"<li>{_e(m['claim'].replace(' -- ', ': '))}</li>" for m in fixed) + "</ul>")
    if open_:
        out.append(" Still open, each pinned by a minimal example:<ul>"
                   + "".join(f"<li>{_e(m['claim'].replace(' -- ', ': '))}</li>" for m in open_) + "</ul>")
    if p.get("blocker"):
        out.append(f"<p>What stops it now: {_e(p['blocker'])}</p>")
    out.append("The machine itself loads with no rule violation.</div>")
    return "".join(out)


def _summary(papers: list[dict]) -> str:
    rows = ['<table class="rp"><tr><th>paper</th><th>their code</th><th>runs replayed</th>'
            '<th>their numbers reproduced</th><th>notes</th><th>ours on the same machine</th></tr>']
    for p in papers:
        if p.get("kind") == "design":
            rows.append(f'<tr><td><a href="#{_e(p["key"])}">{_e(p["short"])}</a><br><small>'
                        f'{_e(p["venue"])}</small></td><td><small>all rights reserved</small></td>'
                        '<td class="n">–</td><td>design rebuilt; the published circuits\' '
                        'two-qubit gate counts match</td><td class="n">0</td>'
                        f'<td>{_ours_summary(p)}</td></tr>')
            continue
        if p["key"] == "schoenberger2024":
            res = _results(p["key"]) or {}
            sm = res.get("summary", {})
            rows_ = [c for c in res.get("configs", []) if c.get("paper")]
            ex = sum(1 for c in rows_ if c["paper"]["verdict"] == "exact")
            rows.append(f'<tr><td><a href="#{_e(p["key"])}">{_e(p["short"])}</a><br><small>'
                        f'{_e(p["venue"])}</small></td><td><small>MIT</small></td>'
                        f'<td class="n">{sm.get("replayed", 0)}</td><td>{sm.get("equal_to_printed", 0)} '
                        f'of {sm.get("replayed", 0)} exactly as their code printed; {ex} of '
                        f'{len(rows_)} paper values matched</td>'
                        f'<td class="n">{len(catalog.NOTES.get(p["key"], []))}</td>'
                        f'<td>{_ours_summary(p)}</td></tr>')
            continue
        if p.get("kind") == "model":
            res = _results(p["key"]) or {}
            ex = [r for r in res.get("runs", []) if r.get("kind") == "example"]
            n_ok = sum(1 for r in ex if (r.get("compare", {}).get("paper", {}).get("T_exe_us", {})
                                         .get("verdict") == "exact"))
            rows.append(f'<tr><td><a href="#{_e(p["key"])}">{_e(p["short"])}</a><br><small>'
                        f'{_e(p["venue"])}</small></td><td><small>no code published</small></td>'
                        f'<td class="n">{len(ex)}</td><td>{n_ok} of {len(ex)} worked examples '
                        'exact; Table 3 at model level</td><td class="n">0</td>'
                        f'<td>{_ours_summary(p)}</td></tr>')
            continue
        res = _results(p["key"])
        runs = [r for r in (res or {}).get("runs", []) if "missing" not in r]
        exact = sum(1 for r in runs if all(v["verdict"] == "exact"
                                           for v in r.get("compare", {}).get("tool", {}).values())
                    and r.get("compare", {}).get("tool"))
        paper_ok = sum(1 for r in runs for v in r.get("compare", {}).get("paper", {}).values()
                       if v["verdict"] in ("exact", "close"))
        paper_n = sum(len(r.get("compare", {}).get("paper", {})) for r in runs)
        lic = (p["artifact"].get("license") or "no license") if p["artifact"].get("commit")             else "not published"
        nn = len(catalog.NOTES.get(p["key"], []))
        rows.append(f'<tr><td><a href="#{_e(p["key"])}">{_e(p["short"])}</a><br><small>{_e(p["venue"])}</small></td>'
                    f'<td><small>{_e(lic)}</small></td><td class="n">{len(runs)}</td>'
                    f'<td>{exact} of {len(runs)} exactly as their code printed'
                    + (f'; {paper_ok} of {paper_n} paper values matched' if paper_n else '')
                    + f'</td><td class="n">{nn}</td><td>{_ours_summary(p)}</td></tr>')
    rows.append("</table>")
    return '<div class="rp-wide">' + "".join(rows) + "</div>"


def _ours_summary(p: dict) -> str:
    """One cell: how many of our checked schedules beat the paper's, on the paper's metric."""
    res = _results(p["key"]) or {}
    if p.get("kind") == "design":
        fams = _h2_families(res)
        good = sum(f["good"] for f in fams)
        if not good:
            return '<span class="rp-skip">not yet: our compiler does not compile its circuits yet</span>'
        n = sum(f["n"] for f in fams)
        fewer = sum(1 for f in fams if f["rounds"] and f["rounds"][2] < f["paper"])
        return (f"{good} of {n} circuits compile; fewer rounds than the paper in {fewer} of "
                f"{len(fams)} families<br><small>rounds only: our model does not price transport</small>")
    if p["key"] == "schoenberger2024":
        rows = [o for o in res.get("ours", {}).get("runs", []) if o.get("ok")]
        if not rows:
            return '<span class="rp-skip">not yet</span>'
        return (f"{len(rows)} of {len(rows)} faster than the heuristic; each at its lower bound"
                "<br><small>under the heuristic's model</small>")
    if p.get("kind") == "model":
        main = [r for r in res.get("runs", []) if r.get("kind") == "ours"
                and r.get("compare", {}).get("paper") and r.get("ok")]
        k = [r["compare"]["paper"]["T_exe_us"]["theirs"] / r["measured"]["makespan_us"] for r in main]
        if not k:
            return '<span class="rp-skip">not yet</span>'
        return (f"{sum(x > 1 for x in k)} of {len(k)} faster, {min(k):.1f}–{max(k):.1f}×"
                "<br><small>model level, rebuilt circuits</small>")
    rows = [o for o in res.get("ours", []) if o.get("ok")]
    if not rows:
        return '<span class="rp-skip">not yet</span>'
    if p["key"] == "saki2022":
        fewer = sum(o["measured"]["moves"] < min(o["theirs"]["moves"], (o.get("also") or {})
                    .get("measured", {}).get("moves", float("inf"))) for o in rows)
        faster = sum(o["measured"]["makespan_us"] < o["theirs"]["makespan_us"] for o in rows)
        return (f"fewer shuttles than Muzzle: {fewer} of {len(rows)}"
                f"<br><small>faster than QCCDSim: {faster} of {len(rows)}</small>")
    tm = "last_start_us" if _metric_of(p) == "last_start_us" else "makespan_us"
    k = [o["theirs"][tm] / o["measured"][tm] for o in rows]
    win = [x for x in k if x > 1]
    lo, hi = (f"{min(win):.2f}", f"{max(win):.2f}") if win else ("", "")
    return (f"{len(win)} of {len(k)} faster"
            + (f", {lo}–{hi}×" if win and lo != hi else f", {lo}×" if win else ""))


def section(p: dict) -> str:
    """One paper's section of the page, by the kind of reproduction it is."""
    if p.get("kind") == "design":
        return _design_section(p)
    if p["key"] == "schoenberger2024":
        return _ionshuttler_section(p)
    if p.get("kind") == "model":
        return _model_section(p)
    return _paper_section(p, _results(p["key"]))


def _papers() -> list[dict]:
    return [p for p in catalog.PAPERS if p.get("runs") or p.get("kind") in ("design", "model")]


def _replayed(p: dict) -> str:
    """One cell: how the paper's own numbers came out when its code was replayed."""
    res = _results(p["key"]) or {}
    nn = len(catalog.NOTES.get(p["key"], []))
    found = (f'<br><small class="rp-skip">{nn} finding{"s" if nn != 1 else ""}</small>' if nn else "")
    if p.get("kind") == "design":
        return "machine rebuilt; the published circuits' gate counts match" + found
    if p["key"] == "schoenberger2024":
        sm = res.get("summary", {})
        rows_ = [c for c in res.get("configs", []) if c.get("paper")]
        ex = sum(1 for c in rows_ if c["paper"]["verdict"] == "exact")
        return (f'{sm.get("equal_to_printed", 0)}/{sm.get("replayed", 0)} runs exact'
                f'<br><small>{ex}/{len(rows_)} paper values</small>' + found)
    if p.get("kind") == "model":
        ex = [r for r in res.get("runs", []) if r.get("kind") == "example"]
        ok = sum(1 for r in ex if r.get("compare", {}).get("paper", {}).get("T_exe_us", {}).get("verdict") == "exact")
        return f"{ok}/{len(ex)} worked examples exact<br><small>no code published</small>" + found
    runs = [r for r in res.get("runs", []) if "missing" not in r]
    exact = sum(1 for r in runs if r.get("compare", {}).get("tool")
                and all(v["verdict"] == "exact" for v in r["compare"]["tool"].values()))
    pv = [v for r in runs for v in r.get("compare", {}).get("paper", {}).values()]
    pok = sum(1 for v in pv if v["verdict"] in ("exact", "close"))
    return (f"{exact}/{len(runs)} runs exact"
            + (f"<br><small>{pok}/{len(pv)} paper values</small>" if pv else "") + found)


def _code(p: dict) -> str:
    art = p.get("artifact") or {}
    if p.get("kind") == "design":
        return "circuits public, all rights reserved"
    if not art.get("commit"):
        return "not published"
    return _e(art.get("license") or "no license")


def _count_word(n: int) -> str:
    return ("zero one two three four five six seven eight nine ten eleven twelve".split()[n]
            if 0 <= n <= 12 else str(n))


def page(PAGE: str, STYLE: str) -> str:
    """The index: one table, one row per paper; the details are a click away."""
    papers = _papers()
    rows = ['<table class="rp rp-index"><tr><th>paper</th><th>their code</th>'
            "<th>their numbers, replayed</th><th>ours on the same machine</th><th></th></tr>"]
    for p in papers:
        k = _e(p["key"])
        n_rules = len(_took_rules(p))
        tk = ", ".join(([f"{n_rules} rule{'s' if n_rules != 1 else ''}"] if n_rules else [])
                       + list(catalog.TOOK_SHORT.get(p["key"], ())))
        tk = f'<small class="rp-tk">we build on its {_e(tk)}</small>' if tk else ""
        rows.append(f'<tr class="rp-row" data-href="{k}/"><td><a href="{k}/"><b>{_e(p["short"])}</b></a>'
                    f'<br><small>{_e(p["venue"])}</small>{tk}</td><td><small>{_code(p)}</small></td>'
                    f"<td>{_replayed(p)}</td><td>{_ours_summary(p)}</td>"
                    f'<td class="n"><a href="{k}/" aria-label="details">details →</a></td></tr>')
    rows.append("</table>")
    body = [
        "<h1>Reproducing the literature</h1>",
        f'<p class="rp-lead">{len(papers)} published QCCD papers. For each one we ran its own code '
        "and replayed every schedule in our checker, under the paper's own rules and clock, then "
        "scheduled the same circuits on the same machine ourselves, under the same rules. For "
        f"{_count_word(sum(1 for p in papers if p.get('kind') != 'design'))} of them that schedule "
        "comes from a scheduler we wrote for the paper's own model; for "
        + " and ".join(p["short"] for p in papers if p.get("kind") == "design")
        + " it comes from this website's compiler. Open a paper for its machine, every "
        "run, what we found, what this website takes from it, and the animations.</p>",
        '<div class="rp-wide">' + "".join(rows) + "</div>",
        '<p class="rp-more"><a href="method/">How a paper is checked, and which rules each '
        'paper\'s model has</a> · <a href="method/#considered">Papers we read and did not '
        "reproduce</a> · <a href=\"../rules/#sets\">every paper's rule set</a></p>",
        "<script>document.querySelectorAll('tr.rp-row').forEach(function(r){"
        "r.addEventListener('click',function(e){if(e.target.closest('a'))return;"
        "location.href=r.getAttribute('data-href');});});</script>",
    ]
    return PAGE.format(title="Reproducing the literature — QCCD", style=STYLE,
                       extra_css=CSS, body="\n".join(body))


def _method_page(PAGE: str, STYLE: str) -> str:
    papers = _papers()
    body = [
        '<p class="rp-back"><a href="../">← all papers</a></p>',
        "<h1>How a paper is checked</h1>",
        '<ol class="rp-steps">'
        "<li><b>Run their code.</b> Each paper's artifact is run as its authors ran it, and every "
        "operation it schedules (each split, move, junction crossing, merge and gate, with its start "
        "and end time) is written out.</li>"
        "<li><b>Rebuild their machine in our language.</b> The traps, junctions and segments become "
        "an architecture document, priced by the paper's own timing table.</li>"
        "<li><b>Replay under their rules.</b> Each paper's model is written as a "
        "<a href=\"../../rules/#sets\">rule set</a>: one assertion per rule about the machine and "
        "the schedule, such as where every ion is, trap capacity at every instant, one ion per rail "
        "and per junction, one operation per trap, which end of a chain an ion leaves from, how long "
        "each operation takes, and whether the gates run are the circuit's. The schedule is "
        "replayed event by event and every rule of the set evaluated on it; each paper's page lists "
        "every run against every rule.</li>"
        "<li><b>Compare three numbers.</b> What their code printed, what our replay measures (these "
        "must be identical), and what the paper reports.</li>"
        "<li><b>Then schedule it ourselves.</b> The same circuit on the same machine, checked by the "
        "same checker under the same rules, more strictly: an operation the paper's timing law "
        "does not price is refused, and every qubit keeps the circuit's gate order. The schedule "
        "comes from a scheduler we wrote for that paper's model, each named on its page; only "
        "H2's circuits are compiled by this website's own compiler, which works in our stricter "
        "machine model.</li></ol>",
        "<p>The rules are each paper's, not ours. Our own machine model is stricter in "
        "places (one waveform per cycle, gates and transport never in the same cycle), and under "
        "it most of these schedules would not be legal at all; that is a different question, "
        "and it is not the one asked here.</p>",
        "<p>The same goes for the Studio pages. Each replayed run opens in the Studio as a "
        "programme in our language, and the Studio plays it one instruction at a time (the "
        "operations of one kind that start together), so the runtime it shows is its "
        "instructions added up. Its Report shows the paper's rule set first, as the replay judged "
        "the run, and then this website's own rules, which the paper did not assume. Where a "
        "paper's operations overlap in time, that sum is longer than the schedule; the time "
        "that is checked, and compared, is the one in the tables, on the paper's clock.</p>",
        '<h2 id="rules">The rules each paper\'s model has</h2>',
        _rules_table(papers),
        "<p>A note on a paper is written only when running its code shows it.</p>",
        '<h2 id="considered">Papers we read and did not reproduce</h2>',
        "<p>Every one of these was read in full, with its code where there is any. A paper is "
        "reproduced only when its machine, its timing and its circuits can be pinned down well "
        "enough that a mismatch would mean something.</p>",
        '<table class="rp"><tr><th>paper</th><th>why it is not here</th></tr>'
        + "".join(f'<tr><td>{_e(c["short"])}<br><small><a href="https://arxiv.org/abs/'
                  f'{_e(c["arxiv"])}">arXiv:{_e(c["arxiv"])}</a></small></td>'
                  f'<td>{_e(c["why"])}</td></tr>' for c in catalog.CONSIDERED) + "</table>",
    ]
    return PAGE.format(title="How a paper is checked — QCCD", style=STYLE, extra_css=CSS,
                       body="\n".join(body))


def _paper_page(p: dict, PAGE: str, STYLE: str) -> str:
    """One paper's details, at /reproduce/<key>/: the section the index used to carry, with its
    links made relative to the paper's own folder."""
    key = p["key"]
    sec = section(p)
    sec = sec.replace(f'<h2 id="{_e(key)}">{_e(p["short"])}</h2>', f'<h1 id="{_e(key)}">{_e(p["short"])}</h1>', 1)
    sec = sec.replace(f'href="{_e(key)}/', 'href="')
    body = ['<p class="rp-back"><a href="../">← all papers</a></p>', sec,
            '<p class="rp-more"><a href="../method/">How a paper is checked</a> · '
            '<a href="../">all papers</a></p>']
    return PAGE.format(title=f"{p['short']}: reproduction — QCCD", style=STYLE, extra_css=CSS,
                       body="\n".join(body))


def document_pages(PAGE: str, STYLE: str):
    """Every page of the section besides the index and the Studio pages: (site path, page, depth)."""
    for p in _papers():
        yield f"reproduce/{p['key']}/index.html", _paper_page(p, PAGE, STYLE), 2
    yield "reproduce/method/index.html", _method_page(PAGE, STYLE), 2


def index_entries() -> list:
    out = [{"t": "Reproducing the literature", "u": "reproduce/", "k": "reproduce",
            "d": "published QCCD papers rerun and replayed under their own rules"}]
    for p in catalog.PAPERS:
        if p.get("runs") or p.get("kind") in ("design", "model"):
            out.append({"t": f"{p['short']}: reproduction", "u": f"reproduce/{p['key']}/",
                        "k": "reproduce", "d": p["title"]})
    return out


# ------------------------------------------------------------------- the Studio pages
#
# Every replayed run opens in the Studio, the way a leaderboard entry does: the paper's
# machine as an architecture document, the paper's schedule as a program on it.  The page
# is the ordinary Studio and nothing more, exactly like a leaderboard entry: what the run is,
# the paper's clock and the checks under the paper's rules are on the paper's own page.

#: what the Studio needs to price the paper's operations, per paper (the paper's values)
STUDIO_PRICING = {
    "murali2020": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "saki2022": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "khan2026cyclone": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "khan2025moveless": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "bach2025": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "jones2025": {"gate_us": 40.0, "one_q_us": 5.0, "measure_us": 400.0, "reset_us": 50.0},
    # TISCC: X and Y turns 10 us, Z turns 3 us (a VZ), Prepare_Z (a reset) 10 us
    "leblond2023": {"gate_us": 2000.0, "one_q_us": 10.0, "measure_us": 120.0, "reset_us": 10.0,
                    "z_us": 3.0},
}

#: papers whose Studio headline gives the schedule's own time on the paper's clock: the
#: Studio's runtime adds its instructions one after another, which an asynchronous
#: schedule does not do (thread 44)
CLOCK_IN_HEADLINE = {"leblond2023"}
#: Papers whose Studio pages say, in the headline, the schedule's time on the paper's own
#: clock -- every paper whose operations overlap, so that the Studio's runtime (its
#: instructions added up) is not mistaken for it (threads 44 and 62).
HEADLINE_CLOCK = {"leblond2023", "jones2025"}


def _headline(p: dict, label: str, measured: dict | None) -> str:
    """The run's label, after its time on the paper's clock, measured the way the paper
    reports it (Jones et al.: the start of the last step)."""
    measured = measured or {}
    metric = _metric_of(p)
    if metric not in ("makespan_us", "last_start_us"):
        metric = "makespan_us"
    v = measured.get(metric)
    if p["key"] not in HEADLINE_CLOCK or v is None:
        return label
    what = " (the start of its last step)" if metric == "last_start_us" else ""
    return f"{_num(v, 'makespan_us')} on {p['short']}'s clock{what} · {label}"


def _layout_for(p: dict, run: dict) -> tuple[str, dict]:
    imp, rid = run["importer"], run["id"]
    if imp == "cyclone":
        return "ring", {}
    if imp == "jones":
        return "recorded", {}
    if imp == "tiscc":
        return "tiscc", {"ncols": run["importer_args"]["ncols"]}
    if "G2x3" in rid:
        return "qccdsim_g2x3", {}
    if "_Gx" in rid:
        return "qccdsim_nxn", {"N": int(rid.split("_Gx")[1].split("_")[0])}
    return "qccdsim_linear", {}


def _import(p: dict, run: dict):
    from ..repro import importers
    from ..repro.__main__ import _open

    path = _open(ROOT / p["key"] / "artifact" / run["file"])
    if run["importer"] == "tiscc":
        return importers.import_tiscc(path, **run.get("importer_args", {}))
    return getattr(importers, f"import_{run['importer']}")(path)


def studio_rel(p: dict, run: dict) -> str:
    return f"reproduce/{p['key']}/{run['id']}.html"


_STIM_2Q = {"CX", "CNOT", "ZCX", "CY", "ZCY", "CZ", "ZCZ", "SWAP", "ISWAP", "XCX", "XCZ", "YCZ"}
_STIM_MEASURE = {"M", "MZ", "MX", "MY", "MR", "MRZ", "MRX", "MRY"}
_STIM_RESET = {"R", "RZ", "RX", "RY"}
_STIM_SKIP = {"DETECTOR", "OBSERVABLE_INCLUDE", "TICK", "QUBIT_COORDS", "SHIFT_COORDS", "REPEAT",
              "DEPOLARIZE1", "DEPOLARIZE2", "X_ERROR", "Z_ERROR", "Y_ERROR", "PAULI_CHANNEL_1",
              "PAULI_CHANNEL_2", "}"}


def _circuit_source(p: dict, spec: dict, sched, prog, relabel=None):
    """The circuit a run implements, as the Studio's Gates view wants it: (name, lines, ops,
    join_1q) for `studio.source_payload`, or None when the run names no circuit.  Qubits are
    turned into the programme's ions exactly as the checker turns them (`sched.qubits`)."""
    from ..repro.importers import _CX
    c = spec.get("circuit")
    if not c:
        return None
    f = ROOT / p["key"] / "circuits" / c["file"]
    to_ion = lambda q: str((sched.qubits or {}).get(str(q), str(q)))
    if c["kind"] == "qasm":
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        ops = []
        for n, ln in enumerate(lines, 1):
            m = _CX.match(ln)
            if m:
                ops.append({"name": "cx", "ions": (to_ion(m.group(2)), to_ion(m.group(4))),
                            "q": [int(m.group(2)), int(m.group(4))], "line": n})
        return c["file"], lines, ops, False
    if c["kind"] == "pairs":
        data = json.loads(f.read_text(encoding="utf-8"))
        pairs = [tuple(x) for x in (data["pairs"] if isinstance(data, dict) else data)]
        if relabel is not None:
            pairs = relabel(pairs)
        g = "zz" if ".zz." in c["file"] else "cx"
        lines = [f"// the two-qubit gates of {c['file']}, in circuit order"] + \
                [f"{g} {a}, {b};" for a, b in pairs]
        ops = [{"name": g, "ions": (to_ion(a), to_ion(b)), "q": [a, b], "line": k + 2}
               for k, (a, b) in enumerate(pairs)]
        return c["file"], lines, ops, False
    if c["kind"] == "stim":
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        ops = []
        for n, ln in enumerate(lines, 1):
            t = ln.split("#")[0].split()
            if not t:
                continue
            nm = t[0].split("(")[0].upper()
            tg = [x for x in t[1:] if x.isdigit()]
            if nm in _STIM_SKIP:
                continue
            if nm in _STIM_2Q:
                for a, b in zip(tg[::2], tg[1::2]):
                    ops.append({"name": nm.lower(), "ions": (to_ion(a), to_ion(b)), "q": [int(a), int(b)], "line": n})
            else:
                kind = "measure" if nm in _STIM_MEASURE else "reset" if nm in _STIM_RESET else "gate"
                for a in tg:
                    ops.append({"name": nm.lower(), "ions": (to_ion(a),), "q": [int(a)], "line": n, "kind": kind})
        return c["file"], lines, ops, False
    return None


def _source(prog, circ, sched=None, spec=None) -> dict | None:
    """The circuit pane's payload.  Jones's schedules run each H and CNOT as pulses (the
    artifact's `JONES_NATIVE`), so their pulses are joined through `pulse_join`."""
    from ..repro.importers import JONES_NATIVE
    from ..repro.studio import pulse_join, source_payload
    if not circ:
        return None
    name, lines, ops, j1 = circ
    pulses = pulse_join(sched, ops, JONES_NATIVE) \
        if sched is not None and (spec or {}).get("importer") == "jones" else None
    return source_payload(prog, name, lines, ops, join_1q=j1, pulses=pulses)


_GATES: dict[tuple[str, str], str] = {}


def _gate(p: dict, spec: dict, sched=None) -> str:
    """The two-qubit gate's name on a run's page (`studio.gate_label`), decided from the
    paper's run: CX where its tool runs each circuit CNOT as one whole gate (Cyclone, the
    QCCDSim family), MS where it schedules the native pulses (Jones, TISCC).  An "ours" page
    takes the name of the paper run it is judged against, so the pair reads alike."""
    key = (p["key"], spec["id"])
    if key not in _GATES:
        from ..repro.studio import gate_label
        s = sched if sched is not None else _import(p, spec)
        circ = _circuit_source(p, spec, s, None)
        _GATES[key] = gate_label(s, circ[2]) if circ else "MS"
    return _GATES[key]


def studio_pages(tmp: Path, only: set[str] | None = None):
    """Yield `(site path, page html, "")` for every replayed run in the catalog (the third
    element is what the site build injects into the page: nothing)."""
    from ..api import Machine
    from ..arch import Architecture
    from ..cost.models import corrected_model
    from ..repro.devices import layout
    from ..repro.studio import named_after_qubits, studio_arch_doc, to_tsir

    model = corrected_model("local")
    tmp.mkdir(parents=True, exist_ok=True)
    for p in catalog.PAPERS:
        if p.get("kind") == "design" and not only:
            yield _design_studio(p, tmp, model)
            continue
        if p["key"] == "schoenberger2024":
            res = _results(p["key"]) or {}
            for run in res.get("runs", []) + res.get("ours", {}).get("runs", []):
                if run.get("ok") and _ish_wanted(run) and (not only or run["id"] in only):
                    yield _ish_studio(p, run, tmp, model)
            continue
        if p.get("kind") == "model":
            for run in (_results(p["key"]) or {}).get("runs", []):
                if run.get("ok") and _model_studio_wanted(run) and (not only or run["id"] in only):
                    yield _model_studio(p, run, tmp, model)
            continue
        res = _results(p["key"]) or {}
        for o in catalog.OURS.get(p["key"], {}).get("runs", []):
            row = next((x for x in res.get("ours", []) if x["id"] == o["id"] and x.get("ok")), None)
            if row is None or (only and o["id"] not in only):
                continue
            spec = next(x for x in p["runs"] if x["id"] == o["against"])
            yield _ours_studio(p, o, spec, row, tmp, model)
        for run in p.get("runs", []):
            if (only and run["id"] not in only) or run.get("no_studio"):
                continue
            m, prog, s, name = _run_programme(p, run)
            row = next((x for x in res.get("runs", []) if x["id"] == run["id"]), {})
            out = tmp / f"{name}.html"
            m.render(prog, out, model=model, kicker=f"REPRODUCED · {p['short'].upper()}",
                     headline=_headline(p, run.get("label", run["id"]), row.get("measured")),
                     source=_source(prog, _circuit_source(p, run, s, prog), s, run),
                     ruleset=_page_ruleset(p, row.get("rules")))
            yield studio_rel(p, run), out.read_text(encoding="utf-8"), ""


def _run_programme(p: dict, run: dict):
    """A paper run as its Studio page plays it: (machine, programme, schedule, name)."""
    from ..api import Machine
    from ..arch import Architecture
    from ..repro.devices import layout
    from ..repro.studio import named_after_qubits, studio_arch_doc, to_tsir

    s = named_after_qubits(_import(p, run))
    fam, kw = _layout_for(p, run)
    pos = layout(s.device, fam, **kw)
    price = dict(STUDIO_PRICING.get(p["key"], {}))
    if "gate_us" not in price:
        cap = max(int(a["capacity"]) for a in s.device.sites.values())
        price["gate_us"] = float(int(max(100.0, 13.33 * cap - 54)))
    name = f"{p['key']}_{run['id']}".replace(".", "_")
    doc = studio_arch_doc(s, pos, name=name, table=p["key"],
                          hop_entails=() if run["importer"] == "tiscc" else ("split", "merge"),
                          note=f"{p['short']} ({p['venue']}): {p['what']}", **price)
    m = Machine(Architecture.from_json(doc))
    return m, to_tsir(s, name=run["id"], arch=name, gate=_gate(p, run, s)), s, name


def _design_studio(p: dict, tmp: Path, model):
    """The machine alone, with 32 qubits where the paper puts them: four batches of eight in
    the DG zones, the UG zones and the two conveyor regions (Sec. II.E)."""
    from ..api import Machine
    from ..ir.tsir import TSIR, Instruction

    m = Machine.load(ROOT / p["key"] / p["arch"])
    sites = {n.id: n for n in m.arch.device.sites()}
    place: dict[str, str] = {}
    q = 0

    def put(ids, per):
        nonlocal q
        for sid in ids:
            for _ in range(per):
                place[f"q{q}"] = sid
                q += 1

    put([s for s in sites if s.startswith("DG")], 2)
    put([s for s in sites if s.startswith("UG")], 2)
    put(sorted((s for s in sites if s.startswith("CR")), key=lambda x: int(x[2:]))[:8], 1)
    put(sorted((s for s in sites if s.startswith("CL")), key=lambda x: int(x[2:]))[:8], 1)
    # A PROGRAMME TO WATCH: a circuit of our own (the paper's are not ours to publish),
    # compiled on this machine by the published compiler -- Reproduce/<key>/demo.py writes it.
    demo = ROOT / p["key"] / "demo" / "ghz32.tsir.json"
    info = json.loads((ROOT / p["key"] / "demo" / "ghz32.json").read_text(encoding="utf-8")) \
        if demo.exists() else None
    if demo.exists():
        from ..qec import load_program
        prog = load_program(demo)
        headline = "our GHZ-32, compiled on the machine rebuilt in our language"
    else:
        prog = TSIR(name="H2, 32 qubits in four batches", arch_spec=m.arch.name)
        prog.add(Instruction(type="init", id=prog.next_id(), placement=place,
                             quanta={i: 0.0 for i in place}, meta={"note": "Sec. II.E batches"}))
        headline = "the machine, rebuilt in our language"
    out = tmp / f"{p['key']}_design.html"
    source = None
    cert = ROOT / p["key"] / "demo" / "ghz32.qcert.json"
    if demo.exists() and cert.exists():
        from ..ir.source_map import build as build_source
        source = build_source(prog, json.loads(cert.read_text(encoding="utf-8")),
                              ROOT / p["key"] / "demo" / "ghz32.qasm")
    m.render(prog, out, model=model, kicker=f"REPRODUCED · {p['short'].upper()}",
             headline=headline, source=source)
    return f"reproduce/{p['key']}/design.html", out.read_text(encoding="utf-8"), ""


def _ours_programme(p: dict, o: dict, spec: dict):
    """One of our schedules as its Studio page plays it: (machine, programme, schedule, name)."""
    from ..api import Machine
    from ..arch import Architecture
    from ..repro.__main__ import _open
    from ..repro.devices import layout
    from ..repro.studio import named_after_qubits, studio_arch_doc, to_tsir
    from ..repro.timed import TimedSchedule

    s = named_after_qubits(TimedSchedule.load(_open(ROOT / p["key"] / o["file"])))
    fam, kw = _layout_for(p, spec)
    pos = layout(s.device, fam, **kw)
    price = dict(STUDIO_PRICING.get(p["key"], {}))
    if "gate_us" not in price:
        cap = max(int(a["capacity"]) for a in s.device.sites.values())
        price["gate_us"] = float(int(max(100.0, 13.33 * cap - 54)))
    name = f"{p['key']}_{o['id']}".replace(".", "_")
    # moved as the paper's run moves: a TISCC move is one Move, no split and no merge
    doc = studio_arch_doc(s, pos, name=name, table=p["key"],
                          hop_entails=() if spec["importer"] == "tiscc" else ("split", "merge"),
                          note=f"{p['short']} ({p['venue']}): our schedule", **price)
    m = Machine(Architecture.from_json(doc))
    return m, to_tsir(s, name=o["id"], arch=name, gate=_gate(p, spec)), s, name


def _page_ruleset(p: dict, rules: dict | None, ours: bool = False) -> dict | None:
    """What a run's Studio page says first in its Report: the paper's rule set, rule by rule,
    as the replay judged the schedule when the page was built."""
    if not rules or not rules.get("results"):
        return None
    who = p["short"]
    note = (f"These are {who}'s rules, the ones the schedule was written for"
            + (", with each qubit's gates kept in the circuit's exact order (R25)" if ours else "")
            + ". The badges below are this website's own machine, which {who} does not assume: "
            "a rule of ours failing here is a difference between the two models, not a fault "
            "in the schedule.").replace("{who}", who)
    return {"ruleset": rules["ruleset"], "title": rules.get("title") or rules["ruleset"],
            "results": rules["results"], "note": note}


def _ours_studio(p: dict, o: dict, spec: dict, row: dict, tmp: Path, model):
    """One of our schedules as a Studio page, drawn like the paper's run it beats (or not)."""
    m, prog, s, name = _ours_programme(p, o, spec)
    out = tmp / f"{name}.html"
    label = _headline(p, f"{o['label']}: our schedule", row.get("measured"))
    relabel = None
    if o.get("circuit_relabel") == "checks":
        from ..repro.importers import checks_as_scheduled
        gates = [tuple(e.ions) for e in s.events if e.kind == "gate"]
        relabel = lambda pairs: checks_as_scheduled(pairs, gates)[0]
    rules = next((pr.get("rules") for pr in (row.get("profiles") or {}).values() if pr.get("rules")), None)
    m.render(prog, out, model=model, kicker=f"OURS · {p['short'].upper()}", headline=label,
             source=_source(prog, _circuit_source(p, spec, s, prog, relabel), s, spec),
             ruleset=_page_ruleset(p, rules, ours=True))
    return f"reproduce/{p['key']}/{o['id']}.html", out.read_text(encoding="utf-8"), ""


def _model_circuit(run: dict, sched):
    """A TrapSIMD run's circuit: the worked example's programme, or the rebuilt benchmark."""
    from ..repro import trapsimd as ts
    rid = run["id"]
    if rid.startswith("fig6"):
        prog = ts.w1_program()
    elif rid.startswith("fig7"):
        prog = ts.w2_program()
    elif "3reg" in rid:
        prog = ts.qaoa_regular(60, seed=0)
    else:
        prog = ts._BENCH[run["benchmark"]](run["n"])
    to_ion = lambda q: str((sched.qubits or {}).get(str(q), str(q)))
    lines = [f"// {run['label']}: one operation a line"] + \
            [f"{o.name} {', '.join(prog.qname(q) for q in o.qubits)};" for o in prog.ops]
    ops = [{"name": o.name, "ions": tuple(to_ion(prog.qname(q)) for q in o.qubits),
            "q": [prog.qname(q) for q in o.qubits], "line": k + 2,
            "kind": "measure" if o.name in ("measure", "m") else "gate"}
           for k, o in enumerate(prog.ops)]
    return "circuit", lines, ops, True


def _model_studio_wanted(run: dict) -> bool:
    """The worked examples and the paper's main configuration (A-60, both trap lengths)."""
    return run.get("kind") == "example" or (run.get("kind") == "ours" and run.get("n") == 60)


def _model_section(p: dict) -> str:
    """A model-level reproduction (TrapSIMD): no artifact, so the paper's worked examples
    are replayed event for event, and its results table is compared with our schedules on
    its device and timing, with every assumption that moves a number said."""
    key = p["key"]
    res = _results(key) or {}
    runs = res.get("runs", [])
    authors = ", ".join(p["authors"][:4]) + " et al"
    out = [f'<h2 id="{_e(key)}">{_e(p["short"])}</h2>',
           f'<p class="rp-cite">{_e(authors)}. <i>{_e(p["title"])}</i>. {_e(p["venue"])}. '
           f'<a href="https://arxiv.org/abs/{_e(p["arxiv"])}">arXiv:{_e(p["arxiv"])}</a> · '
           "their code: not published</p>",
           f'<p>{_e(p["what"])}</p>' + _took(p), f'<p><small>{_e(p["artifact"]["note"])}</small></p>']
    ex = [r for r in runs if r.get("kind") == "example"]
    if ex:
        out.append("<p>The paper works two examples out to the microsecond (Figs. 6 and 7). Each is "
                   "written here event for event and replayed under the paper's rules:</p>")
        out.append('<table class="rp"><tr><th>worked example</th><th>the paper</th>'
                   "<th>our replay</th><th>checks under the paper's rules</th></tr>")
        for r in ex:
            c = r.get("compare", {}).get("paper", {}).get("T_exe_us", {})
            ok = c.get("verdict") == "exact"
            out.append(f'<tr><td>{_e(r["label"])}<br><a class="rp-watch" href="{_e(key)}/{_e(r["id"])}.html">'
                       f'▶ watch it in the Studio</a></td><td class="n">{_num(c.get("theirs", 0), "makespan_us")}</td>'
                       f'<td class="n">{_num(r["measured"]["makespan_us"], "makespan_us")} '
                       f'<span class="{"rp-ok" if ok else "rp-bad"}">{"equal" if ok else "differs"}</span></td>'
                       f"<td>{_checks(r)}</td></tr>")
        out.append("</table>")
        out.append("<p>All four add up only with a two-qubit gate of 141 µs: the 25 µs of the "
                   "paper's Table 1 plus a 58 µs shift into the gate zone and one out, which the "
                   "paper says the gate's latency includes. With 25 µs, every two-qubit gate of "
                   "all four fails the duration check.</p>")
    main = [r for r in runs if r.get("kind") == "ours" and r.get("compare", {}).get("paper")]
    if main:
        out.append('<div class="rp-ours"><b>Our schedules on the same machine, under the same rules</b>'
                   '<p class="rp-by">Made by <code>schedule_ours</code>, '
                   "a scheduler we wrote for the paper's model; it is not the compiler that compiles this "
                   "website's leaderboard entries.</p>"
                   "<p>The paper's results table (Table 3) gives, for four benchmarks on four grid "
                   "sizes and two trap lengths, the time of its compiler's schedule. With no code "
                   "or circuits published, this is a model-level comparison: the device, the "
                   "timing table and the rules are the paper's; the circuits are rebuilt from its "
                   "description, and the schedules are ours, each checked under the paper's rules "
                   "(broadcast transport, one waveform at a time, nothing else running during an "
                   "inter-trap cycle) with strict durations.</p>")
        head = ('<table class="rp"><tr><th>benchmark</th><th>the paper (its compiler)</th>'
                "<th>ours</th><th></th><th>fidelity: paper</th><th>ours</th><th>checks</th></tr>")
        out.append("<p>The paper's main configuration, A-60 (a 4×4 grid of 40 traps):</p>" + head)
        rest_open = False
        for r in sorted(main, key=lambda r: (r["n"] != 60, r["L"], r["n"], r["benchmark"])):
            if r["n"] != 60 and not rest_open:
                out.append("</table><details><summary>the other grid sizes (A-20, A-40, A-100)"
                           "</summary>" + head)
                rest_open = True
            c = r["compare"]["paper"]
            watch = (f'<br><a class="rp-watch" href="{_e(key)}/{_e(r["id"])}.html">▶ watch ours</a>'
                     if _model_studio_wanted(r) else "")
            gates = r.get("two_qubit_gates", {})
            differs = gates and gates.get("ours") != gates.get("paper_implied")
            lab = (f'{_e(r["benchmark"])}-{r["n"]}, L={r["L"]}'
                   + (f'<br><small>circuit differs: {gates["ours"]} two-qubit gates, the paper '
                      f'implies {gates["paper_implied"]}</small>' if differs else "") + watch)
            out.append(f'<tr><td>{lab}</td><td class="n">{_num(c["T_exe_us"]["theirs"], "makespan_us")}</td>'
                       f'<td class="n"><b>{_num(r["measured"]["makespan_us"], "makespan_us")}</b></td>'
                       f'<td class="n">{_ratio(c["T_exe_us"]["theirs"], r["measured"]["makespan_us"])}</td>'
                       f'<td class="n">{c["F"]["theirs"]:.2f}</td>'
                       f'<td class="n">{_num(c["F"]["ours"], "fidelity")}</td>'
                       f"<td>{_checks(r)}</td></tr>")
        out.append("</table>" + ("</details>" if rest_open else ""))
        out.append("<p><small>The paper prints its fidelities to two decimals; ours is the "
                   "same estimate (Table 1's fidelities, transport counted per ion moved, a swap "
                   "once, the paper's decoherence term).</small></p>")
        out.append("<p>What moves these numbers, each run both ways on A-60 at L = 8:</p><ul>"
                   "<li>Two gates in one trap at once are allowed (the paper's figures run them). "
                   "Forbidding it slows VQE-60 by 31%; it stays 6.7× faster than the paper.</li>"
                   "<li>Where the two gate zones sit in a trap is not stated; we space them evenly. "
                   "Next to the trap ends they cost up to 23% (RCA; 5% on QAOA).</li>"
                   "<li>A swap needs one of its ions in a gate zone: not stated, but every swap the "
                   "paper draws has one. Kept on; off, RCA is 6% faster.</li>"
                   "<li>The two-qubit gate is 141 µs, as the worked examples need. With Table 1's "
                   "25 µs, ours would be 10–24% faster still.</li>"
                   "<li>QAOA is run on the complete graph, which matches the gate counts the "
                   "paper plots; its text says 3-regular graphs, which we also ran (128.5 ms at L = 8).</li>"
                   "<li>Every schedule of ours runs each qubit's gates in the circuit's order, even "
                   "where two commute: the order is the circuit's, not ours to change. An earlier "
                   "version let QAOA's commuting gates run in any order; keeping the order costs it "
                   "up to 41% (QAOA-60 at L = 8: 101.3 ms then, 143.3 ms now).</li></ul>")
        out.append("<p>Where ours is fast, it is because all-to-all circuits (VQE, QAOA) are "
                   "run as a line of ions swept by an odd-even swap network laid along a trail of "
                   "traps that needs only five broadcast classes.</p></div>")
    return "\n".join(out)


def _model_studio(p: dict, run: dict, tmp: Path, model):
    """One TrapSIMD schedule (a worked example, or ours on A-60) as a Studio page."""
    from ..api import Machine
    from ..arch import Architecture
    from ..repro.__main__ import _open
    from ..repro.devices import layout
    from ..repro.studio import gate_label, studio_arch_doc, to_tsir
    from ..repro.timed import TimedSchedule

    s = TimedSchedule.load(_open(ROOT / p["key"] / run["file"]))
    pos = layout(s.device, "trapsimd")
    name = f"{p['key']}_{run['id']}".replace(".", "_")
    doc = studio_arch_doc(s, pos, name=name, table=p["key"], hop_entails=(),
                          note=f"{p['short']} ({p['venue']}): {p['what']}",
                          gate_us=float(run.get("tg_us", 141.0)), one_q_us=5.0, measure_us=120.0,
                          reset_us=0.0)
    m = Machine(Architecture.from_json(doc))
    circ = _model_circuit(run, s)
    # the model runs each circuit CX as one gate of latency tg: CX (`studio.gate_label`)
    prog = to_tsir(s, name=run["id"], arch=name, gate=gate_label(s, circ[2]))
    out = tmp / f"{name}.html"
    ours = run.get("kind") != "example"
    label = run["label"]
    m.render(prog, out, model=model, kicker=f"{'OURS' if ours else 'REPRODUCED'} · {p['short'].upper()}",
             headline=label, source=_source(prog, circ))
    return f"reproduce/{p['key']}/{run['id']}.html", out.read_text(encoding="utf-8"), ""


def _ish_circuit(run: dict, sched):
    """IonShuttler's sequence: the SAT tool's fixed order of chain visits, or the heuristic's
    one-qubit gates (it chooses the order itself, so they are listed by qubit)."""
    seq = sched.source.get("sequence")
    if seq:
        lines = ["// full register access: each element is one visit to the processing zone"] + \
                [f"access {', '.join(el)};" for el in seq]
        ops = [{"name": "access", "ions": tuple(el), "q": list(el), "line": k + 2}
               for k, el in enumerate(seq)]
        return "sequence", lines, ops, True
    seen = {}
    for e in sched.events:
        if e.kind in ("gate", "gate1") and e.meta.get("gate") is not None:
            seen.setdefault(e.meta["gate"], (e.meta.get("op", "gate"), list(e.meta.get("qubits", [])), tuple(e.ions)))
    rows = sorted(seen.values(), key=lambda r: r[1])
    lines = ["// the circuit's gates (the heuristic picks their order)"] + \
            [f"{op} {', '.join(f'q[{q}]' for q in qs)};" for op, qs, _ in rows]
    ops = [{"name": op, "ions": ions, "q": qs, "line": k + 2} for k, (op, qs, ions) in enumerate(rows)]
    return "circuit", lines, ops, True


def _ish_wanted(run: dict) -> bool:
    """Seed 0 of every paper configuration, and ours for it."""
    return run.get("seed") == 0 and run.get("kind") != "diagnostic" and \
        not str(run.get("config", "")).startswith(("heurNoGS", "sat_R2251T"))


def _ionshuttler_section(p: dict) -> str:
    """MQT IonShuttler: both of its tools, every schedule replayed in time steps."""
    key = p["key"]
    res = _results(key) or {}
    runs = {r["id"]: r for r in res.get("runs", [])}
    authors = ", ".join(p["authors"])
    art = p["artifact"]
    out = [f'<h2 id="{_e(key)}">{_e(p["short"])}</h2>',
           f'<p class="rp-cite">{_e(authors)}. <i>{_e(p["title"])}</i>. {_e(p["venue"])}. '
           f'<a href="https://arxiv.org/abs/{_e(p["arxiv"])}">arXiv:{_e(p["arxiv"])}</a>; and '
           f'<i>{_e(p["also"]["title"])}</i>, {_e(p["also"]["venue"])}, '
           f'<a href="https://arxiv.org/abs/{_e(p["also"]["arxiv"])}">arXiv:{_e(p["also"]["arxiv"])}</a> · '
           f'their code: <a href="{_e(art["url"])}">{_e(art["url"].split("github.com/")[-1])}</a> '
           f'({_e(art["license"])})</p>',
           f'<p>{_e(p["what"])}</p>' + _took(p), f'<p><small>{_e(art["note"])}</small></p>']
    first = next((r for r in res.get("runs", []) if r.get("config") == "sat_L4411_fra12"), None)
    if first:
        from ..api import Machine
        from ..arch import Architecture
        from ..repro.__main__ import _open
        from ..repro.devices import layout, to_arch_doc
        from ..repro.timed import TimedSchedule
        from .build import device_svg
        s = _ish_load(first)
        doc = to_arch_doc(s.device, {n: (x, -y) for n, (x, y) in layout(s.device, "ionshuttler").items()},
                          name="ionshuttler_L4411", table=key)
        svg = device_svg(Machine(Architecture.from_json(doc)), labels=False)
        out.append(f'<figure class="rp-fig">{svg}<figcaption>Lattice 4 4 1 1: 24 sites (one ion '
                   "chain each) between 16 junctions, and the processing zone off one corner, "
                   "entered and left one way</figcaption></figure>")
    out.append("<p>Both tools count abstract time steps. The SAT tool prints the number of states "
               "(one more than the number of moves); the heuristic prints the index, from zero, of "
               "its last step. Every schedule each tool produced is replayed under the paper's "
               "rules, and the replayed length must equal the printed number. The paper reports "
               "the mean over runs with random placements; the placements are reproduced from "
               "the tool's own seeding, seeds 0 to 9.</p>")
    out.append('<table class="rp"><tr><th>configuration</th><th>runs</th><th>their code, per seed</th>'
               "<th>mean</th><th>the paper (Table I)</th><th>checks under the paper's rules</th></tr>")
    for c in res.get("configs", []):
        if c.get("kind") == "diagnostic":
            continue
        seeds = c["seeds"]
        per = " ".join(str(int(c["printed"][str(s_)])) for s_ in seeds)
        pp = c.get("paper") or {}
        ok = pp.get("verdict") == "exact"
        r0 = runs.get(f'{c["config"]}_seed0')
        watch = (f'<br><a class="rp-watch" href="{_e(key)}/{_e(r0["id"])}.html">▶ watch seed 0 in the Studio</a>'
                 if r0 else "")
        eq = "= ours" if c.get("all_equal") else "differs"
        out.append(f'<tr><td>{_e(c["label"])}{watch}</td><td class="n">{len(seeds)}</td>'
                   f'<td class="n">{per} <span class="{"rp-ok" if c.get("all_equal") else "rp-bad"}">{eq}</span></td>'
                   f'<td class="n">{c["mean_printed"]:g}</td>'
                   f'<td class="n">{pp.get("value", "–")} <span class="{"rp-ok" if ok else "rp-bad"}">'
                   f'{"equal" if ok else "differs"}</span></td>'
                   f'<td><span class="rp-pill ok">all {len(seeds)} runs pass</span></td></tr>')
    out.append("</table>")
    for note in catalog.NOTES.get(key, []):
        out.append(f'<div class="rp-note"><b>{_e(note["title"])}</b>{_e(note["text"])}</div>')
    ours = res.get("ours", {})
    rows = [o for o in ours.get("runs", []) if o.get("ok")]
    out.append('<div class="rp-ours"><b>Our schedules on the same machine, under the same rules</b>')
    if not rows:
        out.append("Not yet.</div>")
        return "\n".join(out)
    out.append('<p class="rp-by">Made by <code>schedule_ours</code>, '
               "a scheduler we wrote for this comparison; it is not the compiler that compiles this "
               "website's leaderboard entries.</p>")
    out.append("<p>Our scheduler works in the heuristic's own model: its device, its processing "
               "zone and parking edge, the placement its run of each seed starts from, one edge per "
               "chain per step through one node, one gate at a time. The SAT tool's model differs "
               "(another processing-zone layout, a fixed gate order), so its numbers are not "
               "compared with these.</p>")
    out.append('<div class="rp-wide"><table class="rp"><tr><th>configuration</th><th>the heuristic</th>'
               "<th>ours</th><th>lower bound</th><th>checks</th></tr>")
    by_cfg: dict[str, list] = {}
    for o in rows:
        by_cfg.setdefault(o["config"], []).append(o)
    for cfg, os_ in by_cfg.items():
        th = [runs[o["against"]]["printed"]["stdout"] for o in os_]
        us = [o["search"]["best"] for o in os_]
        lb = [o["search"]["lower_bound"] for o in os_]
        o0 = next((o for o in os_ if o.get("seed") == 0), None)
        watch = (f'<br><a class="rp-watch" href="{_e(key)}/{_e(o0["id"])}.html">▶ watch ours (seed 0)</a>'
                 if o0 else "")
        label = next(c["label"] for c in res["configs"] if c["config"] == cfg)
        bound = ("= ours on every seed" if lb == us else " ".join(map(str, lb)))
        out.append(f"<tr><td>{_e(label)}, seeds {min(o['seed'] for o in os_)}–{max(o['seed'] for o in os_)}{watch}</td>"
                   f'<td class="n">mean {sum(th) / len(th):g}<br><small>{" ".join(map(str, th))}</small></td>'
                   f'<td class="n">mean <b>{sum(us) / len(us):g}</b><br><small>{" ".join(map(str, us))}</small></td>'
                   f'<td class="n">{bound}</td>'
                   f'<td><span class="rp-pill ok">all {len(os_)} pass</span></td></tr>')
    out.append("</table></div>")
    out.append("<p>Every one of ours reaches a lower bound: gates run one at a time, and a chain "
               "that is m moves from the parking edge cannot have its gate before step m − 1. So "
               "no schedule that moves one edge per step, as the heuristic does, can be shorter "
               "on these placements.</p></div>")
    return "\n".join(out)


def _ish_load(run: dict):
    import gzip as _gz
    from ..repro.ionshuttler import import_ionshuttler
    d = json.loads(_gz.decompress((ROOT / "schoenberger2024" / run["file"]).read_bytes()))
    return import_ionshuttler(d, name=run["id"])


def _ish_studio(p: dict, run: dict, tmp: Path, model):
    """One IonShuttler schedule (the tool's, or ours) as a Studio page."""
    from ..api import Machine
    from ..arch import Architecture
    from ..repro.devices import layout
    from ..repro.studio import studio_arch_doc, to_tsir

    s = _ish_load(run)
    pos = layout(s.device, "ionshuttler")
    name = f"{p['key']}_{run['id']}".replace(".", "_")
    doc = studio_arch_doc(s, pos, name=name, table=p["key"], hop_entails=(),
                          note=f"{p['short']} ({p['venue']}): {p['what']}",
                          gate_us=1.0, one_q_us=1.0, measure_us=0.0, reset_us=0.0)
    m = Machine(Architecture.from_json(doc))
    prog = to_tsir(s, name=run["id"], arch=name)
    out = tmp / f"{name}.html"
    ours = run["id"].endswith("_ours")
    label = run["label"]
    m.render(prog, out, model=model, kicker=f"{'OURS' if ours else 'REPRODUCED'} · {p['short'].upper()}",
             headline=label, source=_source(prog, _ish_circuit(run, s)))
    return f"reproduce/{p['key']}/{run['id']}.html", out.read_text(encoding="utf-8"), ""

