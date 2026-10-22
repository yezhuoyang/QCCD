// The book's figures: what is being proved, shown in the platform's own studio.
//
// The picture IS the studio -- the same page, engine and animation as everywhere else on the
// site, in its embed mode -- so a reader sees ions on the same rails, traps and transport
// controls they used in the course.  Two kinds of figure:
//
//  * a certificate the chapter reasons about (live): the page asks Lean to replay it
//    (QCCDBook.figure, engine/figure.lean -- the checker's own posAt / look / ionOf / check),
//    turns that replay into a studio programme (p.init, p.shuttle, p.gate, p.measure; a
//    cooling step before each two-qubit gate, which a certificate leaves to the cooling pass),
//    and loads it into an embedded studio for the device.  An edit to the certificate in the
//    chapter redraws it;
//  * a published certificate ("watch"): the embedded studio page of the REAL compiled
//    programme, the circuit in lockstep, exactly as the Compilation page shows it.
//
// Under the studio: Lean's verdict and one row per move and witness with each condition the
// checker evaluated on it.  These rows are what is proved; a click on one steps the studio there.
(function (global) {
  'use strict';
  var QCOL = ['#1f5bb5', '#d1631a', '#2f8f5b', '#7b2fb5', '#b5341f', '#0f8a8a', '#8a6d1f', '#5a5a66'];
  var NS = 'http://www.w3.org/2000/svg';
  var GATE = { cx: 'CX', cz: 'CZ', h: 'H', x: 'X', y: 'Y', z: 'Z', s: 'S', t: 'T', tdg: 'TDG', sdg: 'SDG', swap: 'SWAP',
               rx: 'RX', ry: 'RY', rz: 'RZ' };
  function label(name) { return { tdg: 'T†', sdg: 'S†', measure: 'M', reset: '|0⟩' }[name] || (GATE[name] || name.toUpperCase()); }
  function h(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function el(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }

  // the rails an ion takes from a to b: the shortest path through the device's segments
  function route(dev, a, b) {
    var adj = {};
    dev.segs.forEach(function (s) { (adj[s[0]] = adj[s[0]] || []).push(s[1]); (adj[s[1]] = adj[s[1]] || []).push(s[0]); });
    var prev = {}, q = [a], seen = {}; seen[a] = 1;
    while (q.length) { var u = q.shift(); if (u === b) break; (adj[u] || []).forEach(function (v) { if (!seen[v]) { seen[v] = 1; prev[v] = u; q.push(v); } }); }
    if (!seen[b]) return [a, b];
    var p = [b]; while (p[0] !== a) p.unshift(prev[p[0]]); return p;
  }

  // Lean's replay of a certificate -> a studio programme, and which frame each event became.
  // A move the checker found to be a hop walks the device's rails; one it did not is written as
  // the certificate claims it, and the studio says whether the machine can do that.  Within a
  // cycle the order is the checker's: a witness at cycle t sees where the ions are at the START
  // of t (posAt), so it comes first; then the moves of t, then the loop turns of t (`merge`
  // puts a move before a turn of the same cycle).
  function programme(fig, dev) {
    var lines = ['p.init(' + JSON.stringify(Object.fromEntries(fig.init)) + ')'], at = { init: 0 }, events = [];
    fig.gates.forEach(function (g, i) { events.push({ t: g[1], k: 'g' + i, kind: 0, g: g }); });
    fig.moves.forEach(function (m, i) { events.push({ t: m[0], k: 'm' + i, kind: 1, m: m }); });
    (fig.rots || []).forEach(function (r, i) { events.push({ t: r[0], k: 'r' + i, kind: 2, r: r }); });
    events.sort(function (a, b) { return a.t - b.t || a.kind - b.kind; });
    var frameT = [0];
    events.forEach(function (e) {
      if (e.r) {
        lines.push('p.rotate(' + e.r[2] + ', loop=' + JSON.stringify(e.r[1]) + ')');
      } else if (e.m) {
        var path = e.m[4] ? route(dev, e.m[2], e.m[3]) : [e.m[2], e.m[3]];
        lines.push('p.shuttle(' + JSON.stringify(e.m[1]) + ', ' + JSON.stringify(path) + ')');
      } else {
        var g = e.g, name = GATE[g[7]] || g[7].toUpperCase();
        if (g[3].length >= 2) {
          lines.push('p.cool()'); frameT.push(e.t);
          lines.push('p.gate(' + JSON.stringify(name) + ', ' + JSON.stringify([g[3].slice(0, 2)]) + ')');
        } else {
          lines.push('p.gate(' + JSON.stringify(name) + ', [], ' + JSON.stringify([g[2]]) + ')');
        }
      }
      at[e.k] = lines.length - 1; frameT.push(e.t);
    });
    var ionOf = {}; fig.qmap.forEach(function (p) { ionOf[p[0]] = p[1]; });
    var measured = [];
    fig.ops.forEach(function (o) { if (o[1] === 'measure') o[2].forEach(function (q) { if (ionOf[q] && measured.indexOf(ionOf[q]) < 0) measured.push(ionOf[q]); }); });
    if (measured.length) { lines.push('p.measure(' + JSON.stringify(measured) + ')'); frameT.push(fig.T); }
    return { src: lines.join('\n'), at: at, frameT: frameT };
  }

  var uid = 0;
  function Figure(root, opts) {
    this.root = root; this.opts = opts; this.id = ++uid;
    root.innerHTML = '';
    this.head = h('div', 'fighead'); root.appendChild(this.head);
    var body = h('div', 'figbody'); root.appendChild(body);
    var box = h('div', 'studiobox'); body.appendChild(box);
    this.frame = document.createElement('iframe');
    this.frame.className = 'studio';
    this.frame.title = 'the studio, replaying ' + (root.getAttribute('data-name') || 'a certificate');
    this.frame.src = opts.src;
    box.appendChild(this.frame);
    this.side = h('div', 'figside'); body.appendChild(this.side);
    this.circ = el('svg', { class: 'figcirc', role: 'img' }, this.side);
    this.studioSays = h('div', 'studiosays'); this.side.appendChild(this.studioSays);
    this.list = h('div', 'figchecks'); root.appendChild(this.list);
    var self = this;
    this.frame.addEventListener('load', function () { self.loaded = true; self.whenReady(); });
    this.timer = setInterval(function () { self.follow(); }, 250);
  }
  Figure.prototype.ed = function () {
    try { var w = this.frame.contentWindow; return w && w.EDITOR && w.EDITOR.applyProgramSource ? w.EDITOR : null; } catch (e) { return null; }
  };
  Figure.prototype.whenReady = function () {
    var self = this;
    if (!this.ed()) { setTimeout(function () { self.whenReady(); }, 200); return; }
    this.ready = true;
    if (this.fig) this.load();
  };
  Figure.prototype.update = function (fig, dev) {
    this.fig = fig; this.dev = dev;
    if (this.opts.programme) {
      // a programme the lesson built in Lean: no certificate, so no rows and no circuit strip
      this.head.innerHTML = '';
      this.head.appendChild(h('b', '', this.root.getAttribute('data-name')));
      this.head.appendChild(document.createTextNode(' on ' + fig.arch + ': the programme the lesson builds in Lean, played in the studio.'));
      this.circ.style.display = 'none'; this.list.innerHTML = ''; this.rows = [];
    } else {
      this.drawHead(); this.drawRows(); this.drawCircuit(-1);
    }
    if (this.ready) this.load();
  };
  Figure.prototype.load = function () {
    var E = this.ed(); if (!E) return;
    this.studioSays.innerHTML = '';
    if (this.opts.programme) {
      var rp = null;
      try { rp = E.applyProgramSource(this.fig.src); } catch (e) { rp = { ok: false, problems: [{ message: e.message }] }; }
      var pp = (rp && (rp.problems || []).concat(rp.errors || [])) || [];
      this.refused = pp.length > 0;
      if (pp.length) this.say('bad', 'The studio refuses this programme: ' + pp.map(function (x) { return String(x.message || x).replace(/^"|"$/g, ''); }).join('; ') + '.');
      try { if (E.fit) E.fit(); } catch (e) { /* keep the studio's own view */ }
      try { E.seekTo(+(this.root.getAttribute('data-at') || 0)); } catch (e) { /* no such step */ }
    } else if (this.opts.live) {
      var prog = programme(this.fig, this.dev);
      this.prog = prog;
      var r = null;
      try { r = E.applyProgramSource(prog.src); } catch (e) { r = { ok: false, problems: [{ message: e.message }] }; }
      var probs = (r && (r.problems || []).concat(r.errors || [])) || [];
      this.refused = probs.length > 0;
      if (probs.length) {
        this.say('bad', 'The studio refuses this programme: ' + probs.map(function (p) { return String(p.message || p).replace(/^"|"$/g, ''); }).join('; ') + '.');
      }
      var at = +(this.root.getAttribute('data-at') || 0), k = 0;
      prog.frameT.forEach(function (t, i) { if (t <= at) k = i; });
      try { if (E.fit) E.fit(); } catch (e) { /* keep the studio's own view */ }
      try { E.seekTo(k); } catch (e) { /* the studio has no frame there */ }
    }
    var self = this;
    setTimeout(function () { self.studioRules(); }, 600);
  };
  // the studio's OWN rule check on the same moves, as a second opinion beside the proof
  Figure.prototype.studioRules = function () {
    var E = this.ed(); if (!E || !E.ruleCoverage || this.refused) return;   // a refused programme was never replayed
    var cov = [];
    try { cov = E.ruleCoverage(); } catch (e) { return; }
    // a programme the studio parsed but could not replay (an ion not where a move says it is)
    // has no verdicts at all: every rule is withdrawn, which is not "passes"
    var stuck = cov.filter(function (c) { return c.state === 'unchecked' && /^(no rule could run|the programme has not been replayed|there is no programme)/.test(c.why || ''); })[0];
    if (stuck) { this.say('bad', 'The studio could not replay this programme: ' + stuck.why.replace(/^no rule could run: /, '') + '.'); return; }
    var failed = cov.filter(function (c) { return c.state === 'failed'; });
    if (!failed.length) { this.say('ok', 'The studio replays this programme, and every rule it checks passes.'); return; }
    this.say('bad', 'The studio, replaying the same moves, flags ' + failed.map(function (c) {
      return c.rule + (c.statement ? ' (' + c.statement + ')' : ''); }).join('; ') + '.');
  };
  Figure.prototype.say = function (kind, text) { this.studioSays.appendChild(h('p', kind, text)); };
  Figure.prototype.drawHead = function () {
    var f = this.fig;
    this.head.innerHTML = '';
    this.head.appendChild(h('b', '', this.root.getAttribute('data-name')));
    this.head.appendChild(document.createTextNode(this.opts.live
      ? ' on ' + f.arch + ': the certificate\'s moves and gates, played in the studio. '
      : ' on ' + f.arch + ': the compiled programme in the studio, its certificate checked by Lean. '));
    this.head.appendChild(h('span', 'figverdict ' + (f.check ? 'ok' : 'bad'),
      f.check ? 'Lean: check = true, the certificate holds' : 'Lean: check = false, the checker refuses it'));
  };
  Figure.prototype.drawRows = function () {
    var f = this.fig, self = this, rows = [];
    f.moves.forEach(function (m, i) {
      rows.push({ t: m[0], key: 'm' + i, text: 'cycle ' + m[0] + ': ' + m[1] + ' moves ' + m[2] + ' → ' + m[3],
        chips: [[m[4], m[4] ? 'a hop of the device' : 'NOT a hop of the device'], [m[5], m[5] ? 'starts where it is' : m[1] + ' is not in ' + m[2]]] });
    });
    f.gates.forEach(function (g, i) {
      var together = g[4].every(function (s) { return s === g[2]; });
      rows.push({ t: g[1], key: 'g' + i, op: g[0], text: 'cycle ' + g[1] + ': ' + label(g[7]) + ' (op ' + g[0] + ') in ' + g[2] + ' on ' + g[3].join(', '),
        chips: [[together, together ? 'ions together in ' + g[2] : g[3].map(function (ion, j) { return ion + ' is in ' + g[4][j]; }).join(', ')],
                [g[5], g[5] ? 'the trap can gate' : g[2] + ' cannot gate'], [g[6], g[6] ? "the op's own ions" : g[7] === '?' ? 'no such op' : "not the op's ions"]] });
    });
    (f.rots || []).forEach(function (r) {
      rows.push({ t: r[0], text: 'cycle ' + r[0] + ': loop ' + r[1] + ' turns ' + (r[2] > 0 ? '+' : '') + r[2] + ' (every ion on it moves ' + Math.abs(r[2]) + ' slot' + (Math.abs(r[2]) === 1 ? '' : 's') + ')', chips: [] });
    });
    f.ops.forEach(function (o) {
      if (['measure', 'reset', 'barrier'].indexOf(o[1]) < 0 && o[3] !== 1) rows.push({ t: 0, text: 'op ' + o[0] + ' (' + label(o[1]) + ')', chips: [[false, o[3] + ' witnesses, not 1']] });
    });
    rows.sort(function (a, b) { return a.t - b.t; });
    this.list.innerHTML = '';
    this.rows = rows.map(function (r) {
      var d = h('div', 'figrow');
      d.appendChild(h('span', 'figrowt', r.text));
      r.chips.forEach(function (c) { d.appendChild(h('span', 'chip ' + (c[0] ? 'ok' : 'bad'), (c[0] ? '✓ ' : '✗ ') + c[1])); });
      d.title = 'Show this in the studio';
      d.addEventListener('click', function () { self.seekRow(r); });
      self.list.appendChild(d);
      return { r: r, el: d };
    });
  };
  Figure.prototype.seekRow = function (r) {
    var E = this.ed(); if (!E) return;
    try {
      if (this.opts.live && this.prog && r.key && this.prog.at[r.key] != null) E.seekTo(this.prog.at[r.key]);
      else if (!this.opts.live && r.op != null) E.seekTo('realises:' + r.op);
    } catch (e) { /* nothing to step to */ }
  };
  // follow the studio as it plays: the row and the op at its current frame light up
  Figure.prototype.follow = function () {
    if (!document.body.contains(this.root)) { clearInterval(this.timer); return; }
    if (!this.fig || !this.ready || this.root.offsetParent === null) return;
    var doc, k;
    try { doc = this.frame.contentDocument; k = +doc.getElementById('slider').value; } catch (e) { return; }
    if (k === this.lastK) return;
    this.lastK = k;
    var keys = {}, ops = {};
    if (this.opts.programme) return;
    if (this.opts.live && this.prog) {
      for (var key in this.prog.at) if (this.prog.at[key] === k) keys[key] = 1;
      this.fig.gates.forEach(function (g, i) { if (keys['g' + i]) ops[g[0]] = 1; });
    } else {
      // the studio keeps its programme and source map in script-scope constants (P, D), which
      // are not properties of its window: read them in its own scope
      try {
        var realised = this.frame.contentWindow.eval('(function(k){ var id = P.frames[k] && P.frames[k].id;'
          + ' return (D.source && D.source.realises && D.source.realises[id]) || []; })(' + k + ')');
        realised.forEach(function (o) { ops[o] = 1; });
      } catch (e) { /* no source map */ }
    }
    var live = !!this.opts.live;
    // a real boolean: classList.toggle(name, undefined) FLIPS the class instead of clearing it
    this.rows.forEach(function (x) {
      var on = live ? !!(x.r.key && keys[x.r.key]) : !!(x.r.op != null && ops[x.r.op]);
      x.el.classList.toggle('now', on);
    });
    this.drawCircuit(ops);
  };
  Figure.prototype.drawCircuit = function (lit) {
    var svg = this.circ, f = this.fig;
    if (!f) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var nq = f.qmap.length;
    f.ops.forEach(function (o) { o[2].forEach(function (q) { nq = Math.max(nq, q + 1); }); });
    var col = 40, row = 32, x0 = 34, y0 = 22, w = x0 + col * f.ops.length + 16, ht = y0 + row * nq + 4;
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + ht); svg.setAttribute('width', w); svg.setAttribute('height', ht);
    var hd = el('text', { x: 2, y: 12, 'font-size': 11, fill: '#8a8892' }, svg);
    hd.textContent = 'the circuit (lit: the op at the studio\'s step)';
    for (var q = 0; q < nq; q++) {
      var y = y0 + row * q + row / 2;
      el('line', { x1: x0 - 6, y1: y, x2: w - 6, y2: y, stroke: '#b9b5a9' }, svg);
      var tq = el('text', { x: 2, y: y + 4, 'font-size': 11, fill: QCOL[q % QCOL.length], 'font-weight': 700, 'font-family': 'ui-monospace,monospace' }, svg);
      tq.textContent = 'q' + q;
    }
    lit = lit || {};
    f.ops.forEach(function (o, i) {
      var x = x0 + col * i + col / 2, on = !!lit[o[0]];
      var stroke = on ? '#1f5bb5' : '#56545e', fill = on ? '#eaf0fa' : '#fff';
      var ys = o[2].map(function (q) { return y0 + row * q + row / 2; });
      if (o[1] === 'cx' && ys.length === 2) {
        el('line', { x1: x, y1: ys[0], x2: x, y2: ys[1], stroke: stroke, 'stroke-width': 1.6 }, svg);
        el('circle', { cx: x, cy: ys[0], r: 4.5, fill: stroke }, svg);
        el('circle', { cx: x, cy: ys[1], r: 9, fill: fill, stroke: stroke, 'stroke-width': on ? 2.2 : 1.6 }, svg);
        el('line', { x1: x - 9, y1: ys[1], x2: x + 9, y2: ys[1], stroke: stroke, 'stroke-width': 1.6 }, svg);
        el('line', { x1: x, y1: ys[1] - 9, x2: x, y2: ys[1] + 9, stroke: stroke, 'stroke-width': 1.6 }, svg);
      } else {
        if (ys.length > 1) el('line', { x1: x, y1: Math.min.apply(null, ys), x2: x, y2: Math.max.apply(null, ys), stroke: stroke, 'stroke-width': 1.6 }, svg);
        ys.forEach(function (y) {
          el('rect', { x: x - 15, y: y - 11, width: 30, height: 22, rx: 4, fill: fill, stroke: stroke, 'stroke-width': on ? 2.2 : 1.4 }, svg);
          var t = el('text', { x: x, y: y + 4, 'text-anchor': 'middle', 'font-size': 10.5, fill: stroke, 'font-weight': 700 }, svg);
          t.textContent = label(o[1]);
        });
      }
    });
  };

  global.BookFigure = {
    // live: a certificate from the chapter, played on the device's studio page
    live: function (root, arch) { return new Figure(root, { live: true, src: 'ex/dev-' + arch + '.html#embed' }); },
    // a programme the lesson builds in Lean (fields arch, src), played on the device's studio page
    programme: function (root, arch) { return new Figure(root, { programme: true, src: 'ex/dev-' + arch + '.html#embed' }); },
    // a published certificate: its real compiled programme's studio page
    compiled: function (root, id) { return new Figure(root, { live: false, src: 'ex/' + id + '.html#embed&step=1' }); },
  };
})(window);
