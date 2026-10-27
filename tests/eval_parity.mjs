// The JS half of the EVALUATION parity harness.
//
//   node tests/eval_parity.mjs <cases.json>   ->  one JSON line on stdout
//
// `tests/test_eval_parity.py` evaluates every case with the Python interpreter of the
// evaluation language (qccd/evalset) and writes the inputs and its answers here; this file
// evaluates the same parse trees with qccd/viz/js/evaluate.js over the engine's own replay
// and reports every metric, requirement and budget line, for Python to compare.  Nothing is
// stored between runs.
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
for (const f of ['qccd/viz/js/edit.js', 'qccd/viz/engine.js', 'qccd/viz/js/evaluate.js']) {
  try { new Function(fs.readFileSync(path.join(root, f), 'utf8'))(); }
  catch (e) { console.log(JSON.stringify({ fatal: f + ' failed to load: ' + e.message })); process.exit(2); }
}
const Q = globalThis.QCCD;
Q.setSchema(cases.schema_blob);
if (globalThis.QCCDEdit && cases.schema_blob.bounds) globalThis.QCCDEdit.setBounds(cases.schema_blob.bounds);
Q.setTemplates(cases.templates, cases.template_default);
if (!Q.evaluation) { console.log(JSON.stringify({ fatal: 'evaluate.js did not register' })); process.exit(3); }

const out = [];
for (const c of cases.cases) {
  const r = Q.applyProgram(c.calls);
  if (r.error) { out.push({ name: c.name, error: 'architecture refused: ' + r.error.message }); continue; }
  const st = r.ok, dev = st.device;
  // the page's own model on its own clock; another clock is the grader's model on that table
  // (`qccd.evalset.Evaluator.under` replays under `corrected_model(clock)`), as the editor's
  // `clockModel` builds it
  const opts = (table) => table === c.clock
    ? Object.assign({}, c.model, { policy: { table, objective: 'fastest' } })
    : { kind: 'corrected', corner_hops: 1, junction_min_degree: 3, length_scaling: false, pitch: 1.0,
        include_anomalous: true, anomalous_per_ms: c.model.anomalous_per_ms, policy: { table, objective: 'fastest' } };
  const mk = (table) => {
    const m = Q.makeModel(st.primitives, Q.degrees(dev), Q.cornerEndpoints(dev), dev.segments, opts(table));
    m._pair = Q.pairIndex(dev);
    return m;
  };
  try {
    const t0 = Date.now();
    const res = Q.evaluation.evaluatePage(c.payload, c.frames, c.loops, mk(c.clock), c.classes || {}, dev,
      { control: st.control, budget: st.budget, primitives: st.primitives, species: st.species },
      { closedLoops: c.closed_loops, mk });
    const metrics = {};
    for (const m of res.metrics) metrics[m.name] = m.missing !== undefined ? { missing: m.missing } : m.value;
    const budget = {};
    if (res.budget && res.budget.lines) for (const k of res.budget.order) budget[k] = res.budget.lines[k];
    out.push({ name: c.name, metrics, requirements: res.requirements, budget,
               budget_error: res.budget && res.budget.error, ms: Date.now() - t0, frames: c.frames.length });
  } catch (e) {
    out.push({ name: c.name, error: (e && e.message) || String(e), stack: e && e.stack && e.stack.split('\n').slice(0, 4).join(' | ') });
  }
}
console.log(JSON.stringify({ results: out }));
