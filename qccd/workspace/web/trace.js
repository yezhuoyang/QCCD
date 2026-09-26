/* The trace viewer (/trace): what an agent did for a request, to read, audit and replay.
 *
 * Left: the conversations the workspace traced and their requests.  Right, two views of one
 * request:
 *   Program  the request as a program (atir.py): one instruction per line -- recv, boot, think
 *            (a model call), say, call (a tool, the page), end -- with its time, the agent's
 *            memory (the context a model call read) and its effects (a design commit, a job).
 *            Like a compiled QCCD programme on a leaderboard page, the listing runs beside a
 *            picture: the design as it stood at the instruction, drawn from the workspace.  A
 *            time bar shows where the time went, machine by machine; Play replays the run.
 *            The checker's verdict (well-formed; every commit logged and replaying) is on top.
 *   Steps    the raw record, one card per step, with the recorded gaps replayed (scaled).
 * Deep link: /trace?session=<id>&prompt=<id>[&view=steps].
 */
(function () {
'use strict';

var S = { traces: [], session: null, prompt: null, steps: [], timer: null, shown: -1, speed: 4,
          hide: { mcp: true }, view: 'program', prog: null, cur: 0, graphs: {}, ptimer: null };
var Q = new URLSearchParams(location.search);
if (Q.get('view') === 'steps') S.view = 'steps';

function h(tag, attrs, kids) {
  var e = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    var v = attrs[k];
    if (v === undefined || v === null || v === false) return;
    if (k === 'text') e.textContent = v;
    else if (k === 'cls') e.className = v;
    else if (k === 'on') Object.keys(v).forEach(function (ev) { e.addEventListener(ev, v[ev]); });
    else e.setAttribute(k, v === true ? '' : v);
  });
  (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return e;
}
var SVGNS = 'http://www.w3.org/2000/svg';
function s(tag, attrs) {
  var e = document.createElementNS(SVGNS, tag);
  Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
  return e;
}
function get(path) {
  return fetch(path, { credentials: 'same-origin' }).then(function (r) {
    return r.json().then(function (d) { return { ok: r.ok, data: d }; });
  });
}
function when(t) { var d = new Date(t * 1000); return d.toLocaleString(); }
function secs(x) { return x < 60 ? x.toFixed(1) + ' s' : Math.floor(x / 60) + ' min ' + Math.round(x % 60) + ' s'; }
function dur(ms) { if (ms === null || ms === undefined) return '–'; return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : Math.round(ms) + ' ms'; }
function ktok(n) { return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k' : String(n); }
function pretty(v) {
  if (v === undefined || v === null) return '';
  if (typeof v === 'string') { try { return JSON.stringify(JSON.parse(v), null, 1); } catch (e) { return v; } }
  return JSON.stringify(v, null, 1);
}
function fold(label, body, open) {
  var d = h('details', { cls: 'fold', open: !!open }, [h('summary', { text: label }), h('pre', { text: body })]);
  return d;
}
function short(v, n) { var s = typeof v === 'string' ? v : JSON.stringify(v); s = s || ''; return s.length > n ? s.slice(0, n) + '…' : s; }

// ------------------------------------------------------------------ the list of conversations
function loadIndex() {
  return get('/api/traces').then(function (r) {
    var list = document.getElementById('tr-list');
    list.innerHTML = '';
    if (!r.ok) { list.appendChild(h('p', { cls: 'bad', text: (r.data.error || {}).message || 'could not load the traces' })); return; }
    S.traces = r.data.traces || [];
    if (!S.traces.length) {
      list.appendChild(h('p', { cls: 'muted', text: 'No traces yet. Every request you send an agent from Studio or a website page is traced from now on.' }));
      return;
    }
    S.traces.forEach(function (t) {
      var sec = h('section', { cls: 'tr-sess' }, [
        h('div', { cls: 'tr-sh' }, [h('b', { text: t.label || t.session }), h('span', { cls: 'muted', text: ' ' + (t.client || '') + ' · ' + when(t.last) })])
      ]);
      t.prompts.slice().reverse().forEach(function (p) {
        var b = h('button', { cls: 'tr-req', 'data-session': t.session, 'data-prompt': p.prompt || '',
                              on: { click: function () { open(t.session, p.prompt || ''); } } }, [
          h('span', { cls: 'tr-rt', text: p.text || (p.prompt ? p.prompt : 'steps outside a request') }),
          h('span', { cls: 'tr-rm', text: when(p.first) + ' · ' + p.steps + ' steps · ' + p.tools + ' calls' })
        ]);
        sec.appendChild(b);
      });
      list.appendChild(sec);
    });
    var want = Q.get('session'), wp = Q.get('prompt');
    var t0 = S.traces.filter(function (t) { return t.session === want; })[0] || S.traces[0];
    var p0 = t0.prompts.filter(function (p) { return p.prompt === wp; })[0] || t0.prompts[t0.prompts.length - 1];
    open(t0.session, p0 ? (p0.prompt || '') : '');
  });
}

function open(session, prompt) {
  stop(); pstop();
  S.session = session; S.prompt = prompt; S.prog = null; S.cur = 0;
  Array.prototype.forEach.call(document.querySelectorAll('.tr-req'), function (b) {
    b.classList.toggle('on', b.getAttribute('data-session') === session && b.getAttribute('data-prompt') === prompt);
  });
  var q = 'session=' + encodeURIComponent(session) + (prompt ? '&prompt=' + encodeURIComponent(prompt) : '') +
          (S.view === 'steps' ? '&view=steps' : '');
  history.replaceState(null, '', '/trace?' + q);
  var path = '/api/traces/' + encodeURIComponent(session) + (prompt ? '?prompt=' + encodeURIComponent(prompt) : '');
  var ppath = '/api/traces/' + encodeURIComponent(session) + '/program' + (prompt ? '?prompt=' + encodeURIComponent(prompt) : '');
  document.getElementById('tr-json').setAttribute('href', S.view === 'program' ? ppath : path);
  return Promise.all([get(path), prompt ? get(ppath) : Promise.resolve({ ok: false })]).then(function (rr) {
    var r = rr[0];
    S.steps = r.ok ? (prompt ? r.data.steps : r.data.steps.filter(function (s) { return !s.prompt; })) : [];
    S.prog = rr[1].ok ? rr[1].data : null;
    show();
  });
}
function show() {
  document.getElementById('tr-steps-view').hidden = S.view !== 'steps';
  document.getElementById('tr-prog-view').hidden = S.view !== 'program';
  document.getElementById('tr-steps-bar').hidden = S.view !== 'steps';
  document.getElementById('tr-prog-bar').hidden = S.view !== 'program';
  Array.prototype.forEach.call(document.querySelectorAll('.tr-tab'), function (b) { b.classList.toggle('on', b.getAttribute('data-v') === S.view); });
  if (S.view === 'steps') render(); else renderProgram();
}
function setView(v) {
  stop(); pstop();
  S.view = v;
  if (S.session) open(S.session, S.prompt); else show();
}

// ================================================================== Program: the request as instructions
var MACH = { M: 'model', T: 'tools', P: 'page', '-': '' };
function renderProgram() {
  var box = document.getElementById('tr-prog-view');
  box.innerHTML = '';
  var P = S.prog;
  if (!S.prompt) { box.appendChild(h('p', { cls: 'muted', text: 'Steps outside a request have no program: pick a request on the left, or use Steps.' })); return; }
  if (!P || !(P.instructions || []).length) { box.appendChild(h('p', { cls: 'muted', text: 'Nothing recorded for this request.' })); return; }
  var sm = P.summary || {}, by = sm.by_machine_ms || {}, wall = sm.wall_ms || 1;
  var pct = function (v) { return Math.round(100 * (v || 0) / wall) + '%'; };
  box.appendChild(h('h1', { text: P.request ? '“' + short(P.request, 300) + '”' : 'A request' }));
  var chips = [
    ['time', dur(sm.wall_ms)],
    ['model', dur(by.model) + ' · ' + pct(by.model)],
    ['tools', dur(by.tools) + ' · ' + pct(by.tools)],
    ['page', dur(by.page) + ' · ' + pct(by.page)],
    ['model calls', String(sm.model_calls)],
    ['tool calls', sm.calls + (sm.polls ? ' (' + sm.polls + ' job polls)' : '')],
    ['commits', String(sm.commits)]
  ];
  if (sm.memory_peak) chips.push(['memory', ktok(sm.memory_first) + ' → ' + ktok(sm.memory_peak) + ' tokens']);
  if (sm.cost_usd !== undefined && sm.cost_usd !== null) chips.push(['cost', '$' + Number(sm.cost_usd).toFixed(2)]);
  box.appendChild(h('div', { cls: 'pg-chips' }, chips.map(function (c) { return h('span', { cls: 'pg-chip' }, [h('i', { text: c[0] }), c[1]]); })));
  var fnd = P.findings || [];
  box.appendChild(h('p', { cls: 'pg-check ' + (fnd.length ? 'bad' : 'ok'), text: fnd.length
    ? '✗ ' + fnd.length + ' finding(s): ' + fnd.map(function (f) { return '#' + f.i + ' ' + f.rule + ': ' + f.message; }).join('; ')
    : '✓ checked: well-formed, and every design commit it claims is in this workspace\'s log and replays' }));
  if (!sm.memory_peak) box.appendChild(h('p', { cls: 'muted small', text: 'This trace predates per-call token records: memory per model call is unknown, and a result\'s size in tokens is estimated as characters ÷ 4 (marked ~).' }));
  box.appendChild(timeline(P));
  var mem = memoryCurve(P);
  if (mem) box.appendChild(mem);
  var grid = h('div', { cls: 'pg-grid' }, [
    h('div', { cls: 'pg-list', id: 'pg-list' }, listingRows(P)),
    h('div', { cls: 'pg-stage' }, [h('div', { id: 'pg-pic', cls: 'pg-pic' }), h('div', { id: 'pg-detail', cls: 'pg-detail' })])
  ]);
  box.appendChild(grid);
  select(Math.min(S.cur, P.instructions.length - 1), true);
}

function timeline(P) {
  var wall = (P.summary || {}).wall_ms || 1, W = 1000, H = 34;
  var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + (H + 14), class: 'pg-time', preserveAspectRatio: 'none' });
  var lanes = { M: 0, T: 1, P: 1, '-': 2 };
  (P.instructions || []).forEach(function (x) {
    if (!x.ms || x.op === 'end') return;
    var x0 = (x.t * 1000) / wall * W, w = Math.max(1.2, x.ms / wall * W);
    var lane = lanes[x.machine] === undefined ? 2 : lanes[x.machine];
    var r = s('rect', { x: x0, y: 2 + lane * 11, width: w, height: 9, class: 'm-' + (x.machine === '-' ? 'x' : x.machine) + (x.op === 'boot' ? ' m-boot' : ''), 'data-i': x.i });
    var t = s('title', {}); t.textContent = '#' + x.i + ' ' + x.op + (x.tool ? ' ' + x.tool : '') + ' · ' + dur(x.ms);
    r.appendChild(t);
    svg.appendChild(r);
  });
  var head = s('line', { x1: 0, x2: 0, y1: 0, y2: H, class: 'pg-head', id: 'pg-head' });
  svg.appendChild(head);
  svg.addEventListener('click', function (e) {
    var rc = svg.getBoundingClientRect(), tt = (e.clientX - rc.left) / rc.width * wall / 1000;
    var best = 0;
    (P.instructions || []).forEach(function (x) { if (x.t <= tt) best = x.i; });
    pstop(); select(best);
  });
  var legend = h('div', { cls: 'pg-legend' }, [
    h('span', { cls: 'lg m-M', text: 'model (think)' }), h('span', { cls: 'lg m-T', text: 'tools (call)' }),
    h('span', { cls: 'lg m-P', text: 'page (the agent\'s visible hands)' }), h('span', { cls: 'lg m-x', text: 'start, other' }),
    h('span', { cls: 'muted small', text: ' · click the bar to jump' })
  ]);
  return h('div', { cls: 'pg-timebox' }, [svg, legend]);
}

function memoryCurve(P) {
  var th = (P.instructions || []).filter(function (x) { return x.op === 'think' && x.memory; });
  if (th.length < 2) return null;
  var wall = (P.summary || {}).wall_ms || 1, W = 1000, H = 44;
  var mx = Math.max.apply(null, th.map(function (x) { return x.memory; }));
  var pts = th.map(function (x) { return ((x.t * 1000) / wall * W).toFixed(1) + ',' + (H - 4 - (x.memory / mx) * (H - 10)).toFixed(1); });
  var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'pg-mem', preserveAspectRatio: 'none' });
  svg.appendChild(s('polyline', { points: pts.join(' '), class: 'pg-memline' }));
  return h('div', { cls: 'pg-timebox' }, [svg, h('div', { cls: 'pg-legend' }, [
    h('span', { cls: 'muted small', text: 'memory: the context each model call read, from ' + ktok(th[0].memory) + ' to ' + ktok(mx) + ' tokens' })])]);
}

function opText(x) {
  if (x.op === 'recv') return '“' + short(x.request || '', 160) + '”';
  if (x.op === 'boot') return (x.model || '') + ' · ' + x.tools + ' tools (' + x.qccd_tools + ' QCCD)';
  if (x.op === 'think') {
    var b = x.blocks || {}, parts = [];
    ['thinking', 'say', 'call'].forEach(function (k) { if (b[k]) parts.push(b[k] + ' ' + k); });
    return '→ ' + parts.join(', ') + (x.tokens && x.tokens.out ? ' · ' + x.tokens.out + ' tokens out' : '');
  }
  if (x.op === 'say') return '“' + short(x.text || '', 160) + '”';
  if (x.op === 'call') {
    var eff = (x.effects || []).map(function (e) {
      if (e.kind === 'commit') return 'commit r' + e.revision;
      if (e.kind === 'job') return 'job started';
      if (e.kind === 'poll') return 'poll: ' + e.status;
      if (e.kind === 'design') return 'new design';
      if (e.kind === 'present') return 'shows ' + (e.action || '');
      return e.kind;
    });
    var verb = x.args && x.args.verb ? '.' + x.args.verb : (x.args && x.args.action ? '.' + x.args.action : '');
    return x.tool + verb + (x.ok === false ? '  ✗' : '') + (eff.length ? '  {' + eff.join(', ') + '}' : '');
  }
  if (x.op === 'end') return (x.status || '') + (x.cost_usd !== undefined && x.cost_usd !== null ? ' · $' + Number(x.cost_usd).toFixed(2) : '');
  return short(x, 120);
}
function memText(x) {
  if (x.op === 'think' && x.memory) return ktok(x.memory);
  if (x.op === 'call' && x.result_tokens) return '+~' + ktok(x.result_tokens.est);
  return '';
}
function listingRows(P) {
  var mx = 1;
  (P.instructions || []).forEach(function (x) { if (x.ms && x.op !== 'end') mx = Math.max(mx, x.ms); });
  return [h('div', { cls: 'pg-row pg-hdr' }, [h('span', { text: '#' }), h('span', { text: 't' }), h('span', { text: 'cost' }),
            h('span', { text: 'memory' }), h('span', { text: 'op' }), h('span', { text: 'operands' })])].concat(
    (P.instructions || []).map(function (x) {
      var bar = x.ms && x.op !== 'end' ? h('i', { cls: 'pg-bar m-' + (x.machine === '-' ? 'x' : x.machine), style: 'width:' + Math.max(2, Math.round(40 * x.ms / mx)) + 'px' }) : null;
      return h('div', { cls: 'pg-row op-' + x.op, 'data-i': x.i, on: { click: function () { pstop(); select(x.i); } } }, [
        h('span', { cls: 'pg-n', text: String(x.i) }),
        h('span', { cls: 'pg-t', text: x.t.toFixed(1) }),
        h('span', { cls: 'pg-c' }, [bar, dur(x.op === 'say' || x.op === 'recv' ? null : x.ms)]),
        h('span', { cls: 'pg-m', text: memText(x) }),
        h('span', { cls: 'pg-op', text: x.op }),
        h('span', { cls: 'pg-o', text: opText(x) })
      ]);
    }));
}

// the design an instruction left: the last commit at or before it
function designAt(P, i) {
  var last = null;
  for (var k = 0; k <= i && k < P.instructions.length; k++) {
    (P.instructions[k].effects || []).forEach(function (e) { if (e.kind === 'commit' && e.design) last = { design: e.design, revision: e.revision, i: k }; });
  }
  if (last) return last;
  var first = null;
  P.instructions.some(function (x) {
    return (x.effects || []).some(function (e) { if (e.kind === 'commit' && e.design) { first = { design: e.design, revision: Math.max(0, e.revision - 1), i: -1, before: true }; return true; } return false; });
  });
  return first;
}
function graph(design, rev) {
  var key = design + '@' + rev;
  if (S.graphs[key]) return Promise.resolve(S.graphs[key]);
  return get('/api/design-graph?branch=' + encodeURIComponent(design) + '&rev=' + rev).then(function (r) {
    S.graphs[key] = r.ok ? r.data : null;
    return S.graphs[key];
  });
}
function drawDesign(where, g, prev, label) {
  where.innerHTML = '';
  where.appendChild(h('div', { cls: 'pg-pic-h' }, [h('b', { text: label.title }), h('span', { cls: 'muted', text: ' ' + label.sub })]));
  if (!g || !g.ok || !g.nodes.length) { where.appendChild(h('p', { cls: 'muted small', text: g && !g.ok ? 'this revision does not build' : 'an empty design' })); return; }
  var xs = g.nodes.map(function (n) { return n.xy[0]; }), ys = g.nodes.map(function (n) { return n.xy[1]; });
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var pad = 1.2, w = Math.max(1, x1 - x0) + 2 * pad, hh = Math.max(1, y1 - y0) + 2 * pad;
  var svg = s('svg', { viewBox: (x0 - pad) + ' ' + (y0 - pad) + ' ' + w + ' ' + hh, class: 'pg-svg' });
  var pos = {};
  g.nodes.forEach(function (n) { pos[n.id] = n.xy; });
  var had = {};
  if (prev && prev.ok) prev.nodes.forEach(function (n) { had[n.id] = true; });
  var sz = Math.max(w, hh) / 160;
  g.segments.forEach(function (sg) {
    var a = pos[sg.a], b = pos[sg.b];
    if (!a || !b) return;
    svg.appendChild(s('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: sg.loop ? 'pg-loop' : 'pg-rail', 'stroke-width': Math.max(0.08, sz * 0.5) }));
  });
  g.nodes.forEach(function (n) {
    var fresh = prev && !had[n.id];
    // sites sit about one lattice unit apart: a dot a quarter of that stays a dot at any size
    svg.appendChild(s('circle', { cx: n.xy[0], cy: n.xy[1], r: Math.max(0.22, sz * 0.7) * (n.kind === 'junction' ? 0.8 : 1), class: 'pg-node' + (fresh ? ' fresh' : '') + (n.kind === 'junction' ? ' junc' : '') }));
  });
  where.appendChild(svg);
  var added = prev && prev.ok ? g.nodes.filter(function (n) { return !had[n.id]; }).length : 0;
  where.appendChild(h('p', { cls: 'muted small', text: g.nodes.length + ' sites, ' + g.segments.length + ' rails' + (added ? ' · ' + added + ' new at this commit (green)' : '') }));
}
function select(i, force) {
  var P = S.prog;
  if (!P) return;
  i = Math.max(0, Math.min(i, P.instructions.length - 1));
  var changed = i !== S.cur || force;
  S.cur = i;
  var x = P.instructions[i];
  Array.prototype.forEach.call(document.querySelectorAll('.pg-row.now'), function (r) { r.classList.remove('now'); });
  var row = document.querySelector('.pg-row[data-i="' + i + '"]');
  if (row) { row.classList.add('now'); if (changed) row.scrollIntoView({ block: 'nearest' }); }
  var wall = (P.summary || {}).wall_ms || 1, head = document.getElementById('pg-head');
  if (head) { var X = (x.t * 1000) / wall * 1000; head.setAttribute('x1', X); head.setAttribute('x2', X); }
  document.getElementById('tr-prog-pos').textContent = '#' + i + ' / ' + (P.instructions.length - 1) + ' · ' + x.t.toFixed(1) + ' s';
  detail(x);
  var at = designAt(P, i);
  var pic = document.getElementById('pg-pic');
  if (!at) { pic.innerHTML = ''; pic.appendChild(h('p', { cls: 'muted small', text: 'No design changed in this request.' })); return; }
  var title = (P.designs || {})[at.design] || at.design;
  var justNow = at.i === i;
  Promise.all([graph(at.design, at.revision), at.revision > 0 && justNow ? graph(at.design, at.revision - 1) : Promise.resolve(null)]).then(function (gg) {
    if (S.cur !== i) return;
    drawDesign(pic, gg[0], gg[1], { title: title, sub: 'r' + at.revision + (at.before ? ' (before the first change)' : justNow ? ' · committed by this instruction' : ' · as of #' + at.i) });
  });
}
function detail(x) {
  var box = document.getElementById('pg-detail');
  box.innerHTML = '';
  var head = '#' + x.i + '  ' + x.op + (x.tool ? '  ' + x.tool : '') + '  ·  at ' + x.t.toFixed(2) + ' s' +
             (x.op !== 'say' && x.op !== 'recv' ? '  ·  ' + dur(x.ms) : '') + (MACH[x.machine] ? '  ·  on the ' + MACH[x.machine] : '');
  box.appendChild(h('div', { cls: 'pg-dh', text: head }));
  if (x.op === 'recv') {
    box.appendChild(h('p', { cls: 'said', text: x.request || '' }));
    box.appendChild(h('p', { cls: 'muted small', text: 'the agent received ' + x.harness_chars + ' characters: these words inside the workspace\'s harness (how to answer, the frozen context, the tools to use)' }));
  } else if (x.op === 'boot') {
    box.appendChild(h('p', { cls: 'muted small', text: 'the agent process started in ' + dur(x.ms) + ' with ' + x.tools + ' tools: ' + x.qccd_tools + ' QCCD tools and ' + (x.builtin_tools || []).length + ' of its own' }));
    box.appendChild(fold('its own tools', (x.builtin_tools || []).join('\n')));
  } else if (x.op === 'think') {
    var t = x.tokens || {};
    var bits = ['a model call: ' + dur(x.ms) + ' from the moment it could start to its last block'];
    if (x.memory) bits.push('it read ' + x.memory + ' tokens of context (' + (t.cache_read || 0) + ' from the cache, ' + (t.cache_write || 0) + ' newly cached, ' + (t.in || 0) + ' fresh) and wrote ' + (t.out || 0) + ' tokens');
    box.appendChild(h('p', { cls: 'small', text: bits.join('; ') }));
  } else if (x.op === 'say') {
    box.appendChild(h('div', { cls: 'said', text: x.text || '' }));
  } else if (x.op === 'call') {
    var costs = ['end to end ' + dur(x.ms)];
    if (x.exec_ms !== undefined && x.exec_ms !== null) costs.push('the tool itself ' + dur(x.exec_ms));
    if (x.page) costs.push('the page ' + (x.page.action || '') + (x.page.verb ? ' ' + x.page.verb : '') + ' ' + dur(x.page.ms));
    costs.push('its result: ' + (x.result_chars || 0) + ' characters, ~' + ((x.result_tokens || {}).est || 0) + ' tokens added to memory');
    box.appendChild(h('p', { cls: 'small', text: costs.join(' · ') }));
    (x.effects || []).forEach(function (e) { box.appendChild(h('p', { cls: 'pg-eff', text: 'effect: ' + JSON.stringify(e) })); });
    box.appendChild(fold('arguments', pretty(x.args), true));
    box.appendChild(fold(x.ok === false ? 'refused / failed' : 'result (first ' + 400 + ' characters)', pretty(x.result), false));
  } else if (x.op === 'end') {
    box.appendChild(h('p', { cls: 'small', text: [x.status, 'wall ' + dur(x.duration_ms), 'model ' + dur(x.api_ms), x.model_calls + ' model calls',
      x.cost_usd !== undefined && x.cost_usd !== null ? '$' + Number(x.cost_usd).toFixed(3) : ''].filter(Boolean).join(' · ') }));
    if (x.usage) box.appendChild(fold('tokens', pretty(x.usage)));
  } else box.appendChild(fold('instruction', pretty(x), true));
}
// replay: the instructions at their recorded pace (scaled)
function pstop() {
  if (S.ptimer) clearTimeout(S.ptimer);
  S.ptimer = null;
  var b = document.getElementById('tr-pplay');
  if (b) b.textContent = '▶ Play';
}
function pplay() {
  if (S.ptimer) { pstop(); return; }
  var P = S.prog;
  if (!P) return;
  if (S.cur >= P.instructions.length - 1) select(0);
  document.getElementById('tr-pplay').textContent = '❚❚ Pause';
  (function next() {
    var i = S.cur;
    if (i >= P.instructions.length - 1) { pstop(); return; }
    var gap = (P.instructions[i + 1].t - P.instructions[i].t) * 1000;
    var ms = S.speed === 0 ? 900 : Math.min(Math.max(gap / S.speed, 60), 6000);
    S.ptimer = setTimeout(function () { select(i + 1); next(); }, ms);
  })();
}

// ================================================================== Steps: the raw record
function visible(s) {
  if (S.hide.mcp && s.source === 'mcp' && S.steps.some(function (x) { return x.source === 'agent' && (x.kind === 'tool_call' || x.kind === 'item'); })) return false;
  if (S.hide.thinking && s.kind === 'thinking') return false;
  if (S.hide.page && s.kind === 'page_action') return false;
  if (s.kind === 'model') return false;
  return true;
}
function render() {
  var box = document.getElementById('tr-steps');
  box.innerHTML = '';
  var steps = S.steps.filter(visible);
  var head = document.getElementById('tr-head');
  head.innerHTML = '';
  if (!S.steps.length) { box.appendChild(h('p', { cls: 'muted', text: 'Nothing recorded for this request.' })); return; }
  var t0 = S.steps[0].at, t1 = S.steps[S.steps.length - 1].at;
  var d = S.steps.filter(function (s) { return s.kind === 'delivery'; })[0];
  var end = S.steps.filter(function (s) { return s.kind === 'turn'; }).pop();
  var calls = S.steps.filter(function (s) { return s.source === 'agent' && (s.kind === 'tool_call' || s.kind === 'skill' || (s.kind === 'item' && (s.data || {}).phase === 'completed' && s.name !== 'reasoning')); }).length;
  var mcp = S.steps.filter(function (s) { return s.source === 'mcp'; }).length;
  var pages = S.steps.filter(function (s) { return s.kind === 'page_action'; }).length;
  head.appendChild(h('h1', { text: d && (d.data || {}).request ? '“' + short(d.data.request, 300) + '”' : 'A request' }));
  var facts = [secs(t1 - t0), calls + ' agent tool calls', mcp + ' QCCD tool calls (MCP server)', pages + ' page actions'];
  if (end && end.data && end.data.total_cost_usd !== undefined) facts.push('$' + Number(end.data.total_cost_usd).toFixed(3));
  if (end && end.data && end.data.num_turns !== undefined) facts.push(end.data.num_turns + ' model turns');
  head.appendChild(h('p', { cls: 'muted', text: facts.join(' · ') }));
  var results = {};
  S.steps.forEach(function (s) { if (s.kind === 'tool_result' && s.data) results[s.data.id] = s; });
  steps.forEach(function (s, i) {
    if (s.kind === 'tool_result' && s.data && S.steps.some(function (x) { return x.kind !== 'tool_result' && x.data && x.data.id === s.data.id; })) return;
    if (s.kind === 'item' && (s.data || {}).phase === 'started' && S.steps.some(function (x) { return x !== s && x.kind === 'item' && x.data && x.data.id === s.data.id && x.data.phase === 'completed'; })) return;
    box.appendChild(card(s, t0, results));
  });
}

var SOURCE = { agent: 'agent', mcp: 'MCP server', page: 'page', service: 'workspace' };
function card(s, t0, results) {
  var d = s.data || {};
  var title = s.kind, body = [];
  if (s.kind === 'delivery') {
    title = s.name === 'steer' ? 'a correction, into the running turn' : 'handed to the agent';
    if (d.request) body.push(h('p', { cls: 'said', text: d.request }));
    body.push(fold('the full text the agent received (' + String(d.text || '').length + ' characters)', String(d.text || '')));
  } else if (s.kind === 'session') {
    title = 'the agent started' + (d.model ? ' on ' + d.model : '') + (d.claude_code_version ? ' (Claude Code ' + d.claude_code_version + ')' : '');
    if (d.mcp_servers) body.push(h('p', { cls: 'muted', text: 'MCP servers: ' + d.mcp_servers.map(function (m) { return m.name + ' ' + m.status; }).join(', ') }));
    if (d.skills) body.push(h('p', { cls: 'muted', text: 'skills: ' + [].concat(d.skills).map(function (x) { return typeof x === 'string' ? x : x.name; }).join(', ') }));
    body.push(fold('tools it had (' + (d.tools || []).length + ')', (d.tools || []).join('\n')));
  } else if (s.kind === 'message') {
    title = 'said';
    body.push(h('div', { cls: 'said', text: d.text || '' }));
  } else if (s.kind === 'thinking') {
    title = 'thought';
    var th = d.text || (d.summary ? [].concat(d.summary).join('\n') : '') || (d.content ? [].concat(d.content).join('\n') : '');
    body.push(h('div', { cls: 'thought', text: th || '(the agent does not share the text of its thinking; this marks when it thought)' }));
  } else if (s.kind === 'tool_call' || s.kind === 'skill') {
    title = (s.kind === 'skill' ? 'used the skill ' + ((d.input || {}).skill || '') : 'called ' + (s.name || '')) +
            (s.source === 'mcp' ? '' : '');
    var input = s.source === 'mcp' ? d.args : d.input;
    body.push(fold('input: ' + short(input, 120), pretty(input), false));
    var res = s.source === 'mcp' ? (d.error ? { error: d.error } : d.result) : (results[d.id] || {}).data;
    if (res !== undefined) {
      var bad = s.source === 'mcp' ? !!d.error : !!(res && res.is_error);
      var content = s.source === 'mcp' ? res : res.content;
      var f = fold((bad ? 'refused / failed: ' : 'result: ') + short(content, 140), pretty(content), false);
      if (bad) f.classList.add('bad');
      body.push(f);
    }
  } else if (s.kind === 'tool_result') {
    title = 'a tool result';
    body.push(fold(short(d.content, 140), pretty(d.content)));
  } else if (s.kind === 'item') {
    title = codexTitle(s.name, d);
    if (d.arguments !== undefined) body.push(fold('input: ' + short(d.arguments, 120), pretty(d.arguments)));
    if (d.result !== undefined || d.error) body.push(fold((d.error ? 'failed: ' : 'result: ') + short(d.error || d.result, 140), pretty(d.error || d.result)));
    if (d.aggregatedOutput) body.push(fold('output', d.aggregatedOutput));
    body.push(fold('the whole item', pretty(d)));
  } else if (s.kind === 'page_action') {
    title = 'on the page: ' + s.name + (d.ok ? '' : d.answered ? ' (refused)' : ' (no answer)');
    body.push(h('p', { cls: 'muted', text: d.page || '' }));
    body.push(fold('what it asked: ' + short(d.args, 120), pretty(d.args)));
    body.push(fold((d.ok ? 'what the page answered: ' : 'why not: ') + short(d.ok ? d.result : d.error, 140), pretty(d.ok ? d.result : d.error)));
  } else if (s.kind === 'turn') {
    title = 'the turn ended: ' + (s.name || '');
    var bits = [];
    if (d.duration_ms !== undefined) bits.push(secs(d.duration_ms / 1000));
    if (d.num_turns !== undefined) bits.push(d.num_turns + ' model turns');
    if (d.total_cost_usd !== undefined) bits.push('$' + Number(d.total_cost_usd).toFixed(3));
    if (d.error) bits.push('error: ' + short(d.error, 300));
    body.push(h('p', { cls: 'muted', text: bits.join(' · ') }));
    if (d.usage) body.push(fold('tokens', pretty(d.usage)));
  } else if (s.kind === 'error') {
    title = 'failed: ' + (s.name || '');
    body.push(h('pre', { cls: 'bad', text: pretty(d) }));
  } else {
    body.push(fold('data', pretty(d)));
  }
  var el = h('article', { cls: 'step k-' + s.kind + ' s-' + s.source, 'data-n': s.n }, [
    h('div', { cls: 'st-h' }, [
      h('span', { cls: 'st-t', text: '+' + (s.at - t0).toFixed(1) + ' s' }),
      h('span', { cls: 'st-src', text: SOURCE[s.source] || s.source }),
      h('b', { text: title }),
      s.ms !== undefined && s.kind !== 'turn' ? h('span', { cls: 'muted', text: ' ' + (s.ms >= 1000 ? (s.ms / 1000).toFixed(1) + ' s' : Math.round(s.ms) + ' ms') }) : null
    ])
  ].concat(body));
  el._step = s;
  return el;
}
function codexTitle(type, d) {
  if (type === 'mcpToolCall') return 'called ' + (d.server ? d.server + '.' : '') + (d.tool || '') + (d.status ? ' (' + d.status + ')' : '');
  if (type === 'commandExecution') return 'ran a command: ' + short(d.command, 100);
  if (type === 'webSearch') return 'searched the web: ' + short(d.query, 100);
  if (type === 'fileChange') return 'changed files';
  return type || 'an item';
}

// ------------------------------------------------------------------ replay (Steps)
function stop() {
  if (S.timer) clearTimeout(S.timer);
  S.timer = null;
  var b = document.getElementById('tr-play');
  if (b) b.textContent = '▶ Replay';
  document.body.classList.remove('replaying');
  Array.prototype.forEach.call(document.querySelectorAll('.step'), function (e) { e.classList.remove('hidden', 'now'); });
}
function play() {
  if (S.timer) { stop(); return; }
  var els = Array.prototype.slice.call(document.querySelectorAll('.step'));
  if (!els.length) return;
  document.body.classList.add('replaying');
  els.forEach(function (e) { e.classList.add('hidden'); e.classList.remove('now'); });
  document.getElementById('tr-play').textContent = '❚❚ Stop';
  var i = 0;
  (function next() {
    if (i > 0) els[i - 1].classList.remove('now');
    if (i >= els.length) { S.timer = null; document.getElementById('tr-play').textContent = '▶ Replay'; document.body.classList.remove('replaying'); return; }
    var e = els[i];
    e.classList.remove('hidden'); e.classList.add('now');
    e.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    document.getElementById('tr-prog').textContent = (i + 1) + ' / ' + els.length;
    var gap = i + 1 < els.length ? els[i + 1]._step.at - e._step.at : 0;
    var ms = S.speed === 0 ? 900 : Math.min(gap * 1000 / S.speed, 8000);
    i++;
    S.timer = setTimeout(next, Math.max(ms, 120));
  })();
}

// ------------------------------------------------------------------ the page
function build() {
  var speeds = [[1, 'real time'], [4, '4×'], [16, '16×'], [0, 'one step a second']];
  var speedSel = function (id) {
    return h('select', { id: id, title: 'replay speed', on: { change: function (e) { S.speed = +e.target.value; syncSpeed(); } } },
      speeds.map(function (s) { var o = h('option', { value: s[0], text: s[1] }); if (s[0] === S.speed) o.selected = true; return o; }));
  };
  document.body.appendChild(h('div', { id: 'tr' }, [
    h('aside', { id: 'tr-side' }, [
      h('div', { cls: 'tr-brand' }, [h('b', { text: 'Agent traces' }), h('a', { href: '/studio', text: 'Studio' })]),
      h('p', { cls: 'muted small', text: 'What each agent did for each request, as a program: every model call, tool call and page action, with its time, the agent\'s memory and what it changed, checked against the workspace. Steps shows the raw record.' }),
      h('div', { id: 'tr-list' })
    ]),
    h('main', { id: 'tr-main' }, [
      h('div', { id: 'tr-bar' }, [
        h('span', { cls: 'tr-tabs' }, [
          h('button', { cls: 'tr-tab', 'data-v': 'program', text: 'Program', on: { click: function () { setView('program'); } } }),
          h('button', { cls: 'tr-tab', 'data-v': 'steps', text: 'Steps', on: { click: function () { setView('steps'); } } })
        ]),
        h('span', { id: 'tr-prog-bar', cls: 'tr-sub' }, [
          h('button', { title: 'first instruction', text: '⏮', on: { click: function () { pstop(); select(0); } } }),
          h('button', { title: 'previous (←)', text: '◀', on: { click: function () { pstop(); select(S.cur - 1); } } }),
          h('button', { id: 'tr-pplay', cls: 'pri', text: '▶ Play', on: { click: pplay } }),
          h('button', { title: 'next (→)', text: '▶', on: { click: function () { pstop(); select(S.cur + 1); } } }),
          h('button', { title: 'last instruction', text: '⏭', on: { click: function () { pstop(); if (S.prog) select(S.prog.instructions.length - 1); } } }),
          speedSel('tr-pspeed'),
          h('span', { id: 'tr-prog-pos', cls: 'muted' })
        ]),
        h('span', { id: 'tr-steps-bar', cls: 'tr-sub' }, [
          h('button', { id: 'tr-play', cls: 'pri', text: '▶ Replay', on: { click: play } }),
          speedSel('tr-speed'),
          h('span', { id: 'tr-prog', cls: 'muted' }),
          toggle('thinking', 'Thinking'), toggle('page', 'Page actions'), toggle('mcp', 'MCP server copies')
        ]),
        h('span', { cls: 'sp' }),
        h('a', { id: 'tr-json', href: '#', target: '_blank', rel: 'noopener', text: 'JSON' })
      ]),
      h('div', { id: 'tr-prog-view' }),
      h('div', { id: 'tr-steps-view' }, [h('div', { id: 'tr-head' }), h('div', { id: 'tr-steps' })])
    ])
  ]));
  document.addEventListener('keydown', function (e) {
    if (S.view !== 'program' || !S.prog || /INPUT|SELECT|TEXTAREA/.test((e.target || {}).tagName || '')) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { pstop(); select(S.cur + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { pstop(); select(S.cur - 1); e.preventDefault(); }
    else if (e.key === ' ') { pplay(); e.preventDefault(); }
  });
  show();
}
function syncSpeed() {
  ['tr-pspeed', 'tr-speed'].forEach(function (id) { var el = document.getElementById(id); if (el) el.value = String(S.speed); });
}
// a checked box shows those steps
function toggle(key, label) {
  var cb = h('input', { type: 'checkbox', on: { change: function (e) { stop(); S.hide[key] = !e.target.checked; render(); } } });
  cb.checked = !S.hide[key];
  return h('label', { cls: 'tg', title: key === 'mcp' ? 'the QCCD MCP server\'s own record of each call (shown anyway when the agent\'s stream has none, e.g. a terminal agent)' : '' }, [cb, ' ' + label]);
}
window.QTRACE = { state: function () { return S; }, select: select, setView: setView };   // for tests
build();
// `qccd trace --open` pairs this browser with a one-time code, as `qccd studio` does
var pair = /[#&]pair=([A-Za-z0-9_-]+)/.exec(location.hash || '');
if (pair) {
  history.replaceState(null, '', location.pathname + location.search);
  fetch('/api/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
                       body: JSON.stringify({ code: pair[1] }) }).then(loadIndex, loadIndex);
} else loadIndex();
})();
