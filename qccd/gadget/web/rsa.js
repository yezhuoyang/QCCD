/* RSA-2048 at d = 11: one prime of Gidney 2025 played on the machine's floorplan, step by step, at
 * any level of detail -- the whole machine, its patches, the gadgets at work, every ion.
 *
 * The prime is 358 steps run end to end on one clock.  A step's gadgets (one file each, gzipped:
 * per gadget its start in µs, its program, its tiles) are fetched when the clock nears it and
 * dropped once it is behind, so no more than four steps are ever held -- the whole prime (282
 * million gadgets) never is.  The clock waits for a step that has not arrived instead of
 * skipping it, so what is drawn is always the schedule itself.
 *
 * Levels (Auto follows the zoom, as a map does; the reader may fix one):
 *   machine   the floorplan, a lit tile where a gadget works
 *   patches   every tile, a patch where a block is held
 *   gadgets   each gadget at work outlined and named
 *   ions      every trap and ion: a gadget's ions as its compiled program moves them, a held
 *             patch's in its syndrome round, a free tile's at rest
 * A gadget is a translated copy of one compiled program, so its ions are that program's file
 * (data/ions/KEY.bin.gz, written by `qccd.gadget.rsa.ion_bytes`), fetched the first time a gadget
 * of it is in view.
 *
 * Data, beside the page: data/meta.json (the steps, their parts and times), data/programs.json
 * (every distinct program, its exact time and its check at d = 11), data/layout.json (the
 * floorplan; its own `names` and `colors` if it has them), data/frames/NNN.bin.gz,
 * data/live/NNN.bin.gz (the tiles holding a block as each step starts), data/ions/ and
 * data/pages/ (each program's ions, QIO1 or QIO2, and its design page).
 *
 * A step whose schedule runs on from the step before (a flat schedule has no barriers) has a
 * `lead`: its frame also holds the gadgets still at work as it starts, and its times count from
 * `lead` µs before the step, so its live bits are the tiles holding a block at that moment.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const DATA = "data/";
  const KEEP = 4;                      // steps decoded at most: the one before, this one, two ahead
  const AHEAD = 2;
  const TRAIL = 0.05;                  // s of the reader's time a gadget stays drawn, at least (coarse levels)
  const G = 24;                        // grid units a tile: 2 (d + 1), d = 11
  const SNAP = 16;                     // a program's ion positions kept every SNAP cycles
  const LEVELS = ["machine", "patches", "gadgets", "ions"];
  const AUTO_AT = {patches: 4, gadgets: 16, ions: 160};         // px a tile where Auto moves on
  const LEAST = {machine: 0, patches: 2.5, gadgets: 9, ions: 96};
  const AUTO_SPEED = {machine: 10, patches: 10, gadgets: 0.1, ions: 0.001};
  const ZMIN = 0.2, ZMAX = 1200;
  const COLOR = {merge: "#2e6fd8", prep: "#12a150", read: "#e08a00", ht: "#8b3fd0", idle: "#7a8496",
                 "|T>": "#c2185b", "|Y+>": "#0f9d9a", h: "#8b3fd0", cold: "#795548"};
  const HOLDS = new Set(["prep", "|T>", "|Y+>", "h", "cold", "idle"]);   // a tile holds a block once one of these is done
  const BASE = {factory: "#e7e9ee", site: "#c8ccd6", seat: "#9aa3b5", bus: "#fbfbfd",
                dlog: "#7cc4a4", help: "#b39ddb", acc: "#f4a7a7", res: "#f2cf86",
                pp: "#8fb3e8", addr: "#5b7fc7", exp: "#d6dbe4"};
  const NAMES = {dlog: "dlog", help: "help", acc: "acc", res: "res", pp: "powerProduct (pp)", addr: "addr",
                 exp: "the exponent", D: "dlog's factories", H: "help's factories", A: "acc's factories",
                 P: "powerProduct's factories"};
  const MOVE = 1, PAIR = 2, PULSE = 3, MEASURE = 4, RESET = 5, COOL = 6;
  // A trap, a junction, a rail and an ion are drawn by the studio's numbers (qccd/viz/theme.py's PALETTE and
  // GEOMETRY, shipped into the page as QCCD_THEME), as a design page and the gadget town draw them
  // (qccd/viz/render.py, qccd/gadget/web/app.js::drawDevice): a trap is a capsule on its axis with a ring a
  // slot, a junction a white square, a rail a butt-capped line, an ion a white-outlined disc coloured by what
  // the machine does to it now.  Every trap of the machine is a `trap` zone of 2 slots.
  const THEME = window.QCCD_THEME || {}, PAL = THEME.palette || {}, GEO = THEME.geometry || {};
  const pal = (k, v) => PAL[k] || v;
  const INK = {rail: pal("rail", "#f2b8b5"), trap: pal("zone_trap", "#0f766e"), grid: pal("grid", "#8a94a6"),
               panel: pal("panel", "#ffffff"), ion: pal("data", "#475467"), gate: pal("x", "#0f766e"),
               read: pal("z", "#b42318"), active: pal("active", "#f6c34a"), well: pal("anc", "#4338ca"),
               stroke: pal("ion_stroke", "#ffffff")};
  const RAIL_W = GEO.RAIL_W_FRAC || 0.083, K_ION = (GEO.ION_D_FRAC_ACTIVE || 0.175) + 0.065, K_REST = GEO.ION_D_FRAC || 0.13;
  const CAP = 2, SITE_LEN = Math.min(0.88, 0.30 + 0.15 * CAP), PITCH = SITE_LEN / CAP;   // layout.py::_site_len, grid units
  const OFF = [[]];                                   // render.py::slotOffsets: k ions' places along a trap's axis
  for (let k = 1; k <= 8; k++) {
    const s0 = Math.floor((CAP - k) / 2), step = Math.min(PITCH, 0.86 / k);
    OFF.push([...Array(k)].map((_, j) => (k <= CAP ? (s0 + j - (CAP - 1) / 2) * PITCH : (j - (k - 1) / 2) * step)));
  }
  let meta, progs, layout, kindOf, durOf, maxDur, ionsOf, W, H, X0, Y0, idleId = -1;
  const steps = new Map();            // step -> {promise, data, live}
  const ionFiles = new Map();         // program -> {promise, data}
  let t = 0;                          // the clock, s from the prime's start
  let playing = false, speedSel = "auto", last = null, waiting = false;
  let view = {s: 1, x: 0, y: 0};      // px per tile, the tile at the canvas's left/top
  let detail = "auto";
  let baseImg = null, baseKey = "", card = null, stats = {active: 0, ions: 0};
  let hashDue = 0;

  function pad(i) { return String(i).padStart(3, "0"); }
  const level = () => detail !== "auto" ? detail : view.s >= AUTO_AT.ions ? "ions" :
    view.s >= AUTO_AT.gadgets ? "gadgets" : view.s >= AUTO_AT.patches ? "patches" : "machine";
  const speed = () => speedSel === "auto" ? AUTO_SPEED[level()] : Number(speedSel);
  const tid = (x, y) => (y - Y0) * W + (x - X0);
  const inside = (x, y) => x >= X0 && y >= Y0 && x < X0 + W && y < Y0 + H;

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

  // ---- a step: its gadgets and the tiles holding a block as it starts ------------------------

  function decode(buf, step) {
    // records: uint32 start, uint16 program (the step's own index), uint8 n, n x (int16 x, int16 y)
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    let n = 0, nt = 0;
    for (let p = 0; p < buf.length; n++) { const k = buf[p + 6]; nt += k; p += 7 + 4 * k; }
    const start = new Uint32Array(n), prog = new Uint16Array(n), off = new Uint32Array(n + 1);
    const tiles = new Int16Array(2 * nt);
    const local = meta.steps[step].programs;          // the step's index -> the page's program
    for (let i = 0, p = 0, q = 0; i < n; i++) {
      start[i] = dv.getUint32(p, true);
      prog[i] = local[dv.getUint16(p + 4, true)];
      const k = buf[p + 6];
      off[i] = q; p += 7;
      for (let j = 0; j < 2 * k; j++, p += 2) tiles[q++] = dv.getInt16(p, true);
    }
    off[n] = nt * 2;
    return {n, start, prog, off, tiles, idx: null};
  }

  function bits(buf) {                               // one bit a tile, row by row, the low bit first
    const live = new Uint8Array(W * H);
    for (let i = 0; i < W * H; i++) live[i] = (buf[i >> 3] >> (i & 7)) & 1;
    return live;
  }

  function load(i) {
    if (i < 0 || i >= meta.steps.length) return null;
    if (!steps.has(i)) {
      const e = {data: null, live: null};
      e.promise = Promise.all([bytes(`${DATA}frames/${pad(i)}.bin.gz`), bytes(`${DATA}live/${pad(i)}.bin.gz`)])
        .then(([f, l]) => { e.live = bits(l); e.data = decode(f, i); })
        .catch((err) => { e.error = String(err); steps.delete(i); });
      steps.set(i, e);
    }
    return steps.get(i);
  }

  const lead = (i) => meta.steps[i].lead || 0;       // µs of the step before that its frame's times count from

  function stepAt(time) {                             // the step running at `time`
    const s = meta.steps;
    let lo = 0, hi = s.length - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (s[m].t0 <= time) lo = m; else hi = m - 1; }
    return lo;
  }

  function keep(i) {                                  // fetch ahead, drop what is behind
    for (let k = i - 1; k <= i + AHEAD; k++) load(k);
    for (const k of [...steps.keys()]) if (k < i - 1 || k > i + KEEP - 2) steps.delete(k);
  }

  function origin(d, i) {                             // a gadget's least x and least y: its program's (0, 0) tile
    let x = Infinity, y = Infinity;
    for (let q = d.off[i]; q < d.off[i + 1]; q += 2) { x = Math.min(x, d.tiles[q]); y = Math.min(y, d.tiles[q + 1]); }
    return [x, y];
  }

  function index(d, r) {                              // the step's gadgets on each tile of the area r, in start order
    if (d.idx && r[0] >= d.idx.r[0] && r[1] >= d.idx.r[1] && r[2] <= d.idx.r[2] && r[3] <= d.idx.r[3]) return d.idx;
    const wx = r[2] - r[0], wy = r[3] - r[1];
    const R = [r[0] - (wx >> 1) - 2, r[1] - (wy >> 1) - 2, r[2] + (wx >> 1) + 2, r[3] + (wy >> 1) + 2];
    const m = new Map();
    for (let i = 0; i < d.n; i++) {
      for (let q = d.off[i]; q < d.off[i + 1]; q += 2) {
        const x = d.tiles[q], y = d.tiles[q + 1];
        if (x < R[0] || x > R[2] || y < R[1] || y > R[3]) continue;
        const k = tid(x, y);
        let a = m.get(k); if (!a) m.set(k, a = []);
        a.push(i);
      }
    }
    return (d.idx = {r: R, m});
  }

  function role(d, i, x, y) {                         // does tile (x, y) hold a block once gadget i is done?
    const p = d.prog[i], k = kindOf[p];
    if (HOLDS.has(k)) return true;
    if (k === "read") return false;
    const [ox, oy] = origin(d, i);
    if (k === "ht") return x === ox;
    return progs[p].patchSet.has(`${x - ox},${y - oy}`);
  }

  function tileState(e, idx, x, y, us) {              // {g: the gadget at work or -1, live, since (µs)}
    const d = e.data, a = idx.m.get(tid(x, y));
    if (a) {
      let lo = 0, hi = a.length - 1, g = -1;
      while (lo <= hi) { const m = (lo + hi) >> 1; if (d.start[a[m]] <= us) { g = a[m]; lo = m + 1; } else hi = m - 1; }
      if (g >= 0) {
        const end = d.start[g] + durOf[d.prog[g]];
        if (us < end) return {g, live: true, since: d.start[g]};
        return {g: -1, live: role(d, g, x, y), since: end};
      }
    }
    return {g: -1, live: inside(x, y) && e.live[tid(x, y)] === 1, since: 0};
  }

  // ---- a program's ions ------------------------------------------------------------------------

  function decodeIons(buf) {
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

  function ions(p) {                                  // a program's ion file, fetched once
    if (!ionFiles.has(p)) {
      const e = {data: null};
      if (!progs[p].key) { e.error = "no ion file"; ionFiles.set(p, e); return e; }
      e.promise = bytes(`${DATA}ions/${progs[p].key}.bin.gz`).then((b) => (e.data = decodeIons(b)))
        .catch((err) => { e.error = String(err); });
      ionFiles.set(p, e);
    }
    return ionFiles.get(p);
  }

  // ---- drawing -----------------------------------------------------------------------------

  const px = (x) => (x - view.x) * view.s, py = (y) => (y - view.y) * view.s;

  function area() {                                   // the tiles in view
    const c = $("rsa-canvas");
    return [Math.floor(view.x), Math.floor(view.y), Math.ceil(view.x + c.width / view.s), Math.ceil(view.y + c.height / view.s)];
  }

  function fit() {                                   // the whole machine's height, at its busiest part
    const c = $("rsa-canvas"), b = layout.bounds, pp = layout.registers.pp;
    if (!pp) {                                       // a machine that fits: all of it
      view.s = Math.min(c.height / (b[3] - b[1] + 3), c.width / (b[2] - b[0] + 3));
      view.x = (b[0] + b[2] + 1) / 2 - c.width / view.s / 2; view.y = (b[1] + b[3] + 1) / 2 - c.height / view.s / 2;
      return;
    }
    view.s = c.height / (b[3] - b[1] + 3);
    const mid = pp[pp.length >> 1][0];
    view.x = mid - c.width / view.s / 2; view.y = b[1] - 1;
  }

  function base(lv) {
    const c = $("rsa-canvas");
    const key = `${c.width}x${c.height}:${view.s}:${view.x}:${view.y}:${lv}`;
    if (key === baseKey) return baseImg;
    const off = document.createElement("canvas");
    off.width = c.width; off.height = c.height;
    const g = off.getContext("2d");
    g.fillStyle = BASE.bus; g.fillRect(0, 0, off.width, off.height);
    const s = view.s, w = Math.max(1, s), gap = s >= 8 ? Math.max(1, s / 14) : 0;
    g.fillStyle = BASE.factory;
    for (const [, x0, y0, x1, y1] of layout.factories) g.fillRect(px(x0), py(y0), (x1 - x0 + 1) * s, (y1 - y0 + 1) * s);
    if (lv === "machine" || lv === "patches") {
      g.fillStyle = BASE.site;
      for (const [x, y] of layout.sites) g.fillRect(px(x), py(y), w, w);
      for (const [r, tiles] of Object.entries(layout.registers)) {
        g.fillStyle = BASE[r] || "#999";
        for (const [x, y] of tiles) g.fillRect(px(x), py(y), w, w);
      }
      g.fillStyle = BASE.seat;
      for (const [x, y] of layout.seats) g.fillRect(px(x), py(y), w, w);
    }
    if (gap && lv !== "ions") {                      // the tiles' edges, once a tile is big enough to see (not across the rails)
      const [ax, ay, bx, by] = area();
      g.strokeStyle = "rgba(60,70,90,.13)"; g.lineWidth = gap;
      g.beginPath();
      for (let x = Math.max(ax, X0); x <= Math.min(bx, X0 + W); x++) { g.moveTo(px(x), py(Math.max(ay, Y0))); g.lineTo(px(x), py(Math.min(by, Y0 + H))); }
      for (let y = Math.max(ay, Y0); y <= Math.min(by, Y0 + H); y++) { g.moveTo(px(Math.max(ax, X0)), py(y)); g.lineTo(px(Math.min(bx, X0 + W)), py(y)); }
      g.stroke();
    }
    if (lv === "ions") {                             // every tile's traps and rails
      const pat = tilePattern(s), [ax, ay, bx, by] = area();
      for (let y = Math.max(ay, Y0); y < Math.min(by, Y0 + H); y++)
        for (let x = Math.max(ax, X0); x < Math.min(bx, X0 + W); x++) g.drawImage(pat, px(x), py(y));
    }
    labels(g, lv);
    baseKey = key; baseImg = off;
    return off;
  }

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

  function tilePattern(s) {                           // one tile's rails, junctions and traps at s px a tile
    if (tilePattern.s === s) return tilePattern.img;
    const n = Math.ceil(s), off = document.createElement("canvas"); off.width = n; off.height = n;
    const g = off.getContext("2d"), u = s / G, mk = marks(u), at = (v) => (v + 0.5) * u;
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
    tilePattern.s = s; tilePattern.img = off;
    return off;
  }

  function labels(g, lv) {                            // the floorplan's names, as a map writes its places'
    if (lv === "ions") return;
    g.font = `600 ${Math.max(11, Math.min(15, view.s * 1.6))}px system-ui, sans-serif`;
    g.textAlign = "center"; g.textBaseline = "middle"; g.lineJoin = "round";
    const c = $("rsa-canvas"), placed = [];
    for (const [name, [x0, y0, x1, y1]] of layout.places) {
      const cx = px((x0 + x1 + 1) / 2), cy = py((y0 + y1 + 1) / 2), wpx = (x1 - x0 + 1) * view.s;
      const text = NAMES[name] || name, tw = g.measureText(text).width;
      if (wpx < tw * 0.8 || cx < -tw || cx > c.width + tw || cy < -10 || cy > c.height + 10) continue;
      const box = [cx - tw / 2 - 4, cy - 9, cx + tw / 2 + 4, cy + 9];
      if (placed.some((b) => b[0] < box[2] && box[0] < b[2] && b[1] < box[3] && box[1] < b[3])) continue;
      placed.push(box);
      g.strokeStyle = "rgba(255,255,255,.9)"; g.lineWidth = 4; g.strokeText(text, cx, cy);
      g.fillStyle = "#3a4256"; g.fillText(text, cx, cy);
    }
  }

  function outline(g, d, i, color, width) {           // the edge of a gadget's tiles
    const set = new Set();
    for (let q = d.off[i]; q < d.off[i + 1]; q += 2) set.add(`${d.tiles[q]},${d.tiles[q + 1]}`);
    g.strokeStyle = color; g.lineWidth = width; g.beginPath();
    for (let q = d.off[i]; q < d.off[i + 1]; q += 2) {
      const x = d.tiles[q], y = d.tiles[q + 1], X = px(x), Y = py(y), s = view.s;
      if (!set.has(`${x},${y - 1}`)) { g.moveTo(X, Y); g.lineTo(X + s, Y); }
      if (!set.has(`${x},${y + 1}`)) { g.moveTo(X, Y + s); g.lineTo(X + s, Y + s); }
      if (!set.has(`${x - 1},${y}`)) { g.moveTo(X, Y); g.lineTo(X, Y + s); }
      if (!set.has(`${x + 1},${y}`)) { g.moveTo(X + s, Y); g.lineTo(X + s, Y + s); }
    }
    g.stroke();
  }

  function short(p) {
    const pr = progs[p], k = pr.kind;
    if (k === "merge") return `${pr.shape[1]}̄${pr.shape[1]}̄ merge` + (pr.shape[3].length ? ` · ${pr.shape[3].length}-tile corridor` : "");
    if (k === "ht") return "H̄";
    if (k === "idle") return "syndrome round";
    return `${k} ${pr.shape[1]}`;
  }

  function draw(e, local) {
    const c = $("rsa-canvas"), g = c.getContext("2d"), lv = level();
    g.drawImage(base(lv), 0, 0);
    stats = {active: 0, ions: 0, drawn: 0, loading: 0};
    const d = e && e.data;
    if (!d) return;
    const us = local * 1e6, coarse = lv === "machine" || lv === "patches";
    const trail = coarse ? TRAIL * speed() * 1e6 : 0, from = us - Math.max(maxDur, trail);
    let lo = 0, hi = d.n;                            // the first gadget that may still be drawn
    while (lo < hi) { const m = (lo + hi) >> 1; if (d.start[m] < from) lo = m + 1; else hi = m; }
    const r = area(), s = view.s, w = Math.max(1.5, s), shown = [];
    if (lv !== "machine") {                          // the held patches
      const idx = index(d, r);
      const ax = Math.max(r[0], X0), ay = Math.max(r[1], Y0), bx = Math.min(r[2], X0 + W), by = Math.min(r[3], Y0 + H);
      const pad = lv === "ions" ? 0 : Math.max(0.5, s / 9);
      g.fillStyle = lv === "ions" ? "rgba(122,132,150,.10)" : "#8d96a8";
      const idle = [];
      for (let y = ay; y < by; y++) for (let x = ax; x < bx; x++) {
        const st = tileState(e, idx, x, y, us);
        if (st.g < 0 && st.live) {
          g.fillRect(px(x) + pad, py(y) + pad, s - 2 * pad, s - 2 * pad);
          if (lv === "ions") idle.push([x, y, st.since]);
        }
      }
      if (lv === "ions") drawIons(g, d, lo, us, r, idle);
    }
    for (let i = lo; i < d.n && d.start[i] <= us; i++) {
      const p = d.prog[i], end = d.start[i] + durOf[p];
      if (Math.max(end, d.start[i] + trail) <= us) continue;
      if (end > us) { stats.active++; stats.ions += ionsOf[p]; }
      if (lv === "ions") continue;
      const q0 = d.off[i], q1 = d.off[i + 1];
      let vis = false;
      for (let q = q0; q < q1; q += 2) if (d.tiles[q] >= r[0] - 1 && d.tiles[q] <= r[2] && d.tiles[q + 1] >= r[1] - 1 && d.tiles[q + 1] <= r[3]) { vis = true; break; }
      if (!vis) continue;
      g.fillStyle = COLOR[kindOf[p]] || "#000";
      if (lv === "gadgets") g.globalAlpha = 0.78;
      for (let q = q0; q < q1; q += 2) g.fillRect(px(d.tiles[q]), py(d.tiles[q + 1]), w, w);
      g.globalAlpha = 1;
      if (lv === "gadgets") shown.push(i);
    }
    for (const i of shown) {                          // each gadget at work outlined, named, how far along
      outline(g, d, i, "#1b2333", Math.max(1.2, s / 18));
      const p = d.prog[i];
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let q = d.off[i]; q < d.off[i + 1]; q += 2) { x0 = Math.min(x0, d.tiles[q]); x1 = Math.max(x1, d.tiles[q]); y0 = Math.min(y0, d.tiles[q + 1]); y1 = Math.max(y1, d.tiles[q + 1]); }
      const bw = (x1 - x0 + 1) * s, text = short(p);
      g.font = `600 ${Math.max(10, Math.min(14, s / 3))}px system-ui, sans-serif`;
      if (g.measureText(text).width > bw * 1.6 + 30) continue;
      const cx = px((x0 + x1 + 1) / 2), cy = py((y0 + y1 + 1) / 2);
      g.textAlign = "center"; g.textBaseline = "middle"; g.lineJoin = "round";
      g.strokeStyle = "rgba(255,255,255,.95)"; g.lineWidth = 4; g.strokeText(text, cx, cy - 6);
      g.fillStyle = "#141a26"; g.fillText(text, cx, cy - 6);
      const f = Math.min(1, (us - d.start[i]) / durOf[p]), bl = Math.min(bw * 0.8, 90);
      g.fillStyle = "rgba(255,255,255,.9)"; g.fillRect(cx - bl / 2, cy + 6, bl, 4);
      g.fillStyle = "#141a26"; g.fillRect(cx - bl / 2, cy + 6, bl * f, 4);
    }
    if (card && card.step === stepAt(t) && card.g >= 0 && lv !== "ions") outline(g, d, card.g, "#ff8c1a", Math.max(2, s / 10));
  }

  function drawIons(g, d, lo, us, r, idle) {          // every ion in view, each where its program has it now
    const s = view.s, u = s / G, mk = marks(u), tiny = u < 4;
    const taken = new Set(), jobs = [];
    const key = (X, Y) => (X + 20000) * 8192 + (Y + 2000);
    for (let i = lo; i < d.n && d.start[i] <= us; i++) {
      const p = d.prog[i];
      if (d.start[i] + durOf[p] <= us) continue;
      let vis = false;
      for (let q = d.off[i]; q < d.off[i + 1]; q += 2) if (d.tiles[q] >= r[0] - 1 && d.tiles[q] <= r[2] && d.tiles[q + 1] >= r[1] - 1 && d.tiles[q + 1] <= r[3]) { vis = true; break; }
      if (vis) jobs.push({p, o: origin(d, i), tau: us - d.start[i], i});
    }
    if (idleId >= 0) for (const [x, y, since] of idle) jobs.push({p: idleId, o: [x, y], tau: 0, since, idle: true});
    for (const j of jobs) {                          // the traps a program's ions start in are its own
      const f = ions(j.p);
      if (!f.data) { if (!f.error) stats.loading++; continue; }
      const P = f.data;
      if (j.idle) j.tau = ((us - j.since) % P.us + P.us) % P.us;
      j.P = P;
      for (let k = 0; k < P.n; k++) taken.add(key(j.o[0] * G + P.home[2 * k], j.o[1] * G + P.home[2 * k + 1]));
    }
    // every ion as a disc, batched by colour: [x, y, r] triples for at rest, in a gate (or moving), read or reset
    const disc = [[], [], []], lit = [], wells = [];
    const ax = Math.max(r[0], X0), ay = Math.max(r[1], Y0), bx = Math.min(r[2], X0 + W), by = Math.min(r[3], Y0 + H);
    for (let y = ay; y < by; y++) for (let x = ax; x < bx; x++) {           // a free trap's ion, at rest in its first ring
      for (let gy = 0; gy < G; gy++) for (let gx = gy & 1; gx < G; gx += 2) {
        if (taken.has(key(x * G + gx, y * G + gy))) continue;
        const o = OFF[1][0] / G, up = gy & 1;
        disc[0].push(px(x + (gx + 0.5) / G + (up ? 0 : o)), py(y + (gy + 0.5) / G + (up ? o : 0)), mk.rest);
        stats.drawn++;
      }
    }
    for (const j of jobs) {
      if (!j.P) continue;
      const P = j.P, c = at(P, Math.min(j.tau, P.us)), cur = P.cur, ox = j.o[0], oy = j.o[1];
      const [offA, offB] = places(P), f = P.f;
      const b = c >= 0 ? P.bodyOf[c] : 0, live = c >= 0 && j.tau < P.t1[c] ? P.what[b] : 0;
      const X = (gx) => px(ox + (gx + 0.5) / G), Y = (gy) => py(oy + (gy + 0.5) / G);
      const fly = new Map();                         // a mover: its path's first point in pts
      if (live === MOVE) for (let m = P.mOff[b]; m < P.mOff[b + 1]; m++) fly.set(P.mIon[m], m);
      const doing = new Map();                       // an ion in this cycle's gate (1), readout or reset (2)
      if (live && live !== MOVE && live !== COOL) for (let m = P.gOff[b]; m < P.gOff[b + 1]; m++) doing.set(P.gIon[m], live === MEASURE || live === RESET ? 2 : 1);
      for (let k = 0; k < P.n; k++) {
        const x = cur[2 * k], y = cur[2 * k + 1], up = x & 1;
        const m = fly.get(k);
        if (m === undefined) {                       // standing: its place in its trap, sliding if a trap-mate comes or goes
          const o = f >= 0 ? offA[k] + (offB[k] - offA[k]) * f : offA[k], st = doing.get(k) || 0;
          disc[st].push(X(up ? x : x + o), Y(up ? y + o : y), st ? mk.busy : mk.rest);
          if (st && !tiny) lit.push(X(x), Y(y), up);
          continue;
        }
        // flying: from its place in the trap it leaves, through the junctions, to its place in the trap it enters
        const a = P.mAt[m], e = P.mAt[m + 1] - 1, n = e - a + 1, dx = P.pts[2 * e], dy = P.pts[2 * e + 1];
        const sx0 = up ? x : x + offA[k], sy0 = up ? y + offA[k] : y;
        const ex = (dx & 1) ? dx : dx + offB[k], ey = (dx & 1) ? dy + offB[k] : dy;
        const v = Math.min(n - 1e-6, f * n), q = Math.floor(v), w = v - q;
        const x0 = q === 0 ? sx0 : P.pts[2 * (a + q - 1)], y0 = q === 0 ? sy0 : P.pts[2 * (a + q - 1) + 1];
        const x1 = a + q === e ? ex : P.pts[2 * (a + q)], y1 = a + q === e ? ey : P.pts[2 * (a + q) + 1];
        const fx = X(x0 + (x1 - x0) * w), fy = Y(y0 + (y1 - y0) * w);
        disc[1].push(fx, fy, mk.rest + (mk.rIon - mk.rest) * 4 * f * (1 - f));
        if (!tiny && fly.size <= 40) wells.push(fx, fy);
      }
      stats.drawn += P.n;
    }
    if (lit.length) {                                // the halo under a trap in play, the trap drawn over it lit
      g.fillStyle = INK.active; g.globalAlpha = 0.5; g.beginPath();
      for (let i = 0; i < lit.length; i += 3) { g.moveTo(lit[i] + mk.rActive, lit[i + 1]); g.arc(lit[i], lit[i + 1], mk.rActive, 0, 2 * Math.PI); }
      g.fill(); g.globalAlpha = 1;
      for (let i = 0; i < lit.length; i += 3) drawTrap(g, lit[i], lit[i + 1], lit[i + 2] === 1, mk, true);
    }
    if (wells.length) {                              // the potential well under a moving ion
      g.fillStyle = INK.well; g.globalAlpha = 0.16; g.beginPath();
      for (let i = 0; i < wells.length; i += 2) { g.moveTo(wells[i] + mk.wellRx, wells[i + 1]); g.ellipse(wells[i], wells[i + 1], mk.wellRx, mk.wellRy, 0, 0, 2 * Math.PI); }
      g.fill(); g.globalAlpha = 1;
    }
    [INK.ion, INK.gate, INK.read].forEach((ink, st) => {
      const a = disc[st];
      if (!a.length) return;
      g.fillStyle = ink; g.strokeStyle = INK.stroke; g.lineWidth = mk.swHalo;
      g.beginPath();
      for (let i = 0; i < a.length; i += 3) { g.moveTo(a[i] + a[i + 2], a[i + 1]); g.arc(a[i], a[i + 1], a[i + 2], 0, 2 * Math.PI); }
      g.fill();
      if (mk.rest >= 1.6) g.stroke();                // the white edge, once a disc is big enough to carry one
    });
    for (const j of jobs) if (j.P && !j.idle) outline(g, d, j.i, card && card.g === j.i ? "#ff8c1a" : COLOR[kindOf[j.p]], Math.max(1.5, s / 60));
  }

  function map(data, local) {                        // the whole machine, small, and where the view is
    const c = $("rsa-map"), g = c.getContext("2d"), b = layout.bounds;
    const s = Math.min(c.width / (b[2] - b[0] + 1), c.height / (b[3] - b[1] + 1));
    const ox = (c.width - (b[2] - b[0] + 1) * s) / 2, oy = (c.height - (b[3] - b[1] + 1) * s) / 2;
    if (!map.img || map.w !== c.width) {
      const off = document.createElement("canvas"); off.width = c.width; off.height = c.height;
      const h = off.getContext("2d");
      h.fillStyle = BASE.bus; h.fillRect(0, 0, off.width, off.height);
      h.fillStyle = BASE.factory;
      for (const [, x0, y0, x1, y1] of layout.factories) h.fillRect(ox + (x0 - b[0]) * s, oy + (y0 - b[1]) * s, (x1 - x0 + 1) * s, (y1 - y0 + 1) * s);
      for (const [r, tiles] of Object.entries(layout.registers)) {
        h.fillStyle = BASE[r] || "#999";
        for (const [x, y] of tiles) h.fillRect(ox + (x - b[0]) * s, oy + (y - b[1]) * s, Math.max(1, s), Math.max(1, s));
      }
      map.img = off; map.w = c.width;
    }
    g.drawImage(map.img, 0, 0);
    if (data) {
      const us = local * 1e6, trail = TRAIL * Math.max(speed(), 1) * 1e6, from = us - Math.max(maxDur, trail);
      let lo = 0, hi = data.n;
      while (lo < hi) { const m = (lo + hi) >> 1; if (data.start[m] < from) lo = m + 1; else hi = m; }
      for (let i = lo; i < data.n && data.start[i] <= us; i++) {
        const p = data.prog[i];
        if (data.start[i] + Math.max(durOf[p], trail) <= us) continue;
        g.fillStyle = COLOR[kindOf[p]] || "#000";
        const q = data.off[i];
        g.fillRect(ox + (data.tiles[q] - b[0]) * s, oy + (data.tiles[q + 1] - b[1]) * s, Math.max(1.5, s), Math.max(1.5, s));
      }
    }
    const v = $("rsa-canvas"), vw = Math.max(3, v.width / view.s * s), vh = Math.max(3, v.height / view.s * s);
    g.strokeStyle = "#111"; g.lineWidth = 1.5;
    g.strokeRect(ox + (view.x - b[0]) * s, oy + (view.y - b[1]) * s, vw, vh);
    map.scale = s; map.ox = ox; map.oy = oy;
  }

  function fmt(s) {
    const fine = speed() < 0.1;
    if (s < 60) return fine ? `${s.toFixed(6)} s` : `${s.toFixed(2)} s`;
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? `${h} h ` : "") + `${m} min ${fine ? r.toFixed(6) : r.toFixed(1)} s`;
  }

  function rate(v) {
    return v >= 1 ? `${v} s a second` : v >= 0.001 ? `${v * 1000} ms a second` : `${v * 1e6} µs a second`;
  }

  function frame(now) {
    if (playing && last !== null && !waiting) t = Math.min(meta.prime_s, t + Math.min(0.1, (now - last) / 1000) * speed());
    last = now;
    if (t >= meta.prime_s) playing = false;
    const i = stepAt(t), st = meta.steps[i];
    keep(i);
    const e = steps.get(i);
    waiting = !(e && e.data);
    draw(e && e.data ? e : null, t - st.t0 + lead(i) / 1e6);
    map(e && e.data, t - st.t0 + lead(i) / 1e6);
    const lv = level();
    $("rsa-step").textContent = `step ${i + 1} of ${meta.steps.length}: ${st.label}` +
      (waiting ? (e && e.error ? ` (could not load: ${e.error})` : " (loading…)") : "");
    $("rsa-clock").textContent = `${fmt(t)} of ${fmt(meta.prime_s)} into the prime · ${stats.active.toLocaleString()} gadgets at work, ` +
      `${stats.ions.toLocaleString()} ions in them · ${rate(speed())}`;
    $("rsa-level").textContent = {machine: "Detail: the machine — a lit tile is a gadget at work",
      patches: "Detail: patches — a grey square is a held block (a d = 11 patch, 241 ions)",
      gadgets: "Detail: gadgets — each gadget at work outlined and named",
      ions: `Detail: ions — every trap and ion in view (${stats.drawn.toLocaleString()})` +
        (stats.loading ? ", fetching a program…" : "")}[lv];
    for (const b of document.querySelectorAll("#rsa-detail button")) b.classList.toggle("on", b.dataset.level === detail);
    $("rsa-time").value = String(t);
    $("rsa-play").textContent = playing ? "Pause" : "Play";
    strip(i);
    if (card) cardTick();
    if (now > hashDue) { hashDue = now + 400; saveHash(); }
    requestAnimationFrame(frame);
  }

  function strip(i) {                                // the prime's steps, the clock on them
    const c = $("rsa-strip"), g = c.getContext("2d"), W_ = c.width, H_ = c.height;
    if (!strip.img) {
      const off = document.createElement("canvas"); off.width = W_; off.height = H_;
      const h = off.getContext("2d");
      for (const p of meta.parts) {
        h.fillStyle = p.color; h.fillRect(p.t0 / meta.prime_s * W_, 0, Math.max(1, p.span / meta.prime_s * W_), H_);
      }
      strip.img = off;
    }
    g.drawImage(strip.img, 0, 0);
    const st = meta.steps[i];
    g.fillStyle = "rgba(0,0,0,.25)";
    g.fillRect(st.t0 / meta.prime_s * W_, 0, Math.max(1, st.span / meta.prime_s * W_), H_);
    g.fillStyle = "#111"; g.fillRect(t / meta.prime_s * W_ - 1, 0, 2, H_);
  }

  // ---- what is under the pointer: the card -----------------------------------------------------

  function tileAt(ev) {
    const c = $("rsa-canvas"), r = c.getBoundingClientRect();
    return [Math.floor((ev.clientX - r.left) * c.width / r.width / view.s + view.x),
            Math.floor((ev.clientY - r.top) * c.height / r.height / view.s + view.y)];
  }

  function where(x, y) {                              // what the floorplan has at a tile
    for (const [r, ts] of Object.entries(layout.registers)) {
      const k = ts.findIndex(([a, b]) => a === x && b === y);
      if (k >= 0) return `the home of ${NAMES[r] || r}[${k}]`;
    }
    const f = layout.factories.find(([, a, b, c, d]) => x >= a && x <= c && y >= b && y <= d);
    if (f) return `inside one of ${NAMES[f[0]]}`;
    if (layout.seats.some(([a, b]) => a === x && b === y)) return "a crossbar seat";
    return "a bus tile";
  }

  function pick(ev) {
    const [x, y] = tileAt(ev), i = stepAt(t), e = steps.get(i);
    if (!e || !e.data) return;
    const us = (t - meta.steps[i].t0) * 1e6 + lead(i), st = tileState(e, index(e.data, area()), x, y, us);
    card = {x, y, step: i, g: st.g, live: st.live};
    if (st.g >= 0 && (ev.ctrlKey || ev.metaKey) && progs[e.data.prog[st.g]].page) { window.open(page(e.data.prog[st.g]), "_blank"); return; }
    showCard();
  }

  const page = (p) => `${DATA}pages/${progs[p].key}.html`;

  function showCard() {
    const el = $("rsa-card"), e = steps.get(card.step);
    if (!e || !e.data) return;
    const d = e.data, esc = (s) => String(s).replace(/[&<>]/g, (ch) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"})[ch]);
    let h;
    if (card.g >= 0) {
      const p = d.prog[card.g], pr = progs[p], tiles = (d.off[card.g + 1] - d.off[card.g]) / 2;
      h = `<h3>${esc(pr.title)}</h3><p id="rsa-card-when"></p>` +
        `<p>${pr.ions ? `${pr.ions.toLocaleString()} ions on ` : ""}${tiles} tile${tiles > 1 ? "s" : ""}` +
        (pr.instructions ? ` · ${pr.instructions.toLocaleString()} instructions` : "") + ` · ${(pr.us / 1000).toFixed(3)} ms</p>` +
        `<p>${esc(pr.check)}</p><p>${pr.uses.toLocaleString()} copies in a prime.</p>` +
        `<div class="acts">${pr.key ? `<button data-act="ions">Watch its ions</button>` : ""}` +
        (pr.page ? `<a href="${page(p)}" target="_blank" rel="noopener">Open its design page ↗</a>` : "") +
        `<button data-act="next">The next one like it</button></div>`;
    } else {
      const pr = idleId >= 0 ? progs[idleId] : null;
      h = `<h3>Tile (${card.x}, ${card.y})</h3><p>${where(card.x, card.y)}.</p>` +
        (card.live ? `<p>It holds a block: a d = 11 patch running its syndrome round (${pr ? (pr.us / 1000).toFixed(3) : "5.822"} ms a round) until a gadget takes it.</p>` +
          `<div class="acts"><button data-act="ions">Watch its ions</button>` +
          (pr && pr.page ? `<a href="${page(idleId)}" target="_blank" rel="noopener">The round's design page ↗</a>` : "") + `</div>`
          : `<p>Nothing is held here now: its 288 traps hold ions at rest.</p><div class="acts"><button data-act="ions">Watch its ions</button></div>`);
    }
    el.innerHTML = `<button class="x" data-act="close" aria-label="close">×</button>` + h;
    el.hidden = false;
    cardTick();
  }

  function cardTick() {
    const w = $("rsa-card-when"), e = steps.get(card.step);
    if (!w || !e || !e.data || card.g < 0) return;
    const d = e.data, p = d.prog[card.g], t0 = meta.steps[card.step].t0 + (d.start[card.g] - lead(card.step)) / 1e6;
    const f = (t - t0) / (durOf[p] / 1e6);
    w.textContent = f < 0 ? `starts ${fmt(t0)} into the prime` : f >= 1 ? `done (it ran from ${fmt(t0)})` :
      `at work: ${(f * 100).toFixed(1)}% done, started ${fmt(t0)} into the prime`;
  }

  function cardAct(act) {
    if (act === "close") { card = null; $("rsa-card").hidden = true; return; }
    const e = steps.get(card.step);
    if (act === "ions") {
      if (card.g >= 0 && e && e.data) look(e.data, card.g);
      else { zoomTo(card.x + 0.5, card.y + 0.5, AUTO_AT.ions * 2.2); if (detail !== "auto" && detail !== "ions") detail = "auto"; }
    } else if (act === "next" && card.g >= 0 && e && e.data) {
      show(e.data.prog[card.g], meta.steps[card.step].t0 + (e.data.start[card.g] - lead(card.step)) / 1e6 + 1e-6);
    }
  }

  function zoomTo(x, y, s) {
    const c = $("rsa-canvas");
    view.s = Math.min(ZMAX, Math.max(ZMIN, s));
    view.x = x - c.width / view.s / 2; view.y = y - c.height / view.s / 2;
  }

  function look(d, i) {                               // a gadget in the middle of the view, close enough to see its ions
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let q = d.off[i]; q < d.off[i + 1]; q += 2) { x0 = Math.min(x0, d.tiles[q]); x1 = Math.max(x1, d.tiles[q]); y0 = Math.min(y0, d.tiles[q + 1]); y1 = Math.max(y1, d.tiles[q + 1]); }
    const c = $("rsa-canvas"), fitS = Math.min(c.width / (x1 - x0 + 1.4), c.height / (y1 - y0 + 1.4));
    let cx = (x0 + x1 + 1) / 2, cy = (y0 + y1 + 1) / 2;
    if (fitS < AUTO_AT.ions * 1.25) {                 // too big to see whole with its ions: its own tile nearest the middle (a bent corridor's box is mostly bus)
      let best = Infinity, bx = cx, by = cy;
      for (let q = d.off[i]; q < d.off[i + 1]; q += 2) {
        const dx = d.tiles[q] + 0.5 - cx, dy = d.tiles[q + 1] + 0.5 - cy;
        if (dx * dx + dy * dy < best) { best = dx * dx + dy * dy; bx = d.tiles[q] + 0.5; by = d.tiles[q + 1] + 0.5; }
      }
      cx = bx; cy = by;
    }
    zoomTo(cx, cy, Math.max(AUTO_AT.ions * 1.25, Math.min(fitS, AUTO_AT.ions * 3)));
    if (detail !== "auto" && detail !== "ions") detail = "auto";
  }

  async function show(p, after) {                     // the next gadget of program p after `after` (s): clock, view, card
    const pr = progs[p];
    if (!pr.steps.length) return;
    let k = pr.steps.find((j) => meta.steps[j].t0 + meta.steps[j].span > after);
    if (k === undefined) k = pr.steps[0];
    for (let tries = 0; tries < pr.steps.length; tries++) {
      const e = load(k);
      await e.promise;
      if (!e.data) return;
      const d = e.data, from = (after - meta.steps[k].t0) * 1e6 + lead(k);
      for (let i = 0; i < d.n; i++) {
        if (d.prog[i] !== p || d.start[i] < from) continue;
        steps.set(k, e);                             // the clock moved meanwhile: this step is the clock's again
        playing = false;
        t = meta.steps[k].t0 + (d.start[i] - lead(k) + Math.min(durOf[p] * 0.02, 400)) / 1e6;
        look(d, i);
        card = {x: d.tiles[d.off[i]], y: d.tiles[d.off[i] + 1], step: k, g: i, live: true};
        showCard();
        $("rsa").scrollIntoView({behavior: "smooth", block: "start"});
        return;
      }
      const at = pr.steps.indexOf(k);
      k = pr.steps[(at + 1) % pr.steps.length]; after = meta.steps[k].t0;
    }
  }

  // ---- the address keeps the place, the moment and the level ---------------------------------

  function saveHash() {
    const h = `#t=${t.toFixed(6)}&x=${view.x.toFixed(2)}&y=${view.y.toFixed(2)}&z=${view.s.toFixed(3)}&d=${detail}`;
    if (location.hash !== h) history.replaceState(null, "", h);
  }

  function readHash() {
    const q = new URLSearchParams(location.hash.slice(1));
    if (!q.has("z")) return false;
    const num = (k, v) => (Number.isFinite(Number(q.get(k))) ? Number(q.get(k)) : v);
    t = Math.min(meta.prime_s, Math.max(0, num("t", 0)));
    view = {s: Math.min(ZMAX, Math.max(ZMIN, num("z", 1))), x: num("x", 0), y: num("y", 0)};
    if (LEVELS.includes(q.get("d")) || q.get("d") === "auto") detail = q.get("d");
    return true;
  }

  // ---- controls ------------------------------------------------------------------------------

  function setLevel(lv) {
    detail = lv;
    if (lv !== "auto" && view.s < LEAST[lv]) {
      const c = $("rsa-canvas");
      zoomTo(view.x + c.width / view.s / 2, view.y + c.height / view.s / 2, AUTO_AT[lv] * 1.3);
    }
  }

  function controls() {
    $("rsa-play").addEventListener("click", () => { if (t >= meta.prime_s) t = 0; playing = !playing; });
    $("rsa-speed").addEventListener("change", (e) => { speedSel = e.target.value; });
    const tr = $("rsa-time");
    tr.max = String(meta.prime_s);
    tr.addEventListener("input", (e) => { t = Number(e.target.value); });
    const part = $("rsa-part");
    for (const p of meta.parts) part.add(new Option(`${p.name} (${p.steps} steps, ${fmt(p.span)})`, String(p.t0)));
    part.addEventListener("change", (e) => { t = Number(e.target.value); });
    $("rsa-strip").addEventListener("click", (e) => {
      const r = e.target.getBoundingClientRect();
      t = (e.clientX - r.left) / r.width * meta.prime_s;
    });
    const c = $("rsa-canvas");
    const zoom = (f, cx, cy) => {
      cx = cx ?? c.width / 2; cy = cy ?? c.height / 2;
      const tx = cx / view.s + view.x, ty = cy / view.s + view.y;
      view.s = Math.min(ZMAX, Math.max(ZMIN, view.s * f));
      view.x = tx - cx / view.s; view.y = ty - cy / view.s;
    };
    $("rsa-in").addEventListener("click", () => zoom(2));
    $("rsa-out").addEventListener("click", () => zoom(0.5));
    $("rsa-fit").addEventListener("click", fit);
    $("rsa-detail").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setLevel(b.dataset.level); });
    $("rsa-card").addEventListener("click", (e) => { const b = e.target.closest("[data-act]"); if (b) cardAct(b.dataset.act); });
    $("rsa-map").addEventListener("click", (e) => {    // the view centred where the map is clicked
      const m = $("rsa-map"), r = m.getBoundingClientRect(), b = layout.bounds;
      const x = ((e.clientX - r.left) * m.width / r.width - map.ox) / map.scale + b[0];
      const y = ((e.clientY - r.top) * m.height / r.height - map.oy) / map.scale + b[1];
      view.x = x - c.width / view.s / 2; view.y = y - c.height / view.s / 2;
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      zoom(Math.exp(-Math.max(-60, Math.min(60, e.deltaY)) * 0.004),
           (e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height);
    }, {passive: false});
    c.addEventListener("dblclick", (e) => {
      const r = c.getBoundingClientRect();
      zoom(e.shiftKey ? 0.5 : 2, (e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height);
    });
    let drag = null;
    c.addEventListener("pointerdown", (e) => { drag = {x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false}; c.setPointerCapture(e.pointerId); });
    c.addEventListener("pointerup", (e) => { if (drag && !drag.moved && e.detail < 2) pick(e); drag = null; });
    c.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const r = c.getBoundingClientRect(), k = c.width / r.width / view.s;
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) drag.moved = true;
      view.x = drag.vx - (e.clientX - drag.x) * k; view.y = drag.vy - (e.clientY - drag.y) * k;
    });
    window.addEventListener("keydown", (e) => {
      if (e.target.closest && e.target.closest("input,select,textarea")) return;
      if (e.key === "+" || e.key === "=") zoom(1.5); else if (e.key === "-") zoom(1 / 1.5);
      else if (e.key === "Escape" && card) cardAct("close");
    });
    document.addEventListener("click", (e) => {        // the programs' table
      const b = e.target.closest("[data-show]");
      if (b) show(Number(b.dataset.show), t);
    });
    const size = () => {
      const r = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
      for (const id of ["rsa-strip", "rsa-map"]) {
        const sr = $(id).getBoundingClientRect();
        $(id).width = Math.round(sr.width * dpr); $(id).height = Math.round(sr.height * dpr);
      }
      strip.img = null; map.img = null;
    };
    window.addEventListener("resize", () => { size(); baseKey = ""; });
    size();
    if (!readHash()) fit();
  }

  async function start() {
    [meta, progs, layout] = await Promise.all(["meta.json", "programs.json", "layout.json"]
      .map((f) => fetch(DATA + f).then((r) => r.json())));
    kindOf = progs.map((p) => p.kind);
    durOf = new Uint32Array(progs.map((p) => p.dur_us));
    ionsOf = progs.map((p) => p.ions);
    maxDur = Math.max(...durOf);
    idleId = progs.findIndex((p) => p.kind === "idle");
    for (const p of progs) p.patchSet = new Set(p.kind === "merge" ? p.shape[2].map(([[a, b]]) => `${a},${b}`) : []);
    [X0, Y0] = layout.bounds; W = layout.bounds[2] - X0 + 1; H = layout.bounds[3] - Y0 + 1;
    Object.assign(NAMES, layout.names || {}); Object.assign(BASE, layout.colors || {});
    controls();
    keep(stepAt(t));
    requestAnimationFrame(frame);
    window.RSA_VIEW = {meta, steps, progs, layout, get t() { return t; }, set t(v) { t = v; }, play: () => { playing = true; },
                       pause: () => { playing = false; }, get view() { return view; }, level, setLevel, show, zoomTo,
                       get stats() { return stats; }, ions};
  }
  start().catch((err) => { $("rsa-step").textContent = `could not start: ${err}`; });
})();
