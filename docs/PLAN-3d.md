# 3D QCCD: machines built in layers, and a stage that shows them

Status: **decided and partly built** · 2026-11-10 · branch `qccd-3d`, cut from `compiler` at
`bc282b5`. The questions of §5 were answered the same day, and three of the answers differ
from what §1 to §3 recommend: where they do, §5 and §7 are right and the earlier section is
kept as the record of the plan. §7 says what is built, what is not, and what must happen
before a merge. §0 reports measurements from a throwaway prototype that is not in the
repository (`out/spike3d/`, git-ignored; open `out/spike3d/spike3d.html`).

**The idea.** A machine is a stack of planar trap layers. A junction may join up to six rails:
up to four in its own layer, one up and one down. A vertical rail between two layers is called
a **riser** in this plan, because "via" is taken twice already: it is the segment list of a
move in TSIR and the bend points of a road in the floorplan language. An ion that has far to
go, or a partner the plane keeps far away, changes layer instead of crossing the chip. Layers
need not be alike: a compute layer under a layer that is only a highway is one such hierarchy.

**What the plan commits to.**

1. A machine document carries a third coordinate, and everything that reads a position reads it.
2. The rules stay the rules. No programme rule changes. The three geometry rules get their 3D
   statement, the direction label gains `+z` and `-z`, and the price list gains two entries.
3. The compiler compiles onto a layered machine as it does onto any graph.
4. The page gets a 3D stage beside the SVG stage: drag to rotate, wheel to zoom, pan, play,
   step. One switch goes back to 2D, where a layered machine is drawn as one panel per layer.
5. Both stages draw the same positions, taken from the one function that computes them today.

The stage is the main deliverable, and four of the six phases are about it.

---

## 0. What was checked before planning

### 0.1 The trunk already expects layered traps, as a convention only

| what exists | where | what it lacks |
|---|---|---|
| R19 caps a junction at six rails by default, "a trap built in more than one layer has them" | `qccd/verify/rules.py:928` | nothing; it is the right rule already |
| A crossing of five or six rails is charged the X-junction's price | `FALLBACK_DEGREE = {5: 4, 6: 4}`, `qccd/arch/curves.py:136` | a price of its own |
| A two-layer design is drawn flat and judged under a rule set that drops R19, R20 and R21 | `docs/rule-language.md:103`, `tests/test_declared_rules.py:27` | any crossing check at all, and a picture that does not overlap itself |
| The schema admits one to three coordinates | `qccd/arch/schema.py:95` | a reader for the third |

So a two-layer machine can be entered today, but its vertical move is free, its planarity is
unchecked, and its drawing lays one layer over the other. A real third coordinate lets R20 and
R21 be kept in place of being dropped.

### 0.2 Who reads a position

| component | third coordinate today | file |
|---|---|---|
| `Node.pos`, corners, `edit.py`, `edit.js`, `move_site` | carried; `_unit` zips two positions and silently cuts to the shorter | `qccd/arch/device.py:74` |
| `DeviceBuilder`, browser `_dBuild` | refused: "needs exactly two coordinates" | `qccd/arch/builder.py:39`, `engine.js:2034` |
| Browser export, statements, layout input | dropped without a word | `engine.js:643, 2410, 6285, 6518` |
| Page payload | only `x` and `y` are shipped | `qccd/viz/render.py:243` |
| Generators, components, library, variants | two coordinates by construction | `qccd/arch/generators.py` |
| R20, R21 and the rule-language words `angle`, `lies_on`, `cross` | projected to the plane by `_xy` | `qccd/verify/rules.py:948` |
| R22 and R4d direction label | `direction_label(dx, dy)`, mirrored in JS and OCaml | `rules.py:1171`, `engine.js:5908`, `route.ml:178` |
| Compiler loader | a three-coordinate position becomes `(0, 0)` | `Compiler/ocaml/lib/arch.ml:105` |
| Compiler passes, placement, Lean checker | graph only: hop counts, capacities, loops | `traps.ml:91`, `Cert/Syntax.lean:80` |
| Electrode layout and GDSII | refused with a clear message; one metal plane by design | `qccd/phys/build.py:227` |

Every other device rule (R0 to R18, R23 and above) reads the graph, the occupancy and the
clock, and never a coordinate. Two families outside this plan do read the plane and stay
planar: the C2LR rules C1 to C10, which read their lattice from `x, y`
(`qccd/arch/c2lr.py:182`), and the floorplan rules F3 and F4.

### 0.3 How the page draws and animates

- The stage is SVG. The device is built once by `buildStatic`; each ion is a pooled set of marks
  that `draw()` moves with `setAttribute` (`render.py:2362, 2993, 3084`).
- The playhead is two globals, `frame` and `phase`. One call gives every ion's position:
  `TRANSIT.place({before, pos, paths, t, ...})` (`render.py:3128`, `js/transit.js:764`). It has a
  Python twin, `qccd/viz/transit.py`, held to 1e-9 by `tests/test_transit_parity.py`. The README
  GIFs come from that twin through Pillow; no browser is involved (`tools/make_gif.py`).
- A page is one self-contained file: about 1.42 MB of template and our own JavaScript, plus
  0.3 to 2.9 MB of data. `render_html` raises if the page contains `<script src=`, `fetch(`,
  `XMLHttpRequest` and four more strings, anywhere, comments included (`render.py:894`).
- The page stamp is a hash of the template and the six inlined JS files. A change to any of them
  means rebuilding every committed page: **204 pages, 385 MB** in `BBResults`, `SmallCode` and
  `MemoryBoard`.
- Editing is two-dimensional throughout: the pointer is mapped through the SVG viewBox to
  lattice `x, y`, and a drop emits `move_site [id, x, y]`.
- A frame's time on screen is a display rule (`durOf`, `render.py:4049`: it grows with the square
  root of the hop count), not the replay's microseconds.
- The committed pages reach 580 nodes and 288 ions, and at most 20,000 frames (a hard cap). The
  gadget and RSA-2048 pages, with thousands of ions, are a different renderer: Canvas2D in
  `qccd/gadget/web/`, not `render_html`.

### 0.4 A prototype, timed

A single self-contained page: a stack of `grid(a, b)` layers with a riser at every second
junction, ions hopping between free traps through junctions and risers, three.js with one
instanced mesh per kind of part, orbit controls, and a render loop that stops when nothing
moves. Timed in headless Chrome over the DevTools protocol, the way `tests/gadget_browser.mjs`
drives a real Chrome. Canvas 1600 × 900, pixel ratio 1, 240 frames, GPU forced to finish each
frame before the clock is read.

| machine | nodes | ions | update, ms | draw, ms | GPU buffers | JS heap |
|---|---:|---:|---:|---:|---:|---:|
| 2 layers of 9 × 9 (BB144-sized) | 450 | 168 | 0.01 | 0.36 | 0.09 MB | 4.9 MB |
| 4 layers of 30 × 30 | 10,560 | 3,000 | 0.09 | 0.74 | 1.8 MB | 11.9 MB |
| 4 layers of 100 × 100 | 119,200 | 20,000 | 0.79 | 3.08 | 19.3 MB | 56 MB |
| 8 layers of 100 × 100 | 238,400 | 95,040 | 4.60 | 6.97 | 42.8 MB | 104 MB |

That is on this workstation's GeForce RTX 4070, which is a strong card. With the GPU taken away
(Chrome's software renderer) the first row draws in 25 ms and the second in 417 ms.

What follows from it:

- On a discrete GPU, memory and compute are not the constraint at any size this project draws.
  The largest page today is the size of the first row.
- GPU memory is about 64 bytes per node or segment and 76 bytes per ion, one matrix each. The
  heap in the last two rows is the prototype's own graph arrays, not the renderer.
- Draw calls stay between 10 and 22 at every size, because each kind of part is one instanced
  mesh. That is the property to keep.
- Without a GPU, 3D is usable only at today's page sizes. The page has to detect a software
  renderer and say so; §2.8.
- The prototype draws each ion as a 252-triangle sphere. Drawing it as a shaded disc cuts the
  second row from 1.08 million triangles to about 0.33 million. That is the first thing to do if
  a budget is missed.

The library, measured (minified, bundled with esbuild from the published npm packages):

| | bundle | gzip | licence | still to write ourselves |
|---|---:|---:|---|---|
| three.js 0.187.0, only the WebGL renderer, instancing, standard material, orbit controls, ray picking | 572 KB | 141 KB | MIT | nothing |
| OGL 1.0.11, the same role | 63 KB | 19 KB | Unlicense | lit materials, picking of instances, labels |
| regl 2.1.1 | 87 KB | 28 KB | MIT | camera, controls, geometry, and the above |

The three.js bundle contains none of the strings `render_html` refuses, and no `eval(`,
`new Function` or `Worker(`, so it passes the workspace's content security policy
(`script-src 'unsafe-inline'`, `qccd/workspace/service.py:1087`) as it is.

### 0.5 What is published about moving an ion vertically

A short search, not a literature review; the knowledge graph should take these in properly.

- No experiment was found that moves an ion between two stacked trap layers. Two US patents on
  multi-level trap devices came up (11721537, 12080541), one describing an opening in a
  substrate through which ions pass between a first-level and a second-level trap. They are
  claims, not results, and were not read in full.
- arXiv:2603.06208 (simulation, ¹⁷¹Yb⁺) changes an ion's height above one chip from 71 to 141 µm.
  Its point for us: a DC push adds micromotion, so moving an ion vertically means moving the RF
  null itself, by the shape of the RF electrodes (their "escalator") or by extra RF voltages
  (their "elevator"). A riser is not a matter of DC waveforms alone.
- arXiv:2604.21350 reports, in its abstract, a 134 µm to 86 µm move within 500 µs with fewer than
  eight quanta. The full text was not read, so whether this is simulation or experiment is
  unconfirmed. It is the only time and heat figure found, and it is 100 times a 5 µs shuttle.
- arXiv:2308.07915: the Tanner graph of every bivariate bicycle code has degree six and
  "consists of two edge-disjoint planar subgraphs". Two layers are the natural machine for it.
- arXiv:2404.17676 proposes a two-layer architecture for qLDPC codes on hardware with restricted
  movement and finds bivariate bicycle codes well suited to it. It is not a trapped-ion design.

### 0.6 What this repository's own numbers say about the motivation

- For transport dominating at the device level: yes. The shipped BB [[144,12,12]] schedule spends
  99.6% of its cost in rotation, and each data ion crosses a junction 445 times a round, which is
  1,336 of its 1,747 quanta (`docs/PLAN.md` §0.1, §0.4). Junction heating is 76% of the summed
  gate error (`qccd/README.md`).
- For Shor at the gadget level: not on the critical path. Roads are 2 to 8% of a compiled
  template's critical path, "so better roads would not pay" (`docs/FULLSTACK_PLAN.md:731`), and
  cutting Shor-15's traffic by 36% moved its makespan from 11.16 s to 11.04 s
  (`docs/ARCHITECTURE.md` §7). Roads are busy, though: in the one concrete 32-bit adder some road
  or junction is in use 57% of the time (`FULLSTACK_PLAN.md:852`).

So the first place a second layer can be shown to pay is a qLDPC syndrome round at the device
level, and that is what phase 5 measures. Storeys for floorplans come after, and only if that
measurement says so.

---

## 1. Decisions this plan makes

Each is a recommendation until §5 is answered.

| | decision | why |
|---|---|---|
| D1 | **three.js, vendored**: a pinned, tree-shaken build checked in with its licence and hash, inlined into a page, never loaded from a CDN | An existing framework with orbit controls, instancing and picking done; the page stays one file with no network. The renderer sits behind a thin adapter (§2.7), so moving to OGL later costs one file |
| D2 | **Inline it only where it is used**: pages of layered machines, and the Studio | Inlining 572 KB into all 204 committed pages would add 117 MB to the tree |
| D3 | **A third coordinate, not a `layer` field**: `pos: [x, y, z]`, layers are the distinct `z` | The schema admits it already; R20 and R21 become ordinary 3D geometry; nothing declares a riser, as nothing declares a junction |
| D4 | **A document is 2D or 3D, never mixed** | `_unit` cuts mixed positions to the shorter today, silently |
| D5 | **No new programme rule**; R20, R21 and the direction label are generalised so that a 2D machine gets the same verdicts and the same message strings | "Except for more edges in a junction, the rules are the same" |
| D6 | **A riser has its own price, and a machine that has one must declare it** | Priced as an ordinary rail, a riser is a 5 µs, 0.1-quanta shuttle, which would decide every comparison in favour of 3D before it is run |
| D7 | **One position law.** `place()` gains a layer coordinate; the SVG stage, the 3D stage and the GIF tool all read it | Two animations would drift; one cannot |
| D8 | **Editing stays 2D, one layer at a time**, in the first version; the 3D stage is view-only | The editor's whole pointer path is planar |
| D9 | **Electrode layout, GDSII and C2LR machines stay planar**; the electrode builder refuses a 3D node already | A multi-plane field solve, and a C2LR lattice in three dimensions, are separate projects |

---

## 2. Design

### 2.1 The document and the device

- `pos` has two coordinates or three. Three means lattice `x, y, z`. Lattice to lab stays a
  positive diagonal map, with a third factor `nm_per_unit_z` in the technology file.
- `Device.dim` is 2 or 3. `Device.layers` is the sorted distinct `z`; `Device.layer_of(node)` is
  an index into it. A segment whose ends share `x, y` and differ in `z` is a riser:
  `Device.is_riser(seg)`. Nothing is declared; all three are derived and cached like `degree`.
- A node with a rail in, a rail out and a riser has degree 3 and is a junction by R18. An
  X-junction with a riser up and one down has degree 6. A T-junction with both has degree 5.
- `DeviceBuilder` and the browser's `_dBuild` accept two or three coordinates and refuse a
  mixture. The browser export, `documentStatements`, `explicitStatements` and the page payload
  carry all of them. `move_site id x y` on a 3D machine keeps `z`.
- One new generator, `stack(base, layers, risers)`: copies of any planar generator's output at
  `z = 0, 1, ...`, with risers at the junctions named by a rule ("every k-th", or a list). Layer 0
  keeps the base ids, so a one-layer stack is the base machine. A riser is an ordinary
  `add_segment` between two nodes; there is no riser verb.
- An existing 2D document loads, expands and writes back byte for byte.

### 2.2 The rules

| rule | on a 2D machine | on a 3D machine |
|---|---|---|
| R19, at most `max_junction_degree` rails | unchanged | unchanged; the default is already 6 |
| R20, rails at a node subtend at least `min_rail_angle_deg` | unchanged | the angle between the two 3D directions; a riser is at 90° to every rail of its layer |
| R21, rails lie in a plane | unchanged, same messages | **rails lie in layers**: a rail stays in one layer or is vertical between two adjacent layers; no rail passes through a node it does not end at; two rails share a point only at a node they both end at |
| R22 and R4d, one waveform per cycle | labels unchanged byte for byte | a riser hop is labelled `+z` or `-z`, so a cycle cannot mix a vertical hop with a horizontal one |
| R2, R3, R23 | unchanged | apply as written: one ion in a riser, one ion across its end junctions, no ion rests on a riser's end |

R20, R21 and `direction_label` exist three times each (Python, `engine.js`, and for the label
OCaml). All three change together, and the zero-tolerance parity suite gets layered cases. The
two-layer rule set in `docs/rule-language.md` keeps working for a design that is drawn flat.

Which DAC channels a layer's electrodes are on is a wiring statement, not a rule:
`control.channels` learns to group by layer as it groups by row and column
(`qccd/arch/control.py:409`). Whether layers share a broadcast plane is question 6 in §5.

### 2.3 The prices

- `shuttle_riser`: a new primitive curve, time and quanta, for one riser hop.
- `junction_cross.curve_by_degree["5"]` and `["6"]`: declared by the machine.
- A machine that has a riser and does not price it, or has a five- or six-rail junction with a
  riser and no price at that degree, fails R11's "every junction is priceable" check. The
  fallback stays for flat-drawn designs, where it is today.
- No measurement exists. The reference machines take 500 µs and 8 quanta for a riser from
  arXiv:2604.21350, tagged `"table": "assumed"`, and every result on a layered machine is
  reported with a sweep of the riser price from a quarter to four times that
  (`python -m qccd sweep`). A board that scores a layered design lists these as added physics,
  which `conformance.physics.added` does already.
- `cost/hardware.py` counts 48 electrodes per junction whatever its degree, and its roles are
  `linear_h`, `linear_v` and `junction`. A riser needs a role of its own and, by §0.5, may need
  RF control. The hardware report says "not counted" for risers until that is specified; it does
  not guess.

### 2.4 The compiler

- `arch.ml` reads two or three coordinates, and refuses anything else where it now writes
  `(0, 0)`.
- `route.ml`'s `direction_label` gains the `z` case, equal to Python's on a corpus of random hops.
- Nothing else changes for correctness. Placement and routing use hop counts, and the Lean
  checker reads loops and moves. A layered machine is a graph to them.
- One thing does change for quality, in phase 5: a riser counts as one hop today, and it costs
  100 shuttles. Until the router weighs a hop by its price it will overuse risers.

### 2.5 One position law

`TRANSIT.place` already returns, for every flying ion, the hop it is on and how far along:
`flying[ion] = {a, b, u}`. The extension:

- every ion in the result carries `layer`: its node's layer at rest, the hop's layer in flight,
  and `layer(a) + (layer(b) - layer(a)) * u` on a riser;
- the geometry adapter's `edgeLen` gives a riser its declared `length` in place of its in-plane
  length, which is zero. Time inside a frame is shared by arc length, so without this a riser
  would be crossed in no time;
- inside a layer the law is today's, unchanged: slot offsets, the detour round a trap-mate,
  bowed segments.

A riser hop costs 100 shuttles and, under today's display rule, would be on screen as long as
one. The frame's replayed duration goes into the payload, and a "time to scale" speed setting
plays each frame for a time that grows with the logarithm of that duration. On either setting
the stage shows the frame's real duration beside the playhead.

`transit.py` gets the same change, under the same 1e-9 test. `compute_layout` measures the
pixel gap per layer and takes the smallest, over one shared bounding box, so that every layer
has the same scale and a riser is vertical. For a one-layer machine its output is bit-identical.

### 2.6 The 2D stage for a layered machine

- One panel per layer, on one SVG, at one scale. Panels are placed along the short side of the
  bounding box, so a wide machine stacks its layers and a tall one sets them side by side.
- A riser is a marked port in both of its panels with the same label. An ion crossing it shrinks
  into one port and grows out of the other, with a ring that fills as `u` goes from 0 to 1.
- A layer can be shown alone. That is also the editing view: the layer in focus is edited
  exactly as a machine is edited today.
- `tools/make_gif.py` draws the same panels through the same twin, so a README clip of a layered
  machine needs no browser.
- A one-layer machine has one panel, and the page is what it is now.

### 2.7 The 3D stage

**Structure.** Two parts, so that everything that can be wrong is testable without a GPU:

1. `scene(device, layout, placed, view)`: a pure function from the page's own data to typed
   arrays, one per kind of part (rails, traps, junctions, risers, pads, ions, beams): position,
   size, colour. It runs in Node under the existing shim, and has a Python twin for the tests.
2. An adapter of a few hundred lines that owns the three.js objects and copies those arrays into
   instanced meshes. It holds no knowledge of ions, frames or rules.

**What is drawn, and what each thing means.**

| on the stage | drawn as | carries |
|---|---|---|
| layer | a faint plane | which rails belong together |
| rail, trap, junction | bars and squares at the 2D proportions (`theme.GEOMETRY`); a trap's length is its capacity; a junction of five or six rails is coloured apart | the same marks as 2D, extruded |
| riser | a translucent shaft, so the ion inside stays visible | a vertical rail |
| ion | a sphere, coloured by role or by heat with the existing `#mode` switch | the same fill rule as 2D |
| which layer an ion is in | a soft shadow disc on its own layer's plane | depth, readable in a still |
| gate, measurement, preparation | a beam onto the ion, in the four beam colours of `theme.PALETTE` | the frame's `pairs`, `acts`, `ions` |
| lit DC pads | emissive pads, only when zoomed in | the frame's `ctl` |

Nothing is drawn that a frame field does not ask for. The beam's direction is a depiction, and
the legend says so.

**The camera and the controls.**

- Drag rotates, wheel or pinch zooms, right-drag or shift-drag pans, with damping.
- Presets: Top, Front, Corner. Top is orthographic and frames the machine exactly as the 2D
  panel does. Double-click centres on a node or an ion; "follow" keeps a chosen ion centred.
- Layer spacing is a slider. True spacing is tens of micrometres under millimetres of chip, so a
  riser would vanish; the page states that spacing is not to scale, and the existing True scale
  switch sets it from `nm_per_unit_z`.
- A layer can be isolated, the others drawn at low opacity. This is the answer to one layer
  hiding another, and it is on the transport bar, not in a menu.
- Hover gives what the 2D hover gives. Play, step, glide, speed and the slider are the page's
  own; the 3D stage adds none.
- The switch between 2D and 3D animates: the panels slide over one another and tilt into the
  stack. It shows which panel became which layer. It is skipped under `prefers-reduced-motion`.
- A button saves the canvas as a PNG, for figures.

### 2.8 What the 3D stage costs, and how it is kept small

| cost | size | how it is bounded |
|---|---|---|
| Page bytes | +572 KB for three.js, plus our stage code | inlined only where used (D2) |
| Parse and start-up | the bundle is inert text until 3D is first shown | it sits in a `<script type="text/plain">` and is started by inserting an inline script, which the workspace policy allows and `eval` would not be |
| GPU memory | 64 B per node or segment, 76 B per ion | one instanced mesh per kind of part |
| Timeline memory | none added | positions come from the `states` table the page already builds; no positions are stored per frame |
| CPU per frame | one loop over the ions into a preallocated array | no allocation in the frame loop |
| GPU per frame | 10 to 22 draw calls at every size measured | pixel ratio capped at 2; pads and labels only when zoomed in |
| Idle | zero frames | the loop runs only while playing, dragging or damping |
| WebGL contexts | browsers allow about sixteen a page, and Learn pages embed several stages | one context a page, created when 3D is shown and released when it is hidden |
| No GPU | 25 ms a frame at today's sizes, 417 ms at 10,000 nodes | the context is first requested with `failIfMajorPerformanceCaveat`; on refusal the page offers 3D with reduced detail below a node count and explains above it |
| No WebGL at all | | the switch is disabled with the reason; 2D is always there |

### 2.9 Where it attaches to the page

`CLAUDE.md` allows no overlay and no patching after `render_html`, so all of this is template.

- `render_html(..., stage3d="auto" | "always" | "never")`. `auto` is on for a machine with more
  than one layer. The Studio page is `always`, because a layered machine can be drawn there.
- A third file tuple beside `ENGINE_JS` and `EDITOR_JS`: `STAGE3D_JS = ("js/stage3d.js",
  "vendor/three.qccd.min.js")`, with a stamp of its own in `<meta name="qccd-page-3d">`. It is
  inlined only when the stage is on, so iterating on the 3D stage rebuilds the 3D pages and
  leaves the 204 planar ones alone. `tests/test_board_engine.py` learns the second stamp.
- The template itself changes once: a mount point beside `#svg` in `#canvas`, the switch in
  `#stagebar .ctrl` beside `#mode` and `#tTrue`, and four hooks, at `draw()`, `seek()`,
  `rebuildStatic()` and `deriveStage()`. That one change rebuilds the 204 pages; it lands
  together with the panels of §2.6 so that it happens once.
- The SVG `draw()` keeps running while 3D is shown. `paintQLabels` and the headless harnesses
  read ion positions back from the SVG marks, and a second source would be a second truth.
- `tests/shim.mjs` has no `getContext`. The stage is feature-detected, and `scene()` needs none.

### 2.10 Tests

The existing bar is that a claim is checked by something that can fail. For the stage:

- **Projection.** For sampled `(frame, phase)` on three machines, every ion's 3D position seen
  from Top equals its 2D panel position. Run in Node on `scene()`, and against the Python twin.
- **On the rail.** Every flying ion is on the drawn path of the hop `place()` says it is on;
  every resting ion is in a slot of its trap.
- **Census.** The instance counts equal the document's nodes, segments and ions, after load,
  after an edit and after a seek. None duplicated, none dropped.
- **Effects.** A table maps every drawn effect to the frame field that asks for it; the test
  fails on an effect with no field.
- **In a real Chrome**, with the harness the gadget tests use: one context while 3D is shown and
  none after; no draw in two idle seconds; with WebGL off, the 2D stage and a disabled switch;
  draw calls at most 16 plus the layer count.
- **The vendored file**: its SHA-256 against the manifest, the build script's pinned version, the
  licence text present, and none of `FORBIDDEN`, `eval(`, `new Function`, `Worker(` inside.
- **The benchmark** of §0.4 is committed and its table recorded. Times are reported, not asserted;
  counts are asserted.

---

## 3. Phases, each with an exit test

Phases 1 and 3 do not wait for each other: the 3D stage draws a one-layer machine as well.

| | deliverable | exit test |
|---|---|---|
| **P0** | The answers to §5. The vendored three.js build with its script, hash and licence. The benchmark as `tests/stage3d_bench.mjs` | The vendored-file test passes; the benchmark reproduces §0.4 within a factor of two on the same machine |
| **P1** | The layered machine: document, `stack` generator, R20, R21, direction labels, the two prices, the compiler's loader and label, in Python, JS and OCaml | Every shipped `.arch.json` round-trips byte for byte. The parity suite passes at zero tolerance with layered cases. A six-rail junction passes R19 to R21; an oblique rail between layers fails R21; an unpriced riser fails R11; a cycle mixing `+z` and `+x` fails R22. `steane_esm.qasm` compiles onto `stack(grid(3, 3), layers=2)`, every rule passes, and the Lean checker accepts the certificate. A layered document opened in the Studio and saved is the same document |
| **P2** | One position law, the layered 2D stage, the layered GIF, and the single template change of §2.9 | Transit parity at 1e-9 on layered machines, riser hops included. The mark census of every planar page is unchanged. `make_gif.py` renders a two-layer clip. The 204 pages are rebuilt once and the stamp test passes |
| **P3** | The 3D stage, view only: `scene()`, the adapter, the camera, the switch | The projection, on-the-rail and census tests in Node. The real-Chrome tests of §2.10. The benchmark table recorded |
| **P4** | What makes it worth looking at: beams, heat, shadows, isolate a layer, presets, follow, the fold animation, hover, PNG, time to scale, and a clip in `docs/img` | The effects test. Under time to scale a riser frame plays longer than a shuttle frame. `prefers-reduced-motion` honoured. Every control reachable by keyboard |
| **P5** | The first measurement: one BB [[144,12,12]] round on three machines: the best planar machine on the trunk; a compute layer under a highway layer; and a two-layer machine whose layers are the two planar halves of the code's Tanner graph. The router weighs a hop by its price | Every rule passes and R10 is certified on all three. A table of runtime, quanta, junction and riser transits and DACs. The riser price at which each layered machine stops winning, stated |
| **P6** | Each needs its own go-ahead: editing in 3D; storeys for floorplans, whose pages are Canvas2D and not `render_html`; electrodes for more than one plane | |

---

## 4. Risks

| risk | what is done about it |
|---|---|
| The riser's price is unknown, and every 3D result depends on it | A declared, tagged price; a sweep with every result; the break-even price reported (P5) |
| No one has moved an ion between stacked layers | Layered machines are labelled as assumed physics wherever they are scored |
| A template change rebuilds 204 pages, 385 MB | One template change, in P2; a separate stamp for the 3D files |
| A reader without a GPU | Detected and stated; reduced detail; 2D always available |
| One layer hides another, and a hidden ion is a wrong picture | Isolate a layer; shadows; 2D one switch away; the 3D stage is never the only view |
| Two animations that disagree | One `place()`; the projection test |
| three.js changes under us | Pinned, vendored, hashed; the adapter is the only file that names it |
| The 3D logic cannot run in the Node shim | `scene()` is pure; only the adapter needs WebGL |
| The compiler overuses risers because a hop is a hop | Price-weighted routing in P5, before any comparison is published |

---

## 5. Decisions, as answered on 2026-11-10

"Follow your recommendation", with two statements that override it.

1. **The library: three.js, vendored**, inlined only where used. As recommended.
2. **A riser is an ordinary shuttle.** "The shape (how it looks), the DAC and how it heats are
   exactly the same as the shuttle in 2D." This replaces D6 and question 4: there is no
   `shuttle_riser` primitive and no placeholder price. A riser is a segment like any other, a
   hop along it is charged `shuttle_segment`, and it is drawn as a rail. A node it ends at has
   one rail more and is priced by its degree, with the existing fallback (a crossing of five or
   six rails at the X-junction's price). §2.3 no longer applies.
3. **Layers may differ in shape, and each has a broadcast of its own.** "Layer A and layer B,
   connected by a 3D shuttle, can have different shape, and they each have unique
   broadcasting." This answers question 6. No control channel drives sites of two layers, and
   one cycle is one waveform a layer (R22). It also changes how a layered machine is made:
   not copies of one generator, but any planar machines stacked (`Machine.layered`).
4. **The degree, where a riser may end, the first target, boards**: as recommended. Both five
   and six rails are allowed under the cap of six; a riser may end at a junction or at a trap,
   which then is a junction by R18; the first measurement is a BB round at the device level;
   boards are not touched.

One thing in these answers is this plan's reading and not the user's words: an ion on a riser
leaves one layer's electrodes and arrives on the other's, so its hop is taken to engage BOTH
layers' broadcasts for that cycle. It is the stricter reading. If a riser has electrodes of
its own, it is one line in `r22_uniform_motion` to relax.

---

## 6. What this plan does not change

- Any existing `.arch.json`, TSIR programme, certificate or rule verdict for a 2D machine.
- The page of a planar machine, apart from the one rebuild in P2.
- The rule numbering, the rule language, and the two-layer rule set people already use.
- The Lean checker.
- The zero-dependency Python toolchain. three.js is a file in the page, and building pages needs
  neither Node nor npm; only regenerating the vendored file does.
- Electrode layout and GDSII.

---

## 7. As built, 2026-11-10

What exists on `qccd-3d`, and where it departs from §1 to §3.

**The machine.**
- `pos` is `(x, y)` or `(x, y, z)`. `Device.dim`, `layers`, `layer_of`, `is_riser`, `risers`,
  `layer_name` are derived (`qccd/arch/device.py`). A machine that mixes two and three
  coordinates, and a loop through two layers, are structural errors. Python, `edit.js` and
  `engine.js` say them in the same words.
- `qccd.arch.layers.stack` and `Machine.layered([a, b], risers=[("A.J1_1", "B.S3")])` put
  planar machines one above another. Every id of a layer is prefixed with its name (`A.`,
  `B.`); a layer above the first is shifted so that its first riser stands vertical. There is
  no `stack` generator: a layered machine is an explicit document, so the browser needs no new
  generator. Zone types, curves and wiring are the first machine's; a zone a later layer
  defines differently is kept as `<layer>.<zone>`.
- `move_site id x y` on a layered machine keeps the node's `z`.

**The rules.** No new rule.
- R20: a riser is at 90 degrees to every rail of its layer and 180 to a riser going the other
  way. These are stated, not computed from `z`, because the lattice has a scale of its own on
  every axis and only a right angle to a plane survives that.
- R21 reads "rails lie in layers": a riser is vertical between adjacent layers, and the two
  planar tests are made layer by layer. A planar machine gets the verdicts and the words it had.
- R22 is one waveform a layer, and `direction_label` gives a riser hop `+z` or `-z`, in
  Python, `engine.js` and `route.ml`. The channel groups of the control plane are split by
  layer, so R4d is layer by layer with no change to its code.
- The rule language has two new words, `upright(r)` and `same_broadcast(a, b)`, and the
  catalogue's R21 and R22 use them.
- One declaration, many layers: the control block is one text, instantiated once a layer.
  Giving two layers different wiring PARAMETERS (one broadcast, one direct) is not built.

**The compiler.** `arch.ml` reads a third coordinate where it wrote `(0, 0)`, and `route.ml`
labels a riser hop. Nothing else changed. `steane_esm.qasm` compiles onto two lattices of
different shape joined by two risers, uses both layers, and every rule holds
(`tests/test_layers.py`). The router still counts a riser as one hop; under decision 2 that is
also its price, so the weighting of §2.4 is not needed.

**The flat stage.** One panel a layer, framed and named (`layout.panel_offsets`, mirrored as
`engine.js::panelOffsets`). `compute_layout` was not touched: the panel offsets are applied to
the nodes before it. A riser is a ring at each of its ends, labelled with the layer it leads to;
an ion on it closes into one port and opens out of the other.

**The 3-D stage.** `qccd/viz/js/stage3d.js` and `qccd/viz/vendor/three.qccd.min.js` (568 KB,
built by `tools/vendor_three.mjs`), inlined when `render_html(stage3d=...)` says so: `"auto"`
gives it to a layered machine only, and `python -m qccd run ... --stage3d always` gives it to
any machine. Its switch is the `3D` button on the transport bar.
- It computes no position: `draw()` hands it `TRANSIT.place`'s answer, and it slides each
  layer's panel back over the others and lifts it. `fold` 0 is the SVG picture seen from
  above, 1 is the stack, and the switch animates between them.
- Drawn: layers as faint planes, rails, traps, junctions, risers as upright rails, ions with a
  shadow on their own layer, the beam and spot of a gate, a measurement or a preparation, the
  link of a two-ion gate, the sites in play.
- Controls: drag, wheel, right-drag; Corner, Top and Front; layer spacing; show one layer; PNG;
  hover names the ion.
- Nothing is drawn while nothing changes, and the WebGL context is released when 3D is hidden.
- A website does not inline it. `render.link_stage3d(page, prefix)` adds two script tags for
  the same two files, served once under `prefix`, and the line that shows the switch once
  they have loaded. `qccd/site/build.py` writes the files to `static/3d/<their stamp>/` and
  links every Studio page it puts, so any machine on the site can be turned and no page grows
  by the 570 KB. A page that carries the stage, and one written before the template had a
  place for it, are left as they are; a linked page read away from the site is the page it was.
- Measured in headless Chrome on the pages built here: 8 draw calls for a planar ring of 144
  ions, 13 for two layers, 22 for three; no frame drawn in 1.5 s of idle; no context held after
  the switch back.

**Tests.** `tests/test_layers.py` (29 tests) and `tests/stage3d.mjs`; layered cases in the
parity suite's build and geometry buckets. Two of them carry the weight: the browser lays a
layered machine out exactly as Python does (equal, not close), and the 3-D stage puts every
ion exactly where the SVG mark is within its panel, at every frame and five phases of it.

**Pages to look at** (git-ignored, under `out/layered/`): `steane_two_layers.html` and
`steane_three_layers.html` are the Steane round compiled onto stacked lattices;
`grid_under_ring.html` is a hand-written programme on two layers of different shape.

**Not built.**
- Editing a layered machine on the stage. Every press pans, and a toast says why. Its text can
  be edited, and each layer can be designed as its own planar machine and stacked.
- `tools/make_gif.py` refuses a layered machine. It is its own renderer and has not learned the
  panels.
- The lit DC pads, "time to scale" playback, follow an ion, and the software-renderer check of
  §2.8. The 3-D scripts are ordinary inline scripts, not inert text started on first use.
- A committed benchmark and the real-Chrome tests of §2.10. The 3-D stage was checked in
  headless Chrome by hand; nothing in the suite drives it there.
- Per-layer wiring parameters, a `nm_per_unit_z`, and electrodes for more than one plane.

**On the website since 2026-11-11** (built from this branch), **and who may see it.** The 3-D
stage and everything compiled onto a layered machine are unpublished, so the site has three
readers (the user, 2026-11-11; `qccd/site/access.py`, `qccd/site/deploy/README.md`):

- **A visitor** sees none of it. The stage's two files are refused to anyone not signed in,
  so no visitor's page shows the 3D switch; the Goal pages are refused the same way, and the
  Leaderboard's index is served without its Goal sections. The bar, the search index, the
  documents and the Rules page say nothing of layers: the documents drop the passages marked
  unpublished, and the Rules page prints each rule in its planar reading.
- **A member** (any signed-in account) has the 3D switch on every Studio page of the sections
  rebuilt here (the Design page, the Leaderboard's entries, the examples, Reproduce) and
  reads the Goal pages.
- **A member granted `layers`** (two accounts) also reads `layers/`: the page on machines
  built in layers, three Studio pages, and the BB board with the conveyor sandwich's three
  entries (`layers/bb144/`). Those entries are marked `"restricted": "layers"` in
  `Codesign/scripts/bb_studio.py`; the public board is built without them.

The web server asks the accounts service before it serves a guarded path, so a guarded page
is never sent to a reader it is not for. What this does not hide: the code inside a public
Studio page (the engine reads a layered machine; the template has the hidden switch), and
this repository, which is public.

Not on the new template, so without the 3D switch for anyone: the gadget pages (5.2 GB; the
machine this was built on has 2 GB free and not their cache), the Debug page (it lists every
Studio page, the gadgets included), Goal 3's point pages (drawn from compile outputs that are
not in the repository), and the pages the official grading service draws (its image predates
the template). What was replaced on the server is in `/root/qccd-site-backups/`.

**Before a merge.**
1. ~~Rebuild the committed pages.~~ Done 2026-11-10, at page stamp f65fc153934e7d9e. The
   small-code, memory and C2LR boards were redrawn from their committed files
   (`--pages-only`), and their manifests changed in page sizes only. The BB board's twelve
   qccdc entries need the files of `Compiler/build/bb2/`, which are not in the repository:
   `Codesign/scripts/bb2_remake.py` makes them again: the machine each entry's committed page
   records (generator, parameters, template, borrowed control blocks, description), entered on
   bb144@2, and the workspace's compile job run with the released qccdc cd3efedbbc10. All
   eight programmes replay to the time their rows had, and the data of all twelve rebuilt
   pages is equal, block for block, to the data of the pages they replace.
2. **Publish the compiler.** `Compiler/ocaml` changed, so `tests/test_workspace_toolchain.py`
   will fail once this is committed, until the binaries are rebuilt and published and
   `qccd/workspace/toolchain.json` is updated (`deploy/toolchain/README.md`).
3. Run the parity suite's mutation guards (`-k planted_drift`), which were not run here: each
   writes about 240 MB of temporary files, and the disk had 3 GB free.

## 8. The measurement (P5), 2026-11-10

Does a machine built in layers run a circuit faster than a planar one? The study is
`Codesign/scripts/q07_layers.py`, its rows are `Codesign/data/q07.json`, and what they say, with
every caveat, is `Codesign/findings/q07-layers.md`. In short:

- **The site's compiler, unchanged, gains nothing from layers.** On the Steane board, stacked
  lattices take fewer transport cycles than a planar lattice (41 to 45 against 52 to 63) and
  more time (17.0 to 17.6 ms against 15.3 to 16.9 ms). `qccdc` packs one waveform a cycle
  whatever the machine, and both ends of a riser are junctions.
- **A machine designed for layers does.** `qccd.arch.sandwich.conveyor_sandwich` keeps the data
  still between two conveyor loops of ancillas, and is one graph built in one, two or three
  layers; `qccd.compile.conveyor_router` drives it. On the BB [[144,12,12]] board (`bb144@2`),
  by the board's own grader: 31.59 ms in a plane, 24.11 ms on two layers, 20.27 ms on three,
  where the board's leader takes 288.37 ms. Each layer is a broadcast of its own, so the two
  loops turn in the same cycle: 213, 151 and 118 transport cycles.
- **What the layers are worth depends on what the time is.** Where transport is the time, three
  layers are 1.2 to 1.9 times faster than the same machine in a plane. Where cooling is the
  time (Goal 3's 3 ms cooling), 6 to 17 %. On the four small boards, where the leader holds
  every ion in one trap and moves nothing, the sandwich loses and its layers change nothing.
- **Most of the gain over the leaderboards is the architecture, not the third dimension.** The
  planar sandwich is already 9 times faster than the BB board's leader.

What the study added to the branch: `qccd/arch/sandwich.py`, `qccd/compile/conveyor_router.py`,
`tests/test_sandwich.py`, and one reading in `qccd/verify/replay.py::conveyed_paths`: on a
machine built in layers, R23's conveyor test is made a layer at a time, because a cycle there
is a waveform a layer and the instruction names one class for them all. A planar machine is
judged as before.

Sources for §0.5: [arXiv:2603.06208](https://arxiv.org/abs/2603.06208) ·
[arXiv:2604.21350](https://arxiv.org/abs/2604.21350) ·
[arXiv:2308.07915](https://arxiv.org/abs/2308.07915) ·
[arXiv:2404.17676](https://arxiv.org/abs/2404.17676) ·
[US 11721537](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11721537) ·
[US 12080541](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12080541)
