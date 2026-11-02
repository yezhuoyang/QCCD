// The Debug page's Python: the grader's own rule language, run in the browser.
//
// A module worker, so a long check never freezes the page.  It loads Pyodide from beside it
// (debug/pyodide/, a pinned release the site build copies in) and qccd's own packages
// (debug/qccd-py.zip), once, then answers calls to `qccd.debug.web`: every function there
// takes and returns JSON text, so nothing but strings crosses into the page.
//
//   page -> worker   {id, fn, args}             fn: open_session | call | rulesets | render_page
//   worker -> page   {id, ok: true, out}        out: the function's JSON text
//                    {id, ok: false, error}     the runtime itself failed (a refusal is in `out`)
//                    {progress: "..."}          what the first load is doing
import { loadPyodide } from './pyodide/pyodide.esm.js';   // the release's pyodide.mjs (debugpage.SERVED)

const BASE = new URL('./', import.meta.url).href;
let booting = null, W = null;

function say(text) { self.postMessage({ progress: text }); }
// a runtime that cannot start (a policy refusing WebAssembly, a file that did not load) says so,
// instead of leaving the page waiting
function fatal(e) { self.postMessage({ fatal: String((e && (e.message || e.reason)) || e || 'the rule language stopped') }); }
self.addEventListener('error', fatal);
self.addEventListener('unhandledrejection', function (e) { fatal(e.reason || e); });

async function boot() {
  const t0 = performance.now();
  say('starting Python (about 13 MB, once; the browser keeps it)');
  const py = await loadPyodide({ indexURL: BASE + 'pyodide/' });
  say('loading the rule language');
  const res = await fetch(BASE + 'qccd-py.zip');
  if (!res.ok) throw new Error('the rule language did not load (' + res.status + ')');
  py.unpackArchive(await res.arrayBuffer(), 'zip', { extractDir: '/home/pyodide/qccd-py' });
  py.runPython("import sys\nsys.path.insert(0, '/home/pyodide/qccd-py')\nimport qccd.debug.web");
  W = py.pyimport('qccd.debug.web');
  say('ready in ' + Math.round((performance.now() - t0) / 100) / 10 + ' s');
}

self.onmessage = async (e) => {
  const m = e.data || {};
  try {
    if (!booting) booting = boot();
    await booting;
    if (!m.fn) { self.postMessage({ id: m.id, ok: true, out: '{"ok": true}' }); return; }
    const f = W[m.fn];
    if (typeof f !== 'function') throw new Error('no function ' + m.fn);
    const out = f(...(m.args || []).map(String));
    self.postMessage({ id: m.id, ok: true, out: String(out) });
  } catch (err) {
    self.postMessage({ id: m.id, ok: false, error: String((err && err.message) || err) });
  }
};
