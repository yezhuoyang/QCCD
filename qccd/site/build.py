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
"""

APP_CSS = "<style>main{height:calc(100vh - 40px)!important}</style>"

STUDIO_HASH_JS = """<script>
(function(){
  // The site's deep links into the one studio page: #learn opens the course, #learn=B2
  // a lesson, #design is the blank canvas (the default).  Under the test shim there is no
  // `location`, and nothing here runs.
  if(typeof location === 'undefined' || typeof window === 'undefined' || !window.addEventListener) return;
  function apply(){
    var h = (location.hash || '').replace(/^#/, ''), m = /^learn(?:=([A-Za-z]\\d+))?$/.exec(h);
    var dk = document.getElementById('dock'), pl = document.getElementById('paneL');
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
</script>"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{style}{extra_css}</style></head><body>
<main>{body}</main></body></html>"""


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
    n = sum(1 for t in ts for r in t["rows"] if r.get("status") == "ok")
    tiles = "".join(
        f'<a class="tile" href="{u}"><b>{label}</b><span>{blurb}</span></a>'
        for key, label, u, blurb in PARTS)
    return ((HERE / "landing.html").read_text(encoding="utf-8")
            .replace("__TILES__", tiles).replace("__N__", str(n)).replace("__T__", str(len(ts))))


def learn_page(parts: list[dict], less: list[dict], docs: dict, ts: list[dict]) -> str:
    body = ["<h1>Learn</h1>",
            "<p class=\"sub\">One path: the course inside the design tool, then the reference, "
            "then a real design stepped instruction by instruction. Progress is kept in this "
            "browser by the course itself.</p>",
            "<h2>The course</h2>",
            "<p class=\"sub\">Every lesson opens the studio on its exercise; the check runs on the page's own verdicts. "
            "<a href=\"../studio.html#learn\">Open the course</a>.</p>"]
    if not parts:
        body.append('<p class="note">This build carries no course: <code>qccd/viz/js/tutorial.js</code> '
                    'is not in the tree it was built from.</p>')
    for p in parts:
        items = "".join(
            f'<li><span class="id">{L["id"]}</span><a href="../studio.html#learn={L["id"]}">{html.escape(L["title"])}</a>'
            f'<span class="stars" data-lesson="{L["id"]}"></span></li>'
            for L in less if L["part"] == p["id"])
        body.append(f'<div class="part"><h3>Part {p["id"]} &middot; {html.escape(p["title"])}</h3>'
                    f'<ul class="lessons">{items}</ul></div>')
    body.append("<h2>The reference</h2><ul>")
    for name in DOC_NAMES:
        body.append(f'<li><a href="../docs/{name}/">{html.escape(docs[name]["title"])}</a> <code>docs/{name}.md</code></li>')
    body.append("</ul>")
    body.append("<h2>A real design</h2><p class=\"sub\">Every leaderboard entry is a worked example: the executing "
                "instruction is marked in the programme, the circuit statement it discharges beside it, and "
                "<code>#step=N</code> links any step.</p><ul>")
    for t in ts:
        best = [r for r in t["rows"] if _entry_ok(r) and _r10(r)]
        best.sort(key=lambda r: (r.get("numbers") or {}).get("T_jones", 1e9))
        if best:
            b = best[0]
            body.append(f'<li><a href="../board/{t["id"]}/{b["page"]}#step=1">{html.escape(t["title"])}</a> '
                        f'on {html.escape(b.get("short") or b["key"])}: the fastest verified round</li>')
    body.append("</ul>")
    body.append("""<script>
(function(){ var p = null; try { p = JSON.parse(localStorage.getItem('qccd.studio.tutorial') || 'null'); } catch(e){}
  var stars = (p && p.stars) || {}, els = document.querySelectorAll('.stars[data-lesson]');
  for(var i = 0; i < els.length; i++){ var n = stars[els[i].getAttribute('data-lesson')] || 0;
    els[i].textContent = n ? new Array(n + 1).join('\\u2605') : ''; } })();
</script>""")
    return PAGE.format(title="Learn - QCCD studio", style=STYLE, extra_css="", body="\n".join(body))


def board_index(ts: list[dict]) -> str:
    cards = []
    for t in ts:
        rows = t["rows"]
        ok = [r for r in rows if r.get("status") == "ok"]
        ok.sort(key=lambda r: (r.get("numbers") or {}).get("T_jones", 1e9))
        sound = [r for r in ok if _entry_ok(r) and _r10(r)]
        best = sound[0] if sound else None
        refused = [r.get("short") or r["key"] for r in rows if r.get("status") != "ok"]
        lines = "".join(
            f"<tr><td>{html.escape(r.get('short') or r['key'])}"
            f"{'' if _entry_ok(r) else ' <span style=color:#c62828>(rules failed)</span>'}</td>"
            f"<td>{(r.get('numbers') or {}).get('T_jones', float('nan')):.2f} ms</td>"
            f"<td>{(r.get('numbers') or {}).get('ions_per_instruction', float('nan')):.1f}</td>"
            f"<td>{'Lean checker' if _r10(r) else '&ndash;'}</td></tr>" for r in ok[:6])
        cards.append(
            f'<a class="card" href="{t["id"]}/"><h2>{html.escape(t["title"])}</h2>'
            f'<p>{len(ok)} of {len(rows)} designs run it'
            f'{(" &middot; " + str(sum(1 for r in ok if _r10(r))) + " with R10 by the Lean checker") if ok else ""}'
            f'{(" &middot; refused: " + html.escape(", ".join(refused))) if refused else ""}</p>'
            f'<p class="best">fastest verified: <b>{html.escape(best.get("short") or best["key"]) if best else "&ndash;"}</b>'
            f'{(" at %.2f ms" % best["numbers"]["T_jones"]) if best else ""}</p>'
            f'<table><tr><th>design</th><th>round</th><th>ions/instr</th><th>R10</th></tr>{lines}</table></a>')
    body = ("<h1>Leaderboard</h1><p class=\"sub\">One board per task. A task is a fixed circuit, physics package "
            "and cost table; every design on a board ran one syndrome-extraction round of that circuit, was "
            "replayed against the 23 rules and, where it passed, checked by the proved Lean checker (R10). "
            "Open a task for its ranking plot; click a dot there to step the programme in the studio.</p>"
            f'<div class="cards">{"".join(cards)}</div>'
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
        p.write_text(with_nav(page, depth, active, idx, app=app, extra=extra), encoding="utf-8", newline="")

    # the studio, once: the one page Learn and Design both are
    from ..__main__ import main as qccd_main
    studio = out / "studio.html"
    qccd_main(["studio", "-o", str(studio)])
    put("studio.html", studio.read_text(encoding="utf-8"), 0, None, app=True, extra=STUDIO_HASH_JS)

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

    put("board/index.html", board_index(ts), 1, "board")
    n_pages = 0
    for t in ts:
        src = t["seed_dir"]
        if not (src / "index.html").exists():
            print(f"  {t['id']:12s} no seed pages under {src}")
            continue
        dst = out / "board" / t["id"]
        dst.mkdir(parents=True, exist_ok=True)
        put(f"board/{t['id']}/index.html", (src / "index.html").read_text(encoding="utf-8"), 2, "board")
        for name in ("manifest.json", "rows.json"):
            if (src / name).exists():
                shutil.copy2(src / name, dst / name)
        for r in t["rows"]:
            if r.get("status") != "ok" or not r.get("page"):
                continue
            put(f"board/{t['id']}/{r['page']}", (src / r["page"]).read_text(encoding="utf-8"), 2, "board", app=True)
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
