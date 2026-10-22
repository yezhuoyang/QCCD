/-
# The specification, and its decision procedure

The pattern here is deliberate and worth naming, because it is what makes the soundness
theorem in `Sound.lean` mean something rather than being a tautology dressed up.

`Implements` is a **Prop**, written in terms of a replay of the certificate's movement from
the initial placement.  It never mentions the checker.  It says what it means for the
compiled program to follow its certificate -- qubits on distinct ions, every move a hop the
architecture admits, every op other than a measurement, reset or barrier witnessed exactly
once, by the ions its qubits are mapped to, which the replay has together in a trap that
can gate, any two ops on a common qubit in program order -- and it would say the same thing
if no checker existed.

It does not say that the program applies the circuit's gates.  A witness (`GateW`) names an
op, a cycle, a trap and ions; it names no gate, and the checker never reads the pulses the
program emits.  Which gate fires is decided by the semantic comparison in
`bridge/check_cert.py`, which composes the emitted pulses and compares them with the source
circuit, and R10 rests on the two together.  A compiler that turned a two-qubit `barrier`
or a docked `cz` into a CX (releases up to c7c535380bfb) was accepted here and refused
there.

`check` is then *nothing but* `decide (Implements inp)`.  So the theorem
`check inp = true → Implements inp` is `of_decide_eq_true`, and all of the content has
moved to where it belongs: into whether `Implements` is the right statement, which a reader
can judge by reading it.  A checker written as a pile of `Bool`s with a separately asserted
meaning is the arrangement that hides its assumptions; this one cannot.

The conjuncts are the whole claim:

  1. `unrealised = []`   -- the compiler declined no op it was given
  2. `MapInjective`      -- distinct qubits get distinct ions
  3. `MapTotal`          -- every qubit of the circuit is mapped
  4. `EventsSorted`      -- movement is in cycle order, so replaying it is well defined
  5. `RotationsKnown`    -- every rotation names a loop the ARCHITECTURE declares
  6. `MovesAreHops`      -- an ion never teleports: every move is one the architecture
                            admits, and departs from where the ion actually is
  7. `GatesLegal`        -- the ions a witness names are together at the trap it names,
                            and that trap can gate
  8. `OwnIons`           -- and they are its op's own ions: the ones the op's qubits are
                            mapped to, no more and no fewer, in any order
  9. `Covered`           -- every circuit op that needs a witness has exactly one
 10. `Grounded`          -- and every witness realises an op that exists
 11. `RespectsOrder`     -- two ops sharing a qubit happen in program order, whether or
                            not they commute

Together they are O1: a dropped gate fails coverage, an invented one fails grounding, a
witness that puts two other ions together in place of its op's fails `OwnIons`, and a
reordered pair -- a commuting one too -- fails the order check.  None of them is O3, the
Pauli frame, and none is gate identity; both are the semantic comparison's
(`Compiler/PLAN.md` D1).

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
import QCCDC.Cert.Perm

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

/-- Every ion a witness names is, in the replay, at the trap the witness names at its cycle,
and that trap can gate.

These are the ions the WITNESS names.  That they are its op's operands is `OwnIons`, the
next conjunct: on its own this one accepts a witness naming any ions that happen to stand
together. -/
def GatesLegal (inp : Input) : Prop :=
  let R := replay inp
  ∀ g ∈ inp.gates,
    g.site ∈ inp.gateSites ∧
    ∀ i ∈ g.ions, look (stateIn R g.t) i = some g.site

/-- The ion qubit `q` is mapped to, or `none` if the map does not have it. -/
def ionOf (inp : Input) (q : Nat) : Option String :=
  (inp.qmap.find? (fun p => p.1 = q)).map Prod.snd

/-- Each witness names its op's own ions: the ions its op's qubits are mapped to, no more
and no fewer, in any order.  With `GatesLegal`, that puts the op's operands -- not merely
some ions -- together in a trap that can gate.

`List.Perm` is "the same list, rearranged", so a one-qubit op's witness names exactly the
one ion its qubit is on, and a two-qubit op's names both of its ions, each once.  The order
is free because a witness's ions say where its op ran, not which operand plays which part:
the pulses say that, and the semantic half of R10 reads them (`Perm.lean` has the numbers).
A qubit the map does not have looks up to `none`, which no named ion is. -/
def OwnIons (inp : Input) : Prop :=
  ∀ g ∈ inp.gates, ∀ o ∈ inp.ops, o.idx = g.dag →
    (g.ions.map some).Perm (o.qubits.map (ionOf inp))

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
into a double one -- on a BB round, 750 000 pairs rather than 10^10.

It carries no gate name.  It used to, for the commutation exemption the order rule had until
2026-10-19 (`RespectsOrder`); the rule now reads only which qubits two ops share, so the
join carries only that. -/
structure WQ where
  idx : Nat            -- the op index, i.e. program order
  t : Nat              -- the cycle its witness claims
  qubits : List Nat
  deriving Repr, DecidableEq, Inhabited

def witnessQubits (inp : Input) : List WQ :=
  inp.gates.filterMap fun g =>
    (inp.ops.find? (fun o => o.idx = g.dag)).map fun o =>
      { idx := g.dag, t := g.t, qubits := o.qubits }

/-- Two ops sharing a qubit are realised in program order: the earlier op's witness at a
strictly earlier cycle than the later one's.  There is no exemption, not even for two ops
that commute.

This is where a reordering shows up, and it is strict by the user's decision of
2026-10-19: the order of a qubit's gates is the circuit author's, and the compiler keeps
it.  Until that day the rule let through the two reorderings `Commute.lean` proves harmless
-- two `cx` sharing a control, or sharing a target -- and the rotation compiler used the
gap to serve a syndrome round's fan of CNOTs in whatever order the loop brought the
ancillas round.  The exemption was true as a statement about unitaries, and that is
exactly why it had to go.  Swapping two commuting CNOTs gives the same unitary, so the
semantic half of R10 (`bridge/check_cert.py`, which composes the emitted pulses and
compares them with the circuit) cannot see the swap at all; this clause is the only place
in either half where such a reorder can be caught, and a rule that admits it here admits
it everywhere.  And the order can matter where the unitary cannot show it: in a syndrome
round, the order of an ancilla's CNOTs decides which data qubits a single fault on that
ancilla spreads to, which is a choice the circuit's author makes and the compiler keeps.

What keeps it honest is unchanged: the precedence is DERIVED from the op list, so a
compiler cannot supply a permissive one, and nothing in the certificate can claim that a
pair is exempt, because no pair is. -/
def RespectsOrder (inp : Input) : Prop :=
  let W := witnessQubits inp
  ∀ a ∈ W, ∀ b ∈ W, a.idx < b.idx →
    (a.qubits.any (fun q => b.qubits.contains q)) = true → a.t < b.t

/-- **The specification.**  What it means for the compiled program to follow its
certificate -- mapping, transport, co-location of each op's own ions, and order -- said
without reference to any checker.  It names no gate, so it is not by itself "the program
implements the circuit": which gates the program applies is decided by comparing its
emitted pulses with the source circuit (`bridge/check_cert.py`), and R10 needs both. -/
def Implements (inp : Input) : Prop :=
  inp.unrealised = [] ∧
  MapInjective inp ∧
  MapTotal inp ∧
  EventsSorted inp ∧
  RotationsKnown inp ∧
  MovesAreHops inp ∧
  GatesLegal inp ∧
  OwnIons inp ∧
  Covered inp ∧
  Grounded inp ∧
  RespectsOrder inp

/-! ## Decidability

The replay is computed ONCE per check, as the header says.  `MovesAreHops`, `GatesLegal` and
`RespectsOrder` bind their replay (or their witness join) with a `let`; decided through
`unfold; infer_instance`, that `let` landed inside the compiled per-move and per-operand
functions, so a BB round was replayed about 3 700 times and one check took ten minutes
(2026-09-26).  So each of the three is also stated with what it binds as a PARAMETER
(`...In`), the same proposition by definition (`Iff.rfl`), and the instances decide that
form with the replay computed once and passed in.  What `check` decides is unchanged:
`Implements` is as written above, and `check_sound` is still `of_decide_eq_true`. -/

/-- A replay: the snapshots, and the end state (`replay`'s type). -/
abbrev Replay := List (Nat × List (String × String)) × List (String × String)

/-- `MovesAreHops` over a given replay. -/
def MovesAreHopsIn (inp : Input) (R : Replay) : Prop :=
  ∀ m ∈ inp.moves,
    (m.src, m.dst) ∈ inp.hops ∧ look (stateIn R m.t) m.ion = some m.src

/-- `GatesLegal` over a given replay. -/
def GatesLegalIn (inp : Input) (R : Replay) : Prop :=
  ∀ g ∈ inp.gates,
    g.site ∈ inp.gateSites ∧
    ∀ i ∈ g.ions, look (stateIn R g.t) i = some g.site

/-- `RespectsOrder` over a given witness join. -/
def RespectsOrderIn (W : List WQ) : Prop :=
  ∀ a ∈ W, ∀ b ∈ W, a.idx < b.idx →
    (a.qubits.any (fun q => b.qubits.contains q)) = true → a.t < b.t

/-- The specification with the replay and the witness join as parameters. -/
def ImplementsIn (inp : Input) (R : Replay) (W : List WQ) : Prop :=
  inp.unrealised = [] ∧
  MapInjective inp ∧
  MapTotal inp ∧
  EventsSorted inp ∧
  RotationsKnown inp ∧
  MovesAreHopsIn inp R ∧
  GatesLegalIn inp R ∧
  OwnIons inp ∧
  Covered inp ∧
  Grounded inp ∧
  RespectsOrderIn W

theorem movesAreHops_iff (inp : Input) : MovesAreHops inp ↔ MovesAreHopsIn inp (replay inp) := Iff.rfl
theorem gatesLegal_iff (inp : Input) : GatesLegal inp ↔ GatesLegalIn inp (replay inp) := Iff.rfl
theorem respectsOrder_iff (inp : Input) : RespectsOrder inp ↔ RespectsOrderIn (witnessQubits inp) := Iff.rfl
theorem implements_iff (inp : Input) :
    Implements inp ↔ ImplementsIn inp (replay inp) (witnessQubits inp) := Iff.rfl

instance (inp : Input) : Decidable (MapInjective inp) := by
  unfold MapInjective; infer_instance

instance (inp : Input) : Decidable (MapTotal inp) := by
  unfold MapTotal; infer_instance

instance (inp : Input) : Decidable (EventsSorted inp) := by
  unfold EventsSorted; infer_instance

instance (inp : Input) : Decidable (RotationsKnown inp) := by
  unfold RotationsKnown; infer_instance

instance (inp : Input) (R : Replay) : Decidable (MovesAreHopsIn inp R) := by
  unfold MovesAreHopsIn; infer_instance

instance (inp : Input) (R : Replay) : Decidable (GatesLegalIn inp R) := by
  unfold GatesLegalIn; infer_instance

instance (W : List WQ) : Decidable (RespectsOrderIn W) := by
  unfold RespectsOrderIn; infer_instance

instance (inp : Input) : Decidable (MovesAreHops inp) :=
  decidable_of_iff _ (movesAreHops_iff inp).symm

instance (inp : Input) : Decidable (GatesLegal inp) :=
  decidable_of_iff _ (gatesLegal_iff inp).symm

/-- One witness against one op.  Written out rather than inferred, for two reasons: the
permutation is decided by `decPerm` (`Perm.lean`), whose proof needs only `propext`, and
never by core's `List.decidablePerm`, which needs `Classical.choice`; and it is compared
only when the op carries the witness's index, so the check costs one index comparison per
op and witness, not one permutation. -/
def ownIonsAt (inp : Input) (g : GateW) (o : Op) :
    Decidable (o.idx = g.dag → (g.ions.map some).Perm (o.qubits.map (ionOf inp))) :=
  if h : o.idx = g.dag then
    match decPerm (g.ions.map some) (o.qubits.map (ionOf inp)) with
    | isTrue hp => isTrue fun _ => hp
    | isFalse hp => isFalse fun f => hp (f h)
  else isTrue fun e => absurd e h

instance (inp : Input) : Decidable (OwnIons inp) :=
  @List.decidableBAll _ _ (fun g => @List.decidableBAll _ _ (ownIonsAt inp g) inp.ops) inp.gates

instance (inp : Input) : Decidable (Covered inp) := by
  unfold Covered; infer_instance

instance (inp : Input) : Decidable (Grounded inp) := by
  unfold Grounded; infer_instance

instance (inp : Input) : Decidable (RespectsOrder inp) :=
  decidable_of_iff _ (respectsOrder_iff inp).symm

instance (inp : Input) (R : Replay) (W : List WQ) : Decidable (ImplementsIn inp R W) := by
  unfold ImplementsIn; infer_instance

/-- One replay and one witness join for the whole check. -/
instance (inp : Input) : Decidable (Implements inp) :=
  decidable_of_iff _ (implements_iff inp).symm

/-- The checker: the decision procedure for `Implements`, and nothing else. -/
def check (inp : Input) : Bool := decide (Implements inp)

/-! ## Diagnosis

Which conjunct failed.  Not part of the trusted claim -- `check` alone is -- but a checker
that says only "no" is a checker nobody runs twice. -/

/-- The first pair `RespectsOrder` refuses, named: an op, a later op sharing a qubit with
it, and the cycles their witnesses claim.  The witness join `W` is a parameter so that it is
computed once, by the caller.  Diagnosis only; `check` never runs it. -/
def orderWhy (inp : Input) (W : List WQ) : String :=
  let nameOf (i : Nat) : String := ((inp.ops.find? (fun o => o.idx = i)).map Op.name).getD "?"
  let bad := W.findSome? fun a =>
    (W.find? fun b => decide (a.idx < b.idx) && a.qubits.any (fun q => b.qubits.contains q)
        && !decide (a.t < b.t)).map fun b => (a, b)
  match bad with
  | some (a, b) =>
    s!"two ops sharing a qubit are out of order: op {a.idx} ({nameOf a.idx} {a.qubits}) comes \
before op {b.idx} ({nameOf b.idx} {b.qubits}) in the circuit, but its witness is at cycle {a.t}, \
not before {b.t}"
  | none => "two ops sharing a qubit are out of order"

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
         "a gate witness's ions are not together at a gate-capable trap"
    ++ add (decide (OwnIons inp))
         "a gate witness names other ions than the ones its op's qubits are mapped to"
    ++ add (decide (Covered inp)) "a circuit op has no witness, or has several"
    ++ add (decide (Grounded inp)) "a gate witness names a circuit op that does not exist"
    ++ (if decide (RespectsOrder inp) then [] else [orderWhy inp (witnessQubits inp)])

end QCCDC.Cert
