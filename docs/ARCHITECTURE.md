# Fault-tolerant architecture: floorplans, gadget rules, parallel execution, Shor

**Goal.** Compile the whole execution of a fault-tolerant Shor's algorithm onto a QCCD
machine built from verified gadgets, on a floorplan the user designs, with every gadget
working in parallel wherever nothing conflicts, and with every rule the composition obeys
written down as an assertion a reader can check.

This document is the specification. It extends the verified place library and the Logical
Algorithm town (`docs/GADGETS.md` §9–§10); the device-level rule language it reuses is
`docs/rule-language.md`. Started 2026-10-20 on the `compiler` branch.

---

## 1 · The four layers, and what is checked at each

| layer | what it is | what is checked | how |
|---|---|---|---|
| **place** | one verified gadget: a device, ports, ops (`leaves/places.py`) | each op as hardware (the device rules) and as logic (flows, fault distance) | once per master |
| **gate** | a composite gate written in the algorithm language from places (`gate t q: …`) | its body implements its contract, in every branch | once per definition (flat sign-off) |
| **floorplan** | where the places stand and how roads, junctions and wires join them | the floorplan rules (§3): footprints, roads, doors, regions, wiring | once per floorplan |
| **execution** | a program scheduled on a floorplan: events, carries, messages | the execution rules (§4): interface, capacity, ownership, non-interference, causality | once per program |

The claim for a whole program is compositional: if every place op is verified, every gate
definition implements its contract, the floorplan and the execution obey their rules, and
every gate call in the program is its definition's instruction sequence (renamed), then the
program implements its logical circuit gate for gate. The logical circuit itself is then
checked against the algorithm's specification at the logical level (§6), where it is small.
The flat sign-off (`logic/flat.py`) stays as the end-to-end check for programs whose
branches can be enumerated, and as the check for gate definitions.

---

## 2 · The floorplan language

A floorplan is a text file (`*.floor`). Every place of the machine is placed by hand, in
model units, and every road is drawn: nothing about the layout is fixed by the tool.

```ebnf
floorplan = "floorplan", name, [title], NL, { line } ;
line      = "code", name
          | "region", kind, name, [title]                    (* following places belong to it *)
          | "place", name, master, "at", num, num            (* master or category word *)
          | "junction", name, "at", num, num
          | "road", end, "--", end, [ "via", point, { ",", point } ], [ "as", name ]
          | "wire", end, "->", end, [ "latency", num ]
          | "wires", "auto"                                  (* every measuring place to the decoder, … *)
          | "param", name, "=", value ;
end       = name, ".", port          (* a place's door, or a junction's arm n/e/s/w *)
          | name ;                    (* a junction: the arm is chosen by direction *)
kind      = "memory" | "compute" | "factory" | "supply" | "control" | "traffic" ;
```

* A **region** groups places by what they are for. `memory` holds logical zones; `compute`
  holds the places that act on blocks (preparation, syndrome extraction, transversal gates,
  lattice surgery, readout); `factory` holds magic-state factories and injection places;
  `supply` holds ion depots; `control` holds the decoder and the classical memory;
  `traffic` names a set of junctions (a bus). The region a place sits in is a statement the
  floorplan rules check (F9).
* A **road** is a conveyor channel. Its length in sites is its Manhattan route length less
  one, exactly as for every composite (`designs.Designer`). A road may end on a place's
  door or on a junction arm.
* A **junction** is a `junc9`: four arms, it passes one bundle at a time.
* A **wire** carries bits, not ions; its one cost is its latency.

`town.floor` is the Logical Algorithm town of GADGETS.md §9 written in this language;
`qccd.gadget.floorplan.load()` turns any floorplan into the same `Library` composite the
town builder made.

---

## 3 · The floorplan rules

Each rule is an assertion in the rule language (`docs/rule-language.md`) over the words of
§5, evaluated on the floorplan alone (no program).

| id | rule |
|---|---|
| F1 | every road joins two ion ports that accept each other (kind, role, width, code, direction) |
| F2 | every ion door of a place is on exactly one road; a junction arm is on at most one |
| F3 | footprints do not overlap |
| F4 | a road passes through no place it does not end at, and two roads cross only at a junction |
| F5 | a road a bundle may wait on holds a bundle: every road ending at a door is at least as long as the door is wide |
| F6 | every out door reaches every in door it may have to serve (places are endpoints, junctions are the only thoroughfares) |
| F7 | every place that measures is wired to a decoder, every place that reports an outcome to a classical memory, and every place that can be told to run is wired from one |
| F8 | the decoder's frame reaches the classical memory |
| F9 | every place stands in a region of its kind |

---

## 4 · The execution rules

The G-rules of GADGETS.md §8–§10 (G1–G11) become assertions, and three more state what
parallel execution must respect. An execution is the GIR of a scheduled program.

| id | family | rule |
|---|---|---|
| G1 | interface | ports on a road agree; every bundle arrives in the order its place takes it in |
| G2 | ownership | every ion leaves only from where it is; residents end at home; loaded ions end dumped or in a living block |
| G3 | capacity | no place ever holds more ions than its capacity |
| G4 | capacity | no road ever holds more ions than its sites, nor carries two ways at once |
| G5 | exclusivity | a place runs one op at a time |
| G6 | physics | no ion gets hotter than the ion-loss limit |
| G7 | composition | every instruction is realised, and each block's instructions run in program order |
| G8 | physics | no block goes longer than its coherence budget without syndrome extraction |
| G9 | contract | every op used is verified (a classical op is modeled) |
| G10 | causality | every classical bit is used after it exists |
| G11 | classical | every syndrome is decoded, the decoder keeps up, nothing reads an outcome before it is decoded |
| G12 | ownership | every bundle that travels is taken in at the far end, by exactly one op: no ion is left on a road |
| G13 | non-interference | a road carries one bundle at a time, from its first ion in to its last taken off |
| G14 | traffic | a road holds the ions on it: at no moment are more of a bundle's ions on a road (travelling, or stopped until taken in) than it has sites -- so a whole bundle stops only in front of a door |

G5 and G13 are the rules that make parallelism safe: a place runs one op at a time and a road
carries one bundle at a time, so a magic-state factory producing while a lattice surgery runs
elsewhere is legal exactly when no place, junction or road is asked to do two things at
once; G2 follows every ion, so no ion is ever in two of them. G1–G11, written as text, give
the place model's own checks' verdicts on every example, sequential and parallel, and on
every mutation (`tests/test_gadget_architecture.py`); G12 catches a bundle nobody takes in,
which those checks never looked at.

---

## 5 · The vocabulary (machine level)

The rule language is the device-level one; the machine level adds an execution kind,
`machine`, built from a floorplan and (optionally) a GIR. Its words:

| word | meaning |
|---|---|
| `places`, `junctions`, `roads`, `wires`, `doors`, `regions` | the floorplan's parts |
| `family(p)`, `region(p)`, `master(p)`, `capacity(x)` | what a place is, where it stands, how many ions a place or road holds |
| `length(r)`, `ends(r)`, `door_of(r)`, `width(d)`, `dir(d)`, `accepts(d1, d2)` | roads and doors |
| `overlaps(p, q)`, `passes_through(r, p)`, `crosses(r1, r2)`, `reachable(d1, d2)`, `wired(p, s)` | geometry and connectivity |
| `blocks`, `instructions`, `events`, `carries`, `messages`, `windows` | the execution |
| `place(e)`, `road(c)`, `instruction(x)`, `ions(x)`, `start(x)`, `end(x)`, `overlap(x, y)` | where and when |
| `occupancy(x, t)`, `holder(b, t)`, `owner(i, t)` | state at an instant |
| `resources(x)` | the places, junctions, roads and ions an activity holds while it runs |
| `traffic()`, `ion_sites(c)`, `wait(c)` | how much shuttling an execution does |

---

## 6 · Composite gates, and the logical check

The algorithm language (`algorithm.py`) gains **gate definitions**:

```
gate t q:                 # T on q, by teleportation
  inject m
  zz q m -> a
  read m X -> b
  decode
  if a: s q
  if b: z q
```

A definition's body is ordinary instructions over its parameters and local blocks; its
outcomes are local. A call `t q3` expands to the body with fresh names (`t3_m`, `t3_a`).
Guards nest: a guarded call guards every instruction of the body that can be guarded, and a
condition is then a conjunction of parities (`if m & a: s q`); what cannot be guarded
(`prep`, `inject`, `se`, `store`, `decode`) runs either way, which only creates, rests,
checks or decodes. `include "standard"` brings the standard gates
(`qccd/gadget/gates/standard.alg`): `sdg`, `sxdg`, `h` (S̄·√X̄·S̄), `t`, `tdg`, `cz`, `ccx`
(Toffoli, seven T), `cswap`. The π/4 rotations `s` and `sx` are instructions, not gates:
each consumes a |Ȳ⟩ block and conjugates the block's Pauli frame (X̄ → Ȳ for S̄, Z̄ → Ȳ for
√X̄), which only the scheduler can do.

**Contracts** (`qccd/gadget/logic/contract.py`). Every primitive has a logical meaning the
place library verifies on hardware -- `inject` is the factory's own output, T†|+⟩; `s` and
`sx` are S and √X (each checked here as its composite: |Ȳ⟩, surgery, readout, and the
correction the scheduler writes); `zz`/`xx`/`read` are measurements. A gate's contract is
checked exactly: its parameters Bell-paired with references, every branch of every
measurement it makes, every guard evaluated on that branch; each reachable branch must leave
(U ⊗ 1)|Φ⟩ up to a phase, and nothing else alive. A call inside a body counts as its own
proved contract, so a Toffoli of seven T gates is one branch, not 4⁷.

**What the contracts found.** The T gadget as first shipped (`t_gate.alg`: Z̄Z̄ against the
magic block, X̄ readout, S̄ if the surgery outcome is 1) assumed the magic block holds T|+⟩.
The factory is verified to make T†|+⟩. With the real state the program is T† in two
branches and S̄T in the other two: never T. The flat sign-off could not see it -- it treats
an injected state as a free input and checks the Clifford skeleton -- and the test of the
correction table started from T|+⟩. The fix is free: X̄ on the magic block, a line in the
archive, turns T†|+⟩ into T|+⟩ (`t_gate.alg` now does it, and so does `t`); `tdg` uses the
state as it is with an S̄† correction. Both are proved in all four branches, and a test keeps
the old program failing.

The logical check of a whole program simulates it with every call as its contract, exactly,
branch by branch over its own top-level outcomes (`program_outcomes`).

---

## 7 · Parallel execution

`city.py` scheduled one instruction after another, and one road trip after another. The
parallel scheduler keeps program order as the order instructions are *issued*, and places
each as early as its dependencies and its resources allow:

* every resource -- place, junction, road, zone -- has a calendar of leases; a lease may
  be open-ended (a block resting in a zone until its next use is issued) and is closed
  when that use is scheduled;
* a trip waits only where a bundle fits (its door roads); between them it runs as a green
  wave, reserved hop by hop, and the whole wave moves later if any hop is leased;
* a magic state is produced while the computation runs: `inject` has no dependency on any
  block, so the factory starts as soon as it is free and the state rests in a zone until
  it is consumed;
* the classical half is unchanged: a decode works alongside the ions, and a `decode` is one
  call, made once every window it names exists;
* a place is chosen by when the op could start there -- the place free, the blocks arrived
  -- not by distance alone, so a second bridge or factory takes work while the first is busy;
* a block resting in a memory leaves it just in time: no earlier than the place it goes to
  can take it, less the trip, because a block waiting on a road is corrected by nobody.

**Memory that corrects** (`mem_d3`, family `memory`). A zone holds a block and touches
nothing, so a block may rest there only for its coherence budget (G8). A memory is the
clinic's device with the block kept on its ring: `store` (in, one syndrome round),
`refresh` (one round at rest), `release` (out), each verified like every place (flows 18/18,
distance ≥ 3 for the rounds). **Refresh** (`qccd/gadget/refresh.py`) schedules, finds every
stretch a block would spend without syndrome extraction past 80% of its budget, inserts
`se BLOCK 1` rounds (each with the earliest time it may run, issued just before the block's
next use), and schedules again; a round on a block resting in a memory runs in place, inside
the block's own lease, and its syndrome is decoded at once. A round changes no logical state.

The **traffic** of an execution -- ion-sites shuttled, junction passes, time bundles wait on
roads -- is reported with every schedule, and is what a floorplan is designed to reduce.

**Designing for traffic** (`qccd/gadget/optimize.py`, `python -m qccd.gadget optimize`). A
seeded hill climb over the floorplans the town builder makes (which places stand in which row,
in what order), every candidate *scheduled* -- its traffic and makespan are the schedule's own,
and a candidate that refuses an instruction or breaks a machine rule is not one. The best is
written as an ordinary floorplan. From `shor.floor`, 150 candidates (160 s) found
`shor_traffic.floor`: Shor moves 199,580 ion-sites there against 313,727 (36% fewer, refresh
rounds included), every rule holding and the compositional sign-off unchanged; the makespan
hardly moves (11.16 → 11.04 s), because Shor's critical path is lattice surgery, not travel.
(Re-measured 2026-10-20 under split/merge charged for what a move does; the search was run
under the earlier pricing, and its floorplan is still the better one.)

---

## 8 · Shor

`shor15.alg`: order finding for N = 15, a = 7, with one control block recycled three times
(Kitaev's iterative phase estimation, the form Monz et al. ran on trapped ions, Science
351:1068, 2016), the work register in four blocks, the semiclassical inverse QFT as
classically controlled S̄† and T̄† corrections, and every Toffoli as seven T gates from the
factories. Its expected output: the first bit is 0 and the last two are uniform, so the
phase is s/4 and the order is 4, and gcd(7² ± 1, 15) = 3, 5.

It runs on `examples/gadgets/floorplans/shor.floor`: six memories, three bridges (every T gate
and every π/4 gadget is a surgery: 44 of them), one workshop, two readouts, one preparation
place, two factories beside two pharmacies, two depots, the control floor; three rows between
four streets. The town could not run it: three passive zones neither hold five resting blocks
nor keep them alive for seconds.

| | |
|---|---|
| instructions | 157 written (after `standard` expands) + 32 refresh rounds |
| magic states / π/4 gadgets / surgeries | 15 / 29 / 44 |
| events, carries | 27,559, 3,736 (a junction passes one ion at a time: GADGETS.md §4.1) |
| makespan | 11.11 s |
| traffic | 325,888 ion-sites; 26,386 junction passes, one ion each; longest wait on a road 0.23 s; hottest ion n̄ = 204 (limit 510) |
| machine rules | F1–F9, G1–G14, H1 (every road's own program, GADGETS.md §4.2) and H2 (shared planes, §4.3) hold; nothing refused |
| flat sign-off | does not apply: 11 outcomes a guard reads are corrected by frames that are products of bits |
| compositional sign-off | passed: places (G9), gadgets (S̄, √X̄), gates (sdg, h, t, tdg, ccx, cswap), composition (23 rules), semantics m1 = 0, (m2, m3) uniform |

**The compositional sign-off** (`qccd/gadget/logic/compose.py`) is the claim for programs
too large to flatten, in five parts, each a check that ran: every op is a verified place op
(G9); the π/4 gadgets are their rotations in every branch; every gate called is its contract
in every branch; the floorplan and the schedule obey every machine rule; and the program,
calls as contracts, has exactly the outcome distribution reported. What it does not
establish: that the classical memory evaluates frames that are products of bits correctly at
run time (it is a classical program the logical check models as the Paulis it applies), and
logical error rates (distance is counted, noise is not sampled).

```bash
python -m qccd.gadget algorithm examples/gadgets/algorithms/shor15.alg   # its floorplan is named in it
```

---

## 9 · Phases

| | deliverable | state |
|---|---|---|
| A1 | machine vocabulary; G1–G11 as rules, equal to `place_checks.py` on every example and mutation | ✅ `qccd/ruleset/machine.py`, `library/machine.rules`; 0.05 s per example |
| A2 | floorplan language, `town.floor`, F1–F9 | ✅ `qccd/gadget/floorplan.py`; the town round-trips exactly; seven broken floorplans each fail their rule |
| A3 | parallel scheduler, G12–G14, traffic | ✅ `city.py` `parallel=True`; every example passes F1–F9 and G1–G14 and signs off; `magic_teleport` 817 → 508 ms (2026-10-20 pricing) |
| A4 | gate definitions, nested guards, `sx`/`h`/`t`/`tdg`/`ccx`/`cswap`, contracts | ✅ `algorithm.py`, `gates/standard.alg`, `logic/contract.py`; every gate proved; the shipped T gadget was wrong, fixed |
| A5 | `shor15.alg` on a Shor floorplan, compositional sign-off, logical check | ✅ `shor.floor`, `mem_d3`, `refresh.py`, `logic/compose.py`; 23 rules hold, compositional sign-off passed |
| A6 | the design page: floorplans, traffic, parallel lanes | ✅ the page shows the 23 rules (each opening to its assertion), the compositional sign-off, a traffic panel and the floorplan's text; the editor exports `.floor`; `optimize` searches floorplans for traffic |
| A7 | the tutorial's gadget parts | drafted: Part J, six Lean lessons (`book/chapters/J1`–`J6`), 47 exercises, each checked natively; publishing is the tutorial maintainer's |
