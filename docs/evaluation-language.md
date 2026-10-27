# Evaluation as an input: the evaluation language

Every number a design is judged by is a term of a language, written as text, and evaluated by
an interpreter of that text. Python reads it in `qccd/evalset/` (the grader, the workspace, the
site); the browser reads the same text in `qccd/viz/js/evaluate.js` (every studio page):

* a **metric** is a named term over the run (`metric T "Execution time" unit "ms" better low
  = time(final()) / 1000`), optionally on a `clock` of its own (`T_jones`, `T_transport`,
  `T_quantinuum` replay the programme under that operating-point table);
* a **requirement** is a formula a design must satisfy to be ranked (`require B1 "The wiring
  fits the DAC budget" max_dacs = none or dacs <= max_dacs`);
* a **channel** says which Pauli error goes where in the circuit a run becomes
  (`after g in two_qubit_gates: DEPOLARIZE2(min(15 / 16, ms_error(...))) on ions(g)`), and
  which budget line(s) its expected faults are booked under (`counted as ms_base = ...,
  ms_heating = ..., ms_chain = ...`, split in proportion to the terms);
* `param`, `define` (named terms, with arguments), `include`, `clock` (the operating-point
  table the replay prices at; a name `qccd.arch.curves.TABLES` does not declare is refused,
  with its line), `rules` (the rule set), `rank`.

Terms and formulas are the rule language's (`qccd/ruleset/syntax.py`, reused by subclassing
its parser), plus `if C then a else b`. The grammar is in `qccd/evalset/syntax.py`.

## The pieces

| file | what |
|---|---|
| `syntax.py` | the items, the parser, `show` |
| `model.py` | the run: one replay (`qccd.verify.replay`, or `verify` with `rules=True`) read into steps, operations, instants and the state (position, occupancy, n̄, time); the preparation and the experiment's appended readout as extra steps. A state equal to the step before's is the same dict, never a copy |
| `words.py` | every word, with its definition (printed on the Evaluation page) and implementation |
| `interp.py` | term evaluation; `emit_circuit` (the stabilizer translation plus the channels, in a fixed order inside each step); `noise_budget` (the same bookings without a circuit, for a page with no memory experiment) |
| `ast_json.py` | the parse trees as JSON, for the browser (`payload(ev)`) |
| `compare.py` | `same_circuit`: equality of two stim circuits qubit by qubit (their Mazurkiewicz traces) plus measurement order and detectors |
| `bundle.py` | evaluate a leaderboard bundle (zip or folder) against its release |
| `library/` | `metrics.eval`, `board.eval` (B1, B2: the hardware budget), `qccd-noise@1.eval`, `qccd-noise@2.eval`, `goal1.eval` |
| `examples/` | two published Goal 1 entries with their official reports, and `agreement.json` |

`python -m qccd.evalset list | show <name> | bundle <zip> [--sample] [--stim f] | agree <zip>...`

## Where the text is read

* **The grader.** `qccd.qec.extract` writes the noisy circuit with `emit_circuit` under the
  release's noise text (`qccd.qec.noise.evaluation(model)` sets the knobs a model differs in);
  the rules stage reads the verifier's verdicts from the same replay. The metrics stage
  (`qccd.workspace.metrics.compute_metrics`) is `metrics.eval`; the device stage is
  `board.eval`'s B1 and B2 on the release's budget. The old extraction is kept as
  `qccd/qec/legacy_extract.py`, for the tests only.
* **Every studio page.** `qccd.viz.render.evaluation_view` embeds `for_page(clock)` (every board
  metric, the budget, and the clock's noise model) as parse trees, with the values Python gave
  them on the shipped device and programme. The head's chips, the price line, the Backed table,
  the hardware table and the Report's "Judged by the evaluation" section show those values until
  the page is changed, and the browser's (`js/evaluate.js`, recorded off the editor's own price
  walk) after. `EDITOR.evaluationCheck()` holds the browser to the shipped values.
* **The boards.** The memory boards' noise panel lists the noise text's budget lines by the
  clauses that book them; the waiting column sums the lines the text prices by elapsed time.
  The leaderboard links each measure to its definition on the Evaluation page.

## Held to the grader

`tests/test_evalset.py`:

* the circuit `goal1.eval` writes equals the legacy extraction's, qubit by qubit, on both
  example entries; the same for `qccd-noise@1`; `extract` is the text;
* the error budget agrees channel by channel to 1e-9 with the official reports;
* `T`, `instructions`, `transport_instructions`, `ions_per_instruction`, `dacs` equal the
  reports, and `ler`, `ler_per_round`, `ler_per_round_per_logical` and the Wilson ends equal
  them when fed the reports' failure counts;
* `T`, `cost`, `peak_nbar`, `gate_error_sum`, `junction_crossings` equal the replay's totals;
* `electrodes`, `switches`, `dacs` equal `hardware_report(arch, floors=WIRING_FLOORS)` on every
  `arch/*.arch.json`;
* a clock is a table; the interpreter's shortcuts give what the plain walk gives.

`tests/test_eval_parity.py`: the two interpreters agree on every metric, requirement and budget
line (13 cases: two Goal 1 entries, five devices, the deck model twice, four small programmes).
`tests/test_eval_page.py`: on an emitted page, the browser gives the shipped values, and after
an edit the evaluation of the edited device.

2026-10-26, outside the test suite: all 71 Goal 1 designs held locally (10 releases, 10 to 180
ions) agree on all of the above (`examples/agreement.json`); the text's circuit equals the
legacy extraction's on every stored bundle under both noise models and custom knobs (284 checks).

## Things the text makes visible

Writing the grader's behaviour down exposed choices that were only in code:

* the appended readout is not a step of the programme, so it is charged its readout error
  and the coherent dephasing of the wait before it, but not linear memory or crosstalk;
* a reset's crosstalk reaches only ions in *other* traps (the same-trap ions get neither the
  local nor the global channel);
* `ions_per_instruction` counts a rotation's riders as the ions placed on a closed loop at the
  programme's start (`loop_riders(k)`), not the ions actually on the loop at that step
  (`movers(k)` is the latter);
* the clock adds cycle by cycle while the noise reads each step's own summed duration; the
  two agree, but the circuit and the clock are computed from the same views in different
  orders;
* `depth` is read off the moves (a route one deep per rail and per charged split or merge), not
  off a pricing model's step count: under the deck model the two differ (5,444 and 8,808 on
  the ring's deck programme), and the browser once showed the second;
* seven task files named a table that does not exist (`qccdsim_transport`); their
  `T_transport` was never computed and a copy of `T_jones` was reported in its place.

## Performance

The interpreter keeps a define's value per argument tuple and a domain per term, never a
word's value per call (a 20,000-step programme asks `nbar(i, t)` three million times). A
one-variable aggregate rebinds one environment in place and evaluates a word's other arguments
once. On the largest stored Goal 1 programme (19,569 steps, 98 ions) Python's run is 2.9 s and
`peak_nbar` 2.2 s; the browser evaluates its head metrics in about 1.5 s.

## Adding things

A word: one `@word(...)` in `words.py` (its definition is the page's) and its twin in
`js/evaluate.js` (the parity test fails without it). A metric, requirement or channel: text
only. A new noise model is a new file with a new id, never an edit of a published one: a
logical error rate is comparable only with another computed under the same text.
