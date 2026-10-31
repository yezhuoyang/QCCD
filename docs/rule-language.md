# Rules as an input: the rule language

Until 2026-10-19 the rules were fixed: twenty-odd Python checks (`qccd/verify/rules.py`), the
same for every programme, plus a separate set of boolean switches for the papers on Reproduce
(`qccd/repro/timed.py`'s `Profile`).  Papers assume different rules, and the compiler could
not be told which to obey.  Now:

* a **rule** is one assertion about the hardware and the execution, written in a small
  language (`qccd/ruleset/syntax.py`) over a fixed vocabulary of words
  (`qccd/ruleset/vocabulary.py`), and the checker evaluates exactly that text;
* a **rule set** picks rules and sets their parameters (`qccd/ruleset/library/*.rules`);
* the **compiler** takes a rule set as input (`qccdc ... --rules <file>`) and plans with the
  rules it knows; its output is judged by every rule of the set, including rules it has
  never seen (`qccd/ruleset/compile.py`);
* every **paper** on Reproduce has its rule set, and its pages show every run against every
  rule.

## A rule

    rule R1 "A site holds at most its capacity" core
      about "An ion standing in a site, or passing through it, takes one of its places."
      source "deck_v3; arXiv:2004.04706"
      for all s in sites, t in instants:
        occupancy(s, t) <= capacity(s)

`for all x in D where C: B`, `for some ...`, `and`, `or`, `not`, `implies`, comparisons,
`in`, arithmetic, `count(x in D where C)`, `sum/max/min(e for x in D where C)`,
`{e for x in D where C}` and `size(S)`, `all_different(e for x in D)`.  A quoted name stands
for the site or ion of that name (`position(i, t) != "C3"`).  `when C:` before the formula
is a precondition: when it is false the rule *does not apply*, which is reported as such.

Verdicts: **holds**, **fails** (with the number of counterexamples and the first few, each
naming the values that make the formula false), **does not apply** (with why: a word the
execution does not have, a `when` that is false), **could not be checked** (a value the rule
needs is missing).  A rule never holds by not being evaluated.

## The vocabulary

Every word has a definition in words (printed on the Rules page) and an implementation for
both kinds of execution:

* a **programme** (`qccd/ruleset/programme.py`): the replay's per-step views.  Instants are
  before each step, during each transport step, and the end.  During a step a site counts the
  ions resting there before or after (the larger) plus those passing through -- the existing
  conservative R1 -- and a rail counts the ions crossing it.
* a **timed schedule** (`qccd/ruleset/schedule.py`): the timed checker's replay, event by
  event; instants are every start and end.

Adding a word is one `@term(...)` in `vocabulary.py`.  Adding a rule needs no code.

## Evaluation

`qccd/ruleset/evaluate.py` evaluates formulas directly, but plans quantifiers so long
executions are checked in seconds: equality joins through an index, `simultaneous` through
steps or a sorted interval list (and both together, per key), relations (`next_on_qubit`,
`next_run`) enumerated, body-directed pruning for `for all` (only pairs that share a key can
break `site(a) != site(b)` or `disjoint(ions(a), ions(b))`), "one value per step" for
`for all a, b where simultaneous(a, b): f(a) = f(b)` on a programme, and, for
`for all x, t in instants` over `occupancy(x, t)` / `position(x, t)`, only the x that changed
at each instant.  None of these changes an answer; `tests/test_ruleset.py` holds the language
to the legacy checkers rule by rule.

Measured 2026-10-19: all 19 compiled examples and 93 programmes of `Compiler/build/matrix`
get the legacy verifier's verdicts rule for rule (BB144 on `ring144_24v`, 5,468 instructions:
12 s, against 8.4 s for the legacy verifier); all 105 Reproduce runs, theirs and ours, get
the timed checker's (a 1.2 million event Cyclone schedule: 73 s).

## Core rules

R0 (every operation finds its ions where it says), R1 (capacity), R8 (an ion does one thing
at a time) and R24 (every two-qubit gate of the circuit is performed, and no other) are
`core`: they are in every rule set and `drop` refuses them.

## The compiler

`Compiler/ocaml/lib/rules.ml` reads a resolved rule set (`RuleSet.text()`: every rule
written out, no `include`/`use`) and plans with:

* R13: `param max_chain` bounds a gate chain; without R13 only the trap's capacity does;
* R22: a transport cycle is one waveform; without it a cycle may carry unlike motions.

Everything else the compiler already obeys whatever the set says (a stricter programme obeys
a weaker set).  Without `--rules` it plans with the standard set and its output is byte for
byte what it was.  `python -m qccd.ruleset compile <qasm> --arch <doc> -o <dir> --rules <set>`
compiles, cools, checks R10 (with the proved checker when it is built) and judges every rule.

## The rule sets

`standard` (this website's machine), `qccdsim`, `qccdsim-physical`, `qccdsim-gateset`,
`cyclone`, `jones`, `tiscc`, `trapsimd`, `ionshuttler`, `ionshuttler-heuristic`.
`python -m qccd.ruleset list` / `show <set>`.  Our own schedules on Reproduce are judged by
the paper's set with R25 (each qubit's exact order) in place of R26 (`keeping_order`).

## A leaderboard entry's own rules

Since 2026-10-29 a design is entered on a board under a rule set: the board's own (`standard`)
unless it names another.  The person picks it in the Studio's Leaderboard panel (Rules, next to
Test on this board), or `qccd submit --local --board <b> --rules <name or file>`, or an agent's
`qccd_submit_local(rules=...)`; `qccd_list_rules` lists the choices -- every shipped set and the
workspace's own `.rules` files (top level or `rules/`).  A set written for a design usually starts
from the board's:

    ruleset "two-layer"
    title "Two stacked junction layers"
    include "standard"
    drop R19, R20, R21
    rule L19 "A junction joins at most six rails"
      for all n in nodes: degree(n) <= 6

The bundle carries it written out whole (`rules.json`, `qccd.ruleset.declared.rules_doc`), and
the grader reads it with `load_declared`: `include` names only a shipped set, never a path; the
core rules hold as the catalogue writes them (a set that drops or rewrites R0, R1, R8 or R24 is
refused); at most 64 KB and 200 rules.  The rules stage judges the program by that set (every rule
holds or does not apply; R10 is the certificate stages'), and still checks the board's rules, so the
report's `conformance` says which board rules the entry breaks and what its set drops, rewrites and
adds.  The certificate, the Lean checker, the semantics and the logical error rate are the same for
every entry.

The physics is the board's (`qccd.workspace.tasks.scored_device`): a number the board has is the
board's; a number it lacks -- a degree-5 junction's crossing cost -- may come from the device, and is
listed (`conformance.physics.added`); the budget is the board's, whole.

A ranking (`/official/v1/leaderboard/<board>`, `/official/v1/goals/goal1`) lists the entries that
keep every board rule with the board's physics; `?rules=all` lists every eligible entry, each with
its `conformance`, and `counts` says how many each view has.  On the website and in the Studio the
Rules choice switches between the two, and an entry under its own rules is tagged with what they
drop and add (the user's decision: the board's rules first, the others a filter away).
