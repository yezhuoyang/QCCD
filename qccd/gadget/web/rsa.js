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
  const I = window.QCCDIons;           // a program's ions, and the machine drawn as the studio draws it (ions.js)
  const G = I.G;                       // grid units a tile: 2 (d + 1), d = 11
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
      e.promise = Promise.all([I.bytes(`${DATA}frames/${pad(i)}.bin.gz`), I.bytes(`${DATA}live/${pad(i)}.bin.gz`)])
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

  function ions(p) {                                  // a program's ion file, fetched once
    if (!ionFiles.has(p)) {
      const e = {data: null};
      if (!progs[p].key) { e.error = "no ion file"; ionFiles.set(p, e); return e; }
      e.promise = I.bytes(`${DATA}ions/${progs[p].key}.bin.gz`).then((b) => (e.data = I.decode(b)))
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
      const pat = I.tilePattern(s), [ax, ay, bx, by] = area();
      for (let y = Math.max(ay, Y0); y < Math.min(by, Y0 + H); y++)
        for (let x = Math.max(ax, X0); x < Math.min(bx, X0 + W); x++) g.drawImage(pat, px(x), py(y));
    }
    labels(g, lv);
    baseKey = key; baseImg = off;
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
    if (k === "merge") return `${`${pr.shape[1]}̄`.repeat(pr.shape[2].length)} merge` + (pr.shape[3].length ? ` · ${pr.shape[3].length}-tile corridor` : "");
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
    const s = view.s, u = s / G;
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
    // every ion as a disc, batched by colour (ions.js): a free trap's ion at rest in its first ring, then each program's
    const out = I.batch(), mk = I.marks(u);
    const ax = Math.max(r[0], X0), ay = Math.max(r[1], Y0), bx = Math.min(r[2], X0 + W), by = Math.min(r[3], Y0 + H);
    const o = I.OFF[1][0] / G;
    for (let y = ay; y < by; y++) for (let x = ax; x < bx; x++) {
      for (let gy = 0; gy < G; gy++) for (let gx = gy & 1; gx < G; gx += 2) {
        if (taken.has(key(x * G + gx, y * G + gy))) continue;
        const up = gy & 1;
        out.disc[0].push(px(x + (gx + 0.5) / G + (up ? 0 : o)), py(y + (gy + 0.5) / G + (up ? o : 0)), mk.rest);
        stats.drawn++;
      }
    }
    for (const j of jobs) {
      if (!j.P) continue;
      const ox = j.o[0], oy = j.o[1];
      I.job(j.P, j.tau, (gx) => px(ox + (gx + 0.5) / G), (gy) => py(oy + (gy + 0.5) / G), mk, out);
      stats.drawn += j.P.n;
    }
    I.paint(g, out, mk);
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
    const asked = new URLSearchParams(location.hash.slice(1)).get("show");     // `#show=KEY` (a design page's notes link it): its first copy
    if (asked) { const p = progs.findIndex((x) => x.key === asked); if (p >= 0) show(p, 0); }
    keep(stepAt(t));
    requestAnimationFrame(frame);
    window.RSA_VIEW = {meta, steps, progs, layout, get t() { return t; }, set t(v) { t = v; }, play: () => { playing = true; },
                       pause: () => { playing = false; }, get view() { return view; }, level, setLevel, show, zoomTo,
                       get stats() { return stats; }, ions};
  }
  start().catch((err) => { $("rsa-step").textContent = `could not start: ${err}`; });
})();
