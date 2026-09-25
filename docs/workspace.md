# QCCD workspaces: local-first, agent-native co-design

Architecture and implementation status for `qccd/workspace/` and `qccd/official/`.
Last updated 2026-09-22.

A person and an agent (Codex, Claude Code, or any MCP client) work on **one versioned local
workspace**. The person edits in the existing QCCD Studio page. The agent edits through MCP
tools. Both go through the same validated change-set API. Prompts sent from Studio carry
frozen context: anchors, sketches, demonstrations. They reach a connected agent session
without anyone typing in the terminal. Local submissions are immutable, graded by the same
evaluator the official server runs, and published only after a person approves one exact
bundle.

---

## 1 · What was already there (reconnaissance)

| concern | where | notes |
|---|---|---|
| Studio page | `qccd/viz/render.py` (`render_html`, `_TEMPLATE`, `page_stamp`), `qccd/viz/js/editor.js` (`EDITOR`), `edit.js`, `engine.js` | one self-contained HTML page; the `FORBIDDEN` scan rejects `fetch(` anywhere in it |
| browser design state | `editor.js` `GEOM/SEED/POST/EDITS` + `PROG` | the page's truth is a **list of records**, replayed from scratch (`replay()`); undo pops records |
| record vocabularies | `qccd/arch/edit.py` (`OPS`, `SEED`, `MUTATE`, `BUILD`, `apply_edit`, `apply_call`, `apply_program`) | parity with `edit.js` is held by `tests/test_edit_parity.py` |
| architecture document | `qccd/arch/device.py` (`Architecture.to_json/from_json`), `qccd/arch/schema.py` (`SCHEMA_VERSION = "0.3"`) | validation runs on load only |
| browser export | `editor.js::documentRecord` (`kind: qccd.studio`), `qccd/__main__.py::cmd_open` | `cmd_open` trusted the browser-serialised `arch` |
| program | `qccd/ir/tsir.py` (TSIR, stable instruction ids), `qccd/api.py::Program.apply_calls` | |
| rules, replay | `qccd/verify/` (27 rules; passed/failed/skipped/partial) | R10 is decided outside Python |
| compiler | `Compiler/ocaml` → `qccdc_cli` (compile/rotate/parse), `Compiler/bridge/*.py` | Python has no QASM compiler |
| proved checker | `Compiler/lean` → `qcheck` (`QCCDC.Cert.check_sound`), `Compiler/bridge/check_cert.py` (O1 + O2) | the certificate's FNV hashes were never checked; `circuit_ops` came from the compiler |
| boards | `Codesign/scripts/small_codes.py`, `bb_studio.py`, `qccd/site/build.py` | maintainer scripts; no submission path; `tasks/*/task.json` is mostly unread metadata |
| server-side | `qccd/site/comments_api.py` (stdlib, accounts/comments) | nothing resembling a submission API |

Before this work there were two edit-to-render paths: browser records rendered by the page
itself, and Python `Machine` methods rendered by `render_html`. They were joined only through
file export and re-import. The compile-to-grade path existed only as scripts on one machine.

## 2 · Architecture

```
Codex / Claude Code / any MCP client
      │ qccd_* tools (stdio MCP, qccd/workspace/mcp_server.py)       ▲ turn/start, turn/steer, turn/interrupt
      │                                                              │ (Codex app-server bridge, agents/codex.py)
      ▼                                                              │ notifications/claude/channel (mcp_server.py --channel)
   local workspace service (service.py: FastAPI + SSE, loopback) ────┘
      │   ▲ HTTP commands + /api/events (Studio page: web/cowork.js, injected after render_html)
      ▼   │
   app.Workspace = core (revisions, change sets) + collab (prompts, delivery) + results (jobs, snapshots)
      │           ▲
      │           └── design.replay / operations  (the studio's records through qccd.arch.edit)
      ▼
   evaluator.grade(release, bundle, profile) ── qccdc_cli, check_cert.py, qcheck, verify(), metrics
      │
      ▼  a person approves one digest (Studio dialog or `qccd publish --submission` at a terminal)
   qccd/official: API ── queue (db.py) ── worker ── spool ── grader (no network, no credentials)
```

**One applier.** `design.replay` rebuilds the architecture from the studio's own records
using the toolchain's whitelists, exactly as `editor.js::replay` does. A browser gesture
reaches the service as a `studio_sync` operation carrying those records. An agent's semantic
operation (`add_site`, `add_chain`, `replicate`, `construct`, …, from `operations.OPERATIONS`)
compiles to the same record shapes. Both are replayed and validated by the same code before
anything is committed. `tests/test_workspace_design.py` drives the real studio page through
edits of every record kind and requires the Python replay to reproduce the browser's
architecture byte for byte. The replay never trusts a client's serialised `arch`.

**State categories** (`store.py`):

| category | tables | behaviour |
|---|---|---|
| design inputs | `branches`, `revisions`, `change_sets`, `idempotency`, `artifacts` | revisioned; every commit is one SQLite transaction with its event |
| collaboration | `notes` (versioned prompts, comments, replies), `context_snapshots`, `prompt_links`, `work` | independent of design revisions; comments never invalidate results |
| view / agent | `views`, `sessions`, `deliveries` | per tab, per agent runtime |
| generated results | `jobs`, `snapshots`, `submissions`, `approvals` + read-only files under `.qccd/snapshots/` | immutable, bound to an input digest |

`input_digest` covers the design records and programs. It excludes `meta` and constraints,
so a comment, a zoom or a lock never makes a result stale. Positions in the device are
physical inputs and are included; sketch and viewport coordinates live in prompts and are not.

**Identity.** Entities are keyed by their device ids (`node:<id>`, `segment:<id>`,
`loop:<id>`, `zone:<name>`, `block:<name>`). The studio has no rename verb, so an id is an
identity. A deleted entity shows up in a frozen prompt as a `missing` anchor, never as
whatever now sits at its position. Instruction ids come from TSIR and are preserved by the
cooling pass (`qccd/compile/cooling.py::_rebuild`).

**Files.** `design/studio.json` is a derived mirror written atomically after every commit on
main. It carries a `qccd_workspace.revision` stamp. **The database is authoritative during
live editing.** A file written outside Studio (an optimiser, a hand edit) comes in only through
`qccd import` / `qccd_import_file`. That becomes a change set against the revision the file
was stamped with, so a stale file is a conflict and never overwrites newer UI edits. Crash
safety: SQLite WAL, one transaction per commit, and the mirror rewritten from the database on
service start.

**Concurrency.** Optimistic. Every change set names `expected_revision`. A stale request is a
409 conflict listing the intervening change sets and what they touched. `rebase: if_disjoint`
(which Studio uses for human edits) recompiles the same operations on the current head only
when nothing overlaps. Nothing is last-writer-wins. Idempotency keys (`request_id`) make
retries return the stored result, and a reused key with different content is refused.

**Protection** (`core._protection_violations`) is enforced in the service for every door:
Studio, MCP, file import and candidate adoption. An agent may protect entities, but may not
unprotect what a person protected. `entity` level forbids changing or removing the record;
`neighbourhood` level also forbids changing its connections.

**Undo** of a change set is a new change set. It removes the records that change set stamped
(`meta.change_set`), replays, and reports dependent later changes as a conflict instead of
breaking them.

**Candidates**: `cand/<name>` branches fork main at a revision. Adoption replays the
candidate's operations onto main through the change-set path, so conflicts and protection
apply.

### Prompts and delivery (`collab.py`)

- A draft autosaves and never delivers. **Send** freezes a context snapshot. It records the
  revision the page had rendered, the selection, anchors resolved to entity records at that
  revision (with `missing` keys), sketches in diagram coordinates plus the viewport they were
  drawn in, and a demonstration: before/after revisions, a structured diff, the change sets,
  and target keys. The snapshot, the immutable prompt version, the delivery row and the work
  row are written in one transaction.
- Editing a sent prompt creates a new version. A still-queued older version is cancelled as
  superseded, never resent.
- `comment` intent is never delivered. The **Ask / Propose / Apply locally** modes are
  enforced: an `ask` prompt authorises no change set, and a `propose` prompt authorises
  changes only on candidate branches.
- Delivery states are `queued → sending → accepted | uncertain | failed | cancelled`. Work
  states are `pending → working → waiting_input | ready_for_review → resolved | cancelled`.
  The two are kept separate.
- Agent replies never create deliveries, so a reply cannot trigger a turn.
- **Stop** fences the session's writes, cancels its queued deliveries and the jobs its
  prompts started, and asks the runtime to interrupt where it can. Committed changes stay;
  they are undone one change set at a time.

### Session adapters (`agents/`, `mcp_server.py`, `delivery.py`)

| mode | deliver | acknowledgement | steer | interrupt | status |
|---|---|---|---|---|---|
| **Codex app-server** (`qccd agent connect --client codex`, or Studio → Agent → Connect) | `turn/start` with `clientUserMessageId` = delivery id | the turn id returned (runtime acceptance) | `turn/steer` with `expectedTurnId`; if the turn has already finished, a new turn | `turn/interrupt` | **live-tested** (§5) |
| **Claude Code channel** (`qccd mcp --client claude --channel`, `claude --dangerously-load-development-channels server:qccd`) | `notifications/claude/channel` `{content, meta}` | none from Claude Code, so `uncertain` until the agent reads the prompt through a tool | not supported (queued) | not supported; the write fence still applies | **contract-tested** over real MCP stdio; not run against a live Claude Code session |
| **pull** (any MCP client) | none: prompts are listed in `qccd_get_context.unread_prompts` | reading | – | fence only | tested; labelled everywhere as reduced capability, **not** automatic delivery |
| Claude Agent SDK managed session | – | – | – | – | **not implemented**: it needs an Anthropic API key, cannot reuse a claude.ai subscription login, and would be a different conversation from the user's terminal |

The Codex bridge uses the documented existing-session pattern: an app server on loopback
(`codex app-server --listen ws://127.0.0.1:PORT`), the user's terminal attached with
`codex --remote ws://127.0.0.1:PORT`, and the bridge as a second client on the same thread.
Starting a *new* thread is supported and disclosed as new. The QCCD MCP server is passed to
the thread through `thread/start`'s per-thread `config`, with
`default_tools_approval_mode = "approve"`. Measured on Codex 0.154: with an approval policy of
`never`, an unapproved MCP call is simply declined. So the user's global Codex configuration
and project trust are left alone. The session records enough to re-attach after a service
restart: a later `serve` resumes the same thread, starting a new app server if the old one is
gone. A turn accepted by one service process and finished under the next is still linked to
its prompt through the turn id stored on the delivery. An app server left from before a
restart is stopped by pid only while that pid is still a Codex executable. An `uncertain` delivery is reconciled by listing the thread's turns and looking for the
delivery id before anything is resent.

### Studio live layer (`web/cowork.js`, `web/cowork.css`)

This layer is injected by the service **after** `render_html` writes the stock page. It
changes nothing in `render.py`, `editor.js`, `engine.js` or the page stamp, and every static
page keeps its self-containment scan. What it does:

- **Sync.** It diffs `EDITOR`'s record lists against the last confirmed revision and sends
  one `studio_sync` change set. On a refusal (protection, invalid design, conflict) it
  restores the service's revision and says why.
- **Remote changes.** A commit that only appends records is applied as one
  `EDITOR.transaction` (the view holds, and the user can Ctrl+Z it). Anything else is a
  `restore()`. Missed events (`gap`) trigger a fresh snapshot, and reconnects resume from
  `Last-Event-ID` / the cursor.
- **Anchors and sketches.** Anchors: the selection, pick (canvas hit test, program row
  `_ref`, `data-id`, `data-hint` fields, a panel fallback), lasso, point, the current frame,
  and demonstrations. Sketches: freehand, arrow and lasso, stored in lattice units with the
  view box. A screenshot is not the authoritative representation and is not captured.
- **The chat (the default).** The dock is one conversation with the agent: the person's
  messages, the agent's own messages (for Codex, what it writes in the turn is stored as the
  thread's replies), a typing indicator while it works, and one box with Send, which becomes
  Stop while it works. The current selection goes with the message as a chip that can be
  removed. **+** holds lasso, point, arrow, freehand, "show an edit" (a demonstration) and
  lock or unlock parts. What the agent did appears as small cards: a change with Show and
  Undo, a run with its round time and "Watch it run", and a comparison with "Open side by
  side". If no agent is connected and Codex is installed, sending starts a Codex conversation
  (approval policy `never`, read-only shell; it changes the design only through the QCCD
  tools). `QCCD_CODEX=none` turns that off. The header also carries the draft picker.
- **The debug view.** Under **⋯**: the old tabs. They hold threads with delivery and work
  state and their links, the agent session panel (Stop, Resume, target per tab, Follow
  agent), attributed history with "Undo this change", jobs, and the local leaderboard
  labelled "Local results - not published". There is a publication review dialog.
- **Presentation.** With Follow on, the view widens to show the target (`reveal`) and flashes
  it. With Follow off, a notification with a Show button appears instead. Either way the page
  reports `displayed` / `notified`.
- **Run view.** `/run/<snapshot>` renders an immutable snapshot's device and final program,
  view-only, for comments anchored on generated instructions (`#instr=<id>` focuses one).
- All text from people or agents is set with `textContent`.

### Runs, drafts and comparison (`runs.py`, `perf.py`, `compare_page.py`)

- **A run** compiles a program onto a design and tells where the time goes. It is a job of
  kind `run` and an experiment, never a submission. The program is a catalogue name, from
  the tracked `Compiler/examples/*.qasm` ("bb", "bb code" and "gross" mean `bb144_esm`, the
  BB [[144,12,12]] syndrome round), or the agent's own OpenQASM. The design is `main` or any
  draft. The job:
  1. exports the design;
  2. compiles with `qccdc_cli rotate` when the device has closed loops, falling back to
     `compile`, and otherwise uses `compile`;
  3. inserts cooling, and replays with the rules checked under the release's cost model and
     under the transport table.

  A design too small for the program, or one the compiler cannot route, fails with the
  reason. Measured on this machine: BB144 on a 2×72 ring with 24 docks takes about 12 s end
  to end and gives 522 ms per round.
- **The performance report** (`perf.performance`) is read off that replay:
  - the round time, and time by category (transport, cooling, gates, measurement, reset)
    with shares;
  - transport time by move class, and the five longest steps;
  - junction transits with their imbalance, the busiest ions, and ions per trap at gates;
  - peak heating, and failed rules;
  - bottleneck sentences computed from those numbers.
- **Designs** have the names their person gives them, any text ("Two triangles", "Ring v2").
  Internally each is a branch with a made-up id that is never shown; every door takes the
  name. A name two designs share is refused rather than guessed. Studio saves, renames,
  switches and edits them from the chat header, and agents list, create, rename and run them.
- **Side by side**: `/compare?runs=A,B` shows the comparison table (B minus A), a verdict,
  each run's bottlenecks, and both runs' own pages (`/runview/<id>`, view-only) in frames.
  One slider and Play drive both by modelled time: every step's start time is kept with
  the run. The faster design finishes first, visibly. Only `/runview` may be framed, and
  only by the same origin.
- **MCP**: `qccd_list_programs`, `qccd_run_program` (it waits up to 50 s and returns the
  report), `qccd_compare_runs` (returns the table and opens the side-by-side view in Studio),
  and `qccd_manage_branch` (drafts).

### The website with the chat (`mirror.py`, `web/pageact.js`, `web/mirror.js`)

- **Every qccd.academy page, through the workspace.** The service listens on a second port
  and serves the site at `http://127.0.0.1:47100/web/...`. `qccd studio` prints the
  address; `qccd web [page]` pairs the browser and opens it. 47100 is a well-known port
  (`mirror.WEB_PORT`, `QCCD_WEB_PORT` changes it): every page of qccd.academy has an
  **Agent** button whose "Open this page with my agent" is a plain link to
  `http://127.0.0.1:47100/web/<that page>`, and "Open my Studio" to
  `http://127.0.0.1:47100/studio`, which redirects to the workspace's own Studio. When
  another workspace holds 47100, the service uses the port it had last time or any free
  one, and `qccd web` prints where. Each page is fetched from the
  site live, kept in memory for 5 minutes, then revalidated (ETag / Last-Modified). When the
  site cannot be reached, the last copy is served, marked stale. `QCCD_SITE_URL` points it at
  another copy of the site, which the tests do. `QCCD_WEB=0` turns the second port off.
- **What a page gets.**
  - The chat, the same one as in Studio, framed in the corner.
  - A link back to the person's own Studio in the site bar.
  - The site's own Agent button is hidden. The comment layer is switched off: it needs the
    person's qccd.academy account, and it would call the local API.
  - The Official leaderboard's reads are passed through.
  - The site's Studio (`studio.html`, with the course) stays the site's. It is not the
    workspace's design; that is `/studio`.
- **Page context.** A message sent from a page carries the page's URL, title and kind. It
  also carries the text the person selected (a `page` anchor with the `quote`) and anything
  they pointed at with **+**. The delivery text tells the agent which page it is and which
  tools read it. `qccd_get_context` lists the open pages. In the conversation, a message
  says which page it was sent from.
- **The agent's hands on the page.** Two MCP tools cover a fixed set of actions, all
  implemented in `web/pageact.js`. Nothing else runs; there is no script execution.
  - `qccd_page_read` returns the headings, the text (by section, paged), the visible
    controls (the ones on screen first), the embedded examples with their animation step,
    the reader's selection, and on studio pages the transport and the lessons.
  - `qccd_page_act` does `highlight` (with a caption), `scroll`, `click`, `fill`, `press`,
    `navigate`, `step` and `open_lesson`.
  - Targets are refs from the last read, CSS selectors, or visible text. `frame` reaches into
    an embedded example.
  - `POST /api/page-actions` sends the action to the page as an event and waits for the
    page's answer, at most 30 s. The default page is the one the current request came from,
    else the page used last. Only an agent or the CLI can start an action, and only the
    page's own view can answer it.
  - The workspace's own Studio takes the same actions (its transport, panels and lessons).

### The agent as a person at the screen

The design goal: a person can ask their agent for anything they could do on the site
themselves ("open the tutorial and show me how to finish each lesson", "review the rules
for mistakes and comment under my name", even "scroll back and forth ten times"), and
watch it happen.

- **Visible.** Every page action first glides a purple cursor, labelled with the agent's
  name ("Codex: clicking "Show answer""), to its target, then acts. Typing appears letter
  by letter. The cursor rests, dimmed, where the agent last worked. When the target is
  under the chat, the chat turns see-through for a while (hovering it restores it).
  `scroll` takes `by` (pixels, `'page'`, `'-page'`), and `wait` paces a walkthrough.
- **The Studio.** `studio` runs one verb of the page's Studio API (`EDITOR`): design on the
  canvas, write the program, and the course's own `lessonLoad` / `lessonCheck` /
  `lessonHint` / `lessonSolution`. The course solves its exercises with the same verbs.
  Verbs that touch files or storage are refused. On the person's own Studio the agent
  draws with the same verbs (`sketchDraw` a shape, `closeLoop`, `stampComponent`, …).
  The page first syncs the person's pending edits, then runs the verb and commits what it
  drew as the agent's change set (`by_page_action`, checked against the running action).
  Each drawing is therefore attributed, protected and undoable. A verb that refuses fails
  the action with its rule, for example R20 for a corner under 60°. The course's verbs
  stay off there, since they would replace the design. Checked on the live
  site: all 31 lessons' own solutions pass their checks through these tools (Part D on
  its compiled companion page).
- **Comments, as the person.** In the local website the site's comment layer works. Its
  `/api/` requests are relayed to qccd.academy, and the person signs in once with their
  own account. The session is kept in the mirror's own cookie (`qccd_site`, path
  `/api/`), and writes are relayed only from the mirror's origin. Threads are keyed by the
  site's path, so a comment made locally is in the same place on the public site. The
  agent has `comments`, `comment`, `reply` and `resolve`, which call the comment layer's
  own functions. Each comment it writes ends with "— via <agent>" and appears in the chat
  as a card. It cannot sign in, press the comment layer's buttons, or delete anything.
  Checked against the live pages with a private copy of the comment service
  (`tests/workspace_live_comments.py`).
- **Claude Code, like Codex** (`agents/claude.py`). Each chat message runs `claude -p`
  headless, resuming ONE conversation by id (`--session-id` first, `--resume` after). It
  gets the QCCD tools and read-only file tools, `--permission-mode dontAsk`, and no shell.
  Its messages stream into the chat, and a failed run says why. The chat starts whichever
  agent is installed, or the one last chosen under ⋯ ("New conversation with Codex" /
  "…with Claude"). `QCCD_CLAUDE=none` turns it off. Live-checked: asked on the Rules page
  "What does rule R7 say? Show me its failing example", Claude read the section,
  highlighted the failing example with a caption, and answered with the page's numbers.

### Only what is declared

An agent may do on a page only what the code declares (`qccd/workspace/interface.py`; the
developer's rule is `docs/agent-interface.md`):

- **controls:** a `data-hint` key the page describes, links and fold-outs, fields of a
  generated form, and a short out-of-line list that may only shrink;
- **places:** the site's index, the page's own links, and `/studio`;
- **Studio functions:** those declared for agents in `qccd/viz/js/editor_api.json` (148 of 239).

Anything else is refused with what is declared instead, and the refusal lands in the trace.
The declarations are read when a page is served, so a rebuild updates what agents know.
`tests/test_agent_interface.py` fails a change that adds UI or an API function without its
declaration. The opt-in `tests/workspace_live_interface.py` checks every place on the live
site.

### Nothing out of sight

What the agent does to the person's design or with their programs happens where they are
looking.

- **The Design page is the live Studio.** On the local website, `studio.html` (and `#design`)
  opens the workspace's own Studio (`/studio`). Lesson links (`#learn=…`) stay on the site's
  Studio. Every change set an agent commits appears there at once, with the agent's cursor
  at the changed parts and the change set's summary as its caption.
- **A run is shown before it is reported.** While `qccd_run_program` compiles, the Studio
  shows the run's program (the first lines of its OpenQASM) and its progress. When it
  succeeds, the person's tab opens the run's own page (`/runview/<run>`, the circuit
  beside the compiled program). Follow off, or a background tab, gets a "Watch it run"
  button instead. The tool's result tells the agent to press Play there and let it play
  before reporting numbers. A comparison opens side by side the same way.
- **Failures say why.** When the agent's runtime stops without answering, the chat shows
  why: a failed Codex turn or Claude run leaves its error message (a usage limit, for
  example) as a notice in the conversation, instead of the conversation going quiet.

### The chat: where it sits, which model, and what the agent did

- **It moves.** Drag the chat by its header, resize it from the bottom-right corner, and
  double-click the header to put it back. On a website page the chat is a frame, and the
  page moves the frame when the chat asks it to. The browser remembers the place, once
  for the Studio and once for the website.
- **Model and thinking.** The button beside the agent's name picks the model and how hard
  it thinks, as in Claude or Codex. Claude offers Default, Fable, Opus, Sonnet and Haiku
  (`claude --model <alias>`) and the levels low, medium, high, xhigh and max (`--effort`).
  Codex's list is its app server's own (`model/list`), each model with the levels it
  supports, passed as `model` and `effort` on every `turn/start`. A choice applies from
  the next message. It is kept on the session (`capabilities.settings`, and in what a
  restarted service re-attaches with). Before a conversation exists it is remembered in
  the browser and used when one starts. `GET /api/agents/models` and
  `POST /api/sessions/{id}/settings` (a person only) are behind it. Checked: each Claude
  alias answers with `--effort low`, and Codex's list came from its app server.
- **The trace** (`trace.py`, `web/trace.js`). Everything an agent does for a request is
  recorded in order, in `.qccd/traces/<session>.jsonl`:
  - the full text it was handed, and the person's own words;
  - what it started with: model, tools, MCP servers, skills;
  - its thinking;
  - every tool, MCP and Skill call with its input and result;
  - every page action and what the page answered;
  - how the turn ended: time, model turns, cost, error.

  The steps come from three places: Claude's stream-json, Codex's item events, and the
  QCCD MCP server (which records every call it serves, so agents started in a terminal
  are traced too), plus the service for page actions. Long values keep their head, keys
  that name credentials are blanked, and nothing leaves the computer.

  To read a trace, use `/trace` in the browser (⋯ → "Trace: what the agent did, step by
  step"). It lists each conversation's requests and shows one step by step. Replay shows
  the steps again at their recorded pace (real time, 4×, 16×, or one a second), and JSON
  exports them. From a terminal, `qccd trace` lists the conversations and
  `qccd trace --session <id> [--prompt <id>] [--full | --json | --open]` prints one
  request. `GET /api/traces[/<session>?prompt=]` serves the same data.
- **The agent knows the site.** `qccd_read_reference` section `site` is read from the live
  site's own search index. It holds:
  - the main pages, the lessons and the leaderboards;
  - a short how-to: walk someone through a lesson, show an animation, test a program on
    the design, try one on a site Studio page, check a page for mistakes, comment.

  Section `site:studio` lists every Studio control in the Studio's own words (the explain
  layer's HINTS table, the sentences on its hover cards). In a page read, each Studio
  control carries its `hint` key and that sentence. `query` searches both sections.

## 3 · Grading (`evaluator.py`, `bundle.py`, `metrics.py`, `tasks.py`)

`grade(release, bundle, profile) → report`. The local service, the CLI and the official
worker all call it.

| stage | establishes | uses |
|---|---|---|
| bundle | manifest schema; every member re-hashed; no stray or unknown files; names **this** release (id and digest) | `bundle.read_bundle` |
| device | loads under the schema and structure checks; size limit | `Architecture.from_json` |
| physics_lock | `primitives/heating/species/budget` equal the release's `physics.json`; otherwise the design is *exploratory*, never eligible | `tasks.physics_mismatch` |
| program | well-formed TSIR within limits | `validate_program` |
| rules | replay under the task model; only R7b, R9 and R10 may be skipped, only R15 may be partial | `qccd.verify.verify` |
| correspondence | the final program is the certified program with only allowed insertions (`cool`, fresh ids), identical otherwise except the provenance index `meta.call` | new |
| certificate_binding | the certificate's `circuit_ops` are the **release's** circuit as the compiler's own parser reads it; gate witnesses match certified instructions | `qccdc_cli parse`, `qccd.ir.source_map` |
| lean_certificate | `QCCDC.Cert.check` (proved sound) accepts, against facts re-derived from the **submitted** device | `mk_qcheck_input.py`, `qcheck` |
| semantics | O1 transport replay and O2 tableau of the emitted pulses equal to the trusted circuit (partial outside Clifford) | `check_cert.py` without `--qcheck` |
| metrics | `T_jones`, `T_transport` (ms, modelled), `instructions`, `dacs`, `ions_per_instruction` | `metrics.compute_metrics` |

**Eligible** means every required stage `passed` under the `reference` profile. `skipped`,
`unsupported`, `partial`, `timeout`, `cancelled` and `internal_error` are never passes. The
report records task and bundle digests, the evaluator identity (a source digest over the
code that decides verdicts, plus sha256 of `qccdc_cli` and `qcheck`), per-stage coverage,
diagnostics with stable ids (`RULE.R1`, `TASK.PHYSICS_MODIFIED`, `CERT.BINDING`,
`CORR.MISMATCH`, …), metrics with units, and non-deterministic timing kept apart.
`evaluator.comparable` / `publish.compare_reports` define parity.

**What it does not establish.** Physical realisability beyond the task's model. It also does
not compare the certificate's moves with the final program's transport instruction by
instruction: co-location of the program's own gates is rule R6b of the replay, and the
semantic link is O2 through the certificate's qubit→ion map.

**Metrics parity.** `tests/test_workspace_metrics.py` reproduces the published board numbers
(`SmallCode/*/manifest.json`, re-replayed) exactly when those files are present. Modelled
time never depends on wall-clock time.

**Boards.** Each leaderboard of the website is a task release
(`qccd/workspace/releases/<task>@<release>/`), which pins the circuit, the physics and the
manifest by sha256. `.gitattributes` keeps their bytes exact. People only ever see and type a
board's title. Code finds a board with `find_board` (title or short name, newest edition) and
refuses an ambiguous name.

Six boards ship, the five from the website (written by `tools/make_releases.py` from each
board's own title, description and circuit) plus the GHZ starter. Measured 2026-09-24, each on
its suggested first device, with the full reference grade (rules, Lean certificate,
semantics) eligible:

| board | first device | per round | grade time |
|---|---|---|---|
| BB [[144,12,12]] | ring 72 × 2, 24 docks | 522 ms | about 6 min, mostly Lean |
| BB [[144,12,12]], drawn in the Studio | a triangle loop of 144 sites, 24 docks inside it ("a large triangle and a smaller one") | 505.8 ms | 451 s |
| Repetition code, distance 9 | ring 8 × 2, 8 docks | 9.45 ms | seconds |
| Five-qubit code [[5,1,3]] | 3 × 3 grid | 8.89 ms | seconds |
| Steane code [[7,1,3]] | ring 8 × 2, 8 docks | 11.48 ms | seconds |
| Surface code, distance 3 (17 qubits) | ring 8 × 2, 8 docks | 14.1 ms | seconds |
| GHZ state on four qubits (starter) | chain of 4 | — | about 4 s |

Every metric has a unit and a direction. The numerical policy is `rel_tol 1e-9 / abs_tol
1e-12`. The track is `codesign-artifact`: hardware plus final schedule for a fixed circuit.

**A workspace is general.** Its lockfile (version 2) names no board, and one workspace
submits any design to any board. The old version-1 lockfile pinned one release; it still
opens, and that release becomes its default. `submit_design(design, board)` (the Studio's
"Submit to this board", the agent's `qccd_submit_local(design, board)`, and `qccd submit
--local --board <title>`) runs as one job:

1. compile the board's circuit onto the design (the conveyor first for a design with a closed
   loop, else the router);
2. adopt the program, which records the circuit it was compiled for;
3. freeze a snapshot, recording the board and its digest;
4. grade it against that board.

A program compiled for one board is refused for another, which compiles again instead.
Databases from before (store schema 1) are migrated in place.

Board entries whose junction curves extend to degrees 5–16 (`random16`, `tanner`) would
fail `physics_lock` against the reference physics. That is intended: their physics differs
from the task's.

## 4 · Security and trust boundaries

- **Local service.** Binds 127.0.0.1. Host must be `127.0.0.1:<port>` or `localhost:<port>`.
  Cross-origin `Origin` is refused, and there are no CORS headers. A browser pairs with a
  one-time code (5 minutes) that `qccd studio` mints and puts in the URL *fragment*, then
  strips. The page gets an HttpOnly SameSite=Strict cookie plus a CSRF token required on
  every write. Agents and the CLI use bearer tokens from `~/.qccd/runtime/<ws>.json`,
  outside the project and never in URLs, logs, exports or MCP responses. Bearer tokens are
  new for every service process. Browser pairings outlive one: `<ws>.sticky.json`, in the
  same user-private directory, keeps the last port and each pairing as the SHA-256 of its
  cookie with its CSRF token (never the cookie itself), so a restarted service still
  recognises an open page. Pairings expire after 14 days. Bodies are parsed
  strictly (duplicate keys, NaN, depth, size). Imports cannot leave the workspace. The CSP
  allows only same-origin connections.
- **The website mirror.** Its pages come from the internet, so they get their own origin
  (the second port), which the workspace does not trust:
  - The mirror serves GET pages and nothing else. It never honours the pairing cookie, even
    though browsers send it there (cookies ignore ports).
  - The workspace refuses the mirror's `Origin` like any other.
  - The chat on a mirror page is a frame from the workspace's origin (`/chatframe`), and only
    the mirror's origin may frame it (`frame-ancestors`). Only that frame holds the pairing.
    It talks to the page by `postMessage`, checking the source window and origin both ways.
  - Page actions cannot reach the chat: the dock, its menus, dialogs and notices. Navigation
    and link clicks stay on the site.
  - What a page answers is website content: data, not authority. It is labelled that way
    for the agent.
  - A hostile page could still hide or cover the chat frame, or answer page actions falsely.
    It cannot read the conversation or act as the person.
- **Agent scope.** The agent token can edit, comment, run jobs, submit locally and *prepare*
  publication. It cannot send prompts, approve publication, stop or resume sessions, mint
  pairing codes, unprotect a person's lock, or shut the service down.
- **Residual risk.** Any process running as the user can read the runtime file. QCCD's
  guarantees apply to its own API surface and are not a sandbox against a local agent with a
  shell. `qccd publish --submission` approves only at an interactive terminal (it asks for
  the digest). The Studio dialog approves on a click.
- **Grading.** Bundles are data and never executed. Subprocesses run from argument lists,
  under timeouts, memory limits (Windows Job Objects, POSIX rlimits) and whole-tree kills. The
  official grader runs with no network and no credentials (Compose: `network_mode: none`,
  read-only root, tmpfs, all capabilities dropped). The server never uses client reports or
  profiles, and resolves tasks from its own releases. Uploads fail closed without configured
  uploaders. The development token is explicit and loopback-only.
- **Publication.** An approval is bound to the bundle digest *and* the parameters, used once,
  and re-verified against the files on disk before upload. The archive contains only the
  bundle: no optimiser source, conversations, database or credentials.

## 5 · Implementation status

| requirement (brief §) | status | evidence |
|---|---|---|
| A · shared domain boundary: one applier for human and agent edits; revisioned contracts | **done** | `test_workspace_design.py` (browser parity), `test_workspace_core.py` |
| A · evaluator entry point reusing the toolchain | **done** | `test_workspace_grading.py` |
| B · Studio ↔ service live sync, SSE, reconnect, gaps | **done** | `test_workspace_browser.py` (real Chrome), `test_workspace_service.py` |
| B · selected-object/region prompts, durable delivery, a real session adapter, agent reply, revision-safe change, highlight | **done**, Codex **live-tested** | `tests/workspace_live_codex.py`: a prompt sent from the page reached a new Codex thread (turn/start accepted); Codex committed a change set via MCP, linked it to the prompt, replied `ready_for_review`; the page rendered it without reload |
| C · anchors across Studio, sketches, demonstrations, threads, protection, candidates, undo, jobs, immutable local submissions, run view, comparison | **done** | core + browser + grading tests; comparison is the local leaderboard + `compare` |
| C · full compile/check pipeline | **done** (starter task) | compile ~1 s, reference grade ~4 s with Lean |
| D · skill + references, installers, capability reporting | **done** | `skill/`, `installers.py`; generated references |
| D · Codex adapter: deliver, observe, steer, interrupt, reconcile, re-attach | deliver/observe/reconcile **live**; steer/interrupt implemented against the verified schema, **not exercised live** | |
| D · Claude channel | **contract-tested** only | `test_claude_channel_pushes_studio_prompts_and_reading_acknowledges` |
| D · generic pull fallback | **done**, labelled reduced | |
| E · official API, auth, queue, worker, isolated grader, reports, leaderboard, parity | **done**: SQLite, both inline and containerised; the PostgreSQL path has not run | `test_official.py`; `deploy/official/` |
| E · production deployment | **live** since 2026-09-22 at `https://qccd.academy/official/` (JSON API); authorised by the user | `deploy/official/README.md`; a private smoke submission graded eligible there, with exact parity |
| F · WebMCP / Playwright | **not done** (optional); a CDP driver (`tests/workspace_browser.mjs`) serves testing | |
| demonstration script | **done**; live Codex run: see below | `examples/workspace_demo/demo.py` |

**The demonstration with a live Codex agent** (`demo.py --agent codex`, 2026-09-22, third
run). What happened, in order:

1. *"Use this structure here, but preserve these gate zones"*: Codex asked which source
   structure was meant (`waiting_input`) instead of guessing.
2. The clarification queued behind the running turn and was delivered when that turn ended.
   Codex committed C4 and C5 with two segments through MCP, linked the change set to the
   prompt, and resolved it. C0 and C1 were untouched.
3. The person moved C5 by hand and sent *"Like this; apply the same arrangement to the other
   selected module"*, with the edit as a demonstration and the protected C0 as the target.
   Codex read it as "move C0 the same way". The service's protection check refused the
   preview, and Codex reported that and asked (`waiting_input`). It did not work around the
   protection. (The scripted stand-in reads the same words as "build the mirrored chain
   beside C0", which protection allows. The sentence is ambiguous.)
4. Compile, local reference grade (all ten stages passed, eligible), a later edit leaving that
   result stale and byte-identical, and approve + publish to the development official
   server. That server's independent report agreed exactly (no stage or metric
   differences).

The earlier runs did not complete. In run 1, Codex asked a question and the demo had no
answer step. In run 2, the service was killed from outside twice, which led to the
restart-continuity work in §6.

Tested combination: Windows 11, Python 3.14.3, FastAPI 0.135.3 / Starlette 1.0.0 / uvicorn
0.43.0 / pydantic 2.13.5, MCP SDK 2.2.0 (`mcp`, `mcp-types`), websockets 16.0, Codex CLI
0.154.0-alpha.6.2 (app-server protocol schema generated locally), Chrome headless, Node 24.
Claude Code 2.1.199 is installed but its channel was not exercised live.

## 6 · Known issues and gaps

- **Services killed from outside; cause not found.** On 2026-09-22, processes on this
  machine were terminated several times with no output at all: no traceback, no
  `faulthandler` record, no Windows Application or System event.
  - Twice (about 14:37–14:50 and 15:14–15:21 local time), every `python.exe` on the machine
    died, including another project's long-running workers.
  - In the second live Codex demonstration, only this workspace's service died, twice:
    3 s into a Codex turn (15:24:57) and 2 min after the turn ended (15:27:25). Other Python
    processes survived both times.

  Nothing in this code kills by name or image. Every kill targets a pid: its own
  subprocesses, and an app server it started (checked first to still be Codex). The system
  is built to survive such a kill:
  - A restarted service listens on the **same port** and still honours the browser
    pairings, so an open Studio page's event stream reconnects without a reload.
    `test_a_page_survives_a_service_restart` kills the service mid-session in real Chrome.
  - Adapters and the CLI find or restart the service (`ensure_service`). They never start a
    twin while the old pid lives, and `.qccd/service.lock` allows one service per workspace.
  - Codex sessions re-attach to their thread.
  - Uncertain deliveries are reconciled.
  - Dead jobs become `internal_error` and are not rerun.

  - `qccd studio --keep-alive` stays in the foreground and restarts a service that was
    killed. It does not restart one stopped on purpose (`qccd stop` removes the runtime
    file; a kill leaves it behind).

  **Gap:** without `--keep-alive`, nothing restarts a killed service until an agent or the
  CLI next needs it. A page on its own cannot start one, and shows *disconnected* until then. Two earlier bugs from
  this investigation are fixed: `_pid_alive` treated *access denied* as dead, and a service
  spawned inside an agent's job object could die with it (it now spawns with job breakaway).
- **No prebuilt compiler for macOS**, and none of the Lean checker for any platform. On a
  Mac, build `Compiler/ocaml` from source. Local reference grades stay `unsupported` at the
  certificate stage until `qcheck` is built. `tests/test_workspace_toolchain.py` fails when
  `Compiler/ocaml` changes without a new release (`deploy/toolchain/README.md`).
- The `qccd` console script needs the repository layout. The toolchain reads repo-relative
  files (`arch/` templates, `Compiler/` binaries), so install with `pip install -e .[agent]`
  from a clone. A standalone wheel is not complete.
- The BB board's reference check takes about 6 minutes on this machine, mostly the Lean
  checker. The official grader's memory limit has to cover it (see the deploy notes).
- `studio_sync` build records are hoisted before the seal, as the page does. A device whose
  seal changes form between revisions (generator ↔ explicit) makes an in-flight human edit
  unrebaseable. It is refused, and the page shows the current design.
- `render_html` is called once per service for the design view. The page's other embedded
  data (templates, schema) is fixed at that render.
- A run's page footer (the in-browser engine's estimate) can disagree with the evaluator's
  replay: 569 ms against 522 ms for BB144 on the 24-dock ring. That is the Studio engine's
  parity gap (`tests/parity.mjs`), not the run's. The comparison page says which number is
  the evaluator's.
- The official service is live at `https://qccd.academy/official/`, built from commit
  `10152b1`, which had not been pushed to GitHub when it was deployed. Its leaderboard is
  JSON only; qccd.academy has no page for it yet. Its data volumes are not backed up. The
  PostgreSQL code path has never been run.
- **The website mirror needs the internet** for any page it has not cached; the Studio and
  everything else stay offline. Pages are cached per service process, in memory only.
- Page actions work on the DOM as it is. A site page that builds its controls from script
  without buttons, links or ARIA roles shows fewer controls; the agent can still target
  them by selector or text.
- A live Codex answer on a website page has **not** been observed yet. On 2026-09-23 the
  account's Codex usage limit was reached, and both attempts ended with Codex's
  `usageLimitExceeded`. The full loop is tested with a real MCP client standing in for the
  agent (`test_an_mcp_agent_answers_a_question_from_the_page`); the rerun is
  `QCCD_LIVE_CODEX=1 python tests/workspace_live_page.py`.
- A demonstration is delivered as data (diff, change sets, targets). Generalising it is the
  agent's job, and the service only guarantees that the result is valid and respects
  protection.

## 7 · Running it

**Install** (from a clone; the toolchain reads repository files):

```bash
python -m venv --system-site-packages .venv
.venv/Scripts/pip install -e .[agent]            # or: pip install -r requirements-agent.txt
qccd toolchain install                           # the prebuilt compiler, for runs and compiles
# the reference checks also need the Lean checker (see Compiler/README.md); without it, a
# reference grade reports that stage `unsupported`, which is never eligible
```

`qccd toolchain install` fetches `qccdc_cli` for Windows or Linux on x86-64 from
qccd.academy. It refuses the download unless its size and SHA-256 match
`qccd/workspace/toolchain.json`, installs it under `~/.qccd/toolchain/`, and runs it once on a
test circuit. `qccd toolchain status` says which compiler is in use: `QCCD_QCCDC` first, then
one built in the checkout, then the installed one. How a release is built and published:
`deploy/toolchain/README.md`.

**One workspace, end to end.** Two kinds of words below. **Yours to name, anything you
like:** the workspace folder ("My QCCD designs" is only an example; `qccd init` with no name makes
the folder you are in the workspace; rename or move it later while the service is stopped), every
design (the Studio's design menu, `--design`, or ask the agent), and the name shown if you
publish. **Type as shown:** the commands, and a board by its title or any unambiguous part of it
(`"BB"`, `"surface code"`; `qccd boards` lists them). There is no task to choose: one workspace
takes any number of designs to every board.

```bash
qccd init "My QCCD designs"              # any folder name; or `qccd init` inside a folder you have
cd "My QCCD designs"
qccd agent install --client codex        # .agents/skills/qccd, .codex/config.toml block, AGENTS.md block
qccd agent install --client claude --channel   # .claude/skills/qccd, .mcp.json "qccd", CLAUDE.md block
qccd studio                              # starts the service, opens Studio paired (one-time code)
qccd studio --keep-alive                 # ...and stays, restarting the service if something kills it
qccd web                                 # the qccd.academy website with the same chat on every page
qccd web rules/                          # ...opened at a page
qccd agent connect --client codex        # a NEW Codex thread bound to this workspace
qccd agent connect --client codex --thread <id>   # ...or your existing conversation
#   then attach your terminal to the same app server:  codex --remote ws://127.0.0.1:<port>
#   Claude Code instead: claude --dangerously-load-development-channels server:qccd
qccd boards                              # the leaderboards, by title
qccd submit --local --board "BB [[144,12,12]]" --design "Two triangles" --wait
                                         # compile for the board, adopt, freeze, reference grade
                                         #   -> "Local result - not published"
qccd leaderboard --board "BB [[144,12,12]]"
qccd compile --board surface --adopt     # the steps one by one, when wanted
qccd validate --board surface --json     # a draft grade of the head revision
qccd publish --submission <sub_id>       # review + approve at an interactive terminal
qccd publish --approval <ap_id> --server https://qccd.academy/official   # token: ~/.qccd/credentials.json
qccd trace                               # what the agents did: every request, step by step
qccd trace --session <id> --open         # ...one conversation in the browser, with Replay
qccd status | qccd stop
```

`python -m qccd.workspace <command>` is the same CLI without the console script. Outside a
workspace, `qccd studio` still writes the static page exactly as `python -m qccd studio`
does. `qccd agent install` is idempotent and also serves as repair; `qccd agent status`
reports what is installed and what each client can actually do.

**Offline.** After install, designing, compiling, checking and local grading contact no QCCD
server. The only outbound traffic is the agent's own connection to its provider, and an
explicit `qccd publish`. QCCD uploads nothing by default. Whatever context you give the
agent goes to its provider as part of that agent's normal operation.

**Platforms.** Developed and tested on Windows 11 with Python 3.14.3. The code is portable
(POSIX process groups, rlimits and `fcntl` locks in place of Job Objects and `msvcrt`), but
Linux and macOS were not exercised in this session.

**Tests.**

| file | what it holds | time |
|---|---|---|
| `tests/test_workspace_design.py` | browser/Python replay parity (real studio page through `tests/editor.mjs`), strict JSON, operations | ~10 s |
| `tests/test_workspace_core.py` | change sets, idempotency, conflicts, rebase, protection (every door), import, undo, candidates, prompts, modes, stop, delivery, recovery, a Codex turn finishing after a restart | ~2 s |
| `tests/test_workspace_grading.py` | compile → adopt → reference grade; immutability/staleness; tampered bundles; unsafe archives; publication binding | ~20 s |
| `tests/test_workspace_service.py` | Host/Origin/CSRF/pairing, agent-token limits, body limits, path escapes, hostile text, SSE replay/gap over a real socket, MCP over real stdio, the Claude channel push, a hard-killed service coming back on its port with its pairings | ~35 s |
| `tests/test_workspace_browser.py` | real Chrome: sync, render without reload, frozen lasso/arrow context, protection in the UI, two tabs, presentation with Follow on/off, reconnect, a page surviving a hard kill of the service | ~50 s |
| `tests/test_workspace_contracts.py` | generated JSON Schemas are current; real documents validate | ~2 s |
| `tests/test_workspace_metrics.py` | the evaluator's metrics equal 12 published board entries | <1 s |
| `tests/test_workspace_cli.py` | `qccd` commands as a user runs them | ~5 s |
| `tests/test_official.py` | the official service on SQLite with the inline grader, parity included | ~15 s |
| `tests/test_workspace_claude.py` | Claude Code as a chat agent with a stand-in executable: one conversation resumed, only the QCCD and read-only tools, a failed run's reason in the chat; the model and thinking level the person picks reach the command line; a turn's trace step by step (thinking, Skill and MCP calls with results, cost); a Codex turn traced from its item events | ~8 s |
| `tests/workspace_live_comments.py` | **live**, opt-in (`QCCD_LIVE_WEB=1`): the agent comments on real qccd.academy pages as a signed-in person, against a PRIVATE copy of the comment service | ~20 s |
| `tests/test_workspace_toolchain.py` | the prebuilt compiler: the manifest matches the committed source, a download is refused unless its size and hash match, an install runs once, and which compiler is used | ~3 s |
| `tests/test_workspace_web.py` | the website mirror against a local copy of a site: paths, cache, stale copies, what the mirror refuses, that its origin has no authority, the chat frame's framing, the page-action round trip and who may take part, a page's context in a message; the trace of MCP calls and page actions (and `qccd trace`); the site guide from the site's index and the Studio's hints; in real Chrome every page action on a mirrored page, the Studio's own page actions, the agent's design and runs shown in the person's Studio, and a real MCP client answering a question asked on a page | ~60 s |
| `tests/test_agent_interface.py` | **the gate**: every Studio API function declared (`editor_api.json`), the page actions agree across the service, pageact.js, the MCP tool and the skill, agent-facing text names only agent verbs, the out-of-line list only shrinks; in Chrome every Studio control declared and every `data-hint` described (and a planted undeclared button is caught) | ~10 s |
| `tests/workspace_live_interface.py` | **live**, opt-in (`QCCD_LIVE_WEB=1`): every place of the live site through the mirror; fails on an undeclared control not in its known-debt list | ~10 min |
| `tests/workspace_live_web.py` | **live**, opt-in (`QCCD_LIVE_WEB=1`): the real qccd.academy through the mirror, every kind of page, with console and CSP messages | ~30 s |
| `tests/workspace_live_page.py` | **live**, opt-in (`QCCD_LIVE_CODEX=1`): a question asked on a website page, answered by a real Codex | ~2 min |
| `tests/workspace_live_codex.py` | **live**, opt-in (`QCCD_LIVE_CODEX=1`): a Studio prompt into a real Codex thread | ~1 min |
| `examples/workspace_demo/demo.py` | the full demonstration (scripted or live Codex) | ~1–10 min |

The service, MCP and browser tests need the agent extras (`requirements-agent.txt`); run them
with the venv interpreter. The rest run under the plain toolchain Python.
