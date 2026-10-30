# C2LR swap-or-stay transport

Status: design, 2026-10-30. Decisions taken by the project owner on 2026-10-30: pairing P2 is
allowed and marked assumed; C2LR designs are ranked on a separate track; the certificate writes
swaps as plain moves (no change to the proved checker); every physical number is checked against
the paper before it enters a physics file.

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
            "c2lr": {"pairings": ["P1", "P2"], "assumed": ["P2"]},
            "classes": {"extra": ["swap_or_stay_p1", "swap_or_stay_p2", "reorder_or_not"]}}
```

and prices its steps as primitives (numbers per section 6, each with its source):

```json
"swap_or_stay":  {"us": ..., "quanta_swap": ..., "quanta_stay": ..., "source": "..."},
"reorder_or_not": {"us": ..., "quanta": ..., "source": "..."}
```

## 2. The step

**Parity.** Colour the junctions like a checkerboard, `(r + c) mod 2`. Two adjacent junctions
have different colours, so every leg touches exactly one junction of each colour. A step names
one parity `p`; its junctions are *active*, and each leg is paired at its one active junction.

**Pairing.** At an active junction with legs W, E, N, S (read off the positions):
P1 pairs W with S and N with E; P2 pairs W with N and S with E. A leg whose partner does not
exist (the device's edge) is unpaired in that step. Every step is therefore a matching of legs.

**Direction bits.** Every crystal shifts toward its active junction or away from it (C2LR, the
per-leg switch). A step lists the crystals shifted **toward**; every other crystal shifts away.
Then one shared junction waveform exchanges, at every active junction, the two wells nearest it
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
{"type": "simd", "class": "swap_or_stay_p1", "mode": "inter",
 "template": {"kind": "swap_or_stay", "parity": 0, "pairing": "P1", "toward": ["q3", "q17"]}}
```

The replay expands it from the **device**, never from the programme: the active junctions, the
pairs and each mover's route (leg -> junction -> partner leg) are computed from the geometry and
the parity, and the programme supplies only the bits. That is what lets the rules below exempt a
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
| C6 | the pairs are a matching under P1 or P2 at one parity (checked against the device) |
| C7 | no crystal ends a step in a junction (R2/R23, unchanged) |
| C8 | the COM frequency is held during a shift (a property of the primitive; recorded, not decided) |
| C9 | every crystal is heated by every waveform (the replay's charge) |
| C10 | control is the fixed shared signals plus one input and one switch per leg (the wiring census) |

R2, R3, R5 and R22 are exempt for a swap-or-stay step **because** its routes are the device's
matching (two crystals cross one junction in a swap, and a swap is an exchange on the two rails
by design). R4 still requires the step's class to be declared. P2 steps carry the device's
`assumed` mark into the report; the rule set `c2lr-demonstrated` drops P2.

## 5. The certificate

The compiler writes each crystal that moves as an ordinary move at the step's time, leg to
partner leg (a hop through one junction, which the checker's hop table already holds). Crystals
that stay write nothing. The proved checker is unchanged; `check_program_transport` expands the
template with the device to compare it with the certificate.

## 6. Numbers

Every number is checked against arXiv:2403.00756 and cited by figure or table; a number the
paper does not give is marked as the proposer's derivation or left out. (To be filled from the
check.)

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
