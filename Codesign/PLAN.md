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
the round; a longer round costs idle dephasing, which is *already inside* the same scalar. That
is why this study has one objective rather than a frontier — but it does **not** follow that the
cooling budget has an interior optimum, and [`findings/q01b`](findings/q01b-bb144-and-the-floor.md)
§5 measures that it does not: at `T_coh = 600 s` a 300 µs cool costs `8.4e-5` of idle and saves
up to `2.0e-3` per gate it protects, so the curve is monotone and the optimum is R7's cap.

> **What the objective actually looks like, measured.** It is **96 % floor** — a constant
> `n_pairs·ε₀ + spam` identical on all nine architectures. R7's `max_quanta = 1.0` bounds the
> gate term at **2.09×** between any two *feasible* schedules, and the cooling pass drives every
> schedule to the bottom of that interval (mean gate n̄ = **exactly 0** on the BB round). The
> device-attributable share of `−ln F` is **3.9 % at the median** over 73 verified pairs. The
> geometry signal is not weak — the device-attributable *part* spreads 19–121× — it is diluted.
> **Report the device-attributable part beside `−ln F`, or a ranking is dominated by a
> constant.**

---

## CD1 · Three model gaps that must close before searching

Derived in [`EVALUATION.md`](EVALUATION.md) §4. **G1 is closed**
([`findings/g1`](findings/g1-chain-length.md)); G2 and G3 remain, and change answers without
invalidating them.

### G1 — gate error has no chain-length term · ✅ **CLOSED** 2026-08-24

> [`findings/g1`](findings/g1-chain-length.md). Declared as `error_vs_chain: "murali:2"` on
> all nine architectures; `gate_error` reads the chain length the way R13 does. **The
> calibration has no free parameter** — requiring equality with the old model at `N_ref` for
> every `n̄` pins both constants, so D1 reduces to borrowing the shape. Bit-identical at
> `N_ref`; 71 of 73 verified programs unmoved; oracle still 397,184 / 8,808.
>
> R13's cap of 15 ions now costs **1.50×** at `n̄ = 0` (**1.72×** at R7's cap), and
> `stationary_chain` fell from **4th of 9 to last**. **Trap capacity is now safe to sweep.**
> But seven of nine devices are capacity-2, so G1 adds no signal to today's comparison — and
> nothing in the compiler ever *chooses* a long chain, which is the next thing to check.

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
                      └──────────────┬─────────────────┘
                        both non-zero at n̄ = 0
```

**And it is now the only repair on this list that can restore an architecture signal.**
[`findings/q01b`](findings/q01b-bb144-and-the-floor.md) §4: the cooling pass drives the mean
gate n̄ to *exactly zero* on the BB round, so anything reaching the gate error through `n̄` is
laundered into runtime — and runtime is nearly free. G1's terms survive at `n̄ = 0`. G2's do
not; it reaches the objective only as layout → heating → cooling stops → runtime → idle, which
is 5.7 % of `−ln F`.

**Calibrate against the shipped oracle**: `ring144_24v` at its current chain length must
reproduce its current error. This is a refinement of a validated model, not a replacement.
*Done, and exactly rather than approximately — the two models are the same function of `n̄` at
the reference chain length, bit for bit.*

### G2 — anomalous heating is a constant, but `qccd/phys/` computes the ion height

Field noise scales as **d⁻⁴**. The solver already returns `ion_height_um`, and the repo has
already measured that no shipped device sits at its design height — neighbouring metal moves
it by up to 15%, which under d⁻⁴ is a **1.75× error in the heating rate** on every ion.

Deriving `Γ_anom` from the solved height gives **layout → electrodes → ion height → heating →
gate error** end to end. A study without a field solver has to assume this number; this one
can compute it. It is the part of the result no purely algorithmic paper can produce.

### G3 — idle error is linear in time

`idle_error = N · T / T_coh`. Hyperfine ion memory under correlated field noise is often
closer to `(t/T₂)²`. Linear is conservative and defensible; run the loop under both and report
whether the winner changes. It does **not** set the cooling optimum, as this plan previously
claimed: at 522 ms against `T₂ = 600 s` the quadratic law makes idle **1150× smaller**, so both
laws put the cooling optimum at the same boundary
([`q01b`](findings/q01b-bb144-and-the-floor.md) §5).

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
slow end and "are not a choice".

> ~~**There is an interior minimum, and finding it is the cleanest early win in this whole
> plan.**~~ **There is not, and it was not.** Re-scoring that same measured frontier under the
> full objective gives a **monotone** curve
> ([`q01b`](findings/q01b-bb144-and-the-floor.md) §5): gate error falls 22.5× as the budget
> tightens while idle rises only 2.2× from a base an order of magnitude smaller. The minimum
> sits at R7's cap of 1.0 quantum — where the shipped policy already sits. The reason is
> `T_coh = 600 s` against round times of tens to hundreds of ms: a 300 µs cool costs `8.4e-5`
> of idle and buys up to `2.0e-3` on **each** gate it protects, so it repays itself 24× on the
> first one. **This axis is closed. It has no knob left in it**, and it is why the heating
> signal is missing from the objective rather than a place to look for one.

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
  hardware has not won. **This is a constraint and a cost, never an objective term**:
  [`q01`](findings/q01-error-structure.md) measured `grid9x9` and `deck_unit_cell` — the same
  lattice, 5,760 DACs against 44 — scoring *identically* on every shared circuit. The error
  model is blind to wiring, correctly so, and it enters only as R4 drivability and DAC count.

**Constraints that make a candidate real, not just a graph.** `qccd/phys/` derives the
electrodes: a device that fails DRC (`rf_dc_clearance` already fails on `ring144_24v`'s dock
spurs) cannot be built. Feed DRC and DAC count into the outer loop as constraints, not
objectives — this is the part of the study a purely algorithmic paper cannot do.

### C1 — the space-efficient rail-and-storage layout · **requested by a collaborator**

A sketch, and the request: *"Since the BB codes are already set up on the ring architecture,
do you think you could implement this space-efficient version as well? It'd be awesome to
compare it directly to the single-ion ring setup."*

**This is not a side quest. It is the first serious second candidate**, and B2 says the study
does not currently have one. Take it on those grounds alone.

#### What the sketch shows, as read — *confirm before building*

Four horizontal transport rails, alternating in two colours, closed into **two interleaved
loops** (the brackets at both ends). Vertical rungs cross all four. The vertical segments
*between* rails are ringed in yellow and labelled **Storage**; the arrows show ions leaving
storage onto a rail, travelling, and returning.

So: **ions are parked densely in the vertical columns, and the horizontal rails are kept
mostly empty as transport.** That is where "space-efficient" comes from — segments that in a
plain grid are only wires here also hold qubits.

**Answered by the author, 2026-08-24:**

| | question | answer |
|---|---|---|
| **a** | two independent closed loops, or one fabric in two colours? | **two independent closed loops** |
| **b** | where do gates happen? | **on the rails** — read from a "yes" to a three-way question, and consistent with the green dots sitting on the green rails in the sketch. *One word corrects it if wrong; it is the assumption everything below rests on.* |
| **c** | ions per storage column? | **typically 2, but expose it as a generator parameter** |

**Corrected by the author after the first draft of this section:** *"we add a 'Storage'
design, which is essentially a **larger Site**. The **idle ions** can be put in the storage."*
So storage is not a same-size site used differently — it is a site with **larger capacity than
a rail site**, and its job is to hold ions that are **not currently being gated**. `typically
2` is the default value of that parameter, not a claim that storage equals a rail site.

#### What the design actually is: two media, not one

This is the point, and it took the author's correction to see it. Every device in the corpus
today has **one** medium — the same sites both hold ions and carry them. `ring144_24v` is 168
capacity-2 sites, each holding a resident ion, and transport happens by moving those residents
around each other. That is why occupancy and routability are the same problem there.

This design **separates them**:

| | rails | storage |
|---|---|---|
| capacity | small — a transit lane | **larger**, a parameter (default 2) |
| holds | ions in flight, and gates (b) | **idle ions**, parked off the transport path |

**That is an architectural answer to B2, and it is a better one than the swap router.** Five of
nine devices fail the BB round because our planner needs an empty destination and 58 %
occupancy leaves none. Here the occupancy that matters is *the rails'*, and idle ions are not
on them. The rails can be kept near-empty **no matter how many qubits the device holds** —
because capacity is bought in storage, which is not a transport medium. The occupancy ceiling
stops being a function of qubit count.

#### What it costs, and what it does not

**It does not buy fidelity, and the plan should say so plainly.** Under this model an idle ion
in storage is charged exactly what an idle ion on a rail is charged: `idle_error` is
`n_ions × T_round / T_coh` with no reference to position, and `anomalous_per_us` accrues per
ion per microsecond wherever it sits. **So the entire benefit of storage, as the model stands,
is routability and area — not error.** That is not a defect of the design; it is a limit of the
metric, and it belongs in the write-up beside the result rather than being discovered later.

What it pays:

- **`eject` / `reinsert` on every gate.** Those entail `split` + `merge` at **~6 quanta each
  against ~0.1 for a shuttle hop — a 60× heating ratio** (`qccd/compile/oddeven.py`). This is
  the dominant cost and it is charged automatically.
- **R14, and only once storage is deeper than 2.** *"An ion must be at a trap edge to split;
  getting there costs a 3-CX swap"* — flat 3 CX per position of burial, distance-independent.
  At the default capacity 2 both ions are at an edge and it is **free**; at 3 and above,
  extracting a buried ion costs 3 CX **per position**, i.e. ~5.5e-3 of gate error against a
  gate's own 1.84e-3. **An extraction from one position deep costs three times what the gate it
  enables costs.** That is a steep wall, and it is very likely why the author says 2 is typical.
- **A scheduling opportunity that follows directly**: R14 charges for *burial depth*, so a
  placer that keeps the next-needed ion at the edge of its storage chain pays nothing even in a
  deep column. Ordering the storage chain by next-use is the obvious move and nothing in the
  compiler does it today. This is the most interesting piece of new work in C1.

**G1 does not bite here, and it also did not unblock this.** The chain-length term is priced
**at a gate**; with gates on the rails (b) and storage never gated, `gate_error` never sees a
storage chain. So `storage_capacity` may be swept regardless of G1 — the rule *"nothing may
sweep trap capacity until G1 is closed"* covers **gate-capable** capacity, which this is not.
Worth stating precisely rather than claiming today's G1 work as the enabler.

> **A prediction worth writing down before measuring it.**
> [`q01b`](findings/q01b-bb144-and-the-floor.md) found that the cooling pass converts heating
> into runtime and that runtime is nearly free at `T_coh = 600 s`. If that holds, this layout
> pays its 60× heating ratio **in cooling time and almost nothing in score** — so it should win
> on die area and DAC count while scoring level with the ring. If instead it scores clearly
> worse, `q01b`'s central finding has a limit we have not found yet, and *that* is the more
> valuable result. Either way the experiment is worth running.

#### Why it is worth the effort, in this study's own terms

- **It is an architectural answer to B2.** The whole reason 5 of 9 devices fail to route the
  BB round is that our planner needs an *empty destination* and 58 % occupancy leaves none. A
  layout that deliberately concentrates ions in storage and **keeps the rails empty** may
  route where `grid9x9` does not, with no compiler change at all. That is a hypothesis this
  platform can test in an afternoon, and it is a genuinely different answer from the swap
  router — worth having both, because they fail differently.
- **Its cost is already modelled**: `eject`/`reinsert` heating always, plus R14 once storage
  is deeper than 2. The movement classes declare `entails` and the cost model reads it. **No
  new physics is needed to evaluate this design** — which is unusual and is why it is worth
  doing now rather than later.
- **Two closed loops change what can route it.** Rigid rotation preserves the cyclic order of
  riders, so on one loop two ions can never meet (`Compiler/PLAN.md` §11.9a) — which is why
  pure rotation only serves a bipartite round. **Ejecting to storage and reinserting breaks
  that order**, so rotation *plus* eject/reinsert is strictly more powerful than either. That
  is what the arrows in the sketch are doing, and it is the reason this layout might compile
  the BB round where `grid9x9` cannot.

#### What building it needs

Mostly assembly of parts that exist:

| | |
|---|---|
| geometry | a new generator — `ladder(width, rungs, highways)` has rails, rungs and `eject`/`reinsert` on-ramps but its four horizontal lines are **all open** (`closed=false`, from `qccdc arch`). (a) says the two rail pairs must each **close into a loop**, so this is a variant of `ladder`, not a call to it. Signature: `rail_storage(width, loops=2, storage_capacity=2)` — (c) asks for the capacity to be a parameter, not a constant |
| storage zones | already expressible: `zone_types` carries per-type capacity and capability, and `load` is *already* capacity 8 with `gate: false` — a precedent for a large non-gating site. A `storage` type with capacity from the parameter and `gate: false` is a few lines of JSON |
| **rail capacity** | must be **smaller than storage**, or the two media collapse back into one and the design is just a grid. This is the parameter that makes or breaks the comparison; state it explicitly in the generator rather than inheriting a default |
| gate sites | **on the rails**, per (b) — so rail sites carry `gate: true` and storage columns do not. Note this is the mirror image of `ring144_24v`, where the loop is bare and the *docks* gate |
| movement classes | `eject` / `reinsert` already exist on the ladder and already declare `entails: [split, merge]`, so the cost model charges them automatically |
| the cost of depth | R14, above — already implemented and already checked |

The genuinely new work is in two places:

- **the placer** — which ions live in which column, when to pull one out, and **in what order
  the column is stacked**. R14 charges for burial depth, so keeping the next-needed ion at the
  edge is worth 3 CX per gate. CD2 · 2, and the same gap the capacity sweep exposed: *nothing
  in the compiler currently chooses to stack ions, let alone order the stack*.
- **the router**, and here is the risk to name up front. `conveyor.ml` detects *"a closed loop
  with gate-capable **docks** hanging off it by one spur"*. This device has closed loops whose
  **gate sites are on the loop itself**, which is the opposite arrangement — so
  `rotate_pipeline` may decline on it exactly as it declines on `cyclone_dual_loop`, whose
  gate sites also sit on its loops. If it does, this layout needs either the swap-based
  fallback router or an extension of the conveyor detector to handle eject/reinsert as the
  order-breaking move. **Check this before building the generator**: it is a ten-minute read of
  `conveyor.ml` and it decides whether C1 is a geometry task or a geometry-plus-router task.

#### How it gets judged

No differently from anything else, and this matters for the comparison to mean something:
the same `bb144_esm` round, `(p_eff, T_round)`, the 23 rules and **R10 by the proved checker**,
plus DAC count and DRC as constraints. And the **fairness protocol** below — same inner-loop
budget as the ring gets, recorded in the ledger. A space-efficient layout that wins because we
tuned it harder has not won.

### B2 — the comparison set at BB scale is **N = 1** · **BLOCKER**

Measured in [`findings/q01b`](findings/q01b-bb144-and-the-floor.md) §1. Of the nine shipped
devices, `bb144_esm` (168 qubits, 864 two-qubit gates) compiles on **`ring144_24v` alone**:

- `stationary_chain`, `h2_racetrack`, `chain` **cannot hold it** — 64, 80, 144 slots against
  168 qubits. A real result, not a compiler limit.
- the other five sit at 29–58 % occupancy, above the individual-ion router's measured ~46 %
  ceiling, and **rigid rotation is the only thing that compiles past it**. Rotation needs a
  *closed loop with gate-capable docks off it*, and only `ring144_24v` has that shape —
  `cyclone_dual_loop`'s two closed loops do not count, because its gate sites sit **on** the
  loops.

**An outer loop cannot compare one candidate.** This blocks CD5 as hard as G1 does, and it is
not fixed by any model change. Two ways out, and they are not equally good:

| | route | verdict |
|---|---|---|
| **preferred** | build the comparison set from **conveyor-shaped generators** — `ring(width, height, verticals)`, `dual_loop` with docks on spurs, `racetrack` sized to hold 168 — so every candidate is rotation-compilable by construction | this is what CD3 was always going to do; the nine shipped devices were never the search space |
| | extend the router past 46 % occupancy (the SAT encoding of `Compiler/PLAN.md` §6 as an optimiser) | large, and it changes what "the compiler" means mid-study — the fairness protocol below then compares devices under two different routers |

Whichever is taken, record it: a comparison run on devices *some of which needed a different
router* measures the router, not the machine.

---

## CD4 · Phase 0 — cheap exploration, before any optimiser

The user's instruction, and it is the right one. **Nothing below needs a new model term**,
so it runs while CD1 is being closed, and each item can falsify a plan assumption in an hour.

| # | question | how | what it would change | state |
|---|---|---|---|---|
| 0.1 | Does heating actually discriminate? | `error_budget` on the 9 shipped devices, same circuit | if not, geometry is irrelevant → report and stop | ✅ [q01](findings/q01-error-structure.md), **corrected by q01b** |
| 0.1b | the same, on `bb144_esm` | `run_matrix.py --circuits bb144_esm`, then re-measure the verified matrix | whether size or the model explains the 1.3× | ✅ [q01b](findings/q01b-bb144-and-the-floor.md) — **neither; it is the cooling pass** |
| 0.2 | Where is the cooling optimum? | `c5_pareto.py`, scoring each point by `p_eff` rather than by gate error alone | the interior optimum CD0 predicts — or its absence | ✅ [q01b §5](findings/q01b-bb144-and-the-floor.md) — **its absence.** Monotone; the optimum is R7's cap |
| 0.3 | How much does ancilla count matter? | `gen_bb144.py --ancillas 12,24,36,48,72` on `ring144_24v` + `grid9x9` | sizes the schedule axis | ⬜ — and note `grid9x9` cannot compile the round at any ancilla count above ~144 qubits (B2) |
| 0.4 | How much does CX order matter? | 3 hand-written orders, same device, compare data-ion idle | if it is 2%, deprioritise; if 2×, it leads | ⬜ *exempt from the optimiser, still worth measuring* |
| 0.5 | Grid vs ring vs ladder at fixed trap count | the `micro_demo.py` pattern at BB scale | first real intuition about geometry | ⬜ **blocked by B2** — at BB scale only the ring compiles |
| 0.6 | What does the router actually cost? | occupancy sweep per family (`c7_occupancy.py` generalised) | tells us which families the compiler can even serve | 🔶 partly answered by B2: 8 of 9 shipped devices cannot serve the BB round |

**Deliverable:** one file per question in [`findings/`](findings/), each naming the command
that produced it, with the raw output committed to `data/`. Written as things were learned,
including the ones that came out boring.

None of these needs the new model terms, so CD4 runs *while* G1 and G2 are being closed --
except any sweep over trap capacity, which must wait for G1.

> **What CD4 has cost the plan so far, and it was worth it.** 0.1 and 0.1b between them
> falsified the interior cooling optimum (0.2, before it was run), showed the objective is
> 96 % floor, and found that the BB round reaches one of nine devices. Three planned items
> below are now blocked or closed. That is what phase 0 is *for*: each of those would have
> been discovered by an optimiser as a confident wrong answer instead.

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

A number that reaches `findings/` must have passed the top tier: **an infeasible schedule
has no performance.** T1 may rank; only T2 may be quoted.

---

## CD6 · What would make this study worthless, and how to find out early

| risk | why it is fatal | when it is checked | state |
|---|---|---|---|
| `p_eff` is dominated by the gate floor `ε₀` | geometry cannot move it; the answer is "buy better gates" | CD4 · 0.1, day one | 🔴 **FIRED.** 96 % floor, median device share **3.9 %**. Not fatal *yet* — the device-attributable part still spreads 19–121×, so the signal exists and is diluted, not absent. G1 is the repair |
| `p_eff` is dominated by data-ion idle time | study collapses to "minimise round time" and the heating model stops mattering | CD4 · 0.2 | 🔶 **partly.** On the BB round idle is **100 %** of the device-attributable part (gate excess is exactly 0), so at BB scale the objective *is* `n_ions × T_round`. On smaller circuits idle is 2–5 % of it |
| ~~G1 not closed before searching capacity~~ | the optimiser reports R13's hard cap as an optimum | CD1 · G1, before any sweep over trap size | ✅ **closed** — R13's cap now costs 1.50–1.72×, and the one device that gates 15-ion chains fell from 4th of 9 to last |
| **nothing in the compiler ever chooses a long chain** | capacity is priced but never *used*, so the axis G1 unblocked is inert for an unrelated reason | the capacity sweep, immediately | 🔴 **CONFIRMED** — capacity 4 compiles byte-identically to capacity 2, every gate still at chain length 2 ([q03](findings/q03-capacity-is-inert.md)). Moves to CD2 · 2 |
| **capacity is not a monotone axis** | a sweep would report an artifact of router selection as an optimum | q03 | 🔴 **FIRED** — capacity 8 leaves 79 ops unrealised because the general router stops raising `Unroutable`, so rotation never fires. Fix the fallback before any sweep |
| **B2 — the comparison set at BB scale is N = 1** | an outer loop cannot compare one candidate | CD4 · 0.1b | 🔴 **FIRED.** 8 of 9 devices cannot compile `bb144_esm`. Build the set from conveyor-shaped generators (CD3 · B2) |
| every candidate sits far above 0.7 % | nothing being compared would work; the ranking is of losers | CD4 · 0.1 — compare to threshold immediately | ✅ no: 73 of 73 verified pairs sit **10–27× below** threshold. The study is about margin |
| the compiler cannot serve a family | comparison silently becomes "which device suits *our router*" | CD4 · 0.6 | 🔴 **FIRED** — it is B2. Only the conveyor family is served at BB scale |
| the model is not recalibrated after G1/G2 | every number downstream drifts from the validated oracle | on each model change — the 397,184 / 8,808 replay must still hold | ✅ oracle re-checked 2026-08-24, EXPECTATIONS MET |
| the search space is too small to contain a good answer | converges fast to something mediocre | after the first convergence: perturb hard, see if it comes back | ⬜ |

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

## The order, as it now stands

1. Read [`EVALUATION.md`](EVALUATION.md), then this file, then `docs/PLAN.md` §6 and §0.2
   (which already argue the objective), then `Compiler/PLAN.md` §0–§2 (the trust architecture).
2. ~~**CD4 · 0.1** — the falsification test.~~ **Done**, twice:
   [q01](findings/q01-error-structure.md) and [q01b](findings/q01b-bb144-and-the-floor.md).
   Heating *does* discriminate (19–121× on the device-attributable part) and nothing is above
   threshold — but the objective dilutes that signal to 3.9 % of itself, and the BB round
   compiles on one device.
3. ~~Close **G1**.~~ **Done** — [`findings/g1`](findings/g1-chain-length.md). Trap capacity
   is now safe to sweep; the oracle re-validated at 397,184 / 8,808.
4. ~~Sweep trap capacity.~~ **Checked first, and it is inert** —
   [`q03`](findings/q03-capacity-is-inert.md). Capacity 4 is byte-identical to capacity 2. The
   axis has no content until a placer stacks (CD2 · 2), and the rotation fallback must be fixed
   before any sweep or the result is an artifact of router selection.
5. Close **B2** — build a comparison set of conveyor-shaped candidates from the CD3
   generators, so the outer loop has more than one machine to compare.
6. Then G2 — wire `qccd/phys`'s solved ion height into the heating rate. Re-validate the
   oracle after each model change. Expect it to move the objective by percent, not orders.
7. Then CD4's remaining questions, then the loop.

Do not build the optimiser first. The optimiser is the easy part, and an optimiser pointed
at an objective nobody has validated will produce a confident, precise, wrong answer — which
is worse than no answer, because it looks like a result.
