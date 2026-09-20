// WHERE EVERY ION IS WHILE THE MACHINE IS MOVING IT.  One law, two canvases.
//
// This file exists because there were two of them.  The studio stage (the inline script in
// `qccd/viz/render.py`) had grown a careful occupancy model -- slot order carried forward
// across frames, a departing ion interpolating from the slot it leaves to the slot it
// arrives in, and a detour drawn round a trap-mate it has to get past -- and
// `tests/test_viz_js.py` pins two properties of it: no two ion marks ever overlap, and no
// ion jumps at a frame boundary.  The gadget Design canvas (`qccd/gadget/web/core.js`)
// had none of it: every ion was interpolated along its own node path with no reference to
// any other ion, and `app.js::spread` then pushed apart only those whose coordinates
// collided to three decimal places.  Measured on `site/gadgets/demo` before this module
// existed: 41% of sampled instants drew two ions through each other, the worst pair at a
// separation of exactly zero, and 23,496 sideways flicks as that three-decimal test
// switched on and off between one sample and the next -- which is what "the ions jump back
// and forth" looks like from the outside.
//
// So the law lives here and both canvases call it.  Geometry does not: each canvas keeps
// its own, and hands this module six functions (`pos`, `axis`, `slotOffsets`, `edgeLen`,
// `edgePoint`, and the `bow` amplitude).  The studio's rails are bowed quadratics and its
// distances are pixels; the gadget's are straight lines in device units.  Neither fact
// belongs in an occupancy rule.
//
// THE RULE, in four parts.
//
//   1. ORDER IS CARRIED FORWARD, NEVER RE-DERIVED.  `slotOrder` walks the whole programme
//      once, in order: ions that stay keep their place, ions that leave are struck out, and
//      an arrival joins at the END IT ARRIVES FROM.  Re-deriving the order inside a step
//      cannot satisfy both invariants at once -- within a step it must not change (or two
//      ions cross through each other) and across a boundary it must not change either (or
//      an ion jumps a whole slot pitch).
//
//   2. EVERY ION INTERPOLATES ITS SLOT, moving or resting.  A flier goes from (source node,
//      source slot) to (destination node, destination slot); a rester whose trap-mate has
//      just left slides into the room that freed up over the same interval.  One law for
//      the whole stage: nothing on it is pinned to an end state while the other marks move.
//
//   3. AN ION THAT MUST GET PAST ANOTHER GOES ROUND IT.  `passes` answers "which ions
//      cannot reach their slot without crossing another ion's ground?", and those ions are
//      drawn along a detour of `bow` at mid-flight.  It is not decoration: two ions
//      exchanging places inside a trap is a real and expensive event (the corpus prices it
//      as three CX), and one drawn as a straight line through its neighbour would be both
//      an overlap and a lie about the hardware.
//
//   4. PARTNERS TAKE OPPOSITE SIDES.  This is the part the studio did not have.  Its detour
//      was always `-ax.uy, +ax.ux` -- one fixed side -- so two ions exchanging ends of one
//      rail both swung the same way and still met in the middle.  Conflicting ions are
//      grouped, sorted naturally, and given alternating sides, so an exchange is drawn as
//      what it physically is: the two ions passing on opposite sides of the axis and
//      arriving in each other's slot.
//
// Nothing here knows about frames, instructions, SVG or canvas.  A `step` is four plain
// objects -- where everything was, where everything ends up, the node path of each ion that
// moves, and the two slot orders -- and `place` answers with a position per ion.

(function (root) {
  "use strict";

  // 'd10' after 'd2', not before it.  Plain lexicographic order changes which slot an ion
  // holds as the population around it changes, which is a jump at a frame boundary.
  function naturalCmp(a, b) {
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  }

  // `{place: [ion, ...]}` as `{place: {ion: index}}`: the same order, read rather than
  // searched for.
  function _rankOf(ord) {
    var out = {};
    for (var id in ord) {
      var L = ord[id], r = out[id] = {};
      for (var i = 0; i < L.length; i++) r[L[i]] = i;
    }
    return out;
  }

  function Transit(geom) {
    this.geom = geom || {};
    this._plen = (typeof WeakMap === "function") ? new WeakMap() : null;
  }

  // ---------------------------------------------------------------- geometry adapter

  Transit.prototype.pos = function (id) {
    var p = this.geom.pos ? this.geom.pos(id) : null;
    return (p && isFinite(p[0]) && isFinite(p[1])) ? p : null;
  };
  Transit.prototype.axis = function (id) {
    var a = this.geom.axis ? this.geom.axis(id) : null;
    return (a && (a[0] || a[1])) ? a : [1, 0];
  };
  Transit.prototype.slotOffsets = function (id, k) {
    if (this.geom.slotOffsets) return this.geom.slotOffsets(id, k);
    return { off: new Array(k).fill(0), pitch: 0 };
  };
  Transit.prototype.edgeLen = function (a, b) {
    if (this.geom.edgeLen) return this.geom.edgeLen(a, b);
    var p = this.pos(a), q = this.pos(b);
    return (p && q) ? Math.hypot(q[0] - p[0], q[1] - p[1]) : 0;
  };
  Transit.prototype.edgePoint = function (a, b, u) {
    if (this.geom.edgePoint) return this.geom.edgePoint(a, b, u);
    var p = this.pos(a), q = this.pos(b);
    if (!p || !q) return null;
    return { x: p[0] + (q[0] - p[0]) * u, y: p[1] + (q[1] - p[1]) * u };
  };
  Transit.prototype.bow = function () {
    return +(this.geom.bow || 0);
  };

  // WHICH PLACE A NODE IS, which is not always which node it is.  A device may put two
  // distinct nodes at one coordinate -- the BB72 memory leaf does it twelve times, two
  // ancilla traps to a point -- and ions standing on them are drawn at the same spot.
  // Occupancy is about the PICTURE, so it is grouped by place: `site(id)` returns the node
  // that stands for the coordinate, and two co-located traps share one slot stack.  The
  // default is the identity, which is what every shipped studio architecture wants (none
  // of the nine has a repeated position), and the adapter overrides it where the geometry
  // needs it.  Paths are NOT mapped through this: a rail joins real nodes.
  Transit.prototype.site = function (id) {
    return this.geom.site ? (this.geom.site(id) || id) : id;
  };

  // ------------------------------------------------------- a point along a node path

  // BY ARC LENGTH, not by hop count.  Spreading `t` uniformly over the HOPS of a path
  // makes an ion crossing a 3-unit segment and then a 1-unit one spend half the step on
  // each: a constant-velocity shuttle drawn at two different speeds, visibly lurching at
  // the join.  The hop lengths are the DRAWN lengths (a bow's arc where the canvas bows
  // its rails), summed once per path and cached.
  Transit.prototype.hopLengths = function (path) {
    var rec = this._plen && this._plen.get(path);
    if (rec) return rec;
    var segs = [], total = 0;
    for (var i = 0; i + 1 < path.length; i++) {
      var d = this.edgeLen(path[i], path[i + 1]);
      segs.push(d); total += d;
    }
    rec = { segs: segs, total: total };
    if (this._plen) this._plen.set(path, rec);
    return rec;
  };

  Transit.prototype.pointOnPath = function (path, t) {
    if (!path || !path.length) return null;
    if (path.length === 1) {
      var n = this.pos(path[0]);
      return n ? { x: n[0], y: n[1], a: null, b: null, u: 0 } : null;
    }
    var u = Math.min(Math.max(t, 0), 1), H = this.hopLengths(path), i, local;
    if (H.total <= 1e-9) {
      // every hop is a point (a device drawn at zero scale): fall back to hop-uniform
      var span = (path.length - 1) * u;
      i = Math.min(Math.floor(span), path.length - 2); local = span - i;
    } else {
      var d = H.total * u; i = 0;
      while (i < H.segs.length - 1 && d > H.segs[i]) { d -= H.segs[i]; i++; }
      local = H.segs[i] > 1e-9 ? Math.min(1, d / H.segs[i]) : 0;
    }
    var q = this.edgePoint(path[i], path[i + 1], local);
    if (!q) {
      var m = this.pos(path[i]) || this.pos(path[i + 1]);
      return m ? { x: m[0], y: m[1], a: null, b: null, u: 0 } : null;
    }
    // a hop with no rail under it is not a hop: the ion is parked on the node it was on,
    // and nothing downstream may treat it as travelling along a segment
    if (q.norail) return { x: q.x, y: q.y, a: null, b: null, u: 0, norail: true };
    return { x: q.x, y: q.y, a: path[i], b: path[i + 1], u: local };
  };

  // ------------------------------------------------------------------- slot order

  // A WALKER, because the whole programme is more than anyone is looking at.
  //
  // The order at step k depends on every step before it, so it cannot be computed for one
  // instant in isolation -- but it does not have to be computed for the WHOLE op before
  // the first frame either.  `orderWalker(steps).at(k)` extends the walk to k and no
  // further, so opening a view 12% of the way into an op costs 12% of the walk and
  // playing it forward pays the rest a step at a time.  Computing all of it up front is
  // what put a visible hitch on the gadget's top level, where sixteen leaf instances
  // prepare their masters on the frame you first open it.
  //
  // `slotOrder(steps)` is the whole walk, which is what a renderer that is about to draw
  // every frame anyway (`tools/make_gif.py`) and every test wants.
  Transit.prototype.orderWalker = function (steps) {
    var self = this, out = [], done = 0;
    var cur = {}, where = {}, nWhere = 0;
    var posOf = {}, axOf = {}, siteOf = {};
    var siteFor = function (id) {
      var s = siteOf[id];
      return s === undefined ? (siteOf[id] = self.site(id)) : s;
    };
    var posFor = function (id) {
      var p = posOf[id];
      return p === undefined ? (posOf[id] = self.pos(id)) : p;
    };
    var axFor = function (id) {
      var a = axOf[id];
      return a === undefined ? (axOf[id] = self.axis(id)) : a;
    };
    // `steps` is an array, or `{length, get(i)}` for a caller that would rather build a
    // step's tables when the walk reaches it than build all of them up front.
    var count = steps.length;
    var stepAt = (typeof steps.get === "function")
      ? function (i) { return steps.get(i); }
      : function (i) { return steps[i]; };
    function advance(upto) {
      for (; done <= upto && done < count; done++) {
        var st = stepAt(done) || {};
        var ps = st.paths || {}, pos = st.pos || {}, was = st.before || {};
        // ONE pass over `pos`, which is the only O(ions) thing left: it decides where
        // everyone is, which places changed, and -- IN `pos` ORDER, which is what makes
        // this identical to the straightforward version -- who arrived.  Everything after
        // it is proportional to the handful of places an instruction actually disturbs.
        var now = {}, dirty = {}, arrived = [], nNow = 0, ion, at, p;
        for (ion in pos) {
          at = siteFor(pos[ion]);
          if (!posFor(at)) continue;          // a node the device no longer has
          now[ion] = at; nNow++;
          if (where[ion] !== at) {
            dirty[at] = 1;
            arrived.push(ion);
            if (where[ion] !== undefined) dirty[where[ion]] = 1;
          }
        }
        // an ion that LEFT the stage also disturbs the place it was in.  Only possible
        // when the population shrank, so the scan is skipped in the common case.
        if (nNow !== nWhere) for (ion in where) if (now[ion] === undefined) dirty[where[ion]] = 1;
        var next = {};
        for (p in cur) if (dirty[p] === undefined) next[p] = cur[p];
        var rebuilt = {};
        for (p in dirty) {
          var old = cur[p] || [], keep = [];
          for (var j = 0; j < old.length; j++) if (now[old[j]] === p) keep.push(old[j]);
          rebuilt[p] = keep;
        }
        for (var a = 0; a < arrived.length; a++) {
          ion = arrived[a]; at = now[ion];
          var src = (ps[ion] && ps[ion][0]);
          if (src === undefined) src = was[ion];
          if (src === undefined) src = pos[ion];
          var n = posFor(at), o = posFor(siteFor(src)), ax = axFor(at);
          var d = o ? (o[0] - n[0]) * ax[0] + (o[1] - n[1]) * ax[1] : 0;
          if (d < 0) rebuilt[at].unshift(ion); else rebuilt[at].push(ion);
        }
        for (p in rebuilt) if (rebuilt[p].length) next[p] = rebuilt[p];
        out.push(next); cur = next; where = now; nWhere = nNow;
      }
    }
    return {
      at: function (k) {
        if (k < 0) return {};
        advance(k);
        return out[k] || {};
      },
      all: function () { advance(count - 1); return out; },
    };
  };

  // `steps` is `[{before, pos, paths}]` for the whole programme, in order.  The return is
  // one `{node: [ion, ...]}` per step: the order ions occupy each PLACE at the end of it.
  //
  // The whole walk, for a caller that is going to draw every frame anyway
  // (`tools/make_gif.py`) or that is asserting something about all of them (the tests).
  // A canvas should use `orderWalker` and pay for the part it is showing.
  Transit.prototype.slotOrder = function (steps) {
    return this.orderWalker(steps).all();
  };

  // ------------------------------------------------------ who has to get past whom

  // Every unordered pair of ions that cannot both hold a straight line through this step.
  // Four kinds, all of which the picture must draw as a detour rather than a glide:
  //
  //   exchange  A ends where B began and B ends where A began -- they trade places
  //   leave     A exits its trap towards a side a trap-mate is staying on
  //   arrive    A enters a trap past a resident already between it and its own slot
  //   over      A's path runs THROUGH a node a resting ion holds for the whole step
  //
  // `kinds` is returned as well as `side` so a caller can report what it drew.
  Transit.prototype.passes = function (step, ranks) {
    var self = this;
    var was = step.before || {}, pos = step.pos || {}, paths = step.paths || {};
    var ordStart = step.ordStart || {}, ordEnd = step.ordEnd || {};
    var pairs = [], seen = {};
    // `ranks` is `{start, end}`, each `{place: {ion: index}}`.  `_prep` has already built
    // them; a caller that has not (the tests, the parity harness) gets them built here.
    // Without them `blockedBy` was `list.indexOf` around `resident.indexOf`, which is
    // O(k^2) per mover -- 5,000 operations per ion in a trap of capacity 72, 144 movers,
    // and the single most expensive thing this file did.
    var rank = ranks || { start: _rankOf(ordStart), end: _rankOf(ordEnd) };

    function add(a, b, kind) {
      var swap = naturalCmp(a, b) > 0;
      var lo = swap ? b : a, hi = swap ? a : b;
      var k = lo + "\u0000" + hi;
      if (seen[k]) return;
      seen[k] = kind;
      pairs.push([lo, hi, kind]);
    }

    // which side of `here` is `there` on, measured along the site's own axis
    function side(here, there) {
      var n = self.pos(here), o = self.pos(there), ax = self.axis(here);
      return (n && o) ? (o[0] - n[0]) * ax[0] + (o[1] - n[1]) * ax[1] : 0;
    }
    // is anyone in `list` between `ion` and the end it is heading for, who is in `resident`?
    // `at` is the ion's index in `list` and `resident` is a membership map: both are reads
    // rather than searches, which is what keeps this linear.
    function blockedBy(list, at, dir, resident) {
      var out = [];
      if (at === undefined || list.length < 2 || !dir) return out;
      for (var k = 0; k < list.length; k++) {
        if (k === at) continue;
        if ((dir > 0 ? k > at : k < at) && resident[list[k]] !== undefined) out.push(list[k]);
      }
      return out;
    }

    // who sits where at the start, and -- INDEXED, not searched -- where each mover ends.
    // The exchange test was a loop over every other mover, which is O(movers^2) and the
    // stage runs it once per animation frame: 144 ions in a rigid rotation is 20,000
    // comparisons 60 times a second for an answer that one lookup gives.
    var atStart = {}, endsAt = {};
    for (var ion0 in was) {
      var s0 = self.site(was[ion0]);
      (atStart[s0] || (atStart[s0] = [])).push(ion0);
    }
    for (var ion1 in paths) {
      var q1 = paths[ion1];
      if (!q1 || q1.length < 2) continue;
      var e1 = self.site(q1[q1.length - 1]);
      (endsAt[e1] || (endsAt[e1] = [])).push(ion1);
    }

    for (var ion in paths) {
      var path = paths[ion];
      if (!path || path.length < 2) continue;
      var from = self.site(path[0]), to = self.site(path[path.length - 1]);
      if (from === to) continue;

      // -- exchange: someone else ends where this one began, and began where it ends
      var back = endsAt[from] || [];
      for (var bi = 0; bi < back.length; bi++) {
        var other = back[bi];
        if (other === ion) continue;
        var op = paths[other];
        if (op && self.site(op[0]) === to) add(ion, other, "exchange");
      }

      // -- leaving: does anyone STAY BEHIND on the side it exits towards?
      var outs = blockedBy(ordStart[from] || [], (rank.start[from] || {})[ion],
                           side(from, to), rank.end[from] || {});
      for (var i1 = 0; i1 < outs.length; i1++) add(ion, outs[i1], "leave");

      // -- arriving: is anyone ALREADY THERE between the end it enters by and its slot?
      var ins = blockedBy(ordEnd[to] || [], (rank.end[to] || {})[ion],
                          side(to, from), rank.start[to] || {});
      for (var i2 = 0; i2 < ins.length; i2++) add(ion, ins[i2], "arrive");

      // -- passing OVER a node someone is holding for the whole step.  This is the case
      // the studio never tested for: its `mustPass` looked only at the two ENDS of the
      // walk, so an ion crossing a trap that another ion sits in the whole time was drawn
      // gliding straight over it.  On a lattice that is the common case, because a route
      // from one trap to another runs through the traps in between.
      for (var h = 1; h < path.length - 1; h++) {
        var at3 = self.site(path[h]), held = atStart[at3] || [];
        for (var i3 = 0; i3 < held.length; i3++) {
          var occ = held[i3];
          if (occ === ion) continue;
          // only a RESTING ion is passed over; one that is itself leaving this node is
          // getting out of the way, and the two are a convoy rather than a conflict
          if (self.site(pos[occ]) === at3) add(ion, occ, "over");
        }
      }
    }

    // ---- which side of the axis each one goes round on.
    //
    // A RESTING ion is never given a detour -- it is drawn in its slot, and `place` sends
    // it down the other branch entirely -- so the side of a mover that has to get past a
    // stationary trap-mate is free, and it keeps the +1 the studio has always used.  What
    // is NOT free is two ions that are BOTH moving and both have to get past each other:
    // a single fixed side sent them round the same way and they met in the middle anyway,
    // which is the defect this part exists to fix.  So the alternation runs over the
    // mover-only subgraph, and everything else keeps the old side.
    var side_ = {};
    for (var p = 0; p < pairs.length; p++) {
      side_[pairs[p][0]] = 1; side_[pairs[p][1]] = 1;
    }
    // BY PLACE, as everywhere else in this function.  Comparing the raw node ids instead
    // says an ion shuffling between two traps at one coordinate is "moving", and it is not
    // -- the loop above skipped it for exactly that reason (`if (from === to) continue`).
    // The two implementations disagreed here and nowhere else: the Python twin asked about
    // the place, this asked about the node, and an ion in that position came out bowed on
    // one side in the browser and the other in a GIF of the same programme.  Reachable
    // whenever such an ion is the partner in somebody else's conflict;
    // `tests/test_transit_parity.py::colocated_mover` is that case, and it failed.
    var moves = function (i) {
      var q = paths[i];
      return !!(q && q.length > 1 && self.site(q[0]) !== self.site(q[q.length - 1]));
    };
    var adj = {};
    for (var p2 = 0; p2 < pairs.length; p2++) {
      var a2 = pairs[p2][0], b2 = pairs[p2][1];
      if (!moves(a2) || !moves(b2)) continue;
      (adj[a2] || (adj[a2] = [])).push(b2);
      (adj[b2] || (adj[b2] = [])).push(a2);
    }
    var keys = Object.keys(adj).sort(naturalCmp), done = {};
    for (var ki = 0; ki < keys.length; ki++) {
      if (done[keys[ki]]) continue;
      // one component, gathered breadth-first from its naturally-first member, then sorted
      // so the assignment does not depend on the order the pairs were found in
      var comp = [], queue = [keys[ki]];
      done[keys[ki]] = 1;
      while (queue.length) {
        var cur = queue.shift();
        comp.push(cur);
        var nb = adj[cur] || [];
        for (var n2 = 0; n2 < nb.length; n2++) if (!done[nb[n2]]) { done[nb[n2]] = 1; queue.push(nb[n2]); }
      }
      comp.sort(naturalCmp);
      for (var c = 0; c < comp.length; c++) side_[comp[c]] = (c % 2 === 0) ? 1 : -1;
    }
    return { pairs: pairs, side: side_, kinds: seen };
  };

  // ------------------------------------------------------------------- placement

  // Where every ion is drawn at `t` in [0, 1] of one step.
  //
  //   step.before   {ion: node} at the start      step.pos     {ion: node} at the end
  //   step.paths    {ion: [node, ...]} for movers step.t       0..1 through the step
  //   step.ordStart {node: [ion, ...]}            step.ordEnd  {node: [ion, ...]}
  //   step.rest     true to pin everything to its end state (the studio's `phase === 1`)
  //
  // Returns `{live, flying, occStart, occEnd, passes}`:
  //   live[ion]     {x, y, fly, tt, pitchA, pitchB, swap, tight}  for a mover
  //                 {x, y, fly: false, node, pitch}               for a rester
  //   flying[ion]   the raw point on the path, carrying `{a, b, u}`: which hop it is on
  // EVERYTHING IN A STEP THAT DOES NOT DEPEND ON `t`, worked out once.
  //
  // The step's combinatorics -- who is at which place, in what order, in which slot, and
  // who has to get past whom -- are the same at every instant of it; only the positions
  // move.  Recomputing them per instant is what made this 95x slower than the code it
  // replaced on a trap of capacity 72: `Array.indexOf` inside a sort comparator is O(k)
  // per comparison, and `slotOffsets` was rebuilding a 72-element array twice per ion.
  // KEYED ON THE STEP, NOT ON THE LAST CALL.  A one-entry memo looks sufficient -- an
  // animation advances smoothly and asks for the same step many times -- and it is, for
  // one device on screen.  The gadget's top level draws sixteen leaf instances per frame
  // out of ONE `LeafSim` per master, each at its own instruction, so a single slot is
  // evicted by the next instance and every one of them pays the full derivation: 12 ms
  // per frame became 75 ms.  A WeakMap on `step.pos` -- an object both callers hold for
  // the life of a step -- gives every instance its own entry and lets the whole thing be
  // collected with the programme.  The other four references are still checked, so a
  // caller that rebuilds its tables without rebuilding `pos` gets a correct miss.
  Transit.prototype._prep = function (step) {
    var cache = this._prepCache ||
      (this._prepCache = (typeof WeakMap === "function") ? new WeakMap() : null);
    var m = cache ? (step.pos && cache.get(step.pos)) : this._memo;
    if (m && m.before === step.before && m.pos === step.pos && m.paths === step.paths &&
        m.ordStart === step.ordStart && m.ordEnd === step.ordEnd) return m;
    var self = this;
    var pos = step.pos || {}, paths = step.paths || {};
    var ordStart = step.ordStart || {}, ordEnd = step.ordEnd || {};
    var srcOf = {}, dstOf = {}, occS = {}, occ = {};
    for (var ion in pos) {
      var path = paths[ion];
      // the PLACE each end is, not the node: two traps at one coordinate are one stack
      var a = self.site(path ? path[0] : pos[ion]);
      var b = self.site(path ? path[path.length - 1] : pos[ion]);
      if (!self.pos(a) || !self.pos(b)) continue;
      srcOf[ion] = a; dstOf[ion] = b;
      (occS[a] || (occS[a] = [])).push(ion);
      (occ[b] || (occ[b] = [])).push(ion);       // occupancy at the END of the step
    }
    // ONE key for both lists -- the precomputed order.  Using different keys at the two
    // ends lets the order invert mid-step, and two ions swapping slots then pass straight
    // through each other.  The rank is read out of a map rather than searched for, which
    // is the same order by a cheaper route.
    var rS = _rankOf(ordStart), rE = _rankOf(ordEnd);
    var sortBy = function (tbl, ranks) {
      for (var id in tbl) {
        var r = ranks[id] || {};
        tbl[id].sort(function (x, y) {
          var ix = r[x], iy = r[y];
          return ((ix === undefined ? 1e9 : ix) - (iy === undefined ? 1e9 : iy))
            || naturalCmp(x, y);
        });
      }
    };
    sortBy(occS, rS); sortBy(occ, rE);

    // one slot table per (place, population), not one per ion
    var slots = {};
    var slotsFor = function (id, k) {
      var key = id + "\u0000" + k;
      return slots[key] || (slots[key] = self.slotOffsets(id, k));
    };
    var slotAt = function (id, list, at) {
      var k = list.length, s = slotsFor(id, k), ax = self.axis(id), o = s.off[at] || 0;
      return { ox: ax[0] * o, oy: ax[1] * o, pitch: k > 1 ? s.pitch : 0, node: id };
    };
    var A = {}, B = {};
    for (var p1 in occS) for (var i1 = 0; i1 < occS[p1].length; i1++)
      A[occS[p1][i1]] = slotAt(p1, occS[p1], i1);
    for (var p2 in occ) for (var i2 = 0; i2 < occ[p2].length; i2++)
      B[occ[p2][i2]] = slotAt(p2, occ[p2], i2);

    var rec = {
      before: step.before, pos: step.pos, paths: step.paths,
      ordStart: step.ordStart, ordEnd: step.ordEnd,
      srcOf: srcOf, dstOf: dstOf, occS: occS, occ: occ, A: A, B: B,
      pass: self.passes(step, { start: rS, end: rE }),
    };
    if (cache && step.pos) cache.set(step.pos, rec); else this._memo = rec;
    return rec;
  };

  Transit.prototype.place = function (step) {
    var self = this;
    var paths = step.paths || {};
    var ordStart = step.ordStart || {}, ordEnd = step.ordEnd || {};
    var t = Math.min(1, Math.max(0, +step.t || 0));
    var rest = !!step.rest;
    var P = this._prep(step);
    var srcOf = P.srcOf, occS = P.occS, occ = P.occ, pass = P.pass;
    var live = {}, flying = {};
    var bow0 = self.bow();

    for (var ion2 in srcOf) {
      var pth = paths[ion2];
      var A = P.A[ion2], B = P.B[ion2];
      if (pth && !rest) {
        var q = self.pointOnPath(pth, t);
        if (!q || !isFinite(q.x)) continue;
        // the detour, zero at both ends and `bow` at mid-flight, on this ion's own side
        var sd = pass.side[ion2] || 0;
        var bow = (sd && bow0) ? sd * bow0 * 4 * t * (1 - t) : 0;
        var bx = 0, by = 0;
        if (bow) { var ax2 = self.axis(A.node); bx = -ax2[1] * bow; by = ax2[0] * bow; }
        // an ion moving into or out of an OCCUPIED trap gets no flourish -- it needs the
        // room, not the bulk
        var tight = ((ordStart[A.node] || []).length > 1) || ((ordEnd[B.node] || []).length > 1);
        live[ion2] = { x: q.x + A.ox + (B.ox - A.ox) * t + bx,
                       y: q.y + A.oy + (B.oy - A.oy) * t + by,
                       fly: true, tt: t, pitchA: A.pitch, pitchB: B.pitch,
                       swap: !!bow, tight: tight };
        flying[ion2] = q;
      } else {
        // A resting ion interpolates its slot too.  Its site's POPULATION changes during
        // the step -- an ion leaving frees a slot and the one staying behind shifts into
        // the middle -- so pinning it to the end-state slot makes it jump the moment the
        // step begins, straight into the ion still departing.
        var pa = self.pos(A.node), pb = self.pos(B.node);
        if (!pa || !pb) continue;
        var x0 = pa[0] + A.ox, y0 = pa[1] + A.oy, x1 = pb[0] + B.ox, y1 = pb[1] + B.oy;
        var u2 = rest ? 1 : t;
        live[ion2] = { x: x0 + (x1 - x0) * u2, y: y0 + (y1 - y0) * u2, fly: false, node: B.node,
                       pitch: (A.pitch && B.pitch) ? A.pitch + (B.pitch - A.pitch) * u2
                                                   : (B.pitch || A.pitch) };
      }
    }
    return { live: live, flying: flying, occStart: occS, occEnd: occ, passes: pass };
  };

  // Published on `globalThis`, the way `engine.js` publishes `QCCD`, and NOT on `window`
  // alone: under `tests/shim.mjs` the page's scripts run inside one `new Function`, where
  // `window` is a stub object and a bare `QCCDTransit` resolves against `globalThis`.  A
  // module that reached only `window` was invisible to the page that inlines it, which is
  // a whole harness reporting `QCCDTransit is not defined`.
  var api = { Transit: Transit, naturalCmp: naturalCmp };
  if (typeof globalThis !== "undefined") globalThis.QCCDTransit = api;
  if (root && root !== globalThis) root.QCCDTransit = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
