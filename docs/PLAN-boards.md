# Two leaderboards, a logical error rate, and one declaration per agent feature

Status: **decided** · 2026-09-28 · the first iteration; the noise model and the scoring are meant
to be revised once the product can be seen.

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
| 1 | how compilers are submitted | a **source archive** built and run on our pinned runtimes (python3.13, ocaml5.3, a static linux-x86_64 binary), with no network |
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

## Later: a more realistic noise model (`qccd-noise@2`)

* Idle time per ion from the schedule's `t0/t1`, not the serial replay.
* Dephasing induced by transport and junctions.
* Crosstalk from gates in neighbouring zones.
* Ion loss after about 85 uncooled junction round trips.
* Imperfect sympathetic cooling.
* A calibrated T2.
