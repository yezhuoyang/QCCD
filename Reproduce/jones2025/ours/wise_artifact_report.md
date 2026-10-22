# Jones & Murali WISE artifact replay

The unchanged PartIIProject artifact at `d235a22c4a6b18549e9c1afb3adf5141477ecf80`
was observed through `process_circuit_wise_arch(distance=d, capacity=2,
gate_improvements=[], num_shots=1)`, with `MPLBACKEND=Agg`, `PYTHONHASHSEED=0`
and numpy seed 0. No artifact implementation or pricing formula is included here.
These opaque global permutations are distinct from broadcast control on Jones's
junction grid in `sched_wise.py`.

| d | Tool / replay last start (µs) | Makespan (µs) | Tool / replay ops | Timed checker | Rule set |
|---|---:|---:|---:|---|---|
| 2 | 14,280 / 14,280 (exact) | 14,680 | 69 / 69 | FAIL: CNOT order only | FAIL: R26 only |
| 3 | 19,975 / 19,975 (exact) | 20,025 | 182 / 182 | FAIL: CNOT order only | FAIL: R26 only |
| 6 | 30,270 / 30,270 (exact) | 30,320 | 853 / 853 | FAIL: CNOT order only | FAIL: R26 only |

Each run has four global events, recorded as 3,055, 4,295 and 6,775 µs per
event respectively. **Global durations are recorded, not checked.** Ordinary
operation durations are checked against Jones's timings. `JONES_WISE_ARTIFACT`
alone permits global events; JONES, JONES_WISE and QCCDSIM reject them.

## Order finding

At d=3, data qubit **5 (ion 16)** meets Z-check **13 (ion 5)** at
**4,420 µs** (event 57), before X-check **11 (ion 4)** at **13,925 µs**
(event 123). The source circuit requires the reverse order. Between them are
GlobalReconfigurations event **82**, 4,485–8,780 µs, and event **121**,
9,630–13,925 µs. The importer retains the artifact's `allOps` event/gate order;
this is an artifact ordering error, not an importer reorder.

For every d, `tools/order.py` against both memory-Z and memory-X reports FAIL:
the executed detectors are nondeterministic, so executed distance is not
searched. Exact timing reproduction therefore does not certify a valid round.

## Scope of checking

Both checkers verify conservation and unique ion IDs, recorded input chains,
capacity after the permutation, positions changing at its end, and exclusion
on held ions and the **entire device** (every trap, segment and junction).
All rule verdicts except R26 hold: A1, R0, R1, R2, R3, R6b, R8, R14, R24,
R27, R29 and R30. R29 checks ordinary operations only; the rule result's
`recorded_checks` field explicitly excludes global durations and paths.

A global event must take finite positive time. A test moving an ion between
two disconnected traps in 0 µs is **rejected for nonpositive duration**.
The dumps supply no internal routes or topology: routing feasibility,
waveforms, and the global pricing formula are **not checked**, never passed.
A positive recorded permutation alone provides no certificate of a physical
route. The tests also reject a concurrent junction crossing by an untouched
ion; the whole-device hold does not invent extra ion occupancy.

The all-paper before/after audit found **no ok/BAD changes in any of the 97 rows**
(77 ok / 20 BAD in both runs). Both commands exit 1 for the existing failures.
The requested suites plus noise tests pass: 84 passed, 48 expected xfails
for the existing geometry-label gap. Only the three WISE artifact result rows
are updated; unrelated runtime fields are preserved.

From the repository root:

```sh
.venv/bin/python -m qccd.repro check
for d in 2 3 6; do
  for basis in z x; do
    .venv/bin/python tools/order.py "Reproduce/jones2025/artifact/wise_rot_d${d}_c2.schedule.json.gz" "Reproduce/jones2025/circuits/rotated_memory_${basis}_d${d}_r1.stim"
  done
done
.venv/bin/python -m pytest tests/test_repro_jones_wise_artifact.py tests/test_repro_timed.py tests/test_ruleset.py tests/test_repro_wise.py tests/test_wise_geometry.py tests/test_wise_memory_x.py -q
```
