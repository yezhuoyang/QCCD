# A swap-based router does not fix B2 — and the occupancy diagnosis was wrong

**Status:** attempted, **failed**, reverted. **Verdict:** a push/swap fallback router fixes
**0 of 14** failing (circuit, device) pairs. More importantly it falsified the reason it was
built: the BB round fails to compile at **26 % occupancy with 138 free slots**, so *free space
was never the constraint*. What actually blocks is now characterised precisely, and it is not
what either `q01b` or this attempt assumed.

The code is reverted. This file is the deliverable.

---

## 1 · Why it was built, and why that reasoning was wrong

`q01b` §1 attributed the five routable-in-principle failures to an occupancy ceiling: the
individual-ion router *"needs an empty destination and 58 % occupancy leaves none"*. The fix
seemed obvious and I argued it was **certain to work**: adjacent swaps generate the symmetric
group on a connected graph, so any permutation is reachable with no free space at all.

**That reasoning imports a theorem about the wrong problem.** Token swapping assumes one token
per vertex and a free exchange of adjacent tokens. This hardware has neither:

- **Traps hold 2 ions, not 1.** You cannot exchange through a full trap; you must first push an
  occupant out, which needs reachable free space — so the swap formulation does not remove the
  space requirement, it relocates it.
- **Gate operands are immovable.** An ion standing on a trap where a gate is scheduled is
  pinned there for the whole layer. No push can move it, and no swap can pass through it.

## 2 · The measurement that killed the hypothesis

`--ancillas` is a free knob in `gen_bb144.py`, and fewer ancillas means fewer qubits *and*
fewer simultaneous gates. Both push toward "easier", so if occupancy or layer saturation were
the constraint, a small enough round would compile.

| ancillas | qubits | occupancy on `ladder_2x72` (576 slots) | outcome |
|---:|---:|---:|---|
| 24 | 168 | 29 % | fails |
| 12 | 156 | 27 % | fails |
| **6** | **150** | **26 %** | **fails** |

At 6 ancillas a layer can hold at most 6 gates, so at most 12 ions are pinned across 144 traps.
It still fails. **Occupancy is not the constraint and neither is layer saturation.**

## 3 · What actually blocks, measured

Instrumenting the failure point to print the state of every candidate hop:

```
q11 boxed in at T0_0v (occ 2/2) en route to T0_0h
  hops: T0_0h[occ 2/2 frozen 1 closer=true]  T0_1h[occ 2/2 frozen 0]  T0_1v[occ 1/2 frozen 0]
```

Read that carefully, because it is the useful part of this whole exercise:

- The **goal trap is full but only half-pinned** — `frozen 1` of 2. There is a movable ion to
  evict.
- A neighbour, `T0_1v`, **has a free slot**.
- The device has **138 free slots** in total.

So the instance is not blocked. **A correct pusher gets in; mine did not.** The failure is an
implementation defect, not a property of the problem — which is exactly why "it doesn't work"
would have been the wrong thing to record without this line.

## 4 · Three real defects in the *existing* compiler, found on the way

These are independent of the reverted router and worth keeping:

1. **`compile.ml` never tells the router which operands must not move.** It builds `needed`
   (every operand and its meeting trap) and then passes only `targets` — those that need
   transport — to `Route.plan_layer`. An operand *already standing on* its meeting trap is
   invisible. Today's A\* planner is safe by accident, because it treats non-targets as static
   obstacles; **any future planner that can move a non-target will push an operand off its own
   gate site.** The certificate self-check catches it — `gate dag=4: q5 is at S36, but the
   witness says the gate is at S34` — which is the checker earning its keep.
2. **Excluding one trap from a search disconnects a 1-connected device.** `chain` is a path
   graph, so "search for free space, but not through here" can find nothing when the free space
   is all on the other side.
3. **Taking the first distance-closing neighbour is not enough.** On a ring or a chain exactly
   one neighbour closes the distance, so a single blocked trap is the entire frontier.

## 5 · Why the attempt was abandoned rather than iterated

Five iterations, each fixing a real defect and revealing the next:

| # | defect | symptom after fixing it |
|---|---|---|
| 1 | operands already at their meeting trap not held | certificate self-check failure |
| 2 | push search disconnected by its own exclusion | `no free slot reachable` |
| 3 | only the first distance-closing hop tried | `boxed in (1 candidate hop)` |
| 4 | pusher could move the ion it was routing | `boxed in (2 candidate hops)` |
| 5 | visited-set barred a neighbour that had room | **oscillation** — `made no progress` |

Trading `boxed in` for `made no progress` is the signature of a greedy walker being asked to do
a job that needs a real algorithm. **Push-and-swap / push-and-rotate are published, complete
methods with proper cycle handling**; hand-rolling one incrementally converges on rediscovering
them badly. If this is attempted again, start from the published algorithm, not from a walker.

## 6 · What this means for B2

**B2 is not closed and this route did not close it.** But the target has moved:

- The next attempt should be judged against a **specific, measured blocking state** (§3), not
  against "58 % occupancy".
- Whatever is built must handle **pinned gate operands**, which is the constraint the published
  MAPF algorithms do *not* model — they assume every token is movable. That is the genuinely
  novel part here and it should be designed for deliberately rather than discovered.
- The cheapest alternative remains **C1**, the collaborator's rail-and-storage layout: it
  attacks the same blocker from the architecture side by keeping the transport medium empty, and
  needs no MAPF solver at all.

## 7 · Guard rails after the revert

| | |
|---|---|
| `deck24` oracle | **397,184 / 8,808 — EXPECTATIONS MET** |
| spot regressions (`ghz16`/grid9x9, `qft8`/ladder_2x72, `rep9_esm`/ring144_24v) | all compile |
| working tree | `route.ml` and `compile.ml` reverted to `d9ad586` |

## 8 · Caveats

- **14 pairs tested, not the full matrix.** The 10 non-BB failures from
  `matrix_after_fallback.json` plus `bb144_esm` on grid9x9, ladder_2x72, h2_racetrack and
  cyclone_dual_loop. A full matrix run was started twice and killed both times by session
  teardown; the 14 are the pairs that could have improved, so a full run would have added
  confirmation, not information.
- **The 6-ancilla circuits were not rule-checked or R10-verified.** Nothing is claimed about
  their performance — only that they fail to compile, which needs no verification.
- The `--ancillas 6/12` variants live in the session scratchpad; `gen_bb144.py` regenerates them.
