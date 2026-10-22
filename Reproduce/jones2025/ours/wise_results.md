# Broadcast control on Jones's junction grid

The split schedule and John's current Ours both preserve exact per-qubit Stim
CNOT order, with the same device, ion mapping, native operations and Jones times.
Both retain full searched distance in memory-Z and memory-X at d=2,3,6,12;
all source/executed detectors are deterministic. WISE split adds broadcast
control on Jones's junction grid, with idle zones opting out through per-site
switches. It is not a different trapping architecture.

| d | John's current Ours, last start (µs) | WISE split, last start (µs) | Memory-Z source/executed, both | Memory-X source/executed, both |
|---|---:|---:|---|---|
| 2 | 2850 | 4070 | 2 / 2 | 2 / 2 |
| 3 | 3010 | 4070 | 3 / 3 | 3 / 3 |
| 6 | 3085 | 4070 | 6 / 6 | 6 / 6 |
| 12 | 3070 | 4070 | 12 / 12 | 12 / 12 |

The 6% figure applied only against a return-home direct schedule, now superseded
by John's current Ours. The shared_shape experiment was removed because it
fails strict per-qubit CNOT order and memory-X.

WISE split has 72 broadcast cycles, 3240 µs of transport, and 4120 µs makespan
at every distance. Transport per Stim step is 540,1080,1080,540 µs, including
returns home; in the middle steps X checks run before Z checks. The four
rotations and MS of each native CX take 60 µs per parallel pass. Last start,
not makespan, matches the Reproduce page's measure.

Distances are the bounded search in tools/order.py: “no logical error shorter
than d found” within that search, not proofs. The tool prints
`circuit distance <= N` and checks 500 noiseless shots.

Assumptions: Jones Table 1 durations (50 µs junction entry and 50 µs exit),
separate j_enter/j_exit waveform classes, no waveform switching cost, and
per-site switches allowing idle zones to hold still. Junction exclusion R2
still applies. The rule checks below make the broadcast/opt-out assumptions
explicit rather than treating class metadata as geometric evidence.

Reproduce from the QCCD root:

```sh
for d in 2 3 6 12; do
  .venv/bin/python -m qccd.repro.sched_wise --distance "$d"
  for kind in ours wise; do
    for basis in z x; do
      .venv/bin/python tools/order.py "Reproduce/jones2025/ours/grid_rot_d${d}_c2_${kind}.schedule.json.gz" "Reproduce/jones2025/circuits/rotated_memory_${basis}_d${d}_r1.stim"
    done
  done
 done
.venv/bin/python -m qccd.repro check jones2025
```
