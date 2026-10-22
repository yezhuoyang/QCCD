import QCCDC.Cert.Sound

/-!
# Every verdict, reproduced

*Part H, lesson H4. You need lessons H1 and H3.*

Lesson H1 checked the O1 half of R10 on files the site publishes. This lesson reproduces a whole
verdict the way you would on your own computer: install the tools, make a workspace, compile the
GHZ circuit onto a design, grade it under the reference profile -- the official grader, run
locally -- and read what the report says about R10. Then you compare what you made with what the
Compilation page published, and state in Lean what a verdict of `passed` requires.

Two kinds of words appear in the commands below. **Yours to name, anything you like:** the
workspace folder (`"My QCCD designs"` below is only an example) and the designs in it. **Type
as shown:** everything else -- the commands, and a board by its title or any unambiguous part of
it (`GHZ` below).
-/

open QCCDC.Cert

/-!
## 1. Once: the repository, the compiler, the checker

You need Lean's installer, elan, for the checker (lesson H1, step 1, says how to install it).
Then, from a terminal, on macOS or Linux:

```
git clone -c core.autocrlf=false -b compiler https://github.com/yezhuoyang/QCCD
cd QCCD
python3.13 -m venv .venv && source .venv/bin/activate     # any Python 3.10 or newer
pip install -e ".[agent]"
qccd toolchain install
qccd toolchain build-checker
```

On Windows, in PowerShell:

```
git clone -c core.autocrlf=false -b compiler https://github.com/yezhuoyang/QCCD
cd QCCD
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[agent]"
qccd toolchain install
qccd toolchain build-checker
```

`-c core.autocrlf=false` keeps every file's line endings as published: a certificate records a hash
of its circuit file's bytes, and Git on Windows would otherwise rewrite the circuit's line endings
on checkout, so section 4's comparison would differ in that one field.

`pip install -e ".[agent]"` installs the workspace, with what its grader needs (qiskit, stim and
numpy, which the semantic check uses). `qccd toolchain install` downloads the compiler `qccdc` and
the checker `qcheck` that the repository pins -- for each, a version, and for each platform a URL,
a size and a SHA-256 -- refuses either unless the size and the hash match, installs them in your
home folder, and runs each once on a test (a circuit; a certificate it must accept and one it must
reject). Windows and Linux on x86-64 have prebuilt binaries of both. When this lesson was written
the pinned versions had none for macOS; on a Mac the command says so and says how to build the
compiler from the repository's OCaml sources.

**The checker, from source.** `qccd toolchain build-checker` builds the checker from the clone's
own Lean sources, runs it once on a certificate it must accept and one it must reject, and from
then on the workspace uses it:

```
built the checker (checked: it accepted and rejected a test certificate): ...
the workspace uses it from now on; `qccd toolchain check <file>.qcheck.json` runs it
```

On a Mac it is the way to get a checker at all; elsewhere it makes the checker the one of the
sources in front of you (lesson H1). Then

```
qccd toolchain status
```

lists the compiler `in use` (installed) and, on its `checker` line, the checker you built
(built in this checkout). Before you build it, that line reads "none: reference grades report the
Lean stage `unsupported` (`qccd toolchain install`, or `qccd toolchain build-checker`)" -- and a
grade with the Lean stage `unsupported` is never eligible.

When this lesson was written, the Windows lines of this section and all of the next, and the
checks of sections 4 and 6, were run as written, in PowerShell, from a fresh clone, a fresh
environment and an empty home folder for the toolchain; when the pinned compiler moved to
`e799ecf5a5cd` the same day, the workspace steps were run again, and the numbers quoted below are
that run's. The macOS and Linux lines differ only in how the environment is made and activated;
they were not run on macOS or Linux.

## 2. A workspace, a compile, a grade

Leave the clone, and make a workspace in a folder of your own:

```
cd ..
qccd init "My QCCD designs"
cd "My QCCD designs"
```

`qccd init` starts the workspace with one design, the four-trap chain, and says which words are
yours (two of its lines, wrapped here):

```
  yours to name, anything you like:  this folder (rename or move it while `qccd stop`ped), every
                                     design (in the Studio's design menu, or ask the agent), and
                                     the name shown if you publish
  type as shown:                     the commands; a board by its title, or any part of it that
                                     is unambiguous ("BB", "surface code"; `qccd boards` lists them)
```

`qccd boards` lists the boards; the last is "GHZ state on four qubits (starter)". Its circuit is
byte for byte the Compilation page's GHZ chain (lesson H3). Compile it onto your design, adopt the
programme, and grade it:

```
qccd compile --board GHZ --adopt
qccd submit --local --wait --json
```

The first prints

```
succeeded: compiled GHZ state on four qubits (starter) at r0: 28 instructions (cooled); certificate stored
adopted as the final program at r1
```

-- the compiler ran, the cooling pass added cooling where a gate would start on ions too hot to
gate (R7), and the rules were checked on the cooled programme. The second freezes the design and
its programme as a bundle and grades it under the reference profile: binding the certificate to
the task's circuit, running the proved Lean checker, checking the programme implements the
circuit. It prints

```
submission sub_... of r1 (Local result - not published); bundle sha256:...
eligible  bundle=passed  device=passed  physics_lock=passed  program=passed  rules=passed
          correspondence=passed  certificate_binding=passed  lean_certificate=passed
          semantics=passed  metrics=passed
  T_jones                  2.13 ms
```

(one line, here broken; then the other metrics), and with `--json` the whole report. Nothing has
been published: `qccd publish` is a separate, deliberate step.

## 3. What the report says about R10

The two stages that make up R10, from that report:

```
{"id": "lean_certificate", "status": "passed",
 "coverage": "QCCDC.Cert.check (proved sound: check_sound) accepted the certificate against
              hop/gate-site facts re-derived from the submitted device", ...}
{"id": "semantics", "status": "passed",
 "coverage": "O1 transport replay of the certificate; O2 stabilizer tableau, composed from the
              emitted pulses, against the source QASM",
 "detail": {"o1_transport": "ok", "unrealised_ops": 0, "order": "ok", "o2_semantics": "ok",
            "method": "stabilizer tableau, composed from the emitted pulses, against the source QASM",
            "circuit_sha256": "a6c1803b8301e79ceba044278137397890d40a37eb2eaaa6057b8e96a49bdd00"}}
```

and `certificate_binding` passed before them, which is what lets the Lean stage run at all
(lesson H3).

## 4. Compare with what the page published

Three things tie your grade to the Compilation page's GHZ chain.

* **The certificate.** Your report's `bundle.files.certificate` is the SHA-256 of the bundle's
  certificate, written canonically (sorted keys, one spelling of every number). It is
  `sha256:7b1db8a631b93bd4bc22daca3babc1afd918ffd71803d191a215855b9d1668c6` -- and that is also
  the SHA-256 of the published [algo_ghz.qcert.json](data/algo_ghz.qcert.json) written the same
  way: the same certificate, field for field.
* **The checker's input.** The Lean stage built its input from your bundle's device and
  certificate; rebuilt the same way, it equals the published
  [algo_ghz.qcheck.json](data/algo_ghz.qcheck.json). So `qccd toolchain check algo_ghz.qcheck.json`
  (lesson H1, step 4) checks exactly what your grade's Lean stage checked, and lesson H1's proofs
  are about it.
* **The circuit.** `circuit_sha256` in the semantics stage is the SHA-256 of the board's circuit
  file, the same bytes the Compilation page compiled.

Your bundle's own digest, `bundle sha256:...`, will differ from anyone else's: the bundle records
where it came from (your workspace's ids), and that is part of what it hashes.

When this lesson was written we also compiled all 19 of the Compilation page's examples with the
pinned compiler (version `e799ecf5a5cd`, and before it `f88754a2fa71`): each one's programme and
certificate came out byte for byte the page's files, and its R10 verdict the same.

## 5. The verdict, in Lean

The Compilation page's verdicts, and the computed marks of lesson H2's boards, come from the
semantic check run *together with* the proved checker, which ends in one field, `R10`, decided by
one chain of tests. (Before the chain, the semantic check reads the certificate's shape: every
field it uses, with its type. A malformed certificate, a move whose cycle is written `20.0` say,
fails R10 there, by name, and never reaches the chain. The model below starts after that step.)
Here is that chain, with its inputs as Lean data. A list of problems is empty where the verdict
says `"ok"`; `qcheck` is `some true` when the proved checker accepted, `some false` when it
rejected, and `none` when it was not run; `same` is O2: `some true` for `ok`, `some false` for a
mismatch, `none` when it could not be established.
-/

/-- R10's three verdicts. `partial` is a word Lean reserves, so that one is written `«partial»`. -/
inductive R10 where
  | passed | «partial» | failed
  deriving DecidableEq, Repr

/-- What the semantic check decides R10 from. -/
structure Verdict where
  unbound : Bool          -- the checker input was not made from this certificate on this device
  qcheck : Option Bool    -- the proved checker's verdict
  binding : List String   -- the certificate against the circuit
  o1 : List String        -- co-location, the witnesses against the programme, its transport
  unrealised : Nat        -- `unrealised_ops`
  order : List String     -- each qubit's ops in the circuit's order, in the programme
  same : Option Bool      -- O2
  deriving Repr

/-- The semantic check's last step, test for test. -/
def r10 (v : Verdict) : R10 :=
  if v.unbound then .failed
  else if v.qcheck = some false then .failed
  else if v.binding ≠ [] then .failed
  else if v.o1 ≠ [] ∨ v.unrealised ≠ 0 then .failed
  else if v.order ≠ [] then .failed
  else if v.same = some false then .failed
  else if v.same = some true ∧ v.qcheck = some true then .passed
  else .«partial»

/-!
Your grade, read into it: `certificate_binding` passed, so no binding problem; `lean_certificate`
passed, so `qcheck = some true`; the semantics stage's `o1_transport` and `order` are `ok`,
`unrealised_ops` is 0, `o2_semantics` is `ok`. And `unbound` is `false`: the grader builds the
checker's input itself, from your bundle, so it cannot be handed another.
-/

def ghzVerdict : Verdict :=
  { unbound := false, qcheck := some true, binding := [], o1 := [], unrealised := 0, order := [],
    same := some true }

/-!
#### Exercise 1 (★) `ghz_verdict_passed`
Prove that your verdict, read through the rule, is `passed`.
-/

theorem ghz_verdict_passed : r10 ghzVerdict = .passed := by
  decide

/-!
#### Exercise 2 (★★) `refused_input_fails`
Given a checker input it did not build, the semantic check builds its own from the certificate
and the device and refuses one that differs: the proved checker is not run on it, and R10 fails,
whatever else holds. Prove it for every verdict.
-/

theorem refused_input_fails {v : Verdict} (h : v.unbound = true) : r10 v = .failed := by
  simp [r10, h]

/-!
#### Exercise 3 (★★) `no_qcheck_no_pass`
Without the proved checker's verdict, R10 is at best `partial` -- which is why the official
grader runs `qcheck` as a stage of its own, beside the semantic check. Prove that a verdict with
`qcheck = none` is never `passed`. `unfold r10 at hp`, then `repeat' split at hp` takes the chain
apart; `simp_all` closes each piece.
-/

theorem no_qcheck_no_pass {v : Verdict} (h : v.qcheck = none) : r10 v ≠ .passed := by
  intro hp
  unfold r10 at hp
  repeat' split at hp
  all_goals simp_all

/-!
#### Exercise 4 (★★) `order_fails`
A reorder fails R10 whatever O2 says -- even when the two ops commute and the tableaux agree.
Prove it.
-/

theorem order_fails {v : Verdict} (h : v.order ≠ []) : r10 v = .failed := by
  unfold r10
  repeat' split
  all_goals simp_all

/-!
#### Exercise 5 (★★★) `passed_iff`
State exactly what `passed` requires of the fields, and prove it: no refused input, `qcheck`
accepted, no binding problem, no O1 problem, nothing unrealised, no order problem, and O2
established. `unfold r10`, `repeat' split`, then on each goal `simp_all`; a few goals are left as
implications, which `intros` followed by `simp_all` closes.
-/

theorem passed_iff (v : Verdict) :
    r10 v = .passed ↔ v.unbound = false ∧ v.qcheck = some true ∧ v.binding = [] ∧ v.o1 = [] ∧
      v.unrealised = 0 ∧ v.order = [] ∧ v.same = some true := by
  unfold r10
  repeat' split
  all_goals (simp_all; try (intros; simp_all))

/-!
## 6. A doctored input

The checker's input is a file, and you can change it. Take your copy of
[algo_ghz.qcheck.json](data/algo_ghz.qcheck.json) and change the first move's `"t": 3` to
`"t": 9`, so that `q0` leaves `C3` after the CNOT that needs it in `C2` at cycle 8. In Lean:
-/

def ghz : Input :=
  { circuit := "circuit", arch := "chain4", nQubits := 4,
    qmap := [(0, "q0"), (1, "q1"), (2, "q2"), (3, "q3")],
    init := [("q0", "C3"), ("q1", "C2"), ("q2", "C1"), ("q3", "C0")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "cx", [1, 2]⟩, ⟨3, "cx", [2, 3]⟩, ⟨4, "measure", [0]⟩, ⟨5, "measure", [1]⟩, ⟨6, "measure", [2]⟩, ⟨7, "measure", [3]⟩],
    moves := [⟨3, "q0", "C3", "C2"⟩, ⟨9, "q1", "C2", "C1"⟩, ⟨16, "q2", "C1", "C0"⟩],
    rots := [],
    gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C2", ["q0", "q1"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩, ⟨3, 21, "C0", ["q2", "q3"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

def ghzLate : Input :=
  { ghz with moves := [⟨9, "q0", "C3", "C2"⟩, ⟨9, "q1", "C2", "C1"⟩, ⟨16, "q2", "C1", "C0"⟩] }

/-!
{{figure ghzLate at=8}}

Save the edited copy under a name of your own -- `algo_ghz_late.qcheck.json`, say -- and check it
with `qccd toolchain check algo_ghz_late.qcheck.json`. It says

```
qcheck circuit on chain4
  8 circuit ops, 4 gate witnesses, 3 moves, 0 rotations, 4 qubits
  REJECTED
    - a gate witness's ions are not together at a gate-capable trap
```

with exit status 1. Nothing you can submit reaches the grader this way: it never reads a checker
input you supply. It builds its own from your bundle's device and certificate -- Exercise 2's
case, ruled out by construction. To lie to the grader you must change the certificate itself, as
the tampered bundle of lesson H3 did, and the checker refuses that.

#### Exercise 6 (★★) `ghz_late_rejected`
Prove the doctored input refused, with `rejects_not_colocated`, on the witness of the first CNOT
and the ion that is not there -- the way lesson F1 refused its late move: membership by `simp`,
the position by computation.
-/

theorem ghz_late_rejected : check ghzLate = false :=
  rejects_not_colocated (g := ⟨1, 8, "C2", ["q0", "q1"]⟩) (i := "q0")
    (by simp [ghzLate, ghz]) (by simp) (by decide +kernel)

/-!
## 7. What `qccd toolchain install` asks of a checker

When it installs a prebuilt `qcheck` (Windows and Linux), `qccd toolchain install` runs it twice before
calling it installed (and `qccd toolchain build-checker` does the same with the one it builds): on
an input it must accept and one it must reject. The first is a certificate of nothing: no qubits,
no ops, no moves, no witnesses, one gate site `S0`, every field written out (the checker's strict
reader refuses a missing one). The second has two qubits on ions `a` and `b`, both starting in
`S0`, and one op, `cx` on both -- and no witness. In Lean:
-/

def smokeOk : Input :=
  { circuit := "smoke", arch := "none", nQubits := 0, qmap := [], init := [], ops := [],
    moves := [], rots := [], gates := [], unrealised := [], gateSites := ["S0"], hops := [],
    loops := [] }

def smokeBad : Input :=
  { smokeOk with nQubits := 2, qmap := [(0, "a"), (1, "b")], init := [("a", "S0"), ("b", "S0")],
                 ops := [⟨0, "cx", [0, 1]⟩] }

/-!
#### Exercise 7 (★) `smoke_ok_accepted`
Prove the checker accepts the empty certificate: a circuit of no ops on no qubits holds.
-/

theorem smoke_ok_accepted : check smokeOk = true := by
  decide +kernel

/-!
#### Exercise 8 (★★) `smoke_bad_rejected`
Prove the checker rejects the second, with `rejects_dropped_gate`: the `cx` is one of the ops
(`simp`), it needs a witness, and it has none (both by computation).
-/

theorem smoke_bad_rejected : check smokeBad = false :=
  rejects_dropped_gate (o := ⟨0, "cx", [0, 1]⟩) (by simp [smokeBad]) (by decide +kernel)
    (by decide +kernel)

/-!
## 8. The whole part, in one table

| what the site shows | what you can check | how |
|---|---|---|
| the Compilation page's R10, 19 examples | O1 in the page; the GHZ chain's whole R10 in a workspace of your own | lesson H1's panel; sections 1-4 here |
| a board's R10 mark | the one board certificate in lesson H2; your own design on the same board, graded locally | lesson H2; sections 1-4 here |
| an official entry's eligibility | the grader's rule, and your own bundle's report; another person's certificate only with their bundle | lesson H3 |

Every check above runs the same proved `check`, and every R10 the same semantic check. What that
leaves you trusting is what Part F lists: Lean's kernel for what you prove in the page, and Lean's
compiler too for `qcheck`; and the computed half -- the semantic check (`check_cert`), the device
facts' builder (`mk_qcheck_input`, with `export_arch`) that makes the checker's input, and the
qiskit and stim they use -- which is computed, not proved.
-/
