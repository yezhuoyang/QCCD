/-
# The certificate, as the trusted checker reads it

Obligation O1 of `Compiler/PLAN.md` §1, as the certificate carries it: every circuit op
that needs one has exactly one gate witness, the ions that witness names are the ones the
op's qubits are mapped to (`qmap`), they are already in one trap at its cycle, and that
trap must be able to gate.  A witness (`GateW`) names an op, a cycle, a trap and ions --
never a gate, and never the pulses the program emits -- so which gate the compiled program
executes is not read here; `bridge/check_cert.py` decides it by composing the emitted
pulses and comparing them with the source circuit.

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

/-- The claim that one circuit op was realised, at a cycle and a trap, on `ions` -- which
`OwnIons` (`Check.lean`) requires to be the ions the op's qubits are mapped to, in any
order. -/
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

/-! ## Reading it strictly

`Input.ofJson` reads what it can: a malformed entry is dropped and a missing field is empty.
That is what a page wants that shows a certificate, and it is not what a checker may do --
a move whose `t` is `20.0` dropped is a programme with one move fewer, and `{}` read as empty
is a programme with nothing to check, which `check` then accepts.  So `qcheck` reads with
`Input.parse`: every field the checker relies on must be present and of its type, and every
entry of every list must read -- the lists inside an entry too (an op's qubits, a witness's
ions) -- or the file is refused, naming the first thing that is wrong and why. -/

/-- What a field must be: a whole number, an integer, a string, or a list of either. -/
private inductive Kind where
  | nat | int | str | nats | strs

private def Kind.says : Kind → String
  | .nat => "a whole number"
  | .int => "an integer"
  | .str => "a string"
  | .nats => "a list of whole numbers"
  | .strs => "a list of strings"

private def natList? (j : Json) : Option (List Nat) :=
  match j.getArr? with
  | .ok a => a.toList.mapM nat?
  | .error _ => none

private def strList? (j : Json) : Option (List String) :=
  match j.getArr? with
  | .ok a => a.toList.mapM str?
  | .error _ => none

private def Kind.reads (v : Json) : Kind → Bool
  | .nat => (nat? v).isSome
  | .int => (int? v).isSome
  | .str => (str? v).isSome
  | .nats => (natList? v).isSome
  | .strs => (strList? v).isSome

/-- Why the entry `x` does not read as `fields`: the first field missing or not of its kind. -/
private def why (fields : List (String × Kind)) (x : Json) : String :=
  match fields.find? (fun (k, kind) =>
      match x.getObjVal? k with
      | .ok v => !kind.reads v
      | .error _ => true) with
  | some (k, kind) =>
    match x.getObjVal? k with
    | .ok _ => s!"\"{k}\" is not {kind.says}"
    | .error _ => s!"\"{k}\" is missing"
  | none => "it is not an object"

private def need (j : Json) (k : String) : Except String Json :=
  match j.getObjVal? k with
  | .ok v => .ok v
  | .error _ => .error s!"the field \"{k}\" is missing"

private def eachGo {α : Type} (what : String) (f : Json → Option α) (explain : Json → String) :
    Nat → List Json → Array α → Except String (List α)
  | _, [], acc => .ok acc.toList
  | i, x :: xs, acc =>
    match f x with
    | some y => eachGo what f explain (i + 1) xs (acc.push y)
    | none => .error s!"{what}[{i}] is malformed: {explain x}: {x.compress}"

/-- The list under `k`: every entry read by `f`.  An optional field that is absent is empty. -/
private def listField {α : Type} (j : Json) (k : String) (f : Json → Option α)
    (explain : Json → String) (optional : Bool := false) : Except String (List α) :=
  match j.getObjVal? k with
  | .ok v =>
    match v.getArr? with
    | .ok a => eachGo k f explain 0 a.toList #[]
    | .error _ => .error s!"\"{k}\" is not a list"
  | .error _ => if optional then .ok [] else .error s!"the field \"{k}\" is missing"

private def objField (j : Json) (k : String) (optional : Bool := false) :
    Except String (List (String × Json)) :=
  match j.getObjVal? k with
  | .ok v =>
    match v.getObj? with
    | .ok o => .ok o.toList
    | .error _ => .error s!"\"{k}\" is not an object"
  | .error _ => if optional then .ok [] else .error s!"the field \"{k}\" is missing"

private def strField (j : Json) (k : String) : Except String String := do
  match str? (← need j k) with
  | some s => pure s
  | none => throw s!"\"{k}\" is not a string"

private def natField (j : Json) (k : String) : Except String Nat := do
  match nat? (← need j k) with
  | some n => pure n
  | none => throw s!"\"{k}\" is not a whole number"

/-- The entries, read strictly: the lists inside them must read whole as well. -/
private def opStrict (j : Json) : Option Op := do
  let i ← nat? (field j "i")
  let n ← str? (field j "name")
  let qs ← natList? (field j "qubits")
  pure { idx := i, name := n, qubits := qs }

private def gateStrict (j : Json) : Option GateW := do
  let dag ← nat? (field j "dag")
  let t ← nat? (field j "t")
  let s ← str? (field j "site")
  let ions ← strList? (field j "ions")
  pure { dag := dag, t := t, site := s, ions := ions }

/-- Parse the checker's input, refusing anything malformed (see above). -/
def Input.parse (j : Json) : Except String Input := do
  let circuit ← strField j "circuit"
  let arch ← strField j "arch"
  let nQubits ← natField j "n_qubits"
  let qmap ← (← objField j "map").mapM fun (k, v) =>
    match k.toNat?, str? v with
    | some n, some s => .ok (n, s)
    | none, _ => .error s!"map[\"{k}\"] is malformed: the qubit is not a whole number"
    | _, none => .error s!"map[\"{k}\"] is malformed: the ion is not a string: {v.compress}"
  let init ← (← objField j "init").mapM fun (k, v) =>
    match str? v with
    | some s => .ok (k, s)
    | none => .error s!"init[\"{k}\"] is malformed: the trap is not a string: {v.compress}"
  let loops ← (← objField j "loops" (optional := true)).mapM fun (k, v) =>
    match strList? v with
    | some ns => .ok (k, ns)
    | none => .error s!"loops[\"{k}\"] is malformed: it is not a list of strings: {v.compress}"
  pure { circuit, arch, nQubits, qmap, init, loops
         ops := ← listField j "circuit_ops" opStrict
                  (why [("i", .nat), ("name", .str), ("qubits", .nats)])
         moves := ← listField j "moves" moveOf
                  (why [("t", .nat), ("ion", .str), ("from", .str), ("to", .str)])
         rots := ← listField j "rotations" rotOf
                  (why [("t", .nat), ("loop", .str), ("delta", .int)]) (optional := true)
         gates := ← listField j "gates" gateStrict
                  (why [("dag", .nat), ("t", .nat), ("site", .str), ("ions", .strs)])
         unrealised := ← listField j "unrealised" nat? (fun _ => "it is not a whole number")
                  (optional := true)
         gateSites := ← listField j "gate_sites" str? (fun _ => "it is not a string")
         hops := ← listField j "hops" pairOf (why [("from", .str), ("to", .str)]) }

end QCCDC.Cert
