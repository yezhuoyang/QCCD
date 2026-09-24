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
//   studio      one verb of the page's Studio API (EDITOR): design on the canvas, write the
//               program, check a lesson -- the same verbs the course solves its exercises with
//   wait        pause, so a demonstration can be followed
//   comments    the comment threads on this page (the site's own, as the signed-in person sees them)
//   comment     pin a new thread on an element, as the signed-in person, signed "via <agent>";
//   reply       answer in a thread;  resolve  mark a thread addressed (or reopen it)
// Every action is VISIBLE: a cursor with the agent's name glides to the target and says what
// it is about to do, then does it; typing appears letter by letter.
// ONLY WHAT IS DECLARED (qccd/workspace/interface.py, docs/agent-interface.md): click, fill and
// press operate declared controls only; navigate goes to places (the site's index, the links
// on the page, the workspace's pages); studio calls the verbs editor_api.json declares for
// agents.  Anything else is refused with what IS declared -- never guessed at.
// A target is {ref} (from the last read), {selector} (CSS), or {text} (visible text), plus
// {frame: 'fN'} to reach into an embedded example.  The chat, its menus and its dialogs are
// out of reach: an agent must not press the person's own Send, Approve or Undo.

(function () {
'use strict';
if (window.QCCD_PAGE) return;

// the chat, and the site's own comment controls (Sign in is the person's; the agent comments
// through the `comment` action, which signs what it writes)
var PRIVATE = '#qcl-dock, #qcl-menu, .qcl-modal, #qcl-notice, #qcl-unpaired, #qccd-chat, [data-qccd-private], ' +
              '#qcnav, #qc-layer, #qc-menu, [id^="qc-"], ' +
              '#qa-btn, #qa-panel';      // the site's own "connect an agent" panel (hidden here: this IS the agent)
var COMMENT_UI = '#qcnav, #qc-layer, #qc-menu, [id^="qc-"]';
var NOT_SIGNED = 'the person is not signed in to qccd.academy on this page: ask them to press "Sign in" in the ' +
                 'site bar (their own account; the agent never signs in for them)';
function hookOf() {
  var f = window.QCCD_COMMENTS_HOOK;
  if (typeof f !== 'function') throw new Error('this page has no comments (they work on the website pages your workspace serves)');
  return f();
}
function signed(text, who) { return text + '\n\n\u2014 via ' + (who || 'an agent'); }
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
    // what the control IS, in the Studio's own words (its explain layer: the hover card's sentence)
    var dc = declared(el);
    if (!dc) c.undeclared = true;                      // read it, point at it; do not operate it
    else {
      if (dc.hint) c.hint = dc.hint;
      if (dc.what && c.kind !== 'link') c.what = clip(dc.what, 140);
    }
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
  if (el.closest && el.closest(COMMENT_UI))
    throw new Error('that is the site\'s sign-in and comment controls, which are the person\'s: read comments with ' +
                    'the comments action and write with comment, reply, resolve');
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
  box.className = 'qccd-hl';
  box.style.cssText = 'position:fixed;z-index:2147483000;pointer-events:none;border:3px solid #e8890c;border-radius:6px;' +
                      'box-shadow:0 0 0 4000px rgba(20,18,30,.10),0 0 14px rgba(232,137,12,.7);transition:all .25s';
  HL = [box];
  document.body.appendChild(box);
  if (note) {
    var tip = document.createElement('div');
    tip.setAttribute('data-qccd-private', '');
    tip.className = 'qccd-hl-note';
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

// ------------------------------------------------------------------ the agent, visibly
//
// Like a person at the keyboard: before the agent acts on something, a cursor with its name
// glides there and says what it is about to do; typing appears letter by letter.  The person
// always sees where the agent is working.  The cursor rests (dimmed) where it last acted.
var CUR = null, CURXY = null, CURFADE = null;
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function cursor(who) {
  if (!CUR) {
    CUR = document.createElement('div');
    CUR.id = 'qccd-agent-cursor';
    CUR.setAttribute('data-qccd-private', '');
    CUR.setAttribute('aria-hidden', 'true');
    CUR.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483600;pointer-events:none;will-change:transform;' +
                        'transition:transform .5s cubic-bezier(.22,.8,.26,1),opacity .4s;opacity:1';
    CUR.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" style="display:block;filter:drop-shadow(0 2px 3px rgba(0,0,0,.3))">' +
      '<path d="M4 2.5 L4 19.5 L8.6 15.2 L11.8 22 L14.6 20.7 L11.4 14 L17.6 14 Z" fill="#7c3aed" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>' +
      '<span style="position:absolute;left:20px;top:18px;background:#7c3aed;color:#fff;font:600 12px/1.3 system-ui,sans-serif;' +
      'padding:4px 9px;border-radius:11px;white-space:nowrap;max-width:360px;overflow:hidden;text-overflow:ellipsis;' +
      'box-shadow:0 3px 10px rgba(76,29,149,.35)"></span>';
    document.body.appendChild(CUR);
    CURXY = { x: window.innerWidth - 120, y: window.innerHeight - 140 };     // it comes from the chat
    CUR.style.transition = 'none';
    CUR.style.transform = 'translate(' + CURXY.x + 'px,' + CURXY.y + 'px)';
    void CUR.offsetWidth;
    CUR.style.transition = 'transform .5s cubic-bezier(.22,.8,.26,1),opacity .4s';
  }
  return CUR;
}
function say(who, caption) {
  var c = cursor(who);
  c.style.opacity = '1';
  c.querySelector('span').textContent = (who || 'Agent') + (caption ? ': ' + caption : '');
  if (CURFADE) clearTimeout(CURFADE);
  CURFADE = setTimeout(function () { if (CUR) CUR.style.opacity = '.45'; }, 6000);
}
function glideTo(x, y, who, caption) {
  say(who, caption);
  var d = Math.hypot(x - CURXY.x, y - CURXY.y);
  var ms = Math.max(180, Math.min(650, d * 0.9));
  CUR.style.transition = 'transform ' + ms + 'ms cubic-bezier(.22,.8,.26,1),opacity .4s';
  CUR.style.transform = 'translate(' + Math.round(x) + 'px,' + Math.round(y) + 'px)';
  CURXY = { x: x, y: y };
  return sleep(ms + 60);
}
function inView(r) { return r.bottom > 40 && r.top < window.innerHeight - 20 && r.right > 0 && r.left < window.innerWidth; }
// bring an element (maybe inside an embedded example) on screen, then glide to it
function pointAt(el, frameEl, who, caption) {
  var outer = frameEl || el, wait = Promise.resolve();
  if (!inView(outer.getBoundingClientRect())) {
    outer.scrollIntoView({ block: 'center', behavior: frameEl ? 'instant' : 'smooth' });
    wait = sleep(frameEl ? 60 : 480);
  }
  return wait.then(function () {
    var r = el.getBoundingClientRect(), off = frameEl && el !== frameEl ? frameEl.getBoundingClientRect() : { left: 0, top: 0 };
    var x = off.left + r.left + Math.min(r.width / 2, 60), y = off.top + r.top + Math.min(r.height / 2, 18);
    yieldChat({ left: off.left + r.left, top: off.top + r.top, right: off.left + r.right, bottom: off.top + r.bottom });
    return glideTo(x, y, who, caption);
  }).then(function () { ring(el, frameEl); });
}
// the chat sits over the page's corner: when the agent works under it, it turns see-through
// for a while (hovering it brings it back at once)
var YIELD = null;
function yieldChat(r) {
  var c = document.getElementById('qccd-chat');
  if (!c) return;
  var q = c.getBoundingClientRect();
  if (r.right < q.left || r.left > q.right || r.bottom < q.top || r.top > q.bottom) return;
  c.classList.add('qccd-yield');
  if (YIELD) clearTimeout(YIELD);
  YIELD = setTimeout(function () { c.classList.remove('qccd-yield'); }, 9000);
}
function ring(el, frameEl) {
  // a brief outline on what the agent touches
  var r = el.getBoundingClientRect(), off = frameEl && el !== frameEl ? frameEl.getBoundingClientRect() : { left: 0, top: 0 };
  var b = document.createElement('div');
  b.setAttribute('data-qccd-private', '');
  b.className = 'qccd-ring';
  b.style.cssText = 'position:fixed;z-index:2147483500;pointer-events:none;border:2px solid #7c3aed;border-radius:5px;' +
                    'transition:opacity .6s;opacity:1;left:' + (off.left + r.left - 3) + 'px;top:' + (off.top + r.top - 3) +
                    'px;width:' + (r.width + 6) + 'px;height:' + (r.height + 6) + 'px';
  document.body.appendChild(b);
  setTimeout(function () { b.style.opacity = '0'; }, 700);
  setTimeout(function () { b.remove(); }, 1400);
}
function stageOf(doc) {
  return doc.getElementById('svg') || doc.getElementById('canvas') || doc.querySelector('main') || doc.body;
}

// ------------------------------------------------------------------ actions
// ------------------------------------------------------------------ the contract (interface.py)
function iface() { return window.QCCD_INTERFACE || {}; }
function hintOf(key, win) {
  win = win || window;
  try { var E = win.EDITOR; if (E && typeof E.hintFor === 'function') { var h = E.hintFor(key); if (h) return h; } } catch (e) { /* none */ }
  var t = win.QCCD_HINTS;
  return (t && t[key]) || null;
}
// how a control is declared, or null: inline (data-hint the page describes), by construction
// (a link, a fold-out), by the generated form it is in, or out of line (interface.py)
function declared(el) {
  var win = el.ownerDocument.defaultView, key = el.getAttribute('data-hint'), I = iface(), s;
  if (key) { var h = hintOf(key, win); if (h) return { how: 'inline', hint: key, what: h.d || h.t || '' }; }
  // one choice of a described control group (Hardware / Gates / Both): the group's hint declares its options;
  // a screen region's hint (region:...) describes the region, not the controls in it
  var grp = el.parentElement && el.parentElement.closest('[data-hint]');
  if (grp && (el.tagName === 'BUTTON' || /^(tab|option|radio)$/.test(el.getAttribute('role') || ''))) {
    var gk = grp.getAttribute('data-hint'), gh = /^region:/.test(gk) ? null : hintOf(gk, win);
    if (gh) return { how: 'group', hint: gk, what: gh.d || gh.t || '' };
  }
  if (el.tagName === 'A' && el.hasAttribute('href')) return { how: 'link' };
  if (el.tagName === 'SUMMARY') return { how: 'fold-out' };
  for (s in (I.controls || {})) { try { if (el.matches(s)) return { how: 'declared', what: I.controls[s].does }; } catch (e) { /* bad selector */ } }
  for (s in (I.forms || {})) { try { if (el.closest(s)) return { how: 'form', what: I.forms[s] }; } catch (e) { /* bad selector */ } }
  return null;
}
function undeclaredError(el, doc) {
  var near = [];
  doc.querySelectorAll(CONTROL).forEach(function (c) {
    if (near.length < 10 && !isPrivate(c) && vis(c) && c.tagName !== 'A' && declared(c)) near.push('"' + label(c) + '"' + (c.getAttribute('data-hint') ? ' [data-hint=' + c.getAttribute('data-hint') + ']' : ''));
  });
  return new Error('"' + label(el) + '" is not a declared control, so an agent may not operate it (only declared ' +
                   'controls are: docs/agent-interface.md). Declared controls on this page include: ' +
                   (near.join(', ') || 'none besides links') + '. Say to the person what you wanted to do instead.');
}
function mustBeDeclared(el, doc) { if (!declared(el)) throw undeclaredError(el, doc || el.ownerDocument); }
// is a URL a PLACE?  The site's index, a link that is on this page, the workspace's pages
var WS_PLACE = /^\/(studio|trace|runview\/[\w-]+|compare|run\/[\w-]+)\/?$/;
function isPlace(u) {
  var p = u.pathname;
  if (!window.QCCD_MIRROR) return WS_PLACE.test(p);
  if (p === '/studio' || p === '/web/' || p === '/web' || (iface().aliases || []).indexOf(p) >= 0) return true;
  var P = iface().places, bare = p.replace(/index\.html$/, '');
  if (P && (P.indexOf(p) >= 0 || P.indexOf(bare) >= 0)) return true;
  var links = document.querySelectorAll('a[href]');
  for (var i = 0; i < links.length; i++) {
    try { var l = new URL(links[i].getAttribute('href'), location.href); if (l.origin === u.origin && l.pathname === p) return true; } catch (e) { /* none */ }
  }
  return false;
}
function nearestPlaces(p) {
  var P = (iface().places || []).slice(), words = p.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  document.querySelectorAll('a[href]').forEach(function (a) {
    try { var l = new URL(a.getAttribute('href'), location.href); if (l.origin === location.origin && P.indexOf(l.pathname) < 0) P.push(l.pathname); } catch (e) { /* none */ }
  });
  if (window.QCCD_MIRROR) P.push('/studio');
  var scored = P.map(function (q) {
    var ql = q.toLowerCase(), sc = 0;
    words.forEach(function (w) { if (ql.indexOf(w) >= 0) sc += 2 + w.length / 10; });
    for (var k = 0; k < Math.min(ql.length, p.length) && ql[k] === p.toLowerCase()[k]; k++) sc += 0.05;
    return [sc, q];
  }).filter(function (x) { return x[0] > 0.2; });
  scored.sort(function (a, b) { return b[0] - a[0]; });
  return scored.slice(0, 4).map(function (x) { return x[1]; });
}

var KEYS = { Enter: 13, Escape: 27, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Tab: 9, ' ': 32, Space: 32,
             Home: 36, End: 35, PageUp: 33, PageDown: 34 };
// the Studio's own API is the agent's hands on the canvas (the course solves its exercises with
// the same verbs) -- the verbs qccd/viz/js/editor_api.json declares for agents, no other.  On the
// WORKSPACE's own Studio an edit through the page would be recorded as the person's, so there
// only the verbs that read or change the view: the agent changes the design with its own tools.
function designPage() { return !!(window.QCCD_DESIGN_PAGE || iface().design_page); }
function sitePath(href) {
  // the one place navigation may go: this site, on this origin, under the mirror's /web/
  // (or the Studio's own pages when this IS the Studio)
  var u;
  try { u = new URL(href, location.href); } catch (e) { return null; }
  if (u.origin !== location.origin) return null;
  // (/studio on the website's port leads to the person's own Studio, their Design page)
  if (window.QCCD_MIRROR && u.pathname.indexOf('/web/') !== 0 && u.pathname !== '/web' && u.pathname !== '/studio') return null;
  return u;
}
function setValue(el, v, quiet) {
  var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : (el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype);
  var d = Object.getOwnPropertyDescriptor(proto, 'value');
  if (d && d.set) d.set.call(el, v); else el.value = v;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  if (!quiet) el.dispatchEvent(new Event('change', { bubbles: true }));
}
function typeInto(el, text) {
  // letter by letter, at most ~1.5 s however long the text
  var n = text.length, steps = Math.min(n, 45), i = 0;
  if (!n) { setValue(el, ''); return Promise.resolve(); }
  return new Promise(function (done) {
    (function tick() {
      i++;
      var upto = Math.round(n * i / steps);
      setValue(el, text.slice(0, upto), i < steps);
      if (i < steps) setTimeout(tick, Math.max(12, Math.min(40, 1500 / steps))); else done();
    })();
  });
}
function jsonable(x, budget) {
  // a Studio verb's answer, as plain data: no DOM, no functions, bounded
  var seen = [];
  var s = JSON.stringify(x === undefined ? null : x, function (k, v) {
    if (typeof v === 'function') return undefined;
    if (v && typeof v === 'object') {
      if (v.nodeType || v === window) return undefined;
      if (seen.indexOf(v) >= 0) return '[repeated]';
      seen.push(v);
    }
    return v;
  });
  if (s && s.length > (budget || 60000)) return { truncated: true, bytes: s.length, head: s.slice(0, 4000) };
  return s ? JSON.parse(s) : null;
}
function studioBrief(ed) {
  var out = {};
  try { var st = ed.state(), d = st && st.device; if (d) out.device = { nodes: Object.keys(d.nodes || {}).length, segments: Object.keys(d.segments || {}).length }; } catch (e) { /* none */ }
  try { out.problems = (ed.problems() || []).slice(0, 8).map(function (p) { return p.message || p.code || p; }); } catch (e) { /* none */ }
  try { out.program_rows = (ed.program() || []).length; } catch (e) { /* none */ }
  try { if (ed.lessonState) { var ls = ed.lessonState(); if (ls && ls.id) out.lesson = { id: ls.id, stage: ls.stage, passed: ls.passed, feedback: ls.feedback }; } } catch (e) { /* none */ }
  return out;
}
function act(action, args, who) {
  args = args || {};
  var r, el;
  switch (action) {
    case 'read':
      var h1 = document.querySelector('main h1, h1') || document.body;
      pointAt(h1, null, who, 'reading this page');                        // shown; the answer does not wait
      return read(args);
    case 'wait':
      var ms = Math.max(0, Math.min(10000, +args.ms || 1000));
      say(who, args.note ? String(args.note).slice(0, 120) : 'waiting');
      return sleep(ms).then(function () { return { ok: true, waited_ms: ms }; });
    case 'scroll':
      var before = window.scrollY, top;
      if (args.to === 'top') top = 0;
      else if (args.to === 'bottom') top = document.documentElement.scrollHeight;
      else if (args.by !== undefined) {
        var by = args.by === 'page' ? window.innerHeight * 0.85 : (args.by === '-page' ? -window.innerHeight * 0.85 : +args.by);
        top = before + (isFinite(by) ? by : 0);
      }
      if (top !== undefined) {
        return glideTo(window.innerWidth * 0.55, window.innerHeight * 0.45, who, top < before ? 'scrolling up' : 'scrolling down')
          .then(function () { window.scrollTo({ top: top, behavior: 'smooth' }); return sleep(650); })
          .then(function () { return { ok: true, from: Math.round(before), to: Math.round(window.scrollY) }; });
      }
      r = resolve(args.target);
      return pointAt(r.el, r.frame && r.el !== r.frame ? r.frame : null, who, 'looking at "' + label(r.el) + '"')
        .then(function () { return { ok: true, element: describe(r.el), to: Math.round(window.scrollY) }; });
    case 'highlight':
      r = resolve(args.target);
      return pointAt(r.el, r.frame && r.el !== r.frame ? r.frame : null, who, args.note ? 'look here' : 'here').then(function () {
        highlight(r.el, args.note, r.frame && r.el !== r.frame ? r.frame : null);
        return { ok: true, element: describe(r.el), note: args.note ? 'shown beside it for 9 s' : undefined };
      });
    case 'click':
      r = resolve(args.target); el = r.el;
      mustBeDeclared(el, r.doc);
      var nav = null;
      if (el.tagName === 'A') {
        var href = el.getAttribute('href') || '';
        if (href && href[0] !== '#' && !/^javascript:/i.test(href)) {
          nav = sitePath(el.href);
          if (!nav) throw new Error('that link leaves the site (' + clip(href, 100) + '); give the person the link instead');
        }
      }
      if (el.tagName === 'INPUT' && el.type === 'file') throw new Error('file inputs are the person\'s to use');
      if (el.disabled) throw new Error('that control is disabled right now');
      return pointAt(el, r.frame && el !== r.frame ? r.frame : null, who, 'clicking "' + label(el) + '"').then(function () {
        if (nav && el.target === '_blank') { location.assign(nav.href); return { ok: true, navigating: nav.pathname + nav.search + nav.hash }; }
        if (typeof el.click === 'function') el.click();
        else el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: r.win }));
        return { ok: true, element: describe(el), navigating: nav ? nav.pathname + nav.search + nav.hash : undefined };
      });
    case 'fill':
      r = resolve(args.target); el = r.el;
      mustBeDeclared(el, r.doc);
      var value = String(args.value === undefined ? '' : args.value);
      if (el.tagName === 'SELECT') {
        var opt = null;
        Array.prototype.forEach.call(el.options, function (o) { if (!opt && (o.value === value || o.textContent.trim() === value)) opt = o; });
        if (!opt) throw new Error('no option ' + JSON.stringify(value) + ' (options: ' + Array.prototype.map.call(el.options, function (o) { return o.textContent.trim(); }).slice(0, 20).join(', ') + ')');
        return pointAt(el, r.frame && el !== r.frame ? r.frame : null, who, 'choosing "' + opt.textContent.trim() + '"').then(function () {
          setValue(el, opt.value);
          return { ok: true, element: describe(el), value: el.value };
        });
      }
      if (el.tagName !== 'TEXTAREA' && !(el.tagName === 'INPUT' && !/^(password|file|hidden|checkbox|radio|button|submit|image|reset)$/.test(el.type)))
        throw new Error('fill works on text fields, sliders and selects; this is a ' + el.tagName.toLowerCase());
      return pointAt(el, r.frame && el !== r.frame ? r.frame : null, who, 'typing').then(function () {
        el.focus();
        return el.type === 'range' ? (setValue(el, value), null) : typeInto(el, value);
      }).then(function () { return { ok: true, element: describe(el), value: el.value }; });
    case 'press':
      var key = String(args.key || '');
      if (!KEYS[key]) throw new Error('press takes one of: ' + Object.keys(KEYS).join(', '));
      if (key === 'Space') key = ' ';
      if (args.target) { el = resolve(args.target).el; mustBeDeclared(el); }
      else {
        // a key to the page itself only moves around it; one that acts (Enter, arrows) needs a declared target
        if (!/^(Escape|Tab|Home|End|PageUp|PageDown|Space| )$/.test(key)) throw new Error('press ' + key + ' needs a target: a declared control');
        el = document.activeElement && !isPrivate(document.activeElement) && declared(document.activeElement) ? document.activeElement : document.body;
      }
      return pointAt(el, null, who, 'pressing ' + (key === ' ' ? 'Space' : key)).then(function () {
        ['keydown', 'keyup'].forEach(function (t) {
          el.dispatchEvent(new KeyboardEvent(t, { key: key, code: key === ' ' ? 'Space' : key, keyCode: KEYS[key], which: KEYS[key], bubbles: true, cancelable: true }));
        });
        return { ok: true, key: key, element: describe(el) };
      });
    case 'navigate':
      var p = sitePath(String(args.path || ''));
      if (!p) throw new Error('navigate stays on this site: give a path like ../rules/ or /web/rules/');
      if (!isPlace(p)) {
        var near = nearestPlaces(p.pathname);
        throw new Error('there is no page ' + p.pathname + ' (a path is taken from the site map or a link, never guessed)' +
                        (near.length ? '; the nearest places: ' + near.join(', ') : '') +
                        (window.QCCD_MIRROR ? '; the person\'s Design page is /studio' : ''));
      }
      return glideTo(window.innerWidth / 2, 60, who, 'opening ' + p.pathname.replace(/^\/web/, '')).then(function () {
        setTimeout(function () { location.assign(p.href); }, 50);
        return { ok: true, navigating: p.pathname + p.search + p.hash };
      });
    case 'step':
      var win = window, doc = document, fr = null;
      if (args.target || args.frame) { r = resolve(args.target || { frame: args.frame }); win = r.win; doc = r.doc; fr = r.frame; }
      if (typeof win.seek !== 'function') throw new Error('this page has no animation to step');
      var sl = doc.getElementById('slider'), play = doc.getElementById('play');
      var cur = sl ? +sl.value : 0, last = sl ? +sl.max : 0;
      var what = args.play ? 'playing' : args.pause ? 'pausing' : (args.step !== undefined ? 'going to step ' + args.step : 'stepping');
      return pointAt(play || sl || stageOf(doc), fr, who, what).then(function () {
        if (args.play) { if (play && !/pause/i.test(play.textContent)) play.click(); }
        else if (args.pause) { if (play && /pause/i.test(play.textContent)) play.click(); }
        else if (args.frame_index !== undefined || args.step !== undefined) win.seek(Math.max(0, Math.min(last, +(args.step !== undefined ? args.step : args.frame_index))), {});
        else win.seek(Math.max(0, Math.min(last, cur + (args.delta === undefined ? 1 : +args.delta))), {});
        return { ok: true, transport: transport(win, doc) };
      });
    case 'open_lesson':
      var ed = window.EDITOR;
      if (!ed || !ed.lessonLoad) throw new Error('lessons open on the Studio page (studio.html); navigate there first');
      var id = String(args.lesson || args.id || '');
      var known = (ed.lessonList ? ed.lessonList() : []).map(function (l) { return l.id; });
      if (known.length && known.indexOf(id) < 0) throw new Error('no lesson ' + id + '; the lessons are ' + known.join(', '));
      return glideTo(window.innerWidth * 0.75, 150, who, 'opening lesson ' + id).then(function () {
        ed.lessonLoad(id);
        try { var dk = document.getElementById('dock'); if (dk && typeof window.foldPanel === 'function') window.foldPanel(dk, false);
              if (typeof window.setPane === 'function') window.setPane('L'); } catch (e) { /* the page lays out itself */ }
        return { ok: true, lesson: ed.lessonState ? ed.lessonState() : { id: id } };
      });
    case 'studio':
      var sw = window, sd = document, sf = null;
      if (args.frame) { r = resolve({ frame: args.frame }); sw = r.win; sd = r.doc; sf = r.frame; }
      var E = sw.EDITOR, verb = String(args.verb || '');
      if (!E) throw new Error('this page has no Studio (an EDITOR); open studio.html, a lesson, or an example');
      var V = iface().verbs || {}, NA = iface().not_for_agents || {}, dp = designPage() && !sf;
      var usable = function (k) { return V[k] && typeof E[k] === 'function' && (!dp || V[k].kind === 'read' || V[k].kind === 'view'); };
      if (verb === 'verbs') {
        var list = {};
        Object.keys(V).sort().forEach(function (k) { if (usable(k)) list[k] = V[k]; });
        return { ok: true, verbs: Object.keys(list), declared: list,
                 note: dp ? 'the person\'s own design: read and view verbs only; change it with qccd_apply_change_set' : undefined };
      }
      if (NA[verb] === 'never') throw new Error(verb + ' touches your files or storage, or restarts the page; the agent does not use it');
      if (!V[verb]) throw new Error('the Studio has no agent verb ' + JSON.stringify(verb) +
                                    (NA[verb] ? ' (it exists, for tests and the pointer, not for agents)' : '') +
                                    '; studio verb "verbs" lists the declared ones with what each does');
      if (typeof E[verb] !== 'function') throw new Error('this page\'s Studio has no ' + verb);
      if (dp && !usable(verb))
        throw new Error('this is the workspace\'s own design: change it with qccd_apply_change_set, so the change is recorded as the agent\'s');
      var a = Array.isArray(args.args) ? args.args : (args.args === undefined ? [] : [args.args]);
      return pointAt(stageOf(sd), sf, who, verb + (a.length ? ' ' + clip(JSON.stringify(a), 60) : '')).then(function () {
        var res = E[verb].apply(null, a);
        return Promise.resolve(res).then(function (v) {
          return { ok: !(v && v.ok === false), verb: verb, result: jsonable(v), studio: studioBrief(E) };
        });
      });
    case 'comments':
      var hk = hookOf(), me0 = hk.me();
      if (!me0) return { ok: true, signed_in: false, note: NOT_SIGNED };
      return hk.api('GET', '/threads?page=' + encodeURIComponent(hk.page())).then(function (d) {
        return { ok: true, signed_in: true, me: me0.name, page: hk.page(), threads: (d.threads || []).map(function (t) {
          var e = null;
          try { e = hk.resolveAnchor(t.anchor); } catch (x) { e = null; }
          return { id: t.id, resolved: !!t.resolved, author: (t.user || {}).name, at: (t.anchor || {}).text,
                   element: e ? describe(e) : null,
                   comments: (t.comments || []).map(function (c) { return { author: (c.user || {}).name, text: c.text, when: c.created }; }) };
        }) };
      });
    case 'comment':
      var hc = hookOf();
      if (!hc.me()) throw new Error(NOT_SIGNED);
      var ctext = String(args.text || '').trim();
      if (!ctext) throw new Error('comment needs text');
      if (ctext.length > 3800) throw new Error('keep a comment under 3800 characters');
      r = resolve(args.target);
      if (r.frame && r.el !== r.frame) throw new Error('a comment goes on this page, not inside an embedded example: target the example itself ({frame: ...})');
      el = r.el;
      var cbody = signed(ctext, who);
      return pointAt(el, null, who, 'commenting here').then(function () {
        var R = el.getBoundingClientRect();
        var anchor = hc.describe(el, R.left + Math.min(R.width / 2, 40), R.top + Math.min(R.height / 2, 12));
        return hc.api('POST', '/threads', { page: hc.page(), anchor: anchor, text: cbody });
      }).then(function (d) {
        hc.reload();
        return { ok: true, thread: (d.thread || {}).id, page: hc.page(), text: cbody, as: hc.me().name, element: describe(el) };
      });
    case 'reply':
    case 'resolve':
      var hr = hookOf();
      if (!hr.me()) throw new Error(NOT_SIGNED);
      var tid = parseInt(args.thread, 10);
      if (!(tid > 0)) throw new Error(action + ' needs thread: a thread id from the comments action');
      var th = (hr.threads() || []).filter(function (t) { return t.id === tid; })[0];
      var node = document.querySelector('#qc-layer [data-id="' + tid + '"]');
      var there = th ? (hr.resolveAnchor(th.anchor) || node) : node;
      var go = there ? pointAt(there, null, who, action === 'reply' ? 'replying' : 'marking it addressed') : Promise.resolve();
      if (action === 'reply') {
        var rtext = String(args.text || '').trim();
        if (!rtext) throw new Error('reply needs text');
        var rbody = signed(rtext, who);
        return go.then(function () { return hr.api('POST', '/threads/' + tid + '/comments', { text: rbody }); })
          .then(function () { hr.reload(); return { ok: true, thread: tid, page: hr.page(), text: rbody, as: hr.me().name }; });
      }
      return go.then(function () { return hr.api('POST', '/threads/' + tid + '/resolve', { resolved: args.resolved !== false }); })
        .then(function () { hr.reload(); return { ok: true, thread: tid, resolved: args.resolved !== false }; });
    default:
      throw new Error('unknown action ' + JSON.stringify(action) + ': read, scroll, highlight, click, fill, press, navigate, step, open_lesson, studio, wait, comments, comment, reply, resolve');
  }
}
function safe(action, args, who) {
  var p;
  try { p = Promise.resolve(act(action, args, who)); } catch (e) { p = Promise.reject(e); }
  return p.then(function (out) {
    if (out && out.error) return { ok: false, error: out.error, detail: out };
    var s = JSON.stringify(out);
    if (s && s.length > 120000) return { ok: true, result: { truncated: true, note: 'the answer was too large; read a section or use offset' } };
    return { ok: true, result: out };
  }, function (e) {
    if (who) say(who, 'could not: ' + String(e && e.message || e).slice(0, 80));
    return { ok: false, error: String(e && e.message || e) };
  });
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

window.QCCD_PAGE = { read: read, act: safe, context: context, pick: pick,
                     // the gate (tests/test_agent_interface.py): every control on the page, declared or listed here
                     undeclared: function () {
                       var out = [];
                       document.querySelectorAll(CONTROL).forEach(function (c) {
                         if (isPrivate(c) || (c.tagName === 'INPUT' && c.type === 'hidden') || declared(c)) return;
                         var p = c.parentElement && c.parentElement.closest('[id]');
                         out.push({ label: label(c), tag: c.tagName.toLowerCase(), id: c.id || null, in: p ? p.id : null });
                       });
                       return out;
                     },
                     declared: function (el) { return declared(el); },
                     highlight: function (t, note) { return safe('highlight', { target: t, note: note }); },
                     // the chat layer shows work that did not come through a page action (a change set)
                     point: function (x, y, who, caption) { return glideTo(x, y, who, caption); } };
})();
