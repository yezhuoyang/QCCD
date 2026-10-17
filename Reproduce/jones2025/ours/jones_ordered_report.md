# Direct wiring, Stim order

The new standard-wiring scheduler preserves Stim's CNOT order exactly on
every qubit, using the original capacity-2 Jones devices, qubit-to-ion mappings,
and initial chains. Existing sched_jones.py, JONES, checker logic, and schedule
archives are unchanged.

| d | Last start (µs) | Makespan (µs) | timed.check, JONES | Memory-Z source/executed | Memory-X source/executed |
|---|---:|---:|---|---|---|
| 2 | **3830** | 3880 | PASS, 0 violations | 2 / 2, PASS | 2 / 2, PASS |
| 3 | **3830** | 3880 | PASS, 0 violations | 3 / 3, PASS | 3 / 3, PASS |
| 6 | **3830** | 3880 | PASS, 0 violations | 6 / 6, PASS | 6 / 6, PASS |
| 12 | **3830** | 3880 | PASS, 0 violations | 12 / 12, PASS | 12 / 12, PASS |

timed.check uses JONES with strict durations and the original CX circuit.
JONES permits commuting runs, so build also checks a copy of that profile with
circuit_order="strict". tools/order.py confirms the executed order matches
the source on every qubit, deterministic detectors, and RESULT: PASS in both
bases. Distances are bounded-search results, not proofs.

Scheduling starts with the source reset/H prefix (60 µs), then four Stim
steps of 600, 1080, 1080, and 600 µs, followed by each qubit's source suffix
independently at home (at most 460 µs). The total makespan is 3880 µs; the
final reset starts at 3830 µs.

Within each step, each ancilla takes one contiguous 270-µs outbound route,
executes the 60-µs native CX sequence, and takes a contiguous 270-µs return.
Routes reserve the declared Jones component locks and segment/junction
residency before starting. Middle steps give X-check ancillas priority before
Z-check ancillas in the reservation list, without a time barrier between the
groups. Gates start at each visitor's own arrival; returns start after that
visitor's gate as soon as its resources are free. Every visitor returns home
before the next Stim step. No waveform classes are declared and broadcast is
correctly skipped under JONES. The middle steps overlap gates with other
ancillas' transport, saving 120 µs each relative to WISE split.

This is a simple schedule, not an optimality claim: step barriers, return-home
routing, contiguous routes, and deterministic reservation priorities remain.
All native times and locks match the existing Jones model; junction entry
and exit are 50 µs each. No waveform overhead or swaps are added. Logical
measurement-record identities use the source circuit, as in the existing
standard and WISE checks. Memory-X tests substitute its source prefix/suffix
around the schedule's identical CX list; the saved archives compile memory-Z.

Reproduce from the QCCD repository root:

```sh
for d in 2 3 6 12; do
  .venv/bin/python -m qccd.repro.sched_jones_ordered --distance "$d" || exit 1
  for basis in z x; do
    .venv/bin/python tools/order.py "Reproduce/jones2025/ours/grid_rot_d${d}_c2_ordered.schedule.json.gz" "Reproduce/jones2025/circuits/rotated_memory_${basis}_d${d}_r1.stim" || exit 1
  done
done
.venv/bin/python -m pytest tests/test_repro_jones_ordered.py -q
```

Five regression tests check all distances and both bases, actual asynchronous
transport/gate overlap, home returns, and planted duration/lock/order faults.
All raw outputs are in `jones_ordered_validation.json`. The comparison is
updated in `wise_results.md`. Nothing was pushed.
