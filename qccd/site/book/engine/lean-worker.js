// lean-worker.js -- the QCCD book's Lean engine, in a dedicated Web Worker.
//
// Lean 4.33.0-pre (cauli/lean4 fork, wasm32 + pthreads), kept resident: the runtime starts
// once and every request goes through the fork's `lean_wasm_compile`, which caches one
// environment per import set.  The init sequence, the export names and the IO-result tag
// offset follow cauli/lean4-wasm-in-browser's lean-worker-persistent.worker.js (Apache-2.0).
//
// Everything comes from one manifest.json (written by tools/build_packs.py): the engine files
// and the library packs, each content-hashed and checked against its sha256 here.
//
// in : {type:'boot', manifestUrl, memoryMB}
//      {type:'compile', id, code, path}
//      {type:'write_file', id, path, text}
//      {type:'mem', id}
// out: {type:'progress', phase, file, loaded, total}   phase: 'download' | 'verify' | 'fs' | 'start'
//      {type:'ready', ms, heapBytes} | {type:'fatal', why}
//      {type:'compile_result', id, ok, ioError, crashed, why, ms, messages:[lean json], output:[text]}
//      {type:'file_written', id, bytes} | {type:'mem', id, heapBytes}
'use strict';
const t0 = performance.now();
const post = (m) => self.postMessage(m);
let ready = false;
let busy = false;
let capture = null;
let wasmMem = null;

function mkdirp(FS, p) { let c = ''; for (const s of p.split('/').filter(Boolean)) { c += '/' + s; try { FS.mkdir(c); } catch (e) { /* exists */ } } }
function isNoise(t) {
  return /^\s*\[(WASM DEBUG|DEBUG|IFRAME|PROFILE|PWORKER|COMPILE|WASM|MEM)/.test(t) || /^\s*-\s+\/lib\/lean\//.test(t) ||
    /^(Lean 4 WASM running|LEAN_PATH:|Working directory:)/.test(t);
}
function onLine(t, isErr) { if (isNoise(t)) return; if (capture) capture.push({ t, isErr }); }

async function sha256hex(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}

// Fetch one manifest entry with byte-level progress, then verify its sha256.
async function fetchEntry(base, e, shared) {
  const res = await fetch(new URL(e.file, base));
  if (!res.ok) throw new Error(`${e.file}: HTTP ${res.status}`);
  const out = new Uint8Array(e.bytes);
  let off = 0;
  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (off + value.length > out.length) throw new Error(`${e.file}: more than the ${e.bytes} bytes the manifest lists (an HTML fallback page?)`);
    out.set(value, off); off += value.length;
    shared.loaded += value.length;
    const now = performance.now();
    if (now - shared.last > 100) { shared.last = now; post({ type: 'progress', phase: 'download', file: e.file, loaded: shared.loaded, total: shared.total }); }
  }
  if (off !== e.bytes) throw new Error(`${e.file}: ${off} bytes, manifest says ${e.bytes}`);
  const h = await sha256hex(out);
  if (h !== e.sha256) throw new Error(`${e.file}: sha256 ${h} does not match the manifest`);
  return out;
}

function pickMemory(mb) {
  for (const m of [mb, 1024, 768, 512]) {
    try { return new WebAssembly.Memory({ initial: (m * 1048576) / 65536, maximum: 65536, shared: true }); } catch (e) { /* smaller */ }
  }
  return null;
}

async function boot(msg) {
  if (typeof SharedArrayBuffer === 'undefined' || !self.crossOriginIsolated) {
    throw new Error('not cross-origin isolated: serve the page and this worker with Cross-Origin-Opener-Policy: same-origin and Cross-Origin-Embedder-Policy: require-corp');
  }
  const manifestUrl = new URL(msg.manifestUrl, self.location.href);
  const man = await (await fetch(manifestUrl)).json();
  const base = new URL('.', manifestUrl);
  const eng = man.engine.files;
  const total = eng['lean.wasm'].bytes + man.packs.reduce((s, p) => s + p.bytes + p.index_bytes, 0);
  const shared = { loaded: 0, total, last: 0 };
  post({ type: 'progress', phase: 'download', file: '', loaded: 0, total });
  const indexes = await Promise.all(man.packs.map(async (p) => {
    const b = await fetchEntry(base, { file: p.index, bytes: p.index_bytes, sha256: p.index_sha256 }, shared);
    return JSON.parse(new TextDecoder().decode(b));
  }));
  const [wasm, ...packBytes] = await Promise.all([fetchEntry(base, eng['lean.wasm'], shared), ...man.packs.map((p) => fetchEntry(base, p, shared))]);
  post({ type: 'progress', phase: 'download', file: '', loaded: shared.loaded, total });
  wasmMem = pickMemory(msg.memoryMB || 1024);
  const jsUrl = new URL(eng['lean.js'].file, base).href;
  // This build ignores Module.wasmBinary (not in its INCOMING_MODULE_JS_API), so the verified
  // bytes are handed over as a same-origin blob: URL that locateFile returns for lean.wasm.
  const wasmUrl = URL.createObjectURL(new Blob([wasm], { type: 'application/wasm' }));
  self.Module = {
    ...(wasmMem ? { wasmMemory: wasmMem, INITIAL_MEMORY: wasmMem.buffer.byteLength } : {}),
    locateFile: (p) => (p.endsWith('.wasm') ? wasmUrl : new URL(p, base).href),
    mainScriptUrlOrBlob: jsUrl,
    print: (t) => onLine(t, false),
    printErr: (t) => onLine(t, true),
    noInitialRun: true,
    preRun: [function () {
      const FS = Module.FS;
      Module.ENV.LEAN_PATH = '/lib/lean';
      for (const d of ['/lib/lean', '/work', '/bin']) mkdirp(FS, d);
      let n = 0;
      indexes.forEach((idx, k) => {
        const bytes = packBytes[k];
        for (const [p, off, len] of idx.files) {
          const full = '/lib/lean/' + p;
          mkdirp(FS, full.slice(0, full.lastIndexOf('/')));
          FS.writeFile(full, bytes.subarray(off, off + len));
          n++;
        }
      });
      FS.chdir('/work');
      post({ type: 'progress', phase: 'fs', file: '', loaded: n, total: n });
    }],
    onRuntimeInitialized() {
      try {
        Module._lean_initialize_runtime_module();
        Module._lean_initialize();
        Module._lean_io_mark_end_initialization();
        if (Module._lean_init_task_manager) Module._lean_init_task_manager();
        if (Module._lean_enable_initializer_execution) Module._lean_enable_initializer_execution();
        const sp = Module._lean_init_search_path();
        if ((Module.getValue(sp + 7, 'i8') & 0xff) !== 0) throw new Error('lean_init_search_path failed');
        // This build links with EXIT_RUNTIME=1 and main() never runs, so the keepalive counter
        // is 0.  The first callback the runtime thread handles afterwards -- e.g. a message from
        // a pthread that Lean started to generate equation lemmas -- then "exits" the runtime:
        // FS.quit() closes every file descriptor (the EBADF jam) and all pthreads are
        // terminated (the hang).  Holding one keepalive reference prevents it.  The glue is a
        // classic script, so its top-level `runtimeKeepalivePush` is a global here.
        if (typeof runtimeKeepalivePush === 'function') runtimeKeepalivePush();
        else throw new Error('runtimeKeepalivePush not found in lean.js');
        ready = true;
        post({ type: 'ready', ms: Math.round(performance.now() - t0), heapBytes: wasmMem ? wasmMem.buffer.byteLength : null });
      } catch (e) { post({ type: 'fatal', why: 'Lean init failed: ' + (e && e.message || e) }); }
    },
    onAbort(what) { ready = false; post({ type: 'fatal', why: 'aborted: ' + what }); },
  };
  post({ type: 'progress', phase: 'start', file: eng['lean.js'].file, loaded: 0, total: 0 });
  importScripts(jsUrl);
}

function leanString(s) { const p = Module.stringToNewUTF8(s); const o = Module._lean_mk_string(p); Module._free(p); return o; }

function compile(code, path) {
  if (!ready) return { ok: false, crashed: true, why: 'runtime not ready' };
  if (busy) return { ok: false, crashed: false, why: 'busy' };
  busy = true; capture = [];
  const ts = performance.now();
  try {
    const r = Module._lean_wasm_compile(leanString(code), leanString(path || '/work/Page.lean'));
    const tag = Module.getValue(r + 7, 'i8') & 0xff;
    let ioErrorText = null;
    if (tag !== 0) {
      // IO result `error e w`: field 0 at +8 (wasm32 object header); IO.Error.toString gives a
      // Lean string whose bytes start at +20 (header, size, capacity, length).
      try { const s = Module._lean_io_error_to_string(Module.getValue(r + 8, 'i32')); ioErrorText = Module.UTF8ToString(s + 20); }
      catch (e) { ioErrorText = 'unreadable IO error: ' + e; }
    }
    const messages = [], output = [];
    for (const l of capture) {
      if (!l.isErr && l.t.startsWith('{')) { try { messages.push(JSON.parse(l.t)); continue; } catch (e) { /* plain */ } }
      output.push(l.t);
    }
    // Safety net: if the runtime ever "exits" again (see runtimeKeepalivePush above), its file
    // descriptors are gone and every later compile fails with EBADF; tell the page, which
    // then replaces this worker.  With the keepalive reference held this has not happened.
    const stdioClosed = [0, 1, 2].some((fd) => !Module.FS.streams[fd]);
    return { ok: tag === 0, ioError: tag !== 0, ioErrorText, stdioClosed, ms: Math.round(performance.now() - ts), messages, output };
  } catch (e) {
    // A RangeError (JavaScript stack) leaves the runtime unusable; the page restarts this worker.
    return { ok: false, crashed: true, why: String(e && e.message || e).split('\n')[0], ms: Math.round(performance.now() - ts) };
  } finally { capture = null; busy = false; }
}

self.onmessage = (ev) => {
  const m = ev.data || {};
  if (m.type === 'boot') boot(m).catch((e) => post({ type: 'fatal', why: String(e && e.message || e) }));
  else if (m.type === 'compile') post({ type: 'compile_result', id: m.id, ...compile(m.code, m.path) });
  else if (m.type === 'write_file') {
    try { const FS = Module.FS; mkdirp(FS, m.path.slice(0, m.path.lastIndexOf('/'))); FS.writeFile(m.path, m.text); post({ type: 'file_written', id: m.id, bytes: m.text.length }); }
    catch (e) { post({ type: 'file_written', id: m.id, error: String(e) }); }
  } else if (m.type === 'mem') post({ type: 'mem', id: m.id, heapBytes: wasmMem ? wasmMem.buffer.byteLength : null });
};
