# Designing a device to the person's shape

The person describes a shape ("a large triangle and a smaller one", "an H", "a ring with
spokes") and a leaderboard. You draw that shape, then fill in the details the machine needs,
until the board's circuit runs on it with every rule passing. Then you submit it. Work where
they watch, in their Studio, one visible step at a time, with a line in the chat for each.

## 0. Start a new design, named as they would name it

Create a new design (`qccd_manage_branch(action="create", title="Two triangles")`) and put it
in front of them in their Studio. Call it by that name from then on. Never overwrite another
design unless they ask.

In their Studio, clear the canvas with `newCanvas` (`qccd_page_act` action `studio`). The
canvas keeps the physics every board shares.

## 1. Pick the mechanism the board needs

Two compilers exist, and a submission picks between them by the device's shape. A design with
a closed loop tries the conveyor first, then the router.

| board | mechanism | what the device must have | known to work (measured) |
|---|---|---|---|
| BB [[144,12,12]] | conveyor (rotate) | ONE closed loop that carries the data qubits, plus docks: gate sites one rail off the loop | `ring` width 72, height 2, 24 docks: 522 ms per round, every check eligible. Drawn: a triangle loop of 144 sites with 24 docks inside it (below): 505.8 ms, every rule passing |
| Repetition code, Steane, Surface code | conveyor | the same, smaller | `ring` width 8, height 2, 8 docks: 9.45, 11.48 and 14.1 ms, all eligible |
| Five-qubit code | router (compile) | gate-capable traps that hold 2 ions, sites that can measure, room for all 9 qubits | `grid` 3 × 3: 8.89 ms, eligible |
| GHZ (starter) | router | 4 qubits' room | `chain` of 4 |

Conveyor facts:

- It drives only the FIRST closed loop.
- A dock is a gate-capable site one segment off that loop.
- The circuit's qubits split into riders (on the loop) and docked ones (parked at docks).

Consequences:

- Two separate loops cannot both work. Uncoupled, their qubits never meet. Joined, the joint
  becomes a junction: R2 allows at most one ion there, and R19 at most degree 4.
- The router also refuses a bare loop or line for BB; BB then needs an 11 × 11 lattice or
  larger.

A board's circuit is available as a program by its title:
`qccd_run_program(program="Five-qubit code [[5,1,3]]")`. `qccd_read_reference(section="boards",
query=<title>)` gives its circuit and what is ranked.

## 2. Turn their shape into that structure

- **The outline becomes the loop.** Draw the main shape as ONE closed polyline:
  `sketchDraw` with `["poly", [[x, y], ...], null, {"closed": true}]` (model units).
  - Sites land one unit apart along it; every corner is a site; the closed shape becomes the
    loop.
  - **Corners must be at least 60°** (R20, `budget.min_rail_angle_deg`). A triangle's corners
    are 60° at best, and lattice rounding makes one smaller, so the sketch is refused with the
    angle. Cut the corners instead: every corner becomes 120° and it still reads as a triangle.
    For example `[[2,0],[10,0],[11,1.732],[7,8.66],[5,8.66],[1,1.732]]`.
  - A circle is `ellipse` with `{"shift": true}`; its sides must be long enough for 60° corners.
- **A second shape becomes the docks, or joins the loop.** For "a large triangle and a
  smaller one", make the large one the loop, and put the docks one unit inside its sides,
  spread evenly: together they draw the smaller triangle. Tell them in one sentence why it is
  not a second loop.
  - A dock is a trap site joined to ONE loop site by ONE plain rail. All of them at once, as one
    visible step: one `qccd_apply_change_set` whose operations are `{"type": "add_site", "pos":
    [x, y], "zone": "trap", "to": ["<loop site>"]}`, one per dock (measured: 24 in one change
    set, same result as drawing them). One at a time on the canvas: `addNodeAt(x, y, {"kind":
    "site", "zone": "trap"})`, then `joinNodes(<loop site>, <the new site>)`.
  - Do NOT draw docks with `sketchDraw("line", ...)`. A sketched line declares an open path,
    and the verifier then reads docking from different sides as different motions: R22 (one
    waveform per cycle) fails on every dock batch.
  - Finding the loop sites: `sketchDraw` returns the loop's site ids in order, one unit apart
    from the first corner you gave; `state` has every site's position. A dock goes one unit
    from its loop site, perpendicular to that side, towards the inside.
  - Measured (2026-09-24): a triangle of side 50 with its corners cut by 2 is a loop of 144
    sites; 8 docks per side, one unit in, make 24. BB ran at 505.8 ms per round with every
    rule passing, against 522 ms for the ring.
- **Enough of everything.**
  - Loop sites at least equal the riders.
  - Docks at least equal the docked qubits, as far as the conveyor needs; the known-good rings
    above give the scale.
  - Total capacity at least equals the circuit's qubits (a run refuses a design that holds too
    few, and says how many).
- **Zones.** Sketched sites take the default zone `trap`, which can gate, measure and cool.
  A site in a zone that cannot gate or measure fails R6.

## 3. Check before running, and read the run

- Read `problems` (the studio verb) after each shape. Geometry rules R19 to R21 show there,
  and a sketch that would create one is undone and refused with the rule.
- Run the board's circuit on the design (`qccd_run_program`). Show the run on their screen.
  Then fix what it reports, one visible change at a time:

  | the run says | fix |
  |---|---|
  | too few ion slots | a longer loop, or more sites |
  | "rotation does not apply" | not one closed loop, too few docks, or a gate on a loop rider: add docks |
  | "cannot reach … within N cycles" | the router needs other routes: another rail, or a lattice |
  | R4 or R4d (movement class not declared or not drivable) | declare the class with `qccd_apply_change_set`, operation `call`, method `declare_class` |
  | R1, R2, R6, R19 to R22 | capacity, a junction holding two ions, a zone, geometry |

- Stop when the run passes every rule it checks. Report its round time, and where the time
  goes, from the run's own report. Never invent numbers.

## 4. Submit

`qccd_submit_local(design=<name>, board=<title>)` compiles the board's circuit onto the design,
adopts the program, freezes the design and grades it with the reference evaluator (rules, Lean
certificate, semantics, metrics). Follow the job. The result is local and not published; say
so. Publishing needs the person's approval: `qccd_prepare_publish` shows exactly what would be
uploaded.

## Be honest about what cannot be done

When a shape cannot work as asked, say why in one sentence and build the nearest one that does.
Examples: two separate loops for the conveyor; a sharp corner under 60°; a shape too small for
the circuit.

Still uncertain, so check each by running, never by assuming:

- how a dock placed at a corner behaves (the measured triangle kept its docks off the
  corners);
- whether declaring movement classes by hand is enough for R4 and R4d when a run asks for it
  (the measured triangle needed none: it passed on the classes the canvas starts with).
