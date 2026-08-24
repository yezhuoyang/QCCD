# `qccd/phys/` — the electrodes a device implies

The layer that turns a trap graph into metal with coordinates, and metal into an ion
height. Before it, the only "electrodes" anywhere in this project were the authored
integer `control.wiring.electrodes_per_trap = 24` and a decorative pill tiling in the
browser whose count came from *drawn pixel length*. Neither was falsifiable. Now
rectangles exist, they have coordinates in nanometres, a fab tool opens them, and a field
solver reads them.

```
qccd/phys/field.py     the gapless-plane RF pseudopotential, in closed form
qccd/phys/tech.py      the technology sidecar: every dimension in nm, with a source
qccd/phys/shapes.py    integer-nanometre polygons, cells, placements, design rules
qccd/phys/build.py     (Device, Technology) -> Layout.  Nothing is authored
qccd/phys/drc.py       design rules, plus disclosures that are deliberately not verdicts
qccd/phys/gds.py       a hand-written GDSII writer, and an independent reader
qccd/phys/svg.py       the same shape table, rendered for a person
qccd/analysis/field.py ANALYSES['field'] -- ion height, secular frequency, junction cost
```

The browser page can carry the metal as a backdrop -- `qccd phys <device> --html` --
but it is a *view*, not an editor: see [the fab view](#the-fab-view) for what it is
deliberately not.

```bash
python -m qccd phys ring144_24v                    # the metal, and its DRC
python -m qccd phys chain --svg out/chain.svg
python -m qccd gds ring144_24v -o ring.gds         # 1372 polygons, database unit 1e-9 m
python -m qccd sweep field chain rf.w_rf_nm 60000,99500,140000 --set mass_u=137.905247
```

## Nothing is authored

`build_layout(arch, tech)` is a pure function. No field was added to `Node` or `Segment`,
no section to `.arch.json`, no `SCHEMA_VERSION` bump, no architecture file changed, and
the browser is untouched. The metal carries no information the pair does not already
determine, so storing it would only create something to lose — `Knowledge:
d_technology_is_a_sidecar`.

## The model, and what it costs

Planar electrodes in the `z = 0` plane, ion above. Two assumptions, taken verbatim from
the paper this layer is checked against:

> 1. that there is no gap between electrodes;
> 2. that the entire plane is covered by conducting electrodes.
> — arXiv:2201.12579 `ms.tex:200-208`

Each electrode then contributes the solid angle it subtends, over 2π (Wesenberg,
arXiv:0808.1623, eq. `biotsavartpot`; the companion analytic treatment is House, PRA 78
033402, which has no arXiv preprint and is recorded in `Library/non_arxiv_ledger.csv`).
No mesh, no BEM, no external solver, no new dependency.

**Every tolerance in this layer is quoted from one table**, the same paper's measurement
of its own model against FEM of the fabricated trap:

| | |
|---|---|
| positions of pseudopotential minima | within **5%** |
| worst-case pseudopotential, near a junction centre | ~**20%** |
| FEM heights lower, with the fabricated 5 µm gaps | ~**2.7 µm** |
| with gaps reduced to 1 µm: height / confinement | 0.7% / 0.1% |

Wesenberg also states when the model may be used, and one condition drives a knob:

> "necessary conditions … include that gaps between electrodes are much smaller than `d`,
> that **the extent of the trap is much larger than `d`**, and that the distance from ion
> to other conducting surfaces is much larger than `d`"

A finite rail violates the second in proportion to `(h/L)²`. That is measured, not
assumed: the truncation is monotone and second order, and `rail_length_over_h` exists
because of it.

## The technology file

A sidecar, never part of the document. `Dim` has two fields and both are required — there
is no `Dim(41500)`. A source beginning `declared:` marks a number this project chose
rather than read, and `Technology.declared()` lists them.

**The one shipped preset has none.** Every dimension of
`eth_junction_2201.12579.tech.json` is either a page reference or a derivation checked
against its inputs:

| dim | nm | where from |
|---|---|---|
| `w_rf` | 99500 | `ms.tex:283-288` `tab:junction_linear` |
| `w_g` | 41500 | same, `0.83h` at `h = 50` µm |
| `gap` | 5000 | `ms.tex:368`; declared for DRC only, never modelled |
| `dc_pitch` | 75000 | `ms.tex:682` |
| `dc_width` | 49750 | `ms.tex:725` |
| `dc_centre_width` | 31500 | derived `w_g − 2·gap`, and the paper prints 31.5 µm at `ms.tex:685` |
| `dc_setback` | 125250 | derived `w_g/2 + w_rf + gap` |
| `min_axis_pitch` | 355000 | derived `2(dc_setback + dc_width) + gap` |
| `rail_end_extension` | 50000 | derived: one nominal ion height |
| `well_gap` | 5000 | derived `= gap`; wells are made by voltages, not geometry |

### Two lattice scales, and why one is not enough

`nm_per_unit_x` = 225000 nm: one axial trap pitch, which is three broadcast control
electrodes. arXiv:2305.03828 states the conveyor belt supports "20 wells on each side
**(one for every three electrodes)**"; 2201.12579 gives 75 µm segments. 3 × 75 = 225.

`nm_per_unit_y` = 355000 nm: one `min_axis_pitch`, the closest two *parallel* trap axes
can be drawn, because each needs 175 µm of electrode stack on the facing side plus a gap.

These measure different things and they disagree. An isotropic 225 µm was tried first and
shorts the north control column of every ring device against the opposite rail — the two
rows physically do not fit.

## What a device becomes

Each axis-aligned segment gets two RF rails flanking its axis at `w_g/2`, one segmented
centre control electrode between them, and two segmented control columns outboard at
`dc_setback`. A non-axis-aligned segment is **refused by id**, never approximated. All
1,392 segments of all nine shipped devices are axis-aligned, and no lattice coordinate
needs rounding — measured, not assumed.

Two things are interrupted, for the same reason:

* a **control pad** is dropped where a perpendicular rail crosses its column;
* a **rail** stops at a perpendicular trap's gap.

The second is not obvious and is not what the build plan said. Running both rail pairs
straight through each other lays RF metal across both trap axes, and solving that finds
*no confined ion position anywhere near the junction* — a 175 µm dead zone, because the
ion would be directly over driven metal. Interrupting the rails leaves the four L-shaped
quadrant electrodes of 2201.12579 fig. 7(a), which is the geometry that reproduces its
numbers. `Knowledge: fd_the_naive_crossing_must_keep_both_gaps_clear`.

### The junction is the paper's counterexample, on purpose

A degree ≥ 3 node is drawn as the naive crossing and tagged `role='naive_crossing'`, with
the citation travelling in the layout notes. The optimized alternative in that paper is a
cubic B-spline boundary fitted by Nelder–Mead; shipping it means shipping an optimizer.
Drawing the counterexample costs nothing and yields a checkable number.

## What it found

Numbers this platform could not previously state. Each is pinned by a test and recorded in
`Knowledge/notes/accumulated.yaml`.

**The naive junction is as bad as the paper says.** At 2201.12579's own conditions
(⁴⁰Ca⁺, 40 V peak, 2π × 40 MHz), fed only the two published RF widths:

| | computed | paper | band |
|---|---|---|---|
| transport-path height | **86.51 µm** | 84 | +2.99% (5%) |
| minimum confinement | **0.0607 meV/µm²** | 0.07 | −13.3% (20%) |

Nothing is fitted, and the assertions are written to the paper's bands and no tighter: the
84 µm is read off a figure. Far out along an arm the same geometry returns the
linear-section closed form to 3.6e-4, approaching it monotonically, so agreement at the
centre is not a coincidence of arm length. **PLAN §0.5's prose about RF barriers is now
two checked numbers.**

**No shipped device sits at its design height.** A rail sized for 49.95 µm delivers it
only in isolation.

| device | ion height | vs design | off-axis |
|---|---|---|---|
| `chain72` | 49.948 µm | −0.0% | 0.000 µm |
| `cyclone_base`, `h2_racetrack` | 50.57 | +1.2% | +3.28 |
| `cyclone_dual_loop` | 51.98 | +4.1% | −3.97 |
| `grid9x9`, `deck_unit_cell` | 52.88 | +5.9% | −5.77 |
| `ring144_24v` | 56.28 | **+12.7%** | +4.23 |
| `ladder_2x72` | 57.54 | **+15.2%** | +4.79 |

Four of the nine are outside the model's own 5% band, so the shift is geometry rather than
numerics. The null also moves **off the trap axis**, which is why height is solved in the
transverse plane (`transverse_null`) and not in `z` alone — a search constrained to the
axis reports no null at all for eight of the nine.

**`ring144_24v` is not fabricable in this technology**, and the threshold is exact:

```
along the rail    (w_g/2 + w_rf) + (dc_setback + dc_width) + gap = 300250 nm
across the rails  2 (dc_setback + dc_width + rail_end_extension + gap) = 460000 nm
```

The shipped scales are 225000 and 355000. One nanometre under either threshold and the
violations return. The axial trap pitch the control electrodes imply is 225 µm and the
axial requirement is 300 µm; **they are incompatible**. That is an independent geometric
argument for PLAN §0.5's conclusion about the 24 verticals: they do not merely cost
quanta, at these dimensions they do not fit.

**The naive crossing also deletes control electrodes** — 456 on `ring144_24v`, and *all*
of them on `grid9x9` and `deck_unit_cell`, which as drawn have nothing left to control
them with. Unlike the confinement this is a count, not a simulation.

**Four segments in `cyclone_dual_loop` run through nodes they are not incident to.**
`EA35` goes from (35,0) to (35,3) straight through `DT35` and `DB35`, both degree-2 nodes
on the data loop. A planar trap has no overpass, so the graph and the plane disagree: if
the crossing is real those nodes are degree 4, they are junctions, they are not being
charged as junctions, and the router may send two ions through the same metal.
`ring144_24v` has two more at degree 1 — its end-cap docks, which `docs/adl.md` already
records. Reported, never repaired: `arch/` is not this layer's to edit, and the two
possible repairs say different things about the machine.

## The fab view

`qccd phys <device> --html out/x.html` renders the studio page with the derived electrodes
underneath the schematic, in a `gMetal` group that sits first in the SVG and therefore
below everything else.

**It carries its own transform, and the page does no arithmetic to get it.** The scale, the
fit and even the scale bar's rectangle are computed in Python by
`qccd.phys.svg.metal_view_model` and read verbatim. They have to be: the page's own
`px()/py()` is anisotropic by up to `K_ANISO = 12`, and pushing a 99.5 µm by 16 mm rail
through it would draw a shape no fab could make. A test strips the block's comments and
strings and asserts it contains no `*`, `/`, `Math.`, `px(` or `py(`.

**The underlay does not register with the schematic, and the page says so.** Registration
would need the page's `sx/sy` to equal the technology's `nm_per_unit_x / nm_per_unit_y`;
on `chain72` those are 1.0 and 0.634, and no shipped device matches. One of the two views
has to misstate a proportion and it is not the one with nanometres in it, so the metal is
drawn true to scale with a **scale bar** — the one length on the page that means a physical
distance — and a caption saying which view is which.

**A page that did not ask for metal is unchanged.** The payload is absent from the view
model unless a technology was named, so every page emitted before this existed is
byte-identical to the one emitted now. `render.py` never imports `qccd.phys`: the metal
arrives as a dict, because a page is a property of a run and the metal is a property of
`(device, technology)`, and wiring one to the other in the renderer would make every page
pay for a field solve it did not ask for.

What it is deliberately not: no `engine.js` edit, no parity bucket, no editing verb, no
hit-testing, no palette tile. The mirrored half of this project is diffed at tolerance
zero, and a fab view that computed anything would have to be diffed too.

## Counted versus declared is a disclosure, not a verdict

`qccd/cost/hardware.py` prices `electrodes_per_trap = 24`; this layer draws 5.89 per site
on `ring144_24v`. **Neither number is measured** — the 24 is an authored integer that may
count shim and compensation electrodes this layer does not draw, and the 5.89 follows from
a `dc_pitch` and a lattice scale that are the technology's claims. So the report prints
both, prints the axial trap pitch each implies (600 µm against 225 µm), and stops. Turning
that into a pass/fail would be inventing a fact out of two conventions.

Nothing here is a verifier rule. `RULE_STATEMENTS` is still 23, `BROWSER_SET` still 17.
Those are mirrored in `engine.js` and diffed at tolerance zero over every architecture
file, so a Python-only rule firing there is an automatic red harness — and a design rule is
a different kind of claim anyway, about a technology's fabrication limits, which the
document does not declare and the browser cannot know.

## Integers, and one implementation

`shapes.py` and `tech.py` contain **no float literal** — checked by tokenising, so a
comment saying "0.83h" is fine and `1e3` cannot sneak through. Coordinates are integer
nanometres from the lattice to the GDSII file, whose database unit is 1e-9 m, so the
round trip is `==` on lists of ints rather than a tolerance. Placement is quarter-turns
through `qccd/arch/component.py::translate_point`; four of them compose to the identity by
equality, not by `approx`.

**The field kernel is Python only, forever.** A transcendental solver cannot be
differentialled at tolerance zero — `atan2` is not required to agree to the ulp between
CPython and V8 — so it is not written twice, and `tests/test_field.py` asserts that no
symbol of it reaches `engine.js`, `edit.js` or `editor.js`.

## Out of scope, deliberately

No DC voltage solution, no waveform synthesis, no axial confinement, no gap modelling, no
multi-layer solve (a non-zero `z_offset` refuses rather than approximating), no heating
rate from geometry, no optimized junction geometry, and **no trap depth** — a 1-D bisection
above the null returns a local maximum on one line, not the escape saddle, and depth is the
quantity most sensitive to every declared hole. `hardware_report.area_mm2` still reports
`0.0`; connecting it is a separate decision.

## Where these came from

```bash
python Knowledge/kg/query.py why            # phys/S0, S2 and S6 decisions, to their papers
python Knowledge/kg/query.py param h_ion    # every ion height on record, with its source
```

Findings: `fd_gapless_model_error_is_published`, `fd_naive_crossing_is_quantified_under_our_own_model`,
`fd_finite_rail_truncation_is_second_order`, `fd_naive_crossing_reproduced`,
`fd_the_naive_crossing_must_keep_both_gaps_clear`,
`fd_no_shipped_device_sits_at_its_design_height`.

> **`Library/` and `Knowledge/` are gitignored**, so the corpus and the notes above do not
> travel with a clone. The corpus is 1.1 GB and the graph database is past GitHub's
> per-file limit, which is why; but it also takes the ~200 KB of YAML notes with it. A
> `!Knowledge/notes/` negation would keep the provenance and leave the bulk out.
