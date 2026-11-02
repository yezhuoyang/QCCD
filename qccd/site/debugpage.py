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
          ("rules/", "Rules examples"), ("language/", "Language examples"), ("physics/", "Physics"),
          ("", "Design and tutorial"))


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
    api = json.loads((SRC / "debug_api.json").read_text(encoding="utf-8"))
    return {"root": root, "catalogue": cat, "rulesets": rulesets(),
            "examples": [{"t": t, "text": x} for t, x in EXAMPLES],
            "python": {"version": PYODIDE_VERSION, "worker": "worker.js"},
            "verbs": sorted(api["verbs"])}


BODY = """<div id="dbg">
<header id="dbgBar">
  <button id="dOpen" type="button" data-hint="debug:open">Open&hellip;</button>
  <span id="dSrc" class="src" data-hint="debug:source">no program open</span>
  <span class="sep"></span>
  <span class="tp">
    <button id="dFirst" type="button" data-hint="debug:first" title="first step (Home)" aria-label="first step">&#x23EE;</button>
    <button id="dBack" type="button" data-hint="debug:back" title="back one step (Left arrow)" aria-label="back one step">&#x25C0;</button>
    <label class="stepbox">Step <input id="dStep" type="number" min="1" value="1" data-hint="debug:step"
      aria-label="step number"> <span id="dOf">/ 0</span></label>
    <button id="dFwd" type="button" data-hint="debug:forward" title="forward one step (Right arrow)" aria-label="forward one step">&#x25B6;</button>
    <button id="dLast" type="button" data-hint="debug:last" title="last step (End)" aria-label="last step">&#x23ED;</button>
  </span>
  <span class="sep"></span>
  <button id="dPrevFail" type="button" class="fail" data-hint="debug:prevfail" title="previous step where a rule fails (Shift+Left)">&#x2717;&#x25C0;</button>
  <button id="dNextFail" type="button" class="fail" data-hint="debug:nextfail" title="next step where a rule fails (Shift+Right)">&#x25B6;&#x2717;</button>
  <button id="dPrevEv" type="button" class="ev" data-hint="debug:prevevent" title="previous step where something you watch changes (Alt+Left)">&#x25C6;&#x25C0;</button>
  <button id="dNextEv" type="button" class="ev" data-hint="debug:nextevent" title="next step where something you watch changes (Alt+Right)">&#x25B6;&#x25C6;</button>
  <button id="dContBack" type="button" data-hint="debug:continueback" title="run backward to the previous breakpoint">&#x25C0;&#x25C0;</button>
  <button id="dCont" type="button" class="p" data-hint="debug:continue" title="run forward to the next breakpoint (F8)">Continue &#x25B6;&#x25B6;</button>
  <span class="grow"></span>
  <button id="dIsolate" type="button" class="tgl" aria-pressed="false" data-hint="debug:isolate">Isolate</button>
  <button id="dTrails" type="button" class="tgl on" aria-pressed="true" data-hint="debug:trails">Trails</button>
  <button id="dCrosses" type="button" class="tgl on" aria-pressed="true" data-hint="debug:crosses">Crosses</button>
</header>
<div id="dbgBody">
  <section id="dbgStage">
    <iframe id="dFrame" title="the program, in the Studio"></iframe>
    <div id="dStart"></div>
    <div id="dMsg" role="status"></div>
  </section>
  <div id="dSplit" data-hint="debug:split" title="drag to resize"></div>
  <aside id="dbgSide">
    <nav id="dTabs" role="tablist">
      <button type="button" role="tab" data-tab="state" data-hint="debug:tab:state" aria-selected="true">State</button>
      <button type="button" role="tab" data-tab="watch" data-hint="debug:tab:watch">Watch</button>
      <button type="button" role="tab" data-tab="rules" data-hint="debug:tab:rules">Rules</button>
      <button type="button" role="tab" data-tab="breaks" data-hint="debug:tab:breaks">Breakpoints</button>
      <button type="button" role="tab" data-tab="report" data-hint="debug:tab:report">Report</button>
    </nav>
    <div class="pane" id="pState" data-pane="state"></div>
    <div class="pane" id="pWatch" data-pane="watch" hidden></div>
    <div class="pane" id="pRules" data-pane="rules" hidden></div>
    <div class="pane" id="pBreaks" data-pane="breaks" hidden></div>
    <div class="pane" id="pReport" data-pane="report" hidden></div>
  </aside>
</div>
<div id="dTime" data-hint="debug:timeline"><svg id="dTimeSvg" aria-label="timeline: every step, the steps where rules fail and where what you watch changes"></svg></div>
</div>"""


def page(PAGE: str, STYLE: str, cfg: dict) -> str:
    css = (SRC / "debug.css").read_text(encoding="utf-8")
    js = (SRC / "debug.js").read_text(encoding="utf-8")
    blob = json.dumps(cfg, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    body = (BODY + "\n<script>window.QCCD_DEBUG_CONFIG=" + blob + ";</script>\n<script>\n" + js
            + "\n</script>")
    out = PAGE.format(title="Debug - QCCD studio", style=STYLE, extra_css=css, body=body)
    # the sheet is the whole viewport: no footer under an app
    return out.replace("__FOOTER__", "")


def write(out: Path, put, PAGE: str, STYLE: str, app_pages: list[tuple[str, str]],
          index: list[dict] | None = None) -> int:
    """The section: the page (through the site's `put`), the worker, the Python and the runtime."""
    d = out / "debug"
    (d / "pyodide").mkdir(parents=True, exist_ok=True)
    cat = catalogue(app_pages, index)
    put("debug/index.html", page(PAGE, STYLE, config(cat)), 1, "debug", app=True)
    (d / "worker.js").write_text((SRC / "worker.js").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (d / "qccd-py.zip").write_bytes(python_zip())
    for name, data in pyodide_files().items():
        (d / "pyodide" / name).write_bytes(data)
    return len(cat)


def index_entries() -> list[dict]:
    return [{"t": "Debug", "d": "any program step by step: watch ions and places, check rules and your own "
                                 "assertions, see where they fail", "u": "debug/", "k": "part"}]


def main(argv=None) -> int:
    """Build the section alone over an existing site build (`--pages`), for a quick look."""
    import argparse
    import shutil
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
            if 'id="svg"' in text and "window.EDITOR" not in rel:
                dst = out / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.resolve() != p.resolve():
                    shutil.copy2(p, dst)
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
