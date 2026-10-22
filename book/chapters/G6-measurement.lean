import QCCDC.Cert.Sound
import QCCDC.Cert.Commute

/-!
# Measurement and reset

*Part G, lesson G6. You need lessons G1 to G5.*

A measurement is not a unitary: it reads a qubit and leaves it in the state it read. A reset is
not one either: it throws a qubit's state away and starts it at |0⟩. Neither has a matrix, so
lessons G2 to G5 left them out. The semantic check, `check_cert`, compares them anyway, by turning each into a
unitary on one more qubit. This lesson does that exactly, shows why moving a measurement past a
gate on its qubit changes the experiment, and finds the one kind of move that no unitary can
see.
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

/-!
*Lessons G1 to G4 are restated, unseen, above this line: the numbers, matrices, `kron` (qubit 0
the more significant bit), `H`, `CX`, `PX`, `entry`, `UpToPhase` and lesson G4's
`not_phase_of_zero`.*

## 1. A measurement is a CNOT onto a fresh qubit

Measure qubit q in the Z basis and keep the outcome for the end. The same experiment: take a
fresh qubit a in |0⟩, apply a CNOT from q onto a, and measure a at the end. If q holds
α|0⟩ + β|1⟩, the CNOT makes α|00⟩ + β|11⟩: a reads 1 with probability |β|², as q would have, and
afterwards q is in the state a read. `check_cert` says it in one line (`_defer`): "a Z
measurement IS that CNOT, read out at the end".

A two-qubit matrix's first column is what it does to |00⟩: qubit 0 the data, qubit 1 the fresh
one. The probability that qubit 1 reads 1 is the sum of |amplitude|² over the basis states 01
and 11, entries 1 and 3.
-/

def col0 (U : Mat) : List Zω := U.map fun r => nth r 0

/-- The probability that qubit 1 (the fresh one) reads 1, for a two-qubit state. -/
def probFresh1 (v : List Zω) : Zω := nth v 1 * conj (nth v 1) + nth v 3 * conj (nth v 3)

#eval col0 (CX * kron H I2)

/-!
#### Exercise 1 (★) `measure_probability`
`h q[0]; measure q[0];`: the Hadamard, then the measurement deferred onto qubit 1. Prove the state
is (|00⟩ + |11⟩)/√2 and the fresh qubit reads 1 with probability one half, exactly.
-/

theorem measure_probability :
    col0 (CX * kron H I2) = [invSqrt2, 0, 0, invSqrt2] ∧ probFresh1 (col0 (CX * kron H I2)) = half := by
  decide +kernel

/-!
## 2. A reset is a SWAP with a fresh qubit

A reset "hands the old state to a qubit nobody reads and takes a fresh |0⟩" (`_defer` again): a
SWAP with a fresh qubit. Afterwards the data qubit is |0⟩, whatever it held.
-/

def SWAP : Mat := [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]

/-!
#### Exercise 2 (★) `reset_is_swap`
`x q[0]; reset q[0];`: prove that from |00⟩ the deferred form ends in |01⟩: the data qubit back at
0, the 1 it held now in the fresh qubit nobody reads.
-/

theorem reset_is_swap : col0 (SWAP * kron PX I2) = [0, 1, 0, 0] := by
  decide +kernel

/-!
## 3. The plan: which readouts get a fresh qubit

`readout_plan` in `check_cert` pairs the circuit's measurements and resets with the
programme's, qubit by qubit. First the kinds must agree: the k-th readout of a qubit is a
measurement on both sides, or a reset on both. Then "the `j`-th measurement of qubit `q` is a
CNOT onto a fresh qubit of its own, the `j`-th reset a SWAP with one, on BOTH sides, so the two
extended unitaries agree exactly when the readouts happen at equivalent points." One case is
left out: "a measurement that is the last thing to happen to its qubit on both sides", since
"deferring it is exact on each side alone".

Here is the plan for one qubit. A circuit or programme is a list of events; `readoutsOf q` lists
q's readouts with whether each is the last thing done to q; `deferredOf q` numbers the ones that
get a fresh qubit.
-/

inductive Ev where
  | gate (qs : List Nat)
  | measure (q : Nat)
  | reset (q : Nat)

def touches (q : Nat) : Ev → Bool
  | .gate qs => qs.contains q
  | .measure p => p == q
  | .reset p => p == q

/-- Qubit `q`'s readouts in order: their kind, and whether nothing happens to `q` after. -/
def readoutsOf (q : Nat) : List Ev → List (String × Bool)
  | [] => []
  | e :: es =>
    let rest := readoutsOf q es
    let last := !(es.any (touches q))
    match e with
    | .measure p => if p == q then ("measure", last) :: rest else rest
    | .reset p => if p == q then ("reset", last) :: rest else rest
    | .gate _ => rest

/-- Which of `q`'s readouts get a fresh qubit: all but a measurement last on both sides. -/
def deferredOf (q : Nat) (circ prog : List Ev) : List Nat :=
  ((List.range (readoutsOf q circ).length).zip ((readoutsOf q circ).zip (readoutsOf q prog))).filterMap
    fun (j, (ka, la), (_, lb)) => if ka == "measure" && la && lb then none else some j

/-!
The Bell circuit ends `measure q -> c;`, one measurement per qubit, and the programme ends with
one `measure` instruction on both ions. The Compilation page's verdict for the Bell pair reports
"0 measurement(s)/reset(s) compared in place, final measurements at the end"; the `reset`
example's verdict reports 1.
-/

def bellCirc : List Ev := [.gate [0], .gate [0, 1], .measure 0, .measure 1]
/-- The programme's events, read over the qubits: seven pulses, then the readout of both. -/
def bellProg : List Ev :=
  [.gate [0], .gate [0], .gate [0], .gate [0, 1], .gate [0], .gate [1], .gate [0], .measure 0, .measure 1]

/-!
#### Exercise 3 (★) `bell_nothing_deferred`
Prove the Bell certificate defers nothing, on either qubit.
-/

theorem bell_nothing_deferred : deferredOf 0 bellCirc bellProg = [] ∧ deferredOf 1 bellCirc bellProg = [] := by
  decide +kernel

/-!
## 4. A measurement moved before a gate

Now a wrong programme. The circuit is `h q[0]; measure q[0];`. The programme measures `q0` first
and applies the Hadamard after. Each side measures qubit 0 once, so the kinds agree; but on the
programme's side something happens to qubit 0 after the measurement, so it is not last on both
sides, and the plan gives it a fresh qubit.

#### Exercise 4 (★) `moved_is_deferred`
Prove it.
-/

theorem moved_is_deferred : deferredOf 0 [.gate [0], .measure 0] [.measure 0, .gate [0]] = [0] := by
  decide +kernel

/-!
With the measurement deferred onto qubit 1, the circuit is H then the CNOT, and the programme is
the CNOT then H. They are different experiments: measured after the Hadamard, the qubit reads 1
half the time; measured before it, on |0⟩, never.

#### Exercise 5 (★) `moved_changes_probability`
Prove both probabilities, exactly.
-/

theorem moved_changes_probability :
    probFresh1 (col0 (kron H I2 * CX)) = 0 ∧ probFresh1 (col0 (CX * kron H I2)) = half := by
  decide +kernel

/-!
#### Exercise 6 (★★) `moved_refused`
Prove the two extended unitaries are not equal up to any phase, which is how `check_cert`
refuses the programme. Use `not_phase_of_zero` at an entry where the programme's matrix is 0 and
the circuit's is not: from |00⟩, the circuit reaches |11⟩ and the programme does not.
-/

theorem moved_refused : ¬ UpToPhase (CX * kron H I2) (kron H I2 * CX) :=
  not_phase_of_zero (i := 3) (j := 0) (by decide +kernel) (by decide +kernel)

/-!
## 5. A move no unitary can see

`readout_plan` adds: "Two readouts that commute with what lies between them stay equal (a Z
measurement before or after a CNOT it controls), as they should." Take `cx q[0],q[1];
measure q[0];` and a programme that measures `q0` before the CNOT. The plan defers the
measurement onto a fresh qubit 2, and the two sides become CX(0→1) then CX(0→2), against CX(0→2)
then CX(0→1): two CNOTs with the same control.

Those commute, and the proved checker has the proof. The module `QCCDC.Cert.Commute`
writes `cx c t` as the permutation it is on basis states, `cxAct c t s` (s gives each qubit its
bit), and proves `cxAct_comm_same_control`. A permutation matrix is determined by its permutation,
so equal permutations are equal unitaries: the semantic check cannot tell these two programmes
apart. Lesson F6 used the same theorem on two CNOTs of a circuit; here the second CNOT is a
measurement, deferred.
-/

#check @QCCDC.Cert.cxAct_comm_same_control

/-!
#### Exercise 7 (★★) `deferred_reorder_invisible`
Prove the two orders act the same on every basis state, by applying that theorem. Its three
hypotheses are facts about the numbers 0, 1 and 2.
-/

theorem deferred_reorder_invisible (s : Nat → Bool) :
    QCCDC.Cert.cxAct 0 2 (QCCDC.Cert.cxAct 0 1 s) = QCCDC.Cert.cxAct 0 1 (QCCDC.Cert.cxAct 0 2 s) :=
  QCCDC.Cert.cxAct_comm_same_control (by decide) (by decide) (by decide) s

/-!
## 6. The strict order

So the programme with the early measurement passes the semantic check, correctly as far as
unitaries go. It is refused anyway, by a different check, `check_program_order` in
`check_cert`: "STRICT: two ops on one qubit must run in their source order even when they
commute. The order of a qubit's gates is the circuit author's to choose". Its docstring records
why: "A compiler that batched a check's CNOTs by rotation offset passed every one of them with 726
adjacent pairs out of order on a BB144 round."

The proved checker of Part F has the same rule for gates, `RespectsOrder`. A measurement needs no
witness there (`Covered` exempts measurements, resets and barriers), so a measurement's place in
its qubit's order is held by `check_program_order` alone, and that is computed, not proved.

The Compilation page's `reset` example is the extreme case. The circuit is `reset q[0];`; the
programme is one `reset` instruction; the certificate has the one op and no witness at all. Here
it is as the proved checker reads it ([reset.qcheck.json](data/reset.qcheck.json)). The checker accepts it, and has
nothing to say about where, or whether, the reset happened: what holds the programme to it is
computed, the `reset` instruction's place by `check_program_order` and its kind by the readout
plan ("1 measurement(s)/reset(s) compared in place", says its verdict on the Compilation page).
-/

open QCCDC.Cert in
def resetCert : Input :=
  { circuit := "circuit", arch := "race8", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "S0"), ("q1", "S1")],
    ops := [⟨0, "reset", [0]⟩],
    moves := [],
    rots := [],
    gates := [],
    unrealised := [],
    gateSites := ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"],
    hops := [("S0", "S1"), ("S0", "S7"), ("S1", "S0"), ("S1", "S2"), ("S2", "S1"), ("S2", "S3"),
      ("S3", "S2"), ("S3", "S4"), ("S4", "S3"), ("S4", "S5"), ("S5", "S4"), ("S5", "S6"),
      ("S6", "S5"), ("S6", "S7"), ("S7", "S6"), ("S7", "S0")],
    loops := [("L0", ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"])] }

#eval QCCDC.Cert.check resetCert

/-!
{{figure resetCert}}

Here is the order rule, for programmes where each op is one instruction: on every qubit, the
programme performs the ops that touch it in the circuit's order.
-/

/-- An op: its index in the circuit, and its qubits. -/
structure OpQ where
  idx : Nat
  qubits : List Nat

/-- The ops that touch qubit `q`, in the order they are performed. -/
def seqOn (q : Nat) (ops : List OpQ) : List Nat := (ops.filter (·.qubits.contains q)).map (·.idx)

def strictOrder (n : Nat) (circ prog : List OpQ) : Bool :=
  (List.range n).all fun q => seqOn q prog == seqOn q circ

/-- `cx q[0],q[1]; measure q[0];` as op 0 and op 1. -/
def cxThenMeasure : List OpQ := [⟨0, [0, 1]⟩, ⟨1, [0]⟩]

/-!
#### Exercise 8 (★) `order_refuses`
Prove the strict order refuses the programme that measures first, and accepts the faithful one.
-/

theorem order_refuses :
    strictOrder 2 cxThenMeasure [⟨1, [0]⟩, ⟨0, [0, 1]⟩] = false ∧
    strictOrder 2 cxThenMeasure cxThenMeasure = true := by
  decide +kernel

/-!
The rule is per qubit. Two ops on different qubits may run in either order, or at once: "pulses
on OTHER qubits may overlap, which is parallelism, not reordering." That is a theorem about every
programme.

#### Exercise 9 (★★★) `disjoint_swap`
Prove that exchanging two adjacent ops that do not both touch qubit q leaves q's sequence
unchanged, wherever they stand in the programme. Induction on the ops before them; at the pair
itself, four cases on whether each touches q (`by_cases`), and `simp_all` with the definitions.
-/

theorem disjoint_swap (q : Nat) (pre post : List OpQ) (a b : OpQ)
    (h : ¬ (a.qubits.contains q = true ∧ b.qubits.contains q = true)) :
    seqOn q (pre ++ a :: b :: post) = seqOn q (pre ++ b :: a :: post) := by
  induction pre with
  | nil =>
    simp only [seqOn, List.nil_append]
    by_cases ha : a.qubits.contains q = true <;> by_cases hb : b.qubits.contains q = true <;>
      simp_all [List.filter]
  | cons x xs ih =>
    simp only [seqOn, List.cons_append] at ih ⊢
    by_cases hx : x.qubits.contains q = true <;> simp_all [List.filter]

/-!
#### Exercise 10 (★★) `parallel_ok`
Apply it: an op on qubit 0 and an op on qubit 1, in either order, give every qubit the same
sequence.
-/

theorem parallel_ok (q : Nat) :
    seqOn q [⟨0, [0]⟩, ⟨1, [1]⟩] = seqOn q [⟨1, [1]⟩, ⟨0, [0]⟩] :=
  disjoint_swap q [] [] ⟨0, [0]⟩ ⟨1, [1]⟩ (by intro ⟨h0, h1⟩; simp at h0 h1; omega)

/-!
## 7. What this lesson proved

Deferred, a measurement is a CNOT onto a fresh qubit and a reset a SWAP with one, so readouts
take part in the exact comparison like gates; moving a measurement before a gate on its qubit is
a different experiment, and refused. Moving it past a CNOT it controls is the same experiment as
far as any unitary can tell, and is refused by the strict order alone. Lesson G7 puts G1 to G6
together on the Bell certificate.
-/
