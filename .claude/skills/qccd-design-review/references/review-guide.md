# Review guide: reading failures, fixing them, and what the checkers cannot see

Hand-written. The generated files say what each rule asserts and how the grader treats it. This
one gives a reviewer's judgement: what a failure usually means, what to change, and what to look
at even when everything passes. Pointers into the code say where each claim can be checked. If
the code moved, trust the code and the generated references.

## Contents

1. Reading a failure
2. Causes and fixes, rule by rule (2b: the machine rules)
3. Beyond the checkers: look at these in every review
4. The checkers' known blind spots
5. Stages that are not rules

## 1. Reading a failure

- **Find where it fails before you say why.** A grader diagnostic names the instruction
  (`instr 6`) or, for a device-level rule, the node or rail (shown as `device:`). A rule-language
  counterexample names the values that make the assertion false:
  `g = MS q0,q1 at C0, step 5, i = ion q0: heat(i, start(g)) is 6.1 and heat_budget() is 1`.
  Quote one of these in the finding.
- **Count distinct problems, not counterexamples.** The language checks ordered pairs, so a
  single sharp corner can appear as two cases (`r1, r2` and `r2, r1`). Count each node, rail
  or instruction once.
- **A failure under the board's physics is not always a design flaw.** The grader scores the
  device with the board's `primitives`, `heating`, `species` and `budget`
  (`qccd.workspace.tasks.scored_device`). Suppose a programme was cooled or timed under the
  device's own physics, or the template's. Then it can fail R7 on the board while passing at
  home. Say which physics produced the failure.

## 2. Causes and fixes, rule by rule

One row per rule of every shipped set (a test keeps it so: a new rule fails CI until it has a row
here). Device rules first, in the catalogue's groups; the machine rules (F, G, H) are in 2b.

| Rule | Usually means | Fix |
|---|---|---|
| R0 | an operation names an ion somewhere it isn't: a programme replayed on a different device than it was compiled for, or a hand-edited programme | recompile for this device. Check that the bundle's device is the one the programme was written for |
| R1 | more ions in a site, or passing through it, than its capacity | raise the zone's capacity where the physics allows it (R13 caps a gate chain at 15), or route through a site with room |
| R2 | two ions in one junction at once, resting or passing | serialise the crossings, and don't park ions on junctions (see R23) |
| R3 | more ions on a rail than its capacity in one step | serialise, or give the rail more capacity only if the device really can hold it |
| R4 | a transport step uses a movement class the device doesn't declare, or more classes in one cycle than `control.max_simd_classes_per_cycle` | declare the class in `control.classes` (Studio/workspace: operation `call`, method `declare_class`), or split the cycle |
| R4b | one cycle mixes transport with gates, or intra- with inter-trap transport | split the cycle: one mode per cycle |
| R4d | the control channels cannot drive the step. Either ions on one channel move differently, or a loaded site must hold still without a per-site switch | set `control.channels.switch_per_site: true` if the hardware has one; otherwise make every ion on the channel move the same way, or split the step. The grader's verifier reports this under **R4** |
| R5 | two ions swap ends of one rail in one step | reroute one of them, or exchange them inside a trap (an ion swap) instead |
| R6 | a gate, measurement/reset or cooling at a site whose zone type lacks that capability | give the site a zone type that has it (the device's `zone_types`: `gate`, `spam`, `cool`), or move the operation |
| R6b | the two ions of a 2-qubit gate are not in the same trap when it starts | transport them together first. A programme that names `sites` for a gate must name the one they are in |
| R7 | an ion is hotter than the board's heat budget when a 2-qubit gate starts | **re-cool under the board's physics** (snippet below). Inserting `cool` is the one change a board allows between the certified and the final programme. Otherwise, fewer junction crossings or shorter transport before the gate |
| R7b | a gate zone absorbs more heat over time than its duty budget. It does not apply today: no device declares a duty budget | nothing to fix yet. Once a device declares one, spread the gates over more zones or leave the zone idle between them |
| R7c | a programme with 2-qubit gates and no cooling at all | insert cooling (snippet below) |
| R8 | an ion takes part in two operations at once, appears, vanishes, or moves without an instruction moving it | a programme bug: recompile. If it is hand-written, find the instruction the diagnostic names |
| R9 | a time, cost, step count or heat figure the programme states about itself is not what its replay computes. Advisory: the grader skips it, and the review script checks it | recompile, or drop the stale figures. A hand-edited programme often keeps its old totals |
| R10 | the pulses, read through the certificate's qubit-to-ion map, do not compose to the circuit, or a gate is not performed once by its own ions in a trap that can gate. Only `certificate_binding`, `lean_certificate` and `semantics` decide it | compile the board's own circuit, and keep the certificate with the programme it was made for. If those three stages pass on another machine but not here, the toolchain is missing (section 5) |
| R11 | ions on one loop move in different directions in one step, or a junction degree has no crossing cost (5- and 6-rail junctions cross at the X-junction's cost unless the device prices them) | one direction per loop per step. Price every junction degree the device uses (`junction_cross`), and note that a price the board lacks is listed as added physics |
| R12 | two gates in one trap at once | serialise them |
| R13 | a gate starts in a chain longer than `max_chain` (15) | split the chain first, or cap the zone's capacity |
| R14 | an ion leaves a chain of more than 2 without an accounted `gate_swap` (the check charges every such split) | account the swap, or keep chains at 2 |
| R15 | `allowed partial` by design: heat from successive motions is added up, so every heat figure is an upper bound | nothing to fix. Report heat figures as upper bounds |
| R16 | a property of the board's cost model (gate error read from the ions' heat), not of the design: the same verdict for every entry on the board | nothing in the design. A failure means the board's physics lacks the property: tell the board's maintainers |
| R17 | a property of the board's cost model (background heating accrues with time), as R16 | as R16 |
| R18 | true by definition: `junctions` are the nodes where three or more rails meet, and crossing one is priced by that number | nothing. A failure means the vocabulary changed: read `qccd/ruleset/vocabulary.py` |
| R19 | a node joins more rails than `budget.max_junction_degree` (default 6; a flat surface trap declares 4) | fewer rails at that node: split it into two junctions |
| R20 | two rails at a node are closer than `budget.min_rail_angle_deg` (default 60°) | move the node, or cut the corner. A triangle's 60° corners become two 120° corners, and the shape still reads the same |
| R21 | a rail passes through a node it doesn't end at, or two rails cross away from a shared node | put a junction where they cross, or reroute. A periodic grid cannot be drawn flat, so use `cylinder` |
| R22 | the moving ions of one broadcast cycle don't all make the same motion | split into uniform cycles (the compiler's pass `qccd/compile/uniform.py` does it). Docks drawn as a sketched open path read as different motions, so draw docks as plain rails |
| R23 | an ion ends a cycle on a junction while the traps have room for every ion | move it on to a trap. A conveyor's own slots are exempt |
| R24 | a 2-qubit gate of the circuit is never performed, or one is performed that the circuit doesn't ask for | a compiler or bundle mismatch. Check that the certificate's circuit is the board's circuit (the binding stage) |
| R25 | two operations on one qubit run out of the circuit's order, even commuting ones | keep the circuit's order. This website's compilers never reorder, and the Lean checker's order clause is strict |
| R26 | as R25, but consecutive CNOTs in which a qubit plays the same role (always control, or always target) may run in any order among themselves: a failure is a reorder beyond that freedom | keep the circuit's order outside runs of same-role CNOTs |
| R27 | two operations of the kinds in the set's `serial_kinds` overlap in one trap. The paper's machine does one such operation per trap at a time | serialise them in that trap. The set's `serial_kinds` (`references/rule-sets.md`) says which kinds count. 0 cases means the programme never had two in one trap |
| R29 | a paper's timed schedule gives an operation a duration other than its clock's law. It reads words only a schedule has, so it never applies to an entry's programme (`does not apply`) | nothing for a programme entry |
| R30 | a paper's timed schedule runs two operations that hold one component at once. Only a schedule declares what an operation holds, so it never applies to a programme | nothing for a programme entry |
| R31 | broadcast hops that overlap in time don't start and end together, or are of different classes, or a hop names no class | make every broadcast cycle one class with one start and one end (uniform cycles: `qccd/compile/uniform.py`) |
| R32 | a rotation, preparation or measurement runs in a zone outside the set's `single_ion_zones` | move the operation to an allowed zone, or give the device such zones |
| R33 | the two ions of a 2-qubit gate stand in zones that are neither the same nor neighbours when it starts | bring them into one zone, or neighbouring ones, before the gate |
| C1 | a C2LR leg holds two crystals other than between an enter and its exit, or a crystal that entered a partner leg leaves by another step type than the one it came in by | keep one crystal per leg. A crystal that entered a partner leg leaves by the same step type. The C2LR router (`qccd/compile/c2lr_router.py`) keeps both |
| C2 | a transport step on a C2LR device writes its own routes, or chooses anything per leg beyond the direction bit | emit steps as direction bits on the device's matching, as the router does. Never hand-write the routes of a C2LR class |
| C3 | a step ties horizontal and vertical legs together (a bit per axis, not per crystal) | give every crystal its own direction bit |
| C4 | an exchange step is not "C2LR, one shared junction waveform, C2LR back": two waveforms, or a shift that is not undone | use the device's `swap_or_stay` primitive as one step. Don't compose a step from separate moves |
| C5 | a crystal ends a step somewhere other than a leg centre | end every step with every crystal at a leg centre |
| C6 | a step pairs legs other than by the device's matching, or uses a form the set doesn't allow (`c2lr_forms`) | use a demonstrated diagonal (W-S, W-N, E-S, E-N). Under `c2lr` the checkerboard is allowed but **assumed**: the review reports it. `c2lr-demonstrated` (Goal 3's C2LR family) refuses it |
| C7 | a crystal rests on a junction when a step ends | a step carries crystals through junctions; never leave one there |
| C8 | `allowed partial`: the COM frequency held during a shift is recorded with the step's primitive, not decided from the programme. Skipped (and blocking) when the primitive records none | nothing in the programme. Record the COM frequency on the device's `swap_or_stay` primitive (`Machine.c2lr_grid` does) |
| C9 | a step heats only the crystals that move, as if one that sat it out stayed cold. The shared waveforms heat every crystal | give the device's `swap_or_stay` primitive its stay heat (`quanta_stay`, which `Machine.c2lr_grid` writes for Ba-Sr and Yb-Ba), then replay under the board's model |
| C10 | a device with C2LR control whose document is not shared signals plus one input and one switch per leg | write the `control` block as `Machine.c2lr_grid` does, or don't declare C2LR control |
| budget (device stage) | too many DACs (B1) or junctions (B2) for the board (`references/boards.md`) | broadcast wiring instead of direct drive (a direct-wired grid needs thousands of DACs), or fewer junctions. Declaring fewer electrodes buys nothing, because counts are floored by the physics |

**Re-cooling under the board's physics** (fixes R7 and R7c without recompiling, and keeps
`correspondence` passing):

```python
from qccd.arch.device import Architecture
from qccd.compile.cooling import CoolingPolicy, insert_cooling
from qccd.cost.models import corrected_model
from qccd.ir.tsir import TSIR
from qccd.workspace.tasks import find_board, scored_device
rel = find_board("<board>")
arch = Architecture.from_json(scored_device(device_doc, rel)[0])
model = corrected_model(rel.manifest["physics"]["rank_table"])
final = insert_cooling(TSIR.from_json(certified_doc), arch, model, policy=CoolingPolicy()).program
```

Then rebuild the bundle with `final` as `program.tsir.json`. In a workspace,
`qccd submit --local --board <b>` compiles, cools and grades in one step.

### 2b. The machine rules: a floorplan of gadgets, and a schedule on it

`review.py --floorplan` judges F1-F9; `--algorithm` schedules the program and judges all of
them. A counterexample names the places, doors, roads, carries or events involved. Fix the
floorplan (`place NAME MASTER at x y`, `road`, `junction`, `wire` lines) or the algorithm. When
the floorplan is clean and a G rule still fails, suspect the scheduler, and report it with the
`.alg` and `.floor` files.

| Rule | Usually means | Fix |
|---|---|---|
| F1 | a road or wire joins doors that carry different things (medium, role, width or code), or that face the same way | join doors that match, or use an `inout` door |
| F2 | a place's ion door is on no road or on two, or a junction arm is on two roads | give every door exactly one road, and every junction arm at most one |
| F3 | two footprints overlap | move a place (its `at x y`) until its footprint is clear |
| F4 | a road passes through a place it does not serve, or two roads cross or touch away from a junction | put a junction where roads meet, and route around places (`via` points) |
| F5 | a road ending at a place's door is shorter than the bundle the door takes | lengthen the road: move its junction, or add `via` points |
| F6 | some out door cannot reach some in door along roads and junctions | add the missing roads or junctions |
| F7 | a measuring or controlled place has a classical door wired to neither the decoder nor the classical memory | wire it (`wires auto bus ...`, or an explicit `wire`) |
| F8 | the decoder's frame door is not wired to a classical memory | wire it to the archive |
| F9 | a place stands outside its family's region (zones in memory, factories in factory, ...). It does not apply to a floorplan without regions | move the place, or put it under its region's heading |
| G1 | as F1 for the roads the schedule uses, or bundles arrive in another order than the op's verified program takes them | match the doors, and route bundles so that they arrive in the op's order |
| G2 | an ion leaves from where it is not, a resident ends away from home, or a loaded ion ends neither at a depot nor in a living block | follow the ion the counterexample names. Usually a scheduler bug |
| G3 | a place holds more ions than it was built for, at some instant | stagger the arrivals, or use a place with more room |
| G4 | a road holds more ions than its sites, or carries bundles both ways at once | serialise the opposite traffic, or lengthen the road |
| G5 | two ops of one place overlap | serialise them. Two instructions racing for one place is the usual cause |
| G6 | an ion's n-bar passes the ion-loss limit in an op, at a door, or on its way between places | shorter routes with fewer junction passes, or cooling on the way |
| G7 | an instruction is refused or never realised, or a block's instructions run out of program order | read the refusal (`--algorithm` prints the schedule's counts). The floorplan may lack a place the instruction needs |
| G8 | a block waits or travels longer than its coherence budget between syndrome extractions | let the build add syndrome rounds (its `refresh`), or shorten the trip |
| G9 | the schedule runs an op that is not verified, or whose logic check failed | verify the place (`python -m qccd.gadget places`) |
| G10 | a classical message uses no wire or the wrong one, takes the wrong latency, leaves before its bits were measured, or is read before it arrives | wire the doors (F7, F8) and order the classical traffic |
| G11 | a syndrome is never sent to the decoder, the decoder falls behind, or an outcome is read before the decoder corrected it | add `decode` instructions, or give the decoder room between rounds |
| G12 | a carried bundle is not taken in at the far end exactly once, in order | usually a scheduler bug: report it with the inputs |
| G13 | two bundles share a road at once | serialise them, or add a parallel road |
| G14 | more of a bundle's ions are on a road's sites than the road has | lengthen the road, or split the bundle |
| H1 | a carry, compiled onto its road's own device, breaks one of R1-R23, delivers an ion before the far side takes it, or shares a door's stub with the op there | a road's program is a device programme: read which device rule failed (section 2), then reshape or lengthen the road |
| H2 | two devices that share a plane of DACs, or a laser, play different cycles at once | let them take turns, or give them planes of their own |

## 3. Beyond the checkers: look at these in every review

`rule-check.md` already reports the facts behind items 1-3, 5, 7, 8, 9 and 10 (fit, physics
lock, budget, the shipped device of the same name, each rule's closest case, cases per rule, R9,
buildability). Your job is the judgement: what each fact means for this design.

1. **Board fit.** Does the device hold the circuit's qubits (total capacity), and does its shape
   suit the compiler that will route it? The conveyor drives only the first closed loop plus
   docks one rail off it. The router needs a lattice with enough gate sites. Two separate loops
   never meet. A design named after a shipped device (`arch/<name>.arch.json`) but wired
   differently is worth a sentence. A reader citing "grid9x9" will assume the shipped wiring.
2. **Physics the board overrides.** `physics_lock` lists `ignored` numbers (the device's, unused)
   and `added` ones (the device's, used because the board has none). Ignored numbers mean the
   design was tuned for physics it won't be scored with. Added numbers mean the entry is ranked
   with the "any rules" entries, not on the board's own ranking.
3. **Budget headroom.** Look at DACs and junctions against the budget, and the wiring notes
   about counts floored by the physics. A design near the limit is fragile to small edits.
4. **Its own rule set.** If the entry declares `rules.json`, its own set decides the rules stage,
   and the board's set decides only whether it is on the board's own ranking. Report both. The
   review script's table then has a column for each. Every Goal 3 entry does this: each family
   declares its paper's set (`references/boards.md`). List what the set drops, replaces and adds
   (`conformance`), and judge whether each drop is physically justified. For example, a trap
   built in two layers may drop R20/R21. A drop made only so the design passes is not
   justified. Core rules (R0, R1, R8, R24) cannot be dropped. A paper's set can carry rules that
   only its timed schedule can judge (R29, R30): on a programme they `do not apply`, which
   neither fails nor proves anything.
5. **Heat margin.** R7 passing by a hair (peak n-bar just under the budget) is worth a sentence.
   Every heat figure is an upper bound (R15), so the real margin is no smaller. The review
   script's "closest case" note gives the tightest case of each rule that compares with a limit
   (R7's heat, R1's occupancy, R20's angle, G6's ion-loss heat), with where it is. A site or road
   at exactly its capacity (slack 0) is legal; say so only when a small change would break it.
6. **Control model.** `direct` wiring makes R22 vacuous and costs DACs. `switch_per_site: false`
   is what the R4d hold clause bites on. A C2LR device is judged by C1-C10, and its checkerboard
   steps are assumed.
7. **The programme's own claims (R9, advisory).** If the programme states totals that the replay
   doesn't reproduce, the grader won't notice (it skips R9). A reader of the programme would.
8. **Buildability beyond the rules (information only).** `qccd.phys` derives the electrodes a
   device implies and runs a DRC. The review script reports it (`--tech` picks the preset; the
   CLI `python -m qccd phys` only takes shipped devices). It is not part of grading. Many
   shipped designs are not DRC-clean under the ETH preset, so report it as an observation,
   never as a failure. Refused shapes (a diagonal rail the builder can't draw) are the useful
   part.
9. **Local and official.** When the entry is on the official board, compare the local grade with
   the server's `/report`. The LER may differ within its interval. Anything else that differs is
   worth reporting.
10. **Rules with nothing to check.** A `pass (nothing to check)` rule (0 cases) didn't constrain
    this design. That is worth saying when a reader might take the pass as evidence: "R14 holds"
    means little for a programme that never splits a chain.

## 4. The checkers' known blind spots

These hold as of the code this guide was written against. Each pointer says where to re-check it.

- **R4d is reported under R4 by the grader's verifier** (`qccd/verify/rules.py`, `r4_drivable`
  files `Violation("R4", ...)`). The grader's report can show R4 failing while R4d reads
  `passed`. The rule language reports R4d under its own id. The review script's two columns show
  both.
- **R15, R16 and R17 read only the board's cost model, and R18 is true by definition**
  (`qccd/verify/__init__.py`, `verify`; the words' groups in `qccd/ruleset/vocabulary.py`). R16
  and R17 are marked checked whenever the cost model models heating and time. R18's
  `junctions` are, by definition, the nodes where three or more rails meet. None of them says
  anything about a design. The review script finds model-only rules from the words they use,
  so a new one is labelled the same way.
- **A device alone decides only some rules.** Those whose words are all about the device and
  the board's physics (`references/rules.md`, "Reads") are judged. R11 is half and half:
  without a programme, only its "every junction is priced" half is judged.
- **R29 and R30 never apply to a programme.** They read words only a paper's timed schedule
  has (`clock`, `locks`). A paper's set that carries them is judged without them.
- **The place checks cover fewer machine rules than the rule language.** The Python place
  checks (`qccd/gadget/place_checks.py`) judge G1-G11, H1 and H2. F1-F9 and G12-G14 exist only
  in the rule language. The machine review shows both columns.
- **R0, R24 and R25 are not run by the rules stage of a `standard` board.** The board's set is
  checked by `qccd.verify`, which has no such rules (`references/grader.md`). Their substance is
  covered in part by the Lean checker's conjuncts (`Compiler/lean/QCCDC/Cert/Check.lean`:
  `MovesAreHops`, `GatesLegal`, `Covered`, `Grounded`, and `RespectsOrder`, strict since
  2026-10-19). The review script judges them directly in the rule language, with the
  certificate's circuit. On C2LR boards the rules stage runs the rule language but passes no
  circuit, so R24 and R25 do not apply there either.
- **R10 is skipped by the rules stage on purpose.** The certificate binding, the Lean checker
  and the semantics stage decide it. Outside the Clifford fragment the semantics stage's
  tableau check is `partial`.
- **R14 is conservative.** Order inside a chain is not tracked, so every split from a chain of
  more than 2 is charged, whether or not the ion is at the end.
- **R15 is partial by design.** Heat is added up, while the true composition can cancel. Heat
  figures are upper bounds.
- **R23 has a precondition.** If the traps off the junctions cannot hold every ion, it does not
  apply.
- **R7b cannot apply yet.** No device declares a per-zone duty-cycle budget.
- **What two moving ions do on one waveform across different paths is not judged** (R4d's
  off-path count is per class of sites, not a field solve). That is a layout question for a
  reviewer, not a checker verdict.

## 5. Stages that are not rules

| Stage | Fails when | Typical fix |
|---|---|---|
| `bundle` | a file's digest is wrong, a member is unknown or missing, or the bundle is for another task release | rebuild the bundle for this board's release |
| `device` | the device doesn't load, exceeds `max_nodes`, or is over the DAC/junction budget | fix the document; see budget above |
| `physics_lock` | the device with the board's physics doesn't load | usually a C2LR or exotic primitive the board can't take |
| `program` | not a valid TSIR, or over `max_instructions` | regenerate the programme |
| `correspondence` | the final programme isn't the certified one plus inserted `cool` instructions with fresh ids | only add cooling after certifying, never edit other instructions |
| `certificate_binding` | the certificate's circuit isn't the board's circuit op for op, or a gate witness names the wrong instruction | compile the board's own circuit |
| `lean_certificate` | the proved checker rejects the certificate (or `unsupported`: no `qcheck`) | a compiler bug if rejected; install the toolchain if unsupported |
| `semantics` | the composed pulses don't implement the circuit (O2), or transport replay disagrees (O1) | a compiler bug: report it with the bundle |
| `ler` | the detectors aren't deterministic with the circuit's parity (memory boards) | the programme doesn't run the memory experiment the board defines |
| `metrics` | the replay under the board's tables fails | usually follows an earlier failure |

The evaluations' requirements (`references/grader.md`, generated from `qccd/evalset`) are
checks beside the rules, with ids of their own. **Goal 1's G1-G3 are not the machine rules
G1-G3.**

| Requirement | Where it is enforced | Fails when |
|---|---|---|
| B1, B2 (`board`) | the `device` stage (the review's budget line) | the wiring needs more DACs, or the device more junctions, than the board's budget |
| G1 (`goal1`) | the `rules` stage | any rule of the standard set fails |
| G2 (`goal1`) | Goal 1's listing, not eligibility | the programme places more than 200 ions (the review reports the count). The official listing filters by the board's experiment, which every Goal 1 board keeps under 200 |
| G3 (`goal1`) | the `ler` stage's noiseless check (`QEC.CHECK_FAILED`) | with noise taken out, a detector or observable is not the fixed 0 the experiment's circuit gives it |
