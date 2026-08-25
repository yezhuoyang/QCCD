# Progress

**The state of the study, in one file.** A session starts by reading this and ends by
updating it. If this file and the code disagree, the code is right and this file is a bug.

- **What is being asked** — [`PLAN.md`](PLAN.md)
- **How a design is scored** — [`EVALUATION.md`](EVALUATION.md)
- **What has been learned** — [`findings/`](findings/), one file per question
- **How to start a session** — [`SESSION_PROMPT.md`](SESSION_PROMPT.md)

---

## Next action

> **Make the rotation fallback fire on a partial placement, not only on an exception.**
> [`findings/q03`](findings/q03-capacity-is-inert.md) §2: at trap capacity 8 the general router
> stops raising `Route.Unroutable` and instead returns a partial placement with **79 ops
> unrealised** — so `qccdc_cli.ml` never tries rigid rotation, and a device with *more* room
> compiles *worse* than the shipped one.
>
> A compile that leaves ops unrealised has declined just as surely as one that raises. This is
> a few lines in [`qccdc_cli.ml:360`](../Compiler/ocaml/bin/qccdc_cli.ml#L360), it can only add
> programs that compile, and it removes a non-monotonicity that would otherwise make any
> capacity sweep report an artifact of control flow as an optimum.
>
> After that, the honest ordering is **B2 before more model work**: the study still has one
> candidate at BB scale. Either the swap-based fallback router (certain to work, no checker
> changes needed — verified) or C1's rail-and-storage layout (architectural, and the
> collaborator is waiting on it).

---

## Status

| | item | state | where |
|---|---|---|---|
| **CD4 · 0.1** | does the architecture move the score? | ✅ answered, then **corrected** — the 48 % was SPAM miscounted; it is **3.9 %** | [findings/q01](findings/q01-error-structure.md) |
| **CD4 · 0.1b** | the same, on `bb144_esm` | ✅ **answered** — the BB round compiles on **1 of 9** devices; the spread is not size and not G1, it is the **cooling pass** | [findings/q01b](findings/q01b-bb144-and-the-floor.md) |
| **CD4 · 0.2** | where is the cooling optimum? | ✅ **answered — there isn't one.** Monotone; the minimum is R7's cap, where the shipped policy already sits | [q01b §5](findings/q01b-bb144-and-the-floor.md) |
| CD4 · 0.3 | how much does ancilla count matter? | ⬜ — scoped down by B2: above ~144 qubits only the ring can compile the round | — |
| CD4 · 0.4 | how much does CX order matter? | ⬜ *exempt from the optimiser, still worth measuring* | — |
| CD4 · 0.5 | grid vs ring vs ladder at fixed trap count | 🔴 **blocked by B2** — at BB scale only the ring compiles | — |
| CD4 · 0.6 | what does the router actually cost? | 🔶 partly answered — 8 of 9 devices cannot serve the BB round, for two separate reasons | [q01b §1](findings/q01b-bb144-and-the-floor.md) |
| **G1** | chain-length term in the gate error | ✅ **CLOSED** — parameter-free calibration, bit-identical at N=2, R13's cap now costs 1.50–1.72×; `stationary_chain` fell from 4th of 9 to last. **Capacity is safe to sweep** | [findings/g1](findings/g1-chain-length.md) |
| **CD3 · capacity** | does the compiler ever *fill* a trap? | ✅ **answered — no.** Capacity 4 gives a byte-identical program; every gate still at chain length 2. The axis G1 guards is unreachable until a placer stacks. Capacity 8 is *worse* (79 unrealised) via router selection, not physics | [findings/q03](findings/q03-capacity-is-inert.md) |
| **C1** | space-efficient rail-and-storage layout (collaborator's sketch) | ⬜ **queued** — **two media**: small-capacity transport rails, larger-capacity `storage` sites holding *idle* ions off the transport path. The first serious second candidate, and an architectural answer to B2: rail occupancy stops scaling with qubit count | [PLAN CD3 · C1](PLAN.md) |
| **B2** | the comparison set at BB scale is **N = 1** | 🔴 **BLOCKER** — an outer loop cannot compare one candidate. A capacity sweep on the ring is the cheapest partial answer: every point is conveyor-shaped | [PLAN CD3 · B2](PLAN.md) |
| G2 | anomalous heating from the solved ion height | ⬜ — worth doing, but it reaches the objective only through runtime → idle (5.7 %) | [EVALUATION §4](EVALUATION.md) |
| G3 | idle error linear vs Gaussian | ⬜ — and it does **not** set the cooling optimum, as was claimed | [EVALUATION §4](EVALUATION.md) |
| CD5 | the loop | ⬜ not started, and must not start before **G1 and B2** | [PLAN CD5](PLAN.md) |

## Decisions taken

| | decision | when |
|---|---|---|
| **no decoder** | designs are scored by `(p_eff, T_round)` from `t2_metrics`; logical error only ever as a labelled extrapolation from Bravyi's published curve | at planning |
| **wiring is a constraint, not an objective** | `grid9x9` and `deck_unit_cell` score *identically* — the error model is blind to wiring. It enters as R4 feasibility and DAC count | [q01](findings/q01-error-structure.md) |
| **CX order is exempt from the optimiser** | a metric counting expected faults cannot see that a bad order turns one ancilla fault into a weight-2 data error | [EVALUATION §5](EVALUATION.md) |
| **the cooling budget is not a search axis** | re-scoring `c5_pareto`'s measured frontier under the full objective is monotone; the optimum is R7's cap, where the shipped policy sits. `T_coh = 600 s` makes runtime nearly free | [q01b §5](findings/q01b-bb144-and-the-floor.md) |
| **report the device-attributable part beside `−ln F`** | the objective is 96 % floor, so a ranking on `−ln F` alone is dominated by a constant. The device part spreads 19–121× where `−ln F` spreads 1.04–1.30× | [q01b §3](findings/q01b-bb144-and-the-floor.md) |
| **D1 needs no answer** | `κ` and `ε₀'` are *derived*, not fitted: equality with the pre-G1 model at the reference chain length, for every `n̄`, pins both. The only assumption left is Murali's *shape* | [g1](findings/g1-chain-length.md) |
| **`Γτ` is already modelled** | R17 accrues anomalous heating over elapsed time including the gate's own duration, so adding the published `Γ·τ` term explicitly would double count | [g1 §8](findings/g1-chain-length.md) |
| **only verified programs may be measured** | `q01` measured 39 programs from a scratch directory with no rule or R10 verdict. Measure `Compiler/build/matrix/`, gated on `matrix.json` status `ok` | [q01b §2](findings/q01b-bb144-and-the-floor.md) |

## Open questions raised by the work itself

- ~~Does the 1.3× device spread grow with circuit size, or is it the model?~~ **Answered:
  neither.** Flat over 1→68 two-qubit gates, and at 864 gates the heating is erased entirely.
- ~~How much of the architecture signal is hidden in the cooling pass?~~ **Answered: at BB
  scale, all of it.** Mean gate n̄ after cooling is exactly 0; the gate term sits exactly on
  its floor.
- `p_eff` sits 10–27× below threshold everywhere, on all 73 verified pairs. Is any *reachable*
  design near it? If not, the study is about margin, and the title should say so.
- ~~How much of the 19–121× device-part spread survives G1?~~ **Answered: none of it moves.**
  G1 changed 2 of 73 programs, both on `stationary_chain`. Seven of nine devices are
  capacity-2, so the term is a constant on them.
- ~~Will a placer ever choose a long chain if given the capacity?~~ **Answered: no.** Capacity
  4 compiles byte-identically to capacity 2. The question moves to CD2 · 2 (mapping).
- **Does keeping the transport rails empty route where a uniformly-loaded device does not?**
  C1's central claim, and the reason it may answer B2 without any compiler work.
- **Storage buys routability and area, but not fidelity — is that a defect of the design or of
  the metric?** `idle_error` is `n_ions × T / T_coh` with no reference to *where* an ion sits,
  and anomalous heating accrues per ion per µs wherever it is. So the model is blind to the
  difference between an ion parked in storage and one riding a rail. Real hardware may not be.
- **How deep is storage worth making?** R14 charges 3 CX *per position of burial*, ~3× what the
  gate it enables costs — so depth is expensive unless the placer keeps the next-needed ion at
  the chain edge. Ordering the stack by next-use is the cheapest big win C1 offers.
- **Does separating storage from gating dodge G1?** If gates happen on the rails at chain
  length 2 while storage columns are deep, a design gets density without the `N/ln N` penalty.
  C1 question (b) decides whether that is what the sketch means.
- **Is the R7 budget of 1.0 quantum itself the right number?** It caps geometry's influence on
  the gate term at 2.09×, which is most of what this study is trying to measure. It is a
  policy constant from `docs/PLAN.md` §0.4 ("PLAN §0.4 puts it at 1–2 quanta, 1.0 is the
  strict end"), not a measurement. Sweeping it is not a design choice — it changes what
  counts as feasible — but the study should say what it assumed.
- **Does the shipped `[[144,12,12]]` round need 168 qubits?** `--ancillas` is a free knob
  (CD2·1). Fewer ancillas is a smaller circuit and might reach more devices — at the cost of a
  longer round. That is the cheapest partial answer to B2 available.

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
| 2026-08-24 | `q03`. Swept `ring144_24v` trap capacity 2 -> 4 -> 8 on the BB round. **Capacity 4 is byte-identical to capacity 2** — same batches, hops, runtime, and `{2: 864}` chain lengths — so the compiler never fills a trap and the axis G1 guards is unreachable. Capacity 8 is *worse* (79 ops unrealised) because the general router stops raising `Unroutable`, so the rotation fallback never fires: the latent gap found in `q01b` §1, firing for real. **Capacity is not a monotone axis under the current compiler**, and a sweep run today would report `qccdc_cli.ml`'s control flow as an optimum. |
| 2026-08-24 | **G1 closed.** `error_vs_chain: "murali:2"` on all nine architectures; the replay reads the chain length the way R13 does. The calibration turned out **parameter-free** — equality with the old model at `N_ref` for every `n̄` pins `κ` and `ε₀'`, so D1's "largest single assumption" reduces to borrowing Murali's shape. Bit-identical at `N_ref`; 71 of 73 verified programs unmoved; oracle still 397,184 / 8,808; `bb144_esm` recompiled and R10 still `passed`. The raw `N/lnN` has a minimum at `N = e` and so discounts a 3-ion chain by 5.4 % — refused with a monotone envelope rather than handed to a search. **Result: `stationary_chain` fell from 4th of 9 to last** on both circuits it runs. But seven of nine devices are capacity-2, so G1 adds no signal to today's comparison, which corrects q01b §6. New question raised: nothing in the compiler ever *chooses* a long chain. |
| 2026-08-24 | `q01b`. Generated `bb144_esm.qasm` and ran the matrix on it: **1 of 9 devices compiles the BB round** (three cannot hold 168 qubits; rigid rotation is the only router past ~46 % occupancy and only `ring144_24v` is conveyor-shaped) → new blocker **B2**. Re-measured 73 *verified* pairs and found `q01`'s 48 % was SPAM charged to the architecture — it is **3.9 %**. Diagnosed the 1.3 ×: not size, not G1, but **the cooling pass**, which drives the mean gate n̄ to exactly 0 and pins the gate term to its floor, while `T_coh = 600 s` makes the runtime it costs nearly free. Falsified the interior cooling optimum (**0.2 answered by its absence**) and the claim that G3 sets it. G1 survives as the next action, on a better argument: its terms are the only ones non-zero at n̄ = 0. Oracle re-checked: 397,184 / 8,808, EXPECTATIONS MET. |
