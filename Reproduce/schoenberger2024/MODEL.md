# MQT IonShuttler (Schoenberger et al.): the model we replay

One artifact, two papers, two tools:

- **P1**: D. Schoenberger, S. Hillmich, M. Brandl, R. Wille, "Using Boolean Satisfiability for Exact
  Shuttling in Trapped-Ion Quantum Computers", ASP-DAC 2024, arXiv 2311.03454 (v1). An exact SAT
  formulation that finds the minimum number of time steps (`SAT.py`, `run.py`).
- **P2**: the same authors, "Shuttling for Scalable Trapped-Ion Quantum Computers", IEEE TCAD 44(6),
  arXiv 2402.14065 (v2). A cycle-based heuristic (`run_heuristic.py`, `scheduling.py`, `Cycles.py`).
- **Artifact**: github.com/munich-quantum-toolkit/ionshuttler (formerly cda-tum/mqt-ion-shuttler), MIT,
  commit `bc71e46` (2024-11-06). This is the earliest commit that has the exact solver, and it pins the Z3
  version P1 states. It was run with Python 3.11.9 and z3-solver 4.12.1.0.

We ran both tools ourselves. The patches in `artifact/patches/` change no constraint, constant or loop.
They do three things: add a `--seed` argument (switching on the code's own random placement), write out
the schedule the tool computed, and, for the diagnostic runs only, pass the code's own "without Gate
Selection" switch. Every schedule they produced is in `artifact/` and is replayed in `qccd.repro.timed`.

The code is in `qccd/repro/ionshuttler.py` and the tests are in `tests/test_repro_ionshuttler.py`.
`python -m qccd.repro.ionshuttler Reproduce/schoenberger2024` regenerates `results.json` and
`ours/` from `artifact/` and `circuits/`.

Sources are cited by section, table, equation or figure of the arXiv versions above, and by file and
line of the code at `bc71e46`. The labels are:

- **Q**: quoted from a paper.
- **C**: read from the code.
- **I**: inferred.
- **A**: an assumption of ours.

## 1. Device

| item | value | where | how it is written down |
|---|---|---|---|
| abstraction | a graph whose **edges are ion-chain sites** and whose nodes are junctions ("major nodes") or the separators between neighbouring sites of one linear region ("minor nodes") | P1 Def. 1, "The set of nodes V contains two different types: major nodes representing junctions and minor nodes separating adjacent traps" (Q); P2 Sec. V.A (Q) | every tool edge is a `PortGraph` site. Every tool node is a `PortGraph` node with `{"node_type", "junction", "pz"}`. A segment `"<edge>@<node>"` joins each edge to each of its two endpoint nodes |
| layouts | `L(m, n, v, h)`: an m×n grid of junctions with v sites on every vertical wire and h on every horizontal one; \|E_M\| = m(n−1)h + n(m−1)v | P1 Sec. V, P2 Sec. VII.B (Q) | the tools' own graphs, read from the dump (`graph.nodes`, `graph.edges`, in the tools' own edge order) |
| what counts as a junction | SAT: every grid node `(i·v, j·h)`, including the degree-2 corners of a racetrack. Heuristic: every grid node plus the exit, entry and connection nodes of the PZ paths, and the PZ node | `SAT.py` `create_graph` (C); `Cycles.py` l.56-63 and `find_nonfree_and_free_circle_idxs` l.302 (C) | `nodes[n]["junction"]` |
| SAT processing zone | a two-site bypass between the two bottom corners. `e_out` runs from the bottom-right corner (the exit junction) to the PZ node, capacity 1. `e_in` runs from the PZ node to the bottom-left corner (the entry junction), capacity 2, and is **the gate site** | P1 Sec. IV.B: "in the case of the inbound edge e_in, this is relaxed to at most two ion chains" (Q); `SAT.py` `create_graph` l.47-54, l.524 (C) | site roles `exit` and `gate` |
| heuristic processing zone | an exit path of `n // 2` edges from the bottom-right corner to the PZ node. A dead-end **parking edge** at the PZ node (the gate site) holds 3 chains. An entry path of `n // 2` edges runs back to the bottom-left corner; its first edge is a `first_entry_connection` that shortest paths avoid | `graph_utils.py` `GraphCreator` (C); `run_heuristic.py` `max_chains_in_parking=3` (C); P2 Sec. V.C (Q, one-way) | site roles `exit` (with its order along the path), `gate`, and `entry` (with its order) |
| placement | `random.seed(seed); random.sample(range(n_of_traps), k)`; chain i goes on the i-th sampled trap edge. Seeds are 0-9 (SAT) and 0-4 or 0-9 (heuristic). The two tools give the same 12 edges for every seed on 4 4 1 1 | P1 Sec. V, "the initial placement of chains was done randomly" (Q); `scheduling.py` `create_starting_config` (C); the seeds are **A** (neither paper states them) | `TimedSchedule.chains` |
| chains | one ion per chain (chain i = qubit i) | P1 Sec. IV.D (Q); P2 Sec. VII.A (Q) | ions `c0`, `c1`, … |

The configurations replayed are in §4.

## 2. Time and the representation

Both papers count **abstract time steps**. Neither has a microsecond, a fidelity or heating. The
unit of every number here is one time step. The core checker's `makespan_us` field therefore holds
steps.

**One step is one unit.** For step `k` of a tool, the unit is `[k + offset, k + offset + 1)`.

**A move.** A chain goes from edge A through nodes n1…nk, and through the edges C1…C(k−1) between them,
to edge B. In one unit `[a, a+1)` it is written as:

| event | interval | duration law |
|---|---|---|
| `split` off A onto `A@n1` | `[a, a+1/4)` | 1/4 |
| for each node: a `move` across it, and through each intermediate edge a `merge` then a `split` | `[a+1/4, a+3/4)`, divided evenly over the 3k−2 events | (1/2)/(3k−2) each |
| `merge` into B from `B@nk` | `[a+3/4, a+1)` | 1/4 |

A split releases A when it ends, and a merge takes B when it starts. A chain may therefore move into an
edge that another chain leaves in the same step. Both tools need this:

- the heuristic's cycle rotations: "every chain on that cycle" moves one edge forward (P2 Sec. V.B, Q;
  `Cycles.py` `rotate`, C);
- the SAT optima's trains: Eq. (1) requires only the edges passed to be empty, not the target.

Every node a move passes is held inside its step. The same representation is used for TrapSIMD's
lockstep shift (`trapsimd.py`).

**Gates.** A gate is a `gate1` (one chain) or a `gate` (two chains) at the gate site. It is an
**instant** at the end of its step's unit, where the chains stand in that step's positions. Its
duration in the law is 0.

- **P1** has no gate duration: "if s^t_j is true, the respective ion chains have to be in the inbound
  edge e_in at t" (Sec. IV.D, Q).
- **P2's code** counts a gate in an iteration when its chains are on the parking edge after that
  iteration's moves (`scheduling.py` l.357, C). It completes when the count reaches `time_1qubit_gate = 1`
  or `time_2qubit_gate = 3` (l.376; `run_heuristic.py`, C). Each counted iteration is one instant, and the
  rule `gates` checks the count.

**The clocks.** The two tools count differently. The replay keeps each count, so that the replayed
makespan (the last event's end) is the printed number:

| tool | what it prints | our clock | makespan |
|---|---|---|---|
| SAT | `Found satisfying solution with N time steps`. N is the number of **states** t = 0…N−1, because `run.py` l.55 loops `for timesteps in range(2, …)` and the solver has one state per value of t. That is N−1 transitions (C). P1 writes `0 ≤ t ≤ T` (Sec. IV.A, Q), so its T would be one less | state t is the unit `[t, t+1)`. The transition from state t−1 fills it, and state t's element executes at t+1. State 0 has no incoming transition, so `[0, 1)` is empty | N |
| heuristic | `Circuit successfully executed in N time steps`. N is the loop counter, the **0-based index of the last iteration** (`scheduling.py` l.379, C), so there are N+1 iterations | iteration t is the unit `[t−1, t)`, and its gate step is the instant t. Iteration 0 runs in `[−1, 0)` | N, while the schedule lasts N+1 units |

The two printed numbers are therefore not in the same units. On the same ten placements of 4 4 1 1 with
12 chains:

- the SAT's 18.5 states are 17.5 transitions;
- the heuristic's 21.6 is 22.6 iterations.

The processing zones also differ (§1). P2's `T^min` (20.2) is not P1's model either, so the two tools are
not compared with each other.

## 3. Rules

Each rule is either checked by the core checker (`check(..., strict=True)`, under `IONSHUTTLER_SAT` or
`IONSHUTTLER_HEURISTIC`) or by `ionshuttler_rules`.

| rule | statement | where | checked by |
|---|---|---|---|
| one chain per edge | at most 1 chain per edge; SAT's `e_in` holds 2 and the heuristic's parking edge 3 | P1 Sec. IV.B: "each site can only hold zero or one ion chain in any given time step (in the case of the inbound edge e_in, this is relaxed to at most two ion chains…)" (Q); `SAT.py` l.524 "# MV_CONSTR_3: can't move to occupied trap (changed: exclude processing zone -> 2 register are allowed)" (C); `scheduling.py` `check_duplicates` "More than {max_number_parking} chains in parking edge" (C) | core `capacity` |
| a move is one step | each move fills one unit, a chain moves at most once per step, and gates are instants at the end of a step | the representation (§2) | ours, `layout` |
| durations | the law of §2 | | core `durations`, strict: an unpriced event fails |
| at most one junction per move | | P1 Sec. IV.C: "This approach allows every chain to move past one junction, given the path leading to this junction is not blocked" (Q); P2 Sec. VI: "Within one time step, the chains are allowed to move through one junction, if the path is not blocked by another chain" (Q) | ours, `junctions` |
| the path is empty | the edges a move passes, between its start and its end, are empty at the **previous** step. The target need not be | P1 Eq. (1): "each ion chain is allowed to stay at its current edge e or move to one of the edges e\* given by N\*(e), if the path P(e, e\*) in between e and e\* is not occupied at time step t by any other ion chain" (Q); `SAT.py` l.455 "# also all edges in between have to be empty at this timestep" (C); P2 Sec. VI (Q, above) | ours, `path`. In this representation it is also implied by capacity together with `nodes`: an occupant of a passed edge either stays (capacity) or leaves through a node the mover passes |
| the SAT code's stricter path | `get_path_between_edges` takes a shortest path between one endpoint of each edge. Round a lattice face it can name the two far sides of the face, which the chain never touches | `SAT.py` l.223-226, its own comment: "could take wrong path if graph is quadratic" (C); RUN.md, quirk 4 | **reported, not checked**. P1's rule is the one checked. See §5 for why the dump's record of it is not authoritative |
| one chain per node per step | at most one chain passes any node (junction or minor) in a step. In the heuristic, the PZ node is exempt | P1 Sec. IV.C: "we limit the number of ion chains that are allowed to pass over a node to 1. This includes every node in the layout, meaning all junctions and all minor nodes within two junctions" (Q); `SAT.py` l.507 "only one ion can move through node (not only JUNCTION nodes) per timestep" (C). P2 Sec. V.A: "While one ion chain is shuttled through a junction, all other junctions may shuttle other ions at the same time" (Q). The exemption is `Cycles.py` l.374: "# new: exclude processing zone node -> if pz node in circles -> can both be executed" (C) | ours, `nodes`, per step. SAT also core `junction_mutex` and `segment_mutex`, per instant. The heuristic's profile turns both mutexes off (§6.1) |
| no overtaking | chains do not pass each other inside a region | P1 Sec. IV.C (Q); P2 Sec. V.B (Q) | implied by `nodes` (a head-on swap passes one node twice) and by capacity |
| the PZ is one-way, SAT | (a) a chain on `e_out` is on `e_in` at the next step; (b) a chain on `e_in` came from `e_out` or stayed; (c) a chain leaves `e_in` only to an edge at the entry junction | P1 Sec. IV.C: "ion chains that use the outbound edge e_out have to move to the connected processing zone edge e_in and back into the memory zone graph to a neighbor edge of the inbound edge n ∈ N(e_in)", and its three constraints (Q); `SAT.py` l.541, l.561, l.577 (C) | ours, `pz` |
| | the code adds: a chain enters `e_out` from an edge at the exit junction | `SAT.py` l.541 "# if in exit -> was in connected edge in graph at t-1 and has to move to entry at t+1" (C) | ours, `pz` |
| SAT: the PZ is empty at the start and at the end | | start: P1 Sec. IV.A, "including the inbound and outbound edge interfacing the processing zone (which are always considered to be empty at the beginning)" (Q). End: `SAT.py` l.596 "# can't be in exit or entry in the last time step" (C, code only) | ours, `pz` |
| the PZ is one-way, heuristic | chains move along the exit path, onto the parking edge, along the entry path and back into memory, never backwards. The code's "rare edge case" also lets a chain go from the end of the exit path straight onto the entry path. The heuristic may end with chains parked | P2 Sec. V.C: "we consider the processing zone to be a one-way pass that is separated from the grid-type memory zone" (Q); `graph_utils.py` `GraphCreator` (C); `scheduling.py` l.284 "-> should be rare edge case -> chain moves from exit to entry" (C) | ours, `pz` |
| placement on memory edges | | `scheduling.py` l.30: only `edge_type == "trap"` edges are drawn (C); P1 (above) | ours, `pz` |
| gates at the gate site | gates run only on `e_in` (SAT) or the parking edge (heuristic), with all their chains there | P1 Sec. IV.D (Q, above); `scheduling.py` l.357 (C) | core `positions` (the chains are where the gate says) plus ours, `gates` (the place is the gate site) |
| one gate at a time | | SAT: P1 Sec. IV.D, "the next sequence element j+1 has to be true at a later time step t' > t" (Q), and `SAT.py` l.710 "# only one sequence ion in entry per timestep" (C). Heuristic: `update_sequence_and_process_gate` processes only `seq[0]` (C) | ours, `gates` |
| a heuristic gate's time | counted in exactly `time_1qubit_gate = 1` or `time_2qubit_gate = 3` consecutive iterations, completing on the last | `scheduling.py` l.376 "if time_in_pz_counter == time_gate" (C); `run_heuristic.py` defaults (C); P2 Sec. VI: "This can take up to several time steps" (Q) | ours, `gates` |
| SAT: the sequence in its fixed order | each element executes exactly once, in the sequence's order, at strictly increasing steps. Full register access (FRA) is "an ascending list of all ion chain indices" | P1 Sec. IV.D (Q, above); "every sequence element s^t_j is only true once" (Q); P1 Sec. V (Q, FRA) | ours, `sequence` |
| heuristic: free order, each gate once | every gate of the circuit runs exactly once. The order is the heuristic's choice (Gate Selection, P2 Sec. IV); FRA's `h` gates all commute. Without Gate Selection the code walks qubit tuples and never names the operation, so those runs are matched on their chains alone | P2 Sec. IV, VI (Q); `scheduling.py` (C) | ours, `sequence` (the core `circuit` check for two-qubit pairs is skipped: FRA has none) |
| SAT code: e_in during the sequence | a one-chain element executes **alone** on `e_in`, and no chain outside two consecutive elements is on `e_in` between them | `SAT.py` l.647-651 (`Not(states[t][entry][other_ion]) … if other_ion != tpl`) and l.678 "no other ions in PZ in time between the two gates" (C, code only: P1 relaxes `e_in` to two chains so that two-chain elements can execute) | ours, `sequence_code` |

**Checks that do not apply.**

- `trap_serial`: gates are instants and transport takes no exclusive trap.
- `chain_order`: one chain per edge.
- `declared_locks`: the tools state no locks.
- `broadcast`: transport is not broadcast.
- `circuit`: FRA has no two-qubit gate.

## 4. Results

Every one of the **75** dumped schedules passes every configured check. Its replayed makespan equals
the number the tool printed, which is read from the console line in `artifact/runs.json.gz` and agrees
with the dump. Each replay also ends with every chain where the dump says its last step leaves it. The 75 are 40 SAT and 15 heuristic paper configurations, plus 20 labelled diagnostics.

The paper's means are means over runs. "Ours" is the mean of the replayed makespans over the seeds the
paper averages: P1 averages 10 runs, P2 five.

| configuration | tool | per seed, printed = replayed | ours | paper | where | verdict |
|---|---|---|---|---|---|---|
| Lattice 4 4 1 1, 12/24, FRA | SAT | 18 18 20 19 19 18 18 18 19 18 | 18.5 | 18.5 | P1 Table I, Lattice, T^ | **exact** |
| Lattice 3 3 1 1, 6/12, FRA | SAT | 11 11 12 11 11 10 11 11 11 10 | 10.9 | 10.9 | P1 Table I, Lattice, T^ | **exact** |
| Lattice 4 4 1 1, 6/24, FRA | SAT | 12 12 14 13 13 12 12 12 13 12 | 12.5 | 12.5 | P1 Table I, Lattice, T^ | **exact** |
| Racetrack 2 2 1 5, 6/12, FRA | SAT | 13 14 14 14 12 14 14 13 13 11 | 13.2 | 11.0 | P1 Table I, Racetrack, T^ | differs (+2.2) |
| Lattice 4 4 1 1, 12/24, FRA | heuristic | 18 21 21 23 21 \| 23 21 29 19 20 | 20.8 (seeds 0-4); 21.6 (0-9) | 26.6 | P2 Table I, Lattice, FRA T^ | differs (−5.8) |
| Horizontal Grate 6 2 1 1, 8/16, FRA | heuristic | 18 18 12 13 12 | 14.6 | 24.4 | P2 Table I, Horizontal Grate, FRA T^ | differs (−9.8) |
| (same) | heuristic | 18 18 12 13 12 | 14.6 | 14.6, the authors' own log | `qft.txt` in the artifact's history (added in a4588e4, 2024-06-25; present at 4f8564b; removed in abe9c51): "array ts: [18, 18, 12, 13, 12]", "& 6 2 1 1 & 8/16 & 8 & 14.6 & … compilation=True" | **exact** |

**Diagnostics.** These are not paper configurations; the paper value is shown for scale only. Every one
passes every check.

| diagnostic | per seed | mean | for scale |
|---|---|---|---|
| heuristic without Gate Selection, Lattice 4 4 1 1, 12/24 | 23 23 27 28 24 | 25.0 | P2 26.6 |
| heuristic without Gate Selection, Horizontal Grate 6 2 1 1, 8/16 | 15 22 25 22 22 | 21.2 | P2 24.4 |
| SAT on the transposed racetrack (arch [2, 2, 5, 1]: the PZ on a short side), 6/12 | 14 15 13 14 15 14 14 14 12 11 | 13.6 | P1 11.0 |

**Not replayed.**

- **Lattice 3 3 1 1, 12/12 (100%), FRA, seed 0.** The solver proved T = 2…19 unsatisfiable. It was killed
  at 1800 s while deciding T = 20. This seed alone therefore needs at least 20 states, against P1's
  10-run mean of 17.0 and mean t_CPU of 36.1 s. There is no schedule to replay; the console output is in
  `artifact/runs.json.gz`.

**Scale.** The 75 schedules hold 6,728 moves (4,125 SAT, 2,603 heuristic) as 22,037 events. 156 SAT moves
are multi-node racetrack slides of 2-5 nodes. Replaying and checking all of them takes about 2 s.

## 5. What the replay shows

- **P1's lattice rows are the code's printed count of states.** Three 10-run means match Table I to the
  decimal. Table I therefore reports N states (t = 0…N−1), not N−1 transitions. It is not the paper's
  own `0 ≤ t ≤ T`, which would be N−1.
- **P1's racetrack row is not reproduced** by the public code with these placements (13.2 against 11.0).
  Neither is its 100%-fill 3 3 1 1 row. The diagnostic transposition does not explain the racetrack
  row: it moves the mean further away, to 13.6. The ASP-DAC-era code (2023) is not in the repository's
  public history, whose oldest commit is 2024-06-05.
- **P2's heuristic rows are not reproduced, and the authors' own log agrees with our runs.** With the
  same code, the same seeds and Gate Selection on, 6 2 1 1 gives the authors' committed `qft.txt`
  exactly (mean 14.6), while Table I prints 24.4. On 4 4 1 1 the mean is 20.8 against 26.6. Without
  Gate Selection the numbers move toward the table (25.0, 21.2) but do not reach it. (RUN.md calls the
  log `results.txt`; it is `qft.txt`. `results.txt` at a4588e4 holds the single-seed line "6 2 1 1 …
  18.0".)
- **The SAT code's path quirk.** The SAT dumps record, for each move, the edges `get_path_between_edges`
  names, in `required_empty_at_prev_state`. This is the dump's own recomputation, in the edge
  orientation of the code's index dictionary. The solver satisfies a move through whichever disjunct of
  MV_CONSTR_2 holds, and the same target edge appears there in other orientations, which give other
  paths.
  - 209 moves list edges beyond those passed: 112 in L4411 12/24, 40 in L4411 6/24, 37 in L3311 and 20 in
    the transposed racetrack.
  - In 12 of them, all on the transposed racetrack and all leaving `e_in`, the listed edge (`e_out`) was
    occupied. The solver evidently used another orientation, so the field over-states what was required.
  - P1's rule, the edges actually passed, is what we check. It holds in all 6,728 moves.
- **The heuristic's PZ exchange.** In 185 iterations, one chain entered the parking edge while another
  left it through the PZ node. This is the code's explicit exemption. Every other node is passed by at
  most one chain per step in every schedule.

## 6. Our schedules, under the heuristic's model

`schedule_ours` writes a schedule for each of the heuristic's 15 paper runs. It uses the heuristic's
model only, so the comparison is like for like:

- the same device and the same placement, read from that run's dump;
- the same circuit (FRA);
- the rules of §3 that apply to the heuristic: one edge per chain per iteration through one node; one
  chain per node per iteration, except the PZ node; following into vacated edges; the one-way PZ; one
  gate per iteration, counted when the chain stands on the parking edge; chains may stay parked at the
  end.

Each schedule is written in the heuristic's dump format, judged by the same `check_ionshuttler`, and
counted as the heuristic counts: the 0-based index of the last iteration.

**The method.** It is prioritised planning in space and time.

- Chains are planned one at a time, against the moves already planned; a chain not yet planned stands
  still.
- The next chain planned is the one whose gate can complete earliest.
- A chain that is done leaves the parking edge when the edge is full. It goes to an edge off the pending
  chains' routes and away from where returning chains land.
- A chain that stands in everyone's way is moved aside, recursively.
- Restarts are seeded and randomised. The search stops at the first schedule that meets the **distance
  lower bound**: gates are serial, one per iteration, and a chain m moves from the parking edge is
  counted at iteration m−1 at the earliest, so the last iteration is at least
  max_j (d_(j) − 1 + (n − 1 − j)) over the sorted distances d.
- A schedule that meets the bound is optimal in this model.

| configuration | seeds | heuristic, printed | ours (= lower bound) | mean, heuristic → ours |
|---|---|---|---|---|
| Lattice 4 4 1 1, 12/24, FRA | 0-9 | 18 21 21 23 21 23 21 29 19 20 | 13 14 13 13 14 13 13 14 13 13 | 21.6 → **13.3**; seeds 0-4: 20.8 → 13.4 |
| Horizontal Grate 6 2 1 1, 8/16, FRA | 0-4 | 18 18 12 13 12 | 8 8 8 8 8 | 14.6 → **8.0** |

- All 15 pass every check, and an independent check (the earlier session's `check_heur.py`) finds no
  error either.
- Every one meets its lower bound, so each is optimal under the heuristic's model.
- They use about as many moves as the heuristic did on the same 15 runs: 1,598 against 1,571.
- They use the PZ exchange about as often: in 117 iterations against the heuristic's 115.
- Both tools need the exchange. Without it, the PZ node passes at most one chain per iteration, and 12
  arrivals plus at least 9 departures need 21 passes. That alone would put the 4 4 1 1 runs at 20 or
  more.

**Not compared with the SAT runs.** The SAT uses another PZ (§1) and another count (§2). P2's
`T^min` ("exhaustive search", method not described) cannot be in this model and count either. Its 20.2
on 4 4 1 1 is far above the optimum we find in this model for seeds 0-4 (13.4), if those are its five
runs.

The search takes about 15 s for all 15 runs. `reproduce()` regenerates the files in `ours/` byte for
byte.

## 7. What the core checker could not express (timed.py: not edited)

1. **A one-node exemption from `junction_mutex`.**
   - Minimal example: one dead-end site P (capacity 3) at node Z, and two sites X and N at Z. In one
     step, `c1` goes X → Z → P while `c0` goes P → Z → N.
   - Under a profile with `junction_mutex=True`, the core reports both node Z and segment `P@Z`
     overlapping. The node is held by both during `[a+1/4, a+3/4)`, and the chains cross on `P@Z`.
   - The heuristic's code allows exactly this (`Cycles.py` l.374).
   - `IONSHUTTLER_HEURISTIC` therefore turns both mutexes off, and `nodes` checks every other node per
     step.
   - Test: `test_the_pz_exchange_is_why_the_heuristic_profile_shares_nodes`.
2. **`junction_mutex` judges instants, the papers judge steps.**
   - Two multi-node slides can pass one node at disjoint sub-intervals of one step. The core then passes
     them, while P1 forbids them.
   - A one-node move holds its node for the whole middle half of the step, so such a pair is always
     caught when either move is a one-node move.
   - `nodes` closes the rest.
3. **Following into a vacated site needs half-steps.**
   - A `hop` holds its source until it ends and its destination from its start, so a train or a cycle
     rotation written as hops would fail `capacity`.
   - Written as split / passage / merge, it does not. This is the representation TrapSIMD uses; it is a
     representation, not a blocker.
4. **No rule for a fixed sequence or step counts.**
   - The core's `circuit` check orders two-qubit pairs per qubit. It does not cover a sequence of
     one-chain elements, "strictly increasing steps", or a gate counted in k iterations.
   - These are ours: `sequence`, `gates`, `sequence_code`.
5. **The makespan is the last event's end.**
   - To make it equal a tool's count, the clock carries the count's convention: SAT state t is
     `[t, t+1)`, and the heuristic's iteration t is `[t−1, t)`, so its schedule starts at −1.
   - The core handles a negative start without change.

## 8. Files

- `ours/<run>_ours.schedule.json.gz`: our schedule for each heuristic paper run (§6), in the heuristic's
  dump format. `schedule_ours` writes these files, and they are not tool output.
- `artifact/<config>_seed<S>.schedule.json.gz`: each tool's dump, byte for byte, with one exception. The
  absolute path of the run folder is cut from the command line and from the heuristic's circuit path. The
  file holds the tool's graph, the initial placement, every step's moves, gates and positions, and the
  printed number.
- `artifact/runs.json.gz`: each run's console output, exit code, wall time and peak memory, including the
  killed run.
- `artifact/patches/`: the three patches applied to `bc71e46`.
- `artifact/LICENSE`: the artifact's MIT license.
- `circuits/`: the two FRA circuits (`h q[i]` on every qubit). These are the authors' own
  `full_register_access_{8,12}.qasm`, recovered from commit `b6dc36c`. The SAT's sequence is in its dump.
- `results.json`: written by `reproduce()`. It holds per run the printed and replayed numbers, every
  check, violation counts and the module's counts; per configuration the means and the paper's value
  with its location; the run that was not replayed; the model (clocks, profiles, rules with their
  sources); and, under `ours`, each of our schedules with its checks, its lower bound and the
  heuristic's number for the same placement.
