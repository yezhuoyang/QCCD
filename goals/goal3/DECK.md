# Goal 3 and the transport deck: where each item went

This maps every item of the deck "QCCD Transport Architectures" (Ke, 2026-11-06) and of
"Helios Nominal Noise Model" (the deck's `base-error-model`) to what Goal 3 does with it.
Where the two documents disagree or leave a gap, the reading taken is under "Readings to confirm".

The deck's slide 2 sets the order: use the noise model's number where it has one, and the deck's
where it has none. Section 7 of the noise model calls itself an example clock, so the deck's
transport numbers beat it, and it only fills what the deck also lacks.

| what | where |
|---|---|
| the noise model | [`qccd-noise@3.eval`](../../qccd/evalset/library/qccd-noise@3.eval) |
| its physics, table `helios` | [`qccd-physics@3.json`](../../qccd/workspace/physics/qccd-physics@3.json) |
| prices and hardware census | [`c2lr.py`](../../qccd/arch/c2lr.py), [`wise.py`](../../qccd/arch/wise.py), [`cyclone.py`](../../qccd/arch/cyclone.py) |
| compilers | [`c2lr_router.py`](../../qccd/compile/c2lr_router.py), [`wise_router.py`](../../qccd/compile/wise_router.py), [`cyclone_router.py`](../../qccd/compile/cyclone_router.py) |
| rule sets | [`c2lr-demonstrated`](../../qccd/ruleset/library/c2lr-demonstrated.rules), [`wise`](../../qccd/ruleset/library/wise.rules), [`cyclone-deck`](../../qccd/ruleset/library/cyclone-deck.rules) |
| families and sizes | [`families.py`](families.py) |
| tests, the deck's tables included | [`test_goal3_deck.py`](../../tests/test_goal3_deck.py) |

## The noise model

| section | what it says | here |
|---|---|---|
| 1 | 1Q p = 2.07e-5, leakage 8.4e-6 | channels `gate_1q`, `gate_1q_leakage`; a frame change and an identity are not pulses |
| 1 | 2Q cooled, excluding cooling dephasing, p ~ 6.74e-4; leakage 9e-5 on each target | channels `ms`, `ms_leakage`: r = 5.5e-4 - 1.1e-5, p = 5r/4 = 6.7375e-4 |
| 1 | prep / reset 5e-5; readout 4.3e-4 | channels `prepare`, `reset`, `readout_z`, `readout_x` |
| 1 | crosstalk 7.2e-5 per spectator; MR's combined row used once | channel `crosstalk`, at the measurement; a reset adds none |
| 2 | pZ(t) = 0.5 [1 - exp(-t/T_lin - (t/T_g)^2)], once over each idle window | channel `idle_dephasing`, over `waited(i, o)`: transport, cooling and other ions' operations do not split it |
| 2 | idle leakage: the transport-layer rate, folded by 0.75 | channel `idle_leakage`; **the rate is not printed**, see below |
| 3 | cooling 3 ms, 2Q 70 us, measure 150 us, reset 20 us | `qccd-physics@3` |
| 4 | n_cal 0.05, 3 quanta/s, junction 0.014 quanta, slope 9.5e-4 | the ledger starts from 0 at n_cal; `p_2Q = p_baseline + slope * excess` inside `ms` |
| 5 | shuttle 0.1, split 2 or 4.16, merge grouped with split, reorder 0.007 | `qccd-physics@3` takes split 2 and merge 0; see below |
| 6 | R7: one broadcast cool before a batch that exceeds the budget | the cooling pass, unchanged; the budget stays 1 quantum |
| 7 | the example clock | 1Q 5 us outside WISE; C2LR's merge and split, 80 us each |

Not used: the 1.1e-5 adjustment's alternative row (the original p = 6.875e-4), since pre-gate
cooling is counted in the idle window, and dynamical decoupling and correlated dephasing, which the
model turns off.

## C2LR (arXiv:2403.00756)

**Sweep:** Cx x Cy, species Yb-Ba. **Compiler:** the existing baseline router. **Rule set:** `c2lr-demonstrated`.

| deck | here |
|---|---|
| S1 one crystal per leg | C1 |
| S2 individual control per leg | C2 |
| S3 everyone heats | C9; the replay heats every crystal |
| S4 broadcasting limits | C6, with only the four diagonals allowed (`c2lr_forms`) |
| S5 parallel axes | C3 |
| S6 multi-well waveforms | inside the step's primitive; the checkerboard form is off, as the deck says it isn't needed |
| S7 step shape | C4, C5, C7 |
| S8 swap-or-stay | C6, and the R2, R3, R5, R22 exemption |
| S9 constant frequency | C8, 1.4 MHz recorded on the primitive |
| S10 reorder-or-not | the device prices it (93 us); the router has no use for it, since nothing tracks chain order here |
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
| S5 switchable swap | W2: a step's pairs are one kind and one parity, the same pairing the device derives |
| S6 swap mechanism | a merge half and a split half, R5 as written; 1 t0 between neighbours, 2 t0 across a junction zone, 3 t0 between rows |
| S7 odd-even sort | an algorithm, so no rule. The router takes real steps from the same step types; the deck's worst case is reported beside it (`reconfiguration_ms`) |
| S8 junction occupancy | R2, R23. See "Readings to confirm" |
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
| S7 swap before leaving, T3 | 3 CX of time on the split; **time only**, see below |
| S8 flexible trap count | any even x, every trap waiting for the busiest in lockstep |
| S10 cooling | **no heat and no cooling time**, as the deck says it stays at the paper's default |
| HW1-HW7 | Y4 and `cyclone.census`, which reproduces the deck's three example columns |
| HW5, HW6 | not used: variants the paper proposes, outside this sweep |
| T1, T2 | split 80, move 10, merge 80; each degree-2 crossing 10 |
| T4 | QCCDSim's law, max(100, 13.33 n - 54), scaled so its flat part is the noise model's 70 us |
| T5 | reported as `t5_worst_case_ms`; a program's real time comes from its replay |
| T6 | what the circuit's order needs; 2 rotations under S6 is not reachable here |
| T7, T8 | the noise model's channels |
| T9-T11 | the paper's results, not inputs |

## Readings to confirm

These are the choices a person could reasonably have made another way, the ones that move results first.

1. **Crosstalk under parallel readout (noise model Sec. 1).** Charged per measured ion: every spectator takes
   7.2e-5 for each ion read out, so reading out in parallel earns no discount. 7.2e-5 is 3/2 of Helios's
   4.8e-5 per measurement and reset, which is why this reading looks right, but it is the largest term in the
   budget: **44 to 58% of all error on surface d=7**, where 48 ancillas read out each round. If the model means
   once per readout layer, this term nearly vanishes for C2LR and WISE, and logical error rates fall.
2. **Cooling time (noise model Sec. 3, Sec. 6).** Used the 3 ms reference. It dominates C2LR's and WISE's time,
   while Cyclone pays nothing under S10, so the time comparison leans on it: at 300 us C2LR's surface d=3 round
   goes from 18.2 to 7.4 ms, ahead of Cyclone's 9.2. The logical error rate barely moves.
   `results/variants/cool300us/` has this for surface and colour d = 3, 5, 7.
3. **Idle leakage rate (noise model Sec. 2).** It names the rate without printing it. Used:
   0.75 x 4.0e-4 per ion per 55 ms layer = 5.45e-3 /s, from Helios (arXiv:2511.05465 Sec. III B4). The
   T_lin in the model matches that paper's non-leakage part, so this is probably what is meant, but
   it needs Zhuangzhuang's word. It is about 60% of the idle error, and 7 to 12% of all error on surface d=7.
4. **The GateSwap's error (Cyclone S7).** Time only. Neither document gives it an error. Charging its
   3 CX to the ions would put about 2 x 10⁻³ on an ancilla at every step, which is hard to square
   with the paper's reported logical error rates, so the paper must not be charging it either. Cyclone's
   logical error rates depend on this.
5. **Chain length.** Neither document has an error that grows with chain length, so a Cyclone trap of
   30 ions pays for its length only in gate time. The noise model marks longer chains as open. Compressed rings
   are favoured by this.
6. **C2LR's merge and split.** The paper has none; used the noise model's 80 us and its heat, which it
   marks as open for C2LR. This is S11's cost.
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

## Differences from the paper the deck may want to check

- **C2LR T8 (Yb-Ba 18.5 / 1.0 mq):** `docs/c2lr.md` records 18.5 as the paper's v1 figure, and v2 (Phys.
  Rev. X 14, 041028) gives 11.7 at 6 m/s. Nothing here charges T8 apart, so no result changes.
- **Cyclone T1 (move 10 us):** the paper's artifact, as Reproduce replays it, uses 5 us. This sweep follows
  the deck.
- **Cyclone S2 ("chains stay well under R13's 15")** against **HW1 (n <= 30):** this sweep follows HW1.
