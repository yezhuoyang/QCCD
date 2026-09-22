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
- **Dock and markers.** Pins on the stage for open prompts, threads with delivery and work
  state and linked change sets/jobs/results, the agent session panel (Stop, Resume, target
  per tab, Follow agent), attributed history with "Undo this change", jobs, and the local
  leaderboard labelled "Local results - not published". There is a publication review dialog.
- **Presentation.** With Follow on, the view widens to show the target (`reveal`) and flashes
  it. With Follow off, a notification with a Show button appears instead. Either way the page
  reports `displayed` / `notified`.
- **Run view.** `/run/<snapshot>` renders an immutable snapshot's device and final program,
  view-only, for comments anchored on generated instructions (`#instr=<id>` focuses one).
- All text from people or agents is set with `textContent`.

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

**Task releases** (`qccd/workspace/releases/<task>@<release>/`) pin the circuit, the physics
and the manifest by sha256. `.gitattributes` keeps their bytes exact. A workspace pins one
release in `qccd.lock.json` and never follows "latest". The starter release is `ghz4@1`
(4-qubit GHZ on a 4-site chain; a full reference grade takes about 4 s). Every metric has a
unit and a direction. The numerical policy is `rel_tol 1e-9 / abs_tol 1e-12`. The track is
`codesign-artifact`: hardware plus final schedule for a fixed circuit.

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
| E · official API, auth, queue, worker, isolated grader, reports, leaderboard, parity | **done** on SQLite + inline grader; Compose/PostgreSQL **written, not run** | `test_official.py`; `deploy/official/` |
| E · production deployment | **not done** (needs authorisation and credentials) | |
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
- The `qccd` console script needs the repository layout. The toolchain reads repo-relative
  files (`arch/` templates, `Compiler/` binaries), so install with `pip install -e .[agent]`
  from a clone. A standalone wheel is not complete.
- Only the `ghz4@1` release ships. `build_release` can cut the five board tasks from
  `tasks/*/task.json`, but those circuits' reference checks take minutes (BB144 needs more
  memory than `qcheck` can use).
- `studio_sync` build records are hoisted before the seal, as the page does. A device whose
  seal changes form between revisions (generator ↔ explicit) makes an in-flight human edit
  unrebaseable. It is refused, and the page shows the current design.
- `render_html` is called once per service for the design view. The page's other embedded
  data (templates, schema) is fixed at that render.
- The Compose deployment, the Dockerfile and the PostgreSQL code path were not executed here.
- A demonstration is delivered as data (diff, change sets, targets). Generalising it is the
  agent's job, and the service only guarantees that the result is valid and respects
  protection.

## 7 · Running it

**Install** (from a clone; the toolchain reads repository files):

```bash
python -m venv --system-site-packages .venv
.venv/Scripts/pip install -e .[agent]            # or: pip install -r requirements-agent.txt
# the reference checks need the built OCaml compiler and the Lean checker (see Compiler/README.md);
# without them, a reference grade reports those stages `unsupported`, which is never eligible
```

**One workspace, end to end:**

```bash
qccd init my-design --task ghz4@1        # lockfile, design/studio.json, .qccd/ (git-ignored)
cd my-design
qccd agent install --client codex        # .agents/skills/qccd, .codex/config.toml block, AGENTS.md block
qccd agent install --client claude --channel   # .claude/skills/qccd, .mcp.json "qccd", CLAUDE.md block
qccd studio                              # starts the service, opens Studio paired (one-time code)
qccd studio --keep-alive                 # ...and stays, restarting the service if something kills it
qccd agent connect --client codex        # a NEW Codex thread bound to this workspace
qccd agent connect --client codex --thread <id>   # ...or your existing conversation
#   then attach your terminal to the same app server:  codex --remote ws://127.0.0.1:<port>
#   Claude Code instead: claude --dangerously-load-development-channels server:qccd
qccd compile --adopt                     # the real compiler; adopts the final program
qccd validate --json                     # a draft grade of the head revision
qccd submit --local --wait               # freeze + reference grade -> "Local result - not published"
qccd leaderboard
qccd publish --submission <sub_id>       # review + approve at an interactive terminal
QCCD_UPLOAD_TOKEN=... qccd publish --approval <ap_id> --server http://127.0.0.1:8300
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
| `tests/workspace_live_codex.py` | **live**, opt-in (`QCCD_LIVE_CODEX=1`): a Studio prompt into a real Codex thread | ~1 min |
| `examples/workspace_demo/demo.py` | the full demonstration (scripted or live Codex) | ~1–10 min |

The service, MCP and browser tests need the agent extras (`requirements-agent.txt`); run them
with the venv interpreter. The rest run under the plain toolchain Python.
