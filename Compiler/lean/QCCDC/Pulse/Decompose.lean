/-
# O2: every gate is a native pulse sequence

`u3_decomp` is the theorem that matters for **cost** as well as for correctness.  It says
the general single-qubit gate is one virtual frame update composed with exactly **one
physical pulse** -- not the three a naive Euler (Z-X-Z) decomposition would emit.  `VZ`
has no duration, so a compiler that believed the Euler form would pay three times over
for every single-qubit gate in the program.

Stated in the hardware's phase-gate convention it also comes out with *no* global phase,
which is one fewer constant for a compiler to get wrong.  `bridge/derive_pulses.py`
searched for the form; this file is what establishes it.

## A note on how the proof is arranged

The obvious one-liner -- unfold everything and let `simp`/`ring_nf` finish -- does not
work, and the reason is worth recording so nobody retries it.  `ring_nf` rewrites the
*argument* of `Complex.exp` into a normal form that no longer matches any stated lemma
about `exp (i·π/2)`, so the one place π appears becomes unreachable exactly when it is
needed.  So π is discharged first, in `R_pi_half_sub`, under full manual control; after
that the main proof contains no π at all and the remaining algebra is just `exp_add`.
-/
import QCCDC.Pulse.Native

namespace QCCDC

open Complex Matrix

/-! ## Groundwork: the quarter-turn phases -/

/-- `e^{i·π/2} = i`. -/
theorem exp_pi_div_two_mul_I :
    Complex.exp (((Real.pi / 2 : ℝ) : ℂ) * Complex.I) = Complex.I := by
  rw [Complex.exp_mul_I]
  push_cast
  simp

/-- `e^{-i·π/2} = -i`. -/
theorem exp_neg_pi_div_two_mul_I :
    Complex.exp ((-((Real.pi / 2 : ℝ) : ℂ)) * Complex.I) = -Complex.I := by
  rw [neg_mul, Complex.exp_neg, exp_pi_div_two_mul_I]
  simp

/-- The pulse the single-qubit decomposition actually uses, with π already discharged.

`R θ (π/2 − λ)` is the axis a virtual-Z frame of `λ` leaves pointing along, and writing
it out here is what keeps π out of `u3_decomp`. -/
theorem R_pi_half_sub (θ lam : ℝ) :
    R θ (Real.pi / 2 - lam) =
      !![(Real.cos (θ / 2) : ℂ),
          -(Complex.exp ((lam : ℂ) * Complex.I) * (Real.sin (θ / 2) : ℂ));
         Complex.exp ((-(lam : ℂ)) * Complex.I) * (Real.sin (θ / 2) : ℂ),
          (Real.cos (θ / 2) : ℂ)] := by
  have hp : ((Real.pi / 2 - lam : ℝ) : ℂ) * Complex.I
      = ((Real.pi / 2 : ℝ) : ℂ) * Complex.I + (-(lam : ℂ)) * Complex.I := by
    push_cast; ring
  have hn : (-((Real.pi / 2 - lam : ℝ) : ℂ)) * Complex.I
      = (-((Real.pi / 2 : ℝ) : ℂ)) * Complex.I + (lam : ℂ) * Complex.I := by
    push_cast; ring
  -- π is discharged HERE, by `rw` on the whole goal, before `ext` or `simp` runs.  Doing
  -- it inside a `simp` fails: simp first normalises the argument of `exp`, after which
  -- no lemma about `exp (i·π/2)` matches any more.
  rw [R_eq, hp, hn, Complex.exp_add, Complex.exp_add, exp_pi_div_two_mul_I,
    exp_neg_pi_div_two_mul_I]
  ext i j
  -- `ring` introduces `I ^ 2`, so `I_sq` has to come after it, not before
  fin_cases i <;> fin_cases j <;> simp <;> ring_nf <;> simp [Complex.I_sq]

/-! ## The single-qubit gate is one pulse -/

/-- **u3(θ,φ,λ) = VZ(φ+λ) · R(θ, π/2 − λ)** -- exactly, with no global phase.

One virtual frame update and one physical pulse.  `VZ` costs no time, so this says the
*physical* cost of any single-qubit gate is exactly one pulse. -/
theorem u3_decomp (θ φ lam : ℝ) :
    u3 θ φ lam = VZ (φ + lam) * R θ (Real.pi / 2 - lam) := by
  -- align the coercion in `VZ`'s exponent with the one `u3` uses, so the two are
  -- syntactically comparable rather than merely equal
  have hVZ : VZ (φ + lam)
      = !![1, 0; 0, Complex.exp (((φ : ℂ) + (lam : ℂ)) * Complex.I)] := by
    unfold VZ; push_cast; rfl
  -- the one piece of exponential algebra the whole theorem needs, stated already
  -- associated the way the matrix product leaves it
  have hc : ∀ s : ℂ,
      Complex.exp (((φ : ℂ) + (lam : ℂ)) * Complex.I)
          * (Complex.exp ((-(lam : ℂ)) * Complex.I) * s)
        = Complex.exp ((φ : ℂ) * Complex.I) * s := by
    intro s
    rw [← mul_assoc, ← Complex.exp_add]
    ring_nf
  -- `hc` is applied here, at the goal level, for the same reason `hp`/`hn` were:
  -- simp reassociates the exponent and the rewrite stops matching.
  rw [R_pi_half_sub, hVZ, Matrix.mul_fin_two, hc]
  ext i j
  fin_cases i <;> fin_cases j <;> simp [u3]

/-! ## The entangler

The CX-from-MS identity, and its global phase.  The phase is the reason this is proved
rather than quoted: the identity appears in the literature under at least three
conventions, and a wrong global phase is invisible on its own but observable the moment
the gate is controlled.

The proof keeps the quarter-turn amplitudes `cos (π/4)` and `sin (π/4)` **symbolic** all
the way to the last step.  Expanding them to `√2/2` early would put a square root inside
sixty-four-term matrix sums; instead the entries stay polynomials in one variable, and the
only arithmetic facts needed at the end are `sin (π/4) = cos (π/4)` and `cos (π/4)² = 1/2`.
-/

/-- `Rx(-π/2)` at its one used angle. -/
theorem Rx_neg_pi_half :
    Rx (-(Real.pi / 2)) =
      !![(Real.cos (Real.pi / 4) : ℂ), Complex.I * (Real.sin (Real.pi / 4) : ℂ);
         Complex.I * (Real.sin (Real.pi / 4) : ℂ), (Real.cos (Real.pi / 4) : ℂ)] := by
  have h1 : -(Real.pi / 2) / 2 = -(Real.pi / 4) := by ring
  unfold Rx R axis
  rw [h1]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [PX, PY, Real.cos_neg, Real.sin_neg, Matrix.one_apply] <;> ring

/-- `Ry(-π/2)` at its one used angle. -/
theorem Ry_neg_pi_half :
    Ry (-(Real.pi / 2)) =
      !![(Real.cos (Real.pi / 4) : ℂ), (Real.sin (Real.pi / 4) : ℂ);
         -(Real.sin (Real.pi / 4) : ℂ), (Real.cos (Real.pi / 4) : ℂ)] := by
  have h1 : -(Real.pi / 2) / 2 = -(Real.pi / 4) := by ring
  have h2 : Real.cos (Real.pi / 2) = 0 := Real.cos_pi_div_two
  have h3 : Real.sin (Real.pi / 2) = 1 := Real.sin_pi_div_two
  unfold Ry R axis
  rw [h1]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [PX, PY, Real.cos_neg, Real.sin_neg, Matrix.one_apply, h2, h3] <;>
    ring_nf <;> simp [Complex.I_sq] <;> ring

/-- `Ry(π/2)` at its one used angle. -/
theorem Ry_pi_half :
    Ry (Real.pi / 2) =
      !![(Real.cos (Real.pi / 4) : ℂ), -(Real.sin (Real.pi / 4) : ℂ);
         (Real.sin (Real.pi / 4) : ℂ), (Real.cos (Real.pi / 4) : ℂ)] := by
  have h1 : Real.pi / 2 / 2 = Real.pi / 4 := by ring
  have h2 : Real.cos (Real.pi / 2) = 0 := Real.cos_pi_div_two
  have h3 : Real.sin (Real.pi / 2) = 1 := Real.sin_pi_div_two
  unfold Ry R axis
  rw [h1]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [PX, PY, Matrix.one_apply, h2, h3] <;>
    ring_nf <;> simp [Complex.I_sq] <;> ring

/-- `MS(π/2)`, the maximally entangling operating point. -/
theorem MS_pi_half :
    MS (Real.pi / 2) =
      (Real.cos (Real.pi / 4) : ℂ) • (1 : Matrix (Fin 4) (Fin 4) ℂ)
        - (Complex.I * (Real.sin (Real.pi / 4) : ℂ)) • XX := by
  have h1 : Real.pi / 2 / 2 = Real.pi / 4 := by ring
  unfold MS
  rw [h1]

/-- `sin (π/4) = cos (π/4)`, over ℂ. -/
theorem sin_eq_cos_pi_div_four :
    ((Real.sin (Real.pi / 4) : ℝ) : ℂ) = ((Real.cos (Real.pi / 4) : ℝ) : ℂ) := by
  rw [Real.sin_pi_div_four, Real.cos_pi_div_four]

/-- `cos (π/4)² = 1/2`, over ℂ.  The only place `√2` is touched. -/
theorem cos_pi_div_four_sq : ((Real.cos (Real.pi / 4) : ℝ) : ℂ) ^ 2 = 1 / 2 := by
  rw [Real.cos_pi_div_four]
  push_cast
  rw [div_pow]
  norm_num
  rw [show ((Real.sqrt 2 : ℝ) : ℂ) ^ 2 = ((Real.sqrt 2 ^ 2 : ℝ) : ℂ) by push_cast; ring,
    Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 2)]
  norm_num

/-- `(√2)² = 2`, over ℂ.  Everything below reduces to a power of this. -/
theorem sqrtTwo_sq : ((Real.sqrt 2 : ℝ) : ℂ) ^ 2 = 2 := by
  rw [show ((Real.sqrt 2 : ℝ) : ℂ) ^ 2 = (((Real.sqrt 2 ^ 2 : ℝ)) : ℂ) by push_cast; ring,
    Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 2)]
  norm_num

@[simp] theorem sqrtTwo_pow_four : ((Real.sqrt 2 : ℝ) : ℂ) ^ 4 = 4 := by
  calc ((Real.sqrt 2 : ℝ) : ℂ) ^ 4 = (((Real.sqrt 2 : ℝ) : ℂ) ^ 2) ^ 2 := by ring
  _ = 4 := by rw [sqrtTwo_sq]; norm_num

@[simp] theorem sqrtTwo_pow_six : ((Real.sqrt 2 : ℝ) : ℂ) ^ 6 = 8 := by
  calc ((Real.sqrt 2 : ℝ) : ℂ) ^ 6 = (((Real.sqrt 2 : ℝ) : ℂ) ^ 2) ^ 3 := by ring
  _ = 8 := by rw [sqrtTwo_sq]; norm_num

set_option maxHeartbeats 2000000 in
/-- **CX = e^{−iπ/4} · [Ry(−π/2)⊗I] · [Rx(−π/2)⊗Rx(−π/2)] · MS(π/2) · [Ry(π/2)⊗I]**

Matrix order: the rightmost factor is the first pulse in time.  One MS gate and four
single-qubit pulses. -/
theorem cx_decomp :
    CX =
      Complex.exp ((-(Real.pi / 4) : ℝ) * Complex.I) •
        (kron (Ry (-(Real.pi / 2))) 1
          * kron (Rx (-(Real.pi / 2))) (Rx (-(Real.pi / 2)))
          * MS (Real.pi / 2)
          * kron (Ry (Real.pi / 2)) 1) := by
  have hphase : Complex.exp ((-(Real.pi / 4) : ℝ) * Complex.I)
      = ((Real.cos (Real.pi / 4) : ℝ) : ℂ)
        - Complex.I * ((Real.sin (Real.pi / 4) : ℝ) : ℂ) := by
    rw [Complex.exp_mul_I]
    push_cast
    simp [Complex.cos_ofReal_re]
    ring
  rw [Rx_neg_pi_half, Ry_neg_pi_half, Ry_pi_half, MS_pi_half, hphase]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [CX, kron, hiBit, loBit, XX, PX, Matrix.mul_apply, Fin.sum_univ_four,
      Matrix.one_apply, sin_eq_cos_pi_div_four] <;>
    ring_nf <;>
    simp [cos_pi_div_four_sq, sqrtTwo_sq, sqrtTwo_pow_four, sqrtTwo_pow_six] <;>
    ring

/-! ## The other entangler: `RZZ`

The `rzz` gate set (Quantinuum's H-series and Helios, arXiv:2003.01293 and
arXiv:2511.05465) has `RZZ θ = exp (-i·θ/2·Z⊗Z)` where the `ms` set has `MS θ`.  Three
facts about it, each with its global phase:

* `cz_decomp`, `cz_decomp_vz`: `RZZ(π/2)` is a controlled-Z up to a quarter-turn frame
  change on each qubit, in the rotation convention `RZ` and in the hardware's `VZ`;
* `cx_rzz_via_cz`, `cx_decomp_rzz`: so a CNOT is **one `RZZ`, two physical one-qubit
  pulses** (the target's two Hadamards, one pulse each by `u3_decomp`) **and frame
  changes** -- against one `MS` and four physical pulses in `cx_decomp`.  The first is
  read off the CZ; the second folds the Hadamards' frame changes into the pulses' axes
  and is the sequence a compiler emits, as `cx_decomp` is for the `ms` set;
* `MS_eq_conj_RZZ`: at every angle the two entanglers differ by a Hadamard on each
  qubit, so anything one set can do the other can, with local basis changes.

`RZZ` is diagonal in the computational basis.  A syndrome circuit lowered to it stays a
CSS circuit up to one-qubit basis changes, the class Ellis (Hou and Ravi,
arXiv:2611.26173) simulates under qubit loss and leakage with a Pauli frame and per-shot
masks.  That is a reason to choose the set; nothing here simulates leakage. -/

/-- `e^{iπ/4} = cos (π/4) + i·sin (π/4)`, in the casts the pulse definitions use. -/
theorem exp_pi_div_four_mul_I :
    Complex.exp (((Real.pi / 4 : ℝ) : ℂ) * Complex.I)
      = ((Real.cos (Real.pi / 4) : ℝ) : ℂ)
        + Complex.I * ((Real.sin (Real.pi / 4) : ℝ) : ℂ) := by
  rw [Complex.exp_mul_I, ← Complex.ofReal_cos, ← Complex.ofReal_sin]
  ring

/-- `e^{-iπ/4} = cos (π/4) − i·sin (π/4)`. -/
theorem exp_neg_pi_div_four_mul_I :
    Complex.exp ((-((Real.pi / 4 : ℝ) : ℂ)) * Complex.I)
      = ((Real.cos (Real.pi / 4) : ℝ) : ℂ)
        - Complex.I * ((Real.sin (Real.pi / 4) : ℝ) : ℂ) := by
  rw [Complex.exp_mul_I, Complex.cos_neg, Complex.sin_neg, ← Complex.ofReal_cos,
    ← Complex.ofReal_sin]
  ring

/-- `RZ(-π/2) = diag (e^{iπ/4}, e^{-iπ/4})`, at its one used angle. -/
theorem RZ_neg_pi_half :
    RZ (-(Real.pi / 2)) =
      !![Complex.exp (((Real.pi / 4 : ℝ) : ℂ) * Complex.I), 0;
         0, Complex.exp ((-((Real.pi / 4 : ℝ) : ℂ)) * Complex.I)] := by
  have h1 : -((-(Real.pi / 2) : ℝ) : ℂ) * Complex.I / 2
      = ((Real.pi / 4 : ℝ) : ℂ) * Complex.I := by
    push_cast; ring
  have h2 : ((-(Real.pi / 2) : ℝ) : ℂ) * Complex.I / 2
      = (-((Real.pi / 4 : ℝ) : ℂ)) * Complex.I := by
    push_cast; ring
  -- as in `R_pi_half_sub`: the angle is put in shape by `rw` before any `simp` sees it
  unfold RZ
  rw [h1, h2]

/-- `VZ(-π/2) = diag (1, -i)`: the frame change that turns `RZZ(π/2)` into a CZ. -/
theorem VZ_neg_pi_half : VZ (-(Real.pi / 2)) = !![1, 0; 0, -Complex.I] := by
  have h : ((-(Real.pi / 2) : ℝ) : ℂ) * Complex.I
      = (-((Real.pi / 2 : ℝ) : ℂ)) * Complex.I := by
    push_cast; ring
  unfold VZ
  rw [h, exp_neg_pi_div_two_mul_I]

/-- `RZZ(π/2)`, the maximally entangling operating point. -/
theorem RZZ_pi_half :
    RZZ (Real.pi / 2) =
      (Real.cos (Real.pi / 4) : ℂ) • (1 : Matrix (Fin 4) (Fin 4) ℂ)
        - (Complex.I * (Real.sin (Real.pi / 4) : ℂ)) • ZZ := by
  have h1 : Real.pi / 2 / 2 = Real.pi / 4 := by ring
  unfold RZZ
  rw [h1]

/-- **CZ = e^{−iπ/4} · [RZ(−π/2)⊗RZ(−π/2)] · RZZ(π/2)**

`RZZ(π/2)` is a controlled-Z up to a quarter turn about Z on each qubit and a global
phase.  Every factor is diagonal, so the order of the factors does not matter. -/
theorem cz_decomp :
    CZ =
      Complex.exp ((-(Real.pi / 4) : ℝ) * Complex.I) •
        (kron (RZ (-(Real.pi / 2))) (RZ (-(Real.pi / 2))) * RZZ (Real.pi / 2)) := by
  have hphase : Complex.exp ((-(Real.pi / 4) : ℝ) * Complex.I)
      = ((Real.cos (Real.pi / 4) : ℝ) : ℂ)
        - Complex.I * ((Real.sin (Real.pi / 4) : ℝ) : ℂ) := by
    rw [Complex.ofReal_neg, exp_neg_pi_div_four_mul_I]
  rw [RZ_neg_pi_half, exp_pi_div_four_mul_I, exp_neg_pi_div_four_mul_I, RZZ_pi_half, hphase]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [CZ, kron, hiBit, loBit, ZZ, PZ, Matrix.mul_apply, Fin.sum_univ_four,
      Matrix.one_apply] <;>
    ring_nf <;>
    simp [sqrtTwo_pow_four] <;>
    ring

/-- **CZ = e^{iπ/4} · [VZ(−π/2)⊗VZ(−π/2)] · RZZ(π/2)**

The same identity in the hardware's phase-gate convention, the one a compiler emits: one
`RZZ` and one frame change per qubit.  The global phase is `+π/4` here and `−π/4` in
`cz_decomp` because `VZ` and `RZ` differ by a phase themselves (`VZ_eq_smul_RZ`). -/
theorem cz_decomp_vz :
    CZ =
      Complex.exp ((Real.pi / 4 : ℝ) * Complex.I) •
        (kron (VZ (-(Real.pi / 2))) (VZ (-(Real.pi / 2))) * RZZ (Real.pi / 2)) := by
  rw [VZ_neg_pi_half, exp_pi_div_four_mul_I, RZZ_pi_half]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [CZ, kron, hiBit, loBit, ZZ, PZ, Matrix.mul_apply, Fin.sum_univ_four,
      Matrix.one_apply] <;>
    ring_nf <;>
    simp [sqrtTwo_sq] <;>
    ring

/-- **CX = (1⊗H) · CZ · (1⊗H)**: a CNOT is a controlled-Z between two Hadamards on the
target. -/
theorem cx_from_cz : CX = kron 1 Had * CZ * kron 1 Had := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [CX, CZ, Had, kron, hiBit, loBit, Matrix.mul_apply, Fin.sum_univ_four,
      Matrix.one_apply] <;>
    ring_nf <;>
    simp [sqrtTwo_sq]

/-- The Hadamard is `u3 (π/2) 0 π`. -/
theorem Had_eq_u3 : Had = u3 (Real.pi / 2) 0 Real.pi := by
  have h1 : Real.pi / 2 / 2 = Real.pi / 4 := by ring
  unfold u3 Had
  rw [h1]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [Complex.exp_pi_mul_I, Real.cos_pi_div_four, Real.sin_pi_div_four]

/-- **H = VZ(π) · R(π/2, −π/2)**: one frame change and one physical pulse, by
`u3_decomp`. -/
theorem Had_decomp : Had = VZ Real.pi * R (Real.pi / 2) (-(Real.pi / 2)) := by
  have h := u3_decomp (Real.pi / 2) 0 Real.pi
  rw [zero_add, show Real.pi / 2 - Real.pi = -(Real.pi / 2) by ring] at h
  rw [Had_eq_u3, h]

/-- **CX = e^{iπ/4} · [1⊗H] · [VZ(−π/2)⊗VZ(−π/2)] · RZZ(π/2) · [1⊗H]**, with
**H = VZ(π) · R(π/2, −π/2)**: the CNOT of the `rzz` set, read off the three facts above.

Matrix order: the rightmost factor is the first pulse in time.  One `RZZ` gate, two
physical one-qubit pulses (both on the target, qubit 1) and four frame changes -- where
`cx_decomp` has one `MS` gate and four physical pulses. -/
theorem cx_rzz_via_cz :
    CX =
      Complex.exp ((Real.pi / 4 : ℝ) * Complex.I) •
        (kron 1 (VZ Real.pi * R (Real.pi / 2) (-(Real.pi / 2)))
          * kron (VZ (-(Real.pi / 2))) (VZ (-(Real.pi / 2)))
          * RZZ (Real.pi / 2)
          * kron 1 (VZ Real.pi * R (Real.pi / 2) (-(Real.pi / 2)))) := by
  -- no entry is computed: fold the two pulses back into `Had`, bring the phase beside the
  -- three diagonal factors, and the middle is `cz_decomp_vz`
  rw [← Had_decomp, Matrix.mul_assoc (kron 1 Had), ← Matrix.smul_mul, ← Matrix.mul_smul,
    ← cz_decomp_vz]
  exact cx_from_cz

/-- `R(π/2, π)`, at its one used angle: a quarter turn about `−X`, the same matrix as
`Rx(−π/2)`. -/
theorem R_pi_half_pi :
    R (Real.pi / 2) Real.pi =
      !![(Real.cos (Real.pi / 4) : ℂ), Complex.I * (Real.sin (Real.pi / 4) : ℂ);
         Complex.I * (Real.sin (Real.pi / 4) : ℂ), (Real.cos (Real.pi / 4) : ℂ)] := by
  have h1 : Real.pi / 2 / 2 = Real.pi / 4 := by ring
  unfold R axis
  rw [h1]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [PX, PY, Real.cos_pi, Real.sin_pi]

/-- **CX = e^{iπ/4} · [VZ(−π/2)⊗VZ(−π/2)] · [1⊗R(π/2, π)] · RZZ(π/2) · [1⊗R(−π/2, π/2)]**

The same CNOT with each Hadamard's frame change folded into the pulses' axes: the form
with the fewest operations, and the one a compiler emits for the `rzz` set.  Matrix
order: the rightmost factor is the first pulse in time.  One `RZZ` gate, two physical
one-qubit pulses, both on the target (qubit 1), and one frame change per qubit. -/
theorem cx_decomp_rzz :
    CX =
      Complex.exp ((Real.pi / 4 : ℝ) * Complex.I) •
        (kron (VZ (-(Real.pi / 2))) (VZ (-(Real.pi / 2)))
          * kron 1 (R (Real.pi / 2) Real.pi)
          * RZZ (Real.pi / 2)
          * kron 1 (R (-(Real.pi / 2)) (Real.pi / 2))) := by
  rw [show R (-(Real.pi / 2)) (Real.pi / 2) = Ry (-(Real.pi / 2)) from rfl, Ry_neg_pi_half,
    R_pi_half_pi, VZ_neg_pi_half, RZZ_pi_half, exp_pi_div_four_mul_I]
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [CX, kron, hiBit, loBit, ZZ, PZ, Matrix.mul_apply, Fin.sum_univ_four,
      Matrix.one_apply] <;>
    ring_nf <;>
    simp [sqrtTwo_pow_four] <;>
    ring

/-- `(H⊗H)² = 1`. -/
theorem HH_sq : kron Had Had * kron Had Had = 1 := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [Had, kron, hiBit, loBit, Matrix.mul_apply, Fin.sum_univ_four] <;>
    ring_nf <;>
    simp [sqrtTwo_pow_four]

/-- `(H⊗H) · Z⊗Z · (H⊗H) = X⊗X`: a Hadamard on each qubit turns one generator into the
other. -/
theorem HH_ZZ_HH : kron Had Had * ZZ * kron Had Had = XX := by
  ext i j
  fin_cases i <;> fin_cases j <;>
    simp [Had, ZZ, XX, PX, PZ, kron, hiBit, loBit, Matrix.mul_apply, Fin.sum_univ_four] <;>
    ring_nf <;>
    simp [sqrtTwo_pow_four]

/-- **MS(θ) = (H⊗H) · RZZ(θ) · (H⊗H)**, at every angle.

The two native gate sets differ by local basis changes.  No entry is computed: the
closed forms of `MS` and `RZZ` are the same expression in their generators, and
`HH_ZZ_HH` carries one generator to the other. -/
theorem MS_eq_conj_RZZ (θ : ℝ) : MS θ = kron Had Had * RZZ θ * kron Had Had := by
  unfold MS RZZ
  simp only [mul_sub, sub_mul, Matrix.mul_smul, Matrix.smul_mul, Matrix.mul_one, HH_sq,
    HH_ZZ_HH]

/-! ## The trusted-base check

`u3_decomp` is the statement the compiler's cost model and its decomposition table both
rest on, so what it depends on is worth printing rather than assuming.  Anything beyond
Lean's three standard axioms -- in particular a `sorryAx` -- would mean the theorem proves
nothing. -/

#print axioms u3_decomp
#print axioms R_pi_half_sub
#print axioms axis_sq
#print axioms XX_sq
#print axioms VZ_eq_smul_RZ
#print axioms cx_decomp
#print axioms ZZ_sq
#print axioms cz_decomp
#print axioms cz_decomp_vz
#print axioms cx_from_cz
#print axioms Had_decomp
#print axioms cx_rzz_via_cz
#print axioms cx_decomp_rzz
#print axioms MS_eq_conj_RZZ

end QCCDC
