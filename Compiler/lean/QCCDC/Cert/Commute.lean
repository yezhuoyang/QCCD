/-
# Which reorderings the checker may admit, and why

`RespectsOrder` in `Check.lean` demands that two circuit ops sharing a qubit be realised in
program order.  That is the right default and it is what catches a scheduler that has
dropped a dependency -- but it is strictly stronger than correctness, and the rotation
compiler runs into the gap immediately.

A syndrome-extraction round is a fan of `cx` gates out of one ancilla.  They all share that
ancilla, so the strict rule pins their order completely; but they commute, and a compiler
that may reorder them can serve them in the order the loop happens to present them.  On
BB[[144,12,12]] that is the difference between 637 rotation batches and 546.

So the checker admits a reordering exactly when this file proves one, and no further.  The
alternative -- letting the certificate assert "these two commute" -- would hand the
compiler the power to reorder anything it liked.

## The proof, and what it covers

`cx c t` is a **permutation** of the computational basis: it sends the assignment `s` to
the one with `s t` replaced by `s t ⊕ s c`.  A permutation matrix is determined by its
permutation, so proving two of them commute as functions on assignments proves the
matrices commute; there is no gap between the statement here and the statement about
unitaries.

That is why this file can be Lean core and `decide`-shaped, while `Pulse/Native.lean` and
`Pulse/Decompose.lean` need Mathlib's matrices: those state facts about `R`, `VZ` and `MS`,
which are not permutations and genuinely need `Matrix ℂ`.

Two commutations hold and are proved below:

* **same control** -- `cx c t₁` and `cx c t₂` both read `c` and write disjoint targets.
* **same target**  -- `cx c₁ t` and `cx c₂ t` both add into `t`, and `⊕` is associative
  and commutative.

Nothing else is admitted.  In particular `cx a b` and `cx b c` do NOT commute (the first
writes what the second reads), and a one-qubit gate never commutes with anything sharing
its qubit -- both fall through to the strict rule.
-/

namespace QCCDC.Cert

/-- The action of `cx c t` on a computational basis state, written as an assignment of a
bit to each qubit.  Every other qubit is untouched. -/
def cxAct (c t : Nat) (s : Nat → Bool) : Nat → Bool :=
  fun q => if q = t then xor (s t) (s c) else s q

/-- Two `cx` gates that share a control and have distinct targets commute.

Neither writes the control, so each sees the other's control bit unchanged; and each
writes a target the other only leaves alone. -/
theorem cxAct_comm_same_control {c t₁ t₂ : Nat} (h₁ : t₁ ≠ c) (h₂ : t₂ ≠ c) (ht : t₁ ≠ t₂)
    (s : Nat → Bool) :
    cxAct c t₁ (cxAct c t₂ s) = cxAct c t₂ (cxAct c t₁ s) := by
  funext q
  by_cases e₁ : q = t₁ <;> by_cases e₂ : q = t₂ <;>
    simp_all [cxAct, Ne.symm h₁, Ne.symm h₂]

/-- Two `cx` gates that share a target and have distinct controls commute.

Both add their control bit into the same target, and `xor` is associative and
commutative -- so the target ends up as `s t ⊕ s c₁ ⊕ s c₂` either way. -/
theorem cxAct_comm_same_target {c₁ c₂ t : Nat} (h₁ : c₁ ≠ t) (h₂ : c₂ ≠ t)
    (s : Nat → Bool) :
    cxAct c₁ t (cxAct c₂ t s) = cxAct c₂ t (cxAct c₁ t s) := by
  funext q
  by_cases e : q = t
  · subst e
    simp only [cxAct, if_pos rfl, if_neg h₁, if_neg h₂]
    cases s q <;> cases s c₁ <;> cases s c₂ <;> rfl
  · simp [cxAct, e]

/-- And the negative direction, which is the one that keeps the rule honest: a `cx` whose
target is another's control does **not** commute with it.  Exhibited on a state, so it is
a refutation rather than an unproved omission. -/
theorem cxAct_not_comm_chain :
    cxAct 0 1 (cxAct 1 2 (fun q => decide (q = 0))) ≠
      cxAct 1 2 (cxAct 0 1 (fun q => decide (q = 0))) := by
  intro h
  have := congrFun h 2
  simp [cxAct] at this

/-! ## The rule the checker uses

`Commutes` is the syntactic side of the theorems above: it says yes in exactly the two
cases proved, and no everywhere else.  `Check.lean` consults it and nothing else, so the
set of reorderings the checker tolerates is precisely the set justified here. -/

/-- Do these two ops commute?  `qs` are the operands in program order: for a `cx`, control
then target. -/
def Commutes (n₁ : String) (qs₁ : List Nat) (n₂ : String) (qs₂ : List Nat) : Bool :=
  match n₁, qs₁, n₂, qs₂ with
  | "cx", [c₁, t₁], "cx", [c₂, t₂] =>
    (c₁ == c₂ && t₁ != t₂ && t₁ != c₁ && t₂ != c₂) ||
    (t₁ == t₂ && c₁ != c₂ && c₁ != t₁ && c₂ != t₂)
  | _, _, _, _ => false

end QCCDC.Cert
