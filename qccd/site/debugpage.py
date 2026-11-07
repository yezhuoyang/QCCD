"""The Debug page (`/debug/`): any program on the website, step by step, checked by rules.

The page hosts a Studio page -- a leaderboard entry, a Reproduce run, a compiled example,
the reader's own design, or a file from their computer -- in a frame, unchanged, and drives
it from beside it: forward and back one step, watched ions and places, breakpoints, and the
rule language's verdicts drawn on the picture as red crosses where they fail.

Nothing in the Studio is edited for it, so no page's stamp moves and every page already on
the site can be debugged as it is.  The rules are judged by the grader's own Python
(`qccd.debug`, `qccd.ruleset`), run in the browser by Pyodide, which this module copies onto
the site from a pinned release (checked by its digest) so the page never fetches code from
anywhere but the site itself.

    put(...)                    the page, from qccd/site/debug/{debug.css,debug.js}
    debug/worker.js             the Python worker (a module worker)
    debug/qccd-py.zip           the Python the worker runs: qccd's own packages
    debug/pyodide/*             the runtime, five files

`python -m qccd.site.debugpage -o DIR --pages SITE` builds the section alone, over the Studio
pages of an existing site build, for a quick look.  See docs/debug.md.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "debug"
ROOT = HERE.parents[1]

PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = (f"https://github.com/pyodide/pyodide/releases/download/{PYODIDE_VERSION}/"
               f"pyodide-core-{PYODIDE_VERSION}.tar.bz2")
PYODIDE_SHA256 = "2abdcc2e35208af406e07724cffa85bc582ced97e9028383ecf5462541393f95"
#: what the worker loads; the rest of the release (a Node launcher, type files) stays out
PYODIDE_FILES = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip",
                 "pyodide-lock.json")
CACHE = ROOT / "out" / "debug-cache"

#: qccd's packages the worker imports: the debugger, the rule language and what it reads,
#: and the Studio's renderer (an uploaded device and programme are drawn as the site draws them)
PY_PACKAGES = ("debug", "ruleset", "verify", "arch", "ir", "cost", "viz", "evalset", "phys", "qec", "codes")
PY_MODULES = ("__init__.py", "api.py", "repo_paths.py")
#: the one device the renderer reads off disk (`qccd.api.DEFAULT_TEMPLATE`)
PY_ARCH = ("ring144_24v",)


# ------------------------------------------------------------------ the runtime

def pyodide_files(cache: Path = CACHE) -> dict[str, bytes]:
    """The runtime's five files, from the pinned release (downloaded once into `cache`,
    and refused if its digest is not the one pinned here)."""
    cache.mkdir(parents=True, exist_ok=True)
    tar = cache / f"pyodide-core-{PYODIDE_VERSION}.tar.bz2"
    if not tar.exists():
        with urllib.request.urlopen(PYODIDE_URL, timeout=120) as r:
            data = r.read()
        tar.write_bytes(data)
    data = tar.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != PYODIDE_SHA256:
        raise SystemExit(f"{tar} has digest {got}, not the pinned {PYODIDE_SHA256}; delete it to fetch again")
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as t:
        for m in t.getmembers():
            name = m.name.split("/")[-1]
            if name in PYODIDE_FILES and m.isfile():
                f = t.extractfile(m)
                if f is not None:
                    out[name] = f.read()
    missing = [n for n in PYODIDE_FILES if n not in out]
    if missing:
        raise SystemExit(f"the Pyodide release lacks {', '.join(missing)}")
    return served(out)


#: Edits that make the release work from any static server.  qccd.academy's nginx (1.18) maps
#: neither `.mjs` nor `.wasm`, and serves both as application/octet-stream: a module worker
#: refuses to import a `.mjs` served so, and Pyodide compiles its wasm with
#: `instantiateStreaming`, which refuses that type and then only logs a warning (the page would
#: wait forever).  So the modules are served as `.js`, and the wasm is compiled from its bytes,
#: whatever type it comes with.  Each edit must match exactly once in the pinned release.
SERVED = {"pyodide.mjs": "pyodide.esm.js", "pyodide.asm.mjs": "pyodide.asm.js"}
PATCHES = {
    "pyodide.mjs": (("pyodide.asm.mjs", "pyodide.asm.js"),
                    ("n?l=await WebAssembly.instantiateStreaming(n,s):l=await WebAssembly.instantiate(await t,s)",
                     "l=n?await WebAssembly.instantiate(await (await n).arrayBuffer(),s):await WebAssembly.instantiate(await t,s)")),
    "pyodide.asm.mjs": (("pyodide.asm.mjs", "pyodide.asm.js"),),
}


def served(files: dict[str, bytes]) -> dict[str, bytes]:
    """The release's files under the names, and with the edits, the page serves them with."""
    out = {}
    for name, data in files.items():
        for old, new in PATCHES.get(name, ()):
            text = data.decode("utf-8")
            if text.count(old) != 1:
                raise SystemExit(f"Pyodide {PYODIDE_VERSION}'s {name} holds {old[:50]!r} {text.count(old)} times, not once: "
                                 "the release changed; review debugpage.PATCHES")
            data = text.replace(old, new).encode("utf-8")
        out[SERVED.get(name, name)] = data
    return out


def _committed(rel: str) -> bytes | None:
    """A file as HEAD has it (the shared tree's arch/ carries other sessions' edits)."""
    try:
        return subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True,
                              check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def python_zip() -> bytes:
    """qccd's own Python, as the worker unpacks it: the packages in `PY_PACKAGES` (sources,
    rule sets, the Studio's scripts and schema), the top-level modules, and the template
    device the renderer reads."""
    buf = io.BytesIO()
    pkg = ROOT / "qccd"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def add(p: Path):
            rel = p.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))   # same bytes every build
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
        for m in PY_MODULES:
            if (pkg / m).exists():
                add(pkg / m)
        for name in PY_PACKAGES:
            for p in sorted((pkg / name).rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".rules", ".eval", ".js", ".json"):
                    add(p)
        for stem in PY_ARCH:
            rel = f"arch/{stem}.arch.json"
            data = _committed(rel) or (ROOT / rel).read_bytes()
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


# ------------------------------------------------------------------ what the page offers

#: how a page of the site is grouped in the Open list, by where it lives
GROUPS = (("board/", "Leaderboard"), ("reproduce/", "Reproduce"), ("compilation/", "Compilation"),
          ("gadgets/", "Gadgets"), ("rules/", "Rules examples"), ("language/", "Language examples"),
          ("book/", "Tutorial examples"), ("physics/", "Physics"), ("", "Design and tutorial"))
#: what makes a page a Studio page (the Studio's own data block): a board index draws an <svg> too
STUDIO_MARK = '<script id="data" type="application/json">'


def catalogue(app_pages: list[tuple[str, str]], index: list[dict] | None = None) -> list[dict]:
    """Every Studio page the build wrote, as `{t: title, g: group, u: path}`, titled from the
    search index where it has the page and from the page's own <title> otherwise."""
    titles: dict[str, str] = {}
    for e in index or ():
        u = str(e.get("u", "")).split("#")[0]
        if u.endswith(".html") and u not in titles:
            titles[u] = str(e.get("t", ""))
    out, seen = [], set()
    for rel, title in app_pages:
        if rel in seen or rel.startswith("debug/"):
            continue
        seen.add(rel)
        g = next(name for prefix, name in GROUPS if rel.startswith(prefix))
        t = titles.get(rel) or title or rel
        if rel == "studio.html":
            t = "The Design page (a blank canvas, or your own design)"
        out.append({"t": t, "g": g, "u": rel})
    # a title several pages share (every compiled example's is "chain4 - circuit+cooling") says
    # which page it is by its file name too
    seen_t: dict[str, int] = {}
    for r in out:
        seen_t[r["t"]] = seen_t.get(r["t"], 0) + 1
    for r in out:
        if seen_t[r["t"]] > 1:
            r["t"] = f"{r['t']} · {r['u'].rsplit('/', 1)[-1].removesuffix('.html')}"
    order = {name: i for i, (_, name) in enumerate(GROUPS)}
    out.sort(key=lambda r: (order.get(r["g"], 99), r["u"]))
    return out


def page_title(page: str) -> str:
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def rulesets() -> list[dict]:
    """Every rule set the website ships, written out whole, for the Rules tab."""
    from ..ruleset import load, names
    out = []
    for n in names():
        rs = load(n)
        out.append({"name": n, "title": rs.title, "rules": rs.ids, "text": rs.text()})
    out.sort(key=lambda r: (r["name"] != "standard", r["name"]))
    return out


#: a few assertions to start from, in the rule language, offered in the Rules tab
EXAMPLES = (
    ("Every site holds at most one ion",
     "assert \"one ion per site\":\nfor all s in sites, t in instants: occupancy(s, t) <= 1"),
    ("An ion never stops in a junction",
     "assert \"no ion rests in a junction\":\nfor all i in ions, t in instants: not (position(i, t) in junctions)"),
    ("A named site is never over capacity",
     "assert \"C0 within capacity\":\nfor all t in instants: occupancy(\"C0\", t) <= capacity(\"C0\")"),
    ("Every two-ion gate starts cool",
     "assert \"cool gates\":\nfor all g in two_qubit_gates, i in ions(g): heat(i, start(g)) <= heat_budget()"),
    ("A named ion never visits a named site",
     "assert \"q0 stays out of C3\":\nfor all t in instants: position(\"q0\", t) != \"C3\""),
)


def config(cat: list[dict], root: str = "../") -> dict:
    from ..debug.guide import guide
    api = json.loads((SRC / "debug_api.json").read_text(encoding="utf-8"))
    return {"root": root, "catalogue": cat, "rulesets": rulesets(),
            "examples": [{"t": t, "text": x} for t, x in EXAMPLES],
            "guide": guide(),
            "python": {"version": PYODIDE_VERSION, "worker": "worker.js"},
            "verbs": sorted(api["verbs"])}


#: the Debug page's frame; the toolbar, the panels and the timeline are debug.js's own (TOOLS_HTML,
#: SIDE_HTML, TIME_HTML), the same controls a Studio page's panel carries
BODY = """<div id="dbg">
<header id="dbgBar">
  <button id="dOpen" type="button" data-hint="debug:open">Open&hellip;</button>
  <span id="dSrc" class="q-src" data-hint="debug:source">no program open</span>
  <span class="q-sep"></span>
  <span id="dTools" class="q-tools"></span>
</header>
<div id="dbgBody">
  <section id="dbgStage">
    <iframe id="dFrame" title="the program, in the Studio"></iframe>
    <div id="dStart"></div>
    <div id="dMsg" role="status"></div>
  </section>
  <div id="dSplit" data-hint="debug:split" title="drag to resize"></div>
  <aside id="dbgSide"></aside>
</div>
<div id="dTime" data-hint="debug:timeline"></div>
</div>"""


#: What every Studio page of the site carries (the site build appends it, as it does the hash
#: router): a Debug button beside the Studio's transport, and the bar's Debug link, both of which
#: open the debugger HERE, beside the program the reader is looking at -- no other page, no reload,
#: the reader's own unsaved design included.  The debugger itself (debug/inpage.js, inpage.css,
#: config.json) loads the first time it is opened.  An agent's `debug` verbs work on the page
#: before then: each opens the panel and runs.  Nothing happens inside a frame (the Debug page's
#: own, an embedded example) or on an embed.
LOADER = r"""<script id="qdbg-loader">
(function(){
  if(window.self !== window.top || /(^|[#&])embed(&|$)/.test(location.hash)) return;
  var nav = document.getElementById('sitenav'), brand = nav && nav.querySelector('.sn-brand');
  if(!brand || !document.getElementById('svg')) return;
  var ROOT = brand.getAttribute('href') || './', VERBS = __VERBS__, loading = null;
  function load(){
    if(window.QCCD_DEBUG_INPAGE) return Promise.resolve(window.QCCD_DEBUG_INPAGE);
    if(loading) return loading;
    loading = fetch(ROOT + 'debug/config.json').then(function(r){
      if(!r.ok) throw new Error('the debugger is not on this site');
      return r.json();
    }).then(function(cfg){
      cfg.host = 'inpage'; cfg.root = ROOT; cfg.python = cfg.python || {};
      cfg.python.worker = ROOT + 'debug/worker.js';
      window.QCCD_DEBUG_CONFIG = cfg;
      return new Promise(function(ok, no){
        var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = ROOT + 'debug/inpage.css';
        document.head.appendChild(l);
        var s = document.createElement('script'); s.src = ROOT + 'debug/inpage.js';
        s.onload = function(){ ok(window.QCCD_DEBUG_INPAGE); };
        s.onerror = function(){ no(new Error('the debugger did not load')); };
        document.head.appendChild(s);
      });
    });
    loading.catch(function(){ loading = null; });
    return loading;
  }
  function toggle(e){
    if(e){ e.preventDefault(); e.stopPropagation(); }
    load().then(function(D){ return D.toggle(); }).catch(function(err){ if(window.console) console.warn(err); });
  }
  function start(){
    var bar = document.querySelector('#stagebar .ctrl');
    if(bar && !document.getElementById('qdbgBtn')){
      var b = document.createElement('button');
      b.id = 'qdbgBtn'; b.type = 'button'; b.className = 'tgl'; b.setAttribute('aria-pressed', 'false');
      b.setAttribute('data-hint', 'debug:enter'); b.textContent = 'Debug';
      b.title = 'debug this program here: step it forward and back, watch ions and places, check rules (failures are crossed out in red)';
      b.addEventListener('click', toggle);
      var after = document.getElementById('tLabels');
      if(after && after.parentNode === bar) bar.insertBefore(b, after.nextSibling); else bar.appendChild(b);
    }
    var link = document.getElementById('sn-debug');
    if(link) link.addEventListener('click', toggle);
    if(/^#debug(&|$)/.test(location.hash)) load().then(function(D){ return D.enter(); }).catch(function(){});
  }
  window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, { 'debug:enter': { t: 'Debug',
    d: 'debug this program here, beside it: step forward and back, watch ions and places, check rules and your own assertions, see where they fail' } });
  if(!window.QCCD_DEBUG){
    var stub = {};
    VERBS.forEach(function(v){ stub[v] = function(a){ return load().then(function(D){
      return v === 'about' || v === 'sources' || v === 'open' || v === 'link' ? null : D.enter();
    }).then(function(){ return window.QCCD_DEBUG[v](a); }); }; });
    window.QCCD_DEBUG = stub;
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
</script>"""


def loader() -> str:
    """The loader with the declared verbs written in (an agent's verbs work before the panel opens)."""
    api = json.loads((SRC / "debug_api.json").read_text(encoding="utf-8"))
    return LOADER.replace("__VERBS__", json.dumps(sorted(api["verbs"])))


#: The Debug button in a page of the person's workspace (its Studio, a run's page), where the
#: debugger itself is already on the page (`workspace_inject`): the same button the site's loader
#: puts beside the Studio's transport, and `#debug` in the address opens the panel.
WORKSPACE_BUTTON = r"""<script id="qdbg-workspace">
(function(){
  if(window.self !== window.top) return;                 // a run framed side by side: no panel
  function toggle(e){
    if(e){ e.preventDefault(); e.stopPropagation(); }
    window.QCCD_DEBUG_INPAGE.toggle().catch(function(err){ if(window.console) console.warn(err); });
  }
  function start(){
    // debug.js boots on DOMContentLoaded too, and was there first
    if(!window.QCCD_DEBUG_INPAGE || !document.getElementById('svg')) return;
    var bar = document.querySelector('#stagebar .ctrl');
    if(bar && !document.getElementById('qdbgBtn')){
      var b = document.createElement('button');
      b.id = 'qdbgBtn'; b.type = 'button'; b.className = 'tgl'; b.setAttribute('aria-pressed', 'false');
      b.setAttribute('data-hint', 'debug:enter'); b.textContent = 'Debug';
      b.title = 'debug this program here: step it forward and back, see each rule at work at every step, ask your agent to review it';
      b.addEventListener('click', toggle);
      var after = document.getElementById('tLabels');
      if(after && after.parentNode === bar) bar.insertBefore(b, after.nextSibling); else bar.appendChild(b);
    }
    if(/^#debug(&|$)/.test(location.hash)) window.QCCD_DEBUG_INPAGE.enter().catch(function(){});
  }
  window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, { 'debug:enter': { t: 'Debug',
    d: 'debug this program here, beside it: step forward and back, see each rule at work at every step, check your own assertions, have your agent review it' } });
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
</script>"""


def workspace_inject(endpoint: str = "/api/debug/web") -> str:
    """The debugger in a page of the person's workspace (its Studio, a run's page): the panel's code
    on the page itself (the workspace's pages load nothing from elsewhere), and its rule language
    the workspace's own Python, behind `endpoint` -- the same module the site's pages run in the
    browser, so the answers are the same, with nothing to download."""
    cfg = config([], root="/")
    cfg.update(host="inpage", python={"backend": "service", "endpoint": endpoint})
    css = ((SRC / "panel.css").read_text(encoding="utf-8") + (SRC / "inpage.css").read_text(encoding="utf-8")
           + "\n:root{--qdbg-top:0px}\n")
    blob = json.dumps(cfg, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    js = (SRC / "debug.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    return ("<style id=\"qdbg-css\">\n" + css + "</style>\n<script>window.QCCD_DEBUG_CONFIG=" + blob + ";</script>\n"
            "<script>\n" + js + "\n</script>\n" + WORKSPACE_BUTTON + "\n")


def page(PAGE: str, STYLE: str, cfg: dict) -> str:
    css = (SRC / "debug.css").read_text(encoding="utf-8") + (SRC / "panel.css").read_text(encoding="utf-8")
    js = (SRC / "debug.js").read_text(encoding="utf-8")
    blob = json.dumps({**cfg, "host": "frame"}, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    body = (BODY + "\n<script>window.QCCD_DEBUG_CONFIG=" + blob + ";</script>\n<script>\n" + js
            + "\n</script>")
    out = PAGE.format(title="Debug - QCCD studio", style=STYLE, extra_css=css, body=body)
    # the sheet is the whole viewport: no footer under an app
    return out.replace("__FOOTER__", "")


def write(out: Path, put, PAGE: str, STYLE: str, app_pages: list[tuple[str, str]],
          index: list[dict] | None = None) -> int:
    """The section: the page (through the site's `put`), what a Studio page loads when its Debug
    button is pressed (inpage.js, inpage.css, config.json), the worker, the Python and the runtime."""
    d = out / "debug"
    (d / "pyodide").mkdir(parents=True, exist_ok=True)
    cat = catalogue(app_pages, index)
    cfg = config(cat)
    put("debug/index.html", page(PAGE, STYLE, cfg), 1, "debug", app=True)
    text = lambda name: (SRC / name).read_text(encoding="utf-8")
    (d / "inpage.js").write_text(text("debug.js"), encoding="utf-8", newline="\n")
    (d / "inpage.css").write_text(text("panel.css") + text("inpage.css"), encoding="utf-8", newline="\n")
    (d / "config.json").write_text(json.dumps({k: v for k, v in cfg.items() if k != "root"}, ensure_ascii=False,
                                              separators=(",", ":")), encoding="utf-8", newline="\n")
    (d / "worker.js").write_text(text("worker.js"), encoding="utf-8", newline="\n")
    (d / "qccd-py.zip").write_bytes(python_zip())
    for name, data in pyodide_files().items():
        (d / "pyodide" / name).write_bytes(data)
    return len(cat)


def with_loader(page: str) -> str:
    """A Studio page with the loader, as the site build writes it (once; before </body>)."""
    if 'id="qdbg-loader"' in page or STUDIO_MARK not in page:
        return page
    j = page.rindex("</body>")
    return page[:j] + loader() + page[j:]


def index_entries() -> list[dict]:
    return [{"t": "Debug", "d": "any program step by step: watch ions and places, check rules and your own "
                                 "assertions, see where they fail", "u": "debug/", "k": "part"}]


def main(argv=None) -> int:
    """Build the section alone over an existing site build (`--pages`), for a quick look."""
    import argparse
    from . import build as B
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--pages", help="a built site whose Studio pages the Open list offers (copied in)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    app_pages: list[tuple[str, str]] = []
    idx_json, index = "[]", None
    if a.pages:
        src = Path(a.pages)
        home = src / "index.html"
        m = re.search(r"INDEX = (\[.*?\]);\n", home.read_text(encoding="utf-8")) if home.exists() else None
        if m:                                      # the site's own search index, so the bar searches it
            idx_json, index = m.group(1), json.loads(m.group(1))
        for p in sorted(src.rglob("*.html")):
            rel = p.relative_to(src).as_posix()
            if rel.startswith("debug/"):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if STUDIO_MARK in text:
                dst = out / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(with_loader(text), encoding="utf-8", newline="")
                app_pages.append((rel, page_title(text)))

    def put(rel, pg, depth, active, app=False, extra=""):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        pg = B.with_nav(pg, depth, active, idx_json, app=app, extra=extra)
        p.write_text(pg.replace("__FOOTER__", "").replace("__SITEROOT__", "../" * depth), encoding="utf-8", newline="")
    n = write(out, put, B.PAGE, B.STYLE, app_pages, index)
    print(f"wrote {out / 'debug'} ({n} programs offered)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
