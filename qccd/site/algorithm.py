"""The Algorithm section of the Compilation page.

The page's pipeline list says what happens to a circuit; this section states the problem
each pass solves, the algorithm it uses, and the proposition the checker decides, in the
register of a systems paper -- numbered definitions, problems, algorithms as pseudo-code,
propositions with proof sketches -- and follows every one of them with an EXAMPLE, drawn
as a diagram, on one of four circuits compiled for the purpose (`compile_examples.ALGO`):
circuit 1 on the 2x3 lattice, circuit 2 on the four-site register, circuit 3 on the
six-site ring by rotation, and a Toffoli for the lowering.

Nothing in an example is typed in.  Every diagram is drawn here from the device document
and the certificates (a Python trap graph, the ASAP rule, the meeting-trap argmin, the
waveform signature, a replay with rotations), and the placement traces re-execute the
placer's own tie rules and are ASSERTED against the layout the compiler chose, so a trace
that disagreed with the compiler would fail the build rather than reach a reader.

Mathematics is set in HTML and Unicode (no MathJax): the site loads no third-party script
outside /discuss/.
"""

from __future__ import annotations

import html
import math
import re
from collections import deque

#: The headings, for the search index.
HEADINGS = [
    ("algorithm", "The algorithm"),
    ("algo-problem", "1. Setting and problem statement"),
    ("algo-lower", "2. Lowering"),
    ("algo-layout", "3. Placement"),
    ("algo-layers", "4. Layering and round emission"),
    ("algo-site", "5. Site selection"),
    ("algo-route", "6. Routing within a layer"),
    ("algo-rotate", "7. The conveyor pass"),
    ("algo-cool", "8. Cooling insertion"),
    ("algo-correct", "9. Correctness"),
    ("algo-limits", "10. Limitations and measured gaps"),
    ("algo-examples", "11. The four circuits, compiled and verified"),
]

INK, GREEN, RED, ORANGE, BLUE, TEAL, GREY, PURPLE = "#1c2a4a", "#0b7a4b", "#c62828", "#b26a00", "#2a78d6", "#1baf7a", "#8a8985", "#7b3fa0"

CSS = """
.algo p{max-width:78ch}
.algo .df{margin:14px 0 10px;padding:12px 16px;border-left:3px solid #1c2a4a;background:var(--card);max-width:100ch}
.algo .df:has(pre.alg){max-width:none}
.algo .df>b:first-child,.algo .eg>b:first-child{font-family:var(--sans);font-size:12px;letter-spacing:.06em;text-transform:uppercase;margin-right:8px}
.algo .df>b:first-child{color:#1c2a4a} .algo .df p{margin:6px 0 0} .algo .df .words{margin-top:8px;color:var(--ink2);font-size:14px}
.algo .eg{margin:8px 0 18px 0;padding:10px 16px 12px;border-left:3px solid #0b7a4b;background:#f4faf6;max-width:none;font-size:14.5px}
.algo .eg>b:first-child{color:#0b7a4b} .algo .eg p{margin:6px 0 0;max-width:78ch} .algo .eg table{margin:8px 0 2px}
.algo .eg pre{margin:8px 0 4px}
.algo .eq{font-family:var(--serif);font-size:16.5px;text-align:center;margin:12px auto;max-width:78ch;line-height:1.7}
.algo var,.algo i{font-style:italic}
.algo pre.alg{font-size:12.5px;line-height:1.65;background:#faf9f5;padding:12px 16px;border:1px solid var(--line);border-radius:10px;white-space:pre;overflow-x:auto}
.algo pre.alg b{color:#1c2a4a}
.algo .notes{font-size:12px;background:#f7f6f2;padding:8px 10px;margin:6px 0 10px;border-left:3px solid #1c2a4a}
.algo .notes .lab{font-family:var(--sans);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;font-weight:700;color:var(--ink3);display:block;margin:0 0 4px}
.algo pre.ex{font-size:11.5px;background:#f7f6f2;padding:8px 10px;margin:6px 0 10px}
.algo table.sm{font-size:12.5px;width:auto;margin:6px 0 10px} .algo table.sm td,.algo table.sm th{padding:3px 14px 3px 0;white-space:nowrap}
.algo table.sm td:first-child{color:var(--ink3)} .algo table.sm code{font-size:12px} .algo table.sm .pick{font-weight:700;color:#0b7a4b} .algo table.sm .out{color:#c62828}
.algo figure.dg{margin:10px 0 8px} .algo figure.dg figcaption{font-size:12.5px;color:var(--ink2);margin-top:6px;line-height:1.45;max-width:90ch}
.algo figure.dg .figsvg{padding:10px 12px} .algo figure.dg svg{display:block;width:auto;max-width:100%;height:auto;margin:0 auto}
.algo .strip{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start;margin:8px 0} .algo .strip figure{flex:1 1 180px;margin:0;max-width:260px}
.algo .strip .figsvg{padding:8px} .algo .strip figcaption{font-size:12px;color:var(--ink2);margin-top:4px;line-height:1.4}
.algo .strip figure.wide{flex:1 1 100%;max-width:560px}
.algo .wide svg.qc{max-width:100%}
.algo table.glance{font-size:12.5px} .algo table.glance td:first-child{white-space:nowrap;font-weight:600;color:#1c2a4a}
.algo ul.refs{font-size:12.5px;color:var(--ink2);padding-left:18px;max-width:78ch} .algo ul.refs li{margin:0 0 3px}
.algo ol.pf{max-width:78ch;padding-left:22px} .algo ol.pf li{margin:0 0 6px}
.algo .guide{background:var(--soft);border:1px solid #d6e2f4;border-radius:10px;padding:10px 16px;max-width:100ch;font-size:14.5px}
"""


# ---------------------------------------------------------------- the device, in Python

def machine(dev: str):
    from .compile_examples import DEVICES
    from ..api import Machine
    gen, params, _ = DEVICES[dev]
    return getattr(Machine, gen)(**params, name=dev)


def trap_graph(m, max_transit: int = 3):
    """The compiler's trap graph, rebuilt from the device document: sites in document
    order, one hop per pair of traps joined through junctions only (shortest `via` kept),
    all-pairs hop distances, capacities and positions."""
    dev = m.arch.device
    nodes, segs = dev.nodes, dev.segments
    adj: dict[str, list[tuple[str, str]]] = {}
    for sid, s in segs.items():
        a, b = s.ends
        adj.setdefault(a, []).append((b, sid))
        adj.setdefault(b, []).append((a, sid))
    sites = [n for n in nodes if nodes[n].kind == "site"]
    hops: dict[str, dict[str, tuple[list[str], list[str]]]] = {s: {} for s in sites}
    for src in sites:
        def walk(node, via, transit, depth):
            if depth > max_transit:
                return
            for nbr, sid in adj.get(node, []):
                if sid in via:
                    continue
                via2 = via + [sid]
                if nodes[nbr].kind == "site":
                    if nbr != src and (nbr not in hops[src] or len(hops[src][nbr][0]) > len(via2)):
                        hops[src][nbr] = (via2, list(transit))
                else:
                    walk(nbr, via2, transit + [nbr], depth + 1)
        walk(src, [], [], 0)
    dist: dict[str, dict[str, int]] = {}
    for s in sites:
        d = {s: 0}
        q = deque([s])
        while q:
            u = q.popleft()
            for v in hops[u]:
                if v not in d:
                    d[v] = d[u] + 1
                    q.append(v)
        dist[s] = d
    cap = {s: nodes[s].capacity for s in sites}
    return sites, hops, dist, cap


def lab_signature(m, hop_via: list[str], src: str) -> str:
    """The waveform signature of a hop on a device without loops or spurs (the lattice)."""
    dev = m.arch.device
    at = src
    labels = []
    for sid in hop_via:
        s = dev.segments[sid]
        nxt = s.ends[1] if s.ends[0] == at else s.ends[0]
        ax, ay = dev.nodes[at].pos
        bx, by = dev.nodes[nxt].pos
        dx, dy = bx - ax, by - ay
        sx = "+x" if dx > 1e-6 else "-x" if dx < -1e-6 else ""
        sy = "+y" if dy > 1e-6 else "-y" if dy < -1e-6 else ""
        labels.append((sx + sy) or "0")
        at = nxt
    return "shuttle " + ",".join(labels)


def replay(cert: dict, loops: dict[str, list[str]], t: int) -> dict:
    """Positions at the start of cycle t: init with every event before t applied, moves and
    rotations alike (a rotation advances every ion standing on the loop)."""
    pos = dict(cert["init"])
    events = [("m", m["t"], m) for m in cert.get("moves") or []] + [("r", r["t"], r) for r in cert.get("rotations") or []]
    for kind, tt, e in sorted(events, key=lambda x: (x[1], 0 if x[0] == "m" else 1)):
        if tt >= t:
            break
        if kind == "m":
            pos[e["ion"]] = e["to"]
        else:
            nodes = loops[e["loop"]]
            slot = {s: i for i, s in enumerate(nodes)}
            for ion, site in list(pos.items()):
                if site in slot:
                    pos[ion] = nodes[(slot[site] + e["delta"]) % len(nodes)]
    return pos


# ---------------------------------------------------------------- the placer, re-executed

def interaction(ops: list[dict]) -> dict[tuple[int, int], int]:
    w: dict[tuple[int, int], int] = {}
    for o in ops:
        qs = o.get("qubits") or []
        if len(qs) == 2:
            k = (min(qs), max(qs))
            w[k] = w.get(k, 0) + 1
    return w


def phi(assign: dict[int, str], w, dist) -> int:
    return sum(c * dist[assign[a]][assign[b]] for (a, b), c in w.items())


def greedy_trace(n: int, w, dist, usable: list[str]):
    """Algorithm 1 with the placer's tie rules.  Returns (steps as (qubit, site, reason),
    the assignment)."""
    def wt(a, b):
        return w.get((min(a, b), max(a, b)), 0)
    deg = [sum(wt(a, b) for b in range(n)) for a in range(n)]
    seed = max(range(n), key=lambda a: (deg[a], -a))
    ecc = {s: max(dist[s].get(t, 10 ** 6) for t in usable) for s in usable}
    centre = min(usable, key=lambda s: (ecc[s], usable.index(s)))
    assign = {seed: centre}
    steps = [(seed, centre, f"the most central trap (eccentricity {ecc[centre]}); q{seed} has the most two-qubit gates ({deg[seed]})")]
    for _ in range(n - 1):
        best = None
        for q in range(n - 1, -1, -1):
            if q in assign:
                continue
            tie = sum(wt(q, p) for p in assign)
            if best is None or tie > best[1] or (tie == best[1] and best[1] == 0):
                best = (q, tie)
        q, tie = best
        free = [s for s in usable if s not in assign.values()]
        cost = {s: sum(wt(q, p) * dist[s][assign[p]] for p in assign) for s in free}
        pick = min(free, key=lambda s: (cost[s], free.index(s)))
        partners = [f"q{p}" for p in assign if wt(q, p)]
        steps.append((q, pick, f"tied to {', '.join(partners)}; the first free trap at weighted distance {cost[pick]}"))
        assign[q] = pick
    return steps, assign


def fiedler(n: int, w) -> list[float]:
    if n <= 2:
        return [float(i) for i in range(n)]
    deg = [0.0] * n
    for (a, b), c in w.items():
        deg[a] += c
        deg[b] += c
    dmax = max(deg + [1.0])
    v = [math.sin(i * 7 + 1) for i in range(n)]

    def centre(x):
        mu = sum(x) / n
        return [t - mu for t in x]

    def norm(x):
        s = math.sqrt(sum(t * t for t in x))
        return [t / s for t in x] if s > 1e-12 else x
    v = norm(centre(v))
    for _ in range(400):
        nxt = [(dmax - deg[i]) * v[i] for i in range(n)]
        for (a, b), c in w.items():
            nxt[a] += c * v[b]
            nxt[b] += c * v[a]
        v = norm(centre(nxt))
    return v


def spectral_assign(n: int, w, hops, dist, usable: list[str]):
    f = fiedler(n, w)
    qorder = sorted(range(n), key=lambda i: f[i])
    ecc = {s: max(dist[s].get(t, 10 ** 6) for t in usable) for s in usable}
    centre = min(usable, key=lambda s: (ecc[s], usable.index(s)))
    seen, order, q = {centre}, [], deque([centre])
    while q:
        u = q.popleft()
        if u in usable:
            order.append(u)
        for v in hops[u]:
            if v not in seen:
                seen.add(v)
                q.append(v)
    order += [s for s in usable if s not in seen]
    return {qb: order[k] for k, qb in enumerate(qorder)}, qorder, order, f


def climb_trace(assign: dict[int, str], n: int, w, dist):
    cur = dict(assign)
    score = phi(cur, w, dist)
    steps = []
    rounds = 0
    improved = True
    while improved and rounds < 40:
        improved = False
        rounds += 1
        for x in range(n):
            for y in range(x + 1, n):
                cur[x], cur[y] = cur[y], cur[x]
                sc = phi(cur, w, dist)
                if sc < score:
                    steps.append((x, y, score, sc, dict(cur)))
                    score = sc
                    improved = True
                else:
                    cur[x], cur[y] = cur[y], cur[x]
    return cur, steps, rounds


# ---------------------------------------------------------------- helpers over the artefacts

def layers_of(ops: list[dict]) -> list[list[dict]]:
    last: dict[int, int] = {}
    out: list[list[dict]] = []
    for op in ops:
        qs = list(op.get("qubits") or [])
        lay = max([1 + last.get(q, -1) for q in qs] + [0])
        for q in qs:
            last[q] = lay
        while len(out) <= lay:
            out.append([])
        out[lay].append(op)
    return out


def dag_edges(ops: list[dict]) -> list[tuple[int, int]]:
    last: dict[int, int] = {}
    edges = []
    for op in ops:
        for q in op.get("qubits") or []:
            if q in last and (last[q], op["i"]) not in edges:
                edges.append((last[q], op["i"]))
            last[q] = op["i"]
    return edges


def op_text(op: dict) -> str:
    return f'{op["name"]} ' + ",".join(f"q{q}" for q in op.get("qubits") or [])


def pretty_pulse(p: str) -> str:
    """'R(1.5708,-1.5708)@q0' -> 'R(π/2, −π/2) on q0'; angles printed as multiples of π/4."""
    def ang(x):
        v = float(x)
        k = round(v / (math.pi / 4))
        if abs(v - k * math.pi / 4) < 1e-3:
            names = {0: "0", 1: "π/4", 2: "π/2", 3: "3π/4", 4: "π", -1: "−π/4", -2: "−π/2", -3: "−3π/4", -4: "−π"}
            return names.get(k, f"{k}π/4")
        return f"{v:.3g}"
    m = re.match(r"(R|VZ|MS)\(([^)]*)\)@(.*)", p)
    if not m:
        return p
    kind, args, who = m.groups()
    return f"{kind}({', '.join(ang(a) for a in args.split(','))}) on {who.replace(',', ', ')}"


def notes_block(meta: dict, label: str) -> str:
    notes = meta.get("notes") or []
    return (f'<pre class="notes"><span class="lab">{html.escape(label)}</span>' + html.escape("\n".join(notes)) + "</pre>") if notes else ""


def listing_excerpt(prog: dict, cert: dict, ids: list[int]) -> str:
    from .build import _listing
    lines = _listing(prog, cert).splitlines()
    by = {int(ln[1:4].strip()): ln for ln in lines if ln.startswith("#")}
    return '<pre class="ex"><code>' + html.escape("\n".join(by[i] for i in ids if i in by)) + "</code></pre>"


def df(kind: str, name: str, body: str, words: str = "") -> str:
    w = f'<p class="words"><b>In words.</b> {words}</p>' if words else ""
    return f'<div class="df"><b>{kind}</b> <i>{html.escape(name)}.</i> {body}{w}</div>'


def eg(body: str, label: str = "Example") -> str:
    return f'<div class="eg"><b>{label}</b> {body}</div>'


def fig(svg: str, caption: str) -> str:
    return f'<figure class="dg"><div class="figsvg">{svg}</div><figcaption>{caption}</figcaption></figure>'


def tbl(head: list[str], rows: list[list[str]]) -> str:
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="sm"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def esc(s) -> str:
    return html.escape(str(s))


def dev_fig(dev: str, caption: str, **kw) -> str:
    from .build import device_svg
    svg = device_svg(machine(dev), labels=True, **kw)
    cls = ' wide' if dev == 'chain4' else ''
    return f'<figure class="dg{cls}"><div class="figsvg">{svg}</div><figcaption>{caption}</figcaption></figure>'


def ions_of(pos: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ion, site in sorted(pos.items()):
        out.setdefault(site, []).append(ion)
    return out


# ---------------------------------------------------------------- diagrams

_SEQ = [0]


def _svg(w: float, h: float, body: str, aria: str) -> str:
    return (f'<svg viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="ui-sans-serif,system-ui,sans-serif" role="img" aria-label="{esc(aria)}">{body}</svg>')


def _arrow(x1, y1, x2, y2, col=INK, width=2.0, dashed=False, shrink=0.0) -> str:
    _SEQ[0] += 1
    mid = f"da{_SEQ[0]}"
    L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 or 1.0
    x2s, y2s = x2 - (x2 - x1) / L * shrink, y2 - (y2 - y1) / L * shrink
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    return (f'<defs><marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">'
            f'<path d="M0 0L10 5L0 10z" fill="{col}"/></marker></defs>'
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2s:.1f}" y2="{y2s:.1f}" stroke="{col}" stroke-width="{width}"{dash} marker-end="url(#{mid})"/>')


def _box(x, y, w, h, text, fill="#fff", stroke=INK, color=INK, size=11.5, bold=False, rx=6) -> str:
    fw = ' font-weight="700"' if bold else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
            f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + size * 0.36:.1f}" font-size="{size}" text-anchor="middle" fill="{color}"{fw}>{esc(text)}</text>')


def _text(x, y, text, size=11, color=INK, anchor="middle", bold=False, italic=False) -> str:
    fw = ' font-weight="700"' if bold else ""
    fs = ' font-style="italic"' if italic else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{color}"{fw}{fs}>{esc(text)}</text>'


def dag_svg(ops: list[dict], n: int) -> str:
    """The dependency graph, laid out by layer (columns) and qubit (rows)."""
    layers = layers_of(ops)
    lam = {o["i"]: k for k, l in enumerate(layers) for o in l}
    edges = dag_edges(ops)
    colw, rowh, bw, bh = 168, 46, 112, 26
    W, H = 60 + colw * len(layers) + 20, 46 + rowh * n + 10
    x = lambda k: 70 + colw * k
    y = lambda q: 56 + rowh * q
    body = []
    for k in range(len(layers)):
        body.append(f'<rect x="{x(k) - 10}" y="18" width="{colw - 12}" height="{H - 28}" rx="10" fill="{"#f4f3ee" if k % 2 == 0 else "#faf9f5"}"/>')
        body.append(_text(x(k) - 10 + (colw - 12) / 2, 34, f"layer {k}", 11.5, GREY, bold=True))
    for q in range(n):
        body.append(f'<line x1="{x(0) - 10}" y1="{y(q)}" x2="{W - 12}" y2="{y(q)}" stroke="#e5e3dc" stroke-width="1" stroke-dasharray="3 4"/>')
        body.append(_text(x(0) - 22, y(q) + 4, f"q{q}", 11, GREY, anchor="end"))
    centre = {}
    for o in ops:
        qs = o["qubits"]
        cy = sum(y(q) for q in qs) / len(qs)
        cx = x(lam[o["i"]]) + (colw - 12) / 2 - 10
        centre[o["i"]] = (cx, cy)
    for a, b in edges:
        (ax, ay), (bx, by) = centre[a], centre[b]
        body.append(_arrow(ax + bw / 2, ay, bx - bw / 2, by, GREY, 1.6, shrink=2))
    for o in ops:
        cx, cy = centre[o["i"]]
        fill = "#fff3e0" if len(o["qubits"]) == 2 else ("#eef3fb" if o["name"] not in ("measure", "reset") else "#f2f1ec")
        body.append(_box(cx - bw / 2, cy - bh / 2, bw, bh, f"o{o['i']}: {op_text(o)}", fill=fill, size=11))
    return _svg(W, H, "".join(body), "the dependency graph by layer")


def interaction_svg(w: dict, n: int) -> str:
    W, H = 60 + 120 * n, 92
    x = lambda q: 60 + 120 * q
    body = []
    for (a, b), c in w.items():
        body.append(f'<line x1="{x(a)}" y1="40" x2="{x(b)}" y2="40" stroke="{INK}" stroke-width="{1.5 + c}"/>')
        body.append(_text((x(a) + x(b)) / 2, 30, f"w = {c}", 11, INK, bold=True))
    deg = {q: sum(c for (a, b), c in w.items() if q in (a, b)) for q in range(n)}
    for q in range(n):
        body.append(f'<circle cx="{x(q)}" cy="40" r="15" fill="{INK}"/>')
        body.append(_text(x(q), 44, f"q{q}", 11.5, "#fff", bold=True))
        body.append(_text(x(q), 74, f"weight {deg[q]}", 10.5, GREY))
    return _svg(W, H, "".join(body), "the interaction graph")


def zip_svg(qorder: list[int], f: list[float], torder: list[str], assign: dict[int, str]) -> str:
    n = len(qorder)
    W, H = 60 + 120 * n, 158
    x = lambda k: 60 + 120 * k
    body = [_text(6, 20, "qubits sorted by the Fiedler vector", 10.5, GREY, anchor="start"),
            _text(6, 146, "traps in breadth-first order from the centre", 10.5, GREY, anchor="start")]
    for k, q in enumerate(qorder):
        body.append(_box(x(k) - 44, 26, 88, 26, f"q{q}   f = {f[q]:+.2f}", fill="#eef3fb"))
        body.append(_arrow(x(k), 54, x(k), 96, GREEN, 2.2, shrink=2))
        body.append(_box(x(k) - 30, 98, 60, 24, torder[k], fill="#fff3e0"))
    return _svg(W, H, "".join(body), "zipping the two orders")


def rounds_svg(wa: dict, wb: dict, first_id: int | None, la: str, lb: str) -> str:
    """Two gates' pulse sequences side by side, one column per round; with `first_id` the
    columns are labelled by the instruction each round became, otherwise by position."""
    k = max(len(wa["pulses"]), len(wb["pulses"]))
    colw, W, H = 158, 150 + 158 * k, 132
    body = [_text(8, 32, la, 11, INK, anchor="start", bold=True), _text(8, 82, lb, 11, INK, anchor="start", bold=True),
            _text(8, 118, "instruction" if first_id is not None else "pulse", 10.5, GREY, anchor="start")]
    for j in range(k):
        x0 = 150 + colw * j
        body.append(_text(x0 + colw / 2 - 8, 14, f"round {j}" if first_id is not None else f"{j + 1}{'st' if j == 0 else 'nd' if j == 1 else 'rd' if j == 2 else 'th'}", 10.5, GREY, bold=True))
        for w, yy in ((wa, 20), (wb, 70)):
            if j < len(w["pulses"]) and w["pulses"][j]:
                p = w["pulses"][j]
                body.append(_box(x0, yy, colw - 16, 30, pretty_pulse(p), fill="#fff3e0" if p.startswith("MS") else "#f2f1ec" if p.startswith("VZ") else "#eef3fb", size=10))
        if first_id is not None:
            body.append(_text(x0 + colw / 2 - 8, 118, f"#{first_id + j}", 11, INK, bold=True))
    if first_id is not None:
        body.append(f'<rect x="146" y="8" width="{colw * k - 8}" height="112" rx="10" fill="none" stroke="#cfceca" stroke-dasharray="4 4"/>')
    return _svg(W, H, "".join(body), "pulse sequences side by side")


def spacetime_svg(traps: list[str], ncyc: int, tracks: dict, waves: list[str], movers: set[str]) -> str:
    """tracks: ion -> (list of trap per cycle 0..ncyc, colour).  Columns are cycle starts."""
    colw, rowh = 200, 44
    W, H = 110 + colw * ncyc + 40, 50 + rowh * len(traps) + 40
    x = lambda t: 110 + colw * t
    y = lambda s: 50 + rowh * traps.index(s)
    body = []
    for s in traps:
        body.append(f'<line x1="{x(0) - 8}" y1="{y(s)}" x2="{x(ncyc) + 8}" y2="{y(s)}" stroke="#e5e3dc"/>')
        body.append(_text(x(0) - 16, y(s) + 4, s, 11, INK, anchor="end", bold=True))
    for t in range(ncyc + 1):
        body.append(f'<line x1="{x(t)}" y1="30" x2="{x(t)}" y2="{H - 34}" stroke="#e5e3dc" stroke-dasharray="3 4"/>')
        body.append(_text(x(t), 22, f"start of cycle {t}", 10, GREY))
    for t, wv in enumerate(waves):
        body.append(_text((x(t) + x(t + 1)) / 2, H - 12, f"cycle {t}: waveform {wv}", 10.5, PURPLE, bold=True))
    slots: dict[tuple[str, int], int] = {}
    for ion, (path, col) in tracks.items():
        pts = []
        for t, s in enumerate(path):
            k = slots.get((s, t), 0)
            slots[(s, t)] = k + 1
            pts.append((x(t) + (k * 14 - 7 if ion in movers or k else 0), y(s)))
        for t in range(len(path) - 1):
            (ax, ay), (bx, by) = pts[t], pts[t + 1]
            if path[t] != path[t + 1]:
                body.append(_arrow(ax, ay, bx, by, col, 2.4, shrink=12))
            elif ion in movers:
                body.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="{col}" stroke-width="1.4" stroke-dasharray="4 4"/>')
                body.append(_text((ax + bx) / 2, ay - 9, "waits", 10, col, italic=True))
        for (px, py) in pts:
            body.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="10" fill="{col}"/>' + _text(px, py + 3.5, ion, 9.5, "#fff", bold=True))
    return _svg(W, H, "".join(body), "the reservation table as a space-time grid")


def loop_order_svg() -> str:
    """Two panels on an eight-slot loop: who is planned first decides the makespan."""
    def panel(ox, title, plans, note):
        cx, cy, R = ox + 130, 128, 82
        ang = lambda k: -math.pi / 2 + 2 * math.pi * k / 8
        P = lambda k: (cx + R * math.cos(ang(k)), cy + R * math.sin(ang(k)))
        b = [_text(cx, 246, title, 12, INK, bold=True), f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="#cfceca" stroke-width="6"/>']
        for k in range(8):
            px, py = P(k)
            b.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="11" fill="#fff" stroke="{INK}" stroke-width="1.4"/>' + _text(px, py + 3.5, str(k), 10, INK))
        for ion, route, col, dy in plans:
            for a, c in zip(route, route[1:]):
                (ax, ay), (bx, by) = P(a), P(c)
                b.append(_arrow(ax, ay, bx, by, col, 2.4, shrink=13))
            k0 = route[0]
            sx, sy = cx + (R + 26) * math.cos(ang(k0)), cy + (R + 26) * math.sin(ang(k0))
            b.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="9" fill="{col}"/>' + _text(sx, sy + 3.5, ion, 9.5, "#fff", bold=True))
        b.append(_text(cx, 264, note, 11, INK))
        return "".join(b)
    body = panel(0, "a planned first", [("a", [0, 1, 2, 3], BLUE, 0), ("b", [2, 1], GREEN, 0)], "b waits two cycles, then steps 2→1: makespan 3")
    body += panel(300, "b planned first", [("b", [2, 1], GREEN, 0), ("a", [0, 7, 6, 5, 4, 3], BLUE, 0)], "slot 1 is taken for good: a goes round, makespan 5")
    return _svg(560, 276, body, "priority order on a loop")


def timeline_svg(cert: dict, ncyc: int, moves: list[dict], title: str) -> str:
    """The replay of a certificate (without rotations) as a cycle-by-cycle table, with each
    witness checked against it."""
    ions = sorted(cert["init"])
    colw, rowh = 58, 30
    W, H = 80 + colw * ncyc + 10, 60 + rowh * len(ions) + 34
    x = lambda t: 80 + colw * t
    y = lambda i: 60 + rowh * ions.index(i)
    body = [_text(6, 16, title, 12, INK, anchor="start", bold=True)]
    for t in range(ncyc):
        body.append(_text(x(t) + colw / 2, 40, f"t = {t}", 10, GREY))
    gates = cert["gates"]
    bad_cols = set()
    for g in gates:
        pos = dict(cert["init"])
        for m in moves:
            if m["t"] < g["t"]:
                pos[m["ion"]] = m["to"]
        ok = all(pos.get(i) == g["site"] for i in g["ions"])
        col = GREEN if ok else RED
        if not ok:
            bad_cols.add(g["t"])
        body.append(f'<rect x="{x(g["t"]) + 2}" y="48" width="{colw - 4}" height="{rowh * len(ions) + 4}" rx="6" fill="{col}" opacity="0.13"/>')
        body.append(_text(x(g["t"]) + colw / 2, H - 8, f"witness: op {g['dag']} at {g['site']}" + ("" if ok else " ✗"), 10, col, bold=True))
    for i in ions:
        body.append(_text(x(0) - 10, y(i) + 15, i, 11, INK, anchor="end", bold=True))
        pos = cert["init"][i]
        for t in range(ncyc):
            mv = next((m for m in moves if m["ion"] == i and m["t"] == t), None)
            body.append(_text(x(t) + colw / 2, y(i) + 15, pos, 11, INK))
            if mv:
                body.append(_arrow(x(t) + colw / 2 + 12, y(i) + 11, x(t + 1) + colw / 2 - 12, y(i) + 11, BLUE, 2.2))
                pos = mv["to"]
    return _svg(W, H, "".join(body), "the certificate replayed")


def strip_svg(instrs: list[dict]) -> str:
    """The programme as a strip of boxes coloured by kind, with the inserted cools marked."""
    bw, gap = 30, 6
    W, H = 20 + (bw + gap) * len(instrs) + 10, 96
    body = []
    kinds = {"init": ("init", "#efeeeb", INK), "cool": ("cool", "#dbeeff", INK), "simd": ("move", "#d7f2e4", INK),
             "measure": ("meas", "#ecdff5", INK), "barrier": ("bar", "#efeeeb", INK), "reset": ("reset", "#ecdff5", INK)}
    for k, ins in enumerate(instrs):
        x = 20 + (bw + gap) * k
        t = ins["type"]
        meta = ins.get("meta") or {}
        if t == "gate":
            g = ins.get("gate")
            label, fill = (g or "gate", "#fff3e0" if g == "MS" else "#eef3fb" if g == "R" else "#f7f6f2")
        else:
            label, fill, _ = kinds.get(t, (t, "#fff", INK))
        inserted = t == "cool" and meta.get("trigger")
        stroke = RED if inserted else "#cfceca"
        body.append(_box(x, 30, bw, 30, label, fill=fill, stroke=stroke, size=9.5, rx=5))
        body.append(_text(x + bw / 2, 76, f"#{ins['id']}", 9, GREY))
        if inserted:
            body.append(_text(x + bw / 2, 20, "R7", 9.5, RED, bold=True))
            body.append(_arrow(x + bw + 2, 45, x + bw + gap + 3, 45, RED, 1.8))
    return _svg(W, H, "".join(body), "the cooled programme as a strip")


def tableau_svg() -> str:
    """How the stabilizer generators move through the Bell circuit, and where the two
    mutations diverge (textbook Clifford facts, not read from an artefact)."""
    cols = [("start |00⟩", ["Z ⊗ I", "I ⊗ Z"], "#f2f1ec", INK), ("after H on q0", ["X ⊗ I", "I ⊗ Z"], "#eef3fb", INK),
            ("after CX(q0→q1)", ["X ⊗ X", "Z ⊗ Z"], "#e6f4ec", GREEN)]
    body = []
    for k, (title, gens, fill, col) in enumerate(cols):
        x = 20 + 190 * k
        body.append(_text(x + 70, 18, title, 11, GREY, bold=True))
        body.append(_box(x, 28, 140, 58, "", fill=fill, stroke=col))
        body.append(_text(x + 70, 52, gens[0], 13, col, bold=True) + _text(x + 70, 74, gens[1], 13, col, bold=True))
        if k:
            body.append(_arrow(x - 48, 57, x - 4, 57, INK, 2.0))
    for j, (title, gens, yy) in enumerate((("CX reversed (q1→q0)", ["X ⊗ I", "I ⊗ Z"], 112), ("frame update dropped", ["−X ⊗ X", "Z ⊗ Z"], 196))):
        x = 400
        body.append(_arrow(x - 48, 57 + (j + 1) * 6, x - 4, yy + 29, RED, 1.8, dashed=True))
        body.append(_text(x + 70, yy - 6, title, 11, RED, bold=True))
        body.append(_box(x, yy + 2, 140, 54, "", fill="#fbe9e7", stroke=RED))
        body.append(_text(x + 70, yy + 24, gens[0], 13, RED, bold=True) + _text(x + 70, yy + 46, gens[1], 13, RED, bold=True))
    return _svg(570, 262, "".join(body), "stabilizer generators through the Bell circuit")


# ---------------------------------------------------------------- the section

def section(by: dict, lowering_text: str = "") -> str:
    """`by` maps example id to the loaded example (see `compile_examples.load_compiled`)."""
    from .build import _gate_row, circuit_svg, device_svg
    par, ghz, rot, ccx, bell = by.get("algo_par"), by.get("algo_ghz"), by.get("algo_rot"), by.get("algo_ccx"), by.get("bell")
    if not (par and ghz and rot and ccx and bell):
        return ""
    pc, gc, rc, bc = par["cert"], ghz["cert"], rot["cert"], bell["cert"]
    mg, mc, mr = machine("grid2x3"), machine("chain4"), machine("ring6d")
    g_sites, g_hops, g_dist, g_cap = trap_graph(mg)
    c_sites, c_hops, c_dist, c_cap = trap_graph(mc)
    r_loops = {k: list(v.nodes) for k, v in mr.arch.device.loops.items()}
    ops1 = pc["circuit_ops"]
    w1 = interaction(ops1)
    edges1 = dag_edges(ops1)
    lay1 = layers_of(ops1)
    init1, init2 = pc["init"], gc["init"]
    body: list[str] = ['<div class="algo">']
    A = body.append

    A('<h2 id="algorithm">The algorithm</h2>'
      '<p class="sub">The pipeline above says what happens to a circuit, and the cards show the result for '
      'every basic gate. This section states the problem each pass solves, the algorithm that solves it, '
      'and the proposition the checker decides afterwards, for the compiler that produced every programme '
      'on this page and the leaderboard entries.</p>'
      '<div class="guide"><b>How to read it.</b> Each numbered definition, algorithm, theorem or check is stated '
      'once, formally, with a line in words underneath. It is then followed by a green <b>Example</b>, '
      'drawn as a diagram, on one of four circuits compiled for this section: <b>circuit 1</b>, two Bell '
      'pairs then a joining CNOT, on the 2×3 lattice; <b>circuit 2</b>, a GHZ chain, on the four-site '
      'register; <b>circuit 3</b>, a three-qubit parity check, on the six-site ring by rigid rotation; and '
      'a Toffoli, for the lowering. The Bell pair of the worked example above is used where two qubits '
      'suffice. Every diagram is drawn from the device document and the compiler\'s own certificate, and the '
      'placement traces re-execute the placer\'s rules and are checked against the layout it chose. The '
      'four circuits can be played in §11.</div>')

    # ================================================================ 1. setting
    A('<h3 id="algo-problem">1. Setting and problem statement</h3>')
    A(df("Definition 1", "device",
         'An <i>expanded architecture</i> is a tuple <var>A</var> = (<var>V</var>, <var>E</var>, cap, gate, spam, '
         '<var>L</var>) where <var>V</var> = <var>T</var> ⊔ <var>J</var> is a set of nodes partitioned into '
         '<i>traps</i> and <i>junctions</i>, <var>E</var> ⊆ <var>V</var>×<var>V</var> is the set of segments, '
         'cap: <var>T</var> → ℕ is the trap capacity, gate, spam ⊆ <var>T</var> are the traps in which a gate '
         'or a measurement may happen, and <var>L</var> is a set of named loops, each a cyclic sequence of '
         'nodes with a movement class per direction.',
         'The device is a graph. Some nodes are traps, where ions can sit (up to a capacity) and, in some '
         'of them, be gated or measured; the other nodes are junctions, where rails cross; the edges are '
         'the rails. Some rails form named loops that the control system can turn as a whole. This '
         'document is the same one the studio draws, and it is all the compiler ever reads about the '
         'hardware.'))
    A(eg('The three devices of this section, with their traps (discs), junctions (squares) and rails. '
         'On the lattice every trap can gate and measure and there is no loop. On the ring, the six loop '
         'sites S0–S5 (yellow) hold ions but cannot gate; only the two docks A0 and A3 (blue), each on a '
         'spur off the loop, can. The register is four traps in a line, no junction at all.'
         + '<div class="strip">'
         + dev_fig("grid2x3", "the 2×3 lattice: 7 traps, 6 junctions")
         + dev_fig("ring6d", "the six-site ring: loop L0 = S0…S5, docks A0 (on S0) and A3 (on S3)")
         + dev_fig("chain4", "the four-site register")
         + '</div>'))
    A(df("Definition 2", "trap graph",
         'The <i>trap graph</i> <var>G</var><sub><var>T</var></sub> = (<var>T</var>, <var>H</var>) has an edge, a '
         '<i>hop</i> <var>h</var> = (<var>t</var>, <var>t′</var>, via, junc), for every path in '
         '(<var>V</var>, <var>E</var>) from trap <var>t</var> to trap <var>t′</var> whose interior nodes are '
         'junctions only, with at most three of them; via(<var>h</var>) is its segment list and junc(<var>h</var>) its '
         'junctions. <var>d</var>(<var>t</var>, <var>t′</var>) is the shortest-path distance in '
         '<var>G</var><sub><var>T</var></sub>, and ecc(<var>t</var>) = max<sub><var>t′</var></sub> '
         '<var>d</var>(<var>t</var>, <var>t′</var>) the eccentricity of a trap.',
         'A hop is what one machine cycle can do to an ion: carry it from a trap to a neighbouring trap, '
         'passing through junctions on the way without stopping, because an ion never rests on a junction '
         '(R2). The compiler plans on the graph of traps and hops, not on the raw rails. The distance '
         'between two traps is the fewest hops between them.'))
    hop_paths = [{"nodes": ["T0_1h"] + junc + [v], "color": col}
                 for (v, (via, junc)), col in zip(g_hops["T0_1h"].items(), (BLUE, GREEN, ORANGE, PURPLE))]
    ecc_g = {s: max(g_dist[s].values()) for s in g_sites}
    A(eg('The four hops out of the lattice\'s central trap, each one cycle long. Every one passes through a '
         'junction and crosses two rails. Two traps on the same row, such as T0_1h and T0_0h, are two hops '
         f'apart, and the two ends of the lattice, T0_0h and T0_2h, are {g_dist["T0_0h"]["T0_2h"]}. T0_1h has '
         f'eccentricity {ecc_g["T0_1h"]}, the smallest, so it is the most central trap; the two row ends have '
         f'eccentricity {ecc_g["T0_0h"]}.'
         + dev_fig("grid2x3", "the four hops out of T0_1h: to T0_0v (blue) and T0_1v (green) through J0_1, to T1_0v (orange) and T1_1v (purple) through J1_1",
                   paths=hop_paths)))
    A(df("Definition 3", "circuit",
         'A circuit is <var>C</var> = (<var>Q</var>, ⟨<var>o</var><sub>0</sub>, …, <var>o</var><sub><var>m</var>−1</sub>⟩) '
         'with <var>n</var> = |<var>Q</var>| qubits and operations in program order, each with a qubit set '
         '<var>q</var>(<var>o</var>) ⊆ <var>Q</var> and a wire set (its qubits and classical bits). The '
         '<i>dependency DAG</i> has an edge <var>o</var><sub><var>i</var></sub> → <var>o</var><sub><var>j</var></sub> '
         'whenever the two are consecutive on some wire. The <i>interaction graph</i> <var>W</var> on '
         '<var>Q</var> has weight <var>w</var>(<var>a</var>, <var>b</var>) = the number of two-qubit operations '
         'on {<var>a</var>, <var>b</var>}.',
         'Two operations must keep their order only if they touch a common qubit (or classical bit); '
         'otherwise they are independent. Drawing an arrow from each operation to the next one on each of '
         'its wires gives the dependency graph. The interaction graph forgets the order and only counts '
         'how many two-qubit gates each pair of qubits shares.'))
    A(eg('Circuit 1 is <code>' + esc(par["qasm"].split("creg c[4];\n", 1)[-1].strip().replace("\n", " ")) + '</code>. '
         'Its dependency graph, laid out so that each column is one layer of §4: an arrow means "must come '
         'after". The two Hadamards depend on nothing; the third CNOT waits for both of the first two; each '
         'measurement waits for the last gate on its qubit.'
         + fig(dag_svg(ops1, 4), "the dependency graph of circuit 1; orange boxes are two-qubit gates, blue single-qubit gates, grey measurements")
         + '<p>Its interaction graph is a path: three pairs, one CNOT each. Circuit 2 has the same interaction '
         'graph but a very different dependency graph, a single chain, because every CNOT shares a qubit with '
         'the next.</p>'
         + fig(interaction_svg(w1, 4), "the interaction graph of circuits 1 and 2")))
    A(df("Definition 4", "programme and certificate",
         'A <i>programme</i> <var>P</var> is a sequence of instructions, each occupying one machine cycle: '
         'transport (a set of moves, or one loop rotation), pulses, cooling, readout. A <i>certificate</i> '
         '<var>κ</var> = (<var>μ</var>, init, moves, rots, gates) consists of the qubit-to-ion map '
         '<var>μ</var>: <var>Q</var> → <var>I</var>, the initial position init: <var>I</var> → <var>T</var>, '
         'the movement events with their cycles, and one <i>witness</i> per gate naming the instruction, '
         'the trap and the ions. The replay pos<sub><var>t</var></sub>: <var>I</var> → <var>T</var> is the '
         'position function obtained by applying the events before cycle <var>t</var> to init.',
         'Besides the programme, the compiler hands over a certificate: which ion plays which qubit, where '
         'each ion starts, every move it will make and when, and for every gate a claim of the form "this '
         'gate happened at this instruction, in this trap, on these ions". Replaying the moves from the '
         'start tells you where every ion is at every cycle, and each claim can be checked against that.'))
    bm, bw, hw = bc["moves"][0], bc["gates"][1], bc["gates"][0]
    A(eg(f'The Bell pair\'s certificate is small enough to draw in full. Ion q0 starts at {esc(bc["init"]["q0"])} and q1 at '
         f'{esc(bc["init"]["q1"])}; the one move carries q0 to {esc(bm["to"])} in cycle {bm["t"]}. The two witnesses, for '
         f'the Hadamard at cycle {hw["t"]} and the CNOT at cycle {bw["t"]}, are the shaded columns: at each, the '
         f'replay finds the named ions in the named trap, so the columns are green.'
         + fig(timeline_svg(bc, 10, bc["moves"], "the Bell certificate, replayed cycle by cycle"),
               "where each ion is at the start of every cycle, from the initial placement and the one move; a witness column is green when the ions it names are where it says")))
    A(df("Problem", "compilation",
         'Given (<var>A</var>, <var>C</var>), produce (<var>P</var>, <var>κ</var>) such that '
         '(i) <var>P</var> satisfies every rule of the rule set it is given (the standard set unless another '
         'is given; see the <a href="../rules/">Rules</a> page) '
         '(<i>legality</i>); (ii) <var>κ</var> satisfies Implements(<var>A</var>, <var>C</var>, <var>κ</var>) of '
         '§9 (<i>realisation</i>); (iii) the unitary of the pulses of <var>P</var>, read through <var>μ</var>, '
         'equals that of <var>C</var> up to global phase (<i>semantics</i>); while keeping the number of '
         'ion-hops, and the number of cycles, small.',
         'Produce a programme the machine can run, a certificate that places every gate of the circuit once, '
         'in order, in a trap where its ions are together, and pulses that compute '
         'the right thing; and make it short. Hops are the cost that matters physically: each hop heats its '
         'ion, and a hot ion gates worse (R7, R16), so a programme that moves ions further is not only '
         'slower but less accurate.'))
    A('<p>Conditions (i)–(iii) are decided after the fact by checkers that share no code with the '
      'compiler (§9); the objective is pursued heuristically, because its two sub-problems are '
      'NP-hard: placement is a quadratic assignment problem and routing is multi-agent path finding '
      'under capacity constraints. The pipeline is therefore <i>untrusted heuristics followed by a '
      'trusted decision procedure</i>. At a glance:</p>'
      '<div class="tw"><table class="glance"><thead><tr><th>pass</th><th>problem</th><th>algorithm</th>'
      '<th>cost</th><th>discharged by</th></tr></thead><tbody>'
      '<tr><td>§2 lowering</td><td>arity ≤ 2</td><td>rewrite table</td><td>linear</td><td>§9 (iii), tableau route only</td></tr>'
      '<tr><td>§3 placement</td><td>QAP on <var>W</var>, <var>d</var></td><td>greedy + spectral, 2-swap descent</td>'
      '<td><var>O</var>(<var>n</var>²|<var>W</var>|) per sweep</td><td>none (objective only)</td></tr>'
      '<tr><td>§4 layering</td><td>DAG scheduling</td><td>ASAP levels, round emission</td><td>linear</td><td>R12 by construction, §9 (ii)</td></tr>'
      '<tr><td>§5 site selection</td><td>meeting trap</td><td>argmin over <var>T</var></td><td><var>O</var>(|<var>T</var>|) per gate</td><td>R6, R6b, R1</td></tr>'
      '<tr><td>§6 routing</td><td>MAPF with capacities and waveforms</td><td>prioritised space-time A*</td>'
      '<td><var>O</var>(|<var>T</var>|·<var>H</var> log) per ion</td><td>R1–R5, R8, R11, R22</td></tr>'
      '<tr><td>§7 conveyor</td><td>bipartite contacts on a loop</td><td>readiness batching, greedy sweep</td><td>linear in contacts</td><td>as §6, plus R4d</td></tr>'
      '<tr><td>§8 cooling</td><td>R7 feasibility</td><td>replay and insert</td><td>one pass</td><td>R7, R7c</td></tr>'
      '</tbody></table></div>')

    # ================================================================ 2. lowering
    A('<h3 id="algo-lower">2. Lowering</h3>'
      '<p>Every operation with |<var>q</var>(<var>o</var>)| ≥ 3 is replaced, before placement, by a '
      'sequence of one- and two-qubit operations from a composite table, and the DAG is rebuilt from the '
      'rewritten list. All later passes see the lowered circuit <var>C′</var>. The rewrite is checked '
      'wherever condition (iii) is checked, because (iii) compares the programme with the original '
      '<var>C</var>: by the stabilizer tableau for a Clifford circuit, and for a Toffoli, which is not '
      'Clifford, as exact matrices, the Toffoli at its own defining matrix. An incorrect table entry would '
      'therefore show as a mismatch.</p>')
    low = re.search(r"native: (\d+) beams, (\d+) MS gates, (\d+) virtual-Z", lowering_text or "")
    cc = ccx["cert"]
    A(eg('The Toffoli, before and after. The router only ever brings <i>two</i> ions together, so the '
         'three-qubit gate on the left becomes the fifteen-gate circuit on the right, which is what the '
         'placer and router are given. The six CNOTs meet one pair at a time.'
         + fig(circuit_svg([{"i": 0, "name": "ccx", "qubits": [0, 1, 2], "params": []}], 3), "the source circuit: one Toffoli")
         + '<div class="wide">' + fig(circuit_svg(cc["circuit_ops"], 3), f"the lowered circuit the compiler works on: {len(cc['circuit_ops'])} operations, read from the certificate") + '</div>'
         + (f'<p>Each of those is then rewritten into pulses (Theorem 1). The tool\'s own count for the whole Toffoli is '
            f'{low.group(2)} two-ion pulses, {low.group(1)} single-ion pulses and {low.group(3)} frame updates. Because the '
            f'T gates are not Clifford, this circuit is checked against the exact 8×8 unitary; its card in §11 says so.</p>' if low else "")))

    # ================================================================ 3. placement
    A('<h3 id="algo-layout">3. Placement</h3>')
    A(df("Problem", "placement",
         'Choose π: <var>Q</var> → <var>T</var><sub>u</sub> with |π<sup>−1</sup>(<var>t</var>)| ≤ '
         '<var>c</var>(<var>t</var>) for every trap, minimising'
         '<div class="eq">Φ(π) = ∑<sub>{<var>a</var>,<var>b</var>}</sub> '
         '<var>w</var>(<var>a</var>, <var>b</var>) · <var>d</var>(π(<var>a</var>), π(<var>b</var>)).</div>'
         '<var>T</var><sub>u</sub> ⊆ <var>T</var> is the usable set and <var>c</var> the per-trap start limit, '
         'both defined below.',
         'Give every qubit a starting trap so that qubits which will gate together start close together. '
         'The score of a layout adds up, over every pair of qubits, "how many gates they share" times "how '
         'many hops apart they start". Lower is better. Single-qubit gates do not count: where they happen '
         'is decided later, and on a device where every trap gates they cost no transport at all.'))
    phi1 = phi({int(k[1:]): v for k, v in init1.items()}, w1, g_dist)
    A(eg('Circuit 1 on the lattice, with the layout the compiler chose. Each of the three interacting pairs '
         'is one hop apart, so the score is 1 + 1 + 1 = 3. It cannot be lower: interacting qubits starting in '
         'different traps are at least one hop apart, so the score is at least the number of gates. The '
         f'right-hand layout is a bad one for comparison: q1 and q2 at the two ends of the lattice, '
         f'{g_dist["T0_0h"]["T0_2h"]} hops apart, so that pair alone costs {g_dist["T0_0h"]["T0_2h"]}.'
         + '<div class="strip">'
         + dev_fig("grid2x3", f"the compiler's layout: Φ = {phi1}", ions=ions_of(init1),
                   paths=[{"nodes": [init1[f"q{a}"], j, init1[f"q{b}"]], "color": GREEN, "label": f"d = {g_dist[init1[f'q{a}']][init1[f'q{b}']]}"}
                          for (a, b) in sorted(w1) for j in [g_hops[init1[f"q{a}"]][init1[f"q{b}"]][1][0]]])
         + dev_fig("grid2x3", "a poor layout: q1 and q2 far apart, Φ = 1 + 3 + 1 = 5",
                   ions=ions_of({"q0": "T0_1h", "q1": "T0_0h", "q2": "T0_2h", "q3": "T1_1v"}),
                   paths=[{"nodes": ["T0_0h", "J0_0", "T0_0v", "J0_1", "T0_1v", "J0_2", "T0_2h"], "color": RED, "label": "d = 3"}])
         + '</div>'))
    A('<p><b>Usable traps.</b> Let <var>T</var><sub>1</sub> ⊇ <var>T</var><sub>2</sub> ⊇ … be the tiers '
      '{gate-capable, non-junction} ⊆ {non-junction} ⊆ {gate-capable} ⊆ <var>T</var>, each restricted to '
      'traps of positive capacity. <var>T</var><sub>u</sub> is the first tier whose total capacity '
      '∑ cap(<var>t</var>) is at least <var>n</var>. The start limit is <var>c</var>(<var>t</var>) = 1 if '
      '|<var>T</var><sub>u</sub>| ≥ <var>n</var>, otherwise <var>c</var>(<var>t</var>) = cap(<var>t</var>). '
      'In words: prefer traps that can gate and are not junctions (a junction holds one ion, and an ion '
      'parked on one blocks every route through it for a whole layer); start one ion per trap when there '
      'are enough traps, and only pack two into a trap on a device that is tight, which the compiler then '
      'reports.</p>')
    A(eg('On the lattice every trap qualifies: seven traps for four qubits, one ion each. On the ring only '
         'the two docks can gate, and together they hold four ions, exactly the four qubits, so the general '
         'router would start two ions in each dock and report "device is tight". (Circuit 3 is compiled by '
         'the conveyor pass of §7 instead, which lays out riders and docks by its own rule.)'
         + '<div class="strip">'
         + dev_fig("grid2x3", "the lattice: all 7 traps usable, one ion each", hi=set(g_sites))
         + dev_fig("ring6d", "the ring: only A0 and A3 gate, so two ions per dock", hi={"A0", "A3"},
                   ions={"A0": ["q0", "q1"], "A3": ["q2", "q3"]})
         + '</div>'))
    A(df("Algorithm 1", "greedy weighted insertion",
         '<pre class="alg">'
         '<b>seed</b>   <var>a</var>₀ ← argmax<sub><var>a</var></sub> ∑<sub><var>b</var></sub> <var>w</var>(<var>a</var>,<var>b</var>);   '
         'π(<var>a</var>₀) ← argmin<sub><var>t</var> ∈ <var>T</var><sub>u</sub></sub> ecc(<var>t</var>)   (the most central trap)\n'
         '<b>repeat</b> <var>n</var> − 1 times\n'
         '   <var>a</var> ← argmax<sub><var>a</var> unplaced</sub> ∑<sub><var>b</var> placed</sub> <var>w</var>(<var>a</var>,<var>b</var>)\n'
         '   π(<var>a</var>) ← argmin<sub><var>t</var> free</sub> ∑<sub><var>b</var> placed</sub> <var>w</var>(<var>a</var>,<var>b</var>) · <var>d</var>(<var>t</var>, π(<var>b</var>))\n'
         '</pre>'
         'A trap is <i>free</i> while it holds fewer than <var>c</var>(<var>t</var>) placed qubits. Ties: the seed is '
         'the lowest such qubit, the next qubit the highest-index one among equals, the trap the first in '
         'document order. Running time <var>O</var>(<var>n</var> · |<var>T</var><sub>u</sub>| · <var>n</var>).',
         'Start with the busiest qubit in the middle of the device. Then, again and again, take the '
         'unplaced qubit with the most gates to qubits already placed, and put it in the free trap closest '
         'to its partners.'))
    g_steps, g_assign = greedy_trace(4, w1, g_dist, g_sites)
    assert {f"q{q}": s for q, s in g_assign.items()} == init1, (g_assign, init1)
    c_steps, c_assign = greedy_trace(4, w1, c_dist, c_sites)
    phi_c_greedy = phi(c_assign, w1, c_dist)
    assert phi_c_greedy == 5, phi_c_greedy

    def steps_strip(dev: str, steps, final_phi: str) -> str:
        out = ['<div class="strip">']
        state: dict[str, str] = {}
        for k, (q, s, why) in enumerate(steps):
            state[f"q{q}"] = s
            out.append(dev_fig(dev, f"step {k + 1}: q{q} → {s}. " + why, ions=ions_of(state), hi_ions={f"q{q}"}))
        out.append("</div>")
        return "".join(out)
    A(eg('Circuit 1 on the lattice, one step per picture (the newly placed ion is green). q1 and q2 both have '
         'two gates; q1 is the lower index, so it is the seed, and it goes to the most central trap. The '
         'result is the compiler\'s layout, score 3.'
         + steps_strip("grid2x3", g_steps, "3")
         + '<p>On the register the same rule goes wrong. After q1 is seeded at C1, q2 is placed before q0 (the '
         'tie rule prefers the higher index), and it takes C0, the first free trap next to C1. That leaves q0 '
         'the trap on the other side, C2, and then q3 has to go three hops from its partner q2. Score 5, '
         'exactly the score the compiler reports for its greedy layout. Greedy commits one qubit at a time and never '
         'reconsiders; the next two algorithms exist for this case.</p>'
         + steps_strip("chain4", c_steps, "5")))
    A(df("Algorithm 2", "spectral ordering",
         'Let <var>L</var><sub><var>W</var></sub> = <var>D</var> − <var>W</var> be the Laplacian of the interaction '
         'graph and <var>f</var> its Fiedler vector, the eigenvector of the second-smallest eigenvalue, '
         'which minimises ∑ <var>w</var>(<var>a</var>,<var>b</var>)(<var>x</var><sub><var>a</var></sub> − '
         '<var>x</var><sub><var>b</var></sub>)² over unit vectors orthogonal to 𝟙. It is computed by 400 '
         'steps of power iteration on <var>d</var><sub>max</sub><var>I</var> − <var>L</var><sub><var>W</var></sub>, '
         're-centred and normalised at each step. Qubits are sorted by <var>f</var>; traps are ordered by '
         'breadth-first search of <var>G</var><sub><var>T</var></sub> from the central trap, each trap repeated '
         '<var>c</var>(<var>t</var>) times; the two orders are zipped.',
         'Instead of placing qubits one at a time, spread all of them along a line at once, so that '
         'qubits sharing many gates land next to each other (the Fiedler vector is the best such line, in '
         'the least-squares sense). Then walk the device outward from its centre, and hand the traps out '
         'along that walk, in order.'))
    s_assign, qorder, torder, fvec = spectral_assign(4, w1, c_hops, c_dist, c_sites)
    phi_c_spec = phi(s_assign, w1, c_dist)
    assert phi_c_spec == 4, phi_c_spec
    A(eg('For the path q0 – q1 – q2 – q3 the Fiedler vector puts the four qubits on a line in their own '
         'order (up to a sign), so sorting gives q3, q2, q1, q0. On the register the walk from the central '
         'trap C1 visits C1, then C0, then C2, then C3, alternating sides. Zipping the two orders gives the '
         'layout below: score 4, better than greedy\'s 5 and the score the compiler reports for it, but not '
         'yet 3, because the walk alternates sides of the centre while the line does not.'
         + fig(zip_svg(qorder, fvec, torder, s_assign), "the two orders and their zip: each qubit takes the trap below it")
         + dev_fig("chain4", "the spectral layout on the register: Φ = 2 + 1 + 1 = 4 (q2–q1 are two hops apart)",
                   ions=ions_of({f"q{q}": s for q, s in s_assign.items()}))))
    A(df("Algorithm 3", "selection and 2-swap descent",
         '<pre class="alg">'
         'π ← argmin { Φ(π<sub>greedy</sub>), Φ(π<sub>spectral</sub>) }\n'
         '<b>for</b> at most 40 sweeps, while some swap improved in the last sweep\n'
         '   <b>for</b> each pair <var>a</var> &lt; <var>b</var>:  <b>if</b> Φ(π ∘ (<var>a</var> <var>b</var>)) &lt; Φ(π) <b>then</b> π ← π ∘ (<var>a</var> <var>b</var>)\n'
         '<b>return</b> ⟨π, π<sub>greedy</sub>, π<sub>spectral</sub>⟩                (a ranked list)\n'
         '</pre>'
         'Φ is blind to routability, so the compiler tries the ranked candidates in turn and keeps '
         'the first that routes (§6).',
         'Take whichever of the two layouts scores better, then keep swapping pairs of qubits whenever a '
         'swap lowers the score, until no swap does. Keep the other two layouts as spares: a layout can '
         'score well and still leave an ion somewhere the router cannot get past later.'))
    climbed, swaps, rounds = climb_trace(s_assign, 4, w1, c_dist)
    assert {f"q{q}": s for q, s in climbed.items()} == init2, (climbed, init2)
    x0, y0, s0, s1, after = swaps[0]
    A(eg(f'On the register the spectral layout (score 4) is chosen over greedy (5). The descent tries the six '
         f'pairs in order; swapping q{x0} and q{y0} is the first that helps, taking the score from {s0} to {s1}, and '
         f'no later swap improves on it, so the descent stops after {rounds} sweeps. That is the layout the '
         f'compiler chose, the chain of the circuit laid along the chain of the device; its note reads '
         f'"hill-climb 4 -> 3 in 2 rounds". On the lattice greedy already scored 3 and no swap improves it.'
         + '<div class="strip">'
         + dev_fig("chain4", f"before: the spectral layout, Φ = {s0}", ions=ions_of({f"q{q}": s for q, s in s_assign.items()}), hi_ions={f"q{x0}", f"q{y0}"})
         + dev_fig("chain4", f"after swapping q{x0} and q{y0}: Φ = {s1}, the compiler's layout", ions=ions_of({f"q{q}": s for q, s in after.items()}), hi_ions={f"q{x0}", f"q{y0}"})
         + '</div>'
         + f'<div class="two"><div>{notes_block(par["meta"], "the compiler on circuit 1")}</div><div>{notes_block(ghz["meta"], "the compiler on circuit 2")}</div></div>'))

    # ================================================================ 4. layering
    A('<h3 id="algo-layers">4. Layering and round emission</h3>')
    A(df("Definition 5", "ASAP layering",
         'λ(<var>o</var>) = 0 if <var>o</var> has no predecessor in the DAG, otherwise λ(<var>o</var>) = '
         '1 + max{λ(<var>o′</var>) : <var>o′</var> → <var>o</var>}; equivalently, the length of the longest '
         'path from a source to <var>o</var>. The <i>layer</i> Λ<sub><var>k</var></sub> = '
         '{<var>o</var> : λ(<var>o</var>) = <var>k</var>}.',
         'Every operation goes into the earliest layer it can: one layer after the latest operation it '
         'depends on. Layers run one after another; inside a layer, nothing depends on anything else, so '
         'everything in it may happen at the same time, as far as the hardware allows.'))
    A(eg('Circuit 1\'s dependency graph from Definition 3, read column by column: the two Hadamards are '
         'layer 0; the two CNOTs, each one arrow later, are layer 1; the third CNOT and the measurements '
         'of q0 and q3 are layer 2; the last two measurements are layer 3. Four layers, as the compiler '
         'reports. Circuit 2 is one long chain, so each of its five layers holds a single gate (plus the '
         'measurements that become ready); there the layering buys nothing and it is the round emission '
         'below that still applies.'
         + fig(dag_svg(ops1, 4), "circuit 1 by layer: the columns are the layers")))
    A('<p>Within a layer, each gate <var>g</var> is decomposed into its pulse sequence '
      '<var>p</var><sub><var>g</var>,1</sub>, …, <var>p</var><sub><var>g</var>,ℓ(<var>g</var>)</sub> from the '
      'pulse table built on the identities of Theorem 1 (one beam and one frame update for a single-qubit '
      'gate; four beams around one '
      'two-ion pulse for a CNOT). Pulses are emitted in <i>rounds</i>: round <var>k</var> is the '
      'instruction set {<var>p</var><sub><var>g</var>,<var>k</var></sub> : <var>k</var> ≤ ℓ(<var>g</var>)}, one '
      'instruction per pulse kind, all in one machine cycle.</p>')
    A(df("Proposition 1", "R12 by construction",
         'If every gate of a layer is assigned a distinct trap (§5), then no round places two gates in one '
         'trap, so R12 (one gate per trap per cycle) holds for every emitted cycle. <i>Proof.</i> A round '
         'contains at most one pulse per gate, and gates have pairwise distinct traps. ∎ Consequently a layer '
         'with <var>r</var> gates costs max<sub><var>g</var></sub> ℓ(<var>g</var>) pulse cycles rather than '
         '∑<sub><var>g</var></sub> ℓ(<var>g</var>).',
         'Gates in the same layer fire in lock-step: the first pulse of every gate together, then the second '
         'pulse of every gate, and so on. Because each gate is in its own trap, this never asks one trap to '
         'do two things in a cycle, so a layer of many gates takes as long as one gate.'))
    wa = next(g for g in pc["gates"] if g["dag"] == 2)
    wb = next(g for g in pc["gates"] if g["dag"] == 3)
    A(eg(f'Layer 1 of circuit 1 holds two CNOTs, one at {esc(wa["site"])} and one at {esc(wb["site"])}. Each is five '
         'pulses. Read the diagram column by column: every column is one instruction and one machine cycle, '
         'carrying the same-numbered pulse of both gates. Instruction #6 is a single two-ion instruction naming '
         'two pairs at two traps. Five cycles for two gates.'
         + fig(rounds_svg(wa, wb, 5, f"cx q0,q1 at {wa['site']}", f"cx q2,q3 at {wb['site']}"), "round emission for layer 1: columns are cycles, rows are gates; orange is the two-ion pulse")
         + '<p>The same happens in layer 0, where the two Hadamards share one beam instruction and one frame '
         'instruction (#2, #3), and in layer 2, where the two measurements share one readout instruction '
         '(#17), since measurements do not claim traps:</p>'
         + listing_excerpt(par["tsir"], pc, [2, 3, 5, 6, 7, 8, 9, 17])))
    A('<p>Measurements and resets do not claim traps (R12 bounds gates), so a layer emits one readout '
      'instruction naming every ion it measures; a barrier orders and costs nothing. The scheme forgoes '
      'pipelining across layers: transport for the next layer starts only after the pulses of this one. '
      '§10 reports what that costs against an exact solver.</p>')

    # ================================================================ 5. site
    A('<h3 id="algo-site">5. Site selection</h3>'
      '<p>Let pos be the current positions, <var>K</var> the traps already claimed in this layer, and '
      'occ(<var>t</var>) = |pos<sup>−1</sup>(<var>t</var>)| the number of ions in a trap.</p>')
    A(df("Definition 6", "meeting trap",
         'For a two-qubit gate on ions <var>i</var>, <var>j</var> at <var>s</var><sub><var>i</var></sub>, '
         '<var>s</var><sub><var>j</var></sub>,'
         '<div class="eq">site(<var>g</var>) = argmin { <var>d</var>(<var>s</var><sub><var>i</var></sub>, <var>t</var>) + '
         '<var>d</var>(<var>s</var><sub><var>j</var></sub>, <var>t</var>) : <var>t</var> ∈ gate ∖ <var>K</var>, '
         'room(<var>t</var>) ≥ 2 },</div>'
         'where room(<var>t</var>) = cap(<var>t</var>) − occ(<var>t</var>) + |{<var>s</var><sub><var>i</var></sub>, '
         '<var>s</var><sub><var>j</var></sub>} ∩ {<var>t</var>}|. Ties are broken by document order. A '
         'single-qubit gate happens where its ion stands if that trap can gate and is unclaimed, else at '
         'the nearest such trap with room; a measurement or reset at the nearest trap that can measure, '
         'with no claim. If the set is empty the operation is reported <i>unrealised</i>.',
         'A two-qubit gate happens in the trap that can gate, is not already taken by another gate of this '
         'layer, has room for both ions once the ions already parked in it are counted, and is closest to '
         'the pair, counting both ions\' journeys. Among equals, the first in the document wins.'))
    bi = bc["init"]
    A(eg(f'The Bell pair on the register: q0 starts at {esc(bi["q0"])}, q1 at {esc(bi["q1"])}. Both C0 and C1 '
         f'are a total of one hop from the pair; C0 comes first in the document, so it is the site, and q0 is '
         f'the ion that travels. Had the order been the other way round, q1 would have travelled instead: the '
         f'tie rule is arbitrary but fixed, and the checker does not mind which.'
         + dev_fig("chain4", "candidates for the Bell CNOT: the number under each trap is d(q0, t) + d(q1, t); C0 is chosen",
                   ions=ions_of(bi), hi={bc["gates"][1]["site"]},
                   marks={t: (f"{c_dist[bi['q0']][t]} + {c_dist[bi['q1']][t]} = {c_dist[bi['q0']][t] + c_dist[bi['q1']][t]}",
                              GREEN if t == bc["gates"][1]["site"] else GREY) for t in c_sites})))
    g3 = next(g for g in pc["gates"] if g["dag"] == 4)
    pos_before = replay(pc, {}, 9)
    assert g3["site"] == "T0_0v" and pos_before == {"q0": "T0_1h", "q1": "T0_1h", "q2": "T0_0h", "q3": "T0_0h"}, pos_before
    occ = {}
    for ion, s in pos_before.items():
        occ[s] = occ.get(s, 0) + 1
    si, sj = pos_before["q1"], pos_before["q2"]
    marks, crossed = {}, set()
    for t in g_sites:
        room = g_cap[t] - occ.get(t, 0) + sum(1 for s in (si, sj) if s == t)
        ssum = g_dist[si][t] + g_dist[sj][t]
        if room < 2:
            crossed.add(t)
            marks[t] = (f"{ssum} hops, full", RED)
        else:
            marks[t] = (f"{ssum} hops, room {room}", GREEN if t == g3["site"] else GREY)
    A(eg('The third CNOT of circuit 1, <code>cx q1,q2</code>, at the start of layer 2. After layer 1, q0 and q1 '
         'both stand at T0_1h and q2, q3 at T0_0h; nobody went home, because every ion is used again in '
         'layer 2 (Definition 7). The two traps the operands stand in are nearest by distance but are full: '
         'two ions each, which is their capacity, and only one of the two is an operand, so they are struck '
         'out. Among the rest, the smallest total is two hops, reached by T0_0v and T1_0v; T0_0v comes first '
         'in the document. So the pair meets in the trap between them, one hop each.'
         + dev_fig("grid2x3", "candidates for cx q1,q2: total hops for the two operands and the room left; the full traps are struck out, T0_0v is chosen",
                   ions=ions_of(pos_before), hi={g3["site"]}, crossed=crossed, marks=marks,
                   paths=[{"nodes": ["T0_1h", "J0_1", "T0_0v"], "color": BLUE, "label": "q1"}, {"nodes": ["T0_0h", "J0_0", "T0_0v"], "color": ORANGE, "label": "q2"}])
         + listing_excerpt(par["tsir"], pc, [10, 11, 12, 13])))
    A(df("Definition 7", "undocking with one-layer lookahead",
         'After the pulses of Λ<sub><var>k</var></sub>, the set '
         '<var>R</var><sub><var>k</var></sub> = { <var>i</var> : <var>i</var> an operand of a two-qubit gate of '
         'Λ<sub><var>k</var></sub>, <var>i</var> ∉ ions(Λ<sub><var>k</var>+1</sub>), pos(<var>i</var>) ≠ init(<var>i</var>) } '
         'is routed back to init by the layer router of §6; an ion that cannot be routed home stays and '
         'is noted.',
         'When a layer is done, every ion that gated in it and is not needed by the very next layer is sent '
         'back to where it started. That keeps the few gate-capable traps of a device free for the gates '
         'still to come, and the one-layer lookahead stops the rule from sending an ion home only to fetch '
         'it straight back.'))
    sw = by.get("swap")
    swc = sw["cert"] if sw else None
    A(eg('In circuit 2, the GHZ chain, each layer holds one CNOT and the next layer needs one of its ions: '
         'after <code>cx q0,q1</code> at C2, q1 is wanted by <code>cx q1,q2</code> and q0 by its measurement, so '
         'nobody moves back, and likewise at every layer. The whole circuit costs three hops, one per CNOT. '
         'Without the lookahead q1 would have gone back to C2 and set out again.'
         + dev_fig("chain4", "circuit 2: the three moves of the whole programme, one per CNOT; the ions stay where they gated",
                   ions=ions_of(init2),
                   paths=[{"nodes": [m["from"], m["to"]], "color": [BLUE, GREEN, ORANGE][k], "label": f"{m['ion']} (cycle {m['t']})"} for k, m in enumerate(gc["moves"])])
         + (('<p>The <a href="#swap">swap</a> card above shows the other case: on the ring q1 rides '
             f'{len([m for m in swc["moves"] if m["t"] < swc["gates"][0]["t"]])} hops to the dock A0, the three CNOTs of the swap fire, and since '
             f'nothing follows, q1 is returned along the same {len([m for m in swc["moves"] if m["t"] > swc["gates"][0]["t"]])} hops to its starting trap.</p>'
             + dev_fig("ring6d", "the swap: q1 rides to A0 (blue) and, with nothing left to do, home again (grey)",
                       ions=ions_of(swc["init"]),
                       paths=[{"nodes": [m["from"], m["to"]], "color": BLUE if m["t"] < swc["gates"][0]["t"] else GREY, "dashed": m["t"] > swc["gates"][0]["t"]} for m in swc["moves"]]))
            if swc else "")))

    # ================================================================ 6. routing
    A('<h3 id="algo-route">6. Routing within a layer</h3>')
    A(df("Problem", "layer routing",
         'Given pos: <var>I</var> → <var>T</var>, a set of movers <var>M</var> ⊆ <var>I</var> with targets '
         'τ: <var>M</var> → <var>T</var>, and a horizon <var>H</var>, find for each <var>i</var> ∈ <var>M</var> a '
         'walk in the time-expanded graph <var>G</var><sub><var>T</var></sub> × {0, …, <var>H</var>} (a hop or a '
         'wait per cycle) from (pos(<var>i</var>), 0) to (τ(<var>i</var>), <var>t</var><sub><var>i</var></sub>) such '
         'that, at every cycle, the constraints listed below hold jointly for the movers and the stationary '
         'ions <var>I</var> ∖ <var>M</var>; minimise the makespan max <var>t</var><sub><var>i</var></sub>.',
         'Some ions must get from where they are to the trap their gate was assigned; every other ion '
         'stands still and is in the way. Find each mover a route, one hop per cycle with waiting allowed, '
         'so that no rule is broken at any cycle, and finish as early as possible. This is multi-agent path '
         'finding, with the twist that ions share traps up to a capacity and that, on a broadcast-wired '
         'device, everything moving in one cycle must move the same way.'))
    A('<p>The solver is prioritised planning with a space-time reservation table (cooperative A*): '
      'movers are planned one at a time in a priority order; each plan is found by A* over states '
      '(<var>t</var>, <var>τ</var>) with actions <i>wait</i> and <i>hop</i>, cost 1 per cycle and the admissible '
      'heuristic <var>h</var>(<var>t</var>) = <var>d</var>(<var>t</var>, goal); a finished plan reserves what it uses, '
      'and later movers plan around it. A hop <var>h</var> departing <var>t</var> at cycle <var>τ</var> is '
      'admissible iff all of the following hold, each of which is one rule of the Rules page:</p>'
      '<pre class="alg">'
      'occ(<var>t′</var>, τ+1) &lt; cap(<var>t′</var>)                                  R1   the destination has room\n'
      '∀ <var>j</var> ∈ junc(<var>h</var>):  transits(<var>j</var>, τ) &lt; 1                     R2   one transit per junction per cycle\n'
      '∀ <var>e</var> ∈ via(<var>h</var>):   load(<var>e</var>, τ) &lt; cap(<var>e</var>)                  R3   segment capacity\n'
      '(<var>t′</var>, <var>t</var>, τ) not reserved                                   R5   no head-on exchange on a segment\n'
      'one action per ion per cycle                                 R8\n'
      'act(<var>h</var>) = (ℓ, δ) ⇒ act(ℓ, τ) ∈ {⊥, δ}                          R4d, R11   one signed direction per loop per cycle\n'
      'wave(τ) ∈ {⊥, sig(<var>h</var>)}      (broadcast devices only)         R22  one waveform per cycle\n'
      '</pre>'
      '<p>The <i>waveform signature</i> sig(<var>h</var>) is the movement class followed by one label per '
      'segment crossed: along a named loop the signed unit step on that loop; across a spur, inward or '
      'outward; otherwise the direction of the lab-frame displacement. It is computed by the same '
      'function the verifier uses for R22, so the two agree by construction. The first mover to reserve '
      'a cycle fixes its waveform; any other mover whose hop has a different signature waits. Arrival is '
      'not sufficient: an ion parks at its goal for the rest of the layer, so the goal must have room at '
      'every later cycle too.</p>')

    def sig(src, dst):
        via, _ = g_hops[src][dst]
        return lab_signature(mg, via, src)
    s_q0, s_q2 = sig("T0_1v", "T0_1h"), sig("T0_0v", "T0_0h")
    s_q1b, s_q2b = sig("T0_1h", "T0_0v"), sig("T0_0h", "T0_0v")
    assert s_q0 == s_q2 and s_q1b != s_q2b, (s_q0, s_q2, s_q1b, s_q2b)
    A(eg('Layer 1 of circuit 1 on the space-time grid: rows are traps, columns are cycle boundaries, and '
         'each arrow is one hop. q0 and q2 must move (blue, orange); q1 and q3 stand still at the two goals '
         '(grey). Both movers descend a column and then turn right, the same waveform, so both hops fit in '
         'cycle 0 as one instruction (#4). The makespan, 1, equals the lower bound (the longest single '
         'distance), so no other priority order is even tried.'
         + fig(spacetime_svg(["T0_1v", "T0_1h", "T0_0v", "T0_0h"], 1,
                             {"q0": (["T0_1v", "T0_1h"], BLUE), "q2": (["T0_0v", "T0_0h"], ORANGE), "q1": (["T0_1h", "T0_1h"], GREY), "q3": (["T0_0h", "T0_0h"], GREY)},
                             [s_q0.replace("shuttle ", "")], {"q0", "q2"}),
               "layer 1: two hops with the same waveform share cycle 0")
         + '<p>Layer 2 is the instructive case. q1 and q2 both head for T0_0v, both one hop away, and the trap '
         'has room for both. But q1 turns left then down, and q2 turns left then up: two different waveforms. '
         'q1 is planned first and fixes the waveform of cycle 0, so q2 waits a cycle and hops in cycle 1. '
         'Every priority order gives two cycles here, so all four are tried and the first is kept, '
         'instructions #10 and #11. Nothing physical prevents the two hops sharing a cycle; the cost is '
         'R22, one waveform per cycle on a broadcast-wired machine.</p>'
         + fig(spacetime_svg(["T0_1h", "T0_0h", "T0_0v"], 2,
                             {"q1": (["T0_1h", "T0_0v", "T0_0v"], BLUE), "q2": (["T0_0h", "T0_0h", "T0_0v"], ORANGE), "q0": (["T0_1h", "T0_1h", "T0_1h"], GREY), "q3": (["T0_0h", "T0_0h", "T0_0h"], GREY)},
                             [s_q1b.replace("shuttle ", ""), s_q2b.replace("shuttle ", "")], {"q1", "q2"}),
               "layer 2: different waveforms, so q2 waits one cycle")
         + listing_excerpt(par["tsir"], pc, [4, 10, 11])))
    A(df("Algorithm 4", "layer routing",
         '<pre class="alg">'
         'LB ← max<sub><var>i</var> ∈ <var>M</var></sub> <var>d</var>(pos(<var>i</var>), τ(<var>i</var>));   <var>H</var> ← 2·ecc(<var>G</var><sub><var>T</var></sub>) + 8\n'
         '<b>for</b> rung <b>in</b> ⟨(uniform, strict), (¬uniform, strict), (¬uniform, tolerant)⟩\n'
         '   <b>for</b> <var>H′</var> <b>in</b> ⟨<var>H</var>, 2<var>H</var>⟩            (the third rung tries <var>H</var> only)\n'
         '      best ← ⊥\n'
         '      <b>for</b> order <b>in</b> ⟨farthest first, nearest first, program order, its reverse⟩\n'
         '         reserve every stationary ion at its trap for cycles 0..<var>H′</var>\n'
         '         <b>for</b> <var>i</var> <b>in</b> order:  plan <var>i</var> by A*; <b>if</b> none, <b>next</b> order;  reserve its walk and its parking\n'
         '         lay the hops out by departure cycle;  <b>if</b> ¬uniform, split each cycle by signature\n'
         '         replay the emitted cycles against R1;  <b>if</b> strict and a trap overflows, <b>next</b> order\n'
         '         <b>if</b> |cycles| &lt; |best| <b>then</b> best ← cycles;   <b>if</b> |best| ≤ LB <b>then return</b> best\n'
         '      <b>if</b> best ≠ ⊥ <b>then return</b> best\n'
         '<b>raise</b> Unroutable\n'
         '</pre>'
         'Rungs 1 and 2 guarantee R1 on what is emitted; rung 3 exists so that a layer no arrangement '
         'can make both uniform and legal still yields a programme, for the verifier to report.',
         'Try four different orders of planning the movers and keep the plan with the fewest cycles, '
         'stopping early if a plan is provably as short as possible. If nothing routes within the search '
         'horizon, double it once. If the strict one-waveform search fails, relax it in two steps. Before '
         'anything is emitted, replay it and refuse a plan that would overfill a trap.'))
    A(eg('Why the order matters, on a loop of eight slots. Ion a must go from slot 0 to slot 3 and ion b '
         'from slot 2 to slot 1. Left: a is planned first, walks 0→1→2→3 and parks at 3; b waits two cycles '
         'for a to pass and steps to 1. Makespan 3, which equals the lower bound, so the search stops there. '
         'Right: b is planned first, steps to slot 1 at once and parks there for the whole layer; a now finds '
         'slot 1 blocked at every cycle and must go the long way round. Makespan 5. No single order avoids '
         'this kind of trap on every layer, which is why four are compared. On a racetrack the exact solver '
         'of §10 once found a layer where the single order then in use cost 43 cycles against 5.'
         + fig(loop_order_svg(), "the same two movers on an eight-slot loop, planned in the two orders"), "Example (a small loop)"))
    A('<p><b>Failure is a result.</b> When Algorithm 4 raises, or §5 leaves an operation unrealised, the '
      'compiler tries again with the next placement of Algorithm 3; when all three fail on a device with a '
      'closed loop and docks, §7 is tried; otherwise the compiler declines with the reason. No branch '
      'emits a programme that violates a rule it knows about.</p>')

    # ================================================================ 7. conveyor
    A('<h3 id="algo-rotate">7. The conveyor pass</h3>')
    A(df("Definition 8", "conveyor device",
         'A closed loop ℓ ∈ <var>L</var> with rail slots <var>r</var><sub>0</sub>, …, <var>r</var><sub><var>N</var>−1</sub> '
         'and a set of <i>docks</i> <var>D</var>, each a gate-capable trap joined to one rail slot by one spur '
         'segment. A <i>rider</i> <var>i</var> has a fixed slot σ(<var>i</var>) ∈ ℤ<sub><var>N</var></sub> and the loop an '
         'offset θ ∈ ℤ<sub><var>N</var></sub>; the rider stands at <var>r</var><sub>σ(<var>i</var>)+θ</sub>. A '
         '<i>rotation</i> by δ is one instruction that sets θ ← θ + δ, costing |δ| hops per rider.',
         'Think of the loop as a conveyor belt with a few workstations (the docks) beside it. Ions riding '
         'the belt keep their places on it; one instruction advances the whole belt by any number of '
         'slots, and it needs no empty slot to do so, because everything moves together.'))
    ri = rc["init"]
    snaps = [replay(rc, r_loops, t) for t in (1, 11, 20)]
    assert snaps[1]["q2"] == "S0" and snaps[2]["q3"] == "S0", snaps
    A(eg('The six-site ring is a conveyor with a belt of six slots and two docks. Circuit 3 has one ancilla, '
         'q0, and three data qubits. The pass parks q0 at dock A0 and spreads the three riders two slots '
         'apart. Each turn of the belt by two slots, backwards, brings the next rider in the circuit\'s '
         'order to S0, beside the dock: q2, then q3.'
         + '<div class="strip">'
         + dev_fig("ring6d", "start: q1 is beside the dock", ions=ions_of(snaps[0]), hi={"A0"})
         + dev_fig("ring6d", "after rotating by −2: q2 is beside the dock", ions=ions_of(snaps[1]), hi={"A0"})
         + dev_fig("ring6d", "after another −2: q3 is beside the dock", ions=ions_of(snaps[2]), hi={"A0"})
         + '</div>'))
    A(df("Proposition 2", "domain of rigid rotation",
         'Rotation preserves the cyclic order of the riders, so two riders never share a trap. Hence a '
         'circuit is compilable by rotation alone only if its interaction graph <var>W</var> is bipartite, '
         '<var>Q</var> = <var>Q</var><sub><var>D</var></sub> ⊔ <var>Q</var><sub><var>L</var></sub>, with '
         '|<var>Q</var><sub><var>D</var></sub>| ≤ |<var>D</var>| parked at docks and '
         '|<var>Q</var><sub><var>L</var></sub>| ≤ <var>N</var> riding. The pass two-colours <var>W</var> and '
         'refuses, naming a conflicting pair, otherwise.',
         'Two ions on the belt can never meet, so every gate must pair a rider with a docked ion. That works '
         'exactly when the qubits split into two groups with all gates running between the groups, one '
         'group small enough for the docks: the shape of a syndrome-extraction round, ancillas docked and '
         'data riding. It is also why the general router\'s limit, about half occupancy on a loop, does not '
         'apply here.'))
    A(eg('Circuit 3\'s interaction graph is a star: q0 in the middle, three gates out to q1, q2, q3. Two '
         'colours suffice, with q0 alone on the dock side (one ancilla, two docks available) and the three '
         'data qubits riding (three riders, six slots). Circuit 2 is bipartite too, but the pass refuses it '
         'on this ring for another reason: it measures every qubit, and a rider can never be measured, since '
         'only the docks can. The compiler\'s message when it tried was "measure acts on q1, which rides the '
         'loop; only docked ions can gate". A triangle of CNOTs, <code>cx a,b; cx b,c; cx c,a</code>, fails the '
         'first condition: whichever two ride the belt can never meet, and the pass says which.'
         + fig(interaction_svg({(0, 1): 1, (0, 2): 1, (0, 3): 1}, 4), "the interaction graph of circuit 3: a star, with q0 the ancilla on every gate")))
    A('<p><b>Layout.</b> Docks are assigned to <var>Q</var><sub><var>D</var></sub> in order and riders spread at '
      'stride ⌊<var>N</var>/|<var>Q</var><sub><var>L</var></sub>|⌋, unless a layout is supplied from outside, as '
      'the code-aware leaderboard entries do; a supplied layout is validated (each dock exists, no slot '
      'or dock named twice, the partition respected) and otherwise treated as data.</p>'
      '<p><b>Schedule.</b> Operations are emitted by <i>readiness</i>: <var>o</var> is ready when every '
      'earlier <var>o′</var> sharing a qubit with it is emitted, commuting or not. In words: every qubit '
      'sees its own operations in the circuit\'s order, and only operations on different qubits move '
      'relative to each other, which is what lets contacts of different ancillas share one batch. The '
      'order of a qubit\'s gates is the circuit author\'s to choose, so the compiler keeps it even where '
      'two CNOTs commute: one that shares only its control, or only its target, with another would give '
      'the same unitary in either order, which is exactly why no semantic check could tell, and why the '
      'order conjunct of §9 has no exemption for it.</p>')
    A(df("Algorithm 5", "greedy sweep",
         '<pre class="alg">'
         '<b>while</b> operations remain\n'
         '   drain: emit every ready measurement, reset and single-qubit gate on a docked ion   (no rotation needed)\n'
         '   for each ready contact <var>c</var> = (rider <var>i</var>, dock <var>D</var><sub><var>c</var></sub>):  θ<sub><var>c</var></sub> ← rail(<var>D</var><sub><var>c</var></sub>) − σ(<var>i</var>)  (mod <var>N</var>)\n'
         '   θ* ← the θ<sub><var>c</var></sub> with the largest batch (ties: nearest, either way)      [monotone variant: nearest ahead]\n'
         '   <var>B</var> ← a set of ready contacts at θ*, at most one per dock and one per rider\n'
         '   emit rotate(θ* − θ, the shorter way);  dock(<var>B</var>);  the CNOT rounds of <var>B</var> together;  undock(<var>B</var>)\n'
         '</pre>'
         'Invariant: the loop never turns while a dock is occupied. In the certificate a rotation is one '
         'event, and the checker\'s replay applies it to every ion on the loop and to no other.',
         'First do everything that needs no turn: measure, reset and single-qubit gates on docked ions. '
         'Then work out, for each gate that is ready, how far the belt would have to turn to bring its '
         'rider beside its dock, turn to the position where the most ready gates line up (the nearest, '
         'either way, when it is a tie), and do every ready gate there: pull the riders into their docks, '
         'fire, push them back out. The belt never turns while a rider is docked, since a docked ion is '
         'off the belt and would come back beside the wrong slot. Since every qubit\'s gates stay in the '
         'circuit\'s order, an ancilla has one gate ready at a time, and turning the shorter way to it '
         'costs fewer turns and fewer hops than always turning forward, which would lap the belt for each '
         'one; the forward-only rule is still there as an option.'))
    rots = rc["rotations"]
    assert [r["delta"] for r in rots] == [-2, -2] and [g["dag"] for g in rc["gates"]] == [0, 1, 2, 3, 4], rc["gates"]
    A(eg('Circuit 3, traced. The dock hangs on slot 0, so a rider at slot s needs a turn of −s: q1 needs '
         'no turn, q2 (at slot 2) needs −2 (or +4 the long way), q3 (at slot 4) −4 (or +2). The three '
         'CNOTs share q0, so they become ready one at a time, in the circuit\'s order, although they '
         'commute. The sweep serves q1 without turning, then <code>cx q0,q2</code> with a turn of −2; that '
         'turn carries q3 from slot 4 to slot 2, so <code>cx q0,q3</code> needs −2 more: two turns of two, '
         'four hops in all, three contacts, exactly as the compiler\'s note reads. Taking '
         '<code>cx q0,q3</code> first is not an option: the order of q0\'s gates is the circuit\'s.'
         + tbl(["step", "ready", "turn", "emitted"],
               [["drain", "<code>h q0</code> (q0 is docked)", "—", "beam and frame update, #2–#3"],
                ["1", "cx q0,q1", "none: q1 is already beside the dock", "dock q1, five rounds, undock: #4–#10"],
                ["2", "cx q0,q2 (needs −2)", "−2", "rotate (#11), dock q2, rounds, undock: #12–#18"],
                ["3", "cx q0,q3 (now needs −2)", "−2 more", "rotate (#19), dock q3, rounds, undock: #20–#26"],
                ["drain", "<code>h q0</code>, <code>measure q0</code>", "—", "#27–#29"]])
         + listing_excerpt(rot["tsir"], rc, [4, 6, 10, 11, 12, 14, 18, 19, 20, 22, 26])))

    # ================================================================ 8. cooling
    A('<h3 id="algo-cool">8. Cooling insertion</h3>'
      '<p>Let <var>n̄</var><sub><var>i</var></sub>(<var>t</var>) be the motional quanta of ion <var>i</var> at cycle '
      '<var>t</var> under the heating model (each hop, split and merge adds a table value; a global cool '
      'sets every <var>n̄</var><sub><var>i</var></sub> to zero). R7 requires, for every two-qubit gate <var>g</var> '
      'at cycle <var>t</var>, max<sub><var>i</var> ∈ <var>g</var></sub> <var>n̄</var><sub><var>i</var></sub>(<var>t</var>) ≤ '
      'budget. The pass replays <var>P</var>, inserts a global cool immediately before each violating gate, '
      'and replays once more.</p>')
    A(df("Proposition 3", "one pass suffices",
         'After a single pass no gate violates R7. <i>Proof sketch.</i> Inserting a cool before cycle '
         '<var>t</var> sets every <var>n̄</var><sub><var>i</var></sub>(<var>t</var>) to zero and, heating being additive '
         'and non-negative, does not increase <var>n̄</var><sub><var>i</var></sub>(<var>t′</var>) for any '
         '<var>t′</var> &gt; <var>t</var>; hence a gate that satisfied R7 before still does, and a gate that '
         'violated it now starts from zero. ∎',
         'Moving an ion warms it up, and a warm ion makes a worse two-qubit gate. The pass walks through '
         'the programme with a thermometer, and wherever a two-qubit gate would fire too hot it inserts a '
         'cooling step just before it. Cooling is global, so one step cools every ion; that is why one '
         'walk is enough, and the pass checks the result by walking through again.'))
    cools = [i for i in par["tsir"]["instructions"] if i["type"] == "cool" and (i.get("meta") or {}).get("trigger")]
    before = [i["meta"].get("before_instruction") for i in cools]
    A(eg(f'Circuit 1 leaves the compiler with {len(par["raw"]["instructions"])} instructions and the cooling pass returns '
         f'{len(par["tsir"]["instructions"])}. The strip below is the cooled programme in order: the two red-bordered boxes '
         f'are the cooling steps the pass inserted, each immediately before a two-ion pulse (instructions #{before[0]} '
         f'and #{before[1]}), whose operands the replay had found above the budget after their hops. The first '
         'cooling step, at #1, is state preparation and is always there. The pulses and moves are untouched, '
         'so the certificate still applies: a cool moves nothing.'
         + fig(strip_svg(par["tsir"]["instructions"]), "the cooled programme of circuit 1: orange boxes are two-ion pulses, blue beams, green moves; the cools marked R7 were inserted by the pass")))

    # ================================================================ 9. correctness
    A('<h3 id="algo-correct">9. Correctness</h3>'
      '<p>The compiler is outside the trusted base. Conditions (i)–(iii) of the Problem are decided by '
      'three independent checkers, each reading only (<var>A</var>, <var>C</var>, <var>P</var>, <var>κ</var>). '
      'The checker for (ii) is proved correct (Theorem 2); the checkers for (i) and (iii) are '
      'executable and not proved (Checks 1 and 2). The gate rewrite rests on proved identities.</p>')
    A(df("Theorem 1", "pulse identities, proved",
         '<div class="eq">u3(θ, φ, λ) = VZ(φ + λ) · R(θ, π/2 − λ),</div>'
         '<div class="eq">CX = e<sup>−iπ/4</sup> (R<sub>y</sub>(−π/2) ⊗ I)(R<sub>x</sub>(−π/2) ⊗ R<sub>x</sub>(−π/2)) '
         'MS(π/2) (R<sub>y</sub>(π/2) ⊗ I),</div>'
         'where R(θ, φ) is a resonant single-ion pulse, VZ a frame update (no laser, no duration) and '
         'MS(θ) the two-ion Mølmer–Sørensen interaction. Every supported gate is a u3 and every '
         'composite reduces to these two.',
         'Any single-qubit gate is one laser pulse plus a bookkeeping change of phase that costs nothing; a '
         'CNOT is one two-ion pulse with four single-ion pulses around it. Both facts are theorems, checked '
         'by a proof assistant, and the table is also tested numerically. The global phase is kept because it '
         'becomes visible once the gate is controlled, and a wrong phase convention is exactly the kind of '
         'error that hides until then.'))
    A(eg('The Bell pair\'s Hadamard is u3(π/2, 0, π), which becomes one pulse and one frame update; its '
         'witness lists them in that order. The frame update costs no cycle, but it is not nothing: a '
         'compiler that dropped it as free would emit the pulse alone, which is not a Hadamard. The CNOT\'s '
         'five pulses are the second identity read from right to left.'
         + fig(rounds_svg(hw, bw, None, "h q0: 1 pulse + 1 frame update", "cx q0,q1: 5 pulses"),
               "the Bell pair's two gates as pulse sequences, read from their witnesses: blue is a single-ion pulse, grey a frame update, orange the two-ion pulse")))

    A(df("Check 1", "legality",
         'The verifier of the Rules page, which shares no code with the router, replays the cooled '
         'programme from its starting positions and decides every rule of the rule set.',
         'The same rule checker the studio runs on any hand-written programme is run on the compiled one, '
         'and it does not know or care how the programme was made. A router bug shows up as a failed rule on '
         'this page, not as a programme a machine would refuse.'))
    rr = par["rules"]
    A(eg(f'For circuit 1 the verifier reports {len(rr.get("passed", []))} rules passed and none failed (the badge on its card in §11). '
         'Suppose the router had let q1 and q2 hop in the same cycle of layer 2, which §6 showed it must not: the '
         'replay would find two waveforms in one transport instruction and report R22 failed, with the instruction '
         'number, here on this page.'))
    A(df("Definition 9", "Implements",
         'For a certificate <var>κ</var> over (<var>A</var>, <var>C</var>), with hops <var>H</var><sub><var>A</var></sub>, '
         'gate-capable traps gate<sub><var>A</var></sub> and loop orders loops<sub><var>A</var></sub> extracted from '
         '<var>A</var>, and pos<sub><var>t</var></sub> the replay of Definition 4, Implements is the conjunction of:'
         '<ol class="pf">'
         '<li>unrealised(<var>κ</var>) = ∅;</li>'
         '<li><var>μ</var> is injective and total on <var>Q</var>;</li>'
         '<li>the events are sorted by cycle, so the replay is well defined;</li>'
         '<li>every rotation names a loop in loops<sub><var>A</var></sub>;</li>'
         '<li>∀ <var>m</var> ∈ moves: (<var>m</var>.src, <var>m</var>.dst) ∈ <var>H</var><sub><var>A</var></sub> and '
         'pos<sub><var>m</var>.<var>t</var></sub>(<var>m</var>.ion) = <var>m</var>.src;</li>'
         '<li>∀ <var>g</var> ∈ gates: <var>g</var>.site ∈ gate<sub><var>A</var></sub> and ∀ <var>i</var> ∈ <var>g</var>.ions: '
         'pos<sub><var>g</var>.<var>t</var></sub>(<var>i</var>) = <var>g</var>.site;</li>'
         '<li>∀ <var>g</var> ∈ gates: the ions <var>g</var> names are, in some order, <var>μ</var> of the '
         'qubits of the operation it names;</li>'
         '<li>every operation of <var>C′</var> other than measure, reset and barrier has exactly one witness;</li>'
         '<li>every witness names an operation of <var>C′</var>;</li>'
         '<li>for operations <var>o</var> before <var>o′</var> sharing a qubit, the witness cycle of <var>o</var> '
         'precedes that of <var>o′</var>, whether or not the two commute.</li>'
         '</ol>',
         'Nothing was left out; each qubit has its own ion; the moves can be replayed in order; every '
         'rotation is of a real loop; no ion ever teleports (every move is a real hop, starting from where '
         'the ion actually is); every claim names exactly the ions its operation\'s qubits live on, and finds '
         'them together in a trap that can gate; every '
         'gate of the circuit is claimed exactly once, and every claim names an operation of the circuit; and '
         'gates on a common qubit happen in the order written. A claim names an operation, never the gate '
         'applied there, so a claim on a barrier, a measurement or a reset passes too, and which gate fires '
         'is for Check 2 to decide.'))
    shifted = [dict(bm, t=bw["t"] + 1)]
    A(eg('The Bell certificate from Definition 4, replayed twice. Left, as the compiler wrote it: both witness '
         'columns are green, because at cycle 2 q0 is at C1 where the Hadamard is claimed, and at cycle 8 both '
         'ions are at C0 where the CNOT is claimed. Right, one number changed: the move delayed from cycle 3 '
         'to cycle 9. Now at cycle 8 the replay still has q0 at C1, the CNOT\'s witness is false, and conjunct '
         '(6) fails. Deleting the Hadamard\'s witness would fail (8), an operation with no witness; sending the '
         'move to C3 instead of C0 would fail (5), since C1–C3 is not a hop. Each is one of the fourteen mutations '
         'of Theorem 2, and each is rejected.'
         + '<div class="two">'
         + fig(timeline_svg(bc, 10, bc["moves"], "as compiled: the move at cycle 3"), "accepted")
         + fig(timeline_svg(bc, 10, shifted, "mutated: the move at cycle 9"), "rejected: at cycle 8 the ions are not together")
         + '</div>'))
    A(df("Theorem 2", "soundness of the checker",
         'The checker is defined as check(<var>κ</var>) := decide(Implements(<var>κ</var>)), and the theorem '
         'check(<var>κ</var>) = true → Implements(<var>κ</var>) has a machine-checked proof that assumes '
         'nothing beyond propositional extensionality. The extraction of '
         '<var>H</var><sub><var>A</var></sub>, gate<sub><var>A</var></sub> and loops<sub><var>A</var></sub> from '
         '<var>A</var> is performed by code the compiler never executes. Fourteen mutations of real certificates '
         '(dropped, duplicated and shifted witnesses, a teleported move, a wrong departure, an ungateable site, '
         'aliased qubits, an operation marked unrealised, unsorted events, reordered and swapped dependent '
         'gates, two swapped CNOTs that commute, a witness naming other ions than its operation\'s, swapped '
         'operands) are rejected in practice: thirteen by this checker, and the fourteenth, swapped operands, '
         'which moves no ion and names the same two ions, by Check 2.',
         'The checker is nothing but a decision procedure for Definition 9, and a machine-checked proof says '
         'that whenever it answers yes, the definition really holds. So the whole claim lives in the '
         'definition, which a reader can judge. The facts about the device that the definition refers to '
         '(which traps gate, which pairs are one hop apart, how each loop is ordered) are read out of the '
         'document by code the compiler never runs, so the compiler cannot make the device bigger by '
         'saying so; that is what makes the guarantee hold for any device.'))
    A(eg('What "any device" buys. Suppose a compiler, by a bug, believed the loop site S2 of the ring could '
         'gate, and emitted a contact there; its witness would name S2, the checker\'s list of gate-capable '
         'traps is {A0, A3}, and conjunct (6) fails. Suppose instead it believed S0 and S3 were adjacent and '
         'moved an ion across in one cycle: that pair is not a hop of the ring, and (5) fails. The compiler '
         'cannot argue with either, because nothing it writes is consulted for the device facts.'
         + dev_fig("ring6d", "two things a wrong compiler might claim on the ring, both refused: a gate at S2 (not a gate trap), a hop S0→S3 (not adjacent)",
                   crossed={"S2"}, paths=[{"nodes": ["S0", "S3"], "color": RED, "label": "not a hop", "dashed": True}], hi={"A0", "A3"})))
    A(df("Check 2", "semantics",
         'Let <var>U</var>(<var>P</var>) be the unitary of the pulses of <var>P</var>, read out of the emitted programme '
         'and composed through <var>μ</var>, and <var>U</var>(<var>C</var>) that of the source circuit. For a Clifford '
         '<var>C</var> the checker compares stabilizer tableaux, T(<var>U</var>(<var>P</var>)) = T(<var>U</var>(<var>C</var>)); '
         'otherwise, for <var>n</var> ≤ 10 (one more qubit for each measurement or reset in the middle of '
         'the circuit), it compares <var>U</var>(<var>P</var>) with <var>U</var>(<var>C</var>), the matrix of the '
         'source circuit, each gate at its defining matrix, up to global phase; for a larger circuit outside '
         'the Clifford gates, (iii) is reported as not checked. The comparison is executable; it is not '
         'proved.',
         'Finally, does the programme compute the right thing? The checker reads the pulses back out of the '
         'programme and works out what they do to the qubits, then compares with what the circuit does. For '
         'Clifford circuits this is done with stabilizer tableaux, which scale; otherwise the full matrix is '
         'compared, up to ten qubits. A wrong angle, a dropped frame update, a swapped control and target, '
         'or one gate compiled as another all change the answer. It is the only check that sees which gate '
         'the pulses make.'))
    A(eg('The Bell circuit turns the two generators of |00⟩ into X⊗X and Z⊗Z, top row. The checker rebuilds '
         'the same map from the seven emitted pulses and finds it equal; the card says "O2: ok". Two wrong '
         'programmes, in red: with control and target exchanged, the second generator stays I⊗Z; with the '
         'Hadamard\'s frame update dropped, the pulse alone sends Z to −X and the first generator comes out as '
         '−X⊗X, only a sign, but a different tableau and a different Bell state. Circuits 1, 2 and 3 are all '
         'Clifford and are checked this way; the Toffoli is not, and its card says the exact unitary was compared.'
         + fig(tableau_svg(), "the stabilizer generators through the Bell circuit, and the two mutations")))
    A('<p>Together: Check 1 gives (i), Theorem 2 gives (ii), Check 2 gives (iii), and Theorem 1 is why '
      'the compiler\'s pulse table can '
      'be right in the first place; that the pulses of <var>P</var> are the gates of <var>C</var> is what '
      'Check 2 establishes. The page\'s '
      'drawing is derived from the same certificate: the studio joins the programme to the circuit through '
      'the per-instruction stamps and refuses to draw if a witness disagrees with the stamp on the '
      'instruction it names.</p>')

    # ================================================================ 10. limits
    A('<h3 id="algo-limits">10. Limitations and measured gaps</h3>'
      '<p><b>Optimality.</b> Nothing above certifies minimality. The router\'s layer instances were re-solved '
      'exactly by a SAT encoding of the same problem on the same trap graph; the heuristic was optimal '
      'on every instance of the lattice and cyclone devices and lost on a racetrack, where a single '
      'priority order had cost 43 cycles against 5, which is the origin of the four orders in '
      'Algorithm 4. The placement relaxation of Algorithm 2 was predicted to dominate Algorithm 1 and '
      'did not; the 2-swap descent on Φ is what won, by up to 12 %.</p>'
      '<p><b>Scope.</b> Routing is planned within a layer; cross-layer pipelining is not attempted. '
      'R10 is reported <i>partial</i> for a non-Clifford circuit of more than ten qubits, where neither a '
      'tableau nor a dense unitary is available. Operations on three or more qubits are lowered, never '
      'executed as such. The general router runs out at about 46 % loop occupancy, and the conveyor '
      'pass covers only bipartite circuits. Whether the source circuit is the intended one (for a '
      'syndrome round, whether it measures the intended stabilizers) is outside R10 and is tested '
      'upstream. The physical model behind R7 and R16 is the subject of the '
      '<a href="../physics/">Physics</a> page.</p>')
    A(eg('Circuit 1 shows the pipelining limit. At the end of layer 1, q1 and q2 are already known to be '
         'heading for T0_0v, and q2 could have set off while the last three beams of layer 1 fired on q0 and '
         'q1; instead the layer\'s transport begins after its last pulse. A cross-layer scheduler could '
         'overlap two cycles here, and proportionally more on a long chain. The checker would accept such a '
         'programme just as it accepts this one; what stops it is the compiler\'s structure, not the rules.'))
    A('<p><b>Background.</b></p>'
      '<ul class="refs">'
      '<li>Quadratic assignment and its hardness: Sahni and Gonzalez, 1976. Spectral ordering by the second Laplacian eigenvector: Fiedler, 1973.</li>'
      '<li>Prioritised planning with space-time reservations (cooperative A*): Silver, 2005. Hardness of multi-agent path finding: Yu and LaValle, 2013.</li>'
      '<li>The Mølmer–Sørensen gate and the CNOT-from-MS identity in the trapped-ion literature; the identity is re-proved here under one fixed phase convention.</li>'
      '</ul>')

    # ================================================================ 11. the cards
    A('<h3 id="algo-examples">11. The four circuits, compiled and verified</h3>'
      '<p class="sub">The examples quoted above, through the same pipeline and the same checks as every '
      'gate: the input, the programme it became, the ion mapping, the pulses witnessed, and the '
      'verdicts. Press Play, or step the machine.</p>')
    A(_gate_row(par, "ex/algo_par.html", head="Circuit 1: two Bell pairs, then joined"))
    A(_gate_row(ghz, "ex/algo_ghz.html", head="Circuit 2: a GHZ chain"))
    A(_gate_row(rot, "ex/algo_rot.html", head="Circuit 3: a parity check, by rotation"))
    A(_gate_row(ccx, "ex/algo_ccx.html", head="A Toffoli, lowered"))
    A("</div>")
    return "".join(body)
