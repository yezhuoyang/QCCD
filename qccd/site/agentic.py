"""The Agentic Design page: how this website is built for AI agents, as a reference with examples.

Everything on the page that can be read from the code is read from it at build time -- the MCP
tools and their inputs, the design operations, the page actions, the Studio's verbs, the
reference sections, the harness texts, the skill -- so a tool, verb or section added without a
line here fails `tests/test_agentic_page.py` instead of going unexplained.  What each
instruction COSTS is measured, not written: `agentic_measured.json` holds real runs compiled
into programs (tools/agent_costs.py), and the page says when and on what they were measured.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

__all__ = ["page", "index_entries", "PAGE_ACTION_ABOUT", "SECTION_ABOUT", "OP_EXAMPLES"]

HERE = Path(__file__).resolve().parent
MEASURED = HERE / "agentic_measured.json"
GITHUB = "https://github.com/yezhuoyang/QCCD/blob/compiler"

#: every page action, in words (a new one without a line here fails the page's test)
PAGE_ACTION_ABOUT = {
    "read": "the page as text: headings, sections, controls (each with a ref), embedded examples, the person's selection",
    "scroll": "scroll to an element, or by an amount",
    "highlight": "outline an element with a short note the person sees",
    "click": "press a declared control",
    "fill": "type into a declared field (the typing is animated)",
    "press": "press a key on the page",
    "navigate": "go to another place of the site (only places: the site map, the page's own links, the Studio)",
    "step": "step or play an animation (a compiled programme, a run)",
    "open_lesson": "open a lesson of the course",
    "studio": "run one declared Studio verb: draw, edit, read, check a lesson",
    "wait": "pause, so the person can watch",
    "comments": "read a page's comments",
    "comment": "comment on the page, as the signed-in person, marked as their agent's",
    "reply": "reply to a comment, the same way",
    "resolve": "mark a comment thread resolved",
}

#: every reference section an agent can read, in words
SECTION_ABOUT = {
    "index": "the list of sections, and which to read first",
    "design": "how a person's shape becomes a device that runs a board's circuit: mechanisms, measured sizes, run-and-fix",
    "boards": "the leaderboards: title, circuit, what is ranked",
    "interface": "everything an agent may do on a page: places, controls, Studio verbs (nothing else is allowed)",
    "site": "the website's pages, the course, and how to do the common things visibly",
    "site:studio": "every Studio control in its own words",
    "task": "the boards (a workspace pinned to one release: that release)",
    "operations": "the design operations and their parameters",
    "rules": "the hardware rules' statements",
    "evaluator": "the grading stages, what counts as passed",
    "program": "the hardware programme's verbs",
    "anchors": "what a request can point at: selections, regions, sketches",
    "workflow": "the workspace model: designs, revisions, jobs, submissions",
    "docs:adl": "the architecture description language",
    "docs:rules": "the rules in full",
    "docs:tsir": "the compiled programme format",
    "docs:phys": "the physics model",
}

#: an example for the operations a design is usually made of (the rest are listed with their fields)
OP_EXAMPLES = {
    "add_docks": {"type": "add_docks", "count": 24},
    "construct": {"type": "construct", "generator": "ring", "params": {"width": 72, "height": 2, "verticals": 24}},
    "add_site": {"type": "add_site", "pos": [6, 1], "zone": "trap", "to": ["k1.s4"]},
    "add_chain": {"type": "add_chain", "prefix": "C", "count": 8, "start": [0, 0], "step": [1, 0], "zone": "trap"},
}

#: the skill as shown on the page: its two mentions of paths, rewritten as words (the website never shows a
#: path into the repository; the agent's own copy keeps them).  A new path in the skill fails the page's test.
SKILL_DISPLAY = [
    ("(`qccd_read_reference` section `interface`; docs/agent-interface.md)",
     "(`qccd_read_reference` section `interface`, and the agent interface's documentation)"),
    ("Never edit `.qccd/` or the database.", "Never edit the workspace's internal folder or its database."),
]

CSS = """
.ag-lead{font-size:17px}
.ag-arch{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;align-items:stretch;margin:14px 0 6px}
.ag-box{background:#fff;border:1px solid #d9d6cf;border-radius:10px;padding:9px 11px;font:13px/1.4 system-ui,sans-serif}
.ag-box b{display:block;font-size:13.5px;margin-bottom:3px}
.ag-box.you{border-color:#2563eb} .ag-box.ws{border-color:#0f766e} .ag-box.ag{border-color:#7c3aed}
.ag-box.off{border-style:dashed}
.ag-arrows{font:12px system-ui,sans-serif;color:#6b6a66;text-align:center;margin:0 0 12px}
.ag-t{width:100%;border-collapse:collapse;font:13px/1.4 system-ui,sans-serif;margin:8px 0 14px}
.ag-t th,.ag-t td{border-bottom:1px solid #e6e3dc;padding:5px 7px;text-align:left;vertical-align:top}
.ag-t th{font-weight:600;color:#52514e;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.ag-t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ag-t code{font-size:12px}
.ag-ro{color:#0b7a4b;font-size:11.5px} .ag-rw{color:#b45309;font-size:11.5px}
.ag-list{font:12px/1.4 ui-monospace,Consolas,monospace;background:#f7f6f3;border-radius:8px;padding:8px 10px;overflow:auto;
  white-space:pre;max-height:560px}
.ag-list .th{color:#7c3aed} .ag-list .ca{color:#2563eb} .ag-list .sa{color:#0b7a4b} .ag-list .mu{color:#8a8985}
.ag-bars{display:grid;grid-template-columns:120px 1fr;gap:4px 10px;align-items:center;font:13px system-ui,sans-serif;margin:8px 0 14px}
.ag-bar{height:16px;border-radius:3px;display:flex;overflow:hidden;background:#eee}
.ag-bar i{display:block;height:100%} .ag-bar .M{background:#7c3aed} .ag-bar .T{background:#2563eb}
.ag-bar .P{background:#ea580c} .ag-bar .B{background:#a8a29e} .ag-bar .O{background:#d6d3cc}
.ag-key{font:12px system-ui,sans-serif;color:#52514e;margin:-6px 0 12px}
.ag-key span::before{content:"";display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:-1px}
.ag-key .M::before{background:#7c3aed} .ag-key .T::before{background:#2563eb} .ag-key .P::before{background:#ea580c}
.ag-key .B::before{background:#a8a29e} .ag-key .O::before{background:#d6d3cc}
details.ag-d{margin:6px 0 12px} details.ag-d>summary{cursor:pointer;font:600 13.5px system-ui,sans-serif}
details.ag-d pre{white-space:pre-wrap;max-height:520px;overflow:auto}
@media (max-width:860px){.ag-arch{grid-template-columns:1fr 1fr}}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _code(v) -> str:
    return f"<code>{_e(v)}</code>"


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _dur(ms) -> str:
    if ms is None:
        return "–"
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def _measured() -> dict | None:
    try:
        return json.loads(MEASURED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _first_sentence(s: str) -> str:
    s = " ".join(str(s).split())
    for stop in (". ", "; "):
        k = s.find(stop)
        if 0 < k < 260:
            return s[:k] + "."
    return s[:260] + ("…" if len(s) > 260 else "")


# ---------------------------------------------------------------------------- sections

def _principles(n_tools: int, n_verbs: int, n_actions: int) -> str:
    items = [
        ("The agent is the person's own.", "It runs on their computer (Claude Code or Codex), in a folder they chose, "
         "and works on their designs. This website never connects to it: the website's Agent button is only a link to "
         "the person's own machine."),
        ("One design store, two hands.", "The person edits in the Studio; the agent edits through the same workspace. "
         "Every change from either is a change set: validated, attributed to who made it, protected where the person "
         "locked something, and undoable. Both see every change the moment it lands."),
        ("A closed world.", f"An agent can do exactly what is declared: {n_tools} tools, {n_actions} page actions and "
         f"{n_verbs} Studio verbs. Anything else is refused, and the refusal lists what is declared instead. A new "
         "control on the website without a declaration fails the build's gate, so the agent's view of the site "
         "cannot fall behind the site."),
        ("Visible work.", "An agent acting on a page moves a cursor with its name on it and says what it is doing; "
         "a change it makes appears in the person's Studio; a run it starts opens in their tab."),
        ("Nothing published without a person.", "Grading happens locally with the same reference checker the official "
         "server runs. Uploading needs the person to approve one exact bundle at a terminal."),
        ("Every run is a program.", "Everything an agent did for a request is recorded, and reads back as a sequence "
         "of instructions with what each cost and what it changed. That is how a run is audited, replayed, "
         "checked against the design store, and made faster."),
    ]
    return "<ul>" + "".join(f"<li><b>{_e(a)}</b> {_e(b)}</li>" for a, b in items) + "</ul>"


def _architecture() -> str:
    boxes = [
        ("you", "The person's browser", "the Studio and every page of this website, served through their workspace"),
        ("ws", "The workspace service", "on the person's computer: the designs and their history, change sets, jobs "
         "(compile, run, grade), the page bridge, the trace"),
        ("ws", "The QCCD MCP server", "the agent's tools: typed calls into the workspace, each traced"),
        ("ag", "The agent runtime", "Claude Code or Codex, started by the chat, with the QCCD skill"),
        ("ag", "The model", "reads the whole context each call, writes text, thinking and tool calls"),
    ]
    out = ['<div class="ag-arch">']
    for cls, t, d in boxes:
        out.append(f'<div class="ag-box {cls}"><b>{_e(t)}</b>{_e(d)}</div>')
    out.append("</div>")
    out.append('<p class="ag-arrows">page ⇄ service: HTTP and a live event stream · service ⇄ MCP server: HTTP with the '
               'agent\'s token · MCP server ⇄ runtime: MCP over stdio · runtime ⇄ model: the model provider\'s API</p>')
    out.append('<div class="ag-arch" style="grid-template-columns:1fr"><div class="ag-box off"><b>The official server '
               '(qccd.academy/official)</b>grades an uploaded bundle again with the same reference checker and '
               'keeps the public leaderboards. The workspace reaches it only when the person approves a publish.</div></div>')
    return "".join(out)


def _tools_table(meas: dict | None) -> str:
    from ..workspace.mcp_server import READ_ONLY, _tools
    stats = (meas or {}).get("tools") or {}
    rows = []
    for name, desc, schema in _tools():
        props = schema.get("properties") or {}
        req = set(schema.get("required") or [])
        args = ", ".join((f"<b>{_e(k)}</b>" if k in req else _e(k)) for k in props)
        st = stats.get(name) or {}
        cost = ""
        if st:
            cost = (f"{_dur(st.get('exec_ms_median'))} in the tool"
                    + (f" (max {_dur(st.get('exec_ms_max'))})" if st.get("exec_ms_max") and st.get("n", 0) > 1 else "")
                    + f"; ~{st.get('result_tokens_est_median')} tokens back · {st['n']}×")
        kind = '<span class="ag-ro">reads</span>' if name in READ_ONLY else '<span class="ag-rw">acts</span>'
        rows.append(f"<tr><td>{_code(name)}<br>{kind}</td><td>{_e(_first_sentence(desc))}</td>"
                    f"<td>{args or '–'}</td><td>{cost or '<span class=mu>not in the measured runs</span>'}</td></tr>")
    return ('<table class="ag-t"><thead><tr><th>tool</th><th>what it does</th><th>inputs (<b>required</b>)</th>'
            '<th>measured cost</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def _ops_table() -> str:
    from ..workspace.operations import describe_operations
    rows = []
    for op in describe_operations():
        if op["browser_only"]:
            continue
        ex = OP_EXAMPLES.get(op["type"])
        fields = ", ".join(_e(k) for k in op["params"])
        rows.append(f"<tr><td>{_code(op['type'])}</td><td>{_e(op['summary'])}</td><td>{fields}</td>"
                    f"<td>{_code(_j(ex)) if ex else ''}</td></tr>")
    return ('<table class="ag-t"><thead><tr><th>operation</th><th>what it does</th><th>fields</th><th>example</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def _page_tables(meas: dict | None) -> str:
    from ..workspace.interface import WORKSPACE_PLACES, editor_api
    from ..workspace.service import PAGE_ACTIONS
    pstats = (meas or {}).get("page") or {}
    rows = []
    for a in PAGE_ACTIONS:
        ms = [v["ms_median"] for k, v in pstats.items() if k == a or k.startswith(a + " ")]
        rows.append(f"<tr><td>{_code(a)}</td><td>{_e(PAGE_ACTION_ABOUT[a])}</td>"
                    f"<td class=n>{_dur(ms[0]) if ms else '–'}</td></tr>")
    out = ['<table class="ag-t"><thead><tr><th>action</th><th>what it does</th><th>measured on the page</th></tr>'
           '</thead><tbody>' + "".join(rows) + "</tbody></table>"]
    api = editor_api()
    kinds: dict = {}
    for k, v in api.items():
        if v["use"] == "agent":
            kinds.setdefault(v.get("kind") or "other", []).append(k)
    about = {"design": "change the design on the canvas (committed as the agent's change set)",
             "read": "read what the page knows (never changes anything)", "view": "move the view, arm a tool, open a panel",
             "program": "write the hardware programme", "course": "the lessons: load, check, show the solution"}
    rows = [f"<tr><td>{_e(k)}</td><td class=n>{len(v)}</td><td>{_e(about.get(k, ''))}</td>"
            f"<td>{', '.join(_code(x) for x in sorted(v)[:14])}{' …' if len(v) > 14 else ''}</td></tr>"
            for k, v in sorted(kinds.items(), key=lambda kv: -len(kv[1]))]
    n_all = len(api)
    n_agent = sum(len(v) for v in kinds.values())
    out.append(f"<p>The Studio's editor has {n_all} verbs. {n_agent} are declared for agents; the other "
               f"{n_all - n_agent} are for the test harness or for no one, and an agent calling one is refused.</p>")
    out.append('<table class="ag-t"><thead><tr><th>kind</th><th>verbs</th><th>what they do</th><th>some of them</th>'
               '</tr></thead><tbody>' + "".join(rows) + "</tbody></table>")
    out.append("<p>Places an agent may go, besides every page in the site's own index and the links on the page it "
               "is on:</p><ul>" + "".join(f"<li>{_code(p['url'])}: {_e(p['about'])}</li>" for p in WORKSPACE_PLACES)
               + "</ul>")
    return "".join(out)


def _atir_spec() -> str:
    from ..workspace import atir
    doc = atir.__doc__ or ""
    a = doc.index("Machines")
    b = doc.index("Semantics.")
    spec = doc[a:b].rstrip()
    rows = [
        ("recv", "–", "the harness around the person's words", "memory := the harness + the words"),
        ("boot", "–", "the time the agent process takes to start", "the tools and skill are loaded"),
        ("think", "M (model)", "latency; tokens read (= memory) and written", "memory grows by what it writes"),
        ("say", "–", "none (written by the think before it)", "the person reads it"),
        ("call", "T (tools), P (page), or the runtime's own", "end-to-end time and in-tool time; the result's size",
         "memory grows by the result; its effects apply: a commit, a job, a page action"),
        ("end", "–", "the turn's wall time, model time, model calls, money", "–"),
    ]
    t = ('<table class="ag-t"><thead><tr><th>instruction</th><th>machine</th><th>its cost</th><th>what it changes</th>'
         '</tr></thead><tbody>' + "".join(f"<tr><td>{_code(r[0])}</td><td>{_e(r[1])}</td><td>{_e(r[2])}</td>"
                                           f"<td>{_e(r[3])}</td></tr>" for r in rows) + "</tbody></table>")
    return f'<pre class="ag-list">{_e(spec)}</pre>' + t


def _listing(ex: dict) -> str:
    lines = [f'<span class=mu>{"#":>4} {"t":>7} {"cost":>7} {"memory":>8}  op     operands</span>']
    for x in ex["instructions"]:
        op = x["op"]
        mem = f"{x['memory'] / 1000:.1f}k" if x.get("memory") else ""
        if op == "recv":
            what = "“" + str(x.get("request") or "") + "”"
        elif op == "boot":
            what = f"{x.get('model')} · the agent process started"
        elif op == "think":
            b = x.get("blocks") or {}
            what = "→ " + ", ".join(f"{b[k]} {k}" for k in ("thinking", "say", "call") if b.get(k))
        elif op == "say":
            what = "“" + str(x.get("text") or "") + "”"
            what = what if len(what) < 150 else what[:148] + "…”"
        elif op == "call":
            args = x.get("args") or {}
            sub = args.get("verb") or args.get("action") or ""
            eff = " ".join("{" + ("commit r" + str(e.get("revision")) if e.get("kind") == "commit" else
                                  "job started" if e.get("kind") == "job" else
                                  f"poll: {e.get('status')}" if e.get("kind") == "poll" else
                                  "new design" if e.get("kind") == "design" else e.get("kind", "")) + "}"
                           for e in x.get("effects") or [])
            shown = {k: v for k, v in args.items() if k not in ("origin_prompt_id", "request_id", "note")}
            a = _j(shown)
            a = a if len(a) < 110 else a[:108] + "…}"
            what = f"{x.get('tool')}{'.' + sub if sub and x.get('tool') in ('qccd_page_act', 'qccd_present', 'qccd_manage_branch') else ''}({a}) {eff}"
        elif op == "end":
            what = (f"{x.get('status')} · wall {_dur(x.get('duration_ms'))} · model {_dur(x.get('api_ms'))} · "
                    f"{x.get('model_calls')} model calls" + (f" · ${x['cost_usd']:.2f}" if x.get("cost_usd") is not None else ""))
        else:
            what = op
        cls = {"think": "th", "call": "ca", "say": "sa"}.get(op, "")
        cost = _dur(x.get("ms")) if op not in ("say", "recv") else ""
        row = f"{x['i']:>4} {x['t']:>6.1f}s {cost:>7} {mem:>8}  {op:<6} "
        lines.append(f'<span class="{cls}">{_e(row)}</span>{_e(what)}')
    return '<pre class="ag-list">' + "\n".join(lines) + "</pre>"


def _costs(meas: dict | None) -> str:
    if not meas or not meas.get("runs"):
        return "<p>No measured runs are bundled with this build.</p>"
    out = []
    runs = meas["runs"]
    out.append("<p>Each run below is the same request, sent from the Studio's chat to Claude Code in a fresh workspace:"
               " “" + _e(runs[0].get("request") or "") + "”. The model was " + _e(runs[0].get("model") or "")
               + ". Times are wall-clock, from the trace.</p>")
    out.append('<div class="ag-bars">')
    for r in runs:
        by, wall = r["by_machine_ms"], r["wall_ms"] or 1
        segs = [("M", by.get("model", 0)), ("T", by.get("tools", 0)), ("P", by.get("page", 0)), ("B", by.get("boot", 0))]
        rest = max(0, wall - sum(v for _, v in segs))
        bar = "".join(f'<i class="{k}" style="width:{100 * v / wall:.2f}%" title="{_dur(v)}"></i>' for k, v in segs + [("O", rest)] if v)
        out.append(f"<div>{_e(r['label'])} · {_dur(r['wall_ms'])}</div><div class=ag-bar>{bar}</div>")
    out.append("</div>")
    out.append('<p class="ag-key"><span class="M">model (think)</span><span class="T">tools (call)</span>'
               '<span class="P">page actions</span><span class="B">starting the agent</span><span class="O">other</span></p>')
    rows = []
    for r in runs:
        by = r["by_machine_ms"]
        rows.append(f"<tr><td>{_e(r['label'])}</td><td class=n>{_dur(r['wall_ms'])}</td>"
                    f"<td class=n>{_dur(by.get('model'))} ({100 * by.get('model', 0) / (r['wall_ms'] or 1):.0f}%)</td>"
                    f"<td class=n>{r['model_calls']}</td><td class=n>{_dur(r['think_ms']['median'])} · {_dur(r['think_ms']['p90'])} · {_dur(r['think_ms']['max'])}</td>"
                    f"<td class=n>{r['calls']} ({r['polls']} polls)</td>"
                    f"<td class=n>{(str(round(r['memory_first'] / 1000)) + 'k → ' + str(round(r['memory_peak'] / 1000)) + 'k') if r.get('memory_peak') else '–'}</td>"
                    f"<td class=n>{('$%.2f' % r['cost_usd']) if r.get('cost_usd') is not None else '–'}</td></tr>")
    out.append('<table class="ag-t"><thead><tr><th>run</th><th>wall</th><th>model</th><th>model calls</th>'
               '<th>one model call: median · 90% · slowest</th><th>tool calls</th><th>memory (tokens)</th><th>money</th></tr>'
               '</thead><tbody>' + "".join(rows) + "</tbody></table>")
    sub = [r for r in runs if r.get("to_submission")]
    if sub:
        last = runs[-1]
        out.append("<p><b>The design itself</b>, from the request to the submission. After it the agent waits for the "
                   "reference grade: minutes for this board until the Lean checker computed its replay once, seconds "
                   f"since (the last run's whole turn, grade and verdict included, took {_dur(last['wall_ms'])}).</p>")
        rows = [f"<tr><td>{_e(r['label'])}</td><td class=n>{r['to_submission']['s']:.1f} s</td>"
                f"<td class=n>{r['to_submission']['model_calls']}</td><td class=n>{_dur(r['to_submission']['model_ms'])}</td>"
                f"<td class=n>{_dur(r['to_submission']['tool_ms'])}</td><td class=n>{_dur(r['to_submission']['page_ms'])}</td>"
                f"<td class=n>{_dur(r.get('start_ms'))}</td></tr>" for r in sub]
        out.append('<table class="ag-t"><thead><tr><th>run</th><th>submitted after</th><th>model calls</th><th>model</th>'
                   '<th>tools</th><th>page</th><th>starting the agent</th></tr></thead><tbody>' + "".join(rows)
                   + "</tbody></table>")
        rounds = [(runs[i - 1]["label"], r["label"], CHANGES[r["label"]]) for i, r in enumerate(runs)
                  if i and r["label"] in CHANGES]
        if rounds:
            out.append("<p>What changed between the runs, each change made because the earlier run's program "
                       "showed its cost:</p>")
            for a, b, items in rounds:
                out.append(f"<p><b>{_e(a)} → {_e(b)}</b></p><ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
    out.append("<p><b>Time.</b> A model call costs seconds and a tool call milliseconds (the tool table above has the "
               "in-tool times). So a design is fast when it takes few model calls: batch operations, apply without "
               "an extra preview, wait on a job inside one call instead of polling.</p>"
               "<p><b>Memory.</b> Memory is the context a model call reads: the harness, the tools, the skill, and "
               "every result so far. Memory is exact where Claude Code reported it (the runs recorded after "
               "per-call usage was added). A call's result adds about characters ÷ 4 tokens, shown with ~ as an "
               "estimate.</p>"
               "<p><b>Tokens and money.</b> Tokens written and money are Claude Code's own totals for the turn. Most "
               "of the input is served from the provider's cache.</p>")
    return "".join(out)


#: what changed before each measured run, by the run's label: every item was found in the earlier
#: run's program, and the numbers are that run's and this one's
CHANGES = {
    "after (2026-09-26)": [
        "One model call spent 57.5 s working out 24 dock coordinates. The operation <code>add_docks</code> now "
        "computes them from a count in 0.3 s, and the slowest model call fell to 9.0 s.",
        "76 model calls polled the grade every few seconds. <code>qccd_get_job</code> now waits up to 50 s inside "
        "one call.",
        "Every change was previewed and then applied, a model call apart. The agent is now told to apply directly "
        "when confident, since an applied change is validated the same way and can be undone.",
        "The agent tried to schedule a check-in that could not outlive its turn. Scheduling tools are denied, and it "
        "now follows the grade to the end, which is why this run's wall time is longer: it includes the whole "
        "grade.",
    ],
    "now (2026-09-26, later)": [
        "The grade took 11 minutes, 93% of it the Lean certificate check. The compiled checker replayed the "
        "certificate about 3,700 times instead of once. Its decision procedure now computes the replay once; the "
        "specification it decides and its soundness theorem are unchanged. The check went from 623 s to under a "
        "second, and the whole grade from 667 s to 12 s.",
        "Claude Code started again for every message, 7 to 9 s each time. It is now one process per conversation, "
        "kept alive between messages and started before the first one: when a page with the chat opens, or when the "
        "person starts typing. The start of the run fell from 8.8 s to 0.1 s.",
        "The agent had all of Claude Code's own tools and bundled skills. Twice it lost about 9 s to a research "
        "skill it had opened by accident. It now gets only Read, Grep and Glob of them, and a wait for the QCCD tools "
        "to connect; its first model call read 13k tokens instead of 27k.",
        "Thinking effort now defaults to medium, which halved each model call (4.2 s against 8.3 s) with the "
        "same design.",
        "One <code>qccd_get_job</code> call follows a submission through its compile and its grade and returns the "
        "verdict. The chat shows the submission as a card that follows it too. A submission right after a run adopts "
        "that run's compile instead of compiling again.",
    ],
}


def _harness(meas: dict | None) -> str:
    from ..workspace.agents.claude import ALLOWED, BUILTIN_TOOLS, DEFAULT_EFFORT, DENIED, SYSTEM_NOTE
    from ..workspace.mcp_server import INSTRUCTIONS
    ex = (meas or {}).get("excerpt") or {}
    out = ["<p>An agent is told what to do in four layers. Each is text in the code, shown here as it is.</p>",
           "<ol><li><b>The tools' own descriptions and the MCP server's instructions</b>, which every MCP client "
           "gives its model: the rules of the workspace (read the context first, change the design only through "
           "change sets, how to show work, the closed world of pages).</li>"
           "<li><b>The skill</b>, which the agent loads when a request is about QCCD: the workflow, and references "
           "it reads on demand.</li>"
           "<li><b>The request as delivered</b>: the person's words, quoted as data, inside the workspace's framing: "
           "which page they were on, the frozen context of what they meant, the mode (ask, propose, apply), how to "
           "answer.</li>"
           "<li><b>The runtime's settings</b>: which of its own tools it may use, what it may never do, and a standing "
           "note.</li></ol>"]
    out.append(f'<details class="ag-d"><summary>The MCP server\'s instructions ({len(INSTRUCTIONS)} characters)</summary>'
               f"<pre>{_e(INSTRUCTIONS)}</pre></details>")
    if ex.get("harness"):
        out.append(f'<details class="ag-d"><summary>A request as the agent received it ({len(ex["harness"])} characters; '
                   f"internal ids replaced by names)</summary><pre>{_e(ex['harness'])}</pre></details>")
    out.append("<p>Claude Code runs as one process per conversation (<code>--input-format stream-json</code>), kept "
               "alive between messages and started before the first one: when a page with the chat opens, or when the "
               "person starts typing. It runs with <code>--permission-mode dontAsk</code> (nothing waits for a person to "
               "approve a prompt), <code>--strict-mcp-config</code> (only the QCCD server), and tool search off (so all "
               "QCCD tools are loaded from the first call). Of its own tools it gets only " + _code(BUILTIN_TOOLS)
               + " (<code>--tools</code>), with no bundled skills or slash commands. Its thinking effort is "
               "<code>" + _e(DEFAULT_EFFORT) + "</code> unless the person picks another. Allowed: " + _code(ALLOWED)
               + ". Denied: "
               + _code(DENIED) + ". The denied ones either change files or schedule work that cannot outlive a "
               "single run. Its standing note:</p><pre>" + _e(SYSTEM_NOTE) + "</pre>"
               "<p>Codex runs through its app server, with the QCCD tools pre-approved: the workspace authorises "
               "them itself (a scoped token, protected entities, no publishing).</p>")
    return "".join(out)


def _skills() -> str:
    from ..workspace import installers
    from ..workspace.reference import SECTIONS
    skill = (Path(installers.__file__).resolve().parent / "skill")
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    for x, y in SKILL_DISPLAY:
        text = text.replace(x, y)
    refs = sorted(p.name for p in (skill / "references").glob("*.md"))
    out = [f"<p>The skill (version {_e(installers.SKILL_VERSION)}) is installed into the workspace folder for the "
           f"agent the person uses, by <code>qccd agent install --client codex</code> or <code>--client claude</code>. "
           f"It is a short workflow (SKILL.md) plus references the agent opens when it needs them: "
           + ", ".join(_code(r) for r in refs)
           + ", and generated ones listing the boards, the operations, the rules and the evaluator's stages.</p>",
           f'<details class="ag-d"><summary>SKILL.md ({len(text)} characters)</summary><pre>{_e(text)}</pre></details>',
           "<p>Knowledge the agent can ask for at any time, version-matched with the checker, through "
           + _code("qccd_read_reference(section, query)") + ":</p>",
           '<table class="ag-t"><thead><tr><th>section</th><th>what it holds</th></tr></thead><tbody>'
           + "".join(f"<tr><td>{_code(s)}</td><td>{_e(SECTION_ABOUT[s])}</td></tr>" for s in SECTIONS)
           + "</tbody></table>",
           "<p>The design guide is where measured know-how lives: which mechanism each board needs, sizes that are "
           "known to work, how a shape becomes a loop and its docks, and a table of what a failed run says and how "
           "to fix it. The agent reads it before drawing.</p>"]
    return "".join(out)


def _build_rules() -> str:
    items = [
        ("Declare every control.", "A button, field or panel an agent should be able to use carries a declaration "
         "(a hint key) or is one of the declared forms; a Studio verb is listed in the editor's API file as for "
         "agents, for the harness, or for no one. The gate test fails a build that adds UI without it."),
        ("Measure before optimising.", "Open the request's program (the Agent traces page, or "
         "<code>qccd trace --program</code>): the slowest instructions and the time by machine say what to fix."),
        ("Raise the instruction level.", "When the model spends a long call computing something (coordinates, a "
         "layout), make it an operation. <code>add_docks</code> replaced a 57-second model call with a 0.3-second "
         "tool call."),
        ("Check what a run claims.", "<code>qccd trace --check</code> verifies that every design commit in a program "
         "is in the workspace's log at that revision and replays."),
        ("Keep names human.", "A person never sees an internal id: designs by the names they gave them, boards by "
         "title."),
    ]
    return "<ul>" + "".join(f"<li><b>{_e(a)}</b> {b}</li>" for a, b in items) + "</ul>"


def _try_it() -> str:
    return ("<pre>git clone -b compiler https://github.com/yezhuoyang/QCCD\ncd QCCD\npython -m venv .venv\n"
            ".venv\\Scripts\\activate\npip install -e \".[agent]\"\nqccd toolchain install\ncd ..\n"
            "qccd init \"My QCCD designs\"\ncd \"My QCCD designs\"\nqccd agent install --client claude\nqccd studio</pre>"
            "<p><b>Yours to name, anything you like:</b> the workspace folder (\"My QCCD designs\" is only an example) "
            "and every design. <b>Type as shown:</b> everything else. Then ask in the Studio's chat, and open "
            "<code>qccd trace --open</code> to watch the run as a program. The full account, including what is and is "
            f'not tested, is in <a href="{GITHUB}/docs/workspace.md">the workspace\'s documentation</a> and '
            f'<a href="{GITHUB}/docs/agent-interface.md">the agent interface\'s</a>.</p>')


# ---------------------------------------------------------------------------- the page

def page(PAGE: str, STYLE: str) -> str:
    from ..workspace.interface import editor_api
    from ..workspace.mcp_server import _tools
    from ..workspace.service import PAGE_ACTIONS
    meas = _measured()
    n_tools = len(_tools())
    n_verbs = sum(1 for v in editor_api().values() if v["use"] == "agent")
    ex = (meas or {}).get("excerpt")
    body = [
        "<h1>Agentic Design</h1>",
        '<p class="sub ag-lead">This website and its design tools were built from the start to be used by a person '
        "and their own AI agent together. This page explains how, and is the reference for what an agent is "
        "given and can do, what each of its instructions costs, and how every run is recorded as a program that "
        "can be read, replayed and checked.</p>",
        '<h2 id="idea">The idea</h2>', _principles(n_tools, n_verbs, len(PAGE_ACTIONS)),
        '<h2 id="architecture">How the pieces fit</h2>', _architecture(),
        f'<h2 id="protocol">The protocol: {n_tools} tools</h2>',
        "<p>The agent works through the QCCD MCP server's tools. Each is a typed call into the person's workspace, "
        "which does the work and records it. <span class=ag-ro>reads</span> tools change nothing; "
        "<span class=ag-rw>acts</span> tools change designs, start jobs or move the person's page, and each such "
        "change is attributed to the agent. Costs are medians over the measured runs below.</p>",
        _tools_table(meas),
        '<h2 id="operations">Changing a design</h2>',
        "<p>A change is one call, " + _code("qccd_apply_change_set") + ", carrying a list of semantic operations. "
        "The workspace validates it (structure, protection, the geometry rules), commits it as a new revision of "
        "the named design, and every open Studio shows it at once. It can be previewed, and undone.</p>",
        "<pre>" + _e(json.dumps({"branch": "Two triangles", "expected_revision": 2, "request_id": "docks-1",
                                  "mode": "apply", "operations": [{"type": "add_docks", "count": 24}]}, indent=1)) + "</pre>",
        _ops_table(),
        '<h2 id="page">The agent\'s hands on a page</h2>',
        "<p>On the person's own pages (the website served through their workspace, and their Studio) the agent acts "
        "with " + _code("qccd_page_act") + ". Everything it does is visible, and only declared controls and places "
        "can be used. Examples, from real runs:</p>"
        "<pre>" + _e("\n".join(_j(x) for x in [
            {"action": "navigate", "path": "rules/"},
            {"action": "highlight", "target": {"ref": "c12"}, "note": "R20: rails meet at 60 degrees or more"},
            {"action": "studio", "verb": "newCanvas"},
            {"action": "studio", "verb": "sketchDraw",
             "args": ["poly", [[2, 43.30127], [48, 43.30127], [49, 41.56922], [26, 1.73205], [24, 1.73205], [1, 41.56922]],
                      None, {"closed": True}]},
            {"action": "step", "play": True}])) + "</pre>",
        _page_tables(meas),
        '<h2 id="instructions">A run as a program</h2>',
        "<p>Everything an agent does for one request is recorded. The trace comes from the agent's own stream, the "
        "MCP server's record of each call, and the page's record of each action. It is then compiled into a program "
        "in a small instruction language. This is the same idea as a compiled QCCD programme, where each hardware "
        "instruction runs beside the animation and says what it costs. On the person's computer the Agent traces "
        "page shows the program: the listing, a time bar by machine, the memory curve, and beside it the design "
        "as it stood at each instruction. A checker verifies the program. It must be well-formed (every call "
        "answered, time moving forward, a request at the start and an end), and every design commit it claims must "
        "be in the workspace's own log at that revision and replay.</p>",
        _atir_spec(),
    ]
    if ex:
        body += ['<h3 id="example">One request, end to end</h3>',
                 "<p>The program of a real run: the person asked for “a large triangle and a smaller triangle”, submitted "
                 "to the BB code's leaderboard. The agent made a new design, drew the large triangle as one closed loop "
                 "in the Studio, added 24 docks inside it (the smaller triangle), ran the BB syndrome round on it, and "
                 "submitted it. Internal ids are replaced by names.</p>", _listing(ex)]
    body += ['<h2 id="cost">What each instruction costs</h2>', _costs(meas),
             '<h2 id="harness">The harness: what the agent is told</h2>', _harness(meas),
             '<h2 id="skills">Skills and knowledge</h2>', _skills(),
             '<h2 id="building">Building on it</h2>', _build_rules(),
             '<h2 id="try">Try it</h2>', _try_it()]
    return PAGE.format(title="Agentic Design - QCCD studio", style=STYLE, extra_css=CSS, body="\n".join(body))


def index_entries() -> list:
    """The page and its sections, for the site's search."""
    secs = [("idea", "The idea"), ("architecture", "How the pieces fit"), ("protocol", "The protocol: the MCP tools"),
            ("operations", "Changing a design: operations"), ("page", "The agent's hands on a page"),
            ("instructions", "A run as a program"), ("cost", "What each instruction costs"),
            ("harness", "The harness: what the agent is told"), ("skills", "Skills and knowledge"),
            ("building", "Building on it"), ("try", "Try it")]
    return ([{"t": "Agentic Design", "d": "how this website is built for AI agents: tools, instructions, costs, harness, skills",
              "u": "agentic/", "k": "page"}]
            + [{"t": t, "d": "Agentic Design", "u": f"agentic/#{sid}", "k": "page"} for sid, t in secs])
