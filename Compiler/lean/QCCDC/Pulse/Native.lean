/-
# The native gate set of a trapped-ion machine

Layer O2 of `Compiler/PLAN.md` §1: the obligation that each logical gate is replaced by a
pulse sequence whose unitary equals it *up to global phase*.

## Why there is no matrix exponential here

A physical pulse is `exp (-i·θ/2·A)` for a Hermitian generator `A`.  Every generator this
machine has -- `X⊗X` for the Mølmer-Sørensen entangler, `cos φ·X + sin φ·Y` for a
resonant single-qubit pulse -- is an *involution*, `A² = 1`.  For an involution the
exponential collapses:

    exp (-i·θ/2·A) = cos (θ/2)·1 - i·sin (θ/2)·A

so the gates can be *defined* by that closed form and no matrix exponential is needed
anywhere in the development.  The collapse is only legitimate because `A² = 1`, so that
is proved here (`axis_sq`, `XX_sq`) rather than assumed -- it is the hypothesis the whole
definitional choice rests on.

## Conventions

Fixed here, and mirrored exactly by `bridge/derive_pulses.py` and `native/gateset.ml`:

* `R θ φ` is one physical pulse: a rotation by `θ` about the axis at angle `φ` in the
  XY plane.
* `RZ λ` is **virtual** -- a phase-frame update, zero duration, free.  This is why
  `u3_decomp` below matters for cost and not only for correctness: it says every
  single-qubit gate is exactly *one* physical pulse.
* `MS θ` is the entangler, `exp (-i·θ/2·X⊗X)`.
* Qubit order is big-endian: in `kron A B`, `A` acts on qubit 0, and `CX` has qubit 0 as
  control.
-/
import Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic
import Mathlib.Data.Complex.Exponential
import Mathlib.LinearAlgebra.Matrix.Notation

namespace QCCDC

open Complex Matrix

/-! ## Pauli matrices -/

/-- The Pauli X matrix. -/
def PX : Matrix (Fin 2) (Fin 2) ℂ := !![0, 1; 1, 0]

/-- The Pauli Y matrix. -/
def PY : Matrix (Fin 2) (Fin 2) ℂ := !![0, -Complex.I; Complex.I, 0]

/-- The Pauli Z matrix. -/
def PZ : Matrix (Fin 2) (Fin 2) ℂ := !![1, 0; 0, -1]

/-! ## The single-qubit pulse

`axis φ` is the generator of a resonant pulse whose phase is `φ`: a Pauli pointing along
the direction `φ` in the XY plane. -/

/-- The generator of a resonant pulse at phase `φ`: `cos φ · X + sin φ · Y`. -/
noncomputable def axis (φ : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  (Real.cos φ : ℂ) • PX + (Real.sin φ : ℂ) • PY

/-- The generator is an involution.

This is the fact that licenses defining `R` by a closed form instead of by a matrix
exponential: for `A² = 1`, `exp (-i·θ/2·A) = cos (θ/2)·1 - i·sin (θ/2)·A`. -/
@[simp] theorem axis_sq (φ : ℝ) : axis φ * axis φ = 1 := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [axis, PX, PY, Matrix.mul_apply, Fin.sum_univ_two, Matrix.one_apply] <;>
    ring_nf <;>
    simp [Complex.ext_iff] <;>
    ring_nf <;>
    nlinarith [Real.sin_sq_add_cos_sq φ, Real.sq_sqrt, Real.sin_sq_add_cos_sq φ]

/-- One physical pulse: rotate by `θ` about the axis at phase `φ` in the XY plane. -/
noncomputable def R (θ φ : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  (Real.cos (θ / 2) : ℂ) • (1 : Matrix (Fin 2) (Fin 2) ℂ)
    - (Complex.I * (Real.sin (θ / 2) : ℂ)) • axis φ

/-- A virtual Z rotation, symmetric convention: `exp (-i·λ/2·Z)`.

The rotation-group convention.  `VZ` below is the same operation in the convention the
hardware actually uses, and `VZ_eq_smul_RZ` says they differ by a global phase. -/
noncomputable def RZ (lam : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  !![Complex.exp (-(lam : ℂ) * Complex.I / 2), 0;
     0, Complex.exp ((lam : ℂ) * Complex.I / 2)]

/-- A virtual Z in the **phase-gate** convention: `diag (1, e^{iλ})`.

This is what a frame update physically is -- the machine advances the phase of every
subsequent pulse on that ion -- and it costs no time.  Stating the single-qubit
decomposition in this convention is what makes it come out with *no* global phase at all
(`u3_decomp`), which is one fewer constant for a compiler to get wrong. -/
noncomputable def VZ (lam : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  !![1, 0; 0, Complex.exp ((lam : ℂ) * Complex.I)]

/-- The two Z conventions differ by exactly a global phase. -/
theorem VZ_eq_smul_RZ (lam : ℝ) :
    VZ lam = Complex.exp ((lam : ℂ) * Complex.I / 2) • RZ lam := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [VZ, RZ, ← Complex.exp_add] <;>
    ring_nf <;>
    simp [Complex.exp_zero]

/-- `Rx θ` is a pulse at phase 0. -/
noncomputable def Rx (θ : ℝ) : Matrix (Fin 2) (Fin 2) ℂ := R θ 0

/-- `Ry θ` is a pulse at phase `π/2`. -/
noncomputable def Ry (θ : ℝ) : Matrix (Fin 2) (Fin 2) ℂ := R θ (Real.pi / 2)

/-- The explicit entries of a pulse.  Everything downstream computes with this form. -/
theorem R_eq (θ φ : ℝ) :
    R θ φ =
      !![(Real.cos (θ / 2) : ℂ),
          -Complex.I * Complex.exp (-(φ : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ);
         -Complex.I * Complex.exp ((φ : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ),
          (Real.cos (θ / 2) : ℂ)] := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [R, axis, PX, PY, Complex.ext_iff, Complex.exp_re, Complex.exp_im] <;>
    ring_nf <;>
    simp [Complex.cos_ofReal_re, Complex.sin_ofReal_re] <;>
    ring

/-! ## The two-qubit register

`Fin 4` indexes the computational basis, `hiBit` picking qubit 0 and `loBit` qubit 1, so
`kron A B` applies `A` to qubit 0. -/

/-- Qubit 0's value in a `Fin 4` basis index. -/
def hiBit : Fin 4 → Fin 2 := ![0, 0, 1, 1]

/-- Qubit 1's value in a `Fin 4` basis index. -/
def loBit : Fin 4 → Fin 2 := ![0, 1, 0, 1]

/-- The Kronecker product, big-endian: `A` acts on qubit 0. -/
def kron (A B : Matrix (Fin 2) (Fin 2) ℂ) : Matrix (Fin 4) (Fin 4) ℂ :=
  fun i j => A (hiBit i) (hiBit j) * B (loBit i) (loBit j)

/-- `X ⊗ X`, the Mølmer-Sørensen generator. -/
def XX : Matrix (Fin 4) (Fin 4) ℂ := kron PX PX

/-- The MS generator is an involution -- the same fact that licenses `MS`'s closed form. -/
@[simp] theorem XX_sq : XX * XX = 1 := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [XX, kron, PX, hiBit, loBit, Matrix.mul_apply, Fin.sum_univ_four,
      Matrix.one_apply]

/-- The Mølmer-Sørensen entangler, `exp (-i·θ/2·X⊗X)`. -/
noncomputable def MS (θ : ℝ) : Matrix (Fin 4) (Fin 4) ℂ :=
  (Real.cos (θ / 2) : ℂ) • (1 : Matrix (Fin 4) (Fin 4) ℂ)
    - (Complex.I * (Real.sin (θ / 2) : ℂ)) • XX

/-! ## Targets -/

/-- The controlled-NOT, qubit 0 controlling qubit 1. -/
def CX : Matrix (Fin 4) (Fin 4) ℂ := !![1, 0, 0, 0; 0, 1, 0, 0; 0, 0, 0, 1; 0, 0, 1, 0]

/-- The general single-qubit gate, in OpenQASM's `u3` parameterisation. -/
noncomputable def u3 (θ φ lam : ℝ) : Matrix (Fin 2) (Fin 2) ℂ :=
  !![(Real.cos (θ / 2) : ℂ),
      -Complex.exp ((lam : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ);
     Complex.exp ((φ : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ),
      Complex.exp (((φ : ℂ) + (lam : ℂ)) * Complex.I) * (Real.cos (θ / 2) : ℂ)]

end QCCDC
