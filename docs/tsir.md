# TSIR — the control IR

Layer 2 of the stack (PLAN §4). A TSIR program is a list of instructions over an
architecture, plus the metrics it *claims*. Two backends emit it — the importer that
brings in the shipped schedule, and (later) the compiler — and the same verifier checks
both.

```
qccd/ir/tsir.py         Instruction, Participant, TSIR, round-trip
qccd/ir/import_deck.py  the INLINE_DATA importer (M1's external oracle)
qccd/verify/replay.py   executes a program under a cost model
```

## Two design decisions

### One instruction is one concurrent cycle

A `simd` instruction carries **many** participants and a `gate` instruction carries
**many** pairs, because a broadcast-wired machine really does drive them together — that
is the entire content of R4's *"a class fixes (type, direction); participation is
variadic"*. Cost sums across participants; depth and duration take the maximum. That is
also the deck's own rule (*"one rigid hop takes the maximum primitive edge depth used by
any moving ion in that hop"*), and it is what makes "9.1 % batch utilization" a number
the IR can express at all.

### Cost, duration and quanta are annotations, not content

An instruction says *what moves where*. `qccd.verify.replay` computes what it costs under
a given `qccd.cost` model. Any annotation already present is a **claim**, and R9 is
exactly the check that the claims equal the replay.

That separation is why M1 and M2 run the *identical program* — under `DeckModel` and
under `CorrectedModel` — and every difference is attributable to the model rather than to
the schedule. `tests/test_golden_24ancilla.py::test_corner_hops_is_the_only_geometry_change_between_the_models`
asserts it: same moves, same contacts, same junction transits; different cost.

Claims are **model-scoped**. `meta.metrics_model` records which cost model produced them;
replaying under a different one reports R9 as *skipped, with the reason*, rather than as a
red failure that means nothing.

## Instruction types

| type | carries | notes |
|---|---|---|
| `init` | `placement`, `quanta` | must be the first instruction |
| `simd` | `class`, `mode`, `template` or `participants`, `holds` | one transport cycle |
| `gate` | `gate`, `pairs`, `sites` | many co-located pairs driven together |
| `measure` / `reset` | `ions` | |
| `cool` | `broadcast` or `ions` | global by default: one op cools every ion |
| `barrier` | — | explicit synchronization, costs nothing |

```jsonc
{ "type": "simd", "id": 1, "class": "rotate_cw", "mode": "inter",
  "template": { "kind": "loop_shift", "loop": "L0", "delta": 13 },
  "holds": ["L0", "E0", "E1", ...],
  "cost": 1924, "steps": 39,
  "meta": { "batch": 0, "kind": "rotate", "hops": 13, "direction": "cw" } }

{ "type": "simd", "id": 2, "class": "dock", "mode": "inter",
  "participants": [ { "ion": "d108", "from": "S120", "to": "A120", "via": ["V120"] } ] }

{ "type": "gate", "id": 3, "gate": "CX", "mode": "intra",
  "pairs": [["d108", "a120"]], "sites": ["A120"] }
```

## The movement template

```jsonc
"template": { "kind": "loop_shift", "loop": "L0", "delta": 1 }
```

This is not a compression trick. PLAN §1's thesis is that rigid rotation needs *exactly
one* movement template where an odd–even sort needs many, so the IR has to be able to say
"one template" in one object — counting them is how M4 will measure the claim.

`TSIR.templates()` returns distinct templates → machine cycles issued. A shift by `k` is
**not** `k` templates; it is the same unit-hop template driven `k` times, which is exactly
the property that makes one broadcast waveform enough. So the key normalizes to
`(loop, direction)` and the value counts unit cycles:

```python
prog.templates()
# {'loop_shift:L0:+1': 1159, 'loop_shift:L0:-1': 1513,
#  'class:dock': 396, 'class:undock': 396}
```

Four templates for the whole shipped schedule, independent of code size. It is four rather
than three because the schedule rotates in both directions, which is not obviously free
under broadcast wiring — see `Knowledge: q_bidirectional_rotation_template_count`.

The replay decomposes `|delta| > 1` into `|delta|` unit sub-cycles, each a real machine
cycle with its own depth, duration and rule check.

## Execution model

Instructions execute in list order, each atomically; `t0` of the next is `t1` of the
previous. That is honest for an imported barrier-scheduled artifact, and `holds` plus
explicit `t0`/`t1` leave room for the event scheduler of PLAN §7 pass 7 to overlap them
later without an IR change.

The replay owns no cost knowledge and no schedule knowledge. It carries per-ion `n̄`
**by component** — `shuttle`, `junction`, `split_merge`, `gate`, `anomalous` — because a
single opaque total cannot be checked against a published budget, and PLAN §0.4's budget
is stated as three named components.

```python
from qccd.verify import replay
res = replay(prog, arch, corrected_model())
res.quanta_components         # {'shuttle': 38649.6, 'junction': 194976.0, ...}
res.per_ion_quanta["d17"]     # {'shuttle': 268.4, 'junction': 1354.0, ...}
res.junction_transits["d17"]  # 453
res.us_by_class               # {'rotate_cw': 115900.0, 'dock': 65340.0, ...}
```

A `probe(instruction, current_quanta)` callback fires before each instruction; the cooling
pass uses it to read the `n̄` a cool is about to remove without the replay storing a trace.

## The importer

`INLINE_DATA` is one JSON object on line 344 of
`visualizer_24_ancillas_24_junctions_standalone.html`. The importer locates it by prefix,
not by line number, so an edit above it cannot silently change what gets imported.

It does **not** trust the artifact. Ion positions are recomputed by the replay from the
initial order and the rotation history, and the replay raises if a docking participant is
not where its instruction says it is — so every one of the 864 contacts is an assertion.
Totals can agree by coincidence in two places; 864 recomputed positions cannot.

Before importing anything it cross-checks the artifact's geometry against the
architecture: capacity, dock slots, ancilla sites, and every slot's side label and
position. A mismatch is an error, not a warning.

`completeness_report(prog)` reports what the artifact **omits**: it carries no ancilla
measurement and no reset, so the imported program is a transport-and-contact schedule
rather than a complete ESM round. The missing SPAM is ~1.0 ms and moves none of the M1 or
M2 numbers, but it is named rather than papered over.

## Round-trip

```python
prog.save("out/deck24.tsir.json")
again = TSIR.load("out/deck24.tsir.json")
```

`validate_program(prog)` shape-checks ids, types and required fields without needing an
architecture.
