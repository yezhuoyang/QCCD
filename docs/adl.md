# `.arch.json` — the architecture description language

Layer 1 of the stack (PLAN §3). One declarative document describes a QCCD machine: its
trap graph, its zone capabilities, its control wiring, and the `(duration, quanta)`
curves of its primitives. Everything downstream — compiler, verifier, cost model, viz —
consumes the **expanded graph**, never the generator parameters.

```
qccd/arch/schema.py       the schema and its validator
qccd/arch/generators.py   ring / grid / chain
qccd/arch/device.py       the expanded graph; degree, corners, loops
qccd/arch/curves.py       (duration, quanta) curves and operating-point policies
arch/*.arch.json          the reference architectures
tools/make_arch.py        regenerates them
```

```python
from qccd.arch import load
arch = load("arch/ring144_24v.arch.json")
arch.device.summary()
# {'n_nodes': 168, 'degree_histogram': {1: 24, 2: 120, 3: 24},
#  'n_junction_nodes': 24, 'n_corners': 4, 'n_docks': 24, 'n_dock_corners': 2, ...}
```

## The two derived quantities everything hangs off

### Degree

`Device.degree(node)` counts segment incidences **in the expanded graph**. Rule R18 then
reads straight off it: a node is a junction iff its degree is ≥ 3, and `junction_cross`
is charged at that degree.

Nothing declares "this is a junction". Attaching a spur to a rail node makes it degree 3,
and it *becomes* one. That is the point — the shipped design's 24 vertical shuttling
lines are expensive because of what they do to the graph, and the cost model finds that
out by counting, not by being told.

```python
arch.device.junction_nodes        # ('S0', 'S102', 'S108', ...)  24 of them
arch.device.degree("S0")          # 3  -- two rail segments plus the dock spur
arch.device.degree("S71")         # 2  -- a corner, but only a bend
```

### Corners

A corner is a property of a **transport loop**, not of a node in isolation.

In the shipped ring the two corner docks sit exactly on the end-caps: the ancilla for the
dock at `S0 = (0, 0)` is at `(0, 0.5)`, which lies on the segment from `S0` to
`S143 = (0, 1)`. A purely geometric "did the direction change here?" test over all
incident segments would call every T-junction a corner. So corners are found by walking
the loop's cyclic node order and comparing in- and out-directions there
(`Device.corners(loop_id)`), and the geometric test is used only at degree 2, where "the
two directions" is unambiguous (`Device.geometric_bends`) — which is what catches the
degree-2 corners of a grid, where the turn happens between two different paths.

`Device.corner_endpoints[segment]` counts how many of a segment's endpoints are corners.
A value of **2** means the segment contains a whole turn. In a height-2 ring exactly the
two end-caps qualify, which is what the deck charges 3 primitive hops for:

```
142 straight x 1  +  2 end-cap x 3  =  148 cost, depth 3     per rigid hop
```

Both numbers fall out of the graph. See `docs/rules.md` for why this reading of the deck's
rule is the only one consistent with its own totals.

## Loops

```jsonc
"loops": [ { "id": "L0", "kind": "ring", "closed": true, "nodes": ["S0", "S1", ...] } ]
```

A closed loop is the domain of exactly one movement template — "shift every ion on `L0`
by k" — which is the whole reason rigid rotation needs one SIMD class where an odd–even
sort needs many (PLAN §1). `Device.shift_map(loop, delta)` *is* that template. Keeping
loops in the geometry rather than rediscovering cycles at compile time is what makes the
thesis's quantity expressible.

An open loop (`closed: false`) is a linear register; asking it for a rigid shift raises.

## Generators

| generator | parameters | produces |
|---|---|---|
| `ring` | `width, height, verticals` | `2W + 2H − 4` rail slots in one closed loop, plus `V` dock spurs to mid-line ancilla sites |
| `grid` | `a, b` | an `a × b` lattice of junctions with one trap in the middle of every wire: `2ab − a − b` traps, interior junctions degree 4, boundary 3, lattice corners 2 |
| `chain` | `n` | `n` sites in a line; no loop, no junction, two degree-1 ends. `chain(1)` with a large capacity is the stationary-chain baseline |

Ring slot numbering reproduces the shipped visualizer's `slot_coordinates` exactly — top
row left to right, down the right end, bottom row right to left, up the left end — so
slot ids in an imported schedule mean the same thing here.

`ring(72, 2, 24)` puts docks at `{0, 6, …, 138}`; `verticals` must divide the slot count.

## Compact and expanded forms

A document may carry `geometry.generator` + `geometry.params` (compact) or the explicit
`nodes` / `segments` / `loops` (expanded). `load()` always returns the expanded graph.
`arch.to_json(expanded=…)` writes either.

`nodes` is ONE ordered array (schema 0.3). It replaced a `sites` + `junctions` split,
which was a lossy encoding of an ordered dict: `from_json` rebuilt as `sites + junctions`,
so any generator that interleaves the two — `grid`, and therefore `grid9x9` and
`deck_unit_cell` — came back reordered. Node order is load-bearing twice over: an
`explicit`-mode architecture listing emits its statements in it, and `viz/layout.py`'s
`_bows` sums a centroid over `pos.values()` in it, where two orders differ by a few ulp
after a drag — enough to flip a collinear tie-break and a segment's bow sign. The two
legacy keys are still READ, so an older expanded file still loads; it simply cannot
promise an order it never recorded.

The compact form is only emitted when re-running the generator on those params reproduces
the graph exactly (`Device.reproducible_from_generator()`); otherwise the expanded form is
written instead. Writing `generator + params` for a device that has since been edited
would throw the real graph away and hand back a file that loads as something else.

The expanded form caches the derived `degree` and `corner` on each node and
`corner_endpoints` on each segment — and **re-checks them on load**, so a hand-edited
file cannot quietly disagree with its own graph:

```
ExpansionError: node 'S0': file says degree 10, graph gives 3
```

## Zone types

```jsonc
"zone_types": {
  "data":    { "capacity": 2, "gate": false, "spam": false, "cool": true },
  "ancilla": { "capacity": 2, "gate": true,  "spam": true,  "cool": true }
}
```

`capacity` is R1's bound; the flags are R6's capabilities. Motion and gate capability are
decoupled: an ion may traverse a site it cannot be gated in. A site with no explicit
`capacity` inherits its zone type's.

## Primitives are curves, not scalars

Slowing a transport buys motional quanta, so the compiler has to be allowed to make that
trade (PLAN §3.2, §7 pass 5). Every point is tagged with the **table** it was measured in:

```jsonc
"shuttle_segment": { "curve": [
  { "us": 5,  "quanta": 0.10, "table": "qccdsim_jones",        "source": "2510.23519" },
  { "us": 12, "quanta": 1.00, "table": "transport_excitation", "source": "2605.25118" },
  { "us": 14, "quanta": 0.10, "table": "transport_excitation", "source": "2605.25118" }
]},
"junction_cross": { "curve_by_degree": {
  "3": [ { "us": 100, "quanta": 3.0, "table": "qccdsim_jones" },
         { "us": 200, "quanta": 3.0, "table": "measured", "source": "1210.3655" } ],
  "4": [ { "us": 120, "quanta": 3.0, "table": "cyclone" } ]
}}
```

The corpus supplies two mutually incompatible tables differing by 2–3× in time
(`Knowledge: q_heating_rate_measurement`). Rather than pick one, the whole stack re-runs
against either, so *ranking stability under parameter uncertainty* is itself measurable.

```python
from qccd.arch import OperatingPointPolicy
curve = arch.primitives.curve("shuttle_segment")
curve.pick(OperatingPointPolicy("qccdsim_jones", "fastest"))   # 5 us / 0.1 quanta
curve.pick(OperatingPointPolicy("transport_excitation", "coolest"))  # 14 us / 0.1
```

A policy whose table is silent about a primitive falls back to the union of tables so a
partial table is usable at all. That fallback is **reported, not hidden** —
`primitives.table_coverage(table)` says which primitives the table actually covers,
because an unnoticed fallback turns a two-table comparison into a comparison of a table
with itself.

`junction_cross` carries a degree-2 entry only because Cyclone's table has one. PLAN §0.5
rejects it — a two-arm bend has a continuous RF null and therefore no barrier — and
`CorrectedModel` ignores it.

## Control

```jsonc
"control": {
  "model": "simd_classes",
  "max_simd_classes_per_cycle": 1,          // WISE = 1; C2LR = 4
  "intra_inter_exclusive": true,            // R4b
  "classes": { "generator": "x_junction_grid", "count": 18, "extra": [
    { "id": "rotate_cw", "type": "shift", "orbit": "L0", "delta": 1 },
    { "id": "dock", "type": "shift", "orbit": "spurs", "entails": ["split", "merge"] }
  ]},
  "wiring": { "scheme": "wise", "dacs_dynamic": 100, "electrodes_per_junction": 48 }
}
```

`entails` is load-bearing. A rigid rotation is a conveyor-belt transport of a whole chain
along a moving potential — nothing splits (H2 drives 20 wells per side from three
broadcast signals, arXiv:2305.03828). A dock lifts one ion out of the rail's potential and
inserts it into a separate trap, so it costs a split at the source and a merge at the
destination, each way. That difference is a property of the movement class, not of
occupancy, so the architecture declares it and the cost model reads it
(`arch.entails("dock") == ("split", "merge")`).

## The reference architectures

| file | shape | degrees | note |
|---|---|---|---|
| `ring144_24v` | `ring(72, 2, 24)` | `{1:24, 2:120, 3:24}` | the shipped design. 24 degree-3 docks, 4 corners, 2 of them docks |
| `cyclone_base` | `ring(36, 2, 0)` | `{2:72}` | base Cyclone, `m/2 = 72` traps, ancillas in-line, **no junction on the rotation path** |
| `chain` | `chain(72)` | `{1:2, 2:70}` | the unrolled control: same 144 ion slots, no loop, no spur, no junction |
| `grid9x9` | `grid(9, 9)` | `{2:148, 3:28, 4:49}` | baseline grid QCCD; `2ab − a − b = 144` traps, one per data qubit |

## Validation

`validate_document(doc)` shape-checks and returns messages with a JSON path;
`check(doc)` raises. Structural checks that need the expanded graph —
dangling endpoints, duplicate ids, self-loops, parallel segments, a junction declared with
non-zero capacity, declared-vs-derived mismatches — live in `Device.check_structure` and
run on every `load()`.

One further check is program-level and lives in the verifier: every node the graph makes a
junction must be **priceable** at that degree, or the architecture cannot charge for what
it built. It is reported under R11.
