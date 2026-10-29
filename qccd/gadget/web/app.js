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
  const { Model, LeafSim, lowerBound, naturalCmp, geomOf, ionRadius } = window.GadgetCore;
  const D = JSON.parse(document.getElementById("gdata").textContent);
  // A FOLDED RUN (GIR 0.3, qccd/gadget/fold.py) is hours of machine time in a few megabytes:
  // each gadget once, every call of it a box.  The page draws a WINDOW of it, expanded around
  // the playhead (fold.js), and expands the next one when the playhead leaves it -- every
  // view below reads `M`, which is that window.
  let foldStale = false;
  const FOLD = (D.gir && D.gir.schema === "qccd.gir/0.3" && window.GadgetFold)
    ? new window.GadgetFold.Folded(D.gir, D.macroFiles, () => { foldStale = true; }) : null;
  const FOLD_SPAN = 4e6;
  if (FOLD) D.gir = FOLD.window(0, FOLD_SPAN);
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
      case "dome": {
        // a domed roof on a low building: the observatory, where a product is measured
        const r = Math.min(w * 0.32, h * 0.42), cx = x + w / 2, wall = y + r;
        return `M${f(x)} ${f(y + h)}V${f(wall)}H${f(cx - r)}A${f(r)} ${f(r)} 0 0 1 ${f(cx + r)} ${f(wall)}` +
               `H${f(x + w)}V${f(y + h)}Z`;
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
      case "diamond": {                // a lozenge: both ends pointed
        const q = Math.min(w * 0.22, h * 0.6);
        return `M${f(x + q)} ${f(y)}H${f(x + w - q)}L${f(x + w)} ${f(y + h / 2)}L${f(x + w - q)} ${f(y + h)}H${f(x + q)}L${f(x)} ${f(y + h / 2)}Z`;
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
      case "ward": {
        // a block with a raised centre: the memory, where a resting block is cared for
        const q = Math.min(h * 0.22, w * 0.12);
        return `M${f(x)} ${f(y + q)}H${f(x + w * 0.36)}V${f(y)}H${f(x + w * 0.64)}V${f(y + q)}` +
               `H${f(x + w)}V${f(y + h)}H${f(x)}Z`;
      }
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
    if (shape === "ward") {           // a cross in the raised centre
      const cx = x + w / 2, cy = y + Math.min(h * 0.22, w * 0.12) + h * 0.2, a = Math.min(w, h) * 0.1;
      return `M${f(cx - a)} ${f(cy)}H${f(cx + a)}M${f(cx)} ${f(cy - a)}V${f(cy + a)}`;
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
                  ["ppm.", "ppm"],
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
  function indexMessages() {
    for (const k in msgByWire) delete msgByWire[k];
    for (const m of MSGS) (msgByWire[m[1]] || (msgByWire[m[1]] = [])).push(m);
    for (const k in msgByWire) msgByWire[k].sort((a, b) => a[6] - b[6]);
  }
  indexMessages();
  function messagesOn(wire, t) {
    const ms = msgByWire[wire];
    if (!ms) return [];
    return ms.filter((m) => m[6] <= t && t <= m[7]);
  }

  // THE DECODER IS CALLED BY AN INSTRUCTION.  A place that measures keeps its syndrome in
  // its readout buffer; `decode` is what sends it -- down the wire from that place to the
  // decoder, and the frame the decoder decides down its wire to the classical memory.  While
  // a `decode` runs, exactly those wires are lit, with the places at their ends, and every
  // other wire steps back.
  //
  // And the playhead SLOWS through it.  The whole exchange is a few microseconds of a
  // program measured in hundreds of milliseconds, so at any rate that shows ions moving it
  // is over inside one frame: a highlight that is honest about time would never be seen.
  // `DECODE_WALL_S` is how long one `decode` takes on screen; the clock says it is slowed.
  const DECODE_WALL_S = 2.5;
  const DECODES = [];
  const DECODE_OF = {};
  function buildDecodes() {
    DECODES.length = 0;
    for (const k in DECODE_OF) delete DECODE_OF[k];
    for (const ins of M.gir.program.instructions) {
      if (ins.kind !== "decode") continue;
      const ms = MSGS.filter((m) => m[10] === ins.id);
      if (!ms.length) continue;
      const sp = M.insSpan[ins.id] || [Infinity, -Infinity];
      const d = {
        id: ins.id, text: ins.text,
        t0: Math.min(sp[0], ...ms.map((m) => m[6])), t1: Math.max(sp[1], ...ms.map((m) => m[7])),
        nets: new Set(ms.map((m) => m[1])),
        // the places at either end of a lit wire, and which of them are SENDING a syndrome
        places: new Set(ms.flatMap((m) => [m[2], m[4]])),
        senders: new Set(ms.filter((m) => m[9] === "syndrome").map((m) => m[2])),
        // which way the bits go on each wire, from the first message on it
        from: {},
        windows: (M.gir.windows || []).filter((w) => w.decoded_by === ins.id),
        bits: ms.filter((m) => m[9] === "syndrome").reduce((a, m) => a + (+m[8] || 0), 0),
        // where to put the playhead to see it: the first syndromes half-way down their wires
        show: 0,
      };
      for (const m of ms) if (!(m[1] in d.from)) d.from[m[1]] = m[2];
      const first = ms.filter((m) => m[9] === "syndrome").sort((a, b) => a[6] - b[6])[0] || ms[0];
      d.show = first[6] + 0.5 * (first[7] - first[6]);
      DECODES.push(d);
    }
    DECODES.sort((a, b) => a.t0 - b.t0);
    for (const d of DECODES) DECODE_OF[d.id] = d;
  }
  buildDecodes();
  // what is lit at time t: the union over the `decode` instructions running then
  function litAt(t) {
    const on = DECODES.filter((d) => d.t0 <= t && t <= d.t1);
    if (!on.length) return null;
    const lit = { list: on, nets: new Set(), places: new Set(), senders: new Set(), from: {},
                  sent: {} };
    for (const d of on) {
      for (const n of d.nets) lit.nets.add(n);
      for (const p of d.places) lit.places.add(p);
      for (const p of d.senders) lit.senders.add(p);
      Object.assign(lit.from, d.from);
      for (const w of d.windows) lit.sent[w.place] = (lit.sent[w.place] || 0) + (+w.bits || 0);
    }
    return lit;
  }
  // the next `decode` still to finish after t, for the playhead
  function nextDecode(t) {
    for (const d of DECODES) if (d.t1 > t + 1e-9) return d;
    return null;
  }
  const WIRE_LIT = (CATS.wire && CATS.wire.lit) || "#b45309";
  const WIRE_GLOW = (CATS.wire && CATS.wire.glow) || "#fcd34d";
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
  // ONE derivation of the trap axis, in `core.js::geomOf`.  It lived here, and `LeafSim`
  // needs the same numbers to place an ion in its slot -- two derivations of one axis
  // would be two pictures of one trap, and the ions would sit in the second while the
  // capsules were drawn along the first.
  function deviceGeom(mname) {
    if (geomCache[mname]) return geomCache[mname];
    const d = M.leafData[mname];
    if (!d) return null;
    const sim = sims[mname];
    return (geomCache[mname] = (sim && sim.geom) || geomOf(d.device));
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
    // the `decode` instructions running now (see DECODES), and how much the playhead is
    // being slowed through them
    lit: null, slowed: 0,
    showClassical: shownClassical(),
    cam: { s: 1, x: 0, y: 0 }, tl: { t0: 0, t1: Math.max(1, M.makespan) },
  };

  // THE READER MAY PUT THE CLASSICAL HALF AWAY: the decoder, the classical memory and the
  // wires to them, and a decode's glow with them -- together, because a wire drawn to a
  // place that is not there reads as a bug.  One setting with the studio's "Classical" box
  // (`qccd/site/qec_cycle.js`, the same key), remembered per viewer where the browser keeps
  // anything at all.  Only the drawing changes: the programme, the listing and the checks
  // still say what the decoder does.
  function shownClassical() {
    try { return window.localStorage.getItem("qccd.classical") !== "0"; } catch (e) { return true; }
  }
  const CLASSICAL = new Set(["decoder", "archive"]);
  const putAway = (cm) => !S.showClassical && CLASSICAL.has(cm.family);
  const sims = {};
  // before the leaves arrive: a page with its leaves inline (a tutorial figure) hands them
  // over right here, not after the page has loaded
  const portCache = new Map();
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
    S.lit = litAt(S.t);
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
      if (cm.family === "road" || putAway(cm)) continue;
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
      if (putAway(cm)) continue;
      const cx = ox + inst.x, cy = oy + inst.y;
      const r = rectOf(cpath, cx, cy);
      const rw = r[2] - r[0], rh = r[3] - r[1];
      if (r[2] < 0 || r[0] > w || r[3] < 0 || r[1] > h || rw < 2) continue;
      const sty = styleOf(cm);
      const busy = cm.kind === "leaf" ? M.eventAt(cpath, t) : null;
      if (cm.family === "road") {
        // a junction is its own device -- a centre, four arms, four stubs -- and once the zoom
        // gives it room it is drawn as one, its ions where its pass's TSIR has them
        if (cm.kind === "leaf" && rw > 22 && M.leafData[cm.name]) {
          drawLeafInterior(ctx, cpath, cx, cy, depth, rw);
          if (depth === 0) hits.push([r[0], r[1], r[2], r[3], "inst", { path: cpath }]);
          continue;
        }
        // farther out it is a point on a street: a dot, lit while it passes a bundle
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
      // a place at the end of a lit wire glows: the one sending its syndrome, the decoder
      // working on it, the memory taking the frame
      const lit = S.showClassical && S.lit && S.lit.places.has(cpath);
      if (lit) {
        ctx.save();
        ctx.setLineDash([]);
        ctx.globalAlpha = 0.85;
        ctx.strokeStyle = WIRE_GLOW;
        ctx.lineWidth = depth === 0 ? 9 : 5;
        ctx.stroke(outline);
        ctx.restore();
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
      if (lit) {
        ctx.strokeStyle = WIRE_LIT;
        ctx.lineWidth = depth === 0 ? 2.2 : 1.4;
        ctx.stroke(outline);
        if (depth === 0 && S.lit.senders.has(cpath)) {
          const bits = S.lit.sent[cpath] || 0;
          labels.push(() => {
            ctx.font = "600 11px ui-sans-serif, system-ui, sans-serif";
            const text = `sends ${bits} bits`;
            const tw = ctx.measureText(text).width;
            haloText(ctx, text, (r[0] + r[2]) / 2 - tw / 2, r[3] + 27, WIRE_LIT);
          });
        }
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
      if (ch.kind === "wire") { if (S.showClassical) drawWire(ctx, path, ch, pts, ox, oy, depth); continue; }
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
      // A ROAD IS ITS OWN DEVICE: `chan<L>`, L sites one lattice unit apart along its path,
      // a stub at each end on the doors it joins.  Its sites are drawn once the zoom puts
      // them apart, and an ion on it is where that conveyor's TSIR has it at the ion's own
      // local time -- the replay (`LeafSim`) that places an ion inside a place.
      const rec = M.gir.nets[net];
      const lengths = [];
      let total = 0;
      for (let i = 1; i < pts.length; i++) {
        const d = Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]);
        lengths.push(d);
        total += d;
      }
      const at = (d) => {
        let k = 0;
        while (k < lengths.length - 1 && d > lengths[k]) { d -= lengths[k]; k++; }
        const p0 = pts[k], p1 = pts[k + 1];
        const u = lengths[k] > 0 ? Math.max(0, Math.min(1, d / lengths[k])) : 0;
        return [X(ox + p0[0] + (p1[0] - p0[0]) * u), Y(oy + p0[1] + (p1[1] - p0[1]) * u)];
      };
      const L = rec ? Math.max(1, rec[4]) : 0;
      const unit = L ? total / (L + 1) : 0;        // one site, in floor units (1 when the path is L + 1 long)
      // the road's nodes, from the near end of its polyline: 0 a stub, 1..L its sites, L + 1
      // the other stub -- each leg its own whole number of steps, a site at every corner
      // (leaves/transport.py::road), so a bent road is drawn as the device it is
      const nodes = L ? roadNodes(pts, rec, L, ox, oy) : [];
      if (L && depth === 0 && S.cam.s * unit >= 7) {
        const sr = Math.max(1.2, Math.min(3, S.cam.s * unit * 0.12));
        ctx.fillStyle = "#ffffff";
        ctx.strokeStyle = COLOR.rail;
        ctx.lineWidth = 1;
        for (let k = 1; k <= L; k++) {
          const [x, y] = nodes[k];
          ctx.beginPath(); ctx.rect(x - sr, y - sr, 2 * sr, 2 * sr); ctx.fill(); ctx.stroke();
        }
      }
      const carries = M.carriesOn(net, S.t);
      if (!carries.length) continue;
      const sim = sims["chan" + (rec && rec.length > 5 ? rec[5] : L)];
      for (const c of carries) {
        const forward = c[2] === rec[0];
        const radius = depth === 0 ? Math.max(2.2, Math.min(5, S.cam.s * 0.32)) : Math.max(1, Math.min(3, S.cam.s * 0.25));
        const draw = (gid, x, y) => {
          const col = S.follow === gid ? "#000000" : (ROLE[c[8]] || COLOR.teal);
          mark(col, x, y, S.follow === gid ? radius * 1.8 : radius);
          if (depth <= 1) hits.push([x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2, "ion", { gid, carry: c }]);
        };
        const nodeXY = (idx) => {
          const i = forward ? idx : L + 1 - idx;
          const a = Math.max(0, Math.min(L + 1, Math.floor(i))), b = Math.min(L + 1, a + 1), f = i - a;
          return [nodes[a][0] + (nodes[b][0] - nodes[a][0]) * f, nodes[a][1] + (nodes[b][1] - nodes[a][1]) * f];
        };
        // THE ROAD'S OWN PROGRAM: the carry compiled with its whole bundle (roads.py, what H1
        // judged) -- one class a cycle, blocked ions holding -- replayed at this instant
        const run = c.run || (!FOLD && M.gir.road_runs && M.gir.road_programs ? M.gir.road_programs[M.gir.road_runs[c[0]]] : null);
        if (run && nodes.length) {
          for (const r of roadState(c, run, S.t)) {
            const [x, y] = nodeXY(r.idx);
            draw(r.gid, x, y);
          }
          continue;
        }
        // (a page built before the road programs: each ion by the conveyor's one-ion program)
        const queued = [];
        const place = (ion, d) => {
          const [x, y] = at(forward ? d : total - d);
          draw(ion.gid, x, y);
        };
        for (const ion of M.carryIons(c, S.t, L)) {
          if (ion.queued) { queued.push(ion); continue; }
          const local = S.t - (c[4] + c[5][ion.j]);
          const st = sim ? sim.state("carry", local) : null;
          place(ion, st ? (st.xy[0][0] + 1) * unit : ion.f * total);
        }
        queued.forEach((ion, q) => place(ion, Math.max(1, L - q) * unit));
      }
    }
  }

  // A road's nodes in canvas space, from the near end of its polyline (`pts`): each straight
  // leg (collinear pieces merged) split into its steps, the steps the shape names (`rec[5]`,
  // e.g. "3R10") or, for a straight road, its L + 1.  0 and L + 1 are the stubs at the doors.
  function roadNodes(pts, rec, L, ox, oy) {
    const legs = [];
    for (let i = 1; i < pts.length; i++) {
      const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
      if (Math.abs(dx) + Math.abs(dy) < 1e-9) continue;
      const last = legs[legs.length - 1];
      if (last && Math.abs(last.dx * dy - last.dy * dx) < 1e-9 && last.dx * dx + last.dy * dy > 0) {
        last.dx += dx; last.dy += dy;
      } else legs.push({ x: pts[i - 1][0], y: pts[i - 1][1], dx, dy });
    }
    let steps = rec && rec.length > 5 ? (String(rec[5]).match(/\d+/g) || []).map(Number) : [L + 1];
    if (steps.length !== legs.length || steps.reduce((a, b) => a + b, 0) !== L + 1) {
      // not the legs the shape names (a drawing edited since): even steps along the path
      const all = legs.reduce((a, l) => a + Math.hypot(l.dx, l.dy), 0);
      steps = legs.map((l) => Math.hypot(l.dx, l.dy) / all * (L + 1));
    }
    const out = [[X(ox + pts[0][0]), Y(oy + pts[0][1])]];
    legs.forEach((l, i) => {
      const n = steps[i];
      for (let s = 1; s <= Math.round(n); s++) {
        const f = s / Math.round(n);
        out.push([X(ox + l.x + l.dx * f), Y(oy + l.y + l.dy * f)]);
      }
    });
    while (out.length < L + 2) out.push(out[out.length - 1]);
    return out.slice(0, L + 2);
  }

  // Every ion of carry `c` at time t on its road, from the road's compiled program `run`
  // (`roads.run_json`): its node index (fractional while it moves), shown from when the op
  // on the near side puts it on the stub until the far side takes it off.
  function roadState(c, run, t) {
    if (!run._st) {
      const pos = new Array(run.n).fill(0);
      run._st = run.cycles.map((cy) => { const before = pos.slice(); for (const [k, , b] of cy[3]) pos[k] = b; return before; });
      run._end = pos.slice();
    }
    const tl = t - c[4];
    const k = lowerBound(run.cycles, tl + 1e-9, (cy) => cy[0]) - 1;
    const before = k < 0 ? new Array(run.n).fill(0) : run._st[k];
    const cy = k >= 0 ? run.cycles[k] : null;
    const moving = {};
    if (cy && tl < cy[1]) {
      const f = cy[1] > cy[0] ? Math.max(0, Math.min(1, (tl - cy[0]) / (cy[1] - cy[0]))) : 1;
      for (const [ion, a, b] of cy[3]) moving[ion] = a + (b - a) * f;
    }
    const after = k >= 0 && cy && tl >= cy[1] ? (k + 1 < run._st.length ? run._st[k + 1] : run._end) : before;
    const out = [];
    for (let i = 0; i < run.n; i++) {
      if (t < c[4] + c[5][i] || t >= c[4] + c[12][i]) continue;
      out.push({ gid: c[7][i], idx: i in moving ? moving[i] : after[i] });
    }
    return out;
  }

  // A classical wire: dashed and dark, with a square packet per message crossing it now.
  // While a `decode` sends bits down it, it is LIT: a glow under it, the dashes in the lit
  // colour and marching the way the bits go, the packets bigger and saying how many bits
  // they carry.  The wires that decode does not use step back while it runs.
  function drawWire(ctx, path, ch, pts, ox, oy, depth) {
    const style = CATS.wire || { stroke: "#065f46" };
    const net = path + "/" + ch.name;
    const lit = !!(S.lit && S.lit.nets.has(net));
    const quiet = !!(S.lit && !lit);
    const base = depth === 0 ? Math.max(1, Math.min(2.2, S.cam.s * 0.14)) : 0.8;
    const dash = [Math.max(3, S.cam.s * 0.5), Math.max(2, S.cam.s * 0.35)];
    const trace = () => {
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(X(ox + p[0]), Y(oy + p[1])) : ctx.moveTo(X(ox + p[0]), Y(oy + p[1]))));
    };
    ctx.save();
    if (lit) {
      ctx.strokeStyle = WIRE_GLOW;
      ctx.globalAlpha = 0.75;
      ctx.lineWidth = base * 5 + 3;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      trace();
      ctx.stroke();
    }
    ctx.strokeStyle = lit ? WIRE_LIT : style.stroke;
    ctx.globalAlpha = lit ? 1 : quiet ? 0.28 : depth === 0 ? 0.75 : 0.5;
    ctx.lineWidth = lit ? base * 2 + 0.6 : base;
    ctx.setLineDash(dash);
    if (lit) {
      // the dashes march from the sending end: six dash periods over one `decode`, by the
      // clock rather than the wall, so a paused frame and a screenshot agree
      const d = S.lit.list[0];
      const f = (S.t - d.t0) / Math.max(1e-9, d.t1 - d.t0);
      const period = dash[0] + dash[1];
      const fromA = S.lit.from[net] === ch.a.split(".")[0];
      ctx.lineDashOffset = (fromA ? -1 : 1) * f * period * 6;
    }
    trace();
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
    // two windows a place sends at once cross its wire as one burst: drawn as one packet
    // carrying both, so the label says what is really on the wire
    const bursts = new Map();
    for (const msg of live) {
      const key = `${msg[2]}|${msg[6]}|${msg[7]}`;
      const b = bursts.get(key);
      if (b) { b.n++; b.bits += +msg[8] || 0; } else bursts.set(key, { msg, n: 1, bits: +msg[8] || 0 });
    }
    for (const { msg, n: count, bits } of bursts.values()) {
      const span = Math.max(1e-6, msg[7] - msg[6]);
      let f = (S.t - msg[6]) / span;
      const fromA = msg[2] === ch.a.split(".")[0];
      if (!fromA) f = 1 - f;
      let d = f * total, k = 0;
      while (k < lengths.length - 1 && d > lengths[k]) { d -= lengths[k]; k++; }
      const p0 = pts[k], p1 = pts[k + 1];
      const u = lengths[k] > 0 ? Math.min(1, d / lengths[k]) : 0;
      const x = X(ox + p0[0] + (p1[0] - p0[0]) * u), y = Y(oy + p0[1] + (p1[1] - p0[1]) * u);
      const r = Math.max(2, Math.min(5, S.cam.s * 0.3)) * (lit ? 1.6 : 1);
      if (lit) {
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(x - r - 1.5, y - r * 0.7 - 1.5, 2 * r + 3, 1.4 * r + 3);
      }
      ctx.fillStyle = lit ? WIRE_LIT : style.stroke;
      ctx.fillRect(x - r, y - r * 0.7, 2 * r, 1.4 * r);       // a packet, not an ion
      if (lit && depth === 0 && S.cam.s > 2.5) {
        ctx.font = "600 10px ui-monospace, monospace";
        haloText(ctx, `${bits} bit${bits === 1 ? "" : "s"}` + (count > 1 ? ` · ${count} windows` : ""),
                 x + r + 4, y - r - 2, WIRE_LIT);
      }
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
        out.push({ name, gid, xy: st.xy[i], role: roleOf(d, m, name), active: st.active.includes(i),
                   // how the mark is sized and whether it is threading past a neighbour
                   p: (st.live && st.live[name]) || null });
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

  // WHERE `spread` USED TO BE.  It pushed apart ions whose coordinates agreed to three
  // decimal places, by a fixed 0.32 along x, re-indexing the whole stack each time it ran.
  // Three things were wrong with that and all three were visible: the test was an exact
  // one, so the correction switched on and off between adjacent animation frames (23,496
  // such flicks over `site/gadgets/demo`, the worst of them 22.7 lattice units); the
  // offset was along x whatever direction the trap ran in; and it did nothing at all for
  // the far commoner case of two ions merely CLOSE together, which is every approach to an
  // occupied trap and every exchange along a rail -- 41% of sampled instants drew two ions
  // through each other, the worst pair at a separation of exactly zero.
  //
  // Ions now arrive here already in their slots: `LeafSim.state` places the whole stage
  // through `transit.js`, the same law the studio stage runs, so the order is carried
  // forward across instructions and each ion interpolates from the slot it leaves to the
  // slot it arrives in.  Nothing downstream needs a `dx`, and there is no second pass.
  // The mark's SIZE is the other half of it, and that is `core.js::ionRadius` -- kept
  // there so `tests/gadget_stage.mjs` can measure what this canvas draws rather than a
  // second opinion about it.  A pitch arrives in device units, so it scales by the camera
  // exactly as a position does.
  const ionR = (ion, mk, s) => ionRadius(ion.p, mk.rIon, mk.rRest, s, ion.active);

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
        ctx.ellipse(X(ox + ion.xy[0]), Y(oy + ion.xy[1]),
                    mk.wellRx, mk.wellRy, 0, 0, 2 * Math.PI);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    // -- the ions: white-outlined discs, coloured by what is being done to them
    const kind = st.step ? st.step.step.ins.type : null;
    for (const ion of st.ions) {
      const x = X(ox + ion.xy[0]), y = Y(oy + ion.xy[1]);
      const r = ionR(ion, mk, S.cam.s);
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
        // a mark squeezed to make room shrinks whole, halo and all (the studio's rule)
        const r0 = ion.p && ion.p.room ? ionRadius(ion.p, mk.rIon, mk.rRest, S.cam.s, ion.active, true) : r;
        ctx.lineWidth = r < r0 ? mk.swHalo * r / r0 : mk.swHalo;
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
        const r = ionR(ion, mk, S.cam.s);
        if (r < 6) continue;
        ctx.font = "700 " + Math.min(0.66 * r, 1.5 * r / Math.max(2, label.length)).toFixed(1) +
                   "px ui-sans-serif, system-ui, sans-serif";
        ctx.fillText(label, X(ox + ion.xy[0]), Y(oy + ion.xy[1]));
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
      for (const ion of st.ions) ctx.fillText(ion.name, X(ion.xy[0]) + 6, Y(ion.xy[1]) - 6);
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
    ctx.fillText(FOLD ? "program (folded)" : "program", 10, y0 + 14);
    if (FOLD) {
      // the whole run, at every zoom: each call the program makes is a box, named by its
      // gate; the instructions drawn over it are the window's, the stretch under the ruler
      ctx.save();
      ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
      for (const c of FOLD.g.calls) {
        const a = c.t0, b = c.t0 + FOLD.spanOf(c);
        if (b < S.tl.t0 || a > S.tl.t1) continue;
        const x = tx(a), x1 = Math.max(x + 1, tx(b));
        ctx.fillStyle = "rgba(30, 39, 97, 0.10)";
        ctx.fillRect(x, y0 + 1, x1 - x, TL.progH - 4);
        ctx.fillStyle = "rgba(30, 39, 97, 0.45)";
        ctx.fillRect(x, y0 + 1, 1, TL.progH - 4);
        if (x1 - x > 60) { ctx.fillStyle = COLOR.navy; ctx.fillText(clip(ctx, c.gate, x1 - x - 6), x + 4, y0 + TL.progH - 6); }
      }
      const win = M.gir.window;
      if (win) {
        ctx.fillStyle = "rgba(15, 118, 110, 0.55)";
        ctx.fillRect(tx(Math.max(win.a, S.tl.t0)), TL.ruler - 2, Math.max(1, tx(Math.min(win.b, S.tl.t1)) - tx(Math.max(win.a, S.tl.t0))), 2);
      }
      ctx.restore();
    }
    const lanes = [];
    const laneH = 4;
    const insHits = [];
    M.insSpan.forEach((sp, i) => {
      if (!sp || sp[1] < S.tl.t0 || sp[0] > S.tl.t1) return;
      let lane = lanes.findIndex((end) => end <= sp[0]);
      if (lane < 0) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = sp[1];
      if (lane >= Math.floor((TL.progH - 4) / (laneH + 1))) return;
      const ins = M.gir.program.instructions[i];
      // a `decode` is microseconds long: it gets a mark wide enough to see and to click
      const x = tx(sp[0]), x1 = Math.max(x + (ins.kind === "decode" ? 4 : 1.5), tx(sp[1]));
      ctx.fillStyle = S.hoverIns === i ? COLOR.accent
        : ins.kind === "decode" && S.lit && S.lit.list.some((d) => d.id === i) ? WIRE_LIT
        : kindColor(ins.kind);
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
                  inject: "injection", store: "zone", decode: "decoder", ppm: "ppm" }[kind];
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

  // The compositional sign-off (docs/ARCHITECTURE.md §1, §6): for a program the flat
  // check cannot enumerate, five parts, each a check that ran.
  function compositionalHtml(c) {
    const parts = c.parts || {};
    let h = `<div class="so ${c.passed ? "pass" : "fail"}">${c.passed ? "✓ signed off in parts: every part checked and held" : "✗ a part of the sign-off did not hold"}</div>`;
    const row = (ok, text) => `<div class="sol ${ok ? "pass" : "fail"}">${ok ? "✓" : "✗"} ${text}</div>`;
    if (parts.places) h += row(parts.places.passed, "every op the schedule runs is a verified place op (G9)");
    if (parts.gadgets && parts.gadgets.rows.length)
      h += row(parts.gadgets.passed, "the π/4 gadgets are their rotations in every branch: " +
        parts.gadgets.rows.map((g) => `${escapeHtml(g.gate)} = ${escapeHtml(g.contract)} (${escapeHtml(g.correction || "")})`).join("; "));
    if (parts.gates && parts.gates.rows.length)
      h += row(parts.gates.passed, (parts.gates.rows.some((g) => g.contract === null)
        ? "every gate called is its contract in every branch, or is expanded: "
        : "every gate called is its contract in every branch: ") +
        (() => {
          const named = parts.gates.rows.filter((g) => g.contract !== null);
          const open = parts.gates.rows.filter((g) => g.contract === null);
          const txt = named.map((g) => `${escapeHtml(g.gate)} = ${escapeHtml(g.contract)} ` +
            `(${(g.branches || []).length} branch${(g.branches || []).length === 1 ? "" : "es"}, ${parts.gates.calls[g.gate] || 0} call${parts.gates.calls[g.gate] === 1 ? "" : "s"})`);
          if (open.length > 3) {
            const calls = open.reduce((n, g) => n + (parts.gates.calls[g.gate] || 0), 0);
            txt.push(`${open.length} gates expanded, without contracts (${calls} calls: ${open.slice(0, 3).map((g) => escapeHtml(g.gate)).join(", ")} …)`);
          } else {
            for (const g of open) txt.push(`${escapeHtml(g.gate)}: expanded, no contract (${parts.gates.calls[g.gate] || 0} call${parts.gates.calls[g.gate] === 1 ? "" : "s"})`);
          }
          return txt.join("; ");
        })());
    if (parts.composition) h += row(parts.composition.passed, `the floorplan and the schedule obey every machine rule (${(parts.composition.held || []).length} hold)`);
    if (parts.semantics && parts.semantics.kind === "distribution") {
      // decided against the program's own source: the FormalRV .prog, simulated as written
      const s = parts.semantics;
      const dist = Object.entries(s.gadget || {}).map(([k, v]) => `${escapeHtml(k)}: ${(+v).toFixed(3)}`).join(", ");
      h += row(s.passed, s.passed
        ? `its results have exactly the FormalRV .prog's distribution (${dist}), every branch: at most ${s.peak_branches} live at once`
        : `its results differ from the .prog's (by ${(+s.worst_difference).toExponential(2)})`);
    } else if (parts.semantics && parts.semantics.method && parts.semantics.method.startsWith("symbolic")) {
      // every outcome as an affine expression of the random ones: all branches at once
      const s = parts.semantics;
      const det = (s.deterministic || []).map((k) => `${escapeHtml(k)} = ${escapeHtml(s.outcomes[k])}`);
      h += row(s.passed, s.passed
        ? `the program, decided symbolically on its expansion (every branch at once): ${s.random} random outcome${s.random === 1 ? "" : "s"}` +
          (det.length ? `; deterministic: ${det.join(", ")}` : "")
        : `the program's semantics was not decided: ${escapeHtml(s.why || "")}`);
    } else if (parts.semantics) {
      const d = parts.semantics.distribution || [];
      h += row(parts.semantics.passed, `the program, every call as its contract, gives: ` +
        d.map((x) => `${Object.entries(x.outcomes).map(([k, v]) => `${escapeHtml(k)}=${v}`).join(" ")} with probability ${(+x.p).toFixed(3)}`).join("; "));
    }
    return h;
  }

  function renderSignoff() {
    const box = $("verif");
    const r = D.signoff;
    if (!box) return;
    if (!r) { box.style.display = "none"; return; }
    if (r.passed == null && r.skipped && D.compositional) {
      $("verifBody").innerHTML = compositionalHtml(D.compositional) +
        `<div class="note">the flat sign-off does not apply: ${escapeHtml(r.skipped)}</div>`;
      return;
    }
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

  // The machine's rules, as the rule language decided them: F1-F9 on the floorplan, G1-G14
  // on the schedule, H1-H2 on the hardware between the gadgets (the basic rules on every
  // road's own program; devices sharing a plane take turns).  A rule opens to its own assertion, word for word, and what it found.
  function renderMachineRules(mr) {
    const el = $("checksBody");
    el.textContent = "";
    const groups = [["F", "the floorplan"], ["G", "the schedule"], ["H", "the hardware between gadgets"]];
    for (const [pre, title] of groups) {
      const rows = (mr.rules || []).filter((r) => r.id.startsWith(pre));
      if (!rows.length) continue;
      const g = document.createElement("div");
      g.className = "ck";
      g.innerHTML = `<span class="grp">${title}</span>`;
      el.appendChild(g);
      for (const r of rows) {
        const div = document.createElement("div");
        div.className = "ck";
        const state = r.verdict === "holds" ? "pass" : r.verdict === "fails" ? "fail" : "skip";
        let more = "";
        if (r.about) more += `<div>${escapeHtml(r.about)}</div>`;
        if (r.text) more += `<pre>${escapeHtml(r.text)}</pre>`;
        if (r.verdict === "fails") more += `<div style="color:var(--bad)">${r.count || 0} counterexample(s): ${escapeHtml((r.examples || []).slice(0, 4).join("; "))}</div>`;
        else if (r.reason) more += `<div class="muted">${escapeHtml(r.verdict)}: ${escapeHtml(r.reason)}</div>`;
        div.innerHTML = `<b class="${state}">${r.id}</b><span>${escapeHtml(r.title)}</span><div class="t">${more}</div>`;
        if (state === "fail") div.classList.add("open");
        div.onclick = () => div.classList.toggle("open");
        el.appendChild(div);
      }
      // what the wiring costs: every device's own plane of DACs, counted as one device's is
      const hw = pre === "H" && ((D.checks || {}).metrics || {}).hardware;
      if (hw) {
        const fam = hw.by_family || {};
        const bus = ["road", "junction"].reduce((n, k) => n + ((fam[k] || {}).dacs || 0), 0);
        const div = document.createElement("div");
        div.className = "ck census";
        const shared = hw.dacs_shared_away ? `; ${hw.dacs_shared_away.toLocaleString()} saved by ${hw.planes} shared plane${hw.planes === 1 ? "" : "s"}` : "";
        div.innerHTML = `<span class="skip">∑</span><span>${hw.dacs.toLocaleString()} DACs, ${hw.electrodes.toLocaleString()} electrodes` +
          (hw.beams ? `, ${hw.beams} beams` : "") +
          ` <span class="muted">(${bus.toLocaleString()} DACs on the roads and junctions${shared})</span></span>` +
          `<div class="t"><div>Every device drives its own plane, as its master declares: ` +
          Object.entries(fam).map(([k, v]) => `${escapeHtml(k)} ${v.devices}× · ${v.dacs} DACs`).join(", ") + `.</div></div>`;
        div.onclick = () => div.classList.toggle("open");
        el.appendChild(div);
      }
    }
    const nf = (mr.failed || []).length;
    // a rule that does not apply (F9 on a floorplan without regions) is not one that failed
    const na = Object.keys(mr.not_applicable || {}).length;
    $("checksCount").textContent = `${(mr.passed || []).length} of ${(mr.rules || []).length - na} hold` +
      (na ? ` · ${na} does not apply` : "");
    const b = $("checksBadge");
    b.textContent = nf ? `${nf} rule${nf === 1 ? "" : "s"} failed` : "F1–F9 · G1–G14 · H1–H2 ✓";
    b.className = nf ? "bad" : "ok";
    b.title = "the machine's rules: the floorplan's (F), the schedule's (G) and the hardware between gadgets (H), each an assertion in the rule language";
  }

  function renderTraffic() {
    const t = D.traffic;
    if (!t) return;
    $("traffic").hidden = false;
    const fmt = (v) => typeof v === "number" ? v.toLocaleString() : escapeHtml(String(v));
    const rows = [
      ["ion-sites shuttled", fmt(t.ion_sites)],
      ["bundles carried", fmt(t.carries)],
      ["junction passes", fmt(t.junction_passes)],
      ["time bundles waited", `${(t.wait_us / 1000).toFixed(1)} ms`],
      ["longest wait on a road", `${(t.longest_wait_us / 1000).toFixed(1)} ms`],
      ["busiest road", `${escapeHtml(t.busiest_road || "-")} (${(100 * t.busiest_road_share).toFixed(0)}%)`],
      ["places at work at once", `${t.peak_parallel_ops} at most, ${(+t.mean_parallel_ops).toFixed(2)} on average`],
    ];
    $("trafficBody").innerHTML = rows.map(([k, v]) => `<span class="k">${k}</span><span class="n">${v}</span>`).join("");
  }

  function setupFloorplan() {
    if (!D.floorplan) return;
    const b = $("bFloor");
    b.hidden = false;
    $("fvText").textContent = D.floorplan;
    const m = /^floorplan\s+(\w+)/m.exec(D.floorplan);
    $("fvTitle").textContent = `Floorplan ${m ? m[1] : ""}`;
    const save = $("fvSave");
    save.href = URL.createObjectURL(new Blob([D.floorplan], { type: "text/plain" }));
    save.download = `${m ? m[1] : "floorplan"}.floor`;
    b.onclick = () => $("floorView").classList.toggle("open");
    $("fvClose").onclick = () => $("floorView").classList.remove("open");
  }

  function renderChecks() {
    if (D.machineRules && (D.machineRules.rules || []).length) { renderMachineRules(D.machineRules); return; }
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
        // A FAILED CHECK SHOWS ITS REASON WITHOUT BEING ASKED.  The reason was already in
        // the page, one click away, which for a reader is the same as not being there:
        // `gadgets/demo` reports G7 failed because the programme asks for a transversal H
        // that this code does not admit -- refused on purpose, and the page explained that
        // only on hover.  A red verdict a reader cannot explain is a bug report waiting to
        // be filed, and one was.
        if (state === "fail") div.classList.add("open");
        div.onclick = () => div.classList.toggle("open");
      } else if ((ck.skipped || {})[id]) {
        div.title = ck.skipped[id];
      }
      el.appendChild(div);
    }
    const b = $("checksBadge");
    const nf = (ck.failed || []).length;
    const ids = Object.keys(rules);
    // NO TICK FOR A CHECK THAT DID NOT RUN.  A folded run too large to expand here has every
    // check skipped, each saying why; the badge says that, not "all passed".
    const ran = (ck.passed || []).length + nf;
    if (!ran && Object.keys(ck.skipped || {}).length) {
      b.textContent = "G not run here";
      b.className = "";
      b.title = Object.values(ck.skipped)[0];
      return;
    }
    b.textContent = nf ? `G ${nf} failed` : `${ids[0] || "G1"}–${ids[ids.length - 1] || "G9"} ✓`;
    b.className = nf ? "bad" : "ok";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // the LogicQ source, one element per line, lit by the instructions running now.  The lines
  // are drawn once; which instructions each carries is bound again whenever a folded run's
  // window moves (`bindProgram`), so a program of tens of thousands of lines is built once.
  const progLines = [];
  const progDivs = [];
  const progIds = {};
  function renderProgram() {
    const src = M.gir.program.source || "";
    $("progName").textContent = M.gir.program.name;
    $("progTitle").textContent = STK ? "Gadget" : "Program";
    const el = $("progBody");
    el.textContent = "";
    progDivs.length = 0;
    const lines = src.split(/\r?\n/);
    const calls = LANGS.defined("alg", lines);
    colOf("alg").calls = calls;
    colOf("alg").chars = lineChars(lines);
    lines.forEach((text, i) => {
      const line = i + 1;
      const div = document.createElement("div");
      const trimmed = text.trim();
      div.className = "pl" + (trimmed.startsWith("code ") ? " decl" : "");
      div.innerHTML = `<span class="ln">${line}</span>` + codeSpan("alg", text, calls);
      div.dataset.ids = "";
      div.onclick = () => {
        const ids = progIds[line] || [];
        if (!ids.length) return;
        const sp = M.insSpan[ids[0]];
        const dec = DECODE_OF[ids[0]];
        if (sp) { seek(dec ? dec.show : sp[0]); focusInstruction(ids[0]); }
      };
      div.onmouseenter = () => { const ids = progIds[line] || []; if (ids.length) { S.hoverIns = ids[0]; S.tlDirty = true; } };
      div.onmouseleave = () => { if (S.hoverIns !== null) { S.hoverIns = null; S.tlDirty = true; } };
      el.appendChild(div);
      progDivs.push(div);
    });
    bindProgram();
  }
  function bindProgram() {
    for (const [div] of progLines) {
      div.className = div.className.replace(/ (live|done|refused)/g, "");
      div.title = "";
      div.dataset.ids = "";
    }
    progLines.length = 0;
    for (const k in progIds) delete progIds[k];
    for (const ins of M.gir.program.instructions) (progIds[ins.line] || (progIds[ins.line] = [])).push(ins.id);
    for (const line in progIds) {
      const div = progDivs[line - 1];
      if (!div) continue;
      const ids = progIds[line];
      if (ids.some((id) => M.refused[id] !== undefined)) {
        div.className += " refused";
        div.title = ids.map((id) => M.refused[id]).filter(Boolean).join("\n");
      }
      div.dataset.ids = ids.join(",");
      progLines.push([div, ids]);            // (integer keys come in order: line order)
    }
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
    // (a folded program is tens of thousands of lines: after a seek too, or the line lit is
    // somewhere nobody is looking)
    // the lit line kept in view when it changes, and after a seek
    if (first && (first !== progLit || S.progFollow)) {
      S.progFollow = false;
      progLit = first;
      if (!$("prog").hidden) inView($("progBody"), first);
    }
  }
  let progLit = null;
  // one line of a program, coloured and explained (langs.js): its indentation kept, and a
  // wrapped line's continuation indented under its own first word -- but never by more than
  // 40% of the column, or a formula FormalRV aligns forty characters in is left one
  // character wide in a narrow column
  function codeSpan(lang, text, calls) {
    const t = text || "";
    const body = t.replace(/^\s+/, "");
    const ind = t.length - body.length;
    return `<span class="tx" style="padding-left:min(${ind + 2}ch, 40%)">${body ? LANGS.paint(lang, body, { calls }) : " "}</span>`;
  }
  // scroll a column so that a row is in view, a third of the way down, if it is not already
  function inView(box, el) {
    const top = el.offsetTop, h = box.clientHeight;
    if (!h) return;
    if (top < box.scrollTop + 4 || top + el.offsetHeight > box.scrollTop + h - 24) box.scrollTop = Math.max(0, top - h / 3);
  }
  // ------------------------------------------------------------------ the full stack
  //
  // A program lowered from FormalRV (qccd/fullstack/qec2alg.py) carries `D.stack`: the
  // `.prog`, `.qppm` and `.qec` it came from and, for every line of this program, the `.qec`
  // statement it implements, each link saying how it was made.  The Stack section answers,
  // at the playhead, which logical operation, which PPM, which QEC operation, which gadget
  // instruction, which Clifford circuit instruction and which hardware instruction is
  // running -- the place op's own TSIR at its local time -- and never leaves a row blank
  // without saying why.
  const STK = D.stack || null;
  const STK_LAYERS = [["prog", "Logical"], ["qppm", "PPM"], ["qec", "QEC"]];
  const stkFirst = { prog: {}, qppm: {}, qec: {} };
  function stkChain(algLine) {
    const q = (STK.alg || {})[algLine];
    if (!q) return {};
    const p = (STK.qec_links || {})[q[0]];
    const g = p ? (STK.qppm_links || {})[p[0]] : null;
    return { qec: q, qppm: p || null, prog: g || null };
  }
  function stkWhere(layer, line) {
    const L = STK.layers[layer];
    for (const [m, first, n] of L.members) if (line >= first && line < first + n) return `${m || L.file}:${line - first + 1}`;
    return `${L.file}:${line}`;
  }
  function stkHow(h) {
    return h ? `<span class="stk-how" title="${escapeHtml((STK.how || {})[h] || h)}">${escapeHtml(h)}</span>` : "";
  }
  // THE RUN AS TSIR MODULES.  A folded run is also written as modules (qccd/gadget/modules.py,
  // `hw/` beside the page): the program, a file per gadget, a file per leaf master.  Every
  // row of the run is one line of its gadget's module (`run 45 ... ppm_d3.ppm.ZZZ @bench1`),
  // and the hardware instruction running in it one line of that leaf's TSIR.  A module is
  // loaded the first time the Stack names it.  A run that is not folded has no modules of its
  // own, but each leaf's TSIR is written beside the page all the same, where a folded run keeps
  // its leaf modules (`hw/leaf/<master>.js`, qccd/gadget/build.py), so its Hardware column reads
  // the same text.
  const HW = (D.gir && D.gir.hw) || null;
  const LEAF_TEXT = !HW && !!D.leafText;
  const hwMods = {};
  window.__gadgetModule = function (rel, text) {
    const lines = text.split("\n");
    const at = {};
    let leaf = null;
    lines.forEach((s, i) => {
      const w = s.trim().split(/\s+/);
      if (w[0] === "leaf") { leaf = w[1]; return; }
      if (leaf !== null) { if (/^\d+$/.test(w[0])) at[leaf + "#" + w[0]] = i + 1; return; }
      if (w[0] === "run" || w[0] === "hold") at[w[0] + " " + w[1]] = i + 1;
    });
    hwMods[rel] = { lines, at };
    stkUpdate();
  };
  function hwModule(rel) {
    if (!(HW || LEAF_TEXT) || !rel) return null;
    if (hwMods[rel]) return hwMods[rel];
    if (hwMods[rel] === undefined) {
      hwMods[rel] = false;
      const s = document.createElement("script");
      s.src = (HW ? HW.dir : "hw/") + rel.replace(/\.tsir$/, ".js");
      s.async = true;
      document.head.appendChild(s);
    }
    return null;
  }
  // where the hardware instruction running now is written: its leaf's TSIR, and the line
  function leafWhere(op) {
    if (!op || !op.ins) return null;
    const rel = HW ? HW.leaves[op.master] : LEAF_TEXT ? `leaf/${op.master}.tsir` : null;
    if (!rel) return null;
    const m = hwModule(rel);
    return { rel, line: m ? m.at[`${op.master}.${op.e[4]}#${op.ins.id}`] : null };
  }
  // where a row of the window and the TSIR instruction running in it are written
  function hwWhere(op) {
    if (!HW || !op || !op.e.hw) return null;
    const [mid, kind, id] = op.e.hw;
    const rel = mid === null ? HW.program : HW.gadgets[mid];
    const g = hwModule(rel);
    const lrel = HW.leaves[op.master];
    const l = op.ins ? hwModule(lrel) : null;
    return { row: { rel, line: g ? g.at[kind + " " + id] : null },
             leaf: { rel: lrel, line: l ? l.at[`${op.master}.${op.e[4]}#${op.ins.id}`] : null } };
  }
  function hwCite(w) {
    if (!w.rel) return "";
    return `<a href="${escapeHtml(HW.dir + w.rel)}" target="_blank" rel="noopener">${escapeHtml(w.rel)}${w.line ? ":" + w.line : ""}</a>`;
  }
  function stkInit() {
    if (!STK) return;
    // every layer's program is a column of its own (the columns, below), the line running
    // now lit in each and its link to the layer above badged in the column's head; the
    // hardware instruction is lit in the Hardware column.  (The page no longer repeats them in
    // a caption: the user, 2026-10-29, "no need to show" it.)
    // the first instruction carrying each line of each layer: click a line, land there.  A
    // folded run is not all on the page at once, so there it is the first TIME each line of
    // the program runs (`line_first`, from fold.py), and the click seeks there.
    if (FOLD) {
      for (const al in FOLD.g.line_first || {}) {
        const t = FOLD.g.line_first[al];
        const c = stkChain(+al);
        for (const [k] of STK_LAYERS) {
          const ln = c[k] && c[k][0];
          if (ln && !(stkFirst[k][ln] <= t)) stkFirst[k][ln] = t;
        }
        for (const ql of ((STK.qec_absorbed_into || {})[al] || [])) {
          if (!(stkFirst.qec[ql] <= t)) stkFirst.qec[ql] = t;
        }
      }
    } else {
      for (const ins of M.gir.program.instructions) {
        const c = stkChain(ins.src || ins.line);
        for (const [k] of STK_LAYERS) {
          const ln = c[k] && c[k][0];
          if (ln && stkFirst[k][ln] === undefined) stkFirst[k][ln] = ins.id;
        }
        for (const ql of ((STK.qec_absorbed_into || {})[ins.src || ins.line] || [])) {
          if (stkFirst.qec[ql] === undefined) stkFirst.qec[ql] = ins.id;
        }
      }
    }
  }
  // a line of a layer, clicked: the animation goes to the first instruction carrying it
  function stkJump(layer, line) {
    const id = (stkFirst[layer] || {})[line];
    if (FOLD) { if (id !== undefined) seek(id + 1e-3); return; }
    const sp = id !== undefined ? M.insSpan[id] : null;
    if (sp) { seek(sp[0]); focusInstruction(id); }
  }
  // the instruction the stack describes: the one selected, else the first running, else the
  // last one finished
  // what an op does that is not a block waiting or travelling: work at a place
  const STK_IDLE = /^(store|release|check_in|check_out|load\.|dump\.|pass\.)/;
  function stkFocus() {
    if (S.sel && S.sel.kind === "ins") return S.sel.id;
    const live = M.activeInstructions(S.t).filter((i) => !M.gir.program.instructions[i].refresh);
    if (live.length) {
      const working = live.filter((i) => M.gir.events.some((e) => e[6] === i && e[1] <= S.t && S.t < e[2] && !STK_IDLE.test(e[4])));
      return Math.min(...(working.length ? working : live));
    }
    let best = null;
    M.insSpan.forEach((s, i) => { if (s && s[1] <= S.t && (best === null || s[1] > M.insSpan[best][1])) best = i; });
    return best;
  }
  // the place op of instruction `id` running at t (or its last), and its TSIR instruction now
  function stkOpNow(id) {
    const evs = M.gir.events.filter((e) => e[6] === id);
    if (!evs.length) return null;
    let e = evs.find((x) => x[1] <= S.t && S.t < x[2]);
    if (!e) e = evs.filter((x) => x[1] <= S.t).pop() || evs[0];
    const master = M.gir.leaves[e[3]][0];
    const d = M.leafData[master];
    const out = { e, master, place: e[3].split(".").pop() };
    if (!d || !d.programs[e[4]]) return out;
    const lt = M.localTime(e, Math.min(Math.max(S.t, e[1]), e[2]));
    const times = d.times[e[4]] || [];
    let k = lowerBound(times, lt.local + 1e-9, (x) => x[1]) - 1;
    k = Math.max(0, Math.min(times.length - 1, k));
    const ins = d.programs[e[4]].instructions;
    out.k = k; out.ins = ins[k]; out.local = lt.local;
    // the circuit instruction this one is, or the one it is bringing ions to
    let c = k;
    while (c < ins.length && !["gate", "measure", "reset"].includes(ins[c].type)) c++;
    out.circuit = c < ins.length ? ins[c] : null;
    out.towards = c !== k;
    return out;
  }
  // what the columns describe, at the playhead
  function stkUpdate() {
    const id = stkFocus();
    const op = id === null || id === undefined ? null : stkOpNow(id);
    colsUpdate(id, op);
  }

  // ------------------------------------------------------------------ the columns
  //
  // EVERY PROGRAM IN A COLUMN OF ITS OWN.  A run is several programs, each lowered from the one
  // above it: FormalRV's .prog, .qppm and .qec, the gadget program the town runs (.alg), the
  // TSIR module its rows are written in, and the hardware program of the leaf at work.  Each
  // stands in a full-height column beside the animation, the line running now lit and kept in
  // view, its words coloured by what they do and explained on hover (langs.js).  The reader
  // picks which columns stand there (the bar above them), how wide each one is, and whether
  // long lines wrap; the side panels fold away to give them room.  EVERY BOUNDARY DRAGS (the
  // user, 2026-10-29: "make all boundary dragable"): between two columns (the two share their
  // room), between the stage and the columns, at each side panel's edge, between the stage
  // and the timeline, and between the sections of a side panel; a double click puts one back.
  // A column is drawn once; the playhead only moves its lit line.  A TSIR module is tens of
  // thousands of lines, so a module column draws only the rows in view.
  const LANGS = window.QCCD_LANGS;
  const APP = $("app");
  const COLS = [];
  const colOf = (key) => COLS.find((c) => c.key === key) || {};
  // a page held as a figure (the tutorial's <iframe>s) keeps its own choices, and opens with
  // its programs folded: a figure is the picture first.  (It only asks whether it has a
  // parent; a figure never reaches for the page around it.)
  let IN_FRAME = false;
  try { IN_FRAME = window.parent !== window; } catch (e) { IN_FRAME = true; }
  const lineChars = (lines) => {
    const n = lines.map((x) => x.replace(/\s+$/, "").length).filter((x) => x > 0).sort((a, b) => a - b);
    return n.length ? n[Math.floor(n.length * 0.9)] : 40;
  };
  const store = {
    get(k) { try { return window.localStorage.getItem("qccd.gadget." + (IN_FRAME ? "frame." : "") + k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem("qccd.gadget." + (IN_FRAME ? "frame." : "") + k, v); } catch (e) { /* not kept */ } },
  };
  // the layout's own keys: a full-stack page keeps its own (it opens differently)
  const LK = (k) => (STK ? "fs." : "") + k;
  function colsDefine() {
    if (STK) for (const [k, title] of STK_LAYERS) COLS.push({ key: k, title, lang: k, kind: "layer", file: STK.layers[k].file });
    COLS.push({ key: "alg", title: STK ? "Gadget" : "Program", lang: "alg", kind: "alg", el: $("prog"), body: $("progBody") });
    if (HW) COLS.push({ key: "gmod", title: "Module", lang: "tsir", kind: "module", which: "row",
                         about: "the TSIR module the running row is written in" });
    if (HW || LEAF_TEXT) COLS.push({ key: "hw", title: "Hardware", lang: "tsir", kind: "module", which: "leaf",
                                      about: "the TSIR of the leaf gadget at work" });
  }
  // the columns a page opens with: on a full-stack run every layer and the hardware
  // instructions running under them (the gadget program is a button away), the program alone
  // elsewhere -- unless the reader chose otherwise on this kind of page
  const COLS_KEY = STK ? LK("cols") : "cols.plain";
  const colsDefault = () => STK ? ["prog", "qppm", "qec", colOf("hw").key ? "hw" : "alg"] : ["alg"];
  function colsChosen() {
    const s = store.get(COLS_KEY);
    if (s !== null) { const on = new Set(s.split(",").filter(Boolean)); return (k) => on.has(k); }
    const on = colsDefault();
    return (k) => on.includes(k);
  }
  function colsInit() {
    if (!COLS.length) colsDefine();
    const row = $("colsRow"), chosen = colsChosen();
    for (const col of COLS) {
      if (!col.el) {
        col.el = document.createElement("section");
        col.el.className = "pcol";
        col.el.dataset.col = col.key;
        col.el.dataset.lang = col.lang;
        col.el.innerHTML = `<h3><span class="pc-t">${escapeHtml(col.title)}</span><span class="muted pc-at">${escapeHtml(col.file || "")}</span>` +
          `<button class="fold pc-x" data-hint="cols:close" title="hide this column">×</button></h3>` +
          `<div class="pc-b"></div>`;
        col.body = col.el.querySelector(".pc-b");
        col.at = col.el.querySelector(".pc-at");
        if (col.kind === "layer") row.insertBefore(col.el, $("prog"));
        else row.appendChild(col.el);
      }
      // the boundary on a column's left: drag it, and this column and the one before share
      // their room differently
      if (!col.el.querySelector(":scope > .pc-edge")) {
        const e = document.createElement("div");
        e.className = "pc-edge";
        e.dataset.hint = "cols:edge";
        e.title = "drag to share the room between these two columns differently (double-click: evenly)";
        col.el.prepend(e);
        colEdge(e, col);
      }
      col.on = chosen(col.key);
      col.el.querySelector(".pc-x").onclick = () => colsToggle(col.key, false);
      if (col.kind === "layer") layerBuild(col);
      if (col.kind === "module") vBuild(col);
    }
    // the bar: a button a column, then the line wrap and the fold
    const bar = $("colsBar");
    bar.innerHTML = `<span class="cb-l">Programs</span>` +
      COLS.map((c) => `<button data-col="${c.key}" data-hint="cols:toggle" title="${escapeHtml(colTip(c))}">${escapeHtml(c.title)}</button>`).join("") +
      `<span class="cb-sp"></span><button id="colsWrap" data-hint="cols:wrap" title="wrap long lines inside their column">Wrap</button>` +
      `<button class="fold" id="colsHide" data-hint="cols:hide" title="fold the program columns away">›</button>`;
    for (const b of bar.querySelectorAll("button[data-col]")) b.onclick = () => colsToggle(b.dataset.col);
    const wrap = store.get("wrap") !== "0";
    $("cols").classList.toggle("nowrap", !wrap);
    $("colsWrap").className = wrap ? "on" : "";
    $("colsWrap").onclick = () => {
      const on = $("cols").classList.toggle("nowrap");
      $("colsWrap").className = on ? "" : "on";
      store.set("wrap", on ? "0" : "1");
      colsFollow(true);
    };
    $("colsHide").onclick = () => { APP.classList.add("nocols"); store.set("hidden", "1"); colsApply(); };
    $("colsRail").onclick = () => {
      APP.classList.remove("nocols"); store.set(LK("hidden"), "0");
      if (!COLS.some((c) => c.on)) { const on = colsDefault(); for (const c of COLS) c.on = on.includes(c.key); }
      colsApply();
    };
    if ((store.get(LK("hidden")) || (IN_FRAME ? "1" : "0")) === "1") APP.classList.add("nocols");
    // the side panels: a full-stack run gives its columns the room the hierarchy took (below
    // 2200 px), and the right-hand panel's always -- its columns say what the verification,
    // the inspector and the old caption said (the user, 10-29: "no need to show" them); each
    // opens again from its bar
    const folded = (side, dflt) => { const s = store.get(LK("fold." + side)); return s === null ? dflt : s === "1"; };
    APP.classList.toggle("noleft", folded("left", !!STK && window.innerWidth < 2200));
    APP.classList.toggle("noright", folded("right", !!STK));
    APP.classList.toggle("stack", !!STK);
    const fold = (side) => () => { const on = APP.classList.toggle("no" + side); store.set(LK("fold." + side), on ? "1" : "0"); colsApply(); };
    $("leftFold").onclick = $("leftRail").onclick = fold("left");
    $("rightFold").onclick = $("rightRail").onclick = fold("right");
    colsSplitter();
    edgesInit();
    $("cols").addEventListener("mouseover", colsTip);
    $("cols").addEventListener("mouseleave", () => { $("tip").style.display = "none"; });
    APP.classList.add("hascols");
    colsApply();
  }
  function colTip(c) {
    if (c.kind === "layer") return `the ${c.title.toLowerCase()} program (${c.file})`;
    if (c.kind === "alg") return "the gadget program the machine runs";
    return c.about;
  }
  function colsToggle(key, on) {
    const col = colOf(key);
    if (!col.el) return;
    col.on = on === undefined ? !col.on : on;
    store.set(COLS_KEY, COLS.filter((c) => c.on).map((c) => c.key).join(","));
    if (COLS.some((c) => c.on)) APP.classList.remove("nocols");
    colsApply();
  }

  // THE WIDTHS.  Every column has the width the reader gave it (px, kept), or shares what the
  // stage leaves; the region is their sum, and when that is more than the screen has, they keep
  // their proportions.  Each side panel has its own width too.
  const COLW = (() => { try { return JSON.parse(store.get(LK("colws")) || "{}") || {}; } catch (e) { return {}; } })();
  const keepWidths = () => store.set(LK("colws"), JSON.stringify(COLW));
  const MIN_COL = 120;
  function sidePx(side) {
    if (APP.classList.contains("no" + side)) return 30;
    return +store.get(LK(side + "w")) || (side === "left" ? 250 : STK ? 300 : 340);
  }
  // what the columns may take: the stage keeps 360 px
  const colsRoom = () => Math.max(200, window.innerWidth - sidePx("left") - sidePx("right") - 360);
  function colsShown() { return APP.classList.contains("nocols") ? [] : COLS.filter((c) => c.on); }
  function colsApply() {
    const shown = colsShown();
    for (const c of COLS) c.el.hidden = !c.on;
    for (const b of $("colsBar").querySelectorAll("button[data-col]")) b.className = colOf(b.dataset.col).on ? "on" : "";
    if (!shown.length) APP.classList.add("nocols");
    // the first column shown has no boundary on its left (that one is the stage's)
    shown.forEach((c, i) => { c.el.querySelector(":scope > .pc-edge").hidden = i === 0; });
    for (const side of ["left", "right"]) {
      const w = +store.get(LK(side + "w"));
      if (w) APP.style.setProperty(`--${side}-open`, w + "px"); else APP.style.removeProperty(`--${side}-open`);
    }
    // a column the reader never sized is as wide as its lines are long (QEC's run to a hundred
    // characters, the logical program's to twenty), at least 200 px and at most 680 (480 for a
    // column alone: the stage is the page then); together they leave the stage 440 px, the
    // narrow ones keeping their 200 and the wide ones giving up the rest
    const n = shown.length, budget = Math.max(200 * n, window.innerWidth - sidePx("left") - sidePx("right") - 440);
    const want = shown.map((c) => Math.max(200, Math.min(n === 1 ? 480 : 680, (c.chars || 60) * 7 + 56)));
    const sum = want.reduce((a, b) => a + b, 0);
    const fitted = sum <= budget ? want : want.map((w) => 200 + (w - 200) * (budget - 200 * n) / (sum - 200 * n));
    const ws = shown.map((c, i) => COLW[c.key] || Math.round(fitted[i]));
    shown.forEach((c, i) => { c.el.style.flex = `${ws[i]} 1 0px`; });
    const total = ws.reduce((a, b) => a + b, 0);
    APP.style.setProperty("--cols-w", (shown.length ? Math.min(colsRoom(), total) : 30) + "px");
    S.dirty = true;
    requestAnimationFrame(() => { fit(); colsFollow(true); });
  }
  // a drag along one axis: `start()` once, `move(d)` with the pointer's travel since, `end()`
  function dragEdge(el, axis, start, move, end) {
    el.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      el.setPointerCapture(ev.pointerId);
      el.classList.add("on");
      document.body.classList.add(axis === "x" ? "dragx" : "dragy");
      const x0 = ev.clientX, y0 = ev.clientY;
      const ctx = start();
      const mv = (e) => { move(axis === "x" ? e.clientX - x0 : e.clientY - y0, ctx); S.dirty = true; };
      const up = () => {
        el.removeEventListener("pointermove", mv);
        el.classList.remove("on");
        document.body.classList.remove("dragx", "dragy");
        end(ctx);
      };
      el.addEventListener("pointermove", mv);
      el.addEventListener("pointerup", up, { once: true });
      el.addEventListener("pointercancel", up, { once: true });
    });
  }
  // the edge between the stage and the columns: every column grows or shrinks in proportion
  function colsSplitter() {
    const el = $("vsplit");
    dragEdge(el, "x", () => {
      const shown = colsShown();
      return { shown, px: shown.map((c) => c.el.getBoundingClientRect().width), total: $("cols").getBoundingClientRect().width };
    }, (d, c) => {
      const T = Math.max(MIN_COL * c.shown.length, Math.min(window.innerWidth - sidePx("left") - sidePx("right") - 160, c.total - d));
      c.shown.forEach((col, i) => { COLW[col.key] = Math.round(c.px[i] * T / c.total); col.el.style.flex = `${COLW[col.key]} 1 0px`; });
      APP.style.setProperty("--cols-w", T + "px");
    }, () => { keepWidths(); fit(); colsFollow(true); });
    el.addEventListener("dblclick", () => { for (const c of colsShown()) delete COLW[c.key]; keepWidths(); colsApply(); });
    window.addEventListener("resize", () => colsApply());
  }
  // the edge between a column and the one before it: the two share their room differently
  function colEdge(el, col) {
    const before = () => { const shown = colsShown(), i = shown.indexOf(col); return i > 0 ? shown[i - 1] : null; };
    dragEdge(el, "x", () => {
      const a = before();
      if (!a) return null;
      // every column shown keeps the width it has now; these two then trade
      for (const c of colsShown()) { COLW[c.key] = c.el.getBoundingClientRect().width; c.el.style.flex = `${COLW[c.key]} 1 0px`; }
      return { a, pa: COLW[a.key], pb: COLW[col.key] };
    }, (d, c) => {
      if (!c) return;
      const na = Math.max(MIN_COL, Math.min(c.pa + c.pb - MIN_COL, c.pa + d));
      COLW[c.a.key] = na;
      COLW[col.key] = c.pa + c.pb - na;
      c.a.el.style.flex = `${COLW[c.a.key]} 1 0px`;
      col.el.style.flex = `${COLW[col.key]} 1 0px`;
    }, (c) => { if (c) { keepWidths(); colsFollow(true); } });
    el.addEventListener("dblclick", () => {
      const a = before();
      if (!a) return;
      const both = Math.round(a.el.getBoundingClientRect().width + col.el.getBoundingClientRect().width);
      for (const c of colsShown()) COLW[c.key] = Math.round(c.el.getBoundingClientRect().width);
      COLW[a.key] = Math.floor(both / 2);
      COLW[col.key] = both - COLW[a.key];
      keepWidths();
      colsApply();
    });
  }
  // the side panels' edges, and the boundaries between the sections of each
  function edgesInit() {
    const side = (name, sign) => {
      const el = document.createElement("div");
      el.className = "edge e-" + (sign > 0 ? "r" : "l");
      el.id = name + "Edge";
      el.dataset.hint = "panel:width";
      el.title = "drag to make this panel wider or narrower (double-click: as it was)";
      $(name).appendChild(el);
      dragEdge(el, "x", () => $(name).getBoundingClientRect().width, (d, w0) => {
        const w = Math.max(180, Math.min(Math.round(window.innerWidth * 0.45), Math.round(w0 + sign * d)));
        store.set(LK(name + "w"), String(w));
        APP.style.setProperty(`--${name}-open`, w + "px");
      }, () => colsApply());
      el.addEventListener("dblclick", () => { store.set(LK(name + "w"), ""); colsApply(); });
    };
    side("left", +1);
    side("right", -1);
    // a section's height: the hierarchy above the checks, the verification above the inspector
    const sect = (id, sign) => {
      const box = $(id);
      const el = document.createElement("div");
      el.className = "edge " + (sign > 0 ? "e-b" : "e-t");
      el.dataset.hint = "panel:height";
      el.title = "drag to give this section more or less of the panel (double-click: as it was)";
      box.appendChild(el);
      const set = (h) => {
        if (h) { box.style.flex = `0 0 ${h}px`; box.style.maxHeight = "none"; } else { box.style.flex = ""; box.style.maxHeight = ""; }
      };
      set(+store.get(LK("h." + id)) || 0);
      dragEdge(el, "y", () => box.getBoundingClientRect().height, (d, h0) => {
        const h = Math.max(60, Math.min(box.parentNode.getBoundingClientRect().height - 120, Math.round(h0 + sign * d)));
        set(h);
        store.set(LK("h." + id), String(h));
      }, () => {});
      el.addEventListener("dblclick", () => { store.set(LK("h." + id), ""); set(0); });
    };
    sect("checks", -1);
    sect("verif", +1);
  }
  // a word with a note: the note, under it
  function colsTip(ev) {
    const tip = $("tip");
    const w = ev.target.closest && ev.target.closest("[data-w]");
    const pc = w && w.closest(".pcol");
    const col = pc ? colOf(pc.dataset.col) : null;
    const n = col ? LANGS.note(col.lang, w.dataset.w, col.calls) : null;
    if (!n) { tip.style.display = "none"; return; }
    tip.textContent = `${n[0]}\n${n[1]}`;
    tip.style.display = "block";
    const r = w.getBoundingClientRect();
    tip.style.left = Math.max(4, Math.min(window.innerWidth - 370, r.left)) + "px";
    tip.style.top = (r.bottom + 6 + 120 > window.innerHeight ? r.top - 8 - tip.offsetHeight : r.bottom + 6) + "px";
  }

  // A LAYER COLUMN: the whole file, drawn once; a program of several files says where each
  // begins, and numbers its lines file by file, as FormalRV does
  // (its specification -- `main.spec.prog`, a `.qppm`'s `spec NAME:` blocks -- left out: the
  // columns show what runs; the user, 10-29, "no need to show the specification")
  function layerBuild(col) {
    const k = col.key, L = STK.layers[k];
    col.calls = LANGS.defined(k, L.lines, (ln) => stkWhere(k, ln));
    const spec = LANGS.spec(k, L.lines, (ln) => stkWhere(k, ln));
    const parts = [];
    let file = null;
    for (let ln = 1; ln <= L.lines.length; ln++) {
      if (spec.has(ln)) continue;
      const at = stkWhere(k, ln), c = at.lastIndexOf(":");
      const f = at.slice(0, c);
      if (L.members.length > 1 && f !== file) parts.push(`<div class="cl-file">${escapeHtml(f)}</div>`);
      file = f;
      const can = stkFirst[k][ln] !== undefined;
      parts.push(`<div class="cl${can ? " can" : ""}" data-line="${ln}"${can ? ' data-hint="fullstack-line"' : ""}>` +
        `<span class="ln">${at.slice(c + 1)}</span>${codeSpan(k, L.lines[ln - 1], col.calls)}</div>`);
    }
    col.body.innerHTML = parts.join("");
    col.chars = lineChars(L.lines.filter((x, i) => !spec.has(i + 1)));
    col.rows = {};
    for (const r of col.body.querySelectorAll(".cl")) col.rows[r.dataset.line] = r;
    col.cur = 0;
    col.marks = [];
    col.body.addEventListener("click", (ev) => {
      const r = ev.target.closest(".cl.can");
      if (r) stkJump(k, +r.dataset.line);
    });
  }
  // the line running now lit; the column's head says where it is written and how it is linked
  // to the layer below (a badge, explained on hover) -- or why no line of this layer runs now
  function layerMark(col, line, also, how, why) {
    if (line !== col.cur || (!line && why !== col.why)) {
      for (const el of col.marks) el.classList.remove("cur", "abs");
      col.marks = [];
      col.cur = line;
      col.why = why;
      const el = line ? col.rows[line] : null;
      if (el) { el.classList.add("cur"); col.marks.push(el); col.follow = true; }
      for (const a of also || []) { const e = col.rows[a]; if (e && a !== line) { e.classList.add("abs"); col.marks.push(e); } }
      col.at.innerHTML = line ? `${escapeHtml(stkWhere(col.key, line))} ${stkHow(how)}` :
        why ? `<span title="${escapeHtml(why)}">${escapeHtml(why)}</span>` : escapeHtml(col.file);
    }
  }

  // A MODULE COLUMN: a virtual list over the module's lines, one row of 18 px each
  const VROW = 18;
  function vBuild(col) {
    col.chars = 90;                      // a TSIR line: an instruction and its ions, then its notes
    col.body.innerHTML = `<div class="pc-none"></div><div class="vl"><div class="vw"></div></div>`;
    col.msg = col.body.firstChild;
    col.vl = col.body.querySelector(".vl");
    col.vw = col.body.querySelector(".vw");
    col.mod = null;
    col.cur = 0;
    col.drawn = -1;
    col.body.addEventListener("scroll", () => vPaint(col, false));
  }
  function vShow(col, rel, line, why) {
    const mod = rel ? hwModule(rel) : null;
    col.at.innerHTML = rel ? (HW ? hwCite({ rel, line }) : escapeHtml(rel + (line ? ":" + line : ""))) : "";
    if (!mod) {
      col.msg.hidden = false;
      col.msg.textContent = rel ? `loading ${rel}…` : why;
      col.vl.hidden = true;
      col.mod = null;
      return;
    }
    col.msg.hidden = !!line;
    col.msg.textContent = line ? "" : why;
    col.vl.hidden = false;
    if (col.mod !== mod) {
      col.mod = mod;
      let w = 0;
      for (const s of mod.lines) if (s.length > w) w = s.length;
      col.vl.style.height = mod.lines.length * VROW + "px";
      col.vl.style.width = `calc(${Math.min(w, 400) + 2}ch + 54px)`;
      col.cur = -1;
    }
    if (line !== col.cur) { col.cur = line || 0; col.follow = !!line; col.drawn = -1; }
    if (!col.el.hidden) vPaint(col, true);
  }
  function vPaint(col, force) {
    if (!col.mod || col.el.hidden) return;
    const box = col.body, n = col.mod.lines.length;
    if (col.follow && col.cur) {
      const top = (col.cur - 1) * VROW + (col.msg.hidden ? 0 : col.msg.offsetHeight), h = box.clientHeight;
      if (h && (top < box.scrollTop + 4 || top > box.scrollTop + h - 30)) box.scrollTop = Math.max(0, top - h / 3);
      col.follow = false;
    }
    const first = Math.max(0, Math.floor(box.scrollTop / VROW) - 30);
    if (!force && first === col.drawn) return;
    col.drawn = first;
    const last = Math.min(n, first + Math.ceil((box.clientHeight || 600) / VROW) + 60);
    let h = "";
    for (let i = first; i < last; i++) {
      const s = col.mod.lines[i];
      h += `<div class="cl${i + 1 === col.cur ? " cur" : ""}"><span class="ln">${i + 1}</span><span class="tx">` +
        `${LANGS.paint("tsir", s.length > 400 ? s.slice(0, 400) : s)}${s.length > 400 ? ` <span class="tk-c">… ${(s.length - 400).toLocaleString()} more characters</span>` : ""}</span></div>`;
    }
    col.vw.style.top = first * VROW + "px";
    col.vw.innerHTML = h;
  }

  // the playhead moved (or a module arrived): every column lights its line
  function colsUpdate(id, op) {
    if (!COLS.length) return;
    let c = {}, also = [], why = "";
    if (STK && id !== null && id !== undefined) {
      const ins = M.gir.program.instructions[id];
      if (!(STK.input_lines || []).includes(ins.line)) c = stkChain(ins.src || ins.line);
      also = (STK.qec_absorbed_into || {})[ins.src || ins.line] || [];
      why = (STK.input_lines || []).includes(ins.line) ? "now: an input, prepared before the program runs" :
        ins.refresh ? "now: a syndrome round QCCD added to keep a resting block alive" : "";
    }
    for (const col of COLS) {
      if (col.kind === "layer") layerMark(col, c[col.key] ? c[col.key][0] : 0, col.key === "qec" ? also : [],
                                          c[col.key] ? c[col.key][1] : "", why);
      else if (col.kind === "module" && col.on) {
        if (col.which === "row") {
          const w = op ? hwWhere(op) : null;
          vShow(col, w && w.row.rel, w && w.row.line, op ? "this row is not in a module" : "nothing runs at a place now");
        } else {
          const w = leafWhere(op);
          vShow(col, w && w.rel, w && w.line,
                !op ? "nothing runs at a place now" : !op.ins ? `${op.master} works on bits: no hardware program` : "");
        }
      }
    }
    colsFollow(false);
  }
  // keep each shown column's lit line in view (after a layout change, `all`)
  function colsFollow(all) {
    for (const col of COLS) {
      if (!col.on || APP.classList.contains("nocols")) continue;
      if (col.kind === "layer" && col.rows[col.cur] && (col.follow || all)) { inView(col.body, col.rows[col.cur]); col.follow = false; }
      if (col.kind === "module" && all) { col.follow = true; vPaint(col, true); }
    }
    if (all) { const lit = $("progBody").querySelector(".pl.live"); if (lit) inView($("progBody"), lit); }
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
    const dec = DECODE_OF[id];
    if (dec) {
      h += `<div class="note">a call to the decoder: each place below sends the syndrome it ` +
        `kept in its readout buffer down its wire to the decoder, the decoder runs one job ` +
        `per window, and the Pauli-frame update goes down the frame wire to the classical ` +
        `memory. ${dec.bits} syndrome bits in all; the wires are lit while it runs, and ` +
        `playing slows through it (${(dec.t1 - dec.t0).toFixed(1)} µs, shown over ` +
        `${DECODE_WALL_S} s). <a href="#" data-seek="${dec.show}">show it</a></div>`;
      h += `<table><tr><th>measured by</th><th>at</th><th class="num">bits</th><th class="num">waited</th><th class="num">decoded</th></tr>`;
      for (const w of dec.windows) {
        h += `<tr><td>${escapeHtml(w.op)} · ${escapeHtml(w.blocks.join(" "))}</td>` +
          `<td><a href="#" data-sel="${escapeHtml(w.place)}">${escapeHtml(w.place)}</a></td>` +
          `<td class="num">${w.bits}</td>` +
          `<td class="num" title="measured at ${fmtTime(w.measured_us)}, sent at ${fmtTime(w.sent_us)}">${fmtTime(w.sent_us - w.measured_us)}</td>` +
          `<td class="num"><a href="#" data-seek="${w.sent_us}">${fmtTime(w.decoded_us)}</a></td></tr>`;
      }
      h += `</table>`;
    }
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
    // a key names only what is drawn
    if (!S.showClassical) for (const c of ["wire", ...CLASSICAL]) used.delete(c);
    const cats = CAT_ORDER.filter((c) => CATS[c] && used.has(c));
    // the ion colours are the studio's: what the machine is doing to an ion this instant,
    // not what role a code gave it (qccd/viz/render.py::ionColour)
    const swatches = [[P("data", "#475467"), "an ion"],
                      [P("x", "#0f766e"), "in a gate"],
                      [P("z", "#b42318"), "measured or reset"],
                      [P("active", "#f6c34a"), "the site in play"],
                      [P("accent", "#e4572e"), "the ion you follow"]];
    if (DECODES.length && S.showClassical) swatches.push([WIRE_LIT, "bits sent by a decode"]);
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
    if (FOLD && outsideWindow(S.t)) refold(S.t);
    if (FOLD) S.progFollow = true;
    if (S.t < S.tl.t0 || S.t > S.tl.t1) {
      const span = S.tl.t1 - S.tl.t0;
      S.tl.t0 = Math.max(0, S.t - span / 2);
      S.tl.t1 = S.tl.t0 + span;
    }
    S.dirty = true;
  }
  // A folded run's window: expanded again once the playhead is in its last tenth (or before
  // it), or once a macro it was waiting for has arrived.
  function outsideWindow(t) {
    const w = M.gir.window;
    return !w || t < w.a || t > w.b - 0.1 * (w.b - w.a);
  }
  function refold(t) {
    const a = Math.max(0, t - 0.25 * FOLD_SPAN);
    const w = FOLD.window(a, a + FOLD_SPAN);
    M.reload(w);
    MSGS.length = 0;
    for (const m of w.messages) MSGS.push(m);
    FRAMES.length = 0;
    for (const f of w.frames) FRAMES.push(f);
    indexMessages();
    buildDecodes();
    bindProgram();
    if (S.sel && S.sel.kind === "ins") S.sel = null;
    foldStale = false;
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
    return { focus: h.get("f") || D.focus || null, t: h.has("t") ? S.t : null };
  }

  // WHAT A PAGE THAT EMBEDS THIS ONE POINTS AT (the tutorial's figures).  A place on the
  // floorplan by its path ("row1.msf15_0") or by its kind ("msf15": the first one), or an
  // instruction of the program by its number ("i5") or its line ("L13"), which also moves the
  // clock to where it starts.  False when the page has no such thing.
  function focusOn(id) {
    id = String(id == null ? "" : id);
    const ins = M.gir.program.instructions || [];
    let k = /^i\d+$/.test(id) ? +id.slice(1)
      : /^L\d+$/.test(id) ? (ins.find((x) => x.line === +id.slice(1)) || { id: -1 }).id : null;
    if (k !== null) {
      const sp = M.insSpan[k];
      if (!sp) return false;
      const dec = DECODE_OF[k];
      seek(dec ? dec.show : sp[0]);
      focusInstruction(k);
      return true;
    }
    let path = id in M.nodes ? id : null;
    if (path === null) for (const p in M.nodes) if (p && M.nodes[p].master === id) { path = p; break; }
    if (path === null) return false;
    if (path === "") { go(""); return true; }
    go(M.node(path).parent || "");
    select({ kind: "inst", path });
    return true;
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
      let hitIns = null;
      for (const hh of TL.insHits || []) if (x >= hh[0] - 1 && x <= hh[2] + 1 && y >= hh[1] && y <= hh[3] + 1) { focusInstruction(hh[4]); hitIns = hh[4]; }
      tdrag = { kind: "scrub" };
      // a `decode` is too short to land in by pointing: clicking it puts the playhead
      // where its syndromes are half-way down their wires
      if (hitIns !== null && DECODE_OF[hitIns]) { seek(DECODE_OF[hitIns].show); tdrag = null; }
      else seek(tToX(x, r.width, true));
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
  $("bClassical").onclick = () => {
    S.showClassical = !S.showClassical;
    try { window.localStorage.setItem("qccd.classical", S.showClassical ? "1" : "0"); } catch (e) { /* not kept */ }
    $("bClassical").className = S.showClassical ? "on" : "";
    legend();
    S.dirty = true;
  };
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
    else if (ev.key === "c" || ev.key === "C") $("bClassical").onclick();
    else if (ev.key === "Enter" && S.sel && S.sel.kind === "inst") go(S.sel.path);
  });
  window.addEventListener("resize", () => { DPR = window.devicePixelRatio || 1; fit(); });

  // what the stage says while a `decode` runs: which one, how far in, and -- because the
  // playhead is slowed through it -- by how much, so nobody reads the pace as the machine's
  function decodeBadge() {
    const el = $("hudDecode");
    if (!el) return;
    const lit = litAt(S.t);
    el.hidden = !lit;
    if (!lit) return;
    const d = lit.list[0];
    const into = Math.max(0, S.t - d.t0), len = d.t1 - d.t0;
    const slowed = S.playing && S.slowed > 1
      ? ` · shown ${S.slowed >= 100 ? Math.round(S.slowed).toLocaleString() : S.slowed.toFixed(0)}× slower` : "";
    el.style.color = WIRE_LIT;
    el.style.borderColor = WIRE_GLOW;
    el.textContent = `${d.text.trim().split("#")[0].trim()} · ${d.windows.length} syndrome ` +
      `window${d.windows.length === 1 ? "" : "s"} to the decoder · ${into.toFixed(1)} of ` +
      `${len.toFixed(1)} µs${slowed}`;
  }

  // ------------------------------------------------------------------ the loop

  let last = performance.now(), lastPanel = 0, lastHash = 0, panelDue = false;
  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (S.playing) {
      let step = dt * S.rate;
      S.slowed = 0;
      const d = nextDecode(S.t);
      if (d && S.t < d.t0 && S.t + step >= d.t0) {
        step = d.t0 - S.t;                    // stop at a `decode` rather than jump it
      } else if (d && S.t >= d.t0) {
        const slow = (d.t1 - d.t0) / DECODE_WALL_S;
        if (slow < S.rate) { step = Math.min(step, dt * slow); S.slowed = S.rate / slow; }
      }
      S.t += step;
      if (S.t >= M.makespan) { S.t = M.makespan; S.playing = false; $("bPlay").textContent = "▶"; }
      if (FOLD && outsideWindow(S.t)) refold(S.t);
      if (S.t > S.tl.t1) { const span = S.tl.t1 - S.tl.t0; S.tl.t0 = S.t - span * 0.1; S.tl.t1 = S.tl.t0 + span; }
      S.dirty = true;
    }
    if (FOLD && foldStale) refold(S.t);
    if (S.dirty && editing()) {
      window.GADGET_EDITOR.draw();
      S.dirty = false;
    } else if (S.dirty) {
      drawStage();
      drawTimeline();
      $("clock").innerHTML = `${fmtTime(S.t)} <small>/ ${fmtTime(M.makespan)}</small>`;
      decodeBadge();
      // the level and instance inspectors quote live counts, so they follow the clock too
      if (now - lastPanel > 180) { updateProgram(); stkUpdate(); updateTreeDots(); if (!S.sel || S.sel.kind === "inst") renderInspector(); lastPanel = now; panelDue = false; }
      else panelDue = true;
      if (now - lastHash > 1000) { writeHash(); lastHash = now; }
      S.dirty = false; S.tlDirty = false;
    } else if (S.tlDirty) { drawTimeline(); S.tlDirty = false; }
    // A PANEL REFRESH THE THROTTLE SKIPPED IS OWED, NOT DROPPED.  Two seeks inside 180 ms --
    // clicking a `decode` just after anything else -- used to leave the Program pane showing
    // the first moment until something else moved the clock.
    if (panelDue && !S.dirty && now - lastPanel > 180) {
      updateProgram(); stkUpdate(); updateTreeDots(); if (!S.sel || S.sel.kind === "inst") renderInspector();
      lastPanel = now; panelDue = false;
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ boot

  const ASKED = readHash();
  $("designTitle").textContent = `${M.gir.program.title || M.masters[M.top].title} · ${M.gir.stats ? M.gir.stats.ions.toLocaleString() + " ions" : ""}`;
  document.title = `${M.gir.program.title || M.gir.program.name} — QCCD gadgets`;
  $("rate").value = String(S.rate);
  $("bIons").className = S.showIons ? "on" : "";
  $("bClassical").className = S.showClassical ? "on" : "";
  // a folded run opens on its first window, not on hours squeezed into one canvas
  S.tl = { t0: 0, t1: FOLD ? Math.min(M.makespan, FOLD_SPAN) : Math.max(1, M.makespan) };
  for (const p of M.ancestors(S.path)) treeOpen.add(p);
  renderChecks();
  renderSignoff();
  renderTraffic();
  setupFloorplan();
  colsDefine();
  renderProgram();
  stkInit();
  colsInit();
  stkUpdate();
  legend();
  go(S.path);
  fit();
  if (ASKED.focus) focusOn(ASKED.focus);
  if (ASKED.t !== null) seek(ASKED.t);
  requestAnimationFrame(frame);

  // the handle a headless test drives
  window.GADGETS = { M, S, D, FOLD, go, seek, select, fit, leafIons, drawStage, drawTimeline,
    WIRES, MSGS, FRAMES, CONTROL, messagesOn, archiveAt, frameText, guardOf, guardText,
    DECODES, litAt, nextDecode, DECODE_WALL_S, WIRE_LIT, WIRE_GLOW,
    portXY, portCache, COLOR, FAMILY, ROLE, CATS, styleOf, shapeD, shapeSvg, roundRect, haloText, clip, sizeCanvas, stage, sctx,
    X, Y, fmtTime, escapeHtml, renderLibrary, renderInspector, renderTree, crumbs,
    redraw: () => { S.dirty = true; },
    ready: () => Object.keys(D.leafFiles || {}).every((m) => M.leafData[m]),
    // the columns as they stand, and the hardware instruction the panels describe
    cols: () => COLS.map((c) => ({ key: c.key, on: !!c.on && !APP.classList.contains("nocols"), cur: c.cur || 0,
                                   rel: c.mod ? Object.keys(hwMods).find((r) => hwMods[r] === c.mod) : null })),
    focusOp: () => {
      const id = stkFocus(), op = id === null || id === undefined ? null : stkOpNow(id);
      return op ? { id, master: op.master, op: op.e[4], ins: op.ins ? op.ins.id : null } : null;
    } };
  if (window.GADGET_EDITOR) window.GADGET_EDITOR.attach(window.GADGETS);
  // the handle a page that embeds this one drives: `ready()` once every leaf is drawn,
  // `focus(id)` as `#f=`, `seek(us)` as `#t=`
  window.GADGET = {
    ready: window.GADGETS.ready,
    focus: (id) => focusOn(id),
    seek: (us) => { seek(+us || 0); writeHash(); return S.t; },
  };
})();
