// book-runtime.js -- the page-side API of the QCCD book's in-browser Lean.  No dependencies.
//
//   Book.configure({ manifest, worker, prelude, memoryMB, timeoutMs, spare })   before ready()
//   await Book.ready(onProgress)          onProgress({phase, file, loaded, total}); `loaded`/`total`
//                                         are bytes while phase === 'download'
//   await Book.check(text)       -> { messages:[{line,col,endLine,endCol,severity,text}], ms }
//   await Book.goals(text, line) -> string   (the goals after `line`; before it on a blank/`sorry` line)
//   await Book.writeFile(path, text)       put a file into the engine's filesystem (kept across restarts)
//   Book.stats()                 -> { restarts, crashes, heapBytes, lastReadyMs, warmMs }
//
// Positions: `line` is 1-based in the text you passed (a configured prelude is subtracted; a
// message inside the prelude gets inPrelude:true); `col` is 0-based, in Unicode code points.
//
// Requests: one runs at a time.  A newer request of the same kind (check, goals) replaces one
// still waiting; the replaced promise resolves with { superseded:true }.  Call check() on every
// edit and only the latest text is elaborated.
//
// Restarts: the engine is replaced (a) after a compile that left it without stdin/stdout/stderr
// -- a safety net: that happened whenever Lean generated equation lemmas until lean-worker.js
// began holding an Emscripten keepalive reference -- in the background, so the result in hand
// is returned at once; (b) when a request hits the JavaScript stack limit
// or finds the engine jammed, and that request is retried once on the new engine, resolving
// { crashed:true, why } if it fails again; (c) when a request runs longer than timeoutMs (Lean
// cannot be interrupted any other way).  With { spare:true } a second engine is kept booted, so
// a replacement is immediate instead of the ~4-5 s of a cold restart (it costs a second heap).
(function (global) {
  'use strict';
  const here = (global.document && document.currentScript && document.currentScript.src) || global.location.href;
  const cfg = {
    manifest: new URL('site/manifest.json', here).href,
    worker: new URL('lean-worker.js', here).href,
    prelude: '',
    memoryMB: 1024,
    timeoutMs: 300000,
    spare: false,
  };
  let progressCb = null, active = null, spare = null, started = null, running = null;
  const queue = new Map();          // kind -> {run, resolve}
  const files = new Map();          // path -> text, replayed into every new engine
  const stats = { restarts: 0, crashes: 0, heapBytes: null, lastReadyMs: null, warmMs: null };
  let nextId = 1;

  function preludeText() { return cfg.prelude ? cfg.prelude.replace(/\n?$/, '\n') : ''; }
  function preludeLines() { return preludeText().split('\n').length - 1; }

  // One engine = one worker, booted, with the files written and the prelude elaborated once.
  function makeEngine(report) {
    const w = new Worker(cfg.worker);
    const pending = new Map();
    const e = { w, dead: false };
    e.rpc = (msg, timeoutMs) => new Promise((resolve) => {
      if (e.dead) return resolve({ ok: false, crashed: true, why: 'engine stopped' });
      const id = nextId++;
      let timer = null;
      pending.set(id, (m) => { if (timer) clearTimeout(timer); resolve(m); });
      if (timeoutMs) timer = setTimeout(() => { pending.delete(id); resolve({ ok: false, crashed: true, timedOut: true, why: `timeout after ${timeoutMs} ms` }); }, timeoutMs);
      w.postMessage({ ...msg, id });
    });
    e.stop = () => { e.dead = true; w.terminate(); for (const [, r] of pending) r({ ok: false, crashed: true, why: 'engine stopped' }); pending.clear(); };
    e.ready = new Promise((resolve, reject) => {
      w.onmessage = (ev) => {
        const m = ev.data;
        if (m.id && pending.has(m.id)) { const r = pending.get(m.id); pending.delete(m.id); r(m); return; }
        if (m.type === 'progress') { if (report && progressCb) progressCb({ phase: m.phase, file: m.file, loaded: m.loaded, total: m.total }); }
        else if (m.type === 'ready') { stats.heapBytes = m.heapBytes; stats.lastReadyMs = m.ms; resolve(); }
        else if (m.type === 'fatal') reject(new Error(m.why));
      };
      w.onerror = (ev) => reject(new Error('worker: ' + (ev.message || 'failed to start')));
      w.postMessage({ type: 'boot', manifestUrl: cfg.manifest, memoryMB: cfg.memoryMB });
    }).then(async () => {
      for (const [path, text] of files) await e.rpc({ type: 'write_file', path, text });
      if (cfg.prelude) {
        if (report && progressCb) progressCb({ phase: 'import', file: '', loaded: 0, total: 0 });
        const r = await e.rpc({ type: 'compile', code: preludeText(), path: '/work/Prelude.lean' });
        stats.warmMs = r.ms;
        const errs = (r.messages || []).filter((m) => m.severity === 'error');
        if (r.crashed || errs.length) throw new Error('prelude failed: ' + (r.why || errs.map((m) => m.data).join('; ')));
        if (r.stdioClosed) throw new Error('the prelude itself leaves the engine unusable (it generates equation lemmas)');
      }
    });
    e.ready.catch(() => {});
    return e;
  }
  function bootSpare() { if (cfg.spare && !spare) spare = makeEngine(false); }
  // Replace the active engine; resolves when the new one is ready.
  function replace(why) {
    stats.restarts++;
    if (active) active.stop();
    if (spare) { active = spare; spare = null; } else active = makeEngine(false);
    const a = active;
    return a.ready.then(() => { if (a === active) bootSpare(); });
  }

  // Elaborate `code` on the active engine with the restart policy above.
  async function compile(code) {
    for (let attempt = 0; attempt < 2; attempt++) {
      await active.ready;
      const r = await active.rpc({ type: 'compile', code, path: '/work/Page.lean' }, cfg.timeoutMs);
      const jammed = r.ioError && !(r.messages || []).length;
      if (!r.crashed && !jammed) {
        if (r.stdioClosed) replace('the last file generated equation lemmas (stdio closed)');   // in the background
        return r;
      }
      const why = r.crashed ? r.why : `engine jammed (${r.ioErrorText || 'IO error, no messages'})`;
      await replace(why);
      if (attempt === 1 || r.timedOut) { stats.crashes++; return { crashed: true, why }; }
    }
  }

  function schedule(kind, run) {
    return new Promise((resolve) => {
      const old = queue.get(kind);
      if (old) old.resolve({ superseded: true });
      queue.set(kind, { run, resolve });
      pump();
    });
  }
  async function pump() {
    if (running || !queue.size) return;
    const [kind, job] = queue.entries().next().value;
    queue.delete(kind);
    running = job;
    try { job.resolve(await job.run()); }
    catch (e) { job.resolve({ crashed: true, why: String(e && e.message || e) }); }
    finally { running = null; pump(); }
  }

  function normalise(m, offset) {
    const line = m.pos.line - offset;
    return { line, col: m.pos.column, endLine: (m.endPos ? m.endPos.line : m.pos.line) - offset,
      endCol: m.endPos ? m.endPos.column : m.pos.column, severity: m.severity, text: m.data,
      ...(line < 1 ? { inPrelude: true } : {}) };
  }

  const Book = {
    configure(opts) { Object.assign(cfg, opts || {}); return Book; },
    ready(onProgress) {
      if (onProgress) progressCb = onProgress;
      if (!started) {
        active = makeEngine(true);
        started = active.ready.then(() => { bootSpare(); if (progressCb) progressCb({ phase: 'ready', file: '', loaded: 0, total: 0 }); });
      }
      return started;
    },
    check(text) {
      return schedule('check', async () => {
        await started;
        const r = await compile(preludeText() + text);
        if (r.crashed) return r;
        const off = preludeLines();
        return { messages: r.messages.map((m) => normalise(m, off)), ms: r.ms, output: r.output };
      });
    },
    goals(text, line) {
      return schedule('goals', async () => {
        await started;
        const lines = text.split('\n');
        const L = Math.max(1, Math.min(line, lines.length)) - 1;
        const cur = lines[L] || '';
        const before = /^\s*(sorry)?\s*$/.test(cur);
        const upto = before ? L - 1 : L;
        let indent = (cur.match(/^\s*/) || [''])[0];
        if (!before && /\bby\s*$/.test(cur)) indent += '  ';
        if (before && cur.trim() === '') { for (let k = L + 1; k < lines.length; k++) if (lines[k].trim()) { indent = lines[k].match(/^\s*/)[0]; break; } }
        const probe = lines.slice(0, upto + 1).join('\n') + `\n${indent}trace_state\n${indent}all_goals sorry\n`;
        const r = await compile(preludeText() + probe);
        if (r.crashed) return `(Lean crashed: ${r.why})`;
        const off = preludeLines();
        const probeLine = upto + 2 + off;
        const st = r.messages.filter((m) => m.pos.line === probeLine && m.severity === 'information');
        if (st.length) return st.map((m) => m.data).join('\n\n').trim() || 'no goals';
        const errs = r.messages.filter((m) => m.severity === 'error').map((m) => normalise(m, off));
        return errs.length ? `(no goal state here: ${errs[0].text.split('\n')[0]})` : 'no goals';
      });
    },
    async writeFile(path, text) {
      files.set(path, text);
      await started;
      await active.ready;
      return active.rpc({ type: 'write_file', path, text });
    },
    stats() { return { ...stats, spareReady: !!spare }; },
    // for tests
    _replace: replace,
    _active: () => active,
    async _raw(code) { await started; await active.ready; return active.rpc({ type: 'compile', code, path: '/work/Page.lean' }, cfg.timeoutMs); },
  };
  global.Book = Book;
})(typeof window !== 'undefined' ? window : self);
