import QCCDC.Cert.Sound

/-!
# The native pulses

*Part G, lesson G2. You need lesson G1.*

A trapped-ion machine does three things to its qubits, and the compiler emits nothing else
(the semantic check, `check_cert`, calls them `NATIVE`: "the pulses the hardware executes, and so the only gates the
semantic half can compose"):

* `R(θ, φ)`, one physical laser pulse on one ion: a rotation by θ about an axis at angle φ in
  the XY plane of its Bloch sphere;
* `VZ(λ)`, a virtual Z rotation: the machine advances the phase of every later pulse on that ion;
  no laser fires and it takes no time;
* `MS(θ)`, the Mølmer-Sørensen pulse, which entangles two ions in one trap.

The platform's proofs define all three in the module `QCCDC.Pulse.Native`, with Mathlib's
complex matrices. This lesson writes them again as exact matrices over the numbers of lesson G1,
quotes each definition from `QCCDC.Pulse.Native` beside its twin, and checks the twins agree on the π/4
grid.
-/

-- setup
structure Zω where
  a : Int
  b : Int
  c : Int
  d : Int
  k : Nat
  deriving DecidableEq
namespace Zω
def norm : Nat → Int → Int → Int → Int → Zω
  | 0, a, b, c, d => ⟨a, b, c, d, 0⟩
  | k + 1, a, b, c, d =>
    if a % 2 == 0 && b % 2 == 0 && c % 2 == 0 && d % 2 == 0 then
      norm k (a / 2) (b / 2) (c / 2) (d / 2)
    else ⟨a, b, c, d, k + 1⟩
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
def conj (x : Zω) : Zω := ⟨x.a, -x.b, x.c, -x.d, x.k⟩
instance : Add Zω := ⟨add⟩
instance : Mul Zω := ⟨mul⟩
instance : Neg Zω := ⟨neg⟩
instance : Sub Zω := ⟨fun x y => add x (neg y)⟩
instance : OfNat Zω n := ⟨⟨n, 0, 0, 0, 0⟩⟩
def I : Zω := ⟨0, 1, 0, 0, 0⟩
def invSqrt2 : Zω := ⟨0, 0, 1, 0, 1⟩
def half : Zω := ⟨1, 0, 0, 0, 1⟩
def ω : Zω := ⟨0, 0, 1, 1, 1⟩
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
def cisT : Nat → Zω
  | 0 => 1
  | 1 => ω
  | 2 => I
  | 3 => ⟨0, 0, -1, 1, 1⟩
  | 4 => ⟨-1, 0, 0, 0, 0⟩
  | 5 => ⟨0, 0, -1, -1, 1⟩
  | 6 => ⟨0, -1, 0, 0, 0⟩
  | _ => ⟨0, 0, 1, -1, 1⟩
def cis (n : Int) : Zω := cisT (n % 8).toNat

/-!
*Lesson G1 is restated, unseen, above this line: the numbers `Zω` (a + b·i + (c + d·i)·√2)/2^k in
normal form, their arithmetic, the constants `I`, `invSqrt2` (that is √2/2), `half` and `ω`, and the
phase `cis n` = e^(inπ/4), an angle written as the whole number `n` of eighths of a turn.*

## 1. Matrices

A matrix is a list of rows. The product's row `i` is row `i` of `A` times `B`, that is the sum
over `k` of `A[i][k]` times row `k` of `B`. `c • A` multiplies every entry by `c`, and `dagger A`
is the conjugate transpose A†. For matrices over these numbers `=` is again decided by
comparing entries, each in its normal form.
-/

/-- A matrix: a list of rows. -/
abbrev Mat := List (List Zω)

def vadd : List Zω → List Zω → List Zω
  | x :: xs, y :: ys => (x + y) :: vadd xs ys
  | _, _ => []

def vscale (c : Zω) : List Zω → List Zω
  | [] => []
  | x :: xs => (c * x) :: vscale c xs

/-- A row times a matrix: the sum of `r[k]` times row `k`. -/
def rowMul : List Zω → Mat → List Zω
  | [x], [row] => vscale x row
  | x :: xs, row :: rows => vadd (vscale x row) (rowMul xs rows)
  | _, _ => []

def mmul (A B : Mat) : Mat := A.map (rowMul · B)

/-- Entry `j` of a row (0 past the end). -/
def nth : List Zω → Nat → Zω
  | [], _ => 0
  | x :: _, 0 => x
  | _ :: xs, n + 1 => nth xs n

/-- The conjugate transpose. -/
def dagger (A : Mat) : Mat :=
  (List.range (A.headD []).length).map fun j => A.map fun row => conj (nth row j)

instance : Mul Mat := ⟨mmul⟩
instance : Add Mat := ⟨List.zipWith vadd⟩
instance : SMul Zω Mat := ⟨fun c A => A.map (vscale c)⟩
instance : Sub Mat := ⟨fun A B => A + (-1 : Zω) • B⟩

def I2 : Mat := [[1, 0], [0, 1]]

/-!
## 2. The three Paulis

`QCCDC.Pulse.Native` starts from the Pauli matrices:

```
def PX : Matrix (Fin 2) (Fin 2) ℂ := !![0, 1; 1, 0]
def PY : Matrix (Fin 2) (Fin 2) ℂ := !![0, -Complex.I; Complex.I, 0]
def PZ : Matrix (Fin 2) (Fin 2) ℂ := !![1, 0; 0, -1]
```

`!![a, b; c, d]` is Mathlib's notation for the matrix with rows `a b` and `c d`. Ours:
-/

def PX : Mat := [[0, 1], [1, 0]]
def PY : Mat := [[0, -I], [I, 0]]
def PZ : Mat := [[1, 0], [0, -1]]

#eval PX * PY

/-!
That is i·Z: X·Y = iZ.

## 3. Sines and cosines on the grid

An angle is a whole number `n` of eighths of a turn, n·π/4. Its cosine is one of 0, ±1, ±√2/2;
`cosE` looks it up, and the sine is the cosine a quarter turn earlier, sin x = cos(x − π/2).
-/

def cosT : Nat → Zω
  | 0 => 1
  | 1 => invSqrt2
  | 2 => 0
  | 3 => -invSqrt2
  | 4 => -1
  | 5 => -invSqrt2
  | 6 => 0
  | _ => invSqrt2

/-- cos(n·π/4). -/
def cosE (n : Int) : Zω := cosT (n % 8).toNat
/-- sin(n·π/4) = cos(n·π/4 − π/2). -/
def sinE (n : Int) : Zω := cosE (n - 2)

/-!
#### Exercise 1 (★) `euler_grid`
Check both tables against the phases of lesson G1, by Euler's formulas cos x = (e^(ix) + e^(-ix))/2
and sin x = −(i/2)·(e^(ix) − e^(-ix)), at the eight angles.
-/

theorem euler_grid : ∀ n : Fin 8,
    cosE n = half * (cis n + cis (-n)) ∧ sinE n = -(half * I) * (cis n - cis (-n)) := by
  decide +kernel

/-!
#### Exercise 2 (★★) `sin_shift`
Prove sin(x + π/2) = cos x for every angle on the grid, not just eight: unfold `sinE`, then
`congr 1` and `omega` (lesson G1) for the angles.
-/

theorem sin_shift (n : Int) : sinE (n + 2) = cosE n := by
  unfold sinE
  congr 1
  omega

/-!
## 4. One pulse: `R(θ, φ)`

A pulse rotates about the axis cos φ·X + sin φ·Y. `QCCDC.Pulse.Native`:

```
noncomputable def axis (φ : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  (Real.cos φ : ℂ) • PX + (Real.sin φ : ℂ) • PY

noncomputable def R (θ φ : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  (Real.cos (θ / 2) : ℂ) • (1 : Matrix (Fin 2) (Fin 2) ℂ)
    - (Complex.I * (Real.sin (θ / 2) : ℂ)) • axis φ
```

A physical pulse is exp(−i·θ/2·A) for its axis A. `QCCDC.Pulse.Native` defines it by the closed form
cos(θ/2)·1 − i·sin(θ/2)·A instead of an exponential, which is right only because A·A = 1; so it
proves that (`axis_sq`), and so do you, below.

**One restriction.** θ enters only as θ/2. If θ is an odd number of eighths, θ/2 is an odd
multiple of π/8, and cos(π/8) is not one of our numbers. So for `R`, and for `MS` below, θ must be
an even number of eighths: a multiple of π/2. Clifford+T needs nothing else: every beam the
compiler emits for those gates turns by 0, ±π/2 or π (lessons G3 and G4). Our `R` writes `θ / 2`
with Lean's division of whole numbers, which for an odd θ rounds and would give a wrong matrix;
this lesson only ever writes even θ, as `2 * t`.
-/

def axis (φ : Int) : Mat := cosE φ • PX + sinE φ • PY

def R (θ φ : Int) : Mat := cosE (θ / 2) • I2 - (I * sinE (θ / 2)) • axis φ

#eval R 2 0
#eval R 2 2

/-!
`R 2 0` is a quarter turn about X, (1 − i·X)/√2; `R 2 2` the same about Y.

#### Exercise 3 (★) `axis_sq_grid`
`QCCDC.Pulse.Native` proves `axis_sq : axis φ * axis φ = 1` for every real φ, the fact the closed form
rests on. Prove it at the eight angles of the grid.
-/

theorem axis_sq_grid : ∀ φ : Fin 8, axis φ * axis φ = I2 := by
  decide +kernel

/-!
`QCCDC.Pulse.Native` then writes the pulse out entry by entry, the form everything downstream computes
with:

```
theorem R_eq (θ φ : ℝ) :
    R θ φ =
      !![(Real.cos (θ / 2) : ℂ),
          -Complex.I * Complex.exp (-(φ : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ);
         -Complex.I * Complex.exp ((φ : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ),
          (Real.cos (θ / 2) : ℂ)]
```

Here are those entries over our numbers, for θ = 2t eighths:
-/

/-- The explicit pulse of `QCCDC.Pulse.Native`'s `R_eq`, for θ = 2t eighths of a turn. -/
def Rentries (t φ : Int) : Mat :=
  [[cosE t, -I * cis (-φ) * sinE t], [-I * cis φ * sinE t, cosE t]]

/-!
#### Exercise 4 (★) `R_eq_grid`
Prove our `R` has those entries, for the four rotations a Clifford+T beam can have (θ = 0, π/2,
π, −π/2, so t = 0, 1, 2, −1) and all eight phases: 32 matrices. (All 64 values of t mod 8 would
do the whole grid; the kernel takes a few seconds over them, so this page checks the 32 the
compiler uses. Exercise 8 then covers every angle, for the explicit form.) `∀ t ∈ l, ...`
ranges over a list.
-/

theorem R_eq_grid : ∀ t ∈ ([0, 1, 2, -1] : List Int), ∀ φ : Fin 8, R (2 * t) φ = Rentries t φ := by
  decide +kernel

/-!
## 5. The virtual Z: `VZ(λ)`

`QCCDC.Pulse.Native` gives a Z rotation in two conventions:

```
noncomputable def RZ (lam : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  !![Complex.exp (-(lam : ℂ) * Complex.I / 2), 0;
     0, Complex.exp ((lam : ℂ) * Complex.I / 2)]

noncomputable def VZ (lam : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  !![1, 0; 0, Complex.exp ((lam : ℂ) * Complex.I)]

theorem VZ_eq_smul_RZ (lam : ℝ) :
    VZ lam = Complex.exp ((lam : ℂ) * Complex.I / 2) • RZ lam
```

`VZ`, the phase-gate form diag(1, e^(iλ)), is what a frame update physically is, and the one the
compiler emits. `RZ` has half angles, so again λ is even here.
-/

def VZ (l : Int) : Mat := [[1, 0], [0, cis l]]

def RZ (l : Int) : Mat := [[cis (-(l / 2)), 0], [0, cis (l / 2)]]

/-!
#### Exercise 5 (★) `VZ_eq_smul_RZ_grid`
Prove the two conventions differ by the phase e^(iλ/2), for λ = 2t on the grid.
-/

theorem VZ_eq_smul_RZ_grid : ∀ t : Fin 8, VZ (2 * t) = cis t • RZ (2 * t) := by
  decide +kernel

/-!
## 6. Two ions: `MS(θ)`

A 4×4 matrix acts on two qubits. `QCCDC.Pulse.Native` fixes the order: index `0 .. 3` is the pair of bits
(qubit 0, qubit 1), qubit 0 the more significant, so `kron A B` puts `A` on qubit 0:

```
def hiBit : Fin 4 → Fin 2 := ![0, 0, 1, 1]
def loBit : Fin 4 → Fin 2 := ![0, 1, 0, 1]
def kron (A B : Matrix (Fin 2) (Fin 2) ℂ) : Matrix (Fin 4) (Fin 4) ℂ :=
  fun i j => A (hiBit i) (hiBit j) * B (loBit i) (loBit j)
def XX : Matrix (Fin 4) (Fin 4) ℂ := kron PX PX
noncomputable def MS (θ : ℝ) : Matrix (Fin 4) (Fin 4) ℂ :=
  (Real.cos (θ / 2) : ℂ) • (1 : Matrix (Fin 4) (Fin 4) ℂ)
    - (Complex.I * (Real.sin (θ / 2) : ℂ)) • XX
```

The helpers that build the floating-point matrices for `check_cert` (`apply_gate` and its
neighbours) use the same order
("qubit 0 is the most significant bit, matching `QCCDC.kron` in Lean"). Our `kron`: for each row
of `A` and each row of `B`, the row whose entries are `a·b` with `a` running over `A`'s row on the
outside. `List.flatMap f l` applies `f` to each element and joins the lists.
-/

def kron (A B : Mat) : Mat :=
  A.flatMap fun ra => B.map fun rb => ra.flatMap fun a => vscale a rb

def I4 : Mat := kron I2 I2
def XX : Mat := kron PX PX

def MS (θ : Int) : Mat := cosE (θ / 2) • I4 - (I * sinE (θ / 2)) • XX

#eval MS 2

/-!
`MS 2` is MS(π/2), the operating point the compiler uses for every CNOT: the identity and −i·XX in
equal measure, each times √2/2. `QCCDC.Pulse.Native` proves `XX_sq : XX * XX = 1`, which licenses `MS`'s
closed form as `axis_sq` licenses `R`'s; Exercise 7 has you check it.
-/

/-!
## 7. Unitary

A matrix U is a legitimate gate when U·U† = 1: it preserves lengths, so probabilities still add up
to one after it. Every pulse is.

#### Exercise 6 (★) `pulse_inverse`
Prove that a quarter turn back undoes a quarter turn forward, about each of the eight axes:
R(−π/2, φ)·R(π/2, φ) = 1.
-/

theorem pulse_inverse : ∀ φ : Fin 8, R (-2) φ * R 2 φ = I2 := by
  decide +kernel

/-!
#### Exercise 7 (★) `MS_inverse`
Prove XX·XX = 1, that MS(π/2) is unitary, and that MS(−π/2) undoes it.
-/

theorem MS_inverse : XX * XX = I4 ∧ MS 2 * dagger (MS 2) = I4 ∧ MS (-2) * MS 2 = I4 := by
  decide +kernel

/-!
#### Exercise 8 (★★★) `R_unitary`
Now for **every** angle: all whole numbers `t` and `φ`, not just a list of them, for the explicit
form. A pulse depends on its angles only through their remainders mod 8, and there are 64 pairs
of remainders. The plan:

1. `key`: for all `a < 8` and `b < 8`, the pulse built from the table entries at `a` and `b` is
   unitary (`decide +kernel`). `Rt` below is that pulse, with the table lookups in place.
2. `Rentries t φ = Rt (t % 8).toNat (φ % 8).toNat`: unfold, then let `omega` match each table
   index (`(t - 2) % 8` against `((t % 8).toNat + 6) % 8`, `-φ % 8` against `(8 - ...) % 8`).
3. Rewrite with 2, apply 1.
-/

/-- `Rentries t φ` with `t` and `φ` given by their remainders `a` and `b` mod 8. -/
def Rt (a b : Nat) : Mat :=
  [[cosT a, -I * cisT ((8 - b) % 8) * cosT ((a + 6) % 8)],
   [-I * cisT b * cosT ((a + 6) % 8), cosT a]]

theorem R_unitary (t φ : Int) : Rentries t φ * dagger (Rentries t φ) = I2 := by
  have key : ∀ a < 8, ∀ b < 8, Rt a b * dagger (Rt a b) = I2 := by decide +kernel
  have h : Rentries t φ = Rt (t % 8).toNat (φ % 8).toNat := by
    unfold Rentries sinE cosE cis Rt
    rw [show ((t - 2) % 8).toNat = ((t % 8).toNat + 6) % 8 by omega,
      show (-φ % 8).toNat = (8 - (φ % 8).toNat) % 8 by omega]
  rw [h]
  exact key _ (by omega) _ (by omega)

/-!
#### Exercise 9 (★★) `first_pulse_unitary`
The Bell programme's first pulse is R(π/2, −π/2): `R 2 (-2)`. Prove it unitary: compute that it
equals `Rentries 1 (-2)`, rewrite, and apply `R_unitary`.
-/

theorem first_pulse_unitary : R 2 (-2) * dagger (R 2 (-2)) = I2 := by
  have h : R 2 (-2) = Rentries 1 (-2) := by decide +kernel
  rw [h]
  exact R_unitary 1 (-2)

/-!
## 8. The Bell programme's pulses, on the machine

The Bell pair of lesson F1, compiled for `chain4`, is this programme (the one the Compilation page shows for it): its native gate instructions, in
order, with the angles written as eighths of a turn. Each `R` and `VZ` acts on the ion named, in the trap named; the `MS` on both ions in `C0`.
Between the second and the third, `q0` moves from `C1` to `C0`.
-/

/-- A step of a programme: a native pulse (its angles in eighths of a turn, its ions, its trap),
or a move along a path of traps. -/
inductive Step where
  | pulse (name : String) (angles : List Int) (ions : List String) (site : String)
  | shuttle (ion : String) (path : List String)

def bellSteps : List Step :=
  [.pulse "R" [2, -2] ["q0"] "C1", .pulse "VZ" [4] ["q0"] "C1",
   .shuttle "q0" ["C1", "C0"],
   .pulse "R" [2, 2] ["q0"] "C0", .pulse "MS" [2] ["q0", "q1"] "C0",
   .pulse "R" [-2, 0] ["q0"] "C0", .pulse "R" [-2, 0] ["q1"] "C0",
   .pulse "R" [-2, 2] ["q0"] "C0"]

/-- The matrix of a pulse on its own ion (or pair); a move has none. -/
def Step.mat : Step → Mat
  | .pulse "R" [θ, φ] _ _ => R θ φ
  | .pulse "VZ" [l] _ _ => VZ l
  | .pulse "MS" [θ] _ _ => MS θ
  | _ => []

/-- The identity of size `n`. -/
def ident (n : Nat) : Mat := (List.range n).map fun i => (List.range n).map fun j => if i = j then 1 else 0

/-!
Here it is in the studio, built from `bellSteps` (the studio's programme language names a
one-ion gate by its trap, so read the ion from the list above). Edit the list and the figure
follows. The studio checks the machine's rules: where the ions are, which traps can gate. It has
no idea what an `R` does to a qubit; that is this Part's question.
-/

def quote (s : String) : String := "\"" ++ s ++ "\""

def quoteAll (xs : List String) : String := String.intercalate ", " (xs.map quote)

def Step.line : Step → String
  | .pulse g _ [a, b] _ => s!"p.gate({quote g}, [[{quote a}, {quote b}]])"
  | .pulse g _ _ site => s!"p.gate({quote g}, [], [{quote site}])"
  | .shuttle ion path => s!"p.shuttle({quote ion}, [{quoteAll path}])"

structure Studio where
  arch : String
  src : String

def bellOnMachine : Studio :=
  { arch := "chain4",
    src := "\n".intercalate
      (["p.init({\"q0\": \"C1\", \"q1\": \"C0\"})", "p.cool()"] ++ bellSteps.map Step.line ++
        ["p.measure([\"q0\", \"q1\"])"]) }

/-!
{{studio bellOnMachine}}

#### Exercise 10 (★) `bell_pulses_unitary`
Prove that every pulse the figure plays is unitary.
-/

theorem bell_pulses_unitary :
    ∀ s ∈ bellSteps, s.mat * dagger s.mat = ident s.mat.length := by
  decide +kernel

/-!
## 9. What this lesson built

`R`, `VZ` and `MS` as exact matrices, defined the way `QCCDC.Pulse.Native` defines them, checked against
its explicit forms on the grid, and unitary for every angle there. They are seven matrices in the
Bell programme. Lesson G3 multiplies the first two and finds the Hadamard; lesson G4 multiplies
the last five and finds the CNOT, up to a phase.
-/
