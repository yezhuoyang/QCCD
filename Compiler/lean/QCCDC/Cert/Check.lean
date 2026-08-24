/-
# The specification, and its decision procedure

The pattern here is deliberate and worth naming, because it is what makes the soundness
theorem in `Sound.lean` mean something rather than being a tautology dressed up.

`Implements` is a **Prop**, written in terms of a replay of the certificate's movement from
the initial placement.  It never mentions the checker.  It says what it means for the
compiled program to realise the circuit, and it would say the same thing if no checker
existed.

`check` is then *nothing but* `decide (Implements inp)`.  So the theorem
`check inp = true → Implements inp` is `of_decide_eq_true`, and all of the content has
moved to where it belongs: into whether `Implements` is the right statement, which a reader
can judge by reading it.  A checker written as a pile of `Bool`s with a separately asserted
meaning is the arrangement that hides its assumptions; this one cannot.

The conjuncts are the whole claim:

  1. `unrealised = []`   -- the compiler realised every op it was given
  2. `MapInjective`      -- distinct qubits get distinct ions
  3. `MapTotal`          -- every qubit of the circuit is mapped
  4. `EventsSorted`      -- movement is in cycle order, so replaying it is well defined
  5. `RotationsKnown`    -- every rotation names a loop the ARCHITECTURE declares
  6. `MovesAreHops`      -- an ion never teleports: every move is one the architecture
                            admits, and departs from where the ion actually is
  7. `GatesLegal`        -- operands co-located, at the named trap, and that trap can gate
  8. `Covered`           -- every circuit op that needs a witness has exactly one
  9. `Grounded`          -- and every witness realises an op that exists
 10. `RespectsOrder`     -- two ops sharing a qubit happen in program order

Together they are O1, and O3 comes with (8)-(10): a dropped gate fails coverage, an
invented one fails grounding, and a reordered pair fails the order check.

## Why the replay is a list

`posAt inp t` used to be a fold over the moves before cycle `t`, which reads beautifully
and costs O(moves) per lookup.  With one lookup per move and one per gate that is
quadratic, and on a BB[[144,12,12]] round -- 113 472 moves, once every rotation is expanded
into one move per ion per unit hop -- it does not finish in any useful time.  Two changes
fix that without weakening anything:

* rotation is a **witness** (`Rot`) rather than 264 moves.  It is the primitive the
  hardware itself has, and it cuts the event list by a factor of fifty.
* the replay is materialised **once**, as a list of snapshots, and the conjuncts quantify
  over it.

Neither is a concession.  `∀ m ∈ moves, ... look (stateIn R m.t) m.ion = some m.src` says
what the fold said -- the snapshots are computed here, from `init` and the witnesses, and
none of them is supplied by the compiler.
-/
import QCCDC.Cert.Syntax
import QCCDC.Cert.Commute

namespace QCCDC.Cert

/-! ## The replay -/

/-- Update an association list. -/
def upd (l : List (String × String)) (k v : String) : List (String × String) :=
  (k, v) :: l.filter (fun p => p.1 ≠ k)

/-- Look a key up. -/
def look (l : List (String × String)) (k : String) : Option String :=
  (l.find? (fun p => p.1 = k)).map Prod.snd

/-- Where in a loop's cyclic order a trap sits, if it is on the loop at all. -/
def slotOf : List String → String → Nat → Option Nat
  | [], _, _ => none
  | a :: rest, s, k => if a = s then some k else slotOf rest s (k + 1)

/-- Advance every ion standing on `nodes` by `delta` slots; leave every other ion alone.

That "leave every other ion alone" is the whole reason a rotation may not happen while an
ion is docked: a docked ion is off the loop, so the loop would turn underneath it and it
would come back beside a different slot.  Emitting no such rotation is the compiler's job;
modelling it faithfully is the checker's, and this is that model. -/
def rotateState (nodes : List String) (delta : Int)
    (st : List (String × String)) : List (String × String) :=
  let n : Int := nodes.length
  if n ≤ 0 then st
  else
    st.map fun p =>
      match slotOf nodes p.2 0 with
      | none => p
      | some i => (p.1, nodes.getD ((((i : Int) + delta) % n + n) % n).toNat p.2)

/-- One thing that happens at a cycle: an ion hops, or a loop turns. -/
inductive Ev where
  | mv : Move → Ev
  | rot : Rot → Ev
  deriving Repr, Inhabited

def Ev.cyc : Ev → Nat
  | .mv m => m.t
  | .rot r => r.t

/-- How an event moves the ions.  A rotation of a loop the architecture does not declare
leaves everything where it is -- and `RotationsKnown` refuses such a certificate outright,
so that branch is unreachable in anything the checker accepts. -/
def Ev.apply (loops : List (String × List String))
    (st : List (String × String)) : Ev → List (String × String)
  | .mv m => upd st m.ion m.dst
  | .rot r =>
    match loops.find? (fun p => p.1 = r.loop) with
    | none => st
    | some p => rotateState p.2 r.delta st

/-- Everything that happens, in cycle order.  Both lists arrive sorted, so a merge suffices
-- and `EventsSorted` checks the result rather than trusting either. -/
def merge : List Move → List Rot → List Ev
  | [], rs => rs.map Ev.rot
  | ms, [] => ms.map Ev.mv
  | m :: ms, r :: rs =>
    if m.t ≤ r.t then Ev.mv m :: merge ms (r :: rs)
    else Ev.rot r :: merge (m :: ms) rs
  termination_by ms rs => ms.length + rs.length

def events (inp : Input) : List Ev := merge inp.moves inp.rots

/-- For each event, the cycle it belongs to and where every ion was **before** it.

Computed from `init` and the movement witnesses ALONE.  Any position the certificate also
claims is checked against this, never substituted for it -- the discipline
`qccd/ir/import_deck.py` applies to the shipped artifact, and for the same reason: totals
can agree by coincidence, a few thousand recomputed positions cannot. -/
def snapshots (loops : List (String × List String)) (st : List (String × String)) :
    List Ev → List (Nat × List (String × String))
  | [] => []
  | e :: es => (e.cyc, st) :: snapshots loops (Ev.apply loops st e) es

/-- The positions once every event has happened. -/
def finalPos (loops : List (String × List String)) (st : List (String × String)) :
    List Ev → List (String × String)
  | [] => st
  | e :: es => finalPos loops (Ev.apply loops st e) es

/-- The replay of one certificate: its snapshots, and its end state. -/
def replay (inp : Input) : List (Nat × List (String × String)) × List (String × String) :=
  let es := events inp
  (snapshots inp.loops inp.init es, finalPos inp.loops inp.init es)

/-- Where the ions are at the start of cycle `t`: after every event before `t`, and before
every event at or after it.  The snapshots are in cycle order, so the first one at or after
`t` is exactly that state; if every event is already past, the end state is. -/
def stateIn (R : List (Nat × List (String × String)) × List (String × String))
    (t : Nat) : List (String × String) :=
  match R.1.find? (fun p => t ≤ p.1) with
  | some p => p.2
  | none => R.2

/-- Where every ion is at the start of cycle `t`. -/
def posAt (inp : Input) (t : Nat) : List (String × String) := stateIn (replay inp) t

/-! ## The conjuncts -/

/-- Distinct qubits are carried by distinct ions. -/
def MapInjective (inp : Input) : Prop :=
  ∀ p ∈ inp.qmap, ∀ q ∈ inp.qmap, p.2 = q.2 → p.1 = q.1

/-- Every qubit of the circuit is mapped. -/
def MapTotal (inp : Input) : Prop :=
  inp.qmap.length = inp.nQubits

/-- Is a list of events in non-decreasing cycle order?

Written out rather than reached for from a library: `List.Chain'` lives in Mathlib, and
this file deliberately imports only Lean core so that the checker rebuilds in seconds. -/
def sortedByT : List Ev → Bool
  | [] => true
  | [_] => true
  | a :: b :: rest => a.cyc ≤ b.cyc && sortedByT (b :: rest)

/-- Movement is in cycle order, which is what makes the replay well defined. -/
def EventsSorted (inp : Input) : Prop := sortedByT (events inp) = true

/-- Every rotation turns a loop the architecture actually has.  Without this a compiler
could name a loop nobody declared, the replay would leave every ion where it was, and a
schedule that never moved anything could still claim its gates were co-located. -/
def RotationsKnown (inp : Input) : Prop :=
  ∀ r ∈ inp.rots, (inp.loops.any (fun p => p.1 = r.loop)) = true

/-- Every move is a hop the architecture admits, and departs from where the ion actually
is.  Without this an ion could be teleported into position and every later check would
pass. -/
def MovesAreHops (inp : Input) : Prop :=
  let R := replay inp
  ∀ m ∈ inp.moves,
    (m.src, m.dst) ∈ inp.hops ∧ look (stateIn R m.t) m.ion = some m.src

/-- Each gate's operands are co-located, at the trap the witness names, and that trap can
gate. -/
def GatesLegal (inp : Input) : Prop :=
  let R := replay inp
  ∀ g ∈ inp.gates,
    g.site ∈ inp.gateSites ∧
    ∀ i ∈ g.ions, look (stateIn R g.t) i = some g.site

/-- The ops that must be realised: everything that is not classical bookkeeping. -/
def needsWitness (o : Op) : Bool :=
  o.name ≠ "measure" && o.name ≠ "reset" && o.name ≠ "barrier"

/-- Every op that needs a witness has exactly one. -/
def Covered (inp : Input) : Prop :=
  ∀ o ∈ inp.ops, needsWitness o = true →
    (inp.gates.filter (fun g => g.dag = o.idx)).length = 1

/-- And every witness realises an op the circuit contains.  Coverage alone would let a
compiler bury extra gates under indices no op carries. -/
def Grounded (inp : Input) : Prop :=
  ∀ g ∈ inp.gates, (inp.ops.any (fun o => o.idx = g.dag)) = true

/-- Each witness, carrying the qubits of the op it claims to realise.

The qubits are looked up in the circuit's own op list, so this is a join and not something
the compiler asserts.  Materialising it turns the order check from a quadruple quantifier
into a double one -- on a BB round, 750 000 pairs rather than 10^10. -/
structure WQ where
  idx : Nat            -- the op index, i.e. program order
  t : Nat              -- the cycle its witness claims
  name : String
  qubits : List Nat
  deriving Repr, DecidableEq, Inhabited

def witnessQubits (inp : Input) : List WQ :=
  inp.gates.filterMap fun g =>
    (inp.ops.find? (fun o => o.idx = g.dag)).map fun o =>
      { idx := g.dag, t := g.t, name := o.name, qubits := o.qubits }

/-- Two ops sharing a qubit are realised in program order -- unless they commute.

This is where a reordering shows up.  Two things keep it honest.  The precedence is
DERIVED from the op list, so a compiler cannot supply a permissive one; and the exemption
is `Commutes`, whose two cases are the two proved in `Commute.lean` and no others.  A
certificate cannot claim that a pair commutes. -/
def RespectsOrder (inp : Input) : Prop :=
  let W := witnessQubits inp
  ∀ a ∈ W, ∀ b ∈ W, a.idx < b.idx →
    (a.qubits.any (fun q => b.qubits.contains q)) = true →
      Commutes a.name a.qubits b.name b.qubits = true ∨ a.t < b.t

/-- **The specification.**  What it means for the compiled program to implement the
circuit, said without reference to any checker. -/
def Implements (inp : Input) : Prop :=
  inp.unrealised = [] ∧
  MapInjective inp ∧
  MapTotal inp ∧
  EventsSorted inp ∧
  RotationsKnown inp ∧
  MovesAreHops inp ∧
  GatesLegal inp ∧
  Covered inp ∧
  Grounded inp ∧
  RespectsOrder inp

/-! ## Decidability -/

instance (inp : Input) : Decidable (MapInjective inp) := by
  unfold MapInjective; infer_instance

instance (inp : Input) : Decidable (MapTotal inp) := by
  unfold MapTotal; infer_instance

instance (inp : Input) : Decidable (EventsSorted inp) := by
  unfold EventsSorted; infer_instance

instance (inp : Input) : Decidable (RotationsKnown inp) := by
  unfold RotationsKnown; infer_instance

instance (inp : Input) : Decidable (MovesAreHops inp) := by
  unfold MovesAreHops; infer_instance

instance (inp : Input) : Decidable (GatesLegal inp) := by
  unfold GatesLegal; infer_instance

instance (inp : Input) : Decidable (Covered inp) := by
  unfold Covered; infer_instance

instance (inp : Input) : Decidable (Grounded inp) := by
  unfold Grounded; infer_instance

instance (inp : Input) : Decidable (RespectsOrder inp) := by
  unfold RespectsOrder; infer_instance

instance (inp : Input) : Decidable (Implements inp) := by
  unfold Implements; infer_instance

/-- The checker: the decision procedure for `Implements`, and nothing else. -/
def check (inp : Input) : Bool := decide (Implements inp)

/-! ## Diagnosis

Which conjunct failed.  Not part of the trusted claim -- `check` alone is -- but a checker
that says only "no" is a checker nobody runs twice. -/
def diagnose (inp : Input) : List String :=
  let add (b : Bool) (s : String) : List String := if b then [] else [s]
  (add (decide (inp.unrealised = [])) "some circuit ops were never realised")
    ++ add (decide (MapInjective inp)) "two qubits share an ion"
    ++ add (decide (MapTotal inp)) "the qubit map does not cover the circuit"
    ++ add (decide (EventsSorted inp)) "the movement is not in cycle order"
    ++ add (decide (RotationsKnown inp)) "a rotation names a loop the architecture lacks"
    ++ add (decide (MovesAreHops inp))
         "a move is not a one-cycle hop, or departs from the wrong trap"
    ++ add (decide (GatesLegal inp))
         "a gate's operands are not co-located at a gate-capable trap"
    ++ add (decide (Covered inp)) "a circuit op has no witness, or has several"
    ++ add (decide (Grounded inp)) "a gate witness names a circuit op that does not exist"
    ++ add (decide (RespectsOrder inp)) "two ops sharing a qubit are out of order"

end QCCDC.Cert
