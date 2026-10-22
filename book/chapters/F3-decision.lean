import QCCDC.Cert.Sound

/-!
# Decision procedures

*Part F, lesson F3. You need lessons F1 and F2.*

The checker is one line, `check inp := decide (Implements inp)`. Everything it computes is hidden
in that `decide`: the decision procedures of the eleven conditions, which Lean finds and assembles
by itself. This lesson opens them. What a decision procedure is in Lean, the four ways of running
one and what each of them trusts, why the checker carries its own procedure for permutations
rather than the one Lean ships, and how the replay comes to be computed once per check. You will
also write the decision procedure of a condition the checker does not have.
-/

open QCCDC.Cert

-- setup
def bell : Input :=
  { circuit := "bell", arch := "chain4", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "C1"), ("q1", "C0")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [1]⟩],
    moves := [⟨3, "q0", "C1", "C0"⟩],
    rots := [],
    gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

/-!
## 1. What `Decidable` is

A proposition (`Prop`) is a statement; it need not come with a way to find out whether it holds.
`Decidable p` is a way: a value that is either `isTrue h`, carrying a proof `h` of `p`, or
`isFalse h`, carrying a proof of `¬ p`.
-/

#print Decidable

/-!
`decide p` reads which of the two a decision procedure for `p` is, as a `Bool`. Three facts tie the
`Bool` back to the statement, and the whole checker rests on them:
-/

#check @of_decide_eq_true
#check @decide_eq_true
#check @decide_eq_false

/-!
The square brackets, `[inst : Decidable p]`, mean the procedure is found by **instance search**:
Lean looks for one by itself. It builds them out of smaller ones. There is one for `=` on numbers
and on strings, one for `∧`, one for "for every element of a list" (`∀ x ∈ l, P x`, when `P x` has
one), and so on. The checker's own instances are mostly a single line, which unfolds the
condition's definition and asks instance search for the rest:

```
instance (inp : Input) : Decidable (MapTotal inp) := by
  unfold MapTotal; infer_instance
```

`MapTotal` is an equation between two numbers, so the procedure found is the one for numbers.
-/

#eval decide (MapTotal bell)

/-!
#### Exercise 1 (★) `bell_total`
Prove `MapTotal bell` with plain `decide`.
-/

theorem bell_total : MapTotal bell := by
  decide

/-!
## 2. Four ways to run one, and what each trusts

There is a choice in how `decide p` gets computed, and it matters for what the answer is worth.

* `#eval decide p` runs the procedure as a program (on this page, Lean's interpreter; for `qcheck`,
  compiled code). You get `true` or `false`, and no theorem.
* `by decide` evaluates the procedure inside Lean's elaborator, the part that reads your text, and
  if it finds `isTrue` it hands the kernel the proof `of_decide_eq_true rfl`. The kernel, the small
  trusted core that checks every proof, then computes `decide p` again to check that `rfl`.
* `by decide +kernel` skips the elaborator's evaluation and gives the same proof straight to the
  kernel. The theorem is the same, and the trust is the same: only the kernel.
* `by native_decide` compiles `decide p`, runs the compiled code, and records its answer as an
  **axiom**: a statement Lean accepts without proof.

The first and the last trust the compiler, and the code it produced; the middle two trust only the
kernel. Why have both of the middle ones? Plain `decide` can get stuck where the kernel does not.
Its evaluator will not unfold a function defined by well-founded recursion, and the replay's
`merge` is one (its `termination_by`): plain `decide` cannot compute where an ion is, `decide
+kernel` can. That is why every replay proof in Part F is `decide +kernel`.

Here is the Bell certificate proved the fourth way, and what the proof rests on:
-/

theorem bell_native : check bell = true := by native_decide

#print axioms bell_native

/-!
Besides `propext`, the list names an axiom Lean made up for this one proof: it says the compiled
code returned `true`. That is a perfectly good fact about the compiled code, and nothing more. The
exercises of this tutorial accept only proofs that rest on `propext`, `Quot.sound` and
`Classical.choice`, so `native_decide` never solves one.

## 3. Permutations, and an axiom kept out

Condition 8, `OwnIons`, is a `List.Perm`: a witness's ions are a rearrangement of its op's ions.
Lean's core library has a decision procedure for `List.Perm`, `List.decidablePerm`. Look at what
it rests on:
-/

#print axioms List.decidablePerm

/-!
`Classical.choice` is the axiom of choice: harmless to most mathematics, and an axiom all the same.
A theorem rests on every axiom of every definition its proof mentions, and `check` mentions every
instance it evaluates. So had `OwnIons` been decided by core's instance, `check_sound` would rest on
`Classical.choice` and `Quot.sound` as well as `propext`. Here is the same fact proved both ways.
The first lets instance search pick core's procedure; the second uses the checker's own, `permB`,
and its correctness theorem `permB_sound`:
-/

theorem rider_perm_core : [some "q1", some "q0"].Perm ([0, 1].map (ionOf bell)) := by
  decide +kernel

theorem rider_perm_own : [some "q1", some "q0"].Perm ([0, 1].map (ionOf bell)) :=
  permB_sound (by decide +kernel)

#print axioms rider_perm_core
#print axioms rider_perm_own

/-!
The checker's procedure is in the module `QCCDC.Cert.Perm`, and it is the obvious one. `takeOut a l`
removes the
first `a` from `l`, or says `none` if there is no `a`; `permB` takes the first element of the left
list out of the right one, and recurses.
-/

#print takeOut
#print permB

#eval takeOut "q0" ["q1", "q0", "q2"]
#eval permB ["q1", "q0"] ["q0", "q1"]
#eval permB ["q1", "q1"] ["q0", "q1"]

/-!
`QCCDC.Cert.Perm` proves it right in both directions, using only lemmas of core that need `propext`:
`permB_sound` (if `permB` says `true`, the lists are rearrangements) and `permB_complete` (if they
are, it says `true`). `decPerm` packages the two as a `Decidable`. It is deliberately not an
instance: the checker names it where `OwnIons` is decided (`ownIonsAt`), so that instance search
can never pick
core's procedure in its place.
-/

#check @permB_sound
#check @permB_complete
#print axioms decPerm

/-!
#### Exercise 2 (★) `permB_rider`
Compute it: `permB` says the witness `q1, q0` is a rearrangement of `q0, q1`.
-/

theorem permB_rider : permB [some "q1", some "q0"] [some "q0", some "q1"] = true := by
  decide +kernel

/-!
#### Exercise 3 (★★) `takeOut_example`
`takeOut_perm` says: if `takeOut a l = some r`, then `l` is a rearrangement of `a :: r`. Use it to
prove the statement below; its hypothesis is a computation.
-/

#check @takeOut_perm

theorem takeOut_example : ["q1", "q0", "q2"].Perm ("q0" :: ["q1", "q2"]) :=
  takeOut_perm (by decide +kernel)

/-!
#### Exercise 4 (★★) `twice_not_perm`
Prove that a witness naming `q1` twice is **not** a rearrangement of the Bell CNOT's ions. Go
through `permB_complete`: a proof `h` of the `Perm` would make `permB` say `true`, and it says
`false`. (`absurd h₁ h₂` closes any goal from `h₁ : a` and `h₂ : ¬ a`.)
-/

theorem twice_not_perm : ¬ [some "q1", some "q1"].Perm ([0, 1].map (ionOf bell)) :=
  fun h => absurd (permB_complete h) (by decide +kernel)

#print axioms twice_not_perm

/-!
#### Exercise 5 (★★★) `permB_refl`
Prove, for every list, that `permB` finds it a rearrangement of itself. Do it by induction on the
list. In the step, `simp` with the definitions `permB` and `takeOut` and your induction hypothesis
sees that `takeOut a (a :: l)` is `some l`.
-/

theorem permB_refl : ∀ l : List String, permB l l = true := by
  intro l
  induction l with
  | nil => rfl
  | cons a l ih => simp [permB, takeOut, ih]

/-!
## 4. The replay, computed once

Three conditions read the replay: `MovesAreHops` and `GatesLegal` (where every ion is), and
`RespectsOrder` (the witness join). Each binds it with a `let` in its definition, as F2 printed.
Decided by "unfold, then instance search", that `let` landed inside the procedure for each single
move and witness, so the replay was recomputed for every one of them. The checker's own comments
record what
that cost: on a BB[[144,12,12]] round the replay ran about 3 700 times, and one check took ten
minutes (2026-09-26).

The fix states each of the three a second time, with the replay as a **parameter**, and the whole
specification likewise:
-/

#check @ImplementsIn
#check @implements_iff

/-!
`implements_iff` is proved by `Iff.rfl`: the two sides are the same proposition once definitions
are unfolded, so this changes nothing about what `Implements` says. The decision procedure for
`Implements` is then built from the one for `ImplementsIn` with `decidable_of_iff`, which turns a
procedure for one side of an `↔` into a procedure for the other. Evaluating it computes `replay
inp` and `witnessQubits inp` once, and passes them in.
-/

#check @decidable_of_iff

/-!
#### Exercise 6 (★★) `bell_implementsIn`
Prove the Bell certificate's `ImplementsIn`, with its replay and its witness join, from
`check_sound` and `implements_iff`. (`h.mp` is the left-to-right half of `h : a ↔ b`.)
-/

theorem bell_implementsIn : ImplementsIn bell (replay bell) (witnessQubits bell) :=
  (implements_iff bell).mp (check_sound (by decide +kernel))

/-!
## 5. A condition the checker does not have

Lesson F2 found that a witness for a measurement is accepted: `Covered` asks for witnesses only of
ops that need one, and says nothing about the others. Here is that certificate again,

{{figure bellMeasureW at=9}}

and here is a twelfth condition that would refuse it: no witness names an operation that needs
none. The checker does not have it; a computed check outside Lean covers that ground (lesson F6).
Its decision procedure is the usual single line.
-/

def bellMeasureW : Input := { bell with gates := bell.gates ++ [⟨2, 9, "C0", ["q0"]⟩] }

def NoReadoutWitness (inp : Input) : Prop :=
  ∀ g ∈ inp.gates, ∀ o ∈ inp.ops, o.idx = g.dag → needsWitness o = true

instance (inp : Input) : Decidable (NoReadoutWitness inp) := by
  unfold NoReadoutWitness; infer_instance

#eval decide (NoReadoutWitness bell)
#eval decide (NoReadoutWitness bellMeasureW)

/-!
#### Exercise 7 (★) `measure_witness_flagged`
Prove both: the checker accepts `bellMeasureW`, and it breaks the new condition.
-/

theorem measure_witness_flagged : check bellMeasureW = true ∧ ¬ NoReadoutWitness bellMeasureW :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
Instance search wrote that procedure. You can also write one by hand, as a `Bool` function, and
then owe a proof that it says `true` exactly when the condition holds. That proof is what
`decidable_of_iff` needs to make it a decision procedure. Here is a hand-written function: for
every witness and every op, either the op is not the witness's (`!=`) or it needs a witness.
-/

def noReadoutB (inp : Input) : Bool :=
  inp.gates.all fun g => inp.ops.all fun o => o.idx != g.dag || needsWitness o

#eval noReadoutB bellMeasureW

/-!
#### Exercise 8 (★★★) `noReadoutB_iff`
Prove that `noReadoutB` agrees with `NoReadoutWitness` on every certificate.

Hints: `simp only [noReadoutB, NoReadoutWitness, List.all_eq_true, Bool.or_eq_true, bne_iff_ne,
ne_eq]` turns the left side into "for every `g` and `o`, `¬ o.idx = g.dag ∨ needsWitness o =
true`". Then `constructor`, and in each direction take the `∨` apart (`rcases`) or build it
(`by_cases` on `o.idx = g.dag`, then `Or.inl` or `Or.inr`). The block goes on to make your proof a
decision procedure.
-/

theorem noReadoutB_iff (inp : Input) : noReadoutB inp = true ↔ NoReadoutWitness inp := by
  simp only [noReadoutB, NoReadoutWitness, List.all_eq_true, Bool.or_eq_true, bne_iff_ne, ne_eq]
  constructor
  · intro h g hg o ho he
    rcases h g hg o ho with h1 | h1
    · exact absurd he h1
    · exact h1
  · intro h g hg o ho
    by_cases he : o.idx = g.dag
    · exact Or.inr (h g hg o ho he)
    · exact Or.inl he

def decNoReadout (inp : Input) : Decidable (NoReadoutWitness inp) :=
  decidable_of_iff _ (noReadoutB_iff inp)

/-!
#### Exercise 9 (★★) `bell_check_is_decide`
Last, the checker itself. Prove that `check bell` is, by definition, the decision of `Implements
bell`, and that it is `true`.
-/

theorem bell_check_is_decide : check bell = decide (Implements bell) ∧ check bell = true :=
  ⟨rfl, by decide +kernel⟩

/-!
That `rfl` is the whole design. Because `check` is literally `decide (Implements inp)`, its
soundness is `of_decide_eq_true`, and nothing about the eleven procedures needs to be trusted
separately: whatever instance search assembled, `of_decide_eq_true` is a theorem about it. Lesson
F4 is that proof, and the proofs of refusal built the same way.
-/
