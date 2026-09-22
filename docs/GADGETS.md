# Logical QCCD gadgets — plan and beta

**Goal.** Compile a LogicQ logical program over QLDPC codes onto a QCCD architecture at the
scale of 10⁴ ions, and let a person read the result at every level: the logical
instructions; the gadgets that realise them, drawn as black boxes with ions streaming in
and out; and, inside any one box, the ion-by-ion hardware program, step by step.

**The one idea: never flatten.** Each logical cycle, logical operation and magic-state
factory is a *gadget*, a black box with ion ports. A gadget is compiled and verified once
per master, then characterized into an abstract: duration, port transfer windows, heating.
A machine instantiates masters many times. The whole-machine schedule is computed over the
abstracts alone. Ion-level programs exist once per master, and the tool replays one on
demand for whichever instance the user opens. This is how integrated circuits scale past
10⁹ transistors, and the correspondence is close enough to borrow the vocabulary (§1).

Written 2026-09-16 on the `compiler` branch. **Status:** the beta (§5–§8, BB codes at 9,720
ions) is verified as hardware only; the **verified place library** (§9: surface code, d = 3)
is verified as hardware *and* as logic, and composes into logical algorithms that are signed
off end to end. Every number below is measured.

```bash
python -m qccd.gadget places                         # the verified places, and what was checked
python -m qccd.gadget algorithm examples/gadgets/algorithms/teleport.alg   # schedule + sign-off
python -m qccd.gadget showcase                       # beta: 96 BB[[72,12,6]] blocks, 9,720 ions
python -m qccd.gadget build PROGRAM.lq [--studio]    # beta: a LogicQ program in the accepted subset
python -m qccd.gadget build PROGRAM.lq --design edited.gadget.json   # a design from the editor
# open out/gadgets/<name>/index.html
```

---

## 1 · What IC design already solved

| IC design | Here | What carries over |
|---|---|---|
| standard cell, hard macro | **leaf gadget**: a device fragment plus one TSIR per operation | the layout exists once per master; every instance shares it |
| cell abstract (LEF: size, pins) | footprint, ports (boundary node, side, direction, role, width) | a parent sees only the abstract |
| timing library (Liberty arcs) | **op characterization**: duration, per-ion port crossing times, n̄ at the port, gate counts, rule report | a parent schedules against it |
| characterization, SPICE → `.lib` | replay the op's TSIR under the cost model and run all 27 rules | numbers come from the verifier, never from a formula written beside it |
| testbench with ideal sources and loads | **port stubs**: ideal ion source/sink sites just outside the boundary | a leaf is verified in isolation |
| structural module, netlist | **composite gadget**: instances, channels, exported ports | recursion to any depth |
| bus, bit order | **ion bundle** crossing one port in a declared order | pairing two bundles is a bit-order check (G1) |
| wire-load model, RC extraction | **channel** priced by replaying a conveyor of its length | a channel is not a cell |
| static timing analysis | arrival vs. intake at every port; n̄ along every path | G-rules (§8) |
| elastic / latency-insensitive pipelines | **paced handshakes**: a source may stall, a channel holds at most its length | §7 |
| mixed-level simulation | gadget-level execution plus ion-level replay of one instance | drill-down |
| descend / return | double-click an instance / `Esc` | the navigation model |
| HLS scheduling and binding | logical program → gadget schedule: bind, schedule, route | `synth` (§7) |
| DRAM refresh | syndrome extraction: a block that stops cycling loses its contents | the refresh constraint (G8) |
| hierarchical DRC/LVS, flat sign-off | G-rules on the composition; flatten + 27 rules on small designs | the abstraction is checked, not trusted (§9, planned) |

---

## 2 · Concepts

**Master.** A named gadget definition: `kind` is `leaf` or `composite`. It has ports, a
resting inventory of ions by role, a capacity, a footprint and ops.

**Port.** Where ions cross the boundary. `dir` in / out / inout; `role` (`data`,
`messenger`, `magic`, `any`); `width`, the ions one transfer carries; `order`, the order a
bundle crosses in; and on a leaf the device `node` it sits on and the testbench `stub`
beyond it. A composite's port binds to one child port.

**Op.** Something a master does. On a leaf, an op is one TSIR on the master's testbench
device. Every op carries a characterization and a **status**, under the repository's
reporting contract (`docs/rules.md`: *passed* only if the check ran):

| status | meaning | drill-down |
|---|---|---|
| `verified` | the TSIR replays, no rule fails, and it ends in the placement it promised; R10 reported *skipped* with the verifier's reason | ion by ion |
| `certified` | as `verified`, plus R10 *passed* by the proved Lean checker (none yet) | ion by ion |
| `modeled` | no TSIR; an analytic abstract with its source named (none used) | none, and the tool says why |
| `failed` | a TSIR that breaks a rule or its placement contract; G9 fails any schedule that uses it | ion by ion, violations listed |

**Instance.** A master placed in a composite. Instances share their master's programs; an
instance adds only a position and the global ids of its ions.

**Channel.** A transport link between two ports: a Manhattan route, and a length in sites
(route length − 1). Every carry is priced by replaying `chan<L>`, a conveyor of that
length, memoized per length.

**Control domain.** Every leaf instance and every channel has its own control electronics,
so R4 and R22 (one class, one waveform per cycle) hold *within* a domain, and concurrency
*across* domains is the scheduler's business. This is a modelling assumption, stated rather
than hidden: a modular machine with per-module DAC groups (§14).

**Placement contract.** A leaf op starts from the master's home placement and ends in the
placement it declares, checked by characterization. Compiled syndrome rounds do not do this
on their own: the BB [[72,12,6]] round from `qccd/compile` ends with the whole ring rotated
45 of 72 slots, warm. So `cycle` appends the restoring rotation and a cool, and its
characterization pays for both. That is what makes `cycle × N` a legal thing to write.

---

## 3 · The interface contract

A transfer is one ion bundle crossing one port during one op, measured from the replay:
`port`, `dir`, `count`, the local ion names in crossing order, each ion's crossing time
from op start, and the largest n̄ among them (read with the replay's probe).

- **Stubs.** A leaf's testbench device is its device plus one stub per port: a site in zone
  `port` (capacity = width; no gate, no SPAM, no cooling) one segment beyond the port node.
  An outgoing ion ends its op parked on the stub; an incoming ion starts there. Handoffs use
  the classes `port_in` / `port_out` (no split or merge). A stub is never cooled by its
  gadget (R6 enforces it), so a bundle leaves as warm as the op made it, and says so.
- **Stack discipline.** A bundle that leaves and returns through one port comes back
  reversed: the memory emits in ring order (`d1, d37, d2, …`) and absorbs in reverse; the
  station's rails are LIFO. Pairing the i-th ion of two identical blocks is preserved end to
  end, and G1 checks it on every station event.

---

## 4 · Execution: events, carries, ions

The compiled machine program is a **GIR** (`gir.json`, schema `qccd.gir/0.1`):

- an **event** is one op on one leaf instance:
  `[id, t0, t1, leaf, op, repeat, instruction, group, bind, warp]`. `repeat` is `N` for
  `cycle × N`; `bind` names the visiting ions (`[local name, global id]`); `warp` maps the
  op's own clock to the global one where a handshake stretched it (piecewise linear).
- a **carry** is ions moving along one channel:
  `[id, net, from leaf, from port, t0, departures, transit, ions, role, instruction, group,
  last taken, taken]`. `taken` is when the sink takes each ion off the far end.
- **ions have global identity**, allocated to instance inventories at power-on. G2 follows
  every one of them through every carry and home again.

Viewing a composite shows its children's events and the carries on its channels; viewing a
leaf replays the master's TSIR for the op running at the global time, at local time
`t − t0` (modulo the op inside `cycle × N`, and through the warp). Every level reads the
same event list.

---

## 5 · The beta library

### 5.1 Leaves (all ops `verified`)

| master | device | ops (duration) |
|---|---|---|
| `mem_bb72` | `ring(36, 2, 24, dock_offset=1)`: 72 data ions, 24 ancillas in dock traps; corner port traps `P_bus` (data bundle) and `P_net` (messenger) | `cycle` 53.9 ms (432 CX contacts, then restore + cool) · `layer1q` 2.0 ms · `emit` 19.3 ms · `absorb` 26.5 ms · `visit.<B><i>` per logical operator, compiled on demand (12–29 ms for supports of 6–28 ions) |
| `tcx72` | a comb: two 72-site conveyor rails over 72 gate traps | `cx` / `xc` 37.0 ms (72 CX in one batch; bundles in at n̄ ≈ 1, out at n̄ ≈ 221) |
| `res4` | four traps in a row | `release.Z/X` 0.36 ms (reset, H for X, cool, out) · `accept.Z/X` 0.43 ms (in, cool, H for X, measure) |
| `xjunc` | one degree-4 junction, four arms | `pass` 0.14 ms |
| `fac_t15` | `ring(10, 2, 1, dock_offset=1)`: 15 ions, one gate dock, a corner port trap | `produce` 178.5 ms (the [[15,1,3]] encoder, transversal T, decoder, fold, 14 syndrome measurements: 66 CX) · `consume` 1.4 ms |
| `chan<L>` | L conveyor sites | `carry`: one ion takes (L+1) × 5 µs |

The memory's `cycle` is `qccd.compile.compile_code` (pure Python, ~2–4 s) on the checks
LogicQ's matrices define, so any bivariate-bicycle polynomial LogicQ admits compiles. It is
a *contact schedule*: every contact is a CX between the data ion and the check's ancilla,
without the extraction circuit's H layers and CX orientation, so R10 cannot be decided on
it (noted on the op). The factory's circuit sequencing is a small ring circuit compiler
(`leaves/factory.py::ring_circuit`) in which every gate leaves the ring a rigid rotation of
what it was.

### 5.2 Composites

| master | children | ports |
|---|---|---|
| `tile_bb72` | `mem`, `res` (`res4`), `jx` (`xjunc`) | `bus` (data), `west`, `east` (messenger bus) |
| `pair_bb72` | two tiles, `st` (`tcx72`) between their data ports | `west`, `east` |
| `row_bb72_<P>` | P pairs, buses joined end to end | `west`, `east` |
| `proc_bb72_<R>x<P>` | R rows; a spine of `xjunc` on the west; a `fac_t15` on every row's east end | — |

---

## 6 · From LogicQ to gadgets

### 6.1 The text accepted

A program file (`.lq`) is a sequence of statements, each in LogicQ's own surface syntax as
LogicQ's parsers read it: `//` comments; statements split on newlines and `;` (a `code …
{ … }` declaration runs to its brace); `q[i]` is logical qubit `i` of block `q`. A statement
LogicQ would refuse is refused here too, with its line and the reason.

```ebnf
decl    = "code", ident, "as", "BivariateBicycle", "{", "l","=",nat,";", "m","=",nat,";",
          "A","=",poly,";", "B","=",poly,";", ["params","=","(",nat,",",nat,",",nat,")",";"], "}"
        | "code", ident, "as", "Bare" ;
logical = "Logical", ( ("H"|"S"|"T"|"X"|"Z"), qubit | ("CNOT"|"CZ"), qubit, qubit
                     | "measure", mtarget, "->", cvar
                     | "blockTransversal", block, ("H"|"S")          (* the Tutorial's forms *)
                     | "transversalBatch", block, "->", block ) ;
mixed   = "transversal", nat, ("H"|"S") | "transversalCNOT", qubit, qubit, boolmat
        | "pauli", ("X"|"Y"|"Z"), qubit | "magic", "T", qubit
        | "ppm", ( cvar, ":=", "M", mtarget | "frame", P, "(", qubit, ")" | "discard", qubit | "skip" )
        | "blockTransversal", block, ("H"|"S") | "transversalCNOTBatch", block, block ;   (* the paper's *)
mtarget = qubit, "↦", ("X"|"Y"|"Z"), { ",", qubit, "↦", ("X"|"Y"|"Z") } ;   (* "|->" also read *)
```

What the hardware needs is re-derived the way LogicQ derives it, so the two tools cannot
disagree about what a program means (`qccd/gadget/codes.py`, `gf2.py`, tested against the
Tutorial's table):

- `n` and `k` from GF(2) rank; BB matrices as `ChainQ/BBCode/Basic.lean::Internal.bb`;
- the logical basis exactly as `deriveLogicalBasis?` (`kernelBasis`, `quotientBasis`,
  `gf2Inv`, transcribed step by step), which decides which ions a messenger visits;
- `checkTransversal`'s phase-free stabilizer-preservation test for H and S.

Blocks with equal matrices share one code identity (the Tutorial's name, e.g. `bb72`, or
`bb<n>_<digest>`), so they share one memory master.

### 6.2 Realisation

| instruction | gadget ops | what is claimed |
|---|---|---|
| `code q as …` | bind block `q` to the next memory of that code, row-major | — |
| `pauli P q[i]`, `Logical X/Z q[i]`, `ppm frame` | a Pauli-frame update: zero time | exact |
| `ppm c := M …`, `Logical measure` (one or more blocks, one logical per block, all X or all Z) | `res.release.B` → bus → `mem.visit.Bi` (→ bus → next block's `visit`) → bus → `res.accept.B`, `--ppm-rounds` times | hardware `verified`; one bare ancilla is **not** fault-tolerant against hook errors (an obligation) |
| `transversalCNOTBatch`, `Logical transversalBatch` | `emit` both blocks → channels → `tcx.cx` (or `xc`) → channels → `absorb` both | hardware `verified`; that it is CNOT on all `k` pairs is a CSS fact LogicQ checks |
| `transversal b H/S`, `blockTransversal`, `Logical H/S` on k = 1 | `mem.layer1q` | refused when `checkTransversal` refuses it (e.g. H on bb72) |
| `magic T q[i]`, `Logical T q[i]` | `fac.produce` (scheduled so the state is ready when needed) and one messenger: `res.release.Z` → `mem.visit.Zi` → `fac.consume` (CX from the T ion, then the T ion measured in X) → `res.accept.Z` | hardware `verified`; distillation statistics (35p³) are the protocol's claim; the S̄ correction is classical (drawn as a frame update) |
| `Logical H/S/CNOT/CZ` on single logicals of k > 1 blocks, single-incidence `transversalCNOT` | refused with the reason: these are LogicQ's PPM gadgets with an ancilla logical (planned) | — |
| mixed-basis or Y measurements | refused: the messenger would need a basis change | — |
| `automorphism`, `switch`, `parallelPPM` | refused: no text form in LogicQ | — |

LogicQ lowers `magic`, `switch`, `automorphism` and multi-round surgery to nothing
physical today; they are typed obligations. The gadget layer is where `magic` first becomes
ion movements, so every row above names what is verified and what is assumed.

---

## 7 · Synthesis

`synthesize(program, library, leaves) → GIR` (`qccd/gadget/synth.py`), deterministic:

1. **Flatten** the hierarchy into leaf instances and nets (both ends of every channel
   resolved through composite port bindings); give every resting ion a global id.
2. **Bind** blocks to memories of the same code, row-major.
3. **Schedule** instructions in program order. A memory's ops start only at its own cycle
   boundaries, never before its previous op ends, and are followed by `cycles_between`
   extraction cycles (default 1) before the next. Shared resources (stations, reservoirs,
   junctions, channels, factories) keep interval calendars, so instructions on disjoint
   blocks run concurrently.
4. **Paced handshakes.** A bundle handed from op U through a channel of `L` sites to op D:
   each ion enters the channel no earlier than U's program sends it (U may stall), no
   earlier than the ion `L` places ahead has been taken by D (so the channel never holds
   more than `L`), and arrives before D takes it in (else D starts later). Stalls become
   the event's `warp`. The station's exit stalls once for both bundles, since both leave in
   the same cycles.
5. **Routes** are shortest paths over nets and junctions, reserved hop by hop and shifted
   later as a whole when any hop is busy (circuit switching: no deadlock). A messenger that
   must wait for a memory's cycle boundary waits in the last channel, which stays reserved.
6. **Factories** produce as early as their calendar allows; a T request takes the factory
   whose state is ready soonest counting the trip, and the messenger is released to arrive
   about then.
7. **Refresh**: every gap in every block's timeline is filled with `cycle × N`.

---

## 8 · Hierarchy checks

`qccd/gadget/checks.py`, reported as the rule report is: `passed` only if the check ran.
Each rule is also broken on purpose in `tests/test_gadget_schedule.py`.

| | check |
|---|---|
| G1 | every channel joins compatible ports (role, width, code, direction); every bundle reaches a station in the order it pairs and comes home in the order the ring expects |
| G2 | ion by ion, in time: every ion leaves an instance only after it has arrived there, and ends where it started |
| G3 | no instance ever holds more ions than its capacity, or gives away more than it holds |
| G4 | a channel never holds more ions than it has sites, nor carries ions both ways at once |
| G5 | an instance runs one op at a time |
| G6 | no ion crosses a port hotter than the ion-loss limit (~85 uncooled junction round trips, arXiv:1210.3655, at 3 quanta a transit) |
| G7 | every logical instruction is discharged, in program order on each block |
| G8 | no block goes longer than one cycle without extraction outside a logical op |
| G9 | every leaf op the schedule uses is verified (or certified) |

---

## 9 · Verified places and logical algorithms

The beta proves its gadgets are *runnable*; it does not prove they compute anything: its
syndrome round is a contact schedule with no H layers or CNOT orientation, and its parities
are single-shot bare ancillas. This section is the rebuild around correctness. It fixes one
code, the rotated surface code at d = 3, the textbook home of lattice surgery, and makes
every gadget a **place** whose program is checked twice: as hardware (the 27 rules, §5) and
as logic (below). Code: `qccd/gadget/{categories,surface,town,city,place_checks,verified,
svg}.py`, `leaves/{bay,places}.py`, `logic/`.

### 9.1 Places, by category

A gadget is a place many ions go to for one job; blocks travel between places as people
travel between a school, a hotel and a hospital. Every category has its own colour and its
own silhouette, defined once (`categories.py`) and drawn identically by the tool, the
landing page and the SVG renderer.

| category | place | silhouette | master | ops |
|---|---|---|---|---|
| Logical Preparation | school | house | `prep_d3` | `prep.Z`, `prep.X` |
| Syndrome Extraction | clinic | octagon | `se_d3` | `se.1`, `se.3` |
| Logical Zone | hotel | vault | `zone_d3` | `check_in`, `check_out` |
| Logical Operation | workshop | hexagon | `tcx_d3` | `cx` |
| Lattice Surgery | bridge | arched bridge | `ls_d3` | `zz`, `xx` |
| Logical Readout | post office | stadium | `read_d3` | `read.Z`, `read.X` |
| Magic State Factory | factory | sawtooth roof | `msf15` | `produce` |
| Magic State Injection | pharmacy | chevron | `inj_d3` | `inject`, `inject.Y` |
| Ion Depot | station | garage | `depot9` | `load.1/8/9`, `dump.9` |
| Roads and Junctions | road | dot | `junc9` | `pass.<a>.<b>[.<k>]` |
| Decoder | control room | chip with pins | `dec_d3` | `decode` (§10) |
| Classical Memory | archive | drum of cells | `arch1` | `record`, `update`, `resolve` (§10) |

**The bay.** Every ring place is one device family (`leaves/bay.py`): a racetrack of `2W`
storage slots, dock traps on outward spurs (so the two rows never share a trap position),
and doors on the corner slots, each a spur to a port trap and a stub. Blocks enter at the
left doors and leave at the right ones, so with the ring turning clockwise a place is FIFO.
Residents (ancillas, a surgery seam, factory staff) live in the docks or on the ring and
come home at the end of every op. Ions inside are cooled by name every few arrivals and
departures, since a broadcast cool is illegal while an ion waits on a stub (R6): the
hottest ion in any place op is n̄ ≈ 131 (lattice surgery peaked at 546 before).

**The conveyor round.** Ancillas stay in their docks; data ions ride the ring. At every
step each data ion standing beside the dock of the check it is due at next — and whose
earlier contacts are done — docks, all docked pairs take one batched CX, and the ring
turns one slot. The schedule it realises is the Tomita-Svore four-layer order (X checks
NW-NE-SW-SE, Z checks NW-SW-NE-SE) recorded as a *partial order* (each ancilla's contacts in
sequence; on each data ion, contacts with checks of different type in layer order); any
execution respecting it is the layered circuit up to commuting gates, so the machine never
has to stand in layers. `place()` searches data slots and dock assignments for the shortest
round (seeded).

**Lattice surgery** merges two 3×3 patches through a resident seam into a 7×3 (Z̄Z̄) or
3×7 (X̄X̄) patch of the same rules (`surface.merged_patch`): seam in |+⟩ (|0⟩), three merged
rounds by the same conveyor compiler, seam measured in X (Z). The outcome is the product of
the four seam checks of the measured type.

### 9.2 The logic checks

`logic/` is pure Python, like the rest of the site build.

- **The circuit** of an op is read off its TSIR: `gate`, `measure` and `reset` in program
  order on the ions they name (`circuit.from_tsir`). Ions keep identity through every move,
  which is what makes this sound.
- **Stabilizer flows** (`spec.py`, `tableau.py`). A flow `P → Q ⊕ rec[S]` says the value P
  had before equals the value Q has after, XOR records S. The circuit runs once on a
  tableau whose signs are affine functions of the measurement record, from a Choi state
  (every ion Bell-paired with a reference), so one run covers every input and every
  outcome. Resets measure into hidden variables; a recorded outcome that depends on one is
  solved for. A spec is **complete** when its flows' symplectic rank equals the kept input
  qubits plus the kept output qubits: then no other channel satisfies them.
- **Fault distance** (`experiment.py`, `faults.py`). The op sits between ideal encoders
  (each logical qubit Bell-paired with a reference) and ideal decoders (stabilizers, then one
  product measurement per logical flow). A deterministic record parity is a detector if it
  involves no logical measurement and an observable if it does — canonical, because of the
  references. Every fault of the standard circuit-level model is one bit of a Pauli frame,
  all propagated at once; one fault with no detector and some observable, or two with equal
  detector signatures and different observables, is a logical error of weight ≤ 2.
- **The factory** (`statevec.py`): a sparse state vector shows acceptance probability 1 and
  output T†|+⟩ exactly; Z faults after the 15 T gates, pushed through the Clifford decoder as
  frames, show all 15 singles and 105 pairs detected and exactly 35 undetected triples, all
  logical (output error 35p³).

| place op | flows | fault distance | time |
|---|---|---|---:|
| `prep_d3.prep.Z/X` | 9, rank 9/9 | ≥ 3 | 27.9 ms |
| `se_d3.se.1` / `se.3` | 18, rank 18/18 | ≥ 3 (400 / 1,200 faults) | 31.0 / 56.9 ms |
| `zone_d3.check_in/out` | 18 identity | — | 3.0 / 2.8 ms |
| `tcx_d3.cx` | 40, rank 36/36 | ≥ 3 | 5.2 ms |
| `ls_d3.zz` / `xx` | 36, rank 36/36 | ≥ 3 (3,168 / 3,222 faults) | 203.5 / 197.4 ms |
| `read_d3.read.Z/X` | 10, rank 9/9 | ≥ 3 | 8.4 ms |
| `inj_d3.inject` / `inject.Y` | 10, rank 10/10 · 9, rank 9/9 | **1, by design** (injection is not FT) | 28.6 / 28.7 ms |
| `msf15.produce` | state vector: T†\|+⟩ exact | 1–2 T errors caught; 35p³ | 194.7 ms |
| `dec_d3.decode` | lookup table: 136 entries, 16 syndrome bits | corrects all 400 single faults of `se.1` | 1 µs (modeled) |
| `arch1.record/update/resolve` | checked where used: the sign-off reads its frames | — | 0.1 / 0.1 / 0.2 µs (modeled) |

Every one of these is also `verified` as hardware. Cross-checks (`tests/test_gadget_logic.py`,
`test_gadget_places.py`): the tableau agrees with stim's `has_flow` on random circuits; every
place's fault distance equals stim's `search_for_undetectable_logical_errors`; a CNOT turned
around, a dropped Hadamard and a missing reset fail the flows; a hook-unsafe order passes the
flows and fails the distance with a two-fault witness.

### 9.3 Logical algorithms

A program over blocks (`algorithm.py`), one instruction per line: `prep q Z|X`,
`se q 1|3`, `cx c t`, `zz a b -> m`, `xx a b -> m`, `read q Z|X -> m`, `inject t`,
`store q`, `s q` (the S̄ gadget, §10), `x|y|z q` (a Pauli kept in software, §10) and
`decode [q …]` (call the decoder, §10), each optionally `@ instance` and each but `decode`
optionally guarded by `if <condition>:` (§10). Its ideal circuit is the obvious one on one qubit per
block, with `inject` as a free input: the factory is verified by state vector, everything
after it is Clifford.

**The town** (`town.py`) is the design they run on: every place in two rows between three
streets; top doors take a vertical channel (≥ 9 sites, room to queue a bundle) to a junction
on the street above, bottom doors one below; end junctions join the streets. **The scheduler**
(`city.py`) runs instructions one after another: shortest routes over channels and junctions,
junctions passing a bundle when free, handshakes at every door from the ops' measured
per-ion crossing times, depots as sources and sinks, and any block the next instruction
does not use parked in a free zone. The GIR is the beta's format plus `"model": "places"`,
`outcomes` and `stock`.

**The checks.** `place_checks.py` re-derives G1–G9 for the place model, and adds G10 and G11 for the
classical half (§10) (§8, with loaded and
dumped ions in G2, the op's peak n̄ in G6, and a coherence budget between syndrome-extracting
ops in G8). `logic/flat.py` is the LVS that §9 of the plan promised: every event's circuit is
renamed onto global ions and every gate, measurement and reset placed at its absolute time
(exact: instructions on one ion never overlap, others commute); the flat circuit is simulated
once; outcomes and frames are decoded from the gadgets' own flow reports; and the result must
(1) satisfy every relation of the ideal circuit up to declared frames, (2) carry the same
number of random bits, (3) carry every ideal flow from inputs to surviving blocks, and (4)
for Clifford algorithms, keep distance 3 as a whole schedule (detectors: deterministic
parities no logical Pauli inserted between gadgets can flip; surviving blocks read by ideal
decoders).

| algorithm | blocks | ions | machine time | flat circuit | relation / flow | schedule distance |
|---|---:|---:|---:|---|---|---|
| `bell_cnot` | 2 | 79 | 173.9 ms | 34 ions, 50 records | Z̄₀ ⊕ Z̄₁ = 0 | ≥ 3 (1,798 faults) |
| `bell_surgery` | 2 | 79 | 294.2 ms | 49 ions, 97 records | m ⊕ Z̄₀ ⊕ Z̄₁ = 0 | ≥ 3 (4,058) |
| `ghz3` | 3 | 88 | 190.0 ms | 43 ions, 59 records | Z̄₀ ⊕ Z̄₁ = 0, Z̄₀ ⊕ Z̄₂ = 0 | ≥ 3 (1,951) |
| `teleport` | 3 | 88 | 566.8 ms | 66 ions, 176 records | 1 → X̄_b ⊕ m₂ | ≥ 3 (8,116) |
| `magic_state` | 1 | 70 | 290.1 ms | 25 ions, 32 records | X̄_t → X̄_t, Z̄_t → Z̄_t (from the magic ion) | injection: 1 by design |
| `magic_teleport` | 3 | 88 | 791.1 ms | 74 ions, 192 records | X̄_t → X̄_b ⊕ m₂; Z̄_t → Z̄_b ⊕ m₁ ⊕ m₃ ⊕ m₄ | injection: 1 by design |
| `frames` | 2 | 79 | 110.3 ms | 34 ions, 42 records | m = 1, n = 1 (both flipped by a frame the archive holds) | ≥ 3 (1,290) |
| `s_gate` | 1 | 88 | 524.6 ms | 74 ions, 185 records | m = 1 (two S̄ gadgets make a Z̄) | injection: 1 by design |
| `cond_cx` | 3 | 88 | 140.5 ms | 2 branches | `m=0`: u = 0 · `m=1`: u ⊕ v = 0 | ≥ 3 per branch |
| `teleport_fix` | 3 | 88 | 542.7 ms | 2 branches | r = 0 in **both** branches | ≥ 3 per branch |
| `t_gate` | 2 | 88 | 703.5 ms | 4 branches | X̄[t] → X̄[q0] / Ȳ[q0]; Z̄[t] → Z̄[q0], exactly | injection: 1 by design |

All pass G1–G11; scheduling, checks and sign-off take under 0.3 s each warm (a guarded
program takes one sign-off pass per branch). The checks fail
where they should (`tests/test_gadget_algorithms.py`): a Bell schedule against a |0̄0̄⟩
ideal, a GHZ schedule against `cx q2 q0`, swapped ions in a carry (G1), a dropped event
(G7), overlapping ops (G5).

**Not established:** logical error rates (distance is counted, no noise is sampled),
Clifford errors in the factory, measurement crosstalk, and throughput — the town serialises
instructions, and since the classical layer landed it serialises road trips too, so no
bundle ever queues in a channel shorter than itself (G4).

---

## 10 · The classical half: decoder, memory, wires, frames

A fault-tolerant machine is half classical, and that half is just as finite as the quantum
half: a decoder sitting somewhere, wired to every place that measures, and a memory holding
what it decides. Both are places on the same map, and the bits between them are drawn
dashed and dark, because a bit is not an ion. Code: `leaves/classical.py`,
`logic/decode.py`, the classical parts of `model.py`, `town.py`, `city.py`,
`place_checks.py`, `logic/flat.py`; the latencies and the decoder profiles come from
`qccd/analysis/feedback.py`, which the studio and the leaderboard read too.

| category | place | silhouette | master | ops |
|---|---|---|---|---|
| Decoder | control room | chip with pins | `dec_d3` | `decode` |
| Classical Memory | archive | drum of cells | `arch1` | `record`, `update`, `resolve` |
| Classical Data Bus | wire | dashed link | — | messages |

**Ports and wires.** A `Port` has a `kind`: `ion` or `classical`. A classical port carries
a `signal` (`syndrome`, `outcome`, `frame`, `decision`) and a number of `bits`, has no
device node and no stub, and cannot be joined to an ion port — G1 refuses it, and so does
the signal type (`tests/test_gadget_classical.py`). A `Channel` has a `kind` too: a `wire`
has one cost, `latency_us`, and never enters the road network the scheduler routes ions on.
Every measuring place has a `syn` port, the two that produce a logical result have `res`,
and the three whose operation can be made conditional have `ctl`.

**The control floor.** `town.py` puts the decoder and the archive below the last street and
wires them up: `place.syn → decoder.syn` (5 µs), `decoder.frame → archive.frame` (1 µs),
`place.res → archive.in` (5 µs), `archive.ctl → place.ctl` (2 µs). The wires run down to a
bus line below the town and along it, so they never pretend to be streets.

**Decoding is an instruction.** A place that measures keeps its syndrome in its readout
buffer; nothing reaches the decoder until the program says `decode`. Then each waiting
*window* -- one op's syndrome, `bits` wide -- leaves the place that measured it down that
place's `syn` wire, the decoder runs one `decode` job on it, and the frame update goes down
`decoder.frame` into the archive. `decode q0 q1` takes the windows those blocks were
measured in, including those of a |Ȳ⟩ block an S̄ gadget consumed into them; a bare
`decode` takes every window still waiting. It cannot be guarded (every syndrome is decoded
in every branch) and names no outcome. Two rules make it more than a label:

* **an outcome is not a result until its window is decoded.** A logical measurement of a
  surface code is a parity the decoder corrects, so a guard may not read one before that:
  the scheduler waits for the decode, and if the program never asked it refuses the
  guarded line with the one it needs: add `decode a` before this line;
* **every window is decoded before the program ends** -- G11 fails a program that
  measures something and never decodes it, naming each window.

**The decoder works alongside the ions.** A `decode` holds up nothing that does not read
what it decides: the next instruction starts when the ions are ready, exactly as if the
line were not there (`tests/test_gadget_decode.py` checks that adding `decode` lines moves
no ion by a microsecond), and only a guard waits for it. The shipped programs decode after
the rounds that make their results and at the end, so a makespan grows by the last
decode's few microseconds at most (`frames`: 110.2 → 110.3 ms). On the Design canvas a
`decode` lights exactly the wires it uses, and the places at their ends, while it runs,
and the playhead slows through it (§11).

**Latencies**, each from a measured system (`logic/decode.py::LATENCY`): 5 µs from a
measurement to a conditional branch on the control processor (AQT M-ACTION,
arXiv:2101.11390); 1 µs for one lookup (an FPGA belief-propagation iteration is 24 ns, IBM
real-time decoder arXiv:2510.21600, so a d = 3 table read is sub-µs); 0.1 µs to write the
archive; 0.2 µs to read it back; 2 µs for a guard to reach the place that waits for it.
The **reaction time** — last measurement to decoded result, the quantity Walking Cat
(arXiv:2604.19481) budgets a syndrome round for — is 6.1 µs here when the program decodes
straight after the round, against milliseconds per place op, so the classical half costs
these schedules nothing. That is the least it can be: a program that decodes later has
chosen to wait, and the decode's inspector shows the wait per window. A message leaves no
earlier than its bits exist (the op's *last measurement*, read from the replayed
instruction times); a syndrome then waits in its place until a `decode` sends it.

**The decoder is verified, not asserted.** `logic/decode.py::lookup_table` builds its
function from the syndrome-extraction gadget's own fault experiment (§9.2): group every
single fault by the detector signature it produces, and read off the observables it flips.
The table is a function exactly when no two faults with one signature differ in their
logical effect, and the all-zero signature must flip nothing. For `se.1` that is a 16-bit,
136-entry table that corrects **every one of the 400 single faults**. Its op's status is
`modeled`, the reporting contract's word for a number from a stated model rather than this
repository's verifier: a decoder has no ions, so there is no TSIR to replay.

**Frames: what "kept in software" means.** Every block carries a Pauli frame `X̄^ax Z̄^az`
whose two bits are *parity expressions* over logical outcomes — a constant and a set of
`(event, flow)` names, resolved to records by the sign-off. The archive holds them, and
`city.py` propagates them:

* a logical Pauli (`x`, `y`, `z`) is one `update` at the archive and touches no ion;
* a transversal CNOT copies the frame the way it copies a Pauli: `ax_t ^= ax_c`, `az_c ^= az_t`;
* the S̄ gadget conjugates it (`X̄ → Ȳ`, so `az ^= ax`) and adds its own correction;
* a measurement of `X̄^qx Z̄^qz` comes out flipped by `ax·qz ⊕ az·qx` — the frame it
  anticommutes with — and it is the *corrected* value the archive records and the sign-off
  compares;
* the decoder's window updates are written too, marked `window`: they are the frames the
  measuring gadget's own flow report already declares, which is what the sign-off allows.

**The S̄ gadget** is the first gadget built by composing places rather than by building a
device: a |Ȳ⟩ block from the pharmacy (`inject.Y`: the corner ion is reset, H and S here,
then encoded — verified complete, distance 1 like any injection), a Z̄Z̄ lattice surgery
against the data block, an X̄ readout of the |Ȳ⟩ block, and a Z̄ frame in the archive
conditioned on the parity of the two outcomes (Litinski 2019, *Game of Surface Codes*,
§4: a π/4 rotation is a Z̄Z̄ measurement against |Y⟩). `s_gate.alg` does it twice, which
must make a Z̄: the sign-off confirms `X̄[q0] = 1` deterministically, which is a check on
the correction's condition, not an assumption about it.

**Classically controlled execution.** `if <condition>: <instruction>` is a dynamic circuit:
the ions travel and the place reserves the op either way -- the worst case, so the schedule
stays static -- and only the pulses depend on a bit measured while the machine runs. That is
a pre-compiled branch with the same port contract, which is how trapped-ion control systems
run conditionals (AQT M-ACTION, arXiv:2101.11390), and it is what OpenQASM 3's `if` and the
QIR adaptive profile express at the language level.

A **condition** is exact and linear: the parity of a constant and some measured outcomes,
`c ⊕ m₁ ⊕ m₂ …`, written `m`, `!m`, `m == 0`, `m ^ k`. The archive `resolve`s it once the
outcomes are recorded and sends it down the decision wire to the place's `ctl` port; the op
may not start before it lands, which G11 checks — and a guard on an instruction that no
place can be told about (syndrome extraction has no `ctl` port) is refused with that reason.
A guarded instruction may not name a new outcome, so nothing downstream depends on whether
it ran.

**Per-branch sign-off.** A dynamic program means something different for each value of the
bits its guards read, so `logic/flat.py` signs it off once per branch: the events of a
guarded instruction that does not run are left out (its place ran the skip branch — ions in,
wait, ions out), the ideal circuit is built for that branch, the archive's lines are folded
in the order they were written with the guarded ones kept only where they hold, and the
branch's own bits are *substituted* rather than compared. That last step is what makes the
statement readable: `m ⊕ r = 1` in the branch where the correction ran is the claim `r = 0`.
A branch the algorithm can never take (a guard reading an outcome the program fixes) is
reported as unreachable instead of being checked vacuously.

The archive's lines are **operations on a frame**, not snapshots: `X̄^dax Z̄^daz`, optionally
after conjugating through S̄ (`X̄ → Ȳ`) or copying another block's frame (a transversal
CNOT). So a conditional S̄ and a conditional Pauli on one block compose, and a block may be
*corrected and then measured* -- the readout is corrected by whatever frame that branch has.
The one thing refused is a bit a guard reads that would itself depend on a branch.

**What the three examples show** (§9.3):

| | what is conditional | why it has to be |
|---|---|---|
| `cond_cx` | a transversal CNOT at the workshop | the plainest form: the same ions, the same slot, the pulses gated on one bit. The two branches promise different things (`u = 0` versus `u ⊕ v = 0`) and both are checked |
| `t_gate` | the S̄ gadget, and a Pauli | a T teleportation's S̄ correction is **Clifford, not Pauli**: no frame can hold it, so the gadget itself must run, and only when the surgery outcome says so |
| `teleport_fix` | the Pauli correction after a Bell measurement | the receiving block ends in a *known* state: `r = 0` in every branch, instead of `r ⊕ m₂ = 0` for the rest of the program to carry |

The T gadget's flows are Clifford statements, so they show that the hardware runs exactly
the conditional Clifford program stated -- teleportation, plus S̄ exactly in the branches
where the surgery outcome asks for it. That the *program* is the right one for a T gate is a
statement about a non-Clifford state, and it is checked on the state itself
(`tests/test_gadget_classical.py::test_the_t_gadget_correction_table_is_right`), while the
T-ness of the resource comes from the factory's own state-vector check (§9.2).

**The new checks.** G10: every message crosses a wire the design has, takes that wire's
latency, leaves no earlier than the op that measured its bits began (and, unless it is a
syndrome a `decode` sent from the readout buffer, no later than that op's end), and is used
by something that runs after it arrives; no frame or outcome names a measurement that has
not happened. G11: every syndrome window is decoded, by a `decode` whose message carries it
from the place that measured it; no decoding job waits past the next syndrome's arrival
(the backlog problem); no classically controlled op starts before its guard has reached
it; and no guard, nor any guarded frame write, reads an outcome before the decoder has
corrected it.

**Where the loop shows up outside this section.** The same numbers are the site's, from
one table (`qccd/analysis/feedback.py`: `LINK`, `DECODERS`, `cycle_report`, and the `cycle`
analysis, which is sweepable — *how slow may my decoder be before the backlog grows*):

* **the studio's Design tab** (`studio.html#design`) has a **QEC cycle** panel — the round
  the device on the canvas actually runs (the studio's own priced runtime, via
  `EDITOR.price()`, and nothing shown at all when that price is blocked), the loop drawn
  stage by stage, a decoder to pick, and the two verdicts. It is *injected* by
  `qccd/site/build.py` (`qccd/site/qec_cycle.py::studio_block`), so the studio's own
  modules are untouched and the panel cannot disagree with the places;
* **the leaderboard** (`/board/`) carries each board's QEC clock on its row — the round it
  ranks, cycles per second, the decoder margin and what feedback costs as a fraction of a
  cycle — and one section comparing every board, with a superconducting round as the
  yardstick. That contrast is the point: at 55 ms a round a lookup decoder has ~55,000× of
  margin and feedback costs 0.015% of a cycle; at 1 µs a round the same GPU decoder is
  0.001× — it falls behind by 999 µs every round — and the reaction time is 830% of a
  cycle. The ion transport that makes a QCCD round slow is what makes its classical loop
  nearly free.

**Not established here:** a decoder for more than one window at a time (the table is
per-round), soft-information decoding, the cost of the classical hardware itself, and
route-changing branches -- a guard can gate an op, not send a block somewhere else, and
bounded retries (a factory rejection) still need the worst-case reservation of §14's P13.
A guard also cannot yet read a bit that itself depends on a branch, and a software H̄ (a
Clifford frame rather than a Pauli one) is not implemented.

---

## 11 · The design tool

`index.html` in the build directory, plus `leaf/<master>.js` (one master's device and
programs, loaded with a plain `<script>` so `file://` works) and, with `--studio`,
`studio/<master>.<op>.html`. Canvas throughout; no network.

**One visual language with the studio.** A trapping site, a junction, a rail and an ion are
drawn here by the same numbers `studio.html` draws them by: the page is handed
`qccd/viz/theme.py`'s `PALETTE` and `GEOMETRY` and reproduces `qccd/viz/render.py`'s
shapes — a site is a capsule of length `min(0.88g, (0.30 + 0.15m)g)` and thickness
`0.1992g` rotated onto its trap axis, with one ring per slot and its stroke saying what it
is (over capacity, in play, a dock at degree ≥ 3, a corner, or just its zone); a junction is
a sharp white square of side `0.60g`; a rail is a butt-capped line coloured by its role
(`rail`, `highway`, `compute` thin); an ion is a white-outlined disc of radius `r_ion` when
it is in play and `r_rest` otherwise, with the gold halo under a site in play and the
potential-well ellipse under an ion in flight. `g` — the nearest-neighbour distance — is
measured per device and multiplied by the camera's scale, which is what the studio's fit
does. Ion colour follows the studio's rule (*what the machine is doing to this ion now*,
not what role a code gave it): a gate teal, a measurement or reset red, everything else
slate. Change the palette in `theme.py` and both tools move together.

- **Run.** The stage draws the current level's children as gadget boxes (family colour,
  running op and its progress, an inventory gauge), their ports, and the channels with ions
  streaming along them. Up to three levels further down are drawn inside the boxes, each
  leaf's ions replayed from its verified TSIR at that instance's local time, so the whole
  machine visibly moves at the top. The timeline has one row per child, the ops of its
  whole subtree (`cycle × N` in grey), and the program's instructions in lanes above:
  click to seek, drag the ruler to pan, wheel to zoom. The LogicQ source is lit as it runs;
  clicking a line opens the smallest level holding everything it touches. The inspector
  shows the selected instance (ports, ops with status badges and notes), channel, port,
  instruction, or ion — and **follows an ion** across the whole program.
- **Decode.** While a `decode` instruction runs, the dashed wires it uses are lit -- the
  syndrome wire from each place that measured, and the frame wire from the decoder to the
  memory -- with the dashes marching the way the bits go, a packet per burst saying how
  many bits and windows it carries, the places at the ends outlined, and every other wire
  stepped back. The stage badge names the instruction and how far into it the playhead is.
  A decode is a few microseconds of a program hundreds of milliseconds long, so playing
  stops at it and slows through it (it takes 2.5 s on screen, and the badge says how many
  times slower that is); clicking it in the Program pane or on the timeline puts the
  playhead where its syndromes are half-way down their wires. Its inspector lists the
  windows: which op measured them, where, how many bits, how long each waited in its buffer.
  Colours come from the `wire` entry of `categories.py` (`lit`, `glow`).
- **Descend.** Double-click an instance, pick it in the hierarchy tree, or use the
  breadcrumb; `Esc` goes up. A leaf level is the ion-by-ion view: its device, its ions by
  role, the instruction running (`visit.X1 · instruction 30/35 · cool`), with the op's TSIR
  instruction ticks on the timeline. **Studio page** (with `--studio`) opens the full
  studio debugger for that op, with every listing and all 27 rules.
- **Library.** A datasheet per master: thumbnail, ports, and every op's status, duration,
  instruction count, CX count, cools and peak n̄, with the rule report on hover.
- **Edit.** A copy of the library: place instances from a palette, drag them (channels
  re-route and re-measure), connect port to port (G1 is checked as you connect; compatible
  ports light green, the rest red, and a refused connection says why), export a child port
  as a port of the gadget, **group** a selection into a new composite gadget (every channel
  crossing the selection's boundary now ends on a port the new gadget exports), ungroup,
  undo/redo, and export `*.gadget.json`. `build … --design` re-derives everything from it
  (leaves re-characterized, geometry recomputed) and synthesizes.

---

## 12 · Measured

| | small (`--rows 1 --pairs 2`) | showcase (`8 × 6`) |
|---|---:|---:|
| code blocks / logical qubits | 4 / 48 | 96 / 1,152 |
| ions at rest | 415 | **9,720** |
| instructions (T gates) | 13 (1) | 335 (24) |
| events / carries | 95 / 64 | 3,456 / 2,626 |
| machine time (makespan) | 456.6 ms | 995.6 ms |
| G1–G9 | all pass | all pass |
| synthesis + checks (warm cache) | 0.2 s | 0.6 s |
| characterizing all leaves (cold cache) | ≈ 15 s | same leaves |
| page + leaf data | 0.23 MB + 2.2 MB | 1.7 MB + 2.2 MB |
| page ready in headless Chrome (`file://`) | — | 0.83 s |
| one frame at the top level, every leaf's ions replayed | 2.5 ms | 10.9 ms |
| JS heap | — | 21 MB |

**A frame costs what it draws.** The 10.9 ms above is a 1200 × 800 window; the same page,
same build, measured across three windows: **11.2 ms at 1200 × 800, 15.7 at 1400 × 900,
47.9 at 1700 × 1050** (best of 40 calls, showcase, 9,720 ions). So quote the window with
the number or the number means nothing -- a figure remembered without its window looks
like a 4x regression the next time somebody measures on a bigger screen. It is not one:
that is what this note exists to say.

Tests: `tests/test_gadget_frontend.py` (23), `test_gadget_schedule.py` (25, including a
mutation for every G-rule), `test_gadget_page.py` (3: the page's JS replay of every leaf
program ends on exactly the Python verifier's positions), `test_gadget_browser.py` (7:
headless Chrome, every level, the editor round trip, a verified algorithm page, all
thirteen silhouettes), `test_gadget_logic.py` (12), `test_gadget_places.py` (10),
`test_gadget_algorithms.py` (18), `test_gadget_classical.py` (22: the decoder's table, a
wire that refuses ions, causality and backlog, the frames the archive holds, the S̄ gadget,
guards and their delivery, a sign-off per branch, and the mutations of a conditional
correction that must fail) — 120 tests, about four minutes cold.

---

## 13 · Formats

- **Library / design** `*.gadget.json`, schema `qccd.gadget/0.1`: `{top, masters{…}}`.
  `library.json` in a build is the characterized library; the editor exports the same shape.
- **Program** `*.lq`: §6.1.
- **GIR** `gir.json`, schema `qccd.gir/0.1`: `{top, makespan_us, n_ions, ion_order, leaves,
  nets, blocks, program, events, carries, refused, idle_us, channels, stats}` (§4). The
  place model writes `qccd.gir/0.2`, which adds `model: "places"`, `outcomes`, `stock` and
  the classical half (§10): `wires{name: [leaf, port, leaf, port, latency_us]}`,
  `messages[[id, wire, from, port, to, port, t0, t1, bits, signal, instruction, group,
  label, source event]]`, `frames[{id, t, block, kind, instruction, event, ax, az, text,
  why}]`, `block_frames{block: {ax, az}}` and `control{decoder, archive, latency_us}`.
  A parity expression (`ax`, `az`, an outcome's `flip` and `value`) is
  `{const, sources: [[event, flow]]}`: a constant and the logical outcomes it is the parity
  of, each named by the event that measured it and that gadget's own flow.
- **Checks** `checks.json`: `{passed, failed, skipped, violations, metrics}`.
- **Leaf data** `leaf/<master>.js`: `{master, device, arch, zones, home, roles, programs,
  times}`, wrapped as `window.__gadgetLeaf(…)`. A classical place has no `arch` and no
  `programs`: `classical: true`, a device to draw and nothing to replay.

---

## 14 · Phases

| | deliverable | state |
|---|---|---|
| **P0** | this plan; model, codes, LogicQ front end | ✅ `k` and bases match the Tutorial; every accepted form parses |
| **P1** | leaves `mem`, `tcx`, `res`, `xjunc`, channel pricing; characterization with cache | ✅ every op `verified` |
| **P2** | composites, synthesis, GIR, G1–G9, CLI | ✅ the 9,720-ion showcase passes every G-rule |
| **P3** | the page: run, descend, leaf replay, library, follow an ion | ✅ headless walk, JS/Python replay parity |
| **P4** | edit: place, move, connect, group, ungroup, export, `--design` | ✅ an edited design round-trips and synthesizes |
| **P5** | `fac_t15` and the magic path | ✅; PPM gadgets for single logicals, mixed-basis PPMs, OpenQASM input: open |
| **P6** | flat sign-off on a small design | ✅ as logic (§9.3: every example algorithm); the flat 27-rule replay across instance devices: open |
| **P7** | verified place library: categories and silhouettes, logic checks, fault distance | ✅ §9.1–9.2, every op verified, distances cross-checked with stim |
| **P8** | Logical Algorithm: the town, the scheduler, place-model G1–G9, end-to-end sign-off | ✅ §9.3, eight algorithms; a composer for new towns in the page: the editor, not yet the scheduler |
| **P9** | the classical half: classical ports, wires, messages, the control floor, G10/G11 | ✅ §10; drawn dashed with packets, causality and backlog checked |
| **P10** | the decoder as a gadget: a lookup table verified against the SE gadget's own faults, stated latencies, reaction time | ✅ §10; 136 entries, every single fault corrected |
| **P11** | corrections kept in software: the classical memory, the frame algebra, the S̄ gadget | ✅ §10; `frames.alg`, `s_gate.alg`; the sign-off allows exactly the archive's frames |
| **P12** | classically *controlled* execution: guards on ops (`if (m) …`), pre-compiled branches with identical port contracts, per-branch sign-off | ✅ §10; `cond_cx`, `t_gate`, `teleport_fix`; mutate a guard or a correction and the sign-off fails |
| **P13** | route-changing branches: bounded retries (factory rejection), worst-case reservation | open |

## 15 · Open questions and findings

- **The comb station heats bundles to n̄ ≈ 221** on the way out, because every rail site is
  a degree-3 junction and the exit conveyor crosses up to 72 of them. It passes G6 (the
  ion-loss limit is ~510 quanta) and the memory cools on absorb, but a station whose rails
  cross no junction (interleaving the bundles at one Y-junction) should cut it by ~70×.
- **Bare-ancilla PPM** (beta) measures a logical once with one messenger: not fault-tolerant
  against hook errors. The verified library measures Z̄Z̄ and X̄X̄ by lattice surgery instead
  (§9.1); for BB codes the analogue is a gauging measurement (arXiv:2410.02213).
- **Next for the verified library:** logical H (transversal H plus the 90° patch
  permutation, which QCCD can do by moving ions), BB codes as places (their SE rounds now
  need the same partial-order compiler), parallel scheduling in the town, and noise sampling
  for rates. The S̄ gadget landed in §10 (a |Ȳ⟩ injection consumed by Z̄Z̄ surgery), so a T
  teleportation can now be corrected — what is still missing is the *conditional* form:
  running the correction only when the outcome says so, rather than folding it into a frame.
- **A Clifford frame, not just a Pauli frame.** The archive has a cell for a block's Clifford
  frame, and the algebra for it is worked out (a readout basis conjugated by U, `zz`↔`xx`
  swapped under H, a two-block op refused when the two frames differ), but only the Pauli
  frame is implemented: a software H would change which physical op a later instruction must
  use, which is P12's business.
- **The decoder decodes one window at a time.** Its table is per-round and per-gadget, built
  from that gadget's own fault experiment; a real sliding window across rounds, soft
  information, and the cost of the classical hardware itself are all out of scope here.
- **LogicQ's single-logical gadgets** (`progHAt`, `progSAt`, `progCNOTAt`) are sequences of
  PPMs with an ancilla logical, all expressible with messengers; they are the next
  realisation to add.
- **The control-domain assumption** against the flat sign-off: how much do shared DACs
  cost? P6 answers it for a small design.
- **Fold-transversal gates** (H on every ion plus a qubit permutation): a permutation is
  free in QCCD only if the memory keeps a second compiled round for the permuted placement.
- **Measurement crosstalk** between neighbouring traps (the factory's T ion is measured
  while a messenger waits one trap away) is not modelled.
