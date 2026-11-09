"""RSA-2048 at d = 11 in Gidney's own compilation, as a page of the website: gadgets/rsa2048/gidney/ (docs/FULLSTACK_PLAN.md S10).

FormalRV's listing of Gidney 2025's program, run as written: every product one lattice-surgery merge under FormalRV's own `merge`
(`qccd.fullstack.scale.gidney_surgery`), the magic states cultivated and turned into |AutoCCZ> in factory slots, the exponent in yoked cold
storage.  The page gives the answer, what it rests on, and one prime played on the floorplan by the player of gadgets/rsa2048/
(`web/rsa.js`): the prime's flat schedule cut into windows, each fetched as the clock nears it, at any level from the machine to every ion.

    page(dst)                          the page's body, from `tools/gidney_surgery.py page`'s data
    build(base, put, data)             gadgets/rsa2048/gidney/ into a site (the data copied beside it)
    standalone(out, data, index_json)  only that page and its data, wrapped as the site wraps its pages
    summary(dst)                       the numbers another page quotes: years, ions
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from .rsa import CSS, HINTS as RSA_HINTS, KEY as RSA_KEY, WEB, _years, ion_key, theme_script

__all__ = ["KEY", "page", "build", "standalone", "summary", "index_entry"]

KEY = f"{RSA_KEY}/gidney"
#: the player's agent declaration, the same controls as gadgets/rsa2048/; the prime here is cut into windows, not steps
HINTS = {**RSA_HINTS,
         "rsa-play": ("Play", "plays the prime on the machine, window after window on one clock"),
         "rsa-strip": ("The prime's windows", "the parts of the prime along its time; a click moves the clock there"),
         "rsa-fit": ("Whole machine", "shows the whole floorplan"),
         "rsa-programs": ("Every program", "each distinct compiled program the prime places, its ions, its exact time, how often a "
                          "prime runs it and its check at d = 11")}
#: what each kind of gadget is drawn in, as the player draws it
KINDS = (("merge", "#2e6fd8"), ("preparation", "#12a150"), ("readout", "#e08a00"), ("cultivated |T>", "#c2185b"),
         ("|Ȳ>", "#0f9d9a"), ("transversal H", "#8b3fd0"), ("cold access (composed)", "#795548"), ("held patch", "#8d96a8"),
         ("factory slot", "#e7e9ee"))


def summary(dst) -> dict:
    meta = json.loads((Path(dst) / "meta.json").read_text(encoding="utf-8"))
    return {"years": _years(meta["program_s"]), "prime_s": meta["prime_s"], "hot": meta["hot"]["ions"], "cold": meta["cold_ions"],
            "ions": meta["hot"]["ions"] + meta["cold_ions"], "tiles": meta["hot"]["tiles"]}


def page(dst, ours: dict | None = None) -> str:
    """The page's body from the data in `dst`; `ours`, if given, gadgets/rsa2048/'s {years, ions} to compare with."""
    meta = json.loads((Path(dst) / "meta.json").read_text(encoding="utf-8"))
    progs = json.loads((Path(dst) / "programs.json").read_text(encoding="utf-8"))
    e = html.escape
    prime, hot, cold = meta["prime_s"], meta["hot"], meta["cold_ions"]
    years = _years(meta["program_s"])
    compiled = [p for p in progs if p["d11"] in ("pass", "fail")]
    passed = sum(p["d11"] == "pass" for p in compiled)
    merges = {json.dumps(p["shape"]) for p in progs if p["kind"] == "merge"}
    work = meta["work_s"]
    total_work = sum(work.values())
    rows = "".join(f"<tr><td>{e(p['name'])}</td><td>{p['steps']}</td><td>{p['span']:,.0f} s</td></tr>" for p in meta["parts"])
    wrows = "".join(f"<tr><td>{e(k)}</td><td>{v:,.0f} s</td><td>{100 * v / total_work:.1f} %</td></tr>"
                    for k, v in sorted(work.items(), key=lambda kv: -kv[1]) if v >= 1)
    order = sorted(progs, key=lambda p: (p["kind"] != "idle", -p["uses"]))
    prow = "".join(
        f"<tr><td>{e(p['title'])}</td><td>{p['ions']:,}</td><td>{p['us'] / 1000:,.3f} ms</td><td>{p['dur_us'] / 1000:,.3f} ms</td>"
        f"<td>{'every held patch, every round' if p['kind'] == 'idle' else format(p['uses'], ',')}</td>"
        f"<td>{'pass' if p['d11'] == 'pass' else e(p['check'])}</td><td>"
        + ("" if p["kind"] == "idle" or not p["uses"] else f'<button data-show="{p["id"]}" data-hint="rsa-show">Show one</button>')
        + "</td></tr>" for p in order)
    hints = json.dumps({k: {"t": a, "d": b} for k, (a, b) in HINTS.items()}, ensure_ascii=False)
    js = (WEB / "rsa.js").read_text(encoding="utf-8")
    key = "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c in (*KINDS, *ion_key()))
    speeds = "".join(f'<option value="{v}"{" selected" if v == "auto" else ""}>{n}</option>' for v, n in
                     (("auto", "Auto"), ("0.0001", "0.1 ms a second"), ("0.001", "1 ms a second"),
                      ("0.01", "10 ms a second"), ("0.1", "0.1 s a second"), ("1", "1 s a second"),
                      ("10", "10 s a second"), ("100", "100 s a second")))
    versus = ""
    if ours:
        versus = (f'<p class="muted">Our own compilation of the same program (<a href="../">RSA-2048 at d = 11</a>) takes '
                  f'{ours["years"]:.2f} years on {ours["ions"] / 1e6:.1f} million ions: this one takes {years / ours["years"]:.2f} times as '
                  f'long on {ours["ions"] / (hot["ions"] + cold):.0f} times fewer ions.</p>')
    return f"""
<p class="sub"><a href="../../">Gadgets</a> &rsaquo; <a href="../">RSA-2048 at d = 11</a> &rsaquo; Gidney's compilation</p>
<h1>Factoring RSA-2048 at d = 11, as Gidney compiles it</h1>
<p class="rsa-answer">{years:.2f} years</p>
<p>of machine time for Gidney's 2025 algorithm in his own compilation, as FormalRV's listing writes it, on the stationary-tile QCCD
machine at code distance 11, with <b>{hot['ions']:,} ions</b> on {hot['tiles']:,} tiles and {cold:,} more in yoked cold storage.
The listing runs as written, in its order: each of its products is one lattice-surgery merge (FormalRV's own <code>merge</code>,
11 rounds), its magic states are grown by cultivation and turned into CCZ states in factory slots, and its exponent waits in cold
storage. One prime is scheduled whole on one clock, {prime:,.0f} s, and every program it places is compiled and checked (a cold
access, composed, and a transversal H, one pulse, are not programs); the program, {meta['primes']:,} primes and the work around
them, is scheduled whole too.</p>
{versus}
<div class="gstat">
<div><b>{prime / 3600:.2f} h</b>one prime</div>
<div><b>{meta['primes']:,}</b>primes</div>
<div><b>{(hot['ions'] + cold) / 1e6:.2f} million</b>ions</div>
<div><b>{meta['gadgets'] / 1e6:.1f} million</b>gadgets in a prime</div>
<div><b>{passed} of {len(compiled)}</b>programs checked at d = 11</div>
</div>

<div id="rsa">
  <canvas id="rsa-map" data-hint="rsa-map" aria-label="the whole machine"></canvas>
  <div class="rsa-stage">
    <canvas id="rsa-canvas" data-hint="rsa-canvas" aria-label="the machine's floorplan"></canvas>
    <div id="rsa-detail" data-hint="rsa-detail" role="group" aria-label="level of detail">
      <button data-level="auto">Auto</button><button data-level="machine">Machine</button><button
      data-level="patches">Patches</button><button data-level="gadgets">Gadgets</button><button data-level="ions">Ions</button>
    </div>
    <div class="rsa-zoom"><button id="rsa-in" data-hint="rsa-in" title="zoom in">+</button><button
      id="rsa-out" data-hint="rsa-out" title="zoom out">&minus;</button></div>
    <div id="rsa-card" data-hint="rsa-card" hidden></div>
  </div>
  <canvas id="rsa-strip" data-hint="rsa-strip" aria-label="the prime's windows along its time"></canvas>
  <div class="rsa-bar">
    <button id="rsa-play" data-hint="rsa-play">Play</button>
    <select id="rsa-speed" data-hint="rsa-speed" aria-label="speed">{speeds}</select>
    <input id="rsa-time" data-hint="rsa-time" type="range" min="0" step="0.000001" value="0" aria-label="time in the prime">
    <select id="rsa-part" data-hint="rsa-part" aria-label="part of the prime"><option value="" disabled selected>Go to a part</option></select>
    <button id="rsa-fit" data-hint="rsa-fit">Whole machine</button>
  </div>
  <p class="rsa-read" id="rsa-level" data-hint="rsa-level"></p>
  <p class="rsa-read" id="rsa-step"></p>
  <p class="rsa-read" id="rsa-clock"></p>
  <div class="rsa-key">{key}</div>
</div>
<p class="muted">Zoom as on a map -- the wheel, a double-click, + and &minus; -- and the map draws more as it grows: the machine,
then its patches, then each gadget named, then every trap and ion. Click anything for what it is; the address keeps the place and
the moment, to share. The schedule has no barriers, so the player cuts the prime into windows of {meta['window_s']:.0f} s and
fetches each as the clock nears it, with the gadgets still at work from the window before; a program's ions are fetched once one
of its gadgets is in view.</p>

<h2>The ions</h2>
<table><tr><th>where</th><th>what they are</th><th>ions</th></tr>
<tr><td>{hot['tiles']:,} tiles</td><td>every data and measure trap any patch, corridor or factory ever holds, and every trap a merge's
own program uses: {hot['data']:,} data and {hot['faces']:,} measure ions</td><td>{hot['ions']:,}</td></tr>
<tr><td>yoked cold storage</td><td>the exponent: 1,280 logical qubits at 73 ions each (inner d = 5, 16 wide)</td><td>{cold:,}</td></tr>
<tr><td></td><td><b>in all</b></td><td><b>{hot['ions'] + cold:,}</b></td></tr></table>
<p class="muted">A cultivated |T> needs five more ions than its tile's 241, in the column beside it: at most
{5 * 6 * meta['factories']:,} more on the {meta['factories']} factory slots' {6 * meta['factories']} T tiles, not in the count. Gidney's paper machine is 897,864 qubits at d = 25; at d = 11 a hot
logical qubit is 241 ions.</p>

<h2>One prime</h2>
<table><tr><th>part</th><th>windows</th><th>time</th></tr>{rows}
<tr><td><b>the prime</b></td><td>{len(meta['steps'])}</td><td><b>{prime:,.0f} s</b></td></tr></table>
<p>Where the time goes, as if nothing ran at once ({total_work / prime:.2f} programs run at once on average):</p>
<table><tr><th>work</th><th>a prime</th><th></th></tr>{wrows}</table>

<h2>Every program</h2>
<p>A gadget is a translated copy of one compiled program, so these are all the machine runs in a prime: {len(merges):,} merges,
the cultivated |T>, the |Ȳ> made and read in place, the preparations, the readouts and the round a held patch runs, each checked at
d = 11 ({passed} of {len(compiled)} pass). "Its time" is the program's own; "in the schedule" is what a copy holds its tiles for (a
preparation the slower basis's slot, a |T> until its gap is decided, a Ȳ read until it is decoded). "Show one" takes the map to the
next copy and draws its ions.</p>
<div class="rsa-progs" data-hint="rsa-programs"><table><tr><th>program</th><th>ions</th><th>its time</th><th>in the schedule</th>
<th>copies in a prime</th><th>d = 11</th><th></th></tr>{prow}</table></div>

<h2>What the number rests on</h2>
<ul class="steps">
<li>FormalRV's listing, every gate in its order: no gate is reordered and the program is not changed. Each product is FormalRV's
<code>merge</code> with its 11 rounds; a patch held H̄-turned or not is solved once for the whole program, and a product whose
patches disagree takes FormalRV's H̄.</li>
<li>One clock: every merge starts on a round, and below a prime each gadget starts as soon as its tiles, its patches and the
outcomes it waits for are ready (a <code>when source</code> waits for its decode). No tile, patch or wire is used twice at once in
the whole program.</li>
<li>Every program is compiled for the machine at d = 11 and checked against every rule and R10: the {len(merges):,} merges (each
61.032 ms, with its logic checked at d = 3 and one waveform set for the whole machine), the cultivated |T> (117.369 ms; Gidney,
Shutty and Jones's circuit on our tiles, kept 95.4 % of the time, 1.3 &times; 10<sup>-7</sup> error per kept T at our noise), Ȳ read
in place (48.415 ms), |Ȳ> made in place (43.862 ms), the preparations, the readouts and a held patch's round (5.822 ms).</li>
<li>The factory is FormalRV's |AutoCCZ> from eight cultivated T, scheduled once in a slot of 10 by 3 tiles and copied: 1.230 s, or
1.458 s when an output must leave turned.</li>
<li>Composed or assumed: a cold access is counted as a merge at a hot round with the stored block held (not compiled, its corridor
not routed); the yoked storage's outer checks are not on ions; a decode takes one round; a transversal H is one pulse, not a program
of its own; a slot's T tiles run their own program while the chip around them runs rounds, so they need drive lines of their own.
The schedule takes every T attempt as kept: with six T tiles a slot, 0.44 % of factories would end late, by 0.4 ms on average.</li>
<li>Drawn as checked: a merge's program ends by reading every data ion, the patches' too, so the check can test its result; in the
run the patches go on and only the corridor is read.</li>
</ul>
<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {{}}, {hints});</script>
{theme_script()}
<script>{js}</script>
"""


def build(base, put, data, ours: dict | None = None) -> None:
    """gadgets/rsa2048/gidney/ under the site section `base`, its data copied from `data`."""
    from ..site.build import PAGE, STYLE
    from .site import EXTRA_CSS, SECTION
    where = Path(base) / KEY
    if (where / "data").exists():
        shutil.rmtree(where / "data")
    shutil.copytree(data, where / "data")
    put(f"{SECTION}/{KEY}/index.html",
        PAGE.format(title="Factoring RSA-2048 at d = 11, as Gidney compiles it - QCCD studio", style=STYLE,
                    extra_css=EXTRA_CSS + CSS + "#rsa-map{height:84px}", body=page(data, ours)), 3, SECTION)


def standalone(out, data, index_json: str, ours: dict | None = None) -> Path:
    """Only gadgets/rsa2048/gidney/ (and its data) into the site directory `out`, wrapped as the site wraps its pages."""
    from ..site.build import FOOTER, with_nav
    from .site import SECTION
    idx = index_json.replace("</", "<\\/")

    def put(rel, page_, depth, active, app=False, extra=""):
        p = Path(out) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        page_ = with_nav(page_, depth, active, idx, app=app, extra=extra)
        p.write_text(page_.replace("__FOOTER__", FOOTER).replace("__SITEROOT__", "../" * depth), encoding="utf-8", newline="")
    build(Path(out) / SECTION, put, data, ours)
    return Path(out) / SECTION / KEY / "index.html"


def index_entry() -> dict:
    return {"t": "Factoring RSA-2048 at d = 11, as Gidney compiles it", "d": "how long Gidney 2025's program takes on the "
            "stationary-tile QCCD machine at d = 11 in his own compilation, as FormalRV's listing writes it: every product a "
            "lattice-surgery merge, magic states by cultivation, one prime played from the whole machine down to every ion",
            "u": f"gadgets/{KEY}/", "k": "gadgets"}
