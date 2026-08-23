// The JS half of the ENGINE parity harness.
//
//   node tests/parity.mjs <cases.json>   ->  one JSON line on stdout
//
// `tests/test_engine_parity.py` computes the reference answers with LIVE PYTHON on every
// run, writes them into <cases.json> alongside the inputs, and this file recomputes the
// same answers with `qccd/viz/engine.js` and diffs them.  Nothing is stored between runs,
// so there is no golden vector to go stale: the only way to make it pass is to make the
// two implementations agree.
//
// The engine is loaded with `new Function(src)()` -- exactly what `tests/census.mjs`
// already does to a whole emitted page -- so the bytes under test are the bytes on disk,
// which `test_the_page_inlines_this_exact_engine` separately proves are the bytes in the
// page.  There is no transform anywhere in that chain and therefore no transformed-versus-
// source gap to test.
//
// TOLERANCE IS ZERO.  Not 1e-9.  After the portable-arithmetic patch `compute_layout` uses
// only + - * /, sqrt, min, max, floor, ceil, comparisons and a total-order sort; every one
// of those is correctly rounded and identically specified by IEEE-754 binary64 in CPython
// and V8, and neither contracts to FMA nor uses x87 extended precision on x86-64.  The set
// of reachable answers is a single bit pattern.  An epsilon here would hide a defect
// rather than absorb noise -- and needing one is itself the alarm that a non-portable
// idiom crept back in.
//
// Mismatches are reported in ULP DISTANCE, not relative error, so a report is actionable
// ("2 ulp in `g` on fuzz:ring:1136") rather than "close enough".
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');

const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

// edit.js first: engine.js delegates degree / corner / corner_endpoints to it rather than
// keeping a third copy of code that already has its own differential test.
const editSrc = fs.readFileSync(cases.edit_js || path.join(root, 'qccd', 'viz', 'js', 'edit.js'), 'utf8');
const engineSrc = fs.readFileSync(cases.engine || path.join(root, 'qccd', 'viz', 'engine.js'), 'utf8');
try {
  new Function(editSrc)();
  new Function(engineSrc)();
} catch (e) {
  console.log(JSON.stringify({ fatal: 'engine failed to load: ' + e.message }));
  process.exit(2);
}
const Q = globalThis.QCCD;
if (Q && cases.schema_blob) {
  // The engine keeps NO copy of the schema; it gets the one `qccd/arch/schema.py`
  // exported, exactly as the emitted page hands it over at `D.schema`.  No default and no
  // fallback -- a corpus without it must fail loudly rather than let the mirror guess.
  Q.setSchema(cases.schema_blob);
  if (globalThis.QCCDEdit && cases.schema_blob.bounds) {
    globalThis.QCCDEdit.setBounds(cases.schema_blob.bounds);
  }
}
if (Q && cases.templates) {
  // Same contract as the schema: `Machine.ring(..., template=...)` reads a file off disk
  // in Python, so the browser is handed the template as the RECORDS THAT DECLARE IT.  No
  // default and no fallback -- without this the `from_template` / `from_device` /
  // `d.build()` listing shapes are unexecutable and the bucket would pass by vacuum.
  Q.setTemplates(cases.templates, cases.template_default);
}
if (!Q || Q.ENGINE !== 'qccd-engine/1') {
  // An engine that failed to publish itself must be a FAILURE, never a silent skip.
  console.log(JSON.stringify({ fatal: 'globalThis.QCCD is missing or is the wrong version' }));
  process.exit(3);
}

// ---------------------------------------------------------------- ulp distance
const buf = new DataView(new ArrayBuffer(8));
function bits(x) {
  buf.setFloat64(0, x);
  const hi = buf.getUint32(0), lo = buf.getUint32(4);
  let v = BigInt(hi) * 4294967296n + BigInt(lo);
  // map the sign-magnitude layout onto a monotone ordering so subtraction is an ulp count
  return (hi & 0x80000000) ? -(v & 0x7fffffffffffffffn) : v;
}
function ulps(a, b) {
  if (Object.is(a, b)) return 0;
  if (typeof a !== 'number' || typeof b !== 'number') return Infinity;
  if (!isFinite(a) || !isFinite(b)) return Infinity;
  const d = bits(a) - bits(b);
  return Number(d < 0n ? -d : d);
}

let compared = 0, mismatched = 0, worst = null, worstUlp = -1;
// A handful of DISTINCT failures, not a hundred copies of one: a drift usually hits every
// case of one shape, and a report that showed the first hundred of those would hide the
// second, unrelated drift underneath it.
const sample = [], seenKinds = new Map();

function note(kind, name, key, py, js) {
  mismatched++;
  const u = ulps(py, js);
  if (u > worstUlp || worst === null) {
    worstUlp = u;
    worst = { kind, case: name, key, py, js, ulp: isFinite(u) ? u : null };
  }
  const sig = kind + '|' + String(key).replace(/[0-9]+/g, '#');
  const n = (seenKinds.get(sig) || 0) + 1;
  seenKinds.set(sig, n);
  if (n <= 2 && sample.length < 24) {
    sample.push({ kind, case: name, key, py: clip(String(py)), js: clip(String(js)) });
  }
}

// Exact equality, with -0 distinguished from 0: Object.is is the right primitive here
// because the two ARE different bit patterns and `_bows` reads the sign of a dot product.
function same(a, b) {
  if (typeof a === 'number' && typeof b === 'number') return Object.is(a, b);
  return a === b;
}

function diffFlat(kind, name, py, js) {
  const keys = new Set([...Object.keys(py), ...Object.keys(js)]);
  for (const k of keys) {
    if (!(k in py) || !(k in js)) { compared++; note(kind, name, k + ' (key present on one side only)', py[k], js[k]); continue; }
    if (k === 'bows') {
      const bp = py[k] || {}, bj = js[k] || {};
      const bk = new Set([...Object.keys(bp), ...Object.keys(bj)]);
      for (const s of bk) {
        compared++;
        if (!(s in bp) || !(s in bj) || !same(bp[s], bj[s])) note(kind, name, 'bows.' + s, bp[s], bj[s]);
      }
      continue;
    }
    compared++;
    if (!same(py[k], js[k])) note(kind, name, k, py[k], js[k]);
  }
}

// Ordered, field-by-field structural comparison.  ORDER IS LOAD-BEARING and comparing sets
// would pass a device that then lays out differently: `_bows`'s centroid is an
// order-dependent float sum whose winner is the first strict minimum in declaration order,
// and `Device.to_json` emits dict-insertion order into a byte-compared file.
function diffDevice(name, py, js) {
  for (const part of ['nodes', 'segments', 'loops']) {
    const pk = Object.keys(py[part] || {}), jk = Object.keys(js[part] || {});
    compared++;
    if (pk.length !== jk.length || pk.some((k, i) => k !== jk[i])) {
      note('gen', name, part + ' id order', pk.join(','), jk.join(','));
      continue;
    }
    for (const id of pk) {
      const a = py[part][id], b = js[part][id];
      const fk = new Set([...Object.keys(a), ...Object.keys(b)]);
      for (const f of fk) {
        compared++;
        const av = a[f], bv = b[f];
        if (Array.isArray(av) || Array.isArray(bv)) {
          const as = JSON.stringify(av), bs = JSON.stringify(bv);
          if (as !== bs) note('gen', name, `${part}.${id}.${f}`, as, bs);
        } else if (!same(av === undefined ? null : av, bv === undefined ? null : bv)) {
          note('gen', name, `${part}.${id}.${f}`, av, bv);
        }
      }
    }
  }
  compared++;
  if (py.generator !== js.generator) note('gen', name, 'generator', py.generator, js.generator);
  compared++;
  const pp = JSON.stringify(py.params), jp = JSON.stringify(js.params);
  if (pp !== jp) note('gen', name, 'params', pp, jp);
}

// ------------------------------------------------------------------- 1. generators
for (const c of cases.generators || []) {
  let dev;
  try {
    dev = Q.expandGenerator(c.generator, c.params, c.zone_types || null);
  } catch (err) {
    compared++;
    if (c.error === undefined || c.error === null) {
      note('gen', c.name, 'unexpected refusal', null, err.name + ': ' + err.message);
    } else if (c.error !== err.message) {
      note('gen', c.name, 'error message', c.error, err.message);
    }
    continue;
  }
  if (c.error !== undefined && c.error !== null) {
    compared++;
    note('gen', c.name, 'accepted an edit Python refused', c.error, null);
    continue;
  }
  diffDevice(c.name, c.device, JSON.parse(JSON.stringify(dev)));
  // and the layout OF that device, computed from the JS expansion -- proving the
  // composition, not just the two halves
  if (c.layout) {
    diffFlat('layout', c.name, c.layout, Q.layoutOf(dev));
    if (c.layout_raw) diffFlat('layout_raw', c.name, c.layout_raw, Q.layoutOf(dev, { raw: true }));
  }
}

// ------------------------------------------------------------------- 2. explicit layouts
// The drag bucket lives here: arbitrary doubles, which is what a drag actually produces,
// not lattice points.
for (const c of cases.layouts || []) {
  let got, gotRaw;
  try {
    got = Q.computeLayout(c.nodes, c.segments || []);
    gotRaw = c.layout_raw ? Q.computeLayout(c.nodes, c.segments || [], { raw: true }) : null;
  } catch (err) {
    compared++;
    note('layout', c.name, 'threw', null, err.message);
    continue;
  }
  diffFlat('layout', c.name, c.layout, got);
  if (gotRaw) diffFlat('layout_raw', c.name, c.layout_raw, gotRaw);
}

// ------------------------------------------------------------------- 3. site/pad tiling
// The two mirrors that already existed in `render.py` UNCHECKED: the page computed
// `siteLen` inline with no int() where `layout.site_length` does `int(cap or 0)`, and it
// tiled pads at 0.34*g while `layout.pad_tiling` derives k from 0.50*g.  Neither was
// covered by anything.  Folding them in here is what makes this harness cover the parts
// that had already drifted rather than certifying the parts that were fine.
for (const c of cases.marks || []) {
  compared++;
  const sl = Q.siteLength(c.cap, c.g);
  if (!same(c.site_length, sl)) note('marks', c.name, 'site_length', c.site_length, sl);
  const pt = Q.padTiling(c.length, c.g);
  for (let i = 0; i < 3; i++) {
    compared++;
    if (!same(c.pad_tiling[i], pt[i])) note('marks', c.name, 'pad_tiling[' + i + ']', c.pad_tiling[i], pt[i]);
  }
}

// ------------------------------------------------------------------- 4. pyRepr
for (const c of cases.reprs || []) {
  compared++;
  const got = Q.pyRepr(new Q.PyFloat(c.v));
  if (got !== c.repr) note('repr', 'repr', String(c.v), c.repr, got);
}

// ------------------------------------------------------------------- 5. hardware
for (const c of cases.hardware || []) {
  let got;
  try {
    got = Q.hardwareReport(c.device, c.control || {}, c.budget || {}, c.name_arg || '');
  } catch (err) {
    compared++; note('hw', c.name, 'threw', null, err.message); continue;
  }
  for (const k of Object.keys(c.report)) {
    compared++;
    const a = c.report[k], b = got[k];
    if (k === 'degree_histogram' || k === 'over_budget') {
      if (JSON.stringify(a) !== JSON.stringify(b)) note('hw', c.name, k, JSON.stringify(a), JSON.stringify(b));
    } else if (!same(a, b)) note('hw', c.name, k, a, b);
  }
}

// ------------------------------------------------------------------- 6. listing replay
// Feed the identical `ArchLine.call` records to the JS interpreter and compare the whole
// serialized document.  Harvested from `call` ALONE, never reading `target` -- which is
// precisely the assertion that catches a geometry statement whose generator name lives
// only in `target`.
for (const c of cases.programs || []) {
  const r = Q.applyProgram(c.calls);
  compared++;
  if (r.error) {
    if (!c.error) note('program', c.name, 'refused', null, r.error.message + ' at ' + r.error.index);
    else if (r.error.index !== c.error.index) note('program', c.name, 'error index', c.error.index, r.error.index);
    continue;
  }
  if (c.error) { note('program', c.name, 'accepted what Python refused', c.error.message, null); continue; }
  const doc = canon(Q.serialize(r.ok));
  const want = canon(c.doc);
  if (doc !== want) {
    // localise: report the first differing top-level key rather than a 300 KB blob
    const a = JSON.parse(doc), b = JSON.parse(want);
    let where = 'document';
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) { where = k; break; }
    }
    note('program', c.name, where, clip(JSON.stringify(b[where])), clip(JSON.stringify(a[where])));
  }
}

// ------------------------------------------------------------------- 8. pricing
// THE BUCKET THAT WAS MISSING.  A drag re-prices, and until now nothing compared the
// re-pricer against Python at all: `priceFrames`, `makeModel`, `pickPoint` and their
// sixteen helpers were never executed by this harness.  Two real defects shipped through
// that hole.  Compare every total, every quanta component, the transit count, the peak
// n-bar AND the per-ion deposit -- the last one because the two accumulators (running
// n-bar, which a cool zeroes, and lifetime deposit, which it does not) are easy to
// conflate and every TOTAL still looks right when you do.
for (const c of cases.pricing || []) {
  const r = Q.applyProgram(c.calls);
  compared++;
  if (r.error) { note('price', c.name, 'architecture refused', null, r.error.message); continue; }
  const dev = r.ok.device;
  let priced;
  try {
    const model = Q.makeModel(r.ok.primitives, Q.degrees(dev), Q.cornerEndpoints(dev),
                              dev.segments, c.model);
    model._pair = Q.pairIndex(dev);      // exactly what editor.js attaches before pricing
    priced = Q.priceFrames(c.frames, c.loops, model, c.classes || {});
  } catch (err) {
    compared++;
    note('price', c.name, 'threw', null, err.name + ': ' + err.message);
    continue;
  }
  for (const k of ['cost', 'steps', 'us']) {
    compared++;
    if (!same(c.totals[k], priced.totals[k])) note('price', c.name, 'totals.' + k, c.totals[k], priced.totals[k]);
  }
  compared++;
  if (!same(c.transits, priced.transits)) note('price', c.name, 'transits', c.transits, priced.transits);
  // TOLERANCE ZERO EVERYWHERE EXCEPT HERE, and the exception is stated rather than
  // hidden. `cost`, `steps`, `us`, `transits` and every quanta COMPONENT are bit-exact.
  // The per-ion and peak n-bar accumulators are not: both sides add the same terms, but
  // Python keeps five per-component running totals and reduces them, while the mirror
  // keeps one running scalar per ion, so ~10^4 additions land in a different ORDER and
  // the last one or two bits differ. That is a property of the two shapes, not of the
  // arithmetic -- no reordering-free formulation exists without making one side carry the
  // other's data structure. So it is bounded at 4 ulp and any drift beyond that fails,
  // which still catches every defect this bucket was built for: the two that shipped were
  // 5,048 against 5,444 and 1,805 against 55.17.
  // The bound SCALES WITH THE ACCUMULATION, because that is what drives it. Python keeps
  // five per-component running totals and reduces them; the mirror keeps one running
  // scalar per ion. Same terms, different ORDER, so the last bits diverge -- and the
  // divergence grows with the number of additions, not with anything about the physics.
  //
  //   142-frame rotation        : 0 ulp
  //   1,975-frame gated deck run: 241 ulp on 1775.67, i.e. 3.1e-14 relative
  //
  // A fixed 64 covered the short programmes and would have had to be nudged upward the
  // first time a longer one appeared -- which is how an epsilon stops meaning anything.
  // One quarter of the frame count is comfortably above what was measured and still
  // eleven orders below any number a designer would act on. The defects this bucket
  // exists to catch were 5,048 against 5,444 and 1,805 against 55.17, both astronomically
  // outside any ulp bound at any length.
  const ACC_ULP = Math.max(64, Math.ceil((c.frames || []).length / 4));
  const acc = (key, py, js) => {
    compared++;
    if (same(py, js)) return;
    const u = ulps(py, js);
    if (!(isFinite(u) && u <= ACC_ULP)) note('price', c.name, key, py, js);
  };
  acc('peak n-bar', c.peak, priced.peak);
  for (const k of Object.keys(c.comp)) {
    compared++;
    if (!same(c.comp[k], priced.comp[k])) note('price', c.name, 'comp.' + k, c.comp[k], priced.comp[k]);
  }
  // per ion, not just the sum: a sign error that cancels across ions is invisible in totals
  const ions = new Set([...Object.keys(c.per_ion), ...Object.keys(priced.life || {})]);
  for (const ion of ions) {
    const before = mismatched;
    acc('per-ion n-bar ' + ion, c.per_ion[ion], (priced.life || {})[ion]);
    if (mismatched > before) break;   // one is enough to localise; not 168 lines
  }
}

// ------------------------------------------------------------------- 9. mutate verbs
// move_site IS the drag.  set_site_capacity and set_segment_length are the two retunes an
// editor offers next to it.  All three were unexecuted here.  Apply the identical record
// through both interpreters and diff the whole document AND the layout -- the layout
// because a drag's entire purpose is to move geometry, and `g` decides every drawn mark.
for (const c of cases.mutate || []) {
  const r = Q.applyProgram(c.calls);
  compared++;
  if (r.error) { note('mutate', c.name, 'setup refused', null, r.error.message); continue; }
  let st = r.ok;
  let failed = null;
  for (let i = 0; i < c.edits.length; i++) {
    let out;
    // the interpreter signals a refusal by THROWING, not by returning {error}
    try { out = Q.apply(st, c.edits[i]); }
    catch (err) { failed = { index: i, message: err.message }; break; }
    if (out && out.error) { failed = { index: i, message: out.error.message }; break; }
    st = out;                 // applyCall returns the STATE, not {ok}
  }
  compared++;
  if (failed) {
    if (!c.error) note('mutate', c.name, 'refused an edit Python accepted', null,
                       failed.message + ' at ' + failed.index);
    else if (c.error.index !== failed.index) note('mutate', c.name, 'error index', c.error.index, failed.index);
    else if (c.error.message !== failed.message) note('mutate', c.name, 'error message', c.error.message, failed.message);
    continue;
  }
  if (c.error) { note('mutate', c.name, 'accepted an edit Python refused', c.error.message, null); continue; }
  const doc = canon(Q.serialize(st)), want = canon(c.doc);
  if (doc !== want) {
    const a = JSON.parse(doc), b = JSON.parse(want);
    let where = 'document';
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) { where = k; break; }
    }
    note('mutate', c.name, where, clip(JSON.stringify(b[where])), clip(JSON.stringify(a[where])));
  }
  if (c.layout) diffFlat('mutate-layout', c.name, c.layout, Q.layoutOf(st.device));
  if (c.hardware) {
    const hw = Q.hardwareReport(st.device, st.control || {}, st.budget || {}, '');
    for (const k of Object.keys(c.hardware)) {     // the keys Python sent, not the union
      compared++;
      if (!same(c.hardware[k], hw[k])) note('mutate-hw', c.name, k, c.hardware[k], hw[k]);
    }
  }
}

// ------------------------------------------------------------------- 10. lint
// The editor's only state-free legality check.  Compare the WHOLE enumeration, not the
// verdict: reporting one violation where Python reports 28 still says "illegal", so a
// verdict-only comparison would call that agreement.
for (const c of cases.lint || []) {
  const r = Q.applyProgram(c.calls);
  compared++;
  if (r.error) { note('lint', c.name, 'architecture refused', null, r.error.message); continue; }
  let got;
  try { got = Q.architectureViolations(r.ok) || []; }
  catch (err) { compared++; note('lint', c.name, 'threw', null, err.message); continue; }

  // `lint()` ITSELF, which coverage showed was executed ZERO times by this workload --
  // the bucket only ever called `architectureViolations`, the function lint WRAPS. It has
  // no Python oracle (its advisory checks are browser-side), so this is EXECUTION
  // coverage, not parity: it must run, return an array, and every entry must carry the
  // fields the page renders. A lint that throws or hands back a malformed record would
  // have gone unnoticed indefinitely.
  compared++;
  try {
    const lints = Q.lint(r.ok);
    if (!Array.isArray(lints)) note('lint', c.name, 'lint() shape', 'array', typeof lints);
    for (const l of lints) {
      compared++;
      if (!l || typeof l.message !== 'string' || !l.message) {
        note('lint', c.name, 'lint entry without a message', 'string', JSON.stringify(l));
        break;
      }
    }
  } catch (err) { note('lint', c.name, 'lint() threw', null, err.message); }
  const norm = v => (v.rule || '') + '|' + (v.message || '');
  const py = (c.violations || []).map(norm).sort();
  const js = got.map(norm).sort();
  compared++;
  if (py.length !== js.length) note('lint', c.name, 'violation count', py.length, js.length);
  for (let i = 0; i < Math.max(py.length, js.length); i++) {
    compared++;
    if (py[i] !== js[i]) { note('lint', c.name, 'violation[' + i + ']', clip(py[i]), clip(js[i])); break; }
  }
}

// ------------------------------------------------------------------- 11. schema
//
// THE BUCKET THAT CLOSES THE LAST HAND-COPIED CONSTANT.  `engine.js::validateDocument` is
// a mirror of `schema.py::_walk`, driven entirely by the schema shipped in the data blob:
// no enum, no bound and no pattern is written down in JavaScript.  That makes VALUE drift
// structurally impossible -- but it does not make WALKER drift impossible, and a walker
// that consulted the wrong node, anchored a pattern differently or worded a message
// differently would let an unloadable document out of the browser just as effectively as
// a stale enum did.
//
// So the comparison is BEHAVIOURAL, over the nine shipped architectures, their expanded
// forms, and ten families of one-token mutation applied at random reachable places
// (bad enum value, short array, wrong type, unknown key, dropped required key, negative
// number, malformed id, malformed map key, null hole, wrong schema version).  Both sides
// must produce the SAME error list, in message text, at tolerance zero.
for (const c of cases.schema || []) {
  compared++;
  let got;
  try { got = Q.validateDocument(c.doc); }
  catch (err) { note('schema', c.name, 'threw', JSON.stringify(c.errors), err.message); continue; }
  const py = (c.errors || []).slice().sort();
  const js = got.slice().sort();
  compared++;
  if (py.length !== js.length) {
    note('schema', c.name, 'error count', py.length + ': ' + clip(JSON.stringify(py)),
         js.length + ': ' + clip(JSON.stringify(js)));
    continue;
  }
  for (let i = 0; i < py.length; i++) {
    compared++;
    if (py[i] !== js[i]) { note('schema', c.name, 'error[' + i + ']', clip(py[i]), clip(js[i])); break; }
  }
}

// The version the engine stamps on a serialized document must be the shipped one, not a
// remembered one.  This is what used to be `var SCHEMA_VERSION = '0.2'`.
if (cases.schema_version !== undefined) {
  compared++;
  if (Q.schemaVersion() !== cases.schema_version) {
    note('schema', 'version', 'schema_version', cases.schema_version, Q.schemaVersion());
  }
}

// ------------------------------------------------------------------- 11b. classes
//
// `class_participants` (listing.py) against `Q.classParticipants`, ELEMENT BY ELEMENT IN
// ORDER, with the lengths reported separately so a cardinality drift reads as one.
//
// The existing `lint` bucket could not see any of this: it diffs `architectureViolations`
// (R11 only), and `lint()` itself reads `classParticipants(...).length`, so the order was
// compared nowhere and `Q.classParticipants` was exported and never called.  Measured on
// the shipped engine over this corpus: 38 of 106 cases wrong, and two of the four causes
// changed the COUNT rather than the order -- `orbit: "docks"` gave 0 where Python gives
// 24, and a class with no orbit key gave 0 where Python gives every site -- so `lint()`
// already raised `class_no_participants` where Python's `_orbit_warnings` does not.
for (const c of cases.classes || []) {
  compared++;
  const r = Q.applyProgram(c.calls);
  if (r.error) { note('classes', c.name, 'replay', '', JSON.stringify(r.error)); continue; }
  let got;
  try { got = Q.classParticipants(r.ok, c.cls); }
  catch (err) { note('classes', c.name, 'threw', '', err.message); continue; }
  const py = c.participants || [];
  compared++;
  if (py.length !== got.length) {
    note('classes', c.name, 'participant count',
         py.length + ': ' + clip(py.slice(0, 6).join(',')),
         got.length + ': ' + clip(got.slice(0, 6).join(',')));
    continue;
  }
  for (let i = 0; i < py.length; i++) {
    compared++;
    if (py[i] !== got[i]) { note('classes', c.name, 'participant[' + i + ']', py[i], got[i]); break; }
  }
}

// ------------------------------------------------------------------- 12. strings
//
// ALL THREE EDGES of the triangle, which is what makes this a round trip rather than two
// independent checks that can agree on a wrong answer:
//     Q.lit(s) === json.dumps(s)                    the writer IS json.dumps
//     parse('m.describe(' + lit + ')') gives s      the reader undoes the writer
//     render(that stmt) === the same text           byte-exact re-emission
// The reader and the writer used to name DIFFERENT escape sets, through two open ternary
// chains neither of which said what it covered: `"A\bB"` was read as `AbB` and written as
// `"AB"`, `"A\x41B"` was read as `Ax41B`, and a malformed `\uZZZZ` became a NUL.
// Measured on the shipped engine over this corpus: 18 of 378 comparisons wrong.
for (const c of cases.strings || []) {
  compared++;
  let w;
  try { w = Q.lit(c.s); } catch (err) { note('strings', c.name, 'lit threw', c.lit, err.message); continue; }
  if (w !== c.lit) note('strings', c.name, 'lit', JSON.stringify(c.lit), JSON.stringify(w));
  const text = 'm.describe(' + c.lit + ')';
  compared++;
  const p = Q.parse(text);
  if (p.errors.length) {
    note('strings', c.name, 'parse', JSON.stringify(c.s), p.errors[0].message);
    continue;
  }
  const back = p.stmts[0].args[0];
  if (back !== c.s) note('strings', c.name, 'read', JSON.stringify(c.s), JSON.stringify(back));
  compared++;
  const re = Q.render(p.stmts[0]);
  if (re !== text) note('strings', c.name, 'render', JSON.stringify(text), JSON.stringify(re));
}

// ------------------------------------------------------------------ 11. text refusals
// `_perr` -- the tokenizer's ENTIRE refusal path, 19 throw sites -- was executed zero
// times. The `strings` bucket proves valid text round-trips; nothing proved MALFORMED
// text is refused rather than silently corrupted, which is exactly the half of the
// ``/`` defect that mattered: an unknown escape used to fall through to `out += e`
// and produce "AbB" with no error at all.
for (const c of cases.refusals || []) {
  compared++;
  let p;
  try { p = Q.parse(c.src); }
  catch (err) { note('refuse', c.name, 'threw instead of reporting', 'a parse error', err.message); continue; }
  if (!p.errors || !p.errors.length) {
    note('refuse', c.name, 'ACCEPTED malformed source', 'a parse error', JSON.stringify(p.stmts || []).slice(0, 120));
  }
}

// ------------------------------------------------------------------- 7. text round trip
for (const c of cases.sources || []) {
  compared++;
  const p = Q.parse(c.src);
  if (p.errors.length) {
    note('text', c.name, 'parse error line ' + p.errors[0].line, '', p.errors[0].message);
    continue;
  }
  const back = Q.renderProgram(p.stmts);
  if (back !== c.src) {
    const a = back.split('\n'), b = c.src.split('\n');
    let i = 0;
    while (i < a.length && i < b.length && a[i] === b[i]) i++;
    note('text', c.name, 'line ' + (i + 1), b[i], a[i]);
  }
}

// ------------------------------------------------------------------- 14. build verbs
// THE EIGHT FROM-SCRATCH VERBS.  `DeviceBuilder`, `d.site`, `d.junction`, `d.segment`,
// `d.loop`, `blank_device`, `from_device` -- the whole of the "build a device from
// nothing" surface, and until this bucket existed not one of them was executed here.
//
// Compare THE WHOLE ENUMERATION on a refusal: the code, the statement INDEX and the full
// message, not merely "both said no".  Reporting `no_builder` at statement 0 where Python
// reports `ValidationError` at statement 9 still says "illegal", and a verdict-only
// comparison would call that agreement while the user's error strip pointed at the wrong
// line.  The `bad:` family is the half that can see a MISSING refusal -- 403 of 403 of
// those devices were built by the browser, priced, drawn and exported before the seal
// guard landed.
for (const c of cases.build || []) {
  compared++;
  let r;
  try { r = Q.applyProgram(c.calls); }
  catch (err) { note('build', c.name, 'applyProgram threw', '(a result)', err.name + ': ' + err.message); continue; }
  if (r.error) {
    if (!c.error) {
      note('build', c.name, 'refused what Python accepted', '(accepted)',
           r.error.code + ' @' + r.error.index + ': ' + clip(r.error.message));
      continue;
    }
    compared += 2;
    if (r.error.index !== c.error.index) {
      note('build', c.name, 'error index', c.error.index, r.error.index);
    } else if (r.error.code !== c.error.code) {
      note('build', c.name, 'error code ' + c.error.code, clip(c.error.message), clip(r.error.message));
    } else if (r.error.message !== c.error.message) {
      note('build', c.name, 'error message', clip(c.error.message), clip(r.error.message));
    }
    continue;
  }
  if (c.error) {
    note('build', c.name, 'accepted what Python refused',
         c.error.code + ' @' + c.error.index + ': ' + clip(c.error.message), '(built a document)');
    continue;
  }
  let doc;
  try { doc = Q.serialize(r.ok); }
  catch (err) { note('build', c.name, 'serialize threw', '(a document)', err.name + ': ' + err.message); continue; }
  compared++;
  const got = canon(doc), want = canon(c.doc);
  if (got !== want) {
    const a = JSON.parse(got), b = JSON.parse(want);
    let where = 'document';
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) { where = k; break; }
    }
    if (where === 'geometry') {
      for (const k of new Set([...Object.keys(a.geometry || {}), ...Object.keys(b.geometry || {})])) {
        if (JSON.stringify(a.geometry[k]) !== JSON.stringify(b.geometry[k])) { where = 'geometry.' + k; break; }
      }
    }
    const pick = (o) => where.startsWith('geometry.') ? o.geometry[where.slice(9)] : o[where];
    note('build', c.name, where, clip(JSON.stringify(pick(b))), clip(JSON.stringify(pick(a))));
  }
}

// The advertised build vocabulary must BE the dispatched one, on both sides.  Derived from
// the dispatcher in JS and from `qccd/arch/edit.py`'s frozensets in Python, so a verb that
// is advertised-but-undispatchable, or dispatchable-but-unadvertised, is impossible rather
// than merely tested against.
if (cases.build_vocabulary) {
  for (const [kind, want] of Object.entries(cases.build_vocabulary)) {
    compared++;
    const got = (kind === 'build' ? Q.BUILD_METHODS : Q.SEED_METHODS).slice().sort();
    const w = want.slice().sort();
    if (JSON.stringify(got) !== JSON.stringify(w)) {
      note('build', 'vocabulary:' + kind, 'method set', JSON.stringify(w), JSON.stringify(got));
    }
  }
}

// ------------------------------------------------------------------- 15. rule verdicts
// THE VERDICTS, and they are compared as COUNTS PER RULE and as the sorted MULTISET OF
// MESSAGES -- never as pass/fail.  `engine.js::architectureViolations` already carries the
// reason in its own comment: deduping by degree reported 2 where Python reported 77, "the
// verdict agreed -- both said illegal -- which is exactly why a verdict-only comparison
// called it agreement."  Two prototypes of THIS mirror reported 864 where Python reported
// 1,728, both with the verdict identical: once by judging only transport cycles, once by
// enumerating only the nodes an ion arrived at.  Counts see both; verdicts see neither.
for (const c of cases.rules || []) {
  compared++;
  const r = Q.applyProgram(c.calls);
  if (r.error) { note('rules', c.name, 'architecture refused', null, r.error.message); continue; }
  const st = r.ok, dev = st.device;
  let model;
  try {
    model = Q.makeModel(st.primitives, Q.degrees(dev), Q.cornerEndpoints(dev), dev.segments, {
      kind: c.model.name === 'deck' ? 'deck' : 'corrected',
      corner_hops: c.model.corner_hops,
      junction_min_degree: c.physics.junction_min_degree,
      length_scaling: !!c.model.length_scaling,
      pitch: c.model.pitch || 1.0,
      include_anomalous: c.model.include_anomalous !== false,
      anomalous_per_ms: c.physics.anomalous_per_ms || 0,
      policy: c.physics.policy });
  } catch (err) { note('rules', c.name, 'makeModel threw', '(a model)', err.message); continue; }
  model._pair = Q.pairIndex(dev);
  let rep;
  try {
    rep = Q.checkFrames(dev, c.frames, c.loops, model, c.classes, {
      zone_types: c.zone_types, max_simd: c.max_simd,
      gate_threshold: c.physics.gate_threshold,
      models_heating: c.models_heating, chain_limit: 15, state: st });
  } catch (err) { note('rules', c.name, 'checkFrames threw', '(a report)', err.message); continue; }
  const got = {}, want = c.python || {};
  for (const [k, v] of Object.entries(rep.by_rule || {})) if (c.browser_set.includes(k)) got[k] = v;
  for (const k of new Set([...Object.keys(want), ...Object.keys(got)])) {
    compared++;
    if ((want[k] || 0) !== (got[k] || 0)) note('rules', c.name, k + ' count', want[k] || 0, got[k] || 0);
  }
  // the MESSAGE MULTISET, sorted: strictly stronger than the counts and still independent
  // of the order two different walks happen to visit their nodes in.
  const mine = (rep.messages || []).filter(v => c.browser_set.includes(v.rule))
                                   .map(v => v.message).sort();
  const theirs = (c.messages || []).slice().sort();
  compared += 1 + Math.max(mine.length, theirs.length);
  if (mine.length !== theirs.length) {
    note('rules', c.name, 'message count', theirs.length, mine.length);
  } else {
    for (let i = 0; i < mine.length; i++) {
      if (mine[i] !== theirs[i]) { note('rules', c.name, 'message ' + i, theirs[i], mine[i]); break; }
    }
  }
}

// The mirrored rule set is DERIVED FROM THE DISPATCHER on the JS side and declared in
// `render.py::BROWSER_SET` on the Python side; the two must be the same set or the page
// ships a `rule_checksum` for a rule nothing checks, or checks a rule nothing pinned.
if (cases.browser_set) {
  compared++;
  const got = Q.MIRRORED_RULES.slice().sort(), want = cases.browser_set.slice().sort();
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    note('rules', 'browser_set', 'mirrored rule set', JSON.stringify(want), JSON.stringify(got));
  }
}

// ------------------------------------------------------------------- 16. programme lane
// THE TWELVE AUTHORING VERBS, lowered to frames and to TSIR.
//
// FIELD BY FIELD, FRAME BY FRAME -- never totals.  Planting
// `cls: kw.cls || "rotate_cw"` in the rotate branch was MEASURED to leave every total,
// every per-ion quantum and `validateProgram` completely blind, because `rotate_cw` and
// `rotate_ccw` both declare `entails: ()`.  Python is not blind: `MNEMONIC_BY_CLASS`
// prints ROT.CW where it should print ROT.CCW, `prog.templates()` keys on `:+1` instead of
// `:-1`, and `Instruction.cls` differs in the exported `.tsir.json`.  That is why this
// bucket diffs frames and instructions rather than the three numbers at the bottom.
const PFIELDS = cases.prog_frame_fields || [];
for (const c of cases.prog || []) {
  compared++;
  const r = Q.applyProgram(c.calls);
  if (r.error) { note('prog', c.name, 'architecture refused', null, r.error.message); continue; }
  const dev = r.ok.device;
  let low;
  try {
    low = Q.lowerProgram(c.records.map(x => ({ ...x, lane: 'prog' })), dev, c.loops,
                         { name: c.arch_name, classes: c.classes });
  } catch (err) { note('prog', c.name, 'lowerProgram threw', '(a result)', err.message); continue; }

  if (c.error) {
    compared += 2;
    if (!low.errors.length) {
      note('prog', c.name, 'accepted what Python refused', c.error, '(lowered)');
    } else if (low.errors[0].message !== c.error) {
      note('prog', c.name, 'refusal message', c.error, low.errors[0].message);
    }
    continue;
  }
  if (low.errors.length) {
    note('prog', c.name, 'refused what Python accepted', '(accepted)',
         low.errors[0].code + ' @' + low.errors[0].i + ': ' + low.errors[0].message);
    continue;
  }
  if (c.frames === null) continue;              // an ALLOW case with nothing to compare

  compared++;
  if (low.frames.length !== c.frames.length) {
    note('prog', c.name, 'frame count', c.frames.length, low.frames.length);
    continue;
  }
  let bad = false;
  for (let i = 0; i < c.frames.length && !bad; i++) {
    for (const k of PFIELDS) {
      const want = c.frames[i][k], got = low.frames[i][k];
      if (want === undefined && got === undefined) continue;
      compared++;
      if (canon(want === undefined ? null : want) !== canon(got === undefined ? null : got)) {
        note('prog', c.name, 'frame ' + i + '.' + k, clip(JSON.stringify(want)),
             clip(JSON.stringify(got)));
        bad = true;
        break;
      }
    }
  }
  if (bad) continue;

  // and the TSIR projection, instruction by instruction.  `programToTsir` reads the
  // RECORDS, never the frames: a frame carries a node path and a participant carries `via`
  // segment ids, and path -> segments is ambiguous on a multigraph.
  let doc;
  try {
    doc = Q.programToTsir(c.records.map(x => ({ ...x, lane: 'prog' })), dev, c.loops,
                          { name: 'bucket', archSpec: '', classes: c.classes }).doc;
  } catch (err) { note('prog', c.name, 'programToTsir threw', '(a document)', err.message); continue; }
  compared++;
  if (doc.instructions.length !== c.tsir.length) {
    note('prog', c.name, 'instruction count', c.tsir.length, doc.instructions.length);
    continue;
  }
  for (let i = 0; i < c.tsir.length; i++) {
    const want = { ...c.tsir[i] }, got = { ...doc.instructions[i] };
    delete want.meta; delete got.meta;           // provenance is compared by its own rule
    compared++;
    if (canon(want) !== canon(got)) {
      note('prog', c.name, 'instruction ' + i, clip(JSON.stringify(want)),
           clip(JSON.stringify(got)));
      break;
    }
  }
}

// The programme TEXT round trip, byte for byte -- the same property the architecture lane
// already holds, over the same `lit`/`kwd`.  `parse(render(x)) === x` is asserted in that
// direction because the browser RENDERS what it holds and then re-reads it.
for (const c of cases.progtext || []) {
  compared++;
  let src;
  try { src = Q.renderProgramSource(c.records); }
  catch (err) { note('progtext', c.name, 'render threw', '(text)', err.message); continue; }
  const p = Q.parse(src);
  if (p.errors.length) {
    note('progtext', c.name, 'parse error line ' + p.errors[0].line, '', p.errors[0].message);
    continue;
  }
  compared++;
  if (p.prog.length !== c.records.length) {
    note('progtext', c.name, 'statement count', c.records.length, p.prog.length);
    continue;
  }
  const back = Q.renderProgramSource(p.prog);
  compared++;
  if (back !== src) {
    const a = back.split('\n'), b = src.split('\n');
    let i = 0;
    while (i < a.length && i < b.length && a[i] === b[i]) i++;
    note('progtext', c.name, 'line ' + (i + 1), b[i], a[i]);
  }
  // and the RECORDS survive the text: a round trip that renders the same bytes from a
  // different record list would still be wrong
  compared++;
  if (canon(p.prog.map(x => ({ method: x.method, args: x.args, kwargs: x.kwargs }))) !==
      canon(c.records.map(x => ({ method: x.method, args: x.args, kwargs: x.kwargs })))) {
    note('progtext', c.name, 'records', clip(JSON.stringify(c.records)),
         clip(JSON.stringify(p.prog.map(x => ({ method: x.method, args: x.args, kwargs: x.kwargs })))));
  }
}

// ADVERTISED IMPLIES DISPATCHABLE, on the programme lane too.  `Q.PROGRAM_METHODS` is
// derived over `PCALLS`; `qccd.api.PROGRAM_METHODS` is derived over `Program`'s public
// methods.  A verb the parser advertises and the lowerer lacks is invisible to every
// differential bucket, because Python would never emit it.
if (cases.program_methods) {
  compared++;
  const got = Q.PROGRAM_METHODS.slice().sort(), want = cases.program_methods.slice().sort();
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    note('prog', 'vocabulary', 'program methods', JSON.stringify(want), JSON.stringify(got));
  }
}

// Key order is not semantic (the comparator sorts it) but LIST order is: `Device.to_json`
// emits dict-insertion order and the export is byte-compared.
function canon(v) {
  if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
  if (v && typeof v === 'object') {
    return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}';
  }
  if (typeof v === 'number' && Object.is(v, -0)) return '-0';
  return JSON.stringify(v === undefined ? null : v);
}
function clip(s) { return s === undefined ? s : (s.length > 300 ? s.slice(0, 300) + '...' : s); }

const byShape = [];
seenKinds.forEach((n, sig) => byShape.push([sig, n]));
byShape.sort((a, b) => b[1] - a[1]);
process.stdout.write(JSON.stringify(
  { compared, mismatched, worst, shapes: byShape.slice(0, 12), sample }) + '\n');
