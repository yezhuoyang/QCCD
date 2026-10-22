import QCCDC.Cert.Sound

/-!
# Soundness and completeness

*Part F, lesson F4. You need lessons F1 to F3.*

A checker can be wrong in two directions. It can say yes to a certificate that does not hold, and
then a proof built on its yes proves nothing; or it can say no to one that does, and then a
compiler that is right is turned away and nobody can tell why. The module `QCCDC.Cert.Sound` rules
out both, for every certificate, with three theorems a line each. This lesson reads them, takes
apart the pattern behind the eleven rejection lemmas, and has you prove the ones that module does
not have.
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
def bellLate : Input := { bell with moves := [⟨9, "q0", "C1", "C0"⟩] }

/-!
## 1. Three theorems
-/

#print check_sound
#check @check_complete
#check @check_iff

/-!
* `check_sound`: if the checker says `true`, the certificate holds. It is `of_decide_eq_true`,
  because `check` is `decide (Implements inp)` (lesson F3).
* `check_complete`: if the certificate holds, the checker says `true`. It is `decide_eq_true`.
  So a refusal is always a real defect, never a limitation of the check.
* `check_iff`: the two together.

None of the three looks inside the eleven decision procedures. That is the point of the design:
whatever instance search assembled, `of_decide_eq_true` and `decide_eq_true` are theorems about it,
so the only question left is whether `Implements` says the right thing, and a reader settles that
by reading it (lesson F2). A checker written as a pile of `Bool` tests would instead need a proof,
test by test, that each one means what it is supposed to; lesson F3 wrote one such proof for one
test (`noReadoutB_iff`).

#### Exercise 1 (★) `bell_iff`
State the equivalence for the Bell certificate: apply `check_iff`.
-/

theorem bell_iff : check bell = true ↔ Implements bell :=
  check_iff bell

/-!
Completeness is what lets you prove a certificate holds in pieces, and then get the checker's yes
for free. `bellSwapped` is `bell` with the CNOT's witness naming its ions the other way round,
which `OwnIons` allows (F2).

{{figure bellSwapped at=8}}
-/

def bellSwapped : Input := { bell with gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q1", "q0"]⟩] }

/-!
#### Exercise 2 (★★) `swapped_complete`
Prove `check bellSwapped = true` with `check_complete`, giving `Implements bellSwapped` as an
anonymous constructor `⟨h1, h2, ..., h11⟩`: one proof per condition, in order. The first is `rfl`
(the list is empty); each of the others is a computation.
-/

theorem swapped_complete : check bellSwapped = true :=
  check_complete ⟨rfl, by decide +kernel, by decide +kernel, by decide +kernel, by decide +kernel,
    by decide +kernel, by decide +kernel, by decide +kernel, by decide +kernel, by decide +kernel,
    by decide +kernel⟩

/-!
Read backwards, completeness says: if the checker says `false`, the certificate does not hold.

#### Exercise 3 (★) `late_false`
Prove that F1's late move does not satisfy the specification. Suppose it did (`fun h => ...`):
`check_complete h` would say `check bellLate = true`, and computing it says otherwise.
-/

theorem late_false : ¬ Implements bellLate :=
  fun h => absurd (check_complete h) (by decide +kernel)

/-!
## 2. How a rejection lemma is made
-/

#print rejects_teleport

/-!
Read it from the inside out. `himp` is a supposed proof of `Implements inp`. It is a chain of
`∧`: for a proof of `a ∧ b`, `.1` is the proof of `a` and `.2` the proof of `b` (Lean prints them
`.left` and `.right`). Condition `k` sits behind `k - 1` steps of `.2`:

| condition | its proof inside `himp` |
|---|---|
| 1 `unrealised = []` | `himp.1` |
| 2 `MapInjective` | `himp.2.1` |
| 3 `MapTotal` | `himp.2.2.1` |
| 4 `EventsSorted` | `himp.2.2.2.1` |
| 5 `RotationsKnown` | `himp.2.2.2.2.1` |
| 6 `MovesAreHops` | `himp.2.2.2.2.2.1` |
| 7 `GatesLegal` | `himp.2.2.2.2.2.2.1` |
| 8 `OwnIons` | `himp.2.2.2.2.2.2.2.1` |
| 9 `Covered` | `himp.2.2.2.2.2.2.2.2.1` |
| 10 `Grounded` | `himp.2.2.2.2.2.2.2.2.2.1` |
| 11 `RespectsOrder` | `himp.2.2.2.2.2.2.2.2.2.2` |

`rejects_teleport` takes condition 6, applies it to the move `m` (it is a "for every move"), takes
the first half (the hop), and the hypothesis `h` says that half is false: so `himp` cannot exist.
`decide_eq_false` turns "`Implements inp` cannot hold" into `decide (Implements inp) = false`,
which is `check inp = false` by definition. Every rejection lemma in `QCCDC.Cert.Sound` has this
shape,
and so `#print axioms` finds only `propext` in each, the axiom `check_sound` itself rests on.

The module has eleven of them, but not one per condition: `GatesLegal` and `MovesAreHops` have two
each, and two conditions have none. F1 proved the one for `MapTotal`. The other is next.

#### Exercise 4 (★★) `rejects_unsorted`
Prove that a certificate whose movement is not in cycle order is refused, for every certificate.
-/

theorem rejects_unsorted {inp : Input} (h : ¬ EventsSorted inp) : check inp = false :=
  decide_eq_false fun himp => h himp.2.2.2.1

/-!
## 3. New rejection lemmas from old ones

A rejection lemma is a theorem, so it can be used like one: to prove more specific refusals that
are easier to apply. `rejects_foreign_ions` needs you to show two lists are not rearrangements of
each other. Two common reasons they are not: they have different lengths, or the op has a qubit
the map does not carry.

#### Exercise 5 (★★★) `rejects_wrong_count`
Prove that a witness naming a different number of ions from its op's number of qubits is refused,
for every certificate. Apply `rejects_foreign_ions`; for its last argument, suppose a
rearrangement `hp` and use `hp.length_eq`, which `simpa` can bring to the form of `h`.
-/

theorem rejects_wrong_count {inp : Input} {g : GateW} (hg : g ∈ inp.gates) {o : Op}
    (ho : o ∈ inp.ops) (hi : o.idx = g.dag) (h : g.ions.length ≠ o.qubits.length) :
    check inp = false :=
  rejects_foreign_ions hg ho hi (fun hp => h (by simpa using hp.length_eq))

/-!
F2's `bellShort` (the CNOT witnessed on one ion) is the case it was made for:

{{figure bellShort at=8}}
-/

def bellShort : Input := { bell with gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0"]⟩] }

example : check bellShort = false :=
  rejects_wrong_count (g := ⟨1, 8, "C0", ["q0"]⟩) (o := ⟨1, "cx", [0, 1]⟩)
    (by simp [bellShort]) (by simp [bellShort, bell]) rfl (by decide)

/-!
#### Exercise 6 (★★★) `rejects_unmapped_operand`
Prove that a witness for an op one of whose qubits has no ion in the map is refused. Its op's
ions, `o.qubits.map (ionOf inp)`, then contain `none`, and the witness's, `g.ions.map some`,
cannot. Hints: `List.mem_map.mpr ⟨q, hq, hn⟩` shows `none` is in the first list; a rearrangement
`hp` has `hp.mem_iff`, which moves membership across; `simp` knows `none` is never `some _`.
-/

theorem rejects_unmapped_operand {inp : Input} {g : GateW} (hg : g ∈ inp.gates) {o : Op}
    (ho : o ∈ inp.ops) (hi : o.idx = g.dag) {q : Nat} (hq : q ∈ o.qubits)
    (hn : ionOf inp q = none) : check inp = false :=
  rejects_foreign_ions hg ho hi fun hp => by
    have hmem : none ∈ o.qubits.map (ionOf inp) := List.mem_map.mpr ⟨q, hq, hn⟩
    have := hp.mem_iff.mpr hmem
    simp at this

/-!
#### Exercise 7 (★★) `rejects_same_cycle`
The order condition is strict. Prove that two witnesses of ops sharing a qubit, the first before
the second in the circuit, are refused when they claim the same cycle. `rejects_reordering` wants
`¬ a.t < b.t`; `omega` proves it from `a.t = b.t`.
-/

theorem rejects_same_cycle {inp : Input} {a b : WQ} (ha : a ∈ witnessQubits inp)
    (hb : b ∈ witnessQubits inp) (hlt : a.idx < b.idx)
    (hsh : (a.qubits.any fun q => b.qubits.contains q) = true) (ht : a.t = b.t) :
    check inp = false :=
  rejects_reordering ha hb hlt hsh (by omega)

/-!
## 4. A refusal always has a reason

`diagnose`, which `qcheck` prints under a REJECTED, lists the conditions that fail. Is that list
ever empty for a certificate the checker refuses? Completeness says no, and the next two exercises
prove it: a `false` from `check` always means at least one of the eleven conditions fails.

#### Exercise 8 (★★) `check_false_iff`
Prove that `check inp = false` exactly when `Implements inp` does not hold. One direction is
`decide_eq_false`. For the other, given `h : check inp = false` and a proof `hi` of `Implements
inp`, rewrite `h` with `check_complete hi` (`rw [...] at h`): it becomes `true = false`, and
`contradiction` finishes.
-/

theorem check_false_iff (inp : Input) : check inp = false ↔ ¬ Implements inp :=
  ⟨fun h hi => by rw [check_complete hi] at h; contradiction, fun h => decide_eq_false h⟩

/-!
#### Exercise 9 (★★★) `some_condition_fails`
Prove that when the checker refuses, one of the eleven conditions fails: the disjunction below.

From `¬ (a ∧ b)` you cannot in general conclude `¬ a ∨ ¬ b` without knowing which of `a` and `b`
holds; here you can find out, because each condition is decidable. That step is `orNot`, given:
it asks whether `p` holds (`if hp : p then ... else ...`) and answers accordingly.

Hints: `(check_false_iff inp).mp h` gives `¬ Implements inp`; `unfold Implements at hn` shows the
eleven. Then repeat one step ten times: `refine (orNot hn).imp id fun hn => ?_`. (`Or.imp f g`
turns `a ∨ b` into `c ∨ d` from `f : a → c` and `g : b → d`; `?_` is the rest, still to prove.)
-/

theorem orNot {p q : Prop} [Decidable p] (h : ¬ (p ∧ q)) : ¬ p ∨ ¬ q :=
  if hp : p then Or.inr fun hq => h ⟨hp, hq⟩ else Or.inl hp

theorem some_condition_fails {inp : Input} (h : check inp = false) :
    inp.unrealised ≠ [] ∨ ¬ MapInjective inp ∨ ¬ MapTotal inp ∨ ¬ EventsSorted inp ∨
      ¬ RotationsKnown inp ∨ ¬ MovesAreHops inp ∨ ¬ GatesLegal inp ∨ ¬ OwnIons inp ∨
      ¬ Covered inp ∨ ¬ Grounded inp ∨ ¬ RespectsOrder inp := by
  have hn : ¬ Implements inp := (check_false_iff inp).mp h
  unfold Implements at hn
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  refine (orNot hn).imp id fun hn => ?_
  exact hn

/-!
`diagnose` is not proved to agree with this; it computes the same eleven decisions with sentences
attached, and is not part of what `check_sound` vouches for. What you just proved is the statement
behind it: a refusal never comes without a failing condition.

## 5. Completeness in general

Completeness instances so far were single certificates. Here is one for a whole family.

#### Exercise 10 (★★★) `empty_accepted`
Prove that a certificate with no qubits, no operations, no movement and no witnesses is accepted,
on any device. Apply `check_complete`; give the first condition as `hu` and the other ten as
`?_`, and let one `simp` call, given the definitions of the conditions and of the replay (`events`,
`merge`, `sortedByT`, `witnessQubits`) and your hypotheses, close all ten (`<;>` runs it on each).
-/

theorem empty_accepted {inp : Input} (hq : inp.qmap = []) (hn : inp.nQubits = 0)
    (ho : inp.ops = []) (hm : inp.moves = []) (hr : inp.rots = []) (hg : inp.gates = [])
    (hu : inp.unrealised = []) : check inp = true := by
  apply check_complete
  refine ⟨hu, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩ <;>
    simp [MapInjective, MapTotal, EventsSorted, RotationsKnown, MovesAreHops, GatesLegal, OwnIons,
      Covered, Grounded, RespectsOrder, events, merge, sortedByT, witnessQubits, hq, hn, ho, hm, hr, hg]

/-!
An empty certificate is accepted on any device, and that is right: it claims nothing, so there is
nothing to refuse. What makes an accepted certificate mean something is the rest of the verdict:
that its circuit is the source circuit, and its programme the one that was run. Those checks are
computed outside Lean, and lesson F6 lists them. Lesson F5 first turns the rejection lemmas on
real mutants of a published certificate.
-/
