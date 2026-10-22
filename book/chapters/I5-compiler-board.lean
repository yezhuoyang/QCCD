import QCCDC.Cert.Sound

/-!
# The Compiler board

*Part I, lesson I5, the last of the tutorial. You need lessons I1 to I4.*

The Leaderboard has two sections. On the **Architecture** boards a person designs a device and
our compiler, `qccdc`, compiles each board's circuit onto it. On the **Compiler** board a person
submits a compiler, and the grader runs it on every pair of a benchmark suite, a circuit and a
device, and grades every programme it writes. This lesson is how to enter: the contract a
compiler must keep, how each pair is graded, how an entry is scored and ranked, and how to submit.
Lean comes in at the end of each part, to state the board's rules exactly and prove what follows
from them.

The suite, `suite@1`, is every pair of twelve circuits
(four QEC memory experiments, six Clifford circuits, two small non-Clifford ones) and nine
reference devices: 108 public pairs, and 8 hidden ones that only the official server generates.

## 1. The contract, `qccd.compiler@1`

A compiler is a directory with a manifest, `qccd-compiler.json`:

```
{"kind": "qccd.compiler", "version": 1,
 "name": "my-compiler", "about": "one line on what it does",
 "runtime": "python3", "entry": ["python3", "<your script>"], "build": null}
```

* `kind` and `version` exactly as shown; `name` of 1 to 80 characters, the one people see;
* `runtime`: `python3`, the grader's own Python, with numpy, stim, qiskit and the `qccd` package
  importable; or `native`, a static linux-x86_64 executable inside the directory (the grader has
  no build tools);
* `entry`: the command, as a list of arguments; there is no shell, so nothing in it is
  interpreted;
* `build`: `null`, or a command run once before any pair, with no network.

The directory holds at most 2000 files and 64 MB, and no links. For each pair the grader runs

```
<entry> --circuit circuit.qasm --device device.arch.json --expanded device.expanded.json --out DIR
```

in the compiler's directory, with no network, a minimal environment (no credentials), and a time
and memory limit per pair. `suite@1` sets them: 120 s and 4096 MB a pair, 600 s for the build,
5400 s for the whole suite, at most 500 000 instructions a programme.

The compiler must write `DIR/program.tsir.json`, the programme in the device's **native** gates
(`R`, `VZ`, `MS`), with `meta.qubit_map`, `{"<qubit>": "<ion>"}`, and exit 0. Exit 4 means "I
cannot compile this pair": a refusal, which costs coverage and nothing else. Any other exit is a
crash; exit 0 without a programme is `no_output`; running out of time is `timeout`. Cooling is the
compiler's job: the heating budget (rule R7) is checked like every other rule, and
the platform's cooling pass (`insert_cooling`) is there to call. It may also write
`DIR/certificate.qcert.json`, a certificate in `qccdc`'s format (lesson I4): **reported, never
required**. Section 3 is what that means.

## 2. Grading a pair

The board's grader (`grade_pair`) runs these checks in order, and the first to fail decides:

1. **program**: the file is a well-formed TSIR within the suite's limits;
2. **native**: every gate is `R`, `VZ` or `MS`. An abstract `CX` would escape the cost of its
   decomposition, and the semantic check reads native pulses;
3. **qubit map**: `meta.qubit_map` names qubits `0 .. n-1`, each with its own ion, every ion
   placed by the programme's `init`;
4. **rules**: the programme is replayed under the suite's physics, and every rule passes, but for
   the same allowed skips as a board's grade: R7b (uncheckable by design), R9 (it checks claims
   an entry need not make) and R10 (decided by the next check instead), and R15, which the replay
   checks only in part;
5. **semantics**: the programme implements the circuit. For a memory experiment, the detectors and
   observables of the stim circuit extracted from the programme are deterministic and have the
   source circuit's parity; for a Clifford circuit, the stabilizer tableau of the emitted pulses, through the qubit
   map, is the circuit's, measurements and resets included; otherwise, the exact unitary, up to a
   global phase, for circuits of at most 10 qubits;
6. **metrics**: `T_jones` (the replayed time, in ms) and the rest, under the suite's physics;
7. **LER**: for memory pairs, the logical error rate under the suite's noise model.

A pair that passes them all is `valid`; one that fails any is `wrong`. Before grading, the harness that
runs the suite has already recorded what the compiler did: `refused`, `timeout`,
`crash`, `no_output`, and `not_run` or `cancelled` when the run's budget ran out or it was stopped.

Two programmes from this part, as the board would see them. The first is the one I1's compiler
wrote for the Bell pair. It plays, and the studio's rules pass; but its gates are `H` and `CX`,
so the board refuses it at check 2. A compiler for the board emits the pulses.
-/

structure Studio where
  arch : String
  src : String

def i1Bell : Studio := ⟨"chain4", "\n".intercalate [
  "p.init({\"q0\": \"C0\", \"q1\": \"C1\"})",
  "p.gate(\"H\", [], [\"C0\"])",
  "p.shuttle(\"q0\", [\"C0\", \"C1\"])",
  "p.cool()",
  "p.gate(\"CX\", [[\"q0\", \"q1\"]])",
  "p.shuttle(\"q0\", [\"C1\", \"C0\"])",
  "p.measure([\"q0\"])",
  "p.measure([\"q1\"])"]⟩

/-!
{{studio i1Bell}}

The second is I1's forgetful router on the pile-up circuit: three ions in `C1`, whose capacity is
two. Its certificate holds (I1, Exercise 9). Even with its gates written as native pulses, the
board would grade it `wrong` at check 4, by rule R1, the rule the studio flags here.
-/

def pileUp : Studio := ⟨"chain4", "\n".intercalate [
  "p.init({\"q0\": \"C0\", \"q1\": \"C1\", \"q2\": \"C2\"})",
  "p.shuttle(\"q0\", [\"C0\", \"C1\"])",
  "p.cool()",
  "p.gate(\"CX\", [[\"q0\", \"q1\"]])",
  "p.shuttle(\"q2\", [\"C2\", \"C1\"])",
  "p.cool()",
  "p.gate(\"CX\", [[\"q2\", \"q1\"]])",
  "p.measure([\"q0\"])",
  "p.measure([\"q1\"])",
  "p.measure([\"q2\"])"]⟩

/-!
{{studio pileUp}}

## 3. No certificate

Nothing in the grade reads a certificate. The harness records, per pair, whether the compiler
wrote one, and no check opens it; the grader says of its semantic check that the programme
implements the circuit *without trusting a certificate*. (The board's plan proposed a "verified"
badge for a certificate the Lean checker accepts; as built, the report
carries only the flag.) What that means:

* **The verdict is computed, not proved.** A `valid` pair rests on the rules replay and on the
  semantic comparison of the pulses with the circuit, both run as code. The proved checker of
  Part F, and `check_sound`, play no part. That is the price of letting any compiler in: a
  compiler that writes no certificate gives the checker nothing to check.
* **Transport is judged by the rules**, as for every programme: capacity, junctions, rails,
  the waveform rule, at the replayed positions (Part E). A certificate would add the proved
  account of where every ion is, and nothing on the board asks for it.
* **The order of commuting gates is not checked.** R10's strict order (lesson I4) is read from a
  certificate's witnesses and the programme's `meta.op` stamps; no check of the board reads
  either, and the semantic checks cannot see two commuting gates swapped. A programme that serves
  a syndrome round's CNOTs in another order can be `valid` here.
* **Which gate fires is checked in full**: the semantic check is the same code R10 uses
  (`check_cert`'s reading of the pulses, the source and the readouts).

So the Compiler board answers "does this programme compute the circuit, legally, and how fast?",
and an Architecture board's R10 answers, in addition, "and does it follow a certificate the proved
checker accepts?". A compiler that writes `qccdc`'s certificate (lesson I4) can have both answers;
the semantic check (`check_cert`) gives the second for any programme and certificate.

## 4. Scoring an entry

Over the whole suite (the harness's `summarize`):

* **coverage**: valid pairs over all pairs;
* **speedup**: the geometric mean, over the pairs that **both** this compiler and the reference
  compiled validly, of `T_jones(reference) / T_jones(yours)`. Above 1 is faster than `qccdc`;
* **LER ratio**: the geometric mean, over memory pairs both compiled, of `LER(yours) / LER(ref)`,
  each LER smoothed as `(errors + 0.5) / (shots + 1)`, so a run with no errors still counts;
* **wrong**: the number of wrong pairs; **compile seconds**, reported and not ranked.

An entry is **eligible** to rank when its build succeeded, no pair is wrong, at least one pair is
valid, at least one pair could be compared with the reference, and the official grader produced
the report. The suite ranks by speedup. The reference itself, `qccdc` plus cooling, is the
denominator: on `suite@1` it has 100 of the 108 pairs valid and refuses 8 (the suite's reference
results, measured 2026-10-19).

In Lean, a report's row is a pair's two statuses and two times; we write times in whole
microseconds, so that every comparison is exact.
-/

/-- What became of one pair (the harness's `STATUSES`). -/
inductive Status where
  | valid | wrong | refused | timeout | crash | noOutput | notRun | cancelled
  deriving DecidableEq, Repr

/-- One pair: your status and time, the reference's, and whether you wrote a certificate. -/
structure Row where
  you : Status
  tYou : Nat
  ref : Status
  tRef : Nat
  certificate : Bool := false
  deriving Repr

/-- Does the pair enter the speedup? Both valid, both times known (the harness skips a zero or
missing time). -/
def Row.compared (r : Row) : Bool :=
  r.you == .valid && r.ref == .valid && r.tYou != 0 && r.tRef != 0

def compared (rs : List Row) : List Row := rs.filter Row.compared

def prod : List Nat → Nat
  | [] => 1
  | x :: xs => x * prod xs

def refProd (rs : List Row) : Nat := prod ((compared rs).map Row.tRef)
def youProd (rs : List Row) : Nat := prod ((compared rs).map Row.tYou)

/-!
The geometric mean of the ratios `tRef / tYou` over `n` pairs is
`(refProd / youProd)^(1/n)`, and it is above 1 exactly when `youProd < refProd`. No root and no
real number is needed. Two entries compare the same way: with `n` and `m` pairs compared,
`(Ra/Ya)^(1/n) > (Rb/Yb)^(1/m)` exactly when `Rb^n * Ya^m < Ra^m * Yb^n`, all in whole numbers.
-/

/-- The speedup is above 1. -/
def faster (rs : List Row) : Bool := youProd rs < refProd rs

/-- Entry `a`'s speedup is above entry `b`'s. -/
def ranksAbove (a b : List Row) : Bool :=
  refProd b ^ (compared a).length * youProd a ^ (compared b).length <
    refProd a ^ (compared b).length * youProd b ^ (compared a).length

structure Entry where
  buildOk : Bool
  official : Bool
  rows : List Row

/-- The eligibility policy: built, no wrong pair, a valid pair, a pair compared, official. -/
def Entry.eligible (e : Entry) : Bool :=
  e.buildOk && e.official && e.rows.all (fun r => r.you != .wrong) &&
    e.rows.any (fun r => r.you == .valid) && !(compared e.rows).isEmpty

/-- Coverage, as (valid pairs, pairs). -/
def coverage (rs : List Row) : Nat × Nat :=
  ((rs.filter (fun r => r.you == .valid)).length, rs.length)

/-!
#### Exercise 1 (★) `refusal_is_not_wrong`
An entry valid on one pair and refusing the other is eligible. (Times: the reference's `bell2` on
`chain`, 0.49 ms, from the suite's reference results; yours, 0.40 ms, is made up.)
-/

theorem refusal_is_not_wrong :
    (Entry.eligible ⟨true, true, [⟨.valid, 400, .valid, 490, false⟩,
                                  ⟨.refused, 0, .valid, 1510, false⟩]⟩) = true := by
  decide

/-!
#### Exercise 2 (★★) `one_wrong_ineligible`
One wrong pair makes an entry ineligible, however good the rest. Prove it for every entry. Show
first that the `all` in `eligible` is `false` (`Bool.eq_false_iff`, and `List.all_eq_true` to use
a hypothetical `true`), then let `simp` finish.
-/

theorem one_wrong_ineligible (e : Entry) (r : Row) (hr : r ∈ e.rows) (hw : r.you = .wrong) :
    e.eligible = false := by
  have h : e.rows.all (fun r => r.you != .wrong) = false := by
    apply Bool.eq_false_iff.2
    intro h
    have := List.all_eq_true.1 h r hr
    simp [hw] at this
  simp [Entry.eligible, h]

/-!
The rows include the hidden pairs, which only the server runs: a programme that is wrong on one
of them makes the entry ineligible as surely as a public one.

#### Exercise 3 (★★) `nothing_compared_ineligible`
An entry with no pair to compare with the reference has no speedup, and is not eligible.
-/

theorem nothing_compared_ineligible (e : Entry) (h : compared e.rows = []) :
    e.eligible = false := by
  simp [Entry.eligible, h]

/-!
#### Exercise 4 (★) `faster_overall`
The reference's times on three pairs of `suite@1` (`bell2` and `ghz8` on `chain`, `ghz16` on
`grid9x9`: 0.49, 1.51 and 9.13 ms), and a made-up entry that is slower on the second. Prove its
speedup is above 1 all the same: the geometric mean weighs ratios, not pairs.
-/

def mine : List Row :=
  [⟨.valid, 400, .valid, 490, false⟩, ⟨.valid, 1600, .valid, 1510, false⟩,
   ⟨.valid, 9000, .valid, 9130, false⟩]

theorem faster_overall : faster mine = true := by
  decide

/-!
Speedup is taken over the pairs both compiled, and a refusal costs only coverage, so the two
numbers pull apart. Entry A is twice as fast on one pair and half as fast on another: speedup
exactly 1. Entry B is A, refusing the slow pair: speedup 2, at half the coverage. The board ranks
by speedup and shows coverage beside it, and that is why it must show both.

#### Exercise 5 (★★) `refuse_to_win`
Prove all of it: A is not faster, its products are equal, B ranks above A, and the coverages.
-/

def entryA : List Row := [⟨.valid, 245, .valid, 490, false⟩, ⟨.valid, 3020, .valid, 1510, false⟩]
def entryB : List Row := [⟨.valid, 245, .valid, 490, false⟩, ⟨.refused, 0, .valid, 1510, false⟩]

theorem refuse_to_win :
    faster entryA = false ∧ youProd entryA = refProd entryA ∧ ranksAbove entryB entryA = true ∧
      coverage entryB = (1, 2) ∧ coverage entryA = (2, 2) := by
  decide

/-!
#### Exercise 6 (★) `ranks_above_irrefl`
No entry ranks above itself.
-/

theorem ranks_above_irrefl (rs : List Row) : ranksAbove rs rs = false := by
  simp [ranksAbove]

/-!
#### Exercise 7 (★★★) `no_slower_one_faster`
Prove, for every report: if on every compared pair you are no slower than the reference, and on
one you are strictly faster, your speedup is above 1. Two lemmas first, by induction (`prod_pos`:
a product of non-zero numbers is positive; `prod_le`: pointwise `≤` gives `≤` of products), then
an induction for the strict case. For the arithmetic, `Nat.mul_le_mul` multiplies two
inequalities, and `Nat.succ_mul` / `Nat.mul_succ` unfold a `+ 1`; `omega` treats a product of two
unknowns as one unknown.
-/

theorem no_slower_one_faster (rs : List Row)
    (hle : ∀ r ∈ compared rs, r.tYou ≤ r.tRef) (hlt : ∃ r ∈ compared rs, r.tYou < r.tRef) :
    faster rs = true := by
  have prod_pos : ∀ (l : List Nat), (∀ x ∈ l, x ≠ 0) → 0 < prod l := by
    intro l
    induction l with
    | nil => intro _; exact Nat.one_pos
    | cons x xs ih =>
      intro h
      have hx : 0 < x := Nat.pos_of_ne_zero (h x (by simp))
      simp only [prod]
      exact Nat.mul_pos hx (ih (fun y hy => h y (by simp [hy])))
  have prod_le : ∀ (l : List Row), (∀ r ∈ l, r.tYou ≤ r.tRef) →
      prod (l.map Row.tYou) ≤ prod (l.map Row.tRef) := by
    intro l
    induction l with
    | nil => intro _; exact Nat.le_refl _
    | cons r rs ih =>
      intro h
      simp only [List.map, prod]
      exact Nat.mul_le_mul (h r (by simp)) (ih (fun x hx => h x (by simp [hx])))
  have hpos : ∀ r ∈ compared rs, r.tYou ≠ 0 ∧ r.tRef ≠ 0 := by
    intro r hr
    simp only [compared, List.mem_filter] at hr
    obtain ⟨_, hc⟩ := hr
    refine ⟨fun h0 => ?_, fun h0 => ?_⟩ <;> simp [Row.compared, h0] at hc
  have key : ∀ (l : List Row), (∀ r ∈ l, r.tYou ≠ 0 ∧ r.tRef ≠ 0) → (∀ r ∈ l, r.tYou ≤ r.tRef) →
      (∃ r ∈ l, r.tYou < r.tRef) → prod (l.map Row.tYou) < prod (l.map Row.tRef) := by
    intro l
    induction l with
    | nil => intro _ _ h; simp at h
    | cons r rs ih =>
      intro hp hl ht
      simp only [List.map, prod]
      have hrest := prod_le rs (fun x hx => hl x (by simp [hx]))
      have hposY := prod_pos (rs.map Row.tYou) (by
        intro x hx; simp only [List.mem_map] at hx
        obtain ⟨y, hy, rfl⟩ := hx; exact (hp y (by simp [hy])).1)
      rcases ht with ⟨x, hx, hlt⟩
      simp only [List.mem_cons] at hx
      rcases hx with rfl | hx
      · -- strictly faster on this pair
        have h1 : (x.tYou + 1) * prod (rs.map Row.tYou) ≤ x.tRef * prod (rs.map Row.tRef) :=
          Nat.mul_le_mul hlt hrest
        rw [Nat.succ_mul] at h1
        omega
      · -- strictly faster on a later pair
        have h2 := ih (fun y hy => hp y (by simp [hy])) (fun y hy => hl y (by simp [hy])) ⟨x, hx, hlt⟩
        have hr := hl r (by simp)
        have hposr : 0 < r.tYou := Nat.pos_of_ne_zero (hp r (by simp)).1
        have h3 : r.tYou * (prod (rs.map Row.tYou) + 1) ≤ r.tRef * prod (rs.map Row.tRef) :=
          Nat.mul_le_mul hr h2
        rw [Nat.mul_succ] at h3
        omega
  exact decide_eq_true (key _ hpos hle hlt)

/-!
The LER ratio smooths each rate to `(errors + 0.5) / (shots + 1)`. Doubled, so that it is a
fraction of whole numbers, that is `(2 errors + 1) / (2 shots + 2)`; one fraction is below another
when cross-multiplying says so.
-/

def smoothed (errors shots : Nat) : Nat × Nat := (2 * errors + 1, 2 * shots + 2)

def below (a b : Nat × Nat) : Bool := a.1 * b.2 < b.1 * a.2

/-!
#### Exercise 8 (★) `zero_errors_count`
The reference's `rep3_r3_z` on `chain` saw 12 errors in 100 000 shots (the suite's reference
results). A made-up
entry sees none in as many. Prove that its smoothed rate is not zero, and is below the
reference's: its LER ratio is below 1, as it should be, rather than undefined.
-/

theorem zero_errors_count :
    (smoothed 0 100000).1 ≠ 0 ∧ below (smoothed 0 100000) (smoothed 12 100000) = true := by
  decide

/-!
Last, the certificate. A row carries whether one was written, and nothing in the score reads it.

#### Exercise 9 (★★) `certificate_ignored`
Prove that setting every row's certificate flag to any value leaves eligibility as it was. First:
`Row.compared` ignores the flag (`rfl`), so `compared` commutes with the update (`List.filter_map`
moves a filter past a map); then `simp` with the `all` and `any` of a map.
-/

theorem certificate_ignored (e : Entry) (b : Bool) :
    (Entry.mk e.buildOk e.official (e.rows.map fun r => { r with certificate := b })).eligible =
      e.eligible := by
  have hrc : ∀ r : Row, Row.compared { r with certificate := b } = Row.compared r := fun _ => rfl
  have hc : compared (e.rows.map fun r => { r with certificate := b }) =
      (compared e.rows).map fun r => { r with certificate := b } := by
    simp only [compared, List.filter_map, Function.comp_def, hrc]
  simp [Entry.eligible, hc, List.all_map, List.any_map, Function.comp_def]

/-!
## 5. Submitting

From a clone of the repository, in its virtual environment, with the workspace installed and
the toolchain fetched (`pip install -e ".[agent]"`, then `qccd toolchain install`; the starting
compiler calls `qccdc`; the workspace's documentation has the details):

```
qccd bench init-compiler my-compiler      # a working compiler to start from: the reference
qccd bench run --compiler my-compiler     # grade it on the public pairs, on your machine
qccd bench publish my-compiler --server https://qccd.academy/official
```

`init-compiler` writes a manifest and a script that is the reference compiler (`qccdc`, then
cooling), so the starting point passes the suite as it is; replace its `compile_pair` with yours.
`bench run` grades every public pair exactly as the server will, prints coverage, speedup, LER
ratio and the reasons the entry could not rank (a local run is never ranked: only the official
grader's report is), and saves the report: in a workspace, where an agent reads it with
`qccd_get_bench`, and otherwise to `bench-report.json`. `--only`
narrows the run to some pairs, circuits or devices; `--jobs` runs compilers in parallel (their
outputs are graded one at a time). `bench suite` lists the suite's circuits and devices.

Running a compiler runs your code, so it happens in your shell, never through an agent's tools.
Publishing needs a person at an interactive terminal, signed in (`qccd login`): it packs the
directory with the suite's identity and digest, shows the compiler's digest, and asks you to type
its first 12 hex digits back. The server builds the compiler and runs it on every pair, public and
hidden, in a separate sandbox container with no network and no secrets, and grades the outputs
with the same code as `bench run`.

## 6. Where this part leaves you

You have built a compiler and proved it correct (I1, I2), checked a compiler in any language with
the proved checker (I3), learned the full contract for a certificate and a programme the platform
judges as it judges its own (I4), and now the board, which judges any programme by what it
computes and the rules it keeps (I5). A compiler that does all of it writes a fast native
programme, a certificate the proved checker accepts, and a report the board can rank.
-/
