// The QEC cycle panel of the studio's Design tab: the round this device runs, and the
// classical loop around it.  Injected by qccd/site/build.py at site-build time, so it
// touches none of the studio's own modules; it reads the page's numbers and nothing else.
//
// The round time is the page's OWN runtime -- PRICE.totals.us after an edit, the shipped
// metrics before one -- so the panel never invents a quantum number.  The classical path
// is __PAYLOAD__, the same table the gadget layer's places and the leaderboard read.
(function () {
  "use strict";
  var CFG = __PAYLOAD__;
  var STATE = { decoder: "lut", mode: "store", open: false, timer: null };

  function fmt(us) {
    if (!us) return "0";
    if (us < 1) return (us * 1000).toFixed(0) + " ns";
    if (us < 1000) return (+us.toPrecision(3)) + " µs";
    if (us < 1e6) return (+(us / 1000).toPrecision(3)) + " ms";
    return (+(us / 1e6).toPrecision(3)) + " s";
  }
  function times(x) {
    if (!isFinite(x)) return "—";
    return x >= 1000 ? Math.round(x).toLocaleString() + "×"
         : x >= 10 ? x.toFixed(0) + "×" : x.toPrecision(2) + "×";
  }
  // The round: what this page measured for the programme on the canvas.  `EDITOR.price()`
  // is the studio's own public accessor for the re-priced totals, so the panel reads the
  // same number the head chips and the Report pane print -- never its own.
  function roundUs() {
    try {
      var P = window.EDITOR && EDITOR.price && EDITOR.price();
      // A BLOCKED price is not a slow round: the device does not run the programme at all.
      // Falling back to the shipped runtime here would answer a question about a different
      // machine, and a "the decoder keeps up" verdict would be confident nonsense.
      if (P && P.blocked && (P.blocked.length === undefined || P.blocked.length))
        return { us: 0, blocked: true,
                 from: "the price is blocked — this device does not run the programme" };
      if (P && P.totals && P.totals.us)
        return { us: P.totals.us,
                 from: "this device, priced in the browser" +
                       (P.frameChecked === false ? ", not yet cross-checked against Python" : "") };
    } catch (e) { /* no live price yet */ }
    try {
      if (typeof D !== "undefined" && D.metrics && D.metrics.runtime_us)
        return { us: D.metrics.runtime_us, from: "the programme Python shipped, replayed" };
    } catch (e) { /* no shipped metrics */ }
    try {   // last resort: the Report pane's own row, which is a formatted string
      var rows = (window.EDITOR && EDITOR.metricRows && EDITOR.metricRows()) || [];
      for (var i = 0; i < rows.length; i++) {
        if (rows[i][0] === "runtime") {
          var ms = parseFloat(String(rows[i][1]));
          if (ms > 0) return { us: ms * 1000, from: "the Report pane's runtime" };
        }
      }
    } catch (e) { /* no rows */ }
    return { us: 0, from: "no programme yet" };
  }
  // the same arithmetic as qccd.analysis.feedback.cycle_report, on the page's own round
  function report(us) {
    var L = CFG.link, prof = CFG.decoders[STATE.decoder];
    var stages = [
      { s: "syndrome extraction", us: us, q: true, w: "the ions" },
      { s: "outcomes to the decoder", us: L.readout_to_control_us, w: "the syndrome wire" },
      { s: "decode", us: prof.latency_us, w: prof.title },
      { s: "frame update", us: L.frame_write_us, w: "the classical memory" }
    ];
    if (STATE.mode === "react") stages.push(
      { s: "resolve the guard", us: L.resolve_us, w: "the classical memory" },
      { s: "decision to the place", us: L.decision_us, w: "the decision wire" });
    var classical = 0;
    for (var i = 1; i < stages.length; i++) classical += stages[i].us;
    var cycle = us + (STATE.mode === "react" ? classical : 0);
    return {
      stages: stages, round: us, classical: classical, cycle: cycle,
      keepsUp: prof.latency_us <= us, margin: prof.latency_us > 0 ? us / prof.latency_us : Infinity,
      perSecond: cycle > 0 ? 1e6 / cycle : 0,
      fraction: us > 0 ? classical / us : 0, decoder: prof
    };
  }
  // Widths by the square root of the time: the stages span four orders of magnitude, so a
  // linear axis would hide the classical path and a log one would flatter it.  Every box
  // carries its own number, and the legend under the drawing repeats them in order.
  function loopSvg(r) {
    var W = 424, H = 96, pad = 6, qw = (W - 2 * pad) * 0.28;
    var cw = (W - 2 * pad) - qw, x = pad, out = [], i, tot = 0;
    for (i = 1; i < r.stages.length; i++) tot += Math.sqrt(r.stages[i].us);
    tot = tot || 1;
    out.push('<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '">');
    for (i = 0; i < r.stages.length; i++) {
      var s = r.stages[i];
      var w = i === 0 ? qw : Math.max(30, cw * Math.sqrt(s.us) / tot);
      var fill = s.q ? "#e0e7ff" : "#ecfdf5", stroke = s.q ? "#3730a3" : "#065f46";
      out.push('<rect x="' + x.toFixed(1) + '" y="24" width="' + w.toFixed(1) + '" height="28" rx="4" fill="' +
               fill + '" stroke="' + stroke + '" stroke-width="1.2"' + (s.q ? '' : ' stroke-dasharray="4 2"') + '/>');
      out.push('<text x="' + (x + w / 2).toFixed(1) + '" y="42" text-anchor="middle" font-size="9" fill="#667085">' +
               fmt(s.us) + '</text>');
      x += w;
    }
    out.push('<path d="M' + x.toFixed(1) + ' 52 V78 H' + (pad + qw / 2).toFixed(1) +
             ' V56" fill="none" stroke="#065f46" stroke-width="1.2" stroke-dasharray="4 2"/>');
    out.push('<path d="M' + (pad + qw / 2 - 3.5).toFixed(1) + ' 62 L' + (pad + qw / 2).toFixed(1) + ' 54 L' +
             (pad + qw / 2 + 3.5).toFixed(1) + ' 62 Z" fill="#065f46"/>');
    out.push('<text x="' + pad + '" y="16" font-size="9" fill="#3730a3">one round (quantum)</text>');
    out.push('<text x="' + (pad + qw + 6) + '" y="16" font-size="9" fill="#065f46">the classical path: ' +
             fmt(r.classical) + '</text>');
    out.push('<text x="' + pad + '" y="92" font-size="9" fill="#065f46">' +
             (STATE.mode === "react" ? "↳ back at the place, and only then may its op start"
                                     : "↳ it stops in the classical memory: no ion waits") + '</text>');
    out.push('</svg>');
    var leg = r.stages.map(function (s) { return "<b>" + esc(s.s) + "</b> " + fmt(s.us); }).join(" · ");
    return out.join("") + '<p class="qc-note">' + leg + '</p>';
  }
  function esc(s) { return String(s).replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); }

  function render(box) {
    var got = roundUs(), r = report(got.us);
    var opts = CFG.shown.map(function (k) {
      return '<option value="' + k + '"' + (k === STATE.decoder ? " selected" : "") + '>' +
             esc(CFG.decoders[k].title) + " · " + fmt(CFG.decoders[k].latency_us) + '</option>';
    }).join("");
    var h = '';
    if (got.blocked) {
      h += '<p class="qc-note"><b>No round to report.</b> ' + esc(got.from) +
           ', so there is no time to build a cycle on. Fix the problems the status line ' +
           'lists, or re-verify in Python, and this panel will read the round again.</p>';
    } else if (!got.us) {
      h += '<p class="qc-note">No programme priced yet — press <b>Test drive</b> or write one in ' +
           '<b>Write</b>, and this panel reads its runtime as one syndrome-extraction round.</p>';
    }
    h += '<div class="qc-head">' +
         '<label><input type="checkbox" id="qcLayerOn"' + (LAYER.on ? " checked" : "") +
         '> show it on the device</label>' +
         '<label>decoder <select id="qcDec">' + opts + '</select></label>' +
         '<label>the answer <select id="qcMode">' +
         '<option value="store"' + (STATE.mode === "store" ? " selected" : "") + '>stops in the classical memory</option>' +
         '<option value="react"' + (STATE.mode === "react" ? " selected" : "") + '>comes back to the ions</option>' +
         '</select></label></div>';
    h += loopSvg(r);
    h += '<table class="qc-t"><tr><th>one round</th><td>' + fmt(r.round) + '</td>' +
         '<td class="qc-w">' + esc(got.from) + '</td></tr>' +
         '<tr><th>QEC clock</th><td>' + (r.perSecond ? r.perSecond.toFixed(r.perSecond < 10 ? 2 : 0) : "—") +
         ' cycles/s</td><td class="qc-w">cycle ' + fmt(r.cycle) + '</td></tr>' +
         '<tr><th>classical path</th><td>' + fmt(r.classical) + '</td>' +
         '<td class="qc-w">' + (STATE.mode === "react" ? "wire, decode, write, resolve, wire back"
                                                       : "wire, decode, write") + '</td></tr></table>';
    if (got.us) {
    h += '<div class="qc-v ' + (r.keepsUp ? "ok" : "bad") + '"><b>' + (r.keepsUp ? "✓" : "✗") +
         (r.keepsUp ? " the decoder keeps up" : " the decoder falls behind") + '</b> · ' +
         times(r.margin) + ' of margin: one round is ' + fmt(r.round) +
         ' and decoding it takes ' + fmt(r.decoder.latency_us) +
         (r.keepsUp ? ', so the undecoded backlog never grows.'
                    : ', so the backlog grows by ' + fmt(r.decoder.latency_us - r.round) +
                      ' every round — no amount of buffering fixes that.') + '</div>';
    var cheap = r.fraction < 0.1;
    h += '<div class="qc-v ' + (cheap ? "ok" : "bad") + '"><b>' + (cheap ? "✓" : "✗") +
         (cheap ? " feedback is nearly free" : " feedback costs a real part of the cycle") +
         '</b> · the reaction time is ' + fmt(r.classical) + ', ' +
         (r.round ? (100 * r.fraction).toPrecision(2) : "–") + '% of a cycle' +
         (STATE.mode === "react" ? ': what a conditional operation waits for.'
                                 : ' — but in this mode no ion waits for it.') + '</div>';
    }
    h += '<p class="qc-note">' + esc(CFG.modes[STATE.mode]) + '</p>';
    h += '<p class="qc-note">' + esc(r.decoder.note) + ' <i>' + esc(r.decoder.source) + '</i></p>';
    h += '<p class="qc-note">The wire charges ' + fmt(CFG.link.readout_to_control_us) +
         ' — ' + esc(CFG.link.readout_to_control_note) + '. The round above is this page\'s own ' +
         'number; the loop is scheduled, drawn and checked ion by ion under ' +
         '<a href="gadgets/algorithms/">Logical Algorithm</a>.</p>';
    box.innerHTML = h;
    var d = box.querySelector("#qcDec"), m = box.querySelector("#qcMode");
    if (d) d.onchange = function () { STATE.decoder = d.value; render(box); drawLayer(); };
    if (m) m.onchange = function () { STATE.mode = m.value; render(box); drawLayer(); };
    var lay = box.querySelector("#qcLayerOn");
    if (lay) lay.onchange = function () { setShown(lay.checked); };
  }

  // ---------------------------------------------------------------- the classical layer
  //
  // The device drawing shows the ions and nothing else, so the half of the machine that
  // reads them is invisible: this draws it, over the stage and in the same frame of
  // reference.  Wires leave the sites where measurement is allowed (the zones whose type
  // permits SPAM), meet at a bus, cross to the decoder, and its answer goes on to the
  // classical memory -- with the return arrow that makes it a loop rather than a pipeline.
  //
  // It is an OVERLAY, never a change to the page's own SVG: positions come from the
  // device's layout through the stage's current transform, so the layer follows every pan,
  // zoom and resize, and if the studio redraws its stage nothing of mine is lost.
  var LAYER = { on: true, el: null, sig: "", fitted: "" };

  function stageSvg() { return document.getElementById("svg"); }

  // ---------------------------------------------------------------- showing it at all
  //
  // THE READER MAY NOT WANT IT.  The classical half is drawn by default, because without it
  // a page that runs a circuit hides the machine that reads the circuit out.  But a reader
  // studying the ions can put it away, with the "Classical" box in the tools bar or the one
  // in this panel (they are one setting).  The wires, the decoder, the memory and a decode's
  // lit path go TOGETHER: a wire running to a place that is not drawn reads as a bug.
  //
  // Remembered per viewer, in `localStorage`, which may not be there at all (a private
  // window, blocked site data), so every touch of it is guarded and the default is "shown".
  // Putting it away gives the frame back to the device -- but only if the camera is still
  // where `fit()` left it.  A reader who has zoomed in on a trap keeps their view.
  var SHOW_KEY = "qccd.classical";
  function remembered() {
    try { return window.localStorage.getItem(SHOW_KEY) !== "0"; } catch (e) { return true; }
  }
  function camera() {
    try { return (typeof VB !== "undefined" && VB) ? [VB.x, VB.y, VB.w, VB.h].join(",") : ""; }
    catch (e) { return ""; }
  }
  function setShown(on) {
    on = !!on;
    var untouched = !!LAYER.fitted && camera() === LAYER.fitted;
    try { window.localStorage.setItem(SHOW_KEY, on ? "1" : "0"); } catch (e) { /* not kept */ }
    var boxes = [document.getElementById("qcShowOn"), document.getElementById("qcLayerOn")];
    boxes.forEach(function (b) { if (b) b.checked = on; });
    if (on === LAYER.on) return;
    LAYER.on = on;
    LAYER.sig = "";
    drawLayer();
    paintLit();                  // off, it clears a decode's lit path as well
    if (untouched) {
      try { if (typeof fit === "function") { fit(); if (typeof draw === "function") draw(); } }
      catch (e) { /* not a studio page */ }
    }
  }

  function anchors(max) {
    // IN THE DEVICE'S OWN COORDINATES, not the screen's.  The layer lives inside the stage
    // svg, so the viewBox the studio rewrites when you zoom carries these points with the
    // ions -- there is no transform to apply and nothing to keep in step.
    var svg = stageSvg();
    if (!svg || typeof D === "undefined" || !D.arch || !D.layout) return null;
    var L = D.layout, zt = D.arch.zone_types || {};
    var spam = {};
    for (var z in zt) if (zt[z] && zt[z].spam) spam[z] = 1;
    var nodes = D.arch.nodes.filter(function (n) { return n.kind !== "junction" && spam[n.zone]; });
    if (!nodes.length) nodes = D.arch.nodes.filter(function (n) { return n.kind !== "junction"; });
    if (!nodes.length) return null;
    var all = D.arch.nodes.map(function (n) { return [n.x * L.sx + L.ox, n.y * L.sy + L.oy]; });
    // `g`, the nearest-neighbour distance, is the unit everything else is a fraction of --
    // the same unit the studio draws sites and ions with, which is what makes the classical
    // half sit at the same scale as the quantum half instead of at some pixel size.
    var g = Infinity;
    for (var i = 1; i < all.length && i < 400; i++) {
      var dx = all[i][0] - all[i - 1][0], dy = all[i][1] - all[i - 1][1];
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d > 1e-6) g = Math.min(g, d);
    }
    if (!isFinite(g) || g <= 0) g = 20;
    var xs = all.map(function (q) { return q[0]; }), ys = all.map(function (q) { return q[1]; });
    var step = Math.max(1, Math.floor(nodes.length / max)), out = [];
    for (var j = 0; j < nodes.length && out.length < max; j += step) {
      var n = nodes[j];
      out.push({ id: n.id, zone: n.zone, x: n.x * L.sx + L.ox, y: n.y * L.sy + L.oy });
    }
    return { pts: out, total: nodes.length, g: g,
             x0: Math.min.apply(null, xs), x1: Math.max.apply(null, xs),
             y0: Math.min.apply(null, ys), y1: Math.max.apply(null, ys) };
  }

  function layerEl() {
    // A GROUP INSIDE THE STAGE, not an overlay over the page.  Two things follow: the
    // browser applies the camera for us, and the element can no longer paint a sheet over
    // anything -- the opaque-overlay bug this file once had is structurally gone.
    var svg = stageSvg();
    if (!svg) return null;
    if (LAYER.el && LAYER.el.parentNode === svg) return LAYER.el;
    var el = document.createElementNS("http://www.w3.org/2000/svg", "g");
    el.setAttribute("id", "qcLayer");
    el.setAttribute("pointer-events", "none");
    svg.appendChild(el);
    LAYER.el = el;
    return el;
  }

  // ---------------------------------------------------------------- the places, as themselves
  //
  // Ported from `qccd/gadget/svg.py::shape_d`, so the decoder here is the same chip the
  // gadget tool draws and the classical memory is the same drum.  A place that is one shape
  // in one picture and a rounded rectangle in another is two places to a reader.
  function chipPath(x, y, w, h) {
    var m = Math.min(w, h);
    var n = Math.max(2, Math.min(6, Math.floor(h / Math.max(1e-6, m * 0.3))));
    var pin = Math.min(w * 0.12, h / (2 * n + 1));
    var d = "M" + (x + pin) + " " + y + "H" + (x + w - pin) + "V" + (y + h) + "H" + (x + pin) + "Z";
    for (var i = 0; i < n; i++) {
      var cy = y + h * (i + 0.5) / n;
      d += "M" + (x + pin) + " " + (cy - pin / 2) + "H" + x + "V" + (cy + pin / 2) + "H" + (x + pin) + "Z" +
           "M" + (x + w - pin) + " " + (cy - pin / 2) + "H" + (x + w) + "V" + (cy + pin / 2) + "H" + (x + w - pin) + "Z";
    }
    return d;
  }
  function drumPath(x, y, w, h) {
    var r = Math.min(w * 0.16, h / 2);
    return "M" + (x + r) + " " + y + "H" + (x + w - r) + "A" + r + " " + (h / 2) + " 0 0 1 " +
           (x + w - r) + " " + (y + h) + "H" + (x + r) + "A" + r + " " + (h / 2) + " 0 0 1 " +
           (x + r) + " " + y + "Z" +
           "M" + (x + w - r) + " " + y + "A" + r + " " + (h / 2) + " 0 0 0 " + (x + w - r) + " " + (y + h);
  }

  function box(x, y, w, h, fill, stroke, label, sub) {
    var g = '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="4" fill="' +
            fill + '" stroke="' + stroke + '" stroke-width="1.4"/>';
    g += '<text x="' + (x + w / 2) + '" y="' + (y + 14) + '" text-anchor="middle" font-size="10.5" ' +
         'font-weight="600" fill="' + stroke + '">' + label + '</text>';
    if (sub) g += '<text x="' + (x + w / 2) + '" y="' + (y + h - 5) + '" text-anchor="middle" ' +
                  'font-size="9" fill="#667085">' + sub + '</text>';
    return g;
  }

  function drawLayer() {
    var el = LAYER.on ? layerEl() : LAYER.el;
    if (!el) return;
    if (!LAYER.on) { el.innerHTML = ""; return; }
    var a = anchors(8);
    if (!a) { el.innerHTML = ""; return; }
    var prof = CFG.decoders[STATE.decoder];
    var P = CFG.places || {};
    var W = P.wire || { stroke: "#065f46" };
    var DEC = P.decoder || { title: "Decoder", fill: "#dcfce7", stroke: "#15803d" };
    var MEM = P.archive || { title: "Classical Memory", fill: "#dbeafe", stroke: "#1e40af" };

    // EVERY LENGTH IS A MULTIPLE OF `g`, the lattice step, so the classical half is drawn
    // at the same scale as the traps it is wired to -- on a 72-site ring and on a 4-site
    // register alike -- and it stays that way through every zoom, because the browser
    // scales this group with the rest of the stage.
    var g = a.g;
    // the drum is a little wider than the chip: its near rim takes a slice of the face, and
    // what is left has to hold its name
    var dec = { w: 3.4 * g, h: 1.5 * g }, mem = { w: 3.8 * g, h: 1.5 * g };
    var floorY = a.y1 + 2.6 * g;                       // the control floor, under the device
    var busY = floorY - 0.9 * g;
    var span = Math.max(a.x1 - a.x0, 6 * g);
    var decX = a.x0 + span * 0.42, memX = decX + dec.w + 1.9 * g;
    var sw = Math.max(g * 0.055, 0.6);                 // a hairline at this device's scale
    var fs = 0.62 * g, fsm = 0.52 * g;                 // labels, likewise
    var dash = (0.34 * g).toFixed(2) + " " + (0.24 * g).toFixed(2);
    var out = [];
    LAYER.geo = { g: g, dec: dec, mem: mem, floorY: floorY, busY: busY, decX: decX,
                  memX: memX, sw: sw, fs: fs, fsm: fsm, dash: dash, x0: a.x0, x1: a.x1,
                  W: W, DEC: DEC, MEM: MEM };
    // what the studio's `fit()` must frame so the decoder and the memory are in the
    // picture: the device, the floor under it and the loop back (`STAGE_EXTENT`)
    LAYER.bounds = { x0: Math.min(a.x0, decX) - 0.6 * g, y0: a.y0 - 0.6 * g,
                     x1: Math.max(a.x1, memX + mem.w) + 0.6 * g,
                     y1: floorY + Math.max(dec.h, mem.h) + 1.5 * g };

    // the syndrome wires: from each place that may measure, down to the bus
    a.pts.forEach(function (q) {
      out.push('<path d="M' + q.x.toFixed(2) + ' ' + q.y.toFixed(2) + ' V' + busY.toFixed(2) +
               '" fill="none" stroke="' + W.stroke + '" stroke-width="' + sw.toFixed(2) +
               '" stroke-dasharray="' + dash + '" opacity=".75"/>');
      out.push('<circle cx="' + q.x.toFixed(2) + '" cy="' + q.y.toFixed(2) + '" r="' +
               (0.13 * g).toFixed(2) + '" fill="' + W.stroke + '" opacity=".85"/>');
    });
    out.push('<path d="M' + a.x0.toFixed(2) + ' ' + busY.toFixed(2) + ' H' +
             Math.max(a.x1, decX + dec.w / 2).toFixed(2) + '" fill="none" stroke="' + W.stroke +
             '" stroke-width="' + (sw * 1.5).toFixed(2) + '" stroke-dasharray="' + dash + '"/>');
    // BELOW the bus and LEFT of the decoder's feed, right-aligned to it.  Above the bus every
    // syndrome wire comes down (and on a decode frame the lit ones are painted after this
    // layer, so no halo hides them); right of the feed is where the lit layer writes
    // "decode · N outcomes".  Left of the feed and below the bus, nothing else is drawn.  The
    // type shrinks to the width a device leaves there, so a small board keeps it in frame.
    var feedX = decX + dec.w / 2, busText = a.total + ' sites → decoder, ' +
                fmt(CFG.link.readout_to_control_us);
    var room = Math.max(g, feedX - 0.3 * g - a.x0);
    var fbus = Math.max(0.28 * fsm, Math.min(fsm, room * 0.9 / Math.max(1, busText.length * 0.55)));
    // held back and written LAST, so its halo lies over this layer's own return path too
    var busLabel = '<text x="' + (feedX - 0.3 * g).toFixed(2) + '" y="' + (busY + 0.62 * g).toFixed(2) +
             '" text-anchor="end" font-size="' + fbus.toFixed(2) + '" fill="' + W.stroke +
             '" paint-order="stroke" stroke="#fff" stroke-width="' + (0.22 * fbus).toFixed(2) +
             '" stroke-linejoin="round">' + busText + '</text>';

    // the two places, in the shapes the gadget library gives them: a chip, and a drum
    out.push(place(chipPath(decX, floorY, dec.w, dec.h), decX + dec.w / 2, floorY, dec.h,
                   DEC.fill, DEC.stroke, DEC.title, prof.title.split(" (")[0], sw, fs, fsm, dec.w));
    // The drum's near rim is an arc bulging LEFT from x+w-r by r, so the clear face runs
    // from x+r to x+w-2r.  Centring the name on the whole drum put "Classical Memory" under
    // that arc; it is centred on, and fitted to, the face that is actually clear.
    var rim = Math.min(mem.w * 0.16, mem.h / 2);
    out.push(place(drumPath(memX, floorY, mem.w, mem.h), memX + (mem.w - rim) / 2, floorY, mem.h,
                   MEM.fill, MEM.stroke, MEM.title, "frames · outcomes", sw, fs, fsm,
                   Math.max(mem.w - 3 * rim, mem.w * 0.4)));

    // decoder to memory, with the decode time on the wire
    out.push('<path d="M' + (decX + dec.w).toFixed(2) + ' ' + (floorY + dec.h / 2).toFixed(2) +
             ' H' + memX.toFixed(2) + '" fill="none" stroke="' + W.stroke + '" stroke-width="' +
             (sw * 1.5).toFixed(2) + '" stroke-dasharray="' + dash + '"/>');
    out.push('<text x="' + (decX + dec.w + 0.25 * g).toFixed(2) + '" y="' +
             (floorY + dec.h / 2 - 0.3 * g).toFixed(2) + '" font-size="' + fsm.toFixed(2) +
             '" fill="' + W.stroke + '">' + fmt(prof.latency_us) + '</text>');

    // and the loop back to the ions, which is what makes it a loop and not a pipeline
    var backX = (a.x0 + a.x1) / 2, topY = a.y0 - 0.1 * g;
    out.push('<path d="M' + (memX + mem.w / 2).toFixed(2) + ' ' + (floorY + mem.h).toFixed(2) +
             ' V' + (floorY + mem.h + 0.7 * g).toFixed(2) + ' H' + backX.toFixed(2) + ' V' +
             topY.toFixed(2) + '" fill="none" stroke="' + MEM.stroke + '" stroke-width="' + (sw * 1.3).toFixed(2) +
             '" stroke-dasharray="' + dash + '" opacity=".85"/>');
    out.push('<path d="M' + (backX - 0.22 * g).toFixed(2) + ' ' + (topY + 0.42 * g).toFixed(2) +
             ' L' + backX.toFixed(2) + ' ' + topY.toFixed(2) + ' L' + (backX + 0.22 * g).toFixed(2) +
             ' ' + (topY + 0.42 * g).toFixed(2) + ' Z" fill="' + MEM.stroke + '" opacity=".85"/>');
    out.push('<text x="' + (backX + 0.35 * g).toFixed(2) + '" y="' +
             (floorY + mem.h + 0.55 * g).toFixed(2) + '" font-size="' + fsm.toFixed(2) +
             '" fill="' + MEM.stroke + '">frame update, or a guard</text>');
    out.push(busLabel);
    el.innerHTML = out.join("");
    LIT.key = null;                        // the base moved: the lit layer redraws on it
    paintLit();
  }

  // ---------------------------------------------------------------- a decode, lit
  //
  // DECODING IS AN INSTRUCTION (`decode` in docs/tsir.md), and when the studio is on one
  // the wires it uses light up: from every site where one of its ions was MEASURED -- not
  // where the ion stands now, since it may have moved on since -- down to the bus, along
  // it to the decoder, and from the decoder on to the classical memory, with the decoder
  // and the memory outlined and the outcomes moving down the wires as the frame plays.
  //
  // Cheap by construction, because a board can have a hundred and forty wires: the lit
  // group is rebuilt only when the frame CHANGES to or from a decode (keyed on the frame's
  // id), and within a decode frame only the packets' positions are written.  Colours are
  // `CFG.places.wire.lit` / `.glow`, the gadget canvas's own, from `categories.py`.
  var LIT = { el: null, key: null, parts: null, cache: {} };

  function litEl() {
    var svg = stageSvg();
    if (!svg) return null;
    if (LIT.el && LIT.el.parentNode === svg) return LIT.el;
    var el = document.createElementNS("http://www.w3.org/2000/svg", "g");
    el.setAttribute("id", "qcLit");
    el.setAttribute("pointer-events", "none");
    svg.appendChild(el);
    LIT.el = el;
    return el;
  }

  function studioAt() {
    // the studio's own state, read by name: `frame` and `phase` are its playhead,
    // `P.frames` the programme and `states[j]` where every ion is after frame j
    try {
      if (typeof P === "undefined" || !P.frames || typeof frame === "undefined") return null;
      return { fi: frame, ph: (typeof phase === "number" ? phase : 1), frames: P.frames,
               states: (typeof states !== "undefined" ? states : []) };
    } catch (e) { return null; }
  }

  // Where the ions a decode reads were measured: for each, the last `measure` frame before
  // the decode that names it, and its site then.  Grouped by site, cached per frame id.
  function decodeSites(at, fi) {
    var f = at.frames[fi];
    var key = String(f.id) + "@" + fi;
    if (LIT.cache[key]) return LIT.cache[key];
    var L = D.layout, byId = {};
    D.arch.nodes.forEach(function (n) { byId[n.id] = n; });
    var want = {}, left = 0;
    (f.ions || []).forEach(function (ion) { want[ion] = null; left++; });
    for (var j = fi - 1; j >= 0 && left > 0; j--) {
      var fj = at.frames[j];
      if (!fj || fj.type !== "measure") continue;
      var st = at.states[j] || { pos: {} };
      (fj.ions || []).forEach(function (ion) {
        if (want[ion] === null && st.pos[ion] !== undefined) { want[ion] = st.pos[ion]; left--; }
      });
    }
    var now = at.states[fi] || { pos: {} }, bySite = {}, sites = [];
    for (var ion in want) {
      var node = want[ion] !== null ? want[ion] : now.pos[ion];
      var n = byId[node];
      if (!n) continue;
      if (!bySite[node]) {
        bySite[node] = { node: node, x: n.x * L.sx + L.ox, y: n.y * L.sy + L.oy, ions: [] };
        sites.push(bySite[node]);
      }
      bySite[node].ions.push(ion);
    }
    var out = { sites: sites, n: (f.ions || []).length, id: f.id };
    LIT.cache[key] = out;
    return out;
  }

  function paintLit() {
    var el = litEl();
    if (!el) return;
    var at = LAYER.on && !embedded() ? studioAt() : null;
    var f = at ? at.frames[at.fi] : null;
    var geo = LAYER.geo;
    var key = (f && f.type === "decode" && geo) ? String(f.id) + "@" + at.fi + "|" + LAYER.sig : "";
    if (key !== LIT.key) {
      LIT.key = key;
      LIT.parts = null;
      el.innerHTML = key ? buildLit(decodeSites(at, at.fi), geo) : "";
      if (key) LIT.parts = {
        packets: Array.prototype.slice.call(el.querySelectorAll(".qc-pk")),
        bus: el.querySelector(".qc-bus-pk"), frame: el.querySelector(".qc-fr-pk"),
        wires: Array.prototype.slice.call(el.querySelectorAll(".qc-lw")),
        info: decodeSites(at, at.fi)
      };
    }
    if (key && LIT.parts) movePackets(LIT.parts, geo, at.ph);
  }

  function buildLit(info, geo) {
    var g = geo.g, W = geo.W, lit = W.lit || "#b45309", glow = W.glow || "#fcd34d";
    var sw = geo.sw, busY = geo.busY, dash = geo.dash;
    var decCx = geo.decX + geo.dec.w / 2, out = [];
    var xs = info.sites.map(function (s) { return s.x; });
    var bx0 = Math.min.apply(null, xs.concat([decCx])), bx1 = Math.max.apply(null, xs.concat([decCx]));
    var paths = [];
    info.sites.forEach(function (s) {
      paths.push("M" + s.x.toFixed(2) + " " + s.y.toFixed(2) + " V" + busY.toFixed(2));
    });
    paths.push("M" + bx0.toFixed(2) + " " + busY.toFixed(2) + " H" + bx1.toFixed(2));
    paths.push("M" + decCx.toFixed(2) + " " + busY.toFixed(2) + " V" + geo.floorY.toFixed(2));
    var fy = geo.floorY + geo.dec.h / 2;
    paths.push("M" + (geo.decX + geo.dec.w).toFixed(2) + " " + fy.toFixed(2) + " H" + geo.memX.toFixed(2));
    var d = paths.join(" ");
    // the glow under, then the wire in the lit colour, dashed as a wire is
    // light enough that the device still reads through it: a board can light a hundred
    // wires at once, and they cross the traps on their way down to the bus
    out.push('<path d="' + d + '" fill="none" stroke="' + glow + '" stroke-width="' + (sw * 3).toFixed(2) +
             '" stroke-linecap="round" stroke-linejoin="round" opacity=".55"/>');
    out.push('<path class="qc-lw" d="' + d + '" fill="none" stroke="' + lit + '" stroke-width="' +
             (sw * 1.3).toFixed(2) + '" stroke-dasharray="' + dash + '"/>');
    // the sites that measured, ringed; the decoder and the memory, outlined
    info.sites.forEach(function (s) {
      out.push('<circle cx="' + s.x.toFixed(2) + '" cy="' + s.y.toFixed(2) + '" r="' + (0.2 * g).toFixed(2) +
               '" fill="none" stroke="' + lit + '" stroke-width="' + (sw * 1.3).toFixed(2) + '"/>');
    });
    [chipPath(geo.decX, geo.floorY, geo.dec.w, geo.dec.h),
     drumPath(geo.memX, geo.floorY, geo.mem.w, geo.mem.h)].forEach(function (p) {
      out.push('<path d="' + p + '" fill="none" stroke="' + glow + '" stroke-width="' + (sw * 5).toFixed(2) +
               '" opacity=".8" stroke-linejoin="round"/>');
      out.push('<path d="' + p + '" fill="none" stroke="' + lit + '" stroke-width="' + (sw * 2).toFixed(2) +
               '" stroke-linejoin="round"/>');
    });
    // the outcomes on their way: one packet per site, one along the bus, one frame update
    var r = 0.11 * g;
    info.sites.forEach(function () {
      out.push('<rect class="qc-pk" width="' + (2 * r).toFixed(2) + '" height="' + (1.4 * r).toFixed(2) +
               '" fill="' + lit + '" stroke="#fff" stroke-width="' + (sw * 0.8).toFixed(2) + '"/>');
    });
    out.push('<rect class="qc-bus-pk" width="' + (3 * r).toFixed(2) + '" height="' + (1.8 * r).toFixed(2) +
             '" fill="' + lit + '" stroke="#fff" stroke-width="' + (sw * 0.8).toFixed(2) + '"/>');
    out.push('<rect class="qc-fr-pk" width="' + (2 * r).toFixed(2) + '" height="' + (1.4 * r).toFixed(2) +
             '" fill="' + lit + '" stroke="#fff" stroke-width="' + (sw * 0.8).toFixed(2) + '"/>');
    // said once, short, just right of where the outcomes drop into the decoder -- the rings
    // say where from and the lit wire says where to.  That strip, above the decoder and the
    // memory, is always inside the frame (`STAGE_EXTENT` spans to the memory's far side)
    // and nothing else is drawn in it; anchored to the decoder's LEFT it ran off the frame,
    // and anchored at the device's edge the connector cut through it.
    out.push('<text x="' + (decCx + 0.3 * g).toFixed(2) + '" y="' + (geo.floorY - 0.25 * g).toFixed(2) +
             '" font-size="' + geo.fsm.toFixed(2) + '" font-weight="600" fill="' + lit +
             '">decode · ' + info.n + ' outcome' + (info.n === 1 ? '' : 's') + '</text>');
    return out.join("");
  }

  // The frame's phase drives the bits: down each wire over the first half, along the bus
  // to the decoder over the next third, and the decoder's frame update to the memory at
  // the end -- so a paused frame, a stepped frame and a screenshot all show the same thing.
  function movePackets(parts, geo, ph) {
    var t = Math.max(0, Math.min(1, ph));
    var decCx = geo.decX + geo.dec.w / 2, r = 0.11 * geo.g;
    var down = Math.min(1, t / 0.5);
    parts.packets.forEach(function (el, k) {
      var s = parts.info.sites[k];
      if (!s) return;
      var y = s.y + (geo.busY - s.y) * down;
      el.setAttribute("x", (s.x - r).toFixed(2));
      el.setAttribute("y", (y - 0.7 * r).toFixed(2));
      el.setAttribute("opacity", t <= 0.52 ? "1" : "0");
    });
    if (parts.bus) {
      var along = Math.max(0, Math.min(1, (t - 0.5) / 0.33));
      var xs = parts.info.sites.map(function (s) { return s.x; });
      var from = xs.length ? xs.reduce(function (a, b) { return a + b; }, 0) / xs.length : decCx;
      var x = from + (decCx - from) * along;
      var y = along < 1 ? geo.busY : geo.busY + (geo.floorY - geo.busY) * Math.min(1, (t - 0.83) / 0.05);
      parts.bus.setAttribute("x", (x - 1.5 * r).toFixed(2));
      parts.bus.setAttribute("y", (y - 0.9 * r).toFixed(2));
      parts.bus.setAttribute("opacity", t > 0.5 && t < 0.9 ? "1" : "0");
    }
    if (parts.frame) {
      var fr = Math.max(0, Math.min(1, (t - 0.88) / 0.12));
      var fx = geo.decX + geo.dec.w + (geo.memX - geo.decX - geo.dec.w) * fr;
      parts.frame.setAttribute("x", (fx - r).toFixed(2));
      parts.frame.setAttribute("y", (geo.floorY + geo.dec.h / 2 - 0.7 * r).toFixed(2));
      parts.frame.setAttribute("opacity", t >= 0.88 ? "1" : "0");
    }
    // the dashes march the way the bits go, by the frame's own clock
    parts.wires.forEach(function (w) {
      w.setAttribute("stroke-dashoffset", (-t * 6 * 0.58 * geo.g).toFixed(2));
    });
  }

  //: One place: its silhouette, its name, and what it is doing -- the same three things the
  //: gadget tool shows, in the same order.
  function place(d, cx, y, h, fill, stroke, label, sub, sw, fs, fsm, w) {
    // The label belongs INSIDE the silhouette, so it is shrunk to fit rather than allowed to
    // spill across the drawing: a name wider than the thing it names reads as a caption that
    // has come loose.  0.55 em per character is the usual approximation for this stack.
    var fit = function (text, want) {
      return Math.max(0.28 * fs, Math.min(want, (w * 0.86) / Math.max(1, text.length * 0.55)));
    };
    // A two-word name that would have to shrink below half size to fit on one line is set
    // on two instead: "Classical / Memory" at a readable size beats one line nobody can read.
    var words = String(label).split(" "), two = false, f1 = fit(label, fs);
    if (words.length === 2 && f1 < 0.5 * fs) {
      two = true;
      f1 = Math.min(fit(words[0], fs), fit(words[1], fs));
    }
    var f2 = fit(sub || "", fsm);
    var g = '<path d="' + d + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="' +
            (sw * 1.6).toFixed(2) + '" stroke-linejoin="round"/>';
    if (two) {
      g += '<text x="' + cx.toFixed(2) + '" text-anchor="middle" font-size="' + f1.toFixed(2) +
           '" font-weight="600" fill="' + stroke + '">' +
           '<tspan x="' + cx.toFixed(2) + '" y="' + (y + h * 0.36).toFixed(2) + '">' + esc(words[0]) + '</tspan>' +
           '<tspan x="' + cx.toFixed(2) + '" y="' + (y + h * 0.36 + f1 * 1.05).toFixed(2) + '">' + esc(words[1]) + '</tspan>' +
           '</text>';
    } else {
      g += '<text x="' + cx.toFixed(2) + '" y="' + (y + h * (sub ? 0.46 : 0.58)).toFixed(2) +
           '" text-anchor="middle" font-size="' + f1.toFixed(2) + '" font-weight="600" fill="' +
           stroke + '">' + esc(label) + '</text>';
    }
    if (sub) g += '<text x="' + cx.toFixed(2) + '" y="' + (y + h * (two ? 0.86 : 0.78)).toFixed(2) +
                  '" text-anchor="middle" font-size="' + f2.toFixed(2) + '" fill="#667085">' +
                  esc(sub) + '</text>';
    return g;
  }

  function embedded() {
    // an entry page is embedded view-only on the landing page (#embed), and there the
    // page is a picture: no controls, and no layer of mine over it either
    try { return document.body.getAttribute("data-embed") === "1"; } catch (e) { return false; }
  }

  function syncLayer() {
    if (embedded()) {
      // the hash router sets data-embed AFTER this script mounts, so the check has to
      // happen here rather than only at mount: take the control away again if the page
      // turns out to be an embedded, view-only picture
      if (LAYER.el) LAYER.el.innerHTML = "";
      ["qcBtn", "qcShow"].forEach(function (id) {
        var btn = document.getElementById(id);
        if (btn && btn.parentNode) btn.parentNode.removeChild(btn);
      });
      var box = document.getElementById("qcBox");
      if (box) box.hidden = true;
      LAYER.sig = "embed";
      return;
    }
    // THE CAMERA IS NO LONGER OUR BUSINESS.  Pan, zoom and resize move the group with the
    // ions because it is in the stage, so nothing here watches the transform -- which is
    // what the delay was.  What is left is structural: the studio rebuilding the stage
    // (our group goes with it), an edit that moves the device, or a different decoder.
    var svg = stageSvg();
    if (!svg) return;
    var attached = !!(LAYER.el && LAYER.el.parentNode === svg);
    var L = (typeof D !== "undefined" && D.layout) || {};
    var nodes = (typeof D !== "undefined" && D.arch && D.arch.nodes) ? D.arch.nodes.length : 0;
    var sig = [attached, nodes, L.sx, L.sy, L.ox, L.oy, LAYER.on, STATE.decoder].join(",");
    if (sig === LAYER.sig) return;
    LAYER.sig = sig;
    drawLayer();
  }

  function panel() {
    var box = document.getElementById("qcBox");
    if (box) return box;
    box = document.createElement("div");
    box.id = "qcBox";
    box.className = "qc-box";
    box.hidden = true;
    document.body.appendChild(box);
    return box;
  }
  function toggle() {
    var box = panel(), btn = document.getElementById("qcBtn");
    STATE.open = box.hidden;
    box.hidden = !STATE.open;
    if (btn) btn.className = STATE.open ? "on" : "";
    if (STATE.open) {
      render(box);
      var r = btn ? btn.getBoundingClientRect() : { left: 12, bottom: 90 };
      box.style.left = Math.max(8, Math.min(window.innerWidth - 470, r.left)) + "px";
      box.style.top = (r.bottom + 6) + "px";
      STATE.timer = setInterval(function () { if (!box.hidden) render(box); }, 700);
    } else if (STATE.timer) { clearInterval(STATE.timer); STATE.timer = null; }
  }
  function mount() {
    // the site's embedded examples run view-only: no controls there
    if (embedded()) return;
    var tools = document.getElementById("tools");
    if (!tools || document.getElementById("qcBtn")) return;
    var b = document.createElement("button");
    b.id = "qcBtn";
    b.type = "button";
    b.textContent = "QEC cycle";
    b.title = "the syndrome round this device runs, and the classical feedback loop around " +
              "it: does the decoder keep up, and what does acting on its answer cost?";
    // The explain layer is driven by one HINTS table inside the studio, and a `data-hint`
    // with no entry is a hole (the hover card, Explain, the guide and Ctrl+K all go
    // silent, and tests/test_hints.py fails).  So the attribute is added only once the
    // entry exists: no attribute today, a hover card the moment `qec:cycle` lands.
    try {
      if (window.EDITOR && EDITOR.hintFor && EDITOR.hintFor("qec:cycle"))
        b.setAttribute("data-hint", "qec:cycle");
    } catch (e) { /* no explain layer on this page */ }
    b.onclick = toggle;
    var search = tools.querySelector("#search");
    if (search && search.parentNode === tools) tools.insertBefore(b, search);
    else tools.appendChild(b);
    // and beside it, the one switch for the whole classical half (`setShown`)
    var show = document.createElement("label");
    show.id = "qcShow";
    show.title = "Show the classical half of the machine on the device: the wires from the " +
                 "sites that measure, the decoder and the classical memory. Untick it to " +
                 "see the device alone.";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = "qcShowOn";
    cb.checked = LAYER.on;
    cb.onchange = function () { setShown(cb.checked); };
    show.appendChild(cb);
    show.appendChild(document.createTextNode("Classical"));
    tools.insertBefore(show, b.nextSibling);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { var box = document.getElementById("qcBox");
        if (box && !box.hidden) toggle(); }
    });
  }
  // The stage frames what this layer draws, too (the studio's `fit()` asks), so a decode's
  // outcomes are seen ARRIVING at the decoder instead of leaving the frame.
  window.STAGE_EXTENT = function () {
    return (LAYER.on && !embedded() && LAYER.bounds) ? LAYER.bounds : null;
  };

  function start() {
    LAYER.on = remembered();
    mount();
    // where `fit()` last put the camera, so putting the layer away can tell whether the
    // reader has moved it since (`setShown`)
    try {
      if (typeof fit === "function" && !fit.__qcFit) {
        var _qcFit = fit;
        fit = function () {
          var r = _qcFit.apply(this, arguments);
          LAYER.fitted = camera();
          return r;
        };
        fit.__qcFit = true;
      }
    } catch (e) { /* not a studio page */ }
    // every frame the studio draws, the lit layer follows -- a decode is one frame long,
    // shorter than any polling interval, so it has to ride the studio's own draw
    try {
      if (typeof draw === "function" && !draw.__qcLit) {
        var _qcDraw = draw;
        draw = function () {
          var r = _qcDraw.apply(this, arguments);
          try { paintLit(); } catch (e) { /* the stage comes first */ }
          return r;
        };
        draw.__qcLit = true;
      }
    } catch (e) { /* not a studio page */ }
    // the layer is the point of the exercise on a page that shows a circuit, so it draws
    // itself whether or not anyone opens the panel; it re-syncs only when the stage's
    // transform actually changes, so panning and playing cost nothing
    syncLayer();
    // the studio fitted its stage before this layer existed; fit it once more now that the
    // floor is drawn (nothing has moved the camera yet -- the hash router restores steps,
    // never views)
    try { if (LAYER.bounds && typeof fit === "function") { fit(); if (typeof draw === "function") draw(); } }
    catch (e) { /* not a studio page */ }
    // with the layer put away there was nothing to refit for, and the camera is still the
    // one the studio fitted before this script ran
    if (!LAYER.fitted) LAYER.fitted = camera();
    setInterval(syncLayer, 400);
    window.addEventListener("resize", function () { LAYER.sig = ""; syncLayer(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  window.QEC_CYCLE = { report: report, roundUs: roundUs, toggle: toggle, state: STATE, cfg: CFG,
                       layer: LAYER, drawLayer: drawLayer, anchors: anchors,
                       lit: LIT, paintLit: paintLit, decodeSites: decodeSites, studioAt: studioAt,
                       setShown: setShown };
})();
