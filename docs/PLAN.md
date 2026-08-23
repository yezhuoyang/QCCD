# QCCD Compiler & Architecture-Exploration Platform — Design Plan

Status: draft **v2** · 2026-08-20 · supersedes v1 (2026-08-19)
Scope: turns `README.md` steps 1–7 into a buildable spec, grounded in `ion_transport_deck_v3.pptx.pdf`,
a numerical audit of `visualizer_24_ancillas_24_junctions_standalone.html`, and a reading pass over
the `Library/` corpus (78 arXiv seeds, 76 with source; 4 papers mined, 8 skimmed).

Every literature fact below is traceable: `python Knowledge/kg/query.py <query>`. See §12.

> **What changed from v1.** Four of v1's load-bearing assumptions were wrong, and the literature
> supplies a sharper question than the one the README asks. In short: (a) `cost` and `steps` are not
> rival objectives, they are two named halves of one error budget; (b) transport carries *no direct
> gate infidelity* — it heats, and heating degrades the *next* gate, which makes cooling a
> first-class scheduling resource the current design omits entirely; (c) the parallelism rule has a
> published, finite formalization (JT-SIMD) that v1's abstract symmetry group only approximated; and
> (d) the project's real thesis is not "which architecture is best" but "does rigid rotation cancel
> WISE's 25× control-serialization penalty". §1.

---

## 0. Findings

### 0.1 The audit of the shipped 24-ancilla schedule — unchanged, and now a golden test

Extracted `INLINE_DATA` (line 344 of the standalone HTML) and recomputed every figure from the
396-operation list. Everything reproduces exactly.

```
geometry      W72_H2 ring, capacity 144, 0 empty slots, 24 docks at {0,6,…,138}
schedule      396 batch-ops, 864 contacts, 144 checks × 6 members  ✓ complete
total_cost    397 184  =  2 672 rotate hops × 148  +  864 contacts × 2
total_steps     8 808  =  2 672 hops × 3 steps/hop  +  792
```

| Diagnostic | Value | Why it matters |
|---|---|---|
| Cost that is pure rotation | **99.6 %** | Docking is free in the deck's model. §0.3 shows it is the opposite in physics. |
| Cost per rigid hop | **148** ≈ `N_ions` | Rigid rotation touches every ion on every hop: O(N) transport events per hop. |
| Steps per rigid hop | **3** | The deck attributes this to the 4 corners. §0.5 shows corners are free and the real per-hop tax is the 24 in-path junctions. |
| Contact-batch utilization | **2.18 / 24 = 9.1 %** | ~11× parallelism unclaimed against the design's own limit. |
| Relaxed lower bound on hops | **480** vs 2 672 → **≥ 5.6×** | Optimistic (drops fixed-ancilla and reuse rules) but the gap is large. |
| vs `ladder_2x72_baseline` | steps **2.07×** better, cost **12.0×** worse | v1 read this as a Pareto tradeoff. §0.2 shows it is not. |

### 0.2 `cost` and `steps` are two halves of one objective, not two objectives

v1 concluded that no single number can rank architectures. That was an artifact of leaving the
weights unspecified. Jones & Murali optimize precisely these two quantities and name the physical
error each one proxies:

> "we optimise two proxy metrics: overall circuit runtime (primary) and number of routing operations
> (secondary) which serve as effective heuristics for minimising the dominant drivers of error,
> **idling noise and ion heating**, respectively." — arXiv:2510.23519

So `steps` ∝ runtime → idling noise; `cost` ∝ routing-operation count → heating. The weights are
hardware constants (`T_coh`, the per-primitive quanta), not a designer's choice. There is one
objective. `Knowledge: fd_cost_and_steps_are_one_objective`, `fd_objective_proxies_confirmed`.

### 0.3 Transport does not cause gate error. It causes heating, which causes gate error.

This is the correction that matters most, and neither of v1's two candidate models was right.

> "The reconfiguration steps (t7–t11) **do not directly cause gate infidelity**; however, they
> introduce idling noise and **increase subsequent gate error rates due to heating**, quantified
> using the mean vibrational energy n̄." — arXiv:2510.23519

The canonical primitive table, with a heating budget attached to every operation:

| primitive | time | heating |
|---|---:|---:|
| (t7) ion shuttling, one segment | 5 µs | n̄ < **0.1** |
| (t8–t9) **split and merge** | 80 µs | n̄ < **6** |
| (t10–t11) junction entry/exit | 100 µs | n̄ < **3** |
| (t6) qubit reset | 50 µs | 5 × 10⁻³ error |

Three consequences:

1. **Split/merge dominates heating** — 60× a shuttle, 16× in time. The deck charges merge the same
   as a move (p. 14). That understates the dominant term of every dock/undock.
2. **Heating does not add linearly.** The composition law carries a phase-dependent interference
   term, `n̄_tot = n̄_hom + n̄_inhom + 2√(n̄_hom·n̄_inhom)·cos θ`, which can be **negative** —
   a phase-aware schedule can cancel excitation (arXiv:2605.25118). The additive sum is an upper bound.
3. **Primitive duration is a decision variable, not a constant.** Measured: merge/split 30 µs at
   n̄ ≈ 1 versus 40 µs at n̄ ≈ 0.1; swap 18 vs 20 µs; single-ion transport 12 vs 14 µs. Slowing a
   primitive buys quanta. Closed-loop waveform optimization reaches 0.36 ± 0.08 quanta for a fast
   multi-electrode round trip (arXiv:2201.07358). And **idle time heats too**, via anomalous
   heating `ṅ̄ = S_E(ω)e²/4mℏω`, which dominates on long timescales and in the radial modes.

`Knowledge: fd_transport_model_resolved`, rules R15–R17.

### 0.4 The shipped schedule's heating budget is over by ~400×, and it contains no cooling at all

Applying the table above to the audited schedule:

```
rotation    2672 hops × 0.1 quanta                = 267.2 quanta per data ion
junctions   445 transits × 3 quanta               = 1336.0  ← dominates, see §0.5
docking     6 contacts × 4 split/merge × 6 quanta = 144.0
                                            TOTAL ≈ 1747   quanta per data ion per ESM round
                                    MS gate budget ≈ 1–2   quanta
```

The 445 figure is `2672 hops / 144 slots = 18.56 revolutions × 24 vertical lines`: every rotating
ion crosses every vertical, every revolution. Measured, an ion survives only **~85 uncooled
junction round trips** before it is *lost* (arXiv:1210.3655) — so this is 5.2× past an ion-loss
limit, not merely a fidelity limit.

The schedule has **zero** cooling operations. Cooling is not a refinement of this design; it is a
missing first-order component, of order 1700 quanta to remove per data ion per round. And this is
not a small correction to the runtime either:

> "In trapped-ion systems that rely heavily on transport of ions, **transport and cooling consume a
> majority of execution time**, and **up to 50 % of the ions are used only for cooling**."
> — arXiv:2606.06455, citing the H2 race track and Helios

So the real ion inventory for 144 data + 24 ancilla is up to ~336 ions once coolants are counted,
and WISE-style wiring cannot pass a logical error rate of 10⁻⁴ *at all* without cooling support
(arXiv:2510.23519). Cyclone's assumption that "sympathetic cooling after every gate mitigates most
heat accumulation" is the single most load-bearing unmodelled term in the literature we are building
on. `Knowledge: fd_heating_budget_blows_up`, `fd_cooling_is_the_dominant_term`, rule R7c.

### 0.5 A corner is not a junction — the verticals are

The deck charges a ring corner **3 primitive hops** and a hop takes the maximum depth of any moving
ion, which is where the audited schedule's 3× step inflation comes from. That is a modelling
artifact. The literature definition is unambiguous:

> "Shuttling through junctions, **where three or more linear trap axes join**, is greatly
> complicated by the presence of **rf barriers** leading into the junction." — arXiv:quant-ph/0702175

The barrier "results from unbalanced fields produced by the electrodes across the junction". A
two-arm bend has continuous RF rails, no unbalanced field, and therefore no barrier — and shipped
hardware confirms it: **Quantinuum's H2 race track is a single continuous RF null** (two concentric
RF electrodes circumscribing the centre, RF tunnels letting DC electrodes tile the full perimeter),
and its **curved end zones are ordinary conveyor-belt regions** driven by the same `{a,b,c}`
broadcast tiling as the straights. That paper lists junction transport as a challenge of *future*
2D traps, which H2 does not have (arXiv:2305.03828).

**So: corner cost = 1 shuttle**, plus a principal-axis rotation handled by DC shim electrodes. Set
`corner_hops = 1`, not 3.

But the corner was the wrong thing to worry about. The **24 vertical shuttling lines** each meet the
rail at a **degree-3 T-junction sitting on the rotation path**:

| | |
|---|---|
| revolutions per ESM round | `2672 / 144` = **18.56** |
| T-junction transits per data ion | `18.56 × 24` = **445** |
| measured uncooled survival limit | ~65 round trips at >98 %, **ion loss beyond ~85** (arXiv:1210.3655) |
| → | **5.2× past an ion-loss limit** |
| heating contribution | `445 × 3` = **1336 of the round's ~1747 quanta** |
| wall clock | a rigid hop costs the max over all moving ions, and with V ≥ 1 junctions on the path *every* hop crosses one: **2672 × 100 µs = 267 ms**, not 13 ms |

Measured junction transit times are not kind either: 85 round trips took ~34 ms, i.e. **~200 µs
one-way**, against a ~5 s untransported ion lifetime. And an X-junction is not something you get for
free by drawing two lines crossing — "a junction naively assembled from the intersection of two
linear sections does not provide adequate three-dimensional confinement to allow controlled
transport"; the RF electrode shape must be redesigned, and residual dielectric charging can still
leave an unmodelled barrier at the centre that transport has to route around.

**The verticals are therefore a net loss under the measured cost model.** They buy a ~6× reduction
in rotation hops and impose a 20× time and 30× heating multiplier on every remaining hop. Base
Cyclone, with ancillas **in-line on the loop** and only degree-2 corners, predicts 62–170 ms for the
same code. The README's tradeoff is real, but it resolves the other way: **put the ancillas in-line,
so the rotation path crosses no degree-3 node.** `Knowledge: fd_corner_is_not_a_junction`,
`fd_verticals_are_the_real_junction_cost`, `fd_verticals_are_net_negative`, rule R18.

### 0.6 Trap capacity has no code-independent optimum

Two P0 papers disagree, and both are probably right:

- **Capacity 2 is optimal** for surface codes — lowest elapsed time, cycle time *constant* in code
  distance, 1–2 orders of magnitude better logical error rate, fewest electrodes. Explicitly
  counter to the prior NISQ recommendation of 20–30 ions (arXiv:2510.23519).
- **Capacity 8 across 64 traps** is optimal for HGP [[225,9,6]] on a Cyclone ring (arXiv:2511.15910).

Surface-code checks are local, so parallelism dominates and small traps win; qLDPC checks are
non-local, so communication dominates and larger traps win. The deck fixes 1–2 ions per zone — the
surface-code answer — for a qLDPC target. **Capacity and ancilla count must be sweep axes.**
`Knowledge: fd_capacity_optimum_is_code_dependent`.

### 0.7 The design is ancilla-starved relative to both reference points

For BB [[144,12,12]]: IBM's reference syndrome circuit uses **n = 144 ancillas at depth 7** on
degree-6 connectivity (two edge-disjoint planar subgraphs, threshold 0.8%, arXiv:2308.07915);
base Cyclone uses **m/2 = 72**; ours uses **24**. Each of our ancillas therefore serves six checks
in sequence, which is the structural cause of the extra rotations — not a scheduling deficiency.
`Knowledge: fd_ancilla_count_vs_reference`.

### 0.8 The deck's wiring is shipped hardware; so is the ring

Quantinuum's H2 is *a linear trap with periodic boundary conditions* — a race track — running with
**electrode broadcasting**: conveyor-belt DC electrodes tied `{a,b,c,a,b,c,…}` so **three voltage
signals drive 20 wells per side**, with separate individually-driven shim electrodes for micromotion
compensation (arXiv:2305.03828). That is the deck's scheme (12 linear + 4 junction broadcast, 8
compensation behind a 1:100 demux) in one dimension. Measured there: 2Q infidelity 1.84(5) × 10⁻³,
1Q 2.5(3) × 10⁻⁵, SPAM 1.6(1) × 10⁻³, 32 qubits, QV 2¹⁶. The paper's stated next step is "truly
two-dimensional trapping structures for fast ion sorting" — the deck's array.

**Use 1.84 × 10⁻³ for two-qubit gates in any [[144,12,12]] projection**, not a 10⁻⁴ aspiration.

---

## 1. The thesis

The README asks "for BB [[144,12,12]], what is the best architecture?". The corpus supports a
sharper and falsifiable version, and the deck already chose the design that poses it (p. 11:
"⇒ WISE architecture + cyclone").

**The two halves have each been priced, separately, and never together:**

| | benefit | price | source |
|---|---|---|---|
| **WISE** broadcast wiring | >2 orders of magnitude lower control data rate and power; ~100 DACs regardless of system size | only **one movement primitive type may co-occur** → up to **25× slower** logical clock at LER 10⁻⁹ under **odd–even sort** reconfiguration | arXiv:2510.23519 |
| **Cyclone** rigid rotation | roadblock-free, ~4× faster than a grid, bounded movement, O(1) DACs | evaluated on *unconstrained* wiring; assumes free cooling | arXiv:2511.15910 |

The 25× penalty is not intrinsic to WISE. It is the cost of **odd–even transposition sort**, which
needs many distinct movement templates while WISE permits one per cycle. **A rigid lockstep rotation
needs exactly one template.**

> **Thesis.** Rotation-based reconfiguration should make WISE's serialization penalty nearly free,
> yielding `O(1)` DAC count at grid-competitive logical clock speed — provided the cooling schedule
> that WISE demands (§0.4) does not eat the gain.

That is a claim that can be *refuted*, it is what this project is uniquely positioned to test, and
it makes the whole software stack instrumental rather than an end in itself. It becomes milestone
**M4**, ahead of any general architecture sweep. `Knowledge: fd_project_thesis`, `q_rotation_beats_oddeven`.

**A second scope correction.** The README's long-term goal — "best Code + Architecture to
demonstrate breakeven" — has been overtaken. Breakeven was demonstrated on a trapped-ion device with
**no ion transport at all**: a stationary chain, steerable Raman beams for all-to-all gates, OMG
in-place mid-circuit measurement, ancillas doubling as coolants (arXiv:2606.06455). The defensible
long-term question is the **crossover**: at what code size does a transport architecture beat a
stationary chain, once transport *and* cooling are both charged? A single chain cannot hold 144 data
qubits at usable gate fidelity, so a crossover exists — finding it is the deliverable.
`Knowledge: fd_breakeven_already_done`, `q_crossover_vs_stationary`.

---

## 2. Scope

**In scope.** A Python-first platform that (a) describes an arbitrary QCCD architecture
declaratively including its control wiring, (b) compiles a QASM circuit — initially a BB-code
syndrome-extraction round — into a timed, rule-checked hardware program *with cooling scheduled*,
(c) verifies that program, (d) scores it on combinatorial, physical and logical metrics, and
(e) renders it as a self-contained HTML animation for human verification.

**Out of scope for v1.** Electrode-level waveform synthesis (we consume `(time, n̄)` curves, we do
not compute them), magic-state distillation scheduling, multi-logical-qubit lattice surgery.

**Non-negotiable constraints.**
- Every architecture in the corpus expressible with **no code changes**: grid QCCD, CYCLONE ring,
  WISE-wired grid, H2 race track, Quantinuum C2LR grid, 2×72 ladder, **and the stationary chain**.
- Every emitted program **replayable and checkable** by an independent verifier.
- Two external validation targets before any of our own numbers are believed (§9, M1 and M3).

---

## 3. Layer 1 — the architecture description language (`.arch.json`)

Modelled on ZAC's `hardware_spec/*.json`, but the zone-and-AOD geometry is replaced by a
**capacitated trap graph + an enumerated SIMD instruction-class set + tunable primitive curves**.

```jsonc
{
  "name": "wise_cyclone_144",
  "schema_version": "0.2",

  // ---- GEOMETRY: capacitated multigraph ------------------------------
  "geometry": {
    "generator": "ring",                    // grid | ring | ladder | racetrack | chain | explicit
    "params": { "width": 72, "height": 2, "verticals": 24 },
    "sites":     [ { "id": "S0", "pos": [0,0], "capacity": 2, "zone_type": "data" } ],
    "junctions": [ { "id": "J0", "pos": [1,0], "degree": 2 } ],
    "segments":  [ { "id": "E0", "ends": ["S0","J0"], "length": 1, "capacity": 1 } ]
  },

  // ---- ZONE TYPES (deck p.24) ---------------------------------------
  "zone_types": {
    "data":     { "capacity": 2, "gate": false, "spam": false, "cool": true  },
    "ancilla":  { "capacity": 2, "gate": true,  "spam": true,  "cool": true  },
    "tfactory": { "capacity": 4, "gate": true,  "spam": true,  "cool": true  },
    "load":     { "capacity": 8, "gate": false, "spam": true,  "cool": true, "photoionization": true }
  },

  // ---- CONTROL: enumerated SIMD classes (the AOD-analogue) ------------
  // A class fixes (type, direction). Participation is VARIADIC: each junction
  // or ion group may join or stay idle. This is JT-SIMD (arXiv:2504.17886).
  "control": {
    "model": "simd_classes",
    "classes": {
      "generator": "x_junction_grid",       // expands to the 18 canonical classes:
      "count": 18,                          //   12 directional shifts + 6 directional swaps
      "extra": [ { "id": "rotate_cw", "type": "shift", "orbit": "whole_ring" } ]
    },
    "max_simd_classes_per_cycle": 1,        // WISE = 1; C2LR = 4; WISE-with-banks = k
    "intra_inter_exclusive": true,          // R4b: distinct control pathways
    "wiring": {
      "scheme": "wise",                     // wise | direct | broadcast_groups
      "dacs_dynamic": 100,                  // ~constant in N under WISE
      "shim_per_dac": 100,                  // 1:100 demux on compensation electrodes
      "electrodes_per_trap": 24, "electrodes_per_junction": 48
    },
    "optical": { "addressing": "global_beam", "per_zone_switch": true }
  },

  // ---- PRIMITIVES: (duration, quanta) CURVES, not constants ----------
  // The compiler picks an operating point per instance. See §5.
  "primitives": {
    "shuttle_segment": { "curve": [ {"us": 5,  "quanta": 0.10},
                                    {"us": 12, "quanta": 1.0 },
                                    {"us": 14, "quanta": 0.10} ] },
    "junction_cross":  { "curve_by_degree": { "2": [{"us": 10,  "quanta": 0.5}],
                                              "3": [{"us": 100, "quanta": 3.0}],
                                              "4": [{"us": 120, "quanta": 3.0}] } },
    "split":  { "curve": [ {"us": 30, "quanta": 1.0}, {"us": 40, "quanta": 0.1},
                           {"us": 80, "quanta": 6.0} ] },
    "merge":  { "curve": [ {"us": 30, "quanta": 1.0}, {"us": 40, "quanta": 0.1},
                           {"us": 80, "quanta": 6.0} ] },
    "ion_swap":  { "curve": [ {"us": 18, "quanta": 1.0}, {"us": 20, "quanta": 0.1} ] },
    "gate_swap": { "gates": 3 },            // 3 CX; distance-independent
    "ms_gate":   { "us": 25, "fidelity_at_n0": 0.99816, "error_vs_quanta": "linear:2.0e-3" },
    "1q_gate":   { "us": 5,  "fidelity": 0.999975 },
    "measure":   { "us": 120, "fidelity": 0.9984 },
    "reset":     { "us": 50,  "error": 5e-3 },
    "cool":      { "us": 300, "removes_quanta": "all", "broadcastable": true }
  },

  // ---- HEATING: the background term (R17) ---------------------------
  "heating": { "anomalous_rate_quanta_per_ms": 0.05, "note": "S_E(w)e^2/4mhw; device-specific, measure it" },

  // ---- SPECIES & INVENTORY ------------------------------------------
  "species": { "qubit": "Ba+", "coolant": "Sr+", "sympathetic": true,
               "coolant_fraction": 0.5, "T_coh_s": 600 },

  "budget": { "max_dacs": 128, "max_junctions": 200, "max_area_mm2": 100,
              "junction_electrode_multiplier": 2 }
}
```

### 3.1 Why an enumerated class set rather than v1's symmetry group

v1 proposed: *two actions may share a step iff they lie in the same orbit of the broadcast wiring
group*. That is correct but not implementable as a runtime check. The published form is finite:
**18 JT-SIMD classes — 12 directional shifts + 6 directional swaps** — each fixing a type and a
global direction, with **variadic participation** (each junction may opt out). v1 missed the opt-out
and missed intra/inter mutual exclusion entirely. The symmetry group survives as the *generator* of
the class list, not as the check. `Knowledge: d_simd_isa`, `fd_simd_is_the_published_form`.

### 3.2 Why primitives carry curves

Because slowing a transport buys quanta, and the compiler must be allowed to make that trade. The
corpus gives two incompatible primitive tables — QCCDSim/Jones (shuttle 5 µs / 0.1 q, split-merge
80 µs / 6 q) and the transport-excitation framework (split-merge 30–40 µs at n̄ 1→0.1) differing by
2–3× in time. Rather than pick one, the spec holds both as points on a curve, and **the sweep runs
against both tables and reports whether the architecture ranking is stable**. Ranking stability
under parameter uncertainty is itself a result worth publishing. `Knowledge: q_heating_rate_measurement`.

### 3.3 Capacity and ancilla count are sweep axes

Minimum capacity set `{1, 2, 4, 8, 15}`; minimum ancilla set `{24, 72, 144}` for BB [[144,12,12]].
Gate time degrades sharply above ~15 ions/trap, which bounds the top of the range.
`Knowledge: d_capacity_is_swept`.

---

## 4. Layer 2 — the control IR (`TSIR`, `.tsir.json`)

Same shape as ZAC's ZAIR, but **event-scheduled, not barrier-scheduled**. v1 made `par_step` the
only concurrency construct with one `t0/t1` per step; that is depth-oriented scheduling, and it
forfeits measured wins — operation latencies span 5–500 µs, and time-sliced synchronization that
tracks actual completion times took a worked example from 823 µs to 545 µs (arXiv:2504.17886).

```jsonc
{
  "name": "bb144_esm_round0",
  "arch_spec": "arch/wise_cyclone_144.arch.json",
  "instructions": [
    { "type": "init", "id": 0, "t0": 0, "t1": 0,
      "placement": { "d0": "S0", "a0": "A0" }, "quanta": { "d0": 0.0 } },

    // one SIMD cycle: a class + the variadic participant set
    { "type": "simd", "id": 1, "t0": 0, "t1": 5,
      "class": "rotate_cw", "mode": "inter",
      "participants": [ { "ion": "d17", "from": "S3", "to": "S4", "via": ["E12"] } ],
      "holds": ["E12", "J4"],                  // resources occupied over [t0,t1)
      "quanta_delta": { "d17": 0.10 },
      "operating_point": { "us": 5, "quanta": 0.10 } },

    { "type": "gate", "id": 2, "t0": 5, "t1": 30, "gate": "MS",
      "ions": ["d17","a3"], "site": "A3",
      "quanta_at_gate": { "d17": 0.10, "a3": 0.30 },
      "error": 1.84e-3 },

    { "type": "cool", "id": 3, "t0": 30, "t1": 330,
      "ions": ["d17","a3"], "broadcast": true, "quanta_after": 0.0 },

    { "type": "measure", "id": 4, "ions": ["a3"], "site": "A3" }
  ],
  "metrics": { "total_cost": 397184, "total_steps": 8808,
               "runtime_us": 3.3e5, "cooling_us": 8.1e4,
               "peak_quanta": 1.7, "coolant_ions": 168 }
}
```

Design notes.
- **Instructions overlap freely.** Legality is checked by interval overlap against the declared
  `holds` resource set plus the SIMD-class and mode rules — not by a barrier. `Knowledge: d_async_ir`.
- **`mode: intra | inter`** makes R4b (mutual exclusion of intra- and inter-trap transport) a
  one-line check.
- **`quanta_delta` / `quanta_at_gate`** make the heating model auditable per instruction, and let a
  reader see exactly which gate a heating violation belongs to.
- **`operating_point`** records which point on the primitive's curve the compiler chose.
- **Two backends emit TSIR** — a hand-written importer (to bring in the existing schedule) and the
  compiler. Both are checked by the same verifier.

---

## 5. Layer 3 — the rules, as machine-checkable invariants

Every rule traces to a source: `python Knowledge/kg/query.py rules`.

| # | Invariant | Source |
|---|---|---|
| **R1** | `occupancy(site) ≤ site.capacity`; exceeding it triggers a scheduled *rebalance* | deck, 2004.04706, 2511.15910 |
| **R2** | ≤ 1 ion on any junction at any instant | deck, 2510.23519 |
| **R3** | ≤ `segment.capacity` (=1) ions on any segment | deck, 2510.23519 |
| **R4** | ≤ `max_simd_classes_per_cycle` classes active; a class fixes (type, direction); participation is variadic | deck, 2504.17886 |
| **R4b** | intra-trap and inter-trap transport never overlap in time — distinct control pathways | 2504.17886 |
| **R5** | no two ions exchange positions along one segment in a single step | deck |
| **R6** | gate / measure / cool only where the zone type has the capability | deck, 2504.17886 |
| **R6b** | a 2Q gate acts only on ions **co-located in the same gate zone** | 2504.17886 |
| **R7** | a 2Q gate requires both ions' `n̄ ≤ ms_gate.max_quanta` | deck, 2511.15910 |
| **R7b** | per-gate-zone thermal duty-cycle budget, not just instantaneous occupancy | 2504.17886 |
| **R7c** | **cooling is mandatory** under broadcast wiring — without it LER cannot pass 10⁻⁴ | 2510.23519, 2606.06455 |
| **R8** | the ion→site map is a bijection over time outside explicit load/unload | deck |
| **R9** | claimed steps/cost/duration/quanta equal the replayed values | self-consistency |
| **R10** | the compiled program implements the input circuit (interaction multiset + dependency order) | correctness |
| **R11** | shuttling is unidirectional; a trap connects to ≤ 2 shuttling paths | 2511.15910 |
| **R12** | intra-trap parallelism = 1; inter-trap parallelism unconstrained | 2511.15910, 2004.04706 |
| **R13** | 2Q gate time degrades sharply above ~15 ions per trap | 2511.15910 |
| **R14** | an ion must be at a **trap edge to split**; getting there costs an intra-trap swap = 3 CX | 2510.23519 |
| **R15** | quanta compose as `n̄_hom + n̄_inhom + 2√(n̄_hom n̄_inhom) cos θ`, not additively | 2605.25118 |
| **R16** | 2Q gate error is a **function of accumulated n̄** at gate time, not a constant | 2510.23519, 2605.25118 |
| **R17** | anomalous heating accrues with elapsed time whether or not an ion moves | 2605.25118 |

R1–R9, R11–R14, R16–R17 are cheap replay checks. R15 is checkable only up to the phase model.
**R10 needs real work**: symbolic tracking (permutation + Pauli frame vs the QASM DAG, as ZAC's
`verify_scheduling` does) plus a stabilizer differential test against `stim` on small instances.

---

## 6. Layer 4 — the objective

One scalar, three tiers of fidelity to it.

**T1 — combinatorial (µs to evaluate).** `total_steps`, `total_cost`, rotate hops, batch
utilization, junction contention, **cooling-op count**, **coolant-ion count**. Used inside
optimization loops.

**T2 — physical (ms).** Replay with the primitive curves and the anomalous-heating rate → per-ion
`n̄(t)`, per-ion idle time, wall-clock µs, cooling time. Then

```
−ln F  ≈  Σ_gates ε(n̄_at_gate)  +  n · T_exe / T_coh  +  Σ_spam ε_spam
```

with `ε(n̄)` from R16. Hardware cost enters here too: DACs, switches (`48N`), electrodes
(junctions ×2 vs traps), area, power, controller data rate.

**T3 — logical (s to min).** Emit a `stim` circuit for the ESM round with the T2 parameters attached
to the *actual* schedule; decode with BP-OSD (`ldpc`) for BB codes; extract the logical error rate
per round. `stim`, `pymatching` and `ldpc` are installed and are themselves C++-backed, so the
heaviest tier needs no custom fast code.

`Knowledge: d_single_objective`, `d_quanta_cost_model`.

---

## 7. Layer 5 — the compilation pipeline

```
QASM ─parse─▶ circuit DAG ─▶ [ code layer: BB / HGP / surface ]
                                     │
                    ┌────────────────┴────────────────┐
                    │ 1. PLACEMENT                     │  ion → initial site
                    │ 2. INTERACTION ORDER             │  stabilizer→ancilla, colouring
                    │ 3. ROUTING                       │  paths over time-expanded graph
                    │ 4. SIMD AGGREGATION              │  group actions into legal classes (R4)
                    │ 5. OPERATING-POINT SELECTION     │  pick (time, quanta) per primitive
                    │ 6. COOLING INSERTION             │  satisfy R7/R7c at minimum time cost
                    │ 7. EVENT SCHEDULING              │  time-sliced, not depth-oriented
                    └────────────────┬────────────────┘
                                     ▼
                                   TSIR ─▶ verifier §5 ─▶ objective §6 ─▶ HTML viz §9
```

| Pass | v1 | v2 |
|---|---|---|
| **1. Placement** | trivial / ring order from `initial_order` | simulated annealing on routing distance (ZAC ships `saplacer`, `vmplacer`) |
| **2. Interaction order** | fixed ancilla per check, six-group waves | **dynamic** stabilizer→ancilla assignment (Cyclone's, and a large part of why it needs only 2 rotations); edge colouring where the code allows |
| **3. Routing** | shortest path, greedy multi-pass with priority | cycle rotation for deadlock freedom (2402.14065); SAT/ILP on small instances as an optimality oracle (2311.03454, MQT IonShuttler) |
| **4. SIMD aggregation** | greedy grouping by template | **maximum independent set on the conflict graph**, plus FluxTrap's timeline-aware forward search with position reuse. Evaluate all 18 classes with lookahead and issue the lowest-cost one. This is where 9.1 % utilization gets fixed. |
| **5. Operating point** | — (new) | per-instance choice on the primitive curve; slow down when the quanta budget binds, speed up when the clock binds |
| **6. Cooling insertion** | — (new) | greedy insertion when R7 would fail, then a pass that batches cooling into broadcast groups; report cooling time as a named component |
| **7. Scheduling** | ASAP with barriers | time-sliced: shortest-remaining-time within intra-trap mode, longest-remaining-time to drain before an inter-trap batch. Defer global transport until its gain exceeds 2× the best intra-trap alternative (FluxTrap's rule). |

### 7.1 Baselines — fixed, and each run with its own best policy

Cyclone's confusion matrix shows a *mismatched* policy makes any architecture look bad, so a
baseline crippled that way is not a baseline. Every comparison runs against all four:

1. **Stationary chain** with OMG mid-circuit measurement, no transport (arXiv:2606.06455);
2. **WISE grid** with odd–even sort reconfiguration (arXiv:2510.23519, 2305.12773);
3. **Baseline `l×l` grid**, `l = ⌈√n⌉`, static EJF scheduling (arXiv:2004.04706);
4. **Base Cyclone ring**, `m/2` traps, dynamic assignment, lockstep rotation (arXiv:2511.15910).

`Knowledge: d_baseline_set`, `d_codesign_sweep`.

---

## 8. Language choice: Python first, C++ for four kernels

Unchanged from v1, and the reading pass strengthens it: most of the heavy computation is already
someone else's C++.

**Python is the right host.** `qiskit`, `stim`, `pymatching`, `ldpc`, `networkx`, `numpy`/`scipy` —
all installed here, the last four C++ underneath.

**C++ earns its place in four places, after the data structures freeze:**
1. **Verifier replay** — O(instructions × ions), now with per-ion `n̄` state; run inside every loop.
2. **Routing / SIMD aggregation** — time-expanded A* and conflict-graph MIS over ~10⁴ nodes,
   re-solved thousands of times.
3. **The sweep driver** — embarrassingly parallel over (architecture × policy × primitive table).
4. **Transport-noise Monte Carlo**, if the closed-form heating model proves insufficient.

**Sequencing.** Phase A pure Python (M0–M3). Phase B profile, then `numba @njit` on the replay loop
— often enough on its own, at ~zero engineering cost. Phase C port only what is still hot, via
**nanobind + scikit-build-core + CMake** (`g++ 13.2` and `cmake 4.2.1` are present; add `nanobind`).
Keep the pure-Python reference **permanently** and run randomized differential tests in CI; gate
with `QCCD_NATIVE=0|1`.

**The rule that avoids a two-language swamp: C++ never owns state.** Flat arrays in, flat arrays
out. All parsing, JSON, schema validation and visualization stay in Python.

### Repository layout

```
qccd/
  arch/     schema.py, generators.py (grid/ring/ladder/racetrack/chain), device.py, curves.py
  ir/       tsir.py (dataclasses + JSON round-trip), builder.py
  codes/    bb.py ([[144,12,12]] + family), hgp.py, surface.py, esm.py
  compile/  place.py, order.py, route.py, simd.py, opoint.py, cooling.py, schedule.py, pipeline.py
  verify/   rules.py (R1–R17), replay.py, differential.py
  cost/     combinatorial.py (T1), physical.py (T2: quanta + time), logical.py (T3: stim + ldpc)
  viz/      render.py   -> standalone HTML
  native/   src/*.cpp, bindings.cpp             # phase C only
arch/       *.arch.json (wise_cyclone, wise_grid, baseline_grid, cyclone_base,
                         h2_racetrack, ladder_2x72, stationary_chain)
benchmark/  bb144_esm.qasm, hgp225_esm.qasm, *.qasm
examples/   reproduce_24_ancilla.py, cyclone_model_check.py, sweep_verticals.py,
            heating_budget.py, thesis_rotation_vs_oddeven.py
docs/       PLAN.md, adl.md, tsir.md, rules.md
tests/      test_golden_24ancilla.py, test_cyclone_formula.py, test_rules.py, test_differential.py
```

---

## 9. Visualization

Keep the shape of the current artifact — **one self-contained HTML file, no server, no CDN** — but
make it a renderer over `(arch, tsir)`, so a grid renders as a grid and a ring as a ring from one
code path. Keep the step slider, play/pause, metrics strip and per-batch contact list.

**Add, in priority order:**
1. **Per-ion `n̄` heat colouring** with the gate threshold marked — the heating budget (§0.4) is the
   design's biggest problem and it should be visible in one glance;
2. **cooling operations drawn on the timeline** as a named track, so their share of runtime is legible;
3. per-step rule badges (which of R1–R17 hold);
4. a *"why is this cycle alone?"* annotation naming the SIMD class and mode that forced the batch —
   this turns the viewer into a debugger for the 9.1 % utilization problem.

---

## 10. Milestones and acceptance criteria

| M | Deliverable | Acceptance criterion |
|---|---|---|
| **M0** | ADL schema + validator + generators + curves | The 7 reference architectures parse, expand, round-trip; DAC/electrode counts match deck p. 21 and arXiv:2510.23519 formulas. |
| **M1** | TSIR + verifier (R1–R9, R11–R14) + importer | Importing `INLINE_DATA` reproduces **cost 397 184 and steps 8 808 exactly** and passes the rules. **External oracle #1.** Locked as a golden test. |
| **M2** | Heating model (R15–R17) + cooling insertion pass | Replaying the shipped schedule reproduces **≈1747 quanta per data ion** (267 shuttling + 1336 junction + 144 docking) and reports the cooling schedule needed to make it legal. |
| **M3** | Cyclone model reproduction | Our compiler reproduces Cyclone's base BB [[144,12,12]] runtime within 10 % of the corrected formula (**62 ms at g=25 µs, 170 ms at g=100 µs**). **External oracle #2.** |
| **M4** | **Thesis experiment: rotation vs odd–even sort under WISE** | Both reconfiguration schemes on the same WISE-constrained architecture, compared at matched logical error rate. Answers `q_rotation_beats_oddeven`. **← the headline result.** |
| **M5** | Architecture sweep with junction/DAC/power terms | The `V` sweep has an **interior optimum**; Pareto frontier over (runtime, quanta, DACs, power) for BB [[144,12,12]], reported against **both** primitive tables with a ranking-stability statement. |
| **M6** | T3 logical-error pipeline + stationary-chain crossover | LER per round vs physical error rate for all 4 baselines + ours; the code size at which transport overtakes a stationary chain. Answers `q_crossover_vs_stationary`. |
| **M7** | Optimizing compiler + native kernels | ≥3× batch-utilization over the 9.1 % baseline; native kernels agree with the Python reference on 10⁴ randomized differential tests. |

M1 and M3 are **external oracles** — an architecture explorer with no oracle produces unfalsifiable
rankings. Nothing after M3 is trusted until both pass.

---

## 11. Open questions

Resolved: **is a corner a junction?** No — a junction needs three or more axes; a corner is a
bend costing one shuttle (§0.5). Also resolved from v1 §10: the per-ion-vs-per-step transport cost question
(→ §0.3: neither; it is heating that degrades the next gate); the trap-capacity default
(→ §0.6: no code-independent optimum, so it is a sweep axis); fixed-vs-free ancilla binding
(→ Cyclone assigns dynamically, and that is much of why it needs only 2 rotations).

Still open, in priority order — `python Knowledge/kg/query.py open`:

| id | question | blocks |
|---|---|---|
| `q_rotation_beats_oddeven` | Does rigid rotation remove WISE's 25× penalty? **The thesis.** | M4, M5 |
| `q_inline_vs_hanging_ancillas` | In-line ancillas (no degree-3 node on the rotation path) or hanging on vertical spurs (24 T-junctions on it)? §0.5 says in-line by a wide margin; confirm by compiling both. | M3, M4, M5 |
| `q_heating_rate_measurement` | `S_E(ω)` and the per-primitive `(time, n̄)` curves for the target trap. Two corpus tables differ by 2–3×. | M5 |
| `q_crossover_vs_stationary` | At what code size does transport beat a stationary OMG chain, charging transport *and* cooling? | M6 |
| `q_simd_class_budget` | Is `max_simd_classes_per_cycle` really 1? Deck gives WISE 2 and C2LR 4; FluxTrap's Quantinuum grid is strictly 1. | M3 |
| `q_cost_unit_equivalence` | Do the ring and ladder planners' `cost` figures count the same thing? | M1 |

---

## 12. The knowledge base

`Knowledge/` holds the literature and everything we accumulate, as a Kuzu graph built from
git-tracked YAML/Markdown notes. See `Knowledge/README.md`.

```bash
python Knowledge/kg/build.py                     # notes + Library manifest -> graph
python Knowledge/kg/query.py disputes            # claims that contradict each other
python Knowledge/kg/query.py param t_split       # every reported value, with provenance
python Knowledge/kg/query.py why                 # every decision in this plan, traced
python Knowledge/kg/query.py unsourced           # assertions with no provenance (keep at zero)
```

Current contents: 89 papers · 460 authors · 174 citation edges · 61 measurements · 40 claims with
**8 recorded contradictions** · 21 hardware rules · 25 findings · 11 open questions · 12 decisions.

Every decision in this plan is in the graph with the findings and papers behind it, so `query.py why`
reconstructs the argument for any section — and if a future paper overturns a claim, `query.py
challenged` shows exactly which decisions rest on it.

---

## 13. The first three things to build

1. **`qccd/arch/`** — schema + `ring`/`grid`/`chain` generators + primitive curves + validator.
   Half a day; unblocks everything.
2. **`qccd/ir/tsir.py` + `qccd/verify/replay.py` + the `INLINE_DATA` importer.** The moment
   397 184 / 8 808 falls out of an independent replay, the model is validated against reality (M1);
   adding the quanta accumulator immediately gives M2's ≈411 and the cooling schedule it implies.
3. **`examples/thesis_rotation_vs_oddeven.py`** — even as a crude analytic model before the compiler
   exists. If rotation does not beat odd–even sort under a one-class-per-cycle constraint on paper,
   we want to know that in week one, not month six.
