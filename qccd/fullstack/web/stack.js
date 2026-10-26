// THE FULL STACK, in the Program pane (qccd/fullstack/page.py injects this into full-stack pages
// only; the engine and template are untouched).
//
// The engine already answers "which circuit operation is this instruction for" (SRC.realises,
// .toward, .after -- built from the compiler's stamps and checked against the certificate's
// witnesses).  SRC.stack carries the rest of the chain: each circuit operation's .sysprog event,
// and that event's .qec statement, .qppm PPM and .prog line, each with HOW the link was made.
// So at every instruction the reader sees the logical operation, the PPM, the QEC operation, the
// Clifford statement (operands bound to ions) and the hardware instruction -- never a blank:
// a layer with nothing to say says why.
(function () {
  'use strict';
  if (typeof SRC === 'undefined' || !SRC || !SRC.stack) return;
  const S = SRC.stack;
  const LAY = ['prog', 'qppm', 'qec', 'sys'];
  const E_SYS = 1, E_CTX = 2, E_OPS = 3;
  const AT = {prog: [8, 9], qppm: [6, 7], qec: [4, 5]};
  const esc2 = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  function where(k, line) {
    const L = S.layers[k];
    for (const [m, first, n] of L.members)
      if (line >= first && line < first + n) return [(m || L.file), line - first + 1];
    return [L.file, line];
  }
  function text(k, line) { return ((S.layers[k].lines[line - 1]) || '').trim(); }

  // what the instruction is doing, and for which circuit operations
  function opsOf(f) {
    const now = SRC.realises[f.id] || [];
    if (now.length) return ['executing', now];
    const soon = (SRC.toward || {})[f.id] || [];
    if (soon.length) return ['shuttling towards', soon];
    const past = (SRC.after || {})[f.id] || [];
    if (past.length) return ['clearing after', past];
    return ['', []];
  }
  // the chain of the instruction's first operation; the lines of the others are counted
  function chainOf(f) {
    const [mode, ops] = opsOf(f);
    if (!ops.length) return null;
    const uid = S.op_event[ops[0]];
    const E = S.events[uid];
    const evs = new Set(ops.map((o) => S.op_event[o]));
    return {mode, ops, uid, E, n: evs.size};
  }
  function lineOf(c, k) {
    if (!c) return 0;
    if (k === 'sys') return c.E[E_SYS];
    return c.E[AT[k][0]] || 0;
  }
  function howOf(c, k) {
    if (!c || k === 'sys') return '';
    return c.E[AT[k][1]] || '';
  }
  function howBadge(h) {
    if (!h) return '';
    return '<span class="fs-how" title="' + esc2(S.how[h] || h) + '">' + esc2(h) + '</span>';
  }
  function atText(k, line) {
    const [m, l] = where(k, line);
    return '<span class="fs-at">' + esc2(m) + ':' + l + '</span>';
  }
  // one caption row per layer
  function rowHTML(k, c) {
    const key = '<span class="fs-k">' + esc2(S.titles[k]) + '</span>';
    if (!c) return key + '<span class="fs-v fs-mode">no circuit operation: the compiler&#39;s own bookkeeping</span>';
    const line = lineOf(c, k);
    if (!line) return key + '<span class="fs-v fs-mode">not linked at this layer</span>';
    let body = '<code>' + esc2(text(k, line)) + '</code>' + atText(k, line) + howBadge(howOf(c, k));
    if (k === 'sys') {
      const ctx = c.E[E_CTX], ops = c.E[E_OPS];
      body = '<code>' + esc2(text(k, line).replace(/^@\S+\s+\S+\s+/, '')) + '</code>' +
             (ctx ? '<span class="fs-ctx">' + esc2(ctx) + '</span>' : '') + atText(k, line) +
             (ops ? '<br><span class="fs-at">' + esc2(ops) + '</span>' : '');
      if (c.n > 1) body += '<span class="fs-at">+' + (c.n - 1) + ' more</span>';
    }
    return key + '<span class="fs-v">' + body + '</span>';
  }
  function captionHTML(f, withHw) {
    const c = chainOf(f);
    let h = '<div class="fs-cap">';
    for (const k of LAY) h += rowHTML(k, c);
    if (withHw) {
      const mode = c ? '<span class="fs-mode">' + esc2(c.mode) + '</span> ' : '';
      h += '<span class="fs-k">Hardware</span><span class="fs-v" style="white-space:normal">' + mode +
           hwHTML(f) + '</span>';
    }
    return h + '</div>';
  }
  // the instruction as the Hardware pane writes it (the engine's own argsHTML)
  function hwHTML(f) {
    if (f.id === undefined) return '<span class="fs-mode">no instruction</span>';
    return '<b>#' + f.id + '</b> ' + esc2(f.type || '') + (f.cls ? ' · ' + esc2(f.cls) : '') +
           (f.mode ? ' · ' + esc2(f.mode) : '') +
           (typeof argsHTML === 'function' ? '<br>' + argsHTML(f) : '');
  }

  // ---- the Stack view (the Hardware, Gates and Both views stay exactly the Studio's own)
  const seg = document.getElementById('pView');
  const pane = document.getElementById('paneP');
  if (!seg || !pane) return;
  const btn = document.createElement('button');
  btn.id = 'pvstack';
  btn.setAttribute('data-view', 'stack');
  btn.setAttribute('data-hint', 'fullstack-view');
  btn.title = 'every layer at the executing instruction';
  btn.textContent = 'Stack';
  seg.appendChild(btn);
  const block = document.createElement('div');
  block.className = 'pblock';
  block.id = 'pStack';
  let lays = '';
  for (const k of LAY) {
    lays += '<section class="fs-lay" data-layer="' + k + '"><header><h4>' + esc2(S.titles[k]) +
            '</h4><span class="fs-file">' + esc2(S.layers[k].file) + '</span></header>' +
            '<div class="fs-lines" data-layer="' + k + '"></div></section>';
  }
  const a = S.abstract || {}, cc = S.concrete || {};
  block.innerHTML =
    '<div class="ph"><h4>Full stack</h4><span class="grow"></span></div>' +
    '<div id="fsNow"></div>' + lays +
    '<div class="fs-foot">FormalRV ' + esc2(S.formalrv) + ' · ' + esc2(S.code) + ' d=' + S.distance +
    ' · abstract (FormalRV VM): ' + ((a.runtime_ns || 0) / 1e6).toFixed(1) + ' ms, ' + a.peak_qubits + ' qubits' +
    ' · concrete (this machine): ' + ((cc.runtime_us || 0) / 1e3).toFixed(1) + ' ms, ' + cc.ions + ' ions' +
    '<br>A badge says how a link between two layers was made; hover it.  Click a line to jump to the ' +
    'first instruction that executes it.</div>';
  pane.appendChild(block);

  // the first frame executing each line of each layer (click a line, land there)
  const FIRST = {prog: {}, qppm: {}, qec: {}, sys: {}};
  for (let i = 0; i < P.frames.length; i++) {
    const ops = SRC.realises[P.frames[i].id] || [];
    for (const o of ops) {
      const E = S.events[S.op_event[o]];
      if (!E) continue;
      const ls = {sys: E[E_SYS], qec: E[4], qppm: E[6], prog: E[8]};
      for (const k of LAY) if (ls[k] && FIRST[k][ls[k]] === undefined) FIRST[k][ls[k]] = i;
    }
  }
  function seek(i) { stop(); frame = i; phase = 1; slider.value = String(frame); draw(); }
  block.addEventListener('click', (ev) => {
    const r = ev.target.closest('.fs-ln');
    if (!r) return;
    const k = r.getAttribute('data-layer'), ln = +r.getAttribute('data-line');
    const i = FIRST[k][ln];
    if (i !== undefined) seek(i);
  });

  const WIN = 6;
  function renderLayers(c) {
    for (const k of LAY) {
      const box = block.querySelector('.fs-lines[data-layer="' + k + '"]');
      const lines = S.layers[k].lines;
      const cur = lineOf(c, k);
      let lo = 1, hi = lines.length;
      if (lines.length > 2 * WIN + 1) {
        const mid = cur || 1;
        lo = Math.max(1, mid - WIN); hi = Math.min(lines.length, lo + 2 * WIN);
        lo = Math.max(1, hi - 2 * WIN);
      }
      let h = '';
      for (let ln = lo; ln <= hi; ln++) {
        const [, l] = where(k, ln);
        const can = FIRST[k][ln] !== undefined;
        h += '<div class="fs-ln' + (ln === cur ? ' fs-cur' : '') + (can ? '' : ' fs-none') +
             '" data-layer="' + k + '" data-line="' + ln + '" data-hint="fullstack-line"><span>' + l +
             '</span><span>' + esc2(lines[ln - 1] || '') + '</span></div>';
      }
      box.innerHTML = h;
      // the current line in the middle of its box, not at an edge
      const cl = box.querySelector('.fs-cur');
      if (cl) box.scrollTop = Math.max(0, cl.offsetTop - (box.clientHeight - cl.offsetHeight) / 2);
    }
  }
  function refresh() {
    if (pane.getAttribute('data-view') !== 'stack') return;
    const f = P.frames[frame] || {};
    document.getElementById('fsNow').innerHTML = captionHTML(f, true);
    renderLayers(chainOf(f));
  }

  // the view switch: Stack joins Hardware / Gates / Both
  const baseView = setProgView;
  // eslint-disable-next-line no-global-assign
  setProgView = function (v) {
    if (v !== 'stack') {
      btn.className = '';
      const out = baseView(v);
      const pc = document.getElementById('progcol');
      if (pc && pc.getAttribute('data-view') === 'stack') pc.setAttribute('data-view', 'one');
      return out;
    }
    baseView('hw');
    PROG_VIEW = 'stack';
    pane.setAttribute('data-view', 'stack');
    for (const k of ['hw', 'gates', 'both']) {
      const b = document.getElementById('pv' + k); if (b) b.className = '';
    }
    btn.className = 'on';
    const pc = document.getElementById('progcol');
    if (pc && PROG_COL) pc.setAttribute('data-view', 'stack');
    try { localStorage.setItem('qccd.studio.progview', 'stack'); } catch (e) { /* no store */ }
    sizeStage();
    refresh();
    return 'stack';
  };
  btn.addEventListener('click', () => setProgView('stack'));

  const baseSync = syncCursor;
  // eslint-disable-next-line no-global-assign
  syncCursor = function (force) {
    const before = lastCur;
    baseSync(force);
    if (force || frame !== before) refresh();
  };
  // a full-stack page opens on the stack
  setTimeout(() => { setProgView('stack'); syncCursor(true); }, 0);
  window.FULLSTACK = {chainOf, captionHTML, FIRST, stack: S, seek};
})();
