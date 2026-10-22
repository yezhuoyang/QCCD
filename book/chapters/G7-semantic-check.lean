import QCCDC.Cert.Sound

/-!
# The semantic check

*Part G, lesson G7. You need lessons G1 to G6, and lesson F1.*

Lesson F1 proved that the Bell certificate holds: where every ion is, cycle by cycle, and that
every gate's ions are together in a trap that can gate. It ended on what that proof does not
say: which gate the pulses make. That is the semantic half of the verdict called R10, and the
semantic check, `check_cert`, computes it. This lesson proves it, for the Bell certificate: it reads the
witnesses' pulses, composes them into the exact 4×4 unitary, and proves it equal to the circuit's
up to a global phase. Then it takes the one mutation of Part F's suite that the proved checker
accepts, finds the bug it stands for in the pulses, and refuses that.
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
def CX : Mat := [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]
def row : Mat → Nat → List Zω
  | [], _ => []
  | r :: _, 0 => r
  | _ :: rs, i + 1 => row rs i
def entry (A : Mat) (i j : Nat) : Zω := nth (row A i) j
def UpToPhase (A B : Mat) : Prop := ∃ c : Zω, c * conj c = 1 ∧ A = c • B
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
theorem not_phase_of_zero {A B : Mat} {i j : Nat}
    (hB : entry B i j = 0) (hA : entry A i j ≠ 0) : ¬ UpToPhase A B := by
  intro ⟨c, _, h⟩
  apply hA
  rw [h, entry_smul, hB, mul_zero_right]
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
*Lessons F1 and G1 to G6 are restated, unseen, above this line: the certificate `bell`, the
numbers and matrices, the pulses `R`, `VZ`, `MS` (angles in eighths of a turn), `kron`, `H`,
`CX`, `entry`, `UpToPhase`, and lesson G4's `not_phase_of_zero`.*

## 1. The certificate

Here is lesson F1's Bell certificate again, in the studio. The checker's rows are its conditions on
the move and on the two witnesses: the Hadamard's at cycle 2 in `C1`, the CNOT's at cycle 8 in
`C0`. Nothing in those rows says *Hadamard* or *CNOT*.

{{figure bell}}

## 2. Reading the pulses

The certificate file [bell.qcert.json](data/bell.qcert.json) carries, beside each witness, the
pulses the compiler emitted for it ("the native sequence, from `Gateset`", says the certificate
writer), in time order, as text. The checker's input ([bell.qcheck.json](data/bell.qcheck.json),
the `Input` above) does not carry
them, and Lean's checker never reads them:
-/

def hText : List String := ["R(1.5708,-1.5708)@q0", "VZ(3.14159)@q0"]

def cxText : List String :=
  ["R(1.5708,1.5708)@q0", "MS(1.5708)@q0,q1", "R(-1.5708,0)@q0", "R(-1.5708,0)@q1",
   "R(-1.5708,1.5708)@q0"]

/-!
**The trusted step.** The compiler prints each angle to six significant figures (`%.6g`). `1.5708` is not π/2; it is the six-digit decimal nearest to it. To turn the text
into pulses on the grid, this lesson rounds each angle to the nearest whole number of eighths of
a turn, so `1.5708` becomes 2 (π/2) and `3.14159` becomes 4 (π). That rounding is **trusted, not
proved**: Lean's core has no π to prove anything about, and nothing below could tell a pulse of
1.5708 radians from one of π/2. `check_cert` takes the same step its own way: it reads the
angles from the programme, where they are the double-precision numbers (`1.5707963267948966` in
the Bell programme), and its tableau route accepts an angle as a multiple of π/2 when it is one
to within 10⁻⁹ (`is_right_angle`). It also reads the instructions, not the certificate's copy of
them; for this certificate the seven pulses are the same, in the same order (instructions 2, 3 and
5 to 9 of the programme), which is one more thing this lesson takes from the file rather than
proves. The ions become qubits through the certificate's own map, `bell.qmap`.

A pulse on qubits, and the reading: written in Lean, run by `#eval`, which proves nothing.
-/

/-- A native pulse, angles in eighths of a turn, on qubits. -/
inductive Pulse where
  | R (θ φ : Int) (q : Nat)
  | VZ (l : Int) (q : Nat)
  | MS (θ : Int) (a b : Nat)
  deriving DecidableEq, Repr

def splitOn (sep : Char) : List Char → List (List Char)
  | [] => [[]]
  | c :: cs =>
    let rest := splitOn sep cs
    if c == sep then [] :: rest
    else match rest with
      | r :: rs => (c :: r) :: rs
      | [] => [[c]]

def digits (cs : List Char) : Nat := cs.foldl (fun n c => 10 * n + (c.toNat - '0'.toNat)) 0

/-- **Trusted:** a decimal angle in radians, rounded to the nearest whole number of eighths of a
turn, in floating point. -/
def eighths (cs : List Char) : Int :=
  let neg := cs.head? == some '-'
  let body := if neg then cs.drop 1 else cs
  let ip := body.takeWhile (· != '.')
  let fp := (body.dropWhile (· != '.')).drop 1
  let x : Float := (digits ip).toFloat + (digits fp).toFloat / (fp.map fun _ => (10 : Float)).foldl (· * ·) 1
  let k : Int := ((x / (3.141592653589793 / 4)).round.toUInt64.toNat : Nat)
  if neg then -k else k

/-- The two bracket characters, written by their codes (0x28 and 0x29). -/
def lpar : Char := '\x28'
def rpar : Char := '\x29'

/-- `"R(1.5708,-1.5708)@q0"` to `.R 2 (-2) 0`, the ion read through the qubit map. -/
def readPulse (qmap : List (Nat × String)) (s : String) : Option Pulse :=
  let cs := s.toList
  let name := cs.takeWhile (· != lpar)
  let args := (splitOn ',' (((cs.dropWhile (· != lpar)).drop 1).takeWhile (· != rpar))).map eighths
  let ions := splitOn ',' ((cs.dropWhile (· != '@')).drop 1)
  let qs := ions.filterMap fun i => (qmap.find? fun p => p.2.toList == i).map (·.1)
  match name, args, qs with
  | ['R'], [θ, φ], [q] => some (.R θ φ q)
  | ['V', 'Z'], [l], [q] => some (.VZ l q)
  | ['M', 'S'], [θ], [a, b] => some (.MS θ a b)
  | _, _, _ => none

#eval hText.map (readPulse bell.qmap)
#eval cxText.map (readPulse bell.qmap)

/-!
From here on, the pulses are these values, and everything is proved about them:
-/

def hPulses : List Pulse := [.R 2 (-2) 0, .VZ 4 0]

def cxPulses : List Pulse := [.R 2 2 0, .MS 2 0 1, .R (-2) 0 0, .R (-2) 0 1, .R (-2) 2 0]

#eval (hText.map (readPulse bell.qmap) == hPulses.map some,
       cxText.map (readPulse bell.qmap) == cxPulses.map some)

/-!
## 3. The programme's unitary

`check_cert`'s `program_unitary` starts from the identity and, for each pulse in time order,
left-multiplies by that pulse on its qubits, through its helper `apply_gate`:

```
def apply_gate(U: np.ndarray, g: np.ndarray, qubits: list[int], n: int) -> np.ndarray:
    """Left-multiply `U` by `g` acting on `qubits`.

    Done by reshaping the row index into `n` qubit axes and contracting, which costs
    O(2^n · 2^n) per gate instead of the O(8^n) a dense matrix product would.
    Qubit 0 is the most significant bit.
    """
```

Ours does the same on two qubits. `on q g U` is `kron g 1 * U` for q = 0 and `kron 1 g * U` for
q = 1, computed row pair by row pair without forming the 4×4. An `MS` acts on both qubits, and on
(q1, q0) it is the same matrix as on (q0, q1), since X⊗X is symmetric.
-/

def on (q : Nat) (g U : Mat) : Mat :=
  match U with
  | [r0, r1, r2, r3] =>
    let a := entry g 0 0
    let b := entry g 0 1
    let c := entry g 1 0
    let d := entry g 1 1
    if q = 0 then
      [vadd (vscale a r0) (vscale b r2), vadd (vscale a r1) (vscale b r3),
       vadd (vscale c r0) (vscale d r2), vadd (vscale c r1) (vscale d r3)]
    else
      [vadd (vscale a r0) (vscale b r1), vadd (vscale c r0) (vscale d r1),
       vadd (vscale a r2) (vscale b r3), vadd (vscale c r2) (vscale d r3)]
  | _ => U

/-- One pulse, applied after `U`. -/
def applyPulse (U : Mat) : Pulse → Mat
  | .R θ φ q => on q (R θ φ) U
  | .VZ l q => on q (VZ l) U
  | .MS θ _ _ => MS θ * U

/-- The unitary of a list of pulses in time order. -/
def run (ps : List Pulse) : Mat := ps.foldl applyPulse I4

/-!
#### Exercise 1 (★) `on_is_kron`
Check `on` against lesson G2's `kron` on the Hadamard's pulses, on both qubits.
-/

theorem on_is_kron :
    on 0 (R 2 (-2)) (kron (VZ 4) I2) = kron (R 2 (-2)) I2 * kron (VZ 4) I2 ∧
    on 1 (R 2 (-2)) (kron I2 (VZ 4)) = kron I2 (R 2 (-2)) * kron I2 (VZ 4) := by
  decide +kernel

/-!
#### Exercise 2 (★) `h_witness_unitary`
Prove the Hadamard witness's pulses make H on qubit 0, exactly (lesson G3).
-/

theorem h_witness_unitary : run hPulses = kron H I2 := by
  decide +kernel

/-!
#### Exercise 3 (★) `cx_witness_unitary`
Prove the CNOT witness's pulses make e^(iπ/4)·CX (lesson G4).
-/

theorem cx_witness_unitary : run cxPulses = ω • CX := by
  decide +kernel

/-!
## 4. The circuit's unitary, and the comparison

The circuit is `h q[0]; cx q[0],q[1];` and two measurements. Both measurements are the last thing
done to their qubit, in the circuit and in the programme, so `readout_plan` defers neither
(lesson G6), and the comparison is over the two qubits alone: the circuit's unitary is the
Hadamard on qubit 0, then the CNOT. `check_cert` reads the circuit's gates from the QASM through
qiskit's own definitions (`source_unitary`); ours are the matrices of lessons G3 and G4.
-/

def circuitU : Mat := CX * kron H I2

/-- The programme: the Hadamard's witness, then the CNOT's. -/
def programmeU : Mat := run (hPulses ++ cxPulses)

#eval programmeU

/-!
#### Exercise 4 (★★) `bell_semantic`
The theorem `check_cert` only computes, for this certificate: the programme's pulses implement
the circuit, up to a global phase. Name the phase.
-/

theorem bell_semantic : UpToPhase circuitU programmeU :=
  ⟨cis (-1), by decide +kernel, by decide +kernel⟩

/-!
`check_cert` itself decides this certificate by the tableau route (its verdict on the
Compilation page: "stabilizer tableau, composed from the emitted pulses, against the source QASM"), which lesson G5
proved. The full matrix is its route outside the Clifford fragment, for up to
`--max-unitary-qubits` (default 10) qubits; it is the stronger comparison, since it sees every
amplitude.

## 5. The mutant the proved checker accepts, and the bug behind it

Part F's mutation suite (`mutate_cert`, lesson F5) seeds defects into accepted
certificates and requires each refused. One of them it expects the proved checker to accept:

```
`swap_operands` exchanges the two ions of a gate.  The Lean checker verifies that a
witness names its op's own ions, in any order (`OwnIons`), and that they are
**co-located** in one gate-capable trap; swapping them changes neither, so it passes,
correctly, because O1 does not claim to see operand *order*.  A CX is not symmetric, so
the swap is a real bug
```
-/

def bellSwapped : QCCDC.Cert.Input :=
  { bell with gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q1", "q0"]⟩] }

/-!
#### Exercise 5 (★) `swapped_accepted`
Prove that Part F's checker accepts the Bell certificate with the CNOT's witness written `q1, q0`.
-/

theorem swapped_accepted : QCCDC.Cert.check bellSwapped = true := by
  decide +kernel

/-!
Lesson F5 explains why that is right: a witness names the ions an operation ran on, never which
one is the control, and `OwnIons` compares them as a rearrangement, so the order of a witness's ions
carries no meaning. The bug the mutation stands for is real all the same, and it lives in the
pulses: a CNOT with its control and target exchanged. A compiler with that bug emits
`decompose_cx` with `q1` as the control, so the single-ion pulses move to the other ion; the
witnesses need not change at all, and the proved checker, which never reads a pulse, cannot tell.
The Compilation page's `swap` certificate has exactly this sequence, as the second of its three
CNOTs:
`"R(1.5708,1.5708)@q1","MS(1.5708)@q1,q0","R(-1.5708,0)@q1","R(-1.5708,0)@q0","R(-1.5708,1.5708)@q1"`.
-/

def cxSwappedText : List String :=
  ["R(1.5708,1.5708)@q1", "MS(1.5708)@q1,q0", "R(-1.5708,0)@q1", "R(-1.5708,0)@q0",
   "R(-1.5708,1.5708)@q1"]

def cxSwapped : List Pulse := [.R 2 2 1, .MS 2 1 0, .R (-2) 0 1, .R (-2) 0 0, .R (-2) 2 1]

#eval cxSwappedText.map (readPulse bell.qmap) == cxSwapped.map some

def swappedU : Mat := run (hPulses ++ cxSwapped)

/-!
#### Exercise 6 (★★) `swapped_refused`
Prove the semantic check refuses a programme with those pulses for its CNOT: no phase takes it to
the circuit. From |00⟩ the circuit reaches |11⟩ (entry (3, 0)) and the swapped programme does not:
apply `not_phase_of_zero` there.
-/

theorem swapped_refused : ¬ UpToPhase circuitU swappedU :=
  not_phase_of_zero (i := 3) (j := 0) (by decide +kernel) (by decide +kernel)

/-!
That is the reason R10 has two halves. Neither sees what the other does: the proved checker never
reads a pulse, and the semantic check never asks where an ion is. Lesson F5 ran this bug through
`check_cert` on the published Bell programme: accepted by the proved checker, refused by the
tableau. Exercise 6 is that refusal, proved.

## 6. The check as a program

`check_cert`'s comparison is a procedure that answers yes or no (`same_up_to_phase`). Here is
one for our numbers: try the eight phases ω⁰ .. ω⁷. Like Part F's `check`, it is worth having only
with a theorem that its yes means the specification holds.
-/

def samePhase (A B : Mat) : Bool := (List.range 8).any fun k => A == cis k • B

/-!
#### Exercise 7 (★★★) `samePhase_sound`
Prove it: if `samePhase A B` is `true`, then `UpToPhase A B`, for every pair of matrices. Hints:
`List.any_eq_true` turns the `any` into an `∃ k ∈ List.range 8`; `List.mem_range` gives `k < 8`;
`beq_iff_eq` turns `==` into `=`; and the eight phases have size one by a finite check, `key`.
-/

theorem samePhase_sound (A B : Mat) (h : samePhase A B = true) : UpToPhase A B := by
  have key : ∀ k : Nat, k < 8 → cis k * conj (cis k) = 1 := by decide +kernel
  unfold samePhase at h
  rw [List.any_eq_true] at h
  obtain ⟨k, hk, he⟩ := h
  exact ⟨cis k, key k (List.mem_range.mp hk), beq_iff_eq.mp he⟩

/-!
#### Exercise 8 (★★) `bell_semantic_by_check`
Prove `bell_semantic` again, without naming the phase: run the check, and apply its soundness.
-/

theorem bell_semantic_by_check : UpToPhase circuitU programmeU :=
  samePhase_sound _ _ (by decide +kernel)

/-!
## 7. What is proved, and what is trusted

For the Bell certificate, this Part has proved what `check_cert` computes: the pulses the
witnesses carry make the circuit, exactly, up to the phase e^(−iπ/4) (Exercise 4); with lesson
F1, both halves of R10 are proved for this certificate, up to the steps the table marks. What
remains trusted or computed, and where:

| step | how it is checked |
|---|---|
| the text `1.5708` read as π/2 | trusted: rounded, in floating point (this lesson); `is_right_angle` in `check_cert` |
| the certificate's pulses are the programme's | trusted here; `check_cert` reads the programme itself |
| the witnesses name the programme's instructions, its moves are the certificate's | computed: `check_witnesses`, `check_program_transport` |
| the certificate's copy of the circuit is the QASM | computed: `check_binding` |
| each qubit's ops in the circuit's order | proved of the certificate's witnesses (`RespectsOrder`, Part F); computed of the programme's instructions, readouts included (`check_program_order`, lesson G6) |
| the pulses make the circuit | proved here, for one certificate; computed by `check_cert` for every one |

The last row is why this lesson is about one certificate: the kernel composes a 4×4 matrix in
seconds, but the Compilation page's larger examples and the boards' rounds are far beyond a
matrix, and there the comparison stays `stim`'s.
-/
