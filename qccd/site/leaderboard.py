"""The Leaderboard as a place to take part: "Try your own design" on every board, each board's
community submissions live with who submitted them, and the page where a person lets their own
workspace submit in their name.

    board_block(task, title)   one board's "Try your own design" button and its live community list,
                               placed in the board's own section of /board/ (seed boards and memory
                               boards alike)
    SCRIPT / CSS               fill every such block from the official service (/official/v1/),
                               newest data each visit; the popover the button opens
    connect_page(PAGE, STYLE)  /connect/: a signed-in person allows ONE workspace's sign-in code
                               (qccd/official/accounts.py), and sees and revokes the workspaces that
                               can submit as them, and what they submitted

Why a button needs a popover on the public site: the design, the agent and the grading run on the
person's own computer (a QCCD workspace), which this page cannot reach.  So "Try your own design"
offers the link into that workspace (127.0.0.1:47100, the port its website mirror always takes,
which forwards to the workspace's Studio with the board in focus) and says how to set one up.  On
the workspace's own copy of the site (`window.QCCD_MIRROR`) it goes straight there.

Every control here is a link, a fold-out, or a button declared in QCCD_HINTS; the /connect/ page's
Allow and Revoke are the PERSON's (data-qccd-private): an agent reading the page through a
workspace cannot press them, and in the workspace's copy the page is only a pointer to the site.
"""

from __future__ import annotations

import html
import json

__all__ = ["board_block", "SCRIPT", "CSS", "HINTS", "connect_page", "board_ids", "script"]

LOCAL_STUDIO = "http://127.0.0.1:47100/studio"

CSS = """
.lbx{margin:14px 0 4px;padding:12px 14px;border:1px solid #d7e4f6;border-radius:12px;background:#f6f9fe}
.lbx-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.lbx-try{font:600 14px/1 var(--sans,system-ui,sans-serif);padding:9px 16px;border-radius:999px;border:1px solid #1f5bb5;
 background:#1f5bb5;color:#fff;cursor:pointer}
.lbx-try:hover{background:#174a97}
.lbx-top .lbx-t{font:600 12px/1.3 var(--sans,system-ui,sans-serif);letter-spacing:.06em;text-transform:uppercase;color:#56545e}
.lbx-live table{width:100%;border-collapse:collapse;font:13.5px/1.4 var(--sans,system-ui,sans-serif);margin:8px 0 2px}
.lbx-live th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#56545e;padding:4px 8px;
 border-bottom:1px solid #e0e6ef}
.lbx-live td{padding:5px 8px;border-bottom:1px solid #edf1f6}
.lbx-live td.n{text-align:right;font-variant-numeric:tabular-nums}
.lbx-live .lbx-by{font-weight:600;color:#1a2540}
.lbx-live .lbx-empty,.lbx-live .lbx-wait{color:#6b6a66;font:13.5px/1.5 var(--sans,system-ui,sans-serif);margin:6px 0 0}
.lbx-seedby{font:12.5px/1.4 var(--sans,system-ui,sans-serif);color:#6b6a66;margin:4px 0 0}
#lbx-pop{position:absolute;z-index:1200;width:min(420px,calc(100vw - 24px));background:#fff;border:1px solid #cfceca;
 border-radius:12px;box-shadow:0 14px 36px rgba(0,0,0,.18);padding:14px 16px;font:14px/1.5 var(--sans,system-ui,sans-serif);
 color:#1d1d1b}
#lbx-pop h4{margin:0 26px 6px 0;font-size:16px}
#lbx-pop p{margin:6px 0}
#lbx-pop .lbx-go{display:inline-block;margin:6px 8px 2px 0;padding:8px 14px;border-radius:8px;background:#1f5bb5;color:#fff;
 text-decoration:none;font-weight:600}
#lbx-pop .lbx-go.alt{background:#fff;color:#1f5bb5;border:1px solid #bcd4f2}
#lbx-pop .lbx-small{font-size:12.5px;color:#56545e}
#lbx-pop .lbx-x{position:absolute;top:6px;right:8px;border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:#6b6a66}
"""

HINTS = {
    "board:try": ("Try your own design", "design a device for this board in your own QCCD workspace (with your AI agent "
                  "if you like), test it there with the reference checks, and submit it under your name"),
    "board:try-close": ("Close", "close the Try your own design box"),
}


def board_ids() -> dict:
    """task (a board's section id on /board/) -> the official release id and title the server ranks."""
    try:
        from ..workspace.tasks import list_boards
        return {b.manifest["task"]: {"id": b.id, "title": b.title} for b in list_boards()}
    except Exception:                       # the workspace package is optional for a site build
        return {}


def board_block(task: str, title: str, seed_note: bool = True) -> str:
    """One board's "Try your own design" and its live community list (filled by SCRIPT)."""
    t = html.escape(task)
    seed = ('<p class="lbx-seedby">The bars above are the QCCD study&rsquo;s own designs. Submissions from people '
            'are listed here, with who submitted each, straight from the official server.</p>' if seed_note else "")
    return (f'<div class="lbx" data-board="{t}"><div class="lbx-top">'
            f'<button class="lbx-try" type="button" data-board="{t}" data-title="{html.escape(title)}" '
            f'data-hint="board:try">Try your own design</button>'
            f'<span class="lbx-t">Community submissions</span></div>'
            f'<div class="lbx-live" data-board="{t}"><p class="lbx-wait">Loading the official ranking&hellip;</p></div>'
            f'{seed}</div>')


SCRIPT = r"""
(function(){
  var API = '/official/v1', BOARDS = __BOARDS__, LOCAL = __LOCAL__;
  function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function fmt(v){ return (typeof v === 'number') ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toPrecision(4)) : '-'; }
  function day(t){ return t ? new Date(t * 1000).toISOString().slice(0, 10) : '-'; }
  function get(path){ return fetch(API + path, { headers: { 'Accept': 'application/json' } }).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }); }
  // "Try your own design": into the person's own workspace, focused on this board
  function studioURL(task){ return (window.QCCD_MIRROR ? '/studio' : LOCAL) + '?board=' + encodeURIComponent(task); }
  var pop = null;
  function closePop(){ if (pop) { pop.remove(); pop = null; } }
  function openPop(btn){
    var task = btn.getAttribute('data-board'), title = btn.getAttribute('data-title') || task;
    if (window.QCCD_MIRROR) { location.assign(studioURL(task)); return; }
    closePop();
    pop = el('div'); pop.id = 'lbx-pop'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Try your own design');
    var x = el('button', 'lbx-x', '×'); x.type = 'button'; x.setAttribute('aria-label', 'close'); x.setAttribute('data-hint', 'board:try-close');
    x.onclick = closePop; pop.appendChild(x);
    pop.appendChild(el('h4', null, 'Try your own design: ' + title));
    pop.appendChild(el('p', null, 'Design the device in your own QCCD workspace, where your AI agent can help. It opens ' +
      'the Studio on this board: its ranking, a test with every check the server runs, and Submit, which lists your ' +
      'design here under your name once you sign in with your qccd.academy account.'));
    var go = el('a', 'lbx-go', 'Open in my workspace'); go.href = studioURL(task); go.target = '_blank'; go.rel = 'noopener';
    var setup = el('a', 'lbx-go alt', 'Set up a workspace'); setup.href = '../studio.html?agent#design';   // /board/ is one level down
    var p = el('p'); p.appendChild(go); p.appendChild(setup); pop.appendChild(p);
    pop.appendChild(el('p', 'lbx-small', '"Open in my workspace" works while your workspace runs on this computer ' +
      '(qccd studio). Set up once: the Design page’s Agent panel lists the steps.'));
    document.body.appendChild(pop);
    var r = btn.getBoundingClientRect();
    pop.style.left = Math.max(12, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 12)) + 'px';
    pop.style.top = (window.scrollY + r.bottom + 8) + 'px';
  }
  document.addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('.lbx-try');
    if (b) { e.preventDefault(); openPop(b); return; }
    if (pop && !pop.contains(e.target)) closePop();
  });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') closePop(); });
  // each board's community list, live
  var boxes = document.querySelectorAll('.lbx-live[data-board]');
  if (!boxes.length || typeof fetch !== 'function') return;
  get('/tasks').then(function(t){
    var have = {}; ((t && t.tasks) || []).forEach(function(x){ have[x.id] = x; });
    [].forEach.call(boxes, function(box){
      var b = BOARDS[box.getAttribute('data-board')];
      if (!b || !have[b.id]) { box.textContent = ''; box.appendChild(el('p', 'lbx-empty', 'The official server does not rank this board yet.')); return; }
      var task = have[b.id], unit = ((task.metrics || []).filter(function(m){ return m.name === task.rank_by; })[0] || {}).unit || '';
      get('/leaderboard/' + encodeURIComponent(b.id)).then(function(lb){
        box.textContent = '';
        var rows = lb.rows || [];
        if (!rows.length) { box.appendChild(el('p', 'lbx-empty', 'No submissions yet: the first design that passes is #1.')); return; }
        var tb = el('table'), hd = el('tr');
        ['#', 'design', 'by', lb.rank_by + (unit ? ' (' + unit + ')' : ''), 'submitted', ''].forEach(function(c){ hd.appendChild(el('th', null, c)); });
        var th = el('thead'); th.appendChild(hd); tb.appendChild(th);
        var body = el('tbody');
        rows.forEach(function(r, i){
          var tr = el('tr');
          tr.appendChild(el('td', 'n', String(i + 1)));
          tr.appendChild(el('td', null, r.display_name || '-'));
          tr.appendChild(el('td', 'lbx-by', r.by || '-'));
          tr.appendChild(el('td', 'n', fmt(r.rank_value)));
          tr.appendChild(el('td', null, day(r.created_at)));
          var td = el('td'), a = el('a', null, 'report'); a.href = API + '/submissions/' + encodeURIComponent(r.id) + '/report';
          td.appendChild(a); tr.appendChild(td);
          body.appendChild(tr);
        });
        tb.appendChild(body); box.appendChild(tb);
      }, function(err){ box.textContent = ''; box.appendChild(el('p', 'lbx-empty', 'The official server did not answer (' + err.message + ').')); });
    });
  }, function(err){
    [].forEach.call(boxes, function(box){ box.textContent = ''; box.appendChild(el('p', 'lbx-empty', 'The official server did not answer (' + err.message + ').')); });
  });
})();
"""


def script() -> str:
    hints = {k: {"t": t, "d": d} for k, (t, d) in HINTS.items()}
    js = SCRIPT.replace("__BOARDS__", json.dumps(board_ids())).replace("__LOCAL__", json.dumps(LOCAL_STUDIO))
    return ("<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, " + json.dumps(hints) + ");</script>"
            f"<script>{js}</script>")


# ---------------------------------------------------------------------- /connect/

CONNECT_CSS = """
.cx-card{border:1px solid #d7e4f6;background:#f6f9fe;border-radius:12px;padding:16px 18px;margin:16px 0;font-family:var(--sans,system-ui,sans-serif)}
.cx-card h2{margin:0 0 8px}
.cx-code{font:600 22px/1.2 ui-monospace,Consolas,monospace;letter-spacing:.12em;color:#1a2540}
.cx-btn{font:600 14px/1 var(--sans,system-ui,sans-serif);padding:10px 18px;border-radius:8px;border:1px solid #1f5bb5;background:#1f5bb5;
 color:#fff;cursor:pointer;margin:8px 10px 0 0}
.cx-btn.alt{background:#fff;color:#1f5bb5}
.cx-btn[disabled]{opacity:.55;cursor:default}
.cx-ok{color:#0b5d36;font-weight:600}
.cx-bad{color:#9a3412}
.cx-t{width:100%;border-collapse:collapse;font:13.5px/1.4 var(--sans,system-ui,sans-serif);margin:8px 0}
.cx-t th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#56545e;padding:4px 8px;border-bottom:1px solid #e0e6ef}
.cx-t td{padding:6px 8px;border-bottom:1px solid #edf1f6}
.cx-small{font-size:13px;color:#56545e}
"""

CONNECT_BODY = r"""<h1>Connect a workspace</h1>
<p class="sub">A QCCD workspace runs on your own computer: your designs, your AI agent, and the grading
all happen there. To put a design on the <a href="../board/">official leaderboard</a> under your name,
the workspace asks for your permission here, with your qccd.academy account. Your password stays on
this site; the workspace gets a key that can only submit designs as you, and you can revoke it below.</p>
<div id="cx" data-qccd-private="1">
  <div id="cx-msg" class="note">Checking whether you are signed in&hellip;</div>
  <div id="cx-ask" class="cx-card" hidden>
    <h2>Allow this workspace to submit as you?</h2>
    <p><b id="cx-label"></b> asks to submit designs to the leaderboard under the name <b id="cx-name"></b>.</p>
    <p class="cx-small">It showed the code below. Allow it only if you just pressed <b>Sign in</b> in your own
    workspace (or ran <code>qccd login</code>) and it shows the same code.</p>
    <p class="cx-code" id="cx-code"></p>
    <button class="cx-btn" type="button" id="cx-allow" data-hint="connect:allow">Allow</button>
    <button class="cx-btn alt" type="button" id="cx-deny" data-hint="connect:deny">Don&rsquo;t allow</button>
  </div>
  <div id="cx-done" class="cx-card" hidden></div>
  <section id="cx-mine" hidden>
    <h2>Workspaces that can submit as you</h2>
    <table class="cx-t"><thead><tr><th>workspace</th><th>signed in</th><th>last used</th><th></th></tr></thead>
      <tbody id="cx-keys"></tbody></table>
    <h2>What you submitted</h2>
    <table class="cx-t"><thead><tr><th>board</th><th>design</th><th>shown</th><th>status</th><th>date</th></tr></thead>
      <tbody id="cx-subs"></tbody></table>
  </section>
</div>
<script>
window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, {
  "connect:allow": {"t": "Allow", "d": "let the workspace that showed this code submit designs to the leaderboard under your name (only the person presses it)"},
  "connect:deny": {"t": "Don't allow", "d": "refuse this sign-in code; the workspace gets nothing"},
  "connect:revoke": {"t": "Revoke", "d": "stop this workspace submitting as you; what it submitted stays"}});
(function(){
  if (typeof fetch !== 'function') return;
  if (window.top !== window.self) {       // never inside another site's frame: Allow must be a click on qccd.academy itself
    document.getElementById('cx-msg').textContent = 'Open this page in its own tab.';
    return;
  }
  if (window.QCCD_MIRROR) {             // a workspace's own copy of the site: the Allow happens on qccd.academy
    document.getElementById('cx-msg').textContent = 'Open this page on qccd.academy itself (your workspace opens it there when you press Sign in).';
    return;
  }
  var $ = function(id){ return document.getElementById(id); };
  var m = /[?&]code=([A-Za-z0-9-]+)/.exec(location.search), CODE = m ? m[1].toUpperCase() : null;
  var ME = null;
  function el(t, c, x){ var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  function day(t){ return t ? new Date(t * 1000).toISOString().slice(0, 10) : '-'; }
  function post(url, body){
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body || {}) }).then(function(r){
      return r.json().catch(function(){ return {}; }).then(function(d){
        if (!r.ok) { var e = d && d.error; throw new Error((e && (e.message || e)) || ('HTTP ' + r.status)); }
        return d; }); });
  }
  function grant(purpose, code){ return post('/api/leaderboard/grant', { purpose: purpose, code: code }).then(function(d){ return d.grant; }); }
  function done(ok, text){ var d = $('cx-done'); d.hidden = false; d.textContent = ''; d.appendChild(el('p', ok ? 'cx-ok' : 'cx-bad', text)); $('cx-ask').hidden = true; }
  function mine(){
    return grant('manage').then(function(g){ return post('/official/v1/account/keys', { grant: g }); }).then(function(d){
      $('cx-mine').hidden = false;
      var kb = $('cx-keys'), sb = $('cx-subs'); kb.textContent = ''; sb.textContent = '';
      if (!(d.keys || []).length) { var tr0 = el('tr'); var td0 = el('td', 'cx-small', 'None yet.'); td0.colSpan = 4; tr0.appendChild(td0); kb.appendChild(tr0); }
      (d.keys || []).forEach(function(k){
        var tr = el('tr');
        tr.appendChild(el('td', null, k.label || 'a workspace'));
        tr.appendChild(el('td', null, day(k.created_at)));
        tr.appendChild(el('td', null, k.revoked ? 'revoked' : day(k.last_used_at)));
        var td = el('td');
        if (!k.revoked) {
          var b = el('button', 'cx-btn alt', 'Revoke'); b.type = 'button'; b.setAttribute('data-hint', 'connect:revoke');
          b.onclick = function(){ b.disabled = true; grant('manage').then(function(g){
            return post('/official/v1/account/keys/' + encodeURIComponent(k.key_id) + '/revoke', { grant: g }); }).then(mine,
            function(e){ b.disabled = false; alert('Not revoked: ' + e.message); }); };
          td.appendChild(b);
        }
        tr.appendChild(td); kb.appendChild(tr);
      });
      if (!(d.submissions || []).length) { var tr1 = el('tr'); var td1 = el('td', 'cx-small', 'Nothing yet.'); td1.colSpan = 5; tr1.appendChild(td1); sb.appendChild(tr1); }
      (d.submissions || []).forEach(function(s){
        var tr = el('tr');
        [s.board, s.display_name || '-', s.visibility, s.status, day(s.created_at)].forEach(function(v){ tr.appendChild(el('td', null, v)); });
        sb.appendChild(tr);
      });
    }, function(e){ $('cx-mine').hidden = true; if (!CODE) $('cx-msg').textContent = 'Your workspaces could not be listed: ' + e.message; });
  }
  function ask(){
    fetch('/official/v1/links/' + encodeURIComponent(CODE), { headers: { 'Accept': 'application/json' } }).then(function(r){
      return r.json().then(function(d){ return { ok: r.ok, d: d }; }); }).then(function(x){
      if (!x.ok) { done(false, 'This sign-in code is unknown or expired. Press Sign in in your workspace again.'); return; }
      var l = x.d;
      if (l.status !== 'pending') { done(l.status === 'delivered' || l.status === 'approved',
        l.status === 'delivered' || l.status === 'approved' ? 'This workspace is already signed in.' : 'This sign-in is ' + l.status + '. Press Sign in in your workspace again.'); return; }
      $('cx-label').textContent = l.label; $('cx-name').textContent = ME.name; $('cx-code').textContent = l.code;
      $('cx-ask').hidden = false;
      $('cx-allow').onclick = function(){
        $('cx-allow').disabled = $('cx-deny').disabled = true;
        grant('link', l.code).then(function(g){ return post('/official/v1/links/' + encodeURIComponent(l.code) + '/approve', { grant: g }); })
          .then(function(){ done(true, 'Allowed. ' + l.label + ' now submits as ' + ME.name + '. You can close this tab and go back to your workspace.'); mine(); },
                function(e){ $('cx-allow').disabled = $('cx-deny').disabled = false; done(false, 'Not allowed: ' + e.message); });
      };
      $('cx-deny').onclick = function(){
        grant('link', l.code).then(function(g){ return post('/official/v1/links/' + encodeURIComponent(l.code) + '/deny', { grant: g }); })
          .then(function(){ done(false, 'Refused. The workspace got nothing.'); }, function(e){ done(false, e.message); });
      };
    });
  }
  function start(){
    fetch('/api/me', { credentials: 'same-origin' }).then(function(r){ return r.ok ? r.json() : { user: null }; }).then(function(d){
      if (!d || !d.user) {
        $('cx-msg').textContent = 'Sign in first, with Sign in in the bar above (your own qccd.academy account). This page continues by itself.';
        setTimeout(start, 2500);
        return;
      }
      ME = d.user;
      $('cx-msg').textContent = 'Signed in as ' + ME.name + '.';
      if (CODE) ask();
      mine();
    }, function(){ setTimeout(start, 5000); });
  }
  start();
})();
</script>"""


def connect_page(PAGE: str, STYLE: str) -> str:
    return PAGE.format(title="Connect a workspace - QCCD studio", style=STYLE, extra_css=CONNECT_CSS, body=CONNECT_BODY)
