// qccd.gadget page: the design tool.  GADGETS.md section 10.
//
// One global clock `S.t` (microseconds) and one current level `S.path` (an instance path;
// "" is the top).  Every view is redrawn from the model at S.t:
//   the stage     the current level's children as gadget boxes with their ports and
//                 channels; ions stream along channels; up to three levels further down
//                 are drawn faintly inside the boxes, with the ions replayed from each
//                 leaf's verified TSIR at that instance's own local time
//   the timeline  one row per child, the ops of its whole subtree; the program's
//                 instructions above; click to seek, wheel to zoom, drag the ruler to pan
//   the panels    hierarchy tree, checks, inspector, the LogicQ source lit as it runs
// A leaf level is the ion-by-ion view: its device, its ions, the TSIR instruction running.

(function () {
  "use strict";
  const { Model, LeafSim, lowerBound, naturalCmp } = window.GadgetCore;
  const D = JSON.parse(document.getElementById("gdata").textContent);
  const M = new Model(D);
  const $ = (id) => document.getElementById(id);
  const css = getComputedStyle(document.documentElement);
  const C = (name, fallback) => (css.getPropertyValue("--" + name).trim() || fallback);

  const COLOR = {
    ink: C("ink", "#182230"), muted: C("muted", "#667085"), line: C("line", "#d0d5dd"),
    soft: C("soft", "#eef2f6"), navy: C("navy", "#1e2761"), accent: C("accent", "#e4572e"),
    teal: C("teal", "#0f766e"), gold: "#b7791f", indigo: "#4338ca", rail: "#e7a3a0",
    spur: "#c7cdd6", panel: "#ffffff", bg: C("bg", "#f7f8fb"), violet: "#7c5bfa",
  };
  const FAMILY = {
    memory: ["#e8ecf6", "#1e2761"], station: ["#e2f1ee", "#0f766e"],
    reservoir: ["#ecebfb", "#4338ca"], junction: ["#fbf1d9", "#9a6a12"],
    factory: ["#fdecea", "#b42318"],
    tile: ["#ffffff", "#98a2b3"], pair: ["#ffffff", "#98a2b3"], row: ["#ffffff", "#98a2b3"],
    processor: ["#ffffff", "#98a2b3"], channel: ["#ffffff", "#98a2b3"],
  };
  const ROLE = { data: "#1e2761", ancilla: "#98a2b3", messenger: "#e4572e", magic: "#b42318",
                 fresh: "#a8a29e", any: "#0f766e" };
  // the fault-tolerant place categories (qccd/gadget/categories.py): colour AND silhouette
  const CATS = D.categories || {};
  const CAT_ORDER = D.categoryOrder || Object.keys(CATS);
  function styleOf(m) {
    const c = CATS[m.family];
    if (c) return { fill: c.fill, stroke: c.stroke, shape: c.shape, glyph: c.glyph, title: c.title, place: c.place, job: c.job };
    const fam = FAMILY[m.family] || FAMILY.tile;
    return { fill: fam[0], stroke: fam[1], shape: m.kind === "composite" ? "dashed" : "round", glyph: "",
             title: m.kind === "leaf" ? m.family + " leaf" : m.family };
  }
  // A silhouette as an SVG path: canvas draws it with Path2D, the legend and the library as
  // inline SVG, so a category looks the same everywhere.
  function shapeD(shape, x, y, w, h) {
    const f = (v) => +v.toFixed(2);
    const m = Math.min(w, h);
    switch (shape) {
      case "octagon": {
        const c = m * 0.3;
        return `M${f(x + c)} ${f(y)}H${f(x + w - c)}L${f(x + w)} ${f(y + c)}V${f(y + h - c)}L${f(x + w - c)} ${f(y + h)}H${f(x + c)}L${f(x)} ${f(y + h - c)}V${f(y + c)}Z`;
      }
      case "house": {
        const r = Math.min(h * 0.38, w * 0.32);
        return `M${f(x)} ${f(y + r)}L${f(x + w / 2)} ${f(y)}L${f(x + w)} ${f(y + r)}V${f(y + h)}H${f(x)}Z`;
      }
      case "stadium": {
        const r = m / 2;
        return `M${f(x + r)} ${f(y)}H${f(x + w - r)}A${f(r)} ${f(r)} 0 0 1 ${f(x + w - r)} ${f(y + h)}H${f(x + r)}A${f(r)} ${f(r)} 0 0 1 ${f(x + r)} ${f(y)}Z`;
      }
      case "hexagon": {
        const q = Math.min(w * 0.2, h * 0.5);
        return `M${f(x + q)} ${f(y)}H${f(x + w - q)}L${f(x + w)} ${f(y + h / 2)}L${f(x + w - q)} ${f(y + h)}H${f(x + q)}L${f(x)} ${f(y + h / 2)}Z`;
      }
      case "bridge": {
        const n = w > 2.2 * h ? 2 : 1;
        let d = `M${f(x)} ${f(y)}H${f(x + w)}V${f(y + h)}`;
        for (let i = n; i >= 1; i--) {
          const cx = x + w * i / (n + 1), aw = Math.min(w / (n + 1) * 0.62, h * 1.1), ah = Math.min(h * 0.42, aw / 2);
          d += `H${f(cx + aw / 2)}A${f(aw / 2)} ${f(ah)} 0 0 0 ${f(cx - aw / 2)} ${f(y + h)}`;
        }
        return d + `H${f(x)}Z`;
      }
      case "sawtooth": {
        const n = Math.max(2, Math.min(6, Math.round(w / Math.max(1e-6, h) * 2)));
        const tw = w / n, th = Math.min(h * 0.28, tw * 0.7);
        let d = `M${f(x)} ${f(y + h)}V${f(y + th)}`;
        for (let i = 0; i < n; i++) d += `L${f(x + (i + 1) * tw)} ${f(y)}V${f(y + th)}`;
        return d + `V${f(y + h)}Z`;
      }
      case "chevron": {
        const q = Math.min(w * 0.18, h * 0.5);
        return `M${f(x)} ${f(y)}H${f(x + w - q)}L${f(x + w)} ${f(y + h / 2)}L${f(x + w - q)} ${f(y + h)}H${f(x)}L${f(x + q)} ${f(y + h / 2)}Z`;
      }
      case "garage": {
        const q = Math.min(w * 0.14, h * 0.45);
        return `M${f(x + q)} ${f(y)}H${f(x + w - q)}L${f(x + w)} ${f(y + h)}H${f(x)}Z`;
      }
      case "circle": {
        const r = m / 2, cx = x + w / 2, cy = y + h / 2;
        return `M${f(cx - r)} ${f(cy)}A${f(r)} ${f(r)} 0 1 0 ${f(cx + r)} ${f(cy)}A${f(r)} ${f(r)} 0 1 0 ${f(cx - r)} ${f(cy)}Z`;
      }
      case "vault":
        return `M${f(x)} ${f(y)}H${f(x + w)}V${f(y + h)}H${f(x)}Z`;
      case "chip": {
        // a package with pins down both sides: the decoder
        const n = Math.max(2, Math.min(6, Math.floor(h / Math.max(1e-6, m * 0.3))));
        const pin = Math.min(w * 0.12, h / (2 * n + 1));
        let d = `M${f(x + pin)} ${f(y)}H${f(x + w - pin)}V${f(y + h)}H${f(x + pin)}Z`;
        for (let i = 0; i < n; i++) {
          const cy = y + h * (i + 0.5) / n;
          d += `M${f(x + pin)} ${f(cy - pin / 2)}H${f(x)}V${f(cy + pin / 2)}H${f(x + pin)}Z` +
               `M${f(x + w - pin)} ${f(cy - pin / 2)}H${f(x + w)}V${f(cy + pin / 2)}H${f(x + w - pin)}Z`;
        }
        return d;
      }
      case "drum": {
        // a cylinder on its side: the classical memory
        const r = Math.min(w * 0.16, h / 2);
        return `M${f(x + r)} ${f(y)}H${f(x + w - r)}A${f(r)} ${f(h / 2)} 0 0 1 ${f(x + w - r)} ${f(y + h)}` +
               `H${f(x + r)}A${f(r)} ${f(h / 2)} 0 0 1 ${f(x + r)} ${f(y)}Z` +
               `M${f(x + w - r)} ${f(y)}A${f(r)} ${f(h / 2)} 0 0 0 ${f(x + w - r)} ${f(y + h)}`;
      }
      case "wire": {
        const cy = y + h / 2;
        return `M${f(x)} ${f(cy - h * 0.12)}H${f(x + w)}V${f(cy + h * 0.12)}H${f(x)}Z`;
      }
      default: {
        const r = Math.min(8, w / 8, h / 8);
        return `M${f(x + r)} ${f(y)}H${f(x + w - r)}Q${f(x + w)} ${f(y)} ${f(x + w)} ${f(y + r)}V${f(y + h - r)}Q${f(x + w)} ${f(y + h)} ${f(x + w - r)} ${f(y + h)}H${f(x + r)}Q${f(x)} ${f(y + h)} ${f(x)} ${f(y + h - r)}V${f(y + r)}Q${f(x)} ${f(y)} ${f(x + r)} ${f(y)}Z`;
      }
    }
  }
  function shapeDetail(shape, x, y, w, h) {
    const f = (v) => +v.toFixed(2);
    if (shape === "vault") {          // the second line a vault has: its inner wall
      const i = Math.min(w, h) * 0.09;
      return `M${f(x + i)} ${f(y + i)}H${f(x + w - i)}V${f(y + h - i)}H${f(x + i)}Z`;
    }
    if (shape === "drum") {           // the rows of cells inside
      let d = "";
      for (const k of [1, 2]) { const yy = y + h * k / 3; d += `M${f(x + w * 0.2)} ${f(yy)}H${f(x + w * 0.86)}`; }
      return d;
    }
    if (shape === "chip") {           // the die inside the package
      const ix = w * 0.3, iy = h * 0.28;
      return `M${f(x + ix)} ${f(y + iy)}H${f(x + w - ix)}V${f(y + h - iy)}H${f(x + ix)}Z`;
    }
    return "";
  }
  function shapeSvg(cat, size) {
    const c = CATS[cat];
    if (!c) return "";
    const w = size * 1.5, h = size;
    const d = shapeD(c.shape, 1, 1, w - 2, h - 2), dd = shapeDetail(c.shape, 1, 1, w - 2, h - 2);
    return `<svg class="shp" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" fill="${c.fill}" stroke="${c.stroke}" stroke-width="1.4"/>${dd ? `<path d="${dd}" fill="none" stroke="${c.stroke}" stroke-width="1"/>` : ""}</svg>`;
  }
  const OP_CAT = [["prep.", "prep"], ["se.", "se"], ["check_", "zone"], ["zz", "surgery"], ["xx", "surgery"],
                  ["read.", "readout"], ["inject", "injection"], ["produce", "factory"], ["load.", "depot"],
                  ["dump.", "depot"], ["pass.", "road"], ["decode", "decoder"], ["record", "archive"],
                  ["update", "archive"], ["resolve", "archive"]];
  function opColor(op) {
    for (const [prefix, cat] of OP_CAT) if (op.startsWith(prefix) && CATS[cat]) return CATS[cat].stroke;
    if (op === "cx" && CATS.operation) return CATS.operation.stroke;
    if (op === "cycle") return "#cfd6e2";
    if (op === "emit" || op === "absorb") return COLOR.navy;
    if (op === "cx" || op === "xc") return COLOR.teal;
    if (op.startsWith("visit")) return COLOR.accent;
    if (op.startsWith("release") || op.startsWith("accept")) return COLOR.indigo;
    if (op === "pass") return COLOR.gold;
    if (op === "layer1q") return COLOR.violet;
    if (op === "produce" || op === "consume") return "#b42318";
    return COLOR.ink;
  }

  // ------------------------------------------------------------------ the classical half
  //
  // Wires are channels of kind "wire": no ions travel on them, so they are drawn dashed and
  // dark, with one square packet per message in flight.  The archive's contents at time t are
  // the frame rows written up to t (`gir.frames`), which is what "kept in software" looks like.

  const WIRES = M.gir.wires || {};
  const MSGS = M.gir.messages || [];
  const FRAMES = M.gir.frames || [];
  const CONTROL = M.gir.control || {};
  const msgByWire = {};
  for (const m of MSGS) (msgByWire[m[1]] || (msgByWire[m[1]] = [])).push(m);
  for (const k in msgByWire) msgByWire[k].sort((a, b) => a[6] - b[6]);
  function messagesOn(wire, t) {
    const ms = msgByWire[wire];
    if (!ms) return [];
    return ms.filter((m) => m[6] <= t && t <= m[7]);
  }
  function archiveAt(t) {
    // the last write per (block, kind), and every outcome, up to t
    const frames = {}, outcomes = [];
    let windows = 0;
    for (const f of FRAMES) {
      if (f.t > t) break;
      if (f.kind === "window") { windows++; continue; }
      if (f.kind === "outcome") outcomes.push(f);
      else frames[f.block] = f;
    }
    return { frames, outcomes, windows };
  }
  function exprText(e, blockNames) {
    if (!e) return "0";
    const parts = [];
    if (e.const) parts.push("1");
    for (const [eid, flow] of e.sources || []) parts.push(`m${eid}`);
    return parts.length ? parts.join(" ⊕ ") : "0";
  }
  // event row element 10: a classically controlled op (`{guard, cond, ready_us, branches}`)
  function guardOf(e) { return (e && e.length > 10 && e[10]) || null; }
  function guardText(e) {
    const g = guardOf(e);
    return g ? "if " + (g.guard || "?") : "";
  }

  function frameText(f) {
    const ax = exprText(f.ax), az = exprText(f.az);
    if (ax === "0" && az === "0") return "no correction";
    return `X̄^(${ax}) Z̄^(${az})`;
  }

  // ------------------------------------------------------------------ the studio's shapes
  //
  // A trapping site, a junction, a rail and an ion are drawn here by the SAME numbers the
  // studio draws them by (qccd/viz/theme.py's PALETTE and GEOMETRY, qccd/viz/layout.py's
  // fractions of `g`, and the shapes in qccd/viz/render.py::buildStatic).  Both tables are
  // shipped into this page, so the two tools cannot drift apart: a site is a capsule
  // rotated onto its trap axis with one ring per slot, a junction is a sharp white square,
  // a rail is a butt-capped line coloured by its role, and an ion is a white-outlined disc
  // whose colour says what the machine is doing to it this instant.
  //
  // `g` is the one number everything is a fraction of: the nearest-neighbour distance of
  // the device, in the units it is drawn at.  The studio measures it in px after its own
  // fit; here it is measured in device units per master and multiplied by the camera's
  // scale at draw time, which comes to the same thing.
  const PAL = D.palette || {};
  const GEO = D.geometry || {};
  const P = (k, fallback) => PAL[k] || fallback;
  const RAIL_W_FRAC = GEO.RAIL_W_FRAC || 0.083, RUNG_W_FRAC = GEO.RUNG_W_FRAC || 0.065;
  const K_ION = (GEO.ION_D_FRAC_ACTIVE || 0.175) + 0.065, K_REST = GEO.ION_D_FRAC || 0.13;
  // theme.py::SEGMENT_ROLE, first match wins
  const SEG_ROLE = [["highway", "highway"], ["onramp", "highway"], ["rung", "compute"],
                    ["compute", "compute"], ["spur", "compute"], ["coupling", "compute"],
                    ["rail", "rail"]];
  function segRole(sg) {
    const labels = sg.labels || [];
    for (const [needle, role] of SEG_ROLE) if (labels.indexOf(needle) >= 0) return role;
    return "rail";
  }
  function zoneColour(z) { return P("zone_" + (z || "other"), P("zone_other", "#98a2b3")); }

  // Per master: the lattice step `gd`, and each node's trap axis -- the incident arm that
  // maximises the alignment with the others (render.py's AXIS), which is what a site's
  // capsule and its slot rings are rotated onto.
  const geomCache = {};
  function deviceGeom(mname) {
    if (geomCache[mname]) return geomCache[mname];
    const d = M.leafData[mname];
    if (!d) return null;
    const pos = {}, arms = {};
    for (const n of d.device.nodes) { pos[n.id] = n.pos; arms[n.id] = []; }
    let gd = Infinity;
    for (const sg of d.device.segments) {
      const a = pos[sg.ends[0]], b = pos[sg.ends[1]];
      if (!a || !b) continue;
      const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.sqrt(dx * dx + dy * dy);
      if (len > 1e-9) {
        gd = Math.min(gd, len);
        arms[sg.ends[0]].push([dx / len, dy / len]);
        arms[sg.ends[1]].push([-dx / len, -dy / len]);
      }
    }
    if (!isFinite(gd)) {                       // no segments: fall back to node spacing
      const ns = d.device.nodes;
      for (let i = 0; i < ns.length; i++) for (let j = i + 1; j < ns.length; j++) {
        const dx = ns[j].pos[0] - ns[i].pos[0], dy = ns[j].pos[1] - ns[i].pos[1];
        const l = Math.sqrt(dx * dx + dy * dy);
        if (l > 1e-9) gd = Math.min(gd, l);
      }
    }
    if (!isFinite(gd) || gd <= 0) gd = 1;
    const axis = {};
    for (const n of d.device.nodes) {
      const list = (arms[n.id] || []).map(([ux, uy]) => (ux < -1e-9 || (Math.abs(ux) <= 1e-9 && uy < 0))
        ? [-ux, -uy] : [ux, uy]);            // sign-normalised to the right/up half-plane
      let best = [1, 0], score = -1;
      for (const cand of list) {
        let s = 0;
        for (const other of list) s += Math.abs(cand[0] * other[0] + cand[1] * other[1]);
        if (s > score) { score = s; best = cand; }
      }
      axis[n.id] = best;
    }
    return (geomCache[mname] = { gd, axis, pos });
  }

  // the studio's per-`g` marks, in px for the current camera
  function marks(g) {
    const rIon = Math.min(26, Math.max(Math.min(3, 0.45 * g), K_ION * g));
    return {
      g,
      siteT: RAIL_W_FRAC * 2.4 * g,
      siteMax: 0.88 * g,
      swNode: Math.max(1.0, 0.05 * g),
      swRail: Math.max(1.2, RAIL_W_FRAC * g),
      swThin: Math.max(1.0, RUNG_W_FRAC * g),
      swHalo: Math.max(0.7, 0.055 * g),
      slotR: 0.085 * g,
      rJunc: 0.30 * g,
      rIon,
      rRest: Math.min(rIon, Math.max(Math.min(1.6, 0.30 * g), K_REST * g)),
      rActive: 0.46 * g,
      wellRx: 0.44 * g, wellRy: 0.30 * g,
    };
  }
  function siteLen(cap, g) {                   // layout.py::_site_len
    const m = Math.max(1, Math.min(cap || 1, 6));
    return Math.min(0.88 * g, (0.30 + 0.15 * m) * g);
  }
  function slots(cap) { return Math.max(1, Math.min(cap || 1, 6)); }

  // one capsule + its slot rings, rotated onto the trap axis
  function drawSite(ctx, n, sx, sy, mk, ax, state) {
    const cap = n.capacity === undefined ? 1 : n.capacity;
    const len = siteLen(cap, mk.g), t = mk.siteT, zc = zoneColour(n.zone_type);
    const dock = (n.degree || 0) >= 3, corner = !!n.corner;
    const big = dock || corner || state === "active";
    ctx.save();
    ctx.translate(sx, sy);
    ctx.rotate(Math.atan2(ax[1], ax[0]));
    ctx.beginPath();
    const r = Math.min(t / 2, len / 2);
    ctx.moveTo(-len / 2 + r, -t / 2);
    ctx.lineTo(len / 2 - r, -t / 2);
    ctx.arc(len / 2 - r, 0, r, -Math.PI / 2, Math.PI / 2);
    ctx.lineTo(-len / 2 + r, t / 2);
    ctx.arc(-len / 2 + r, 0, r, Math.PI / 2, -Math.PI / 2);
    ctx.closePath();
    ctx.globalAlpha = 0.16;
    ctx.fillStyle = zc;
    ctx.fill();
    ctx.globalAlpha = (big || state === "over") ? 0.95 : 0.55;
    ctx.strokeStyle = state === "over" ? P("z", "#b42318")
      : state === "active" ? P("active", "#f6c34a")
        : dock ? P("gold", "#e8b84b") : corner ? P("corner", "#f6c34a") : zc;
    ctx.lineWidth = (state === "over" ? 2.4 : 1) * (big ? mk.swNode * 1.7 : mk.swNode);
    ctx.stroke();
    const m = slots(cap), sr = Math.min(mk.slotR, 0.36 * len / m);
    if (sr > 0.6) {
      ctx.globalAlpha = 0.55;
      ctx.strokeStyle = zc;
      ctx.lineWidth = Math.max(0.7, mk.swNode * 0.8);
      for (let i = 0; i < m; i++) {
        ctx.beginPath();
        ctx.arc(((i + 0.5) / m - 0.5) * len, 0, sr, 0, 2 * Math.PI);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function drawJunction(ctx, n, sx, sy, mk) {
    const h = mk.rJunc;
    ctx.fillStyle = P("panel", "#ffffff");
    ctx.strokeStyle = n.corner ? P("corner", "#f6c34a") : P("grid", "#8a94a6");
    ctx.lineWidth = mk.swNode;
    ctx.beginPath();
    ctx.rect(sx - h, sy - h, 2 * h, 2 * h);
    ctx.fill();
    ctx.stroke();
  }

  // ------------------------------------------------------------------ state

  const S = {
    path: "", t: 0, playing: false, rate: 10000, sel: null, follow: null,
    showIons: true, dirty: true, lastProg: -1,
    cam: { s: 1, x: 0, y: 0 }, tl: { t0: 0, t1: Math.max(1, M.makespan) },
  };
  const sims = {};
  window.__gadgetLeaf = function (d) {
    M.leafData[d.master] = d;
    sims[d.master] = new LeafSim(d);
    portCache.clear();
    S.dirty = true;
  };
  for (const name in D.leafFiles || {}) {
    if (D.leafInline && D.leafInline[name]) { window.__gadgetLeaf(D.leafInline[name]); continue; }
    const s = document.createElement("script");
    s.src = D.leafFiles[name];
    s.async = true;
    document.head.appendChild(s);
  }

  // ------------------------------------------------------------------ geometry

  const portCache = new Map();
  // Where port `port` of master `mname` sits in the master's own frame.
  function portXY(mname, port) {
    const key = mname + "|" + port;
    if (portCache.has(key)) return portCache.get(key);
    const m = M.masters[mname];
    let xy = null;
    const p = (m.ports || []).find((q) => q.name === port);
    if (m.kind === "leaf") {
      const d = M.leafData[mname];
      if (d && p && p.node) {
        const n = d.device.nodes.find((q) => q.id === p.node);
        if (n) xy = n.pos.slice();
      }
      if (!xy) xy = channelEndpoint(mname, port);
    } else if (p && p.bind) {
      const [iname, cport] = p.bind.split(".");
      const inst = m.instances.find((i) => i.name === iname);
      const c = inst && portXY(inst.master, cport);
      if (c) xy = [c[0] + inst.x, c[1] + inst.y];
    }
    if (!xy && m.bbox && m.bbox.length) xy = sideXY(m, p);
    portCache.set(key, xy);
    return xy;
  }
  function channelEndpoint(mname, port) {
    for (const cname in M.masters) {
      const cm = M.masters[cname];
      if (cm.kind !== "composite") continue;
      for (const ch of cm.channels) {
        for (const [ref, pt] of [[ch.a, ch.points[0]], [ch.b, ch.points[ch.points.length - 1]]]) {
          const [iname, pname] = ref.split(".");
          if (pname !== port) continue;
          const inst = cm.instances.find((i) => i.name === iname);
          if (inst && inst.master === mname) return [pt[0] - inst.x, pt[1] - inst.y];
        }
      }
    }
    return null;
  }
  function sideXY(m, p) {
    const [x0, y0, x1, y1] = m.bbox;
    const at = p ? p.at : 0.5;
    switch (p ? p.side : "N") {
      case "E": return [x1, y0 + (y1 - y0) * at];
      case "S": return [x0 + (x1 - x0) * at, y1];
      case "W": return [x0, y0 + (y1 - y0) * at];
      default: return [x0 + (x1 - x0) * at, y0];
    }
  }

  // ------------------------------------------------------------------ canvas setup

  const stage = $("stage"), sctx = stage.getContext("2d");
  const tl = $("tl"), tctx = tl.getContext("2d");
  let DPR = window.devicePixelRatio || 1;
  function sizeCanvas(cv, ctx) {
    const r = cv.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width * DPR)), h = Math.max(1, Math.round(r.height * DPR));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    return [r.width, r.height];
  }

  function levelBox(path) {
    const node = M.node(path);
    const m = M.master(path);
    if (node.leaf) {
      const d = M.leafData[m.name];
      if (d) {
        const xs = d.device.nodes.map((n) => n.pos[0]), ys = d.device.nodes.map((n) => n.pos[1]);
        return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
      }
    }
    return m.bbox && m.bbox.length ? m.bbox : [0, 0, 10, 10];
  }
  function fit() {
    const [w, h] = sizeCanvas(stage, sctx);
    const [x0, y0, x1, y1] = levelBox(S.path);
    const pad = 36;
    // the legend sits over the bottom of the stage, so the drawing is fitted above it
    const lg = $("legend");
    const below = 30 + (lg ? Math.min(140, lg.getBoundingClientRect().height + 10) : 0);
    const s = Math.min((w - 2 * pad) / Math.max(1e-6, x1 - x0),
                       (h - 2 * pad - below) / Math.max(1e-6, y1 - y0));
    S.cam.s = Math.max(0.05, s);
    S.cam.x = (w - (x1 - x0) * S.cam.s) / 2 - x0 * S.cam.s;
    S.cam.y = (h - below - (y1 - y0) * S.cam.s) / 2 - y0 * S.cam.s + 12;
    S.dirty = true;
  }
  const X = (x) => x * S.cam.s + S.cam.x;
  const Y = (y) => y * S.cam.s + S.cam.y;

  // ------------------------------------------------------------------ stage drawing

  const hits = [];     // [x0, y0, x1, y1, kind, payload] in screen px, topmost last
  const batch = {};    // colour -> [x, y, r] ion marks, flushed once per frame
  function mark(color, x, y, r) { (batch[color] || (batch[color] = [])).push(x, y, r); }
  function flushMarks(ctx) {
    for (const color in batch) {
      const a = batch[color];
      if (!a.length) continue;
      ctx.fillStyle = color;
      ctx.beginPath();
      for (let i = 0; i < a.length; i += 3) {
        const r = a[i + 2];
        if (r < 1.6) ctx.rect(a[i] - r, a[i + 1] - r, 2 * r, 2 * r);
        else { ctx.moveTo(a[i] + r, a[i + 1]); ctx.arc(a[i], a[i + 1], r, 0, 2 * Math.PI); }
      }
      ctx.fill();
      a.length = 0;
    }
  }

  function drawStage() {
    const [w, h] = sizeCanvas(stage, sctx);
    const ctx = sctx;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = COLOR.bg;
    ctx.fillRect(0, 0, w, h);
    hits.length = 0;
    const node = M.node(S.path);
    if (node.leaf) drawLeafLevel(ctx, w, h);
    else drawCompositeLevel(ctx, w, h);
    flushMarks(ctx);
    drawOverlays(ctx);
    updateHud();
  }

  function rectOf(path, ox, oy) {
    const m = M.master(path);
    const b = m.bbox && m.bbox.length ? m.bbox : [0, 0, 1, 1];
    const pad = m.kind === "leaf" ? 0.6 : 1.4;
    // a place is drawn at least five units tall, so its silhouette reads as its category's
    const padY = m.kind === "leaf" && CATS[m.family] ? Math.max(pad + 0.4, (5 - (b[3] - b[1])) / 2) : pad;
    let padX = pad;
    if (CATS[m.family] && CATS[m.family].shape === "stadium") {
      // a pill, however square the device
      padX = Math.max(pad, ((b[3] - b[1] + 2 * padY) * 1.7 - (b[2] - b[0])) / 2);
    }
    return [X(ox + b[0] - padX), Y(oy + b[1] - padY), X(ox + b[2] + padX), Y(oy + b[3] + padY)];
  }

  const labels = [];
  function haloText(ctx, text, x, y, color) {
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(255,255,255,.92)";
    ctx.lineJoin = "round";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  function drawCompositeLevel(ctx, w, h) {
    labels.length = 0;
    drawComposite(ctx, S.path, 0, 0, 0, w, h);
    // ports of the current level's children, on top
    const m = M.master(S.path);
    for (const inst of m.instances) {
      const cm = M.masters[inst.master];
      if (cm.family === "road") continue;
      for (const p of cm.ports || []) {
        const xy = portXY(cm.name, p.name);
        if (!xy) continue;
        const px = X(inst.x + xy[0]), py = Y(inst.y + xy[1]);
        const r = Math.max(2.5, Math.min(6, S.cam.s * 0.9));
        ctx.fillStyle = COLOR.panel;
        ctx.strokeStyle = ROLE[p.role] || COLOR.muted;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.arc(px, py, r, 0, 2 * Math.PI);
        ctx.fill(); ctx.stroke();
        hits.push([px - r - 2, py - r - 2, px + r + 2, py + r + 2, "port",
                   { path: childPath(S.path, inst.name), port: p }]);
      }
    }
    flushMarks(ctx);
    for (const draw of labels) draw();
  }

  function childPath(parent, name) { return parent ? parent + "." + name : name; }

  function drawComposite(ctx, path, ox, oy, depth, w, h) {
    const m = M.master(path);
    const t = S.t;
    for (const inst of m.instances) {
      const cpath = childPath(path, inst.name);
      const cm = M.masters[inst.master];
      const cx = ox + inst.x, cy = oy + inst.y;
      const r = rectOf(cpath, cx, cy);
      const rw = r[2] - r[0], rh = r[3] - r[1];
      if (r[2] < 0 || r[0] > w || r[3] < 0 || r[1] > h || rw < 2) continue;
      const sty = styleOf(cm);
      const busy = cm.kind === "leaf" ? M.eventAt(cpath, t) : null;
      if (cm.family === "road") {
        // a junction is a point on a street: a dot, lit while it passes a bundle
        const b = cm.bbox && cm.bbox.length ? cm.bbox : [0, 0, 0, 0];
        const jx = X(cx + (b[0] + b[2]) / 2), jy = Y(cy + (b[1] + b[3]) / 2);
        const jr = Math.max(2.5, Math.min(7, S.cam.s * 0.9));
        ctx.fillStyle = busy ? opColor(busy[4]) : "#ffffff";
        ctx.strokeStyle = busy ? opColor(busy[4]) : sty.stroke;
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.arc(jx, jy, jr, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        if (depth === 0) hits.push([jx - jr - 2, jy - jr - 2, jx + jr + 2, jy + jr + 2, "inst", { path: cpath }]);
        continue;
      }
      ctx.globalAlpha = depth === 0 ? 1 : depth === 1 ? 0.9 : 0.75;
      ctx.fillStyle = sty.fill;
      ctx.strokeStyle = sty.stroke;
      ctx.lineWidth = depth === 0 ? 1.6 : 1;
      let outline;
      if (cm.kind === "composite") {
        ctx.setLineDash(depth === 0 ? [5, 3] : [3, 3]);
        outline = new Path2D(shapeD("round", r[0], r[1], rw, rh));
      } else {
        outline = new Path2D(shapeD(sty.shape, r[0], r[1], rw, rh));
      }
      ctx.fill(outline);
      ctx.stroke(outline);
      ctx.setLineDash([]);
      const detail = cm.kind === "leaf" ? shapeDetail(sty.shape, r[0], r[1], rw, rh) : "";
      if (detail) { ctx.lineWidth = 1; ctx.stroke(new Path2D(detail)); }
      if (busy && busy[4] !== "cycle") {
        ctx.strokeStyle = opColor(busy[4]);
        ctx.lineWidth = depth === 0 ? 3.2 : 2;
        // a classically controlled op is outlined dashed: the place is reserved, and
        // whether it fires depends on a bit that arrived on the decision wire
        if (guardOf(busy)) ctx.setLineDash([7, 4]);
        ctx.stroke(outline);
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;
      if (depth === 0) hits.push([r[0], r[1], r[2], r[3], "inst", { path: cpath }]);
      if (cm.kind === "leaf") {
        if (Math.min(rw, rh) > 14 || rw > 60) drawLeafInterior(ctx, cpath, cx, cy, depth, rw);
      } else if (depth < 3 && rw > 50) {
        drawComposite(ctx, cpath, cx, cy, depth + 1, w, h);
      }
      if (depth === 0) labels.push(() => drawBoxLabel(ctx, cpath, cm, r, busy));
      else if (depth === 1 && rw > 110 && rh > 36 && cm.kind === "composite") {
        // a nested composite labels its bottom-left corner, clear of its parent's title
        labels.push(() => {
          ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
          const text = inst.name + (blockLabel(cpath) ? "  " + blockLabel(cpath) : "");
          haloText(ctx, clip(ctx, text, rw - 12), r[0] + 6, r[3] - 5, COLOR.muted);
        });
      }
    }
    drawChannels(ctx, path, ox, oy, depth);
  }

  const blockLabels = {};
  function blockLabel(path) {
    if (!(path in blockLabels)) {
      const blocks = M.leavesUnder(path).map((l) => M.blockOfLeaf[l]).filter(Boolean).sort(naturalCmp);
      blockLabels[path] = blocks.length > 6 ? `${blocks[0]} … ${blocks[blocks.length - 1]}` : blocks.join(" ");
    }
    return blockLabels[path];
  }

  function drawBoxLabel(ctx, path, cm, r, busy) {
    const rw = r[2] - r[0];
    if (rw < 36 || (cm.family === "road" && rw < 90)) return;
    const sty = styleOf(cm);
    if (sty.glyph && r[3] - r[1] > 34 && rw > 70) {
      ctx.font = "700 10px ui-sans-serif, system-ui, sans-serif";
      const gw = ctx.measureText(sty.glyph).width;
      const inset = (sty.shape === "hexagon" || sty.shape === "chevron") ? Math.min(rw * 0.2, (r[3] - r[1]) * 0.5) : 0;
      haloText(ctx, sty.glyph, r[2] - gw - 16 - inset, r[3] - 8, sty.stroke);
    }
    const node = M.node(path);
    ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
    const blk = blockLabel(path);
    const title = node.name + (blk ? " · " + blk : "");
    if (rw < 96 && CATS[cm.family]) {
      // a small place is named under its outline, where the name fits
      const tw = ctx.measureText(title).width;
      haloText(ctx, title, (r[0] + r[2]) / 2 - tw / 2, r[3] + 14, COLOR.navy);
      return;
    }
    haloText(ctx, clip(ctx, title, rw - 24), r[0] + 16, r[1] + 15, COLOR.navy);
    if (rw > 120) {
      ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
      ctx.fillStyle = COLOR.muted;
      let sub = cm.title || cm.name;
      if (cm.kind === "composite") {
        const active = activeOpsUnder(path);
        if (active) sub = active;
      } else if (busy) {
        const lt = M.localTime(busy, S.t);
        sub = (guardOf(busy) ? guardText(busy) + " · " : "") + busy[4] +
          (busy[5] > 1 ? `  ${lt.iteration + 1}/${busy[5]}` : "") +
          `  ${fmtTime(lt.local)} / ${fmtTime(lt.duration)}`;
      }
      haloText(ctx, clip(ctx, sub, rw - 24), r[0] + 16, r[1] + 29, COLOR.muted);
      if (busy && cm.kind === "leaf") {
        const lt = M.localTime(busy, S.t);
        const pw = Math.min(rw - 12, 120);
        ctx.fillStyle = COLOR.soft;
        ctx.fillRect(r[0] + 6, r[1] + 34, pw, 3);
        ctx.fillStyle = opColor(busy[4]);
        ctx.fillRect(r[0] + 6, r[1] + 34, pw * lt.local / Math.max(1, lt.duration), 3);
      }
    }
    // inventory gauge on the right edge
    const cap = cm.capacity || 0;
    if (cap && r[3] - r[1] > 30) {
      const inv = cm.kind === "leaf" ? M.inventory(path, S.t) : M.inventoryUnder(path, S.t);
      const gh = Math.min(60, r[3] - r[1] - 16);
      const gx = r[2] - 9, gy = r[1] + 8;
      ctx.fillStyle = COLOR.soft;
      ctx.fillRect(gx, gy, 4, gh);
      ctx.fillStyle = inv > cap ? "#b42318" : COLOR.teal;
      const f = Math.min(1, inv / cap);
      ctx.fillRect(gx, gy + gh * (1 - f), 4, gh * f);
    }
  }

  function activeOpsUnder(path) {
    const counts = {};
    let n = 0;
    for (const leaf of M.leavesUnder(path)) {
      const e = M.eventAt(leaf, S.t);
      if (!e) continue;
      const key = e[4].replace(/\.[XYZ]?\d*$/, "");
      counts[key] = (counts[key] || 0) + 1;
      n++;
    }
    if (!n) return "idle";
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([k, v]) => (v > 1 ? `${k} ×${v}` : k)).join(" · ");
  }

  function drawChannels(ctx, path, ox, oy, depth) {
    const m = M.master(path);
    for (const ch of m.channels || []) {
      const pts = ch.points;
      if (!pts || pts.length < 2) continue;
      if (ch.kind === "wire") { drawWire(ctx, path, ch, pts, ox, oy, depth); continue; }
      ctx.strokeStyle = depth === 0 ? COLOR.rail : "#efc9c7";
      ctx.lineWidth = depth === 0 ? Math.max(1.5, Math.min(5, S.cam.s * 0.35)) : 1;
      ctx.lineJoin = "round";
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(X(ox + p[0]), Y(oy + p[1])) : ctx.moveTo(X(ox + p[0]), Y(oy + p[1]))));
      ctx.stroke();
      const net = path + "/" + ch.name;
      if (depth === 0) {
        const a = pts[0], b = pts[pts.length - 1];
        hits.push([Math.min(X(ox + a[0]), X(ox + b[0])) - 4, Math.min(Y(oy + a[1]), Y(oy + b[1])) - 4,
                   Math.max(X(ox + a[0]), X(ox + b[0])) + 4, Math.max(Y(oy + a[1]), Y(oy + b[1])) + 4,
                   "chan", { net, ch }]);
      }
      const carries = M.carriesOn(net, S.t);
      if (!carries.length) continue;
      const rec = M.gir.nets[net];
      const lengths = [];
      let total = 0;
      for (let i = 1; i < pts.length; i++) {
        const d = Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]);
        lengths.push(d);
        total += d;
      }
      for (const c of carries) {
        const forward = c[2] === rec[0];
        const radius = depth === 0 ? Math.max(2.2, Math.min(5, S.cam.s * 0.32)) : Math.max(1, Math.min(3, S.cam.s * 0.25));
        for (const ion of M.carryIons(c, S.t, rec[4])) {
          let f = forward ? ion.f : 1 - ion.f;
          let d = f * total, k = 0;
          while (k < lengths.length - 1 && d > lengths[k]) { d -= lengths[k]; k++; }
          const p0 = pts[k], p1 = pts[k + 1];
          const u = lengths[k] > 0 ? Math.min(1, d / lengths[k]) : 0;
          const x = X(ox + p0[0] + (p1[0] - p0[0]) * u), y = Y(oy + p0[1] + (p1[1] - p0[1]) * u);
          const col = S.follow === ion.gid ? "#000000" : (ROLE[c[8]] || COLOR.teal);
          mark(col, x, y, S.follow === ion.gid ? radius * 1.8 : radius);
          if (depth <= 1) hits.push([x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2, "ion", { gid: ion.gid, carry: c }]);
        }
      }
    }
  }

  // A classical wire: dashed and dark, with a square packet per message crossing it now.
  function drawWire(ctx, path, ch, pts, ox, oy, depth) {
    const style = CATS.wire || { stroke: "#065f46" };
    const net = path + "/" + ch.name;
    ctx.save();
    ctx.strokeStyle = style.stroke;
    ctx.globalAlpha = depth === 0 ? 0.75 : 0.5;
    ctx.lineWidth = depth === 0 ? Math.max(1, Math.min(2.2, S.cam.s * 0.14)) : 0.8;
    ctx.setLineDash([Math.max(3, S.cam.s * 0.5), Math.max(2, S.cam.s * 0.35)]);
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(X(ox + p[0]), Y(oy + p[1])) : ctx.moveTo(X(ox + p[0]), Y(oy + p[1]))));
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
    if (depth === 0) {
      const xs = pts.map((p) => X(ox + p[0])), ys = pts.map((p) => Y(oy + p[1]));
      hits.push([Math.min(...xs) - 4, Math.min(...ys) - 4, Math.max(...xs) + 4, Math.max(...ys) + 4,
                 "wire", { net, ch }]);
    }
    const live = messagesOn(net, S.t);
    if (!live.length) return;
    const lengths = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) {
      const d = Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]);
      lengths.push(d); total += d;
    }
    const forward = ch.a.split(".")[0] === (M.gir.wires[net] ? M.gir.wires[net][0] : "");
    for (const msg of live) {
      const span = Math.max(1e-6, msg[7] - msg[6]);
      let f = (S.t - msg[6]) / span;
      const fromA = msg[2] === ch.a.split(".")[0];
      if (!fromA) f = 1 - f;
      let d = f * total, k = 0;
      while (k < lengths.length - 1 && d > lengths[k]) { d -= lengths[k]; k++; }
      const p0 = pts[k], p1 = pts[k + 1];
      const u = lengths[k] > 0 ? Math.min(1, d / lengths[k]) : 0;
      const x = X(ox + p0[0] + (p1[0] - p0[0]) * u), y = Y(oy + p0[1] + (p1[1] - p0[1]) * u);
      const r = Math.max(2, Math.min(5, S.cam.s * 0.3));
      ctx.fillStyle = style.stroke;
      ctx.fillRect(x - r, y - r * 0.7, 2 * r, 1.4 * r);       // a packet, not an ion
      hits.push([x - r - 2, y - r - 2, x + r + 2, y + r + 2, "msg", { msg, net, ch }]);
    }
  }

  // The ions of one leaf instance at the global time, in the leaf's own device frame.
  function leafIons(path) {
    const m = M.master(path);
    const d = M.leafData[m.name];
    if (!d) return null;
    const sim = sims[m.name];
    const e = M.eventAt(path, S.t);
    const binds = {};
    if (e && e[8]) for (const pr of e[8]) binds[pr[0]] = pr[1];
    const first = M.gir.leaves[path][3];
    const order = M.gir.ion_order[m.name];
    const localId = {};
    order.forEach((n, i) => { localId[n] = first + i; });
    if (e && d.programs[e[4]]) {
      const lt = M.localTime(e, S.t);
      const st = sim.state(e[4], lt.local);
      const stubs = stubSet(d);
      const out = [];
      st.ions.forEach((name, i) => {
        const moving = st.step.moves.some((mv) => mv[0] === i);
        const nodeId = d.device.nodes[st.step.before[i]].id;
        if (!moving && stubs.has(nodeId)) return;
        const gid = binds[name] !== undefined ? binds[name] : localId[name];
        out.push({ name, gid, xy: st.xy[i], role: roleOf(d, m, name), active: st.active.includes(i) });
      });
      return { ions: out, step: st, event: e, local: lt };
    }
    const out = [];
    for (const name of order) {
      const node = d.device.nodes.find((q) => q.id === d.home[name]);
      if (node) out.push({ name, gid: localId[name], xy: node.pos, role: roleOf(d, m, name), active: false });
    }
    return { ions: out, step: null, event: e, local: null };
  }
  const stubSets = new WeakMap();
  function stubSet(d) {
    if (!stubSets.has(d)) stubSets.set(d, new Set(d.device.nodes.filter((n) => (n.labels || []).includes("port_stub")).map((n) => n.id)));
    return stubSets.get(d);
  }
  function roleOf(d, m, name) {
    if (d.roles[name]) return d.roles[name];
    if (m.family === "station" || CATS[m.family]) return "data";
    return "messenger";
  }

  function spread(ions) {
    // ions sharing a position (a docked pair, two in one trap) sit side by side
    const seen = new Map();
    for (const ion of ions) {
      const key = ion.xy[0].toFixed(3) + "," + ion.xy[1].toFixed(3);
      const k = seen.get(key) || 0;
      seen.set(key, k + 1);
      ion.dx = k ? (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.32 : 0;
    }
  }

  // The device, in the studio's own z-order: rails, the halo under a site in play, the
  // nodes, the potential well under a flying ion, then the ions and their labels.  Every
  // size is a fraction of `g` exactly as qccd/viz/layout.py computes it, so a trap here
  // and a trap on studio.html are the same object drawn twice.
  function drawDevice(ctx, path, ox, oy, depth) {
    const m = M.master(path);
    const d = M.leafData[m.name];
    const geom = deviceGeom(m.name);
    if (!d || !geom) return;
    const g = geom.gd * S.cam.s;
    const mk = marks(g);
    const pos = geom.pos;
    const tiny = g < 4;                  // too small for the real shapes: keep it cheap
    // -- rails, coloured by role
    const byRole = {};
    for (const sg of d.device.segments) {
      const role = segRole(sg);
      (byRole[role] || (byRole[role] = [])).push(sg);
    }
    for (const role in byRole) {
      const thin = role === "compute";
      ctx.strokeStyle = P(role, P("rail", "#f2b8b5"));
      ctx.lineWidth = thin ? mk.swThin : mk.swRail;
      ctx.lineCap = "butt";
      ctx.globalAlpha = thin ? 0.9 : 1;
      ctx.beginPath();
      for (const sg of byRole[role]) {
        const a = pos[sg.ends[0]], b = pos[sg.ends[1]];
        if (!a || !b) continue;
        ctx.moveTo(X(ox + a[0]), Y(oy + a[1]));
        ctx.lineTo(X(ox + b[0]), Y(oy + b[1]));
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (!S.showIons && depth > 0) return;
    const st = leafIons(path);
    const active = new Set();
    if (st && st.step) {
      for (const ion of st.ions) if (ion.active) active.add(ion.xy.join(","));
    }
    // -- the halo under a site that is in play this instant
    if (!tiny && active.size) {
      ctx.fillStyle = P("active", "#f6c34a");
      ctx.globalAlpha = 0.5;
      for (const n of d.device.nodes) {
        if (!active.has(n.pos.join(","))) continue;
        ctx.beginPath();
        ctx.arc(X(ox + n.pos[0]), Y(oy + n.pos[1]), mk.rActive, 0, 2 * Math.PI);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    // -- the nodes
    const stubs = stubSet(d);
    for (const n of d.device.nodes) {
      const sx = X(ox + n.pos[0]), sy = Y(oy + n.pos[1]);
      if (tiny) {
        ctx.fillStyle = n.kind === "junction" ? P("grid", "#8a94a6") : zoneColour(n.zone_type);
        ctx.globalAlpha = 0.45;
        ctx.fillRect(sx - 1, sy - 1, 2, 2);
        ctx.globalAlpha = 1;
        continue;
      }
      if (n.kind === "junction" || (n.capacity || 0) === 0) {
        if (stubs.has(n.id)) {           // a testbench stub: the channel a parent attaches
          ctx.save();
          ctx.setLineDash([3, 2]);
          ctx.strokeStyle = COLOR.muted;
          ctx.lineWidth = mk.swNode;
          ctx.strokeRect(sx - mk.rJunc, sy - mk.rJunc, 2 * mk.rJunc, 2 * mk.rJunc);
          ctx.restore();
        } else {
          drawJunction(ctx, n, sx, sy, mk);
        }
        continue;
      }
      const state = active.has(n.pos.join(",")) ? "active" : "";
      drawSite(ctx, n, sx, sy, mk, geom.axis[n.id] || [1, 0], state);
    }
    if (!st) return;
    spread(st.ions);
    // -- the potential well under an ion that is moving (`leafIons` returns the sim
    // state, whose own `.step` is the instruction record with the moves in it)
    const rec = st.step ? st.step.step : null;
    const moving = rec ? rec.moves.length : 0;
    if (!tiny && moving && moving <= 40) {
      ctx.fillStyle = P("anc", "#4338ca");
      ctx.globalAlpha = 0.16;
      for (const [i2] of rec.moves) {
        const ion = st.ions[i2];
        if (!ion) continue;
        ctx.beginPath();
        ctx.ellipse(X(ox + ion.xy[0] + (ion.dx || 0)), Y(oy + ion.xy[1]),
                    mk.wellRx, mk.wellRy, 0, 0, 2 * Math.PI);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    // -- the ions: white-outlined discs, coloured by what is being done to them
    const kind = st.step ? st.step.step.ins.type : null;
    for (const ion of st.ions) {
      const x = X(ox + ion.xy[0] + (ion.dx || 0)), y = Y(oy + ion.xy[1]);
      const r = ion.active ? mk.rIon : mk.rRest;
      // the studio colours an ion by what the machine is doing to it, not by any role it
      // was given: a Z-check red, an X-check teal, everything else slate.  The gadget
      // layer has the instruction rather than the check, so a measurement or a reset
      // reads as Z and a gate as X -- the same two colours, from the same table.
      ctx.fillStyle = S.follow === ion.gid ? P("accent", "#e4572e")
        : !ion.active ? P("data", "#475467")
          : (kind === "measure" || kind === "reset") ? P("z", "#b42318") : P("x", "#0f766e");
      ctx.beginPath();
      ctx.arc(x, y, Math.max(0.6, r), 0, 2 * Math.PI);
      ctx.fill();
      if (r >= 1.6) {
        ctx.strokeStyle = P("ion_stroke", "#ffffff");
        ctx.lineWidth = mk.swHalo;
        ctx.stroke();
      }
      if (depth <= 1 && r >= 2) {
        hits.push([x - r - 1, y - r - 1, x + r + 1, y + r + 1, "ion",
                   { gid: ion.gid, leaf: path, name: ion.name }]);
      }
    }
    // -- the ion's own number, white on the disc, exactly when the studio shows it
    if (0.66 * mk.rIon >= 8) {
      ctx.fillStyle = P("ion_stroke", "#ffffff");
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const ion of st.ions) {
        const label = ion.name.replace(/^[da]/, "");
        const r = ion.active ? mk.rIon : mk.rRest;
        if (r < 6) continue;
        ctx.font = "700 " + Math.min(0.66 * r, 1.5 * r / Math.max(2, label.length)).toFixed(1) +
                   "px ui-sans-serif, system-ui, sans-serif";
        ctx.fillText(label, X(ox + ion.xy[0] + (ion.dx || 0)), Y(oy + ion.xy[1]));
      }
      ctx.textAlign = "start";
      ctx.textBaseline = "alphabetic";
    }
  }

  function drawLeafInterior(ctx, path, ox, oy, depth, pxWidth) {
    drawDevice(ctx, path, ox, oy, depth);
  }

  function drawLeafLevel(ctx, w, h) {
    const path = S.path;
    const m = M.master(path);
    const d = M.leafData[m.name];
    if (!d) {
      ctx.fillStyle = COLOR.muted;
      ctx.font = "14px ui-sans-serif, system-ui, sans-serif";
      ctx.fillText(`loading ${m.name} …`, 20, 60);
      return;
    }
    const s = S.cam.s;
    drawDevice(ctx, path, 0, 0, 0);
    // the node ids, once the zoom gives them room -- the studio keeps identity in its
    // tooltips, and this view is the one that exists to be read instruction by instruction
    const geom = deviceGeom(m.name);
    const mk = marks((geom ? geom.gd : 1) * s);
    for (const n of d.device.nodes) {
      const x = X(n.pos[0]), y = Y(n.pos[1]);
      const half = Math.max(2, mk.rJunc);
      hits.push([x - half, y - half, x + half, y + half, "node", { node: n, leaf: path }]);
      if (s > 44) {
        ctx.fillStyle = COLOR.muted;
        ctx.font = "10px ui-monospace, monospace";
        ctx.fillText(n.id, x - half, y + half + 11);
      }
    }
    const st = leafIons(path);
    if (st && s > 34 && 0.66 * mk.rIon < 8) {
      ctx.font = "10px ui-monospace, monospace";
      ctx.fillStyle = COLOR.ink;
      for (const ion of st.ions) ctx.fillText(ion.name, X(ion.xy[0] + (ion.dx || 0)) + 6, Y(ion.xy[1]) - 6);
    }
  }

  function drawOverlays(ctx) {
    if (S.sel && S.sel.kind === "inst") {
      const node = M.node(S.sel.path);
      if (node && node.parent === S.path) {
        const inst = M.master(S.path).instances.find((i) => i.name === node.name);
        const r = rectOf(S.sel.path, inst.x, inst.y);
        ctx.strokeStyle = COLOR.accent;
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 3]);
        roundRect(ctx, r[0] - 3, r[1] - 3, r[2] - r[0] + 6, r[3] - r[1] + 6, 9);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.max(0, r);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  function clip(ctx, text, width) {
    if (ctx.measureText(text).width <= width) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (ctx.measureText(text.slice(0, mid) + "…").width <= width) lo = mid; else hi = mid - 1;
    }
    return text.slice(0, lo) + "…";
  }

  function fmtTime(us) {
    const a = Math.abs(us);
    if (a >= 1e6) return (us / 1e6).toFixed(3) + " s";
    if (a >= 1e3) return (us / 1e3).toFixed(a >= 1e5 ? 1 : 2) + " ms";
    return us.toFixed(0) + " µs";
  }

  function updateHud() {
    const node = M.node(S.path);
    const m = M.master(S.path);
    $("hudLvl").textContent = S.path ? `${node.name} — ${m.title || m.name}` : (m.title || m.name);
    const step = $("hudStep");
    if (node.leaf && (m.family === "archive" || m.family === "decoder")) {
      const e = M.eventAt(S.path, S.t);
      step.hidden = !e;
      if (e) step.textContent = `${e[4]} · instruction ${e[6]} · ${fmtTime(e[2] - e[1])}`;
      const st = archiveAt(S.t);
      const held = Object.values(st.frames).filter((f) => frameText(f) !== "no correction").length;
      $("hudWhat").textContent = m.family === "archive"
        ? `${styleOf(m).title} · ${held} block frame(s) held, ${st.outcomes.length} outcome(s) recorded, ${st.windows} window update(s)`
        : `${styleOf(m).title} · ${M.eventsOf(S.path).filter((x) => x[1] <= S.t).length} of ${M.eventsOf(S.path).length} decoding jobs done`;
    } else if (node.leaf) {
      const e = M.eventAt(S.path, S.t);
      const st = e ? leafIons(S.path) : null;
      if (e && st && st.step) {
        const I = st.step.step.ins;
        const total = M.leafData[m.name].programs[e[4]].instructions.length;
        let what = I.type + (I.class ? " " + I.class : "") + (I.gate ? " " + I.gate : "");
        if (I.template) what += ` ${I.template.loop} ${I.template.delta > 0 ? "+" : ""}${I.template.delta}`;
        if (I.participants) what += ` · ${I.participants.length} ion${I.participants.length > 1 ? "s" : ""}`;
        if (I.pairs) what += ` · ${I.pairs.length} pair${I.pairs.length > 1 ? "s" : ""}`;
        step.hidden = false;
        step.textContent = `${guardOf(e) ? guardText(e) + " · " : ""}${e[4]}${e[5] > 1 ? ` (${st.local.iteration + 1}/${e[5]})` : ""} · instruction ${st.step.k + 1}/${total} · ${what}`;
        $("hudWhat").textContent = `${styleOf(m).title} · replaying its verified TSIR at ${fmtTime(st.local.local)} of ${fmtTime(st.local.duration)}`;
      } else {
        step.hidden = true;
        $("hudWhat").textContent = `${styleOf(m).title} · idle at this time`;
      }
    } else {
      step.hidden = true;
      const leaves = M.leavesUnder(S.path).length;
      const ions = M.inventoryUnder(S.path, S.t);
      $("hudWhat").textContent = `${node.children.length} instances · ${leaves} leaf gadgets · ${ions.toLocaleString()} ions inside now`;
    }
  }

  // ------------------------------------------------------------------ timeline

  const TL = { gutter: 170, ruler: 22, progH: 34, rowH: 22, scroll: 0, rows: [] };

  function tlRows() {
    const node = M.node(S.path);
    if (node.leaf) return [{ path: S.path, label: node.name, leaf: true }];
    const rank = (p) => { const i = CAT_ORDER.indexOf(M.node(p).family); return i < 0 ? 99 : i; };
    const roads = node.children.filter((p) => M.node(p).family === "road");
    const rows = node.children.filter((p) => roads.length < 4 || M.node(p).family !== "road")
      .map((p) => ({ path: p, label: M.node(p).name, leaf: M.node(p).leaf }));
    if (Object.keys(CATS).length) rows.sort((a, b) => rank(a.path) - rank(b.path));
    if (roads.length >= 4) {
      if (!TL.roadEvents || TL.roadKey !== S.path) {
        TL.roadEvents = roads.flatMap((p) => M.eventsOf(p)).sort((a, b) => a[1] - b[1] || a[2] - b[2]);
        TL.roadKey = S.path;
      }
      rows.push({ path: null, label: `roads · ${roads.length} junctions`, leaf: true, events: TL.roadEvents });
    }
    return rows;
  }

  function drawTimeline() {
    const [w, h] = sizeCanvas(tl, tctx);
    const ctx = tctx;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = COLOR.panel;
    ctx.fillRect(0, 0, w, h);
    const g = TL.gutter, span = Math.max(1e-6, S.tl.t1 - S.tl.t0);
    const tx = (t) => g + (t - S.tl.t0) / span * (w - g - 8);
    // ruler
    ctx.fillStyle = COLOR.soft;
    ctx.fillRect(g, 0, w - g, TL.ruler);
    ctx.fillStyle = COLOR.muted;
    ctx.font = "10.5px ui-monospace, monospace";
    const step = niceStep(span / Math.max(1, (w - g) / 110));
    for (let t = Math.ceil(S.tl.t0 / step) * step; t <= S.tl.t1; t += step) {
      const x = tx(t);
      ctx.fillRect(x, TL.ruler - 5, 1, 5);
      ctx.fillText(fmtTime(t), x + 3, 14);
    }
    // program lanes
    const y0 = TL.ruler + 2;
    ctx.fillStyle = COLOR.muted;
    ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
    ctx.fillText("program", 10, y0 + 14);
    const lanes = [];
    const laneH = 4;
    const insHits = [];
    M.insSpan.forEach((sp, i) => {
      if (!sp || sp[1] < S.tl.t0 || sp[0] > S.tl.t1) return;
      let lane = lanes.findIndex((end) => end <= sp[0]);
      if (lane < 0) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = sp[1];
      if (lane >= Math.floor((TL.progH - 4) / (laneH + 1))) return;
      const x = tx(sp[0]), x1 = Math.max(x + 1.5, tx(sp[1]));
      const ins = M.gir.program.instructions[i];
      ctx.fillStyle = S.hoverIns === i ? COLOR.accent : kindColor(ins.kind);
      ctx.fillRect(x, y0 + 2 + lane * (laneH + 1), x1 - x, laneH);
      insHits.push([x, y0 + lane * (laneH + 1), x1, y0 + 2 + (lane + 1) * (laneH + 1), i]);
    });
    TL.insHits = insHits;
    // rows
    TL.rows = tlRows();
    const top = y0 + TL.progH;
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, top, w, h - top);
    ctx.clip();
    const node = M.node(S.path);
    TL.rows.forEach((row, ri) => {
      const y = top + ri * TL.rowH - TL.scroll;
      if (y + TL.rowH < top || y > h) return;
      if (ri % 2) { ctx.fillStyle = "#fafbfc"; ctx.fillRect(0, y, w, TL.rowH); }
      const selected = S.sel && S.sel.path === row.path;
      ctx.fillStyle = selected ? COLOR.accent : COLOR.ink;
      ctx.font = (selected ? "600 " : "") + "12px ui-sans-serif, system-ui, sans-serif";
      const blk = row.path ? blockLabel(row.path) : "";
      if (row.path && Object.keys(CATS).length) {
        const sty = styleOf(M.master(row.path));
        ctx.fillStyle = sty.stroke;
        ctx.fill(new Path2D(shapeD(sty.shape, 10, y + 6, 13, 10)));
        ctx.fillStyle = selected ? COLOR.accent : COLOR.ink;
        ctx.fillText(clip(ctx, row.label + (blk ? "  " + blk : ""), g - 32), 28, y + 15);
      } else {
        ctx.fillText(clip(ctx, row.label + (blk ? "  " + blk : ""), g - 16), 10, y + 15);
      }
      const evs = row.events || (row.leaf ? M.eventsOf(row.path) : M.subtreeEvents(row.path));
      drawEventBars(ctx, evs, y + 4, TL.rowH - 8, tx, w, node.leaf);
    });
    ctx.restore();
    // cursor
    const cx = tx(S.t);
    ctx.fillStyle = COLOR.accent;
    ctx.fillRect(cx - 0.5, 0, 1.5, h);
    ctx.beginPath();
    ctx.moveTo(cx - 5, 0); ctx.lineTo(cx + 5, 0); ctx.lineTo(cx, 7); ctx.fill();
    // gutter line
    ctx.fillStyle = COLOR.line;
    ctx.fillRect(g - 1, 0, 1, h);
    ctx.fillRect(0, top - 1, w, 1);
  }

  function kindColor(kind) {
    const cat = { prep: "prep", se: "se", cx: "operation", zz: "surgery", xx: "surgery", read: "readout",
                  inject: "injection", store: "zone" }[kind];
    if (cat && CATS[cat]) return CATS[cat].stroke;
    return { ppm: COLOR.accent, tcnot_batch: COLOR.teal, transversal: COLOR.violet, pauli: COLOR.ink,
             magic: "#b42318" }[kind] || COLOR.muted;
  }

  function drawEventBars(ctx, evs, y, hgt, tx, w, detailed) {
    const lo = S.tl.t0, hi = S.tl.t1;
    let lastPx = -1, lastColor = null;
    const start = Math.max(0, lowerBound(evs, lo - M.makespan, (e) => e[1]));
    for (let i = start; i < evs.length; i++) {
      const e = evs[i];
      if (e[1] > hi) break;
      if (e[2] < lo) continue;
      const x0 = Math.max(TL.gutter, tx(e[1])), x1 = Math.min(w - 8, tx(e[2]));
      if (e[2] === e[1]) {
        ctx.fillStyle = COLOR.ink;
        ctx.fillRect(x0, y - 2, 1, hgt + 4);
        continue;
      }
      const color = opColor(e[4]);
      const px = Math.floor(x0);
      if (px === lastPx && color === lastColor && x1 - x0 < 1) continue;
      lastPx = px; lastColor = color;
      ctx.fillStyle = color;
      if (e[4] === "cycle") {
        ctx.fillRect(x0, y + 2, Math.max(1, x1 - x0), hgt - 4);
        if (x1 - x0 > 40) {
          ctx.fillStyle = "#475467";
          ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
          ctx.fillText(clip(ctx, `cycle ×${e[5]}`, x1 - x0 - 6), x0 + 3, y + hgt - 3);
        }
        // boundaries between repeated cycles
        const n = e[5];
        const per = (e[2] - e[1]) / n;
        if ((x1 - x0) / n > 6) {
          ctx.fillStyle = "#ffffff";
          for (let k = 1; k < n; k++) ctx.fillRect(tx(e[1] + k * per), y + 2, 1, hgt - 4);
        }
      } else {
        ctx.fillRect(x0, y, Math.max(1.5, x1 - x0), hgt);
        if (guardOf(e)) {
          // hatched: reserved either way, fired only if the condition holds
          ctx.save();
          ctx.strokeStyle = "rgba(255,255,255,.75)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          for (let x = x0; x < x1; x += 5) { ctx.moveTo(x, y + hgt); ctx.lineTo(x + hgt, y); }
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, y, Math.max(1.5, x1 - x0), hgt);
          ctx.clip();
          ctx.stroke();
          ctx.restore();
          ctx.restore();
        }
        if (x1 - x0 > 46) {
          ctx.fillStyle = "#ffffff";
          ctx.font = "10.5px ui-sans-serif, system-ui, sans-serif";
          const text = (guardOf(e) ? guardText(e) + " " : "") + e[4];
          ctx.fillText(clip(ctx, text, x1 - x0 - 6), x0 + 3, y + hgt - 3);
        }
      }
    }
    // inside one leaf, the running op's TSIR instructions as ticks
    if (detailed) {
      const e = M.eventAt(S.path, S.t);
      const m = M.master(S.path);
      const d = M.leafData[m.name];
      if (e && d && d.times[e[4]] && e[5] === 1 && !(e[9] && e[9].length)) {
        ctx.fillStyle = "rgba(24,34,48,.35)";
        for (const tt of d.times[e[4]]) {
          const x = tx(e[1] + tt[1]);
          if (x > TL.gutter && x < w - 8) ctx.fillRect(x, y + hgt, 1, 3);
        }
      }
    }
  }

  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
    const r = raw / p;
    return (r < 1.5 ? 1 : r < 3.5 ? 2 : r < 7.5 ? 5 : 10) * p;
  }

  // ------------------------------------------------------------------ panels

  function crumbs() {
    const el = $("crumbs");
    el.textContent = "";
    const chain = M.ancestors(S.path);
    chain.forEach((p, i) => {
      if (i) { const s = document.createElement("span"); s.className = "sep"; s.textContent = "›"; el.appendChild(s); }
      const a = document.createElement("a");
      a.textContent = p ? M.node(p).name : M.top;
      a.title = p ? `${M.node(p).master}` : "the top";
      if (p === S.path) a.className = "here";
      a.onclick = () => go(p);
      el.appendChild(a);
    });
  }

  const treeOpen = new Set([""]);
  function renderTree() {
    const el = $("tree");
    el.textContent = "";
    let count = 0;
    function row(path, depth) {
      const node = M.node(path);
      const m = M.master(path);
      const div = document.createElement("div");
      div.className = "tn" + (S.path === path || (S.sel && S.sel.path === path) ? " sel" : "");
      div.style.paddingLeft = (4 + depth * 12) + "px";
      const tw = document.createElement("span");
      tw.className = "tw";
      tw.textContent = node.leaf ? "" : treeOpen.has(path) ? "▾" : "▸";
      tw.onclick = (ev) => { ev.stopPropagation(); if (treeOpen.has(path)) treeOpen.delete(path); else treeOpen.add(path); renderTree(); };
      div.appendChild(tw);
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.dataset.path = path;
      div.appendChild(dot);
      const nm = document.createElement("span");
      nm.className = "nm";
      nm.textContent = path ? node.name : M.top;
      div.appendChild(nm);
      const blk = M.blockOfLeaf[path];
      if (blk) { const b = document.createElement("span"); b.className = "blk"; b.textContent = blk; div.appendChild(b); }
      const sub = document.createElement("span");
      sub.className = "sub";
      sub.textContent = m.kind === "leaf" ? (CATS[m.family] ? `${CATS[m.family].title}` : m.name) : `${m.family}`;
      div.appendChild(sub);
      div.onclick = () => { if (node.leaf || !path) { go(path === "" ? "" : node.parent === null ? "" : node.parent); select({ kind: "inst", path }); } else { go(node.parent); select({ kind: "inst", path }); } };
      div.ondblclick = () => go(path);
      el.appendChild(div);
      count++;
      if (!node.leaf && treeOpen.has(path)) for (const c of node.children) row(c, depth + 1);
    }
    row("", 0);
    $("treeCount").textContent = `${Object.keys(M.gir.leaves).length} leaves`;
    updateTreeDots();
  }
  function updateTreeDots() {
    for (const dot of document.querySelectorAll("#tree .dot")) {
      const p = dot.dataset.path;
      const node = M.node(p);
      let busy = false;
      if (node.leaf) { const e = M.eventAt(p, S.t); busy = !!(e && e[4] !== "cycle"); }
      else if (p !== "") busy = M.leavesUnder(p).some((l) => { const e = M.eventAt(l, S.t); return e && e[4] !== "cycle"; });
      dot.className = "dot" + (busy ? " busy" : "");
    }
  }

  function renderSignoff() {
    const box = $("verif");
    const r = D.signoff;
    if (!box) return;
    if (!r) { box.style.display = "none"; return; }
    let h = `<div class="so ${r.passed ? "pass" : "fail"}">${r.passed ? "✓ the schedule computes the algorithm" : "✗ the schedule does not match the algorithm"}</div>`;
    h += `<div class="note">flattened: ${r.circuit.qubits} ions, ${Object.entries(r.circuit.ops).map(([k, v]) => `${v} ${k}`).join(" · ")}; one symbolic run</div>`;
    for (const x of r.relations || []) h += `<div class="sol ${x.ok ? "pass" : "fail"}">${x.ok ? "✓" : "✗"} ${escapeHtml(x.relation)}${x.why ? `<br><span class="muted">${escapeHtml(x.why)}</span>` : ""}</div>`;
    for (const x of r.flows || []) h += `<div class="sol ${x.ok ? "pass" : "fail"}">${x.ok ? "✓" : "✗"} ${escapeHtml(x.flow)}${x.why ? `<br><span class="muted">${escapeHtml(x.why)}</span>` : ""}</div>`;
    if (r.random_bits) h += `<div class="note">random outcome bits: hardware ${r.random_bits.hardware}, ideal ${r.random_bits.ideal}</div>`;
    if (r.branch_count) {
      const per = (r.branches || []).map((b) => `${escapeHtml(b.label)}${b.unreachable ? " (cannot occur)" : b.passed ? " ✓" : " ✗"}`);
      h += `<div class="note">this program is <b>dynamic</b>: ${r.branch_count} branches of ` +
        `the guards ${escapeHtml((r.guards || []).join(", "))}, each signed off on its own — ` +
        `${per.join(" · ")}</div>`;
    }
    if (r.archive) {
      const blocks = Object.entries(r.archive.blocks || {});
      h += `<div class="note">the classical memory: ${r.archive.frames} write(s), ${r.archive.software_paulis} correction(s) kept in software` +
        (blocks.length ? `; blocks still holding a frame: ${escapeHtml(blocks.map(([b]) => b).join(", "))}` : "") +
        ` — every relation and flow above is checked up to exactly those</div>`;
    }
    const d = r.distance || {};
    if (d.skipped) h += `<div class="note">fault distance: ${escapeHtml(d.skipped)}</div>`;
    else if (d.faults) h += `<div class="sol ${d.passed ? "pass" : "fail"}">${d.passed ? "✓" : "✗"} no 1 or 2 of the schedule's ${d.faults.toLocaleString()} fault locations flip a logical result unseen (${d.detectors} detectors)${d.witness && d.witness.length ? `<br><span class="muted">${escapeHtml(d.witness.join(" + "))}</span>` : ""}</div>`;
    for (const w of r.why || []) h += `<div class="note" style="color:var(--bad)">${escapeHtml(w)}</div>`;
    $("verifBody").innerHTML = h;
  }

  function renderChecks() {
    const ck = D.checks || {};
    const el = $("checksBody");
    el.textContent = "";
    const rules = D.rules || {};
    const passed = new Set(ck.passed || []), failed = new Set(ck.failed || []);
    for (const id of Object.keys(rules)) {
      const div = document.createElement("div");
      div.className = "ck";
      const state = passed.has(id) ? "pass" : failed.has(id) ? "fail" : "skip";
      div.innerHTML = `<b class="${state}">${id}</b><span>${escapeHtml(rules[id])}</span>`;
      const v = (ck.violations || {})[id];
      if (v && v.length) {
        const vv = document.createElement("div");
        vv.className = "v";
        vv.textContent = v.slice(0, 8).join("\n");
        vv.style.whiteSpace = "pre-line";
        div.appendChild(vv);
        div.onclick = () => div.classList.toggle("open");
      } else if ((ck.skipped || {})[id]) {
        div.title = ck.skipped[id];
      }
      el.appendChild(div);
    }
    const b = $("checksBadge");
    const nf = (ck.failed || []).length;
    const ids = Object.keys(rules);
    b.textContent = nf ? `G ${nf} failed` : `${ids[0] || "G1"}–${ids[ids.length - 1] || "G9"} ✓`;
    b.className = nf ? "bad" : "ok";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // the LogicQ source, one element per line, lit by the instructions running now
  const progLines = [];
  function renderProgram() {
    const src = M.gir.program.source || "";
    $("progName").textContent = M.gir.program.name;
    const el = $("progBody");
    el.textContent = "";
    const byLine = {};
    for (const ins of M.gir.program.instructions) (byLine[ins.line] || (byLine[ins.line] = [])).push(ins.id);
    src.split(/\r?\n/).forEach((text, i) => {
      const line = i + 1;
      const div = document.createElement("div");
      const ids = byLine[line] || [];
      const trimmed = text.trim();
      div.className = "pl" + (trimmed.startsWith("code ") ? " decl" : trimmed.startsWith("//") ? " cm" : "");
      if (ids.some((id) => M.refused[id] !== undefined)) {
        div.className += " refused";
        div.title = ids.map((id) => M.refused[id]).filter(Boolean).join("\n");
      }
      div.innerHTML = `<span class="ln">${line}</span><span></span>`;
      div.lastChild.textContent = text || " ";
      div.dataset.ids = ids.join(",");
      if (ids.length) {
        div.onclick = () => {
          const sp = M.insSpan[ids[0]];
          if (sp) { seek(sp[0]); focusInstruction(ids[0]); }
        };
        div.onmouseenter = () => { S.hoverIns = ids[0]; S.tlDirty = true; };
        div.onmouseleave = () => { S.hoverIns = null; S.tlDirty = true; };
      }
      el.appendChild(div);
      progLines.push([div, ids]);
    });
  }
  function updateProgram() {
    const live = new Set(M.activeInstructions(S.t));
    let first = null;
    for (const [div, ids] of progLines) {
      if (!ids.length) continue;
      const isLive = ids.some((id) => live.has(id));
      const done = ids.every((id) => M.insSpan[id] && M.insSpan[id][1] <= S.t);
      const cls = div.className.replace(/ (live|done)/g, "") + (isLive ? " live" : done ? " done" : "");
      if (cls !== div.className) div.className = cls;
      if (isLive && !first) first = div;
    }
    if (first && S.playing) {
      const box = $("progBody");
      const top = first.offsetTop - box.offsetTop;
      if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 40) box.scrollTop = top - 60;
    }
  }
  function focusInstruction(id) {
    const leaves = [...M.insLeaves[id]];
    if (!leaves.length) return;
    // the smallest composite holding every leaf the instruction touches
    const chains = leaves.map((l) => M.ancestors(l));
    let common = "";
    for (let k = 0; k < chains[0].length; k++) {
      const p = chains[0][k];
      if (chains.every((c) => c[k] === p) && !M.node(p).leaf) common = p;
    }
    go(common);
    select({ kind: "ins", id });
  }

  function renderInspector() {
    const el = $("inspBody");
    const sel = S.sel;
    if (!sel) { el.innerHTML = inspectLevel(S.path); return; }
    if (sel.kind === "inst") el.innerHTML = inspectLevel(sel.path);
    else if (sel.kind === "ion") el.innerHTML = inspectIon(sel.gid);
    else if (sel.kind === "chan") el.innerHTML = inspectChannel(sel.net, sel.ch);
    else if (sel.kind === "port") el.innerHTML = inspectPort(sel.path, sel.port);
    else if (sel.kind === "ins") el.innerHTML = inspectInstruction(sel.id);
    else if (sel.kind === "node") el.innerHTML = inspectNode(sel.leaf, sel.node);
    else if (sel.kind === "wire") el.innerHTML = inspectWire(sel.net, sel.ch);
    else if (sel.kind === "msg") el.innerHTML = inspectMessage(sel.msg);
    wireInspector(el);
  }

  function logicCell(op) {
    const lg = op.logic || {};
    if (!lg.status || lg.status === "none") return `<span class="muted">—</span>`;
    const parts = [];
    if (lg.flows) parts.push(`${lg.flows.flows.filter((f) => f.ok).length}/${lg.flows.flows.length} flows`);
    if (lg.distance) {
      const d = lg.distance;
      if (d.at_least) parts.push("d ≥ 3");
      else if (d.distance) parts.push(`d = ${d.distance}${d.expect && d.expect !== "ft" ? " (by design)" : ""}`);
    }
    if (lg.check) parts.push(lg.check.output ? `${lg.check.output} exact` : "checked");
    return `<span class="badge ${lg.status}" title="${escapeHtml((lg.why || []).join("\n"))}">${lg.status === "verified" ? "✓" : "✗"}</span> <span class="muted">${escapeHtml(parts.join(" · "))}</span>`;
  }

  function inspectLevel(path) {
    const node = M.node(path);
    const m = M.master(path);
    const sty = styleOf(m);
    let h = `<h4>${CATS[m.family] ? shapeSvg(m.family, 14) + " " : ""}${escapeHtml(path ? node.name : M.top)}</h4><div class="muted">${escapeHtml(m.title || m.name)} · ${escapeHtml(sty.title)}${sty.place ? " (" + escapeHtml(sty.place) + ")" : ""}</div>`;
    if (sty.job) h += `<div class="note">${escapeHtml(sty.job)}</div>`;
    if (m.doc) h += `<div class="note">${escapeHtml(m.doc)}</div>`;
    const blk = M.blockOfLeaf[path];
    if (blk) { const b = M.gir.blocks[blk]; h += `<div class="note">holds logical block <b>${blk}</b>: ${b.code} [[${b.n},${b.k}]]</div>`; }
    const classical = m.family === "archive" || m.family === "decoder";
    if (!classical) {
      const inv = m.kind === "leaf" ? M.inventory(path, S.t) : M.inventoryUnder(path, S.t);
      h += `<div class="note">${inv.toLocaleString()} ions inside now · capacity ${m.capacity}</div>`;
    } else {
      h += `<div class="note">no ions: this place runs on bits</div>`;
    }
    if (m.family === "archive") h += archivePanel(path);
    if (m.family === "decoder") h += decoderPanel(path);
    if (m.ports && m.ports.length) {
      h += `<table><tr><th>port</th><th>dir</th><th>carries</th><th class="num">width</th></tr>`;
      for (const p of m.ports) {
        const what = p.kind === "classical" ? signalName(p.signal) : p.role;
        h += `<tr><td>${p.name}</td><td>${p.dir}</td><td>${escapeHtml(what)}</td><td class="num">${p.kind === "classical" ? p.bits + " bit" : p.width}</td></tr>`;
      }
      h += `</table>`;
    }
    if (m.kind === "leaf") {
      const e = M.eventAt(path, S.t);
      const logic = Object.values(m.ops).some((op) => op.logic && op.logic.status && op.logic.status !== "none");
      h += `<table><tr><th>op</th><th>hardware</th>${logic ? "<th>logic</th>" : ""}<th class="num">ms</th><th class="num">2Q</th></tr>`;
      const ops = Object.values(m.ops);
      for (const op of ops.slice(0, 16)) {
        const on = e && e[4] === op.name;
        h += `<tr${on ? ' style="background:#fff4ed"' : ""}><td>${escapeHtml(op.name)}</td><td><span class="badge ${(op.rules.failed || []).length ? "failed" : "verified"}">${(op.rules.failed || []).length ? "✗" : "✓"} ${op.rules.passed ? op.rules.passed.length : ""} rules</span></td>` +
          (logic ? `<td>${logicCell(op)}</td>` : "") +
          `<td class="num">${(op.duration_us / 1000).toFixed(2)}</td><td class="num">${op.metrics.gates_2q || 0}</td></tr>`;
      }
      if (ops.length > 16) h += `<tr><td colspan="5" class="muted">… ${ops.length - 16} more</td></tr>`;
      h += `</table>`;
      if (e) {
        const g = guardOf(e);
        if (g) {
          h += `<div class="note"><b>classically controlled:</b> this op runs only when ` +
            `<code>${escapeHtml(g.guard || "")}</code> holds. The guard was at the door at ` +
            `${fmtTime(g.ready_us || 0)}, the op starts at ${fmtTime(e[1])}; the place is ` +
            `reserved either way (${(g.branches || []).join(" / ")} branch, same ports).</div>`;
        }
        const op = m.ops[e[4]];
        if (op && op.notes && op.notes.length) h += op.notes.map((n) => `<div class="note">• ${escapeHtml(n)}</div>`).join("");
      }
    } else {
      h += `<div class="note">${node.children.length} instances, ${(m.channels || []).length} channels, ${M.leavesUnder(path).length} leaf gadgets below</div>`;
    }
    h += `<div class="btns">`;
    if (path !== S.path) h += `<button data-go="${escapeHtml(path)}">Open ↘</button>`;
    const e = m.kind === "leaf" ? M.eventAt(path, S.t) : null;
    const studio = D.studio && e ? D.studio[`${m.name}.${e[4]}`] : null;
    if (studio) h += `<a class="btn" href="${escapeHtml(studio)}" target="_blank" rel="noopener">Studio page for ${escapeHtml(e[4])} ↗</a>`;
    h += `</div>`;
    return h;
  }

  function inspectIon(gid) {
    const home = M.ionHome(gid);
    const trace = M.ionTrace(gid);
    let h = `<h4>ion #${gid}</h4>`;
    if (home) h += `<div class="muted">home: ${escapeHtml(home.leaf)} · ${escapeHtml(home.local)}</div>`;
    h += `<div class="btns"><button data-follow="${gid}">${S.follow === gid ? "Stop following" : "Follow this ion"}</button></div>`;
    const moves = trace.filter((x) => x.kind !== "home-op");
    h += `<table><tr><th>when</th><th>what</th></tr>`;
    for (const x of moves.slice(0, 60)) {
      const what = x.kind === "carry" ? `along ${x.net}` : `${x.op} in ${x.leaf} as ${x.local}`;
      h += `<tr><td class="num"><a href="#" data-seek="${x.t0}">${fmtTime(x.t0)}</a></td><td>${escapeHtml(what)}</td></tr>`;
    }
    h += `</table>`;
    if (!moves.length) h += `<div class="note">this ion never leaves its home in the program</div>`;
    return h;
  }

  function inspectChannel(net, ch) {
    const rec = M.gir.nets[net];
    const cs = M.byNet[net] || [];
    let h = `<h4>channel ${escapeHtml(ch.name)}</h4><div class="muted">${escapeHtml(ch.a)} ↔ ${escapeHtml(ch.b)}</div>`;
    h += `<div class="note">${rec ? rec[4] : ch.length} sites · ${cs.length} carries in the program</div>`;
    const ref = M.gir.channels && M.gir.channels[`${ch.length}x1`];
    if (ref) h += `<div class="note">one ion takes ${fmtTime(ref.transit_us)} end to end (conveyor reference, <span class="badge ${ref.status}">${ref.status}</span>)</div>`;
    const now = M.carriesOn(net, S.t);
    for (const c of now) h += `<div class="note">now: ${c[7].length} ${c[8]} ion(s) from ${escapeHtml(c[2])}, instruction ${c[9]}</div>`;
    return h;
  }

  function signalName(sig) {
    return { syndrome: "syndrome bits", outcome: "a logical outcome",
             frame: "a Pauli-frame update", decision: "a guard" }[sig] || sig;
  }

  function inspectWire(net, ch) {
    const rec = WIRES[net] || [];
    const ms = msgByWire[net] || [];
    const live = messagesOn(net, S.t);
    let h = `<h4>${shapeSvg("wire", 13)} wire ${escapeHtml(ch.name)}</h4>` +
      `<div class="muted">${escapeHtml(ch.a)} → ${escapeHtml(ch.b)} · ${fmtTime(ch.latency_us || rec[4] || 0)} latency</div>`;
    h += `<div class="note">a classical link: bits, not ions. ${ms.length} message(s) cross it in this program.</div>`;
    for (const m of live) h += `<div class="note">now: ${m[8]} bit(s) of ${escapeHtml(signalName(m[9]))} in flight — ${escapeHtml(m[12] || "")}</div>`;
    h += `<table><tr><th>when</th><th>bits</th><th>what</th></tr>`;
    for (const m of ms.slice(0, 40)) h += `<tr><td class="num"><a href="#" data-seek="${m[6]}">${fmtTime(m[6])}</a></td><td class="num">${m[8]}</td><td>${escapeHtml(m[12] || signalName(m[9]))}</td></tr>`;
    h += `</table>`;
    return h;
  }

  function inspectMessage(m) {
    return `<h4>${shapeSvg("wire", 13)} ${escapeHtml(signalName(m[9]))}</h4>` +
      `<div class="muted">${escapeHtml(m[2])}.${escapeHtml(m[3])} → ${escapeHtml(m[4])}.${escapeHtml(m[5])}</div>` +
      `<div class="note">${m[8]} bit(s), instruction ${m[10]}: ${escapeHtml(m[12] || "")}</div>` +
      `<div class="note">leaves <a href="#" data-seek="${m[6]}">${fmtTime(m[6])}</a>, usable ${fmtTime(m[7])} (${fmtTime(m[7] - m[6])} on the wire)</div>` +
      `<div class="note">no ion moves: this is the classical half of the machine</div>`;
  }

  // What the archive holds at the current time, as the sign-off reads it.
  function archivePanel(path) {
    const st = archiveAt(S.t);
    const rows = Object.values(st.frames);
    let h = `<div class="note">what it holds at ${fmtTime(S.t)}:</div>`;
    h += `<table><tr><th>block</th><th>frame</th><th>why</th></tr>`;
    if (!rows.length) h += `<tr><td colspan="3" class="muted">no correction yet</td></tr>`;
    for (const f of rows) h += `<tr><td>${escapeHtml(f.block || "")}</td><td>${escapeHtml(frameText(f))}</td><td class="muted">${escapeHtml(f.text || "")}</td></tr>`;
    h += `</table>`;
    if (st.outcomes.length) {
      h += `<table><tr><th>outcome</th><th>value</th></tr>`;
      for (const o of st.outcomes.slice(-8)) h += `<tr><td>${escapeHtml(o.block || "")}</td><td>${escapeHtml(o.text || "")}</td></tr>`;
      h += `</table>`;
    }
    h += `<div class="note">${st.windows} decoder window update(s) applied so far; a window update is the frame the measuring gadget's own report declares, and the sign-off allows exactly those</div>`;
    return h;
  }

  function decoderPanel(path) {
    const m = M.master(path);
    const chk = (m.ops.decode && m.ops.decode.logic && m.ops.decode.logic.check) || null;
    const lat = CONTROL.latency_us || {};
    let h = "";
    if (chk) h += `<div class="sol ${chk.passed ? "pass" : "fail"}">${chk.passed ? "✓" : "✗"} ${escapeHtml(chk.claim || "")}</div>`;
    h += `<div class="note">${lat.link_us || 0} µs on the wire from a measuring place, ${lat.lookup_us || 0} µs to look up, ${lat.write_us || 0} µs to write the frame — a reaction time of ${((lat.link_us || 0) + (lat.lookup_us || 0) + (lat.write_us || 0)).toFixed(1)} µs</div>`;
    const jobs = M.eventsOf(path).length;
    h += `<div class="note">${jobs} decoding job(s) in this program</div>`;
    return h;
  }

  function inspectPort(path, p) {
    return `<h4>${escapeHtml(M.node(path).name)}.${escapeHtml(p.name)}</h4><div class="muted">${p.dir} · ${p.role} · width ${p.width}${p.order ? " · order " + p.order : ""}</div>` +
      (p.bind ? `<div class="note">exports ${escapeHtml(p.bind)}</div>` : "") + (p.code ? `<div class="note">bundles of ${escapeHtml(p.code)}</div>` : "");
  }

  function inspectNode(leaf, n) {
    return `<h4>${escapeHtml(n.id)}</h4><div class="muted">${n.kind}${n.zone_type ? " · zone " + n.zone_type : ""} · degree ${n.degree ?? ""}</div>` +
      `<div class="note">${(n.labels || []).join(", ")}</div>`;
  }

  function inspectInstruction(id) {
    const ins = M.gir.program.instructions[id];
    const sp = M.insSpan[id];
    let h = `<h4>instruction ${id}</h4><div class="muted">line ${ins.line} · ${ins.kind}` +
      (ins.guard ? ` · guarded by <code>${escapeHtml(ins.guard)}</code>` : "") +
      `</div><pre style="white-space:pre-wrap;font-size:11.5px;margin:6px 0">${escapeHtml(ins.text)}</pre>`;
    if (ins.guard) h += `<div class="note">a classically controlled instruction: the ions go ` +
      `and the places are reserved either way, and the pulses fire only when the condition ` +
      `holds. The sign-off checks every branch of it separately.</div>`;
    if (M.refused[id] !== undefined) h += `<div class="note" style="color:var(--bad)">not realised: ${escapeHtml(M.refused[id])}</div>`;
    if (sp) h += `<div class="note">runs ${fmtTime(sp[0])} → ${fmtTime(sp[1])} (${fmtTime(sp[1] - sp[0])}) on ${M.insLeaves[id].size} leaf gadgets</div>`;
    const evs = M.gir.events.filter((e) => e[6] === id).slice(0, 40);
    h += `<table><tr><th>when</th><th>op</th><th>where</th></tr>`;
    for (const e of evs) h += `<tr><td class="num"><a href="#" data-seek="${e[1]}">${fmtTime(e[1])}</a></td><td>${escapeHtml(e[4])}</td><td><a href="#" data-sel="${escapeHtml(e[3])}">${escapeHtml(e[3])}</a></td></tr>`;
    h += `</table>`;
    return h;
  }

  function wireInspector(el) {
    for (const b of el.querySelectorAll("[data-go]")) b.onclick = () => go(b.dataset.go);
    for (const a of el.querySelectorAll("[data-seek]")) a.onclick = (ev) => { ev.preventDefault(); seek(+a.dataset.seek); };
    for (const a of el.querySelectorAll("[data-sel]")) a.onclick = (ev) => { ev.preventDefault(); const p = a.dataset.sel; go(M.node(p).parent || ""); select({ kind: "inst", path: p }); };
    for (const b of el.querySelectorAll("[data-follow]")) b.onclick = () => { const g = +b.dataset.follow; S.follow = S.follow === g ? null : g; S.dirty = true; renderInspector(); };
  }

  function legend() {
    const used = new Set(Object.values(M.gir.leaves).map((v) => M.masters[v[0]].family));
    if (Object.keys(WIRES).length) used.add("wire");
    const cats = CAT_ORDER.filter((c) => CATS[c] && used.has(c));
    // the ion colours are the studio's: what the machine is doing to an ion this instant,
    // not what role a code gave it (qccd/viz/render.py::ionColour)
    const swatches = [[P("data", "#475467"), "an ion"],
                      [P("x", "#0f766e"), "in a gate"],
                      [P("z", "#b42318"), "measured or reset"],
                      [P("active", "#f6c34a"), "the site in play"],
                      [P("accent", "#e4572e"), "the ion you follow"]];
    $("legend").innerHTML = (cats.length ? `<div class="lg-cats">${cats.map((c) => `<span>${shapeSvg(c, 12)}${escapeHtml(CATS[c].title)}</span>`).join("")}</div>` : "") +
      `<div class="lg-roles">` + swatches.map(([c, v]) => `<span><i style="background:${c}"></i>${v}</span>`).join("") + `</div>`;
  }

  // ------------------------------------------------------------------ the library

  function renderLibrary() {
    const el = $("library");
    const rank = (m) => { const i = CAT_ORDER.indexOf(m.family); return m.kind === "composite" ? 200 : i < 0 ? 100 : i; };
    const masters = Object.values(M.masters).sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
    let h = `<h2>Gadget library <button id="bNewComposite">New composite gadget</button></h2><p class="lede">Every master in this design, by the kind of place it is. A leaf is compiled once, replayed against the 27 hardware rules, and — for the verified places — its own circuit is checked against a complete stabilizer-flow specification and a fault-distance experiment. A composite is instances and channels; schedules are built against these abstracts alone.</p>`;
    let lastGroup = null;
    for (const m of masters) {
      const group = m.kind === "composite" ? "composite" : m.family;
      if (group !== lastGroup) {
        if (lastGroup !== null) h += `</div>`;
        const c = CATS[group];
        h += c ? `<h3 class="libcat">${shapeSvg(group, 16)} ${escapeHtml(c.title)} <span class="muted">· the ${escapeHtml(c.place.toLowerCase())}</span></h3><p class="lede">${escapeHtml(c.job)}</p><div class="cards">`
               : `<h3 class="libcat">${escapeHtml(group === "composite" ? "Composite gadgets" : group)}</h3><div class="cards">`;
        lastGroup = group;
      }
      h += `<div class="card" style="border-top:3px solid ${styleOf(m).stroke}"><h3><span>${escapeHtml(m.name)}</span><span class="fam">${m.kind} · ${escapeHtml(styleOf(m).title)}` +
        (m.kind === "composite" ? ` <button data-edit="${escapeHtml(m.name)}">Edit</button>` : "") + `</span></h3>`;
      h += `<div class="doc">${escapeHtml(m.title)}${m.doc ? " — " + escapeHtml(m.doc) : ""}</div>`;
      h += `<canvas data-master="${escapeHtml(m.name)}"></canvas>`;
      h += `<table><tr><th>port</th><th>dir</th><th>role</th><th class="num">width</th></tr>`;
      for (const p of m.ports || []) h += `<tr><td>${p.name}</td><td>${p.dir}</td><td>${p.role}</td><td class="num">${p.width}</td></tr>`;
      h += `</table>`;
      if (m.kind === "leaf") {
        const logic = Object.values(m.ops).some((op) => op.logic && op.logic.status && op.logic.status !== "none");
        h += `<table><tr><th>op</th><th>status</th>${logic ? "<th>logic</th>" : ""}<th class="num">duration</th><th class="num">2Q</th><th class="num">peak n̄</th></tr>`;
        for (const op of Object.values(m.ops)) {
          const failed = (op.rules.failed || []).join(" ");
          h += `<tr title="${escapeHtml(op.title + (op.notes.length ? "\n" + op.notes.join("\n") : "") + (failed ? "\nfailed: " + failed : "") + "\nskipped: " + Object.keys(op.rules.skipped || {}).join(", "))}"><td>${escapeHtml(op.name)}</td><td><span class="badge ${op.status}">${op.status}</span></td>` +
            (logic ? `<td>${logicCell(op)}</td>` : "") +
            `<td class="num">${fmtTime(op.duration_us)}</td><td class="num">${op.metrics.gates_2q}</td><td class="num">${op.metrics.peak_quanta}</td></tr>`;
        }
        h += `</table>`;
      } else {
        h += `<div class="note">${m.instances.length} instances: ${escapeHtml([...new Set(m.instances.map((i) => i.master))].join(", "))} · ${m.channels.length} channels · ${Object.entries(m.inventory || {}).map(([k, v]) => `${v} ${k}`).join(", ")}</div>`;
      }
      h += `</div>`;
    }
    if (lastGroup !== null) h += `</div>`;
    el.innerHTML = h;
    for (const cv of el.querySelectorAll("canvas[data-master]")) drawThumb(cv, M.masters[cv.dataset.master]);
    for (const b of el.querySelectorAll("[data-edit]")) b.onclick = () => { closeLibrary(); window.GADGET_EDITOR.open(b.dataset.edit); };
    const nb = $("bNewComposite");
    if (nb) nb.onclick = () => { closeLibrary(); window.GADGET_EDITOR.create(); };
  }

  function drawThumb(cv, m) {
    const r = cv.getBoundingClientRect();
    cv.width = r.width * DPR; cv.height = r.height * DPR;
    const ctx = cv.getContext("2d");
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const b = m.bbox && m.bbox.length ? m.bbox : [0, 0, 1, 1];
    const s = Math.min((r.width - 20) / Math.max(1, b[2] - b[0]), (r.height - 20) / Math.max(1, b[3] - b[1]));
    const ox = (r.width - (b[2] - b[0]) * s) / 2 - b[0] * s, oy = (r.height - (b[3] - b[1]) * s) / 2 - b[1] * s;
    const x = (v) => v * s + ox, y = (v) => v * s + oy;
    if (m.kind === "leaf") {
      const d = M.leafData[m.name];
      if (!d) return;
      const pos = {};
      for (const n of d.device.nodes) pos[n.id] = n.pos;
      ctx.lineWidth = 1;
      for (const sg of d.device.segments) {
        const a = pos[sg.ends[0]], c = pos[sg.ends[1]];
        ctx.strokeStyle = sg.loop ? COLOR.rail : COLOR.spur;
        ctx.beginPath(); ctx.moveTo(x(a[0]), y(a[1])); ctx.lineTo(x(c[0]), y(c[1])); ctx.stroke();
      }
      ctx.fillStyle = ROLE.data;
      for (const name in d.home) {
        const p = pos[d.home[name]];
        ctx.fillStyle = ROLE[d.roles[name]] || COLOR.teal;
        ctx.beginPath(); ctx.arc(x(p[0]), y(p[1]), Math.max(1, Math.min(3, s * 0.3)), 0, 7); ctx.fill();
      }
    } else {
      for (const inst of m.instances) {
        const cm = M.masters[inst.master];
        const cb = cm.bbox;
        const sty = styleOf(cm);
        ctx.fillStyle = sty.fill; ctx.strokeStyle = sty.stroke;
        const path = new Path2D(shapeD(cm.kind === "composite" ? "round" : sty.shape, x(inst.x + cb[0]), y(inst.y + cb[1]), (cb[2] - cb[0]) * s, (cb[3] - cb[1]) * s));
        ctx.fill(path); ctx.stroke(path);
      }
      ctx.strokeStyle = COLOR.rail;
      for (const ch of m.channels) {
        ctx.beginPath();
        ch.points.forEach((p, i) => (i ? ctx.lineTo(x(p[0]), y(p[1])) : ctx.moveTo(x(p[0]), y(p[1]))));
        ctx.stroke();
      }
    }
  }

  // ------------------------------------------------------------------ navigation

  function go(path) {
    if (!(path in M.nodes)) return;
    if (S.path !== path) {
      S.path = path;
      if (!S.sel || !(S.sel.kind === "inst" && M.node(S.sel.path) && M.node(S.sel.path).parent === path)) S.sel = null;
      TL.scroll = 0;
      fit();
    }
    crumbs();
    renderTree();
    renderInspector();
    writeHash();
    S.dirty = true;
  }
  function up() { const n = M.node(S.path); if (n && n.parent !== null) go(n.parent); }
  function select(sel) {
    S.sel = sel;
    if (sel && sel.kind === "inst") {
      for (const p of M.ancestors(sel.path)) if (!M.node(p).leaf) treeOpen.add(p);
    }
    renderInspector();
    renderTree();
    S.dirty = true;
  }
  function seek(t) {
    S.t = Math.max(0, Math.min(M.makespan, t));
    if (S.t < S.tl.t0 || S.t > S.tl.t1) {
      const span = S.tl.t1 - S.tl.t0;
      S.tl.t0 = Math.max(0, S.t - span / 2);
      S.tl.t1 = S.tl.t0 + span;
    }
    S.dirty = true;
  }
  function writeHash() {
    const parts = [`p=${encodeURIComponent(S.path)}`, `t=${Math.round(S.t)}`];
    try { history.replaceState(null, "", "#" + parts.join("&")); } catch (e) { /* file:// may refuse */ }
  }
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.has("t")) S.t = +h.get("t") || 0;
    if (h.has("p") && h.get("p") in M.nodes) S.path = h.get("p");
  }

  // ------------------------------------------------------------------ input

  function hitAt(x, y) {
    for (let i = hits.length - 1; i >= 0; i--) {
      const h = hits[i];
      if (x >= h[0] && x <= h[2] && y >= h[1] && y <= h[3]) return h;
    }
    return null;
  }
  let drag = null;
  const editing = () => !!(window.GADGET_EDITOR && window.GADGET_EDITOR.active);
  stage.addEventListener("pointerdown", (ev) => {
    if (editing()) return;
    stage.setPointerCapture(ev.pointerId);
    drag = { x: ev.clientX, y: ev.clientY, cx: S.cam.x, cy: S.cam.y, moved: false };
  });
  stage.addEventListener("pointermove", (ev) => {
    if (editing()) return;
    const r = stage.getBoundingClientRect();
    if (drag) {
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; stage.classList.add("dragging"); }
      if (drag.moved) { S.cam.x = drag.cx + dx; S.cam.y = drag.cy + dy; S.dirty = true; }
      return;
    }
    const h = hitAt(ev.clientX - r.left, ev.clientY - r.top);
    showTip(ev, h);
  });
  stage.addEventListener("pointerup", (ev) => {
    if (editing()) return;
    stage.classList.remove("dragging");
    const r = stage.getBoundingClientRect();
    if (drag && !drag.moved) {
      const h = hitAt(ev.clientX - r.left, ev.clientY - r.top);
      if (h) {
        if (h[4] === "inst") select({ kind: "inst", path: h[5].path });
        else if (h[4] === "wire") select({ kind: "wire", net: h[5].net, ch: h[5].ch });
        else if (h[4] === "msg") select({ kind: "msg", msg: h[5].msg, net: h[5].net });
        else if (h[4] === "ion") { select({ kind: "ion", gid: h[5].gid }); }
        else if (h[4] === "chan") select({ kind: "chan", net: h[5].net, ch: h[5].ch });
        else if (h[4] === "port") select({ kind: "port", path: h[5].path, port: h[5].port });
        else if (h[4] === "node") select({ kind: "node", leaf: h[5].leaf, node: h[5].node });
      } else select(null);
    }
    drag = null;
  });
  stage.addEventListener("dblclick", (ev) => {
    if (editing()) return;
    const r = stage.getBoundingClientRect();
    const h = hitAt(ev.clientX - r.left, ev.clientY - r.top);
    if (h && h[4] === "inst") go(h[5].path);
  });
  stage.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = stage.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const k = Math.exp(-ev.deltaY * 0.0015);
    S.cam.x = mx - (mx - S.cam.x) * k;
    S.cam.y = my - (my - S.cam.y) * k;
    S.cam.s *= k;
    S.dirty = true;
  }, { passive: false });
  stage.addEventListener("pointerleave", () => { $("tip").style.display = "none"; });

  function showTip(ev, h) {
    const tip = $("tip");
    if (!h) { tip.style.display = "none"; return; }
    let text = "";
    if (h[4] === "inst") {
      const p = h[5].path, m = M.master(p);
      const e = m.kind === "leaf" ? M.eventAt(p, S.t) : null;
      text = `${p}\n${m.title || m.name}` + (M.blockOfLeaf[p] ? `\nblock ${M.blockOfLeaf[p]}` : "") +
        (e ? `\nnow: ${e[4]}` + (e[6] >= 0 ? ` (instruction ${e[6]})` : "") : m.kind === "composite" ? `\nnow: ${activeOpsUnder(p)}` : "") +
        (guardOf(e) ? `\nclassically controlled: ${guardText(e)}` : "") +
        "\ndouble-click to open";
    } else if (h[4] === "ion") {
      const home = M.ionHome(h[5].gid);
      text = `ion #${h[5].gid}` + (h[5].name ? ` (${h[5].name} here)` : "") + (home ? `\nhome ${home.leaf} · ${home.local}` : "") + "\nclick to trace";
    } else if (h[4] === "chan") {
      text = `channel ${h[5].ch.name}: ${h[5].ch.a} ↔ ${h[5].ch.b}\n${h[5].ch.length} sites`;
    } else if (h[4] === "port") {
      const p = h[5].port;
      text = `${h[5].path}.${p.name}\n${p.dir} · ${p.role} · width ${p.width}`;
    } else if (h[4] === "node") {
      const n = h[5].node;
      text = `${n.id} · ${n.kind}${n.zone_type ? " · " + n.zone_type : ""}`;
    } else if (h[4] === "wire") {
      const live = messagesOn(h[5].net, S.t);
      text = `wire ${h[5].ch.name}: ${h[5].ch.a} → ${h[5].ch.b}\n${fmtTime(h[5].ch.latency_us || 0)} latency · bits, not ions` +
        (live.length ? `\nnow: ${live[0][12] || signalName(live[0][9])}` : "");
    } else if (h[4] === "msg") {
      const m = h[5].msg;
      text = `${signalName(m[9])}: ${m[8]} bit(s)\n${m[2]}.${m[3]} → ${m[4]}.${m[5]}\n${m[12] || ""}`;
    }
    tip.textContent = text;
    tip.style.display = "block";
    tip.style.left = Math.min(window.innerWidth - 370, ev.clientX + 14) + "px";
    tip.style.top = (ev.clientY + 14) + "px";
  }

  // timeline input
  let tdrag = null;
  tl.addEventListener("pointerdown", (ev) => {
    const r = tl.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    tl.setPointerCapture(ev.pointerId);
    if (x < TL.gutter) {
      const top = TL.ruler + 2 + TL.progH;
      const ri = Math.floor((y - top + TL.scroll) / TL.rowH);
      const row = TL.rows[ri];
      if (row && row.path && y > top) { select({ kind: "inst", path: row.path }); tdrag = null; }
      return;
    }
    if (y < TL.ruler) tdrag = { kind: "pan", x, t0: S.tl.t0, t1: S.tl.t1 };
    else {
      for (const hh of TL.insHits || []) if (x >= hh[0] - 1 && x <= hh[2] + 1 && y >= hh[1] && y <= hh[3] + 1) { focusInstruction(hh[4]); }
      tdrag = { kind: "scrub" };
      seek(tToX(x, r.width, true));
    }
  });
  tl.addEventListener("pointermove", (ev) => {
    const r = tl.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    if (tdrag && tdrag.kind === "scrub") seek(tToX(x, r.width, true));
    else if (tdrag && tdrag.kind === "pan") {
      const span = tdrag.t1 - tdrag.t0;
      const dt = -(x - tdrag.x) / (r.width - TL.gutter - 8) * span;
      S.tl.t0 = tdrag.t0 + dt; S.tl.t1 = tdrag.t1 + dt; S.tlDirty = true;
    } else {
      let hit = null;
      for (const hh of TL.insHits || []) if (x >= hh[0] - 1 && x <= hh[2] + 1 && y >= hh[1] && y <= hh[3] + 1) hit = hh[4];
      const tip = $("tip");
      if (hit !== null) {
        const ins = M.gir.program.instructions[hit];
        tip.textContent = `line ${ins.line}: ${ins.text}\nclick to open where it runs`;
        tip.style.display = "block";
        tip.style.left = Math.min(window.innerWidth - 370, ev.clientX + 14) + "px";
        tip.style.top = (ev.clientY - 40) + "px";
      } else tip.style.display = "none";
    }
  });
  tl.addEventListener("pointerup", () => { tdrag = null; });
  tl.addEventListener("dblclick", (ev) => {
    const r = tl.getBoundingClientRect();
    const y = ev.clientY - r.top;
    const top = TL.ruler + 2 + TL.progH;
    const row = TL.rows[Math.floor((y - top + TL.scroll) / TL.rowH)];
    if (row && row.path && y > top) go(row.path);
  });
  tl.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = tl.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    if (x < TL.gutter || ev.shiftKey) {
      const top = TL.ruler + 2 + TL.progH;
      const maxScroll = Math.max(0, TL.rows.length * TL.rowH - (r.height - top));
      TL.scroll = Math.max(0, Math.min(maxScroll, TL.scroll + ev.deltaY));
    } else {
      const at = tToX(x, r.width, false);
      const k = Math.exp(ev.deltaY * 0.0015);
      let t0 = at - (at - S.tl.t0) * k, t1 = at + (S.tl.t1 - at) * k;
      if (t1 - t0 < 20) { const c = (t0 + t1) / 2; t0 = c - 10; t1 = c + 10; }
      S.tl.t0 = t0; S.tl.t1 = t1;
    }
    S.tlDirty = true;
  }, { passive: false });
  function tToX(x, width, clamp) {
    const t = S.tl.t0 + (x - TL.gutter) / (width - TL.gutter - 8) * (S.tl.t1 - S.tl.t0);
    return clamp ? Math.max(0, Math.min(M.makespan, t)) : t;
  }

  // the split between the stage and the timeline
  $("split").addEventListener("pointerdown", (ev) => {
    const el = $("split");
    el.setPointerCapture(ev.pointerId);
    const move = (e) => {
      const h = Math.max(120, Math.min(window.innerHeight - 200, window.innerHeight - e.clientY));
      document.getElementById("app").style.setProperty("--tl-h", h + "px");
      S.dirty = true;
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", () => el.removeEventListener("pointermove", move), { once: true });
  });

  $("bPlay").onclick = () => { S.playing = !S.playing; if (S.t >= M.makespan) S.t = 0; $("bPlay").textContent = S.playing ? "⏸" : "▶"; };
  $("bStart").onclick = () => seek(0);
  $("bStep").onclick = () => stepEvent(+1);
  $("rate").onchange = () => { S.rate = +$("rate").value; };
  $("bUp").onclick = up;
  $("bFit").onclick = fit;
  $("bIons").onclick = () => { S.showIons = !S.showIons; $("bIons").className = S.showIons ? "on" : ""; S.dirty = true; };
  function closeLibrary() { $("library").classList.remove("open"); $("bLib").className = ""; }
  $("bLib").onclick = () => { const lib = $("library"); lib.classList.toggle("open"); $("bLib").className = lib.classList.contains("open") ? "on" : ""; if (lib.classList.contains("open")) renderLibrary(); };
  $("checksBadge").onclick = () => { $("checks").scrollIntoView(); for (const el of document.querySelectorAll(".ck")) if (el.querySelector(".fail")) el.classList.add("open"); };

  function stepEvent(dir) {
    const evs = M.node(S.path).leaf ? M.eventsOf(S.path) : M.subtreeEvents(S.path);
    const i = lowerBound(evs, S.t + (dir > 0 ? 1 : -1), (e) => e[1]);
    const e = dir > 0 ? evs[i] : evs[i - 1];
    if (e) seek(e[1]);
  }

  window.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
    if (editing()) return;
    if (ev.key === " ") { ev.preventDefault(); $("bPlay").onclick(); }
    else if (ev.key === "Escape") { if ($("library").classList.contains("open")) $("bLib").onclick(); else up(); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); stepEvent(+1); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); stepEvent(-1); }
    else if (ev.key === "Home") seek(0);
    else if (ev.key === "f" || ev.key === "F") fit();
    else if (ev.key === "l" || ev.key === "L") $("bLib").onclick();
    else if (ev.key === "i" || ev.key === "I") $("bIons").onclick();
    else if (ev.key === "Enter" && S.sel && S.sel.kind === "inst") go(S.sel.path);
  });
  window.addEventListener("resize", () => { DPR = window.devicePixelRatio || 1; fit(); });

  // ------------------------------------------------------------------ the loop

  let last = performance.now(), lastPanel = 0, lastHash = 0;
  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (S.playing) {
      S.t += dt * S.rate;
      if (S.t >= M.makespan) { S.t = M.makespan; S.playing = false; $("bPlay").textContent = "▶"; }
      if (S.t > S.tl.t1) { const span = S.tl.t1 - S.tl.t0; S.tl.t0 = S.t - span * 0.1; S.tl.t1 = S.tl.t0 + span; }
      S.dirty = true;
    }
    if (S.dirty && editing()) {
      window.GADGET_EDITOR.draw();
      S.dirty = false;
    } else if (S.dirty) {
      drawStage();
      drawTimeline();
      $("clock").innerHTML = `${fmtTime(S.t)} <small>/ ${fmtTime(M.makespan)}</small>`;
      // the level and instance inspectors quote live counts, so they follow the clock too
      if (now - lastPanel > 180) { updateProgram(); updateTreeDots(); if (!S.sel || S.sel.kind === "inst") renderInspector(); lastPanel = now; }
      if (now - lastHash > 1000) { writeHash(); lastHash = now; }
      S.dirty = false; S.tlDirty = false;
    } else if (S.tlDirty) { drawTimeline(); S.tlDirty = false; }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ boot

  readHash();
  $("designTitle").textContent = `${M.gir.program.title || M.masters[M.top].title} · ${M.gir.stats ? M.gir.stats.ions.toLocaleString() + " ions" : ""}`;
  document.title = `${M.gir.program.title || M.gir.program.name} — QCCD gadgets`;
  $("rate").value = String(S.rate);
  $("bIons").className = S.showIons ? "on" : "";
  S.tl = { t0: 0, t1: Math.max(1, M.makespan) };
  for (const p of M.ancestors(S.path)) treeOpen.add(p);
  renderChecks();
  renderSignoff();
  renderProgram();
  legend();
  go(S.path);
  fit();
  requestAnimationFrame(frame);

  // the handle a headless test drives
  window.GADGETS = { M, S, D, go, seek, select, fit, leafIons, drawStage, drawTimeline,
    WIRES, MSGS, FRAMES, CONTROL, messagesOn, archiveAt, frameText, guardOf, guardText,
    portXY, portCache, COLOR, FAMILY, ROLE, CATS, styleOf, shapeD, shapeSvg, roundRect, haloText, clip, sizeCanvas, stage, sctx,
    X, Y, fmtTime, escapeHtml, renderLibrary, renderInspector, renderTree, crumbs,
    redraw: () => { S.dirty = true; },
    ready: () => Object.keys(D.leafFiles || {}).every((m) => M.leafData[m]) };
  if (window.GADGET_EDITOR) window.GADGET_EDITOR.attach(window.GADGETS);
})();
