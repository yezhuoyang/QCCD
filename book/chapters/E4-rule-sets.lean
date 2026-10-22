import QCCDC.Cert.Sound

/-!
# Rule sets

*Part E, lesson E4. You need lesson E3.*

Papers about QCCD machines do not agree on the rules. In one a trap does one gate, split or
merge at a time, in another one operation of any kind; one holds every rail and junction to one
ion at a time, another lets two chains pass. Until October 2026 this platform's rules were
fixed: twenty-odd checks written in Python, the same for every programme. Now they are an input:

* a **rule** is one assertion about the hardware and the execution, written in a small
  language, and the checker evaluates exactly that text;
* a **rule set** picks the rules a machine (or a paper) assumes and sets their parameters;
* the compiler takes a rule set (`qccdc ... --rules <file>`), and what it produces is judged by
  every rule of the set;
* every verdict is one of four words.

The site's [Rules page](../rules/) describes the language and lists every rule and rule set.
This lesson
reads real rules from it, translates some into Lean over the replay of lessons E2 and E3, and
models a rule set: what its verdicts mean, and two facts every rule set obeys.

The definitions of lessons E1-E3 that this lesson uses are loaded in the page, as they were
written there: devices, `chain4`, `ring6d`, `isJunction`, programmes, `bell`, `early`,
`whereIs`, `states`, `occupancy`, `gatesOf` and `canGate`.
-/

-- setup
inductive Kind where
  | site
  | junction
  deriving DecidableEq, Repr
structure Node where
  id : String
  kind : Kind
  capacity : Nat
  gate : Bool
  deriving Repr
structure Segment where
  id : String
  ends : String × String
  deriving Repr
structure Device where
  name : String
  nodes : List Node
  segments : List Segment
  deriving Repr
def chain4 : Device :=
  { name := "chain4",
    nodes := [⟨"C0", .site, 2, true⟩, ⟨"C1", .site, 2, true⟩,
              ⟨"C2", .site, 2, true⟩, ⟨"C3", .site, 2, true⟩],
    segments := [⟨"E0", ("C0", "C1")⟩, ⟨"E1", ("C1", "C2")⟩, ⟨"E2", ("C2", "C3")⟩] }
def ring6d : Device :=
  { name := "ring6d",
    nodes := [⟨"S0", .site, 2, false⟩, ⟨"S1", .site, 2, false⟩, ⟨"S2", .site, 2, false⟩,
              ⟨"S3", .site, 2, false⟩, ⟨"S4", .site, 2, false⟩, ⟨"S5", .site, 2, false⟩,
              ⟨"A0", .site, 2, true⟩, ⟨"A3", .site, 2, true⟩],
    segments := [⟨"E0", ("S0", "S1")⟩, ⟨"E1", ("S1", "S2")⟩, ⟨"E2", ("S2", "S3")⟩,
                 ⟨"E3", ("S3", "S4")⟩, ⟨"E4", ("S4", "S5")⟩, ⟨"E5", ("S5", "S0")⟩,
                 ⟨"V0", ("S0", "A0")⟩, ⟨"V3", ("S3", "A3")⟩] }
def railsAt (d : Device) (n : String) : List (String × String) :=
  d.segments.flatMap fun s =>
    (if s.ends.1 == n then [(s.ends.2, s.id)] else []) ++
    (if s.ends.2 == n then [(s.ends.1, s.id)] else [])
def degree (d : Device) (n : String) : Nat := (railsAt d n).length
def isJunction (d : Device) (n : String) : Bool := degree d n ≥ 3
inductive Instr where
  | shuttle (ion : String) (path : List String)
  | gate (name : String) (ions : List String) (site : String)
  | cool
  | measure (ions : List String)
  deriving Repr
structure Programme where
  arch : String
  init : List (String × String)
  body : List Instr
  deriving Repr
def quote (s : String) : String := "\"" ++ s ++ "\""
def strs (xs : List String) : String := "[" ++ ", ".intercalate (xs.map quote) ++ "]"
def stmt : Instr → String
  | .shuttle ion path => "p.shuttle(" ++ quote ion ++ ", " ++ strs path ++ ")"
  | .gate name [_] site => "p.gate(" ++ quote name ++ ", [], [" ++ quote site ++ "])"
  | .gate name ions site => "p.gate(" ++ quote name ++ ", [" ++ strs ions ++ "], [" ++ quote site ++ "])"
  | .cool => "p.cool()"
  | .measure ions => "p.measure(" ++ strs ions ++ ")"
def initStmt (init : List (String × String)) : String :=
  "p.init({" ++ ", ".intercalate (init.map fun p => quote p.1 ++ ": " ++ quote p.2) ++ "})"
def Programme.src (p : Programme) : String :=
  "\n".intercalate (initStmt p.init :: p.body.map stmt)
abbrev Pos := List (String × String)
def whereIs (pos : Pos) (ion : String) : Option String :=
  match pos with
  | [] => none
  | (i, n) :: rest => if i == ion then some n else whereIs rest ion
def moveTo (pos : Pos) (ion node : String) : Pos :=
  pos.map fun p => if p.1 == ion then (p.1, node) else p
def step (pos : Pos) : Instr → Pos
  | .shuttle ion path =>
    match path.getLast? with
    | some dst => moveTo pos ion dst
    | none => pos
  | _ => pos
def run (pos : Pos) : List Instr → List Pos
  | [] => []
  | i :: is => step pos i :: run (step pos i) is
def states (p : Programme) : List Pos := p.init :: run p.init p.body
def bell : Programme :=
  { arch := "chain4",
    init := [("q0", "C1"), ("q1", "C0")],
    body := [.gate "H" ["q0"] "C1", .shuttle "q0" ["C1", "C0"], .cool,
             .gate "CX" ["q0", "q1"] "C0", .measure ["q0", "q1"]] }
def early : Programme :=
  { bell with body := [.gate "H" ["q0"] "C1", .cool, .gate "CX" ["q0", "q1"] "C0",
                       .shuttle "q0" ["C1", "C0"], .measure ["q0", "q1"]] }
def occupancy (pos : Pos) (n : String) : Nat :=
  (pos.filter fun p => p.2 == n).length
structure GateAt where
  name : String
  ions : List String
  site : String
  pos : Pos
def gatesOf (p : Programme) : List GateAt :=
  ((states p).zip p.body).flatMap fun x =>
    match x.2 with
    | .gate name ions site => [⟨name, ions, site, x.1⟩]
    | _ => []
def canGate (d : Device) (n : String) : Bool :=
  d.nodes.any fun x => x.id == n && x.gate

/-!
## 1. A rule, as the platform writes it

Every rule the website knows is written once, in the rule language's **catalogue**. Here
is R1 from it, exactly:

```
rule R1 "A site holds at most its capacity" core
  about "An ion standing in a site, or passing through it, takes one of its places."
  source "deck_v3; arXiv:2004.04706; arXiv:2511.15910"
  for all s in sites, t in instants:
    occupancy(s, t) <= capacity(s)
```

A rule has an id, a title, a sentence about what it means, its sources, and one **formula**.
The formula is logic over finite collections -- the sites of the device, the instants of the
execution, its operations -- built from `for all x in D where C: B`, `for some`, `and`, `or`,
`not`, `implies`, comparisons, `count(...)`, `sum(...)` and a few more. Its words (`sites`,
`instants`, `occupancy`, `capacity`) come from a fixed vocabulary ([the words](../rules/#words)),
and every word has a definition in words and a reading for each kind of execution: a programme
(the replay's instants are before each step, during each step that moves ions, and the end) or
a paper's timed schedule. A formula may start with `when C:`, a precondition. `core` marks the
four rules every rule set has (R0, R1, R8 and R24).

R6b, from the same file:

```
rule R6b "A two-ion gate acts on two ions standing in one site"
  about "Both ions of a two-qubit gate are in the same trap when it starts, and in the trap the programme names for it, if it names one."
  source "arXiv:2504.17886"
  (for all g in two_qubit_gates, i in ions(g): position(i, start(g)) = site(g))
  and (for all g in two_qubit_gates where size(declared_sites(g)) > 0:
         site(g) in declared_sites(g))
```

Compare lesson E3's `R6b`: `∀ g ∈ gatesOf p, g.ions.length = 2 → ∀ i ∈ g.ions, whereIs g.pos i
= some g.site`. The rule language's `site(g)` is where the gate's ions stand when it starts,
and the second line checks the site the programme names; E3's gates always name their site, so
there the two lines become one.

## 2. Four verdicts

The rule language's evaluator answers one of four words for a rule:

* **holds**;
* **fails**, with the counterexamples: the values that make the formula false;
* **does not apply**, with why: the rule's `when` is false, or it uses a word the execution
  does not have (an execution given with no input circuit has no `circuit_ops`);
* **could not be checked**: a value the rule needs is missing.

A rule never holds by not being evaluated: a programme passes a rule set when every rule that
applies holds and none could not be checked. In Lean the verdicts are an inductive type, and
`word` gives the platform's word for each.
-/

inductive Verdict where
  | holds
  | fails (examples : List String)
  | doesNotApply (why : String)
  | couldNotCheck (why : String)
  deriving Repr

def Verdict.word : Verdict → String
  | .holds => "holds"
  | .fails _ => "fails"
  | .doesNotApply _ => "does not apply"
  | .couldNotCheck _ => "could not be checked"

/-- The two verdicts a passing programme may get. -/
def Verdict.fine : Verdict → Bool
  | .holds => true
  | .doesNotApply _ => true
  | _ => false

/-!
## 3. A rule in Lean

A `for all` formula, evaluated on one programme, is a list of **cases**, one for each value of
its variables: a description of the case, and the value of the body there -- `some true`,
`some false`, or `none` when a value the body needs is missing (`Option Bool` has those three
values). A Lean `Rule` is its id, its title, whether it is core, when it applies (with the
reason it does not), and its cases. A field written with `:=` has a default, used when a rule
leaves it out.
-/

structure Rule where
  id : String
  title : String
  core : Bool := false
  applies : Device → Programme → Bool := fun _ _ => true
  why : String := ""
  cases : Device → Programme → List (String × Option Bool)

/-!
What it means for a rule to hold, as a statement: if it applies, the body is true in every
case. A rule that does not apply holds, vacuously; a rule with a missing value does not.
-/

def Rule.Holds (r : Rule) (d : Device) (p : Programme) : Prop :=
  r.applies d p = true → ∀ c ∈ r.cases d p, c.2 = some true

instance (r : Rule) (d : Device) (p : Programme) : Decidable (r.Holds d p) := by
  unfold Rule.Holds; infer_instance

/-!
And the verdict, computed. `tally` reads the cases in order: one missing value makes the rule
uncheckable, as an evaluation error does in the platform's evaluator; otherwise every false
case is a counterexample; otherwise it holds.
-/

def tally : List (String × Option Bool) → Verdict
  | [] => .holds
  | (what, none) :: _ => .couldNotCheck what
  | (what, some false) :: rest =>
    match tally rest with
    | .couldNotCheck w => .couldNotCheck w
    | .fails ws => .fails (what :: ws)
    | _ => .fails [what]
  | (_, some true) :: rest => tally rest

def judge (r : Rule) (d : Device) (p : Programme) : Verdict :=
  if r.applies d p then tally (r.cases d p) else .doesNotApply r.why

/-!
## 4. Translations

R1, over E2's replay: one case per state and site. (As in lesson E3, this is the resting half of
R1: E2's replay has no instant during a move.)
-/

def r1 : Rule :=
  { id := "R1", title := "A site holds at most its capacity", core := true,
    cases := fun d p =>
      (states p).flatMap fun pos =>
        (d.nodes.filter fun n => n.kind == .site).map fun n =>
          (n.id ++ " holds " ++ toString (occupancy pos n.id) ++ " of " ++ toString n.capacity,
           some (decide (occupancy pos n.id ≤ n.capacity))) }

/-!
R2 is `for all j in junctions, t in instants: occupancy(j, t) <= 1`, and R6's gate clause
`for all g in gates: can(site(g), "gate")`.
-/

def r2 : Rule :=
  { id := "R2", title := "A junction holds one ion at a time",
    cases := fun d p =>
      (states p).flatMap fun pos =>
        (d.nodes.filter fun n => isJunction d n.id).map fun n =>
          (n.id ++ " holds " ++ toString (occupancy pos n.id), some (decide (occupancy pos n.id ≤ 1))) }

def r6 : Rule :=
  { id := "R6", title := "Each operation runs only where the site is equipped for it",
    cases := fun d p => (gatesOf p).map fun g => (g.name ++ " in " ++ g.site, some (canGate d g.site)) }

/-!
R6b, one case per ion of every two-ion gate. `getD` gives an `Option`'s value, or a default
when it is `none`.
-/

def r6b : Rule :=
  { id := "R6b", title := "A two-ion gate acts on two ions standing in one site",
    cases := fun _ p =>
      ((gatesOf p).filter fun g => g.ions.length == 2).flatMap fun g =>
        g.ions.map fun i =>
          (g.name ++ " in " ++ g.site ++ ": " ++ i ++ " is in " ++ (whereIs g.pos i).getD "no site",
           some (whereIs g.pos i == some g.site)) }

/-!
Three more show the other verdicts. R7 reads each ion's heat when a two-ion gate starts:

```
rule R7 "A two-ion gate starts with cool enough ions"
  for all g in two_qubit_gates, i in ions(g):
    heat(i, start(g)) <= heat_budget()
```

The platform's replay records that heat. E2's replay keeps no heat at all, so here the value is
missing in every case: the rule could not be checked -- unless the programme has no two-ion gate,
when there is no case and it holds.
-/

def r7 : Rule :=
  { id := "R7", title := "A two-ion gate starts with cool enough ions",
    cases := fun _ p =>
      ((gatesOf p).filter fun g => g.ions.length == 2).flatMap fun g =>
        g.ions.map fun i => ("the heat of " ++ i ++ " when " ++ g.name ++ " starts", none) }

/-!
R7c has a precondition:

```
rule R7c "A programme with two-ion gates cools"
  when models_heating():
  count(two_qubit_gates) = 0 or count(coolings) > 0
```

`models_heating()` asks whether the clock tracks how hot each ion gets. The platform's default
clock does, so there R7c applies to every programme; E2's replay has no clock, so here it never
does. (Its formula has no `for all`: it is one case.)
-/

def r7c : Rule :=
  { id := "R7c", title := "A programme with two-ion gates cools",
    applies := fun _ _ => false, why := "it applies only when models_heating()",
    cases := fun _ p =>
      [("the programme", some ((gatesOf p).all (fun g => g.ions.length != 2) ||
                              p.body.any fun i => match i with | .cool => true | _ => false))] }

/-!
R24 is core, and says that every two-qubit gate of the input circuit is performed, and no other.
It reads `circuit_ops`, and E2's programmes carry no circuit, so it does not apply to them, for
the reason the platform gives ("no input circuit was given with this execution").
-/

def r24 : Rule :=
  { id := "R24", title := "Every two-qubit gate of the circuit is performed, and no other",
    core := true,
    applies := fun _ _ => false, why := "no input circuit was given with this execution",
    cases := fun _ _ => [] }

/-!
## 5. Judging a programme

`catalogue` is every rule this lesson translated; `report` judges a programme by a list of rules,
one word per rule; `ok` is the pass mark (every verdict fine).
-/

def catalogue : List Rule := [r1, r2, r6, r6b, r7, r7c, r24]

def report (rs : List Rule) (d : Device) (p : Programme) : List (String × String) :=
  rs.map fun r => (r.id, (judge r d p).word)

def ok (rs : List Rule) (d : Device) (p : Programme) : Bool :=
  rs.all fun r => (judge r d p).fine

#eval report catalogue chain4 bell

/-!
`early` (lesson E3) fails R6b, and the verdict carries the counterexample, naming the values
that make the formula false:
-/

#eval judge r6b chain4 early

/-!
#### Exercise 1 (★) `bell_report`
Prove the Bell programme's report under `catalogue`. All four words appear.
-/

theorem bell_report :
    report catalogue chain4 bell =
      [("R1", "holds"), ("R2", "holds"), ("R6", "holds"), ("R6b", "holds"),
       ("R7", "could not be checked"), ("R7c", "does not apply"), ("R24", "does not apply")] := by
  decide +kernel

/-!
## 6. Rule sets

A rule set is a file too. The website's own, the [standard rule set](../rules/#set-standard),
uses every rule of the catalogue
that is about a programme, a device drawing or the compiler's claims -- 31 rules, R0 to R25 --
and sets `max_chain = 15`. Each paper on the Reproduce pages has its own; here is Cyclone's,
exactly:

```
ruleset "cyclone"
title "Cyclone's rules"
about "A trap does one gate, split or merge at a time; a rail and a junction hold one ion at a time; the compiler keeps a list of ions per trap, not a chain, so no chain order is tracked; every operation lasts what Cyclone's duration law says; each qubit's gates run in the circuit's order, except that consecutive CNOTs in which it plays the same role may run in any order."

use R0, R1, R2, R3, R6b, R8, R24, R26, R27, R29
param serial_kinds = {"gate2", "split", "merge"}
```

`use` picks rules of the catalogue by id, `param` sets a parameter a rule reads, `include` starts
from another rule set, and `drop` removes a rule. The core rules are in every set: loading a set
adds them, and `drop` refuses to remove one. In Lean, over our catalogue (`useRules` keeps the
core rules and the ones named; `dropRule` keeps the core rules and every rule but the one named,
where the platform would refuse):
-/

def useRules (cat : List Rule) (ids : List String) : List Rule :=
  cat.filter fun r => r.core || ids.contains r.id

def dropRule (rs : List Rule) (id : String) : List Rule :=
  rs.filter fun r => r.core || r.id != id

/-- The standard set, as far as this lesson has translated it: every rule of `catalogue`. -/
def standard : List Rule := catalogue

def cyclone : List Rule :=
  useRules catalogue ["R0", "R1", "R2", "R3", "R6b", "R8", "R24", "R26", "R27", "R29"]

#eval cyclone.map fun r => r.id

/-!
Cyclone's set has no R6: under it, a gate may fire anywhere. `loopGate` fires a one-ion gate in
`S1`, a slot of `ring6d`'s loop, whose zone cannot gate. The studio, which judges by its own
fixed rules, flags R6 on it.
-/

def loopGate : Programme :=
  { arch := "ring6d", init := [("q0", "S1")], body := [.gate "H" ["q0"] "S1"] }

#eval report standard ring6d loopGate
#eval report cyclone ring6d loopGate

/-!
{{studio loopGate at=1}}

#### Exercise 2 (★) `two_sets`
Prove that `loopGate` fails the standard set and passes Cyclone's.
-/

theorem two_sets : ok standard ring6d loopGate = false ∧ ok cyclone ring6d loopGate = true := by
  decide +kernel

/-!
## 7. The compiler takes a rule set

`qccdc compile <circuit> --arch <device> -o <out> --rules <file>` makes the compiler plan with a
rule set instead of the standard one. It is given the set resolved into one file, every rule
written out (`python -m qccd.ruleset show <set>` prints it). The compiler reads two things from
it: R13's `max_chain` bounds a gate's chain (without R13 only
the trap's capacity does), and without R22 a transport cycle may carry unlike motions.
Everything else it obeys whatever the set says, since a programme that keeps stricter rules
keeps weaker ones. Then the programme is judged by **every** rule of the set, including rules
the compiler has never heard of: `python -m qccd.ruleset compile <circuit> --arch <device> -o
<out> --rules <set>` does both, and `python -m qccd.ruleset check <programme> --arch <device>
--rules <set>` judges a programme you already have.

The compiler's shortcut -- keep the strict rules, and every weaker set is kept too -- rests on a
fact about rule sets: a programme that keeps a set keeps every smaller set, and adding a rule
can only turn a pass into a failure. Here it is in Lean.
-/

def Satisfies (rs : List Rule) (d : Device) (p : Programme) : Prop :=
  ∀ r ∈ rs, r.Holds d p

instance (rs : List Rule) (d : Device) (p : Programme) : Decidable (Satisfies rs d p) := by
  unfold Satisfies; infer_instance

/-!
## 8. Facts about every rule set

A proof of `∀ r ∈ s, ...` is a function: given `r` and a proof that `r ∈ s`, it returns a proof
about `r`. `fun r hr => ...` writes one in place.

#### Exercise 3 (★★) `satisfies_subset`
If every rule of `s` is a rule of `t`, a programme that satisfies `t` satisfies `s`. (One line:
`fun r hr => h r (hst r hr)`.)
-/

theorem satisfies_subset (s t : List Rule) (d : Device) (p : Programme)
    (hst : ∀ r ∈ s, r ∈ t) (h : Satisfies t d p) : Satisfies s d p :=
  fun r hr => h r (hst r hr)

/-!
#### Exercise 4 (★★) `more_rules_more_fails`
Adding rules can only turn a pass into a failure, never a failure into a pass: if a programme
does not satisfy `s`, it does not satisfy `s ++ t`. Hint: `intro hst`, then use `h` on a proof
that the programme satisfies `s` (a rule of `s` is a rule of `s ++ t`: `by simp [hr]`).
-/

theorem more_rules_more_fails (s t : List Rule) (d : Device) (p : Programme)
    (h : ¬ Satisfies s d p) : ¬ Satisfies (s ++ t) d p := by
  intro hst
  exact h (fun r hr => hst r (by simp [hr]))

/-!
#### Exercise 5 (★★) `drop_keeps_core`
`dropRule` never removes a core rule. (`simp [dropRule, hr, hc]`: membership in a `filter` is
membership in the list and the test being `true`.)
-/

theorem drop_keeps_core (rs : List Rule) (id : String) (r : Rule)
    (hr : r ∈ rs) (hc : r.core = true) : r ∈ dropRule rs id := by
  simp [dropRule, hr, hc]

/-!
## 9. The judge is right

`judge` computes a word; `Rule.Holds` is a statement. Are they the same thing? A checker is only
worth what that question's answer is worth. Here it is answered for every rule and every
programme, in three steps: `tally` says fine only when every case is true (sound), it says
holds whenever every case is true (complete), and so a verdict is fine exactly when the rule
holds. Part F asks the same question of the certificate checker.

The two exercises below are the two steps that need induction; the third step, after them, is
proved for you.

#### Exercise 6 (★★★) `tally_fine`
If `tally` gives a fine verdict, every case is `some true`. Hints: induction on `cs`; take the
first case apart with `obtain ⟨what, v⟩ := c` and split on its value with `cases v with | none
=> ... | some b => ...`, then `cases b`. A `none` makes `tally` say could not be checked, which
is not fine (`simp [tally, Verdict.fine] at h`); a `some false` makes it fail or be uncheckable
(`simp only [tally] at h`, then `split at h <;> simp [Verdict.fine] at h`); a `some true` passes
the question to the rest, where `ih` answers it.
-/

theorem tally_fine (cs : List (String × Option Bool)) (h : (tally cs).fine = true) :
    ∀ c ∈ cs, c.2 = some true := by
  induction cs with
  | nil => simp
  | cons c rest ih =>
    obtain ⟨what, v⟩ := c
    cases v with
    | none => simp [tally, Verdict.fine] at h
    | some b =>
      cases b with
      | false =>
        simp only [tally] at h
        split at h <;> simp [Verdict.fine] at h
      | true =>
        simp only [tally] at h
        intro c hc
        simp only [List.mem_cons] at hc
        rcases hc with rfl | hc
        · rfl
        · exact ih h c hc

/-!
#### Exercise 7 (★★★) `tally_complete`
If every case is `some true`, `tally` says holds. Hints: induction on `cs`; in the `cons` case the
first case's value is `some true` by `h` (`have hv : v = some true := h (what, v) (by simp)`),
`subst hv` puts it in place, `simp only [tally]` takes one step, and `ih` finishes.
-/

theorem tally_complete (cs : List (String × Option Bool)) (h : ∀ c ∈ cs, c.2 = some true) :
    tally cs = .holds := by
  induction cs with
  | nil => rfl
  | cons c rest ih =>
    obtain ⟨what, v⟩ := c
    have hv : v = some true := h (what, v) (by simp)
    subst hv
    simp only [tally]
    exact ih (fun c hc => h c (List.mem_cons_of_mem _ hc))

/-!
With the two, the judge is right: a verdict is fine exactly when the rule holds. Here is the
proof. `constructor` splits `↔` into its two directions. One way, `unfold judge at h` and `rw
[if_pos ha] at h` leave a statement about `tally`, which `tally_fine` answers; the other way,
`unfold judge` and `split` give one goal per branch of the `if`: where the rule applies,
`tally_complete` turns the verdict into `.holds`; where it does not, the verdict is
`.doesNotApply`, fine by `rfl`. (`next ha =>` names the hypothesis the branch comes with.)
-/

theorem judge_right (r : Rule) (d : Device) (p : Programme) :
    (judge r d p).fine = true ↔ r.Holds d p := by
  constructor
  · intro h ha
    unfold judge at h
    rw [if_pos ha] at h
    exact tally_fine _ h
  · intro h
    unfold judge
    split
    · next ha => rw [tally_complete _ (h ha)]; rfl
    · rfl

/-!
## 10. What comes next

Part E has written the machine as data: devices (E1), programmes and their replay (E2), rules
as decidable statements (E3), and rule sets with verdicts proved to mean what they say (E4).
Part F turns to certificates: what a compiler hands the proved checker, what it means for one
to hold, and the proof that the checker's yes is right. Start with
[lesson F1](2.1-certificates.html).
-/
