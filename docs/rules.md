# The rules, and what the verifier can honestly say about each

Layer 3 (PLAN §5). Every rule traces to a source; the statements are those in
`Knowledge/notes/constraints.yaml` (`python Knowledge/kg/query.py rules`).

```python
from qccd.verify import verify
from qccd.cost import deck_model
report = verify(prog, arch, deck_model())
report.rules.summary()
# {'passed': ['R1','R11','R12','R13','R14','R15','R18','R2','R3','R4','R4b',
#             'R5','R6','R6b','R7','R8','R9'],
#  'failed': [], 'partial': {'R15': ...}, 'skipped': {'R10': ..., 'R7b': ...}}
```

## The reporting contract

A rule reports **passed** only if the check ran. Otherwise it reports `skipped` with the
reason, or `partial` with what is approximated. A verifier that prints a green tick for a
check it did not run is worse than no verifier.

Every rule the verifier reports as passing on the shipped schedule is also exercised in
`tests/test_rules.py` against a program built to break it. A check that cannot fail is not
a check.

| rule | status here | what it actually checks |
|---|---|---|
| R1 | checked | `occupancy(site) ≤ capacity` after every cycle |
| R2 | checked | degree-≥3 nodes hold ≤ 1 ion, and ≤ 1 ion crosses one in a cycle |
| R3 | checked | ≤ `segment.capacity` participants per segment per cycle |
| R4 | checked | the movement class is declared; ≤ `max_simd_classes_per_cycle` per cycle. R4d (drivability), three frames and a hold clause: along a named loop every ion on a shared channel moves the same signed hop; on the spurs every acting dock moves the same way, and without per-site switches every spur on the channel must act; off any named path the instruction may not ask for more distinct motions (`motion_signature`) than a site has channel SETS -- one channel per electrode role, so the deck's 32 channels are 2 sets of 16 -- counted per class of sites that see the same channels. Which channel drives which site is NOT judged there: no device document partitions its sites across channels, and with a per-site switch the assignment is free (`Codesign/findings/q06i` §7). Two ions moving the SAME way in different places is still not judged either -- that is a layout question. Hold clause: without per-site switches, every LOADED site of a channel that carries a waveform must follow it, on a path or off one; a site no move leaves, enters or passes through is holding, and a hold needs a switch |
| R4b | checked | a cycle has one mode, and never mixes transport with gates |
| R5 | checked | no `(u→v)` together with `(v→u)` on one segment |
| R6 | checked | gate/measure/cool only where the zone type allows, at the **replayed** site |
| R6b | checked | both ions of a pair co-located; `sites`, when present, cross-checked |
| R7 | checked | `n̄ ≤ budget` for both ions **at gate time** (before the gate's own elapsed-time heating) |
| R7b | **skipped** | no architecture declares a per-zone duty-cycle budget yet |
| R7c | checked | gates under a heating model with no cooling scheduled anywhere is a violation; skipped under a model with no heating |
| R8 | checked | ion set invariant; no ion participates twice in a cycle; no position change without a participant |
| R9 | checked | claimed totals, per-batch figures and per-instruction annotations vs the replay |
| R10 | **skipped** | needs symbolic permutation + Pauli-frame tracking against a QASM DAG |
| R11 | checked | unidirectional per loop per cycle; every junction degree is priceable |
| R12 | checked | ≤ 1 gate per trap per cycle |
| R13 | checked | ≤ 15 ions in a trap at gate time |
| R14 | checked | a split from a chain of > 2 needs an accounted `gate_swap`; intra-chain order is not tracked (`init` places a set), so the check is conservative: it charges every such split, edge ion or not |
| R15 | **partial** | quanta composed additively, which R15 says is an *upper bound* |
| R16 | checked when the model models heating | gate error evaluated from the carried `n̄`, not a constant |
| R17 | checked when the model models time | anomalous heating accrued per elapsed µs |
| R18 | by construction | junction cost charged by the degree the expanded graph reports |
| R19 | checked | no node joins more rails than `budget.max_junction_degree` (default 4: a 2D surface trap has T- and X-junctions and nothing denser; a 3D trap may declare 6) |
| R20 | checked | at every node with two or more rails, any two of them subtend at least `budget.min_rail_angle_deg` (default 60°; a symmetric X-junction is 90°) |
| R21 | checked | rails are planar: a rail passes through no node it does not end at, and two rails cross only at a node they share -- the drawing must be a surface trap |
| R22 | checked | one transport cycle is one waveform: every ion that moves in a `simd` cycle has the same motion signature (class, and hop by hop the same path direction, spur direction or lab-frame axis); a site may opt out, never do something else. Skipped only when `control.model` is `direct` |
| R23 | checked | a junction is crossed, not rested on: no ion ends a cycle on a node where three or more rails meet while the traps off the junctions have room for every ion. Not a violation: a slot of a loop or rail the programme moves as a conveyor, or a device with no such room |

**R19-R21, the geometry rules (2026-09).** These three read the document alone: node
positions and the segment list, no programme, no technology. They exist because a graph
that is not a surface trap can still be replayed, priced and even verified against
R1-R18 -- a Tanner graph laid out as rails, a torus whose wrap wires are drawn as chords
across the die -- and every number such a page reports is a number about a machine that
cannot be built. `geometry_violations(arch)` (`qccd/verify/rules.py`) files one violation
per offending node (R19), per pair of rails (R20), and per rail-through-node or
rail-crossing (R21), all at `instruction -1`; `Machine.violations()` reports them after
every edit, and the browser mirrors them (`engine.js::geometryViolations`, a sibling of
`architectureViolations` so each family keeps its own enumeration and its own parity
diff). The two limits are document fields under `budget`, with the surface-trap defaults
when absent.
The ring generator used to draw its two corner docks ON the end-cap rail (a spur at 0° to
the rail: R20 and R21 both fire); it now runs a corner spur outward, so every shipped
ring passes. A periodic grid (`grid(..., periodic=True)`) cannot pass R21 on a plane and
is not meant to: `cylinder` is the planar drawing of the same torus.

**R22, one waveform (2026-09).** Broadcast control means one waveform per cycle, and the
general router batched every hop that departed at the same time slot into one cycle
whatever its direction, which R4d only counted against the number of channels. R22 asks
the strict question the collaborator asked -- only the exact same operation may run
simultaneously -- through `motion_signature(view, ion)`, which the compiler's
uniformising pass (`qccd/compile/uniform.py`) shares, so a programme the pass emits is a
programme R22 accepts by construction. Rigid rotation is uniform by construction; a dock
batch is uniform when every rider docks the same way.

**R4d, holding still and counting sets (2026-10).** An author of the WISE/WISER line of
work pointed out two gaps in how R4d read the wiring, and both were real. The
deck's broadcast count `12*2 + 4*2` is not a differential pair: "every DC is wired through
a two-way switch" (deck p.19), so each electrode role has two banks of DACs, one carrying
the motion waveform and one the hold waveform, and a site's switch selects which it
follows. That is WISE's N_set = 2, "stay or swap" (arXiv:2305.12773, Table I; Sec. II A:
"it suffices to use one DAC per waveform"), and arXiv:2403.00756 gets the same selection
from "one digital input per site". So a 32-channel broadcast plane plays ONE motion plus a
hold per cycle. First gap: a HOLD is a waveform too. R4d judged a loaded site that sat out
a cycle only on the path that moved (or beside a dock), so on a switchless ring a docked
ancilla took the rotation's waveform with nothing asking whether it could stay put, and a
loop rider away from a dock took the dock waveform likewise. The hold clause now asks it
everywhere: without a per-site switch, a loaded site on an engaged channel that no move
leaves, enters or passes through is a violation, reported once per site and never again
for a site the path or spur clause already named. Every shipped device declares
`switch_per_site: true` and is unaffected; the switchless ring designs of
`Compiler/build/q06g/*_noswitch` now fail on their compiled programmes (docked ancillas
during rotations, idle riders during dock and undock) and pass on the same programmes with
the switch. The argument that keeps R4d silent ACROSS paths -- whether one waveform moves
two ions alike is a field solve -- is about two moving sites, and it stays. Second gap: a
motion drives every electrode role of its cell, so `k` distinct motions need `k` channel
SETS (`ControlPlane.banks`, channels per site / roles), not `k` channels; the off-path count
compared `k` with all 32. The hold is not counted there, because with a switch a site may
simply disconnect (WISE fn. [46]). What a programme asks of the banks -- motions plus the
hold per cycle, the `ceil(log2 k)` select bits a switched plane loads (WISE Table I), and the
shim demultiplexer's charging time `t_sc = M x t_ec`, t_ec = 3 µs (WISE Sec. V B 3; its
design is 1:128 with N_se/(M - 1) shim DACs, the deck's 1:100 being its rounded figure) --
is a census of the programme, not a rule: `qccd.cost.waveforms.waveform_census` and
`hardware_report(arch, census=...)`.

**R23, junctions are crossed (2026-10).** R2 bounds how many ions a junction holds at an instant; it never said an ion may not stay, and a router that read it as a capacity parked ions there. On a hexagon lattice whose corners were declared as sites, a Steane round stopped an ion on a corner 77 times, once for 17 instructions, with empty traps on every side. R23 judges where an ion RESTS: a node where three or more rails meet (R18, whatever the node was declared as) may be crossed inside one move and may not be where a cycle leaves an ion. Two cases are excluded by name. A slot of a loop the programme rotates is part of a conveyor, which carries every ion through every slot, the dock slots of a ring included; so is a slot of a named open path that the programme moves with a shift class (every hop of the cycle along that one path), like the conveyor rails of a transversal-CNOT station, whose sites each have a rung to a gate trap and so meet three rails. And if the traps off the junctions cannot hold the programme's ions (capacity capped at R13's chain limit, as the compiler counts it), some ion must stand on a crossing and the rule does not apply. The general router leaves junction sites out of its trap graph under exactly this test, so a compiled programme passes by construction; each stay is reported once, at the cycle it begins.

**The same rules between gadgets (2026-10).** A machine built of gadgets (`qccd/gadget`) is still one chip. Every op of a place is a programme on that place's device and is judged by R1-R23. The roads between the places are judged by the same rules: every carry is compiled with its whole bundle onto its road's own device, one class a cycle, and replayed by this verifier. A door's stub is driven by one side at a time. The schedule is timed by these programmes. The machine rule H1 (`road_ok`) and the place check of the same name report it (GADGETS.md §4.2). When a floorplan says that devices share one plane of DACs or one laser, they are one set of channels under these same rules: one waveform at a time across all of them. Rule H2 (`plane_conflicts() = 0`) judges every instant of the composed programme (GADGETS.md §4.3).

**R15 in detail.** The true composition is
`n̄_tot = n̄_hom + n̄_inhom + 2√(n̄_hom·n̄_inhom)·cos θ`, whose interference term can be
negative — a phase-aware schedule can *cancel* excitation. The corpus supplies no secular
phase model for these primitives, so `Charge.then` adds. Every quanta figure the platform
reports is therefore an upper bound, and says so.

**R9 is model-scoped.** The deck's 397 184 / 8 808 are facts about the deck's cost model,
not about the program. Checking them against a replay under a different model would be a
category error, so a program records `meta.metrics_model` and R9 skips with the reason
when it does not match.

**R9's coverage is reported, not assumed.** It checks `total_cost`, `total_steps`,
`total_rotate_hops`, `contacts`, `batches`, `runtime_us`, `total_quanta`, `cooling_us`
and `peak_quanta` at the program level; `cost`, `steps` and `contacts` per batch; and
`cost`, `steps`, `t0`, `t1` per instruction. The replay computes no per-instruction
`quanta_delta` or `operating_point`, so when a program carries those annotations R9 lists
them under `not_checked` and reports itself as **partial**. Replaying with
`keep_cycles=False` leaves no per-instruction trace to check against, which is likewise
reported as partial rather than passing — a memory knob must not double as a correctness
knob.

**`only_rules` is a request, not a certificate.** Naming a rule that has no implementation
puts it in `skipped`, never in `passed`.

## The one place the deck's own rule needed disambiguating

The artifact states:

> `rotation_cost_rule`: one rigid one-slot rotation moves every occupied data ion; corner
> edges cost 3 primitive hops, straight edges cost 1
> `rotation_step_rule`: one rigid hop takes the maximum primitive edge depth used by any
> moving ion in that hop

Three readings give a defensible 148 per hop on the 2×72 ring. Only one gives 148 **and**
depth 3:

| reading | cost | depth |
|---|---|---|
| corner **segment** (both endpoints are loop corners) costs 3: `142×1 + 2×3` | **148** ✓ | **3** ✓ |
| each of the 4 corner **slots** costs 3: `140×1 + 4×3` | 152 ✗ | 3 |
| each of the 4 corner-**entering** segments costs +1: `140×1 + 4×2` | **148** ✓ | 2 ✗ |

So the cost is charged to the segment that contains a whole turn — in a height-2 ring, the
two end-caps. `Device.corner_endpoints[seg] == 2` identifies exactly those, and both 148
and 3 are computed from the expanded graph rather than supplied as constants
(`Knowledge: fd_deck_corner_cost_is_per_segment`).

This matters beyond bookkeeping: PLAN §0.5 then replaces `corner_hops = 3` with `1`, and
because the number was derived rather than hard-coded, that is a one-parameter change to
the cost model and nothing else.

## Cost models

`DeckModel` is not a strawman kept for contrast; it is the **oracle**. M1 exists to prove
the replay engine reproduces a schedule someone else computed, and that is only a proof if
the model is theirs.

| | `DeckModel` | `CorrectedModel` |
|---|---|---|
| corner segment | `corner_hops = 3` | `corner_hops = 1` (R18: a bend is ordinary transport) |
| degree-≥3 node | uniform, +1 hop / +1 step | `junction_cross` at that degree: 100 µs, 3.0 quanta |
| split / merge | not modelled | 80 µs, 6 quanta each, on the classes that `entails` them |
| heating | not modelled | per-ion `n̄`, by component |
| wall clock | not modelled | microseconds, from the curves |

**Shuttle speed and junction time as variables (2026-09).** The reviewers asked for the
transport speed along a rail and the time to cross a junction to be variables rather than
fixed per-hop numbers. The junction time already is one: the `junction_cross` curve point
at the node's degree, declared per document. The shuttle time can now be derived from a
speed: `corrected_model(shuttle_speed_m_s=..., pitch_um=...)` charges a rail hop
`length_um / speed` microseconds, where `length_um = Segment.length * pitch_um` and
`pitch_um` is what one lattice unit measures on the chip (the technology's
`nm_per_unit`, 225 um on the ETH preset). Off by default, so every shipped number is
unchanged until a study opts in; heating stays per hop.

**Junction charging convention.** A junction is charged **on entry** to a degree-≥3 node,
once per move. Charging both entry and exit would double-count every transit; charging
only exit would miss the last one. On entry, an ion completing one revolution of the
shipped ring crosses each of the 24 docks exactly once — which is why "445 transits per
data ion" comes out of the replay rather than out of a formula.

The junction crossing **is** that segment's transport, not something added after it: the
ion does not shuttle for 5 µs and then spend another 100 µs at the junction, it spends
100 µs crossing. So durations overlap (`max`) while heating adds.

## Cooling insertion

`qccd/compile/cooling.py`. Cooling is global — Doppler sheet beams cover the whole trap,
so one operation cools every ion. It costs schedule time but does not serialize per ion.

The R7 trigger **provably converges in one pass**: a global cool zeroes every ion, so
inserting one strictly lowers `n̄` everywhere downstream; a gate that satisfied R7 before
still does, and a gate that violated it now starts from zero. The pass asserts that on a
re-replay rather than assuming it.

`CoolingPolicy.max_gate_quanta` overrides the architecture's budget and reaches R7
itself (via the replay's `rule_config`), so a budget sensitivity sweep actually changes
the schedule rather than only the report.

An optional ion-loss trigger caps `n̄` between cools regardless of when the next gate is
(an ion survives only ~85 uncooled junction round trips before it is *lost*,
arXiv:1210.3655). Two details it has to get right:

* **Blame the instruction that crosses the cap, not the next one.** An accumulation peaks
  at the *end* of the instruction that pushes it over, and a cool inserted after that
  instruction cannot undo the excursion. The pass probes before every instruction and
  attributes an over-cap reading to its predecessor.
* **An instruction that jumps the cap on its own is unfixable.** When a cool already sits
  immediately before it and the cap is still exceeded when it finishes, no cooling
  schedule satisfies the cap there; the pass names the instruction and stops, rather than
  padding the program with cools that cannot help or looping to its iteration limit.

`peak_quanta_between_cools` is the replay's running maximum of the live `n̄`, which a cool
resets to zero — so it covers the stretch before the first cool and the tail after the
last one. Sampling only at the cools would report a reassuring `0.0` for a program with no
cooling at all, which is precisely the case the diagnostic exists to flag.


## Where these came from

The reporting contract above is not aspirational. An adversarial review of M0–M2 (six
dimension reviewers, each finding independently verified by a refuter) raised 44 claims,
of which 30 survived refutation and ~24 were distinct defects. Most were holes in checks
that were being *reported as passing*: R6/R6b/R13 driven by an optional `sites`
annotation rather than by the replayed positions, R8 false-firing on a multi-segment
`via`, `only_rules` licensing rules with no implementation, R9 silently skipping its
per-instruction granularity under `keep_cycles=False`, R7c sitting in no bucket at all,
and the cooling pass's R7-budget knob being inert. None of them moved an M0/M1/M2
acceptance number; all of them are now regression-tested in
`tests/test_review_regressions.py`, named for the defect rather than for the fix.
