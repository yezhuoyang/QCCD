/-
# Soundness, and what it does and does not say

`check_sound` is the theorem R10 rests on.  It is short, and that is the design: `check`
is defined as `decide (Implements inp)`, so soundness is `of_decide_eq_true` and every
substantive question has been moved into whether `Implements` says the right thing --
which a reader can settle by reading `Check.lean`, rather than by auditing a pile of
`Bool`s against a meaning asserted elsewhere.

What follows the main theorem is more useful than the main theorem: the *rejection*
lemmas.  A checker is only worth running if a wrong certificate fails it, so each named
failure mode is proved to be one `check` cannot accept.  That is what the C6 mutation
suite exercises on real compiled programs, and what these lemmas say is true by
construction rather than by testing.
-/
import QCCDC.Cert.Check

namespace QCCDC.Cert

/-- **The soundness theorem.**  If the checker accepts, the compiled program implements
the circuit -- in the sense `Implements` spells out. -/
theorem check_sound {inp : Input} (h : check inp = true) : Implements inp :=
  of_decide_eq_true h

/-- And the converse: the checker accepts everything that does implement the circuit, so
a rejection is always a real defect and never a limitation of the check. -/
theorem check_complete {inp : Input} (h : Implements inp) : check inp = true :=
  decide_eq_true h

theorem check_iff (inp : Input) : check inp = true ↔ Implements inp :=
  ⟨check_sound, check_complete⟩

/-! ## What a rejection guarantees

Each lemma names a way a compiler can be wrong and shows `check` cannot accept it.
`check` is definitionally `decide (Implements inp)`, so each is `decide_eq_false` applied
to the conjunct that fails -- which keeps the proofs one line and makes the projection
path itself the documentation of where in the specification the failure lives. -/

/-- A circuit op left unrealised is never accepted. -/
theorem rejects_unrealised {inp : Input} (h : inp.unrealised ≠ []) : check inp = false :=
  decide_eq_false fun himp => h himp.1

/-- Two logical qubits mapped to one ion is never accepted. -/
theorem rejects_aliased_qubits {inp : Input} (h : ¬ MapInjective inp) :
    check inp = false :=
  decide_eq_false fun himp => h himp.2.1

/-- A gate whose operands are not both in the trap the witness names is never accepted.
This is the co-location obligation, O1, and it is checked against the REPLAYED positions
rather than against anything the certificate claims about them. -/
theorem rejects_not_colocated {inp : Input} {g : GateW} (hg : g ∈ inp.gates)
    {i : String} (hi : i ∈ g.ions) (h : look (posAt inp g.t) i ≠ some g.site) :
    check inp = false :=
  decide_eq_false fun himp => h ((himp.2.2.2.2.2.2.1 g hg).2 i hi)

/-- A gate placed in a trap that cannot gate is never accepted. -/
theorem rejects_ungateable_site {inp : Input} {g : GateW} (hg : g ∈ inp.gates)
    (h : g.site ∉ inp.gateSites) : check inp = false :=
  decide_eq_false fun himp => h (himp.2.2.2.2.2.2.1 g hg).1

/-- A dropped gate -- an op that needs a witness and has none -- is never accepted. -/
theorem rejects_dropped_gate {inp : Input} {o : Op} (ho : o ∈ inp.ops)
    (hn : needsWitness o = true)
    (h : (inp.gates.filter (fun g => g.dag = o.idx)).length ≠ 1) :
    check inp = false :=
  decide_eq_false fun himp => h (himp.2.2.2.2.2.2.2.1 o ho hn)

/-- An ion that moves without a hop the architecture admits -- a teleport -- is never
accepted.  Without this conjunct every later check could be satisfied by putting ions
wherever they were needed. -/
theorem rejects_teleport {inp : Input} {m : Move} (hm : m ∈ inp.moves)
    (h : (m.src, m.dst) ∉ inp.hops) : check inp = false :=
  decide_eq_false fun himp => h (himp.2.2.2.2.2.1 m hm).1

/-- A move that departs from a trap the ion is not in is never accepted. -/
theorem rejects_wrong_departure {inp : Input} {m : Move} (hm : m ∈ inp.moves)
    (h : look (posAt inp m.t) m.ion ≠ some m.src) : check inp = false :=
  decide_eq_false fun himp => h (himp.2.2.2.2.2.1 m hm).2

/-- A gate witness for an op the circuit does not contain is never accepted. -/
theorem rejects_invented_gate {inp : Input} {g : GateW} (hg : g ∈ inp.gates)
    (h : (inp.ops.any (fun o => o.idx = g.dag)) = false) : check inp = false :=
  decide_eq_false fun himp => by
    have := himp.2.2.2.2.2.2.2.2.1 g hg
    rw [h] at this; exact Bool.noConfusion this

/-- Two ops sharing a qubit, realised out of program order, are never accepted.

Stated over `witnessQubits` -- each witness paired with the qubits of the op it realises,
joined here from the circuit's own op list.  With `Covered` and `Grounded` that is the same
population as "every pair of ops sharing a qubit", and it is the form the decision
procedure can actually run: quantifying over ops AND their witnesses separately is four
nested quantifiers, which on a BB round is 10^10 pairs. -/
theorem rejects_reordering {inp : Input} {a b : WQ}
    (ha : a ∈ witnessQubits inp) (hb : b ∈ witnessQubits inp) (hlt : a.idx < b.idx)
    (hsh : (a.qubits.any (fun q => b.qubits.contains q)) = true)
    (hnc : Commutes a.name a.qubits b.name b.qubits = false) (h : ¬ a.t < b.t) :
    check inp = false :=
  decide_eq_false fun himp =>
    match himp.2.2.2.2.2.2.2.2.2 a ha b hb hlt hsh with
    | .inl hc => by rw [hnc] at hc; exact Bool.noConfusion hc
    | .inr hlt => h hlt

/-- A rotation of a loop the architecture does not declare is never accepted.  The loop
inventory reaches the checker from the architecture document, so this is the conjunct that
stops a compiler from turning a loop that does not exist. -/
theorem rejects_unknown_loop {inp : Input} {r : Rot} (hr : r ∈ inp.rots)
    (h : (inp.loops.any (fun p => p.1 = r.loop)) = false) : check inp = false :=
  decide_eq_false fun himp => by
    have := himp.2.2.2.2.1 r hr
    rw [h] at this; exact Bool.noConfusion this

/-! ## The trusted base -/

#print axioms check_sound
#print axioms check_complete
#print axioms rejects_not_colocated
#print axioms rejects_dropped_gate
#print axioms rejects_teleport
#print axioms rejects_wrong_departure
#print axioms rejects_reordering
#print axioms rejects_unknown_loop
#print axioms rejects_invented_gate

end QCCDC.Cert
