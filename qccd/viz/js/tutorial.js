// qccd/viz/js/tutorial.js -- THE COURSE, AS DATA.
//
// Every lesson is a record: what it teaches, the handful of editor verbs that build its
// device, the exercise, the check that decides it, three hints of increasing depth, the
// nudges a failure gets, the praise a pass gets, and the solution.  Nothing here runs the
// machine: the engine in editor.js (`lessonLoad`, `lessonCheck`, ...) drives the same verbs
// a user's pointer drives, and a check is a small expression over what the page already
// computed -- frames, verdicts, prices.  `tests/test_tutorial.py` loads every lesson,
// proves the check FAILS on the starting state, applies the solution, proves it passes, and
// runs the result through `python -m qccd open`, so a lesson that lies cannot ship.
//
// Text carries three marks the renderer understands: **bold**, `code`, and [[term|hint-key]]
// for a term that hovers to the same card as the rest of the page.  Facts in praise are
// {name} slots filled from the check ({cost}, {steps}, {dacs}, {sites}, ...).
//
// This file is inlined into the page after editor.js and registers itself at the end.

'use strict';

var QCCD_TUTORIAL = {
  parts: [
    { id: 'A', title: 'What you are looking at',
      milestone: 'Part A done: you can read the map, build on it, and price a move. Part B puts ions on it and moves them.' },
    { id: 'B', title: 'Programming by hand',
      milestone: 'Part B done: you have written a CNOT, a Bell pair and a docked rotation by hand, which most people who use these machines never do. Part R breaks every rule on purpose -- and you repair them.' },
    { id: 'R', title: 'The rules the machine must obey',
      milestone: 'Part R done: every rule this machine obeys, you have broken and repaired -- or read the honest reason it cannot be checked here. Part C builds circuits under all of them.' },
    { id: 'C', title: 'Circuits by hand',
      milestone: 'Part C done: a syndrome round and a plaquette, scheduled by hand under every rule, with the heat paid for. Part D lets the compiler do it and asks you to judge the machine.' },
    { id: 'D', title: 'Architecture decisions with the compiler',
      milestone: 'The course is done. You can read the map, build a device, program it by hand, break and repair every rule, schedule a circuit under them, and judge an architecture from measured numbers. The rest is practice -- and the compiler.' }
  ],
  lessons: [

    // =============================================================== A1
    { id: 'A1', part: 'A', title: 'The machine in one picture',
      story: 'A few micrometres above a chip, in vacuum, a handful of ions are held still by electric fields. This page is a map of that chip.',
      teaches: ['el:site', 'el:junction', 'el:segment', 'region:stage'],
      text: [
        'Each [[trapping site|el:site]] -- a rounded bar -- is a well in the field where ions sit. The ticks inside it are how many fit. Each [[rail|el:segment]] -- a pink line -- is a track an ion can be pushed along, and a [[junction|el:junction]] -- a square -- is where rails meet: ions cross it and never stop in it.',
        'The **elements rail** on the left lists what you can add. The **canvas** in the middle is the chip. The **panels** on the right hold the programme, the device as code, and the report. Hover anything on this page for what it is; press **Explain** in the head to label the regions; **?** opens the guide.'
      ],
      setup: [
        ['newCanvas', { name: 'tee' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'J0', kind: 'junction' }],
        ['addNodeAt', 2, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 3, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 1, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'J0', { id: 'E0' }],
        ['joinNodes', 'J0', 'S1', { id: 'E1' }],
        ['joinNodes', 'S1', 'S2', { id: 'E2' }],
        ['joinNodes', 'J0', 'S3', { id: 'E3' }],
        ['fit']
      ],
      stages: [
        { exercise: 'Click the **junction** -- the square where three rails meet.',
          check: ['selected', { kind: 'junction' }] },
        { exercise: 'Now click the site at the end of the **spur**: the bar hanging below the junction.',
          check: ['selected', { id: 'S3' }] }
      ],
      hints: [
        'A junction is drawn as a square; sites are rounded bars.',
        'The spur is the short rail going down from the junction; its site is at the bottom.',
        'Click on the bar itself, not beside it -- the cursor changes to a hand over it.'
      ],
      nudges: {
        selected: ['Not that one -- look for the square.', 'Close: that is a site. The junction is where the three rails meet.', 'Try the square in the middle of the row.'],
        'default': ['Nothing selected yet: click a part on the canvas.']
      },
      praise: 'That is the whole vocabulary: {sites} sites, {junctions} junction, {segments} rails. Everything else on this page is about moving ions between them.',
      solution: { stages: [[['setSelection', [{ kind: 'junction', id: 'J0' }]]],
                           [['setSelection', [{ kind: 'site', id: 'S3' }]]]] } },

    // =============================================================== A2
    { id: 'A2', part: 'A', title: 'Zones: what may happen where',
      story: 'Not every site can do everything. Some can hold a gate, some can be read out, some only take ions in from the source.',
      teaches: ['el:zone_type', 'zone:new'],
      text: [
        'A [[zone type|el:zone_type]] is a label a site carries: how many ions fit, and whether a gate, a measurement or cooling may happen there. This device knows three: `trap` (gate, measure, cool; two ions), `data` (storage; two ions) and `load` (where ions arrive; eight). The chips under **Zone type** draw each one at its real size -- `load` is visibly the longer bar.',
        'A site takes its zone when it is placed: pick a chip, then place. And a zone is a shared thing: change its capacity and every site that carries it changes with it.'
      ],
      setup: [
        ['newCanvas', { name: 'tee' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'J0', kind: 'junction' }],
        ['addNodeAt', 2, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 3, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 1, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'J0', { id: 'E0' }],
        ['joinNodes', 'J0', 'S1', { id: 'E1' }],
        ['joinNodes', 'S1', 'S2', { id: 'E2' }],
        ['joinNodes', 'J0', 'S3', { id: 'E3' }],
        ['fit']
      ],
      stages: [
        { exercise: 'Click the **load** chip under Zone type, then click an empty spot on the canvas to place a loading site.',
          check: ['all', ['sites', 5], ['anyNodeZone', 'load']] },
        { exercise: 'Now change the `load` zone itself. Open the **Device** tab and choose **Source**: the device as text. The `load` zone comes with the device\'s template, so no line sets its capacity yet -- add `m.set_zone("load", capacity=4)` as a new last line. The chip and your new site both shrink. Then come back to **Learn** and press **Check**.',
          check: ['zoneCapacity', 'load', 4] }
      ],
      hints: [
        'The zone chips sit right under the Zone type tile; the pressed one is the zone the next site gets.',
        'Source is under the **Device** tab. Nothing in it sets the load zone yet, so type a line of your own at the very end: `m.set_zone("load", capacity=4)`.',
        'The picture follows as you type. A `set_zone` line changes only the fields it names, so the zone keeps everything else it allows. When the load chip reads 4, go back to **Learn** and press **Check**.'
      ],
      nudges: {
        sites: ['No new site yet -- pick the load chip and click the canvas.', 'The click has to land on empty canvas, away from the other sites.'],
        anyNodeZone: ['A site was placed, but not in the load zone. Press the load chip first, then place another.'],
        zoneCapacity: ['The load zone still holds {have}. Open Device, choose Source, and add the line m.set_zone("load", capacity=4) at the end.'],
        'default': ['Not there yet. Try the hint.']
      },
      praise: 'A loading site that now holds {cap}. Capacity is the first rule of the machine -- R1 -- and you just set it for a whole zone at once.',
      solution: { stages: [[['addNodeAt', 3, 1, { id: 'S4', kind: 'site', zone: 'load' }]],
                           [['emit', { method: 'set_zone', args: ['load'], kwargs: { capacity: 4 } }]]] } },

    // =============================================================== A3
    { id: 'A3', part: 'A', title: 'Building by hand',
      story: 'Every chip starts empty.',
      teaches: ['el:site', 'el:segment', 'el:loop', 'snap', 'undo', 'fit'],
      text: [
        'Press **Trapping site**, then click the canvas: a ghost shows where it will land -- exactly where you click. Press **Snap** under the canvas and it lands on the lattice instead, which makes a tidy square easy. **Shift-drag** one site onto another to lay a rail between them. Drag a part to move it anywhere; **Undo** takes any gesture back; **Fit** frames the whole device.',
        'A [[loop|el:loop]] is a closed ring of sites the machine can turn as one. Select the sites (shift-drag a box around them), then **right-click** any one of them: the menu that opens offers **Close loop**. Right-click is how you reach everything a part can do -- its size, its zone, deleting it.'
      ],
      setup: [['newCanvas', { name: 'square' }], ['fit']],
      exercise: 'Build a square: four sites one lattice step apart, four rails joining them in a ring, then select all four and press **Close loop**.',
      check: ['all', ['sites', 4], ['segments', 4], ['loops', 1, true]],
      hints: [
        'Place the four sites at the corners of a square: click, click, click, click.',
        'Shift-drag from a site to its neighbour to lay a rail; four times, around the square.',
        'Shift-drag on empty canvas draws a selection box. With all four selected, right-click one of them and press **Close loop**.'
      ],
      nudges: {
        sites: ['{have} of 4 sites so far. Keep placing.', 'You have {have} sites; the square needs exactly four.'],
        segments: ['{have} of 4 rails. Shift-drag from a site onto its neighbour.', 'Almost: {have} rails. One more join around the square.'],
        loops: ['Four sites, four rails -- now select them all and press Close loop.'],
        'default': ['Not a square yet. The hint knows which corner is missing.']
      },
      praise: 'A four-site loop. The machine can now turn every ion on it by one step with a single instruction -- the cheapest move it has.',
      challenge: { label: 'sharp', exercise: 'Do it in nine gestures: four places, four joins, one close.',
                   check: ['editsAtMost', 9] },
      solution: { steps: [
        ['addNodeAt', 0, 0, { id: 'N0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'N1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 1, { id: 'N2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 0, 1, { id: 'N3', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'N0', 'N1', { id: 'E0' }],
        ['joinNodes', 'N1', 'N2', { id: 'E1' }],
        ['joinNodes', 'N2', 'N3', { id: 'E2' }],
        ['joinNodes', 'N3', 'N0', { id: 'E3' }],
        ['closeLoop', 'L0', ['N0', 'N1', 'N2', 'N3'], true, 'ring'],
        ['fit']] } },

    // =============================================================== A4
    { id: 'A4', part: 'A', title: 'The price of motion',
      story: 'Moving an ion is not free. Every push along a rail heats it a little, and a hot ion makes a worse gate. Pushing faster heats it more.',
      teaches: ['m:cost', 'm:runtime', 'm:peak n̄', 'el:primitives', 'el:curve_point', 'tab:R'],
      text: [
        'The head keeps score three ways. [[Cost|m:cost]] counts what the machine is charged for each hop under its cost model. [[Runtime|m:runtime]] is wall-clock time. [[Peak n̄|m:peak n̄]] is how hot the hottest ion got, in motional quanta. Runtime and heat are read off the [[primitives|el:primitives]]: each operation has a curve of measured points, how long against how much it heats, and the model takes the **fastest point in its reference table** (`qccdsim_jones`).',
        'Open **Report** to see every number with where it came from. Nothing on this page is a guess: every price is a point on a measured curve, and the curve cites its paper.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['testDrive'], ['fit']],
      exercise: 'Open **Curve point** (under Append a row), pick the `shuttle_segment` curve, type **2** and **0.2** in the *+ point* row and press **add**. Watch the runtime chip fall -- and the peak heating rise.',
      check: ['all', ['runtimeBelow', 'setup'], ['peakAbove', 'setup']],
      hints: [
        'Append a row is a fold in the rail; Curve point is the tile in it. Its form has a curve picker at the top.',
        'Choose shuttle_segment in the picker; the + point row at the bottom takes microseconds and quanta: 2 and 0.2.',
        'The model always takes the fastest point of its table, so a faster point wins the moment it exists.'
      ],
      nudges: {
        runtimeBelow: ['Runtime is still {have} us. Is the new point faster than the old one? Check its us value.', 'The fastest point still seems to be the old one -- did the add go through? The toast says how many points the curve has.'],
        peakAbove: ['Runtime fell but heating did not rise -- give the fast point more quanta than the old one (0.2).'],
        'default': ['The price has not moved yet.']
      },
      praise: 'Runtime fell from {us0} us to {us} us, and the hottest ion went from {peak0} to {peak} quanta. Faster is hotter: that trade is the whole game, and the curves are where it is decided.',
      solution: { steps: [
        ['curveAddPoint', 'shuttle_segment', { us: 2.0, quanta: 0.2, table: 'qccdsim_jones', source: 'exercise A4', label: 'a faster shuttle' }]] } },

    // =============================================================== A5
    { id: 'A5', part: 'A', title: 'The control plane and broadcasting',
      story: 'A thousand electrodes, and not a thousand wires. How the chip is driven decides what it can do at once.',
      teaches: ['el:control', 'm:DACs', 'm:electrodes', 'm:switches'],
      text: [
        'Each rail is paved with [[electrodes|m:electrodes]], and each electrode needs a voltage. A [[DAC|m:DACs]] is one voltage source. Wire every electrode to its own DAC (**direct** wiring) and the count grows with the chip. **Broadcast** the same waveform along a channel -- the electrodes tied *a, b, c, a, b, c* -- and one DAC drives any length of rail. This device declares its control plane that way: the report counts **channels**, not electrodes.',
        'The price of broadcasting is the sentence this whole course turns on: **every site on a shared channel moves the same way in the same step.** You will meet it as a rule (R4d), in a programme (Part B), and as a design decision (Part D).'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['testDrive'], ['fit']],
      stages: [
        { exercise: 'Open **Control plane** (under Machine settings) and double `wiring.electrodes_per_trap` from 24 to 48, then apply. Open **Report** and read the Hardware table: electrodes double -- and DACs do not move.',
          check: ['all', ['electrodesAbove', 'setup'], ['dacsSame', 'setup']] },
        { exercise: 'Why did the DAC count not move?',
          check: ['answer', 'channels'] }
      ],
      choices: [
        { id: 'stale', text: 'The report has not caught up yet.' },
        { id: 'channels', text: 'Every electrode on a channel shares one waveform, so DACs count channels, not electrodes.' },
        { id: 'junctions', text: 'DACs are only counted for junctions, and this ring has none.' }
      ],
      hints: [
        'Control plane is the second tile under Machine settings; its form lists the wiring fields by name.',
        'Change the number next to wiring.electrodes_per_trap and press apply. The Report tab shows electrodes, switches and DACs.',
        'One waveform, many electrodes: that is what a channel is. The DAC count is the channel count plus a few for compensation.'
      ],
      nudges: {
        electrodesAbove: ['Electrodes are still {have}. Change wiring.electrodes_per_trap in the Control plane form and apply.'],
        dacsSame: ['The DAC count moved to {have} -- change only electrodes_per_trap, nothing else, and apply again.'],
        answer: ['Look at the numbers again: electrodes doubled and DACs stayed. Which sentence explains that?', 'The report is not stale -- it re-priced the moment you applied. Think about what a channel is.'],
        'default': ['Not there yet.']
      },
      praise: 'Electrodes went from {electrodes0} to {electrodes} and DACs stayed at {dacs}. That is broadcasting: a DAC drives a channel, and a channel drives every electrode on it -- which is exactly why every site on it must move the same way.',
      solution: { stages: [[['emit', { method: 'set_wiring', args: [], kwargs: { electrodes_per_trap: 48 } }]],
                           [['setAnswer', 'channels']]] } },

    // =============================================================== A6
    { id: 'A6', part: 'A', title: 'The panels',
      story: 'You have a device and a programme. Now read them.',
      teaches: ['tab:P', 'tab:A', 'tab:M', 'tab:W', 'tab:R', 'follow'],
      text: [
        '[[Program|tab:P]] lists the hardware programme one instruction per row; click a row and the animation jumps there. [[Device|tab:A]] is the device as code: the statements that build it, and a Source view you can edit. [[Machine|tab:M]] and [[Report|tab:R]] hold the totals and the 23 rules -- green passed, red failed, grey not checkable here. [[Write|tab:W]] is where you will write programmes in Part B.',
        'The two right-hand columns of a programme row are its cost and its steps. A rotation moves every ion on the loop at once, so it costs the most -- and does the most.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['testDrive'], ['fit']],
      exercise: 'In **Program**, find the most expensive instruction and click its row so the animation jumps there.',
      check: ['frameIs', 'maxcost'],
      hints: [
        'The right-hand numbers on each row are cost and steps.',
        'Follow keeps the running instruction in view; switch it off to browse the list.',
        'Clicking a row seeks the animation to that instruction; the status strip names it.'
      ],
      nudges: {
        frameIs: ['You are on instruction {have}; the dearest one is elsewhere in the list.', 'Compare the cost column: one row is bigger than the rest.'],
        'default': ['Click a row in the Program panel.']
      },
      praise: 'Instruction {frame} costs {cost}: a rotation moves every ion at once. Part B is about writing your own.',
      solution: { steps: [['seekTo', 'maxcost']] } },

    // =============================================================== B1
    { id: 'B1', part: 'B', title: 'The programme language',
      story: 'A device does nothing until it is told to. Here is how you tell it.',
      teaches: ['p:init', 'p:shuttle', 'evaluate', 'tab:W', 'tab:P'],
      text: [
        'A programme is a list of `p.` statements in the [[Write|tab:W]] panel, one per line, run in order. [[p.init|p:init]] puts named ions on named sites: `p.init({"d0": "S0"})`. [[p.shuttle|p:shuttle]] pushes one ion along a path of site ids, one rail per step: `p.shuttle("d0", ["S0", "S1", "S2"])`. Press **Evaluate** and the page turns the text into frames, prices them, checks every rule and plays them on the canvas.',
        'The [[Program|tab:P]] panel lists the frames the text became. The strip under the canvas says the first thing that is wrong, if anything is. Nothing in the text is taken on trust: an ion asked to walk a rail that is not there is refused at the line that asked.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit']],
      exercise: 'Open **Write**. Put an ion `d0` on `S0` and walk it to `S2`. Press **Evaluate**, then **Check**.',
      check: ['all', ['lowered'], ['ionAt', 'd0', 'S2'], ['rulesPass']],
      hints: [
        'Two lines: a p.init that places d0 on S0, then a p.shuttle with the path S0, S1, S2.',
        'Site ids go in quotes, the path in square brackets: p.shuttle("d0", ["S0", "S1", "S2"]).',
        'Evaluate is the button under the text. The toast says how many statements and frames it made.'
      ],
      nudges: {
        lowered: ['No programme yet. The Write panel is waiting for two lines.', 'The text did not run: {have}', 'Check the quotes and brackets -- the strip names the line.'],
        ionAt: ['d0 ended at {have}, not S2. The path is a list of site ids, each joined to the next by a rail.', 'Nearly: d0 is at {have}. One more site in the path.'],
        rulesPass: ['The ion got there but a rule is red: {have}'],
        'default': ['Not there yet. Evaluate, then Check.']
      },
      praise: 'd0 walked two rails to {at} in {steps} steps at cost {cost}. Press play under the canvas and watch it go: that is your first hardware programme.',
      boundary: { exercise: 'Walk it back: a round trip that visits S2 and ends on S0.',
                  check: ['all', ['visited', 'd0', 'S2'], ['ionAt', 'd0', 'S0'], ['rulesPass']],
                  praise: 'There and back in {steps} steps. Every hop is a row in the Program panel -- click one and the animation jumps there.' },
      solution: { program: 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\n' } },

    // =============================================================== B2
    { id: 'B2', part: 'B', title: 'One CNOT',
      story: 'Two ions, one gate. On paper it is a line in a circuit. On the chip it is a journey, a pause, and then the gate.',
      teaches: ['p:gate', 'p:cool', 'rule:R6b', 'rule:R7c', 'rule:R22'],
      text: [
        'A two-qubit gate happens **in one site**: both ions must sit there ([[R6b|rule:R6b]]), and the site\'s zone must allow gates ([[R6|rule:R6]]). [[p.gate|p:gate]] names the gate and its pairs, control first: `p.gate("CX", [["d0", "d1"]])`.',
        'So a CNOT between ions in different sites is two things: transport, then the gate. Bring `d1` down the line to `S0` with a shuttle, then fire. The ions start where the loaded programme puts them.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\n']],
      stages: [
        { exercise: 'Under the `p.init`, add a shuttle that walks `d1` from `S2` to `S0`, then a `CX` on `d0`, `d1`. Evaluate, then Check.',
          check: ['all', ['lowered'], ['gateBetween', 'd0', 'd1', 'CX']] },
        { exercise: 'The gate fired -- and the badge went **red**. Hover it: R7c. The shuttle heated `d1`, and the machine will not fire a gate on an ion nobody cooled. Put a [[p.cool()|p:cool]] line between the shuttle and the gate, then Evaluate again.',
          check: ['all', ['gateBetween', 'd0', 'd1', 'CX'], ['rulesPass']] }
      ],
      hints: [
        'p.shuttle("d1", ["S2", "S1", "S0"]) brings d1 to d0. Then p.gate("CX", [["d0", "d1"]]).',
        'The pairs argument is a list of pairs -- two levels of brackets: [["d0", "d1"]].',
        'p.cool() with no arguments cools every ion. It goes after the transport and before the gate.'
      ],
      nudges: {
        lowered: ['The text did not run: {have}', 'The strip under the canvas names the line that failed.'],
        gateBetween: ['No CX between d0 and d1 yet. p.gate takes the name, then a list of pairs.', 'The gate must name both ions -- and they must share a site when it fires.'],
        rulesPass: ['Still red: {have}', 'Read the badge on the strip: it names the rule and the instruction. Cooling goes before the gate.'],
        stepsAtMost: ['{have} steps, and four is the answer here. One p.simd would be three, but R22 refuses it: one waveform moves every ion the same way, and these two move towards each other.', 'p.shuttle("d0", ["S0", "S1"]) and p.shuttle("d1", ["S2", "S1"]), then p.cool(), then the gate.'],
        ionAt: ['That ion ends at {have}. The gate fires at S1, so each ion walks one rail -- neither one walks the whole way.', 'd0 goes S0 to S1, d1 goes S2 to S1, and the CX fires where they meet.'],
        'default': ['Not there yet. Evaluate, then Check.']
      },
      praise: 'That is a CNOT on real hardware: d1 walked to d0, was cooled, and CX fired at instruction {gateFrame}. Cost {cost}, {steps} steps, and all {rulesChecked} rules the page checks are green.',
      challenge: { exercise: 'Meet in the middle: fire the `CX` at `S1`, with `d0` and `d1` each walking one rail to get there. You will want to put both moves in one `p.simd` -- try it, and read the rule that goes red. One set of electrodes pushes everything on a rail the same way, so no single instruction can move `d0` right while `d1` moves left ([[R22|rule:R22]]). Two shuttles, then cool and fire.',
                   check: ['all', ['gateBetween', 'd0', 'd1', 'CX'], ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S1'], ['stepsAtMost', 4]],
                   praise: 'The CX fired at S1 in {steps} steps, with both ions travelling. One p.simd would have been three, but R22 refuses it: one waveform moves every ion the same way, and these two move towards each other. That is why Part C rotates a whole ring at once -- every ion the same way is exactly what the electrodes can do.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.gate("CX", [["d0", "d1"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']]],
        challenge: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d0", ["S0", "S1"])\np.shuttle("d1", ["S2", "S1"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']] } },

    // =============================================================== B3
    { id: 'B3', part: 'B', title: 'Single-qubit gates and measurement',
      story: 'A Bell pair is the smallest thing worth entangling. Three gates and a readout.',
      teaches: ['p:gate', 'p:measure', 'p:reset', 'rule:R6'],
      text: [
        'Single-qubit gates name the **site**, not the ion: `p.gate("H", [], ["S0"])` is an H on whatever sits in `S0`. [[p.measure|p:measure]] names ions -- `p.measure(["d0", "d1"])` -- and needs a site whose zone has *spam* (state preparation and measurement): `trap` has it, `data` does not. [[p.reset|p:reset]] puts an ion back to |0>.',
        'A Bell pair is H on the control, CX, then read both out. Every line is one thing the machine does, and the order of the lines is the order it happens.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\n']],
      exercise: 'Write the Bell pair: `H` on `S0`, bring `d1` to `S0`, cool, `CX` on `d0`, `d1`, then measure both ions.',
      check: ['all', ['lowered'], ['gateAt', 'H', 'S0'], ['gateBetween', 'd0', 'd1', 'CX'], ['measured', ['d0', 'd1']], ['rulesPass']],
      hints: [
        'Five lines after the init: the H, the shuttle, the cool, the CX, the measure.',
        'p.gate("H", [], ["S0"]) -- an empty pairs list, then the site. p.measure(["d0", "d1"]) at the end.',
        'Both ions are in S0 when they are measured, and S0 is a trap: measuring is allowed there.'
      ],
      nudges: {
        lowered: ['The text did not run: {have}'],
        gateAt: ['No H on S0 yet. Single-qubit gates take an empty pairs list and then the site.'],
        gateBetween: ['The CX is missing, or names the wrong ions.'],
        measured: ['Not measured yet: {have}. p.measure takes a list of ions.'],
        rulesPass: ['A rule is red: {have}', 'Cooling goes between the shuttle and the CX.'],
        'default': ['Not there yet.']
      },
      praise: 'H, CX, measure: a Bell pair in {frames} frames and {steps} steps. Its two readouts agree every time -- which this page cannot show, and that is the honest limit of a hardware tool: it schedules gates, it does not simulate them.',
      boundary: { exercise: 'Run it twice: after the measurement, reset both ions and do a second H, CX and measurement, still all green.',
                  check: ['all', ['hasFrame', 'reset'], ['gateCount', 'CX', 2], ['rulesPass']],
                  praise: 'Two rounds, {gates} CX gates, one cooling: the ions did not move between rounds, so nothing heated. A syndrome round in Part C is exactly this shape.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S2"})\np.gate("H", [], ["S0"])\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\np.measure(["d0", "d1"])\n' } },

    // =============================================================== B4
    { id: 'B4', part: 'B', title: 'Reading a failure',
      story: 'This programme is wrong. The page already knows why; the skill is reading what it says.',
      teaches: ['status', 'tab:R', 'tab:P', 'undo'],
      text: [
        'When a rule fails, three places say so. The [[strip|status]] under the canvas names the first failing rule and the instruction. The [[Report|tab:R]] panel shows every rule as a badge -- hover a red one for its statement and its source. The [[Program|tab:P]] panel marks the instruction that broke it.',
        'Debugging a hardware programme is reading that sentence and deciding which of two things to change: the programme, or the device. Here the device has a `data` site in the middle, and the loaded programme meets there.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'data' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d0", ["S0", "S1"])\np.shuttle("d1", ["S2", "S1"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']],
      stages: [
        { exercise: 'Press **Evaluate** on the loaded programme, read the strip, and pick the rule that fails.',
          check: ['answer', 'R6'] },
        { exercise: 'Now fix it without touching the device: `S1` is a `data` site and cannot hold a gate, so meet somewhere that can. Make every badge green with the CX still fired.',
          check: ['all', ['rulesPass'], ['gateBetween', 'd0', 'd1', 'CX']] }
      ],
      choices: [
        { id: 'R7c', text: 'R7c -- there is no cooling before the gate.' },
        { id: 'R6', text: 'R6 -- the gate is in a site whose zone does not allow gates.' },
        { id: 'R6b', text: 'R6b -- the two ions are not in the same site.' },
        { id: 'R1', text: 'R1 -- too many ions in one site.' }
      ],
      hints: [
        'The strip under the canvas begins with the rule\'s number.',
        'Hover S1: its zone is data, and data has gate=false. Hover S0 and S2: trap allows gates.',
        'Walk d1 all the way to S0 -- p.shuttle("d1", ["S2", "S1", "S0"]) -- and leave d0 where it is.'
      ],
      nudges: {
        answer: ['Look at the strip under the canvas: it names the rule.', 'Hover the red badge in Report. The sentence says gate=false.'],
        rulesPass: ['Still red: {have}', 'Which sites allow a gate? Hover them: trap does, data does not.'],
        gateBetween: ['Green, but the CX is gone. The fix keeps the gate and moves it.'],
        'default': ['Not there yet.']
      },
      praise: 'R6 read and repaired: the CX now fires in a trap, and every badge is green. You just debugged a hardware programme the way it is done -- from the sentence to the fix, cost {cost}.',
      solution: { stages: [
        [['setAnswer', 'R6']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']]] } },

    // =============================================================== B5
    { id: 'B5', part: 'B', title: 'Loops and rotation',
      story: 'Six ions, one instruction. The loop is the reason these machines can be big.',
      teaches: ['p:fill', 'p:rotate', 'el:loop', 'm:steps'],
      text: [
        'A closed [[loop|el:loop]] is one thing the machine can move as a unit. [[p.fill|p:fill]] puts an ion on every site of it (`d0` on `S0`, `d1` on `S1`, ...). [[p.rotate|p:rotate]] turns it by a number of sites: `p.rotate(2)` moves every ion two sites forward, `p.rotate(-1)` one site back. One instruction, six ions.',
        'Watch the price. A rotation is charged for every ion it moves, so it is not cheaper than six shuttles in **cost** -- it is cheaper in [[steps|m:steps]]: one step per site turned, with the ions never in each other\'s way.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['fit']],
      exercise: 'Fill the loop and rotate it so that `d0` ends on `S2`, in two statements.',
      check: ['all', ['lowered'], ['rotated', 'L0', 2], ['instructionsAtMost', 2], ['rulesPass']],
      hints: [
        'p.fill() with no argument fills the only loop, L0.',
        'S0 to S2 is two sites forward: p.rotate(2).',
        'Two statements, two frames: the fill and the rotate. Nothing else.'
      ],
      nudges: {
        lowered: ['No programme yet: fill, then rotate.', 'The text did not run: {have}'],
        rotated: ['The loop has not turned by two: {have}.', 'Forward is S0 to S1 to S2. Which sign does that need?'],
        instructionsAtMost: ['{have} frames. A fill and one rotate is all it takes.'],
        stepsAtMost: ['{have} steps: four forward costs four. Is there a shorter way round?', 'On a loop of six, forward by four is the same place as back by two.'],
        rulesPass: ['A rule is red: {have}'],
        'default': ['Not there yet.']
      },
      praise: 'Six ions moved by two sites in {steps} steps at cost {cost}. Every one of them moved the same way in the same step -- hold that thought for B7.',
      challenge: { exercise: 'Sharp: put `d0` on `S4` in at most 2 steps. Four forward is two back.',
                   check: ['all', ['rotated', 'L0', 4], ['stepsAtMost', 2], ['rulesPass']],
                   praise: 'Two back is four forward: {steps} steps, cost {cost}. Direction is a choice, and the shorter way round is always the cheaper one.' },
      solution: { program: 'p.fill()\np.rotate(2)\n' } },

    // =============================================================== B6
    { id: 'B6', part: 'B', title: 'Moving many ions at once',
      story: 'A rotation moves everyone. Sometimes you want three ions, not six.',
      teaches: ['p:simd', 'el:control', 'rule:R3', 'rule:R5'],
      text: [
        '[[p.simd|p:simd]] moves several ions in one instruction: `p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S2", "S3"]])`. The first argument is the [[movement class|el:control]] -- the kind of motion, from the list the device declares (this ring declares `shuttle`, `rotate_cw`, `rotate_ccw`, `dock`, `undock` and two sorting moves). The second is a list of moves, each `[ion, from, to]`, from a site to its neighbour.',
        'One instruction is one **step** of the machine: every move in it happens at once. The Program panel shows the class in its own column, and the rules judge the step as a whole: no two ions on one rail ([[R3|rule:R3]]), no head-on exchange ([[R5|rule:R5]]), one direction per loop ([[R11|rule:R11]]).'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2", "d2": "S4"})\n']],
      exercise: 'Move all three ions one site forward (`S0` to `S1`, `S2` to `S3`, `S4` to `S5`) in **one** instruction.',
      check: ['all', ['lowered'], ['frames', 2, 2], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S3'], ['ionAt', 'd2', 'S5'], ['rulesPass']],
      hints: [
        'One p.simd line under the init, with three moves in its list.',
        'Each move is [ion, from, to]: ["d0", "S0", "S1"] and so on.',
        'Two frames in all: the init and the simd. Three shuttles would be four.'
      ],
      nudges: {
        lowered: ['The text did not run: {have}'],
        frames: ['{have} frames. The exercise wants exactly two: the init and one simd.', 'Three separate shuttles are three steps. Put the three moves in one list.'],
        ionAt: ['{have} is not where the exercise asks. Check the from and to of each move.'],
        rulesPass: ['A rule is red: {have}'],
        'default': ['Not there yet.']
      },
      praise: 'Three ions, one instruction, {steps} step, cost {cost}. A rotation is exactly this with every site filled -- and now you can write the ones a rotation cannot.',
      boundary: { exercise: 'Now backward: all three one site back, still one instruction, still green.',
                  check: ['all', ['frames', 2, 2], ['ionAt', 'd0', 'S5'], ['ionAt', 'd1', 'S1'], ['ionAt', 'd2', 'S3'], ['rulesPass']],
                  praise: 'Same step, other direction. The loop does not care which way, as long as it is one way.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S2", "d2": "S4"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S2", "S3"], ["d2", "S4", "S5"]])\n' } },

    // =============================================================== B7
    { id: 'B7', part: 'B', title: 'Broadcasting in a programme',
      story: 'Two ions on one loop, wanting to go opposite ways. The chip has one answer for both of them.',
      teaches: ['el:control', 'rule:R11', 'rule:R4d', 'p:simd'],
      text: [
        'Here is the sentence from A5 as a programme. This ring is wired **wise**: one channel drives every rail on the loop, so in one step the whole loop gets one waveform, and every ion on it moves the same way. The page checks that as [[R11|rule:R11]]: ions on a loop move by one delta per step. Python checks it again at the channel level, [[R4d|rule:R4d]] -- Part R shows you that verdict.',
        'It means two ions can never pass each other on a broadcast loop, and that a move forward and a move back are two different steps. A rotation is the honest name for what one step can do: shift everyone by one.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\n']],
      stages: [
        { exercise: 'Try it: in one `p.simd`, move `d0` forward (`S0` to `S1`) and `d1` back (`S3` to `S2`). Evaluate, read the strip, then Check.',
          check: ['ruleFails', 'R11'] },
        { exercise: 'Now do it legally: `d0` on `S1` and `d1` on `S2` with every badge green.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S2']] }
      ],
      hints: [
        'p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S3", "S2"]]) -- both moves in one list.',
        'The strip says R11: the ions move along L0 by [1, 5] in one cycle. One direction per step.',
        'Two simd lines, one move each: the loop turns one way, then the other.'
      ],
      nudges: {
        ruleFails: ['The page should be objecting and is not. Are both moves in ONE simd, in opposite directions?', 'Forward is S0 to S1; back is S3 to S2. One instruction, two moves.'],
        rulesPass: ['Still refused: {have}', 'One direction per step. Give each move its own instruction.'],
        ionAt: ['{have} is not where the exercise asks. Check the from and to of each move.'],
        'default': ['Not there yet.']
      },
      praise: 'Two steps, one direction each, cost {cost}. That is broadcasting from the programme side: a shared channel makes opposite motions different steps, and a rotation is the one step that moves everyone.',
      boundary: { exercise: 'Both forward by one in one instruction -- legal, because it is one direction. Show it green.',
                  check: ['all', ['frames', 2, 2], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S4'], ['rulesPass']],
                  praise: 'One step, one direction, two ions: the channel is happy. That is the shape of every move on a broadcast machine.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S3", "S2"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d1", "S3", "S2"]])\np.simd("shuttle", [["d0", "S0", "S1"]])\n']]] } },

    // =============================================================== B8
    { id: 'B8', part: 'B', title: 'Docks and spurs',
      story: 'The loop turns as one. To take one ion out of the dance, park it to the side.',
      teaches: ['region:dock', 'el:control', 'p:shuttle', 'p:rotate'],
      text: [
        'A [[spur|region:dock]] is a rail off the loop to a side site -- here `A0` off `S0` and `A3` off `S3`. Park an ion there and the loop can turn without it. On this ring the spur sites are the only `trap` sites, so a gate can only ever happen in one: the dock is where the work gets done.',
        'The classes `dock` and `undock` name that move honestly: they entail a split from the chain and a merge into the dock, and are priced for it. `p.shuttle("d0", ["S0", "A0"], "dock")` -- the third argument is the class. Without it the move is a plain `shuttle`, cheaper on paper because it does not account for the split.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\n']],
      exercise: 'Park `d0` in `A0`, rotate the loop so that `d1` ends on `S3`, then bring `d0` back to `S0`.',
      check: ['all', ['lowered'], ['visited', 'd0', 'A0'], ['ionAt', 'd1', 'S3'], ['ionAt', 'd0', 'S0'], ['rulesPass']],
      hints: [
        'Three statements after the init: out to the dock, a rotation, back from the dock.',
        'p.shuttle("d0", ["S0", "A0"]) parks it; S1 to S3 is p.rotate(2); p.shuttle("d0", ["A0", "S0"]) brings it back.',
        'While d0 is in A0 it is off the loop, so the rotation does not touch it.'
      ],
      nudges: {
        lowered: ['The text did not run: {have}'],
        visited: ['d0 never went into the dock. The spur site is A0, joined to S0.'],
        ionAt: ['{have} is not where the exercise asks. Rotate by 2 for d1; bring d0 back at the end.'],
        rulesPass: ['A rule is red: {have}'],
        'default': ['Not there yet.']
      },
      praise: 'd0 waited in the dock while the loop turned, then rejoined: cost {cost}, {steps} steps. Park, turn, return is how every ion on a deck reaches a gate -- Part C builds circuits out of it.',
      boundary: { exercise: 'Say what you did: use the `dock` and `undock` classes for the two spur moves and read the price difference in the Program panel.',
                  check: ['all', ['classUsed', 'dock'], ['classUsed', 'undock'], ['ionAt', 'd0', 'S0'], ['rulesPass']],
                  praise: 'Cost {cost} with the split and the merge accounted for. That is what a dock really costs, and the Report now says so.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d0", ["S0", "A0"])\np.rotate(2)\np.shuttle("d0", ["A0", "S0"])\n' } },

    // =============================================================== R1
    { id: 'R1', part: 'R', title: 'A site holds what it holds',
      story: 'Three ions were sent into a well built for two. The chip did not stretch.',
      teaches: ['rule:R1', 'rule:R3', 'rule:R13', 'el:zone_type'],
      text: [
        'Every rule in this part is one hover away: a rule name in the text, a badge in the Report, a sentence on the strip. [[R1|rule:R1]] is the first and the plainest: a site holds no more ions than its [[zone|el:zone_type]] says. [[R3|rule:R3]] is the same for a rail. [[R13|rule:R13]] is the soft edge of the same idea: a two-qubit gate in a chain of more than about fifteen ions takes too long to be worth doing.',
        'Each step of this lesson loads a programme that breaks one of the three. Evaluate it, read the strip, repair it. The check wants every badge green and the thing the programme was trying to do still done.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit']],
      stages: [
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1", "d2": "S2"})\np.shuttle("d1", ["S1", "S0"])\np.shuttle("d2", ["S2", "S1", "S0"])\n']],
          breaks: [['ruleFails', 'R1']],
          exercise: 'Evaluate: three ions end up in `S0`, whose capacity is two, and the strip says **R1**. Repair it so that `S0` holds exactly two and nothing is red -- `d2` can stop on `S1`.',
          check: ['all', ['rulesPass'], ['occupancy', 'S0', 2]] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1"})\np.simd("shuttle", [["d0", "S1", "S2"], ["d1", "S1", "S2"]])\n']],
          breaks: [['ruleFails', 'R3']],
          exercise: 'A new programme: two ions on one rail in one step, and the strip says **R3** -- this rail carries one. Send them one at a time; both must still arrive on `S2`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S2'], ['ionAt', 'd1', 'S2']] },
        { setup: [['emit', { method: 'set_zone', args: ['trap'], kwargs: { capacity: 16 } }],
                  ['applyProgramSource', 'p.init({"q0": "S0", "q1": "S0", "q2": "S0", "q3": "S0", "q4": "S0", "q5": "S0", "q6": "S0", "q7": "S0", "q8": "S0", "q9": "S0", "q10": "S0", "q11": "S0", "q12": "S0", "q13": "S0", "q14": "S0", "q15": "S0"})\np.cool()\np.gate("CX", [["q0", "q1"]])\n']],
          breaks: [['ruleFails', 'R13']],
          exercise: 'Now the zone holds sixteen, and sixteen ions sit in `S0` when a CX fires: **R13**. Take one ion out to `S1` before the gate.',
          check: ['all', ['rulesPass'], ['gateBetween', 'q0', 'q1', 'CX']] }
      ],
      hints: [
        'The strip under the canvas names the rule and the instruction; the Report panel has the rule\'s full sentence.',
        'R1: change where d2 ends. R3: two simd lines, or two shuttles. R13: a shuttle of q15 to S1 before the cool.',
        'Capacity is per site, per zone: hover S0 to read what it holds.'
      ],
      nudges: {
        rulesPass: ['Still red: {have}', 'Read the sentence on the strip: it names the site and the number.'],
        occupancy: ['S0 holds {have}; the exercise wants exactly two there.'],
        ionAt: ['{have} is not where that ion should end. Both go to S2, one after the other.'],
        gateBetween: ['Green, but the CX is gone. Keep the gate; move one ion first.'],
        'default': ['Not there yet.']
      },
      praise: 'Three rules, three repairs. R1, R3 and R13 are one idea from three sides: a well holds what it holds, a rail carries what it carries, and a chain that is too long gates badly. Cost {cost}, {steps} steps, every badge green.',
      boundary: { exercise: 'The boundary is legal: put exactly two ions in `S0`, cool, and fire a CX between them. Capacity itself is not a violation.',
                  check: ['all', ['occupancy', 'S0', 2], ['gateBetween', 'q0', 'q1', 'CX'], ['rulesPass']],
                  praise: 'Two in a well for sixteen, all green. A rule is a line, and the line itself is on the legal side.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1", "d2": "S2"})\np.shuttle("d1", ["S1", "S0"])\np.shuttle("d2", ["S2", "S1"])\n']],
        [['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1"})\np.simd("shuttle", [["d0", "S1", "S2"]])\np.simd("shuttle", [["d1", "S1", "S2"]])\n']],
        [['applyProgramSource', 'p.init({"q0": "S0", "q1": "S0", "q2": "S0", "q3": "S0", "q4": "S0", "q5": "S0", "q6": "S0", "q7": "S0", "q8": "S0", "q9": "S0", "q10": "S0", "q11": "S0", "q12": "S0", "q13": "S0", "q14": "S0", "q15": "S0"})\np.shuttle("q15", ["S0", "S1"])\np.cool()\np.gate("CX", [["q0", "q1"]])\n']]] } },

    // =============================================================== R2
    { id: 'R2', part: 'R', title: 'Junctions: one at a time',
      story: 'Two ions arrive at the crossroads in the same instant. There is room for one.',
      teaches: ['rule:R2', 'rule:R18', 'rule:R11', 'el:junction'],
      text: [
        'A [[junction|el:junction]] is where rails meet, and [[R2|rule:R2]] says one ion at a time: never two inside it, never two crossing it in one step. [[R18|rule:R18]] says what makes a node a junction at all -- three or more rails -- and that every crossing is priced by that degree, which is why the Report counts *junction transits* on their own line.',
        '[[R11|rule:R11]] has two halves: a loop turns one way per step (you met that in B7), and a site connects to at most two shuttling paths -- a site with a spur hanging off it is a junction for R2\'s purposes, and holds one ion at rest.'
      ],
      setup: [
        ['newCanvas', { name: 'cross' }],
        ['addNodeAt', 1, 1, { id: 'J0', kind: 'junction' }],
        ['addNodeAt', 0, 1, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 1, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 2, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'J0', { id: 'E0' }],
        ['joinNodes', 'J0', 'S1', { id: 'E1' }],
        ['joinNodes', 'S2', 'J0', { id: 'E2' }],
        ['joinNodes', 'J0', 'S3', { id: 'E3' }],
        ['fit']],
      stages: [
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.simd("shuttle", [["d0", "S0", "S1", ["E0", "E1"]], ["d1", "S2", "S3", ["E2", "E3"]]])\n']],
          breaks: [['ruleFails', 'R2']],
          exercise: 'Evaluate: two ions cross `J0` in one step, and the strip says **R2**. Serialise them -- one crossing per instruction -- with `d0` still ending on `S1` and `d1` on `S3`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S3'], ['transitsAtMost', 2]] },
        { exercise: 'R18: what makes a node a junction, and why does the Report care?',
          check: ['answer', 'deg3'] },
        { setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit'],
                  ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d1", ["S1", "S0"])\n']],
          breaks: [['ruleFails', 'R2']],
          exercise: 'A ring with spurs. `S0` has three rails (two on the loop, one to the dock `A0`), so it is a junction for R2: one ion at rest, and none passing through while one sits there. The loaded programme parks a second one on it. Move `d0` out of the way to `S5`, then send `d1` through `S0` into the dock `A0`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd1', 'A0']] }
      ],
      choices: [
        { id: 'square', text: 'It is drawn as a square.' },
        { id: 'deg3', text: 'Three or more rails meet there -- and each crossing is priced by that degree.' },
        { id: 'any', text: 'Any node an ion passes without stopping.' }
      ],
      hints: [
        'Two p.shuttle lines, each through J0, one after the other.',
        'Degree is the number of rails at a node; three is what makes a junction (R18). Hover the junction to read its degree.',
        'First p.shuttle("d0", ["S0", "S5"]); then p.shuttle("d1", ["S1", "S0", "A0"]) -- through the empty S0, into the dock.'
      ],
      nudges: {
        rulesPass: ['Still red: {have}', 'One crossing per step -- and a spur site with an ion on it cannot be passed through. Clear it first.'],
        ionAt: ['{have} is not where that ion should end.'],
        transitsAtMost: ['{have} junction transits: each ion crosses once, so two is the number.'],
        answer: ['Look at the picture: what do S0 and J0 have that the other sites do not?', 'The Report prices a crossing by the junction\'s degree. What is a degree?'],
        'default': ['Not there yet.']
      },
      praise: 'One at a time through the crossroads, and the dock as the second ion\'s home. {transits} transits, cost {cost}. A junction is the most expensive thing an ion can cross, and now you know why the Report counts them.',
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d0", ["S0", "J0", "S1"])\np.shuttle("d1", ["S2", "J0", "S3"])\n']],
        [['setAnswer', 'deg3']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d0", ["S0", "S5"])\np.shuttle("d1", ["S1", "S0", "A0"])\n']]] } },

    // =============================================================== R3
    { id: 'R3', part: 'R', title: 'Rails: no passing, no ghosts',
      story: 'Two ions on one rail, walking toward each other. They cannot pass.',
      teaches: ['rule:R5', 'rule:R8', 'el:segment'],
      text: [
        'A [[rail|el:segment]] is one dimensional. [[R5|rule:R5]]: two ions never exchange positions along one rail in one step -- there is no lane to pass in. This rail has been given capacity two so that R5 is the only thing in the way.',
        '[[R8|rule:R8]] is the bookkeeping rule: outside loading and unloading, the map from ions to sites is a function -- no ion appears, none vanishes, and none is in two moves of one step. It sounds trivial. It is what makes every other rule checkable.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0', capacity: 2 }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit']],
      stages: [
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S1", "S0"]])\n']],
          breaks: [['ruleFails', 'R5']],
          exercise: 'Evaluate: `d0` and `d1` swap across `E0` in one step, and the strip says **R5**. Swap them legally -- `d0` ends on `S1`, `d1` on `S0` -- using `S2` as the passing place.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S0']] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S0"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d0", "S1", "S2"]])\n']],
          breaks: [['ruleFails', 'R8']],
          exercise: 'One ion in two moves of one step: the strip says **R8**. Get `d0` to `S2` with the map kept a function -- one shuttle, or two steps.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S2']] }
      ],
      hints: [
        'd1 steps aside to S2; d0 moves up to S1; d1 comes back down through S1 to S0. Three shuttles.',
        'A site of capacity two can be passed through while one ion sits in it -- the Report calls that a transit.',
        'p.shuttle("d0", ["S0", "S1", "S2"]) is one statement and two steps: the ion is in one move per step.'
      ],
      nudges: {
        rulesPass: ['Still red: {have}', 'No exchange on one rail in one step. Use the third site.'],
        ionAt: ['{have} is not where that ion should end.'],
        'default': ['Not there yet.']
      },
      praise: 'A legal swap and an honest walk: cost {cost}, {steps} steps. R5 is why sorting ions on a line is expensive, and R8 is why the page can tell you so.',
      boundary: { exercise: 'A convoy is legal: from `d0` on `S1` and `d1` on `S0`, move both one rail up (`S1` to `S2`, `S0` to `S1`) in one step -- same direction, different rails.',
                  check: ['all', ['frames', 2, 2], ['ionAt', 'd0', 'S2'], ['ionAt', 'd1', 'S1'], ['rulesPass']],
                  praise: 'One step, two rails, one direction: that is how a chain of ions moves along a line without ever breaking R5.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d1", ["S1", "S2"])\np.shuttle("d0", ["S0", "S1"])\np.shuttle("d1", ["S2", "S1", "S0"])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\n']]] } },

    // =============================================================== R4
    { id: 'R4', part: 'R', title: 'Steps: one kind of thing',
      story: 'A step is one setting of every voltage on the chip. It can be one thing.',
      teaches: ['rule:R4', 'rule:R4b', 'rule:R12', 'el:control'],
      text: [
        '[[R4|rule:R4]]: a step moves ions by one declared [[movement class|el:control]] -- a class fixes the kind and the direction of the motion, and the device lists the classes it can drive. [[R4b|rule:R4b]]: a step is transport *or* gates, never both; they use different control pathways. [[R12|rule:R12]]: inside one trap, one gate at a time.',
        'The first of these is caught before any rule runs: a class the device does not declare cannot be priced, so the page withholds the price and says why. The second is caught by the language itself. Only the third gets as far as a red badge.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit']],
      stages: [
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("teleport", [["d0", "S0", "S1"]])\n']],
          breaks: [['priceBlocked']],
          exercise: 'Evaluate: the head goes quiet and the strip says the class `teleport` is unknown -- **R4** wants a declared class. Open **Control plane** to see the list, and rename the class to one of them.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1']] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"]], "both")\n']],
          breaks: [['refused', 'R4b']],
          exercise: 'Now the step asks to be intra- and inter-trap at once: the language refuses the statement and names **R4b**. Read the refusal on the strip, then make it a plain transport step.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1']] },
        { setup: [['emit', { method: 'set_zone', args: ['trap'], kwargs: { capacity: 4 } }],
                  ['applyProgramSource', 'p.init({"d0": "A0", "d1": "A0", "d2": "A0", "d3": "A0"})\np.cool()\np.gate("CX", [["d0", "d1"], ["d2", "d3"]])\n']],
          breaks: [['ruleFails', 'R12']],
          exercise: 'Four ions in the dock `A0` and two CX pairs in one instruction: **R12**, one gate per trap per step. Split them into two instructions.',
          check: ['all', ['rulesPass'], ['gateCount', 'CX', 2]] }
      ],
      hints: [
        'The Control plane tile lists the classes: shuttle, rotate_cw, rotate_ccw, dock, undock and two sorting moves.',
        'p.simd takes a third argument, the mode: "inter" (between traps) or "intra". Leave it out and it is inter.',
        'Two p.gate lines, one pair each. They happen in two steps.'
      ],
      nudges: {
        rulesPass: ['Still not green: {have}', 'One class per step, from the declared list; one gate per trap per step.'],
        ionAt: ['{have} is not where d0 should end.'],
        gateCount: ['{have} CX gates: the exercise keeps both pairs, in two instructions.'],
        'default': ['Not there yet.']
      },
      praise: 'Three ways a step can be too much, three repairs: a declared class, one pathway, one gate per trap. Cost {cost}, {steps} steps. R4 is the rule the whole idea of SIMD control comes from.',
      boundary: { exercise: 'Two gates in one step **is** legal in two different traps: put `d2` and `d3` in `A3` instead, and fire both pairs in a single instruction.',
                  check: ['all', ['frames', 3, 3], ['gateBetween', 'd0', 'd1', 'CX'], ['gateBetween', 'd2', 'd3', 'CX'], ['rulesPass']],
                  praise: 'Two traps, two gates, one step: inter-trap parallelism is unconstrained, and that is where all the speed of these machines lives.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "A0", "d1": "A0", "d2": "A0", "d3": "A0"})\np.cool()\np.gate("CX", [["d0", "d1"]])\np.gate("CX", [["d2", "d3"]])\n']]] } },

    // =============================================================== R5
    { id: 'R5', part: 'R', title: 'Broadcasting: what one channel can drive',
      story: 'The chip has one answer for every site on a channel. Two ions asked for two.',
      teaches: ['rule:R4d', 'rule:R11', 'el:control', 'm:DACs'],
      text: [
        '[[R4d|rule:R4d]] is R4 derived from the wiring instead of declared: a channel carries one waveform, so the sites sharing it can do one thing per step, and a site can only sit out if it has a switch of its own. The browser does not run this check -- it needs the electrode-to-channel map -- so the box below is **Python\'s verdict**, computed on this exact programme when the page was built.',
        'The programme is B7\'s head-on pair on a ring wired *wise*. The page catches it as R11; Python names the channel and counts what it was asked to do.'
      ],
      setup: [['loadCase', 'R5'], ['fit']],
      verdict: { 'case': 'R5', which: 'loaded' },
      stages: [
        { exercise: 'Read Python\'s sentence: *channel drives 8 sites with one waveform, but they are asked to do 2 different things; that needs 2 channels.* Which repair does it point to?',
          check: ['answer', 'steps'] },
        { breaks: [['ruleFails', 'R11'], ['shipped', 'R5', 'loaded', 'R4d', 'failed']],
          exercise: 'Apply it: two instructions, one direction each, `d0` ending on `S1` and `d1` on `S2`. The page\'s R11 goes green -- and the second box is Python\'s verdict on the repaired programme, shipped with the page.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S2'], ['shipped', 'R5', 'fixed', 'R4d', 'passed']],
          verdict: { 'case': 'R5', which: 'fixed' } }
      ],
      choices: [
        { id: 'rename', text: 'Rename the movement class.' },
        { id: 'switches', text: 'Add per-site switches -- a switch lets a site sit out, but not do a different thing.' },
        { id: 'steps', text: 'Give each direction its own step -- or its own channel; that is what "needs 2 channels" means.' }
      ],
      hints: [
        'The sentence ends with what it needs: two channels. With one, the only other way to get two things is two steps.',
        'Switches are about opting out, not about doing something else; the verdict says "2 different things".',
        'Two p.simd lines, one move each: d1 back first, then d0 forward.'
      ],
      nudges: {
        answer: ['Read the last clause of Python\'s sentence again.', 'Two things on one waveform is the problem; renaming changes nothing physical.'],
        rulesPass: ['Still red: {have}', 'One direction per step. Split the two moves.'],
        ionAt: ['{have} is not where that ion should end.'],
        'default': ['Not there yet.']
      },
      praise: 'Python\'s R4d went from failed to passed on the programme you wrote, and the page\'s R11 agreed at every step. That is broadcasting as a rule: one waveform, one motion, per channel, per step -- and cost {cost} for doing it right.',
      solution: { stages: [
        [['setAnswer', 'steps']],
        [['loadCase', 'R5', 'fixed'], ['fit']]] } },

    // =============================================================== R6
    { id: 'R6', part: 'R', title: 'Where things may happen',
      story: 'A gate is a laser beam and a measurement is a camera. Not every site has either.',
      teaches: ['rule:R6', 'rule:R6b', 'el:zone_type'],
      text: [
        '[[R6|rule:R6]]: a gate, a measurement or a cooling pulse happens only in a site whose [[zone|el:zone_type]] has that capability -- `trap` has all three here, `data` has cooling only. [[R6b|rule:R6b]]: a two-qubit gate acts on two ions in one site, and if the instruction declares a site, it must be where they are.',
        'This line has a `data` site in the middle. The first programme measures there; the second fires a gate on ions that never met.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'data' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit']],
      stages: [
        { setup: [['applyProgramSource', 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1"])\np.measure(["d0"])\n']],
          breaks: [['ruleFails', 'R6']],
          exercise: 'Evaluate: the measurement lands on `S1`, a `data` site with no *spam*, and the strip says **R6**. Read `d0` out somewhere it can be read -- it still has to move first.',
          check: ['all', ['rulesPass'], ['measured', ['d0']], ['visited', 'd0', 'S1']] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.cool()\np.gate("CX", [["d0", "d1"]])\n']],
          breaks: [['ruleFails', 'R6b']],
          exercise: 'A CX between `d0` on `S0` and `d1` on `S2`: **R6b**, not co-located. Bring them together in a site that allows gates, and fire.',
          check: ['all', ['rulesPass'], ['gateBetween', 'd0', 'd1', 'CX']] }
      ],
      hints: [
        'Hover the sites: S0 and S2 are trap (gate, measure, cool); S1 is data (cool only).',
        'Walk d0 on through S1 to S2, then measure it there.',
        'd1 walks S2, S1, S0 -- through the data site, which is allowed; the gate then fires in S0.'
      ],
      nudges: {
        rulesPass: ['Still red: {have}', 'Which zone allows what? Hover a site to read its capabilities.'],
        measured: ['d0 is not measured yet.'],
        visited: ['d0 has to pass through S1 on its way -- the exercise keeps the move.'],
        gateBetween: ['Green, but the CX is gone. Keep the gate; move the ions to it.'],
        'default': ['Not there yet.']
      },
      praise: 'Measured where a camera looks, gated where a beam reaches: cost {cost}, {steps} steps. R6 is the reason zones exist at all, and R6b is why a compiler spends most of its effort on transport.',
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\np.measure(["d0"])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']]] } },

    // =============================================================== R7
    { id: 'R7', part: 'R', title: 'Heat: the budget, the clock, and why cooling is mandatory',
      story: 'The ion crossed the junction and arrived shaking. The gate fired anyway -- in the programme. Not on the machine.',
      teaches: ['rule:R7', 'rule:R7c', 'rule:R17', 'rule:R15', 'rule:R16', 'rule:R7b', 'm:peak n̄', 'el:heating'],
      text: [
        'Transport heats an ion; a junction crossing heats it most. [[R7|rule:R7]]: both ions of a two-qubit gate must be under the budget -- one quantum on this device -- when the gate fires. [[R7c|rule:R7c]]: under broadcast wiring cooling is mandatory, so a programme with gates and no cooling anywhere is refused outright. [[R17|rule:R17]]: heat also accrues with time, even at rest; the model adds it per millisecond.',
        'Three badges never go green here. [[R15|rule:R15]] is amber: the page adds quanta, which is an upper bound. [[R16|rule:R16]] is grey in the browser and checked by Python: the gate error is a function of n-bar at gate time, and the box below shows the number. [[R7b|rule:R7b]] is grey everywhere: no device declares a duty-cycle budget, so there is nothing to check -- and the badge says so instead of pretending.'
      ],
      setup: [['loadCase', 'R7'], ['fit']],
      verdict: { 'case': 'R7', which: 'loaded' },
      stages: [
        { breaks: [['ruleFails', 'R7']],
          exercise: 'Evaluate: `d0` crosses the junction at `S0` and enters the gate at about 3.2 quanta, and the strip says **R7**. The `p.cool()` before the walk did nothing for it. Move the cooling to after the transport.',
          check: ['all', ['rulesPass'], ['gateBetween', 'd0', 'd1', 'CX']],
          verdict: { 'case': 'R7', which: 'loaded' } },
        { exercise: 'Compare the two boxes: Python\'s gate error (R16) on the hot programme and on the repaired one. Then: why is R15 amber and R7b grey in every Report?',
          check: ['answer', 'bound'],
          verdict: { 'case': 'R7', which: 'fixed' } }
      ],
      choices: [
        { id: 'stale', text: 'They have not been checked yet; reload the page.' },
        { id: 'bound', text: 'R15: quanta are added, which is an upper bound, so the verdict is partial. R7b: no device declares a duty-cycle budget, so there is nothing to check, and the badge says so.' },
        { id: 'browser', text: 'The browser cannot check them but Python did, and passed both.' }
      ],
      hints: [
        'The order of the lines is the order things happen. Cooling before the walk cools an ion that is about to get hot again.',
        'p.cool() between the shuttle and the gate: the ion arrives, is cooled, then gates.',
        'Amber is an honest bound; grey is an honest "nothing to check". Neither is a pass, and neither is a failure.'
      ],
      nudges: {
        rulesPass: ['Still red: {have}', 'Where is the cooling relative to the crossing? It must come after.'],
        gateBetween: ['Green, but the CX is gone. Keep the gate; move the cooling.'],
        answer: ['A partial verdict is a bound; a skipped one says what is missing. Which sentence says both?', 'Neither badge is stale, and Python does not pass R7b either -- hover the grey badge in the Report.'],
        'default': ['Not there yet.']
      },
      praise: 'Cooled after the crossing, gated under budget: cost {cost}, {steps} steps. Python\'s gate error fell with it. R7 is the rule that makes cooling a scheduling problem, and R15, R16 and R7b are the page telling you exactly how much it knows.',
      solution: { stages: [
        [['loadCase', 'R7', 'fixed'], ['fit']],
        [['setAnswer', 'bound']]] } },

    // =============================================================== R8
    { id: 'R8', part: 'R', title: 'Chains: splitting from the middle',
      story: 'Three ions in a row, and the one you want is in the middle.',
      teaches: ['rule:R14', 'el:site'],
      text: [
        'Ions in one site sit in a line, a chain. Only an ion at the end of the chain can be split off; [[R14|rule:R14]] says an ion in the middle must first swap to the edge, and a swap is three CNOTs. A programme that splits from the middle without accounting for that is charged nothing for something the machine has to do.',
        'The `data` zone has been given capacity three, and three ions sit in `S1`. The loaded programme docks the middle one.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit'],
        ['emit', { method: 'set_zone', args: ['data'], kwargs: { capacity: 3 } }],
        ['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1", "d2": "S1"})\np.shuttle("d1", ["S1", "S0", "A0"], "dock")\n']],
      stages: [
        { breaks: [['ruleFails', 'R14']],
          exercise: 'Evaluate: `d1` splits from a chain of three and the strip says **R14**. Move `d2` out to `S2` first, so that `d1` is at the edge of a chain of two, then dock it.',
          check: ['all', ['rulesPass'], ['ionAt', 'd1', 'A0']] },
        { exercise: 'R14 charges three CX for an ion to reach the edge. Why three?',
          check: ['answer', 'swap'] }
      ],
      choices: [
        { id: 'three_rails', text: 'The ion crosses three rails on the way.' },
        { id: 'swap', text: 'A swap of two qubits is three CNOTs, and the ion must swap past its neighbour.' },
        { id: 'cooling', text: 'Three cooling pulses are needed after a split.' }
      ],
      hints: [
        'A shuttle of d2 from S1 to S2 before the dock line.',
        'The dock class entails a split; the rule only fires when the chain it splits from is longer than two.',
        'SWAP = CX, CX, CX. That is where the three comes from.'
      ],
      nudges: {
        rulesPass: ['Still red: {have}', 'Shorten the chain before the split.'],
        ionAt: ['{have} is not where d1 should end: the dock A0.'],
        answer: ['What is a SWAP gate made of?'],
        'default': ['Not there yet.']
      },
      praise: 'Edge first, then split: cost {cost}, {steps} steps -- and an honest price for reaching the edge. R14 is why a compiler cares which ion is where inside a site, not only which site.',
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1", "d2": "S1"})\np.shuttle("d2", ["S1", "S2"])\np.shuttle("d1", ["S1", "S0", "A0"], "dock")\n']],
        [['setAnswer', 'swap']]] } },

    // =============================================================== R9
    { id: 'R9', part: 'R', title: 'Honesty: claims, certificates and the contract',
      story: 'A programme said it would cost two. The replay said three.',
      teaches: ['rule:R9', 'rule:R10', 'tab:R'],
      text: [
        'A programme may carry claims about itself: `p.claim(total_steps=4, total_cost=3)`. [[R9|rule:R9]] checks every claim against the replay, and the browser does not run it -- so the [[Report|tab:R]] shows R9 grey and the box below shows Python\'s verdict. [[R10|rule:R10]] says a compiled programme implements its circuit; it needs the circuit, which no lesson so far has had.',
        'The contract behind every badge: **green** means the check ran and passed; **red** means it ran and failed; **amber** means it ran and can only give a bound; **grey** means it did not run here, and the badge says why. Nothing on this page is green for a check that did not happen.'
      ],
      setup: [['loadCase', 'R9'], ['fit']],
      verdict: { 'case': 'R9', which: 'loaded' },
      stages: [
        { exercise: 'Open **Report**. R9 is grey. What does grey mean on this page?',
          check: ['answer', 'notrun'] },
        { breaks: [['shipped', 'R9', 'loaded', 'R9', 'failed']],
          exercise: 'Python\'s verdict says what the programme claimed and what the replay found. Correct the `p.claim` line so the two agree -- the head shows the cost and the steps.',
          check: ['all', ['rulesPass'], ['claimIs', 'total_steps', 4], ['claimIs', 'total_cost', 3], ['shipped', 'R9', 'fixed', 'R9', 'passed']],
          verdict: { 'case': 'R9', which: 'fixed' } },
        { exercise: 'R10 is grey on every page in this course so far. Why?',
          check: ['answer', 'nocircuit'],
          verdict: { 'case': 'R9', which: 'fixed' } }
      ],
      choices: [
        { id: 'passed', text: 'Passed, with warnings.' },
        { id: 'notrun', text: 'The check did not run here -- and the badge says why.' },
        { id: 'nocircuit', text: 'There is no circuit to check against: R10 needs the QASM a programme was compiled from, which Part D brings.' },
        { id: 'failed', text: 'Failed quietly.' }
      ],
      hints: [
        'Hover the grey R9 badge: it says the claim was not checked in the browser.',
        'The head reads cost 3 and 4 steps; the claim says 2 and 3. Edit the numbers in p.claim.',
        'A hand-written programme has no circuit. A compiled one does, and Part D opens one.'
      ],
      nudges: {
        answer: ['Hover the badge; its tooltip is the answer.', 'Grey is neither a pass nor a failure.'],
        rulesPass: ['A rule is red: {have}'],
        claimIs: ['The claim for that number is {have}; the head shows what the replay found.'],
        'default': ['Not there yet.']
      },
      praise: 'A true claim, and Python\'s R9 turned from failed to passed on it. That is the whole contract: a number on this page is either measured, bounded, or marked as not measured -- never guessed. Cost {cost}, {steps} steps, and both are now also what the programme says.',
      solution: { stages: [
        [['setAnswer', 'notrun']],
        [['loadCase', 'R9', 'fixed'], ['fit']],
        [['setAnswer', 'nocircuit']]] } },

    // =============================================================== C1
    { id: 'C1', part: 'C', title: 'A syndrome round of the repetition code',
      story: 'Three data ions, two check ions, four CNOTs, two measurements. The smallest error-correcting round there is, on a line of five wells.',
      teaches: ['p:gate', 'p:measure', 'rule:R1', 'rule:R6b'],
      text: [
        'The three-qubit repetition code keeps one logical bit in `d0 d1 d2` and asks two check ions whether neighbours agree: `c0` collects `d0` and `d1`, `c1` collects `d1` and `d2`. As a circuit that is four CNOTs -- CX(d0,c0), CX(d1,c0), CX(d1,c1), CX(d2,c1) -- and a measurement of both checks.',
        'On the line the ions start alternating: `d0 c0 d1 c1 d2` in `S0`..`S4`. Every well holds two, so a check ion can visit a data ion but the two checks can never share `S2` with `d1` at once ([[R1|rule:R1]]). Every gate needs both ions in one well ([[R6b|rule:R6b]]), and one cooling anywhere is enough for this little heat.'
      ],
      setup: [
        ['newCanvas', { name: 'line5' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 3, 0, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 4, 0, { id: 'S4', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['joinNodes', 'S2', 'S3', { id: 'E2' }],
        ['joinNodes', 'S3', 'S4', { id: 'E3' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "c0": "S1", "d1": "S2", "c1": "S3", "d2": "S4"})\n']],
      exercise: 'Implement the round: the four CNOTs (control first, check ion second) and a measurement of `c0` and `c1`, every badge green.',
      check: ['all', ['gateBetween', 'd0', 'c0', 'CX'], ['gateBetween', 'd1', 'c0', 'CX'], ['gateBetween', 'd1', 'c1', 'CX'], ['gateBetween', 'd2', 'c1', 'CX'], ['measured', ['c0', 'c1']], ['rulesPass']],
      hints: [
        'c0 walks to d0, gates, walks to d1, gates; then c0 leaves S2 before c1 arrives there; c1 gates with d1, walks to d2, gates. One p.cool() near the top.',
        'p.shuttle("c0", ["S1", "S0"]) then p.gate("CX", [["d0", "c0"]]) -- and so on down the line.',
        'The measurement at the end names both check ions: p.measure(["c0", "c1"]). They must be in trap sites, which every site here is.'
      ],
      nudges: {
        gateBetween: ['A CNOT is missing: {have}', 'Four pairs: d0-c0, d1-c0, d1-c1, d2-c1. Check the ion names in each.'],
        measured: ['Not measured yet: {have}.'],
        rulesPass: ['A rule is red: {have}', 'Two checks and d1 cannot all be in S2: move c0 away before c1 comes.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet.']
      },
      praise: 'A syndrome round, by hand: {frames} instructions, {steps} steps, cost {cost}. Every CNOT of the circuit is a journey and a gate, and you scheduled all four under the rules.',
      solution: { program: 'p.init({"d0": "S0", "c0": "S1", "d1": "S2", "c1": "S3", "d2": "S4"})\np.cool()\np.shuttle("c0", ["S1", "S0"])\np.gate("CX", [["d0", "c0"]])\np.shuttle("c0", ["S0", "S1", "S2"])\np.gate("CX", [["d1", "c0"]])\np.shuttle("c0", ["S2", "S1"])\np.shuttle("c1", ["S3", "S2"])\np.gate("CX", [["d1", "c1"]])\np.shuttle("c1", ["S2", "S3", "S4"])\np.gate("CX", [["d2", "c1"]])\np.measure(["c0", "c1"])\n' } },

    // =============================================================== C2
    { id: 'C2', part: 'C', title: 'Parallelism under the rules',
      story: 'The same round, faster. Not by breaking a rule -- by using the ones that allow things.',
      teaches: ['p:simd', 'rule:R12', 'rule:R4', 'rule:R22', 'm:steps'],
      text: [
        'Two rules allow more than the first round used, and one sets the price. [[R12|rule:R12]] forbids two gates in one trap per step -- but two gates in two traps in one instruction are fine: `p.gate("CX", [["d0", "c0"], ["d1", "c1"]])`. [[R4|rule:R4]] lets one `p.simd` move any number of ions in one step -- as long as they all move the same way. One step is one waveform on the electrodes ([[R22|rule:R22]]), so `d0` stepping right while `d2` steps left takes two steps, not one.',
        'So pick moves that agree. The two check ions sit the same distance apart as the data ions they visit: walk them together, and every move is shared.',
        'The first round took thirteen steps. The check counts steps, so every instruction you merge shows up in the head.'
      ],
      setup: [
        ['newCanvas', { name: 'line5' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 3, 0, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 4, 0, { id: 'S4', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['joinNodes', 'S2', 'S3', { id: 'E2' }],
        ['joinNodes', 'S3', 'S4', { id: 'E3' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "c0": "S1", "d1": "S2", "c1": "S3", "d2": "S4"})\n']],
      exercise: 'The same round -- four CNOTs, both checks measured -- in **at most 10 steps**.',
      check: ['all', ['gateBetween', 'd0', 'c0', 'CX'], ['gateBetween', 'd1', 'c0', 'CX'], ['gateBetween', 'd1', 'c1', 'CX'], ['gateBetween', 'd2', 'c1', 'CX'], ['measured', ['c0', 'c1']], ['rulesPass'], ['stepsAtMost', 10]],
      hints: [
        'Move both checks one site left in one simd -- `c0` onto `d0`, `c1` onto `d1` -- and fire both CNOTs in one instruction.',
        'Then walk both checks right, one site per simd, until `c0` sits on `d1` and `c1` on `d2`, and fire both CNOTs again. Every simd moves the two checks the same way.',
        'One cool at the top, and one simd back left so each check is measured alone. Count: cool, left, gate, right, right, gate, left, measure -- eight.'
      ],
      nudges: {
        gateBetween: ['A CNOT is missing: {have}'],
        measured: ['Not measured yet: {have}.'],
        rulesPass: ['A rule is red: {have}'],
        stepsAtMost: ['{have} steps. Which moves can share an instruction? Which gates can?', 'Two gates in two different traps may share one instruction (R12 is per trap).', 'Moves share a step only when they go the same way (R22). Move the two checks together.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet.']
      },
      praise: '{steps} steps for the round that took thirteen. Nothing was skipped: four CNOTs, two measurements, every rule green -- the machine was simply asked to do several legal things at once.',
      solution: { program: 'p.init({"d0": "S0", "c0": "S1", "d1": "S2", "c1": "S3", "d2": "S4"})\np.cool()\np.simd("shuttle", [["c0", "S1", "S0"], ["c1", "S3", "S2"]])\np.gate("CX", [["d0", "c0"], ["d1", "c1"]])\np.simd("shuttle", [["c0", "S0", "S1"], ["c1", "S2", "S3"]])\np.simd("shuttle", [["c0", "S1", "S2"], ["c1", "S3", "S4"]])\np.gate("CX", [["d1", "c0"], ["d2", "c1"]])\np.simd("shuttle", [["c0", "S2", "S1"], ["c1", "S4", "S3"]])\np.measure(["c0", "c1"])\n' } },

    // =============================================================== C3
    { id: 'C3', part: 'C', title: 'One plaquette',
      story: 'Four data ions around a crossroads, and one check ion that must visit each of them.',
      teaches: ['el:junction', 'rule:R2', 'rule:R7', 'm:junctiontransits'],
      text: [
        'A surface-code check is a plaquette: one check ion touches four data ions. Here the four sit on the arms of a junction, and `a0` starts beside `d0`. Each visit is a CNOT with the data ion as control; each move between arms crosses the [[junction|el:junction]] once -- [[R2|rule:R2]] allows one crossing per step, and the Report counts them as *junction transits*.',
        'A crossing heats the ion by three quanta on this chip, against a gate budget of one ([[R7|rule:R7]]). So the shape of the programme is forced: gate, cross, cool, gate, cross, cool ... Cooling is not a courtesy here; it is the only way the next gate is legal.'
      ],
      setup: [
        ['newCanvas', { name: 'plaquette' }],
        ['addNodeAt', 1, 1, { id: 'J0', kind: 'junction' }],
        ['addNodeAt', 0, 1, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 1, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 2, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'J0', { id: 'E0' }],
        ['joinNodes', 'J0', 'S1', { id: 'E1' }],
        ['joinNodes', 'S2', 'J0', { id: 'E2' }],
        ['joinNodes', 'J0', 'S3', { id: 'E3' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1", "d2": "S2", "d3": "S3", "a0": "S0"})\n']],
      exercise: 'Measure the check on all four: CX from each data ion onto `a0`, then measure `a0`, with **at most 3 junction transits** and every badge green.',
      check: ['all', ['gateBetween', 'd0', 'a0', 'CX'], ['gateBetween', 'd1', 'a0', 'CX'], ['gateBetween', 'd2', 'a0', 'CX'], ['gateBetween', 'd3', 'a0', 'CX'], ['measured', ['a0']], ['transitsAtMost', 3], ['rulesPass']],
      hints: [
        'Gate with d0 where a0 already is; then p.shuttle("a0", ["S0", "J0", "S1"]), cool, gate with d1; and so on around.',
        'Without a p.cool() after each crossing, the next gate fails R7 at about 3.2 quanta.',
        'Three crossings visit four arms: S0, S1, S2, S3. Measure a0 at the last one.'
      ],
      nudges: {
        gateBetween: ['A CNOT is missing: {have}'],
        measured: ['a0 is not measured yet.'],
        transitsAtMost: ['{have} junction transits. Four arms need only three crossings.'],
        rulesPass: ['A rule is red: {have}', 'Cool after every crossing; the gate budget is one quantum.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet.']
      },
      praise: 'One plaquette: {transits} crossings, {steps} steps, cost {cost}, runtime {us} us. Most of that time is the three crossings and the three coolings they force -- which is the number a grid architecture lives or dies by.',
      solution: { program: 'p.init({"d0": "S0", "d1": "S1", "d2": "S2", "d3": "S3", "a0": "S0"})\np.cool()\np.gate("CX", [["d0", "a0"]])\np.shuttle("a0", ["S0", "J0", "S1"])\np.cool()\np.gate("CX", [["d1", "a0"]])\np.shuttle("a0", ["S1", "J0", "S2"])\np.cool()\np.gate("CX", [["d2", "a0"]])\np.shuttle("a0", ["S2", "J0", "S3"])\np.cool()\np.gate("CX", [["d3", "a0"]])\np.measure(["a0"])\n' } },

    // =============================================================== C4
    { id: 'C4', part: 'C', title: 'Two rounds and cooling',
      story: 'Error correction never stops. The second round starts where the first one ended.',
      teaches: ['rule:R7c', 'rule:R7', 'm:runtime', 'p:reset'],
      text: [
        'After the measurement, `a0` is reset ([[p.reset|p:reset]]) and the round runs again. The naive second round walks `a0` back to `d0` first -- three crossings for nothing. The better one runs the plaquette in reverse order from where `a0` already is.',
        'The check is on **runtime**: the head shows it, and the Report breaks it into transport, cooling and gates. Every crossing you save saves a cooling too.'
      ],
      setup: [
        ['newCanvas', { name: 'plaquette' }],
        ['addNodeAt', 1, 1, { id: 'J0', kind: 'junction' }],
        ['addNodeAt', 0, 1, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 1, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 2, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S3', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'J0', { id: 'E0' }],
        ['joinNodes', 'J0', 'S1', { id: 'E1' }],
        ['joinNodes', 'S2', 'J0', { id: 'E2' }],
        ['joinNodes', 'J0', 'S3', { id: 'E3' }],
        ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1", "d2": "S2", "d3": "S3", "a0": "S0"})\n']],
      exercise: 'Two full rounds -- eight CNOTs, two measurements, a reset between -- in **under 3300 us**, every badge green.',
      check: ['all', ['gateCount', 'CX', 8], ['measured', ['a0']], ['hasFrame', 'reset'], ['runtimeAtMost', 3300], ['rulesPass']],
      hints: [
        'Round one as in C3. Then p.reset(["a0"]) and gate with d3 right away -- a0 is already there.',
        'Round two goes d3, d2, d1, d0: the same three crossings, in reverse, with a cool after each.',
        'Walking back to S0 first costs three extra crossings and about 400 us. The bound forbids it.'
      ],
      nudges: {
        gateCount: ['{have} CX gates so far; two rounds are eight.'],
        hasFrame: ['No reset between the rounds: p.reset(["a0"]) after the first measurement.'],
        runtimeAtMost: ['{have} us. Where does a0 start its second round? Do not walk it back.', 'Six crossings in all is the minimum for two rounds; count yours in the Report.'],
        rulesPass: ['A rule is red: {have}'],
        measured: ['a0 is not measured yet.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet.']
      },
      praise: 'Two rounds in {us} us: {gates} CNOTs, six crossings, six coolings, and the reset between. That is a syndrome extraction loop, scheduled by hand under all twenty-three rules -- and Part D shows you what the compiler makes of the same job.',
      challenge: { label: 'sharp', exercise: 'Sharp: the same two rounds with cooling only where a gate needs it -- cool just `a0` (`p.cool(["a0"])`) after each crossing, nothing else -- still under 3300 us and green.',
                   check: ['all', ['gateCount', 'CX', 8], ['runtimeAtMost', 3300], ['rulesPass'], ['not', ['broadcastCool']]],
                   praise: 'Targeted cooling, and the same runtime: on this chip a cooling pulse costs the same for one ion as for all. That is a fact you now know about the model, not a guess.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S1", "d2": "S2", "d3": "S3", "a0": "S0"})\np.cool()\np.gate("CX", [["d0", "a0"]])\np.shuttle("a0", ["S0", "J0", "S1"])\np.cool()\np.gate("CX", [["d1", "a0"]])\np.shuttle("a0", ["S1", "J0", "S2"])\np.cool()\np.gate("CX", [["d2", "a0"]])\np.shuttle("a0", ["S2", "J0", "S3"])\np.cool()\np.gate("CX", [["d3", "a0"]])\np.measure(["a0"])\np.reset(["a0"])\np.gate("CX", [["d3", "a0"]])\np.shuttle("a0", ["S3", "J0", "S2"])\np.cool()\np.gate("CX", [["d2", "a0"]])\np.shuttle("a0", ["S2", "J0", "S1"])\np.cool()\np.gate("CX", [["d1", "a0"]])\np.shuttle("a0", ["S1", "J0", "S0"])\np.cool()\np.gate("CX", [["d0", "a0"]])\np.measure(["a0"])\n' } },

    // =============================================================== D1
    { id: 'D1', part: 'D', title: 'What the compiler does',
      story: 'You have written every instruction by hand. Now read what a compiler wrote for a real circuit, and find the line that is the CNOT.',
      teaches: ['tab:Q', 'rule:R10', 'tab:P'],
      page: 'micro_grid9x9',
      text: [
        'The compiler takes a circuit -- `micro.qasm`, four qubits and seven statements -- and a device, and writes the hardware programme: where the ions start, every shuttle, every gate as the pulses that make it, every cooling. It also writes a **certificate**: for every instruction, which statement of the circuit it realises. [[R10|rule:R10]] is the rule that the programme implements the circuit, and the certificate is its evidence.',
        'On the compiled page the [[Circuit|tab:Q]] pane shows the QASM beside the animation: the statement the current instruction realises is marked, and the one the ions are travelling towards is marked more faintly. Click a statement and the animation jumps to the instruction that discharges it.'
      ],
      setup: [['seekTo', 0]],
      exercise: 'In the **Circuit** pane, click the first `cx q[0],q[1]` statement -- or step the animation until the strip says it is being realised -- so that the current instruction is the one that discharges that CNOT.',
      check: ['frameIs', 'realises:1'],
      hints: [
        'The Circuit pane is the tab after Program. Its statements are clickable.',
        'The CNOT is the second statement (after h q[0]); a compiled CNOT is several pulses in one instruction: R, MS, R, R.',
        'The Program pane marks the same instruction; the status strip names the statement it realises.'
      ],
      nudges: {
        frameIs: ['You are on instruction {have}. Click the cx statement in the Circuit pane.', 'The statement you want is the first cx; the certificate says which instruction realises it.'],
        'default': ['Not there yet.']
      },
      praise: 'Instruction {frame} is the CNOT: the certificate says so, and the page checked that certificate against the instruction stamps before it drew anything. Every hand-written CNOT in Part B was this, with the compiler doing the journey.',
      solution: { steps: [['seekTo', 'realises:1']] } },

    // =============================================================== D2
    { id: 'D2', part: 'D', title: 'The same circuit on nine machines',
      story: 'One circuit, compiled for every shipped architecture, replayed under the same model. The numbers are below. The question is why.',
      teaches: ['m:cost', 'm:runtime', 'm:junctiontransits', 'el:loop'],
      table: 'micro',
      text: [
        'These are measured, not estimated: the compiler\'s output for `micro.qasm` on each shipped device, replayed by the verifier when this page was built, with cooling inserted. Cost is the model\'s charge for transport; steps are machine cycles; runtime is wall-clock; DACs are the device\'s control channels.',
        'Two families stand out. A ring pays for a **rotation** every time it moves anything -- all 144 ions turn together -- so its cost is high and its programme long. A grid pays for **junction crossings** and the cooling they force -- so its runtime is long even when its cost is small. A chain pays for almost nothing on a four-qubit circuit -- and could not hold a bigger one.'
      ],
      setup: [['newCanvas', { name: 'scratch' }]],
      stages: [
        { exercise: 'Before you look too hard at the table: which is cheaper in **cost** for this circuit, `ring144_24v` or `grid9x9`?',
          check: ['measuredLess', 'micro', 'total_cost', 'grid9x9', 'ring144_24v'] },
        { exercise: 'And which is **faster** -- the smaller runtime -- `grid9x9` or `ladder_2x72`?',
          check: ['measuredLess', 'micro', 'runtime_us', 'grid9x9', 'ladder_2x72'] },
        { exercise: 'Why does the ring cost so much more than the grid on this tiny circuit?',
          check: ['answer', 'rotation'] }
      ],
      choices: [
        { id: 'grid9x9', text: 'grid9x9' },
        { id: 'ring144_24v', text: 'ring144_24v' },
        { id: 'ladder_2x72', text: 'ladder_2x72' },
        { id: 'rotation', text: 'A rotation moves every ion on the loop, and the model charges for all of them each time the loop turns.' },
        { id: 'junctions', text: 'The ring has more junctions than the grid.' },
        { id: 'bigger', text: 'The ring is a bigger chip, so everything on it costs more.' }
      ],
      hints: [
        'Read the cost column: the ring is the largest number in it.',
        'Runtime is the fourth column. The ladder has fewer junctions in the way than the grid.',
        'Part B5: a rotation is charged for every ion it moves. The ring has 144 of them.'
      ],
      nudges: {
        measuredLess: ['The table says otherwise: {va} against {vb}.', 'Look at the column the question names, not the one next to it.'],
        answer: ['Think about B5: what does one rotation cost on a full loop?'],
        'default': ['Not there yet.']
      },
      praise: 'The table agrees with you twice, and so does the reason. An architecture decision is a measured number and the rule that explains it -- never a slogan about rings or grids.',
      solution: { stages: [[['setAnswer', 'grid9x9']], [['setAnswer', 'ladder_2x72']], [['setAnswer', 'rotation']]] } },

    // =============================================================== D3
    { id: 'D3', part: 'D', title: 'Broadcast versus direct at scale',
      story: 'The same chip, wired two ways. One needs five thousand DACs. The other needs forty-four.',
      teaches: ['m:DACs', 'm:electrodes', 'el:control', 'rule:R4d'],
      table: 'planes',
      text: [
        '`grid9x9` and `deck_unit_cell` are the same geometry: 144 traps, 77 junctions, the same electrodes. One is wired **direct** -- every electrode its own DAC -- and the other **broadcast**, the deck\'s unit-cell wiring where whole classes of electrodes share a waveform. The table is the hardware report of every shipped device; the DAC column is the whole argument for broadcasting.',
        'The price of broadcasting is what Part R\'s R5 showed: one waveform per channel per step, so some programmes need more steps. Look back at the D2 table for the two devices and see whether this one did.'
      ],
      setup: [['newCanvas', { name: 'scratch' }]],
      stages: [
        { exercise: 'What did broadcasting cost `micro.qasm` on the deck, compared with the direct-wired grid? Read both tables.',
          check: ['all', ['measuredSame', 'micro', 'total_steps', 'grid9x9', 'deck_unit_cell'], ['measuredSame', 'micro', 'runtime_us', 'grid9x9', 'deck_unit_cell'], ['answer', 'nothing']] },
        { exercise: 'Now put the broadcast one on the canvas: from **Start**, open the `deck_unit_cell` device (its template is on this page). The DACs chip in the head must read the deck\'s number, under the budget of 128 -- with all 144 traps.',
          check: ['all', ['dacsAtMost', 128], ['sites', 144]] }
      ],
      choices: [
        { id: 'slower', text: 'It ran slower: more steps and a longer runtime.' },
        { id: 'nothing', text: 'Nothing, on this programme: the same steps and the same runtime. The penalty only appears when two things on one channel want to happen at once.' },
        { id: 'hotter', text: 'The ions ran hotter.' }
      ],
      hints: [
        'Same steps, same runtime in the D2 table: broadcasting was free here. The DAC column is where they differ.',
        'Start (top of the elements rail) lists the shipped devices as templates; deck_unit_cell is one of them.',
        'The grid reads 5760 DACs, over its budget of 128; the deck reads 44.'
      ],
      nudges: {
        measuredSame: ['The measured tables disagree with this lesson: {have}. Trust the table.'],
        answer: ['Compare the two rows in the D2 table first: steps and runtime.', 'The difference between the two devices is in the DAC column, not the runtime column.'],
        dacsAtMost: ['The head reads {have} DACs. That is the direct-wired one, or a blank canvas.'],
        sites: ['{have} sites: the deck has 144 traps. Open the deck_unit_cell template, not a generator card.'],
        'default': ['Not there yet.']
      },
      praise: '{dacs} DACs for 144 traps and 77 junctions. That is the number that makes a large trapped-ion machine buildable, and R4d is the rule you pay for it with.',
      solution: { stages: [[['setAnswer', 'nothing']], [['newFromTemplate', 'deck_unit_cell', { name: 'deck_unit_cell' }], ['fit']]] } },

    // =============================================================== D4
    { id: 'D4', part: 'D', title: 'Running the compiler yourself',
      story: 'Everything on this page came from a command line. Here are the commands.',
      teaches: ['tab:W', 'tab:R'],
      text: [
        'Build a device in this page and press **Export** to get `<name>.arch.json` and, with a programme, `<name>.tsir.json`. Then, in a terminal: `python -m qccd open <name>.studio.json` runs the full verifier over what you built and prints the five verdicts this page cannot compute. `python -m qccd studio --seed <name>.arch.json --program rotate -o x.html` opens a shipped device on a built-in programme. `python -m qccd studio --tsir <compiled>.tsir.json --qasm <circuit>.qasm -o x.html` opens a compiled programme with its circuit beside it -- the page D1 uses. `python -m qccd tutorial -o tutorial` builds this course with its companion page.',
        'The compiler takes a QASM file and an architecture and writes the `.tsir.json` and the `.qcert.json` certificate that the Circuit pane checks. The matrix of shipped results in D2 was built with it.'
      ],
      setup: [['newCanvas', { name: 'scratch' }]],
      exercise: 'Last question. Which command opens a compiled programme with its circuit beside it?',
      check: ['answer', 'studio'],
      choices: [
        { id: 'open', text: 'python -m qccd open <file>.studio.json' },
        { id: 'studio', text: 'python -m qccd studio --tsir <x>.tsir.json --qasm <x>.qasm -o x.html' },
        { id: 'compile', text: 'python -m qccd compile <x>.qasm' }
      ],
      hints: [
        '`open` is the return leg: it verifies what the browser built.',
        'The Circuit pane needs the QASM and the certificate; only one command takes a --qasm.',
        'It is the second one.'
      ],
      nudges: {
        answer: ['Read the two commands that mention a page again: which one names a circuit?'],
        'default': ['Not there yet.']
      },
      praise: 'That is the whole loop: design here, verify in Python, compile a circuit, read the result back on a page like this one. You started with one picture of a chip; you can now build, program, break, repair and judge one.',
      solution: { stages: [[['setAnswer', 'studio']]] } }
  ]
};

if (typeof globalThis !== 'undefined' && globalThis.EDITOR && globalThis.EDITOR.lessonsReady) {
  globalThis.EDITOR.lessonsReady(QCCD_TUTORIAL);
}
