import QCCDC.Cert.Sound

/-!
# CX from MS

*Part G, lesson G4. You need lessons G1 to G3.*

The only entangling operation of the machine is the Mølmer-Sørensen pulse. Every CNOT of a
circuit becomes four one-ion pulses around one MS(π/2). This
lesson proves that exactly, including the phase e^(−iπ/4) the identity carries; says what
"equal up to a global phase" means, and why it is the right equality for a whole programme and the
wrong one for a building block; and proves the CNOT that the Bell certificate's witness stands for.
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
theorem mul_zero_right (x : Zω) : x * 0 = 0 := by
  have norm_zero : ∀ k, Zω.norm k 0 0 0 0 = ⟨0, 0, 0, 0, 0⟩ := by
    intro k
    induction k with
    | zero => rfl
    | succ k ih => simp [Zω.norm, ih]
  show mul x ⟨0, 0, 0, 0, 0⟩ = ⟨0, 0, 0, 0, 0⟩
  have h : mul x ⟨0, 0, 0, 0, 0⟩ = Zω.norm x.k 0 0 0 0 := by simp [mul]
  rw [h, norm_zero]
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
abbrev Mat := List (List Zω)
def vadd : List Zω → List Zω → List Zω
  | x :: xs, y :: ys => (x + y) :: vadd xs ys
  | _, _ => []
def vscale (c : Zω) : List Zω → List Zω
  | [] => []
  | x :: xs => (c * x) :: vscale c xs
def rowMul : List Zω → Mat → List Zω
  | [x], [row] => vscale x row
  | x :: xs, row :: rows => vadd (vscale x row) (rowMul xs rows)
  | _, _ => []
def mmul (A B : Mat) : Mat := A.map (rowMul · B)
def nth : List Zω → Nat → Zω
  | [], _ => 0
  | x :: _, 0 => x
  | _ :: xs, n + 1 => nth xs n
def dagger (A : Mat) : Mat :=
  (List.range (A.headD []).length).map fun j => A.map fun row => conj (nth row j)
instance : Mul Mat := ⟨mmul⟩
instance : Add Mat := ⟨List.zipWith vadd⟩
instance : SMul Zω Mat := ⟨fun c A => A.map (vscale c)⟩
instance : Sub Mat := ⟨fun A B => A + (-1 : Zω) • B⟩
def I2 : Mat := [[1, 0], [0, 1]]
def PX : Mat := [[0, 1], [1, 0]]
def PY : Mat := [[0, -I], [I, 0]]
def PZ : Mat := [[1, 0], [0, -1]]
def cosT : Nat → Zω
  | 0 => 1
  | 1 => invSqrt2
  | 2 => 0
  | 3 => -invSqrt2
  | 4 => -1
  | 5 => -invSqrt2
  | 6 => 0
  | _ => invSqrt2
def cosE (n : Int) : Zω := cosT (n % 8).toNat
def sinE (n : Int) : Zω := cosE (n - 2)
def R (θ φ : Int) : Mat :=
  [[cosE (θ / 2), -I * cis (-φ) * sinE (θ / 2)], [-I * cis φ * sinE (θ / 2), cosE (θ / 2)]]
def VZ (l : Int) : Mat := [[1, 0], [0, cis l]]
def RZ (l : Int) : Mat := [[cis (-(l / 2)), 0], [0, cis (l / 2)]]
def kron (A B : Mat) : Mat :=
  A.flatMap fun ra => B.map fun rb => ra.flatMap fun a => vscale a rb
def I4 : Mat := kron I2 I2
def XX : Mat := kron PX PX
def MS (θ : Int) : Mat := cosE (θ / 2) • I4 - (I * sinE (θ / 2)) • XX
def H : Mat := [[invSqrt2, invSqrt2], [invSqrt2, -invSqrt2]]
theorem h_witness : VZ 4 * R 2 (-2) = H := by decide +kernel
open QCCDC.Cert in
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

/-!
*Lessons G1 to G3 are restated, unseen, above this line: the numbers and `cis`; matrices with `*`,
`•` and `dagger`; `PX`, `PY`, `PZ`, `I2`, `I4`; the pulses `R θ φ`, `VZ λ`, `MS θ` (angles in
eighths of a turn); `kron A B`, with `A` on qubit 0; the Hadamard `H` and lesson G3's `h_witness :
VZ 4 * R 2 (-2) = H`; `mul_zero_right`; and lesson F1's Bell certificate `bell`.*

## 1. The target

```
def CX : Matrix (Fin 4) (Fin 4) ℂ := !![1, 0, 0, 0; 0, 1, 0, 0; 0, 0, 0, 1; 0, 0, 1, 0]
```

`QCCDC.Pulse.Native`'s CNOT, qubit 0 the control: it swaps the basis states 10 and 11.
-/

def CX : Mat := [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]

/-!
## 2. Five pulses and a phase

`QCCDC.Pulse.Decompose` states it as "CX = e^{−iπ/4} · [Ry(−π/2)⊗I] · [Rx(−π/2)⊗Rx(−π/2)] · MS(π/2) ·
[Ry(π/2)⊗I]", with "Matrix order: the rightmost factor is the first pulse in time. One MS gate
and four single-qubit pulses":

```
theorem cx_decomp :
    CX =
      Complex.exp ((-(Real.pi / 4) : ℝ) * Complex.I) •
        (kron (Ry (-(Real.pi / 2))) 1
          * kron (Rx (-(Real.pi / 2))) (Rx (-(Real.pi / 2)))
          * MS (Real.pi / 2)
          * kron (Ry (Real.pi / 2)) 1)
```

`Rx θ` is `R θ 0` and `Ry θ` is `R θ (π/2)`. The compiler's lowering, `decompose_cx`, emits
the same five pulses in time order, the reverse of the matrix order, and records the phase:

```
let decompose_cx ~(control : int) ~(target : int) : decomposed =
  {
    pulses =
      [
        Beam { theta = pi /. 2.0; phi = pi /. 2.0; qubit = control };
        Ms { theta = pi /. 2.0; a = control; b = target };
        Beam { theta = -.pi /. 2.0; phi = 0.0; qubit = control };
        Beam { theta = -.pi /. 2.0; phi = 0.0; qubit = target };
        Beam { theta = -.pi /. 2.0; phi = pi /. 2.0; qubit = control };
      ];
    phase = -.pi /. 4.0;
  }
```

In eighths of a turn: π/2 is 2, −π/4 is −1. In `QCCDC.Pulse.Decompose` the proof is Mathlib algebra,
with `cos (π/4)` kept symbolic and the heartbeat limit raised (`set_option maxHeartbeats 2000000`);
here it is a computation.

#### Exercise 1 (★) `cx_decomp_exact`
Prove `cx_decomp` over our numbers.
-/

theorem cx_decomp_exact :
    CX = cis (-1) • (kron (R (-2) 2) I2 * kron (R (-2) 0) (R (-2) 0) * MS 2 * kron (R 2 2) I2) := by
  decide +kernel

/-!
## 3. Equal up to a global phase

So the five pulses do not make CX. They make e^(iπ/4)·CX: every amplitude of every output turned
by the same eighth of a turn. Nothing can measure that. A measurement's probabilities are the
squared sizes of amplitudes, and |c·a|² = |a|² when |c| = 1. Two matrices that differ by such a
factor are the same gate as far as any experiment on them can tell:
-/

/-- `A` and `B` differ by a global phase: one number `c` of size one with `A = c • B`. -/
def UpToPhase (A B : Mat) : Prop := ∃ c : Zω, c * conj c = 1 ∧ A = c • B

/-!
This is the equality the semantic check, `check_cert`, uses when it compares full matrices
(`same_up_to_phase`: "Is `a = e^{iα}·b`?", with a tolerance of 10⁻⁸ on every entry), and the one a
stabilizer tableau has built in (lesson G5). An `∃` is proved by naming the witness: `⟨c, p, q⟩`
gives `c` and proofs of the two facts about it.

#### Exercise 2 (★★) `cx_up_to_phase`
Prove the five pulses are CX up to a global phase: name the phase, prove it has size one, and use
Exercise 1.
-/

theorem cx_up_to_phase :
    UpToPhase CX (kron (R (-2) 2) I2 * kron (R (-2) 0) (R (-2) 0) * MS 2 * kron (R 2 2) I2) :=
  ⟨cis (-1), by decide +kernel, cx_decomp_exact⟩

/-!
## 4. When a phase is not global

Then why does the compiler track the phase at all? Its lowering says, where it defines a
decomposed gate:

```
(* A decomposed gate: its pulses in TIME order, plus the global phase the identity
   carries.  The phase is tracked rather than dropped because a gate that later becomes
   controlled needs it, and because dropping it silently is the classic way to get a
   compiler that is right on every benchmark and wrong on the one that matters. *)
```

A phase is global only for the whole programme. Make a gate controlled, and its phase applies
when the control is 1 and not when it is 0: it has become a relative phase, which experiments do
see. Take X and ω·X: the same gate on their own. Controlled, they are not. `ctrl U` is the 4×4
matrix that does U to qubit 1 when qubit 0 is 1 (the semantic check's helper `ctrl`).
-/

/-- Row `i` of a matrix, and entry `(i, j)` (0 past the end). -/
def row : Mat → Nat → List Zω
  | [], _ => []
  | r :: _, 0 => r
  | _ :: rs, i + 1 => row rs i

def entry (A : Mat) (i j : Nat) : Zω := nth (row A i) j

def ctrl (U : Mat) : Mat :=
  [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, entry U 0 0, entry U 0 1], [0, 0, entry U 1 0, entry U 1 1]]

#eval ctrl (ω • PX)

/-!
To prove two matrices are **not** equal up to a phase you must rule out every `c`, a number
nobody names. The way in: if A = c·B then every entry of A is c times the same entry of B. So if
two entries of B are equal, the same two entries of A must be equal too, whatever c is. That
needs one fact about `•` that holds for every matrix and every `c`.

#### Exercise 3 (★★★) `entry_smul`
Prove entry (i, j) of c·A is c times entry (i, j) of A. Two inductions: one on a row, for `nth`
of `vscale` (past the end both sides are 0, by `mul_zero_right`), and one on the matrix, for `row`
of `c • A`. `c • A` unfolds to `A.map (vscale c)`, so the cases are `rfl` or the induction
hypothesis.
-/

theorem entry_smul (c : Zω) (A : Mat) (i j : Nat) : entry (c • A) i j = c * entry A i j := by
  have hv : ∀ (r : List Zω) (j : Nat), nth (vscale c r) j = c * nth r j := by
    intro r
    induction r with
    | nil => intro j; simp [vscale, nth, mul_zero_right]
    | cons x xs ih => intro j; cases j <;> simp [vscale, nth, ih]
  have hr : ∀ (A : Mat) (i : Nat), row (c • A) i = vscale c (row A i) := by
    intro A
    induction A with
    | nil => intro i; rfl
    | cons r rs ih =>
      intro i
      cases i with
      | zero => rfl
      | succ i => exact ih i
  unfold entry
  rw [hr, hv]

/-!
#### Exercise 4 (★★) `not_phase_of_ratio`
Prove the refutation rule: if entries (i, j) and (k, l) of B are equal and those of A are not,
no phase takes B to A. Suppose one did (`intro ⟨c, _, h⟩`), and rewrite the two entries of A with
`h` and `entry_smul`.
-/

theorem not_phase_of_ratio {A B : Mat} {i j k l : Nat}
    (hB : entry B i j = entry B k l) (hA : entry A i j ≠ entry A k l) : ¬ UpToPhase A B := by
  intro ⟨c, _, h⟩
  apply hA
  rw [h, entry_smul, entry_smul, hB]

/-!
#### Exercise 5 (★★) `phase_matters`
Prove both: X and ω·X are equal up to a phase, and their controlled versions are not. For the
second, apply `not_phase_of_ratio` at the entries (0, 0) and (2, 3); the four facts it needs
are computations.
-/

theorem phase_matters : UpToPhase PX (ω • PX) ∧ ¬ UpToPhase (ctrl (ω • PX)) (ctrl PX) :=
  ⟨⟨conj ω, by decide +kernel, by decide +kernel⟩,
   not_phase_of_ratio (i := 0) (j := 0) (k := 2) (l := 3) (by decide +kernel) (by decide +kernel)⟩

/-!
The same rule with a zero entry is the one lesson G7 needs: if B is 0 somewhere A is not, no
phase takes B to A, since c·0 = 0 for every `c`.
-/

theorem not_phase_of_zero {A B : Mat} {i j : Nat}
    (hB : entry B i j = 0) (hA : entry A i j ≠ 0) : ¬ UpToPhase A B := by
  intro ⟨c, _, h⟩
  apply hA
  rw [h, entry_smul, hB, mul_zero_right]

/-!
## 5. The CNOT of the Bell certificate

Lesson F1's certificate has a witness for the CNOT: op 1, at cycle 8, in trap `C0`, on ions `q0`
and `q1`, `q0` (qubit 0) the control. The compiler wrote its pulses beside it in
[bell.qcert.json](data/bell.qcert.json), in time order:

```
"pulses":["R(1.5708,1.5708)@q0","MS(1.5708)@q0,q1","R(-1.5708,0)@q0",
          "R(-1.5708,0)@q1","R(-1.5708,1.5708)@q0"]
```

These are `decompose_cx`'s five, with qubit 0 on ion `q0` and qubit 1 on `q1`. The programme
fires the two −π/2 beams as two instructions, one per ion, where the theorem has one `kron`. Here
is the witness, at its cycle, in the studio: the proved checker's row says the two ions are
together in a trap that can gate, and are the op's own. Which gate the five pulses make is
Exercise 6.

{{figure bell at=8}}

#### Exercise 6 (★) `bell_cnot_witness`
Prove the witness's pulses, each on its own ion, in reverse time order, make CX with the
compiler's phase.
-/

theorem bell_cnot_witness :
    CX = cis (-1) • (kron (R (-2) 2) I2 * kron I2 (R (-2) 0) * kron (R (-2) 0) I2 * MS 2 *
      kron (R 2 2) I2) := by
  decide +kernel

/-!
#### Exercise 7 (★) `middle_beams`
Prove the two −π/2 beams on different ions commute, and together are the theorem's single `kron`.
-/

theorem middle_beams :
    kron I2 (R (-2) 0) * kron (R (-2) 0) I2 = kron (R (-2) 0) I2 * kron I2 (R (-2) 0) ∧
    kron I2 (R (-2) 0) * kron (R (-2) 0) I2 = kron (R (-2) 0) (R (-2) 0) := by
  decide +kernel

/-!
## 6. CZ

Most other two-qubit gates are built from the CNOT. The compiler's lowering of composite gates
writes `cz` as a CNOT between two Hadamards on the target:

```
  | "cz", [ c; t ], _ -> seq [ named "h" t; cxg c t; named "h" t ]
```

`seq` adds the parts' phases: 0 + (−π/4) + 0. The Compilation page's `cz` example (`cz q[0],q[1];`)
has these nine pulses in its one witness:

```
"R(1.5708,-1.5708)@q1","VZ(3.14159)@q1","R(1.5708,1.5708)@q0","MS(1.5708)@q0,q1",
"R(-1.5708,0)@q0","R(-1.5708,0)@q1","R(-1.5708,1.5708)@q0","R(1.5708,-1.5708)@q1",
"VZ(3.14159)@q1"
```
-/

def CZ : Mat := [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, -1]]

/-!
#### Exercise 8 (★) `cz_lowering`
Prove the lowering exact: CZ = (1⊗H)·CX·(1⊗H). The Hadamards here are the target's, qubit 1.
-/

theorem cz_lowering : CZ = kron I2 H * CX * kron I2 H := by
  decide +kernel

/-!
#### Exercise 9 (★★) `cz_from_witnesses`
The nine pulses are the Hadamard's two on `q1`, the CNOT's five, and the Hadamard's two again.
Prove they make CZ with the phase e^(−iπ/4), grouping each Hadamard's two pulses by ion, without
multiplying a single 4×4 matrix: rewrite with `h_witness`, turn the CNOT's pulses back into `CX`
with Exercise 6 (`rw [← bell_cnot_witness]`), and finish with Exercise 8. Proofs compose: three
facts already checked make a fourth.
-/

theorem cz_from_witnesses :
    CZ = kron I2 (VZ 4 * R 2 (-2)) *
      (cis (-1) • (kron (R (-2) 2) I2 * kron I2 (R (-2) 0) * kron (R (-2) 0) I2 * MS 2 *
        kron (R 2 2) I2)) * kron I2 (VZ 4 * R 2 (-2)) := by
  rw [h_witness, ← bell_cnot_witness]
  exact cz_lowering

/-!
## 7. What this lesson proved

The CNOT is five native pulses times e^(−iπ/4), exactly; that is equality up to a global phase,
the right equality for a whole programme and the wrong one for a part that will be controlled.
The Bell certificate's CNOT witness carries pulses that make the CNOT. Lesson G5 checks the same
pulses the way `check_cert` does, with a stabilizer tableau, which cannot see a phase at all.
-/
