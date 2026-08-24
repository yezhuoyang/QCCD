/-
`qcheck` -- the trusted checker, as a program.

`Compiler/PLAN.md` §8 argues for this shape: the checker is written in Lean, proved sound
there, and COMPILED, so there is no extraction step and nothing has to be trusted twice.
The OCaml checker in `Compiler/ocaml/lib/cert.ml` exists only as a fast pre-flight during
compilation; this is the one whose verdict R10 is allowed to rest on.
-/
import QCCDC.Cert.Sound
import Lean.Data.Json

open QCCDC.Cert Lean

def main (args : List String) : IO UInt32 := do
  match args with
  | [path] =>
    let text ← IO.FS.readFile path
    match Json.parse text with
    | .error e =>
      IO.eprintln s!"qcheck: {path}: not JSON: {e}"
      pure 2
    | .ok j =>
      let inp := Input.ofJson j
      IO.println s!"qcheck {inp.circuit} on {inp.arch}"
      IO.println s!"  {inp.ops.length} circuit ops, {inp.gates.length} gate witnesses, \
{inp.moves.length} moves, {inp.rots.length} rotations, {inp.nQubits} qubits"
      if check inp then
        IO.println "  ACCEPTED -- the compiled program implements the circuit"
        IO.println "  (QCCDC.Cert.check_sound; R10 may be reported as passed)"
        pure 0
      else
        IO.println "  REJECTED"
        for d in diagnose inp do
          IO.println s!"    - {d}"
        pure 1
  | _ =>
    IO.eprintln "usage: qcheck <qcheck-input.json>"
    pure 2
