# The small-task co-design demonstration

```bash
python -m venv --system-site-packages .venv && .venv/Scripts/pip install -r requirements-agent.txt   # once
.venv/Scripts/python examples/workspace_demo/demo.py --agent scripted     # no credentials needed
QCCD_LIVE_CODEX=1 .venv/Scripts/python examples/workspace_demo/demo.py --agent codex   # a real Codex agent
```

It runs the brief's sequence against the real system. The pieces are the workspace service,
the Studio page in headless Chrome, the MCP adapter, the OCaml compiler, the Lean checker, the
evaluator, and a development instance of the official service:

1. open the starter design (the four-site chain of the GHZ starter board) and connect the agent;
2. protect the gate zones C0 and C1, lasso the empty region right of C3, sketch an arrow,
   and send *"Use this structure here, but preserve these gate zones."*;
3. the prompt is delivered, the agent changes the design, and the reply appears at the prompt;
4. move the new rightmost trap by hand, then send *"Like this; apply the same arrangement to
   the other selected module."* with that edit as a demonstration and C0 as the target;
5. the agent works from the new revision, leaves C0/C1 and the hand-moved trap as they are,
   and builds the arrangement at C0;
6. compile, adopt the program, and submit locally under the reference profile;
7. change the design again: the old local result is unchanged and marked stale;
8. approve that exact snapshot, publish it to the development official server, and compare
   its independent report with the local one.

It prints one JSON report of the evidence: thread states, attributed history, positions of
the protected and hand-moved nodes, stages and metrics, staleness, and parity. It also saves
a screenshot.

**What `--agent scripted` is and is not.** It is a fixed policy that reads each prompt and
acts only through the QCCD MCP tools. It is **not an AI model**, and it runs in *pull* mode,
so its prompts are delivered when it reads its context. Automatic push into a running agent
is what `--agent codex` exercises: a NEW Codex thread through the app-server bridge. A model's
edits are its own, so the checks are the same but the geometry can differ from the scripted
run.
