import QCCDC.Cert.Sound
import QCCDC.Cert.Commute

/-!
# What you trust

*Part F, lesson F6. You need lessons F1 to F5.*

Lesson F1 ended on a warning: a proof is exactly as strong as what it is about. `check_sound` is
about a Lean value of type `Input`. The verdict the site reports, R10, is about files: a certificate,
a programme, a device and a circuit. Between the two sits code that is run and believed, not proved.
This lesson lists all of it, layer by layer, as the code has it today, and wherever Lean can show
something, uses Lean to show what a fault in that layer would let through.

A thing is **trusted** when the verdict is only right if it is right, and nothing checks it. The
point of a proved checker is to make that list short, not empty.
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
def bellTeleport : Input :=
  { bell with init := [("q0", "C3"), ("q1", "C0")], moves := [⟨3, "q0", "C3", "C0"⟩],
              gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩] }
theorem nothing_moves {inp : Input} (hm : inp.moves = []) (hr : inp.rots = []) (t : Nat) :
    posAt inp t = inp.init := by
  simp [posAt, replay, events, merge, stateIn, snapshots, finalPos, hm, hr]

/-!
## 1. The kernel, and one axiom
-/

#print axioms check_sound

/-!
`propext` says that two propositions which imply each other are equal. Every rejection lemma rests
on it too, and on nothing else (lesson F4). Under the axiom is Lean's **kernel**, the small program
that checks every proof term; a theorem is exactly as good as the kernel that accepted it.

On this page the kernel is Lean 4.33 compiled to WebAssembly, and the checker it loads is a
mechanical port of the repository's (the note at the foot of the page says what it changes). One
consequence you have met already: the port leaves the definitions of the module `QCCDC.Cert.Syntax`
unexposed, since that module has private helpers, so on this page the kernel cannot look inside them.
That includes the
equality tests Lean derived for `Op`, `Move`, `Rot` and `GateW`, which is why the lessons prove
"this move is in the list" with `simp` rather than `decide +kernel`. It also includes the parser.

## 2. The parser

`qcheck`, the checker as a program, reads a file, parses it as JSON, turns the JSON into an `Input`,
and prints the answer of `check`. `check_sound` is a theorem about `check`; nothing is proved about
the reading. The checker's syntax module has two readers. `Input.ofJson`, the one this page runs,
is lenient: here is how it reads a list of moves (the others are alike): each entry is turned into
a `Move` if it has a natural number `t` and strings `ion`, `from` and `to`, and is **dropped**
otherwise.

```
moves := (arr? (field j "moves")).toList.filterMap moveOf
```

`filterMap` keeps the entries for which `moveOf` gives `some` and silently drops the rest. A
natural number is written without a decimal point: `20` is one, `20.0` is not.

Until 2026-10-20 `qcheck` read every file this way. Since then it reads with the strict one,
`Input.parse`, which refuses a document with a missing field or an entry it cannot read, and says
which; on a well-formed file the two give the same `Input`. This section is why the strict reader
was needed.
-/

#eval (Lean.Json.parse "20").map fun j => j.getNat?.toOption
#eval (Lean.Json.parse "20.0").map fun j => j.getNat?.toOption

/-!
Here is the published Bell input ([bell.qcheck.json](data/bell.qcheck.json)) with one more move:
ion `q1` from `C0` to `C3` at cycle 20,
which is no hop of `chain4`, with its cycle written `20.0`.
-/

def doc : String :=
  "{\"circuit\": \"bell\", \"arch\": \"chain4\", \"n_qubits\": 2, \"map\": {\"0\": \"q0\", \"1\": \"q1\"}, \
   \"init\": {\"q0\": \"C1\", \"q1\": \"C0\"}, \
   \"circuit_ops\": [{\"i\": 0, \"name\": \"h\", \"qubits\": [0]}, {\"i\": 1, \"name\": \"cx\", \"qubits\": [0, 1]}, \
     {\"i\": 2, \"name\": \"measure\", \"qubits\": [0]}, {\"i\": 3, \"name\": \"measure\", \"qubits\": [1]}], \
   \"moves\": [{\"t\": 3, \"ion\": \"q0\", \"from\": \"C1\", \"to\": \"C0\"}, \
     {\"t\": 20.0, \"ion\": \"q1\", \"from\": \"C0\", \"to\": \"C3\"}], \
   \"rotations\": [], \
   \"gates\": [{\"dag\": 0, \"t\": 2, \"site\": \"C1\", \"ions\": [\"q0\"]}, \
     {\"dag\": 1, \"t\": 8, \"site\": \"C0\", \"ions\": [\"q0\", \"q1\"]}], \
   \"unrealised\": [], \"gate_sites\": [\"C0\", \"C1\", \"C2\", \"C3\"], \
   \"hops\": [{\"from\": \"C0\", \"to\": \"C1\"}, {\"from\": \"C1\", \"to\": \"C0\"}, {\"from\": \"C1\", \"to\": \"C2\"}, \
     {\"from\": \"C2\", \"to\": \"C1\"}, {\"from\": \"C2\", \"to\": \"C3\"}, {\"from\": \"C3\", \"to\": \"C2\"}], \
   \"loops\": {}}"

def parsed : Input :=
  match Lean.Json.parse doc with
  | .ok j => Input.ofJson j
  | .error _ => default

#eval parsed.moves
#eval check parsed

/-!
One move, not two, and the checker accepts. We ran `qcheck` on this document saved as a file: as it
was before 2026-10-20 it reported "1 moves" and ACCEPTED; today it says
`REJECTED -- the input is malformed: moves[1] is malformed` and exits 1.

So there are two certificates here: the one the file means, and the one the lenient reader reads.
-/

def bellMeant : Input := { bell with moves := [⟨3, "q0", "C1", "C0"⟩, ⟨20, "q1", "C0", "C3"⟩] }
def bellParsed : Input := { bell with moves := [⟨3, "q0", "C1", "C0"⟩] }

/-!
{{figure bellMeant at=20}}

#### Exercise 1 (★) `parsed_accepted`
Prove that the certificate the lenient reader produces holds.
-/

theorem parsed_accepted : check bellParsed = true := by
  decide +kernel

/-!
#### Exercise 2 (★★) `meant_rejected`
Prove that the certificate the file means is refused, with `rejects_teleport`.
-/

theorem meant_rejected : check bellMeant = false :=
  rejects_teleport (m := ⟨20, "q1", "C0", "C3"⟩) (by simp [bellMeant]) (by decide +kernel)

/-!
Is `bellParsed` really what the parser produced? Lean can compute that it is:
-/

#eval reprStr parsed == reprStr bellParsed

/-!
To state it as a theorem on this page, Lean would have to look inside `Input.ofJson`, which it
cannot here. `native_decide` gets a theorem anyway, by running the compiled parser, and the axiom
list says so:
-/

theorem parsed_is_bellParsed : reprStr parsed = reprStr bellParsed := by native_decide

#print axioms parsed_is_bellParsed

/-!
That is the trust in one line: the link from the file to the value `check_sound` speaks about is
the compiled reader, believed. The strict reader narrows what that link can get wrong; nothing
proves it right.

What else catches such a file is outside Lean. The semantic check (`check_cert`) reads the same certificate
in Python, and before anything else checks its shape: every field it reads, with its type. On this
one it says

```
  malformed: move 1 has t = 20.0, not a cycle number
  -> R10 FAILED: the certificate is malformed: move 1 has t = 20.0, not a cycle number
```

(Until 2026-10-20 it had no such check, and a cycle of `20.0` stopped its replay with a Python
`TypeError`; the official grader counts a semantic check that produced no verdict as
`internal_error`, which is never a pass either.) With the cycle written `20`, the proved checker
refuses the teleport itself, and the semantic check adds that `q1` makes no moves in the programme
and one in the certificate. Both outcomes we ran.

Under the lenient reader a field that is missing altogether reads as an empty list. For most fields
that fails safe: an empty `gates` leaves the circuit's ops unwitnessed. Not for all. An empty JSON
object `{}` reads as the certificate of lesson F4's last exercise, with nothing in it, and is
accepted; until 2026-10-20 `qcheck` printed "qcheck ? on ?" and ACCEPTED for it. Today it says
`REJECTED -- the input is malformed: the field "circuit" is missing`, as we ran.
-/

#eval check (Input.ofJson (Lean.Json.mkObj []))

/-!
Even before, `{}` was refused outside Lean: an input that was not rebuilt from its certificate is
refused by the binding of section 3, and a certificate with no circuit by the binding of section 5,
which compares the certificate's circuit with the source circuit, op by op.

What a drop by the lenient reader does depends on what is dropped. A move dropped from a certificate the checker would
refuse can make it accepted, as above. A dropped move can also leave a correct certificate correct:
a last move of an ion that no later witness needs is not missed by anything the checker reads.
A dropped ion is different. A witness's `ions` are read with a `filterMap` too, and a correct
witness that loses an ion is always refused: the next exercise proves it for every certificate.

#### Exercise 3 (★★) `stranded`
First the extreme case of dropped moves: none survive. Prove that then any witness naming an ion
that did not start in its trap is refused. F1's `nothing_moves` (restated here, out of sight) says
the replay is `init` at every cycle; rewrite with it (`rw [nothing_moves hm hr]`) inside
`rejects_not_colocated`.
-/

theorem stranded {inp : Input} (hm : inp.moves = []) (hr : inp.rots = []) {g : GateW}
    (hg : g ∈ inp.gates) {i : String} (hi : i ∈ g.ions) (h : look inp.init i ≠ some g.site) :
    check inp = false :=
  rejects_not_colocated hg hi (by rw [nothing_moves hm hr]; exact h)

/-!
#### Exercise 4 (★★★) `dropped_ion_refused`
Prove that if the checker accepts a certificate whose first witness is `g`, then the same
certificate with one ion `i` of `g` erased from it is refused.

Hints: from `check_sound hd`, condition 10 (`Grounded`) gives an op `o` with `o.idx = g.dag`
(`simp only [List.any_eq_true]` turns the `any` into an `∃`), and condition 8 (`OwnIons`) gives the
rearrangement `hp` for `g`. Then refuse with `rejects_foreign_ions`, naming the new certificate
`(inp := ...)`. Lengths finish it: `hp.length_eq`, the same for the new witness,
`List.length_erase_of_mem hi`, `List.length_pos_of_mem hi`, `simp only [List.length_map]`, `omega`.
-/

theorem dropped_ion_refused {inp : Input} {g : GateW} {rest : List GateW} {i : String}
    (hg : inp.gates = g :: rest) (hi : i ∈ g.ions) (hd : check inp = true) :
    check { inp with gates := { g with ions := g.ions.erase i } :: rest } = false := by
  have himp := check_sound hd
  have hgr : (inp.ops.any fun o => o.idx = g.dag) = true := himp.2.2.2.2.2.2.2.2.2.1 g (by simp [hg])
  simp only [List.any_eq_true] at hgr
  obtain ⟨o, ho, hidx⟩ := hgr
  have hidx' : o.idx = g.dag := by simpa using hidx
  have hp := himp.2.2.2.2.2.2.2.1 g (by simp [hg]) o ho hidx'
  refine rejects_foreign_ions (inp := { inp with gates := { g with ions := g.ions.erase i } :: rest })
    (g := { g with ions := g.ions.erase i }) (o := o) (by simp) ho hidx' fun hp' => ?_
  have h1 := hp.length_eq
  have h2 := hp'.length_eq
  have hpos : 0 < g.ions.length := List.length_pos_of_mem hi
  have h3 : (g.ions.erase i).length = g.ions.length - 1 := List.length_erase_of_mem hi
  simp only [List.length_map] at h1 h2
  omega

/-!
## 3. Is this input this certificate's?

`qcheck` decides whatever file it is given. A verdict reported for a certificate must be about that
certificate on that device. The semantic check makes sure with `qcheck_binding`: it rebuilds the input
from the certificate and the device, with the same function that made it, and refuses a file that
differs. Lesson F5 ran it: an input whose witness differed from the certificate's was refused, "not
this certificate's input", and R10 failed. The official grader does not need the comparison: its
Lean stage builds the input itself, from the submitted certificate and the submitted device, and
runs `qcheck` on that.

## 4. The device facts

`gateSites`, `hops` and `loops` are not the compiler's. The device facts' builder
(`mk_qcheck_input`) reads them out of the device's document: the gate sites are the nodes whose zone
can gate; the
loops are the device's closed loops, in their node order; the hops are computed by `trap_hops`, a
search that leaves a site, crosses only junctions (a junction node, or a site where three or more
rails meet), at most three of them, and lands on a site. On `ring6d`, `S0` has three rails, so
`("S1", "A0")` is a hop: an ion may cross `S0` into the dock. This is Python, run and believed.

The checker is exactly as right as these facts. Here is F1's teleport judged against a device
whose hop list has one entry too many:
-/

def bellTeleportWide : Input := { bellTeleport with hops := bellTeleport.hops ++ [("C3", "C0")] }

/-!
{{figure bellTeleportWide at=3}}

Lean accepts it, and the row says the move is "a hop of the device": the row reads the same hop
list. The figure trusts it too. It draws every move that the hop list admits along the device's
rails, so it walks `q0` from `C3` through `C2` and `C1` to `C0` in one step, and the studio plays
that without complaint. Only the hop list claimed that crossing two traps is one hop; nothing on
this row can tell. (In lesson F1 the same move, against the true hop list, was drawn straight and
refused by the studio: no segment between `C3` and `C0`.)

#### Exercise 5 (★★) `device_decides`
Prove both: the same certificate is refused on the true device and accepted on the widened one.
-/

theorem device_decides : check bellTeleport = false ∧ check bellTeleportWide = true :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
Which way can a wrong hop list fail? The builder's own documentation argues that if its search and the
compiler's idea of the device disagree, the checker sees moves that are not in its hop set and
refuses: the safe direction. The next exercise proves the other half of that argument: extra hops
never turn an acceptance into a refusal. So a missing hop can only cost a refusal; an extra hop is
the dangerous mistake.

#### Exercise 6 (★★★) `more_hops_still_accepted`
Prove that if the checker accepts a certificate, it accepts it with any extra hops appended.

Hints: take the eleven conditions apart, `obtain ⟨h1, h2, ..., h11⟩ := check_sound hd`. Only
condition 6 mentions `hops`; the other ten carry over unchanged. Restate `h6` without its `let`
(`have h6' : ∀ m ∈ inp.moves, (m.src, m.dst) ∈ inp.hops ∧ look (posAt inp m.t) m.ion = some m.src
:= h6`), and use `List.mem_append_left`.
-/

theorem more_hops_still_accepted {inp : Input} (hd : check inp = true)
    (extra : List (String × String)) : check { inp with hops := inp.hops ++ extra } = true := by
  obtain ⟨h1, h2, h3, h4, h5, h6, h7, h8, h9, h10, h11⟩ := check_sound hd
  have h6' : ∀ m ∈ inp.moves, (m.src, m.dst) ∈ inp.hops ∧ look (posAt inp m.t) m.ion = some m.src := h6
  apply check_complete
  exact ⟨h1, h2, h3, h4, h5, fun m hm => ⟨List.mem_append_left _ (h6' m hm).1, (h6' m hm).2⟩,
    h7, h8, h9, h10, h11⟩

/-!
## 5. The semantic half: computed, not proved

`Implements` names no gate, and the checker never sees the programme. Everything that ties the
certificate to the programme and to the source circuit is the semantic check (`check_cert`), which
is Python, computed and believed. In the order it runs them:

* **shape**: every field of the certificate it reads is there, with its type (a cycle is a whole
  number); a malformed certificate fails R10 here, by name;
* **binding** (`check_binding`): the certificate's `circuit_ops` are the source circuit's ops, read
  from the QASM, op by op: the same gate (the same name and angles, or the same matrix) on the same
  qubits, and a gate on three or more qubits, such as `ccx`, as a run of ops whose product is its
  defining unitary; the qubit count is the circuit's, and the map sends each qubit to its own ion;
* **transport** (`check_program_transport`): the programme moves each ion exactly as the
  certificate says, in the same order, and turns the same loops by the same amounts;
* **its own replay** (`check_o1`): the certificate's moves and rotations replayed in Python, each
  witness's ions together in a trap that can gate;
* **witnesses** (`check_witnesses`): each witness names its op's ions and an instruction of the
  programme of its op's kind, a gate pulse on at least one of those ions, or a measurement or reset
  of its ion; no witness names a barrier;
* **order** (`check_program_order`): in the programme, every qubit's ops run in the circuit's order,
  strictly, commuting or not;
* **readout** (`readout_plan`): measurements and resets, qubit by qubit, compared in a deferred form
  on both sides;
* **semantics**: the programme's native pulses (`R`, `VZ`, `MS`) composed into a stabilizer tableau
  with the library stim, compared with the tableau of the circuit read by qiskit; outside the
  Clifford gates, exact unitaries, for at most ten qubits by default (counting one for each
  measurement or reset that has to be deferred).

R10 is "passed" only when the proved checker accepts and every one of these holds. The official
grader runs them as stages of its own, in this order: `bundle`, `device`, `physics_lock`,
`program`, `rules`, `correspondence`, then `certificate_binding` (the binding),
`lean_certificate` (`qcheck`, on an input it builds itself) and `semantics` (the semantic check),
then, on a memory board only, `ler`, and last `metrics`.
The grader's documentation lists what each stage establishes, and that no stage that was skipped,
timed out or crashed counts as passed.

Two things Lean can show about this half. First, why it is needed: `Implements` is blind to which
gate an operation is.
-/

def bellCZ : Input :=
  { bell with ops := [⟨0, "h", [0]⟩, ⟨1, "cz", [0, 1]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [1]⟩] }

/-!
{{figure bellCZ at=8}}

#### Exercise 7 (★) `cz_for_cx_accepted`
Prove that the checker accepts the Bell certificate with its CNOT relabelled a CZ.
-/

theorem cz_for_cx_accepted : check bellCZ = true := by
  decide +kernel

/-!
The binding refuses it: the source circuit says `cx`, and a `cz` is another gate. If the label said
`cx` and the pulses made a CZ, the tableau would refuse it. Neither refusal is a theorem.

Second, what the semantic half cannot see: a reorder of two commuting gates. The module
`QCCDC.Cert.Commute` proves
that two CNOTs sharing a control commute, as maps on basis states (`cxAct`), and a permutation
matrix is determined by its map, so the unitary is the same either way. A comparison of tableaux or
unitaries cannot tell the two orders apart; only the order checks can, the proved `RespectsOrder`
and the computed `check_program_order`.

#### Exercise 8 (★★) `commuting_invisible`
Prove that `cx q[0],q[1]` then `cx q[0],q[2]` acts on every basis state as the two in the other
order: F2's `fan` and `fanSwapped`. Use `cxAct_comm_same_control`; its three side conditions are
facts about small numbers.
-/

#check @cxAct_comm_same_control

theorem commuting_invisible (s : Nat → Bool) :
    cxAct 0 1 (cxAct 0 2 s) = cxAct 0 2 (cxAct 0 1 s) :=
  cxAct_comm_same_control (by decide) (by decide) (by decide) s

/-!
## 6. The list

| layer | what it is | proved or believed |
|---|---|---|
| Lean's kernel, `propext` | checks every proof | believed |
| `check_sound`, the rejection lemmas | `check` says yes exactly when `Implements` holds | proved |
| `qcheck` | `check`, compiled by Lean's compiler; the published binary pinned by its SHA-256 | believed |
| `Input.parse` (`qcheck`'s reader) | the file as an `Input`; a missing field or an unreadable entry refused by name | believed |
| `Input.ofJson` (this page's reader) | the same `Input` from a well-formed file; malformed entries dropped, missing fields empty | believed |
| `qcheck_binding`, or the grader's own build of the input | the input is this certificate's, on this device | computed |
| the device facts' builder (`mk_qcheck_input`, `trap_hops`) | gate sites, hops, loops, read from the device | computed |
| the semantic check (`check_cert`) | shape, binding, transport, replay, witnesses, order, readout, tableau or unitary | computed |
| the rules (Part E) | the programme is physically legal on the device | computed |

The second row is the only proved one, and it is the one that would be hardest to get right by
testing: every certificate there will ever be, every way of being wrong. The rest are short programs
that read files, and each is checked against the others: the binding against the circuit, transport
against the programme, the Python replay against the Lean one. What this tutorial calls the proved
checker is that second row, and this lesson is the honest account of the rest.
-/
