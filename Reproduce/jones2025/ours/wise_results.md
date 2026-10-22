# WISE result: source-order split passes both logical bases

**WISE split is the valid result: last start 4070 µs at d=2, 3, 6, and 12.**
All schedules retain their capacity-2 devices/mappings. timed.check passes
with JONES_WISE, strict durations, the source CX circuit, zero violations,
and broadcast enabled. Split makespan is 4120 µs.

| d | Mode | Last start (µs) | Broadcast cycles | timed.check | Memory-Z source/executed | Memory-X source/executed |
|---|---|---:|---:|---|---|---|
| 2 | split | **4070** | 72 | PASS | 2 / 2 | 2 / 2 |
| 2 | shared_shape | 2870 | 48 | PASS | 2 / 2 | **2 / 1 FAIL** |
| 3 | split | **4070** | 72 | PASS | 3 / 3 | 3 / 3 |
| 3 | shared_shape | 2870 | 48 | PASS | 3 / 3 | **3 / 2 FAIL** |
| 6 | split | **4070** | 72 | PASS | 6 / 6 | 6 / 6 |
| 6 | shared_shape | 2870 | 48 | PASS | 6 / 6 | **6 / 4 FAIL** |
| 12 | split | **4070** | 72 | PASS | 12 / 12 | 12 / 12 |
| 12 | shared_shape | 2870 | 48 | PASS | 12 / 12 | **12 / 8 FAIL** |

All detector checks are deterministic. tools/order.py reports PASS for split
in both bases and shared_shape in memory-Z, but FAIL for shared_shape in
memory-X. The X tests reconstruct RX/MX preparation/readout around the saved
CX order; archives remain unchanged.

shared_shape remains in the code but fails memory-X: a visiting order shared
by X and Z checks aligns one check type's hook errors with its logical.
Here Z-check hooks shorten the memory-X logical. No repair was attempted;
memory-Z alone was insufficient.

| Schedule, last start (µs) | d=2 | d=3 | d=6 | d=12 |
|---|---:|---:|---:|---:|
| Jones standard, paper | 4055 | 4085 | 4080 | 4085 |
| John standard | 2295 | 2335 | 2340* | 2340 |
| WISE split | **4070** | **4070** | **4070** | **4070** |
| Direct wiring, Stim order | **3830** | **3830** | **3830** | **3830** |

*John's d=6 executed memory-Z distance is **4 of 6**, so this timing comparison
does not preserve distance. Paper/John times are the requested baselines.

Direct wiring, Stim order uses JONES (no broadcast), strict durations and
an additional exact per-qubit CX-order check. All four grids pass memory-Z
and memory-X at their source distances 2, 3, 6, 12; makespan is 3880 µs.
Independent routes/gates overlap inside each Stim step, with a barrier
between steps. Raw results: [direct-wiring validation](jones_ordered_validation.json).

Distances are from the **bounded search in tools/order.py**, not proofs:
**“no logical error shorter than d found”** for passing rows. Detection-event
set size and edge degree are capped at 4; noiseless sampling uses 500 shots.

Assumptions: **zone opt-out** (idle zones hold still), **no waveform switching
cost**, **separate j_enter/j_exit classes**, and **Jones Table 1 timings with
50 µs entry/exit**. Ancillas return home after each gate; native CX and
exclusion rules are unchanged.

The noise fix adds DEPOLARIZE1 after RX and Z_ERROR before MX/MRX. All 19
existing Jones memory-Z archives have identical before/after output and exit
codes. Generated X sources were checked for identical Z-source CX layers.

Commands and all 16 results: [basis validation](wise_both_bases_validation.json).
Z regressions: [before/after output](order_noise_z_regression.json).
Earlier reports, schedules, Z sources, catalog, profiles, and timed.py remain
unchanged. Nothing was pushed.

The shared_shape experiment was removed because it fails strict per-qubit order and memory-X.
