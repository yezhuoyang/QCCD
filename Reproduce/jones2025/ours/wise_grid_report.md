# WISE capacity-2 Jones grids: d=2, 6, 12

Headline time is **last start**, matching the Reproduce page. All six
schedules pass timed.check under JONES_WISE with strict Jones durations,
the original source circuit, zero violations, and broadcast explicitly
**passed**. The unchanged tools/order.py reports deterministic source and
executed detectors and RESULT: PASS for every row.

| d | Mode | **Last start (µs)** | Makespan (µs) | Broadcast cycles | timed.check | Detectors | Source distance | Executed distance |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2 | split | **4070** | 4120 | 72 | PASS, 0 violations | deterministic | 2 | 2 |
| 2 | shared_shape | **2870** | 2920 | 48 | PASS, 0 violations | deterministic | 2 | 2 |
| 6 | split | **4070** | 4120 | 72 | PASS, 0 violations | deterministic | 6 | 6 |
| 6 | shared_shape | **2870** | 2920 | 48 | PASS, 0 violations | deterministic | 6 | 6 |
| 12 | split | **4070** | 4120 | 72 | PASS, 0 violations | deterministic | 12 | 12 |
| 12 | shared_shape | **2870** | 2920 | 48 | PASS, 0 violations | deterministic | 12 | 12 |

shared_shape loses no distance at these d values, so the requested stop
condition did not occur. split matches the source CNOT order on every qubit.
shared_shape changes per-qubit orders on 1 qubit at d=2, 25 qubits at d=6,
and 121 qubits at d=12. Exact source and
executed sequences, including every changed qubit ID, are preserved for
all modes in `wise_grid_validation.json`.

The builder already derives routes, roles, and layers from its device,
mapping, and circuit inputs. The generalization adds `--distance {2,3,6,12}`
to choose the matching artifact and circuit and name the output; default
distance remains 3. No routing or timing changes were necessary.

Every schedule uses its corresponding artifact's exact device, initial
chains, and qubit-to-ion mapping. Every ancilla returns home after each gate.
The accepted d=3 assumptions and opt-out/junction flags remain in effect.
split has six passes and shared_shape four; each pass takes 600 µs regardless
of distance because distinct traps and junctions operate in parallel.
Transport per step is 540, 1080, 1080, 540 µs in split and 540 µs per step in
shared_shape. The checker verifies resource exclusivity at the larger sizes.

No d=3 archive or report was rewritten. Regression tests rebuild both d=3
rounds and verify exact JSON equality with their saved outputs. New tests
also rebuild and strictly check all six larger/smaller-grid archives against
their original circuits and compare the saved schedules exactly.

The source detector/distance checks use the unchanged tools/order.py:
500-shot noiseless sampling and its bounded circuit-level logical-error
search. These are its reported distances, not a new distance-proof method.
No existing schedule, checker, profile, catalog, or results.json was changed.
No PartIIProject code was used and nothing was pushed.

## Reproduce

Run from the QCCD repository root on branch jaewon/wise-jones using the
existing virtual environment:

```sh
for d in 2 6 12; do
  .venv/bin/python -m qccd.repro.sched_wise --distance "$d" --middle-steps split || exit 1
  .venv/bin/python tools/order.py "Reproduce/jones2025/ours/grid_rot_d${d}_c2_wise.schedule.json.gz" "Reproduce/jones2025/circuits/rotated_memory_z_d${d}_r1.stim" || exit 1
  .venv/bin/python -m qccd.repro.sched_wise --distance "$d" --middle-steps shared_shape || exit 1
  .venv/bin/python tools/order.py "Reproduce/jones2025/ours/grid_rot_d${d}_c2_wise_shared_shape.schedule.json.gz" "Reproduce/jones2025/circuits/rotated_memory_z_d${d}_r1.stim" || exit 1
done
.venv/bin/python -m pytest tests/test_repro_wise.py tests/test_repro_timed.py -q
```

The scheduler runs timed.check before writing each archive. The test command
passes **29 tests**. Validation results and raw order-check output are saved
in `wise_grid_validation.json`, independently of the catalog results.
