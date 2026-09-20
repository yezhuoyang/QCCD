// IS EVERY DRAWN ION ON THE METAL IT IS DRAWN ON?  node tests/on_metal.mjs <page> [f] [s]
//
// `on_rail.mjs` asks how far a mark is from the nearest rail CENTRE LINE, and allows a
// mark inside a trap to sit off that line, because a trap has a stack to arrange along
// its bar.  That licence is along the bar.  ACROSS it there is no licence at all: the
// bar is drawn `site_t` thick and it is a picture of the electrodes, so a mark beyond
// `site_t/2` across is drawn beside its own trap rather than in it.
//
// Reported from the published site: "when ions swap, they shouldn't jump outside the
// site".  They did -- the detour that keeps two swapping ions from being drawn through
// each other pushed them 0.21 g across a bar half of whose thickness is 0.10 g.
//
// This probe measures the thing the reader sees: the distance from a mark's centre to
// the METAL, which is the union of every trap's capsule (a bar of length `siteLen(cap)`
// and thickness `site_t`, rounded) and every rail (its polyline, `sw_rail` wide).  Zero
// means every ion was drawn on something that could hold it.
import { loadPage } from './shim.mjs';
import { PAGE_HOOK } from './drive.mjs';

globalThis.__QCCD_SYNC = true;
const page = process.argv[2];
const MAXF = +(process.argv[3] || 60);
const NS = +(process.argv[4] || 8);

loadPage(page, PAGE_HOOK + `
;globalThis.__rails = () => {
  const out = [];
  for (const sg of A.segments) {
    const pts = [];
    for (let i = 0; i <= 24; i++) {
      const q = edgePoint(sg.a, sg.b, i / 24);
      if (q && isFinite(q.x)) pts.push([q.x, q.y]);
    }
    if (pts.length > 1) out.push(pts);
  }
  return out;
};
;globalThis.__bars = () => A.nodes.filter(n => (n.cap || 0) > 0).map(n => {
  const a = AXIS[n.id] || { ux: 1, uy: 0 };
  return [px(n), py(n), a.ux, a.uy, siteLen(n.cap) / 2, n.id];
});
;globalThis.__metrics = () => ({ g: L.g, site_t: L.site_t, sw_rail: L.sw_rail });
`);
const PG = globalThis.__page;
const rails = globalThis.__rails(), bars = globalThis.__bars();
const M = globalThis.__metrics(), g = M.g;

//: half the drawn thickness of each kind of metal.  A mark's CENTRE is what is measured,
//: so these are the distances at which the centre leaves the shape.
const HALF_BAR = M.site_t / 2;
const HALF_RAIL = Math.max(M.sw_rail, 0.5 * M.site_t) / 2;

function distToSeg(x, y, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  if (l2 < 1e-12) return Math.hypot(x - ax, y - ay);
  let t = ((x - ax) * dx + (y - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x - (ax + t * dx), y - (ay + t * dy));
}
//: how far outside a trap's capsule a point is: the bar is a segment of half-length
//: `half` along the node's axis, dilated by `HALF_BAR`.
function outsideBar(x, y, b) {
  const [cx, cy, ux, uy, half] = b;
  const d = distToSeg(x, y, cx - ux * half, cy - uy * half, cx + ux * half, cy + uy * half);
  return d - HALF_BAR;
}
function outsideRails(x, y) {
  let best = Infinity;
  for (const pts of rails) {
    for (let i = 0; i + 1 < pts.length; i++) {
      const d = distToSeg(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
      if (d < best) best = d;
    }
  }
  return best - HALF_RAIL;
}
function nearestBar(x, y) {
  let best = Infinity, who = null;
  for (const b of bars) {
    const d = outsideBar(x, y, b);
    if (d < best) { best = d; who = b[5]; }
  }
  return [best, who];
}

//: floating point, not slack.  A mark this far outside the metal is 0.004 px on the
//: densest board page and 0.0004 px on a shipped architecture.
const EPS = 1e-4;

const nf = Math.min(PG.nframes(), MAXF);
let worst = -Infinity, worstAt = null, n = 0, off = 0;
for (let f = 0; f < nf; f++) {
  for (let s = 0; s <= NS; s++) {
    PG.drawAt(f, s / NS);
    for (const m of PG.ionMarks()) {
      const [bar, who] = nearestBar(m[1], m[2]);
      const d = Math.min(bar, outsideRails(m[1], m[2])) / g;
      n++;
      if (d > EPS) off++;
      if (d > worst) {
        worst = d;
        worstAt = { frame: f, phase: +(s / NS).toFixed(3), ion: m[0],
                    outside_g: +d.toFixed(4), nearest_bar: who,
                    outside_that_bar_g: +(bar / g).toFixed(4) };
      }
    }
  }
}
console.log(JSON.stringify({
  page: page.replace(/\\/g, '/').split('/').pop(), g, frames: nf, marks: n,
  half_bar_g: +(HALF_BAR / g).toFixed(4), half_rail_g: +(HALF_RAIL / g).toFixed(4),
  tolerance_g: EPS, marks_off_metal: off,
  worst_outside_metal_g: +Math.max(0, worst).toFixed(4), worst_at: worstAt,
}));
