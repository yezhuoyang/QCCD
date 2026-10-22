import QCCDC.Cert.Sound

/-!
# Stabilizer tableaux

*Part G, lesson G5. You need lessons G1 to G4.*

A matrix for n qubits has 4ⁿ entries; a BB[[144,12,12]] round has hundreds of qubits. So for a
Clifford circuit (H, S, CNOT and the like), the semantic check, `check_cert`, never forms the
matrix. It
describes each side by its **stabilizer tableau** and compares those, with the library `stim`;
this is how the Compilation page's Bell certificate was checked (its verdict there: "stabilizer
tableau, composed from the emitted pulses, against the source QASM"). This lesson builds a
tableau in Lean, small enough for the kernel at two qubits, checks its rules against the exact
matrices, and proves the Bell circuit and the Bell programme's pulses have the same one.
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

/-!
*Lessons G1 to G4 are restated, unseen, above this line: the numbers, matrices with `*`, `•`,
`dagger` and `kron`, `PX`, `PY`, `PZ`, `I2`, `I4`, and the pulse `MS θ` (angles in eighths of a
turn).*

## 1. Pauli strings

A Pauli string is a phase iᵉ (e = 0 .. 3) times one Pauli per qubit: `⟨2, [.Z, .X]⟩` is −Z⊗X. One
qubit's Paulis multiply by a table, XY = iZ and so on round the cycle, and a string multiplies
qubit by qubit, the phases adding mod 4.
-/

inductive P1 where
  | I | X | Y | Z
  deriving DecidableEq, Repr

/-- `p·q = iᵉ·r`, as `(e, r)`. -/
def P1.mul : P1 → P1 → Nat × P1
  | .I, q => (0, q)
  | p, .I => (0, p)
  | .X, .X => (0, .I)
  | .Y, .Y => (0, .I)
  | .Z, .Z => (0, .I)
  | .X, .Y => (1, .Z)
  | .Y, .X => (3, .Z)
  | .Y, .Z => (1, .X)
  | .Z, .Y => (3, .X)
  | .Z, .X => (1, .Y)
  | .X, .Z => (3, .Y)

/-- `iᵖʰ` times one Pauli per qubit, qubit 0 first. -/
structure PS where
  ph : Nat
  ps : List P1
  deriving DecidableEq, Repr

def mulList : List P1 → List P1 → Nat × List P1
  | p :: ps, q :: qs =>
    let (e, r) := P1.mul p q
    let (e', rs) := mulList ps qs
    (e + e', r :: rs)
  | _, _ => (0, [])

def PS.mul (x y : PS) : PS :=
  let (e, r) := mulList x.ps y.ps
  ⟨(x.ph + y.ph + e) % 4, r⟩

/-!
Each string is a matrix: a `kron` of the Paulis, times the phase. That is what ties the strings to
lessons G1 to G4.
-/

def P1.mat : P1 → Mat
  | .I => I2
  | .X => PX
  | .Y => PY
  | .Z => PZ

def iPow : Nat → Zω
  | 0 => 1
  | 1 => I
  | 2 => -1
  | _ => -I

def PS.mat (x : PS) : Mat :=
  match x.ps with
  | [p] => iPow x.ph • p.mat
  | [p, q] => iPow x.ph • kron p.mat q.mat
  | _ => []

def paulis : List P1 := [.X, .Y, .Z]

/-!
#### Exercise 1 (★) `products_match`
Prove the table right: for each pair of the three Paulis, the string product's matrix is the
product of the matrices.
-/

theorem products_match :
    ∀ p ∈ paulis, ∀ q ∈ paulis, (PS.mul ⟨0, [p]⟩ ⟨0, [q]⟩).mat = p.mat * q.mat := by
  decide +kernel

/-!
## 2. Conjugating by a quarter turn

A Clifford gate U sends every Pauli string P to another one, U·P·U†, up to sign. For the native
pulses there is one rule. Each of them, at a multiple of π/2, is a power of a **quarter turn**
exp(−iπ/4·Q) = (1 − i·Q)/√2 about a Pauli string Q: `R(π/2, 0)` about X, `R(π/2, π/2)` about Y,
`R(π/2, π)` about −X, `MS(π/2)` about X⊗X. A quarter turn leaves P alone if P commutes with Q, and
sends it to −i·Q·P if they anticommute. Two strings anticommute when they differ at an odd number
of qubits where neither is the identity.
-/

def P1.anti : P1 → P1 → Bool
  | .I, _ => false
  | _, .I => false
  | p, q => p != q

def antiCount : List P1 → List P1 → Nat
  | p :: ps, q :: qs => (if P1.anti p q then 1 else 0) + antiCount ps qs
  | _, _ => 0

def commutes (x y : PS) : Bool := antiCount x.ps y.ps % 2 == 0

/-- `exp(−iπ/4·Q) · P · exp(iπ/4·Q)`. -/
def quarter (Q P : PS) : PS :=
  if commutes Q P then P
  else let qp := PS.mul Q P; ⟨(qp.ph + 3) % 4, qp.ps⟩

/-- The quarter turn itself, as an exact matrix: `(1 − i·Q)/√2`. -/
def turnMat (Q : PS) : Mat := invSqrt2 • (I2 - I • Q.mat)

def axes : List PS := [⟨0, [.X]⟩, ⟨2, [.X]⟩, ⟨0, [.Y]⟩, ⟨2, [.Y]⟩, ⟨0, [.Z]⟩, ⟨2, [.Z]⟩]

/-!
#### Exercise 2 (★) `quarter_rule`
Prove the rule against the exact matrices: for each of the six one-qubit axes ±X, ±Y, ±Z and each
Pauli P, the rule's answer is U·P·U† for U the quarter turn.
-/

theorem quarter_rule :
    ∀ Q ∈ axes, ∀ p ∈ paulis,
      (quarter Q ⟨0, [p]⟩).mat = turnMat Q * p.mat * dagger (turnMat Q) := by
  decide +kernel

/-!
#### Exercise 3 (★) `ms_conjugates`
Two qubits: MS(π/2) is the quarter turn about X⊗X. Prove it sends Z⊗1 to −Y⊗X, by the rule and by
the 4×4 matrices.
-/

theorem ms_conjugates :
    quarter ⟨0, [.X, .X]⟩ ⟨0, [.Z, .I]⟩ = ⟨2, [.Y, .X]⟩ ∧
    MS 2 * (PS.mat ⟨0, [.Z, .I]⟩) * dagger (MS 2) = PS.mat ⟨2, [.Y, .X]⟩ := by
  decide +kernel

/-!
## 3. The tableau

A Clifford U is determined, up to a global phase, by where it sends the 2n strings X₀, Z₀, X₁, Z₁,
...: every string is a product of those. That list of images is its **tableau** (in `stim`, a
`Tableau`, with `x_output(k)` and `z_output(k)`). Here a tableau is a list with one pair per qubit,
the images of Xₖ and Zₖ.

To conjugate any string P by a tableau, write P as a product of the generators, Y = i·X·Z, and
multiply their images in the same order. To compose, conjugate the first tableau's images by the
second.
-/

abbrev Tab := List (PS × PS)

def single (n k : Nat) (p : P1) : PS := ⟨0, (List.range n).map fun j => if j = k then p else .I⟩

def idTab (n : Nat) : Tab := (List.range n).map fun k => (single n k .X, single n k .Z)

def conjGo : Tab → List P1 → PS → PS
  | (tx, tz) :: ts, p :: ps, acc =>
    let acc := match p with
      | .I => acc
      | .X => PS.mul acc tx
      | .Z => PS.mul acc tz
      | .Y => let a := PS.mul (PS.mul acc tx) tz; ⟨(a.ph + 1) % 4, a.ps⟩
    conjGo ts ps acc
  | _, _, acc => acc

/-- `U·P·U†` for the Clifford `U` whose tableau is `T`. -/
def conjBy (T : Tab) (P : PS) : PS :=
  let a := conjGo T P.ps ⟨0, List.replicate T.length .I⟩
  ⟨(a.ph + P.ph) % 4, a.ps⟩

/-- First `T₁`, then `T₂`. -/
def thenTab (T₁ T₂ : Tab) : Tab := T₁.map fun (x, z) => (conjBy T₂ x, conjBy T₂ z)

/-- The tableau of a quarter turn about `Q`, on `n` qubits. -/
def quarterTab (n : Nat) (Q : PS) : Tab :=
  (idTab n).map fun (x, z) => (quarter Q x, quarter Q z)

/-!
## 4. The pulses, and the circuit's gates

`check_cert` translates each native pulse into `stim`'s gates (`_emit_r`, `_emit_ms`, and `S`
for a `VZ`), for angles that are multiples of π/2 only: "R(theta, phi) = exp(-i theta/2 (cos phi X +
sin phi Y)), for theta a multiple of pi/2", the axis read as "0 = X, 1 = Y, 2 = -X, 3 = -Y" quarter
turns of φ. A `T` gate's phase π/4 is off that grid, and there `check_cert` falls back to the
full matrix. In our terms, with angles in eighths of a turn: `R θ φ` is θ/2 quarter turns about
the axis φ/2; `VZ λ` is λ/2 quarter turns about Z (diag(1, e^(iλ)) is that up to a phase); `MS θ`
is θ/2 quarter turns about X⊗X.
-/

/-- A native pulse, angles in eighths of a turn, on qubits. -/
inductive Pulse where
  | R (θ φ : Int) (q : Nat)
  | VZ (l : Int) (q : Nat)
  | MS (θ : Int) (a b : Nat)

def two (n a : Nat) (p : P1) (b : Nat) (q : P1) : PS :=
  ⟨0, (List.range n).map fun j => if j = a then p else if j = b then q else .I⟩

/-- The axis of a beam at phase `φ` (a multiple of π/2): X, Y, −X, −Y. -/
def beamAxis (n q : Nat) (φ : Int) : PS :=
  match ((φ / 2) % 4).toNat with
  | 0 => single n q .X
  | 1 => single n q .Y
  | 2 => ⟨2, (single n q .X).ps⟩
  | _ => ⟨2, (single n q .Y).ps⟩

def power (n : Nat) (T : Tab) : Nat → Tab
  | 0 => idTab n
  | k + 1 => thenTab (power n T k) T

def turns (x : Int) : Nat := ((x / 2) % 4).toNat

def Pulse.tab (n : Nat) : Pulse → Tab
  | .R θ φ q => power n (quarterTab n (beamAxis n q φ)) (turns θ)
  | .VZ l q => power n (quarterTab n (single n q .Z)) (turns l)
  | .MS θ a b => power n (quarterTab n (two n a .X b .X)) (turns θ)

/-- The tableau of a programme: its pulses in time order. -/
def progTab (n : Nat) (ps : List Pulse) : Tab := ps.foldl (fun T p => thenTab T (p.tab n)) (idTab n)

/-!
The circuit's side is the source QASM, read gate by gate (`circuit_tableau`), with `stim`'s own
tableaux for `H` (X ↔ Z) and `CX` (X꜀ ↦ X꜀Xₜ, Zₜ ↦ Z꜀Zₜ, the other two fixed).
-/

def hTab (n q : Nat) : Tab :=
  (List.range n).map fun k => if k = q then (single n q .Z, single n q .X) else (single n k .X, single n k .Z)

def cxTab (n c t : Nat) : Tab :=
  (List.range n).map fun k =>
    if k = c then (two n c .X t .X, single n c .Z)
    else if k = t then (single n t .X, two n c .Z t .Z)
    else (single n k .X, single n k .Z)

/-!
The Hadamard's two pulses in the Bell certificate (lesson G3) were `R(π/2, −π/2)` then `VZ(π)`.

#### Exercise 4 (★) `h_pulses_tableau`
Prove their tableau is the Hadamard's.
-/

theorem h_pulses_tableau : progTab 1 [.R 2 (-2) 0, .VZ 4 0] = hTab 1 0 := by
  decide +kernel

/-!
## 5. The Bell pair, both ways

The Bell programme's seven pulses, in time order, with qubit 0 on ion `q0` and qubit 1 on `q1`
(lesson G2's list):
-/

def bellPulses : List Pulse :=
  [.R 2 (-2) 0, .VZ 4 0, .R 2 2 0, .MS 2 0 1, .R (-2) 0 0, .R (-2) 0 1, .R (-2) 2 0]

/-- The source circuit `h q[0]; cx q[0],q[1];`: first H, then CX. -/
def bellCircuitTab : Tab := thenTab (hTab 2 0) (cxTab 2 0 1)

#eval progTab 2 bellPulses

/-!
Read it as four rows: X₀ ↦ Z⊗1, Z₀ ↦ X⊗X, X₁ ↦ 1⊗X, Z₁ ↦ Z⊗Z. The images of Z₀ and Z₁, X⊗X
and Z⊗Z, are the two stabilizers of the Bell state the circuit prepares from |00⟩.

#### Exercise 5 (★) `bell_tableaux`
Prove the programme's tableau is the circuit's. The measurements are left out on both sides: both
are the last thing done to their qubit, and lesson G6 explains why that lets them go.
-/

theorem bell_tableaux : progTab 2 bellPulses = bellCircuitTab := by
  decide +kernel

/-!
`check_cert` makes the same comparison with `stim`: it builds a `stim.Circuit` from the
programme's pulses (`tableau_from_program`) and another from the QASM (`circuit_tableau`), turns
each into a `stim.Tableau`, and tests `got == want`. That is a computation by a C++ library and a
translation written in Python, trusted, not proved. The compiler's design notes say so: "The tableau
itself is not proved -- it is `stim` plus a translation". What you just did is the same comparison
as a theorem, for one certificate.

## 6. What a tableau cannot see

Lesson G4 showed the CNOT's five pulses are e^(iπ/4)·CX, not CX. A tableau is built from
U·P·U†, and a phase c with |c| = 1 cancels in (cU)·P·(cU)† = c·c̄·U·P·U†.

#### Exercise 6 (★) `phase_blind`
Prove the five pulses have exactly the CNOT's tableau.
-/

def cxPulses : List Pulse := [.R 2 2 0, .MS 2 0 1, .R (-2) 0 0, .R (-2) 0 1, .R (-2) 2 0]

theorem phase_blind : progTab 2 cxPulses = cxTab 2 0 1 := by
  decide +kernel

/-!
That is the right blindness for a whole programme, and the reason the compiler records each gate's
phase itself (lesson G4). What a tableau does see is everything else: a gate on the wrong qubit, a
wrong angle, a dropped pulse. If the compiler had exchanged the CNOT's operands, it would have
emitted the same lowering with `q1` as the control: `R(π/2, π/2)` on `q1`, the `MS`, the two
beams, `R(−π/2, π/2)` on `q1`. (The Compilation page's `swap` certificate has exactly those
pulses, as the second of its three CNOTs.)

#### Exercise 7 (★) `swapped_differs`
Prove that programme's tableau is not the circuit's.
-/

def swappedPulses : List Pulse :=
  [.R 2 (-2) 0, .VZ 4 0, .R 2 2 1, .MS 2 1 0, .R (-2) 0 1, .R (-2) 0 0, .R (-2) 2 1]

theorem swapped_differs : progTab 2 swappedPulses ≠ bellCircuitTab := by
  decide +kernel

/-!
## 7. Commuting is symmetric

The quarter-turn rule asks whether Q and P commute. That had better not depend on the order of the
question, for every pair of strings of every length, not only for the ones above.

#### Exercise 8 (★★★) `anti_symm`
Prove the anticommuting count is symmetric. Induction on the first list, cases on the second; at
one qubit, `P1.anti p q = P1.anti q p` is sixteen cases, `cases p <;> cases q <;> rfl`.
-/

theorem anti_symm : ∀ (p q : List P1), antiCount p q = antiCount q p := by
  have one : ∀ p q : P1, P1.anti p q = P1.anti q p := by
    intro p q
    cases p <;> cases q <;> rfl
  intro p
  induction p with
  | nil => intro q; cases q <;> rfl
  | cons a ps ih =>
    intro q
    cases q with
    | nil => rfl
    | cons b bs => simp only [antiCount, one a b, ih bs]

/-!
#### Exercise 9 (★★) `commutes_symm`
Deduce it for strings: unfold `commutes` and rewrite with `anti_symm`.
-/

theorem commutes_symm (x y : PS) : commutes x y = commutes y x := by
  unfold commutes
  rw [anti_symm]

/-!
## 8. What this lesson proved

For the Bell certificate, the stabilizer comparison `check_cert` runs with `stim` holds as a
theorem: the programme's seven pulses and the circuit's two gates have one tableau. The tableau's
rules were checked against the exact matrices of lessons G1 to G4, and its blindness to a global
phase is the one lesson G4 wanted. Lesson G6 adds what neither a tableau nor a unitary has:
measurements.
-/
