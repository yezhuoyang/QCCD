import QCCDC.Cert.Sound

/-!
# The mutation lab

*Part F, lesson F5. You need lessons F1, F2 and F4.*

A checker is worth exactly what it refuses. The repository tests that with a **mutation suite**
(`mutate_cert`): it takes a certificate the checker accepts, seeds one defect at a
time, each a way a compiler can really be wrong, and requires the checker to refuse every mutant.
Its docstring quotes the criterion from the compiler's plan: a checker that accepts a mutant is a
checker that proves nothing.

The suite has fourteen mutations (its list `MUTATIONS`). This lesson applies all fourteen to a
published certificate, exactly as the script applies them, and refuses each mutant in Lean twice:
once by the kernel's computation of `check`, and once by the condition it breaks, with a rejection
lemma. One mutant is accepted, and the suite expects that. You will see why, and who refuses the
bug it stands for.
-/

open QCCDC.Cert

-- setup
def algoRot : Input :=
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

theorem rejects_unsorted {inp : Input} (h : ¬ EventsSorted inp) : check inp = false :=
  decide_eq_false fun himp => h himp.2.2.2.1

/-!
## 1. The certificate

`algo_ghz` from the Compilation page: a GHZ chain, `h q[0]` and then `cx q[0],q[1]`,
`cx q[1],q[2]`, `cx q[2],q[3]`, and a measurement of each qubit, on the four-trap register
`chain4`. Every CNOT shares a qubit with the next, so the compiler lays the chain along the
register: each qubit's ion moves one trap to meet the next.

Here it is as Lean data, written by the same function that writes the published inputs of lesson
F1's panel, from the published file [algo_ghz.qcheck.json](data/algo_ghz.qcheck.json).
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

theorem ghz_ok : check ghz = true := by decide +kernel

/-!
{{figure ghz at=8}}

As in F2, `failing` lists the conditions that say `false`:
-/

def failing (c : Input) : List String :=
  [("1 unrealised = []", decide (c.unrealised = [])), ("2 MapInjective", decide (MapInjective c)),
   ("3 MapTotal", decide (MapTotal c)), ("4 EventsSorted", decide (EventsSorted c)),
   ("5 RotationsKnown", decide (RotationsKnown c)), ("6 MovesAreHops", decide (MovesAreHops c)),
   ("7 GatesLegal", decide (GatesLegal c)), ("8 OwnIons", decide (OwnIons c)),
   ("9 Covered", decide (Covered c)), ("10 Grounded", decide (Grounded c)),
   ("11 RespectsOrder", decide (RespectsOrder c))].filterMap fun p => if p.2 then none else some p.1

/-!
## 2. Fourteen mutants

Each definition below is the script's mutation applied to `ghz`: the same field changed in the
same way (the script's code is quoted in each comment). Two mutations find nothing to change in
this certificate, and the script says so ("nothing to mutate in this circuit"): every trap of
`chain4` can gate, so there is no trap to move a gate to that cannot; and the CNOTs of the GHZ
chain that share a qubit do not commute, since one's target is the other's control. For those two the lesson
uses `algo_rot` from F2 (out of sight here), whose loop sites cannot gate and whose three CNOTs
share their control.
-/

-- drop_gate: d["gates"] = d["gates"][1:]
def ghzDrop : Input := { ghz with gates := ghz.gates.drop 1 }
-- duplicate_gate: d["gates"] = [d["gates"][0]] + d["gates"]
def ghzDup : Input := { ghz with gates := ⟨0, 2, "C3", ["q0"]⟩ :: ghz.gates }
-- shift_gate_time: d["gates"][-1]["t"] = 0
def ghzShift : Input :=
  { ghz with gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C2", ["q0", "q1"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩,
                       ⟨3, 0, "C0", ["q2", "q3"]⟩] }
-- teleport_move: the first move goes to the first trap that is not a hop from its source
def ghzTeleport : Input :=
  { ghz with moves := [⟨3, "q0", "C3", "C0"⟩, ⟨9, "q1", "C2", "C1"⟩, ⟨16, "q2", "C1", "C0"⟩] }
-- wrong_departure: the first move departs from the first trap other than its own
def ghzDeparture : Input :=
  { ghz with moves := [⟨3, "q0", "C0", "C2"⟩, ⟨9, "q1", "C2", "C1"⟩, ⟨16, "q2", "C1", "C0"⟩] }
-- ungateable_site: the first gate moves to the first trap that cannot gate (algo_rot)
def rotUngateable : Input :=
  { algoRot with gates := [⟨0, 2, "S0", ["q0"]⟩, ⟨1, 8, "A0", ["q1", "q0"]⟩, ⟨2, 17, "A0", ["q2", "q0"]⟩,
                           ⟨3, 26, "A0", ["q3", "q0"]⟩, ⟨4, 29, "A0", ["q0"]⟩] }
-- alias_qubits: d["map"][ks[1]] = d["map"][ks[0]]
def ghzAlias : Input := { ghz with qmap := [(0, "q0"), (1, "q0"), (2, "q2"), (3, "q3")] }
-- mark_unrealised: d["unrealised"] = [0]
def ghzUnrealised : Input := { ghz with unrealised := [0] }
-- unsort_moves: d["moves"] = list(reversed(d["moves"]))
def ghzUnsorted : Input := { ghz with moves := ghz.moves.reverse }
-- reorder_gates: the first two gates exchange their cycles
def ghzReorder : Input :=
  { ghz with gates := [⟨0, 8, "C3", ["q0"]⟩, ⟨1, 2, "C2", ["q0", "q1"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩,
                       ⟨3, 21, "C0", ["q2", "q3"]⟩] }
-- swap_dependent_gates: two witnesses of non-commuting ops sharing a qubit exchange their ops
def ghzSwapDep : Input :=
  { ghz with gates := [⟨1, 2, "C3", ["q0"]⟩, ⟨0, 8, "C2", ["q0", "q1"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩,
                       ⟨3, 21, "C0", ["q2", "q3"]⟩] }
-- swap_commuting_gates: the same, for two CNOTs that commute (algo_rot)
def rotSwapCommuting : Input :=
  { algoRot with gates := [⟨0, 2, "A0", ["q0"]⟩, ⟨2, 8, "A0", ["q1", "q0"]⟩, ⟨1, 17, "A0", ["q2", "q0"]⟩,
                           ⟨3, 26, "A0", ["q3", "q0"]⟩, ⟨4, 29, "A0", ["q0"]⟩] }
-- foreign_ions: a witness names other ions, standing together in a gate-capable trap
def ghzForeign : Input :=
  { ghz with gates := [⟨0, 2, "C0", ["q3"]⟩, ⟨1, 8, "C2", ["q0", "q1"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩,
                       ⟨3, 21, "C0", ["q2", "q3"]⟩] }
-- swap_operands: a gate's two ions exchange places
def ghzSwapOps : Input :=
  { ghz with gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C2", ["q1", "q0"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩,
                       ⟨3, 21, "C0", ["q2", "q3"]⟩] }

/-!
What each breaks, by Lean's own decision of each condition:
-/

#eval [("drop_gate", failing ghzDrop), ("duplicate_gate", failing ghzDup),
       ("shift_gate_time", failing ghzShift), ("teleport_move", failing ghzTeleport),
       ("wrong_departure", failing ghzDeparture), ("ungateable_site", failing rotUngateable),
       ("alias_qubits", failing ghzAlias), ("mark_unrealised", failing ghzUnrealised),
       ("unsort_moves", failing ghzUnsorted), ("reorder_gates", failing ghzReorder),
       ("swap_dependent_gates", failing ghzSwapDep), ("swap_commuting_gates", failing rotSwapCommuting),
       ("foreign_ions", failing ghzForeign), ("swap_operands", failing ghzSwapOps)]

/-!
These are the conditions the command-line checker names too: run on the same mutants,
`qcheck` prints the same failures under REJECTED. Several mutants break more than one condition.
A move sent somewhere it cannot go also leaves its ion out of place for the next gate; a reversed
move list also scrambles the replay; and `wrong_departure` here also fails to be a hop, because
the trap the script picks, `C0`, is not next to `C2`.

## 3. Refused by computation

The first kind of refusal is the kernel computing `check` on each mutant: thirteen `false`s.
-/

theorem kernel_refuses :
    check ghzDrop = false ∧ check ghzDup = false ∧ check ghzShift = false ∧
    check ghzTeleport = false ∧ check ghzDeparture = false ∧ check rotUngateable = false ∧
    check ghzAlias = false ∧ check ghzUnrealised = false ∧ check ghzUnsorted = false ∧
    check ghzReorder = false ∧ check ghzSwapDep = false ∧ check rotSwapCommuting = false ∧
    check ghzForeign = false := by
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩ <;> decide +kernel

/-!
## 4. Refused by the condition they break

A computed `false` says the mutant is refused; it does not say why. A rejection lemma says why: it
names the condition and the move or witness that breaks it. Here are the mutants this lesson
refuses for you; after them, the ones you refuse.

`duplicate_gate` gives the Hadamard two witnesses, and coverage wants exactly one:
-/

theorem dup_refused : check ghzDup = false :=
  rejects_dropped_gate (o := ⟨0, "h", [0]⟩) (by simp [ghzDup, ghz]) (by decide +kernel) (by decide +kernel)

/-!
`shift_gate_time` moves the last CNOT to cycle 0, when `q2` is still in `C1`. It also puts that
CNOT before the one it follows on qubit 2, and the order condition fails as well; either lemma
refuses it. Click the CX row at cycle 0. The studio's own rule check, replaying the same moves,
flags it too (R6b: a two-qubit gate acts only on ions together in one gate zone).

{{figure ghzShift at=0}}
-/

theorem shift_refused : check ghzShift = false :=
  rejects_not_colocated (g := ⟨3, 0, "C0", ["q2", "q3"]⟩) (i := "q2")
    (by simp [ghzShift]) (by simp) (by decide +kernel)

/-!
`wrong_departure` has `q0` leave from `C0`, where it is not; `ungateable_site` puts `algo_rot`'s
first Hadamard on the loop site `S0`, which cannot gate.
-/

theorem departure_refused : check ghzDeparture = false :=
  rejects_wrong_departure (m := ⟨3, "q0", "C0", "C2"⟩) (by simp [ghzDeparture]) (by decide +kernel)

theorem ungateable_refused : check rotUngateable = false :=
  rejects_ungateable_site (g := ⟨0, 2, "S0", ["q0"]⟩) (by simp [rotUngateable]) (by decide +kernel)

/-!
`alias_qubits` puts qubits 0 and 1 on one ion; `mark_unrealised` has the compiler admit it gave up
on op 0.
-/

theorem alias_refused : check ghzAlias = false :=
  rejects_aliased_qubits (by decide +kernel)

theorem unrealised_refused : check ghzUnrealised = false :=
  rejects_unrealised (by simp [ghzUnrealised])

/-!
`reorder_gates` exchanges the cycles of the Hadamard and the first CNOT, which share qubit 0.
`swap_dependent_gates` exchanges which op the same two witnesses realise. Joined with their ops'
qubits, both produce the same pair, op 0 at cycle 8 and op 1 at cycle 2, and the same lemma
refuses both. (The second also breaks `OwnIons`: each of the two witnesses now names the other
op's ions.)
-/

theorem reorder_refused : check ghzReorder = false :=
  rejects_reordering (a := ⟨0, 8, [0]⟩) (b := ⟨1, 2, [0, 1]⟩)
    (by decide +kernel) (by decide +kernel) (by decide) (by decide +kernel) (by decide)

theorem swapdep_refused : check ghzSwapDep = false :=
  rejects_reordering (a := ⟨0, 8, [0]⟩) (b := ⟨1, 2, [0, 1]⟩)
    (by decide +kernel) (by decide +kernel) (by decide) (by decide +kernel) (by decide)

/-!
#### Exercise 1 (★) `teleport_breaks`
Identify what `teleport_move` breaks: prove that, of the eleven conditions, exactly transport (6)
and co-location (7) fail. The first move now takes `q0` from `C3` straight to `C0`, so it is not a
hop, and `q0` is not in `C2` for the first CNOT. The studio, asked to make the move, refuses it:
there is no segment between `C3` and `C0`.

{{figure ghzTeleport at=8}}
-/

theorem teleport_breaks : failing ghzTeleport = ["6 MovesAreHops", "7 GatesLegal"] := by
  decide +kernel

/-!
#### Exercise 2 (★★) `teleport_refused`
Refuse it by its first fault, with `rejects_teleport`: the move is in the list, and `("C3", "C0")`
is not a hop of `chain4`.
-/

theorem teleport_refused : check ghzTeleport = false :=
  rejects_teleport (m := ⟨3, "q0", "C3", "C0"⟩) (by simp [ghzTeleport]) (by decide +kernel)

/-!
#### Exercise 3 (★★) `drop_refused`
`drop_gate` removes the Hadamard's witness. Refuse it with `rejects_dropped_gate`. The ops of
`ghzDrop` are `ghz`'s, so give `simp` both names for the membership.
-/

theorem drop_refused : check ghzDrop = false :=
  rejects_dropped_gate (o := ⟨0, "h", [0]⟩) (by simp [ghzDrop, ghz]) (by decide +kernel) (by decide +kernel)

/-!
#### Exercise 4 (★★) `unsorted_refused`
`unsort_moves` reverses the move list. Refuse it with lesson F4's `rejects_unsorted` (restated here
out of sight): the merged events are not in cycle order.
-/

theorem unsorted_refused : check ghzUnsorted = false :=
  rejects_unsorted (by decide +kernel)

/-!
#### Exercise 5 (★★) `foreign_refused`
`foreign_ions` finds, at the Hadamard's cycle, an ion that is not the Hadamard's own standing alone
in a trap that can gate: `q3` in `C0`. It rewrites the witness to name `q3` there. Every ion the
witness names is where it says, so co-location holds, and so does everything else; only `OwnIons`
fails. The studio plays it without complaint: it gates whatever ion it is told to, and only the
certificate checker knows which ion is the Hadamard's. Refuse it with `rejects_foreign_ions`. For
the last hypothesis, use F3's method: a rearrangement would make `permB` say `true`
(`permB_complete`), and it says `false`.

{{figure ghzForeign at=2}}
-/

theorem foreign_refused : check ghzForeign = false :=
  rejects_foreign_ions (g := ⟨0, 2, "C0", ["q3"]⟩) (o := ⟨0, "h", [0]⟩)
    (by simp [ghzForeign]) (by simp [ghzForeign, ghz]) rfl
    (fun h => absurd (permB_complete h) (by decide +kernel))

/-!
#### Exercise 6 (★★) `commuting_swap_refused`
`swap_commuting_gates` exchanges which op two of `algo_rot`'s CNOTs realise: the one on qubits 0
and 1 and the one on 0 and 2. They commute, so the unitary is the same; since 2026-10-19 the order
condition refuses the swap anyway (F2, section 8). Its witness join now has op 1 at cycle 17 and op
2 at cycle 8. Refuse it with `rejects_reordering`.
-/

#eval witnessQubits rotSwapCommuting

theorem commuting_swap_refused : check rotSwapCommuting = false :=
  rejects_reordering (a := ⟨1, 17, [0, 1]⟩) (b := ⟨2, 8, [0, 2]⟩)
    (by decide +kernel) (by decide +kernel) (by decide) (by decide +kernel) (by decide)

/-!
## 5. The mutant that passes

`swap_operands` exchanges the two ions of the first two-ion witness: the first CNOT is now
witnessed on `q1, q0` instead of `q0, q1`.

{{figure ghzSwapOps at=8}}

#### Exercise 7 (★) `swap_operands_accepted`
Prove the checker accepts it.
-/

theorem swap_operands_accepted : check ghzSwapOps = true := by
  decide +kernel

/-!
That is what the suite expects: it lists `swap_operands` as the one mutation the proved checker is
not supposed to catch. A witness names the ions an operation ran on, never which one is the
control; `OwnIons` compares them as a rearrangement (F2). So the order of a witness's ions carries
no meaning, and exchanging them changes nothing a correct verdict depends on. `algo_rot`'s own
witnesses list the loop rider first, the other way round from the ops' qubits, and they are correct.

The bug the mutation stands for is real: a CNOT whose control and target are exchanged. That bug
lives in the **pulses**, which the checker's input does not carry (the certificate file lists them
beside each witness, as text) and Lean's checker never reads. What refuses
it is the semantic half of the verdict, the semantic check (`check_cert`), which composes the
programme's emitted pulses into a stabilizer tableau (or, off the Clifford gates, an exact unitary)
and compares it with the source circuit (Part G builds that comparison). We ran all three versions
of the bug through the semantic check, with the proved checker, on the published Bell certificate
([bell.qcert.json](data/bell.qcert.json)) and
its programme:

| what was changed | proved checker | the semantic check | R10 |
|---|---|---|---|
| the CNOT's witness lists its ions the other way round (certificate and checker input) | accepted | everything ok | passed |
| only the checker's input, not the certificate | not run: refused by `qcheck_binding`, "not this certificate's input" | the rest ok | failed |
| the programme: the CNOT's single-ion pulses moved to the other ion, so it is a CNOT from `q1` to `q0`; the certificate unchanged | accepted | TABLEAU MISMATCH | failed: "the compiled program does not implement the circuit" |

The first row is right to pass: nothing in it is wrong. The third is the real bug, and only the
semantic half can see it, because only it reads the pulses. Neither half alone is the verdict R10.

## 6. Write your own

A mutation is a function from certificates to certificates, so a refusal can be proved for every
certificate at once, not one mutant at a time.

#### Exercise 8 (★★) `mark_unrealised_refused`
Here is `mark_unrealised` as a Lean function, for any op index `k`. Prove that every certificate it
produces is refused.
-/

def markUnrealised (k : Nat) (d : Input) : Input := { d with unrealised := k :: d.unrealised }

theorem mark_unrealised_refused (k : Nat) (d : Input) : check (markUnrealised k d) = false :=
  rejects_unrealised (by simp [markUnrealised])

/-!
#### Exercise 9 (★★★) `alias_refused_always`
`alias_qubits` as a function: when the map starts with two entries, the second qubit is put on the
first one's ion. Prove that, whenever those two entries are for different qubits, the result is
refused. Hint: `rejects_aliased_qubits` wants `¬ MapInjective`; suppose `h : MapInjective ...` and
apply it to the two entries, which are in the map (`simp [aliasFirstTwo, hq]`) and share an ion
(`rfl`); it says the qubits are equal, which `hab` denies.
-/

def aliasFirstTwo (d : Input) : Input :=
  match d.qmap with
  | (a, i) :: (b, _) :: rest => { d with qmap := (a, i) :: (b, i) :: rest }
  | _ => d

theorem alias_refused_always {d : Input} {a b : Nat} {i j : String} {rest : List (Nat × String)}
    (hq : d.qmap = (a, i) :: (b, j) :: rest) (hab : a ≠ b) : check (aliasFirstTwo d) = false :=
  rejects_aliased_qubits fun h =>
    hab (h (a, i) (by simp [aliasFirstTwo, hq]) (b, i) (by simp [aliasFirstTwo, hq]) rfl)

example : check (aliasFirstTwo ghz) = false := alias_refused_always (j := "q1") (rest := [(2, "q2"), (3, "q3")]) rfl (by decide)

/-!
#### Exercise 10 (★★★) `dupFirst_refused`
`duplicate_gate` as a function: the first witness, written twice. Prove it refused for every
**accepted** certificate whose first witness realises an op that needs one. The accepted
certificate's `Covered` (from `check_sound hd`, condition 9) says that op has exactly one witness;
with the duplicate it has two.

Hints: name the count `hc` (`(check_sound hd).2.2.2.2.2.2.2.2.1 o ho hn`), then apply
`rejects_dropped_gate` to the op. `simp [dupFirst, hg, hi] at hc ⊢` brings both counts to the
length of the same filtered `rest`; if anything is left, `<;> omega` finishes it.
-/

def dupFirst (d : Input) : Input :=
  match d.gates with
  | [] => d
  | g :: _ => { d with gates := g :: d.gates }

theorem dupFirst_refused {d : Input} {g : GateW} {rest : List GateW} (hg : d.gates = g :: rest)
    {o : Op} (ho : o ∈ d.ops) (hi : o.idx = g.dag) (hn : needsWitness o = true)
    (hd : check d = true) : check (dupFirst d) = false := by
  have hc := (check_sound hd).2.2.2.2.2.2.2.2.1 o ho hn
  apply rejects_dropped_gate (o := o) (by simpa [dupFirst, hg] using ho) hn
  simp [dupFirst, hg, hi] at hc ⊢ <;> omega

/-!
Applied to the published certificate, your theorem refuses the script's mutant: `dupFirst ghz` is
`ghzDup`.
-/

example : check (dupFirst ghz) = false :=
  dupFirst_refused (g := ⟨0, 2, "C3", ["q0"]⟩) (rest := ghz.gates.drop 1) rfl
    (o := ⟨0, "h", [0]⟩) (by simp [ghz]) rfl (by decide +kernel) ghz_ok
