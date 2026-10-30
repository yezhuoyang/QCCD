# Goal 3: one code at a time, every architecture

**Question.** For each fixed QEC code, which trapped-ion architecture runs its memory experiment
fastest, and which with the lowest logical error rate, when every architecture may take any
size its rules allow?

Goal 1 asked for the best design over every code and every machine at once, which leaves too
many dimensions free to be a well-defined question. Goal 3 fixes the code and sweeps one axis,
the architecture. It is Fig. 3 of the QCCD Studio paper. (Goal 2, on the same Leaderboard, is the
verified resource estimate for Shor's algorithm.)

On qccd.academy it is the page `board/goal3/`, and each code is a board of the official server
(`goal3_<code>_mem@1`, [boards.py](boards.py)), so anyone can submit a design for it.

## The fixed codes

These are Alex's syndrome circuits. A code's experiment is its Z memory for d rounds, read out
in the program, every ion reset, then its X memory for d rounds, read out by the grader: one
program, one submission, and a round fails when either memory does. The CNOT order is the
circuit's own; the compiler never reorders it. The checks and the two fixes are in
[CODES.md](CODES.md).

| code | [[n,k,d]] | ions | schedule |
|---|---|---|---|
| surface 3 / 5 / 7 | [[d²,1,d]] | 17 / 49 / 97 | N-Z, hook-safe (circuit distance d) |
| colour 3 / 5 / 7 | [[(3d²+1)/4,1,d]] | 10 / 28 / 55 | off-the-hook, one auxiliary per plaquette (distance d−⌊(d+3)/6⌋) |
| BB 72 / 108 / 144 | [[72,12,6]] / [[108,8,10]] / [[144,12,12]] | 144 / 216 / 288 | Bravyi et al., depth 8 |
| HGP | pending (Alex) | | |

## The architectures

**Provisional until Ke's rules land**, except C2LR, which is the platform's swap-or-stay device
(2026-10-30). The definitions, and the sizes each family is tried at, are data in
[families.py](families.py). Every family runs on qccd-physics@2. C2LR adds only its own steps:
swap-or-stay at 400 µs and reorder-or-not at 93 µs, the paper's Yb-Ba row, because physics@2's
crystal is Ba-Yb. Its merge and split stay physics@2's, because the C2LR paper does not measure
them:

| family | device | sizes swept, for a code of N ions and A ancillas |
|---|---|---|
| Grid (baseline) | junction lattice, a trap per wire, direct wiring | every a×b, b ∈ {a−1, a}, with 1.1N to 3N traps |
| WISE | the same lattice, broadcast wiring, one movement class per cycle | as Grid |
| C2LR | the same lattice as a swap-or-stay device (`Machine.c2lr_grid`'s control and step prices, Yb-Ba, demonstrated diagonals only, [docs/c2lr.md](../../docs/c2lr.md)) | as Grid |
| Cyclone | one junction-free loop, moved rigidly | 2W ≈ 1.04N to 1.5N trap sites |
| Conveyor | a loop with dock spurs, rotated rigidly, ions docked to gate | 2W ≈ 1.3N to 1.6N slots, A or 4A/3 docks |

## A point, and what it is scored by

A point is one (family, size), graded on the code's release the way the official grader grades
a submission to it; only the Lean certificate step is left out ([sweep.py](sweep.py)):

1. qccd-physics@2 (Goal 1's cited Helios-class numbers);
2. the pinned reference compiler, qccdc b4a003dbb4ae;
3. the rules (`verify`);
4. **time per round**: the replayed program under the `quantinuum` table, divided by the 2d
   rounds of both memories;
5. **LER per round**: qccd-noise@2 sampled by stim with the release's budget and seed (matching
   for the surface code, BP-OSD otherwise), the grader's `ler_per_round`, which is
   1 − (1 − p_Z)(1 − p_X), with a 95% interval.

A size the compiler refuses, or that breaks a rule, is recorded with its reason and counted
under its family. Until 2026-10-30 the two memories were compiled and sampled apart; those rows
are in `results/separate/`, and they agree with the one-program points within sampling error
(surface d=3 on grid 5x5: the same 20.23 ms per round; 2.0e-4 and 2.2e-4 per round).

## The figure

For each code there are two panels: (a) time per round, (b) LER per round. The x axis is the
architecture family, with sizes smallest first; each family's best point is in colour and
labelled, and every other size is grey. Run [plot.py](plot.py) to get `figures/<code>.{png,pdf,svg}`,
`figures/overview.pdf` and `figures/summary.md` (the table behind the figure).

```
python -I goals/goal3/make_codes.py --builders <Alex's builders, patched>   # stim, qLDPC, igraph
python goals/goal3/boards.py                                                # the releases, once
QCCD_QCCDC=<pinned qccdc> python goals/goal3/sweep.py --code surface5 --workers 3
python goals/goal3/plot.py                                                  # matplotlib
```

The site page draws the same points live, with the official entries on each code
(`qccd/site/goal3.py`; the server's `GET /official/v1/goals/goal3`).

## Who does what (10/26 plan)

- architectures and their rules: Ke
- codes: Alex
- CNOT order: Keyi
- ion shuttling: John
- noise model and decoding: Zhuangzhuang, Jaewon, Yuewen
- site and AI assistance: John, Bingran

## Open

- **Cyclone is not compiled as Cyclone.** qccdc's rigid-rotation pass only applies to a loop with
  docks. On a junction-free loop it falls back to the race-track pass, which moved surface5 with
  0 loop shifts and 7,418 one-ion steps. Cyclone's points therefore measure the missing
  scheduler, not the architecture: they are an upper bound on its time and LER until a Cyclone
  schedule (ancillas rotating past the data, as in Khan et al.) exists. This is the ion-shuttling
  part of the plan (John).
- **C2LR has no router.** A C2LR device moves crystals only by swap-or-stay steps. The reference
  compiler emits plain shuttles, which the device does not declare (R4), and it parks two
  crystals in one leg outside a swap-or-stay enter (C1). So every C2LR size fails until a router
  emits the steps. The proposal's c2lr-router is not available: its author has not been named or
  sent it. Its quoted BB144 round of 160 ms also uses half the paper's step time (1/Γ). When a
  router's programs arrive, they are graded by this same pipeline. Until 2026-10-30 the family
  was a grid with C2LR wiring; those rows gave Grid's numbers exactly and are kept in
  `results/provisional/`. The official server's release carries physics@2 without the C2LR
  steps' price, so a C2LR entry needs a new release once a router exists.
- **WISE equals Grid today.** Its control block reaches the device (32 broadcast DACs), but the
  compiler emits the same program and the replay charges the same time. Even the DAC metric is
  computed from the geometry, not from the wiring block. Nothing enforces WISE's single
  broadcast class beyond rule R4. The difference has to come from Ke's broadcast rules, plus a
  scheduler that uses them.
- **A compiler fault on the largest grids.** surface7's Z memory compiled alone on 11x11, 12x11
  and 12x12 (Grid and WISE) with the pinned qccdc b4a003dbb4ae gives a program that breaks R1: a
  trap holds 3 ions against a capacity of 2, six times. The releases' one-program memory (Z then
  X) compiles on the same three devices without breaking a rule, so the figure has these sizes.
  The fault is still the compiler's: [r1_fault.py](r1_fault.py) reproduces it in about 7 minutes
  (grid11x11: 6 violations at sites T3_7h and T7_6v), and `results/separate/surface7.jsonl`
  records the six failed rows.
- **HGP code:** pending from Alex.
- **Colour-code decoder:** the sweep decodes the colour code with generic BP-OSD. A colour-code
  decoder (e.g. Chromobius) would be fairer before the codes are compared with each other
  (noise and decoding: Zhuangzhuang, Jaewon, Yuewen). Architectures are still compared fairly
  within a code.
- **Code sizes for the target LER < 10⁻⁵:** under qccd-noise@2 only surface d=7 reaches it so
  far (Grid 12x11: 4.1e-6 per round, 95% interval up to 5.4e-6). Colour d=7's best is 1.2e-4,
  so the colour code needs d > 7, or a colour-code decoder.
- **Certificates:** the sweep skips the Lean step. A family's best point should be submitted
  through the full grader before the paper quotes it.
