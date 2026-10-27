// qccd.gadget page: a folded schedule (GIR 0.3, qccd/gadget/fold.py), played a window at a time.
//
// A folded run keeps each gadget once (a *macro*) and every call of it as a box: its time,
// its blocks, the ions it loads.  Hours of machine time are a few megabytes at the top and a
// file per macro.  The page never expands the whole run: `window(a, b)` expands only the
// calls that overlap [a, b), loading their macros the first time they are needed, into the
// GIR 0.2 the rest of the page draws.  It is the same expansion as `fold.py::expand`, cut to
// a window: ions renumbered through the call (a block's ions are its caller's, a call's own
// ions start where its caller's next id was), blocks named by the caller (`c3.l0` for a
// call's own), every instruction with its call chain (the program line lit is the outermost
// call's; each frame names the line inside its gate), and a memory's hold unrolled into the
// refresh rounds that fall in the window.

(function (root) {
  "use strict";
  const PARAM_GID = 1000000000;

  function lowerBound(arr, key, get) {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (get(arr[mid]) < key) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  // per body, once: its rows sorted by start, and the longest of each kind, so a window
  // finds what overlaps it by binary search
  function indexBody(b) {
    if (b._ix) return b._ix;
    const ev = b.events.slice().sort((x, y) => x[1] - y[1]);
    const ca = b.carries.slice().sort((x, y) => x[4] - y[4]);
    const me = (b.messages || []).slice().sort((x, y) => x[6] - y[6]);
    const calls = (b.calls || []).slice().sort((x, y) => x.t0 - y.t0);
    let evMax = 0, caMax = 0, meMax = 0;
    for (const e of ev) evMax = Math.max(evMax, e[2] - e[1]);
    for (const c of ca) caMax = Math.max(caMax, c[11] - c[4]);
    for (const m of me) meMax = Math.max(meMax, m[7] - m[6]);
    const insById = {};
    for (const i of b.program.instructions) insById[i.id] = i;
    b._ix = { ev, ca, me, calls, evMax, caMax, meMax, insById };
    return b._ix;
  }

  function Folded(g, files, onLoad) {
    this.g = g;
    this.files = files || {};
    this.macros = {};
    this.pending = {};
    this.onLoad = onLoad || (() => {});
    this.nRes = g.n_residents;
    this.makespan = g.makespan_us;
    const self = this;
    root.__gadgetMacro = function (id, data) {
      self.macros[id] = data;
      delete self.pending[id];
      self.onLoad(id);
    };
  }

  // A macro, or null while its file is on its way (asked for the first time here).
  Folded.prototype.macro = function (id) {
    if (this.macros[id]) return this.macros[id];
    if (this.g.macros && this.g.macros[id]) return (this.macros[id] = this.g.macros[id]);
    if (!this.pending[id] && this.files[id] && typeof document !== "undefined") {
      this.pending[id] = true;
      const s = document.createElement("script");
      s.src = this.files[id];
      s.async = true;
      document.head.appendChild(s);
    }
    return null;
  };

  Folded.prototype.spanOf = function (call) {
    const m = this.macro(call.m);
    return m ? m.span_us : (this.g.spans && this.g.spans[call.m]) || 0;
  };

  // The calls running at t, outermost first: [{call, macro, t0, path}].
  Folded.prototype.chainAt = function (t) {
    const out = [];
    let body = this.g, base = 0, path = "";
    for (;;) {
      const ix = indexBody(body);
      let hit = null;
      for (let i = lowerBound(ix.calls, t - base + 1e-9, (c) => c.t0) - 1; i >= 0; i--) {
        const c = ix.calls[i];
        if (c.t0 + base + this.spanOf(c) > t) { hit = c; break; }
        if (base + c.t0 + this.maxSpan(body) < t) break;
      }
      if (!hit) return out;
      const m = this.macro(hit.m);
      out.push({ call: hit, macro: m, t0: base + hit.t0, path: path + hit.id });
      if (!m) return out;
      body = m; base += hit.t0; path += hit.id + "/";
    }
  };

  Folded.prototype.maxSpan = function (body) {
    if (body._maxSpan !== undefined) return body._maxSpan;
    let s = 0;
    for (const c of body.calls || []) s = Math.max(s, this.spanOf(c));
    return (body._maxSpan = s);
  };

  // GIR 0.2 for [a, b): every row that overlaps it, at program time, on program ions.
  Folded.prototype.window = function (a, b) {
    const g = this.g;
    const W = { events: [], carries: [], messages: [], windows: [], frames: [], outcomes: [],
                ins: [], insKey: {}, blocks: {}, missing: 0, calls: 0 };
    const ident = (x) => x;
    this.body(g, 0, ident, ident, ident, "", [], a, b, W, null);
    const out = {};
    for (const k in g) if (!(k in { macros: 1, calls: 1, holds: 1 })) out[k] = g[k];
    out.schema = "qccd.gir/0.2";
    // instructions, renumbered for the window (the page indexes them by position)
    out.program = Object.assign({}, g.program, { instructions: W.ins });
    for (const e of W.events) e[6] = W.insKey[e[6]];
    for (const c of W.carries) c[9] = W.insKey[c[9]];
    for (const m of W.messages) m[10] = W.insKey[m[10]];
    for (const w of W.windows) { w.instruction = W.insKey[w.instruction]; if (w.decoded_by != null) w.decoded_by = W.insKey[w.decoded_by]; }
    for (const f of W.frames) f.instruction = W.insKey[f.instruction];
    for (const o of W.outcomes) o.instruction = W.insKey[o.instruction];
    W.events.sort((x, y) => x[1] - y[1] || x[2] - y[2]);
    W.events.forEach((e, i) => { e[0] = i; e[7] = "i" + e[6]; });
    W.carries.sort((x, y) => x[4] - y[4]);
    W.carries.forEach((c, i) => { c[0] = i; c[10] = "i" + c[9]; });
    W.messages.sort((x, y) => x[6] - y[6]);
    W.messages.forEach((m, i) => { m[0] = i; m[11] = "i" + m[10]; m[13] = -1; });
    W.frames.sort((x, y) => x.t - y.t);
    out.events = W.events; out.carries = W.carries; out.messages = W.messages;
    out.windows = W.windows; out.frames = W.frames; out.outcomes = W.outcomes;
    out.blocks = Object.assign({}, g.blocks, W.blocks);
    out.refused = g.refused || [];
    out.window = { a, b, missing: W.missing, calls: W.calls };
    return out;
  };

  // one body's rows in [a, b), and the calls it makes that overlap it.  `mid`: the gadget
  // (macro) the body is, null for the program; every row keeps `hw = [mid, its own id]`, the
  // row the TSIR modules write it as (modules.py), so the Stack can cite file:line.
  Folded.prototype.body = function (body, t0, gm, names, cv, path, chain, a, b, W, mid) {
    const ix = indexBody(body);
    const need = {};
    const key = (id) => path + "#" + id;
    const want = (id) => { need[id] = true; return key(id); };
    for (let i = lowerBound(ix.ev, a - t0 - ix.evMax, (e) => e[1]); i < ix.ev.length && ix.ev[i][1] + t0 < b; i++) {
      const e = ix.ev[i];
      if (e[2] + t0 <= a) continue;
      const ne = e.slice();
      ne[1] = e[1] + t0; ne[2] = e[2] + t0;
      ne[6] = want(e[6]);
      ne[8] = (e[8] || []).map((pr) => [pr[0], gm(pr[1])]);
      if (ne.length > 10 && ne[10] && ne[10].ready_us !== undefined) ne[10] = Object.assign({}, ne[10], { ready_us: ne[10].ready_us + t0 });
      ne.hw = [mid, "run", e[0]];
      W.events.push(ne);
    }
    for (let i = lowerBound(ix.ca, a - t0 - ix.caMax, (c) => c[4]); i < ix.ca.length && ix.ca[i][4] + t0 < b; i++) {
      const c = ix.ca[i];
      if (c[11] + t0 <= a) continue;
      const nc = c.slice();
      nc[4] = c[4] + t0; nc[11] = c[11] + t0;
      nc[7] = c[7].map(gm);
      nc[9] = want(c[9]);
      W.carries.push(nc);
    }
    for (let i = lowerBound(ix.me, a - t0 - ix.meMax, (m) => m[6]); i < ix.me.length && ix.me[i][6] + t0 < b; i++) {
      const m = ix.me[i];
      if (m[7] + t0 <= a) continue;
      const nm = m.slice();
      nm[6] = m[6] + t0; nm[7] = m[7] + t0;
      nm[10] = want(m[10]);
      W.messages.push(nm);
    }
    for (const w of body.windows || []) {
      const t = w.measured_us + t0;
      if (t < a || t >= b) continue;
      // (an event a window, frame or outcome names is this body's; the window renumbers
      // events, so it keeps none of those links)
      const nw = Object.assign({}, w, { blocks: w.blocks.map(names), instruction: want(w.instruction),
                                        measured_us: t, event: null, decode_event: null });
      for (const k of ["sent_us", "decoded_us", "written_us"]) if (nw[k] != null) nw[k] += t0;
      if (nw.decoded_by != null) nw.decoded_by = want(nw.decoded_by);
      W.windows.push(nw);
    }
    for (const f of body.frames || []) {
      const t = f.t + t0;
      if (t < a || t >= b) continue;
      W.frames.push(Object.assign({}, f, { t, block: f.block ? names(f.block) : f.block,
                                           instruction: want(f.instruction), event: null }));
    }
    for (const o of body.outcomes || []) {
      const t = (o.at || 0) + t0;
      if (t < a || t >= b) continue;
      W.outcomes.push(Object.assign({}, o, { at: t, blocks: o.blocks.map(names),
                                             cvar: o.cvar ? cv(o.cvar) : o.cvar,
                                             recorded_us: o.recorded_us != null ? o.recorded_us + t0 : null,
                                             instruction: want(o.instruction), event: null, recorded: null }));
    }
    // a hold: the refresh rounds that fall in the window
    (body.holds || []).forEach((h, hk) => {
      const first = h.from + t0 + h.every - h.round_us;
      const k0 = Math.max(1, Math.floor((a - first) / h.every) + 1);
      for (let k = k0; k <= h.n; k++) {
        const s = h.from + t0 + k * h.every - h.round_us;
        if (s >= b) break;
        if (s + h.round_us <= a) continue;
        const ev = [0, s, s + h.round_us, h.place, "refresh", 1, want(h.ins), "", h.bind.map((pr) => [pr[0], gm(pr[1])]), []];
        ev.hw = [mid, "hold", hk, k];
        W.events.push(ev);
      }
    });
    for (const b_ in body.blocks || {}) {
      const info = body.blocks[b_];
      if (b_.startsWith("arg") && path) continue;
      const nmb = names(b_);
      if (!W.blocks[nmb]) W.blocks[nmb] = Object.assign({}, info, { ions: info.ions.map(gm), born: (info.born || 0) + t0 });
    }
    // the instructions the window touches, as the flat parser would write them
    for (const id in need) {
      const ins = ix.insById[id];
      if (!ins) continue;
      const own = new Set(ins.blocks);
      const outs = new Set(((ins.cond || {}).cvars || []).concat(ins.cvar ? [ins.cvar] : []));
      const ren = (s) => s.replace(/\b([A-Za-z_][\w.]*)\b/g, (m0, w) => own.has(w) ? names(w) : outs.has(w) ? cv(w) : w);
      const ni = Object.assign({}, ins, { blocks: ins.blocks.map(names) });
      ni.text = ren(ins.text);
      if (ins.cvar) ni.cvar = cv(ins.cvar);
      if (ins.guard) ni.guard = ren(ins.guard);
      if (ins.cond) {
        ni.cond = Object.assign({}, ins.cond, { cvars: ins.cond.cvars.map(cv), text: ren(ins.cond.text || "") });
        if (ins.cond.all) ni.cond.all = ins.cond.all.map((p) => Object.assign({}, p, { cvars: p.cvars.map(cv) }));
      }
      if (chain.length) {
        ni.fold = path.replace(/\/$/, "");
        ni.line = chain[0].line;
        const bodies = chain.slice(1).map((f) => f.line).concat([ins.src || ins.line]);
        ni.call = chain.map((f, j) => Object.assign({}, f, { body: bodies[j] }));
        ni.src = bodies[bodies.length - 1];
      }
      ni.global = key(id);
      W.insKey[key(id)] = W.ins.length;
      ni.id = W.ins.length;
      W.ins.push(ni);
    }
    // the calls that overlap the window
    const calls = ix.calls;
    const maxSpan = this.maxSpan(body);
    for (let i = lowerBound(calls, a - t0 - maxSpan, (c) => c.t0); i < calls.length && calls[i].t0 + t0 < b; i++) {
      const c = calls[i];
      const span = this.spanOf(c);
      if (c.t0 + t0 + span <= a) continue;
      const m = this.macro(c.m);
      if (!m) { W.missing++; continue; }
      W.calls++;
      const cid = path + c.id;
      const args = c.args.map(names);
      const nm = (n) => (/^arg\d+$/.test(n) ? args[+n.slice(3)] : cid + "." + n);
      // the call's outer guard reads its caller's outcomes (`x0`, ... inside)
      const ext = {};
      (c.ext_names || []).forEach((n, k) => { ext["x" + k] = cv(n); });
      const ncv = (n) => ext[n] || cid + "." + n;
      const argGids = {};
      c.args.forEach((x, i2) => {
        const info = (body.blocks || {})[x];
        if (info) info.ions.forEach((gid, j) => { argGids[PARAM_GID + 9 * i2 + j] = gm(gid); });
      });
      const base = c.gid >= this.nRes ? gm(c.gid) : c.gid;
      const nRes = this.nRes;
      const cgm = (gid) => (gid >= PARAM_GID ? argGids[gid] : gid < nRes ? gid : base + gid - nRes);
      const ins = ix.insById[c.ins] || {};
      const frame = { gate: c.gate, call: cid, line: ins.src || ins.line || c.line, guard: c.guard || null };
      this.body(m, t0 + c.t0, cgm, nm, ncv, cid + "/", chain.concat([frame]), a, b, W, c.m);
    }
  };

  root.GadgetFold = { Folded, PARAM_GID };
})(typeof window !== "undefined" ? window : globalThis);
