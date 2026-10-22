/-
# Deciding `List.Perm` on the trusted base

`OwnIons` in `Check.lean` says a gate witness names its op's own ions in any order:
`(g.ions.map some).Perm (o.qubits.map (ionOf inp))`.  `List.Perm` is Lean core's, and so is
an instance deciding it -- but `List.decidablePerm` rests on `List.isPerm_iff`, whose proof
uses `Classical.choice` and `Quot.sound`.  `check_sound` inherits the axioms of every
instance `check` evaluates, so using core's would widen the checker's trusted base from
`propext` to all three.

So the decision procedure is here, with its proof, on the lemmas of core that need only
`propext` (`Perm.mem_iff`, `Perm.cons_inv`, `Perm.nil_eq`) and on the constructors of
`Perm`.  It is the obvious one: take the first element of the left list out of the right
one, and recurse.

Why "in any order": a witness's ions say WHERE its op ran, not which operand plays which
part -- the pulses say that, and the semantic half of R10 reads them.  The rotation compiler
names the ion riding the loop first and the docked one second, whichever is the control, and
4 887 of the 21 320 witnesses in the 116 shipped qcheck inputs (2026-10-18) name their two
ions in the other order from the op's qubits.  Equal as a multiset is what
`bridge/check_cert.py`'s `check_witnesses` compares too.
-/

namespace QCCDC.Cert

/-- `l` with its first `a` taken out, or `none` if `a` is not in it. -/
def takeOut {α : Type} [DecidableEq α] (a : α) : List α → Option (List α)
  | [] => none
  | b :: l => if b = a then some l else (takeOut a l).map (b :: ·)

/-- Is `l₁` a rearrangement of `l₂`? -/
def permB {α : Type} [DecidableEq α] : List α → List α → Bool
  | [], [] => true
  | [], _ :: _ => false
  | a :: l₁, l₂ =>
    match takeOut a l₂ with
    | none => false
    | some r => permB l₁ r

theorem takeOut_perm {α : Type} [DecidableEq α] {a : α} :
    ∀ {l r : List α}, takeOut a l = some r → l.Perm (a :: r)
  | [], _, h => nomatch h
  | b :: l, r, h => by
    unfold takeOut at h
    by_cases hb : b = a
    · rw [if_pos hb] at h
      cases h
      cases hb
      exact List.Perm.refl _
    · rw [if_neg hb] at h
      cases e : takeOut a l with
      | none => rw [e] at h; nomatch h
      | some r' =>
        rw [e] at h
        cases h
        exact ((takeOut_perm e).cons b).trans (List.Perm.swap a b r')

theorem takeOut_none {α : Type} [DecidableEq α] {a : α} :
    ∀ {l : List α}, takeOut a l = none → a ∉ l
  | [], _, hm => nomatch hm
  | b :: l, h, hm => by
    unfold takeOut at h
    by_cases hb : b = a
    · rw [if_pos hb] at h; nomatch h
    · rw [if_neg hb] at h
      cases e : takeOut a l with
      | some r' => rw [e] at h; nomatch h
      | none =>
        cases hm with
        | head => exact hb rfl
        | tail _ hm => exact takeOut_none e hm

theorem permB_sound {α : Type} [DecidableEq α] :
    ∀ {l₁ l₂ : List α}, permB l₁ l₂ = true → l₁.Perm l₂
  | [], [], _ => List.Perm.nil
  | [], _ :: _, h => nomatch h
  | a :: l₁, l₂, h => by
    unfold permB at h
    cases e : takeOut a l₂ with
    | none => rw [e] at h; nomatch h
    | some r =>
      rw [e] at h
      exact ((permB_sound h).cons a).trans (takeOut_perm e).symm

theorem permB_complete {α : Type} [DecidableEq α] :
    ∀ {l₁ l₂ : List α}, l₁.Perm l₂ → permB l₁ l₂ = true
  | [], [], _ => rfl
  | [], _ :: _, h => nomatch h.nil_eq
  | a :: l₁, l₂, h => by
    unfold permB
    cases e : takeOut a l₂ with
    | none => exact absurd (h.mem_iff.mp (List.Mem.head _)) (takeOut_none e)
    | some r => exact permB_complete (h.trans (takeOut_perm e)).cons_inv

theorem permB_iff {α : Type} [DecidableEq α] {l₁ l₂ : List α} :
    permB l₁ l₂ = true ↔ l₁.Perm l₂ :=
  ⟨permB_sound, permB_complete⟩

/-- `List.Perm`, decided by `permB`.  Not an instance: `Check.lean` names it where it is
used, so instance search cannot pick core's `List.decidablePerm` in its place. -/
def decPerm {α : Type} [DecidableEq α] (l₁ l₂ : List α) : Decidable (l₁.Perm l₂) :=
  decidable_of_iff _ permB_iff

end QCCDC.Cert
