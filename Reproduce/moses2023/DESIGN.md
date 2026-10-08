# Quantinuum H2 in our architecture language

S. A. Moses *et al.*, *A Race Track Trapped-Ion Quantum Processor*, Phys. Rev. X **13**,
041052 (2023), arXiv 2305.03828 (v2 read: `h2_benchmarking.tex` and the figure source
`h2_benchmarking_figure.pdf`). H2 is the one real machine among the papers we reproduce, so
this is a **design** reproduction: describe the machine faithfully, then compile the
circuits it published and compare the one compiler-level number the paper prints, the
number of two-qubit gate rounds (Table I, "H2-T1").

| file | what |
|---|---|
| `h2.arch.json` | the machine (built by `build_h2.py`, which also checks that it loads) |
| `build_h2.py` | every number of the document, with its source, in one place |
| `circuits.py` | takes the H2-T1 circuits from the paper's artifact and rewrites them exactly |
| `check_rewrites.py` | proves the rewrite exact: identities, and the artifact's own ideal outputs |
| `run_h2.py` | compiles every circuit with the published compiler, verifies, writes `results.json` |
| `results.json` | per-circuit verdicts, bounds, rule reports, the minimal compiler examples |

The artifact (github.com/Quantinuum/quantinuum-hardware-h2-benchmark, commit `d422a71`)
has no licence ("All Rights Reserved"). It is read where it was cloned and nothing of it
is in this repository; `results.json` names each circuit by the sha256 of its QASM.

## The machine at a glance

One closed loop `L0` of 59 sites and 59 rails, no junction (degree 2 everywhere), 77
qubits of capacity, positions in units of the 750 µm gate pitch:

```
          CL01 ... CL10        AUXU4 UG04 AUXU3 UG03 AUXU2 UG02 AUXU1 UG01 AUXU0        CR20 ... CR11
        /                      ------------------- top rail (y = 1) -------------------                 \
    LOAD                                                                                                 )
        \                      ----------------- bottom rail (y = 0) ------------------                 /
          CL11 ... CL20        AUXD0 DG01 AUXD1 DG02 AUXD2 DG03 AUXD3 DG04 AUXD4        CR01 ... CR10
                                     x=0        x=1        x=2        x=3
```

| zone type | sites | capacity (qubits) | gate | spam | cool |
|---|---|---|---|---|---|
| `dg` | DG01-DG04 | 2 | yes | yes | yes |
| `ug` | UG01-UG04 | 2 | no | no | yes |
| `aux` | AUXD0-4, AUXU0-4 | 2 | no | no | yes |
| `conveyor` | CR01-20, CL01-20 | 1 | no | no | yes |
| `load` | LOAD | 1 | no | no | yes |

`Machine.load("Reproduce/moses2023/h2.arch.json").violations()` is empty: R11, R18 and the
geometry rules R19 (no node above degree 4; there is no node above 2), R20 (every pair of
rails at a node subtends ≥ 60°; the sharpest bend on the end curves is 156°) and R21
(planar). R22 and R23 are programme rules; see "Rules on this machine" below.

## Every paper fact, and where it is in the document

"Fig. 2d" numbers are read off the vector drawing in `h2_benchmarking_figure.pdf`, which is
the trap's top metal layer at a fixed scale: 56.0 pt per 750 µm, and at that scale the
drawn DC extent (6.58 mm) and isthmus (2.02 mm) agree with the Fig. 1 caption to 1%.

| paper fact | where (paper) | where (document) |
|---|---|---|
| race track: a linear trap with periodic boundary conditions, one RF null | Abstract; Sec. II.A | one closed loop `L0`, every rail on it |
| no junctions | Sec. II.A (one RF null; junction transport named as future work, Sec. I) | degree histogram `{2: 59}` |
| 4 DG gate zones, DG01-DG04 left to right, bottom row | Sec. II.A; Fig. 2d caption | `DG01`-`DG04` at (0,0)…(3,0) |
| 4 UG gate zones, UG01-UG04 right to left, top row | Sec. II.A; Fig. 2d caption | `UG01`-`UG04` at (3,1)…(0,1) |
| only DG zones gate, prepare and measure | Sec. II.A, II.B, II.D | zone type `dg`: gate, spam |
| UG zones only rearrange (physical swaps) | Sec. II.A; Fig. 2d caption | zone type `ug`: no gate, no spam |
| gate-zone spacing 750 µm | Sec. II.A | unit of every position; `provenance.pitch_um` |
| auxiliary regions around the gate zones, parking UG ions during gates | Sec. II.A, II.E; Fig. 2d | zone type `aux`, 5 per rail |
| conveyor regions, `{a,b,c}` tiling, 3 voltage signals, 20 wells per side, 1 well per 3 electrodes | Sec. II.A; Fig. 2b | 20 `conveyor` sites per end; `control.channels`: `conveyor.a/b/c` drive all 40 wells, `switch_per_site: false` |
| load hole in the middle of the left conveyor, independent electrodes | Sec. II.A; Fig. 2d | site `LOAD` (zone `load`, photoionization) between CL10 and CL11, its own channel |
| gate and auxiliary electrodes independently driven (268 sources for 376 electrodes) | Sec. II.A | one `independent` channel per non-conveyor site; `control.model: "direct"` |
| qubit = one 171Yb+ with one 138Ba+ coolant | Sec. II.B | `species`; every capacity counts YB pairs |
| 2Q gate on one YBBY crystal: two qubits per gate zone | Sec. II.E; Fig. 2e | `dg`/`ug` capacity 2 |
| batches of 8 in the DG zones, the UG zones and each storage region | Sec. II.E | capacities 4 × 2 = 8 per gate row |
| at most four 2Q gates per round (four 2Q beam pairs, four zones) | Table I caption; Sec. II.C | four gate-capable sites; R12 allows one gate per site per cycle |
| transport primitives: split/combine, linear shifts, physical swaps, batch shift | Sec. II.E | curves `split`, `merge`, `shuttle_segment`, `ion_swap`; classes `shuttle`, `batch_shift_ccw/cw` |
| Doppler sheet beams cool every Ba+ during transport | Sec. II.E; Fig. 2d | `cool`: scope global, broadcastable; `cool: true` on every zone |
| 2Q infidelity 1.84(5)e-3 (Table II 18.3(5)e-4) | Abstract; Table II | `ms_gate.fidelity_at_n0 = 0.99816` |
| 1Q infidelity 2.5(3)e-5 | Abstract | `1q_gate.fidelity = 0.999975` |
| SPAM 1.6(1)e-3 (preparation and measurement together) | Abstract | `measure.fidelity = 0.9984`, labelled as SPAM |
| 376 electrodes, 268 sources, 1 RF, 280-pin CPGA; RF ~200 V, 42 MHz; 70 µm ion height | Sec. II.A; Fig. 2c | `provenance`, `control.wiring.note` (prose: nothing computes with them) |
| top rail 750 µm above the bottom one | Fig. 2d (56.0 pt) | top rail at y = 1 |
| end curves: semicircles of radius 375 µm, centres 2604 µm either side of the middle | Fig. 2d (28.0 pt; x = 57.65, 446.5 pt) | the conveyor wells' positions |
| auxiliary blocks midway between and outside the gate zones | Fig. 2d (grey blocks centred at ±28 pt from the blue ones) | `AUX*` at half-integer x |

## What is assumed, and why

| assumption | why this one |
|---|---|
| 5 auxiliary sites per rail (between and outside the 4 gate zones) | the count is not stated; Fig. 2d draws exactly 5 grey blocks per rail |
| auxiliary capacity 2 qubits | not stated; Fig. 2d parks one pair of qubits per block, and UG ions (2 per zone) are moved to "the nearby auxiliary zones" |
| conveyor well capacity 1 qubit | not stated in the text; Fig. 2h shows one YB pair per well |
| 20 wells spread evenly along each green arc: well pitch 154.5 µm | the well pitch is not stated; Fig. 2d's storage ions sit 149 µm apart |
| the load hole is one more site, between wells 10 and 11, capacity 1 | the text says "in the middle of the left-side conveyor"; its capacity and well count are not stated. (Fig. 2d draws about six independent electrodes around it, which would leave nearer 18 tied wells on the left; the text's 20 is kept.) |
| both conveyors share the same three signals | "This requires only three total voltage signals" read literally; the text is ambiguous (step-0 report §1.10.4) |
| every primitive time is 1 µs, every quanta 0 (table `local`, label "unit time") | **the paper states no primitive duration** (only whole-shot times, Table I) and no heating rate; the replay needs a number to count operations with. No time from this document is compared with anything |
| no `heating`, no `T_coh_s`, no R7 budget (`max_quanta`) | none is stated ("T1 of several minutes" is not a number) |

## What the language cannot express

1. **A qubit is an ion pair.** Capacities count qubits and say so in prose; nothing in the
   language knows a site holds Yb-Ba pairs in a fixed orientation, that 1Q gates need the
   pair alone in the zone centre, or that a 2Q gate needs the YBBY order (Sec. II.E).
2. **Zone-scoped cooling.** The resolved sideband cooling of the DG zones before every 2Q
   round (Sec. II.E; 25-68% of shot time, Table I) has no place: `cool` is one global
   primitive, and a zone's `cool` flag says only that cooling may happen there.
3. **Mixed control.** The conveyors are broadcast and the gate region is not. The channel
   map says exactly that, and R4 (drivability) judges it, but R22 (one waveform per
   cycle) is a machine-wide switch: `simd_classes` would forbid H2's independent gate-region
   moves, `direct` (chosen) drops the rule, so R4's channel map is the only broadcast check.
4. **Batch shift as a primitive.** It is declared as a movement class on `L0` with a unit
   curve; nothing says it is "comparatively slow and dominates the circuit time" (Sec.
   II.E), and our rigid-rotation compiler pass does not apply to a loop without docks.
5. **Physical swap in a gate zone** is the `ion_swap` curve; nothing ties it to UG/DG zones.
6. **Angle-dependent 2Q error** ε(θ) = (2.9(2) θ/π + 0.46(6))·10⁻³ (Fig. 3): one fidelity per
   gate type; the law is kept as prose (`ms_gate.angle_law_not_expressible`).
7. **The electrode count.** The language counts channels per site; H2's 268 sources come
   from per-zone electrode counts the paper does not give. The channel map has 22 channels
   (3 conveyor + 19 independent sites) and the hardware report says 22, not 268.
8. **Optics.** Four 2Q beam pairs for four zones, per-zone measurement beams, a PMT array
   over eight zones: prose under `control.optical`. The four-gates-per-round limit is
   enforced only through there being four gate-capable sites.
9. **A curve table named for what it is.** The table name is a closed set
   (`qccd/arch/curves.py::TABLES`); the unit times use `local` with the label "unit time"
   and a `time_basis: "unit"` field on the scalar primitives.

## The circuits, rewritten exactly

From the artifact (`circuits.py`): GHZ N=32 (the Z-basis circuit; all 32 Z keys hold one
text), the ten MB N=32 l=10 circuits, all 200 QV n=16 circuits (`qv_circs`, what ran), the
ten RCS N=32 circuits (`qasm_compiled`, what ran) and all 27 QAOA N=32 p=2 circuits (one
per optimiser step; same structure, different angles).

Our compiler does not know `U1q`, `ZZ`, `RZZ` or `Rxxyyzz` (Quantinuum's `hqslib1.inc`), so
each is rewritten exactly:

| H2 gate | definition | rewrite |
|---|---|---|
| `U1q(θ,φ)` | exp(-iθ/2 (cos φ X + sin φ Y)) | `u3(θ, φ-π/2, π/2-φ)` |
| `RZZ(θ)` | U_ZZ(θ) = exp(-iθ/2 ZZ) (Sec. II.C) | `h h; rxx(θ); h h` |
| `ZZ` | U_ZZ(π/2) (Fig. 5 caption) | the same with θ = π/2 |
| `Rxxyyzz(α,β,γ)` | exp(-i/2 (α XX + β YY + γ ZZ)) | `rxx(α)`; `sdg sdg; rxx(β); s s`; `h h; rxx(γ); h h`, a zero angle dropped |

`rxx` is the one two-qubit gate our compiler emits as a single MS pulse, so **one H2
two-qubit gate stays one MS**, as on H2 ("a phase-sensitive MS gate sandwiched between 1Q
wrapper pulses", Sec. II.C). The brief asked for cx; a cx rewrite is also exact, but a
non-Clifford U_ZZ(θ) needs two cx (two MS), which would double the very count compared.

`check_rewrites.py` (log in `results.json` → `rewrite.checks`):

* identities, 50 random angles each: worst error 6e-16 (up to global phase);
* MB: the stim tableau of each rewritten l=10 circuit measures the artifact's `surv_state`
  deterministically, 10/10 (checks `ZZ` and every Clifford rotation);
* RCS at N=16 (same gates as N=32, which does not fit a statevector): the ideal probability
  of every sampled bitstring matches `bitstring_probs` to 8e-11 relative, 3/3 circuits
  (checks `U1q`, `RZZ` and the readout order);
* QV: the executed circuits are *approximations* of the ideal ones (pytket set about 63
  components per circuit to exactly 0; the ideal circuits are `qv_circs_nomeas`, u3 + 384
  cx). So there is no exact reference; the rewritten executed circuit reaches classical
  fidelity 0.959-0.970 to the ideal circuit, against 0.59-0.62 under each alternative
  convention (angles ×2, ×½, YY sign flipped), 4/4 circuits. The heavy-output probability
  of each ideal circuit reproduces `heavy_ideal` to 1e-14 -- with entries 0 and 1 swapped
  relative to the circuit order (seen on the four circuits simulated; not checked further).

**The "# 2Q gates" column of H2-T1 reproduces exactly:** GHZ 31, MB 320 (all ten), RCS 172
(all ten), QAOA 96 (all 27), and QV 273-317 with mean **296.3** over the 200 circuits -- the
text's "average 296" (Sec. IV.B); 8 of the 200 have exactly Table I's 310.

## Rounds: the paper, the circuits' own bounds, and our compiler

A round is one instruction carrying two-qubit pairs. Three numbers need no compiler,
only the circuit and the four DG zones (`run_h2.py::bounds`):

* **depth**: ASAP layers of two-qubit gates, in the file's gate order (H2 keeps the order
  of gates sharing a qubit, Sec. II.E), barriers honoured;
* **floor** = max(depth, ⌈gates/4⌉): no schedule with ≤ 4 gates a round does better;
* **layered** = Σ over ASAP layers of ⌈|layer|/4⌉: the paper's own procedure (largest
  layer, then batches through the four DG zones, Sec. II.E) with every batch full and
  transport free.

| H2-T1 row | paper gates / rounds | our gates | depth | floor | layered | our compiler |
|---|---|---|---|---|---|---|
| GHZ, N=32 | 31 / 14 | 31 | 5 | 8 | 9 | refused (1/1) |
| MB, N=32, l=10 | 320 / 80 | 320 | 20 | 80 | **80** | refused (10/10) |
| QV, n=16 | 310 / 128 ("an example") | 273-317, mean 296.3 | 42-48 | 69-80 | 83-95 (median 90) | refused (200/200) |
| QV, the 8 circuits with 310 gates | 310 / 128 | 310 | 46-48 | 78 | 89-95 | refused |
| RCS, N=32 | 172 / 56 | 172 | 14 | 43 | 46 | refused (10/10) |
| QAOA p=2, N=32 | 96 / 66 | 96 | 27 | 27 | 34 | refused (27/27) |

MB is the one row where the paper meets the layered count: every layer is 16 gates in four
full batches. Elsewhere the paper runs more rounds than the layered count (GHZ 14 vs 9, QV
128 vs 89-95, RCS 56 vs 46, QAOA 66 vs 34), which is what a compiler that minimises
transport time rather than rounds (Sec. II.E) would do. **None of these is a time**: rounds
are cheap and transport between them is 60% of H2's circuit time (Sec. II.E), and this
document's primitive times are unit times, compared with nothing.

## Our compiler on this machine: every circuit refused

Published compiler 35bf68427d9c (windows-x86_64, sha256 `95951a42…fc0fc`, the digest
`qccd/workspace/toolchain.json` pins), called as `compile_with_qccdc(qasm, h2.arch.json,
workdir, mode="compile")`. 248 of 248 circuits refused:

| circuits | how |
|---|---|
| GHZ (1), MB (10) | crash: `Gateset.Unsupported("barrier on 32 qubits: ...")` (M1) |
| RCS (10), QAOA (27), QV (183) | `unroutable: qN cannot reach DGnn from CLnn within 66 cycles`, then `rotation does not apply either: the device has no closed loop with docks` (M5) |
| QV (17) | ops left unrealised (869-1006 of ~1200 per circuit) (M2, M3) |
| GHZ, MB with their barriers removed (diagnostic only) | GHZ: 27 of 31 cx and 24 of 32 measurements unrealised (M2, M3); MB: unroutable (M5) |

Each cause, as a minimal example on `h2.arch.json` (`results.json` → `minimal_examples`;
`qccdc compile <qasm> --arch <h2 expanded> -o <out> --no-rotate [--init-placement <json>]`):

* **M1, a barrier on three qubits crashes the compiler** (exit 2) before placement:
  `Circuit.lower` sends every op to `Gateset_composites.lower`, which refuses anything on
  more than two qubits that is not `ccx`/`cswap`. The layer loop handles `barrier` -- it is
  never reached. Both GHZ and MB carry full-width barriers.
* **M2, five independent cx on four gate zones**: the fifth is left unrealised. An ASAP
  layer gets one gate-capable trap per op (`compile.ml`, `claimed`); nothing defers the
  excess to a second round of the same layer. H2 runs such a layer in batches of four
  (Sec. II.E). The same holds for measurements: 8 slots in the DG zones, 32 readouts.
* **M3, one cx with an idle qubit in every gate zone**: unrealised. `meet` needs room for
  both operands and never moves a bystander out of a gate zone.
* **M4, a conveyor ion moved alone**: `cx q0,q1` with q0 in CR01 and an idle q2 in CR03.
  The compile succeeds; the program fails **R4** (drivability): instruction 2 moves CR01's
  ion while CR03's, on the same three tied conveyor signals, stays. The router does not read
  `control.channels`. Nine of the 17 partial QV programs fail R4 the same way (3-108
  violations each).
* **M5, no rigid rotation without docks**: `qccdc rotate` answers "the device has no closed
  loop with docks". H2's batch shift is a rigid rotation of a loop whose gate zones sit ON
  the loop; our rotation pass rotates only loops with spur docks, so with every well
  loaded the one-ion-at-a-time router jams within its 66-cycle horizon.
* **`compile_with_qccdc` with a relative `workdir`** fails as "export_arch failed": its
  tools run with `cwd = workdir` and receive workdir-relative paths, so the output lands
  in `workdir/workdir/`. `run_h2.py` passes an absolute path.

## Rules on this machine

`violations()` on the document: none (R11, R18, R19-R21). On the programs the compiler did
emit (`verify(prog, arch, corrected_model(table="local"))`):

* GHZ without barriers (partial: 1 round of 4 pairs, 2 transport cycles): every checked rule
  passes; R7b and R10 skipped, R15 partial.
* 17 partial QV programs: 9 fail R4 (M4), 8 pass every checked rule.
* **R22 is reported as passed although it judged nothing.** Under `control.model: "direct"`
  `r22_uniform_motion` returns at once, yet `verify()` puts R22 in `passed`;
  `docs/rules.md` says it is skipped there. Any program on this document shows it.
* R23 passes vacuously (no junctions); R7, R16 and R17 pass because nothing they need is
  declared (no gate budget, zero quanta, no heating rate): the paper states none of them.
