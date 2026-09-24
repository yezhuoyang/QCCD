// qccd/workspace/web/mirror.js -- the chat on a page of the local website mirror.
//
// The mirror (qccd/workspace/mirror.py) serves qccd.academy's pages on their OWN local
// origin, which the workspace does not trust: nothing on it can reach the workspace API.
// The chat is a frame from the workspace's origin (/chatframe), so the person's pairing
// stays there.  This script only places that frame and answers it:
//   frame -> page   {qccd:'act', id, action, args}  one whitelisted action (pageact.js)
//                   {qccd:'context', id}            the page's URL, title, selection
//                   {qccd:'pick', id}               let the person point at something
//                   {qccd:'size', min}              the chat was folded or unfolded
//   page -> frame   {qccd:'result', id, ...}        the answer
//                   {qccd:'page', context}          the selection changed
// Messages are accepted only from that frame's window and origin.

(function () {
'use strict';
if (window.top !== window.self) return;                   // an embedded example: no chat
if (/(^|[#&])embed(&|$)/.test(location.hash || '')) return;
var cfgEl = document.getElementById('qccd-mirror-config');
if (!cfgEl || !window.QCCD_PAGE) return;
var CFG = JSON.parse(cfgEl.textContent);
var CHAT = CFG.chat_origin;
var MIN_KEY = 'qccd.mirror.min';

function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* none */ } }

var box = document.createElement('div');
box.id = 'qccd-chat';
var frame = document.createElement('iframe');
frame.title = 'Chat with your agent about this page';
frame.src = CHAT + '/chatframe?page=' + encodeURIComponent(location.pathname + location.search);
frame.setAttribute('allow', 'clipboard-write');
box.appendChild(frame);
if (get(MIN_KEY) === '1') box.className = 'min';
var RECT_KEY = 'qccd.mirror.rect';
try {
  var saved = JSON.parse(get(RECT_KEY) || 'null');
  if (saved && saved.x >= 0 && saved.y >= 0 && saved.x < innerWidth - 80 && saved.y < innerHeight - 40) {
    box.style.left = saved.x + 'px'; box.style.top = saved.y + 'px'; box.style.right = 'auto'; box.style.bottom = 'auto';
    if (saved.w) box.style.width = saved.w + 'px';
    if (saved.h && box.className !== 'min') box.style.height = saved.h + 'px';
  }
} catch (e) { /* none */ }
document.body.appendChild(box);

// the site's own nav: a way back to the person's Studio
var nav = document.getElementById('sitenav');
if (nav && CFG.studio_url) {
  var a = document.createElement('a');
  a.href = CFG.studio_url; a.target = '_blank'; a.rel = 'noopener';
  a.textContent = 'Your Studio'; a.id = 'qccd-mirror-studio'; a.setAttribute('data-qccd-private', '');
  a.title = 'your workspace\'s Studio: the design you and the agent are working on';
  nav.appendChild(a);
}

function reply(msg) { if (frame.contentWindow) frame.contentWindow.postMessage(msg, CHAT); }
window.addEventListener('message', function (e) {
  if (e.origin !== CHAT || e.source !== frame.contentWindow) return;
  var m = e.data || {};
  if (m.qccd === 'act') {
    window.QCCD_PAGE.act(String(m.action || ''), m.args || {}, m.who ? String(m.who).slice(0, 40) : 'Agent').then(function (out) {
      reply({ qccd: 'result', id: m.id, ok: out.ok, result: out.result, error: out.error });
    });
  } else if (m.qccd === 'context') {
    reply({ qccd: 'result', id: m.id, ok: true, result: window.QCCD_PAGE.context() });
  } else if (m.qccd === 'pick') {
    window.QCCD_PAGE.pick().then(function (v) { reply({ qccd: 'result', id: m.id, ok: !!v, result: v }); });
  } else if (m.qccd === 'navigate') {
    // the chat asks this tab to open one of the workspace's own pages (a run, the Studio)
    var u = null;
    try { u = new URL(String(m.url || '')); } catch (x) { u = null; }
    if (u && u.origin === CHAT && /^\/(studio|runview\/|compare|run\/)/.test(u.pathname)) location.assign(u.href);
  } else if (m.qccd === 'move' || m.qccd === 'resize') {
    var r = box.getBoundingClientRect();
    box.style.right = 'auto'; box.style.bottom = 'auto';
    if (m.qccd === 'move') {
      box.style.left = Math.max(0, Math.min(innerWidth - 80, r.left + (+m.dx || 0))) + 'px';
      box.style.top = Math.max(0, Math.min(innerHeight - 40, r.top + (+m.dy || 0))) + 'px';
    } else {
      box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
      box.style.width = Math.max(300, r.width + (+m.dw || 0)) + 'px';
      box.style.height = Math.max(220, r.height + (+m.dh || 0)) + 'px';
    }
  } else if (m.qccd === 'moved') {
    var q = box.getBoundingClientRect();
    set(RECT_KEY, JSON.stringify({ x: Math.round(q.left), y: Math.round(q.top), w: Math.round(q.width), h: Math.round(q.height) }));
  } else if (m.qccd === 'home') {
    box.style.cssText = ''; set(RECT_KEY, '');
  } else if (m.qccd === 'size') {
    box.className = m.min ? 'min' : '';
    set(MIN_KEY, m.min ? '1' : '0');
  }
});
var selT = null;
document.addEventListener('selectionchange', function () {
  if (selT) clearTimeout(selT);
  selT = setTimeout(function () { reply({ qccd: 'page', context: window.QCCD_PAGE.context() }); }, 250);
});
})();
