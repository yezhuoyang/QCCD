# Rules review — organized notes

Working notes from the discussions of [`docs/rules.md`](rules.md), reorganized from the
raw file [`notes`](../notes). Three questions were raised and are answered in §2; the rest is
the rule-by-rule audit the discussion was for: **is every rule necessary, and is anything
redundant?**

Everything marked **[verified]** below was produced by running the verifier, not by reading
it. Everything marked **[from docs]** is quoted from the repo and not independently
reproducible here (the deck artifact and the `Knowledge/` graph are not in this checkout).

---

## 1. Terminology

| term | what it means here | where it lives |
|---|---|---|
| **node** | a vertex of the expanded device graph. Two kinds: `site` (a trap slot, has `capacity` and `zone_type`) and `junction` (holds no ions, `capacity 0`) | `qccd/arch/device.py` |
| **segment** | an edge — a piece of shuttling path between two nodes. Has a `capacity` (**1 on all nine shipped devices**) | `qccd/arch/device.py` |
| **ions live on nodes, and travel along segments** | the raw note "ions on the edges" is about travel, not rest; the replay only ever stores `ion → node` | `replay.py` `pos` |
| **degree** | segment incidences of a node **in the expanded graph**. Nothing declares "this is a junction" — attach a spur to a rail node and it *becomes* degree 3 | `Device.degree` |
| **junction** | degree ≥ 3. **This is R18** and it is not a free convention — see §2.2. A degree-2 bend is *not* a junction; it is ordinary transport | R18 |
| **corner** | a property of a **loop**, not of a node: where the loop's in- and out-direction differ. `corner_endpoints[seg] == 2` means the segment contains a whole turn | `Device.corners` |
| **cycle** | one machine step. One instruction is one cycle, except a `loop_shift` template with `|delta| > 1`, which decomposes into `|delta|` unit sub-cycles, each rule-checked separately | `replay.py:123-161` |
| **class (SIMD class)** | a declared movement template. A class fixes **(type, direction)**; participation is *variadic* — each site may join or opt out via its switch. `max_simd_classes_per_cycle = 1` on all nine devices | `arch.simd_classes` |
| **movement broadcast** | one DC waveform on one channel drives every site wired to it. Declaring the movement (direction + amount) first and the participant set second is exactly what the ADL does. Bounded by R4 / R4d / R19 | `control.channels` |
| **laser broadcast** | one fixed-frequency laser enters a fibre under the chip and is split 2ⁿ ways to reach many sites at once, so those sites get the **same gate** in parallel. A different pathway, different hardware, different rules — R12 and R4c's optical clause. R4b is what keeps the two from sharing a cycle | `control.optical` |
| **electrode frame** | whether a movement waveform acts in the **lab frame** (electrodes fixed to the chip axes, so +x and −x are different waveforms and a bend needs its own) or the **path frame** (electrodes tiled along the trap axis, so "advance one well" is one instruction even round a corner). R19; declared by `control.channels.frame`, default `path` | `control.channels.frame` |
| **`entails`** | a property of the *class*, not of occupancy: a `dock` lifts an ion out of one potential into another, so it costs `split` + `merge`; a conveyor rotation costs neither | `arch.entails()` |
| **expanded graph** | the explicit node/segment/loop graph a `.arch.json` becomes on `load()`. Compact (`generator` + `params`) and expanded are two spellings of one device; everything downstream reads only the expanded one, and every derived quantity above is *counted off it*, never declared | `Device`, §1.1 |

Two corrections to the raw terminology notes:

- **"Junction: degree 2, degree 3 more expensive"** — under R18 a degree-2 bend is **not** a
  junction at all and costs one ordinary shuttle. The distinction is categorical, not a
  price difference, and the whole `q_inline_vs_hanging_ancillas` question turns on it (§2.2).
  The degree-2 entry that *does* exist in every `junction_cross` curve is Cyclone's, kept for
  provenance and deliberately ignored by `CorrectedModel`. **[verified]** it is dead data
  today, but nothing asserts that.
- **"Nodes: basically means crossover of shuttling"** — that is a *junction*. A node is any
  vertex; most nodes are traps.

### 1.1 The expanded graph

Every term in the table above — node, segment, degree, corner, junction — is a property of
the **expanded graph**, and none of them is a property of the file you opened. This section
says what the expansion is, shows one small device expanding in full, and shows why the
distinction is load-bearing rather than a serialization detail.

#### The two forms a `.arch.json` may carry

A device is written in one of two spellings of the same graph.

**Compact** — a generator name and its parameters. All nine shipped devices are this form
(`arch/ring144_24v.arch.json`):

```jsonc
"geometry": { "generator": "ring", "params": { "width": 72, "height": 2, "verticals": 24 } }
```

**Expanded** — every node, every segment and every loop stated explicitly, with the derived
`degree` / `corner` / `corner_endpoints` written alongside:

```jsonc
"geometry": {
  "generator": "ring", "params": { "width": 72, ... },        // kept, for provenance
  "nodes":    [ { "id": "S0", "pos": [0.0, 0.0], "kind": "site", "capacity": 2,
                  "zone_type": "data", "labels": ["top","dock"],
                  "degree": 3, "corner": true }, ... ],        // 168 of these
  "segments": [ { "id": "E143", "ends": ["S143","S0"], "length": 1.0, "capacity": 1,
                  "loop": "L0", "labels": ["rail"], "corner_endpoints": 2 }, ... ],
  "loops":    [ { "id": "L0", "kind": "ring", "closed": true, "nodes": ["S0", ...] } ]
}
```

Same device: **9,747 bytes / 371 lines compact, 99,123 bytes / 5,425 lines expanded** — 10.2×
(`json.dumps(arch.to_json(expanded=…), indent=2)`). **[verified]**

`load()` always returns the expanded graph (`qccd/arch/__init__.py:75-79` →
`Architecture.from_json`, `device.py:734-786`). Both spellings are erased by the time
anything downstream sees the device: the compiler, the verifier, the cost model and the viz
consume `Device` and **never read `device.params`**. **[verified]** — grepping `.params`
over `qccd/verify/`, `qccd/cost/` and `qccd/compile/` returns nothing but `tsir.py:173`,
which is an *instruction's* params, not a generator's. The only two readers outside
`qccd/arch/` are `qccd/api.py:639` (rebuilding a `Device` dataclass, carrying provenance
through) and `qccd/viz/render.py:325` (printing the generator name on the page). That is
`docs/adl.md`'s "everything downstream consumes the **expanded graph**, never the generator
parameters", checked rather than asserted.

#### What expansion actually does — `ring(4, 2, 2)` in full

Expansion runs the generator function (`qccd/arch/generators.py:56-162` for `ring`) and gets
back a `Device`: an ordered `{id: Node}`, an ordered `{id: Segment}`, and `{id: Loop}`.
Nothing else is stored. `degree`, `corners`, `corner_endpoints`, `junction_nodes` and
`bend_nodes` are then **derived from that graph on demand** and cached
(`device.py:230-338`), never authored.

The smallest ring with docks — `ring(width=4, height=2, verticals=2)` — is 10 nodes and 10
segments. Perimeter first, in the shipped visualizer's slot order (top row L→R, down the
right end, bottom row R→L, up the left end), then the ancillas the spurs reach:

```
id   pos          kind/cap  zone     labels        degree  corner
S0   (0.0, 0.0)   site/2    data     top, dock       3      True
S1   (1.0, 0.0)   site/2    data     top             2      False
S2   (2.0, 0.0)   site/2    data     top             2      False
S3   (3.0, 0.0)   site/2    data     top             2      True
S4   (3.0, 1.0)   site/2    data     bottom, dock    3      True
S5   (2.0, 1.0)   site/2    data     bottom          2      False
S6   (1.0, 1.0)   site/2    data     bottom          2      False
S7   (0.0, 1.0)   site/2    data     bottom          2      True
A0   (0.0, 0.5)   site/2    ancilla  ancilla         1      False
A4   (3.0, 0.5)   site/2    ancilla  ancilla         1      False

id   ends          len  cap  loop  labels   corner_endpoints
E0   (S0, S1)      1.0   1   L0    rail      1
E1   (S1, S2)      1.0   1   L0    rail      0
E2   (S2, S3)      1.0   1   L0    rail      1
E3   (S3, S4)      1.0   1   L0    rail      2   <- contains a whole turn
E4   (S4, S5)      1.0   1   L0    rail      1
E5   (S5, S6)      1.0   1   L0    rail      0
E6   (S6, S7)      1.0   1   L0    rail      1
E7   (S7, S0)      1.0   1   L0    rail      2   <- contains a whole turn
V0   (S0, A0)      0.5   1   —     spur      0
V4   (S4, A4)      0.5   1   —     spur      0

loop L0   ring, closed, ('S0','S1','S2','S3','S4','S5','S6','S7')
degree histogram  {1: 2, 2: 6, 3: 2}
junction_nodes    ('S0', 'S4')          corners(L0)  {S0, S3, S4, S7}
bend_nodes        ('S3', 'S7')          total_capacity 20
```
**[verified]**, from a document whose `geometry` is exactly
`{"generator":"ring","params":{"width":4,"height":2,"verticals":2}}` and whose
`zone_types` are the shipped ring's. One caveat worth stating: **`capacity` is not the
generator's**. Calling `generators.ring(4,2,2)` directly gives every node `capacity 0` and
`total_capacity 0`; the numbers above appear because `Architecture.from_json` runs
`resolve_capacities(device, zone_types)` (`device.py:789-815`) after expansion, so each
site inherits its zone type's capacity unless it set `capacity_explicit`. Expansion is
generator-then-resolve, and R1's bound comes from the second half.

Read four things off it:

- **Slots are `2W + 2H − 4` = 8**, docks every `8 // 2 = 4` slots, so `{S0, S4}` — the
  "verticals must divide the slot count" rule (`generators.py:78-82`) is why `ring(72,2,24)`
  works and `ring(72,2,25)` raises.
- **`S0` is a corner *and* a junction; `S3` is a corner and not.** Corner is a loop property
  (walk `L0`, compare in- and out-direction: `device.py:274-290`); junction is a degree
  property (R18). They are orthogonal, and this device has one of each combination.
- **`A0 = (0.0, 0.5)` lies exactly on segment `E7`** (`S7 = (0,1)` → `S0 = (0,0)`). That is
  the shipped ring's "corner docks sit on the end-caps" at 1/18 scale, and it is why corners
  are found by walking the loop rather than by a geometric test over all incident segments —
  the geometric test would call every T-junction a corner.
- **Two segments have `corner_endpoints == 2`.** In the 2×72 ring those are `E71` and `E143`
  **[verified]**, and `142 × 1 + 2 × 3 = 148` is exactly the deck's per-hop cost
  (`docs/adl.md`). The graph records the structure; `qccd.cost` decides what to charge.

Scaling the same generator up, `ring(72, 2, 24)` → `2·72 + 2·2 − 4 = 144` rail slots
`S0…S143` plus 24 ancillas `A0, A6, …, A138` = **168 nodes**; 144 rail segments `E0…E143`
plus 24 spurs `V0, V6, …, V138` = **168 segments**; one closed loop `L0` of 144 nodes.
**[verified]**

```
degree histogram  {1: 24, 2: 120, 3: 24}      junction_nodes  24  (S0, S6, S12, … S138)
corners(L0)       {S0, S71, S72, S143}        of which docks  2   bends 2  (S71, S143)
corner segments   E71, E143                   total_capacity  336
```

Nothing in that block is written in `ring144_24v.arch.json`. All of it is counted.

#### Why it matters: R18

**No file declares a junction. Degree is counted, so attaching a spur *makes* one.** The
cleanest demonstration holds the loop fixed and changes only `verticals`:

```
ring(72, 2,  0)   144 nodes   degrees {2: 144}                  junction_nodes ()
ring(72, 2, 24)   168 nodes   degrees {1:24, 2:120, 3:24}       junction_nodes 24 of them
degree("S6")            2                       ->                     3
is_junction("S6")   False                       ->                  True
```
**[verified]** Under R18 that flips 24 of the 144 rotation slots from ordinary transport to
RF-barrier junctions, which is §2.2's 20× in time and 30× in heating — bought by a
parameter, priced by a count. `arch/cyclone_base.arch.json` is the shipped device on the
`verticals=0` side of that line (it is `ring(36, 2, 0)`, 72 nodes, `{2: 72}`, **zero**
junctions **[verified]**); `ring144_24v` is `ring(72, 2, 24)` verbatim.

The same thing happens one edit at a time. Starting from the shipped `cyclone_base`, which
has **zero** junctions, and drawing a single ancilla wired to `S3`:

```python
from qccd.arch import load
from qccd.arch.edit import add_site
d = load("arch/cyclone_base.arch.json").device
d.degree("S3"), d.junction_nodes          # (2, ())
d2, rep = add_site(d, "A3", 3.0, 0.5, zone="trap", capacity=2, to=["S3"])
d2.degree("S3"), d2.junction_nodes        # (3, ('S3',))
rep.degree_changed                        # {'S3': (2, 3)}
```
**[verified]** — `EditReport.degree_changed` is the edit layer telling you a rail slot just
became a junction, which is the only warning anyone gets.

And the graph is what the rules quantify over, not the file. Three more spurs on the same
node take it to degree 5, which no shipped device can price (**all nine price
`junction_cross` only at degrees 2, 3, 4 [verified]**), and R11's structural half fires with
no program at all:

```
[R11] instruction -1: node S3 has degree 5 but the architecture prices no junction_cross at degree 5
```
**[verified]**, from `rules.architecture_violations` (`qccd/verify/rules.py:635-661`), which
iterates `dev.junction_nodes` — a derived tuple. The cost model reaches the same graph by
the same route: `models.py:305` is `self.junction_point(arch, arch.device.degree(dst))`.

#### The round-trip rules

Two guards, in opposite directions, and both exist because a spelling that loads as a
different device is worse than no serialization.

**Expanded → the cached derived fields are re-checked on load.** `Device.to_json` writes
`degree` and `corner` on every node and `corner_endpoints` on every segment
(`device.py:551-570`); `Device.from_json` collects them as `declared` and
`check_structure(declared)` compares each against the graph (`device.py:489-509`). A
hand-edit that adds a spur off `S1` but forgets to bump `S1`'s cached degree is refused:

```
ExpansionError: 1 structural error(s) in 'ring144_24v':
  node 'S1': file says degree 2, graph gives 3
```

Fix the cached value and the same file loads, now with **169 nodes and 25 junctions**.
**[verified]** The corner and corner-endpoint caches are checked the same way —
`node 'S1': file says corner=True, loop geometry gives False`,
`segment 'E0': file says corner_endpoints=2, geometry gives 1`. **[verified]**

**Compact → only written back when re-running the generator reproduces the graph exactly.**
`Device.reproducible_from_generator()` (`device.py:511-548`) re-runs the generator on the
stored params and compares every node's `(pos, kind, zone_type, labels, capacity_explicit)`,
every segment, and every loop's node order. `Architecture.to_json(expanded=False)` asks that
question and **silently falls back to the expanded form when the answer is no**
(`device.py:696-712`). On the edited `cyclone_base` above:

```python
d.reproducible_from_generator()    # True   -> geometry written as {"generator","params"}
d2.reproducible_from_generator()   # False  -> geometry written as nodes+segments+loops
```

**[verified]**, and `save(arch, path, expanded=False)` on the edited device writes
`['generator','loops','nodes','params','segments']` and reloads with **73 nodes**. Forcing
the compact form anyway — writing `generator + params` for that same edited device — reloads
with **72 nodes and `junction_nodes == ()`**: the spur is gone, the junction with it.
**[verified]** That silent loss is the whole reason the guard is there.

Note that `generator` and `params` survive into the expanded form and stay accurate for an
*unedited* device: `load` → `to_json(expanded=True)` → `from_json` still reports
`reproducible_from_generator() == True` and re-serializes compactly. **[verified]** The
compact/expanded choice is a serialization decision, not a fork in the device.

#### Why `nodes` is ONE ordered array (schema 0.3)

Schema 0.3 replaced the expanded geometry's `sites` + `junctions` split with a single
ordered `nodes` array (`qccd/arch/schema.py:45-49`, `157-159`). The split was a **lossy
encoding of an ordered dict**: `from_json` rebuilt the device as `sites + junctions`, so any
generator that does not emit sites-first came back reordered. `grid` is one — `grid(3,3)`
emits `J0_0 … J2_2` before `T0_0h …`, and the legacy rebuild returns the T's first.
**[verified]** on `grid9x9` and `deck_unit_cell`, both 225 nodes.

Node order is load-bearing twice:

1. **`architecture_listing(mode="explicit")` emits its statements in node order.** Reordering
   `grid9x9`'s 225 nodes the legacy way and re-emitting gives a **166-line unified diff**
   over a 622-line listing, with all 225 node statements at different positions —
   **[verified]**, and exactly the number `docs/adl.md` records. A device that "differs by
   166 lines before and after a reload" has no stable text form to review or diff.
2. **`viz/layout.py::_bows` sums a centroid over `pos.values()` in that order**
   (`layout.py:299`), with a deliberately uncompensated left-to-right accumulator
   (`_fsum`, `layout.py:116-129`). The comment records a real failure: `sum()` gave
   `128000.0` where a naive loop gave `127999.99999999999`, the collinear tie-break
   `((mx−cx)·nx + (my−cy)·ny) >= 0` saw `−0.0` in Python and `−1.14e−13` in JS, and spur
   `V28` bowed +13.241 against −13.241 — a sign flip, the spur bowing *into* the loop it
   exists to route around. *Caveat:* reversing node order on the shipped ring did **not**
   reproduce the flip here (bows stayed `{'E71': −11.234, 'E143': −11.234}`) — that ring's
   centroid is exact, so this one is taken from the source comment, not re-derived.

The two legacy keys are still **read** (`device.py:576-579`), so an older expanded file still
loads. It simply cannot promise an order it never recorded. `_GEOMETRY` does not set
`additional: True`, so no third spelling can appear by accident — **[verified]**, adding
`geometry.traps` gives
`ValidationError: $.geometry: unknown key 'traps' (known keys: generator, junctions, loops, nodes, params, segments, sites)`.

#### What the expanded graph does NOT contain

The whole of it, field by field (`qccd/arch/device.py`, **[verified]** by
`dataclasses.fields`):

```
Node     id, pos, kind, capacity, zone_type, labels, capacity_explicit
Segment  id, ends, length, capacity, loop, labels
Loop     id, nodes, closed, kind, note
Device   nodes, segments, loops, generator, params
```

**No voltages. No electrodes. No metal. No dimensions in metres.** `pos` is in abstract
lattice units, `length` is a unitless multiplier, and `capacity` is an ion count. There is no
DC electrode, no RF rail, no waveform, and no field anywhere in the expansion.

All of that lives in `qccd/phys/`, and is a **pure function of `(Device, Technology)`**
(`qccd/phys/__init__.py:1-19`, `qccd/phys/tech.py:1-10`) — the technology sidecar carries
every dimension in integer nanometres with a source, and `phys.build` derives the metal.
Nothing in `qccd/phys/` is authored in an `.arch.json` and no field is added to `Node` or
`Segment` for it, "so there is nothing for the browser to edit or the serialization to
lose." `python -m qccd phys <device>` derives it and runs the DRC; that is the layer at
which `ring144_24v`'s spurs turn out not to fit (`docs/phys.md`).

The same boundary explains the control plane: `control.channels` names *sites*, and which
sites a channel drives is resolved against the expanded graph — but the waveform itself is
never in the graph either, which is the gap the R4d discussion is about (§4.2).

#### How to see it yourself

```bash
python -m qccd devices                          # all nine: nodes, traps, junctions, corners
python -m qccd show ring144_24v                 # degree histogram, junctions, corners, capacity
python -m qccd arch ring144_24v --mode explicit # the expanded graph as the program that rebuilds it
python -m qccd arch cyclone_base --mode generator   # the compact form, for contrast
python -m qccd reach ring144_24v                # what the graph alone permits, with no program
```

```python
from qccd.arch import load, save, Architecture
from qccd.arch.generators import ring, grid, chain

a = load("arch/ring144_24v.arch.json")        # compact on disk, expanded in memory
d = a.device
d.summary()                                   # every derived count in one dict
len(d.nodes), len(d.segments)                 # 168, 168
d.degree("S0"), d.degree("S71")               # 3 (dock), 2 (bend)
d.junction_nodes                              # 24 ids, derived, not declared
d.corners("L0"), d.bend_nodes                 # {S0,S71,S72,S143}, ('S143','S71')  <- id order
d.corner_endpoints["E143"]                    # 2 -- this segment contains a whole turn
d.reproducible_from_generator()               # True -> may be written compact

ring(4, 2, 2).nodes.keys()                    # the 10-node worked example above
a.to_json(expanded=True)["geometry"]["nodes"][0]   # what gets cached per node
Architecture.from_json(a.to_json(expanded=True))   # round trip; re-checks every cache
```

### 1.2 The figures

Every term above has a figure, and every figure is **generated by the verifier**: the
verdict under each panel is `str(Violation)` out of `verify()`, and
`tools/make_rule_figs.py` refuses to emit a figure whose observed rule set differs from
the one the spec declares. Rebuild them all with `python tools/make_rule_figs.py --all`.

### Node, segment, ion

![node and segment](img/rules/t01_node_and_segment.png)

### Degree — and what makes a junction (this is R18)

![degree and junction](img/rules/t02_degree_and_junction.png)

### Corner — a property of the loop, not of a node

![corner](img/rules/t03_corner.png)

### Cycle, and the sub-cycles a template decomposes into

![cycle and sub-cycle](img/rules/t04_cycle_and_subcycle.gif)

### Class, broadcast, and variadic participation

![class and broadcast](img/rules/t05_class_and_broadcast.gif)

### `entails` — what a movement class costs beyond the hop

![entails](img/rules/t06_entails_split_merge.gif)

### Loop — closed, open, and none at all

![loops](img/rules/t07_loops.png)

**Distance is not modelled as a cost.** `length_scaling` is off by default, and the corpus
reason is that excitation is time-dominated, not distance-dominated (2605.25118: the
near-adiabatic regime is reached within 20 µs "regardless of the transport distance"). A
broadcast shift can cross the whole chip in one cycle; it costs more only because it takes
longer.

---

## 2. The questions

### 2.1 What does "replay" mean? What is a "replayed site"?

**Replay** = the verifier re-executes the program instruction by instruction against the
architecture graph and a cost model, maintaining **its own** ion→site map, occupancy
counters, per-ion n̄ (itemized by component: shuttle / junction / split_merge / gate /
anomalous) and clock — and recomputes every number the program claims.
(`qccd/verify/replay.py`)

Its contract is narrow on purpose:

- it owns **no cost knowledge** — every number comes from the `CostModel` handed to it, which
  is why the same program replays under the deck's model (M1) and the corrected one (M2)
  with every difference attributable to the model;
- it owns **no schedule knowledge** — instructions execute in list order, each atomically,
  and `t0`/`t1` are *computed*, so an annotation is always a **claim**;
- it carries n̄ **by component**, because one opaque total cannot be checked against a
  published budget.

**Why it exists: R9.** A TSIR program *carries* `total_cost`, `total_steps`, `t0`, `t1`,
`quanta_delta`. Those are claims. R9 is the rule that the claims equal the replayed values,
checked at three granularities — program totals, per-batch, per-instruction — *because a
total can agree by cancellation*. **[verified]** a program claiming `total_cost: 999` when
the replay computes `1.0` fails R9 and nothing else. That is the whole difference between a
verifier and a pretty-printer.

**"The replayed site"** is the answer to *where did this gate actually happen?* There are two
candidates:

1. the instruction's own `sites: ["A120"]` annotation — optional, written by whoever emitted
   the program;
2. `pos[ion]` in the replay's reconstructed map — **the replayed site**.

`CycleView.gate_sites()` (`rules.py:143`) takes the second, and R6, R6b, R12 and R13 all read
it. There is no noun "replay site" in the repo; the phrase in `docs/rules.md:34` is "at the
**replayed** site".

**Why this is load-bearing.** The earlier implementation drove R6/R6b/R13 off the `sites`
annotation — so **a program could switch those checks off by omitting the annotation**, and
the report still said `passed`. That is one of the ~24 defects the adversarial M0–M2 review
confirmed; the regression is named for the defect
(`tests/test_review_regressions.py:267`, `"omitting sites must not disable R6"`).

> **Mental model.** A TSIR program is a shot log with the analysis already filled in. The
> replay is you re-running the analysis from the raw sequence: trap map from the
> architecture, calibration table from the cost model, step forward tracking where every ion
> is, how crowded each well is, how much motional energy each ion picked up and from which
> mechanism, and how much wall clock has passed. Then compare your numbers to theirs (R9),
> and check the physics against **your** ion positions, not their labels (R6/R6b/R12/R13).

*One caveat found while checking this* — `gate_sites()` returns the replayed positions
**unioned with** the declared annotation, so the documented phrase "at the replayed site" is
really "at the replayed ∪ declared sites". **[verified]** a legal co-located gate at `A0`
that declares `sites=("S5",)` fires both R6 (*"gate at S5 whose zone type 'data' has
gate=false"* — no ion is at S5) and R6b. The bias is conservative (over-rejection only), but
the documented contract is not what the code does.

### 2.2 R18 — what it is, and why it is in the list

**Statement:** *a node is a junction only if three or more trap axes meet at it.*
**Sources:** quant-ph/0702175, 2305.03828, 1210.3655.

**The physics.** A junction is defined by its **RF barrier**: *"Shuttling through junctions,
where three or more linear trap axes join, is greatly complicated by the presence of rf
barriers leading into the junction"* (quant-ph/0702175), the barrier arising from
*"unbalanced fields produced by the electrodes across the junction"*. A two-arm bend has
continuous RF rails, no unbalanced field, and therefore no barrier — just a principal-axis
rotation handled by DC shims. Shipped hardware confirms it: Quantinuum's H2 race track is a
**single continuous RF null**, and its curved end zones are ordinary conveyor-belt regions
driven by the same `{a,b,c}` broadcast tiling as the straights (2305.03828). And an
X-junction is not something you get by drawing two lines crossing — *"a junction naively
assembled from the intersection of two linear sections does not provide adequate
three-dimensional confinement to allow controlled transport"*; the RF electrode shape must be
redesigned, and residual dielectric charging can leave an unmodelled barrier at the centre.

**Why it is a rule and not taste — trace the number.** The deck had it exactly backwards:
`DeckModel` charges a ring corner **3 primitive hops** and a degree-≥3 node **nothing**. R18
inverts both — `corner_hops = 1`, `junction_cross` charged at the degree the graph reports.
The consequences on the shipped ring **[from docs]**:

```
revolutions per ESM round        2672 / 144            = 18.56
T-junction transits per data ion 18.56 × 24            = 445
heating from junctions           445 × 3 quanta        = 1336  of the round's ~1747
wall clock, rotation only        2672 hops × 100 µs    = 267 ms, not 13 ms
measured uncooled survival       ~85 junction round trips before ION LOSS (1210.3655)
```

Because 24 of the 144 loop nodes are degree-3 and a rigid hop takes the max over all moving
ions, **every single hop crosses a junction** — the whole rotation runs at junction price.
The physical layer reaches the same verdict independently: at the technology's dimensions
`ring144_24v`'s spurs *do not fit* (`docs/phys.md`).

**How it is "checked" — bluntly, it is not.** `qccd/verify/__init__.py:282` is
`rules.checked.add("R18")`. That is a green tick for a check that did not run and cannot
fail, which the repo's own contract forbids two files over (*"A check that cannot fail is not
a check"*). What a real R18 check would be, and it is cheap:

- (a) no cost model charges a junction price below degree 3 — `CorrectedModel.junction_min_degree`
      is a free `int`; assert it is 3 (2 overcharges bends, 4 undercharges T-junctions);
- (b) no architecture declares a `kind="junction"` node at degree < 3. **[verified]**
      `add_junction(dev, "JX", 5, 5, to=["S2"])` yields `kind="junction"`, `degree == 1`, and
      `check_structure()` returns `[]`. The mispricing risk is contained — `junction_nodes`
      is derived from degree, so `JX` is *not* in it and is charged as plain transport, and
      the interactive editor does warn — but the load path accepts a node called a junction
      that is not one, and (a) is a live route back to charging it;
- (c) the degree-2 `junction_cross` entry every device ships stays unreachable;
- (d) the threshold "3" is written independently in five places (`device.py` ×2,
      `rules.py` ×2, `replay.py`) plus a settable field on the model. **R18 *is* the claim
      that these are the same number**, and asserting that is the highest-value missing line
      in the verifier.

**Is R18 a rule or a definition?**

| | statement | quantifies over |
|---|---|---|
| R2 | ≤ 1 ion on any junction | `degree(node) >= 3` |
| R11 (structural half) | every junction degree must be priceable | `dev.junction_nodes` |
| **R18** | **which nodes those are** | — nothing |

R2 and R11 are predicates on program state. **R18 has no free program variable at all** — it
is the interpretation function for the word "junction" in the other two. Formally it is a
definition, and the docs half-agree by accident: R18 is *absent from PLAN §5's rule table*,
which runs R1…R17 and stops.

**Keep it, but make it fail.** Three reasons it earns a slot: (1) in this codebase the
definition has **four independent implementations that can disagree**, and a definition with
more than one implementation is a contract; (2) it was a finding *against a source*, and
demoting it to a glossary entry drops the citations that stop the deck's convention creeping
back as taste; (3) it is the only entry whose retraction changes an architectural verdict by
20× in time and 30× in heating. Move it into `architecture_violations` as a
`(model, architecture)` admissibility check with a negative test — or move it to a
**Definitions** section and take it out of `passed`. "By construction" and "passed" cannot
both be true of the same line.

**The design consequence.** `q_inline_vs_hanging_ancillas` — in-line ancillas (no degree-3
node on the rotation path) or hanging on spurs — **is R18 and nothing else**. Under the raw
notes' reading (degree-2 cheap, degree-3 dearer) it is a marginal cost comparison the spurs
probably win, because they cut rotation hops ~6×. Under R18 it is categorical: 20× time, 30×
heating, past an ion-loss limit, and not fabricable. There is also a fourth consequence found
during this audit and **[verified]**: odd–even sort on `ring144_24v` fails **R2 with 864
violations** (a transposition parks both ions of a pair in one slot, and 24 of the 144 rail
slots are degree-3 where R2 allows exactly one) while the same program on `cyclone_base` is
clean. **The verticals do not merely tax the rotation path — they make the rival
reconfiguration scheme structurally illegal on the same hardware**, which is why the thesis
experiment `q_rotation_beats_oddeven` has to be run on `cyclone_base`.

### 2.3 Why ε(n̄) = ε₀ + κ·n̄ makes sense — the theory behind R16

The repo implements `ε = ε₀ + 2.0e-3 · n̄` with ε₀ = 1 − 0.99816 = **1.84×10⁻³** (H2's
measured 2Q infidelity, 2305.03828) and R7's budget `max_quanta = 1.0`.

**The short answer:** the linear law is the **first-order Taylor expansion of a smooth
ε(n̄) about n̄ = 0**, and it is licensed not by the physics being linear everywhere but by
**R7 refusing to evaluate it anywhere else**. `max_quanta` is simultaneously the physics
budget and the declared validity domain of `error_vs_quanta`. They are a matched pair, and
the repo never says so.

#### (a) Why n̄ *nominally* does not appear at all

Two ions in one well share the normal modes of the Coulomb crystal. A Mølmer–Sørensen gate
drives red and blue sidebands together; in the Lamb–Dicke regime the Hamiltonian is

```
H = ℏ η Ω Σⱼ σ_φ⁽ʲ⁾ ( a e^{−iδt} + a† e^{+iδt} )
```

`[H(t), H(t′)]` is a c-number, so the Magnus series terminates and the propagator is exact:

```
U(t) = D( α(t)·S_φ ) · exp[ i Φ(t) S_φ² ]
α(t) = (ηΩ/δ)(1 − e^{iδt})     — a circle in phase space, closing at t_g = 2π/δ
Φ(t) = (ηΩ/δ)²(δt − sin δt)    — twice the enclosed area
```

`D(α)` is a **rigid translation** of phase space: the enclosed area does not depend on where
the blob started. The gate phase is identical for |0⟩, |n⟩, a coherent state or a thermal
mixture. **This "hot gate" property is why a QCCD machine can exist at all** — contrast
Cirac–Zoller, which uses |n=0⟩ as a computational level and fails outright for n > 0. If MS
were CZ-like, `error_vs_quanta` would be a cliff at n̄ ≈ 0, not a gentle slope.

The insensitivity is only first order, because the phase survived only after truncating
`exp[iη(a+a†)]` at O(η), and the loop closed only because δ exactly matched ω. Both are what
n̄ breaks.

#### (b) Where the n̄ dependence comes from — four mechanisms

1. **Debye–Waller / Lamb–Dicke.** The exact matrix element is `Ω_n = Ω e^{−η²/2} L_n(η²)`,
   i.e. `Ω_n/Ω ≈ 1 − η²(n + ½)`. Different Fock components enclose different areas, so the
   entangling angle *dephases* across the distribution — not a coherent over-rotation you
   can calibrate away. Infidelity goes as the **variance**: `1−F ≈ (2Φη²)²·Var(n)`, and for
   a thermal state `Var(n) = n̄(n̄+1)`, giving `~η⁴n̄` for n̄ ≪ 1 and `~η⁴n̄²` for n̄ ≫ 1.
   The crossover sits at n̄ ≈ 1 — exactly where `max_quanta` is.
   At a representative η ≈ 0.1 this contributes ~2.5×10⁻⁴/quantum, **8× less than κ**, so it
   is not the dominant term.
2. **Residual spin–motion entanglement — the one that is *exactly* linear.** If the loop
   closes to α(t_g) = ε ≠ 0, tracing out the motion decoheres the spins by the motional
   state's characteristic function: `|⟨D(ε)⟩_th| = exp[−|ε|²(n̄+½)]`, so
   `1−F ≈ |ε|² + 2|ε|²·n̄`. **That is ε₀ + κn̄ with no expansion in n̄ at all** — affine for
   arbitrary n̄ as long as `|ε|²(2n̄+1) ≪ 1`. Inverting the repo's constant:
   `κ = 2|ε|² = 2.0e-3 ⇒ |ε| = 0.032` phase-space units, a very reasonable residual
   displacement, which then self-consistently predicts a contribution of 1.0×10⁻³ to a
   measured floor of 1.84×10⁻³ — i.e. the H2 infidelity splits ≈54% motional / ≈46% not.
   *(That inversion is an inference made during this audit, not a repo claim.)*
3. **Mode drift / anharmonicity.** ω(n) ≈ ω₀(1 − ξn) mistunes δ on a hot mode, which feeds
   back into (2) with ε ∝ n — converting the linear term into a quadratic one at large n̄.
   Not modelled anywhere; silently folded into κ. *(This is also the mechanism behind R13:
   more ions ⇒ denser mode spectrum ⇒ smaller δ ⇒ longer t_g — "gate time degrades sharply
   above ~15".)*
4. **Heating during the gate.** `ṅ̄ = S_E(ω)e²/4mℏω` — the formula every arch file quotes.
   At the repo's 0.05 quanta/ms over a 25 µs gate this is 1.25×10⁻³ quanta, i.e. 0.14% of
   ε₀: **negligible per gate**. The replay charges it to the *next* gate by snapshotting
   `quanta_at_start` before the anomalous bump, and R7 and R16 read the same snapshot.
   But the same rate over 267 ms of rotation is **13.35 quanta per ion from idling alone** —
   13× R7's budget before a single shuttle is charged.

#### (c) Why linear is nevertheless right *here*

- mechanism (2) is exactly affine in n̄, with slope exactly twice its own intercept
  contribution, and §(b) argues it dominates;
- mechanism (1)'s leading branch is linear precisely in the budgeted region — its quadratic
  branch only matters at n̄ ≈ 1, which is where **R7 stops admitting gates**. The quadratic
  form is not wrong, it is *unreachable*;
- mechanism (4) is n̄-independent per gate, so it lands in ε₀, not κ;
- mechanism (3) has no closed form, but ε(n̄) is smooth with finite ε(0), so its Taylor
  expansion *is* ε₀ + κ′n̄ + O(n̄²).

So `ε(n̄) = ε₀ + κn̄` is a two-parameter fit whose **functional form is derived** and whose
**domain is enforced by R7**.

Where it breaks down, stated plainly:

| regime | what breaks | direction |
|---|---|---|
| n̄ ≳ 1 | (1)'s quadratic branch; (3) turns (2) quadratic | ε **under**estimated |
| n̄ ≳ 1/η² (~100) | Lamb–Dicke fails; `L_n(η²)` oscillates and changes sign | ε not even monotonic |
| ε → O(1) | an infidelity is a probability; the true curve saturates | ε **over**estimated, unboundedly |
| always | ε is evaluated at ⟨n⟩ but (1) depends on Var(n) | see (d) |

#### (d) The thermal assumption, and why R15 has a cosine

The raw note's `P(n) = e^{−nℏω/kT}` is the unnormalized Boltzmann weight; normalized it is
the geometric distribution `P(n) = n̄ⁿ/(1+n̄)^{n+1}`, with `⟨n⟩ = n̄` the **Bose–Einstein**
occupation (not `k_BT/ℏω`, which is only the high-temperature limit) and
`Var(n) = n̄(n̄+1)`.

But **transport is not thermal.** Moving the trap minimum along a designed waveform is a
deterministic, known forcing: non-adiabatic residue leaves a **coherent state** — definite
amplitude *and definite phase* — with `⟨n⟩ = |α|²` and `Var(n) = |α|²` (Poissonian, narrow).
Entropy is unchanged; energy was put in coherently, and coherent energy can be taken back
out. Displacements compose as amplitudes:

```
D(α₁)D(α₂) = e^{i Im(α₁ᾱ₂)} D(α₁+α₂)
⇒ n̄_tot = |α₁+α₂|² = n̄₁ + n̄₂ + 2√(n̄₁n̄₂) cos θ
```

**That is R15, character for character** — and it is the proof that the transport channel is
coherent. If transport heated thermally no cross term could exist. θ is the secular phase
between the two events; at θ = π with equal amplitudes the second transport *exactly undoes*
the first, which is PLAN §0.3's "a phase-aware schedule can cancel excitation" and the
0.36 ± 0.08 quanta measured for a fast optimized round trip (2201.07358).

A sharpening the docs do not currently make: **additive composition is not uniformly loose.**

| component | character | additive composition is… |
|---|---|---|
| `anomalous` | incoherent diffusion, phase-random | **exact** |
| `shuttle`, `junction`, `split_merge` | coherent displacement | upper bound, tight only at θ = 0 |
| `gate` | declared but never populated | n/a |

The components are already tracked separately, so a correct R15 is a change confined to
`Charge.then`.

**And what n̄ means when the two mix.** A displaced thermal state has
`⟨n⟩ = n̄_th + |α|²` but `Var(n) = n̄_th(n̄_th+1) + |α|²(2n̄_th+1)`. The repo's per-ion n̄ is
`⟨n⟩` and nothing else — the correct first moment, and exactly the right argument for
mechanism (2), which depends only on the mean. It is **not sufficient** for mechanism (1),
which depends on the variance: a coherent state with |α|² = 1 has Var = 1; a thermal state
with n̄ = 1 has Var = 2. Same mean, twice the Debye–Waller infidelity. Two ions the platform
reports as equally hot can differ by an order of magnitude at n̄ = 10.

#### (e) Sanity-checking the numbers **[verified]**

```
ε₀ = 1.84e-3   κ = 2.0e-3 /quantum   budget = ms_gate.max_quanta = 1.0

  n̄        ε(n̄)      ε/ε₀    heating share
  0.00    1.84e-3    1.00       0 %
  0.92    3.68e-3    2.00      50 %   ← κ·n̄ = ε₀ exactly
  1.00    3.84e-3    2.09      52 %   ← R7's budget
  2.00    5.84e-3    3.17      68 %   ← the loose end of PLAN §0.4's 1–2
 10.00    2.18e-2   11.9       92 %
499.08    1.000     543               ← ε crosses 1
1747      3.496    1900               ← NOT A PROBABILITY
```

**Reading 1 — the budget is exactly the crossover.** The heating term equals the intrinsic
floor at n̄\* = ε₀/κ = **0.92 quanta**, and R7's budget of 1.0 sits 9% past it. So
`max_quanta = 1.0` has a clean operational meaning nobody wrote down: **R7 refuses a gate as
soon as heating becomes the majority of its error.** That is a far better justification than
"the deck says 1–2", and it also justifies the linear extrapolation — the model is asked to
extrapolate at most 9% past the point where its two terms are equal.

**Reading 2 — the deck schedule is far outside the domain.** `gate_error` has no clamp:
`return eps0 + value * max(nbar, 0.0)`, unbounded above. It crosses 1 at n̄ = 499 and returns
**3.496 at the deck's 1747 quanta** — a number 3.5× larger than certain, being summed into an
objective. `ε(1747)` must never be printed as an infidelity. Preferred fix: report quanta and
the budget ratio ("1747 quanta, 1747× the budget") and make `gate_error` refuse above
`max_quanta` — which is exactly the discipline `qccd/analysis/budget.py` already applies to
an unattributable channel ("Verify, don't assume"). Clamping alone would be *worse*: it turns
nonsense into a plausible-looking 0.75.

**Reading 3 — the floor is not near 1 either.** Even with perfect cooling, 864 contacts at
the H2 fidelity give `864 × 1.84e-3 = 1.59` nats, F ≈ 0.20 per syndrome round. That is not a
bug — it is why a distance-12 code is there and why tier T3 exists — but the T2 scalar should
never be read as if it were close to 1.

#### (f) How it wires together

```
 R17 ──► anomalous n̄   (incoherent — additive composition is EXACT)
 R15 ──► shuttle + junction + split_merge  (coherent — additive is an UPPER BOUND)
          │
          └─► per-ion n̄ ─► R7  gate iff n̄ ≤ 1.0     (= the validity domain of ↓)
                         └─► R16 ε = ε₀ + κ·max(n̄_a, n̄_b)

 −ln F ≈ Σ_gates ε(n̄) + n·T_exe/T_coh + Σ_spam ε_spam
```

`max(n̄_a, n̄_b)` is the conservative reconciliation of per-ion bookkeeping with a
**shared-mode reality** — the mode belongs to the crystal, not to either ion, so a per-ion n̄
is strictly a category error for a co-located pair. What it cannot capture is that after a
merge the combined crystal's occupation is set by the merge dynamics, which is what the
6-quanta split/merge charge is doing as a lump sum.

**A finding this framework produces:** with T_coh = 600 s and ṅ̄ = 0.05 quanta/ms, runtime's
dominant cost is **not** idling dephasing (7.5×10⁻² over the whole program) but the anomalous
heating runtime accrues, which multiplies through κ into every later gate (≈23 nats) — a
factor ~330. PLAN §0.2 adopts Jones & Murali's "steps → idling, cost → heating" proxy split;
under these constants both are proxies for heating, which *strengthens* §0.2's "there is one
objective" conclusion by a route §0.2 does not use.

---

### 2.4 The two broadcasts, and why a rectangle cannot rotate on one channel

Broadcasting is the whole parallelism story of a QCCD machine, and the repo had been
treating it as one thing. It is two, on two different pieces of hardware:

| | **movement broadcast** | **laser broadcast** |
|---|---|---|
| hardware | DC transport electrodes | a fibre under the chip, split 2ⁿ ways |
| what one drive does | moves many ions **the same way** | applies the **same gate** at many sites |
| what fixes the operation | (type, direction) — R4 | one laser, one frequency |
| what bounds the count | `max_simd_classes_per_cycle` — R4 | the splitter fan-out |
| producible? | R4d (per cycle), R19 (per device) | R4c's optical clause |
| opting out | per-site switch — R4d | `optical.per_zone_switch` |
| kept apart | — | R4b: never in one cycle |

**The rotation problem.** You cannot rotate a rectangular loop on one movement broadcast.
A rigid `+1` shift of `ring144_24v` moves ions in **four** lab-frame directions —
RIGHT 71, LEFT 71, UP 1, DOWN 1 — and one waveform cannot produce four. **[verified]**

`path_actions` did not see this, because it measures displacement in *loop-slot order*:
all 144 moves come back labelled `L0:+1`, one distinct action, and R4d passed the cycle.
Under the shipped `grouping: "broadcast"` — where channel `linear_h.all.0` drives all 168
sites — that is a false green on a cycle needing four waveforms.

**But the four directions cost four *channels*, not four *cycles*.** This is the
correction that matters, and it is easy to get wrong (I did). Give the device four
channel groups cut from `Device.shift_directions` and the same rotation verifies clean in
**one cycle**: **[verified]**

| channel map on `ring144_24v` (144 slots) | groups | rigid rotation |
|---|---|---|
| path frame (H2's conveyor) | 1 | legal, 1 cycle |
| lab frame, one direction of travel | 4 + spur = **5** | legal, 1 cycle |

| lab frame, **both** directions | 6 + spur = **7** | legal, 1 cycle |
| shipped `broadcast` (all sites, every channel) | 1 independent block | **illegal** |

Seven channels for 144 slots against 168 for direct wiring: **WISE's O(1)-in-array-size
claim survives the lab frame intact.** The phase count is a property of the *declared
wiring*, not of the geometry — a device with too few groups must serialise, which is the
corner-then-rows schedule, but that is a consequence of its channel map and not a law.

**Why both directions cost more than one.** The sharpest consequence, and not obvious:
`+1` and `−1` do **not** partition the sites the same way. They are offset by one at the
corners, so the site that goes `+y` under `+1` is not the site that goes `−y` under `−1`,
and a four-group map cut for `+1` asks one of its channels for two waveforms under `−1`.
A device that must turn both ways needs the **common refinement** — 6 groups
(70/70/1/1/1/1) on the shipped ring. R19 found this; I had assumed four would do.

**The serialised schedule, when you are short of channels.** With one broadcast group the
rotation must run as phases, and the ordering is constrained — you have to vacate before
you fill. Of the 24 orderings of the four direction groups, **[verified]**

- `cyclone_base`: **24 of 24** legal. No corner is a junction.
- `ring144_24v`: **6 of 24** legal. Two of its four corners (`S0`, `S72`) are degree-3
  docks, and R2 permits one ion on a junction, so the rows must move *before* the corner
  ions that land there.

That is a fourth independent argument for `q_inline_vs_hanging_ancillas`: the verticals do
not merely tax the rotation path, they cut the legal schedule space by 4×.

**Which frame is which is now declared, not assumed.** `control.channels.frame` defaults
to `"path"`, because that is what the repo's own H2 citation describes — *"RF tunnels
letting DC electrodes tile the full perimeter"*, curved ends driven by the same `{a,b,c}`
tiling as the straights (2305.03828). A machine whose electrodes are fixed to the chip
axes declares `"lab"` and pays one channel group per axis direction the loop turns
in. On every loop the fleet ships that equals `len(corners(loop))` = 4, but the two
are different quantities — an L-shaped closed loop has 6 corners and 4 directions.
Both readings are expressible; neither is hard-coded.

---

## 3. The rules, one by one

Reorganized from the raw discussion. **Kind** matters for the redundancy question: rules of
different kinds cannot be redundant with each other even when they talk about the same
physics.

| kind | meaning | members |
|---|---|---|
| **P** — program invariant | a schedule can violate it | R1 R2 R3 R4 R4b **R4c** R4d R5 R6 R6b R7 R7c R8 R11a R12 R13 R14 |
| **A** — architecture | a device violates it with no program at all | R11b **R19** |
| **M** — cost-model contract | only a model can violate it | R15 R16 R17 R18 |
| **S** — self-consistency | claim vs replay | R9 |
| **C** — circuit correctness | out of tree, discharged by the Lean checker | R10 |

**R1** — occupancy ≤ capacity, at two instants: where ions come to *rest*, and where a
multi-segment route passes *through* (the ROADBLOCK clause — the constraint 2511.15910 is
organized around). Load-bearing; the only rule that looks at transit occupancy at all.

![R1](img/rules/R1_capacity.gif)

![R1](img/rules/R1b_roadblock.gif)


**R2** — a junction is exclusive. Two clauses: ≤ 1 ion resting on a degree-≥3 node, and ≤ 1
ion crossing one per cycle. The raw note ("every time one ion through junction; EC electrode:
only one direction to another direction") is clause B. **A junction is a router** — its
electrode configuration realises one in-direction→out-direction map per cycle.

![R2](img/rules/R2_junction_exclusive.gif)


**R3** — ≤ `segment.capacity` ions per segment per cycle. **Not the same as R2** (§4).

![R3](img/rules/R3_segment_capacity.gif)


**R4** — the class is declared; ≤ `max_simd_classes_per_cycle` active. This is the broadcast
rule the raw notes emphasise: **declare the movement type (direction, amount) first, then the
participant set**. Distance is unbounded — a shift can cross the whole chip. Two independent
broadcast groups (upper/lower rail) are two classes.

![R4](img/rules/R4_declared_class.gif)

![R4](img/rules/R4t_classes_over_time.png)


**R4b** — intra- and inter-trap transport never overlap, and **a cycle never mixes transport
with gates** ("when you do transport, you cannot do gates"). Distinct control pathways: DC
electrodes vs lasers.

![R4b](img/rules/R4b_intra_inter.gif)


**R4d** — drivable by the declared channel map. This is the "rotation/sidewalk" note: a
junction crossing has to be sequenced before the broadcast shift, because one channel cannot
ask its sites to do two different things at one instant.

![R4d](img/rules/R4d_drivable.gif)

![R4d](img/rules/R4d2_switch_per_site.gif)

**R4c** *(new)* — **a broadcast is a claim the instruction makes, and the device answers
it.** This is the two-broadcast distinction made checkable. An instruction may declare
`broadcast: "one"` (a single drive reaches every participant — H2's conveyor claim, and
what the legacy `broadcast: true` on a `cool` has always meant), `"per_direction"` (one
drive per direction the device's declared frame requires), or `"per_site"` (the
anti-claim: each participant driven independently, which a `direct`-wired array can do
and a broadcast-wired one cannot). The claim is an **intent** and nothing else — it names
no channel, because one rotation engages `linear_h`, `linear_v` and `junction` groups at
once and a singular field would be a category error; and it states no count, because the
count is a device property. The verifier computes what the device would need and reports
the disagreement.

![R4c](img/rules/R4c_broadcast_claim.gif)

![R4c](img/rules/R4c2_optical_broadcast.gif)

**R19** *(new)* — **a lab-frame tiling needs one channel group per direction it turns.**
An architecture rule, like R11(b): no program variable, fires with no program at all.
Which frame a device is in is *declared* (`control.channels.frame`, default `path`), so no
shipped device changes verdict without an edit, and every one of the nine reports an
explicit skip **reason** rather than a silent pass.

![R19](img/rules/R19_electrode_frame.png)

![R19](img/rules/R19b_both_directions.png)


**R5** — no exchange across one segment in one step.

![R5](img/rules/R5_no_exchange.gif)


**R6** — gate / measure / cool only where the `zone_type` allows. The raw note is exactly
right: gate zones are **discrete points**, set by where laser power can be delivered, and
data zones are labelled `gate: false`. Measurement and cooling are different wavelengths but
the ADL deliberately abstracts that to capability flags — *"we don't want to be specific
about wavelength, just specify this is cooling."* Cooling is available at every trap.

![R6](img/rules/R6_zone_capability.gif)


**R6b** — a 2Q pair must be co-located.

![R6b](img/rules/R6b_colocation.gif)


**R7** — n̄ ≤ `ms_gate.max_quanta` at gate time. See §2.3(e): the budget is the point where
heating error equals the floor.

![R7](img/rules/R7_thermal_budget.png)


**R7b** — per-zone thermal duty cycle. **Skipped, and not merely unimplemented**: the
parameter is *unrepresentable* — `_ZONE_TYPE` has no such field and `allow_extra` is `False`,
so an architecture that tries to declare one is **rejected by the validator**.

**R7c** — cooling is mandatory. The raw note is the physics: *"heating but no cooling is
wrong — in hardware, in the gate zone you shine another cooling laser."* Cooling is global
(Doppler sheet beams cover the whole trap), which is what makes the scheduling problem
tractable.

![R7c](img/rules/R7c_cooling_mandatory.png)


**R8** — ion→site is a bijection over time. Bookkeeping integrity, not hardware.

![R8](img/rules/R8_bijection.gif)


**R9** — claims equal the replay. See §2.1.

![R9](img/rules/R9_claims_vs_replay.png)


**R10** — the program implements the circuit. `docs/rules.md` still says `skipped`; it is now
discharged by a Lean-4-proved checker in `Compiler/`. **The two documents disagree.**

![R10](img/rules/R10_implements_the_circuit.png)


**R11** — (a) shuttling is unidirectional per loop per cycle — *"a ring can only move in one
direction"*; (b) structurally, every junction degree must be **priceable**.

![R11](img/rules/R11_unidirectional.gif)


**R12** — intra-trap parallelism = 1. The raw note identifies the mechanism correctly and it
is *not* the same as R4's: **optical** fan-out. Light goes through fibre to a waveguide
beneath the trap, and the 1→2ⁿ splitter geometry sets the maximum broadcast. R12 is the
optical analogue of R4, hardcoded to 1.

![R12](img/rules/R12_intra_trap_parallelism.png)


**R13** — ≤ ~15 ions in a trap at gate time. Mechanism in §2.3(b)(3).

![R13](img/rules/R13_chain_length.png)


**R14** — an ion must be at a chain edge to split; getting there costs a 3-CX swap. The raw
note has the hardware right: ions sit in a harmonic well, the DC electrodes are reshaped into
a double well, and the chain separates. At capacity ≤ 2 every ion is already at an edge and
the swap is free — **the rule exists so that raising capacity in a sweep does not silently
stop paying it.**

![R14](img/rules/R14_split_at_edge.gif)


**R15 / R16 / R17 / R18** — see §2.2 and §2.3. All four are **contracts on the cost model**,
not constraints on a program. No schedule can violate them, so there is no
illegal stage to draw: what they constrain is a *curve*, and every point below is computed
by calling the shipped model.

![R16](img/rules/R16_gate_error_vs_nbar.png)

![R17](img/rules/R17_anomalous_heating.png)

![R15](img/rules/R15_composition.png)

R18's figure is the degree figure in §1 — which is the point: R18 has no program variable
to draw, only a graph property to read off.

---

## 4. The audit: is every rule necessary? Is anything redundant?

Method: for each rule, construct a program that violates it and **check which rules fire**.
A rule is *load-bearing* if some program trips it and nothing else. All rows below were run.

### 4.1 Isolation results **[verified]**

| witness | fires | isolated |
|---|---|---|
| 2 ions from one site down one segment | `R3` | ✅ |
| 2 ions into one junction on **two** segments | `R2` | ✅ |
| 2 ions crossing a degree-4 grid junction on 4 disjoint segments | `R2` | ✅ |
| swap across a **capacity-2** segment | `R5` | ✅ |
| swap across any **shipped** (capacity-1) segment | `R3 R5` (+`R11` on a loop) | ❌ |
| opposite directions on one loop, different segments, **undeclared channels** | `R11` | ✅ |
| same, on `ring144_24v` (channels declared) | `R4 R11` | ❌ |
| 16 ions gated in a capacity-32 register | `R13` | ✅ |
| pair split across two gate-capable ancillas | `R6b` | ✅ |
| pair co-located in a `gate:false` zone | `R6` | ✅ |
| two pairs gated in one capacity-32 trap | `R12` | ✅ |
| undeclared movement class | `R4` | ✅ |
| transport + gate in one cycle | `R4b` | ✅ |
| two classes overlapping in time | `R4` | ✅ |
| intra + inter overlapping in time | `R4b` | ✅ |
| one ion listed twice in a cycle | `R8` (+`R11`) | ❌ but R8 alone names it |
| 3 ions into a capacity-2 site | `R1` (+`R11`) | ❌ fixture artifact |
| `via` through a full trap, no gate anywhere | `R1` | ✅ |
| split from a chain of 3 with no `gate_swap` | `R14` | ✅ |
| gate at n̄ = 5 | `R7 R7c` | ❌ |
| **cold** gate, zero cooling anywhere | `R7c` | ✅ |
| false `total_cost` claim | `R9` | ✅ |

**Every rule with a per-cycle implementation is load-bearing on some shipped device except
R5** (see below). Nothing in the set is dead weight.

### 4.2 The three redundancy claims from the discussion

**"R3 is the same as R2" — no. Independent, both directions, unconditionally.**
R2 ⊄ R3 is airtight: two ions crossing a degree-4 grid junction from different axes use four
*distinct* capacity-1 segments, each at 1/1, and the cycle is still illegal. A junction is a
router; per-link capacity cannot express that. R3 ⊄ R2 is the co-located, co-directional
pile-up (two ions at S1 both moving S1→S2), which no other rule reaches.
**One line each for the docs:** *R2 = "a junction is a router: one ion through it per cycle,
whatever its segments can hold"*; *R3 = "a segment is a link: at most `segment.capacity` ions
on it per cycle, junction or no junction."*
⚠️ The reason this looked redundant is a **test artifact**: `test_r3_fires_when_a_segment_carries_two_ions`
is a head-on **swap**, so it fires `R3 R5 R11` and never demonstrates R3 catching anything
alone. Same fixture in `test_commonsense.py`. Fix both.

**"R5 is redundant with R4" — right conclusion, wrong reason, and the real answer is R3.**
Two layers:

- *Implementation.* `r4_simd_classes` only checks that `instr.cls` is declared; it never looks
  at the moves. So R4-as-implemented cannot imply R5. What actually implies R5 on every
  shipped device is **R3**: at `segment.capacity == 1` an exchange is necessarily two moves on
  one segment. All nine devices have capacity 1 on every segment, so **R5 currently fires only
  in company with R3 — "none found" for an R5-only witness on the shipped fleet.**
- *Rule.* R4 **as stated** — "a class fixes (type, direction)", `max_simd_classes_per_cycle = 1`
  — does entail R5 at the shipped operating point, because two opposite directions are two
  classes. So the note is right at the rule level and the implementation is what diverges.
  At `k ≥ 2` (the deck gives WISE 2, C2LR 4 — `q_simd_class_budget`) that entailment
  disappears.

**Verdict: keep R5, re-file it against R3 and against R4-at-k≥2, never against
`r4_simd_classes`.** Restate it so it says what it uniquely says — *"no two hops traverse one
segment in opposite directions in one cycle: a 1D channel is order-preserving"* — and document
that it is R3's shadow on today's fleet and the only guard at `segment.capacity ≥ 2`. Deleting
it would be a bug the moment a sweep widens a segment.

**"R11 is redundant" — half true, and the half that survives is the important one.**

- R11(b), the structural half, is an **architecture** check with no program at all. It cannot
  be redundant with anything per-cycle. Consider renumbering it.
- R11(a) vs R4d: on `ring144_24v` **[verified]** both fire on the same violation, so R4d does
  subsume R11(a) there. But R4d is silent in three situations that ship today:
  1. **no `control.channels` declared** — `chain`, `stationary_chain`. R11(a) fires, R4d
     returns `[]` at its guard. **[verified]**
  2. **per-site `direct` wiring** — `drivable` is a tautology when every channel drives one
     site;
  3. **two ions leaving the same site in opposite directions** — `path_actions` is keyed by
     source, so the second move **overwrites the first**. **[verified]** on `ring144_24v`,
     `a: S102→S101` + `b: S102→S103` gives `path_actions == {'L0': {'S102': +1}}`, R4d emits
     **zero** violations, and the control panel reports `feasible=True` for a cycle asking one
     trap to push one ion left and one right at the same instant. Only R11(a) catches it.
- Conversely R4d catches something R11 cannot: *"some sites would have to idle while their
  channel-mates move"* (the `switch_per_site: false` branch). No shipped architecture
  exercises that branch.

**Verdict: keep both.** R11(a) is the only per-cycle check that compares ions' displacements
**to each other**; R4d compares **sites to the channel map**.

### 4.3 The other pairs examined

| pair | verdict | why |
|---|---|---|
| R12 vs R4 / R4b | **independent, structurally** | domains are disjoint over `INSTRUCTION_TYPES` — R12 dispatches on `type=="gate"`, R4/R4b on `type=="simd"`. No program on any device can make both fire on one instruction. Verified on all nine devices. |
| R13 vs R1 | **independent, but only on one device** | on a gate cycle `occ_after == occ_before`, so for any capacity ≤ 15 R13's rejection set is a **strict subset** of R1's — enumerated over cap 1..15 × occ 0..39, zero states fire R13 alone. R13 is live because `stationary_chain` ships a `register` zone at capacity 32 — **[verified]** the only gate-capable site above capacity 4 on any of the nine devices. Two different facts about one trap: how many ions the potential holds (R1) vs how many the MS gate can address (R13). |
| R6b vs R6 | **independent on the whole fleet** | **[verified]** R6's gate clause *cannot fire at all* on `chain72`, `cyclone_base`, `h2_racetrack`, `stationary_chain` — every node on those four is gate-capable — while R6b fires freely; R6 fires without R6b on the other 5. Do not merge: a merged "R6: passed" would certify only one half, with nothing in the summary saying which. |
| R7 vs R16 vs R7c | **all three needed** | R16 is a *model* assertion, so "redundant with R7" is a category error. R7 ⊬ R7c and R7c ⊬ R7 both have isolating witnesses — R7c fires on a **cold** gate with no cooling, R7 fires on a hot gate in a program that does cool. R7's real job is not a physical cliff: it is the **domain marker for R16's fitted curve** (§2.3e). |
| R15/R16/R17/R18 | **not redundant — but not rules either** | none constrains a program; all four are cost-model contracts, and none can currently fail. See §5. |

---

## 5. Defects found while auditing (all **[verified]** by running)

These are the actionable output. None changes a design conclusion; several are false greens,
which the repo's own contract calls worse than no verifier.

1. **`r4_drivable` labels its violations `"R4"`, but is registered under the key `"R4d"`.**
   On the cycle where it produced 32 violations, `failed == ['R11','R4']` and **`'R4d'` is in
   `passed`**. Emit `Violation("R4d", …)`.
2. **`only_rules=["R4b"]` reports R4b as *passed* on a program that violates R4b.**
   `concurrency_violations` — the temporal half of *both* R4 and R4b — is gated on `"R4"`
   alone.
3. **`path_actions` drops a second move out of the same site** (dict keyed by source). R4d
   goes silent and the control panel says `drivable`. Key by `(src, ion)` or accumulate a set
   of deltas.
4. **R4's per-cycle budget clause `if 1 > limit` is unreachable** — the schema declares
   `min: 1`, so `max_simd_classes_per_cycle = 0` is rejected at load. The real R4 budget check
   is the temporal one.
5. **`init` and `barrier` build no `CycleView`, so no rule runs on them.** A program that
   inits 5 ions into a capacity-2 trap, or 2 ions onto a degree-3 junction, fires **nothing**.
   Append any other instruction and R1 fires correctly. R1/R2 are necessary and currently
   under-applied.
6. **`gate_sites()` unions the declared `sites` annotation into the replayed positions**, so
   R6/R13 do not evaluate "at the replayed site" as `rules.py` and `docs/rules.md` both claim.
   Conservative (over-rejection only), but the documented contract is wrong.
7. **R16 and R17 are self-certified.** `verify` reads a boolean the model declares about
   itself (`models_heating`) and writes them into `checked`. A `CorrectedModel` subclass whose
   `gate_error` ignores n̄ entirely still reports **R16 passed**; the shipped, supported
   `corrected_model(include_anomalous=False)` does the same for R17 with no subclassing. Both
   have cheap real probes: `model.gate_error(arch,1.0) > model.gate_error(arch,0.0)`, and
   `anomalous_per_us > 0` whenever `arch.anomalous_rate() > 0`.
8. **R17 is gated on `models_heating` but skipped with the message "does not model elapsed
   time"** — and `docs/rules.md` says it is checked when the model models *time*. Three
   descriptions, two behaviours.
9. **R15 is reported `partial` *and* `passed` simultaneously.** `passed()` filters `failed`
   and `skipped` but not `partial`, so R15 appears in the `passed` list three lines above the
   contract saying a rule passes only if the check ran. `PARTIAL` is also applied
   unconditionally — under `DeckModel`, which deposits no quanta at all, R15 still claims to
   be partially checked.
10. **R18 is `rules.checked.add("R18")`** and nothing else — §2.2.
11. **R13 fires on single-qubit gates** with a message that says "2Q gate". Gate it on
    `iter_pairs` the way R7 does.
12. **`gate_error` has no validity domain** and returns 3.496 for the flagship schedule
    — §2.3(e).
13. **κ = 2.0e-3 is unsourced.** It appears in nine arch files, `PLAN.md` and `models.py`; the
    `source: 2305.03828` on the block covers `fidelity_at_n0`, and the note is silent on κ.
    PLAN §12 states the goal of keeping `query.py unsourced` at zero.
14. **`R4d` appears in no rule table.** `docs/rules.md` has 22 rows and no R4d row; PLAN §5
    runs R1…R17 and has neither R4d nor R18. `README` and `qccd/README` say "23 rules".
15. **R10 is documented as `skipped` in `docs/rules.md` and as `passed` in `Compiler/README`.**
16. **`R14` blames the wrong hop on a multi-segment route** — `ResolvedMove` carries `entails`
    on every segment while the cost model charges it only on the first.

---

## 6. Recommendations

**On necessity and redundancy — the headline: nothing should be deleted.** Every rule with an
implementation catches something no other rule catches, on some device the repo ships. The
three suspected redundancies resolve as: R3/R2 genuinely independent (the appearance came from
a bad test); R5 subsumed **by R3, not R4**, on today's fleet only, and load-bearing the moment
a segment widens or `k ≥ 2`; R11(a) subsumed by R4d **only where channels are declared**, which
is 7 of 9 devices, and the sole check on the other 2.

What the audit says to change is not the rule *set* but its **filing and its honesty**:

1. **Give the rule list a taxonomy** — program invariant / architecture / cost-model contract /
   self-consistency / circuit correctness (§3). Most of the felt redundancy is cross-kind
   confusion: R16 "overlapping" R7, R18 "overlapping" R2.
2. **Add `constructed` and `vacuous` report states** to `RuleReport`, so R15/R16/R17/R18 stop
   appearing in `passed`. The browser engine already has both; this closes a real divergence.
3. **Make R16/R17/R18 fail** with the three-line probes in §5(7) and §2.2.
4. **Fix the four false greens** — §5(1), (2), (3), (9).
5. **Rewrite the R3 and R5 negative tests so each fires exactly one rule.** The
   isolation assertions from §4.1 are now permanent regressions in
   [tests/test_rule_figures.py](../tests/test_rule_figures.py): every figure's declared
   verdict is re-checked against the verifier, and `test_every_implemented_rule_has_an_
   isolating_figure` fails if any per-cycle rule loses the programme that trips it alone.
   That is what pins this audit. The remaining work is in `tests/test_rules.py` itself,
   whose R3 fixture is still a swap.
6. **Declare `error_vs_quanta`'s validity domain** and make `gate_error` refuse above it. The
   coupling between `max_quanta` and κ is the entire argument for using a linear law and it is
   nowhere in the schema.
7. **Bring the docs into line**: add R4d and R18 rows, reconcile R10, and state that
   `gate_sites` is replayed ∪ declared.

## 7. Still open

- `q_simd_class_budget` — is `max_simd_classes_per_cycle` really 1? It decides whether R5 is
  entailed by R4 at the rule level (§4.2).
- `q_heating_rate_measurement` — the two primitive tables differ by 2–3×. Blocks a real R15.
- The secular-phase model R15 needs. Note that a correct R15 is confined to `Charge.then`, and
  that `anomalous` should stay additive because it is exactly additive (§2.3d).
- `q_inline_vs_hanging_ancillas` — decided by R18 in principle (§2.2); confirm by compiling
  both.
- Whether R13 should be a **rule** at all, or a chain-length term in the gate-duration curve
  with the cliff declared by the architecture rather than hardcoded as `limit=15`. "Gate time
  degrades sharply" is a cost statement, not a legality statement.

---

## 8. How the figures are made

[`tools/make_rule_figs.py`](../tools/make_rule_figs.py) with the table in
[`tools/rule_figs_spec.py`](../tools/rule_figs_spec.py). Rebuild everything with:

```bash
python tools/make_rule_figs.py --all          # -> docs/img/rules/
python tools/make_rule_figs.py --only R3 R5   # just these
python tools/make_rule_figs.py --list         # what would be built
```

It is **not a second renderer** — geometry comes from `qccd.viz.layout.compute_layout`,
frames from `qccd.viz.render.build_view_model`, and the stage is advanced by
`make_gif.Clip`, the same object the gallery clips use. It adds exactly four things: node
labels, a highlight ring, an instruction listing, and a verdict band.

**The instruction listing.** Every panel prints the whole hardware program with a cursor
on the executing step — `instruction_text()` renders each TSIR `Instruction`, and the
frame's own `id` places the cursor. It is deliberately rendered from the `Instruction`
dataclass rather than from the view model's frame: the frame is a drawing instruction, and
what a reader debugging this needs is the object `verify()` actually read. Long operand
lists print the first four and a count (`i0@C0, i1@C0, i10@C0, i11@C0, (+11 more)`), which
is how the R13 figure distinguishes 15 ions from 16 when counting circles is hopeless. A
template prints as `template=loop_shift(loop=L0, delta=+3)` while the verdict band reports
how many machine cycles the replay charged it as — one instruction, three cycles, both on
screen. Where the legal/violating difference is a program-level *claim* rather than an
instruction (R9), the `metrics=` row carries it.

The design rule that makes the figures worth trusting: **no caption is written by hand.**
The red line under a violating panel is `str(Violation)` straight out of `verify()`; the
degree histograms, junction sets, corner segments and per-ion n̄ are read off the device
and the replay. And every `Case` declares the rule set it expects, so a figure whose
verifier disagrees is *refused* rather than emitted:

```
R3_segment_capacity          REFUSED: VIOLATION: expected rules ['R3'] to fire, got ['R3', 'R5', 'R11']
```

Two things fell out of building them, which is the argument for building them at all:

- **R4d had no isolating figure**, because it emits under R4's name *and* R11 co-fires on
  any wired device. Hunting for one produced `R4d2_switch_per_site` — the same one-ion
  move, legal with per-site switches and illegal without — which is the only cycle in the
  whole set that R4d rejects and every other rule accepts. It is also the WISE trade in
  one picture.
- **The R4d figure displays defect §5.1 directly**: the violation reads `[R4]`, not
  `[R4d]`.
