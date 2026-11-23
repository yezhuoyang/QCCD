import QCCDC.Cert.Sound

/-!
# CX from RZZ: the other native gate set

*Part G, lesson G8. You need lessons G1 to G4.*

Lessons G2 to G4 used one native gate set: the pulse `R(θ, φ)`, the frame change `VZ(λ)` (those
lessons say frame update) and the Mølmer-Sørensen pulse `MS(θ)`. Not every trapped-ion machine entangles with MS. Quantinuum's
machines, the H-series and Helios ([arXiv:2003.01293](https://arxiv.org/abs/2003.01293),
[arXiv:2511.05465](https://arxiv.org/abs/2511.05465)), have the same one-ion pulse (there it is
called U1q), a Z rotation done as a frame change, and a different two-ion pulse: `RZZ(θ)`, a
rotation about Z⊗Z.

So the compiler has an option, `gate_set`, with two values: `ms`, the default, and `rzz`. This
lesson defines RZZ, proves that one RZZ(π/2) is a CZ and that a CNOT is one RZZ with two pulses on
the target, counts what a CNOT costs in each set, proves that the two sets differ by a change of
basis and nothing else, and ends with how to choose.
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
def CX : Mat := [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]
def CZ : Mat := [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, -1]]
def UpToPhase (A B : Mat) : Prop := ∃ c : Zω, c * conj c = 1 ∧ A = c • B

/-!
*Lessons G1 to G4 are restated, unseen, above this line: the numbers and `cis`; matrices with `*`,
`•` and `dagger`; `PX`, `PY`, `PZ`, `I2`, `I4`; the cosine table `cosT` with `cosE` and `sinE`; the
pulses `R θ φ`, `VZ λ`, `RZ λ` and `MS θ` with its generator `XX` (angles in eighths of a turn);
`kron A B`, with `A` on qubit 0; the Hadamard `H` and lesson G3's `h_witness : VZ 4 * R 2 (-2) = H`;
and lesson G4's `CX`, `CZ` and `UpToPhase`.*

## 1. The pulse RZZ

`QCCDC.Pulse.Native` defines the entangling pulse of the second set beside `MS`:

```
def ZZ : Matrix (Fin 4) (Fin 4) ℂ := kron PZ PZ

noncomputable def RZZ (θ : ℝ) : Matrix (Fin 4) (Fin 4) ℂ :=
  (Real.cos (θ / 2) : ℂ) • (1 : Matrix (Fin 4) (Fin 4) ℂ)
    - (Complex.I * (Real.sin (θ / 2) : ℂ)) • ZZ
```

It is `MS` with Z⊗Z where `MS` has X⊗X: the pulse exp(−i·θ/2·Z⊗Z), written by the closed form
cos(θ/2)·1 − i·sin(θ/2)·Z⊗Z. Ours, over the numbers of lesson G1; as for `MS` in lesson G2, θ is
an even number of eighths of a turn.
-/

def ZZ : Mat := kron PZ PZ

def RZZ (θ : Int) : Mat := cosE (θ / 2) • I4 - (I * sinE (θ / 2)) • ZZ

#eval MS 2
#eval RZZ 2

/-!
#### Exercise 1 (★) `zz_sq`
The closed form is the exponential only because the generator squares to one. `QCCDC.Pulse.Native`
proves that as `ZZ_sq`, as it proves `XX_sq` for `MS`. Prove it here.
-/

theorem zz_sq : ZZ * ZZ = I4 := by
  decide +kernel

/-!
Look at the two matrices printed above. MS(π/2) has entries off its diagonal: it turns the state
00 into a mix of 00 and 11. RZZ(π/2) has none. Each basis state stays the state it was and is
multiplied by a phase: e^(−iπ/4) when its two bits agree, e^(iπ/4) when they differ.

#### Exercise 2 (★) `rzz_diagonal`
Prove RZZ(π/2) is that diagonal matrix.
-/

theorem rzz_diagonal :
    RZZ 2 = [[cis (-1), 0, 0, 0], [0, cis 1, 0, 0], [0, 0, cis 1, 0], [0, 0, 0, cis (-1)]] := by
  decide +kernel

/-!
## 2. One RZZ is a CZ

CZ is diagonal too: 1, 1, 1, −1. By Exercise 2, RZZ(π/2) is e^(−iπ/4) times the diagonal
1, i, i, 1. A frame change by −π/2 on each ion multiplies that diagonal by 1, −i, −i, −1, and what
is left is CZ times a phase. `QCCDC.Pulse.Decompose`:

```
theorem cz_decomp_vz :
    CZ =
      Complex.exp ((Real.pi / 4 : ℝ) * Complex.I) •
        (kron (VZ (-(Real.pi / 2))) (VZ (-(Real.pi / 2))) * RZZ (Real.pi / 2))
```

In eighths of a turn π/2 is 2 and π/4 is 1.

#### Exercise 3 (★) `cz_from_rzz`
Prove `cz_decomp_vz` over our numbers.
-/

theorem cz_from_rzz : CZ = cis 1 • (kron (VZ (-2)) (VZ (-2)) * RZZ 2) := by
  decide +kernel

/-!
`VZ` is the phase-gate form of a Z rotation, the one a programme holds. `QCCDC.Pulse.Decompose`
proves the same fact for the rotation form `RZ(λ)`, diag(e^(−iλ/2), e^(iλ/2)), and there the
phase comes out the other way:

```
theorem cz_decomp :
    CZ =
      Complex.exp ((-(Real.pi / 4) : ℝ) * Complex.I) •
        (kron (RZ (-(Real.pi / 2))) (RZ (-(Real.pi / 2))) * RZZ (Real.pi / 2))
```

The two phases differ because `VZ` and `RZ` differ by a phase themselves (lesson G2, Exercise 5),
once for each ion. Neither can be measured (lesson G4).

#### Exercise 4 (★★) `cz_two_conventions`
Prove that one RZZ(π/2) and a quarter-turn Z rotation on each ion are CZ up to a global phase, in
both conventions. Name each phase and prove it has size one; for `VZ` the equation is Exercise 3.
-/

theorem cz_two_conventions :
    UpToPhase CZ (kron (VZ (-2)) (VZ (-2)) * RZZ 2) ∧
    UpToPhase CZ (kron (RZ (-2)) (RZ (-2)) * RZZ 2) :=
  ⟨⟨cis 1, by decide +kernel, cz_from_rzz⟩, ⟨cis (-1), by decide +kernel, by decide +kernel⟩⟩

/-!
Every factor here is diagonal, so the factors commute: the frame changes may come before the RZZ
or after it. And CZ treats its two ions alike, which CX does not.

## 3. CX from RZZ

A CNOT is a CZ with a Hadamard on the target before and after. That is lesson G4's Exercise 8,
read the other way.

#### Exercise 5 (★) `cx_from_cz`
Prove CX = (1⊗H)·CZ·(1⊗H). The target is qubit 1.
-/

theorem cx_from_cz : CX = kron I2 H * CZ * kron I2 H := by
  decide +kernel

/-!
A Hadamard is one pulse and one frame change: `h_witness`, from lesson G3. So a CNOT is one RZZ,
two pulses on the target, and frame changes. `QCCDC.Pulse.Decompose` states it with each Hadamard
written as its pulse and frame change:

```
theorem cx_rzz_via_cz :
    CX =
      Complex.exp ((Real.pi / 4 : ℝ) * Complex.I) •
        (kron 1 (VZ Real.pi * R (Real.pi / 2) (-(Real.pi / 2)))
          * kron (VZ (-(Real.pi / 2))) (VZ (-(Real.pi / 2)))
          * RZZ (Real.pi / 2)
          * kron 1 (VZ Real.pi * R (Real.pi / 2) (-(Real.pi / 2))))
```

The proof there multiplies no matrix. It rewrites with three facts already proved, and you have
proved the same three.

#### Exercise 6 (★★) `cx_from_rzz`
Prove it from Exercises 3 and 5 and `h_witness`, the way lesson G4's Exercise 9 was proved:
`rw [h_witness, ← cz_from_rzz]`, then Exercise 5. The phase is written beside the RZZ here, where
the rewriting needs it.
-/

theorem cx_from_rzz :
    CX = kron I2 (VZ 4 * R 2 (-2)) * (cis 1 • (kron (VZ (-2)) (VZ (-2)) * RZZ 2)) *
      kron I2 (VZ 4 * R 2 (-2)) := by
  rw [h_witness, ← cz_from_rzz]
  exact cx_from_cz

/-!
## 4. What the compiler emits, and what it costs

Written that way the CNOT has four frame changes: one in each Hadamard and two in the CZ. Three of
them are on the target, and they can be gathered into one. Frame changes on one ion add up, and
they commute with RZZ, which is diagonal like them. And a frame change may be moved to the other
side of a pulse if the pulse's axis is turned to match: that is what `VZ` does, it advances the
phase of every later pulse on its ion (lesson G2).
-/

theorem frame_past_pulse : R 2 (-2) * VZ 2 = VZ 2 * R 2 4 := by
  decide +kernel

/-!
So the compiler emits one frame change per ion, after the pulses. With `gate_set` set to `rzz`,
its lowering of a CNOT, `decompose_cx`, emits these five in time order and records the phase π/4:

| | instruction | on |
|---|---|---|
| 1 | `R(−π/2, π/2)` | the target |
| 2 | `RZZ(π/2)` | both ions |
| 3 | `R(π/2, π)` | the target |
| 4 | `VZ(−π/2)` | the control |
| 5 | `VZ(−π/2)` | the target |

The first pulse is the Hadamard's own, written with the opposite sign and the opposite axis; the
second is the Hadamard's with its axis turned, as in `frame_past_pulse`. `QCCDC.Pulse.Decompose`
proves the sequence, in matrix order (the rightmost factor is the first pulse in time), beside
lesson G4's `cx_decomp`:

```
theorem cx_decomp_rzz :
    CX =
      Complex.exp ((Real.pi / 4 : ℝ) * Complex.I) •
        (kron (VZ (-(Real.pi / 2))) (VZ (-(Real.pi / 2)))
          * kron 1 (R (Real.pi / 2) Real.pi)
          * RZZ (Real.pi / 2)
          * kron 1 (R (-(Real.pi / 2)) (Real.pi / 2)))
```

Here are both CNOTs as one matrix each, qubit 0 the control. `cnotMS` is lesson G4's five pulses.
-/

/-- The CNOT's pulses in the `ms` set, multiplied out. -/
def cnotMS : Mat := kron (R (-2) 2) I2 * kron (R (-2) 0) (R (-2) 0) * MS 2 * kron (R 2 2) I2

/-- The CNOT's pulses and frame changes in the `rzz` set, multiplied out. -/
def cnotRZZ : Mat := kron (VZ (-2)) (VZ (-2)) * kron I2 (R 2 4) * RZZ 2 * kron I2 (R (-2) 2)

/-- Lesson G4's Exercise 1: the `ms` CNOT, with the phase e^(−iπ/4). -/
theorem cx_decomp_exact : CX = cis (-1) • cnotMS := by
  decide +kernel

/-!
#### Exercise 7 (★) `cx_decomp_rzz_exact`
Prove `cx_decomp_rzz` over our numbers: the `rzz` CNOT, with the phase e^(iπ/4).
-/

theorem cx_decomp_rzz_exact : CX = cis 1 • cnotRZZ := by
  decide +kernel

/-!
#### Exercise 8 (★★) `same_cnot`
The two sequences make the same gate. Prove it without multiplying a matrix: each side is CX, by
one of the two theorems above. Rewrite with them from right to left (`rw [← ...]`).
-/

theorem same_cnot : cis (-1) • cnotMS = cis 1 • cnotRZZ := by
  rw [← cx_decomp_exact, ← cx_decomp_rzz_exact]

/-!
So `cnotMS` is i times `cnotRZZ`. A circuit compiled in either set is the same circuit, and each
CNOT turns the global phase by a quarter turn more in one set than in the other. What differs is
what the machine does:

| one CNOT | `ms` | `rzz` |
|---|---|---|
| entangling pulses | 1 `MS` | 1 `RZZ` |
| one-ion pulses `R` | 4: three on the control, one on the target | 2: both on the target |
| frame changes `VZ` | 0 | 2: one on each ion |

For a `cz` the difference is larger. In the `ms` set it is a CNOT between two Hadamards, the nine
instructions of lesson G4. In the `rzz` set it is Exercise 3 and nothing more: one RZZ and two
frame changes, with no one-ion pulse at all.

What a frame change costs is the machine's to say, not the gate set's. A machine that tracks the
phase of each qubit (Quantinuum's do) makes a frame change by changing a number in its
controller: it is virtual, and no laser fires. A machine whose beam reaches every ion of a trap
has no phase of one ion to change. There this platform prices a Z rotation as three one-ion
pulses, unless the machine's own document declares a faster Z gate. So count for your machine:
where frame changes are virtual, the `rzz` CNOT is three physical pulses against five; where they
are not, its two frame changes are six more.

## 5. The two sets differ by a change of basis

H·Z·H = X. So a Hadamard on each ion turns the generator of RZZ into the generator of MS, and two
Hadamards on each ion undo each other:
-/

theorem generators : kron H H * ZZ * kron H H = XX ∧ kron H H * kron H H = I4 := by
  decide +kernel

/-!
Both pulses are cos(θ/2)·1 − i·sin(θ/2)·(their generator). So MS(θ) = (H⊗H)·RZZ(θ)·(H⊗H) at every
angle: anything one set can do the other can, with basis changes on single ions.
`QCCDC.Pulse.Decompose` proves it for every real θ from those two facts and the algebra of
matrices, computing no entry:

```
theorem MS_eq_conj_RZZ (θ : ℝ) : MS θ = kron Had Had * RZZ θ * kron Had Had := by
  unfold MS RZZ
  simp only [mul_sub, sub_mul, Matrix.mul_smul, Matrix.smul_mul, Matrix.mul_one, HH_sq,
    HH_ZZ_HH]
```

Our matrices are lists of lists, with no algebra proved about them. Here the proof goes through
the cosine table instead, as lesson G2's Exercise 8 did: both sides depend on θ only through
θ/2 mod 8.
-/

/-- `MS` and `RZZ` at the angle whose half is `k` eighths, read from the cosine table. The sine is
the cosine a quarter turn earlier: 2 eighths back, which is 6 eighths on. -/
def msT (k : Nat) : Mat := cosT k • I4 - (I * cosT ((k + 6) % 8)) • XX

def rzzT (k : Nat) : Mat := cosT k • I4 - (I * cosT ((k + 6) % 8)) • ZZ

/-!
#### Exercise 9 (★★★) `ms_from_rzz`
Prove MS(θ) = (H⊗H)·RZZ(θ)·(H⊗H) for every angle on the grid, in three steps. First the eight
rows of the table, a computation: `∀ k < 8, msT k = kron H H * rzzT k * kron H H`. Then
`MS θ = msT ((θ / 2) % 8).toNat`, and the same for `RZZ`: `unfold MS sinE cosE msT` leaves one
fact about angles, `((θ / 2 - 2) % 8).toNat = (((θ / 2) % 8).toNat + 6) % 8`, which `omega`
proves; hand it to `rw` as `show ... by omega`. Last, rewrite with both and apply the table row,
whose `k < 8` is `omega`'s too.
-/

theorem ms_from_rzz (θ : Int) : MS θ = kron H H * RZZ θ * kron H H := by
  have key : ∀ k < 8, msT k = kron H H * rzzT k * kron H H := by decide +kernel
  have hm : MS θ = msT ((θ / 2) % 8).toNat := by
    unfold MS sinE cosE msT
    rw [show ((θ / 2 - 2) % 8).toNat = (((θ / 2) % 8).toNat + 6) % 8 by omega]
  have hr : RZZ θ = rzzT ((θ / 2) % 8).toNat := by
    unfold RZZ sinE cosE rzzT
    rw [show ((θ / 2 - 2) % 8).toNat = (((θ / 2) % 8).toNat + 6) % 8 by omega]
  rw [hm, hr]
  exact key _ (by omega)

/-!
## 6. Why choose RZZ

There are two reasons.

**The machine.** A programme can only hold pulses its machine has. Quantinuum's machines have RZZ,
so a programme for one of them, or for a design modelled on them, is compiled with `rzz`.

**RZZ is diagonal.** It never changes a bit: Exercise 2 said so for basis states. The same fact
for operators is that RZZ commutes with Z on either ion. MS does not.

#### Exercise 10 (★) `rzz_keeps_z`
Prove RZZ(π/2) commutes with Z on qubit 0 and with Z on qubit 1, and that MS(π/2) does not commute
with Z on qubit 0.
-/

theorem rzz_keeps_z :
    RZZ 2 * kron PZ I2 = kron PZ I2 * RZZ 2 ∧ RZZ 2 * kron I2 PZ = kron I2 PZ * RZZ 2 ∧
    MS 2 * kron PZ I2 ≠ kron PZ I2 * MS 2 := by
  decide +kernel

/-!
This matters for error-correcting codes. The syndrome circuit of a CSS code is made of CNOTs,
preparations and readouts. Lowered to the `rzz` set, its only two-ion operation is diagonal in the
computational basis, and everything else in it is a basis change on one ion: the circuit stays CSS
up to single-qubit basis changes. That is the class of circuits Ellis (Hou and Ravi,
[arXiv:2611.26173](https://arxiv.org/abs/2611.26173)) simulates fast when ions can be lost or can
leak out of the two qubit states, with a Pauli frame and per-shot masks. This platform does not
simulate leakage that way. The gate set is where such a simulation would start.

## 7. Choosing the gate set in the compiler

* **The option.** `gate_set` is `ms` unless you say otherwise. On the command line it is
  `qccdc ... --gate-set rzz`.
* **The programme.** The entangling pulse of the `rzz` set is the instruction
  `pulse RZZ(pi/2) q0,q1 @C0;`, where the `ms` set has `pulse MS(pi/2) q0,q1 @C0;`. A compiled
  programme holds pulses of one set.
* **Cost and noise.** RZZ is the machine's native two-qubit gate, as MS is: it is priced with the
  duration, and noised with the error, that the machine's document gives its two-qubit gate. The
  gate set changes what surrounds that gate (section 4), not the gate's own price.
* **The checks.** The proved checker of Part F names no gate. A certificate says where and when
  each CNOT happened, and it is checked the same way in either set. The semantic check, `check_cert`,
  composes the pulses the programme holds, RZZ among them, and compares the result with the circuit
  (lesson G7).

## 8. What this lesson proved

RZZ(π/2) is a diagonal matrix, and with one frame change on each ion it is CZ, up to a phase. A
CNOT is one RZZ, two pulses on the target and two frame changes, times e^(iπ/4): the sequence the
compiler emits for `gate_set` `rzz`, and the same gate as lesson G4's five pulses. MS and RZZ are
one pulse seen in two bases, at every angle. Which set to use is decided by the machine, and by
what you want to simulate.
-/
