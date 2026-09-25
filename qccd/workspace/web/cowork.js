// qccd/workspace/web/cowork.js -- the LIVE layer of a Studio page served by a workspace.
//
// Injected by `qccd/workspace/service.py` AFTER `render_html` wrote the stock page, so the
// studio's own sources (render.py, editor.js, engine.js) are untouched and their
// self-containment scan still holds for every static page.  Everything here talks to
// the one workspace service on this page's own origin.
//
// What it does, and the rule it keeps for each:
//  * SYNC.  The page's record lists (EDITOR.geom/seed/post/edits/program) are diffed against
//    the last revision the service confirmed; a difference becomes ONE change set
//    (`studio_sync`) with `expected_revision`.  The service validates it with the Python
//    toolchain -- the same applier an agent's edit goes through -- and either commits it or
//    refuses it (protected entity, invalid design, conflict); a refusal puts the page back
//    to the service's revision and says why.  Nothing is last-writer-wins.
//  * LIVE.  Server-sent events carry every commit; a remote commit that only appends
//    records is applied as one EDITOR.transaction (the view holds), anything else is a
//    full EDITOR.restore of the head.  Missed events (a `gap`) mean a fresh snapshot.
//  * PROMPTS.  A prompt is anchored at Send: the selection, a picked object, a lasso, a
//    point, a program row, a metric, a frame, sketches in DIAGRAM coordinates with the
//    viewport they were drawn in, a manual demonstration.  The service freezes that into
//    an immutable context snapshot and queues the delivery to the connected agent.
//  * TRUTH.  "committed", "rendered", "presented", "queued", "accepted", "uncertain" are
//    different words on this page because they are different facts.
//
//  * CHAT.  By default the dock is one conversation with the agent (buildChat); the tabs
//    with sessions, threads, activity and results are the debug view.
//  * PAGES.  The agent may operate the page it is talking about, through a fixed set of
//    actions (pageact.js: read, scroll, highlight, click, fill, press, navigate, step,
//    open_lesson), never by running script.  In Studio they run here; on a website page
//    (mode `page`: this chat is a frame from the workspace inside the mirrored page, see
//    mirror.py) the frame passes them to the page by postMessage, and whatever the page
//    answers is data, not authority.
//
// All text that came from anyone (labels, prompts, agent replies) is set with
// textContent, never innerHTML.

(function () {
'use strict';
var CFG = JSON.parse(document.getElementById('qccd-live-config').textContent);
if ((CFG.mode === 'run' || CFG.mode === 'compare') && window.self !== window.top) return;   // a frame of the side-by-side view
var ED = globalThis.EDITOR || null;
var PAGE = CFG.mode === 'page';
var S = {
  csrf: null, view: null, branch: 'main', rev: null, synced: null, inflight: false,
  restoring: false, needRefresh: false, mine: Object.create(null), cursor: 0, es: null,
  connected: false, sessions: [], prompts: [], promptById: Object.create(null), target: null,
  follow: true, tab: 'agent', tool: null, anchors: [], sketches: [], demo: null, draftId: null,
  draftTimer: null, mode: localGet('qccd.live.mode') || 'propose', steer: false,
  protected: Object.create(null), lastTouched: [], highlight: [], agentMsgs: [], history: [],
  jobs: [], subs: [], board: null, lastViewPatch: 0, viewDirty: true, openPrompt: null,
  paired: false, lastError: null, compile: null,
  // the chat view (default) vs the debug view (tabs)
  debug: localGet('qccd.live.debug') === '1' || /[?&]debug(=1)?(&|$)/.test(location.search.slice(1)),
  conv: [], working: [], pending: null, codex: null, claude: null, connecting: false, branches: [], convTimer: null,
  lastSel: '', selOff: false, convScrolled: false,
  // the page this chat is about (a website page, or Studio itself)
  page: PAGE ? { url: CFG.page || '/web/', site: CFG.site } : null, pageDoing: null
};
var NS = 'http://www.w3.org/2000/svg';

function localGet(k) { try { return globalThis.localStorage && localStorage.getItem(k); } catch (e) { return null; } }
function localSet(k, v) { try { if (globalThis.localStorage) localStorage.setItem(k, v); } catch (e) { /* none */ } }
function sessGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
function sessSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* none */ } }

// ------------------------------------------------------------------ DOM (text-safe)
function h(tag, attrs, kids) {
  var el = document.createElement(tag);
  attrs = attrs || {};
  for (var k in attrs) {
    if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
    var v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') el.textContent = String(v);
    else if (k === 'on') { for (var ev in v) el.addEventListener(ev, v[ev]); }
    else if (k === 'cls') el.className = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  (kids || []).forEach(function (c) {
    if (c === null || c === undefined || c === false) return;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return el;
}
function J(x) { return JSON.stringify(x); }
function rid(p) { return p + '-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36); }
function strip(rec) {
  var o = {};
  for (var k in rec) if (k !== 'meta' && k !== '_report') o[k] = rec[k];
  return o;
}
function N(rec) { return J(strip(rec)); }

// ------------------------------------------------------------------ transport
function api(method, path, body) {
  var headers = { 'Content-Type': 'application/json' };
  if (S.csrf) headers['X-QCCD-CSRF'] = S.csrf;
  if (S.view) headers['X-QCCD-View'] = S.view;
  return fetch(path, { method: method, headers: headers, credentials: 'same-origin',
                       body: body === undefined ? undefined : J(body) })
    .then(function (r) {
      return r.text().then(function (t) {
        var d = null;
        try { d = t ? JSON.parse(t) : null; } catch (e) { d = { error: { code: 'bad_json', message: t.slice(0, 200) } }; }
        return { ok: r.ok, status: r.status, data: d, error: (d && d.error) || null };
      });
    }, function (e) { return { ok: false, status: 0, data: null, error: { code: 'network', message: String(e) } }; });
}

// ------------------------------------------------------------------ notices
function notice(msg, kind, action) {
  var box = document.getElementById('qcl-notice');
  if (!box) { box = h('div', { id: 'qcl-notice' }); document.body.appendChild(box); }
  var t = h('div', { cls: 'qcl-toast' + (kind === 'bad' ? ' bad' : '') }, [h('span', { text: msg })]);
  if (action) t.appendChild(h('button', { cls: 'qcl-btn', text: action.label, on: { click: function () { action.run(); t.remove(); } } }));
  t.appendChild(h('button', { cls: 'qcl-btn', text: '×', on: { click: function () { t.remove(); } } }));
  box.appendChild(t);
  while (box.children.length > 4) box.removeChild(box.firstChild);
  setTimeout(function () { if (t.parentNode) t.remove(); }, action ? 20000 : 7000);
}

// ------------------------------------------------------------------ local state + diff
function localState() {
  return {
    geom: ED.geom(), seed: ED.seed(), post: ED.post(),
    edits: ED.edits().map(function (r) { var o = strip(r); if (r.meta) o.meta = r.meta; return o; }),
    program: ED.program().map(function (r) { return { method: r.method, args: r.args || [], kwargs: r.kwargs || {} }; })
  };
}
function docState(doc) {
  return { geom: doc.geom || [], seed: doc.seed || null, post: doc.post || [], edits: doc.edits || [],
           program: ((doc.program || {}).calls) || [] };
}
function diffOps(cur, base) {
  if (!base) return null;
  var op = { type: 'studio_sync' }, i;
  var baseChanged = J(cur.geom) !== J(base.geom) || J(cur.seed) !== J(base.seed) || J(cur.post) !== J(base.post);
  if (baseChanged) {
    op.base = { geom: cur.geom, seed: cur.seed, post: cur.post };
    op.append = cur.edits.map(strip);
  } else {
    i = 0;
    while (i < cur.edits.length && i < base.edits.length && N(cur.edits[i]) === N(base.edits[i])) i++;
    if (i < base.edits.length) op.remove = base.edits.slice(i).map(strip);
    if (i < cur.edits.length) op.append = cur.edits.slice(i).map(function (r) {
      var o = strip(r); if (r.meta) o.meta = { group: r.meta.group, label: r.meta.label, src: r.meta.src }; return o; });
  }
  if (J(cur.program) !== J(base.program)) op.program = cur.program;
  return (op.base || op.remove || op.append || op.program) ? op : null;
}
function summarize(op) {
  var bits = [];
  if (op.base) bits.push('new device');
  if (op.remove) bits.push('took back ' + op.remove.length + ' edit' + (op.remove.length > 1 ? 's' : ''));
  if (op.append) bits.push(op.append.length + ' edit' + (op.append.length > 1 ? 's' : '') + ' in Studio');
  if (op.program) bits.push('program');
  return bits.join(', ');
}

// ------------------------------------------------------------------ sync (page -> service)
function sync() {
  if (CFG.mode !== 'design' || !S.paired || S.inflight || S.restoring || S.synced === null) return;
  if (S.agentHold) return;                 // an agent's verb is drawing: its records are the agent's (syncAs)
  if (ED.dragging && ED.dragging()) return;
  var cur = localState();
  var op = diffOps(cur, S.synced);
  if (!op) { if (S.needRefresh) refreshHead(); return; }
  S.inflight = true;
  var req = rid('ui');
  S.mine[req] = true;
  api('POST', '/api/change-sets', { branch: S.branch, expected_revision: S.rev, request_id: req, mode: 'apply',
                                    rebase: 'if_disjoint', summary: summarize(op), operations: [op] })
    .then(function (r) {
      S.inflight = false;
      if (r.ok && (r.data.status === 'committed' || r.data.status === 'noop')) {
        S.synced = cur;
        S.rev = r.data.revision;
        S.viewDirty = true;
        if (r.data.rebased_onto !== null && r.data.rebased_onto !== undefined) S.needRefresh = true;
        if (S.needRefresh) refreshHead();
        renderStatus();
        return;
      }
      var e = r.error || {};
      var why = e.code === 'protected'
        ? 'Not applied: ' + ((e.detail && e.detail.violations || []).map(function (v) { return v.key; }).join(', ')) +
          ' is protected. Unprotect it first.'
        : e.code === 'conflict' ? 'Your edit conflicted with a newer change (' + e.message + '). Studio shows the current design.'
        : 'Not applied: ' + (e.message || e.code || 'the service refused the edit');
      notice(why, 'bad');
      refreshHead(true);
    });
}

// the person's own edits first, so they stay the person's: resolves once nothing is left to sync
function flushSync() {
  return new Promise(function (resolve) {
    var t0 = Date.now();
    (function tick() {
      if (!S.inflight && (S.synced === null || !diffOps(localState(), S.synced) || Date.now() - t0 > 6000)) { resolve(); return; }
      if (!S.inflight) sync();
      setTimeout(tick, 80);
    })();
  });
}
// what an agent's page action drew, committed as THAT agent's change set (service.change_set checks
// that the action is running on this page and records the agent, with the agent's protection limits)
function syncAs(actionId, label) {
  var cur = localState(), op = diffOps(cur, S.synced);
  if (!op) return Promise.resolve({ ok: true, data: { status: 'noop' } });
  S.inflight = true;
  return api('POST', '/api/change-sets', { branch: S.branch, expected_revision: S.rev, request_id: rid('agent'), mode: 'apply',
                                           rebase: 'if_disjoint', summary: label, operations: [op],
                                           by_page_action: actionId })
    .then(function (r) {
      S.inflight = false;
      if (r.ok && (r.data.status === 'committed' || r.data.status === 'noop')) {
        S.synced = cur; S.rev = r.data.revision; S.viewDirty = true; renderStatus();
        return r;
      }
      refreshHead(true);                    // refused: the canvas goes back to the design as it is
      return r;
    });
}

// ------------------------------------------------------------------ service -> page
function loadHead() {
  return api('GET', '/api/design?branch=' + encodeURIComponent(S.branch)).then(function (r) {
    if (!r.ok) { S.lastError = r.error; renderStatus(); return false; }
    applyHead(r.data, true);
    return true;
  });
}
function applyHead(doc, full) {
  var ws = doc.qccd_workspace || {};
  var copy = {};
  for (var k in doc) if (k !== 'qccd_workspace') copy[k] = doc[k];
  var head = docState(copy);
  S.protected = Object.create(null);
  var prot = ((ws.constraints || {}).protected) || {};
  for (var key in prot) S.protected[key] = prot[key];
  S.restoring = true;
  var done = false;
  var local = localState();
  // THE CHEAP PATH: the head only appended records to what this page already holds.
  if (!full && S.synced && J(head.geom) === J(local.geom) && J(head.seed) === J(local.seed) &&
      J(head.post) === J(local.post) && head.edits.length >= local.edits.length) {
    var prefix = true;
    for (var i = 0; i < local.edits.length; i++) if (N(head.edits[i]) !== N(local.edits[i])) { prefix = false; break; }
    if (prefix) {
      var extra = head.edits.slice(local.edits.length).map(strip);
      var ok = true;
      if (extra.length) {
        var t = ED.transaction(extra, 'agent change r' + ws.revision);
        ok = !!(t && t.ok);
      }
      if (ok && J(head.program) !== J(local.program)) ED.setProgram(head.program);
      done = ok;
    }
  }
  if (!done) {
    var res = ED.restore(copy);
    if (!res.ok) notice('Studio could not draw revision ' + ws.revision + ': ' +
                        ((res.problems || [])[0] || {}).message, 'bad');
  }
  S.synced = docState(copy);
  S.rev = ws.revision;
  S.restoring = false;
  S.viewDirty = true;
  paintOverlay();
  renderStatus();
}
function refreshHead(full) {
  if (S.inflight) { S.needRefresh = true; return; }
  S.needRefresh = false;
  return api('GET', '/api/design?branch=' + encodeURIComponent(S.branch)).then(function (r) {
    if (r.ok) applyHead(r.data, !!full);
  });
}

// ------------------------------------------------------------------ events
function openEvents() {
  if (S.es) { try { S.es.close(); } catch (e) { /* ignore */ } }
  var es = new EventSource('/api/events?cursor=' + S.cursor + '&view=' + encodeURIComponent(S.view || ''));
  S.es = es;
  es.onopen = function () { S.connected = true; S.esRetry = 0; renderStatus(); };
  es.onerror = function () {
    S.connected = false; renderStatus();
    // the browser retries a dropped stream itself, but gives up for good on an HTTP error
    // (a service restarting answers 503/401 for a moment): reopen with a backoff
    if (es.readyState === 2 && S.es === es) {
      S.esRetry = Math.min((S.esRetry || 0) + 1, 6);
      setTimeout(function () { if (S.es === es) openEvents(); }, 500 * Math.pow(2, S.esRetry));
    }
  };
  function on(type, fn) { es.addEventListener(type, function (m) {
    var e = null; try { e = JSON.parse(m.data); } catch (x) { return; }
    if (m.lastEventId) S.cursor = +m.lastEventId;
    try { fn(e); } catch (x) { console.error('qccd live', type, x); }
  }); }
  on('hello', function () { S.connected = true; renderStatus(); });
  on('gap', function () { notice('Studio missed some updates; reloading the current design.'); refreshHead(true); refreshAll(); });
  on('design.committed', function (e) {
    if (e.branch !== S.branch) return;
    var p = e.payload || {};
    if (S.mine[p.request_id]) { delete S.mine[p.request_id]; return; }
    S.lastTouched = (p.touched || []).filter(function (k) { return k.indexOf('node:') === 0 || k.indexOf('segment:') === 0; });
    var who = (p.actor || {}).kind === 'agent' ? 'The agent' : ((p.actor || {}).id === 'cli' ? 'The terminal' : 'Another tab');
    if (S.rev !== null && e.revision !== S.rev + 1) S.needRefresh = true;
    refreshHead().then(function () {
      if (S.follow) { reveal(S.lastTouched); flash(S.lastTouched); }
      if ((p.actor || {}).kind === 'agent') showAgentWork(S.lastTouched, shortName((p.actor || {}).label || (p.actor || {}).id),
                                                          'r' + e.revision + ' ' + (p.summary || p.diff_summary || 'changed the design'));
      else if (S.debug) notice(who + ' committed r' + e.revision + ': ' + (p.summary || p.diff_summary || ''), null,
                  { label: 'Show', run: function () { flash(S.lastTouched); } });
    });
    loadHistory();
  });
  ['prompt.sent', 'comment.posted', 'reply.posted', 'work.updated', 'delivery.updated', 'prompt.linked'].forEach(function (t) {
    on(t, function (e) {
      var p = e.payload || {};
      if (!S.debug) loadConvSoon();
      if (t === 'reply.posted' && (p.author || {}).kind === 'agent' && S.debug) {
        notice('Agent replied on ' + p.prompt_id + ': ' + String(p.text || '').slice(0, 120), null,
               { label: 'Open', run: function () { openThread(p.prompt_id); } });
      }
      loadPrompts();
    });
  });
  ['session.updated', 'session.stopped', 'view.updated', 'agent.turn'].forEach(function (t) { on(t, function () { loadSessions(); }); });
  on('agent.message', function (e) {
    S.agentMsgs.push({ at: e.ts, text: (e.payload || {}).text, session: (e.payload || {}).session_id });
    if (S.agentMsgs.length > 30) S.agentMsgs.shift();
    if (S.tab === 'activity') renderBody();
  });
  on('agent.approval_requested', function (e) {
    notice('The agent asked for an approval in its own terminal (' + ((e.payload || {}).method || '') + ').');
  });
  ['job.updated', 'submission.updated', 'snapshot.created'].forEach(function (t) { on(t, function (e) {
    var p = e.payload || {};
    if (!S.debug) loadConvSoon();
    if (t === 'job.updated' && p.kind === 'compile' && p.status === 'succeeded') S.compile = p.job_id;
    if (t === 'job.updated' && p.job_id) trackRun(p);
    loadResults();
  }); });
  on('branch.created', function () { if (S.debug) renderBody(); else loadBranches(); });
  on('present', function (e) { present(e.payload || {}, e.branch); });
  on('page.action', function (e) { pageAction(e.payload || {}); });
}

// ------------------------------------------------------------------ overlay (pins, flashes, sketches)
function overlay() {
  var svg = document.getElementById('svg');
  if (!svg) return null;
  var g = document.getElementById('qcl-ov');
  if (!g || g.parentNode !== svg) {
    g = document.createElementNS(NS, 'g');
    g.setAttribute('id', 'qcl-ov');
    g.setAttribute('pointer-events', 'none');
    svg.appendChild(g);
  }
  return g;
}
function L() { return ED && ED.layout ? ED.layout() : null; }
function toUser(x, y) { var l = L(); return { x: l.ox + x * l.sx, y: l.oy + y * l.sy }; }
function toLattice(ux, uy) { var l = L(); return { x: (ux - l.ox) / l.sx, y: (uy - l.oy) / l.sy }; }
function nodePos(id) {
  var st = ED.state();
  var n = st && st.device && st.device.nodes && st.device.nodes[id];
  if (!n) return null;
  return { x: +QCCD.unbox(n.pos[0]), y: +QCCD.unbox(n.pos[1]) };
}
function keyPos(key) {
  var i = key.indexOf(':'), kind = key.slice(0, i), id = key.slice(i + 1);
  if (kind === 'node') return nodePos(id);
  if (kind === 'segment') {
    var st = ED.state(), s = st && st.device && st.device.segments && st.device.segments[id];
    if (!s) return null;
    var a = nodePos(s.a), b = nodePos(s.b);
    return a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null;
  }
  return null;
}
function el(tag, attrs) {
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function paintOverlay() {
  var g = overlay();
  if (!g) return;
  while (g.firstChild) g.removeChild(g.firstChild);
  var l = L();
  if (!l) return;
  var r = Math.max(4, (l.g || 20) * 0.35);
  // protected entities: a lock ring
  Object.keys(S.protected).forEach(function (k) {
    var p = keyPos(k); if (!p) return;
    var u = toUser(p.x, p.y);
    g.appendChild(el('circle', { cx: u.x, cy: u.y, r: r * 1.35, fill: 'none', stroke: '#7c5bfa',
                                 'stroke-width': Math.max(1.5, r * 0.18), 'stroke-dasharray': r * 0.5 + ' ' + r * 0.3 }));
  });
  // highlights (agent presentation / flashes)
  S.highlight.forEach(function (k) {
    var p = keyPos(k); if (!p) return;
    var u = toUser(p.x, p.y);
    g.appendChild(el('circle', { cx: u.x, cy: u.y, r: r * 1.8, fill: 'rgba(246,195,74,.25)', stroke: '#e8890c',
                                 'stroke-width': Math.max(1.5, r * 0.2) }));
  });
  // prompt pins
  S.prompts.forEach(function (p, idx) {
    if (!p.anchors || !p.anchors.length || p.work === 'resolved' || p.work === 'cancelled') return;
    var pos = anchorCentroid(p.anchors);
    if (!pos) return;
    var u = toUser(pos.x, pos.y);
    var col = p.work === 'ready_for_review' ? '#12a150' : p.work === 'working' ? '#e8890c' : '#1e2761';
    g.appendChild(el('path', { d: 'M' + u.x + ',' + u.y + ' l' + (-r) + ',' + (-2.2 * r) + ' a' + r + ',' + r + ' 0 1 1 ' + 2 * r + ',0 z',
                               fill: col, stroke: '#fff', 'stroke-width': Math.max(1, r * 0.15) }));
  });
  // sketches of the composer (not yet sent)
  S.sketches.forEach(function (s) { drawSketch(g, s, '#c2308a'); });
  S.anchors.forEach(function (a) {
    if (a.kind === 'region' && a.lasso) drawSketch(g, { kind: 'lasso', points: a.lasso }, '#2a78d6');
    if (a.kind === 'point' && a.pos) {
      var u2 = toUser(a.pos[0], a.pos[1]);
      g.appendChild(el('circle', { cx: u2.x, cy: u2.y, r: r * 0.6, fill: '#2a78d6' }));
    }
  });
}
function drawSketch(g, s, color) {
  if (!s.points || s.points.length < 1) return;
  var d = s.points.map(function (p, i) { var u = toUser(p[0], p[1]); return (i ? 'L' : 'M') + u.x + ',' + u.y; }).join(' ');
  if (s.kind === 'lasso') d += ' Z';
  var l = L(), w = Math.max(1.5, (l.g || 20) * 0.08);
  g.appendChild(el('path', { d: d, fill: s.kind === 'lasso' ? 'rgba(42,120,214,.08)' : 'none', stroke: color,
                             'stroke-width': w, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  if (s.kind === 'arrow' && s.points.length >= 2) {
    var a = s.points[s.points.length - 2], b = s.points[s.points.length - 1];
    var ua = toUser(a[0], a[1]), ub = toUser(b[0], b[1]);
    var ang = Math.atan2(ub.y - ua.y, ub.x - ua.x), k = w * 5;
    g.appendChild(el('path', { d: 'M' + ub.x + ',' + ub.y + ' L' + (ub.x - k * Math.cos(ang - 0.45)) + ',' + (ub.y - k * Math.sin(ang - 0.45)) +
                               ' M' + ub.x + ',' + ub.y + ' L' + (ub.x - k * Math.cos(ang + 0.45)) + ',' + (ub.y - k * Math.sin(ang + 0.45)),
                               stroke: color, 'stroke-width': w, fill: 'none', 'stroke-linecap': 'round' }));
  }
}
function anchorCentroid(anchors) {
  var pts = [];
  anchors.forEach(function (a) {
    (a.keys || (a.key ? [a.key] : [])).concat(a.entities_inside || []).forEach(function (k) { var p = keyPos(k); if (p) pts.push(p); });
    if (a.pos) pts.push({ x: a.pos[0], y: a.pos[1] });
  });
  if (!pts.length) return null;
  var x = 0, y = 0;
  pts.forEach(function (p) { x += p.x; y += p.y; });
  return { x: x / pts.length, y: y / pts.length };
}
function flash(keys) {
  S.highlight = (keys || []).slice(0, 200);
  paintOverlay();
  setTimeout(function () { S.highlight = []; paintOverlay(); }, 4000);
}
// FOLLOW: widen the view just enough to show `keys`, keeping what is already on screen.
// The page's own `VB` / `applyVB` (render.py) are top-level bindings of the stock page;
// if a future page does not have them, following degrades to a flash, never an error.
function reveal(keys) {
  if (typeof VB === 'undefined' || typeof applyVB !== 'function') return false;
  var xs = [], ys = [];
  (keys || []).forEach(function (k) { var p = keyPos(k); if (p) { var u = toUser(p.x, p.y); xs.push(u.x); ys.push(u.y); } });
  if (!xs.length) return false;
  var l = L(), pad = Math.max(10, (l && l.g || 20) * 1.5);
  var x0 = Math.min.apply(null, xs) - pad, x1 = Math.max.apply(null, xs) + pad;
  var y0 = Math.min.apply(null, ys) - pad, y1 = Math.max.apply(null, ys) + pad;
  if (x0 >= VB.x && x1 <= VB.x + VB.w && y0 >= VB.y && y1 <= VB.y + VB.h) return false;
  var nx0 = Math.min(VB.x, x0), ny0 = Math.min(VB.y, y0);
  var nx1 = Math.max(VB.x + VB.w, x1), ny1 = Math.max(VB.y + VB.h, y1);
  var w = nx1 - nx0, hgt = ny1 - ny0, aspect = VB.w / Math.max(1e-9, VB.h);
  if (w / hgt > aspect) { var nh = w / aspect; ny0 -= (nh - hgt) / 2; hgt = nh; }
  else { var nw = hgt * aspect; nx0 -= (nw - w) / 2; w = nw; }
  VB.x = nx0; VB.y = ny0; VB.w = w; VB.h = hgt;
  applyVB();
  paintOverlay();
  return true;
}

// ------------------------------------------------------------------ capture (pick / lasso / point / sketch)
function selectionKeys() {
  if (!ED || !ED.selection) return [];
  return (ED.selection() || []).map(function (s) {
    return (s.kind === 'site' || s.kind === 'junction' || s.kind === 'node' ? 'node:' : s.kind + ':') + s.id;
  });
}
function setTool(tool) {
  S.tool = (S.tool === tool) ? null : tool;
  var cap = document.getElementById('qcl-capture');
  if (cap) cap.remove();
  if (S.tool && (S.tool === 'lasso' || S.tool === 'point' || S.tool === 'arrow' || S.tool === 'stroke' || S.tool === 'pick')) {
    var host = document.getElementById('canvas');
    if (host) {
      cap = h('div', { id: 'qcl-capture', title: 'Esc to cancel' });
      host.appendChild(cap);
      var pts = null;
      cap.addEventListener('pointerdown', function (e) {
        e.preventDefault(); e.stopPropagation();
        var m = ED.toModel(e.clientX, e.clientY), lp = toLattice(m.x, m.y);
        if (S.tool === 'point') { addPoint(lp, m); setTool(null); return; }
        if (S.tool === 'pick') { pickAt(m, lp); setTool(null); return; }
        pts = [[lp.x, lp.y]];
        cap.setPointerCapture(e.pointerId);
      });
      cap.addEventListener('pointermove', function (e) {
        if (!pts) return;
        var m = ED.toModel(e.clientX, e.clientY), lp = toLattice(m.x, m.y);
        var last = pts[pts.length - 1];
        if (Math.abs(last[0] - lp.x) + Math.abs(last[1] - lp.y) > 0.02) pts.push([lp.x, lp.y]);
        S.preview = { kind: S.tool === 'lasso' ? 'lasso' : S.tool, points: pts };
        paintOverlay(); var g = overlay(); if (g) drawSketch(g, S.preview, '#e8890c');
      });
      cap.addEventListener('pointerup', function () {
        if (!pts) return;
        var done = pts; pts = null; S.preview = null;
        if (S.tool === 'lasso') addLasso(done);
        else if (done.length >= 2) addSketch(S.tool, done);
        setTool(null);
      });
    }
  }
  renderBody();
}
function addPoint(lp, m) {
  var near = [];
  var hit = ED.hit(m.x, m.y, true);
  if (hit && hit.id) near.push(hitKey(hit));
  addAnchor({ kind: 'point', pos: [round(lp.x), round(lp.y)], near: near, viewport: viewport() });
}
function pickAt(m, lp) {
  var hit = ED.hit(m.x, m.y, true);
  if (hit && hit.id) addAnchor({ kind: 'entity', key: hitKey(hit) });
  else addAnchor({ kind: 'point', pos: [round(lp.x), round(lp.y)], near: [], viewport: viewport() });
}
function hitKey(hit) {
  return (hit.kind === 'site' || hit.kind === 'junction' ? 'node:' : hit.kind + ':') + hit.id;
}
function addLasso(poly) {
  var st = ED.state(), inside = [], nodes = (st && st.device && st.device.nodes) || {}, segs = (st && st.device && st.device.segments) || {};
  var inNode = Object.create(null);
  for (var id in nodes) {
    var p = nodePos(id);
    if (p && pip(p, poly)) { inside.push('node:' + id); inNode[id] = true; }
  }
  for (var sid in segs) if (inNode[segs[sid].a] && inNode[segs[sid].b]) inside.push('segment:' + sid);
  var xs = poly.map(function (q) { return q[0]; }), ys = poly.map(function (q) { return q[1]; });
  addAnchor({ kind: 'region', lasso: poly.map(function (q) { return [round(q[0]), round(q[1])]; }),
              bbox: [round(Math.min.apply(null, xs)), round(Math.min.apply(null, ys)),
                     round(Math.max.apply(null, xs)), round(Math.max.apply(null, ys))],
              entities_inside: inside, viewport: viewport() });
}
function pip(p, poly) {
  var c = false;
  for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    var xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
    if (((yi > p.y) !== (yj > p.y)) && (p.x < (xj - xi) * (p.y - yi) / ((yj - yi) || 1e-12) + xi)) c = !c;
  }
  return c;
}
function addSketch(kind, pts) {
  S.sketches.push({ kind: kind, points: pts.map(function (q) { return [round(q[0]), round(q[1])]; }), viewport: viewport() });
  paintOverlay(); saveDraftSoon(); renderBody();
}
function addAnchor(a) {
  S.anchors.push(a);
  paintOverlay(); saveDraftSoon(); renderBody();
}
function round(x) { return Math.round(x * 1000) / 1000; }
function viewport() {
  var vb = (window.VIEW && window.VIEW.vb) ? window.VIEW.vb() : null;
  var l = L();
  return { vb: vb, layout: l ? { ox: l.ox, oy: l.oy, sx: l.sx, sy: l.sy } : null,
           units: 'diagram: lattice units; vb: stage user units' };
}
// program/listing rows and panel fields: semantic identifiers, not pixels
document.addEventListener('click', function (e) {
  if (S.tool !== 'pick') return;
  var t = e.target;
  if (t && t.closest && (t.closest('#qcl-dock') || t.closest('#qcl-capture'))) return;
  for (var n = t, depth = 0; n && depth < 10; n = n.parentNode, depth++) {
    if (n._ref) {
      var r = n._ref;
      if (r.kind === 'instr') addAnchor({ kind: 'instruction', instr_id: r.id, frame: r.i, run: CFG.snapshot_id || null });
      else addAnchor({ kind: 'entity', key: (r.kind === 'site' || r.kind === 'junction' ? 'node' : r.kind) + ':' + r.id });
      e.preventDefault(); e.stopPropagation(); setTool(null); return;
    }
    if (n.getAttribute && n.getAttribute('data-id') && n.getAttribute('data-k')) {
      var k = n.getAttribute('data-k');
      addAnchor({ kind: 'entity', key: (k === 'site' || k === 'junction' ? 'node' : k) + ':' + n.getAttribute('data-id') });
      e.preventDefault(); e.stopPropagation(); setTool(null); return;
    }
    if (n.getAttribute && n.getAttribute('data-hint')) {
      var pane = n.closest ? n.closest('section,aside,[id^="pane"]') : null;
      var hint = n.getAttribute('data-hint');
      addAnchor(hint.indexOf('rule:') === 0 ? { kind: 'diagnostic', rule: hint.slice(5) }
                : { kind: 'field', panel: pane && pane.id || 'page', field: hint, value: (n.textContent || '').trim().slice(0, 120) });
      e.preventDefault(); e.stopPropagation(); setTool(null); return;
    }
  }
  var sec = t && t.closest ? t.closest('section,aside,[id]') : null;
  addAnchor({ kind: 'panel', panel: (sec && sec.id) || 'page', text: ((t && t.textContent) || '').trim().slice(0, 120) });
  e.preventDefault(); e.stopPropagation(); setTool(null);
}, true);
document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && S.tool) { setTool(null); } }, true);

// ------------------------------------------------------------------ prompts
function body() {
  return { text: (document.getElementById('qcl-text') || {}).value || S.textCache || '',
           intent: S.mode === 'ask' ? 'question' : (S.mode === 'apply' ? 'apply_change' : 'propose_change'),
           mode: S.mode, anchors: S.anchors.slice(), sketches: S.sketches.slice(),
           demonstration: S.demo && S.demo.after !== undefined ?
             { before_revision: S.demo.before, after_revision: S.demo.after, targets: S.demo.targets } : undefined,
           steer: S.steer || undefined };
}
function saveDraftSoon() {
  if (S.draftTimer) clearTimeout(S.draftTimer);
  S.draftTimer = setTimeout(saveDraft, 800);
}
function saveDraft() {
  S.draftTimer = null;
  var b = body();
  if (!b.text && !b.anchors.length && !b.sketches.length) return Promise.resolve();
  return api('POST', '/api/prompts/draft', { prompt_id: S.draftId, body: b }).then(function (r) {
    if (r.ok) S.draftId = r.data.prompt_id;
  });
}
function send(opts) {
  opts = opts || {};
  if (opts.text !== undefined) S.textCache = opts.text;
  var ta = document.getElementById('qcl-text');
  if (ta && opts.text !== undefined && S.debug) ta.value = opts.text;
  if (opts.mode) S.mode = opts.mode;
  if (opts.anchors) S.anchors = opts.anchors.slice();
  if (opts.sketches) S.sketches = opts.sketches.slice();
  if (opts.comment) S.mode = 'comment';
  var b = body();
  if (S.mode === 'comment') { b.intent = 'comment'; b.mode = 'propose'; }
  if (!b.text.trim()) { notice('Write what you want first.', 'bad'); return Promise.resolve(null); }
  if (!b.anchors.length) b.anchors = [{ kind: 'workspace' }];
  var sel = selectionKeys();
  var ctx = { design_revision: CFG.mode === 'design' ? S.rev : undefined, selection: sel, viewport: viewport(),
              frame: (typeof frame === 'number') ? frame : null,
              displayed_run: CFG.snapshot_id || null, target_session: S.target || undefined,
              panel: S.tab, page: pageInfo() };
  var req = rid('send');
  return api('POST', '/api/prompts/send', { prompt_id: S.draftId || undefined, body: b, view_id: S.view,
                                            context: ctx, request_id: req })
    .then(function (r) {
      if (!r.ok) { notice('Not sent: ' + ((r.error || {}).message || 'error'), 'bad'); return null; }
      var d = r.data;
      S.draftId = null; S.anchors = []; S.sketches = []; S.demo = null; S.textCache = ''; S.steer = false;
      if (ta) ta.value = '';
      var how = d.delivery ? 'queued for ' + sessionLabel(d.delivery.session_id) : (d.status === 'posted' ? 'posted as a comment' : 'saved; no agent is connected');
      if (S.debug) notice('Prompt ' + d.prompt_id + ' v' + d.version + ' sent at r' + d.design_revision + ' - ' + how + '.');
      S.mode = localGet('qccd.live.mode') || 'propose';
      paintOverlay(); loadPrompts(); renderBody();
      return d;
    });
}
function openThread(pid) { S.openPrompt = pid; S.tab = 'prompts'; renderTabs(); renderBody(); }
function editPrompt(p) {
  // a NEW version: the sent one stays exactly as it was delivered
  S.draftId = p.prompt_id;
  var sent = p.latest_sent || p.latest;
  S.anchors = (sent.body.anchors || []).filter(function (a) { return a.kind !== 'workspace'; }).map(function (a) {
    var o = {}; for (var k in a) if (k !== 'resolved' && k !== 'missing') o[k] = a[k]; return o; });
  S.sketches = (sent.body.sketches || []).slice();
  S.textCache = sent.body.text;
  S.mode = sent.body.mode || 'propose';
  S.tab = 'compose'; renderTabs(); renderBody();
  var ta = document.getElementById('qcl-text'); if (ta) ta.value = S.textCache;
}

// ------------------------------------------------------------------ presentation
function present(p, branch) {
  if (p.view_id && p.view_id !== S.view) return;
  if (!p.view_id && branch && branch !== S.branch && p.action !== 'compare' && p.action !== 'open_run') return;
  var t = p.target || {};
  function doit() {
    if (!ED && (p.action === 'highlight' || p.action === 'select' || p.action === 'select_frame')) return;
    if (p.action === 'highlight' || p.action === 'select') {
      if (p.action === 'select') ED.select((t.keys || []).map(function (k) {
        var i = k.indexOf(':'), kind = k.slice(0, i); return { kind: kind === 'node' ? 'site' : kind, id: k.slice(i + 1) }; }));
      reveal(t.keys || []);
      flash(t.keys || []);
    } else if (p.action === 'open_prompt') openThread(t.prompt_id);
    else if (p.action === 'select_frame' && typeof seek === 'function') seek(+t.frame || 0, {});
    else if (!S.debug && (p.action === 'compare' || p.action === 'open_run')) {
      var url = p.action === 'compare' ? '/compare?runs=' + (t.runs || []).map(encodeURIComponent).join(',')
                                       : '/runview/' + encodeURIComponent(t.run_id || '');
      loadConvSoon();
      // like a person showing you: the tab you are looking at opens it
      if (document.visibilityState === 'visible') goTo(url);
      else notice(p.action === 'compare' ? 'The agent put two runs side by side.' : 'The agent has a run for you to watch.', null,
                  { label: 'Open', run: function () { goTo(url); } });
    }
    else if (p.action === 'open_result' || p.action === 'compare') { S.tab = 'results'; renderTabs(); loadResults(); }
    else if (p.action === 'reveal_diagnostic') { S.tab = 'results'; renderTabs(); loadResults(); flash(t.keys || []); }
    else if (p.action === 'open_branch') { S.tab = 'activity'; renderTabs(); renderBody(); }
  }
  var outcome;
  if (S.follow) { doit(); outcome = 'displayed'; }
  else { notice('The agent wants to show you: ' + (p.note || p.action), null, { label: 'Show', run: doit }); outcome = 'notified'; }
  api('POST', '/api/views/' + encodeURIComponent(S.view) + '/presented', { presentation_id: p.presentation_id, outcome: outcome });
}

// ------------------------------------------------------------------ loaders
function loadSessions() {
  return api('GET', '/api/sessions').then(function (r) {
    if (!r.ok) return;
    S.sessions = r.data.sessions || [];
    return api('GET', '/api/views').then(function (v) {
      var mine = ((v.data || {}).views || []).filter(function (x) { return x.id === S.view; })[0];
      if (mine) { S.target = mine.target_session; S.follow = mine.follow_agent; }
      renderStatus(); if (S.tab === 'agent' || S.tab === 'compose') renderBody();
    });
  });
}
function loadPrompts() {
  return api('GET', '/api/prompts?limit=100').then(function (r) {
    if (!r.ok) return;
    S.prompts = r.data.prompts || [];
    paintOverlay(); renderStatus();
    if (S.tab === 'prompts') renderBody();
  });
}
function loadHistory() {
  return api('GET', '/api/history?branch=' + encodeURIComponent(S.branch) + '&limit=25').then(function (r) {
    if (r.ok) S.history = r.data.history || [];
    if (S.tab === 'activity') renderBody();
  });
}
function loadResults() {
  return Promise.all([api('GET', '/api/jobs?limit=15'), api('GET', '/api/leaderboard')]).then(function (rs) {
    if (rs[0].ok) S.jobs = rs[0].data.jobs || [];
    if (rs[1].ok) S.board = rs[1].data;
    if (S.tab === 'results') renderBody();
  });
}
function refreshAll() { loadSessions(); loadPrompts(); loadHistory(); loadResults(); }
function sessionLabel(sid) {
  var s = S.sessions.filter(function (x) { return x.id === sid; })[0];
  return s ? (s.label || s.client) : 'no agent';
}
function capabilityText(s) {
  if (s.mode === 'appserver' && s.client === 'claude') return 'automatic: each message runs Claude Code in one conversation; stop supported';
  if (s.mode === 'appserver') return 'automatic: starts a turn in the Codex thread; steer and stop supported';
  if (s.mode === 'channel') return 'automatic push to Claude Code (unacknowledged until the agent reads it); no steer or stop';
  if (s.mode === 'pull') return 'reduced: the agent sees prompts only when it next reads its context';
  return s.mode;
}

// ------------------------------------------------------------------ the dock
function buildDock() {
  if (!S.debug) return buildChat();
  var dock = h('div', { id: 'qcl-dock', cls: localGet('qccd.live.min') === '1' ? 'qcl-min' : '' }, [
    h('div', { cls: 'qcl-head' }, [
      h('span', { id: 'qcl-live', cls: 'qcl-dot' }),
      h('b', { text: 'QCCD live' }),
      h('span', { id: 'qcl-rev', cls: 'qcl-chip', text: '...' }),
      h('span', { id: 'qcl-agent', cls: 'qcl-chip', text: 'no agent' }),
      h('span', { cls: 'qcl-sp' }),
      h('button', { cls: 'qcl-btn', text: 'Chat', title: 'back to the conversation', on: { click: function () { setDebug(false); } } }),
      h('button', { cls: 'qcl-btn', text: '–', title: 'minimise', on: { click: function () {
        dock.classList.toggle('qcl-min'); localSet('qccd.live.min', dock.classList.contains('qcl-min') ? '1' : '0'); } } })
    ]),
    h('div', { cls: 'qcl-tabs', id: 'qcl-tabs' }),
    h('div', { cls: 'qcl-body', id: 'qcl-body' })
  ]);
  document.body.appendChild(dock);
  renderTabs(); renderBody();
}
var TABS = [['compose', 'Prompt'], ['prompts', 'Threads'], ['agent', 'Agent'], ['activity', 'Activity'], ['results', 'Results']];
function renderTabs() {
  var t = document.getElementById('qcl-tabs');
  if (!t) return;
  while (t.firstChild) t.removeChild(t.firstChild);
  TABS.forEach(function (x) {
    t.appendChild(h('button', { cls: S.tab === x[0] ? 'on' : '', text: x[1], on: { click: function () {
      S.tab = x[0]; if (x[0] !== 'prompts') S.openPrompt = null; renderTabs(); renderBody();
      if (x[0] === 'results') loadResults(); if (x[0] === 'activity') loadHistory(); } } }));
  });
}
function renderStatus() {
  if (!S.debug) { renderChatHead(); return; }
  var d = document.getElementById('qcl-live');
  if (d) d.className = 'qcl-dot ' + (S.connected ? 'on' : 'off');
  var r = document.getElementById('qcl-rev');
  if (r) {
    r.textContent = CFG.mode === 'run' ? 'run ' + CFG.snapshot_id + ' (immutable)'
      : (S.rev === null ? 'loading' : ('r' + S.rev + (S.inflight ? ' saving' : '') + (S.connected ? '' : ' offline')));
    r.className = 'qcl-chip' + (S.connected ? '' : ' bad');
  }
  var a = document.getElementById('qcl-agent');
  if (a) {
    var s = S.sessions.filter(function (x) { return x.id === S.target; })[0];
    a.textContent = s ? (s.label || s.client) + ' · ' + s.status + (s.write_fence ? ' · stopped' : '') : 'no agent connected';
    a.className = 'qcl-chip ' + (!s ? 'warn' : (s.status === 'connected' && !s.write_fence ? 'ok' : 'bad'));
  }
}
function renderBody() {
  if (!S.debug) { renderChips(); return; }
  var b = document.getElementById('qcl-body');
  if (!b) return;
  while (b.firstChild) b.removeChild(b.firstChild);
  ({ compose: renderCompose, prompts: renderPrompts, agent: renderAgent, activity: renderActivity,
     results: renderResults })[S.tab](b);
}
function btn(text, fn, cls, title) { return h('button', { cls: 'qcl-btn ' + (cls || ''), text: text, title: title, on: { click: fn } }); }

function renderCompose(b) {
  var ta = h('textarea', { id: 'qcl-text', cls: 'qcl-ta', placeholder: 'What should change here? ("Use this structure here, but keep these gate zones")' });
  ta.value = S.textCache || '';
  ta.addEventListener('input', function () { S.textCache = ta.value; saveDraftSoon(); });
  b.appendChild(ta);
  var modes = h('div', { cls: 'qcl-row' }, [h('span', { cls: 'qcl-note', text: 'The agent may:' })]);
  [['ask', 'Ask (answer only)'], ['propose', 'Propose (candidate)'], ['apply', 'Apply locally']].forEach(function (m) {
    modes.appendChild(btn(m[1], function () { S.mode = m[0]; renderBody(); }, S.mode === m[0] ? 'on' : ''));
  });
  modes.appendChild(btn('Comment only', function () { S.mode = 'comment'; renderBody(); }, S.mode === 'comment' ? 'on' : '',
                        'a remark or preference; never sent to the agent as a request'));
  if (S.mode !== 'comment' && S.mode !== (localGet('qccd.live.mode') || 'propose')) {
    modes.appendChild(btn('make default', function () { localSet('qccd.live.mode', S.mode); renderBody(); }, '',
                          'new prompts start in this mode'));
  }
  b.appendChild(modes);
  b.appendChild(h('div', { cls: 'qcl-row' }, [
    btn('+ Selection', function () {
      var keys = selectionKeys();
      if (!keys.length) { notice('Select something on the stage first.'); return; }
      addAnchor(keys.length === 1 ? { kind: 'entity', key: keys[0] } : { kind: 'entity_group', keys: keys });
    }, '', 'anchor to what is selected now'),
    btn('Pick', function () { setTool('pick'); }, S.tool === 'pick' ? 'on' : '', 'click any object, program row, field or metric'),
    btn('Lasso', function () { setTool('lasso'); }, S.tool === 'lasso' ? 'on' : '', 'draw around a region'),
    btn('Point', function () { setTool('point'); }, S.tool === 'point' ? 'on' : '', 'mark an empty place'),
    btn('Arrow', function () { setTool('arrow'); }, S.tool === 'arrow' ? 'on' : ''),
    btn('Sketch', function () { setTool('stroke'); }, S.tool === 'stroke' ? 'on' : ''),
    btn('Frame', function () {
      var f = (typeof frame === 'number') ? frame : 0, fr = (typeof P !== 'undefined' && P.frames && P.frames[f]) || null;
      addAnchor({ kind: 'event', frame: f, instr_id: fr ? fr.id : null, run: CFG.snapshot_id || null });
    }, '', 'the simulation step on screen')
  ]));
  var demo = S.demo;
  b.appendChild(h('div', { cls: 'qcl-row' }, [
    CFG.mode !== 'design' ? null :
    (!demo ? btn('Demonstrate...', function () { S.demo = { before: S.rev }; renderBody();
                  notice('Make the change by hand now, then press "Done demonstrating".'); },
                 '', 'edit by hand, then ask the agent to apply the same change elsewhere')
     : demo.after === undefined ? btn('Done demonstrating (r' + demo.before + ' → r' + S.rev + ')', function () {
                  S.demo.after = S.rev; S.demo.targets = selectionKeys(); renderBody(); saveDraftSoon(); }, 'on',
                  'the current selection becomes the targets to apply it to')
     : h('span', { cls: 'qcl-chip', text: 'demonstration r' + demo.before + '→r' + demo.after + ', ' + (demo.targets || []).length + ' targets' })),
    S.demo ? btn('×', function () { S.demo = null; renderBody(); }) : null
  ]));
  var chips = h('div', { cls: 'qcl-row' });
  S.anchors.forEach(function (a, i) {
    chips.appendChild(h('span', { cls: 'qcl-chip', text: anchorText(a) }, [
      h('button', { cls: 'qcl-btn', text: '×', on: { click: function () { S.anchors.splice(i, 1); paintOverlay(); renderBody(); } } })]));
  });
  S.sketches.forEach(function (s, i) {
    chips.appendChild(h('span', { cls: 'qcl-chip', text: s.kind + ' (' + s.points.length + ' pts)' }, [
      h('button', { cls: 'qcl-btn', text: '×', on: { click: function () { S.sketches.splice(i, 1); paintOverlay(); renderBody(); } } })]));
  });
  if (!S.anchors.length && !S.sketches.length) chips.appendChild(h('span', { cls: 'qcl-note', text: 'No anchor yet: the whole workspace.' }));
  b.appendChild(chips);
  var tgt = S.sessions.filter(function (x) { return x.id === S.target; })[0];
  b.appendChild(h('div', { cls: 'qcl-note', text: tgt ? 'To: ' + (tgt.label || tgt.client) + ' - ' + capabilityText(tgt)
                                                   : 'No agent is connected: the prompt will wait in the queue.' }));
  if (tgt && tgt.mode === 'appserver') {
    var cb = h('input', { type: 'checkbox' }); cb.checked = !!S.steer;
    cb.addEventListener('change', function () { S.steer = cb.checked; });
    b.appendChild(h('label', { cls: 'qcl-row qcl-note' }, [cb, ' Correction: steer the turn that is running now']));
  }
  b.appendChild(h('div', { cls: 'qcl-row' }, [
    btn(S.draftId ? 'Send (new version)' : 'Send', function () { send(); }, 'pri'),
    btn('Protect selection', function () { protect(selectionKeys()); }, '', 'an enforced constraint: nobody can change these until a person unprotects them'),
    btn('Unprotect selection', function () { unprotect(selectionKeys()); })
  ]));
}
function anchorText(a) {
  if (a.kind === 'entity') return a.key;
  if (a.kind === 'entity_group') return a.keys.length + ' entities';
  if (a.kind === 'region') return 'region: ' + (a.entities_inside || []).length + ' inside';
  if (a.kind === 'point') return 'point (' + a.pos[0] + ', ' + a.pos[1] + ')';
  if (a.kind === 'instruction') return 'instruction #' + a.instr_id;
  if (a.kind === 'event') return 'frame ' + a.frame;
  if (a.kind === 'field') return a.field;
  if (a.kind === 'diagnostic') return 'rule ' + a.rule;
  if (a.kind === 'metric') return 'metric ' + a.name;
  return a.kind + (a.panel ? ' ' + a.panel : '');
}
function protect(keys) {
  if (!keys.length) { notice('Select what to protect first.'); return; }
  api('POST', '/api/change-sets', { branch: S.branch, expected_revision: S.rev, request_id: rid('protect'), mode: 'apply',
                                    summary: 'protect ' + keys.join(', '), operations: [{ type: 'protect', keys: keys }] })
    .then(function (r) { if (!r.ok) notice('Not protected: ' + (r.error || {}).message, 'bad'); else refreshHead(); });
}
function unprotect(keys) {
  if (!keys.length) { notice('Select what to unprotect first.'); return; }
  api('POST', '/api/change-sets', { branch: S.branch, expected_revision: S.rev, request_id: rid('unprotect'), mode: 'apply',
                                    summary: 'unprotect ' + keys.join(', '), operations: [{ type: 'unprotect', keys: keys }] })
    .then(function (r) { if (!r.ok) notice('Not unprotected: ' + (r.error || {}).message, 'bad'); else refreshHead(); });
}

function renderPrompts(b) {
  if (S.openPrompt) return renderThread(b, S.openPrompt);
  if (!S.prompts.length) { b.appendChild(h('div', { cls: 'qcl-note', text: 'No prompts yet.' })); return; }
  S.prompts.forEach(function (p) {
    b.appendChild(h('div', { cls: 'qcl-item', on: { click: function () { openThread(p.prompt_id); } } }, [
      h('div', { text: p.text || '(no text)' }),
      h('div', { cls: 'qcl-row' }, [
        h('span', { cls: 'qcl-chip', text: p.prompt_id + ' v' + p.version }),
        h('span', { cls: 'qcl-chip', text: p.kind === 'comment' ? 'comment' : (p.mode || '') }),
        p.delivery ? h('span', { cls: 'qcl-chip ' + ({ accepted: 'ok', failed: 'bad', uncertain: 'warn' }[p.delivery] || ''), text: 'delivery: ' + p.delivery }) : null,
        p.work ? h('span', { cls: 'qcl-chip ' + ({ ready_for_review: 'ok', cancelled: 'bad', working: 'warn' }[p.work] || ''), text: 'work: ' + p.work }) : null,
        p.replies ? h('span', { cls: 'qcl-chip', text: p.replies + ' repl' + (p.replies > 1 ? 'ies' : 'y') }) : null
      ])
    ]));
  });
}
function renderThread(b, pid) {
  b.appendChild(btn('← all threads', function () { S.openPrompt = null; renderBody(); }));
  var box = h('div', {}, [h('div', { cls: 'qcl-note', text: 'loading ' + pid })]);
  b.appendChild(box);
  api('GET', '/api/prompts/' + encodeURIComponent(pid)).then(function (r) {
    while (box.firstChild) box.removeChild(box.firstChild);
    if (!r.ok) { box.appendChild(h('div', { text: (r.error || {}).message })); return; }
    var p = r.data, sent = p.latest_sent || p.latest;
    box.appendChild(h('div', { cls: 'qcl-label', text: pid + ' · ' + (sent.body.mode || '') + ' · v' + sent.version }));
    p.versions.forEach(function (v) {
      box.appendChild(h('div', { cls: 'qcl-reply' }, [
        h('div', { cls: 'qcl-meta', text: 'v' + v.version + ' · ' + v.status + (v.body.design_revision !== undefined ? ' at r' + v.body.design_revision : '') }),
        h('div', { text: v.body.text || '' }),
        h('div', { cls: 'qcl-row' }, (v.body.anchors || []).map(function (a) {
          return h('span', { cls: 'qcl-chip' + ((a.missing || []).length ? ' warn' : ''), text: anchorText(a) + ((a.missing || []).length ? ' (missing now)' : ''),
                             on: { click: function () { flash((a.keys || (a.key ? [a.key] : [])).concat(a.entities_inside || [])); } } });
        }))
      ]));
    });
    if (p.deliveries.length) {
      var dl = p.deliveries[p.deliveries.length - 1];
      box.appendChild(h('div', { cls: 'qcl-note', text: 'delivery ' + dl.id + ': ' + dl.state + (dl.detail ? ' - ' + dl.detail : '') +
                                                      (dl.last_error ? ' (' + dl.last_error + ')' : '') }));
    }
    box.appendChild(h('div', { cls: 'qcl-note', text: 'work: ' + ((p.work || {}).state || 'none') }));
    p.replies.forEach(function (x) {
      box.appendChild(h('div', { cls: 'qcl-reply ' + (x.author.kind === 'agent' ? 'agent' : '') }, [
        h('div', { cls: 'qcl-meta', text: (x.author.kind === 'agent' ? 'Agent' + (x.author.label ? ' (' + x.author.label + ')' : '') : 'You') +
                                          (x.body.work_state ? ' · ' + x.body.work_state : '') }),
        h('div', { text: x.body.text })
      ]));
    });
    p.links.forEach(function (l) {
      box.appendChild(h('div', { cls: 'qcl-row' }, [h('span', { cls: 'qcl-chip', text: l.kind + ' ' + l.ref }),
        l.kind === 'change_set' ? btn('show', function () {
          api('GET', '/api/change-sets/' + l.ref).then(function (c) { if (c.ok) flash(c.data.touched || []); }); }) : null,
        l.kind === 'submission' ? btn('result', function () { S.tab = 'results'; renderTabs(); loadResults(); }) : null]));
    });
    var reply = h('textarea', { cls: 'qcl-ta', placeholder: 'Reply in this thread (a remark, not a new request)' });
    box.appendChild(reply);
    box.appendChild(h('div', { cls: 'qcl-row' }, [
      btn('Reply', function () {
        if (!reply.value.trim()) return;
        api('POST', '/api/prompts/' + pid + '/reply', { text: reply.value }).then(function () { renderBody(); });
      }),
      btn('Edit & resend', function () { editPrompt(p); }),
      btn('Resolve', function () { api('POST', '/api/prompts/' + pid + '/state', { state: 'resolved' }).then(loadPrompts); }),
      btn('Cancel', function () { api('POST', '/api/prompts/' + pid + '/state', { state: 'cancelled' }).then(loadPrompts); }),
      sent.body.target_session ? btn('Stop agent', function () { stopAgent(sent.body.target_session, pid); }) : null
    ]));
  });
}
function stopAgent(sid, pid) {
  api('POST', '/api/sessions/' + encodeURIComponent(sid) + '/stop', { prompt_id: pid || undefined, reason: 'stopped from Studio' })
    .then(function (r) {
      if (!r.ok) { notice('Stop failed: ' + (r.error || {}).message, 'bad'); return; }
      var i = r.data.interrupt || {};
      notice('Agent writes are stopped. ' + (i.interrupted ? 'The running turn was interrupted.' :
             'Interrupt: ' + (i.message || (i.supported ? 'nothing to interrupt' : 'not supported by this runtime'))) +
             ' Changes already committed stay (undo them in Activity).');
      loadSessions();
    });
}
function renderAgent(b) {
  var fl = h('input', { type: 'checkbox' }); fl.checked = !!S.follow;
  fl.addEventListener('change', function () {
    S.follow = fl.checked;
    api('PATCH', '/api/views/' + encodeURIComponent(S.view), { follow_agent: S.follow });
  });
  b.appendChild(h('label', { cls: 'qcl-row' }, [fl, ' Follow agent (move the view when it presents something)']));
  b.appendChild(h('div', { cls: 'qcl-label', text: 'Agent sessions for this workspace' }));
  if (!S.sessions.length) b.appendChild(h('div', { cls: 'qcl-note', text: 'None. Run `qccd agent connect --client codex` (or start Claude Code with the QCCD channel) in this project.' }));
  S.sessions.forEach(function (s) {
    var isT = s.id === S.target;
    b.appendChild(h('div', { cls: 'qcl-item' }, [
      h('div', { cls: 'qcl-row' }, [
        h('span', { cls: 'qcl-dot ' + (s.status === 'connected' ? (s.write_fence ? 'busy' : 'on') : 'off') }),
        h('b', { text: (s.label || s.client) }),
        h('span', { cls: 'qcl-chip', text: s.client + ' / ' + s.mode }),
        h('span', { cls: 'qcl-chip', text: s.status }),
        s.write_fence ? h('span', { cls: 'qcl-chip bad', text: 'stopped' }) : null
      ]),
      h('div', { cls: 'qcl-note', text: capabilityText(s) }),
      s.runtime_ref ? h('div', { cls: 'qcl-mono', text: 'thread ' + s.runtime_ref }) : null,
      s.bridge && s.bridge.active_turn ? h('div', { cls: 'qcl-note', text: 'turn running: ' + s.bridge.active_turn }) : null,
      h('div', { cls: 'qcl-row' }, [
        isT ? h('span', { cls: 'qcl-chip ok', text: 'prompts from this tab go here' })
            : btn('Send prompts here', function () {
                api('PATCH', '/api/views/' + encodeURIComponent(S.view), { target_session: s.id }).then(loadSessions); }),
        s.write_fence ? btn('Resume', function () { api('POST', '/api/sessions/' + s.id + '/resume', {}).then(loadSessions); })
                      : btn('Stop', function () { stopAgent(s.id); })
      ])
    ]));
  });
  var tid = h('input', { cls: 'qcl-sel', placeholder: 'existing Codex thread id (blank: new thread)' });
  var url = h('input', { cls: 'qcl-sel', placeholder: 'app server ws://127.0.0.1:PORT (blank: start one)' });
  b.appendChild(h('div', { cls: 'qcl-label', text: 'Connect Codex' }));
  b.appendChild(h('div', { cls: 'qcl-row' }, [tid]));
  b.appendChild(h('div', { cls: 'qcl-row' }, [url]));
  b.appendChild(h('div', { cls: 'qcl-row' }, [btn('Connect', function () {
    var body = tid.value.trim() ? { thread_id: tid.value.trim() } : { start_thread: true };
    if (url.value.trim()) body.url = url.value.trim();
    api('POST', '/api/sessions/codex/connect', body).then(function (r) {
      if (!r.ok) { notice('Codex: ' + (r.error || {}).message, 'bad'); return; }
      notice(r.data.note + '. Attach your terminal: ' + r.data.attach);
      loadSessions();
    });
  })]));
}
function renderActivity(b) {
  b.appendChild(h('div', { cls: 'qcl-label', text: 'Change sets (newest first)' }));
  S.history.forEach(function (c) {
    b.appendChild(h('div', { cls: 'qcl-item' }, [
      h('div', { cls: 'qcl-row' }, [h('span', { cls: 'qcl-chip', text: 'r' + c.revision }),
        h('span', { cls: 'qcl-chip ' + ((c.actor || {}).kind === 'agent' ? 'warn' : ''), text: ((c.actor || {}).kind === 'agent' ? 'agent' : 'person') + ((c.actor || {}).label ? ' · ' + c.actor.label : '') }),
        c.status === 'undone' ? h('span', { cls: 'qcl-chip bad', text: 'undone' }) : null,
        c.origin_prompt_id ? h('span', { cls: 'qcl-chip', text: c.origin_prompt_id }) : null]),
      h('div', { text: c.summary || c.diff_summary || '' }),
      h('div', { cls: 'qcl-row' }, [
        btn('Show', function () { flash(c.touched || []); }),
        c.status === 'committed' && !c.undo_of ? btn('Undo this change', function () {
          api('POST', '/api/change-sets/' + c.change_set_id + '/undo', { request_id: rid('undo') }).then(function (r) {
            if (!r.ok) notice('Cannot undo: ' + (r.error || {}).message, 'bad'); else notice('Undone as r' + r.data.revision + '.');
            loadHistory();
          }); }) : null])
    ]));
  });
  if (S.agentMsgs.length) {
    b.appendChild(h('div', { cls: 'qcl-label', text: 'Agent messages (streamed from its session)' }));
    S.agentMsgs.slice().reverse().forEach(function (m) { b.appendChild(h('div', { cls: 'qcl-reply agent', text: m.text })); });
  }
}
function renderResults(b) {
  b.appendChild(h('div', { cls: 'qcl-row' }, [
    btn('Compile', function () { api('POST', '/api/jobs', { kind: 'compile', params: { branch: S.branch }, request_id: rid('compile') }).then(loadResults); },
        '', 'run the real compiler on the task circuit for this revision'),
    btn('Adopt compiled program', adoptCompiled, '', 'make the last successful compile the design\'s final program'),
    btn('Validate (draft)', function () { api('POST', '/api/jobs', { kind: 'evaluate', params: { branch: S.branch, profile: 'draft' }, request_id: rid('val') }).then(loadResults); }),
    btn('Submit locally', function () {
      api('POST', '/api/submissions', { branch: S.branch, profile: 'reference', request_id: rid('sub') }).then(function (r) {
        if (!r.ok) notice('Not submitted: ' + (r.error || {}).message, 'bad'); loadResults(); }); }, 'pri',
        'freeze this revision and grade it with the reference evaluator')
  ]));
  if (S.jobs.length) {
    b.appendChild(h('div', { cls: 'qcl-label', text: 'Jobs' }));
    S.jobs.forEach(function (j) {
      b.appendChild(h('div', { cls: 'qcl-row' }, [h('span', { cls: 'qcl-chip ' + ({ succeeded: 'ok', failed: 'bad', internal_error: 'bad', running: 'warn' }[j.status] || ''), text: j.kind + ' ' + j.status }),
        h('span', { cls: 'qcl-note', text: (j.summary || ((j.progress || {}).message) || '') }),
        (j.status === 'running' || j.status === 'queued') ? btn('Cancel', function () { api('POST', '/api/jobs/' + j.job_id + '/cancel', {}).then(loadResults); }) : null]));
    });
  }
  var bd = S.board;
  if (!bd) return;
  b.appendChild(h('div', { cls: 'qcl-label' }, ['Local leaderboard · ' + bd.task + ' · ranked by ' + bd.rank_by + ' ', h('span', { cls: 'qcl-local', text: 'Local results - not published' })]));
  bd.rows.forEach(function (s) {
    var m = s.metrics || {};
    b.appendChild(h('div', { cls: 'qcl-item' }, [
      h('div', { cls: 'qcl-row' }, [h('span', { cls: 'qcl-chip', text: s.submission_id }), h('span', { cls: 'qcl-chip', text: 'r' + s.revision }),
        h('span', { cls: 'qcl-chip ' + (s.eligible ? 'ok' : (s.status === 'grading' ? 'warn' : 'bad')), text: s.status }),
        s.stale ? h('span', { cls: 'qcl-chip warn', text: 'stale: the design changed since' }) : h('span', { cls: 'qcl-chip ok', text: 'current' })]),
      h('div', { cls: 'qcl-note', text: Object.keys(m).map(function (k) { return k + ' ' + (typeof m[k] === 'number' ? (+m[k]).toPrecision(5) : m[k]); }).join(' · ') }),
      h('div', { cls: 'qcl-row' }, [
        btn('Report', function () { showReport(s.submission_id); }),
        btn('Open run view', function () { window.open('/run/' + encodeURIComponent(s.snapshot_id), '_blank', 'noopener'); }),
        s.eligible ? btn('Review publication', function () { review(s.submission_id); }) : null])
    ]));
  });
}
function adoptCompiled() {
  api('GET', '/api/jobs?limit=30').then(function (r) {
    var j = ((r.data || {}).jobs || []).filter(function (x) { return x.kind === 'compile' && x.status === 'succeeded'; })[0];
    if (!j) { notice('No successful compile yet.', 'bad'); return; }
    api('GET', '/api/jobs/' + j.job_id).then(function (d) {
      var op = ((d.data || {}).result || {}).adopt_with;
      if (!op) { notice('That compile has nothing to adopt.', 'bad'); return; }
      api('POST', '/api/change-sets', { branch: S.branch, expected_revision: S.rev, request_id: rid('adopt'), mode: 'apply',
                                        summary: 'adopt compiled program (' + j.job_id + ')', operations: [op] })
        .then(function (c) { if (!c.ok) notice('Not adopted: ' + (c.error || {}).message, 'bad'); else { notice('Final program adopted at r' + c.data.revision + '.'); refreshHead(); } });
    });
  });
}
function showReport(sub) {
  api('GET', '/api/submissions/' + encodeURIComponent(sub)).then(function (r) {
    if (!r.ok) return;
    var s = r.data, rep = s.report || {};
    var box = h('div', {}, [h('b', { text: 'Report for ' + sub + ' ' }), h('span', { cls: 'qcl-local', text: 'Local result - not published' }),
      h('div', { cls: 'qcl-note', text: 'revision r' + s.snapshot.revision + (s.stale ? ' (stale relative to the editor)' : '') + ' · bundle ' + s.snapshot.bundle_digest }),
      h('div', { cls: 'qcl-note', text: 'evaluator ' + ((rep.evaluator || {}).name || '') + ' ' + ((rep.evaluator || {}).version || '') + ' · profile ' + (rep.profile || '') })]);
    var tb = h('table', { cls: 'qcl-table' }, [h('tr', {}, [h('th', { text: 'stage' }), h('th', { text: 'status' }), h('th', { text: 'coverage' })])]);
    (rep.stages || []).forEach(function (st) {
      tb.appendChild(h('tr', {}, [h('td', { text: st.id + (st.required ? ' *' : '') }), h('td', { text: st.status }), h('td', { text: st.coverage })]));
    });
    box.appendChild(tb);
    box.appendChild(h('div', { cls: 'qcl-label', text: 'Eligibility: ' + ((rep.eligibility || {}).eligible ? 'eligible' : 'not eligible') }));
    ((rep.eligibility || {}).reasons || []).forEach(function (x) { box.appendChild(h('div', { cls: 'qcl-note', text: x })); });
    if ((rep.diagnostics || []).length) {
      box.appendChild(h('div', { cls: 'qcl-label', text: 'Diagnostics' }));
      rep.diagnostics.slice(0, 50).forEach(function (d) {
        box.appendChild(h('div', { cls: 'qcl-item', on: { click: function () {
          if (d.entities) flash(d.entities);
          if (d.instructions && d.instructions.length) window.open('/run/' + encodeURIComponent(s.snapshot.id) + '#instr=' + d.instructions[0], '_blank', 'noopener');
        } } }, [h('div', { cls: 'qcl-mono', text: d.id + ' [' + d.severity + '] ' + (d.instructions ? 'instr ' + d.instructions.join(',') : '') }), h('div', { text: d.message })]));
      });
    }
    box.appendChild(h('div', { cls: 'qcl-row' }, [btn('Open run view', function () { window.open('/run/' + encodeURIComponent(s.snapshot.id), '_blank', 'noopener'); }),
                                                  btn('Close', function () { m.remove(); })]));
    var m = h('div', { cls: 'qcl-modal', on: { click: function (e) { if (e.target === m) m.remove(); } } }, [box]);
    document.body.appendChild(m);
  });
}
function review(sub) {
  var vis = 'public';
  api('POST', '/api/publish/prepare', { submission_id: sub, visibility: vis }).then(function (r) {
    if (!r.ok) { notice((r.error || {}).message, 'bad'); return; }
    var rv = r.data;
    var sel = h('select', { cls: 'qcl-sel' }, [h('option', { value: 'public', text: 'public' }), h('option', { value: 'unlisted', text: 'unlisted' }), h('option', { value: 'private', text: 'private' })]);
    var box = h('div', {}, [h('b', { text: 'Publish exactly this bundle?' }),
      h('div', { cls: 'qcl-note', text: 'task ' + rv.task.id + ' · ' + rv.task.digest }),
      h('div', { cls: 'qcl-mono', text: 'bundle ' + rv.bundle_digest })]);
    rv.files.forEach(function (f) { box.appendChild(h('div', { cls: 'qcl-mono', text: f.path + '  ' + f.bytes + ' B  ' + f.digest })); });
    box.appendChild(h('div', { cls: 'qcl-note', text: 'Excluded: ' + rv.excluded.join(', ') + '. The official server re-grades it independently.' }));
    box.appendChild(h('div', { cls: 'qcl-row' }, ['Visibility ', sel]));
    var out = h('div', { cls: 'qcl-note' });
    box.appendChild(h('div', { cls: 'qcl-row' }, [btn('Approve this digest', function () {
      api('POST', '/api/publish/approve', { submission_id: sub, bundle_digest: rv.bundle_digest, params: { visibility: sel.value } })
        .then(function (a) {
          out.textContent = a.ok ? 'Approved (' + a.data.approval_id + '). Upload it from a terminal: qccd publish --approval ' + a.data.approval_id + ' --server <url>'
                                 : 'Not approved: ' + (a.error || {}).message;
        });
    }, 'pri'), btn('Close', function () { m.remove(); })]));
    box.appendChild(out);
    var m = h('div', { cls: 'qcl-modal' }, [box]);
    document.body.appendChild(m);
  });
}

// ------------------------------------------------------------------ chat (the default view)
//
// One conversation, like talking to a colleague: the person's messages, the agent's own
// messages, a typing indicator while it works, and small cards for what it DID (a change
// with Undo, a run with its time, a side-by-side comparison).  One box to write in; the
// current selection goes with the message; "+" points at things (lasso, point, arrow,
// sketch, show an edit) or locks parts.  Sessions, deliveries, threads, activity and
// results are the debug view (the "..." menu).  If no agent is connected, sending starts
// a Codex conversation when Codex is installed.
function agentSession() {
  return S.sessions.filter(function (x) { return x.id === S.target; })[0] || null;
}
function shortName(n) {
  n = String(n || 'Agent');
  if (/^agent:/.test(n)) return 'Agent';                 // an agent without a named session
  if (/codex/i.test(n)) return 'Codex';
  if (/claude/i.test(n)) return 'Claude';
  return n;
}
function isWorking() { return S.working.some(function (w) { return w.state === 'working'; }); }
function draftName(n) { return n === 'main' ? 'Main design' : 'Draft: ' + String(n).replace(/^cand\//, ''); }

function buildChat() {
  var min = localGet('qccd.live.min') === '1';
  var ta = h('textarea', { id: 'qcl-text', cls: 'qcl-input', rows: '1', placeholder: 'Message the agent…',
                           'aria-label': 'message the agent' });
  ta.value = S.textCache || '';
  ta.addEventListener('input', function () { S.textCache = ta.value; grow(ta); renderSendButton(); });
  ta.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); chatSend(); }
  });
  var dock = h('div', { id: 'qcl-dock', cls: 'qcl-chat' + (min ? ' qcl-min' : '') }, [
    h('div', { cls: 'qcl-head' }, [
      h('span', { id: 'qcl-live', cls: 'qcl-dot' }),
      h('b', { id: 'qcl-who', text: 'Agent' }),
      h('button', { id: 'qcl-model', cls: 'qcl-btn qcl-modelbtn', title: 'the model and how hard it thinks',
                    on: { click: function (e) { e.stopPropagation(); toggleMenu('model'); } } }),
      h('span', { id: 'qcl-sub', cls: 'qcl-sub' }),
      h('span', { cls: 'qcl-sp' }),
      PAGE ? null : h('select', { id: 'qcl-draft', cls: 'qcl-draftsel', title: 'the design you are working on',
                                  'aria-label': 'design', on: { change: onDraftPick } }),
      h('button', { id: 'qcl-more', cls: 'qcl-btn qcl-icon', text: '⋯', title: 'more',
                    on: { click: function (e) { e.stopPropagation(); toggleMenu('more'); } } }),
      h('button', { cls: 'qcl-btn qcl-icon', text: '–', title: 'minimise', on: { click: function () {
        dock.classList.toggle('qcl-min'); localSet('qccd.live.min', dock.classList.contains('qcl-min') ? '1' : '0');
        if (CFG.framed) toParent({ qccd: 'size', min: dock.classList.contains('qcl-min') }); } } })
    ]),
    h('div', { id: 'qcl-conv', cls: 'qcl-conv', 'aria-live': 'polite' }),
    h('div', { cls: 'qcl-composer' }, [
      h('div', { id: 'qcl-chips', cls: 'qcl-chips' }),
      ta,
      h('div', { cls: 'qcl-cbar' }, [
        h('button', { id: 'qcl-plus', cls: 'qcl-btn qcl-icon', text: '+',
                      title: PAGE ? 'point at something on the page' :
                             'point at something (lasso, point, arrow, sketch), show an edit, or lock parts',
                      on: { click: function (e) { e.stopPropagation(); toggleMenu('plus'); } } }),
        h('span', { id: 'qcl-tip', cls: 'qcl-note' }),
        h('span', { cls: 'qcl-sp' }),
        h('button', { id: 'qcl-send', cls: 'qcl-btn pri qcl-send', text: 'Send', on: { click: function () {
          if (isWorking() && !ta.value.trim()) chatStop(); else chatSend(); } } })
      ])
    ])
  ]);
  if (min && CFG.framed) toParent({ qccd: 'size', min: true });
  dock.appendChild(h('div', { cls: 'qcl-grip', title: 'drag to resize' }));
  document.body.appendChild(dock);
  movable(dock);
  renderConv(); renderChips(); renderChatHead(); renderDrafts();
}
// the chat moves out of the way: drag its header; resize it from the corner.  In a website page
// the chat is a frame, and the page moves the frame (mirror.js).
function movable(dock) {
  var head = dock.querySelector('.qcl-head'), grip = dock.querySelector('.qcl-grip');
  if (!CFG.framed) {
    try { var r = JSON.parse(localGet('qccd.dock.rect') || 'null');
          if (r && r.x >= 0 && r.y >= 0 && r.x < innerWidth - 80 && r.y < innerHeight - 40) place(r); } catch (e) { /* none */ }
  }
  function place(r) {
    dock.style.left = r.x + 'px'; dock.style.top = r.y + 'px'; dock.style.right = 'auto'; dock.style.bottom = 'auto';
    if (r.w) dock.style.width = r.w + 'px';
    if (r.h) { dock.style.height = r.h + 'px'; dock.style.maxHeight = 'none'; }
  }
  function drag(el, onDelta) {
    el.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || (e.target.closest && e.target.closest('button, select, input, textarea, a'))) return;
      e.preventDefault();
      var x0 = e.screenX, y0 = e.screenY;
      el.setPointerCapture(e.pointerId);
      function mv(ev) { onDelta(ev.screenX - x0, ev.screenY - y0); x0 = ev.screenX; y0 = ev.screenY; }
      function up() { el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', up);
                      el.removeEventListener('pointercancel', up); if (CFG.framed) toParent({ qccd: 'moved' }); else save(); }
      el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    });
  }
  function save() {
    var r = dock.getBoundingClientRect();
    localSet('qccd.dock.rect', J({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }));
  }
  drag(head, function (dx, dy) {
    if (CFG.framed) { toParent({ qccd: 'move', dx: dx, dy: dy }); return; }
    var r = dock.getBoundingClientRect();
    place({ x: Math.max(0, Math.min(innerWidth - 80, r.left + dx)), y: Math.max(0, Math.min(innerHeight - 40, r.top + dy)) });
  });
  drag(grip, function (dx, dy) {
    if (CFG.framed) { toParent({ qccd: 'resize', dw: dx, dh: dy }); return; }
    var r = dock.getBoundingClientRect();
    place({ x: r.left, y: r.top, w: Math.max(300, r.width + dx), h: Math.max(220, r.height + dy) });
  });
  head.addEventListener('dblclick', function (e) {
    if (e.target.closest && e.target.closest('button, select')) return;
    if (CFG.framed) { toParent({ qccd: 'home' }); return; }
    localSet('qccd.dock.rect', ''); dock.style.cssText = '';
  });
}
document.addEventListener('click', function () { closeMenu(); });
function grow(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; }
function tip(t) { var e = document.getElementById('qcl-tip'); if (e) e.textContent = t || ''; }

function closeMenu() { var m = document.getElementById('qcl-menu'); if (m) m.remove(); }
function toggleMenu(which) {
  var had = document.getElementById('qcl-menu');
  closeMenu();
  if (had && had.getAttribute('data-which') === which) return;
  var s = agentSession();
  if (which === 'model') return modelMenu();
  var items = PAGE ? (which === 'plus' ? [
      ['Point at something on the page', pickOnPage],
      [S.selOff ? 'Send the text you selected on the page with the message' : 'Do not send the selected text',
       function () { S.selOff = !S.selOff; renderChips(); }]
    ] : [
      S.codex ? ['New conversation with Codex', function () { localSet('qccd.agent', 'codex'); connectAgent('codex'); }] : null,
      S.claude ? ['New conversation with Claude', function () { localSet('qccd.agent', 'claude'); connectAgent('claude'); }] : null,
      s ? ['Stop the agent', chatStop] : null,
      null,
      ['Open your Studio (the design you work on)', function () { goTo('/studio'); }],
      ['Trace: what the agent did, step by step', openTrace],
      ['Debug view (sessions, threads, activity, results)', function () { setDebug(true); }]
    ]) : which === 'plus' ? [
      ['Lasso a region', function () { setTool('lasso'); }],
      ['Point at a place', function () { setTool('point'); }],
      ['Draw an arrow', function () { setTool('arrow'); }],
      ['Sketch freehand', function () { setTool('stroke'); }],
      [!S.demo ? 'Show an edit (make it by hand, then ask)' : (S.demo.after === undefined ? 'Done showing the edit'
                                                                                          : 'Forget the shown edit'), demoToggle],
      null,
      ['Lock the selected parts (nobody may change them)', function () { protect(selectionKeys()); }],
      ['Unlock the selected parts', function () { unprotect(selectionKeys()); }]
    ] : [
      [(S.follow ? '✓ ' : '') + 'Follow the agent (move the view when it shows something)', function () {
        QCCD_LIVE.setFollow(!S.follow); }],
      S.codex ? ['New conversation with Codex', function () { localSet('qccd.agent', 'codex'); connectAgent('codex'); }] : null,
      S.claude ? ['New conversation with Claude', function () { localSet('qccd.agent', 'claude'); connectAgent('claude'); }] : null,
      s ? ['Stop the agent', chatStop] : null,
      null,
      CFG.web_url ? ['Open the website with this agent', function () { window.open(CFG.web_url, '_blank', 'noopener'); }] : null,
      CFG.mode === 'run' || CFG.mode === 'compare' ? ['Back to your Studio (the design)', function () { goTo('/studio'); }] : null,
      ['Trace: what the agent did, step by step', openTrace],
      ['Debug view (sessions, threads, activity, results)', function () { setDebug(true); }]
    ];
  var m = h('div', { id: 'qcl-menu', cls: 'qcl-menu ' + (which === 'plus' ? 'up' : 'down'), 'data-which': which,
                     on: { click: function (e) { e.stopPropagation(); } } });
  items.forEach(function (it) {
    if (it === null) { m.appendChild(h('div', { cls: 'qcl-sep' })); return; }
    if (!it) return;
    m.appendChild(h('button', { cls: 'qcl-mi', text: it[0], on: { click: function () { closeMenu(); it[1](); } } }));
  });
  document.getElementById('qcl-dock').appendChild(m);
}
function demoToggle() {
  if (!S.demo) { S.demo = { before: S.rev }; tip('Make the edit by hand now, then choose "Done showing the edit" under +.'); }
  else if (S.demo.after === undefined) { S.demo.after = S.rev; S.demo.targets = selectionKeys(); tip(''); }
  else { S.demo = null; tip(''); }
  renderChips();
}

// what goes with the next message
function renderChips() {
  var box = document.getElementById('qcl-chips');
  if (!box) return;
  while (box.firstChild) box.removeChild(box.firstChild);
  var sel = selectionKeys();
  S.lastSel = sel.join(',');
  var quote = PAGE && S.page && S.page.selection;
  if (quote && !S.selOff) {
    box.appendChild(chipX('“' + (quote.length > 60 ? quote.slice(0, 58) + '…' : quote) + '”',
                          function () { S.selOff = true; renderChips(); }, 'the text you selected on the page goes with your message'));
  }
  if (sel.length && !S.selOff) {
    box.appendChild(chipX(sel.length === 1 ? sel[0].replace(/^[a-z]+:/, '') + ' (selected)' : sel.length + ' selected parts',
                          function () { S.selOff = true; renderChips(); }, 'the current selection goes with your message'));
  }
  S.anchors.forEach(function (a, i) {
    box.appendChild(chipX(anchorText(a), function () { S.anchors.splice(i, 1); paintOverlay(); renderChips(); }));
  });
  S.sketches.forEach(function (sk, i) {
    box.appendChild(chipX(sk.kind === 'stroke' ? 'sketch' : sk.kind, function () { S.sketches.splice(i, 1); paintOverlay(); renderChips(); }));
  });
  if (S.demo) {
    box.appendChild(chipX(S.demo.after === undefined ? 'showing an edit: make it now' : 'edit shown: r' + S.demo.before + ' → r' + S.demo.after,
                          function () { S.demo = null; renderChips(); }));
  }
  if (S.tool) tip({ lasso: 'Draw around a region on the canvas. Esc cancels.', point: 'Click a place on the canvas.',
                    arrow: 'Drag an arrow on the canvas.', stroke: 'Sketch on the canvas.', pick: 'Click anything.' }[S.tool] || '');
  else if (!S.demo || S.demo.after !== undefined) tip('');
  box.style.display = box.children.length ? '' : 'none';
}
function chipX(text, onX, title) {
  return h('span', { cls: 'qcl-chip', title: title || '' }, [text,
    h('button', { cls: 'qcl-x', text: '×', title: 'remove', on: { click: onX } })]);
}

// sending, starting an agent, stopping it
function chatSend(textArg) {
  var ta = document.getElementById('qcl-text');
  var text = String(textArg !== undefined ? textArg : (ta ? ta.value : '')).trim();
  if (!text) return Promise.resolve(null);
  var sel = selectionKeys(), anchors = S.anchors.slice();
  if (sel.length && !S.selOff) anchors.unshift(sel.length === 1 ? { kind: 'entity', key: sel[0] } : { kind: 'entity_group', keys: sel });
  if (PAGE && S.page && S.page.selection && !S.selOff) anchors.unshift({ kind: 'page', url: S.page.url, quote: S.page.selection });
  var wasWorking = isWorking();
  S.pending = text;
  if (ta) { ta.value = ''; grow(ta); }
  S.textCache = '';
  renderConv(); renderSendButton();
  return (PAGE ? refreshPage() : Promise.resolve()).then(ensureAgent).then(function () {
    var s = agentSession();
    if (s && s.write_fence) return api('POST', '/api/sessions/' + encodeURIComponent(s.id) + '/resume', {}).then(loadSessions);
  }).then(function () {
    var s = agentSession();
    S.steer = !!(wasWorking && s && s.mode === 'appserver' && s.client === 'codex');   // a Claude run takes the next message after it
    return send({ text: text, mode: 'apply', anchors: anchors });
  }).then(function (d) {
    if (!d) { S.pending = null; if (ta) { ta.value = text; grow(ta); } renderConv(); return null; }
    S.selOff = false; renderChips();
    return loadConv().then(function () { return d; });
  });
}
function ensureAgent() {
  var s = agentSession();
  if (s && (s.status === 'connected' || s.mode === 'pull' || s.mode === 'channel')) return Promise.resolve(s);
  var live = S.sessions.filter(function (x) { return x.status === 'connected'; })[0];
  if (live) {
    return api('PATCH', '/api/views/' + encodeURIComponent(S.view), { target_session: live.id }).then(loadSessions);
  }
  return (S.codex === null ? loadAgents() : Promise.resolve())
    .then(function () { var k = agentChoice(); return k ? connectAgent(k) : null; });
}
function loadAgents() {
  return api('GET', '/api/agents').then(function (r) {
    S.codex = !!(r.ok && r.data.codex_available); S.claude = !!(r.ok && r.data.claude_available);
  });
}
// which agent the chat starts: the one the person last chose, else whichever is installed
function agentChoice() {
  var pref = localGet('qccd.agent');
  if (pref === 'claude' && S.claude) return 'claude';
  if (pref === 'codex' && S.codex) return 'codex';
  return S.codex ? 'codex' : (S.claude ? 'claude' : null);
}
function agentTitle(k) { return k === 'claude' ? 'Claude' : 'Codex'; }
// which agent the chat is about: the connected one, else the one it will start
function agentKind() { var s = agentSession(); return s ? (s.client === 'claude' ? 'claude' : 'codex') : agentChoice(); }
// the trace viewer (trace.js), at this conversation's latest request
function openTrace() {
  var s = agentSession();
  window.open(location.origin + '/trace' + (s ? '?session=' + encodeURIComponent(s.id) : ''), '_blank', 'noopener');
}
function modelPref(kind) { try { return JSON.parse(localGet('qccd.model.' + kind) || '{}') || {}; } catch (e) { return {}; } }
function modelOf(kind) {
  var s = agentSession();
  var set = s && s.client === kind ? ((s.capabilities || {}).settings || {}) : {};
  var p = modelPref(kind);
  return { model: set.model !== undefined ? set.model : (p.model || ''), effort: set.effort !== undefined ? set.effort : (p.effort || '') };
}
function modelLabel(kind) {
  var m = modelOf(kind), cat = (S.models || {})[kind] || {}, name = '';
  (cat.models || []).forEach(function (x) { if (x.id === m.model) name = x.label; });
  return (name || (m.model ? m.model : 'default model')) + (m.effort ? ' · ' + m.effort : '');
}
function setModel(kind, change) {
  var cur = modelOf(kind), next = { model: change.model !== undefined ? change.model : cur.model,
                                    effort: change.effort !== undefined ? change.effort : cur.effort };
  localSet('qccd.model.' + kind, J(next));
  var s = agentSession();
  var done = s && s.client === kind
    ? api('POST', '/api/sessions/' + encodeURIComponent(s.id) + '/settings', next).then(function (r) {
        if (!r.ok) notice('Not changed: ' + ((r.error || {}).message || 'error'), 'bad'); return loadSessions(); })
    : Promise.resolve();
  return done.then(renderChatHead);
}
function modelMenu() {
  var kind = agentKind();
  if (!kind) { notice('No agent is installed: install Codex or Claude Code first.'); return; }
  var go = function () {
    var cat = (S.models || {})[kind] || {}, cur = modelOf(kind);
    var m = h('div', { id: 'qcl-menu', cls: 'qcl-menu down', 'data-which': 'model', on: { click: function (e) { e.stopPropagation(); } } });
    m.appendChild(h('div', { cls: 'qcl-mh', text: agentTitle(kind) + ': model' }));
    (cat.models || []).forEach(function (x) {
      m.appendChild(h('button', { cls: 'qcl-mi', text: (x.id === cur.model ? '✓ ' : '') + x.label + (x.note ? ' — ' + x.note : ''),
                                  on: { click: function () { closeMenu(); setModel(kind, { model: x.id }); } } }));
    });
    var efforts = cat.efforts || [];
    (cat.models || []).forEach(function (x) { if (x.id === cur.model && x.efforts) efforts = x.efforts; });
    if (efforts.length) {
      m.appendChild(h('div', { cls: 'qcl-sep' }));
      m.appendChild(h('div', { cls: 'qcl-mh', text: 'Thinking' }));
      [''].concat(efforts).forEach(function (e) {
        m.appendChild(h('button', { cls: 'qcl-mi', text: (e === cur.effort ? '✓ ' : '') + (e || 'the model\'s default'),
                                    on: { click: function () { closeMenu(); setModel(kind, { effort: e }); } } }));
      });
    }
    m.appendChild(h('div', { cls: 'qcl-note', text: 'Applies from your next message.' }));
    document.getElementById('qcl-dock').appendChild(m);
  };
  if (S.models && S.models[kind]) go();
  else api('GET', '/api/agents/models').then(function (r) { if (r.ok) S.models = r.data; go(); });
}
function connectCodex() { return connectAgent('codex'); }
function connectAgent(kind) {
  S.connecting = kind; renderChatHead(); renderConv();
  // nobody can answer an approval prompt from Studio: neither agent is ever asked -- Codex runs
  // with approval `never` and a read-only shell, Claude with `dontAsk` and no shell -- and each
  // changes the design only through the QCCD tools
  var pick = modelPref(kind);
  var req = kind === 'claude' ? api('POST', '/api/sessions/claude/connect', { label: 'Claude', model: pick.model || undefined,
                                                                             effort: pick.effort || undefined })
    : api('POST', '/api/sessions/codex/connect', { start_thread: true, label: 'Codex', approval_policy: 'never',
                                                   sandbox: 'read-only',
                                                   turn_overrides: { model: pick.model || undefined, effort: pick.effort || undefined } });
  return req.then(function (r) {
    S.connecting = false;
    if (!r.ok) { notice('Could not start ' + agentTitle(kind) + ': ' + ((r.error || {}).message || 'error'), 'bad'); renderChatHead(); return null; }
    return api('PATCH', '/api/views/' + encodeURIComponent(S.view), { target_session: r.data.session.id }).then(loadSessions);
  });
}
function chatStop() {
  var s = agentSession();
  if (!s) return;
  var w = S.working.filter(function (x) { return x.state === 'working'; })[0];
  stopAgent(s.id, w && w.prompt_id);
}
function renderSendButton() {
  var b = document.getElementById('qcl-send'), ta = document.getElementById('qcl-text');
  if (!b || !ta) return;
  var stop = isWorking() && !ta.value.trim();
  b.textContent = stop ? 'Stop' : 'Send';
  b.className = 'qcl-btn qcl-send ' + (stop ? 'stop' : 'pri');
}
function renderChatHead() {
  var s = agentSession(), who = document.getElementById('qcl-who'), sub = document.getElementById('qcl-sub'),
      dot = document.getElementById('qcl-live');
  if (!who) return;
  who.textContent = s ? shortName(s.label || s.client) : 'Agent';
  var mb = document.getElementById('qcl-model'), kind = agentKind();
  if (mb) { mb.textContent = kind ? modelLabel(kind) + ' ▾' : ''; mb.style.display = kind ? '' : 'none'; }
  // a chosen model is named as its agent names it (the list comes once, on demand)
  if (kind && modelOf(kind).model && !(S.models && S.models[kind]) && !S.modelsAsked) {
    S.modelsAsked = true;
    api('GET', '/api/agents/models').then(function (r) { if (r.ok) { S.models = r.data; renderChatHead(); } });
  }
  var t, cls;
  if (!S.connected) { t = 'offline: reconnecting'; cls = 'off'; }
  else if (S.connecting) { t = 'starting ' + agentTitle(S.connecting) + '…'; cls = 'busy'; }
  else if (!s) { t = agentChoice() ? agentTitle(agentChoice()) + ' starts when you send' : 'no agent connected'; cls = ''; }
  else if (isWorking()) { t = 'working…'; cls = 'busy'; }
  else if (s.write_fence) { t = 'stopped'; cls = 'off'; }
  else if (s.mode === 'pull') { t = 'checks when you ask it'; cls = 'on'; }
  else if (s.status === 'connected') { t = 'ready'; cls = 'on'; }
  else { t = s.status; cls = 'off'; }
  sub.textContent = t;
  dot.className = 'qcl-dot ' + cls;
  renderSendButton();
}

// drafts: the workspace's branches
function loadBranches() {
  return api('GET', '/api/branches').then(function (r) {
    if (!r.ok) return;
    S.branches = (r.data.branches || []).filter(function (b) { return b.name === 'main' || (b.status || 'open') === 'open'; });
    renderDrafts();
  });
}
function renderDrafts() {
  var sel = document.getElementById('qcl-draft');
  if (!sel) return;
  while (sel.firstChild) sel.removeChild(sel.firstChild);
  var names = S.branches.map(function (b) { return b.name; });
  if (names.indexOf('main') < 0) names.unshift('main');
  if (names.indexOf(S.branch) < 0) names.push(S.branch);
  names.forEach(function (n) { sel.appendChild(h('option', { value: n, text: draftName(n) })); });
  sel.appendChild(h('option', { value: '__save', text: 'Save as a new draft…' }));
  sel.value = S.branch;
}
function onDraftPick(e) {
  var v = e.target.value;
  if (v === '__save') { e.target.value = S.branch; saveAsDraft(); return; }
  switchDraft(v);
}
function saveAsDraft(name) {
  name = name || window.prompt('Name this draft (letters, digits, - and _):', '');
  if (!name || !String(name).trim()) return Promise.resolve(null);
  name = String(name).trim();
  return api('POST', '/api/branches', { name: name, source: S.branch }).then(function (r) {
    if (!r.ok) { notice('Not saved: ' + ((r.error || {}).message || 'error'), 'bad'); return null; }
    notice('Saved as draft "' + name + '". You are still on the ' + draftName(S.branch).toLowerCase() + '.');
    return loadBranches().then(function () { return r.data; });
  });
}
function switchDraft(name) {
  if (!name || name === S.branch) return Promise.resolve(true);
  var t0 = Date.now();
  return new Promise(function (resolve) {
    (function waitSync() {                              // finish saving the page's edits to the old draft first
      if (S.inflight && Date.now() - t0 < 5000) { setTimeout(waitSync, 100); return; }
      sync();
      S.branch = name; S.rev = null; S.synced = null;
      api('PATCH', '/api/views/' + encodeURIComponent(S.view), { branch: name }).then(function () {
        return loadHead();
      }).then(function () { loadHistory(); renderDrafts(); renderChatHead(); resolve(true); });
    })();
  });
}

// the conversation
function loadConvSoon() {
  if (S.convTimer) clearTimeout(S.convTimer);
  S.convTimer = setTimeout(loadConv, 200);
}
function loadConv() {
  return api('GET', '/api/conversation?limit=40').then(function (r) {
    if (!r.ok) return;
    S.conv = r.data.items || [];
    S.working = r.data.working || [];
    S.pending = null;
    renderConv(); renderChatHead();
  });
}
function renderConv() {
  var box = document.getElementById('qcl-conv');
  if (!box) return;
  var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  while (box.firstChild) box.removeChild(box.firstChild);
  if (!S.conv.length && !S.pending && !S.connecting) { box.appendChild(emptyState()); return; }
  var lastWho = null, runs = {};
  S.conv.forEach(function (it) { if (it.type === 'run') runs[it.run_id] = true; });
  S.conv.forEach(function (it) {
    if (it.type === 'run_view' && runs[it.run_id]) return;
    var n = convItem(it, lastWho);
    if (n) box.appendChild(n);
    if (it.type === 'agent') lastWho = shortName(it.author);
    else if (it.type === 'user' || it.type === 'person') lastWho = 'you';
  });
  if (S.pending) box.appendChild(h('div', { cls: 'qcl-msg you pending' }, [h('div', { cls: 'qcl-bubble' }, [txt(S.pending)])]));
  if (isWorking() || S.pending || S.connecting) {
    box.appendChild(h('div', { cls: 'qcl-typing' }, [h('i'), h('i'), h('i'), h('em', { text: ' ' + workingText() })]));
  }
  if (atBottom || !S.convScrolled) box.scrollTop = box.scrollHeight;
  S.convScrolled = true;
}
function workingText() {
  var run = S.conv.filter(function (i) { return i.type === 'run' && (i.status === 'running' || i.status === 'queued'); }).pop();
  var s = agentSession();
  if (S.connecting) return 'starting ' + agentTitle(S.connecting);
  if (S.pageDoing && Date.now() - S.pageDoing.at < 4000) return S.pageDoing.text;
  if (run) return run.progress || ('running ' + (run.program || 'a program'));
  if (!s) return isWorking() ? 'the agent is working' : (agentChoice() ? 'starting ' + agentTitle(agentChoice()) : 'waiting for an agent to connect');
  if (s.mode === 'pull') return 'waiting until you ask ' + shortName(s.label || s.client) + ' to check';
  return shortName(s.label || s.client) + ' is working';
}
function emptyState() {
  if (PAGE) return pageEmptyState();
  var ex = ['Compile and run the BB code on this design, and tell me the bottleneck',
            'Save this design as a draft called A',
            'Run the BB code on the main design and on draft A, and show them side by side'];
  return h('div', { cls: 'qcl-empty' }, [
    h('div', { cls: 'qcl-empty-t', text: 'Ask the agent about this design.' }),
    h('div', { cls: 'qcl-note', text: 'Select parts on the canvas and they go with your message. The agent can change the ' +
                                      'design, compile and run programs on it, and compare drafts.' })
  ].concat(ex.map(function (t) {
    return h('button', { cls: 'qcl-ex', text: t, on: { click: function () {
      var ta = document.getElementById('qcl-text'); ta.value = t; S.textCache = t; grow(ta); ta.focus(); renderSendButton(); } } });
  })));
}
function convItem(it, lastWho) {
  if (it.type === 'user' || it.type === 'person') {
    var extra = [it.context ? 'with ' + it.context : '', it.branch && it.branch !== 'main' ? 'on ' + draftName(it.branch).toLowerCase() : '']
      .filter(Boolean).join(' · ');
    if (it.page && (!PAGE || it.page !== (S.page || {}).title)) extra = [extra, 'on ' + it.page].filter(Boolean).join(' · ');
    return h('div', { cls: 'qcl-msg you' }, [h('div', { cls: 'qcl-bubble' }, [txt(it.text)]),
                                            extra ? h('div', { cls: 'qcl-ctx', text: extra }) : null]);
  }
  if (it.type === 'agent') {
    var name = shortName(it.author);
    return h('div', { cls: 'qcl-msg agent' }, [lastWho === name ? null : h('div', { cls: 'qcl-who', text: name }),
                                              h('div', { cls: 'qcl-bubble' }, md(it.text))]);
  }
  if (it.type === 'change') {
    var undone = it.status === 'undone';
    return h('div', { cls: 'qcl-event' }, [
      h('span', { cls: 'qcl-evtxt', text: (undone ? 'Undone: ' : 'Changed the design') +
        (it.revision !== undefined && it.revision !== null ? ' (r' + it.revision + ')' : '') +
        (it.branch && it.branch !== 'main' ? ' on ' + draftName(it.branch).toLowerCase() : '') + (it.summary ? ': ' + it.summary : '') }),
      btn('Show', function () { showChange(it.change_set_id); }, 'qcl-link'),
      !undone ? btn('Undo', function () { undoChange(it.change_set_id); }, 'qcl-link') : null]);
  }
  if (it.type === 'run') {
    var ok = it.status === 'succeeded', busy = it.status === 'running' || it.status === 'queued';
    return h('div', { cls: 'qcl-card' }, [
      h('div', { cls: 'qcl-card-t', text: (it.program || 'program') + (it.draft ? ' on ' + (it.draft === 'main' ? 'the main design' : 'draft ' + it.draft) : '') }),
      h('div', { cls: 'qcl-card-s', text: busy ? ((it.progress || 'running') + '…')
                                             : (ok ? (it.total_ms !== null && it.total_ms !== undefined ? it.total_ms + ' ms per round' : 'done')
                                                   : 'did not run: ' + (it.summary || it.status)) }),
      ok && it.view ? btn('Watch it run', function () { window.open(it.view, '_blank', 'noopener'); }, 'qcl-link') : null]);
  }
  if (it.type === 'compare') {
    return h('div', { cls: 'qcl-card cmp' }, [h('div', { cls: 'qcl-card-t', text: 'Side by side' }),
      it.verdict ? h('div', { cls: 'qcl-card-s', text: it.verdict }) : null,
      btn('Open side by side', function () { window.open(it.view, '_blank', 'noopener'); }, 'pri')]);
  }
  if (it.type === 'run_view') {
    return h('div', { cls: 'qcl-event' }, [h('span', { cls: 'qcl-evtxt', text: 'A run to watch' }),
      btn('Open', function () { window.open(it.view, '_blank', 'noopener'); }, 'qcl-link')]);
  }
  if (it.type === 'site_comment') {
    // on the site, as the person: open the page at that thread (the comment layer scrolls to ?c=)
    var where = String(it.page || '/'), url = (CFG.framed && CFG.parent_origin ? CFG.parent_origin + '/web' :
                (CFG.web_url ? CFG.web_url.replace(/\/web\/$/, '/web') : '/web')) + where + '?c=' + encodeURIComponent(it.thread);
    return h('div', { cls: 'qcl-card' }, [
      h('div', { cls: 'qcl-card-t', text: (it.kind === 'reply' ? 'Replied' : 'Commented') + ' as ' + (it.as || 'you') + ' on ' + where }),
      h('div', { cls: 'qcl-card-s', text: String(it.text || '').replace(/\n\n\u2014 via .*$/, '').slice(0, 220) }),
      h('a', { cls: 'qcl-btn qcl-link', href: url, target: CFG.framed ? '_top' : '_blank', rel: 'noopener', text: 'Show on the page' })]);
  }
  if (it.type === 'notice') {
    return h('div', { cls: 'qcl-event qcl-warn' }, [h('span', { cls: 'qcl-evtxt', text: it.text })]);
  }
  if (it.type === 'job') {
    return h('div', { cls: 'qcl-event' }, [h('span', { cls: 'qcl-evtxt', text: (it.kind === 'compile' ? 'Compiled' : it.kind) + ': ' + (it.summary || it.status) })]);
  }
  return null;
}
function showChange(csid) {
  api('GET', '/api/change-sets/' + encodeURIComponent(csid)).then(function (c) {
    if (c.ok) { reveal(c.data.touched || []); flash(c.data.touched || []); }
  });
}
function undoChange(csid) {
  api('POST', '/api/change-sets/' + encodeURIComponent(csid) + '/undo', { request_id: rid('undo') }).then(function (r) {
    if (!r.ok) notice('Cannot undo: ' + ((r.error || {}).message || 'error'), 'bad');
    loadConv();
  });
}
// agent text: a little markdown (bold, code, links, lists, headings, tables), always as
// DOM nodes built from text -- never innerHTML
function md(text) {
  var out = [], list = null, table = null, code = null;
  String(text || '').split('\n').forEach(function (ln) {
    if (/^\s*```/.test(ln)) {                       // a fenced code block, verbatim
      if (code) { code = null; return; }
      code = h('code', {}); out.push(h('pre', { cls: 'qcl-code' }, [code])); list = null; table = null;
      return;
    }
    if (code) { code.appendChild(document.createTextNode((code.firstChild ? '\n' : '') + ln)); return; }
    var quote = /^\s*>\s?(.*)$/.exec(ln);
    if (quote) { list = null; table = null; out.push(h('div', { cls: 'qcl-quote' }, inline(quote[1]))); return; }
    if (/^\s*\|.*\|\s*$/.test(ln)) {
      var cells = ln.trim().slice(1, -1).split('|').map(function (c) { return c.trim(); });
      list = null;
      if (cells.every(function (c) { return /^:?-{2,}:?$/.test(c); })) {       // the header rule
        if (table) table.setAttribute('data-head', '1');
        return;
      }
      if (!table) { table = h('table', { cls: 'qcl-mdt' }); out.push(table); }
      var head = !table.firstChild;
      table.appendChild(h('tr', {}, cells.map(function (c) { return h(head ? 'th' : 'td', {}, inline(c)); })));
      return;
    }
    table = null;
    var ul = /^\s*[-*•]\s+(.*)$/.exec(ln), ol = /^\s*\d+[.)]\s+(.*)$/.exec(ln);
    if (ul || ol) {
      if (!list || list.tagName.toLowerCase() !== (ul ? 'ul' : 'ol')) { list = h(ul ? 'ul' : 'ol', {}); out.push(list); }
      list.appendChild(h('li', {}, inline((ul || ol)[1])));
      return;
    }
    list = null;
    if (!ln.trim()) { out.push(h('div', { cls: 'qcl-gap' })); return; }
    var hd = /^#{1,4}\s+(.*)$/.exec(ln);
    out.push(h('div', { cls: hd ? 'qcl-h' : '' }, inline(hd ? hd[1] : ln)));
  });
  return out;
}
function inline(s) {
  var parts = [], re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*)/g, last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) parts.push(document.createTextNode(s.slice(last, m.index)));
    var t = m[0];
    if (t[0] === '[') {
      var lk = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t);
      // only this site's own paths or web links become links; a website path opens in the
      // page this chat sits in
      if (CFG.framed && CFG.parent_origin && /^\/web(\/|$)/.test(lk[2])) parts.push(h('a', { href: CFG.parent_origin + lk[2], target: '_top', text: lk[1] }));
      else parts.push(/^(\/[^\/]|https?:\/\/)/.test(lk[2])
        ? h('a', { href: lk[2], target: '_blank', rel: 'noopener', text: lk[1] }) : document.createTextNode(lk[1]));
    } else if (t[0] === '`') parts.push(h('code', { text: t.slice(1, -1) }));
    else if (t.slice(0, 2) === '**') parts.push(h('b', { text: t.slice(2, -2) }));
    else parts.push(h('i', { text: t.slice(1, -1) }));
    last = m.index + t.length;
  }
  if (last < s.length) parts.push(document.createTextNode(s.slice(last)));
  return parts;
}
function txt(s) {
  var d = h('div', {});
  String(s || '').split('\n').forEach(function (l, i) { if (i) d.appendChild(h('br')); d.appendChild(document.createTextNode(l)); });
  return d;
}
function setDebug(on) {
  S.debug = !!on;
  localSet('qccd.live.debug', S.debug ? '1' : '0');
  closeMenu();
  var d = document.getElementById('qcl-dock');
  if (d) d.remove();
  buildDock();
  refreshAll();
  if (!S.debug) { loadConv(); loadBranches(); }
}

// ------------------------------------------------------------------ pages: the agent's hands on the UI
//
// Studio runs the agent's page actions itself (window.QCCD_PAGE from pageact.js).  A website
// page's chat is a frame: it asks the page, which is another origin, by postMessage, and
// only its own parent window at the configured origin can answer.
var parentCalls = Object.create(null);
function toParent(msg) { if (CFG.framed && CFG.parent_origin && window.parent !== window) window.parent.postMessage(msg, CFG.parent_origin); }
function askParent(kind, extra, ms) {
  if (!CFG.framed || !CFG.parent_origin) return Promise.resolve({ ok: false, error: 'no page around this chat' });
  var id = rid('m');
  return new Promise(function (resolve) {
    var t = setTimeout(function () { delete parentCalls[id]; resolve({ ok: false, error: 'the page did not answer' }); }, ms || 15000);
    parentCalls[id] = function (m) { clearTimeout(t); resolve(m); };
    var msg = { qccd: kind, id: id };
    for (var k in (extra || {})) msg[k] = extra[k];
    toParent(msg);
  });
}
function listenToPage() {
  window.addEventListener('message', function (e) {
    if (e.source !== window.parent || e.origin !== CFG.parent_origin) return;
    var m = e.data || {};
    if (m.qccd === 'result' && parentCalls[m.id]) { var f = parentCalls[m.id]; delete parentCalls[m.id]; f(m); }
    else if (m.qccd === 'page' && m.context) { setPage(m.context); }
  });
}
function setPage(ctx) {
  var before = J(S.page || {});
  S.page = { url: ctx.url || (S.page || {}).url, title: ctx.title, kind: ctx.kind, selection: ctx.selection,
             in_view: ctx.in_view, site: CFG.site };
  if (J(S.page) !== before) { if (!ctx.selection) S.selOff = false; S.viewDirty = true; renderChips(); }
}
function refreshPage() {
  return askParent('context', null, 1500).then(function (m) { if (m && m.ok && m.result) setPage(m.result); });
}
function pageInfo() {
  if (PAGE) return S.page;
  if (CFG.mode === 'run' && CFG.run_id) return { url: location.pathname, title: document.title, kind: 'run' };
  if (CFG.mode === 'compare') return { url: location.pathname + location.search, title: document.title, kind: 'comparison' };
  if (CFG.mode !== 'design') return null;
  return { url: location.pathname, title: document.title, kind: 'studio' };
}
// go to one of the workspace's own pages in the tab the person is looking at; from a website
// page (this chat is a frame) the page itself goes, when asked by this frame
function goTo(path) {
  if (CFG.framed) toParent({ qccd: 'navigate', url: location.origin + path });
  else location.assign(path);
}

// ------------------------------------------------------------------ the agent's work, shown where it happens
// a design change: the cursor at the changed parts, saying what changed
function showAgentWork(keys, who, caption) {
  if (!window.QCCD_PAGE || !window.QCCD_PAGE.point) return;
  var svg = document.getElementById('svg'), xs = [], ys = [];
  if (!svg || !svg.getScreenCTM) return;
  (keys || []).slice(0, 200).forEach(function (k) {
    var q = keyPos(k); if (!q) return;
    var u = toUser(q.x, q.y), pt = svg.createSVGPoint(); pt.x = u.x; pt.y = u.y;
    var s = pt.matrixTransform(svg.getScreenCTM()); xs.push(s.x); ys.push(s.y);
  });
  var r = svg.getBoundingClientRect();
  var x = xs.length ? xs.reduce(function (a, b) { return a + b; }) / xs.length : r.left + r.width / 2;
  var y = ys.length ? ys.reduce(function (a, b) { return a + b; }) / ys.length : r.top + r.height / 2;
  window.QCCD_PAGE.point(x, y, who, String(caption || '').slice(0, 90));
}
// a run: the program appears while it compiles, then the run's own page opens
var RUNS = Object.create(null);
function trackRun(p) {
  var jid = p.job_id, known = RUNS[jid];
  if (known === 'other') return;
  if (!known) {
    RUNS[jid] = 'asking';
    api('GET', '/api/runs/' + encodeURIComponent(jid) + '/program').then(function (r) {
      if (!r.ok) { RUNS[jid] = 'other'; return; }
      RUNS[jid] = r.data;
      runPanel(jid, p);
    });
    return;
  }
  if (known !== 'asking') runPanel(jid, p);
}
function runPanel(jid, p) {
  var info = RUNS[jid];
  if (CFG.framed || CFG.mode === 'run' || CFG.mode === 'compare') {
    // the chat's run card shows the progress here; a finished run still opens in this tab
    if (p.status === 'succeeded' && S.follow && document.visibilityState === 'visible')
      setTimeout(function () { goTo('/runview/' + encodeURIComponent(jid)); }, 1500);
    return;
  }
  var box = document.getElementById('qcl-runpanel');
  if (!box || box.getAttribute('data-run') !== jid) {
    if (box) box.remove();
    var qasm = String(info.qasm || '').split('\n');
    box = h('div', { id: 'qcl-runpanel', 'data-run': jid, 'data-qccd-private': '' }, [
      h('div', { cls: 'qcl-rp-t', text: (info.agent ? shortName(info.agent) + ' is running ' : 'Running ') + info.name +
                                         ' on ' + (info.draft === 'main' ? 'the main design' : 'draft ' + info.draft) }),
      h('div', { id: 'qcl-rp-s', cls: 'qcl-rp-s', text: 'loading the program' }),
      h('div', { cls: 'qcl-rp-l', text: 'the program (' + qasm.length + ' lines of OpenQASM)' }),
      h('pre', { cls: 'qcl-rp-q', text: qasm.slice(0, 80).join('\n') + (qasm.length > 80 ? '\n…' : '') }),
      h('div', { cls: 'qcl-row' }, [btn('Hide', function () { box.remove(); }, 'qcl-link')])
    ]);
    document.body.appendChild(box);
  }
  var st = document.getElementById('qcl-rp-s');
  if (p.status === 'running' || p.status === 'queued') { if (p.message) st.textContent = p.message + '…'; return; }
  if (p.status === 'succeeded') {
    st.textContent = 'compiled and replayed: opening the run so you can watch it';
    box.classList.add('done');
    if (S.follow && document.visibilityState === 'visible') setTimeout(function () { goTo('/runview/' + encodeURIComponent(jid)); }, 1500);
    else box.appendChild(btn('Watch it run', function () { goTo('/runview/' + encodeURIComponent(jid)); }, 'pri'));
    return;
  }
  if (p.status) { st.textContent = 'the run ' + p.status + ' (the chat says why)'; box.classList.add('bad');
                  setTimeout(function () { if (box.parentNode) box.remove(); }, 20000); }
}
function pickOnPage() {
  tip('Click something on the page. Esc cancels.');
  askParent('pick', null, 120000).then(function (m) {
    tip('');
    if (!m || !m.ok || !m.result) return;
    var el = m.result;
    addAnchor({ kind: 'page', url: (S.page || {}).url, element: { ref: el.ref, tag: el.tag, text: el.text, section: el.section } });
  });
}
var PAGE_VERBS = { read: 'reading the page', scroll: 'scrolling the page', highlight: 'pointing at something',
                   click: 'pressing a control', fill: 'typing', press: 'pressing a key',
                   navigate: 'opening another page', step: 'stepping the animation', open_lesson: 'opening a lesson',
                   studio: 'working in the Studio', wait: 'pausing', comments: 'reading the comments',
                   comment: 'writing a comment', reply: 'replying to a comment', resolve: 'marking a comment addressed' };
function pageAction(p) {
  if (!p.action_id || p.view_id !== S.view) return;
  if (p.expires_at && Date.now() / 1000 > p.expires_at) return;
  S.pageDoing = { text: PAGE_VERBS[p.action] || p.action, at: Date.now() };
  if (!S.debug) renderConv();
  var done = function (r) {
    r = r || { ok: false, error: 'no answer' };
    api('POST', '/api/page-actions/' + encodeURIComponent(p.action_id) + '/result',
        { ok: !!r.ok, result: r.ok ? r.result : undefined, error: r.ok ? undefined : (r.error || 'refused') });
  };
  var who = shortName((p.actor || {}).label || (p.actor || {}).id);
  if (CFG.framed) { askParent('act', { action: p.action, args: p.args || {}, who: who }, 28000).then(done); return; }
  if (!window.QCCD_PAGE) { done({ ok: false, error: 'this page has no page actions' }); return; }
  window.QCCD_DESIGN_PAGE = CFG.mode === 'design';        // the workspace's design: its edits go through change sets
  var verb = (p.args || {}).verb, V = (((window.QCCD_INTERFACE || {}).verbs) || {})[verb];
  if (CFG.mode === 'design' && p.action === 'studio' && V && (V.kind === 'design' || V.kind === 'program')) {
    // the agent draws on the person's design: their pending edits first, then the verb, then its records
    // as the agent's change set -- visible on the canvas, attributed, undoable
    var label = who + ': ' + verb + (V.does ? ' (' + V.does.slice(0, 60) + ')' : '');
    flushSync().then(function () {
      S.agentHold = true;
      return window.QCCD_PAGE.act(p.action, p.args || {}, who);
    }).then(function (r) {
      if (!r || !r.ok || (r.result && r.result.ok === false)) {
        S.agentHold = false; refreshHead(true);
        done(r && r.ok ? { ok: false, error: verb + ' refused: ' + JSON.stringify((r.result || {}).result || {}).slice(0, 300) } : r);
        return;
      }
      return syncAs(p.action_id, label).then(function (cs) {
        S.agentHold = false;
        if (cs.ok) {
          r.result = r.result || {};
          r.result.change_set = { status: cs.data.status, revision: cs.data.revision, change_set_id: cs.data.change_set_id,
                                  diagnostics: cs.data.diagnostics, summary: cs.data.summary };
          done(r);
        } else {
          var e = cs.error || {};
          done({ ok: false, error: 'the Studio drew it, but it was not committed (' + (e.code || '') + '): ' + (e.message || '') });
        }
      });
    }, function (e) { S.agentHold = false; refreshHead(true); done({ ok: false, error: String(e && e.message || e) }); });
    return;
  }
  window.QCCD_PAGE.act(p.action, p.args || {}, who).then(done, function (e) { done({ ok: false, error: String(e && e.message || e) }); });
}
function pageEmptyState() {
  var k = (S.page || {}).kind || 'page', ex;
  if (/studio/.test(k)) ex = ['What does this lesson teach? Step me through it', 'Explain what happens at the current step'];
  else if (k === 'rules') ex = ['Explain rule R7, and show me its failing example', 'Which rules limit how fast ions can move?'];
  else if (/leaderboard/.test(k)) ex = ['Which design is fastest here, and why?', 'Compare the top two designs on this board'];
  else if (k === 'course index') ex = ['Which lesson should I start with?', 'Open the first lesson on junctions'];
  else ex = ['Summarize this page', 'What should I read next?'];
  return h('div', { cls: 'qcl-empty' }, [
    h('div', { cls: 'qcl-empty-t', text: 'Ask about this page.' }),
    h('div', { cls: 'qcl-note', text: 'The agent reads the page you are on and can point at things, scroll, step ' +
                                      'animations and open other pages. Select text and it goes with your message. ' +
                                      'It can also work on your own design, in your Studio.' })
  ].concat(ex.map(function (t) {
    return h('button', { cls: 'qcl-ex', text: t, on: { click: function () {
      var ta = document.getElementById('qcl-text'); ta.value = t; S.textCache = t; grow(ta); ta.focus(); renderSendButton(); } } });
  })));
}

// ------------------------------------------------------------------ view state reporting
function reportView() {
  if (!S.view || !S.paired) return;
  var now = Date.now();
  if (!S.viewDirty && now - S.lastViewPatch < 20000) return;
  if (now - S.lastViewPatch < 1500) return;
  S.lastViewPatch = now; S.viewDirty = false;
  api('PATCH', '/api/views/' + encodeURIComponent(S.view), {
    rendered_revision: CFG.mode === 'design' ? S.rev : null, selection: selectionKeys().slice(0, 100),
    viewport: viewport(), frame: (typeof frame === 'number') ? frame : null,
    displayed_run: CFG.snapshot_id || null, page: pageInfo() });
}

// ------------------------------------------------------------------ boot
function pair() {
  var m = /[#&]pair=([A-Za-z0-9_-]+)/.exec(location.hash || '');
  var p = m ? api('POST', '/api/pair', { code: m[1] }) : Promise.resolve(null);
  return p.then(function (r) {
    if (m) history.replaceState(null, '', location.pathname + location.search);
    if (r && !r.ok) notice('Pairing failed: ' + (r.error || {}).message, 'bad');
    return api('GET', '/api/whoami');
  }).then(function (w) {
    if (!w.ok || !w.data.csrf) {
      document.body.appendChild(h('div', { id: 'qcl-unpaired' }, PAGE ? [
        h('b', { text: 'This browser is not paired with your workspace yet.' }),
        h('div', { text: 'Run `qccd web` in the workspace folder: it pairs this browser and opens the website with the chat.' })] : [
        h('b', { text: 'This Studio page is not paired with the workspace.' }),
        h('div', { text: 'Run `qccd studio` in the workspace and open the link it prints. Edits here are not saved to the workspace.' })]));
      return false;
    }
    S.csrf = w.data.csrf; S.paired = true;
    return true;
  });
}
function registerView() {
  var key = 'qccd.view.' + CFG.workspace_id;
  var vid = sessGet(key);
  var go = function () {
    return api('POST', '/api/views', { branch: S.branch, page: pageInfo(),
                                       label: CFG.mode === 'run' ? 'run ' + CFG.snapshot_id : (PAGE ? 'Website' : 'Studio') }).then(function (r) {
      if (r.ok) { S.view = r.data.id; sessSet(key, S.view); S.target = r.data.target_session; S.follow = r.data.follow_agent; }
    });
  };
  if (!vid) return go();
  S.view = vid;
  return api('PATCH', '/api/views/' + encodeURIComponent(vid), { branch: S.branch, closed: false, page: pageInfo() }).then(function (r) {
    if (!r.ok) return go();
    S.target = r.data.target_session; S.follow = r.data.follow_agent;
  });
}
function boot() {
  if (PAGE) listenToPage();
  buildDock();
  pair().then(function (ok) {
    if (!ok) return;
    return registerView().then(function () {
      return api('GET', '/api/whoami').then(function () {
        if (CFG.mode === 'design') return loadHead();
        if (PAGE) return refreshPage();
        if (ED && ED.setViewOnly) ED.setViewOnly(true);
        var m = /[#&]instr=(\d+)/.exec(location.hash || '');
        if (m && typeof seek === 'function' && P && P.frames) {
          for (var i = 0; i < P.frames.length; i++) if (P.frames[i].id === +m[1]) { seek(i, {}); break; }
        }
      });
    }).then(function () {
      return api('GET', '/api/whoami').then(function (w) {
        if (w.ok) api('GET', '/api/context').then(function (c) {
          if (c.ok) S.cursor = c.data.cursor || 0;
          openEvents(); refreshAll();
          if (!S.debug) { loadConv(); loadBranches();
            loadAgents().then(function () { renderChatHead(); renderConv(); }); }
        });
      });
    });
  });
  setInterval(function () {
    try {
      sync(); reportView(); if (!document.getElementById('qcl-ov')) paintOverlay();
      if (!S.debug && selectionKeys().join(',') !== S.lastSel) { S.selOff = false; renderChips(); }
    } catch (e) { console.error('qccd live tick', e); }
  }, 400);
  window.addEventListener('pagehide', function () {
    if (S.view && S.csrf) {
      try { fetch('/api/views/' + encodeURIComponent(S.view), { method: 'PATCH', keepalive: true, credentials: 'same-origin',
             headers: { 'Content-Type': 'application/json', 'X-QCCD-CSRF': S.csrf, 'X-QCCD-View': S.view },
             body: J({ closed: true }) }); } catch (e) { /* best effort */ }
    }
  });
}

// the harness and the browser driver read and drive the layer through this, never the DOM
window.QCCD_LIVE = {
  state: function () { return { rev: S.rev, view: S.view, connected: S.connected, paired: S.paired, inflight: S.inflight,
    anchors: S.anchors.slice(), sketches: S.sketches.slice(), target: S.target, follow: S.follow, sessions: S.sessions,
    prompts: S.prompts, protected: Object.keys(S.protected), highlight: S.highlight.slice(), mode: S.mode, demo: S.demo,
    synced_edits: S.synced ? S.synced.edits.length : null, cursor: S.cursor }; },
  send: send, setTool: setTool, addAnchor: addAnchor, addSketch: addSketch, addLasso: addLasso,
  reveal: reveal,
  disconnect: function () { if (S.es) S.es.close(); S.es = null; S.connected = false; renderStatus(); },
  reconnect: function () { openEvents(); },
  selectionKeys: selectionKeys, refreshHead: refreshHead, sync: sync, protect: protect, unprotect: unprotect,
  demoStart: function () { S.demo = { before: S.rev }; }, demoEnd: function (targets) {
    S.demo.after = S.rev; S.demo.targets = targets || selectionKeys(); },
  setFollow: function (on) { S.follow = !!on; return api('PATCH', '/api/views/' + encodeURIComponent(S.view), { follow_agent: S.follow }); },
  setMode: function (m) { S.mode = m; }, openThread: openThread, stop: stopAgent,
  // the chat
  chat: function () { return { debug: S.debug, items: S.conv.slice(), working: S.working.slice(), pending: S.pending,
                               branch: S.branch, branches: S.branches.map(function (b) { return b.name; }),
                               agent: (agentSession() || {}).id || null, codex: S.codex, claude: S.claude }; },
  chatSend: chatSend, loadConv: loadConv, setDebug: setDebug, switchDraft: switchDraft, saveAsDraft: saveAsDraft,
  page: function () { return S.page; }, refreshPage: refreshPage
};

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
})();
