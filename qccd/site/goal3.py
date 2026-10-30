"""Goal 3 on the Leaderboard: one code at a time, every architecture (board/goal3/).

For each fixed code (goals/goal3/README.md) two charts: time per round and logical error rate
per round, one column per architecture family.  Each family's best point by that measure is in
colour and named; every other size is grey.  The points are the reference sweep's
(goals/goal3/results: every family at every size, compiled by the pinned reference compiler and
graded like an entry, minus the Lean step) and the official entries on the code's release,
fetched live from /official/v1/goals/goal3 and drawn as diamonds that open their entry page.

The page is made of the site's own parts: the Leaderboard's "Try your own design" button for
the selected code (leaderboard.board_block), and the user's rule for Leaderboard pages: one
line per section, details in fold-outs.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOAL = ROOT / "goals" / "goal3"

_e = html.escape


def _families():
    """goals/goal3/families.py: the family order and each family's caveat."""
    sys.path.insert(0, str(GOAL))
    try:
        import families
        return list(families.FAMILIES), dict(families.CAVEATS)
    finally:
        sys.path.remove(str(GOAL))


def _size(r: dict) -> float:
    p = r.get("params") or {}
    return p["a"] * p["b"] if r.get("generator") == "grid" else 2 * p.get("width", 0) + p.get("verticals", 0)


def data() -> dict | None:
    """Every Goal 3 release with its reference points, or None when this build has none."""
    try:
        from ..workspace.tasks import list_boards
    except Exception:                       # the workspace package is optional for a site build
        return None
    rels = sorted((b for b in list_boards() if b.manifest.get("goal") == "goal3"),
                  key=lambda b: b.manifest.get("order", 0))
    if not rels:
        return None
    fams, caveats = _families()
    # every point's own studio page (goals/goal3/pages.py), served under board/goal3/p/
    pf = GOAL / "pages.json"
    pages = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    codes = []
    for b in rels:
        task = b.manifest["task"]
        code = task.removeprefix("goal3_").removesuffix("_mem")
        spec = b.detectors() or {}
        rows: dict = {}
        f = GOAL / "results" / f"{code}.jsonl"
        for ln in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
            if ln.strip():
                r = json.loads(ln)
                rows[(r["family"], r["point"])] = r        # the last line of a point wins
        counts = {fam: {"tried": 0, "failed": 0} for fam in fams}
        pts, failed = [], []
        mine = pages.get(code, {})
        for (fam, name), r in sorted(rows.items(), key=lambda kv: _size(kv[1])):
            c = counts.setdefault(fam, {"tried": 0, "failed": 0})
            c["tried"] += 1
            u = {"u": "p/" + mine[name]["page"]} if name in mine else {}
            if r.get("status") != "ok":
                c["failed"] += 1
                why = ("breaks " + ", ".join(r["rules_failed"]) + ": " if r.get("rules_failed") else "") + (r.get("why") or "")
                failed.append({"f": fam, "n": name, "why": why[:240], **u})
                continue
            ler = r["ler"]
            pts.append({"f": fam, "n": name, "s": _size(r), "t": round(r["T_round_ms"], 4),
                        "l": ler["per_round"], "lo": ler["per_round_lo"], "hi": ler["per_round_hi"],
                        "e": ler["errors"], "sh": ler["shots"], **u})
        name, short = b.title.removeprefix("Goal 3: "), _short(b.title)
        codes.append({"task": task, "id": b.id, "title": name, "short": short, "about": b.manifest.get("description", ""),
                      "ions": (b.manifest.get("qec") or {}).get("n_ions"), "rounds": spec.get("rounds"),
                      "counts": counts, "points": pts, "failed": failed})
    return {"families": fams, "caveats": caveats, "codes": codes}


def _short(title: str) -> str:
    t = title.removeprefix("Goal 3: ")
    d = t.rsplit("distance ", 1)[-1]
    kind = {"surface": "Surface", "colour": "Colour", "bicycle": "BB", "hypergraph": "HGP"}
    return next((v for k, v in kind.items() if k in t), t.split(",")[0]) + f" d={d}"


CSS = """
.g3-tabs{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 10px;padding:0;list-style:none}
.g3-tabs button{font:600 13.5px/1 var(--sans);padding:8px 12px;border:1px solid var(--line);border-radius:999px;
 background:var(--paper);color:var(--ink2);cursor:pointer}
.g3-tabs button[aria-selected=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.g3-tabs button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.g3-code{margin:0 0 6px;color:var(--ink2);font:14px/1.5 var(--sans)}
.g3-panels{display:grid;grid-template-columns:1fr;gap:14px}
.g3-panel{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:10px 10px 4px;position:relative}
.g3-panel h3{font:600 13.5px/1.3 var(--sans);color:var(--head);margin:2px 4px 4px}
.g3-panel svg{display:block;width:100%;height:auto;overflow:visible}
.g3-panel .ax{stroke:#c3c2b7;stroke-width:1}
.g3-panel .gr{stroke:var(--grid);stroke-width:1}
.g3-panel text{font:11px var(--sans);fill:var(--ink2)}
.g3-panel text.lab{fill:var(--ink);font-size:11px;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round}
.g3-panel text.fam{fill:var(--ink);font-weight:600;font-size:11.5px}
.g3-panel .pt{cursor:default}
.g3-panel .pt.go{cursor:pointer}
.g3-panel .pt.go:focus{outline:none}
.g3-panel .pt.go:focus .hit{stroke:var(--accent);stroke-width:2}
.g3-panel .hit{fill:transparent}
.g3-key{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 2px 0;font:12.5px/1.4 var(--sans);color:var(--ink2)}
.g3-key svg{vertical-align:-2px;margin-right:5px}
.g3-tip{position:fixed;z-index:50;pointer-events:none;background:var(--paper);border:1px solid var(--line);
 border-radius:8px;box-shadow:0 6px 18px rgba(20,20,30,.12);padding:8px 10px;font:12.5px/1.45 var(--sans);
 color:var(--ink);max-width:300px;display:none}
.g3-tip b{display:block;font-size:13px}
.g3-tip .m{color:var(--ink2)}
.g3 details{margin:12px 0 0;font:14px/1.55 var(--sans);color:var(--ink2)}
.g3 details summary{cursor:pointer;color:var(--accent);font-weight:600}
.g3 table{width:100%;border-collapse:collapse;margin:8px 0;font:13px/1.4 var(--sans)}
.g3 th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink2);padding:4px 8px;
 border-bottom:1px solid var(--line)}
.g3 td{padding:5px 8px;border-bottom:1px solid var(--grid);color:var(--ink)}
.g3 td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.g3 .tw{overflow-x:auto}
.g3 .lbx{margin-top:14px}
"""

SCRIPT = r"""
(function(){
  var D = __DATA__, API = '/official/v1';
  var ACC = '#1f5bb5', GREY = '#8f8e88';
  var root = document.getElementById('g3'), tabs = document.getElementById('g3-tabs');
  var tip = document.createElement('div'); tip.className = 'g3-tip'; document.body.appendChild(tip);
  var live = {}, cur = 0;
  function E(tag, attrs, text){ var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
  function H(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function sci(v){ if (!(v > 0)) return '0'; var e = Math.floor(Math.log10(v)), m = v / Math.pow(10, e);
    return m.toFixed(2) + '×10' + sup(e); }
  function sup(n){ var s = '⁰¹²³⁴⁵⁶⁷⁸⁹'; return String(n).replace('-', '⁻').replace(/\d/g, function(d){ return s[+d]; }); }
  function ms(v){ return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2); }
  function pts(c){ return c.points.map(function(p){ return {f: p.f, n: p.n, s: p.s, t: p.t, l: p.l, lo: p.lo, hi: p.hi,
      e: p.e, sh: p.sh, u: p.u, ref: true}; }).concat(live[c.id] || []); }
  // a point's own page: a reference size's studio page here, an entry's on the official server
  function href(p){ return p.sid ? API + '/submissions/' + encodeURIComponent(p.sid) + '/page' : (p.u || null); }
  function famsOf(c, ps){ var have = {}; ps.forEach(function(p){ have[p.f] = 1; });
    return D.families.filter(function(f){ return (c.counts[f] && c.counts[f].tried) || have[f]; })
      .concat(have.Other && D.families.indexOf('Other') < 0 ? ['Other'] : []); }

  function panel(host, c, key, title){
    var ps = pts(c), fams = famsOf(c, ps), W = 560, Hh = 330, L = 58, R = 12, T = 12, B = 64;
    var vals = [];
    ps.forEach(function(p){ vals.push(p[key]); if (key === 'l') { vals.push(p.lo > 0 ? p.lo : p.l); vals.push(p.hi); } });
    vals = vals.filter(function(v){ return v > 0; });
    var box = H('div', 'g3-panel'); box.appendChild(H('h3', null, title)); host.appendChild(box);
    var svg = E('svg', {viewBox: '0 0 ' + W + ' ' + Hh, role: 'img', 'aria-label': title + ', ' + c.title});
    box.appendChild(svg);
    if (!vals.length) { svg.appendChild(E('text', {x: W / 2, y: Hh / 2, 'text-anchor': 'middle'}, 'No design ran yet.')); return; }
    var lo = Math.log10(Math.min.apply(null, vals)), hi = Math.log10(Math.max.apply(null, vals));
    var pad = Math.max(0.08, (hi - lo) * 0.08); lo -= pad; hi += pad;
    function y(v){ return T + (Hh - T - B) * (1 - (Math.log10(v) - lo) / (hi - lo)); }
    var colW = (W - L - R) / fams.length, labels = [];
    function xc(i){ return L + colW * (i + 0.5); }
    // grid + y ticks: decades, and 2 and 5 between them for time or a range under two decades
    var subs = (key === 't' || hi - lo < 2) ? [1, 2, 5] : [1];
    for (var d = Math.floor(lo); d <= Math.ceil(hi); d++) {
      subs.forEach(function(m){
        var v = m * Math.pow(10, d), lv = Math.log10(v); if (lv < lo || lv > hi) return;
        svg.appendChild(E('line', {x1: L, x2: W - R, y1: y(v), y2: y(v), 'class': 'gr'}));
        svg.appendChild(E('text', {x: L - 6, y: y(v) + 3.5, 'text-anchor': 'end'},
          key === 't' ? String(+v.toPrecision(3)) : (m === 1 ? '' : m + '×') + '10' + sup(d)));
      });
    }
    svg.appendChild(E('line', {x1: L, x2: L, y1: T, y2: Hh - B, 'class': 'ax'}));
    svg.appendChild(E('line', {x1: L, x2: W - R, y1: Hh - B, y2: Hh - B, 'class': 'ax'}));
    svg.appendChild(E('text', {x: 14, y: T + (Hh - T - B) / 2, transform: 'rotate(-90 14 ' + (T + (Hh - T - B) / 2) + ')',
      'text-anchor': 'middle'}, key === 't' ? 'ms per round' : 'logical error rate per round'));
    fams.forEach(function(f, i){
      var mine = ps.filter(function(p){ return p.f === f; }).sort(function(a, b){ return (a.s || 0) - (b.s || 0); });
      var cnt = c.counts[f] || {tried: 0, failed: 0}, ok = cnt.tried - cnt.failed, sub = mine.filter(function(p){ return !p.ref; }).length;
      svg.appendChild(E('text', {x: xc(i), y: Hh - B + 17, 'text-anchor': 'middle', 'class': 'fam'}, f + (D.caveats[f] ? '*' : '')));
      svg.appendChild(E('text', {x: xc(i), y: Hh - B + 31, 'text-anchor': 'middle'}, cnt.tried ? ok + ' of ' + cnt.tried + ' sizes' : ''));
      svg.appendChild(E('text', {x: xc(i), y: Hh - B + 45, 'text-anchor': 'middle'},
        (cnt.failed ? cnt.failed + ' failed' : '') + (sub ? (cnt.failed ? ', ' : '') + sub + ' submitted' : '')));
      if (!mine.length) return;
      var best = mine.reduce(function(a, b){ return b[key] < a[key] ? b : a; });
      var n = mine.length, span = Math.min(colW * 0.62, 18 * n);
      mine.forEach(function(p, j){
        var x = xc(i) + (n > 1 ? -span / 2 + span * j / (n - 1) : 0), yy = y(p[key]), on = p === best, col = on ? ACC : GREY;
        var to = href(p), g = E('g', {'class': 'pt' + (to ? ' go' : ''), tabindex: to ? 0 : -1});
        if (to) { g.setAttribute('role', 'link'); g.setAttribute('aria-label', p.n + ', ' + p.f + ': open its page'); }
        if (key === 'l' && p.hi > 0) g.appendChild(E('line', {x1: x, x2: x, y1: y(p.lo > 0 ? p.lo : p.l), y2: y(p.hi),
          stroke: col, 'stroke-width': on ? 2 : 1.5, 'stroke-linecap': 'round', opacity: on ? 1 : .7}));
        var r = on ? 6 : 4.5;
        g.appendChild(p.ref ? E('circle', {cx: x, cy: yy, r: r, fill: col, stroke: '#fff', 'stroke-width': 2})
          : E('path', {d: 'M' + x + ' ' + (yy - r - 1.5) + 'l' + (r + 1.5) + ' ' + (r + 1.5) + 'l' + (-r - 1.5) + ' ' + (r + 1.5) + 'l' + (-r - 1.5) + ' ' + (-r - 1.5) + 'z',
                       fill: col, stroke: '#fff', 'stroke-width': 2}));
        g.appendChild(E('circle', {cx: x, cy: yy, r: 11, 'class': 'hit'}));
        g.addEventListener('mouseenter', function(ev){ show(ev, p, c); });
        g.addEventListener('mousemove', move);
        g.addEventListener('mouseleave', hide);
        g.addEventListener('focus', function(){ var b = g.getBoundingClientRect(); show({clientX: b.right, clientY: b.top}, p, c); });
        g.addEventListener('blur', hide);
        if (to) {
          var open = function(){ window.open(to, '_blank', 'noopener'); };
          g.addEventListener('click', open);
          g.addEventListener('keydown', function(ev){ if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
        }
        svg.appendChild(g);
        // the best point's name, to its right unless that runs off the panel
        if (on) { var room = x + 9 + 6.2 * p.n.length <= W - 2;
          labels.push(E('text', {x: room ? x + 9 : x - 9, y: yy + 3.5, 'class': 'lab', 'text-anchor': room ? 'start' : 'end'}, p.n)); }
      });
    });
    labels.forEach(function(t){ svg.appendChild(t); });
  }
  function show(ev, p, c){
    tip.textContent = '';
    tip.appendChild(H('b', null, p.n));
    tip.appendChild(H('div', 'm', p.f + (p.ref ? ' · reference compile' : ' · submitted' + (p.by ? ' by ' + p.by : ''))));
    tip.appendChild(H('div', null, ms(p.t) + ' ms per round'));
    tip.appendChild(H('div', null, sci(p.l) + ' per round' + (p.hi ? '  (' + sci(p.lo) + ' to ' + sci(p.hi) + ')' : '')));
    if (p.e != null) tip.appendChild(H('div', 'm', p.e + ' logical errors in ' + Number(p.sh).toLocaleString() + ' shots'));
    if (href(p)) tip.appendChild(H('div', 'm', 'Click to open its page: the ions moving, the program against the circuit, the rules and the noise.'));
    tip.style.display = 'block'; move(ev);
  }
  function move(ev){ var w = tip.offsetWidth, h = tip.offsetHeight, x = ev.clientX + 14, yy = ev.clientY + 14;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14; if (yy + h > innerHeight - 8) yy = ev.clientY - h - 14;
    tip.style.left = x + 'px'; tip.style.top = yy + 'px'; }
  function hide(){ tip.style.display = 'none'; }

  function table(host, c){
    var ps = pts(c), fams = famsOf(c, ps), tw = H('div', 'tw'), t = H('table');
    var hd = H('tr'); ['family', 'fastest', 'ms / round', 'lowest LER', 'LER / round (95%)'].forEach(function(s){ hd.appendChild(H('th', null, s)); });
    t.appendChild(hd);
    fams.forEach(function(f){
      var mine = ps.filter(function(p){ return p.f === f; }), tr = H('tr');
      tr.appendChild(H('td', null, f));
      if (!mine.length) { ['none ran', '', '', ''].forEach(function(s){ tr.appendChild(H('td', null, s)); }); t.appendChild(tr); return; }
      var ft = mine.reduce(function(a, b){ return b.t < a.t ? b : a; }), fl = mine.reduce(function(a, b){ return b.l < a.l ? b : a; });
      tr.appendChild(named(ft)); tr.appendChild(H('td', 'n', ms(ft.t)));
      tr.appendChild(named(fl)); tr.appendChild(H('td', 'n', sci(fl.l) + '  (' + sci(fl.lo) + ' to ' + sci(fl.hi) + ')'));
      t.appendChild(tr);
    });
    tw.appendChild(t); host.appendChild(tw);
  }
  function named(p){ var td = H('td'), to = href(p);
    if (!to) { td.textContent = p.n; return td; }
    var a = H('a', null, p.n); a.href = to; a.target = '_blank'; a.rel = 'noopener'; td.appendChild(a); return td; }
  // the sizes that failed, each with its reason; one whose program broke a rule opens its page
  function failures(host, c){
    var fs = c.failed || [];
    host.parentNode.hidden = !fs.length;
    if (!fs.length) return;
    var tw = H('div', 'tw'), t = H('table'), hd = H('tr');
    ['family', 'size', 'why'].forEach(function(s){ hd.appendChild(H('th', null, s)); }); t.appendChild(hd);
    fs.forEach(function(p){ var tr = H('tr'); tr.appendChild(H('td', null, p.f)); tr.appendChild(named(p));
      tr.appendChild(H('td', null, p.why)); t.appendChild(tr); });
    tw.appendChild(t); host.appendChild(tw);
  }

  function draw(i){
    cur = i; var c = D.codes[i];
    [].forEach.call(tabs.querySelectorAll('button'), function(b, j){ b.setAttribute('aria-selected', j === i ? 'true' : 'false'); });
    [].forEach.call(document.querySelectorAll('.g3-try'), function(el){ el.hidden = el.getAttribute('data-task') !== c.task; });
    document.getElementById('g3-code').textContent = c.ions + ' ions: ' + c.rounds + ' rounds of a Z memory, then ' +
      c.rounds + ' of an X memory on the same ions.';
    document.getElementById('g3-about').textContent = c.about;
    var pan = document.getElementById('g3-panels'); pan.textContent = '';
    panel(pan, c, 't', 'Time per syndrome round');
    panel(pan, c, 'l', 'Logical error rate per round (Z and X memories)');
    var tb = document.getElementById('g3-table'); tb.textContent = ''; table(tb, c);
    var fl = document.getElementById('g3-failed'); fl.textContent = ''; failures(fl, c);
    if (location.hash !== '#' + c.task) history.replaceState(null, '', '#' + c.task);
  }
  D.codes.forEach(function(c, i){
    var b = H('button', null, c.short); b.type = 'button'; b.setAttribute('role', 'tab');
    b.setAttribute('data-hint', 'goal3:code'); b.onclick = function(){ draw(i); }; tabs.appendChild(b);
  });
  var start = Math.max(0, D.codes.findIndex(function(c){ return '#' + c.task === location.hash; }));
  draw(start);
  // the official entries on each code's release, as diamonds; the page stands without them
  if (typeof fetch === 'function') fetch(API + '/goals/goal3', {headers: {'Accept': 'application/json'}}).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function(g){
    (g.releases || []).forEach(function(rel){
      live[rel.id] = (rel.entries || []).filter(function(e){ return e.T_round_ms > 0 && e.ler_per_round > 0; }).map(function(e){
        var l = e.ler || {};
        return {f: e.family, n: e.device || e.display_name || e.id, by: e.by, s: 0, t: e.T_round_ms, l: e.ler_per_round,
                lo: l.per_round_lo || e.ler_per_round, hi: l.per_round_hi || e.ler_per_round, e: l.errors, sh: l.shots, sid: e.id};
      });
    });
    draw(cur);
  }, function(){});
})();
"""


def page(PAGE: str, STYLE: str) -> str | None:
    """board/goal3/index.html, or None when this build has no Goal 3 release."""
    d = data()
    if d is None:
        return None
    from . import leaderboard
    cav = "".join(f"<li><b>{_e(f)}*</b> {_e(t)}.</li>" for f, t in d["caveats"].items())
    tries = "".join(f'<div class="g3-try" data-task="{_e(c["task"])}" hidden>'
                    + leaderboard.board_block(c["task"], c["title"], live=False, root="../../").replace(
                        "Coloured dots are submitted designs", "Diamonds are submitted designs") + "</div>"
                    for c in d["codes"])
    fams = ("<tr><td>Grid</td><td>a junction lattice, a trap on every wire, every electrode its own DAC</td></tr>"
            "<tr><td>WISE</td><td>the same lattice with broadcast DACs and a switch per site</td></tr>"
            "<tr><td>C2LR</td><td>the same lattice as a swap-or-stay device (Yb-Ba crystals, the demonstrated "
            "diagonals)</td></tr>"
            "<tr><td>Cyclone</td><td>one junction-free loop of traps, moved rigidly</td></tr>"
            "<tr><td>Conveyor</td><td>a loop of rail slots with dock spurs, rotated rigidly, ions docked to gate</td></tr>")
    body = (
        "<h1>Goal 3: one code at a time, every architecture</h1>"
        '<p class="sub">For each fixed QEC code, every architecture family at every size its rules allow. '
        "Each family&rsquo;s best point is in colour; every other size is grey.</p>"
        '<details class="about"><summary>About this goal</summary>'
        "<p>Goal 1 ranks every code and every machine together, which leaves too many dimensions free to be one "
        "question. Goal 3 fixes the code and sweeps one axis, the architecture: which family runs the code&rsquo;s "
        "memory fastest, and which with the lowest logical error rate. A memory is the code&rsquo;s Z memory and then "
        "its X memory on the same ions, d rounds each, so a round fails when either memory does.</p>"
        "<p>Every point is graded on qccd-physics@2 and qccd-noise@2, the physics and noise of Goal 1, with the "
        "<code>quantinuum</code> time table and every rule. The circles are the reference sweep: each family at every "
        "size, compiled by the pinned reference compiler (qccdc b4a003dbb4ae) and graded like an entry, without the "
        "Lean certificate step. The diamonds are entries submitted to the official server, which ran every check. "
        "Click any point to open its page: the ions moving on the device, the program against the circuit, the "
        "rules, and the noise model&rsquo;s stim program block by block.</p>"
        f"<div class=\"tw\"><table><tr><th>family</th><th>the device</th></tr>{fams}</table></div></details>"
        '<div class="g3" id="g3">'
        '<div class="g3-tabs" id="g3-tabs" role="tablist" aria-label="Code"></div>'
        '<p class="g3-code" id="g3-code"></p>'
        '<details><summary>About this code</summary><p id="g3-about"></p></details>'
        '<div class="g3-panels" id="g3-panels"></div>'
        '<div class="g3-key">'
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="#1f5bb5"/></svg>best in its family</span>'
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="4" fill="#8f8e88"/></svg>every other size</span>'
        '<span><svg width="12" height="12"><path d="M6 0.5l5.5 5.5-5.5 5.5-5.5-5.5z" fill="#8f8e88"/></svg>'
        "submitted entry</span>"
        "<span>Bars: 95% interval</span><span>Click any point to open its page</span></div>"
        '<details><summary>Each family&rsquo;s best, as a table</summary><div id="g3-table"></div></details>'
        '<details><summary>The sizes that failed, and why</summary><div id="g3-failed"></div></details>'
        f'<details><summary>What the starred families cannot show yet</summary><ul>{cav}</ul>'
        "<p>A size counts as failed when the reference compiler refused it or its program broke a rule.</p></details>"
        f"{tries}</div>"
    )
    js = SCRIPT.replace("__DATA__", json.dumps(d, separators=(",", ":")).replace("</", "<\\/"))
    hints = "<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, " + json.dumps(HINTS) + ");</script>"
    out = PAGE.format(title="Goal 3: one code at a time, every architecture - QCCD studio", style=STYLE,
                      extra_css=CSS + leaderboard.CSS, body=body)
    return out.replace("</body>", hints + f"<script>{js}</script>" + leaderboard.script() + "</body>", 1)


#: what each control on the page does, for the hover card and the agent (data-hint)
HINTS = {"goal3:code": {"t": "Code", "d": "Show the two charts of this fixed QEC code: every architecture family at "
                                          "every size it was run at, and the submitted entries."}}
