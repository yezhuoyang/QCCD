<div align="center">

# QCCD

**A fault-tolerant algorithm and QCCD architecture codesign tool**

[![python](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![dependencies](https://img.shields.io/badge/dependencies-none-lightgrey.svg)](#install)

</div>

In a QCCD machine, ions are transported between traps so that they can be brought together
for gates. Each trap holds only a limited number of ions, so an occupied trap can obstruct
the ions behind it. Each junction an ion crosses adds motional heating. And because a single
broadcast waveform drives every site connected to it identically, an operation as simple as
moving two neighbouring ions past one another may not be realisable on the hardware at all.

Which architecture performs best is therefore not self-evident: the answer depends jointly
on the error-correcting code, on the transport schedule, and on the control wiring. This
project provides a platform for answering that question quantitatively. A device is
described declaratively, a program is written or compiled for it, and the tool then replays
the program, evaluates twenty-three hardware rules against it, reports its cost, and
derives the electrodes it would take to build.

## Install

No dependencies — the toolchain is pure standard-library Python.

```bash
git clone https://github.com/yezhuoyang/QCCD.git && cd QCCD
python -m qccd devices          # every architecture in arch/, and what it costs to wire
```

## Use it

Every command is self-contained: no server, no build step, no network. Pages are written
to `out/` and opened in a browser.

| | |
|---|---|
| `python -m qccd studio` | **the design tool**, as one page → `out/studio.html` |
| `python -m qccd demo` | every device × program, rendered → `out/index.html` |
| `python -m qccd devices` · `show <device>` | the architectures, and one in detail |
| `python -m qccd run ring144_24v --program deck --html out/r.html` | replay, rule-check, price, render |
| `python -m qccd reach <device>` | what a machine can do before any program exists |
| `python -m qccd analyses` · `sweep <analysis> <device> <knob> <values>` | the knobs, and one curve |
| `python -m qccd phys <device> [--svg f.svg] [--html f.html]` | the electrodes it implies, and their design rules |
| `python -m qccd gds <device> -o x.gds` | the same metal as GDSII, for a fab tool |
| `python -m qccd open <export>` | re-verify a design the browser produced |
| `python -m qccd regen` | rebuild every emitted page |

Start with `python -m qccd studio` and open `out/studio.html`: drag a trap, draw a segment,
and the cost model and all 23 rules re-evaluate as you go.

`run` exits non-zero when a rule fails, and on the shipped schedule two do — under the
default cost model the deck's own program blows its heating budget, because it schedules no
cooling anywhere. That is a result, not a broken install; add `--model deck` to reproduce
the artifact's own figures instead (397,184 cost / 8,808 steps, all rules pass).

## 1 · Design an architecture

![designing a device](docs/img/design.gif)

A device is a **graph of traps and the segments between them**. Nothing declares "this is a
junction": attach a spur to a rail node and it *becomes* degree 3, and the cost model finds
that out by counting. Above, one parameter — the number of vertical shuttling lines — trades
ancillas against junctions.

Three ways in, all producing the same object:

```python
from qccd import Machine

m = Machine.ring(width=72, height=2, verticals=24, name="my_ring")   # a generator
#   Machine.grid(9, 9)                              …or a lattice
#   Machine.load("arch/ring144_24v.arch.json")      …or a document on disk

m.set_zone("data", capacity=4)                          # retune the device
m.set_curve("shuttle_segment", [(5, 0.10), (12, 1.0)])  # its (µs, quanta) physics
m.save("arch/my_ring.arch.json")
```

- **Python** — `qccd.Machine`, above. See [examples/program_in_python.py](examples/program_in_python.py).
- **A document** — [`arch/*.arch.json`](arch/), the architecture description language ([docs/adl.md](docs/adl.md)).
- **A browser** — `python -m qccd studio -o out/studio.html` opens a design tool as one
  self-contained page: drag traps, draw segments, write the program. `python -m qccd open
  my_design.qccd.json` brings it back for the full verifier.

Then program it. Space is node ids, time is cycles, and **one cycle is one machine step**:

```python
p = m.program("one_turn")
p.init({f"d{i}": f"S{i}" for i in range(144)})
p.rotate(+13)                                      # one template, every ion
```

## 2 · Evaluate it

![evaluating a design](docs/img/evaluate.gif)

`run` replays the program instruction by instruction, charges every hop to a cost model,
and reports what the [23 rules](docs/rules.md) say about it:

```python
r = m.run(p)                          # replay + every rule + the objective
r.cost, r.steps, r.runtime_ms, r.rules_failed
m.render(p, "out/my_ring.html")       # one self-contained, animated page
```

A rule reports *passed* only if the check actually ran — otherwise it says `skipped`, with
the reason. And the rules bite:

```python
bad = m.program("squeeze")
bad.init({"d0": "S0", "d1": "S1", "d2": "S143"})
with bad.cycle("shuttle") as c:                    # one machine step
    c.move("d1", "S1", "S0")
    c.move("d2", "S143", "S0")

m.run(bad).rules_failed
# ['R1', 'R11', 'R2', 'R4']
#  [R1] site S0 holds 3 ions but capacity is 2
#  [R2] 2 ions cross junction S0 in one cycle
#  [R4] channel 'linear_h.all.0' drives 168 sites with one waveform, but they are
#       asked to do 2 different things (L0:+1 and L0:-1); that needs 2 channels
```

Beyond a single run, `python -m qccd sweep` varies one design knob and prints the curve —
the question an architect actually asks:

```console
$ python -m qccd sweep budget ring144_24v scale.junction 0.25,0.5,1,2
budget on ring144_24v: scale.junction over 4 settings
scale.junction     total_error   heating_error     floor_error  dominant_share
------------------------------------------------------------------------------
0.25                  671.7777        670.1880          1.5898          0.4381
0.5                   965.3922        963.8025          1.5898          0.6093
1                    1552.6212       1551.0315          1.5898          0.7572
2                    2727.0792       2725.4895          1.5898          0.8618
```

Junction heating is 76% of the summed gate error at nominal — which is what makes the
number of vertical shuttling lines a design decision rather than a detail. See
`python -m qccd analyses` for the rest, and `python -m qccd reach <device>` for what a
machine can do before anyone writes a program for it.

## 3 · Compile a circuit for it

The programs above are written by hand. [`Compiler/`](Compiler/) takes an **OpenQASM 2.0
circuit and any architecture in [`arch/`](arch/)** and produces a TSIR program the verifier
accepts — choosing which ion carries which qubit, routing the ions so that every two-qubit
gate finds its operands in one trap, and decomposing each gate into the native trapped-ion
pulses `R θ φ`, `VZ λ` and `MS θ`.

```bash
cd Compiler/ocaml && source ./ocamlenv.sh && dune build      # OCaml 5.3 / dune 3.19
cd .. && ocaml/_build/default/bin/qccdc_cli.exe compile examples/steane_esm.qasm \
    --arch build/grid9x9.expanded.json -o build/out/steane
python bridge/check_tsir.py build/out/steane.tsir.json --arch arch/grid9x9.arch.json
python bridge/mk_qcheck_input.py build/out/steane \
    --arch build/grid9x9.expanded.json -o build/qc_steane.json
python bridge/check_cert.py build/out/steane --qasm examples/steane_esm.qasm \
    --arch arch/grid9x9.arch.json --qcheck build/qc_steane.json     # R10
```

The split of languages is the point. Search is untrusted and lives in **OCaml** (5,300
lines): placement, a space-time A-star router over a reservation table, gate decomposition.
Everything it does it must *justify*, by emitting a certificate alongside the program —
which qubit went to which ion, where every ion was at every cycle, and which circuit
operation each pulse group realises. Checking that certificate is **Lean 4**: 1,112 lines
and 31 theorems with no `sorry`, of which the trusted checker itself is 712.

### R10, the rule that was always skipped

Of the [23 rules](docs/rules.md), twenty-two are structural and the platform has always
checked them. R10 — *"the compiled program implements the input circuit"* — has only ever
been reported **skipped**, never `passed`. Run the verifier on anything and it says so
itself, and says why:

```console
skipped R10: needs symbolic permutation + Pauli-frame tracking against a QASM DAG
```

It is now checked, in two halves:

- **transport** — `QCCDC.Cert.check` is defined as `decide (Implements inp)`, where
  `Implements` is a *proposition* saying what it means for the program to realise the
  circuit: operands co-located at a trap that can gate, every move a hop the **architecture**
  admits, every op witnessed exactly once, dependent ops in program order. Soundness is
  then `of_decide_eq_true`, so all the content sits in a statement a reader can judge rather
  than in a pile of `Bool`s with a meaning asserted elsewhere. Ten further theorems prove
  each named way of being wrong is one `check` *cannot* accept — a dropped gate, a teleport,
  an aliased qubit, a reordering — and `bridge/mutate_cert.py` injects all ten into real
  compiled certificates to confirm the checker rejects them in practice too.
- **semantics** — the pulses are composed back into a stabilizer tableau (or, for small
  non-Clifford circuits, an exact unitary) and compared against the QASM. This reads the
  emitted program, not the compiler's claims about it.

The facts the checker judges against — which traps can gate, which pairs are one cycle
apart, the cyclic order of each loop — are re-derived from the architecture document by
code the compiler never runs. A compiler cannot widen the machine by asserting it.

### What it compiles

Nine example circuits × the nine reference architectures:

```console
$ cd Compiler && python bridge/run_matrix.py
81 (circuit, architecture) pairs
   72 fully verified   -- compiled, all rules pass, R10 passed
    9 out of reach     -- device too small, or the heuristic router declined
    0 DEFECTS          -- a rule violated or R10 refused
```

The nine out of reach are honest failures, not silent ones: `stationary_chain` has two
traps, and the router declines rather than emitting something illegal.

**`BB [[144,12,12]]` on `ring144_24v`** is the case worth naming, because it needed a second
router. Moving ions one at a time runs out at **46.2% loop occupancy** — past half full on a
ring, a path across the device is a traffic jam no amount of replanning clears — and the
round needs 53.8%. Rigid rotation has no such limit: one `loop_shift` template advances
*every* ion at once, so occupancy never changes. `compile` tries the general router first
and rotation only once it has declined, so it can add programs that compile but never change
one that already did.

| `BB [[144,12,12]]` ESM round | general router | rigid rotation | shipped Python pipeline |
|---|---|---:|---:|
| hops | *unroutable* | **776** | 2,672 |
| batches | — | 546 | **396** |

Fewer hops, more cycles — a different trade, not a free win. All applicable rules pass and
R10 is `passed`, the proved checker deciding a 1,008-witness certificate in about five
minutes.

Two of those results are ones the checker found rather than testing: the first rotation
compiler emitted the ancilla Hadamards as pulses but never *witnessed* them (invisible to a
tableau, which composes from what was emitted), and its scheduler reordered commuting `cx`
gates, which the order rule rejected outright. The fix for the second was not to relax the
rule but to prove the exemption — `Cert/Commute.lean` proves two `cx` gates commute when
they share a control with distinct targets, or a target with distinct controls, and exhibits
a state where a control/target chain does not.

### Watching one run

A hand-written program answers *what is executing?* with the instruction. A compiled one
has a second answer, and it is the one that makes the page a debugger: **which statement of
your circuit that instruction is discharging** -- or, while the machine is only shuttling,
which statement it is travelling towards. Pass `--qasm` and the page carries both listings
and steps them together; click a statement to jump to the instruction that discharges it.

```bash
cd Compiler
python bridge/render.py build/out/steane.cooled.tsir.json \
    --arch arch/grid9x9.arch.json --qasm examples/steane_esm.qasm \
    -o ../out/compiled/steane.html

cd .. && python -m qccd studio \
    --tsir Compiler/build/out/steane.cooled.tsir.json \
    --qasm Compiler/examples/steane_esm.qasm -o out/studio.html
```

The correspondence is checked before it is drawn. Every instruction carries the circuit
operations it serves, stamped as the compiler emitted it; the certificate's gate witnesses
-- the ones the Lean checker decides -- are then used to verify those stamps, and a
disagreement refuses the page rather than illustrating it.

Full design notes, the SAT routing oracle, and the measured (runtime, error) frontier:
[Compiler/PLAN.md](Compiler/PLAN.md) · [Compiler/README.md](Compiler/README.md).

## 4 · Derive its electrodes

A device plus a **technology file** determines the metal. Nothing is authored: no
`.arch.json` changes, no field is added to a trap. `qccd/phys/` derives integer-nanometre
polygons, solves the RF pseudopotential in closed form, and writes GDSII and SVG from one
shape table.

```console
$ python -m qccd phys ring144_24v
ring144_24v  [eth_junction_2201.12579]
  1372 polygons from 15 cells placed 192 times
  die 16.215 x 0.705 mm
    dc_pad           990
    naive_crossing   46
    rail             336
  ...
  rf_dc_clearance    66     ← the 24 dock spurs do not fit between the two rails
```

Every dimension in the technology file carries a page reference. Fed only the two RF widths
that arXiv:2201.12579 publishes, the solver reproduces that paper's own naive-junction
measurements — an **86.5 µm** transport path against their 84, and a confinement 30% of the
linear section — which turns the RF-barrier argument behind the junction cost from a
citation into a checked number. It also finds that **no shipped device sits at its design
ion height**: neighbouring metal moves it by up to 15%. [docs/phys.md](docs/phys.md).

`--html` puts the metal under the schematic as a true-to-scale backdrop. It does not
register with the diagram above it and it is not meant to: the schematic is stretched to
fit the page and the electrodes are not, so the page draws a scale bar and says which is
which.

## The gallery

Every clip below is generated from the checked-in architecture by
`python tools/make_gif.py --all`, through the same layout and replay code that renders the
interactive pages. Pale dots are empty traps, gold squares are junctions, dark blue dots
are data ions and indigo dots ancillas; pink segments are the data region, green the
computing region and blue the shuttling highways. (The `ring144_24v` clip replays a
schedule imported from a third-party artifact that is not in the repo; without it that one
clip is skipped and the rest still build.)

### Rotation loops

**`ring144_24v`** — the shipped 24-ancilla design: a 2×72 rotation loop with 24 dock spurs.
The spurs are what put a junction on every rigid hop. Replaying its schedule reproduces the
artifact exactly: **397,184 cost / 8,808 steps**, all rules pass.

![ring144_24v](docs/img/ring144_24v.gif)

**`cyclone_base`** — 72 traps on one loop, ancillas in line, so **no junction sits on the
rotation path**. One instruction turns the whole register: 1,296 cost / 18 steps.

![cyclone_base](docs/img/cyclone_base.gif)

**The same realignment by odd-even sort**, for contrast — 143 instructions of pairwise
swaps: 3,888 cost / 284 steps. Rigid rotation wins by 3× in cost and nearly 16× in steps.

![odd-even sort](docs/img/cyclone_oddeven.gif)

**`cyclone_dual_loop`** — one data loop and one ancilla loop, concentric. The data loop
holds still while the ancilla loop turns past it; two turns finish the syndrome extraction.

![cyclone_dual_loop](docs/img/cyclone_dual_loop.gif)

**`h2_racetrack`** — Quantinuum H2, a linear trap with periodic boundary conditions. One
continuous RF null, so the curved ends are ordinary conveyor regions and the device has **no
junctions at all**.

![h2_racetrack](docs/img/h2_racetrack.gif)

### Grids and rails

Identical geometry, different wiring — and the wiring is the whole cost. Both are 225 nodes,
144 traps, 77 junctions; `grid9x9` drives every electrode directly, `deck_unit_cell`
broadcasts in groups behind a demux.

<table>
<tr>
<td width="50%"><img src="docs/img/grid9x9.gif" alt="grid9x9" width="100%"><br>
<b>grid9x9</b> — baseline grid QCCD, a trap in the middle of every wire.<br><b>5,760 DACs</b>, direct.</td>
<td width="50%"><img src="docs/img/deck_unit_cell.gif" alt="deck_unit_cell" width="100%"><br>
<b>deck_unit_cell</b> — the same lattice, 24 electrodes per cell in three classes.<br><b>44 DACs</b>, broadcast.</td>
</tr>
</table>

**`ladder_2x72`** — rails and highways: two 72-slot rails joined by rungs (the computing
region, green), plus top and bottom shuttling highways (blue) an ion can be ejected onto,
run along, and re-inserted from.

![ladder_2x72](docs/img/ladder_2x72.gif)

### Baselines

**`chain72`** — the unrolled ring: the same 144 ion slots with no loop, no spur and no
junction. The control the ring's topology is measured against.

![chain72](docs/img/chain.gif)

**`stationary_chain`** — one trap, no transport: the degenerate case the platform has to
express without special-casing, and the baseline that already demonstrated break-even.

<img src="docs/img/stationary_chain.gif" alt="stationary_chain" width="360">

### Side by side

| device | nodes | traps | junctions | DACs | wiring | program | cost | steps |
|---|---:|---:|---:|---:|---|---|---:|---:|
| ring144_24v | 168 | 168 | 24 | 46 | `wise` | deck schedule | 397,184 | 8,808 |
| cyclone_base | 72 | 72 | 0 | 38 | `broadcast_groups` | rotate ×18 | 1,296 | 18 |
| cyclone_base | 72 | 72 | 0 | 38 | `broadcast_groups` | odd-even ×18 | 3,888 | 284 |
| cyclone_dual_loop | 144 | 144 | 0 | 44 | `broadcast_groups` | rotate ×18 | 1,296 | 18 |
| h2_racetrack | 40 | 40 | 0 | 72 | `broadcast_groups` | rotate ×10 | 400 | 10 |
| ladder_2x72 | 288 | 288 | 46 | 56 | `wise` | walk ×20 | 1,284 | 55 |
| grid9x9 | 225 | 144 | 77 | 5,760 | `direct` | walk ×8 | 184 | 16 |
| deck_unit_cell | 225 | 144 | 77 | 44 | `wise` | walk ×8 | 184 | 16 |
| chain72 | 72 | 72 | 0 | 1,728 | `direct` | walk ×12 | 720 | 60 |
| stationary_chain | 2 | 2 | 0 | 48 | `direct` | walk ×1 | 1 | 1 |

`wise` and `broadcast_groups` are both broadcast schemes: the DAC count stays flat as the
array grows, and only the per-trap compensation electrodes scale. `direct` pays one DAC per
electrode — which is why the same 144-trap lattice costs 5,760 DACs or 44.

Reproduce the whole table with `python -m qccd devices` and `python -m qccd demo`, which
renders every device to `out/index.html`.

## What is in the box

| | |
|---|---|
| [`qccd/arch/`](qccd/arch/) | the architecture description language and its generators |
| [`qccd/ir/`](qccd/ir/) | TSIR, the control IR a hardware program is written in |
| [`qccd/verify/`](qccd/verify/) | the replay and the 23 rules, as machine-checkable invariants |
| [`qccd/cost/`](qccd/cost/) | the objective: combinatorial (steps, templates) and physical (µs, quanta) |
| [`qccd/compile/`](qccd/compile/) | placement, ordering, cooling insertion, the program builders |
| [`qccd/viz/`](qccd/viz/) | the renderer, and the browser design tool it emits |
| [`qccd/phys/`](qccd/phys/) | the electrodes a device implies: polygons, the RF field, GDSII |
| [`arch/`](arch/) | nine reference architectures, every one from a published design |
| [`examples/`](examples/) | runnable studies — routing benchmarks, heating budgets, `BB [[144,12,12]]` |
| [`Compiler/`](Compiler/) | QASM → TSIR: OCaml search, Lean-verified checker, the R10 decision procedure |
| [`tools/make_gif.py`](tools/make_gif.py) | the clips above (needs Pillow: `pip install pillow`) |

**Docs** — [design plan](docs/PLAN.md) · [architecture language](docs/adl.md) ·
[control IR](docs/tsir.md) · [the rules](docs/rules.md) ·
[the electrodes](docs/phys.md) · [the compiler](Compiler/PLAN.md)

## Where this is going

- **Near term** — for `BB [[144,12,12]]`, which architecture is best? The compiler makes
  that a measurement rather than an argument: same circuit, every device, verified output.
- **Long term** — which code *and* architecture together give the cheapest demonstration
  of break-even?

Contributions welcome: a new architecture is a `.arch.json` in [`arch/`](arch/), a new
routing strategy is a builder in [`qccd/compile/programs.py`](qccd/compile/programs.py), and
a new claim about hardware is a rule in [`qccd/verify/rules.py`](qccd/verify/rules.py) with a
test that breaks it. Run the suite with `python -m pytest`.

## License

[MIT](LICENSE)
