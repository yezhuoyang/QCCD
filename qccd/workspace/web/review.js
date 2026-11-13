// qccd/workspace/web/review.js -- an agent review's page (service.py `_review_page`, reviews.py).
//
// One page, three moments:
//   new       /review/new: the Debug page that opened this tab hands over its programme -- this
//             page says {qccd:'review-ready'} to its opener, which answers {qccd:'review-request',
//             request}.  Only the workspace's own pages and the website mirror may (CFG.openers).
//             The person picks Claude or ChatGPT, signs it in once if it is not (its CLI's own
//             sign-in, in Docker), and presses Run.
//   running   /reviewview/<id>: the rule check's verdict as soon as it is in, then the agent's
//             progress, line by line
//   done      the report: the rule check's verdicts (the only verdicts), the agent's review and its
//             notes by step -- every "step N" a link to the Debug page at that step -- and what the
//             container reached
// Writes carry the pairing's CSRF token (GET /api/whoami); signing in and running are the person's.
(function () {
'use strict';
var CFG = JSON.parse(document.getElementById('qccd-review-config').textContent);
var root = document.getElementById('rv');
var ACTIVE = ['queued', 'checking', 'reviewing'];
var LIVE = ['preparing', 'waiting', 'finishing'];
var S = { csrf: null, id: CFG.review_id, req: null, waiting: !CFG.review_id, rev: null, who: null, models: null,
          choice: get('qccd.review.agent') || 'claude', model: {}, signin: null, busy: false, error: '', past: [],
          code: '' };

function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function put(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* none */ } }
function esc(s) {
  return String(s === undefined || s === null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function h(tag, attrs, kids) {
  var el = document.createElement(tag);
  for (var k in (attrs || {})) {
    var v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') el.textContent = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'cls') el.className = v;
    else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  (kids || []).forEach(function (c) { if (c !== null && c !== undefined && c !== false) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return el;
}
function api(method, path, body) {
  var opt = { method: method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  if (method !== 'GET' && S.csrf) opt.headers['X-QCCD-CSRF'] = S.csrf;
  return fetch(path, opt).then(function (r) {
    return r.json().catch(function () { return null; }).then(function (j) {
      if (!r.ok) throw new Error(((j || {}).error || {}).message || ('the workspace answered ' + r.status));
      return j;
    });
  });
}
function fail(e) { S.busy = false; S.error = String((e && e.message) || e); render(); }
function ago(t) {
  if (!t) return '';
  var s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? s + ' s ago' : s < 3600 ? Math.round(s / 60) + ' min ago' : new Date(t * 1000).toLocaleString();
}
// a link this page draws: http(s) only -- a request handed over by a page is that page's data
function safeUrl(u) { return /^https?:\/\//i.test(String(u || '')) ? String(u) : null; }
function secs(a, b) { return a ? Math.max(0, Math.round((b || Date.now() / 1000) - a)) : 0; }
// a model by its name: the chat's own label for it, or "Claude Sonnet 5.5" from claude-sonnet-5-5
function modelName(id) {
  if (!id) return '';
  for (var k in (S.models || {})) {
    var hit = (((S.models || {})[k] || {}).models || []).filter(function (m) { return m.id === id && m.label; })[0];
    if (hit) return hit.label;
  }
  var m = /^claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?$/.exec(id);
  return m ? 'Claude ' + m[1].charAt(0).toUpperCase() + m[1].slice(1) + ' ' + m[2] + '.' + m[3] : id;
}
function whoOf(id) { return ((S.who || {}).reviewers || []).filter(function (r) { return r.id === id; })[0] || null; }

// ------------------------------------------------------------------ what the Debug page hands over

function waitForRequest() {
  window.addEventListener('message', function (e) {
    var m = e.data || {};
    if (m.qccd !== 'review-request' || !window.opener || e.source !== window.opener) return;
    if (e.origin !== location.origin && CFG.openers.indexOf(e.origin) < 0) return;
    if (!m.request || typeof m.request.arch !== 'string' || typeof m.request.program !== 'string') return;
    S.req = m.request; S.waiting = false;
    render();
  });
  if (window.opener) { try { window.opener.postMessage({ qccd: 'review-ready' }, '*'); } catch (x) { /* none */ } }
  setTimeout(function () { if (S.waiting) { S.waiting = false; render(); } }, 8000);
}
function machineName(req) {
  try { var a = JSON.parse(req.arch); return a.name || a.title || ''; } catch (e) { return ''; }
}

// ------------------------------------------------------------------ the agents and signing in

function loadWho() {
  return api('GET', '/api/reviewers').then(function (w) {
    S.who = w;
    var live = (w.signins || []).filter(function (s) { return LIVE.indexOf(s.state) >= 0; })[0];
    if (live && !S.signin) { S.signin = live; pollSignin(); }
    render();
  }).catch(fail);
}
function watchWho() {
  if (!(S.rev && ACTIVE.indexOf(S.rev.status) >= 0) && !S.signin) loadWho();
  setTimeout(watchWho, 4000);
}
function signIn(rid) {
  S.error = '';
  api('POST', '/api/reviewers/' + rid + '/signin', {}).then(function (v) { S.signin = v; S.code = ''; render(); pollSignin(); }).catch(fail);
}
function pollSignin() {
  var s = S.signin;
  if (!s) return;
  api('GET', '/api/signins/' + s.signin_id).then(function (v) {
    if (!S.signin || S.signin.signin_id !== v.signin_id) return;
    S.signin = v;
    if (LIVE.indexOf(v.state) >= 0) setTimeout(pollSignin, 1000);
    else loadWho();
    render();
  }).catch(fail);
}
function sendCode() {
  var s = S.signin, code = (S.code || '').trim();
  if (!s || !code) return;
  api('POST', '/api/signins/' + s.signin_id + '/code', { code: code }).then(function (v) { S.signin = v; S.code = ''; render(); }).catch(fail);
}
function cancelSignin() {
  var s = S.signin;
  if (!s) return;
  api('POST', '/api/signins/' + s.signin_id + '/cancel', {}).then(function () { S.signin = null; loadWho(); }).catch(fail);
}
function signOut(rid) {
  api('POST', '/api/reviewers/' + rid + '/signout', {}).then(function () { loadWho(); }).catch(fail);
}

// ------------------------------------------------------------------ running, and the review

function run() {
  S.busy = true; S.error = ''; render();
  api('POST', '/api/reviews', { request: S.req, reviewer: S.choice, model: S.model[S.choice] || null }).then(function (r) {
    S.id = r.review_id; S.rev = r; S.busy = false;
    try { history.replaceState(null, '', '/reviewview/' + r.review_id); } catch (e) { /* a sandbox */ }
    render(); pollReview();
  }).catch(fail);
}
function again(rid) {
  S.busy = true; S.error = ''; render();
  api('POST', '/api/reviews/' + S.id + '/again', { reviewer: rid, model: S.model[rid] || null }).then(function (r) {
    location.assign('/reviewview/' + r.review_id);
  }).catch(fail);
}
function cancelRun() {
  api('POST', '/api/reviews/' + S.id + '/cancel', {}).then(function (r) { S.rev = Object.assign({}, S.rev, r); render(); }).catch(fail);
}
function pollReview() {
  api('GET', '/api/reviews/' + S.id + '?brief=1').then(function (r) {
    if (ACTIVE.indexOf(r.status) >= 0) { S.rev = Object.assign({}, S.rev || {}, r); render(); setTimeout(pollReview, 1500); return; }
    return api('GET', '/api/reviews/' + S.id).then(function (full) { S.rev = full; render(); loadPast(); });
  }).catch(fail);
}
function loadPast() {
  api('GET', '/api/reviews?limit=12').then(function (r) { S.past = r.reviews || []; render(); }).catch(function () { /* none */ });
}

// ------------------------------------------------------------------ the report

// the Debug page at step n: the link the review came with, its step set, its check on
function stepLink(n) {
  var link = safeUrl((S.rev || S.req || {}).link);
  if (!link) return null;
  try {
    var u = new URL(link), hsh = (u.hash || '').replace(/^#/, '');
    if (/^debug(&|$)/.test(hsh)) {
      var q = new URLSearchParams(hsh.replace(/^debug&?/, ''));
      q.set('step', String(n)); q.set('check', '1');
      u.hash = 'debug&' + q.toString();
    } else {
      u.searchParams.set('step', String(n)); u.searchParams.set('check', '1');
    }
    return u.href;
  } catch (e) { return null; }
}
function stepA(n, text) {
  var u = stepLink(n);
  return u ? '<a class="step" href="' + esc(u) + '" target="_blank" rel="noopener" title="open the Debug page at step ' + n + '">' + text + '</a>' : text;
}
// "step 12", "steps 5-7", "steps 3, 6 and 9": each number linked to the Debug page at that step
// (in text that is already escaped)
function linkSteps(s) {
  return s.replace(/\b(steps?)(\s+)(\d{1,6}(?:\s*(?:,|–|-|to|and|or)\s*\d{1,6})*)/gi, function (all, w, sp, list) {
    return w + sp + list.replace(/\d{1,6}/g, function (n) { return stepA(+n, n); });
  });
}
function inline(s) {
  var codes = [];
  s = esc(s).replace(/`([^`]+)`/g, function (_, c) { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  s = linkSteps(s);
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, t, u) {
    return '<a href="' + u + '" target="_blank" rel="noopener">' + t + '</a>';
  });
  return s.replace(/\u0000(\d+)\u0000/g, function (_, k) { return '<code>' + codes[+k] + '</code>'; });
}
// Markdown as agents write it: headings, lists (nested by indent), tables, code, quotes, paragraphs.
// Every character of the source is escaped first; only this function's own tags are HTML.
function md(src) {
  var L = String(src || '').replace(/\r\n?/g, '\n').split('\n'), out = [], i = 0, para = [];
  function flush() { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } }
  while (i < L.length) {
    var ln = L[i], m;
    if (/^\s*```/.test(ln)) {
      flush();
      var code = []; i++;
      while (i < L.length && !/^\s*```/.test(L[i])) code.push(L[i++]);
      i++;
      out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>');
      continue;
    }
    if ((m = /^(#{1,6})\s+(.*)$/.exec(ln))) { flush(); var lv = Math.min(6, m[1].length + 1); out.push('<h' + lv + '>' + inline(m[2]) + '</h' + lv + '>'); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(ln)) { flush(); out.push('<hr>'); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < L.length && /^\s*\|?\s*:?-{2,}/.test(L[i + 1])) {
      flush();
      var cells = function (row) { return row.trim().replace(/^\||\|$/g, '').split('|').map(function (c) { return c.trim(); }); };
      var head = cells(ln), rows = []; i += 2;
      while (i < L.length && /^\s*\|.*\|\s*$/.test(L[i])) rows.push(cells(L[i++]));
      out.push('<table><tr>' + head.map(function (c) { return '<th>' + inline(c) + '</th>'; }).join('') + '</tr>' +
        rows.map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + inline(c) + '</td>'; }).join('') + '</tr>'; }).join('') + '</table>');
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(ln)) {
      flush();
      var stack = [];               // [{indent, tag}]
      var html = '';
      while (i < L.length && (/^\s*([-*+]|\d+[.)])\s+/.test(L[i]) || (/^\s{2,}\S/.test(L[i]) && stack.length))) {
        var li = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(L[i]);
        if (!li) { html += ' ' + inline(L[i].trim()); i++; continue; }
        var ind = li[1].length, tag = /\d/.test(li[2]) ? 'ol' : 'ul';
        while (stack.length && ind < stack[stack.length - 1].indent) { html += '</li></' + stack.pop().tag + '>'; }
        if (!stack.length || ind > stack[stack.length - 1].indent) { html += '<' + tag + '>'; stack.push({ indent: ind, tag: tag }); }
        else html += '</li>';
        html += '<li>' + inline(li[3]);
        i++;
      }
      while (stack.length) html += '</li></' + stack.pop().tag + '>';
      out.push(html);
      continue;
    }
    if (/^\s*>/.test(ln)) {
      flush();
      var q = [];
      while (i < L.length && /^\s*>/.test(L[i])) q.push(L[i++].replace(/^\s*>\s?/, ''));
      out.push('<blockquote>' + md(q.join('\n')) + '</blockquote>');
      continue;
    }
    if (!ln.trim()) { flush(); i++; continue; }
    para.push(ln.trim()); i++;
  }
  flush();
  return out.join('\n');
}

// ------------------------------------------------------------------ drawing

// a poll redraws only what changed: an open menu, or the code being typed, is left alone
var drawn = '';
function render() {
  var tick = S.rev && ACTIVE.indexOf(S.rev.status) >= 0 ? Math.floor(Date.now() / 3000) : 0;      // the clock, while it runs
  var key = JSON.stringify([S.who, S.signin, S.models, S.rev, !!S.req, S.waiting, S.error, S.busy, S.choice, S.model, S.past, tick]);
  if (key === drawn) return;
  drawn = key;
  var focus = document.activeElement && document.activeElement.id, at = focus ? document.activeElement.selectionStart : null;
  var kids = [h('div', { cls: 'rv-top' }, [h('div', {}, [h('div', { cls: 'rv-brand', text: 'QCCD · agent review' }),
                                                       h('h1', { text: titleOf() })]), statusChip()])];
  if (S.rev) kids = kids.concat(reviewView(S.rev));
  else if (S.req) kids = kids.concat(newView(S.req));
  else if (S.waiting) kids.push(h('p', { cls: 'muted', text: 'Waiting for the Debug page to hand over its programme…' }));
  else kids = kids.concat([noRequest(), agentsCard('Your agents'), dockerCard('Docker')]);
  if (S.error) kids.push(h('p', { cls: 'err', role: 'alert', text: S.error }));
  kids.push(pastView());
  root.replaceChildren.apply(root, kids.filter(Boolean));
  var back = focus && document.getElementById(focus);
  if (back) { back.focus(); try { if (at !== null) back.setSelectionRange(at, at); } catch (e) { /* not a text field */ } }
  document.title = 'QCCD: agent review' + (titleOf() !== 'Agent review' ? ' of ' + titleOf() : '');
}
function titleOf() { return (S.rev && S.rev.title) || (S.req && S.req.title) || 'Agent review'; }
function statusChip() {
  var st = S.rev ? S.rev.status : S.req ? 'ready' : '';
  if (!st) return null;
  var words = { ready: 'ready to run', queued: 'starting', checking: 'checking the rules', reviewing: 'the agent is reviewing',
                done: 'done', failed: 'failed', cancelled: 'cancelled' };
  return h('span', { cls: 'chip ' + (ACTIVE.indexOf(st) >= 0 ? 'run' : st), text: words[st] || st });
}
function noRequest() {
  var web = CFG.web;
  return h('div', { cls: 'card' }, [
    h('p', { text: 'No programme was handed over. Open one in the Debug page (a board entry, your design, any Studio page) and press ' +
                   'Run agent review in its Rules tab.' }),
    h('p', {}, [web ? h('a', { href: web + 'debug/', text: 'The Debug page, from your workspace ↗' }) : null, web ? ' · ' : null,
                h('a', { href: '/studio', text: 'Your Studio ↗' })])]);
}
function newView(req) {
  var out = [];
  var m = machineName(req);
  out.push(h('p', { cls: 'rv-sub', text: 'Your own agent reviews this programme, in Docker on this computer. The rule check (the grader\'s own evaluator) ' +
                                         'judges every rule first, and its verdicts are the only ones; the agent explains what fails, where, and how to fix it.' }));
  out.push(h('div', { cls: 'card' }, [h('h2', { text: 'What it reviews' }), h('dl', { cls: 'kv' }, [
    h('dt', { text: 'programme' }), h('dd', { text: (req.title || 'a programme') + (req.steps ? ' — ' + req.steps + ' steps' : '') }),
    m ? h('dt', { text: 'machine' }) : null, m ? h('dd', { text: m }) : null,
    h('dt', { text: 'rules' }), h('dd', { text: (req.rules === 'none' ? 'no rule set' : 'the ' + (req.rules || 'standard') + ' rule set') +
                                                  ((req.assertions || '').trim() ? ', and your assertions' : '') }),
    safeUrl(req.link) ? h('dt', { text: 'from' }) : null,
    safeUrl(req.link) ? h('dd', {}, [h('a', { href: safeUrl(req.link), target: '_blank', rel: 'noopener', text: 'the Debug page ↗' })]) : null])]));
  out.push(agentsCard());
  out.push(dockerCard());
  var me = whoOf(S.choice), d = (S.who || {}).docker || {};
  var can = !!(me && me.signed_in && d.ready && !S.busy);
  var vendor = me ? me.vendor : 'its vendor';
  out.push(h('div', { cls: 'row' }, [
    h('button', { cls: 'pri big', type: 'button', disabled: !can, onclick: run, text: S.busy ? 'Starting…' : 'Run agent review' }),
    h('span', { cls: 'muted small', text: !me ? '' : !me.signed_in ? 'sign ' + me.title + ' in first' : !d.ready ? 'Docker first' :
      'with ' + me.title + ', in a container that reaches only ' + vendor + '\'s API' })]));
  return out;
}
function agentsCard(heading) {
  var list = (S.who || {}).reviewers || [];
  var rows = list.map(function (r) {
    var on = S.choice === r.id;
    var models = modelOptions(r.id);
    return h('div', { cls: 'agent' + (on ? ' on' : ''), onclick: function (e) {
      if (e.target.closest('button,select,a,input[type=text]')) return;
      S.choice = r.id; put('qccd.review.agent', r.id); render(); } }, [
      h('label', { cls: 'agent-h' }, [h('input', { type: 'radio', name: 'agent', checked: on, onchange: function () { S.choice = r.id; put('qccd.review.agent', r.id); render(); } }),
                                      r.title + ' ', h('span', { cls: 'muted small', text: '(' + r.cli + ')' })]),
      h('p', { cls: r.signed_in ? 'ok' : 'muted', text: r.signed_in ? 'Signed in: ' + r.account : 'Not signed in. Uses ' + r.plan + '.' }),
      h('div', { cls: 'row' }, [
        r.signed_in ? null : h('button', { type: 'button', cls: on ? 'pri' : '', disabled: !((S.who || {}).docker || {}).ready || liveSignin(r.id),
                                           onclick: function () { signIn(r.id); }, text: 'Sign in with ' + r.title }),
        r.signed_in && models.length > 1 ? h('label', { cls: 'small muted' }, ['model ', h('select', { onchange: function (e) { S.model[r.id] = e.target.value; } },
          models.map(function (o) { return h('option', { value: o.id, selected: (S.model[r.id] || '') === o.id, text: o.label }); }))]) : null,
        r.signed_in ? h('button', { type: 'button', cls: 'link small', onclick: function () { signOut(r.id); }, text: 'sign out' }) : null]),
      signinView(r.id)]);
  });
  return h('div', { cls: 'card' + (list.some(function (r) { return r.signed_in; }) ? '' : ' pri') }, [
    h('h2', { text: heading || '1 · Your agent' }),
    h('p', { cls: 'muted small', text: 'Sign in once, with your own account: the agent\'s own sign-in runs in Docker on this computer, and what it ' +
                                       'leaves is kept here, readable by you only. Your usual Claude Code or Codex is not touched.' }),
    S.who ? h('div', { cls: 'agents' }, rows) : h('p', { cls: 'muted', text: 'Asking who is signed in…' })]);
}
function modelOptions(rid) {
  var c = (S.models || {})[rid] || {};
  var list = (c.models || []).map(function (m) { return { id: m.id || m.model || '', label: m.label || m.display_name || m.id || 'Default' }; });
  if (!list.length || list[0].id) list.unshift({ id: '', label: 'Default' });
  return list;
}
function liveSignin(rid) { return !!(S.signin && S.signin.reviewer === rid && LIVE.indexOf(S.signin.state) >= 0); }
function signinView(rid) {
  var s = S.signin;
  if (!s || s.reviewer !== rid) return null;
  if (s.state === 'signed_in') return h('div', { cls: 'signin' }, [h('span', { cls: 'ok', text: s.title + ' is signed in.' })]);
  if (s.state === 'failed' || s.state === 'cancelled') {
    return h('div', { cls: 'signin' }, [h('p', { cls: s.state === 'failed' ? 'err' : 'muted', text: s.error || 'The sign-in was cancelled.' }),
      h('button', { type: 'button', onclick: function () { signIn(rid); }, text: 'Try again' })]);
  }
  var steps = [];
  if (s.state === 'preparing' || !s.url) steps.push(h('li', { cls: 'muted', text: (s.note || 'starting') + '…' }));
  if (safeUrl(s.url)) steps.push(h('li', {}, ['Open ', h('a', { href: safeUrl(s.url), target: '_blank', rel: 'noopener', text: rid === 'claude' ? 'claude.com' : 'auth.openai.com' }),
                                     ' and sign in with your ' + (rid === 'claude' ? 'Claude' : 'ChatGPT') + ' account.']));
  if (rid === 'codex' && s.code) {
    steps.push(h('li', {}, ['Enter this one-time code there: ', h('span', { cls: 'code', text: s.code }), ' ',
      h('button', { type: 'button', cls: 'link small', onclick: function () { try { navigator.clipboard.writeText(s.code); } catch (e) { /* none */ } }, text: 'copy' })]));
    steps.push(h('li', { cls: 'muted small', text: 'Codex printed it, in the container this page started. It expires in 15 minutes, and this page ' +
                                                   'goes on by itself once you approve. If that page says codes are off for your account, turn device ' +
                                                   'codes on in ChatGPT\'s security settings, then try again.' }));
  }
  if (rid === 'claude' && s.url) {
    steps.push(h('li', {}, ['Approve, then paste the code that page shows: ',
      h('div', { cls: 'row' }, [h('input', { type: 'text', id: 'rv-code', 'aria-label': 'the code', value: S.code, placeholder: 'the code', autocomplete: 'off',
                                             oninput: function (e) { S.code = e.target.value; },
                                             onkeydown: function (e) { if (e.key === 'Enter') sendCode(); } }),
                                h('button', { type: 'button', cls: 'pri', disabled: s.state !== 'waiting', onclick: sendCode, text: s.state === 'finishing' ? 'Checking…' : 'Send' })])]));
  }
  if (s.state === 'finishing') steps.push(h('li', { cls: 'muted small', text: (s.note || 'checking the code') + '…' }));
  return h('div', { cls: 'signin' }, [h('b', { text: 'Signing ' + s.title + ' in' }), h('ol', {}, steps),
    h('button', { type: 'button', cls: 'link small', onclick: cancelSignin, text: 'cancel' })]);
}
function dockerCard(heading) {
  var d = (S.who || {}).docker;
  if (!d) return h('div', { cls: 'card' }, [h('h2', { text: heading || '2 · Docker' }), h('p', { cls: 'muted', text: 'Asking Docker…' })]);
  return h('div', { cls: 'card' + (d.ready ? '' : ' badcard') }, [h('h2', { text: heading || '2 · Docker' }),
    d.ready ? h('p', { cls: 'ok', text: 'Docker is running.' + (d.built ? '' : ' The reviewer image is built on the first sign-in or review: a few minutes, once.') })
            : h('p', { cls: 'bad', text: d.why || 'Docker is not running.' }),
    d.ready ? null : h('button', { type: 'button', onclick: loadWho, text: 'Check again' })]);
}
function reviewView(r) {
  var out = [];
  var who = (r.reviewer_title || r.reviewer || 'the agent') + (r.model ? ' (' + modelName(r.model) + ')' : '');
  out.push(h('p', { cls: 'rv-sub' }, [(r.steps ? r.steps + ' steps, ' : '') + (r.rules === 'none' ? 'your assertions only' : 'the ' + r.rules + ' rule set') +
    (r.has_assertions && r.rules !== 'none' ? ' and your assertions' : '') + ', reviewed by ' + who + ' in Docker, ' + ago(r.created_at) + '. ',
    safeUrl(r.link) ? h('a', { href: safeUrl(r.link), target: '_blank', rel: 'noopener', text: 'Open it in the Debug page ↗' }) : null]));
  if (r.verdict) {
    out.push(h('div', { cls: 'card ' + (r.verdict.failing.length ? 'badcard' : '') }, [h('h2', { text: 'The rule check' }),
      h('p', { cls: 'verdict', html: esc(r.verdict.line) + '.' }),
      h('p', { cls: 'muted small', text: 'The grader\'s own evaluator. These verdicts are the review\'s; the agent explains them and never changes them.' }),
      r.check ? rulesTable(r.check) : null]));
  }
  if (ACTIVE.indexOf(r.status) >= 0) {
    out.push(h('div', { cls: 'card pri' }, [h('h2', { text: (r.status === 'reviewing' ? who + ' is reviewing' : 'Checking the rules') + ' · ' + secs(r.started_at || r.created_at) + ' s' }),
      h('ul', { cls: 'prog' }, (r.progress || []).map(function (p) {
        return h('li', {}, [h('span', { cls: 't', text: secs(r.started_at || r.created_at, p.t) + ' s' }), p.text]);
      })),
      h('div', { cls: 'row' }, [h('button', { type: 'button', onclick: cancelRun, text: 'Cancel' })])]));
    return out;
  }
  if (r.error) out.push(h('div', { cls: 'card badcard' }, [h('h2', { text: r.status === 'cancelled' ? 'Cancelled' : 'The review did not finish' }), h('p', { cls: 'err', text: r.error })]));
  if (r.review_md) {
    out.push(h('div', { cls: 'card' }, [h('h2', { text: 'The review, by ' + who }), h('div', { cls: 'md', html: md(r.review_md) })]));
  }
  if ((r.notes || []).length) {
    out.push(h('div', { cls: 'card' }, [h('h2', { text: 'Notes, by step' }), h('ul', { cls: 'notes' }, r.notes.map(function (n) {
      return h('li', {}, [h('span', { html: stepA(n.step, 'step ' + n.step) + (n.rule ? ' · ' + esc(n.rule) : '') }), h('span', { html: linkSteps(esc(n.text)) })]);
    }))]));
  }
  out.push(detailsCard(r));
  out.push(agentsCard('Review it again'));
  var me = whoOf(S.choice);
  out.push(h('div', { cls: 'row' }, [h('button', { type: 'button', cls: 'pri', disabled: S.busy || !(me && me.signed_in),
                                                   onclick: function () { again(S.choice); }, text: 'Review it again' + (me ? ', with ' + me.title : '') }),
    me && !me.signed_in ? h('span', { cls: 'muted small', text: 'sign ' + me.title + ' in first' }) : null]));
  return out;
}
function rulesTable(check) {
  var rules = (check.rules || []).slice().sort(function (a, b) { return (a.verdict === 'fails' ? 0 : 1) - (b.verdict === 'fails' ? 0 : 1); });
  function row(x) {
    var f = (x.failures || [])[0], first = f && f.step ? stepA(f.step, 'step ' + f.step) : f ? 'every step' : '';
    var cls = x.verdict === 'fails' ? 'v-fails' : x.verdict === 'holds' ? 'v-holds' : 'v-na';
    return '<tr><td><b>' + esc(x.id) + '</b></td><td>' + esc(x.title) + '</td><td class="' + cls + '">' +
      esc(x.verdict === 'fails' ? 'fails (' + x.count + ')' : x.verdict === 'holds' ? 'holds' : 'does not apply') + '</td><td>' + first + '</td></tr>';
  }
  var bad = rules.filter(function (x) { return x.verdict === 'fails'; }), rest = rules.filter(function (x) { return x.verdict !== 'fails'; });
  var head = '<tr><th>rule</th><th></th><th>verdict</th><th>first fails</th></tr>';
  var box = h('div', {});
  if (bad.length) box.appendChild(h('table', { cls: 'rules', html: head + bad.map(row).join('') }));
  box.appendChild(h('details', {}, [h('summary', { text: 'every rule (' + rules.length + ')' }), h('table', { cls: 'rules', html: head + rest.map(row).join('') })]));
  return box;
}
function detailsCard(r) {
  var u = r.usage || {}, eg = r.egress || {};
  var bits = [];
  if (u.seconds) bits.push(['time', Math.round(u.seconds) + ' s' + (u.num_turns ? ', ' + u.num_turns + ' turns' : '')]);
  if (u.tokens) bits.push(['tokens', String(u.tokens)]);
  if (u.total_cost_usd) bits.push(['at API prices', '$' + u.total_cost_usd.toFixed(2) + ' (your plan pays for it, not per token)']);
  if (eg.allowed) bits.push(['the container reached', eg.allowed.join(', ') || 'nothing']);
  if (eg.refused && eg.refused.length) bits.push(['refused', eg.refused.join(', ')]);
  if (r.redacted) bits.push(['removed', r.redacted + ' thing(s) that looked like a credential']);
  if (!bits.length) return null;
  return h('details', { cls: 'card' }, [h('summary', { text: 'How it ran' }), h('dl', { cls: 'kv' }, bits.reduce(function (a, b) {
    return a.concat([h('dt', { text: b[0] }), h('dd', { text: b[1] })]);
  }, []))]);
}
function pastView() {
  var rows = (S.past || []).filter(function (p) { return p.review_id !== S.id; });
  if (!rows.length) return null;
  return h('div', { cls: 'card' }, [h('h2', { text: 'Earlier reviews' }), h('ul', { cls: 'past' }, rows.map(function (p) {
    return h('li', {}, [h('span', { cls: 'chip ' + (ACTIVE.indexOf(p.status) >= 0 ? 'run' : p.status), text: p.status }),
      h('a', { cls: 'grow', href: p.page, text: p.title || p.review_id }),
      h('span', { cls: 'muted small', text: (p.reviewer_title || p.reviewer) + ' · ' + ((p.verdict || {}).failing || []).length + ' failing · ' + ago(p.created_at) })]);
  }))]);
}

// ------------------------------------------------------------------ start

api('GET', '/api/whoami').then(function (w) { S.csrf = w.csrf || null; }).catch(fail);
api('GET', '/api/agents/models').then(function (m) { S.models = m; render(); }).catch(function () { /* the default only */ });
render();
loadPast();
loadWho();
setTimeout(watchWho, 4000);
if (S.id) pollReview();
else waitForRequest();
})();
