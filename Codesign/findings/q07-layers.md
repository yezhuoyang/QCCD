# 0.7 · Does a machine built in layers beat a planar one? Where transport is the time, yes

**Status:** answered for one machine family and one router; open for the site's compiler.
**Verdict:** a layer is worth what its broadcast is worth. On the BB [[144,12,12]] board a
machine designed for layers runs the round in **20.27 ms on three layers, 24.11 ms on two and
31.59 ms in a plane**, where the board's leader takes 288.37 ms. So the third dimension is
worth **1.56×** there (1.43× against the planar machine's best variant, section 6), and the
architecture under it, which also exists in a plane, is worth
9×. Across the study three layers are worth 1.2× to 1.9× where transport is the time and 6 to
17 % where cooling is; on the small boards, nothing; and the site's compiler, unchanged, gains
nothing from stacked lattices.

```bash
python Codesign/scripts/q07_layers.py run qccdc     # E1
python Codesign/scripts/q07_layers.py run boards    # E2
python Codesign/scripts/q07_layers.py run goal3 --ler surface3 surface5 bb72   # E3
python Codesign/scripts/q07_layers.py table
python Codesign/scripts/q07_layers.py page --only bb144_three_layers     # a design page, 3-D stage on
```

Rows: [`../data/q07.json`](../data/q07.json). The layered machine model is
[`docs/PLAN-3d.md`](../../docs/PLAN-3d.md): planar layers joined by risers; a riser is priced,
drawn and driven as an ordinary shuttle; **each layer has a broadcast of its own**, so a cycle
carries one waveform a layer where a planar machine carries one.

---

## 1 · What a layer can and cannot give

A riser costs what a rail costs, so a layered machine differs from a planar one in two ways
only: its rail graph need not be planar, and it plays one waveform **a layer** in a cycle.
Every result below is one of those two things, measured.

The second is paid for in control lines. Each layer's broadcast has its own DACs: the machine
of section 3 needs 50 in a plane, 82 on two layers and 114 on three. The boards allow 128, so
at this size three layers is where the budget stops.

## 2 · E1: the site's compiler, unchanged, gains nothing

`qccdc` reads a third coordinate and routes through risers (PLAN-3d §7), so the first
experiment changes nothing but the machine: the Steane board's circuit on planar lattices and
on the same lattices stacked, a riser at every junction.

| machine | layers | transport cycles | T_jones |
|---|---:|---:|---:|
| grid5x5 (the board's reference lattice) | 1 | 52 | 15.29 ms |
| grid4x3 | 1 | 56 | 15.72 ms |
| grid3x3 | 1 | 63 | 16.93 ms |
| grid2x2 x 4 layers | 4 | 41 | 17.04 ms |
| grid3x2 x 2 layers | 2 | 43 | 17.15 ms |
| grid3x3 x 2 layers | 2 | 45 | 17.60 ms |

Stacking shortens the paths, and the cycle count falls by a fifth. The time rises. The
compiler still emits one waveform a cycle, so it never uses a second broadcast, and both ends
of a riser are junctions, so a trip between layers is two junction crossings where a trip
along a lattice edge is one. **A compiler that does not know about layers turns them into a
slower lattice.** That is a statement about `qccdc` as it is, not about what a layer-aware
router in it could do.

## 3 · E2: a machine designed for layers, on the Architecture boards

**The machine** (`qccd.arch.sandwich.conveyor_sandwich`). The data ions never move. They
stand in a ring of traps. The ancillas ride two conveyor loops, the X checks on one and the Z
checks on the other, with one slot facing every data trap and a rail from the slot to the
trap. A layer of CNOTs is then three movements, each one waveform however many ancillas take
part: the loop turns until every ancilla faces its partner, they all hop into the traps, they
all hop back. It is one graph built three ways:

- **three layers**: a loop below the data, a loop above, every slot-to-trap rail a riser;
- **two layers**: one loop in the data's plane, inside their ring, and one above;
- **one layer**: a planar machine, one loop inside the ring of data traps and one outside.

**Its router** (`qccd.compile.conveyor_router`) keeps the circuit's gate order, plays qccdc's
native pulses, and writes the same certificate every compiler here writes. It packs moves into
cycles by the verifier's own motion labels, so what it puts in a cycle is what R22 accepts:
one waveform, or one a layer.

### 3.1 BB [[144,12,12]] (`bb144@2`), by the board's own grader

The circuit numbers its qubits `6i + j` on the code's 12 × 6 torus, and every layer of its
CNOTs is one translation of the torus. With L and R data qubit k in trap k, a translation is
one loop offset, or two where it wraps a row: 9 visits a loop a round.

| build | DACs | transport cycles (turning + hops) | coolings | **T_jones** | against the leader |
|---|---:|---:|---:|---:|---:|
| the board's leader, `cyc100_h2` (qccdc, 196 traps) | 48 | 1,694 | 7 | 288.37 ms | 1 |
| sandwich, 1 layer | 50 | 213 (151 + 62) | 19 | **31.59 ms** | 9.1× |
| sandwich, 2 layers | 82 | 151 (93 + 58) | 19 | **24.11 ms** | 12.0× |
| sandwich, 3 layers | 114 | 118 (82 + 36) | 19 | **20.27 ms** | 14.2× |

All three are 216 to 220 nodes with 144 of them junctions (budget 200), and compile in under a
second (the leader's programme took 1,462 s). `grade(bb144@2, bundle, "reference")` passes
every stage for each of them: the bundle, the budget, the physics lock, the standard rule set
with no rule broken, the correspondence and binding of the certificate, the Lean re-check of
the certificate (`qcheck e8acd6640fd3`), the semantics check (a stabilizer-tableau comparison
with the board's circuit), and the metrics. The report says `eligible: true`. The same holds
for the twelve rows of section 3.2.

Since 2026-11-10 the three builds are entries of the BB board (`sandwich72_L3`, `sandwich72_L2`
and `sandwich72_L1` in the catalogue of `Codesign/scripts/bb_studio.py`), in a family of their
own and marked as compiled by their own router, with R10 decided by the proved Lean checker.
**None of them has been submitted to the official grading service**: the service in production
runs an image that predates machines built in layers and would refuse one.

Where the 20.27 ms go, and what each layer took away:

| | 1 layer | 2 layers | 3 layers |
|---|---:|---:|---:|
| loops turning | 15.85 ms (151 cycles) | 9.30 ms (93) | 8.20 ms (82) |
| hops between slot and trap | 8.69 ms (62) | 7.76 ms (58) | 5.02 ms (36) |
| cooling | 5.70 ms | 5.70 ms | 5.70 ms |
| gates and readout | 1.35 ms | 1.35 ms | 1.35 ms |

- **The second layer lets the two loops turn in the same cycle.** In a plane they take turns:
  151 turning cycles become 93.
- **The third layer makes every hop of a loop one motion.** In a plane a hop from the top row
  of a racetrack and one from the bottom row are two directions in the lab frame, so two
  waveforms; up a riser they are one. And a loop's hops no longer share a layer with the other
  loop's turning: 58 hop cycles become 36.
- Cooling, gates and readout are the same 7 ms on every build. They are a third of the
  three-layer time, which is why the next layer could not be worth as much.

**How much of 14× is the third dimension: 1.56×.** The planar sandwich is already 9.1× faster
than the leader. That part is the architecture and a router written for it: data that never
move, ancillas that move together. Nothing about it needs layers.

### 3.2 The four small boards: the layers change nothing, and the sandwich loses

| board | leader (one chain, nothing moves) | sandwich, 1 layer | 2 layers | 3 layers |
|---|---:|---:|---:|---:|
| `steane@1` | 9.79 ms | 14.77 ms | 15.12 ms | 14.53 ms |
| `rep9@1` | 2.91 ms | 12.92 ms | 12.72 ms | 12.72 ms |
| `five_qubit@1` | 9.16 ms | 11.01 ms | 10.90 ms | 10.72 ms |
| `surface17@1` | 11.67 ms | 15.91 ms | 16.21 ms | 15.60 ms |

Each board's leader is `stationary_chain`: every ion in one trap, no transport at all. There
is no transport for a layer to shorten, and a machine that moves ions cannot beat one that
does not. Between the sandwich's own builds the spread is 4 % at most, and two layers are
sometimes slower than one: with 2 to 4 ancillas a loop there is little for a second waveform
to do, and 13 to 20 coolings are a third or more of the time.

## 4 · E3: the same machine on Goal 3's codes

`goal3_point` scores a sandwich as `goals/goal3/sweep.py` scores a point: the code's release
(Z memory then X memory), qccd-physics@3, the cooling pass on the `helios` table, the replayed
time over the rounds run. Two things differ from a Goal 3 family and are not hidden: the rule
set is the site's `standard`, not a deck family's; and Goal 3 compares the transport deck's
three architectures, of which this is not one.

Cooling needs a reading. The Helios model's ground-state cooling takes 3 ms, and C2LR and WISE
pay it. Goal 3's Cyclone family does not: it reads the deck's S10 as sympathetic cooling in no
time. The sandwich is scored both ways, and each column is compared with the families that
share its reading.

### 4.1 Surface codes: a round that does not grow with the distance

The data are laid row by row along the ring with a spare trap after each row
(`conveyor_router.lattice_rows`). Every check then meets its four corners at the same four
offsets, the boundary checks included, so a round is four layers of gates at any distance:
four coolings, and turning that grows with d only.

Time per round, ms. Goal 3's fastest point of each family, then the sandwich:

| code | C2LR | WISE | sandwich, 3 ms cooling: 1 / 2 / 3 layers | Cyclone (free cooling) | sandwich, free cooling: 1 / 2 / 3 layers |
|---|---:|---:|---:|---:|---:|
| surface 3 | 18.2 | 35.6 | 17.9 / 17.9 / **16.8** | 9.2 | 5.9 / 6.2 / **5.0** |
| surface 5 | 53.7 | 71.9 | 19.1 / 18.6 / **17.6** | 31.3 | 7.1 / 6.6 / **5.8** |
| surface 7 | 78.5 | 140.2 | 20.3 / 19.4 / **18.4** | 73.2 | 8.3 / 7.4 / **6.5** |

At distance 7 the three-layer sandwich is 4.3× faster than C2LR on the same cooling, and 11×
faster than Cyclone on its cooling. The planar sandwich is nearly as fast. **With 3 ms
coolings, 12 of the 17 ms are cooling, and three layers are worth 6 to 10 % over one. With
free cooling they are worth 18 to 27 %.**

Logical error rate per round, sampled as the sweep samples it (qccd-noise@3, the release's
budget and seed), for the three-layer point with 3 ms cooling:

| code | sandwich, 3 layers | Goal 3's lowest (family, point) |
|---|---:|---:|
| surface 3 | 3.08e-4 [2.7e-4, 3.5e-4] | 2.76e-4 (WISE, wise_h3_4x4); 2.83e-4 (C2LR, Cyclone) |
| surface 5 | 6.97e-5 [6.1e-5, 8.0e-5] | 7.22e-5 (WISE, wise_h3_6x6); 7.27e-5 (C2LR) |

So the speed is not bought with errors: Goal 3's lowest rates lie inside these 95 %
intervals.

### 4.2 BB [[72,12,6]]

The circuit is Bravyi et al.'s depth-8 schedule on a 6 × 6 torus, six rounds of each memory.
Two placements were run: one data ion a trap (72 traps, so a visit makes a chain of two), and
the L and R ion of a position in one trap (36 traps and chains of three, as on bb144).

Time per round, ms:

| placement | C2LR | WISE | sandwich, 3 ms cooling: 1 / 2 / 3 layers | Cyclone (free cooling) | sandwich, free cooling: 1 / 2 / 3 layers |
|---|---:|---:|---:|---:|---:|
| one data ion a trap (144 junction nodes; 50 / 82 / 114 DACs) | 417.0 | 807.3 | 100.9 / 89.9 / **85.9** | 165.2 | 56.1 / 38.3 / **32.4** |
| two data ions a trap (72 junction nodes; 41 / 73 / 105 DACs) | | | 111.6 / 101.9 / 96.3 | | 38.6 / 26.1 / **20.5** |

On the same cooling the three-layer sandwich is 4.9× faster than C2LR. On Cyclone's cooling
it is 5.1× to 8.0× faster than Cyclone, and it is faster than Cyclone even while paying the
3 ms coolings Cyclone does not (85.9 against 165.2).

**This is where the layers are worth most: 1.73× and 1.88× with free cooling**, because
twelve rounds of two loops turning long distances are nearly all transport. With 3 ms
coolings they are worth 1.16× to 1.17×.

These points are not tuned. They take 15 to 25 coolings a round where the code needs 9 visits
a loop, and the router chooses between its placements, and between holding a loop for the
other and not, by a rough estimate (`conveyor_router._proxy`).

**Logical error rate.** The three-layer point with one data ion a trap and 3 ms coolings,
sampled as the sweep samples it (BP-OSD, 60,000 shots, 225 errors): **6.26e-4 a round
[5.5e-4, 7.1e-4]**. Goal 3's lowest are Cyclone's 8.43e-4 [7.3e-4, 9.7e-4] (`cyclone72_n2`),
C2LR's 1.44e-3 and WISE's 8.37e-3. The interval is just clear of Cyclone's, so this is the
lowest rate measured for the code here, on one sample of each.

### 4.3 Colour codes and the HGP code: the sandwich loses

| code | Goal 3's fastest (ms / round) | sandwich, 3 layers, 3 ms cooling | free cooling |
|---|---:|---:|---:|
| colour 3 | Cyclone 5.4 · C2LR 63.1 | 45.2 | 9.1 |
| colour 5 | Cyclone 30.5 · C2LR 73.0 | 147.9 | 32.4 |
| colour 7 | Cyclone 55.0 · C2LR 165.1 | 307.6 | 68.4 |
| HGP 58 | Cyclone 151.4 · C2LR 250.4 | 619.4 | 120.8 |

The sandwich needs the checks of a layer to be translations of one another along the ring.
The router is given qubit numbers, not a lattice, and for these circuits neither of its
placements finds that structure: a round of the distance-7 colour code takes 75 coolings
where the surface code takes 4. The colour code's bulk is translation-invariant, so a
placement that knew its lattice might do for it what `lattice_rows` does for the surface
code. That was not tried. The HGP code of a Hamming code has no such structure to find.

## 5 · What this does and does not show

1. **A layer helps when two streams of ions must move differently at the same time, and
   transport is the time.** That is the BB round on the sandwich: 1.56× from three layers on
   bb144, 1.7× to 1.9× on BB72 with free cooling, 1.2× to 1.3× on the surface codes.
2. **It does not help a compiler that emits one waveform a cycle** (E1) **or a circuit with no
   transport to save** (the small boards), **and it helps little where a round's time is
   cooling** (Goal 3 with 3 ms coolings: 6 to 17 %).
3. **The large factors against the leaderboards (9× to 14× on bb144, 4× to 5× on surface 7 and
   BB72) are the architecture and its router, and a planar machine has them too.** They say that these
   boards' leaders are far from what a machine built around the code can do. They are not
   evidence for the third dimension.

## 6 · Caveats

- **The router is the sandwich's own**, as Goal 3's three families have theirs. `qccdc` was
  not run on the sandwich. A board entry compiled by another compiler is graded on its bundle
  like any other, but the Architecture board's leader was compiled by `qccdc`, so this is a
  device and a compiler against a device.
- **The Goal 3 rows are scored without the Lean stage**, as the sweep scores a point. The
  board rows of section 3 passed it.
- **A chain of three on bb144.** A data trap holds its L and R ions and one visiting ancilla.
  R14 (an ion splits from the end of its chain) is not judged for the hop's class, which
  declares no split. Physically the visitor joined last and from the end it leaves by, so it
  is an edge ion; the rule as written cannot see that, and this is the same blind spot as
  issue #22. One data ion a trap needs 288 junction nodes, over the board's 200. Goal 3's
  BB72 point with one data ion a trap (section 4.2) has chains of two only.
- **A riser hop engages both layers' broadcasts.** This is the stricter reading of "each layer
  has its own broadcast", and it is why an X descent (up from below) and a Z descent (down
  from above) take two cycles. Under a reading in which a riser hop is driven from one side
  only, the three-layer times would be lower.
- **One reading of R23 was changed for layered machines.** `conveyed_paths` now makes the
  conveyor test a layer at a time, because a cycle on a layered machine is a waveform a layer
  and the instruction names one class for all of them. Without it a loop that only ever turns
  beside the other loop's hops is not recognised as a conveyor. A planar machine is judged as
  before (`tests/test_r23_junctions.py` passes unchanged). It wants a second pair of eyes.
- **The sandwich's wiring is assumed buildable.** The model prices a riser as a shuttle and a
  layer's broadcast as that layer's DACs. Whether 144 risers through a three-layer stack can be
  built is not something this study knows.
- **Two visitors a trap** (`_v2` rows: a data trap with room for an ancilla of each loop at
  once, so a chain of four). On two and three layers it changed nothing. In a plane it took
  2.5 ms off, to 29.08 ms, because hops of the two loops then share cycles; the cooling pass
  still cools before every batch of gates, so no cooling is saved. **Against that planar row
  the three layers are worth 1.43×, not 1.56×.** The tables above compare like with like, one
  visitor a trap.
