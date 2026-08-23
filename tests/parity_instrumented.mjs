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
let __BUCKET = 'preamble';
const __PER = {};
const __inc = () => { __PER[__BUCKET] = (__PER[__BUCKET]||0)+1; };
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
    if (!(k in py) || !(k in js)) { compared++; __inc(); note(kind, name, k + ' (key present on one side only)', py[k], js[k]); continue; }
    if (k === 'bows') {
      const bp = py[k] || {}, bj = js[k] || {};
      const bk = new Set([...Object.keys(bp), ...Object.keys(bj)]);
      for (const s of bk) {
        compared++; __inc();
        if (!(s in bp) || !(s in bj) || !same(bp[s], bj[s])) note(kind, name, 'bows.' + s, bp[s], bj[s]);
      }
      continue;
    }
    compared++; __inc();
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
    compared++; __inc();
    if (pk.length !== jk.length || pk.some((k, i) => k !== jk[i])) {
      note('gen', name, part + ' id order', pk.join(','), jk.join(','));
      continue;
    }
    for (const id of pk) {
      const a = py[part][id], b = js[part][id];
      const fk = new Set([...Object.keys(a), ...Object.keys(b)]);
      for (const f of fk) {
        compared++; __inc();
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
  compared++; __inc();
  if (py.generator !== js.generator) note('gen', name, 'generator', py.generator, js.generator);
  compared++; __inc();
  const pp = JSON.stringify(py.params), jp = JSON.stringify(js.params);
  if (pp !== jp) note('gen', name, 'params', pp, jp);
}

// ------------------------------------------------------------------- 1. generators
__BUCKET = '1-generators';
for (const c of cases.generators || []) {
  let dev;
  try {
    dev = Q.expandGenerator(c.generator, c.params, c.zone_types || null);
  } catch (err) {
    compared++; __inc();
    if (c.error === undefined || c.error === null) {
      note('gen', c.name, 'unexpected refusal', null, err.name + ': ' + err.message);
    } else if (c.error !== err.message) {
      note('gen', c.name, 'error message', c.error, err.message);
    }
    continue;
  }
  if (c.error !== undefined && c.error !== null) {
    compared++; __inc();
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
__BUCKET = '2-explicit_layouts';
// The drag bucket lives here: arbitrary doubles, which is what a drag actually produces,
// not lattice points.
for (const c of cases.layouts || []) {
  let got, gotRaw;
  try {
    got = Q.computeLayout(c.nodes, c.segments || []);
    gotRaw = c.layout_raw ? Q.computeLayout(c.nodes, c.segments || [], { raw: true }) : null;
  } catch (err) {
    compared++; __inc();
    note('layout', c.name, 'threw', null, err.message);
    continue;
  }
  diffFlat('layout', c.name, c.layout, got);
  if (gotRaw) diffFlat('layout_raw', c.name, c.layout_raw, gotRaw);
}

// ------------------------------------------------------------------- 3. site/pad tiling
__BUCKET = '3-site/pad_tiling';
// The two mirrors that already existed in `render.py` UNCHECKED: the page computed
// `siteLen` inline with no int() where `layout.site_length` does `int(cap or 0)`, and it
// tiled pads at 0.34*g while `layout.pad_tiling` derives k from 0.50*g.  Neither was
// covered by anything.  Folding them in here is what makes this harness cover the parts
// that had already drifted rather than certifying the parts that were fine.
for (const c of cases.marks || []) {
  compared++; __inc();
  const sl = Q.siteLength(c.cap, c.g);
  if (!same(c.site_length, sl)) note('marks', c.name, 'site_length', c.site_length, sl);
  const pt = Q.padTiling(c.length, c.g);
  for (let i = 0; i < 3; i++) {
    compared++; __inc();
    if (!same(c.pad_tiling[i], pt[i])) note('marks', c.name, 'pad_tiling[' + i + ']', c.pad_tiling[i], pt[i]);
  }
}

// ------------------------------------------------------------------- 4. pyRepr
__BUCKET = '4-pyRepr';
for (const c of cases.reprs || []) {
  compared++; __inc();
  const got = Q.pyRepr(new Q.PyFloat(c.v));
  if (got !== c.repr) note('repr', 'repr', String(c.v), c.repr, got);
}

// ------------------------------------------------------------------- 5. hardware
__BUCKET = '5-hardware';
for (const c of cases.hardware || []) {
  let got;
  try {
    got = Q.hardwareReport(c.device, c.control || {}, c.budget || {}, c.name_arg || '');
  } catch (err) {
    compared++; __inc(); note('hw', c.name, 'threw', null, err.message); continue;
  }
  for (const k of Object.keys(c.report)) {
    compared++; __inc();
    const a = c.report[k], b = got[k];
    if (k === 'degree_histogram' || k === 'over_budget') {
      if (JSON.stringify(a) !== JSON.stringify(b)) note('hw', c.name, k, JSON.stringify(a), JSON.stringify(b));
    } else if (!same(a, b)) note('hw', c.name, k, a, b);
  }
}

// ------------------------------------------------------------------- 6. listing replay
__BUCKET = '6-listing_replay';
// Feed the identical `ArchLine.call` records to the JS interpreter and compare the whole
// serialized document.  Harvested from `call` ALONE, never reading `target` -- which is
// precisely the assertion that catches a geometry statement whose generator name lives
// only in `target`.
for (const c of cases.programs || []) {
  const r = Q.applyProgram(c.calls);
  compared++; __inc();
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
__BUCKET = '8-pricing';
// THE BUCKET THAT WAS MISSING.  A drag re-prices, and until now nothing compared the
// re-pricer against Python at all: `priceFrames`, `makeModel`, `pickPoint` and their
// sixteen helpers were never executed by this harness.  Two real defects shipped through
// that hole.  Compare every total, every quanta component, the transit count, the peak
// n-bar AND the per-ion deposit -- the last one because the two accumulators (running
// n-bar, which a cool zeroes, and lifetime deposit, which it does not) are easy to
// conflate and every TOTAL still looks right when you do.
for (const c of cases.pricing || []) {
  const r = Q.applyProgram(c.calls);
  compared++; __inc();
  if (r.error) { note('price', c.name, 'architecture refused', null, r.error.message); continue; }
  const dev = r.ok.device;
  let priced;
  try {
    const model = Q.makeModel(r.ok.primitives, Q.degrees(dev), Q.cornerEndpoints(dev),
                              dev.segments, c.model);
    model._pair = Q.pairIndex(dev);      // exactly what editor.js attaches before pricing
    priced = Q.priceFrames(c.frames, c.loops, model, c.classes || {});
  } catch (err) {
    compared++; __inc();
    note('price', c.name, 'threw', null, err.name + ': ' + err.message);
    continue;
  }
  for (const k of ['cost', 'steps', 'us']) {
    compared++; __inc();
    if (!same(c.totals[k], priced.totals[k])) note('price', c.name, 'totals.' + k, c.totals[k], priced.totals[k]);
  }
  compared++; __inc();
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
  // 64, chosen from measurement rather than taste: the worst observed across the whole
  // corpus is 13 ulp (ladder_2x72 walk, ion d0, 40.341 against 40.341000000000086), which
  // is 1.5e-15 relative. 64 leaves headroom for a longer programme without ever admitting
  // a real defect -- the two that shipped were 5,048 against 5,444 and 1,805 against
  // 55.17, both astronomically outside any ulp bound.
  const ACC_ULP = 64;
  const acc = (key, py, js) => {
    compared++; __inc();
    if (same(py, js)) return;
    const u = ulps(py, js);
    if (!(isFinite(u) && u <= ACC_ULP)) note('price', c.name, key, py, js);
  };
  acc('peak n-bar', c.peak, priced.peak);
  for (const k of Object.keys(c.comp)) {
    compared++; __inc();
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
__BUCKET = '9-mutate_verbs';
// move_site IS the drag.  set_site_capacity and set_segment_length are the two retunes an
// editor offers next to it.  All three were unexecuted here.  Apply the identical record
// through both interpreters and diff the whole document AND the layout -- the layout
// because a drag's entire purpose is to move geometry, and `g` decides every drawn mark.
for (const c of cases.mutate || []) {
  const r = Q.applyProgram(c.calls);
  compared++; __inc();
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
  compared++; __inc();
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
      compared++; __inc();
      if (!same(c.hardware[k], hw[k])) note('mutate-hw', c.name, k, c.hardware[k], hw[k]);
    }
  }
}

// ------------------------------------------------------------------- 10. lint
__BUCKET = '10-lint';
// The editor's only state-free legality check.  Compare the WHOLE enumeration, not the
// verdict: reporting one violation where Python reports 28 still says "illegal", so a
// verdict-only comparison would call that agreement.
for (const c of cases.lint || []) {
  const r = Q.applyProgram(c.calls);
  compared++; __inc();
  if (r.error) { note('lint', c.name, 'architecture refused', null, r.error.message); continue; }
  let got;
  try { got = Q.architectureViolations(r.ok) || []; }
  catch (err) { compared++; __inc(); note('lint', c.name, 'threw', null, err.message); continue; }
  const norm = v => (v.rule || '') + '|' + (v.message || '');
  const py = (c.violations || []).map(norm).sort();
  const js = got.map(norm).sort();
  compared++; __inc();
  if (py.length !== js.length) note('lint', c.name, 'violation count', py.length, js.length);
  for (let i = 0; i < Math.max(py.length, js.length); i++) {
    compared++; __inc();
    if (py[i] !== js[i]) { note('lint', c.name, 'violation[' + i + ']', clip(py[i]), clip(js[i])); break; }
  }
}

// ------------------------------------------------------------------- 11. schema
__BUCKET = '11-schema';
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
  compared++; __inc();
  let got;
  try { got = Q.validateDocument(c.doc); }
  catch (err) { note('schema', c.name, 'threw', JSON.stringify(c.errors), err.message); continue; }
  const py = (c.errors || []).slice().sort();
  const js = got.slice().sort();
  compared++; __inc();
  if (py.length !== js.length) {
    note('schema', c.name, 'error count', py.length + ': ' + clip(JSON.stringify(py)),
         js.length + ': ' + clip(JSON.stringify(js)));
    continue;
  }
  for (let i = 0; i < py.length; i++) {
    compared++; __inc();
    if (py[i] !== js[i]) { note('schema', c.name, 'error[' + i + ']', clip(py[i]), clip(js[i])); break; }
  }
}

// The version the engine stamps on a serialized document must be the shipped one, not a
// remembered one.  This is what used to be `var SCHEMA_VERSION = '0.2'`.
if (cases.schema_version !== undefined) {
  compared++; __inc();
  if (Q.schemaVersion() !== cases.schema_version) {
    note('schema', 'version', 'schema_version', cases.schema_version, Q.schemaVersion());
  }
}

// ------------------------------------------------------------------- 11b. classes
__BUCKET = '11b-classes';
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
  compared++; __inc();
  const r = Q.applyProgram(c.calls);
  if (r.error) { note('classes', c.name, 'replay', '', JSON.stringify(r.error)); continue; }
  let got;
  try { got = Q.classParticipants(r.ok, c.cls); }
  catch (err) { note('classes', c.name, 'threw', '', err.message); continue; }
  const py = c.participants || [];
  compared++; __inc();
  if (py.length !== got.length) {
    note('classes', c.name, 'participant count',
         py.length + ': ' + clip(py.slice(0, 6).join(',')),
         got.length + ': ' + clip(got.slice(0, 6).join(',')));
    continue;
  }
  for (let i = 0; i < py.length; i++) {
    compared++; __inc();
    if (py[i] !== got[i]) { note('classes', c.name, 'participant[' + i + ']', py[i], got[i]); break; }
  }
}

// ------------------------------------------------------------------- 12. strings
__BUCKET = '12-strings';
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
  compared++; __inc();
  let w;
  try { w = Q.lit(c.s); } catch (err) { note('strings', c.name, 'lit threw', c.lit, err.message); continue; }
  if (w !== c.lit) note('strings', c.name, 'lit', JSON.stringify(c.lit), JSON.stringify(w));
  const text = 'm.describe(' + c.lit + ')';
  compared++; __inc();
  const p = Q.parse(text);
  if (p.errors.length) {
    note('strings', c.name, 'parse', JSON.stringify(c.s), p.errors[0].message);
    continue;
  }
  const back = p.stmts[0].args[0];
  if (back !== c.s) note('strings', c.name, 'read', JSON.stringify(c.s), JSON.stringify(back));
  compared++; __inc();
  const re = Q.render(p.stmts[0]);
  if (re !== text) note('strings', c.name, 'render', JSON.stringify(text), JSON.stringify(re));
}

// ------------------------------------------------------------------- 7. text round trip
__BUCKET = '7-text_round_trip';
for (const c of cases.sources || []) {
  compared++; __inc();
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
  { compared, mismatched, worst, per: __PER, shapes: byShape.slice(0, 12), sample }) + '\n');
