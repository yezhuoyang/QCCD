"""`python -m qccd site` -- the website, as static files.

The site is a build target that ARRANGES what exists: the studio page (`qccd studio`),
the reference docs (`docs/*.md`), the boards `Codesign/scripts/bb_studio.py` already
rendered for the five tasks, and a discussion page.  It writes plain HTML.  The only
thing it adds to a page is the 40 px navigation bar with its search box, and the only
third-party script on the whole site is giscus on /discuss/.  Nothing runs on a server.

    site/                  the landing: one sentence, four tiles
    site/studio.html       the design tool; #learn opens the course, #learn=B2 a lesson,
                           #design the blank canvas
    site/learn/            the learning path: the course, the docs, then a real design
    site/design/           -> studio.html#design
    site/board/            the five tasks; board/<task>/ is the ranking page, and the
                           entry pages sit beside it exactly as bb_studio.py wrote them
    site/discuss/          GitHub Discussions, embedded
    site/docs/<name>/      adl, tsir, rules, phys, rendered from docs/*.md

A task is `tasks/<id>/task.json`; its `seed` names the directory whose `manifest.json`
and pages are the board's rows today (`BBResults/studio`, `SmallCode/<code>`).
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from .examples import GRAMMAR, IR_TABLE, RULES, VERBS, build_example, machine, rule_meta
from .md import Renderer, hints

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
              "rules": "All 23 rules, what each one checks, and what the verifier can honestly say about it.",
              "phys": "From the graph to electrodes: the metal a device implies, and its design rules."}


def _t(r: dict):
    return (r.get("numbers") or {}).get("T_jones")


def fam_color(f: str) -> str:
    return FAMHEX.get(f, GREY)


def short_name(r: dict) -> str:
    """The design's name: the row's short name, else its key without the rank prefix."""
    return r.get("short") or re.sub(r"^\d+_", "", r["key"])


STYLE = """
:root{color-scheme:light;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8985;--line:#e6e5e1;--grid:#efeeeb;--accent:#2a78d6}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:28px 24px 64px}
h1{font-size:24px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:18px;font-weight:600;margin:30px 0 8px} h3{font-size:15px;font-weight:600;margin:22px 0 6px}
p,li{color:var(--ink)} .sub{color:var(--ink2);max-width:76ch}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
code{font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f3f2ee;padding:1px 4px;border-radius:4px}
pre{background:#f7f6f2;border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto}
pre code{background:none;padding:0;font-size:12.5px}
.tw{overflow-x:auto} table{border-collapse:collapse;font-size:13px;margin:8px 0}
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
.card{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;color:inherit}
.card:hover{border-color:var(--accent);text-decoration:none} .card h2{font-size:16px;margin:0 0 6px;color:#1c2a4a}
.card p{margin:0 0 6px;color:var(--ink2);font-size:13px} .card .best{color:var(--ink)}
.card table{width:100%;font-size:12.5px} .card td:nth-child(n+2),.card th:nth-child(n+2){text-align:right;font-variant-numeric:tabular-nums}
.lessons{list-style:none;padding:0;margin:4px 0 0} .lessons li{padding:3px 0;display:flex;gap:10px;align-items:baseline}
.lessons .id{color:var(--ink3);font-variant-numeric:tabular-nums;width:2.4em;flex:0 0 auto} .lessons .stars{color:#d59a00;margin-left:auto;font-size:12px;white-space:nowrap}
.part{margin:18px 0 0} .part h3{margin:0 0 2px} .part .ms{color:var(--ink2);font-size:13px;margin:0}
.note{background:#fff;border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:10px 14px;margin:14px 0;color:var(--ink2)}
/* learn: the path, one row per part */
.path{display:flex;flex-direction:column;gap:14px;margin:10px 0 0}
.pt{display:grid;grid-template-columns:72px 1fr;gap:18px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.pt .ic{width:64px;height:64px;display:block} .pt h3{margin:0 0 2px;font-size:16px}
.pt .kick{font-size:11px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;margin-bottom:2px}
.pt .sum{color:var(--ink2);margin:0 0 8px;font-size:13.5px;max-width:70ch}
.pt .lessons{columns:2;column-gap:28px} .pt .lessons li{break-inside:avoid}
.pt .prog{font-size:12px;color:var(--ink3);margin-left:auto;white-space:nowrap}
.docs{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.doc{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px;color:inherit}
.doc:hover{border-color:var(--accent);text-decoration:none} .doc b{display:block;color:#1c2a4a;margin-bottom:2px} .doc span{color:var(--ink2);font-size:13px}
.doc .tag{display:inline-block;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:700;margin-bottom:4px}
/* board: one row per task */
.task{display:grid;grid-template-columns:240px 1fr 170px;gap:26px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 22px;margin:14px 0}
.task h2{margin:0 0 4px;font-size:17px} .task h2 a{color:#1c2a4a} .task .desc{color:var(--ink2);font-size:13px;margin:0 0 10px}
.task .more{font-size:13px} .task .meta{font-size:12.5px;color:var(--ink3);margin:8px 0 0}
.bars{display:flex;flex-direction:column;gap:3px}
.bar{display:grid;grid-template-columns:168px 1fr 68px;align-items:center;gap:8px;font-size:12.5px;color:inherit;text-decoration:none}
.bar:hover{text-decoration:none} .bar:hover .lbl{color:var(--accent)}
.bar .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .tr{display:block;height:10px;background:var(--grid);border-radius:3px;overflow:hidden} .bar .fl{display:block;height:100%;border-radius:3px}
.bar .v{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2)}
.bar.best .lbl,.bar.best .v{font-weight:700;color:#0b7a4b} .bar.bad .lbl{color:#c62828}
.refused{font-size:12px;color:var(--ink3);margin-top:8px} .refused b{color:#c62828;font-weight:600}
.stats{display:grid;grid-template-columns:1fr;gap:8px;align-content:start}
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
.verb,.rule{border-top:1px solid var(--line);padding:20px 0 10px;scroll-margin-top:48px}
.verb h3,.rule h3{margin:0 0 6px;font-size:16px} .verb h3 code{font-size:14px;background:none;padding:0;color:#1c2a4a}
.two{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}
dl.sem{margin:8px 0 0;display:grid;grid-template-columns:64px 1fr;gap:5px 10px;font-size:13px}
dl.sem dt{color:var(--ink3);text-transform:uppercase;font-size:10.5px;letter-spacing:.06em;padding-top:3px} dl.sem dd{margin:0}
.ex{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px;position:relative}
.ex pre{margin:0 0 8px;background:#f7f6f2;font-size:12px;padding:10px 12px;overflow-x:auto} .ex pre.ir{white-space:pre-wrap;word-break:break-all}
.two>*,.pair>*{min-width:0}
.ex .tag{position:absolute;top:10px;right:12px;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;font-weight:700;padding:2px 8px;border-radius:10px;background:#efeeeb;color:#52514e}
.ex.pass .tag{background:#e6f4ec;color:#0b7a4b} .ex.fail .tag{background:#fbe9e7;color:#c62828} .ex.partial .tag{background:#fff3e0;color:#b26a00} .ex.any .tag{background:#eef3fb;color:#2a78d6}
.ex.fail{border-color:#f1c4c0} .ex.pass{border-color:#bfe3cf}
.ex .why{font-size:12.5px;color:var(--ink2);margin:0 0 8px;padding-right:70px}
.verdict{font-size:12.5px;margin:0 0 8px} .verdict b.ok{color:#0b7a4b} .verdict b.bad{color:#c62828} .verdict b.skip{color:#52514e} .verdict b.partial{color:#b26a00}
.verdict .m{display:block;color:var(--ink2);margin-top:2px} .verdict .nums{display:block;color:var(--ink3);margin-top:3px}
.runbox{margin:6px 0 4px}
.runbox button.run{font:inherit;font-size:13px;padding:6px 12px;border:1px solid #cfceca;border-radius:7px;background:#fff;cursor:pointer;color:var(--ink)}
.runbox button.run:hover{border-color:var(--accent);color:var(--accent)}
.runbox iframe.live{display:block;width:100%;height:300px;border:1px solid var(--line);border-radius:8px;background:#fff}
.ex .open{font-size:12px}
.rule h3 .st{font-weight:400;color:var(--ink2);font-size:14px} .rule .checks{margin:0 0 4px;font-size:13.5px}
.rule .src{font-size:12px;color:var(--ink3);margin:0 0 10px} .pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.toc2{font-size:13px;display:flex;flex-wrap:wrap;gap:4px 12px;margin:0 0 6px;padding:0;list-style:none}
.contract{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:10px 0 0} .contract div{border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12.5px;background:#fff}
.contract b{display:block;margin-bottom:2px} .contract .ok b{color:#0b7a4b} .contract .bad b{color:#c62828} .contract .skip b{color:#52514e} .contract .partial b{color:#b26a00}
@media (max-width:860px){.two,.pair{grid-template-columns:1fr}.contract{grid-template-columns:1fr 1fr}}
"""


APP_CSS = "<style>main{height:calc(100vh - 40px)!important}</style>"

# The footer on every document page: who leads the project and who funds it, with the
# three marks.  The wordmarks are the public-domain text logos; CIQC's is its own header
# image.  `__SITEROOT__` becomes the page's path back to the site root.
FOOTER = """<footer id="sitefoot">
  <p>Led by a collaboration between <a href="https://www.ucla.edu/">UCLA</a> and
  <a href="https://www.berkeley.edu/">UC Berkeley</a>, funded by the
  <a href="https://ciqc.berkeley.edu/">Challenge Institute for Quantum Computation</a> (CIQC),
  an NSF Quantum Leap Challenge Institute.</p>
  <div class="logos">
    <a href="https://www.ucla.edu/" title="UCLA"><img src="__SITEROOT__static/ucla.svg" alt="UCLA"></a>
    <a href="https://www.berkeley.edu/" title="University of California, Berkeley"><img src="__SITEROOT__static/berkeley.svg" alt="University of California, Berkeley"></a>
    <a href="https://ciqc.berkeley.edu/" title="Challenge Institute for Quantum Computation"><img src="__SITEROOT__static/ciqc.png" alt="Challenge Institute for Quantum Computation"></a>
  </div>
</footer>"""
FOOTER_CSS = """
#sitefoot{background:#fff;border-top:1px solid #e6e5e1;padding:22px 24px 26px;text-align:center;color:#52514e;
 font:13.5px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
#sitefoot p{margin:0 auto 16px;max-width:72ch;color:#52514e}
#sitefoot a{color:#0b0b0b;text-decoration:none} #sitefoot p a{border-bottom:1px solid #cfceca} #sitefoot p a:hover{border-bottom-color:#2a78d6}
#sitefoot .logos{display:flex;justify-content:center;align-items:center;gap:44px;flex-wrap:wrap}
#sitefoot .logos img{display:block;height:38px;width:auto} #sitefoot .logos img[alt="UCLA"]{height:30px}
"""
FOOTER_BLOCK = "<style>" + FOOTER_CSS + "</style>" + FOOTER
STYLE = STYLE + FOOTER_CSS

HASH_JS = """<script>
(function(){
  // The site's deep links into an app page: #learn opens the course, #learn=B2 a lesson,
  // #design is the blank canvas (the default), and #embed (with &step=N) is the page as
  // the landing shows it -- head, tools bar, rail and dock folded away, the stage playing
  // on a loop.  It is the same page, not a picture of it.  Under the test shim there is
  // no `location`, and nothing here runs.
  if(typeof location === 'undefined' || typeof window === 'undefined' || !window.addEventListener) return;
  var looping = false;
  function embed(){
    document.body.setAttribute('data-embed', '1');
    try { var rail = document.getElementById('rail'); if(rail && typeof foldPanel === 'function') foldPanel(rail, true); } catch(e){}
    try { var dk = document.getElementById('dock'); if(dk && typeof foldPanel === 'function') foldPanel(dk, true); } catch(e){}
    try { if(typeof relayout === 'function') relayout(); } catch(e){}
    var play = document.getElementById('play'), reset = document.getElementById('reset'), slider = document.getElementById('slider');
    if(looping || !play) return;
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
    if(h.split('&').indexOf('embed') >= 0){ embed(); return; }
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
</style>"""

EXAMPLE_JS = """<script>
(function(){
  // Run: the example's own page, in embed mode, takes the button's place
  document.addEventListener('click', function(ev){
    var b = ev.target && ev.target.closest ? ev.target.closest('button.run') : null; if(!b) return;
    var box = b.parentNode, f = document.createElement('iframe');
    f.className = 'live'; f.src = box.getAttribute('data-src'); f.title = 'the example, running';
    box.replaceChild(f, b);
  });
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
        t["rows"] = (json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
                     if (seed / "manifest.json").exists() else [])
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


def with_nav(page: str, depth: int, active: str | None, index_json: str,
             app: bool = False, extra: str = "") -> str:
    """The bar goes right after `<body>`; an app page (the studio frame, one viewport
    high) also gets the 40 px taken off its `main`."""
    bar = (APP_CSS if app else "") + nav_html(depth, active, index_json)
    # the real tag, not the `<body data-explain>` a stylesheet comment in the studio quotes
    i = page.index("<body", page.index("</head>"))
    i = page.index(">", i) + 1
    if extra:
        j = page.rindex("</body>")
        page = page[:j] + extra + page[j:]
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
        body = r.render((DOCS / f"{name}.md").read_text(encoding="utf-8"))
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
        url = page + "#embed&step=2"
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
        body.append('<p class="note">This build carries no course: <code>qccd/viz/js/tutorial.js</code> '
                    'is not in the tree it was built from.</p>')
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
    body.append('<a class="doc" href="../language/"><span class="tag">the language</span><b>Every statement, with a running example</b>'
                '<span>The syntax, what each statement does to the machine, what it costs, and the IR beneath it.</span></a>')
    body.append('<a class="doc" href="../rules/"><span class="tag">the rules</span><b>All 23 rules, each with a programme that passes and one that fails</b>'
                '<span>The verifier\'s own verdicts, runnable here.</span></a>')
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
    one in green, refused and rule-failing ones called out, and the numbers that matter."""
    rows_html = []
    for t in ts:
        rows = t["rows"]
        ok = [r for r in rows if r.get("status") == "ok" and _t(r) is not None]
        ok.sort(key=_t)
        sound = [r for r in ok if _entry_ok(r) and _r10(r)]
        best = sound[0] if sound else None
        refused = [r for r in rows if r not in ok]
        tmax = max((_t(r) for r in ok), default=1.0)
        fmt = (lambda v: f"{v:.2f} ms") if tmax < 100 else (lambda v: f"{v:.0f} ms")
        bars = []
        for r in ok:
            cls = "bar" + (" best" if r is best else "") + ("" if _entry_ok(r) else " bad")
            name = html.escape(short_name(r))
            title = html.escape(r.get("title", ""))
            note = "" if _entry_ok(r) else " &middot; rules failed"
            bars.append(f'<a class="{cls}" href="{t["id"]}/{r["page"]}" title="{title}">'
                        f'<span class="lbl">{name}{note}</span>'
                        f'<span class="tr"><span class="fl" style="width:{100 * _t(r) / tmax:.1f}%;background:{fam_color(r.get("family", ""))}"></span></span>'
                        f'<span class="v">{fmt(_t(r))}</span></a>')
        ref = ""
        if refused:
            ref = ('<div class="refused"><b>refused</b> by the compiler: ' +
                   ", ".join(html.escape(short_name(r)) for r in refused) + "</div>")
        n_lean = sum(1 for r in ok if _r10(r))
        n_bad = sum(1 for r in ok if not _entry_ok(r))
        stats = (
            f'<div class="stat good"><span>fastest verified</span><b>{fmt(_t(best)) if best else "&ndash;"}</b>'
            f'<small>{html.escape(short_name(best)) if best else "none yet"}</small></div>'
            f'<div class="stat lean"><span>R10 by Lean</span><b>{n_lean}</b><small>of {len(ok)} that run it</small></div>'
            f'<div class="stat"><span>designs</span><b>{len(rows)}</b><small>{len(ok)} run the round</small></div>'
            f'<div class="stat{" warn" if (refused or n_bad) else ""}"><span>refused &middot; rules failed</span>'
            f'<b>{len(refused)} &middot; {n_bad}</b><small>{"marked in red" if (refused or n_bad) else "none"}</small></div>')
        rows_html.append(
            f'<section class="task" id="{t["id"]}"><div><h2><a href="{t["id"]}/">{html.escape(t["title"])}</a></h2>'
            f'<p class="desc">{html.escape(t.get("description", ""))}</p>'
            f'<p class="more"><a href="{t["id"]}/">Open the ranking plot &rarr;</a></p>'
            f'<p class="meta">{t.get("n_data", "?")} data qubits &middot; ranked by time on the jones table &middot; click a bar to step the programme</p></div>'
            f'<div><div class="bars">{"".join(bars)}</div>{ref}</div>'
            f'<div class="stats">{stats}</div></section>')
    legend = ('<div class="legend">' +
              "".join(f'<span><i style="background:{c}"></i>{f}</span>' for f, c in FAMHEX.items()) +
              f'<span><i style="background:{GREY}"></i>a loop without docks, a line, rails, or two loops</span>'
              '<span><i style="background:#0b7a4b"></i>fastest verified</span><span><i style="background:#c62828"></i>refused or rules failed</span></div>')
    body = ("<h1>Leaderboard</h1><p class=\"sub\">One board per task. A task is a fixed circuit, physics package "
            "and cost table; every design on a board ran one syndrome-extraction round of that circuit, was "
            "replayed against the 23 rules and, where it passed, checked by the proved Lean checker (R10). "
            "Each row ranks its designs by running time; open a task for the full plot, where you can rank by "
            "anything and click a dot to step the programme in the studio.</p>" + legend + "".join(rows_html) +
            "<p class=\"note\">Contributing a design from the studio (Verify &rarr; Contribute) arrives in phase 3 of "
            f"<a href=\"{REPO}/blob/main/docs/WEBSITE_PLAN.md\">the plan</a>; until then the boards carry the study's seed entries.</p>")
    return PAGE.format(title="Leaderboard - QCCD studio", style=STYLE, extra_css="", body=body)


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
                      f'<a href="https://giscus.app">giscus.app</a> into <code>qccd/site/giscus.json</code>. '
                      f'Until then, <a href="https://github.com/{cfg["repo"]}/discussions">discuss on GitHub</a>.</p>')
        secs.append(f'<h2 id="{cat.lower()}">{cat}</h2><p class="sub">{blurb}</p>{widget}')
    body = ("<h1>Discuss</h1><p class=\"sub\">GitHub Discussions on the repository, embedded here with giscus. "
            "A rule change is a pull request touching the rule's doc, its Python and browser twins and the parity "
            "test together; the discussion happens here, the decision is a merge.</p>" + "".join(secs))
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


def _example_card(ex: dict, focus: str | None, root: str) -> str:
    cls, tag, verdict = _verdict(ex["verdict"], focus, ex.get("expect"), ex.get("tag"))
    return (f'<div class="ex {cls}"><span class="tag">{tag}</span>'
            f'<p class="why">{ex["why"]}</p>'
            f'<pre{" class=\"ir\"" if ex.get("ir") else ""}><code>{html.escape(ex["text"])}</code></pre>'
            f'<div class="verdict">{verdict}</div>'
            f'<div class="runbox" data-src="{ex["page"]}#embed&amp;step=1"><button class="run" type="button">&#9654; Run it here</button></div>'
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
                "<p class=\"sub\">The IR reference is <a href=\"../docs/tsir/\">docs/tsir</a>; the device language "
                "the programmes run on is <a href=\"../docs/adl/\">docs/adl</a>.</p>")
    return PAGE.format(title="Language - QCCD studio", style=STYLE, extra_css="main{max-width:1120px}",
                       body="\n".join(body) + EXAMPLE_JS)


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
            "<p class=\"sub\">Twenty-three rules a programme must obey on a QCCD machine, each traced to a source. "
            "The verifier replays every cycle and reports each rule as one of four things; a green tick is "
            "only ever printed for a check that ran. Each rule below has a programme that passes it and one "
            "that fails it, both judged by the real verifier and both runnable here as the page the studio "
            "would open on them. The rules are code: <a href=\"../docs/rules/\">docs/rules</a> is the prose, "
            "<code>qccd/verify/rules.py</code> the checks, <code>engine.js</code> their browser twins, and a "
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
        cards = "".join(_example_card(b[w], R["id"], "") for w in ("pass", "fail") if w in b)
        note = f'<p class="note">{R["note"]}</p>' if R.get("note") else ""
        body.append(
            f'<section class="rule" id="{R["id"]}"><h3>{R["id"]} <span class="st">{html.escape(meta["statement"])}</span></h3>'
            f'<p class="checks">{R["checks"]}</p>'
            f'<p class="src">device: {R["device"]["about"]} &middot; cost model: {b.get("model", "corrected")} &middot; sources: {_sources(meta["sources"])}</p>'
            f'{note}<div class="pair">{cards}</div></section>')
    return PAGE.format(title="Rules - QCCD studio", style=STYLE, extra_css="main{max-width:1120px}",
                       body="\n".join(body) + EXAMPLE_JS)


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
        m = machine(R["device"])
        entry: dict = {"model": R.get("model", "corrected")}
        for which in ("pass", "fail"):
            if which not in R:
                continue
            ex = R[which]
            model = ex.get("model") or R.get("model", "corrected")
            rel = f"rules/ex/{R['id']}_{which}.html"
            r = build_example(ex, m, model, out / rel, kicker=f"RULE {R['id']}",
                              headline=f"{R['id']}: {'passes' if which == 'pass' else 'fails'}" if not ex.get("expect") else f"{R['id']}: {ex.get('tag') or ex['expect']}",
                              lede=R["checks"], focus=R["id"], check_metrics=R.get("check_metrics", False))
            put(rel, (out / rel).read_text(encoding="utf-8"), 2, "rules", app=True, extra=HASH_JS)
            entry[which] = {**r, "page": f"ex/{R['id']}_{which}.html", "model": model}
        rules[R["id"]] = entry
    return lang, rules


def doc_page(name: str, d: dict) -> str:
    body = (f'<p class="sub"><a href="../../learn/">Learn</a> &rsaquo; reference &middot; '
            f'<a href="{REPO}/blob/main/docs/{name}.md">docs/{name}.md</a></p>'
            f'<ul class="toc">{d["toc"]}</ul>{d["body"]}')
    return PAGE.format(title=f'{html.escape(d["title"])} - QCCD studio', style=STYLE, extra_css="", body=body)


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
    for t in ts:
        index.append({"t": t["title"], "d": f"leaderboard · {len(t['rows'])} designs", "u": f"board/{t['id']}/", "k": "task"})
        for r in t["rows"]:
            if r.get("status") == "ok" and r.get("page"):
                index.append({"t": r.get("short") or r["key"], "d": f"{t['title']} · {r.get('family', '')}",
                              "u": f"board/{t['id']}/{r['page']}", "k": "entry"})
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
    put("studio.html", studio.read_text(encoding="utf-8"), 0, None, app=True, extra=HASH_JS)

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
    lang, rules = build_examples(out, put)
    put("language/index.html", language_page(lang), 1, "language")
    put("rules/index.html", rules_page(rules), 1, "rules")
    print(f"  language     {len(lang)} statements, rules {len(rules)} with {sum(len([w for w in ('pass', 'fail') if w in e]) for e in rules.values())} example pages")

    put("board/index.html", board_index(ts), 1, "board")
    n_pages = 0
    for t in ts:
        src = t["seed_dir"]
        if not (src / "index.html").exists():
            print(f"  {t['id']:12s} no seed pages under {src}")
            continue
        dst = out / "board" / t["id"]
        dst.mkdir(parents=True, exist_ok=True)
        put(f"board/{t['id']}/index.html", (src / "index.html").read_text(encoding="utf-8"), 2, "board",
            extra=FOOTER_BLOCK)
        for name in ("manifest.json", "rows.json"):
            if (src / name).exists():
                shutil.copy2(src / name, dst / name)
        for r in t["rows"]:
            if r.get("status") != "ok" or not r.get("page"):
                continue
            put(f"board/{t['id']}/{r['page']}", (src / r["page"]).read_text(encoding="utf-8"), 2, "board",
                app=True, extra=HASH_JS)
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
