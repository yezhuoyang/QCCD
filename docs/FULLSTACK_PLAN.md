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

**S5c — done.**
- **The page** (`qccd/gadget/web/fold.js`):
  - It keeps the root inline and loads each macro from `macro/<id>.js` the first time the time on screen
    needs it.
  - It expands a 4 s window around the playhead into the GIR 0.2 the page already draws, and expands the
    next one as the playhead leaves it.
  - Every instruction carries its call chain. The program line lit is the outermost call's; the Stack
    caption names each gate down to its body line, and a guard inside a call reads its caller's outcomes
    by their names.
  - The timeline draws every call of the run as a box, at every zoom.
- **On the 3-hour modexp:**
  - the page is 3.8 MB, plus 757 macro files;
  - a seek anywhere in the three hours shows what runs there, seven gadgets deep
    (`ctl_modmulmcp › ctl_modmulinplace › … › ctl_majinv_3`);
  - a window takes 27 ms to expand once its macros are loaded, and 2–9 s the first time a region's
    files are read.
- **Checks at scale.** A run too large to expand is checked **per view**: each body's own rows, the
  blocks it was passed resting in their homes, its calls as boxes. `check_places` gained `initial`,
  `boxes` and `until`. A gap at a call's edge may take at most half the coherence budget, so the two
  sides of every edge together keep within it.
- **The composition** is checked from the rows and the macros' footprints, not from the calendars that
  placed them: no two calls of a body, and no call and the body's own rows, hold one place at once.
- **Agreement and mutations.** On Shor-15, DJ and the reuse program the per-view verdict is the
  expansion's. Each check catches its own fault: overlapping calls (composition), a block that does not
  come home (G2), dropped holds (G8).
- **The 3-hour modexp, checked.** All 11 place checks hold in each of its 758 views (51 s), and the
  composition holds (3.7 s). Four checks in G10/G11 used to scan every message or event per item:
  quadratic, which no flat run had shown. They are indexed now, the same verdicts in 1/50 of the time.

**S5d — rotations.** FormalRV leaves `ppmrot(±π/2ᵏ)` as a synthesis stub (`tSynth[k]` magic states, no
circuit). QCCD supplies its own: the angle π/8 is a T state from a factory; finer angles are injected
rotation states consumed by a Pauli-product measurement, with the doubled-angle correction repeated until
it reaches T (repeat-until-success). This is exact, so the distribution check still applies.

**S5d — done.**
- **The rotation source** (`qccd/gadget/leaves/rotation.py`, master `rot1`, category "Rotation
  Source"):
  - one fresh ion in, `reset · H · RZn` at the dock, one ion out holding `RZn|+⟩`, for n = 3…10 and the
    inverses (16 ops);
  - the pharmacy's flow-verified `inject` encodes it, as it does the factory's T state;
  - each op is replayed as hardware and its circuit simulated as a state vector: fidelity 1.0;
  - the circuit IR gained `RZn` / `RZn_DAG`, non-Clifford like T.
- **`prep q Rn` / `prep q Rndg`** is a town instruction (rotation source → pharmacy).
- **The lowering** of `ppmrot(±π/2ᵏ) P`:
  - prepare `RZ(k−1)|+⟩` (a |Ȳ⟩ at k = 2, the factory's T state at k = 3), measure `P̄ ⊗ Z̄` against
    it, read it in X, frame `P̄` on that outcome;
  - on the product's outcome 1, the same rotation at k − 1;
  - checked first as matrices over every branch, sign and k (P = Z, X, ZZ, ZX, XZZ), and now a test
    on the town's own simulator;
  - FormalRV's open synthesis tables (`tablemax(synthBits, …)`) only lay out the stub's records,
    which QCCD names itself, so they read as 0.
- **What FormalRV's own `Examples/Shor` needed besides:**
  - the link rules read `!ctl!g` names and the one-trip loops by which the `.qec` binds a descending
    loop's variable;
  - a gadget whose gates FormalRV merged, or which it copied under `ctrl`, is linked by prefix
    differencing like the program. All 3,401 reached `.qppm` statements link to a `.prog` line;
  - the `.prog` simulator gained `ctrl`, qubit maps (`@ shift(m)`), descending loops, FormalRV's `^`,
    and a sparse limit in place of a qubit limit. The witness simulates in 1 s;
  - `--input Q=1`: the basis state the spec `assumes` (`|rev(bits, 1)⟩` on the work register). From
    it the phase register reads 0000 or 1000, half each: order 2, as it should for a = 2, N = 3.
- The wide machine gained two rotation sources.

**FormalRV's own Shor, end to end, unmodified** (`Examples/Shor`, its witness a = 2, N = 3, 3 bits,
4 exponent bits; `python -m qccd.fullstack run …/Shor.prog --specialize m=4 --specialize bits=3
--specialize N=3 --specialize a=2 --specialize ainv=2 --specialize rec=19 --fold --floorplan
fullstack_wide --input 6=1`, 846 s; without the six `--specialize`s the lowering stops at an unbound
`m`):
- all 9,858 `.qec` statements lowered and linked to their `.qppm` statement, and every `.qppm`
  statement to its `.prog` line;
- 577,245 town instructions, 0 refused, **2.96 hours** of machine time;
- **semantics:** the gadget program's results are exactly the `.prog`'s (0000 and 1000, half each)
  and its states agree, fidelity 1.0, at most 7 live branches (549 s);
- **checks:** all 11 place checks hold in every view, and the composition holds;
- **the page:** a seek anywhere names the `.prog` gate (e.g. `CCX(a, b, cw)`, UMA.prog:2), its PPM,
  its `.qec` merge, the gadget instruction and its eight-deep call chain, the Clifford step and the
  hardware instruction (`move 7 hops … 37.876 ms into ppm.ZZZZ`).

**What the composition check caught.** In this run it found 15 boxes overlapping their caller's own
trips. The fitting stopped scanning a footprint at a rounding difference (1e-11 µs) and then took
that for "no shift". Now a conflict must exceed the tolerance; there is a regression test.

**What the scale caught.** `merged.distribution` precomputed, for every instruction, the set of all
blocks named later: billions of entries here. Now it uses the last use of each block. The run was
watched for memory, and stopped once before it got there.

- **Shor from FormalRV's own example**, unmodified, next to the hand-written Shor-15.

### Slice S6 (user, 2026-10-26): nothing virtual; the hardware program is a language

The user's two requirements:
1. *Every single detail must be devirtualized, including how the magic state factory is compiled.*
2. *Extend our hardware language (TSIR) to a program that works on a given architecture, where each
   hardware instruction names the gadget it belongs to: modular and scalable.*

**What is still virtual today.**

| part | today | after S6 |
|---|---|---|
| a place's op (prep, bench, memory, junction, …) | TSIR on its device, verified | unchanged; each TSIR instruction says which step of the op it is |
| **the factory** | TSIR from `ring_circuit` over a gate list that `rm15_circuit()` builds in code; no line of the TSIR says which gate of the protocol it is | the protocol is a **source program** (`rm15.circ`, one gate a line); the factory's compiler tags every gate, reset and measurement with its source line; a check pairs them in order (the factory keeps gate order) beside the state-vector check; the page names the protocol line |
| the rotation source | TSIR, no source | the same: `rz<n>.circ` |
| a road | a carry timed by replaying `chan<L>` | a call of the road's own leaf gadget `chan<L>.transit` (its TSIR) |
| the decoder, the archive | modeled latencies | unchanged: classical, modeled, and called by name (not hardware) |

**TSIR modules (the language).** TSIR stays one concurrent cycle per instruction. It gains modules,
in a text form beside its JSON:

```
tsir modules 1
architecture fullstack_wide                 # the .floor the program runs on

leaf msf15.produce                          # a LEAF GADGET: one op of one place master
  master msf15                              #   TSIR on that master's device, from its home placement
  source rm15.circ                          #   the program it compiles
  port in in m0                             #   doors, ions in crossing order
  port out out m0
  simd rotate_cw L0 +3
  simd dock f3 S1->A1
  gate CX f3 f12 @A1      ; rm15.circ:21    #   every gate names its source line
  measure f4              ; rm15.circ:52
  cool
end

gadget ctl_maj_3 (arg0 arg1 arg2 arg3)      # a COMPOSITE GADGET: what one gate of the program does
  homes mem12 mem13 mem19 mem20             #   its blocks' memories (the context it was scheduled in)
  ins 7 "ppm Z arg0 Z arg1 Z l3 -> o4"      #   the program instruction the rows below carry out
    at 135.0 bench2 ppm.ZZZ  in_a=arg0 in_b=arg1 in_c=l3     # a leaf gadget at a place instance
    at 140.5 street3 chan12.transit l3 bench2.out_c -> post1.in
    at 160.2 decoder decode window 12        # classical
  call 900.0 when_3 (arg0)                  #   a composite call: its own rows, shifted
  hold arg1 mem13 from 1300 every 413230 n 4
end

program shor on fullstack_wide
  call 0.0 ctl_modmulmcp (q18 q21 q4 …)
end
```

- **Every hardware instruction is in exactly one gadget.** A leaf body is TSIR; every row above it
  names a place instance and a gadget.
- **Modular.** A leaf gadget is defined once per master op; a composite once per context (the folded
  schedule's macro); a call is one line.
- **Scalable.** FormalRV's 3-hour Shor is the same size as its folded schedule, one file a gadget,
  loaded as the page needs it.
- **It is the source of truth.** The writer turns a folded schedule into modules, and the reader turns
  modules back into the schedule the checks read; the round trip is tested. The page's Program pane
  gets a **Hardware** view, and the Stack's hardware row cites `file:line`.
- **The up-bridge:** derive the `.machine` from characterized macros.
- **The Goal 2 board**, once an entry can be graded end to end.

**Status (2026-10-26).**
- **S6a, done (`70aed64`):** the factory compiles `rm15.circ` line by line.
- **S6b, done (`9ba1e90`):** TSIR text.
- **S6c, done (`83bbc5a`):** TSIR modules (`qccd/gadget/modules.py`). The round trip is
  exact, FormalRV's Shor included. The page's Stack has a Module row.
- **S6d (user: "the roads look too straight and standard; are they modeled like a
  shuttle inside a gadget?"):** roads are drawn and replayed as their `chan<L>` devices,
  and junctions as `junc9`. Heat is followed across the town (`heat.py`, GADGETS.md
  §4.1). It found used ions bound for a far depot passing the ion-loss limit, unseen
  because every op had assumed a cold arrival. The user decided that junctions pass a
  bundle one ion at a time, with the queue on the road's sites. Places with lasers now cool
  bundles as they come in, readouts recool, and the scheduler routes by heat and makes a
  cooling stop when it must. Every ion road on today's floors is
  straight, so `chan<L>` is its exact geometry. A bent road would still be priced as
  straight: the corners are dropped in `synth.flatten`, and the cost model charges a
  corner like a straight step. (Closed by S7b, below.)
- **S7 (user: "everything, especially the shuttling between gadgets, still follows the basic
  rules -- DACs, electrodes -- make sure the rules apply to the hardware program when there are
  gadgets").** Every carry is one program on its road's device, judged by R1-R23. A door's
  stub is driven by one side at a time. The schedule is timed by those programs. This is rule
  H1, and the machine's DACs and electrodes are counted (GADGETS.md §4.2).
- **S7b (user: "close the open gaps").**
  - A road is its real geometry: a bent road is its own conveyor, with a site at every
    corner. A corner splits a shift into one cycle per direction (R22).
  - The page plays every carry's compiled program.
  - Shared planes: `plane NAME dacs|lasers MEMBERS` in the floorplan. Members take turns,
    with a store-and-forward wait between a trip's roads on one plane, and turn-taking
    refresh rounds. Rule H2 judges the composed program, and the census counts a shared
    plane once (GADGETS.md §4.3). A bus of every road saves 55% of the town's DACs at
    7-20% more time.
- **Published (user, 2026-10-27: "full stack should be the main purpose and theme for the
  gadget page").** qccd.academy/gadgets/ opens on the full stack: its runs in one table, its
  layers, its machine, what is and is not checked. `gadgets/fullstack/<key>/` is each run's
  design page with its Stack, built by the site (`qccd/gadget/site.py`, `FULLSTACK`); the
  place catalogue moved to `gadgets/places/`. The user approved publishing FormalRV's own
  examples with their sources.

  | run | `.qec` linked | gadget instructions | machine time | computes | checks |
  |---|---|---|---|---|---|
  | CX (`cnot.prog`) | 62/62 | 27 | 311.7 ms | exactly CX, 64 branches | 13 place, 25/25 machine |
  | Deutsch–Jozsa (FormalRV's) | 259/259 | 94 | 1.01 s | distribution and states | 13 place, 25/25 machine |
  | Shor for 15 (`shor15.prog`) | 1,551/1,551 | 650 | 9.02 s | distribution and states | 13 place, 25/25 machine |
  | FormalRV's Shor (folded) | 9,858/9,858 | 577,245 | 2.92 h | distribution and states | 13 place in each of 792 views, composition |

  The section build takes about 25 minutes, 14 of them FormalRV's Shor
  (`python -m qccd.gadget site --fullstack small` leaves it out).

### Slice S8 (user, 2026-10-29): Shor at scale, toward RSA-2048

The user's goal: *"make the Shor15 scale to larger size, eventually Shor2048 ... How to represent
program in a symbolic compact way so it actually fit in the browser while still is correct, how to
make the end to end compilation all the way from QEC stack to actual QCCD device scale. One very
very key principle is make everything localized: there must be many many repeated structure, that
you don't need to recompile, and don't need to store twice (magic factory / syndrome extraction /
etc). ... If we don't immediately get to Shor2048, at least I want to know what is the largest
scale that is achievable."*

**Where the pipeline stood (measured 2026-10-29, FormalRV's own Shor, N = 2^(bits-1) - 1 style
witnesses).** FormalRV's `.qec` is **the same 11,978 lines at every size**: its gadgets keep their
loops and parameters. Everything that grows is QCCD's:

| bits (N) | `.alg` lines | gate defs | lowering | folded schedule | per-view checks | macros (reused) | GIR | machine time |
|---|---|---|---|---|---|---|---|---|
| 3 (3) | 19,739 | 790 | 12 s | 400 s | 258 s | 791 (51) | 298 MB | 2.9 h |
| 4 (7) | 37,737 | 1,545 | 13–20 s | 687 s | — | 1,547 (121) | 564 MB | 7.4 h |
| 5 (15) | 65,259 | 2,716 | 34 s | — | — | — | — | — |

Three things grow with the run: `qec2alg` unrolls every loop; it lowers a call once per value of
*all* its arguments, the modular multiplier's constant included (61 `prepMasked` bodies at 5
bits), and a repeat-until-success rotation becomes a `when_m<k>` gate per outcome (2,513 at 5
bits); and the folded schedule keys a macro by its blocks' absolute memories, so almost nothing is
reused. FormalRV's compiler itself stops at 64 bits (`int_of_string`: OCaml's 63-bit integers).
**The largest Shor that compiles end to end on the device today is about N = 15 (5 bits).**

**The design: the run is never written down; the program is.** Six layers, each the size of the
program, not of its execution.

1. **The symbolic program** (`qccd/fullstack/scale/program.py`, done). The `.qec` lowered once
   per *structure*:
   - a loop stays a loop;
   - a gadget is keyed by its structural parameters only (those that set a trip count);
   - a decision on data (`if bit b of cst`, which FormalRV writes as a 0-or-1-trip loop) is a
     **slot**, reserved either way, so every gadget takes the same time whatever its constant;
   - a straight run of statements is a **template** with its blocks as parameters, stored once;
   - a rotation is one node.
2. **The lane machine.** A town of repeated districts, one per register position: the data
   memories, a bench, a pharmacy. The controls the program fans out (`__cw`, `__anc`, the flag)
   ride a carriage along the adder's sweep, so every step of every adder has the same local
   geometry.
3. **Templates compiled once.** Each template, and each one-district move of the carriage, is
   scheduled once by the town's own scheduler on a two-district fragment. It is checked there
   once (G-rules, machine rules, heat), and becomes TSIR once. A call is a translation in space
   and an offset in time.
4. **Time as an address.** Instances run one after another, so an instant is a path of loop
   indices, found by descending the tree: a loop whose body's duration does not depend on its
   index divides, and an affine one is an arithmetic series. Integers in ns; RSA-2048 runs
   longer than 2^53 µs.
5. **Checks by induction.** Every template is checked once on its fragment; every loop's
   interface (iteration k's end against k+1's start) once; the composition is sequential.
   Expanded wholly at small sizes, the existing checks must agree.
6. **The page resolves an instant.** It loads the tree and the templates (kilobytes) and
   expands only the instance under the playhead.

**Status of layer 1 (2026-10-29).** The symbolic program, from FormalRV's *generic* `Shor.qec`
evaluated by QCCD with exact integers (a power under a remainder taken modulo), is
**instruction for instruction** `qec2alg`'s lowering: 577,244 instructions at 3 bits, 1,499,128 at
4, and 3,074,914 at 5 (Shor for 15). Those runs are lowered from the `.qec` FormalRV specialized
itself; `tests/test_scale_symbolic.py` holds the 3-bit case. It is the same size at every key
size:

| instance | logical qubits | symbolic program | built in | instructions executed (counted, not expanded) |
|---|---|---|---|---|
| 3 bits (N = 3) | 23 | 9 templates, 25 gadget instances, 78 nodes | 0.15 s | 607,820 (slots reserved) |
| 16 bits | 127 | same | 0.15 s | 1.24 × 10⁸ |
| 256 bits | 2,047 | same | 0.15 s | 5.35 × 10¹¹ |
| 1024 bits | 8,191 | same | 0.22 s | 3.43 × 10¹³ |
| **RSA-2048** (2049-bit registers, m = 4096) | **16,391** | **same** | **0.23 s** | **2.75 × 10¹⁴** (3.3 × 10¹³ PPMs, 1.1 × 10¹³ T states) |

The nine templates are FormalRV's controlled MAJ, UMA and MAJ⁻¹ steps (103 PPMs each), a
controlled swap (61), a Toffoli (21), a CNOT, an H, a SWAP and the final readout. Every one of the
10¹⁴ instructions is an instance of one of them, or of a rotation chain.

**Found on the way:** the `.alg` parser tagged a call's locals by the gate's name followed by the
call's number, so `when_m5822` call 1 and `when_m582` call 21 were one block. Every 4-bit run's
expansion failed on it. A name ending in a digit now takes a separator.

**Status of layer 3 (2026-10-29): compiled once** (`qccd/fullstack/scale/compile.py`). Each
template becomes a gate, with its guarded groups (a T gate's S correction) written as gates called
under their guard, as `qec2alg` writes them. It is scheduled once by the folded scheduler on one
district (`fullstack_wide.floor`) and checked there by the place and machine rules.

- **Coverage:** all 9 templates and the 12 rotation heads (resource Y, T or a rotation source, ×
  sign × one or two factors) hold **G1–G11, H1 and H2**.
- **Cost:** about 2 minutes in all. The folded schedule took 400 s at 3 bits and 687 s at 4, and
  grew with the run.
- **Spans:** a controlled MAJ step takes 14.8 s on the machine, most of it waiting on T states (195 ms
  per factory run, 35 per step); a Toffoli 2.8 s; a controlled swap 8.9 s; a CNOT 0.27 s.

**The run's time, composed** (`program.duration_ns`: exact integers over the symbolic program, every
instance after the one before it, every slot reserved). At 3 bits the folded schedule gave 2.92 h;
the composition gives 2.87 h.

| instance | machine time (one district, one gadget at a time) | composed in |
|---|---|---|
| 3 bits | 2.87 h | 0.00 s |
| 5 bits (Shor for 15) | 15.6 h | 0.00 s |
| 64 bits | 4.5 years | 0.01 s |
| 256 bits | 288 years | 0.05 s |
| 1024 bits | 18,489 years | 0.18 s |
| RSA-2048 | 148,191 years | 0.36 s |

That number is honest for *this* circuit on *this* hardware. FormalRV's textbook Shor executes 2 ×
10¹² controlled Toffolis; Gidney's windowed arithmetic needs about 6.5 × 10⁹. One ion-trap
Pauli-product measurement takes 22–64 ms and a T state 195 ms, and nothing here runs in
parallel. The co-design levers are all visible in the composition: parallel factories, districts
working at once, and a windowed program from FormalRV.

**Not yet:**
- moving blocks between districts: each template is compiled with all its blocks in one district;
- resolving an instant to its hardware instruction;
- the page.

**S8, second round (user, 2026-10-29: "We definitely need to support advanced Shor's algorithm
and parallelism. ... all System call service (Especially Lattice Surgery) must now be compiled in
details").**

- **Lattice surgery in full.** FormalRV's system layer writes a merge as the product checks
  (an ancilla against each patch's logical support, three times), then `rounds` rounds of every
  patch of the merge (`surfaceRound` per patch), then decoding and frame updates. QCCD had kept
  only the first part, the bench's probe, and absorbed `extract … rounds=r` as "the bench's own
  rounds". Both lowerings now emit it: `qec2alg` (every published run) and the symbolic program,
  still instruction for instruction the same. Each `extract` becomes `se b 3` at a clinic for
  every block of the merge, in its order. A single-factor merge that becomes a destructive
  readout drops its rounds, since the block is consumed.
  - FormalRV's Shor at 3 bits is now 758,494 instructions (from 577,244); its `.alg` is 25,294
    lines.
  - The 9 templates still compile once and hold every rule.
  - RSA-2048 (textbook) executes 3.6 × 10¹⁴ instructions.
  - The published pages are NOT rebuilt yet.
- **Parallel loops in the time model.** A loop FormalRV marks parallel (`__par`) runs its
  iterations at once, each on its own district: it takes as long as its slowest iteration.
- **The advanced Shor: FormalRV's Gidney–Ekerå 2021 audit at RSA-2048** (`GE2021.qec`,
  distance 27, `Resource/Inputs/RuntimeGap/execution.inputs`; `tests/test_scale_ge2021.py`).
  - **The builder grew to read it:** table reads (`T[...]`, contents optional);
    destructive `measure`; structural guards decided when an instance is lowered; recursive
    gadgets keyed by the depth their guards read (the unary lookup); and FormalRV's
    fan-out guards (`rv_fan_g`).
  - **Size and count:** 19 templates, 3,931 gadget instances and 23,010 nodes, built and
    counted in about 13 s. The run executes 4.9 × 10¹² instructions and requests 4.89 × 10⁹
    CCZ states (every data slot taken), against the paper's 2.7 × 10⁹ Toffolis.
  - **Still to lower, in survey mode:**
    - a Pauli product over a whole register (`merge … * PROD i …`, up to 2,049 blocks):
      lattice surgery across many districts;
    - |AutoCCZ⟩ states, which no place makes;
    - classical parities, record copies into source slots, guards on both, and records written
      by another call. These belong to the archive.

**S8, third round (2026-10-29).**

- **|AutoCCZ⟩ states: the CCZ factory** (`msccz`, `qccd/gadget/leaves/ccz.py`;
  `tests/test_gadget_ccz.py`). The user chose a new factory. It runs 8T→CCZ on the [[8,3,2]]
  cube code, then makes FormalRV's nine-site AutoCCZ state. It is verified as a place, and
  FormalRV's own Toffoli consumption through it is CCX in all 4,096 branches. An `autoccz`
  instruction runs it and encodes each of its nine outputs at a pharmacy.
- **Lattice surgery over a register: cat states** (`catbench_d3` + `depot9x`,
  `qccd/gadget/leaves/catbench.py`; `tests/test_gadget_cat.py`). The user chose "Cat chain".
  - **How a merge runs.** A `ppm` over more blocks than a bench's four gives every block a cat
    bench of its own. All benches open probes in |+⟩ (`cat.open`). Each pair of neighbouring
    benches is fused by its own bundle of three links, all pairs at once: `cat.lend` copies Z
    of the left probes, and `cat.check` reads it against the right ones. Each probe then
    couples to its block and is read in X (`cat.couple.P`). Three cats are made at once, like
    the bench's three rounds.
  - **Outcome and frames.** The outcome is the parity of round 0's probe bits. A check that
    reads 1 flips the probes to its right by X, and their couplings carry it onto their blocks
    as P̄. The archive writes that into the blocks' frames, so nobody waits for it.
  - **Parallel, measured.** A merge's ions take 37.6 / 39.7 / 40.0 / 40.7 ms over 2 / 3 / 4 / 5
    blocks with one shared depot. With a depot beside each bench they take
    35.3 / 36.0 / 37.2 / 37.2 ms: flat, where the first design, a sequential link walk, grew by
    about 10 ms per block.
  - **Signed off.** The flat sign-off proves the outcome is the product over five blocks. It
    also proves the frames: X̄⁵ measured on |0̄⟩ blocks, then every Z̄ᵢZ̄ⱼ = 0. Dropping a bit or a
    frame breaks it.
  - **Circuit distance, which the user does not ask for.** The first program measures 2, the
    four-door bench's own. The frame program measures 1: one probe holds a whole logical
    operator of its block, so an X fault between fusion and coupling lands on the block as P̄.
    Shor's construction would give every support ion its own probe.
- **The symbolic program: a `Cat` node** for `merge … * PROD v … if cond . P[fam[idx]]`. It is
  one node however many blocks it names; its factors' bits are data, and it carries its
  extraction rounds on every block. Count: 5.19 × 10⁸ register-wide merges and
  1.06 × 10¹² block couplings (every slot taken), almost all of them the unary lookup's.
- **A lowering gap, fixed in both lowerings.** `prepare |+> -> LogicalAncilla[…]` and
  `prepare |0> -> LogicData[i][0]` (after a readout) prepare logical blocks. Both lowerings had
  taken every `prepare` for a merge's own probe ion and dropped it: 45 sites in 14 GE2021
  gadgets, and CainXu2026 too. The textbook Shor never writes one.
- **Semantics.** GE2021's own `cPrepMasked` (n = 4), with its `Cat` node and prepared
  ancilla, is expanded and run in the ideal simulator. For all 16 constants and both control
  values, the register reads the constant exactly when the control is 1.
- **Still to lower:**
  - **classical parities, 1,237 sites:**
    - 899 are copies of one record;
    - 336 are the AND of two;
    - 4 are table-masked XORs over 2,048 source records;
  - **source slots and the guards on them;**
  - **records written by another call.**

  Copies and ANDs fold into the guards that read them (`if a & b:`). Source slots and foreign
  records need a global record table.

**S8, fourth round (2026-10-29): GE2021 lowers completely.** Nothing is left in survey mode.

- **The classical layer is the archive's, with no instruction.**
  - A classical bit is an XOR of conjunctions of parities of outcomes. One term is an `.alg`
    guard (`a ^ b & c`). A Pauli frame on several terms is that Pauli once per term:
    P^(t₁ ⊕ t₂) = P^t₁ · P^t₂.
  - FormalRV's `parity` (copies, ANDs, XORs over a loop, table-masked XORs over source slots),
    `source c[k] <-`, guards on source slots, and frames on several records all lower to such
    formulas.
  - Records are read through a table keyed by FormalRV's own addresses: a call's records live
    at its `rv_recbase` plus their index in it. That is how the spacelike adder hands trip k's
    outputs (local 1411 and up) to trip k+1 (local 0 and up): 37 reads that are not local.
  - Two silent gaps found on the way:
    - 42 `frame on c[a), c[b)` lines read an XOR of two records, but the builder took only
      the first;
    - re-preparing a data block reused its first generation.
- **`bit` (`.alg`): a bit the archive computes.** It is written `bit (a & !b) ^ c -> f`, and a
  guard reads `f` like an outcome (`tests/test_gadget_bit.py`).
  - The spacelike adder picks a readout basis by an XOR of conjunctions of earlier outcomes
    (`when c: measure Z` / `when ¬c: measure X`); no guard can say that. The builder computes
    such a guard as a bit first: 4 lines in 2 templates, 4.3 × 10⁹ evaluations at RSA-2048.
  - Exact branch enumeration follows it. The symbolic simulator takes it when affine and
    refuses a product by name. The town schedules it as an archive job after its outcomes are
    decoded. The flat sign-off, which folds outcomes as parities, says it cannot follow a
    product.
- **Compiling GE2021's 20 templates once on a district (CCZ factory, 20 memories):**
  - **The harness grew:**
    - a template's outcomes measured before it are taken as decided (a guarded group reserves
      its places either way);
    - a gate may read out a block it was passed (`fold`: `Macro.gone`), and G2 accepts its
      ions ending at a depot.
  - **7 compile and hold all 13 rules.**
  - **5 fail one rule:**
    - G7 (3): a block's next instruction starts before the call holding it is done;
    - G8 (2): a block idles 1,084 ms without extraction, against a 1,000 ms budget (the
      compile path runs no refresh pass).
  - **3 make a block that outlives the template.** An ancilla crosses into the next template
    when a register merge splits the run, and a gate returns only its parameters.
  - **5 read an outcome measured inside a guarded group** (the adaptive-basis readouts).
- **S8, fifth round (2026-10-29): parallel time, the advanced audits, the first codesign.**
  - **The model.** The run's parallel time is a dataflow over each compiled template's PORTS
    (`qccd/fullstack/scale/parallel.py`; `tests/test_scale_parallel.py`). An instance starts as
    soon as its blocks are free and its outcomes recorded, its schedule shifted whole. Loops
    reach a closed form per key, in whole nanoseconds, checked in the middle and before last,
    and undone when refuted. It is exact against full iteration on every size checked.
  - **Textbook Shor** gains only 1.06× (one dependency chain).
  - **The advanced audits.** GE2021 compiles completely. Gidney 2025 (FormalRV/Audit/Gidney2025,
    paper machine) lowers completely: program parameters are global, and a call or loop under
    a guard is a `When` body (`tests/test_scale_gidney2025.py`).
  - **Gidney 2025 at RSA-2048, one district, sequential:**

    | Design | Time |
    |---|---|
    | Baseline | 883 years |
    | + syndrome rounds in each block's memory (`rounds_at="memory"`) | 597 years |
    | + transversal CNOT for the lattice-surgery CNOT | 178 years |

    - Rounds were at least 78% of the time, queued at the district's one clinic.
    - The lattice-surgery CNOT template was 74.5% of the run. It is proved CX in every branch,
      and takes 51 ms as a transversal CNOT against 0.89 s.
  - **Next bottleneck:** the rounds of the templates that are a Hadamard (three |Ȳ⟩ merges), a
    guarded CZ, and the Toffolis' AutoCCZ consumption. A transversal H̄ (transversal H plus a
    relabelling of ions, which shuttling gives) is the candidate place.
  - **Fixed since: the parallel run's memory.** A call's ancillas are dropped when it returns
    (bac81b8). A record table is kept only if something reads it by address. A closed form's
    records and source slots are strided runs, read by arithmetic (e15a05d).
- **S8, sixth round (2026-10-29): the critical path, and codesign by Amdahl's law.**
  - **The user's decisions.** Merges keep the paper's 25 rounds ("paper faithful"). Gadgets and
    traffic may be redesigned, bottleneck first.
  - **One-letter products by transversal CNOT.** A product of all-Z̄ or all-X̄ factors, over
    one to four blocks, is measured by transversal CNOTs into a fresh ancilla block and that
    block's destructive readout. That replaces the merge and its rounds.
    - The identity is proved once per letter and size (exact state vector).
    - Every Clifford template rewritten this way is also proved equal to its original, in
      every branch.
  - **Gidney 2025 at RSA-2048:**

    | Design | Sequential, one district | Parallel, unlimited districts |
    |---|---|---|
    | As compiled | 883 years | 86.8 years |
    | Rounds in memory + transversal CNOT + one-letter products | 125 years | 26.0 years |

    - The codesigned parallel run averages 4.8 districts busy.
  - **The critical path.** Each top template is halved in turn and the parallel time is read
    again. The bulk templates (the CNOT copies, the power products) save 0.2–3%. Two templates
    save 12–13% each, so each holds about a quarter of the critical path:
    - `gidneyInterior`, the carry's Toffoli through |AutoCCZ⟩;
    - `gidneyMeasInteriorRev`, its measurement-based uncompute.

    The wall clock is the ripple-carry chain of Gidney's adder. Its steps still carry mixed
    X̄Z̄ products, each a merge with 25 rounds on each block.
  - **Next:** a transversal-Hadamard place (H on every ion plus a relabelling, done with the
    bay's dock and ring). It makes every mixed product a one-letter product, and so takes those
    merges off the carry chain.
- **S8, seventh round (2026-10-29): geometry and the compile unit.** The user: "optimize the
  gadget geometry / merge smaller gadgets to larger gadgets / design better shuttle highway ...
  even reconfigure gadget layout".
  - **The Hadamard bay** (`hbay_d3`, `qccd/gadget/leaves/hadamard.py`; `.alg` primitive `ht`,
    since the standard gate `h` is S̄·√X̄·S̄). H on every ion, and the block leaves a quarter turn
    round, which is H̄ on the rotated d=3 code. The GIR records the block's new ion order
    (`turns`), and the flat check reads it; without it the same schedule is distance 1.
    `tests/test_gadget_hadamard.py`.
  - **Two rewrites, each proved by the exact simulator:**
    - a mixed product ∏Z̄_z ∏X̄_x is one ancilla: |0̄⟩, CNOTs from the Z̄ blocks into it, H̄ on
      it, CNOTs from it into the X̄ blocks, read X̄ (every letter pattern of 1–4 blocks);
    - FormalRV's H̄, three |Ȳ⟩ merges S̄, √X̄, S̄, is exactly `ht`. Its six outcomes steer only
      its own corrections. Gidney 2025 reads no record by address, only source slots, and no
      source slot reads them.
  - **What a template's critical path is made of** (walked back op by op): roads 2–8%, gates
    about a quarter, a memory's release and store about half, and the rest one instruction
    waiting for another's unrelated block. So better roads would not pay.
  - **Two scheduler opt-ins** (`City(free_at="block", prefetch=True)`, threaded through
    `schedule_folded` and `compile_template`; the defaults are unchanged):
    - a block is free when what an instruction did to IT is over. Before, a CNOT's ancilla
      waited while its partner was stored. G7 now compares each block's own stretch, which is
      what the rule says ("each block's instructions run in program order");
    - a CNOT's operand resting in memory starts out as early as its release takes, so it
      arrives with the other. `tests/test_gadget_free_at.py`.
  - **A lowering bug, found and fixed.** Reading the Toffoli template's lines showed a
    lattice-surgery CNOT from a block into itself. FormalRV's `gidneyForwardProp` reads
    `for 0 <= i < nSeg: if i ≥ 1: gidneyInterior(i) else: gidneyFirst`. The guard reads only
    the loop variable, but the lowering made it a data slot, reserved either way, which had two
    effects:
    - the adder's first step was charged on every step (`gidneyFirst` and
      `gidneyMeasFirstRev`: 3.06e8 and 2.70e8 instances; once an adder, they are 1.20e7 and
      1.04e7);
    - the interior was lowered at i = 0, against its own `requires 1 ≤ i`. There the previous
      carry `(i - 1)*3 + 2` and `i*3 + 2` are both 2 (FormalRV's minus saturates), so they
      became one block.

    A guard that reads only loop variables and parameters is now structural (`Pred.structural`):
    it is taken only where it holds, in `expand`, `total`, `duration_ns` and `parallel_us`, and
    its body is lowered where it holds. A guard on data (`if bit b of cst`, a table's bits)
    stays a slot. Call memos are keyed by the arguments a count reads.
    `tests/test_scale_gidney2025.py` holds the fix. **Every Gidney 2025 figure in rounds five
    and six was computed before it** and overcounts the adder; the table below is all after it.
    The lesson is that `compare` against `qec2alg` is the only check of the lowering's aliasing
    assumption, and Gidney 2025 at RSA-2048 never had it.
  - **A door-aware memory** (`mem_d3f`, `qccd/gadget/leaves/doors.py`). It is the same ring,
    docks, ancillas and programs, with the ions resting where the doors are short as well as
    the round: entry 86 → 28 slots, exit 81 → 30, the round's contact batches 13 → 9. Store is
    24.1 → 18.2 ms, refresh 13.2 → 12.0 ms, release 9.4 → 5.5 ms. `tests/test_gadget_doors.py`.
  - **Lookups as fan-out calls** (scratch for now). The lookups' rows are 89% of the single
    CNOT calls: for each one-hot flag, a CNOT into every output bit the table sets. One call
    per row keeps the control out of memory between targets: 50.5 → 12.7 ms a target. The
    instructions are the same and so are the slots, and the number of CNOTs is checked equal.
  - **Gidney 2025 at RSA-2048, the fixed program** (paper machine, 25 rounds a merge kept):

    | Design | Sequential, one district | Parallel, unlimited districts |
    |---|---|---|
    | As compiled | 796.6 years | 82.4 years |
    | Round six (rounds in memory, transversal CNOT, one-letter products) | 95.3 years | 25.0 years |
    | + Hadamard bay (both rewrites) | 58.4 years | |
    | + a block is free when its own part is done | 52.0 years | |
    | + prefetch | 50.2 years | 9.46 years |
    | + lookup rows as fan-out calls | 37.4 years | 9.47 years |
    | + door-aware memories, every template recompiled on them | 34.1 years | 8.69 years |
    | + FormalRV's lattice-surgery CNOT recognized whole: a transversal `cx` | 29.4 years | 4.65 years |
    | + door-aware preparation and Hadamard bays, everything recompiled | 28.4 years | 4.31 years |

    Fusing the lookups halves the work (5.3 → 4.0 districts busy) but leaves the wall clock
    where it was: the lookups are off the critical path.
  - **The carry chain's CNOTs, whole.** The corrected interior template spent its second half
    on two lattice-surgery CNOTs, each measured product by product (three ancilla preps, four
    transversal CNOTs, three readouts). The idiom `prep L X; ZZ(c, L); XX(L, t..); read L Z` and
    its frames is exactly `cx c t..`, proved for one target and two. On the carry chain the
    interior goes 673 → 437 ms and the uncompute 505 → 229 ms, and `gidneyFirst` now holds G8.
    Every rewrite without an |AutoCCZ⟩ state is proved equal to its template whole.
  - **Door-aware preparation and Hadamard bays** (`prep_d3f`, `hbay_d3f`). The H bay's ions
    rested where `place()` puts them, for a syndrome round the bay never runs: `h` is 20.3 →
    12.3 ms. The prep bay is 30.0 → 23.5 ms.
  - **The rewrites are in the repository** (`qccd/fullstack/scale/codesign.py`,
    `tests/test_scale_codesign.py`): products, the H̄ triple, the CNOT idiom, the lookup fusion,
    and `exports` (which outcomes a call site hands on).
  - **The critical path after them** (halve one template, read the parallel time again): the
    interior holds about 37% of it and the uncompute about 31%. An adder step forward waits
    172 ms, and backward 161 ms; about 47 ms of each forward step is parking a block in memory
    (a store, with its syndrome round) between nearby uses. More preparation, readout and CNOT
    places help the backward step (161 → 133 ms) and not the forward one.
  - **How a block waits between uses: two opt-ins, the user's decision pending** (2026-10-30:
    "measure both, decide later"). The defaults are the rule as it was.
    `tests/test_gadget_park_policy.py`.
    - `City(park_round=False)`: a parked block rests without a syndrome round (`hold` on
      `mem_d3f`, 6.5 ms against a store's 18.2). It takes its round anyway once 200 ms have
      passed since its last (`ROUND_DUE_US`), and its refreshes count from that round. A decode
      written for the round a store would have measured is listed under the GIR's `noops`.
    - `City(park_within=K)`: a block whose next use is among the next K instructions that move
      ions waits where it is, instead of parking.
    - On small programs that park between uses, every policy keeps the outcomes and a fault
      distance of 3. The logical error rate, which fewer rounds raise, is not measured yet.

    | Park policy (on the last row above) | Sequential | Parallel |
    |---|---|---|
    | A round at every park (the default) | 28.4 years | 4.31 years |
    | `park_round=False`: a round only when due | 25.6 years | 3.79 years |
    | `park_within=6`: no park when the next use is near | 27.0 years | 3.99 years |
    | both | 24.8 years | 3.65 years |
  - **The doors are the bottleneck of every place.** Of a place op's time, the doors take 92%
    (H bay, 20.3 ms; the H gates 1.7 ms), 79% (transversal CNOT), 78% (readout), 45% of a
    memory store and all of a release. A ring takes an ion in with a ring turn (100 µs a slot
    crossed); `place()` set the homes for the round alone, so an entry crossed 86 slots.
- **A register-wide merge, measured:**
  - its cat states take 35.3 / 36.0 / 37.2 / 37.2 ms over 2–5 blocks, with a depot per bench;
  - its 27 extraction rounds take 534 ms on each block, at that block's district's clinic,
    every block at once;
  - about 0.57 s in all, whatever the register's size.
- **Eighth round (2026-10-30): one whole adder, concrete.** The user asked whether the time was
  "fully compiled to trapped-ion movements, even the T factory", and then: "please do not cheat,
  and everything should be concrete hardware instruction, there is no virtual qubit, virtual
  system calls".
  - **What the years above are.** Each template is compiled once to ions and checked, on a
    district of its own. The CCZ factory is part of that: 109 ms of ion operations inside the
    Toffoli template, then nine injections. The years are arithmetic over those templates:
    instance counts times spans, or a dataflow over their ports. That dataflow gives every
    instance a district of its own, with its blocks already in that district's memories and no
    transport between districts. The whole run has never been one schedule.
  - **The check.** Gidney 2025's 32-bit adder, as `gLoop1` calls it, written out as ONE `.alg`
    program. It has 3,448 instructions on 95 data blocks; every ancilla is a block of its own
    (717), and every outcome is one (1,367). Each forward step's bit, read by its uncompute,
    is the archive's `bit` over the outcomes that wrote it. Every frame keeps its guard. It is
    scheduled on one concrete machine: step I's district with 140 memories, so every block
    has a home. Scratch: `concrete.py`, `adder_whole.py`, `adder_compile.py`.

    | The same adder | Time |
    |---|---|
    | Composed, sequential (sum of template spans) | 22.5 s |
    | Composed, parallel (dataflow over ports; 2.9 districts busy on average) | 7.77 s |
    | **One concrete schedule on one machine: 648,370 events, 78,000 carries, G1–G11, H1, H2 all hold** | **19.29 s** |

    The parallel composition is 2.5x optimistic for this adder. No place is saturated (the
    preparation bay 34%, the CCZ foundry 16%). The time goes to the carry chain and to moving
    blocks between their homes and the places: some road or junction is in use 57% of the
    time, and some memory is storing 57% of the time.
  - **What going concrete found, each fixed:**
    - *Lowering* (`program.py`). Gidney's measured uncompute writes `source c[k]` from its own
      measurement and branches on it at once. The builder read the slot as written by an
      earlier call, so the fix-up's guard became an outside outcome. Each template took it as
      decided, so the fix-up ran unconditionally, beside everything else. Now the uncompute
      waits for its measurement: 204 → 244 ms and 191 → 231 ms
      (`test_a_source_slot_written_and_read_in_one_run_reads_the_bit_just_written`).
    - *Frames* (scratch writer). A gate reads only its own outcomes. A frame that reads both a
      guarded branch's outcomes and outer ones is placed where all of them are measured, with
      its whole guard. The template compile had dropped every frame's guard.
    - *An adaptive basis* (`fold.py`). FormalRV reads each CCZ routing pair in Z or in X, by
      an earlier outcome: two complementary guarded calls. After both, the block is gone.
      It was kept for ever: six blocks a Toffoli, until no memory was left (refused at step 5).
    - *A block read out inside a call* (`fold.py`, `place_checks.py`). Its memory's lease
      never ended, so the next block to rest there was booked at time infinity (a NaN).
      The caller's view now counts its ions out of the memory, each when it departed (G3),
      and stops answering for the block there (G8).
    - *Rounds in step* (`fold.py`). Every block passed into a call rested from one instant, so
      95 blocks sent the one decoder their syndromes at once (G11). Each block's rounds now
      come 2 µs before the previous block's (`REFRESH_STAGGER_US`); a round early never hurts.
    - *A bug of the second round* (1d2766c, found by bisect). `se` on a resting block ran its
      refresh without taking its turn on a shared plane (H2 failed in `test_planes`). It now
      takes its turn. A refresh on a busy plane takes the latest gap before it falls due, not
      the first one after (G8).
    - `tests/test_gadget_fold_branches.py` holds the adaptive basis, the lease, the counts and
      the stagger on a small program.
    - *Two more from the second round, found building FormalRV's full Shor for the site* (its
      expansion stopped on `KeyError: 57`). A body passed its calls the rest places it held,
      not the ones its own caller held. So a gate two levels down rested an ancilla in a
      memory where the top level had a block, and the gate between could never fit: five calls
      were refused. And a block left its memory with its last round at a call's start, then
      queued for the clinic, and met its next round 520 ms after the call began. A block that
      has rested `LEAVE_FRESH_US` (200 ms) or more now takes one round as it leaves, ending
      at its release (`tests/test_gadget_fold_context.py`).
  - **Next: the run itself, concrete.** A fixed machine of districts, each with its own
    register memories and work places. Each adder shape is compiled once per district as one
    schedule like the one above, and replayed by start time. Blocks that change districts ride
    compiled transports. No district per instance, and no formula.
- **Ninth round (2026-10-30): the whole program written concrete; one prime scheduled; and
  d = 35.** The user asked whether everything scales (memory on this machine, a browser that
  animates in detail, compile time), and on hearing the blocks were d = 3: "this is
  ridiculous. We need distance like 35."
  - **The whole RSA-2048 program, concrete** (`concrete.write_program`, dd215ad): one gate per
    distinct gadget call; the 21,640 primes are ONE gate (the classical controller supplies
    their table bits: primes 0, 1 and 21,639 write the same text). A prime reads one source
    slot nothing ever writes (it holds 0), and the inputs give no contents for `Tanf`/`Tbar`:
    FormalRV oddities, reported, not fixed here.
  - **Loops as gadgets** (3231301). The writer unrolled every loop in place, so `gLoop1`'s 214
    lookups were one body of 2,637,764 lines (439,556 calls): 4 h and 21 GB without finishing.
    A loop that calls no gadget is now a gate called where it stands (`fold=64`): a lookup's
    64 × 32 rows are one gadget on the same blocks every iteration. One prime: 2.69M lines →
    46,641; `gLoop1`'s body 8,346 calls.
  - **One prime on the 1,900-memory town, d = 3** (stopped, not finished). `gLoop1` scheduled
    concrete: 8,346 calls, **33,269 s** of machine time, in 29 min. With `gLoop3` (5,313 s)
    and `gLoop4` (1,254 s) that is **11.1 h for part of one prime** — so RSA-2048 at d = 3 on
    that town is over 21,640 × 11.1 h ≈ 27 years (concrete, a lower bound; the composed
    sequential figure was 24.8 years). Stopped in `gUnloop3` at 44 GB private: (1) a gadget
    is compiled once per placement of its blocks, and `gUnloop3`'s steps land on ~35 home
    sets, so each step's lookup subtree compiles afresh (one `powerProduct` context ran over
    an hour); (2) a replayed call copies its whole footprint (≤ 256 intervals a resource)
    into its caller's calendars. Remedies: stage a repeated step's operands into fixed work
    memories (one context, compiled once, shorter trips); compact a caller's calendars
    behind its frontier. Also fixed: a macro no longer keeps the machine's tables (~5 MB each).
  - **d = 35, tiled** (`qccd/compile/tiled_surface.py`, cfa110b, c5a9847). qccdc's search
    grows as ions^~3.9 (one round: 12 s at d = 7, 176 s at d = 11, 1,954 s at d = 15; about
    17 days at d = 35, extrapolated). A patch on `grid(cols + 1, rows + 2, spacing=2)` needs
    no search: data on the horizontal wires' traps, each check's measure ion at its face's
    centre, one junction from each of its data; a CNOT layer is two shuttle steps out (north-,
    then south-bound), the pulses, two back. 39 instructions a round at every distance; gates
    keep their order; the certificate is built from the instructions and check_cert.py and
    the proved Lean checker accept it.

    | | ions | written | every rule | R10 (both halves, Lean) | round (physics@2) |
    |---|---|---|---|---|---|
    | d = 11 round | 241 | 0.02 s | pass | 1.3 s | 5.6 ms |
    | d = 35 round | 2,449 | 0.14 s | pass, 6.7 s | 9.2 s | 5.6 ms |
    | d = 35 merge (71 × 35) | 4,969 | 0.4 s | pass, 22 s | 32 s | 5.8 ms |

    Under qccd-noise@2 the LER falls with d (memories, d rounds): 2.6e-4, 3.6e-5, 2.5e-6 at
    d = 3, 5, 7 (the last on 5 errors). Extrapolated (memory only), d ≈ 23–25 suffices for an
    RSA-2048-length run. **The user settled on d = 25** ("25 is about enough"): FormalRV's 25
    rounds per merge are then also the fault-tolerant count. At d = 25 a round is 1,249 ions
    (written 0.07 s, every rule 1.7 s, R10 4.7 s) and a merge 51 × 25 = 2,549 ions (0.2 s,
    6.3 s, 8.7 s); 5.6–5.8 ms a round, so a 25-round merge is ~0.15 s of machine time.
  - **Next.** The d = 25 machine: a d = 25 block is 625 data ions and junctions pass one ion
    at a time, so blocks should not travel. Stationary patches on a lattice with one seam
    row/column between neighbours; logic by lattice surgery (any rectangle or corridor of
    faces is tiled the same way), 25 rounds a merge; a T factory built from the same
    patches. The folded scheduler keeps its shape — a block's home is its patch, a macro a
    surgery gadget — and reserves corridor cells instead of carrying blocks. The viewer
    draws blocks zoomed out, ions zoomed in.

### Slice S9 (user, 2026-10-30; distance 11 since 2026-10-31): the stationary-tile machine

The user: d = 3 at RSA-2048 scale "is ridiculous"; then "25 is about enough"; then, after the
distance study below (2026-10-31): "use much smaller code distance, as the gate error rate of
trapped ion should be better than superconducting qubit -- can be slightly aggressive, be
optimistic about future trapped ion hardware progress". So the machine FormalRV's Gidney 2025
runs on is a **d = 11** machine on projected trapped-ion hardware, and every number from it is
concrete. `qccd.fullstack.scale.machine` holds the choice in one place: distance 11, physics
`qccd-physics-best` (the best published fidelity for each operation, each cited, the rest
Helios's), noise qccd-noise@2 at scale 1/2 (every error half of today's best -- the one
projection), 25 rounds a merge (paper-faithful, unchanged). Everything below is written for any
d and was built and checked up to d = 25.

**The machine.** A lattice of d × d patch tiles on the tiled grid (`tiled_surface.device`:
data on horizontal-wire traps, measure ions on vertical-wire traps), one seam row and one seam
column between neighbouring tiles: tile pitch d + 1 = 12 data columns. A block lives in its
tile and never travels -- a d = 11 block is 121 data ions, and a junction passes one ion at a
time. Ions move only inside a tile and across a seam, by the tiled compiler's steps.

**Why it scales where the d = 3 town did not.** Every tile is the same piece of grid, so a
gadget compiled on one neighbourhood is the same program, translated, on any other. A macro is
keyed by its *shape* (which tiles, relative to each other, and which seams), not by absolute
homes: the ~35 home contexts that made `gUnloop3` recompile its lookups cannot arise, and the
footprint of a replayed call is a translated copy of one list, not a fresh one.

**The gadgets, each a tiled program checked once (every rule, R10 with the proved checker):**

| Gadget | Program | Rounds |
|---|---|---|
| idle | a patch's syndrome round | 1 (a block refreshes every round) |
| Z̄Z̄ / X̄X̄ measurement | seam prepared, the merged patch's rounds, seam read | 25 |
| multi-patch product (Gidney's lookups and PROD merges) | a corridor of faces joining the patches (any face set) | 25 |
| CNOT | Z̄Z̄ with an ancilla patch, then X̄X̄ to the target, ancilla read (FormalRV's own lattice-surgery CNOT) | 2 × 25 |
| H̄ | transversal H, the boundaries turned by deformation over the seam and the neighbouring tile (Gehér et al. 2024), every data state moved one column back through a measure ion | 3 × 25 + 3 |
| prep / read | reset, or measure every data ion | 0 + 1 |
| T̄ / CCZ | magic states from a factory built of the same patches, injected | factory-specific |

**Steps, each with its exit test.**
1. The tiled compiler takes any face set (a corridor, an L, a merged pair) and the seam's
   preparation and readout; exit: a Z̄Z̄ measurement at d = 3, 5 passes every rule and R10, and
   its stabilizer flows give Z̄_A Z̄_B (the `qccd.gadget.logic` spec check).
2. The surgery gadgets in the table above, as leaves; exit: each passes every rule and R10 at the
   machine's distance and its LER falls with d at d = 3, 5, 7 under qccd-noise@2.
3. The patch-lattice scheduler: the folded scheduler with tiles for homes, shapes for macro
   keys, seams and corridor tiles for resources; exit: Gidney's 32-bit adder at the machine's
   distance on one concrete lattice, every place check.
4. The factory at the machine's distance (15-to-1 from the same patches first; Gidney's
   cultivation later).
5. One prime, then the whole program, concrete; the viewer draws tiles zoomed out and ions
   zoomed in.

**Step 3 in detail (2026-11-01).** One real call of the 32-bit adder in Gidney 2025 at
RSA-2048, expanded at the PPM level (FormalRV's own lines, no transversal-CNOT rewrite), is
15,593 instructions:
- products: 310 Z̄Z̄, 93 X̄X̄, 61 X̄X̄X̄ (CNOT fan-outs), 94 X̄Z̄ and 1 Z̄X̄Z̄;
- preparations: 185 |+̄>, 96 |Ȳ>;
- 31 CCZ resources and 746 readouts.

On the surgery machine a product is native: a corridor merge, 25 rounds whatever its shape.
So the program keeps FormalRV's products, with the rewrites that suit merges:
- **H̄ triples.** The 96 |Ȳ> are exactly 32 FormalRV H̄ triples, each one `ht` (the H̄ gadget),
  so no |Ȳ> state is needed.
- **The Toffoli's X̄ on a CCZ output.** 31 of the X̄Z̄ products take Z̄ of a fresh CCZ output.
  The factory turns that output with H̄ before delivering it, off the critical path, and the
  product becomes X̄X̄.
- **The uncompute's "measure X̄".** `ht p; ppm Z p; if o: x p` is `read p X; prep p Z`.
- **The uncompute's conditional CZ.** The other 31 X̄Z̄ are `prep l X; Z̄Z̄; X̄_l Z̄_p; read l Z`
  under an outcome. They take H̄ on the ancilla between its two merges (on the path, ~0.7 s
  each, half the time); a cheaper CZ is an open codesign item.

The lattice is *tile in a sea*: block homes on the even tiles (2i, 2j), every other tile a bus.
Every block then has bus tiles on all four sides (Z̄ on its top and bottom, X̄ on its left and
right), the H̄ gadget's neighbour tile, and a home for the ancilla of an idiom. The cost is four
tiles a block.

The scheduler runs the instruction stream in program order on each block and waits for each
outcome a guard reads to be decoded. For each instruction:
- a product routes a corridor through bus tiles that are free for its whole duration
  (breadth-first from each patch's attachment side, joined into a tree) and reserves them;
- `ht` reserves the bus tile and seam to the block's right;
- preparations and readouts take one round on the home tile;
- every block not in a gadget refreshes each round.
Each scheduled instance is a translated copy of a shape compiled once, which is the macro key
(every rule, R10 at the machine's distance). The schedule is checked: no two instances share a
tile, seam or face trap at the same time, and every outcome is decoded before it is read.
Exit, staged honestly:
- (a) the adder's CCZ-free half concrete: the final CNOT cascade and the 31 measurement-based
  uncompute steps;
- (b) the forward half once step 4's factory delivers CCZ states to ports on the lattice.

**Step 3 (a) done (2026-11-01): `qccd.fullstack.scale.lattice`.** The adder's CCZ-free half,
expanded from one real call in Gidney 2025 with `surgery_rewrite` applied, is 1,085
instructions. It runs on a 65 x 7 tile lattice: 95 block homes, bit i's three blocks in column
2i, 455 tiles, 438 of them used.
- **Schedule:** 589 gadgets (248 corridor merges, 31 H̄, 155 preparations, 155 readouts), and
  the check is clean: no tile holds two gadgets at once, every block's instructions run in
  program order, and every guard is decoded before it is read.
- **Machine time:** 1.517 s, with 37 gadgets running at once on average. A lattice-surgery
  CNOT's first merge, control with a fresh ancilla, depends on nothing earlier, so the 31
  uncompute steps pipeline instead of running as a chain. The conditional CZs' H̄ (0.68 s each)
  hang off the side.
- **Programs:** the run is copies of 10 programs, each compiled at the machine's distance,
  every rule and R10. They are 5 merge shapes (the facing pair used 155 times, four X̄X̄ and
  fan-out corridors down a bus column 2 to 6 tiles long), the H̄ gadget, and a tile's
  preparation and readout in each basis (0.31-0.44 ms).
- **Assumed:** decoding takes one round, and gadgets start off the round clock (no alignment
  of every tile's rounds to one clock yet).
- **Not yet built:** a flat hardware program for the whole run. Each gadget is a translated
  copy of a checked program on tiles no other gadget holds at the time.

**Step 4: what the factory needs (2026-11-01).** The Toffoli consumes |AutoCCZ>: CCZ|+++> on
three patches plus three routing pairs, each CZ|++> and CNOT-linked from two of the three. That
is the state the d = 3 town's `msccz` makes, with the check that FormalRV's own consumption of
it is CCX in all 4,096 branches. On the surgery machine the encoding, the routing pairs and the
links are merges. The T gates of 15-to-1 and 8T -> CCZ are teleported, and each needs an S̄
correction half the time. S̄ needs |Ȳ> or another non-CSS operation, and nothing built so far
can supply one: merges, CSS preparations and readouts, and the transversal-H H̄ keep every
amplitude real. So the factory needs one new gadget first:
- Gidney's in-place access to the Y basis (2023): |Ȳ> on a rotated patch, local, about d
  rounds; or
- a fold-transversal S̄, if a symmetry of our patch allows it; or
- a twist for Z̄⊗Ȳ, as in Litinski's auto-corrected π/8.

The Toffoli's mixed X̄Z̄ on its CCZ output becomes X̄X̄ once the factory turns that output with
H̄ before delivering it.

**|Ȳ> done (2026-11-01): `y_memory` and `y_prep_qasm`, Gidney's own circuits.** The paper's
data (zenodo 7487893, CC BY 4.0) has his noisy Y-basis memory circuits for d = 3 .. 17. Those
for d = 3, 5, 7, 9, 11, with (d - 1) / 2 padding rounds and the noise removed, are now in
`qccd/qec/inplace_y` (README with the citation). They fit our lattice as written:
- **Orientation:** transposed, his patch is exactly ours. Every check of our patch, and our Ȳ,
  has a definite value in his state between rounds.
- **Gates:** every two-qubit gate is a CZ between a data qubit and the measure qubit beside it,
  so the tiled compiler plays his circuit gate for gate. The two extra measure qubits of the
  twisted patch sit at boundary faces our tiles already have.

**Checks:**
- **Distance:** graphlike distance d at d = 3, 5, 7 and 11 under our uniform noise, as on his
  circuits. With fewer than (d - 3) / 2 padding rounds it is lower, his own finding.
- **Rules and R10:** every rule and R10 at d = 3 (tests) and at the machine's d = 11.
- **Cost at d = 11 (qccd-physics-best):** the whole Y memory is 251 ions, 1,515 instructions,
  154.7 ms. |Ȳ> alone is every operation up to the transition round's measurements:
  431 instructions, 43.9 ms, a third of a merge. The lattice scheduler's `prep Y` is that
  gadget.

So S̄ is `prep L Y; ppm Z p Z L; read L X` (FormalRV's own), all concrete, and the factory can
be built.

**Injection and the 15-to-1 at the logical level (2026-11-01).**
- **Injection:** `injection(d, basis)` puts the state on the corner of a patch. Row 0 and the
  upper-right triangle start in |0>, column 0 and the lower-left in |+> (Li 2015). stim's flow
  solver shows the map from the corner to the patch is an encoding (Z on the corner to Z̄, X to
  X̄), so it injects any state, T|+> included. Its circuit distance is 1, as an injection's is.
  Tiled, every rule and R10; the T itself is one virtual Z on the corner ion.
- **15-to-1:** prepare five patches in |+>. For each of the 15 nonzero v in F₂⁴, apply a π/8
  rotation of Z on the patches of supp(v) and the output patch. Then read the first four in X.
  By state vector, with no faults it accepts with probability 1 and outputs T†|+>. Every single
  and double fault in a magic state is caught, and exactly 35 triple-fault sets are not: the
  textbook 35 p³.
- **On the lattice:** each rotation is a corridor merge (2 to 5 patches and the injected one),
  the injected patch's X readout, and S̄ on the product half the time (`prep Y`, a merge, a
  readout).
- **`qccd.fullstack.scale.factory.distill_15to1`:** the 15-to-1 as lattice instructions. Run
  on a state vector with outcomes sampled and frames applied, every faultless run accepts and
  leaves exactly T†|+>, and a Z fault on any one of the 15 magic states is always rejected.
  Scheduled, it takes 4.24 s on about 34 tiles: 99 gadgets, check clean. The 15 rotations all
  touch the output patch, so they run in turn, each a merge and a correction merge reserved
  either way. It uses 30 distinct merge shapes, which are not yet compiled at d = 11.
- **Injection error under the machine's noise (sampled, tiled at qccd-physics-best):**

  | d | Z basis | X basis | sum |
  |---|---|---|---|
  | 3 | 2.0e-4 | 3.6e-4 | ~6e-4 |
  | 5 | 2.5e-4 | 7.2e-4 | ~1e-3 |

  It grows with d in X, so take p_inj ~ 1e-3. One 15-to-1 level then gives 35 p³ ~ 3.5e-8 a
  T, far from enough alone. 8T -> CCZ on those gives 28 (3.5e-8)² ~ 3e-14 a CCZ, about 0.003 %
  of shots over the 8.1e8 CCZ. So the two levels planned suffice, with room.

Budget for the 8.1e8 CCZ a shot. One level of 15-to-1 from injected T gives 35 p³ a T: 3.5e-11
at p = 1e-4, 4.4e-12 at p = 5e-5. Spending 7 such T on each CCZ loses about 20 % of shots at
p = 1e-4 (2.5 % at p = 5e-5). Feeding them to 8T -> CCZ (msccz's protocol, 28 p² on its
inputs) instead makes the CCZ error ~3e-20, nothing beside the memory's 2.2 %. So: 15-to-1,
then 8T -> CCZ, then the routing pairs.

**The second stage: |AutoCCZ> from eight distilled T (2026-11-01, `factory.autoccz`).**
- **8T -> CCZ as rotations.** The [[8,3,2]] cube code's T on even vertices and T† on odd is
  logical CCZ. With the code's rows chosen as the all-ones word (a check patch) and the three
  faces (x, y, z), vertex v is a π/8 rotation of Z on the check and on the outputs of supp(v):
  T for even |v|, T† for odd -- the 15-to-1's teleported gadget with the correction's guard
  flipped. Both values of the check leave CCZ|+++>, so it reads +1 in X, and any one T fault
  turns it (28 p² out). All eight products are Z̄ products: no new gadget.
- **The routing pairs are CZ edges.** Commuting `autoccz.circ`'s CNOTs through its pairs gives
  CZ_xy CZ_xz CZ_yz on x y z -- Pauli frames, since CCZ CZ_xy CZ_xz CZ_yz|+++> = Z⊗3 X⊗3
  CCZ|+++> -- and each site attached by CZ to its partner and one of x, y, z. A site attached to
  qubits all in one frame is one merge on a fresh patch, in the opposite frame: H-turned and a
  Z̄ product with them (a parity copy), or plain and an X̄ product with turned ones.
- **Two H̄.** FormalRV's consumption needs x and y plain and z turned (its X̄_t Z̄_z becomes
  X̄X̄), so z takes an H̄. The pair on x and y closes an odd cycle (y - s7 - s8 - x), so s7
  takes the second, between its two attachments. The state is held with z, s4, s6, s8 turned
  (`TURNED`); `codesign.surgery_rewrite` writes `autoccz_h` for it and swaps the consumer's
  letters on those sites (`_held`), which is exact.
- **Checked by state vector.** The stage's output, turned sites turned back, is exactly
  FormalRV's |AutoCCZ> (`ccz.autoccz_vector`), on injected T (40 of 40 runs) and fed by eight
  15-to-1s; FormalRV's Toffoli -- its own 42 instructions from the adder's first call, letters
  swapped on the turned sites -- is CCX on random data (30 of 30); each of the 8 single T
  faults is caught.
- **On the lattice (`factory_schedule`).** One 15-to-1 is scheduled on a strip of its own (its
  five patches a column, a bus column each side), and the eight are translated copies of it,
  side by side: the same gadgets, so the same shapes. The stage's ten patches sit in a row
  below and are scheduled around the copies (`schedule(reserved=, start=)`). 851 gadgets on
  360 tiles, 7.59 s (4.24 s of 15-to-1s, then 3.35 s of stage), 5.7 at once, check clean; 58
  distinct shapes, 52 of them merges. (Scheduled as one stream the 15-to-1s fought over bus
  columns: 12 to 23 s and 260 shapes.)
- **Two faults found on the way.** `corridor_merge` closed a *slit* -- a patch's open side
  beside the corridor -- with filled seam crossings at both ends: a hole, one logical qubit too
  many, in 18 of the 52 shapes. A crossing on a slit now stays open; the adder's 5 merge shapes
  and the tests' corridors are unchanged bit for bit. And `lattice.check` did not see a gadget
  on a tile where a block lives between its preparation and its readout; it does now (the
  scheduler always honoured it, and the adder's half is still clean).
- **d = 11, all 52 merge shapes, every rule and R10** (qccd-physics-best, 25 rounds): 505 to
  7,371 ions, 138.3 ms each. The largest needed ~17 GB for R10's one tableau of the whole
  program (n plus a fresh qubit per readout); `_r10_blocks` (tests/test_c2lr_router.py)
  compares it block by block instead -- sound because `check_program_order` keeps each
  qubit's ops in source order, so instructions of different blocks that run out of block
  order commute -- 1.2 GB and 130 s where the whole check took ~13 GB and 267 s.
- **Cost:** one |AutoCCZ> per factory per 7.59 s on 360 tiles; 8 x 15 injected T and 128 |Ȳ>
  a state (made either way, used about half the time). (Gidney 2025 makes its T states by
  cultivation, far smaller; the 15-to-1 is our stand-in, a codesign axis.)

**Step 3 (b): the whole adder on one lattice (2026-11-01, `tools/lattice_adder.py --farm`).**
- **The forward half, rewritten.** With `_held` the Toffolis' 31 X̄Z̄ are X̄X̄. Two more mixed
  products remained, both in bit 0: FormalRV writes that CNOT as three |Ȳ> rotations (S̄ on c,
  exp(-iπ/4 Z̄_c X̄_t), √X̄ on t). `_rotation_cx` makes it FormalRV's own lattice-surgery CNOT,
  proved on the segment alone (both are `cx c t` once their outcomes are forgotten). That is
  sound because nothing reads the six dropped outcomes: no later line, no source-slot write,
  and Gidney 2025 reads no record by address (`records_read`). The forward half is then 1,827
  instructions, 31 `autoccz_h` and no mixed product.
- **Factories as macros.** `factory.Farm`: each `autoccz_h` becomes a `macro` line followed by
  a renamed copy of the factory's lines, served by a translated, time-shifted copy of the
  factory's checked schedule (`lattice.schedule(macro=)`). So program order runs from the
  factory to its consumer, and the check sees each site made before it is read. A factory's
  inside is walled off; only its row of sites and the bus row below are open.
- **One factory per bit, bits beneath them.** With bit i's three blocks under factory i
  (`Farm.beneath`) every Toffoli reaches its factory alike, so the shapes repeat. The whole
  call -- 31 Toffolis, the CNOT cascade, the 31 uncompute steps -- is 27,776 gadgets on 16,522
  tiles, **21.83 s**, check clean, 78 distinct shapes (71 merges: the factory's 52 and 19 of
  the adder's). All 31 states are ready at 7.59 s; the Toffoli chain then takes 0.43 s a bit.
- **Fewer factories cost time.** Assigned dynamically (the factory free soonest, no repeating
  geometry): 4 factories 75.6 s on 2,134 tiles, 8 47.2 s, 16 34.2 s, 32 24.4 s, but 360-475
  distinct shapes. Periodic with 2 to 6 bits a factory: 131-205 s, because a factory's one row
  of sites holds its last state until the chain reaches it. A second row per factory
  (double-buffering) is the fix; not built.
- **Concrete vs composed.** The 21.83 s is a schedule of checked programs, each a compiled
  program's measured time; there is still no one flat program for the call. Decoding is
  assumed to take a round.
- **d = 11, the adder's 19 merge shapes, every rule and R10:** 1,555 to 8,430 ions, 138.3 ms
  each, so all 71 merge shapes of the whole call pass (14 h of checking; the longest, a
  29-tile X̄X̄ corridor, 83 min). The corner injection, until now checked at d = 3 only, passes
  at d = 11 too (241 ions, 11 rounds, either basis, every rule and R10 on its Clifford twin;
  the T is one virtual Z on the corner). With the H̄, |Ȳ>, and the preparations and readouts
  checked before, every distinct program of the call is checked at d = 11.
- **Open:** 8 of those shapes are X̄X̄ corridors 24-29 tiles long, because bits sit 24 tiles
  apart; a factory of two rows of four 15-to-1s, 12 tiles wide, would halve them. A second
  row of sites per factory would let fewer factories serve the chain.

**The narrow factory and two sets of sites (2026-11-02, user: "Yes, agree").**
- **Narrow factory (`factory_schedule(strips=4)`).** Four 15-to-1 strips, 12 tiles wide, make
  two T each in turn: the second 15-to-1 on a strip is a copy of the same unit, started once
  the stage has read the strip's first T and no stage gadget is on the strip. The stage's ten
  patches sit in two rows below (`NARROW_ROWS`), scheduled in two parts around the two batches.
  192 tiles instead of 360, 11.52 s a state instead of 7.59, longest corridor 13 tiles instead
  of 22; check clean, the eight unit copies alike.
- **The whole adder, one narrow factory a bit (`--farm`):** 27,776 gadgets on 8,671 tiles,
  25.08 s, check clean (wide: 16,522 tiles, 21.83 s -- 40 % less tile-time now). 72 merge
  shapes, longest corridor 19; 42 of them were already checked at d = 11, 30 are new.
- **Two sets of sites (`Farm(buffers=2)`, `--farm --bits 2`).** A factory's states take two
  sets of sites in turn, the second set two rows below the first, so its next state is made
  while the last waits for the chain; each set's schedule walls off the other's sites (a
  corridor through a waiting state was the one bug, caught by the check). Two bits a factory:
  16 factories, 5,184 tiles, 36.18 s, check clean -- the least tile-time yet (188k tile-s
  against 217k), at 11 s more latency; 96 merge shapes. Without the second set the same farm
  takes 183.9 s; with three bits a factory two sets are not enough (137.8 s).
- **d = 11 of the narrow farm's 30 new shapes, every rule and R10 (block by block):** all pass,
  1,297 to 5,517 ions, 138.3 ms each (3.2 h of checking in all; the longest, a 16-tile X̄
  product of three patches, 21 min). With the 42 checked before, every distinct program of
  the narrow-farm adder is checked at d = 11. The `--bits 2` farm's other shapes are not.

**Step 5, the census (2026-11-02).** With every template rewritten for the surgery machine --
`_pauli_frame` made gidneyCompare's four-merge step (a CNOT-and-S̄ by |Ȳ> rotations, then its
inverse: Z̄ Z̄, every branch) two frames, so no template has a mixed product left -- one prime
(`gOnePrime`, P = 1 minus P = 0) is 1,770,257 merges, 877,707 preparations, 1,437,042 readouts,
46,633 H̄ and 37,289 |AutoCCZ> states, on the program's 1,695 blocks; times 21,640 primes it is
the program (outside the primes: 1.84M merges, 60,162 states). A data decision reserves its
places either way, so every prime's schedule is the same: one prime scheduled concretely, times
21,640, is exact, not an estimate.
- **Factories set the pace** (composed): 37,289 states at 11.5 s each is 13,800 s a prime with
  31 factories (9.5 years for the primes), 1,430 s with 300 (about one year, ~58k factory tiles).
- **Shapes set the checking.** One prime is ~4M gadgets, which the scheduler handles (the
  whole adder, 27,776, takes 6 s). But its Toffolis act on many registers: on a free floorplan
  every consumer's corridor is a shape of its own -- thousands to check at d = 11. The adder
  kept 72 by laying bits beneath their factories; a prime needs a floorplan that does the same
  for every register a Toffoli touches, or local moves (fixed-shape hops) instead of long
  corridors.

**Progress (2026-10-31).**
- Step 1 done (9ba329a): `lattice_surgery`, Z̄Z̄ and X̄X̄; d = 25, 25 rounds: 2,549 ions, every
  rule, 138 ms.
- Step 2, the CNOT done (65c4ad5): `surgery_cnot`, FormalRV's own (A in |+̄>, Z̄_C Z̄_A, X̄_A
  X̄_T, A read in Z), three patches in an L, two phases, detectors by one rule (`_phased`).
  Circuit distance d; every rule and R10 at d = 3 .. 15; LER under qccd-noise@2 2.4e-3,
  2.3e-4, 1.9e-5 (Z̄ flow) and 2.2e-3, 2.8e-4, 2.8e-5 (X̄ flow) at d = 3, 5, 7.  At d = 25:
  3,849 ions, 94,848 detectors, 1,976 instructions written in 8 s, every rule (132 s), 280 ms.
  R10 at d = 25 was stopped at 36 GB: it needs a leaner tableau before it can run here.
- Step 2, H̄ done (2026-10-31): `hadamard_bar`, below.  `_phased` gained a transversal H
  between phases (each check compared with the one of the other type on its support), data
  states moved through face ions (checks move with them), and the detectors it lacked when a
  shrink reads more than a seam (a check whose data were all read).  `MemoryExperiment.faces`
  names each ancilla's face, and `compile_tiled(faces=)` plays faces on either checkerboard.
  Tiled at d = 3, 5: every rule and R10, 46 / 117 ions, 111 / 160 ms (d rounds a phase).
  LER under qccd-noise@2 (d rounds a phase): 2.9e-3, 3.0e-4, 2.4e-5 (Z̄ -> X̄) and 2.7e-3,
  3.3e-4, 3.0e-5 (X̄ -> Z̄) at d = 3, 5, 7.  Per patch area per round (the data of each phase,
  in patches, times its rounds) that is 1.2-1.3x the Helios memory's rate at d = 3, 5 and
  0.9-1.1x at d = 7: H̄ fails like an idle patch, as the paper found.  Under the machine's own
  noise (25 rounds a phase): 5.8e-5 / 3.7e-5 at d = 3, the memory's rate (3.4e-7 / 2.2e-7 vs
  3.0e-7 per patch area per round).  At d = 5: 2 errors in 1.3e7 shots and 0 in 1.3e7, where
  the memory's rate predicts 3 each (a short run, stopped there).
- Step 2, the corridor merge done (2026-11-01): `corridor_merge(d, letter, patches, corridor)`,
  the product of Z̄ (or X̄) of any patches through any connected set of free tiles. Each patch
  joins the corridor through the seam on one of its Z̄ sides (X̄ sides). Each patch's far side
  keeps its own boundary; the rest of the region's boundary takes the merge's type. One rule
  had to be found: on a tile-aligned region, the top-left and bottom-right corners always carry
  faces of the X checkerboard colour. On an all-Z boundary such a corner is a point of X
  boundary, which makes one logical qubit too many, so those corner points are cut (their face
  becomes a weight-3 check). A U, bends either way, three patches in a row, a fan-out column,
  a long X̄X̄ path and a snake each measure the product, keep every patch's logical and keep
  distance d at d = 3, 5, 7. All run on the four-layer Tomita–Svore schedule. Tiled, every rule
  and R10; any shape is the same instructions (115 for 3 rounds). At d = 11 with 25 rounds the
  U is 1,295 ions, 951 instructions, every rule (20 s), R10 (51 s), 138 ms. The five-tile X̄X̄
  path is 1,817 ions, every rule (42 s), R10 (96 s), also 138 ms: a merge costs the same
  whatever its corridor.
- The machine at d = 11 (qccd-physics-best, 25 rounds a merge), every rule and R10 on each:

  | gadget | ions | instructions | every rule | R10 | machine time |
  |---|---|---|---|---|---|
  | syndrome round | 241 | 39 | 0.1 s | 1.2 s | 5.8 ms |
  | Z̄Z̄ merge, 25 rounds | 505 | 951 | 4.3 s | 21 s | 138 ms |
  | surgery CNOT, 2 × 25 rounds | 769 | 1,934 | 16 s | 63 s | 278 ms |
  | H̄, 3 × 25 + 3 rounds | 522 | 4,567 | 16 s | 67 s | 680 ms |

  Against d = 25: 5x fewer ions a gadget (3,849 -> 769 for the CNOT), the same machine time (the
  rounds are the same 25; a tiled round takes 5.6-5.8 ms at every d), and R10 fits in memory.
  Under the machine's own noise (25 rounds a merge) the CNOT's LER is 4.2e-5 / 1.8e-7 (Z̄ flow)
  and 6.9e-5 / 3.9e-7 (X̄ flow) at d = 3 / 5: Λ 232 and 178 against the memory fit's 206.  Per
  patch area per round the Z̄ flow fails at the memory's rate (1.1e-9 vs 1.4e-9 at d = 5), the X̄
  flow 1.7x above it; were the whole run 1.7x worse, 3.7 % of shots would be lost, still
  under the paper's 6.7 %.

**What the program asks of the machine (census, 2026-10-31).** Gidney 2025 at RSA-2048 on
the paper machine, weighted by how often each template runs.  As FormalRV writes it the PPMs
are Z̄Z̄ 2.07e10, X̄X̄ 1.63e10, X̄Z̄ 1.87e9, Z̄ 1.05e9, X̄X̄X̄ 6.0e8, X̄Z̄Z̄ 1.5e7, with 3.2e9
|Ȳ> preparations and 8.1e8 CCZ.  Every X̄Z̄ is `prep l Y; ppm X p Z l`, the middle of
FormalRV's H̄ (S̄ √X̄ S̄ from three |Ȳ> merges).  After codesign's rewrites no PPM is left:
`cx` 2.76e10, `read` 1.87e10, `prep Z` 5.5e9, `ht` 2.9e9, `prep X` 1.0e9, `autoccz` 8.1e8,
`prep Y` 4.2e7.  So the machine needs the surgery CNOT and H̄ (both built), preparations and
readouts, and the CCZ factory.  Of the `ht`: 2.1e9 sit on a fresh ancilla between two
`cx` (`prep l Z; cx a l; ht l; cx l b; read l X` -- the mixed product Z̄_a X̄_b), and 7.8e8
open a template on a block (`ht p0; cx p0 l; read l Z` -- "measure X̄", which an X̄X̄ merge
with a |+̄> patch does with no H̄; a codesign rewrite to make).

**H̄ is the one gadget a CSS-only machine cannot make.** Z̄Z̄ and X̄X̄ merges, with
preparations and readouts in X and Z, generate only the Cliffords that keep Z-type Paulis
Z-type (CNOT circuits).  A merge that is a CSS code measures its Z-type and X-type products
separately, so no straight seam, corridor or L of faces measures Z̄_a X̄_b (checked by hand
and against the boundary-segment count: every such merged region keeps both logicals).
H̄ on a stationary tile (built 2026-10-31, `qccd.qec.experiments.hadamard_bar`) is Gehér,
McLauchlan, Campbell, Moylett and Crawford's (Quantum 8, 1394 (2024), arXiv:2312.11605), turned
onto our lattice. It uses the tile, its seam column and the neighbouring tile: d x (2d + 1)
data points.
1. Transversal H (one beam per data ion). The checks swap type, so the lattice under the patch
   becomes the other checkerboard (Z faces at odd i + j). No code deformation can flip it back,
   because a deformation extends the checkerboard it has.
2. Grow over the seam and the neighbour (prepared |+>). Three corners sit on the top edge and
   one at the bottom-left; d rounds.
3. The bottom boundary becomes X. The corners are then at the four ends of the top and the
   right, so the boundaries have turned a quarter; d rounds.
4. Read columns 0 .. d in Z. The patch is now the neighbour tile; one round.
5. Grow back over columns 1 .. d (prepared |0>); d rounds.
6. Read columns d + 1 .. 2d in Z. The patch sits on columns 1 .. d; one round.
7. Move every data state one column left, through the measure ion of the face between: two
   CNOTs in, a reset of both data ions, two CNOTs out. A fault touches one data state, never
   two. The translation by one site restores the machine's checkerboard, and the patch is home
   with X̄ and Z̄ exchanged.

Two things differ from the paper. Step 7 moves states through ions instead of SWAPping qubits.
Every row of the machine is full of ions, so no ion could move one trap without a hole to move
into. And steps 2 and 3 measure every X check and then every Z check (eight layers), each
touching one diagonal pair of corners and then the other. Near the top corner, those patches'
logicals have short representatives both horizontally and vertically. A hook then lands on a
diagonal pair, two opposite edges of a face, which no shortest logical string can use. The
four-layer split schedules tried here (one diagonal line per phase, as the paper describes)
reached only d - 1 at d = 5 and 7. With the diagonal schedule the circuit keeps distance d
at d = 3, 5, 7, 9, 11 in both bases, and every deterministic parity is a declared detector.
The cost: an eight-layer round takes about 1.9 four-layer rounds.

H̄ takes 680 ms at the machine's distance with 25 rounds a phase, which is 4.9 merge-times.
FormalRV's H̄ is three |Ȳ> merges, 3 merge-times, so 2.9e9 H̄ add ~1.4e10 merge-times to the
CNOTs' 5.5e10 (+26 %). A four-layer schedule that kept distance d would bring H̄ to ~3.1
merge-times. The neighbouring tile and the seam are busy for the whole gate, and the mirror
image uses the left neighbour instead.
Off the critical path instead: an H̄ applied in advance to one half of a Bell pair gives the
pair Z̄_1 X̄_2, X̄_1 Z̄_2, and then Z̄_a X̄_b is two ordinary merges at once (Z̄_a Z̄_1 and
X̄_b X̄_2, which commute, product Z̄_a X̄_b), and H̄ on a block is teleportation through the
pair -- the rotations become a resource factory, like magic states.  (A physical 90°
rotation of the tile's ions would replace the deformation with O(d) transport steps, but needs
holes in the ion lattice and a router that moves data ions through the measure ions' traps;
not now.)

**What distance trapped-ion fidelities need (2026-10-31; user: "trapped ions have better gate
fidelity -- estimate the distance we actually need").** Measured, not assumed: the idle gadget
(a stationary d x d patch, d rounds, tiled, physics@2 clock) under qccd-noise@2 with the device's
fidelities replaced, X and Z memories, sampled with stim + pymatching to 300 errors or 0.6-4e8
shots; fitted per patch per round as p(d) = A Λ^-((d+1)/2), p = p_X + p_Z, over every point with
at least 10 errors (also at noise x2, x4 for the cleaner scenarios).  The budget is Gidney 2025's
own (arXiv:2505.15917): 6.9e13 logical-qubit rounds a shot at d = 25 (1,600 logical qubits, 12 h
at 1 µs), 93.3 % of shots surviving logical errors; every surgery step lasts d rounds, so a shot
is 6.9e13 · d / 25 rounds.  Gidney picks d = 25 for p = 1e-3 (1e-15 per patch per round).
`tools/distance_study.py build | sample | fit` reproduces every number.

| Fidelities (everything not named stays Helios) | Λ | d needed | volume x10 | ions a patch, 2(d+1)² |
|---|---|---|---|---|
| Helios 2025 = qccd-noise@2 (2Q 7.9e-4, 1Q 2.5e-5, reset 5e-4) | 13.7 | **23** | 25 | 1,152 |
| Helios, every error halved | 27 | 19 | 21 | 800 |
| Helios + the smooth gate, 2Q 8.4e-5 (arXiv:2510.17286) | 77 | **15** | 15 | 512 |
| best published for each operation: + 1Q 1.5e-7 (arXiv:2412.04421), SPAM 7e-6 (arXiv:2409.05805) | 103 | **13** | 15 | 392 |
| **the Shor machine**: best published, every error halved (projected; `qccd-physics-best` at noise scale 1/2) | 206 | **11** | 13 | 288 |

The user chose the last row (2026-10-31, "be optimistic about future trapped ion hardware
progress"): d = 11.  Its p at d = 3 was measured directly, 5.8e-7 a round against the fit's 5.9e-7;
at d = 11 the fit gives 3.3e-16 a patch a round, so with 25 rounds a merge (the paper's 6.9e13
logical-qubit rounds a shot, whatever d) 2.2 % of shots are lost to logical errors, against the
paper's 6.7 %.

- Helios measured: p = 1.8e-4, 1.4e-5, 1.1e-6, 6.5e-8 at d = 3, 5, 7, 9 (Λ step by step 13.0, 12.8,
  16.3: not falling).  At d = 25 its fit gives 5.7e-17, so d = 25 is the Helios answer with a
  17x margin; d = 23 meets the budget.
- Where the error comes from (summed probability per data ion per round, d = 9): on Helios the
  MS gate 72 % (base 68, heating 4), reset 13 %, one-ion pulses 11 %, memory 2 %, readout
  crosstalk 1 %.  With the best numbers the MS gate is still 69 %, but memory (18 %) and readout
  crosstalk (10 %) follow: both are charged per 5.6 ms round, so only faster rounds lower them.
- The two-qubit gate is the lever: replacing it alone takes Helios from d = 23 to d = 15; the
  1Q and SPAM records add one step (13).  Reset and one-ion errors are cheaper for the decoder
  than their share of the budget suggests (Helios + smooth gate: 2.8x less summed error, 5.6x the Λ).
- The surgery CNOT fails at the memory's rate per patch area per round (Helios, d = 7: 1.07e-6
  for the CNOT, 14 rounds over 3.14 patch areas, vs 1.06e-6), so the memory fit stands for the gadgets.
- Caveats: the best row combines records from separate lab experiments (a two-ion gate, a
  single-ion SPAM, microwave 1Q on ⁴³Ca⁺); Helios is the one whole machine.  Extrapolated from
  d <= 9.  Leakage is charged as Pauli, as Helios's emulator does; no ion loss, no per-shuttle
  error (qccd-noise@2's own limits).  The factory's output error at these fidelities is not measured.
- Wall-clock (composed from Gidney's round count, not from our program): a shot is
  4.3e10 · d / 25 rounds; at 5.6 ms a round that is 7.1 years at d = 23 and 4.0 years at d = 13
  (with 25 rounds a merge at any d, as the machine keeps them: 7.7 years).
  The distance shortens a shot only linearly; the round time (5.6 ms against Gidney's 1 µs) is
  the trapped-ion machine's real wall-clock problem.

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
- **Shor N=15** (`shor15.alg`, `shor.floor`): 157 + 32 instructions, 11.11 s (11.16 s before heat was followed, 2026-10-26), all 24 rules hold (H1 added 2026-10-26), signed off
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
