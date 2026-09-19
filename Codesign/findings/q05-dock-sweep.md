# 0.5 · The first multi-point comparison at BB scale — and the architecture is worth 0.7 %

**Status:** answered. **Verdict:** the BB round now compiles on **eleven** points instead of
one, every one of them feasible — 20 rules and **R10 passed by the proved Lean checker**. But
the naive sweep's headline (2.89× runtime, 1.16× `p_eff`) is mostly *not* the architecture.
Separating the two knobs, **the dock geometry alone is worth 1.139× on round time and
1.0070× — seven tenths of one percent — on `p_eff`.**

```bash
python Codesign/scripts/q05_ring_dock_sweep.py --json Codesign/data/q05.json
```

Eleven `(device, program)` points: seven where dock count and ancilla count move together,
four where the geometry moves alone. Each is `ring144_24v` with **one parameter changed**, so
the primitives, wiring, `T_coh` and heating rate are the shipped ones and the `verticals=24`
row *is* the shipped baseline — it reproduces [`q01b`](q01b-bb144-and-the-floor.md)'s 522.32 ms
and 5.27e-4 exactly.

---

## 1 · B2 is downgraded, not closed

B2 was *"of nine shipped devices only `ring144_24v` compiles the BB round"*. The way out was in
`PLAN.md` CD3 all along and I had been reading the blocker too literally: **the nine shipped
devices were never the search space.** `conveyor.ml` serves any *"closed loop with gate-capable
docks off it"*, and every device the `ring` generator emits has exactly that shape. So the dock
count is a sweepable architecture axis with real candidates at both ends, reachable today with
no compiler work.

**State it no stronger than this: N = 1 *device* becomes N = 1 *family*, 11 points.** The
general A\* router declined at every single point and rigid rotation carried all of them.
Nothing changed for the eight devices that could not compile the round. The study still has no
second *shape*, and `PLAN.md` CD6's failure mode — *"the comparison silently becomes 'which
device suits our router'"* — is precisely what a dock sweep on the one conveyor family is.
Declare that wherever these numbers appear.

## 2 · The confounded sweep, and why its headline is mostly SPAM

| docks = ancillas | T_round | `p_eff` | −ln F | floor | device | dev % | resets | batches |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | 729.52 ms | 5.4665e-4 | 2.66984 | 2.4802 | 0.18968 | 7.1 % | 132 | 706 |
| 16 | 674.68 ms | 5.4100e-4 | 2.64007 | 2.4602 | 0.17991 | 6.8 % | 128 | 674 |
| 18 | 658.92 ms | 5.3876e-4 | 2.62807 | 2.4502 | 0.17791 | 6.8 % | 126 | 686 |
| **24** (shipped) | 522.32 ms | 5.2677e-4 | 2.56641 | 2.4202 | 0.14625 | 5.7 % | 120 | 546 |
| 36 | 367.17 ms | 5.0829e-4 | 2.47031 | 2.3602 | 0.11015 | 4.5 % | 108 | 381 |
| 48 | 339.80 ms | 4.9688e-4 | 2.40890 | 2.3002 | 0.10874 | 4.5 % | 96 | 350 |
| 72 | 252.14 ms | 4.7076e-4 | 2.27093 | 2.1802 | 0.09077 | 4.0 % | 72 | 262 |

Monotone, no interior optimum — and **the shipped V = 24 baseline is dominated inside its own
family** on both axes. That much is real.

**But `verticals` sets the dock count and the ancilla count with one number.**
[`gen_bb144.py`](../../Compiler/bridge/gen_bb144.py) emits a reset only for checks beyond the
first `n_anc`, so **resets = 144 − V**, and reset error (5e-3) is 3× measure error (1.6e-3).
Decomposing the −ln F improvement from V = 12 to V = 72: **Δ = 0.3989, of which SPAM is 0.3000
(75.2 %), idle is 0.0989 (24.8 %), and gate is exactly 0.** Three quarters of the fidelity
headline is *"we did 60 fewer resets"*, which would be true on any machine.

Worse, that saving is an artifact of scoring one round in isolation: in steady-state syndrome
extraction every ancilla arrives carrying the previous round's outcome, so all 144 checks need
a reset regardless of V.

## 3 · The geometry axis, ancillas held at 24

Same circuit at every point, so SPAM (0.83040), operation count (4872) and reset count (120)
are **identical** and every difference is the machine:

| docks | ancillas | T_round | `p_eff` | −ln F | device | dev % | batches |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 24 | 24 | 522.32 ms | 5.2677e-4 | 2.56641 | 0.14625 | 5.7 % | 546 |
| 36 | 24 | 477.76 ms | 5.2421e-4 | 2.55393 | 0.13377 | 5.2 % | 488 |
| 48 | 24 | **503.44 ms** | 5.2568e-4 | 2.56112 | 0.14096 | 5.5 % | 522 |
| **72** | 24 | **458.68 ms** | **5.2311e-4** | 2.54859 | 0.12843 | 5.0 % | 464 |

```
T_round : 522.32 ms -> 458.68 ms  =  1.139x
p_eff   : 5.2677e-4 -> 5.2311e-4  =  1.0070x     <- the architecture, alone
```

`v72_a24` is fully verified: 20 rules pass, and **`R10 passed: O1 by the proved Lean checker
(QCCDC.Cert.check_sound); O2 by stabilizer tableau`**.

**So tripling the docks buys 14 % of round time and 0.7 % of score.** Set against the
confounded sweep's 2.89× / 1.16×, the split is roughly: **ancilla count 1.82×, geometry 1.14×**
on runtime — the schedule axis does about five times more work than the architecture axis.

**And the geometry curve is not monotone.** 48 docks is *worse* than 36 (503.44 vs 477.76 ms).
Do not fit a derivative to four lumpy points; the rigid-rotation planner's batching is doing as
much here as the geometry is.

## 4 · Why the trade never fired

The sweep was built to test a real trade — more docks means fewer rotations, but each dock spur
makes its rail node degree-3, i.e. **a junction on every rigid hop** (`docs/PLAN.md` §0.5 calls
verticals *"the real junction cost"*). Both halves are true. Neither costs what was expected:

- **The junction penalty saturates at one dock.** Every `rotate_cw` hop is charged exactly
  **100.0 µs at every dock count** — the degree-3 `junction_cross` rate, against 5 µs for a
  plain shuttle. A rigid hop moves all 144 riders at once, so as soon as *one* spur exists some
  rider crosses a junction and the whole SIMD batch pays the junction class. Adding docks does
  not make a hop dearer; it only reduces how many hops you need. **The cost was fully paid at
  the smallest V in the sweep.**
- **The extra transits are heating, and heating is free here.** Junction transits follow
  `transits = V × rotate_hops + 864` exactly at all seven points, and because `V × hops` is
  nearly invariant the total is nearly flat. It never reaches a gate: `insert_cooling` puts one
  global 300 µs cool before every batch and `cool` removes *all* quanta, so the cooling bill
  scales with **batch count**, not with how much heat there is (`cool_us / 300 = batches + 1`
  at all seven points).

**`gate_error_sum` is 1.58976 at all eleven points**, equal to `864 × 1.84e-3` to 3.6e-14,
because `mean_gate_quanta = 0.0` and `chain_len_at_gate = {2: 864}` everywhere.
[`q01b`](q01b-bb144-and-the-floor.md)'s central result now has eleven points instead of one and
holds at every one. R7's 2.09× cap on the gate term is not merely un-approached — **it is never
exercised at all**, because every feasible schedule sits on the floor.

The whole clock is `T = 725 µs × batches + 100 µs × rotate_hops + 300 µs + gate + measure +
reset` (725 = dock 165 + undock 260 + cool 300), with **zero residual at all seven confounded
points**. The batch term is 70–76 % of `T_round`; the rotation term — the only one the dock
geometry touches — is 11–22 % and shrinking as V grows. **The sweep is a batch-count sweep
wearing a geometry costume.**

## 5 · The one axis where more docks is clearly worse, and it was not in the score

`qccd.phys.drc.check` on the derived electrodes (this reproduces `tests/test_drc.py`'s asserted
66 for the tracked `ring144_24v` exactly):

| docks | 12 | 16 | 18 | **24** | 36 | 48 | 72 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `rf_dc_clearance` violations | 30 | 42 | 48 | **66** | 102 | 138 | **140** |
| DACs | 45 | 45 | 45 | 46 | 47 | 48 | 50 |
| junctions | 12 | 16 | 18 | 24 | 36 | 48 | 72 |

**Every one of these devices fails DRC, and the count rises monotonically with the parameter
the score says to increase** — 30 → 140, a 4.7× penalty on the dock count that wins both scored
axes. Neither declared budget (`max_dacs` 128, `max_junctions` 200) ever binds, so the only
buildability constraint that *does* bite is the one not being optimised. `PLAN.md` CD3 says DRC
belongs in the outer loop as a constraint; this is the first time score and buildability have
pointed in opposite directions, and it is the more interesting half of the result.

## 6 · Caveats

- **One family, one router.** Eleven points of `ring(width=72, height=2, verticals=V)`. The A\*
  router declined at every point; rigid rotation carried all of them.
- **"20/20" would be loose.** It is 20 passed of 23, with R15 additionally *partial* (quanta
  composed additively — an upper bound, so conservative), R7b and R9 skipped structurally, and
  R10 discharged separately by the Lean checker.
- **`p_eff`'s denominator counts operations, not idle locations** — 4872 at every geometry-axis
  point. All eleven sit 12.8–14.9× below the 0.7 % threshold.
- **The steady-state recomputation in §2 is arithmetic on measured terms, not a compiled
  program**, and is not quotable as a result. Under it the confounded sweep's `p_eff` spread
  falls from 1.16× to **1.0376×**.
- **Dock utilisation falls as docks are added** — contacts per batch divided by V drops from
  10.2 % to 4.6 %. At 72 docks the planner places 3.30 contacts per batch across 72 available
  docks. It is not using what it is given, so part of this sweep measures the planner.
