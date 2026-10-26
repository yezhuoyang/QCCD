# Full-stack fault tolerance on QCCD — plan (approved 2026-10-26)

**Decisions (user, 2026-10-26):**
- **D1:** ask FormalRV for opt-in provenance sidecars.
- **D2:** two pinned physics profiles: `physics@2` and an IonQ-like aspirational one, labelled as such.
- **D3:** GE2021 is the first scale target.
- **D4:** keep `.alg` as the hand path.

**The user's decisions after S1 (2026-10-26):**
1. *"We don't care about preserving circuit level code distance here."* Distance is not a rung of
   the ladder and gates nothing.
2. *"Please don't touch FormalRV, design our own system here."* FormalRV is input only. There will
   be no provenance sidecars from FormalRV (D1 is withdrawn): the links between layers are QCCD's
   own system.
3. S1 committed.
4. *"The full gadgetized architecture design page should be the default."* A FormalRV program runs
   on the gadget town (places, roads, junctions, decoder, archive), and the page is the gadget
   design page with the stack in it. The flat Studio compile of S1 is the exactness check, not the
   default view.

## Progress

### Slice S1 done (2026-10-26): one logical CX, every layer, on ions

**Run it.** `python -m qccd.fullstack run examples/fullstack/cnot.prog --machine examples/fullstack/s1.machine --sysspec examples/fullstack/cnot.sysspec --arch arch/grid9x9.arch.json -o out/fullstack/s1/run --specialize n=2`, then `qccd.fullstack.page.render(run, html)`. The whole run takes 38 s.

**What happens, layer by layer.**
- FormalRV, at its pinned commit, compiles `CX(0, 1)`:
  - to 6 PPMs and 8 frame updates (`.qppm`);
  - then 62 `.qec` statements (merge, extract, decode, split, frame);
  - then a 312-line staged `.sysprog`.
- FormalRV's VM admits that listing: `RESOURCE_VALIDATED`, 268.5 ms, 70 qubits.
- Our reader expands it to 2,086 events, equal to the VM's counts op by op.
- Every program line is linked up to `.prog`; the links are reconstructed (see below).
- Qubits are devirtualized to 36 ions:
  - ancillas are allocated by lifetime;
  - QCCD's |Y+> factory is a stim-synthesized encoder, checked, and uses none of the 17 workspace qubits the contract grants.
- qccdc compiles the program for `grid9x9` under physics@2: 6,306 instructions, **1.389 s**.

**Checks.**
- 24 rules pass, none fail.
- Correspondence holds: only cooling is inserted.
- R10 holds in both halves: check_cert gives semantics ok and order ok, and the Lean checker gives ACCEPTED.
- The §2.6a stack check passes: 5,890 instructions, 9,995 event chains, 0 problems.
- A moved link fails it, as it should.

**Page.** `out/fullstack/s1/cnot_fullstack.html` is the Studio with a fourth Program view, **Stack**:
- the Logical / PPM / QEC / Clifford / Hardware caption at the executing instruction;
- the four source listings, each with its line lit;
- a click on a line jumps to its first instruction;
- a badge on every link saying how it was made.

The Hardware, Gates and Both views and every other page are unchanged. Tests: `tests/test_fullstack.py`, 9 tests.

### Slice S2 (2026-10-26): the gadget town is the default

`python -m qccd.fullstack run examples/fullstack/cnot.prog -o DIR --specialize n=2 [--machine … --sysspec …]`
takes 5 s. Its page is `DIR/gadgets/index.html`: the gadget design tool, with the stack above its Program
panel.

**What happens, in order.**
- FormalRV's `.prog` goes through `.qppm` to `.qec`, unchanged.
- **QCCD's own lowering** (`qccd/fullstack/qec2alg.py`) turns the `.qec` into a gadget program (`.alg`):
  - `prepare |Y+>` becomes `prep a Y`;
  - a merge group becomes `ppm P -> m` (or `read` when the block is never used again);
  - `frame on c[..)` becomes `if m: <pauli>`.
- All 62 `.qec` statements are linked: 23 to an instruction of their own, 39 absorbed into one, with the
  reason stated.
- The contract checker proves the gadget program is **exactly CX in all 64 branches** (not CZ; removing
  one frame breaks it).
- It is scheduled on the **full-stack machine** (`examples/gadgets/floorplans/fullstack.floor`, generated
  by the town layout: two Pauli-product benches, two |Ȳ⟩ pharmacies, five memories, prep, clinic, two
  readouts, two depots, decoder, archive).
- Result: 0 refused, 298.8 ms. G1–G11 hold, 23/23 machine rules hold, and the program is signed off
  compositionally. FormalRV's VM, on its abstract machine, says 268.5 ms.

**New in the gadget library.**
- **`ppm_d3`, the Pauli-product bench** (`qccd/gadget/leaves/bench.py`, category "Pauli Product
  Measurement", a dome silhouette): a probe ion measures any product of one X̄ or Z̄ from up to three
  blocks. It has 14 ops, all verified, each with a complete specification (rank 18K/18K). It is not
  circuit-level fault-tolerant, by the user's decision.
- **The language** gains `ppm P q … -> m` and `prep q Y`.
- **The page shows the stack.** At the playhead it gives the Logical / PPM / QEC / Gadget / Clifford /
  Hardware rows. Clifford and Hardware are the place op's own TSIR at its local time. The `.prog`,
  `.qppm` and `.qec` listings light up as they run, and a click on a line seeks to it.

**Where it lives.** The bench sits outside `places.py`, so no cached place is re-keyed. The full-stack
tests are `tests/test_fullstack.py`: 14 tests, 13 of them without FormalRV.

### Slice S3 (2026-10-26): gadgets, loops, branches and calls (Deutsch–Jozsa)

`python -m qccd.fullstack run <FormalRV>/FormalRV/Examples/DJ/DJ.prog -o DIR --specialize n=1 --specialize a=1`
takes 4 s. The result: 94 instructions, 942 ms on the town, 0 refused, and all checks hold. The spec
**`Pr[c[0,1) = a] = 1` is proved in every branch at once** (24 random outcomes, symbolically).

**The lowering** (`qec2alg.py`) is now an interpreter over the whole `.qec`.
- It evaluates indices in the listing language's natural arithmetic and unrolls loops.
- Each FormalRV gadget call becomes an **`.alg` gate**, one definition per distinct body, so the oracle
  is `gate djoracle p0 p1:` called by name. The page's hierarchy and call frames show it.
- A `when` body becomes guarded Paulis, or a gate called under the guard.
- `magic |T>` becomes `inject` plus `x`.
- A single-factor measurement of a block that is never used again becomes a destructive `read`.
- All 259 reachable `.qec` lines are linked.

**The links** (`structure.py`) are made per unit, statement by statement, in order. Each one is checked
by what both sides must share:
- Pauli letters and block families (`shape`);
- the magic state (`order`);
- the gadget name (`name`);
- FormalRV's compile-time `if`, which it writes as a 0/1-trip `for rv_g…` loop (`branch`);
- for `.prog` gates, `template`: the statement count FormalRV's own `prepare_qppm` writes for that gate
  alone, learned once per commit (X 1, Z 1, H 15, CX 17, CZ 17, CCX 74).

The `.prog` reader follows `use` includes. Only the units the program reaches are linked, since a
library may hold gates the program never calls. DJ links completely: `.qec`→`.qppm` 259 of 260 (the
one left is a `skip`), and `.qppm`→`.prog` 96 of 96.

**Semantics without enumeration** (`qccd/gadget/logic/symbolic.py`).
- **What it is.** A stabilizer simulation, one qubit per block. Every sign and outcome is an affine
  GF(2) expression of the random outcomes, so all branches are decided in one polynomial run, where
  branch enumeration would need 2²⁵ state vectors.
- **Mutation controls behave as the physics says.** A correction that matters, removed, turns the
  outcome into `1 ⊕ o1`. A Z on q0 while it is still |0⟩, removed, changes nothing, correctly.
- **The compositional sign-off** uses it *only* when a called gate names no contract; every existing
  program is checked exactly as before. The gadget suite passes, 200 of 200.

**On the page,** an instruction expanded from a gate links through its own body line (`Instr.src_line`),
and the Gadget row names the call it is inside. Inside the oracle the stack reads
`CX(i, n) ../Primer/primer.prog/djOracle.prog:4` → `DJ.qppm:484` → `DJ.qec:1282` →
`ppm Z q0 Z djoracle1_l3` → its CZ → its TSIR.

### Slice S4 (2026-10-26): T states — Shor for 15, written in FormalRV

**The program.** `examples/fullstack/shor15.prog` is the order of 7 mod 15, the same circuit as the hand-written
`shor15.alg`: three controls, two Toffolis, a semiclassical inverse QFT. It is written in **FormalRV's own
`.prog` language** and compiled by FormalRV, unchanged.
`python -m qccd.fullstack run examples/fullstack/shor15.prog -o DIR --specialize n=7` takes 30 s.

**The result.**
- 15 T states, 61 |Ȳ⟩ rotations, four-block products for the Toffolis' π/8 rotations;
- 1,551 of 1,551 `.qec` lines linked; 0 refused; 8.94 s on the town; every rule holds; signed off
  compositionally;
- **its results and, result by result, the work register's state are exactly the `.prog`'s**: worst
  state fidelity 1.0 over all four results, never more than 10 live branches.

**What it took.**
- **The bench takes four blocks** (`ppm.ZZZZ`: a Toffoli's Z̄Z̄Z̄ against a T block): 30 ops, all verified,
  ranks complete up to 72.
- **The machine gains two 15-to-1 factories, eight memories and a zone.**
- **`.prog`→`.qppm` by prefix differencing.** FormalRV's `.prog` compiler optimises across gates
  (adjacent rotations merge), so a gate has no fixed template. Every prefix of the program is compiled
  with FormalRV's own `prepare_qppm` and aligned with the previous one, with ancilla slots and record
  numbers blanked.
  - A statement that appears when gate k is added is k's (`prefix`); one that changes is shared
    (`merged`). Each Toffoli owns its 134 statements.
  - The last prefix must reproduce FormalRV's `.qppm` exactly, or nothing is linked.
- **Refresh keeps a block alive to the end of the program.** A register nothing reads again used to
  wait more than 1 s at the tail (G8).
- **Exact semantics with T states** (`qccd/gadget/logic/merged.py`): a state vector per branch, with
  branches merged as soon as no later guard reads their outcome. `progsim.py` simulates the `.prog`
  itself in **FormalRV's convention, `Rz q k` = exp(−iπ/2ᵏ) on |1⟩** (GateLang.lean).
- **The sign-off records the full stack's verdict as its semantics part.**

**What the state check caught.** It found two bugs in my own work that the distribution could not show,
because a period-4 Shor's results are uniform either way:
- the simulator used the opposite `Rz` sign;
- the first `shor15.prog` therefore wrote S where it meant S†.

A deliberately wrong gate (one CX rotation measured as Z̄ instead of X̄, i.e. a CZ) is caught.

### Differences from the plan as written

- **The reader is QCCD's own.** It is not FormalRV's parser: the VM's parser is not a library, and the Python oracle lags the staged compiler (it refuses `spec maintenance`). It is held to FormalRV's VM instead, which must agree op by op on every listing.
- **Provenance is reconstructed.** FormalRV's owner said provenance in Sysc waits until their port is merged. Each link names its kind (pauli / band / operands / kind / count / order / group / only).
- **The machine profile is hand-written** for now (`examples/fullstack/s1.machine`); the up-bridge is still to build.
- **S1 compiles flat.** It is exact, but compiling each gadget once per tile is the next slice.
- **Fault distance is shown, not gating.** FormalRV's d=3 `surfaceRound` has circuit distance 2 against Z-bar (its Z-check CNOTs run row-major; stim, 1 and 3 rounds). FormalRV's owner answered "We don't care about fault-tolerance", so "distance >= d" is QCCD's own rung: shown, and it gates nothing unless the user says otherwise.
- **FormalRV's staged compiler and its VM can disagree.** On an 8-gate machine the compiler emitted a listing the VM refused (20 > 8 gate reservations). The profile has to be one the VM admits.

### Slice S5 (in progress, 2026-10-26): scale — a gadget is lowered, scheduled and checked once

**The workload.** FormalRV's own `Examples/Shor` at its witness (N = 3, 3 bits, 4 exponent bits, a = 2)
compiles in 5 s to an 11,978-line `.qec` with its loops and gadgets folded. Unrolled, it is **576,651 town
instructions**: 68,928 Pauli products, 45,683 |Ȳ⟩ and 23,268 T states, 137,856 decoder calls. Flat, at
Shor-15's measured cost (8 ms per instruction to schedule, 10 s per 7,000 events to judge), that is hours
of compute, and a page of gigabytes. So nothing is flattened.

**S5a — lowering, once per call shape (done).** FormalRV's `.qec` gadgets name their scratch offsets
(`rv_ancillabase`, `rv_logicalancillabase`, `rv_synbase`, `rv_recbase`). A call is lowered once per value
of every *other* argument, with the offsets at zero, and every call of that shape shares the body. A body
that names a logical ancilla it did not prepare is lowered exactly instead, so the sharing is never wrong.
Readouts are decided body by body, in one pass. Up to its inverse QFT (whose rotations wait for S5d), the
Shor witness lowers in 9.5 s to **19,154 `.alg` lines and 757 gates**; DJ and Shor-15 lower byte for byte
as before. (The 576,651 above counts that part.)

**S5b — schedule each gate once per context.** A gate call is a *macro*:
- its body is scheduled once, in isolation, for each binding of its blocks to their **homes**
  (every data block lives in one memory for its whole life, and a call returns it there);
- the macro's **footprint** is every interval it reserves on a place, road, junction, the decoder and the
  archive, relative to its start;
- the caller places a call as one box: the earliest start at which every interval of the footprint is
  free (a place keeps serving in issue order; junctions and classical places take any gap);
- outcomes and frames are the macro's own, named per instance; a parameter's Pauli frame enters as a
  symbol and leaves as a parity over it, substituted at the call.
GIR 0.3 holds the macros and their instances, not their expansion. Expanding it gives GIR 0.2, so on
Shor-15 and DJ every existing check runs on the expansion as a differential test.

**S5b — done (`qccd/gadget/fold.py`).**
- **Holds.** A block resting in its memory gets a syndrome round every 0.4 s, so G8's 1 s budget holds
  even across a call's start. Those rounds are one **hold** row: block, memory, first round, period,
  count. The memory is the block's own while it rests, and a round's decode (1 µs against a 13 ms round)
  goes into the decoder's free time, which `expand` places and checks with every other row.
- **Differential tests.** On Shor-15, DJ and a gate called three times, the expansion passes all 11
  place checks and all 23 machine rules. The folded makespan is within 3–9 % of the flat one, from
  returning blocks home. A frame left by a call composes correctly in its caller.
- **FormalRV's Shor witness, up to its QFT, on a wide machine.**
  - The machine (`fullstack_wide.floor`): 4 benches, 4 factories, 32 memories.
  - 576,652 instructions as 757 macros (51 reused); scheduled in 135 s, 0 refused.
  - **3.03 hours** of machine time.
  - GIR 0.3: 176 MB (19 MB gzipped). The root is 2 MB; its 163,133 refresh rounds are 97 holds. Macros
    are 89 KB at the median and 5.9 MB at most, so the page loads them as it needs them.

**S5c — the page plays GIR 0.3** by expanding only the instances on screen, at the time on screen.

**S5d — rotations.** FormalRV leaves `ppmrot(±π/2ᵏ)` as a synthesis stub (`tSynth[k]` magic states, no
circuit). QCCD supplies its own: the angle π/8 is a T state from a factory; finer angles are injected
rotation states consumed by a Pauli-product measurement, with the doubled-angle correction repeated until
it reaches T (repeat-until-success). This is exact, so the distribution check still applies.

- **Shor from FormalRV's own example**, unmodified, next to the hand-written Shor-15.
- **The up-bridge:** derive the `.machine` from characterized macros.
- **The Goal 2 board**, once an entry can be graded end to end.

**Overriding requirement (user):** *everything is debuggable*. At every instant of the animation, the viewer
must show exactly which **logical operation**, which **PPM**, which **Clifford circuit** and which
**hardware instruction** they are looking at. See §2.6a.

**Goal.** A user writes, or picks, a fault-tolerant algorithm in FormalRV's language. They choose a QEC code
and a QCCD machine. Then they watch the whole run, which can last many days for real Shor, on concrete
ions. At any instant they can stop and see why each thing is happening, as one line at every layer:

```
QParam (.prog)  ->  QParamPPM (.qppm)  ->  QParamQEC (.qec)  ->  QParamSys (.sysprog)  ->  QCCD hardware
 algorithm + spec   Pauli-product meas.   ops on code blocks    timed physical ops +     ions, traps, shuttles,
                                                                syscalls, VIRTUAL qubits  junctions, cooling, decoders
```

Every verified resource total (ions, trap sites, days, success probability) goes on a Goal 2 leaderboard.
Each entry shows which checks hold, layer by layer.

Everything from QParam to QParamSys stays FormalRV's, unchanged. QCCD adds the last arrow, which FormalRV
does not have today: its VM takes "all qubits are virtual" as a house rule (`compiler/lib/syntax/sys_ast.ml`),
and `route` is "mechanism-free by construction" (`FormalRV/System/SysProg.lean`).

---

## 1. What exists (surveyed 2026-10-26)

**FormalRV** (a separate repository, HEAD df3a1427)
- The four layers are text stage folders. Lowering is done by OCaml passes, with Lean soundness proofs where
  they exist: P3–P6 for prog→qppm, `compile_sound` for qppm→qec (ideal semantics), and the checkers C2–C6
  for qec→sysprog.
- The lowest layer is the **resource VM** (`ftq_vm/ocaml/resource_vm.ml`). It is an admission checker, not
  an executor. Its input is a flat key/value `.machine` with six service pools and constant latencies,
  plus factory, decoder and maintenance contracts. It has no topology, placement or zones.
- **Syscalls:** `request_ancilla`, `release_ancilla`, `route`, `request_magic`, `decode_syndrome`,
  `pauli_frame_update` and `parity`.
- **Scale without flattening.** Gadgets, `for … every P`, and calls are kept at every layer, and the VM walks
  summaries and reuses them by translation. GE2021 is 34,171 listing lines. The VM checked
  1.67·10¹⁷ dynamic instructions from 284,066 summary nodes in about 19 CPU-minutes (14.23 days,
  17.7 M qubits, `RESOURCE_VALIDATED`).
- **Results.** GE2021 and Gidney2025 are VM-validated. The OratomicUs2026 RSA-2048 lane frontier
  (LP24 qLDPC, 122.8–208.8 days) has program contracts only; its VM run is pending, for lack of memory.
- **Missing for us:**
  - There are **no source maps** between layers; only gadget names survive.
  - There is no event trace or timeline.
  - There is no trapped-ion model. A neutral-atom precedent (`Example/neutral_atom`, ZAC) was removed
    from the repo on 2026-10-06.

**QCCD gadgets** (this repo, `qccd/gadget`, docs/GADGETS.md, docs/ARCHITECTURE.md)
- **Places.** These are verified hardware macros for surface code d=3: `prep_d3`, `se_d3`, `mem_d3`,
  `tcx_d3`, `ls_d3`, `read_d3`, `inj_d3`, `msf15`, `depot9`, `junc9`, `dec_d3` and `arch1`.
  - Each op passes 28 rules by replay. R10 (the Lean certificate) is skipped on gadget ops.
  - Each op's logic is checked by symbolic stabilizer flows plus fault distance ≥ 3.
  - Each place is characterized once and cached by content hash.
- **Programs and machines.** `.alg` is a logical program, `.floor` a floorplan, and `city.py` a parallel
  scheduler that writes flat GIR 0.2 events. The rule sets F1–F9/G1–G14 and the contract and
  compositional sign-offs check the result.
- **Shor N=15** (`shor15.alg`, `shor.floor`): 157 + 32 instructions, 11.16 s, all 23 rules hold, signed off
  compositionally. It is the target of today's "Goal 2" card (`qccd/site/boards.py::GOALS`).
- **Limits:**
  - GIR is a flat event list: about 2 MB for Shor-15, all inlined into one page, with no streaming.
  - Only surface d=3. The BB beta is hardware-only.
  - No noise is sampled at gadget level.
  - Nothing references FormalRV.

**IonQ, arXiv:2609.05625** (Sept 2026): secp256k1 ECDLP in 25.7 days on 19,397 ions.
- **Codes and logical layer.** 1,457 logical qubits, generalized-bicycle codes Q102 [[102,22,9]] and
  Q66 [[66,4,10]], "walking cat" logical measurements, 4 CCZ factories of 319 ions each.
- **Physical assumptions.** POC 200 µs, transport 10 µs/step, p₂ = 10⁻⁴.
- **Where it stops.** Its compiler ends at architecture-legal *logical measurement schedules*. In the
  paper's words, lowering to transport, gates and readout "happens below the compiler, at runtime."
- **What it assumes.** That cat-bundle routes never conflict (Proposition 3). A uniform 200 µs POC, against
  measured merge+gate+split of 200–500 µs and readout of 350 µs per image. Cooling is not scheduled.
  Trap sites and junctions are not counted.

  Every one of these is something a devirtualizer on a real QCCD floorplan measures instead of assuming.
  That is our contribution.

---

## 2. Design

### 2.1 The seam: `.sysprog` is the contract, with one bridge each way

```
                 FormalRV (unchanged, pinned commit)                       QCCD
 .prog -> .qppm -> .qec -> .sysprog ──────────────── down-bridge ──────────►  devirtualizer ─► hierarchical hardware program
                     ▲        │                                                    │            (GIR 0.3: calls, loops, translates)
                     │        └─► resource VM verdict ("abstract QCCD")            ▼
                     └────────── .machine + factory/decoder contracts ◄── up-bridge ── characterized QCCD macros
```

- **Up-bridge** (`qccd fullstack machine ARCH`; the command name is the user's to choose). It turns a QCCD
  architecture into a FormalRV `.machine` profile, with factory and maintenance contracts derived from
  *characterized* QCCD macros:
  - gate, measure and reset times from the pinned physics;
  - `route_latency_ns` as the conservative bound over the floor's road graph;
  - factory latency and footprint from the factory macro;
  - decoder latency from `qccd/analysis/feedback.py`;
  - capacities from the finite machine.

  This file is only an *input* to FormalRV, so FormalRV's code is untouched. FormalRV then compiles any of
  its programs, GE2021 included, *for this QCCD machine*, and its VM gives the **abstract** estimate.
- **Down-bridge.** The devirtualizer reads the `.sysprog` listing, plus the `.qec` `codes:` block for code
  structure, and produces a **concrete** program on the finite machine. That program is checked to
  *refine* the `.sysprog`: same operations per call, the same per-qubit order and record order, and every
  syscall honoured.
- **Reported gap.** The difference between the two estimates is reported per gadget: how much time
  transport, contention, cooling and loading add over the VM's abstract machine. This is
  FormalRV's gap-attribution table, extended by one row.

**Why `.sysprog` and not `.qec`.**
- It is the lowest layer FormalRV fully checks (C2–C6, VM).
- It already names every physical gate and every syscall.
- Its gadget, call and loop structure is exactly the reuse unit we need.

The `.qec` layer is still read, for which virtual qubits form which code block.

### 2.2 What "devirtualize" means on QCCD

| `.sysprog` | becomes, on QCCD | decided by |
|---|---|---|
| virtual qubit `data[i]` / `ancilla[j]` | an ion with a home site in a tile; the ion can change over time | block placement (static), then ion assignment (dynamic) |
| `request_ancilla` / `release_ancilla` | load or reset from a finite depot (loading rate, ion loss, reservoir) / dump or recycle | depot model; ion conservation checked |
| `route` inside a gadget body | split / move / junction / merge / swap inside the tile | the macro compiler (qccdc on the tile) |
| `route` between tiles | a road trip on the junction graph, leased hop by hop (existing `city.trip`/`_wave`) | road scheduler; cost depends only on the tile offset (§2.3) |
| `H S X Z CNOT CZ CY`, `measure` | TSIR gate, measure and reset in a gate or SPAM zone, with cooling inserted | macro compiler; R1–R28 by replay; R10 certificate against the body |
| `request_magic state=|T>/|CCZ>/|Y+>` | a factory tile run whose output is carried to the consumer's port | factory macro; output state checked by `logic/contract.py` (the T† lesson) |
| `decode_syndrome` | decoder place + syndrome and frame wires, with the latencies in `feedback.py` | classical layer (exists); guards wait on decision bits |
| `pauli_frame_update`, `parity` | archive operations (exists) | classical layer |

**Finite machine.** FormalRV's paper machines set capacities to 10¹². Ours are real, so a program either
fits, waits (retimed, with the delay attributed to a gadget), or is **refused at a located line**, in the
style of the VM's refusal codes.

**Gate order.** The devirtualizer only retimes. It never changes the order of gates on any qubit, nor the
order of records (see memory: the compiler keeps gate order).

### 2.3 Scalability: three principles

1. **Never flatten what FormalRV kept folded.**
   - The QCCD program has the same tree as the `.sysprog`:
     - a **leaf** is a *macro*: one TSIR program on one tile class, compiled and checked once;
     - an **inner node** is a placed call (macro × tile translate × start time), a loop (`every P`, with an
       affine placement function), or a route.
   - Its size is O(listing), not O(dynamic instructions). GE2021 would be about 34 k nodes plus a few
     hundred distinct macros.
   - This becomes **GIR 0.3** (working name). GIR 0.2, today's flat events, is the special case with no
     calls, so existing pages keep working.
2. **Gadgets are local, and the machine is periodic.**
   - The floorplan is a tiling: `.floor` gains `array` / `symmetry` statements (additive syntax), in the
     same way FormalRV's codes have `symmetry sigma … shift=91`.
   - A macro compiled on one tile is valid on every translate, so it is compiled **once per (gadget body,
     tile class, port binding)**.
   - A route's cost depends only on the offset between its two tiles, so it is memoized by offset
     (`channel_timing(L, k)` is the start of this).
   - Composition checks therefore run on summaries:
     - one loop trip plus the overlap between trips, as FormalRV's trip clocks charge it;
     - tile, road and junction reservations over (place × time);
     - the "spatial VM" (working name): a position-aware version of FormalRV's admission rule, built in this
       repo and not as a FormalRV change.
3. **Evaluate lazily in space-time.**
   - Every tree node carries its span `[t0, t1)` and a hull of the tiles it touches. Seeking to "day 9,
     14:03:22.415, factory row 3" walks the tree, pruning whatever misses the time or the viewport. The
     cost is O(depth · log), never O(events).
   - Only leaves that are on screen *and* zoomed in far enough are replayed ion by ion. That replay is
     today's `GadgetCore.LeafSim` at local time, generalized from `cycle × N` to a call tree.
   - The page streams macros by content hash instead of inlining them, so a many-day run loads as fast as
     Shor-15 does now.

### 2.4 Where the current gadget design fits (nothing is thrown away)

- **Places become hand-made macros, like standard cells.**
  - A place op (for example `se_d3.se.3`) is registered as an implementation of a `.sysprog` gadget body
    (for example the body FormalRV emits for `extract rounds=3` on a surface d=3 block).
  - It is accepted by the existing logic machinery: the circuit read off the hardware has the same
    stabilizer flows as the body, and distance ≥ d.
  - Where no hand place exists, qccdc compiles the body on a tile, and that path gets R10 as well.
  - The devirtualizer chooses the faster valid one; both options appear on the page.
- **`.floor`** stays the architecture language; arrays and tiles are added (§2.3).
- **`city.py`'s** leases, waves, refresh and classical layer become the inner engine at call level.
- **`.alg`** stays as the *hand* path for teaching and small hand designs. **Today's Shor-15 stays live**,
  relabelled "hand-designed, d=3", and becomes one entry in the small-N class of the leaderboard.

  (Optional later: an `.alg → .qec` exporter, so hand programs can also go through FormalRV.)
- **The studio's look and rules apply** to the new page: the program column, no extra panels unless asked,
  the shared studio shapes for ions, rails and junctions, and an agent declaration (`editor_api.json`) for
  every new control.

### 2.5 The verification ladder (what an entry shows, per layer)

| rung | owner | today |
|---|---|---|
| `.prog` meets its spec | FormalRV, Lean | proved for some audits (GE2021 Semantic/); not for OratomicUs2026 |
| prog→qppm→qec sound | FormalRV, Lean | P3–P6 proved; qppm→qec `compile_sound` (ideal); some obligations pending |
| qec→sysprog | FormalRV, OCaml checkers C2–C6, proved sound in Lean | checked per compile; no end-to-end theorem |
| abstract resources | FormalRV VM on the **QCCD-derived** `.machine` | not proved; differential-tested |
| each macro is its body | QCCD: R1–R28 replay + **R10 both halves** (Lean checker + `check_cert.py` vs the body's QASM); flows + distance for hand places | exists per piece; new: binding to `.sysprog` bodies |
| composition | QCCD spatial VM: reservations, port handshakes, ion conservation, coherence/refresh, n̄ budget | new (G1–G14 are the seed) |
| refinement | QCCD: the concrete tree realizes the `.sysprog` tree | new |
| logical error | stim on the *actual* macro TSIR under the pinned physics (memory-board machinery), composed using the VM's location counts (`block_rounds`, …) | new at gadget level; any extrapolation in d is labelled as extrapolated |

The leaderboard ranks only entries whose required rungs hold, and it prints the rest as text. That follows
the existing "established / not established" section of the gadgets page.

### 2.6 The page: a debugger for the whole stack

- **Stage.**
  - Machine view: tiles coloured and outlined by category, using today's palette and silhouettes.
  - Zoom down to tiles, then to ions.
  - Time runs from 1 µs/s up to 1 day/s, with level of detail (heat per tile at fast rates, ions at
    slow ones).
- **Stack.** For the selected ion, tile or instant, the call stack across all five layers, one line each:
  `.prog` line → `.qppm` line → `.qec` statement → `.sysprog` statement → macro / TSIR instruction.
  Clicking a frame opens that file at that line.
- **Stepping.** Step over / into / out *at any layer*. For example, "next `.qec` statement" jumps time to
  where it starts.
- **Breakpoints** on a line at any layer, or on a condition: "first refusal", "factory 3 idle > 1 ms",
  "a block waits past 80 % of its coherence budget".
- **Line-level stepping needs source maps,** which FormalRV does not emit (§1). QCCD reconstructs them
  itself (§2.6a). FormalRV is not changed.

### 2.6a The four-line caption: a hard requirement with a test

**Paused anywhere** (any time, any zoom, any selected ion, tile or road), the page shows one caption, and
every line links to its source:

| line | layer | example |
|---|---|---|
| **logical operation** | `.prog` gate / gadget call, and the `.qec` statement on code blocks | `andFresh(W, rS, rD, rT)` · `merge Z[P[0][0]] Z[P[1][0]]` on blocks 3, 7 |
| **PPM** | the `.qppm` Pauli-product measurement, magic preparation or frame update | `measure Z q[17] Z anc[25]` |
| **Clifford circuit** | the `.sysprog` physical circuit of that statement: gates on (virtual) qubits, with the current gate highlighted | `CNOT data[12] ancilla[40]` in `extract rounds=3`, round 2 of 3 |
| **hardware instruction** | the TSIR instruction(s) running now, with ions, trap, junction and duration | `MS(ion 4012, ion 4019) @ gate zone T7-g2, 40 µs` |

**Rules:**
- No line is ever blank or approximate. If a level has nothing at that instant, it says so: "idle: block 7
  waiting for magic state, 312 µs".
- A route or shuttle is captioned with the `route` syscall it realizes and the gate it brings qubits
  together for.
- A stall is captioned with what it waits on.

**How it's made possible.** Every link is QCCD's own; FormalRV is not changed.
- *TSIR instruction → macro gate → `.sysprog` statement*: the macro compiler keeps gate order, and R10's
  `check_cert.py` already pairs every TSIR gate with a source gate.
- *`.sysprog` → `.qec` → `.qppm` → `.prog`*: reconstructed by `qccd/fullstack/provenance.py` from what the
  lowering preserves exactly (Pauli products, record bands, operands). Each link names how it was made, and
  a line that cannot be anchored is refused, never guessed.

**The test** (in every phase from 2 on):
- Sample 1,000 random (time, place) points from a program, plus every macro boundary.
- Assert that all four lines resolve.
- Assert they are mutually consistent: the hardware gate's ions hold the virtual qubits of the Clifford
  gate, the Clifford gate lies inside the PPM's template, and the PPM lies inside the logical operation.
- Mutation: shuffle one provenance entry, and the test must fail.

### 2.7 Goal 2 leaderboard: verified resource estimates

- **Entry.** An entry is (FormalRV commit + program + point + compiler options, QCCD architecture files,
  pinned physics), packed into a content-addressed bundle in the existing release/bundle/evaluator form.
- **Grading.** The official service grades it in its sandbox with new stages, one per rung in §2.5.
- **Problem classes:**
  - *small N, fully checked* (N = 15, 21: every ion simulated, LER sampled);
  - *cryptographic* (RSA-2048; ECDLP-256 once FormalRV has a `.prog` for it).
- **Ranking:**
  - ion-days at a declared success target;
  - plus the Pareto front of ions against days;
  - machine size shown as **ions, trap sites and junctions**, because ions alone flatter a design.
- **Credits.** Each entry credits the algorithm, the code and the architecture separately, so co-design is
  visible: the same algorithm on two codes, or the same code on two machines.

### 2.8 Co-design axes (each one a file)

- **Algorithm:** a FormalRV `.prog` plus a point.
- **Code:** a FormalRV `codes:` block:
  - surface with any d;
  - LP/qLDPC, as in OratomicUs2026;
  - GB/BB — IonQ-style Q102 maps naturally onto QCCD **rings**, because its syndrome extraction is cyclic
    shifts, which is what `se_d3`'s ring conveyor already does.
- **Architecture:** a `.floor` with tile classes and arrays, plus a pinned physics profile.

---

## 3. Phases, each with an exit test

Phases 1, 2 and 3 can run in parallel. Heavy FormalRV compiles (GE2021: about 25 min and 9 GB) run one at a
time, because this desktop has hard-reset under load.

| # | phase | exit test |
|---|---|---|
| 0 | **Interface note.** Pin the FormalRV commit; write `docs/FULLSTACK.md`: what QCCD reads (the `.sysprog`, the `.qec` codes block, the point) and what it writes back (the `.machine` profile). Ask the FormalRV sessions to confirm nothing in it collides with their work. | FormalRV owner/sessions agree; D1–D4 decided |
| 1 | **Up-bridge.** QCCD architecture → `.machine` + contracts, from characterized macros. | FormalRV VM gives `RESOURCE_VALIDATED` (or a located refusal) for GE2021 recompiled on a QCCD profile, an abstract QCCD estimate in days and qubits |
| 2 | **Down-bridge, smallest case.** A `.sysprog` reader built on FormalRV's own OCaml parser at the pinned commit (no second grammar); block recovery from the `.qec` and the sysprog groups; macro synthesis (qccdc + R10, or a hand place by flows); placement, routing and retiming on a tiled floor; refinement check; GIR 0.3. | a small surface-d=3 FormalRV program runs end to end on ions; every macro certified; refinement holds; a mutated macro or a dropped route is refused |
| 3 | **The debugger page.** Lazy space-time index; streaming macros; machine → tile → ion zoom; stack panel; step and breakpoints; agent declaration. | at 10⁴ tiles, a seek takes under 50 ms and a frame under 16 ms, on a synthetic 14-day tree; browser test walks the stack and steps at every layer |
| 4 | **Shor, small N, from FormalRV.** N = 15 (then 21) from FormalRV `.prog` with real modular arithmetic, if FormalRV can compile the modexp at a small width (to check); compared against the hand Shor-15. | clicking any ion event shows the `.prog` line behind it; both Shor-15s on the board with their ladders |
| 5 | **Codes × architectures.** Tile classes for surface at any d (built from plaquette cells for large d), GB/BB on rings, CCZ factory tiles; per-tile LER by stim. | one algorithm on ≥2 codes × ≥2 machines, with comparable numbers |
| 6 | **Cryptographic scale.** GE2021, Gidney2025 and OratomicUs2026 (lanes) devirtualized on named QCCD machines; spatial VM at full size. | RSA-2048 on QCCD: ions, sites, junctions, days, success, the full ladder; a many-day animation that scrubs |
| 7 | **Goal 2 board.** Bundle kind, release, grader stages, page; the Goal 2 card points at it. | ≥3 entries graded by the official service |

---

## 4. Risks

- **Block structure.** The `.sysprog` may not keep enough to tell which virtual qubits form one code block
  (P5 maps groups to `ancilla[base_g+e]`).
  *Check in phase 2. Fallback: ask FormalRV for an opt-in block annotation.*
- **Route latency.** FormalRV's `route` latency is one constant. The up-bridge must use a conservative
  bound, so the abstract estimate is pessimistic, and only the down-bridge is exact.
  *Optional FormalRV extension later: a route-latency model.*
- **Large tiles.** A surface d=27 tile has about 1,458 ions, which may be too big for qccdc in one piece.
  *Build big tiles from periodic plaquette cells: the same translation principle, one level down.*
- **Logical error at large d** can only be sampled at small d, so the large-d figure is an extrapolation.
  *It is labelled as such. IonQ extrapolated from p ≥ 5×10⁻⁴ down to 10⁻⁴ the same way.*
- **Proof gaps.** FormalRV's VM and its qec→sysprog step have no end-to-end theorem, and our composition
  checker is code, not Lean.
  *The ladder says so. Nothing is called "proved" that is not.*
- **Two FormalRV sessions are busy now.**
  *FormalRV is read-only from this repo. Any change there is proposed to them, not made.*

## 5. Decisions needed (from the user)

- **D1, source maps.** Option (a): ask FormalRV to emit an opt-in provenance sidecar per pass, giving
  line-level stepping at every layer. The languages do not change. Option (b): keep FormalRV untouched
  and accept gadget-level stepping.
- **D2, physics for Goal 2.** Option (a): only today's pinned `physics@2` (Helios-class). Option (b): also a
  second pinned "aspirational" profile in the style of IonQ (p₂ = 10⁻⁴, 200 µs POC), clearly labelled.
- **D3, first scale target.**
  - GE2021: RSA-2048 on surface code, already VM-validated in FormalRV.
  - Gidney2025: yoked surface code, about 1 M qubits.
  - OratomicUs2026: LP24 lanes, qLDPC, the closest to IonQ's code style.
- **D4, `.alg`.** Option (a): keep it as the hand path (recommended). Option (b): retire it once FormalRV
  programs run end to end.

## 6. What this plan does not change

- FormalRV's languages, passes, VM and files. QCCD only *reads* them at a pinned commit and *writes* input
  files (`.machine`) in this repo.
- Today's gadget pages, `.alg` / `.floor`, GIR 0.2 and their tests.
- The order of gates on any qubit.
