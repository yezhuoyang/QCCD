// qccd/viz/js/tutorial.js -- THE COURSE, AS DATA.
//
// Every lesson is a record: what it teaches, the handful of editor verbs that build its
// device, the exercise, the check that decides it, the nudges a failure gets, the praise a
// pass gets, and the solution.  Its **Hint** button is a walk-through (the page dims and the
// next control is ringed): a lesson whose answer is a gesture writes it as `guide`, and the
// rest are derived from the solution.  Nothing here runs the machine: the engine in
// editor.js (`lessonLoad`, `lessonCheck`, ...) drives the same verbs a user's pointer
// drives, and a check is a small expression over what the page already computed --
// frames, verdicts, prices.  `tests/test_tutorial.py` loads every lesson, proves the check
// FAILS on the starting state, applies the solution, proves it passes, and runs the result
// through `python -m qccd open`; `tests/guide.mjs` follows every guide with real input.
//
// Story, text, exercises and choices carry three marks the renderer understands: **bold**,
// `code`, and [[term|hint-key]] for a term that hovers to the same card as the rest of the
// page (a single *star* is NOT a mark: it shows as a star).  Nudges, praise and milestones
// are shown as PLAIN text, so they carry no marks; facts are {name} slots filled from the
// check ({cost}, {steps}, {dacs}, {sites}, ...).  Write for a reader who is new to all of
// this: short sentences, one action each, every control named as it reads on screen.
//
// This file is inlined into the page after editor.js and registers itself at the end.

'use strict';

var QCCD_TUTORIAL = {
  parts: [
    { id: 'A', title: 'What you are looking at',
      milestone: 'Part A done: you can read the map, build on it, and price a move. Part B puts ions on it and moves them.' },
    { id: 'B', title: 'Programming by hand',
      milestone: 'Part B done: you have written a CNOT, a Bell pair and a docked rotation by hand. Most people who use these machines never do that. Part R breaks every rule on purpose, and you fix them.' },
    { id: 'R', title: 'The rules the machine must obey',
      milestone: 'Part R done: you have broken and fixed every rule this machine obeys, or read why it cannot be checked here. Part C builds circuits under all of them.' },
    { id: 'C', title: 'Circuits by hand',
      milestone: 'Part C done: a syndrome round and a plaquette, scheduled by hand under every rule, with the heat paid for. Part D lets the compiler do it and asks you to judge the machine.' },
    { id: 'D', title: 'Architecture decisions with the compiler',
      milestone: 'The course is done. You can read the map, build a device, program it by hand, break and fix every rule, schedule a circuit, and judge an architecture from measured numbers. The rest is practice, and the compiler.' }
  ],
  // The tutorial goes on past this page: Parts E onward are lessons whose code is Lean, checked
  // in the page, on the site's Learn page (their titles are book/book.json's; tests/test_book.py
  // keeps the two in step).  The Learn tab links to them; it never loads or scores them here.
  continues: {
    href: 'https://qccd.academy/learn/',
    parts: [
      { id: 'E', title: 'The machine as data' },
      { id: 'F', title: 'Certificates and the proved checker' },
      { id: 'G', title: 'Semantics: which gate the pulses make' },
      { id: 'H', title: 'Verify the website yourself' },
      { id: 'I', title: 'Build your own verified compiler' },
      { id: 'J', title: 'Gadgets and fault-tolerant architecture' }
    ]
  },
  lessons: [

    // =============================================================== A1
    { id: 'A1', part: 'A', title: 'The machine in one picture',
      story: 'A few micrometres above a chip, in a vacuum, a few ions are held in place by electric fields. This page is a map of that chip.',
      teaches: ['el:site', 'el:junction', 'el:segment', 'region:stage'],
      text: [
        'A [[trapping site|el:site]] is a rounded bar. Ions sit in it. The small rings inside are the places for ions: one ring, one ion.',
        'A [[rail|el:segment]] is a pink line. Ions are pushed along it.',
        'A [[junction|el:junction]] is a square where rails meet. Ions pass through it, but never stop in it.',
        'On the left are the parts you can add. In the middle is the **canvas**: the chip. On the right is this panel. Hover over anything to see what it is. **Explain** (in the top bar) labels each area. **?** (under the canvas) opens the guide.'
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
        { exercise: 'Click the **junction**: the square where three rails meet.',
          check: ['selected', { kind: 'junction' }],
          guide: [['click', 'J0', ['selected', { kind: 'junction' }], 'Click the junction: the square where the rails meet.'], ['check']] },
        { exercise: 'Now click the site at the end of the **spur**: the short rail that hangs below the junction.',
          check: ['selected', { id: 'S3' }],
          guide: [['click', 'S3', ['selected', { id: 'S3' }], 'Click the site below the junction.'], ['check']] }
      ],
      nudges: {
        selected: ['Not that one. The junction is the square; the spur site is the bar below it.', 'Click right on the part. The pointer turns into a hand over it.', 'Press Hint: it shows you where to click.'],
        'default': ['Nothing is selected yet. Click a part on the canvas.']
      },
      praise: 'That is the whole picture: {sites} sites, {junctions} junction, {segments} rails. The rest of this page is about moving ions between them.',
      solution: { stages: [[['setSelection', [{ kind: 'junction', id: 'J0' }]]],
                           [['setSelection', [{ kind: 'site', id: 'S3' }]]]] } },

    // =============================================================== A2
    { id: 'A2', part: 'A', title: 'Zones: what may happen where',
      story: 'Not every site can do everything. Some can hold a gate, some can measure, some only take in new ions.',
      teaches: ['el:zone_type', 'zone:new'],
      text: [
        'A [[zone type|el:zone_type]] is a label on a site. It says how many ions fit, and whether a gate, a measurement or cooling may happen there.',
        'This device has three: `trap` (gate, measure, cool; 2 ions), `data` (stores ions, can cool; 2 ions) and `load` (where new ions arrive; 8 ions). Their chips are under **Zone type** on the left. Each chip shows the name and the size, for example `load · 8`.',
        '**Pick a zone:** press a chip, then click the canvas. The new site gets that zone.',
        '**A zone is shared:** change its capacity once, and every site with that zone changes.'
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
        { exercise: 'Press the **load** chip under **Zone type**. Then click an empty spot on the canvas. A new `load` site appears there.',
          check: ['all', ['sites', 5], ['anyNodeZone', 'load']],
          guide: [['site', 3, 1, { zone: 'load', chip: true }], ['check']] },
        { exercise: 'Now change the `load` zone. Open the **Device** tab and press **Source**: the device as text. No line sets the `load` capacity yet, so type a new last line: `m.set_zone("load", capacity=4)`. The chip now reads `load · 4`, and your new site gets shorter. Go back to **Learn** and press **Check**.',
          check: ['zoneCapacity', 'load', 4],
          guide: [['tab', 'A'], ['view', 'src'], ['line', '#eSrc', 'm.set_zone("load", capacity=4)', 'Type this as a new last line.'],
                  ['goal', ['zoneCapacity', 'load', 4]], ['check']] }
      ],
      nudges: {
        sites: ['No new site yet. Press the load chip, then click an empty spot on the canvas.', 'The click must land on empty canvas, away from the other sites.'],
        anyNodeZone: ['A site was placed, but not in the load zone. Press the load chip first, then place another site.'],
        zoneCapacity: ['The load zone still holds {have}. Open Device, press Source, and type the line m.set_zone("load", capacity=4) at the end.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'A load site that now holds {cap}. Capacity is the first rule of the machine, R1, and you just set it for a whole zone at once.',
      solution: { stages: [[['addNodeAt', 3, 1, { id: 'S4', kind: 'site', zone: 'load' }]],
                           [['emit', { method: 'set_zone', args: ['load'], kwargs: { capacity: 4 } }]]] } },

    // =============================================================== A3
    { id: 'A3', part: 'A', title: 'Building by hand',
      story: 'Every chip starts empty. You will build a square loop by hand.',
      teaches: ['el:site', 'el:segment', 'el:loop', 'snap', 'undo', 'fit'],
      text: [
        '**Place a site:** press **Trapping site** on the left, then click the canvas. Turn on **Snap** (under the canvas) and it lands on the grid.',
        '**Lay a rail:** hold **Shift** and drag from one site onto another.',
        '**Make a loop:** a [[loop|el:loop]] is a ring of sites the machine can turn all at once. Hold **Shift** and drag a box around the sites to select them, then **right-click** one of them and choose **Close loop**.',
        '**Undo** takes back any step. On a real chip the corners of a square are often [[junctions|el:junction]]; a square built that way is a loop too. Press **Hint** to be shown each step.'
      ],
      setup: [['newCanvas', { name: 'square' }], ['fit']],
      exercise: 'Build a square: four sites, four rails joining them in a ring, then close it into a loop.',
      check: ['all', ['sites', 4], ['loops', 1, true]],
      guide: [['fresh', [[0, 0], [1, 0], [1, 1], [0, 1]]], ['snap'],
              ['site', 0, 0], ['site', 1, 0], ['site', 1, 1], ['site', 0, 1],
              ['rail', [0, 0], [1, 0]], ['rail', [1, 0], [1, 1]], ['rail', [1, 1], [0, 1]], ['rail', [0, 1], [0, 0]],
              ['box', [[0, 0], [1, 0], [1, 1], [0, 1]]],
              ['menu', [0, 0], 'close-loop', ['loops', 1, true], 'Press Close loop.'], ['check']],
      nudges: {
        sites: ['{have} of 4 sites so far. Keep placing.', 'You have {have} sites; the square needs exactly four.'],
        loops: ['Four sites. Now join them in a ring, select all four, and choose Close loop.', 'If Close loop does not work, a rail is missing: each corner needs a rail to both of its neighbours.'],
        'default': ['Not a square yet. Press Hint: it shows you the next step.']
      },
      praise: 'A four-site loop. The machine can now move every ion on it one site with a single instruction: the cheapest move it has.',
      challenge: { label: 'sharp', exercise: 'Do it in nine gestures: four sites, four rails, one **Close loop**.',
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
      story: 'Moving an ion is not free. Each push along a rail warms the ion a little, and a warm ion makes a worse gate. A faster push warms it more.',
      teaches: ['m:cost', 'm:runtime', 'm:peak n̄', 'el:primitives', 'el:curve_point', 'tab:R'],
      text: [
        'The top bar shows the price of the programme on the canvas. [[Cost|m:cost]] is what the model charges for the moves. [[Runtime|m:runtime]] is how long the programme takes.',
        '[[Report|tab:R]] also shows the [[peak n̄|m:peak n̄]], written **peak n-bar**: the heat of the hottest ion, in quanta.',
        'Times and heats come from the [[primitives|el:primitives]]. Each kind of move has a curve: a short table of measured rows. Each row is one way to do the move: its **duration** in microseconds (us) and the **heat** it adds in quanta.',
        'The model always uses the fastest row in its table (`qccdsim_jones`). A fast move heats more than a slow one: 0.2 quanta in 2 us is 100 quanta per millisecond.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['testDrive'], ['fit']],
      exercise: 'Press **Append a row** in the tools bar, then **Curve point**. A form opens on the left. Pick the `shuttle_segment` curve. At the bottom, type duration **2** (us) and heat added **0.2** (quanta), then press **add**. **RUNTIME** in the top bar falls, and in **Report** the **peak n-bar** rises.',
      check: ['all', ['runtimeBelow', 'setup'], ['peakAbove', 'setup']],
      guide: [['tool', 'row'], ['tile', 'curve_point', 'Press Curve point.'],
              ['pick', '#cfName', 'shuttle_segment', 'Pick the shuttle_segment curve.'],
              ['fill', '#cfNewUs', '2', 'Duration: 2 (microseconds).'], ['fill', '#cfNewQ', '0.2', 'Heat added: 0.2 (quanta).'],
              ['press', '#cfAdd', 'Press add.'], ['goal', ['all', ['runtimeBelow', 'setup'], ['peakAbove', 'setup']]], ['check']],
      nudges: {
        runtimeBelow: ['Runtime is still {have} us. Is the new row faster than the old ones? Check its duration.', 'The fastest row still seems to be an old one. Did add work? A short message at the bottom of the canvas says how many points the curve has.'],
        peakAbove: ['Runtime fell, but the heat did not rise. Give the new row more heat than the old ones, for example 0.2.'],
        'default': ['The price has not moved yet. Press Hint to be shown the next step.']
      },
      praise: 'Runtime fell from {us0} us to {us} us, and the hottest ion went from {peak0} to {peak} quanta. Faster is hotter: that trade-off is the whole game, and the curves decide it.',
      solution: { steps: [
        ['curveAddPoint', 'shuttle_segment', { us: 2.0, quanta: 0.2, table: 'qccdsim_jones', source: 'exercise A4', label: 'a faster shuttle' }]] } },

    // =============================================================== A5
    { id: 'A5', part: 'A', title: 'The control plane and broadcasting',
      story: 'A chip has thousands of electrodes, but not thousands of wires. How the electrodes are wired decides what the chip can do at once.',
      teaches: ['el:control', 'm:DACs', 'm:electrodes', 'm:switches'],
      text: [
        'Each [[electrode|m:electrodes]] along a rail needs a voltage. A [[DAC|m:DACs]] (digital-to-analog converter) makes that voltage. One DAC for every electrode does not scale to big chips.',
        'This device uses **broadcast** wiring: many electrodes share one **channel**, and one DAC drives the whole channel. So the device counts DACs by channel, not by electrode.',
        'The price: **every site on a shared channel must move the same way in the same step.** You will meet this again as rule R4d, in Part B, and in Part D.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['testDrive'], ['fit']],
      stages: [
        { exercise: 'Press **Machine settings** in the tools bar, then **Control plane**. A form opens on the left. Change `wiring.electrodes_per_trap` from 24 to 48. Press **apply** at the bottom of the form. Then open **Report**: **electrodes** goes up, and **DACs** stays the same.',
          check: ['all', ['electrodesAbove', 'setup'], ['dacsSame', 'setup']],
          guide: [['tool', 'block'], ['tile', 'control', 'Press Control plane.'],
                  ['fill', 'label:wiring.electrodes_per_trap', '48', 'Change 24 to 48.'], ['press', '#bfSave', 'Press apply.'],
                  ['goal', ['all', ['electrodesAbove', 'setup'], ['dacsSame', 'setup']]],
                  ['tab', 'R'], ['look', '#report', 'Report: the electrodes went up, the DACs stayed the same.'], ['check']] },
        { exercise: 'Why did the number of DACs not change?',
          check: ['answer', 'channels'] }
      ],
      choices: [
        { id: 'stale', text: 'The report has not updated yet.' },
        { id: 'channels', text: 'All electrodes on a channel share one signal, so DACs count channels, not electrodes.' },
        { id: 'junctions', text: 'DACs are only counted for junctions, and this ring has none.' }
      ],
      nudges: {
        electrodesAbove: ['Electrodes are still {have}. Change wiring.electrodes_per_trap in the Control plane form, then press apply.'],
        dacsSame: ['The DAC count changed to {have}. Change only electrodes_per_trap, nothing else, and press apply again.'],
        answer: ['Look at the numbers again: electrodes went up, DACs did not. Which answer explains that?', 'The report is not stale: it updated the moment you pressed apply. Think about what a channel is.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Electrodes rose to {electrodes}, and DACs stayed at {dacs}. That is broadcasting: one DAC drives a channel, and a channel drives every electrode on it. That is why every site on it must move the same way.',
      solution: { stages: [[['emit', { method: 'set_wiring', args: [], kwargs: { electrodes_per_trap: 48 } }]],
                           [['setAnswer', 'channels']]] } },

    // =============================================================== A6
    { id: 'A6', part: 'A', title: 'The panels',
      story: 'You have a device and a programme. Now read them.',
      teaches: ['tab:P', 'tab:A', 'tab:M', 'tab:W', 'tab:R', 'follow'],
      text: [
        'The tabs at the top right open the panels. **Learn** is this course.',
        '[[Program|tab:P]]: the hardware programme, one instruction per row. Click a row and the animation jumps there.',
        '[[Device|tab:A]]: the device as code. Press **Source** to see it as text you can edit.',
        '[[Machine|tab:M]]: the rules and the hardware.',
        '[[Report|tab:R]]: the totals and the rules. Each rule is a badge: green passed, red failed, amber only partly checked, grey (dashed) not checked on this page.',
        '[[Write|tab:W]]: where you write programmes, from Part B on.',
        'A rotation row reads like `L0 × +1  1 hop`: the loop turns one site. Every ion on the loop pays for every hop, so the row with the most hops costs the most.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['testDrive'], ['fit'], ['seekTo', 0]],
      exercise: 'Open **Program**. Find the most expensive instruction: the rotation with the most hops. Click its row, and the animation jumps there.',
      check: ['frameIs', 'maxcost'],
      nudges: {
        frameIs: ['You are on instruction {have}. The most expensive one is another row.', 'Compare the hops: one rotation turns the loop further than the others.'],
        'default': ['Click a row in the Program panel.']
      },
      praise: 'Instruction {frame} costs {cost}: a rotation moves every ion at once. Part B is about writing your own programmes.',
      solution: { steps: [['seekTo', 'maxcost']] } },

    // =============================================================== B1
    { id: 'B1', part: 'B', title: 'The programme language',
      story: 'A device does nothing until you tell it what to do. Here is how.',
      teaches: ['p:init', 'p:shuttle', 'evaluate', 'tab:W', 'tab:P'],
      text: [
        'A programme is a list of `p.` lines in the [[Write|tab:W]] tab. The lines run in order, one after another.',
        '[[p.init|p:init]] puts named ions on named sites: `p.init({"d0": "S0"})` puts ion `d0` on site `S0`.',
        '[[p.shuttle|p:shuttle]] moves one ion along a path of sites, one rail per step: `p.shuttle("d0", ["S0", "S1", "S2"])`.',
        'Press **Evaluate**. The page runs the lines, prices them, checks the rules, and plays the result on the canvas. The [[Program|tab:P]] tab lists the instructions it made.',
        'Nothing is taken on trust. If a line cannot run, for example a path along a rail that does not exist, the **Write** tab says which line and why.'
      ],
      setup: [
        ['newCanvas', { name: 'line3' }],
        ['addNodeAt', 0, 0, { id: 'S0', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 1, 0, { id: 'S1', kind: 'site', zone: 'trap' }],
        ['addNodeAt', 2, 0, { id: 'S2', kind: 'site', zone: 'trap' }],
        ['joinNodes', 'S0', 'S1', { id: 'E0' }],
        ['joinNodes', 'S1', 'S2', { id: 'E1' }],
        ['fit']],
      exercise: 'Open **Write**. Put ion `d0` on `S0` and walk it to `S2`. Press **Evaluate**, then **Check**.',
      check: ['all', ['lowered'], ['ionAt', 'd0', 'S2'], ['rulesPass']],
      nudges: {
        lowered: ['No programme yet. The Write tab is waiting for two lines.', 'The text did not run: {have}', 'Check the quotes and brackets. The Write tab names the line that failed.'],
        ionAt: ['d0 ended at {have}, not S2. The path is a list of sites, each joined to the next by a rail.', 'Nearly: d0 is at {have}. Add one more site to the path.'],
        rulesPass: ['The ion got there, but a rule failed: {have}. Open Report to read it.'],
        'default': ['Not there yet. Press Evaluate, then Check.']
      },
      praise: 'd0 walked two rails to {at} in {steps} steps at cost {cost}. Press Play under the canvas to watch it: your first hardware programme.',
      boundary: { exercise: 'Now walk it back: a round trip that visits `S2` and ends on `S0`.',
                  check: ['all', ['visited', 'd0', 'S2'], ['ionAt', 'd0', 'S0'], ['rulesPass']],
                  praise: 'There and back in {steps} steps. Every hop is a row in the Program tab: click one and the animation jumps there.' },
      solution: { program: 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\n' } },

    // =============================================================== B2
    { id: 'B2', part: 'B', title: 'One CNOT',
      story: 'Two ions, one gate. On paper it is one line of a circuit. On the chip it is a trip, a pause, then the gate.',
      teaches: ['p:gate', 'p:cool', 'rule:R6b', 'rule:R7c', 'rule:R22'],
      text: [
        'A two-qubit gate happens **in one site**. Both ions must be there ([[R6b|rule:R6b]]), and the zone of that site must allow gates ([[R6|rule:R6]]).',
        '[[p.gate|p:gate]] names the gate and the pair of ions, control first: `p.gate("CX", [["d0", "d1"]])`.',
        'So a CNOT between ions in different sites takes two things: move one ion to the other, then fire the gate. The loaded programme puts `d0` on `S0` and `d1` on `S2`.'
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
        { exercise: 'Open **Write**. Under the `p.init` line, add a shuttle that walks `d1` from `S2` to `S0`. Then add a `CX` on `d0`, `d1`. Press **Evaluate**, then **Check**.',
          check: ['all', ['lowered'], ['gateBetween', 'd0', 'd1', 'CX']] },
        { exercise: 'The gate fired, but a rule failed. Open **Report**: the badge **R7c** is red. The trip warmed `d1`, and the machine refuses gates when no ion is ever cooled. Add a [[p.cool()|p:cool]] line between the shuttle and the gate. Press **Evaluate** again, then **Check**.',
          check: ['all', ['gateBetween', 'd0', 'd1', 'CX'], ['rulesPass']] }
      ],
      nudges: {
        lowered: ['The text did not run: {have}', 'The Write tab names the line that failed.'],
        gateBetween: ['No CX between d0 and d1 yet. p.gate takes the gate name, then a list of pairs.', 'The gate must name both ions, and they must be in the same site when it fires.'],
        rulesPass: ['A rule still fails: {have}', 'Open Report and read the red badge. The cooling goes before the gate.'],
        stepsAtMost: ['{have} steps; five is the goal. One p.simd would be four, but R22 refuses it: one signal moves every ion the same way, and these two move towards each other.', 'Use p.shuttle("d0", ["S0", "S1"]) and p.shuttle("d1", ["S2", "S1"]), then p.cool(), then the gate.'],
        ionAt: ['That ion ends at {have}. The gate fires at S1, so each ion walks one rail. Neither walks the whole way.', 'd0 goes from S0 to S1, d1 goes from S2 to S1, and the CX fires where they meet.'],
        'default': ['Not there yet. Press Evaluate, then Check.']
      },
      praise: 'That is a CNOT on real hardware: d1 walked to d0, was cooled, and CX fired at instruction {gateFrame}. Cost {cost}, {steps} steps, and all {rulesChecked} rules the page checks passed.',
      challenge: { exercise: 'Meet in the middle: fire the `CX` at `S1`, with `d0` and `d1` each walking one rail. You may want both moves in one `p.simd`. Try it, and see which rule fails: one set of electrodes pushes every ion on a rail the same way, so one instruction cannot move `d0` right and `d1` left ([[R22|rule:R22]]). Use two shuttles, then cool and fire.',
                   check: ['all', ['gateBetween', 'd0', 'd1', 'CX'], ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S1'], ['stepsAtMost', 5]],
                   praise: 'The CX fired at S1 in {steps} steps, with both ions moving. One p.simd would have been four steps, but R22 refuses it: one signal moves every ion the same way, and these two move towards each other. That is why Part C turns a whole ring at once: moving every ion the same way is exactly what the electrodes can do.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.gate("CX", [["d0", "d1"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']]],
        challenge: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d0", ["S0", "S1"])\np.shuttle("d1", ["S2", "S1"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']] } },

    // =============================================================== B3
    { id: 'B3', part: 'B', title: 'Single-qubit gates and measurement',
      story: 'A Bell pair is the simplest pair of entangled qubits: an H, a CX, then read both out.',
      teaches: ['p:gate', 'p:measure', 'p:reset', 'rule:R6'],
      text: [
        'A single-qubit gate names the **site**, not the ion: `p.gate("H", [], ["S0"])` is an H on the ion in `S0`. The `[]` is an empty list of pairs.',
        '[[p.measure|p:measure]] names ions: `p.measure(["d0", "d1"])`. The site must be able to measure: `trap` can, `data` cannot. [[p.reset|p:reset]] puts an ion back to |0>.',
        'Each line is one thing the machine does, in the order you write them.'
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
      exercise: 'Write the Bell pair: `H` on `S0`, bring `d1` to `S0`, cool, `CX` on `d0`, `d1`, then measure both ions. Press **Evaluate**, then **Check**.',
      check: ['all', ['lowered'], ['gateAt', 'H', 'S0'], ['gateBetween', 'd0', 'd1', 'CX'], ['measured', ['d0', 'd1']], ['rulesPass']],
      nudges: {
        lowered: ['The text did not run: {have}'],
        gateAt: ['No H on S0 yet. A single-qubit gate takes an empty list [] and then the site: p.gate("H", [], ["S0"]).'],
        gateBetween: ['The CX is missing, or names the wrong ions.'],
        measured: ['Not measured yet: {have}. p.measure takes a list of ions.'],
        rulesPass: ['A rule fails: {have}', 'Put p.cool() between the shuttle and the CX.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'H, CX, measure: a Bell pair in {frames} instructions and {steps} steps. Its two readouts always agree, but this page cannot show that: it schedules gates, it does not simulate them.',
      boundary: { exercise: 'Run it twice: after the measurement, reset both ions, then do a second H, CX and measurement. No rule may fail.',
                  check: ['all', ['hasFrame', 'reset'], ['gateCount', 'CX', 2], ['rulesPass']],
                  praise: 'Two rounds, {gates} CX gates, and a reset between them. The ions did not move between the rounds, so they barely warmed. A syndrome round in Part C has exactly this shape.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S2"})\np.gate("H", [], ["S0"])\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\np.measure(["d0", "d1"])\n' } },

    // =============================================================== B4
    { id: 'B4', part: 'B', title: 'Reading a failure',
      story: 'This programme is wrong. The page already knows why. The skill is to read what it says.',
      teaches: ['status', 'tab:R', 'tab:P', 'undo'],
      text: [
        '**Where a failure shows:** open [[Report|tab:R]]. Each rule is a badge. A failed rule is red, with a count. A line under the badges says what broke and where.',
        '**What a rule says:** hover a badge to read the rule.',
        '**Where it happens:** the [[Program|tab:P]] tab lists every instruction. Click one, and the [[line under the canvas|status]] names the instruction the animation is on.',
        'To fix a failure you change one of two things: the programme or the device. Here the device has a `data` site in the middle, `S1`, and the loaded programme makes the ions meet there.'
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
        { exercise: 'The programme is loaded and has already run. Open **Report** and find the red badge. Then go back to **Learn**, choose that rule below, and press **Check**.',
          check: ['answer', 'R6'] },
        { exercise: 'Now fix it without changing the device. `S1` is a `data` site and cannot hold a gate. In **Write**, make the ions meet in a site that can. Keep the CX. No rule may fail.',
          check: ['all', ['rulesPass'], ['gateBetween', 'd0', 'd1', 'CX']] }
      ],
      choices: [
        { id: 'R7c', text: 'R7c: there is no cooling before the gate.' },
        { id: 'R6', text: 'R6: the gate is in a site whose zone does not allow gates.' },
        { id: 'R6b', text: 'R6b: the two ions are not in the same site.' },
        { id: 'R1', text: 'R1: too many ions in one site.' }
      ],
      nudges: {
        answer: ['Open Report: one badge is red. Which rule is it?', 'The line under the badges in Report says gate=false. Which rule is about where a gate may happen?'],
        rulesPass: ['A rule still fails: {have}', 'Which sites allow a gate? Click a site: the Selection box on the left shows its zone. trap allows gates, data does not.'],
        gateBetween: ['No rule fails, but the CX is gone. Keep the gate; change where it happens.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'R6 found and fixed: the CX now fires in a trap site, and no rule fails. You debugged a hardware programme the real way, from the message to the fix. Cost {cost}.',
      solution: { stages: [
        [['setAnswer', 'R6']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']]] } },

    // =============================================================== B5
    { id: 'B5', part: 'B', title: 'Loops and rotation',
      story: 'Six ions, one instruction. Loops are why these machines can grow big.',
      teaches: ['p:fill', 'p:rotate', 'el:loop', 'm:steps'],
      text: [
        'A closed [[loop|el:loop]] moves as one unit.',
        '[[p.fill|p:fill]] puts one ion on every site of the loop: `d0` on `S0`, `d1` on `S1`, and so on.',
        '[[p.rotate|p:rotate]] turns the loop: `p.rotate(2)` moves every ion two sites forward, and `p.rotate(-1)` moves every ion one site back.',
        'A rotation is charged for every ion it moves, so its **cost** is no lower than six shuttles. It wins on [[steps|m:steps]]: one step per site turned, and the ions never block each other.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['fit']],
      exercise: 'Fill the loop and turn it so that `d0` ends on `S2`. Use two lines.',
      check: ['all', ['lowered'], ['rotated', 'L0', 2], ['instructionsAtMost', 2], ['rulesPass']],
      nudges: {
        lowered: ['No programme yet: fill, then rotate.', 'The text did not run: {have}'],
        rotated: ['The loop has not turned by two: {have}.', 'Forward is S0 to S1 to S2. Which sign does that need?'],
        instructionsAtMost: ['{have} instructions. A fill and one rotate are all it takes.'],
        stepsAtMost: ['{have} steps: four forward takes four steps. Is there a shorter way round?', 'On a loop of six, four forward lands on the same site as two back.'],
        rulesPass: ['A rule fails: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Six ions moved two sites in {steps} steps at cost {cost}. They all moved the same way in the same step. Remember that for B7.',
      challenge: { exercise: 'Put `d0` on `S4` in at most 2 steps. Four forward is two back.',
                   check: ['all', ['rotated', 'L0', 4], ['stepsAtMost', 2], ['rulesPass']],
                   praise: 'Two back is four forward: {steps} steps, cost {cost}. You choose the direction, and the shorter way round is the cheaper one.' },
      solution: { program: 'p.fill()\np.rotate(2)\n' } },

    // =============================================================== B6
    { id: 'B6', part: 'B', title: 'Moving many ions at once',
      story: 'A rotation moves every ion. Sometimes you want to move three, not six.',
      teaches: ['p:simd', 'el:control', 'rule:R3', 'rule:R5'],
      text: [
        '[[p.simd|p:simd]] moves several ions in one instruction: `p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S2", "S3"]])`.',
        'The first argument is the [[movement class|el:control]]: the kind of motion. It must be one the device declares. This ring declares `shuttle`, `rotate_cw`, `rotate_ccw`, `dock`, `undock`, `sort_merge` and `sort_split`.',
        'The second argument is a list of moves. Each move is `[ion, from, to]`, from a site to the next one.',
        'One instruction is one **step**: all its moves happen at once. The **Program** tab shows the class in its own column. The rules judge the step as a whole: no two ions on one rail ([[R3|rule:R3]]), no two ions swapping places ([[R5|rule:R5]]), one direction per loop ([[R11|rule:R11]]).'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2", "d2": "S4"})\n']],
      exercise: 'Move all three ions one site forward (`S0` to `S1`, `S2` to `S3`, `S4` to `S5`) in **one** instruction.',
      check: ['all', ['lowered'], ['frames', 2, 2], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S3'], ['ionAt', 'd2', 'S5'], ['rulesPass']],
      nudges: {
        lowered: ['The text did not run: {have}'],
        frames: ['{have} instructions. The exercise wants exactly two: the init and one simd.', 'Three separate shuttles are three steps. Put the three moves in one list.'],
        ionAt: ['{have} is not where the exercise asks. Check the from and to of each move.'],
        rulesPass: ['A rule fails: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Three ions, one instruction, {steps} step, cost {cost}. A rotation is this with every site filled. Now you can also write the moves a rotation cannot make.',
      boundary: { exercise: 'Now backward: all three one site back, still in one instruction, with no rule failing.',
                  check: ['all', ['frames', 2, 2], ['ionAt', 'd0', 'S5'], ['ionAt', 'd1', 'S1'], ['ionAt', 'd2', 'S3'], ['rulesPass']],
                  praise: 'Same step, other direction. The loop does not mind which way, as long as every ion goes the same way.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S2", "d2": "S4"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S2", "S3"], ["d2", "S4", "S5"]])\n' } },

    // =============================================================== B7
    { id: 'B7', part: 'B', title: 'Broadcasting in a programme',
      story: 'Two ions on one loop want to go opposite ways. The chip can give only one answer to both.',
      teaches: ['el:control', 'rule:R11', 'rule:R4d', 'p:simd'],
      text: [
        'This is the A5 rule as a programme. This ring uses the `wise` wiring scheme: one channel drives every rail of the loop. So in one step the whole loop gets one signal, and every ion on it moves the same way.',
        'The page checks this as [[R11|rule:R11]]: in one step, all ions on a loop move by the same amount. The checker checks it again when the page is built, channel by channel, as [[R4d|rule:R4d]]. Part R shows that verdict.',
        'So two ions can never pass each other on a broadcast loop, and one move forward plus one move back take two steps.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 0 }, { name: 'ring6' }], ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\n']],
      stages: [
        { exercise: 'Try it: in one `p.simd`, move `d0` forward (`S0` to `S1`) and `d1` back (`S3` to `S2`). Press **Evaluate**. Open **Report**: **R11** is red. Then press **Check**.',
          check: ['ruleFails', 'R11'] },
        { exercise: 'Now do it legally: `d0` on `S1` and `d1` on `S2`, with no rule failing.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S2']] }
      ],
      nudges: {
        ruleFails: ['R11 has not failed yet. Are both moves in ONE p.simd, in opposite directions?', 'Forward is S0 to S1; back is S3 to S2. One instruction, two moves.'],
        rulesPass: ['Still refused: {have}', 'One direction per step. Give each move its own instruction.'],
        ionAt: ['{have} is not where the exercise asks. Check the from and to of each move.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Two steps, one direction each, cost {cost}. That is broadcasting seen from the programme: a shared channel turns opposite moves into separate steps.',
      boundary: { exercise: 'Move both ions forward by one in one instruction. That is legal, because it is one direction. No rule may fail.',
                  check: ['all', ['frames', 2, 2], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S4'], ['rulesPass']],
                  praise: 'One step, one direction, two ions: the channel is happy. Every move on a broadcast machine has this shape.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S3", "S2"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d1", "S3", "S2"]])\np.simd("shuttle", [["d0", "S0", "S1"]])\n']]] } },

    // =============================================================== B8
    { id: 'B8', part: 'B', title: 'Docks and spurs',
      story: 'The loop turns as one. To keep one ion out of the turn, park it to the side.',
      teaches: ['el:spur', 'el:control', 'p:shuttle', 'p:rotate'],
      text: [
        'A [[spur|el:spur]] is a short rail from the loop to a side site. Here `A0` hangs off `S0`, and `A3` off `S3`. An ion parked there stays put while the loop turns.',
        'On this ring only the spur sites are `trap` sites, so gates can only happen there.',
        'The classes `dock` and `undock` name this move honestly: the ion splits off its chain and merges into the dock, and the price includes that. Give the class as the third argument: `p.shuttle("d0", ["S0", "A0"], "dock")`. Without it the move is a plain `shuttle`, which looks cheaper because it leaves the split out.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit'],
        ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\n']],
      exercise: 'Park `d0` in `A0`. Turn the loop so that `d1` ends on `S3`. Then bring `d0` back to `S0`.',
      check: ['all', ['lowered'], ['visited', 'd0', 'A0'], ['ionAt', 'd1', 'S3'], ['ionAt', 'd0', 'S0'], ['rulesPass']],
      nudges: {
        lowered: ['The text did not run: {have}'],
        visited: ['d0 never went into the dock. The spur site is A0, next to S0.'],
        ionAt: ['{have} is not where the exercise asks. Rotate by 2 for d1, and bring d0 back at the end.'],
        rulesPass: ['A rule fails: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'd0 waited in the dock while the loop turned, then came back: cost {cost}, {steps} steps. Park, turn, return: that is how an ion reaches a gate on these machines. Part C builds circuits from it.',
      boundary: { exercise: 'Now say what you did: use the `dock` and `undock` classes for the two spur moves. Watch **COST** and **STEPS** in the top bar go up.',
                  check: ['all', ['classUsed', 'dock'], ['classUsed', 'undock'], ['ionAt', 'd0', 'S0'], ['rulesPass']],
                  praise: 'Cost {cost}, with the split and the merge counted. That is what a dock really costs.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d0", ["S0", "A0"])\np.rotate(2)\np.shuttle("d0", ["A0", "S0"])\n' } },

    // =============================================================== R1
    { id: 'R1', part: 'R', title: 'A site holds what it holds',
      story: 'Three ions were sent into a site built for two. The chip did not stretch.',
      teaches: ['rule:R1', 'rule:R3', 'rule:R13', 'el:zone_type'],
      text: [
        '**How Part R works:** each step loads a programme that breaks a rule, and runs it. Open **Report** to see which rule failed: its badge is red. Fix the programme in **Write**, press **Evaluate**, then **Check**. The check wants no red badge, and the programme still doing its job.',
        '[[R1|rule:R1]]: a site holds no more ions than its [[zone|el:zone_type]] allows. [[R3|rule:R3]]: the same for a rail.',
        '[[R13|rule:R13]]: a two-qubit gate in a chain of more than about fifteen ions is too slow to be worth doing.'
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
          exercise: 'Three ions end up in `S0`, which holds two. **Report** shows **R1** in red. Fix it so that `S0` holds exactly two and no rule fails. `d2` can stop on `S1`.',
          check: ['all', ['rulesPass'], ['occupancy', 'S0', 2]] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1"})\np.simd("shuttle", [["d0", "S1", "S2"], ["d1", "S1", "S2"]])\n']],
          breaks: [['ruleFails', 'R3']],
          exercise: 'A new programme: two ions use one rail in the same step, and this rail carries one. **Report** shows **R3** in red. Send them one at a time. Both must still end on `S2`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S2'], ['ionAt', 'd1', 'S2']] },
        { setup: [['emit', { method: 'set_zone', args: ['trap'], kwargs: { capacity: 16 } }],
                  ['applyProgramSource', 'p.init({"q0": "S0", "q1": "S0", "q2": "S0", "q3": "S0", "q4": "S0", "q5": "S0", "q6": "S0", "q7": "S0", "q8": "S0", "q9": "S0", "q10": "S0", "q11": "S0", "q12": "S0", "q13": "S0", "q14": "S0", "q15": "S0"})\np.cool()\np.gate("CX", [["q0", "q1"]])\n']],
          breaks: [['ruleFails', 'R13']],
          exercise: 'Now the zone holds sixteen, and sixteen ions sit in `S0` when a CX fires: **R13** fails. Move one ion out to `S1` before the gate.',
          check: ['all', ['rulesPass'], ['gateBetween', 'q0', 'q1', 'CX']] }
      ],
      nudges: {
        rulesPass: ['A rule still fails: {have}', 'Open Report and read the line under the badges: it names the site and the number.'],
        occupancy: ['S0 holds {have}; the exercise wants exactly two there.'],
        ionAt: ['{have} is not where that ion should end. Both go to S2, one after the other.'],
        gateBetween: ['No rule fails, but the CX is gone. Keep the gate; move one ion out first.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Three rules, three fixes. R1, R3 and R13 are one idea: a site holds what it holds, a rail carries what it carries, and a chain that is too long makes slow gates. Cost {cost}, {steps} steps, no rule failed.',
      boundary: { exercise: 'The limit itself is allowed: put exactly two ions in `S0`, cool, and fire a CX between them. Being full is not a failure.',
                  check: ['all', ['occupancy', 'S0', 2], ['gateBetween', 'q0', 'q1', 'CX'], ['rulesPass']],
                  praise: 'Two ions in a site for sixteen, and no rule failed. A rule draws a line, and the line itself is on the allowed side.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1", "d2": "S2"})\np.shuttle("d1", ["S1", "S0"])\np.shuttle("d2", ["S2", "S1"])\n']],
        [['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1"})\np.simd("shuttle", [["d0", "S1", "S2"]])\np.simd("shuttle", [["d1", "S1", "S2"]])\n']],
        [['applyProgramSource', 'p.init({"q0": "S0", "q1": "S0", "q2": "S0", "q3": "S0", "q4": "S0", "q5": "S0", "q6": "S0", "q7": "S0", "q8": "S0", "q9": "S0", "q10": "S0", "q11": "S0", "q12": "S0", "q13": "S0", "q14": "S0", "q15": "S0"})\np.shuttle("q15", ["S0", "S1"])\np.cool()\np.gate("CX", [["q0", "q1"]])\n']]] } },

    // =============================================================== R2
    { id: 'R2', part: 'R', title: 'Junctions: one at a time',
      story: 'Two ions reach a crossing at the same moment. There is room for one.',
      teaches: ['rule:R2', 'rule:R18', 'rule:R11', 'el:junction'],
      text: [
        '[[R2|rule:R2]]: a [[junction|el:junction]] takes one ion at a time. Never two inside it, never two crossing it in one step.',
        '[[R18|rule:R18]]: a node is a junction when three or more rails meet there. Each crossing is priced by that number, so **Report** counts junction transits on their own line.',
        '[[R11|rule:R11]] has two parts. A loop turns one way per step (you met this in B7). And a site with three rails counts as a junction for R2: only one ion may rest there.'
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
          exercise: 'Two ions cross `J0` in one step, and **Report** shows **R2** in red. Send them one at a time, one crossing per instruction. `d0` must still end on `S1`, and `d1` on `S3`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S3'], ['transitsAtMost', 2]] },
        { exercise: 'R18: what makes a node a junction, and why does the Report care?',
          check: ['answer', 'deg3'] },
        { setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit'],
                  ['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d1", ["S1", "S0"])\n']],
          breaks: [['ruleFails', 'R2']],
          exercise: 'A ring with spurs. `S0` has three rails: two on the loop, one to the dock `A0`. So for R2 it is a junction: one ion may rest there, and no ion may pass through while one rests. The loaded programme parks a second ion on it. Move `d0` out of the way to `S5`. Then send `d1` through `S0` into the dock `A0`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd1', 'A0']] }
      ],
      choices: [
        { id: 'square', text: 'It is drawn as a square.' },
        { id: 'deg3', text: 'Three or more rails meet there, and each crossing is priced by that number.' },
        { id: 'any', text: 'Any node an ion passes without stopping.' }
      ],
      nudges: {
        rulesPass: ['A rule still fails: {have}', 'One crossing per step. And no ion may pass through a spur site while another ion sits on it: clear it first.'],
        ionAt: ['{have} is not where that ion should end.'],
        transitsAtMost: ['{have} junction transits. Each ion crosses once, so the answer is two.'],
        answer: ['Look at the picture: what do S0 and J0 have that the other sites do not?', 'Report prices a crossing by the number of rails at the junction. How many rails meet there?'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'One at a time through the crossing, and the dock as a home for the second ion. {transits} transits, cost {cost}. A junction is the most expensive thing an ion can cross, and that is why Report counts them.',
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d0", ["S0", "J0", "S1"])\np.shuttle("d1", ["S2", "J0", "S3"])\n']],
        [['setAnswer', 'deg3']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d0", ["S0", "S5"])\np.shuttle("d1", ["S1", "S0", "A0"])\n']]] } },

    // =============================================================== R3
    { id: 'R3', part: 'R', title: 'Rails: no passing, no ghosts',
      story: 'Two ions on one rail walk toward each other. They cannot pass.',
      teaches: ['rule:R5', 'rule:R8', 'el:segment'],
      text: [
        'A [[rail|el:segment]] is a single lane. [[R5|rule:R5]]: two ions never swap places along one rail in one step. This rail was given room for two ions, so R5 is the only rule in the way.',
        '[[R8|rule:R8]] is bookkeeping: outside loading, every ion is on exactly one site. No ion appears, none vanishes, and no ion makes two moves in one step. It sounds obvious. It is what makes every other rule checkable.'
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
          exercise: '`d0` and `d1` swap across `E0` in one step, and **Report** shows **R5** in red. Swap them legally, using `S2` as the passing place: `d0` ends on `S1`, `d1` on `S0`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S0']] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S0"})\np.simd("shuttle", [["d0", "S0", "S1"], ["d0", "S1", "S2"]])\n']],
          breaks: [['ruleFails', 'R8']],
          exercise: 'One ion makes two moves in one step: **R8** fails. Get `d0` to `S2` the right way: one shuttle, or two steps.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S2']] }
      ],
      nudges: {
        rulesPass: ['A rule still fails: {have}', 'No swapping on one rail in one step. Use the third site.'],
        ionAt: ['{have} is not where that ion should end.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'A legal swap and an honest walk: cost {cost}, {steps} steps. R5 is why sorting ions on a line is expensive, and R8 is why the page can tell you so.',
      boundary: { exercise: 'Moving together is legal: start from `d0` on `S1` and `d1` on `S0`, and move both one rail up (`S1` to `S2`, `S0` to `S1`) in one step. Same direction, different rails.',
                  check: ['all', ['frames', 2, 2], ['ionAt', 'd0', 'S2'], ['ionAt', 'd1', 'S1'], ['rulesPass']],
                  praise: 'One step, two rails, one direction: that is how a chain of ions moves along a line without ever breaking R5.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S1"})\np.shuttle("d1", ["S1", "S2"])\np.shuttle("d0", ["S0", "S1"])\np.shuttle("d1", ["S2", "S1", "S0"])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\n']]] } },

    // =============================================================== R4
    { id: 'R4', part: 'R', title: 'Steps: one kind of thing',
      story: 'One step is one setting of every voltage on the chip. So a step can do only one kind of thing.',
      teaches: ['rule:R4', 'rule:R4b', 'rule:R12', 'el:control'],
      text: [
        '[[R4|rule:R4]]: a step moves ions by one declared [[movement class|el:control]]. A class fixes the kind and the direction of the motion, and the device lists the classes it can drive.',
        '[[R4b|rule:R4b]]: a step has one mode, inter-trap (between traps) or intra-trap (inside a trap), and never mixes moving with gates.',
        '[[R12|rule:R12]]: inside one trap, one gate at a time.',
        'The page catches these in three ways. An undeclared class cannot be priced, so the top bar shows no price and the line under the canvas says why. A wrong mode is refused by the language itself. Only R12 gets as far as a red badge in **Report**.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit']],
      stages: [
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("teleport", [["d0", "S0", "S1"]])\n']],
          breaks: [['priceBlocked']],
          exercise: 'The loaded programme uses a class called `teleport`, which this device does not declare. So the top bar shows no price. **R4** wants a declared class: `shuttle`, `rotate_cw`, `rotate_ccw`, `dock`, `undock`, `sort_merge` or `sort_split`. In **Write**, replace `teleport` with one of them, so that `d0` still moves from `S0` to `S1`.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1']] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"]], "both")\n']],
          breaks: [['refused', 'R4b']],
          exercise: 'Now the step asks for mode `"both"`. The language refuses the line, and the line under the canvas names **R4b**. The third argument of `p.simd` is the mode: `"inter"` or `"intra"`; leave it out and it is `"inter"`. Make this a plain transport step.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1']] },
        { setup: [['emit', { method: 'set_zone', args: ['trap'], kwargs: { capacity: 4 } }],
                  ['applyProgramSource', 'p.init({"d0": "A0", "d1": "A0", "d2": "A0", "d3": "A0"})\np.cool()\np.gate("CX", [["d0", "d1"], ["d2", "d3"]])\n']],
          breaks: [['ruleFails', 'R12']],
          exercise: 'Four ions sit in the dock `A0`, and one instruction fires two CX pairs there. **Report** shows **R12** in red: one gate per trap per step. Split it into two instructions.',
          check: ['all', ['rulesPass'], ['gateCount', 'CX', 2]] }
      ],
      nudges: {
        rulesPass: ['Not passing yet: {have}', 'One declared class per step; one gate per trap per step.'],
        ionAt: ['{have} is not where d0 should end.'],
        gateCount: ['{have} CX gates. Keep both pairs, in two instructions.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Three ways a step can ask too much, three fixes: a declared class, one mode, one gate per trap. Cost {cost}, {steps} steps. R4 is the rule behind moving many ions with one instruction.',
      boundary: { exercise: 'Two gates in one step **are** legal in two different traps. Put `d2` and `d3` in `A3` instead, and fire both pairs in one instruction.',
                  check: ['all', ['frames', 3, 3], ['gateBetween', 'd0', 'd1', 'CX'], ['gateBetween', 'd2', 'd3', 'CX'], ['rulesPass']],
                  praise: 'Two traps, two gates, one step: gates in different traps may run together, and that is where the speed of these machines comes from.' },
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S3"})\np.simd("shuttle", [["d0", "S0", "S1"]])\n']],
        [['applyProgramSource', 'p.init({"d0": "A0", "d1": "A0", "d2": "A0", "d3": "A0"})\np.cool()\np.gate("CX", [["d0", "d1"]])\np.gate("CX", [["d2", "d3"]])\n']]] } },

    // =============================================================== R5
    { id: 'R5', part: 'R', title: 'Broadcasting: what one channel can drive',
      story: 'One channel gives one answer to every site on it. Two ions asked for two different answers.',
      teaches: ['rule:R4d', 'rule:R11', 'el:control', 'm:DACs'],
      text: [
        '[[R4d|rule:R4d]] is R4 worked out from the wiring: a channel carries one signal, so all the sites on it do one thing per step. A site can sit out only if it has its own switch.',
        'The browser cannot run this check: it needs the map from electrodes to channels. So the box below shows **the checker\'s verdict**, computed on this exact programme when the page was built.',
        'The programme is the B7 pair moving head-on, on a ring with the `wise` wiring scheme. The page itself catches it as R11.'
      ],
      setup: [['loadCase', 'R5'], ['fit']],
      verdict: { 'case': 'R5', which: 'loaded' },
      stages: [
        { exercise: 'Read the checker\'s verdict in the box below: "channel ... drives 8 sites with one waveform, but they are asked to do 2 different things ...; that needs 2 channels". Which fix does it point to?',
          check: ['answer', 'steps'] },
        { breaks: [['ruleFails', 'R11'], ['shipped', 'R5', 'loaded', 'R4d', 'failed']],
          exercise: 'Apply it in **Write**: two instructions, one direction each, `d0` ending on `S1` and `d1` on `S2`. R11 then passes. The box below now shows the checker\'s verdict on the fixed programme, computed when the page was built.',
          check: ['all', ['rulesPass'], ['ionAt', 'd0', 'S1'], ['ionAt', 'd1', 'S2'], ['shipped', 'R5', 'fixed', 'R4d', 'passed']],
          verdict: { 'case': 'R5', which: 'fixed' } }
      ],
      choices: [
        { id: 'rename', text: 'Rename the movement class.' },
        { id: 'switches', text: 'Add a switch per site. (A switch lets a site sit out, but not do something different.)' },
        { id: 'steps', text: 'Give each direction its own step, or its own channel. That is what "needs 2 channels" means.' }
      ],
      nudges: {
        answer: ['Read the end of the checker\'s sentence again.', 'The problem is two different things on one signal. Renaming changes nothing physical.'],
        rulesPass: ['A rule still fails: {have}', 'One direction per step. Split the two moves.'],
        ionAt: ['{have} is not where that ion should end.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'The checker\'s R4d verdict went from failed to passed on your programme, and the page agreed through R11. That is broadcasting as a rule: one signal, one motion, per channel, per step. Cost {cost}.',
      solution: { stages: [
        [['setAnswer', 'steps']],
        [['loadCase', 'R5', 'fixed'], ['fit']]] } },

    // =============================================================== R6
    { id: 'R6', part: 'R', title: 'Where things may happen',
      story: 'A gate needs a laser beam, and a measurement needs a camera. Not every site has them.',
      teaches: ['rule:R6', 'rule:R6b', 'el:zone_type'],
      text: [
        '[[R6|rule:R6]]: a gate, a measurement or a cooling happens only in a site whose [[zone|el:zone_type]] allows it. Here `trap` allows all three, and `data` allows only cooling.',
        '[[R6b|rule:R6b]]: a two-qubit gate needs both ions in one site. If the instruction names a site, the ions must be there.',
        'This line has a `data` site in the middle, `S1`. The first programme measures there. The second fires a gate on two ions that never met.'
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
          exercise: 'The measurement happens on `S1`, a `data` site that cannot measure, and **Report** shows **R6** in red. Measure `d0` somewhere it can be measured. It must still pass through `S1` first.',
          check: ['all', ['rulesPass'], ['measured', ['d0']], ['visited', 'd0', 'S1']] },
        { setup: [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.cool()\np.gate("CX", [["d0", "d1"]])\n']],
          breaks: [['ruleFails', 'R6b']],
          exercise: 'A CX between `d0` on `S0` and `d1` on `S2`: **R6b** fails, because the ions are not together. Bring them into one site that allows gates, then fire.',
          check: ['all', ['rulesPass'], ['gateBetween', 'd0', 'd1', 'CX']] }
      ],
      nudges: {
        rulesPass: ['A rule still fails: {have}', 'Which zone allows what? Click a site: the Selection box on the left shows its zone.'],
        measured: ['d0 is not measured yet.'],
        visited: ['d0 has to pass through S1 on its way. Keep that move.'],
        gateBetween: ['No rule fails, but the CX is gone. Keep the gate; move the ions to it.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Measured where a camera looks, gated where a beam reaches: cost {cost}, {steps} steps. R6 is why zones exist, and R6b is why a compiler spends most of its effort moving ions.',
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S0"})\np.shuttle("d0", ["S0", "S1", "S2"])\np.measure(["d0"])\n']],
        [['applyProgramSource', 'p.init({"d0": "S0", "d1": "S2"})\np.shuttle("d1", ["S2", "S1", "S0"])\np.cool()\np.gate("CX", [["d0", "d1"]])\n']]] } },

    // =============================================================== R7
    { id: 'R7', part: 'R', title: 'Heat: the budget, the clock, and why cooling is mandatory',
      story: 'The ion crossed a junction and arrived shaking. The gate fired anyway, in the programme. On the real machine it would fail.',
      teaches: ['rule:R7', 'rule:R7c', 'rule:R17', 'rule:R15', 'rule:R16', 'rule:R7b', 'm:peak n̄', 'el:heating'],
      text: [
        'Moving an ion warms it, and a junction crossing warms it most.',
        '[[R7|rule:R7]]: both ions of a two-qubit gate must be cool enough when the gate fires: under one quantum on this device.',
        '[[R7c|rule:R7c]]: with broadcast wiring, cooling is required. A programme with gates and no cooling at all is refused.',
        '[[R17|rule:R17]]: ions also warm with time, even at rest. The model adds that heat per millisecond.',
        'Three heat badges never turn green here. [[R15|rule:R15]] is amber: the page adds heats together, which only gives an upper bound. [[R16|rule:R16]] is grey: the checker checks it when the page is built, not the browser. The gate error depends on the heat at gate time, and the box below shows the number. [[R7b|rule:R7b]] is grey everywhere: no device declares a duty-cycle budget, so there is nothing to check, and the page says so.'
      ],
      setup: [['loadCase', 'R7'], ['fit']],
      verdict: { 'case': 'R7', which: 'loaded' },
      stages: [
        { breaks: [['ruleFails', 'R7']],
          exercise: '`d0` crosses the junction at `S0` and reaches the gate at about 3.2 quanta, and **Report** shows **R7** in red. The `p.cool()` before the walk did not help. In **Write**, move the cooling to after the walk.',
          check: ['all', ['rulesPass'], ['gateBetween', 'd0', 'd1', 'CX']],
          verdict: { 'case': 'R7', which: 'loaded' } },
        { exercise: 'The box below now shows the checker\'s verdict on the fixed programme. Compare its gate error (R16) with the one in step 1. Then: why are R15 amber and R7b grey in every Report?',
          check: ['answer', 'bound'],
          verdict: { 'case': 'R7', which: 'fixed' } }
      ],
      choices: [
        { id: 'stale', text: 'They have not been checked yet; reload the page.' },
        { id: 'bound', text: 'R15: heats are added, which gives only an upper bound, so the verdict is partial. R7b: no device declares a duty-cycle budget, so there is nothing to check, and the page says so.' },
        { id: 'browser', text: 'The browser cannot check them, but the checker did, and both passed.' }
      ],
      nudges: {
        rulesPass: ['A rule still fails: {have}', 'Where is the cooling compared with the crossing? It must come after.'],
        gateBetween: ['No rule fails, but the CX is gone. Keep the gate; move the cooling.'],
        answer: ['A partial verdict is a bound; a skipped one says what is missing. Which answer says both?', 'Neither badge is stale, and the checker does not pass R7b either. Report lists R7b under Not checked here, with the reason.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Cooled after the crossing, gated under the limit: cost {cost}, {steps} steps. The gate error the checker measured fell with it. R7 makes cooling a scheduling problem, and R15, R16 and R7b show exactly how much the page knows.',
      solution: { stages: [
        [['loadCase', 'R7', 'fixed'], ['fit']],
        [['setAnswer', 'bound']]] } },

    // =============================================================== R8
    { id: 'R8', part: 'R', title: 'Chains: splitting from the middle',
      story: 'Three ions sit in a row, and the one you want is in the middle.',
      teaches: ['rule:R14', 'el:site'],
      text: [
        'Ions in one site sit in a line: a chain. Only an ion at an end of the chain can be split off.',
        '[[R14|rule:R14]]: an ion in the middle must first swap to the end, and a swap takes three CNOTs. A programme that splits from the middle without paying for that gets for free a move the machine cannot do for free.',
        'Here the `data` zone holds three, and three ions sit in `S1`. The loaded programme docks the middle one.'
      ],
      setup: [['newFromGenerator', 'ring', { width: 3, height: 2, verticals: 2 }, { name: 'ring6d' }], ['fit'],
        ['emit', { method: 'set_zone', args: ['data'], kwargs: { capacity: 3 } }],
        ['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1", "d2": "S1"})\np.shuttle("d1", ["S1", "S0", "A0"], "dock")\n']],
      stages: [
        { breaks: [['ruleFails', 'R14']],
          exercise: '`d1` splits from the middle of a chain of three, and **Report** shows **R14** in red. First move `d2` out to `S2`: then `d1` is at the end of a chain of two. Then dock it.',
          check: ['all', ['rulesPass'], ['ionAt', 'd1', 'A0']] },
        { exercise: 'R14 charges three CX for an ion to reach the end. Why three?',
          check: ['answer', 'swap'] }
      ],
      choices: [
        { id: 'three_rails', text: 'The ion crosses three rails on the way.' },
        { id: 'swap', text: 'A swap of two qubits takes three CNOTs, and the ion must swap past its neighbour.' },
        { id: 'cooling', text: 'A split needs three cooling pulses afterwards.' }
      ],
      nudges: {
        rulesPass: ['A rule still fails: {have}', 'Make the chain shorter before the split.'],
        ionAt: ['{have} is not where d1 should end: the dock A0.'],
        answer: ['What is a SWAP gate made of?'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'End first, then split: cost {cost}, {steps} steps, and an honest price for reaching the end. R14 is why a compiler cares where an ion sits inside a site, not only which site.',
      solution: { stages: [
        [['applyProgramSource', 'p.init({"d0": "S1", "d1": "S1", "d2": "S1"})\np.shuttle("d2", ["S1", "S2"])\np.shuttle("d1", ["S1", "S0", "A0"], "dock")\n']],
        [['setAnswer', 'swap']]] } },

    // =============================================================== R9
    { id: 'R9', part: 'R', title: 'Honesty: claims, certificates and the contract',
      story: 'A programme said it would cost two. The replay said three.',
      teaches: ['rule:R9', 'rule:R10', 'tab:R'],
      text: [
        'A programme may make claims about itself: `p.claim(total_steps=5, total_cost=3)`. [[R9|rule:R9]] checks each claim against the replay. The browser does not run R9, so the [[Report|tab:R]] shows it grey, and the box below shows the checker\'s verdict.',
        '[[R10|rule:R10]] says a compiled programme does what its circuit says. It needs the circuit, and no lesson so far had one.',
        'What a badge colour means. **Green:** the check ran and passed. **Red:** it ran and failed. **Amber:** it ran, but can only give a bound. **Grey:** it did not run here, and the page says why. Nothing on this page is green for a check that did not run.'
      ],
      setup: [['loadCase', 'R9'], ['fit']],
      verdict: { 'case': 'R9', which: 'loaded' },
      stages: [
        { exercise: 'Open **Report**. R9 is grey. What does grey mean on this page?',
          check: ['answer', 'notrun'] },
        { breaks: [['shipped', 'R9', 'loaded', 'R9', 'failed']],
          exercise: 'In step 1 the box showed the claim (cost 2, 3 steps) and what the replay found. In **Write**, correct the `p.claim` line to the true numbers: **COST** and **STEPS** in the top bar show them. The box below now shows the checker\'s verdict on a corrected programme.',
          check: ['all', ['rulesPass'], ['claimIs', 'total_steps', 5], ['claimIs', 'total_cost', 3], ['shipped', 'R9', 'fixed', 'R9', 'passed']],
          verdict: { 'case': 'R9', which: 'fixed' } },
        { exercise: 'R10 has been grey on every page of this course so far. Why?',
          check: ['answer', 'nocircuit'],
          verdict: { 'case': 'R9', which: 'fixed' } }
      ],
      choices: [
        { id: 'passed', text: 'Passed, with warnings.' },
        { id: 'notrun', text: 'The check did not run here, and the page says why.' },
        { id: 'nocircuit', text: 'There is no circuit to check against: R10 needs the QASM the programme was compiled from. Part D brings one.' },
        { id: 'failed', text: 'Failed quietly.' }
      ],
      nudges: {
        answer: ['Report lists the grey rules under Not checked here, each with its reason.', 'Grey is neither a pass nor a failure.'],
        rulesPass: ['A rule fails: {have}'],
        claimIs: ['The claim for that number is {have}. The top bar shows what the replay found.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'A true claim, and the checker\'s R9 verdict turned from failed to passed. That is the whole contract: every number on this page is measured, bounded, or marked as not measured. Never guessed. Cost {cost}, {steps} steps, and the programme now says the same.',
      solution: { stages: [
        [['setAnswer', 'notrun']],
        [['loadCase', 'R9', 'fixed'], ['fit']],
        [['setAnswer', 'nocircuit']]] } },

    // =============================================================== C1
    { id: 'C1', part: 'C', title: 'A syndrome round of the repetition code',
      story: 'Three data ions, two check ions, four CNOTs, two measurements: the smallest error-correcting round, on a line of five sites.',
      teaches: ['p:gate', 'p:measure', 'rule:R1', 'rule:R6b', 'rule:R7'],
      text: [
        'The three-qubit repetition code keeps one logical bit in `d0 d1 d2`. Two check ions ask whether neighbours agree: `c0` checks `d0` and `d1`, and `c1` checks `d1` and `d2`.',
        'As a circuit: four CNOTs, CX(d0,c0), CX(d1,c0), CX(d1,c1), CX(d2,c1), then measure both checks.',
        'The ions start in the order `d0 c0 d1 c1 d2`, in `S0` to `S4`. Each site holds two. So a check ion can visit a data ion, but both checks cannot be in `S2` with `d1` at once ([[R1|rule:R1]]). Each gate needs both ions in one site ([[R6b|rule:R6b]]). Every move warms the ion that moves, and a gate needs it cold ([[R7|rule:R7]]): cool before each gate that follows a move.'
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
      exercise: 'Write the round: the four CNOTs (data ion first, check ion second) and a measurement of `c0` and `c1`. No rule may fail.',
      check: ['all', ['gateBetween', 'd0', 'c0', 'CX'], ['gateBetween', 'd1', 'c0', 'CX'], ['gateBetween', 'd1', 'c1', 'CX'], ['gateBetween', 'd2', 'c1', 'CX'], ['measured', ['c0', 'c1']], ['rulesPass']],
      nudges: {
        gateBetween: ['A CNOT is missing: {have}', 'Four pairs: d0-c0, d1-c0, d1-c1, d2-c1. Check the ion names in each.'],
        measured: ['Not measured yet: {have}.'],
        rulesPass: ['A rule fails: {have}', 'A gate right after a move fails R7: put p.cool() between them.', 'Both checks and d1 cannot all be in S2. Move c0 away before c1 comes.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'A syndrome round, by hand: {frames} instructions, {steps} steps, cost {cost}. Each CNOT of the circuit is a trip and a gate, and you scheduled all four under the rules.',
      solution: { program: 'p.init({"d0": "S0", "c0": "S1", "d1": "S2", "c1": "S3", "d2": "S4"})\np.cool()\np.shuttle("c0", ["S1", "S0"])\np.cool()\np.gate("CX", [["d0", "c0"]])\np.shuttle("c0", ["S0", "S1", "S2"])\np.cool()\np.gate("CX", [["d1", "c0"]])\np.shuttle("c0", ["S2", "S1"])\np.shuttle("c1", ["S3", "S2"])\np.cool()\np.gate("CX", [["d1", "c1"]])\np.shuttle("c1", ["S2", "S3", "S4"])\np.cool()\np.gate("CX", [["d2", "c1"]])\np.measure(["c0", "c1"])\n' } },

    // =============================================================== C2
    { id: 'C2', part: 'C', title: 'Parallelism under the rules',
      story: 'The same round, faster. Not by breaking a rule, but by using what the rules allow.',
      teaches: ['p:simd', 'rule:R12', 'rule:R4', 'rule:R22', 'm:steps'],
      text: [
        'Two rules allow more than your first round used. [[R12|rule:R12]] forbids two gates in one trap per step, but two gates in two different traps may share an instruction: `p.gate("CX", [["d0", "c0"], ["d1", "c1"]])`.',
        '[[R4|rule:R4]] lets one `p.simd` move many ions in one step, if they all move the same way. One step is one signal on the electrodes ([[R22|rule:R22]]), so `d0` moving right while `d2` moves left takes two steps.',
        'So choose moves that agree. The two check ions are as far apart as the data ions they visit: move them together, and every move is shared.',
        'The C1 solution took 24 steps. **STEPS** in the top bar shows your count.'
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
      exercise: 'The same round (four CNOTs, both checks measured) in **at most 12 steps**.',
      check: ['all', ['gateBetween', 'd0', 'c0', 'CX'], ['gateBetween', 'd1', 'c0', 'CX'], ['gateBetween', 'd1', 'c1', 'CX'], ['gateBetween', 'd2', 'c1', 'CX'], ['measured', ['c0', 'c1']], ['rulesPass'], ['stepsAtMost', 12]],
      nudges: {
        gateBetween: ['A CNOT is missing: {have}'],
        measured: ['Not measured yet: {have}.'],
        rulesPass: ['A rule fails: {have}'],
        stepsAtMost: ['{have} steps. Which moves can share an instruction? Which gates can?', 'Two gates in two different traps may share one instruction (R12 is per trap).', 'Moves share a step only when they go the same way (R22). Move the two checks together.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: '{steps} steps for the round that took 24. Nothing was skipped: four CNOTs, two measurements, and no rule failed. The machine simply did several legal things at once.',
      solution: { program: 'p.init({"d0": "S0", "c0": "S1", "d1": "S2", "c1": "S3", "d2": "S4"})\np.cool()\np.simd("shuttle", [["c0", "S1", "S0"], ["c1", "S3", "S2"]])\np.cool()\np.gate("CX", [["d0", "c0"], ["d1", "c1"]])\np.simd("shuttle", [["c0", "S0", "S1"], ["c1", "S2", "S3"]])\np.simd("shuttle", [["c0", "S1", "S2"], ["c1", "S3", "S4"]])\np.cool()\np.gate("CX", [["d1", "c0"], ["d2", "c1"]])\np.measure(["c0", "c1"])\n' } },

    // =============================================================== C3
    { id: 'C3', part: 'C', title: 'One plaquette',
      story: 'Four data ions around a crossing, and one check ion that must visit each of them.',
      teaches: ['el:junction', 'rule:R2', 'rule:R7', 'm:junctiontransits'],
      text: [
        'A surface-code check is a plaquette: one check ion meets four data ions. Here the four sit on the arms of a [[junction|el:junction]], and `a0` starts next to `d0` in `S0`.',
        'Each visit is a CNOT with the data ion as control. Each move to another arm crosses the junction once. [[R2|rule:R2]] allows one crossing per step, and **Report** counts them as junction transits.',
        'A crossing warms the ion by about three quanta on this chip, but a gate needs it under one ([[R7|rule:R7]]). So the programme must go: gate, cross, cool, gate, cross, cool, and so on. Here cooling is not optional: it is the only way the next gate is allowed.'
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
      exercise: 'Do the check on all four: a CX from each data ion onto `a0`, then measure `a0`. Use **at most 3 junction transits**. No rule may fail.',
      check: ['all', ['gateBetween', 'd0', 'a0', 'CX'], ['gateBetween', 'd1', 'a0', 'CX'], ['gateBetween', 'd2', 'a0', 'CX'], ['gateBetween', 'd3', 'a0', 'CX'], ['measured', ['a0']], ['transitsAtMost', 3], ['rulesPass']],
      nudges: {
        gateBetween: ['A CNOT is missing: {have}'],
        measured: ['a0 is not measured yet.'],
        transitsAtMost: ['{have} junction transits. Four arms need only three crossings.'],
        rulesPass: ['A rule fails: {have}', 'Cool after every crossing: a gate needs the ion under one quantum.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'One plaquette: {transits} crossings, {steps} steps, cost {cost}, runtime {us} us. Most of that time is the three crossings and the three coolings they force. A grid architecture lives or dies by this number.',
      solution: { program: 'p.init({"d0": "S0", "d1": "S1", "d2": "S2", "d3": "S3", "a0": "S0"})\np.cool()\np.gate("CX", [["d0", "a0"]])\np.shuttle("a0", ["S0", "J0", "S1"])\np.cool()\np.gate("CX", [["d1", "a0"]])\np.shuttle("a0", ["S1", "J0", "S2"])\np.cool()\np.gate("CX", [["d2", "a0"]])\np.shuttle("a0", ["S2", "J0", "S3"])\np.cool()\np.gate("CX", [["d3", "a0"]])\np.measure(["a0"])\n' } },

    // =============================================================== C4
    { id: 'C4', part: 'C', title: 'Two rounds and cooling',
      story: 'Error correction never stops. The second round starts where the first one ended.',
      teaches: ['rule:R7c', 'rule:R7', 'm:runtime', 'p:reset'],
      text: [
        'After the measurement, [[p.reset|p:reset]] resets `a0`, and the round runs again.',
        'The naive second round first walks `a0` back to `d0`: three crossings for nothing. The better one visits the arms in reverse order, starting where `a0` already is.',
        'The check is on **runtime**. **RUNTIME** in the top bar shows it, in ms. Each crossing you save also saves a cooling.'
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
      exercise: 'Two full rounds (eight CNOTs, two measurements, a reset between them) in **under 4500 us** (4.5 ms). No rule may fail.',
      check: ['all', ['gateCount', 'CX', 8], ['measured', ['a0']], ['hasFrame', 'reset'], ['runtimeAtMost', 4500], ['rulesPass']],
      nudges: {
        gateCount: ['{have} CX gates so far; two rounds need eight.'],
        hasFrame: ['No reset between the rounds: add p.reset(["a0"]) after the first measurement.'],
        runtimeAtMost: ['{have} us. Where does a0 start its second round? Do not walk it back.', 'Two rounds need at least six crossings. Report shows your count as junction transits.'],
        rulesPass: ['A rule fails: {have}'],
        measured: ['a0 is not measured yet.'],
        lowered: ['The text did not run: {have}'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Two rounds in {us} us: {gates} CNOTs, six crossings, six coolings, and a reset between. That is a syndrome extraction loop, scheduled by hand with no rule failing. Part D shows what the compiler makes of the same job.',
      challenge: { label: 'sharp', exercise: 'Cool only where a gate needs it: after each crossing, cool just `a0` with `p.cool(["a0"])`, and nothing else. Stay under 4500 us, with no rule failing.',
                   check: ['all', ['gateCount', 'CX', 8], ['runtimeAtMost', 4500], ['rulesPass'], ['not', ['broadcastCool']]],
                   praise: 'Targeted cooling, and the same runtime: on this chip a cooling pulse costs the same for one ion as for all. Now you know that about the model.' },
      solution: { program: 'p.init({"d0": "S0", "d1": "S1", "d2": "S2", "d3": "S3", "a0": "S0"})\np.cool()\np.gate("CX", [["d0", "a0"]])\np.shuttle("a0", ["S0", "J0", "S1"])\np.cool()\np.gate("CX", [["d1", "a0"]])\np.shuttle("a0", ["S1", "J0", "S2"])\np.cool()\np.gate("CX", [["d2", "a0"]])\np.shuttle("a0", ["S2", "J0", "S3"])\np.cool()\np.gate("CX", [["d3", "a0"]])\np.measure(["a0"])\np.reset(["a0"])\np.gate("CX", [["d3", "a0"]])\np.shuttle("a0", ["S3", "J0", "S2"])\np.cool()\np.gate("CX", [["d2", "a0"]])\np.shuttle("a0", ["S2", "J0", "S1"])\np.cool()\np.gate("CX", [["d1", "a0"]])\np.shuttle("a0", ["S1", "J0", "S0"])\np.cool()\np.gate("CX", [["d0", "a0"]])\np.measure(["a0"])\n' } },

    // =============================================================== D1
    { id: 'D1', part: 'D', title: 'What the compiler does',
      story: 'You have written every instruction by hand. Now read what a compiler wrote for a real circuit, and find the instruction that does the CNOT.',
      teaches: ['tab:Q', 'rule:R10', 'tab:P'],
      page: 'micro_grid9x9',
      text: [
        'The compiler takes a circuit and a device. The circuit here is `micro.qasm`: four qubits, seven statements. The compiler writes the hardware programme: where the ions start, every shuttle, every gate (as the pulses that make it), every cooling.',
        'It also writes a **certificate**: for each instruction, which statement of the circuit it carries out. [[R10|rule:R10]] says the programme does what the circuit says. The certificate says where and when each statement is carried out; which gate the pulses make is checked separately, by composing them back into the circuit.',
        'On this page the [[Circuit|tab:Q]] tab shows the circuit next to the animation. The statement being carried out now is marked. Click a statement, and the animation jumps to the instruction that carries it out.'
      ],
      setup: [['seekTo', 0]],
      exercise: 'Open the **Circuit** tab. Click the statement `cx q[0],q[1]`: the animation jumps to the instruction that carries out that CNOT, and the line above the circuit reads `executing cx q[0],q[1]`. Then go back to **Learn** and press **Check**.',
      check: ['frameIs', 'realises:1'],
      guide: [['stmt', 1], ['check']],
      nudges: {
        frameIs: ['You are on instruction {have}. Click the cx q[0],q[1] statement in the Circuit tab.', 'The statement you want is the first cx. The certificate says which instruction carries it out.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'Instruction {frame} is the CNOT: the certificate says so, and the page checked that certificate before it drew anything. Every CNOT you wrote by hand in Part B was this, with the compiler doing the trip.',
      solution: { steps: [['seekTo', 'realises:1']] } },

    // =============================================================== D2
    { id: 'D2', part: 'D', title: 'The same circuit on nine machines',
      story: 'One circuit, compiled for every shipped device and replayed under the same model. The numbers are in the table below. The question is why.',
      teaches: ['m:cost', 'm:runtime', 'm:junctiontransits', 'el:loop'],
      table: 'micro',
      text: [
        'These numbers are measured, not estimated: the compiler output for `micro.qasm` on each shipped device, replayed when this page was built, with cooling added.',
        'In the table: **cost** is the charge for moving ions, **steps** are machine steps, **runtime** is how long it takes, and **DACs** are the control channels of the device.',
        'Two families stand out. A ring pays for a **rotation** every time it moves anything, because all 144 ions turn together: its cost is high and its programme long. A grid pays for **junction crossings** and the cooling they force: its runtime is long even when its cost is small. A chain pays almost nothing on a four-qubit circuit, but could not hold a bigger one.'
      ],
      setup: [['newCanvas', { name: 'scratch' }]],
      stages: [
        { exercise: 'Look at the table below. Which has the lower **cost** for this circuit: `ring144_24v` or `grid9x9`?',
          check: ['measuredLess', 'micro', 'total_cost', 'grid9x9', 'ring144_24v'] },
        { exercise: 'Which is **faster** (the smaller runtime): `grid9x9` or `ladder_2x72`?',
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
      nudges: {
        measuredLess: ['The table says otherwise: {va} against {vb}.', 'Look at the column the question names, not the one next to it.'],
        answer: ['Think of B5: what does one rotation cost on a full loop?'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'The table agrees with you twice, and so does the reason. An architecture decision is a measured number and the rule that explains it, never a slogan about rings or grids.',
      solution: { stages: [[['setAnswer', 'grid9x9']], [['setAnswer', 'ladder_2x72']], [['setAnswer', 'rotation']]] } },

    // =============================================================== D3
    { id: 'D3', part: 'D', title: 'Broadcast versus direct at scale',
      story: 'The same chip, wired two ways. One needs over five thousand DACs. The other needs forty-four.',
      teaches: ['m:DACs', 'm:electrodes', 'el:control', 'rule:R4d'],
      table: 'planes',
      text: [
        '`grid9x9` and `deck_unit_cell` have the same layout: 144 traps, 77 junctions, and the same 8496 electrodes. The grid is wired **direct**, with thousands of DACs. The deck is wired **broadcast** (the table calls its scheme `wise`): whole groups of electrodes share one signal.',
        'The table below is the hardware report of every shipped device. Its DAC column is the whole case for broadcasting.',
        'The price of broadcasting, from R5: one signal per channel per step, so some programmes need more steps. The D2 table shows whether this one did.'
      ],
      setup: [['newCanvas', { name: 'scratch' }]],
      stages: [
        { exercise: 'What did broadcasting cost `micro.qasm` on the deck, compared with the direct-wired grid? Compare their steps and runtime in the D2 table (the ◀ button above goes back to D2).',
          check: ['all', ['measuredSame', 'micro', 'total_steps', 'grid9x9', 'deck_unit_cell'], ['measuredSame', 'micro', 'runtime_us', 'grid9x9', 'deck_unit_cell'], ['answer', 'nothing']] },
        { exercise: 'Now put the broadcast device on the canvas. Press **Start** in the tools bar. Next to **open**, choose `deck_unit_cell`, then press **open**. **DACS** in the top bar must stay under the budget of 128, with all 144 traps.',
          check: ['all', ['dacsAtMost', 128], ['sites', 144]],
          guide: [['tool', 'start'], ['pick', '#palOpen', 'deck_unit_cell', 'Pick deck_unit_cell.'],
                  ['press', 'button[data-open-pick="palOpen"]', 'Press open.'],
                  ['goal', ['all', ['dacsAtMost', 128], ['sites', 144]]], ['check']] }
      ],
      choices: [
        { id: 'slower', text: 'It ran slower: more steps and a longer runtime.' },
        { id: 'nothing', text: 'Nothing, on this programme: the same steps and the same runtime. The cost only appears when two things on one channel want to happen at once.' },
        { id: 'hotter', text: 'The ions got hotter.' }
      ],
      nudges: {
        measuredSame: ['The measured tables disagree with this lesson: {have}. Trust the table.'],
        answer: ['Compare the two rows in the D2 table first: steps and runtime.', 'The two devices differ in the DAC column, not the runtime column.'],
        dacsAtMost: ['The top bar reads {have} DACs. That is the direct-wired grid, or an empty canvas.'],
        sites: ['{have} sites: the deck has 144 traps. Press Start and open deck_unit_cell next to open.'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: '{dacs} DACs for 144 traps and 77 junctions. That number is what makes a large trapped-ion machine buildable, and R4d is the rule you pay for it with.',
      solution: { stages: [[['setAnswer', 'nothing']], [['newFromTemplate', 'deck_unit_cell', { name: 'deck_unit_cell' }], ['fit']]] } },

    // =============================================================== D4
    { id: 'D4', part: 'D', title: 'Running the compiler yourself',
      story: 'Everything on this page came from a command line. Here are the commands.',
      teaches: ['tab:W', 'tab:R'],
      text: [
        '**Save your work:** press **Ctrl+S** to save it as `<name>.studio.json`. To copy the device or the programme, open **Device**, press **Source**, choose `as .arch.json` or `as .tsir.json` under the text, and press **Copy**.',
        '**Check everything:** `python -m qccd open <name>.studio.json` runs the full checker on what you built, including the rules the [[Report|tab:R]] lists as not checked here.',
        '**Open a device:** `python -m qccd studio --seed <name>.arch.json --program rotate -o x.html` opens a device with a built-in programme.',
        '**Open a compiled programme:** `python -m qccd studio --tsir <compiled>.tsir.json --qasm <circuit>.qasm -o x.html` opens it with its circuit beside it. Lesson D1 uses such a page.',
        '**Build this course:** `python -m qccd tutorial -o tutorial` builds this page and its companion page.',
        'The compiler takes a QASM file and a device, and writes the `.tsir.json` programme and the `.qcert.json` certificate that the **Circuit** tab checks. The D2 table was built with it. A programme of your own goes in the [[Write|tab:W]] tab.'
      ],
      setup: [['newCanvas', { name: 'scratch' }]],
      exercise: 'Last question. Which command opens a compiled programme with its circuit beside it?',
      check: ['answer', 'studio'],
      choices: [
        { id: 'open', text: 'python -m qccd open <file>.studio.json' },
        { id: 'studio', text: 'python -m qccd studio --tsir <x>.tsir.json --qasm <x>.qasm -o x.html' },
        { id: 'compile', text: 'python -m qccd compile <x>.qasm' }
      ],
      nudges: {
        answer: ['Read the commands again: which one names a circuit?'],
        'default': ['Not there yet. Press Hint to be shown the next step.']
      },
      praise: 'That is the whole loop: design here, check everything on your own machine, compile a circuit, and read the result back on a page like this one. You started with one picture of a chip. Now you can build, program, break, fix and judge one.',
      solution: { stages: [[['setAnswer', 'studio']]] } }
  ]
};

if (typeof globalThis !== 'undefined' && globalThis.EDITOR && globalThis.EDITOR.lessonsReady) {
  globalThis.EDITOR.lessonsReady(QCCD_TUTORIAL);
}
