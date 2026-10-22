import QCCDC.Cert.Sound

/-!
# The Compilation page

*Part H, lesson H1. You need Part F (certificates and the proved checker) and Part G (which
gate the pulses make).*

Part H turns the tutorial on the site itself. Every page of it that says "R10 holds" -- the
compiled programme implements its circuit -- is making a claim, and the point of a proved
checker is that you need not take the claim on trust. This lesson does it for the Compilation
page; H2 for the leaderboards; H3 for the official grader; H4 reproduces a verdict from
nothing but the repository.

The rule of the part: a claim is checked **in this page** when its certificate is small enough
for Lean's kernel to compute, and **with the command-line checker** `qcheck` otherwise. Both
run the same `check`, proved sound by `check_sound` (lesson F1).
-/

open QCCDC.Cert

/-!
## 1. What the page publishes

The Compilation page compiles 19 circuits and shows what became of each: the Bell pair it
walks through stage by stage, fourteen basic gates (one each), and four short algorithms.
They are spread over the four small devices of this tutorial, so that transport is seen on a
line (`chain4`), a loop (`race8`), a lattice (`grid2x3`) and a loop with docks (`ring6d`).

For every one of them, two files are published beside this lesson, under `data/`:

* `<id>.qcert.json`: the certificate the compiler wrote;
* `<id>.qcheck.json`: what the checker reads. It is the certificate's fields together with
  three facts read out of the **device's own document**, never from the compiler: the traps
  that can gate (`gate_sites`), the pairs of traps one cycle apart (`hops`), and each closed
  loop's order (`loops`). The device facts' builder, `mk_qcheck_input`, makes it.

Here are the 19, with what is in each checker input:

| example | device | ops | witnesses | moves | rotations |
|---|---|---|---|---|---|
| `bell` | `chain4` | 4 | 2 | 1 | 0 |
| `h`, `s`, `rz` | `chain4` | 1 | 1 | 0 | 0 |
| `x`, `t` | `race8` | 1 | 1 | 0 | 0 |
| `y`, `rx` | `grid2x3` | 1 | 1 | 0 | 0 |
| `z`, `ry` | `ring6d` | 1 | 1 | 0 | 0 |
| `cx` | `grid2x3` | 1 | 1 | 2 | 0 |
| `cz` | `race8` | 1 | 1 | 2 | 0 |
| `swap` | `ring6d` | 1 | 1 | 6 | 0 |
| `measure` | `chain4` | 1 | 0 | 0 | 0 |
| `reset` | `race8` | 1 | 0 | 0 | 0 |
| `algo_par` | `grid2x3` | 9 | 5 | 4 | 0 |
| `algo_ghz` | `chain4` | 8 | 4 | 3 | 0 |
| `algo_rot` | `ring6d` | 6 | 5 | 6 | 2 |
| `algo_ccx` | `grid2x3` | 15 | 15 | 10 | 0 |

"ops" is the certificate's copy of the circuit, after lowering: the Toffoli of `algo_ccx` is
fifteen ops. A `swap` is one op with one witness; its three CNOTs are in the pulses, which the
checker never reads.

## 2. One of them, in Lean: the GHZ chain

`algo_ghz` prepares a four-qubit GHZ state: `h q[0]`, then `cx q[0],q[1]`, `cx q[1],q[2]`,
`cx q[2],q[3]`, then a measurement of each qubit. Below is its checker input as a Lean value:
the file `algo_ghz.qcheck.json`, field by field and in the order the checker's own reader,
`Input.ofJson`, puts them. (`qcheck` reads a well-formed file to the same value with its strict
reader, `Input.parse`; the value below and the parsed file print the same.) The panel in section 6
builds its values the same way.
-/

def case_algo_ghz : Input :=
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
The compiler laid the chain along the register, one qubit per trap, `q0` in `C3` down to `q3`
in `C0`. Each CNOT carries one ion one trap down to its partner, and that ion stays there:
nothing is sent home while the next gate still needs it. Play it:

{{figure case_algo_ghz}}

`circuit` is the circuit's name because the page's files were compiled from a file called
`circuit.qasm`. The checker prints that name; it checks nothing about it.
-/

#eval case_algo_ghz.gates.map (fun g => (g.dag, g.t, g.site))

/-!
When you run `qcheck` on the downloaded `algo_ghz.qcheck.json` (section 7), its second line is

```
  8 circuit ops, 4 gate witnesses, 3 moves, 0 rotations, 4 qubits
```

#### Exercise 1 (★) `ghz_counts`
Prove that the value above has those counts: the same file, read twice.
-/

theorem ghz_counts :
    case_algo_ghz.ops.length = 8 ∧ case_algo_ghz.gates.length = 4 ∧
      case_algo_ghz.moves.length = 3 ∧ case_algo_ghz.rots.length = 0 ∧ case_algo_ghz.nQubits = 4 :=
  ⟨rfl, rfl, rfl, rfl, rfl⟩

/-!
#### Exercise 2 (★) `ghz_holds`
Prove that the GHZ chain's certificate holds, the way lesson F1 proved the Bell pair's:
`check_sound` applied to the kernel's computation that the checker says `true`.
-/

theorem ghz_holds : Implements case_algo_ghz :=
  check_sound (by decide +kernel)

/-!
That is the whole O1 half of R10 for this example, proved in your browser with nothing trusted
but Lean's kernel.

#### Exercise 3 (★) `ghz_last_cx_cycle`
Find the cycle at which the witness for op 3 (`cx q[2],q[3]`) fires, in trap `C0` on `q2` and
`q3`: replace `sorry` by `⟨t, proof⟩` with your cycle. Read it off the figure (step to the
last two-qubit gate) or off the `#eval` above. As in lesson F1, `by simp [case_algo_ghz]` shows
membership in a literal list.
-/

theorem ghz_last_cx_cycle :
    ∃ t, (⟨3, t, "C0", ["q2", "q3"]⟩ : GateW) ∈ case_algo_ghz.gates :=
  ⟨21, by simp [case_algo_ghz]⟩

/-!
At that cycle the replay must have both ions in `C0`. `q3` started there; `q2` came down from
`C1` at the move stamped 16.

{{figure case_algo_ghz at=21}}

#### Exercise 4 (★) `ghz_last_cx_meets`
Prove it: at the start of cycle 21, `q2` and `q3` are both in `C0`.
-/

theorem ghz_last_cx_meets :
    look (posAt case_algo_ghz 21) "q2" = some "C0" ∧ look (posAt case_algo_ghz 21) "q3" = some "C0" :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
## 3. A loop that turns: the parity check by rotation

`algo_rot` is the shape of one stabilizer measurement: an ancilla `q0` and three data qubits,
`h q[0]`, three CNOTs from `q0`, `h q[0]`, then `q0` is measured. It was compiled by the
**rotation** pass. On `ring6d` the loop `L0 = S0 .. S5` cannot gate; the docks `A0` (off `S0`)
and `A3` (off `S3`) can. The ancilla waits at the dock `A0`; each data ion steps into the dock,
meets it, and steps back; then the whole loop turns two slots (`delta = -2`) and brings the
next data ion to `S0`. A rotation is one witness for the whole loop: every ion standing on the
loop advances, every docked ion stays (lesson F1, `rotateState`).
-/

def case_algo_rot : Input :=
  { circuit := "circuit", arch := "ring6d", nQubits := 4,
    qmap := [(0, "q0"), (1, "q1"), (2, "q2"), (3, "q3")],
    init := [("q0", "A0"), ("q1", "S0"), ("q2", "S2"), ("q3", "S4")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "cx", [0, 2]⟩, ⟨3, "cx", [0, 3]⟩, ⟨4, "h", [0]⟩, ⟨5, "measure", [0]⟩],
    moves := [⟨3, "q1", "S0", "A0"⟩, ⟨9, "q1", "A0", "S0"⟩, ⟨12, "q2", "S0", "A0"⟩, ⟨18, "q2", "A0", "S0"⟩, ⟨21, "q3", "S0", "A0"⟩, ⟨27, "q3", "A0", "S0"⟩],
    rots := [⟨10, "L0", -2⟩, ⟨19, "L0", -2⟩],
    gates := [⟨0, 2, "A0", ["q0"]⟩, ⟨1, 8, "A0", ["q1", "q0"]⟩, ⟨2, 17, "A0", ["q2", "q0"]⟩, ⟨3, 26, "A0", ["q3", "q0"]⟩, ⟨4, 29, "A0", ["q0"]⟩],
    unrealised := [],
    gateSites := ["A0", "A3"],
    hops := [("S0", "S1"), ("S0", "S5"), ("S0", "A0"), ("S1", "S0"), ("S1", "S5"), ("S1", "A0"), ("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("S2", "S4"), ("S2", "A3"), ("S3", "S2"), ("S3", "S4"), ("S3", "A3"), ("S4", "S3"), ("S4", "S2"), ("S4", "A3"), ("S4", "S5"), ("S5", "S4"), ("S5", "S0"), ("S5", "S1"), ("S5", "A0"), ("A0", "S0"), ("A0", "S1"), ("A0", "S5"), ("A3", "S3"), ("A3", "S2"), ("A3", "S4")],
    loops := [("L0", ["S0", "S1", "S2", "S3", "S4", "S5"])] }

/-!
Step the figure to cycle 11, just after the first turn: `q2`, which started on `S2`, now
stands on `S0`, beside the dock -- and no move of `q2` has happened yet.

{{figure case_algo_rot at=11}}

#### Exercise 5 (★★) `q2_rides_the_loop`
Prove both halves: at the start of cycle 11 `q2` is on `S0`, and every move of `q2` in the
certificate is at cycle 12 or later. So the loop, not a move, brought it there.
-/

theorem q2_rides_the_loop :
    look (posAt case_algo_rot 11) "q2" = some "S0" ∧
      ∀ m ∈ case_algo_rot.moves, m.ion = "q2" → 12 ≤ m.t :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
## 4. Several examples at once

The three basic two-qubit gates were compiled onto three different devices: `cx` on the
lattice, `cz` on the race track, `swap` on the ring with docks. In each, one ion travels to
the other, the gate fires, and the travelling ion goes back where it came from. Here is the
`swap`, at its gate: `q1` came from the dock `A3` round the loop to `A0`, where `q0` waits.
-/

def case_cx : Input :=
  { circuit := "circuit", arch := "grid2x3", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "T0_1h"), ("q1", "T0_0v")],
    ops := [⟨0, "cx", [0, 1]⟩],
    moves := [⟨1, "q1", "T0_0v", "T0_1h"⟩, ⟨7, "q1", "T0_1h", "T0_0v"⟩],
    rots := [],
    gates := [⟨0, 6, "T0_1h", ["q0", "q1"]⟩],
    unrealised := [],
    gateSites := ["T0_0h", "T0_0v", "T0_1h", "T0_1v", "T0_2h", "T1_0v", "T1_1v"],
    hops := [("T0_0h", "T0_0v"), ("T0_0h", "T1_0v"), ("T0_1h", "T0_0v"), ("T0_1h", "T0_1v"), ("T0_1h", "T1_0v"), ("T0_1h", "T1_1v"), ("T0_2h", "T0_1v"), ("T0_2h", "T1_1v"), ("T0_0v", "T0_0h"), ("T0_0v", "T0_1h"), ("T0_0v", "T0_1v"), ("T0_1v", "T0_1h"), ("T0_1v", "T0_0v"), ("T0_1v", "T0_2h"), ("T1_0v", "T0_0h"), ("T1_0v", "T0_1h"), ("T1_0v", "T1_1v"), ("T1_1v", "T0_1h"), ("T1_1v", "T1_0v"), ("T1_1v", "T0_2h")],
    loops := [] }

def case_cz : Input :=
  { circuit := "circuit", arch := "race8", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "S0"), ("q1", "S1")],
    ops := [⟨0, "cz", [0, 1]⟩],
    moves := [⟨1, "q1", "S1", "S0"⟩, ⟨11, "q1", "S0", "S1"⟩],
    rots := [],
    gates := [⟨0, 10, "S0", ["q0", "q1"]⟩],
    unrealised := [],
    gateSites := ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"],
    hops := [("S0", "S1"), ("S0", "S7"), ("S1", "S0"), ("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("S3", "S2"), ("S3", "S4"), ("S4", "S3"), ("S4", "S5"), ("S5", "S4"), ("S5", "S6"), ("S6", "S5"), ("S6", "S7"), ("S7", "S6"), ("S7", "S0")],
    loops := [("L0", ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"])] }

def case_swap : Input :=
  { circuit := "circuit", arch := "ring6d", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "A0"), ("q1", "A3")],
    ops := [⟨0, "swap", [0, 1]⟩],
    moves := [⟨1, "q1", "A3", "S4"⟩, ⟨2, "q1", "S4", "S5"⟩, ⟨3, "q1", "S5", "A0"⟩, ⟨19, "q1", "A0", "S5"⟩, ⟨20, "q1", "S5", "S4"⟩, ⟨21, "q1", "S4", "A3"⟩],
    rots := [],
    gates := [⟨0, 18, "A0", ["q0", "q1"]⟩],
    unrealised := [],
    gateSites := ["A0", "A3"],
    hops := [("S0", "S1"), ("S0", "S5"), ("S0", "A0"), ("S1", "S0"), ("S1", "S5"), ("S1", "A0"), ("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("S2", "S4"), ("S2", "A3"), ("S3", "S2"), ("S3", "S4"), ("S3", "A3"), ("S4", "S3"), ("S4", "S2"), ("S4", "A3"), ("S4", "S5"), ("S5", "S4"), ("S5", "S0"), ("S5", "S1"), ("S5", "A0"), ("A0", "S0"), ("A0", "S1"), ("A0", "S5"), ("A3", "S3"), ("A3", "S2"), ("A3", "S4")],
    loops := [("L0", ["S0", "S1", "S2", "S3", "S4", "S5"])] }

/-!
{{figure case_swap at=18}}

A statement about several certificates is a statement about a list of them. `∀ c ∈ [a, b, c], P c`
is decidable whenever `P` is, so the kernel can settle it in one computation.

#### Exercise 6 (★) `two_qubit_gates_hold`
Prove that all three certificates are accepted by the checker.
-/

theorem two_qubit_gates_hold : ∀ c ∈ [case_cx, case_cz, case_swap], check c = true := by
  decide +kernel

/-!
#### Exercise 7 (★★) `visitors_go_home`
Prove that in all three, by cycle 30 every ion is back in the trap it started in: for every
pair `(ion, trap)` of `init`, the replay at cycle 30 has the ion in that trap.
-/

theorem visitors_go_home :
    ∀ c ∈ [case_cx, case_cz, case_swap], ∀ p ∈ c.init, look (posAt c 30) p.1 = some p.2 := by
  decide +kernel

/-!
## 5. Two examples with no witness at all

`measure` and `reset` have one op each and **no** witness. That is not an omission: lesson F1's
condition 9, `Covered`, asks a witness only of an op that is not a measurement, a reset or a
barrier (`needsWitness`). Where a readout happens, and that it happens in its place, is the
semantic half's business (Part G, and the readout comparison of the semantic check, `check_cert`).
So a certificate
with nothing to witness, nothing to move and nothing left unrealised holds as soon as its
qubit map is sound -- for every such certificate, not just these two.
-/

def case_measure : Input :=
  { circuit := "circuit", arch := "chain4", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "C1"), ("q1", "C0")],
    ops := [⟨0, "measure", [0]⟩],
    moves := [],
    rots := [],
    gates := [],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

def case_reset : Input :=
  { circuit := "circuit", arch := "race8", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "S0"), ("q1", "S1")],
    ops := [⟨0, "reset", [0]⟩],
    moves := [],
    rots := [],
    gates := [],
    unrealised := [],
    gateSites := ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"],
    hops := [("S0", "S1"), ("S0", "S7"), ("S1", "S0"), ("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("S3", "S2"), ("S3", "S4"), ("S4", "S3"), ("S4", "S5"), ("S5", "S4"), ("S5", "S6"), ("S6", "S5"), ("S6", "S7"), ("S7", "S6"), ("S7", "S0")],
    loops := [("L0", ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"])] }

/-!
#### Exercise 8 (★★★) `readout_only`
Prove it in general. `Implements` is eleven conditions joined by `∧`; `refine ⟨hu, hinj, htot, ?_, ...⟩`
supplies the first three and leaves eight goals. Each of the eight follows from `moves`,
`rots` or `gates` being empty (unfold the condition and `simp` with the hypothesis; for
`EventsSorted`, unfold `events`, `merge` and `sortedByT` too), except `Covered`, which follows
from `hops`: an op whose `needsWitness` is `false` cannot also have it `true`.
-/

theorem readout_only {inp : Input} (hu : inp.unrealised = []) (hinj : MapInjective inp)
    (htot : MapTotal inp) (hm : inp.moves = []) (hr : inp.rots = []) (hg : inp.gates = [])
    (hops : ∀ o ∈ inp.ops, needsWitness o = false) : Implements inp := by
  refine ⟨hu, hinj, htot, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
  · simp [EventsSorted, events, merge, sortedByT, hm, hr]
  · simp [RotationsKnown, hr]
  · simp [MovesAreHops, hm]
  · simp [GatesLegal, hg]
  · simp [OwnIons, hg]
  · intro o ho hn
    simp [hops o ho] at hn
  · simp [Grounded, hg]
  · simp [RespectsOrder, witnessQubits, hg]

/-!
#### Exercise 9 (★★) `readouts_hold`
Use `readout_only` for both published certificates. The map conditions and `hops` are small
computations (`decide +kernel`); the rest are `rfl`.
-/

theorem readouts_hold : Implements case_measure ∧ Implements case_reset :=
  ⟨readout_only rfl (by decide +kernel) rfl rfl rfl rfl (by decide +kernel),
   readout_only rfl (by decide +kernel) rfl rfl rfl rfl (by decide +kernel)⟩

/-!
## 6. All nineteen

The panel below does for every published example what Exercise 2 did for one: it reads each
checker input into a Lean value exactly as `Input.ofJson` would, and proves by
`decide +kernel` that it holds. Each row links the two files, and **watch** opens the real
compiled programme in the studio, the circuit beside it, with the certificate's rows from Lean
under it.

{{certificates}}

## 7. The same files, outside this page

A proof in this page and a run of `qcheck` decide the same `check` on the same input. To run
`qcheck` yourself you need the workspace's command, `qccd`, and Lean. Every word in the commands
below is typed as shown, except where a file you saved is: that path is yours.

**Step 1: Lean, and the workspace.** Install Lean's installer, elan: on macOS
`brew install elan-init`; on Linux and Windows as
[github.com/leanprover/elan](https://github.com/leanprover/elan) says (and step 2's command,
run before elan is installed, prints the way for each platform). Then open a new terminal and
install the workspace from the repository's `compiler` branch (its default branch, `main`, does
not have the checker yet). On macOS or Linux:

```
git clone -c core.autocrlf=false -b compiler https://github.com/yezhuoyang/QCCD
cd QCCD
python3.13 -m venv .venv && source .venv/bin/activate     # any Python 3.10 or newer
pip install -e ".[agent]"
```

On Windows, in PowerShell:

```
git clone -c core.autocrlf=false -b compiler https://github.com/yezhuoyang/QCCD
cd QCCD
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[agent]"
```

(`-c core.autocrlf=false` keeps every file's line endings as published. Git on Windows otherwise
rewrites them on checkout, and a certificate records a hash of its circuit file's bytes, so a
circuit checked out with other line endings gives a certificate that differs in that field.)

**Step 2: build the checker.**

```
qccd toolchain build-checker
```

builds `qcheck` from the clone's Lean sources -- Lean core only, no Mathlib -- runs it once on a
certificate it must accept and one it must reject, and from then on the workspace uses it. The
first time, elan downloads the Lean the checker is pinned to (`leanprover/lean4:v4.29.0-rc2`).
It prints (the `...` stand for where the checker is, on your computer)

```
building the checker in ... (about a minute the first time; it does not need Mathlib) ...
built the checker (checked: it accepted and rejected a test certificate): ...
the workspace uses it from now on; `qccd toolchain check <file>.qcheck.json` runs it
```

Without Lean it says how to install elan instead.

**Step 3: get the file.** Save [algo_ghz.qcheck.json](data/algo_ghz.qcheck.json), or any
row's file from the panel above.

**Step 4: run it** on the file you saved:

```
qccd toolchain check algo_ghz.qcheck.json
```

This runs the checker the workspace uses and passes its output and its exit status through. On
the published file it prints

```
qcheck circuit on chain4
  8 circuit ops, 4 gate witnesses, 3 moves, 0 rotations, 4 qubits
  ACCEPTED -- the program follows its certificate: mapping, transport, co-location of
  each op's own ions, order (QCCDC.Cert.check_sound).  It names no gate: which gate fires
  is decided by comparing the emitted pulses with the circuit (the semantic check), and
  R10 needs both.
```

**Step 5: read the verdict.** `ACCEPTED` and exit status 0 mean `check` returned `true` on
that input, so by `check_sound` the input satisfies `Implements`. `REJECTED` and exit status 1
come with `diagnose`'s list of the conditions that failed. Exit status 2 means the file is
missing or cannot be read as a checker input.

When this lesson was written, steps 1 to 4 were run as written on Windows, from a fresh clone,
and printed what is quoted here; they were not run on macOS or Linux.

A prebuilt `qcheck` is published too, for Windows and Linux on x86-64, pinned with its size and
SHA-256 in the repository's toolchain manifest; `qccd toolchain install` fetches it there,
together with the compiler, and runs it once on a certificate it must accept and one it must
reject. When this lesson was written there was no macOS build of either, so on a Mac step 2 is
the way. Building it yourself is worth doing on every platform anyway: then it is the checker of
the sources in front of you, and of lesson F1. `qccd toolchain status` says which checker the
workspace uses.

## 8. What you have checked, and what you have not

You have checked the O1 half of R10 for each published certificate: mapping, transport,
co-location of each op's own ions, order. That is a theorem about the **checker input**. Three
things tie it to the Compilation page's verdict, and none of them is in these two files:

* that the certificate describes the **source circuit** (the binding, `check_binding`, of the
  semantic check `check_cert`: its `circuit_ops` are the QASM op by op, its map covers the
  QASM's qubits);
* that it describes the **programme** (every witness names a real instruction of the right
  kind, and the programme's own transport equals the certificate's moves);
* O2, which gates the pulses make (Part G).

For those you need the circuit, the device and the programme, and the page publishes the
circuit as text but not the programme as a file. Lesson H4 compiles the GHZ chain again, in a
workspace of your own with the published compiler, has the whole of R10 checked on it, and shows
that its certificate is the one above.
-/
