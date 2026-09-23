// qccd/workspace/web/pageact.js -- what an agent may read and do on a page.
//
// Injected into the workspace's own Studio page and into every page of the local website
// mirror (qccd/workspace/mirror.py).  It defines `window.QCCD_PAGE`, and it never talks to
// the network: the chat layer (cowork.js) carries each request from the agent to here and
// the answer back.
//
// The agent gets a FIXED set of actions, never script execution:
//   read        the page's title, headings, text (by section, paged), its controls, its
//               embedded examples, the reader's text selection, the studio's transport
//   scroll      bring an element into view
//   highlight   outline an element, with an optional note beside it
//   click       press a button, link, tab, checkbox or summary
//   fill        type into a text field or pick a select option
//   press       one key (Enter, Escape, arrows, Tab, Space) on an element or the page
//   navigate    go to another page of the same site (never off it)
//   step        a studio animation: seek a step, step forward or back, play, pause
//   open_lesson open a lesson of the course (a studio page)
// A target is {ref} (from the last read), {selector} (CSS), or {text} (visible text), plus
// {frame: 'fN'} to reach into an embedded example.  The chat, its menus and its dialogs are
// out of reach: an agent must not press the person's own Send, Approve or Undo.

(function () {
'use strict';
if (window.QCCD_PAGE) return;

var PRIVATE = '#qcl-dock, #qcl-menu, .qcl-modal, #qcl-notice, #qcl-unpaired, #qccd-chat, [data-qccd-private]';
var CONTROL = 'a[href], button, input, select, textarea, summary, [role="button"], [role="tab"], [role="link"], ' +
              '[role="checkbox"], [role="menuitem"], [role="option"], [onclick]';
var MAX_TEXT = 6000, MAX_CONTROLS = 160;
var seq = {};                     // one counter per kind: s1.. sections, c1.. controls, f1.. examples

function vis(el) {
  if (!el || !el.getBoundingClientRect) return false;
  var r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return false;
  var cs = el.ownerDocument.defaultView.getComputedStyle(el);
  return cs.visibility !== 'hidden' && cs.display !== 'none';
}
function isPrivate(el) { return !!(el && el.closest && el.closest(PRIVATE)); }
function clip(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function ref(el, p) {
  var r = el.getAttribute('data-qccd-ref');
  if (!r) { seq[p] = (seq[p] || 0) + 1; r = p + seq[p]; el.setAttribute('data-qccd-ref', r); }
  return r;
}
function label(el) {
  var t = el.getAttribute('aria-label') || '';
  if (!t && el.id) {
    var lb = el.ownerDocument.querySelector('label[for="' + cssEsc(el.id) + '"]');
    if (lb) t = lb.textContent;
  }
  if (!t && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) t = el.getAttribute('placeholder') || el.name || '';
  // what is SHOWN (innerText leaves out a hidden hover card inside a button), first line first
  if (!t) t = (el.innerText !== undefined ? el.innerText : el.textContent || '').split('\n').filter(function (x) {
    return x.trim(); }).slice(0, 2).join(' · ') || el.getAttribute('title') || el.getAttribute('value') || '';
  if (!t && el.tagName === 'SELECT') t = el.name || el.id;
  return clip(t || el.getAttribute('data-hint') || el.id || el.tagName.toLowerCase(), 90);
}
function cssEsc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'); }
function kindOf(el) {
  var t = el.tagName.toLowerCase(), role = el.getAttribute('role');
  if (t === 'a') return 'link';
  if (t === 'input') return (el.type || 'text') === 'checkbox' ? 'checkbox' : (el.type === 'radio' ? 'radio' :
                            (el.type === 'range' ? 'slider' : (el.type === 'button' || el.type === 'submit' ? 'button' : 'input')));
  if (t === 'textarea') return 'input';
  if (t === 'select') return 'select';
  if (t === 'summary') return 'disclosure';
  return role || (t === 'button' ? 'button' : 'control');
}
function sectionOf(el, heads) {
  var best = null;
  for (var i = 0; i < heads.length; i++) {
    if (heads[i].el.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) best = heads[i];
    else break;
  }
  return best ? best.ref : null;
}

// ------------------------------------------------------------------ the page's kind and app state
function pageKind(doc, win) {
  var p = (win.location.pathname || '').replace(/^\/web/, '');
  if (win.EDITOR && typeof win.seek === 'function') {
    if (/studio(\.html)?$/.test(p) || p === '/studio') return 'studio';
    if (/^\/board\//.test(p)) return 'leaderboard entry (a studio page)';
    return 'studio page';
  }
  if (/^\/?$/.test(p) || /index\.html$/.test(p) && p.split('/').length <= 2) return 'home';
  var first = p.split('/').filter(Boolean)[0] || '';
  return ({ learn: 'course index', board: 'leaderboard', rules: 'rules', language: 'language', physics: 'physics',
            compilation: 'compilation', docs: 'reference document', people: 'people', publications: 'publications',
            discuss: 'discussion', gadgets: 'logical gadgets' })[first] || 'page';
}
function transport(win, doc) {
  if (typeof win.seek !== 'function') return null;
  var sl = doc.getElementById('slider'), play = doc.getElementById('play');
  var out = { step: sl ? +sl.value : null, steps: sl ? +sl.max + 1 : null,
              playing: !!(play && /pause/i.test(play.textContent || '')) };
  var row = doc.querySelector('#plist .on, #plist .cur, .prow.on, .prow.cur');
  if (row) out.current = clip(row.textContent, 160);
  return out;
}
function appState(win, doc) {
  var ed = win.EDITOR;
  if (!ed) return null;
  var out = { transport: transport(win, doc) };
  try {
    if (ed.lessonList) {
      var ls = ed.lessonList() || [];
      if (ls.length) out.lessons = ls.map(function (l) { return { id: l.id, part: l.part, title: l.title }; });
    }
    if (ed.lessonState) { var st = ed.lessonState(); if (st && st.id) out.lesson = { id: st.id, stage: st.stage, passed: st.passed }; }
  } catch (e) { /* a page without the course */ }
  try { if (ed.state) { var s = ed.state(), d = s && s.device; if (d) out.device = { name: d.name || null,
    nodes: Object.keys(d.nodes || {}).length, segments: Object.keys(d.segments || {}).length }; } } catch (e) { /* none */ }
  return out;
}

// ------------------------------------------------------------------ read
function headings(doc) {
  var out = [];
  doc.querySelectorAll('h1, h2, h3, h4').forEach(function (el) {
    if (isPrivate(el) || !vis(el)) return;
    out.push({ el: el, ref: ref(el, 's'), level: +el.tagName[1], text: clip(el.textContent, 140) });
  });
  return out;
}
function mainRoot(doc) {
  return doc.querySelector('main') || doc.querySelector('article') || doc.body;
}
function textOf(root) {
  // the visible words, one block per line, without the chat or the scripts
  var out = [], skip = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, SVG: 1, TEMPLATE: 1, IFRAME: 1, CANVAS: 1 };
  var block = /^(P|DIV|LI|H[1-6]|TR|PRE|BLOCKQUOTE|SECTION|ARTICLE|TABLE|UL|OL|DT|DD|FIGCAPTION|SUMMARY|DETAILS)$/;
  (function walk(n) {
    if (n.nodeType === 3) { var t = n.nodeValue.replace(/\s+/g, ' '); if (t.trim()) out.push(t); return; }
    if (n.nodeType !== 1 || skip[n.tagName.toUpperCase()] || isPrivate(n)) return;
    if (n.getAttribute('aria-hidden') === 'true') return;
    var cs = n.ownerDocument.defaultView.getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (/^H[1-6]$/.test(n.tagName)) out.push('\n' + '#'.repeat(+n.tagName[1]) + ' ');
    else if (n.tagName === 'TD' || n.tagName === 'TH') out.push(' | ');
    for (var c = n.firstChild; c; c = c.nextSibling) walk(c);
    if (block.test(n.tagName)) out.push('\n');
  })(root);
  return out.join('').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}
function sectionRange(doc, want, heads) {
  // the text from a heading to the next heading of the same or a higher level
  var w = String(want).toLowerCase(), h = null, i;
  for (i = 0; i < heads.length; i++) if (heads[i].ref === want || heads[i].el.id === want) { h = heads[i]; break; }
  if (!h) for (i = 0; i < heads.length; i++) if (heads[i].text.toLowerCase().indexOf(w) >= 0) { h = heads[i]; break; }
  if (!h) return null;
  var end = null;
  for (var j = heads.indexOf(h) + 1; j < heads.length; j++) if (heads[j].level <= h.level) { end = heads[j].el; break; }
  var r = doc.createRange();
  r.setStartBefore(h.el);
  if (end) r.setEndBefore(end); else r.setEndAfter(mainRoot(doc).lastChild || mainRoot(doc));
  var box = doc.createElement('div');
  box.appendChild(r.cloneContents());
  box.style.cssText = 'position:absolute;left:-99999px;top:0;width:900px';
  doc.body.appendChild(box);
  var t = textOf(box);
  box.remove();
  return { heading: h, text: t };
}
function controls(doc, heads) {
  // the ones on screen first, then the rest in page order, up to MAX_CONTROLS
  var near = [], far = [], vh = doc.defaultView.innerHeight, vw = doc.defaultView.innerWidth;
  doc.querySelectorAll(CONTROL).forEach(function (el) {
    if (isPrivate(el) || !vis(el) || (el.tagName === 'INPUT' && el.type === 'hidden')) return;
    var r = el.getBoundingClientRect();
    (r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw ? near : far).push(el);
  });
  var all = near.concat(far), out = [];
  all.slice(0, MAX_CONTROLS).forEach(function (el) {
    var c = { ref: ref(el, 'c'), kind: kindOf(el), label: label(el) };
    if (c.kind === 'link') { var hr = el.getAttribute('href') || ''; c.href = hr.length > 120 ? hr.slice(0, 120) + '…' : hr; }
    if (c.kind === 'input' || c.kind === 'slider') c.value = clip(el.value, 80);
    if (c.kind === 'select') { c.value = el.value; c.options = Array.prototype.slice.call(el.options, 0, 20).map(function (o) { return clip(o.textContent, 40); }); }
    if (c.kind === 'checkbox' || c.kind === 'radio') c.checked = !!el.checked;
    if (el.disabled) c.disabled = true;
    var sec = sectionOf(el, heads);
    if (sec) c.section = sec;
    out.push(c);
  });
  out.total = all.length;
  return out;
}
function frames(doc) {
  var out = [];
  doc.querySelectorAll('iframe').forEach(function (f) {
    if (isPrivate(f)) return;
    var info = { ref: ref(f, 'f'), src: clip(f.getAttribute('src') || '', 120) };
    var cap = f.closest('figure, .ex, .example, section, div');
    var fc = cap && cap.querySelector('figcaption, .cap, h3, h4, b');
    if (fc) info.caption = clip(fc.textContent, 120);
    try {
      var w = f.contentWindow, d = f.contentDocument;
      if (w && d) { var t = transport(w, d); if (t) info.transport = t; info.title = clip(d.title, 80); }
    } catch (e) { info.cross_origin = true; }
    out.push(info);
  });
  return out;
}
function selectionText(win) {
  try { return clip(String(win.getSelection ? win.getSelection() : ''), 1200); } catch (e) { return ''; }
}
function visibleSection(doc, heads) {
  var cur = null;
  for (var i = 0; i < heads.length; i++) {
    if (heads[i].el.getBoundingClientRect().top < 160) cur = heads[i]; else break;
  }
  return cur ? { ref: cur.ref, text: cur.text } : null;
}
function read(args) {
  args = args || {};
  var doc = document, win = window, heads = headings(doc);
  var out = { url: location.pathname + location.search + location.hash, title: doc.title, kind: pageKind(doc, win),
              headings: heads.slice(0, 120).map(function (x) { return { ref: x.ref, level: x.level, text: x.text }; }),
              selection: selectionText(win) || undefined, in_view: visibleSection(doc, heads) || undefined,
              scroll: { y: Math.round(win.scrollY), height: Math.round(doc.documentElement.scrollHeight) } };
  var text, lim = Math.max(500, Math.min(+args.max_chars || MAX_TEXT, 20000)), off = Math.max(0, +args.offset || 0);
  if (args.section) {
    var s = sectionRange(doc, args.section, heads);
    if (!s) return { error: 'no section matches ' + JSON.stringify(args.section) + '; the headings are listed', headings: out.headings };
    out.section = { ref: s.heading.ref, text: s.heading.text };
    text = s.text;
  } else text = textOf(mainRoot(doc));
  out.text = text.slice(off, off + lim);
  out.text_range = [off, Math.min(text.length, off + lim), text.length];
  if (off + lim < text.length) out.more = 'call read again with offset=' + (off + lim) + ' (or a section) for the rest';
  if (args.controls !== false) {
    out.controls = controls(doc, heads);
    if (out.controls.total > out.controls.length)
      out.controls_note = out.controls.length + ' of ' + out.controls.total + ' controls, the ones on screen first; ' +
                          'target any control by its text or a selector, or scroll and read again';
  }
  var fr = frames(doc);
  if (fr.length) out.embedded = fr;
  var app = appState(win, doc);
  if (app) out.app = app;
  return out;
}

// ------------------------------------------------------------------ targets
function resolve(t) {
  t = t || {};
  var doc = document, win = window, f = null;
  if (t.frame) {
    f = doc.querySelector('[data-qccd-ref="' + cssEsc(t.frame) + '"]') || (function () {
      try { return doc.querySelector(t.frame); } catch (e) { return null; } })();
    if (!f || f.tagName !== 'IFRAME') throw new Error('no embedded example ' + t.frame + ' (read lists them under embedded)');
    try { doc = f.contentDocument; win = f.contentWindow; } catch (e) { throw new Error('that example is not readable here'); }
    if (!doc) throw new Error('that example has not loaded yet');
    if (!t.ref && !t.selector && !t.text) return { el: f, doc: doc, win: win, frame: f };
  }
  var el = null;
  if (t.ref) el = doc.querySelector('[data-qccd-ref="' + cssEsc(t.ref) + '"]') || doc.getElementById(t.ref);
  else if (t.selector) { try { el = doc.querySelector(String(t.selector)); } catch (e) { throw new Error('bad selector: ' + e.message); } }
  else if (t.text) {
    var want = String(t.text).trim().toLowerCase(), best = null, bestScore = 1e9;
    doc.querySelectorAll(CONTROL + ', h1, h2, h3, h4, p, li, td, th, figcaption, label').forEach(function (c) {
      if (isPrivate(c) || !vis(c)) return;
      var s = label(c).toLowerCase(), full = clip(c.textContent, 400).toLowerCase();
      var score = s === want ? 0 : (s.indexOf(want) >= 0 ? 1 + s.length / 1000 : (full.indexOf(want) >= 0 ? 2 + full.length / 1000 : 1e9));
      if (c.matches(CONTROL)) score -= 0.5;
      if (score < bestScore) { bestScore = score; best = c; }
    });
    el = best;
  } else throw new Error('a target needs ref, selector or text');
  if (!el) throw new Error('nothing on the page matches ' + JSON.stringify(t));
  if (isPrivate(el)) throw new Error('that is part of the chat; the agent cannot operate it');
  return { el: el, doc: doc, win: win, frame: f };
}
function describe(el) {
  return { ref: el.getAttribute && el.getAttribute('data-qccd-ref') || ref(el, 'e'), tag: el.tagName.toLowerCase(),
           label: label(el) };
}

// ------------------------------------------------------------------ highlight
var HL = null;
function unhighlight() { if (HL) { HL.forEach(function (n) { n.remove(); }); HL = null; } }
function highlight(el, note, frameEl) {
  unhighlight();
  // inside an embedded example: the example comes to the middle at once.  'instant', because
  // the site's CSS makes scrolling smooth, and any second scroll (inside the example) would
  // cancel a smooth one half way
  if (frameEl) frameEl.scrollIntoView({ block: 'center', behavior: 'instant' });
  else el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
  function place() {
    var r = el.getBoundingClientRect(), off = frameEl ? frameEl.getBoundingClientRect() : { left: 0, top: 0 };
    var box = HL && HL[0], tip = HL && HL[1];
    if (!box) return;
    box.style.left = (off.left + r.left - 4) + 'px'; box.style.top = (off.top + r.top - 4) + 'px';
    box.style.width = (r.width + 8) + 'px'; box.style.height = (r.height + 8) + 'px';
    if (tip) { tip.style.left = Math.max(8, Math.min(window.innerWidth - 330, off.left + r.left)) + 'px';
               tip.style.top = (off.top + r.bottom + 10 > window.innerHeight - 80 ? off.top + r.top - 10 - tip.offsetHeight : off.top + r.bottom + 10) + 'px'; }
  }
  var box = document.createElement('div');
  box.setAttribute('data-qccd-private', '');
  box.style.cssText = 'position:fixed;z-index:2147483000;pointer-events:none;border:3px solid #e8890c;border-radius:6px;' +
                      'box-shadow:0 0 0 4000px rgba(20,18,30,.10),0 0 14px rgba(232,137,12,.7);transition:all .25s';
  HL = [box];
  document.body.appendChild(box);
  if (note) {
    var tip = document.createElement('div');
    tip.setAttribute('data-qccd-private', '');
    tip.textContent = String(note).slice(0, 400);
    tip.style.cssText = 'position:fixed;z-index:2147483001;max-width:320px;background:#1d1d1b;color:#fff;padding:8px 11px;' +
                        'border-radius:8px;font:13px/1.45 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25);pointer-events:none';
    HL.push(tip);
    document.body.appendChild(tip);
  }
  var n = 0, iv = setInterval(function () { if (!HL || HL[0] !== box) { clearInterval(iv); return; } place(); if (++n > 40) clearInterval(iv); }, 100);
  place();
  setTimeout(function () { if (HL && HL[0] === box) unhighlight(); }, 9000);
}

// ------------------------------------------------------------------ actions
var KEYS = { Enter: 13, Escape: 27, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Tab: 9, ' ': 32, Space: 32,
             Home: 36, End: 35, PageUp: 33, PageDown: 34 };
function sitePath(href) {
  // the one place navigation may go: this site, on this origin, under the mirror's /web/
  // (or the Studio's own pages when this IS the Studio)
  var u;
  try { u = new URL(href, location.href); } catch (e) { return null; }
  if (u.origin !== location.origin) return null;
  if (window.QCCD_MIRROR && u.pathname.indexOf('/web/') !== 0 && u.pathname !== '/web') return null;
  return u;
}
function setValue(el, v) {
  var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : (el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype);
  var d = Object.getOwnPropertyDescriptor(proto, 'value');
  if (d && d.set) d.set.call(el, v); else el.value = v;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}
function act(action, args) {
  args = args || {};
  var r, el;
  switch (action) {
    case 'read': return read(args);
    case 'scroll':
      if (args.to === 'top') { window.scrollTo({ top: 0, behavior: 'smooth' }); return { ok: true }; }
      if (args.to === 'bottom') { window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' }); return { ok: true }; }
      r = resolve(args.target);
      (r.frame || r.el).scrollIntoView({ block: 'center', behavior: 'smooth' });
      return { ok: true, element: describe(r.el) };
    case 'highlight':
      r = resolve(args.target);
      highlight(r.el, args.note, r.frame && r.el !== r.frame ? r.frame : null);
      return { ok: true, element: describe(r.el), note: args.note ? 'shown beside it for 9 s' : undefined };
    case 'click':
      r = resolve(args.target); el = r.el;
      if (el.tagName === 'A') {
        var href = el.getAttribute('href') || '';
        if (href && href[0] !== '#' && !/^javascript:/i.test(href)) {
          var u = sitePath(el.href);
          if (!u) throw new Error('that link leaves the site (' + clip(href, 100) + '); give the person the link instead');
          if (el.target === '_blank') { location.assign(u.href); return { ok: true, navigating: u.pathname + u.search + u.hash }; }
        }
      }
      if (el.tagName === 'INPUT' && el.type === 'file') throw new Error('file inputs are the person\'s to use');
      if (el.disabled) throw new Error('that control is disabled right now');
      (r.frame || el).scrollIntoView({ block: 'center', behavior: 'instant' });
      if (typeof el.click === 'function') el.click();
      else el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: r.win }));
      return { ok: true, element: describe(el) };
    case 'fill':
      r = resolve(args.target); el = r.el;
      if (el.tagName === 'SELECT') {
        var want = String(args.value), opt = null;
        Array.prototype.forEach.call(el.options, function (o) { if (!opt && (o.value === want || o.textContent.trim() === want)) opt = o; });
        if (!opt) throw new Error('no option ' + JSON.stringify(want) + ' (options: ' + Array.prototype.map.call(el.options, function (o) { return o.textContent.trim(); }).slice(0, 20).join(', ') + ')');
        setValue(el, opt.value);
        return { ok: true, element: describe(el), value: el.value };
      }
      if (el.tagName !== 'TEXTAREA' && !(el.tagName === 'INPUT' && !/^(password|file|hidden|checkbox|radio|button|submit|image|reset)$/.test(el.type)))
        throw new Error('fill works on text fields, sliders and selects; this is a ' + el.tagName.toLowerCase());
      el.focus();
      setValue(el, String(args.value === undefined ? '' : args.value));
      return { ok: true, element: describe(el), value: el.value };
    case 'press':
      var key = String(args.key || '');
      if (!KEYS[key]) throw new Error('press takes one of: ' + Object.keys(KEYS).join(', '));
      if (key === 'Space') key = ' ';
      el = args.target ? resolve(args.target).el : (document.activeElement && !isPrivate(document.activeElement) ? document.activeElement : document.body);
      ['keydown', 'keyup'].forEach(function (t) {
        el.dispatchEvent(new KeyboardEvent(t, { key: key, code: key === ' ' ? 'Space' : key, keyCode: KEYS[key], which: KEYS[key], bubbles: true, cancelable: true }));
      });
      return { ok: true, key: key, element: describe(el) };
    case 'navigate':
      var p = sitePath(String(args.path || ''));
      if (!p) throw new Error('navigate stays on this site: give a path like ../rules/ or /web/rules/');
      setTimeout(function () { location.assign(p.href); }, 50);
      return { ok: true, navigating: p.pathname + p.search + p.hash };
    case 'step':
      var win = window, doc = document;
      if (args.target || args.frame) { r = resolve(args.target || { frame: args.frame }); win = r.win; doc = r.doc;
        if (r.frame) r.frame.scrollIntoView({ block: 'center', behavior: 'instant' }); }
      if (typeof win.seek !== 'function') throw new Error('this page has no animation to step');
      var sl = doc.getElementById('slider'), play = doc.getElementById('play');
      var cur = sl ? +sl.value : 0, last = sl ? +sl.max : 0;
      if (args.play) { if (play && !/pause/i.test(play.textContent)) play.click(); }
      else if (args.pause) { if (play && /pause/i.test(play.textContent)) play.click(); }
      else if (args.frame_index !== undefined || args.step !== undefined) win.seek(Math.max(0, Math.min(last, +(args.step !== undefined ? args.step : args.frame_index))), {});
      else win.seek(Math.max(0, Math.min(last, cur + (args.delta === undefined ? 1 : +args.delta))), {});
      return { ok: true, transport: transport(win, doc) };
    case 'open_lesson':
      var ed = window.EDITOR;
      if (!ed || !ed.lessonLoad) throw new Error('lessons open on the Studio page (studio.html); navigate there first');
      var id = String(args.lesson || args.id || '');
      var known = (ed.lessonList ? ed.lessonList() : []).map(function (l) { return l.id; });
      if (known.length && known.indexOf(id) < 0) throw new Error('no lesson ' + id + '; the lessons are ' + known.join(', '));
      ed.lessonLoad(id);
      try { var dk = document.getElementById('dock'); if (dk && typeof window.foldPanel === 'function') window.foldPanel(dk, false);
            if (typeof window.setPane === 'function') window.setPane('L'); } catch (e) { /* the page lays out itself */ }
      return { ok: true, lesson: ed.lessonState ? ed.lessonState() : { id: id } };
    default:
      throw new Error('unknown action ' + JSON.stringify(action) + ': read, scroll, highlight, click, fill, press, navigate, step, open_lesson');
  }
}
function safe(action, args) {
  try {
    var out = act(action, args);
    if (out && out.error) return { ok: false, error: out.error, detail: out };
    var s = JSON.stringify(out);
    if (s.length > 120000) return { ok: true, result: { truncated: true, note: 'the answer was too large; read a section or use offset' } };
    return { ok: true, result: out };
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
}

// ------------------------------------------------------------------ what goes with a message
function context() {
  var heads = headings(document);
  return { url: location.pathname + location.search + location.hash, title: document.title,
           kind: pageKind(document, window), selection: selectionText(window) || undefined,
           in_view: (visibleSection(document, heads) || {}).text };
}
// the person points at something: outline under the pointer, a click picks it, Esc cancels
function pick() {
  return new Promise(function (resolveP) {
    var box = document.createElement('div');
    box.setAttribute('data-qccd-private', '');
    box.style.cssText = 'position:fixed;z-index:2147483000;pointer-events:none;border:2px solid #2a78d6;border-radius:4px;background:rgba(42,120,214,.08)';
    document.body.appendChild(box);
    var cur = null;
    function over(e) {
      var el = e.target;
      if (!el || isPrivate(el) || el === box) return;
      cur = el;
      var r = el.getBoundingClientRect();
      box.style.left = r.left - 2 + 'px'; box.style.top = r.top - 2 + 'px'; box.style.width = r.width + 4 + 'px'; box.style.height = r.height + 4 + 'px';
    }
    function done(v) {
      document.removeEventListener('mouseover', over, true); document.removeEventListener('click', click, true);
      document.removeEventListener('keydown', key, true); box.remove(); resolveP(v);
    }
    function click(e) {
      if (isPrivate(e.target)) return;
      e.preventDefault(); e.stopPropagation();
      var el = cur || e.target, heads = headings(document), sec = sectionOf(el, heads), h = null;
      heads.forEach(function (x) { if (x.ref === sec) h = x; });
      done({ ref: ref(el, 'e'), tag: el.tagName.toLowerCase(), text: clip(el.textContent || label(el), 300),
             section: h ? h.text : undefined });
    }
    function key(e) { if (e.key === 'Escape') { e.preventDefault(); done(null); } }
    document.addEventListener('mouseover', over, true);
    document.addEventListener('click', click, true);
    document.addEventListener('keydown', key, true);
  });
}

window.QCCD_PAGE = { read: read, act: safe, context: context, pick: pick, highlight: function (t, note) { return safe('highlight', { target: t, note: note }); } };
})();
