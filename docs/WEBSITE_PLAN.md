# The QCCD website — plan

**Goal.** One website where anyone can learn the platform, design a QCCD computer in the
browser, compile a QEC circuit onto it, verify the result, post it to a leaderboard, and
discuss the rules. Two principles fix every decision below:

1. **As simple and elegant as possible.** One studio page is the whole design tool; one
   ranking page is the whole leaderboard; GitHub is the whole back office.
2. **Design and verification happen in the browser, never on a server.** The site is
   static files. There is no server of ours: no accounts, no database, no API. What must
   run "somewhere" runs either in the visitor's browser or in GitHub's CI on a pull
   request, and CI only re-runs what the browser already ran.

Written 2026-09-09 against the `compiler` branch. Sections: what exists (§1), the site
(§2), each of the four parts (§3–§6), the submission format (§7), repository layout (§8),
phases with acceptance tests (§9), decisions (§10), do-not-do (§11), the prompt for the
executing session (§12).

---

## 1 · What already exists, and what the website reuses unchanged

| Piece | Where | State | Reused as |
|---|---|---|---|
| The studio page: device editor, program pane, animation, pricing, Report pane, tools bar, search, hover hints, cheat sheet | `qccd/viz/render.py`, `qccd/viz/engine.js`, `qccd/viz/js/editor.js` (built by `python -m qccd studio`) | shipped; one self-contained HTML | the **Design** app and the **Learn** app |
| The course: 31 lessons, Parts A/B/R/C/D, checks over page verdicts, progress in `localStorage` | `qccd/viz/js/tutorial.js`, Learn tab; plan in `docs/TUTORIAL_PLAN.md` | phase 0 shipped (Part A); B/R/C/D per that plan | the **Learn** tab, untouched |
| Browser rule set: 21 of 27 rules with a differential parity test against Python | `BROWSER_SET` in `render.py`; `tests/test_engine_parity.py` | shipped | the v1 verifier's rule half |
| Python verifier: all 27 rules, `qccd open` (the return leg for a `qccd.studio` artifact) | `qccd/verify/rules.py`, `qccd/__main__.py` | shipped | CI's re-check, and the parity oracle for the six rules still to port |
| The compiler: QASM → TSIR + certificate | `Compiler/ocaml` (5.3 k lines OCaml, yojson only; file I/O in `qasm.ml`, `cert.ml`, the CLI) | shipped as `qccdc_cli.exe` | compiled to JavaScript with js_of_ocaml (§4.3) |
| The proved checker (R10) | `Compiler/lean` — `QCCDC.Cert.check`, `check_sound`; `qcheck` exe imports Lean core only | shipped; ~10 s for small codes, 15 min / 2 GB for BB | compiled to WebAssembly (§4.4); the soundness proof is checked once, in CI |
| Checker input assembly, stim check, cost, buildability | `Compiler/bridge/mk_qcheck_input.py`, `check_cert.py`, `small_codes.py`, `q06_campaign.py` | shipped (Python) | ported where the browser needs them (§4.2), else CI |
| The ranking page: white page, one plot, metric pickers, dot per design, click for details, table view, `?y=&x=&sel=` URLs | `write_index` in `Codesign/scripts/bb_studio.py` → `BBResults/studio/index.html`, `SmallCode/*/index.html` | shipped | the **Leaderboard** page, one per task |
| Five tasks with results: BB[[144,12,12]] (29 designs), repetition d = 9, five-qubit, Steane, surface-17 (15 designs each) | `BBResults/`, `SmallCode/` (`rows.json`, `manifest.json`, pages) | shipped | the leaderboards' seed entries |
| Reference docs: device language, control IR, rules, physics | `docs/adl.md`, `tsir.md`, `rules.md`, `phys.md` (823 lines) | shipped | the **Learn** reference, rendered to HTML |
| Browser test harness: headless Chrome + CDP, `tests/studio.mjs`, `tests/drive.mjs` | `tests/` | shipped | CI's browser re-verification (§6.3) |
| The repository on GitHub | `github.com/yezhuoyang/QCCD` | exists; no `.github/` yet | Pages hosting, Discussions, Issues, Actions |

Nothing in the website is a second implementation of any of these. The website is a
build target that arranges them, plus the few missing pieces named in §4.

## 2 · The site

Four parts, four top-level pages, one navigation bar. Static files in a `site/` directory
that `python -m qccd site` builds, served as they are by a web server of our own (§10.1).

```
/                     Landing: one sentence, four tiles (Learn · Design · Leaderboard · Discuss)
/learn/               The studio page opened on the Learn tab (#learn), with the reference
                      docs and the instruction cheat sheet in the same tools bar
/design/              The same studio page, blank device, ending in Verify → Contribute
/board/               The five tasks; /board/<task>/ is the ranking page; /board/<task>/<entry>/
                      is that entry's studio page (the BBResults/SmallCode pages, as they are)
/discuss/             GitHub Discussions embedded (giscus); categories Rules · Bugs · Features
/docs/<name>/         adl · tsir · rules · phys · cheat sheet, rendered from the Markdown
```

**One page, not two.** Learn and Design are the same built HTML file (`studio.html`)
opened with a different hash: `#learn` opens the Learn tab, `#design` the blank editor,
`#task=steane` the editor with that task's circuit loaded. The tutorial already runs inside
the design tool; the website does not separate them.

**Visual identity.** The ranking page's white style is the site's style: white ground,
one accent per family (ring #2a78d6, lattice #eb6834, torus #1baf7a, random graph
#4a3aa7, grey #8a8985 for the rest — the validated palette), system font, no cards. The
landing page is four tiles and a sentence; nothing scrolls.

**Navigation.** A 40 px bar at the top of every page: the four parts and a search box
that is the studio's existing Ctrl-K search extended over the docs and lessons. On the
studio page the bar sits above the tools bar; nothing else in the studio moves.

## 3 · Learn

What the visitor can do: read the course, do its exercises in the live editor, read the
four reference docs, open the cheat sheet, open any leaderboard entry and step through
its programme instruction by instruction.

- **The course** is `tutorial.js`, unchanged. Its remaining parts (B, R, C, D of
  `TUTORIAL_PLAN.md`) ship on that plan's schedule; the website links whatever exists.
  Part D (compile and evaluate) points at the leaderboard entries as its worked examples,
  which it already does by companion pages.
- **The reference** is the four Markdown docs rendered to HTML by the site build with the
  same hover vocabulary the studio uses (every term with a `data-hint` in the studio gets
  the same hint in the docs — the HINTS table is exported once as JSON and both consume
  it). The instruction cheat sheet becomes a fifth doc, generated from the same `OPS`
  table that drives the hover hints, so it cannot drift.
- **Worked examples** are the leaderboard entries: every entry page has the executing card,
  the highlighted listing, `#step=N` deep links and the qubit labels. A lesson can link to
  `/board/steane/01_cyclone_base/#step=40`.
- **The learning path** on `/learn/` is one column: Part A → B → R → C → D, then the docs,
  then "open a real design". Progress comes from the course's own `localStorage` store.

Nothing new is written for Learn beyond the doc renderer (~80 lines Python: Markdown →
HTML with the hint spans) and the landing column.

## 4 · Design and verify — all in the browser

The visitor designs a device, writes or compiles a programme for a chosen circuit, and
gets a verdict card. Every step below runs in the page.

### 4.1 What the studio already does
Build the device from parts or generators; write a programme in the eleven verbs; replay
and price it; report 21 rules live; export a `qccd.studio` artifact (device + program
records + edits). That is Design today.

### 4.2 Verification, version 1 ("basic")
The verdict card has four lines, each computed in the page:

1. **Well-formed** — the device is a valid ADL document and the programme parses (the
   engine already refuses otherwise).
2. **Rules** — all 23. Seventeen exist in `engine.js`; port the remaining six as
   browser twins under the parity discipline that already governs the others
   (`test_engine_parity.py` runs both sides on every shipped device and every leaderboard
   entry and requires identical verdicts): **R4d** (~150 lines, the twin
   `TUTORIAL_PLAN.md` §6 already scoped), **R7b**, **R9**, **R15**, **R16**, and **R10-basic**.
3. **R10-basic** — "the programme implements the circuit", without Lean: every circuit
   op is realised exactly once, per-qubit order is preserved, each two-qubit gate fires
   with both ions in one gate-capable trap, and for Clifford circuits (all five tasks) a
   stabilizer-tableau simulation in JavaScript (~250 lines, the same check `stim` does in
   `small_codes.py`) confirms the emitted gate sequence maps the circuit's stabilizers to
   themselves. The card says "R10 (basic)" until the proved checker says "R10 (Lean)".
4. **Score** — total time on both cost tables, ions per instruction, DACs, area and
   buildability, using the page's existing pricing (the `score()`/`buildability()`
   functions of `q06_campaign.py` move to `engine.js` under a parity test, ~120 lines).

The `qccd open` command stays as the command-line return leg and as CI's oracle: it must
agree with the card on every entry, which is the parity test.

### 4.3 Compilation in the browser
The compiler is 5.3 k lines of OCaml over yojson with no Unix dependency. Build it with
**js_of_ocaml** into one `qccdc.js` (expected 1–2 MB) that runs in a Web Worker:

- add `Qasm.parse_string` and an in-memory `Compile.run : arch_json -> qasm -> options ->
  (tsir_json, cert_json)` entry (the CLI's `compile`/`rotate` bodies without the file
  reads/writes, ~60 lines), keep `qccdc_cli.exe` as is;
- a `dune` rule with `(modes js)` for a second executable `qccdc_js` exposing `run` on
  `globalThis`;
- the studio's Program pane gains a **Compile** action: pick the circuit (a task's QASM or
  a pasted one), pick rotation/general routing and the placement, run in the worker,
  receive TSIR + certificate, load the TSIR as the programme (the page already renders
  TSIR: every leaderboard page is `render_html` over TSIR).

Cooling insertion (`insert_cooling.py`) and the checker-input assembly
(`mk_qcheck_input.py`, the trap-hop BFS that must not come from the compiler) are ported
to JavaScript (~200 lines together) with parity tests against the Python originals.

Until the js_of_ocaml build exists, Design accepts an uploaded TSIR compiled locally with
the CLI; verification still runs in the page. That is the fallback, not the target.

### 4.4 Verification, version 2 — the proved checker in the browser
`qcheck` imports only Lean core and reads one JSON file. Lean 4 compiles to C; compile
that C with the Lean runtime under Emscripten to `qcheck.wasm` (~5 MB) and run it in the
same worker on the certificate assembled in 4.3. The page then reports **R10 (Lean)**
for a submission without any server. The soundness theorem `check_sound` is checked by
Lean once, in CI, whenever `Compiler/lean` changes — that is a property of the tool, not
of a submission, and is the one thing that never runs per user.

Memory is the known limit: the native checker needs 2 GB for a 5,081-move BB certificate
and fails at 6,319. In WebAssembly (4 GB address space, browsers allow ~2 GB) BB-sized
certificates will not check in the page. Rule: the page checks what it can (all small
codes; BB rings up to ~3 k moves) and marks larger ones "R10 (basic); Lean in CI". The
first improvement to make, once a profile exists, is the checker's memory (its move set is
consumed as a list) rather than the site.

### 4.5 Version 3 — user-supplied Lean proofs
Later, a submission may carry its own Lean development: a proof that *this* compiled
programme implements *this* circuit and obeys the rules, stated against `QCCDC` as a
library. A Lean kernel does not run in the browser today; such proofs are checked by
`lake build` in the pull-request CI, with the result written into the entry's badge.
This is the single exception to the browser-only principle, stated as such on the page,
and it is optional: an entry without a proof keeps its "R10 (Lean checker)" badge.

## 5 · Leaderboard

One board per task. A task is a fixed circuit, a fixed physics package and cost table,
and a fixed set of metrics. The five tasks exist with their seed entries. The board page
is `write_index` — the plot, the metric pickers, the dots, the click-through — generated
from a `rows.json`. Nothing about the page changes; what changes is where rows come from.

**A row is a submission** (§7) that CI verified. The default ranking is total time on the
jones table among entries whose 27 rules pass and whose R10 badge is Lean; the pickers
still let a visitor rank by anything.

**Contributing.** From the verdict card, **Contribute** writes one file,
`submission.json` (the `qccd.studio` artifact plus the circuit id, the TSIR, the
certificate and the page's verdicts), and opens the repository's "New submission" issue
form with the file attached and the task and author name pre-filled. A GitHub Action on
that issue validates the file, re-verifies it (§6.3), builds the entry page, opens a pull
request adding it under `submissions/<task>/<author>-<name>/`, and comments the verdicts
on the issue. Merging the PR publishes the entry; the board rebuilds on push. No account
beyond GitHub, no upload endpoint, no server.

**Badges on a dot.** Each dot carries rules (23/23), R10 level (basic · Lean checker ·
Lean proof), buildability, and the author. The hover card and the details pane show them;
the plot never encodes them in colour (colour stays the device family).

## 6 · Discuss, and how the back office works

### 6.1 Discussion
GitHub Discussions on the repository, embedded on `/discuss/` with giscus (a static
script; no server of ours). Categories: **Rules** (proposals to add, drop or reword a
rule, each linking the rule's doc anchor), **Bugs** (with a link to the Issues template
that asks for the `qccd.studio` artifact), **Features**. Every board entry page gets a
giscus thread keyed by its path, so a design can be discussed under itself.

### 6.2 Rule changes
A rule change is a pull request touching `docs/rules.md`, `rules.py`, `engine.js` and
the parity test together; CI re-runs every board entry against the new set and the PR
shows which entries change verdict. That is the whole governance: the rules are code,
the discussion is a Discussion, the decision is a merge.

### 6.3 CI (GitHub Actions)
- `site.yml` — on push to `main`: `python -m qccd site`, the studio harness on the built
  page, then `rsync` of `site/` to the server named by the repository's `DEPLOY_*` settings.
- `submission.yml` — on the submission issue form: validate, run the browser bundle
  headless (the `studio.mjs` harness driving the same `studio.html` the visitor used) and
  `qccd open`, require both verdict sets equal to the submitted ones, run `qcheck`, build
  the entry page, open the PR.
- `checker.yml` — on changes under `Compiler/lean`: `lake build` (the soundness proof),
  rebuild `qcheck.wasm`; under `Compiler/ocaml`: rebuild `qccdc.js`. Both artefacts are
  committed to `site/static/` by the workflow so the site build never needs a toolchain.
- `parity.yml` — on every PR: `test_engine_parity.py` over shipped devices and all board
  entries; the Node harnesses (1 s each) on every page the site builds.

## 7 · The submission format

One JSON file, produced by the browser, readable by the CLI:

```json
{ "kind": "qccd.submission", "version": 1,
  "task": "steane",                       // one of the five task ids
  "author": "github-login", "name": "cyclone with 8 docks",
  "studio": { ...the qccd.studio artifact: arch, program records, geom, edits... },
  "tsir": { ...the compiled programme, cooled... },
  "cert": { ...the compiler's certificate... },
  "verdicts": { "rules": {"R1": "pass", ...}, "r10": "lean-checker", "score": {...} },
  "tool": { "site": "<git sha>", "qccdc": "<sha>", "qcheck": "<sha>" } }
```

CI trusts none of `verdicts`; it recomputes them and rejects on any difference. A task is
defined by `tasks/<id>/task.json` (circuit file, physics package, cost tables, metrics,
title) and `tasks/<id>/circuit.qasm`; the five existing tasks are written in that form
from `small_codes.py` and `bb_studio.py`, whose `codes()`/`CATALOG` become readers of it.

## 8 · Repository layout (new files only)

```
qccd/site/                 build.py (python -m qccd site), md.py (the docs renderer), nav.html,
                           landing.html, giscus.json
site/                      the built output (gitignored; the workflow copies it to the server)
tasks/<id>/                task.json, circuit.qasm; `seed` names the rows (BBResults/SmallCode today)
tests/site.mjs             every built page in headless Chrome: exceptions, the bar, the deep links
submissions/<task>/<entry>/  submission.json + the built entry page inputs
Compiler/ocaml/bin/qccdc_js.ml   the js_of_ocaml entry (+ dune stanza)
Compiler/lean/wasm/        the Emscripten build script for qcheck.wasm
qccd/viz/js/verify.js      R4d/R7b/R9/R15/R16 twins, R10-basic (tableau), score(), cooling, hop BFS
qccd/viz/js/compile.js     the worker glue around qccdc.js and qcheck.wasm
.github/workflows/         site.yml, submission.yml, checker.yml, parity.yml
.github/ISSUE_TEMPLATE/    submission.yml (the form), bug.yml
docs/WEBSITE_PLAN.md       this file
```

`BBResults/` and `SmallCode/` stay where they are until the tasks directory carries
their rows; then the site build is the only thing that renders them.

## 9 · Phases and acceptance tests

| Phase | Delivers | Done when |
|---|---|---|
| **0 · Skeleton** | `python -m qccd site` producing landing, nav, `studio.html` with `#learn`/`#design`, docs rendered, the five boards from existing rows, giscus page; `site.yml` deploying to Pages | the site is live at https://qccd.academy/; every existing board entry opens from it; the Node harness passes on every built page; a screenshot walkthrough of the four parts |
| **1 · Verify v1** | `verify.js`: the six rule twins, R10-basic with the tableau, `score()`; the verdict card; parity tests green over shipped devices and all 89 board entries | `qccd open` and the card agree on every entry; the R10-basic tableau agrees with stim on all five tasks; a deliberately broken programme is caught on the card |
| **2 · Compile in the page** | `qccdc.js` via js_of_ocaml in a worker; Compile action; cooling and hop-BFS in JS with parity | compiling each task's circuit onto each seed device in the page gives byte-identical TSIR and certificate to the CLI |
| **3 · Contribute** | `submission.json`, the issue form, `submission.yml` producing a PR, the entry page and badge | a submission made in a fresh browser appears on the board after merge with the verdicts CI recomputed |
| **4 · Lean in the page** | `qcheck.wasm`, "R10 (Lean checker)" on the card; `checker.yml` | every small-code entry checks in the page in < 30 s; BB entries fall back as specified in §4.4 |
| **5 · Proofs** | the user-supplied Lean proof slot, checked in PR CI, third badge level | one entry with a proof merged end to end |

Phase 0 is the one to review first. Phases 1–2 are independent of each other and can run
in parallel sessions; 3 needs 1; 4 needs 2; 5 needs 4.

## 10 · Decisions to confirm

1. **Hosting.** Decided 2026-09-09 morning: GitHub Pages from the same repository.
   **Revised 2026-09-09 during phase 0: a server of our own, not GitHub.** The site stays
   static files. **Named 2026-09-09 evening: the DigitalOcean droplet at 165.232.55.161**,
   the host that already serves alphata.org, qprogram.org, paperpilot.it.com and
   sfplseucla.org behind one nginx. The site lives in `/var/www/qccd.academy`, owned by
   the user `qccd`; the nginx site is `/etc/nginx/sites-available/qccd.academy` (port 80
   now; certbot adds 443 once DNS points here). The domain is **qccd.academy**
   (Namecheap); its A records for `@` and `www` point at 165.232.55.161 since the same
   evening, and certbot issued the certificate for both names (auto-renewing; HTTP
   redirects to HTTPS). **The site is live at https://qccd.academy/.** `site.yml` builds
   `site/`, runs the studio harness on it, and rsyncs as `qccd` with the repository secret
   `DEPLOY_KEY` (the private half of `~/.ssh/qccd_deploy` on the machine that set the
   server up); the host key is pinned in the workflow. GitHub stays the back office:
   Issues, Discussions, Actions.
2. **Submission channel.** Decided: issue form + Action-opened PR. A contributor needs a
   GitHub login and nothing else; the Action does the fork-free part.
3. **R10 levels on the board.** Decided: three badges as in §5, all entries shown. The
   default ranking is time on the jones table among entries with 27/27 rules and a badge of
   Lean checker or better; the others stay on the plot (hollow, as today) and in the table.
4. **Compiler in the browser.** Confirmed: §4.3 supersedes `TUTORIAL_PLAN.md` §10 for
   Design; the course does not depend on it.
5. **Naming.** Decided: the site is **QCCD studio** (the bar says `QCCD studio`); the task
   ids are `bb144`, `rep9`, `five_qubit`, `steane`, `surface17`, each `tasks/<id>/task.json`
   with its `circuit.qasm` (`rep9` seeds from `SmallCode/repetition`).

## 11 · Do not do

- No server, no database, no accounts, no upload endpoint. If a feature needs one, it is
  the wrong feature. **Revised 2026-09-14:** the one exception is the comment layer the
  user asked for (accounts by email and password, notes pinned to any spot of any page,
  visible to signed-in readers only, an admin who deletes anything): `qccd/site/comments_api.py`
  behind nginx at `/api/`, see the status entry below. Design and verification still
  never touch it; a copy of the site served without it is the site with a Sign-in button
  that leads nowhere.
- No second implementation of a rule, a price or a checker for the website; every browser
  piece is a twin under a parity test, or the original compiled to the browser.
- No trust in a submitted verdict; CI recomputes everything it publishes.
- No framework. The site is the studio page plus static HTML the build writes; the only
  third-party script is giscus.
- No change to the studio's look for the website beyond the navigation bar.

## 12 · Prompt for the executing session

```
Read docs/WEBSITE_PLAN.md and execute it, phase by phase, starting with phase 0.

Context you need before touching anything:
- The repository is a shared working tree with concurrent sessions: never stash, checkout
  or clean; treat other sessions' uncommitted files as read-only. Commit only your own
  files, on a branch named website/<phase>.
- The studio page is built by `python -m qccd studio`; its sources are qccd/viz/render.py,
  qccd/viz/engine.js, qccd/viz/js/editor.js, qccd/viz/js/tutorial.js. Coordinate any change
  to them with the studio session (see the memory notes on the tools bar and parity drift);
  prefer adding files (qccd/viz/js/verify.js, compile.js) over editing those four.
- The ranking page generator is write_index in Codesign/scripts/bb_studio.py; the task
  results are BBResults/studio/rows.json and SmallCode/<code>/rows.json (manifest.json
  beside each). The entry pages already exist under those directories.
- The OCaml build needs the opam 4.14.1 switch on PATH:
  export PATH="/c/Users/yezhu/AppData/Local/opam/4.14.1/bin:$PATH"; cd Compiler/ocaml; dune build
- The Lean checker is Compiler/lean (lake exe qcheck <input.json>); run one at a time, it
  needs 2 GB for BB-sized certificates.
- Browser verification: headless Chrome via tests/studio.mjs and tests/drive.mjs (1 s per
  page); the pytest suite takes ~80 min, run it only at the end of a phase.
- Follow the two principles: as simple and elegant as possible; all designing and
  verification in the browser, never on a server. Do-not-do list in §11.

For each phase: implement, run the acceptance test named in §9, take a screenshot
walkthrough with headless Chrome, update the Status section at the end of
docs/WEBSITE_PLAN.md with the date, what shipped and what was learned, and stop for
review before the next phase. Hosting is decided: GitHub Pages from this same repository
(§10.1). In phase 0 settle the remaining decisions in §10 by choosing the recommended
option unless I say otherwise, and write the choices into §10.
```

## Status

### Phase 0 · Skeleton — 2026-09-09, branch `website/0`

**Shipped.** `python -m qccd site` (`qccd/site/build.py`, ~330 lines, and `md.py`, ~170)
writes `site/` in 7 s: the landing; `studio.html` with `#learn`, `#learn=<lesson>` and
`#design` (a 20-line hash handler appended after render; nothing in the studio's sources
changed); `learn/` as one column — the parts and lessons with the course's own stars read
from `localStorage`, the four docs, the fastest verified entry of every task; `design/`
(a redirect); `docs/{adl,tsir,rules,phys}/` rendered with the studio's hover vocabulary
(rule ids everywhere, the first mention of each element per doc; the sentences are read
out of `editor.js` at build time); `board/` (five task cards) and `board/<task>/` (the
existing ranking page and its entry pages byte-for-byte, plus the manifest); `discuss/`
(giscus, dormant until the category ids exist). One 40 px bar on every page with a site
search over parts, lessons, doc headings, tasks and entries (169 entries, inlined, so it
works from `file://` too); Ctrl+K reaches it on pages without a search of their own.
Restructured the same evening on request: the landing is a two-column hero with the
five-qubit code on the 24-site ring running live — the entry page itself in an iframe, in an
**embed mode** every app page now has (`#embed&step=N` in the injected hash handler folds the
head, tools bar, rail, dock and study card away and loops the transport), linking into the
entry at `#step=2`; a GIF was tried first and dropped because the user wants what the page shows; Learn is one row per part
with a pictogram and a sentence each, then the four docs as cards and the fastest verified round
of every task; the board is one row per task with the designs as bars coloured by family, the
fastest verified in green, refusals and rule failures in red, and four stat tiles. Every example on the Language, Rules and Compilation pages is now shown in place (2026-09-10 evening):
the example's page sits in a lazily loaded frame, in embed mode, paused at its first step with
the transport controls ready, so the device and its ions are visible before any click and Play
runs it; only the landing's frame autoplays (`#embed&play`). The Compilation page became one
full-width row per gate — the circuit drawn the textbook way (an SVG from the certificate's
parsed ops: wires, boxes, the CNOT dot and crossed circle, the meter), the input, the mapping,
the pulses and the verdicts on the left, the machine and the hardware programme on the right.
A second session added Physics, People and Publications pages to the same build module that
day; they are carried on this branch so it builds. A third expert page, **Compilation** (`compilation/`, 2026-09-10): the accepted QASM gates and
the native set they lower to, the pipeline in nine stages (parse, lower, place, route, emit, cool,
rules, R10's two halves, draw), the Bell pair's ion mapping and pulse witnesses, and every basic
gate compiled onto the six-site ring and verified — the hardware programme, the mapping, the
pulses, the rules and R10 by the Lean checker and the tableau or exact unitary — each runnable
with the circuit beside the programme. The artefacts come from the real toolchain once, by
`python -m qccd.site.compile_examples` (OCaml compiler, cooling pass, check_tsir, mk_qcheck_input,
check_cert with qcheck), and live in `qccd/site/compiled/` (410 KB) so the site build needs no
toolchain. Two expert pages were added on request (2026-09-10): **Language** (`language/`), the hardware
language in PL style — an EBNF of the Write pane's syntax, the machine state and its transitions,
and every one of the twelve statements with its signature, meaning, cost, the rules that judge it,
the IR it becomes, and a running example — and **Rules** (`rules/`), all 27 rules with the
verifier's statement, what it checks, its sources, and a programme that passes and one that fails
(or, for R7b, R10, R15 and R18, what the verifier honestly reports instead). Both live in
`qccd/site/examples.py` as the records the Write pane produces, so the text shown is what ran;
each example is rendered by `Machine.render` into a real page under `language/ex/` and
`rules/ex/` (55 pages, 60 MB) and opens in place, in embed mode, from a Run button; the verdicts
are quoted from `qccd.verify`, asking for the focus rule alone as the course does. The bar
gained Language and Rules. Every document page ends in a footer naming the UCLA and UC Berkeley collaboration and the
CIQC funding, with the three marks (`qccd/site/static/`, served as `static/`; asked for on
2026-09-09 evening). `tasks/<id>/task.json` + `circuit.qasm` for the five tasks. `.github/workflows/site.yml`
builds, runs `tests/studio.mjs` on the shipped studio, keeps `site` as an artefact and
rsyncs it when the `DEPLOY_*` settings exist. `tests/site.mjs` opens every built page in
headless Chrome over HTTP and fails on any exception, console error, missing bar, dead
search or wrong deep link; `--shots` writes the walkthrough.

**Acceptance.** 108 pages pass `tests/site.mjs` (`out/site-walkthrough/*.png` is the
walkthrough). `tests/studio.mjs` passes on `studio.html` and on 71 of the 89 entry pages;
the other 18 (the torus, cylinder and h3-ring variants) fail identically on the originals
under `SmallCode/` and `BBResults/` (`st.device` is null in the editor's node walk), so
that is the studio's to fix, not the site's. **Not live:** hosting changed mid-phase
(§10.1). **Then deployed the same evening** to the droplet by `tar` over SSH from this
machine: nginx serves it for `qccd.academy` and `www.qccd.academy` on port 80, the deploy user
and key exist, and the workflow can take over once `DEPLOY_KEY` is a repository secret. The A
records were set the same evening, certbot issued the certificate, and **https://qccd.academy/
answers**; `node tests/site.mjs site --base https://qccd.academy/` walks the live copy (108
pages, 0 failures on the first walk, made with the name resolved locally before DNS moved). A clean export of the branch (`git archive`)
builds and passes the studio harness, but without the course, the tools bar and the hover
hints: `tutorial.js`, `RULE_HINTS` and the current `render.py`/`editor.js` are the studio
session's uncommitted work, so a site built by CI from a checkout lacks them until that work
is committed (138 search entries from the export against 169 here); the build tolerates
their absence and the Learn page says so.

**Learned.** (1) the studio's stylesheet quotes `<body data-explain>` in a comment, so a
post-render injection must look for `<body` after `</head>`; (2) the studio defines
`.sq{width:10px}`, so everything injected into it carries its own class prefix (`sn-`);
(3) the course remembers its open lesson across visits, so `#design` has to fold the
dock; (4) the entry pages are 1.5–3 MB each, 137 MB in all — fine for a static host, and
the reason the seed pages are a second, separable commit; (5) the hints are pure data in
`editor.js`, so a regex reads them and no JSON export was needed; (6) the plan's
"instruction cheat sheet" has no `OPS` table behind it yet — deferred to phase 1; (7) an
unanchored `site/` in `.gitignore` also ignores `qccd/site/`, so the pattern is `/site/`.

**For review.** the server (host, path, key); whether the seed pages may live in the
repository (commit 2 of `website/0`; the site build reads them from `BBResults/` and
`SmallCode/`, which are untracked today); enabling Discussions and installing the giscus
app, then pasting the three category ids into `qccd/site/giscus.json`.

**2026-09-10, the front door and three pages more.** The landing now says *Build a
fault-tolerant QCCD quantum computer!* over one sentence (learn to design, compile any
program, evaluate the performance), as asked. Three document pages joined the bar:
**Physics** (`physics/`, rendered from `qccd/site/physics.md` through the docs renderer, so
it carries the studio's hover cards and a table of contents: trapping one ion, the qubit,
gates, cooling, the QCCD idea, the primitives and their prices, the control system, the six
noise sources, the three levels of noise simulation, and what the replay does and does not
model), **People** (`people/`, from the lists in `qccd/site/people.py`: cards by group,
then the three institutions with their marks) and **Publications** (`publications/`, from
`qccd/site/publications.py`: 21 papers grouped by what each contributes, one *used for*
line each, arXiv and DOI links, and a BibTeX for citing the software until a paper exists).
All three are searchable (page, section, person and paper entries) and in the walkthrough.

**2026-09-10, later: widths, physics figures, varied devices, an empty list.** Four requests.
(1) Text and boxes now share one width: the `76ch`/`70ch`/`52ch` caps on intro paragraphs
are gone and tables span the column. (2) The Physics page carries figures: three NIST
photographs (public domain, credited in the captions; `qccd/site/static/nist_*.{jpg,png}`),
device diagrams drawn straight from `m.arch.device` by `build.py::device_svg` (the five
example devices as a gallery, the 24-site ring with 8 docks), four programmes rendered by
the studio and embedded in embed mode (`physics/ex/*.html`, from `PHYS_RUNS`), and a table
of the formulas with the rule each one enters; `physics.md` places them with `{{photo:}}`,
`{{device:}}`, `{{gallery:}}` and `{{run:}}` markers on lines of their own. (3) The
examples no longer all stand on ring6d: `examples.py` defines a four-site chain, an
eight-site racetrack, a 2x3 lattice (T-junctions beside corners) and a coupled dual loop
beside the ring variants, each statement and rule on the device that shows it best, and
`compile_examples.py` compiles the fifteen gates onto four devices (the Bell pair on the
chain); every verdict was re-judged before the rebuild. (4) `publications.py`'s list is
empty on purpose, with the entry shape in a comment; the page says so. Also:
`tests/site.mjs::open` waited for about:blank's load event and could probe a slow page
before its footer images arrived; it now waits that event out first.

### Accounts and comments — 2026-09-14

**Asked.** Sign in with an email and a password; comments visible to signed-in readers
only; the user (yezhuoyang@cs.ucla.edu) is the admin and deletes any comment; a reader
pins a comment anywhere on any page, shown as a coloured chat box with the author's name
and avatar, so a problem can be pointed at where it is; the boxes stay on the page when
it scrolls (they are in the document, never fixed to the viewport).

**Built.** (1) `qccd/site/comments_api.py`: the API, standard library only (`http.server`,
`sqlite3`, `hashlib.scrypt` for passwords, a random session token in an HttpOnly
SameSite cookie, hashed at rest); routes for register/login/logout/password, threads per
page, comments per thread, delete by author or admin, an admin list of every thread;
refusals for cross-site Origins, malformed or oversized bodies and bursts of sign-in
attempts; a `passwd` command that makes or resets an account from the shell (the admin
account is made that way, so nobody can register the address; there is no mail on the
host, so no reset email). (2) `qccd/site/comments.html`: the layer `build.py::with_nav`
injects into every page after the bar. Signed out: a Sign in button in the bar and
nothing else. Signed in: `+ Comment` enters a placing mode (crosshair, a hint bar,
Escape cancels); a click describes the spot as an anchor (`#id`, else the tag path from
the nearest id, the fraction of the element's box, its first 80 characters as a fallback,
the layer offsets as a last resort) and opens a draft box; a posted thread is a pin (the
author's initials in their colour, twelve colours by account) at the spot and a chat box
beside it (coloured header with avatar, name and time; messages as tinted bubbles; a
reply field; delete on own messages, admin on all; fold to the pin). Positions are
recomputed on scroll (capture, so panels inside the studio count), resize, load, fonts,
a ResizeObserver on `body` and once a second, so a note follows its paragraph through a
reflow; a point inside a scrolled-away panel is not drawn over the panel's edge.
Embedded examples (iframes, `#embed`), `file://` copies and the test shim get nothing.
The account menu hides/shows the page's comments, unfolds all, changes the password,
signs out, and for the admin opens *All comments on the site* (every thread, newest
first, open-in-place links `page?c=<id>`, delete). (3) `qccd/site/deploy/`: the systemd
unit (user `qccd`, `/var/lib/qccd/comments.db`, `--secure --proxied`), the nginx
snippet (`/api/` to `127.0.0.1:8200`), a README with the install and the one sudo rule;
`site.yml` now also rsyncs the API file and restarts the unit. (4) Tests:
`tests/test_comments_api.py` (the API over HTTP: who may see, post and delete what;
sessions ending on sign-out and password change; every refusal), `tests/comments.mjs` +
`tests/test_site_comments.py` (headless Chrome on a page made through `with_nav`, with the
real API proxied: sign in with real typing, pin with a real click, the pin stands on the
paragraph through a scroll, a reload and a 720 px window, reply, delete, sign out, admin
deletes it all; no console errors); `tests/site.mjs` answers `/api/me` as nobody
signed in and asserts every page offers Sign in and shows no pins.

**Not done.** No email verification (no mail on the host); no avatar upload (initials
in the account's colour); comments are keyed by path, not by the studio's `#learn`/
`#design` hash (the anchor records it for context).

**2026-09-14, the collaborator's rules.** `QCCD Rules.xlsx` (Ke; Jaewon and Jack on the
angle rule) became four verifier rules and a technology layer, and every board entry was
re-judged: R19 (junction degree <= 4), R20 (rails at a node >= 60 degrees), R21 (planar
rails) read the drawing alone; R22 (one waveform per transport cycle) reads the programme.
The ring generator's corner docks and the dual loop's end caps were drawing defects that
R20/R21 caught on every shipped ring; fixed in the generators. The OCaml router now emits
uniform cycles; the 28 board programmes it had batched were rewritten by
`qccd/compile/uniform.py` or recompiled (`Codesign/scripts/uniformize_entries.py`,
`recompile_entries.py`), the small codes fully recompiled, and every board page now takes
its running time from the replay of the programme it shows (`bb_studio.py::build_one`,
`numbers.T_source == "replay"`). Consequences on the boards: the torus, annulus, random
and Tanner designs fail geometry and stay listed as failing; the BB lattices cost ~3x more
transport time under R22 and the ring answer is unchanged; the site's three-per-task view
shows only verified designs, so the change is visible mostly on the task pages. The Rules
page has R19-R22 with explicit devices (a five-rail star, a 30-degree fork, two crossing
rails) and the physics page explains them; the studio gained a physical scale (the
technology's nm per unit), micrometre readouts, a measure tool and a true-scale toggle.

### Invitations — 2026-09-17

**Asked.** Anyone could register, and the site should not be open like that: the admin
(yezhuoyang@cs.ucla.edu) signs in, names an email, sends an invitation to it, and only
somebody who clicks that link can make an account.

**Built.** (1) `comments_api.py` grew an `invites` table (the address, the token as a
sha256 hash, who invited, when it expires, when it was sent, and the account it became)
and a `users.access` column. `POST /api/register` now takes a `token` instead of an
email: the invitation decides the address, so the person following the link cannot
register as somebody else, and checking the token and spending it happen inside one
lock. `GET /api/invite?token=` is the one public read, so the form can say whose
invitation it is before that person has an account. The admin's endpoints are
`GET /api/admin/people` (accounts and invitations), `POST /api/admin/invites`
(`{email, name, note, send}`), `DELETE /api/admin/invites/<id>` and
`POST /api/admin/users/<id>/access`. Closing an account deletes its sessions, so a
reader removed is out at once rather than when their cookie expires; admins are always
let in and cannot close themselves. An invitation lasts `INVITE_DAYS` (14), makes one
account, and is replaced rather than duplicated when the same address is invited again.
(2) The mail: a `Mailer` over `smtplib`, any relay that speaks SMTP (Gmail with an app
password is what the droplet uses), configured from `/etc/qccd/mail.env` through the
unit's `EnvironmentFile`, so the password is in no command line. A relay that refuses is
not a failed invitation -- it is made either way and the answer carries the link, which
is also the whole behaviour when no relay is configured at all. (3) `comments.html`: the
sign-in dialog has no way into a registration form any more; a page opened as
`?invite=<token>` reads the invitation, opens *Accept your invitation* with the address
fixed and unchangeable, and takes the token out of the URL before anything else. The
admin's menu gained *Readers and invitations*: invite by email with an optional name and
note, the link to copy, the invitations with their state (waiting, mailed, expired,
joined) with *Invite again* and *Withdraw*, and the accounts with *Close account* /
*Let back in*. (4) The command line kept up: `invite`, `invites`, `access --allow
|--revoke`, and `users` now shows which accounts are closed. (5) Tests:
`test_comments_api.py` covers registration without a link, a made-up one, an expired
one, a spent one, an address the browser tried to change, the admin's four endpoints,
closing and reopening an account, and the invitation mail read out of a small SMTP
server the test stands up; `comments.mjs` now opens with the admin inviting the reader
through the panel and the reader following that link, and closes with her account being
closed and refused.

**Kept deliberately.** The readers who registered while the site was open keep their
accounts (the migration gives every existing row `access = 1`); the panel lists them and
closes any one of them in a click. Nothing here verifies that the person who followed
the link is the person the mail reached -- possession of the link is the proof, as with
any invitation.
