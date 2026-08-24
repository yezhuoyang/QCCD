# 0.1 · Does anything the architecture controls actually move the score?

> ### ⚠ Superseded in part by [`q01b`](q01b-bb144-and-the-floor.md)
>
> Three defects in this file's method were found when `q01b` re-ran it. Read that file's §2
> before quoting any number below.
>
> - **The 48 % headline is wrong. It is 3.9 %.** The floor here is `n_pairs · ε₀` alone, so
>   the whole SPAM term was charged to the architecture. All nine devices declare identical
>   `measure.fidelity` and `reset.error`, so SPAM is floor.
> - **These 39 programs are not the verification matrix.** `Compiler/build/out/` is scratch
>   output; the matrix is `Compiler/build/matrix/`, with verdicts in `matrix.json`. Nothing
>   below is known to have passed the 23 rules or R10, which `PLAN.md` CD5 requires of a
>   number in this directory. `q01b` re-measures 73 pairs that did.
> - **`quanta_per_data_ion` in `q01.json` is identically zero in all 39 rows.**
>   `t2_metrics(data_ion_prefix="d")` finds no data ions in programs whose ions are named
>   `q0…q167`.
>
> What survives: the *shape* of the answer (geometry is not irrelevant), the observation that
> wiring is invisible to the objective, and that everything sits far below threshold. What
> does not: the 48 %, and the two candidate explanations for the 1.3 × — `q01b` falsifies
> both and identifies the cooling pass instead.

**Status:** answered, preliminary — small circuits only, `BB [[144,12,12]]` not yet included.
**Verdict:** the study is **not** void, but the device spread under the current model is
**1.3×**, not the three orders of magnitude the literature reports. That gap is the finding.

```bash
python Codesign/scripts/q01_error_structure.py --json Codesign/data/q01.json
```

39 already-compiled programs from `Compiler/build/out/` (nine circuits × nine architectures,
whatever the matrix could route). No new compilation; replay only.

## What was measured

`−ln F` decomposed into `gate + idle + spam`, and separately into `floor + excess`, where the
floor is what the gate set costs at `n̄ = 0` — i.e. on a machine with no transport at all.
**Excess is the part the architecture is responsible for.**

| | |
|---|---|
| device-attributable share of `−ln F` | **10 % … 70 %, median 48 %** |
| same circuit, best vs worst device | **1.12× … 1.38×** |
| `p_eff` across all 39 programs | 0.00027 … 0.00074 (**2.75× spread**) |
| programs above the 0.7 % threshold | **0 of 39** |

## Three things this says

**1. Geometry is not irrelevant — the falsification test does not fire.** Roughly half the
error budget is transport-attributable at the median. The plan may proceed.

**2. But the device barely separates.** Best-to-worst is 1.3× on the same circuit, against
the **three orders of magnitude** Murali *et al.* report for trap sizing and topology. Two
candidate explanations, and they are distinguishable:

- **G1, the missing chain-length term.** Their spread comes substantially from `A ∝ N/ln N`
  and the capacity optimum it creates. This model has no such term, so one of the two axes
  they found most powerful is invisible here. *This is now the strongest argument for closing
  G1 before anything else.*
- **These circuits are too small.** `ghz8`, `qft6`, `steane_esm` on 144-trap devices barely
  move ions. The BB round is 864 two-qubit gates across 168 qubits and should stress
  transport far harder. **Re-run this on `bb144_esm` before drawing conclusions.**

> Both explanations are **wrong**, and [`q01b`](q01b-bb144-and-the-floor.md) §4 says what is
> actually happening: the cooling pass converts heating into runtime, runtime is nearly free
> at `T_coh = 600 s`, and R7's `max_quanta = 1.0` caps the whole gate term at 2.09× between
> any two *feasible* schedules. The BB round erases **more** of its heating than these small
> circuits do, not less — its mean gate n̄ after cooling is exactly 0. G1 is still the right
> next repair, but for a different reason than the one given here.

**3. Everything is far below threshold, so the question is margin, not feasibility.**
`p_eff ≈ 3–7 × 10⁻⁴` against a 0.7 % threshold — a factor of 10–25 of headroom. No design
compared here fails because the code cannot help it. That is good news for the study and it
also means `p_eff`-versus-threshold is not a discriminator; the *excess* is.

## A structural observation worth acting on

`grid9x9` and `deck_unit_cell` return **identical** numbers on every shared circuit
(`ghz32`: 0.1092 both; `ghz8`: 0.0257 both). They are the same lattice with different wiring
— 5,760 DACs against 44.

**The error model is blind to wiring.** That is correct as physics under this model (wiring
sets which waveforms are *possible*, not how hot an ion gets), but it means the wiring axis of
`PLAN.md` CD3 cannot be optimised against `−ln F`. Wiring enters only as a **feasibility
constraint** (R4 drivability) and a **cost** (DAC count). Do not put it in the objective.

## What to do next

1. ~~Re-run on `bb144_esm` across every device that can route it.~~ **Done** —
   [`q01b`](q01b-bb144-and-the-floor.md). Only `ring144_24v` compiles it; the spread is
   unmeasurable at BB scale, and the small-circuit spread does not grow with size.
2. Close **G1**, recalibrate against the 397,184 / 8,808 oracle, and re-run this script. The
   change in spread *is* the measurement of how much G1 mattered. **Still the next action**,
   and `q01b` §6 gives it a stronger justification: it is the only proposed term that
   survives cooling to n̄ = 0.
3. ~~Move wiring from the objective to the constraint set in `PLAN.md` CD3.~~ **Done.**

## Caveats

- `p_eff`'s denominator counts operations (2q gates, 1q gates, measures, resets) and **not**
  idle locations. A threshold comparison under a different convention would shift it.
- The cooling pass runs before this measurement, so heating has already been converted into
  *time* wherever the budget allowed. The raw heating signal is therefore partly hidden in
  runtime; question 0.2 (the cooling knob) is where it reappears.
- Nine architectures, but only some pairs routed — the matrix reports 72 of 81. Absent pairs
  are not failures of the device.
