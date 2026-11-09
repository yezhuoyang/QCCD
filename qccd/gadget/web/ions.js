/* A compiled program's ions, read from its ion file and drawn as the studio draws a machine -- shared by the
 * RSA-2048 player (rsa.js) and each gadget's own page (rsa_gadget.js), so the two cannot draw two machines.
 *
 * An ion file (`qccd.gadget.rsa.ion_bytes`, QIO1 or QIO2) holds every ion's trap at the start and, cycle by
 * cycle, every move (the junctions it passes, the trap it ends in) and every gate, readout and reset.  `decode`
 * reads it into typed arrays with the ions' places kept every SNAP cycles, `at` puts them tau µs into the
 * program, `places` says where in its trap each one sits, and `job` + `paint` draw them.
 *
 * A trap, a junction, a rail and an ion are drawn by the studio's numbers (qccd/viz/theme.py's PALETTE and
 * GEOMETRY, shipped into the page as QCCD_THEME), as a design page and the gadget town draw them
 * (qccd/viz/render.py, qccd/gadget/web/app.js::drawDevice): a trap is a capsule on its axis with a ring a
 * slot, a junction a white square, a rail a butt-capped line, an ion a white-outlined disc coloured by what
 * the machine does to it now.  Every trap of the machine is a `trap` zone of 2 slots.  Grid units: a junction
 * at odd x and even y, a trap between two junctions (even x and y across, odd x and y up and down), a tile
 * G = 2 (d + 1) units a side.
 */
(function (root) {
  "use strict";
  const G = 24;                        // grid units a tile: 2 (d + 1), d = 11
  const SNAP = 16;                     // a program's ion positions kept every SNAP cycles
  const MOVE = 1, PAIR = 2, PULSE = 3, MEASURE = 4, RESET = 5, COOL = 6, VIRTUAL = 7;
  const THEME = root.QCCD_THEME || {}, PAL = THEME.palette || {}, GEO = THEME.geometry || {};
  const pal = (k, v) => PAL[k] || v;
  const INK = {rail: pal("rail", "#f2b8b5"), trap: pal("zone_trap", "#0f766e"), grid: pal("grid", "#8a94a6"),
               panel: pal("panel", "#ffffff"), ion: pal("data", "#475467"), gate: pal("x", "#0f766e"),
               read: pal("z", "#b42318"), active: pal("active", "#f6c34a"), well: pal("anc", "#4338ca"),
               stroke: pal("ion_stroke", "#ffffff"), follow: pal("accent", "#e4572e")};
  const RAIL_W = GEO.RAIL_W_FRAC || 0.083, K_ION = (GEO.ION_D_FRAC_ACTIVE || 0.175) + 0.065, K_REST = GEO.ION_D_FRAC || 0.13;
  const CAP = 2, SITE_LEN = Math.min(0.88, 0.30 + 0.15 * CAP), PITCH = SITE_LEN / CAP;   // layout.py::_site_len, grid units
  const OFF = [[]];                                   // render.py::slotOffsets: k ions' places along a trap's axis
  for (let k = 1; k <= 8; k++) {
    const s0 = Math.floor((CAP - k) / 2), step = Math.min(PITCH, 0.86 / k);
    OFF.push([...Array(k)].map((_, j) => (k <= CAP ? (s0 + j - (CAP - 1) / 2) * PITCH : (j - (k - 1) / 2) * step)));
  }

  async function bytes(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url}: ${r.status}`);
    let buf = new Uint8Array(await r.arrayBuffer());
    if (buf[0] === 0x1f && buf[1] === 0x8b) {            // gzipped, and not undone on the way
      const s = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
      buf = new Uint8Array(await new Response(s).arrayBuffer());
    }
    return buf;
  }

  // ---- a program's ions ------------------------------------------------------------------------

  function decode(buf) {
    // QIO1: per cycle its times and its body; QIO2: each distinct body once, then per cycle its times and a body index
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    const magic = String.fromCharCode(buf[0], buf[1], buf[2], buf[3]);
    if (magic !== "QIO1" && magic !== "QIO2") throw new Error("not an ion file");
    const two = magic === "QIO2";
    const n = dv.getUint32(4, true), nc = dv.getUint32(8, true), us = dv.getFloat64(12, true);
    const kind = new Uint8Array(n), home = new Int16Array(2 * n);
    let p = 20;
    for (let i = 0; i < n; i++, p += 5) { kind[i] = buf[p]; home[2 * i] = dv.getInt16(p + 1, true); home[2 * i + 1] = dv.getInt16(p + 3, true); }
    const nb = two ? dv.getUint32(p, true) : nc;     // bodies
    const at0 = new Uint32Array(nb);                 // where each body starts
    const t0 = new Float32Array(nc), t1 = new Float32Array(nc), bodyOf = new Uint32Array(nc);
    if (two) {
      p += 4;
      for (let b = 0; b < nb; b++) { const len = dv.getUint32(p, true); at0[b] = p + 4; p += 4 + len; }
      for (let c = 0; c < nc; c++, p += 12) { t0[c] = dv.getFloat32(p, true); t1[c] = dv.getFloat32(p + 4, true); bodyOf[c] = dv.getUint32(p + 8, true); }
    } else {
      for (let c = 0; c < nc; c++) {
        t0[c] = dv.getFloat32(p, true); t1[c] = dv.getFloat32(p + 4, true); bodyOf[c] = c; at0[c] = p + 8;
        const w = buf[p + 8], cnt = dv.getUint32(p + 9, true); p += 13;
        if (w === MOVE) for (let j = 0; j < cnt; j++) p += 3 + 4 * buf[p + 2];
        else if (w === PAIR) p += 4 * cnt;
        else if (w !== COOL) p += 2 * cnt;
      }
    }
    let nm = 0, np = 0, ng = 0;                      // first pass: how much of each
    for (let b = 0; b < nb; b++) {
      p = at0[b];
      const w = buf[p], cnt = dv.getUint32(p + 1, true); p += 5;
      if (w === MOVE) for (let j = 0; j < cnt; j++) { const k = buf[p + 2]; nm++; np += k; p += 3 + 4 * k; }
      else if (w === PAIR) ng += 2 * cnt;
      else if (w !== COOL) ng += cnt;
    }
    const what = new Uint8Array(nb);
    const mOff = new Uint32Array(nb + 1), gOff = new Uint32Array(nb + 1);
    const mIon = new Uint16Array(nm), mAt = new Uint32Array(nm + 1), pts = new Int16Array(2 * np), gIon = new Uint16Array(ng);
    nm = 0; np = 0; ng = 0;
    for (let c = 0; c < nb; c++) {
      p = at0[c];
      const w = what[c] = buf[p], cnt = dv.getUint32(p + 1, true); p += 5;
      if (w === MOVE) {
        for (let j = 0; j < cnt; j++) {
          const k = buf[p + 2];
          mIon[nm] = dv.getUint16(p, true); mAt[nm] = np; nm++; p += 3;
          for (let q = 0; q < k; q++, p += 4) { pts[2 * np] = dv.getInt16(p, true); pts[2 * np + 1] = dv.getInt16(p + 2, true); np++; }
        }
      } else if (w === PAIR) { for (let j = 0; j < 2 * cnt; j++, p += 2) gIon[ng++] = dv.getUint16(p, true); }
      else if (w !== COOL) { for (let j = 0; j < cnt; j++, p += 2) gIon[ng++] = dv.getUint16(p, true); }
      mOff[c + 1] = nm; gOff[c + 1] = ng;
    }
    mAt[nm] = np;
    // what, mOff and gOff are a body's; bodyOf maps a cycle to its body (QIO1: itself)
    const P = {n, nc, us, kind, home, t0, t1, bodyOf, what, mOff, gOff, mIon, mAt, pts, gIon, snaps: [], sides: [], arrs: []};
    const cur = new Int16Array(home), side = new Int8Array(n), arr = new Int32Array(n).fill(-1);
    for (let c = 0; c < nc; c++) {
      if (c % SNAP === 0) { P.snaps.push(new Int16Array(cur)); P.sides.push(new Int8Array(side)); P.arrs.push(new Int32Array(arr)); }
      settle(P, c, cur, side, arr);
    }
    P.cur = new Int16Array(2 * n); P.side = new Int8Array(n); P.arr = new Int32Array(n);
    P.offs = new Map();
    return P;
  }

  function settle(P, c, pos, side, arr) {             // every move of cycle c, done; a mover's side is the end of its trap it came in by
    const b = P.bodyOf[c];
    for (let m = P.mOff[b]; m < P.mOff[b + 1]; m++) {
      const a = P.mAt[m], e = P.mAt[m + 1] - 1, i = P.mIon[m];
      const x = P.pts[2 * e], y = P.pts[2 * e + 1];
      const x0 = e > a ? P.pts[2 * e - 2] : pos[2 * i], y0 = e > a ? P.pts[2 * e - 1] : pos[2 * i + 1];
      side[i] = Math.sign((x & 1) ? y0 - y : x0 - x); arr[i] = c;      // a trap at odd x runs up and down
      pos[2 * i] = x; pos[2 * i + 1] = y;
    }
  }

  function at(P, tau) {                               // the traps tau µs into the program (P.cur), the cycle running and, mid-move, how far (P.f)
    let lo = 0, hi = P.nc - 1, c = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (P.t0[m] <= tau) { c = m; lo = m + 1; } else hi = m - 1; }
    const s = c < 0 ? 0 : Math.floor(c / SNAP);
    P.cur.set(P.snaps[s]); P.side.set(P.sides[s]); P.arr.set(P.arrs[s]);
    for (let k = s * SNAP; k < c; k++) settle(P, k, P.cur, P.side, P.arr);
    P.c = c; P.f = -1; P.done = c < 0 ? 0 : c;
    if (c >= 0) {
      if (tau >= P.t1[c]) { settle(P, c, P.cur, P.side, P.arr); P.done = c + 1; }
      else if (P.what[P.bodyOf[c]] === MOVE) P.f = (tau - P.t0[c]) / (P.t1[c] - P.t0[c]);
    }
    return c;
  }

  // Where in its trap each ion sits, as the studio rests ions (render.py::slotOffsets, transit.js's law): a lone ion
  // in the first ring, two side by side, the order carried forward -- an arrival takes the end it came in by.  Mid-move
  // both ends' places: a mover flies from its place in one trap to its place in the next, and an ion that stays slides
  // between its two places over the same interval.
  const trapKey = (x, y) => (x + 8192) * 16384 + (y + 8192);

  function slotted(P, pos, side, arr) {
    const off = new Float32Array(P.n).fill(OFF[1][0]), at = new Map();
    for (let k = 0; k < P.n; k++) {
      const key = trapKey(pos[2 * k], pos[2 * k + 1]), a = at.get(key);
      if (a === undefined) at.set(key, k); else if (typeof a === "number") at.set(key, [a, k]); else a.push(k);
    }
    const rank = (i) => side[i] * (1e7 + arr[i]);
    for (const a of at.values()) {
      if (typeof a === "number") continue;
      a.sort((i, j) => rank(i) - rank(j) || i - j);
      const o = OFF[Math.min(a.length, OFF.length - 1)];
      for (let q = 0; q < a.length; q++) off[a[q]] = o[Math.min(q, o.length - 1)];
    }
    return off;
  }

  function places(P) {                                // [before, after] for the state at() left; one array when nothing is flying
    const key = P.f >= 0 ? -1 - P.c : P.done;
    let v = P.offs.get(key);
    if (v) return v;
    const a = slotted(P, P.cur, P.side, P.arr);
    if (P.f < 0) v = [a, a];
    else {
      const nx = new Int16Array(P.cur), sd = new Int8Array(P.side), ar = new Int32Array(P.arr);
      settle(P, P.c, nx, sd, ar);
      v = [a, slotted(P, nx, sd, ar)];
    }
    if (P.offs.size >= 24) P.offs.delete(P.offs.keys().next().value);
    P.offs.set(key, v);
    return v;
  }

  // ---- the machine ---------------------------------------------------------------------------

  function marks(g) {                                 // app.js::marks: the studio's sizes at g px a grid unit (junction to trap)
    const rIon = Math.min(26, Math.max(Math.min(3, 0.45 * g), K_ION * g));
    const rRest = Math.min(rIon, Math.max(Math.min(1.6, 0.30 * g), K_REST * g)), room = 0.44 * PITCH * g;
    return {g, siteT: RAIL_W * 2.4 * g, swNode: Math.max(1.0, 0.05 * g), swRail: Math.max(1.2, RAIL_W * g),
            swHalo: Math.max(0.7, 0.055 * g), slotR: 0.085 * g, rJunc: 0.30 * g, rActive: 0.46 * g,
            wellRx: 0.44 * g, wellRy: 0.30 * g, rIon,
            // core.js::ionRadius in a trap of 2 slots: at rest, in a gate, and swelling to rIon mid-flight
            rest: Math.max(0.6, Math.min(rRest, room)), busy: Math.max(0.6, Math.min(rIon, room))};
  }

  function drawTrap(g, x, y, across, mk, lit) {       // app.js::drawSite: a capsule on the trap's axis, a ring a slot
    const len = SITE_LEN * mk.g, t = mk.siteT, r = Math.min(t / 2, len / 2);
    g.save(); g.translate(x, y);
    if (across) g.rotate(Math.PI / 2);
    g.beginPath();
    g.moveTo(-len / 2 + r, -t / 2); g.lineTo(len / 2 - r, -t / 2); g.arc(len / 2 - r, 0, r, -Math.PI / 2, Math.PI / 2);
    g.lineTo(-len / 2 + r, t / 2); g.arc(-len / 2 + r, 0, r, Math.PI / 2, -Math.PI / 2); g.closePath();
    g.globalAlpha = 0.16; g.fillStyle = INK.trap; g.fill();
    g.globalAlpha = lit ? 0.95 : 0.55; g.strokeStyle = lit ? INK.active : INK.trap;
    g.lineWidth = lit ? mk.swNode * 1.7 : mk.swNode; g.stroke();
    const sr = Math.min(mk.slotR, 0.36 * len / CAP);
    if (sr > 0.6) {
      g.globalAlpha = 0.55; g.strokeStyle = INK.trap; g.lineWidth = Math.max(0.7, mk.swNode * 0.8);
      for (let i = 0; i < CAP; i++) { g.beginPath(); g.arc(((i + 0.5) / CAP - 0.5) * len, 0, sr, 0, 2 * Math.PI); g.stroke(); }
    }
    g.globalAlpha = 1; g.restore();
  }

  const patterns = new Map();
  function tilePattern(s) {                           // one tile's rails, junctions and traps at s px a tile
    let img = patterns.get(s);
    if (img) return img;
    const n = Math.ceil(s);
    img = document.createElement("canvas"); img.width = n; img.height = n;
    const g = img.getContext("2d"), u = s / G, mk = marks(u), at = (v) => (v + 0.5) * u;
    g.strokeStyle = INK.rail; g.lineWidth = mk.swRail; g.lineCap = "butt";
    g.beginPath();                                   // a junction at odd x, even y; a trap between two junctions
    for (let y = 0; y < G; y += 2) { g.moveTo(0, at(y)); g.lineTo(s, at(y)); }
    for (let x = 1; x < G; x += 2) { g.moveTo(at(x), 0); g.lineTo(at(x), s); }
    g.stroke();
    if (u < 4) {                                     // too small for the real shapes: a dot a node, as app.js keeps it cheap
      g.globalAlpha = 0.45;
      for (let y = 0; y < G; y++) for (let x = 0; x < G; x++) {
        const junction = (x & 1) && !(y & 1), trap = !((x ^ y) & 1);
        if (!junction && !trap) continue;
        g.fillStyle = junction ? INK.grid : INK.trap; g.fillRect(at(x) - 1, at(y) - 1, 2, 2);
      }
      g.globalAlpha = 1;
    } else {
      g.fillStyle = INK.panel; g.strokeStyle = INK.grid; g.lineWidth = mk.swNode;
      for (let y = 0; y < G; y += 2) for (let x = 1; x < G; x += 2) {
        g.beginPath(); g.rect(at(x) - mk.rJunc, at(y) - mk.rJunc, 2 * mk.rJunc, 2 * mk.rJunc); g.fill(); g.stroke();
      }
      for (let y = 0; y < G; y++) for (let x = y & 1; x < G; x += 2) drawTrap(g, at(x), at(y), (y & 1) === 1, mk, false);
    }
    if (patterns.size > 3) patterns.delete(patterns.keys().next().value);
    patterns.set(s, img);
    return img;
  }

  // ---- a program's ions in view ----------------------------------------------------------------

  function batch() { return {disc: [[], [], []], lit: [], wells: []}; }

  // One program's ions tau µs in, into the batch `out`: X, Y map grid units of the program (its (0, 0) tile's
  // corner at 0) to px.  `xy` (Float32Array, 2 n) and `st` (Int8Array, n), when given, get each ion's drawn place
  // and what it does: 0 at rest, 1 in a gate or moving, 2 read or reset.  Returns the cycle running.
  function job(P, tau, X, Y, mk, out, xy, st) {
    const tiny = mk.g < 4, c = at(P, Math.min(tau, P.us)), cur = P.cur;
    const [offA, offB] = places(P), f = P.f;
    const b = c >= 0 ? P.bodyOf[c] : 0, live = c >= 0 && tau < P.t1[c] ? P.what[b] : 0;
    const fly = new Map();                           // a mover: its path's first point in pts
    if (live === MOVE) for (let m = P.mOff[b]; m < P.mOff[b + 1]; m++) fly.set(P.mIon[m], m);
    const doing = new Map();                         // an ion in this cycle's gate (1), readout or reset (2)
    if (live && live !== MOVE && live !== COOL) for (let m = P.gOff[b]; m < P.gOff[b + 1]; m++) doing.set(P.gIon[m], live === MEASURE || live === RESET ? 2 : 1);
    for (let k = 0; k < P.n; k++) {
      const x = cur[2 * k], y = cur[2 * k + 1], up = x & 1;
      const m = fly.get(k);
      let fx, fy, r, s;
      if (m === undefined) {                         // standing: its place in its trap, sliding if a trap-mate comes or goes
        const o = f >= 0 ? offA[k] + (offB[k] - offA[k]) * f : offA[k];
        s = doing.get(k) || 0;
        fx = X(up ? x : x + o); fy = Y(up ? y + o : y); r = s ? mk.busy : mk.rest;
        if (s && !tiny) out.lit.push(X(x), Y(y), up);
      } else {                                       // flying: from its place in the trap it leaves, through the junctions, to its place in the next
        const a = P.mAt[m], e = P.mAt[m + 1] - 1, n = e - a + 1, dx = P.pts[2 * e], dy = P.pts[2 * e + 1];
        const sx0 = up ? x : x + offA[k], sy0 = up ? y + offA[k] : y;
        const ex = (dx & 1) ? dx : dx + offB[k], ey = (dx & 1) ? dy + offB[k] : dy;
        const v = Math.min(n - 1e-6, f * n), q = Math.floor(v), w = v - q;
        const x0 = q === 0 ? sx0 : P.pts[2 * (a + q - 1)], y0 = q === 0 ? sy0 : P.pts[2 * (a + q - 1) + 1];
        const x1 = a + q === e ? ex : P.pts[2 * (a + q)], y1 = a + q === e ? ey : P.pts[2 * (a + q) + 1];
        fx = X(x0 + (x1 - x0) * w); fy = Y(y0 + (y1 - y0) * w);
        s = 1; r = mk.rest + (mk.rIon - mk.rest) * 4 * f * (1 - f);
        if (!tiny && fly.size <= 40) out.wells.push(fx, fy);
      }
      out.disc[s].push(fx, fy, r);
      if (xy) { xy[2 * k] = fx; xy[2 * k + 1] = fy; }
      if (st) st[k] = s;
    }
    return c;
  }

  function paint(g, out, mk, follow) {               // the batch: halos and lit traps, wells, then the ions; `follow` [x, y, r] in the accent
    if (out.lit.length) {                            // the halo under a trap in play, the trap drawn over it lit
      g.fillStyle = INK.active; g.globalAlpha = 0.5; g.beginPath();
      for (let i = 0; i < out.lit.length; i += 3) { g.moveTo(out.lit[i] + mk.rActive, out.lit[i + 1]); g.arc(out.lit[i], out.lit[i + 1], mk.rActive, 0, 2 * Math.PI); }
      g.fill(); g.globalAlpha = 1;
      for (let i = 0; i < out.lit.length; i += 3) drawTrap(g, out.lit[i], out.lit[i + 1], out.lit[i + 2] === 1, mk, true);
    }
    if (out.wells.length) {                          // the potential well under a moving ion
      g.fillStyle = INK.well; g.globalAlpha = 0.16; g.beginPath();
      for (let i = 0; i < out.wells.length; i += 2) { g.moveTo(out.wells[i] + mk.wellRx, out.wells[i + 1]); g.ellipse(out.wells[i], out.wells[i + 1], mk.wellRx, mk.wellRy, 0, 0, 2 * Math.PI); }
      g.fill(); g.globalAlpha = 1;
    }
    [INK.ion, INK.gate, INK.read].forEach((ink, s) => {
      const a = out.disc[s];
      if (!a.length) return;
      g.fillStyle = ink; g.strokeStyle = INK.stroke; g.lineWidth = mk.swHalo;
      g.beginPath();
      for (let i = 0; i < a.length; i += 3) { g.moveTo(a[i] + a[i + 2], a[i + 1]); g.arc(a[i], a[i + 1], a[i + 2], 0, 2 * Math.PI); }
      g.fill();
      if (mk.rest >= 1.6) g.stroke();                // the white edge, once a disc is big enough to carry one
    });
    if (follow) {
      g.fillStyle = INK.follow; g.strokeStyle = INK.stroke; g.lineWidth = Math.max(1, mk.swHalo);
      g.beginPath(); g.arc(follow[0], follow[1], Math.max(2.5, follow[2] * 1.15), 0, 2 * Math.PI); g.fill(); g.stroke();
    }
  }

  root.QCCDIons = {G, SNAP, MOVE, PAIR, PULSE, MEASURE, RESET, COOL, VIRTUAL, INK, OFF, PITCH,
                   bytes, decode, at, places, marks, drawTrap, tilePattern, batch, job, paint};
})(typeof window !== "undefined" ? window : globalThis);
