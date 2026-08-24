# The brief for a fresh session

Copy everything between the rules into a new session, from the repository root.

---

You are continuing a codesign study in this repository: **which QCCD architecture runs
`BB [[144,12,12]]` syndrome extraction best**. It is a double optimisation — for a fixed
architecture find the best hardware program, then compare architectures by the best each can
do — and it is meant to run as an iterative loop until it converges.

**Read these first, in this order. Do not skip to the code.**

1. `Codesign/PROGRESS.md` — the state of the study and the single next action
2. `Codesign/EVALUATION.md` — how a design is scored, and why there is no decoder
3. `Codesign/PLAN.md` — what is searched, in what order, and what must not start yet
4. `Codesign/findings/` — what has already been learned
5. `docs/PLAN.md` §0.2 and §6, and `Compiler/PLAN.md` §0–§2 — the objective and the trust
   architecture they were written against

**Then do the "Next action" in `PROGRESS.md`, and nothing else first.**

## The rules this study runs under

- **Verify against the code, not the prose.** These documents drift; `qccd/` and `Compiler/`
  do not. If a plan claims the model does something, `grep` for it before believing it. The
  most important gap already found (G1) was found exactly this way.
- **Feasibility is not negotiable.** A candidate must pass the 23 hardware rules *and* R10,
  the latter by the proved Lean checker for anything reported. An infeasible schedule has no
  performance — do not report its numbers.
- **G1 is a blocker.** The gate-error model has no chain-length term, so longer ion chains
  cost nothing and the optimiser would drive trap capacity to R13's hard cap of 15 and report
  that cap as an optimum. **Nothing may sweep trap capacity until G1 is closed.** Other work
  may proceed in parallel.
- **Recalibrate after every model change.** The shipped `ring144_24v` schedule must keep
  reproducing **397,184 cost / 8,808 steps**. That replay is the oracle the whole platform is
  validated against; a model change that breaks it is a regression, not a refinement.
- **Do not build the optimiser yet.** An optimiser pointed at an objective nobody has
  validated produces a confident, precise, wrong answer, which is worse than no answer. The
  loop (CD5) starts after G1 and after CD4's questions have been answered.
- **CX order within a check is exempt from the optimiser.** A bad order turns one ancilla
  fault into a weight-2 data error, and a metric that counts expected faults scores the good
  and bad orders identically. Fix it from the published depth-7 schedule.

## Where progress goes

```
Codesign/
  PROGRESS.md    state, next action, decisions, session log   ← update at the END of every session
  findings/      one markdown file per question               ← the write-up IS the deliverable
  scripts/       one runnable script per question
  data/          raw JSON each script emits, committed
```

- **Every number in a finding names the command that produced it.** A claim that cannot be
  re-run is not a finding.
- **Every script writes its raw output to `data/`**, so a later session can re-check a claim
  without re-deriving it.
- **Write up the boring results too.** A question that came out flat is what stops the next
  session repeating the work. `findings/q01` says the device spread is only 1.3×; that is
  unwelcome and it is the most useful thing in the folder.
- **Update `PROGRESS.md` before you finish** — the status table, the next action, any decision
  taken, and one line in the session log. A session that leaves it stale has cost the next one
  an hour.

## What "done" looks like for a session

Not "the loop converged". A session is done when:

1. the next action in `PROGRESS.md` has been carried out and written up in `findings/`,
2. the script that produced it is committed and re-runnable,
3. `PROGRESS.md` names the *new* next action, and
4. anything the result invalidated in `PLAN.md` or `EVALUATION.md` has been corrected there,
   not just noted.

If a result kills part of the plan — the falsification test fires, an axis turns out flat, the
metric fails to discriminate — **that is a successful session.** Say so plainly, correct the
plan, and stop; do not rescue a dead branch by weakening the test.

## Current state, in one paragraph

The objective is settled and needs no decoder: score a design by `p_eff` (expected faults per
operation, from `qccd.cost.t2_metrics`) and `T_round`, with the logical error rate only ever
as an explicitly-labelled extrapolation from Bravyi's published threshold. Question 0.1 has
been answered on 39 already-compiled programs: about half the error budget is transport-
attributable, so geometry matters and the study is live — but best-to-worst across devices is
only **1.3×**, against the three orders of magnitude Murali *et al.* report. The next action
tests whether that is because the circuits used were too small, or because the model is
missing the chain-length term (G1). Wiring has already been found invisible to the objective
and demoted to a constraint.

---
