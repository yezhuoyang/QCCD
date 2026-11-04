# Agent review of a submission

A person submits a design; the official server grades it, decides, and asks a coding agent with
the design-review skill (`.claude/skills/qccd-design-review`) to explain the decision.

## The experience

One button, two answers, one page.

1. **Submit.** In the Studio's Leaderboard panel (Submit as …), or on the review console of a
   development server (`/review`), the person picks who reviews it: Claude, ChatGPT, or no agent.
   Each choice shows its model and whether it is signed in.
2. **The decision, in minutes.** The server grades the bundle as always. Then the skill's review
   script checks every rule of the board on the bundle and the server's own report, and the
   server decides: *Accepted*, *Not accepted*, *Held for a maintainer*, or *Not decided on this
   server* (a required stage could not run here, never the design's fault).
3. **The review, while you watch.** The agent reads the skill and the rule check and writes the
   review: what fails, where (instruction, site, rail), and how to fix it. The page shows its
   progress live (Running …, Reading …) and the review when it is written.
4. **The result.** The review page (`/review/<submission>`) holds the decision and its reasons,
   anything to download (the re-cooled programme when the heat rules fail), the agent's review,
   and every rule's verdict. In the Studio, the submission's card says the same in a line and
   opens the page.

![A real review: GPT-6.1 Sol (Codex) on the GHZ chain with its cooling taken out, on a development server](img/agent-review.png)

The decision never depends on the agent: it is computed from the grade and the deterministic rule
check (`decide` in `qccd/official/review.py`), before the agent starts. A review that fails, times
out, or says something else leaves it standing. Two people with the same design get the same
decision whichever agent they choose, and text inside a design cannot talk an agent into accepting
it.

## How it runs

```
Studio / console ──submit (X-QCCD-Reviewer)──▶ API ──▶ jobs ──▶ grader (no network, no credentials)
                                                 │                     │ report
                                                 └──▶ reviews ◀────────┘
                                                        │
                                   reviewer: rule check (limited child) ─▶ decision
                                             agent CLI in its sandbox  ─▶ review.md, progress
```

- `qccd/official/review.py`: the reviewer (`python -m qccd.official.worker reviewer`), the decision,
  the agent's command and sandbox, and the parsing of each CLI's event stream.
- `qccd/official/review_pages.py`: the review page and the console.
- `qccd/official/devstack.py`: the whole stack on one computer.
- API: `GET /v1/reviewers`, `POST /v1/submissions/{id}/review`, `GET /v1/submissions/{id}/review`,
  `GET /v1/submissions/{id}/review/files/{name}`; an upload may name its reviewer in
  `X-QCCD-Reviewer`.

The agent works in a job folder outside any repository that holds only the bundle, the report,
the rule check and `out/`. Claude Code runs with its bash sandbox on (no network, writes only in
the folder, no reads of credential folders), `--permission-mode dontAsk`, only the
Bash/Read/Glob/Grep/Write/Edit tools, no MCP servers and project settings only. Codex runs
`exec` in its workspace-write sandbox with no network, without the person's config, rules or
memories. Each gets a minimal environment: no server token, no database URL, no other key.

## Signing the agents in

| Where | Claude | ChatGPT |
|---|---|---|
| This computer (development server, `QCCD_REVIEW_LOCAL_LOGINS=1`) | the person's own Claude subscription, signed in once for reviews | the person's own ChatGPT plan, signed in once for reviews |
| A public server | the server's Anthropic API key (`QCCD_REVIEW_ANTHROPIC_API_KEY`) | the server's OpenAI API key (`QCCD_REVIEW_OPENAI_API_KEY`), or OpenAI's Sign in with ChatGPT program once approved |

On this computer each reviewer has a home of its own (`CODEX_HOME`, `CLAUDE_CONFIG_DIR` under the
stack's folder), signed in once with `python -m qccd.official.devstack login codex` (or `claude`).
A person's usual agent home carries their own instructions and memories into every session that
uses it (Codex reads `~/.codex/AGENTS.md` whatever else is switched off): a review must see none of
that, and a separate sign-in leaves the usual one untouched.

A public server must not offer people's claude.ai logins: "Unless previously approved, Anthropic
does not allow third party developers to offer claude.ai login or rate limits for their products,
including agents built on the Claude Agent SDK" (the Agent SDK's documentation). OpenAI's Codex
documentation recommends API keys for automation; a ChatGPT plan reaches third-party apps only
through Sign in with ChatGPT, a limited program (plan usage for open-source partners and selected
clients). On a public server each review also runs in a container of its own.

## Try it on this computer

```bash
pip install fastapi uvicorn markdown-it-py            # the server; the agent extras grade more fully
python -m qccd.official.devstack login codex           # once: ChatGPT, for reviews
python -m qccd.official.devstack login claude          # once: Claude, for reviews
python -m qccd.official.devstack                       # opens http://127.0.0.1:8300/review
```

Pick an example (or drop a `bundle.zip`), pick the reviewer, and submit. Pass `--grader-python`
with a Python that has the agent extras (`pip install -e ".[agent]"`: qiskit, stim, pymatching) so
the certificate binding, semantics and LER stages run. macOS has no build of the Lean checker, so
on a Mac a design that passes everything else is *Not decided on this server*.

A Studio can submit to the same stack: `devstack` prints the `QCCD_OFFICIAL_URL` and
`QCCD_CREDENTIALS` to start it with. The Studio submits only a design that passed its own test on
the board, which needs the Lean checker too, so on a Mac use the console.
