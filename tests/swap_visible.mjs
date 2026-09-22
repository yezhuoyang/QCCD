// IS EVERY SWAP DRAWN AS A SWAP?   node tests/swap_visible.mjs <page.html> [maxFrames] [samples]
//
// Two ions that trade places in one site must be SEEN to: each steps off the line it is
// shuttled along, to its own side -- one left, one right -- goes past, and steps back
// (a collaborator, via the user, 2026-09-21: "not directly through each other, but
// slightly off the middle"; the user again: "make sure this is enforced everywhere,
// including all leaderboard circuits").  This asks the DRAWING, not the occupancy law's
// own bookkeeping: a pair the law never recognised as passing is exactly the case that
// would slip through.
//
// A SWAP IN A SITE, measured, not declared.  Over one step, take every pair of ions that
// come within `MEET` lattice steps of each other.  At the sample where they are closest,
// find a trap whose capsule (its bar, `siteLen(cap)` long and `site_t` thick) holds them
// BOTH; they swapped in it if their order ALONG that bar at the start of the step is the
// reverse of their order at the end.  An ion leaving sideways through a rail at the middle
// of its bar, while its trap-mates close up behind it, reverses nothing along the bar and
// is not a swap; two ions on crossing rails of a non-planar drawing are in no one bar.
//
// Each swapper's offset from that bar's CENTRE LINE, at the sample where they are most
// nearly side by side along it (the crossing):
//
//   both      both off it by at least `MIN_DEV` of a lattice step, on opposite sides
//   one       only one of them left the line (the other was passed through the middle)
//   same      both left it, to the SAME side
//   none      neither left it: drawn straight through each other
//   overlap   the two marks intersect at the closest point (any class)
//
// Everything is in lattice steps `g`; the worst case of each class is kept with its frame.
import { loadPage } from './shim.mjs';
import { PAGE_HOOK } from './drive.mjs';

globalThis.__QCCD_SYNC = true;
const page = process.argv[2];
const MAXF = +(process.argv[3] || 1e9);
const NS = +(process.argv[4] || 24);
const MEET = 0.5;          // lattice steps: closer than this at the closest point, they met
const MIN_DEV = 0.02;      // lattice steps: the least that reads as "off the line"
const ORDER = 0.02;        // lattice steps: an along-bar gap smaller than this has no order

loadPage(page, PAGE_HOOK + `
;globalThis.__bars = () => A.nodes.filter(n => (n.cap || 0) > 0).map(n => {
  const a = AXIS[n.id] || { ux: 1, uy: 0 };
  return [px(n), py(n), a.ux, a.uy, siteLen(n.cap) / 2, n.id];
});
`);
const PG = globalThis.__page;
const L = PG.layout(), g = L.g, HALF_BAR = L.site_t / 2;
const bars = globalThis.__bars();
const nf = Math.min(PG.nframes(), MAXF);

// bars by grid cell, for "which bar holds this point"
const BCELL = 2 * g, bgrid = new Map();
for (const b of bars) {
  const k = Math.floor(b[0] / BCELL) + "," + Math.floor(b[1] / BCELL);
  (bgrid.get(k) || bgrid.set(k, []).get(k)).push(b);
}
const inBar = (x, y, b) => {
  const [cx, cy, ux, uy, half] = b;
  const along = (x - cx) * ux + (y - cy) * uy, across = -(x - cx) * uy + (y - cy) * ux;
  const past = Math.max(0, Math.abs(along) - half);
  return Math.hypot(past, across) <= HALF_BAR * (1 + 1e-6) + 1e-9;
};
const barHolding = (pa, pb) => {
  const mx = (pa[0] + pb[0]) / 2, my = (pa[1] + pb[1]) / 2;
  const cx = Math.floor(mx / BCELL), cy = Math.floor(my / BCELL);
  let best = null, bestD = Infinity;
  for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
    for (const b of bgrid.get((cx + dx) + "," + (cy + dy)) || []) {
      if (!inBar(pa[0], pa[1], b) || !inBar(pb[0], pb[1], b)) continue;
      const d = Math.hypot(mx - b[0], my - b[1]);
      if (d < bestD) { bestD = d; best = b; }
    }
  }
  return best;
};
const alongOf = (p, b) => (p[0] - b[0]) * b[2] + (p[1] - b[1]) * b[3];
const acrossOf = (p, b) => -(p[0] - b[0]) * b[3] + (p[1] - b[1]) * b[2];

const at = (f, ph) => {
  PG.drawAt(f, ph);
  const m = new Map();
  for (const [ion, x, y, r] of PG.ionMarks()) m.set(ion, [x, y, r]);
  return m;
};

const out = { page: page.split(/[\\/]/).pop(), g, frames: nf, swaps: 0, met: 0, outside: 0,
              both: 0, one: 0, same: 0, none: 0, overlap: 0,
              min_dev_g: null, min_gap_ratio: null, worst: {} };
const keep = (cls, rec) => {
  const w = out.worst[cls];
  if (!w || rec.key < w.key) out.worst[cls] = rec;
};

for (let f = 0; f < nf; f++) {
  const m0 = at(f, 0), m1 = at(f, 1);
  const moved = [];
  for (const [ion, p] of m0) {
    const q = m1.get(ion);
    if (q && Math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-6 * g) moved.push(ion);
  }
  if (!moved.length) continue;
  const samples = [m0];
  for (let s = 1; s < NS; s++) samples.push(at(f, s / NS));
  samples.push(m1);
  // every pair with a mover in it, and its closest sample
  const movedSet = new Set(moved), best = new Map();
  samples.forEach((m, s) => {
    const cell = MEET * g, grid = new Map();
    for (const [ion, p] of m) {
      const k = Math.floor(p[0] / cell) + "," + Math.floor(p[1] / cell);
      (grid.get(k) || grid.set(k, []).get(k)).push(ion);
    }
    for (const a of moved) {
      const p = m.get(a);
      if (!p) continue;
      const cx = Math.floor(p[0] / cell), cy = Math.floor(p[1] / cell);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const b of grid.get((cx + dx) + "," + (cy + dy)) || []) {
          if (b === a || (movedSet.has(b) && b < a)) continue;
          const q = m.get(b), d = Math.hypot(p[0] - q[0], p[1] - q[1]);
          if (d >= MEET * g) continue;
          const key = a + "|" + b, cur = best.get(key);
          if (!cur || d < cur.d) best.set(key, { a, b, d, s, pa: p, pb: q });
        }
      }
    }
  });
  // REFINED WHERE AN ORDER FLIPS.  An ion leaving a 72-ion trap threads past its trap-mates
  // several to a sample, so at `NS` samples a step the one being passed has often stepped
  // back onto the line by the next sample -- 376 of 548 swaps on `tcx72` read `one` at 24
  // samples and 0 of 548 at 400.  Between two samples where some pair's order flips, the
  // drawing is sampled 16 times more, once for every pair flipping there.
  const fine = new Map();
  const fineAt = (k) => {
    if (!fine.has(k)) {
      const sub = [];
      for (let j = 1; j < 16; j++) sub.push({ t: (k - 1 + j / 16) / NS, m: at(f, (k - 1 + j / 16) / NS) });
      fine.set(k, sub);
    }
    return fine.get(k);
  };
  for (const m of best.values()) {
    out.met++;
    const bar = barHolding(m.pa, m.pb);
    if (!bar) continue;
    const a0 = m0.get(m.a), b0 = m0.get(m.b), a1 = m1.get(m.a), b1 = m1.get(m.b);
    if (!a0 || !b0 || !a1 || !b1) continue;
    const u0 = alongOf(a0, bar) - alongOf(b0, bar), u1 = alongOf(a1, bar) - alongOf(b1, bar);
    if (!(Math.abs(u0) > ORDER * g && Math.abs(u1) > ORDER * g && u0 * u1 < 0)) continue;
    // JUDGED WHERE THEY ARE SIDE BY SIDE, not where they are closest.  In a crowded trap
    // two ions stepping round each other are further apart at the crossing than they
    // rest, so the closest sample is the start of the step, where nobody has moved yet.
    // And the order must flip WHILE BOTH ARE IN THE BAR: an ion that leaves the bar at an
    // angle can end on the other side of a trap-mate along it without ever having been
    // beside it -- that is not a swap in the site.
    let c = null, prev = null;
    samples.forEach((sm, s) => {
      const pa = sm.get(m.a), pb = sm.get(m.b);
      if (!pa || !pb || !inBar(pa[0], pa[1], bar) || !inBar(pb[0], pb[1], bar)) { prev = null; return; }
      const u = alongOf(pa, bar) - alongOf(pb, bar);
      const here = { u: Math.abs(u), s, pa, pb, d: Math.hypot(pa[0] - pb[0], pa[1] - pb[1]) };
      if (prev && prev.sign * u <= 0) {
        let pick = here.u < prev.rec.u ? here : prev.rec;
        for (const fs of fineAt(s)) {
          const qa = fs.m.get(m.a), qb = fs.m.get(m.b);
          if (!qa || !qb || !inBar(qa[0], qa[1], bar) || !inBar(qb[0], qb[1], bar)) continue;
          const fu = Math.abs(alongOf(qa, bar) - alongOf(qb, bar));
          if (fu < pick.u) pick = { u: fu, s: fs.t * NS, pa: qa, pb: qb, d: Math.hypot(qa[0] - qb[0], qa[1] - qb[1]) };
        }
        if (!c || pick.u < c.u) c = pick;
      }
      prev = { sign: u, rec: here };
    });
    if (!c) { out.outside++; continue; }
    out.swaps++;
    const oa = acrossOf(c.pa, bar) / g, ob = acrossOf(c.pb, bar) / g;
    const da = Math.abs(oa), db = Math.abs(ob);
    const cls = (da >= MIN_DEV && db >= MIN_DEV) ? (oa * ob < 0 ? "both" : "same")
              : (da >= MIN_DEV || db >= MIN_DEV) ? "one" : "none";
    out[cls]++;
    const gap = c.d / (c.pa[2] + c.pb[2]);
    if (gap < 1 - 1e-9) out.overlap++;
    const dev = Math.min(da, db);
    if (cls === "both" && (out.min_dev_g === null || dev < out.min_dev_g)) out.min_dev_g = +dev.toFixed(4);
    if (out.min_gap_ratio === null || gap < out.min_gap_ratio) out.min_gap_ratio = +gap.toFixed(3);
    keep(cls, { key: cls === "both" ? dev : -Math.max(da, db), frame: f, phase: +(c.s / NS).toFixed(4),
                a: m.a, b: m.b, trap: bar[5], off_g: [+oa.toFixed(4), +ob.toFixed(4)],
                apart_g: +(c.d / g).toFixed(4),
                radii_g: [+(c.pa[2] / g).toFixed(4), +(c.pb[2] / g).toFixed(4)] });
  }
}
for (const k in out.worst) delete out.worst[k].key;
console.log(JSON.stringify(out));
