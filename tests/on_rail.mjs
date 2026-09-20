// IS EVERY DRAWN ION ON THE METAL?  node tests/on_rail.mjs <page.html> [frames] [sub]
//
// An ion is carried by the electrodes under the rail it is riding.  It can be in a trap,
// which has length and holds a stack, and it can be on a rail between traps -- but it is
// never BESIDE the rail, because there is nothing there to hold it.  A picture that puts
// one beside the rail draws a motion no QCCD machine can perform.
//
// This exists because one did.  Reported against `board/bb144/22_planar12_own_a72`: the
// detour that keeps two ions from being drawn through each other peaked at the middle of
// a walk, and the middle of a two-hop walk is exactly the junction -- so an ion crossing
// a junction swung 0.62 g wide of it and read as jumping over the square rather than
// entering it and turning.
//
// Two numbers, because the rule has two halves:
//
//   worst_off_rail_g        the furthest any mark strays from the nearest rail
//   worst_on_open_rail_g    the same, counting ONLY marks that are well clear of every
//                           trap -- out on the rail and at junctions, where nothing may
//                           stray at all.  A trap has length and a stack to arrange
//                           within it; open rail does not.
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
;globalThis.__traps = () => A.nodes.filter(n => (n.cap || 0) > 0)
    .map(n => [px(n), py(n), siteLen(n.cap) / 2]);
;globalThis.__juncs = () => A.nodes.filter(n => n.kind === 'junction' || (n.cap || 0) === 0)
                                   .map(n => [n.id, px(n), py(n)]);
;globalThis.__g = () => L.g;
`);
const PG = globalThis.__page;
const rails = globalThis.__rails(), traps = globalThis.__traps();
const juncs = globalThis.__juncs(), g = globalThis.__g();

function distToSeg(x, y, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  if (l2 < 1e-12) return Math.hypot(x - ax, y - ay);
  let t = ((x - ax) * dx + (y - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x - (ax + t * dx), y - (ay + t * dy));
}
function offRail(x, y) {
  let best = Infinity;
  for (const pts of rails) {
    for (let i = 0; i + 1 < pts.length; i++) {
      const d = distToSeg(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
      if (d < best) best = d;
    }
  }
  return best;
}
// How far OUTSIDE the nearest trap's own bar this point is.  Not the distance to the
// node: a capacity-4 trap is 0.88 g long, so an ion a third of a lattice step from its
// node is still inside its own trap, with the trap's own room to arrange a stack in.
function clearOfTraps(x, y) {
  let best = Infinity;
  for (const p of traps) {
    const d = Math.hypot(x - p[0], y - p[1]) - p[2];
    if (d < best) best = d;
  }
  return best;
}
function nearestJunction(x, y) {
  let best = Infinity, who = null;
  for (const j of juncs) {
    const d = Math.hypot(x - j[1], y - j[2]);
    if (d < best) { best = d; who = j[0]; }
  }
  return [best, who];
}

//: how far CLEAR OF EVERY TRAP BAR a mark must be before it counts as "out on the rail".
//: Measured from the end of the bar, not from the node, plus a mark's width so that an
//: ion still settling into the outermost slot is not judged as though it were on open
//: metal.
const OPEN = 0.15;

const nf = Math.min(PG.nframes(), MAXF);
let worst = 0, worstAt = null, worstOpen = 0, worstOpenAt = null, n = 0;
for (let f = 0; f < nf; f++) {
  for (let s = 0; s <= NS; s++) {
    PG.drawAt(f, s / NS);
    for (const m of PG.ionMarks()) {
      const d = offRail(m[1], m[2]) / g;
      const trap = clearOfTraps(m[1], m[2]) / g;
      n++;
      const rec = () => {
        const [jd, jid] = nearestJunction(m[1], m[2]);
        return { frame: f, phase: +(s / NS).toFixed(3), ion: m[0],
                 off_rail_g: +d.toFixed(4), clear_of_trap_g: +trap.toFixed(3),
                 nearest_junction: jid, junction_away_g: +(jd / g).toFixed(3) };
      };
      if (d > worst) { worst = d; worstAt = rec(); }
      if (trap > OPEN && d > worstOpen) { worstOpen = d; worstOpenAt = rec(); }
    }
  }
}
console.log(JSON.stringify({
  page: page.replace(/\\/g, '/').split('/').pop(), g, frames: nf, marks: n,
  open_rail_threshold_g: OPEN,
  worst_off_rail_g: +worst.toFixed(4), worst_at: worstAt,
  worst_on_open_rail_g: +worstOpen.toFixed(4), worst_on_open_rail_at: worstOpenAt,
}));
