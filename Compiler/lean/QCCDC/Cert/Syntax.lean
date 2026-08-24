/-
# The certificate, as the trusted checker reads it

Obligation O1 of `Compiler/PLAN.md` §1: every gate the compiled program executes must
find its operands already in one trap, and that trap must be able to gate.

## What is in here, and what is deliberately not

The checker is meant to be small.  `Compiler/PLAN.md` §2 puts a number on it -- past about
500 lines it has stopped being a checker and become a second compiler -- so anything that
tempts it to grow belongs in the certificate as a witness it merely validates.

Two things travel with the certificate that might look like conveniences and are not:

* **the circuit's op list.**  The checker DERIVES the dependency order from it rather than
  being told the order is right.  A certificate that merely asserted "these gates are
  correctly sequenced" would be the compiler grading its own homework.
* **the architecture's facts** -- which traps can gate, and which pairs of traps are one
  cycle apart.  These are *not* supplied by the compiler: `bridge/mk_qcheck_input.py`
  reads them out of the architecture document itself, so a compiler cannot widen the set
  of legal moves or of gate-capable traps by asserting it.

Only Lean core is imported.  Nothing here needs Mathlib, and keeping it out means this
file rebuilds in seconds rather than minutes -- which matters for a checker that is meant
to be run, not admired.
-/
import Lean.Data.Json

namespace QCCDC.Cert

open Lean

/-- One operation of the source circuit, in program order. -/
structure Op where
  idx : Nat
  name : String
  qubits : List Nat
  deriving Repr, DecidableEq, Inhabited

/-- One ion hop, at the machine cycle it departs in. -/
structure Move where
  t : Nat
  ion : String
  src : String
  dst : String
  deriving Repr, DecidableEq, Inhabited

/-- A rigid rotation of a named loop: at cycle `t`, every ion standing on that loop
advances by `delta` slots along it.

This is one witness rather than a move per ion per unit hop.  Expanding it would need no
new case here -- but a BB[[144,12,12]] round expands to 113 472 moves, and a replay that
is a fold over the move list then costs O(moves) per lookup and never finishes.  The
loop's node order is read out of the ARCHITECTURE by `bridge/mk_qcheck_input.py`, exactly
as `hops` and `gate_sites` are, so this buys speed without letting the compiler assert
anything new. -/
structure Rot where
  t : Nat
  loop : String
  delta : Int
  deriving Repr, DecidableEq, Inhabited

/-- The claim that one circuit op was realised, at a cycle and a trap. -/
structure GateW where
  dag : Nat
  t : Nat
  site : String
  ions : List String
  deriving Repr, DecidableEq, Inhabited

/-- Everything the checker is given.  `gateSites` and `hops` come from the architecture
document, not from the compiler. -/
structure Input where
  circuit : String
  arch : String
  nQubits : Nat
  qmap : List (Nat × String)        -- logical qubit -> ion
  init : List (String × String)     -- ion -> its initial trap
  ops : List Op
  moves : List Move
  rots : List Rot
  gates : List GateW
  unrealised : List Nat
  gateSites : List String
  hops : List (String × String)     -- ordered pairs one cycle apart
  loops : List (String × List String)  -- loop id -> its nodes in cyclic order
  deriving Repr, Inhabited

/-! ## Reading it -/

private def str? (j : Json) : Option String := j.getStr?.toOption
private def nat? (j : Json) : Option Nat := j.getNat?.toOption

private def arr? (j : Json) : Array Json :=
  match j.getArr? with
  | .ok a => a
  | .error _ => #[]

private def field (j : Json) (k : String) : Json :=
  match j.getObjVal? k with
  | .ok v => v
  | .error _ => Json.null

private def natList (j : Json) : List Nat :=
  (arr? j).toList.filterMap nat?

private def strList (j : Json) : List String :=
  (arr? j).toList.filterMap str?

private def opOf (j : Json) : Option Op := do
  let i ← nat? (field j "i")
  let n ← str? (field j "name")
  pure { idx := i, name := n, qubits := natList (field j "qubits") }

private def moveOf (j : Json) : Option Move := do
  let t ← nat? (field j "t")
  let i ← str? (field j "ion")
  let s ← str? (field j "from")
  let d ← str? (field j "to")
  pure { t := t, ion := i, src := s, dst := d }

private def int? (j : Json) : Option Int :=
  match j.getInt? with
  | .ok i => some i
  | .error _ => none

private def rotOf (j : Json) : Option Rot := do
  let t ← nat? (field j "t")
  let l ← str? (field j "loop")
  let d ← int? (field j "delta")
  pure { t := t, loop := l, delta := d }

private def gateOf (j : Json) : Option GateW := do
  let dag ← nat? (field j "dag")
  let t ← nat? (field j "t")
  let s ← str? (field j "site")
  pure { dag := dag, t := t, site := s, ions := strList (field j "ions") }

private def pairOf (j : Json) : Option (String × String) := do
  let a ← str? (field j "from")
  let b ← str? (field j "to")
  pure (a, b)

private def assocNatStr (j : Json) : List (Nat × String) :=
  match j.getObj? with
  | .ok o =>
    o.toList.filterMap fun (k, v) =>
      match (k.toNat?, str? v) with
      | (some n, some s) => some (n, s)
      | _ => none
  | .error _ => []

private def assocStrStr (j : Json) : List (String × String) :=
  match j.getObj? with
  | .ok o => o.toList.filterMap fun (k, v) => (str? v).map (fun s => (k, s))
  | .error _ => []

/-- Parse the checker's input.  A field the document does not carry becomes empty, and
the checks below then fail on it -- silence is never taken for assent. -/
def Input.ofJson (j : Json) : Input :=
  { circuit := (str? (field j "circuit")).getD "?"
    arch := (str? (field j "arch")).getD "?"
    nQubits := (nat? (field j "n_qubits")).getD 0
    qmap := assocNatStr (field j "map")
    init := assocStrStr (field j "init")
    ops := (arr? (field j "circuit_ops")).toList.filterMap opOf
    moves := (arr? (field j "moves")).toList.filterMap moveOf
    rots := (arr? (field j "rotations")).toList.filterMap rotOf
    gates := (arr? (field j "gates")).toList.filterMap gateOf
    unrealised := natList (field j "unrealised")
    gateSites := strList (field j "gate_sites")
    hops := (arr? (field j "hops")).toList.filterMap pairOf
    loops :=
      match (field j "loops").getObj? with
      | .ok o => o.toList.map fun (k, v) => (k, strList v)
      | .error _ => [] }

end QCCDC.Cert
