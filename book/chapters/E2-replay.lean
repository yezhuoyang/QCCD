import QCCDC.Cert.Sound

/-!
# Programmes and replay

*Part E, lesson E2. You need lesson E1.*

In the studio a programme is a list of statements: `p.init` places the ions,
`p.shuttle` carries one along a path of nodes, `p.gate` fires a gate, `p.cool` cools every ion,
`p.measure` reads ions out. This lesson writes that language as a Lean type, writes the Bell
pair in it, computes the studio's text from it (so the figure below is drawn from your Lean),
and writes the **replay**: where every ion is after every statement. Then it proves things
about the replay, first for the Bell pair and then for every programme.
-/

/-!
## 1. Statements as data

In lesson E1 an `inductive` type listed plain values. Its values can also carry data: each
line below is one kind of statement and the fields it carries. An `Instr` is exactly one of
the four, with its fields.

* `shuttle ion path`: carry `ion` along `path`, a list of nodes, to the last one.
* `gate name ions site`: fire the gate `name` on `ions` (one ion or two), which stand in `site`.
* `cool`: cool every ion.
* `measure ions`: read those ions out.
-/

inductive Instr where
  | shuttle (ion : String) (path : List String)
  | gate (name : String) (ions : List String) (site : String)
  | cool
  | measure (ions : List String)
  deriving Repr

/-!
A programme names its device, places its ions (the `p.init` statement, kept as a list of
pairs ion, node), and lists its statements.
-/

structure Programme where
  arch : String
  init : List (String × String)
  body : List Instr
  deriving Repr

/-!
The Bell pair on `chain4`, with its ions where the compiler placed them for the Compilation
page and its statements in the order lesson F1's figure plays its certificate: `q0` starts in
`C1` and `q1` in `C0`; the Hadamard fires on
`q0` where it stands, `q0` moves next door, every ion is cooled, the CX fires in `C0`, and both
ions are measured. `.gate` is short for `Instr.gate`.
-/

def bell : Programme :=
  { arch := "chain4",
    init := [("q0", "C1"), ("q1", "C0")],
    body := [.gate "H" ["q0"] "C1",
             .shuttle "q0" ["C1", "C0"],
             .cool,
             .gate "CX" ["q0", "q1"] "C0",
             .measure ["q0", "q1"]] }

/-!
#### Exercise 1 (★) `bell_has_five`
Prove that the Bell programme has five statements after `p.init`.
-/

theorem bell_has_five : bell.body.length = 5 := rfl

/-!
## 2. The studio's text, computed

A function on an inductive type is written by pattern matching, one line per kind of
statement. A pattern can look inside the fields: `.gate name [_] site` matches a gate whose
list of ions has exactly one element (`_` is "anything"), and the line after it catches every
other gate. The studio names a one-ion gate by its site, `p.gate("H", [], ["C1"])`; a two-ion
gate by its pair and, here, the site it is said to fire in, which the studio checks.
-/

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

/-!
`Programme.src` is a function of a programme, and Lean lets you call it as `bell.src`, as if
it were a field. `"\n"` is a line break; `IO.println` prints a string as text.
-/

def Programme.src (p : Programme) : String :=
  "\n".intercalate (initStmt p.init :: p.body.map stmt)

#eval IO.println bell.src

/-!
The page asks Lean for `bell.arch` and `bell.src` and plays them in the studio. Press Play.
Then edit `bell` above -- start `q1` in `C2`, or leave out the shuttle -- and the figure
follows, with the studio's own rule check beside it.

{{studio bell}}

## 3. Replay

Where is every ion? The state of the machine is a list of pairs ion, node, like `init`.
`abbrev` gives a type a second name.
-/

abbrev Pos := List (String × String)

/-!
`whereIs pos ion` looks an ion up. Its answer has type `Option String`: either `some n`, the
node, or `none`, when the ion is not in the list. It is written by pattern matching on the
list: an empty list (`[]`) has no ion in it; a list `(i, n) :: rest` starts with the pair
`(i, n)`.
-/

def whereIs (pos : Pos) (ion : String) : Option String :=
  match pos with
  | [] => none
  | (i, n) :: rest => if i == ion then some n else whereIs rest ion

/-!
`moveTo pos ion node` is `pos` with `ion` at `node` and every other ion where it was. A shuttle
moves its ion to the last node of its path (`getLast?` is the last element of a list, if it
has one); nothing else moves an ion.
-/

def moveTo (pos : Pos) (ion node : String) : Pos :=
  pos.map fun p => if p.1 == ion then (p.1, node) else p

def step (pos : Pos) : Instr → Pos
  | .shuttle ion path =>
    match path.getLast? with
    | some dst => moveTo pos ion dst
    | none => pos
  | _ => pos

/-!
`step` believes the path it is given: it does not check that the path starts where the ion
is, nor that rails join its nodes, nor that the gates fire where their ions are. Those are
**rules**, and lesson E3 states them about this replay.

The replay is `step`, statement after statement. `run pos is` lists the state after each
statement of `is`, starting from `pos`; `states p` puts the starting state in front, so a
programme of five statements has six states. `final p` is the state at the end, computed with
`foldl`, which walks a list from the left carrying a value along: here the state, through
`step`.
-/

def run (pos : Pos) : List Instr → List Pos
  | [] => []
  | i :: is => step pos i :: run (step pos i) is

def states (p : Programme) : List Pos := p.init :: run p.init p.body

def final (p : Programme) : Pos := p.body.foldl step p.init

#eval states bell
#eval final bell

/-!
The studio is finer than this replay: it carries an ion one trap per machine cycle (a
`p.shuttle` along three traps is two cycles), and the platform's rules also look at the
instant during each move. Our replay keeps one state per statement, which is enough for every
programme in this part; lesson E3 says where the difference would show.

#### Exercise 2 (★) `q0_ends_in_C0`
Prove that at the end of the Bell programme `q0` is in `C0`. (It computes: `decide +kernel`,
as in lesson E1.)
-/

theorem q0_ends_in_C0 : whereIs (final bell) "q0" = some "C0" := by
  decide +kernel

/-!
The compiler chose to move `q0` to `q1`. It could have moved `q1` to `q0` instead, and fired the
CX in `C1`. `bellThere` is that programme; it is not on the Compilation page, it is yours. Its
figure is drawn the same way.
-/

def bellThere : Programme :=
  { bell with body := [.gate "H" ["q0"] "C1",
                       .shuttle "q1" ["C0", "C1"],
                       .cool,
                       .gate "CX" ["q0", "q1"] "C1",
                       .measure ["q0", "q1"]] }

/-!
{{studio bellThere}}

#### Exercise 3 (★) `there_both_in_C1`
Prove that at the end of `bellThere` both ions are in `C1`.
-/

theorem there_both_in_C1 :
    whereIs (final bellThere) "q0" = some "C1" ∧ whereIs (final bellThere) "q1" = some "C1" := by
  decide +kernel

/-!
## 4. Proofs about every programme

The theorems so far computed. The next ones hold for every programme, and a computation cannot
check every programme: they need proofs that follow the definitions.

A list is either `[]` or `i :: is`. To prove something for every list, prove it for `[]`, then
prove it for `i :: is` assuming it for `is`; the assumption is called the **induction
hypothesis** (`ih`). That is `induction is with | nil => ... | cons i is ih => ...`.
`generalizing pos` makes the hypothesis hold for every starting state, which `run` needs, since
it starts the rest of the list from a new state.

#### Exercise 4 (★★) `run_length`
The replay has one state per statement. Hint: `induction is generalizing pos with`, then `rfl`
for `nil` and `simp [run, ih]` for `cons`.
-/

theorem run_length (pos : Pos) (is : List Instr) : (run pos is).length = is.length := by
  induction is generalizing pos with
  | nil => rfl
  | cons i is ih => simp [run, ih]

/-!
Here is a proof by induction on the state, written out. It says: if `ion` is somewhere in
`pos`, then after `moveTo pos ion node` it is at `node`. At `[]` the hypothesis `h` is false
(no ion is in the empty list), and `simp` says so. At `(i, n) :: rest`, either `i` is `ion`,
and the first pair is the one that moved, or it is not, and the induction hypothesis speaks
about `rest`. `obtain ⟨i, n⟩ := p` names the two halves of the first pair; `by_cases hi : i =
ion` splits the proof in two; `simp_all` simplifies every hypothesis and the goal with each
other and the definitions given.
-/

theorem moveTo_self (pos : Pos) (ion node : String) (h : whereIs pos ion ≠ none) :
    whereIs (moveTo pos ion node) ion = some node := by
  induction pos with
  | nil => simp [whereIs] at h
  | cons p rest ih =>
    obtain ⟨i, n⟩ := p
    by_cases hi : i = ion <;> simp_all [moveTo, whereIs]

/-!
#### Exercise 5 (★★) `shuttle_lands`
A shuttle leaves its ion at the end of its path, if the ion was placed. Hint: `simp only
[step, hl]` replaces `path.getLast?` by `some dst` inside `step`; then apply `moveTo_self`.
-/

theorem shuttle_lands (pos : Pos) (ion dst : String) (path : List String)
    (h : whereIs pos ion ≠ none) (hl : path.getLast? = some dst) :
    whereIs (step pos (.shuttle ion path)) ion = some dst := by
  simp only [step, hl]
  exact moveTo_self pos ion dst h

/-!
#### Exercise 6 (★★★) `only_its_ion`
A shuttle moves only the ion it names: every other ion is where it was. Hints: `simp only
[step]`, then `split`, one goal for a path with a last node and one for the empty path (that
one is `rfl`). The first is about `moveTo`: prove it by induction on the state, as
`moveTo_self` is proved, this time splitting on both `i = ion` and `i = other`.
-/

theorem only_its_ion (pos : Pos) (ion other : String) (path : List String) (h : other ≠ ion) :
    whereIs (step pos (.shuttle ion path)) other = whereIs pos other := by
  simp only [step]
  split
  · induction pos with
    | nil => rfl
    | cons p rest ih =>
      obtain ⟨i, n⟩ := p
      by_cases hi : i = ion <;> by_cases ho : i = other <;> simp_all [moveTo, whereIs]
  · rfl

/-!
`Instr.moves i` says whether a statement can move an ion: only a shuttle can. (`.shuttle ..`
matches a shuttle whatever its fields.)
-/

def Instr.moves : Instr → Bool
  | .shuttle .. => true
  | _ => false

/-!
#### Exercise 7 (★★★) `nothing_moves`
A programme with no shuttle leaves every ion where it started: every state of its replay is the
starting state. Hints: induction on `is` (no need to generalise `pos`: it never changes). In the
`cons` case, first prove `have hi : step pos i = pos` by `cases i <;> simp_all [Instr.moves,
step]` (one goal per kind of statement); then `simp only [run, hi, List.mem_cons]`, `intro s
hs`, and split `hs` with `rcases hs with rfl | hs`: either `s` is the first state, or it is in
the rest, where `ih` applies (`List.mem_cons_of_mem` passes the hypothesis on to the rest of
the list).
-/

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

/-!
One more fact about every programme: the replay never makes or loses an ion. Every state lists
as many ions as the one the replay started from. Lesson E3 builds on it.

#### Exercise 8 (★★★) `ions_kept`
Hints: induction on `is`, generalizing `pos`. In the `cons` case first prove `have hs : (step
pos i).length = pos.length`: `cases i <;> simp only [step]` leaves the shuttle, where `split <;>
simp [moveTo]` finishes (`moveTo` is a `map`, and a `map` keeps the length). Then take `s ∈ step
pos i :: run (step pos i) is` apart as in `nothing_moves`; in the rest, `ih` and `hs` give the
two equations to `rw` with.
-/

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

/-!
## 5. What comes next

A programme is a value, its replay a function, and facts about every programme are theorems.
Lesson E3 states the rules as propositions about this replay -- a site holds at most its
capacity, a two-ion gate's ions stand together, a shuttle walks the rails -- proves the Bell
programme keeps them, and proves a broken one breaks exactly one.
-/
