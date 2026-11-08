"""The Rules page, written from the rule language itself (`qccd.ruleset`).

Every rule is printed as the assertion the checker evaluates -- the same text, never a
paraphrase of it -- with each word linked to its definition, and every rule set the site
ships is listed with the rules it picks.  A rule set can be downloaded as the one file the
compiler is given.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..ruleset import RuleSet, catalogue, load, names, rule_order
from ..ruleset.syntax import KEYWORDS
from ..ruleset.vocabulary import TERMS
from . import learn

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
    # the C2LR boards' rules (docs/c2lr.md): the checkerboard steps allowed and marked assumed,
    # and refused in the second, which keeps to what arXiv:2403.00756 demonstrated
    ("c2lr", "C2LR swap-or-stay"),
    ("c2lr-demonstrated", "C2LR swap-or-stay, as demonstrated"),
    # Goal 3's WISE and Cyclone, as the transport deck writes them (goals/goal3/DECK.md)
    ("wise", "WISE"),
    ("cyclone-deck", "Cyclone, as the transport deck writes it"),
)

#: The Reproduce papers that are not replays of an artifact's runs, and their rule sets.
MODEL_PAPER_SETS = {"ruan2025": ("trapsimd",),
                    "schoenberger2024": ("ionshuttler", "ionshuttler-heuristic")}

#: What each parameter a shipped rule set sets means, in a line.
PARAM_NOTES = {
    "max_chain": "the most ions a gate's chain may hold (R13)",
    "serial_kinds": "the operation kinds that hold their trap one at a time (R27), named as kind(o) names them",
    "single_ion_zones": "the zones single-ion operations may run in (R32)",
    "c2lr_forms": ("how a C2LR step may pair legs (C6): \"diagonal\", one diagonal at every junction "
                   "(demonstrated), and \"checkerboard\", two pairs at the junctions of one colour (assumed)"),
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
.rule .runbox iframe.live{height:420px}
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
            ym = re.search(r"(\d{4})-(\d{2})", tok)
            month = ("January February March April May June July August September October "
                     "November December").split()[int(ym.group(2)) - 1] if ym else "September"
            out.append(f"collaborators' review, {month} {ym.group(1) if ym else '2026'}")
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
            + learn.runbox(ex["page"], ex.get("poster", ""), "the example, on its page")
            + f'<a class="open" href="{ex["page"]}#step=1">open the page, with its Report pane</a></div>')


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
         learn.strip("rules"),
         learn.summary([
             '<b>What a rule is.</b> One sentence of logic about the device and a programme on it, evaluated on '
             'the replay, true or false. You will be able to read every rule on this page in its own words.',
             f'<b>What the rules are for.</b> They are the hardware\'s side of the bargain: capacity, junctions, '
             f'one waveform per cycle, cold ions at a gate. A programme that breaks one is refused, and the page '
             f'says which values broke it. {len(core)} of the {n_rules} are <span class="core">core</span> and hold '
             f'on every machine.',
             '<b>Rule sets.</b> Different machines, and the papers that model them, assume different rules. A rule '
             'set names the rules and their numbers; the compiler plans with it and the checker judges with it.',
             '<b>Every rule, with a programme that keeps it and one that breaks it</b>, both playable, so a rule '
             'is never only a formula.',
             '<b>Your own rule</b>, in the same language, which the checker then decides without the compiler '
             'ever having heard of it.',
         ], lead='The <a href="../language/">Language</a> page gave you the statements a programme is made of. '
                 'This page is what a programme may and may not do with them.'),
         learn.ask('Who decides whether a programme could really run on the hardware: the compiler that wrote it, '
                   'or someone else?',
                   'Someone else. The compiler is a heuristic that may be wrong; the rules are a short text that '
                   'anyone can read, and a checker that shares no code with the compiler evaluates exactly that '
                   'text on the replay. That is why a rule is written as a formula and shown here as one.'),
         '<ul class="toc2"><li><a href="#language">the language</a></li><li><a href="#words">the words</a></li>'
         '<li><a href="#sets">rule sets</a></li><li><a href="#all">every rule</a></li>'
         '<li><a href="#yours">writing a rule</a></li></ul>']

    # -- the language
    b += ['<h2 id="language">Reading a rule</h2>',
          learn.section('the ten shapes a rule can take. They are ordinary logic (for all, for some, and, or, '
                        'counts and sums) over four finite sets: sites, operations, instants, and the circuit. '
                        'After this section every rule on the page reads as a sentence.'),
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
    b.append(learn.bridge('A formula is only as clear as its words. The next section is the vocabulary: every '
                          'name a rule may use and what it reads off the replay. Skim it now and come back to it '
                          'from any rule below; each word is a link.'))
    b.append('<h2 id="words">The words</h2>')
    b.append(learn.section('the dictionary of the rule language, grouped by what the words are about: the device, '
                           'the execution, the instants, the circuit.'))
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
    b.append(learn.ask('Is there one list of rules for every trapped-ion machine?',
                       'No. A machine with a signal per electrode has no one-waveform rule; a swap-or-stay lattice '
                       'has rules no ring has. So the rules come in sets, and a design is judged by the set its '
                       'machine declares.'))
    b.append('<h2 id="sets">Rule sets</h2>')
    b.append(learn.section('the shipped rule sets, each with the rules it contains and the numbers it fixes, and '
                           'which boards and papers each one judges. The standard set is this website\'s machine.'))
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
                '<a href="../board/#c2lr">the C2LR boards</a>' if n == "c2lr" else
                ", ".join(f'<a href="../reproduce/{k}/">{html.escape(s)}</a>' for k, s in users) or "&mdash;")
        b.append(f'<tr id="set-{n}"><td><b>{html.escape(label)}</b><div class="how">'
                 f'<a href="sets/{n}.rules" download>{n}.rules</a></div></td>'
                 f'<td><div class="chips">{chips}</div><div class="how">{html.escape(rs.about)}</div>{params}</td>'
                 f'<td>{used}</td></tr>')
    b.append('</tbody></table>')

    # -- every rule
    b.append(learn.bridge('Now the rules themselves. Each card is one rule: the formula the checker evaluates, a '
                          'sentence on what it protects, where it comes from, and two small programmes, one that '
                          'keeps it and one that breaks it. Press Play on either to watch the cycle the verdict '
                          'names.'))
    b.append(f'<h2 id="all">Every rule</h2>'
             + learn.section(f'all {n_rules} rules, grouped by what they are about (the device\'s geometry, '
                             f'occupancy, transport, control, gates, heat, cost, correctness), in the reader\'s '
                             f'browser. The group headings are the map; the cards are the territory.')
             + f'<p class="sub">All {n_rules} rules the website knows, each with '
             'the assertion the checker evaluates. Where a rule has examples, one programme that keeps it and '
             'one that breaks it are judged here by the standard rule set (the C2LR rules by the set c2lr, on '
             'a C2LR lattice), and each opens as the page the studio would open on it.</p>')
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
          learn.section('a rule set is a text file you can write. One example: a machine without broadcast '
                        'control, with one rule of its own.'),
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
    b.append(learn.recap([
        'A rule is one formula over four finite sets, evaluated on the replay of a programme. It holds, fails '
        '(naming the values), does not apply, or could not be checked; it is never trusted on anyone\'s word.',
        f'The rules are the hardware\'s constraints made explicit: capacity (R1), junctions (R2), one waveform per '
        f'cycle (R22), cold ions at a gate (R7), and the rest. {len(core)} core rules hold on every machine; the '
        f'others come in rule sets, one per machine model.',
        'The compiler plans with a rule set and is then judged by it, including rules it never saw. A failed '
        'rule on a compiled programme is a compiler bug made visible, not a programme a machine would refuse.',
        'You can write a rule set. The checker decides a new rule the same way it decides the shipped ones.',
    ], "rules", more='The Compilation page shows the other side: how the compiler keeps a programme inside '
                     'these rules while it routes, and which checks run on its output afterwards.'))
    # "Every rule" grouped into categories in the reader's browser, the cards and ids unchanged
    page_body = learn.apply("\n".join(b), "rules/", 1)
    page_body += "<script>\n" + (CATEGORIES / "rules.js").read_text(encoding="utf-8") + "\n</script>"
    return PAGE.format(title="Rules - QCCD studio", style=STYLE,
                       extra_css="main{max-width:1120px}" + CSS + (CATEGORIES / "rules.css").read_text(encoding="utf-8"),
                       body=page_body)


#: the rules' categories (qccd/workspace/web/rules.js), which the workspace's mirror of the site
#: also lays over this page
CATEGORIES = Path(__file__).resolve().parents[1] / "workspace" / "web"


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
