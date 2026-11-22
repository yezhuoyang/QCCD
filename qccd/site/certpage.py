"""The Compilation page: QCCDCert, the verified compiler, explained on real compilations.

The page follows the QCCDCert paper ("QCCDCert: Formally Verified Compilation for QCCD
Co-Design"): the three inputs (hardware program, rule program, OpenQASM), the event frame,
rules as assertions over one frame, the nine stages, and the theorem

    compile(H, R, P, κ) ⇓ (K, T)  ⟹  Meaning ∧ Legality ∧ Time

each clause shown on the paper's worked examples.  Every listing, number, table and drawing
is computed at build time from the compiler's output in qccd/site/compiled/cert/ (written by
`python -m qccd.cert.examples` and `python -m qccd.cert.studio_examples`); nothing is typed.

`body(...)` returns the page body; `build_pages(...)` writes the playable pages of the studio
examples with the one design-page template.  `IDS` lists the anchors the glossary may use.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable

from . import learn
from ..cert import frames as FR
from ..cert.examples import OUT as CERT, read
from ..cert.syntax import Names, lowered, rules_program

STUDIO = CERT / "studio"

#: anchors this page writes (the glossary and the tutorial link to them)
IDS = {"question": "question", "inputs": "inputs", "hardware": "hardware", "rules-program": "rules-program",
       "frame": "frame", "core": "core", "rules": "rules", "observations": "observations",
       "elaboration": "elaboration", "stages": "stages", "theorem": "theorem", "meaning": "meaning",
       "legality": "legality", "time": "time", "parallel": "parallel", "chain": "chain", "ex1": "ex1",
       "ex2": "ex2", "ex3": "ex3", "studio": "studio", "loops": "loops", "gates": "gates", "proof": "proof",
       "trust": "trust", "extensions": "extensions", "conclusion": "conclusion"}

#: (anchor, heading) for the search index
HEADINGS = [("question", "What must a compiler know?"), ("inputs", "The three inputs"),
            ("frame", "The event frame"), ("rules", "Rules: assertions over one frame"),
            ("elaboration", "Elaboration: one proof for every rule"), ("stages", "The nine stages"),
            ("theorem", "The theorem: Meaning, Legality, Time"), ("meaning", "Meaning: the same result"),
            ("legality", "Legality: every frame is legal"), ("time", "Time: the declared durations"),
            ("ex1", "Example 1: one CX, stage by stage"), ("ex2", "Example 2: feedback, and a rule that changes the code"),
            ("ex3", "Example 3: broadcast transport, parallel time, chain order"),
            ("studio", "On a studio machine"), ("loops", "A machine with a loop, and a router that proposes"),
            ("gates", "Every basic gate, compiled by QCCDCert"),
            ("proof", "How the proof is built"), ("extensions", "The compiler of this site: QCCDCert, extended")]

esc = html.escape


def code(lines, cls: str = "ex") -> str:
    if isinstance(lines, str):
        lines = lines.splitlines()
    return f'<pre class="{cls}"><code>{esc(chr(10).join(lines))}</code></pre>'


def tbl(head: list[str], rows: list[list[str]], cls: str = "ct") -> str:
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def eg(title: str, inner: str) -> str:
    return f'<div class="eg"><p class="egh">Example &middot; {title}</p>{inner}</div>'


def df(label: str, inner: str, anchor: str = "") -> str:
    a = f' id="{anchor}"' if anchor else ""
    return f'<div class="df"{a}><p class="dfh">{label}</p>{inner}</div>'


def fig(svg: str, caption: str, cls: str = "") -> str:
    return f'<figure class="dg {cls}">{svg}<figcaption>{caption}</figcaption></figure>'


# ------------------------------------------------------------------------ the paper's devices
LAYOUT = {  # node positions of the paper's three devices, as the paper draws them
    "Primer": {"S": (0, 0), "J": (1, 0), "G": (2, 0)},
    "Tee": {"S": (0, 0), "J": (1, 0), "G": (2, 0), "P": (1, 0.8)},
    "Line4": {"C0": (0, 0), "C1": (1, 0), "C2": (2, 0), "C3": (3, 0)},
}


def machine_of(hw: dict):
    """The paper's device as a studio machine, so it is drawn the studio's way.  A site with
    a calibration is a gate zone; a site through which a path passes is a junction."""
    from ..api import Machine
    from ..arch.builder import DeviceBuilder
    base = hw["device"]["base"]
    geo = base["geometry"]
    names = [s["name"] for s in geo["sites"]]
    pos = LAYOUT[base["name"]]
    gate = {c["site"] for c in base["calibrations"]}
    transit = {v for p in geo["paths"] for v in p["via"]}
    d = DeviceBuilder("explicit")
    for i, s in enumerate(geo["sites"]):
        x, y = pos[s["name"]]
        if i in transit:
            d.junction(s["name"], float(x), float(y))
        else:
            d.site(s["name"], float(x), float(y), zone="trap" if i in gate else "data", capacity=int(s["capacity"]))
    seen = set()
    for p in geo["paths"]:
        hops = [p["src"], *p["via"], p["dst"]]
        for a, b in zip(hops, hops[1:]):
            key = tuple(sorted((names[a], names[b])))
            if key not in seen:
                seen.add(key)
                d.segment(f"{key[0]}-{key[1]}", *key)
    return Machine.from_device(d.build(), name=base["name"].lower())


def ions_at(names: list[str], pos: list[int]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for q, s in enumerate(pos):
        out.setdefault(names[s], []).append(f"q{q}")
    return out


# ------------------------------------------------------------------------ figures
def timeline_svg(c, starts: list[int], durations: list[int] | None = None, chain: dict | None = None,
                 width: int = 760) -> str:
    """One row per site and one for the shared transport channel; a bar per event."""
    p = c.printer
    sites = p.sites
    timed = c.stages["timed"]["code"]
    rows = sites + (["channel 0"] if c.hardware["device"]["base"]["geometry"].get("broadcasts") else [])
    bars = []
    at, ex = 0, 0
    n = len(starts)
    for i in range(n):
        if chain and chain["isExchange"][i]:
            trap = chain["exchanges"][ex][0]
            ex += 1
            bars.append((trap, starts[i], chain["durations"][i], "swap"))
            continue
        tok = timed[at]
        at += 1
        kind, b = next(iter(tok.items()))
        d = durations[i] if durations else p.duration(tok)
        if kind == "transport":
            for m in b["moves"]:
                path = p.paths[m["path"]]
                bars += [(path["src"], starts[i], d, "move"), (path["dst"], starts[i], d, "move")]
            if len(rows) > len(sites):
                bars.append((len(sites), starts[i], d, f"w{p.paths[b['moves'][0]['path']]['waveform']}"))
        elif kind == "pulse":
            bars.append((p.calibrations[b["calibration"]]["site"], starts[i], d, "gate"))
        elif kind == "broadcast":
            for cal in b.get("calibrations", []):
                bars.append((p.calibrations[cal]["site"], starts[i], d, "gate"))
        elif kind in ("readout", "pump"):
            key = "readouts" if kind == "readout" else "pumps"
            bars.append((p.device[key][b["calibration"]]["site"], starts[i], d, "read"))
    end = max((s + d for _, s, d, _ in bars), default=1)
    left, rh, top = 70, 20, 8
    scale = (width - left - 16) / max(end, 1)
    h = top + rh * len(rows) + 30
    col = {"gate": "#2f9e8f", "read": "#8a94a6", "move": "#9cc1ee", "w0": "#2a78d6", "w1": "#e07b2a", "swap": "#d64545"}
    out = [f'<svg viewBox="0 0 {width} {h}" width="{width}" height="{h}" class="tl" role="img">']
    for r, name in enumerate(rows):
        y = top + r * rh
        out.append(f'<text x="{left - 6}" y="{y + 14}" text-anchor="end" class="tlab">{esc(name)}</text>'
                   f'<line x1="{left}" y1="{y + rh - 1}" x2="{width - 10}" y2="{y + rh - 1}" class="tgrid"/>')
    for r, s, d, k in bars:
        y = top + r * rh + 3
        out.append(f'<rect x="{left + s * scale:.1f}" y="{y}" width="{max(d * scale - 1, 1.5):.1f}" height="{rh - 7}" '
                   f'rx="2" fill="{col[k]}"><title>{esc(rows[r])}: t={s}, d={d}</title></rect>')
    step = 4 if end <= 60 else 10 if end <= 200 else 50 if end <= 1000 else 200
    yb = top + rh * len(rows)
    for t in range(0, end + 1, step):
        out.append(f'<line x1="{left + t * scale:.1f}" y1="{yb}" x2="{left + t * scale:.1f}" y2="{yb + 4}" class="tgrid"/>'
                   f'<text x="{left + t * scale:.1f}" y="{yb + 15}" text-anchor="middle" class="ttick">{t}</text>')
    out.append(f'<line x1="{left + end * scale:.1f}" y1="{top - 4}" x2="{left + end * scale:.1f}" y2="{yb + 2}" class="tend"/>'
               f'<text x="{left + end * scale - 3:.1f}" y="{top + 2}" text-anchor="end" class="tendl">finish {end}</text>')
    out.append("</svg>")
    return "".join(out)


TL_KEY = ('<p class="tlkey"><span style="background:#2f9e8f"></span>pulse <span style="background:#8a94a6"></span>readout '
          '<span style="background:#9cc1ee"></span>transport (source, destination) '
          '<span style="background:#2a78d6"></span>waveform 0 (+x) <span style="background:#e07b2a"></span>waveform 1 (&minus;x) '
          '<span style="background:#d64545"></span>exchange</p>')


def cx_identity_svg() -> str:
    """CX ≃ R(π/2,π/2) · MS(π/2) · R(−π/2,0)⊗R(−π/2,0) · R(−π/2,π/2), as stage 4 emits it."""
    w, h = 640, 96
    y0, y1 = 30, 70
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" class="circ" role="img">']
    for y, q in ((y0, "q0"), (y1, "q1")):
        out.append(f'<text x="6" y="{y + 4}" class="tlab">{q}</text><line x1="26" y1="{y}" x2="96" y2="{y}" class="wire"/>'
                   f'<line x1="134" y1="{y}" x2="{w - 8}" y2="{y}" class="wire"/>')
    out.append(f'<line x1="60" y1="{y0}" x2="60" y2="{y1}" class="wire"/><circle cx="60" cy="{y0}" r="4" class="dot"/>'
               f'<circle cx="60" cy="{y1}" r="8" class="targ"/><line x1="52" y1="{y1}" x2="68" y2="{y1}" class="wire"/>'
               f'<line x1="60" y1="{y1 - 8}" x2="60" y2="{y1 + 8}" class="wire"/>'
               f'<text x="115" y="{(y0 + y1) / 2 + 6}" text-anchor="middle" class="eqs">&#8771;</text>')

    def box(x, y, label, hh=22, ww=96):
        return (f'<rect x="{x}" y="{y - hh / 2}" width="{ww}" height="{hh}" rx="3" class="gbox"/>'
                f'<text x="{x + ww / 2}" y="{y + 4}" text-anchor="middle" class="glab">{label}</text>')
    out.append(box(146, y0, "R(π/2, π/2)"))
    out.append(f'<rect x="254" y="{y0 - 11}" width="92" height="{y1 - y0 + 22}" rx="3" class="gbox"/>'
               f'<text x="300" y="{(y0 + y1) / 2 + 4}" text-anchor="middle" class="glab">MS(π/2)</text>')
    out.append(box(358, y0, "R(−π/2, 0)") + box(358, y1, "R(−π/2, 0)") + box(466, y0, "R(−π/2, π/2)", ww=110))
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------------ frames
def frames_table(c, rules: list[dict], timeline: bool = False, first: int | None = None) -> str:
    fs = c.frames(timeline=timeline)
    FR.check_all(c.hardware, rules, fs)  # the display must agree with the compiler's acceptance
    vals = FR.evaluate(c.hardware, rules, fs)
    names = c.printer.sites
    rows = []
    for f, v in zip(fs[:first] if first else fs, vals):
        o = f.obs
        rows.append([f"<b>F<sub>{f.index}</sub></b>", f"<code>{esc(f.text)}</code>", f"{f.start}, {f.duration}",
                     ", ".join(map(str, o["N-"])), ", ".join(map(str, o["Ntr"])), ", ".join(map(str, o["N+"])),
                     str(o["n_optical"]), " ".join(f'<span class="ok">{esc(k)} &#10003;</span>' for k in v)])
    head = ["F", "instruction u", "t, &delta;", f"N<sup>&minus;</sup> ({', '.join(names)})",
            f"N<sup>tr</sup> ({', '.join(names)})", f"N<sup>+</sup> ({', '.join(names)})", "n<sub>optical</sub>", "rules on F"]
    return tbl(head, rows, "ct frames")


def projection(c) -> tuple[list[str], list[str]]:
    """proj(K): delete transports and waits, unpack broadcasts, forget calibrations -- and
    native(P), the pulse program of stage 4.  Theorem clause 1 says they are equal."""
    p = c.printer
    proj = []
    for tok in c.stages["timed"]["code"]:
        kind, b = next(iter(tok.items()))
        if kind in ("transport", "idle"):
            continue
        if kind == "broadcast":
            for pl in b["pulses"]:
                proj.append(p.token({"pulse": {"operation": pl, "guarded": b.get("guarded", False)}}))
        elif kind == "readout":
            proj.append(p.token({"measure": {k: v for k, v in b.items() if k != "calibration"}}))
        elif kind == "pump":
            proj.append(p.token({"reset": {k: v for k, v in b.items() if k != "calibration"}}))
        else:
            proj.append(p.token({kind: {k: v for k, v in b.items() if k != "calibration"}}))
    native = [p.token(t) for t in c.stages["pulses"]["code"]]
    return proj, native


# ------------------------------------------------------------------------ studio pages
def studio_rows() -> list[dict]:
    f = STUDIO / "rows.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def build_pages(out: Path, put: Callable, poster: Callable) -> list[dict]:
    """Each studio example as the page the studio opens on it, written by `render_html`, with
    the circuit beside it through QCCDCert's source origins."""
    from ..arch import Architecture
    from ..cost import corrected_model
    from ..ir.source_map import build as build_source
    from ..ir.tsir import TSIR
    from ..verify import verify
    from ..viz.render import render_html
    rows = studio_rows()
    if not rows:
        return []
    from .compile_examples import OUT as COMPILED
    model = corrected_model()
    archs: dict = {}
    for r in rows:
        dev = r["device"]
        if dev not in archs:
            archs[dev] = Architecture.from_json(json.loads((COMPILED / f"{dev}.arch.json").read_text(encoding="utf-8")))
        arch = archs[dev]
        folder = STUDIO / r["id"]
        prog = TSIR.load(folder / "prog.cooled.tsir.json")
        raw = json.loads((folder / "prog.cooled.tsir.json").read_text(encoding="utf-8"))
        cert = json.loads((folder / "prog.qcert.json").read_text(encoding="utf-8"))
        src = build_source(prog, cert, folder / "main.qasm")
        res = verify(prog, arch, model, check_metrics=False).result
        rel = f"compilation/cert/{r['id']}.html"
        render_html(arch, prog, res, model, out / rel, kicker="COMPILED BY QCCDCERT",
                    headline=f"{r['statement']} on {dev}",
                    lede=(f"QCCDCert's target code ({r['instructions']} instructions, {r['finish_us']} µs) with this "
                          f"site's cooling extension added ({r['cools']} cooling operations)."),
                    source=src, open_pane="Q")
        put(rel, (out / rel).read_text(encoding="utf-8"), 2, "compilation", app=True)
        init = raw["instructions"][0]["placement"]
        r["page"] = f"cert/{r['id']}.html"
        r["poster"] = poster(_machine(dev), init)
    return rows


def _machine(dev: str):
    from .compile_examples import DEVICES
    from ..api import Machine
    gen, params, _ = DEVICES[dev]
    return getattr(Machine, gen)(**params, name=dev)


# ------------------------------------------------------------------------ the page
def body(rows: list[dict], device_svg: Callable) -> str:
    primer, ex1, ex2 = read("primer"), read("ex1"), read("ex2")
    ex2b, ex3 = read("ex2", "-one-optical"), read("ex3")
    fp = ex1.fingerprint
    w: list[str] = []
    W = w.append

    def dev(c, pos=None, paths=None, fit=(420, 150), marks=None):
        m = machine_of(c.hardware)
        names = c.printer.sites
        ions = ions_at(names, pos) if pos is not None else None
        return device_svg(m, labels=True, ions=ions, paths=paths, marks=marks, fit=fit)

    W("<h1>Compilation</h1>")
    W('<p class="sub">How a quantum circuit becomes target code that a QCCD machine can run, and why the result is '
      'correct on <i>every</i> machine. The compiler of this site is <b>QCCDCert</b>, a compiler written and proved in '
      'Lean 4: the machine and its operating rules are inputs, and one theorem covers every candidate design. '
      'Every listing, number and drawing below is the compiler&rsquo;s own output, read when this page was built.</p>')
    W(learn.strip("compilation"))
    W(learn.summary([
        "<b>The inputs</b> (&sect;1&ndash;2). A <i>hardware program</i> says what the machine can do, a <i>rule program</i> "
        "says what it may do, and an OpenQASM program says what to compute.",
        "<b>The event frame</b> (&sect;3&ndash;4). Every instruction is checked on one frame: the placements before and "
        "after it, its start time and its duration. A rule is a formula over the counts of one frame.",
        "<b>The nine stages</b> (&sect;5). From OpenQASM to timed target code, one program per stage, on Example 1.",
        "<b>The theorem</b> (&sect;6&ndash;9). <i>Meaning</i>: the target code computes the circuit. <i>Legality</i>: every "
        "frame passes the core checks and every rule. <i>Time</i>: every event takes its declared duration, and the "
        "reported finish is the latest end. Each clause on a real example.",
        "<b>On a studio machine</b> (&sect;10). The same compiler on the machines of this site, every basic gate, "
        "playable; then how the proof is built (&sect;11) and what this site adds outside it (&sect;12).",
    ], lead="The page follows the paper <a href=\"../publications/#qccdcert\">QCCDCert: Formally Verified Compilation "
            "for QCCD Co-Design</a>: the same syntax, the same passes, the same theorem."))

    # ------------------------------------------------------------------ 1 question
    W('<h2 id="question">1. What must a compiler know?</h2>')
    W(learn.ask("What must a designer write down so that a compiler can decide whether target code may run on a machine?",
                "Where the ions are before, during and after each instruction, and when it runs. QCCDCert asks for exactly "
                "that, in three programs, and gives every operating rule one form: an assertion about one instruction."))
    W("<p>A QCCD machine holds its ions in traps and moves them along segments that meet at junctions; lasers act on the "
      "ions of one trap. Whether an instruction may run depends on the placement of every ion around it and on the clock: "
      "a junction passes one ion at a time, a trap holds a fixed number, and a laboratory may forbid a laser pulse until "
      "the optics have settled. A compiler that is to be <i>proved</i> correct for every candidate machine cannot have any "
      "of this built in. QCCDCert takes it as input.</p>")

    # ------------------------------------------------------------------ 2 inputs
    W('<h2 id="inputs">2. The three inputs</h2>')
    W(learn.section("the hardware program, the rule program and the source, on the paper&rsquo;s running example: a storage "
                    "site S, a transit site J and a gate zone G."))
    W(fig(dev(primer, primer.execution["initial"], [{"nodes": ["S", "J", "G"], "color": "#2a78d6", "label": "p0"}], fit=(520, 150)),
          "The running example. S holds one ion, J one, G two; path p<sub>0</sub> runs from S through J to G in two ticks; "
          "every laser control is at G, on optical channel 7. The ions stand where the compiler&rsquo;s placement put them."))
    W(df("Definition 1 (hardware program)",
         "<p>A <b>site</b> is any place where an ion can stay or pass through; its <b>capacity</b> cap(s) is the most "
         "ions it may hold. A <b>placement</b> maps each ion to a site. A <b>path</b> is a declared transport from a source "
         "site to a destination site, with the sites it passes through, via(p), its duration, the <b>transport channel</b> "
         "that drives it and the <b>waveform</b> that channel plays for it. A <b>calibration</b> binds one native operation, "
         "with its exact angles, to a site, an optical channel and a duration. The native operations are single-ion "
         "rotations R(&theta;,&phi;), two-ion M&oslash;lmer&ndash;S&oslash;rensen gates MS(&theta;), readout, reset by "
         "optical pumping, and a controller latch that saves the result of a classical comparison.</p>", "hardware"))
    W(eg("the hardware program of the running example, as the compiler holds it",
         code(primer.lines["hardware"]) +
         "<p>Read it line by line: three sites with capacities; one path; one optical channel; one calibration per exact "
         "waveform the laser at G can play (c0 to c2 serve the U gate, c3 to c6 the CX); one broadcast that plays "
         "R(&minus;&pi;/2,0) on up to two ions of G at once; readout (<code>acquire</code>) and optical pumping "
         "(<code>pump</code>) at G; and the controller latch. It says what the machine <i>can</i> do. It does not say where "
         "ions start.</p>"))
    W(df("Definition 2 (rule program)",
         "<p>A rule program is a list of named assertions <code>assert a: &phi;;</code>. Each must hold in the initial "
         "frame and in every later frame of the target code. &sect;4 says what &phi; may read.</p>", "rules-program"))
    W(eg("the rules of the running example",
         code(primer.lines["rules"]) +
         "<p><code>init</code>: at time zero one ion is at S. <code>cap</code>: no site holds more ions than its capacity "
         "during any event. <code>rel</code>: no optical pulse before tick 5 (the optics settle). Change these lines and "
         "the target code changes; the compiler and its proof do not.</p>"))
    W("<p>The third input is an ordinary OpenQASM 2 program: U and CX gates, gate definitions, barriers, measurement, reset, "
      "and conditions on a whole register such as <code>if(c==1)</code>.</p>")
    W(eg("the source of the running example", code(primer.lines["source"])))

    # ------------------------------------------------------------------ 3 frame
    W('<h2 id="frame">3. The event frame</h2>')
    W(learn.ask("What does a rule need to know to decide whether one instruction may run?",
                "The placements before and after it, the instruction itself, its start time and its duration: the event frame."))
    W(df("Definition 3 (event frame)",
         "<p>The machine runs each instruction as one <b>event</b>. Its <b>frame</b> is F = (H, pos<sup>&minus;</sup>, "
         "pos<sup>+</sup>, u, t, &delta;): the hardware H, the placements before and after, the instruction u, its start "
         "time t and its duration &delta;. Initialization is a frame F<sub>0</sub> at time zero, with zero duration and no "
         "activity.</p>", "frame"))
    W(df("Definition 4 (the three core checks)",
         "<p>An event is <b>legal</b> if (i) every ion it uses is where the event needs it: at the source of its path or at "
         "the site of its calibration; (ii) no site holds more ions than its capacity, before, during or after the event; "
         "(iii) each shared channel applies one waveform, and every ion it drives without a switch is listed as moving. "
         "Target code is legal if its initial placement respects every capacity and every event is legal and satisfies "
         "the designer&rsquo;s rules. The core checks apply to every machine; rules can only add restrictions.</p>", "core"))
    snaps = []
    fs = ex1.frames()
    for f in fs[:3] + [fs[5]]:
        paths = None
        if f.instruction and "transport" in f.instruction:
            paths = [{"nodes": ["S", "J", "G"], "color": "#2a78d6"}]
        snaps.append(f'<div class="snap">{dev(ex1, f.after if f.index else f.before, paths, fit=(250, 96))}'
                     f'<p><b>F<sub>{f.index}</sub></b> <code>{esc(f.text)}</code><br>t = {f.start}, &delta; = {f.duration}</p></div>')
    W('<div class="snaps">' + "".join(snaps) + "</div>")
    W(eg("the frames of Example 1 (one CX and a readout on the running-example machine)",
         frames_table(ex1, ex1.rules) +
         "<p>The transport F<sub>1</sub> shows the transit count: q0 counts at S, where it leaves, at J, which it crosses, "
         "and at G, where it joins q1, so N<sup>tr</sup> = 1, 1, 2. Rule <code>rel</code> is why F<sub>2</sub> to "
         "F<sub>4</sub> are waits: without them the first pulse would start at t = 2.</p>"))

    # ------------------------------------------------------------------ 4 rules
    W('<h2 id="rules">4. Rules: assertions over one frame</h2>')
    W(learn.ask("Why a language for rules, instead of a fixed list of checks built into the compiler?",
                "Operating rules differ between machines and change during co-design. Built in, every new rule would need a "
                "new proof. Written in one language, rules are inputs, and one proof covers them all."))
    W("<p>A rule is a first-order assertion with Boolean connectives, natural-number arithmetic and finite quantification "
      "over the declared sites, paths and transport channels. These are the assertions of Hoare logic with one change of "
      "role: a Hoare assertion is proved about one program, while a QCCDCert rule is an input the compiler enforces. It "
      "returns target code only if every rule is true on every frame of that code.</p>")
    o = ex1.frames()[1].obs
    W(df("Definition 5 (what a rule observes)", tbl(["notation", "in a rule program", "meaning", "on F<sub>1</sub> of Example 1"], [
        ["N<sup>&minus;</sup>(s), N<sup>+</sup>(s)", "<code>N-(s)</code>, <code>N+(s)</code>", "ions at s before / after the event",
         f"N<sup>&minus;</sup> = {o['N-']}, N<sup>+</sup> = {o['N+']}"],
        ["N<sup>tr</sup>(s)", "<code>Ntr(s)</code>", "ions that use s during the event: residents before or after, plus every move "
         "whose path passes through s", f"{o['Ntr']}"],
        ["cap(s)", "<code>cap(s)</code>", "the declared capacity", f"{o['cap']}"],
        ["M(p), M(c)", "<code>M(p)</code>", "moves along path p / on transport channel c", f"M(p<sub>0</sub>) = {o['M_path'][0]}"],
        ["W(c)", "<code>W(c)</code>", "distinct waveforms requested on channel c", f"W(0) = {o['W'].get(0, 0)}"],
        ["P(s)", "<code>P(s)</code>", "optical actions at s", f"{o['P']}"],
        ["n<sub>optical</sub>, n<sub>R</sub>, n<sub>MS</sub>, n<sub>move</sub>", "<code>n_optical</code> &hellip;",
         "counts of each kind of action (readout and pumping count as optical)", f"n<sub>move</sub> = {o['n_move']}, n<sub>optical</sub> = {o['n_optical']}"],
        ["t, &delta;", "<code>t</code>, <code>delta</code>", "start time, duration", f"{o['t']}, {o['delta']}"],
    ]), "observations"))
    W("<p>The transit count is conservative on purpose: it holds for the whole event, so it detects an ion that passes "
      "through a full trap, or through a junction another ion uses, without knowing in which order the departure and the "
      "arrival happen.</p>")
    W(tbl(["family", "example rule", "what it says"], [
        ["occupancy", "<code>forall s:Sites. Ntr(s) &lt;= cap(s)</code>", "restates the core capacity check; <code>Ntr(G) &lt;= 1</code> would "
         "operate the two-slot zone G with one ion without changing the machine"],
        ["transport and control", "<code>forall p:Paths. M(p) &lt;= 1</code>, <code>forall c:Channels. W(c) &lt;= 1</code>",
         "one ion per path; one waveform per channel, extended from broadcast channels to direct ones"],
        ["operation and timing", "<code>n_optical &lt;= 1</code>, <code>forall s:Sites. P(s) &lt;= 1</code>, <code>n_optical &gt; 0 =&gt; t &gt;= 5</code>",
         "one optical action per event; one per site; a release time"],
    ]))
    W('<h3 id="elaboration">Elaboration: one proof for every rule</h3>')
    W("<p>A designer writes a rule in general terms, such as &ldquo;no site exceeds its capacity&rdquo;. <b>Elaboration</b> "
      "compiles it into explicit checks for one machine: on the machine with sites S, J and G, &ldquo;for every site&rdquo; "
      "becomes three conditions joined by &ldquo;and&rdquo;. Below is the compiler&rsquo;s own elaboration of the three "
      "rules, as it writes them into the device file it then compiles against (the trailing <code>0 = 0</code> is the "
      "empty conjunction, true).</p>")
    names = Names(ex1.hardware)
    eff = json.loads((ex1.folder / "effective.json").read_text(encoding="utf-8"))
    W(tbl(["rule as written", "elaborated by the compiler for S, J, G"],
          [[f"<code>{esc(a)}</code>", f"<code>{esc(lowered(b, names))}</code>"] for a, b in zip(ex1.lines["rules"], eff["rules"])]))
    W(df("Lemma (elaboration preserves truth)",
         "<p>For every machine H, frame F and rule &phi;, with F &#8872;<sub>H</sub> &phi; the meaning of the rule as written:"
         "</p><p class=\"eq\">elab<sub>H</sub>(&phi;) &dArr; &psi; &nbsp;&rArr;&nbsp; ( eval(&psi;, F) = true &nbsp;&hArr;&nbsp; F &#8872;<sub>H</sub> &phi; )</p>"
         "<p>Proved once in Lean by induction over formulas (<code>Assertions.elaborate_correct</code>). Together with the "
         "theorem of &sect;6, which holds for every machine and every set of explicit checks: for every machine, rule and "
         "program, target code the compiler returns satisfies the rule <i>as the designer wrote it</i>. A new rule can make a "
         "compilation fail; it never needs a new proof.</p>"))
    W(learn.bridge("The inputs and the frame are defined. Next: what the compiler does with them, one program at a time."))

    # ------------------------------------------------------------------ 5 stages
    W('<h2 id="stages">5. The nine stages</h2>')
    W(learn.section("the chain of programs one compilation passes through, each the complete program after its stage, on "
                    "Example 1. Stages 1&ndash;4 fix <i>what</i> is computed and never look at the machine; stages 5&ndash;9 "
                    "decide <i>how</i>."))
    pipe = [("Source", "OpenQASM 2", "the program as written", "1–3"), ("Effects", "P<sub>E</sub>", "gates expanded, conditions saved", "4"),
            ("Pulses", "P<sub>P</sub>", "U, CX become R, MS pulses", "5"), ("Bound", "P<sub>B</sub>", "candidate controls", "6"),
            ("Placed", "&lang;pos<sub>0</sub>, P<sub>P</sub>&rang;", "initial sites", "7"), ("Routed", "", "transports, chosen controls", "8"),
            ("Packed", "", "shared pulses", "9"), ("Timed", "target code K", "durations and waits", "+"), ("Timeline", "T", "overlap, exchanges", "")]
    W('<div class="pipe">' + "".join(
        f'<div class="pb"><b>{a}</b><span>{b}</span><i>{c}</i></div>' + (f'<div class="pa">{n}</div>' if n else "")
        for a, b, c, n in pipe) + "</div>")
    W('<p class="cap">Teal numbers are stage numbers; &ldquo;+&rdquo; is the reported timeline, with the parallel schedule '
      'and chain order.</p>')
    stage_notes = [
        ("effects", "Stages 1–3: Effects", "Includes are resolved, angles checked, gate definitions and registers expanded. "
         "<i>Correct because</i> each operation is kept, in order."),
        ("pulses", "Stage 4: Pulses", "The CX becomes five native pulses. <i>Correct because</i> the five act as CX on every "
         "state, up to a global phase (&sect;7)."),
        ("bound", "Stage 5: Bound", "Each pulse gets the declared controls with exactly its waveform. <i>Correct because</i> a "
         "missing control fails here, not later."),
        ("placed", "Stage 6: Placed", "An initial site per ion. <i>Correct because</i> it fits every capacity and the rule "
         "<code>init</code>: one ion at S."),
        ("routed", "Stage 7: Routed", "A transport brings q0 to G, and each pulse picks one candidate there. <i>Correct "
         "because</i> the transport is legal at its placement, and a transport changes where ions are, never the quantum state."),
        ("packed", "Stage 8: Packed", "Two equal R pulses on distinct ions become one declared broadcast b0. <i>Correct "
         "because</i> the broadcast acts as the two pulses, and expanding it gives them back."),
        ("timed", "Stage 9: Timed", "Declared durations, and waits where a rule needs them: <code>rel</code> forbids optical "
         "actions before tick 5. <i>Correct because</i> a wait changes only the time, and every frame is checked at its own "
         "t and &delta;."),
    ]
    W('<div class="stages">' + "".join(
        f'<div class="stage"><p class="sh">{t}</p><p class="sn">{n}</p>{code(ex1.lines[k])}</div>' for k, t, n in stage_notes)
      + "</div>")
    W(f"<p>The last listing is the target code K: {len(ex1.execution['instructions'])} events, {ex1.finish} ticks. The "
      "compiler may fail instead, for example when a control is missing or the search bound is reached; a failure returns "
      "no target code.</p>")

    # ------------------------------------------------------------------ 6 theorem
    W('<h2 id="theorem">6. The theorem: Meaning, Legality, Time</h2>')
    W(learn.ask("What does target code that the compiler returns guarantee?",
                "Three things, for every machine, rule program, source and search setting: it computes the source, every "
                "one of its frames is legal, and its reported time is the time the machine model assigns."))
    W(df("Theorem (verified compilation)",
         "<p>Write compile(H, R, P, &kappa;) &dArr; (K, T) when the compiler, given a hardware description H, rules R, a source "
         "program P and a search configuration &kappa;, returns target code K with a timeline T. T lists the steps "
         "e<sub>1</sub>&hellip;e<sub>m</sub> (the instructions of K with chain exchanges inserted), their start times "
         "t<sub>i</sub> and a finish t<sub>end</sub>; H declares each duration &delta;<sub>i</sub>; the frame of e<sub>i</sub> "
         "is F<sub>i</sub> = (H, pos<sub>i&minus;1</sub>, pos<sub>i</sub>, e<sub>i</sub>, t<sub>i</sub>, &delta;<sub>i</sub>). "
         "Then, for all H, R, P, &kappa;, K and T, under the declared ideal primitive semantics,</p>"
         "<p class=\"eq\">compile(H, R, P, &kappa;) &dArr; (K, T) &nbsp;&rArr;&nbsp; <b>Meaning</b> &and; <b>Legality</b> &and; <b>Time</b></p>"
         "<ul class=\"clauses\">"
         "<li><b>Meaning.</b> erase(e<sub>1</sub>..e<sub>m</sub>) = K, proj(K) = native(P), and for every angle assignment "
         "&eta; and initial state &sigma;, the source result &sigma;&prime; exists and obs &#10214;K<sup>T</sup>&#10215;<sub>H,&eta;</sub> &sigma; "
         "&simeq; &sigma;&prime;.</li>"
         "<li><b>Legality.</b> Every frame passes the core checks and every rule: &forall;i. F<sub>i</sub> &#8872; Core<sub>H</sub> "
         "&and; &forall;&phi;&isin;R. F<sub>i</sub> &#8872;<sub>H</sub> &phi;; the chain replay accepts the steps and ends in the "
         "placement pos<sub>m</sub>; steps that share a resource never overlap; two steps on one broadcast channel are apart "
         "in time or play one waveform together; and on a uniform machine, moves of one broadcast transport make one motion "
         "in one time.</li>"
         "<li><b>Time.</b> t<sub>end</sub> = max<sub>i</sub>(t<sub>i</sub> + &delta;<sub>i</sub>) &le; &Sigma;<sub>i</sub> "
         "&delta;<sub>i</sub>, and every pulse takes the duration of the calibration at the trap that holds its ions.</li>"
         "</ul><p>In Lean it is one declaration, <code>verified_compilation</code>, whose conclusion is the conjunction of the "
         "three clauses. It is a soundness theorem: it constrains every result the compiler returns.</p>"))
    W(learn.bridge("The next three sections take one clause each, on the example that shows it best."))

    # ------------------------------------------------------------------ 7 meaning
    W('<h2 id="meaning">7. Meaning: the same result</h2>')
    W(learn.ask("A compiler that drops, merges or reorders one gate produces a program that still looks plausible. How "
                "would anyone notice?",
                "Erase everything the physical stages added (transports, waits, sharing) and you must get back the pulse "
                "program of stage 4, exactly. A dropped or reordered gate makes the two differ."))
    W("<p><b>erase</b> deletes the exchanges of the timeline; <b>proj</b> deletes transports and waits and unpacks shared "
      "pulses; <b>native(P)</b> is the ordered pulse program of stages 1&ndash;4. Below, both columns are computed from the "
      "compiler&rsquo;s output for Example 1.</p>")
    proj, native = projection(ex1)
    same = proj == native
    W(eg("proj(K) = native(P) on Example 1",
         tbl(["proj(K): the target code, transports and waits deleted, the broadcast unpacked", "native(P): stage 4", ""],
             [[f"<code>{esc(a)}</code>", f"<code>{esc(b)}</code>", '<span class="ok">=</span>' if a == b else '<span class="bad">&ne;</span>']
              for a, b in zip(proj, native)])
         + f"<p>{'Equal, line for line.' if same else 'NOT equal.'} This equality is the first conjunct of Meaning, and it is "
           "what rules out a dropped, merged or reordered operation.</p>"))
    W("<p>The rest of Meaning is about states. Each pass has its own equation between meanings, proved by induction over the "
      "program, and chaining them gives the clause:</p>")
    W('<div class="chain">' + "".join(
        f'<div class="cb"><b>{a}</b><span>{b}</span></div>' + (f'<div class="ce">=<i>{c}</i></div>' if c else "")
        for a, b, c in [("P", "&#10214;P&#10215;(&rho;)", "expansion keeps every operation, its order, and the moment a condition is read"),
                        ("P<sub>E</sub>", "&#10214;P<sub>E</sub>&#10215;(&rho;)", "U and CX act as their R/MS pulses on every state, up to a global phase"),
                        ("P<sub>P</sub>", "&#10214;P<sub>P</sub>&#10215;(&rho;)", "a transport changes the placement only; a pulse acts on the ions at its site"),
                        ("routed A", "obs &#10214;A&#10215;<sub>H</sub>(&sigma;<sub>0</sub>)", "a broadcast acts as the pulses it replaces"),
                        ("packed A&prime;", "obs &#10214;A&prime;&#10215;<sub>H</sub>(&sigma;<sub>0</sub>)", "a wait changes the time only"),
                        ("target code K", "obs &#10214;K&#10215;<sub>H</sub>(&sigma;<sub>0</sub>)", "")]) + "</div>")
    W(fig(cx_identity_svg(), "Stage 4 on Example 1: five pulses for one CX. Proved for the operators, so it holds when the two "
                             "ions are entangled with others; &simeq; is equality up to a global phase, which no measurement observes."))
    inv = []
    pos = list(ex1.stages["routed"]["initial"])
    done: list[str] = []
    names_ = ex1.printer.sites
    inv.append(["(empty)", ", ".join(f"q{i}@{names_[s]}" for i, s in enumerate(pos)), "none"])
    prefix = []
    for tok in ex1.stages["routed"]["code"][:4]:
        kind, b = next(iter(tok.items()))
        if kind == "transport":
            for m in b["moves"]:
                pos[m["ion"]] = ex1.printer.paths[m["path"]]["dst"]
            prefix.append("transport")
        else:
            prefix.append(ex1.printer.token(tok).split()[1].split("(")[0])
            done.append(prefix[-1])
        inv.append(["; ".join(prefix), ", ".join(f"q{i}@{names_[s]}" for i, s in enumerate(pos)), "; ".join(done) or "none"])
    W(eg("the routing invariant on Example 1 (stage 7)",
         tbl(["prefix A of the routed program", "placement", "pulses done, &pi;(A)"], inv) +
         "<p>After every prefix, &#10214;A&#10215;<sub>H</sub> and &#10214;&pi;(A)&#10215; agree on the logical state, and the "
         "placement reached is legal. The next step is either a transport that is legal here, or the next pulse with its "
         "ions already at a site that has the control (<code>CoreExecution.prefix_invariant</code>).</p>"))
    W("<p>On the timeline, events that overlap share no resource, and events that share no resource act in either order with "
      "the same result (<code>Independence.hardware_comm</code>), so running K in order of start time gives the same result "
      "as in program order: every timeline equals the program order up to swaps of independent neighbours "
      "(<code>TimeOrder.time_order_sound</code>).</p>")

    # ------------------------------------------------------------------ 8 legality
    W('<h2 id="legality">8. Legality: every frame is legal</h2>')
    W(learn.ask("A rule is checked on the frames of the target code. Who checks it, and when?",
                "The compiler, before it returns anything: the router admits a transport only with a proof that it passes "
                "the core checks, the timing stage evaluates every rule on every frame, and the timeline evaluates them "
                "again at the reported start times."))
    f5 = ex1.frames()[5]
    early = dict(f5.obs, t=2, end=2 + f5.duration)
    rel = next(r for r in ex1.rules if r["name"] == "rel")
    ev = FR.Eval(FR.Device(ex1.hardware))
    W(eg("a rule forces waits (Example 1)",
         f"<p>Frame F<sub>5</sub> is the first optical pulse, <code>{esc(f5.text)}</code>, at t = {f5.start}: "
         f"rule <code>rel</code> evaluates to <b>{ev.formula(rel['formula'], {}, f5.obs)}</b>. Without the three waits the same "
         f"frame would start at t = 2, and <code>rel</code> evaluates to <b class=\"bad\">{ev.formula(rel['formula'], {}, early)}</b> "
         "there. Stage 9 inserts exactly the waits the rule needs.</p>"))
    W('<h3 id="ex2">Example 2: feedback, and a rule that changes the code</h3>')
    W("<p>Two data ions each interact with one ancilla; the ancilla is read, and reset only if it read 1. The machine has "
      "storage S for two ions, a gate zone G for two, a parking site P and a junction J between them.</p>")
    W('<div class="strip2">' + fig(dev(ex2, ex2.execution["initial"], fit=(330, 170)), "The Tee machine, with the compiler&rsquo;s initial placement.")
      + fig(code(ex2.lines["source"]), "The source: a parity check with a conditional reset.") + "</div>")
    W("<p><b>Feedback.</b> The source tests the register in <code>if(c==1)</code>. The compiler turns the test into a "
      "<b>latch</b> that samples it once, and marks the guarded operations <code>when saved</code>. A guarded event "
      "reserves the same duration on both branches, so the placement and the finish time do not depend on the outcome; one "
      "target code covers both branches, and the rules are checked for both.</p>")
    W('<div class="strip2">' + fig(code(ex2.lines["rules"]), f"Rule program A: {len(ex2.execution['instructions'])} events, {ex2.finish} ticks.")
      + fig(code(ex2b.lines["rules"]), f"Rule program B adds <code>one</code>: {len(ex2b.execution['instructions'])} events, {ex2b.finish} ticks.") + "</div>")
    a_lines, b_lines = ex2.lines["timed"], ex2b.lines["timed"]
    W(eg("the same machine and source under the two rule programs",
         '<div class="strip2">' + fig(code(a_lines), "target code under A") + fig(code(b_lines), "target code under B") + "</div>"
         "<p>Under B, the broadcast frame would have n<sub>optical</sub> = 2, so rule <code>one</code> rejects it; stage 8 keeps "
         f"the two pulses and the code takes {ex2b.finish - ex2.finish} more ticks. Nothing in the compiler or its proof names "
         "this rule.</p>"))
    W(frames_table(ex2, ex2.rules))

    # ------------------------------------------------------------------ 9 time
    W('<h2 id="time">9. Time: the declared durations</h2>')
    W(learn.ask("A compiler reports that a program takes 32 ticks. What makes that number trustworthy?",
                "Every event is charged the duration the machine declares for it, events that overlap provably share no "
                "resource, and the reported finish is the latest end of an event."))
    W('<h3 id="ex3">Example 3: broadcast transport, parallel time, chain order</h3>')
    W(fig(dev(ex3, ex3.execution["initial"], [{"nodes": ["C0", "C1"], "color": "#2a78d6"}, {"nodes": ["C2", "C3"], "color": "#2a78d6"}],
              fit=(560, 120)),
          "Four traps in a line share one transport channel with two waveforms, one per direction; each trap has its own laser "
          "channel. Both first transports make the motion +x on waveform 0: one play of the waveform moves both ions."))
    W(fig(code(ex3.lines["hardware"]), "The hardware program: six paths on one shared transport channel, with their motions."))
    W('<div class="strip2">' + fig(code(ex3.lines["rules"]), "The rules: one ion per trap at the start, capacity, one waveform per channel.")
      + fig(code(ex3.lines["source"]), "The source: three CX and two readouts.") + "</div>")
    W('<h3 id="parallel">The parallel schedule</h3>')
    W(f"<p>Stage 9 produces one sequence of {len(ex3.execution['instructions'])} events, {ex3.finish} ticks. The parallel "
      "schedule keeps this sequence and gives each event the earliest start at which everything it holds is free: its ions, "
      "sites, direct channels, classical bits and the controller. A broadcast channel cannot be held, or the schedule is "
      "back to one transport at a time; instead its <b>activations</b> are recorded, one waveform from a start to an end, and "
      "a transport may start when every activation on its channel has ended, begins after it ends, or is the same waveform "
      "over the same interval.</p>")
    W(fig(timeline_svg(ex3, _sequential(ex3)) + TL_KEY,
          f"Example 3 in sequence: {ex3.finish} ticks."))
    W(fig(timeline_svg(ex3, ex3.parallel["starts"]) + TL_KEY,
          f"The parallel schedule: {ex3.parallel['finish']} ticks. The pulses in C1 and C3 overlap; the two +x transports start "
          "together on one play of waveform 0; the two later transports go in opposite directions, need two waveforms, and "
          "follow one another."))
    W(code(ex3.lines["parallel"]))
    W('<h3 id="chain">Chain order</h3>')
    W("<p>Stages 1&ndash;9 track which trap holds each ion. A trap also orders its ions, and only the ion at the end that "
      "faces a path can leave through it. The last pass replays the target code on a <b>chain machine</b> and inserts an "
      "<b>exchange</b> wherever the departing ion is not at that end; an exchange costs three two-qubit gates (12 ticks) and "
      "holds its trap and both ions.</p>")
    ch = ex3.chain
    W(fig(timeline_svg(ex3, ch["starts"], ch["durations"], ch) + TL_KEY,
          f"With chain order: {len(ch['exchanges'])} exchanges are inserted and overlap in different traps; {ch['finish']} ticks "
          f"(in sequence they would be {ch['sequentialFinish']})."))
    W(code(ex3.lines["chain"]))
    W(df("Time, on these numbers",
         f"<p>Every pulse is charged the duration of the calibration at the trap that holds its ions; the reported finish is "
         f"the latest end of a step: {ex3.parallel['finish']} = max<sub>i</sub>(t<sub>i</sub> + &delta;<sub>i</sub>) &le; "
         f"&Sigma;<sub>i</sub> &delta;<sub>i</sub> = {ex3.finish} (<code>ReportedTime.finish_latest_end</code>, "
         "<code>Parallel.finish_le_sequential</code>). The rules are evaluated again at the new start times, and the "
         "sequential timing is kept if one fails; the chain machine accepts only departures from a trap end, and an accepted "
         "replay keeps in every chain exactly the ions the target code places in that trap (<code>Chain.check_agrees</code>).</p>"))
    W('<h3 id="ex1">Example 1, complete</h3>')
    W("<p>Every program of Example 1 is in &sect;5; its frames are in &sect;3. The running example of &sect;2, with U, "
      f"measurement, conditions, reset and a barrier, compiles to {len(primer.execution['instructions'])} events, "
      f"{primer.finish} ticks ({primer.parallel.get('finish')} in parallel):</p>")
    W(code(primer.lines["timed"]))

    # ------------------------------------------------------------------ 10 studio
    W('<h2 id="studio">10. On a studio machine</h2>')
    W(learn.ask("The examples so far are the paper&rsquo;s small machines. What does the same compiler do on the machines "
                "of this site?",
                "The machine is translated into a hardware program, the studio&rsquo;s frame-level rules join the rule "
                "program, and QCCDCert compiles; the theorem covers the result on that machine exactly as above."))
    bell = next((r for r in rows if r["id"] == "bell"), None)
    if bell:
        bc = _load_studio("bell")
        W("<p>A studio machine is traps, junctions and the segments between them, zone types that say what a trap can do, "
          "and a cost model with the time of every primitive. The translation is a function of the machine and of the "
          "program&rsquo;s waveforms, and every choice it makes is stated:</p>")
        W("<ul>" + "".join(f"<li>{esc(n)}</li>" for n in bell["notes"]) + "</ul>")
        W(eg("the Bell pair on grid2x3",
             fig(code(bc.lines["hardware"][:9] + ["..."]), "The machine as a hardware program (first lines).")
             + fig(code(bc.lines["rules"]), "The rule program: capacity during every event, no ion rests on a junction, "
                                              "one waveform per channel (studio rule R22), one optical action per trap (R12), "
                                              "and a designer&rsquo;s rule that starts one ion per trap.")
             + code(bc.lines["timed"])
             + f"<p>QCCDCert: {bell['instructions']} events, {bell['finish_us']} µs. With this site&rsquo;s cooling extension "
               f"({bell['cools']} cooling operation{'' if bell['cools'] == 1 else 's'}): {bell['cooled_us']:g} µs, and the studio&rsquo;s replay passes all "
               f"{bell['studio_passed']} rules it checks.</p>"
             + learn.runbox(bell["page"], bell["poster"], "the Bell pair, compiled by QCCDCert, with cooling added", cls="wide")))
    ring = next((r for r in rows if r["id"] == "ghz4_ring6d"), None)
    if ring:
        rc = _load_studio("ghz4_ring6d")
        plan = json.loads((STUDIO / "ghz4_ring6d" / "route-plan.json").read_text(encoding="utf-8")) \
            if (STUDIO / "ghz4_ring6d" / "route-plan.json").exists() else None
        W('<h3 id="loops">A machine with a loop, and a router that proposes</h3>')
        W("<p>ring6d is a ring of six storage sites with two gate zones on spurs, each for two ions. Its loop is "
          "QCCDCert&rsquo;s own broadcast transport: one mandatory channel whose waveform moves every ion on the ring one "
          "slot. Where three rails meet (S0, S3) the studio&rsquo;s rules R2 and R23 become a capacity of one and a "
          "<code>rest</code> rule. QCCDCert&rsquo;s transport search moves the operands of the next pulse; here a gate "
          "zone is full and a bystander must leave first, so the search finds no route. This site&rsquo;s router "
          "then proposes one, and QCCDCert&rsquo;s routing-proposal stage admits each proposed transport only with its "
          "legality proof: the theorem covers the result exactly as before.</p>")
        moved = sum(len(ev) for ev in plan["transports"]) if plan else 0
        W(eg("GHZ-4 on ring6d",
             fig(code(rc.lines["rules"]), "The rule program on ring6d: R1, R23 (rest), R22, R12 and R4 (one movement "
                                          "class per step) as QCCDCert assertions.")
             + (f"<p>The proposal: an initial placement and {moved} transports before the {len(plan['transports'])} "
                f"tokens of the pulse program; QCCDCert admitted every one.</p>" if plan else "")
             + code(rc.lines["timed"][:14] + ["..."])
             + f"<p>QCCDCert: {ring['instructions']} events, {ring['finish_us']} µs. With cooling: {ring['cooled_us']:g} µs, "
               f"and the studio&rsquo;s replay passes all {ring['studio_passed']} rules it checks.</p>"
             + (learn.runbox(ring["page"], ring["poster"], "GHZ-4 on ring6d, compiled by QCCDCert from a checked proposal",
                             cls="wide") if ring.get("page") else "")))
    W('<h3 id="gates">Every basic gate, compiled by QCCDCert</h3>')
    W("<p>Stage 4 on each gate of the standard library, as the compiler emitted it. QCCDCert has no virtual Z: a beam reaches "
      "every ion of a trap, so a phase cannot be advanced for one ion alone, and every U becomes three R pulses with exact "
      "angles. A CX is one MS between four R pulses.</p>")
    grows = []
    for r in rows:
        if r["id"] == "bell" or r["device"] != "grid2x3":
            continue
        grows.append([f"<code>{esc(r['statement'])}</code>", esc(r["what"]),
                      "<code>" + "<br>".join(esc(x) for x in r["pulses"]) + "</code>",
                      f"{r['instructions']} / {r['finish_us']} µs",
                      f'<a href="{r["page"]}#step=1">play</a>' if r.get("page") else ""])
    W(tbl(["source", "what", "stage 4: the pulses", "target code: events / time", ""], grows, "ct gates"))

    # ------------------------------------------------------------------ 11 proof
    W('<h2 id="proof">11. How the proof is built</h2>')
    W(learn.ask("One theorem for every machine: how can a proof cover machines nobody has drawn yet?",
                "The machine and the rules are ordinary arguments of the compiler and of the theorem. No stage inspects which "
                "machine it is given: stages 1&ndash;4 do not see it at all, and stages 5&ndash;9 read it only through its "
                "declarations and through one evaluator for rules."))
    W("<p>The pipeline has CompCert&rsquo;s shape: a chain of intermediate languages with one proved pass between neighbours. "
      "Where a pass must search, a proved checker validates the result: a routing proposal from an external router is "
      "admitted only with the legality proof the search requires, or rejected. The theorem is proved by composing the "
      "contracts of the passes: source passes preserve the semantics of their languages, and physical passes also establish "
      "legality. Programs are finite, so preservation is an equality of results.</p>")
    W(tbl(["part of the theorem", "Lean declaration"], [
        ["the theorem", "<code>verified_compilation</code>"], ["success contract", "<code>compileQASM_sound</code>"],
        ["source expansion", "<code>compileFrontend_source_correct</code>"], ["native selection (stage 4)", "<code>selectProgram_correct</code>"],
        ["binding, placement", "<code>Binding.stage_correct</code>, <code>Placed.stage_correct</code>"],
        ["routing prefixes", "<code>CoreExecution.prefix_invariant</code>"], ["optical broadcast", "<code>Packed.stage_correct</code>"],
        ["timing, legality", "<code>Timed.stage_correct</code>"], ["assertion meaning", "<code>Assertions.elaborate_correct</code>"],
        ["assertion legality", "<code>Assertions.initial_satisfies</code>, <code>Assertions.run_satisfies</code>"],
        ["routing proposals", "<code>RoutePlan.compileQASM_sound</code>"],
        ["reported timeline", "<code>Reported.reported_sound</code>, <code>TimeOrder.reported_time_order_sound</code>"],
        ["independent events", "<code>Independence.hardware_comm</code>, <code>TimeOrder.resource_order</code>"],
        ["reported time", "<code>ReportedTime.finish_latest_end</code>, <code>ReportedTime.gate_durations</code>"],
        ["chain order", "<code>Chain.check_agrees</code>, <code>Chain.timeline_exclusive</code>"],
        ["broadcast transport", "<code>Broadcast.execution_same_motion</code>, <code>Shared.timeline_exclusive</code>"],
    ]))
    W('<h3 id="trust">What is proved, what is decided, what is assumed</h3>')
    W(tbl(["", ""], [
        ["<b>Proved once</b> (Lean kernel)", "stages 1&ndash;9 for every program, machine and rule set; rule elaboration; the parallel "
         "schedule; that events on disjoint resources commute; what the chain machine accepts. The proofs use only Lean&rsquo;s "
         "three standard axioms (<code>propext</code>, <code>Classical.choice</code>, <code>Quot.sound</code>)."],
        ["<b>Decided in each compilation</b>", "by a proved procedure inside the compiler: legality of every transport, including "
         "proposed ones; the rules on every frame; the rules again at parallel start times; the chain replay."],
        ["<b>Assumed</b>", "the machine performs each declared event within its declared duration whenever the core checks pass "
         "(the <i>primitive-realization assumption</i>), and Lean&rsquo;s compiler and runtime. The listings on this page are "
         "displays, outside the theorem."],
    ], "ct trust"))

    # ------------------------------------------------------------------ 12 extensions
    W('<h2 id="extensions">12. The compiler of this site: QCCDCert, extended</h2>')
    W(learn.ask("If this site needs more than the paper&rsquo;s compiler does, does it write a second compiler?",
                "No. Its compiler is QCCDCert with extensions. An extension either changes what QCCDCert is given, and "
                "QCCDCert checks the result, or adds to what QCCDCert returns, and says so; none replaces one of its passes."))
    W(tbl(["extension", "what it does", "covered by the theorem?"], [
        ["machine translation", "writes a studio machine as a hardware program (&sect;10): traps and junctions as sites, "
         "routes as paths, durations from the cost model", "the theorem covers the hardware program it writes"],
        ["the studio&rsquo;s rules as QCCDCert rules", "capacity during every event (R1), one ion on a junction (R2, as its "
         "capacity), no ion resting on a junction (R23), one waveform per channel (R22), one movement class per step (R4), "
         "one optical action per trap (R12)", "<b>yes</b>: they are in the hardware and rule programs"],
        ["closed loops", "a conveyor loop is QCCDCert&rsquo;s own broadcast transport: a mandatory channel whose one waveform "
         "moves every ion on the loop one slot", "<b>yes</b>: every rotation is a checked transport event"],
        ["this site&rsquo;s router", "where QCCDCert&rsquo;s transport search finds no route (a gate zone that is full), it "
         "proposes routes to QCCDCert&rsquo;s checked routing-proposal stage", "<b>yes</b>: each proposed transport is "
         "admitted only with its legality proof, or the compilation is refused"],
        ["cooling", "a state-preparation cool, and cooling before any gate whose ions would be over the motional budget "
         "(R7, R7c)", "no: heat is outside QCCDCert&rsquo;s model; the studio&rsquo;s replay checks the cooled programme"],
        ["the studio&rsquo;s replay", "judges the played programme under every studio rule and prices it", "no: a second, "
         "independent check"],
    ]))
    W("<p>The project&rsquo;s earlier compiler is kept only as a fallback for a design this pipeline cannot compile, outside "
      "the theorem; a programme it wrote says so where it is shown.</p>")
    W(f"<p class=\"note\">Compiler build used for this page: QCCDCert, Lean sources {esc(fp.get('sources_sha256', '?'))} "
      f"({fp.get('files', '?')} files, {esc(fp.get('lean', '?'))}).</p>")

    # ------------------------------------------------------------------ conclusion
    W('<h2 id="conclusion">What to remember</h2>' + learn.recap([
        "A QCCD machine is a <b>hardware program</b> (sites, paths, calibrations), its operating rules are a <b>rule program</b> "
        "(assertions over one event frame), and both are inputs of the compiler.",
        "The <b>event frame</b> F = (H, pos<sup>&minus;</sup>, pos<sup>+</sup>, u, t, &delta;) is what every check reads: the "
        "core checks and every rule.",
        "Nine stages, one program each; stages 1&ndash;4 never see the machine, so every candidate design starts from the same pulses.",
        "<b>Meaning</b>: erasing what the physical stages added gives back the pulses of stage 4, and the result is the "
        "source&rsquo;s. <b>Legality</b>: every frame passes the core checks and every rule. <b>Time</b>: declared durations, "
        "finish = latest end &le; the sum.",
        "One proof covers every machine and every rule; a new rule can make a compilation fail, never need a new proof.",
    ], "compilation", more="The tutorial continues in Lean: <a href=\"../learn/\">Part I, Build your own verified compiler</a>, "
                           "restates these definitions and proofs in lessons you can run in the page."))
    return "\n".join(w)


def _sequential(c) -> list[int]:
    out, t = [], 0
    for tok in c.stages["timed"]["code"]:
        out.append(t)
        t += c.printer.duration(tok) or 0
    return out


def _load_studio(gid: str):
    from ..cert.compile import load
    return load(STUDIO / gid)


CSS = r"""
.df{border-left:3px solid #1c2a4a;background:var(--card);padding:10px 14px;margin:14px 0;border-radius:0 8px 8px 0}
.df .dfh{font-family:var(--sans);font-weight:700;font-size:13px;letter-spacing:.02em;color:#1c2a4a;margin:0 0 6px}
.eg{border:1px solid #cfe3d8;background:#f4faf6;padding:10px 14px;margin:14px 0;border-radius:8px}
.eg .egh{font-family:var(--sans);font-weight:700;font-size:13px;color:#1f6b45;margin:0 0 6px}
.eq{text-align:center;font-size:17px;margin:10px 0}
pre.ex{font-size:12.5px;line-height:1.45;overflow-x:auto;background:#fbfaf7;border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0}
table.ct{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;display:block;overflow-x:auto}
table.ct th,table.ct td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
table.ct th{font-family:var(--sans);font-size:12px;color:var(--ink3);font-weight:600}
table.frames td:nth-child(2){white-space:nowrap}
table.gates td:nth-child(3){font-size:12px}
.ok{color:#1f8a4c;font-weight:600;white-space:nowrap}
.bad{color:#c0392b;font-weight:700}
figure.dg{margin:12px 0;text-align:center}
figure.dg svg{width:auto;max-width:100%;height:auto}
figure.dg pre{text-align:left}
figure.dg figcaption{font-size:13px;color:var(--ink3);margin-top:4px;text-align:left}
.snaps{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:10px 0}
.snap{border:1px solid var(--line);border-radius:8px;padding:6px;background:var(--card);text-align:center}
.snap svg{width:auto;max-width:100%;height:auto}.snap p{font-size:12px;margin:2px 0 0;text-align:left}
.strip2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;align-items:start}
.strip2 figure.dg{margin:6px 0}
.pipe{display:flex;flex-wrap:wrap;align-items:stretch;gap:4px;margin:10px 0}
.pipe .pb{flex:1 1 74px;border:1px solid #1c2a4a;border-radius:6px;padding:5px 6px;background:#fff;text-align:center;font-size:12px}
.pipe .pb b{display:block;font-family:var(--sans)}.pipe .pb span{display:block;color:var(--ink3);font-size:11px;min-height:14px}
.pipe .pb i{display:block;font-size:11px;color:#1f6b45}
.pipe .pa{align-self:center;font:700 11px var(--sans);color:#2f9e8f}
.pipe .pa:before{content:"\2192\00a0"}
.cap{font-size:12.5px;color:var(--ink3)}
.stages{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.stage{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--card)}
.stage .sh{font-family:var(--sans);font-weight:700;margin:0;font-size:14px}.stage .sn{font-size:13px;margin:2px 0 4px}
.stage:last-child{grid-column:1/-1}
ul.clauses li{margin:6px 0}
.chain{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:10px 0}
.chain .cb{border:1px solid #1c2a4a;border-radius:6px;padding:4px 8px;background:#fff;text-align:center;font-size:12px}
.chain .cb b{display:block;font-family:var(--sans)}.chain .cb span{display:block;background:#eef7f1;border-radius:4px;padding:1px 4px;margin-top:2px}
.chain .ce{font:700 16px var(--sans);color:#1f6b45;text-align:center;max-width:120px}
.chain .ce i{display:block;font:italic 10.5px var(--serif);color:var(--ink3)}
svg.tl .tlab,svg.circ .tlab{font:12px var(--sans);fill:#334}svg.tl .ttick{font:10px var(--sans);fill:#667}
svg.tl .tgrid{stroke:#e3e1da;stroke-width:1}svg.tl .tend{stroke:#c0392b;stroke-width:1.5}svg.tl .tendl{font:11px var(--sans);fill:#c0392b}
.tlkey{font-size:12px;color:var(--ink3)}.tlkey span{display:inline-block;width:12px;height:10px;border-radius:2px;margin:0 3px 0 10px;vertical-align:-1px}
svg.circ .wire{stroke:#223;stroke-width:1.3;fill:none}svg.circ .dot{fill:#223}svg.circ .targ{fill:#fff;stroke:#223;stroke-width:1.3}
svg.circ .gbox{fill:#fff;stroke:#223;stroke-width:1.2}svg.circ .glab{font:12px var(--sans);fill:#223}svg.circ .eqs{font:22px var(--serif);fill:#223}
table.trust td:first-child{white-space:nowrap}
@media (max-width:700px){.strip2,.stages,.snaps{grid-template-columns:1fr}}
"""
