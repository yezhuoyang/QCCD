// THE DESIGN-TOOL HARNESS.
//
//   node tests/studio.mjs <page.html> [script.json] -> one JSON object on stdout
//
// It drives an emitted page through `tests/drive.mjs`'s ONE step vocabulary and reports
// what the page would tell a user: whether editing is available, what it refuses, what it
// checked, and -- the load-bearing part -- what it says about checks that did NOT run.
//
// WHAT THIS EXISTS TO CATCH, measured on this tree before the fix:
//
//   * an empty page printing "self-check ... agrees with the Python verifier to 0.0e+0
//     quanta per ion" -- a green tick for a loop that never executed, in the one panel
//     that asserts the page is trustworthy;
//   * `Step 1 / 0 - undefined` as the first sentence a new user reads;
//   * an empty `<div>` where the rule verdicts go, which reads as "nothing wrong";
//   * `exportJson()` handing over a document Python refuses with 24 structural errors
//     while the page reported 576 DACs for a machine whose ion capacity was zero.
//
// It does NOT stub `localStorage`, and `tests/shim.mjs` must not either: the design tool's
// most important guarantee is that your work survives, and asserting that against a fake
// would prove nothing.  `snapshot()`/`restore()` are pure functions and the round trip is
// asserted through `EDITOR.digest()`, which exercises the real applier.
import fs from 'fs';
import { loadPage } from './shim.mjs';
import { PAGE_HOOK, applyScript } from './drive.mjs';

globalThis.__QCCD_SYNC = true;

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

function evidence(label) {
  const side = PG.side() || '';
  const cov = PG.coverage();
  const pr = ED.price();
  let exportErr = null, exported = null;
  try { exported = ED.exportJson(); } catch (err) { exportErr = String(err.message); }
  return {
    label,
    ready: ED.ready(), why: ED.why(),
    nodes: PG.nodes().length, segs: PG.segs().length,
    loops: Object.keys(PG.loops() || {}).length,
    frames: PG.nframes(),
    layout: (({ W, H, sx, sy, g, n, wide }) => ({ W, H, sx, sy, g, n, wide }))(PG.layout()),
    data_layout: (byId['row'].attrs || {})['data-layout'] || null,
    // THE THREE HONESTY SENTENCES, matched on the page's own words
    says_agrees_with_python: /agrees with the Python verifier/.test(side),
    says_nothing_to_check: /nothing to check this one against/.test(side),
    says_no_programme_replayed: /no programme has been replayed/.test(side),
    says_all_pass: /all rules pass/i.test(side),
    says_rule_count: /rules checked in this browser/.test(side),
    status: byId['status'].innerHTML || '',
    status_invents_a_step: /<b>Step 1<\/b> \/ 0/.test(byId['status'].innerHTML || ''),
    // the verdict coverage, three states per rule
    coverage: cov,
    n_checked: cov.filter(c => c[1] === 'checked' || c[1] === 'failed').length,
    n_unchecked: cov.filter(c => c[1] === 'unchecked' || c[1] === 'partial').length,
    // what leaves the page
    schema_errors: ED.schemaErrors(),
    export_error: exportErr,
    export_bytes: exported ? exported.length : 0,
    price: pr && !pr.blocked
      ? { cost: pr.totals.cost, steps: pr.totals.steps, us: pr.totals.us,
          frameChecked: pr.frameChecked, frameDrift: pr.frameDrift }
      : null,
    blocked: pr && pr.blocked ? pr.blocked.map(b => b.kind) : null,
    hardware: ED.hardware() ? { dacs: ED.hardware().dacs,
                                total_capacity: ED.hardware().total_capacity } : null,
    program_statements: ED.program().length,
    program_errors: ED.programErrors().map(e => e.message),
    authored: ED.authored(),
    rules: ED.rules() ? { by_rule: ED.rules().by_rule,
                          messages: (ED.rules().messages || []).map(m => m.message),
                          oracle: ED.rules().oracle } : null,
    digest: ED.digest(),
  };
}

out.base = evidence('base');
out.steps = applyScript(ED, PG, script).map((rec, i) => ({ ...rec, after: evidence('s' + i) }));

// -------- the layout regime, at three widths and both aspect ratios -------------------
// `applyLayout` is called rather than inspected: the point of the change from a CSS class
// to an attribute is that the regime became READABLE, and `classList` is a no-op here.
out.layout_regimes = {};
for (const w of [800, 1000, 1400]) {
  globalThis.window.innerWidth = w;
  out.layout_regimes[String(w)] = PG.applyLayout();
}
globalThis.window.innerWidth = 1600;

// -------- persistence: a pure round trip through the one applier ----------------------
const d0 = JSON.stringify(ED.digest());
const p0 = JSON.stringify((ED.price() || {}).totals || null);
const snap = ED.snapshot();
out.snapshot_bytes = JSON.stringify(snap).length;
out.snapshot_kind = snap.kind;
out.autosave_ok = ED.autosave();
out.autoload_matches = JSON.stringify(ED.autoload()) === JSON.stringify(snap);
// PERTURB FIRST.  Restoring the state you just snapshotted is a no-op whatever `restore`
// does, so the round trip has to cross a real difference or it proves nothing -- which is
// exactly what the mutation guard for this test checks.
ED.setMode('edit');
const perturb = ED.addNodeAt(97.0, 97.0, { id: 'ZZ_probe', zone: 'data', capacity: 2 });
out.perturb_ok = perturb.ok;
out.perturbed_digest_differs = JSON.stringify(ED.digest()) !== d0;
const back = ED.restore(snap);
out.restore_ok = back.ok;
out.restore_same_digest = JSON.stringify(ED.digest()) === d0;
out.restore_same_price = JSON.stringify((ED.price() || {}).totals || null) === p0;

// -------- the palette, generated ------------------------------------------------------
const pal = ED.palette();
out.palette = pal.map(e => ({ type: e.type, kind: e.kind, verb: e.verb,
                              fields: e.fields.map(f => f.name),
                              inert: e.fields.filter(f => f.inert).map(f => f.name) }));

console.log(JSON.stringify(out));
