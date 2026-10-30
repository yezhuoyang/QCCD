# C2LR swap-or-stay transport

Status: design, 2026-10-30. Decisions taken by the project owner on 2026-10-30: what the paper
did not demonstrate is allowed and marked assumed (the proposal's two-pair "P1/P2" steps, below);
C2LR designs are ranked on a separate track; the certificate writes swaps as plain moves (no
change to the proved checker); every physical number is checked against the paper before it
enters a physics file. The check (2026-10-30, arXiv v2 = Phys. Rev. X 14, 041028) corrected the
proposal in five places; section 6 has them.

Source: R. D. Delaney et al. (Quantinuum), *Scalable multispecies ion transport in a grid-based
surface-electrode trap*, arXiv:2403.00756 (2024). Proposal: "support C2LR swap-or-stay transport
in QCCD Studio" (c2lr-router; BB [[144,12,12]] round in 160.0 ms on a 10x10 grid, Ba-Sr).

## 1. The device

A C2LR device is a lattice of X-junctions whose every wire carries one **leg**: a trap site at
the wire's centre (the `grid(a, b)` generator already draws exactly this: a site `T...h`/`T...v`
on every wire between junction nodes `J{r}_{c}`). Each leg holds one two-ion crystal (a qubit
ion and its coolant, one *ion* in this model, as R2 says) that moves as a unit.

The device says it is C2LR in its control block:

```json
"control": {"model": "c2lr",
            "c2lr": {"diagonals": ["WS", "WN", "ES", "EN"], "checkerboard": ["P1", "P2"],
                     "assumed": ["checkerboard"],
                     "shared_signals": 27, "inputs_per_leg": 1, "switches_per_leg": 1},
            "classes": {"extra": [{"id": "swap_or_stay", "type": "c2lr"},
                                  {"id": "reorder_or_not", "type": "c2lr"}]}}
```

(Phase 1: a class is an object with an `id`, as every other device's are --
`Architecture.simd_classes` reads `extra[k]["id"]`, so bare names would not load. The three
counts are what C10 reads; `Machine.c2lr_grid` writes them from p4.)

and prices its steps as primitives (numbers per section 6, each with its source):

```json
"swap_or_stay":  {"us": ..., "quanta_swap": ..., "quanta_stay": ..., "source": "..."},
"reorder_or_not": {"us": ..., "quanta": ..., "source": "..."}
```

## 2. The step

**Variation (demonstrated).** The global waveforms are broadcast to every cell, so every
junction runs the same variation: one diagonal pair of its legs, W-S, W-N, E-S or E-N (read off
the positions). The paper demonstrates all four ("four swap or stay variations, each across a
different diagonal", Sec. IV; Fig. 4(f)). Under one diagonal every leg is in exactly one pair:
with W-S, a horizontal leg pairs at its east junction (where it is that junction's W leg) and a
vertical leg at its north junction (where it is the S leg). A leg whose partner does not exist
(the device's edge) is unpaired in that step. Every step is therefore a matching of legs.

**Checkerboard (assumed, not in the paper).** The proposal's step pairs *two* diagonals at one
junction (P1: W-S and N-E; P2: W-N and S-E) at the junctions of one checkerboard parity
`(r + c) mod 2`, which is also a matching of every leg. Two pairs crossing one junction at once
was neither demonstrated nor discussed. It is allowed, marked assumed in every report, and the
rule set `c2lr-demonstrated` refuses it.

**Direction bits.** Every crystal shifts toward its pair's junction or away from it (C2LR, the
per-leg switch). A step lists the crystals shifted **toward**; every other crystal shifts away.
Then the shared junction waveform exchanges, at every junction in play, the two wells nearest it
(one in each paired leg), and C2LR⁻¹ brings every crystal back to a leg centre.

So for each pair of legs (A, B) the crystal toward the junction in A goes to B, and the one in
B goes to A. That one rule covers every case:

| A | B | result |
|---|---|---|
| toward | toward | **swap** |
| away | away | **stay** (both) |
| toward | away, B holds a crystal | **enter**: A's crystal joins B's (B holds two, C1) |
| toward | B empty | **move**: A's crystal to B |
| -- | B holds two, the entered one toward | **exit**: it returns to A |

In the TSIR a step is one `simd` instruction with a template, as a rotation is today:

```json
{"type": "simd", "class": "swap_or_stay", "mode": "inter",
 "template": {"kind": "swap_or_stay", "diagonal": "WS", "toward": ["q3", "q17"]}}
```

and the assumed form names `"checkerboard": {"parity": 0, "pairing": "P1"}` instead of
`"diagonal"`.

The replay expands it from the **device**, never from the programme: the junctions in play, the
pairs and each mover's route (leg -> junction -> partner leg) are computed from the geometry and
the variation, and the programme supplies only the bits. That is what lets the rules below exempt a
swap without a programme being able to claim the exemption for anything else.

**reorder-or-not** is `{"kind": "reorder_or_not", "reverse": [ions]}`: a 180-degree rotation of
the listed crystals' wells; positions are unchanged.

## 3. Time, heat, gates

A swap-or-stay step takes the primitive's `us`, however many crystals move (one waveform).
Every crystal plays every waveform (C9): a crystal that moved gains `quanta_swap`, every other
crystal on the device `quanta_stay`; anomalous heating accrues as for any step. reorder-or-not
likewise, with its own primitive, every crystal heated.

A two-qubit gate on a C2LR device acts on the two crystals of one leg after an enter (C1): the
gate step entails a merge before the MS gate and a split after it, priced from the device's
merge and split curves.

## 4. Rules

The new rules join the catalogue as C1-C10 and form the rule set `c2lr` (`include "standard"`,
plus C1-C10). In a C2LR step:

| rule | how it is decided |
|---|---|
| C1 | a leg holds at most one crystal, except two between an enter and the next exit of the same step type |
| C2 | the step's only per-leg choice is the direction bit: the template has no other per-leg field |
| C3 | horizontal and vertical legs choose independently in one step (by construction) |
| C4/C5 | a step is C2LR -> shared waveform -> C2LR⁻¹ and ends with every crystal at a leg centre (by construction; C7) |
| C6 | the pairs are the device's matching under one variation it offers, and the rule set allows the variation's form (`c2lr_forms`): every crystal shifted toward has a partner leg |
| C7 | no crystal ends a step in a junction (R23 without its room clause) |
| C8 | the COM frequency is held during a shift (a property of the primitive; recorded, not decided) |
| C9 | every crystal is heated by every waveform (the replay's charge) |
| C10 | control is the fixed shared signals plus one input and one switch per leg (the wiring census) |

R2, R3, R5 and R22 are exempt for a swap-or-stay step **because** its routes are the device's
matching (two crystals cross one junction in a swap, and a swap is an exchange on the two rails
by design). R4 still requires the step's class to be declared. Checkerboard steps carry the
`assumed` mark into the report; the rule set `c2lr-demonstrated` refuses them (C6).

## 5. The certificate

The compiler writes each crystal that moves as an ordinary move at the step's time, leg to
partner leg (a hop through one junction, which the checker's hop table already holds). Crystals
that stay write nothing. The proved checker is unchanged; `check_program_transport` expands the
template with the device to compare it with the certificate.

## 6. Numbers

Checked against arXiv:2403.00756 v2 (2026-10-30). One crystal is one ion of this model, so one
n̄ per crystal: the axial COM mode's (XCOM). The stretch mode is not modelled.

| primitive | Ba-Sr | Yb-Ba | where |
|---|---|---|---|
| swap-or-stay step, time | 312.5 µs | 400 µs | 1/Γ_swap, Γ = 3.2 kHz at 3.0 m/s and 2.5 kHz at 3.3 m/s (Sec. V, p6). One 1/Γ is one swap-or-stay, both C2LR shifts, the 10 µs interpolations and (Yb-Ba) the rotations included; the "750 µm, twice the on-grid distance" is the summed well travel of one operation, so the proposal's 1/(2Γ) halves it |
| heat per step, swap / stay | 0.14 / 0.035 q | 0.18 / 0.165 q | derived, the larger of two readings: Table II (measured at this rate: XCOM n̄ after a doubled operation, Ba-Sr 0.48 / 0.25, Yb-Ba 0.56 / 0.53) less the ≈ 0.2 q cooling leaves (p4), halved; and Table III's effective rates during slow transport (Ba-Sr 123 / 113 q/s, Yb-Ba 183 / 202 q/s) × 1/Γ = 0.038 / 0.035 and 0.073 / 0.081 q. The first is larger except for Ba-Sr's stay, where it gives 0.025 q, below what static heating alone adds |
| reorder-or-not | -- | 93 µs, 0.0195 q | Fig. 5(a) and text, p7 (Yb-Ba only) |
| C2LR shift | 62.5 µm | 62.5 µm | p4, "1/6 of the leg length" (inside the step above; not priced separately) |
| COM frequency held | 1.85 MHz | 1.4 MHz | p4 (C8) |
| shared analog signals | 27 | 27 | p4; plus N digital inputs and N switches, and two site-dependent shims per leg (C10) |
| merge, split | 40 µs, 0.1 q each | same | not in the paper: the platform's own merge/split curves, slow point |

What the proposal had wrong: the step time (1/(2Γ): half), the heat (Table II halved, but its
entries are final n̄ after a doubled operation with the 0.1-0.2 q left by cooling, not
increments), Yb-Ba's C2LR excitation (18.5 mq is v1's; v2 gives 11.7 mq at 6 m/s, Table I), the
shift (62.5 µm, not 63), and the pairings (all four single diagonals were demonstrated; its
two-pair steps were not).

## 6a. Phase 1: what the Python core decided (2026-10-30)

Where this design left a choice open, phase 1 made it as follows (code in `qccd/arch/c2lr.py`,
`qccd/verify/c2lr.py`; tests in `tests/test_c2lr.py`).

- **Compass.** W is the smaller x, E the larger; N is the **smaller** y, because the studio draws
  y down the page. A diagonal's name depends on this; which steps exist does not.
- **What a lattice is.** A leg is a site with exactly two rails, each to a non-site node, on
  opposite sides of it along one axis; every rail of a junction node ends at a leg. A degree-2
  corner node is a junction of the lattice (E-S at the corner pairs through a bend). A
  wrap-around wire (a periodic grid) is not a leg, so a periodic grid is not C2LR. The
  checkerboard colour is `(r + c) mod 2` by the junction's ranks in x and y; a lattice on which
  some leg joins two junctions of one colour offers no checkerboard.
- **Template shape** (`qccd.ir.tsir.template_problems`): exactly one of `diagonal` and
  `checkerboard {parity, pairing}`, a `toward` list, no other key, and no `participants` beside
  the template (C2) -- refused by `validate_program` and before the replay runs.
- **Two strengths of refusal.** A step that cannot execute raises `ReplayError`: the device is
  not C2LR, a named crystal is unplaced or not in a leg, the shape is wrong, the device prices
  no such step (a Ba-Sr device has no `reorder_or_not`: the paper measures it for Yb-Ba only).
  A step that executes but breaks a rule runs as the exchange rule says and fails the rule: a
  bit on a leg with no partner (C6; the crystal stays), a variation the device does not offer
  (C6), a form the rule set does not allow (C6, `c2lr-demonstrated`), a bit on the crystal
  further from the junction in a two-crystal leg (C1; it stays), an exit by another variation,
  the resident leaving, or a third crystal (C1).
- **"The same step type"** is the same variation: the diagonal, or the checkerboard's pairing
  and parity. In a leg holding two, each crystal is near one end; only the one nearer the step's
  junction can be shifted toward it.
- **Price.** Mover `quanta_swap`, every other crystal `quanta_stay`, on a heat channel of its own,
  `c2lr` (not added to the five shared channels, so nothing else's report changes). The step's
  time is the primitive's even with no mover; its cost is one per mover. reorder-or-not heats
  every crystal by `quanta` (or `quanta_reverse` / `quanta_stay` if given). A gate on two
  crystals of one leg is merge + MS + split; the merge's heat lands before the gate's n-bar is
  read, so R7 and R16 see what the MS gate sees. The device's merge and split curves carry the
  40 µs / 0.1 q point only, so every operating-point policy reads it.
- **The exemption** is R2's crossing half, R3, R5 and R22 on a template step, keyed on the
  replay's own `CycleView.c2lr` (`qccd.verify.rules.device_exchange`); in the rule language,
  `exchange(t)` / `exchange(a)` in R2, R3, R5 and R22.
- **C rules.** C1 (occupancy and exits), C2 (a C2LR-class step with its own routes), C5 (every
  resting crystal in a leg), C6, C7 and C10 (the document's census) decide; C3, C4 and C9 hold by
  construction and are checked; C8 is recorded from the primitive's `com_mhz`. They judge C2LR
  devices only and are marked checked only there. Their legacy one-line statements are
  `qccd.verify.rules.C2LR_RULE_STATEMENTS`, apart from the 28 every page lists.
- **Assumed** steps are listed under `assumed` in both verifiers' summaries, and only when
  there is one.

## 7. Boards

C2LR designs are ranked on their own releases (the same circuits as the existing boards, with
physics that adds the C2LR primitives), shown beside the shuttle boards. No existing release or
entry changes.

## 8. Order of work

1. Core (Python): device detection and pairing, the two templates in the replay, pricing,
   C1-C10, the `c2lr` rule sets, the certificate correspondence, a C2LR grid device. Tests:
   a hand-written step judged both ways, a swap that would break R2/R3/R5/R22 as plain moves and
   passes as a template, a forged template refused, stayer heating, enter/gate/exit.
2. Browser: engine.js twin of the expansion and the price (parity tests), the animation (paired
   crystals exchanging at the junction, stayers out and back).
3. Boards and bench: the C2LR releases, `qccd bench run --compiler` accepting the new steps, the
   proposer's router graded on them; the site (Rules, Language, Physics).
