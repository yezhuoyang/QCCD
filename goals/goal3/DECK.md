# Goal 3 and the transport deck: where each item went

This maps every item of the deck "QCCD Transport Architectures" (Ke, 2026-11-06), of
"Helios Nominal Noise Model" (the deck's `base-error-model`) and of Ke's feedback on the first
sweep ("QCCD Studio Feedback", 2026-11-07, under "The feedback") to what Goal 3 does with it.
Where the documents disagree or leave a gap, the reading taken is under "Readings", each marked
open or answered.

The deck's slide 2 sets the order: use the noise model's number where it has one, and the deck's
where it has none. Section 7 of the noise model calls itself an example clock, so the deck's
transport numbers beat it, and it only fills what the deck also lacks.

| what | where |
|---|---|
| the noise model | [`qccd-noise@3.eval`](../../qccd/evalset/library/qccd-noise@3.eval) |
| its physics, table `helios` | [`qccd-physics@3.json`](../../qccd/workspace/physics/qccd-physics@3.json) |
| prices and hardware census | [`c2lr.py`](../../qccd/arch/c2lr.py), [`wise.py`](../../qccd/arch/wise.py), [`cyclone.py`](../../qccd/arch/cyclone.py) |
| compilers | [`c2lr_router.py`](../../qccd/compile/c2lr_router.py), [`wise_router.py`](../../qccd/compile/wise_router.py), [`cyclone_router.py`](../../qccd/compile/cyclone_router.py) |
| rule sets | [`c2lr-deck`](../../qccd/ruleset/library/c2lr-deck.rules), [`wise`](../../qccd/ruleset/library/wise.rules), [`cyclone-deck`](../../qccd/ruleset/library/cyclone-deck.rules), [`conveyor-deck`](../../qccd/ruleset/library/conveyor-deck.rules), [`grid-deck`](../../qccd/ruleset/library/grid-deck.rules) |
| the shuttling speed | [`speed.py`](../../qccd/arch/speed.py) (the law), [`speed.py`](speed.py) (the runs) |
| chain order, for R34 and the gate swap | [`chains.py`](../../qccd/verify/chains.py) |
| the cost table on the Goal 3 page (F1) | [`goal3_costs.py`](../../qccd/site/goal3_costs.py) |
| families and sizes | [`families.py`](families.py) |
| tests, the deck's tables included | [`test_goal3_deck.py`](../../tests/test_goal3_deck.py) |

## The noise model

| section | what it says | here |
|---|---|---|
| 1 | 1Q p = 2.07e-5, leakage 8.4e-6 | channels `gate_1q`, `gate_1q_leakage`; a frame change and an identity are not pulses |
| 1 | 2Q cooled, excluding cooling dephasing, p ~ 6.74e-4; leakage 9e-5 on each target | channels `ms`, `ms_leakage`: r = 5.5e-4 - 1.1e-5, p = 5r/4 = 6.7375e-4 |
| 1 | prep / reset 5e-5; readout 4.3e-4 | channels `prepare`, `reset`, `readout_z`, `readout_x` |
| 1 | crosstalk 7.2e-5 per spectator; MR's combined row used once | channel `crosstalk`, at the measurement; a reset adds none; summed over the ions read out together, a simulation assumption (reading 1) |
| 2 | pZ(t) = 0.5 [1 - exp(-t/T_lin - (t/T_g)^2)], once over each idle window | channel `idle_dephasing`, over `waited(i, o)`: transport, cooling and other ions' operations do not split it |
| 2 | idle leakage: the transport-layer rate, folded by 0.75 | channel `idle_leakage`; the model does not print the rate, Jaewon's report does (reading 3) |
| 3 | cooling 3 ms, 2Q 70 us, measure 150 us, reset 20 us | `qccd-physics@3` |
| 4 | n_cal 0.05, 3 quanta/s, junction 0.014 quanta, slope 9.5e-4 | the ledger starts from 0 at n_cal; `p_2Q = p_baseline + slope * excess` inside `ms` |
| 5 | shuttle 0.1, split 2 or 4.16, merge grouped with split, reorder 0.007 | `qccd-physics@3` takes split 2 and merge 0; see below |
| 6 | R7: one broadcast cool before a batch that exceeds the budget | the cooling pass, unchanged; the budget stays 1 quantum |
| 7 | the example clock | 1Q 5 us outside WISE; C2LR's merge and split, 80 us each |

Not used: the 1.1e-5 adjustment's alternative row (the original p = 6.875e-4), since pre-gate
cooling is counted in the idle window, and dynamical decoupling and correlated dephasing, which the
model turns off.

## C2LR (arXiv:2403.00756)

**Sweep:** Cx x Cy, species Yb-Ba. **Compiler:** the existing baseline router, packing a layer's pulses by chain
position (R34). **Rule set:** `c2lr-deck`, which is `c2lr-demonstrated` and R34; `c2lr-demonstrated` itself is
unchanged, since the C2LR boards judge by it.

| deck | here |
|---|---|
| S1 one crystal per leg | C1 |
| S2 individual control per leg | C2 |
| S3 everyone heats | C9; the replay heats every crystal |
| S4 broadcasting limits | C6, with only the four diagonals allowed (`c2lr_forms`): one diagonal for the whole chip in a step, which is F3 |
| S5 parallel axes | C3 |
| S6 multi-well waveforms | inside the step's primitive; the checkerboard form is off, as the deck says it isn't needed |
| S7 step shape | C4, C5, C7 |
| S8 swap-or-stay | C6, and the R2, R3, R5, R22 exemption |
| S9 constant frequency | C8, 1.4 MHz recorded on the primitive |
| S10 reorder-or-not | the machine prices it (93 us); the router never reorders a crystal: it keeps each leg's order (`qccd.verify.chains`) and splits a layer's pulses by the position they address (R34, F2) |
| **S11 enter and exit (open)** | the platform's existing reading: C1's enter-exit window, one crystal enters by a step and leaves by the same type, merge + MS + split between. Marked open, as the deck has it |
| HW1, HW7 | `grid(Cx, Cy)`; one pair of gate beams per leg, in the census |
| HW2-HW6 | the census (`c2lr.deck_census`); C10 for HW3, HW4 |
| T1 | 400 us a step (Ba-Sr 312.5 us) |
| T2-T4 | inside T1 |
| T5, T9 | reorder-or-not: 93 us, 0.007 quanta |
| T6, T7 | the gate mode's heat per step: (0.31 - 0.03)/2 = 0.14 q a swap, (0.10 - 0.03)/2 = 0.035 q a stay |
| T8 | not charged apart: T6 and T7 include it |
| T10-T12 | not used: the noise model's 3 quanta/s is the background, and T6, T7 are at this transport speed |

## WISE (arXiv:2305.12773)

**Sweep:** h, Cx, Cy. **Compiler:** new, [`wise_router.py`](../../qccd/compile/wise_router.py). **Rule set:** `wise`.

| deck | here |
|---|---|
| S1 zones and storage | W1, R1 (zone capacity 2) |
| S2 zone parity | W2 |
| S3 one bit per zone | R22 as written: each half of a swap is one motion, so nothing needs relaxing |
| S4 one operation kind per step | W4, R4b |
| S5 switchable swap | W2: a step's pairs are one kind and one parity, the same pairing the machine derives |
| S6 swap mechanism | **one step (F4):** the pair exchanges its ions in the step, priced by kind (`wise_swap`): t0 between neighbours, a junction step of C2LR's 400 us across a junction zone or between rows (F1). The replay calls it an exchange only where every move is the chip's own pair of that kind, so R2, R3, R5 and R22 leave the crossing alone as for a C2LR exchange |
| S7 odd-even sort | an algorithm, so no rule. The router takes real steps from the same step types; the deck's worst case is reported beside it (`reconfiguration_ms`) |
| S8 junction occupancy | R2, R23. See "Readings" |
| S9 transport-assisted gates | R6, R6b, R12: a pair merges into one gate zone, gates, splits |
| S10 gate layers | W10: one gate at one angle per layer; a frame change is not a pulse |
| S11 shims held | W11: every layer pays its charge, and a reconfiguration opens with one |
| S12 switching | nothing to charge; R17 and the waveforms carry the heat |
| HW1-HW8 | `wise.census`, which reproduces the deck's three example columns |
| T1 | t0 = 100 us |
| T2 | the census's `reconfiguration_ms`; a program's real time comes from its real steps |
| T3 | the census's `switch_word_us`, under t0 at every size swept |
| T4 | 384 us in every layer's price |
| T5, T6 | 1Q 1 + 384 us; **2Q 70 + 384 us: the noise model's gate wins over the deck's 100 us** |
| T7 | follows from the above |
| T8 | the noise model's idle channels |
| T9 | channel `residual_drive`: 1e-5 on every qubit a layer of pulses leaves out, the deck's bound used as the value; 0 on the other families |
| T10, T11 | no error number to charge; both within tolerance by the deck's figures |

## Cyclone (arXiv:2511.15910)

**Sweep:** x, n <= 30. **Compiler:** new, [`cyclone_router.py`](../../qccd/compile/cyclone_router.py). **Rule set:** `cyclone-deck`.

| deck | here |
|---|---|
| S1 ring topology, S9 one loop | Y1 |
| S2 data stays | Y2 |
| S3 lockstep | Y3, R5, R11 |
| S4 gates, then rotate | R4b, and the compiler's step |
| S5 one operation per trap | R27, R12 |
| **S6 two rotations per round** | **not followed (the project owner's decision, 2026-11-06):** Goal 3 keeps the circuit's gate order, so R25 rather than R26, and a round takes as many rotations as that order needs |
| S7 swap before leaving, T3 | 3 CX of time on the split, every step (lockstep waits for the slowest trap); its error, 3 x the two-qubit gate's (F1), on the ancilla and the ion it changes places with (`gate_swap`) |
| S8 flexible trap count | any even x, every trap waiting for the busiest in lockstep |
| S10 cooling | **heats and cools as the other two do (Q2):** the noise model's split, merge, rail and background heat, and the same 3 ms cooling by R7 |
| HW1-HW7 | Y4 and `cyclone.census`, which reproduces the deck's three example columns |
| HW5, HW6 | not used: variants the paper proposes, outside this sweep |
| T1, T2 | split 80, move 10, merge 80; each degree-2 crossing 10 |
| T4 | QCCDSim's law, max(100, 13.33 n - 54), scaled so its flat part is the noise model's 70 us |
| T5 | reported as `t5_worst_case_ms`; a program's real time comes from its replay |
| T6 | what the circuit's order needs; 2 rotations under S6 is not reachable here |
| T7, T8 | the noise model's channels, and `gate_swap` |
| T9-T11 | the paper's results, not inputs |

## The feedback (Ke, 2026-11-07)

"QCCD Studio Feedback": the first sweep "basically follows the rules"; some fixes, a new table, and the
answers to the first round's questions.

| item | here |
|---|---|
| F1 the cost terms | a table on the Goal 3 page ([`goal3_costs.py`](../../qccd/site/goal3_costs.py)): every term of the error model, cooling, motion and transport, and each architecture's own, beside what the sweep charges, read from the physics, the noise model and the prices the sweep runs on. Values the feedback marks for the noise-model team to verify (split and merge heat 6, junction 3, WISE swap error ~5 x 10⁻³ and heat 12, the per-shuttle and per-junction errors, ion loss) are shown and marked, and the sweep keeps the noise model's own until they are verified (Q3) |
| F2 a laser step addresses one chain position | **R34**, in all three rule sets. The replay does not keep the order inside a chain (#22), so it is kept beside it ([`chains.py`](../../qccd/verify/chains.py)), the same way for the compilers and the rules; nothing an older rule judges changes. Each compiler packs a layer by chain position: C2LR's when asked (`positions=True`, the boards keep their packing), WISE's by construction, Cyclone's by playing, at each instruction, the position most traps are ready for |
| F3 one C2LR junction type a step | already so: a step's template names one diagonal for every junction, C6 judges it, and `c2lr-demonstrated` refuses the checkerboard form. C6's text now says "the same diagonal at every junction" |
| F4 a WISE swap is one step | done (S6 above): one exchange step, t0, not two half steps |
| F5 WISE is serial | an observation; no change |
| Q1 three Cyclone rotations a round | fine with the circuit's order kept, as here |
| Q2 should Cyclone pay for cooling | yes: the same rule (R7) and time (3 ms) for all three families, and Cyclone heats as the others do. The cooling pass now counts a gate's heat from the last cool it put in (`CoolingPolicy.lazy`) for all three, which matters most on Cyclone, whose traps gate in turn: one broadcast cool serves them all |
| Q3 C2LR's enter and exit (S11) | fine for now; the noise-model team takes the costs up after the F1 table |
| slide 4, GateSwap | 3 x 70 us and 3 x the two-qubit error: the error is now charged (`gate_swap`, `gate_swap_leakage`) on the ancilla and the ion it changes places with |
| slide 4, WISE junction step | C2LR's time, 400 us, for a swap across a junction zone or between rows |

## The comments on the pages (Ke, 2026-11-20 and 11-23)

Six notes pinned on the Rules and Goal 3 pages, after the five families went up.

| comment | here |
|---|---|
| Conveyor, BB code: "some of the data qubits on the outer side don't move while others are moving", and many sites on the ring stand empty | **Right, and it was every Conveyor point, not only BB72's.** The family was a loop with dock spurs compiled by the released qccdc, whose rotation mode declines a memory experiment (it cannot read out an ion that rides the loop), so its general router compiled all 26 points: ions shuttled one at a time along the loop, past ions standing on it (on BB72, 10,234 of 11,000 loop steps). No rule judged it: the machine declares a switch at every site, so R4d lets a site sit a step out. **Now:** the family is the planar conveyor of `qccd.arch.sandwich`, data traps that stand still beside one or two loops that carry the ancillas, under the conveyor router; a loop turns as one. **R39** says so, in `conveyor-deck`; the old points fail it (kept in `results/loop_by_general_router/`) |
| Cyclone: "every trapping zone need to hold the same number of ions", ex. colour d=3 | **Y5**, in `cyclone-deck`. One set of waveforms is broadcast to every trap, so every trap holds the same number of data qubits and of ancillas after every step. Colour d=3's traps held 4 and 6. Where a code's qubits do not divide among the traps, spare ions fill them: a spare among the data stands still; a spare in an ancilla's place rotates with the ancillas and pays the same gate swap. The sweep takes rings whose trap count divides the ancillas where there are any, which need no rotating spare. BB72's 6- and 72-trap rings were level already; its 10, 16, 26 and 44-trap rings were not (`results/uneven_traps/`), and its level rings are 6, 12, 18, 24, 36 and 72 |
| Helios: "why is it preferable for ions to complete a full loop rather than shuttling back and forth through junctions? Does this help reduce error rates?" | It does not reduce error; it was the baseline router's choice, and a consequence of the 5 us rail. The router turned the ring the way that carried fewer ions across the X junction, whatever the distance: on colour d=3 its first fetch went 75 sites round rather than 7 back. At 5 us a rail and 120 us a crossing that is in fact quicker (375 us against 875). **Now** the turn is chosen by time at the machine's own prices, so at a shuttling speed of a few m/s it goes back and forth through the junction |
| "Add default QCCD architecture" | **Grid**, a sixth family: the junction lattice of this website's boards, every ion shuttled by itself, compiled by the released qccdc, judged by `grid-deck` (the standard rules and R34). It was Goal 3's baseline before the deck (`results/pre_deck/`) |
| WISE: "the ion-swapping is wrong: need to add one more trapping site in between every two trapping sites, mainly for ion swap. Or add one more rule that ions can swap in between trapping sites" | The second: **W5**, in `wise`. A swap is a switched swap between a pair's two zones, over the rail that joins them, with no site of its own; two ions cross on a rail only so. Until now only the exemptions in R2, R3 and R5 implied it. No program or number changes. A site between every two zones would be a different chip (2h - 1 sites a cell) and a recompile of every WISE point; see reading 19 |
| "How is the shuttling speed counted? m/s?" | It was not. A rail cost a fixed time whatever its length: 5 us in the noise model's example clock, 10 us for a Cyclone move (deck T1), half of WISE's t0. On the 464 um trap pitch the pages are drawn in that is 93, 46 and 9 m/s. **Now** `qccd.arch.speed` prices a rail from a speed in m/s, and `speed.py` runs each family's best point at 1 to 35 m/s; the Goal 3 page draws it ("Against the shuttling speed"). The main figure keeps each family's own prices until the team picks a speed; see reading 18 |

## Readings

These are the choices a person could reasonably have made another way, the ones that move results first.
Each is open unless it says it was answered.

1. **Crosstalk under parallel readout (noise model Sec. 1). Answered by Zhuangzhuang, 2026-11-07: a
   simulation assumption, kept.** Jaewon's report gives 7.2e-5 per affected spectator for a measurement or
   reset, but not how several ions measured in parallel combine, and each one's contribution depends on where
   the spectator sits relative to the measurement light. If the scattering is weak and independent, the
   contributions add to first order. Charging every spectator the same 7.2e-5 for each ion read out, as the
   channel does (one `DEPOLARIZE1` of k x 7.2e-5 for k ions read out together), is that assumption, and the
   channel's text says so. Also this sweep's choice: every other ion on the chip is a spectator, at the same
   strength. It is the largest term in the budget, **44 to 58% of all error on surface d=7**, where 48
   ancillas read out each round, so the logical error rates lean on it. A contribution that falls with
   distance from the measurement light would lower it most on the largest chips.
2. **Cooling time (noise model Sec. 3, Sec. 6). Answered by Ke, 2026-11-07 (Q2): every family pays the same
   rule and time.** Used the 3 ms reference for all three. In the first sweep Cyclone paid nothing under S10,
   and at 300 us C2LR's surface d=3 round went from 18.2 to 7.4 ms, ahead of Cyclone's 9.2. Those runs, at 300 us
   for surface d = 3, 5, 7 and colour d = 3, 5, are in `results/deck_r1/variants/cool300us/`.
3. **Idle leakage rate (noise model Sec. 2). Answered by Zhuangzhuang, 2026-11-07: confirmed.** The model
   names the rate without printing it. Used 0.75 x 4.0e-4 per ion per 55 ms layer = 5.45e-3 /s. Jaewon's
   report gives 1.45e-3 per ion at 200 ms before the Stim conversion, which is 4.0e-4 x 200/55 rounded, and
   applies the 0.75 in `DEPOLARIZE1`, as here. It is also Helios's rate (arXiv:2511.05465 Sec. III B4). It is
   about 60% of the idle error, and 7 to 12% of all error on surface d=7.
4. **The GateSwap's error (Cyclone S7). Answered by Ke, 2026-11-07: 3 x the two-qubit gate's error.**
   Charged on the ancilla and the ion it changes places with, three cooled two-ion depolarizing channels composed,
   and their leakage, where the chain order says an ion stands between the ancilla and the end it leaves by. It is
   the largest term in Cyclone's budget, about 4 x 10⁻³ on an ancilla and its partner at every hop, which is hard
   to square with the paper's reported logical error rates, so the paper must not be charging it. An ancilla
   enters a trap at the end toward the previous trap and leaves by the other, so wherever a data ion sits in the
   trap every hop pays one, and a ring of x traps makes some x hops per ancilla per rotation. On surface d=7 the
   50-trap ring charges 29 expected faults a round to gate swaps, against 0.23 to every MS gate together, and its
   logical error rate is 0.18 a round; the 4-trap ring charges 2.3 and reads 0.018. The compressed rings come
   first for this reason, and every larger Cyclone is past threshold under this charge.
5. **Chain length.** Neither document has an error that grows with chain length, so a Cyclone trap of
   30 ions pays for its length only in gate time. The noise model marks longer chains as open. Compressed rings
   are favoured by this.
6. **C2LR's merge and split.** The paper has none; used the noise model's 80 us and its heat, which it
   marks as open for C2LR. This is S11's cost; Ke, 2026-11-07 (Q3): fine for now.
7. **The heat mode.** Used the stretch mode (XSTR) for C2LR, because the noise model counts heat in the
   gate mode and its background rate is the stretch mode's. `docs/c2lr.md` and the C2LR boards use XCOM.
8. **Split heat (noise model Sec. 5: "2 or 4.16").** Used 2. It should barely matter: a merge trips R7's
   1-quantum budget, so these families cool before each batch of gates either way.
9. **WISE's junction zones (S8).** The deck says the layout fits if they are modelled as sites. Here they
   stay real junctions on which no ion rests, which keeps R18 and R23 as written. A chip therefore holds h
   qubits per cell, not the deck's h + 1; its census is still the deck's.
10. **Cyclone's ancillas.** Goal 3's circuits have one ancilla per check, not the paper's one per two
    checks. So a ring holds N + m ions, where the deck writes N + m/2.
11. **Ancillas sharing a trap leave in turn**, because R3 and R27 require it. A rotation then costs
    A x (s + t), where T5 prints s + A x t.
12. **WISE's sizes.** h is swept over the deck's three example values, 3, 5 and 7, where it says "sweep freely".
13. **Which end is position 1 (R34).** On a ring, the end of a trap toward the previous trap, so every trap of
    a Cyclone ring is the same trap and a step can reach the same position in all of them; on any other site,
    the end with the smaller coordinate. A pattern is the chain's length and the positions: the 1st of 3 and the
    1st of 4 are different positions. Read the other way (from the chip's left on a ring, say), the top and bottom
    rows of a Cyclone ring, mirror images, would seldom play together.
14. **When a gate swap happens.** Every Cyclone step charges its 3 CX of time, since lockstep waits for the
    slowest trap; the error lands only where an ion stands between the ancilla and its exit, with that ion. One
    swap reaches the end however far, since a two-ion gate can pair any two ions of a chain.
15. **WISE's junction step heat.** Its time is C2LR's (400 us); its heat is its routes', a split, a merge,
    its rails and its crossings, 2.2 to 2.3 quanta, where C2LR's step deposits 0.14. The page re-prices heat
    from the routes, and every WISE layer cools after its splits anyway, so no cooling moves.
16. **Cyclone's corner heat.** The feedback says "shared junction row", where that row's 3 quanta is not yet
    verified and the noise model's proxy is 0.014. A corner pays its two rails, 0.2.
17. **The values to verify (F1).** Split and merge 6 quanta each, a junction 3, a WISE swap ~5 x 10⁻³ and 12
    quanta, an error per shuttle and per junction, ion loss: shown in the cost table, not charged, until the
    noise-model team confirms them.
18. **The shuttling speed.** A rail's time is its length over the speed; its heat is 0.36 (v / 35 m/s)^2 quanta a
    shuttle, the fastest low-excitation shuttle measured (arXiv:2201.07358: 35 m/s, at most 0.36 +- 0.08 quanta),
    scaled as the square of the speed, a kick's law. At 4 m/s that is 0.005 quanta a rail, which is Quantinuum's
    Ba-Yb figure there once the background heating is taken off (arXiv:2206.11888: at most 0.03 quanta for four
    375 um legs, of which 29 quanta/s over 375 us is 0.011). The noise model's 0.1 quanta a shuttle is this law at
    18 m/s. Only straight rails scale: a junction crossing (100 to 120 us, about 4 m/s over a pitch already), a
    split, a merge, a C2LR step (its paper's, at 3.3 m/s) and a WISE junction step keep their sources' times,
    since the same paper finds coherent excitation above 6 m/s through a junction. A WISE swap between neighbours
    is two rails, as t0 is twice its half. No error is charged for a shuttle itself. Open: which speed the main
    figure should take, and whether a junction's time should scale too.
19. **Where a WISE swap happens.** Between the pair's two zones, over the rail that joins them (W5), as the
    feedback's F4 made it one step. The other reading the comment offers, a trapping site between every two
    zones where the pair merges, turns and splits, would make a cell 2h - 1 sites and price a swap as a merge, a
    crystal rotation and a split there; it changes every WISE program.
20. **Cyclone's spare ions.** Y5 needs every trap to hold the same number; the deck does not say what fills a
    trap when a code's qubits do not divide. Here: spare ions, which are real ions (they heat, are cooled, and
    lengthen their chain), carry no qubit, and where they stand in an ancilla's place rotate and are gate-swapped
    to the trap's edge like one, the swap's error landing on the ion they change places with. Read otherwise (a
    spare slot left empty, or no swap for a spare), the larger rings of an indivisible code would do better.
21. **What a conveyor is.** Data traps that stand still beside the loops, the ancillas riding (the planar
    machine of `qccd.arch.sandwich`), since that is the conveyor this platform has a router for that can run a
    memory experiment. Its loop slots carry lasers, so an ancilla is prepared and read out where it rides. A loop
    with dock spurs in which every ion that gates is docked is another conveyor; qccdc's rotation mode compiles
    it for a syndrome round but not for a memory, whose data are read out.

## Differences from the paper the deck may want to check

- **C2LR T8 (Yb-Ba 18.5 / 1.0 mq):** `docs/c2lr.md` records 18.5 as the paper's v1 figure, and v2 (Phys.
  Rev. X 14, 041028) gives 11.7 at 6 m/s. Nothing here charges T8 apart, so no result changes.
- **Cyclone T1 (move 10 us):** the paper's artifact, as Reproduce replays it, uses 5 us. This sweep follows
  the deck.
- **Cyclone S2 ("chains stay well under R13's 15")** against **HW1 (n <= 30):** this sweep follows HW1.
