import QCCDC.Cert.Sound

/-!
# The specification, condition by condition

*Part F, lesson F2. You need lesson F1: the Bell certificate `bell`, `posAt`, `look`,
`check`, `check_sound`, and the rejection lemmas.*

Lesson F1 printed the specification `Implements` and read its eleven conditions in one line
each. This lesson takes them one at a time: what each one says, how the checker writes it, and
a certificate that breaks **exactly that one** and nothing else. A certificate that breaks one
condition alone is the evidence that the condition is not redundant: take it away and the
checker would accept that certificate.

Most certificates below are F1's `bell` with one thing changed. The rotation conditions need a
loop, so for those the lesson uses a second published certificate, `algo_rot`, compiled for a
ring.
-/

open QCCDC.Cert

-- setup
def bell : Input :=
  { circuit := "bell", arch := "chain4", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")],
    init := [("q0", "C1"), ("q1", "C0")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [1]⟩],
    moves := [⟨3, "q0", "C1", "C0"⟩],
    rots := [],
    gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

/-!
## 0. Which conditions fail

`decide P` is the answer, `true` or `false`, of the decision procedure of a condition `P` (lesson
F3 is about those procedures). `failing` asks all eleven and keeps the names of those that say
`false`. It is the checker's `diagnose` with shorter names; neither is part of the proof.
-/

def failing (c : Input) : List String :=
  [("1 unrealised = []", decide (c.unrealised = [])), ("2 MapInjective", decide (MapInjective c)),
   ("3 MapTotal", decide (MapTotal c)), ("4 EventsSorted", decide (EventsSorted c)),
   ("5 RotationsKnown", decide (RotationsKnown c)), ("6 MovesAreHops", decide (MovesAreHops c)),
   ("7 GatesLegal", decide (GatesLegal c)), ("8 OwnIons", decide (OwnIons c)),
   ("9 Covered", decide (Covered c)), ("10 Grounded", decide (Grounded c)),
   ("11 RespectsOrder", decide (RespectsOrder c))].filterMap fun p => if p.2 then none else some p.1

#eval failing bell

/-!
## 1. Nothing given up: `unrealised = []`

When the compiler cannot realise an operation it says so, by writing the operation's index in
`unrealised`. The first condition asks that list to be empty. It needs no replay: the compiler's
own admission is enough to refuse the certificate.

`bellUnrealised` is `bell` with the CNOT listed as unrealised, although its witness is still
there and holds. Every row of the figure is green, and Lean still says `false`: the rows are the
conditions on single moves and witnesses, and this one is about the certificate as a whole, as
are several below. Read a figure's verdict first, then its rows.

{{figure bellUnrealised at=8}}
-/

def bellUnrealised : Input := { bell with unrealised := [1] }

#eval failing bellUnrealised

/-!
## 2. The map: `MapInjective` and `MapTotal`
-/

#print MapInjective
#print MapTotal

/-!
`MapInjective` says that two entries of `qmap` naming the same ion name the same qubit: no ion
carries two qubits. `MapTotal` says the map has one entry per qubit of the circuit.

Here is why the first one is needed. In `bellAliased` both qubits are put on ion `q0`, and the
CNOT's witness names `q0` twice. Condition 8 (`OwnIons`, section 6) compares the witness's ions
with the ions of the op's qubits, `[q0, q0]`: the same list. Condition 7 asks that the named ions
be in the trap: `q0` is. Only `MapInjective` sees a CNOT of an ion with itself.

{{figure bellAliased at=8}}
-/

def bellAliased : Input :=
  { bell with qmap := [(0, "q0"), (1, "q0")],
              gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q0"]⟩] }

#eval failing bellAliased

/-!
#### Exercise 1 (★) `aliased_only_injective`
Prove that `bellAliased` satisfies `OwnIons` and breaks `MapInjective`. Each half is a
computation for the kernel.
-/

theorem aliased_only_injective : OwnIons bellAliased ∧ ¬ MapInjective bellAliased :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
`MapTotal` alone: say the circuit has three qubits while the map has two. Lesson F1 proved, for
every certificate, that this is refused (`rejects_partial_map`).
-/

def bellPartial : Input := { bell with nQubits := 3 }

#eval failing bellPartial

/-!
## 3. Replay in order: `EventsSorted`

The replay of F1 is three steps. `merge` interleaves the moves and the rotations into one list
of events, `snapshots` records where every ion is before each event, and `stateIn R t` takes the
**first** snapshot whose cycle is at or after `t`. That last step is right only if the snapshots
are in cycle order, and they are in the order the certificate lists its moves. So condition 4
asks the merged list to be sorted by cycle:
-/

#print EventsSorted
#print sortedByT

/-!
`bellUnsorted` adds two moves after the CNOT, `q1` back to `C1` at cycle 20 and `q0` back to `C1`
at cycle 15, and lists them in that order: 20 before 15. Every move still departs from where the
replay has its ion, and every witness still holds. But look at what the replay does with the move
stamped 15:
-/

def bellUnsorted : Input :=
  { bell with moves := [⟨3, "q0", "C1", "C0"⟩, ⟨20, "q1", "C0", "C1"⟩, ⟨15, "q0", "C0", "C1"⟩] }

#eval [14, 15, 16, 20, 21].map fun t => (t, look (posAt bellUnsorted t) "q0")

/-!
`q0` is still in `C0` at cycles 16 and 20, after the cycle its move names, and arrives in `C1` only
at 21: the replay applied the moves in the order they were written. No row of a figure would show
that, because each row checks one move against that same replay. Only `EventsSorted` refuses it.
-/

#eval failing bellUnsorted

/-!
#### Exercise 2 (★) `unsorted_detected`
Prove that `bellUnsorted` breaks condition 4. (The module `QCCDC.Cert.Sound` has no rejection
lemma for it; lesson F4 writes one.)
-/

theorem unsorted_detected : ¬ EventsSorted bellUnsorted := by
  decide +kernel

/-!
## 4. Rotations: `rotateState` and `RotationsKnown`

On a device with a closed loop, the hardware can turn the whole loop at once, carrying every ion
that stands on it. A certificate records such a turn as one **rotation** witness `⟨t, loop,
delta⟩` rather than as one move per ion.

The published certificate `algo_rot` (on the Compilation page) uses two of them. The device is
`ring6d`: a loop `L0` of six sites `S0 ... S5` that cannot gate, and two docks that can, `A0` off
`S0` and `A3` off `S3`. The circuit is a parity check: `h q[0]`, three CNOTs from qubit 0 to
qubits 1, 2 and 3, `h q[0]`, and a measurement of qubit 0. The compiler parks ion `q0` (qubit 0)
at the dock `A0` and puts the other three on the loop, two sites apart. Each in turn steps from
`S0` into `A0`, has its CNOT, and steps back; then the loop turns by `-2` and brings the next one
to `S0`.

Here it is as Lean data, written by the same function (`lean_input`) that writes every published
input for the panel at the end of lesson F1, from the published file
[algo_rot.qcheck.json](data/algo_rot.qcheck.json). The
`loops` field, like `hops` and `gateSites`, is read from the device:
-/

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

#eval check algoRot

/-!
What a rotation does to the ions is `rotateState`:
-/

#print rotateState

/-!
For each ion it looks up the ion's site in the loop's node list (`slotOf`). An ion at slot `i`
goes to slot `i + delta`, counted round the loop (the `% n`, written so that a negative `delta`
works too). An ion whose site is not on the loop at all, `none`, stays where it is: that is the
docked ion `q0` here. Before and after the first turn:
-/

#eval posAt algoRot 10
#eval rotateState ["S0", "S1", "S2", "S3", "S4", "S5"] (-2) (posAt algoRot 10)

/-!
`q1` has just stepped back to `S0` (slot 0) and goes to slot 4, `S4`; `q2` goes from `S2` to `S0`,
next in line for the dock; `q0` in `A0` does not move.

This time, instead of asking the page to turn the certificate into a programme, as lesson F1's
figures do, the lesson computes the studio programme from `algoRot` itself, in Lean, so you can
see how it is done. `play` walks the cycles in order and writes, at each one, the gates its witnesses claim, the
moves, and the turns, as the studio's own statements (`p.gate`, `p.shuttle`, `p.rotate`), with a
cooling step before each two-qubit gate as F1's figures do. Edit `algoRot` and the figure follows.
-/

structure Play where
  arch : String
  src : String

def qt (s : String) : String := "\"" ++ s ++ "\""

def gateLines (c : Input) (g : GateW) : List String :=
  match c.ops.find? (fun o => o.idx = g.dag) with
  | none => []
  | some o =>
    let ions := o.qubits.filterMap (ionOf c)
    if ions.length ≥ 2 then
      ["p.cool()", "p.gate(" ++ qt o.name.toUpper ++ ", [[" ++ ", ".intercalate (ions.map qt) ++ "]])"]
    else ["p.gate(" ++ qt o.name.toUpper ++ ", [], [" ++ qt g.site ++ "])"]

def play (c : Input) : Play :=
  let last := (c.moves.map (·.t) ++ c.rots.map (·.t) ++ c.gates.map (·.t)).foldl max 0
  let init := "p.init({" ++ ", ".intercalate (c.init.map fun p => qt p.1 ++ ": " ++ qt p.2) ++ "})"
  let measured := (c.ops.filter (·.name = "measure")).flatMap fun o => o.qubits.filterMap (ionOf c)
  let steps := (List.range (last + 1)).flatMap fun t =>
    (c.gates.filter (·.t = t)).flatMap (gateLines c) ++
    (c.moves.filter (·.t = t)).map (fun m => "p.shuttle(" ++ qt m.ion ++ ", [" ++ qt m.src ++ ", " ++ qt m.dst ++ "])") ++
    (c.rots.filter (·.t = t)).map (fun r => "p.rotate(" ++ toString r.delta ++ ", loop=" ++ qt r.loop ++ ")")
  { arch := c.arch,
    src := "\n".intercalate (init :: steps ++ ["p.measure([" ++ ", ".intercalate (measured.map qt) ++ "])"]) }

def algoRotPlay : Play := play algoRot

#eval IO.println algoRotPlay.src

/-!
The figure opens on the seventh statement, the first turn. Step on: `q2` arrives at `S0` and
steps into the dock.

{{studio algoRotPlay at=6}}

#### Exercise 3 (★) `rotation_brings_q2`
Prove that `q2` is in `S2` at the start of cycle 10 and in `S0` at the start of cycle 11: the
turn of cycle 10 carries it to the dock's door.
-/

theorem rotation_brings_q2 :
    look (posAt algoRot 10) "q2" = some "S2" ∧ look (posAt algoRot 11) "q2" = some "S0" :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
The loop's node list comes from the device, never from the compiler: if a compiler could supply
its own order, it could turn ions to wherever its gates needed them. So a rotation must name a
loop the device has, and condition 5 says exactly that:
-/

#print RotationsKnown

/-!
What happens to a rotation of an unknown loop in the replay? `Ev.apply` looks the loop up, finds
nothing, and leaves every ion where it is. A compiler could then claim any turns it liked of a
loop nobody declared, and the replay would simply ignore them. `rotUnknown` adds one such turn,
of a loop `L1`, after everything else; nothing else is disturbed, and only condition 5 fails.
-/

def rotUnknown : Input :=
  { algoRot with rots := [⟨10, "L0", -2⟩, ⟨19, "L0", -2⟩, ⟨30, "L1", 1⟩] }

#eval failing rotUnknown

/-!
Rename both real turns to `L1` and the damage shows: the replay never turns the loop, `q2` is
still in `S2` at cycle 12, and the move that should take it from `S0` to the dock departs from the
wrong site. Ask the studio to play it and it refuses: the device has no loop `L1`.

{{studio rotRenamedPlay at=6}}
-/

def rotRenamed : Input := { algoRot with rots := [⟨10, "L1", -2⟩, ⟨19, "L1", -2⟩] }
def rotRenamedPlay : Play := play rotRenamed

#eval failing rotRenamed

/-!
#### Exercise 4 (★★) `unknown_loop_rejected`
Prove `rotUnknown` refused, with `rejects_unknown_loop`: the rotation is in the list, and no loop
of the device has its name. As in F1, `by simp [rotUnknown]` shows membership in a literal list;
the second fact is a computation.
-/

#check @rejects_unknown_loop

theorem unknown_loop_rejected : check rotUnknown = false :=
  rejects_unknown_loop (r := ⟨30, "L1", 1⟩) (by simp [rotUnknown]) (by decide +kernel)

/-!
#### Exercise 5 (★★★) `docked_stays`
Prove, for every loop, every turn and every placement, that an ion standing on a site the loop
does not contain is where it was after the turn. This is the docked ancilla of `algo_rot`, for
every certificate there is.

Hints: `unfold rotateState` and `dsimp only` expose an `if`; `split` takes its two cases. In the
second, do induction on the placement `st`; at `p :: st`, ask whether `p.1 = i` (`by_cases`).
When it is not, `cases e : slotOf nodes p.2 0` lets `simp_all [look]` see what the map does to
`p`.
-/

theorem docked_stays (nodes : List String) (delta : Int) (st : List (String × String))
    (i s : String) (h : look st i = some s) (hs : slotOf nodes s 0 = none) :
    look (rotateState nodes delta st) i = some s := by
  unfold rotateState
  dsimp only
  split
  · exact h
  · induction st with
    | nil => simp [look] at h
    | cons p st ih =>
      by_cases hp : p.1 = i
      · have hp2 : p.2 = s := by simpa [look, hp] using h
        subst hp2
        simp [look, hp, hs]
      · have h' : look st i = some s := by simpa [look, hp] using h
        have := ih h'
        cases e : slotOf nodes p.2 0 <;> simp_all [look]

/-!
## 5. Moves: `MovesAreHops`
-/

#print MovesAreHops

/-!
Two halves: the pair `(src, dst)` is a hop of the device, and the ion departs from where the
replay has it. F1's teleport broke the first. `bellDeparture` breaks only the second: it adds a
"move" of `q1` from `C1` to `C0` at cycle 5, while `q1` is in `C0`. The hop exists, and after the
move the replay has `q1` in `C0`, where it already was, so nothing downstream changes. The row
says what is wrong.

{{figure bellDeparture at=5}}
-/

def bellDeparture : Input :=
  { bell with moves := [⟨3, "q0", "C1", "C0"⟩, ⟨5, "q1", "C1", "C0"⟩] }

#eval failing bellDeparture

/-!
#### Exercise 6 (★★) `departure_rejected`
Prove it refused, with `rejects_wrong_departure`: the move is in the list (`simp`, as above), and
its last hypothesis is about `posAt`, the replay; compute it.
-/

#check @rejects_wrong_departure

theorem departure_rejected : check bellDeparture = false :=
  rejects_wrong_departure (m := ⟨5, "q1", "C1", "C0"⟩) (by simp [bellDeparture]) (by decide +kernel)

/-!
## 6. Witnesses: `GatesLegal` and `OwnIons`
-/

#print GatesLegal

/-!
Again two halves: the trap can gate, and every ion the witness names is in it at the witness's
cycle. F1's late move broke the second. On `chain4` every trap gates, so the first needs the
ring: `ringH` is a one-qubit certificate on `ring6d` that puts a Hadamard on ion `q0` where it
stands, in `S1`, a site of the loop. The ion is there; the site cannot gate. The studio's own rule
check, replaying the same programme, flags it too (R6: a gate only where the zone can).

{{figure ringH at=1}}
-/

def ringH : Input :=
  { algoRot with nQubits := 1, qmap := [(0, "q0")], init := [("q0", "S1")],
                 ops := [⟨0, "h", [0]⟩], moves := [], rots := [], gates := [⟨0, 1, "S1", ["q0"]⟩] }

#eval failing ringH

theorem ringH_rejected : check ringH = false :=
  rejects_ungateable_site (g := ⟨0, 1, "S1", ["q0"]⟩) (by simp [ringH]) (by decide +kernel)

/-!
`GatesLegal` speaks of the ions the witness names. That they are the op's own is condition 8:
-/

#print OwnIons

/-!
`List.Perm l₁ l₂` says that `l₁` is a rearrangement of `l₂`: the same elements, each as many
times. The witness's ions (each wrapped in `some`) must be a rearrangement of the ions the op's
qubits are mapped to (`ionOf`, which gives `none` for a qubit the map lacks, and no named ion is
`none`). So a witness names exactly its op's ions, each once, **in any order**.

The order is free because a witness says where an operation ran, not which operand plays which
part; the pulses say that (lesson F5 shows who reads them). `algo_rot` uses the freedom: the
rotation pass writes the ion riding the loop first and the docked one second, whichever is the
control, so its CNOT on qubits 0 and 1 is witnessed on `q1, q0`.
-/

#eval algoRot.gates.map (·.ions)
#eval algoRot.ops.map fun o => o.qubits.map (ionOf algoRot)

/-!
`bellShort` names one ion too few: the CNOT's witness names only `q0`. That ion is in `C0`, so
condition 7 holds; condition 8 does not.

{{figure bellShort at=8}}
-/

def bellShort : Input := { bell with gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0"]⟩] }

#eval failing bellShort

/-!
#### Exercise 7 (★★) `short_rejected`
Prove `bellShort` refused, with `rejects_foreign_ions`. The witness and the op are in their lists
(`simp`; the ops are `bell`'s, so name `bell` too), and `rfl` matches the index. The last
hypothesis says the two lists are
not rearrangements of each other. They have different lengths: a proof `h` of a `Perm` gives
`h.length_eq`, and `simpa using h.length_eq` turns `1 = 2` into the contradiction.
-/

#check @rejects_foreign_ions

theorem short_rejected : check bellShort = false :=
  rejects_foreign_ions (g := ⟨1, 8, "C0", ["q0"]⟩) (o := ⟨1, "cx", [0, 1]⟩)
    (by simp [bellShort]) (by simp [bellShort, bell]) rfl (fun h => by simpa using h.length_eq)

/-!
## 7. Coverage: `Covered` and `Grounded`
-/

#print needsWitness
#print Covered
#print Grounded

/-!
Every operation other than a measurement, reset or barrier has exactly one witness (`Covered`),
and every witness names an operation the circuit has (`Grounded`). Without the second, a compiler
could bury extra gates under indices no operation carries.

`bellDropped` loses the Hadamard's witness; the figure's first row is the op with none.
`bellInvented` adds a witness for an op 7 the circuit does not have; each breaks one condition.

{{figure bellDropped}}
-/

def bellDropped : Input := { bell with gates := [⟨1, 8, "C0", ["q0", "q1"]⟩] }
def bellInvented : Input := { bell with gates := bell.gates ++ [⟨7, 9, "C0", ["q0"]⟩] }

#eval failing bellDropped
#eval failing bellInvented

/-!
A measurement needs no witness, and `Covered` says nothing about one that has one. So a witness
for the first measurement, with `q0` in a trap at a later cycle than the CNOT, is accepted:
-/

def bellMeasureW : Input := { bell with gates := bell.gates ++ [⟨2, 9, "C0", ["q0"]⟩] }

#eval check bellMeasureW

/-!
That is not a hole in the proof: the specification never claimed more. What ties a witness to an
instruction of the right kind (a measurement witness to a measurement of its ion) is a computed
check outside Lean, listed in lesson F6.

#### Exercise 8 (★★) `coverage_both_ways`
Prove both refused: `bellDropped` with `rejects_dropped_gate` (the Hadamard op is in the list, it
needs a witness, and the number of its witnesses is not 1), and `bellInvented` with
`rejects_invented_gate`. Memberships by `simp`, the rest by computation.
-/

#check @rejects_dropped_gate
#check @rejects_invented_gate

theorem coverage_both_ways : check bellDropped = false ∧ check bellInvented = false :=
  ⟨rejects_dropped_gate (o := ⟨0, "h", [0]⟩) (by simp [bellDropped, bell]) (by decide +kernel) (by decide +kernel),
   rejects_invented_gate (g := ⟨7, 9, "C0", ["q0"]⟩) (by simp [bellInvented]) (by decide +kernel)⟩

/-!
## 8. Order: `RespectsOrder`, strictly
-/

#print RespectsOrder
#print witnessQubits

/-!
`witnessQubits` pairs each witness with its op's index and qubits, looked up in the circuit's own
op list, so the order the checker enforces is derived from the circuit and never supplied by the
compiler. Condition 11 then says: if op `a` comes before op `b` in the circuit and they share a
qubit, `a`'s witness is at a **strictly** earlier cycle than `b`'s. There is no exemption.

`bellReordered` puts the Hadamard at cycle 9, after the CNOT at cycle 8. `q0` is in `C0` by then,
so the witness holds; only the order fails. Put both at cycle 8 and it still fails: "strictly"
means two ops on one qubit never share a cycle.
-/

def bellReordered : Input := { bell with gates := [⟨0, 9, "C0", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩] }
def bellSameCycle : Input := { bell with gates := [⟨0, 8, "C0", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩] }

#eval failing bellReordered
#eval failing bellSameCycle

/-!
Now a reorder nobody could see in the result. `fan` is a three-qubit circuit, `cx q[0],q[1]` then
`cx q[0],q[2]`, on `chain4`: `q0` waits in `C1`, `q1` comes in from `C0`, has its CNOT and goes
back, then `q2` comes in from `C2`. `fanSwapped` serves `q2` first. Both CNOTs read the same
control and write different targets, so in either order they make the same unitary (the repository
proves it: `cxAct_comm_same_control` in the module `QCCDC.Cert.Commute`, lesson F6). Every move of
`fanSwapped` is a
hop from where its ion is, every witness has its own ions together in a trap. Only the order fails.

{{figure fanSwapped at=2}}
-/

def fan : Input :=
  { circuit := "fan", arch := "chain4", nQubits := 3,
    qmap := [(0, "q0"), (1, "q1"), (2, "q2")],
    init := [("q0", "C1"), ("q1", "C0"), ("q2", "C2")],
    ops := [⟨0, "cx", [0, 1]⟩, ⟨1, "cx", [0, 2]⟩],
    moves := [⟨1, "q1", "C0", "C1"⟩, ⟨3, "q1", "C1", "C0"⟩, ⟨4, "q2", "C2", "C1"⟩],
    rots := [],
    gates := [⟨0, 2, "C1", ["q0", "q1"]⟩, ⟨1, 5, "C1", ["q0", "q2"]⟩],
    unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"],
    hops := [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")],
    loops := [] }

def fanSwapped : Input :=
  { fan with moves := [⟨1, "q2", "C2", "C1"⟩, ⟨3, "q2", "C1", "C2"⟩, ⟨4, "q1", "C0", "C1"⟩],
             gates := [⟨0, 5, "C1", ["q0", "q1"]⟩, ⟨1, 2, "C1", ["q0", "q2"]⟩] }

#eval (check fan, failing fanSwapped)

/-!
Until 2026-10-19 the rule let exactly this through: two CNOTs sharing a control, or sharing a
target. That day it became strict, and the comment on `RespectsOrder` gives the reasons. Swapping
two commuting
CNOTs leaves the unitary unchanged, so the half of the verdict that compares the emitted pulses
with the circuit cannot see the swap at all; this condition is the only place it can be caught.
And the order can matter where the unitary cannot show it: in a syndrome round, the order of an
ancilla's CNOTs decides which data qubits one fault on that ancilla spreads to, which is a choice
the circuit's author makes.

#### Exercise 9 (★★) `commuting_reorder_rejected`
Prove `fanSwapped` refused, with `rejects_reordering`. Its two witnesses, joined with their ops'
qubits, are `⟨0, 5, [0, 1]⟩` and `⟨1, 2, [0, 2]⟩` (op index, cycle, qubits).
-/

#check @rejects_reordering
#eval witnessQubits fanSwapped

theorem commuting_reorder_rejected : check fanSwapped = false :=
  rejects_reordering (a := ⟨0, 5, [0, 1]⟩) (b := ⟨1, 2, [0, 2]⟩)
    (by decide +kernel) (by decide +kernel) (by decide) (by decide +kernel) (by decide)

/-!
#### Exercise 10 (★★★) `order_separates`
Prove, for every certificate that respects the order: two witnesses of **different** ops that
share a qubit are never in the same cycle, whichever comes first in the circuit.

Hints: `RespectsOrder inp` is a `let` around a `∀`; restate it as the plain `∀` with `have h' :
... := h`. `Nat.lt_or_gt_of_ne` splits the two orders. For the second, you need the sharing the
other way round: `simp only [List.any_eq_true] at hsh ⊢` turns it into "some `q` in one list is in
the other", which you can turn around.
-/

theorem order_separates {inp : Input} (h : RespectsOrder inp) {a b : WQ}
    (ha : a ∈ witnessQubits inp) (hb : b ∈ witnessQubits inp) (hne : a.idx ≠ b.idx)
    (hsh : (a.qubits.any fun q => b.qubits.contains q) = true) : a.t ≠ b.t := by
  have h' : ∀ a ∈ witnessQubits inp, ∀ b ∈ witnessQubits inp, a.idx < b.idx →
      (a.qubits.any fun q => b.qubits.contains q) = true → a.t < b.t := h
  rcases Nat.lt_or_gt_of_ne hne with hlt | hgt
  · exact Nat.ne_of_lt (h' a ha b hb hlt hsh)
  · have hsh' : (b.qubits.any fun q => a.qubits.contains q) = true := by
      simp only [List.any_eq_true] at hsh ⊢
      obtain ⟨q, h1, h2⟩ := hsh
      exact ⟨q, by simpa using h2, by simpa using h1⟩
    exact Nat.ne_of_gt (h' b hb a ha hgt hsh')

/-!
## 9. The eleven, each alone

| condition | broken alone by | refused with |
|---|---|---|
| 1 `unrealised = []` | `bellUnrealised` | `rejects_unrealised` |
| 2 `MapInjective` | `bellAliased` | `rejects_aliased_qubits` |
| 3 `MapTotal` | `bellPartial` | F1's `rejects_partial_map` |
| 4 `EventsSorted` | `bellUnsorted` | (lesson F4) |
| 5 `RotationsKnown` | `rotUnknown` | `rejects_unknown_loop` |
| 6 `MovesAreHops` | F1's teleport, `bellDeparture` | `rejects_teleport`, `rejects_wrong_departure` |
| 7 `GatesLegal` | F1's late move, `ringH` | `rejects_not_colocated`, `rejects_ungateable_site` |
| 8 `OwnIons` | `bellShort`, F1's foreign ions | `rejects_foreign_ions` |
| 9 `Covered` | `bellDropped` | `rejects_dropped_gate` |
| 10 `Grounded` | `bellInvented` | `rejects_invented_gate` |
| 11 `RespectsOrder` | `bellReordered`, `fanSwapped` | `rejects_reordering` |

Each of them is needed: remove any one condition and the certificate in its row is accepted.
Lesson F3 opens the decision procedures that compute all this, and lesson F4 the proofs that
connect them to the specification.
-/
