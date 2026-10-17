# Jones & Murali WISE artifact replay

Ran the unchanged local PartIIProject clone at
`d235a22c4a6b18549e9c1afb3adf5141477ecf80`, with `MPLBACKEND=Agg`,
`PYTHONHASHSEED=0`, and numpy seed 0. The `wise` recipe calls
`process_circuit_wise_arch(distance=d, capacity=2, gate_improvements=[], num_shots=1)`.
An empty improvements list skips the simulation/decoding loop, as in the
existing grid reproduction. Observers capture ionRoutingWISEArch inputs/outputs
and global-operation before/after states; no artifact algorithm was modified.

## Tool numbers and replay

| d | Tool last start (µs) | Replay last start (µs) | Tool / replay operations | Makespan (µs) | Physical/duration checks | Source circuit check |
|---|---:|---:|---|---:|---|---|
| 2 | 14280 | **14280** | 69 / 69 | 14680 | PASS | FAIL, one order violation |
| 3 | 19975 | **19975** | 182 / 182 | 20025 | PASS | FAIL, one order violation |
| 6 | 30270 | **30270** | 853 / 853 | 30320 | PASS | FAIL, one order violation |

The new catalog runs report **exact** tool last-start comparisons. Differences
of roughly 1e-12 µs arise only from float-to-microsecond conversion. The tool's
ElapsedTime fields and its printed last-start values agree with replay.
Every operation is retained: respectively 65, 178, and 849 qubit operations,
plus four GlobalReconfigurations per run. Compilation took 0.0, 0.0, and 0.1 s
after import/setup; d=6 was comfortably within the requested 30-minute limit.

The WISE architecture parameters recorded from the tool are (m,n,k) = (3,3,2),
(5,5,2), and (9,9,2). The dumped global-event durations are 3055, 4295, and
6775 µs respectively. Four such operations contribute 12220, 17180, and
27100 µs to their round times. The d=3 4295-µs cost is thus present in the
artifact's timed global operations, not introduced by our checker.

These are the artifact's WISE devices: 4, 9, and 36 capacity-2 manipulation
traps, with global permutations and no explicit segments/junctions. They are
not the same devices or explicit primitive schedules as the earlier WISE
grid experiments in sched_wise.py. Those schedules and reports are unchanged.

## Circuit/order results

All six tools/order.py invocations exit 1 and print RESULT: FAIL. The source
circuits have deterministic detectors and searched distances 2, 3, and 6.
The executed circuits have nondeterministic detectors even without noise:

| d | Memory-Z fired detector IDs | Memory-X fired detector IDs | Executed distance |
|---|---|---|---|
| 2 | 0, 1 | 1, 3 | Not searched: detector failure |
| 3 | 3, 7 | 2, 6 | Not searched: detector failure |
| 6 | 4, 9, 15, 16, 21, 26, 32, 33 | 3, 10, 15, 16, 21, 28, 33, 34 | Not searched: detector failure |

Changed per-qubit CX sequences are reported on qubits 6,12 (d=2), 5,13 (d=3),
and 24,38,50,64,66,70,80,84 (d=6). The core circuit check reports one violation
per run summarizing role-order errors on 1, 1, and 4 ions respectively. No
ordering was repaired or checker rule relaxed. The exact time reproduction
does not certify a valid source-code round. This agrees with the concern
in the existing Jones notes about data-qubit gate order; it is now observed
on the artifact's WISE path as well.

## What the lumped replay checks

import_jones translates each GlobalReconfigurations into one `reconfigure`
event carrying the recorded input/output chains and all affected ion/trap
locks. Replay checks that the input equals its current state, ions and traps
are conserved, ion IDs are unique, output capacity is respected, and no held
ion/trap overlaps another event. The chain permutation takes effect at the
operation's end. Duration is read from the artifact's dumped `operationTime`
and checked against its start/end timestamps; the formula is not copied or
independently reimplemented. Tests plant bad input chains, lost ions, excess
capacity, duration changes, and concurrent held-ion/trap operations.

Internal shuttle paths and waveform classes are absent from the artifact's
global events. Accordingly these runs use JONES, not JONES_WISE; broadcast is
explicitly skipped, and no claim is made to verify internal WISE geometry or
the formula's physical tightness. No PartIIProject source code is included.

`.venv/bin/python -m qccd.repro check jones2025` was run and exits 1: all new
WISE timing comparisons are exact, but their circuit checks fail, along with
the pre-existing grid circuit failures. Existing run definitions and result
rows (including stored runtime fields) were preserved; only three new catalog
and result entries were appended. A standard-recipe smoke run gives the exact
same d=2 event stream, device, and initial layout (4055 µs, 161 operations).
The clone's pre-existing log/cache status
was unchanged. No existing schedules were overwritten and nothing was pushed.

## Reproduce

From the QCCD root, point `artifact_repo` at the existing clone and use its
dependency environment. Output goes outside the clone, and bytecode writes
are disabled:

```sh
artifact_repo=/Users/jaewonkim/Documents/Jaewon_folder/Berkeley/Masters/qecc/PartIIProject
for d in 2 3 6; do
  MPLBACKEND=Agg PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/jones-wise-mode-mpl "$artifact_repo/.venv/bin/python" Reproduce/jones2025/scripts/run_jones.py --repo "$artifact_repo" --out "/tmp/jones-wise-mode-d$d" --name "wise_rot_d${d}_c2" --recipe wise --distance "$d" --capacity 2 --commit d235a22c4a6b18549e9c1afb3adf5141477ecf80
done
.venv/bin/python -m qccd.repro check jones2025
for d in 2 3 6; do
  for basis in z x; do
    .venv/bin/python tools/order.py "Reproduce/jones2025/artifact/wise_rot_d${d}_c2.schedule.json.gz" "Reproduce/jones2025/circuits/rotated_memory_${basis}_d${d}_r1.stim"
  done
done
.venv/bin/python -m pytest tests/test_repro_jones_wise_artifact.py tests/test_repro_timed.py -q
```

The driver writes plain schedule.json into each output directory; the saved
artifact archives are gzip-compressed JSON copies of those dumps. Source
circuits are the existing noiseless memory-Z circuits (the generated CX lists
match). Order checks also use the existing memory-X sources.
Raw replay summaries and all order outputs are in
[wise_artifact_validation.json](wise_artifact_validation.json). The combined
artifact, timed-checker, existing scheduler, geometry, and noise tests pass
**163 tests**.
