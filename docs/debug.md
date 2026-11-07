# Debugging a program step by step

The website's **Debug page** (`/debug/`, in the site bar after Design) opens any program the
site shows and lets you take it apart: step forward and back, watch ions and places, stop at
breakpoints, and check it against the website's rules or your own assertions.  Every failure
is crossed out in red on the picture, at the site, junction, rail or ion it is about, for as
long as it lasts.  Agents get the same thing twice: on the page (`qccd_page_act` action
`debug`) and headless (`qccd_debug`).

## Opening a program

* **On the program you are looking at**: every Studio page (a leaderboard entry, a Reproduce
  run, a compiled example, the Design page with your own unsaved design) has a **Debug** button
  beside its Play/Step controls, and the site bar's **Debug** link does the same there.  The
  debugger opens as a panel beside the program, at the step you are on: no other page, no
  reload.  The element rail folds while it is open; **×** (or Debug again) closes it and puts
  everything back.  A link with `#debug&step=12&watch=q3&check=1` opens a page that way.
* **On the Debug page** (`/debug/`), press **Open**: a program from the website (every Studio page the site
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
| rules | the Rules tab: a rule set the website ships (standard, one per paper) and/or your own assertions; **Check** judges the program and crosses out every failure; ✗◀ ▶✗ jump between failures; an open rule shows what its failure usually means and the fix, **Why, at this step** and **Closest case** |
| why | the Why tab: every rule at the step shown (how many cases it judges here, how many fail), and one rule's cases there, each with its variables, the places it names and the value of every part of the rule; it follows the step |
| notes | what an agent reviewing the program pinned to a step: in State at that step, beside the rule, as purple diamonds on the timeline and in the report; × removes one |
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

## Reviewing a program, rule by rule

A check says which rules fail and where.  A review also says why, and how close the rules that
hold came.  It happens in the same panel, for the person and for their agent alike.

* **The runtime error.**  At the step where a shown rule starts failing, the head of the panel says
  so, the way a debugger stops on an exception: the rule, the step, the comparison that broke
  (both numbers and a gauge), the case's variables, **Why?** and **How to fix it**.  Continue ▶▶
  stops there when "stop where a shown rule starts failing" is on.
* **Why** (`Session.rules_at`, `Session.inspect`).  `check` walks only the cases that can fail,
  which is what a verdict needs.  To show a rule at work, these walk every binding of its `for all`s
  (through `and`s, and an `implies` whose condition holds) with the variables that range over time
  -- instants, operations, steps -- held to the step shown.  A case that names no moment (a corner,
  a rail) is the same at every step and says so.  Each case carries the value of every part of the
  rule's body: a comparison with both sides and the **room** left (its limit minus its value, for
  `<=`), the false part of a failing case in red.  The failing cases come first, then the closest
  to failing.  A budget (`CASES_PER_STEP`) keeps one step's walk bounded.
* **Gauges.**  The comparison that decides a case -- the false one if it fails, else the one with
  the least room -- is drawn beside the ion or place it names: the value as a bar, the limit as a
  tick.  Failures carry theirs (`Failure.gauge`), so the error and the picture show it at once.
* **The closest case** (`Session.closest`): of every case a rule judges in the whole program, the
  one that holds with the least room -- the hottest gate under the heat budget, the fullest site
  under its capacity.  It searches up to `CLOSEST_BUDGET` cases and says when it stopped early.
* **The review guide** (`qccd/debug/guide.md`, `qccd.debug.guide()`): what a rule's failure usually
  means and what to change, one row for every rule of every shipped set (a test fails until a new
  rule has one).  It is written for a person and read the same way by their agent: the panel shows
  a rule's row beside its failure, and the workspace skill carries the whole file.  Rows name no
  file of the repository.
* **The only verdict is the grader's.**  The review explains the grader's verdicts; it adds none.

**With your own agent.**  **Review with my agent** (Rules tab) writes a review request into the
chat to the person's own agent (Codex, Claude Code or Cursor) -- in their workspace's Studio, on a
run's page, or on a website page opened through their workspace (`qccd web`).  The person sends it;
a page never sends for them (on the website mirror the page is another, untrusted origin).  On
qccd.academy itself, the button says how to open the page with their agent.  The agent then works
in the same panel, through the declared verbs below, while the person watches its cursor: every
verb shows what it returns.

## For agents

**On the page** (the person watches): `qccd_page_act` with `action: "debug"`, `verb` and
one object in `args`:

    {"action": "navigate", "path": "/web/debug/?src=board/surface3_mem/02_ring16.html"}
    {"action": "debug", "verb": "check",  "args": [{"rules": "standard", "assertions": "for all s in sites, t in instants: occupancy(s, t) <= 1"}]}
    {"action": "debug", "verb": "failures", "args": [{"limit": 10}]}
    {"action": "debug", "verb": "goto",   "args": [{"step": 41}]}
    {"action": "debug", "verb": "watch",  "args": [{"add": ["q3", "J1"], "isolate": true}]}
    {"action": "debug", "verb": "find",   "args": [{"condition": "occupancy(\"J1\", t) > 1"}]}

A review:

    {"action": "debug", "verb": "review",  "args": [{}]}                          every rule, its first failing step, the guide's row
    {"action": "debug", "verb": "inspect", "args": [{"rule": "R7", "step": 41}]}  the cases there, shown in Why
    {"action": "debug", "verb": "note",    "args": [{"step": 41, "rule": "R7", "text": "q3 reaches the gate after three junction crossings: cool it at C4"}]}
    {"action": "debug", "verb": "closest", "args": [{"rule": "R1"}]}              a near miss of a rule that holds

The verbs are declared in `qccd/site/debug/debug_api.json` (verb `verbs` lists them); the
controls are declared with `data-hint` and `window.QCCD_HINTS`, so `click`/`fill` work too.
A note is signed with the agent's name (`pageact.js` adds it).  A Studio page built before a verb
existed carries a stand-in without it; the page action opens the panel first, which loads the
debugger with every verb.

**Headless** (`qccd_debug`, `POST /api/debug`): the same session on a finished run (`run_id`,
which brings its certificate, so R24/R25 are judged) or on two workspace files (`arch`,
`program`, optional `certificate`).  Actions: summary, check, state, step, trace, find, value,
diff, rules_at, inspect, closest.  A compiler bug hunt is usually: `check`, then `inspect`,
`state` and `trace` at the first failing step, then `find` for the condition that led there.

**At a terminal**: `python -m qccd.debug PROG.tsir.json --arch DEV.arch.json check --assert '...'`
(also summary, state, step, trace, find, value; `--cert` for the circuit rules).

## How it is built

| piece | where |
|---|---|
| the model: steps, states, located failures, conditions, expressions | `qccd/debug/session.py` |
| JSON in/out for the page's worker | `qccd/debug/web.py` |
| the page: markup, config, the Python zip, the pinned runtime; the loader every Studio page carries | `qccd/site/debugpage.py` (`LOADER`, `with_loader`) |
| the script (both hosts: `CFG.host` frame or inpage) and the styles | `qccd/site/debug/debug.js`; `panel.css` (the controls, all under `#dbg`), `debug.css` (the Debug page), `inpage.css` (the panel in a Studio page) |
| what a Studio page loads when Debug is pressed | `debug/inpage.js` (= debug.js), `debug/inpage.css`, `debug/config.json` |
| the worker (Pyodide, a module worker) | `qccd/site/debug/worker.js` |
| the agent verbs | `qccd/site/debug/debug_api.json` (+ `qccd/workspace/interface.py`, `web/pageact.js`) |
| the headless tool | `qccd/workspace/debugging.py`, `features.py` (`qccd_debug`) |
| the review guide | `qccd/debug/guide.md` (hand-written), `qccd/debug/guide.py`; the page gets it in `config.json`, the skill as `references/review-guide.md` |
| the panel in the person's workspace | `debugpage.workspace_inject` (the Studio and every run's page, `service.py`), its Python behind `POST /api/debug/web` |

* **In a Studio page**, the site build appends `LOADER` (after the hash router): a Debug button in
  `#stagebar .ctrl` and a listener on the bar's Debug link, both of which load `debug/inpage.js`
  on first use and open the panel; stub `QCCD_DEBUG` verbs make an agent's first verb do the same.
  The panel is `#dbg.inpage`, fixed at the right; `body[data-qdbg]` gives `main` a right margin and
  the Studio re-lays itself out (`relayout()`).  Every class the debugger uses is `q-` prefixed: the
  Studio styles `.pane`, `.bad`, `.ok`, `.mut`, `.row`... and its rules would otherwise reach the panel.
  Plain arrow keys stay the Studio's (the panel follows its `draw()`); Shift/Alt+arrows and F8 are
  caught before the Studio, which would read Shift+Right as one more step.
* **The Studio is not edited.**  The Debug page hosts a Studio page in a frame and reads its tables
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
* **In the person's workspace** (their Studio, a run's page) the panel's code is on the page itself
  (the workspace's pages load nothing from elsewhere) and its rule language is the workspace's own
  Python: `CFG.python.backend` is `service`, and the worker's calls go to `POST /api/debug/web`,
  which runs the same `qccd.debug.web` functions.  Nothing is downloaded, and the answers are the
  ones the site's pages compute in the browser.
* **Limits**: a page shows at most 20,000 frames; a longer program's failures past the last frame
  are drawn on it.  The first check loads the runtime (a few seconds); a 5,000-step program takes
  about as long to check as the grader natively, two to three times over.

## Tests

`tests/test_debug.py` (the model against the grader, placement, quoted names, the glue, the
CLI, the headless tool, the page section, the runtime under Pyodide, the page in Chrome with and
without the mirror's policy; a rule at each step, its cases and their gauges, the closest case,
the guide's row for every rule, a review in Chrome on both hosts, and the workspace Studio's
panel handing a review to the agent's chat) and `tests/test_agent_interface.py` (verbs and
controls declared).
`node tests/debug_page.mjs <site> <studio page> [--csp ...] [--shots dir]` drives a built page.
`python -m qccd.site.debugpage -o DIR --pages SITE` builds the section alone over a site build.
