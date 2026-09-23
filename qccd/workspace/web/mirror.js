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
    var out = window.QCCD_PAGE.act(String(m.action || ''), m.args || {});
    reply({ qccd: 'result', id: m.id, ok: out.ok, result: out.result, error: out.error });
  } else if (m.qccd === 'context') {
    reply({ qccd: 'result', id: m.id, ok: true, result: window.QCCD_PAGE.context() });
  } else if (m.qccd === 'pick') {
    window.QCCD_PAGE.pick().then(function (v) { reply({ qccd: 'result', id: m.id, ok: !!v, result: v }); });
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
