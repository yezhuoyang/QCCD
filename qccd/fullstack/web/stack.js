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
  //
  // EVERY LAYER IN A COLUMN OF ITS OWN (the user, 2026-10-28: "All programs should take an
  // entire column! Don't squeeze them in one column!!").  The .prog, .qppm, .qec and .sysprog
  // stand side by side, each the whole height of the pane, the line the executing instruction
  // carries lit and kept in view; the Studio's own hardware listing can stand beside them.
  // Every word is coloured by what it does and explained on hover (qccd/gadget/web/langs.js,
  // the same tables the gadget pages use).  A column is drawn once; the cursor only moves its
  // lit line.
  const LANGS = window.QCCD_LANGS || null;
  const seg = document.getElementById('pView');
  const pane = document.getElementById('paneP');
  const pHw = document.getElementById('pHw');
  if (!seg || !pane) return;
  const store = {
    get(k) { try { return localStorage.getItem('qccd.studio.fs.' + k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem('qccd.studio.fs.' + k, v); } catch (e) { /* no store */ } },
  };
  const btn = document.createElement('button');
  btn.id = 'pvstack';
  btn.setAttribute('data-view', 'stack');
  btn.setAttribute('data-hint', 'fullstack-view');
  btn.title = 'every layer at the executing instruction, a column each';
  btn.textContent = 'Stack';
  seg.appendChild(btn);
  const block = document.createElement('div');
  block.className = 'pblock';
  block.id = 'pStack';
  const COLS = LAY.concat(['hw']);
  const TITLE = (k) => (k === 'hw' ? 'Hardware' : S.titles[k]);
  const chosen = new Set((store.get('cols') || 'prog,qppm,qec,sys').split(',').filter((k) => COLS.includes(k)));
  let cols = '';
  for (const k of LAY) {
    cols += '<section class="fs-col" data-layer="' + k + '"><header><h4>' + esc2(S.titles[k]) +
            '</h4><span class="fs-file">' + esc2(S.layers[k].file) + '</span>' +
            '<button class="fs-x" data-hint="fullstack-close" title="hide this column">×</button></header>' +
            '<div class="fs-lines" data-layer="' + k + '"></div></section>';
  }
  const a = S.abstract || {}, cc = S.concrete || {};
  block.innerHTML =
    '<div class="ph fs-bar"><h4>Full stack</h4>' +
    COLS.map((k) => '<button class="tgl" data-col="' + k + '" data-hint="fullstack-col" title="show or hide the ' +
                    esc2(TITLE(k)) + ' column">' + esc2(TITLE(k)) + '</button>').join('') +
    '<span class="grow"></span><button class="tgl" id="fsWrap" data-hint="fullstack-wrap" ' +
    'title="wrap long lines inside their column">Wrap</button></div>' +
    '<div id="fsNow"></div><div class="fs-cols">' + cols + '</div>' +
    '<div class="fs-foot" title="A badge in a column&#39;s header says how the link between two layers was made; ' +
    'hover it. Hover a word for what it means; click a line to jump to the first instruction that executes it.">' +
    'FormalRV ' + esc2(S.formalrv) + ' · ' + esc2(S.code) + ' d=' + S.distance +
    ' · abstract (FormalRV VM): ' + ((a.runtime_ns || 0) / 1e6).toFixed(1) + ' ms, ' + a.peak_qubits + ' qubits' +
    ' · concrete (this machine): ' + ((cc.runtime_us || 0) / 1e3).toFixed(1) + ' ms, ' + cc.ions + ' ions</div>';
  pane.appendChild(block);
  const colsBox = block.querySelector('.fs-cols');

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

  // each layer's whole file, once: a program of several files says where each begins and
  // numbers its lines file by file, as FormalRV does
  const ROWS = {}, CALLS = {}, CUR = {};
  for (const k of LAY) {
    const lines = S.layers[k].lines;
    CALLS[k] = LANGS ? LANGS.defined(k, lines, (ln) => where(k, ln).join(':')) : {};
    const parts = [];
    let file = null;
    for (let ln = 1; ln <= lines.length; ln++) {
      const [m, l] = where(k, ln);
      if (S.layers[k].members.length > 1 && m !== file) parts.push('<div class="fs-fileh">' + esc2(m) + '</div>');
      file = m;
      const t = lines[ln - 1] || '', body = t.replace(/^\s+/, ''), ind = t.length - body.length;
      const can = FIRST[k][ln] !== undefined;
      parts.push('<div class="fs-ln' + (can ? '' : ' fs-none') + '" data-layer="' + k + '" data-line="' + ln +
                 '" data-hint="fullstack-line"><span>' + l + '</span><span class="fs-tx" style="padding-left:min(' +
                 (ind + 2) + 'ch, 40%)">' + (body ? (LANGS ? LANGS.paint(k, body, {calls: CALLS[k]}) : esc2(body)) : ' ') +
                 '</span></div>');
    }
    const box = block.querySelector('.fs-lines[data-layer="' + k + '"]');
    box.innerHTML = parts.join('');
    ROWS[k] = box.querySelectorAll('.fs-ln');
    CUR[k] = 0;
  }
  block.addEventListener('click', (ev) => {
    const x = ev.target.closest('.fs-x');
    if (x) { setCol(x.closest('.fs-col').getAttribute('data-layer'), false); return; }
    const b = ev.target.closest('.fs-bar button[data-col]');
    if (b) { setCol(b.getAttribute('data-col')); return; }
    const r = ev.target.closest('.fs-ln');
    if (!r) return;
    const k = r.getAttribute('data-layer'), ln = +r.getAttribute('data-line');
    const i = FIRST[k][ln];
    if (i !== undefined) seek(i);
  });
  // a row in view, a third of the way down, if it is not already
  function inView(box, el) {
    const top = el.offsetTop, h = box.clientHeight;
    if (!h) return;
    if (top < box.scrollTop + 4 || top + el.offsetHeight > box.scrollTop + h - 20) box.scrollTop = Math.max(0, top - h / 3);
  }
  function markLayers(c, follow) {
    for (const k of LAY) {
      const cur = lineOf(c, k);
      const box = block.querySelector('.fs-lines[data-layer="' + k + '"]');
      if (cur !== CUR[k]) {
        const old = CUR[k] ? ROWS[k][CUR[k] - 1] : null;
        if (old) old.classList.remove('fs-cur');
        CUR[k] = cur;
        const el = cur ? ROWS[k][cur - 1] : null;
        if (el) { el.classList.add('fs-cur'); if (chosen.has(k)) inView(box, el); }
      } else if (follow && cur && chosen.has(k)) inView(box, ROWS[k][cur - 1]);
    }
  }

  // which columns stand, and how wide the pane is: a listing's width each
  const home = pHw ? pHw.nextSibling : null;
  function applyCols() {
    for (const k of LAY) block.querySelector('.fs-col[data-layer="' + k + '"]').hidden = !chosen.has(k);
    for (const b of block.querySelectorAll('.fs-bar button[data-col]'))
      b.className = 'tgl' + (chosen.has(b.getAttribute('data-col')) ? ' on' : '');
    // the hardware listing is the Studio's own; while the stack shows it, it stands in the row
    if (pHw) {
      const want = pane.getAttribute('data-view') === 'stack' && chosen.has('hw');
      if (want && pHw.parentNode !== colsBox) { colsBox.appendChild(pHw); pHw.classList.add('fs-col'); }
      if (!want && pHw.parentNode === colsBox) { pane.insertBefore(pHw, home); pHw.classList.remove('fs-col'); }
    }
    const n = Math.max(1, chosen.size);
    const w = Math.min(n * 250 + 40, Math.round(window.innerWidth * 0.66)) + 'px';
    for (const id of ['progcol', 'dock']) { const el = document.getElementById(id); if (el) el.style.setProperty('--fs-w', w); }
  }
  function setCol(k, on) {
    if (on === undefined) on = !chosen.has(k);
    if (on) chosen.add(k); else chosen.delete(k);
    store.set('cols', COLS.filter((x) => chosen.has(x)).join(','));
    applyCols();
    sizeStage();
    // a listing that has just moved starts at its top: the Studio's own follow brings the
    // executing instruction back into view, and every column its lit line
    syncCursor(true);
  }
  const wrapBtn = block.querySelector('#fsWrap');
  function setWrap(on) {
    block.classList.toggle('fs-nowrap', !on);
    wrapBtn.className = 'tgl' + (on ? ' on' : '');
    wrapBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
  setWrap(store.get('wrap') !== '0');
  wrapBtn.addEventListener('click', () => {
    const on = block.classList.contains('fs-nowrap');
    setWrap(on); store.set('wrap', on ? '1' : '0');
    markLayers(chainOf(P.frames[frame] || {}), true);
  });

  // a word's note, under it (the tables are langs.js's)
  const tip = document.createElement('div');
  tip.className = 'fs-tip';
  document.body.appendChild(tip);
  colsBox.addEventListener('mouseover', (ev) => {
    const w = ev.target.closest && ev.target.closest('[data-w]');
    const ln = w && w.closest('.fs-ln');
    const n = ln && LANGS ? LANGS.note(ln.getAttribute('data-layer'), w.getAttribute('data-w'), CALLS[ln.getAttribute('data-layer')]) : null;
    if (!n) { tip.style.display = 'none'; return; }
    tip.textContent = n[0] + '\n' + n[1];
    tip.style.display = 'block';
    const r = w.getBoundingClientRect();
    tip.style.left = Math.max(4, Math.min(window.innerWidth - 370, r.left)) + 'px';
    tip.style.top = (r.bottom + 6 + 120 > window.innerHeight ? r.top - 8 - tip.offsetHeight : r.bottom + 6) + 'px';
  });
  colsBox.addEventListener('mouseleave', () => { tip.style.display = 'none'; });

  // above the columns, only what they do not show: the hardware instruction itself, and the
  // ions the Clifford statement's operands are bound to
  function nowHTML(f, c) {
    const mode = c ? '<span class="fs-mode">' + esc2(c.mode) + '</span> '
                   : '<span class="fs-mode">no circuit operation: the compiler&#39;s own bookkeeping</span> ';
    let h = '<span class="fs-k">Hardware</span><span class="fs-v">' + mode + hwHTML(f).replace('<br>', ' &nbsp;') + '</span>';
    if (c && c.E[E_OPS]) {
      h += '<span class="fs-k">Operands</span><span class="fs-v">' + esc2(c.E[E_OPS]) +
           (c.n > 1 ? '<span class="fs-at">+' + (c.n - 1) + ' more</span>' : '') + '</span>';
    }
    return '<div class="fs-cap">' + h + '</div>';
  }
  // each column's header: where its lit line is written, how the link to it was made, and
  // for the Clifford statement the loop and gadget it stands in
  function heads(c) {
    for (const k of LAY) {
      const head = block.querySelector('.fs-col[data-layer="' + k + '"] .fs-file');
      const cur = CUR[k];
      if (!cur) { head.textContent = S.layers[k].file; continue; }
      const ctx = k === 'sys' && c ? c.E[E_CTX] : '';
      head.innerHTML = esc2(where(k, cur).join(':')) + howBadge(howOf(c, k)) +
                       (ctx ? '<span class="fs-ctx" title="' + esc2(ctx) + '">' + esc2(ctx) + '</span>' : '');
    }
  }
  function refresh(follow) {
    if (pane.getAttribute('data-view') !== 'stack') return;
    const f = P.frames[frame] || {}, c = chainOf(f);
    document.getElementById('fsNow').innerHTML = nowHTML(f, c);
    markLayers(c, follow);
    heads(c);
  }

  // the view switch: Stack joins Hardware / Gates / Both, and widens what holds the pane
  function markHolder(on) {
    const pc = document.getElementById('progcol'), dk = document.getElementById('dock');
    let changed = false;
    const mark = (el, v) => { if (el && el.getAttribute('data-view') !== v) { el.setAttribute('data-view', v); changed = true; } };
    if (on) { if (PROG_COL) mark(pc, 'stack'); else if (PANE === 'P') mark(dk, 'stack'); }
    return changed;
  }
  const baseSyncWidth = syncProgWidth;
  // eslint-disable-next-line no-global-assign
  syncProgWidth = function () {
    const changed = baseSyncWidth();
    return markHolder(PROG_VIEW === 'stack') || changed;
  };
  const baseView = setProgView;
  // eslint-disable-next-line no-global-assign
  setProgView = function (v) {
    if (v !== 'stack') {
      btn.className = '';
      const out = baseView(v);
      applyCols();                   // the hardware listing goes home
      return out;
    }
    baseView('hw');
    PROG_VIEW = 'stack';
    pane.setAttribute('data-view', 'stack');
    for (const k of ['hw', 'gates', 'both']) {
      const b = document.getElementById('pv' + k); if (b) b.className = '';
    }
    btn.className = 'on';
    syncProgWidth();
    applyCols();
    try { localStorage.setItem('qccd.studio.progview', 'stack'); } catch (e) { /* no store */ }
    sizeStage();
    refresh(true);
    return 'stack';
  };
  btn.addEventListener('click', () => setProgView('stack'));
  window.addEventListener('resize', () => { if (PROG_VIEW === 'stack') { applyCols(); sizeStage(); } });

  const baseSync = syncCursor;
  // eslint-disable-next-line no-global-assign
  syncCursor = function (force) {
    const before = lastCur;
    baseSync(force);
    if (force || frame !== before) refresh(!!force);
  };
  // a full-stack page opens on the stack
  setTimeout(() => { setProgView('stack'); syncCursor(true); }, 0);
  window.FULLSTACK = {chainOf, captionHTML, FIRST, stack: S, seek, setCol, cols: () => [...chosen], cur: () => Object.assign({}, CUR)};
})();
