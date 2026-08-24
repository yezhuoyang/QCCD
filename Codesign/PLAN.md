# `Codesign/` — finding the architecture that runs BB codes best

**Read [`EVALUATION.md`](EVALUATION.md) first**, then this. That file settles what a design
is scored by; this one settles what is searched and in what order.

The work is a **double optimisation**: for a fixed architecture, find the best hardware
program; across architectures, compare the best each can do. Neither half is meaningful
without the other — a bad compiler makes a good machine look bad, and a good compiler on a
badly-shaped machine finds a local optimum nobody cares about.

---

## CD0 · The objective

**No decoder, no logical simulation.** A design is scored by two numbers this repository
already computes for every compiled program:

```
T_round  =  wall-clock for one BB[[144,12,12]] syndrome round      res.total_us
p_eff    =  neg_log_fidelity / (fault locations)                   mean fault probability
            neg_log_fidelity = Σ_gates ε(n̄) + N·T/T_coh + Σ_spam
```

`p_eff` is the quantity the code's published **0.7 % threshold** is quoted against, so the
first thing any candidate reports is `p_eff` versus 0.7 %. A design at 5 % is not slow; the
code does not help it at all, and no compiler work will fix that.

This is the same construction the closest published precedent uses — Murali *et al.* (ISCA
2020) rank QCCD architectures by multiplying per-operation fidelities, with no simulation.
[`EVALUATION.md`](EVALUATION.md) gives the derivation, the optional ranking scalar, the
optimistic/pessimistic bracket, and the five things this metric cannot see.

**Time is a term, not a rival objective.** Cooling more lowers `n̄` at each gate and lengthens
the round; a longer round costs idle dephasing, which is *already inside* the same scalar. So
there is an interior optimum in the cooling budget rather than a frontier to pick a point on.

---

## CD1 · Three model gaps that must close before searching

Derived in [`EVALUATION.md`](EVALUATION.md) §4. The first is a blocker; the other two change
answers but do not invalidate them.

### G1 — gate error has no chain-length term · **BLOCKER**

`gate_error(arch, nbar) = ε₀ + k·n̄`. No dependence on how many ions share the trap.
**Trap capacity is one of the knobs we intend to search**, and with no penalty for long
chains, more ions per trap is strictly better — fewer shuttles, no cost. The optimiser will
drive capacity to R13's hard cap of 15 and report the cap as an optimum. That is the
constraint being read back, not a finding.

Murali *et al.* locate the real optimum at **15–25 ions**, and it exists *because* of the
`A ∝ N/ln N` laser-instability term this model omits. Add it, with the `Γτ` term so gate
duration costs something:

```
ε(n̄, N, τ)  =  ε₀  +  Γ·τ(N)  +  κ · (N / ln N) · (2n̄ + 1)
```

**Calibrate against the shipped oracle**: `ring144_24v` at its current chain length must
reproduce its current error. This is a refinement of a validated model, not a replacement.

### G2 — anomalous heating is a constant, but `qccd/phys/` computes the ion height

Field noise scales as **d⁻⁴**. The solver already returns `ion_height_um`, and the repo has
already measured that no shipped device sits at its design height — neighbouring metal moves
it by up to 15%, which under d⁻⁴ is a **1.75× error in the heating rate** on every ion.

Deriving `Γ_anom` from the solved height gives **layout → electrodes → ion height → heating →
gate error** end to end. A study without a field solver has to assume this number; this one
can compute it. It is the part of the result no purely algorithmic paper can produce.

### G3 — idle error is linear in time

`idle_error = N · T / T_coh`. Hyperfine ion memory under correlated field noise is often
closer to `(t/T₂)²`. Linear is conservative and defensible, but it **directly sets the cooling
optimum**, so run the loop under both and report whether the winner changes.

---

## CD2 · The inner loop — best program for a fixed architecture

Four axes, in the order the user named them. All four are currently *fixed* by fiat, and
each fixed choice is an unexamined assumption.

### 1. Syndrome schedule — which ancilla serves which check, and in what order

[`qccd/codes/bb.py`](../qccd/codes/bb.py) says so itself: *"which ancilla serves which
check, and in what order, is a scheduling decision"*. Right now
[`gen_bb144.py`](../Compiler/bridge/gen_bb144.py) makes one arbitrary choice and exposes a
single knob, `--ancillas`.

| sub-axis | space |
|---|---|
| ancilla count | 12 … 144 (reuse via mid-circuit measure/reset) |
| check → ancilla binding | an assignment problem; locality-aware vs round-robin |
| CX order within a check | 6 members; the order sets which data qubits idle longest |
| interleaving of X and Z checks | affects hook errors and ancilla contention |

**The CX order is not free, and `p_eff` cannot see why.** A wrong order in a weight-6 check
turns a single ancilla fault into a weight-2 data error — a *distance* problem, not a speed
one, and two orders that differ this way score identically under a metric that only counts
expected faults ([`EVALUATION.md`](EVALUATION.md) §5). So this sub-axis is the one exception
to CD0: fix the CX order from the published depth-7 schedule and do **not** let the optimiser
move it, unless and until something that can see error structure is added.

### 2. Mapping — which ion carries which qubit

Exists: greedy weighted insertion, a spectral (Fiedler) candidate, hill-climbing, with
tiering by capability and capacity ([`Compiler/ocaml/lib/place.ml`](../Compiler/ocaml/lib/place.ml)).
Measured finding to respect: **spectral placement lost to greedy on every instance tried.**
Do not re-propose it without a new reason.

Unexplored: placement that knows the *code* — data qubits sharing a check placed within one
transport hop; ancilla docks placed at the centroid of the checks they serve.

### 3. Routing

Exists: prioritised planning with space-time A\* over a reservation table, multi-order
retry, plus rigid rotation for conveyor devices. Known limit, measured: the individual-ion
router stops at **46.2% loop occupancy**; rigid rotation has no such limit but preserves
cyclic order, so it only serves bipartite interaction graphs.

Unexplored: the SAT encoding from `Compiler/PLAN.md` §6 as an *optimiser* rather than an
oracle; hybrid rotation + local swaps; routing that reorders commuting `cx` gates
(the commutation rule is already **proved** in
[`Cert/Commute.lean`](../Compiler/lean/QCCDC/Cert/Commute.lean), so a scheduler may use it
and still pass R10).

### 4. Cooling budget

Exists and is the one axis already understood: `max_gate_quanta` traces a genuine
(runtime, error) frontier, measured at **2.19× runtime for 22.5× error** by
[`c5_pareto.py`](../Compiler/bridge/c5_pareto.py). The three shipped policies sit at the
slow end and "are not a choice". Under `p_eff` this becomes a real optimum rather than a
frontier: more cooling means fewer gate faults but a longer round, and a longer round means
more idle dephasing — and both are terms in the same scalar. **There is an interior minimum,
and finding it is the cleanest early win in this whole plan.**

> **Fairness protocol.** Every architecture gets the **same inner-loop compute budget**
> (same number of `evaluate` calls, same proposer, same seeds). Otherwise the outer
> comparison measures how hard we searched, not how good the machine is. Record the budget
> in the ledger with every verdict.

---

## CD3 · The outer loop — architecture

The search space is the six generators in
[`qccd/arch/generators.py`](../qccd/arch/generators.py), which take integers:

| generator | parameters | what it trades |
|---|---|---|
| `ring(width, height, verticals)` | `verticals` is the dock count | ancillas ↔ junctions on the rotation path |
| `grid(a, b)` | lattice size | gate-anywhere ↔ junction count |
| `ladder(width, rungs, highways)` | rungs, highways | local coupling ↔ bypass routes |
| `dual_loop(width, couplings)` | coupling positions | data loop still, ancilla loop turning |
| `racetrack(straight)` | straight length | zero junctions ↔ path length |
| `chain(n)` | — | the control |

Beyond geometry, and cheaper to sweep than it looks:

- **zone capacity** — how many ions a trap holds (R13 caps a gate chain at 15)
- **which zones can gate** — the ring's whole character is "only docks gate"
- **the wiring** — `direct` / `wise` / `broadcast_groups`. Measured: the same 144-trap
  lattice costs **5,760 DACs or 44**. A design that wins by 10% and needs 130× the control
  hardware has not won.

**Constraints that make a candidate real, not just a graph.** `qccd/phys/` derives the
electrodes: a device that fails DRC (`rf_dc_clearance` already fails on `ring144_24v`'s dock
spurs) cannot be built. Feed DRC and DAC count into the outer loop as constraints, not
objectives — this is the part of the study a purely algorithmic paper cannot do.

---

## CD4 · Phase 0 — cheap exploration, before any optimiser

The user's instruction, and it is the right one. **Nothing below needs a new model term**,
so it runs while CD1 is being closed, and each item can falsify a plan assumption in an hour.

| # | question | how | what it would change |
|---|---|---|---|
| 0.1 | Does heating actually discriminate? | `error_budget` on the 9 shipped devices, same circuit | if not, geometry is irrelevant → report and stop |
| 0.2 | Where is the cooling optimum? | `c5_pareto.py`, scoring each point by `p_eff` rather than by gate error alone | the interior optimum CD0 predicts — or its absence |
| 0.3 | How much does ancilla count matter? | `gen_bb144.py --ancillas 12,24,36,48,72` on `ring144_24v` + `grid9x9` | sizes the schedule axis |
| 0.4 | How much does CX order matter? | 3 hand-written orders, same device, compare data-ion idle | if it is 2%, deprioritise; if 2×, it leads |
| 0.5 | Grid vs ring vs ladder at fixed trap count | the `micro_demo.py` pattern at BB scale | first real intuition about geometry |
| 0.6 | What does the router actually cost? | occupancy sweep per family (`c7_occupancy.py` generalised) | tells us which families the compiler can even serve |

**Deliverable:** `Codesign/FINDINGS.md`, one section per question, each with the command that
produced it. Written as things were learned, including the ones that came out boring.

None of these needs the new model terms, so CD4 runs *while* G1 and G2 are being closed --
except any sweep over trap capacity, which must wait for G1.

---

## CD5 · The autoresearch loop

```
   ledger.jsonl ──► propose ──► compile ──► evaluate ──► accept? ──► ledger.jsonl
        ▲             │            │            │                         │
        │             │            │            └── 23 rules + R10, then p_eff, T_round
        │             │            └── Compiler/, with the inner-loop knobs as flags
        │             └── coordinate descent over the axis with the largest measured slope
        └───────────────────────────────────────────────────────────────────┘
```

**State is one append-only file.** `Codesign/ledger.jsonl`, one JSON object per evaluated
candidate: the architecture document hash, the compiler settings, every metric, the rule
verdicts, the R10 verdict, wall-clock spent, and the git SHA that produced it. A fresh
session reads the ledger and continues; nothing else is session state. Same discipline as
`Compiler/`: **untrusted search, checked results, everything reproducible from a file.**

**Proposer.** Start with coordinate descent on the axis whose measured derivative is
largest — `budget.py` already reports exact derivatives per channel, so the first proposal
is informed rather than random. Do **not** start with Bayesian optimisation: with a
surrogate nobody trusts yet, it will confidently converge on an artifact of the surrogate.

**Acceptance.** Feasible (23 rules + R10) **and** `margin` improved by more than the
evaluator's own noise. Infeasible candidates are recorded, not discarded — the boundary of
what the compiler can serve is itself a finding (46.2% occupancy came from exactly this).

**Convergence.** Stop when no axis yields > 2% margin improvement over `K = 3` consecutive
rounds, or the compute budget is spent. Report the Pareto set, not a single winner: margin
against DAC count against die area are genuinely rival, and collapsing them to one number
hides the engineering.

**Fidelity ladder** — the same fast/proved discipline the compiler already uses:

| tier | cost | used for |
|---|---|---|
| T1 combinatorial — steps, hops, junction transits | µs | inside the inner loop |
| T2 physical — replay → `p_eff`, `T_round`, the channel split | ms | accepting a candidate |
| T2 + feasibility — 23 rules and R10 by the **proved Lean checker** | s–min | anything reported |

A number that reaches `FINDINGS.md` must have passed the top tier: **an infeasible schedule
has no performance.** T1 may rank; only T2 may be quoted.

---

## CD6 · What would make this study worthless, and how to find out early

| risk | why it is fatal | when it is checked |
|---|---|---|
| `p_eff` is dominated by the gate floor `ε₀` | geometry cannot move it; the answer is "buy better gates" | CD4 · 0.1, day one |
| `p_eff` is dominated by data-ion idle time | study collapses to "minimise round time" and the heating model stops mattering | CD4 · 0.2 |
| **G1 not closed before searching capacity** | the optimiser reports R13's hard cap as an optimum | CD1 · G1, before any sweep over trap size |
| every candidate sits far above 0.7 % | nothing being compared would work; the ranking is of losers | CD4 · 0.1 — compare to threshold immediately |
| the compiler cannot serve a family | comparison silently becomes "which device suits *our router*" | CD4 · 0.6 |
| the model is not recalibrated after G1/G2 | every number downstream drifts from the validated oracle | on each model change — the 397,184 / 8,808 replay must still hold |
| the search space is too small to contain a good answer | converges fast to something mediocre | after the first convergence: perturb hard, see if it comes back |

The last row deserves emphasis. **A converged loop is not evidence of an optimum.** When it
converges, restart it from three deliberately bad corners; if it returns to the same design,
that is evidence. If it does not, the proposer is the problem.

---

## Decisions needed before CD1

| | question | recommendation |
|---|---|---|
| **D1** | Where do `κ` and `Γ` in the new gate model come from? | calibrate on the shipped `ring144_24v` schedule so its current error is reproduced, and take the `N/ln N` *shape* from Murali *et al.* Alternative — a literature value per trap technology — is better if a source can be found; **this is the largest single assumption in the study** |
| **D2** | Report the optional `Λ = ε_L/T_round` ranking scalar at all? | **yes, labelled an extrapolation.** It costs nothing, makes the time/error trade explicit rather than arbitrary, and never appears without `(p_eff, T_round)` beside it |
| **D3** | Memory round only, or logical operations too? | **memory round first.** One ESM round is the smallest unit that exercises transport, gates, SPAM and idling together; logical gates multiply the search space before anything is understood |
| **D4** | Which BB code? | `[[144,12,12]]` throughout, so every number is comparable to the shipped artifact and to Bravyi's published threshold. `[[72,12,6]]` as a fast proxy during development |

---

## First session, in order

1. Read [`EVALUATION.md`](EVALUATION.md), then this file, then `docs/PLAN.md` §6 and §0.2
   (which already argue the objective), then `Compiler/PLAN.md` §0–§2 (the trust architecture).
2. **CD4 · 0.1** — the falsification test. One afternoon, no new code: `error_budget` across
   the nine shipped devices, and `p_eff` against 0.7 %. If heating does not discriminate, or
   if everything is far above threshold, write that up and stop.
3. If it survives: close **G1**. Nothing may sweep trap capacity before it is closed.
4. Then G2 — wire `qccd/phys`'s solved ion height into the heating rate. Re-validate the
   397,184 / 8,808 oracle after each model change.
5. Then CD4's remaining questions, then the loop.

Do not build the optimiser first. The optimiser is the easy part, and an optimiser pointed
at an objective nobody has validated will produce a confident, precise, wrong answer — which
is worse than no answer, because it looks like a result.
