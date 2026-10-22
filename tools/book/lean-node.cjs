#!/usr/bin/env node
// Run the wasm `lean` (cauli fork) under Node with real directories mounted via NODEFS.
// Adapted from cauli/lean4-wasm-in-browser scripts/lean-wasm-node.cjs (Apache-2.0).
//
// usage: node --stack-size=8192 lean-node.cjs <lean.js> <libdir> <workdir> [lean args...]
//   <lean.js>  path to the emscripten glue (lean.wasm next to it)
//   <libdir>   real directory mounted at /lib/lean (LEAN_PATH; may hold several roots
//              given as extra LEAN_PATH entries via env LEAN_NODE_EXTRA = "real1;real2")
//   <workdir>  real directory mounted at /work (cwd)
// env LEAN_NODE_MEMORY_MB  initial shared memory (default: glue's default)
// env LEAN_NODE_EXTRA      ';'-separated real dirs mounted at /extra0, /extra1 ... and appended to LEAN_PATH
const path = require('path');
const fs = require('fs');
const vm = require('vm');
const wt = require('worker_threads');

// By default the runtime runs in a worker_thread, as it does in a browser Web Worker.
// On Node's main thread emscripten treats the thread as the "main browser thread": its
// futex waits busy-spin and proxied syscalls queue behind them, which made a plain Init
// import take 100-210 s. LEAN_NODE_MAIN=1 keeps the old behaviour.
if (wt.isMainThread && process.env.LEAN_NODE_MAIN !== '1') {
  const w = new wt.Worker(__filename, { argv: process.argv.slice(2), workerData: 'lean-host', stdout: false, stderr: false });
  w.on('message', (m) => { if (m && m.type === 'exit') process.exitCode = m.code; });
  w.on('error', (e) => { console.error('worker error', e); process.exitCode = 3; });
  w.on('exit', () => { setTimeout(() => process.exit(process.exitCode || 0), 50); });
  return;
}
const inWorker = !wt.isMainThread;

const [leanJsArg, libDirArg, workDirArg, ...leanArgs] = process.argv.slice(2);
if (!leanJsArg || !libDirArg || !workDirArg) {
  console.error('usage: lean-node.cjs <lean.js> <libdir> <workdir> [lean args...]');
  process.exit(2);
}
const leanJs = path.resolve(leanJsArg);
const libLean = path.resolve(libDirArg);
const realWork = path.resolve(workDirArg);
const extra = (process.env.LEAN_NODE_EXTRA || '').split(';').filter(Boolean).map((p) => path.resolve(p));
const memoryMb = process.env.LEAN_NODE_MEMORY_MB ? Number(process.env.LEAN_NODE_MEMORY_MB) : null;
const memoryConfig = memoryMb === null ? {} : {
  wasmMemory: new WebAssembly.Memory({ initial: memoryMb * 16, maximum: 65536, shared: true }),
  INITIAL_MEMORY: memoryMb * 1024 * 1024,
};

if (!inWorker) process.chdir('/');
process.argv[1] = '/bin/lean';
const t0 = Date.now();

// LEAN_NODE_LATE_TASKMGR=N (use with `-j0`): run the import with no task manager, so the
// .olean reads happen on this thread instead of being proxied from a pthread (the cause of
// the 170-250 s imports), then start an N-worker task manager when finalizeImport announces
// itself -- before anything calls IO.Promise.new, which panics without one.
const lateTm = Number(process.env.LEAN_NODE_LATE_TASKMGR || 0);
let tmStarted = false;
function hook(text) {
  if (lateTm > 0 && !tmStarted && /^\[DEBUG:PROGRESS\] Loading \d+ modules/.test(text)) {
    tmStarted = true;
    Module._lean_init_task_manager_using(lateTm);
    if (process.env.LEAN_NODE_TIME) console.error(`[lean-node] task manager (${lateTm} workers) started at ${Date.now() - t0} ms`);
  }
}

globalThis.Module = {
  arguments: leanArgs,
  print: (t) => { hook(t); process.stdout.write(t + '\n'); },
  printErr: (t) => { hook(t); process.stderr.write(t + '\n'); },
  ...memoryConfig,
  locateFile: (file) => path.join(path.dirname(leanJs), file),
  mainScriptUrlOrBlob: leanJs,
  preRun: [function () {
    const FS = Module.FS;
    const NODEFS = FS.filesystems.NODEFS;
    const mkdirTree = (p) => {
      let cur = '';
      for (const part of p.split('/').filter(Boolean)) {
        cur += '/' + part;
        try { FS.mkdir(cur); } catch (e) { /* exists */ }
      }
    };
    for (const d of ['/lib/lean', '/work', '/bin', '/workspace']) mkdirTree(d);
    // LEAN_NODE_MEMFS=1: copy library trees into MEMFS (as the browser worker does)
    // instead of mounting them through NODEFS.
    const useMem = process.env.LEAN_NODE_MEMFS === '1';
    const copyTree = (real, virt) => {
      mkdirTree(virt);
      for (const ent of fs.readdirSync(real, { withFileTypes: true })) {
        const r = path.join(real, ent.name), v = virt + '/' + ent.name;
        if (ent.isDirectory()) copyTree(r, v);
        else FS.writeFile(v, fs.readFileSync(r));
      }
    };
    const tc = Date.now();
    if (useMem) copyTree(libLean, '/lib/lean'); else FS.mount(NODEFS, { root: libLean }, '/lib/lean');
    FS.mount(NODEFS, { root: realWork }, '/work');
    const lp = ['/lib/lean'];
    extra.forEach((real, i) => { const v = '/extra' + i; mkdirTree(v); if (useMem) copyTree(real, v); else FS.mount(NODEFS, { root: real }, v); lp.push(v); });
    if (useMem && process.env.LEAN_NODE_TIME) console.error(`[lean-node] MEMFS copy ${Date.now() - tc} ms`);
    Module.ENV.LEAN_PATH = lp.join(':');
    process.env.LEAN_PATH = lp.join(':');
    FS.chdir('/work');
    // Windows: lean_main's Node branch mounts NODEFS roots "/home" and "/tmp" (C:\home,
    // C:\tmp, absent) and chdirs to process.cwd() ("C:\"). Redirect both to scratch dirs.
    const scratch = path.join(realWork, '.lean-node');
    for (const d of ['home', 'tmp']) fs.mkdirSync(path.join(scratch, d), { recursive: true });
    const origMount = FS.mount.bind(FS);
    FS.mount = (type, opts, mountpoint) => {
      if (type === NODEFS && opts && (opts.root === '/home' || opts.root === '/tmp')) {
        mkdirTree(mountpoint);
        return origMount(type, { root: path.join(scratch, opts.root.slice(1)) }, mountpoint);
      }
      return origMount(type, opts, mountpoint);
    };
    process.cwd = () => '/work';
  }],
  onExit: (code) => {
    process.exitCode = code;
    if (inWorker) wt.parentPort.postMessage({ type: 'exit', code });
    if (process.env.LEAN_NODE_TIME) console.error(`[lean-node] exit ${code} after ${Date.now() - t0} ms, rss ${(process.memoryUsage().rss / 1048576).toFixed(0)} MB`);
  },
  onAbort: (what) => { console.error('ABORT:', what); if (inWorker) wt.parentPort.postMessage({ type: 'exit', code: 3 }); process.exit(3); },
};

globalThis.require = require;
globalThis.__filename = '/bin/lean.js';
globalThis.__dirname = '/bin';

vm.runInThisContext(fs.readFileSync(leanJs, 'utf8'), { filename: leanJs });
