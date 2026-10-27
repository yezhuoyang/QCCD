// THE EVALUATION ON AN EMITTED PAGE: the browser's interpreter against the grader's values.
//
//   node tests/eval_page.mjs <page.html> [script.json]  ->  one JSON line on stdout
//
// `tests/eval_parity.mjs` holds the two interpreters to each other on inputs Python builds;
// this holds THE PAGE to them -- the device the editor rebuilt from the page's records, the
// model it prices with, the classes, the closed loops -- by calling `EDITOR.evaluationCheck()`
// on the page as it shipped, and then reads what the head and the Report show.  With a
// script (drive.mjs's step vocabulary) it also reports what an edit does to them.
import fs from 'fs';
import { loadPage } from './shim.mjs';
import { PAGE_HOOK, applyStep } from './drive.mjs';

globalThis.__QCCD_SYNC = true;
// a board page's injected panels read the address (the noise panel opens on `#noise`)
if (typeof globalThis.location === 'undefined') globalThis.location = { hash: '', search: '', href: '', pathname: '' };

const page = process.argv[2];
const script = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], 'utf8')) : [];
const out = { page: page.replace(/\\/g, '/').split('/').pop() };
let byId;
try {
  ({ byId } = loadPage(page, PAGE_HOOK));
} catch (err) {
  console.log(JSON.stringify({ ...out, fatal: String(err && err.message) }));
  process.exit(0);
}
const ED = globalThis.EDITOR, PG = globalThis.__page;
out.ready = !!(ED && ED.ready());
if (!out.ready) { out.why = ED && ED.why(); console.log(JSON.stringify(out)); process.exit(0); }

function shows(label) {
  const v = ED.evaluationView ? ED.evaluationView() : null;
  return {
    label,
    head: (byId['metrics'] && byId['metrics'].innerHTML) || '',
    rows: ED.metricRows(),
    formal: ['cost', 'depth', 'T', 'peak_nbar', 'dacs', 'transport_quanta', 'junction_crossings']
      .reduce((o, k) => { o[k] = ED.formal(k); return o; }, {}),
    hardware: ED.hardware() ? { dacs: ED.hardware().dacs, report: ED.hardware().report || null,
                                over_budget: ED.hardware().over_budget } : null,
    source: v ? v.source : null,
    metrics: v ? v.metrics.length : 0,
    requirements: v ? v.requirements.map(r => [r.id, r.verdict]) : [],
    budget: v && v.budget && v.budget.order ? v.budget.order.length : 0,
    report: (byId['report'] && byId['report'].innerHTML) || '',
  };
}

let t = Date.now();
out.check = ED.evaluationCheck();
out.check_ms = Date.now() - t;
out.shipped = shows('shipped');
out.steps = [];
for (const step of script) {
  const rec = applyStep(ED, PG, step);
  if (rec && rec.error) { out.steps.push({ label: step.label || step.do, error: rec.error }); continue; }
  t = Date.now();
  const s = shows(step.label || step.do);
  s.ms = Date.now() - t;
  out.steps.push(s);
}
console.log(JSON.stringify(out));
