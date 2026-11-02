# Debugging a program step by step

The website's **Debug page** (`/debug/`, in the site bar after Design) opens any program the
site shows and lets you take it apart: step forward and back, watch ions and places, stop at
breakpoints, and check it against the website's rules or your own assertions.  Every failure
is crossed out in red on the picture, at the site, junction, rail or ion it is about, for as
long as it lasts.  Agents get the same thing twice: on the page (`qccd_page_act` action
`debug`) and headless (`qccd_debug`).

## Opening a program

* **From any Studio page**, press **Debug** in the site bar: a leaderboard entry, a Reproduce
  run, a compiled example or a lesson's page opens in the debugger at the step you were on.
  From the Design page, your own design and program come along.
* **On the Debug page**, press **Open**: a program from the website (every Studio page the site
  build wrote, searchable), your design (the one the Design page last saved in this browser),
  the Design page itself (design and write a program in the frame, then check it there), or a
  file: a Studio page saved from the site, a saved design (`.studio.json`, `.arch.json`), or a
  device and its program together (choose the `.arch.json` and the `.tsir.json` at once; the
  page draws them as the site would).
* **A link**: `debug/?src=board/surface3_mem/02_ring16.html&step=40&watch=q3,J1&rules=standard&assert=...&check=1`
  opens exactly that. The Report tab writes this link for you.

The program runs in its own Studio page, unchanged, inside the frame: whatever the Studio's
status line says ("Step 7 / 311") is the step the debugger is on.  **Steps count from 1**,
everywhere: the page, the agent's verbs, `python -m qccd.debug` and `qccd_debug`.

## What you can do

| | |
|---|---|
| step | ⏮ ◀ Step [n] ▶ ⏭, the arrow keys, Home/End, or a click on the timeline |
| state | the State tab: the instruction, what moved, places over capacity, the hottest ions' n̄, what fails here, what you watch |
| watch | the Watch tab: ions, sites, junctions, rails by id, from the Studio's selection, or picked on the picture; each gets a colour, ions a trail; **Isolate** fades every other ion; ◆◀ ▶◆ jump to the steps where anything watched changes |
| expressions | the Watch tab: terms of the rule language evaluated at every step you visit, with `t` the instant after it: `occupancy("C0", t)`, `position("q3", t)` |
| rules | the Rules tab: a rule set the website ships (standard, one per paper) and/or your own assertions; **Check** judges the program and crosses out every failure; ✗◀ ▶✗ jump between failures |
| breakpoints | the Breakpoints tab: steps, conditions in the rule language over `t` (`occupancy("J0", t) > 0`), a rule starting to fail, something watched changing; **Continue ▶▶** runs to the next, ◀◀ to the previous |
| report | the Report tab: what the page shows as Markdown (program, rules, failures with places, this step, the watch list) and the link that reopens it exactly here |

### Assertions

The rule language of the website's Rules page (docs/rule-language.md; grammar in
`qccd/ruleset/syntax.py`, words in `qccd/ruleset/vocabulary.py`).  One formula per paragraph,
optionally led by a title:

    assert "one ion per site":
    for all s in sites, t in instants: occupancy(s, t) <= 1

    for all t in instants: position("q0", t) != "C3"

or whole rules (`rule MINE "..."` and an indented formula).  A quoted name stands for the ion,
site or rail of that name, as an argument too (`occupancy("C3", t)`).  Unknown words are
refused before anything runs.

## How a failure is placed

The rule language reports each counterexample as the values that make the formula false
(`s = C0, t = during step 5`).  `qccd.debug` turns those into:

* **the step**: an instant's step, else the step of the operation or hop it names; none for a
  rule about the device itself (drawn at every step);
* **how long**: a state that goes bad is reported once, where it goes bad; the debugger
  re-evaluates the same culprit at the following instants and keeps the cross until it holds
  again (`until`);
* **the places**: every site, junction, rail and ion the values name, an operation's site,
  rails and ions, a hop's rail and ends; when the values name none, the places the formula
  names (`occupancy("C0", t)`);
* **the sentence**: the grader's own, with its replay-step numbers turned into page steps.

On the picture a place gets a cross labelled with the rule ids (bold at the step the failure
starts, lighter while it lasts); an ion standing in a place the same failure names gets a red
ring.  Crosses within 16 px are one cross.

## For agents

**On the page** (the person watches): `qccd_page_act` with `action: "debug"`, `verb` and
one object in `args`:

    {"action": "navigate", "path": "/web/debug/?src=board/surface3_mem/02_ring16.html"}
    {"action": "debug", "verb": "check",  "args": [{"rules": "standard", "assertions": "for all s in sites, t in instants: occupancy(s, t) <= 1"}]}
    {"action": "debug", "verb": "failures", "args": [{"limit": 10}]}
    {"action": "debug", "verb": "goto",   "args": [{"step": 41}]}
    {"action": "debug", "verb": "watch",  "args": [{"add": ["q3", "J1"], "isolate": true}]}
    {"action": "debug", "verb": "find",   "args": [{"condition": "occupancy(\"J1\", t) > 1"}]}

The verbs are declared in `qccd/site/debug/debug_api.json` (verb `verbs` lists them); the
controls are declared with `data-hint` and `window.QCCD_HINTS`, so `click`/`fill` work too.

**Headless** (`qccd_debug`, `POST /api/debug`): the same session on a finished run (`run_id`,
which brings its certificate, so R24/R25 are judged) or on two workspace files (`arch`,
`program`, optional `certificate`).  Actions: summary, check, state, step, trace, find, value,
diff.  A compiler bug hunt is usually: `check`, then `state` and `trace` at the first failing
step, then `find` for the condition that led there.

**At a terminal**: `python -m qccd.debug PROG.tsir.json --arch DEV.arch.json check --assert '...'`
(also summary, state, step, trace, find, value; `--cert` for the circuit rules).

## How it is built

| piece | where |
|---|---|
| the model: steps, states, located failures, conditions, expressions | `qccd/debug/session.py` |
| JSON in/out for the page's worker | `qccd/debug/web.py` |
| the page: markup, config, the Python zip, the pinned runtime | `qccd/site/debugpage.py` |
| the page's script and style | `qccd/site/debug/debug.js`, `debug.css` |
| the worker (Pyodide, a module worker) | `qccd/site/debug/worker.js` |
| the agent verbs | `qccd/site/debug/debug_api.json` (+ `qccd/workspace/interface.py`, `web/pageact.js`) |
| the headless tool | `qccd/workspace/debugging.py`, `features.py` (`qccd_debug`) |

* **The Studio is not edited.**  The page hosts a Studio page in a frame and reads its tables
  through a small inline bridge script (getters for `P`, `states`, `IONP`, ...; no `eval`), wraps
  its `draw()` to repaint one `<g id="qdbg">` appended to its SVG, and seeks with its own `seek`.
  No stamped file changes, so no page needs regenerating and every page already on the site can
  be debugged.  If the Studio renames those bindings, the bridge says "this page has no Studio
  animation"; `tests/test_debug.py::test_the_page_in_a_browser` catches it.
* **The rules are the grader's own Python**, run by Pyodide `314.0.7` (Python 3.14), pinned by
  version and SHA-256 in `debugpage.py`; the site build downloads the release once into
  `out/debug-cache/` and copies five files to `debug/pyodide/` (about 13 MB, cached by the
  browser).  `debug/qccd-py.zip` (about 1 MB) is qccd's packages from the working tree (the template
  device from HEAD).  The verdicts in the browser are the native verdicts, held by
  `test_the_rule_language_runs_in_the_browser_runtime`.  The page exports the program with
  `EDITOR.exportPair()`, so the judged program is the one on screen (an edited design included).
* **Serving**: any static server.  qccd.academy's nginx (1.18) types neither `.mjs` nor `.wasm`,
  so the build ships the release's two ES modules as `pyodide.esm.js` / `pyodide.asm.js` and
  patches the loader to compile the wasm from its bytes instead of `instantiateStreaming` (which
  refuses an untyped wasm and then only logs a warning); `debugpage.PATCHES`, each edit checked to
  match exactly once.  `tests/debug_page.mjs` serves both untyped, as the droplet does.  Under the
  workspace mirror's CSP the page needs `'wasm-unsafe-eval'` (added to `mirror_csp`); without it the
  page says the policy refuses WebAssembly rather than hanging.
* **Limits**: a page shows at most 20,000 frames; a longer program's failures past the last frame
  are drawn on it.  The first check loads the runtime (a few seconds); a 5,000-step program takes
  about as long to check as the grader natively, two to three times over.

## Tests

`tests/test_debug.py` (the model against the grader, placement, quoted names, the glue, the
CLI, the headless tool, the page section, the runtime under Pyodide, the page in Chrome with and
without the mirror's policy) and `tests/test_agent_interface.py` (verbs and controls declared).
`node tests/debug_page.mjs <site> <studio page> [--csp ...] [--shots dir]` drives a built page.
`python -m qccd.site.debugpage -o DIR --pages SITE` builds the section alone over a site build.
