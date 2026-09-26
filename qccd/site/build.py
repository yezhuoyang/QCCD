"""`python -m qccd site` -- the website, as static files.

The site is a build target that ARRANGES what exists: the studio page (`qccd studio`),
the reference docs (`docs/*.md`), the boards `Codesign/scripts/bb_studio.py` already
rendered for the five tasks, and a discussion page.  It writes plain HTML.  What it adds
to a page is the 40 px navigation bar with its search box and the comment layer
(`comments.html`): a signed-in reader pins a note to any spot of any page, and only
signed-in readers see the notes -- and only an invited reader has an account at all.  The one process behind the site is that layer's API,
`comments_api.py` (accounts, sessions and threads in SQLite, standard library only),
reached through nginx at `/api/`; the only third-party script is giscus on /discuss/.

    site/                  the landing: one sentence, four tiles
    site/studio.html       the design tool; #learn opens the course, #learn=B2 a lesson,
                           #design the blank canvas
    site/learn/            the learning path: the course, the docs, then a real design
    site/design/           -> studio.html#design
    site/board/            the five tasks; board/<task>/ is the ranking page, and the
                           entry pages sit beside it exactly as bb_studio.py wrote them
    site/discuss/          GitHub Discussions, embedded
    site/docs/<name>/      adl, tsir, rules, phys, rendered from docs/*.md
    site/physics/          the physics background, rendered from qccd/site/physics.md
    site/people/           who takes part, from qccd/site/people.py
    site/publications/     the papers the site is built on, from qccd/site/publications.py

A task is `tasks/<id>/task.json`; its `seed` names the directory whose `manifest.json`
and pages are the board's rows today (`BBResults/studio`, `SmallCode/<code>`).
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from .compile_examples import GATES, OUT as COMPILED, load_compiled
from .examples import GRAMMAR, IR_TABLE, RULES, VERBS, build_example, machine, rule_meta
from .md import Renderer, hints, slug
from . import prose
from .people import GROUPS as PEOPLE_GROUPS, INSTITUTIONS, JOIN, PEOPLE
from .publications import GROUPS as PUB_GROUPS, PUBS, SOFTWARE

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
TASKS = ROOT / "tasks"
TUTORIAL_JS = ROOT / "qccd" / "viz" / "js" / "tutorial.js"
REPO = "https://github.com/yezhuoyang/QCCD"
DOC_NAMES = ("adl", "tsir", "rules", "phys")
MARKER = ".qccd-site"

PARTS = (("learn", "Learn", "learn/", "the course, the reference docs, and worked examples"),
         ("design", "Design", "studio.html#design", "build a device and program it, in the browser"),
         ("board", "Leaderboard", "board/", "five tasks, every design ranked"),
         ("discuss", "Discuss", "discuss/", "rules, bugs and features, on GitHub Discussions"))

#: The running example on the landing: one seed entry, embedded as the page it is, in
#: embed mode (HASH_JS) so only its stage and transport show.
EXAMPLE = {"task": "five_qubit", "device": "ring24_8",
           "what": "The five-qubit code's syndrome round on a 24-site ring with 8 docks"}

#: The ranking pages' family colours (bb_studio.py's FAMHEX); everything else is grey.
FAMHEX = {"ring + docks": "#2a78d6", "lattice": "#eb6834", "torus": "#1baf7a", "random graph": "#4a3aa7"}
GREY = "#8a8985"

#: One pictogram and one sentence per part of the course, keyed by the part id.
PART_ART = {
    "A": ("The map you will build on: sites, junctions, segments, loops and zones, and what one move costs.",
          '<svg class="ic" viewBox="0 0 64 64" aria-hidden="true"><rect x="8" y="14" width="48" height="36" rx="18" fill="none" stroke="#2a78d6" stroke-width="3"/>'
          '<rect x="26" y="4" width="12" height="9" rx="2" fill="#e8b940"/><rect x="26" y="51" width="12" height="9" rx="2" fill="#e8b940"/>'
          '<circle cx="20" cy="14" r="4" fill="#1c2a4a"/><circle cx="44" cy="50" r="4" fill="#1c2a4a"/><circle cx="56" cy="32" r="4" fill="#1c2a4a"/></svg>'),
    "B": ("Ions on the map: a CNOT, a Bell pair and a docked rotation, written by hand in the eleven verbs.",
          '<svg class="ic" viewBox="0 0 64 64" aria-hidden="true"><rect x="8" y="10" width="48" height="44" rx="6" fill="none" stroke="#eb6834" stroke-width="3"/>'
          '<path d="M16 22h12M16 30h22M16 38h16M16 46h26" stroke="#eb6834" stroke-width="3" stroke-linecap="round"/>'
          '<path d="M44 20l6 4-6 4" fill="none" stroke="#1c2a4a" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
    "R": ("Every rule the machine obeys, broken on purpose and repaired, or the honest reason it cannot be checked here.",
          '<svg class="ic" viewBox="0 0 64 64" aria-hidden="true"><path d="M32 6l22 8v16c0 14-9 24-22 28C19 54 10 44 10 30V14z" fill="none" stroke="#1baf7a" stroke-width="3" stroke-linejoin="round"/>'
          '<path d="M22 32l7 7 13-14" fill="none" stroke="#1baf7a" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
    "C": ("A syndrome round and a plaquette, scheduled by hand under all the rules, with the heat paid for.",
          '<svg class="ic" viewBox="0 0 64 64" aria-hidden="true"><path d="M6 20h52M6 44h52" stroke="#4a3aa7" stroke-width="3" stroke-linecap="round"/>'
          '<circle cx="24" cy="20" r="4.5" fill="#4a3aa7"/><path d="M24 20v24" stroke="#4a3aa7" stroke-width="3"/>'
          '<circle cx="24" cy="44" r="8" fill="#fff" stroke="#4a3aa7" stroke-width="3"/><path d="M24 36v16M16 44h16" stroke="#4a3aa7" stroke-width="3"/>'
          '<rect x="42" y="12" width="14" height="16" rx="2" fill="#fff" stroke="#4a3aa7" stroke-width="3"/>'
          '<text x="49" y="24.5" font-size="11" font-weight="700" text-anchor="middle" fill="#4a3aa7" font-family="ui-sans-serif,system-ui,sans-serif">H</text></svg>'),
    "D": ("Let the compiler do it, then judge an architecture from measured numbers on the leaderboard's tasks.",
          '<svg class="ic" viewBox="0 0 64 64" aria-hidden="true"><path d="M10 56h44" stroke="#8a8985" stroke-width="3" stroke-linecap="round"/>'
          '<rect x="14" y="30" width="9" height="24" rx="2" fill="#2a78d6"/><rect x="28" y="18" width="9" height="36" rx="2" fill="#eb6834"/><rect x="42" y="38" width="9" height="16" rx="2" fill="#1baf7a"/>'
          '<path d="M12 14l14-6 12 8 16-8" fill="none" stroke="#1c2a4a" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
}
PART_COLOR = {"A": "#2a78d6", "B": "#eb6834", "R": "#1baf7a", "C": "#4a3aa7", "D": "#1c2a4a"}
DOC_TAGS = {"adl": "device language", "tsir": "control IR", "rules": "the rules", "phys": "physics and metal"}
DOC_BLURBS = {"adl": "One document describes a machine: its trap graph, zones, wiring and the curves of its primitives.",
              "tsir": "The eleven verbs a programme is written in, and what each one costs.",
              "rules": "All 27 rules, what each one checks, and what the verifier can honestly say about it.",
              "phys": "From the graph to electrodes: the metal a device implies, and its design rules."}


def _t(r: dict):
    return (r.get("numbers") or {}).get("T_jones")


def fam_color(f: str) -> str:
    return FAMHEX.get(f, GREY)


def short_name(r: dict) -> str:
    """The design's name: the row's short name, else its key without the rank prefix."""
    return r.get("short") or re.sub(r"^\d+_", "", r["key"])


STYLE = """
/* Three depths and no more: the desk the page lies on (--surface), the sheet the words
   are printed on (--paper), and the cards that carry one item each (--card).  The sheet is
   the lightest thing on the screen, so the eye goes to the words. */
:root{color-scheme:light;
 --surface:#eceae3;--paper:#fff;--card:#fbfaf7;
 --ink:#15141a;--ink2:#56545e;--ink3:#8a8892;--head:#1a2540;
 --line:#e4e2db;--grid:#efeee9;--accent:#1f5bb5;--soft:#eaf0fa;
 --serif:"Iowan Old Style","Palatino Linotype",Palatino,Charter,"Bitstream Charter","Sitka Text",Cambria,Georgia,serif;
 --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 --mono:ui-monospace,"SF Mono","Cascadia Mono","JetBrains Mono",Menlo,Consolas,"Liberation Mono",monospace;
 --radius:16px;--shadow:0 1px 2px rgba(18,16,28,.05),0 12px 34px rgba(18,16,28,.07)}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--surface);color:var(--ink);font:16px/1.68 var(--serif);
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
#sitenav{position:sticky;top:0}
main{max-width:868px;margin:26px auto 44px;padding:46px 52px 60px;background:var(--paper);
 border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}
main.wide{max-width:1180px}
/* prose keeps a readable measure even when the sheet is wide */
main>p,main>ul,main>ol,main>blockquote,main>.sub{max-width:80ch}
h1,h2,h3,h4{font-family:var(--serif);color:var(--head)}
h1{font-size:42px;font-weight:600;margin:0 0 12px;letter-spacing:-.022em;line-height:1.1}
h2{font-size:27px;font-weight:600;margin:46px 0 10px;letter-spacing:-.012em;line-height:1.22}
h3{font-size:19.5px;font-weight:600;margin:28px 0 7px;line-height:1.3}
main h2[id],main h3[id],main section[id],main article[id],main li[id]{scroll-margin-top:72px}
p,li{color:var(--ink)} p{margin:0 0 15px}
.sub{color:var(--ink2);font-size:18.5px;line-height:1.6}
h1+.sub{font-size:19.5px;margin-bottom:26px}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
strong,b{font-weight:600}
/* the code face is its own world: mono, panelled, and the words that matter picked out */
code{font-family:var(--mono);font-size:.855em;background:#f2f1ec;padding:.1em .38em;border-radius:5px;color:#26304a}
pre{font-family:var(--mono);font-size:13px;line-height:1.62;background:#faf9f5;color:#22212a;
 border:1px solid var(--line);border-left:3px solid #cfd6e4;border-radius:10px;
 padding:14px 16px;overflow-x:auto;margin:16px 0}
pre code{background:none;padding:0;font-size:inherit;color:inherit}
/* anything that is a control, a label or a table of numbers reads as interface, not prose */
.card,.doc,.pt,.task,.stat,.stats,.bar,.badge,.legend,.stages,.ex,.verdict,.contract,.kv,
table,dl.sem,.toc,.toc2,.lessons,.person,.inst,.pub,.fig figcaption,.gallery figcaption,
.note,.refused,.tag,.lab,.src,.meta,.prog{font-family:var(--sans)}
.tw{overflow-x:auto;margin:14px 0} table{border-collapse:collapse;font-size:13px;margin:8px 0;width:100%}
th,td{padding:5px 10px 5px 0;border-bottom:1px solid var(--grid);vertical-align:top} th{color:var(--ink3);font-weight:500}
blockquote{margin:0;padding:0 0 0 14px;border-left:3px solid var(--line);color:var(--ink2)}
hr{border:0;border-top:1px solid var(--line);margin:24px 0}
h1 .anchor,h2 .anchor,h3 .anchor,h4 .anchor{opacity:0;margin-left:8px;font-weight:400;color:var(--ink3)}
h1:hover .anchor,h2:hover .anchor,h3:hover .anchor,h4:hover .anchor{opacity:1}
.h{border-bottom:1px dotted var(--accent);position:relative;cursor:help}
.h:hover::after{content:attr(data-t) " \\2014  " attr(data-d);position:absolute;left:0;top:1.5em;z-index:20;width:max-content;max-width:360px;
 background:#fff;color:var(--ink);border:1px solid #cfceca;border-radius:6px;padding:7px 10px;font-size:12.5px;line-height:1.4;box-shadow:0 2px 10px rgba(0,0,0,.1);white-space:normal}
.toc{font-size:13px;color:var(--ink2);margin:0 0 18px;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:4px 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:16px}
.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;color:inherit}
.card:hover{border-color:var(--accent);text-decoration:none} .card h2{font-size:16px;margin:0 0 6px;color:#1c2a4a}
.card p{margin:0 0 6px;color:var(--ink2);font-size:13px} .card .best{color:var(--ink)}
.card table{width:100%;font-size:12.5px} .card td:nth-child(n+2),.card th:nth-child(n+2){text-align:right;font-variant-numeric:tabular-nums}
.lessons{list-style:none;padding:0;margin:4px 0 0} .lessons li{padding:3px 0;display:flex;gap:10px;align-items:baseline}
.lessons .id{color:var(--ink3);font-variant-numeric:tabular-nums;width:2.4em;flex:0 0 auto} .lessons .stars{color:#d59a00;margin-left:auto;font-size:12px;white-space:nowrap}
.part{margin:18px 0 0} .part h3{margin:0 0 2px} .part .ms{color:var(--ink2);font-size:13px;margin:0}
.note{background:var(--soft);border:1px solid #d6e2f4;border-left:3px solid var(--accent);border-radius:10px;
 padding:12px 16px;margin:18px 0;color:#32425e;font-size:13.5px;max-width:78ch}
/* learn: the path, one row per part */
.path{display:flex;flex-direction:column;gap:14px;margin:10px 0 0}
.pt{display:grid;grid-template-columns:72px 1fr;gap:20px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 24px}
.pt .ic{width:64px;height:64px;display:block} .pt h3{margin:0 0 2px;font-size:16px}
.pt .kick{font-size:11px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;margin-bottom:2px}
.pt .sum{color:var(--ink2);margin:0 0 8px;font-size:13.5px}
.pt .lessons{columns:2;column-gap:28px} .pt .lessons li{break-inside:avoid}
.pt .prog{font-size:12px;color:var(--ink3);margin-left:auto;white-space:nowrap}
.docs{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.doc{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;color:inherit}
.doc:hover{border-color:var(--accent);text-decoration:none} .doc b{display:block;color:#1c2a4a;margin-bottom:2px} .doc span{color:var(--ink2);font-size:13px}
.doc .tag{display:inline-block;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:700;margin-bottom:4px}
/* board: one row per task */
.task{display:grid;grid-template-columns:240px 1fr;gap:28px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 26px;margin:18px 0}
.task h2{margin:0 0 4px;font-size:17px} .task h2 a{color:#1c2a4a} .task .desc{color:var(--ink2);font-size:13px;margin:0 0 10px}
.task .more{font-size:13px} .task .meta{font-size:12.5px;color:var(--ink3);margin:8px 0 0}
.bars{display:flex;flex-direction:column;gap:3px}
.bar{display:grid;grid-template-columns:168px 1fr 68px;align-items:center;gap:8px;font-size:12.5px;color:inherit;text-decoration:none}
.bar:hover{text-decoration:none} .bar:hover .lbl{color:var(--accent)}
.bar .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .tr{display:block;height:10px;background:var(--grid);border-radius:3px;overflow:hidden} .bar .fl{display:block;height:100%;border-radius:3px}
.bar .v{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2)}
.bar.best .lbl,.bar.best .v{font-weight:700;color:#0b7a4b} .bar.bad .lbl{color:#c62828}
.bar .rank{display:inline-block;width:20px;height:20px;border-radius:10px;background:#1c2a4a;color:#fff;font-size:11.5px;font-weight:700;text-align:center;line-height:20px;margin-right:8px;vertical-align:1px}
.bar .rank.r1{background:#0b7a4b} .bar .rank.r2{background:#52514e} .bar .rank.r3{background:#8a8985}
.bar{grid-template-columns:200px 1fr 68px;font-size:13.5px;padding:3px 0} .refused a{color:var(--accent)}
.refused{font-size:12px;color:var(--ink3);margin-top:8px} .refused b{color:#c62828;font-weight:600}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;align-content:start;margin-top:14px}
.stat{border:1px solid var(--line);border-radius:8px;padding:7px 10px} .stat b{display:block;font-size:19px;line-height:1.15;font-variant-numeric:tabular-nums}
.stat small{display:block;font-size:11.5px;color:var(--ink2);margin-top:1px}
.stat span{display:block;font-size:10.5px;color:var(--ink3);text-transform:uppercase;letter-spacing:.06em}
.stat.good b{color:#0b7a4b} .stat.lean b{color:var(--accent)} .stat.warn b{color:#c62828}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--ink2);margin:10px 0 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
@media (max-width:900px){.task{grid-template-columns:1fr}.pt .lessons{columns:1}.stats{grid-template-columns:1fr 1fr}}
/* language and rules: statements and rules with their running examples */
main.wide{max-width:1120px}
pre.grammar{font-size:12.5px;line-height:1.6}
/* one statement, one rule, one gate: each is a card with air around it, never a run of
   paragraphs separated by a hairline that the eye slides straight past */
.verb,.rule,.gaterow{background:var(--card);border:1px solid var(--line);border-radius:14px;
 padding:24px 26px 20px;margin:26px 0;scroll-margin-top:72px;box-shadow:0 1px 2px rgba(18,16,28,.04)}
.verb h3,.rule h3{margin:0 0 8px;font-size:21px}
.verb h3 code,.rule h3 code{font-size:19px;background:none;padding:0;color:var(--head)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:26px;align-items:start}
dl.sem{margin:8px 0 0;display:grid;grid-template-columns:64px 1fr;gap:5px 10px;font-size:13px}
dl.sem dt{color:var(--ink3);text-transform:uppercase;font-size:10.5px;letter-spacing:.06em;padding-top:3px} dl.sem dd{margin:0}
.ex{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:14px 16px;position:relative}
.ex pre{margin:0 0 8px;background:#f7f6f2;font-size:12px;padding:10px 12px;overflow-x:auto} .ex pre.ir{white-space:pre-wrap;word-break:break-all}
.two>*,.pair>*{min-width:0}
.ex .tag{position:absolute;top:10px;right:12px;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;font-weight:700;padding:2px 8px;border-radius:10px;background:#efeeeb;color:#52514e}
.ex.pass .tag{background:#e6f4ec;color:#0b7a4b} .ex.fail .tag{background:#fbe9e7;color:#c62828} .ex.partial .tag{background:#fff3e0;color:#b26a00} .ex.any .tag{background:#eef3fb;color:#2a78d6}
.ex.fail{border-color:#f1c4c0} .ex.pass{border-color:#bfe3cf}
.ex .why{font-size:12.5px;color:var(--ink2);margin:0 0 8px;padding-right:70px}
.verdict{font-size:12.5px;margin:0 0 8px} .verdict b.ok{color:#0b7a4b} .verdict b.bad{color:#c62828} .verdict b.skip{color:#52514e} .verdict b.partial{color:#b26a00}
.verdict .m{display:block;color:var(--ink2);margin-top:2px} .verdict .nums{display:block;color:var(--ink3);margin-top:3px}
.runbox{margin:6px 0 4px}
.runbox iframe.live{display:block;width:100%;height:300px;border:1px solid var(--line);border-radius:8px;background:#fff}
.ex .open{font-size:12px}
.rule h3 .st{font-weight:400;color:var(--ink2);font-size:16px} .rule .checks{margin:0 0 4px;font-size:14px}
.rule .src{font-size:12px;color:var(--ink3);margin:0 0 12px} .pair{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:stretch}
/* the two cards of a pair share their rows, so programme, verdict and the running machine line up */
.pair>.ex{display:grid;grid-template-rows:subgrid;grid-row:span 5;align-content:start}
.pair>.ex>.runbox{align-self:end} .pair>.ex>.verdict{align-self:start}
.toc2{font-size:13px;display:flex;flex-wrap:wrap;gap:4px 12px;margin:0 0 6px;padding:0;list-style:none}
.contract{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:10px 0 0} .contract div{border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:12.5px;background:var(--card)}
.contract b{display:block;margin-bottom:2px} .contract .ok b{color:#0b7a4b} .contract .bad b{color:#c62828} .contract .skip b{color:#52514e} .contract .partial b{color:#b26a00}
@media (max-width:860px){.two,.pair{grid-template-columns:1fr}.contract{grid-template-columns:1fr 1fr}}
/* compilation: the pipeline and the compiled gates */
.stages{counter-reset:st;list-style:none;padding:0;margin:10px 0 0;display:grid;gap:8px}
.stages li{display:grid;grid-template-columns:34px 190px 1fr;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 16px;font-size:13.5px}
.stages li:before{counter-increment:st;content:counter(st);width:26px;height:26px;border-radius:13px;background:#1c2a4a;color:#fff;font-weight:700;font-size:12.5px;display:flex;align-items:center;justify-content:center}
.stages b{color:#1c2a4a} .stages .art{color:var(--ink3);font-size:12px;display:block;margin-top:2px}
.stages .out{font-size:12.5px;color:var(--ink2)} .stages .out code{font-size:11.5px}
.badge{font-size:11.5px;padding:2px 8px;border-radius:10px;background:#efeeeb;color:#52514e}
.badge.ok{background:#e6f4ec;color:#0b7a4b} .badge.bad{background:#fbe9e7;color:#c62828} .badge.lean{background:#eef3fb;color:#2a78d6}
.runbox.tall iframe.live{height:360px}
.gaterow>h3{margin:0 0 6px;font-size:21px;display:flex;flex-wrap:wrap;align-items:baseline;gap:10px}
.gaterow h3 code{font-size:19px;background:none;padding:0;color:var(--head)}
.gaterow h3 .st{font-weight:400;color:var(--ink2);font-size:14.5px;font-family:var(--sans)}
.gaterow>.src{margin:0 0 16px;padding-bottom:14px;border-bottom:1px solid var(--line)}
/* the two columns carry the same weight: neither the drawing nor the words dwarf the other,
   and both start on the same line so the reader knows where to begin */
.gr{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:30px;align-items:start}
.gr>*{min-width:0;display:flex;flex-direction:column}
.gr .runbox.tall{flex:0 0 auto;display:block;min-height:0}
.gr .runbox.tall iframe.live{height:330px;min-height:330px;flex:0 0 auto}
/* a listing is a listing, not a column of its own: it scrolls rather than stretching the row */
.gr pre{max-height:320px;overflow:auto}
.gr .lab,.gr pre,.gr table,.gr .badges,.gr .open,.gr .pulses,.gr svg{flex:0 0 auto}
.gr pre{margin:0 0 6px;font-size:11.5px;padding:8px 10px;background:#f7f6f2;overflow-x:auto}
.gr .lab{font-family:var(--sans);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
 font-weight:700;color:var(--ink3);margin:16px 0 5px} .gr .lab:first-child{margin-top:0}
.gr .badges{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px}
svg.qc{display:block;width:100%;max-width:320px;height:auto;background:var(--paper);
 border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:0 0 8px}
@media (max-width:860px){.gr{grid-template-columns:1fr}}
.kv{font-size:12.5px;border-collapse:collapse} .kv td{padding:2px 12px 2px 0;border:0;vertical-align:top} .kv td:first-child{color:var(--ink3)}
.pulses code{display:block;font-size:11.5px;background:#f7f6f2;padding:2px 6px;border-radius:4px;margin:2px 0}
/* a gate set is a long list nobody reads at a glance: name it, and open it if you want it */
details.gset summary{cursor:pointer;color:var(--accent);font-family:var(--sans);font-size:12.5px;list-style:none}
details.gset summary::-webkit-details-marker{display:none}
details.gset summary:before{content:"\25b8 ";color:var(--ink3)} details.gset[open] summary:before{content:"\25be "}
details.gset code{display:block;margin:6px 0 2px;line-height:1.7}
dl.native{display:grid;grid-template-columns:auto 1fr;gap:8px 18px;margin:14px 0 0;align-items:baseline;max-width:74ch}
dl.native dt{font-family:var(--mono);font-size:13.5px;color:var(--head);font-weight:600;white-space:nowrap}
dl.native dd{margin:0;color:var(--ink)}

/* physics, people and publications */
.folk{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin:10px 0 0}
.person{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;scroll-margin-top:72px}
.person b{display:block;font-size:16px;color:#1c2a4a} .person .role{color:var(--accent);font-size:12.5px;font-weight:600;letter-spacing:.02em}
.person .aff{color:var(--ink2);font-size:13px;margin:2px 0 8px} .person p{margin:0 0 8px;font-size:13.5px}
.person .links a{font-size:12.5px;margin-right:10px}
.inst{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin:10px 0 0}
.inst a,.inst .card{display:flex;flex-direction:column;align-items:flex-start;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;color:inherit}
.inst a:hover{border-color:var(--accent);text-decoration:none} .inst img{height:34px;width:auto;max-width:100%;object-fit:contain;object-position:left center;flex:0 0 auto} .inst img[alt="UCLA"]{height:26px}
.inst b{display:block;color:#1c2a4a} .inst span{color:var(--ink2);font-size:12.5px}
.pubs{list-style:none;padding:0;margin:8px 0 0}
.pub{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 12px;scroll-margin-top:72px}
.pub .ti{font-size:15px;font-weight:600;color:#1c2a4a;margin:0 0 2px} .pub .au{font-size:13px;margin:0 0 2px}
.pub .ve{font-size:12.5px;color:var(--ink2);margin:0 0 6px} .pub .ve a{margin-left:10px;white-space:nowrap}
.pub .use{font-size:12.5px;color:var(--ink2);margin:0;padding-top:6px;border-top:1px dashed var(--grid)}
.pub .use b{color:var(--ink3);font-weight:500;text-transform:uppercase;font-size:10.5px;letter-spacing:.07em;margin-right:6px}
.cite pre{font-size:12px}

/* physics: figures drawn from the device data, embeds, photographs */
.fig{margin:16px 0 22px} .fig figcaption{font-size:12.5px;color:var(--ink2);margin-top:7px;line-height:1.45}
.fig .credit{color:var(--ink3)} .fig img{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:12px;background:var(--paper)}
.figsvg{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:12px 14px} .figsvg svg{display:block;width:100%;height:auto}
.fig iframe.live{display:block;width:100%;height:340px;border:1px solid var(--line);border-radius:10px;background:#fff}
.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin:12px 0 22px}
.gallery figure{margin:0} .gallery .figsvg{padding:8px 10px}
.gallery figcaption{font-size:12.5px;color:var(--ink2);margin-top:6px} .gallery figcaption b{color:#1c2a4a}
.legend.zones{margin:6px 0 0;font-size:12px}
table.formulas td:first-child{white-space:nowrap;color:var(--ink)} table.formulas td:nth-child(2) code{font-size:12.5px;white-space:nowrap}

@media (max-width:760px){
 main{margin:0;border-radius:0;border-left:0;border-right:0;padding:28px 20px 44px}
 body{font-size:15.5px} h1{font-size:32px} h2{font-size:23px} h3{font-size:18px}
 .verb,.rule,.gaterow{padding:18px 16px 14px;margin:20px 0}}
"""


APP_CSS = "<style>main{height:calc(100vh - 40px)!important}</style>"

# The footer on every document page: who leads the project and who funds it, with the
# three marks.  The wordmarks are the public-domain text logos; CIQC's is its own header
# image.  `__SITEROOT__` becomes the page's path back to the site root.
FOOTER = """<footer id="sitefoot">
  <p>Led by a collaboration between <a href="https://www.ucla.edu/">UCLA</a> and
  <a href="https://www.berkeley.edu/">UC Berkeley</a>, funded by the
  <a href="https://ciqc.berkeley.edu/">Challenge Institute for Quantum Computation</a> (CIQC),
  an NSF Quantum Leap Challenge Institute, and by NQVL FTL.</p>
  <div class="logos">
    <a href="https://www.ucla.edu/" title="UCLA"><img src="__SITEROOT__static/ucla.svg" alt="UCLA"></a>
    <a href="https://www.berkeley.edu/" title="University of California, Berkeley"><img src="__SITEROOT__static/berkeley.svg" alt="University of California, Berkeley"></a>
    <a href="https://ciqc.berkeley.edu/" title="Challenge Institute for Quantum Computation"><img src="__SITEROOT__static/ciqc.png" alt="Challenge Institute for Quantum Computation"></a>
  </div>
</footer>"""
FOOTER_CSS = """
#sitefoot{background:transparent;border-top:0;padding:8px 24px 34px;text-align:center;color:#56545e;
 font:13.5px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
#sitefoot p{margin:0 auto 16px;max-width:72ch;color:#52514e}
#sitefoot a{color:#0b0b0b;text-decoration:none} #sitefoot p a{border-bottom:1px solid #cfceca} #sitefoot p a:hover{border-bottom-color:#2a78d6}
#sitefoot .logos{display:flex;justify-content:center;align-items:center;gap:44px;flex-wrap:wrap}
#sitefoot .logos img{display:block;height:38px;width:auto} #sitefoot .logos img[alt="UCLA"]{height:30px}
"""
FOOTER_BLOCK = "<style>" + FOOTER_CSS + "</style>" + FOOTER

BOARD_SKIN = """<style>
:root{--surface:#eceae3;--ink:#15141a;--ink2:#56545e;--ink3:#8a8892;--line:#e4e2db;--grid:#efeee9;--accent:#1f5bb5;
 --serif:"Iowan Old Style","Palatino Linotype",Palatino,Charter,"Bitstream Charter","Sitka Text",Cambria,Georgia,serif}
body{background:var(--surface)}
main{background:#fff;border:1px solid var(--line);border-radius:16px;margin:26px auto 44px;padding:34px 40px 44px;
 box-shadow:0 1px 2px rgba(18,16,28,.05),0 12px 34px rgba(18,16,28,.07)}
h1{font-family:var(--serif);font-size:33px;font-weight:600;letter-spacing:-.02em;line-height:1.14;margin:0 0 8px;color:#1a2540}
h2,h3{font-family:var(--serif);color:#1a2540}
.sub{font-family:var(--serif);font-size:17px;line-height:1.6;color:var(--ink2)}
#sitefoot{background:transparent;border-top:0}
</style>"""
STYLE = STYLE + FOOTER_CSS

HASH_JS = """<script>
(function(){
  // The site's deep links into an app page: #learn opens the course, #learn=B2 a lesson,
  // #design is the blank canvas (the default), and #embed (with &step=N) is the page as
  // the examples show it -- head, tools bar, rail and dock folded away, the stage and its
  // transport waiting at the step named; with &play it runs on a loop (the landing).  It
  // is the same page, not a picture of it.  Under the test shim there is
  // no `location`, and nothing here runs.
  if(typeof location === 'undefined' || typeof window === 'undefined' || !window.addEventListener) return;
  var looping = false;
  function embed(play){
    document.body.setAttribute('data-embed', '1');
    // the website's embedded examples run and are watched, never edited
    try { if(window.EDITOR && EDITOR.setViewOnly) EDITOR.setViewOnly(true); } catch(e){}
    try { var rail = document.getElementById('rail'); if(rail && typeof foldPanel === 'function') foldPanel(rail, true); } catch(e){}
    try { var dk = document.getElementById('dock'); if(dk && typeof foldPanel === 'function') foldPanel(dk, true); } catch(e){}
    try { if(typeof relayout === 'function') relayout(); } catch(e){}
    var autoplay = play;
    play = document.getElementById('play'); var reset = document.getElementById('reset'), slider = document.getElementById('slider');
    if(looping || !play || !autoplay) return;
    looping = true;
    setTimeout(function(){ try { if(!play.disabled && play.textContent === 'Play') play.click(); } catch(e){} }, 400);
    // the transport stops on the last frame and says Play again: start over
    setInterval(function(){ try {
      if(play.disabled || !slider) return;
      if(play.textContent === 'Play' && +slider.max > 0 && +slider.value >= +slider.max){ if(reset) reset.click(); play.click(); }
    } catch(e){} }, 700);
  }
  function apply(){
    var h = (location.hash || '').replace(/^#/, ''), m = /^learn(?:=([A-Za-z]\\d+))?$/.exec(h);
    var dk = document.getElementById('dock'), pl = document.getElementById('paneL');
    if(h.split('&').indexOf('embed') >= 0){ embed(h.split('&').indexOf('play') >= 0); return; }
    if(h === 'design'){
      // the canvas, not the course: the course remembers itself across visits, so a lesson
      // left open is folded away here -- the work on the canvas is untouched
      try { if(pl && /\\bon\\b/.test(pl.className) && dk && typeof foldPanel === 'function') foldPanel(dk, true); } catch(e){}
      return;
    }
    if(!m) return;
    try { if(m[1] && window.EDITOR && EDITOR.lessonLoad) EDITOR.lessonLoad(m[1]); } catch(e){}
    try { if(dk && typeof foldPanel === 'function') foldPanel(dk, false);
          if(typeof setPane === 'function') setPane('L'); } catch(e){}
  }
  window.addEventListener('hashchange', apply); apply();
})();
</script>
<style>
body[data-embed="1"] #sitenav,body[data-embed="1"] .head,body[data-embed="1"] #tools,body[data-embed="1"] #bbpill,
body[data-embed="1"] #bbnotes,body[data-embed="1"] #bbtools,body[data-embed="1"] #bbnow{display:none!important}
body[data-embed="1"] main{height:100vh!important}
/* view-only: the transport stays (play, step, reset, fit, speed); the editing tools, the
   display modes, the element rail and the guide go */
body[data-embed="1"] #ebar,body[data-embed="1"] #glide,body[data-embed="1"] #phase,body[data-embed="1"] #mode,
body[data-embed="1"] #stagebar label,body[data-embed="1"] #rail,body[data-embed="1"] #toasts,
body[data-embed="1"] #eCount,body[data-embed="1"] #eProb{display:none!important}
</style>"""

# The reading furniture every document page carries: the contents rail and the code
# blocks.  It is appended to `<body>`, never woven into the words, so a comment pinned to
# a paragraph still finds that paragraph after it runs.
READING_JS = r"""
<style>
#sitetoc{position:fixed;top:58px;width:218px;max-height:calc(100vh - 92px);overflow:auto;z-index:40;
 font:13px/1.4 var(--sans,ui-sans-serif,system-ui,sans-serif);padding-right:6px}
#sitetoc .tc-l{margin:0 0 10px 13px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;color:var(--ink3,#8a8892)}
#sitetoc a{display:block;color:var(--ink2,#56545e);text-decoration:none;padding:5px 10px 5px 13px;
 border-left:2px solid var(--line,#e4e2db);border-radius:0 7px 7px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#sitetoc a:hover{color:var(--ink,#15141a);border-left-color:var(--ink3,#8a8892);background:rgba(21,20,26,.04);text-decoration:none}
#sitetoc a.tc-3{padding-left:26px;font-size:12.2px;color:var(--ink3,#8a8892)}
#sitetoc a.tc-on{color:var(--accent,#1f5bb5);border-left-color:var(--accent,#1f5bb5);background:var(--soft,#eaf0fa);font-weight:600}
#sitetoc::-webkit-scrollbar{width:7px} #sitetoc::-webkit-scrollbar-thumb{background:#d8d5cc;border-radius:4px}
@media (max-width:1159px){#sitetoc{display:none!important}}
@media (min-width:1160px){main>.toc{display:none}}
.tk-k{color:#6d28d9;font-weight:600} .tk-s{color:#0b7a4b} .tk-n{color:#b05a00}
.tk-c{color:#8a8892;font-style:italic} .tk-t{color:#1f5bb5}
</style>
<script>
(function(){
  if(typeof document === 'undefined' || !document.querySelector) return;
  var main = document.querySelector('main');
  if(!main) return;

  // ---- code: one face, and the words that carry the meaning in their own colour -------
  var KW = {
    py: 'from import def class return None True False and or not in is for while if else with as lambda',
    qasm: 'OPENQASM include qreg creg gate opaque barrier measure reset if',
    tsir: 'init cool shuttle gate measure reset swap split merge rotate barrier op idle',
    json: 'true false null'
  };
  function guess(t){
    if(/^\s*OPENQASM|\bqreg\b|\bcreg\b/.test(t)) return 'qasm';
    if(/^\s*[[{]/.test(t)) return 'json';
    if(/^\s*(from|import|def|class)\s/m.test(t)) return 'py';
    if(/^#\d+\s/m.test(t) || /\b(shuttle|init|cool)\b/.test(t)) return 'tsir';
    return '';
  }
  var RX_HASH = /(#[^\n]*|\/\/[^\n]*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(\b\d+(?:\.\d+)?(?:e[-+]?\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g;
  var RX_PLAIN = /(\/\/[^\n]*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(#\d+|\b\d+(?:\.\d+)?(?:e[-+]?\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g;
  function paint(el){
    var text = el.textContent, lang = guess(text), words = ' ' + (KW[lang] || '') + ' ';
    var rx = (lang === 'py' || lang === '') ? RX_HASH : RX_PLAIN;
    rx.lastIndex = 0;
    var frag = document.createDocumentFragment(), i = 0, m;
    while((m = rx.exec(text))){
      if(m.index > i) frag.appendChild(document.createTextNode(text.slice(i, m.index)));
      var cls = m[1] ? 'tk-c' : m[2] ? 'tk-s' : m[3] ? 'tk-n'
              : (m[4] && words.indexOf(' ' + m[4] + ' ') >= 0 ? 'tk-k' : '');
      if(cls){ var sp = document.createElement('span'); sp.className = cls; sp.textContent = m[0]; frag.appendChild(sp); }
      else frag.appendChild(document.createTextNode(m[0]));
      i = m.index + m[0].length;
    }
    if(i < text.length) frag.appendChild(document.createTextNode(text.slice(i)));
    el.textContent = '';
    el.appendChild(frag);
  }
  Array.prototype.forEach.call(main.querySelectorAll('pre'), function(pre){
    var el = (pre.children.length === 1 && pre.firstElementChild.tagName === 'CODE') ? pre.firstElementChild : pre;
    if(el.children.length) return;                    // already marked up by whoever wrote it
    if(el.textContent.length > 20000) return;
    paint(el);
  });

  // ---- the contents rail, built from the page's own headings --------------------------
  var items = [];
  Array.prototype.forEach.call(main.querySelectorAll('h2,h3'), function(h){
    var id = h.id;
    if(!id){ var q = h.parentElement; if(q && q !== main && q.id && q.firstElementChild === h) id = q.id; }
    if(!id) return;
    var c = h.querySelector('code'), t;
    if(c) t = c.textContent || '';
    else { var k = h.cloneNode(true), an = k.querySelector('.anchor'); if(an) an.parentNode.removeChild(an); t = k.textContent || ''; }
    t = t.replace(/\s+/g, ' ').trim();
    if(!t) return;
    if(t.length > 44) t = t.slice(0, 43) + '…';
    items.push({ id: id, t: t, lvl: h.tagName === 'H3' ? 3 : 2 });
  });
  if(items.length < 3) return;                        // not a page anyone needs a map of

  var nav = document.createElement('nav');
  nav.id = 'sitetoc'; nav.setAttribute('aria-label', 'on this page');
  var lbl = document.createElement('p'); lbl.className = 'tc-l'; lbl.textContent = 'On this page';
  nav.appendChild(lbl);
  items.forEach(function(it){
    var a = document.createElement('a');
    a.href = '#' + it.id; a.textContent = it.t; a.title = it.t;
    if(it.lvl === 3) a.className = 'tc-3';
    nav.appendChild(a);
  });
  document.body.appendChild(nav);

  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  nav.addEventListener('click', function(ev){
    var a = ev.target && ev.target.closest ? ev.target.closest('a') : null;
    if(!a || !nav.contains(a)) return;
    var t = document.getElementById(a.getAttribute('href').slice(1));
    if(!t) return;
    ev.preventDefault();
    t.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
    try { history.replaceState(null, '', a.getAttribute('href')); } catch(e){}
  });

  var RAIL = 218, GAP = 26, cardW = 0;
  function measure(){
    main.style.marginLeft = ''; main.style.marginRight = '';
    cardW = main.offsetWidth;
    place();
  }
  function place(){
    var room = window.innerWidth - (cardW + RAIL + GAP);
    if(room < 34 || window.innerWidth < 1160){
      nav.style.display = 'none';
      main.style.marginLeft = ''; main.style.marginRight = '';
      return;
    }
    var left = Math.round(room / 2);
    nav.style.display = '';
    nav.style.left = left + 'px';
    main.style.marginLeft = (left + RAIL + GAP) + 'px';
    main.style.marginRight = '0';
  }
  var on = null;
  function spy(){
    var y = window.scrollY + 120, cur = items[0].id;
    for(var i = 0; i < items.length; i++){
      var e = document.getElementById(items[i].id);
      if(e && e.getBoundingClientRect().top + window.scrollY <= y) cur = items[i].id;
    }
    if(cur === on) return;
    on = cur;
    Array.prototype.forEach.call(nav.getElementsByTagName('a'), function(a){
      var base = a.className.replace(/\s*tc-on/, '');
      a.className = base + (a.getAttribute('href') === '#' + cur ? ' tc-on' : '');
    });
  }
  var tick = false;
  function frame(){ tick = false; spy(); }
  function ask(){ if(tick) return; tick = true; (window.requestAnimationFrame || setTimeout)(frame, 16); }
  window.addEventListener('scroll', ask, { passive: true });
  window.addEventListener('resize', function(){ measure(); ask(); });
  if(window.ResizeObserver){ try { new ResizeObserver(function(){ measure(); }).observe(document.body); } catch(e){} }
  measure();
  spy();
})();
</script>"""


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{style}{extra_css}</style></head><body>
<main>{body}</main>__FOOTER__</body></html>"""


# ------------------------------------------------------------------------- inputs

def tasks() -> list[dict]:
    out = []
    for p in sorted(TASKS.glob("*/task.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        t["dir"] = p.parent
        seed = ROOT / t["seed"]
        rows = (json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
                if (seed / "manifest.json").exists() else [])
        # a design that fails any rule is disqualified: not ranked, not published, not
        # searchable -- the board carries only candidates that obey every rule
        t["disqualified"] = [r for r in rows if r.get("rules_failed")]
        t["rows"] = [r for r in rows if not r.get("rules_failed")]
        t["seed_dir"] = seed
        out.append(t)
    out.sort(key=lambda t: t.get("order", 99))
    return out


def lessons() -> tuple[list[dict], list[dict]]:
    """The course's parts and lessons, read out of `tutorial.js` (pure data).  A tree
    without the course (it ships on `docs/TUTORIAL_PLAN.md`'s schedule) builds a site that
    says so rather than none."""
    if not TUTORIAL_JS.exists():
        return [], []
    src = TUTORIAL_JS.read_text(encoding="utf-8")
    s = lambda x: x.replace("\\'", "'")
    parts = [{"id": a, "title": s(b)} for a, b in re.findall(r"\{ id: '([A-Z])', title: '((?:[^'\\]|\\.)*)'", src)]
    less = [{"id": a, "part": b, "title": s(c)}
            for a, b, c in re.findall(r"\{ id: '([A-Z]\d+)', part: '([A-Z])', title: '((?:[^'\\]|\\.)*)'", src)]
    return parts, less


def _entry_ok(r: dict) -> bool:
    return r.get("status") == "ok" and not r.get("rules_failed")


def _r10(r: dict) -> bool:
    return bool((r.get("numbers") or {}).get("r10") or r.get("r10"))


# ------------------------------------------------------------------------- the bar

def nav_html(depth: int, active: str | None, index_json: str) -> str:
    root = "../" * depth
    return ((HERE / "nav.html").read_text(encoding="utf-8")
            .replace("__ROOT__", root).replace("__ACTIVE__", active or "")
            .replace("__INDEX__", index_json))


def comments_html() -> str:
    """The comment layer (`comments.html`): the Sign-in control in the bar, and for a
    signed-in reader the pins and chat boxes anchored in the page.  It also carries the
    one way an account is made -- a page opened as `?invite=<token>` asks for a name and
    a password and makes it -- and, for the admin, the panel that sends those links.  It
    talks to `/api/` (`comments_api.py` behind nginx) and does nothing on a copy served
    without it."""
    return (HERE / "comments.html").read_text(encoding="utf-8")


def with_nav(page: str, depth: int, active: str | None, index_json: str,
             app: bool = False, extra: str = "") -> str:
    """The bar goes right after `<body>`, the comment layer right after the bar; an app
    page (the studio frame, one viewport high) also gets the 40 px taken off its `main`."""
    bar = (APP_CSS if app else "") + nav_html(depth, active, index_json) + comments_html()
    # the real tag, not the `<body data-explain>` a stylesheet comment in the studio quotes
    i = page.index("<body", page.index("</head>"))
    i = page.index(">", i) + 1
    # a document page also gets the contents rail and the code blocks; an app page is its
    # own thing and gets neither
    tail = extra + ("" if app else READING_JS)
    if tail:
        j = page.rindex("</body>")
        page = page[:j] + tail + page[j:]
    return page[:i] + bar + page[i:]


# ------------------------------------------------------------------------- pages

def _doc_link(target: str) -> str:
    if re.match(r"^(https?:|#|mailto:)", target):
        return target
    path, _, frag = target.partition("#")
    frag = f"#{frag}" if frag else ""
    stem = Path(path).name[:-3] if path.endswith(".md") else None
    if stem in DOC_NAMES:
        return f"../{stem}/{frag}"
    rel = Path(path[3:]).as_posix() if path.startswith("../") else (Path("docs") / path).as_posix()
    return f"{REPO}/blob/main/{rel}{frag}"


def render_docs() -> dict[str, dict]:
    table = hints()
    out = {}
    for name in DOC_NAMES:
        r = Renderer(table, _doc_link)
        # the reference pages are the project's own engineering documents; `prose.strip`
        # takes the repository out of them, since a reader here has no such files
        body = r.render(prose.strip((DOCS / f"{name}.md").read_text(encoding="utf-8"), name))
        title = re.sub(r"<[^>]+>", "", r.headings[0][2]) if r.headings else name
        toc = "".join(f'<li><a href="#{sid}">{text}</a></li>' for lvl, sid, text in r.headings if lvl == 2)
        out[name] = {"title": html.unescape(title), "body": body, "toc": toc, "headings": r.headings}
    return out


def landing(ts: list[dict]) -> str:
    n_all = sum(len(t["rows"]) for t in ts)
    n = sum(1 for t in ts for r in t["rows"] if r.get("status") == "ok" and _t(r) is not None)
    tiles = "".join(
        f'<a class="tile" href="{u}"><b>{label}</b><span>{blurb}</span></a>'
        for key, label, u, blurb in PARTS)
    # the running example: the clip of one seed entry, captioned from that entry's own row
    ex = EXAMPLE
    task = next((t for t in ts if t["id"] == ex["task"]), None)
    row = next((r for r in (task["rows"] if task else []) if ex["device"] in r["key"]), None)
    if row:
        n_ins = row.get("instructions")
        cap = (f'{ex["what"]}: {n_ins} instructions, {_t(row):.1f} ms on the jones table, '
               f'every rule passed{", R10 by the proved Lean checker" if _r10(row) else ""}. '
               f'Step through it &rarr;')
        page = f'board/{task["id"]}/{row["page"]}'
        url = page + "#embed&play&step=2"
    else:
        cap, url, page = ex["what"], "board/", "board/"
    return ((HERE / "landing.html").read_text(encoding="utf-8")
            .replace("__TILES__", tiles).replace("__N__", f"{n_all} designs on {len(ts)} tasks, {n} run their round")
            .replace("__EXAMPLE_PAGE__", page + "#step=2").replace("__EXAMPLE_URL__", url)
            .replace("__EXAMPLE_CAPTION__", cap).replace("__EXAMPLE_ALT__", html.escape(ex["what"]))
            .replace("__FOOTER__", FOOTER_BLOCK))


def learn_page(parts: list[dict], less: list[dict], docs: dict, ts: list[dict]) -> str:
    body = ["<h1>Learn</h1>",
            "<p class=\"sub\">One path: the course inside the design tool, then the reference, "
            "then a real design stepped instruction by instruction. Every lesson opens the studio on "
            "its exercise and checks your work against the page's own verdicts; progress is kept in "
            "this browser by the course itself. <a href=\"../studio.html#learn\">Open the course</a>.</p>",
            "<h2>The course</h2>"]
    if not parts:
        body.append('<p class="note">This build carries no course: the lessons were not in the tree it was '
                    'built from.</p>')
    body.append('<div class="path">')
    for p in parts:
        mine = [L for L in less if L["part"] == p["id"]]
        blurb, art = PART_ART.get(p["id"], ("", ""))
        items = "".join(
            f'<li><span class="id">{L["id"]}</span><a href="../studio.html#learn={L["id"]}">{html.escape(L["title"])}</a>'
            f'<span class="stars" data-lesson="{L["id"]}"></span></li>' for L in mine)
        body.append(f'<div class="pt">{art}<div><div class="kick" style="color:{PART_COLOR.get(p["id"], "#2a78d6")}">Part {p["id"]} &middot; {len(mine)} lessons'
                    f'<span class="prog" data-part="{p["id"]}"></span></div>'
                    f'<h3>{html.escape(p["title"])}</h3><p class="sum">{blurb}</p>'
                    f'<ul class="lessons">{items}</ul></div></div>')
    body.append("</div>")
    body.append("<h2>The reference</h2><div class=\"docs\">")
    body.append('<a class="doc" href="../physics/"><span class="tag">background</span><b>The physics, from one trapped ion to the noise model</b>'
                '<span>Traps, qubits, gates, transport, the control system, where the noise comes from and how this website simulates it.</span></a>')
    body.append('<a class="doc" href="../language/"><span class="tag">the language</span><b>Every statement, with a running example</b>'
                '<span>The syntax, what each statement does to the machine, what it costs, and the IR beneath it.</span></a>')
    body.append('<a class="doc" href="../rules/"><span class="tag">the rules</span><b>All 27 rules, each with a programme that passes and one that fails</b>'
                '<span>The verifier\'s own verdicts, runnable here.</span></a>')
    body.append('<a class="doc" href="../compilation/"><span class="tag">compilation</span><b>From QASM to hardware instructions, verified</b>'
                '<span>The pipeline on a Bell pair, then every basic gate compiled, mapped, witnessed and checked.</span></a>')
    for name in DOC_NAMES:
        body.append(f'<a class="doc" href="../docs/{name}/"><span class="tag">{DOC_TAGS[name]}</span>'
                    f'<b>{html.escape(docs[name]["title"])}</b><span>{DOC_BLURBS[name]}</span></a>')
    body.append("</div>")
    body.append("<h2>A real design</h2><p class=\"sub\">Every leaderboard entry is a worked example: the executing "
                "instruction is marked in the programme, the circuit statement it discharges beside it, and "
                "<code>#step=N</code> links any step. The fastest verified round of each task:</p><ul>")
    for t in ts:
        best = [r for r in t["rows"] if _entry_ok(r) and _r10(r) and _t(r) is not None]
        best.sort(key=_t)
        if best:
            b = best[0]
            body.append(f'<li><a href="../board/{t["id"]}/{b["page"]}#step=1">{html.escape(t["title"])}</a> on '
                        f'<b>{html.escape(short_name(b))}</b> '
                        f'<span style="color:#0b7a4b;font-weight:600">{_t(b):.2f} ms</span> '
                        f'<span style="color:var(--ink3)">&middot; {b.get("instructions")} instructions</span></li>')
    body.append("</ul>")
    body.append("""<script>
(function(){ var p = null; try { p = JSON.parse(localStorage.getItem('qccd.studio.tutorial') || 'null'); } catch(e){}
  var stars = (p && p.stars) || {}, els = document.querySelectorAll('.stars[data-lesson]'), done = {}, all = {};
  for(var i = 0; i < els.length; i++){ var id = els[i].getAttribute('data-lesson'), n = stars[id] || 0, part = id.charAt(0);
    els[i].textContent = n ? new Array(n + 1).join('\\u2605') : ''; all[part] = (all[part] || 0) + 1; if(n) done[part] = (done[part] || 0) + 1; }
  var ps = document.querySelectorAll('.prog[data-part]');
  for(var j = 0; j < ps.length; j++){ var k = ps[j].getAttribute('data-part'); if(done[k]) ps[j].textContent = done[k] + ' of ' + all[k] + ' done'; } })();
</script>""")
    return PAGE.format(title="Learn - QCCD studio", style=STYLE, extra_css="", body="\n".join(body))


def board_index(ts: list[dict]) -> str:
    """One row per task: the ranked designs as bars coloured by family, the fastest verified
    one in green, refused and rule-failing ones called out, and the numbers that matter.

    Each row also carries its QEC clock cycle -- the round it ranks with the classical
    feedback loop composed onto it -- and the boards are compared that way in one section
    at the end (`qec_cycle`)."""
    from . import cowork, qec_cycle
    rows_html, cycles = [], []
    for t in ts:
        rows = t["rows"]
        ok = [r for r in rows if r.get("status") == "ok" and _t(r) is not None]
        ok.sort(key=_t)
        sound = [r for r in ok if _entry_ok(r) and _r10(r)]
        best = sound[0] if sound else None
        refused = [r for r in rows if r not in ok]
        # the board shows the three fastest verified designs of each task; the task page has them all
        shown = (sound or ok)[:3]
        tmax = max((_t(r) for r in shown), default=1.0)
        fmt = (lambda v: f"{v:.2f} ms") if tmax < 100 else (lambda v: f"{v:.0f} ms")
        bars = []
        for i, r in enumerate(shown, 1):
            cls = "bar" + (" best" if r is best else "") + ("" if _entry_ok(r) else " bad")
            name = html.escape(short_name(r))
            title = html.escape(r.get("title", ""))
            note = "" if _entry_ok(r) else " &middot; rules failed"
            bars.append(f'<a class="{cls}" href="{t["id"]}/{r["page"]}" title="{title}">'
                        f'<span class="lbl"><span class="rank r{i}">{i}</span>{name}{note}</span>'
                        f'<span class="tr"><span class="fl" style="width:{100 * _t(r) / tmax:.1f}%;background:{fam_color(r.get("family", ""))}"></span></span>'
                        f'<span class="v">{fmt(_t(r))}</span></a>')
        rest = len(ok) - len(shown)
        ref = (f'<div class="refused">top {len(shown)} of {len(sound)} verified designs &middot; '
               f'<a href="{t["id"]}/">{rest} more on the task page &rarr;</a></div>' if rest > 0 else "")
        n_lean = sum(1 for r in ok if _r10(r))
        n_bad = len(t.get("disqualified") or [])
        stats = (
            f'<div class="stat good"><span>fastest verified</span><b>{fmt(_t(best)) if best else "&ndash;"}</b>'
            f'<small>{html.escape(short_name(best)) if best else "none yet"}</small></div>'
            f'<div class="stat lean"><span>R10 by Lean</span><b>{n_lean}</b><small>of {len(ok)} that run it</small></div>'
            f'<div class="stat"><span>designs</span><b>{len(rows)}</b><small>{len(ok)} run the round</small></div>'
            f'<div class="stat{" warn" if (refused or n_bad) else ""}"><span>refused &middot; disqualified</span>'
            f'<b>{len(refused)} &middot; {n_bad}</b><small>{"by the compiler &middot; by the rules, not listed" if (refused or n_bad) else "none"}</small></div>')
        # the QEC clock cycle of this board: the round it ranks, plus the classical loop
        # around it (qccd/site/qec_cycle.py, from qccd.analysis.feedback)
        cycles.append({"id": t["id"], "title": t["title"],
                       "round_us": (_t(best) or 0.0) * 1000.0, "n_data": t.get("n_data")})
        rows_html.append(
            f'<section class="task" id="{t["id"]}"><div><h2><a href="{t["id"]}/">{html.escape(t["title"])}</a></h2>'
            f'<p class="desc">{html.escape(t.get("description", ""))}</p>'
            f'<p class="more"><a href="{t["id"]}/">Open the ranking plot &rarr;</a></p>'
            f'<p class="meta">{t.get("n_data", "?")} data qubits &middot; the three fastest verified designs, by time on the jones table &middot; click a bar to step the programme</p></div>'
            f'<div><div class="bars">{"".join(bars)}</div>{ref}<div class="stats">{stats}</div>'
            f'{qec_cycle.board_block(_t(best) * 1000.0 if best else None)}</div></section>')
    legend = ('<div class="legend">' +
              "".join(f'<span><i style="background:{c}"></i>{f}</span>' for f, c in FAMHEX.items()) +
              f'<span><i style="background:{GREY}"></i>a loop without docks, a line, rails, or two loops</span>'
              '<span><i style="background:#0b7a4b"></i>fastest verified</span></div>')
    body = ("<h1>Leaderboard</h1><p class=\"sub\">One board per task. A task is a fixed circuit, physics package "
            "and cost table; every design on a board ran one syndrome-extraction round of that circuit, was "
            "replayed against the 27 rules and, where it passed, checked by the proved Lean checker (R10). "
            "A design that fails any rule is disqualified and not listed. Each row shows the three fastest verified designs of its task; open a task for the full ranking of "
            "every listed design, where you can rank by anything and click a dot to step the programme in the studio. "
            "A round is only half of a QEC cycle, so every board also carries "
            "<a href=\"#cycle\">the classical loop</a> around it.</p>" + legend + "".join(rows_html) +
            qec_cycle.board_section(cycles) +
            # the official leaderboard, read live from /official (qccd/site/cowork.py)
            cowork.board_section())
    return PAGE.format(title="Leaderboard - QCCD studio", style=STYLE,
                       extra_css=qec_cycle.CSS, body=body)


CREDIT_CSS = """
.cr{width:100%;border-collapse:collapse;margin:10px 0 0}
.cr th{text-align:left;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink3);font-weight:700;padding:0 10px 8px 0}
.cr td{padding:10px 10px 10px 0;border-top:1px solid var(--grid);vertical-align:middle}
.cr td.n{font-variant-numeric:tabular-nums;text-align:right;padding-right:18px}
.cr .who{display:flex;align-items:center;gap:10px;font-weight:600;color:var(--head)}
.cr .av{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;
 color:#fff;font:600 11px/1 var(--sans);flex:0 0 auto}
.cr .tot{font-size:19px;font-weight:600;color:var(--head)}
.cr .gone{color:var(--ink3);font-weight:400;font-size:12px;margin-left:6px}
.crbar{display:flex;height:7px;border-radius:4px;overflow:hidden;background:var(--grid);min-width:90px}
.crbar i{display:block;height:100%} .crbar .a{background:#0b7a4b} .crbar .o{background:#d59a00}
.crkey{font-family:var(--sans);font-size:12px;color:var(--ink2);margin:14px 0 0}
.crkey i{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 5px 0 14px;vertical-align:0}
.crkey i:first-child{margin-left:0}
.crlist{list-style:none;padding:0;margin:10px 0 0;font-family:var(--sans)}
.crlist li{border-top:1px solid var(--grid);padding:10px 0;display:flex;gap:12px;align-items:flex-start;font-size:13px}
.crlist .b{flex:1 1 auto;min-width:0} .crlist .b p{margin:3px 0 0;color:var(--ink);white-space:pre-wrap;word-wrap:break-word}
.crlist small{color:var(--ink3)} .crlist .tag{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;font-weight:700;
 padding:2px 8px;border-radius:10px;white-space:nowrap;flex:0 0 auto}
.crlist .tag.a{background:#e6f4ec;color:#0b7a4b} .crlist .tag.o{background:#fff3e0;color:#b26a00}
"""


def credit_page() -> str:
    """Who has read the site closely enough to say something about it.

    The numbers come from `/api/credit`, an append-only ledger in the comments database:
    one row per comment, written when it is posted and marked rather than deleted when the
    comment is dealt with.  So addressing a comment clears it off the page it was pinned to
    and never off this one."""
    body = r"""<h1>Credit</h1>
<p class="sub">Who has read this site closely enough to say something about it. A comment counts
once, when it is posted. Addressing one takes it off the page it was pinned to and never off
this page &mdash; the record of having noticed the thing is the part worth keeping.</p>
<div id="crmsg" class="note">Sign in from the bar above to see this.</div>
<div id="crbody" hidden>
  <h2 id="people">Who has commented</h2>
  <table class="cr"><thead><tr><th>Collaborator</th><th class="n">Comments</th>
    <th>Addressed &middot; still open</th><th>Latest</th></tr></thead><tbody id="crpeople"></tbody></table>
  <p class="crkey"><i style="background:#0b7a4b"></i>addressed &mdash; dealt with and taken off the page
    <i style="background:#d59a00"></i>still on the page</p>
  <h2 id="every">Every comment, newest first</h2>
  <ul class="crlist" id="critems"></ul>
</div>
<script>
(function(){
  if(typeof fetch !== 'function') return;
  var PAL = ['#2a78d6','#d64545','#1baf7a','#eb6834','#4a3aa7','#c2308a','#0e9aa7','#8a6d1e','#5b8c1e','#b5471b','#6b4fbb','#2f7f6e'];
  function el(t, c, x){ var e = document.createElement(t); if(c) e.className = c; if(x != null) e.textContent = x; return e; }
  function initials(n){ var w = String(n || '?').trim().split(/\s+/).filter(Boolean);
    return ((w[0] || '?')[0] + (w.length > 1 ? w[w.length - 1][0] : '')).toUpperCase(); }
  function when(t){ if(!t) return ''; var d = new Date(t * 1000);
    try { return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
    catch(e){ return d.toISOString().slice(0, 10); } }
  // ask who we are first: a signed-out reader then gets the message without a refused
  // request, which would otherwise be a console error on an ordinary visit to this page
  fetch('/api/me', { credentials: 'same-origin' }).then(function(r){
    return r.ok ? r.json() : { user: null };
  }).then(function(m){
    if(!m || !m.user) throw new Error('sign-in');
    return fetch('/api/credit', { credentials: 'same-origin' });
  }).then(function(r){
    if(r.status === 401) throw new Error('sign-in');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(d){
    document.getElementById('crmsg').hidden = true;
    document.getElementById('crbody').hidden = false;
    var tb = document.getElementById('crpeople');
    d.people.forEach(function(p){
      var tr = el('tr');
      var td = el('td'), who = el('div', 'who');
      var av = el('span', 'av', initials(p.name)); av.style.background = PAL[(p.color || 0) % PAL.length];
      who.appendChild(av); who.appendChild(el('span', null, p.name));
      if(p.gone) who.appendChild(el('span', 'gone', 'account closed'));
      td.appendChild(who); tr.appendChild(td);
      var n = el('td', 'n'); n.appendChild(el('span', 'tot', String(p.total))); tr.appendChild(n);
      var bt = el('td'), bar = el('div', 'crbar');
      var a = el('i', 'a'), o = el('i', 'o');
      a.style.width = (100 * p.addressed / (p.total || 1)) + '%';
      o.style.width = (100 * p.open / (p.total || 1)) + '%';
      bar.appendChild(a); bar.appendChild(o);
      bar.title = p.addressed + ' addressed, ' + p.open + ' still open';
      bt.appendChild(bar); tr.appendChild(bt);
      tr.appendChild(el('td', null, when(p.last)));
      tb.appendChild(tr);
    });
    var ul = document.getElementById('critems');
    d.items.forEach(function(it){
      var li = el('li');
      var tag = el('span', 'tag ' + (it.removed ? 'a' : 'o'), it.removed ? 'addressed' : 'open');
      var b = el('div', 'b');
      b.appendChild(el('small', null, it.name + ' \u00b7 ' + when(it.created) + ' \u00b7 ' + it.page));
      b.appendChild(el('p', null, it.text));
      li.appendChild(tag); li.appendChild(b);
      ul.appendChild(li);
    });
    if(!d.people.length){
      document.getElementById('crbody').hidden = true;
      var m = document.getElementById('crmsg');
      m.hidden = false; m.textContent = 'Nobody has commented yet.';
    }
  }).catch(function(e){
    var m = document.getElementById('crmsg');
    m.textContent = (e && e.message === 'sign-in')
      ? 'Sign in from the bar above to see this.'
      : 'The credit list could not be loaded: ' + (e && e.message);
  });
})();
</script>"""
    return PAGE.format(title="Credit - QCCD studio", style=STYLE, extra_css=CREDIT_CSS, body=body)


def discuss_page() -> str:
    cfg = json.loads((HERE / "giscus.json").read_text(encoding="utf-8"))
    secs = []
    for cat, meta in cfg["categories"].items():
        blurb = meta.get("blurb", "")
        if meta.get("id"):
            widget = (f'<script src="https://giscus.app/client.js" data-repo="{cfg["repo"]}" '
                      f'data-repo-id="{cfg["repo_id"]}" data-category="{cat}" data-category-id="{meta["id"]}" '
                      f'data-mapping="specific" data-term="{cat.lower()}" data-strict="0" data-reactions-enabled="1" '
                      f'data-emit-metadata="0" data-input-position="top" data-theme="light" data-lang="en" '
                      f'crossorigin="anonymous" async></script>')
        else:
            widget = (f'<p class="note">This category is not wired yet: the repository owner enables Discussions on '
                      f'<a href="https://github.com/{cfg["repo"]}/settings">the repository</a>, installs the '
                      f'<a href="https://github.com/apps/giscus">giscus app</a>, and pastes the category id from '
                      f'<a href="https://giscus.app">giscus.app</a> into the site\'s giscus configuration. '
                      f'Until then, <a href="https://github.com/{cfg["repo"]}/discussions">discuss on GitHub</a>.</p>')
        secs.append(f'<h2 id="{cat.lower()}">{cat}</h2><p class="sub">{blurb}</p>{widget}')
    body = ("<h1>Discuss</h1><p class=\"sub\">GitHub Discussions on the repository, embedded here with giscus. "
            "A rule change is a pull request touching the rule's doc, its Python and browser twins and the parity "
            "test together; the discussion happens here, the decision is a merge.</p>"
            "<p class=\"note\">To point at one spot instead: <b>sign in</b> from the bar, press <b>+ Comment</b>, and "
            "click the word, figure or control you mean. The note stays pinned there for every signed-in reader, who "
            "can reply under it. Only signed-in readers see comments, and accounts are by invitation: ask the site "
            "admin for a link, follow it once, and the account is yours.</p>" + "".join(secs))
    return PAGE.format(title="Discuss - QCCD studio", style=STYLE, extra_css="", body=body)


def _verdict(v: dict, focus: str | None, expect: str | None, tag: str | None) -> tuple[str, str, str]:
    """(card class, tag text, verdict html) for one example."""
    f = v.get("focus")
    parts = []
    if f:
        st = f["state"]
        cls = {"failed": "fail", "passed": "pass", "skipped": "skip", "partial": "partial"}.get(st, "any")
        parts.append(f'<b class="{ {"fail": "bad", "pass": "ok", "skip": "skip", "partial": "partial"}.get(cls, "ok") }">{focus} {st}</b>')
        if f["why"]:
            parts.append(f'<span class="m">{html.escape(f["why"])}</span>')
        for msg in f["messages"][:2]:
            parts.append(f'<span class="m">{html.escape(msg)}</span>')
        if len(f["messages"]) > 2:
            parts.append(f'<span class="m">&hellip; and {len(f["messages"]) - 2} more sentences like these</span>')
        others = [r for r in v["failed"] if r != focus]
        if others:
            parts.append(f'<span class="m">also fails {", ".join(others)}</span>')
    else:
        cls = "pass" if not v["failed"] else "fail"
        parts.append('<b class="ok">no rule fails</b>' if not v["failed"] else f'<b class="bad">fails {", ".join(v["failed"])}</b>')
        for msg in v["messages"][:3]:
            parts.append(f'<span class="m">{html.escape(msg["message"])}</span>')
    nums = f'cost {v["cost"]:g} &middot; steps {v["steps"]} &middot; {v["us"]:.0f} &micro;s &middot; peak n&#772; {v["peak_quanta"]:.2f}'
    if focus == "R16":
        nums += f' &middot; gate error {v["gate_error"]:.3g}'
    if focus == "R17" and v.get("anomalous"):
        nums += " &middot; anomalous n&#772; " + ", ".join(f"{k} {x}" for k, x in v["anomalous"].items())
    parts.append(f'<span class="nums">{nums}</span>')
    if expect == "any":
        cls = "any"
    tag_text = tag or {"pass": "passes", "fail": "fails", "skip": "skipped", "partial": "partial", "any": "compare"}[cls]
    return cls, tag_text, "".join(parts)


def _example_card(ex: dict, focus: str | None, root: str, device: str | None = None) -> str:
    cls, tag, verdict = _verdict(ex["verdict"], focus, ex.get("expect"), ex.get("tag"))
    dev = f'<p class="src">device: {html.escape(device)}</p>' if device else ""
    return (f'<div class="ex {cls}"><span class="tag">{tag}</span>'
            f'<p class="why">{ex["why"]}</p>{dev}'
            f'<pre{" class=\"ir\"" if ex.get("ir") else ""}><code>{html.escape(ex["text"])}</code></pre>'
            f'<div class="verdict">{verdict}</div>'
            f'<div class="runbox"><iframe class="live" loading="lazy" src="{ex["page"]}#embed&amp;step=1" title="the example, on its page"></iframe></div>'
            f'<a class="open" href="{ex["page"]}#step=1">open the page, with its Report pane</a></div>')


def language_page(built: dict) -> str:
    body = ["<h1>The hardware language</h1>",
            "<p class=\"sub\">A programme is a list of statements over one device. Each statement is one instruction "
            "and one machine cycle: it says what moves where, or which ions a gate, a measurement or a cooling "
            "touches. It says nothing about cost. The replay computes the cost, the duration and the heating of "
            "every cycle from the device's own primitive tables, and the rules judge each cycle as a whole. "
            "The same text runs in the studio's Write pane and, unchanged, in Python through <code>qccd.api</code>.</p>",
            "<h2 id=\"syntax\">Syntax</h2>",
            f"<pre class=\"grammar\"><code>{html.escape(GRAMMAR)}</code></pre>",
            "<p class=\"sub\">Statements are Python calls on a programme <code>p</code>; the literals are Python's. "
            "A statement may span lines while a bracket is open, which is Python's own rule and the only "
            "line-joining rule there is. Positional arguments come first, keywords after.</p>",
            "<h2 id=\"semantics\">Semantics</h2>",
            "<p class=\"sub\">The machine state is a map from ions to sites, a motional excitation n&#772; per ion, "
            "and a clock. <code>init</code> and <code>fill</code> create the map; every other statement is a "
            "transition of it. Transport statements move ions along segments and through junctions; each "
            "crossing is priced from the device's curves and adds to n&#772;. Gates leave positions alone and "
            "read n&#772; to evaluate their error. Cooling lowers n&#772;. Instructions execute in order, each "
            "atomically; the next starts when the previous ends. Every cycle is then judged by the "
            "<a href=\"../rules/\">rules</a>, which is where a programme is refused.</p>",
            "<h2 id=\"statements\">Statements</h2>",
            "<ul class=\"toc2\">" + "".join(f'<li><a href="#{v["verb"]}"><code>p.{v["verb"]}</code></a></li>' for v in VERBS) + "</ul>"]
    for v in VERBS:
        ex = built[v["verb"]]
        rules = ", ".join(f'<a href="../rules/#{r}">{r}</a>' for r in v["rules"]) or "&ndash;"
        body.append(
            f'<section class="verb" id="{v["verb"]}"><h3><code>{html.escape(v["sig"])}</code></h3>'
            f'<div class="two"><div><p>{v["what"]}</p>'
            f'<dl class="sem"><dt>state</dt><dd>{v["state"]}</dd><dt>cost</dt><dd>{v["cost"]}</dd>'
            f'<dt>rules</dt><dd>{rules}</dd><dt>IR</dt><dd><code>{v["ir"]}</code></dd></dl></div>'
            f'{_example_card(ex, None, "")}</div></section>')
    body.append("<h2 id=\"ir\">Beneath the text: the IR</h2>"
                "<p class=\"sub\">Every statement becomes one instruction of the control IR, the JSON the compiler "
                "emits and the verifier replays. The Write pane and the compiler meet there.</p>"
                "<div class=\"tw\"><table><thead><tr><th>statement</th><th>instruction type</th><th>carries</th></tr></thead><tbody>"
                + "".join(f"<tr><td><code>{a}</code></td><td><code>{b}</code></td><td>{c}</td></tr>" for a, b, c in IR_TABLE)
                + "</tbody></table></div>"
                "<p class=\"sub\">The IR reference is <a href=\"../docs/tsir/\">the control IR</a>; the device language "
                "the programmes run on is <a href=\"../docs/adl/\">the architecture description</a>.</p>")
    return PAGE.format(title="Language - QCCD studio", style=STYLE, extra_css="main{max-width:1120px}",
                       body="\n".join(body))


def _sources(src: str) -> str:
    out = []
    for tok in [t.strip() for t in src.split(",") if t.strip()]:
        if re.match(r"^\d{4}\.\d{4,5}$", tok):
            out.append(f'<a href="https://arxiv.org/abs/{tok}">arXiv:{tok}</a>')
        elif tok.startswith("quant-ph/"):
            out.append(f'<a href="https://arxiv.org/abs/{tok}">arXiv:{tok}</a>')
        elif tok == "deck_v3":
            out.append("the ion-transport deck (v3)")
        elif tok == "local":
            out.append("this platform")
        else:
            out.append(html.escape(tok))
    return ", ".join(out)


def rules_page(built: dict) -> str:
    body = ["<h1>The rules</h1>",
            "<p class=\"sub\">Twenty-seven rules a programme must obey on a QCCD machine, each traced to a source. "
            "The verifier replays every cycle and reports each rule as one of four things; a green tick is "
            "only ever printed for a check that ran. Each rule below has a programme that passes it and one "
            "that fails it, both judged by the real verifier and both runnable here as the page the studio "
            "would open on them. The rules are code: <a href=\"../docs/rules/\">the rules reference</a> is the prose, "
            "the verifier holds the checks, the browser carries their twins, and a "
            "parity test holds the two implementations to identical verdicts.</p>",
            '<div class="contract"><div class="ok"><b>passed</b>the check ran and found nothing</div>'
            '<div class="bad"><b>failed</b>the check ran and names the cycle and the reason</div>'
            '<div class="skip"><b>skipped</b>the check could not run, and says why</div>'
            '<div class="partial"><b>partial</b>the check ran on an approximation, and says which</div></div>',
            "<h2 id=\"all\">All rules</h2>",
            "<ul class=\"toc2\">" + "".join(f'<li><a href="#{r["id"]}">{r["id"]}</a></li>' for r in RULES) + "</ul>"]
    for R in RULES:
        meta = rule_meta(R["id"])
        b = built[R["id"]]
        own = any(isinstance(R.get(w), dict) and R[w].get("device") for w in ("pass", "fail"))
        cards = "".join(_example_card(b[w], R["id"], "", b[w].get("device") if own else None)
                        for w in ("pass", "fail") if w in b)
        note = f'<p class="note">{R["note"]}</p>' if R.get("note") else ""
        body.append(
            f'<section class="rule" id="{R["id"]}"><h3>{R["id"]} <span class="st">{html.escape(meta["statement"])}</span></h3>'
            f'<p class="checks">{R["checks"]}</p>'
            f'<p class="src">device: {R["device"]["about"]} &middot; cost model: {b.get("model", "corrected")} &middot; sources: {_sources(meta["sources"])}</p>'
            f'{note}<div class="pair">{cards}</div></section>')
    return PAGE.format(title="Rules - QCCD studio", style=STYLE, extra_css="main{max-width:1120px}",
                       body="\n".join(body))


def build_examples(out: Path, put) -> tuple[dict, dict]:
    """Render every example page under language/ex and rules/ex, judge it, and return what
    the two pages show."""
    lang: dict[str, dict] = {}
    for v in VERBS:
        m = machine(v["device"])
        rel = f"language/ex/{v['verb']}.html"
        r = build_example({"program": v["example"]}, m, "corrected", out / rel, kicker="THE LANGUAGE",
                          headline=f'p.{v["verb"]}', lede=v["what"], check_metrics=v.get("check_metrics", False))
        put(rel, (out / rel).read_text(encoding="utf-8"), 2, "language", app=True, extra=HASH_JS)
        lang[v["verb"]] = {**r, "page": f"ex/{v['verb']}.html"}
    rules: dict[str, dict] = {}
    for R in RULES:
        entry: dict = {"model": R.get("model", "corrected")}
        for which in ("pass", "fail"):
            if which not in R:
                continue
            ex = R[which]
            m = machine(ex.get("device") or R["device"])
            model = ex.get("model") or R.get("model", "corrected")
            rel = f"rules/ex/{R['id']}_{which}.html"
            r = build_example(ex, m, model, out / rel, kicker=f"RULE {R['id']}",
                              headline=f"{R['id']}: {'passes' if which == 'pass' else 'fails'}" if not ex.get("expect") else f"{R['id']}: {ex.get('tag') or ex['expect']}",
                              lede=R["checks"], focus=R["id"], check_metrics=R.get("check_metrics", False))
            put(rel, (out / rel).read_text(encoding="utf-8"), 2, "rules", app=True, extra=HASH_JS)
            entry[which] = {**r, "page": f"ex/{R['id']}_{which}.html", "model": model,
                            "device": (ex.get("device") or R["device"])["about"]}
        rules[R["id"]] = entry
    return lang, rules


# ---------------------------------------------------------------- compilation

QASM_GATES = [("single-qubit", "id x y z h s sdg t tdg sx sxdg rx ry rz u1 p u2 u3 u",
               "a turn about z, then one laser pulse"),
              ("two-qubit", "cx cz cy ch swap cu1 cp", "cx is the primitive: R, MS(&pi;/2), R, R, R; the others are cx with single-qubit gates around it"),
              ("three-qubit", "ccx", "the standard six-CNOT decomposition"),
              ("non-unitary", "measure reset barrier", "readout and preparation in a zone with SPAM; barrier orders, and costs nothing")]


def _listing(prog: dict, cert: dict) -> str:
    """The hardware programme as one line per instruction, with the circuit op it serves."""
    ops = {op["i"]: op for op in cert.get("circuit_ops", [])}
    lines = []
    for ins in prog["instructions"]:
        t = ins["type"]
        if t == "init":
            body = "init " + ", ".join(f'{k}@{v}' for k, v in (ins.get("placement") or {}).items())
        elif t == "simd":
            ps = ins.get("participants") or []
            body = f'{ins.get("class", "simd")} ' + ", ".join(f'{q["ion"]} {q["from"]}→{q["to"]}' for q in ps)
            if ins.get("template"):
                body = f'{ins.get("class", "simd")} rotate {ins["template"].get("loop")} by {ins["template"].get("delta")}'
        elif t == "gate":
            who = ",".join(ins.get("ions") or []) or ",".join(",".join(pr) for pr in (ins.get("pairs") or []))
            body = f'gate {ins.get("gate")} {who} @{",".join(ins.get("sites") or [])}'
        elif t == "cool":
            body = "cool " + ("all" if ins.get("broadcast") else ",".join(ins.get("ions") or []))
        else:
            body = f'{t} ' + ",".join(ins.get("ions") or [])
        op = (ins.get("meta") or {}).get("op")
        tail = ""
        if op:
            names = [f'{i} {ops[i]["name"]}' if i in ops else str(i) for i in op]
            tail = f'   ← op {", ".join(names)}'
        lines.append(f'#{ins["id"]:<3} {body}{tail}')
    return "\n".join(lines)


def _positions(cert: dict) -> dict:
    pos = dict(cert.get("init") or {})
    for mv in cert.get("moves") or []:
        pos[mv["ion"]] = mv["to"]
    return pos


GATE_LABELS = {"h": "H", "x": "X", "y": "Y", "z": "Z", "s": "S", "sdg": "S†", "t": "T", "tdg": "T†", "id": "I",
               "sx": "√X", "sxdg": "√X†", "rx": "Rx", "ry": "Ry", "rz": "Rz", "u1": "U1", "p": "P", "u2": "U2", "u3": "U3", "u": "U"}


def circuit_svg(ops: list[dict], n: int) -> str:
    """The circuit as the textbook draws it: one wire per qubit, gates as boxes, cx as a
    dot and a crossed circle, measurement as a meter.  Built from the certificate's
    circuit_ops, which are the parsed input."""
    cols = [0] * n
    placed = []
    for op in ops:
        qs = list(op.get("qubits") or [])
        if op["name"] == "barrier" and not qs:
            qs = list(range(n))
        if not qs:
            continue
        lo, hi = min(qs), max(qs)
        col = max(cols[lo:hi + 1])
        placed.append((col, op, qs))
        for q in range(lo, hi + 1):
            cols[q] = col + 1
    ncol = max(cols) if placed else 1
    W, H = 60 + 58 * ncol + 24, 44 * n + 14
    y = lambda q: 26 + 44 * q
    x = lambda c: 74 + 58 * c
    out = [f'<svg class="qc" viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" font-family="ui-sans-serif,system-ui,sans-serif">']
    for q in range(n):
        out.append(f'<text x="10" y="{y(q) + 4}" font-size="12" fill="#52514e">q{q}</text>'
                   f'<line x1="36" y1="{y(q)}" x2="{W - 12}" y2="{y(q)}" stroke="#1c2a4a" stroke-width="1.5"/>')
    ink = "#1c2a4a"
    for col, op, qs in placed:
        cx_, name = x(col), op["name"]
        params = op.get("params") or []
        if name == "cx" and len(qs) == 2:
            c, t = qs
            out.append(f'<line x1="{cx_}" y1="{y(c)}" x2="{cx_}" y2="{y(t)}" stroke="{ink}" stroke-width="1.5"/>'
                       f'<circle cx="{cx_}" cy="{y(c)}" r="5" fill="{ink}"/>'
                       f'<circle cx="{cx_}" cy="{y(t)}" r="10" fill="#fff" stroke="{ink}" stroke-width="1.5"/>'
                       f'<line x1="{cx_ - 10}" y1="{y(t)}" x2="{cx_ + 10}" y2="{y(t)}" stroke="{ink}" stroke-width="1.5"/>'
                       f'<line x1="{cx_}" y1="{y(t) - 10}" x2="{cx_}" y2="{y(t) + 10}" stroke="{ink}" stroke-width="1.5"/>')
        elif name == "cz" and len(qs) == 2:
            c, t = qs
            out.append(f'<line x1="{cx_}" y1="{y(c)}" x2="{cx_}" y2="{y(t)}" stroke="{ink}" stroke-width="1.5"/>'
                       f'<circle cx="{cx_}" cy="{y(c)}" r="5" fill="{ink}"/><circle cx="{cx_}" cy="{y(t)}" r="5" fill="{ink}"/>')
        elif name == "swap" and len(qs) == 2:
            a, b = qs
            out.append(f'<line x1="{cx_}" y1="{y(a)}" x2="{cx_}" y2="{y(b)}" stroke="{ink}" stroke-width="1.5"/>')
            for q in (a, b):
                out.append(f'<path d="M{cx_ - 7} {y(q) - 7}l14 14M{cx_ + 7} {y(q) - 7}l-14 14" stroke="{ink}" stroke-width="1.8" fill="none"/>')
        elif name == "barrier":
            out.append(f'<line x1="{cx_}" y1="{y(min(qs)) - 16}" x2="{cx_}" y2="{y(max(qs)) + 16}" stroke="#8a8985" stroke-width="1.5" stroke-dasharray="4 3"/>')
        elif name == "measure":
            q = qs[0]
            out.append(f'<rect x="{cx_ - 19}" y="{y(q) - 14}" width="38" height="28" rx="3" fill="#fff" stroke="{ink}" stroke-width="1.5"/>'
                       f'<path d="M{cx_ - 11} {y(q) + 7}a11 11 0 0 1 22 0" fill="none" stroke="{ink}" stroke-width="1.5"/>'
                       f'<line x1="{cx_}" y1="{y(q) + 7}" x2="{cx_ + 8}" y2="{y(q) - 6}" stroke="{ink}" stroke-width="1.5"/>')
        elif name == "reset":
            q = qs[0]
            out.append(f'<rect x="{cx_ - 19}" y="{y(q) - 14}" width="38" height="28" rx="3" fill="#fff" stroke="{ink}" stroke-width="1.5"/>'
                       f'<text x="{cx_}" y="{y(q) + 4}" font-size="12" text-anchor="middle" fill="{ink}">|0⟩</text>')
        else:
            label = GATE_LABELS.get(name, name.upper())
            if params:
                label += "(" + ",".join(f"{float(v):.2f}".rstrip("0").rstrip(".") for v in params) + ")"
            w = max(38, 8 + 7 * len(label))
            fs = 12 if len(label) <= 5 else 10.5
            for q in qs:
                out.append(f'<rect x="{cx_ - w / 2}" y="{y(q) - 14}" width="{w}" height="28" rx="3" fill="#fff" stroke="{ink}" stroke-width="1.5"/>'
                           f'<text x="{cx_}" y="{y(q) + 4}" font-size="{fs}" text-anchor="middle" fill="{ink}" font-weight="600">{html.escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def _gate_row(g: dict, page: str) -> str:
    v, r, c, m = g["verdict"], g["rules"], g["cert"], g["meta"]
    final = _positions(c)
    mapping = "".join(f'<tr><td>q{q}</td><td>{ion}</td><td>{c["init"].get(ion, "?")}</td><td>{final.get(ion, "?")}</td></tr>'
                      for q, ion in sorted(c.get("map", {}).items(), key=lambda kv: int(kv[0])))
    pulses = "".join(f'<code>op {w["dag"]} at {w["site"]}: {html.escape(", ".join(w["pulses"]))}</code>' for w in c.get("gates") or [])
    if not pulses:
        pulses = '<span style="color:var(--ink3)">no pulses: nothing for a tableau to compose, and no witness needed</span>'
    n_pass, failed = len(r.get("passed", [])), r.get("failed", [])
    badges = [f'<span class="badge {"ok" if not failed else "bad"}">{n_pass} rules passed{(", " + ", ".join(failed) + " failed") if failed else ""}</span>',
              f'<span class="badge {"ok" if v.get("R10") == "passed" else "bad"}">R10 {v.get("R10", "?")}</span>',
              f'<span class="badge lean">O1 Lean: {v.get("lean", "?")}</span>',
              f'<span class="badge {"ok" if str(v.get("o2_semantics", "")).startswith("ok") else "bad"}">O2: {html.escape(str(v.get("o2_semantics", "?")))}</span>']
    body = g["qasm"].split("creg c[2];\n", 1)[-1].strip()
    dev = m.get("device", "ring6d")
    return (f'<section class="gaterow" id="{g["id"]}"><h3><code>{html.escape(body.splitlines()[0])}</code> <span class="st">{m["note"]}</span></h3>'
            f'<p class="src">device: <b>{dev}</b>, {html.escape(m.get("device_about", ""))}</p>'
            f'<div class="gr"><div>'
            f'<div class="lab">the circuit</div>{circuit_svg(c.get("circuit_ops", []), c.get("n_qubits", 2))}'
            f'<div class="lab">input</div><pre><code>{html.escape(body)}</code></pre>'
            f'<div class="lab">ion mapping</div><table class="kv"><tr><td>qubit</td><td>ion</td><td>starts</td><td>ends</td></tr>{mapping}</table>'
            f'<div class="lab">pulses witnessed</div><div class="pulses">{pulses}</div>'
            f'<div class="lab">verdict &middot; {html.escape(str(v.get("method", "")))}</div><div class="badges">{"".join(badges)}</div>'
            f'<a class="open" href="{page}#step=1">open the page, with the circuit beside the programme</a></div>'
            f'<div><div class="lab">the machine: press Play, or step it</div>'
            f'<div class="runbox tall"><iframe class="live" loading="lazy" src="{page}#embed&amp;step=1" title="{html.escape(m["title"])}, compiled, on its page"></iframe></div>'
            f'<div class="lab">hardware programme ({len(g["tsir"]["instructions"])} instructions, cooled)</div><pre><code>{html.escape(_listing(g["tsir"], c))}</code></pre>'
            f'</div></div></section>')


def compilation_page(built: list[dict]) -> str:
    by = {g["id"]: g for g in built}
    bell = by.get("bell")
    body = ["<h1>Compilation</h1>",
            "<p class=\"sub\">You write a quantum circuit. This page shows what the machine actually has to "
            "do to run it: which ion is which qubit, which ions have to be moved next to each other, and which laser "
            "pulses fire in what order. Every example here can be played.</p>"
            "<p>Everything here is checked by machine: that the plan obeys what the "
            "<a href=\"../rules/\">hardware can do</a>, and that it still computes the circuit you wrote.</p>"]
    # the input
    body.append("<h2 id=\"input\">What goes in</h2>"
                "<p class=\"sub\">A circuit, written the ordinary way. No trapped-ion machine can perform those "
                "gates as written, so each one is rewritten into the only three things this machine can actually "
                "do:</p>"
                "<dl class=\"native\">"
                "<dt>A pulse on one ion</dt><dd>The laser turns that one qubit. Two numbers say how far it turns "
                "and about which axis. Almost everything is built out of these.</dd>"
                "<dt>A turn about z</dt><dd>A turn of the qubit about the other axis. On some machines this is free "
                "&mdash; you leave the ion alone and adjust the pulses that come afterwards &mdash; but not on this "
                "one: the laser beams are shared between ions, so there is no way to change what just one ion sees. "
                "Here it costs real pulses, three of them, unless the trap has a dedicated beam for the job.</dd>"
                "<dt>A pulse across two ions</dt><dd>One laser reaching <i>two</i> ions that sit in the same trap. "
                "It is the only way this machine can make two qubits interact &mdash; which is why ions have to be "
                "moved next to each other before a two-qubit gate can happen at all.</dd>"
                "</dl>"
                "<p>Every single-qubit gate is a turn of the qubit, and three angles are enough to describe any turn "
                "&mdash; how far, and about which axis. So each one becomes a turn about z followed by a laser pulse. "
                "The rewrite is not done by hand: it is a proved identity, so the gate you wrote and the pulses that "
                "fire are the same operation.</p>"
                "<div class=\"tw\"><table><thead><tr><th>what you write</th><th>which gates</th>"
                "<th>what the machine actually does</th></tr></thead><tbody>"
                + "".join(f'<tr><td>{a}</td><td><details class="gset"><summary>{len(b.split())} gates</summary>'
                          f'<code>{b}</code></details></td><td>{c}</td></tr>' for a, b, c in QASM_GATES)
                + "</tbody></table></div>")
    if bell:
        c = bell["cert"]
        body.append(f'<div class="two"><pre><code>{html.escape(bell["qasm"].strip())}</code></pre>'
                    f'<div><div class="lab">the same circuit, drawn</div>{circuit_svg(c.get("circuit_ops", []), c.get("n_qubits", 2))}</div></div>')
    # the pipeline
    stages = [
        ("Parse", "circuit_ops, a DAG", "the QASM becomes a list of operations with their qubits, parameters and source lines, and the per-qubit order between them; a second front end in Python agrees on 507 of 507 test circuits."),
        ("Rewrite", "pulses per gate", "every gate is rewritten as pulses this machine can fire: a single-qubit gate becomes a turn about z and one pulse, a CNOT becomes four single-ion pulses around one two-ion pulse, and anything bigger unfolds into those."),
        ("Place", "map, init", "qubits are bound to ions (<code>map</code>) and ions to sites (<code>init</code>): the ion mapping. Candidates from a greedy and a spectral placement are scored by weighted interaction distance and the better one kept."),
        ("Route and schedule", "moves, layers", "ops are scheduled in DAG layers; every two-qubit gate's operands are carried to one gate-capable trap. The general router moves one ion at a time along hops the device admits; on rings past about half occupancy the rigid-rotation pass turns the whole loop instead. Every move is recorded."),
        ("Emit", "prog.tsir.json + prog.qcert.json", "the hardware programme in the language, every instruction stamped with the circuit op it serves (<code>meta.op</code>), and the certificate: the mapping, the moves, one witness per gate with its site, ions and pulses."),
        ("Cool", "prog.cooled.tsir.json", "the cooling pass replays the programme under the heating model and inserts cooling where a gate would otherwise fire hot (R7)."),
        ("Verify the rules", "rules.json", "the same verifier the studio runs replays the cooled programme and reports the 26 structural rules; R10 is what remains."),
        ("Verify R10", "verdict.json", "<b>O1</b>: the certificate's moves are replayed from <code>init</code>; every gate must find its operands together in a trap that can gate, every hop must be one the device admits, every op witnessed exactly once and in order. The Lean checker <code>QCCDC.Cert.check</code> decides this, and <code>check_sound</code> proves that an accepted input implements the circuit. The device facts it judges against are re-derived from the architecture by code the compiler never runs. <b>O2</b>: the pulses are read out of the emitted programme, composed through the mapping into a stabilizer tableau and compared with the circuit's; outside the Clifford fragment an exact unitary is compared instead. A swapped operand, a dropped gate, a wrong angle or a mis-tracked frame all move the tableau."),
        ("Draw", "the page", "the studio joins the programme and the circuit through the stamps, but only after checking every witness against the stamp on the instruction it names; a disagreement refuses to draw."),
    ]
    body.append("<h2 id=\"pipeline\">How a circuit becomes a machine programme</h2>"
                "<p class=\"sub\">In short: read the circuit, rewrite every gate as laser pulses, decide which ion "
                "plays which qubit, move ions so that the pairs that must interact end up in the same trap, write out "
                "the instruction list, add cooling where the ions would be too hot to gate &mdash; then check the "
                "result, twice. The nine steps below are that in full.</p>"
                "<ol class=\"stages\">" + "".join(
        f'<li><div><b>{t}</b><span class="art">{a}</span></div><div class="out">{d}</div></li>' for t, a, d in stages) + "</ol>")
    if bell:
        c, v = bell["cert"], bell["verdict"]
        final = _positions(c)
        body.append("<h2 id=\"mapping\">The ion mapping, verified</h2>"
                    "<p class=\"sub\">For the Bell pair, compiled here onto the four-site register, each qubit is given an ion and "
                    "a starting trap. The CNOT needs both ions in one trap, and that happens in two steps: one ion is "
                    "carried along the register until its trap sits right beside the other &mdash; the ion waiting there "
                    "does not move &mdash; and then the two traps are merged into one. The waiting ion shifts by a couple "
                    "of micrometres as the merge completes, against the hundreds it would move if it were the one being "
                    "carried. The check recomputes every position "
                    "from <code>init</code> and the move list and checks each gate's operands are where the witness says.</p>"
                    "<div class=\"two\"><div><table class=\"kv\"><tr><td>qubit</td><td>ion</td><td>starts</td><td>ends</td></tr>"
                    + "".join(f'<tr><td>q{q}</td><td>{ion}</td><td>{c["init"].get(ion)}</td><td>{final.get(ion)}</td></tr>' for q, ion in sorted(c["map"].items(), key=lambda kv: int(kv[0])))
                    + "</table><p class=\"sub\" style=\"margin-top:8px\">moves recorded: " + ", ".join(f'{m["ion"]} {m["from"]}→{m["to"]}' for m in c["moves"]) + "</p></div>"
                    f'<div><pre><code>{html.escape(_listing(bell["tsir"], c))}</code></pre></div></div>')
        body.append("<h2 id=\"pulses\">The pulse sequence, matched to the gate</h2>"
                    "<p class=\"sub\">Each gate witness names the instruction that completes the op, the site, the ions and the "
                    "pulses in time order. Two checks meet here: the lowering is a theorem (these pulses equal this gate, up "
                    "to global phase), and O2 composes the pulses actually emitted back into the circuit's semantics, so a "
                    "pulse the compiler emitted but did not witness, or witnessed but did not emit, is caught.</p><div class=\"pulses\">"
                    + "".join(f'<code>op {w["dag"]} ({c["circuit_ops"][w["dag"]]["name"]}) at {w["site"]}, instruction #{w["instr"]}: {html.escape(", ".join(w["pulses"]))}</code>' for w in c["gates"])
                    + f'</div><p class="sub">Verdict for the Bell pair: rules {len(bell["rules"].get("passed", []))} passed; R10 <b style="color:#0b7a4b">{v.get("R10")}</b> &mdash; {html.escape(str(v.get("R10_reason", "")))}.</p>')
        body.append(f'<div class="ex pass"><span class="tag">runs</span><p class="why">The compiled Bell pair on the four-site register: press Play, or step it. The full page shows the circuit stepping beside the programme.</p>'
                    f'<div class="runbox tall"><iframe class="live" loading="lazy" src="ex/bell.html#embed&amp;step=1" title="the Bell pair, on its page"></iframe></div>'
                    f'<a class="open" href="ex/bell.html#step=1">open the page</a></div>')
    # every basic gate
    body.append("<h2 id=\"gates\">Every basic gate, compiled and verified</h2>"
                "<p class=\"sub\">One circuit per gate, each on one of four small devices named on its card, through the same pipeline: the input, the hardware "
                "programme it became, the ion mapping, the pulses witnessed, and the verdicts of the rules and of R10's two "
                "halves. The gates that need no laser show a lone frame update; the two-qubit gates show the transport that "
                "brings the ions together; the non-Clifford ones are checked against the exact unitary.</p>")
    body.append("".join(_gate_row(g, f'ex/{g["id"]}.html') for g in built if g["id"] != "bell"))
    if not built:
        body.append('<p class="note">No compiled examples in this build: the compiler and the proof checker write them, '
                    'and the site build reads them.</p>')
    body.append("<p class=\"sub\">Phase 2 of the plan brings the compiler itself into the browser, so the Design page "
                "can compile and check a circuit without leaving it.</p>")
    return PAGE.format(title="Compilation - QCCD studio", style=STYLE, extra_css="main{max-width:1120px}",
                       body="\n".join(body))


def build_compiled_pages(out: Path, put) -> list[dict]:
    """Every compiled example as the page the studio opens on it: the cooled programme,
    the circuit beside it through the checked source map."""
    from ..arch import Architecture
    from ..cost import corrected_model
    from ..ir.source_map import build as build_source
    from ..ir.tsir import TSIR
    from ..verify import verify
    from ..viz.render import render_html
    built = load_compiled()
    if not built:
        return built
    archs: dict[str, Architecture] = {}
    model = corrected_model()
    for g in built:
        dev = g["meta"].get("device", "ring6d")
        if dev not in archs:
            archs[dev] = Architecture.from_json(json.loads((COMPILED / f"{dev}.arch.json").read_text(encoding="utf-8")))
        arch = archs[dev]
        prog = TSIR.load(g["dir"] / "prog.cooled.tsir.json")
        source = build_source(prog, g["cert"], g["dir"] / "circuit.qasm")
        res = verify(prog, arch, model, check_metrics=False).result
        rel = f"compilation/ex/{g['id']}.html"
        render_html(arch, prog, res, model, out / rel, kicker="COMPILED", headline=f'{g["meta"]["title"]} on {dev}',
                    lede=g["meta"]["note"], source=source, open_pane="Q")
        put(rel, (out / rel).read_text(encoding="utf-8"), 2, "compilation", app=True, extra=HASH_JS)
    return built


def doc_page(name: str, d: dict) -> str:
    body = (f'<p class="sub"><a href="../../learn/">Learn</a> &rsaquo; reference</p>'
            f'<ul class="toc">{d["toc"]}</ul>{d["body"]}')
    return PAGE.format(title=f'{html.escape(d["title"])} - QCCD studio', style=STYLE, extra_css="", body=body)



# ------------------------------------------------------------------------- physics, people, publications

def _phys_link(target: str) -> str:
    """Links in `physics.md`: a reference doc by its file name (`adl.md`) goes to its page,
    a site path (`../rules/#R7`) is kept as written, anything else goes to the repository."""
    if re.match(r"^(https?:|#|mailto:|\.\./)", target):
        return target
    path, _, frag = target.partition("#")
    frag = f"#{frag}" if frag else ""
    stem = Path(path).name[:-3] if path.endswith(".md") else None
    if stem in DOC_NAMES:
        return f"../docs/{stem}/{frag}"
    return f"{REPO}/blob/main/{path}{frag}"



# ------------------------------------------------------------------------- physics: figures

#: The zone colours the diagrams use; anything else is grey.
ZONE_HEX = {"trap": "#2a78d6", "data": "#e8b940", "load": "#1baf7a", "big": "#4a3aa7"}


def device_svg(m, labels: bool | None = None) -> str:
    """The device as the system holds it: every node of the expanded graph at its position,
    every segment as a line.  Sites are discs coloured by zone, lattice junctions small dark
    squares, and a site where three or more axes meet gets a dark ring.  Drawn straight from
    `m.arch.device`, so the picture cannot disagree with what the verifier walks."""
    dev = m.arch.device
    nodes, segs = dev.nodes, dev.segments
    if not nodes:
        return ""
    deg = {n: 0 for n in nodes}
    for s in segs.values():
        for e in s.ends:
            deg[e] = deg.get(e, 0) + 1
    xs = [n.pos[0] for n in nodes.values()]
    ys = [n.pos[1] for n in nodes.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x1 - x0, 0.5), max(y1 - y0, 0.2)
    scale = min(150.0, 820.0 / w, 420.0 / h)
    if labels is None:
        labels = len(nodes) <= 40
    r = max(3.5, min(11.0, scale * 0.13))
    pad = 34 if labels else 18
    W, H = w * scale + 2 * pad, h * scale + 2 * pad + (14 if labels else 0)
    X = lambda x: pad + (x - x0) * scale
    Y = lambda y: pad + (y1 - y) * scale       # y up on the device, down on the screen
    cx0, cy0 = X((x0 + x1) / 2), Y((y0 + y1) / 2)
    # a small device is drawn at its natural size, centred; a big one fills the column
    out = [f'<svg viewBox="0 0 {W:.0f} {H:.0f}" style="max-width:{W:.0f}px;margin:0 auto" xmlns="http://www.w3.org/2000/svg" '
           f'font-family="ui-sans-serif,system-ui,sans-serif" role="img" aria-label="the device {html.escape(m.name)}">']
    sw = max(2.0, r * 0.55)
    for s in segs.values():
        a, b = nodes[s.ends[0]], nodes[s.ends[1]]
        ax, ay, bx, by = X(a.pos[0]), Y(a.pos[1]), X(b.pos[0]), Y(b.pos[1])
        length = ((a.pos[0] - b.pos[0]) ** 2 + (a.pos[1] - b.pos[1]) ** 2) ** 0.5
        if length > 1.5:
            # a long segment (a loop closing on itself) is bowed away from the centre, so it
            # is not drawn through the nodes that happen to lie between its ends
            mx, my = (ax + bx) / 2, (ay + by) / 2
            nx, ny = -(by - ay), (bx - ax)
            nl = (nx * nx + ny * ny) ** 0.5 or 1.0
            nx, ny = nx / nl, ny / nl
            if (mx - cx0) * nx + (my - cy0) * ny < 0:
                nx, ny = -nx, -ny
            bow = min(0.45 * length * scale, 90.0)
            out.append(f'<path d="M{ax:.1f} {ay:.1f}Q{mx + nx * bow:.1f} {my + ny * bow:.1f} {bx:.1f} {by:.1f}" fill="none" '
                       f'stroke="#cfceca" stroke-width="{sw:.1f}" stroke-linecap="round"/>')
        else:
            out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="#cfceca" stroke-width="{sw:.1f}" stroke-linecap="round"/>')
    for nid, n in nodes.items():
        cx, cy = X(n.pos[0]), Y(n.pos[1])
        if n.kind == "junction":
            s2 = r * 0.9
            out.append(f'<rect x="{cx - s2:.1f}" y="{cy - s2:.1f}" width="{2 * s2:.1f}" height="{2 * s2:.1f}" rx="2" fill="#1c2a4a"/>')
        else:
            fill = ZONE_HEX.get(n.zone_type or "", "#8a8985")
            ring = f' stroke="#1c2a4a" stroke-width="{max(1.5, r * 0.28):.1f}"' if deg.get(nid, 0) >= 3 else ' stroke="#fff" stroke-width="1.5"'
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"{ring}/>')
        if labels:
            out.append(f'<text x="{cx:.1f}" y="{cy + r + 12:.1f}" font-size="10.5" text-anchor="middle" fill="#52514e">{html.escape(nid)}</text>')
    out.append("</svg>")
    return "".join(out)


def _zone_legend(m) -> str:
    zones = sorted({n.zone_type for n in m.arch.device.nodes.values() if n.zone_type})
    items = "".join(f'<span><i style="background:{ZONE_HEX.get(z, "#8a8985")}"></i>{html.escape(z)}</span>' for z in zones)
    if any(n.kind == "junction" for n in m.arch.device.nodes.values()):
        items += '<span><i style="background:#1c2a4a"></i>lattice junction</span>'
    return f'<div class="legend zones">{items}<span><i style="background:#fff;border:2px solid #1c2a4a;width:8px;height:8px"></i>a site where three or more axes meet</span></div>'


#: Devices the physics page draws besides the examples' small ones: the leaderboard's
#: 24-site ring with 8 dock spurs, the one the landing page's example runs on.
PHYS_DEVICES: dict[str, tuple[str, dict]] = {
    "ring24_8": ("ring", {"width": 12, "height": 2, "verticals": 8}),
}


def _arch_machine(name: str):
    """A device by name: one of the examples' small devices, or `arch:<file>` from `arch/`."""
    from .examples import DEVICES as EX_DEVICES, machine as ex_machine
    from ..api import Machine
    if name.startswith("arch:"):
        return Machine.load(ROOT / "arch" / f"{name[5:]}.arch.json")
    if name in PHYS_DEVICES:
        gen, params = PHYS_DEVICES[name]
        return getattr(Machine, gen)(**params, name=name)
    dev = next((d for d in EX_DEVICES if d["name"] == name), None)
    if dev is None:
        raise KeyError(f"physics.md names an unknown device {name!r}")
    return ex_machine(dev)


def _about(name: str) -> str:
    from .examples import DEVICES as EX_DEVICES
    dev = next((d for d in EX_DEVICES if d["name"] == name), None)
    return dev["about"] if dev else ""


#: The programmes the physics page runs, in embed mode, beside the prose.  Each is written
#: in the language and rendered by the studio, like the Language page's examples.
PHYS_RUNS: dict[str, dict] = {
    "shuttle": {"device": "grid2x3", "model": "corrected", "headline": "A shuttle through a T-junction, then a cool",
                "lede": "the walk from T0_0v through J0_1 to T0_1h charges the junction crossing; the cool sets n-bar back to zero",
                "program": [("init", [{"d0": "T0_0v"}], {}), ("shuttle", ["d0", ["T0_0v", "J0_1", "T0_1h"]], {}), ("cool", [], {})]},
    "dock": {"device": "ring6d", "model": "corrected", "headline": "Docking: a split at the loop and a merge at the spur",
             "lede": "d0 walks the loop to S0 and is docked into the trap A0 where d1 waits; the dock entails a split and a merge, and the gate follows a cool",
             "program": [("init", [{"d0": "S2", "d1": "A0"}], {}), ("shuttle", ["d0", ["S2", "S1", "S0"]], {}),
                         ("move", ["d0", "S0", "A0"], {"cls": "dock"}), ("cool", [], {}), ("gate", ["CX", [["d0", "d1"]]], {})]},
    "rotate": {"device": "dual3", "model": "corrected", "headline": "One waveform turns a whole loop",
               "lede": "the outer trap loop is filled and rotated past the inner data loop: one instruction moves every ion on it",
               "program": [("init", [{"a0": "AT0", "a1": "AT1", "a2": "AT2", "a3": "AB2", "a4": "AB1", "a5": "AB0", "d0": "DT0", "d1": "DT2"}], {}),
                           ("rotate", [1], {"loop": "A"}), ("rotate", [1], {"loop": "A"}), ("rotate", [1], {"loop": "A"})]},
    "budget": {"device": "chain4", "model": "corrected", "headline": "A walk, a cool, a gate: the report prices it",
               "lede": "d0 walks the register to d1; the Report pane lists the quanta by channel, the wall clock, and the gate error read off n-bar",
               "program": [("init", [{"d0": "C0", "d1": "C3"}], {}), ("shuttle", ["d0", ["C0", "C1", "C2", "C3"]], {}),
                           ("cool", [], {}), ("gate", ["CX", [["d0", "d1"]]], {}), ("measure", [["d0", "d1"]], {})]},
}


def build_phys_runs(out: Path, put) -> dict[str, dict]:
    """Render every PHYS_RUNS programme as a page under physics/ex/ and judge it."""
    from .examples import build_example, rec
    built = {}
    for rid, spec in PHYS_RUNS.items():
        m = _arch_machine(spec["device"])
        rel = f"physics/ex/{rid}.html"
        recs = [rec(meth, *args, **kw) for meth, args, kw in spec["program"]]
        r = build_example({"program": recs}, m, spec.get("model", "corrected"), out / rel, kicker="PHYSICS",
                          headline=spec["headline"], lede=spec["lede"])
        put(rel, (out / rel).read_text(encoding="utf-8"), 2, "physics", app=True, extra=HASH_JS)
        built[rid] = {**r, "page": f"ex/{rid}.html", "device": spec["device"]}
    return built


def _figures(body: str, runs: dict[str, dict]) -> str:
    """Expand the markers physics.md carries, each on a line of its own:
         {{photo:file|alt|caption|credit}}      a photograph from static/
         {{device:name|caption}}                one device, drawn from its data
         {{gallery:name,name,...}}              several devices side by side, captioned from the examples
         {{run:id|caption}}                     one of PHYS_RUNS, in embed mode
    """
    first_run = [True]

    def photo(args):
        f, alt, cap, credit = (args + ["", "", ""])[:4]
        alt = html.unescape(re.sub(r"<[^>]+>", "", alt))
        return (f'<figure class="fig photo"><img src="../static/{html.escape(f)}" alt="{html.escape(alt)}">'
                f'<figcaption>{cap} <span class="credit">{credit}</span></figcaption></figure>')

    def device(args):
        name, cap = args[0], (args[1] if len(args) > 1 else "")
        m = _arch_machine(name)
        return (f'<figure class="fig"><div class="figsvg">{device_svg(m)}{_zone_legend(m)}</div>'
                f'<figcaption>{cap}</figcaption></figure>')

    def gallery(args):
        cells = []
        for name in args[0].split(","):
            name = name.strip()
            m = _arch_machine(name)
            cells.append(f'<figure><div class="figsvg">{device_svg(m)}</div>'
                         f'<figcaption><b>{html.escape(name)}</b>: {html.escape(_about(name))}</figcaption></figure>')
        return f'<div class="gallery">{"".join(cells)}</div>'

    def run(args):
        rid, cap = args[0], (args[1] if len(args) > 1 else "")
        r = runs[rid]
        lazy = "" if first_run[0] else ' loading="lazy"'
        first_run[0] = False
        return (f'<figure class="fig run"><iframe class="live"{lazy} src="{r["page"]}#embed&amp;step=1" title="{html.escape(PHYS_RUNS[rid]["headline"])}"></iframe>'
                f'<figcaption>{cap} <a href="{r["page"]}#step=1">Open the page &rarr;</a></figcaption></figure>')

    kinds = {"photo": photo, "device": device, "gallery": gallery, "run": run}

    def sub(mm):
        # the captions went through the prose renderer already (links, bold, hover cards)
        # and stay HTML; file names and device names are plain
        kind, rest = mm.group(1), mm.group(2)
        return kinds[kind]([a.strip() for a in rest.split("|")])

    return re.sub(r"<p>\{\{(photo|device|gallery|run):(.*?)\}\}</p>", sub, body, flags=re.S)


def render_physics() -> dict:
    """`qccd/site/physics.md` through the docs renderer: the same hover vocabulary, the
    same anchors, one table of contents from its `##` headings."""
    r = Renderer(hints(), _phys_link)
    body = r.render(prose.strip((HERE / "physics.md").read_text(encoding="utf-8"), "physics"))
    title = html.unescape(re.sub(r"<[^>]+>", "", r.headings[0][2])) if r.headings else "Physics background"
    toc = "".join(f'<li><a href="#{sid}">{text}</a></li>' for lvl, sid, text in r.headings if lvl == 2)
    return {"title": title, "body": body, "toc": toc, "headings": r.headings}


def physics_page(d: dict, runs: dict[str, dict] | None = None) -> str:
    body = (f'<p class="sub"><a href="../learn/">Learn</a> &rsaquo; background</p>'
            f'<ul class="toc">{d["toc"]}</ul>{_figures(d["body"], runs or {})}')
    return PAGE.format(title=f'{html.escape(d["title"])} - QCCD studio', style=STYLE, extra_css="", body=body)


def people_page() -> str:
    """One card per person, grouped; then the three institutions with their marks."""
    ids = [g for g, _, _ in PEOPLE_GROUPS]
    group_of = lambda p: p.get("group") if p.get("group") in ids else ids[-1]
    body = ["<h1>People</h1>",
            '<p class="sub">Who takes part in the project. The work is led by a collaboration between UCLA and '
            'UC Berkeley and funded by the Challenge Institute for Quantum Computation, an NSF Quantum Leap '
            'Challenge Institute, and by NQVL FTL, within which the project began. Its contributors are '
            'also based at the University of Arizona, the University of Michigan, UC San Diego and '
            'Cornell University.</p>']
    for gid, gtitle, gblurb in PEOPLE_GROUPS:
        mine = [p for p in PEOPLE if group_of(p) == gid]
        if not mine:
            continue
        body.append(f'<h2 id="{gid}">{html.escape(gtitle)}</h2><p class="sub">{html.escape(gblurb)}</p><div class="folk">')
        for p in mine:
            links = "".join(f'<a href="{html.escape(u)}">{html.escape(k)}</a>' for k, u in (p.get("links") or {}).items())
            # an empty field leaves no empty box behind
            body.append(f'<div class="person" id="{slug(p["name"])}"><b>{html.escape(p["name"])}</b>'
                        + (f'<div class="role">{html.escape(p["role"])}</div>' if p.get("role") else "")
                        + (f'<div class="aff">{html.escape(p["affiliation"])}</div>' if p.get("affiliation") else "")
                        + (f'<p>{html.escape(p["about"])}</p>' if p.get("about") else "")
                        + (f'<div class="links">{links}</div>' if links else "") + "</div>")
        body.append("</div>")
    body.append('<h2 id="institutions">Institutions</h2><div class="inst">')
    for i in INSTITUTIONS:
        # a logo and a link are both optional: a place with no mark on file is a text card,
        # and a funder with no page to point at is a card that is not a link
        mark = (f'<img src="../static/{i["logo"]}" alt="{html.escape(i["short"])}">'
                if i.get("logo") else "")
        inner = (f'{mark}<div><b>{html.escape(i["name"])}</b>'
                 f'<span>{html.escape(i["what"])}</span></div>')
        body.append(f'<a href="{html.escape(i["url"])}">{inner}</a>' if i.get("url")
                    else f'<div class="card">{inner}</div>')
    body.append("</div>")
    body.append(f'<p class="note">{Renderer({}, lambda t: t).inline(JOIN, hint=False)} '
                f'<a href="{REPO}/graphs/contributors">Contributors on GitHub &rarr;</a></p>')
    return PAGE.format(title="People - QCCD studio", style=STYLE, extra_css="", body="\n".join(body))


def _pub_links(p: dict) -> str:
    out = []
    if p.get("arxiv"):
        out.append(f'<a href="https://arxiv.org/abs/{p["arxiv"]}">arXiv:{p["arxiv"]}</a>')
    if p.get("doi"):
        out.append(f'<a href="https://doi.org/{p["doi"]}">doi:{p["doi"]}</a>')
    if p.get("url"):
        out.append(f'<a href="{html.escape(p["url"])}">{html.escape(p.get("url_label", "link"))}</a>')
    return "".join(out)


def _authors(a: list[str]) -> str:
    if not a:
        return ""
    return html.escape(a[0] if len(a) == 1 else ", ".join(a[:-1]) + " and " + a[-1])


def publications_page() -> str:
    """The papers by group, each with where it enters the site; then how to cite the tool."""
    body = ["<h1>Publications</h1>",
            '<p class="sub">The publications of the project, grouped by what each one contributes, and how to '
            'cite the website itself.</p>',
            '<ul class="toc2">' + "".join(f'<li><a href="#{gid}">{html.escape(t)}</a></li>' for gid, t, _ in PUB_GROUPS
                                          if any(p.get("group") == gid for p in PUBS))
            + '<li><a href="#cite">Citing this website</a></li></ul>']
    if not PUBS:
        body.append('<p class="note">No publications are listed yet. Each entry names the paper, its authors, '
                    'venue and links, and one line on where it enters the site.</p>')
    for gid, gtitle, gblurb in PUB_GROUPS:
        mine = [p for p in PUBS if p.get("group") == gid]
        if not mine:
            continue
        body.append(f'<h2 id="{gid}">{html.escape(gtitle)}</h2><p class="sub">{html.escape(gblurb)}</p><ul class="pubs">')
        for p in mine:
            year = f' ({p["year"]})' if p.get("year") else ""
            used = f'<p class="use"><b>used for</b>{html.escape(p["used"])}</p>' if p.get("used") else ""
            body.append(f'<li class="pub" id="{p["key"]}"><p class="ti">{html.escape(p["title"])}</p>'
                        f'<p class="au">{_authors(p.get("authors", []))}</p>'
                        f'<p class="ve">{html.escape(p.get("venue", ""))}{year}{_pub_links(p)}</p>{used}</li>')
        body.append("</ul>")
    body.append('<h2 id="cite">Citing this website</h2>'
                f'<p class="sub">{html.escape(SOFTWARE["note"])}</p>'
                f'<div class="cite"><pre><code>{html.escape(SOFTWARE["bibtex"])}</code></pre></div>')
    return PAGE.format(title="Publications - QCCD studio", style=STYLE, extra_css="", body="\n".join(body))


# ------------------------------------------------------------------------- build

def favicon(size: int = 32) -> bytes:
    """A 32 px PNG (browsers accept PNG bytes at /favicon.ico): the accent disc, one
    trapping-site pill across it.  Written here so the site needs no image file."""
    import struct
    import zlib
    r = size / 2
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx, dy = x + .5 - r, y + .5 - r
            inside = dx * dx + dy * dy <= (r - 1) ** 2
            pill = abs(dy) <= size * .12 and abs(dx) <= size * .3
            row += bytes((255, 255, 255, 255) if (inside and pill) else
                         (0x2a, 0x78, 0xd6, 255) if inside else (0, 0, 0, 0))
        rows.append(bytes(row))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def build(out: Path) -> int:
    out = Path(out)
    if out.exists() and not (out / MARKER).exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and was not written by `qccd site`; refusing to overwrite it")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("written by `python -m qccd site`\n", encoding="utf-8")
    (out / "favicon.ico").write_bytes(favicon())
    shutil.copytree(HERE / "static", out / "static")

    ts = tasks()
    parts, less = lessons()
    docs = render_docs()
    phys = render_physics()

    # the search index, before any page is written: every page carries it
    index: list[dict] = [{"t": label, "d": blurb, "u": u, "k": "part"} for key, label, u, blurb in PARTS]
    index += [{"t": f"Lesson {L['id']} · {L['title']}", "d": f"the course, part {L['part']}",
               "u": f"studio.html#learn={L['id']}", "k": "lesson"} for L in less]
    for name in DOC_NAMES:
        for lvl, sid, text in docs[name]["headings"]:
            index.append({"t": html.unescape(re.sub(r"<[^>]+>", "", text)),
                          "d": f"{docs[name]['title']} · docs", "u": f"docs/{name}/#{sid}", "k": "doc"})
    index += [{"t": f"p.{v['verb']}", "d": v["what"][:90], "u": f"language/#{v['verb']}", "k": "syntax"} for v in VERBS]
    index += [{"t": f"{R['id']} · {rule_meta(R['id'])['statement'][:70]}", "d": "a rule, with a passing and a failing programme",
               "u": f"rules/#{R['id']}", "k": "rule"} for R in RULES]
    index += [{"t": f"compile {title}", "d": note[:90], "u": f"compilation/#{gid}", "k": "compiled"} for gid, body, title, note, dev in GATES]
    index.append({"t": "Compilation", "d": "from QASM to hardware instructions, and how R10 is decided", "u": "compilation/", "k": "page"})
    index.append({"t": "Physics background", "d": "from trapping one ion to the noise model, and how the website simulates it",
                  "u": "physics/", "k": "page"})
    index += [{"t": html.unescape(re.sub(r"<[^>]+>", "", text)), "d": "Physics background",
               "u": f"physics/#{sid}", "k": "physics"} for lvl, sid, text in phys["headings"] if lvl == 2]
    index.append({"t": "People", "d": "who takes part in the project", "u": "people/", "k": "page"})
    index += [{"t": p["name"], "d": " \u00b7 ".join(x for x in (p.get("role"), p.get("affiliation")) if x),
               "u": f"people/#{slug(p['name'])}", "k": "person"} for p in PEOPLE]
    # how the site is built for AI agents (qccd/site/agentic.py): generated from the workspace's own code
    from .agentic import index_entries as agentic_index_entries
    index += agentic_index_entries()
    index.append({"t": "Publications", "d": "the papers this website is built on, and how to cite it", "u": "publications/", "k": "page"})
    index += [{"t": p["title"], "d": (p.get("venue", "") + (" \u00b7 " if p.get("venue") else "") + (p.get("used") or ""))[:110],
               "u": f"publications/#{p['key']}", "k": "paper"} for p in PUBS]
    for t in ts:
        index.append({"t": t["title"], "d": f"leaderboard · {len(t['rows'])} designs", "u": f"board/{t['id']}/", "k": "task"})
        for r in t["rows"]:
            if r.get("status") == "ok" and r.get("page"):
                index.append({"t": r.get("short") or r["key"], "d": f"{t['title']} · {r.get('family', '')}",
                              "u": f"board/{t['id']}/{r['page']}", "k": "entry"})
    # the logical-gadget tool (qccd/gadget/site.py, docs/GADGETS.md)
    from ..gadget.site import index_entries as gadget_index_entries
    index += gadget_index_entries()
    idx = json.dumps(index, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

    def put(rel: str, page: str, depth: int, active: str | None, app: bool = False, extra: str = "") -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        page = with_nav(page, depth, active, idx, app=app, extra=extra)
        page = page.replace("__FOOTER__", FOOTER).replace("__SITEROOT__", "../" * depth)
        p.write_text(page, encoding="utf-8", newline="")

    # the studio, once: the one page Learn and Design both are
    from ..__main__ import main as qccd_main
    studio = out / "studio.html"
    qccd_main(["studio", "-o", str(studio)])
    from . import cowork, qec_cycle
    # the QEC-cycle panel rides in with the hash router: the studio itself is untouched,
    # and the panel reads the page's own priced round (qccd/site/qec_cycle.py); the Agent
    # button is the way from this page to a local co-design workspace (qccd/site/cowork.py)
    put("studio.html", studio.read_text(encoding="utf-8"), 0, None, app=True,
        extra=HASH_JS + qec_cycle.studio_block() + cowork.studio_block())

    # the compiled companion Part D steps through.  A lesson with `page:` set runs on a
    # COMPILED page, and says so; it links to this one by bare file name, beside the
    # studio.  Built here from the shipped artifact because the browser cannot compile.
    comp_tsir = ROOT / "Compiler" / "build" / "matrix" / "micro_grid9x9.cooled.tsir.json"
    comp_qasm = ROOT / "Compiler" / "examples" / "micro.qasm"
    if comp_tsir.exists() and comp_qasm.exists():
        companion = out / "micro_grid9x9.html"
        qccd_main(["studio", "-o", str(companion),
                   "--tsir", str(comp_tsir), "--qasm", str(comp_qasm)])
        put("micro_grid9x9.html", companion.read_text(encoding="utf-8"), 0, None,
            app=True, extra=HASH_JS)
        print("  companion    micro_grid9x9.html, the page Part D runs on")
    else:
        missing = comp_tsir if not comp_tsir.exists() else comp_qasm
        print(f"  companion    SKIPPED, {missing} is missing -- Part D will 404")

    put("index.html", landing(ts), 0, None)
    put("learn/index.html", learn_page(parts, less, docs, ts), 1, "learn")
    put("design/index.html",
        PAGE.format(title="Design - QCCD studio", style=STYLE,
                    extra_css="", body='<p class="sub">Opening the studio&hellip; '
                                       '<a href="../studio.html#design">continue</a></p>')
        .replace("<title>", '<meta http-equiv="refresh" content="0; url=../studio.html#design"><title>'),
        1, "design")
    for name in DOC_NAMES:
        put(f"docs/{name}/index.html", doc_page(name, docs[name]), 2, "learn")
    put("discuss/index.html", discuss_page(), 1, "discuss")
    put("credit/index.html", credit_page(), 1, None)
    runs = build_phys_runs(out, put)
    put("physics/index.html", physics_page(phys, runs), 1, "physics")
    put("people/index.html", people_page(), 1, "people")
    from . import agentic
    put("agentic/index.html", agentic.page(PAGE, STYLE), 1, "agentic")
    put("publications/index.html", publications_page(), 1, "publications")
    lang, rules = build_examples(out, put)
    put("language/index.html", language_page(lang), 1, "language")
    put("rules/index.html", rules_page(rules), 1, "rules")
    compiled = build_compiled_pages(out, put)
    put("compilation/index.html", compilation_page(compiled), 1, "compilation")
    print(f"  compilation  {len(compiled)} compiled examples")
    from ..gadget.site import build_section as build_gadget_section
    build_gadget_section(out, put)
    print(f"  language     {len(lang)} statements, rules {len(rules)} with {sum(len([w for w in ('pass', 'fail') if w in e]) for e in rules.values())} example pages")

    put("board/index.html", board_index(ts), 1, "board")
    from . import qec_cycle
    n_pages = 0
    for t in ts:
        src = t["seed_dir"]
        if not (src / "index.html").exists():
            print(f"  {t['id']:12s} no seed pages under {src}")
            continue
        dst = out / "board" / t["id"]
        dst.mkdir(parents=True, exist_ok=True)
        put(f"board/{t['id']}/index.html", (src / "index.html").read_text(encoding="utf-8"), 2, "board",
            extra=FOOTER_BLOCK + BOARD_SKIN)
        # the published manifest carries only the listed designs; the seed directory keeps
        # the full record, disqualified rows included
        (dst / "manifest.json").write_text(json.dumps(t["rows"], indent=1, default=str) + "\n",
                                           encoding="utf-8", newline="\n")
        if (src / "rows.json").exists():
            out_keys = {r["key"] for r in t["rows"]}
            raw = json.loads((src / "rows.json").read_text(encoding="utf-8"))
            kept = [r for r in raw if not isinstance(r, dict) or r.get("key") is None or r["key"] in out_keys]
            (dst / "rows.json").write_text(json.dumps(kept, indent=1, default=str) + "\n",
                                           encoding="utf-8", newline="\n")
        for r in t["rows"]:
            if r.get("status") != "ok" or not r.get("page"):
                continue
            # an entry page IS a circuit: it gets the classical layer and the cycle panel
            # too, so the half of the machine that reads the ions is visible there as well
            put(f"board/{t['id']}/{r['page']}", (src / r["page"]).read_text(encoding="utf-8"), 2, "board",
                app=True, extra=HASH_JS + qec_cycle.studio_block())
            n_pages += 1
            gif = r.get("gif")
            if gif and (src.parent / gif).exists():
                shutil.copy2(src.parent / gif, out / "board" / gif)
        print(f"  {t['id']:12s} {sum(1 for r in t['rows'] if r.get('status') == 'ok'):3d} entries from {src.relative_to(ROOT)}")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"wrote {out}  ({n_pages} entry pages, {len(index)} search entries, {total / 1e6:.0f} MB)")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="the website, as static files")
    ap.add_argument("-o", "--out", default=str(ROOT / "site"))
    a = ap.parse_args(argv)
    return build(Path(a.out))


if __name__ == "__main__":
    raise SystemExit(main())
