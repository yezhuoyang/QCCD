import QCCDC.Cert.Sound

/-!
# Exact numbers

*Part G, lesson G1. Part G assumes Parts E and F; you need at least lesson F1, whose last section
this Part takes up.*

Lesson F1 ended on a gap. The proved checker names no gate: a witness says *an operation happened
here, now, on these ions*, never *which* gate the laser pulses make. That second question is
answered by the semantic check, `check_cert`. It reads the native pulses out of the emitted
programme, composes them, and compares the result with the source circuit: with the stabilizer
library `stim` for a Clifford circuit, and otherwise, up to ten qubits by default, with the full
matrix in floating point, equal when every entry agrees to within 10⁻⁸ up to one global phase
(`same_up_to_phase`). It is computed, not proved.

Part G does that comparison in Lean, as a proof. The objects are matrices: a pulse on one ion is
a 2×2 matrix of complex numbers, the entangling pulse on two ions a 4×4 one, and their entries are
numbers like cos(π/4) and e^(iπ/4). The compiler's own pulse identities are proved in Lean with
Mathlib, whose real and complex numbers make that easy (the modules `QCCDC.Pulse.Native` and `QCCDC.Pulse.Decompose`). This page
has Lean's core only, which has whole numbers and no real numbers at all. So this lesson builds,
out of `Int`, exactly the numbers the pulses need, and makes equality between them something
Lean's kernel can decide.

## 1. Which numbers

For a circuit of Clifford+T gates (`h`, `s`, `t`, `x`, `cx`, `cz`, `swap`, `ccx` and their
relatives) every angle the compiler writes into a pulse is a whole multiple of π/4; lessons G3
and G4 show where each one comes from. On that grid every phase is a power of

  ω = e^(iπ/4) = (1 + i)/√2,

and every sine and cosine is one of 0, ±1, ±√2/2. Add and multiply such numbers and you never
leave the set of numbers

  (a + b·i + (c + d·i)·√2) / 2^k,   a, b, c, d whole numbers, k ≥ 0,

because i·i = -1 and √2·√2 = 2 turn every product back into that shape. This set is a ring;
its usual name is ℤ[ω, ½]. Here is one of its numbers as a Lean record. A `structure` with
`deriving DecidableEq` also gets, written by Lean, the program that decides whether two records
are equal field by field.
-/

/-- The number `(a + b·i + (c + d·i)·√2) / 2^k`. -/
structure Zω where
  a : Int
  b : Int
  c : Int
  d : Int
  k : Nat
  deriving DecidableEq

/-!
## 2. One spelling per number

The same number has many spellings: one half is `⟨1, 0, 0, 0, 1⟩` and also `⟨2, 0, 0, 0, 2⟩`.
Two records are equal only when every field is, so as records those two differ. To make equality
of records the same thing as equality of numbers, we keep exactly one spelling per number, the
one with the smallest power of two: while k > 0 and all four of a, b, c, d are even, halve them
and lower k.

When that stops, either k = 0 or one coefficient is odd, and no spelling of the number has a
smaller k. The reason is one fact about the four numbers 1, i, √2 and i√2: no whole-number
combination of them is 0 unless every coefficient is 0 (√2 is irrational and i is not real). So
for each k a number has at most one spelling, and halving all four is the only way to lower k.

`norm k a b c d` is that normal form. It is defined by pattern matching on `k`, one equation for
`0` and one for `k + 1`; each call lowers `k`, so Lean accepts that it stops. A `namespace`
groups names: inside `namespace Zω`, `norm` is `Zω.norm`.
-/

namespace Zω

/-- The normal form of `(a + b·i + (c + d·i)·√2) / 2^k`: halve while every coefficient is even. -/
def norm : Nat → Int → Int → Int → Int → Zω
  | 0, a, b, c, d => ⟨a, b, c, d, 0⟩
  | k + 1, a, b, c, d =>
    if a % 2 == 0 && b % 2 == 0 && c % 2 == 0 && d % 2 == 0 then
      norm k (a / 2) (b / 2) (c / 2) (d / 2)
    else ⟨a, b, c, d, k + 1⟩

/-!
## 3. Arithmetic

To add, bring both numbers to the larger power of two and add the coefficients. To multiply,
write each number as g + h·√2 with g = a + b·i and h = c + d·i; then

  (g + h√2)(g' + h'√2) = (g·g' + 2·h·h') + (g·h' + h·g')·√2,

and multiplying out the Gaussian products gives the four lines of `mul`; the powers of two add.
Negation flips every sign. The complex conjugate flips the sign of i, which leaves √2 alone.
Every operation that can produce an even spelling ends in `norm`, so every number they build is
in normal form.
-/

def add (x y : Zω) : Zω :=
  if x.k = y.k then norm x.k (x.a + y.a) (x.b + y.b) (x.c + y.c) (x.d + y.d)
  else if x.k < y.k then
    let s : Int := ((2 ^ (y.k - x.k) : Nat) : Int)
    norm y.k (x.a * s + y.a) (x.b * s + y.b) (x.c * s + y.c) (x.d * s + y.d)
  else
    let s : Int := ((2 ^ (x.k - y.k) : Nat) : Int)
    norm x.k (x.a + y.a * s) (x.b + y.b * s) (x.c + y.c * s) (x.d + y.d * s)

def mul (x y : Zω) : Zω :=
  norm (x.k + y.k)
    (x.a * y.a - x.b * y.b + 2 * (x.c * y.c - x.d * y.d))
    (x.a * y.b + x.b * y.a + 2 * (x.c * y.d + x.d * y.c))
    (x.a * y.c - x.b * y.d + x.c * y.a - x.d * y.b)
    (x.a * y.d + x.b * y.c + x.c * y.b + x.d * y.a)

def neg (x : Zω) : Zω := ⟨-x.a, -x.b, -x.c, -x.d, x.k⟩

/-- The complex conjugate: `i ↦ -i`, and `√2` is real. -/
def conj (x : Zω) : Zω := ⟨x.a, -x.b, x.c, -x.d, x.k⟩

def pow (x : Zω) : Nat → Zω
  | 0 => ⟨1, 0, 0, 0, 0⟩
  | n + 1 => mul (pow x n) x

/-!
An `instance` tells Lean what `+`, `*`, `-`, `^` and the numerals `0`, `1`, `2`, ... mean on
`Zω`. A numeral `n` is the whole number n: `⟨n, 0, 0, 0, 0⟩`, already in normal form.
-/

instance : Add Zω := ⟨add⟩
instance : Mul Zω := ⟨mul⟩
instance : Neg Zω := ⟨neg⟩
instance : Sub Zω := ⟨fun x y => add x (neg y)⟩
instance : Pow Zω Nat := ⟨pow⟩
instance : OfNat Zω n := ⟨⟨n, 0, 0, 0, 0⟩⟩

/-- The imaginary unit. -/
def I : Zω := ⟨0, 1, 0, 0, 0⟩
/-- √2. -/
def sqrt2 : Zω := ⟨0, 0, 1, 0, 0⟩
/-- 1/√2, which is √2/2. -/
def invSqrt2 : Zω := ⟨0, 0, 1, 0, 1⟩
/-- 1/2. -/
def half : Zω := ⟨1, 0, 0, 0, 1⟩
/-- ω = e^(iπ/4) = (1 + i)/√2 = (1 + i)·√2/2. -/
def ω : Zω := ⟨0, 0, 1, 1, 1⟩

/-!
So that `#eval` shows a number the way you would write it, here is a printer. It is for your
eyes only: no proof below uses it.
-/

def gaussStr (a b : Int) : String :=
  let im (b : Int) : String := if b == 1 then "i" else if b == -1 then "-i" else s!"{b}i"
  if b == 0 then s!"{a}" else if a == 0 then im b
  else if b > 0 then s!"{a} + {im b}" else s!"{a} - {im (-b)}"

def pretty (x : Zω) : String :=
  let g := gaussStr x.a x.b
  let r : String :=
    if x.c == 0 && x.d == 0 then ""
    else if x.d == 0 then (if x.c == 1 then "√2" else if x.c == -1 then "-√2" else s!"{x.c}√2")
    else if x.c == 0 then s!"{gaussStr 0 x.d}√2"
    else s!"({gaussStr x.c x.d})√2"
  let body := if r == "" then g else if x.a == 0 && x.b == 0 then r else s!"{g} + {r}"
  let sum := (r != "" && (x.a != 0 || x.b != 0)) || (r == "" && x.a != 0 && x.b != 0)
  if x.k == 0 then body else if sum then s!"({body})/{2 ^ x.k}" else s!"{body}/{2 ^ x.k}"

instance : Repr Zω := ⟨fun x _ => Std.Format.text (pretty x)⟩

end Zω

open Zω

/-!
## 4. Computing, and deciding

`#eval` computes and shows the answer. It proves nothing.
-/

#eval ω
#eval ω * ω
#eval ω ^ 3
#eval invSqrt2 + invSqrt2
#eval (⟨2, 0, 0, 0, 2⟩ : Zω) + 0

/-!
The last line is one half spelled badly; adding `0` ran it through `norm`.

A proof that two numbers are equal is a computation too. `decide +kernel` (lesson F1) has Lean's
kernel run the equality test that `deriving DecidableEq` wrote, on the normal forms the operations
produce, and accept the statement only if the answer is `true`. No floating point, no tolerance:
equal means equal.

#### Exercise 1 (★) `sqrt2_mul_sqrt2`
Prove √2 · √2 = 2.
-/

theorem sqrt2_mul_sqrt2 : sqrt2 * sqrt2 = 2 := by
  decide +kernel

/-!
#### Exercise 2 (★) `omega_sq`
Prove ω² = i: two eighths of a turn are a quarter turn.
-/

theorem omega_sq : ω * ω = I := by
  decide +kernel

/-!
#### Exercise 3 (★) `omega_pow_eight`
Prove ω⁸ = 1: eight eighths of a turn are a whole turn.
-/

theorem omega_pow_eight : ω ^ 8 = 1 := by
  decide +kernel

/-!
#### Exercise 4 (★) `gauss_div_sqrt2`
Prove (1 + i)/√2 = ω, dividing by √2 as multiplying by `invSqrt2`.
-/

theorem gauss_div_sqrt2 : (1 + I) * invSqrt2 = ω := by
  decide +kernel

/-!
#### Exercise 5 (★) `two_spellings`
Prove both halves: the normal form of 2/2 is 1, and the badly spelled record `⟨2, 0, 0, 0, 1⟩`
is not equal to `1` as a record. That second half is why every operation ends in `norm`: a
record you write by hand is trusted to be in normal form, and these lessons only write ones
that are.
-/

theorem two_spellings : Zω.norm 1 2 0 0 0 = 1 ∧ (⟨2, 0, 0, 0, 1⟩ : Zω) ≠ 1 := by
  decide +kernel

/-!
## 5. Angles on the grid

An angle n·π/4 is stored as the whole number `n`. Its phase e^(inπ/4) is ω^n, and since ω⁸ = 1
only n mod 8 matters. `cis n` (for *cos + i sin*) looks the phase up in a table of the eight
powers, written in normal form. Lean's `%` on `Int` is the remainder in `0 .. 7` here, also for
negative `n`, and `toNat` turns it into a `Nat` for the table.
-/

def cisT : Nat → Zω
  | 0 => 1
  | 1 => ω
  | 2 => I
  | 3 => ⟨0, 0, -1, 1, 1⟩
  | 4 => ⟨-1, 0, 0, 0, 0⟩
  | 5 => ⟨0, 0, -1, -1, 1⟩
  | 6 => ⟨0, -1, 0, 0, 0⟩
  | _ => ⟨0, 0, 1, -1, 1⟩

/-- e^(i·n·π/4). -/
def cis (n : Int) : Zω := cisT (n % 8).toNat

#eval (List.range 8).map fun (n : Nat) => cis n

/-!
A table is a claim; check it.

#### Exercise 6 (★) `cis_is_power`
Prove that the table is the powers of ω: for each of the eight `n`, `cis n = ω ^ n`. Over
`Fin 8` (the numbers below 8) a `∀` is eight cases, and `decide +kernel` runs them all.
-/

theorem cis_is_power : ∀ n : Fin 8, cis n = ω ^ (n : Nat) := by
  decide +kernel

/-!
The remaining exercises of this section are about every angle, not eight of them. `omega` is a
tactic that proves statements of linear arithmetic over `Int` and `Nat`, including `%` and `/`
by a fixed number: here, facts like `(n + 8) % 8 = n % 8`.

#### Exercise 7 (★★) `cis_periodic`
Prove that a whole turn changes nothing. Unfold `cis`, then rewrite the remainder with a fact
`omega` proves: `rw [show (n + 8) % 8 = n % 8 by omega]`.
-/

theorem cis_periodic (n : Int) : cis (n + 8) = cis n := by
  unfold cis
  rw [show (n + 8) % 8 = n % 8 by omega]

/-!
#### Exercise 8 (★★) `cis_neg`
Prove e^(-inπ/4) is the conjugate of e^(inπ/4), for every `n`. Hint: first prove the table fact
for the eight remainders (`key`, by `decide +kernel`); then `unfold cis`, apply `key` to the
remainder of `n`, and let `omega` show the two indices agree. `congr 1` reduces `f x = f y` to
`x = y`.
-/

theorem cis_neg (n : Int) : cis (-n) = conj (cis n) := by
  have key : ∀ m < 8, cisT ((8 - m) % 8) = conj (cisT m) := by decide +kernel
  unfold cis
  rw [← key (n % 8).toNat (by omega)]
  congr 1
  omega

/-!
#### Exercise 9 (★★★) `cis_add`
Prove that phases add, e^(iaπ/4)·e^(ibπ/4) = e^(i(a+b)π/4), for **every** pair of whole numbers.
Same method: a table fact for the 64 pairs of remainders, then `omega` to show that the
remainder of `a + b` is the remainder of the sum of the remainders.
-/

theorem cis_add (a b : Int) : cis a * cis b = cis (a + b) := by
  have key : ∀ x < 8, ∀ y < 8, cisT x * cisT y = cisT ((x + y) % 8) := by decide +kernel
  unfold cis
  rw [key _ (by omega) _ (by omega)]
  congr 1
  omega

/-!
## 6. A fact about every number

Lessons G4 and G7 refute claims of the form *these two matrices differ only by a phase c*,
for a `c` nobody names. They need one fact that holds for every record `c`, normal or not:
c · 0 = 0. It is not a computation, because `c` is unknown; it is a proof by induction.

#### Exercise 10 (★★★) `mul_zero_right`
First prove `norm_zero`: the normal form of 0/2^k is the zero record, by induction on `k`
(`simp [Zω.norm, ih]` does the step: the four remainders are 0, and 0/2 = 0). `show` restates
the goal with `0` spelled as its record, so that `simp` can see its fields; `simp [mul]` then
turns the product into `norm x.k 0 0 0 0`, since every term is a coefficient of `x` times 0.
-/

theorem mul_zero_right (x : Zω) : x * 0 = 0 := by
  have norm_zero : ∀ k, Zω.norm k 0 0 0 0 = ⟨0, 0, 0, 0, 0⟩ := by
    intro k
    induction k with
    | zero => rfl
    | succ k ih => simp [Zω.norm, ih]
  show mul x ⟨0, 0, 0, 0, 0⟩ = ⟨0, 0, 0, 0, 0⟩
  have h : mul x ⟨0, 0, 0, 0, 0⟩ = Zω.norm x.k 0 0 0 0 := by simp [mul]
  rw [h, norm_zero]

/-!
## 7. What this lesson built

A number of ℤ[ω, ½] is a record of four whole numbers and a power of two, kept in its one normal
form, so that `=` is decided by comparing fields; `decide +kernel` has the kernel do that, and is
a proof. Phases on the π/4 grid are a table of eight numbers, checked against the powers of ω.
Lesson G2 builds the native pulses out of them, as matrices, and compares them entry by entry with
the definitions the compiler's proofs use.
-/
