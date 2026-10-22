"""The Rules page, written from the rule language itself (`qccd.ruleset`).

Every rule is printed as the assertion the checker evaluates -- the same text, never a
paraphrase of it -- with each word linked to its definition, and every rule set the site
ships is listed with the rules it picks.  A rule set can be downloaded as the one file the
compiler is given.
"""

from __future__ import annotations

import html
import re

from ..ruleset import RuleSet, catalogue, load, names, rule_order
from ..ruleset.syntax import KEYWORDS
from ..ruleset.vocabulary import TERMS

#: The order and the plain names of the shipped rule sets.
SETS = (
    ("standard", "This website's machine"),
    ("qccdsim", "QCCDSim"),
    ("qccdsim-physical", "QCCDSim, physical gate swap"),
    ("qccdsim-gateset", "QCCDSim, gates as a set"),
    ("cyclone", "Cyclone"),
    ("jones", "Surface-code QCCD"),
    ("tiscc", "TISCC"),
    ("trapsimd", "TrapSIMD"),
    ("ionshuttler", "IonShuttler, exact router"),
    ("ionshuttler-heuristic", "IonShuttler, heuristic router"),
)

#: The Reproduce papers that are not replays of an artifact's runs, and their rule sets.
MODEL_PAPER_SETS = {"ruan2025": ("trapsimd",),
                    "schoenberger2024": ("ionshuttler", "ionshuttler-heuristic")}

#: What each parameter a shipped rule set sets means, in a line.
PARAM_NOTES = {
    "max_chain": "the most ions a gate's chain may hold (R13)",
    "serial_kinds": "the operation kinds that hold their trap one at a time (R27), named as kind(o) names them",
    "single_ion_zones": "the zones single-ion operations may run in (R32)",
    "chain_order": ("how the order inside a chain is followed through a reorder before a split: "
                    "\"qccdsim\" lifts the departing ion out where it stood, \"physical\" moves the end "
                    "ion's state into its place (read by R14)"),
}

GROUPS = (("hardware", "The device"), ("execution", "The execution"),
          ("time", "Instants and the state"), ("circuit", "The input circuit"),
          ("model", "The clock"))

CSS = """
.rules-intro{max-width:880px}
pre.assert{background:#f6f7fb;border:1px solid #dfe3ee;border-radius:6px;padding:10px 12px;
  font-size:13.5px;line-height:1.55;white-space:pre-wrap;margin:8px 0}
pre.assert b{color:#1c2a4a}
pre.assert a{color:#0b5cad;text-decoration:none;border-bottom:1px dotted #0b5cad}
pre.assert .s{color:#8a3b12}
.core{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;color:#fff;
  background:#1c2a4a;border-radius:3px;padding:1px 6px;margin-left:6px;vertical-align:2px}
.chips a{display:inline-block;font-size:12px;border:1px solid #c9d1e3;border-radius:10px;
  padding:0 7px;margin:2px 3px 2px 0;text-decoration:none;color:#1c2a4a;background:#fff}
.chips a.core{background:#1c2a4a;color:#fff;border-color:#1c2a4a}
table.vocab{border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 18px}
table.vocab td{border-top:1px solid #e3e7f0;padding:6px 8px;vertical-align:top}
table.vocab td:first-child{white-space:nowrap;width:1%}
table.vocab .how{color:#555;font-size:13px;margin-top:3px}
table.sets{border-collapse:collapse;width:100%;font-size:14px}
table.sets td,table.sets th{border-top:1px solid #e3e7f0;padding:7px 8px;vertical-align:top;text-align:left}
.verdicts{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 4px}
.verdicts div{flex:1 1 200px;border:1px solid #dfe3ee;border-radius:6px;padding:8px 10px;font-size:13.5px}
.verdicts b{display:block}
.verdicts .ok b{color:#0b7a4b}.verdicts .bad b{color:#c62828}.verdicts .na b{color:#666}
.verdicts .unk b{color:#9a6700}
"""


def _sets() -> list[tuple[str, str, RuleSet]]:
    shipped = set(names())
    return [(n, label, load(n)) for n, label in SETS if n in shipped]


def highlight(text: str, params: set[str] | frozenset = frozenset()) -> str:
    """A rule's formula as written, with the language's words in bold and every word of
    the vocabulary linked to its definition."""
    out = []
    for m in re.finditer(r'"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_]*|\s+|.', text):
        t = m.group()
        if t.startswith('"'):
            out.append(f'<span class="s">{html.escape(t)}</span>')
        elif t in KEYWORDS:
            out.append(f"<b>{t}</b>")
        elif t in TERMS:
            out.append(f'<a href="#w-{t}" title="{html.escape(TERMS[t].meaning)}">{t}</a>')
        elif t in params:
            out.append(f'<a href="#sets" title="a number the rule set sets">{t}</a>')
        else:
            out.append(html.escape(t))
    return "".join(out)


def _sources(src: str) -> str:
    out = []
    for tok in [t.strip() for t in re.split(r"[;,]", src) if t.strip()]:
        m = re.match(r"^(?:arXiv:)?(\d{4}\.\d{4,5}|quant-ph/\d+)$", tok)
        if m:
            out.append(f'<a href="https://arxiv.org/abs/{m.group(1)}">arXiv:{m.group(1)}</a>')
        elif tok == "deck_v3":
            out.append("the ion-transport deck (v3)")
        elif tok.startswith("collaborator review"):
            out.append("collaborators' review, September 2026")
        elif tok.startswith("reader report"):
            out.append("a reader's report, September 2026")
        else:
            out.append(html.escape(tok))
    return ", ".join(out)


VERDICT_WORD = {"holds": "holds", "fails": "fails", "does not apply": "does not apply",
                "could not be checked": "could not be checked"}


def _card(ex: dict, rid: str, device: str | None) -> str:
    """One example programme beside a rule: the programme, and what the rule decides on it."""
    r = (ex.get("rules") or {}).get("focus") or {}
    v = r.get("verdict", "could not be checked")
    cls = {"holds": "pass", "fails": "fail"}.get(v, "skip")
    parts = [f'<b class="{ {"pass": "ok", "fail": "bad"}.get(cls, "skip") }">{rid} {VERDICT_WORD.get(v, v)}</b>']
    for msg in r.get("examples", [])[:2]:
        parts.append(f'<span class="m">{html.escape(msg)}</span>')
    if r.get("count", 0) > 2:
        parts.append(f'<span class="m">&hellip; {r["count"] - 2} more like these</span>')
    if r.get("reason"):
        parts.append(f'<span class="m">{html.escape(r["reason"])}</span>')
    others = [x for x in (ex.get("rules") or {}).get("fails", []) if x != rid]
    if others:
        parts.append(f'<span class="m">this programme also breaks {", ".join(others)}</span>')
    tag = ex.get("tag") or {"pass": "holds", "fail": "fails", "skip": "does not apply"}[cls]
    if ex.get("expect") == "any":
        cls = "any"
    dev = f'<p class="src">device: {html.escape(device)}</p>' if device else ""
    return (f'<div class="ex {cls}"><span class="tag">{html.escape(tag)}</span>'
            f'<p class="why">{ex["why"]}</p>{dev}'
            f'<pre{" class=\"ir\"" if ex.get("ir") else ""}><code>{html.escape(ex["text"])}</code></pre>'
            f'<div class="verdict">{"".join(parts)}</div>'
            f'<div class="runbox"><iframe class="live" loading="lazy" src="{ex["page"]}#embed&amp;step=1" '
            f'title="the example, on its page"></iframe></div>'
            f'<a class="open" href="{ex["page"]}#step=1">open the page, with its Report pane</a></div>')


def page(built: dict, examples_by_rule: dict, PAGE: str, STYLE: str, papers_by_set: dict) -> str:
    """`built`: rule id -> {"pass": card data, "fail": card data} (the example programmes);
    `examples_by_rule`: rule id -> the example entry of `qccd.site.examples.RULES`;
    `papers_by_set`: rule-set name -> [(paper key, short name)] judged by it on Reproduce."""
    cat = catalogue()
    sets = _sets()
    in_sets: dict[str, list[str]] = {}
    for n, label, rs in sets:
        for rid in rs.ids:
            in_sets.setdefault(rid, []).append(n)
    all_params = set(cat.params) | {p for _, _, rs in sets for p in rs.params}
    n_rules = len(cat.rules)
    core = [r.id for r in cat.rules if r.core]

    b = ['<h1>The rules</h1>',
         '<div class="rules-intro">',
         f'<p class="sub">A rule is one assertion about the hardware and what the machine does on it, '
         f'written in a small language you can read on this page: '
         f'<i>for every site and every instant, the ions in the site number at most its capacity</i> is '
         f'written <code>for all s in sites, t in instants: occupancy(s, t) &lt;= capacity(s)</code>. '
         f'The checker evaluates exactly that text; nothing else decides a rule.</p>',
         f'<p class="sub">Rules are not fixed. A <b>rule set</b> chooses the rules a machine assumes and '
         f'sets their numbers, and the compiler takes one as input: it plans with the rules it knows how '
         f'to satisfy, and everything it produces is then checked against every rule of the set, including '
         f'rules it has never seen. The papers on <a href="../reproduce/">Reproduce</a> assume different '
         f'rules from this website&rsquo;s machine and from each other, and each is judged by its own rule '
         f'set. {len(core)} rules are <span class="core">core</span>: they hold in every rule set and '
         f'cannot be dropped ({", ".join(core)}).</p>',
         '<div class="verdicts">'
         '<div class="ok"><b>holds</b>the rule was evaluated and is true</div>'
         '<div class="bad"><b>fails</b>the rule is false; the page names the values that make it so</div>'
         '<div class="na"><b>does not apply</b>the execution has nothing the rule is about, and the page says what</div>'
         '<div class="unk"><b>could not be checked</b>a value the rule needs is missing, and the page says which</div>'
         '</div></div>',
         '<ul class="toc2"><li><a href="#language">the language</a></li><li><a href="#words">the words</a></li>'
         '<li><a href="#sets">rule sets</a></li><li><a href="#all">every rule</a></li>'
         '<li><a href="#yours">writing a rule</a></li></ul>']

    # -- the language
    b += ['<h2 id="language">Reading a rule</h2>',
          '<p class="sub">A rule is a formula of first-order logic over finite sets: the sites of the '
          'device, the operations of the execution, the instants at which its state is judged, and the '
          'operations of the input circuit.</p>',
          '<pre class="assert">' + highlight(
              'for all x in D: P            P holds for every x in the set D\n'
              'for all x in D where C: P    ... for every x in D for which C holds\n'
              'for some x in D: P           P holds for at least one x in D\n'
              'for all a, b in D: P         for every pair a, b in D (also a = b, unless excluded)\n'
              'P and Q,  P or Q,  not P,  P implies Q\n'
              'x = y,  x != y,  x <= y,  x in S,  x not in S,  a + b,  a * b\n'
              'count(x in D where C)        how many x in D satisfy C\n'
              'sum(e for x in D where C)    the sum of e over them (also max, min)\n'
              '{e for x in D where C}       the set of the values of e; size(S) counts it\n'
              '"C3"                         the site (or ion) named C3', all_params) + '</pre>',
          '<p class="sub">A rule may begin with <code><b>when</b> C:</code>. When C is false the rule '
          '<i>does not apply</i> to that execution, which is reported as such, never as holding. A word '
          'that an execution does not have (the steps of a paper&rsquo;s timed schedule, or the circuit when '
          'none was given) makes the rule not apply, and the page says which word.</p>']

    # -- the words
    b.append('<h2 id="words">The words</h2>')
    b.append('<p class="sub">Every word a rule may use, with what it means. Two kinds of execution are '
             'judged by the same rules: a <b>programme</b>, run one step at a time with everything in a step '
             'happening together (what this website&rsquo;s compiler produces), and a paper&rsquo;s <b>timed '
             'schedule</b>, whose operations each have a start and an end and overlap whenever the machine '
             'allows. Where a word is read differently from the two, both readings are given.</p>')
    for g, title in GROUPS:
        terms = [t for t in TERMS.values() if t.group == g]
        if not terms:
            continue
        b.append(f'<h3>{title}</h3><table class="vocab">')
        for t in sorted(terms, key=lambda t: (not t.domain, t.signature.lower())):
            name = t.name.split("(")[0]
            anchor = f' id="w-{name}"' if t.name == name else ""
            how = []
            if t.kinds == ("programme",):
                how.append("Only in a programme.")
            elif t.kinds == ("schedule",):
                how.append("Only in a paper&rsquo;s timed schedule.")
            if t.programme:
                how.append(f"In a programme: {html.escape(t.programme)}")
            if t.schedule:
                how.append(f"In a timed schedule: {html.escape(t.schedule)}")
            hows = "".join(f'<div class="how">{h}</div>' for h in how)
            b.append(f'<tr><td{anchor}><code>{html.escape(t.signature)}</code></td>'
                     f'<td>{html.escape(t.meaning)}{hows}</td></tr>')
        b.append('</table>')

    # -- the rule sets
    b.append('<h2 id="sets">Rule sets</h2>')
    b.append('<p class="sub">Each rule set names the rules it assumes and the numbers they read. The '
             'standard set is this website&rsquo;s machine: every compiled programme and every leaderboard '
             'design is checked against it. Each paper&rsquo;s set says what that paper&rsquo;s model assumes, '
             'and each paper&rsquo;s schedules, and ours beside them, are judged by it. Download a set to read '
             'it whole, or as the start of your own.</p>')
    b.append('<table class="sets"><thead><tr><th>rule set</th><th>its rules</th><th>used for</th></tr></thead><tbody>')
    for n, label, rs in sets:
        chips = "".join(f'<a href="#{rid}" class="{"core" if rs.rule(rid).core else ""}">{rid}</a>' for rid in rs.ids)
        params = "".join(f'<div class="how"><code>{html.escape(k)} = {html.escape(v)}</code>'
                         + (f' &mdash; {html.escape(PARAM_NOTES[k])}' if k in PARAM_NOTES else "")
                         + '</div>' for k, v in rs.param_text.items())
        users = papers_by_set.get(n, [])
        used = ("every programme this website compiles" if n == "standard" else
                ", ".join(f'<a href="../reproduce/{k}/">{html.escape(s)}</a>' for k, s in users) or "&mdash;")
        b.append(f'<tr id="set-{n}"><td><b>{html.escape(label)}</b><div class="how">'
                 f'<a href="sets/{n}.rules" download>{n}.rules</a></div></td>'
                 f'<td><div class="chips">{chips}</div><div class="how">{html.escape(rs.about)}</div>{params}</td>'
                 f'<td>{used}</td></tr>')
    b.append('</tbody></table>')

    # -- every rule
    b.append(f'<h2 id="all">Every rule</h2><p class="sub">All {n_rules} rules the website knows, each with '
             'the assertion the checker evaluates. Where a rule has examples, one programme that keeps it and '
             'one that breaks it are judged here by the standard rule set, and each opens as the page the '
             'studio would open on it.</p>')
    b.append('<ul class="toc2">' + "".join(f'<li><a href="#{r.id}">{r.id}</a></li>'
                                          for r in sorted(cat.rules, key=lambda r: rule_order(r.id))) + '</ul>')
    for r in sorted(cat.rules, key=lambda r: rule_order(r.id)):
        text = (f"when {r.when_text}:\n" if r.when is not None else "") + r.text
        sets_of = in_sets.get(r.id, [])
        chips = "".join(f'<a href="#set-{n}">{n}</a>' for n in sets_of) or "none of the shipped sets"
        cards = ""
        ex = built.get(r.id) or {}
        R = examples_by_rule.get(r.id) or {}
        if ex:
            own = any(isinstance(R.get(w), dict) and R[w].get("device") for w in ("pass", "fail"))
            cards = '<div class="pair">' + "".join(
                _card(ex[w], r.id, ex[w].get("device") if own else None)
                for w in ("pass", "fail") if w in ex) + '</div>'
        dev = f'<p class="src">examples on: {html.escape(R["device"]["about"])}</p>' if ex and R.get("device") else ""
        b.append(f'<section class="rule" id="{r.id}"><h3>{r.id} <span class="st">{html.escape(r.title)}</span>'
                 f'{"<span class=core>core</span>" if r.core else ""}</h3>'
                 f'<pre class="assert">{highlight(text, all_params)}</pre>'
                 f'<p class="checks">{html.escape(r.about)}</p>'
                 + (f'<p class="src">sources: {_sources(r.source)}</p>' if r.source else "")
                 + f'<p class="src chips">in the rule sets: {chips}</p>{dev}{cards}</section>')

    # -- the machine level: a floorplan of places, and a program scheduled on it
    b += _machine_section(all_params)

    # -- your own
    example = ('ruleset "my-machine"\n'
               'title "A machine without broadcast control"\n'
               'include "standard"\n'
               'drop R22                       # every electrode has its own signal\n'
               'param max_chain = 20           # R13 allows a longer chain\n'
               '\n'
               'rule X1 "No ion stands in the corner trap C3"\n'
               '  about "C3 is under repair."\n'
               '  for all i in ions, t in instants: position(i, t) != "C3"\n')
    b += ['<h2 id="yours">Writing a rule</h2>',
          '<p class="sub">A rule set is a text file. It may start from another set (<code>include</code>), '
          'pick rules by name (<code>use R1, R2</code>), remove them (<code>drop</code>), set their numbers '
          '(<code>param</code>) and add rules of its own, in the language above and with the words above, '
          'and nothing else. A word the vocabulary does not have is refused when the file is read, with the '
          'line it is on.</p>',
          f'<pre class="assert">{highlight(example, all_params | {"max_chain"})}</pre>',
          '<p class="sub">Given this set, the compiler stops splitting transport steps into one motion '
          'each (R22 is gone), lets a gate chain grow to twenty ions, and keeps everything else as on the '
          'standard machine. X1 is a rule it has never seen: it does not plan for it, and the check '
          'afterwards decides it all the same, naming every instant an ion stands in C3.</p>']
    return PAGE.format(title="Rules - QCCD studio", style=STYLE,
                       extra_css="main{max-width:1120px}" + CSS, body="\n".join(b))


#: the machine level's rule sets, in the order the page shows them
MACHINE_SETS = (("floorplan", "A floorplan's rules"), ("machine", "A schedule's rules"))
MACHINE_GROUPS = (("floorplan", "Words about a floorplan"),
                  ("machine", "Words about a program scheduled on it"))


def _machine_section(all_params) -> list[str]:
    """F1-F9 and G1-G14: the same language, one level up."""
    b = ['<h2 id="machine">One level up: the machine</h2>',
         '<p class="sub">A fault-tolerant machine is built from <b>places</b> -- each a small device '
         'whose every op is checked by the rules above, then used as a box with doors -- joined by '
         '<b>roads</b> that carry bundles of ions and <b>wires</b> that carry bits. Where the places '
         'stand is its <b>floorplan</b>, and a logical program run on it is a schedule of ops at places '
         'and bundles on roads, many at once. The same language judges both, with its own words. The '
         'floorplan rules (F) hold before any program runs; the schedule rules (G) hold on every run, '
         'and two of them, G5 and G13, are why work may run in parallel: a place does one op at a time '
         'and a road carries one bundle at a time, so whatever runs at once holds different things. '
         'The gadget design pages show every one of these rules, decided, beside the machine they '
         'judge.</p>']
    for g, title in MACHINE_GROUPS:
        terms = [t for t in TERMS.values() if t.group == g]
        if not terms:
            continue
        b.append(f'<h3>{title}</h3><table class="vocab">')
        for t in sorted(terms, key=lambda t: (not t.domain, t.signature.lower())):
            name = t.name.split("(")[0]
            anchor = f' id="w-{name}"' if t.name == name else ""
            b.append(f'<tr><td{anchor}><code>{html.escape(t.signature)}</code></td>'
                     f'<td>{html.escape(t.meaning)}</td></tr>')
        b.append('</table>')
    for n, label in MACHINE_SETS:
        rs = load(n)
        b.append(f'<h3 id="set-{n}">{html.escape(label)} '
                 f'<span class="st"><a href="sets/{n}.rules" download>{n}.rules</a></span></h3>'
                 f'<p class="sub">{html.escape(rs.about)}</p>')
        for r in sorted(rs.rules, key=lambda r: rule_order(r.id)):
            text = (f"when {r.when_text}:\n" if r.when is not None else "") + r.text
            b.append(f'<section class="rule" id="{r.id}"><h3>{r.id} <span class="st">'
                     f'{html.escape(r.title)}</span>{"<span class=core>core</span>" if r.core else ""}</h3>'
                     f'<pre class="assert">{highlight(text, all_params | set(rs.params))}</pre>'
                     f'<p class="checks">{html.escape(r.about)}</p>'
                     + (f'<p class="src">sources: {_sources(r.source)}</p>' if r.source else "")
                     + '</section>')
    return b


def downloads() -> list[tuple[str, bytes]]:
    """Each shipped rule set, resolved into the one file the compiler is given -- and the
    machine level's two."""
    out = [(f"rules/sets/{n}.rules", rs.text().encode("utf-8")) for n, _, rs in _sets()]
    out += [(f"rules/sets/{n}.rules", load(n).text().encode("utf-8")) for n, _ in MACHINE_SETS]
    return out
