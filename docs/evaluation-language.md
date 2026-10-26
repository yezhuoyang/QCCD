# Evaluation as an input: the evaluation language

Until 2026-10-26 the numbers a design is judged by were Python: `qccd/workspace/metrics.py` for
the leaderboard metrics, `qccd/cost/hardware.py` for the DAC count, `qccd/qec/noise.py` +
`qccd/qec/extract.py` for the noisy stim circuit Goal 1 samples. They still are what the grader
runs. Beside them there is now a language in which every one of those numbers is written as
text, and an interpreter (`qccd/evalset/`) that evaluates exactly that text:

* a **metric** is a named term over the run (`metric T "Execution time" unit "ms" better low
  = time(final()) / 1000`);
* a **requirement** is a formula a design must satisfy to be ranked (`require G2 "The wiring
  fits the DAC budget" dacs <= max_dacs`);
* a **channel** says which Pauli error goes where in the circuit a run becomes
  (`after g in two_qubit_gates: DEPOLARIZE2(min(15 / 16, ms_error(...))) on ions(g)`);
* `param`, `define` (named terms, with arguments), `include`, `clock` (the operating-point
  table the replay prices at), `rules` (the rule set), `rank`.

Terms and formulas are the rule language's (`qccd/ruleset/syntax.py`, reused by subclassing
its parser), plus `if C then a else b`. The grammar is in `qccd/evalset/syntax.py`.

## The pieces

| file | what |
|---|---|
| `syntax.py` | the items, the parser, `show` |
| `model.py` | the run: one replay (`qccd.verify.replay`) read into steps, operations, instants and the state (position, occupancy, n̄, time); the preparation and the experiment's appended readout as extra steps |
| `words.py` | every word, with its definition (printed on the Evaluation page) and implementation |
| `interp.py` | term evaluation, and `emit_circuit`: the stabilizer translation plus the channels, in a fixed order inside each step |
| `compare.py` | `same_circuit`: equality of two stim circuits qubit by qubit (their Mazurkiewicz traces) plus measurement order and detectors |
| `bundle.py` | evaluate a leaderboard bundle (zip or folder) against its release |
| `library/` | `metrics.eval`, `qccd-noise@1.eval`, `qccd-noise@2.eval`, `goal1.eval` |
| `examples/` | two published Goal 1 entries with their official reports, and `agreement.json` |

`python -m qccd.evalset list | show <name> | bundle <zip> [--sample] [--stim f] | agree <zip>...`

## Held to the grader

`tests/test_evalset.py`:

* the circuit `goal1.eval` writes equals `qccd.qec.extract`'s, qubit by qubit, on both
  example entries; the same for `qccd-noise@1`;
* the error budget agrees channel by channel to 1e-9 with the official reports;
* `T`, `instructions`, `transport_instructions`, `ions_per_instruction`, `dacs` equal the
  reports, and `ler`, `ler_per_round`, `ler_per_round_per_logical` and the Wilson ends equal
  them when fed the reports' failure counts;
* `T`, `cost`, `peak_nbar`, `gate_error_sum`, `junction_crossings` equal the replay's totals;
* `electrodes`, `switches`, `dacs` equal `hardware_report(arch, floors=WIRING_FLOORS)` on every
  `arch/*.arch.json`.

2026-10-26, outside the test suite: all 71 Goal 1 designs held locally (10 releases, 10 to 180
ions) agree on all of the above (`examples/agreement.json`).

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
  orders.

## Adding things

A word: one `@word(...)` in `words.py` (its definition is the page's). A metric, requirement
or channel: text only. A new noise model is a new file with a new id, never an edit of a
published one: a logical error rate is comparable only with another computed under the same
text.
