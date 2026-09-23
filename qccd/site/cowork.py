"""Two injected blocks that connect qccd.academy to the local co-design workspace.

* `studio_block()`: the Design page's **Agent** button (in the site bar) and its panel.
  Co-design itself runs in a local workspace (`qccd studio`, `qccd/workspace/`), because
  the agent and the design live on the person's machine; this page does not connect to
  it.  The panel says how to set one up, saves the current design as a `.studio.json`
  file (`EDITOR.snapshot()`, the document `qccd import` reads), and says how to submit.
* `board_section()`: the Leaderboard's **Official submissions**, read live from the
  official service at `/official/v1/` (qccd/official/, deploy/official/).  Only eligible,
  public submissions are listed there; the static boards above it are untouched.

Both are injected by `build.py` the way `qec_cycle` is, so the studio and the board
templates are not edited.  An embedded studio (`#embed`, or inside an iframe) gets no
button.  The panel opens by itself when the page is loaded with `?agent`.
"""

from __future__ import annotations

REPO_CLONE = "git clone -b compiler https://github.com/yezhuoyang/QCCD"

STUDIO_CSS = """
#qa-btn{font:inherit;font-size:13px;margin:0 6px 0 4px;padding:4px 11px 4px 9px;border:1px solid #bcd4f2;
 border-radius:99px;background:#eef4fc;color:#1d4f91;cursor:pointer;display:inline-flex;align-items:center;gap:6px;
 white-space:nowrap}
#qa-btn:hover{background:#e2edfb;border-color:#9dbfe9}
#qa-btn[aria-expanded="true"]{background:#2a78d6;border-color:#2a78d6;color:#fff}
#qa-btn i{width:7px;height:7px;border-radius:50%;background:#2a78d6;display:inline-block}
#qa-btn[aria-expanded="true"] i{background:#fff}
#qa-panel{position:fixed;top:48px;right:12px;width:min(480px,calc(100vw - 24px));max-height:calc(100vh - 62px);
 overflow:auto;background:#fff;border:1px solid #cfceca;border-radius:10px;box-shadow:0 14px 36px rgba(0,0,0,.18);
 z-index:1100;display:none;padding:16px 18px 14px;color:#1d1d1b;
 font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
#qa-panel[data-open="1"]{display:block}
#qa-panel h2{font-size:17px;margin:0 28px 6px 0;line-height:1.3}
#qa-panel h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#6b6a66;margin:16px 0 5px;font-weight:600}
#qa-panel p{margin:6px 0}
#qa-panel a{color:#1d4f91}
#qa-panel code,#qa-panel pre{font:12.5px/1.45 ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;background:#f5f4f1;border-radius:5px}
#qa-panel code{padding:1px 4px}
#qa-panel pre{padding:7px 9px;margin:4px 0 8px;white-space:pre-wrap;word-break:break-word;user-select:all}
#qa-panel .qa-note{background:#f3f7fd;border:1px solid #d7e4f6;border-radius:7px;padding:8px 10px;font-size:13px}
#qa-panel .qa-small{font-size:12.5px;color:#52514e}
#qa-panel .qa-x{position:absolute;top:8px;right:10px;border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:#6b6a66;padding:4px}
#qa-panel .qa-save{font:inherit;font-size:13.5px;padding:6px 12px;border:1px solid #2a78d6;border-radius:6px;background:#2a78d6;color:#fff;cursor:pointer}
#qa-panel .qa-save:hover{background:#1f66bd}
#qa-panel .qa-saved{font-size:12.5px;color:#0b7a4b;margin-left:8px}
"""

PANEL_HTML = """
<button class="qa-x" type="button" aria-label="close">&times;</button>
<h2>Design with an AI agent</h2>
<p>Work on a design together with an agent: Codex or Claude Code. Select parts of the device,
lasso a region, sketch an arrow, or show an edit once, and ask. The agent gets exactly what you
pointed at and edits the same design through checked changes. You watch each change arrive in
the Studio. Parts you lock stay locked, and any change can be undone.</p>
<p class="qa-note">This runs on your own computer, in a local workspace that this page does not
connect to. Nothing leaves your machine unless you publish it.</p>
<h3>Set up once</h3>
<pre>__CLONE__
cd QCCD
python -m venv .venv
.venv\\Scripts\\activate
pip install -e ".[agent]"
cd ..</pre>
<p class="qa-small">On macOS or Linux, activate with <code>source .venv/bin/activate</code>. If PowerShell
refuses to run the activate script, run
<code>Set-ExecutionPolicy -Scope CurrentUser RemoteSigned</code> once. The virtual
environment keeps QCCD's libraries apart from the rest of your Python, so installing it cannot
change a version another package of yours needs. Activate it again in each new terminal.
Compiling and grading on your machine also need the OCaml compiler and the Lean checker built:
<a href="https://github.com/yezhuoyang/QCCD/blob/compiler/deploy/official/Dockerfile">deploy/official/Dockerfile</a>
has the exact commands.</p>
<h3>Start a workspace</h3>
<p class="qa-small">Run these from the folder that holds QCCD, not inside it.</p>
<pre>qccd init my-design --task ghz4@1
cd my-design
qccd agent install --client codex
qccd studio --keep-alive</pre>
<p>That opens the live Studio, with a chat at the bottom right. Type to the agent there. If Codex is
installed, it starts by itself; otherwise run <code>qccd agent connect --client codex</code>. Ask it to
change the design, or to run a program and explain it, for example <em>"Compile and run the BB code
on this design and summarize the bottleneck"</em> or <em>"Run the BB code on draft A and draft B side
by side"</em>. The draft menu at the top of the chat saves and switches designs. Claude Code
works too: run <code>qccd agent install --client claude</code>, start <code>claude</code> in the workspace, and
ask it to handle the requests waiting in Studio.</p>
<h3>Ask about any page</h3>
<p>Your workspace also serves this whole website with the same chat on every page. In the workspace
folder, run:</p>
<pre>qccd web</pre>
<p>Ask about the page you are reading: a rule, a lesson, a leaderboard entry. Select a sentence
and it goes with your question. The agent reads the page and points at the answer on it. It
can also step the embedded examples, open lessons and take you to other pages. The
pages come from this site; the chat and the agent stay on your computer, and this public
site still does not connect to it.</p>
<h3>Bring this design</h3>
<p><button class="qa-save" type="button">Save this design as a file</button><span class="qa-saved"></span></p>
<p>Put the file in your workspace folder, then run:</p>
<pre class="qa-import">qccd import my-design.studio.json</pre>
<h3>Put it on the leaderboard</h3>
<pre>qccd submit --local --wait
qccd publish --submission &lt;id&gt;</pre>
<p>The first command grades the design on your machine with the reference checker. The second
asks you to approve and then uploads it. Once the server has graded it too, it appears under
<a href="__ROOT__board/#official">Official submissions</a>. Uploading needs a token from the
maintainers.</p>
<p class="qa-small"><a href="https://github.com/yezhuoyang/QCCD/blob/compiler/docs/workspace.md">How it works</a>:
the workspace, what is and is not tested, and the security model.</p>
"""

STUDIO_JS = r"""
(function(){
  if (window.self !== window.top) return;                 // an embed inside another page
  if (/^#embed/.test(location.hash)) return;
  var nav = document.getElementById('sitenav');
  if (!nav || document.getElementById('qa-btn')) return;
  var btn = document.createElement('button');
  btn.id = 'qa-btn'; btn.type = 'button'; btn.setAttribute('aria-expanded', 'false');
  btn.title = 'Design with an AI agent (Codex or Claude Code)';
  btn.innerHTML = '<i></i>Agent';
  nav.insertBefore(btn, nav.querySelector('.sn-sp'));
  var panel = document.createElement('div');
  panel.id = 'qa-panel'; panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Design with an AI agent');
  panel.setAttribute('data-open', '0');
  panel.innerHTML = __PANEL__;
  document.body.appendChild(panel);
  function open(v){ panel.setAttribute('data-open', v ? '1' : '0'); btn.setAttribute('aria-expanded', v ? 'true' : 'false'); }
  btn.addEventListener('click', function(){ open(panel.getAttribute('data-open') !== '1'); });
  panel.querySelector('.qa-x').addEventListener('click', function(){ open(false); });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') open(false); });
  var saved = panel.querySelector('.qa-saved');
  panel.querySelector('.qa-save').addEventListener('click', function(){
    var E = window.EDITOR;
    if (!E || typeof E.snapshot !== 'function') { saved.textContent = 'this page cannot export a design'; return; }
    var snap = E.snapshot();
    var base = String((snap && snap.arch && snap.arch.name) || '').toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
    if (!base || base === 'studio' || base === 'design') base = 'my-design';   // a fresh canvas's generic name
    var name = base + '.studio.json';
    var url = URL.createObjectURL(new Blob([JSON.stringify(snap, null, 1)], { type: 'application/json' }));
    var a = document.createElement('a');
    a.href = url; a.download = name; a.style.display = 'none';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){ try { URL.revokeObjectURL(url); } catch (e) {} }, 4000);
    panel.querySelector('.qa-import').textContent = 'qccd import ' + name;
    saved.textContent = 'saved ' + name;
    window.__qaLastSave = { name: name, snap: snap };      // for tests
  });
  if (/(^|[?&])agent(=|&|$)/.test(location.search.slice(1))) open(true);
})();
"""

BOARD_CSS = """
#official{margin-top:34px}
#official table{border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 14px}
#official th,#official td{text-align:left;padding:6px 8px;border-bottom:1px solid #e6e5e1}
#official th{font-weight:600;color:#52514e;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
#official td.qo-n{font-variant-numeric:tabular-nums}
#official .qo-task h3{margin:18px 0 2px}
#official .qo-task h3 small{font-weight:400;color:#8a8985;margin-left:6px}
#official .qo-empty,#official .qo-wait{color:#6b6a66}
#official .qo-err{color:#9a3412}
"""

BOARD_JS = r"""
(function(){
  var box = document.getElementById('qo-boards');
  if (!box) return;
  var API = '/official/v1';
  function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function get(path){
    return fetch(API + path, { headers: { 'Accept': 'application/json' } }).then(function(r){
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  function fmt(v){ return (typeof v === 'number') ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toPrecision(4)) : '-'; }
  get('/tasks').then(function(t){
    var tasks = (t && t.tasks) || [];
    return Promise.all(tasks.map(function(task){
      return get('/leaderboard/' + encodeURIComponent(task.id)).then(function(b){ return { task: task, board: b }; });
    }));
  }).then(function(all){
    box.textContent = '';
    if (!all.length) { box.appendChild(el('p', 'qo-empty', 'The official server lists no tasks.')); return; }
    all.forEach(function(x){
      var sec = el('div', 'qo-task');
      var h = el('h3', null, x.task.title || x.task.id); h.appendChild(el('small', null, x.task.id)); sec.appendChild(h);
      var rows = x.board.rows || [];
      var unit = ((x.task.metrics || []).filter(function(m){ return m.name === x.board.rank_by; })[0] || {}).unit || '';
      if (!rows.length) {
        sec.appendChild(el('p', 'qo-empty', 'No published submissions yet.'));
      } else {
        var tb = el('table'), hd = el('tr');
        ['#', 'design', x.board.rank_by + (unit ? ' (' + unit + ')' : ''), 'submitted', 'report'].forEach(function(c){ hd.appendChild(el('th', null, c)); });
        var th = el('thead'); th.appendChild(hd); tb.appendChild(th);
        var body = el('tbody');
        rows.forEach(function(r, i){
          var tr = el('tr');
          tr.appendChild(el('td', 'qo-n', String(i + 1)));
          tr.appendChild(el('td', null, r.display_name || r.id));
          tr.appendChild(el('td', 'qo-n', fmt(r.rank_value)));
          tr.appendChild(el('td', null, r.created_at ? new Date(r.created_at * 1000).toISOString().slice(0, 10) : '-'));
          var td = el('td'), a = el('a', null, 'report');
          a.href = API + '/submissions/' + encodeURIComponent(r.id) + '/report'; td.appendChild(a); tr.appendChild(td);
          body.appendChild(tr);
        });
        tb.appendChild(body); sec.appendChild(tb);
      }
      box.appendChild(sec);
    });
  }).catch(function(err){
    box.textContent = '';
    box.appendChild(el('p', 'qo-err', 'The official server did not answer (' + err.message + '). The boards above are unaffected.'));
  });
})();
"""

BOARD_HTML = """<section id="official">
<h2>Official submissions</h2>
<p class="sub">Designs that their authors submitted and published, each graded again on the
official server with the reference checker. That checker runs the same ten checks as a local
grade, including the proved Lean checker. Only eligible, public submissions appear here; a
private one never does. This list is read live from the server.</p>
<div id="qo-boards"><p class="qo-wait">Loading the official leaderboard...</p></div>
<p class="note">To contribute a design: build it in the <a href="../studio.html?agent#design">Studio</a>,
with an AI agent if you like (the Agent button there). Then, in your workspace,
<code>qccd submit --local --wait</code> grades it on your machine, and
<code>qccd publish --submission &lt;id&gt;</code> uploads it once you approve. The boards above
carry the study's seed entries.</p>
</section>"""


def studio_block(root: str = "") -> str:
    """The Design page's Agent button and panel, as one style + script block.  `root` is
    the page's path to the site root ('' for studio.html itself)."""
    import json
    panel = PANEL_HTML.replace("__CLONE__", REPO_CLONE).replace("__ROOT__", root)
    js = STUDIO_JS.replace("__PANEL__", json.dumps(panel).replace("</", "<\\/"))
    return f"<style>{STUDIO_CSS}</style>\n<script>{js}</script>\n"


def board_section() -> str:
    """The Leaderboard's live Official submissions section (HTML with its style and script)."""
    return f"<style>{BOARD_CSS}</style>\n{BOARD_HTML}\n<script>{BOARD_JS}</script>\n"


#: the note this section replaces on the board page (build.py wrote it before the official
#: service existed); kept here so a live page can be patched in place to exactly what a build
#: now produces
OLD_BOARD_NOTE_START = '<p class="note">Contributing a design from the studio (Verify &rarr; Contribute) arrives in phase 3 of '
