# Progress

**The state of the study, in one file.** A session starts by reading this and ends by
updating it. If this file and the code disagree, the code is right and this file is a bug.

- **What is being asked** — [`PLAN.md`](PLAN.md)
- **How a design is scored** — [`EVALUATION.md`](EVALUATION.md)
- **What has been learned** — [`findings/`](findings/), one file per question
- **How to start a session** — [`SESSION_PROMPT.md`](SESSION_PROMPT.md)

---

## Next action

> **Re-run `q01` on `bb144_esm`.** The device spread on small circuits is only 1.3×; before
> concluding that the model is missing a term (G1), rule out that the circuits were too small
> to stress transport. This is one command plus whatever compilation the matrix has not
> already done.

---

## Status

| | item | state | where |
|---|---|---|---|
| **CD4 · 0.1** | does the architecture move the score? | ✅ **answered** — yes, median 48% of the budget, but only 1.3× best-to-worst | [findings/q01](findings/q01-error-structure.md) |
| CD4 · 0.1b | the same, on `bb144_esm` | ⬜ next | — |
| CD4 · 0.2 | where is the cooling optimum? | ⬜ | — |
| CD4 · 0.3 | how much does ancilla count matter? | ⬜ | — |
| CD4 · 0.4 | how much does CX order matter? | ⬜ *exempt from the optimiser, still worth measuring* | — |
| CD4 · 0.5 | grid vs ring vs ladder at fixed trap count | ⬜ | — |
| CD4 · 0.6 | what does the router actually cost? | ⬜ | — |
| **G1** | chain-length term in the gate error | 🔴 **BLOCKER** — nothing may sweep trap capacity until closed | [EVALUATION §4](EVALUATION.md) |
| G2 | anomalous heating from the solved ion height | ⬜ | [EVALUATION §4](EVALUATION.md) |
| G3 | idle error linear vs Gaussian | ⬜ | [EVALUATION §4](EVALUATION.md) |
| CD5 | the loop | ⬜ not started, and must not start before G1 | [PLAN CD5](PLAN.md) |

## Decisions taken

| | decision | when |
|---|---|---|
| **no decoder** | designs are scored by `(p_eff, T_round)` from `t2_metrics`; logical error only ever as a labelled extrapolation from Bravyi's published curve | at planning |
| **wiring is a constraint, not an objective** | `grid9x9` and `deck_unit_cell` score *identically* — the error model is blind to wiring. It enters as R4 feasibility and DAC count | [q01](findings/q01-error-structure.md) |
| **CX order is exempt from the optimiser** | a metric counting expected faults cannot see that a bad order turns one ancilla fault into a weight-2 data error | [EVALUATION §5](EVALUATION.md) |

## Open questions raised by the work itself

- Does the 1.3× device spread grow with circuit size, or is it the model? (**next action**)
- `p_eff` sits 10–25× below threshold everywhere. Is any reachable design near it? If not,
  the study is about margin, and should say so in its title.
- The cooling pass converts heating into runtime before the measurement is taken. How much of
  the architecture signal is hidden there? (0.2)

---

## Layout

```
Codesign/
  PLAN.md            what is searched, in what order, and what must not start yet
  EVALUATION.md      the metric, its literature, and the three model gaps
  PROGRESS.md        this file — state, next action, decisions
  SESSION_PROMPT.md  the brief for a fresh session
  findings/          one file per question; the write-up IS the deliverable
  scripts/           one script per question, named for it, runnable standalone
  data/              raw JSON from each script, committed so a claim can be re-checked
```

**Rules that keep this honest.** Every number in `findings/` names the command that produced
it. Every script writes its raw output to `data/`. A finding that came out boring is still
written up — the boring ones are what stop the next session repeating the work.

## Session log

| date | what happened |
|---|---|
| 2026-08-24 | Plan and evaluation written. `q01` run on 39 existing programs: the falsification test does not fire (median 48% of `−ln F` is device-attributable), but best-to-worst is only 1.3× against the literature's three orders of magnitude. Wiring found to be invisible to the objective. |
