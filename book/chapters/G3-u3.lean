import QCCDC.Cert.Sound

/-!
# u3 from two pulses

*Part G, lesson G3. You need lessons G1 and G2.*

Every one-qubit gate of a circuit (`h`, `s`, `t`, `x`, a rotation) is a `u3`, and the compiler
replaces every `u3` by two native instructions: one physical pulse and one frame update. This
lesson proves that replacement right on the π/4 grid, and checks it against the pulses the
compiler really wrote for a Hadamard.
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
*Lessons G1 and G2 are restated, unseen, above this line: the numbers, `cis n` = e^(inπ/4) and
`cosE`, `sinE` on the grid (an angle is a whole number of eighths of a turn), matrices with `*`,
`•` and `dagger`, the Paulis `PX`, `PY`, `PZ`, and the pulses `R θ φ`, `VZ λ` and `RZ λ`. `R` is
written by its explicit entries, `QCCDC.Pulse.Native`'s `R_eq`, which lesson G2 checked against the
definition for every beam angle the compiler's Clifford+T output uses.*

## 1. The general one-qubit gate

OpenQASM writes a one-qubit gate as u3(θ, φ, λ). `QCCDC.Pulse.Native`:

```
noncomputable def u3 (θ φ lam : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  !![(Real.cos (θ / 2) : ℂ),
      -Complex.exp ((lam : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ);
     Complex.exp ((φ : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ),
      Complex.exp (((φ : ℂ) + (lam : ℂ)) * Complex.I) * (Real.cos (θ / 2) : ℂ)]
```

Ours, entry for entry; θ enters halved again, so it is an even number of eighths.
-/

def u3 (θ φ l : Int) : Mat :=
  [[cosE (θ / 2), -cis l * sinE (θ / 2)],
   [cis φ * sinE (θ / 2), cis (φ + l) * cosE (θ / 2)]]

/-!
The compiler keeps a table, `u3_of_gate`, beside its lowering, that gives every
gate it supports as a u3. Its Clifford+T rows:

```
  | "id", _ -> Some (0.0, 0.0, 0.0)
  | "x", _ -> Some (pi, 0.0, pi)
  | "y", _ -> Some (pi, pi /. 2.0, pi /. 2.0)
  | "z", _ -> Some (0.0, 0.0, pi)
  | "h", _ -> Some (pi /. 2.0, 0.0, pi)
  | "s", _ -> Some (0.0, 0.0, pi /. 2.0)
  | "sdg", _ -> Some (0.0, 0.0, -.pi /. 2.0)
  | "t", _ -> Some (0.0, 0.0, pi /. 4.0)
  | "tdg", _ -> Some (0.0, 0.0, -.pi /. 4.0)
  | "sx", _ -> Some (pi /. 2.0, -.pi /. 2.0, pi /. 2.0)
  | "sxdg", _ -> Some (pi /. 2.0, pi /. 2.0, -.pi /. 2.0)
```

The same rows, in eighths of a turn. Every θ is 0, π/2 or π: this is why lesson G2 could keep θ
even.
-/

structure U3 where
  name : String
  θ : Int
  φ : Int
  l : Int

def table : List U3 :=
  [⟨"id", 0, 0, 0⟩, ⟨"x", 4, 0, 4⟩, ⟨"y", 4, 2, 2⟩, ⟨"z", 0, 0, 4⟩, ⟨"h", 2, 0, 4⟩,
   ⟨"s", 0, 0, 2⟩, ⟨"sdg", 0, 0, -2⟩, ⟨"t", 0, 0, 1⟩, ⟨"tdg", 0, 0, -1⟩,
   ⟨"sx", 2, -2, 2⟩, ⟨"sxdg", 2, 2, -2⟩]

/-- The Hadamard, the phase gate and the T gate, as a textbook writes them. -/
def H : Mat := [[invSqrt2, invSqrt2], [invSqrt2, -invSqrt2]]
def S : Mat := [[1, 0], [0, I]]
def T : Mat := [[1, 0], [0, ω]]

#eval u3 2 0 4

/-!
#### Exercise 1 (★) `table_gates`
Prove the table right for six of its gates: its u3 is exactly X, Y, Z, H, S and T.
-/

theorem table_gates :
    u3 4 0 4 = PX ∧ u3 4 2 2 = PY ∧ u3 0 0 4 = PZ ∧ u3 2 0 4 = H ∧ u3 0 0 2 = S ∧ u3 0 0 1 = T := by
  decide +kernel

/-!
Not every row is exact. The square root of X, as qiskit defines `sx`, is ½·[[1+i, 1−i], [1−i, 1+i]];
the table's u3 for `sx` is that times a phase. Two matrices that differ by a number of size one
make the same gate on their own, and lesson G4 is about when that is enough.

#### Exercise 2 (★) `sx_phase`
Prove `SX = ω • u3(π/2, −π/2, π/2)`.
-/

def SX : Mat := [[half * (1 + I), half * (1 - I)], [half * (1 - I), half * (1 + I)]]

theorem sx_phase : SX = ω • u3 2 (-2) 2 := by
  decide +kernel

/-!
## 2. One pulse and one frame

The module `QCCDC.Pulse.Decompose` proves, for all real angles:

```
theorem u3_decomp (θ φ lam : ℝ) :
    u3 θ φ lam = VZ (φ + lam) * R θ (Real.pi / 2 - lam)
```

"exactly, with no global phase". Its header says why it matters for cost: the general one-qubit
gate is one virtual frame update and "exactly **one physical pulse** -- not the three a naive Euler
(Z-X-Z) decomposition would emit". The compiler's lowering, `decompose_u3`, is the theorem's
computational content:

```
let decompose_u3 ~(qubit : int) (theta, phi, lam) : decomposed =
  {
    pulses =
      [
        Beam { theta; phi = (pi /. 2.0) -. lam; qubit };
        Frame { lam = phi +. lam; qubit };
      ];
    phase = 0.0;
  }
```

The pulses are listed in **time** order, the beam first; a matrix product applies its rightmost
factor first, so the beam is on the right of `VZ (φ + λ) * R θ (π/2 − λ)`. In eighths of a turn,
π/2 − λ is `2 - l`.

#### Exercise 3 (★) `decomp_table`
Prove `u3_decomp` for every row of the table. (The whole grid, θ = 0, π/2 or π with all 64 pairs
of phases, is 192 identities. They hold too, but take the kernel a few seconds, so this page checks
the table's rows here and every phase at θ = π/2 in Exercise 4.)
-/

theorem decomp_table : ∀ g ∈ table, u3 g.θ g.φ g.l = VZ (g.φ + g.l) * R g.θ (2 - g.l) := by
  decide +kernel

/-!
#### Exercise 4 (★★★) `decomp_quarter_turn`
Prove `u3_decomp` at θ = π/2 for **every** φ and λ on the grid: all whole numbers, the method of
lessons G1 and G2. Both sides depend on φ and λ only through their remainders `a` and `b` mod 8;
`lhs a b` and `rhs a b` below are the two sides with the table lookups in place. Show each side
equals its table form (unfold, then `omega` for the indices), and finish with a 64-case `key`.
-/

def lhs (a b : Nat) : Mat :=
  [[cosE 1, -cisT b * sinE 1], [cisT a * sinE 1, cisT ((a + b) % 8) * cosE 1]]

def rhs (a b : Nat) : Mat :=
  [[1, 0], [0, cisT ((a + b) % 8)]] *
    [[cosE 1, -I * cisT ((b + 6) % 8) * sinE 1], [-I * cisT ((10 - b) % 8) * sinE 1, cosE 1]]

theorem decomp_quarter_turn (φ l : Int) : u3 2 φ l = VZ (φ + l) * R 2 (2 - l) := by
  have key : ∀ a < 8, ∀ b < 8, lhs a b = rhs a b := by decide +kernel
  have hl : u3 2 φ l = lhs (φ % 8).toNat (l % 8).toNat := by
    unfold u3 lhs cis
    rw [show (2 : Int) / 2 = 1 by omega,
      show ((φ + l) % 8).toNat = ((φ % 8).toNat + (l % 8).toNat) % 8 by omega]
  have hr : VZ (φ + l) * R 2 (2 - l) = rhs (φ % 8).toNat (l % 8).toNat := by
    unfold VZ R rhs cis
    rw [show (2 : Int) / 2 = 1 by omega,
      show ((φ + l) % 8).toNat = ((φ % 8).toNat + (l % 8).toNat) % 8 by omega,
      show (-(2 - l) % 8).toNat = ((l % 8).toNat + 6) % 8 by omega,
      show ((2 - l) % 8).toNat = (10 - (l % 8).toNat) % 8 by omega]
  rw [hl, hr]
  exact key _ (by omega) _ (by omega)

/-!
## 3. The Hadamard the compiler wrote

The Compilation page compiles `h q[0];` on its own, onto `chain4`. Its certificate,
[h.qcert.json](data/h.qcert.json), has one witness: op 0 at cycle 2 in trap `C1` on ion `q0`, with the pulses
the compiler emitted for it, in time order:

```
"pulses":["R(1.5708,-1.5708)@q0","VZ(3.14159)@q0"]
```

That is R(π/2, −π/2) then VZ(π): the table's row for `h` is (π/2, 0, π), so the beam has phase
π/2 − π and the frame turns by 0 + π. Here is the certificate as the proved checker reads it
([h.qcheck.json](data/h.qcheck.json)), played in the studio. The checker sees the witness, the trap and the ion;
it never sees the two pulses. Which gate they make is this lesson's question.
-/

open QCCDC.Cert in
def hCert : Input :=
  { circuit := "circuit", arch := "chain4", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "C1"), ("q1", "C0")],
    ops := [⟨0, "h", [0]⟩],
    moves := [],
    rots := [],
    gates := [⟨0, 2, "C1", ["q0"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

/-!
{{figure hCert}}

#### Exercise 5 (★) `h_witness`
Prove the two pulses of the witness make the Hadamard, exactly: the frame after the beam.
-/

theorem h_witness : VZ 4 * R 2 (-2) = H := by
  decide +kernel

/-!
The semantic check, `check_cert`, checks the same two pulses, read from the programme, with a
stabilizer tableau
(lesson G5). The page's other one-qubit examples (`x`, `z`, `s` and `t` on the Compilation page)
carry these in their certificates:

| gate | pulses | in eighths |
|---|---|---|
| `x` | `R(3.14159,-1.5708)`, `VZ(3.14159)` | R(4, −2), VZ(4) |
| `z` | `R(0,-1.5708)`, `VZ(3.14159)` | R(0, −2), VZ(4) |
| `s` | `R(0,0)`, `VZ(1.5708)` | R(0, 0), VZ(2) |
| `t` | `R(0,0.785398)`, `VZ(0.785398)` | R(0, 1), VZ(1) |

Reading `3.14159` as π and `0.785398` as π/4 is a step this lesson takes on trust; lesson G7 says
exactly what it is.

#### Exercise 6 (★) `witnesses`
Prove all four make their gate exactly.
-/

theorem witnesses :
    VZ 4 * R 4 (-2) = PX ∧ VZ 4 * R 0 (-2) = PZ ∧ VZ 2 * R 0 0 = S ∧ VZ 1 * R 0 1 = T := by
  decide +kernel

/-!
#### Exercise 7 (★★) `h_pulses_twice`
H·H = 1, so the witness's pulses, played twice, do nothing. Prove it by rewriting with
`h_witness` and computing what is left.
-/

theorem h_pulses_twice : (VZ 4 * R 2 (-2)) * (VZ 4 * R 2 (-2)) = I2 := by
  rw [h_witness]
  decide +kernel

/-!
## 4. A beam that turns by nothing

For `z`, `s` and `t` the table's θ is 0, and the compiler still writes the beam, of angle 0. That
beam is the identity whatever its phase, so these three gates are their frame update alone.

#### Exercise 8 (★★) `zero_beam`
Prove R(0, φ) = 1 for **every** φ. This is not a finite check: φ is any whole number. The
cosine and sine of 0 are computations; the two other entries are something times
`sinE (0 / 2)`, which is 0, and `mul_zero_right` from lesson G1 (restated above) does the rest.
-/

theorem zero_beam (φ : Int) : R 0 φ = I2 := by
  have hc : cosE (0 / 2) = 1 := by decide +kernel
  have hs : sinE (0 / 2) = 0 := by decide +kernel
  unfold R
  rw [hc, hs, mul_zero_right, mul_zero_right]
  rfl

/-!
## 5. Euler's three

The "naive Euler (Z-X-Z) decomposition" the cost comment warns about writes a one-qubit gate as a
Z rotation, an X rotation and a Z rotation. For the Hadamard it is H = i·Rz(π/2)·Rx(π/2)·Rz(π/2),
with `QCCDC.Pulse.Native`'s symmetric `RZ`, and its `Rx θ`, which it defines as `R θ 0`.

#### Exercise 9 (★) `euler_zxz_h`
Prove it. Three rotations and a phase, against `u3_decomp`'s one pulse and one free frame.
-/

theorem euler_zxz_h : H = I • (RZ 2 * R 2 0 * RZ 2) := by
  decide +kernel

/-!
## 6. What this lesson proved

The compiler's one-qubit rule, `u3 = VZ(φ+λ)·R(θ, π/2−λ)`, holds exactly for every Clifford+T
row of its table, and for every phase on the grid at θ = π/2; the Hadamard's two pulses in a real
certificate make the Hadamard, with no phase left over. The two-qubit rule is not like that:
lesson G4 finds the CNOT in five pulses and one Mølmer-Sørensen gate, and a phase that does not go
away.
-/
