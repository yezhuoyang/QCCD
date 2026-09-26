"""Two runs side by side: their numbers, their bottlenecks, and both animations on ONE clock.

`/compare?runs=A,B` (service.py) serves `compare_html(...)`.  The two animations are the
runs' own Studio pages (`/runview/<id>`) in frames; one slider and one Play button drive
both, by MODELLED time: at every tick each frame is sought to the step that has started by
then (`perf.frame_at` over the run's step start times).  So the faster design visibly
finishes first, rather than two animations merely playing next to each other.

`RUNVIEW_BLOCK` is appended to a run's page: it switches the page to view-only (the
transport answers, nothing edits) and, inside a frame, trims the chrome to the canvas.
"""

from __future__ import annotations

import html
import json

__all__ = ["compare_html", "RUNVIEW_BLOCK"]

RUNVIEW_BLOCK = """<style>
body.qrun-framed #tools, body.qrun-framed #rail, body.qrun-framed #progcol, body.qrun-framed #etools,
body.qrun-framed .toolbar, body.qrun-framed header .actions { display: none !important; }
</style>
<script>
(function(){
  try { if (window.EDITOR && EDITOR.setViewOnly) EDITOR.setViewOnly(true); } catch (e) {}
  if (window.self !== window.top) document.body.classList.add('qrun-framed');
  window.QRUN = {
    frames: function(){ try { return lastFrame() + 1; } catch (e) { return 0; } },
    seek: function(i){ try { seek(i); return true; } catch (e) { return false; } }
  };
  // opened by an agent's run (#play): it plays by itself, as a person showing it would.  The
  // agent used to press Play and wait on the page -- two page actions and three model calls,
  // about 17 s, measured in its trace (2026-09-26)
  if (/(^#|&)play(&|$)/.test(location.hash) && window.self === window.top) {
    var t0 = Date.now();
    (function start() {
      var b = document.getElementById('play');
      if (b && typeof lastFrame === 'function' && lastFrame() > 0) { if (!/pause/i.test(b.textContent)) b.click(); return; }
      if (Date.now() - t0 < 15000) setTimeout(start, 200);
    })();
  }
})();
</script>
"""

_CSS = """
:root{--ink:#1d1d1b;--mut:#6b6a66;--line:#e6e5e1;--a:#2a78d6;--b:#c2410c;--bg:#fbfbfa}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:14px 20px 4px} .kicker{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}
h1{font-size:19px;margin:2px 0 4px} .verdict{margin:0 0 6px;font-size:14.5px}
.note{margin:0 0 4px;color:var(--mut);font-size:12px}
.warn{color:#9a3412} .top{display:grid;grid-template-columns:minmax(320px,1.1fr) 1fr;gap:18px;padding:6px 20px}
table{border-collapse:collapse;width:100%;font-size:13px;background:#fff;border:1px solid var(--line);border-radius:8px}
th,td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left} th{color:var(--mut);font-weight:600;font-size:12px}
td.pos{color:#9a3412} td.neg{color:#0b7a4b} .bn{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.bn div{background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:13px}
.bn h3{margin:0 0 4px;font-size:13px} .bn ul{margin:0;padding-left:17px} .bn li{margin:3px 0}
.player{display:flex;align-items:center;gap:10px;padding:10px 20px;position:sticky;top:0;background:var(--bg);z-index:5;
 border-bottom:1px solid var(--line)}
.player button{font:inherit;padding:5px 14px;border-radius:6px;border:1px solid var(--a);background:var(--a);color:#fff;cursor:pointer}
.player input[type=range]{flex:1} .clock{font-variant-numeric:tabular-nums;min-width:120px;text-align:right}
.player select{font:inherit;padding:3px}
.views{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px 20px 20px}
.pane{background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
.cap{display:flex;justify-content:space-between;gap:8px;padding:7px 11px;border-bottom:1px solid var(--line);font-size:13px}
.cap b.A{color:var(--a)} .cap b.B{color:var(--b)} .state{color:var(--mut);font-variant-numeric:tabular-nums}
.state.done{color:#0b7a4b;font-weight:600} .bar{height:4px;background:var(--line)} .bar i{display:block;height:100%;width:0}
.pane.A .bar i{background:var(--a)} .pane.B .bar i{background:var(--b)}
iframe{border:0;width:100%;height:calc(100vh - 150px);min-height:460px;background:#fff}
@media (max-width:900px){.top,.views,.bn{grid-template-columns:1fr}}
"""

_JS = r"""
(function(){
  var D = JSON.parse(document.getElementById('qcmp').textContent);
  var MAX = Math.max.apply(null, D.runs.map(function(r){ return r.total_ms; })) || 1;
  var t = 0, playing = false, last = null;
  var slider = document.getElementById('t'), clock = document.getElementById('clock');
  var btn = document.getElementById('play'), speed = document.getElementById('speed');
  slider.max = String(MAX); slider.step = String(MAX / 2000);
  var panes = D.runs.map(function(r, k){
    var el = document.getElementById('pane' + k);
    return { run: r, frame: el.querySelector('iframe'), state: el.querySelector('.state'),
             bar: el.querySelector('.bar i'), at: -1 };
  });
  function frameAt(times, us){                      // the last step started by `us`
    var lo = 0, hi = times.length - 1, ans = 0;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (times[mid] <= us) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  function show(){
    clock.textContent = t.toFixed(t < 10 ? 2 : 1) + ' ms';
    slider.value = String(t);
    panes.forEach(function(p){
      var done = t >= p.run.total_ms;
      p.state.textContent = done ? ('finished at ' + p.run.total_ms + ' ms') : ('running, ' + t.toFixed(1) + ' ms');
      p.state.className = 'state' + (done ? ' done' : '');
      p.bar.style.width = Math.min(100, 100 * t / p.run.total_ms) + '%';
      var w = p.frame.contentWindow, i = frameAt(p.run.times, t * 1000);
      if (w && w.QRUN && i !== p.at) { if (w.QRUN.seek(i)) p.at = i; }
    });
  }
  function tick(now){
    if (!playing) return;
    if (last !== null) {
      var rate = MAX / 30 * parseFloat(speed.value);          // x1 plays the longer run in 30 s
      t = Math.min(MAX, t + (now - last) / 1000 * rate);
    }
    last = now; show();
    if (t >= MAX) { playing = false; btn.textContent = 'Replay'; return; }
    requestAnimationFrame(tick);
  }
  btn.addEventListener('click', function(){
    if (playing) { playing = false; btn.textContent = 'Play'; return; }
    if (t >= MAX) t = 0;
    playing = true; last = null; btn.textContent = 'Pause'; requestAnimationFrame(tick);
  });
  slider.addEventListener('input', function(){ t = parseFloat(slider.value) || 0; show(); });
  panes.forEach(function(p){ p.frame.addEventListener('load', function(){ p.at = -1; show(); }); });
  window.QCMP = { seekMs: function(ms){ t = Math.max(0, Math.min(MAX, ms)); show(); return panes.map(function(p){ return p.at; }); },
                  play: function(){ if (!playing) btn.click(); }, state: function(){ return { t: t, playing: playing,
                  at: panes.map(function(p){ return p.at; }) }; } };
  show();
})();
"""


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _tag(letter: str, label: str) -> str:
    """'A' and 'main r1' -> 'A main r1'; 'A' and 'A r0' -> 'A r0' (the draft is already called A)."""
    return label if label.split(" ")[0] == letter else f"{letter} {label}"


def compare_html(cmp: dict, times: list) -> str:
    """The page for two runs (`WorkspaceCore.compare_runs` output + each run's step times)."""
    runs = cmp["runs"]
    labels = [r["label"] for r in runs]
    tags = [_tag("AB"[k], labels[k]) for k in (0, 1)]
    prog = runs[0]["program"] if runs[0]["program"] == runs[1]["program"] else f"{runs[0]['program']} / {runs[1]['program']}"
    rows = []
    for r in cmp["rows"]:
        d = r["difference"]
        cls = "" if not d else ("pos" if d > 0 else "neg")
        dtxt = "-" if d is None else (("+" if d > 0 else "") + _fmt(d))
        unit = f" {r['unit']}" if r["unit"] else ""
        rows.append(f"<tr><td>{html.escape(r['metric'])}{html.escape(unit)}</td><td>{_fmt(r[labels[0]])}</td>"
                    f"<td>{_fmt(r[labels[1]])}</td><td class=\"{cls}\">{dtxt}</td></tr>")
    bns = "".join(f"<div><h3>{html.escape(tags[k])}</h3><ul>"
                  + "".join(f"<li>{html.escape(s)}</li>" for s in cmp["bottlenecks"][labels[k]]) + "</ul></div>"
                  for k in (0, 1))
    panes = "".join(
        f"<div class=\"pane {'AB'[k]}\" id=\"pane{k}\"><div class=\"cap\"><span><b class=\"{'AB'[k]}\">"
        f"{html.escape(tags[k])}</b> &middot; {html.escape(r['design'])} &middot; {_fmt(r['total_ms'])} ms</span>"
        f"<span class=\"state\"></span></div><div class=\"bar\"><i></i></div>"
        f"<iframe title=\"{html.escape(labels[k])}\" src=\"/runview/{html.escape(r['run_id'])}\"></iframe></div>"
        for k, r in enumerate(runs))
    data = {"runs": [{"run_id": r["run_id"], "label": r["label"], "total_ms": r["total_ms"], "times": times[k]}
                     for k, r in enumerate(runs)]}
    warn = f"<p class=\"warn\">{html.escape(cmp['warning'])}</p>" if cmp.get("warning") else ""
    warn += ("<p class=\"note\">Times are the evaluator's replay, the same one grading uses. Each pane's own "
             "footer shows the page's in-browser estimate, which can differ.</p>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>Compare: {html.escape(prog)}</title><style>{_CSS}</style></head><body>"
            f"<header><div class=\"kicker\">QCCD &middot; side by side</div>"
            f"<h1>{html.escape(prog)}: {html.escape(tags[0])} vs {html.escape(tags[1])}</h1>"
            f"<p class=\"verdict\">{html.escape(cmp['verdict'])}</p>{warn}</header>"
            f"<div class=\"top\"><table><tr><th>metric</th><th>A</th><th>B</th><th>B &minus; A</th></tr>{''.join(rows)}"
            f"</table><div class=\"bn\">{bns}</div></div>"
            "<div class=\"player\"><button id=\"play\" type=\"button\">Play</button>"
            "<input id=\"t\" type=\"range\" min=\"0\" value=\"0\" aria-label=\"modelled time\">"
            "<span class=\"clock\" id=\"clock\"></span><select id=\"speed\" aria-label=\"speed\">"
            "<option value=\"0.25\">x0.25</option><option value=\"0.5\">x0.5</option><option value=\"1\" selected>x1</option>"
            "<option value=\"2\">x2</option><option value=\"4\">x4</option></select></div>"
            f"<div class=\"views\">{panes}</div>"
            f"<script id=\"qcmp\" type=\"application/json\">{json.dumps(data).replace('</', '<' + chr(92) + '/')}</script>"
            f"<script>{_JS}</script></body></html>")
