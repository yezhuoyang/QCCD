"""The Leaderboard as a place to take part: "Try your own design" on every board, each board's
community submissions live with who submitted them, and the page where a person lets their own
workspace submit in their name.

    board_block(task, title)   one board's "Try your own design" button and its live community list,
                               placed in the board's own section of /board/ (seed boards and memory
                               boards alike)
    SCRIPT / CSS               fill every such block from the official service (/official/v1/),
                               newest data each visit; the popover the button opens
    connect_page(PAGE, STYLE)  /connect/: a signed-in person allows ONE workspace's sign-in code
                               (qccd/official/accounts.py), and sees and revokes the workspaces that
                               can submit as them, and what they submitted

Why a button needs a popover on the public site: the design, the agent and the grading run on the
person's own computer (a QCCD workspace), which this page cannot reach.  So "Try your own design"
offers the link into that workspace (127.0.0.1:47100, the port its website mirror always takes,
which forwards to the workspace's Studio with the board in focus) and says how to set one up.  On
the workspace's own copy of the site (`window.QCCD_MIRROR`) it goes straight there.

Every control here is a link, a fold-out, or a button declared in QCCD_HINTS; the /connect/ page's
Allow and Revoke are the PERSON's (data-qccd-private): an agent reading the page through a
workspace cannot press them, and in the workspace's copy the page is only a pointer to the site.
"""

from __future__ import annotations

import html
import json
import re

__all__ = ["board_block", "SCRIPT", "CSS", "HINTS", "connect_page", "board_ids", "script", "with_submissions", "goal_page", "goal_info", "goal_table"]

LOCAL_STUDIO = "http://127.0.0.1:47100/studio"

CSS = """
.lbx{margin:14px 0 4px;padding:12px 14px;border:1px solid #d7e4f6;border-radius:12px;background:#f6f9fe}
.lbx-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.lbx-try{font:600 14px/1 var(--sans,system-ui,sans-serif);padding:9px 16px;border-radius:999px;border:1px solid #1f5bb5;
 background:#1f5bb5;color:#fff;cursor:pointer}
.lbx-try:hover{background:#174a97}
.lbx-top .lbx-t{font:600 12px/1.3 var(--sans,system-ui,sans-serif);letter-spacing:.06em;text-transform:uppercase;color:#56545e}
.lbx-live table{width:100%;border-collapse:collapse;font:13.5px/1.4 var(--sans,system-ui,sans-serif);margin:8px 0 2px}
.lbx-live th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#56545e;padding:4px 8px;
 border-bottom:1px solid #e0e6ef}
.lbx-live td{padding:5px 8px;border-bottom:1px solid #edf1f6}
.lbx-live td.n{text-align:right;font-variant-numeric:tabular-nums}
.lbx-live .lbx-by{font-weight:600;color:#1a2540}
.lbx-live .lbx-empty,.lbx-live .lbx-wait{color:#6b6a66;font:13.5px/1.5 var(--sans,system-ui,sans-serif);margin:6px 0 0}
.lbx-seedby{font:12.5px/1.4 var(--sans,system-ui,sans-serif);color:#6b6a66;margin:4px 0 0}
.lbx-bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:10px 0 0;font:13px/1.4 var(--sans,system-ui,sans-serif);color:#56545e}
.lbx-bar select{font:inherit;padding:4px 8px;border:1px solid #cfceca;border-radius:6px;background:#fff;color:#1d1d1b;max-width:100%}
.lbx-tw{overflow-x:auto}.lbx-tw td{white-space:nowrap}
.lbx-live td.lbx-links a{margin-right:10px;white-space:nowrap}
#lbx-pop{position:absolute;z-index:1200;width:min(420px,calc(100vw - 24px));background:#fff;border:1px solid #cfceca;
 border-radius:12px;box-shadow:0 14px 36px rgba(0,0,0,.18);padding:14px 16px;font:14px/1.5 var(--sans,system-ui,sans-serif);
 color:#1d1d1b}
#lbx-pop h4{margin:0 26px 6px 0;font-size:16px}
#lbx-pop p{margin:6px 0}
#lbx-pop .lbx-go{display:inline-block;margin:6px 8px 2px 0;padding:8px 14px;border-radius:8px;background:#1f5bb5;color:#fff;
 text-decoration:none;font-weight:600}
#lbx-pop .lbx-go.alt{background:#fff;color:#1f5bb5;border:1px solid #bcd4f2}
#lbx-pop .lbx-small{font-size:12.5px;color:#56545e}
#lbx-pop .lbx-x{position:absolute;top:6px;right:8px;border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:#6b6a66}
"""

HINTS = {
    "board:try": ("Try your own design", "design a device for this board in your own QCCD workspace (with your AI agent "
                  "if you like), test it there with the reference checks, and submit it under your name"),
    "board:try-close": ("Close", "close the Try your own design box"),
    "board:code": ("Show this code", "show or hide one code's entries in Goal 1's chart and table; one code alone "
                   "gives a link that opens the chart on it"),
    "board:rank": ("Rank by", "rank the submitted designs by another of the board's measures; the board's own "
                   "ranking (the logical error rate per round on a memory board) is the default"),
}


def board_ids() -> dict:
    """task (a board's section id on /board/) -> the official release id and title the server ranks."""
    try:
        from ..workspace.tasks import list_boards
        return {b.manifest["task"]: {"id": b.id, "title": b.title} for b in list_boards()}
    except Exception:                       # the workspace package is optional for a site build
        return {}


def board_block(task: str, title: str, seed_note: bool = False, *, live: bool = True, root: str = "../") -> str:
    """One board's "Try your own design" and its live community list (filled by SCRIPT).  On the
    board's own ranking page (`live=False`) the submissions are dots in its chart instead
    (chart_block), and the block is only the button; `root` is the site's root relative to the
    page.  Short on purpose (the user, 2026-10-21: the Leaderboard had too much text)."""
    t = html.escape(task)
    seed = '<p class="lbx-seedby">Bars: the study&rsquo;s designs. Below: submissions, credited.</p>' if seed_note else ""
    body = (f'<div class="lbx-live" data-board="{t}"><p class="lbx-wait">Loading the official ranking&hellip;</p></div>'
            if live else '<p class="lbx-seedby">Coloured dots are submitted designs, each credited.</p>')
    return (f'<div class="lbx" data-board="{t}"><div class="lbx-top">'
            f'<button class="lbx-try" type="button" data-board="{t}" data-title="{html.escape(title)}" '
            f'data-root="{html.escape(root)}" data-hint="board:try">Try your own design</button>'
            f'<span class="lbx-t">{"Community submissions" if live else "Submit a design"}</span></div>'
            f'{body}{seed}</div>')


SCRIPT = r"""
(function(){
  var API = '/official/v1', BOARDS = __BOARDS__, LOCAL = __LOCAL__, CHARTS = __CHARTS__;
  function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function fmt(v){ return (typeof v !== 'number') ? '-' : (v !== 0 && Math.abs(v) < 1e-3) ? v.toExponential(2)
    : Number.isInteger(v) ? String(v) : (Math.abs(v) >= 100 ? v.toFixed(1) : v.toPrecision(4)); }
  var NICE = {ler_per_round_per_logical: 'logical error rate per round per logical qubit',
              ler_per_round: 'logical error rate per round', ler: 'logical error rate per shot',
              T_quantinuum: 'time, quantinuum table', T_jones: 'time, jones table', T_transport: 'time, transport table',
              instructions: 'instructions', transport_instructions: 'transport instructions', dacs: 'DACs',
              ions_per_instruction: 'ions per transport instruction'};
  function nice(m){ return NICE[m.name] || m.name.replace(/_/g, ' '); }
  function unitOf(m){ return (m.unit && m.unit !== 'count' && !/^per /.test(m.unit)) ? m.unit : ''; }
  function day(t){ return t ? new Date(t * 1000).toISOString().slice(0, 10) : '-'; }
  function get(path){ return fetch(API + path, { headers: { 'Accept': 'application/json' } }).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }); }
  // "Try your own design": into the person's own workspace, focused on this board
  function studioURL(task){ return (window.QCCD_MIRROR ? '/studio' : LOCAL) + '?board=' + encodeURIComponent(task); }
  var pop = null;
  function closePop(){ if (pop) { pop.remove(); pop = null; } }
  function openPop(btn){
    var task = btn.getAttribute('data-board'), title = btn.getAttribute('data-title') || task;
    if (window.QCCD_MIRROR) { location.assign(studioURL(task)); return; }
    closePop();
    pop = el('div'); pop.id = 'lbx-pop'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Try your own design');
    var x = el('button', 'lbx-x', '×'); x.type = 'button'; x.setAttribute('aria-label', 'close'); x.setAttribute('data-hint', 'board:try-close');
    x.onclick = closePop; pop.appendChild(x);
    pop.appendChild(el('h4', null, 'Try your own design: ' + title));
    pop.appendChild(el('p', null, 'Design the device in your own QCCD workspace, where your AI agent can help. It opens ' +
      'the Studio on this board: its ranking, a test with every check the server runs, and Submit, which lists your ' +
      'design here under your name once you sign in with your qccd.academy account.'));
    var go = el('a', 'lbx-go', 'Open in my workspace'); go.href = studioURL(task); go.target = '_blank'; go.rel = 'noopener';
    var setup = el('a', 'lbx-go alt', 'Set up a workspace'); setup.href = (btn.getAttribute('data-root') || '../') + 'studio.html?agent#design';
    var p = el('p'); p.appendChild(go); p.appendChild(setup); pop.appendChild(p);
    pop.appendChild(el('p', 'lbx-small', '"Open in my workspace" works while your workspace runs on this computer ' +
      '(qccd studio). Set up once: the Design page’s Agent panel lists the steps.'));
    document.body.appendChild(pop);
    var r = btn.getBoundingClientRect();
    pop.style.left = Math.max(12, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 12)) + 'px';
    pop.style.top = (window.scrollY + r.bottom + 8) + 'px';
  }
  document.addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('.lbx-try');
    if (b) { e.preventDefault(); openPop(b); return; }
    if (pop && !pop.contains(e.target)) closePop();
  });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') closePop(); });
  // one board's submissions, ranked by the reader's choice of its measures (the board's own by
  // default); each opens on the board's ranking page, where it is a dot with its numbers, its
  // animation, its files and how to grade it again
  function fill(box, task, lb){
    box.textContent = '';
    var rows = lb.rows || [];
    if (!rows.length) { box.appendChild(el('p', 'lbx-empty', 'No submissions yet.')); return; }
    var has = function(r, k){ return typeof (r.metrics || {})[k] === 'number'; };
    var ms = (lb.metrics || []).filter(function(m){ return rows.some(function(r){ return has(r, m.name); }); });
    var main = ms.filter(function(m){ return m.name === lb.rank_by; })[0] || {name: lb.rank_by, better: lb.better, unit: ''};
    var bar = el('div', 'lbx-bar'), lab = el('label', null, 'Rank by '), sel = el('select');
    sel.setAttribute('data-hint', 'board:rank');
    ms.forEach(function(m){ var o = el('option', null, nice(m) + (m.name === lb.rank_by ? ' (the board’s ranking)' : ''));
      o.value = m.name; sel.appendChild(o); });
    sel.value = main.name; lab.appendChild(sel); bar.appendChild(lab);
    bar.appendChild(el('span', 'lbx-n', rows.length + (rows.length === 1 ? ' design' : ' designs')));
    var wrap = el('div', 'lbx-tw'); box.appendChild(bar); box.appendChild(wrap);
    function open(r){ return CHARTS.indexOf(task) >= 0 ? task + '/?sel=c-' + encodeURIComponent(r.id)
                                                         : API + '/submissions/' + encodeURIComponent(r.id) + '/page'; }
    function render(){
      var m = ms.filter(function(x){ return x.name === sel.value; })[0] || main, k = m.name;
      var xs = rows.filter(function(r){ return has(r, k); }).sort(function(a, b){
        var d = a.metrics[k] - b.metrics[k]; return m.better === 'high' ? -d : d; });
      var tb = el('table'), hd = el('tr'), cols = ['#', 'design', 'by', nice(m) + (unitOf(m) ? ' (' + unitOf(m) + ')' : '')];
      if (k !== main.name) cols.push(nice(main));
      cols.push('submitted', '');
      cols.forEach(function(c){ hd.appendChild(el('th', null, c)); });
      var th = el('thead'); th.appendChild(hd); tb.appendChild(th);
      var body = el('tbody');
      xs.forEach(function(r, i){
        var tr = el('tr'), td0 = el('td'), a0 = el('a', null, r.display_name || r.id);
        tr.appendChild(el('td', 'n', String(i + 1)));
        a0.href = open(r); td0.appendChild(a0); tr.appendChild(td0);
        tr.appendChild(el('td', 'lbx-by', r.by || '-'));
        tr.appendChild(el('td', 'n', fmt(r.metrics[k])));
        if (k !== main.name) tr.appendChild(el('td', 'n', fmt((r.metrics || {})[main.name])));
        tr.appendChild(el('td', null, day(r.created_at)));
        var td = el('td', 'lbx-links'), sid = encodeURIComponent(r.id);
        [['watch', API + '/submissions/' + sid + '/page'], ['bundle', API + '/submissions/' + sid + '/bundle.zip'],
         ['report', API + '/submissions/' + sid + '/report']].forEach(function(x){
          var a = el('a', null, x[0]); a.href = x[1]; td.appendChild(a); });
        tr.appendChild(td); body.appendChild(tr);
      });
      tb.appendChild(body); wrap.textContent = ''; wrap.appendChild(tb);
    }
    sel.onchange = render; render();
  }
  // each board's community list, live
  var boxes = document.querySelectorAll('.lbx-live[data-board]');
  if (!boxes.length || typeof fetch !== 'function') return;
  get('/tasks').then(function(t){
    var have = {}; ((t && t.tasks) || []).forEach(function(x){ have[x.id] = x; });
    [].forEach.call(boxes, function(box){
      var b = BOARDS[box.getAttribute('data-board')];
      if (!b || !have[b.id]) { box.textContent = ''; box.appendChild(el('p', 'lbx-empty', 'The official server does not rank this board yet.')); return; }
      get('/leaderboard/' + encodeURIComponent(b.id)).then(function(lb){ fill(box, box.getAttribute('data-board'), lb); },
        function(err){ box.textContent = ''; box.appendChild(el('p', 'lbx-empty', 'The official server did not answer (' + err.message + ').')); });
    });
  }, function(err){
    [].forEach.call(boxes, function(box){ box.textContent = ''; box.appendChild(el('p', 'lbx-empty', 'The official server did not answer (' + err.message + ').')); });
  });
})();
"""


def script(charts=()) -> str:
    """The blocks' script; `charts` are the boards that have a ranking page (board/<task>/), where a
    submission opens as a dot in the chart."""
    hints = {k: {"t": t, "d": d} for k, (t, d) in HINTS.items()}
    js = (SCRIPT.replace("__BOARDS__", json.dumps(board_ids())).replace("__LOCAL__", json.dumps(LOCAL_STUDIO))
          .replace("__CHARTS__", json.dumps(sorted(charts))))
    return ("<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, " + json.dumps(hints) + ");</script>"
            f"<script>{js}</script>")


# ------------------------------------------------- the official entries in a board's ranking chart

#: what each bundle file is, in the words an entry's detail panel uses
BUNDLE_FILES = (("device.arch.json", "device"), ("program.tsir.json", "program"),
                ("certified.tsir.json", "certified program"),
                ("certificate.qcert.json", "certificate"), ("manifest.json", "manifest"))

CHART_CSS = """
.dot.sub circle.mark{stroke:#fff}
#tablewrap{overflow-x:auto}
.detail a.def{font-size:11px;margin-left:4px;color:#56545e}
#tablewrap .tbl td:nth-child(-n+4),#tablewrap .tbl th:nth-child(-n+4){text-align:left}
#tablewrap .tbl td:first-child,#tablewrap .tbl th:first-child{text-align:right}
.detail .repro{margin:14px 0 0;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:#fafaf8}
.detail .repro b{display:block;margin-bottom:4px;color:var(--ink)}
.detail .repro pre{margin:6px 0;padding:8px 10px;background:#f1f0ec;border-radius:6px;font:12.5px/1.45 ui-monospace,Consolas,monospace;
 white-space:pre-wrap;overflow-wrap:anywhere}
.detail .files{font-size:13px;margin:4px 0 0}.detail .files a{color:var(--accent);margin-right:4px}
.detail .by{color:#c2185b;font-weight:600}
.legend .code-t{font:inherit;font-size:12.5px;color:var(--ink2);background:none;border:0;padding:2px 4px;cursor:pointer;border-radius:6px}
.legend .code-t:hover{background:#f1f0ec}.legend .code-t.off{opacity:.4;text-decoration:line-through}
details.about{margin:-10px 0 14px;font-size:13px;color:var(--ink2)}details.about summary{cursor:pointer;color:var(--accent)}
details.about p{max-width:80ch;margin:6px 0 0}
"""

CHART_SCRIPT = r"""
(function(){
  // A ranking page's chart (DATA, the chart's own script) takes the official leaderboard's entries,
  // live: a dot each, credited to who submitted it, ranked by any measure the server scores; its
  // detail opens the studio page the server drew, the bundle's files, and the command that grades
  // it again on the reader's computer.  On a board's page they join the QCCD study's designs; on a
  // goal's page (B.goal) they are the whole chart, every code's entries ranked together.
  var B = __BOARD__, API = '/official/v1', FILES = __FILES__;
  if (!B || typeof fetch !== 'function' || typeof DATA === 'undefined' || typeof draw !== 'function') return;
  var FAM = 'submitted to the official leaderboard', COLOR = '#c2185b';
  var PALETTE = ['#c2185b', '#2a78d6', '#1baf7a', '#eb6834', '#4a3aa7', '#b8860b', '#00838f', '#6d4c41',
                 '#8e24aa', '#43a047', '#d84315', '#546e7a'];
  // an official measure -> this chart's (the logical error rate is plotted in units of 1e-4)
  var SCALE = {ler_per_round: ['ler_round_1e4', 1e4], ler: ['ler_shot_1e4', 1e4],
               ler_per_round_per_logical: ['ler_logical_1e4', 1e4]};
  var NAMES = {ler_per_round_per_logical: 'Logical error rate per round per logical qubit (×10⁻⁴)',
               ler_per_round: 'Logical error rate per round (×10⁻⁴)', ler: 'Logical error rate per shot (×10⁻⁴)',
               logical_qubits: 'Size — logical qubits', ions: 'Size — ions',
               ions_per_logical_qubit: 'Size — ions per logical qubit',
               instructions: 'Program — instructions', transport_instructions: 'Program — transport instructions',
               T_quantinuum: 'Total running time — quantinuum table', T_jones: 'Total running time — jones table',
               T_transport: 'Total running time — transport table', dacs: 'Resource — DACs',
               ions_per_instruction: 'Parallelism — ions per transport instruction'};
  // every official measure is a metric of the evaluation the grader reads (goal1.eval and what it
  // includes): its definition, on the Evaluation page, by the metric's name there
  var DEF = {ler_per_round_per_logical: 'ler_per_round_per_logical', ler_per_round: 'ler_per_round', ler: 'ler',
             logical_qubits: 'n_logical', ions: 'n_ions', ions_per_logical_qubit: 'ions_per_logical_qubit',
             instructions: 'instructions', transport_instructions: 'transport_instructions',
             T_quantinuum: 'T_quantinuum', T_jones: 'T_jones', T_transport: 'T_transport', dacs: 'dacs',
             ions_per_instruction: 'ions_per_instruction'};
  function key(name){ return SCALE[name] ? SCALE[name][0] : name; }
  function E(t, c, x){ var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  function day(t){ return t ? new Date(t * 1000).toISOString().slice(0, 10) : '-'; }
  function link(text, href, cls){ var a = E('a', cls || null, text); a.href = href; return a; }
  function say(text){ var el = $('detail'); if (el) { el.className = 'detail empty'; el.textContent = text; } }
  function options(){
    [ysel, xsel].forEach(function(s){
      [].slice.call(s.options).forEach(function(o){ if (o.value !== 'rank') s.removeChild(o); });
      METRICS.forEach(function(m){ var o = E('option', null, m.name); o.value = m.k; s.appendChild(o); });
    });
  }
  var path = B.goal ? '/goals/' + encodeURIComponent(B.goal) : '/leaderboard/' + encodeURIComponent(B.id);
  fetch(API + path, {headers: {'Accept': 'application/json'}}).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function(lb){
    var rows = lb.rows || [];
    if (!rows.length) {
      if (B.goal) say('No entries yet. The first design submitted to one of this goal’s boards that passes every check is #1.');
      return;
    }
    // what a code buys (logical qubits) and what each costs (ions): FTL's target is 60 logical qubits
    // within 2000 ions.  A server that lists them only in each entry's experiment gets them filled in.
    var SIZE = ['logical_qubits', 'ions', 'ions_per_logical_qubit'];
    var TARGET = lb.ftl_target || {logical_qubits: 60, ions: 2000};
    if (B.goal) {
      var have = (lb.metrics || []).map(function(m){ return m.name; });
      [['logical_qubits', 'high'], ['ions', 'low'], ['ions_per_logical_qubit', 'low']].forEach(function(s, i){
        if (have.indexOf(s[0]) < 0) (lb.metrics = lb.metrics || []).splice(1 + i, 0, {name: s[0], better: s[1], unit: ''}); });
      rows.forEach(function(r){
        var e = r.experiment, m = r.metrics = r.metrics || {};
        if (!e || !e.k) return;
        if (m.logical_qubits == null) m.logical_qubits = e.k;
        if (m.ions == null) m.ions = e.ions;
        if (m.ions_per_logical_qubit == null) m.ions_per_logical_qubit = e.ions / e.k;
      });
    }
    // every measure the server scores is one to rank by here
    (lb.metrics || []).forEach(function(m){
      var k = key(m.name); if (metric(k)) return;
      var M = {k: k, name: NAMES[m.name] || m.name.replace(/_/g, ' '), better: m.better === 'high' ? 'high' : 'low',
               def: DEF[m.name] || null,
               unit: (SCALE[m.name] || SIZE.indexOf(m.name) >= 0 || !m.unit || m.unit === 'count') ? '' : m.unit};
      if (SCALE[m.name]) M.nd = 3; else if (m.unit === 'count' || m.name === 'logical_qubits' || m.name === 'ions') M.nd = 0;
      else if (m.name === 'ions_per_logical_qubit') M.nd = 1;
      METRICS.push(M);
    });
    DATA.forEach(function(d){ if (d.compiled && d.numbers && d.numbers.instructions == null && typeof d.instructions === 'number') d.numbers.instructions = d.instructions; });
    var codes = [], relOf = {};
    rows.forEach(function(r, i){
      var n = {};
      Object.keys(r.metrics || {}).forEach(function(name){ var v = r.metrics[name];
        if (typeof v === 'number') n[key(name)] = SCALE[name] ? v * SCALE[name][1] : v; });
      if (r.ler && typeof r.ler.per_round_lo === 'number') { n.ler_round_lo_1e4 = r.ler.per_round_lo * 1e4; n.ler_round_hi_1e4 = r.ler.per_round_hi * 1e4; }
      var name = r.display_name || r.id, code = r.release_title || r.release || '';
      var fam = B.goal ? code : FAM;
      if (codes.indexOf(fam) < 0) { codes.push(fam); relOf[fam] = r.release; FAMHEX[fam] = B.goal ? PALETTE[(codes.length - 1) % PALETTE.length] : COLOR; }
      DATA.push({key: 'c-' + r.id, sid: r.id, community: true, by: r.by || '', when: r.created_at, ler: r.ler || null,
                 place: i + 1, title: name, short: name + ' · ' + (r.by || 'unnamed'), family: fam, compiled: true,
                 code: code, exp: r.experiment || null, numbers: n, marks: [], rules_failed: [], claim: ''});
    });
    var tcols = [key(lb.rank_by)].concat(/^ler_per_round/.test(lb.rank_by) ? ['ler_round_lo_1e4', 'ler_round_hi_1e4'] : []);
    (lb.metrics || []).forEach(function(m){ var k = key(m.name); if (tcols.indexOf(k) < 0) tcols.push(k); });
    if (B.goal) {
      // a goal's chart holds only its entries: the measures none of them has are not offered; the
      // goal's own comes first, and an error rate (orders of magnitude apart) is drawn on a log scale
      var main = key(lb.rank_by);
      var keep = METRICS.filter(function(m){ return DATA.some(function(d){ return val(d, m.k) != null; }); });
      keep.sort(function(a, b){ return (b.k === main) - (a.k === main); });
      keep.forEach(function(m){ if (/^ler_/.test(m.k)) m.log = true; });
      METRICS.length = 0; keep.forEach(function(m){ METRICS.push(m); });
      options();
      ysel.value = (Q.y && metric(Q.y)) ? Q.y : key(lb.rank_by);
      if (Q.x && metric(Q.x)) xsel.value = Q.x;
      logbox.checked = Q.log != null ? Q.log === '1' : !!(metric(ysel.value) || {}).log;
      // every code is a toggle: the chart and the table show the codes that are on (?code=<release> opens one)
      var ALL = DATA.slice(), HIDE = {};
      if (Q.code) codes.forEach(function(f){ if (relOf[f] !== Q.code) HIDE[f] = true; });
      if (codes.every(function(f){ return HIDE[f]; })) HIDE = {};
      var showing = function(){ DATA.length = 0; ALL.forEach(function(d){ if (!HIDE[d.family]) DATA.push(d); }); };
      showing();
      legend = function(){
        var el = $('legend'); el.innerHTML = '';
        codes.forEach(function(f){
          var b = E('button', 'code-t' + (HIDE[f] ? ' off' : '')), sw = E('i', 'sw');
          b.type = 'button'; b.setAttribute('data-hint', 'board:code'); b.setAttribute('aria-pressed', HIDE[f] ? 'false' : 'true');
          sw.style.background = FAMHEX[f]; b.appendChild(sw); b.appendChild(document.createTextNode(f));
          b.onclick = function(){ HIDE[f] = !HIDE[f]; if (codes.every(function(x){ return HIDE[x]; })) HIDE[f] = false;
                                  showing(); draw(); if (VIEW === 'table') table(); pushState(); };
          el.appendChild(b); });
      };
      var _push = pushState;
      pushState = function(){
        _push();
        var on = codes.filter(function(f){ return !HIDE[f]; });
        if (on.length === 1 && codes.length > 1) try { history.replaceState(null, '', location.search + '&code=' + encodeURIComponent(relOf[on[0]])); } catch (e) {}
      };
    } else {
      options();
      if (Q.y && metric(Q.y)) ysel.value = Q.y;
    }
    // a submitted design is not assessed for the study's buildability: its dot is filled, never hollow
    var _draw = draw;
    draw = function(){
      _draw();
      [].forEach.call(document.querySelectorAll('#svg .dot'), function(g){
        if ((g.getAttribute('data-key') || '').indexOf('c-') === 0) { g.classList.remove('hollow'); g.classList.add('sub'); } });
      var c = $('count'); if (c && !B.goal) c.textContent += ' · ' + rows.length + ' from the official leaderboard';
    };
    var _detail = detail;
    detail = function(d){
      if (!d || !d.community) return _detail(d);
      var el = $('detail'); el.className = 'detail'; el.innerHTML = '';
      var left = E('div'), right = E('div'), sid = encodeURIComponent(d.sid), base = API + '/submissions/' + sid;
      left.appendChild(E('h2', null, d.title));
      var f = E('div', 'fam'); f.appendChild(document.createTextNode('by ')); f.appendChild(E('span', 'by', d.by || 'unnamed'));
      f.appendChild(document.createTextNode(' · #' + d.place + ' · ' + day(d.when) + (B.goal && d.code ? ' · ' + d.code : '')));
      left.appendChild(f);
      left.appendChild(E('p', 'ok', '✓ every check passed on the official server'));
      var btns = E('div', 'btns');
      btns.appendChild(link('Open in studio', base + '/page', 'btn primary'));
      var z = link('Download (.zip)', base + '/bundle.zip', 'btn'); z.setAttribute('download', d.sid + '-bundle.zip'); btns.appendChild(z);
      btns.appendChild(link('Report', base + '/report', 'btn'));
      left.appendChild(btns);
      var fl = E('div', 'files'); fl.appendChild(document.createTextNode('Files: '));
      FILES.forEach(function(x, i){ fl.appendChild(link(x[1], base + '/files/' + x[0])); if (i < FILES.length - 1) fl.appendChild(document.createTextNode(' · ')); });
      left.appendChild(fl);
      var rp = E('div', 'repro'); rp.appendChild(E('b', null, 'Reproduce'));
      rp.appendChild(E('pre', null, 'qccd grade ' + d.sid + '-bundle.zip'));
      rp.appendChild(E('span', null, 'The logical error rate is a sample: expect yours within the 95% interval. '));
      rp.appendChild(link('Set up once', '../../studio.html?agent#design'));
      left.appendChild(rp);
      var tb = E('table');
      if (d.exp) {
        var cn = d.code || d.exp.code || '';
        tb.appendChild(row('code', cn + (d.exp.distance && !/distance/.test(cn) ? ', distance ' + d.exp.distance : '')));
        tb.appendChild(row('ions', d.exp.ions + ' (' + d.exp.n_data + ' data, ' + d.exp.n_ancilla + ' ancilla)'));
        tb.appendChild(row('logical qubits', String(d.exp.k) + (d.exp.basis ? ', measured in ' + d.exp.basis.split('').join(' and ') : '')));
        if (d.exp.k) tb.appendChild(row('ions per logical qubit', (d.exp.ions / d.exp.k).toFixed(1) + ' (FTL target: ' +
          Math.floor(TARGET.ions / TARGET.logical_qubits) + ' or fewer)', d.exp.ions / d.exp.k <= TARGET.ions / TARGET.logical_qubits ? 'ok' : ''));
      }
      if (d.ler && d.ler.shots) tb.appendChild(row('sample', d.ler.errors + ' errors in ' + Number(d.ler.shots).toLocaleString() + ' shots'));
      METRICS.forEach(function(m){
        if (val(d, m.k) == null || (d.exp && SIZE.indexOf(m.k) >= 0)) return;
        var tr = row(m.name.replace(' — ', ': '), fmt(val(d, m.k), m));
        if (m.def && tr.firstChild) {
          var a = link('definition', '/evaluation/#m-' + encodeURIComponent(m.def), 'def');
          a.title = 'how ' + m.def + ' is defined: the evaluation the grader reads';
          tr.firstChild.appendChild(document.createTextNode(' ')); tr.firstChild.appendChild(a);
        }
        tb.appendChild(tr);
      });
      right.appendChild(tb);
      el.appendChild(left); el.appendChild(right);
    };
    // the table names who each design is by (and, on a goal's page, its code); the measures are the server's
    table = function(){
      var w = $('tablewrap'); w.innerHTML = ''; var yk = ysel.value;
      var cols = tcols.map(metric).filter(Boolean), lead = B.goal ? ['#', 'design', 'by', 'code', 'ions'] : ['#', 'design', 'by', 'family'];
      var t = E('table', 'tbl'), tr = E('tr');
      lead.concat(cols.map(function(m){ return m.name.split(' — ')[1] || m.name; })).forEach(function(h, i){
        var th = E('th', null, h), c = cols[i - lead.length];
        if (c) { th.onclick = function(){ ysel.value = c.k; logbox.checked = !!c.log; draw(); pushState(); };
                 th.title = 'rank by this'; if (c.k === yk) th.style.color = '#0b0b0b'; }
        tr.appendChild(th); });
      t.appendChild(tr);
      var rk = ranked(yk), all = rk.concat(DATA.filter(function(d){ return d.compiled && val(d, yk) == null; }),
                                           DATA.filter(function(d){ return !d.compiled; }));
      all.forEach(function(d){
        var r = E('tr'); if (SEL === d.key) r.className = 'sel'; var i = rk.indexOf(d);
        var head = [i >= 0 ? String(i + 1) : '–', d.community ? d.title : shortName(d), d.community ? d.by : 'the QCCD study'];
        head = head.concat(B.goal ? [d.code || '', d.exp ? String(d.exp.ions) : ''] : [d.community ? 'submitted' : d.family]);
        head.concat(cols.map(function(m){ return fmt(val(d, m.k), m); })).forEach(function(v){ r.appendChild(E('td', null, v)); });
        r.onclick = function(){ select(d.key); }; t.appendChild(r);
      });
      w.appendChild(t);
    };
    draw();
    if (VIEW === 'table') table();
    var want = DATA.filter(function(d){ return d.key === Q.sel && d.community; })[0] || (B.goal ? ranked(ysel.value)[0] : null);
    if (want) { select(want.key); if (Q.sel) { var dt = $('detail'); if (dt && dt.scrollIntoView) dt.scrollIntoView({block: 'start'}); } }
  }).catch(function(err){
    // the server did not answer: a board's page keeps the study's ranking; a goal's page says so
    if (B.goal) say('The official server did not answer (' + err.message + '). Try again in a minute.');
  });
})();
"""


_DETAIL = re.compile(r'(<div class="detail empty" id="detail">[^<]*</div>)')


def _with_layer(page: str, board: dict, block: str, task: str) -> str:
    js = (CHART_SCRIPT.replace("__BOARD__", json.dumps(board)).replace("__FILES__", json.dumps(BUNDLE_FILES)))
    page, n = _DETAIL.subn(lambda m: m.group(1) + block, page, count=1)
    if not n:
        page = page.replace("</main>", block + "</main>", 1)
    tail = f"<style>{CSS}{CHART_CSS}</style>" + script([task]) + f"<script>{js}</script>"
    return page.replace("</body>", tail + "</body>", 1)


def with_submissions(page: str, task: str, title: str) -> str:
    """A board's ranking page (board/<task>/) as the site serves it: the official entries in its
    chart (CHART_SCRIPT), and "Try your own design" under the detail panel, before the
    disqualified list.  Unchanged for a board the official server does not rank."""
    b = board_ids().get(task)
    if not b or "</body>" not in page:
        return page
    block = board_block(task, title, seed_note=False, live=False, root="../../")
    return _with_layer(_short(page), {"id": b["id"], "task": task}, block, task)


# ------------------------------------------------------------------ a goal: one ranking across codes

def goal_info(goal: str = "goal1") -> dict | None:
    """The goal as the official service defines it (OfficialService.GOALS), and its boards: the
    releases on the shared noise model, as (task, release id, title)."""
    try:
        from ..official.service import OfficialService
        from ..workspace.tasks import list_boards
    except Exception:                       # the workspace package is optional for a site build
        return None
    g = OfficialService.GOALS.get(goal)
    if g is None:
        return None
    boards = [(b.manifest["task"], b.id, b.title) for b in list_boards()
              if (b.manifest.get("qec") or {}).get("noise") == g["noise"] and b.manifest.get("goal", "goal1") == goal]
    return {**g, "id": goal, "boards": boards}


GOAL_SUB = ("Every curated QEC memory under {max_ions} ions on one ranking, by logical error rate per round "
            "per logical qubit ({noise}). Rank by logical qubits or ions per logical qubit too: FTL's target is "
            "60 logical qubits within 2000 ions.")

#: a board page's one line; its long description folds away (the user: too much text)
BOARD_SUB = "Pick a measure to rank by; click a dot to open a design."
_SUB = re.compile(r'(<h1>[^<]*</h1>\s*)<p class="sub">(.*?)</p>', re.S)
_DQ_SUB = re.compile(r'(<section class="dq" id="disqualified"><h2>[^<]*</h2>)<p class="sub">.*?</p>', re.S)


def _short(page: str) -> str:
    page = _SUB.sub(lambda m: m.group(1) + f'<p class="sub">{BOARD_SUB}</p><details class="about"><summary>About '
                    f'this board</summary><p>{m.group(2)}</p></details>', page, count=1)
    return _DQ_SUB.sub(lambda m: m.group(1) + '<p class="sub">Not ranked: each breaks a rule or the task&rsquo;s '
                       'hardware budget.</p>', page, count=1)


def goal_page(page: str, goal: str = "goal1") -> str | None:
    """A goal's ranking page (board/<goal>/), made from a memory board's chart page: the same
    chart, its data the goal's official entries only, every code together (CHART_SCRIPT with
    B.goal).  None when the goal has no board."""
    g = goal_info(goal)
    if not g or not g["boards"] or "</body>" not in page:
        return None
    codes = ", ".join(t for _, _, t in g["boards"])
    h1 = f"Goal 1: {g['title']}" if goal == "goal1" else g["title"]
    page = re.sub(r"<title>[^<]*</title>", f"<title>{html.escape(h1)}</title>", page, count=1)
    page = re.sub(r"<h1>[^<]*</h1>", f"<h1>{html.escape(h1)}</h1>", page, count=1)
    page = re.sub(r'(<h1>[^<]*</h1>\s*<p class="sub">)[^<]*(</p>)',
                  lambda m: m.group(1) + html.escape(GOAL_SUB.format(max_ions=g["max_ions"], codes=codes,
                                                                     noise=g["noise"])) + m.group(2), page, count=1)
    page = re.sub(r"var DATA = \[.*?\];\n", "var DATA = [];\n", page, count=1, flags=re.S)
    page = re.sub(r'<section class="dq" id="disqualified">.*?</section>', "", page, count=1, flags=re.S)
    page = page.replace("Click a dot to see the design's numbers, its claim, and the steps to check it on.",
                        "Loading the official entries&hellip;", 1)
    task, _, title = g["boards"][0]
    block = board_block(task, title, seed_note=False, live=False, root="../../").replace(
        "The magenta dots are designs", "The dots are designs", 1)
    return _with_layer(page, {"goal": goal}, block, task)


# ------------------------------------------- Goal 1 on the Leaderboard page: one row per code

GOAL_TABLE_CSS = """
.g1t{margin:4px 0 18px}
.g1t table{width:100%;border-collapse:collapse;font:13.5px/1.4 var(--sans,system-ui,sans-serif)}
.g1t th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#56545e;padding:4px 8px;
 border-bottom:1px solid #e0e6ef}
.g1t td{padding:6px 8px;border-bottom:1px solid #edf1f6;white-space:nowrap}
.g1t td.n{text-align:right;font-variant-numeric:tabular-nums}
.g1t .g1w{overflow-x:auto}
.g1t .g1-more{font-size:13.5px;margin:6px 0 0}
"""

GOAL_TABLE_SCRIPT = r"""
(function(){
  // Goal 1 at a glance: one row per code (a curated release), its size and its best entry, live
  // from the official server; each row opens Goal 1's chart on that code
  var box = document.getElementById('g1-codes');
  if (!box || typeof fetch !== 'function') return;
  function el(t, c, x){ var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  function fmt(v){ return typeof v === 'number' ? v.toExponential(2) : '–'; }
  fetch('/official/v1/goals/goal1', {headers: {'Accept': 'application/json'}}).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function(g){
    var best = {};
    (g.rows || []).forEach(function(r){ if (!best[r.release]) best[r.release] = r; });   // rows come ranked
    var rels = (g.releases || []).slice().sort(function(a, b){
      var x = best[a.id], y = best[b.id];
      return x && y ? x.rank_value - y.rank_value : (x ? -1 : y ? 1 : (a.ions || 0) - (b.ions || 0)); });
    var t = el('table'), hd = el('tr');
    ['code', 'ions', 'logical qubits', 'ions per logical qubit', 'entries', 'best, per round per logical qubit', 'design', 'by'].forEach(function(h){
      hd.appendChild(el('th', null, h)); });
    var th = el('thead'); th.appendChild(hd); t.appendChild(th);
    var tb = el('tbody');
    rels.forEach(function(rel){
      var tr = el('tr'), b = best[rel.id], td = el('td'), a = el('a', null, rel.title || rel.id);
      a.href = 'goal1/?code=' + encodeURIComponent(rel.id); td.appendChild(a); tr.appendChild(td);
      tr.appendChild(el('td', 'n', rel.ions == null ? '–' : String(rel.ions)));
      tr.appendChild(el('td', 'n', rel.k == null ? '–' : String(rel.k)));
      tr.appendChild(el('td', 'n', rel.k && rel.ions ? (rel.ions / rel.k).toFixed(1) : '–'));
      tr.appendChild(el('td', 'n', String(rel.entries || 0)));
      tr.appendChild(el('td', 'n', b ? fmt(b.rank_value) : '–'));
      tr.appendChild(el('td', null, b ? (b.display_name || b.id) : 'no entry yet'));
      tr.appendChild(el('td', null, b ? (b.by || '–') : ''));
      tb.appendChild(tr);
    });
    t.appendChild(tb); box.textContent = ''; var w = el('div', 'g1w'); w.appendChild(t); box.appendChild(w);
  }).catch(function(err){ box.textContent = ''; box.appendChild(el('p', null, 'The official server did not answer (' + err.message + ').')); });
})();
"""


def goal_table() -> str:
    """Goal 1 on the Leaderboard page: a live table, one row per code, each opening the chart on it."""
    return ('<section class="g1t" id="goal1-codes" aria-label="Goal 1, code by code">'
            '<div id="g1-codes"><p class="lbx-wait">Loading Goal 1&hellip;</p></div>'
            '<p class="g1-more">FTL&rsquo;s target: 60 logical qubits within 2000 ions, about 33 ions per logical qubit. '
            '<a href="goal1/">The Goal 1 chart: every entry, rank by any measure &rarr;</a></p>'
            f'</section><style>{GOAL_TABLE_CSS}</style><script>{GOAL_TABLE_SCRIPT}</script>')


# ---------------------------------------------------------------------- /connect/

CONNECT_CSS = """
.cx-card{border:1px solid #d7e4f6;background:#f6f9fe;border-radius:12px;padding:16px 18px;margin:16px 0;font-family:var(--sans,system-ui,sans-serif)}
.cx-card h2{margin:0 0 8px}
.cx-code{font:600 22px/1.2 ui-monospace,Consolas,monospace;letter-spacing:.12em;color:#1a2540}
.cx-btn{font:600 14px/1 var(--sans,system-ui,sans-serif);padding:10px 18px;border-radius:8px;border:1px solid #1f5bb5;background:#1f5bb5;
 color:#fff;cursor:pointer;margin:8px 10px 0 0}
.cx-btn.alt{background:#fff;color:#1f5bb5}
.cx-btn[disabled]{opacity:.55;cursor:default}
.cx-ok{color:#0b5d36;font-weight:600}
.cx-bad{color:#9a3412}
.cx-t{width:100%;border-collapse:collapse;font:13.5px/1.4 var(--sans,system-ui,sans-serif);margin:8px 0}
.cx-t th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#56545e;padding:4px 8px;border-bottom:1px solid #e0e6ef}
.cx-t td{padding:6px 8px;border-bottom:1px solid #edf1f6}
.cx-small{font-size:13px;color:#56545e}
"""

CONNECT_BODY = r"""<h1>Connect a workspace</h1>
<p class="sub">A QCCD workspace runs on your own computer: your designs, your AI agent, and the grading
all happen there. To put a design on the <a href="../board/">official leaderboard</a> under your name,
the workspace asks for your permission here, with your qccd.academy account. Your password stays on
this site; the workspace gets a key that can only submit designs as you, and you can revoke it below.</p>
<div id="cx" data-qccd-private="1">
  <div id="cx-msg" class="note">Checking whether you are signed in&hellip;</div>
  <div id="cx-ask" class="cx-card" hidden>
    <h2>Allow this workspace to submit as you?</h2>
    <p><b id="cx-label"></b> asks to submit designs to the leaderboard under the name <b id="cx-name"></b>.</p>
    <p class="cx-small">It showed the code below. Allow it only if you just pressed <b>Sign in</b> in your own
    workspace (or ran <code>qccd login</code>) and it shows the same code.</p>
    <p class="cx-code" id="cx-code"></p>
    <button class="cx-btn" type="button" id="cx-allow" data-hint="connect:allow">Allow</button>
    <button class="cx-btn alt" type="button" id="cx-deny" data-hint="connect:deny">Don&rsquo;t allow</button>
  </div>
  <div id="cx-done" class="cx-card" hidden></div>
  <section id="cx-mine" hidden>
    <h2>Workspaces that can submit as you</h2>
    <table class="cx-t"><thead><tr><th>workspace</th><th>signed in</th><th>last used</th><th></th></tr></thead>
      <tbody id="cx-keys"></tbody></table>
    <h2>What you submitted</h2>
    <table class="cx-t"><thead><tr><th>board</th><th>design</th><th>shown</th><th>status</th><th>date</th></tr></thead>
      <tbody id="cx-subs"></tbody></table>
  </section>
</div>
<script>
window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, {
  "connect:allow": {"t": "Allow", "d": "let the workspace that showed this code submit designs to the leaderboard under your name (only the person presses it)"},
  "connect:deny": {"t": "Don't allow", "d": "refuse this sign-in code; the workspace gets nothing"},
  "connect:revoke": {"t": "Revoke", "d": "stop this workspace submitting as you; what it submitted stays"}});
(function(){
  if (typeof fetch !== 'function') return;
  if (window.top !== window.self) {       // never inside another site's frame: Allow must be a click on qccd.academy itself
    document.getElementById('cx-msg').textContent = 'Open this page in its own tab.';
    return;
  }
  if (window.QCCD_MIRROR) {             // a workspace's own copy of the site: the Allow happens on qccd.academy
    document.getElementById('cx-msg').textContent = 'Open this page on qccd.academy itself (your workspace opens it there when you press Sign in).';
    return;
  }
  var $ = function(id){ return document.getElementById(id); };
  var m = /[?&]code=([A-Za-z0-9-]+)/.exec(location.search), CODE = m ? m[1].toUpperCase() : null;
  var ME = null;
  function el(t, c, x){ var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  function day(t){ return t ? new Date(t * 1000).toISOString().slice(0, 10) : '-'; }
  function post(url, body){
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body || {}) }).then(function(r){
      return r.json().catch(function(){ return {}; }).then(function(d){
        if (!r.ok) { var e = d && d.error; throw new Error((e && (e.message || e)) || ('HTTP ' + r.status)); }
        return d; }); });
  }
  function grant(purpose, code){ return post('/api/leaderboard/grant', { purpose: purpose, code: code }).then(function(d){ return d.grant; }); }
  function done(ok, text){ var d = $('cx-done'); d.hidden = false; d.textContent = ''; d.appendChild(el('p', ok ? 'cx-ok' : 'cx-bad', text)); $('cx-ask').hidden = true; }
  function mine(){
    return grant('manage').then(function(g){ return post('/official/v1/account/keys', { grant: g }); }).then(function(d){
      $('cx-mine').hidden = false;
      var kb = $('cx-keys'), sb = $('cx-subs'); kb.textContent = ''; sb.textContent = '';
      if (!(d.keys || []).length) { var tr0 = el('tr'); var td0 = el('td', 'cx-small', 'None yet.'); td0.colSpan = 4; tr0.appendChild(td0); kb.appendChild(tr0); }
      (d.keys || []).forEach(function(k){
        var tr = el('tr');
        tr.appendChild(el('td', null, k.label || 'a workspace'));
        tr.appendChild(el('td', null, day(k.created_at)));
        tr.appendChild(el('td', null, k.revoked ? 'revoked' : day(k.last_used_at)));
        var td = el('td');
        if (!k.revoked) {
          var b = el('button', 'cx-btn alt', 'Revoke'); b.type = 'button'; b.setAttribute('data-hint', 'connect:revoke');
          b.onclick = function(){ b.disabled = true; grant('manage').then(function(g){
            return post('/official/v1/account/keys/' + encodeURIComponent(k.key_id) + '/revoke', { grant: g }); }).then(mine,
            function(e){ b.disabled = false; alert('Not revoked: ' + e.message); }); };
          td.appendChild(b);
        }
        tr.appendChild(td); kb.appendChild(tr);
      });
      if (!(d.submissions || []).length) { var tr1 = el('tr'); var td1 = el('td', 'cx-small', 'Nothing yet.'); td1.colSpan = 5; tr1.appendChild(td1); sb.appendChild(tr1); }
      (d.submissions || []).forEach(function(s){
        var tr = el('tr');
        [s.board, s.display_name || '-', s.visibility, s.status, day(s.created_at)].forEach(function(v){ tr.appendChild(el('td', null, v)); });
        sb.appendChild(tr);
      });
    }, function(e){ $('cx-mine').hidden = true; if (!CODE) $('cx-msg').textContent = 'Your workspaces could not be listed: ' + e.message; });
  }
  function ask(){
    fetch('/official/v1/links/' + encodeURIComponent(CODE), { headers: { 'Accept': 'application/json' } }).then(function(r){
      return r.json().then(function(d){ return { ok: r.ok, d: d }; }); }).then(function(x){
      if (!x.ok) { done(false, 'This sign-in code is unknown or expired. Press Sign in in your workspace again.'); return; }
      var l = x.d;
      if (l.status !== 'pending') { done(l.status === 'delivered' || l.status === 'approved',
        l.status === 'delivered' || l.status === 'approved' ? 'This workspace is already signed in.' : 'This sign-in is ' + l.status + '. Press Sign in in your workspace again.'); return; }
      $('cx-label').textContent = l.label; $('cx-name').textContent = ME.name; $('cx-code').textContent = l.code;
      $('cx-ask').hidden = false;
      $('cx-allow').onclick = function(){
        $('cx-allow').disabled = $('cx-deny').disabled = true;
        grant('link', l.code).then(function(g){ return post('/official/v1/links/' + encodeURIComponent(l.code) + '/approve', { grant: g }); })
          .then(function(){ done(true, 'Allowed. ' + l.label + ' now submits as ' + ME.name + '. You can close this tab and go back to your workspace.'); mine(); },
                function(e){ $('cx-allow').disabled = $('cx-deny').disabled = false; done(false, 'Not allowed: ' + e.message); });
      };
      $('cx-deny').onclick = function(){
        grant('link', l.code).then(function(g){ return post('/official/v1/links/' + encodeURIComponent(l.code) + '/deny', { grant: g }); })
          .then(function(){ done(false, 'Refused. The workspace got nothing.'); }, function(e){ done(false, e.message); });
      };
    });
  }
  function start(){
    fetch('/api/me', { credentials: 'same-origin' }).then(function(r){ return r.ok ? r.json() : { user: null }; }).then(function(d){
      if (!d || !d.user) {
        $('cx-msg').textContent = 'Sign in first, with Sign in in the bar above (your own qccd.academy account). This page continues by itself.';
        setTimeout(start, 2500);
        return;
      }
      ME = d.user;
      $('cx-msg').textContent = 'Signed in as ' + ME.name + '.';
      if (CODE) ask();
      mine();
    }, function(){ setTimeout(start, 5000); });
  }
  start();
})();
</script>"""


def connect_page(PAGE: str, STYLE: str) -> str:
    return PAGE.format(title="Connect a workspace - QCCD studio", style=STYLE, extra_css=CONNECT_CSS, body=CONNECT_BODY)
