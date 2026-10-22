import QCCDC.Cert.Sound

/-!
# Gates from places, and the T gadget that was not T

*Part J, lesson J4. You need lessons J1 and J2 (places, and the rules a machine of places
obeys), and the first lesson of Part G helps (exact numbers). Everything here is plain Lean:
integers, pairs and `decide`.*

A fault-tolerant machine has no T gate. It has places -- a factory that distils a magic
state, an injection place that turns it into a logical block, a bridge that measures `Z̄Z̄`
between two blocks by lattice surgery, a post office that reads a block out -- and a
**gate** is a little program of places. This one is the T gate:

```
gate t q implements T:
  inject m          # the factory's magic state, as a block
  x m               # a Pauli kept in software: no ion is touched
  zz q m -> a       # Z̄Z̄ between the data block and the magic block
  read m X -> b     # read the magic block out in X
  decode q m
  if a: s q         # the S̄ correction, only when a = 1
  if b: z q         # the Z̄ correction, a line in the classical memory
```

The example program `t_gate` runs these lines on the town. Here it is at the lattice surgery,
`zz q m -> a` -- the factory has made the state, the pharmacy has injected it, and a bridge
measures the two blocks together:

{{gadget algorithms/t_gate focus=i3}}

Every place in it is verified on its own, as hardware and as logic. What no place can tell you
is whether this *program* is a T gate. That depends on the state the factory makes, and on the
corrections, in every one of the four branches `(a, b)`. The site checks it with its
**contract checker**, an exact simulation of the gate in every branch; this lesson does it
in Lean, exactly.

It is not an academic point. The T gadget as it first shipped had no `x m` line: it assumed
the magic block holds `T|+⟩`. The factory is verified to make `T†|+⟩`. With the real state that
program is T† in two branches and S·T in the other two -- never T. Neither the hardware
checks nor the end-to-end sign-off could see it, because both treat the injected state as a
free input. The contract checker did. By the end of this lesson you will have proved both the
bug and the fix.
-/

/-!
## 1. Exact numbers: ℤ[ω]

The amplitudes we need are built from `ω = e^{iπ/4}`, the eighth root of unity. Every number
`a + bω + cω² + dω³` with integer `a, b, c, d` can be stored exactly as four integers, because
`ω⁴ = -1` folds every higher power back down. That set is written `ℤ[ω]`. Two of its
elements are the numbers quantum gates are made of: `i = ω²` and `√2 = ω - ω³`.
-/

structure Zw where
  a : Int
  b : Int
  c : Int
  d : Int
deriving DecidableEq, Repr

namespace Zw

def zero : Zw := ⟨0, 0, 0, 0⟩
def one : Zw := ⟨1, 0, 0, 0⟩
def w : Zw := ⟨0, 1, 0, 0⟩

instance : Add Zw := ⟨fun x y => ⟨x.a + y.a, x.b + y.b, x.c + y.c, x.d + y.d⟩⟩
instance : Neg Zw := ⟨fun x => ⟨-x.a, -x.b, -x.c, -x.d⟩⟩

/-- `(a₀ + a₁ω + a₂ω² + a₃ω³)(b₀ + b₁ω + b₂ω² + b₃ω³)`, every `ω⁴` replaced by `-1`. -/
instance : Mul Zw := ⟨fun x y =>
  ⟨x.a * y.a - x.b * y.d - x.c * y.c - x.d * y.b,
   x.a * y.b + x.b * y.a - x.c * y.d - x.d * y.c,
   x.a * y.c + x.b * y.b + x.c * y.a - x.d * y.d,
   x.a * y.d + x.b * y.c + x.c * y.b + x.d * y.a⟩⟩

def pow (x : Zw) : Nat → Zw
  | 0 => one
  | n + 1 => x * pow x n

/-- `i = ω²` and `√2 = ω - ω³`. -/
def i : Zw := ⟨0, 0, 1, 0⟩
def sqrt2 : Zw := ⟨0, 1, 0, -1⟩

end Zw

open Zw

#eval Zw.pow w 4
#eval sqrt2 * sqrt2

/-!
#### Exercise 1 (★) `w_pow_eight`
`ω` is an eighth root of unity. Prove it. (`decide` evaluates both sides and compares them.)
-/

theorem w_pow_eight : Zw.pow w 8 = one := by
  decide

/-!
#### Exercise 2 (★) `i_and_sqrt2`
Prove that `i² = -1` and `√2·√2 = 2`, in one statement.
-/

theorem i_and_sqrt2 : i * i = -one ∧ sqrt2 * sqrt2 = ⟨2, 0, 0, 0⟩ := by
  decide

/-!
## 2. One qubit, one gate, up to a phase

A qubit's state is two amplitudes, for `|0⟩` and `|1⟩`. We never divide: `|+⟩ = (|0⟩ + |1⟩)/√2`
is stored as `(1, 1)`, its overall size forgotten. That is safe because a gadget's output is
compared with a gate **up to a scalar**: two matrices `M` and `N` describe the same gate when
one is a nonzero multiple of the other (both are unitary, so the multiple is a phase). That
needs no division either: `M ∝ N` exactly when every *cross product* agrees,
`M[r][c] · N[r'][c'] = M[r'][c'] · N[r][c]`, and `M` is not zero.

A one-qubit operator is stored as its two columns: what it does to `|0⟩` and to `|1⟩`.
-/

abbrev V := Zw × Zw            -- amplitudes of |0⟩ and |1⟩
abbrev Op := V × V             -- the images of |0⟩ and |1⟩

def entry (M : Op) (r c : Bool) : Zw :=
  let col := if c then M.2 else M.1
  if r then col.2 else col.1

def bools : List Bool := [false, true]

/-- `M` is `N` up to a nonzero scalar. -/
def propTo (M N : Op) : Bool :=
  (bools.all fun r => bools.all fun c => bools.all fun r' => bools.all fun c' =>
    entry M r c * entry N r' c' == entry M r' c' * entry N r c)
  && bools.any fun r => bools.any fun c => entry M r c != zero

def diag (x y : Zw) : Op := ((x, zero), (zero, y))

def Tm : Op := diag one w                       -- T  = diag(1, ω)
def Tdgm : Op := diag one ⟨0, 0, 0, -1⟩          -- T† = diag(1, ω⁷) = diag(1, -ω³)
def Sm : Op := diag one i                       -- S  = diag(1, i)

/-!
## 3. The gadget, branch by branch

The data block holds `|j⟩` (we follow both basis states, which is the whole operator). The
magic block holds `(m₀, m₁)`. The surgery measures `Z̄Z̄` with outcome `a`: it keeps the part
of the state where the data bit and the magic bit differ by `a`, so the magic bit must be
`j ⊕ a`. Reading the magic block in X with outcome `b` projects it on `|0⟩ + (-1)^b |1⟩`,
which multiplies by `-1` when `b` and the magic bit are both 1. What is left is on the data
block alone, at `|j⟩`.
-/

def sign (b : Bool) (x : Zw) : Zw := if b then -x else x

/-- The data block after the surgery and the readout, from `|j⟩`, before any correction. -/
def raw (m : V) (a b j : Bool) : V :=
  let g := xor j a
  let amp := sign (b && g) (if g then m.2 else m.1)
  if j then (zero, amp) else (amp, zero)

/-- The corrections: `s` on `|1⟩` when `a` (the S̄ gadget, or S̄†), then Z̄ when `b`. -/
def correct (s : Zw) (a b : Bool) (v : V) : V :=
  let v1 := if a then s * v.2 else v.2
  (v.1, sign b v1)

/-- The whole operator of branch `(a, b)`: its image of `|0⟩` and of `|1⟩`. -/
def gadget (m : V) (s : Zw) (a b : Bool) : Op :=
  (correct s a b (raw m a b false), correct s a b (raw m a b true))

/-!
The factory's output, as `msf15.produce` is verified to make it (a state-vector check of the
15-to-1 protocol in the place library): `T†|+⟩`, which up to `1/√2` is `(1, ω⁷)`.
`X̄` on the magic block swaps its two amplitudes -- in the program it is the line `x m`, which
costs nothing.
-/

def factory : V := (one, ⟨0, 0, 0, -1⟩)          -- T†|+⟩ = (1, ω⁷), ω⁷ = -ω³
def xFlip (m : V) : V := (m.2, m.1)

#eval gadget (xFlip factory) i true false

/-!
#### Exercise 3 (★★) `t_gadget_is_t`
The T gate as it ships now: `x m` first, the S̄ correction when `a`, the Z̄ when `b`. Prove that
every branch is T.
-/

theorem t_gadget_is_t : ∀ a b : Bool, propTo (gadget (xFlip factory) i a b) Tm = true := by
  decide

/-!
#### Exercise 4 (★★) `t_as_first_shipped`
Without `x m`, the magic block holds the factory's state as it is. Prove that the branch
`a = 1, b = 0` of that program is not T.
-/

theorem t_as_first_shipped : propTo (gadget factory i true false) Tm = false := by
  decide

/-!
#### Exercise 5 (★★) `never_t`
Worse: prove that it is T in **no** branch.
-/

theorem never_t : ∀ a b : Bool, propTo (gadget factory i a b) Tm = false := by
  decide

/-!
#### Exercise 6 (★★) `tdg_gadget`
The factory's state is exactly what a T† needs. With `S̄†` (multiply `|1⟩` by `-i`) as the
correction, prove that every branch is T†. (In the standard gates this is `tdg`, whose
correction is `if a: sdg q`.)
-/

theorem tdg_gadget : ∀ a b : Bool, propTo (gadget factory (-i) a b) Tdgm = true := by
  decide

/-!
## 4. Only one magic state works

The magic block could hold any of the eight states `(1, ωᵏ)`. Which of them make the program
`x m`-free T gadget -- surgery, readout, `S̄` when `a`, `Z̄` when `b` -- a T gate in every
branch? There are eight states and four branches: `decide` can try them all.
-/

def magic (k : Nat) : V := (one, Zw.pow w k)

/-!
#### Exercise 7 (★★★) `only_t_plus`
Prove that the gadget (without `x m`) is T in every branch exactly for `k = 1`, the state
`T|+⟩` -- which is what the first version assumed, and not what the factory makes (`k = 7`).
-/

theorem only_t_plus :
    ∀ k : Fin 8, (bools.all fun a => bools.all fun b => propTo (gadget (magic k) i a b) Tm)
      = decide (k.val = 1) := by
  decide

/-!
## 5. The S̄ gadget is S

The `s` instruction is itself a composite: a `|Ȳ⟩` block from the injection place
(`(1, i)` up to scale), a `Z̄Z̄` surgery, an `X̄` readout of the `|Ȳ⟩` block, and a `Z̄` correction
-- the scheduler writes it into the frame -- when the two outcomes differ. Our `gadget` already
computes surgery and readout; the S̄ gadget's correction is not "`s` when `a`" but
"Z̄ when `a ⊕ b`".
-/

def sGadget (a b : Bool) : Op :=
  let v0 := raw (one, i) a b false
  let v1 := raw (one, i) a b true
  let fix (v : V) : V := (v.1, sign (xor a b) v.2)
  (fix v0, fix v1)

/-!
#### Exercise 8 (★★) `s_gadget_is_s`
Prove that every branch of the S̄ gadget is S.
-/

theorem s_gadget_is_s : ∀ a b : Bool, propTo (sGadget a b) Sm = true := by
  decide

/-!
#### Exercise 9 (★★) `s_gadget_needs_its_correction`
And that the correction matters: with no correction at all, some branch is not S.
-/

theorem s_gadget_needs_its_correction :
    ∃ a b : Bool, propTo (raw (one, i) a b false, raw (one, i) a b true) Sm = false := by
  exact ⟨false, true, by decide⟩

/-!
## What this proved, and what the site proves

You checked, exactly and in every branch, the statements the contract checker makes about the
standard gates: the S̄ gadget is S, `t` is T, `tdg` is T†, and the T gadget without `x m` is
never T. The site's checker does the same for every gate a program defines -- `h` (S̄·√X̄·S̄),
`ccx` (the seven-T Toffoli), `cswap` -- simulating each body with its parameters entangled with
reference qubits, branch by branch, and counting a call inside a body as the contract it has
already proved. That is what lets a program of a thousand instructions be checked gate by gate
instead of all at once (lesson J5).
-/
