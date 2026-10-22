import QCCDC.Cert.Sound

/-!
# Rules as propositions

*Part E, lesson E3. You need lessons E1 and E2, and Part R of this tutorial (the rules).*

In Part R the studio checked every programme against the rules: a site holds what it holds,
a junction takes one ion at a time, gates happen only where they may. Each rule is a statement
about a programme and its device, true or false. This lesson writes five of them as Lean
statements about lesson E2's replay, has Lean decide each one, proves that the Bell programme
keeps all five, and proves that a programme whose CX comes too early breaks exactly one.

The definitions of lessons E1 and E2 that this lesson uses are loaded in the page, as they were
written there: `Device`, `chain4`, `ring6d`, `neighbours`, `isJunction`, `Instr`, `Programme`,
`bell`, `whereIs`, `step`, `states`, `Instr.moves`, and two theorems with their proofs,
`nothing_moves` and `ions_kept`.
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
def neighbours (d : Device) (n : String) : List String := (railsAt d n).map fun r => r.1
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
def Instr.moves : Instr → Bool
  | .shuttle .. => true
  | _ => false
theorem nothing_moves (pos : Pos) (is : List Instr) (h : ∀ i ∈ is, i.moves = false) :
    ∀ s ∈ run pos is, s = pos := by
  induction is with
  | nil => simp [run]
  | cons i is ih =>
    have hi : step pos i = pos := by
      cases i <;> simp_all [Instr.moves, step]
    simp only [run, hi, List.mem_cons]
    intro s hs
    rcases hs with rfl | hs
    · rfl
    · exact ih (fun j hj => h j (List.mem_cons_of_mem _ hj)) s hs
theorem ions_kept (pos : Pos) (is : List Instr) :
    ∀ s ∈ run pos is, s.length = pos.length := by
  induction is generalizing pos with
  | nil => simp [run]
  | cons i is ih =>
    have hs : (step pos i).length = pos.length := by
      cases i <;> simp only [step]
      split <;> simp [moveTo]
    intro s hs'
    simp only [run, List.mem_cons] at hs'
    rcases hs' with rfl | hs'
    · exact hs
    · rw [ih _ s hs', hs]
def bell : Programme :=
  { arch := "chain4",
    init := [("q0", "C1"), ("q1", "C0")],
    body := [.gate "H" ["q0"] "C1", .shuttle "q0" ["C1", "C0"], .cool,
             .gate "CX" ["q0", "q1"] "C0", .measure ["q0", "q1"]] }

/-!
## 1. Statements and answers

Lean keeps two kinds of truth apart. A `Bool` is a value, `true` or `false`, that a program
computes: `2 + 2 == 4` is the `Bool` `true`. A `Prop` is a **statement**: `2 + 2 = 4` is a
`Prop`, and a proof is what makes it true. A statement may talk about every programme there will
ever be, which no program can run through; a `Bool` is always the answer of one computation.

A statement is **decidable** when there is a program that settles it, true or false, and Lean
records that program as an instance of `Decidable`. `decide p` runs it and gives the `Bool`,
and `decide +kernel`, which you used in lessons E1 and E2, proves `p` by running it in the
kernel. Lean puts the program together from the parts of the statement: `=` between strings or
numbers, `≤` between numbers, `∧`, `¬` ("not"), `→` ("implies") between decidable statements,
and `∀ x ∈ l, ...` over a list `l` (it tries every element). Every rule below is built from
those parts, so every rule is decidable. That is what lets a machine check a programme against
the rules at all.

## 2. Occupancy and R1

How many ions stand in node `n`, in a state of the replay:
-/

def occupancy (pos : Pos) (n : String) : Nat :=
  (pos.filter fun p => p.2 == n).length

/-!
Rule R1, as the platform's rule checker states it: "occupancy(site) <= site.capacity at
every instant". In Lean, over the states of E2's replay: in every state
`pos` of the programme, every node `n` of the device that is a site holds at most its capacity.
-/

def R1 (d : Device) (p : Programme) : Prop :=
  ∀ pos ∈ states p, ∀ n ∈ d.nodes, n.kind = .site → occupancy pos n.id ≤ n.capacity

/-!
`R1` is a definition, and Lean does not look inside a definition when it hunts for a decision
procedure. The `instance` line tells it to: unfold `R1`, then find (`infer_instance`) the
procedure for what is inside.
-/

instance (d : Device) (p : Programme) : Decidable (R1 d p) := by
  unfold R1; infer_instance

#eval decide (R1 chain4 bell)

/-!
The platform's R1 is stricter than this one in one way. Its replay has an instant *during*
every move, at which a site counts the ions resting there before or after the move, whichever
is more, plus every ion passing through it (a full trap is a roadblock). E2's replay has no
such instant, so this `R1` is the resting half. The programmes of this lesson pass no ion
through a trap, and for them the two agree.

#### Exercise 1 (★) `bell_R1`
Prove that the Bell programme keeps R1 on `chain4`.
-/

theorem bell_R1 : R1 chain4 bell := by
  decide +kernel

/-!
## 3. R2: one ion in a junction

R2: "at most one ion occupies any junction at any instant". A junction is what R18 says, a node
where three or more rails meet (lesson E1), so R2 reads `isJunction`, not the node's `kind`.
-/

def R2 (d : Device) (p : Programme) : Prop :=
  ∀ pos ∈ states p, ∀ n ∈ d.nodes, isJunction d n.id = true → occupancy pos n.id ≤ 1

instance (d : Device) (p : Programme) : Decidable (R2 d p) := by
  unfold R2; infer_instance

/-!
(The platform's R2 also forbids two ions crossing one junction in the same machine cycle; one
statement of E2 moves one ion, so that half has nothing to say here.)

## 4. Gates: R6 and R6b

A gate's rules need the state the gate starts in. `List.zip` pairs two lists element by
element, so `(states p).zip p.body` pairs every statement with the state before it. `gatesOf p`
keeps the gates, each with its state.
-/

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

/-!
R6: "gate / measure / cool only where the zone type has the capability". A node of E1 keeps
only whether its zone can gate, so this is R6's gate clause: every gate fires in a site that
can gate.
-/

def canGate (d : Device) (n : String) : Bool :=
  d.nodes.any fun x => x.id == n && x.gate

def R6 (d : Device) (p : Programme) : Prop :=
  ∀ g ∈ gatesOf p, canGate d g.site = true

instance (d : Device) (p : Programme) : Decidable (R6 d p) := by
  unfold R6; infer_instance

/-!
R6b: "a 2Q gate acts only on ions co-located in the same gate zone". The platform checks two
things: the two ions stand in one site when the gate starts, and, when the programme names a
site for the gate, it is that one. Our gates always name their site, so in Lean: every ion of a
two-ion gate stands, when the gate starts, in the site the gate names.
-/

def R6b (p : Programme) : Prop :=
  ∀ g ∈ gatesOf p, g.ions.length = 2 → ∀ i ∈ g.ions, whereIs g.pos i = some g.site

instance (p : Programme) : Decidable (R6b p) := by
  unfold R6b; infer_instance

/-!
## 5. A shuttle walks the rails

E2's `step` believes every path. The fifth rule says what a path must be: it starts where its
ion stands, and a rail joins every two nodes that follow each other on it. `joined` checks the
second half; its pattern `a :: b :: rest` matches a list of at least two nodes. (`head?` is the
first element of a list, if it has one.)
-/

structure ShuttleAt where
  ion : String
  path : List String
  pos : Pos

def shuttlesOf (p : Programme) : List ShuttleAt :=
  ((states p).zip p.body).flatMap fun x =>
    match x.2 with
    | .shuttle ion path => [⟨ion, path, x.1⟩]
    | _ => []

def joined (d : Device) : List String → Bool
  | a :: b :: rest => (neighbours d a).contains b && joined d (b :: rest)
  | _ => true

def Walks (d : Device) (p : Programme) : Prop :=
  ∀ s ∈ shuttlesOf p, s.path.head? = whereIs s.pos s.ion ∧ joined d s.path = true

instance (d : Device) (p : Programme) : Decidable (Walks d p) := by
  unfold Walks; infer_instance

/-!
The platform has no rule number for this one, because a programme that breaks it cannot be
replayed at all: the studio refuses a `p.shuttle` whose path has two nodes no rail joins ("no
segment between ..."), and the platform's replay stops at a move that does not start where its
ion is ("declared at ... but is at ..."). In the rule language of lesson E4 it is part of the
core rule R0, "Every operation finds its ions where it says they are". Lesson F1's checker
asks the same of a certificate's moves (`MovesAreHops`).

#### Exercise 2 (★) `bell_keeps_all`
Prove that the Bell programme keeps all five rules on `chain4`.
-/

theorem bell_keeps_all :
    R1 chain4 bell ∧ R2 chain4 bell ∧ R6 chain4 bell ∧ R6b bell ∧ Walks chain4 bell := by
  decide +kernel

/-!
## 6. Breaking exactly one

`early` is the Bell programme with its CX moved before the shuttle: the gate fires in `C0`
while `q0` is still in `C1`.
-/

def early : Programme :=
  { bell with body := [.gate "H" ["q0"] "C1",
                       .cool,
                       .gate "CX" ["q0", "q1"] "C0",
                       .shuttle "q0" ["C1", "C0"],
                       .measure ["q0", "q1"]] }

/-!
Here are the two side by side, each stopped at its CX. In `bell` (first) both ions stand in
`C0`; in `early` (second) `q0` has not left `C1`. Beside each is the studio's own rule check,
which knows nothing of the Lean above: it passes `bell`, and on `early` it flags R6b and
nothing else -- the rule Exercise 3 proves broken.

{{studio bell at=4}}

{{studio early at=3}}

#### Exercise 3 (★) `early_breaks_one`
Prove that `early` breaks R6b and keeps the other four.
-/

theorem early_breaks_one :
    R1 chain4 early ∧ R2 chain4 early ∧ R6 chain4 early ∧ ¬ R6b early ∧ Walks chain4 early := by
  decide +kernel

/-!
On `chain4` R2 never has anything to say: no node of `chain4` is a junction. On `ring6d` it
does. `S0` is a site that holds two ions, and also a junction, since three rails meet at it
(lesson E1), so it may hold only one at a time. `crowded` puts `q0` in `S0` and brings `q1` in
from the dock `A0`.
-/

def crowded : Programme :=
  { arch := "ring6d",
    init := [("q0", "S0"), ("q1", "A0")],
    body := [.shuttle "q1" ["A0", "S0"]] }

/-!
The studio flags R2 on it too, and nothing else.

{{studio crowded at=1}}

#### Exercise 4 (★) `crowded_breaks_R2`
Prove that `crowded` keeps R1 (`S0` holds two) and breaks R2.
-/

theorem crowded_breaks_R2 : R1 ring6d crowded ∧ ¬ R2 ring6d crowded := by
  decide +kernel

/-!
## 7. Rules about every programme

Deciding a rule settles it for one programme. The next facts are about all programmes at once,
and are proved from the definitions.

Here is the first, proved. `intro` names the parts of a statement you are proving: for `∀ pos ∈
states p, ...` it names `pos` and the proof that `pos` is in the list; for `A → B` it names the
proof of `A`. `rw [h n hn] at hj` rewrites `hj` with the equation `h n hn`, which turns it into
`false = true`; `contradiction` closes a goal when a hypothesis is plainly false like that.
-/

theorem R2_no_junctions (d : Device) (p : Programme)
    (h : ∀ n ∈ d.nodes, isJunction d n.id = false) : R2 d p := by
  intro pos _ n hn hj
  rw [h n hn] at hj
  contradiction

/-!
#### Exercise 5 (★★) `chain4_R2`
Every programme keeps R2 on `chain4`. Apply `R2_no_junctions`; its hypothesis is a computation.
-/

theorem chain4_R2 (p : Programme) : R2 chain4 p :=
  R2_no_junctions chain4 p (by decide +kernel)

/-!
`early` breaks R6b for a reason that has nothing to do with `chain4`: the two ions of its CX
stand in two different sites when it starts. Say that once, for every programme.

#### Exercise 6 (★★) `apart_breaks_R6b`
If a two-ion gate of a programme finds its ions in different places, the programme breaks R6b.
Hints: `intro hr`; `hr g hg (by simp [hab]) a (by simp [hab])` says `a` is in the gate's site,
and the same for `b`; two equations with the same right side contradict `h` (`ha.trans
hb.symm` is "a's place = the site = b's place").
-/

theorem apart_breaks_R6b (p : Programme) (g : GateAt) (a b : String)
    (hg : g ∈ gatesOf p) (hab : g.ions = [a, b]) (h : whereIs g.pos a ≠ whereIs g.pos b) :
    ¬ R6b p := by
  intro hr
  have ha := hr g hg (by simp [hab]) a (by simp [hab])
  have hb := hr g hg (by simp [hab]) b (by simp [hab])
  exact h (ha.trans hb.symm)

/-!
A statement about every element of an empty list is true, because there is no element to
check: "every gate fires where it may" holds of a programme with no gate. That is not a trick
of Lean; it is what "every" means, and the platform's evaluator reads `for all` the same way.

#### Exercise 7 (★★) `no_gates`
A programme with no gate keeps R6 and R6b. (`simp [R6, R6b, h]`.)
-/

theorem no_gates (d : Device) (p : Programme) (h : gatesOf p = []) : R6 d p ∧ R6b p := by
  simp [R6, R6b, h]

/-!
The last two exercises use two theorems of lesson E2, loaded in this page with their proofs:
`nothing_moves` (a programme with no shuttle leaves every state equal to the start) and
`ions_kept` (every state lists as many ions as the start). `↔` is "if and only if";
`constructor` splits it into its two directions.

#### Exercise 8 (★★★) `still_R1`
A programme that moves nothing keeps R1 exactly when its starting placement fits. Hints: for
`→`, use R1 at the first state (`p.init` is in `states p`: `by simp [states]`). For `←`, `intro
pos hpos n hn hk`; a state of `states p` is `p.init` or a state of `run p.init p.body` (`simp
only [states, List.mem_cons] at hpos`, then `rcases hpos with rfl | hpos`); in the second case
`nothing_moves` says that state is `p.init` (`rw` with it).
-/

theorem still_R1 (d : Device) (p : Programme) (hm : ∀ i ∈ p.body, i.moves = false) :
    R1 d p ↔ ∀ n ∈ d.nodes, n.kind = .site → occupancy p.init n.id ≤ n.capacity := by
  constructor
  · intro h n hn hk
    exact h p.init (by simp [states]) n hn hk
  · intro h pos hpos n hn hk
    simp only [states, List.mem_cons] at hpos
    rcases hpos with rfl | hpos
    · exact h n hn hk
    · rw [nothing_moves p.init p.body hm pos hpos]
      exact h n hn hk

/-!
Last, capacity is never a problem when every site could hold every ion: if no site is smaller
than the number of ions, R1 holds for every programme, whatever it does. Besides `ions_kept` it
needs one fact, proved here: a node holds at most every ion there is. The proof is by induction
on the state; `List.filter_cons` says what `filter` does to `p :: rest` (an `if`, which `split`
takes apart), and `omega` does the arithmetic.
-/

theorem occupancy_le (pos : Pos) (n : String) : occupancy pos n ≤ pos.length := by
  induction pos with
  | nil => simp [occupancy]
  | cons p rest ih =>
    simp only [occupancy, List.filter_cons] at *
    split <;> simp <;> omega

/-!
#### Exercise 9 (★★★) `roomy_R1`
Prove it. Hints: `intro pos hpos n hn hk`, and split `hpos` as in `still_R1`; `occupancy_le`,
`ions_kept` and `h n hn hk` give inequalities and an equation, and `omega` combines them.
-/

theorem roomy_R1 (d : Device) (p : Programme)
    (h : ∀ n ∈ d.nodes, n.kind = .site → p.init.length ≤ n.capacity) : R1 d p := by
  intro pos hpos n hn hk
  have h1 := occupancy_le pos n.id
  have h3 := h n hn hk
  simp only [states, List.mem_cons] at hpos
  rcases hpos with rfl | hpos
  · omega
  · have h2 := ions_kept p.init p.body pos hpos
    omega

/-!
## 8. What comes next

Five rules, each a decidable statement, each a few lines of Lean. The platform has more than
twenty, and different papers assume different ones. Lesson E4 is about the platform's own
answer to that: the rule language, in which a rule is one assertion, a rule set picks the rules
a machine assumes, and a verdict is one of four words.
-/
