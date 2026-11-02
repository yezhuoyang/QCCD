# Review guide: reading failures, fixing them, and what the checkers cannot see

Hand-written. The generated files say what each rule asserts and how the grader treats it. This
one gives a reviewer's judgement: what a failure usually means, what to change, and what to look
at even when everything passes. Pointers into the code say where each claim can be checked. If
the code moved, trust the code and the generated references.

## Contents

1. Reading a failure
2. Causes and fixes, rule by rule
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
| R7c | a programme with 2-qubit gates and no cooling at all | insert cooling (snippet below) |
| R8 | an ion takes part in two operations at once, appears, vanishes, or moves without an instruction moving it | a programme bug: recompile. If it is hand-written, find the instruction the diagnostic names |
| R11 | ions on one loop move in different directions in one step, or a junction degree has no crossing cost (5- and 6-rail junctions cross at the X-junction's cost unless the device prices them) | one direction per loop per step. Price every junction degree the device uses (`junction_cross`), and note that a price the board lacks is listed as added physics |
| R12 | two gates in one trap at once | serialise them |
| R13 | a gate starts in a chain longer than `max_chain` (15) | split the chain first, or cap the zone's capacity |
| R14 | an ion leaves a chain of more than 2 without an accounted `gate_swap` (the check charges every such split) | account the swap, or keep chains at 2 |
| R19 | a node joins more rails than `budget.max_junction_degree` (default 6; a flat surface trap declares 4) | fewer rails at that node: split it into two junctions |
| R20 | two rails at a node are closer than `budget.min_rail_angle_deg` (default 60°) | move the node, or cut the corner. A triangle's 60° corners become two 120° corners, and the shape still reads the same |
| R21 | a rail passes through a node it doesn't end at, or two rails cross away from a shared node | put a junction where they cross, or reroute. A periodic grid cannot be drawn flat, so use `cylinder` |
| R22 | the moving ions of one broadcast cycle don't all make the same motion | split into uniform cycles (the compiler's pass `qccd/compile/uniform.py` does it). Docks drawn as a sketched open path read as different motions, so draw docks as plain rails |
| R23 | an ion ends a cycle on a junction while the traps have room for every ion | move it on to a trap. A conveyor's own slots are exempt |
| R24 | a 2-qubit gate of the circuit is never performed, or one is performed that the circuit doesn't ask for | a compiler or bundle mismatch. Check that the certificate's circuit is the board's circuit (the binding stage) |
| R25 | two operations on one qubit run out of the circuit's order, even commuting ones | keep the circuit's order. This website's compilers never reorder, and the Lean checker's order clause is strict |
| C1-C10 | the C2LR swap-or-stay rules (`references/rules.md`, section C2LR) | follow the device's matching and direction bits. C6's checkerboard pairing is allowed but assumed (`c2lr-demonstrated` refuses it) |
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

## 3. Beyond the checkers: look at these in every review

`rule-check.md` already reports the facts behind items 1-3, 7, 8, 9 and 10 (fit, physics lock,
budget, the shipped device of the same name, cases per rule, R9, buildability). Your job is the
judgement: what each fact means for this design.

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
4. **Its own rule set.** If the entry declares `rules.json`, list what it drops, replaces and
   adds (`conformance`). Judge whether each drop is physically justified. For example, a trap
   built in two layers may drop R20/R21. A drop made only so the design passes is not
   justified. Core rules (R0, R1, R8, R24) cannot be dropped.
5. **Heat margin.** R7 passing by a hair (peak n-bar just under the budget) is worth a sentence.
   Every heat figure is an upper bound (R15), so the real margin is no smaller.
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
- **R16, R17 and R18 pass vacuously** (`qccd/verify/__init__.py`, `verify`). R16 and R17 are
  marked checked whenever the cost model models heating and time. R18 is always marked checked:
  junction cost is charged by degree by construction. They say nothing about a design.
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
