# TrapSIMD / FluxTrap (Ruan et al., arXiv 2504.17886): the model we replay

The paper releases no code, schedules or circuits. The reproduction therefore has two parts:

1. **Exact.** The two fully specified worked examples (Fig. 6 and Fig. 7) are written event by event and
   replayed in `qccd.repro.timed`. They must reach the paper's own makespans: 982 / 732 µs and 823 / 545 µs.
2. **Model level.** We use the paper's device, its timing table and its broadcast rule. The circuits are
   **rebuilt** from the paper's description and scheduled by **our** compiler. The comparison with Table 3
   is therefore a comparison of models, not of artifacts.

The code is in `qccd/repro/trapsimd.py`, the tests in `tests/test_repro_trapsimd.py`. Everything in
this directory can be regenerated with `python -m qccd.repro.trapsimd Reproduce/ruan2025`.

Sources are cited by section, table or figure of the compiled paper (arXiv v2). The labels follow
the step-0 report:

- **Q**: quoted from the paper.
- **I**: inferred.
- **A**: an assumption of ours, because the paper does not say.

## 1. Device

| item | value | where | how it is written down |
|---|---|---|---|
| grid | a D×D grid of cells; (D+1)² X-junctions; one linear trap on every internal lattice edge, so 2D(D+1) traps | Table 2 and `fig/DxD_layout.pdf` (I, the trap counts 12/24/40/60 match exactly) | `grid_device(D, L)`. Junctions are `J{r}{c}`. Trap `H{r}{c}` runs from J(r,c) to J(r,c+1) and `V{r}{c}` from J(r,c) to J(r+1,c). Boundary junctions have no trap on their outer arms |
| configurations | A-20: D=2; A-40: D=3; A-60: D=4 (40 traps); A-100: D=5; each at L = 8 and L = 14 | Table 2 (Q) | `TABLE2_D` |
| trap | L ordered positions, each holding at most one ion | §2.1 "each zone typically holds at most one ion" (Q); Table 2 "L (1D Trap Capacity)" (Q) | every position is a `PortGraph` site of capacity 1, with `{"zone": "gate" or "aux", "trap", "index"}`. Consecutive positions are joined by a segment |
| gate zones | 2 per trap | Table 2 (Q) | `PortGraph.adjacent`: a gate-zone site maps to its two neighbours |
| where the zones sit | **not stated** | §3.1 shows only L=5 | **A**: spaced evenly, `round(k(L+1)/(N+1)) - 1`. This gives (2, 5) at L=8 and (4, 9) at L=14. The same formula reproduces both worked-example layouts: the middle position at L=3, and (1, 3, 5) at L=7. Kept as a parameter, see §6 |
| junction | an X-junction; it is not a position, and an ion never rests in it | §2.1 (Q), §3.1 (Q) | a node. A trap's edge position is joined to it by a segment, and the site records the arm (N/E/S/W) it lies on |
| Fig. 6 device | two junctions; traps of 3 positions with the zone in the middle; the left junction's west arm is a single auxiliary position | Fig. 6(a2), read off the figure | `w1_device()` |
| Fig. 7 device | one trap of 7 positions, zones at 1, 3, 5 | Fig. 7(a2) | `w2_device()` |

## 2. Primitives

Times are in µs. The fidelities are from Table 1 and are used only by the fidelity estimate.

| primitive | latency | fidelity | where | event(s) in the schedule |
|---|---|---|---|---|
| one-qubit gate | 5 | 0.999975 | Table 1 (Q) | `gate1` at a gate-zone position |
| two-qubit gate | **141 effective** (58 + 25 + 58: shift in, gate, shift out) | 0.9982 | Table 1 gives 25 (Q). The effective latency follows §2.1: "this implicit transport is accounted for in the gate's effective latency" (Q). The value 141 is **I**: both worked examples add up only with it | `gate` on two ions in neighbouring positions of one trap, one of them in a gate zone; `at` = the zone. 25 µs is kept as `tg`; §6 runs it |
| measurement | 120 | 0.9984 | Table 1 (Q) | not used: T_exe counts gates and transport only (§6.1, Q) |
| intra-trap shift | 58 per ion per position; a group (S3) of ions shifts together in 58 | 0.99978 | Table 1 (Q); group = one 58 µs step, from Fig. 7(c1), T=5→63 (I) | `split` of the ion onto the segment to the next position (29), then `merge` into it (29) |
| intra-trap swap | 200 | 0.99978 | Table 1 (Q) | both ions `split` onto their shared segment (100), then each `merge`s into the other's position (100) |
| inter-trap shift (JT-SIMD) | 250 per cycle, whatever the number of ions | 0.99956 | Table 1 (Q); per cycle, from Fig. 6 and Fig. 9 (I) | `hop` from an edge position through the junction node to the empty edge position of another arm. `meta["class"]` = direction in → direction out, e.g. `D->R` (the paper's DR-SH) |
| inter-trap swap | 500 | 0.99912 | Table 1 (Q) | not used by our schedules, and not expressible (§7) |
| coherence | T_coh = 600 s | | §6.1 (Q) | `fidelity()`: F_decoh = exp(−n·T/T_coh) |

**Why a shift is two events.** The core checker holds a `hop`'s source position until the hop ends, and
holds its destination from the hop's start. That makes the paper's lockstep group shift a capacity
violation: in Fig. 7, Q5 moves into the position Q4 is leaving at the same instant. The core checker
also has no event for two ions exchanging two positions. A split releases its position when it ends,
and a merge takes the next one when it starts. The two halves add up to the paper's latency. See §7.

## 3. Rules

| rule | statement | where | checked by |
|---|---|---|---|
| one ion per position | | §2.1 (Q) | core `capacity` |
| one ion through a junction at a time | (I) a JT shift moves only the source arm's edge ion | §3.1 | core `junction_mutex` |
| broadcast | one JT-SIMD class per cycle: every participating junction moves the same way, starting and ending together; a junction may sit a cycle out | §3.1 "At each timestep, only one JT-SIMD instruction can be issued" (Q); §2.1 (1)–(2) (Q) | core `broadcast` (`hop`s that start together share one class and one end) |
| a hop is a legal JT move | edge position → junction → edge position of another arm; the class is the one those two arms define; 12 shift classes, no U-turns | §3.1, Fig. 3(b) (I) | `simd_rules` `inter` |
| inter-trap cycles are exclusive | cycles never overlap one another, and **no** other event overlaps a cycle | §2.1 (3) "intra-trap and inter-trap transport cannot proceed simultaneously" (Q). Gates are included because §5.2 waits "for all in-flight intra-trap operations" (Q), and Figs. 6(b2) and 6(c2) start a cycle only after the gates end (I) | `simd_rules` `modes` (`Rules.inter_exclusive = "all"`) |
| intra-trap transport is independently addressed | several S3 groups and swaps at once, in different directions, within and across traps, concurrently with gates | §1, §3.1 (Q); Figs. 6–7 run TG‖SH‖SW (I) | (permitted) |
| half-steps are honest | a split is followed at once by the merge into the neighbouring position of the same trap; two ions share a segment only as one swap | representation (§2) | `simd_rules` `intra` |
| gates in gate zones | a 2Q gate on adjacent ions with one in a zone; a 1Q gate in a zone | §2.1, Fig. 2(a) (Q) | core `positions` (adjacency) + `simd_rules` `zones` |
| a swap touches a gate zone | **not stated**; every swap the paper draws (Figs. 2(a), 6, 7) has one ion in a zone | **A** (conservative) | `simd_rules` `zones` (`Rules.swap_gz`, on) |
| one gate per trap at a time | **not stated**; the paper's traps have two zones | **A**: off. §6 runs it on | `simd_rules` `zones` (`Rules.one_gate_per_trap`) |
| durations | Table 1, effective 2Q latency | §2 above | core `durations` (`trapsimd_duration(tg)`) |
| circuit | every operation once, on its qubits, in an order the circuit allows | | core `circuit` (two-qubit pairs; `commuting` order, `multiset` for QAOA) + `simd_rules` `program` (exact per-qubit blocks: a run of CX gates in which a qubit keeps its role commutes, ZZ gates commute, a one-qubit gate is a barrier) |

## 4. Circuits (rebuilt: **not** the authors')

| benchmark | ours | 2Q gates (ours) | 2Q gates the paper implies (Fig. 9 F_2Q) | note |
|---|---|---|---|---|
| VQE-n | RY on every qubit, then CX(i, j) for every i < j in qiskit 'full' order, then RY | n(n−1)/2 | n(n−1)/2 | "standard full-entanglement ansatz" (Q). The rotation gates and reps are not stated (**A**: RY, reps=1) |
| QAOA-n | p = 1: H, then **ZZ on every pair** (one native 2Q gate each), then RX | n(n−1)/2 | n(n−1)/2 (44 at n=10, 434 at n=30: within one gate) | The text says random 3-regular graphs (Q), but the plotted counts are those of the complete graph (I). Table 3 is compared with the complete graph. A 3-regular variant (networkx seed 0, 3n/2 ZZ) is also run |
| BV-n | n−1 data qubits + 1 ancilla; X, H on the ancilla; H on the data; CX(data, ancilla) for each one in a secret with n/2−1 ones (seed 0); H on the data | n/2 − 1 | n/2 − 1 | measurement left out (T_exe has none, §6.1) |
| RCA-n | Cuccaro (quant-ph/0410184) on n = 2m+2 qubits, MAJ/UMA with the 6-CX Toffoli | 16m + 1 (465 at n=60) | 15n − 43 (857 at n=60) | **the paper's decomposition or width is not stated and differs**: our adder has 0.54× its two-qubit gates at n=60. The RCA comparison is weak |

## 5. Our compiler

`schedule_ours` works in two steps.

**The line.** Each program is laid on a line of ions. The line runs along a trail of traps: consecutive traps
meet at a junction, and the trail enters each trap at one end and leaves at the other. `trap_trail`
searches for the trail of ⌈n / per_trap⌉ traps whose junction turns need the fewest JT-SIMD classes.
On A-60 the trail is 15 traps (a down-right then up-left double staircase) and needs **5** classes.

**The rounds.** The line is then run in rounds. A pair of line neighbours meets around a gate zone: its two
ions shift there (order kept, all ions of the trap moving at once), the gate runs, and a swap
exchanges them.

- **VQE, QAOA.** `all_pairs_rounds` builds the odd-even transposition network. QAOA meets every pair of
  the round's parity and needs n rounds. In VQE a pair meets only when its lower qubit has received
  every CX it is the target of; this needs 2n−3 rounds, the dependency depth. The VQE schedule is
  also legal in **strict** program order: it does not rely on CX commutation.
- **BV, RCA.** `lnn_rounds` is a first-come-first-served line router: a ready gate runs when its
  qubits are neighbours, otherwise each qubit steps toward the other by an exchange. It runs on
  `line_order`: the ancilla then its partners for BV, and Cuccaro's (cin, b0, a0, b1, a1, …) for the
  adder.
- **Sparse commuting circuits (3-regular QAOA).** Both the router and the odd-even network are tried.
  In the network, pairs with no gate only exchange. The network wins.

**Pairs across a junction.** When a pair straddles a junction, one ion is brought over: it walks to the
trap edge, and one 250 µs cycle per class carries all such ions at once. The direction is from the
fuller trap to the emptier one. On a tie it alternates from round to round, so a dense round uses one
direction and therefore one set of classes.

**Clocks.** Traps keep their own clocks. All of them stop for a cycle.

**Line density.** Every density of 3, 4 and 5 ions per trap is tried. Each schedule is judged by
`check_trapsimd`, and the fastest one that passes every check is kept.

The paper's initial mapping is unspecified, and so is ours: the line order is our choice.

## 6. Results

**Worked examples (exact).** Fig. 6 eager 982 µs and batched 732 µs; Fig. 7 depth-oriented 823 µs and time-sliced 545 µs (stamps 0, 5, 63, 204, 404, 545). These are the paper's numbers to the microsecond, with every configured check passing. With Table 1's bare 25 µs gate, the `durations` check fails on every 2Q gate of all four.

**Model level, Table 3's grid.** These are our schedules with the paper's FluxTrap 'Our T_exe'. T_inter is the summed inter-trap cycle time. F is our estimate under the paper's §6.1 formula. All 32 schedules pass every configured check. The circuits are rebuilt (§4): RCA has fewer two-qubit gates than the paper's adder.

| L | benchmark | 2Q gates ours / paper-implied | FluxTrap T_exe (ms) | ours T_exe (ms) | paper / ours | ours T_inter (ms) | cycles | F paper / ours | checks |
|---|---|---|---|---|---|---|---|---|---|
| 8 | QAOA-20 | 190 / 190 | 93.8 | 23.5 | 3.99x | 14.2 | 57 | 0.61 / 0.616 | all pass |
| 8 | RCA-20 | 145 / 257 | 70.6 | 62.7 | 1.13x | 10.8 | 43 | 0.57 / 0.651 | all pass |
| 8 | BV-20 | 9 / 9 | 8.4 | 4.1 | 2.02x | 0.2 | 1 | 0.97 / 0.961 | all pass |
| 8 | VQE-20 | 190 / 190 | 130.5 | 32.7 | 3.99x | 16.0 | 64 | 0.56 / 0.614 | all pass |
| 8 | QAOA-40 | 780 / 780 | 327.6 | 67.2 | 4.88x | 48.8 | 195 | 0.13 / 0.136 | all pass |
| 8 | RCA-40 | 305 / 557 | 180.9 | 148.8 | 1.22x | 38.2 | 153 | 0.28 / 0.397 | all pass |
| 8 | BV-40 | 19 / 19 | 16.2 | 9.0 | 1.80x | 0.8 | 3 | 0.94 / 0.92 | all pass |
| 8 | VQE-40 | 780 / 780 | 532.4 | 91.0 | 5.85x | 56.0 | 224 | 0.09 / 0.134 | all pass |
| 8 | QAOA-60 | 1770 / 1770 | 744.9 | 101.3 | 7.35x | 73.8 | 295 | 0.00891 / 0.0107 | all pass |
| 8 | RCA-60 | 465 / 857 | 279.0 | 208.6 | 1.34x | 41.2 | 165 | 0.14 / 0.245 | all pass |
| 8 | BV-60 | 29 / 29 | 23.6 | 13.9 | 1.70x | 1.2 | 5 | 0.92 / 0.881 | all pass |
| 8 | VQE-60 | 1770 / 1770 | 1,255.9 | 143.3 | 8.77x | 90.0 | 360 | 0.00333 / 0.0105 | all pass |
| 8 | QAOA-100 | 4950 / 4950 | 2,256.3 | 160.9 | 14.02x | 74.8 | 299 | 5.85e-07 / 3.11e-06 | all pass |
| 8 | RCA-100 | 785 / 1457 | 561.6 | 354.2 | 1.59x | 71.5 | 286 | 0.03 / 0.0906 | all pass |
| 8 | BV-100 | 49 / 49 | 38.7 | 23.6 | 1.64x | 2.2 | 9 | 0.86 / 0.806 | all pass |
| 8 | VQE-100 | 4950 / 4950 | 3,570.9 | 277.9 | 12.85x | 117.5 | 470 | 8.88e-08 / 3.04e-06 | all pass |
| 14 | QAOA-20 | 190 / 190 | 128.9 | 27.9 | 4.61x | 14.2 | 57 | 0.58 / 0.553 | all pass |
| 14 | RCA-20 | 145 / 257 | 85.7 | 74.4 | 1.15x | 10.8 | 43 | 0.56 / 0.612 | all pass |
| 14 | BV-20 | 9 / 9 | 11.7 | 4.8 | 2.45x | 0.2 | 1 | 0.97 / 0.961 | all pass |
| 14 | VQE-20 | 190 / 190 | 134.2 | 40.6 | 3.31x | 16.0 | 64 | 0.55 / 0.548 | all pass |
| 14 | QAOA-40 | 780 / 780 | 370.9 | 76.2 | 4.87x | 48.8 | 195 | 0.12 / 0.0839 | all pass |
| 14 | RCA-40 | 305 / 557 | 204.5 | 156.1 | 1.31x | 23.0 | 92 | 0.27 / 0.343 | all pass |
| 14 | BV-40 | 19 / 19 | 18.5 | 10.5 | 1.76x | 0.8 | 3 | 0.94 / 0.918 | all pass |
| 14 | VQE-40 | 780 / 780 | 784.3 | 108.1 | 7.25x | 56.0 | 224 | 0.07 / 0.082 | all pass |
| 14 | QAOA-60 | 1770 / 1770 | 822.6 | 115.0 | 7.15x | 73.8 | 295 | 0.00801 / 0.00349 | all pass |
| 14 | RCA-60 | 465 / 857 | 296.6 | 236.3 | 1.26x | 34.8 | 139 | 0.14 / 0.194 | all pass |
| 14 | BV-60 | 29 / 29 | 28.7 | 16.2 | 1.78x | 1.2 | 5 | 0.91 / 0.877 | all pass |
| 14 | VQE-60 | 1770 / 1770 | 1,551.4 | 169.7 | 9.14x | 90.0 | 360 | 0.00248 / 0.00335 | all pass |
| 14 | QAOA-100 | 4950 / 4950 | 2,568.1 | 195.8 | 13.12x | 74.8 | 299 | 3.51e-07 / 2.45e-07 | all pass |
| 14 | RCA-100 | 785 / 1457 | 572.6 | 396.2 | 1.45x | 57.8 | 231 | 0.03 / 0.0612 | all pass |
| 14 | BV-100 | 49 / 49 | 60.3 | 27.5 | 2.19x | 2.2 | 9 | 0.83 / 0.799 | all pass |
| 14 | VQE-100 | 4950 / 4950 | 4,329.2 | 343.0 | 12.62x | 117.5 | 470 | 9.58e-09 / 2.34e-07 | all pass |

**Sensitivities on A-60 (L = 8) and the QAOA variant.** Every one of these schedules also passes every check.

| run | ours T_exe (ms) | vs base | paper / ours | checks |
|---|---|---|---|---|
| VQE-60, L=8, base | 143.3 | 1.00 | 8.77x | all pass |
| VQE-60, L=8, one gate per trap at a time | 187.6 | 1.31 | 6.69x | all pass |
| VQE-60, L=8, bare 25-us 2Q gate | 129.7 | 0.91 | 9.68x | all pass |
| VQE-60, L=8, swaps outside gate zones allowed | 143.3 | 1.00 | 8.77x | all pass |
| VQE-60, L=8, gate zones at (1, 6) | 150.2 | 1.05 | 8.36x | all pass |
| VQE-60, L=8, strict program order | 143.3 | 1.00 | 8.77x | all pass |
| QAOA-60, L=8, base | 101.3 | 1.00 | 7.35x | all pass |
| QAOA-60, L=8, one gate per trap at a time | 125.3 | 1.24 | 5.94x | all pass |
| QAOA-60, L=8, bare 25-us 2Q gate | 94.4 | 0.93 | 7.90x | all pass |
| QAOA-60, L=8, swaps outside gate zones allowed | 101.3 | 1.00 | 7.35x | all pass |
| QAOA-60, L=8, gate zones at (1, 6) | 153.8 | 1.52 | 4.84x | all pass |
| BV-60, L=8, base | 13.9 | 1.00 | 1.70x | all pass |
| BV-60, L=8, one gate per trap at a time | 14.0 | 1.01 | 1.69x | all pass |
| BV-60, L=8, bare 25-us 2Q gate | 10.5 | 0.76 | 2.25x | all pass |
| BV-60, L=8, swaps outside gate zones allowed | 13.6 | 0.98 | 1.74x | all pass |
| BV-60, L=8, gate zones at (1, 6) | 15.4 | 1.11 | 1.54x | all pass |
| RCA-60, L=8, base | 208.6 | 1.00 | 1.34x | all pass |
| RCA-60, L=8, one gate per trap at a time | 207.7 | 1.00 | 1.34x | all pass |
| RCA-60, L=8, bare 25-us 2Q gate | 160.7 | 0.77 | 1.74x | all pass |
| RCA-60, L=8, swaps outside gate zones allowed | 196.8 | 0.94 | 1.42x | all pass |
| RCA-60, L=8, gate zones at (1, 6) | 257.0 | 1.23 | 1.09x | all pass |
| QAOA-60 3-regular (90 ZZ), L=8 | 97.7 | | (7.62x vs the paper's QAOA-60) | all pass |
| QAOA-60 3-regular (90 ZZ), L=14 | 111.1 | | (7.40x vs the paper's QAOA-60) | all pass |

## 7. Every assumption that moves a number

| assumption | ours | alternative | effect at A-60, L=8 (see the sensitivity table) |
|---|---|---|---|
| two-qubit latency | 141 µs effective: the worked examples only add up with it | Table 1's bare 25 µs (Table 3's choice is not stated) | ours drop 7–24 %; the comparison with Table 3 becomes MORE favourable to us |
| two gates at once in one trap | allowed (one per gate zone) | at most one gate per trap | VQE +31 %, QAOA +24 %, BV and RCA ≈ 0 |
| where the two gate zones sit | evenly spaced, (2, 5) | next to the trap ends, (1, 6) | VQE +5 %, QAOA +52 %, BV +11 %, RCA +23 % |
| a swap needs a gate zone | yes (every drawn swap has one) | anywhere | RCA −6 %, BV −2 %, VQE/QAOA 0 |
| gates during an inter-trap cycle | forbidden (Fig. 6 waits for gates) | allowed | not run: forbidding is the conservative side |
| CX commutation | runs of CX in which a qubit keeps its role commute | strict program order | VQE: none; the same schedule passes in strict order |
| circuits | rebuilt (§4) | the authors' (unreleased) | RCA has 0.54× the paper's two-qubit gates at n = 60, so its comparison is not like for like. QAOA uses the complete graph (the paper's gate count), not the 3-regular graph of its text |
| initial placement | ours (the line order) | the paper's mapping (unstated) | — |
| fidelity counting | Table 1, f_2Q = 0.9982, transport per ion moved, a swap once | per SIMD instruction; f_2Q = 0.99817 fits Fig. 9 | F only, not time. Our F is below the paper's on several cells (BV everywhere; QAOA at every size and VQE-20 at L=14): our line moves ions more often than it needs cycles, and every shift and swap is counted |
| measurement | not in T_exe | — | T_exe has none (§6.1) |

**Rules we looked for before believing the numbers.** Every one is either checked or written down
above.

- **Broadcast.** One class per cycle, at most one ion per junction, start and end together. Checked.
- **Exclusion.** Nothing overlaps a cycle, and cycles do not overlap one another. Checked.
- **Edges.** Edge positions and junction arms are legal. Checked.
- **Capacity.** One ion per position. Checked.
- **Gate zones.** Gates sit in gate zones, and swaps touch one. Checked.
- **Durations.** Durations follow the law. Checked.
- **Circuit.** The circuit is executed in a valid order. Checked.
- **Gates per trap.** Two simultaneous gates in one trap is the one permissive choice the paper leaves
  open. Turning it off keeps every Table 3 cell faster for VQE (6.7×) and QAOA (5.9×) at A-60.

## 8. What the core checker could not express (timed.py, models.py: not edited)

Each item below is a minimal example on three capacity-1 sites in a row, `P0 -a- P1 -b- P2`, under
`Profile("probe", broadcast=("hop",))`. `tests/test_repro_trapsimd.py::test_the_core_checker_gaps_and_what_covers_them`
replays each one: the core checker's verdict, and this module's.

1. **A lockstep group shift is a capacity violation when written as `hop`s.**
   - Events: `hop b P1→P2 [0,58)` and `hop a P0→P1 [0,58)`.
   - Result: "trap P1 holds 2 ions at t=0".
   - Cause: a hop holds its source position until it ends and its destination from its start.
   - This is the paper's S3 shift of Q4 and Q5 in Fig. 7.
2. **There is no event for two ions exchanging two positions** (an intra-trap or inter-trap swap).
   - Written as two crossing `hop`s, it fails `capacity`.
   - Written as a `swap` event, it fails `positions`: `swap` requires both ions in `at`, and it only
     reorders a chain inside one site.
3. **A `hop` is not checked against the graph.** `hop a P0→P2` (no edge, no path) passes.
4. **`broadcast` groups only by identical start time.** Two cycles of different classes, at 0–250 and
   100–350, both pass.
5. **There is no intra/inter exclusion.** A `gate1` at t=100, during a cycle at 0–250, passes.

**What this module does instead.**
- Intra-trap transport is written as half-steps (§2): a split releases its position when it ends.
- `simd_rules` adds the `intra` check (half-steps pair up; two ions share a segment only as one swap),
  the `inter` check (gap 3) and the `modes` check (gaps 4 and 5).

**The inter-trap swap is not used** by any schedule here, and it is not expressible (gap 2).

## 9. Files

- `examples/fig6_eager.schedule.json`, `fig6_batched`, `fig7_depth`, `fig7_sliced`: the worked
  examples, as `TimedSchedule.save` writes them.
- `ours/<bench><n>_L<L>.schedule.json`: our schedules for Table 3's grid.
- `ours/qaoa60_3reg_L*`: the QAOA variant on a 3-regular graph.
- `ours/<bench>60_L8_<variant>`: the sensitivities.
- `results.json`: for every run, the checks, violations, makespan, T_inter / T_intra, counts, the
  fidelity estimate, the comparison with Table 3, the line trail and the line densities tried.
