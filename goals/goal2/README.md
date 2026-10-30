# Goal 2: one code at a time, every architecture

**Question.** For each fixed QEC code, which trapped-ion architecture runs its memory experiment
fastest, and which with the lowest logical error rate, when every architecture may take any
size its rules allow?

Goal 1 asked for the best design over every code and every machine at once, which leaves too
many dimensions free to be a well-defined question. Goal 2 fixes the code and sweeps one axis,
the architecture. It is Fig. 3 of the QCCD Studio paper.

## The fixed codes

These are Alex's syndrome circuits, each measured in a Z memory and in an X memory for d rounds.
The CNOT order is the circuit's own; the compiler never reorders it. The checks and the two fixes
are in [CODES.md](CODES.md).

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

A point is one (family, size), graded the way the grader grades a Goal 1 entry; only the Lean
certificate step is left out ([sweep.py](sweep.py)):

1. qccd-physics@2 (Goal 1's cited Helios-class numbers);
2. the pinned reference compiler, qccdc b4a003dbb4ae;
3. the rules (`verify`);
4. **time per round**: the replayed program under the `quantinuum` table, divided by the rounds,
   averaged over the two memories;
5. **LER per round**: qccd-noise@2 sampled by stim, matching for the surface code and BP-OSD
   otherwise, combined as 1 − (1 − p_Z)(1 − p_X), with a 95% interval.

A size the compiler refuses, or that breaks a rule, is recorded with its reason and counted
under its family.

## The figure

For each code there are two panels: (a) time per round, (b) LER per round. The x axis is the
architecture family, with sizes smallest first; each family's best point is in colour and
labelled, and every other size is grey. Run [plot.py](plot.py) to get `figures/<code>.{png,pdf,svg}`,
`figures/overview.pdf` and `figures/summary.md` (the table behind the figure).

```
python -I goals/goal2/make_codes.py --builders <Alex's builders, patched>   # stim, qLDPC, igraph
QCCD_QCCDC=<pinned qccdc> python goals/goal2/sweep.py --code surface5 --workers 3
python goals/goal2/plot.py                                                  # matplotlib
```

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
  router's programs arrive, they are graded by this same pipeline. Until 2026-10-30 the family was a grid with C2LR wiring; those rows gave Grid's
  numbers exactly and are kept in `results/provisional/`.
- **WISE equals Grid today.** Its control block reaches the device (32 broadcast DACs), but the
  compiler emits the same program and the replay charges the same time. Even the DAC metric is
  computed from the geometry, not from the wiring block. Nothing enforces WISE's single
  broadcast class beyond rule R4. The difference has to come from Ke's broadcast rules, plus a
  scheduler that uses them.
- **A compiler fault on the largest grids.** surface7's Z memory on 11x11, 12x11 and 12x12 (all
  three lattice families) compiles with the pinned qccdc b4a003dbb4ae into a program that breaks
  R1: a trap holds 3 ions against a capacity of 2, six times. A rule-breaking program from the
  reference compiler is the compiler's fault. These sizes count as failed until it is fixed; the
  reproduction case is `work/surface7/grid11x11/z/`.
- **HGP code:** pending from Alex.
- **Colour-code decoder:** the sweep decodes the colour code with generic BP-OSD. A colour-code
  decoder (e.g. Chromobius) would be fairer before the codes are compared with each other
  (noise and decoding: Zhuangzhuang, Jaewon, Yuewen). Architectures are still compared fairly
  within a code.
- **Code sizes for the target LER < 10⁻⁵:** surface and colour need d > 7 under qccd-noise@2.
- **Certificates:** the sweep skips the Lean step. A family's best point should be submitted
  through the full grader before the paper quotes it.
