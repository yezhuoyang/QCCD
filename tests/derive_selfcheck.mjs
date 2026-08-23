// Prove the page-side self-check against a page that ALREADY SHIPPED.
//
// Every emitted page carries `arch.nodes[i].deg`, `arch.nodes[i].corner` and
// `arch.segments[i].corner_endpoints` -- computed in Python at emit time.  They are
// exactly the three quantities a topology edit moves, and exactly what the JS mirror in
// `qccd/viz/js/edit.js` has to recompute for itself once the browser owns the device.
//
// So the mirror can be checked against Python ON EVERY PAGE, FOR FREE, with no extra
// payload: read the shipped values out of the data block, rebuild the device in the
// mirror's own shape, run `derived()`, and compare.  This is the same move the page
// already makes with `D.checksum` -- replay in the page, diff against the Python
// verifier, say so out loud if they disagree -- extended to the graph instead of the
// n-bar.  The page-side version of this belongs in `draw()`'s self-check note; this
// file is the CI half, and it needs nothing but an emitted .html.
//
//   node tests/derive_selfcheck.mjs out/deck.html
import fs from 'fs';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const E = (await import(pathToFileURL(
  path.join(here, '..', 'qccd', 'viz', 'js', 'edit.js')).href)).default;

const file = process.argv[2];
const html = fs.readFileSync(file, 'utf8');
const m = html.match(/<script id="data"[^>]*>([\s\S]*?)<\/script>/);
if (!m) { console.log(JSON.stringify({ error: 'no data block' })); process.exit(4); }
const D = JSON.parse(m[1].replace(/\\u003c/g, '<'));
const A = D.arch;

// the page's flat drawing shape -> the mirror's device shape.  `length` and
// `capacity_explicit` are not shipped for drawing, and neither is read by `derived()`.
const dev = { nodes: {}, segments: {}, loops: {}, generator: A.generator, params: A.params };
for (const n of A.nodes) {
  dev.nodes[n.id] = { id: n.id, pos: [n.x, n.y], kind: n.kind, cap: n.cap,
                      zone: n.zone, labels: n.labels || [], capacity_explicit: false };
}
for (const s of A.segments) {
  dev.segments[s.id] = { id: s.id, a: s.a, b: s.b, length: 1.0, cap: s.cap,
                         loop: s.loop, labels: s.labels || [] };
}
// THE PAGE'S LOOP EXPORT IS LOSSY, and this is where that bites.  `arch.loops` ships a
// bare node list -- `Loop.closed`, `kind` and `note` are dropped -- and `closed` is not
// recoverable in general: an open path whose two ends happen to also be joined by a
// segment is indistinguishable from a closed one.  It IS recoverable at k <= 2, where a
// closed loop would need a parallel edge and parallel edges are forbidden, so a 2-node
// loop is always open.  `stationary_chain` is exactly that case, and inferring `closed`
// from "is there a segment last->first" called both its nodes corners and charged its one
// segment two corner endpoints -- the page says neither.
//
// So the mirror is right and the WIRE is wrong: `qccd.arch.edit.device_to_wire` is the
// lossless form an editor needs, and the page must ship it in place of (or beside) the
// drawing form.  Until it does, this probe flags every loop whose `closed` it had to
// guess, rather than quietly guessing.
const inferred = [];
for (const lid of Object.keys(A.loops || {})) {
  const nodes = A.loops[lid];
  let closed;
  if (nodes.length <= 2) {
    closed = false;                       // sound: a closed 2-cycle needs a parallel edge
  } else {
    const first = nodes[0], last = nodes[nodes.length - 1];
    closed = Object.values(dev.segments).some(
      (s) => (s.a === last && s.b === first) || (s.a === first && s.b === last));
    inferred.push(lid);
  }
  dev.loops[lid] = { id: lid, nodes, closed, kind: 'ring', note: null };
}

const mine = E.derived(dev);
let degBad = 0, cornerBad = 0, ceBad = 0;
const examples = [];
for (const n of A.nodes) {
  if (mine.degree[n.id] !== n.deg) {
    degBad++;
    if (examples.length < 6) examples.push({ node: n.id, field: 'deg', page: n.deg, js: mine.degree[n.id] });
  }
  if (mine.corner[n.id] !== !!n.corner) {
    cornerBad++;
    if (examples.length < 6) examples.push({ node: n.id, field: 'corner', page: !!n.corner, js: mine.corner[n.id] });
  }
}
for (const s of A.segments) {
  if (mine.corner_endpoints[s.id] !== s.corner_endpoints) {
    ceBad++;
    if (examples.length < 6) examples.push({ segment: s.id, field: 'corner_endpoints', page: s.corner_endpoints, js: mine.corner_endpoints[s.id] });
  }
}
const comps = E.components(dev);
console.log(JSON.stringify({
  file: file.split(/[\\/]/).pop(),
  arch: A.name,
  nodes: A.nodes.length, segments: A.segments.length, loops: Object.keys(A.loops || {}).length,
  degree_mismatches: degBad,
  corner_mismatches: cornerBad,
  corner_endpoint_mismatches: ceBad,
  n_components: comps.length,
  loops_closed_inferred: inferred,
  structure_errors: E.checkStructure(dev),
  examples,
  ok: degBad === 0 && cornerBad === 0 && ceBad === 0,
}, null, 1));
process.exit(degBad + cornerBad + ceBad ? 1 : 0);
