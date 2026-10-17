# WISE schedules on the Jones d=3 capacity-2 grid

Both modes use the artifact's exact device, qubit-to-ion mapping, and initial
chains. Both return each ancilla home after every CNOT. Both pass the unchanged
timed checker under JONES_WISE with strict Jones durations and the original
source circuit; broadcast is **passed**, not skipped, and there are zero
violations. Both pass the unchanged tools/order.py: source and executed
detectors are deterministic and circuit distance is 3, RESULT: PASS.

The earlier direction audit remains the historical pre-scheduler checkpoint.
The accepted continuation authorizes splitting direction groups or changing
the Z-check visiting order; neither mode uses a new qubit mapping.

## Milestone 1

Only the first Stim CX layer is executed, after the source reset/H prefix,
followed by the ancillas' return home. There are no suffix measurements or
resets. timed.check passes all configured checks, including broadcast, the
six source CNOTs, chain order, declared locks, and strict durations.

* Makespan: **660 µs**; last start: **580 µs**.
* Prefix: 50 µs reset + 10 µs H decomposition.
* CNOT step: **600 µs**, comprising 270 µs outbound transport + 60 µs native
  CNOT decomposition + 270 µs return transport.
* Transport: **540 µs**; **12 broadcast cycles**; 127 events.
* Counts: reset 17, gate1 32, gate 6, split 12, merge 12, transit 24,
  j_enter 12, j_exit 12; measure 0, swap 0.

Archive: `grid_rot_d3_c2_wise_split_step1.schedule.json.gz`.

## Full-round results

Transport time is elapsed broadcast-cycle time, including outward and return
moves, summed over passes. It excludes rotations and MS gates. Summing ion
event durations would instead count simultaneous events multiple times.

| Mode | Last start (µs) | Makespan (µs) | Transport by step (µs) | Transport total (µs) | Broadcast cycles | timed.check | order.py |
|---|---:|---:|---|---:|---:|---|---|
| split | 4070 | **4120** | 540, 1080, 1080, 540 | 3240 | 72 | PASS, zero violations | PASS, deterministic detectors, distance 3 |
| shared_shape | 2870 | **2920** | 540, 540, 540, 540 | 2160 | 48 | PASS, zero violations | PASS, deterministic detectors, distance 3 |

Including the native CNOT operations, step durations are 600, 1200, 1200,
600 µs for split and 600 µs for each shared_shape step. An outward route is
six cycles: split 80, transit 5, j_enter 50, j_exit 50, transit 5, merge 80 µs.
The return route repeats these six primitives. Each pass therefore costs
540 µs of transport plus 60 µs of native CNOT operations.

Both rounds contain **466 events**, with identical counts:

| Kind | split | shared_shape |
|---|---:|---:|
| reset | 25 | 25 |
| gate1 | 112 | 112 |
| gate | 24 | 24 |
| measure | 17 | 17 |
| split | 48 | 48 |
| merge | 48 | 48 |
| transit | 96 | 96 |
| j_enter | 48 | 48 |
| j_exit | 48 | 48 |
| swap | 0 | 0 |

split preserves every qubit's source CNOT order. In steps 2 and 3 it completes
the X-check pass (outward move, gates, return) before starting the Z-check
pass. Nonparticipants remain stationary.

shared_shape assigns each check to the X visiting offsets: (+1,+1), (-1,+1),
(+1,-1), (-1,-1). Boundary checks participate only where they have a partner.
For Z-check ancillas 9 and 18, this exchanges their second and third partners;
boundary Z checks 14 and 13 move to the other middle layer. The order checker
reports changed per-qubit sequences on qubits 8, 9, 12, and 18. These changes
are within the commuting runs allowed by JONES_WISE, and the detector/distance
check confirms distance 3 for this one-round memory-Z circuit.

## Comparison

The baseline round figures below are the values specified in the task.
John's saved standard schedule also confirms makespan 2385 µs (last start
2335 µs). The WISE rows report measured schedule makespans, not last starts.

| Schedule | Round time (µs) | CNOT layer / step (µs) |
|---|---:|---|
| Jones standard | 4085 | — |
| John standard | 2385 | — |
| Jones WISE worst-case formula | 19975 | 4295 |
| This WISE, split | **4120** | 600, 1200, 1200, 600 |
| This WISE, shared_shape | **2920** | 600 each |

Under the requested Jones timings and WISE broadcast assumptions, the
worst-case formula is not tight for this device/circuit: a checked schedule
preserving source CNOT order finishes in 4120 µs. Changing the Z visiting
order reduces it further to 2920 µs while retaining the tested distance.
These results do not establish an optimum, and do not measure physical
hardware waveform performance.

## Assumptions and configurable choices

* Jones timings and all Jones exclusion rules are unchanged. No extra waveform
  setup, switching, cooling, or direction-dependent duration is charged.
* A broadcast class identifies primitive plus physical leg direction, such as
  `split:N`, `transit:E`, `j_enter:N`, or `j_exit:E`. N/S mean positive/negative
  device y; E/W mean positive/negative x. The scheduler validates uniform
  route directions before emitting a pass. The existing checker enforces
  classes, equal starts/ends, and nonoverlapping broadcast cycles.
* `--middle-steps split` is the default. `shared_shape` is the authorized
  alternative CNOT order, validated against the unchanged original circuit.
* `--zone-opt-out` defaults to enabled and is required by both schedules:
  data zones and idle/boundary ancilla zones sit still. `--no-zone-opt-out`
  rejects this layout explicitly instead of silently leaving zones idle.
  Empty zones carry no ions and need no motion events.
* `--separate-junction-classes` defaults to enabled: j_enter and j_exit have
  distinct classes. With `--no-separate-junction-classes`, their waveform
  primitive is named `junction`, retaining the leg direction. The operations
  still occur in distinct cycles and retain their 50 µs duration. This flag
  changes class equivalence only; it does not combine entry and exit into one
  operation or claim a faster round. Default schedules use the separate classes.
* Native operations follow sched_jones.stim_program: H is RY then RX; CX is
  RY(control), RX(control), RX(target), MS, RY(control); MR is M then R.
  The shared trap serializes the four rotations and MS, for 60 µs per pass.
  Logical qubit labels remain attached to ions; MS does not exchange them.
* Every transport primitive carries the same component locks as sched_jones;
  in-place operations carry ion and trap locks. The independent ion-serial
  check also applies to transport. Departures always use the arrival end on
  return, so no swaps are needed. Every ancilla ends at its original home.
* Prefix operations use global barriers, adding 60 µs. After the last return,
  each qubit executes its source suffix independently at home. Data M takes
  400 µs; Z-ancilla MR takes 450 µs; X-ancilla H then MR takes 460 µs.
  Logical measurement record identities are those of the source circuit,
  independent of the different traps' measurement start times, as assumed
  by tools/order.py and the existing standard scheduler.
* This implementation supports the specified one-round circuit with four
  explicit Stim CX layers. `--steps 1` is a source prefix, not a complete
  error-correction round. No distance is claimed for that prefix.
* tools/order.py performs its existing 500-shot noiseless detector/observable
  sampling and bounded circuit-level logical-error search. It reports source
  and executed distance 3 for both full schedules. The checker was not edited.

Existing schedules, sched_jones.py, JONES, timed.py, tools/order.py, catalog.py,
and results.json were not changed. No PartIIProject code was used. No push
was performed. Validation summaries and raw order-check output are saved in
`wise_validation.json`, separately from the catalog results.

## Exact reproduction commands

From the QCCD repository root on branch jaewon/wise-jones, use the existing
virtual environment (the host's `python` is Python 2):

```sh
.venv/bin/python -m qccd.repro.sched_wise --steps 1
.venv/bin/python -m qccd.repro.sched_wise --middle-steps split
.venv/bin/python -m qccd.repro.sched_wise --middle-steps shared_shape
.venv/bin/python tools/order.py Reproduce/jones2025/ours/grid_rot_d3_c2_wise.schedule.json.gz Reproduce/jones2025/circuits/rotated_memory_z_d3_r1.stim
.venv/bin/python tools/order.py Reproduce/jones2025/ours/grid_rot_d3_c2_wise_shared_shape.schedule.json.gz Reproduce/jones2025/circuits/rotated_memory_z_d3_r1.stim
.venv/bin/python -m pytest tests/test_repro_wise.py tests/test_repro_timed.py -q
.venv/bin/python -m pytest tests/test_repro_sched_papers.py -k jones -q
```

The scheduler checks before writing each gzip archive in the existing ours
TimedSchedule JSON format. `--out PATH` selects an alternative output path.
The first test command passes 21 tests; the Jones regression command passes
3 tests (6 deselected).
