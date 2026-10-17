# WISE direction investigation: stop before scheduling

The existing mapping has uniform physical routes in Stim steps 1 and 4,
but two opposite route classes in steps 2 and 3. Task 2 explicitly requires
stopping and reporting in this case. No scheduler, milestone schedule, full
round, or alternative mapping has been used.

Directions below are coordinate signs: +y is called north, -y south, +x east,
and -x west. These names do not assume a screen orientation. Every leg is 60
coordinate units. Routes include both segment IDs and the intervening junction.

| Step | Ancilla → data | Physical route | Direction (coordinate signs) |
|---|---|---|---|
| 1 | 2 → 3 | T17 —C161→ J160 —C316→ T27 | ((0.0, 60.0), (60.0, 0.0)) |
| 1 | 16 → 17 | T58 —C209→ J208 —C344→ T69 | ((0.0, 60.0), (60.0, 0.0)) |
| 1 | 11 → 12 | T38 —C188→ J187 —C332→ T50 | ((0.0, 60.0), (60.0, 0.0)) |
| 1 | 14 → 15 | T56 —C206→ J205 —C342→ T67 | ((0.0, 60.0), (60.0, 0.0)) |
| 1 | 9 → 10 | T36 —C185→ J184 —C330→ T48 | ((0.0, 60.0), (60.0, 0.0)) |
| 1 | 18 → 19 | T60 —C212→ J211 —C346→ T71 | ((0.0, 60.0), (60.0, 0.0)) |
| 2 | 2 → 1 | T17 —C161→ J160 —C315→ T25 | ((0.0, 60.0), (-60.0, 0.0)) |
| 2 | 16 → 15 | T58 —C209→ J208 —C343→ T67 | ((0.0, 60.0), (-60.0, 0.0)) |
| 2 | 11 → 10 | T38 —C188→ J187 —C331→ T48 | ((0.0, 60.0), (-60.0, 0.0)) |
| 2 | 14 → 8 | T56 —C183→ J181 —C328→ T46 | ((0.0, -60.0), (60.0, 0.0)) |
| 2 | 9 → 3 | T36 —C162→ J160 —C316→ T27 | ((0.0, -60.0), (60.0, 0.0)) |
| 2 | 18 → 12 | T60 —C189→ J187 —C332→ T50 | ((0.0, -60.0), (60.0, 0.0)) |
| 3 | 16 → 10 | T58 —C186→ J184 —C330→ T48 | ((0.0, -60.0), (60.0, 0.0)) |
| 3 | 11 → 5 | T38 —C165→ J163 —C318→ T29 | ((0.0, -60.0), (60.0, 0.0)) |
| 3 | 25 → 19 | T79 —C213→ J211 —C346→ T71 | ((0.0, -60.0), (60.0, 0.0)) |
| 3 | 9 → 8 | T36 —C185→ J184 —C329→ T46 | ((0.0, 60.0), (-60.0, 0.0)) |
| 3 | 18 → 17 | T60 —C212→ J211 —C345→ T69 | ((0.0, 60.0), (-60.0, 0.0)) |
| 3 | 13 → 12 | T40 —C191→ J190 —C333→ T50 | ((0.0, 60.0), (-60.0, 0.0)) |
| 4 | 16 → 8 | T58 —C186→ J184 —C329→ T46 | ((0.0, -60.0), (-60.0, 0.0)) |
| 4 | 11 → 3 | T38 —C165→ J163 —C317→ T27 | ((0.0, -60.0), (-60.0, 0.0)) |
| 4 | 25 → 17 | T79 —C213→ J211 —C345→ T69 | ((0.0, -60.0), (-60.0, 0.0)) |
| 4 | 9 → 1 | T36 —C162→ J160 —C315→ T25 | ((0.0, -60.0), (-60.0, 0.0)) |
| 4 | 18 → 10 | T60 —C189→ J187 —C331→ T48 | ((0.0, -60.0), (-60.0, 0.0)) |
| 4 | 13 → 5 | T40 —C168→ J166 —C319→ T29 | ((0.0, -60.0), (-60.0, 0.0)) |

Step 1: 1 direction classes.
Step 2: 2 direction classes.
Step 3: 2 direction classes.
Step 4: 1 direction classes.
Uniform one-junction vector assignments: 256 examined, 4 consistent, 0 with distinct home traps.

## Alternative mapping investigation

There is no alternative mapping with distinct home traps that makes all four
steps uniform under the specified single-junction, six-cycle outbound move.
The audit checks all 4^4 = 256 assignments of displacement (±60, ±60) to the
four steps. It propagates the required relation data_position =
ancilla_position + step_displacement through the connected interaction graph.
Only four assignments are algebraically consistent; each makes all four
step displacements identical, forcing distinct data qubits onto the same
position. None is injective. The search ignores device boundaries, trap axes,
and locks, so it is a relaxation of the actual problem: those extra constraints
cannot restore an injective solution. The result covers one home trap per
qubit as in the source layout, with ancillas returning home between steps.

This does not rule out longer routes with additional intermediate traps,
direction groups within a step using opt-out, or evolving ancilla positions
between steps. These require a different routing investigation; no concrete
alternative mapping with uniform routes has been established. A proposed
next investigation is to preserve the source mapping and serve each of the
two direction groups in steps 2 and 3 in separate broadcast passes. This is
not a verified schedule and changes the one-pass-per-step algorithm.

## Profile and validation

JONES_WISE inherits every JONES exclusion and circuit rule, and enables the
existing broadcast checker for split, merge, transit, j_enter, j_exit, swap.
It uses the unchanged jones_duration law and JONES_US times. JONES and the
checker were not modified. The broadcast checker reads meta["class"]; a
future scheduler must encode primitive and physical direction in this value.
The checker does not infer physical direction from the route.

The checker regression validates a legal broadcast and rejects differing
classes, differing ends, overlapping cycles, and missing classes. This is a
toy profile test, not Milestone 1. Existing timed-checker tests also pass:
17 tests total.

Per-zone opt-out and separate entry/exit classes are assumed in the requested
future default. Scheduler flags for these choices are deferred with the
scheduler. The profile alone does not enforce universal zone participation.
The requested tools/check_cnot_order.py is absent from this checkout.
No CNOT-order or distance validation has been claimed.

## Comparison (user-provided baselines)

| Schedule | Round time (µs) | CNOT layer (µs) |
|---|---:|---:|
| Jones standard | 4085 | — |
| John standard | 2385 | — |
| Jones WISE worst-case formula | 19975 | 4295 |
| This WISE schedule | Not built: task-2 stop condition | Not measured |

Last start, makespan, transport durations, broadcast-cycle counts, and
operation counts are not available because no WISE schedule was built.
No conclusion about the WISE formula's tightness follows from this audit.
Existing schedules, sched_jones.py, catalog.py, and results.json are unchanged.
Only local device and circuit data were used; no PartIIProject code was read
or copied. Nothing was pushed.

## Reproduce

Run from the QCCD repository root on branch jaewon/wise-jones:

```sh
python3 -m qccd.repro.wise_audit
.venv/bin/python -m pytest tests/test_repro_wise.py tests/test_repro_timed.py -q
```
