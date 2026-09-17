// qccd.gadget page core: the model behind every view, with no DOM in it.
//
// Everything the canvas, the timeline and the panels show is a query on this object at
// one global time t (microseconds).  It indexes the GIR once -- events per leaf, carries
// per channel, instruction spans -- and answers by binary search, so the cost of a frame
// grows with what is on screen, not with the 10^4 ions in the machine.
//
// Two pieces of arithmetic carry the hierarchy's meaning and are worth reading:
//   localTime(ev, t)  maps the global clock into a leaf op's own clock: modulo the op's
//                     duration inside `cycle x N`, and through the op's warp where a
//                     handshake stretched it (GADGETS.md section 7);
//   carryIons(c, t)   places each ion of a carry: in its source until it departs, moving
//                     along the channel for one transit, then queued at the far end until
//                     the sink takes it.

(function (root) {
  "use strict";

  function lowerBound(arr, key, get) {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (get(arr[mid]) < key) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function naturalKey(name) {
    const m = /^(.*?)(\d+)$/.exec(name);
    return m ? [m[1], +m[2]] : [name, -1];
  }

  function naturalCmp(a, b) {
    const ka = naturalKey(a), kb = naturalKey(b);
    return ka[0] < kb[0] ? -1 : ka[0] > kb[0] ? 1 : ka[1] - kb[1];
  }

  // ------------------------------------------------------------------ the model

  function Model(data) {
    this.lib = data.library;
    this.gir = data.gir;
    this.checks = data.checks || {};
    this.masters = this.lib.masters;
    this.top = this.lib.top;
    this.nodes = {};           // path -> hierarchy node; "" is the top
    this._buildTree();
    this._indexEvents();
    this._indexCarries();
    this._indexProgram();
    this.leafData = {};        // master -> loaded leaf data
    this._subtreeEvents = {};
  }

  Model.prototype._buildTree = function () {
    const self = this;
    function add(path, name, masterName, parent, x, y) {
      const m = self.masters[masterName];
      const node = { path, name, master: masterName, parent, children: [],
                     x, y, leaf: m.kind === "leaf", family: m.family };
      self.nodes[path] = node;
      if (m.kind === "composite") {
        for (const inst of m.instances) {
          const p = path ? path + "." + inst.name : inst.name;
          node.children.push(p);
          add(p, inst.name, inst.master, path, x + inst.x, y + inst.y);
        }
      }
      return node;
    }
    add("", this.top, this.top, null, 0, 0);
  };

  Model.prototype.node = function (path) { return this.nodes[path]; };
  Model.prototype.master = function (path) { return this.masters[this.nodes[path].master]; };

  Model.prototype.leavesUnder = function (path) {
    const out = [];
    const stack = [path];
    while (stack.length) {
      const n = this.nodes[stack.pop()];
      if (n.leaf) out.push(n.path); else for (const c of n.children) stack.push(c);
    }
    return out;
  };

  Model.prototype.ancestors = function (path) {
    const out = [];
    let n = this.nodes[path];
    while (n) { out.unshift(n.path); n = n.parent === null ? null : this.nodes[n.parent]; }
    return out;
  };

  // ------------------------------------------------------------------ events

  // event row: [id, t0, t1, leaf, op, repeat, instruction, group, bind, warp]
  Model.prototype._indexEvents = function () {
    this.byLeaf = {};
    for (const e of this.gir.events) {
      (this.byLeaf[e[3]] || (this.byLeaf[e[3]] = [])).push(e);
    }
    for (const k in this.byLeaf) this.byLeaf[k].sort((a, b) => a[1] - b[1] || a[2] - b[2]);
    this.makespan = this.gir.makespan_us || 0;
  };

  Model.prototype.eventsOf = function (leaf) { return this.byLeaf[leaf] || []; };

  // The event running on `leaf` at t (the last one started at or before t that has not
  // ended); zero-length frame updates are never "running".
  Model.prototype.eventAt = function (leaf, t) {
    const evs = this.byLeaf[leaf];
    if (!evs) return null;
    // events on one leaf never overlap (G5), so only the last one started at or before t
    // can be running; zero-length frame updates in front of it are skipped
    for (let i = lowerBound(evs, t + 1e-9, (e) => e[1]) - 1; i >= 0; i--) {
      const e = evs[i];
      if (e[2] === e[1]) continue;
      return e[2] > t ? e : null;
    }
    return null;
  };

  Model.prototype.subtreeEvents = function (path) {
    if (this._subtreeEvents[path]) return this._subtreeEvents[path];
    let out = [];
    for (const leaf of this.leavesUnder(path)) out = out.concat(this.eventsOf(leaf));
    out.sort((a, b) => a[1] - b[1] || a[2] - b[2]);
    return (this._subtreeEvents[path] = out);
  };

  Model.prototype.opOf = function (e) {
    const m = this.masters[this.gir.leaves[e[3]][0]];
    return m.ops[e[4]] || null;
  };

  // Global t -> the op's own clock.  Returns {local, iteration, duration}.
  Model.prototype.localTime = function (e, t) {
    const op = this.opOf(e);
    const dur = op ? op.duration_us : Math.max(1, e[2] - e[1]);
    let local = t - e[1];
    let iteration = 0;
    if (e[5] > 1) {
      iteration = Math.min(e[5] - 1, Math.floor(local / dur));
      local = local - iteration * dur;
    }
    const warp = e[9];
    if (warp && warp.length > 1) {
      // warp points are [local, global]; invert the piecewise-linear map
      let j = lowerBound(warp, t, (w) => w[1]);
      if (j <= 0) local = 0;
      else if (j >= warp.length) local = warp[warp.length - 1][0];
      else {
        const a = warp[j - 1], b = warp[j];
        const span = b[1] - a[1];
        local = span > 0 ? a[0] + (b[0] - a[0]) * (t - a[1]) / span : b[0];
      }
    }
    return { local: Math.max(0, Math.min(dur, local)), iteration, duration: dur };
  };

  // ------------------------------------------------------------------ carries

  // carry row: [id, net, fromLeaf, fromPort, t0, deps, transit, ions, role, instruction,
  //             group, lastTaken, takes]
  Model.prototype._indexCarries = function () {
    this.byNet = {};
    for (const c of this.gir.carries) (this.byNet[c[1]] || (this.byNet[c[1]] = [])).push(c);
    for (const k in this.byNet) this.byNet[k].sort((a, b) => a[4] - b[4]);
    this.maxCarrySpan = 0;
    for (const c of this.gir.carries) this.maxCarrySpan = Math.max(this.maxCarrySpan, c[11] - c[4]);
    // per leaf: every departure and arrival, for inventory at t
    this.flows = {};
    const nets = this.gir.nets;
    for (const c of this.gir.carries) {
      const net = nets[c[1]];
      const dst = c[2] === net[0] ? net[2] : net[0];
      const out = this.flows[c[2]] || (this.flows[c[2]] = []);
      const inn = this.flows[dst] || (this.flows[dst] = []);
      for (let j = 0; j < c[5].length; j++) {
        out.push([c[4] + c[5][j], -1]);
        inn.push([c[4] + c[12][j], +1]);
      }
    }
    for (const k in this.flows) {
      const f = this.flows[k].sort((a, b) => a[0] - b[0]);
      let level = 0;
      for (const x of f) { level += x[1]; x.push(level); }
    }
  };

  Model.prototype.carriesOn = function (net, t) {
    const cs = this.byNet[net];
    if (!cs) return [];
    const out = [];
    let i = lowerBound(cs, t - this.maxCarrySpan - 1, (c) => c[4]);
    for (; i < cs.length && cs[i][4] <= t; i++) if (cs[i][11] >= t) out.push(cs[i]);
    return out;
  };

  // Each ion of a carry at t: {gid, f} with f in [0, 1] along the channel from the source
  // end, or nothing while the ion is still in its source or already taken in.
  Model.prototype.carryIons = function (c, t, length) {
    const out = [];
    const L = Math.max(1, length || 1);
    const waiting = [];
    for (let j = 0; j < c[5].length; j++) {
      const dep = c[4] + c[5][j], take = c[4] + c[12][j];
      if (t < dep || t >= take) continue;
      const moving = c[6] > 0 ? (t - dep) / c[6] : 1;
      if (moving < 1) out.push({ gid: c[7][j], f: Math.max(0, moving), j });
      else waiting.push({ gid: c[7][j], j });
    }
    // queued ions stand one site apart at the far end, first taken nearest the end
    waiting.sort((a, b) => a.j - b.j);
    waiting.forEach((w, q) => out.push({ gid: w.gid, f: Math.max(0, 1 - q / L), j: w.j, queued: true }));
    return out;
  };

  Model.prototype.netsIn = function (compositePath) {
    const m = this.master(compositePath);
    return (m.channels || []).map((ch) => ({ path: compositePath + "/" + ch.name, channel: ch }));
  };

  Model.prototype.inventory = function (leaf, t) {
    // a depot starts holding every ion it will ever load (`stock`); a place, its residents
    const home = this.gir.ion_order[this.gir.leaves[leaf][0]].length +
      ((this.gir.stock && this.gir.stock[leaf]) || 0);
    const f = this.flows[leaf];
    if (!f || !f.length) return home;
    const i = lowerBound(f, t + 1e-9, (x) => x[0]) - 1;
    return home + (i >= 0 ? f[i][2] : 0);
  };

  Model.prototype.inventoryUnder = function (path, t) {
    let n = 0;
    for (const leaf of this.leavesUnder(path)) n += this.inventory(leaf, t);
    return n;
  };

  Model.prototype.capacityUnder = function (path) {
    const m = this.master(path);
    return m.capacity || 0;
  };

  // ------------------------------------------------------------------ program

  Model.prototype._indexProgram = function () {
    const n = this.gir.program.instructions.length;
    this.insSpan = new Array(n).fill(null);
    this.insLeaves = new Array(n).fill(null).map(() => new Set());
    for (const e of this.gir.events) {
      const i = e[6];
      if (i < 0) continue;
      const s = this.insSpan[i];
      this.insSpan[i] = s ? [Math.min(s[0], e[1]), Math.max(s[1], e[2])] : [e[1], e[2]];
      this.insLeaves[i].add(e[3]);
    }
    for (const c of this.gir.carries) {
      const i = c[9];
      if (i < 0) continue;
      const s = this.insSpan[i];
      this.insSpan[i] = s ? [Math.min(s[0], c[4]), Math.max(s[1], c[11])] : [c[4], c[11]];
    }
    this.refused = {};
    for (const r of this.gir.refused) this.refused[r[0]] = r[1];
    this.blockOfLeaf = {};
    for (const b in this.gir.blocks) this.blockOfLeaf[this.gir.blocks[b].leaf] = b;
  };

  Model.prototype.activeInstructions = function (t) {
    const out = [];
    this.insSpan.forEach((s, i) => { if (s && s[0] <= t && t < s[1]) out.push(i); });
    return out;
  };

  // ------------------------------------------------------------------ ions

  Model.prototype.ionHome = function (gid) {
    if (!this._homes) {
      this._homes = [];
      for (const p in this.gir.leaves) {
        const v = this.gir.leaves[p];
        this._homes.push([v[3], p, this.gir.ion_order[v[0]]]);
      }
      this._homes.sort((a, b) => a[0] - b[0]);
    }
    const i = lowerBound(this._homes, gid + 1, (h) => h[0]) - 1;
    if (i < 0) return null;
    const h = this._homes[i];
    const k = gid - h[0];
    if (k >= h[2].length) return null;
    return { leaf: h[1], local: h[2][k] };
  };

  Model.prototype.ionTrace = function (gid) {
    const out = [];
    for (const c of this.gir.carries) {
      const j = c[7].indexOf(gid);
      if (j >= 0) out.push({ kind: "carry", t0: c[4] + c[5][j], t1: c[4] + c[12][j], net: c[1],
                             from: c[2], ins: c[9] });
    }
    for (const e of this.gir.events) {
      if (!e[8] || !e[8].length) continue;
      for (const pr of e[8]) if (pr[1] === gid) { out.push({ kind: "op", t0: e[1], t1: e[2], leaf: e[3], op: e[4], ins: e[6], local: pr[0] }); break; }
    }
    const home = this.ionHome(gid);
    if (home) {
      for (const e of this.eventsOf(home.leaf)) {
        if (e[4] === "frame" || e[4] === "cycle") continue;
        out.push({ kind: "home-op", t0: e[1], t1: e[2], leaf: e[3], op: e[4], ins: e[6], local: home.local });
      }
    }
    out.sort((a, b) => a.t0 - b.t0);
    return out;
  };

  // ------------------------------------------------------------------ leaf replay

  // A leaf's TSIR, replayed for drawing: positions after every instruction, and the path
  // each moving ion takes during it.  Built once per (master, op) and kept.
  function LeafSim(data) {
    this.data = data;
    this.nodeIndex = {};
    this.nodes = data.device.nodes;
    this.nodes.forEach((n, i) => { this.nodeIndex[n.id] = i; });
    this.segEnds = {};
    for (const s of data.device.segments) this.segEnds[s.id] = s.ends;
    this.loops = {};
    for (const lp of data.device.loops || []) this.loops[lp.id] = lp.nodes;
    this.cache = {};
  }

  LeafSim.prototype.prepare = function (op) {
    if (this.cache[op]) return this.cache[op];
    const prog = this.data.programs[op];
    if (!prog) return null;
    const times = this.data.times[op] || [];
    const ins = prog.instructions;
    const init = ins[0].placement;
    const ions = Object.keys(init).sort(naturalCmp);
    const idx = {};
    ions.forEach((n, i) => { idx[n] = i; });
    let pos = ions.map((n) => this.nodeIndex[init[n]]);
    const steps = [];
    for (let k = 0; k < ins.length; k++) {
      const I = ins[k];
      const moves = [];
      if (I.type === "simd") {
        if (I.template && I.template.kind === "loop_shift") {
          const loop = this.loops[I.template.loop];
          const li = {};
          loop.forEach((id, j) => { li[this.nodeIndex[id]] = j; });
          const d = I.template.delta, L = loop.length;
          pos.forEach((p, i) => {
            if (!(p in li)) return;
            const path = [];
            for (let s = 0; s <= Math.abs(d); s++) {
              path.push(this.nodeIndex[loop[(((li[p] + Math.sign(d) * s) % L) + L) % L]]);
            }
            moves.push([i, path]);
          });
        } else if (I.participants) {
          for (const p of I.participants) {
            const i = idx[p.ion];
            const path = [this.nodeIndex[p.from]];
            let cur = p.from;
            for (const sid of p.via || []) {
              const e = this.segEnds[sid];
              cur = e[0] === cur ? e[1] : e[0];
              path.push(this.nodeIndex[cur]);
            }
            if (path[path.length - 1] !== this.nodeIndex[p.to]) path.push(this.nodeIndex[p.to]);
            moves.push([i, path]);
          }
        }
      }
      const before = pos.slice();
      for (const [i, path] of moves) pos[i] = path[path.length - 1];
      let active = [];
      if (I.type === "gate") {
        if (I.pairs) for (const pr of I.pairs) active.push(idx[pr[0]], idx[pr[1]]);
        if (I.ions) for (const n of I.ions) active.push(idx[n]);
      } else if (I.type === "measure" || I.type === "reset") {
        for (const n of I.ions || []) active.push(idx[n]);
      }
      const tt = times[k] || [I.id, 0, 0];
      steps.push({ ins: I, t0: tt[1], t1: tt[2], before, moves, active });
    }
    return (this.cache[op] = { ions, idx, steps, final: pos.slice() });
  };

  // Where every ion is at local time `local` of `op`: [x, y] per ion (in device units),
  // the instruction index, and which ions the instruction acts on.
  LeafSim.prototype.state = function (op, local) {
    const P = this.prepare(op);
    if (!P) return null;
    const steps = P.steps;
    let k = lowerBound(steps, local + 1e-9, (s) => s.t0) - 1;
    if (k < 0) k = 0;
    const st = steps[k];
    const frac = st.t1 > st.t0 ? Math.max(0, Math.min(1, (local - st.t0) / (st.t1 - st.t0))) : 1;
    const nodes = this.nodes;
    const xy = st.before.map((ni) => nodes[ni].pos.slice());
    for (const [i, path] of st.moves) {
      if (path.length < 2) continue;
      const u = frac * (path.length - 1);
      const s = Math.min(path.length - 2, Math.floor(u));
      const w = u - s;
      const a = nodes[path[s]].pos, b = nodes[path[s + 1]].pos;
      xy[i] = [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w];
    }
    return { k, frac, xy, ions: P.ions, active: st.active, step: st };
  };

  root.GadgetCore = { Model, LeafSim, lowerBound, naturalCmp };
})(typeof window !== "undefined" ? window : globalThis);
