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

  // HOW FAR A TRAP'S OWN BUSINESS REACHES: half the drawn length of its site bar.  This
  // is the scale the detour tapers over, and it is not the slot pitch -- a capacity-4 trap
  // on `cyclone_base` is 0.88 g long against a lattice step of g, so its ions are still
  // inside their own trap a quarter of the way to the next one, while its slot pitch is
  // 0.22 g.  Tapering on the pitch switched the detour off exactly where an ion leaving
  // that trap has to get past the one standing in the outer slot.
  Transit.prototype.span = function (id) {
    return this.geom.span ? (+this.geom.span(id) || 0) : 0;
  };

  // HALF THE THICKNESS OF THE SITE BAR -- how far ACROSS its own trap an ion may go.
  // The detour that lets two ions get past each other has to happen somewhere, and the
  // somewhere is inside the trap: a bar is `site_t` thick (0.199 g in the shipped
  // layout), so half of that is the whole of the room there is.  Going further draws an
  // ion outside the capsule it is supposed to be confined in, which is what "when ions
  // swap they should not jump outside the site" means.
  Transit.prototype.across = function (id) {
    return this.geom.across ? (+this.geom.across(id) || 0) : 0;
  };

  // The same question for a RAIL, which is the metal between the traps.  A rail is
  // narrower than a site bar -- it carries one ion at a time and has no stack to arrange
  // -- so out between the traps there is almost no room to step aside, and there is no
  // need for any: ions are single file on a rail and `r5_no_exchange` forbids two of
  // them trading places along one.
  Transit.prototype.railAcross = function () {
    return this.geom.rail ? (+this.geom.rail || 0) : 0;
  };

  // HOW MUCH ROOM ACROSS THE METAL THERE IS WHERE THIS ION IS STANDING.  Inside its own
  // bar, half the bar's thickness; out on the rail, half the rail's.  The step between
  // them is ramped at slope one -- the confinement narrows no faster than the ion
  // travels -- so an ion leaving its trap is walked back to the centre line rather than
  // snapped to it.
  Transit.prototype.roomAcross = function (x, y, ends) {
    var best = this.railAcross();
    for (var i = 0; i < ends.length; i++) {
      var S = ends[i];
      if (!S) continue;
      var nd = S.axisNode || S.node, a = this.across(nd);
      if (!(a > best)) continue;
      var p = this.pos(nd);
      if (!p) continue;
      var ax = this.axis(nd), h = this.span(nd);
      var along = Math.abs((x - p[0]) * ax[0] + (y - p[1]) * ax[1]);
      var out = along - h;                       // how far past the end of the bar
      var room = out <= 0 ? a : (out < a - best ? a - out : best);
      if (room > best) best = room;
    }
    return best;
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
    if (q.norail) return { x: q.x, y: q.y, a: null, b: null, u: 0, norail: true,
                           hop: i, hops: path.length - 1 };
    // `s` / `left` are the ARC travelled and the arc remaining, in drawn units.  The slot
    // offsets are shed and taken up over those, not over `t`: a bar has a length and that
    // length is how far an ion has to slide to get out of its stack.
    var done = 0;
    for (var k = 0; k < i; k++) done += H.segs[k];
    done += (H.segs[i] || 0) * local;
    return { x: q.x, y: q.y, a: path[i], b: path[i + 1], u: local,
             hop: i, hops: path.length - 1, s: done, left: H.total - done };
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
    // WHICH IONS ARE TRADING ENDS OF ONE RUN.  Every other conflict is about a trap and
    // can be drawn inside it; an exchange is two ions moving in opposite directions along
    // the same metal, and there is no position on that metal where they are not in each
    // other's way.  It is also a step R5 refuses -- "no two ions exchange positions along
    // one segment in a single step" -- so on a verified programme this set is empty, and
    // where it is not, going round is the only honest picture.
    var exch_ = {};
    for (var pe = 0; pe < pairs.length; pe++) {
      if (pairs[pe][2] === "exchange") { exch_[pairs[pe][0]] = 1; exch_[pairs[pe][1]] = 1; }
    }
    return { pairs: pairs, side: side_, kinds: seen, exchange: exch_ };
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
    var srcOf = {}, dstOf = {}, occS = {}, occ = {}, srcNode = {}, dstNode = {};
    for (var ion in pos) {
      var path = paths[ion];
      // the PLACE each end is, not the node: two traps at one coordinate are one stack
      var a = self.site(path ? path[0] : pos[ion]);
      var b = self.site(path ? path[path.length - 1] : pos[ion]);
      if (!self.pos(a) || !self.pos(b)) continue;
      srcOf[ion] = a; dstOf[ion] = b;
      srcNode[ion] = path ? path[0] : pos[ion];
      dstNode[ion] = path ? path[path.length - 1] : pos[ion];
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
    // THE STACK BELONGS TO THE PLACE, THE DIRECTION BELONGS TO THE NODE.
    //
    // Grouping by place is what lets two traps at one coordinate share a slot stack, and
    // that is right.  Taking the slot AXIS from the place's representative is not: on a
    // device that piles several nodes on one point -- `28_tanner_own` has 168 such groups,
    // 1,440 nodes at 834 positions -- the representative can be any of them, and its axis
    // points along its own rail rather than the one this ion is riding.  `K68_70` is
    // represented by `K27_111`, whose axis is [0.817, 0.577]; that was exactly the
    // direction of a 0.19 g stray on an ion with no conflict and no detour.  So the
    // number of slots and the pitch come from the place, and the direction from the ion's
    // own node.
    var slotAt = function (place, node, list, at) {
      var k = list.length, s = slotsFor(place, k), ax = self.axis(node), o = s.off[at] || 0;
      return { ox: ax[0] * o, oy: ax[1] * o, pitch: k > 1 ? s.pitch : 0,
               node: place, axisNode: node };
    };
    var A = {}, B = {};
    for (var p1 in occS) for (var i1 = 0; i1 < occS[p1].length; i1++) {
      var a1 = occS[p1][i1];
      A[a1] = slotAt(p1, srcNode[a1], occS[p1], i1);
    }
    for (var p2 in occ) for (var i2 = 0; i2 < occ[p2].length; i2++) {
      var b2 = occ[p2][i2];
      B[b2] = slotAt(p2, dstNode[b2], occ[p2], i2);
    }

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
    var live = {}, flying = {}, base = {};
    var bow0 = self.bow();
    // OVER HOW MUCH ARC AN END'S SLOT OFFSET IS TAKEN UP.
    //
    // A slot offset lies along its own trap's BAR, so adding it never changes how far the
    // ion is from the bar's centre LINE -- that distance is the rail's own, and the ion
    // is inside the capsule exactly while the rail is within half a thickness of the
    // line.  Which depends entirely on the angle between the two.
    //
    // A rail that leaves ALONG the bar (a chain, a ring: the common case) never leaves the
    // line at all, and the offset may be taken up over the whole bar.  A rail that leaves
    // ACROSS it -- `cyclone_base` does, a vertical rail into a horizontal bar -- departs
    // the line as fast as the ion travels, so the offset may only be taken up over the
    // last half-thickness, or the ion cuts the corner of the T and is drawn on neither.
    // `across / sin(rail, bar)` is both at once, and everything in between.
    var rampAt = function (node, toward) {
      var a = self.across(node);
      if (!(a > 0)) return 0;                       // no bar: the plain blend, below
      var sp = self.span(node), p = self.pos(node), o = self.pos(toward);
      if (!p || !o) return a;
      var dx = o[0] - p[0], dy = o[1] - p[1], d = Math.hypot(dx, dy);
      if (!(d > 1e-9)) return a;
      var ax = self.axis(node);
      var sin = Math.abs((dx * ax[1] - dy * ax[0]) / d);
      var r = sin > 1e-6 ? a / sin : (sp || a);
      if (r < a) r = a;
      if (sp > 0 && r > sp) r = sp;
      return r;
    };

    // How much of each end's slot offset applies at this point of the walk.  The frame
    // boundaries are exact: at t=0 the source weight is 1, at t=1 the destination weight
    // is 1, whatever the ramps are.
    var slotWeights = function (q, tt, rampA, rampB) {
      if (!q || q.s === undefined) return [1 - tt, tt];
      // A ramp of zero is "no bar to slide along", not "no offset": a junction has no
      // slots, so its weight is moot, and a geometry that supplies no `across` at all
      // wants the plain blend.  Either way the ends stay exact.
      var wa = rampA > 1e-9 ? 1 - Math.min(1, q.s / rampA) : 1 - tt;
      var wb = rampB > 1e-9 ? 1 - Math.min(1, q.left / rampB) : tt;
      if (tt <= 0) { wa = 1; wb = 0; } else if (tt >= 1) { wa = 0; wb = 1; }
      // two ramps longer than the rail between them overlap, and an ion cannot be a
      // whole slot offset into both at once
      else if (wa + wb > 1) wb = 1 - wa;
      return [wa, wb];
    };

    // ---- pass one: where every ion is before anybody gets out of anybody's way
    for (var i1 in srcOf) {
      var p1 = paths[i1], A1 = P.A[i1], B1 = P.B[i1];
      if (p1 && !rest) {
        var q1 = self.pointOnPath(p1, t);
        if (!q1 || !isFinite(q1.x)) continue;
        // THE SLOT OFFSET BELONGS TO THE TRAP, NOT TO THE WALK.
        //
        // A1's offset lies along the SOURCE trap's axis and B1's along the DESTINATION
        // trap's, and those are different directions whenever the walk turns a corner.
        // Blending them linearly over the whole walk therefore puts the ion off BOTH
        // rails in the middle of it -- measured at 0.21 g on `28_tanner_own`, eleven
        // lattice units from the nearest junction, on an ion with no conflict and no
        // detour at all.
        //
        // So each offset is taken up WITHIN THE BAR IT BELONGS TO, measured in arc over
        // a ramp that is as long as the bar keeps the rail -- see `rampAt`.  The frame
        // boundaries stay exact at t=0 and t=1 whatever those ramps come to.
        var w1 = slotWeights(q1, t,
                             rampAt(A1.axisNode || A1.node, p1[1]),
                             rampAt(B1.axisNode || B1.node, p1[p1.length - 2]));
        base[i1] = { x: q1.x + A1.ox * w1[0] + B1.ox * w1[1],
                     y: q1.y + A1.oy * w1[0] + B1.oy * w1[1], fly: true, q: q1, u: t };
      } else {
        var pa1 = self.pos(A1.node), pb1 = self.pos(B1.node);
        if (!pa1 || !pb1) continue;
        var u1 = rest ? 1 : t;
        base[i1] = { x: (pa1[0] + A1.ox) + ((pb1[0] + B1.ox) - (pa1[0] + A1.ox)) * u1,
                     y: (pa1[1] + A1.oy) + ((pb1[1] + B1.oy) - (pa1[1] + A1.oy)) * u1,
                     fly: false, u: u1 };
      }
    }

    // ---- who each ion has to get past, as a list it can measure itself against
    var partners = {};
    for (var pp = 0; pp < pass.pairs.length; pp++) {
      var a0 = pass.pairs[pp][0], b0 = pass.pairs[pp][1];
      (partners[a0] || (partners[a0] = [])).push(b0);
      (partners[b0] || (partners[b0] = [])).push(a0);
    }

    // THE DETOUR IS EXACTLY AS BIG AS IT HAS TO BE, AND NOT ONE PIXEL MORE.
    //
    // An ion is carried by the electrodes under the rail it is riding, so a picture that
    // puts one BESIDE the rail draws a motion the machine cannot make.  At a junction --
    // where mid-flight of a two-hop walk lands precisely on the corner -- that reads as
    // the ion cutting across the junction instead of entering it and turning, which is
    // exactly what was reported against `board/bb144/22_planar12_own_a72`: measured at
    // 0.62 g clear of the metal beside J0_6, the whole of `swap_bow`, because the
    // amplitude peaked at mid-flight no matter what was or was not there to avoid.
    //
    // The detour cannot simply go -- without it, 213 frames of `cyclone_base`'s odd-even
    // sort draw one ion through another -- so it is DERIVED rather than shaped.  `clear`
    // is the centre distance two marks need in order not to intersect.  An ion already
    // that far from everything it has to pass needs no detour and is drawn on the rail;
    // one that is closer is lifted by exactly the amount that restores `clear`, which is
    // sqrt(clear^2 - d^2), the perpendicular leg of the triangle.
    //
    // It needs no ramp to vanish at the ends of the walk, because it vanishes on its own:
    // an ion begins and finishes in its own slot, a slot pitch from its neighbour, and a
    // slot pitch is already `clear` -- so the lift is zero there and the mark lands where
    // the next frame expects it.  And nothing rests at a junction, which has no capacity
    // at all, so a crossing has nothing to get past and is drawn on the metal.
    // HOW FAR APART TWO MARKS HAVE TO BE, and it must be LESS than a slot pitch or the
    // detour never switches off: an ion resting in its own slot is one pitch from its
    // neighbour, so a `clear` above that lifts it where it stands and it drops back at
    // the frame boundary -- a 13.7 px jump, measured, which is the teleport this whole
    // module exists to prevent.  A mark in a stack is 0.44 of the pitch, so two of them
    // need 0.88; 0.95 clears that and still stops short of the pitch itself.  Where there
    // is no stack to measure, the marks are whatever the canvas draws them and `bow`
    // already carries that number (it is 1.9 times the two radii).
    var clearOf = function (pitch) { return pitch > 0 ? 0.95 * pitch : 0.62 * bow0; };

    for (var ion2 in base) {
      var me = base[ion2], A = P.A[ion2], B = P.B[ion2];
      var bx = 0, by = 0, lifted = false, room = 0;
      var mates = partners[ion2];
      var myPitch = Math.max(A.pitch || 0, B.pitch || 0);
      if (mates && mates.length && me.fly) {
        var sd = pass.side[ion2] || 1;
        var worst = 0;
        for (var mi = 0; mi < mates.length; mi++) {
          var mate = mates[mi], other = base[mate];
          if (!other) continue;
          // PER PAIR, on the WIDER of the two traps.  A mark is 0.44 of its own slot
          // pitch, so the room two of them need is set by the bigger one; measuring only
          // the mover's trap let an ion slip past a neighbour standing in a roomier one
          // and be drawn through it -- 1.9% of instants on `tcx72`, which the Design
          // canvas's own stage test caught.  Two ions that rest a pitch apart are in the
          // SAME trap and share the pitch, so taking the larger cannot lift either of
          // them where they stand.
          var mA = P.A[mate], mB = P.B[mate];
          var pitch = Math.max(myPitch, (mA && mA.pitch) || 0, (mB && mB.pitch) || 0);
          var clear = clearOf(pitch);
          if (!(clear > 0)) continue;
          var d = Math.hypot(me.x - other.x, me.y - other.y);
          if (d >= clear) continue;
          var lift = Math.sqrt(Math.max(0, clear * clear - d * d));
          if (lift > worst) worst = lift;
        }
        // AND NEVER OFF THE METAL.  Whatever the arithmetic asks for, the ion stays on
        // the electrodes it is being carried by: half a bar's thickness inside its trap,
        // half a rail's out between them.  An ion drawn beside its trap is drawn where no
        // well exists, which is the second thing reported from the site -- "when ions
        // swap, they shouldn't jump outside the site".
        //
        // The base position is already on the metal by construction (each slot offset is
        // taken up within its own bar, see `slotWeights`), so capping the LIFT caps the
        // whole excursion.  And only the lift gives way: an ion with nothing to pass is
        // left exactly where it is, so no frame boundary can snap.  Where the cap leaves
        // two marks too close for their size, it is the MARKS that shrink (`room`,
        // below) -- the exchange is still drawn as an exchange, on opposite sides and
        // ending in each other's slots.
        //
        // HALF the room, not all of it, because the mark has to fit in the other half.
        // A mark is 0.44 of its slot pitch and a bar is `site_t` thick, which on the
        // shipped layout makes a resting mark very nearly as wide as its own bar is
        // deep: an ion lifted the full half-thickness is inside the bar by its centre
        // and outside it by its whole radius.  So the lift takes half the half-width
        // and `room` below holds the mark to the rest.
        var ax2 = self.axis(A.axisNode || A.node), lim = 0;
        if (worst > 0) {
          var qq = me.q || me;
          lim = self.roomAcross(qq.x !== undefined ? qq.x : qq[0],
                                qq.y !== undefined ? qq.y : qq[1], [A, B]);
          if (lim > 0 && worst > 0.5 * lim) worst = 0.5 * lim;
        }
        if (worst > 0) {
          bx = -ax2[1] * sd * worst; by = ax2[0] * sd * worst;
          lifted = true;
        }
        // AND NO BIGGER THAN THE GAP IT IS GOING THROUGH.  A flier's mark is sized by
        // interpolating between the stack it leaves and the stack it arrives in, so an
        // ion leaving a 71-ion trap for an EMPTY one is drawn growing to full size while
        // it is still threading its old neighbours -- radius 0.005 to 0.068 against a
        // gap of 0.011, which swallows thirteen of them.  `room` is half the distance to
        // the nearest ion it has to pass: a mark never covers the neighbour it is
        // getting by, and it grows naturally as it leaves.
      }
      // A MARK IS NEVER WIDER THAN HALF THE SPACE IT IS IN, and the space is measured
      // against every ion around it rather than only the ones `passes` calls partners.
      //
      // Both of those matter, and each came from a picture.  BOTH SIDES: the ion being
      // got past is as much of the pair as the one getting past it, and leaving it at
      // full size while the mover shrank is how two marks 0.118 g apart came to have
      // radii of 0.097 and 0.053 -- the drawing asked one of them to make all the room.
      // EVERY NEIGHBOUR: an ion entering a trap through the middle of its bar is not
      // passing anybody, so `passes` pairs it with nobody, and it is still drawn through
      // the stack that is already in there -- `cyclone_base` enters S36 across the bar,
      // and at the moment the mark reaches the centre line it is 4.6 px from the ion in
      // the inner slot and 4.2 px wide.  So the gap is measured against the stacks at
      // both ends of the walk as well.  An ion at rest among its own trap-mates sits a
      // pitch from each, and 0.45 of a pitch is what it is drawn at anyway, so this
      // costs nothing where there is nothing going on.
      var nbrs = {}, nb;
      if (mates) for (nb = 0; nb < mates.length; nb++) nbrs[mates[nb]] = 1;
      var lsA = occS[A.node] || [], lsB = occ[B.node] || [];
      for (nb = 0; nb < lsA.length; nb++) nbrs[lsA[nb]] = 1;
      for (nb = 0; nb < lsB.length; nb++) nbrs[lsB[nb]] = 1;
      delete nbrs[ion2];
      var gap = Infinity;
      for (var mj in nbrs) {
        var o2 = base[mj];
        if (!o2) continue;
        gap = Math.min(gap, Math.hypot(me.x + bx - o2.x, me.y + by - o2.y));
      }
      if (isFinite(gap)) room = 0.45 * gap;
      // and inside the metal, alongside the lift that has already been taken out of it
      if (lifted && lim > 0) room = Math.min(room || lim, lim - Math.abs(worst));
      if (me.fly) {
        // `swap` says THIS ION IS THREADING PAST ANOTHER, so the drawing knows not to let
        // it swell to full radius on the way: a mark that has to fit through a gap does
        // not also get bigger.  It is a fact about the step, not about how much lateral
        // room the drawing happened to give it.
        var tight = ((ordStart[A.node] || []).length > 1) || ((ordEnd[B.node] || []).length > 1);
        live[ion2] = { x: me.x + bx, y: me.y + by, fly: true, tt: t,
                       pitchA: A.pitch, pitchB: B.pitch,
                       swap: !!(mates && mates.length), tight: tight, lifted: lifted,
                       room: room };
        flying[ion2] = me.q;
      } else {
        // A resting ion interpolates its slot too.  Its site's POPULATION changes during
        // the step -- an ion leaving frees a slot and the one staying behind shifts into
        // the middle -- so pinning it to the end-state slot makes it jump the moment the
        // step begins, straight into the ion still departing.
        live[ion2] = { x: me.x, y: me.y, fly: false, node: B.node, room: room,
                       pitch: (A.pitch && B.pitch) ? A.pitch + (B.pitch - A.pitch) * me.u
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
