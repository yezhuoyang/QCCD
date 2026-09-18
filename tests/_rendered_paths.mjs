// Does a reader see a path into this repository?
//
// The website says nothing about the tree it was built from: `qccd/site/prose.py` strips
// repository paths out of the reference documents, and `tests/test_site_prose.py` holds
// that.  Both work on MARKUP, and that is their blind spot.  The studio writes text at
// runtime out of its own payload -- the provenance footer under the hardware programme was
// `from qccd/compile/cooling.py:350` on every board entry page, on screen, while a scan of
// 203 pages' HTML reported nothing, because the string was never in the HTML.
//
// So this walks the RENDERED page instead: every text node the reader can actually see,
// after opening each pane, matching anything that reads as a source file or a tree path.
// It is the sibling of the cover check -- both ask the one question markup cannot answer,
// which is what the person looking at the page is looking at.
//
// Usage, from a harness that already has a CDP page open:
//
//   import { PROBE, describe } from "./_rendered_paths.mjs";
//   const hits = await page.eval(PROBE);          // after load, and after any pane opens
//   if (hits.length) fail(describe(hits));
//
// `OPEN_PANES` clicks the studio's pane buttons first, so text behind a tab is included.
// Both are strings evaluated in the page, so this module has no dependencies and works
// with any driver.

//: Clicks every pane the studio offers, so nothing is hidden behind a tab when we look.
//: The buttons are `#tabL`, `#tabP`, `#tabQ`, `#tabA`, `#tabM`, `#tabW`, `#tabR` inside
//: `nav#tabs` -- read from the page rather than listed here, so a new pane is covered the
//: day it is added.  Returns how many it clicked: **zero means it found nothing to open**,
//: which is a broken selector and not a page without panes, so a caller should say so.
export const OPEN_PANES = `(() => {
  var btns = [].slice.call(document.querySelectorAll('nav#tabs button.tab, nav.tabs button.tab'));
  btns.forEach(function (b) { try { b.click(); } catch (e) {} });
  return btns.length;
})()`;

//: What counts as a path.  Deliberately NOT a match for the artifact names a reader does
//: meet -- `prog.tsir.json`, `deck24.arch.json`, the `.tsir.json` export option -- nor for
//: the tool's own CLI (`python -m qccd phys ...`), which names the product, not a file.
//: Kept in the same shape as `qccd.site.prose.PATH`; if you widen one, widen the other.
export const PROBE = String.raw`(() => {
  var DIRS = 'qccd|Compiler|tests|Codesign|tools|Knowledge|Library|examples|docs|arch|site|lean|ChainQ';
  var SRC = 'py|lean|ml|mli|mjs|olean|yaml|yml|js';
  var PAT = new RegExp('(?:(?:' + DIRS + ')\\/[A-Za-z0-9_./*-]*[A-Za-z0-9_*\\/]'
                     + '|[A-Za-z0-9_][A-Za-z0-9_.-]*\\.(?:' + SRC + ')\\b)(?::\\d+)?', 'g');
  var hidden = function (el) {
    for (var e = el; e && e !== document.body; e = e.parentElement) {
      var cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return true;
      if (e.hasAttribute && e.hasAttribute('hidden')) return true;
    }
    return false;
  };
  var out = [], seen = {};
  var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
  var n;
  while ((n = walk.nextNode())) {
    var el = n.parentElement;
    if (!el || el.closest('script, style, template')) continue;
    var text = (n.nodeValue || '').trim();
    if (!text) continue;
    PAT.lastIndex = 0;
    var m;
    while ((m = PAT.exec(text))) {
      var tok = m[0];
      if (seen[tok]) continue;
      seen[tok] = 1;
      // REPORTED EITHER WAY, with a flag, rather than dropped when it is hidden right now.
      // Visibility is a function of whatever state the harness happens to have put the
      // page in, and a reader presses controls the harness does not: the provenance footer
      // sits in #pFoot inside #pHw, which is display:none until you pick the Hardware
      // view -- so a visible-only probe called the live site clean while the path was one
      // click away.  Present in the DOM is the honest bar for "can a reader see this".
      out.push({ path: tok, hidden: hidden(el),
                 where: (el.id ? '#' + el.id : el.tagName.toLowerCase())
                        + (el.className && typeof el.className === 'string'
                           ? '.' + el.className.split(' ')[0] : ''),
                 text: text.slice(0, 120) });
    }
  }
  return out;
})()`;

/** One line per mention, for a harness's failure message. */
export function describe(hits) {
  return hits.map(h => `a reader sees a repository path: ${h.path} in ${h.where}`
                     + (h.hidden ? ' (hidden in this state, one control away)' : '')
                     + ` -- "${h.text}"`)
             .join("\n");
}

//: Plants a path in the page, to prove the probe finds one before its silence is believed.
//: A check that has never caught anything is not known to work -- which is how the string
//: above stayed on screen through a 203-page scan.
//:
//: The token is deliberately one no page would carry: the probe reports each DISTINCT path
//: once, so planting a path the page already shows proves nothing -- the count would not
//: move, and a calibration that expects it to would fail on the very page that needs the
//: check most.  (It did, the first time this was run.)
export const PLANT = `(() => {
  var p = document.createElement('p');
  p.id = 'plantedPath';
  p.textContent = 'from qccd/does_not_exist/planted_probe.py:1';
  document.body.appendChild(p);
  return true;
})()`;

export const UNPLANT = `(() => {
  var el = document.getElementById('plantedPath');
  if (el) el.remove();
  return !document.getElementById('plantedPath');
})()`;
