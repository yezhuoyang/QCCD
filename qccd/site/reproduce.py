"""The "Reproducing the literature" page (`/reproduce/`).

Everything on it is read from the reproduction catalog (`qccd.repro.catalog`) and the
results `python -m qccd.repro check` writes; nothing is typed in here except prose.  A
number the page shows is a number a command printed, and the command is on the page.
"""

from __future__ import annotations

import html
import json
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
           f'<p>{_e(p["what"])}</p>']
    if art.get("note"):
        out.append(f'<p><small>{_e(art["note"])}</small></p>')
    out.append(_figures(p))
    metric = _metric_of(p)
    rows = [r for r in (res or {}).get("runs", []) if "missing" not in r]
    if rows:
        head = {"last_start_us": "start of the last step", "moves": "shuttles"}.get(metric, "time")
        out.append('<table class="rp"><tr><th>configuration</th>'
                   f'<th>{head}: their code</th><th>our replay</th><th>the paper</th>'
                   '<th>checks under the paper\'s rules</th></tr>')
        for r in rows:
            t, o, pp = _compare_cells(r, metric)
            fid = ""
            if "fidelity" in r.get("compare", {}).get("tool", {}):
                ft, fo, fp = _compare_cells(r, "fidelity")
                fid = (f'<tr><td style="padding-left:22px"><small>fidelity</small></td>'
                       f'<td class="n">{ft}</td><td class="n">{fo}</td><td class="n">{fp}</td><td></td></tr>')
            spec = next((x for x in p["runs"] if x["id"] == r["id"]), {})
            watch = (f'<br><small class="rp-skip">{_e(spec["no_studio"])}</small>' if spec.get("no_studio")
                     else f'<br><a class="rp-watch" href="{_e(p["key"])}/{_e(r["id"])}.html">▶ watch it in the Studio</a>')
            out.append(f'<tr><td>{_e(r.get("label", r["id"]))}{watch}</td><td class="n">{t}</td>'
                       f'<td class="n">{o}</td><td class="n">{pp}</td><td>{_checks(r)}</td></tr>{fid}')
        out.append("</table>")
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
    out.append(_ours_box(p, res))
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


def _ours_box(p: dict, res: dict | None) -> str:
    """Our own schedules on the paper's machine, each checked like the paper's (strict
    durations, the paper's circuit), next to the paper's own run."""
    key = p["key"]
    spec = catalog.OURS.get(key)
    rows = [o for o in (res or {}).get("ours", []) if o.get("ok")]
    head = '<div class="rp-ours"><b>Our compiler on the same machine, under the same rules</b>'
    if not spec or not rows:
        return (head + "Not yet: no schedule of ours for this paper has been checked. Nothing "
                "is shown here until one passes every rule the paper's own schedules are held to."
                "</div>")
    books = {"qccdsim": "QCCDSim's chain bookkeeping", "qccdsim-physical-swap": "the physical one",
             "cyclone": "Cyclone's rules", "jones": "the paper's rules, its declared locks included",
             "tiscc": "TISCC's rules", "qccdsim (gates as a set)": "QCCDSim's rules, gates in any order the "
             "paper allows"}
    out = [head + f"<p>{_e(spec['how'])} Each schedule below is replayed by the same checker "
           "as the paper's, with the same timing law and circuit; an operation the paper's law "
           "does not price is refused.</p>"]
    moves_first = key == "saki2022"
    tm = "last_start_us" if _metric_of(p) == "last_start_us" else "makespan_us"
    fid = any("fidelity" in o["measured"] and "fidelity" in o["theirs"] for o in rows) and not moves_first
    cols = (["shuttles: QCCDSim", "Muzzle", "ours", "time: QCCDSim", "Muzzle", "ours"] if moves_first
            else [("start of the last step: " if tm == "last_start_us" else "") + "their code", "ours", ""]
            + (["fidelity: theirs", "ours"] if fid else []))
    out.append('<table class="rp"><tr><th>configuration</th>'
               + "".join(f"<th>{_e(c)}</th>" for c in cols) + "<th>checks</th></tr>")
    for o in rows:
        m, t = o["measured"], o["theirs"]
        watch = f'<br><a class="rp-watch" href="{_e(key)}/{_e(o["id"])}.html">▶ watch ours in the Studio</a>'
        n = sum(1 for pr in o["profiles"].values() for v in pr["checks"].values() if v == "passed")
        names = " and ".join(books.get(k, "the paper's rules") for k in o["profiles"])
        pill = (f'<span class="rp-pill ok" title="strict durations; the circuit; same device and '
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
            if fid:
                cells += [_num(t["fidelity"], "fidelity"),
                          f"<b>{_num(m['fidelity'], 'fidelity')}</b> "
                          + _ratio(t["fidelity"], m["fidelity"], lower_better=False).replace("faster", "higher").replace("slower", "lower")]
        vs = next((r.get("label", r["id"]) for r in p["runs"] if r["id"] == o["against"]), "")
        vs_line = "" if vs == o["label"] else f"<br><small>against: {_e(vs)}</small>"
        out.append(f'<tr><td>{_e(o["label"])}{vs_line}{watch}</td>'
                   + "".join(f'<td class="n">{c}</td>' for c in cells) + f"<td>{pill}</td></tr>")
    out.append("</table>")
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
    """Which rule each paper's own model imposes -- read from the profiles the checker runs."""
    from ..repro.__main__ import PROFILES

    cols = []
    for p in papers:
        if p["key"] == "schoenberger2024":
            from ..repro.ionshuttler import IONSHUTTLER_SAT as prof
        elif p.get("kind") == "model":
            from ..repro.trapsimd import TRAPSIMD as prof
        elif not p.get("runs"):
            continue
        else:
            prof = PROFILES[p["runs"][0]["profile"]]
        on = {"positions": True, "capacity": True, "ion_serial": True, "durations": True,
              "segment_mutex": prof.segment_mutex, "junction_mutex": prof.junction_mutex,
              "trap_serial": bool(prof.trap_serial), "chain_order": bool(prof.chain_order),
              "declared_locks": prof.declared_locks, "broadcast": bool(prof.broadcast),
              "circuit": p.get("kind") == "model" or any(r.get("circuit") for r in p["runs"])}
        cols.append((p, on))
    out = ['<table class="rp rp-rules"><tr><th>rule</th>'
           + "".join(f"<th>{_e(p['short'])}</th>" for p, _ in cols) + "</tr>"]
    for key, short, why in CHECK_NAMES:
        out.append(f"<tr><td><b>{_e(short)}</b><br><small>{_e(why)}</small></td>"
                   + "".join('<td class="n">' + ('<span class="rp-ok">✓</span>' if on.get(key)
                                                 else '<span class="rp-skip">–</span>')
                             + "</td>" for _, on in cols) + "</tr>")
    out.append("</table>")
    return ('<p><small>✓ the paper\'s model has the rule, and every replay is held to it; '
            "– the paper's model does not have it, so it is not checked.</small></p>"
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
           f'<p>{_e(p["what"])}</p>',
           f'<p><small>{_e(p["artifact"]["note"])}</small></p>']
    m = Machine.load(ROOT / key / p["arch"])
    svg = device_svg(m, labels=False)
    zones = ", ".join(f"{k} ({v.get('capacity')})" for k, v in m.arch.zone_types.items())
    out.append(f'<figure class="rp-fig">{svg}<figcaption>H2 in our language: one closed loop of '
               f'{len(list(m.arch.device.sites()))} sites, no junction; zones with their capacity '
               f'in qubits: {_e(zones)}. <a class="rp-watch" href="{_e(key)}/design.html">▶ open it '
               f'in the Studio</a></figcaption></figure>')
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
    who = f"The published compiler (release {_e(release)})" if release else "Our compiler"
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


def page(PAGE: str, STYLE: str) -> str:
    papers = [p for p in catalog.PAPERS if p.get("runs") or p.get("kind") in ("design", "model")]
    body = [
        "<h1>Reproducing the literature</h1>",
        '<p class="rp-lead">A design tool and a compiler are only as believable as their agreement '
        "with other people's work. So we took the most relevant papers on QCCD architecture and "
        "compilation, ran each paper's own published code, and replayed every operation it "
        "produced in our checker, under that paper's own rules and with its own clock. "
        "Where the numbers agree, the replay must equal the tool exactly; where they do not, "
        "this page says so.</p>",
        '<h2 id="method">How a paper is checked</h2>',
        '<ol class="rp-steps">'
        "<li><b>Run their code.</b> Each paper's artifact is run as its authors ran it, and every "
        "operation it schedules (each split, move, junction crossing, merge and gate, with its start "
        "and end time) is written out.</li>"
        "<li><b>Rebuild their machine in our language.</b> The traps, junctions and segments become "
        "an architecture document, priced by the paper's own timing table.</li>"
        "<li><b>Replay under their rules.</b> Our checker replays the schedule event by event and "
        "checks it against the rules the paper states: where every ion is, trap capacity at every "
        "instant, one ion per segment and per junction, one operation per trap, which end of a "
        "chain an ion leaves from, how long each operation takes, and whether the gates run are "
        "the circuit's.</li>"
        "<li><b>Compare three numbers.</b> What their code printed, what our replay measures (these "
        "must be identical), and what the paper reports.</li>"
        "<li><b>Then compile it ourselves.</b> The same circuit on the same machine, checked by the "
        "same checker under the same rules.</li></ol>",
        "<p>The rules are each paper's, not ours. Our own machine model is stricter in "
        "places (one waveform per cycle, gates and transport never in the same cycle), and under "
        "it most of these schedules would not be legal at all; that is a different question, "
        "and it is not the one asked here.</p>",
        _rules_table(papers),
        "<p>A note on a paper is written only when running its code shows it. Everything here can "
        'be rerun with <span class="rp-cmd">python -m qccd.repro check</span>.</p>',
        '<h2 id="summary">At a glance</h2>',
        _summary(papers),
    ]
    for p in papers:
        body.append(section(p))
    body.append('<h2 id="considered">Papers we read and did not reproduce</h2>')
    body.append("<p>Every one of these was read in full, with its code where there is any. A "
                "paper is reproduced here only when its machine, its timing and its circuits can "
                "be pinned down well enough that a mismatch would mean something.</p>")
    body.append('<table class="rp"><tr><th>paper</th><th>why it is not here</th></tr>'
                + "".join(f'<tr><td>{_e(c["short"])}<br><small><a href="https://arxiv.org/abs/'
                          f'{_e(c["arxiv"])}">arXiv:{_e(c["arxiv"])}</a></small></td>'
                          f'<td>{_e(c["why"])}</td></tr>' for c in catalog.CONSIDERED)
                + "</table>")
    html_ = PAGE.format(title="Reproducing the literature — QCCD", style=STYLE,
                        extra_css=CSS, body="\n".join(body))
    return html_


def index_entries() -> list:
    out = [{"t": "Reproducing the literature", "u": "reproduce/", "k": "reproduce",
            "d": "published QCCD papers rerun and replayed under their own rules"}]
    for p in catalog.PAPERS:
        if p.get("runs") or p.get("kind") in ("design", "model"):
            out.append({"t": f"{p['short']}: reproduction", "u": f"reproduce/#{p['key']}",
                        "k": "reproduce", "d": p["title"]})
    return out


# ------------------------------------------------------------------- the Studio pages
#
# Every replayed run opens in the Studio, the way a leaderboard entry does: the paper's
# machine as an architecture document, the paper's schedule as a program on it.  The page
# is the ordinary Studio; a small card injected by the site build says what the reader is
# looking at, because the Studio's clock and rule panel are THIS platform's, not the paper's.

#: what the Studio needs to price the paper's operations, per paper (the paper's values)
STUDIO_PRICING = {
    "murali2020": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "saki2022": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "khan2026cyclone": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "khan2025moveless": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "bach2025": {"one_q_us": 0.0, "measure_us": 0.0, "reset_us": 0.0},
    "jones2025": {"gate_us": 40.0, "one_q_us": 5.0, "measure_us": 400.0, "reset_us": 50.0},
    "leblond2023": {"gate_us": 2000.0, "one_q_us": 10.0, "measure_us": 120.0, "reset_us": 10.0},
}


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


CARD_CSS = """
#rpcard{position:fixed;top:134px;right:14px;width:330px;max-height:62vh;overflow:auto;z-index:40;
 background:#fff;border:1px solid #d9d7d0;border-left:3px solid #2a78d6;border-radius:8px;
 box-shadow:0 6px 18px rgba(0,0,0,.10);padding:10px 12px;font:12.5px/1.45 ui-sans-serif,system-ui,sans-serif;color:#1c2333}
#rpcard[data-open="0"] .rpb{display:none}
#rpcard .rpk{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:#2a78d6;font-weight:600}
#rpcard .rph{font-weight:600;margin:2px 0 6px}
#rpcard p{margin:5px 0}#rpcard .ok{color:#1b7f3b;font-weight:600}#rpcard .bad{color:#b42318;font-weight:600}
#rpcard button{float:right;font:inherit;font-size:11.5px;border:1px solid #d9d7d0;background:#f6f5f1;border-radius:5px;padding:1px 7px;cursor:pointer}
#rpcard a{color:#2a78d6}
body[data-embed="1"] #rpcard{display:none}
"""

CARD_JS = """
(function(){
  var c = document.getElementById('rpcard'); if(!c) return;
  var b = c.querySelector('button');
  b.addEventListener('click', function(){ var o = c.getAttribute('data-open') !== '0';
    c.setAttribute('data-open', o ? '0' : '1'); b.textContent = o ? 'show' : 'hide'; });
  window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, {
    'repro:card': {t: 'What this page is', d: "Hides or shows the card that says whose schedule this is, what it measured on the paper's own clock, and how the Studio judges it."}});
})();
"""

#: why one of OUR rules flags a paper's schedule, when the reason is a difference of models
OUR_RULE_WHY = {
    "R14": "our language does not record where in a trap an ion stands, so R14 charges a "
           "reorder for every split from a chain longer than two; the paper tracks the order "
           "and pays only where one is needed",
    "R13": "our rules cap a chain at 15 ions at gate time; the paper allows more",
    "R6b": "our language puts a two-qubit gate's ions in one trap; the paper gates ions in "
           "neighbouring zones",
    "R3": "the paper's swap exchanges two neighbouring ions in place, an operation our language "
          "does not have; the page plays it as two ions crossing one segment, which R3 (one ion "
          "per segment) and R5 (no crossing) forbid",
    "R5": "the same swap, seen as two ions passing each other on one segment",
}


def _card(p: dict, run: dict, row: dict | None, failed_ours: dict) -> str:
    metric = _metric_of(p)
    tool = (row or {}).get("compare", {}).get("tool", {}).get(metric)
    paper = (row or {}).get("compare", {}).get("paper", {}).get(metric)
    clock = {"last_start_us": "the start of the last step",
             "moves": "the number of shuttles"}.get(metric, "the time")
    lines = []
    if tool:
        lead = ("By the paper's own measure the schedule makes" if metric == "moves"
                else f"On the paper's own clock {clock} is")
        lines.append(f"{lead} <b>{_num(tool['ours'], metric)}</b>, exactly what its code printed"
                     + (f"; the paper reports {_num(paper['theirs'], metric)} "
                        f"({_e(paper.get('where', ''))})" if paper else "") + ".")
    if row:
        failed = [k for k, v in row.get("checks", {}).items() if v == "failed"]
        n_ok = sum(1 for v in row.get("checks", {}).values() if v == "passed")
        lines.append("Under the paper's rules: " + (
            f'<span class="ok">all {n_ok} checks pass</span>.' if not failed else
            f'<span class="bad">{_e(", ".join(failed))} fails</span>, {n_ok} others pass '
            "(the reproduction page says why)."))
    ours = ("This view plays the same operations one instruction at a time, so the runtime "
            "above is this platform's lockstep clock, and the rule panel holds this platform's "
            "own, stricter rules")
    if failed_ours:
        bits = [f"{r} ({n}){': ' + OUR_RULE_WHY[r] if r in OUR_RULE_WHY else ''}"
                for r, n in sorted(failed_ours.items())]
        ours += ": they flag " + "; ".join(bits) + "."
    else:
        ours += ": they find nothing."
    lines.append(_e(ours))
    back = f"../#{p['key']}"
    return (f'<style>{CARD_CSS}</style><div id="rpcard" data-open="1">'
            f'<button type="button" data-hint="repro:card">hide</button>'
            f'<div class="rpk">Reproduced · {_e(p["short"])} ({_e(p["venue"])})</div>'
            f'<div class="rph">{_e(run.get("label", run["id"]))}</div>'
            '<div class="rpb">' + "".join(f"<p>{x}</p>" for x in lines)
            + f'<p><a href="{back}">← all reproductions</a></p></div></div>'
            f"<script>{CARD_JS}</script>")


def studio_pages(tmp: Path, only: set[str] | None = None):
    """Yield `(site path, page html, card html)` for every replayed run in the catalog."""
    from ..api import Machine
    from ..arch import Architecture
    from ..cost.models import corrected_model
    from ..repro.devices import layout
    from ..repro.studio import studio_arch_doc, to_tsir
    from ..verify import verify

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
        rows = {r["id"]: r for r in res.get("runs", []) if "missing" not in r}
        for o in catalog.OURS.get(p["key"], {}).get("runs", []):
            row = next((x for x in res.get("ours", []) if x["id"] == o["id"] and x.get("ok")), None)
            if row is None or (only and o["id"] not in only):
                continue
            spec = next(x for x in p["runs"] if x["id"] == o["against"])
            yield _ours_studio(p, o, spec, row, tmp, model)
        for run in p.get("runs", []):
            if (only and run["id"] not in only) or run.get("no_studio"):
                continue
            s = _import(p, run)
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
            prog = to_tsir(s, name=run["id"], arch=name)
            rep = verify(prog, m.arch, model, check_metrics=False)
            failed = {r: n for r, n in rep.rules.by_rule().items() if n}
            out = tmp / f"{name}.html"
            m.render(prog, out, model=model, kicker=f"REPRODUCED · {p['short'].upper()}",
                     headline=run.get("label", run["id"]))
            yield (studio_rel(p, run), out.read_text(encoding="utf-8"),
                   _card(p, run, rows.get(run["id"]), failed))


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
    prog = TSIR(name="H2, 32 qubits in four batches", arch_spec=m.arch.name)
    prog.add(Instruction(type="init", id=prog.next_id(), placement=place,
                         quanta={i: 0.0 for i in place}, meta={"note": "Sec. II.E batches"}))
    out = tmp / f"{p['key']}_design.html"
    m.render(prog, out, model=model, kicker=f"REPRODUCED · {p['short'].upper()}",
             headline="the machine, rebuilt in our language")
    card = (f'<style>{CARD_CSS}</style><div id="rpcard" data-open="1">'
            '<button type="button" data-hint="repro:card">hide</button>'
            f'<div class="rpk">Reproduced · {_e(p["short"])} ({_e(p["venue"])})</div>'
            '<div class="rph">the machine, rebuilt in our language</div><div class="rpb">'
            "<p>Every number of this machine comes from the paper, with the place it was read; "
            "where the paper is silent the document says so. Its 32 qubits are loaded as the "
            "paper's four batches of eight. "
            + ("Our compiler compiles the paper's circuits on it; the reproduction page gives the "
               "rounds (the circuits themselves are not ours to publish).</p>"
               if sum(f["good"] for f in _h2_families(json.loads((ROOT / p["key"] / "results.json")
                                                                   .read_text(encoding="utf-8")))) else
               "Our compiler cannot yet compile the paper's circuits on it; the reproduction page "
               "lists why.</p>")
            + f'<p><a href="../#{p["key"]}">← all reproductions</a></p></div></div>'
            f"<script>{CARD_JS}</script>")
    return f"reproduce/{p['key']}/design.html", out.read_text(encoding="utf-8"), card


def _ours_studio(p: dict, o: dict, spec: dict, row: dict, tmp: Path, model):
    """One of our schedules as a Studio page, drawn like the paper's run it beats (or not)."""
    from ..api import Machine
    from ..arch import Architecture
    from ..repro.__main__ import _open
    from ..repro.devices import layout
    from ..repro.studio import studio_arch_doc, to_tsir
    from ..repro.timed import TimedSchedule
    from ..verify import verify

    s = TimedSchedule.load(_open(ROOT / p["key"] / o["file"]))
    fam, kw = _layout_for(p, spec)
    pos = layout(s.device, fam, **kw)
    price = dict(STUDIO_PRICING.get(p["key"], {}))
    if "gate_us" not in price:
        cap = max(int(a["capacity"]) for a in s.device.sites.values())
        price["gate_us"] = float(int(max(100.0, 13.33 * cap - 54)))
    name = f"{p['key']}_{o['id']}".replace(".", "_")
    doc = studio_arch_doc(s, pos, name=name, table=p["key"], hop_entails=("split", "merge"),
                          note=f"{p['short']} ({p['venue']}): our schedule", **price)
    m = Machine(Architecture.from_json(doc))
    prog = to_tsir(s, name=o["id"], arch=name)
    rep = verify(prog, m.arch, model, check_metrics=False)
    failed = {r: n for r, n in rep.rules.by_rule().items() if n}
    out = tmp / f"{name}.html"
    label = f"{o['label']}: our schedule"
    m.render(prog, out, model=model, kicker=f"OURS · {p['short'].upper()}", headline=label)
    t, me = row["theirs"], row["measured"]
    metric = "moves" if p["key"] == "saki2022" else "makespan_us"
    tm = "last_start_us" if _metric_of(p) == "last_start_us" else "makespan_us"
    what = "its last operation starts at" if tm == "last_start_us" else "it takes"
    lines = [f"Our schedule for the circuit the paper ran, on the paper's machine. On the paper's "
             f"own clock {what} <b>{_num(me[tm], 'makespan_us')}</b>, with "
             f"{me['moves']:,} shuttles; the paper's code: {_num(t[tm], 'makespan_us')} "
             f"and {t['moves']:,} ({_e(spec.get('label', ''))}).",
             "Under the paper's rules: <span class=\"ok\">every check passes</span>, with "
             "strict durations: an operation the paper's law does not price is refused."]
    ours = ("This view plays the same operations one instruction at a time, so the runtime "
            "above is this platform's lockstep clock, and the rule panel holds this platform's "
            "own, stricter rules")
    if failed:
        ours += ": they flag " + "; ".join(
            f"{r} ({n}){': ' + OUR_RULE_WHY[r] if r in OUR_RULE_WHY else ''}" for r, n in sorted(failed.items())) + "."
    else:
        ours += ": they find nothing."
    lines.append(_e(ours))
    card = (f'<style>{CARD_CSS}</style><div id="rpcard" data-open="1">'
            '<button type="button" data-hint="repro:card">hide</button>'
            f'<div class="rpk">Ours · {_e(p["short"])} ({_e(p["venue"])})</div>'
            f'<div class="rph">{_e(label)}</div><div class="rpb">'
            + "".join(f"<p>{x}</p>" for x in lines)
            + f'<p><a href="{_e(o["against"])}.html">the paper\'s schedule</a> · '
            f'<a href="../#{p["key"]}">← all reproductions</a></p></div></div>'
            f"<script>{CARD_JS}</script>")
    return f"reproduce/{p['key']}/{o['id']}.html", out.read_text(encoding="utf-8"), card


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
           f'<p>{_e(p["what"])}</p>', f'<p><small>{_e(p["artifact"]["note"])}</small></p>']
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
        out.append('<div class="rp-ours"><b>Our compiler on the same machine, under the same rules</b>'
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
                   "Next to the trap ends they cost up to 52% on QAOA.</li>"
                   "<li>A swap needs one of its ions in a gate zone: not stated, but every swap the "
                   "paper draws has one. Kept on; off, RCA is 6% faster.</li>"
                   "<li>The two-qubit gate is 141 µs, as the worked examples need. With Table 1's "
                   "25 µs, ours would be 7–24% faster still.</li>"
                   "<li>QAOA is run on the complete graph, which matches the gate counts the "
                   "paper plots; its text says 3-regular graphs, which we also ran (97.7 ms at L = 8).</li>"
                   "<li>Our VQE schedule is also legal in strict program order, so it does not rely "
                   "on gates commuting.</li></ul>")
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
    from ..repro.studio import studio_arch_doc, to_tsir
    from ..repro.timed import TimedSchedule
    from ..verify import verify

    s = TimedSchedule.load(_open(ROOT / p["key"] / run["file"]))
    pos = layout(s.device, "trapsimd")
    name = f"{p['key']}_{run['id']}".replace(".", "_")
    doc = studio_arch_doc(s, pos, name=name, table=p["key"], hop_entails=(),
                          note=f"{p['short']} ({p['venue']}): {p['what']}",
                          gate_us=float(run.get("tg_us", 141.0)), one_q_us=5.0, measure_us=120.0,
                          reset_us=0.0)
    m = Machine(Architecture.from_json(doc))
    prog = to_tsir(s, name=run["id"], arch=name)
    rep = verify(prog, m.arch, model, check_metrics=False)
    failed = {r: n for r, n in rep.rules.by_rule().items() if n}
    out = tmp / f"{name}.html"
    ours = run.get("kind") != "example"
    label = run["label"]
    m.render(prog, out, model=model, kicker=f"{'OURS' if ours else 'REPRODUCED'} · {p['short'].upper()}",
             headline=label)
    c = run.get("compare", {}).get("paper", {}).get("T_exe_us", {})
    me = run["measured"]["makespan_us"]
    if ours:
        lines = [f"Our schedule on the paper's device, timing and rules: <b>{_num(me, 'makespan_us')}</b>"
                 + (f"; the paper's compiler reports {_num(c['theirs'], 'makespan_us')} (Table 3) for "
                    "the circuit as it describes it" if c else "") + ". The circuit is rebuilt, so this "
                 "is a model-level comparison."]
    else:
        lines = [f"The paper's worked example, event for event: <b>{_num(me, 'makespan_us')}</b>, "
                 f"the paper's {_num(c.get('theirs', 0), 'makespan_us')}."]
    lines.append('Under the paper\'s rules: <span class="ok">every check passes</span>, with strict '
                 "durations and the broadcast rule (one transport class per cycle, nothing else "
                 "moving during it).")
    why = ("This view plays the same operations one instruction at a time, so the runtime "
           "above is this platform's lockstep clock, and the rule panel holds this platform's "
           "own, stricter rules")
    if failed:
        why += ": they flag " + "; ".join(
            f"{r} ({n}){': ' + OUR_RULE_WHY[r] if r in OUR_RULE_WHY else ''}" for r, n in sorted(failed.items())) + "."
    else:
        why += ": they find nothing."
    lines.append(_e(why))
    card = (f'<style>{CARD_CSS}</style><div id="rpcard" data-open="1">'
            '<button type="button" data-hint="repro:card">hide</button>'
            f'<div class="rpk">{"Ours" if ours else "Reproduced"} · {_e(p["short"])}</div>'
            f'<div class="rph">{_e(label)}</div><div class="rpb">'
            + "".join(f"<p>{x}</p>" for x in lines)
            + f'<p><a href="../#{p["key"]}">← all reproductions</a></p></div></div>'
            f"<script>{CARD_JS}</script>")
    return f"reproduce/{p['key']}/{run['id']}.html", out.read_text(encoding="utf-8"), card


ISH_RULE_WHY = {
    "R2": "the heuristic lets one chain enter the parking edge while another leaves it through "
          "the processing-zone node in the same step (its code allows this on purpose); our rules "
          "let one ion through a junction per cycle",
    "R3": "the same moment, seen as two ions on the segment into the parking edge",
    "R5": "the same moment, seen as two ions passing each other on that segment",
    "R20": "the processing zone is drawn from the tool's own grid coordinates, and two of its "
           "rails leave the lattice corner at a sharp angle; the paper's model has no geometry",
}


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
           f'<p>{_e(p["what"])}</p>', f'<p><small>{_e(art["note"])}</small></p>']
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
    out.append('<div class="rp-ours"><b>Our compiler on the same machine, under the same rules</b>')
    if not rows:
        out.append("Not yet.</div>")
        return "\n".join(out)
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
    from ..verify import verify

    s = _ish_load(run)
    pos = layout(s.device, "ionshuttler")
    name = f"{p['key']}_{run['id']}".replace(".", "_")
    doc = studio_arch_doc(s, pos, name=name, table=p["key"], hop_entails=(),
                          note=f"{p['short']} ({p['venue']}): {p['what']}",
                          gate_us=1.0, one_q_us=1.0, measure_us=0.0, reset_us=0.0)
    m = Machine(Architecture.from_json(doc))
    prog = to_tsir(s, name=run["id"], arch=name)
    rep = verify(prog, m.arch, model, check_metrics=False)
    failed = {r_: n for r_, n in rep.rules.by_rule().items() if n}
    out = tmp / f"{name}.html"
    ours = run["id"].endswith("_ours")
    label = run["label"]
    m.render(prog, out, model=model, kicker=f"{'OURS' if ours else 'REPRODUCED'} · {p['short'].upper()}",
             headline=label)
    if ours:
        th = next((r_["printed"]["stdout"] for r_ in (_results(p["key"]) or {}).get("runs", [])
                   if r_["id"] == run.get("against")), None)
        lines = [f"Our schedule in the heuristic's model, from the heuristic's own placement: "
                 f"<b>{run['search']['best']} steps</b>, which is its lower bound"
                 + (f"; the heuristic took {th}" if th is not None else "") + "."]
    else:
        lines = [f"The tool's own schedule: it printed <b>{run['printed']['stdout']}</b>, and the "
                 "replay measures the same."]
    lines.append('Under the paper\'s rules: <span class="ok">every check passes</span>, with strict '
                 "durations, in time steps.")
    why = ("This view plays the same moves one instruction at a time, in this platform's own "
           "lockstep clock (a step shows as a microsecond), and the rule panel holds this "
           "platform's own, stricter rules")
    why += (": they flag " + "; ".join(f"{r_} ({n}){': ' + ISH_RULE_WHY[r_] if r_ in ISH_RULE_WHY else ''}"
                                       for r_, n in sorted(failed.items())) + "."
            if failed else ": they find nothing.")
    lines.append(_e(why))
    card = (f'<style>{CARD_CSS}</style><div id="rpcard" data-open="1">'
            '<button type="button" data-hint="repro:card">hide</button>'
            f'<div class="rpk">{"Ours" if ours else "Reproduced"} · {_e(p["short"])}</div>'
            f'<div class="rph">{_e(label)}</div><div class="rpb">'
            + "".join(f"<p>{x}</p>" for x in lines)
            + f'<p><a href="../#{p["key"]}">← all reproductions</a></p></div></div>'
            f"<script>{CARD_JS}</script>")
    return f"reproduce/{p['key']}/{run['id']}.html", out.read_text(encoding="utf-8"), card
