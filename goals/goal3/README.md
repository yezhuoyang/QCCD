# Goal 3: one code at a time, every architecture

**Question.** For each fixed QEC code, which trapped-ion architecture runs its memory experiment
fastest, and which with the lowest logical error rate, when every architecture may take any
size its rules allow?

Goal 1 asked for the best design over every code and every machine at once, which leaves too
many dimensions free to be a well-defined question. Goal 3 fixes the code and sweeps one axis,
the architecture. It is Fig. 3 of the QCCD Studio paper. (Goal 2, on the same Leaderboard, is the
verified resource estimate for Shor's algorithm.)

On qccd.academy it is the page `board/goal3/`, and each code is a board of the official server
(`goal3_<code>_mem@1`, BB72's `@2`, [boards.py](boards.py)), so anyone can submit a design for it. Those
boards still grade on the earlier physics; see "Open".

## The fixed codes

These are Alex's syndrome circuits, plus one HGP code of ours (Alex had none yet). A code's experiment is its Z memory for d rounds, read out
in the program, every ion reset, then its X memory for d rounds, read out by the grader: one
program, one submission, and a round fails when either memory does. The CNOT order is the
circuit's own; the compiler never reorders it. The checks and the two fixes are in
[CODES.md](CODES.md).

| code | [[n,k,d]] | ions | schedule |
|---|---|---|---|
| surface 3 / 5 / 7 | [[d²,1,d]] | 17 / 49 / 97 | N-Z, hook-safe (circuit distance d) |
| colour 3 / 5 / 7 | [[(3d²+1)/4,1,d]] | 10 / 28 / 55 | off-the-hook, one auxiliary per plaquette (distance d−⌊(d+3)/6⌋) |
| BB 72 / 108 / 144 | [[72,12,6]] / [[108,8,10]] / [[144,12,12]] | 144 / 216 / 288 | Bravyi et al., depth 8 (circuit distance 6 for BB72) |
| HGP 58 | [[58,16,3]], the Hamming [7,4,3] code's product with itself | 100 | Tremblay–Delfosse–Beverland coloration, X then Z, 7 layers each (circuit distance 3) |

Every code but BB108 and BB144 is a board. Those two are checked but neither released nor swept;
see "open".

## The architectures

Three, as the team's transport deck ("QCCD Transport Architectures", Ke, 2026-11-06) defines them:
their shuttling rules, hardware, parameters and cost models. [DECK.md](DECK.md) maps every item of
the deck to what happens to it here, and lists the readings taken where the documents leave a gap,
each open or answered. The definitions and
the sizes tried are data in [families.py](families.py).

| family | device | swept, per the deck | rule set | compiler |
|---|---|---|---|---|
| C2LR | Cx x Cy cells, an X-junction and two legs each, one crystal per leg, moved by swap-or-stay steps (Yb-Ba, the demonstrated diagonals) | Cx, Cy | `c2lr-demonstrated` | the baseline C2LR router |
| WISE | Cx x Cy cells, a junction zone and h gate zones each, sorted by parallel switched swaps, one kind of operation across the chip in a step | h, Cx, Cy | `wise` | the baseline WISE router |
| Cyclone | one ring of x traps of up to n ions, L-shaped junctions at its corners; data still, ancillas rotating in lockstep | x, n <= 30 | `cyclone-deck` | the baseline Cyclone router |

All three compilers keep the circuit's gate order. For Cyclone that departs from the deck's S6, which
assigns all X checks to one rotation and all Z checks to the next: Goal 3 runs one circuit per code on
every architecture (the project owner's decision, 2026-11-06). So Cyclone takes as many rotations a
round as the circuit's order needs, about 3 on surface d=3, and judges order by R25, not R26.

The earlier families, a plain Grid, a WISE that was the same grid with broadcast wiring, and a
Conveyor, are retired. Their rows are in `results/pre_deck/`.

## A point, and what it is scored by

A point is one (family, size), on the code's experiment from its release, with only the Lean
certificate step left out ([sweep.py](sweep.py)):

1. **the physics:** qccd-physics@3, the numbers of the team's Helios Nominal Noise Model (2026-11-05),
   with the deck's prices where the model has none: `qccd.arch.c2lr`, `qccd.arch.wise`, `qccd.arch.cyclone`;
2. **the compile**, by the family's own compiler, with the cooling pass on the `helios` table;
3. **the rules**, the family's own set, judged as the grader judges an entry's declared set. A program
   that breaks one is recorded and not sampled;
4. **time per round:** the replayed program divided by the 2d rounds of both memories;
5. **LER per round:** qccd-noise@3 sampled by stim with the release's budget and seed (matching for the
   surface code, BP-OSD otherwise), 1 - (1 - p_Z)(1 - p_X), with a 95% interval;
6. **the hardware census:** electrodes, DACs, switches, lasers and area, by the deck's formulas (`census` in each row).

A size a compiler refuses, or that breaks a rule, is recorded with its reason and counted under its family.

## The figure

For each code there are two panels: (a) time per round, (b) LER per round. The x axis is the
architecture family, with sizes smallest first; each family's best point is in colour and
labelled, and every other size is grey. Run [plot.py](plot.py) to get `figures/<code>.{png,pdf,svg}`,
`figures/overview.pdf` and `figures/summary.md` (the table behind the figure).

```
python -I goals/goal3/make_codes.py --builders <Alex's builders, patched>   # stim, qLDPC, igraph
python goals/goal3/boards.py                                                # the releases, once
python goals/goal3/sweep.py --code surface5 --workers 3                     # no qccdc: the routers are Python
python goals/goal3/rules.py --work goals/goal3/work                         # each point against its family's set
python goals/goal3/plot.py                                                  # matplotlib
```

The site page draws the same points live, with the official entries on each code
(`qccd/site/goal3.py`; the server's `GET /official/v1/goals/goal3`).

**Every point opens its own page.** A reference point opens its studio page: the ions moving on
its device, the program against the circuit, the rules, and the noise model's stim program
block by block. An official entry opens its page on the server. A size whose program broke a
rule has a page too, listed under "The sizes that failed". [pages.py](pages.py) builds the
pages from the sweep's `work/` folders. Each page replays its program, and the replayed time
must equal the results row. The pages are served at `board/goal3/p/<code>/<point>.html`.
[pages.json](pages.json), their index, is committed; the pages themselves are not (up to 45 MB
each). They are stored on the server only as `.html.gz` and served with nginx's `gzip_static`
(`deploy/official/README.md`, "Goal 3's point pages").

```
python goals/goal3/pages.py --out <dir> --work goals/goal3/work [<other trees' work> ...] --gzip
```

## Who does what (10/26 plan)

- architectures and their rules: Ke
- codes: Alex
- CNOT order: Keyi
- ion shuttling: John
- noise model and decoding: Zhuangzhuang, Jaewon, Yuewen
- site and AI assistance: John, Bingran

## Open

- **The official boards are not on this model yet.** `goal3_<code>_mem` releases still fix qccd-physics@2 and
  qccd-noise@2, and their physics lock has no way to give each family its own prices. They hold no entries.
  This sweep uses their circuits, detectors, budgets and seeds. Moving them means new releases, and a lock
  that recomputes a family's prices from its parameters rather than trusting a device's own.
- **[DECK.md](DECK.md), "Readings":** the 3 ms cooling, the GateSwap's error, and others are open. Each is one
  number here; changing one means sampling again. Two are answered (2026-11-07): the idle leakage rate is the
  report's, and crosstalk summed over the ions read out together is a simulation assumption, kept and so labelled.
- **Cooling decides much of the time comparison.** C2LR and WISE pay 3 ms for each batch of gates that needs it.
  Cyclone pays nothing, as the deck's S10 assumes. `results/variants/cool300us/` is the sweep at Studio's
  300 us for surface d = 3, 5, 7 and colour d = 3, 5.
- **The compilers are baselines.** WISE's router is greedy over the deck's step types and takes real steps rather
  than the paper's worst case. Cyclone's placement is annealed over one round. Neither is tuned, and C2LR's router
  still gives the same time on several sizes.
- **41 of the 189 pages animate only part of their program.** A page holds 20,000 frames, and most of WISE's
  programs on the larger codes are longer (BB72's reach 195,000 instructions). Such a page says "truncated", and its
  self-check covers the frames it animates. Its numbers are always the whole program's.
- **Certificates:** the sweep skips the Lean step. `Compiler/bridge/check_cert.py` accepts the certificates of all
  189 points for transport and for each qubit's gate order (run by hand on 2026-11-06; `tests/test_goal3_deck.py`
  keeps one point of each family). A family's best point should go through the full grader before the paper quotes it.
- **The HGP code is ours, not Alex's.** Alex had no explicit HGP construction yet, so Goal 3 took the textbook one
  on 2026-10-30, with the published coloration schedule ([hgp_code.py](hgp_code.py), [CODES.md](CODES.md)). Alex may
  replace it.
- **Colour-code decoder:** the sweep decodes the colour code with generic BP-OSD. A colour-code decoder (e.g.
  Chromobius) would be fairer before the codes are compared with each other. Architectures are still compared
  fairly within a code.
- **BB108 and BB144** are checked but not swept.
