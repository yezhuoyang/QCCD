/-
# Two reorderings the semantic check cannot see -- and the checker no longer admits

Two `cx` gates that share a control, or share a target, commute: either order is the same
unitary.  This file proves it, and proves that a control/target chain does not.

## What these facts are for now

Since 2026-10-19 they license nothing.  The user decided that day that the compiler never
changes the order of the input circuit's gates, not even of gates that commute, and
`RespectsOrder` in `Check.lean` became strict: two ops sharing a qubit are realised in
program order, with no exemption.  Nothing in the checker consults this file, and
`Check.lean` no longer imports it.

The theorems stay because they are true, and because they are the argument FOR the strict
rule.  They say that each reorder below leaves the unitary unchanged -- so the semantic half
of R10, which composes the emitted pulses and compares them with the circuit
(`bridge/check_cert.py`), cannot tell the reordered program from the faithful one.  The
order clause is the only place such a reorder can be caught at all; exempting it there is
exempting it everywhere.

## What they used to be for

From 2026-08-24 to 2026-10-19 the order rule admitted exactly these two reorderings, through
a syntactic predicate `Commutes` that said yes in the two cases proved here and no anywhere
else.  A syndrome-extraction round is a fan of `cx` gates out of one ancilla; the strict
rule pins their order, and a compiler allowed to reorder them could serve them in the order
the loop presented them.  On BB[[144,12,12]] on `ring144_24v` that was 546 rotations for
the round; in circuit order, 824 (the two certificates qcheck was rerun on, 2026-10-19).
The exemption was sound about unitaries and still wrong about the job: the order
of a qubit's gates is the circuit author's (in a syndrome round it decides which data qubits
one ancilla fault spreads to), and a reorder no other check can see is the one the order
check must not wave through.  `Commutes` was deleted with the exemption: an exemption
predicate kept beside the checker, unused, is an invitation to wire it back in.

## The proof, and what it covers

`cx c t` is a **permutation** of the computational basis: it sends the assignment `s` to
the one with `s t` replaced by `s t ⊕ s c`.  A permutation matrix is determined by its
permutation, so proving two of them commute as functions on assignments proves the
matrices commute; there is no gap between the statement here and the statement about
unitaries.

That is why this file can be Lean core and `decide`-shaped, while `Pulse/Native.lean` and
`Pulse/Decompose.lean` need Mathlib's matrices: those state facts about `R`, `VZ` and `MS`,
which are not permutations and genuinely need `Matrix ℂ`.

* **same control** -- `cx c t₁` and `cx c t₂` both read `c` and write disjoint targets.
* **same target**  -- `cx c₁ t` and `cx c₂ t` both add into `t`, and `⊕` is associative
  and commutative.
* **not a chain**  -- `cx a b` and `cx b c` do NOT commute (the first writes what the
  second reads), exhibited on a state.

## Where it is built

It imports nothing and nothing in the checker imports it, so `qcheck`'s build (`checker/`)
does not compile it.  The library root `QCCDC.lean` imports it, so the parent package's
`lake build` checks the proofs; on its own, `lake env lean ../QCCDC/Cert/Commute.lean` from
`checker/` does it in seconds.
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

/-- And the negative direction: a `cx` whose target is another's control does **not**
commute with it.  Exhibited on a state, so it is a refutation rather than an unproved
omission.  (It once kept the exemption honest; with no exemption left, it is the reminder
that "these share a qubit" and "these commute" were never the same question.) -/
theorem cxAct_not_comm_chain :
    cxAct 0 1 (cxAct 1 2 (fun q => decide (q = 0))) ≠
      cxAct 1 2 (cxAct 0 1 (fun q => decide (q = 0))) := by
  intro h
  have := congrFun h 2
  simp [cxAct] at this

end QCCDC.Cert
