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
    if (lay) lay.onchange = function () { LAYER.on = lay.checked; LAYER.sig = ""; drawLayer(); };
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
  var LAYER = { on: true, el: null, sig: "" };

  function stageSvg() { return document.getElementById("svg"); }

  function anchors(max) {
    var svg = stageSvg();
    if (!svg || typeof D === "undefined" || !D.arch || !D.layout) return null;
    // `getScreenCTM` is the one thing this layer cannot do without: an overlay with no
    // screen transform has nowhere to put anything, so the honest degradation is to draw
    // nothing rather than to guess.  It is asked for the way `D` is -- the test shim
    // deliberately omits it, and a page that throws there takes a whole page walk with it.
    var ctm = svg.getScreenCTM ? svg.getScreenCTM() : null;
    if (!ctm) return null;
    var L = D.layout, zt = D.arch.zone_types || {};
    var spam = {};
    for (var z in zt) if (zt[z] && zt[z].spam) spam[z] = 1;
    var nodes = D.arch.nodes.filter(function (n) { return n.kind !== "junction" && spam[n.zone]; });
    if (!nodes.length) nodes = D.arch.nodes.filter(function (n) { return n.kind !== "junction"; });
    if (!nodes.length) return null;
    var step = Math.max(1, Math.floor(nodes.length / max)), out = [];
    for (var i = 0; i < nodes.length && out.length < max; i += step) {
      var n = nodes[i];
      var vx = n.x * L.sx + L.ox, vy = n.y * L.sy + L.oy;
      out.push({ id: n.id, zone: n.zone,
                 x: vx * ctm.a + ctm.e, y: vy * ctm.d + ctm.f });
    }
    return { pts: out, total: nodes.length, ctm: ctm };
  }

  function layerEl() {
    if (LAYER.el && LAYER.el.parentNode) return LAYER.el;
    var el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    el.setAttribute("id", "qcLayer");
    // `background:transparent` is load-bearing: the studio's stylesheet sets
    // `svg { background: var(--panel) }`, and this overlay IS an <svg>, so without it the
    // element paints an opaque white sheet over the whole page and hides the device.
    el.style.cssText = "position:fixed;left:0;top:0;width:100%;height:100%;" +
                       "pointer-events:none;z-index:25;background:transparent";
    document.body.appendChild(el);
    LAYER.el = el;
    return el;
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
    var stage = document.getElementById("canvas");
    if (!a || !stage) { el.innerHTML = ""; return; }
    var r = stage.getBoundingClientRect();
    var prof = CFG.decoders[STATE.decoder];
    // the control strip sits under the device, inside the stage, out of the ions' way
    var stripY = Math.min(r.bottom - 74, Math.max.apply(null, a.pts.map(function (p) { return p.y; })) + 46);
    var decX = r.left + r.width * 0.46, memX = r.left + r.width * 0.68;
    var busY = stripY - 16;
    var out = [];
    // every syndrome wire, from a place that may measure down to the bus
    a.pts.forEach(function (p) {
      out.push('<path d="M' + p.x.toFixed(1) + ' ' + p.y.toFixed(1) + ' V' + busY.toFixed(1) +
               '" fill="none" stroke="#065f46" stroke-width="1" stroke-dasharray="4 3" opacity=".75"/>');
      out.push('<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="2.2" fill="#065f46" opacity=".8"/>');
    });
    var x0 = Math.min.apply(null, a.pts.map(function (p) { return p.x; }));
    var x1 = Math.max.apply(null, a.pts.map(function (p) { return p.x; }));
    out.push('<path d="M' + x0.toFixed(1) + ' ' + busY.toFixed(1) + ' H' + Math.max(x1, decX).toFixed(1) +
             '" fill="none" stroke="#065f46" stroke-width="1.4" stroke-dasharray="6 3"/>');
    out.push('<text x="' + x0.toFixed(1) + '" y="' + (busY - 5).toFixed(1) + '" font-size="9.5" fill="#065f46">' +
             a.total + ' sites may measure — their outcomes leave on this bus (' +
             fmt(CFG.link.readout_to_control_us) + ')</text>');
    out.push(box(decX, stripY, 104, 34, "#dcfce7", "#15803d", "decoder", prof.title.split(" (")[0]));
    out.push('<path d="M' + (decX + 104) + ' ' + (stripY + 17) + ' H' + memX + '" fill="none" ' +
             'stroke="#065f46" stroke-width="1.4" stroke-dasharray="6 3"/>');
    out.push('<text x="' + (decX + 110) + '" y="' + (stripY + 12) + '" font-size="9" fill="#065f46">' +
             fmt(prof.latency_us) + '</text>');
    out.push(box(memX, stripY, 118, 34, "#dbeafe", "#1e40af", "classical memory",
                 "frames · outcomes"));
    // the loop back to the ions: dashed, up and left, into the device
    var backX = (x0 + x1) / 2;
    out.push('<path d="M' + (memX + 59) + ' ' + (stripY + 34) + ' V' + (stripY + 48) + ' H' +
             backX.toFixed(1) + ' V' + (Math.min.apply(null, a.pts.map(function (p) { return p.y; })) + 6).toFixed(1) +
             '" fill="none" stroke="#1e40af" stroke-width="1.2" stroke-dasharray="4 3" opacity=".8"/>');
    out.push('<path d="M' + (backX - 4).toFixed(1) + ' ' +
             (Math.min.apply(null, a.pts.map(function (p) { return p.y; })) + 12).toFixed(1) + ' L' +
             backX.toFixed(1) + ' ' + (Math.min.apply(null, a.pts.map(function (p) { return p.y; })) + 4).toFixed(1) +
             ' L' + (backX + 4).toFixed(1) + ' ' +
             (Math.min.apply(null, a.pts.map(function (p) { return p.y; })) + 12).toFixed(1) +
             ' Z" fill="#1e40af" opacity=".8"/>');
    out.push('<text x="' + (backX + 8).toFixed(1) + '" y="' + (stripY + 46) + '" font-size="9" fill="#1e40af">' +
             'the correction: a frame update, or a guard for a conditional op</text>');
    el.innerHTML = out.join("");
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
      var btn = document.getElementById("qcBtn");
      if (btn && btn.parentNode) btn.parentNode.removeChild(btn);
      var box = document.getElementById("qcBox");
      if (box) box.hidden = true;
      LAYER.sig = "embed";
      return;
    }
    var svg = stageSvg();
    var ctm = svg && svg.getScreenCTM ? svg.getScreenCTM() : null;
    var stage = document.getElementById("canvas");
    var sig = !ctm || !stage ? "" : [ctm.a, ctm.d, ctm.e, ctm.f, stage.getBoundingClientRect().width,
                                     stage.getBoundingClientRect().height, LAYER.on, STATE.decoder].join(",");
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
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { var box = document.getElementById("qcBox");
        if (box && !box.hidden) toggle(); }
    });
  }
  function start() {
    mount();
    // the layer is the point of the exercise on a page that shows a circuit, so it draws
    // itself whether or not anyone opens the panel; it re-syncs only when the stage's
    // transform actually changes, so panning and playing cost nothing
    syncLayer();
    setInterval(syncLayer, 400);
    window.addEventListener("resize", function () { LAYER.sig = ""; syncLayer(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  window.QEC_CYCLE = { report: report, roundUs: roundUs, toggle: toggle, state: STATE, cfg: CFG,
                       layer: LAYER, drawLayer: drawLayer, anchors: anchors };
})();
