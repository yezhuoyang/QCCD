"""RSA-2048 at d = 11 as a page of the website: gadgets/rsa2048/ (docs/FULLSTACK_PLAN.md S9).

The page gives the answer -- how long Gidney 2025, as FormalRV writes it, takes to factor RSA-2048
on the stationary-tile QCCD machine at d = 11 -- what the number rests on, and one prime played on
the machine's floorplan step by step (`web/rsa.js`): each step's gadgets are fetched when the
clock nears it and dropped behind it, so a reader's browser holds a few steps, never the 282
million gadgets of the prime.

    pack(run, dst, logs, rest=None)  the page's data from `tools/lattice_prime.py --frames` runs
    page(dst)                        the page's body, from the packed data
    build(base, put, data)           gadgets/rsa2048/ into a site (the data copied beside it)
"""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

__all__ = ["KEY", "pack", "page", "build", "index_entry"]

KEY = "rsa2048"
WEB = Path(__file__).resolve().parent / "web"
#: the prime's parts, in program order, and the colour each is drawn in on the clock's strip
PARTS = (("gLoop1", "#8fb3e8"), ("gLoop2", "#7cc4a4"), ("gLoop3", "#b39ddb"), ("gLoop4", "#f4a7a7"),
         ("gUnloop3", "#c3a5f0"), ("gUnloop2", "#9fd8bd"))
PRIMES = 21_640
#: programs other than merges, each checked at d = 11 when it was built (FULLSTACK_PLAN S9)
OTHER = {"ht": "the H̄ gadget (Gehér et al. 2024, on our tile): every rule and R10, 679.8 ms",
         "prep Y": "|Ȳ> in place (Gidney's circuit): every rule and R10, 43.9 ms",
         "prep T": "the T state's corner injection: every rule and R10 on its Clifford twin",
         "prep Z": "a tile's preparation in Z̄: every rule and R10", "prep X": "a tile's preparation in X̄: every rule and R10",
         "read Z": "a tile's readout in Z̄: every rule and R10", "read X": "a tile's readout in X̄: every rule and R10"}


def _label(raw: str) -> tuple[str, str]:
    """(part, a reader's label) for a step label like gOnePrime/gLoop3/j2/k0/gLoop3Step."""
    parts = raw.split("/")[1:]
    part = parts[0]
    if part == "gLoop1":
        return part, f"gLoop1, iteration {int(parts[1][4:]) + 1} of 214"
    idx = [m for m in (re.fullmatch(r"(u2|k2|j|k|u)(\d+)", p) for p in parts[1:]) if m]
    where = ", ".join(f"{m[1]} = {m[2]}" for m in idx)
    what = "the work between steps" if parts[-1] == "between" else ("its last work" if parts[-1] == "end" else
                                                                     parts[-1])
    return part, f"{part}, {what}" + (f" ({where})" if where else "")


def _text(shape, dur_us: int, d11) -> str:
    kind = shape[0]
    if kind == "merge":
        letter, pats, cor = shape[1], shape[2], shape[3]
        what = (f"a {letter}̄{letter}̄ merge of {len(pats)} patches" + (" face to face" if not cor else
                f" through a corridor of {len(cor)} tiles"))
        if d11:
            ions, failed, ms, r10 = d11
            return what + (f", 138.3 ms; checked at d = 11: {ions:,} ions, every rule, R10" if not failed and r10
                           else f"; at d = 11 it FAILS {failed}")
        return what + ", 138.3 ms; its check at d = 11 is still running"
    key = " ".join(str(x) for x in shape)
    return f"{key}, {dur_us / 1000:g} ms; " + OTHER.get(key, "checked at d = 11 when it was built")


def pack(run, dst, logs, rest=None) -> dict:
    """The page's data in `dst` from a `lattice_prime.py --frames` run of one prime (`run`), the
    d = 11 logs of `tools/lattice_check.py` (`logs`) and, if given, a `--rest` run's results:
    meta.json, programs.json, layout.json and frames/."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from lattice_check import parse_log
    run, dst = Path(run), Path(dst)
    (dst / "frames").mkdir(parents=True, exist_ok=True)
    checked = {}
    for lg in logs:
        checked.update(parse_log(Path(lg).read_text(encoding="utf-8").splitlines()))
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "steps").glob("[0-9][0-9][0-9].json"))]
    programs, ids, steps, t = [], {}, [], 0.0
    for i, r in enumerate(rows):
        local = []
        for s, _, dur in r["shapes"]:
            key = json.dumps(s)
            if key not in ids:
                tup = (s[0], s[1], tuple((tuple(a), b) for a, b in s[2]), tuple(tuple(c) for c in s[3])) \
                    if s[0] == "merge" else tuple(s)
                ids[key] = len(programs)
                programs.append({"id": len(programs), "kind": s[0], "shape": s, "dur_us": dur,
                                 "text": _text(s, dur, checked.get(tup)),
                                 "d11": "pass" if s[0] != "merge" or (tup in checked and not checked[tup][1] and checked[tup][3])
                                 else ("fail" if tup in checked else "running")})
            local.append(ids[key])
        part, label = _label(r["label"])
        steps.append({"label": label, "part": part, "t0": round(t, 6), "span": r["span_us"] / 1e6,
                      "gadgets": r["gadgets"], "programs": local, "check": "clean" if not r["n_bad"] else r["check"]})
        t += r["span_us"] / 1e6
        src = run / "frames" / f"{i:03d}.bin.gz"
        if not src.exists() and r.get("same_as"):     # the same schedule as an earlier step: its frame
            src = run / "frames" / r["same_as"].replace(".json", ".bin.gz")
        shutil.copyfile(src, dst / "frames" / f"{i:03d}.bin.gz")
    parts = []
    for name, color in PARTS:
        mine = [s for s in steps if s["part"] == name]
        if mine:
            parts.append({"name": name, "color": color, "steps": len(mine), "t0": mine[0]["t0"],
                          "span": round(sum(s["span"] for s in mine), 6)})
    layout = json.loads((run / "frames" / "layout.json").read_text(encoding="utf-8"))
    meta = {"prime_s": round(t, 6), "primes": PRIMES, "tiles": layout["tiles"], "ions_per_tile": 288,
            "factories": len(layout["factories"]), "steps": steps, "parts": parts,
            "gadgets": sum(s["gadgets"] for s in steps)}
    if rest:
        rr = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(rest) / "steps").glob("*.json"))]
        meta["rest"] = {"steps": len(rr), "s": round(sum(r["span_us"] for r in rr) / 1e6, 6),
                        "clean": all(not r["n_bad"] for r in rr)}
    (dst / "meta.json").write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    (dst / "programs.json").write_text(json.dumps(programs, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (dst / "layout.json").write_text(json.dumps(layout, separators=(",", ":")), encoding="utf-8")
    return meta


def _years(s: float) -> float:
    return s / (365.25 * 86400)


CSS = """
.rsa-answer{font-size:30px;font-weight:650;margin:6px 0 2px}
#rsa{margin:18px 0 8px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:10px 12px}
#rsa-map{width:100%;height:46px;display:block;border-radius:4px;cursor:pointer;margin:0 0 8px}
#rsa-canvas{width:100%;height:330px;display:block;border-radius:6px;cursor:grab;touch-action:none}
#rsa-strip{width:100%;height:14px;display:block;margin:8px 0 2px;border-radius:3px;cursor:pointer}
.rsa-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 0;font-size:13px}
.rsa-bar input[type=range]{flex:1 1 220px}
.rsa-bar button,.rsa-bar select{font:inherit;font-size:13px;padding:3px 9px}
.rsa-read{font-size:12.5px;color:var(--ink2);margin:6px 0 0;min-height:1.3em}
.rsa-key{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink2);margin:8px 0 0}
.rsa-key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
"""

HINTS = {"rsa-play": ("Play", "plays the prime on the machine, step after step on one clock"),
         "rsa-speed": ("Speed", "how many seconds of machine time pass each second"),
         "rsa-time": ("Time in the prime", "moves the clock to any instant of the prime"),
         "rsa-part": ("Part", "moves the clock to the start of one part of the prime"),
         "rsa-strip": ("The prime's steps", "the parts of the prime along its time; a click moves the clock there"),
         "rsa-in": ("Zoom in", "shows a smaller part of the floorplan, larger"),
         "rsa-out": ("Zoom out", "shows more of the floorplan"),
         "rsa-fit": ("Busiest part", "shows the machine's full height at its busiest part, the crossbar"),
         "rsa-canvas": ("The machine", "the floorplan; a click names the tile and what is at work on it now"),
         "rsa-map": ("The whole machine", "all of the floorplan, the part shown below outlined; a click shows that part")}


def page(dst) -> str:
    """The page's body from packed data in `dst`."""
    meta = json.loads((Path(dst) / "meta.json").read_text(encoding="utf-8"))
    progs = json.loads((Path(dst) / "programs.json").read_text(encoding="utf-8"))
    prime = meta["prime_s"]
    rest = meta.get("rest")
    total = PRIMES * prime + (rest["s"] if rest else 0.0)
    merges = [p for p in progs if p["kind"] == "merge"]
    passed = sum(p["d11"] == "pass" for p in merges)
    ions = meta["tiles"] * meta["ions_per_tile"]
    e = html.escape
    rows = "".join(f"<tr><td>{e(p['name'])}</td><td>{p['steps']}</td><td>{p['span']:,.2f} s</td>"
                   f"<td>{p['span'] / p['steps']:,.2f} s</td></tr>" for p in meta["parts"])
    hints = json.dumps({k: {"t": a, "d": b} for k, (a, b) in HINTS.items()}, ensure_ascii=False)
    js = (WEB / "rsa.js").read_text(encoding="utf-8")
    key = "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c in
                  (("merge", "#2e6fd8"), ("preparation", "#12a150"), ("readout", "#e08a00"), ("H̄", "#8b3fd0"),
                   ("factory", "#e7e9ee"), ("dlog", "#7cc4a4"), ("help", "#b39ddb"), ("acc", "#f4a7a7"),
                   ("res", "#f2cf86"), ("pp", "#8fb3e8"), ("addr", "#5b7fc7"), ("exponent", "#d6dbe4")))
    still = len(merges) - passed
    return f"""
<p class="sub"><a href="../">Gadgets</a> &rsaquo; RSA-2048 at d = 11</p>
<h1>Factoring RSA-2048 at d = 11</h1>
<p class="rsa-answer">{_years(total):.2f} years</p>
<p>of machine time for Gidney's 2025 algorithm, as FormalRV writes it, on the stationary-tile QCCD
machine at code distance 11: {meta['tiles']:,} tiles ({ions / 1e6:.1f} million ions), {meta['factories']}
CCZ factories. One prime is scheduled and checked whole -- {len(meta['steps'])} steps, {prime:,.2f} s,
<b>concrete</b>; the program is that prime {PRIMES:,} times, back to back (every prime has the same
schedule, so this is <b>exact</b>, not an estimate){f", plus {rest['s'] / 3600:.2f} h of work done once ({rest['steps']:,} steps, concrete)" if rest else ""}.</p>
<div class="gstat">
<div><b>{prime / 3600:.2f} h</b>one prime</div>
<div><b>{PRIMES:,}</b>primes</div>
<div><b>{meta['gadgets'] / 1e6:.0f} million</b>gadgets in a prime</div>
<div><b>{passed} of {len(merges)}</b>merge programs checked at d = 11{f" ({still} running)" if still else ""}</div>
</div>

<div id="rsa">
  <canvas id="rsa-map" data-hint="rsa-map" aria-label="the whole machine"></canvas>
  <canvas id="rsa-canvas" data-hint="rsa-canvas" aria-label="the machine's floorplan"></canvas>
  <canvas id="rsa-strip" data-hint="rsa-strip" aria-label="the prime's steps along its time"></canvas>
  <div class="rsa-bar">
    <button id="rsa-play" data-hint="rsa-play">Play</button>
    <select id="rsa-speed" data-hint="rsa-speed" aria-label="speed">
      <option value="1">1 s a second</option><option value="10" selected>10 s a second</option>
      <option value="100">100 s a second</option></select>
    <input id="rsa-time" data-hint="rsa-time" type="range" min="0" step="0.001" value="0" aria-label="time in the prime">
    <select id="rsa-part" data-hint="rsa-part" aria-label="part of the prime"><option value="" disabled selected>Go to a part</option></select>
    <button id="rsa-in" data-hint="rsa-in" title="zoom in">+</button>
    <button id="rsa-out" data-hint="rsa-out" title="zoom out">&minus;</button>
    <button id="rsa-fit" data-hint="rsa-fit">Busiest part</button>
  </div>
  <p class="rsa-read" id="rsa-step"></p>
  <p class="rsa-read" id="rsa-clock"></p>
  <p class="rsa-read" id="rsa-pick">Click a tile to see what is at work on it.</p>
  <div class="rsa-key">{key}</div>
</div>
<p class="muted">The player fetches each step as the clock nears it and lets it go once the clock
has passed, so the browser holds a few steps at a time; the clock waits for a step still on its way.
A gadget shorter than the eye can follow stays drawn for a twentieth of a second.</p>

<h2>One prime</h2>
<table><tr><th>part</th><th>steps</th><th>time</th><th>a step</th></tr>{rows}
<tr><td><b>the prime</b></td><td>{len(meta['steps'])}</td><td><b>{prime:,.2f} s</b></td><td></td></tr></table>

<h2>What the number rests on</h2>
<ul class="steps">
<li>Every step is a schedule of compiled programs on the floorplan, checked: no two gadgets on a tile at
once, every block's instructions in program order, every guard decoded before it is read. Steps run
end to end, each from the board at rest.</li>
<li>Each distinct program -- {len(merges)} merges, and {len(progs) - len(merges)} preparations, readouts and the
H̄ -- is compiled for the machine at d = 11 and checked against every rule and R10. A click on a
tile names the program at work on it and its check.</li>
<li>The machine's times are its compiled programs': a merge 138.3 ms (25 rounds), H̄ 679.8 ms, a CCZ
state 11.5 s from a factory. Decoding is taken to take one round (assumed). No gate is reordered and
FormalRV's program is not changed.</li>
<li>Not built: the phase gradient's 29 small-angle rotations (k = 4 .. 32), which need a resource
state no gadget makes yet. They run once, for seconds.</li>
</ul>
<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {{}}, {hints});</script>
<script>{js}</script>
"""


def build(base, put, data) -> None:
    """gadgets/rsa2048/ under the site section `base`, its data copied from `data` (`pack`)."""
    from ..site.build import PAGE, STYLE
    from .site import EXTRA_CSS, SECTION
    where = Path(base) / KEY
    if (where / "data").exists():
        shutil.rmtree(where / "data")
    shutil.copytree(data, where / "data")
    put(f"{SECTION}/{KEY}/index.html",
        PAGE.format(title="Factoring RSA-2048 at d = 11 - QCCD studio", style=STYLE,
                    extra_css=EXTRA_CSS + CSS, body=page(data)), 2, SECTION)


def standalone(out, data, index_json: str) -> Path:
    """Only gadgets/rsa2048/ (and its data) into the site directory `out`, wrapped as the site
    wraps its pages: for a deploy that rebuilds nothing else."""
    from ..site.build import FOOTER, with_nav
    from .site import SECTION
    idx = index_json.replace("</", "<\\/")

    def put(rel, page_, depth, active, app=False, extra=""):
        p = Path(out) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        page_ = with_nav(page_, depth, active, idx, app=app, extra=extra)
        p.write_text(page_.replace("__FOOTER__", FOOTER).replace("__SITEROOT__", "../" * depth),
                     encoding="utf-8", newline="")
    build(Path(out) / SECTION, put, data)
    return Path(out) / SECTION / KEY / "index.html"


def index_entry() -> dict:
    return {"t": "Factoring RSA-2048 at d = 11", "d": "how long Gidney 2025 takes on the stationary-tile "
            "QCCD machine at d = 11: one prime scheduled and checked whole, played step by step",
            "u": f"gadgets/{KEY}/", "k": "gadgets"}
