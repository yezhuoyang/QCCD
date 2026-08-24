# `Codesign/` — finding the architecture that gets BB codes to break-even

**Read this before writing any code.** It states the one claim this directory has to
support, what is missing before that claim can even be *evaluated*, and the order in which
to find out.

The work is a **double optimisation**: for a fixed architecture, find the best hardware
program; across architectures, compare the best each can do. Neither half is meaningful
without the other — a bad compiler makes a good machine look bad, and a good compiler on a
badly-shaped machine finds a local optimum nobody cares about.

---

## CD0 · The claim, and why it is not yet computable

> **Break-even**: a logical qubit encoded in `BB [[144,12,12]]`, on architecture `A`,
> retains its state better over one unit of wall-clock time than the best *physical* ion
> on the same machine does over that same time.

Written as a number, for a memory experiment of `R` rounds:

```
    ε_L(A)  =  logical error per syndrome round        (needs a DECODER)
    T(A)    =  wall-clock per syndrome round           (have it: res.total_us)
    ε_1(T)  =  a single idle physical ion's error over T   (have it: T2 model)

    break-even margin   M(A)  =  ε_1(T(A)) / ε_L(A)          M > 1 is break-even
```

**Three of those four quantities already exist in this repository. `ε_L` does not.**

`qccd/analysis/budget.py` reports `total_error` — the *summed physical gate infidelity* of
a schedule. That is not a logical error rate and must never be presented as one. Summed
physical error goes **up** with more rounds while logical error goes **down**; optimising
the former directly would pick the architecture that runs the fewest gates, which is the
architecture that does no error correction at all.

**So the first thing this directory builds is `ε_L`, and until it exists there is no
objective and no loop.** Everything in CD1 is prerequisite, not preamble.

---

## CD1 · What has to be built before the loop can start

### CD1.a A circuit-level noise model derived from the compiled schedule

The raw material is already there and is *per-location*, not averaged:

| quantity | where it lives now | what is needed |
|---|---|---|
| two-qubit gate error at each gate | `model.gate_error(arch, nbar)`, called per gate at [`replay.py:399`](../qccd/verify/replay.py) and **summed away** | retain it per gate |
| idle / dephasing per ion | `t2_metrics` ([`cost/physical.py`](../qccd/cost/physical.py)), `res.per_ion_quanta` | per ion, per idle interval |
| SPAM | `spam_error` in `physical.py:160` | per measure / reset |
| heating that causes the above | `res.quanta_components`, split by channel | already per-channel |

The compiled program is a TSIR instruction list with an exact replay, so **every ion's
history is known at every cycle**. That is strictly more information than a uniform
depolarising model, and it is the whole reason this platform can say something a paper
study cannot: the noise is *derived from the transport schedule*, not assumed.

Deliverable: `Codesign/noise.py` — `(TSIR, Architecture, CostModel) -> DetectorErrorModel`,
one error mechanism per (location, channel), with provenance back to the instruction that
caused it.

**Falsification test, run first:** if the derived per-gate error is within a few percent of
`floor_error / n_gate_pairs` for every architecture, then heating is not the discriminator,
geometry barely matters, and this whole study collapses to "minimise round time". That
would be a *result* — report it and stop. Check it before building the decoder.

### CD1.b A decoder

BB codes are not surface codes; matching does not apply. The standard choice is **BP+OSD**
over the check matrix `qccd/codes/bb.py` already builds.

> **Decision D1 — dependencies.** The core tool advertises *zero dependencies*, and that is
> worth protecting. Proposal: `Codesign/` may use `stim`, `ldpc`, `numpy` from a
> `requirements-research.txt`, with **nothing in `qccd/` importing them**, and a pure-Python
> BP+OSD-0 fallback (~300 lines) so the loop still runs on a bare clone, slower.
> *This needs the user's agreement before CD1.b starts.*

### CD1.c The physical baseline

`ε_1(T)` — one ion, idle, no transport, for `T` microseconds. `stationary_chain` is already
in `arch/` for exactly this purpose ("the baseline that already demonstrated break-even").
Use the same `corrected_model`, the same T2, no special-casing.

### CD1.d The evaluator

`Codesign/evaluate.py` — one function, the contract everything else obeys:

```python
def evaluate(arch, program) -> Verdict:
    """Feasible?  Then: rounds/s, logical error per round, break-even margin."""
```

- **Feasibility is not negotiable.** A candidate must pass the 23 rules *and* R10. A
  schedule that violates R1 or computes the wrong circuit is not a fast design, it is not a
  design. This is what makes the study different from a paper: `Compiler/` already proves
  the program implements the circuit.
- Verdicts are appended to a ledger (CD5) and never recomputed.

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

**The CX order is not free.** A wrong order in a weight-6 check turns a single ancilla fault
into a weight-2 data error, which is a *distance* problem, not a speed problem. Any schedule
search must be scored by `ε_L`, never by makespan.

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
slow end and "are not a choice". Under `ε_L` this becomes a real optimum rather than a
frontier: more cooling means fewer gate faults but a longer round, and a longer round means
more idle dephasing. **There is an interior minimum, and finding it is the cleanest early
win in this whole plan.**

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

The user's instruction, and it is the right one. **Nothing below needs the decoder**, so it
runs while CD1 is still being built, and each item can falsify a plan assumption in an hour.

| # | question | how | what it would change |
|---|---|---|---|
| 0.1 | Does heating actually discriminate? | `error_budget` on the 9 shipped devices, same circuit | if not, geometry is irrelevant → report and stop |
| 0.2 | Where is the cooling optimum? | `c5_pareto.py` + a crude `ε_L` surrogate (idle + gate faults, no decoder) | tells us whether the interior minimum exists at all |
| 0.3 | How much does ancilla count matter? | `gen_bb144.py --ancillas 12,24,36,48,72` on `ring144_24v` + `grid9x9` | sizes the schedule axis |
| 0.4 | How much does CX order matter? | 3 hand-written orders, same device, compare data-ion idle | if it is 2%, deprioritise; if 2×, it leads |
| 0.5 | Grid vs ring vs ladder at fixed trap count | the `micro_demo.py` pattern at BB scale | first real intuition about geometry |
| 0.6 | What does the router actually cost? | occupancy sweep per family (`c7_occupancy.py` generalised) | tells us which families the compiler can even serve |

**Deliverable:** `Codesign/FINDINGS.md`, one section per question, each with the command that
produced it. Written as things were learned, including the ones that came out boring.

---

## CD5 · The autoresearch loop

```
   ledger.jsonl ──► propose ──► compile ──► evaluate ──► accept? ──► ledger.jsonl
        ▲             │            │            │                         │
        │             │            │            └── 23 rules + R10, then ε_L, T, margin
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
| surrogate `ε_L` (analytic, no decoder) | ms | inner-loop search |
| BP+OSD, few shots | s | accepting a candidate |
| BP+OSD, full shots + R10 by the proved Lean checker | minutes | the reported result |

A number that reaches `FINDINGS.md` must have been through the top tier. **Never quote a
surrogate as a result.**

---

## CD6 · What would make this study worthless, and how to find out early

| risk | why it is fatal | when it is checked |
|---|---|---|
| `ε_L` is dominated by the error floor | geometry cannot move it; the answer is "buy better gates" | CD4 · 0.1, day one |
| `ε_L` is dominated by data-ion idle time | study collapses to "minimise round time", which needs no decoder | CD4 · 0.2 |
| the decoder is the compute bottleneck | the loop never gets enough samples to distinguish candidates | CD1.b, before the loop |
| the compiler cannot serve a family | comparison silently becomes "which device suits *our router*" | CD4 · 0.6 |
| the noise model is not validated | every number downstream is a story | CD1.a — validate against the shipped 397,184 / 8,808 oracle |
| the search space is too small to contain a good answer | converges fast to something mediocre | after the first convergence: perturb hard, see if it comes back |

The last row deserves emphasis. **A converged loop is not evidence of an optimum.** When it
converges, restart it from three deliberately bad corners; if it returns to the same design,
that is evidence. If it does not, the proposer is the problem.

---

## Decisions needed before CD1

| | question | recommendation |
|---|---|---|
| **D1** | May `Codesign/` use `numpy` / `stim` / `ldpc`? | **Yes**, isolated — nothing in `qccd/` imports them, with a pure-Python BP+OSD-0 fallback. The zero-dependency promise is about the *tool*, not the research. |
| **D2** | Break-even against *what* physical baseline? | one idle ion on the same device under the same model — the strictest honest choice, and `stationary_chain` already exists for it |
| **D3** | Memory experiment only, or logical operations too? | **memory first.** `ε_L` per round is the smallest thing that can support the claim; logical gates multiply the search space before anything is understood |
| **D4** | Which BB code? | `[[144,12,12]]` throughout, so every number is comparable to the shipped artifact. `[[72,12,6]]` as a fast proxy during development |

---

## First session, in order

1. Read this file, `Compiler/PLAN.md` §0–§2 (the trust architecture), `docs/PLAN.md` §1.
2. Get D1 answered. Everything in CD1.b depends on it.
3. **CD4 · 0.1** — the falsification test. One afternoon. If heating does not discriminate
   between the nine shipped devices, write that up and stop; the rest of the plan is void.
4. If it survives: CD1.a, the per-location noise model, validated against the shipped oracle.
5. Only then CD1.b and the loop.

Do not build the optimiser first. The optimiser is the easy part, and an optimiser pointed
at an objective nobody has validated will produce a confident, precise, wrong answer — which
is worse than no answer, because it looks like a result.
