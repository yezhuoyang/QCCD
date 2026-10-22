// The book's chapter page: every code block is Lean the reader edits, checked by the Lean that
// runs in this browser (window.Book, from book-runtime.js).  The whole chapter is one Lean
// document -- the cells in order, then the book's hidden grader -- so a definition in one cell
// is in scope in the next, exactly as in the chapter's .lean file.
(function () {
  'use strict';
  var data = JSON.parse(document.getElementById('bookdata').textContent);
  var bar = document.getElementById('leanbar');
  var cells = Array.prototype.slice.call(document.querySelectorAll('#chapter .cell'));
  var TEXT_KEY = 'qccd.book.text.' + data.slug;
  var PROG_KEY = 'qccd.book.progress';
  var rt = window.Book;
  var lastDoc = null, layout = [], graderAt = 0, timer = null, running = false, pending = false;
  var casesRunning = false;

  // ---------------------------------------------------------------- storage (never required)
  function load(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; } }
  function save(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* private window */ } }

  // ---------------------------------------------------------------- the status bar
  function status(state, what, prog) {
    bar.setAttribute('data-state', state);
    bar.querySelector('.what').textContent = what;
    bar.querySelector('.prog').textContent = prog || '';
  }
  function mb(n) { return (n / 1048576).toFixed(n > 10485760 ? 0 : 1) + ' MB'; }

  // ---------------------------------------------------------------- highlighting
  var KW = /^(theorem|lemma|def|abbrev|structure|instance|where|by|fun|let|in|if|then|else|match|with|do|return|open|import|example|namespace|end|section|deriving|have|show|from|calc|at|simp|decide|rfl|exact|apply|intro|intros|cases|induction|rw|unfold|sorry|constructor|refine|omega|trivial|public|module|meta|private)$/;
  var TOKEN = /(--[^\n]*|\/-[\s\S]*?-\/)|("(?:[^"\\\n]|\\.)*")|(#[a-z]+)|(\b\d+\b)|([A-Za-z_][A-Za-z0-9_'.]*)/g;
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function highlight(src) {
    var out = '', i = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(src))) {
      out += esc(src.slice(i, m.index));
      var t = esc(m[0]);
      if (m[1]) out += '<span class="c">' + t + '</span>';
      else if (m[2]) out += '<span class="s">' + t + '</span>';
      else if (m[3]) out += '<span class="k">' + t + '</span>';
      else if (m[4]) out += '<span class="n">' + t + '</span>';
      else if (KW.test(m[5])) out += '<span class="k">' + t + '</span>';
      else if (/^[A-Z]/.test(m[5])) out += '<span class="t">' + t + '</span>';
      else out += t;
      i = TOKEN.lastIndex;
    }
    return out + esc(src.slice(i)) + '\n';
  }

  // ---------------------------------------------------------------- the cells
  var goalsBox = document.getElementById('goals'), goalsTimer = null;
  var saved = load(TEXT_KEY) || {};
  cells.forEach(function (cell, k) {
    var ta = cell.querySelector('textarea');
    cell._orig = ta.value;
    if (saved[k] != null && saved[k] !== ta.value) ta.value = saved[k];
    var ed = document.createElement('div');
    ed.className = 'ed';
    var pre = document.createElement('pre');
    pre.className = 'hl';
    pre.setAttribute('aria-hidden', 'true');
    cell.insertBefore(ed, ta);
    ed.appendChild(pre);
    ed.appendChild(ta);
    var tools = document.createElement('div');
    tools.className = 'tools';
    var reset = document.createElement('button');
    reset.type = 'button';
    reset.textContent = 'Reset';
    reset.title = 'Put this block back as the lesson wrote it';
    reset.addEventListener('click', function () { ta.value = cell._orig; edited(cell, k); });
    tools.appendChild(reset);
    ed.appendChild(tools);
    cell._ta = ta; cell._pre = pre;
    paint(cell);
    ta.addEventListener('input', function () { edited(cell, k); });
    ta.addEventListener('keydown', function (ev) {
      if (ev.key === 'Tab' && !ev.shiftKey) {        // two spaces, as Lean is written
        ev.preventDefault();
        var s = ta.selectionStart, e = ta.selectionEnd;
        ta.value = ta.value.slice(0, s) + '  ' + ta.value.slice(e);
        ta.selectionStart = ta.selectionEnd = s + 2;
        edited(cell, k);
      }
    });
    ['click', 'keyup', 'focus'].forEach(function (ev) { ta.addEventListener(ev, function () { caretMoved(cell); }); });
    // the goals belong to the block being edited: leave it and they go
    ta.addEventListener('blur', function () {
      setTimeout(function () { if (!document.activeElement || !document.activeElement.closest('.cell')) goalsBox.hidden = true; }, 200);
    });
    ta.addEventListener('scroll', function () { pre.scrollLeft = ta.scrollLeft; });
  });
  function paint(cell) {
    var ta = cell._ta;
    cell._pre.innerHTML = highlight(ta.value);
    ta.style.height = 'auto';
    ta.style.height = (ta.scrollHeight + 2) + 'px';
  }
  var lastEdited = null;
  function edited(cell, k) {
    lastEdited = cell;
    paint(cell);
    var s = load(TEXT_KEY) || {};
    if (cell._ta.value === cell._orig) delete s[k]; else s[k] = cell._ta.value;
    save(TEXT_KEY, s);
    schedule(650);
  }

  // ---------------------------------------------------------------- the document
  // The chapter's own `import` line (the hidden cell) is NOT sent: the runtime puts the
  // browser engine's prelude in front of the text instead, and reports lines relative to it.
  function documentText() {
    layout = [];
    var parts = [], line = 1;
    cells.forEach(function (cell) {
      if (cell.hasAttribute('data-hidden')) return;
      var t = cell._ta.value.replace(/\s+$/, '');
      var n = t.split('\n').length;
      layout.push({ cell: cell, start: line, end: line + n - 1 });
      parts.push(t);
      line += n + 1;                                   // the blank line between cells
    });
    graderAt = line;
    var doc = parts.join('\n\n') + '\n' + data.grader;
    // the figures: Lean's replay of each named certificate, one #eval per name, after the grader
    // a {{studio NAME}} figure: NAME is the lesson's own value with fields `arch` (the device) and
    // `src` (a studio programme), which Lean prints for the studio to play
    figLines = {};
    var names = [];
    figs.forEach(function (f) {
      var key = f.getAttribute('data-kind') === 'studio' ? 'studio:' + f.getAttribute('data-name') : f.getAttribute('data-name');
      if (names.indexOf(key) < 0) names.push(key);
    });
    if (names.length) {
      doc += '\n' + data.figureHelper;
      names.forEach(function (key) {
        figLines[doc.split('\n').length] = key;
        if (key.slice(0, 7) === 'studio:') {
          var n = key.slice(7);
          doc += '#eval IO.println (Lean.Json.mkObj [("arch", Lean.toJson ' + n + '.arch), ("src", Lean.toJson ' + n + '.src), ("studio", Lean.toJson true)]).compress\n';
        } else {
          doc += '#eval IO.println (QCCDBook.figure ' + key + ').compress\n';
        }
      });
    }
    return doc;
  }
  // a {{gadget}} figure is a design page drawn as built, with no Lean value behind it
  var figs = Array.prototype.slice.call(document.querySelectorAll('figure.leanfig:not([data-kind="gadget"])'));
  var figLines = {}, figObjs = new Map();
  function drawFigures(messages) {
    if (!window.BookFigure || !figs.length) return;
    var got = {};
    messages.forEach(function (m) {
      var n = figLines[m.line];
      if (n && sev(m.severity) === 'info' && /^\s*\{/.test(m.text)) {
        try { got[n] = JSON.parse(m.text); } catch (e) { /* not the figure's line */ }
      }
    });
    figs.forEach(function (f) {
      var n = f.getAttribute('data-name'), studio = f.getAttribute('data-kind') === 'studio';
      var fig = got[studio ? 'studio:' + n : n];
      if (!fig) {
        if (!figObjs.get(f)) f.innerHTML = '<div class="figwait">Lean has no value named <code>' + esc(n)
          + '</code> right now: an error above stops it, or its definition was changed.</div>';
        else f.classList.add('stale');
        return;
      }
      f.classList.remove('stale');
      var dev = data.devices[fig.arch];
      if (!dev) { f.innerHTML = '<div class="figwait">No studio page for the device <code>' + esc(fig.arch) + '</code>.</div>'; return; }
      var o = figObjs.get(f);
      if (!o) { o = studio ? BookFigure.programme(f, fig.arch) : BookFigure.live(f, fig.arch); figObjs.set(f, o); }
      o.update(fig, dev);
    });
  }
  function cellOf(line) {
    for (var i = 0; i < layout.length; i++) if (line >= layout[i].start && line <= layout[i].end) return layout[i];
    return null;
  }

  function schedule(ms) {
    if (!rt || !ready) return;
    clearTimeout(timer);
    timer = setTimeout(run, ms);
  }
  var ready = false;

  function run() {
    if (casesRunning) { pending = true; return; }
    if (running) { pending = true; return; }
    running = true; pending = false;
    var doc = documentText();
    lastDoc = doc;
    status('busy', 'Lean is checking the lesson…');
    rt.check(doc).then(function (res) {
      running = false;
      if (res && res.superseded) { if (pending) schedule(50); return; }
      if (res && res.crashed) {
        status('failed', 'Lean stopped on this text (' + (res.why || 'it ran out of room') + '). Undo the last change, or Reset a block.');
        if (pending) schedule(50);
        return;
      }
      var msgs = (res.messages || []).filter(function (m) { return !m.inPrelude; });
      // This engine drops a syntax error's message, and an unclosed bracket swallows every line
      // after it.  The grader is the last thing in the document and always prints (one axioms
      // line per exercise), so a grader that printed nothing means Lean stopped reading early:
      // say so at the block the reader changed last, and leave the exercises as they were.
      var graderSeen = msgs.some(function (m) { return m.line >= graderAt; });
      if (data.exercises.length && !graderSeen) {
        show(msgs, true);
        status('failed', 'Lean could not read the whole lesson: there is a syntax error, most likely in the block you changed last.');
        if (lastEdited) {
          lastEdited.classList.add('bad');
          var d = document.createElement('div');
          d.className = 'msg error';
          d.innerHTML = '<span class="ln">syntax</span><pre>Lean could not read past this block. Look for an unclosed bracket, '
            + 'a missing :=, or a half-written line. Everything after it went unread, so later blocks show no messages.</pre>';
          lastEdited.querySelector('.msgs').appendChild(d);
        }
        if (pending) schedule(50);
        return;
      }
      show(msgs, false);
      drawFigures(msgs);
      status('ready', 'Lean checked the lesson', (res.ms != null ? (res.ms / 1000).toFixed(1) + ' s' : ''));
      if (pending) schedule(50);
    }, function (err) {
      running = false;
      status('failed', 'Lean could not check the lesson: ' + (err && err.message || err));
    });
  }

  // ---------------------------------------------------------------- what Lean may not say
  // This engine drops parse errors, and Lean's error recovery then closes a bracket or puts a
  // `sorry` where the text could not be read.  These checks catch the common cases on the page
  // itself: an unclosed or stray bracket, string or block comment, and a block that stops in
  // the middle of a definition.  They never pass anything Lean would refuse; they only add errors.
  var OPEN = '([{⟨⦃', CLOSE = ')]}⟩⦄';
  function syntaxProblems(text) {
    var out = [], stack = [], i = 0, line = 1, depthC = 0, cStart = 0;
    while (i < text.length) {
      var ch = text[i];
      if (ch === '\n') { line++; i++; continue; }
      if (depthC) {                                   // inside /- ... -/, which nests
        if (text.startsWith('/-', i)) { depthC++; i += 2; continue; }
        if (text.startsWith('-/', i)) { depthC--; i += 2; continue; }
        i++; continue;
      }
      if (text.startsWith('--', i)) { while (i < text.length && text[i] !== '\n') i++; continue; }
      if (text.startsWith('/-', i)) { depthC = 1; cStart = line; i += 2; continue; }
      if (ch === '"') {                               // a Lean string may run over several lines
        var s0 = line; i++;
        while (i < text.length && text[i] !== '"') {
          if (text[i] === '\n') line++;
          i += text[i] === '\\' ? 2 : 1;
        }
        if (text[i] !== '"') { out.push({ line: s0, text: 'This string is not closed: a " is missing.' }); }
        i++; continue;
      }
      // a character literal ('(' or '\x28'), which holds no bracket; an apostrophe after a
      // name's character is part of the name (h', ih'), and a non-ASCII one counts as such
      var prev = text[i - 1] || ' ';
      if (ch === "'" && !/[\w'.!?]/.test(prev) && prev.charCodeAt(0) < 128) {
        var lit = /^'(?:\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)|[^'\\\n])'/.exec(text.slice(i, i + 10));
        if (lit) { i += lit[0].length; continue; }
      }
      var o = OPEN.indexOf(ch), c = CLOSE.indexOf(ch);
      if (o >= 0) stack.push({ ch: ch, want: CLOSE[o], line: line });
      else if (c >= 0) {
        var top = stack.pop();
        if (!top) out.push({ line: line, text: 'This ' + ch + ' closes nothing: there is no matching ' + OPEN[c] + ' before it.' });
        else if (top.want !== ch) out.push({ line: line, text: 'This ' + ch + ' does not match the ' + top.ch + ' opened on line ' + top.line + '.' });
      }
      i++;
    }
    if (depthC) out.push({ line: cStart, text: 'This comment is not closed: a -/ is missing.' });
    stack.forEach(function (s) { out.push({ line: s.line, text: 'This ' + s.ch + ' is never closed: a ' + s.want + ' is missing.' }); });
    var lines = text.replace(/\s+$/, '').split('\n');
    var last = (lines[lines.length - 1] || '').replace(/--.*$/, '').trim();
    if (/(:=|\bby|=>|,)$/.test(last)) out.push({ line: lines.length, text: 'This block stops in the middle: something must follow "' + last.split(/\s+/).pop() + '".' });
    return out;
  }
  function hasSorry(text) { return /\bsorry\b/.test(text.replace(/--[^\n]*/g, '').replace(/\/-[\s\S]*?-\//g, '')); }

  // ---------------------------------------------------------------- messages and exercises
  function sev(s) { return s === 'information' ? 'info' : (s || 'info'); }
  function addMsg(cell, kind, where, text) {
    var div = document.createElement('div');
    div.className = 'msg ' + kind;
    var ln = document.createElement('span');
    ln.className = 'ln';
    ln.textContent = where;
    var pre = document.createElement('pre');
    pre.textContent = text;
    div.appendChild(ln); div.appendChild(pre);
    cell.querySelector('.msgs').appendChild(div);
  }
  function show(messages, keepExercises) {
    cells.forEach(function (c) { c.querySelector('.msgs').innerHTML = ''; c.classList.remove('bad'); });
    var docLines = lastDoc.split('\n');
    var graderLine = {}, axiomsOf = {}, graderErr = {};
    for (var L = graderAt; L <= docLines.length; L++) {
      var t = docLines[L - 1] || '';
      var mm = t.match(/@([A-Za-z_][A-Za-z0-9_'.]*)\s*$/);
      if (/example :/.test(t) && mm) graderLine[L] = { kind: 'stmt', name: mm[1] };
      var ma = t.match(/^#print axioms ([A-Za-z_][A-Za-z0-9_'.]*)/);
      if (ma) graderLine[L] = { kind: 'axioms', name: ma[1] };
    }
    var errorsInCell = new Map();
    messages.forEach(function (m) {
      var s = sev(m.severity);
      if (m.line >= graderAt) {
        var g = graderLine[m.line];
        if (!g) return;
        if (g.kind === 'axioms' && s === 'info') {
          var txt = m.text || '';
          if (/does not depend on any axioms/.test(txt)) axiomsOf[g.name] = [];
          var a = txt.match(/depends on axioms: \[([\s\S]*?)\]/);
          if (a) axiomsOf[g.name] = a[1].split(',').map(function (x) { return x.trim(); }).filter(Boolean);
        }
        if (s === 'error') graderErr[g.name] = (graderErr[g.name] || []).concat([m.text]);
        return;
      }
      var slot = cellOf(m.line);
      if (!slot || slot.cell.hasAttribute('data-hidden')) return;
      // shown as "open" on the exercise; Lean 4.29 quotes it 'sorry', 4.33 `sorry`
      if (s === 'warning' && /declaration uses .sorry./.test(m.text) && slot.cell.hasAttribute('data-ex')) return;
      addMsg(slot.cell, s, 'line ' + (m.line - slot.start + 1), m.text);
      if (s === 'error') { slot.cell.classList.add('bad'); errorsInCell.set(slot.cell, true); }
    });
    // the engine now reports parse errors itself; these checks stay as a fallback and speak
    // only when Lean reported no error in the block
    cells.forEach(function (cell) {
      if (cell.hasAttribute('data-hidden') || errorsInCell.get(cell)) return;
      syntaxProblems(cell._ta.value).forEach(function (p) {
        addMsg(cell, 'error', 'line ' + p.line, p.text);
        cell.classList.add('bad'); errorsInCell.set(cell, true);
      });
    });
    if (keepExercises) return;
    // what this reader has solved before, on this browser or (signed in) on any: book-progress.js
    var before = window.QCCDBOOK ? QCCDBOOK.state().done : ((load(PROG_KEY) || {}).done || {});
    var solved = 0;
    // an exercise whose proof uses another exercise that is still open carries that one's sorry:
    // it is open too (on what it uses), not a proof Lean could not read
    var openName = {};
    function exCell(n) { return document.querySelector('.cell[data-ex="' + n + '"]'); }
    function usesOpen(text) {
      var t = text.replace(/--[^\n]*/g, '').replace(/\/-[\s\S]*?-\//g, '');
      return Object.keys(openName).filter(function (n) {
        return new RegExp("(^|[^A-Za-z0-9_.'])" + n.replace(/\./g, '[.]') + "($|[^A-Za-z0-9_'])").test(t);
      });
    }
    data.exercises.forEach(function (e) {
      var c = exCell(e.name);
      if ((axiomsOf[e.name] || []).indexOf('sorryAx') >= 0 && c && hasSorry(c._ta.value)) openName[e.name] = true;
    });
    for (var grew = true; grew;) {
      grew = false;
      data.exercises.forEach(function (e) {
        var c = exCell(e.name);
        if (openName[e.name] || !c || (axiomsOf[e.name] || []).indexOf('sorryAx') < 0) return;
        if (usesOpen(c._ta.value).length) { openName[e.name] = true; grew = true; }
      });
    }
    data.exercises.forEach(function (e) {
      var cell = document.querySelector('.cell[data-ex="' + e.name + '"]');
      var st, label;
      var ax = axiomsOf[e.name];
      if (cell && errorsInCell.get(cell)) { st = 'error'; label = 'error'; }
      else if (graderErr[e.name]) {
        st = 'changed';
        label = /unknown (identifier|constant)/.test(graderErr[e.name].join(' ')) ? 'missing' : 'statement changed';
      }
      else if (!ax) { st = 'open'; label = 'open'; }
      else if (ax.indexOf('sorryAx') >= 0 && cell && !hasSorry(cell._ta.value) && usesOpen(cell._ta.value).length) {
        st = 'open'; label = 'open · uses ' + usesOpen(cell._ta.value)[0];
      }
      else if (ax.indexOf('sorryAx') >= 0 && cell && !hasSorry(cell._ta.value)) {
        // Lean put a sorry where the reader wrote none: part of the proof could not be read
        st = 'error'; label = 'error';
        cell.classList.add('bad');
        addMsg(cell, 'error', 'syntax', 'Lean could not read part of this proof, and the engine on this page drops '
          + 'the parser\'s message. Look for a half-written line or a misspelt keyword.');
      }
      else if (ax.indexOf('sorryAx') >= 0) { st = 'open'; label = 'open'; }
      else if (ax.some(function (a) { return data.allowed.indexOf(a) < 0; })) { st = 'untrusted'; label = 'rests on ' + ax.filter(function (a) { return data.allowed.indexOf(a) < 0; }).join(', '); }
      else {
        st = 'solved'; label = 'solved'; solved++;
        if (window.QCCDBOOK) QCCDBOOK.mark(e.id);
        else { var pp = load(PROG_KEY) || { v: 1, done: {} }; pp.done = pp.done || {}; pp.done[e.id] = 1; save(PROG_KEY, pp); }
      }
      if (st === 'open' && before[e.id]) label = 'open · solved before';
      var tag = document.querySelector('.exstate[data-ex="' + e.name + '"]');
      if (tag) {
        tag.setAttribute('data-s', st);
        tag.textContent = label;
        tag.title = st === 'solved' ? 'Axioms: ' + (ax.length ? ax.join(', ') : 'none') : '';
      }
      if (cell) cell.classList.toggle('solved', st === 'solved');
    });
    score();
  }
  // the chapter's score: what the reader has solved, ever, on this browser or their account
  function score() {
    var s = window.QCCDBOOK ? QCCDBOOK.state() : { done: (load(PROG_KEY) || {}).done || {}, status: 'local' };
    var n = data.exercises.filter(function (e) { return s.done[e.id]; }).length;
    var where = s.user ? ' · kept on your account' : (s.status === 'anon' ? ' · kept in this browser; sign in to keep it on your account' : '');
    bar.querySelector('.score').textContent = n + ' of ' + data.exercises.length + ' exercises done' + where;
  }
  if (window.QCCDBOOK) QCCDBOOK.subscribe(score);

  // ---------------------------------------------------------------- goals at the cursor
  function caretMoved(cell) {
    if (!rt || !ready || !rt.goals) return;
    clearTimeout(goalsTimer);
    goalsTimer = setTimeout(function () {
      var doc = documentText();
      var slot = layout.filter(function (s) { return s.cell === cell; })[0];
      if (!slot) return;
      var ta = cell._ta;
      var line = slot.start + ta.value.slice(0, ta.selectionStart).split('\n').length - 1;
      rt.goals(doc, line).then(function (g) {
        if (typeof g !== 'string') return;               // superseded by a newer request
        g = g.trim();
        if (!g) { goalsBox.hidden = true; return; }
        goalsBox.hidden = false;
        goalsBox.querySelector('pre').textContent = g === 'no goals' ? 'No goals: the proof is complete here.' : g;
      }, function () { goalsBox.hidden = true; });
    }, 300);
  }

  // ---------------------------------------------------------------- the published certificates
  function casesPanel() {
    var rows = document.getElementById('certrows');
    if (!rows || !data.cases.length) return;
    data.cases.forEach(function (c) {
      var r = document.createElement('div');
      r.className = 'row';
      r.innerHTML = '<span><b>' + esc(c.id) + '</b></span>'
        + '<span class="what">' + esc(c.arch) + ' &middot; ' + c.ops + ' ops, ' + c.moves + ' moves'
        + (c.rots ? ', ' + c.rots + ' rotations' : '') + ', ' + c.witnesses + ' witnesses</span>'
        + '<span><button type="button" class="watch">watch</button> &middot; <a href="' + c.files.qcheck + '" download>input</a> &middot; <a href="' + c.files.qcert + '" download>certificate</a></span>'
        + '<span class="v" data-case="' + esc(c.id) + '">not yet proved</span>';
      rows.appendChild(r);
      var fig = document.createElement('figure');
      fig.className = 'leanfig casefig';
      fig.setAttribute('data-name', c.ident);
      fig.hidden = true;
      rows.appendChild(fig);
      r.querySelector('.watch').addEventListener('click', function () {
        if (!fig.hidden) { fig.hidden = true; return; }
        fig.hidden = false;
        if (figObjs.get(fig)) return;
        fig.innerHTML = '<div class="figwait">Lean is replaying ' + esc(c.id) + '…</div>';
        // the case's definition alone (not its theorem), then Lean's replay of it
        var text = 'open QCCDC.Cert\n\n' + c.lean.split('\n\ntheorem')[0] + '\n\n' + data.figureHelper
          + '#eval IO.println (QCCDBook.figure ' + c.ident + ').compress\n';
        (function attempt() {
          if (casesRunning || running) { setTimeout(attempt, 300); return; }
          running = true;
          rt.check(text).then(function (res) {
            running = false;
            var got = null;
            ((res && res.messages) || []).forEach(function (m) {
              if (sev(m.severity) === 'info' && /^\s*\{/.test(m.text || '')) { try { got = JSON.parse(m.text); } catch (e) { /* skip */ } }
            });
            if (!got || !data.devices[got.arch]) { fig.innerHTML = '<div class="figwait">Lean could not replay ' + esc(c.id) + '.</div>'; return; }
            var o = BookFigure.compiled(fig, c.id);
            figObjs.set(fig, o);
            o.update(got, data.devices[got.arch]);
            if (pending) schedule(50);
          }, function () { running = false; fig.innerHTML = '<div class="figwait">Lean could not replay ' + esc(c.id) + '.</div>'; });
        })();
      });
    });
    var go = document.getElementById('certsgo'), sum = document.getElementById('certsum');
    sum.textContent = data.cases.length + ' certificates from the Compilation page.';
    go.addEventListener('click', function () {
      go.disabled = true; casesRunning = true;
      var ok = 0, i = 0, t0 = performance.now();
      (function next() {
        if (i >= data.cases.length) {
          sum.textContent = ok + ' of ' + data.cases.length + ' proved, in ' + ((performance.now() - t0) / 1000).toFixed(1) + ' s.';
          go.disabled = false; casesRunning = false;
          if (pending) schedule(50);
          return;
        }
        var c = data.cases[i++];
        var v = document.querySelector('.v[data-case="' + c.id + '"]');
        v.className = 'v'; v.textContent = 'proving…';
        sum.textContent = 'Proving ' + c.id + ' (' + i + ' of ' + data.cases.length + ')…';
        rt.check('open QCCDC.Cert\n\n' + c.lean).then(function (res) {
          var msgs = ((res && res.messages) || []).filter(function (m) { return !m.inPrelude; });
          var errs = msgs.filter(function (m) { return sev(m.severity) === 'error'; });
          var ax = null;
          msgs.forEach(function (m) {
            var a = (m.text || '').match(/depends on axioms: \[([\s\S]*?)\]/);
            if (a) ax = a[1].split(',').map(function (x) { return x.trim(); });
            if (/does not depend on any axioms/.test(m.text || '')) ax = [];
          });
          if (res && res.crashed) { v.className = 'v no'; v.textContent = 'Lean stopped'; }
          else if (errs.length || !ax) { v.className = 'v no'; v.textContent = 'not proved'; v.title = errs.map(function (m) { return m.text; }).join('\n'); }
          else if (ax.some(function (a) { return data.allowed.indexOf(a) < 0; })) { v.className = 'v no'; v.textContent = 'rests on ' + ax.join(', '); }
          else { ok++; v.className = 'v ok'; v.textContent = 'proved · ' + ((res.ms || 0) / 1000).toFixed(1) + ' s'; v.title = 'Axioms: ' + (ax.join(', ') || 'none'); }
          next();
        }, function (err) { v.className = 'v no'; v.textContent = 'error'; v.title = String(err); next(); });
      })();
    });
  }

  // ---------------------------------------------------------------- start
  casesPanel();
  if (!rt) {
    status('failed', 'Lean did not load on this page. The lesson reads as written; its exercises need Lean.');
    return;
  }
  if (!window.crossOriginIsolated) {
    status('failed', 'This browser did not isolate the page, which the in-browser Lean needs. Try a current desktop Chrome, Edge or Firefox.');
    return;
  }
  status('busy', 'Lean is loading');
  rt.configure({ prelude: data.prelude });
  var PHASE = { download: 'downloading Lean (kept for next time)', start: 'starting Lean', fs: 'unpacking the library',
                import: 'loading the proved checker', ready: 'ready' };
  rt.ready(function (p) {
    if (!p) return;
    var what = 'Lean is loading: ' + (PHASE[p.phase] || p.phase);
    status('busy', what, p.phase === 'download' && p.total ? mb(p.loaded) + ' of ' + mb(p.total) : '');
  }).then(function () {
    ready = true;
    var go = document.getElementById('certsgo');
    if (go) go.disabled = false;
    run();
  }, function (err) {
    status('failed', 'Lean could not start in this browser: ' + (err && err.message || err));
  });
})();
