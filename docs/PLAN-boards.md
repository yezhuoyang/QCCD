# Two leaderboards, a logical error rate, and one declaration per agent feature

Status: **built** (phases 0–4 on `compiler`; phase 5 on `website/invitations`) · 2026-09-28 · the
first iteration; the noise model and the scoring are meant to be revised once the product can be
seen. §"As built" at the end says where the build departed from this plan and why.

The Leaderboard gets two sections:

* **Architecture**: what exists today. A person designs a QCCD device; our compiler (`qccdc`)
  compiles the board's circuit onto it; the grader checks, measures and ranks the result.
* **Compiler**: new. A person submits a compiler; we run it on a benchmark suite of
  (circuit × device) pairs and grade every output for correctness and performance.

Both are graded with a new measurement: the **logical error rate (LER)** of a compiled QEC
program, from a Stim circuit extracted from the hardware program, under a noise model built from
the device's own physics.

Every feature below ships with its agent surface (MCP tool, CLI verb, skill text, declared
page controls), declared **once**.

---

## 0. What was checked before planning

A prototype ran the whole path on real compiler output: a memory-experiment QASM → `qccdc_cli
compile` → `insert_cooling.py` → replay → a noisy `stim.Circuit` → pymatching / BP-OSD → LER.

Surface code d = 3, three rounds, 50 000 shots:

| device | cooling | max n̄ | mean MS error | LER |
|---|---|---:|---:|---:|
| grid9x9 | inserted | 0.0 | 1.84e-3 | 1.3e-3 |
| grid9x9 | none | 74 | 5.0e-2 | 2.5e-1 |
| cyclone_base | inserted | 0.6 | 1.89e-3 | 1.3e-3 |
| cyclone_base | none | 7.8 | 6.7e-3 | 9.9e-3 |

The LER separates devices and compilation choices; extraction and decoding take under a second.

A **noiseless check** of the extracted circuit catches wrong compiler output. Every detector
must be deterministic and must have the parity the source circuit gives it. A flipped MS angle,
a dropped R pulse and a dropped MS were all caught. Determinism alone missed the flipped angle,
because a Pauli-frame slip keeps detectors deterministic and only flips their values. This check
sees measurements and resets, which the existing semantic check does not.

Findings that shaped the plan:

1. The official server never compiles. It grades a bundle the client built, so the Compiler
   track is the first time the server runs someone else's code.
2. Beyond the rule replay, the correctness chain depends on qccdc's certificate. A third-party
   compiler needs a certificate-free semantic check: the Stim check above, plus the tableau and
   unitary checks driven by a qubit map instead of a certificate.
3. The agent surface had no single registry. Tools, dispatch, routes, CLI verbs and skill text
   were hand-kept in parallel tables. `SKILL_VERSION` was bumped by hand, and an unknown job kind
   fell through to `evaluate`.
4. Every release carries `track: "codesign-artifact"`, which is the hook for the two sections.
5. The shipped devices declare `T_coh_s = 600`, which makes idling noise negligible (~2e-5 per
   ion per experiment). Heating through the MS error dominates. T2 is the first thing to
   calibrate when the noise is made more realistic.

## Decisions

| # | question | decision |
|---|---|---|
| 1 | how compilers are submitted | a **source archive** run on our pinned runtimes (the grader's Python 3, or a static linux-x86_64 binary), with no network |
| 2 | how the Compiler section ranks | **speedup** (geometric mean of T_jones baseline / yours over pairs both compiled) among eligible submissions; coverage shown next to it |
| 3 | how memory boards rank | **LER per round**, lower is better |
| 4 | Phase 0 scope | migrate **every** existing tool to the registry now |
| 5 | where the website change lands | `website/invitations` |

## Phase 0: one declaration per agent feature

`qccd/workspace/features.py` holds one `Feature` per capability. Each Feature carries:

* its name and the text written for agents;
* the input schema and whether it only reads;
* the service call it makes, or a named handler for tools with custom dispatch;
* its CLI verb, its skill section and its job kind.

From that one table come the MCP tool list, `READ_ONLY`, dispatch, and the tool table on the
reference and Agentic pages. The skill's refresh keys on a digest of its content, so an edit
reaches installed workspaces with no manual bump. Job kinds dispatch from a table, and an
unknown kind is refused.

The gate test fails when any of these happens:

* a tool, verb or route has no Feature;
* a Feature is missing a surface;
* the skill does not name a tool.

## Phase 1: Stim and LER engine (`qccd/qec/`)

* `experiments`: memory-experiment generators for repetition d, rotated surface d (the
  Tomita–Svore schedule of `gadget/surface.py`), Steane, and BB codes. Each gives QASM plus
  detectors and observables written on **classical bits**, so they are compiler-independent.
* `noise`: named, versioned models. `qccd-noise@1`:

  | where | channel |
  |---|---|
  | after each MS | `DEPOLARIZE2(gate_error(arch, n̄, chain length))` |
  | after each R | `DEPOLARIZE1(1 − F_1q)` (VZ is virtual) |
  | before each measurement | `X_ERROR(1 − F_meas)` |
  | after each reset | `X_ERROR(reset.error)` |
  | on every ion, every cycle | `Z_ERROR((1 − e^{−dt/T2})/2)` |

  A board names its model; changing the physics is a new model, never an edit.
* `extract`: TSIR + qubit map + circuit + detectors → `stim.Circuit`, plus a per-channel error
  budget. A qubit's k-th measurement in the program is its k-th `measure` in the circuit, so no
  certificate is needed.
* `check`: the noiseless determinism-and-parity check.
* `decode`: pymatching for graph-like models, BP-OSD for the rest. Sampling is adaptive (stop at
  `max_errors` or `max_shots`), with a Wilson interval and LER per round.
* Agent surface: job kind `ler`, `qccd_estimate_ler`, `qccd ler`, a `noise` reference section,
  and the `qccd.ler_report` schema.

## Phase 2: LER on the Architecture section

New releases, with the old ones unchanged: `rep5_mem@1` and `surface3_mem@1`, and later a BB
memory board.

* A release names its `noise` model and `ler` budget.
* The evaluator gains an `ler` stage.
* The ranked metric is `ler_per_round`.

The official LER is computed on the server; a local LER is labelled an estimate, because Stim's
sampling differs across machines.

## Phase 3: benchmark suite (`qccd/bench/`, `suite@1`)

* Circuits: QEC memory experiments; GHZ, QFT, Bernstein–Vazirani and adder circuits; seeded
  random Clifford circuits.
* Devices: the reference architectures, plus generated grids, rings, racetracks and dual loops.
* A pair is in the suite only when the device can hold the circuit.
* The baseline is qccdc plus cooling.
* The public split is published. The hidden split uses fresh seeds and sizes and runs only on the
  server.

For each pair, the checks are:

1. the rules replay;
2. semantics: the Stim check for QEC circuits, tableau equivalence for Clifford circuits, and
   unitary equivalence up to 10 qubits;
3. metrics;
4. LER on memory pairs.

Harness: `qccd bench run --compiler "<cmd>"`.

## Phase 4: Compiler section

Contract `qccd.compiler@1`:

* invocation: `<entry> --circuit C --device D --expanded E --out DIR`;
* output: `DIR/program.tsir.json` in the device's native gates, with `meta.qubit_map`;
* optional `DIR/certificate.qcert.json`, which earns a "verified" badge when Lean accepts it;
* exit code 4 is a refusal, and any other non-zero exit is a crash;
* cooling is the compiler's job, and R7 enforces it.

Scoring:

* Each pair is *valid*, *wrong*, *refused*, *timeout* or *crash*.
* Any *wrong* pair makes the submission ineligible.
* Metrics: coverage, speedup, LER ratio, and compile time (reported only).

Flow:

* locally, `qccd bench run`;
* officially, `qccd publish --compiler` (a human approves), which uploads the source plus
  `qccd-compiler.toml`;
* the server builds and runs it without network, with per-pair limits, in the hardened grader
  container.

## Phase 5: website Leaderboard (`website/invitations`)

* `/board/` has two sections, driven by each release's `track`: Architecture (today's boards
  plus the memory boards with LER) and Compiler (the suite board).
* Existing URLs stay.
* Every new control is declared, and the board pages' old undeclared controls are declared too.
* The Agentic page lists the new tools.

Order: 0 → 1 → (2 ∥ 3) → 4 → 5.

## `qccd-noise@2` and `qccd-physics@2` (2026-10-21)

`qccd-noise@2` (`qccd/qec/noise.py`) prices what a schedule does, not only its gates, and
`qccd/workspace/physics/qccd-physics@2.json` is the physics it is graded under. Every number
cites a measurement, and every channel is a Pauli channel stim samples directly. The plan's
list, item by item:

* **Idle time per ion.** Each ion pays `Z_ERROR(r·Δt)` for every instruction that does not
  operate on it, whether it idles, rides a rotation or is shuttled. r = 7.9e-3/s is Helios's
  linear memory rate (arXiv:2511.05465 Table II; Helios-1E emulator). Each idle stretch T
  also ends with `Z_ERROR(sin²(πfT))`, with f = 0.058 Hz. This is the twirl of the coherent
  term, and it reproduces the paper's quadratic memory parameter.
* **Dephasing from transport.** No QCCD experiment or vendor publishes a per-shuttle,
  per-junction or per-split qubit error. Transport is charged through its time (the memory
  channel) and its heating (the MS error).
* **Crosstalk.** Each measurement puts `DEPOLARIZE1(2.1e-4)` on the other ions in its trap
  and 4.3e-5 on every other ion; each reset puts 3.5e-6 on every other ion (Helios §III B5,
  Fig. 10; Helios-1E). MS crosstalk was not detected on Helios, below 4.3e-4, so it is 0.
* **Ion loss.** Not modelled: it is not a Pauli channel and no vendor number exists. Leakage
  is already inside the measured gate and memory errors and is charged as their Pauli error,
  as the vendor's stabilizer emulator does.
* **Imperfect cooling.** Every MS gate sees n̄ plus 0.05, the floor ground-state cooling leaves
  (arXiv:2206.11888).
* **A calibrated T2.** Replaced by the memory channels. The first |0⟩ carries the reset error.

`qccd-physics@2` replaces numbers in `@1`'s block that no source supports. Gate, readout and
memory errors become Helios's (137Ba⁺, 171Yb⁺ coolant). Transport becomes Quantinuum's
published primitives in a new operating-point table, `quantinuum`:

* split and merge: 128 µs, at most 1 quantum (arXiv:2003.01293 Table II);
* one 375 µm leg: 94 µs at 4 m/s, a share of ≤ 0.03 quanta per junction round trip
  (arXiv:2206.11888);
* MS: 7.9e-4, 9.5e-4 per quantum (arXiv:0810.0670 §5.2).

`@1`'s junction figure, 3 quanta, comes from the 2009 NIST experiment and is about 100 times
modern data. Its "+2e-3 per quantum" and T2 = 600 s have no source. A board pins both ids, so
`@1` boards are unchanged: their LERs and budgets are identical to the last bit.

The finding that shapes the boards: **a Z-basis memory cannot see dephasing**, because a Z
error flips no Z logical. Under `@2` on surface-3, h2_racetrack has the most memory error but
the best Z-basis LER, and the third worst X-basis LER. A board that ranks schedules must
measure both bases. rep5 cannot rank at all: its LER is about 1e-7 per round.

`rotated_surface_xz(d)` (`surface3xz`, `surface5xz`) runs a Z-memory patch and an X-memory
patch in lockstep in one circuit, so a board keeps one circuit and one program. A shot fails
if either observable flips. The appended readout reads each data qubit in its own basis
(`readout_basis` in the detector spec, present only when the bases differ). Measured under
`@2`, 4M shots:

| experiment | ions | designs graded | spread | separated tiers |
|---|---|---|---|---|
| surface3, Z basis | 17 | 12 | 1.75× | 3 |
| surface3, X basis | 17 | 11 | 1.81× | 5 |
| surface3xz | 34 | 9 | 2.13× | 4 |
| surface5, X basis | 49 | 4 | 22× (2.0e-5 to 4.4e-4) | — |

At d = 5 the slowest design, ring144_24v (1.8 s for five rounds), is worse than its own d = 3:
transport time takes it above threshold.

**The limit for Goal 1 was the reference compiler, not the noise.** qccdc e799ecf5a5cd
refuses surface5 (49 ions) on 8 of 12 designs and surface5xz (98 ions) on every design:
unroutable, short of docks, or short of capacity. It also refuses the X-basis surface3 on
ring16, because the rotation cannot apply `h` to an ion that rides the loop.

Split rounds (`Compiler/README.md`) are qccdc's last fallback. With them:

* surface5xz compiles on grid9x9 (6.0e-5 per round, 598 ms), deck_unit_cell and cyclone_base
  (6.5e-5, 696 ms), passes every rule, and on grid9x9 passes R10 with the proved checker;
* surface5 compiles on grid5x5 (3.8e-5), ladder_2x72 (5.1e-5), torus4x4 and random16. The last
  two fail only R20/R21, their wiring budget, as they do at d = 3.

Still refused: ring144_24v at 98 ions (rotation needs 48 docks, it has 24), ring24_8, and
devices with fewer slots than ions.

### Goal 1's board: `surface5xz_mem@1` (2026-10-21)

The board is `surface5xz` (98 ions) under `qccd-noise@2` and `qccd-physics@2`. It is timed on
the `quantinuum` table (`T_quantinuum`), ranked by the LER per round, and sampled up to 2,000,000
shots or 400 logical errors (seed 20261021). The starter is grid(9, 9).

A design is entered on a board with that board's physics (`qccd.workspace.tasks.board_device`).
A design still carrying the template's fixed blocks takes the board's, and one whose physics was
edited keeps it and is exploratory, as before. This applies to the compile job, the frozen
bundle, program runs and a board's LER job. Every earlier board's physics is the template's, so
nothing changes on them.

End to end with the released tools (qccdc c774b87b677f, qcheck e8acd6640fd3), the starter is
eligible: every stage passes, 5.2e-5 per round, 598 ms. `Codesign/scripts/memory_boards.py`
builds the board's pages from the release its task names.

## As built (2026-09-28)

Where the implementation departed from the plan, and why.

* **A memory experiment's data readout is appended by the grader.** qccdc's rigid rotation, the
  only mode whose programs pass every rule on ring devices, refuses to measure an ion that rides
  the loop. Its router's programs fail R22 on rings and grids. So a memory board's circuit is its
  syndrome rounds only. The extraction appends the final data measurement where the ions are,
  with the device's measurement error and duration (`MemoryExperiment.readout = "appended"`).
  `rep5_mem@1` and `surface3_mem@1` compile by rotation on the suggested ring(8,2,8) and pass
  every rule. This is an approximation to revisit together with the noise model.
* **The compiler manifest is JSON** (`qccd-compiler.json`), not TOML: `tomllib` is not in Python
  3.10, the oldest the project supports.
* **The runtimes are `python3` and `native`.** The grader image has no OCaml toolchain, so an
  OCaml compiler submits its built static binary.
* **The server never runs a submitted compiler in the grader.** The grader hands every build and
  every pair to a separate `sandbox` container through a spool (`qccd/bench/sandbox.py`). That
  container has no network, no secrets, and nothing but its spool, and kills whatever the
  compiler left behind after each request. The hidden split is generated by the worker, so the
  grader and the sandbox never see its seed. Without `QCCD_HIDDEN_SEED` there is no hidden split,
  and every report says how many hidden pairs it graded.
* **Running a compiler is never an agent tool.** `qccd bench run` runs the person's own code, so
  it runs in their shell, under their agent client's permissions. Agents read the reports
  (`qccd_get_bench`) and the contract (`qccd_read_reference(section="compiler")`). Publishing
  needs a person at an interactive terminal, as for designs.
* **Grading in the harness is serialized.** Compilers run in parallel, but outputs are graded
  one at a time: two threads inside stim at once deadlocked.
* **The reference compiler on `suite@1`:** 68 of 108 pairs valid, 20 wrong (all R22), 19 refused,
  1 timeout. The wrong and refused pairs are where a better compiler shows. Speedup is measured
  only where both are valid, and coverage separately.
* **Memory boards pin their decoder** (pymatching). The flagged Steane experiment decomposes into
  graph-like pieces, yet matching decodes it about 4× worse than BP-OSD. `evaluate_memory` picks
  BP-OSD for a non-matchable experiment when the decoder is `auto`.
* **Known gaps for the next iteration:**
  - VZ is noiseless, though the cost model charges three pulses without a light-shift gate.
  - Idle time is the serial replay's.
  - The official image pins stim 1.15, the development venv has 1.16, so a local LER is an
    estimate.
  - `bb72` compiles only as one round, and its program fails R22.
