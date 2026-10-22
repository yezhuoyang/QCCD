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

## Rules on grid_rot_d3_c2_wise

These are the production `keeping_order(jones-wise)` verdicts: R25 replaces
the included Jones commuting-order rule R26 for our schedules.

| Rule | Verdict |
|---|---|
| R0 positions / well-formedness | holds |
| R1 site capacity | holds |
| R2 junction exclusion | holds |
| R3 rail capacity | holds |
| R4b programme control mode | does not apply (no programme steps) |
| R4d programme channel/opt-out | does not apply (no programme steps) |
| R6b gate locality | holds |
| R8 ion exclusion | holds |
| R14 chain end / paid swap | holds |
| R22 one waveform | holds |
| R24 circuit gates | holds |
| R25 strict per-qubit order | holds |
| R27 trap exclusion | holds |
| R29 durations | holds |
| R30 declared component locks | holds |
| W1 geometry, labels and lockstep | holds |
| W2 timed R4b counterpart | holds |
| W3 timed R4d counterpart | holds |

R4b and R4d themselves quantify programme steps and explicitly do not apply
to timed schedules. W2 enforces their intra/inter transport separation and
exclusion of simultaneous gates, resets, measurements and cooling. W3 checks
every idle trap for a per-site switch; W1 supplies R4d's common-waveform part.
R22 evaluates on schedules; its legacy `motion` still reads the label. W1
compares a separate `displacement` fact derived from device coordinates and
requires the label to agree, so uniformly lying labels are rejected as well.

Each WISE archive now declares `broadcast_switch: true` and
`control_model: broadcast` on every trap, including empty traps. This is an
explicit added control assumption, not a change in trap geometry, capacity,
mapping, initial chains, event timing or gate order. A direct-wiring schedule,
a move delayed by 5 µs, a uniformly mislabelled cycle, a missing idle-site
switch, a gate concurrent with transport, and double junction occupancy are
all rejected by the rule tests. The older timed checker still trusts class
labels; its geometry-gap tests remain strict expected failures.

## Separate box for the website

The smallest added catalog shape is `OURS["jones2025"]["boxes"]`, a list
of independent boxes with `id`, `title`, `how`, `found`, and `runs`.
The existing parent `runs` now contains only John's four Ours schedules.
The one new box has `id: "wise"` and `title: "Ours, WISE control"`, its own
explanation/results text, and the four split schedules.

`check_paper` keeps the existing four result objects in `results.ours` and
emits the extra boxes as `results.ours_boxes`. Each box preserves its four
metadata fields and has `count: 4` and `runs`, containing the same full
checked result objects (including per-rule verdicts) as before. The renderer
can keep its current Ours box/count from `ours`, then iterate `ours_boxes`
to render each box's own title, how/found text, count, and runs. Nothing is
double-counted. No site-renderer changes are made on this branch.

An Ours row is only `ok` if both its strict timed profile and its rule set
pass; geometry/label failures cannot be hidden by the timed checker's gap.
The final reproduction check has all four `_ours` and four `_wise` rows ok;
it still exits 1 for the paper artifact's four pre-existing circuit failures.
