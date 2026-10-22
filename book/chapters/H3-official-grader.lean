import QCCDC.Cert.Sound

/-!
# The official grader

*Part H, lesson H3. You need lessons H1 and H2.*

The leaderboards of lesson H2 were built by the study's own scripts. The **official** leaderboard
is different: anyone may submit a design, and a grader the submitter does not control decides
whether it is eligible. This lesson reads that grader stage by stage, models its verdict in Lean,
and proves what an eligible entry must have passed.
-/

open QCCDC.Cert

/-!
## 1. Where it runs, and what it reads

The official service is at `https://qccd.academy/official/`. A workspace grades a design locally
first (`qccd submit --local`), then `qccd publish` uploads the frozen bundle. The service stores
it, and its grader runs `grade` on it -- the official grader, the same function a local grade runs
-- always under the `reference` profile. Nothing the client says about its own grade is
accepted.

A **bundle** is a directory of canonical JSON files:

| file | what it is |
|---|---|
| `device.arch.json` | the device, as the design document |
| `certified.tsir.json` | the programme the compiler emitted, which the certificate speaks about |
| `program.tsir.json` | the programme that is graded: the certified one, with at most cooling inserted |
| `certificate.qcert.json` | the compiler's certificate |
| `presentation.json` | optional: title and description |
| `manifest.json` | the task (id and digest) and every file's SHA-256 |

The first four are required. The bundle's digest is the manifest's, so one digest names the
whole submission.

## 2. The stages

`grade` runs these stages in this order (`ler` only on a memory board), and each ends with one
status: `passed`, `failed`, `partial`, `skipped`, `unsupported`, `timeout`, `cancelled` or
`internal_error`.

| stage | what it establishes | what refuses |
|---|---|---|
| `bundle` | every file hashes to its manifest digest; no stray or unknown file; the manifest names **this** task release, id and digest | any mismatch; grading stops here |
| `device` | the device loads under the schema and structure checks, within the release's node limit | a device that does not load; grading stops |
| `physics_lock` | the device's `primitives`, `heating`, `species` and `budget` equal the release's physics | changed physics: an exploratory design, graded but never eligible |
| `program` | the final programme is well-formed TSIR within the release's instruction limit | grading stops |
| `rules` | the final programme, replayed on the device under the task's model, passes every rule the replay checks | a failed rule; a skip other than R7b, R9, R10 or a partial other than R15 makes it `partial` |
| `correspondence` | the final programme is the certified one with only the allowed insertions (`cool`, fresh ids), every certified instruction kept in order and unchanged | anything else |
| `certificate_binding` | the certificate's `circuit_ops` are the **task's** circuit op by op (`check_cert.check_binding`), with its qubit count and a one-to-one map; nothing unrealised; every witness names a certified instruction with the same op stamp | any disagreement |
| `lean_certificate` | `qcheck` accepts the certificate, against hops, gate sites and loops re-derived from the **submitted** device (`export_arch`, `mk_qcheck_input`) | `REJECTED`; `skipped` when the binding did not pass; `unsupported` with no `qcheck` installed |
| `semantics` | the semantic check (`check_cert`, run without the proved checker): O1, no unrealised op, the order of each qubit's ops in the programme, and O2 against the task's circuit | a problem in any of them; `partial` when O2 is not established |
| `ler` | (memory boards) the programme runs the memory experiment; its logical error rate | a programme that does not run it |
| `metrics` | the leaderboard's numbers | |

Two details matter for R10. The Lean stage is not run on a certificate that is not bound to the
task: checking it would prove something about some other circuit. And the semantics stage reads
four fields of the semantic check's verdict -- `o1_transport` (which includes the witnesses
checked against the programme's instructions and the programme's transport against the
certificate's moves), `unrealised_ops`, `o2_semantics`, and `order`. A verdict without `order`
comes from a semantic check older than the strict order rule, and counts as not checked.

## 3. The verdict, in Lean
-/

/-- How a stage ends (the official grader's `STAGE_STATUSES`). `partial` is a word Lean reserves,
so that constructor is written `«partial»`. -/
inductive Status where
  | passed | failed | «partial» | skipped | unsupported | timeout | cancelled | internalError
  deriving DecidableEq, Repr

inductive Stage where
  | bundle | device | physicsLock | program | rules | correspondence
  | certificateBinding | leanCertificate | semantics | ler | metrics
  deriving DecidableEq, Repr

/-- The order `grade` runs the stages in. -/
def stageOrder (memory : Bool) : List Stage :=
  [.bundle, .device, .physicsLock, .program, .rules, .correspondence,
   .certificateBinding, .leanCertificate, .semantics] ++ (if memory then [.ler] else []) ++ [.metrics]

/-- The checks every task release on the site requires (`required_checks` in each
`release.json`); the two memory boards add `ler`. -/
def releaseRequired : List Stage :=
  [.bundle, .device, .physicsLock, .program, .rules, .certificateBinding, .leanCertificate,
   .semantics, .correspondence, .metrics]

/-- What eligibility reads from a report. -/
structure Report where
  profile : String
  stages : List (Stage × Status)
  required : List Stage

/-- A stage's status; a stage not in the report counts as `skipped`, which is what `grade` leaves
a stage it never reached. -/
def Report.status (r : Report) (s : Stage) : Status :=
  ((r.stages.find? (fun p => p.1 = s)).map Prod.snd).getD .skipped

/-- The official grader's rule (`_report`): eligible when the profile is `reference` and every required check
`passed`. Anything else -- `skipped`, `unsupported`, `partial`, `timeout`, `cancelled`,
`internal_error` -- is not a pass. -/
def Report.eligible (r : Report) : Bool :=
  r.profile == "reference" && r.required.all (fun s => r.status s == .passed)

/-!
Here is a real grade: the GHZ starter board ("GHZ state on four qubits (starter)", the
four-qubit GHZ circuit), compiled onto a new workspace's starter design, the four-trap chain, and
graded under the reference profile with `qccd submit --local --board GHZ`, in a workspace made
from a fresh clone of the repository, on 2026-10-20 (lesson H4 does it step by step). Every stage
passed.

That board's circuit is byte for byte the Compilation page's `algo_ghz` (both files have the
SHA-256 `a6c1803b...`), and the bundle's certificate is the same certificate as the one you proved
in lesson H1, field for field. So this is the certificate the Lean stage checked:
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

/-!
{{figure ghz}}

And its report's stages, as Lean data:
-/

def ghzReport : Report :=
  { profile := "reference", required := releaseRequired,
    stages := [(.bundle, .passed), (.device, .passed), (.physicsLock, .passed),
      (.program, .passed), (.rules, .passed), (.correspondence, .passed),
      (.certificateBinding, .passed), (.leanCertificate, .passed), (.semantics, .passed),
      (.metrics, .passed)] }

/-!
#### Exercise 1 (★) `ghz_stage_order`
The report lists its stages in the order `grade` ran them. Prove it.
-/

theorem ghz_stage_order : ghzReport.stages.map Prod.fst = stageOrder false := by
  decide

/-!
#### Exercise 2 (★) `ghz_eligible`
Prove the entry eligible. (`==` on strings: use `decide +kernel`.)
-/

theorem ghz_eligible : ghzReport.eligible = true := by
  decide +kernel

/-!
## 4. A tampered bundle

Now change one thing. Take that bundle, swap the cycles of its last two witnesses -- `cx q[1],q[2]`
claims cycle 21 and `cx q[2],q[3]` cycle 14 -- and seal it again with fresh digests, so that the
`bundle` stage passes and something later has to catch it. Graded the same way, the report said
`lean_certificate: failed` and `semantics: failed`, every other stage passed, not eligible. The
Lean stage gave the checker's two reasons:

```
CERT.LEAN_REJECTED  a gate witness's ions are not together at a gate-capable trap
CERT.LEAN_REJECTED  two ops sharing a qubit are out of order: op 2 (cx [1, 2]) comes
                    before op 3 (cx [2, 3]) in the circuit, but its witness is at cycle 21,
                    not before 14
```

and the semantics stage reported O1 (`op 3: operands not co-located at t=14`). Its `order` was
`ok`: the semantic check's order check reads the **programme**, which was not touched; only the
certificate lies.
-/

def ghzSwapped : Input :=
  { ghz with gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C2", ["q0", "q1"]⟩,
                       ⟨2, 21, "C1", ["q1", "q2"]⟩, ⟨3, 14, "C0", ["q2", "q3"]⟩] }

/-!
{{figure ghzSwapped at=14}}
-/

#eval diagnose ghzSwapped

def swappedReport : Report :=
  { ghzReport with
    stages := [(.bundle, .passed), (.device, .passed), (.physicsLock, .passed),
      (.program, .passed), (.rules, .passed), (.correspondence, .passed),
      (.certificateBinding, .passed), (.leanCertificate, .failed), (.semantics, .failed),
      (.metrics, .passed)] }

/-!
#### Exercise 3 (★★) `swapped_rejected`
Prove the checker refuses the tampered certificate for order, with `rejects_reordering`: the
witness join of op 2 is `⟨2, 21, [1, 2]⟩` and of op 3 is `⟨3, 14, [2, 3]⟩`.
-/

theorem swapped_rejected : check ghzSwapped = false :=
  rejects_reordering (a := ⟨2, 21, [1, 2]⟩) (b := ⟨3, 14, [2, 3]⟩)
    (by decide +kernel) (by decide +kernel) (by decide) (by decide) (by decide)

/-!
#### Exercise 4 (★) `swapped_not_eligible`
Prove the tampered entry is not eligible.
-/

theorem swapped_not_eligible : swappedReport.eligible = false := by
  decide +kernel

/-!
## 5. What eligibility requires, for every report

The examples are two reports. The rule is about all of them.

#### Exercise 5 (★★) `eligible_needs_required`
Prove that an eligible report has every required stage passed. `simp [Report.eligible] at h`
turns `h` into a conjunction whose second half is a `∀` over the required list.
-/

theorem eligible_needs_required {r : Report} (h : r.eligible = true) {s : Stage}
    (hs : s ∈ r.required) : r.status s = .passed := by
  simp [Report.eligible] at h
  exact h.2 s hs

/-!
#### Exercise 6 (★★) `no_entry_without_lean_and_semantics`
Under the checks every release on the site requires, no entry is eligible unless the Lean stage
**and** the semantics stage passed -- the two halves of R10. Use Exercise 5 twice.
-/

theorem no_entry_without_lean_and_semantics {r : Report} (hreq : r.required = releaseRequired)
    (h : r.eligible = true) :
    r.status .leanCertificate = .passed ∧ r.status .semantics = .passed :=
  ⟨eligible_needs_required h (by rw [hreq]; decide),
   eligible_needs_required h (by rw [hreq]; decide)⟩

/-!
#### Exercise 7 (★★) `draft_never_eligible`
A `draft` grade skips the certificate stages, and is never eligible whatever its stages say.
Prove that no report under another profile is eligible.
-/

theorem draft_never_eligible {r : Report} (h : r.profile ≠ "reference") : r.eligible = false := by
  simp [Report.eligible, h]

/-!
## 6. The certificate chain, as the code runs it

The official grader runs the last three certificate stages like this: under any profile
but `reference` all three are `skipped`. Otherwise, `ready` says the bundle has a certificate
and a certified programme (the `bundle` stage already refuses one that lacks either file, and
`grade` checks again here). Without them the binding `failed`, and the other two are `skipped`.
With them the binding is the binding check's result; the Lean stage is `skipped` when the
binding did not pass and `qcheck`'s result otherwise; the semantics stage is the semantic
check's result. Below, `binding`, `lean` and `sem` are what each check would say if it ran.
-/

def certChain (profile : String) (ready : Bool) (binding lean sem : Status) :
    Status × Status × Status :=
  if profile = "reference" then
    let b := if ready then binding else .failed
    let l := if !ready then .skipped else if b = .passed then lean else .skipped
    let s := if ready then sem else .skipped
    (b, l, s)
  else (.skipped, .skipped, .skipped)

/-!
#### Exercise 8 (★★★) `lean_stage_needs_binding`
Prove that the Lean stage can pass only under the reference profile, for a bundle that has a
certificate, whose binding passed, and which `qcheck` accepted. Unfold `certChain`, then split
on the profile test and on `ready` (`by_cases`, `cases`); `simp` finishes each case.
-/

theorem lean_stage_needs_binding {p : String} {ready : Bool} {b l s : Status}
    (h : (certChain p ready b l s).2.1 = .passed) :
    p = "reference" ∧ ready = true ∧ b = .passed ∧ l = .passed := by
  unfold certChain at h
  by_cases hp : p = "reference"
  · cases ready
    · simp [hp] at h
    · by_cases hb : b = .passed
      · simp [hp, hb] at h
        exact ⟨hp, rfl, hb, h⟩
      · simp [hp, hb] at h
  · simp [hp] at h

/-!
## 7. The semantics stage's own rule

The semantics stage turns the semantic check's verdict into a status like this:
-/

/-- What the semantics stage reads from the semantic check's verdict. -/
structure SemVerdict where
  o1Ok : Bool          -- `o1_transport` is `"ok"`
  allRealised : Bool   -- `unrealised_ops` is 0
  mismatch : Bool      -- `o2_semantics` contains `MISMATCH`
  orderOk : Bool       -- `order` is `"ok"` (absent: `"not checked"`)
  o2Ok : Bool          -- `o2_semantics` starts with `ok`

def semStage (v : SemVerdict) : Status :=
  if !v.o1Ok || !v.allRealised || v.mismatch || !v.orderOk then .failed
  else if v.o2Ok then .passed else .«partial»

/-!
#### Exercise 9 (★★) `semantics_needs_order`
Prove that the semantics stage passes only when O1, O2 and the order check all passed. Five
Booleans make 32 cases: `cases v`, `revert h`, then `cases` on each field and `decide` closes
every one.
-/

theorem semantics_needs_order {v : SemVerdict} (h : semStage v = .passed) :
    v.o1Ok = true ∧ v.orderOk = true ∧ v.o2Ok = true := by
  cases v with
  | mk o1 ar mm ord o2 =>
    revert h
    cases o1 <;> cases ar <;> cases mm <;> cases ord <;> cases o2 <;> decide

/-!
## 8. A real report, and how to read one

`GET https://qccd.academy/official/v1/submissions/<id>/report` returns the latest report of a
submission: a public submission's to anyone, a private one's to the
account that submitted it, and `409 not_ready` before it is graded. It is the object `grade`
returns, stored as it was. The GHZ report above, shortened:

```
{ "kind": "qccd.evaluation_report", "version": 1,
  "task": {"id": "ghz4@1", "digest": "sha256:be83e5f5..."},
  "bundle": {"digest": "sha256:...", "files": {"certificate": "sha256:...", ...}},
  "evaluator": {"name": "qccd-reference-evaluator", "version": "1.0.0", "source_digest": "sha256:...",
                "toolchain": {"qccdc": "sha256:ba265b7f...", "qcheck": "sha256:...", ...}},
  "profile": "reference",
  "stages": [
    {"id": "bundle", "required": true, "status": "passed",
     "coverage": "manifest schema, member set, sha256 of every file, task id and digest", ...},
    ...
    {"id": "lean_certificate", "required": true, "status": "passed",
     "coverage": "QCCDC.Cert.check (proved sound: check_sound) accepted the certificate against
                  hop/gate-site facts re-derived from the submitted device", ...},
    {"id": "semantics", "required": true, "status": "passed",
     "detail": {"o1_transport": "ok", "o2_semantics": "ok", "order": "ok", "unrealised_ops": 0,
                "method": "stabilizer tableau, composed from the emitted pulses, against the
                           source QASM", "circuit_sha256": "a6c1803b..."}, ...},
    {"id": "metrics", ...}],
  "eligibility": {"eligible": true, "policy": "every required check passed under the reference
                  profile", "required": ["bundle", ..., "metrics"], "reasons": []},
  "metrics": {"T_jones": {"value": 2.13, "unit": "ms", "better": "low", "ranked": true}, ...},
  "rank_by": "T_jones", "diagnostics": [], ... }
```

`toolchain.qccdc` there is the SHA-256 of the compiler binary, here the published Windows one;
`toolchain.qcheck` the checker's. Every diagnostic has a stable id (`CERT.LEAN_REJECTED`,
`SEM.O1`, `RULE.R6b`, ...) and names its stage.

When this lesson was written, the official leaderboards had no entries
(`GET https://qccd.academy/official/v1/leaderboard/<task>` returned no rows for any task), so the
report above is from a local grade with the same evaluator. `evaluator.comparable` says what two
gradings of one bundle must agree on: everything but the timing, the run's ids and the
evaluator's digests.

## 9. What a report does not say

A report carries the bundle's digests, not its files: to re-check an entry's certificate you need
the bundle, which only its submitter has. It says nothing about physical realisability beyond the
task's model -- the rules and the physics tables are the model. And `eligible` means exactly the
rule of Exercise 5: every required stage passed, under the reference profile, no more.
-/
