/* RSA-2048 at d = 11: one prime of Gidney 2025 played on the machine's floorplan, step by step.
 *
 * The prime is 358 steps run end to end on one clock.  A step's gadgets (one file each, gzipped:
 * per gadget its start in µs, its program, its tiles) are fetched when the clock nears it and
 * dropped once it is behind, so no more than four steps are ever held -- the whole prime (282
 * million gadgets) never is.  The clock waits for a step that has not arrived instead of
 * skipping it, so what is drawn is always the schedule itself.
 *
 * Data, beside the page: data/meta.json (the steps, their parts and times), data/programs.json
 * (every distinct program and its check at d = 11), data/layout.json (the floorplan) and
 * data/frames/NNN.bin.gz.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const DATA = "data/";
  const KEEP = 4;                      // steps decoded at most: the one before, this one, two ahead
  const AHEAD = 2;
  const TRAIL = 0.05;                  // s of the reader's time a gadget stays drawn, at least
  const COLOR = {merge: "#2e6fd8", prep: "#12a150", read: "#e08a00", ht: "#8b3fd0"};
  const BASE = {factory: "#e7e9ee", site: "#c8ccd6", seat: "#9aa3b5", bus: "#fbfbfd",
                dlog: "#7cc4a4", help: "#b39ddb", acc: "#f4a7a7", res: "#f2cf86",
                pp: "#8fb3e8", addr: "#5b7fc7", exp: "#d6dbe4"};
  let meta, progs, layout, kindOf, durOf, maxDur;
  const steps = new Map();            // step -> {promise, data}
  let t = 0;                          // the clock, s from the prime's start
  let playing = false, speed = 10, last = null, waiting = false;
  let view = {s: 1, x: 0, y: 0};      // px per tile, the tile at the canvas's left/top
  let baseImg = null, baseKey = "", picked = null;

  function pad(i) { return String(i).padStart(3, "0"); }

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
    return {n, start, prog, off, tiles};
  }

  function load(i) {
    if (i < 0 || i >= meta.steps.length) return null;
    if (!steps.has(i)) {
      const e = {data: null};
      e.promise = bytes(`${DATA}frames/${pad(i)}.bin.gz`).then((b) => (e.data = decode(b, i)))
        .catch((err) => { e.error = String(err); steps.delete(i); });
      steps.set(i, e);
    }
    return steps.get(i);
  }

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

  // ---- drawing -----------------------------------------------------------------------------

  function fit() {                                   // the whole machine's height, at its busiest part
    const c = $("rsa-canvas"), b = layout.bounds;
    view.s = c.height / (b[3] - b[1] + 3);
    const pp = layout.registers.pp, mid = pp[pp.length >> 1][0];
    view.x = mid - c.width / view.s / 2; view.y = b[1] - 1;
  }

  function map(data, local) {                        // the whole machine, small, and where the view is
    const c = $("rsa-map"), g = c.getContext("2d"), b = layout.bounds;
    const s = c.width / (b[2] - b[0] + 1), oy = (c.height - (b[3] - b[1] + 1) * s) / 2;
    if (!map.img || map.w !== c.width) {
      const off = document.createElement("canvas"); off.width = c.width; off.height = c.height;
      const h = off.getContext("2d");
      h.fillStyle = BASE.bus; h.fillRect(0, 0, off.width, off.height);
      h.fillStyle = BASE.factory;
      for (const [, x0, y0, x1, y1] of layout.factories) h.fillRect((x0 - b[0]) * s, oy + (y0 - b[1]) * s, (x1 - x0 + 1) * s, (y1 - y0 + 1) * s);
      for (const [r, tiles] of Object.entries(layout.registers)) {
        h.fillStyle = BASE[r] || "#999";
        for (const [x, y] of tiles) h.fillRect((x - b[0]) * s, oy + (y - b[1]) * s, Math.max(1, s), Math.max(1, s));
      }
      map.img = off; map.w = c.width;
    }
    g.drawImage(map.img, 0, 0);
    if (data) {
      const us = local * 1e6, trail = TRAIL * speed * 1e6, from = us - Math.max(maxDur, trail);
      let lo = 0, hi = data.n;
      while (lo < hi) { const m = (lo + hi) >> 1; if (data.start[m] < from) lo = m + 1; else hi = m; }
      for (let i = lo; i < data.n && data.start[i] <= us; i++) {
        const p = data.prog[i];
        if (data.start[i] + Math.max(durOf[p], trail) <= us) continue;
        g.fillStyle = COLOR[kindOf[p]] || "#000";
        const q = data.off[i];
        g.fillRect((data.tiles[q] - b[0]) * s, oy + (data.tiles[q + 1] - b[1]) * s, Math.max(1.5, s), Math.max(1.5, s));
      }
    }
    const v = $("rsa-canvas");
    g.strokeStyle = "#111"; g.lineWidth = 1.5;
    g.strokeRect((view.x - b[0]) * s, oy + (view.y - b[1]) * s, v.width / view.s * s, v.height / view.s * s);
    map.scale = s; map.oy = oy;
  }

  function base() {
    const c = $("rsa-canvas");
    const key = `${c.width}x${c.height}:${view.s}:${view.x}:${view.y}`;
    if (key === baseKey) return baseImg;
    const off = document.createElement("canvas");
    off.width = c.width; off.height = c.height;
    const g = off.getContext("2d");
    g.fillStyle = BASE.bus; g.fillRect(0, 0, off.width, off.height);
    const px = (x) => (x - view.x) * view.s, py = (y) => (y - view.y) * view.s, w = Math.max(1, view.s);
    g.fillStyle = BASE.factory;
    for (const [, x0, y0, x1, y1] of layout.factories) g.fillRect(px(x0), py(y0), (x1 - x0 + 1) * view.s, (y1 - y0 + 1) * view.s);
    g.fillStyle = BASE.site;
    for (const [x, y] of layout.sites) g.fillRect(px(x), py(y), w, w);
    for (const [r, tiles] of Object.entries(layout.registers)) {
      g.fillStyle = BASE[r] || "#999";
      for (const [x, y] of tiles) g.fillRect(px(x), py(y), w, w);
    }
    g.fillStyle = BASE.seat;
    for (const [x, y] of layout.seats) g.fillRect(px(x), py(y), w, w);
    baseKey = key; baseImg = off;
    return off;
  }

  function draw(data, local) {
    const c = $("rsa-canvas"), g = c.getContext("2d");
    g.drawImage(base(), 0, 0);
    let active = 0;
    const w = Math.max(1.5, view.s);
    if (data) {
      const us = local * 1e6, trail = TRAIL * speed * 1e6, from = us - Math.max(maxDur, trail);
      let lo = 0, hi = data.n;                       // the first gadget that may still be drawn
      while (lo < hi) { const m = (lo + hi) >> 1; if (data.start[m] < from) lo = m + 1; else hi = m; }
      for (let i = lo; i < data.n && data.start[i] <= us; i++) {
        const p = data.prog[i];
        if (data.start[i] + Math.max(durOf[p], trail) <= us) continue;
        if (data.start[i] + durOf[p] > us) active++;
        g.fillStyle = COLOR[kindOf[p]] || "#000";
        for (let q = data.off[i]; q < data.off[i + 1]; q += 2)
          g.fillRect((data.tiles[q] - view.x) * view.s, (data.tiles[q + 1] - view.y) * view.s, w, w);
      }
    }
    return active;
  }

  function fmt(s) {
    if (s < 60) return `${s.toFixed(2)} s`;
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? `${h} h ` : "") + `${m} min ${r.toFixed(1)} s`;
  }

  function frame(now) {
    if (playing && last !== null && !waiting) t = Math.min(meta.prime_s, t + (now - last) / 1000 * speed);
    last = now;
    if (t >= meta.prime_s) playing = false;
    const i = stepAt(t), st = meta.steps[i];
    keep(i);
    const e = steps.get(i);
    waiting = !(e && e.data);
    const active = draw(e && e.data, t - st.t0);
    map(e && e.data, t - st.t0);
    $("rsa-step").textContent = `step ${i + 1} of ${meta.steps.length}: ${st.label}` +
      (waiting ? (e && e.error ? ` (could not load: ${e.error})` : " (loading…)") : "");
    $("rsa-clock").textContent = `${fmt(t)} of ${fmt(meta.prime_s)} into the prime · ${active} gadgets at work`;
    $("rsa-time").value = String(t);
    $("rsa-play").textContent = playing ? "Pause" : "Play";
    strip(i);
    requestAnimationFrame(frame);
  }

  function strip(i) {                                // the prime's steps, the clock on them
    const c = $("rsa-strip"), g = c.getContext("2d"), W = c.width, H = c.height;
    if (!strip.img) {
      const off = document.createElement("canvas"); off.width = W; off.height = H;
      const h = off.getContext("2d");
      for (const p of meta.parts) {
        h.fillStyle = p.color; h.fillRect(p.t0 / meta.prime_s * W, 0, Math.max(1, p.span / meta.prime_s * W), H);
      }
      strip.img = off;
    }
    g.drawImage(strip.img, 0, 0);
    const st = meta.steps[i];
    g.fillStyle = "rgba(0,0,0,.25)";
    g.fillRect(st.t0 / meta.prime_s * W, 0, Math.max(1, st.span / meta.prime_s * W), H);
    g.fillStyle = "#111"; g.fillRect(t / meta.prime_s * W - 1, 0, 2, H);
  }

  // ---- what is under the pointer ---------------------------------------------------------------

  function pick(ev) {
    const c = $("rsa-canvas"), r = c.getBoundingClientRect();
    const x = Math.floor((ev.clientX - r.left) * c.width / r.width / view.s + view.x);
    const y = Math.floor((ev.clientY - r.top) * c.height / r.height / view.s + view.y);
    const i = stepAt(t), e = steps.get(i);
    let found = null;
    if (e && e.data) {
      const d = e.data, us = (t - meta.steps[i].t0) * 1e6;
      for (let k = 0; k < d.n && d.start[k] <= us; k++) {
        if (d.start[k] + durOf[d.prog[k]] <= us) continue;
        for (let q = d.off[k]; q < d.off[k + 1]; q += 2) if (d.tiles[q] === x && d.tiles[q + 1] === y) found = d.prog[k];
      }
    }
    const reg = Object.entries(layout.registers).find(([, ts]) => ts.some(([a, b]) => a === x && b === y));
    const p = found === null ? null : progs[found];
    $("rsa-pick").innerHTML = `tile (${x}, ${y})` + (reg ? ` · ${reg[0]}[${reg[1].findIndex(([a, b]) => a === x && b === y)}]` : "") +
      (p ? ` · now: ${p.text}` : " · nothing at work on it now");
  }

  // ---- controls ------------------------------------------------------------------------------

  function controls() {
    $("rsa-play").addEventListener("click", () => { if (t >= meta.prime_s) t = 0; playing = !playing; });
    $("rsa-speed").addEventListener("change", (e) => { speed = Number(e.target.value); });
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
    const zoom = (f, cx, cy) => {
      const c = $("rsa-canvas");
      cx = cx ?? c.width / 2; cy = cy ?? c.height / 2;
      const tx = cx / view.s + view.x, ty = cy / view.s + view.y;
      view.s = Math.min(24, Math.max(0.2, view.s * f));
      view.x = tx - cx / view.s; view.y = ty - cy / view.s;
    };
    $("rsa-in").addEventListener("click", () => zoom(2));
    $("rsa-out").addEventListener("click", () => zoom(0.5));
    $("rsa-fit").addEventListener("click", fit);
    $("rsa-map").addEventListener("click", (e) => {    // the view centred where the map is clicked
      const m = $("rsa-map"), r = m.getBoundingClientRect(), b = layout.bounds;
      const x = (e.clientX - r.left) * m.width / r.width / map.scale + b[0];
      const y = ((e.clientY - r.top) * m.height / r.height - map.oy) / map.scale + b[1];
      view.x = x - c.width / view.s / 2; view.y = y - c.height / view.s / 2;
    });
    const c = $("rsa-canvas");
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      zoom(e.deltaY < 0 ? 1.25 : 0.8, (e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height);
    }, {passive: false});
    let drag = null;
    c.addEventListener("pointerdown", (e) => { drag = {x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false}; });
    window.addEventListener("pointerup", (e) => { if (drag && !drag.moved) pick(e); drag = null; });
    window.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const r = c.getBoundingClientRect(), k = c.width / r.width / view.s;
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) drag.moved = true;
      view.x = drag.vx - (e.clientX - drag.x) * k; view.y = drag.vy - (e.clientY - drag.y) * k;
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
    size(); fit();
  }

  async function start() {
    [meta, progs, layout] = await Promise.all(["meta.json", "programs.json", "layout.json"]
      .map((f) => fetch(DATA + f).then((r) => r.json())));
    kindOf = progs.map((p) => p.kind);
    durOf = new Uint32Array(progs.map((p) => p.dur_us));
    maxDur = Math.max(...durOf);
    controls();
    keep(0);
    requestAnimationFrame(frame);
    window.RSA_VIEW = {meta, steps, get t() { return t; }, set t(v) { t = v; }, play: () => { playing = true; }};
  }
  start().catch((err) => { $("rsa-step").textContent = `could not start: ${err}`; });
})();
