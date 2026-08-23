// How much of what you can SEE can you actually CLICK?
//
//   node tests/aim.mjs <page.html> [samples]   ->  one JSON line on stdout
//
// The editor's own comments carry the numbers that motivated its hit test -- "only 48% of
// a cap-4 site's own drawn BAR selected it", "a cyclone segment grabbable on 3% of its own
// length", "67-72% of the stage answered nothing". Those were measured once, by hand, and
// then the code changed underneath them. This makes the measurement repeatable, so the
// next change to `slop()` / `segBand()` / `hitRadii()` is answerable rather than argued.
//
// THREE NUMBERS, each the answer to a question a user actually asks:
//
//   bar_cover   I can see the site's bar. If I click ON it, do I get the site?
//   seg_cover   I can see the rail. If I click ON it, do I get the segment?
//   dead        I clicked somewhere in the picture. How often does nothing answer?
//
// Sampling is on the DRAWN marks, not on a uniform grid over the canvas: a uniform grid
// answers "how much of the empty page is empty", which is not a question. `dead` is the
// one uniform-grid number, and it is reported over the stage's own bounding box so a
// device that fills 3% of a 16:9 canvas is not scored as 97% dead.
import { loadPage } from './shim.mjs';

const file = process.argv[2];
const N = +(process.argv[3] || 24);        // samples along each mark

const HOOK = `
;globalThis.__AIM = {
  ready: () => !!(globalThis.EDITOR && EDITOR.ready()),
  edit:  () => EDITOR.setMode('edit'),
  hit:   (x, y) => EDITOR.hit(x, y),
  L:     () => EDITOR.layout(),
  nodes: () => A.nodes.map(n => ({ id:n.id, kind:n.kind, cap:n.cap,
                                   x:px(n), y:py(n) })),
  // the site bar's own drawn axis and length, straight off the mark the stage drew
  bar:   (id) => { const E = (typeof NODEEL !== 'undefined') ? NODEEL[id] : null;
                   if (!E) return null;
                   const ax = E.ax || (typeof AXIS !== 'undefined' ? AXIS[id] : null);
                   if (!ax) return null;
                   return { ux: ax.ux, uy: ax.uy, len: E.len, kind: E.kind }; },
  // SAMPLE THE DRAWN PATH, not the chord between the endpoints. Two of the shipped
  // devices bow a segment around a node it does not touch, and the bow can sit most of a
  // lattice step away from its own chord -- so a chord probe reports a perfectly good
  // rail as unreachable. Measured before this: E71 on ring144 scored 62%, entirely
  // because the probe was asking about points where no rail is drawn.
  segs:  () => A.segments.map(s => { const a = nodeById[s.a], b = nodeById[s.b];
                   if (!a || !b) return null;
                   const I = (typeof SEGINFO !== 'undefined') ? SEGINFO[s.id] : null;
                   const pts = [];
                   for (let i = 0; i <= 32; i++) {
                     const t = i / 32;
                     if (I && I.cp && typeof bezPoint === 'function') {
                       const q = bezPoint(I, t); pts.push([q.x, q.y]);
                     } else {
                       pts.push([px(a) + (px(b) - px(a)) * t, py(a) + (py(b) - py(a)) * t]);
                     }
                   }
                   return { id: s.id, bowed: !!(I && I.cp), pts: pts };
                 }).filter(Boolean),
};`;

loadPage(file, HOOK);
const AIM = globalThis.__AIM;
if (!AIM.ready()) { console.log(JSON.stringify({ error: 'editor not ready' })); process.exit(2); }
AIM.edit();

const L = AIM.L();
const nodes = AIM.nodes();
const segs = AIM.segs();

// ---- 1. the site bars ------------------------------------------------------------
let barHit = 0, barTot = 0;
const barWorst = { id: null, frac: 1 };
for (const n of nodes) {
  const b = AIM.bar(n.id);
  if (!b || b.kind === 'junction') continue;
  const half = Math.max(0, (b.len - L.site_t) / 2);
  if (half <= 0) continue;
  let ok = 0;
  for (let i = 0; i < N; i++) {
    const t = -half + (2 * half) * (i / (N - 1));
    const h = AIM.hit(n.x + b.ux * t, n.y + b.uy * t);
    if (h && h.id === n.id) ok++;
  }
  barHit += ok; barTot += N;
  const f = ok / N;
  if (f < barWorst.frac) { barWorst.id = n.id; barWorst.frac = f; }
}

// ---- 2. the segments -------------------------------------------------------------
let segHit = 0, segTot = 0;
const segWorst = { id: null, frac: 1 };
let bowed = 0;
for (const s of segs) {
  if (s.bowed) bowed++;
  let ok = 0;
  for (let i = 0; i < N; i++) {
    const [x, y] = s.pts[Math.round(i / (N - 1) * (s.pts.length - 1))];
    const h = AIM.hit(x, y);
    // a sample near an endpoint legitimately belongs to the NODE, not the segment
    if (h && (h.id === s.id || h.kind !== 'segment')) ok++;
  }
  segHit += ok; segTot += N;
  const f = ok / N;
  if (f < segWorst.frac) { segWorst.id = s.id; segWorst.frac = f; }
}

// ---- 3. dead area, over the device's own extent -----------------------------------
let dead = 0, cells = 0;
if (nodes.length) {
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const x0 = Math.min(...xs) - L.g, x1 = Math.max(...xs) + L.g;
  const y0 = Math.min(...ys) - L.g, y1 = Math.max(...ys) + L.g;
  const M = 60;
  for (let i = 0; i < M; i++) for (let j = 0; j < M; j++) {
    const x = x0 + (x1 - x0) * (i / (M - 1));
    const y = y0 + (y1 - y0) * (j / (M - 1));
    cells++;
    if (!AIM.hit(x, y)) dead++;
  }
}

const pct = (a, b) => b ? +(100 * a / b).toFixed(1) : null;
console.log(JSON.stringify({
  page: file.split(/[\\/]/).pop(),
  g: L.g, r_ion: L.r_ion, site_t: L.site_t, sw_rail: L.sw_rail,
  bar_cover: pct(barHit, barTot), bar_worst: barWorst,
  seg_cover: pct(segHit, segTot), seg_worst: segWorst,
  dead: pct(dead, cells),
  n_sites: nodes.filter(n => n.kind !== 'junction').length, n_segments: segs.length,
  n_bowed: bowed,
}));
