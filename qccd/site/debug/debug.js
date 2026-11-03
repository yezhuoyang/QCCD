// The Debug page: any program on the website, step by step, checked by rules.
//
// The program runs in a Studio page hosted in the frame #dFrame, UNCHANGED: a leaderboard
// entry, a Reproduce run, a compiled example, the Design page, or a file.  This script drives
// it from outside -- it seeks the Studio's own animation, reads the Studio's own state tables,
// and draws on the Studio's own picture (one <g> appended to its SVG) -- so whatever the
// Studio shows at "Step k", the panels here describe, and a rule that fails there is crossed
// out there.
//
// Steps count from 1, as the Studio's status line counts them.  Internally `cur` is the
// Studio's frame index (step - 1).
//
// The rules are the rule language of the Rules page, judged by the grader's own Python
// (qccd.debug, in worker.js) on the device and programme the Studio exports.  Stepping,
// watching and tracing need none of that: they read the Studio's tables directly.
//
// Everything a person can do here an agent can do through `window.QCCD_DEBUG` (declared in
// debug_api.json) and the declared controls (`data-hint` + `window.QCCD_HINTS` below).
(function () {
'use strict';
var CFG = window.QCCD_DEBUG_CONFIG || { root: '../', catalogue: [], rulesets: [], examples: [], python: {} };
// WHERE THE PROGRAM IS.  'frame': the Debug page, the Studio page in its frame (#dFrame).
// 'inpage': the Studio page itself, the debugger a panel beside it (opened by its Debug button
// or the bar's Debug link, through the loader the site build puts on every Studio page).
var HOST = CFG.host === 'inpage' ? 'inpage' : 'frame';
var NS = 'http://www.w3.org/2000/svg';
var PALETTE = ['#2a78d6', '#1baf7a', '#7b4ae0', '#d9822b', '#0f9bb0', '#c43d9b', '#8a7d12', '#4b5563'];
var RED = '#e0201b';
var STORE = 'qccd.studio.autosave';

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s === undefined || s === null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }

// ------------------------------------------------------------------ state

var S = {
  src: null, title: '', kind: '',      // what is open: kind page | design | file | studio
  win: null, B: null,                  // the frame's window and the bridge into its Studio
  frames: null, n: 0, cur: 0,
  watch: [],                           // {id, kind, color, trail}
  isolate: false, trails: true, crosses: true,
  rules: { set: 'standard', assertions: '', result: null, running: false, stale: false, shown: {}, open: {}, error: '' },
  F: null,                             // the located failures, indexed by frame
  bps: { steps: [], conds: [], onFail: true, onWatch: false },
  exprs: [], exprVals: {},
  tab: 'state', pending: null, picking: false, flash: null,
  active: HOST === 'frame'             // in a Studio page: whether the panel is open
};

// ------------------------------------------------------------------ the controls (both hosts)

var TOOLS_HTML =
  '<span class="q-tp">' +
  '<button id="dFirst" type="button" data-hint="debug:first" title="first step (Home)" aria-label="first step">&#x23EE;</button>' +
  '<button id="dBack" type="button" data-hint="debug:back" title="back one step (Left arrow)" aria-label="back one step">&#x25C0;</button>' +
  '<label class="q-stepbox">Step <input id="dStep" type="number" min="1" value="1" data-hint="debug:step" aria-label="step number"> ' +
  '<span id="dOf">/ 0</span></label>' +
  '<button id="dFwd" type="button" data-hint="debug:forward" title="forward one step (Right arrow)" aria-label="forward one step">&#x25B6;</button>' +
  '<button id="dLast" type="button" data-hint="debug:last" title="last step (End)" aria-label="last step">&#x23ED;</button></span>' +
  '<span class="q-sep"></span>' +
  '<button id="dPrevFail" type="button" class="q-fail" data-hint="debug:prevfail" title="previous step where a rule fails (Shift+Left)">&#x2717;&#x25C0;</button>' +
  '<button id="dNextFail" type="button" class="q-fail" data-hint="debug:nextfail" title="next step where a rule fails (Shift+Right)">&#x25B6;&#x2717;</button>' +
  '<button id="dPrevEv" type="button" class="q-ev" data-hint="debug:prevevent" title="previous step where something you watch changes (Alt+Left)">&#x25C6;&#x25C0;</button>' +
  '<button id="dNextEv" type="button" class="q-ev" data-hint="debug:nextevent" title="next step where something you watch changes (Alt+Right)">&#x25B6;&#x25C6;</button>' +
  '<button id="dContBack" type="button" data-hint="debug:continueback" title="run backward to the previous breakpoint (Shift+F8)">&#x25C0;&#x25C0;</button>' +
  '<button id="dCont" type="button" class="q-p" data-hint="debug:continue" title="run forward to the next breakpoint (F8)">Continue &#x25B6;&#x25B6;</button>' +
  '<span class="q-grow"></span>' +
  '<button id="dIsolate" type="button" class="q-tgl" aria-pressed="false" data-hint="debug:isolate">Isolate</button>' +
  '<button id="dTrails" type="button" class="q-tgl q-on" aria-pressed="true" data-hint="debug:trails">Trails</button>' +
  '<button id="dCrosses" type="button" class="q-tgl q-on" aria-pressed="true" data-hint="debug:crosses">Crosses</button>';
var SIDE_HTML =
  '<div id="dTabs" role="tablist">' +
  '<button type="button" role="tab" data-tab="state" data-hint="debug:tab:state" aria-selected="true">State</button>' +
  '<button type="button" role="tab" data-tab="watch" data-hint="debug:tab:watch">Watch</button>' +
  '<button type="button" role="tab" data-tab="rules" data-hint="debug:tab:rules">Rules</button>' +
  '<button type="button" role="tab" data-tab="breaks" data-hint="debug:tab:breaks">Breakpoints</button>' +
  '<button type="button" role="tab" data-tab="report" data-hint="debug:tab:report">Report</button></div>' +
  '<div class="q-pane" id="pState" data-pane="state"></div>' +
  '<div class="q-pane" id="pWatch" data-pane="watch" hidden></div>' +
  '<div class="q-pane" id="pRules" data-pane="rules" hidden></div>' +
  '<div class="q-pane" id="pBreaks" data-pane="breaks" hidden></div>' +
  '<div class="q-pane" id="pReport" data-pane="report" hidden></div>';
var TIME_HTML = '<svg id="dTimeSvg" aria-label="timeline: every step, the steps where rules fail and where what you watch changes"></svg>';
// the panel a Studio page gets: the same controls, one column
var INPAGE_HTML =
  '<div class="q-ihead"><b>Debug</b><span id="dSrc" class="q-src" data-hint="debug:source"></span><span class="q-grow"></span>' +
  '<a id="dFull" target="_blank" rel="noopener" data-hint="debug:fullpage">open in the Debug page</a>' +
  '<button id="dClose" type="button" data-hint="debug:close" title="close the debugger" aria-label="close the debugger">&times;</button></div>' +
  '<div id="dbgBar"><span id="dTools" class="q-tools">' + TOOLS_HTML + '</span></div>' +
  '<div id="dTime" data-hint="debug:timeline">' + TIME_HTML + '</div>' +
  '<div id="dMsg" role="status"></div>' +
  '<div id="dbgSide">' + SIDE_HTML + '</div>';

// ------------------------------------------------------------------ the bridge
//
// The Studio keeps its tables in page-scope `const`/`let` bindings (P, states, IONP ...),
// which are not properties of its window.  An inline script, run in the frame, hands out
// getters for them: no eval, so this also works under the workspace mirror's CSP.
var BRIDGE_SRC = "(function(){try{if(typeof P==='undefined'||!P||!P.frames){window.__QCCD_DEBUG_BRIDGE={ok:false," +
  "why:'this page has no Studio animation'};return;}window.__QCCD_DEBUG_BRIDGE={ok:true," +
  "get P(){return P},get A(){return A},get D(){return D},get frame(){return frame},get phase(){return phase}," +
  "get states(){return states},get before(){return before},get nodeById(){return nodeById}," +
  "get segById(){return segById},get IONP(){return IONP},get svg(){return svg}," +
  "px:function(n){return px(n)},py:function(n){return py(n)}," +
  "edgePoint:function(a,b,t){return edgePoint(a,b,t)}," +
  "vbPerPx:function(){return typeof vbPerPx==='function'?vbPerPx():1}," +
  "seek:function(i,o){return seek(i,o||{})}};}catch(e){window.__QCCD_DEBUG_BRIDGE={ok:false,why:String(e)}}})();";

function bridge(win) {
  var doc = win.document;
  if (!win.__QCCD_DEBUG_BRIDGE) {
    var s = doc.createElement('script');
    s.textContent = BRIDGE_SRC;
    (doc.head || doc.documentElement).appendChild(s);
  }
  return win.__QCCD_DEBUG_BRIDGE || { ok: false, why: 'the page did not answer' };
}

// the Studio, framed: no site bar, the whole frame for the stage
var FRAME_CSS = '#sitenav,#sitefoot,#qcnav,#qc-layer{display:none!important}main{height:100vh!important}';

function attach(win) {
  var B = bridge(win);
  if (!B.ok) throw new Error(B.why || 'this page has no Studio animation');
  var doc = win.document;
  if (HOST === 'frame' && !doc.getElementById('qdbg-css')) {
    var st = doc.createElement('style');
    st.id = 'qdbg-css';
    st.textContent = FRAME_CSS;
    (doc.head || doc.documentElement).appendChild(st);
    // debugging a program, not designing one: the element rail folds (one click opens it again)
    try { if (S.fold && typeof win.foldPanel === 'function') win.foldPanel(doc.getElementById('rail'), true); } catch (e) { /* stays */ }
    try { if (typeof win.relayout === 'function') win.relayout(); } catch (e) { /* lays out itself */ }
  }
  // every frame the Studio draws, the overlay follows (ions move between frames too)
  if (typeof win.draw === 'function' && !win.draw.__qdbg) {
    var orig = win.draw;
    var wrapped = function () {
      var r = orig.apply(this, arguments);
      try { onDraw(); } catch (e) { if (window.console) console.warn('debug overlay', e); }
      return r;
    };
    wrapped.__qdbg = true;
    win.draw = wrapped;
  }
  if (!win.__qdbgAttached) {
    win.__qdbgAttached = true;
    // a zoom or a pan changes the viewBox, and the crosses keep their size on screen
    try {
      new win.MutationObserver(function () { paint(); }).observe(B.svg, { attributes: true, attributeFilter: ['viewBox'] });
    } catch (e) { /* no observer: the next draw repaints */ }
    B.svg.addEventListener('click', onStageClick, true);
  }
  S.win = win; S.B = B;
  syncFrames(true);
}

function syncFrames(force) {
  var B = S.B; if (!B) return;
  var fr = B.P.frames;
  if (!force && fr === S.frames) return;
  var changed = S.frames && fr !== S.frames;
  S.frames = fr; S.n = fr.length;
  S.events = {};
  if (changed && S.rules.result) { S.rules.stale = true; }
  $('dStep').max = String(Math.max(1, S.n));
  $('dOf').textContent = '/ ' + S.n;
}

// ------------------------------------------------------------------ reading the Studio

function frameAt(i) { return (S.frames && S.frames[i]) || {}; }
function posAfter(i) { var st = S.B.states[i]; return (st && st.pos) || {}; }
function posBefore(i) { return (S.B.before && S.B.before[i]) || {}; }
function nbarAfter(i) { var st = S.B.states[i]; return (st && st.q) || {}; }
function node(id) { return S.B.nodeById[id]; }
function nodeKind(id) {
  var n = node(id); if (!n) return null;
  return n.kind === 'junction' ? 'junction' : (n.kind || 'site');
}
function rail(id) { return S.B.segById[id]; }
function ions() {
  var seen = {}, out = [], i, k;
  for (i = 0; i < Math.min(S.n, 4); i++) for (k in posAfter(i)) if (!seen[k]) { seen[k] = 1; out.push(k); }
  for (k in posAfter(S.n - 1)) if (!seen[k]) { seen[k] = 1; out.push(k); }
  return out.sort(natural);
}
function natural(a, b) {
  var x = String(a).match(/^(\D*)(\d*)(.*)$/), y = String(b).match(/^(\D*)(\d*)(.*)$/);
  if (x[1] !== y[1]) return x[1] < y[1] ? -1 : 1;
  if (x[2] !== y[2]) return (+x[2] || 0) - (+y[2] || 0);
  return x[3] < y[3] ? -1 : x[3] > y[3] ? 1 : 0;
}
function kindOf(id) {
  if (!S.B) return null;
  if (node(id)) return nodeKind(id);
  if (rail(id)) return 'rail';
  var p = posAfter(Math.max(0, S.cur));
  if (own(p, id)) return 'ion';
  for (var i = 0; i < S.n; i += Math.max(1, Math.floor(S.n / 50))) if (own(posAfter(i), id)) return 'ion';
  return null;
}
function occupants(pos, id) {
  var out = [];
  for (var k in pos) if (pos[k] === id) out.push(k);
  return out.sort(natural);
}
function movedIn(i) {
  var a = posBefore(i), b = posAfter(i), out = [];
  for (var k in b) if (a[k] !== b[k]) out.push(k);
  return out.sort(natural);
}
function acted(f) {
  var out = {};
  (f.acts || []).forEach(function (t) { t.forEach(function (x) { out[x] = 1; }); });
  (f.pairs || []).forEach(function (t) { t.forEach(function (x) { out[x] = 1; }); });
  (f.ions || []).forEach(function (x) { out[x] = 1; });
  (f.moves || []).forEach(function (m) { out[m[0]] = 1; });
  if (f.type === 'init') for (var k in (f.place || {})) out[k] = 1;
  return out;
}
function describe(f, i) {
  // the instruction in a sentence, from the Studio's frame
  var t = f.type || '?';
  if (t === 'init') { var n = Object.keys(f.place || {}).length; return 'places ' + plural(n, 'ion'); }
  if (t === 'simd') {
    if (f.shift) return (f.cls ? f.cls + ': ' : '') + 'rotates loop ' + f.shift[0] + ' by ' + f.shift[1];
    if (f.c2lr) return (f.cls ? f.cls + ': ' : '') + (f.c2lr.kind || 'C2LR step');
    var mv = (f.moves || []).map(function (m) { var p = m[1] || []; return m[0] + ' ' + p[0] + '→' + p[p.length - 1]; });
    return (f.cls ? f.cls + ': ' : '') + (mv.length ? mv.slice(0, 4).join(', ') + (mv.length > 4 ? ', …' : '') : 'no move');
  }
  if (t === 'gate') {
    var ops = (f.pairs && f.pairs.length ? f.pairs : (f.acts || [])).map(function (a) { return a.join(','); });
    return (f.gate || 'gate') + ' ' + ops.slice(0, 6).join(' ') + (ops.length > 6 ? ' …' : '') +
      (f.sites && f.sites.length ? ' at ' + f.sites.slice(0, 3).join(', ') : '');
  }
  if (t === 'measure' || t === 'reset' || t === 'decode') return t + ' ' + (f.ions || []).slice(0, 8).join(',');
  if (t === 'cool') return 'cool ' + ((f.ions && f.ions.length) ? f.ions.slice(0, 8).join(',') : 'every ion');
  return t;
}

// does step i change or touch entity e?  (watch events; computed once per entity, then kept)
function eventsOf(id) {
  if (S.events[id]) return S.events[id];
  var kind = kindOf(id), out = new Uint8Array(S.n), i, f, a, b;
  for (i = 0; i < S.n; i++) {
    f = frameAt(i); a = posBefore(i); b = posAfter(i);
    if (kind === 'ion') {
      if (a[id] !== b[id] || acted(f)[id]) out[i] = 1;
    } else if (kind === 'rail') {
      var r = rail(id);
      if (!r) continue;
      for (var k in b) {
        if ((a[k] === r.a && b[k] === r.b) || (a[k] === r.b && b[k] === r.a)) { out[i] = 1; break; }
      }
      if (!out[i]) (f.moves || []).forEach(function (m) {
        var p = m[1] || [];
        for (var j = 1; j < p.length; j++) if ((p[j - 1] === r.a && p[j] === r.b) || (p[j - 1] === r.b && p[j] === r.a)) out[i] = 1;
      });
    } else if (kind) {
      if (occupants(a, id).join() !== occupants(b, id).join()) out[i] = 1;
      else if ((f.sites || []).indexOf(id) >= 0) out[i] = 1;
      else (f.moves || []).forEach(function (m) { if ((m[1] || []).indexOf(id) >= 0) out[i] = 1; });
    }
  }
  S.events[id] = out;
  return out;
}

// ------------------------------------------------------------------ going places

function goto(i, why) {
  if (!S.B || !S.n) return S.cur;
  i = Math.max(0, Math.min(S.n - 1, Math.round(i)));
  S.cur = i;                       // first: the Studio's redraw then finds the debugger already here
  S.B.seek(i, {});
  if (why) say(why);
  refresh();
  return i;
}
function stepBy(d) { return goto(S.cur + (d === undefined ? 1 : d)); }

function failureStarts() { return (S.F && S.F.starts) || []; }
function nextIn(list, from, back) {
  if (back) { for (var j = list.length - 1; j >= 0; j--) if (list[j] < from) return list[j]; }
  else { for (var k = 0; k < list.length; k++) if (list[k] > from) return list[k]; }
  return null;
}
function nextFailure(back) {
  var j = nextIn(failureStarts(), S.cur, back);
  if (j === null) { say(S.F ? 'no failure ' + (back ? 'before' : 'after') + ' this step' : 'check the rules first (Rules)'); return null; }
  goto(j);
  var here = (S.F.at[j] || []).filter(function (x) { return x.f0 === j; });
  say(here.length ? here[0].rule + ' fails here: ' + here[0].message : '');
  return j;
}
function watchEventSteps() {
  var out = [];
  if (!S.watch.length) return out;
  var ev = S.watch.map(function (w) { return eventsOf(w.id); });
  for (var i = 0; i < S.n; i++) for (var k = 0; k < ev.length; k++) if (ev[k][i]) { out.push(i); break; }
  return out;
}
function nextEvent(back, only) {
  var list = only ? stepsOf(eventsOf(only)) : watchEventSteps();
  if (!S.watch.length && !only) { say('watch something first (Watch)'); return null; }
  var j = nextIn(list, S.cur, back);
  if (j === null) { say('nothing you watch changes ' + (back ? 'before' : 'after') + ' this step'); return null; }
  return goto(j);
}
function stepsOf(arr) { var out = []; for (var i = 0; i < arr.length; i++) if (arr[i]) out.push(i); return out; }

// Continue: run to the next breakpoint.  Steps, failures and watch events are known here; a
// condition is the rule language's, asked of Python.
function continueRun(back) {
  if (!S.B) return Promise.resolve(null);
  var cands = [], why = {};
  function cand(i, w) { if (i !== null && i !== undefined) { cands.push(i); why[i] = why[i] || w; } }
  cand(nextIn(S.bps.steps.map(function (s) { return s - 1; }).sort(function (a, b) { return a - b; }), S.cur, back), 'a breakpoint on this step');
  if (S.bps.onFail && S.F) cand(nextIn(failureStarts(), S.cur, back), 'a rule starts failing here');
  if (S.bps.onWatch && S.watch.length) cand(nextIn(watchEventSteps(), S.cur, back), 'something you watch changes here');
  var conds = S.bps.conds.filter(function (c) { return c.on; });
  var asks = conds.map(function (c) {
    return pyCall('find', { condition: c.text, start: S.cur + 1, backward: !!back }).then(function (r) {
      if (r.found) cand(r.step - 1, 'the condition ' + c.text + ' holds (' + r.instant + ')');
      else if (r.error) c.error = r.error;
    }, function (e) { c.error = String(e.message || e); });
  });
  say(asks.length ? 'running ' + (back ? 'backward' : 'forward') + '…' : '');
  return Promise.all(asks).then(function () {
    if (!cands.length) {
      goto(back ? 0 : S.n - 1);
      say('ran to the ' + (back ? 'first' : 'last') + ' step: no breakpoint on the way');
      return { step: S.cur + 1, stopped: 'the end' };
    }
    var j = back ? Math.max.apply(null, cands) : Math.min.apply(null, cands);
    goto(j);
    say('stopped at step ' + (j + 1) + ': ' + why[j]);
    renderPane();
    return { step: j + 1, stopped: why[j] };
  });
}

// ------------------------------------------------------------------ the overlay

function onDraw() {
  if (!S.B) return;
  if (!S.active) { paint(); return; }
  syncFrames(false);
  var f = S.B.frame;
  if (typeof f === 'number' && f !== S.cur) { S.cur = f; refreshSoon(); }
  paint();
}
// the Studio playing at 256x changes step every animation frame: the panels follow at most ten
// times a second, and once more when it stops
var refreshT = null, refreshedAt = 0;
function refreshSoon() {
  if (refreshT) return;
  var wait = Math.max(0, 100 - (Date.now() - refreshedAt));
  refreshT = setTimeout(function () { refreshT = null; refreshedAt = Date.now(); refresh(); }, wait);
}

function svgEl(doc, tag, attrs) {
  var e = doc.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function nodeXY(id) {
  var n = node(id); if (!n) return null;
  return [S.B.px(n), S.B.py(n)];
}
function ionXY(id) {
  var p = S.B.IONP[id];
  if (p && p.c && p.c.getAttribute('display') !== 'none' && p.c.getAttribute('cx') !== null) {
    return [+p.c.getAttribute('cx'), +p.c.getAttribute('cy'), +(p.c.getAttribute('r') || 6)];
  }
  var at = posAfter(S.cur)[id];
  var xy = at ? nodeXY(at) : null;
  return xy ? [xy[0], xy[1], 6] : null;
}
function railPts(id) {
  var r = rail(id); if (!r) return null;
  var out = [];
  for (var k = 0; k <= 8; k++) {
    var p = S.B.edgePoint(r.a, r.b, k / 8);
    if (p) out.push([p.x, p.y]);
  }
  return out.length ? out : null;
}
function whereXY(w) {
  if (w.kind === 'ion') { var q = ionXY(w.id); if (q) return q; return w.at ? nodeXY(w.at) : null; }
  if (w.kind === 'rail') { var pts = railPts(w.id); return pts ? pts[4] || pts[0] : null; }
  if (w.kind === 'loop') return null;
  return nodeXY(w.id);
}

function paint() {
  var B = S.B; if (!B) return;
  var doc = S.win.document, svg = B.svg;
  var g = doc.getElementById('qdbg');
  if (!S.active) { if (g) g.remove(); return; }       // the panel closed: nothing of it stays
  if (!g) { g = svgEl(doc, 'g', { id: 'qdbg' }); }
  if (g.parentNode !== svg || svg.lastChild !== g) svg.appendChild(g);
  while (g.firstChild) g.removeChild(g.firstChild);
  var k = B.vbPerPx() || 1, i = S.cur;
  // isolate: a veil over the whole picture, and above it again only what you watch (and the ions
  // crossed out at this step): the rest of the machine stays visible, faded, for context
  if (S.isolate) {
    g.appendChild(svgEl(doc, 'rect', { x: -1e5, y: -1e5, width: 2e5, height: 2e5, fill: '#ffffff', 'fill-opacity': '0.72',
      'pointer-events': 'none' }));
    var keep = {};
    S.watch.forEach(function (w) { if (w.kind === 'ion') keep[w.id] = 1; });
    if (S.crosses) here().forEach(function (x) { x.where.forEach(function (w) { if (w.kind === 'ion') keep[w.id] = 1; }); });
    for (var ion in keep) {
      var p = B.IONP[ion];
      if (!p || !p.c || p.c.getAttribute('display') === 'none') continue;
      [p.c, p.t].forEach(function (e) {
        if (!e) return;
        var c = e.cloneNode(true);
        c.removeAttribute('id'); c.setAttribute('pointer-events', 'none');
        g.appendChild(c);
      });
    }
  }
  // trails: where each watched ion has been, the last steps up to this one
  if (S.trails) S.watch.forEach(function (w) {
    if (w.kind !== 'ion' || w.trail === false) return;
    var pts = [], last = null;
    for (var j = Math.max(0, i - 40); j <= i; j++) {
      var at = posAfter(j)[w.id]; if (!at || at === last) continue;
      var xy = nodeXY(at); if (xy) { pts.push(xy); last = at; }
    }
    var now = ionXY(w.id); if (now) pts.push([now[0], now[1]]);
    if (pts.length > 1) {
      g.appendChild(svgEl(doc, 'polyline', { points: pts.map(function (q) { return q[0].toFixed(1) + ',' + q[1].toFixed(1); }).join(' '),
        fill: 'none', stroke: w.color, 'stroke-width': (3 * k).toFixed(2), 'stroke-opacity': '0.45',
        'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': (6 * k) + ' ' + (4 * k), 'pointer-events': 'none' }));
      pts.slice(0, -1).forEach(function (q) {
        g.appendChild(svgEl(doc, 'circle', { cx: q[0], cy: q[1], r: 2.6 * k, fill: w.color, 'fill-opacity': '0.55', 'pointer-events': 'none' }));
      });
    }
  });
  // watched things, each in its colour
  S.watch.forEach(function (w) {
    var lab = null;
    if (w.kind === 'ion') {
      var q = ionXY(w.id); if (!q) return;
      g.appendChild(svgEl(doc, 'circle', { cx: q[0], cy: q[1], r: q[2] + 4.5 * k, fill: 'none', stroke: w.color,
        'stroke-width': (2.6 * k).toFixed(2), 'pointer-events': 'none' }));
      lab = [q[0] + q[2] + 6 * k, q[1] - q[2] - 3 * k];
    } else if (w.kind === 'rail') {
      var pts = railPts(w.id); if (!pts) return;
      g.appendChild(svgEl(doc, 'polyline', { points: pts.map(function (q) { return q.join(','); }).join(' '), fill: 'none',
        stroke: w.color, 'stroke-width': (7 * k).toFixed(2), 'stroke-opacity': '0.4', 'stroke-linecap': 'round', 'pointer-events': 'none' }));
      lab = [pts[4][0] + 6 * k, pts[4][1] - 6 * k];
    } else {
      var c = nodeXY(w.id); if (!c) return;
      var s = 13 * k;
      if (w.kind === 'junction') {
        g.appendChild(svgEl(doc, 'polygon', { points: [[c[0], c[1] - s], [c[0] + s, c[1]], [c[0], c[1] + s], [c[0] - s, c[1]]].map(function (q) { return q.join(','); }).join(' '),
          fill: w.color, 'fill-opacity': '0.08', stroke: w.color, 'stroke-width': (2.4 * k).toFixed(2), 'pointer-events': 'none' }));
      } else {
        g.appendChild(svgEl(doc, 'rect', { x: c[0] - s, y: c[1] - s, width: 2 * s, height: 2 * s, rx: 3 * k,
          fill: w.color, 'fill-opacity': '0.08', stroke: w.color, 'stroke-width': (2.4 * k).toFixed(2), 'pointer-events': 'none' }));
      }
      lab = [c[0] + s + 3 * k, c[1] - s];
    }
    if (lab) label(doc, g, lab[0], lab[1], w.id, w.color, k);
  });
  // the crosses: every failure of a shown rule at this step, where it happens.  A place (site,
  // junction, rail) gets a cross; an ion standing in a place the same failure names gets a red
  // ring instead, and crosses closer than 16 px on screen are one cross with every rule beside it
  if (S.crosses && S.F) {
    var spots = [], rings = {};
    here().forEach(function (x) {
      var named = {};
      x.where.forEach(function (w) { if (w.kind !== 'ion') named[w.id] = 1; });
      x.where.forEach(function (w) {
        if (w.kind === 'loop' || w.kind === 'circuit') return;
        if (w.kind === 'ion' && w.at && named[w.at]) { rings[w.id] = rings[w.id] || x.f0 === i; return; }
        var xy = whereXY(w); if (!xy) return;
        var sp = null;
        for (var j = 0; j < spots.length; j++) if (Math.hypot(spots[j].x - xy[0], spots[j].y - xy[1]) < 16 * k) { sp = spots[j]; break; }
        if (!sp) { sp = { x: xy[0], y: xy[1], items: [], fresh: false }; spots.push(sp); }
        if (sp.items.indexOf(x) < 0) sp.items.push(x);
        if (x.f0 === i) sp.fresh = true;
      });
    });
    for (var ri in rings) {
      var q2 = ionXY(ri); if (!q2) continue;
      g.appendChild(svgEl(doc, 'circle', { cx: q2[0], cy: q2[1], r: q2[2] + 2.2 * k, fill: 'none', stroke: RED,
        'stroke-width': ((rings[ri] ? 2.4 : 1.6) * k).toFixed(2), 'pointer-events': 'none' }));
    }
    spots.forEach(function (sp) { cross(doc, g, sp, k); });
  }
  if (S.flash && S.flash.until > Date.now()) {
    var fx = whereXY(S.flash.where);
    if (fx) g.appendChild(svgEl(doc, 'circle', { cx: fx[0], cy: fx[1], r: 22 * k, fill: 'none', stroke: '#111827',
      'stroke-width': (2 * k).toFixed(2), 'stroke-dasharray': (4 * k) + ' ' + (3 * k), 'pointer-events': 'none' }));
  }
}

function label(doc, g, x, y, text, color, k) {
  var t = svgEl(doc, 'text', { x: x, y: y, 'font-size': (11 * k).toFixed(2), 'font-weight': '700', fill: color,
    stroke: '#ffffff', 'stroke-width': (3 * k).toFixed(2), 'paint-order': 'stroke', 'stroke-linejoin': 'round',
    'font-family': 'ui-sans-serif,system-ui,sans-serif', 'pointer-events': 'none' });
  t.textContent = text;
  g.appendChild(t);
}

function cross(doc, g, sp, k) {
  var s = (sp.fresh ? 9 : 7) * k, w = (sp.fresh ? 3.4 : 2.4) * k;
  var grp = svgEl(doc, 'g', { 'class': 'qdbg-x', cursor: 'pointer', 'data-rules': sp.items.map(function (x) { return x.rule; }).join(' ') });
  grp.appendChild(svgEl(doc, 'circle', { cx: sp.x, cy: sp.y, r: s + 4 * k, fill: '#ffffff', 'fill-opacity': '0.75', stroke: RED,
    'stroke-width': (1.2 * k).toFixed(2), 'stroke-opacity': sp.fresh ? '0.9' : '0.45' }));
  [[-1, -1, 1, 1], [-1, 1, 1, -1]].forEach(function (d) {
    grp.appendChild(svgEl(doc, 'line', { x1: sp.x + d[0] * s, y1: sp.y + d[1] * s, x2: sp.x + d[2] * s, y2: sp.y + d[3] * s,
      stroke: RED, 'stroke-width': w.toFixed(2), 'stroke-linecap': 'round', 'stroke-opacity': sp.fresh ? '1' : '0.7' }));
  });
  var ids = [], seen = {};
  sp.items.forEach(function (x) { if (!seen[x.rule]) { seen[x.rule] = 1; ids.push(x.rule); } });
  var tag = svgEl(doc, 'text', { x: sp.x + s + 5 * k, y: sp.y + 4 * k, 'font-size': (11.5 * k).toFixed(2), 'font-weight': '800', fill: RED,
    stroke: '#ffffff', 'stroke-width': (3.2 * k).toFixed(2), 'paint-order': 'stroke', 'stroke-linejoin': 'round',
    'font-family': 'ui-sans-serif,system-ui,sans-serif' });
  tag.textContent = ids.slice(0, 3).join(' · ') + (ids.length > 3 ? ' +' + (ids.length - 3) : '');
  grp.appendChild(tag);
  var tt = svgEl(doc, 'title', {});
  tt.textContent = sp.items.map(function (x) { return x.rule + ': ' + x.message; }).join('\n');
  grp.appendChild(tt);
  grp.addEventListener('click', function (e) {
    e.stopPropagation(); e.preventDefault();
    var x = sp.items[0];
    S.rules.open[x.rule] = true; showTab('rules'); selectFailure(x);
  }, true);
  g.appendChild(grp);
}

// a click on the stage while Pick is on watches what is under it
function onStageClick(e) {
  if (!S.picking || !S.B) return;
  e.stopPropagation(); e.preventDefault();
  var svg = S.B.svg, pt = svg.createSVGPoint();
  pt.x = e.clientX; pt.y = e.clientY;
  var m = svg.getScreenCTM(); if (!m) return;
  var q = pt.matrixTransform(m.inverse()), k = S.B.vbPerPx() || 1, best = null, bd = 18 * k;
  for (var ion in S.B.IONP) {
    var xy = ionXY(ion); if (!xy) continue;
    var d = Math.hypot(xy[0] - q.x, xy[1] - q.y);
    if (d < Math.max(bd, xy[2] + 3 * k)) { bd = d; best = ion; }
  }
  if (!best) {
    S.B.A.nodes.forEach(function (n) {
      var d = Math.hypot(S.B.px(n) - q.x, S.B.py(n) - q.y);
      if (d < bd) { bd = d; best = n.id; }
    });
  }
  if (!best) {
    S.B.A.segments.forEach(function (s) {
      var p = S.B.edgePoint(s.a, s.b, 0.5);
      if (p) { var d = Math.hypot(p.x - q.x, p.y - q.y); if (d < bd) { bd = d; best = s.id; } }
    });
  }
  if (best) { addWatch([best]); say('watching ' + best); }
}

// ------------------------------------------------------------------ watching

function addWatch(ids) {
  var added = [], bad = [];
  (ids || []).forEach(function (raw) {
    var id = String(raw).trim(); if (!id) return;
    if (S.watch.some(function (w) { return w.id === id; })) return;
    var kind = kindOf(id);
    if (!kind) { bad.push(id); return; }
    var used = S.watch.map(function (w) { return w.color; });
    var color = PALETTE.filter(function (c) { return used.indexOf(c) < 0; })[0] || PALETTE[S.watch.length % PALETTE.length];
    S.watch.push({ id: id, kind: kind, color: color, trail: true });
    added.push(id);
  });
  if (added.length || bad.length) { writeUrl(); refresh(); }
  if (bad.length) say((bad.length === 1 ? bad[0] + ' is' : bad.join(', ') + ' are') + ' not an ion, site, junction or rail here');
  return { added: added, unknown: bad };
}
function removeWatch(ids) {
  S.watch = S.watch.filter(function (w) { return (ids || []).indexOf(w.id) < 0; });
  writeUrl(); refresh();
}
function lookAt(id, i) {
  var w = typeof id === 'string' ? { id: id, kind: kindOf(id) } : id;
  var pos = posAfter(i), f = frameAt(i);
  if (w.kind === 'ion') {
    var at = pos[w.id], q = nbarAfter(i)[w.id];
    return { kind: 'ion', at: at === undefined ? null : at, at_kind: at ? nodeKind(at) : null,
             nbar: q === undefined ? null : +(+q).toFixed(4), this_step: acted(f)[w.id] ? describe(f, i) : null,
             moved: posBefore(i)[w.id] !== at };
  }
  if (w.kind === 'rail') {
    var r = rail(w.id) || {}, a = posBefore(i), crossing = [];
    for (var k in pos) if ((a[k] === r.a && pos[k] === r.b) || (a[k] === r.b && pos[k] === r.a)) crossing.push(k);
    (f.moves || []).forEach(function (m) {
      var p = m[1] || [];
      for (var j = 1; j < p.length; j++) if (((p[j - 1] === r.a && p[j] === r.b) || (p[j - 1] === r.b && p[j] === r.a)) && crossing.indexOf(m[0]) < 0) crossing.push(m[0]);
    });
    return { kind: 'rail', ends: [r.a, r.b], capacity: r.cap === undefined ? null : r.cap, crossing: crossing.sort(natural) };
  }
  var n = node(w.id) || {}, here_ = occupants(pos, w.id);
  return { kind: w.kind, ions: here_, capacity: n.cap === undefined ? null : n.cap,
           over: n.cap !== undefined && n.cap !== null && here_.length > n.cap,
           changed: occupants(posBefore(i), w.id).join() !== here_.join() };
}
function lookText(L) {
  if (L.kind === 'ion') return (L.at ? 'at ' + L.at + (L.at_kind === 'junction' ? ' (junction)' : '') : 'in transit') +
    (L.nbar !== null ? ' · n̄ ' + L.nbar : '') + (L.this_step ? ' · ' + L.this_step : '');
  if (L.kind === 'rail') return (L.crossing.length ? 'crossed by ' + L.crossing.join(', ') : 'nobody crosses') +
    (L.capacity !== null ? ' · capacity ' + L.capacity : '');
  return (L.ions.length ? L.ions.join(', ') : 'empty') + (L.capacity !== null ? ' · ' + L.ions.length + '/' + L.capacity : '') +
    (L.over ? ' · OVER CAPACITY' : '');
}
function trace(id, from, to, limit) {
  var kind = kindOf(id);
  if (!kind) throw new Error(id + ' is not an ion, site, junction or rail of this program');
  var a = Math.max(0, (from || 1) - 1), b = Math.min(S.n - 1, (to || S.n) - 1), ev = eventsOf(id), out = [];
  for (var i = a; i <= b; i++) {
    if (i !== a && !ev[i]) continue;
    out.push(Object.assign({ step: i + 1, instruction: describe(frameAt(i), i) }, lookAt({ id: id, kind: kind }, i)));
    if (out.length >= (limit || 200)) return { entity: id, kind: kind, events: out, truncated: true };
  }
  return { entity: id, kind: kind, events: out, truncated: false };
}

// ------------------------------------------------------------------ Python (the rule language)

var PY = { w: null, seq: 0, wait: {}, state: 'off', note: '', ready: null, key: null, digest: null };
function pyStart() {
  if (PY.ready) return PY.ready;
  if (typeof Worker === 'undefined') return Promise.reject(new Error('this browser cannot run the rule language (no workers)'));
  PY.state = 'loading';
  // the smallest WebAssembly module there is: a page whose policy refuses WebAssembly says so here,
  // at once, where the runtime itself would only stop answering
  var probe = (typeof WebAssembly === 'object' && WebAssembly.compile)
    ? WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]))
    : Promise.reject(new Error('this browser has no WebAssembly'));
  PY.ready = probe.then(null, function (e) {
    PY.state = 'failed';
    PY.note = 'the rule language cannot run on this page: ' + (/Content Security|wasm-unsafe-eval|unsafe-eval/i.test(String(e && e.message))
      ? 'the page\'s security policy refuses WebAssembly' : String((e && e.message) || e));
    renderPy();
    throw new Error(PY.note);
  }).then(function () { return new Promise(function (resolve, reject) {
    try { PY.w = new Worker(CFG.python.worker || 'worker.js', { type: 'module' }); }
    catch (e) { PY.state = 'failed'; PY.note = String(e.message || e); reject(e); return; }
    var fail = function (why) {
      PY.state = 'failed'; PY.note = 'the rule language could not start in this browser: ' + why;
      for (var id in PY.wait) { PY.wait[id][1](new Error(PY.note)); delete PY.wait[id]; }
      renderPy(); reject(new Error(PY.note));
    };
    var slow = setTimeout(function () { if (PY.state === 'loading') fail('it did not answer in two minutes (a slow connection, or a page policy refusing WebAssembly)'); }, 120000);
    PY.w.onmessage = function (e) {
      var m = e.data || {};
      if (m.fatal) { clearTimeout(slow); fail(m.fatal); return; }
      if (m.progress) { PY.note = m.progress; say(m.progress); renderPy(); return; }
      var cb = PY.wait[m.id]; if (!cb) return;
      delete PY.wait[m.id];
      if (m.ok) cb[0](m.out); else cb[1](new Error(m.error));
    };
    PY.w.onerror = function (e) { PY.state = 'failed'; PY.note = 'the rule language did not start: ' + (e.message || 'an error'); renderPy(); reject(new Error(PY.note)); };
    send(null, []).then(function () { clearTimeout(slow); PY.state = 'ready'; renderPy(); resolve(); }, function (e) { clearTimeout(slow); fail(e.message); });
  }); });
  return PY.ready;
}
function send(fn, args) {
  return new Promise(function (res, rej) {
    var id = ++PY.seq;
    PY.wait[id] = [res, rej];
    PY.w.postMessage({ id: id, fn: fn, args: args });
  });
}
function pyJSON(fn, args) {
  return pyStart().then(function () { return send(fn, args); }).then(function (out) {
    var r = JSON.parse(out);
    if (!r.ok) throw new Error(r.error || 'the rule language refused');
    return r;
  });
}
// the device and programme as the Studio exports them, opened once per programme
function pySession() {
  if (!S.win || !S.win.EDITOR) return Promise.reject(new Error('open a program first'));
  var pair;
  try { pair = S.win.EDITOR.exportPair(); }
  catch (e) { return Promise.reject(new Error('the Studio cannot export this design: ' + (e.message || e))); }
  var model = (S.B.D && S.B.D.model) || null;
  var digest = pair.arch.text.length + ':' + pair.tsir.text.length + ':' + hash(pair.tsir.text) + hash(pair.arch.text);
  if (PY.key && PY.digest === digest) return Promise.resolve(PY.key);
  return pyJSON('open_session', [pair.arch.text, pair.tsir.text, JSON.stringify(model), S.title || 'programme']).then(function (r) {
    PY.key = r.key; PY.digest = digest; PY.summary = r.summary;
    PY.aligned = r.summary.steps === S.n;
    return r.key;
  });
}
function hash(s) { var h = 2166136261; for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); }
function pyCall(verb, args) {
  return pySession().then(function (key) { return pyJSON('call', [key, verb, JSON.stringify(args || {})]); });
}

// ------------------------------------------------------------------ rules

function runCheck(opts) {
  opts = opts || {};
  if (opts.rules !== undefined) S.rules.set = String(opts.rules);
  if (opts.assertions !== undefined) S.rules.assertions = String(opts.assertions);
  if (!S.B) return Promise.reject(new Error('open a program first'));
  S.rules.running = true; S.rules.error = ''; renderPane();
  var t0 = Date.now();
  var set = S.rules.set === 'none' ? null : S.rules.set;
  var args = { rules: set, assertions: S.rules.assertions || null, limit: 400 };
  if (opts.only) args.only = opts.only;
  say('checking…');
  return pyCall('check', args).then(function (r) {
    S.rules.running = false; S.rules.stale = false;
    S.rules.result = r; S.rules.ms = Date.now() - t0;
    S.rules.shown = {};
    r.rules.forEach(function (x) { if (x.verdict === 'fails') S.rules.shown[x.id] = true; });
    indexFailures();
    writeUrl();
    say(r.failing.length ? plural(r.failing.length, 'rule') + ' fail: ' + r.failing.join(', ') : 'every rule that applies holds');
    refresh();
    return r;
  }, function (e) {
    S.rules.running = false; S.rules.error = String(e.message || e);
    say(S.rules.error); renderPane();
    throw e;
  });
}

// the located failures, by frame: `at[i]` every failure crossed out at frame i
function indexFailures() {
  var r = S.rules.result; if (!r) { S.F = null; return; }
  var list = [], always = [];
  r.rules.forEach(function (x) {
    (x.failures || []).forEach(function (f) {
      var e = { rule: x.id, title: x.title, message: f.message, where: f.where || [], instant: f.instant, phase: f.phase,
                step: f.step, until: f.until, values: f.values };
      if (f.step === null || f.step === undefined) { e.f0 = 0; e.f1 = S.n - 1; e.always = true; always.push(e); }
      else { e.f0 = Math.max(0, Math.min(S.n - 1, f.step - 1)); e.f1 = Math.max(e.f0, Math.min(S.n - 1, (f.until || f.step) - 1)); }
      list.push(e);
    });
  });
  var at = new Array(S.n), starts = {};
  list.forEach(function (e) {
    if (!S.rules.shown[e.rule]) return;
    for (var i = e.f0; i <= e.f1; i++) (at[i] || (at[i] = [])).push(e);
    if (!e.always) starts[e.f0] = 1;
  });
  S.F = { list: list, at: at, starts: Object.keys(starts).map(Number).sort(function (a, b) { return a - b; }), always: always };
}
function here() { return (S.F && S.F.at[S.cur]) || []; }

function selectFailure(x) {
  if (!x) return;
  goto(x.f0);
  var w = x.where.filter(function (q) { return q.kind !== 'circuit' && q.kind !== 'loop'; })[0];
  if (w) { S.flash = { where: w, until: Date.now() + 2400 }; paint(); setTimeout(paint, 2500); }
  say(x.rule + ' at step ' + (x.f0 + 1) + (x.f1 > x.f0 ? '–' + (x.f1 + 1) : '') + ': ' + x.message);
}

// ------------------------------------------------------------------ the panels

function refresh() {
  if (!S.B) return;
  var inp = $('dStep');
  if (document.activeElement !== inp) inp.value = String(S.cur + 1);
  paint();
  renderPane();
  drawTimeline();
  writeUrl();
  if (S.exprs.length && S.tab === 'watch') evalExprs();
}

function showTab(t) {
  S.tab = t;
  Array.prototype.forEach.call(document.querySelectorAll('#dTabs [role=tab]'), function (b) {
    b.setAttribute('aria-selected', b.getAttribute('data-tab') === t ? 'true' : 'false');
  });
  Array.prototype.forEach.call(document.querySelectorAll('#dbgSide .q-pane'), function (p) {
    p.hidden = p.getAttribute('data-pane') !== t;
  });
  renderPane();
  writeUrl();
}

function renderPane() {
  var t = S.tab;
  if (t === 'state') renderState();
  else if (t === 'watch') renderWatch();
  else if (t === 'rules') renderRules();
  else if (t === 'breaks') renderBreaks();
  else if (t === 'report') renderReport();
}

function ent(id, kind) {
  return '<button type="button" class="q-ent" data-hint="debug:entity" data-ent="' + esc(id) + '" title="watch ' + esc(id) + '">' + esc(id) + '</button>';
}
function stateOf(i) {
  var f = frameAt(i), pos = posAfter(i), over = [], counts = {}, k;
  for (k in pos) if (pos[k]) counts[pos[k]] = (counts[pos[k]] || 0) + 1;
  for (k in counts) { var n = node(k); if (n && n.cap !== undefined && n.cap !== null && counts[k] > n.cap) over.push({ place: k, ions: counts[k], capacity: n.cap }); }
  var q = nbarAfter(i), hot = Object.keys(q).sort(function (a, b) { return q[b] - q[a]; }).slice(0, 5)
    .map(function (x) { return { ion: x, nbar: +(+q[x]).toFixed(4) }; });
  var fails = here().filter(function (x) { return true; }).map(function (x) {
    return { rule: x.rule, message: x.message, from: x.f0 + 1, until: x.f1 + 1, starts_here: x.f0 === i, where: x.where };
  });
  var watched = {};
  S.watch.forEach(function (w) { watched[w.id] = lookAt(w, i); });
  return { step: i + 1, of: S.n, type: f.type, class: f.cls || null, instruction: describe(f, i), moved: movedIn(i),
           over_capacity: over, hottest: hot, watch: watched, failures: fails,
           checked: !!S.rules.result, stale: !!S.rules.stale };
}

function renderState() {
  var el = $('pState');
  if (!S.B) { el.innerHTML = '<p class="q-mut">Open a program to step through it.</p>'; return; }
  var st = stateOf(S.cur), f = frameAt(S.cur);
  var h = '<h3>Step ' + st.step + ' <span class="q-mut">/ ' + st.of + '</span> <code>' + esc(st.type) + '</code>' +
          (st.class ? ' <code>' + esc(st.class) + '</code>' : '') + '</h3>';
  h += '<p class="q-what">' + esc(st.instruction) + '</p>';
  if (f.moves && f.moves.length) {
    h += '<details open><summary>' + plural(f.moves.length, 'ion') + ' move</summary><ul class="q-kv">' +
      f.moves.slice(0, 40).map(function (m) { return '<li>' + ent(m[0]) + ' <span class="q-mut">' + esc((m[1] || []).join(' → ')) + '</span></li>'; }).join('') +
      (f.moves.length > 40 ? '<li class="q-mut">… and ' + (f.moves.length - 40) + ' more</li>' : '') + '</ul></details>';
  } else if (st.moved.length) {
    h += '<p><b>Moved:</b> ' + st.moved.slice(0, 40).map(function (x) { return ent(x); }).join(' ') + (st.moved.length > 40 ? ' …' : '') + '</p>';
  }
  if (S.rules.result) {
    var fl = st.failures;
    h += '<h4>' + (fl.length ? '<span class="q-bad">✗ ' + plural(fl.length, 'failure') + ' here</span>' : '<span class="q-ok">✓ no shown rule fails here</span>') +
         (S.rules.stale ? ' <span class="q-mut">(the program changed since it was checked)</span>' : '') + '</h4>';
    if (fl.length) h += '<ul class="q-fails">' + fl.slice(0, 30).map(function (x, j) {
      return '<li><button type="button" class="q-fl" data-hint="debug:failure" data-fi="' + j + '"><b>' + esc(x.rule) + '</b> ' +
        (x.starts_here ? '<span class="q-new">starts here</span> ' : '<span class="q-mut">since step ' + x.from + '</span> ') + esc(x.message) + '</button></li>';
    }).join('') + '</ul>';
  } else {
    h += '<p class="q-mut">Rules: not checked yet. Open <b>Rules</b> and press Check to see what fails here.</p>';
  }
  if (st.over_capacity.length) h += '<p class="q-bad"><b>Over capacity:</b> ' + st.over_capacity.map(function (o) { return ent(o.place) + ' ' + o.ions + '/' + o.capacity; }).join(', ') + '</p>';
  if (S.watch.length) {
    h += '<h4>Watched</h4><ul class="q-kv">' + S.watch.map(function (w) {
      return '<li><i class="q-dot" style="background:' + w.color + '"></i>' + ent(w.id) + ' <span class="q-mut">' + esc(lookText(st.watch[w.id])) + '</span></li>';
    }).join('') + '</ul>';
  }
  if (st.hottest.length && st.hottest[0].nbar > 0) {
    h += '<h4>Hottest ions <span class="q-mut">(n̄, the Studio\'s heating)</span></h4><p>' +
      st.hottest.map(function (x) { return ent(x.ion) + ' ' + x.nbar; }).join(' · ') + '</p>';
  }
  el.innerHTML = h;
  Array.prototype.forEach.call(el.querySelectorAll('.q-fl'), function (b) {
    b.onclick = function () { var x = here()[+b.getAttribute('data-fi')]; if (x) selectFailure(x); };
  });
  wireEnts(el);
}
function wireEnts(el) {
  Array.prototype.forEach.call(el.querySelectorAll('.q-ent'), function (b) {
    b.onclick = function () { addWatch([b.getAttribute('data-ent')]); };
  });
}

function allIds() {
  if (!S.B) return [];
  return ions().concat(S.B.A.nodes.map(function (n) { return n.id; }), S.B.A.segments.map(function (s) { return s.id; }));
}

function renderWatch() {
  var el = $('pWatch');
  if (!S.B) { el.innerHTML = '<p class="q-mut">Open a program to watch its ions, sites, junctions and rails.</p>'; return; }
  var ids = allIds();
  var h = '<p class="q-mut">Highlight and follow parts of the machine: each gets a colour on the picture, ions a trail, and ' +
          '◆◀ ▶◆ jump to the steps where any of them changes.</p>' +
          '<div class="q-row"><input id="wIn" list="wIds" placeholder="q0, C3, J1 …" data-hint="debug:watch:input" aria-label="ids to watch">' +
          '<datalist id="wIds">' + ids.slice(0, 3000).map(function (x) { return '<option value="' + esc(x) + '">'; }).join('') + '</datalist>' +
          '<button type="button" id="wAdd" data-hint="debug:watch:add">Watch</button></div>' +
          '<div class="q-row"><button type="button" id="wSel" data-hint="debug:watch:selection">Watch the Studio\'s selection</button>' +
          '<button type="button" id="wPick" class="q-tgl' + (S.picking ? ' q-on' : '') + '" aria-pressed="' + S.picking + '" data-hint="debug:watch:pick">Pick on the picture</button></div>';
  if (S.watch.length) {
    h += '<ul class="q-wl">' + S.watch.map(function (w, j) {
      var L = lookAt(w, S.cur);
      return '<li><i class="q-dot" style="background:' + w.color + '"></i><b>' + esc(w.id) + '</b> <span class="q-mut">' + esc(w.kind) + '</span>' +
        '<span class="q-grow"></span>' +
        '<button type="button" class="q-mini" data-hint="debug:watch:prev" data-w="' + j + '" data-a="prev" title="previous step where ' + esc(w.id) + ' changes">◀</button>' +
        '<button type="button" class="q-mini" data-hint="debug:watch:next" data-w="' + j + '" data-a="next" title="next step where ' + esc(w.id) + ' changes">▶</button>' +
        (w.kind === 'ion' ? '<button type="button" class="q-mini q-tgl' + (w.trail !== false ? ' q-on' : '') + '" data-hint="debug:watch:trail" data-w="' + j + '" data-a="trail" title="trail">trail</button>' : '') +
        '<button type="button" class="q-mini" data-hint="debug:watch:remove" data-w="' + j + '" data-a="del" title="stop watching" aria-label="stop watching ' + esc(w.id) + '">×</button>' +
        '<div class="q-sub">' + esc(lookText(L)) + '</div></li>';
    }).join('') + '</ul>';
    h += '<p><button type="button" id="wClear" data-hint="debug:watch:clear">Stop watching all</button></p>';
  }
  h += '<h4>Watch expressions <span class="q-mut">(the rule language, with t the instant after this step)</span></h4>' +
       '<div class="q-row"><input id="xIn" placeholder="occupancy(&quot;C0&quot;, t)" data-hint="debug:expr:input" aria-label="an expression">' +
       '<button type="button" id="xAdd" data-hint="debug:expr:add">Add</button></div>';
  if (S.exprs.length) h += '<ul class="q-wl">' + S.exprs.map(function (x, j) {
    var v = S.exprVals[x];
    return '<li><code>' + esc(x) + '</code><span class="q-grow"></span><b class="' + (v && v.error ? 'q-bad' : '') + '">' +
      esc(v === undefined ? '…' : (v.error || v.text)) + '</b>' +
      '<button type="button" class="q-mini" data-hint="debug:expr:remove" data-x="' + j + '" aria-label="remove">×</button></li>';
  }).join('') + '</ul>';
  el.innerHTML = h;
  var inp = $('wIn');
  $('wAdd').onclick = function () { addWatch(inp.value.split(/[\s,]+/)); inp.value = ''; };
  inp.onkeydown = function (e) { if (e.key === 'Enter') $('wAdd').onclick(); };
  $('wSel').onclick = function () {
    var sel = [];
    try { sel = (S.win.EDITOR.selection() || []).map(function (s) { return s.id; }); } catch (e) { sel = []; }
    if (!sel.length) say('nothing is selected in the Studio: click a site, junction or rail there first (or use Pick)');
    else addWatch(sel);
  };
  $('wPick').onclick = function () { S.picking = !S.picking; renderWatch(); say(S.picking ? 'click an ion, site, junction or rail on the picture to watch it' : ''); };
  if ($('wClear')) $('wClear').onclick = function () { S.watch = []; writeUrl(); refresh(); };
  Array.prototype.forEach.call(el.querySelectorAll('[data-w]'), function (b) {
    b.onclick = function () {
      var w = S.watch[+b.getAttribute('data-w')], a = b.getAttribute('data-a'); if (!w) return;
      if (a === 'del') removeWatch([w.id]);
      else if (a === 'trail') { w.trail = w.trail === false; paint(); renderWatch(); }
      else nextEvent(a === 'prev', w.id);
    };
  });
  var xi = $('xIn');
  $('xAdd').onclick = function () { var v = xi.value.trim(); if (v && S.exprs.indexOf(v) < 0) S.exprs.push(v); xi.value = ''; renderWatch(); evalExprs(); };
  xi.onkeydown = function (e) { if (e.key === 'Enter') $('xAdd').onclick(); };
  Array.prototype.forEach.call(el.querySelectorAll('[data-x]'), function (b) {
    b.onclick = function () { S.exprs.splice(+b.getAttribute('data-x'), 1); renderWatch(); };
  });
}
var exprTimer = null;
function evalExprs() {
  if (!S.exprs.length) return;
  clearTimeout(exprTimer);
  exprTimer = setTimeout(function () {
    var at = S.cur + 1;
    pyCall('value', { exprs: S.exprs.slice(), step: at }).then(function (r) {
      if (S.cur + 1 !== at) return;
      S.exprVals = {};
      r.values.forEach(function (v) { S.exprVals[v.expr] = v; });
      if (S.tab === 'watch') renderWatch();
    }, function (e) {
      S.exprs.forEach(function (x) { S.exprVals[x] = { error: String(e.message || e) }; });
      if (S.tab === 'watch') renderWatch();
    });
  }, 180);
}

function renderPy() {
  var el = $('pyState'); if (!el) return;
  el.textContent = PY.state === 'off' ? 'The rule language loads the first time you check (about 13 MB, once).'
    : PY.state === 'loading' ? (PY.note || 'loading the rule language…')
    : PY.state === 'failed' ? PY.note : '';
}

function verdictBadge(v, n) {
  if (v === 'holds') return '<span class="q-vb q-ok">✓ holds</span>';
  if (v === 'fails') return '<span class="q-vb q-bad">✗ fails' + (n ? ' ×' + n : '') + '</span>';
  if (v === 'does not apply') return '<span class="q-vb q-na">does not apply</span>';
  return '<span class="q-vb q-na">could not be checked</span>';
}

function renderRules() {
  var el = $('pRules');
  var opts = '<option value="none"' + (S.rules.set === 'none' ? ' selected' : '') + '>none: only my assertions</option>' +
    CFG.rulesets.map(function (r) {
      return '<option value="' + esc(r.name) + '"' + (r.name === S.rules.set ? ' selected' : '') + '>' + esc(r.name) + ' — ' + esc(r.title || '') + ' (' + r.rules.length + ')</option>';
    }).join('');
  var h = '<p class="q-mut">Judge the program by the website\'s rules or your own assertions, in the rule language of the ' +
          '<a href="' + esc(CFG.root) + 'rules/" target="_blank" rel="noopener">Rules page</a>, by the grader\'s own checker. ' +
          'Every failure is crossed out in red where and when it happens.</p>' +
          '<label class="q-lab">Rule set <select id="rSet" data-hint="debug:rules:set">' + opts + '</select></label>' +
          '<details id="rText"><summary>show the rule set\'s text</summary><pre class="q-code">' + esc(rulesetText(S.rules.set)) + '</pre></details>' +
          '<label class="q-lab">Your assertions <select id="rEx" data-hint="debug:rules:example"><option value="">insert an example…</option>' +
          CFG.examples.map(function (x, j) { return '<option value="' + j + '">' + esc(x.t) + '</option>'; }).join('') + '</select></label>' +
          '<textarea id="rAs" rows="5" spellcheck="false" data-hint="debug:rules:assertions" placeholder="for all s in sites, t in instants: occupancy(s, t) <= 1\n\nassert &quot;q0 stays out of C3&quot;:\nfor all t in instants: position(&quot;q0&quot;, t) != &quot;C3&quot;">' + esc(S.rules.assertions) + '</textarea>' +
          '<div class="q-row"><button type="button" id="rCheck" class="q-p" data-hint="debug:rules:check"' + (S.rules.running ? ' disabled' : '') + '>' +
          (S.rules.running ? 'Checking…' : 'Check') + '</button><span id="pyState" class="q-mut"></span></div>';
  if (S.rules.error) h += '<p class="q-bad">' + esc(S.rules.error) + '</p>';
  var r = S.rules.result;
  if (r) {
    var c = { holds: 0, fails: 0, na: 0 };
    r.rules.forEach(function (x) { if (x.verdict === 'holds') c.holds++; else if (x.verdict === 'fails') c.fails++; else c.na++; });
    h += '<h4>' + plural(r.rules.length, 'rule') + ': <span class="q-ok">' + c.holds + ' hold</span> · <span class="q-bad">' + c.fails + ' fail</span> · ' +
         '<span class="q-mut">' + c.na + ' do not apply</span> <span class="q-mut">(' + (S.rules.ms / 1000).toFixed(1) + ' s)</span></h4>';
    if (S.rules.stale) h += '<p class="q-bad">The program changed since this check: press Check again.</p>';
    if (S.B && S.B.P.truncated) h += '<p class="q-mut">This page carries the first ' + S.n + ' of ' + S.B.P.n_instructions + ' instructions (a page shows at most 20,000): the rules judged those.</p>';
    else if (PY.aligned === false) h += '<p class="q-mut">The Studio exported ' + PY.summary.steps + ' instructions for an animation of ' + S.n + ' frames; failures are drawn by position.</p>';
    var order = r.rules.slice().sort(function (a, b) { return (a.verdict === 'fails' ? 0 : 1) - (b.verdict === 'fails' ? 0 : 1); });
    h += '<ul class="q-rl">' + order.map(function (x) {
      var open = !!S.rules.open[x.id];
      var s = '<li class="' + (x.verdict === 'fails' ? 'q-f' : '') + '"><div class="q-rh">' +
        (x.verdict === 'fails' ? '<input type="checkbox" data-hint="debug:rules:show" data-show="' + esc(x.id) + '"' + (S.rules.shown[x.id] ? ' checked' : '') + ' title="cross it out on the picture" aria-label="show ' + esc(x.id) + ' on the picture">' : '<span class="q-cbsp"></span>') +
        '<button type="button" class="q-rbtn" data-hint="debug:rule" data-rule="' + esc(x.id) + '"><b>' + esc(x.id) + '</b> ' + esc(x.title) +
        (x.origin === 'yours' ? ' <span class="q-tag">yours</span>' : '') + '</button>' + verdictBadge(x.verdict, x.count) + '</div>';
      if (open) {
        s += '<pre class="q-code">' + esc((x.when ? 'when ' + x.when + ':\n' : '') + x.text) + '</pre>';
        if (x.reason) s += '<p class="q-mut">' + esc(x.reason) + '</p>';
        if (x.failures) {
          s += '<ul class="q-fails">' + x.failures.slice(0, 60).map(function (f, j) {
            var steps = f.step === null || f.step === undefined ? 'every step' : 'step ' + f.step + (f.until && f.until > f.step ? '–' + f.until : '');
            var where = (f.where || []).filter(function (w) { return w.kind !== 'circuit'; }).map(function (w) { return w.id; }).slice(0, 4).join(', ');
            return '<li><button type="button" class="q-fl" data-hint="debug:failure" data-rule="' + esc(x.id) + '" data-j="' + j + '"><b>' + steps + '</b>' +
              (where ? ' <span class="q-where">' + esc(where) + '</span>' : '') + ' ' + esc(f.message) + '</button></li>';
          }).join('') + (x.failures.length > 60 ? '<li class="q-mut">… ' + (x.failures.length - 60) + ' more</li>' : '') +
          (x.truncated ? '<li class="q-mut">' + x.count + ' counterexamples in all; the first ' + x.failures.length + ' spans are located</li>' : '') + '</ul>';
        }
      }
      return s + '</li>';
    }).join('') + '</ul>';
  }
  el.innerHTML = h;
  renderPy();
  $('rSet').onchange = function () { S.rules.set = this.value; renderRules(); writeUrl(); };
  $('rEx').onchange = function () {
    var x = CFG.examples[+this.value]; if (!x) return;
    var ta = $('rAs'); ta.value = (ta.value.trim() ? ta.value.replace(/\s+$/, '') + '\n\n' : '') + x.text;
    S.rules.assertions = ta.value; this.value = ''; writeUrl();
  };
  $('rAs').oninput = function () { S.rules.assertions = this.value; };
  $('rAs').onchange = function () { writeUrl(); };
  $('rCheck').onclick = function () { runCheck({}).catch(function () { /* said */ }); };
  Array.prototype.forEach.call(el.querySelectorAll('[data-rule].q-rbtn'), function (b) {
    b.onclick = function () { var id = b.getAttribute('data-rule'); S.rules.open[id] = !S.rules.open[id]; renderRules(); };
  });
  Array.prototype.forEach.call(el.querySelectorAll('[data-show]'), function (b) {
    b.onchange = function () { S.rules.shown[b.getAttribute('data-show')] = b.checked; indexFailures(); refresh(); };
  });
  Array.prototype.forEach.call(el.querySelectorAll('.q-fl[data-rule]'), function (b) {
    b.onclick = function () {
      var id = b.getAttribute('data-rule'), j = +b.getAttribute('data-j');
      var hit = S.F && S.F.list.filter(function (e) { return e.rule === id; })[j];
      if (hit) { if (!S.rules.shown[id]) { S.rules.shown[id] = true; indexFailures(); } selectFailure(hit); }
    };
  });
}
function rulesetText(name) {
  var r = CFG.rulesets.filter(function (x) { return x.name === name; })[0];
  return r ? r.text : (name === 'none' ? '(no rule set: only your assertions are checked)' : '');
}

function renderBreaks() {
  var el = $('pBreaks');
  var h = '<p class="q-mut"><b>Continue ▶▶</b> runs from this step to the next breakpoint; <b>◀◀</b> runs back to the previous one.</p>' +
    '<label class="q-chk"><input type="checkbox" id="bFail" data-hint="debug:breaks:onfail"' + (S.bps.onFail ? ' checked' : '') + '> stop where a shown rule starts failing</label>' +
    '<label class="q-chk"><input type="checkbox" id="bWatch" data-hint="debug:breaks:onwatch"' + (S.bps.onWatch ? ' checked' : '') + '> stop where something you watch changes</label>' +
    '<h4>On steps</h4><div class="q-row"><input id="bStepIn" type="number" min="1" placeholder="step" data-hint="debug:breaks:stepinput" aria-label="a step number">' +
    '<button type="button" id="bStepAdd" data-hint="debug:breaks:addstep">Add</button>' +
    '<button type="button" id="bHere" data-hint="debug:breaks:here">Add this step</button></div>';
  if (S.bps.steps.length) h += '<ul class="q-wl">' + S.bps.steps.slice().sort(function (a, b) { return a - b; }).map(function (s) {
    return '<li><button type="button" class="q-ent" data-hint="debug:breaks:go" data-go="' + s + '">step ' + s + '</button><span class="q-grow"></span>' +
      '<button type="button" class="q-mini" data-hint="debug:breaks:remove" data-del="' + s + '" aria-label="remove">×</button></li>';
  }).join('') + '</ul>';
  h += '<h4>On conditions <span class="q-mut">(the rule language over the instant t)</span></h4>' +
    '<div class="q-row"><input id="bCond" placeholder="occupancy(&quot;J0&quot;, t) > 0" data-hint="debug:breaks:condition" aria-label="a condition">' +
    '<button type="button" id="bCondAdd" data-hint="debug:breaks:addcond">Add</button></div>';
  if (S.bps.conds.length) h += '<ul class="q-wl">' + S.bps.conds.map(function (c, j) {
    return '<li><input type="checkbox" data-hint="debug:breaks:condon" data-on="' + j + '"' + (c.on ? ' checked' : '') + ' aria-label="use it"><code>' + esc(c.text) + '</code>' +
      '<span class="q-grow"></span><button type="button" class="q-mini" data-hint="debug:breaks:findprev" data-find="' + j + '" data-back="1" title="find the previous step where it holds">◀</button>' +
      '<button type="button" class="q-mini" data-hint="debug:breaks:findnext" data-find="' + j + '" title="find the next step where it holds">▶</button>' +
      '<button type="button" class="q-mini" data-hint="debug:breaks:removecond" data-dc="' + j + '" aria-label="remove">×</button>' +
      (c.error ? '<div class="q-sub q-bad">' + esc(c.error) + '</div>' : '') + '</li>';
  }).join('') + '</ul>';
  el.innerHTML = h;
  $('bFail').onchange = function () { S.bps.onFail = this.checked; drawTimeline(); };
  $('bWatch').onchange = function () { S.bps.onWatch = this.checked; };
  var addStep = function (s) { s = Math.round(+s); if (s >= 1 && s <= S.n && S.bps.steps.indexOf(s) < 0) S.bps.steps.push(s); renderBreaks(); drawTimeline(); };
  $('bStepAdd').onclick = function () { addStep($('bStepIn').value); };
  $('bHere').onclick = function () { addStep(S.cur + 1); };
  $('bCondAdd').onclick = function () { var v = $('bCond').value.trim(); if (v) S.bps.conds.push({ text: v, on: true }); renderBreaks(); };
  Array.prototype.forEach.call(el.querySelectorAll('[data-go]'), function (b) { b.onclick = function () { goto(+b.getAttribute('data-go') - 1); }; });
  Array.prototype.forEach.call(el.querySelectorAll('[data-del]'), function (b) {
    b.onclick = function () { var s = +b.getAttribute('data-del'); S.bps.steps = S.bps.steps.filter(function (x) { return x !== s; }); renderBreaks(); drawTimeline(); };
  });
  Array.prototype.forEach.call(el.querySelectorAll('[data-on]'), function (b) { b.onchange = function () { S.bps.conds[+b.getAttribute('data-on')].on = b.checked; }; });
  Array.prototype.forEach.call(el.querySelectorAll('[data-dc]'), function (b) { b.onclick = function () { S.bps.conds.splice(+b.getAttribute('data-dc'), 1); renderBreaks(); }; });
  Array.prototype.forEach.call(el.querySelectorAll('[data-find]'), function (b) {
    b.onclick = function () {
      var c = S.bps.conds[+b.getAttribute('data-find')];
      find(c.text, !!b.getAttribute('data-back'), true).then(function (r) { c.error = r.error || ''; renderBreaks(); }, function (e) { c.error = String(e.message || e); renderBreaks(); });
    };
  });
}

function find(condition, back, go) {
  return pyCall('find', { condition: condition, start: S.cur + 1, backward: !!back }).then(function (r) {
    if (r.found && go !== false) { goto(r.step - 1); say(condition + ' holds ' + r.instant); }
    else if (!r.found) say(r.error || r.note || 'not found');
    return r;
  });
}

// ------------------------------------------------------------------ the report

// what the page shows, as link parameters: the step, the watch list, the rules, the tab
function stateParams() {
  var q = new URLSearchParams();
  if (S.B) q.set('step', String(S.cur + 1));
  if (S.watch.length) q.set('watch', S.watch.map(function (w) { return w.id; }).join(','));
  if (S.rules.set && S.rules.set !== 'standard') q.set('rules', S.rules.set);
  if (S.rules.assertions.trim()) q.set('assert', S.rules.assertions);
  if (S.rules.result) q.set('check', '1');
  if (S.tab !== 'state') q.set('tab', S.tab);
  return q;
}
// this page, as a path under the site's root (board/five_qubit/08_torus4x4.html)
function sitePath() {
  try {
    var root = new URL(CFG.root, location.href).pathname;
    return location.pathname.indexOf(root) === 0 ? location.pathname.slice(root.length) : location.pathname.replace(/^\//, '');
  } catch (e) { return ''; }
}
// the link that reopens exactly this: on a Studio page, the page itself with `#debug&...`; on the
// Debug page, `?src=<page>&...`
function link() {
  var q = stateParams(), u = new URL(location.href);
  if (HOST === 'inpage') { u.hash = 'debug' + (String(q) ? '&' + String(q) : ''); return u.href; }
  u.search = ''; u.hash = '';
  if (S.src) u.searchParams.set('src', S.src);
  q.forEach(function (v, k) { u.searchParams.set(k, v); });
  return u.href;
}
// from a Studio page: the same program on the Debug page (its list, files, the frame to itself)
function fullLink() {
  var q = stateParams(), u = new URL(CFG.root + 'debug/', location.href);
  u.searchParams.set('src', sitePath());
  q.forEach(function (v, k) { u.searchParams.set(k, v); });
  return u.href;
}
// `#debug&step=12&watch=q0,C3` (a Studio page's link) as what to show
function hashState() {
  var h = (location.hash || '').replace(/^#/, '');
  if (!/^debug(&|$)/.test(h)) return null;
  var q = new URLSearchParams(h.replace(/^debug&?/, ''));
  return { step: +q.get('step') || null, watch: (q.get('watch') || '').split(',').filter(Boolean),
           rules: q.get('rules'), assertions: q.get('assert'), check: q.get('check') === '1', tab: q.get('tab') };
}
var urlTimer = null;
function writeUrl() {
  if (!history.replaceState) return;
  clearTimeout(urlTimer);
  urlTimer = setTimeout(function () {
    try {
      if (HOST === 'inpage') {
        // the page's own address, with the debugger's state in its hash while the panel is open
        if (S.active) history.replaceState(null, '', link());
        else if (hashState()) history.replaceState(null, '', location.pathname + location.search);
        var full = $('dFull'); if (full) full.href = fullLink();
        return;
      }
      // a file from the reader's computer has no address: the link names no source then
      history.replaceState(null, '', S.kind === 'file' ? location.pathname : link());
    } catch (e) { /* a sandbox */ }
  }, 250);
}
function report() {
  var r = S.rules.result, st = S.B ? stateOf(S.cur) : null;
  var md = ['# Debug report: ' + (S.title || 'no program open'), ''];
  if (S.src) md.push('Program: ' + S.title + ' (`' + S.src + '`), ' + S.n + ' steps.');
  if (r) {
    md.push('Rules: ' + (r.ruleset || 'none') + (S.rules.assertions.trim() ? ' plus my assertions' : '') + '. ' +
            (r.failing.length ? r.failing.length + ' fail: ' + r.failing.join(', ') + '.' : 'Every rule that applies holds.'));
    r.rules.filter(function (x) { return x.verdict === 'fails'; }).forEach(function (x) {
      md.push('', '## ' + x.id + ' ' + x.title + ' (' + x.count + ')', '', '```', x.text, '```');
      (x.failures || []).slice(0, 5).forEach(function (f) {
        md.push('- step ' + (f.step || 'every') + (f.until && f.until > f.step ? '–' + f.until : '') + ' at ' +
                ((f.where || []).map(function (w) { return w.id; }).slice(0, 4).join(', ') || '(no place)') + ': ' + f.message);
      });
    });
    if (S.rules.assertions.trim()) md.push('', '## My assertions', '', '```', S.rules.assertions.trim(), '```');
  } else md.push('Rules: not checked.');
  if (st) {
    md.push('', '## Step ' + st.step + ' / ' + st.of, '', st.type + (st.class ? ' ' + st.class : '') + ': ' + st.instruction);
    if (st.moved.length) md.push('', 'Moved: ' + st.moved.join(', '));
    st.failures.forEach(function (f) { md.push('- ' + f.rule + ': ' + f.message); });
    if (S.watch.length) { md.push('', 'Watched:'); S.watch.forEach(function (w) { md.push('- ' + w.id + ': ' + lookText(st.watch[w.id])); }); }
  }
  md.push('', 'Reopen exactly here: ' + link());
  return { markdown: md.join('\n'), link: link(), state: st,
           rules: r ? { ruleset: r.ruleset || null, failing: r.failing, rules: r.rules.map(function (x) {
             return { id: x.id, verdict: x.verdict, count: x.count || 0, first: (x.failures || [])[0] || null }; }) } : null };
}
function renderReport() {
  var el = $('pReport'), rep = report();
  el.innerHTML = '<p class="q-mut">What the page shows, as a bug report: paste it into a comment, an issue or a message. The link reopens this page on this program, at this step, with these rules and this watch list.</p>' +
    '<div class="q-row"><button type="button" id="repCopy" class="q-p" data-hint="debug:report:copy">Copy as Markdown</button>' +
    '<button type="button" id="repLink" data-hint="debug:report:link">Copy the link</button>' +
    '<button type="button" id="repJson" data-hint="debug:report:json">Download as data</button></div>' +
    '<pre class="q-code q-rep">' + esc(rep.markdown) + '</pre>';
  $('repCopy').onclick = function () { copy(rep.markdown, 'report copied'); };
  $('repLink').onclick = function () { copy(rep.link, 'link copied'); };
  $('repJson').onclick = function () {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify({ report: rep, result: S.rules.result }, null, 1)], { type: 'application/json' }));
    a.download = 'debug-report.json'; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
  };
}
function copy(text, ok) {
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { say(ok); }, function () { say('select the text and copy it'); });
  else say('select the text and copy it');
}

// ------------------------------------------------------------------ the timeline

function drawTimeline() {
  var svg = $('dTimeSvg'); if (!svg) return;
  var W = Math.max(200, svg.clientWidth || svg.parentNode.clientWidth || 800), H = 44, n = Math.max(1, S.n);
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  var x = function (i) { return 6 + (W - 12) * (n === 1 ? 0.5 : i / (n - 1)); };
  var h = '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="transparent"/>' +
          '<line x1="6" y1="22" x2="' + (W - 6) + '" y2="22" stroke="#cfceca" stroke-width="2"/>';
  if (S.F) {
    var bins = {};
    S.F.list.forEach(function (e) {
      if (!S.rules.shown[e.rule] || e.always) return;
      var a = Math.round(x(e.f0)), b = Math.round(x(e.f1));
      h += '<rect x="' + a + '" y="16" width="' + Math.max(2, b - a) + '" height="12" fill="' + RED + '" fill-opacity="0.16"/>';
      bins[a] = 1;
    });
    for (var px in bins) h += '<line x1="' + px + '" y1="12" x2="' + px + '" y2="32" stroke="' + RED + '" stroke-width="2"/>';
  }
  S.watch.forEach(function (w, row) {
    var ev = eventsOf(w.id), last = -9, y = 37 + (row % 2) * 4;
    for (var i = 0; i < S.n; i++) if (ev[i]) { var px2 = Math.round(x(i)); if (px2 - last >= 2) { h += '<rect x="' + (px2 - 1) + '" y="' + y + '" width="2" height="3" fill="' + w.color + '"/>'; last = px2; } }
  });
  S.bps.steps.forEach(function (s) { var p = x(s - 1); h += '<path d="M' + (p - 4) + ' 2 L' + (p + 4) + ' 2 L' + p + ' 9 Z" fill="#2a78d6"/>'; });
  if (S.B) h += '<line x1="' + x(S.cur) + '" y1="4" x2="' + x(S.cur) + '" y2="40" stroke="#111827" stroke-width="2"/>';
  svg.innerHTML = h;
  svg.onclick = function (e) {
    var r = svg.getBoundingClientRect(), f = (e.clientX - r.left) / Math.max(1, r.width);
    goto(Math.round(f * (n - 1)));
  };
}

// ------------------------------------------------------------------ opening a program

var sayTimer = null;
function say(text) {
  var m = $('dMsg'); if (!m) return;
  m.textContent = text || ''; m.style.display = text ? '' : 'none';
  clearTimeout(sayTimer);
  if (text) sayTimer = setTimeout(function () { m.style.display = 'none'; }, 7000);
}
function autosaved() {
  try {
    var raw = localStorage.getItem(STORE); if (!raw) return null;
    var snap = JSON.parse(raw);
    var a = snap.arch || {}, geo = a.geometry || {};
    var calls = ((snap.program || {}).calls || []).length;
    return { raw: raw, name: a.name || (snap.seed && snap.seed.args && snap.seed.args[0]) || 'your design', calls: calls,
             nodes: (geo.nodes || []).length || null };
  } catch (e) { return null; }
}
function renderStart() {
  var el = $('dStart'), mine = autosaved();
  var groups = {};
  CFG.catalogue.forEach(function (r) { (groups[r.g] = groups[r.g] || []).push(r); });
  el.innerHTML = '<div class="q-start"><h2>Debug a program, step by step</h2>' +
    '<p class="q-lead">Open a program, step through it forward and back, watch ions, sites, junctions and rails, and check it ' +
    'against the website\'s rules or your own assertions. Every failure is crossed out in red on the picture, where and when it happens.</p>' +
    '<div class="q-cards">' +
    (mine ? '<div class="q-card"><h3>Your design</h3><p>The design you last left on the Design page: <b>' + esc(mine.name) + '</b>' +
      (mine.calls ? ', with a program of ' + plural(mine.calls, 'statement') : ', with no program written yet') + '.</p>' +
      '<button type="button" class="q-p" id="sMine" data-hint="debug:start:design">Debug my design</button></div>' : '') +
    '<div class="q-card"><h3>Design one here</h3><p>Open the Design page in this frame: build a device and write its program, then check it here. Your work stays in the frame.</p>' +
    '<button type="button" id="sStudio" data-hint="debug:start:studio">Open the Design page</button></div>' +
    '<div class="q-card"><h3>A file from your computer</h3><p>A Studio page saved from this site (.html), a saved design (.studio.json, .arch.json), ' +
    'or a device and its program together: choose the .arch.json and the .tsir.json at once.</p>' +
    '<input type="file" id="sFile" multiple accept=".html,.htm,.json" data-hint="debug:start:file" aria-label="open a file"></div>' +
    '<div class="q-card q-wide"><h3>A program from the website</h3><input id="sQ" type="search" placeholder="search ' + CFG.catalogue.length +
    ' programs: a board, a design, a paper…" data-hint="debug:start:search" aria-label="search the programs"><div id="sList" class="q-list"></div></div>' +
    '</div></div>';
  var list = function () {
    var q = ($('sQ').value || '').toLowerCase().split(/\s+/).filter(Boolean), h = '', shown = 0;
    Object.keys(groups).forEach(function (g) {
      var rows = groups[g].filter(function (r) { var s = (r.t + ' ' + r.u).toLowerCase(); return q.every(function (w) { return s.indexOf(w) >= 0; }); });
      if (!rows.length) return;
      h += '<h4>' + esc(g) + ' <span class="q-mut">' + rows.length + '</span></h4>' + rows.slice(0, q.length ? 200 : 12).map(function (r) {
        shown++;
        return '<button type="button" class="q-src" data-hint="debug:start:program" data-src="' + esc(r.u) + '">' + esc(r.t) + '<small>' + esc(r.u) + '</small></button>';
      }).join('') + (rows.length > (q.length ? 200 : 12) ? '<p class="q-mut">… type to narrow ' + rows.length + ' programs</p>' : '');
    });
    $('sList').innerHTML = h || '<p class="q-mut">No program matches.</p>';
    Array.prototype.forEach.call($('sList').querySelectorAll('[data-src]'), function (b) {
      b.onclick = function () { open(b.getAttribute('data-src')); };
    });
  };
  $('sQ').oninput = list; list();
  if ($('sMine')) $('sMine').onclick = function () { open('design'); };
  $('sStudio').onclick = function () { open('studio'); };
  $('sFile').onchange = function () { openFiles(this.files); };
}
function showStart(on) {
  $('dStart').style.display = on ? '' : 'none';
  $('dFrame').style.visibility = on ? 'hidden' : 'visible';
  if (on) renderStart();
}

var opening = null;
function open(src, opts) {
  opts = opts || {};
  src = String(src || '').replace(/^\/+/, '');
  var frame = $('dFrame');
  var page = src === 'design' || src === 'studio' ? 'studio.html' : src;
  var hit = CFG.catalogue.filter(function (r) { return r.u === page; })[0];
  S.src = src; S.kind = src === 'design' ? 'design' : src === 'studio' ? 'studio' : 'page'; S.fold = S.kind === 'page';
  S.title = src === 'design' ? 'your design' : src === 'studio' ? 'the Design page' : (hit ? hit.t : src);
  return load(function () { frame.removeAttribute('srcdoc'); frame.src = CFG.root + page + (src === 'studio' || src === 'design' ? '#design' : ''); }, function (win) {
    if (src === 'design') {
      var mine = autosaved();
      if (!mine) throw new Error('the Design page has saved no design in this browser yet');
      var r = win.EDITOR.importText(mine.raw);
      if (!r || r.ok === false) throw new Error('your saved design could not be opened: ' + (((r || {}).problems || [])[0] || {}).message);
      S.title = 'your design: ' + mine.name;
    }
  }, opts);
}
function openHTML(html, title, after) {
  var frame = $('dFrame');
  S.src = null; S.kind = 'file'; S.title = title; S.fold = true;
  return load(function () { frame.removeAttribute('src'); frame.srcdoc = html; }, after || null, {});
}
function openFiles(files) {
  files = Array.prototype.slice.call(files || []);
  if (!files.length) return Promise.resolve();
  return Promise.all(files.map(function (f) {
    return new Promise(function (res, rej) { var fr = new FileReader(); fr.onload = function () { res({ name: f.name, text: String(fr.result) }); }; fr.onerror = function () { rej(new Error('could not read ' + f.name)); }; fr.readAsText(f); });
  })).then(function (got) {
    var html = got.filter(function (g) { return /\.html?$/i.test(g.name); })[0];
    if (html) return openHTML(html.text, html.name);
    var docs = got.map(function (g) { try { return { name: g.name, text: g.text, doc: JSON.parse(g.text) }; } catch (e) { return { name: g.name, bad: e.message }; } });
    var bad = docs.filter(function (d) { return d.bad; })[0];
    if (bad) throw new Error(bad.name + ' is not JSON: ' + bad.bad);
    var tsir = docs.filter(function (d) { return d.doc && Array.isArray(d.doc.instructions); })[0];
    var arch = docs.filter(function (d) { return d.doc && d.doc.geometry && d.doc.schema_version; })[0];
    if (tsir && !arch) throw new Error(tsir.name + ' is a program: choose its .arch.json together with it (both files at once)');
    if (tsir && arch) {
      say('drawing ' + tsir.name + ' on ' + arch.name + ' (the rule language draws it, as the site would)…');
      return pyJSON('render_page', [arch.text, tsir.text, 'null']).then(function (r) { return openHTML(r.html, tsir.name + ' on ' + arch.name); });
    }
    // a saved design (.studio.json), a device (.arch.json) or an edit list: the Design page opens it
    var saved = docs[0], frame = $('dFrame');
    S.src = null; S.kind = 'file'; S.title = saved.name; S.fold = false;
    return load(function () { frame.removeAttribute('srcdoc'); frame.src = CFG.root + 'studio.html#design'; }, function (win) {
      var r = win.EDITOR.importText(saved.text);
      if (!r || r.ok === false) throw new Error(saved.name + ' could not be opened: ' + ((((r || {}).problems || [])[0] || {}).message || 'the Studio refused it'));
    }, {});
  }).catch(function (e) { say(String(e.message || e)); showStart(!S.B); });
}
// load: set the frame going, wait for its Studio, attach, then apply what the link asked for
function load(start, after, opts) {
  var frame = $('dFrame');
  S.B = null; S.win = null; S.frames = null; S.n = 0; S.cur = 0; S.F = null; S.events = {};
  S.rules.result = null; S.rules.stale = false; PY.key = null; PY.digest = null;
  showStart(false);
  say('opening ' + S.title + '…');
  $('dSrc').innerHTML = esc(S.title) + (S.src && S.kind === 'page' ? ' <a href="' + esc(CFG.root + S.src) + '" target="_blank" rel="noopener" data-hint="debug:source:page">open the page</a>' : '');
  opening = new Promise(function (resolve, reject) {
    var t0 = Date.now(), done = false;
    frame.onload = function () {
      var win = frame.contentWindow;
      (function poll() {
        if (done) return;
        var ready = false;
        try { ready = !!(win.EDITOR && win.EDITOR.ready && win.EDITOR.ready()) || (win.EDITOR && win.EDITOR.why && win.EDITOR.why()); } catch (e) { ready = false; }
        if (!ready && Date.now() - t0 < 30000) { setTimeout(poll, 120); return; }
        done = true;
        try {
          if (!win.EDITOR) throw new Error('this page has no Studio animation to debug');
          if (after) after(win);
          attach(win);
          var p = S.pending; S.pending = null;
          if (p && p.watch) addWatch(p.watch);
          goto(p && p.step ? Math.max(0, Math.min(S.n - 1, p.step - 1)) : 0);
          say(S.n ? 'Step 1 to ' + S.n + ': ◀ ▶ or the arrow keys step; Rules checks the program' : 'this program has no steps yet: write one in the Studio\'s Write pane');
          if (p && p.check) runCheck({}).catch(function () {});
          resolve({ title: S.title, src: S.src, steps: S.n });
        } catch (e) { say(String(e.message || e)); reject(e); }
      })();
    };
    try { start(); } catch (e) { reject(e); }
  });
  return opening;
}

// ------------------------------------------------------------------ the agent's API (debug_api.json)

function arg(a) { return a && typeof a === 'object' ? a : {}; }
function need() { if (!S.B) throw new Error('no program is open: call open (sources lists them)'); }
var API = {
  about: function () {
    return { page: 'the Debug page: any program of the website step by step, checked by rules',
             numbering: 'steps count from 1, as the Studio\'s status line does (Step k / N)',
             verbs: CFG.verbs, rules_language: CFG.root + 'rules/', rulesets: CFG.rulesets.map(function (r) { return r.name; }) };
  },
  sources: function (a) {
    a = arg(a);
    var q = String(a.q || '').toLowerCase().split(/\s+/).filter(Boolean);
    var rows = CFG.catalogue.filter(function (r) { var s = (r.t + ' ' + r.u).toLowerCase(); return q.every(function (w) { return s.indexOf(w) >= 0; }); });
    var mine = autosaved();
    return { total: rows.length, programs: rows.slice(0, +a.limit || 40).map(function (r) { return { title: r.t, group: r.g, src: r.u }; }),
             design: mine ? { src: 'design', name: mine.name, statements: mine.calls } : null };
  },
  open: function (a) { return open(String(arg(a).src || '')); },
  status: function () {
    var r = S.rules.result;
    return { host: HOST === 'inpage' ? 'this Studio page' : 'the Debug page', panel_open: S.active,
             open: S.B ? { title: S.title, src: S.src, steps: S.n } : null, step: S.B ? S.cur + 1 : null,
             watch: S.watch.map(function (w) { return { id: w.id, kind: w.kind }; }), isolate: S.isolate,
             rules: { set: S.rules.set, assertions: S.rules.assertions, checked: !!r, stale: S.rules.stale, running: S.rules.running,
                      failing: r ? r.failing : null, error: S.rules.error || null },
             python: PY.state, breakpoints: { steps: S.bps.steps, conditions: S.bps.conds.map(function (c) { return c.text; }), on_failure: S.bps.onFail, on_watch: S.bps.onWatch } };
  },
  goto: function (a) { need(); var s = +arg(a).step; if (!isFinite(s)) throw new Error('goto needs a step'); var i = goto(s < 0 ? S.n + s : s - 1); return stateOf(i); },
  step: function (a) { need(); var d = arg(a).delta; return stateOf(stepBy(d === undefined ? 1 : +d)); },
  state: function (a) { need(); var s = arg(a).step; var i = s === undefined ? S.cur : Math.max(0, Math.min(S.n - 1, +s - 1)); return stateOf(i); },
  watch: function (a) {
    need(); a = arg(a);
    var res = {};
    if (a.clear) S.watch = [];
    if (a.remove) removeWatch([].concat(a.remove).map(String));
    if (a.add) res = addWatch([].concat(a.add).map(String));
    if (a.isolate !== undefined) { S.isolate = !!a.isolate; syncToggles(); }
    if (a.trails !== undefined) { S.trails = !!a.trails; syncToggles(); }
    refresh();
    return Object.assign({ watch: S.watch.map(function (w) { return { id: w.id, kind: w.kind, color: w.color, now: lookAt(w, S.cur) }; }),
                           isolate: S.isolate, trails: S.trails }, res);
  },
  trace: function (a) { need(); a = arg(a); return trace(String(a.entity || ''), +a.from || 1, +a.to || S.n, +a.limit || 200); },
  check: function (a) {
    need(); a = arg(a);
    var job = runCheck({ rules: a.rules, assertions: a.assertions, only: a.only }).then(function (r) {
      return { all_hold: r.all_hold, ruleset: r.ruleset || null, failing: r.failing, seconds: S.rules.ms / 1000,
               rules: r.rules.map(function (x) { return { id: x.id, title: x.title, verdict: x.verdict, count: x.count || 0, reason: x.reason,
                 origin: x.origin, first: (x.failures || []).slice(0, 3) }; }) };
    });
    var slow = new Promise(function (res) { setTimeout(function () { res({ running: true, note: 'still checking (a long program takes a minute in the browser); call status, then failures' }); }, 75000); });
    return Promise.race([job, slow]);
  },
  failures: function (a) {
    a = arg(a);
    if (!S.F) return { checked: false, note: 'call check first' };
    var out = S.F.list.filter(function (e) {
      return (!a.rule || e.rule === a.rule) && (!a.from || e.f1 + 1 >= +a.from) && (!a.to || e.f0 + 1 <= +a.to);
    });
    return { total: out.length, failures: out.slice(0, +a.limit || 50).map(function (e) {
      return { rule: e.rule, from: e.always ? null : e.f0 + 1, until: e.always ? null : e.f1 + 1, phase: e.phase, where: e.where, message: e.message };
    }) };
  },
  next_failure: function () { need(); var j = nextFailure(false); return { step: j === null ? null : j + 1, here: stateOf(S.cur).failures }; },
  prev_failure: function () { need(); var j = nextFailure(true); return { step: j === null ? null : j + 1, here: stateOf(S.cur).failures }; },
  find: function (a) { need(); a = arg(a); return find(String(a.condition || ''), !!a.backward, a.go !== false); },
  value: function (a) {
    need(); a = arg(a);
    return pyCall('value', { exprs: [].concat(a.exprs || []).map(String), step: a.step === undefined ? S.cur + 1 : +a.step });
  },
  breakpoints: function (a) {
    a = arg(a);
    if (a.clear) { S.bps.steps = []; S.bps.conds = []; }
    [].concat(a.add_steps || []).forEach(function (s) { s = Math.round(+s); if (s >= 1 && S.bps.steps.indexOf(s) < 0) S.bps.steps.push(s); });
    var rm = [].concat(a.remove_steps || []).map(Number);
    S.bps.steps = S.bps.steps.filter(function (s) { return rm.indexOf(s) < 0; });
    [].concat(a.add_conditions || []).forEach(function (c) { if (!S.bps.conds.some(function (x) { return x.text === c; })) S.bps.conds.push({ text: String(c), on: true }); });
    var rc = [].concat(a.remove_conditions || []).map(String);
    S.bps.conds = S.bps.conds.filter(function (c) { return rc.indexOf(c.text) < 0; });
    if (a.on_failure !== undefined) S.bps.onFail = !!a.on_failure;
    if (a.on_watch !== undefined) S.bps.onWatch = !!a.on_watch;
    if (S.tab === 'breaks') renderBreaks();
    drawTimeline();
    return API.status().breakpoints;
  },
  continue: function (a) { need(); return continueRun(!!arg(a).backward); },
  report: function () { return report(); },
  link: function () { return { link: link() }; }
};
if (HOST === 'inpage') {
  API.open = function (a) {
    var src = String(arg(a).src || '');
    if (!src || src === 'design' || src === 'studio') src = 'studio.html';
    var to = new URL(CFG.root + src.replace(/^\/+/, ''), location.href);
    to.hash = 'debug';
    setTimeout(function () { location.assign(to.href); }, 50);
    return { navigating: to.pathname + to.hash, note: 'the debugger opens beside that page\'s program' };
  };
  Object.keys(API).forEach(function (k) {
    if (k === 'about' || k === 'sources' || k === 'open' || k === 'link') return;
    var f = API[k];
    API[k] = function (a) { return S.active ? f(a) : enter().then(function () { return f(a); }); };
  });
}
window.QCCD_DEBUG = API;

// what every control does, in the words an agent and the hover card read (docs/agent-interface.md)
window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, {
  'debug:open': { t: 'Open', d: 'choose the program to debug: one from the website, your design, the Design page, or a file' },
  'debug:source': { t: 'Program', d: 'the program open in the page' },
  'debug:source:page': { t: 'Open the page', d: 'the program\'s own page, in a new tab' },
  'debug:fullpage': { t: 'Open in the Debug page', d: 'this program on the Debug page, in a new tab: its list of every program, files from your computer' },
  'debug:close': { t: 'Close the debugger', d: 'close the panel; the program stays where it is' },
  'debug:first': { t: 'First step', d: 'go to step 1' },
  'debug:back': { t: 'Back', d: 'go back one step' },
  'debug:step': { t: 'Step', d: 'the step shown; type a number to go there' },
  'debug:forward': { t: 'Forward', d: 'go forward one step' },
  'debug:last': { t: 'Last step', d: 'go to the last step' },
  'debug:prevfail': { t: 'Previous failure', d: 'go to the previous step where a shown rule starts failing' },
  'debug:nextfail': { t: 'Next failure', d: 'go to the next step where a shown rule starts failing' },
  'debug:prevevent': { t: 'Previous change', d: 'go to the previous step where something you watch changes' },
  'debug:nextevent': { t: 'Next change', d: 'go to the next step where something you watch changes' },
  'debug:continueback': { t: 'Continue backward', d: 'run back to the previous breakpoint' },
  'debug:continue': { t: 'Continue', d: 'run forward to the next breakpoint (steps, conditions, failures, watched changes)' },
  'debug:isolate': { t: 'Isolate', d: 'fade every ion you do not watch, to follow a few' },
  'debug:trails': { t: 'Trails', d: 'draw where each watched ion has been over the last steps' },
  'debug:crosses': { t: 'Crosses', d: 'show or hide the red crosses where rules fail' },
  'debug:split': { t: 'Divider', d: 'drag to make the panel wider or narrower' },
  'debug:timeline': { t: 'Timeline', d: 'every step: red where rules fail, coloured ticks where watched things change, blue marks for breakpoints; click to go there' },
  'debug:tab:state': { t: 'State', d: 'the state after this step: the instruction, what moved, what fails here, what you watch' },
  'debug:tab:watch': { t: 'Watch', d: 'ions, sites, junctions and rails to highlight and follow, and watch expressions' },
  'debug:tab:rules': { t: 'Rules', d: 'check the program against a rule set and your own assertions' },
  'debug:tab:breaks': { t: 'Breakpoints', d: 'where Continue stops: steps, conditions, failures, watched changes' },
  'debug:tab:report': { t: 'Report', d: 'what the page shows as a bug report, with a link back to exactly here' },
  'debug:entity': { t: 'Watch this', d: 'add this ion, site, junction or rail to the watch list' },
  'debug:failure': { t: 'A failure', d: 'go to the step where it fails and point at the place' },
  'debug:rule': { t: 'A rule', d: 'show the rule\'s text and every place it fails' },
  'debug:watch:input': { t: 'Ids to watch', d: 'ion, site, junction or rail ids, separated by commas' },
  'debug:watch:add': { t: 'Watch', d: 'watch the ids typed beside it' },
  'debug:watch:selection': { t: 'Watch the selection', d: 'watch what is selected in the Studio' },
  'debug:watch:pick': { t: 'Pick on the picture', d: 'while on, a click on the picture watches the ion, site, junction or rail under it' },
  'debug:watch:prev': { t: 'Previous change', d: 'the previous step where this changes' },
  'debug:watch:next': { t: 'Next change', d: 'the next step where this changes' },
  'debug:watch:trail': { t: 'Trail', d: 'draw where this ion has been' },
  'debug:watch:remove': { t: 'Stop watching', d: 'remove it from the watch list' },
  'debug:watch:clear': { t: 'Stop watching all', d: 'empty the watch list' },
  'debug:expr:input': { t: 'Expression', d: 'a term of the rule language, with t the instant after this step: occupancy("C0", t), position("q3", t)' },
  'debug:expr:add': { t: 'Add expression', d: 'evaluate it at every step you visit' },
  'debug:expr:remove': { t: 'Remove expression', d: 'stop evaluating it' },
  'debug:rules:set': { t: 'Rule set', d: 'the website\'s rule set to check: standard (this site\'s machine), one per paper, or none' },
  'debug:rules:example': { t: 'Example assertion', d: 'insert an example assertion to start from' },
  'debug:rules:assertions': { t: 'Your assertions', d: 'your own rules in the rule language: a formula per paragraph, optionally led by assert "title":' },
  'debug:rules:check': { t: 'Check', d: 'judge the program by the rule set and your assertions, and cross out every failure' },
  'debug:rules:show': { t: 'Show on the picture', d: 'cross this rule\'s failures out on the picture' },
  'debug:breaks:onfail': { t: 'Stop at failures', d: 'Continue stops where a shown rule starts failing' },
  'debug:breaks:onwatch': { t: 'Stop at changes', d: 'Continue stops where something you watch changes' },
  'debug:breaks:stepinput': { t: 'Step', d: 'a step number for a breakpoint' },
  'debug:breaks:addstep': { t: 'Add step breakpoint', d: 'stop at the step typed beside it' },
  'debug:breaks:here': { t: 'Add this step', d: 'stop at the step shown' },
  'debug:breaks:go': { t: 'Go to breakpoint', d: 'show that step' },
  'debug:breaks:remove': { t: 'Remove breakpoint', d: 'remove that step breakpoint' },
  'debug:breaks:condition': { t: 'Condition', d: 'a formula of the rule language over the instant t, e.g. occupancy("J0", t) > 0' },
  'debug:breaks:addcond': { t: 'Add condition', d: 'stop where the condition holds' },
  'debug:breaks:condon': { t: 'Use condition', d: 'whether Continue stops on this condition' },
  'debug:breaks:findprev': { t: 'Find previous', d: 'the previous step where the condition holds' },
  'debug:breaks:findnext': { t: 'Find next', d: 'the next step where the condition holds' },
  'debug:breaks:removecond': { t: 'Remove condition', d: 'remove the condition' },
  'debug:report:copy': { t: 'Copy as Markdown', d: 'copy the report' },
  'debug:report:link': { t: 'Copy the link', d: 'copy the link that reopens the page exactly here' },
  'debug:report:json': { t: 'Download as data', d: 'the report and every verdict as a JSON file' },
  'debug:start:design': { t: 'Debug my design', d: 'open the design you last left on the Design page' },
  'debug:start:studio': { t: 'Open the Design page', d: 'design a device and write its program in the frame, then check it' },
  'debug:start:file': { t: 'Open a file', d: 'a Studio page, a saved design, or a device and its program together (the person\'s to choose)' },
  'debug:start:search': { t: 'Search programs', d: 'find a program of the website by its title or path' },
  'debug:start:program': { t: 'A program', d: 'open this program in the page' }
});

// ------------------------------------------------------------------ wiring

function syncToggles() {
  [['dIsolate', S.isolate], ['dTrails', S.trails], ['dCrosses', S.crosses]].forEach(function (p) {
    var b = $(p[0]); b.className = 'q-tgl' + (p[1] ? ' q-on' : ''); b.setAttribute('aria-pressed', String(!!p[1]));
  });
}
// the toolbar, the tabs and the timeline: the same on both hosts
function wireTools() {
  $('dFirst').onclick = function () { goto(0); };
  $('dBack').onclick = function () { stepBy(-1); };
  $('dFwd').onclick = function () { stepBy(1); };
  $('dLast').onclick = function () { goto(S.n - 1); };
  $('dStep').onchange = function () { goto((+this.value || 1) - 1); };
  $('dPrevFail').onclick = function () { nextFailure(true); };
  $('dNextFail').onclick = function () { nextFailure(false); };
  $('dPrevEv').onclick = function () { nextEvent(true); };
  $('dNextEv').onclick = function () { nextEvent(false); };
  $('dCont').onclick = function () { continueRun(false); };
  $('dContBack').onclick = function () { continueRun(true); };
  $('dIsolate').onclick = function () { S.isolate = !S.isolate; syncToggles(); paint(); };
  $('dTrails').onclick = function () { S.trails = !S.trails; syncToggles(); paint(); };
  $('dCrosses').onclick = function () { S.crosses = !S.crosses; syncToggles(); paint(); };
  Array.prototype.forEach.call(document.querySelectorAll('#dTabs [role=tab]'), function (b) {
    b.onclick = function () { showTab(b.getAttribute('data-tab')); };
  });
}
// the debugger's keys.  On the Debug page every key below; in a Studio page the plain arrows,
// Home and End stay the Studio's own (it steps, and the panel follows), and the debugger takes
// only the keys the Studio does not have -- caught before the Studio, which would otherwise read
// Shift+Right as one more step too.
function onKey(e) {
  var t = e.target, tag = t && t.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable)) return;
  if (!S.B || !S.active) return;
  var k = e.key, mod = e.shiftKey || e.altKey, done = true;
  if (k === 'ArrowRight' && e.shiftKey) nextFailure(false);
  else if (k === 'ArrowLeft' && e.shiftKey) nextFailure(true);
  else if (k === 'ArrowRight' && e.altKey) nextEvent(false);
  else if (k === 'ArrowLeft' && e.altKey) nextEvent(true);
  else if (k === 'F8') continueRun(e.shiftKey);
  else if (HOST === 'inpage') done = false;
  else if (k === 'ArrowRight') stepBy(1);
  else if (k === 'ArrowLeft') stepBy(-1);
  else if (k === 'Home') goto(0);
  else if (k === 'End') goto(S.n - 1);
  else done = false;
  if (done) { e.preventDefault(); if (HOST === 'inpage' && (mod || k === 'F8')) e.stopImmediatePropagation(); }
}
function wirePage() {
  $('dOpen').onclick = function () { showStart(true); };
  document.addEventListener('keydown', onKey);
  // the divider: the panel's width, remembered
  var split = $('dSplit'), side = $('dbgSide');
  try { var w0 = +localStorage.getItem('qccd.debug.side'); if (w0 > 240) side.style.width = w0 + 'px'; } catch (e) { /* default */ }
  split.onpointerdown = function (e) {
    split.setPointerCapture(e.pointerId);
    var x0 = e.clientX, wd = side.getBoundingClientRect().width;
    document.body.classList.add('dragging');
    split.onpointermove = function (m) { side.style.width = Math.max(260, Math.min(window.innerWidth * 0.7, wd - (m.clientX - x0))) + 'px'; drawTimeline(); };
    split.onpointerup = function () {
      split.onpointermove = null; document.body.classList.remove('dragging');
      try { localStorage.setItem('qccd.debug.side', String(Math.round(side.getBoundingClientRect().width))); } catch (e2) { /* fine */ }
    };
  };
  window.addEventListener('resize', function () { drawTimeline(); });
}

// ------------------------------------------------------------------ in a Studio page

// the Studio has its program once it has booted (or said why it cannot)
function studioReady() {
  return new Promise(function (resolve, reject) {
    var t0 = Date.now();
    (function poll() {
      var E = window.EDITOR, ok = false;
      try { ok = !!(E && ((E.ready && E.ready()) || (E.why && E.why()))); } catch (e) { ok = false; }
      if (ok) { resolve(); return; }
      if (Date.now() - t0 > 20000) { reject(new Error('the Studio on this page did not finish loading')); return; }
      setTimeout(poll, 100);
    })();
  });
}
// the panel open or shut: the Studio gives up the panel's width and lays itself out again
function setLayout(on) {
  // the element rail folds while the panel is open (the picture needs the width more than the
  // parts do; its grip opens it again) and comes back as it was
  var rail = $('rail');
  if (rail && typeof window.foldPanel === 'function') {
    try {
      if (on) { S.railWas = rail.getAttribute('data-collapsed') === '1'; if (!S.railWas) window.foldPanel(rail, true); }
      else if (S.railWas === false) window.foldPanel(rail, false);
    } catch (e) { /* the rail stays as it is */ }
  }
  if (on) document.body.setAttribute('data-qdbg', '1'); else document.body.removeAttribute('data-qdbg');
  try { if (typeof window.relayout === 'function') window.relayout(); } catch (e) { /* lays out itself */ }
  var btn = $('qdbgBtn');
  if (btn) { btn.className = 'tgl' + (on ? ' on' : ''); btn.setAttribute('aria-pressed', String(!!on)); }
}
function enter(state) {
  if (S.active && $('dbg')) return Promise.resolve(API.status());
  return studioReady().then(function () {
    if (!$('dbg')) {
      var a = document.createElement('aside');
      a.id = 'dbg'; a.className = 'inpage'; a.setAttribute('aria-label', 'Debug');
      a.innerHTML = INPAGE_HTML;
      document.body.appendChild(a);
      wireTools();
      $('dClose').onclick = function () { exit(); };
    }
    S.active = true;
    setLayout(true);
    attach(window);
    S.src = sitePath(); S.kind = 'page'; S.title = document.title;
    $('dSrc').textContent = S.title;
    var st = state || hashState() || {};
    if (st.rules) S.rules.set = st.rules;
    if (st.assertions) S.rules.assertions = st.assertions;
    syncToggles();
    showTab(st.tab || S.tab);
    if (st.watch && st.watch.length) addWatch(st.watch);
    // where the reader already is: the Studio's own step, unless a link names another
    goto(st.step ? st.step - 1 : (typeof S.B.frame === 'number' ? S.B.frame : 0));
    say(S.n ? 'debugging this program: \u25C0 \u25B6 step it, Rules checks it, \u00d7 closes the panel'
            : 'this program has no steps yet: write one in the Write pane');
    if (st.check) runCheck({}).catch(function () { /* said */ });
    return API.status();
  });
}
function exit() {
  if (!S.active) return { open: false };
  S.active = false; S.picking = false;
  var p = $('dbg'); if (p) p.remove();
  setLayout(false);
  paint();
  writeUrl();
  return { open: false };
}

function boot() {
  if (HOST === 'inpage') {
    window.QCCD_DEBUG_INPAGE = { enter: enter, exit: exit,
                                 toggle: function () { return S.active ? Promise.resolve(exit()) : enter(); },
                                 get active() { return S.active; } };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', function () { if (S.active) drawTimeline(); });
    return;
  }
  $('dTools').innerHTML = TOOLS_HTML;
  $('dbgSide').innerHTML = SIDE_HTML;
  $('dTime').innerHTML = TIME_HTML;
  wireTools();
  wirePage();
  syncToggles();
  var q = new URLSearchParams(location.search);
  if (q.get('rules')) S.rules.set = q.get('rules');
  if (q.get('assert')) S.rules.assertions = q.get('assert');
  if (q.get('tab')) S.tab = q.get('tab');
  showTab(S.tab);
  drawTimeline();
  var src = q.get('src');
  if (src) {
    S.pending = { step: +q.get('step') || null, watch: (q.get('watch') || '').split(',').filter(Boolean), check: q.get('check') === '1' };
    open(src).catch(function () { showStart(true); });
  } else showStart(true);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
