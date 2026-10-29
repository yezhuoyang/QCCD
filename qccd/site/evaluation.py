"""The Evaluation page: every number a design is judged by, as a term of a language.

The page is written from the evaluation language itself (`qccd.evalset`): every metric,
requirement and noise channel is printed as the text the interpreter evaluates, every word
it uses is listed with its definition, and every example on the page is computed when the
page is built -- the small programmes from the Language and Rules pages, the shipped
devices, and two published Goal 1 entries recomputed from their bundles and set beside
their official reports.  Nothing on the page is typed in that the language could compute.
"""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

from ..evalset import GROUPS, WORDS, load
from ..evalset.interp import CALL_ALIAS
from ..evalset.syntax import ITEM_NAMES, NOISE_OPS, parse_term
from ..ruleset.syntax import KEYWORDS

ROOT = Path(__file__).resolve().parents[2]

#: The search index's entries for this page: (anchor, heading).
HEADINGS = (
    ("texts", "Three texts and one function"),
    ("language", "The evaluation language"),
    ("words", "The words of an evaluation"),
    ("state", "The state a programme leaves"),
    ("speed", "Speed: how long a programme runs"),
    ("heat", "Heat: what the motion costs"),
    ("hardware", "Hardware: electrodes, switches and DACs"),
    ("circuit", "From a run to a stim circuit"),
    ("noise", "The noise model, qccd-noise@2"),
    ("entry", "A Goal 1 entry, recomputed"),
    ("check", "The noiseless check"),
    ("ler", "The logical error rate and its interval"),
    ("goal1", "Reading the Goal 1 board"),
    ("status", "What is proved, decided, computed and estimated"),
    ("yours", "Writing your own evaluation"),
)

CSS = """
.evl p{max-width:80ch}
.evl .df{margin:14px 0 10px;padding:12px 16px;border-left:3px solid #1c2a4a;background:var(--card);max-width:100ch}
.evl .df>b:first-child,.evl .eg>b:first-child{font-family:var(--sans);font-size:12px;letter-spacing:.06em;text-transform:uppercase;margin-right:8px}
.evl .df>b:first-child{color:#1c2a4a} .evl .df p{margin:6px 0 0} .evl .words{margin-top:8px;color:var(--ink2);font-size:14px}
.evl .eg{margin:8px 0 18px;padding:10px 16px 12px;border-left:3px solid #0b7a4b;background:#f4faf6;font-size:14.5px}
.evl .eg>b:first-child{color:#0b7a4b} .evl .eg p{margin:6px 0 0;max-width:80ch}
.evl .eq{font-family:var(--serif);font-size:16.5px;text-align:center;margin:12px auto;max-width:80ch;line-height:1.75}
.evl pre.ev{background:#f6f7fb;border:1px solid #dfe3ee;border-radius:8px;padding:10px 12px;font-size:13px;line-height:1.55;
  white-space:pre-wrap;margin:8px 0;overflow-x:auto}
.evl pre.ev b{color:#1c2a4a} .evl pre.ev a{color:#0b5cad;text-decoration:none;border-bottom:1px dotted #0b5cad}
.evl pre.ev .s{color:#8a3b12} .evl pre.ev .n{color:#7a1f8a;font-weight:600} .evl pre.ev .c{color:#8a8892;font-style:italic}
.evl pre.ev .k{color:#0b6b54;font-weight:600}
.evl pre.stim{background:#faf9f5;border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:12px;line-height:1.5;
  white-space:pre;overflow-x:auto;margin:4px 0}
.evl .item{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:10px 14px 8px;margin:10px 0}
.evl .item h4{margin:0 0 2px;font:600 14.5px/1.35 var(--sans);color:#1c2a4a}
.evl .item h4 code{font-size:13px;color:#56545e;font-weight:500;margin-left:6px}
.evl .item .meta{font-size:12px;color:var(--ink3);margin:0 0 4px}
.evl .item p{margin:4px 0;font-size:14.5px}
.evl .item .src{font-size:12px;color:var(--ink3)}
.evl table.t{font-size:13px;width:auto;margin:8px 0 12px} .evl table.t td,.evl table.t th{padding:4px 14px 4px 0;text-align:left;vertical-align:top}
.evl table.t td.r,.evl table.t th.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.evl table.t th{font:600 12px var(--sans);color:var(--ink2);border-bottom:1px solid var(--line)}
.evl table.t td{border-bottom:1px solid #f0efea}
.evl table.t code{font-size:12px}
.evl .ok{color:#0b7a4b;font-weight:600} .evl .bad{color:#c62828;font-weight:600} .evl .na{color:#8a8892}
.evl .legend{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 6px}
.evl .legend div{flex:1 1 150px;border:1px solid #dfe3ee;border-radius:8px;padding:8px 10px;font-size:13.5px;font-family:var(--sans)}
.evl .legend b{display:block}
.evl .legend .ex b{color:#0b7a4b} .evl .legend .de b{color:#1c2a4a} .evl .legend .pr b{color:#4a3aa7} .evl .legend .es b{color:#9a6700}
.evl figure.dg{margin:12px 0 8px} .evl figure.dg figcaption{font-size:12.5px;color:var(--ink2);margin-top:6px;line-height:1.45;max-width:90ch}
.evl figure.dg .figsvg{padding:10px 12px} .evl figure.dg svg{display:block;width:auto;max-width:100%;height:auto;margin:0 auto}
.evl details{margin:6px 0 12px} .evl details summary{cursor:pointer;font-family:var(--sans);font-size:13.5px;color:#1c2a4a}
.evl .steps td.l{font-family:var(--mono);font-size:12px;max-width:32ch;overflow-wrap:anywhere}
.evl .steps th code{white-space:normal}
.evl .steps pre.stim{font-size:11.5px}
.evl .steps td.w{font-size:12px;color:var(--ink2);max-width:46ch}
.evl .chips span{display:inline-block;font:12px var(--sans);border:1px solid #c9d1e3;border-radius:10px;padding:0 7px;margin:2px 3px 2px 0}
.evl ul.refs{font-size:13px;color:var(--ink2)}
.evl table.vocab{border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 18px}
.evl table.vocab td{border-top:1px solid #e3e7f0;padding:6px 8px;vertical-align:top}
.evl table.vocab td:first-child{white-space:nowrap;width:1%}
.evl td.ex{color:#0b7a4b;font-weight:600} .evl td.de{color:#1c2a4a;font-weight:600}
.evl td.pr{color:#4a3aa7;font-weight:600} .evl td.es{color:#9a6700;font-weight:600}
"""


# ============================================================================ text

_ITEM_WORDS = set(ITEM_NAMES) | {"before", "after", "on", "unit", "better", "group", "about", "source",
                                 "if", "then", "else", "low", "high", "counted", "as"}


def hl(text: str, names: dict[str, str] | None = None, base: str = "") -> str:
    """An evaluation's text as written: its own words in bold, the rule language's keywords
    in bold, noise operations marked, every word of the vocabulary linked to its definition
    and every metric, param and define linked to where it is written."""
    names = names or {}
    out = []
    prev = ""                      # the last word: a name after `param`, `define` or `metric` is declared
    for m in re.finditer(r'#[^\n]*|"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_@]*|\s+|.', text):
        t = m.group()
        if not t.isspace():
            was, prev = prev, t
            decl = {"param": "p", "define": "d", "metric": "m"}.get(was)
            if decl and re.match(r"[A-Za-z_]", t):
                # marked, and made the anchor by `_anchor_declarations` where nothing else is
                out.append(f'<span data-decl="{decl}-{t}">')
                out.append(f'<a href="{names[t]}">{t}</a>' if t in names else html.escape(t))
                out.append("</span>")
                continue
        if t.startswith("#"):
            out.append(f'<span class="c">{html.escape(t)}</span>')
        elif t.startswith('"'):
            out.append(f'<span class="s">{html.escape(t)}</span>')
        elif t in NOISE_OPS:
            out.append(f'<span class="n">{t}</span>')
        elif t in _ITEM_WORDS:
            out.append(f'<span class="k">{t}</span>')
        elif t in KEYWORDS:
            out.append(f"<b>{t}</b>")
        elif t in names:
            out.append(f'<a href="{names[t]}">{t}</a>')
        elif t in WORDS or t in CALL_ALIAS:
            # a call of `ions(o)` is a different word from the domain `ions`
            key = CALL_ALIAS.get(t, t) if text[m.end():m.end() + 1] == "(" else t
            w = WORDS.get(key) or WORDS.get(t)
            out.append(f'<a href="{base}#w-{key if key in WORDS else t}" '
                       f'title="{html.escape(w.meaning) if w else ""}">{t}</a>')
        else:
            out.append(html.escape(t))
    return "".join(out)


def _anchor_declarations(body: str) -> str:
    """Every `#p-`, `#d-` and `#m-` link of the page lands somewhere: a name that has no anchor
    of its own (a card, a parameter row) gets one at the first place its text declares it."""
    ids = set(re.findall(r'\bid="([^"]+)"', body))

    def one(m):
        key = m.group(1)
        if key in ids:
            return m.group(0)
        ids.add(key)
        return f'<span id="{key}" data-decl="{key}">'
    return re.sub(r'<span data-decl="([^"]+)">', one, body)


def _names(ev) -> dict[str, str]:
    out = {m.name: f"#m-{m.name}" for m in ev.metrics_}
    out.update({d.name: f"#d-{d.name}" for d in ev.defines})
    out.update({p: f"#p-{p}" for p in ev.params})
    return out


def _arxiv(src: str) -> str:
    def one(m):
        a = m.group(1)
        return f'<a href="https://arxiv.org/abs/{a}">arXiv:{a}</a>'
    return re.sub(r"arXiv:(\d{4}\.\d{4,5})", one, html.escape(src))


def num(v, unit: str = "") -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and abs(v) < 1e9):
        s = f"{int(v):,}"
    elif isinstance(v, float):
        a = abs(v)
        s = f"{v:.3g}" if (a < 1e-3 or a >= 1e5) else f"{v:.4g}"
        if "e" in s:
            mant, ex = s.split("e")
            s = f"{mant}×10<sup>{int(ex)}</sup>"
    else:
        s = html.escape(str(v))
    return s + (f" {html.escape(unit)}" if unit else "")


def _metric_card(m, names) -> str:
    better = {"low": "lower is better", "high": "higher is better"}.get(m.better, "")
    meta = " · ".join(x for x in (m.unit, better) if x)
    return (f'<div class="item" id="m-{m.name}"><h4>{html.escape(m.title)}<code>{m.name}</code></h4>'
            f'<p class="meta">{html.escape(meta)}</p>'
            f'<pre class="ev">{hl("metric " + m.name + " = " + _cont(m.text), names)}</pre>'
            + (f"<p>{html.escape(m.about)}</p>" if m.about else "") + "</div>")


def _cont(text: str) -> str:
    """A term written over several lines, its continuation lines indented under the first."""
    return text.replace("\n", "\n    ")


def _define_block(ev, names, which: list[str]) -> str:
    rows = []
    for d in ev.defines:
        if d.name in which:
            head = d.name + (f"({', '.join(d.args)})" if d.args else "")
            rows.append(f'<span id="d-{d.name}"></span>' + hl(f"define {head} = {_cont(d.text)}", names))
    return '<pre class="ev">' + "\n".join(rows) + "</pre>"


def _short_line(line: str) -> str:
    """A stim line for reading: its probability to four significant figures."""
    return re.sub(r"\(([0-9.eE+-]+)\)", lambda m: f"({float(m.group(1)):.4g})", line)


# ============================================================================ computed examples

def _small_programmes() -> list[dict]:
    """The Language and Rules pages' small devices, each running a short programme."""
    from .examples import CHAIN, GRID, RACE, machine, rec, text_of

    specs = [
        ("walk", CHAIN, "a walk to a waiting ion, a cooling, a gate and a readout",
         [rec("init", {"d0": "C3", "d1": "C0"}), rec("shuttle", "d0", ["C3", "C2", "C1", "C0"]),
          rec("cool"), rec("gate", "CX", [["d0", "d1"]]), rec("measure", ["d0", "d1"])]),
        ("junction", GRID, "the same, through a T-junction",
         [rec("init", {"d0": "T0_0v", "d1": "T0_1h"}), rec("shuttle", "d0", ["T0_0v", "J0_1", "T0_1h"]),
          rec("cool"), rec("gate", "CX", [["d0", "d1"]])]),
        ("pair", RACE, "two ions carried together on one waveform",
         [rec("init", {"d0": "S0", "d1": "S4"}), rec("simd", "shuttle", [["d0", "S0", "S1"], ["d1", "S4", "S5"]]),
          rec("simd", "shuttle", [["d0", "S1", "S2"], ["d1", "S5", "S6"]])]),
        ("rotation", RACE, "the whole loop turned by two slots",
         [rec("fill"), rec("rotate", 2)]),
    ]
    ev = load("metrics")
    out = []
    for key, dev, what, records in specs:
        m = machine(dev)
        prog = m.program(key, provenance="off").apply_calls(records).build()
        run = ev.run(prog, m.arch)
        out.append({"key": key, "device": dev["name"], "about": dev["about"], "what": what,
                    "text": text_of(records), "machine": m, "run": run, "ev": ev,
                    "metrics": ev.metrics(run)})
    return out


def _step_text(st) -> str:
    ins = st.instr
    if ins is None:
        return st.label()
    if st.kind == "init":
        return "init " + ", ".join(f"{i}@{s}" for i, s in ins.placement.items())
    if st.kind == "simd":
        parts = []
        for o in st.ops:
            parts.append(f"{o.ions[0]} {'→'.join(o.path)}")
        return f"move ({st.cls}) " + "; ".join(parts)
    if st.kind == "gate":
        sites = {o.site for o in st.ops}
        return f"gate {ins.gate} " + "; ".join(",".join(o.ions) for o in st.ops) + (
            f" @ {st.ops[0].site}" if len(sites) == 1 and st.ops[0].site else "")
    if st.kind in ("measure", "reset"):
        return f"{st.kind} " + ",".join(ins.ions)
    if st.kind == "cool":
        return "cool " + ("all" if not ins.ions else ",".join(ins.ions))
    return st.kind


def _state_table(ex: dict) -> str:
    """One row per step of a small run, every cell the term in its column's head."""
    from ..evalset.interp import Evaluator

    run = ex["run"]
    e = Evaluator(ex["ev"], run)
    cols = [("duration(k)", "µs"), ("time(end(k))", "µs"), ('nbar("d0", end(k))', "quanta"),
            ('nbar("d1", end(k))', "quanta"), ('position("d0", end(k))', "")]
    terms = [parse_term(c) for c, _ in cols]
    head = "".join(f'<th class="r"><code>{html.escape(c)}</code></th>' for c, _ in cols)
    rows = []
    for st in run.steps:
        cells = []
        for t in terms:
            v = e.term(t, {"k": st})
            cells.append(f'<td class="r">{num(v) if not isinstance(v, str) else html.escape(v)}</td>')
        rows.append(f'<tr><td class="l">{html.escape(_step_text(st))}</td>{"".join(cells)}</tr>')
    return (f'<div class="tw"><table class="t steps"><tr><th>step k</th>{head}</tr>{"".join(rows)}</table></div>')


def _metric_table(examples: list[dict], names: list[str], ev) -> str:
    head = "".join(f'<th class="r">{html.escape(x["key"])}<br><span class="na">{html.escape(x["device"])}</span></th>'
                   for x in examples)
    rows = []
    for n in names:
        m = ev.metric(n)
        cells = "".join(f'<td class="r">{num(x["metrics"].get(n))}</td>' for x in examples)
        rows.append(f'<tr><td><a href="#m-{n}"><code>{n}</code></a></td><td class="na">{html.escape(m.unit)}</td>{cells}</tr>')
    return f'<div class="tw"><table class="t"><tr><th>metric</th><th>unit</th>{head}</tr>{"".join(rows)}</table></div>'


def _hardware_rows() -> list[dict]:
    """The hardware metrics on the small devices and the shipped ones, by the language."""
    from ..arch import Architecture
    from ..evalset.interp import Evaluator
    from ..ir.tsir import TSIR
    from .examples import CHAIN, GRID, RING, machine

    from ..evalset.bundle import EXAMPLES, open_bundle

    ev = load("metrics")
    items = [(d["name"], machine(d).arch, d["about"]) for d in (CHAIN, GRID, RING)]
    for name in ("grid9x9", "deck_unit_cell", "cyclone_base", "h2_racetrack", "ring144_24v"):
        p = ROOT / "arch" / f"{name}.arch.json"
        if p.exists():
            items.append((name, Architecture.from_json(json.loads(p.read_text(encoding="utf-8"))), "shipped"))
    for sid in ("os_30293bca0826939b", "os_4c75a8b07249aea2"):
        z = EXAMPLES / f"{sid}.zip"
        if z.exists():
            a = Architecture.from_json(open_bundle(z).doc("device.arch.json"))
            items.append((a.name, a, "a Goal 1 entry"))
    out = []
    for name, arch, about in items:
        site = next(n for n, nd in arch.device.nodes.items() if nd.kind == "site")
        prog = TSIR.from_json({"name": "device", "arch_spec": arch.name, "instructions": [
            {"type": "init", "id": 0, "placement": {"q0": site}}]})
        run = ev.run(prog, arch)
        e = Evaluator(ev, run)
        regime = (f"{arch.control_plane.grouping} channel map" if arch.control_plane.declared
                  else f"{e.value('scheme')} wiring, no map" if e.value("broadcast_wiring") else "direct wiring")
        out.append({"name": name, "about": about, "regime": regime,
                    **{k: e.value(k) for k in ("n_sites", "n_junctions", "n_rails", "places", "electrodes",
                                               "switches", "broadcast_dacs", "compensation_dacs", "dacs")}})
    return out


def _hardware_caption(hw: list[dict]) -> str:
    """What the table shows, in a sentence, from its own rows: the same lattice wired two ways."""
    shipped = next((r for r in hw if r["name"] == "grid9x9" and r["about"] == "shipped"), None)
    entry = next((r for r in hw if r["about"] == "a Goal 1 entry" and r["n_junctions"] > 0), None)
    if not shipped or not entry:
        return ""
    per = shipped["dacs"] / shipped["n_sites"] if shipped["n_sites"] else 0
    return (f'<p>The shipped <code>grid9x9</code> gives every site channels of its own (a {shipped["regime"]}): '
            f'{num(per)} DACs a site, {num(shipped["dacs"])} in all, far over the budget. The Goal 1 entry on '
            f'<code>{html.escape(entry["name"])}</code> has {num(entry["n_sites"])} sites and '
            f'{num(entry["n_junctions"])} junctions, wired with a {html.escape(entry["regime"])}: one waveform bank '
            f'shared by every site, {num(entry["broadcast_dacs"])} DACs for it and {num(entry["compensation_dacs"])} '
            f'for the shims, {num(entry["dacs"])} in all. That is what broadcast wiring buys, and rule R22 is its '
            f'price: one motion a cycle.</p>')


def index_entries() -> list[dict]:
    """The site search's entries for this page: its sections, every metric, every noise channel."""
    out = [{"t": "Evaluation", "d": "every metric, requirement and noise channel as a term; a run as a stim circuit",
            "u": "evaluation/", "k": "page"}]
    out += [{"t": t, "d": "Evaluation", "u": f"evaluation/#{a}", "k": "page"} for a, t in HEADINGS]
    for m in load("metrics").metrics_:
        out.append({"t": f"metric {m.name} · {m.title}", "d": (m.about or m.unit)[:100],
                    "u": f"evaluation/#m-{m.name}", "k": "metric"})
    for c in load("qccd-noise@2").channels:
        out.append({"t": f"noise {c.name} · {c.title}", "d": (c.about or "")[:100],
                    "u": f"evaluation/#c-{c.name}", "k": "noise"})
    return out


_ENTRIES: dict = {}


def _entry(sid: str, sample: bool = False) -> dict | None:
    """A published entry, recomputed from its bundle, beside its official report (once a build)."""
    key = (sid, sample)
    if key not in _ENTRIES:
        _ENTRIES[key] = _entry_uncached(sid, sample)
    return _ENTRIES[key]


def _entry_uncached(sid: str, sample: bool = False) -> dict | None:
    from ..evalset.bundle import EXAMPLES, evaluate_bundle

    z = EXAMPLES / f"{sid}.zip"
    if not z.exists():
        return None
    rep = json.loads((EXAMPLES / f"{sid}.report.json").read_text(encoding="utf-8"))
    r = evaluate_bundle(z, "goal1", sample=sample)
    ler = [s for s in rep["stages"] if s["id"] == "ler"][0]["detail"]
    official = SimpleNamespace(shots=ler["ler"]["shots"], errors=ler["ler"]["errors"])
    via = r.evaluation.metrics(r.run, sample=official, checks={"rules": True, "noiseless": r.noiseless.ok})
    return {"sid": sid, "r": r, "report": rep, "ler": ler, "via_official": via}


def _mutant(entry: dict) -> dict:
    """The entry's programme with its first MS pulse turned the other way round."""
    from ..ir.tsir import TSIR
    from ..qec.check import noiseless_check

    r = entry["r"]
    doc = r.run.prog.to_json()
    which = None
    for ins in doc["instructions"]:
        if ins.get("type") == "gate" and ins.get("gate") == "MS" and ins.get("params"):
            ins["params"] = [[-p[0]] + list(p[1:]) for p in ins["params"]]
            which = ins["id"]
            break
    ev = r.evaluation
    run = ev.run(TSIR.from_json(doc), r.run.arch, experiment=r.experiment,
                 cert=r.bundle.files.get("certificate.qcert.json"))
    chk = noiseless_check(ev.circuit(run).stim())
    return {"instr": which, "check": chk}


def _releases() -> list[dict]:
    from ..qec import MemoryExperiment
    from ..workspace.tasks import list_releases

    out = []
    for rel in list_releases():
        q = rel.manifest.get("qec") or {}
        if q.get("noise") != "qccd-noise@2" or rel.manifest.get("track") == "suite":
            continue
        try:
            exp = MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text())
        except Exception:  # noqa: BLE001 -- a release this build cannot read is left off the table
            continue
        s = exp.summary()
        out.append({"id": rel.id, "title": rel.manifest.get("title", ""), "code": s.get("code"),
                    "distance": s.get("distance"), "rounds": s.get("rounds"),
                    "ions": (s.get("n_data") or 0) + (s.get("n_ancilla") or 0), "k": exp.logical_qubits,
                    "decoder": (q.get("ler") or {}).get("decoder", "auto"),
                    "shots": (q.get("ler") or {}).get("max_shots"), "errors": (q.get("ler") or {}).get("max_errors"),
                    "rank": rel.manifest.get("rank_by")})
    out.sort(key=lambda x: (x["ions"], x["id"]))
    return out


def _agreement() -> dict | None:
    from ..evalset.bundle import EXAMPLES

    p = EXAMPLES / "agreement.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _clock_rows(arch, table: str) -> list[tuple[str, str, str, str]]:
    """(what, time, heat or error, source) for every primitive the clock prices."""
    from ..cost.models import corrected_model

    mo = corrected_model(table)
    out = []
    prim = arch.primitives
    names = {"shuttle_segment": "carry one ion along one rail", "split": "split a chain",
             "merge": "merge two chains", "ion_swap": "swap two ions in a chain"}
    for p, what in names.items():
        if p in prim.curves:
            pt = mo._point(arch, p)
            out.append((what, f"{pt.us:g} µs", _quanta(pt.quanta), getattr(pt, "source", "") or ""))
    for d in (3, 4):
        jp = mo.junction_point(arch, d) if d in prim.degree_curve("junction_cross").by_degree else None
        if jp is not None:
            out.append((f"cross a junction of {d} rails", f"{jp.us:g} µs", _quanta(jp.quanta),
                        getattr(jp, "source", "") or ""))
    ms = prim.scalar("ms_gate")
    out.append(("the two-ion gate MS", f"{ms.get('us')} µs",
                f"ε₀ = {1 - float(ms.get('fidelity_at_n0', 1)):.3g}, {ms.get('error_vs_quanta')}, "
                f"{ms.get('error_vs_chain') or 'no chain law'}", str(ms.get("source", ""))))
    for p, what, key in (("1q_gate", "a one-ion pulse R", "fidelity"), ("measure", "a readout", "fidelity"),
                         ("reset", "a reset", "error"), ("cool", "a cooling (every ion at once)", None)):
        s = prim.scalar(p)
        if key == "fidelity":
            err = f"error {1 - float(s.get('fidelity', 1)):.3g}"
        elif key == "error":
            err = f"error {float(s.get('error', 0)):.3g}"
        else:
            err = "n̄ := 0"
        out.append((what, f"{s.get('us')} µs", err, str(s.get("source", ""))))
    rate = arch.anomalous_rate()
    out.append(("anomalous heating, every ion", "", f"{rate:g} quanta/ms",
                str((arch.heating or {}).get("source", "")) if isinstance(arch.heating, dict) else ""))
    return out


def _quanta(q: float) -> str:
    return f"{q:g} quantum" if q == 1 else f"{q:g} quanta"


def _clock_table(rows) -> str:
    body = "".join(f"<tr><td>{html.escape(a)}</td><td class=\"r\">{html.escape(b)}</td><td>{html.escape(c)}</td>"
                   f"<td class=\"na\">{_arxiv(d) if d.startswith('arXiv') else html.escape(d)}</td></tr>"
                   for a, b, c, d in rows)
    return f'<div class="tw"><table class="t"><tr><th>primitive</th><th class="r">time</th><th>heat / error</th><th>source</th></tr>{body}</table></div>'


def _pipeline_svg() -> str:
    """The function on one picture: three texts in, numbers, verdicts and a circuit out."""
    def box(x, y, w, h, title, sub, fill, stroke):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}"/>'
                f'<text x="{x + w / 2}" y="{y + 20}" text-anchor="middle" font-size="13" font-weight="600" fill="#1a2540">{title}</text>'
                f'<text x="{x + w / 2}" y="{y + 37}" text-anchor="middle" font-size="11" fill="#56545e">{sub}</text>')

    def arrow(x1, y1, x2, y2):
        return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#8a8892" stroke-width="1.4" '
                f'marker-end="url(#ev-ar)"/>')

    parts = ['<svg viewBox="0 0 860 330" width="860" height="330" role="img" '
             'aria-label="three texts in; metrics, verdicts and a circuit out; the logical error rate sampled from the circuit" '
             'font-family="ui-sans-serif,system-ui,sans-serif">',
             '<defs><marker id="ev-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             '<path d="M0 0L10 5L0 10z" fill="#8a8892"/></marker></defs>']
    parts.append(box(10, 10, 180, 48, "the device", ".arch.json: graph, physics, wiring", "#eef3fb", "#9dbfe9"))
    parts.append(box(210, 10, 180, 48, "the programme", ".tsir.json: one instruction a step", "#eef3fb", "#9dbfe9"))
    parts.append(box(600, 10, 250, 48, "the evaluation", ".eval: metrics, requirements, channels", "#f3f0fb", "#b9aee6"))
    parts.append(box(110, 104, 240, 48, "the replay", "the hardware language's semantics", "#fbfaf7", "#cfceca"))
    parts.append(box(110, 192, 240, 48, "the run", "steps, operations, instants, state", "#fbfaf7", "#cfceca"))
    parts.append(arrow(100, 58, 180, 104))
    parts.append(arrow(300, 58, 260, 104))
    parts.append(arrow(230, 152, 230, 192))
    parts.append(box(430, 120, 170, 48, "metrics μ", "exact: a number each", "#eef8f2", "#8fd0ae"))
    parts.append(box(430, 186, 170, 48, "requirements ρ", "decided: holds or fails", "#eef1f7", "#9aa7c4"))
    parts.append(box(430, 252, 170, 48, "noisy circuit C", "exact: stim, line by line", "#eef8f2", "#8fd0ae"))
    for y in (144, 210, 276):
        parts.append(arrow(350, 216, 430, y))
    parts.append(arrow(725, 58, 515, 120))
    parts.append(arrow(725, 58, 600, 200))
    parts.append(arrow(725, 58, 600, 262))
    parts.append(box(650, 252, 200, 48, "logical error rate", "estimated: a sample, ± 95 %", "#fbf6e9", "#e0c27a"))
    parts.append(arrow(600, 276, 650, 276))
    parts.append('</svg>')
    return '<figure class="dg"><div class="figsvg">' + "".join(parts) + ('</div><figcaption>The device and the '
            'programme are replayed once; the evaluation is read over the run. Metrics and the circuit are '
            'functions of the three texts, the requirements formulas over them, and the logical error rate the '
            'one number that is sampled.</figcaption></figure>')


def _steps_table(entry: dict, pick) -> str:
    """Hardware steps beside the stim lines they become, each line with what wrote it."""
    circ = entry["r"].circuit
    rows = []
    for tr in circ.trace:
        st = tr["step"]
        if st is None or not pick(st):
            continue
        lines = circ.lines[tr["a"]:tr["b"]]
        who = circ.origin[tr["a"]:tr["b"]]
        shown = [_short_line(ln) for ln in lines]
        width = max((len(x) for x in shown), default=0)
        txt = "\n".join(f"{x:<{width}s}  ← {w}" for x, w in zip(shown, who)) or "(nothing on the qubits)"
        dur = f"{st.duration:g} µs" if st.duration else "0"
        rows.append(f'<tr><td class="l">{html.escape(_step_text(st))}</td><td class="r">{dur}</td>'
                    f'<td><pre class="stim">{html.escape(txt)}</pre></td></tr>')
    return ('<div class="tw"><table class="t steps"><tr><th>hardware step</th><th class="r">duration</th>'
            '<th>the stim it becomes, and what wrote each line</th></tr>' + "".join(rows) + "</table></div>")


def _quiet_frame_change(entry: dict, pick):
    """The first shown step made of frame changes alone that takes time yet writes no noise on
    their ions, with those ions' qubits; None when no shown step is one (then the page says
    nothing about it)."""
    circ = entry["r"].circuit
    for tr in circ.trace:
        st = tr["step"]
        if st is None or not pick(st) or not st.ops or not st.duration:
            continue
        if not all(o.kind == "gate1" and o.gate == "VZ" for o in st.ops):
            continue
        qs = sorted({circ.qubit_of_ion[i] for o in st.ops for i in o.ions})
        own = {str(q) for q in qs}
        noisy = any(own & set(ln.split()[1:])
                    for ln, who in zip(circ.lines[tr["a"]:tr["b"]], circ.origin[tr["a"]:tr["b"]])
                    if who != "translation")
        if not noisy:
            return st, qs
    return None


def _and(xs) -> str:
    xs = [str(x) for x in xs]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def num6(v) -> str:
    return f"{v:.6g}" if isinstance(v, float) else num(v)


def _vs(a, b, rel=1e-9) -> str:
    try:
        same = a == b or abs(float(a) - float(b)) <= rel * max(abs(float(a)), abs(float(b)))
    except (TypeError, ValueError):
        same = a == b
    return '<span class="ok">equal</span>' if same else '<span class="bad">differs</span>'


def files() -> list[tuple[str, bytes]]:
    """What the page links to: the shipped evaluations, and each example entry's circuit."""
    from ..evalset import LIBRARY
    out = [(f"evaluation/{p.name}", p.read_bytes()) for p in sorted(LIBRARY.glob("*.eval"))]
    for sid in ("os_30293bca0826939b", "os_4c75a8b07249aea2"):
        try:
            e = _entry(sid)
        except Exception:  # noqa: BLE001 -- a build without the QEC tools links no circuit
            e = None
        if e:
            out.append((f"evaluation/{sid}.stim", ("\n".join(e["r"].circuit.lines) + "\n").encode("utf-8")))
    return out


# ============================================================================ the page

def page(PAGE: str, STYLE: str, device_svg=None) -> str:
    from ..evalset.interp import TRANSLATION, Evaluator
    from ..evalset.syntax import __doc__ as SYNTAX_DOC

    goal = load("goal1")
    mets = load("metrics")
    n2 = load("qccd-noise@2")
    n1 = load("qccd-noise@1")
    names = _names(goal)
    ex = _small_programmes()
    hw = _hardware_rows()
    entry = _entry("os_30293bca0826939b", sample=True)
    entry2 = _entry("os_4c75a8b07249aea2")
    rels = _releases()
    agree = _agreement()
    b: list[str] = []
    w = b.append

    # ---------------------------------------------------------------- the opening
    w('<h1>Evaluation</h1><div class="evl">')
    w('<p class="sub">A design is judged by numbers: how long its programme runs, how hot it makes its ions, '
      'how much hardware it takes, and how often the logical qubit it stores is lost. On this website every '
      'one of those numbers is a term of a small language, printed on this page, evaluated on three texts: '
      'the device, the programme, and the evaluation. Nothing else is read, so every number on the '
      'leaderboard can be recomputed from the files it came from, and traced to the line of text that '
      'defines it.</p>')
    w('<p class="sub">The language is the <a href="../rules/">Rules</a> page&rsquo;s, extended. A rule is a '
      'formula that holds or fails; an evaluation adds named numbers (<code>metric</code>), the assertions a '
      'design must meet to be ranked (<code>require</code>), and noise (<code>channel</code>): a clause that '
      'says which Pauli error goes where in the circuit a run becomes. The noisy circuit the '
      '<a href="../board/goal1/">Goal 1</a> board samples is the programme&rsquo;s stabilizer translation with '
      'those clauses applied. The grader reads these texts: every number it publishes &mdash; the metrics, the '
      'hardware budget, the noisy circuit and its error budget &mdash; is computed by the interpreter of the '
      'language from the text on this page, and the same interpreter runs in every workspace. The '
      '<a href="../studio.html#design">Design page</a> reads the same texts in the browser with a second interpreter, '
      'held to the first metric by metric: every number it shows (cost, steps, runtime, heat, DACs, the noise '
      'each budget line books) is a metric of these texts, and its Report checks itself against the '
      'grader&rsquo;s values.</p>')
    w('<div class="legend">'
      '<div class="ex"><b>exact</b>a function of the three texts: anyone recomputes it to the last digit</div>'
      '<div class="de"><b>decided</b>a formula that holds or fails, naming the values that make it fail</div>'
      '<div class="pr"><b>proved</b>decided by a checker whose correctness is proved in Lean</div>'
      '<div class="es"><b>estimated</b>a sample of a distribution the three texts fix, with its 95&nbsp;% interval</div>'
      '</div>')
    w('<ul class="toc2">' + "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in HEADINGS) + '</ul>')

    # ---------------------------------------------------------------- 1 texts
    w('<h2 id="texts">Three texts and one function</h2>')
    w('<div class="df"><b>Definition 1</b>(An evaluation.) Let <i>D</i> be a device &mdash; its graph of '
      'sites, junctions and rails, its zones, the (time, heat) curves of its transport primitives, its gates, '
      'its heating, its wiring and its budget &mdash; and <i>P</i> a programme on it, a list of instructions '
      'of the <a href="../language/">hardware language</a>. An <i>evaluation</i> <i>E</i> is a text in the '
      'language of the next section. Its meaning on (<i>D</i>, <i>P</i>) is the triple'
      '<div class="eq">⟦<i>E</i>⟧(<i>D</i>, <i>P</i>) = (μ, ρ, <i>C</i>)</div>'
      '<p>where μ gives every metric a value, ρ gives every requirement a verdict, and <i>C</i> is the noisy '
      'circuit: a stim circuit with the experiment&rsquo;s detectors. The logical error rate is a statistic '
      'of <i>C</i>, LER(<i>C</i>) = Pr[the decoder&rsquo;s correction leaves a logical observable wrong].</p>'
      '<p class="words">In words: the device and the programme are replayed once, which fixes where every ion '
      'is, how hot it is and what the clock says at every instant; the evaluation is then read over that '
      'record.</p></div>')
    w(_pipeline_svg())
    w('<div class="df"><b>Proposition 1</b>(Determinism.) μ, ρ and <i>C</i> are functions of '
      '(<i>D</i>, <i>P</i>, <i>E</i>).'
      '<p><i>Proof sketch.</i> The replay is a deterministic transition system: each instruction&rsquo;s '
      'duration and heat are read off the device&rsquo;s tables (<a href="#state">below</a>). Every word of '
      'the vocabulary reads the device, the programme, the run the replay produced, or a parameter written '
      'in <i>E</i>; the interpreter evaluates terms by structural recursion, with no clock, no random number '
      'and no file besides the three. &#8718;</p>'
      '<p class="words">So the grader is an interpreter of the text, not an authority: a second interpreter '
      'must agree with it, and where it does not, one of them is wrong in a way the text decides.</p></div>')
    if agree:
        rows = "".join(f'<tr><td><code>{html.escape(k)}</code></td><td class="r">{v["designs"]}</td>'
                       f'<td class="r">{v["same"]}</td><td class="r">{v["metrics_equal"]}</td></tr>'
                       for k, v in sorted(agree["by_release"].items()))
        w(f'<div class="eg"><b>Checked</b><p>Until 2026-10-26 the grader wrote its circuit with a '
          f'hand-written extraction; since then it reads the text. The switch changed no circuit: on every Goal 1 '
          f'design we hold, {agree["designs"]} in all, from the 10-ion repetition code to the 180-ion bivariate '
          f'bicycle code, the circuit the text writes and the one the old extraction wrote are the same qubit by '
          f'qubit (<a href="#circuit">what &ldquo;the same&rdquo; means</a>), every channel of the error budget '
          f'agrees to 10<sup>−9</sup>, and so does every metric the board shows. Every logical error rate published '
          f'before the switch is therefore a sample of the circuit on this page.'
          f'</p><div class="tw"><table class="t"><tr><th>release</th><th class="r">designs</th>'
          f'<th class="r">same circuit</th><th class="r">same metrics</th></tr>{rows}</table></div></div>')

    # ---------------------------------------------------------------- 2 the language
    w('<h2 id="language">The evaluation language</h2>')
    w('<p>An evaluation is a text file. Its terms and formulas are the rule language&rsquo;s, unchanged '
      '&mdash; quantifiers over finite sets, comparisons, arithmetic, <code>count</code>, <code>sum</code>, '
      '<code>max</code>, <code>min</code> and set builders &mdash; with one more term, '
      '<code>if C then a else b</code>. What it adds are items:</p>')
    grammar = SYNTAX_DOC.split("Grammar (EBNF, extending the rule language's; `#` starts a comment):", 1)[1]
    grammar = grammar.split("A `define` names a term", 1)[0].strip("\n")
    w('<pre class="ev">' + hl("\n".join(ln[4:] if ln.startswith("    ") else ln for ln in grammar.splitlines())) + '</pre>')
    w('<div class="tw"><table class="t">'
      '<tr><th>item</th><th>what it says</th></tr>'
      '<tr><td><code>param n = term</code></td><td>a number the evaluation fixes, said once, with where it comes from</td></tr>'
      '<tr><td><code>define f(x, …) = term</code></td><td>a named term, with or without arguments; it may use earlier names, never itself</td></tr>'
      '<tr><td><code>metric n "title" … = term</code></td><td>a number every design gets, with its unit and which way is better; '
      'with <code>clock "table"</code>, on a replay at that table&rsquo;s operating points</td></tr>'
      '<tr><td><code>require ID "title" formula</code></td><td>what must hold for a design to be ranked</td></tr>'
      '<tr><td><code>channel n "title" after x in D where C: OP(p) on S</code></td><td>noise: for every binding of <i>x</i> '
      'that satisfies <i>C</i>, the Pauli channel OP with probability <i>p</i> on the ions <i>S</i>, just after '
      '(or <code>before</code>) what <i>x</i> names; <code>counted as k</code> books its expected faults under '
      'the budget line <i>k</i>, and <code>counted as k₁ = t₁, k₂ = t₂</code> shares them out in proportion to '
      'the terms</td></tr>'
      '<tr><td><code>include</code>, <code>clock</code>, <code>rules</code>, <code>rank</code></td><td>another '
      'evaluation&rsquo;s items; the operating-point table the replay prices at; the rule set the programme is '
      'judged by; the metric a board ranks by</td></tr></table></div>')
    w('<div class="df"><b>Definition 2</b>(What a channel writes.) For a channel '
      '<code>after x₁ in D₁, …, xₙ in Dₙ where C: OP(p) on S</code> and every binding of '
      '<i>x</i>₁…<i>x</i>ₙ at which <i>C</i> holds, the circuit carries <code>OP(p) q₁ … qₘ</code>, the '
      '<i>q</i> being the qubits of the ions <i>S</i>, immediately after (for <code>before</code>, immediately '
      'before) the translation of what <i>x</i>₁ names; <i>x</i>₁ ranges over operations or over steps. A '
      'probability of 0, or no ions, writes nothing.</div>')
    w('<div class="eg"><b>Example</b><p>Goal 1&rsquo;s evaluation, whole. It takes the board&rsquo;s metrics '
      'and hardware budget from <a href="#hardware">board.eval</a> and its noise from '
      '<a href="#noise">qccd-noise@2</a>, sets the release&rsquo;s budget and adds three requirements of its own. '
      '<a href="goal1.eval">Download it</a>, or <a href="board.eval">board.eval</a>, '
      '<a href="metrics.eval">metrics.eval</a>, <a href="qccd-noise@2.eval">qccd-noise@2.eval</a> and '
      '<a href="qccd-noise@1.eval">qccd-noise@1.eval</a>.</p>'
      f'<pre class="ev">{hl(goal.text(), names)}</pre></div>')

    # ---------------------------------------------------------------- 3 the words
    w('<h2 id="words">The words of an evaluation</h2>')
    w('<p>Every word a term may use, with what it means. A word the <a href="../rules/#words">rule '
      'language</a> also has means the same thing here. The instants are the evaluation&rsquo;s own: the '
      'start and the end of every step, because a number like the hottest an ion gets needs the state at the '
      'end of a step, before a cooling resets it.</p>')
    for g, label in GROUPS:
        ws = [x for x in WORDS.values() if x.group == g]
        if not ws:
            continue
        rows = "".join(f'<tr id="w-{x.name}"><td><code>{html.escape(x.signature)}</code></td>'
                       f'<td>{html.escape(x.meaning)}</td></tr>' for x in ws)
        w(f'<h3>{html.escape(label)}</h3><table class="vocab">{rows}</table>')

    # ---------------------------------------------------------------- 4 the state
    w('<h2 id="state">The state a programme leaves</h2>')
    w('<p>Every metric reads the run, and the run is the replay: the semantics of the hardware language, '
      'the same one the rules judge. It is worth writing out, because every number below inherits it.</p>')
    w('<div class="df"><b>Definition 3</b>(The run.) The steps <i>k</i> = 1 … <i>n</i> are the '
      'programme&rsquo;s instructions, in order. At each instant <i>t</i> &mdash; start(<i>k</i>) and '
      'end(<i>k</i>) &mdash; the state is position(<i>i</i>, <i>t</i>), occupancy(<i>x</i>, <i>t</i>), the '
      'motional energy n̄(<i>i</i>, <i>t</i>) and time(<i>t</i>). With τ and <i>q</i> the time and heat of a '
      'primitive at the clock&rsquo;s operating point, and Γ the anomalous heating rate:'
      '<div class="eq">time(end(<i>k</i>)) = time(start(<i>k</i>)) + duration(<i>k</i>), &nbsp; '
      'time(start(<i>k</i>+1)) = time(end(<i>k</i>))</div>'
      '<div class="eq">duration(<i>k</i>) = max<sub><i>i</i> moved</sub> [ Σ<sub>rails <i>r</i> of its route</sub> '
      'max(τ<sub>shuttle</sub>, [deg(to(<i>r</i>)) ≥ 3]·τ<sub>junction</sub>) + Σ<sub><i>e</i> ∈ entails(<i>i</i>)</sub> '
      'τ<sub><i>e</i></sub> ]</div>'
      '<div class="eq">n̄(<i>i</i>, end(<i>k</i>)) = n̄(<i>i</i>, start(<i>k</i>)) + [<i>i</i> moved]·( Σ<sub><i>r</i></sub> '
      '<i>q</i><sub>shuttle</sub> + Σ<sub><i>r</i></sub>[deg ≥ 3]·<i>q</i><sub>junction</sub> + Σ<sub><i>e</i></sub> '
      '<i>q</i><sub><i>e</i></sub> ) + Γ·duration(<i>k</i>)</div>'
      '<div class="eq">n̄(<i>i</i>, start(<i>k</i>+1)) = 0 if step <i>k</i> cools <i>i</i>, else n̄(<i>i</i>, end(<i>k</i>))</div>'
      '<p>A transport step lasts as long as its slowest ion; a rotation by δ slots is |δ| such cycles. A gate '
      'step lasts τ<sub>MS</sub> or τ<sub>1q</sub> (a frame change VZ three one-ion pulses, unless the device '
      'declares a one-pulse Z gate); a measurement, reset or cooling its own τ; <code>init</code> and '
      '<code>barrier</code> no time at all. Splits and merges are what a move <a href="#w-entails">entails</a>: '
      'leaving a site where other ions stay, joining one where ions wait, or docking.</p>'
      '<p class="words">In words: the clock adds up the steps; a step waits for its slowest ion; every rail, '
      'junction, split and merge heats the ion carried through it, every microsecond heats every ion, and a '
      'cooling sets the ions it cools back to zero at the end of its step.</p></div>')
    junction = next(x for x in ex if x["key"] == "junction")
    w(f'<div class="eg"><b>Example</b><p>The Rules page&rsquo;s walk through a T-junction, on {html.escape(junction["about"])}. '
      f'Every cell is the term in its column&rsquo;s head, evaluated at that row&rsquo;s step <i>k</i>. The walk '
      f'is charged two rails, the junction&rsquo;s entry and the merge into the trap where d1 waits; every ion '
      f'heats a little with the clock; the cooling takes both back to zero at the end of its step, and the gate '
      f'meets them cold.</p><pre class="ev">{html.escape(junction["text"])}</pre>'
      f'{_state_table(junction)}</div>')
    w('<p>Two clocks appear on this page. The small examples run on the website&rsquo;s default device '
      'document; Goal 1 runs on the cited Helios-class physics every one of its releases fixes '
      '(<code>clock "quantinuum"</code>), and a design may not change it.</p>')
    g1arch = entry["r"].run.arch if entry else None
    if g1arch is not None:
        w('<details open><summary>Goal 1&rsquo;s clock, primitive by primitive</summary>'
          + _clock_table(_clock_rows(g1arch, "quantinuum")) + '</details>')
    w('<details><summary>The examples&rsquo; clock</summary>'
      + _clock_table(_clock_rows(ex[0]["machine"].arch, "qccdsim_jones")) + '</details>')

    # ---------------------------------------------------------------- 5 speed
    w('<h2 id="speed">Speed: how long a programme runs</h2>')
    w('<p>Time is the first thing a design spends. Under <a href="#noise">qccd-noise@2</a> every '
      'microsecond an ion is not operated on is memory error, and the coherent part grows with the square of '
      'each wait, so a slow schedule pays twice. The speed metrics read the clock and the steps:</p>')
    for n in ("T", "T_jones", "T_transport", "T_quantinuum", "time_transport", "time_gates", "time_cooling",
              "time_spam", "instructions", "two_ion_gates", "transport_instructions", "ions_per_instruction",
              "cost", "depth"):
        w(_metric_card(mets.metric(n), names))
    w(_define_block(mets, names, ["is_transport"]))
    w('<p><code>ions_per_instruction</code> is how much of the machine one waveform moves: on a broadcast-wired '
      'device a rotation moves every ion on its loop for the price of one instruction, which is why the '
      'rings run their rounds in few instructions.</p>')
    w('<div class="eg"><b>Example</b><p>Four short programmes on the small devices of the Language and Rules '
      'pages, every value computed by the text above:</p>'
      + "".join(f'<p><b>{html.escape(x["key"])}</b> &mdash; {html.escape(x["what"])}, on '
                f'{html.escape(x["device"])}:</p><pre class="ev">{html.escape(x["text"])}</pre>' for x in ex)
      + _metric_table(ex, ["T", "time_transport", "time_gates", "time_cooling", "time_spam", "instructions",
                           "two_ion_gates", "transport_instructions", "ions_per_instruction", "cost", "depth"], mets)
      + '<p>The rotation moves all eight ions in each instruction: its <code>ions_per_instruction</code> is 8, '
        'against 2 for the pair and 1 for the walks.</p></div>')

    # ---------------------------------------------------------------- 6 heat
    w('<h2 id="heat">Heat: what the motion costs</h2>')
    w('<p>Every operation on a trapped-ion machine is paid for in motional quanta. A two-ion gate shares a '
      'motional mode between its ions, and a hot mode blurs the spin-dependent force that entangles them, so '
      'the gate&rsquo;s error grows with the heat it meets (rule R16), and rule R7 refuses a gate above the '
      'device&rsquo;s budget. The device&rsquo;s law is part of the metrics file:</p>')
    w(_define_block(mets, names, ["eps0", "kappa", "Nref", "chain_factor", "chain_ratio", "ms_error",
                                  "gate_nbar", "gate_chain"]))
    w('<p class="words">ε(n̄, N) = ε₀ + κ·n̄ in a chain no longer than the reference; in a longer one, '
      'ε = (ε₀ − κ/2) + (κ/2)·r(N)·(2n̄ + 1), where r(N) is Murali&rsquo;s laser-instability factor N/ln N '
      'relative to the reference length (arXiv:2004.04706, Eq. 1): the two forms are the same function at '
      'the reference, and a longer chain is never better than it.</p>')
    for n in ("peak_nbar", "gate_nbar_max", "gate_nbar_mean", "junction_crossings", "transport_quanta",
              "total_quanta", "n_coolings", "gate_error_sum"):
        w(_metric_card(mets.metric(n), names))
    w('<div class="eg"><b>Example</b><p>The same four programmes. Both walks end by joining a waiting ion, '
      'and on this clock the merge is the dear part, 6 quanta against a tenth for a rail; the walk through the '
      'T-junction pays 3 more for the junction. Each still reaches its gate cold, because the cooling comes '
      'between the walk and the gate; with the cooling first, R7 would refuse the junction walk&rsquo;s gate.</p>'
      + _metric_table(ex, ["peak_nbar", "gate_nbar_max", "gate_nbar_mean", "junction_crossings",
                           "transport_quanta", "total_quanta", "n_coolings", "gate_error_sum"], mets) + '</div>')

    # ---------------------------------------------------------------- 7 hardware
    w('<h2 id="hardware">Hardware: electrodes, switches and DACs</h2>')
    w('<p>The half of a design that is not time or heat is what it costs to build and drive. Each trap '
      'carries DC electrodes that shape its wells; each electrode is wired through a two-way switch; and '
      'each needs a digital-to-analogue converter (DAC) to play its waveform &mdash; one per electrode when '
      'wired directly, which is the first thing that does not scale. Broadcast wiring shares one DAC among '
      'every electrode that plays the same role, which holds the count near constant at the price that one '
      'cycle carries one motion (rule R22). The floors below are the deck&rsquo;s array (pp. 19&ndash;20) '
      'and WISE (<a href="https://arxiv.org/abs/2305.12773">arXiv:2305.12773</a>): a device may declare more, '
      'never less.</p>')
    w('<pre class="ev">' + "\n".join(f'<span id="p-{p}"></span>' + hl(f"param {p} = {mets.param_text[p]}", names)
                                       for p in ("least_electrodes_per_trap", "least_compensation_per_trap",
                                                 "most_shims_per_dac", "least_channels_per_site")) + '</pre>')
    for n in ("n_sites", "n_junctions", "n_rails", "places", "electrodes", "switches", "dacs"):
        w(_metric_card(mets.metric(n), names))
    w(_define_block(mets, names, ["scheme", "broadcast_wiring", "per_trap", "per_junction", "least_compensation",
                                  "least_driven", "broadcast_dacs", "compensation_dacs"]))
    w('<div class="df"><b>The three regimes</b><p>Written out from the definitions above, with <i>N</i> sites '
      'and <i>J</i> junctions:</p>'
      '<div class="eq">direct: &nbsp; DACs = electrodes = <i>N</i>·max(<i>p</i>, 24) + <i>J</i>·max(<i>q</i>, 2·max(<i>p</i>, 24))</div>'
      '<div class="eq">broadcast, no map: &nbsp; DACs = max(<i>d</i>, 32) + max(⌈<i>N</i>·<i>c</i>/<i>M</i>⌉, ⌈8<i>N</i>/100⌉)</div>'
      '<div class="eq">channel map: &nbsp; DACs = (⌈32·<i>B</i>/<i>b</i>⌉ if <i>b</i> &lt; 32, else <i>B</i>) + max(<i>C</i>, ⌈8<i>N</i>/100⌉)</div>'
      '<p>with <i>p</i>, <i>q</i> the declared electrodes per trap and per junction, <i>d</i> the declared '
      'broadcast DACs, <i>c</i> the shims per trap, <i>M</i> the demultiplexer&rsquo;s ratio, <i>B</i> and '
      '<i>C</i> the shared and compensation channels the map lists, and <i>b</i> the fewest channels driving '
      'any site.</p></div>')
    board = load("board")
    w('<div class="df"><b>The budget, as requirements</b><p>Every task release fixes a hardware budget with '
      'its physics, and every board judges a design&rsquo;s device by two requirements of the same language. '
      'The grader&rsquo;s device stage evaluates them with the release&rsquo;s numbers; a design over budget '
      'keeps its numbers and is not ranked.</p>'
      f'<pre class="ev">{hl(board.text(), _names(board))}</pre></div>')
    rows = "".join(f'<tr><td><code>{html.escape(r["name"])}</code>'
                   + (f' <span class="na">{html.escape(r["about"])}</span>' if r["about"] in ("shipped", "a Goal 1 entry") else "")
                   + f'</td><td>{html.escape(str(r["regime"]))}</td>'
                   f'<td class="r">{num(r["n_sites"])}</td><td class="r">{num(r["n_junctions"])}</td>'
                   f'<td class="r">{num(r["electrodes"])}</td><td class="r">{num(r["switches"])}</td>'
                   f'<td class="r">{num(r["broadcast_dacs"])} + {num(r["compensation_dacs"])}</td>'
                   f'<td class="r"><b>{num(r["dacs"])}</b></td>'
                   f'<td>{"<span class=ok>holds</span>" if r["dacs"] <= 128 else "<span class=bad>fails</span>"}</td></tr>'
                   for r in hw)
    w('<div class="eg"><b>Example</b><p>The small devices and the devices the repository ships, by the text '
      'above; the last column is requirement B1 of every board, <code>dacs &lt;= max_dacs</code>, with Goal '
      '1&rsquo;s <code>max_dacs = 128</code>. The grader&rsquo;s device stage evaluates exactly these two '
      'requirements, with the release&rsquo;s numbers.</p><div class="tw"><table class="t"><tr><th>device</th><th>wiring</th><th class="r">sites</th>'
      '<th class="r">junctions</th><th class="r">electrodes</th><th class="r">switches</th>'
      '<th class="r">shared + shim DACs</th><th class="r">DACs</th><th>B1</th></tr>' + rows + '</table></div>'
      + _hardware_caption(hw) + '</div>')
    w('<p class="note">Chip area is not a metric: no grader measures it, so no design is ranked by it.</p>')

    # ---------------------------------------------------------------- 8 the circuit
    w('<h2 id="circuit">From a run to a stim circuit</h2>')
    w('<p>A memory experiment is a circuit, written in OpenQASM, together with its <i>detectors</i> &mdash; '
      'parities of its measurement results that are 0 when nothing went wrong &mdash; and its logical '
      'observables. To learn how often the logical qubit is lost on a design, the compiled programme is turned '
      'back into a circuit, with noise where the run says it belongs, and sampled.</p>')
    w('<div class="df"><b>Definition 4</b>(The qubits.) The ion holding circuit qubit <i>q</i> is stim qubit '
      '<i>q</i>; ions that hold no circuit qubit come after, in the order they are placed. Which ion holds '
      'which qubit is the programme&rsquo;s own statement, else its certificate&rsquo;s; a certificate is '
      'cross-checked against the measurement order, never believed.</div>')
    tr_rows = "".join(f'<tr><td><code>{html.escape(a)}</code></td><td>{html.escape(c)}</td>'
                      f'<td class="na">{html.escape(d)}</td></tr>' for a, c, d in TRANSLATION)
    w('<div class="df"><b>Definition 5</b>(The translation.) Every operation becomes stabilizer operations on '
      'its ions&rsquo; qubits:<div class="tw"><table class="t"><tr><th>hardware</th><th>stim</th><th>because</th></tr>'
      + tr_rows + '</table></div></div>')
    w('<div class="df"><b>Proposition 2</b>(Transport is the identity on the qubits.) Carrying an ion changes '
      'its position and its heat, never its qubit: the translation of a programme&rsquo;s transport and '
      'cooling steps is empty, and they reach the circuit only through the noise their durations and their '
      'heat put there.<p class="words">Which is why the same circuit run on two designs gives two logical '
      'error rates: the gates are the same, the noise is not.</p></div>')
    w('<div class="df"><b>Proposition 3</b>(The Clifford fragment.) An angle that is not a multiple of π/2 '
      'has no stabilizer translation, and the programme is refused rather than rounded into a different '
      'circuit. Every memory experiment on the leaderboard is Clifford.</div>')
    w('<div class="df"><b>Definition 6</b>(Records.) The <i>k</i>-th measurement of circuit qubit <i>q</i> in '
      'the programme writes the classical bit the <i>k</i>-th <code>measure q[…] -&gt; c[b]</code> of <i>q</i> '
      'writes in the source circuit: a compiler may reorder operations on different qubits, never two '
      'measurements of one. Each detector and observable then becomes a <code>DETECTOR</code> or '
      '<code>OBSERVABLE_INCLUDE</code> line over those records. A programme that measures a qubit more or '
      'fewer times than the circuit does is refused. Where an experiment&rsquo;s programme stops after its '
      'last round, its data readout is <i>appended</i>: one measurement of every data ion in the '
      'experiment&rsquo;s basis, after the last step.</div>')
    w('<div class="df"><b>Definition 7</b>(Where noise goes.) The circuit is the preparation, then every step '
      'in order, then the appended readout and the detectors. Inside one step:'
      '<ol><li>every <code>before</code> channel bound to the step;</li>'
      '<li>every <code>before</code> channel bound to one of its operations, channel by channel;</li>'
      '<li>for each operation in order: its translation, then every <code>after</code> channel bound to it;</li>'
      '<li>every <code>after</code> channel bound to the step.</li></ol>'
      '<p>Two circuits are <i>the same</i> when every qubit sees the same operations in the same order, and the '
      'measurements come in the same order: operations on different qubits commute, so such circuits sample '
      'the same distribution. That is the sense in which this page&rsquo;s circuit is the grader&rsquo;s.</p></div>')

    # ---------------------------------------------------------------- 9 noise
    n2names = _names(n2)
    w('<h2 id="noise">The noise model, qccd-noise@2</h2>')
    w(f'<p>{html.escape(n2.about)}</p>')
    e2 = Evaluator(n2, entry["r"].run) if entry else None
    prow = []
    for p in ("memory_rate", "coherent_rate", "nbar_floor", "crosstalk_near", "crosstalk_far", "crosstalk_reset"):
        val = e2.value(p) if e2 else None
        prow.append(f'<span id="p-{p}"></span>' + hl(f"param {p} = {n2.param_text[p]}", n2names)
                    + (f'   <span class="c"># = {val:.4g}</span>' if isinstance(val, float) else ""))
    w('<pre class="ev">' + "\n".join(prow) + '</pre>')
    w(_define_block(n2, n2names, ["near", "far"]))
    for c in n2.channels:
        w(f'<div class="item" id="c-{c.name}"><h4>{html.escape(c.title)}<code>{c.name}</code></h4>'
          f'<pre class="ev">{hl("channel " + c.name + " " + chr(34) + c.title + chr(34) + chr(10) + "  " + c.text.replace(chr(10), chr(10) + "  "), n2names)}</pre>'
          + (f'<p>{html.escape(c.about)}</p>' if c.about else "")
          + (f'<p class="src">{_arxiv(c.source)}</p>' if c.source else "") + '</div>')
    w('<p>Read as a list, the model says where every error a trapped-ion machine is measured to make enters '
      'the circuit: at the gates, at the readouts and resets, beside a readout, and with the clock. What it '
      'does not say is said in its <code>about</code>: no error per rail, junction, split or swap (transport '
      'pays in time and heat, which <code>memory</code>, <code>memory_coherent</code> and <code>ms</code> '
      'charge), no ion loss, no crosstalk from a two-ion gate.</p>')
    # its one channel @2 does not have keeps a card of its own: a studio page priced on another
    # clock than Goal 1's books under qccd-noise@1 and links each budget line here
    n1only = [c for c in n1.channels if c.name not in {x.name for x in n2.channels}]
    n1names = _names(n1)
    w('<details><summary>The older model, qccd-noise@1, which the first boards were graded under</summary>'
      f'<p>{html.escape(n1.about)}</p><pre class="ev">{hl(n1.text(), n1names)}</pre>'
      + "".join(f'<div class="item" id="c-{c.name}"><h4>{html.escape(c.title)}<code>{c.name}</code></h4>'
                f'<pre class="ev">{hl("channel " + c.name + " " + chr(34) + c.title + chr(34) + chr(10) + "  " + c.text.replace(chr(10), chr(10) + "  "), n1names)}</pre>'
                + (f'<p>{html.escape(c.about)}</p>' if c.about else "") + '</div>' for c in n1only)
      + '</details>')

    # ---------------------------------------------------------------- 10 the entry
    w('<h2 id="entry">A Goal 1 entry, recomputed</h2>')
    if entry:
        r = entry["r"]
        rep = entry["report"]
        mets_rep = rep["metrics"]
        run = r.run
        w('<p>The repetition code&rsquo;s two-basis memory, three rounds, ten ions, on a twelve-site linear '
          'chain: one of the entries on the <a href="../board/goal1/">Goal 1 board</a>, submitted by John '
          f'Zhuoyang Ye (<code>{html.escape(entry["sid"])}</code>). Its bundle &mdash; the device, the '
          'programme and the certificate &mdash; is evaluated here by the text above, and set beside the '
          'official report.</p>')
        if device_svg is not None:
            w('<figure class="dg"><div class="figsvg">' + device_svg(SimpleNamespace(arch=run.arch, name=run.arch.name))
              + f'</div><figcaption>The design, {html.escape(run.arch.name)}: twelve trap sites in a line, no '
                f'junctions.</figcaption></figure>')
        pairs = [("T", "T_quantinuum"), ("instructions", "instructions"), ("transport_instructions", "transport_instructions"),
                 ("ions_per_instruction", "ions_per_instruction"), ("dacs", "dacs")]
        rows = "".join(f'<tr><td><a href="#m-{a}"><code>{a}</code></a></td><td class="r">{num6(r.metrics[a])}</td>'
                       f'<td class="r">{num6(mets_rep[b]["value"])}</td><td>{_vs(r.metrics[a], mets_rep[b]["value"])}</td></tr>'
                       for a, b in pairs)
        w('<div class="eg"><b>The metrics</b><div class="tw"><table class="t"><tr><th>metric</th><th class="r">this page</th>'
          '<th class="r">official report</th><th></th></tr>' + rows + '</table></div></div>')
        circ = r.circuit
        w(f'<p>The circuit is {len(circ.lines):,} lines over {len(circ.qubit_of_ion)} qubits, with '
          f'{circ.measurements} measurements (the programme&rsquo;s {circ.measurements - len(r.experiment.readout_plan())} '
          f'and the appended readout&rsquo;s {len(r.experiment.readout_plan())}). The first steps, as the circuit '
          f'writes them, with what wrote each line; probabilities are shown to four figures, and <a href="{entry["sid"]}.stim">the whole circuit</a> has them exactly:</p>')
        first = {st.index for st in run.steps[:9]}
        firstmeas = next((st.index for st in run.steps if st.kind == "measure"), None)
        firstreset = next((st.index for st in run.steps if st.kind == "reset"), None)
        def shown(st):
            return st.kind in ("prepare", "readout") or st.index in first or st.index in (firstmeas, firstreset)
        w(_steps_table(entry, shown))
        vz = _quiet_frame_change(entry, shown)
        frame = ""
        if vz is not None:
            st, qs = vz
            dur = f"{st.duration:g}&nbsp;µs"
            frame = (f' The frame change <code>VZ</code> writes its <code>S</code> gates and no noise on its own '
                     f'ions (qubit{"s" if len(qs) > 1 else ""} {_and(qs)} above): <a href="#c-gate_1q"><code>gate_1q</code></a> '
                     f'skips it, as a phase update rather than a pulse, and <a href="#c-memory"><code>memory</code></a> '
                     f'skips its ions, as ions the step <a href="#w-operated">operates on</a>, so the step&rsquo;s '
                     f'{dur} is charged to the other ions only. The {dur} is the clock&rsquo;s price of a Z: the '
                     f'beams of a QCCD machine are shared, so a Z is turned by three one-ion pulses.')
        w('<p class="words">Reading it: the preparation resets every qubit and charges each a reset&rsquo;s '
          'error; a transport step writes no gate, only the memory error of everyone who waited through it; a '
          'pulse is its stim gate followed by its infidelity; a two-ion gate is <code>SQRT_XX</code> followed by '
          'the gate law at its heat; a readout is preceded by the coherent dephasing of each ion&rsquo;s wait and '
          'the readout flip, and followed by the crosstalk on the ions beside and elsewhere.' + frame + '</p>')
        budget = entry["ler"]["budget"]
        # both sides are keyed by budget line, as each channel's `counted as` names it (ms is booked
        # as ms_base + ms_heating + ms_chain, readout_z and readout_x as measure, prepare as reset,
        # crosstalk_reset as crosstalk_global), never by channel name
        groups = [("ms", ("ms_base", "ms_heating", "ms_chain")), ("gate_1q", ("gate_1q",)),
                  ("readout", ("measure",)), ("reset", ("reset",)),
                  ("memory", ("memory",)), ("memory_coherent", ("memory_coherent",)),
                  ("crosstalk", ("crosstalk",)), ("crosstalk elsewhere", ("crosstalk_global",))]
        brow = []
        for lab, keys in groups:
            a = sum(circ.budget.get(x, 0.0) for x in keys)
            t = sum(budget.get(x, 0.0) for x in keys)
            brow.append(f'<tr><td>{html.escape(lab)}</td><td class="r">{num(a)}</td><td class="r">{num(t)}</td>'
                        f'<td>{_vs(a, t)}</td></tr>')
        w('<div class="eg"><b>The error budget</b><p>Each channel&rsquo;s expected number of faults: its '
          'probabilities added over every place it writes (a pair counts once). It is not the logical error '
          'rate, which the decoder decides, but it says where the faults come from.</p>'
          '<div class="tw"><table class="t"><tr><th>channel</th><th class="r">this page</th><th class="r">official report</th>'
          '<th></th></tr>' + "".join(brow) + '</table></div></div>')
        if entry2:
            r2 = entry2["r"]
            m2 = entry2["report"]["metrics"]
            tot = sum(r2.circuit.budget.values())
            w(f'<p>The same check on the distance-3 surface code&rsquo;s entry on a 9×9 grid '
              f'(<code>{html.escape(entry2["sid"])}</code>, {len(r2.circuit.lines):,} lines, '
              f'{len(r2.circuit.qubit_of_ion)} qubits): T {num(r2.metrics["T"])} ms against the report&rsquo;s '
              f'{num(m2["T_quantinuum"]["value"])} ({_vs(r2.metrics["T"], m2["T_quantinuum"]["value"])}), '
              f'{num(r2.metrics["dacs"])} DACs against {num(m2["dacs"]["value"])} ({_vs(r2.metrics["dacs"], m2["dacs"]["value"])}), '
              f'{num(tot)} expected faults against {num(entry2["ler"]["budget"]["total"])} '
              f'({_vs(tot, entry2["ler"]["budget"]["total"])}); <a href="{entry2["sid"]}.stim">its circuit</a>.</p>')

    # ---------------------------------------------------------------- 11 the check
    w('<h2 id="check">The noiseless check</h2>')
    w('<div class="df"><b>Definition 8</b>(The noiseless check.) Let <i>C</i>° be <i>C</i> with every noise '
      'operation removed. The check holds when every detector and every observable of <i>C</i>° is '
      'deterministic, and its value is 0 &mdash; the value the experiment&rsquo;s own circuit gives it.'
      '<p class="words">It is decided, not sampled: a stabilizer simulation computes both facts exactly, in '
      'time polynomial in the number of qubits. Determinism alone is not enough: an MS pulse turned the other '
      'way round keeps every detector deterministic and flips some of their values.</p></div>')
    if entry:
        mu = _mutant(entry)
        chk = mu["check"]
        w(f'<div class="eg"><b>Example</b><p>The entry above passes (<code>noiseless_ok()</code> holds). Turn '
          f'its first MS pulse, instruction #{mu["instr"]}, from MS(π/2) to MS(−π/2), and the check '
          f'{"fails" if not chk.ok else "still holds"}: {html.escape(chk.reason)}.</p></div>')

    # ---------------------------------------------------------------- 12 the LER
    w('<h2 id="ler">The logical error rate and its interval</h2>')
    w('<div class="df"><b>Definition 9</b>(The logical error rate.) For a circuit <i>C</i> and a decoder '
      '𝒟, LER(<i>C</i>, 𝒟) is the probability, over the faults <i>C</i>&rsquo;s channels draw, that 𝒟&rsquo;s '
      'correction of the detector outcomes leaves some logical observable wrong. It is a number the three '
      'texts fix; the board reports an estimate of it.'
      '<p><i>The estimate.</i> stim samples <i>C</i> in batches of 10,000 shots (fewer where a release says '
      'so), each batch is decoded &mdash; minimum-weight matching where every fault touches at most two '
      'detectors, BP-OSD otherwise &mdash; and sampling stops at the release&rsquo;s number of failures or '
      'of shots. With <i>k</i> failures in <i>n</i> shots, LER ≈ <i>k</i>/<i>n</i>, inside Wilson&rsquo;s 95&nbsp;% '
      'interval, which stays honest at <i>k</i> = 0.</p></div>')
    w('<pre class="ev">' + hl(f"param z95 = {mets.param_text['z95']}", names) + '</pre>')
    w(_define_block(mets, names, ["wilson_centre", "wilson_half", "wilson_lo", "wilson_hi", "per_round"]))
    for n in ("ler", "ler_per_round", "ler_per_round_per_logical", "ler_per_round_lo", "ler_per_round_hi"):
        w(_metric_card(mets.metric(n), names))
    if entry:
        via = entry["via_official"]
        L = entry["ler"]["ler"]
        mets_rep = entry["report"]["metrics"]
        smp = entry["r"].sample
        w(f'<div class="eg"><b>Example</b><p>The entry&rsquo;s official sample: {L["errors"]:,} failures in '
          f'{L["shots"]:,} shots ({L["decoder"]}, seed {L["seed"]}). Through the text above:</p>'
          '<div class="tw"><table class="t"><tr><th>metric</th><th class="r">from the counts, by this page</th>'
          '<th class="r">official report</th><th></th></tr>'
          + "".join(f'<tr><td><code>{n}</code></td><td class="r">{num(via[n])}</td><td class="r">{num(t)}</td><td>{_vs(via[n], t, 1e-12)}</td></tr>'
                    for n, t in (("ler", mets_rep["ler"]["value"]), ("ler_per_round", mets_rep["ler_per_round"]["value"]),
                                 ("ler_per_round_per_logical", mets_rep["ler_per_round_per_logical"]["value"]),
                                 ("ler_per_round_lo", L["per_round_lo"]), ("ler_per_round_hi", L["per_round_hi"])))
          + '</table></div>'
          + (f'<p>Sampled again when this page was built, from this page&rsquo;s circuit with the same seed and budget: '
             f'{smp.errors:,} failures in {smp.shots:,} shots, {num(smp.per_round)} per round &mdash; '
             f'{"inside" if L["per_round_lo"] <= smp.per_round <= L["per_round_hi"] else "outside"} the official '
             f'interval. stim&rsquo;s samples repeat exactly only on one machine architecture and stim version, '
             f'so the server&rsquo;s number is the board&rsquo;s, and any other is an estimate of the same '
             f'quantity.</p>' if smp is not None else "") + '</div>')

    # ---------------------------------------------------------------- 13 goal 1
    w('<h2 id="goal1">Reading the Goal 1 board</h2>')
    w('<p>Goal 1 asks for the best quantum memory under 200 ions. Every curated memory experiment is one '
      'release, every release is graded by the evaluation above, and every entry of every release is ranked '
      'on one list by <code>ler_per_round_per_logical</code>.</p>')
    cols = [("rank by", "ler_per_round_per_logical", "the logical error rate per round, shared out over the logical qubits"),
            ("95 % interval", "ler_per_round_lo, ler_per_round_hi", "Wilson's interval on the failures, per round"),
            ("logical qubits", "n_logical", "one per observable pair: a Z and an X memory of each"),
            ("ions", "n_ions", "every ion the programme places"),
            ("ions per logical qubit", "ions_per_logical_qubit", "FTL's target is 60 logical qubits within 2,000 ions"),
            ("T_quantinuum", "T", "the round's execution time on Goal 1's clock"),
            ("instructions, DACs, ions per instruction", "instructions, dacs, ions_per_instruction", "speed and hardware")]
    w('<div class="tw"><table class="t"><tr><th>on the board</th><th>metric</th><th>what it is</th></tr>'
      + "".join(f'<tr><td>{html.escape(a)}</td><td>' + ", ".join(f'<a href="#m-{x.strip()}"><code>{x.strip()}</code></a>' for x in m_.split(","))
                + f'</td><td>{html.escape(c)}</td></tr>' for a, m_, c in cols) + '</table></div>')
    # the size of a code, which the table links: what a logical qubit costs in ions
    for n in ("n_ions", "n_logical", "ions_per_logical_qubit"):
        w(_metric_card(mets.metric(n), names))
    rrows = "".join(f'<tr><td><code>{html.escape(x["id"])}</code></td><td>{html.escape(x["code"] or "")}</td>'
                    f'<td class="r">{x["distance"]}</td><td class="r">{x["rounds"]}</td><td class="r">{x["ions"]}</td>'
                    f'<td class="r">{x["k"]}</td><td>{html.escape(str(x["decoder"]))}</td>'
                    f'<td class="r">{num(x["shots"])}</td><td class="r">{num(x["errors"])}</td></tr>' for x in rels)
    w('<details open><summary>The releases on the board</summary><div class="tw"><table class="t"><tr><th>release</th>'
      '<th>code</th><th class="r">d</th><th class="r">rounds</th><th class="r">ions</th><th class="r">k</th>'
      '<th>decoder</th><th class="r">at most shots</th><th class="r">stop at failures</th></tr>'
      + rrows + '</table></div></details>')
    w('<p>An entry is on the board only when it is <i>eligible</i> and places at most 200 ions. Five '
      'conditions are requirements of the evaluation, decided on this page&rsquo;s terms: the wiring fits 128 '
      'DACs (B1) and the device 200 junctions (B2), the rules hold (G1), the programme places at most 200 ions '
      '(G2, the board&rsquo;s own filter rather than a stage of grading), and the noiseless check holds (G3). '
      'The other stages are about the submission itself, and the grader decides them before any number is '
      'computed:</p>'
      '<div class="tw"><table class="t"><tr><th>stage</th><th>decides</th><th>how</th></tr>'
      '<tr><td>bundle</td><td>the files are the ones the manifest names, for this release</td><td class="na">sha-256 of every file</td></tr>'
      '<tr><td>physics lock</td><td>the device&rsquo;s gates, transport, heating and budget are the release&rsquo;s</td><td class="na">equality of four blocks</td></tr>'
      '<tr><td>correspondence</td><td>the graded programme is the certified one, with only coolings added</td><td class="na">instruction by instruction</td></tr>'
      '<tr><td>certificate binding</td><td>the certificate is about this circuit, operation by operation</td><td class="na">the compiler&rsquo;s witnesses</td></tr>'
      '<tr><td>R10, first half</td><td>every gate happens, once, in order, with its own ions together</td>'
      '<td class="na"><a href="../compilation/">the proved checker</a> (Lean)</td></tr>'
      '<tr><td>R10, second half</td><td>the pulses compose to the circuit</td><td class="na">stabilizer tableau, against the circuit as written</td></tr>'
      '</table></div>')
    w('<div class="eg"><b>Recompute an entry</b><p>Every entry&rsquo;s bundle is on its page on the board. '
      'With the website&rsquo;s tools installed, <code>python -m qccd.evalset bundle &lt;id&gt;-bundle.zip '
      '--sample</code> prints every metric and requirement of this page&rsquo;s evaluation for it, and '
      '<code>--stim circuit.stim</code> writes its noisy circuit; <code>qccd grade &lt;id&gt;-bundle.zip</code> '
      'runs the whole grader, every stage above included.</p></div>')

    # ---------------------------------------------------------------- 14 status
    w('<h2 id="status">What is proved, decided, computed and estimated</h2>')
    w('<div class="tw"><table class="t"><tr><th>what</th><th>status</th><th>by</th></tr>'
      '<tr><td>every rule of the rule set</td><td class="de">decided</td><td>the rule language, on the run</td></tr>'
      '<tr><td>every gate happens, in order, with its ions together (R10, first half)</td><td class="pr">proved</td><td>a checker whose correctness is proved in Lean</td></tr>'
      '<tr><td>the pulses compose to the circuit (R10, second half)</td><td>computed</td><td>a stabilizer tableau or exact matrices</td></tr>'
      '<tr><td>time, heat, hardware, size</td><td class="ex">exact</td><td>the evaluation&rsquo;s metrics, on the run</td></tr>'
      '<tr><td>the budget requirements</td><td class="de">decided</td><td>the evaluation&rsquo;s requirements</td></tr>'
      '<tr><td>the noisy circuit</td><td class="ex">exact</td><td>the evaluation&rsquo;s channels and the translation</td></tr>'
      '<tr><td>the circuit is the experiment, without noise</td><td class="de">decided</td><td>the noiseless check</td></tr>'
      '<tr><td>the logical error rate</td><td class="es">estimated</td><td>a sample and a decoder, with a 95&nbsp;% interval</td></tr>'
      '</table></div>')
    w('<p>The replay&rsquo;s heat is an upper bound (rule R15): excitations from successive motions are added, '
      'and the interference that can cancel them is not modelled. The noise model&rsquo;s numbers are '
      'measurements on one machine family, cited channel by channel; a design is ranked by them as a machine '
      'built to that family would run it.</p>')

    # ---------------------------------------------------------------- 15 yours
    w('<h2 id="yours">Writing your own evaluation</h2>')
    custom = ('evaluation "mine"\n'
              'title "Goal 1, with a price on every junction crossing"\n'
              'include "goal1"\n'
              'param junction_error = 1e-4      # per crossing: an assumption, not a measurement\n\n'
              'channel junction "A junction crossing dephases the ion"\n'
              '  after o in moves where size(crossed(o)) > 0:\n'
              '    Z_ERROR(1 - pow(1 - junction_error, size(crossed(o)))) on ions(o)\n\n'
              'metric T_us "Execution time in microseconds" unit "us" better low\n'
              '  = time(final())\n')
    w('<p>An evaluation may start from another (<code>include</code>), change its numbers '
      '(<code>param</code>), and add metrics, requirements and channels of its own, in the language above and '
      'with the words above, and nothing else. A word the vocabulary does not have is refused with the line it '
      'is on.</p>')
    w(f'<pre class="ev">{hl(custom, names)}</pre>')
    if entry2:
        try:
            mine = load(custom)
            r2 = entry2["r"]
            run2 = mine.run(r2.run.prog, r2.run.arch, experiment=r2.experiment,
                            cert=r2.bundle.files.get("certificate.qcert.json"))
            c2 = mine.circuit(run2)
            w(f'<p>On the 9×9 grid entry above, whose ions cross {num(r2.metrics["junction_crossings"])} junctions, '
              f'the new channel writes {sum(1 for o in c2.origin if o == "junction")} lines and adds '
              f'{num(c2.budget["junction"])} expected faults to the '
              f'{num(sum(r2.circuit.budget.values()))} the board&rsquo;s model charges.</p>')
        except Exception as exc:  # noqa: BLE001 -- the page says what went wrong rather than hiding the example
            w(f'<p class="note">The example could not be evaluated: {html.escape(str(exc))}</p>')
    w('</div>')
    return PAGE.format(title="Evaluation - QCCD studio", style=STYLE, extra_css=CSS,
                       body=_anchor_declarations("\n".join(b)))
