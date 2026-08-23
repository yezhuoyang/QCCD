// The minimal DOM shim every page harness runs an emitted page under.
//
// This used to be two byte-identical copies, one in `census.mjs` and one in `panels.mjs`.
// Three copies of one shim is itself a two-implementations problem in miniature -- a stub
// added for one harness and not the other makes the two disagree about what the page can
// do -- so it lives here once and all three import it.
//
// WHAT IS DELIBERATELY NOT STUBBED, because page code must not depend on it:
//   elementFromPoint, getBBox, getTotalLength, getPointAtLength, getScreenCTM,
//   createSVGPoint, closest, matches, contains, dataset, insertAdjacentHTML,
//   MutationObserver, IntersectionObserver, crypto, localStorage, sessionStorage,
//   Event / PointerEvent constructors and dispatchEvent, and `navigator`.
// Anything built on those would be untestable here, which is the point: the editor's
// pointer handlers are thin adapters onto `EDITOR.begin/move/drop` precisely because a
// harness cannot synthesize a pointer event.
//
// TWO STUBS THAT ARE TRAPS, both faithful to what the real harness does:
//   * `remove()` is a NO-OP and leaves the child in `parent.children`, so page code must
//     pool-and-hide rather than remove-and-recreate (or clear via `replaceChildren` /
//     truncating `children`, which works in both environments);
//   * `classList` is a no-op, so a class written by JS can never be read back -- no page
//     behaviour may depend on one.
//   * `requestAnimationFrame` / `setTimeout` NEVER FIRE, which is why the page offers
//     `globalThis.__QCCD_SYNC` to drain its debounced work synchronously.
import fs from 'fs';

export function mk(tag) {
  return {
    tagName: tag, children: [], style: {}, attrs: {}, _text: '',
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    removeAttribute(k) { delete this.attrs[k]; },
    append(...c) { this.children.push(...c); },
    appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    replaceChildren(...c) { this.children.length = 0; this.children.push(...c); },
    addEventListener() {}, remove() {},
    set textContent(v) { this._text = v; }, get textContent() { return this._text; },
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html || ''; },
    set value(v) { this._value = v; }, get value() { return this._value || ''; },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    getBoundingClientRect: () => ({ width: 1600, height: 400, left: 0, top: 0 }),
    querySelector: () => null, querySelectorAll: () => [],
  };
}

export function installShim() {
  const byId = {};
  globalThis.document = {
    createElementNS: (ns, t) => mk(t), createElement: (t) => mk(t),
    getElementById: (id) => (byId[id] ||= mk('div')),
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, documentElement: mk('html'), body: mk('body'),
    activeElement: null,
  };
  globalThis.window = {
    addEventListener() {}, devicePixelRatio: 1, innerWidth: 1600, innerHeight: 900,
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#000000', width: '1600px' });
  globalThis.requestAnimationFrame = () => 0;
  // `stop()` calls `cancelAnimationFrame(raf)` only when `raf` is truthy, and the stub
  // above happens to return 0 -- so the page's freeze path worked here by coincidence.
  // A hidden coupling between a stub's return value and a page's control flow is exactly
  // the kind of thing that makes a harness pass for the wrong reason.
  globalThis.cancelAnimationFrame = () => {};
  globalThis.setInterval = () => 0;
  globalThis.clearInterval = () => {};
  globalThis.setTimeout = () => 0;
  globalThis.clearTimeout = () => {};
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  return byId;
}

// Pull the page apart the way a browser would: the view model out of the JSON block, and
// every OTHER script block concatenated into one body -- which is what makes the inlined
// engine, the topology mirror, the page and the editor share a scope here exactly as they
// do in a real document.
export function loadPage(file, hook = '') {
  const html = fs.readFileSync(file, 'utf8');
  const byId = installShim();
  const dataM = html.match(/<script id="data"[^>]*>([\s\S]*?)<\/script>/);
  if (!dataM) throw new Error('no data block in ' + file);
  const dataEl = mk('script');
  dataEl.textContent = dataM[1];
  byId['data'] = dataEl;
  const code = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)]
    .filter(b => !/application\/json/.test(b[1]))
    .map(b => b[2]).join('\n');
  if (!code.trim()) throw new Error('no code block in ' + file);
  new Function(code + hook)();
  return { html, byId };
}
