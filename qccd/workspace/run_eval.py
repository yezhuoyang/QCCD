"""A run's evaluation, on its page, as the animation plays.

A run's page (`/runview/<id>`) used to show the animation and a cost counter; where the time went
lived in the backend's report, which only the agent read.  Now the page carries the evaluator's
own timeline (`perf.timeline`: per frame, the time it adds by category, the modelled clock, the
ions it moves, the two-qubit gates it runs) and draws the evaluation from it in the left rail:

    the modelled clock        how far into the round the animation is, of the round's total
    time by category          transport, cooling, gates, measurement, reset -- growing as it plays
    counts                    steps, two-qubit gates, ions moved so far
    the final result          at the last frame: the round time, the bottlenecks, the rules, heating

"Skip to result" jumps to the last frame (a run opened with `#result` starts there); `#play` plays
it.  The page reports what its panel shows to the service (`POST /api/runs/<id>/evaluation`), and
`qccd_run_program` hands the agent THAT: the numbers the agent reports are the ones on the person's
screen.  The running totals are the per-cycle durations the replay sums, so the final panel equals
the evaluator's report; the page checks this and says so if it ever does not.
"""

from __future__ import annotations

import json

__all__ = ["eval_block"]

_CSS = """<style>
#qrunEval{border-radius:10px;background:var(--panel,#fff);border:1px solid var(--line,#e4e7ec);padding:10px 11px 11px;
  margin:0 0 10px;font:12.5px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink,#1d2939)}
#qrunEval h4{display:flex;align-items:baseline;gap:6px;margin:0 0 6px}
#qrunEval h4 span{font-weight:500;color:var(--muted,#667085);font-size:11.5px;text-transform:none;letter-spacing:0}
#qrunEval .qe-clock{font-variant-numeric:tabular-nums;margin:0 0 4px}
#qrunEval .qe-clock b{font-size:21px;letter-spacing:-.01em}
#qrunEval .qe-clock span{color:var(--muted,#667085)}
#qrunEval .qe-prog{height:5px;border-radius:3px;background:#eef0f4;overflow:hidden;margin:0 0 9px}
#qrunEval .qe-prog i{display:block;height:100%;width:0;background:#1d2a5b}
#qrunEval .qe-row{display:flex;justify-content:space-between;gap:6px;font-variant-numeric:tabular-nums;margin-top:4px}
#qrunEval .qe-row span{color:var(--muted,#475467)}
#qrunEval .qe-cb{height:4px;border-radius:2px;background:#eef0f4;overflow:hidden;margin-top:2px}
#qrunEval .qe-cb i{display:block;height:100%;width:0}
#qrunEval .qe-kv{margin-top:9px;padding-top:7px;border-top:1px solid var(--line,#eaecf0);display:grid;
  grid-template-columns:1fr auto;gap:2px 8px;font-variant-numeric:tabular-nums}
#qrunEval .qe-kv span{color:var(--muted,#475467)}
#qrunEval .qe-final{margin-top:9px;padding:8px 9px;border-radius:8px;background:#f1f8f4;border:1px solid #cfe8d9}
#qrunEval .qe-final.bad{background:#fdf1f0;border-color:#f3c6c1}
#qrunEval .qe-final b{display:block;margin-bottom:3px}
#qrunEval .qe-final ul{margin:4px 0 0;padding-left:16px}
#qrunEval .qe-final li{margin:2px 0}
#qrunEval .qe-note{color:var(--muted,#667085);font-size:11px;margin-top:6px}
#qrunEval .qe-btns{display:flex;gap:6px;margin-top:9px}
#qrunEval .qe-btns button{flex:1;font:600 12px system-ui,sans-serif;padding:6px 8px;border-radius:7px;cursor:pointer;
  border:1px solid #1d2a5b;background:#1d2a5b;color:#fff}
#qrunEval .qe-btns button.alt{background:#fff;color:#1d2a5b}
#qrunEval .qe-btns button[disabled]{opacity:.45;cursor:default}
body.qrun-live #palStart,body.qrun-live #palElements,body.qrun-live #palInspect{display:none}
body.qrun-framed #qrunEval{display:none}
</style>
"""

_JS = r"""<script>
(function(){
  'use strict';
  var X = JSON.parse(document.getElementById('qrun-eval-data').textContent);
  var T = X.timeline, N = T.cat.length, C = T.categories, K = C.length;
  var COLORS = {transport:'#2a78d6', cooling:'#0e9384', gates:'#7a5af8', measurement:'#dc6803', reset:'#98a2b3'};
  // prefix sums: what frames [0, i) add, so any moment of the animation is a lookup
  var cum = [], clock = new Float64Array(N + 1), ions = new Float64Array(N + 1), g2 = new Float64Array(N + 1),
      steps = new Float64Array(N + 1), k, i;
  for (k = 0; k < K; k++) cum.push(new Float64Array(N + 1));
  for (i = 0; i < N; i++) {
    for (k = 0; k < K; k++) cum[k][i + 1] = cum[k][i] + (T.cat[i] === k ? T.us[i] : 0);
    clock[i + 1] = T.t_us[i]; ions[i + 1] = ions[i] + T.ions[i]; g2[i + 1] = g2[i] + T.gates2q[i];
    steps[i + 1] = steps[i] + (T.cat[i] >= 0 ? 1 : 0);
  }
  var TOTAL = clock[N] / 1000, R = X.report || {};
  var MATCH = Math.abs(TOTAL - ((R.total || {}).ms || TOTAL)) < 0.01;
  var ms = function (us) { return us / 1000; };
  var fmt = function (v) { return v >= 10 ? v.toFixed(2) : v.toFixed(3); };
  var r3 = function (v) { return Math.round(v * 1000) / 1000; };
  // the page's own playback state (render.py): the frame and how far into it
  var cur = function () {
    var f = (typeof frame === 'number') ? frame : 0, p = (typeof phase === 'number') ? phase : 1;
    return { f: Math.max(0, Math.min(N - 1, f)), p: Math.max(0, Math.min(1, p)) };
  };
  var last = function () { try { return lastFrame(); } catch (e) { return N - 1; } };
  var playing = function () { var b = document.getElementById('play'); return !!b && /pause/i.test(b.textContent); };
  // what the round has spent at frame f, phase p (frames before f done, f part-way)
  function at(f, p) {
    var o = { t: clock[f] + p * Math.max(0, clock[f + 1] - clock[f]), cats: [],
              steps: steps[f] + (p >= 1 ? steps[f + 1] - steps[f] : 0),
              g2: g2[f] + (p >= 1 ? g2[f + 1] - g2[f] : 0), ions: ions[f] + (p >= 1 ? ions[f + 1] - ions[f] : 0) };
    for (var k = 0; k < K; k++) o.cats.push(cum[k][f] + p * (cum[k][f + 1] - cum[k][f]));
    return o;
  }
  var h = function (tag, attrs, kids) {
    var e = document.createElement(tag);
    for (var a in attrs || {}) { if (a === 'text') e.textContent = attrs[a]; else e.setAttribute(a, attrs[a]); }
    (kids || []).forEach(function (c) { e.appendChild(c); });
    return e;
  };
  // ---- the panel, first in the rail (a run is view-only: the element palettes have nothing to do)
  var box = h('section', { id: 'qrunEval', 'class': 'pal', 'data-hint': 'run:eval', 'aria-live': 'off' });
  box.innerHTML = '<h4>Evaluation <span id="qe-state"></span></h4>' +
    '<div class="qe-clock"><b id="qe-t">0</b> <span>of <span id="qe-total"></span> ms per round</span></div>' +
    '<div class="qe-prog"><i id="qe-bar"></i></div><div id="qe-cats"></div><div class="qe-kv" id="qe-kv"></div>' +
    '<div id="qe-final" class="qe-final" hidden></div>' +
    '<div class="qe-btns"><button id="qe-skip" data-hint="run:skip" type="button">Skip to result</button>' +
    '<button id="qe-replay" data-hint="run:replay" class="alt" type="button" hidden>Play from start</button></div>' +
    '<div class="qe-note" id="qe-note"></div>';
  var rail = document.getElementById('rail');
  if (rail) { rail.insertBefore(box, rail.firstChild); document.body.classList.add('qrun-live'); }
  else document.body.appendChild(box);
  var $ = function (id) { return document.getElementById(id); };
  $('qe-total').textContent = fmt(TOTAL);
  var order = C.map(function (n, k) { return k; }).sort(function (a, b) { return cum[b][N] - cum[a][N]; });
  var rows = order.map(function (k) { var name = C[k];
    var r = h('div', {}), bar = h('i', {}); bar.style.background = COLORS[name] || '#98a2b3';
    r.appendChild(h('div', { 'class': 'qe-row' }, [h('span', { text: name }), h('b', { text: '0 ms' })]));
    r.appendChild(h('div', { 'class': 'qe-cb' }, [bar]));
    $('qe-cats').appendChild(r);
    return { k: k, val: r.querySelector('b'), bar: bar };
  });
  var totals = at(N - 1, 1);
  var kv = [['steps', function (o) { return o.steps + ' / ' + totals.steps; }],
            ['two-qubit gates', function (o) { return o.g2 + ' / ' + totals.g2; }],
            ['ions moved', function (o) { return String(o.ions); }]];
  var kvv = kv.map(function (x) { var v = h('b', { text: '' }); $('qe-kv').appendChild(h('span', { text: x[0] })); $('qe-kv').appendChild(v); return v; });
  $('qe-note').textContent = MATCH ? "The evaluator's replay, step by step: it ends at the round time the run reports."
                                   : "These totals differ from the run's report (" + ((R.total || {}).ms) + ' ms): the report is the reference.';
  function renderFinal() {
    var f = $('qe-final'), bad = (R.rules && R.rules.failed || []).length > 0;
    f.className = 'qe-final' + (bad ? ' bad' : '');
    f.innerHTML = '';
    f.appendChild(h('b', { text: 'Result: ' + fmt(TOTAL) + ' ms per round' + (bad ? ' (rules broken)' : '') }));
    var ul = h('ul', {});
    (R.bottleneck || []).slice(0, 4).forEach(function (s) { ul.appendChild(h('li', { text: s })); });
    if (R.heating) ul.appendChild(h('li', { text: 'Peak heating ' + R.heating.peak_quanta + ' quanta (' + (R.heating.peak_ion || '?') + ').' }));
    ul.appendChild(h('li', { text: bad ? 'Rules failed: ' + R.rules.failed.join(', ') + '.' : 'Every rule the replay checks passed.' }));
    f.appendChild(ul);
    f.hidden = false;
  }
  // ---- what the panel shows now; and, once, what it would show at the end
  var lastKey = '', finalShown = false;
  function state() {
    var c = cur(), done = c.f >= last() && c.p >= 1;
    return { c: c, done: done, word: done ? 'final result' : playing() ? 'playing' : (c.f === 0 && c.p >= 1 ? 'ready' : 'paused') };
  }
  function paint() {
    var s = state(), key = s.c.f + ':' + s.c.p.toFixed(3) + ':' + s.word;
    if (key === lastKey) return;
    lastKey = key;
    var o = s.done ? totals : at(s.c.f, s.c.p);
    $('qe-t').textContent = fmt(ms(o.t));
    $('qe-bar').style.width = (100 * Math.min(1, ms(o.t) / (TOTAL || 1))).toFixed(1) + '%';
    $('qe-state').textContent = s.word + (s.done ? '' : ' · ' + Math.round(100 * ms(o.t) / (TOTAL || 1)) + '%');
    rows.forEach(function (r) {
      r.val.textContent = fmt(ms(o.cats[r.k])) + ' ms';
      r.bar.style.width = (100 * o.cats[r.k] / (clock[N] || 1)).toFixed(1) + '%';
    });
    kv.forEach(function (x, j) { kvv[j].textContent = x[1](o); });
    $('qe-skip').disabled = s.done;
    $('qe-replay').hidden = !s.done;
    if (s.done && !finalShown) { finalShown = true; renderFinal(); report('final'); }
    if (!s.done && finalShown) { finalShown = false; $('qe-final').hidden = true; }
  }
  // ---- the page tells the service what it shows: the numbers the agent then reports
  var told = {};
  function report(shown) {
    if (told[shown]) return;
    var L = window.QCCD_LIVE;
    if (!L || !L.api || !L.state().paired) { setTimeout(function () { report(shown); }, 400); return; }
    told[shown] = true;
    var o = totals;
    L.api('POST', '/api/runs/' + encodeURIComponent(X.run_id) + '/evaluation', {
      shown: shown, total_ms: r3(TOTAL),
      breakdown: C.map(function (name, k) { return { category: name, ms: r3(ms(o.cats[k])),
                                                     share: +(o.cats[k] / (clock[N] || 1)).toFixed(4) }; })
                  .sort(function (a, b) { return b.ms - a.ms; }),
      counts: { steps: o.steps, two_qubit_gates: o.g2, ions_moved: o.ions },
      heating: R.heating || null, rules_failed: (R.rules && R.rules.failed) || [],
      bottleneck: R.bottleneck || [], matches_evaluator: MATCH, frames: N });
  }
  $('qe-skip').onclick = function () { try { seek(last()); } catch (e) { /* no program */ } paint(); };
  $('qe-replay').onclick = function () { try { seek(0); document.getElementById('play').onclick(); } catch (e) { /* none */ } };
  setInterval(paint, 120);
  paint();
  setTimeout(function () { if (window.QCCD_PAGE && window.QCCD_PAGE.keepInView) window.QCCD_PAGE.keepInView(); }, 900);
  report('loaded');
  // `#result`: open at the result, without the animation (a person or an agent in a hurry)
  if (/(^#|&)result(&|$)/.test(location.hash)) {
    var t0 = Date.now();
    (function jump() {
      if (typeof lastFrame === 'function' && lastFrame() > 0) { seek(last()); paint(); return; }
      if (Date.now() - t0 < 15000) setTimeout(jump, 150);
    })();
  }
})();
</script>
"""


def eval_block(run_id: str, timeline: dict, report: dict) -> str:
    """The panel for a run's page: its data, style and script (appended before </body>)."""
    keep = {k: report.get(k) for k in ("total", "breakdown", "counts", "heating", "rules", "bottleneck")}
    data = json.dumps({"run_id": run_id, "timeline": timeline, "report": keep}, separators=(",", ":"))
    return (_CSS + '<script id="qrun-eval-data" type="application/json">' + data.replace("</", "<\\/") + "</script>\n"
            + _JS)
