/* The trace viewer (/trace): every step an agent took for a request, to read or replay.
 *
 * Left: the conversations the workspace traced and their requests.  Right: the steps of one
 * request in order -- what the agent was handed, what it thought, each tool / MCP / Skill
 * call with its result, each page action with what the page answered, how the turn ended.
 * Replay shows them one at a time with the recorded gaps (scaled), so you can watch a run
 * again the way it happened.  Deep link: /trace?session=<id>&prompt=<id>.
 */
(function () {
'use strict';

var S = { traces: [], session: null, prompt: null, steps: [], timer: null, shown: -1, speed: 4,
          hide: { mcp: true } };
var Q = new URLSearchParams(location.search);

function h(tag, attrs, kids) {
  var e = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    var v = attrs[k];
    if (v === undefined || v === null || v === false) return;
    if (k === 'text') e.textContent = v;
    else if (k === 'cls') e.className = v;
    else if (k === 'on') Object.keys(v).forEach(function (ev) { e.addEventListener(ev, v[ev]); });
    else e.setAttribute(k, v === true ? '' : v);
  });
  (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return e;
}
function get(path) {
  return fetch(path, { credentials: 'same-origin' }).then(function (r) {
    return r.json().then(function (d) { return { ok: r.ok, data: d }; });
  });
}
function when(t) { var d = new Date(t * 1000); return d.toLocaleString(); }
function secs(x) { return x < 60 ? x.toFixed(1) + ' s' : Math.floor(x / 60) + ' min ' + Math.round(x % 60) + ' s'; }
function pretty(v) {
  if (v === undefined || v === null) return '';
  if (typeof v === 'string') { try { return JSON.stringify(JSON.parse(v), null, 1); } catch (e) { return v; } }
  return JSON.stringify(v, null, 1);
}
function fold(label, body, open) {
  var d = h('details', { cls: 'fold', open: !!open }, [h('summary', { text: label }), h('pre', { text: body })]);
  return d;
}
function short(v, n) { var s = typeof v === 'string' ? v : JSON.stringify(v); s = s || ''; return s.length > n ? s.slice(0, n) + '…' : s; }

// ------------------------------------------------------------------ the list of conversations
function loadIndex() {
  return get('/api/traces').then(function (r) {
    var list = document.getElementById('tr-list');
    list.innerHTML = '';
    if (!r.ok) { list.appendChild(h('p', { cls: 'bad', text: (r.data.error || {}).message || 'could not load the traces' })); return; }
    S.traces = r.data.traces || [];
    if (!S.traces.length) {
      list.appendChild(h('p', { cls: 'muted', text: 'No traces yet. Every request you send an agent from Studio or a website page is traced from now on.' }));
      return;
    }
    S.traces.forEach(function (t) {
      var sec = h('section', { cls: 'tr-sess' }, [
        h('div', { cls: 'tr-sh' }, [h('b', { text: t.label || t.session }), h('span', { cls: 'muted', text: ' ' + (t.client || '') + ' · ' + when(t.last) })])
      ]);
      t.prompts.slice().reverse().forEach(function (p) {
        var b = h('button', { cls: 'tr-req', 'data-session': t.session, 'data-prompt': p.prompt || '',
                              on: { click: function () { open(t.session, p.prompt || ''); } } }, [
          h('span', { cls: 'tr-rt', text: p.text || (p.prompt ? p.prompt : 'steps outside a request') }),
          h('span', { cls: 'tr-rm', text: when(p.first) + ' · ' + p.steps + ' steps · ' + p.tools + ' calls' })
        ]);
        sec.appendChild(b);
      });
      list.appendChild(sec);
    });
    var want = Q.get('session'), wp = Q.get('prompt');
    var t0 = S.traces.filter(function (t) { return t.session === want; })[0] || S.traces[0];
    var p0 = t0.prompts.filter(function (p) { return p.prompt === wp; })[0] || t0.prompts[t0.prompts.length - 1];
    open(t0.session, p0 ? (p0.prompt || '') : '');
  });
}

function open(session, prompt) {
  stop();
  S.session = session; S.prompt = prompt;
  Array.prototype.forEach.call(document.querySelectorAll('.tr-req'), function (b) {
    b.classList.toggle('on', b.getAttribute('data-session') === session && b.getAttribute('data-prompt') === prompt);
  });
  history.replaceState(null, '', '/trace?session=' + encodeURIComponent(session) + (prompt ? '&prompt=' + encodeURIComponent(prompt) : ''));
  var path = '/api/traces/' + encodeURIComponent(session) + (prompt ? '?prompt=' + encodeURIComponent(prompt) : '');
  document.getElementById('tr-json').setAttribute('href', path);
  return get(path).then(function (r) {
    S.steps = r.ok ? (prompt ? r.data.steps : r.data.steps.filter(function (s) { return !s.prompt; })) : [];
    render();
  });
}

// ------------------------------------------------------------------ one request's steps
function visible(s) {
  if (S.hide.mcp && s.source === 'mcp' && S.steps.some(function (x) { return x.source === 'agent' && (x.kind === 'tool_call' || x.kind === 'item'); })) return false;
  if (S.hide.thinking && s.kind === 'thinking') return false;
  if (S.hide.page && s.kind === 'page_action') return false;
  return true;
}
function render() {
  var box = document.getElementById('tr-steps');
  box.innerHTML = '';
  var steps = S.steps.filter(visible);
  var head = document.getElementById('tr-head');
  head.innerHTML = '';
  if (!S.steps.length) { box.appendChild(h('p', { cls: 'muted', text: 'Nothing recorded for this request.' })); return; }
  var t0 = S.steps[0].at, t1 = S.steps[S.steps.length - 1].at;
  var d = S.steps.filter(function (s) { return s.kind === 'delivery'; })[0];
  var end = S.steps.filter(function (s) { return s.kind === 'turn'; }).pop();
  var calls = S.steps.filter(function (s) { return s.source === 'agent' && (s.kind === 'tool_call' || s.kind === 'skill' || (s.kind === 'item' && (s.data || {}).phase === 'completed' && s.name !== 'reasoning')); }).length;
  var mcp = S.steps.filter(function (s) { return s.source === 'mcp'; }).length;
  var pages = S.steps.filter(function (s) { return s.kind === 'page_action'; }).length;
  head.appendChild(h('h1', { text: d && (d.data || {}).request ? '“' + short(d.data.request, 300) + '”' : 'A request' }));
  var facts = [secs(t1 - t0), calls + ' agent tool calls', mcp + ' QCCD tool calls (MCP server)', pages + ' page actions'];
  if (end && end.data && end.data.total_cost_usd !== undefined) facts.push('$' + Number(end.data.total_cost_usd).toFixed(3));
  if (end && end.data && end.data.num_turns !== undefined) facts.push(end.data.num_turns + ' model turns');
  head.appendChild(h('p', { cls: 'muted', text: facts.join(' · ') }));
  var results = {};
  S.steps.forEach(function (s) { if (s.kind === 'tool_result' && s.data) results[s.data.id] = s; });
  steps.forEach(function (s, i) {
    if (s.kind === 'tool_result' && s.data && S.steps.some(function (x) { return x.kind !== 'tool_result' && x.data && x.data.id === s.data.id; })) return;
    if (s.kind === 'item' && (s.data || {}).phase === 'started' && S.steps.some(function (x) { return x !== s && x.kind === 'item' && x.data && x.data.id === s.data.id && x.data.phase === 'completed'; })) return;
    box.appendChild(card(s, t0, results));
  });
}

var SOURCE = { agent: 'agent', mcp: 'MCP server', page: 'page', service: 'workspace' };
function card(s, t0, results) {
  var d = s.data || {};
  var title = s.kind, body = [];
  if (s.kind === 'delivery') {
    title = s.name === 'steer' ? 'a correction, into the running turn' : 'handed to the agent';
    if (d.request) body.push(h('p', { cls: 'said', text: d.request }));
    body.push(fold('the full text the agent received (' + String(d.text || '').length + ' characters)', String(d.text || '')));
  } else if (s.kind === 'session') {
    title = 'the agent started' + (d.model ? ' on ' + d.model : '') + (d.claude_code_version ? ' (Claude Code ' + d.claude_code_version + ')' : '');
    if (d.mcp_servers) body.push(h('p', { cls: 'muted', text: 'MCP servers: ' + d.mcp_servers.map(function (m) { return m.name + ' ' + m.status; }).join(', ') }));
    if (d.skills) body.push(h('p', { cls: 'muted', text: 'skills: ' + [].concat(d.skills).map(function (x) { return typeof x === 'string' ? x : x.name; }).join(', ') }));
    body.push(fold('tools it had (' + (d.tools || []).length + ')', (d.tools || []).join('\n')));
  } else if (s.kind === 'message') {
    title = 'said';
    body.push(h('div', { cls: 'said', text: d.text || '' }));
  } else if (s.kind === 'thinking') {
    title = 'thought';
    var th = d.text || (d.summary ? [].concat(d.summary).join('\n') : '') || (d.content ? [].concat(d.content).join('\n') : '');
    body.push(h('div', { cls: 'thought', text: th || '(the agent does not share the text of its thinking; this marks when it thought)' }));
  } else if (s.kind === 'tool_call' || s.kind === 'skill') {
    title = (s.kind === 'skill' ? 'used the skill ' + ((d.input || {}).skill || '') : 'called ' + (s.name || '')) +
            (s.source === 'mcp' ? '' : '');
    var input = s.source === 'mcp' ? d.args : d.input;
    body.push(fold('input: ' + short(input, 120), pretty(input), false));
    var res = s.source === 'mcp' ? (d.error ? { error: d.error } : d.result) : (results[d.id] || {}).data;
    if (res !== undefined) {
      var bad = s.source === 'mcp' ? !!d.error : !!(res && res.is_error);
      var content = s.source === 'mcp' ? res : res.content;
      var f = fold((bad ? 'refused / failed: ' : 'result: ') + short(content, 140), pretty(content), false);
      if (bad) f.classList.add('bad');
      body.push(f);
    }
  } else if (s.kind === 'tool_result') {
    title = 'a tool result';
    body.push(fold(short(d.content, 140), pretty(d.content)));
  } else if (s.kind === 'item') {
    title = codexTitle(s.name, d);
    if (d.arguments !== undefined) body.push(fold('input: ' + short(d.arguments, 120), pretty(d.arguments)));
    if (d.result !== undefined || d.error) body.push(fold((d.error ? 'failed: ' : 'result: ') + short(d.error || d.result, 140), pretty(d.error || d.result)));
    if (d.aggregatedOutput) body.push(fold('output', d.aggregatedOutput));
    body.push(fold('the whole item', pretty(d)));
  } else if (s.kind === 'page_action') {
    title = 'on the page: ' + s.name + (d.ok ? '' : d.answered ? ' (refused)' : ' (no answer)');
    body.push(h('p', { cls: 'muted', text: d.page || '' }));
    body.push(fold('what it asked: ' + short(d.args, 120), pretty(d.args)));
    body.push(fold((d.ok ? 'what the page answered: ' : 'why not: ') + short(d.ok ? d.result : d.error, 140), pretty(d.ok ? d.result : d.error)));
  } else if (s.kind === 'turn') {
    title = 'the turn ended: ' + (s.name || '');
    var bits = [];
    if (d.duration_ms !== undefined) bits.push(secs(d.duration_ms / 1000));
    if (d.num_turns !== undefined) bits.push(d.num_turns + ' model turns');
    if (d.total_cost_usd !== undefined) bits.push('$' + Number(d.total_cost_usd).toFixed(3));
    if (d.error) bits.push('error: ' + short(d.error, 300));
    body.push(h('p', { cls: 'muted', text: bits.join(' · ') }));
    if (d.usage) body.push(fold('tokens', pretty(d.usage)));
  } else if (s.kind === 'error') {
    title = 'failed: ' + (s.name || '');
    body.push(h('pre', { cls: 'bad', text: pretty(d) }));
  } else {
    body.push(fold('data', pretty(d)));
  }
  var el = h('article', { cls: 'step k-' + s.kind + ' s-' + s.source, 'data-n': s.n }, [
    h('div', { cls: 'st-h' }, [
      h('span', { cls: 'st-t', text: '+' + (s.at - t0).toFixed(1) + ' s' }),
      h('span', { cls: 'st-src', text: SOURCE[s.source] || s.source }),
      h('b', { text: title }),
      s.ms !== undefined && s.kind !== 'turn' ? h('span', { cls: 'muted', text: ' ' + (s.ms >= 1000 ? (s.ms / 1000).toFixed(1) + ' s' : Math.round(s.ms) + ' ms') }) : null
    ])
  ].concat(body));
  el._step = s;
  return el;
}
function codexTitle(type, d) {
  if (type === 'mcpToolCall') return 'called ' + (d.server ? d.server + '.' : '') + (d.tool || '') + (d.status ? ' (' + d.status + ')' : '');
  if (type === 'commandExecution') return 'ran a command: ' + short(d.command, 100);
  if (type === 'webSearch') return 'searched the web: ' + short(d.query, 100);
  if (type === 'fileChange') return 'changed files';
  return type || 'an item';
}

// ------------------------------------------------------------------ replay
function stop() {
  if (S.timer) clearTimeout(S.timer);
  S.timer = null;
  var b = document.getElementById('tr-play');
  if (b) b.textContent = '▶ Replay';
  document.body.classList.remove('replaying');
  Array.prototype.forEach.call(document.querySelectorAll('.step'), function (e) { e.classList.remove('hidden', 'now'); });
}
function play() {
  if (S.timer) { stop(); return; }
  var els = Array.prototype.slice.call(document.querySelectorAll('.step'));
  if (!els.length) return;
  document.body.classList.add('replaying');
  els.forEach(function (e) { e.classList.add('hidden'); e.classList.remove('now'); });
  document.getElementById('tr-play').textContent = '❚❚ Stop';
  var i = 0;
  (function next() {
    if (i > 0) els[i - 1].classList.remove('now');
    if (i >= els.length) { S.timer = null; document.getElementById('tr-play').textContent = '▶ Replay'; document.body.classList.remove('replaying'); return; }
    var e = els[i];
    e.classList.remove('hidden'); e.classList.add('now');
    e.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    document.getElementById('tr-prog').textContent = (i + 1) + ' / ' + els.length;
    var gap = i + 1 < els.length ? els[i + 1]._step.at - e._step.at : 0;
    var ms = S.speed === 0 ? 900 : Math.min(gap * 1000 / S.speed, 8000);
    i++;
    S.timer = setTimeout(next, Math.max(ms, 120));
  })();
}

// ------------------------------------------------------------------ the page
function build() {
  var speeds = [[1, 'real time'], [4, '4×'], [16, '16×'], [0, 'one step a second']];
  document.body.appendChild(h('div', { id: 'tr' }, [
    h('aside', { id: 'tr-side' }, [
      h('div', { cls: 'tr-brand' }, [h('b', { text: 'Agent traces' }), h('a', { href: '/studio', text: 'Studio' })]),
      h('p', { cls: 'muted small', text: 'What each agent did for each request: the text it was handed, its thinking, every tool, MCP and Skill call with the result, and every action on a page.' }),
      h('div', { id: 'tr-list' })
    ]),
    h('main', { id: 'tr-main' }, [
      h('div', { id: 'tr-bar' }, [
        h('button', { id: 'tr-play', cls: 'pri', text: '▶ Replay', on: { click: play } }),
        h('select', { id: 'tr-speed', title: 'replay speed', on: { change: function (e) { S.speed = +e.target.value; } } },
          speeds.map(function (s) { var o = h('option', { value: s[0], text: s[1] }); if (s[0] === S.speed) o.selected = true; return o; })),
        h('span', { id: 'tr-prog', cls: 'muted' }),
        h('span', { cls: 'sp' }),
        toggle('thinking', 'Thinking'), toggle('page', 'Page actions'), toggle('mcp', 'MCP server copies'),
        h('a', { id: 'tr-json', href: '#', target: '_blank', rel: 'noopener', text: 'JSON' })
      ]),
      h('div', { id: 'tr-head' }),
      h('div', { id: 'tr-steps' })
    ])
  ]));
}
// a checked box shows those steps
function toggle(key, label) {
  var cb = h('input', { type: 'checkbox', on: { change: function (e) { stop(); S.hide[key] = !e.target.checked; render(); } } });
  cb.checked = !S.hide[key];
  return h('label', { cls: 'tg', title: key === 'mcp' ? 'the QCCD MCP server\'s own record of each call (shown anyway when the agent\'s stream has none, e.g. a terminal agent)' : '' }, [cb, ' ' + label]);
}
build();
// `qccd trace --open` pairs this browser with a one-time code, as `qccd studio` does
var pair = /[#&]pair=([A-Za-z0-9_-]+)/.exec(location.hash || '');
if (pair) {
  history.replaceState(null, '', location.pathname + location.search);
  fetch('/api/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
                       body: JSON.stringify({ code: pair[1] }) }).then(loadIndex, loadIndex);
} else loadIndex();
})();
