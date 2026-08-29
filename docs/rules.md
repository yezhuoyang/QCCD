# The rules, and what the verifier can honestly say about each

Layer 3 (PLAN §5). Every rule traces to a source; the statements are those in
`Knowledge/notes/constraints.yaml` (`python Knowledge/kg/query.py rules`).

```python
from qccd.verify import verify
from qccd.cost import deck_model
report = verify(prog, arch, deck_model())
report.rules.summary()
# {'passed': ['R1','R11','R12','R13','R14','R15','R18','R2','R3','R4','R4b',
#             'R5','R6','R6b','R7','R8','R9'],
#  'failed': [], 'partial': {'R15': ...}, 'skipped': {'R10': ..., 'R7b': ...}}
```

## The reporting contract

A rule reports **passed** only if the check ran. Otherwise it reports `skipped` with the
reason, or `partial` with what is approximated. A verifier that prints a green tick for a
check it did not run is worse than no verifier.

Every rule the verifier reports as passing on the shipped schedule is also exercised in
`tests/test_rules.py` against a program built to break it. A check that cannot fail is not
a check.

| rule | status here | what it actually checks |
|---|---|---|
| R1 | checked | `occupancy(site) ≤ capacity` after every cycle |
| R2 | checked | degree-≥3 nodes hold ≤ 1 ion, and ≤ 1 ion crosses one in a cycle |
| R3 | checked | ≤ `segment.capacity` participants per segment per cycle |
| R4 | checked | the movement class is declared; ≤ `max_simd_classes_per_cycle` per cycle |
| R4b | checked | a cycle has one mode, and never mixes transport with gates |
| R4c | checked when a claim is judgeable | a declared `broadcast` is answered by the control plane; a claim the device cannot judge is **skipped with the reason**, never passed |
| R4d | checked when the channels are declared | the cycle is producible by the declared map; **silent on an undeclared one, which is not a pass**. Emits under R4's name — see `docs/notes.md` §5.1 |
| R5 | checked | no `(u→v)` together with `(v→u)` on one segment |
| R6 | checked | gate/measure/cool only where the zone type allows, at the **replayed** site |
| R6b | checked | both ions of a pair co-located; `sites`, when present, cross-checked |
| R7 | checked | `n̄ ≤ budget` for both ions **at gate time** (before the gate's own elapsed-time heating) |
| R7b | **skipped** | no architecture declares a per-zone duty-cycle budget yet |
| R7c | checked | gates under a heating model with no cooling scheduled anywhere is a violation; skipped under a model with no heating |
| R8 | checked | ion set invariant; no ion participates twice in a cycle; no position change without a participant |
| R9 | checked | claimed totals, per-batch figures and per-instruction annotations vs the replay |
| R10 | **skipped** | needs symbolic permutation + Pauli-frame tracking against a QASM DAG |
| R11 | checked | unidirectional per loop per cycle; every junction degree is priceable |
| R12 | checked | ≤ 1 gate per trap per cycle |
| R13 | checked | ≤ 15 ions in a trap at gate time |
| R14 | checked | a split from a chain of > 2 needs an accounted `gate_swap` |
| R15 | **partial** | quanta composed additively, which R15 says is an *upper bound* |
| R16 | checked when the model models heating | gate error evaluated from the carried `n̄`, not a constant |
| R17 | checked when the model models time | anomalous heating accrued per elapsed µs |
| R18 | by construction | junction cost charged by the degree the expanded graph reports |
| R19 | checked when the tiling is declared `lab` | a lab-frame device needs one independently driven channel group per axis direction its rigid shift turns in; **all nine shipped devices are `path`, so R19 reports a skip reason on every one of them** |

**R4c and R19 in detail.** They are the two halves of the broadcast question, and they sit
in different buckets. R4c is a **program** rule: an instruction may claim
`broadcast: "one" | "per_direction" | "per_site"`, and the verifier answers the claim from
the device. R19 is an **architecture** rule, like R11's structural half: it needs no
program, and it fires on a device that declares a lab-frame electrode tiling without
enough independently driven channel groups to turn its own loop.

Both are conditional, and both say so rather than passing quietly. `r4c_unjudged` gives the
per-claim reason a device could not answer (no `control.channels`, no `control.optical`, an
unrecognised word, or `per_direction`, which states no count and defers to R4d); `r19_scope`
gives the per-device reason R19 said nothing (no channels declared, `frame='path'`, or no
shift class on a closed path). A rule that cannot fail on this input lands in `skipped` with
that string, never in `passed`.

One number to be careful with: on every closed loop the fleet ships, the count of axis
directions a rigid shift turns in equals `len(Device.corners(loop))` = 4. They are **not**
the same quantity — an L-shaped closed loop has 6 corners and 4 directions — so R19's
message reports both and equates neither.

**R15 in detail.** The true composition is
`n̄_tot = n̄_hom + n̄_inhom + 2√(n̄_hom·n̄_inhom)·cos θ`, whose interference term can be
negative — a phase-aware schedule can *cancel* excitation. The corpus supplies no secular
phase model for these primitives, so `Charge.then` adds. Every quanta figure the platform
reports is therefore an upper bound, and says so.

**R9 is model-scoped.** The deck's 397 184 / 8 808 are facts about the deck's cost model,
not about the program. Checking them against a replay under a different model would be a
category error, so a program records `meta.metrics_model` and R9 skips with the reason
when it does not match.

**R9's coverage is reported, not assumed.** It checks `total_cost`, `total_steps`,
`total_rotate_hops`, `contacts`, `batches`, `runtime_us`, `total_quanta`, `cooling_us`
and `peak_quanta` at the program level; `cost`, `steps` and `contacts` per batch; and
`cost`, `steps`, `t0`, `t1` per instruction. The replay computes no per-instruction
`quanta_delta` or `operating_point`, so when a program carries those annotations R9 lists
them under `not_checked` and reports itself as **partial**. Replaying with
`keep_cycles=False` leaves no per-instruction trace to check against, which is likewise
reported as partial rather than passing — a memory knob must not double as a correctness
knob.

**`only_rules` is a request, not a certificate.** Naming a rule that has no implementation
puts it in `skipped`, never in `passed`.

## The one place the deck's own rule needed disambiguating

The artifact states:

> `rotation_cost_rule`: one rigid one-slot rotation moves every occupied data ion; corner
> edges cost 3 primitive hops, straight edges cost 1
> `rotation_step_rule`: one rigid hop takes the maximum primitive edge depth used by any
> moving ion in that hop

Three readings give a defensible 148 per hop on the 2×72 ring. Only one gives 148 **and**
depth 3:

| reading | cost | depth |
|---|---|---|
| corner **segment** (both endpoints are loop corners) costs 3: `142×1 + 2×3` | **148** ✓ | **3** ✓ |
| each of the 4 corner **slots** costs 3: `140×1 + 4×3` | 152 ✗ | 3 |
| each of the 4 corner-**entering** segments costs +1: `140×1 + 4×2` | **148** ✓ | 2 ✗ |

So the cost is charged to the segment that contains a whole turn — in a height-2 ring, the
two end-caps. `Device.corner_endpoints[seg] == 2` identifies exactly those, and both 148
and 3 are computed from the expanded graph rather than supplied as constants
(`Knowledge: fd_deck_corner_cost_is_per_segment`).

This matters beyond bookkeeping: PLAN §0.5 then replaces `corner_hops = 3` with `1`, and
because the number was derived rather than hard-coded, that is a one-parameter change to
the cost model and nothing else.

## Cost models

`DeckModel` is not a strawman kept for contrast; it is the **oracle**. M1 exists to prove
the replay engine reproduces a schedule someone else computed, and that is only a proof if
the model is theirs.

| | `DeckModel` | `CorrectedModel` |
|---|---|---|
| corner segment | `corner_hops = 3` | `corner_hops = 1` (R18: a bend is ordinary transport) |
| degree-≥3 node | uniform, +1 hop / +1 step | `junction_cross` at that degree: 100 µs, 3.0 quanta |
| split / merge | not modelled | 80 µs, 6 quanta each, on the classes that `entails` them |
| heating | not modelled | per-ion `n̄`, by component |
| wall clock | not modelled | microseconds, from the curves |

**Junction charging convention.** A junction is charged **on entry** to a degree-≥3 node,
once per move. Charging both entry and exit would double-count every transit; charging
only exit would miss the last one. On entry, an ion completing one revolution of the
shipped ring crosses each of the 24 docks exactly once — which is why "445 transits per
data ion" comes out of the replay rather than out of a formula.

The junction crossing **is** that segment's transport, not something added after it: the
ion does not shuttle for 5 µs and then spend another 100 µs at the junction, it spends
100 µs crossing. So durations overlap (`max`) while heating adds.

## Cooling insertion

`qccd/compile/cooling.py`. Cooling is global — Doppler sheet beams cover the whole trap,
so one operation cools every ion. It costs schedule time but does not serialize per ion.

The R7 trigger **provably converges in one pass**: a global cool zeroes every ion, so
inserting one strictly lowers `n̄` everywhere downstream; a gate that satisfied R7 before
still does, and a gate that violated it now starts from zero. The pass asserts that on a
re-replay rather than assuming it.

`CoolingPolicy.max_gate_quanta` overrides the architecture's budget and reaches R7
itself (via the replay's `rule_config`), so a budget sensitivity sweep actually changes
the schedule rather than only the report.

An optional ion-loss trigger caps `n̄` between cools regardless of when the next gate is
(an ion survives only ~85 uncooled junction round trips before it is *lost*,
arXiv:1210.3655). Two details it has to get right:

* **Blame the instruction that crosses the cap, not the next one.** An accumulation peaks
  at the *end* of the instruction that pushes it over, and a cool inserted after that
  instruction cannot undo the excursion. The pass probes before every instruction and
  attributes an over-cap reading to its predecessor.
* **An instruction that jumps the cap on its own is unfixable.** When a cool already sits
  immediately before it and the cap is still exceeded when it finishes, no cooling
  schedule satisfies the cap there; the pass names the instruction and stops, rather than
  padding the program with cools that cannot help or looping to its iteration limit.

`peak_quanta_between_cools` is the replay's running maximum of the live `n̄`, which a cool
resets to zero — so it covers the stretch before the first cool and the tail after the
last one. Sampling only at the cools would report a reassuring `0.0` for a program with no
cooling at all, which is precisely the case the diagnostic exists to flag.


## Where these came from

The reporting contract above is not aspirational. An adversarial review of M0–M2 (six
dimension reviewers, each finding independently verified by a refuter) raised 44 claims,
of which 30 survived refutation and ~24 were distinct defects. Most were holes in checks
that were being *reported as passing*: R6/R6b/R13 driven by an optional `sites`
annotation rather than by the replayed positions, R8 false-firing on a multi-segment
`via`, `only_rules` licensing rules with no implementation, R9 silently skipping its
per-instruction granularity under `keep_cycles=False`, R7c sitting in no bucket at all,
and the cooling pass's R7-budget knob being inert. None of them moved an M0/M1/M2
acceptance number; all of them are now regression-tested in
`tests/test_review_regressions.py`, named for the defect rather than for the fix.
