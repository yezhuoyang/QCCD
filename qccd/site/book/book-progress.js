// The book's progress: which exercises a reader has solved, kept in this browser and, when the
// reader is signed in, on their account -- the same /api/progress the course uses, under the
// book's own namespaced ids ("book:V2.1.ex4"), which the course's sync never reads back.
//
// The rules are the course's (qccd/site/progress.py, SYNC_JS): the account only ever gains;
// what this browser holds beyond the account goes up; the account's comes down.  One addition:
// the browser's book progress remembers whose it is (`owner`), so when a different reader signs
// in on this browser their account is shown, and the previous reader's exercises are not
// pushed onto it -- even if the course's sync updated the shared owner key first.
(function () {
  'use strict';
  if (typeof window === 'undefined' || window.QCCDBOOK) return;
  // The book's store keeps its OWN owner.  It never writes the course's 'qccd.course.account':
  // the course's sync trusts that key to decide whether this browser's course stars are the
  // signed-in reader's, so writing it here let a second reader inherit the first's course stars.
  var KEY = 'qccd.book.progress';
  var ID = /^book:[A-Za-z0-9._-]{1,43}$/;
  var live = /^https?:$/.test(location.protocol) && !!window.fetch;
  try { if (window.top !== window.self) live = false; } catch (e) { live = false; }
  var S = { status: live ? 'loading' : 'local', user: null }, subs = [], busy = false, again = false;

  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function load() {
    var p = null;
    try { p = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { p = null; }
    if (!p || typeof p !== 'object') p = { v: 1 };
    if (!p.done || typeof p.done !== 'object') p.done = {};
    return p;
  }
  function save(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) { /* private window */ } }
  function state() { var p = load(); return { status: S.status, user: S.user, done: p.done }; }
  function emit() { var s = state(); subs.forEach(function (f) { try { f(s); } catch (e) { /* a page's own bug */ } }); }
  function api(method, body) {
    var o = { method: method, credentials: 'same-origin', headers: {} };
    if (body) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch('/api/progress', o).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) { var e = new Error((d && d.error) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
        return d;
      });
    });
  }
  function upload(ids) {
    var chunks = [];
    ids = ids.slice();
    while (ids.length) chunks.push(ids.splice(0, 90));        // the server takes 100 per call
    return chunks.reduce(function (pr, ch) {
      var stars = {};
      ch.forEach(function (i) { stars[i] = 1; });
      return pr.then(function () { return api('POST', { stars: stars }); });
    }, Promise.resolve());
  }
  function sync() {
    if (!live) { S.status = 'local'; emit(); return Promise.resolve(); }
    if (busy) { again = true; return Promise.resolve(); }
    busy = true;
    return api('GET').then(function (d) {
      if (!d || !d.user) { S.user = null; S.status = 'anon'; return; }
      S.user = d.user;
      var p = load(), server = {}, st = (d.progress && d.progress.stars) || {}, k;
      for (k in st) if (has(st, k) && ID.test(k) && st[k] > 0) server[k] = 1;
      if (p.owner != null && String(p.owner) !== String(d.user.id)) p = { v: 1, done: {} };   // another reader's
      p.owner = d.user.id;
      var up = [];
      for (k in p.done) if (has(p.done, k) && ID.test(k) && !server[k]) up.push(k);
      for (k in server) p.done[k] = 1;
      save(p);
      return upload(up).then(function () { S.status = 'synced'; });
    }).catch(function (e) { S.status = (e && e.status) ? 'error' : 'offline'; })
      .then(function () { busy = false; emit(); if (again) { again = false; sync(); } });
  }
  // an exercise solved on this page: this browser, and the account when there is one
  function mark(id) {
    if (!ID.test(id)) return;
    var p = load();
    if (p.done[id]) return;
    p.done[id] = 1;
    if (S.user) p.owner = S.user.id;
    save(p);
    emit();
    if (S.user && S.status === 'synced') upload([id]).catch(function () { S.status = 'error'; emit(); });
  }
  // a sign-in or sign-out through the site bar: its comment layer knows who is signed in
  function tick() {
    if (!live || busy) return;
    var me;
    try {
      var q = window.QCCOMMENTS && QCCOMMENTS.state();
      if (q && q.me) me = q.me.id; else if (q && document.getElementById('qc-signin')) me = null;
    } catch (e) { /* no comment layer */ }
    if (me !== undefined && me !== (S.user ? S.user.id : null)) sync();
  }
  window.QCCDBOOK = { state: state, sync: sync, mark: mark, subscribe: function (f) { subs.push(f); } };
  function start() {
    sync();
    if (!live) return;
    setInterval(tick, 2000);
    window.addEventListener('storage', function (ev) { if (!ev || ev.key === KEY) emit(); });
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') sync(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
