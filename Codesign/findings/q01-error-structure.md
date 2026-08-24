# 0.1 · Does anything the architecture controls actually move the score?

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

1. Re-run on `bb144_esm` across every device that can route it. If the spread stays ~1.3×,
   the model — not the circuit size — is the reason, and G1 becomes mandatory.
2. Close **G1**, recalibrate against the 397,184 / 8,808 oracle, and re-run this script. The
   change in spread *is* the measurement of how much G1 mattered.
3. Move wiring from the objective to the constraint set in `PLAN.md` CD3.

## Caveats

- `p_eff`'s denominator counts operations (2q gates, 1q gates, measures, resets) and **not**
  idle locations. A threshold comparison under a different convention would shift it.
- The cooling pass runs before this measurement, so heating has already been converted into
  *time* wherever the budget allowed. The raw heating signal is therefore partly hidden in
  runtime; question 0.2 (the cooling knob) is where it reappears.
- Nine architectures, but only some pairs routed — the matrix reports 72 of 81. Absent pairs
  are not failures of the device.
