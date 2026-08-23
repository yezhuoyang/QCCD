// The JS half of the component-variant differential.
//
// Reads a corpus Python wrote -- the shipped variant blocks plus a list of
// (component, sel, slots) points -- runs the SHIPPED resolver from engine.js, and writes
// each resolved spec back with every number replaced by its raw 64-bit pattern.  Nothing
// is rounded, formatted or normalised on the way out, so a signed zero and a 1-ulp
// difference both survive to the comparison. Neither side ever reads the other's output.
import fs from 'fs';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const Q = require('../qccd/viz/engine.js');

const corpus = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

function bits(x) {
  const dv = new DataView(new ArrayBuffer(8));
  dv.setFloat64(0, x, true);
  let s = '';
  for (let i = 0; i < 8; i++) s += dv.getUint8(i).toString(16).padStart(2, '0');
  return 'f:' + s;
}

function bitify(x) {
  if (Array.isArray(x)) return x.map(bitify);
  if (x && typeof x === 'object') {
    const o = {};
    for (const k of Object.keys(x)) o[k] = bitify(x[k]);   // key ORDER is preserved
    return o;
  }
  if (typeof x === 'number') return bits(x);
  return x;
}

const out = [];
for (const point of corpus.points) {
  const vb = corpus.blocks[point.name];
  let rec;
  try {
    rec = { spec: bitify(Q.resolveVariant(vb, point.sel, point.slots)),
            guard: Q.variantGuard(vb, point.sel, point.slots),
            label: Q.variantLabel(point.name, vb, point.sel) };
  } catch (err) {
    rec = { error: String((err && err.message) || err) };
  }
  out.push(rec);
}

// the arithmetic sweep: every multiplier that actually ships, crossed with hostile values
const arith = [];
for (const c of corpus.coefficients) {
  const row = [];
  for (const v of corpus.values) row.push(bits(c * v));
  arith.push(row);
}

fs.writeFileSync(process.argv[3], JSON.stringify({ results: out, arith }));
