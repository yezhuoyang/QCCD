import QCCDC.Cert.Sound

/-!
# The certificate format

*Part I, lesson I4. You need lessons I1 to I3.*

Lesson I3 was about what the proved checker reads. A compiler that wants its programmes judged
the way the platform judges its own (`qccdc`'s) needs more: rule R10, "the compiled program
implements the input circuit", passes only when the proved checker accepts the certificate *and*
the computed checks of the semantic check (`check_cert`) accept the certificate, the programme
and the circuit together. This lesson is that contract, field by field, for a compiler written by
anyone: what each file holds, who reads each field, and which parts the Lean checker formalises.
The exercises are on those parts, and on what passes them and is refused elsewhere.
-/

open QCCDC.Cert Lean

/-!
## 1. Four documents

| document | written by | what it is |
|---|---|---|
| `prog.tsir.json` | the compiler | the programme: instructions in the device's native gates (section 5) |
| `prog.qcert.json` | the compiler | the certificate (section 2) |
| `circuit.qasm` | the user | OpenQASM 2, the source of truth for the semantics |
| the device, as `chain4.arch.json` | the designer | its traps, rails, junctions and loops |

and two that are derived, never written by the compiler: the device's expanded form (its
exporter, `export_arch`) and the checker's input, `prog.qcheck.json` (the device facts' builder,
`mk_qcheck_input`, section 3). The whole verdict is one run of the semantic check (`check_cert`)
on these: it says R10 `passed`, `partial` or `FAILED`, with the reason (section 6). The official
grader runs it for every design it grades.

## 2. The certificate, field by field

`qccdc` writes the certificate with its certificate writer, `Cert.to_json`. Here is the Bell
pair's, as published beside F1's figure ([bell.qcert.json](data/bell.qcert.json)), laid out:

```
{"version": 1, "circuit": "circuit", "arch": "chain4",
 "circuit_fnv1a": "de8a5049a41c4f7a", "arch_fnv1a": "", "n_qubits": 2,
 "circuit_ops": [{"i": 0, "name": "h", "qubits": [0], "params": [], "line": 5},
                 {"i": 1, "name": "cx", "qubits": [0, 1], "params": [], "line": 6},
                 {"i": 2, "name": "measure", "qubits": [0], "params": [], "line": 7},
                 {"i": 3, "name": "measure", "qubits": [1], "params": [], "line": 7}],
 "map": {"0": "q0", "1": "q1"}, "init": {"q0": "C1", "q1": "C0"},
 "moves": [{"t": 3, "ion": "q0", "from": "C1", "to": "C0", "via": ["E0"]}],
 "gates": [{"dag": 0, "instr": 3, "t": 2, "site": "C1", "ions": ["q0"],
            "pulses": ["R(1.5708,-1.5708)@q0", "VZ(3.14159)@q0"]},
           {"dag": 1, "instr": 9, "t": 8, "site": "C0", "ions": ["q0", "q1"],
            "pulses": ["R(1.5708,1.5708)@q0", "MS(1.5708)@q0,q1", "R(-1.5708,0)@q0",
                       "R(-1.5708,0)@q1", "R(-1.5708,1.5708)@q0"]}],
 "rotations": [], "unrealised": [],
 "claims": {"cycles": 10, "beams": 5, "ms_gates": 1, "frames": 1}}
```

| field | what it holds | read by |
|---|---|---|
| `version` | `1` | none of the checks |
| `circuit`, `arch` | names | reports only (I3, Exercise 5) |
| `circuit_fnv1a`, `arch_fnv1a` | a 64-bit FNV-1a hash of each file, which the writer itself says is not cryptographic | none of the checks: the semantic check takes the QASM's SHA-256 itself |
| `n_qubits` | the circuit's qubit count | the Lean checker (`MapTotal`); the binding |
| `circuit_ops` | the circuit, op by op: `i`, `name`, `qubits`, `params` (angles), `line` (in the QASM) | the Lean checker (all but `params`, `line`); the binding (with `params`) |
| `map` | qubit number (as a string) to ion | the Lean checker; the binding; the semantics |
| `init` | ion to its first trap | the Lean checker (the replay) |
| `moves` | `t`, `ion`, `from`, `to`, and `via`, the rails crossed | the Lean checker (all but `via`); transport (with `via`) |
| `rotations` | `t`, `loop`, `delta` | the Lean checker; transport |
| `gates` | the witnesses: `dag`, `t`, `site`, `ions`; `instr`, the programme instruction that completes the op; `pulses`, its native sequence | the Lean checker (`dag`, `t`, `site`, `ions`); witnesses and order (`instr`) |
| `unrealised` | ops the compiler gave up on | the Lean checker; R10 |
| `claims` | the compiler's own totals | none of the checks |

A cycle `t` is the compiler's own clock, nothing more: it orders events and puts each witness
between the moves around it. It is not an instruction number (the semantic check says so where it
compares transport).

**Shape first.** Before reading anything, the semantic check checks the certificate's shape
(`certificate_shape`): `n_qubits` a whole number; `map` an object; `moves`, `gates` and
`circuit_ops` lists; every move's `t` a whole number and its `ion`, `from`, `to` names; every
witness's `t` and `dag` whole numbers, `instr` (if present) an instruction id, `ions` a list,
`site` a name; every rotation's `t` and `delta` whole numbers. A certificate that fails is refused,
R10 `FAILED`, with the field named. `qcheck` reads as strictly since 2026-10-20 (its reader
`Input.parse` refuses a missing field or an entry it cannot read, by name). The checker's other
reader, `Input.ofJson`, the one this page runs, is forgiving: what it cannot read, it drops.
-/

#eval (Input.ofJson (json% {"moves": [{"t": 20.0, "ion": "q0", "from": "C0", "to": "C1"},
                                     {"t": 21, "ion": "q0", "from": "C1", "to": "C2"}]})).moves

/-!
The move stamped `20.0` is gone, and the Lean checker would judge a certificate with one move
fewer (and refuse it here: the second move departs from `C1`, where the ion never arrived).

## 3. What the device adds

The device facts' builder (`mk_qcheck_input`, its function `build`) copies the certificate's
fields and adds three facts, each read out of the device's expanded document, never out of the
certificate:

* `gate_sites`: the traps whose zone can gate, sorted;
* `hops`: every ordered pair of traps one machine cycle apart. An ion never rests on a junction,
  so a hop leaves a trap, crosses only junctions, and lands on a trap. A junction is what rule R18
  says it is: a bare junction node, or a site where three or more rails meet. At most three
  junctions are crossed in one hop;
* `loops`: the node order of every *closed* loop, for replaying rotations. An open path, like
  `chain4`'s `P0`, is not a loop here.

On `chain4` these are the lists of I1. On `grid2x3`, traps sit between junctions, and a hop
crosses one: `T0_0h` to `T0_0v` is one hop, through the junction `J0_0`. Here are its facts, as
`build` gives them (computed by running it on the device's expanded document,
`grid2x3.expanded.json`), and a CNOT across the junction.
-/

def gridSites : List String := ["T0_0h", "T0_0v", "T0_1h", "T0_1v", "T0_2h", "T1_0v", "T1_1v"]

def gridHops : List (String × String) :=
  [("T0_0h", "T0_0v"), ("T0_0h", "T1_0v"), ("T0_1h", "T0_0v"), ("T0_1h", "T0_1v"),
   ("T0_1h", "T1_0v"), ("T0_1h", "T1_1v"), ("T0_2h", "T0_1v"), ("T0_2h", "T1_1v"),
   ("T0_0v", "T0_0h"), ("T0_0v", "T0_1h"), ("T0_0v", "T0_1v"), ("T0_1v", "T0_1h"),
   ("T0_1v", "T0_0v"), ("T0_1v", "T0_2h"), ("T1_0v", "T0_0h"), ("T1_0v", "T0_1h"),
   ("T1_0v", "T1_1v"), ("T1_1v", "T0_1h"), ("T1_1v", "T1_0v"), ("T1_1v", "T0_2h")]

def gridCert : Input :=
  { circuit := "cx_across", arch := "grid2x3", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")], init := [("q0", "T0_0h"), ("q1", "T0_0v")],
    ops := [⟨0, "cx", [0, 1]⟩, ⟨1, "measure", [0]⟩, ⟨2, "measure", [1]⟩],
    moves := [⟨0, "q0", "T0_0h", "T0_0v"⟩], rots := [],
    gates := [⟨0, 1, "T0_0v", ["q0", "q1"]⟩], unrealised := [],
    gateSites := gridSites, hops := gridHops, loops := [] }

/-!
The studio walks `q0` through the junction in one cycle.

{{figure gridCert}}

#### Exercise 1 (★) `grid_holds`
Prove that the certificate holds: its one move is a hop, though no rail joins its two traps.
-/

theorem grid_holds : Implements gridCert :=
  check_sound (by decide +kernel)

/-!
#### Exercise 2 (★) `hops_land_on_traps`
Prove that every hop of `grid2x3` starts and ends at one of its traps, never at a junction.
-/

theorem hops_land_on_traps : ∀ h ∈ gridHops, h.1 ∈ gridSites ∧ h.2 ∈ gridSites := by
  decide +kernel

/-!
Why must the device, not the compiler, supply these facts? Because they only ever widen what is
accepted: a certificate that holds with some hops holds with more. A compiler allowed to add a
hop could make a teleport legal.

#### Exercise 3 (★★★) `more_hops`
Prove it, for every certificate and every list of extra hops. Take the proof of `Implements inp`
apart into its eleven conditions (`obtain ⟨h1, ..., h11⟩ := h`) and rebuild it for the widened
certificate: ten conditions are the same proposition as before, since they never read `hops`;
for the sixth, a hop of `inp.hops` is a hop of `inp.hops ++ extra` (`List.mem_append`).
-/

theorem more_hops (inp : Input) (extra : List (String × String)) (h : Implements inp) :
    Implements { inp with hops := inp.hops ++ extra } := by
  obtain ⟨h1, h2, h3, h4, h5, h6, h7, h8, h9, h10, h11⟩ := h
  exact ⟨h1, h2, h3, h4, h5,
    fun m hm => ⟨List.mem_append.2 (Or.inl (h6 m hm).1), (h6 m hm).2⟩, h7, h8, h9, h10, h11⟩

/-!
## 4. Loops and rotations

On a closed loop the hardware can turn every ion on it at once: a **rotation**, one witness
`{"t", "loop", "delta"}` rather than a move per ion. The replay advances every ion standing on
the loop by `delta` slots, in the loop's node order from the device, and leaves every other ion
where it is: an ion in a dock is off the loop, and the loop turns underneath it (`rotateState`
in `QCCDC.Cert.Check`).

`ring6d` is a loop `L0 = S0 ... S5`, whose sites cannot gate, with docks `A0` off `S0` and `A3`
off `S3`, which can. `S0` and `S3` are sites where three rails meet, so hops cross them: `S1` to
`A0` is one hop. The Compilation page's parity check on `ring6d` (`algo_rot`) is compiled by
rotation: the ancilla `q0` waits in the dock `A0`, the data ride the loop, and each comes into the
dock for its CNOT. Its checker input, as a value:
-/

def ringHops : List (String × String) :=
  [("S0", "S1"), ("S0", "S5"), ("S0", "A0"), ("S1", "S0"), ("S1", "S5"), ("S1", "A0"),
   ("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("S2", "S4"), ("S2", "A3"), ("S3", "S2"),
   ("S3", "S4"), ("S3", "A3"), ("S4", "S3"), ("S4", "S2"), ("S4", "A3"), ("S4", "S5"),
   ("S5", "S4"), ("S5", "S0"), ("S5", "S1"), ("S5", "A0"), ("A0", "S0"), ("A0", "S1"),
   ("A0", "S5"), ("A3", "S3"), ("A3", "S2"), ("A3", "S4")]

def rotIn : Input :=
  { circuit := "circuit", arch := "ring6d", nQubits := 4,
    qmap := [(0, "q0"), (1, "q1"), (2, "q2"), (3, "q3")],
    init := [("q0", "A0"), ("q1", "S0"), ("q2", "S2"), ("q3", "S4")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "cx", [0, 2]⟩, ⟨3, "cx", [0, 3]⟩,
            ⟨4, "h", [0]⟩, ⟨5, "measure", [0]⟩],
    moves := [⟨3, "q1", "S0", "A0"⟩, ⟨9, "q1", "A0", "S0"⟩, ⟨12, "q2", "S0", "A0"⟩,
              ⟨18, "q2", "A0", "S0"⟩, ⟨21, "q3", "S0", "A0"⟩, ⟨27, "q3", "A0", "S0"⟩],
    rots := [⟨10, "L0", -2⟩, ⟨19, "L0", -2⟩],
    gates := [⟨0, 2, "A0", ["q0"]⟩, ⟨1, 8, "A0", ["q1", "q0"]⟩, ⟨2, 17, "A0", ["q2", "q0"]⟩,
              ⟨3, 26, "A0", ["q3", "q0"]⟩, ⟨4, 29, "A0", ["q0"]⟩],
    unrealised := [], gateSites := ["A0", "A3"], hops := ringHops,
    loops := [("L0", ["S0", "S1", "S2", "S3", "S4", "S5"])] }

/-!
The live figures of this tutorial play moves and gates but not rotations, so this one is a studio
programme made from the certificate in Lean: every witness, move and rotation in cycle order (a
witness first within its cycle, since it sees the cycle's start; then moves; then turns), a
cooling step before each two-qubit gate, and the measurements last. `studioOf` works for any
certificate whose moves join neighbouring traps.
-/

structure Studio where
  arch : String
  src : String

def inQuotes (s : String) : String := "\"" ++ s ++ "\""

def opName (inp : Input) (i : Nat) : String :=
  ((inp.ops.find? (fun o => o.idx = i)).map Op.name).getD "?"

def witnessStmt (inp : Input) (g : GateW) : String :=
  match g.ions with
  | [a, b] => "p.cool()\np.gate(" ++ inQuotes (opName inp g.dag).toUpper ++ ", [[" ++ inQuotes a ++
      ", " ++ inQuotes b ++ "]])"
  | _ => "p.gate(" ++ inQuotes (opName inp g.dag).toUpper ++ ", [], [" ++ inQuotes g.site ++ "])"

def stmts (inp : Input) : List (Nat × Nat × String) :=
  inp.gates.map (fun g => (g.t, 0, witnessStmt inp g)) ++
  inp.moves.map (fun m => (m.t, 1, "p.shuttle(" ++ inQuotes m.ion ++ ", [" ++ inQuotes m.src ++ ", " ++
    inQuotes m.dst ++ "])")) ++
  inp.rots.map (fun r => (r.t, 2, "p.rotate(" ++ toString r.delta ++ ", loop=" ++ inQuotes r.loop ++ ")"))

def insertBy (x : Nat × Nat × String) : List (Nat × Nat × String) → List (Nat × Nat × String)
  | [] => [x]
  | y :: ys => if x.1 < y.1 || (x.1 == y.1 && x.2.1 < y.2.1) then x :: y :: ys else y :: insertBy x ys

def measured (inp : Input) : List String :=
  (inp.ops.filter (fun o => o.name == "measure")).flatMap fun o =>
    o.qubits.filterMap fun q => (inp.qmap.find? (fun p => p.1 == q)).map Prod.snd

def studioOf (inp : Input) : Studio :=
  ⟨inp.arch, "\n".intercalate
    (("p.init({" ++ ", ".intercalate (inp.init.map fun p => inQuotes p.1 ++ ": " ++ inQuotes p.2) ++ "})") ::
     ((stmts inp).foldr insertBy []).map (fun e => e.2.2) ++
     (match measured inp with
      | [] => []
      | ms => ["p.measure([" ++ ", ".intercalate (ms.map inQuotes) ++ "])"]))⟩

def rotStudio : Studio := studioOf rotIn

#eval IO.println rotStudio.src

/-!
{{studio rotStudio}}

Look at the witnesses: the CNOT `cx q[0],q[1]` is witnessed on `["q1", "q0"]`, the ion that rode
in first. `OwnIons` (condition 8) asks for the op's own ions *in any order* (`List.Perm`), because
a witness's ions say where the op ran, not which operand plays which part; the pulses say that,
and the semantic check reads them. The module `QCCDC.Cert.Perm` counts 4 887 of the 21 320 witnesses
in the 116 shipped checker inputs (2026-10-18) naming their two ions in the other order.

#### Exercise 4 (★) `rot_holds`
Prove that the published rotation certificate holds.
-/

theorem rot_holds : Implements rotIn :=
  check_sound (by decide +kernel)

/-!
#### Exercise 5 (★) `loop_turns`
At cycle 10 the loop turns by `-2`. Prove where three ions are at the start of cycle 11: `q2`,
which was in `S2`, is in `S0`, beside the dock; `q1`, back on the loop at `S0` since cycle 9, is
in `S4`; and `q0`, in the dock, has not moved.
-/

theorem loop_turns :
    look (posAt rotIn 11) "q2" = some "S0" ∧ look (posAt rotIn 11) "q1" = some "S4" ∧
      look (posAt rotIn 11) "q0" = some "A0" :=
  ⟨by decide +kernel, by decide +kernel, by decide +kernel⟩

/-!
The loop a rotation names must be one the device declares; otherwise the replay would leave every
ion where it was, and a certificate could claim a rotation that moved nothing.

#### Exercise 6 (★★) `unknown_loop_refused`
A compiler names the first rotation's loop `L1`. Prove it refused, with `rejects_unknown_loop`;
the rotation is the first in the list.
-/

def rotWrongLoop : Input := { rotIn with rots := [⟨10, "L1", -2⟩, ⟨19, "L0", -2⟩] }

#check @rejects_unknown_loop

theorem unknown_loop_refused : check rotWrongLoop = false :=
  rejects_unknown_loop (r := ⟨10, "L1", -2⟩) (List.Mem.head _) (by decide +kernel)

/-!
#### Exercise 7 (★★★) `rotate_off_loop`
Prove, for every loop, every rotation and every state, that an ion whose trap is not on the loop
(`slotOf` finds no slot for it) keeps its entry through the rotation. Unfold `rotateState`; split
on whether the loop is empty (`by_cases hn : (nodes.length : Int) ≤ 0`, then `simp only [hn, ...]`
to take the branch); in the other branch the state is mapped, and `List.mem_map` with `hoff`
shows the entry maps to itself.
-/

theorem rotate_off_loop (nodes : List String) (delta : Int) (st : List (String × String))
    (p : String × String) (hp : p ∈ st) (hoff : slotOf nodes p.2 0 = none) :
    p ∈ rotateState nodes delta st := by
  unfold rotateState
  by_cases hn : (nodes.length : Int) ≤ 0
  · simp only [hn, if_true]; exact hp
  · simp only [hn, if_false]
    exact List.mem_map.2 ⟨p, hp, by simp [hoff]⟩

/-!
## 5. The programme: TSIR

The programme is a TSIR document: a list of instructions, each one concurrent machine cycle,
executed in order.

| type | carries |
|---|---|
| `init` | `placement` (ion to trap), first |
| `simd` | one transport cycle: `class`, and `participants` (`ion`, `from`, `to`, `via`) or a `template` (`loop_shift`, a rotation) |
| `gate` | `gate` (`R`, `VZ` or `MS` for the semantic check), `params`, `ions` or `pairs`, `sites` |
| `measure`, `reset` | `ions` |
| `cool` | `broadcast`, or `ions` |
| `barrier` | nothing |
| `decode` | `ions`: classical, no machine time |

Every instruction may carry `meta`, and one key of it is part of the contract: **`meta.op`**, the
circuit ops the instruction helps perform. The Bell pair's programme, its `prog.tsir.json`:

| id | instruction | `meta.op` |
|---|---|---|
| 0 | `init` `q0` in `C1`, `q1` in `C0` | |
| 1 | `cool`, broadcast | |
| 2 | `R(π/2, -π/2)` on `q0` | `[0]` |
| 3 | `VZ(π)` on `q0` | `[0]` |
| 4 | `simd` shuttle `q0` `C1` to `C0` via `E0` | `[1]` |
| 5 | `R(π/2, π/2)` on `q0` | `[1]` |
| 6 | `MS(π/2)` on the pair `q0, q1` | `[1]` |
| 7, 8, 9 | `R(-π/2, 0)` on `q0`, on `q1`; `R(-π/2, π/2)` on `q0` | `[1]` |
| 10 | `measure` `q0, q1` | `[2, 3]` |

and the certificate's witnesses name `instr: 3` for the Hadamard and `instr: 9` for the CNOT: the
**last** instruction of each op.

## 6. What the semantic check adds

The proved checker sees only the certificate. These are the checks that tie it to the circuit
and to the programme; each is computed, not proved.

**The binding** (`check_binding`). `n_qubits` is the QASM's qubit count; `map` covers qubits
`0 .. n-1`, each with its own ion; `circuit_ops` is numbered `0 .. N-1` in order and *is* the
source circuit, op by op: an op on one or two qubits (and every `measure`, `reset`, `barrier`)
appears as itself, on the same qubits in the same order, with the same name and angles or the same
defining unitary; an op on three or more qubits (a `ccx`) appears as a run of ops on its qubits
whose product is its unitary up to a global phase, which is how a lowering is checked. Then the
checker's input must be exactly the one `build` makes from this certificate and this device
(`qcheck_binding`); any other is refused, so the Lean verdict is about this certificate.

The Lean checker does not ask for `0 .. N-1`: indices are labels to it. A compiler that numbers
everything from 1, ops and witnesses alike, is consistent, and the proved checker accepts it.
-/

def chain4Hops : List (String × String) :=
  [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")]

def bellPub : Input :=
  { circuit := "circuit", arch := "chain4", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")], init := [("q0", "C1"), ("q1", "C0")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [1]⟩],
    moves := [⟨3, "q0", "C1", "C0"⟩], rots := [],
    gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩], unrealised := [],
    gateSites := ["C0", "C1", "C2", "C3"], hops := chain4Hops, loops := [] }

def bellOneBased : Input :=
  { bellPub with
    ops := [⟨1, "h", [0]⟩, ⟨2, "cx", [0, 1]⟩, ⟨3, "measure", [0]⟩, ⟨4, "measure", [1]⟩],
    gates := [⟨1, 2, "C1", ["q0"]⟩, ⟨2, 8, "C0", ["q0", "q1"]⟩] }

/-!
{{figure bellOneBased}}

#### Exercise 8 (★★) `one_based_accepted`
Prove that the proved checker accepts the one-based Bell certificate. The semantic check refuses it:
"circuit_ops is not numbered 0..N-1 in order".
-/

theorem one_based_accepted : Implements bellOneBased :=
  check_sound (by decide +kernel)

/-!
**The witnesses** (`check_witnesses`). Each names exactly its op's ions (as a multiset, like
`OwnIons`), and its `instr` must be an instruction of the programme of the op's kind: a gate pulse
on at least one of those ions for a gate; a `measure` or `reset` of the ion for a measurement or
reset (their witnesses are optional, but if present they must be right). And no witness may name
a `barrier`: a barrier is a fence, which no instruction performs.

The Lean checker cannot see that last rule: a witness names an op, never a gate, and `Covered`
only asks that ops that *need* a witness have one. In compiler releases up to `c7c535380bfb`,
`qccdc` could compile a two-qubit `barrier` as a CNOT (the documentation of `qcheck` and of
`QCCDC.Cert.Check` records it): the proved checker accepted those certificates, and the semantic
comparison refused them. The barrier rule here refuses them as well.
-/

def barrierCert : Input :=
  { bellPub with
    ops := [⟨0, "h", [0]⟩, ⟨1, "barrier", [0, 1]⟩, ⟨2, "cx", [0, 1]⟩,
            ⟨3, "measure", [0]⟩, ⟨4, "measure", [1]⟩],
    gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩, ⟨2, 9, "C0", ["q0", "q1"]⟩] }

/-!
{{figure barrierCert}}

#### Exercise 9 (★★) `barrier_witness_accepted`
Prove that the proved checker accepts a certificate that witnesses a barrier as if it were a
gate.
-/

theorem barrier_witness_accepted : Implements barrierCert :=
  check_sound (by decide +kernel)

/-!
**Order, read from the programme** (`check_program_order`). The Lean checker's `RespectsOrder`
holds the *certificate's* cycles to the circuit's order. This check holds the *programme* to it:
for each qubit, every instruction of an op on that qubit (found through `meta.op`, and through
each witness's `instr`) must come after every instruction of the qubit's previous op. Pulses on
other qubits may overlap; that is parallelism, not reordering. It is strict: two ops on one qubit
keep the circuit's order even when they commute, because the semantic check cannot see a reorder
of commuting gates, and in a syndrome round the order decides where a fault spreads. A barrier is
a fence across its qubits. An instruction whose `meta.op` names an op whose ions it does not touch
is itself a problem.

**Transport** (`check_program_transport`). The programme must move every ion exactly as the
certificate says: the same moves, in the same order, from the same trap to the same trap, along
the same rails (`via`) when both name them; and turn the same loops by the same amounts in the
same order. Order, not time: the certificate's cycles are its own clock. With this, what the
proved checker established about the certificate's transport holds of the programme's.

**Readout** (`readout_plan`). For every qubit, the programme's sequence of measurements and
resets must be the circuit's, and each is compared where it happens: deferred onto a fresh
qubit on both sides, so a measurement of the wrong ion, or at an inequivalent point, changes the
result.

**Semantics.** The programme's native pulses, `R`, `VZ` and `MS`, read through the certificate's
map, are composed and compared with the source circuit parsed from the QASM: as stabilizer
tableaux for a Clifford circuit, as exact unitaries up to a global phase otherwise, up to
`--max-unitary-qubits` (10 by default, counting one extra qubit per deferred readout). This is
which gate fires: the half the certificate never names (Part G).

## 7. The verdict

R10 is `passed` when the Lean checker accepted (`--qcheck`, on the bound input) and every
computed check above agreed, the semantics included. It is `partial` when every check that ran
agreed but the Lean checker was not run, or the semantics could not be settled (a circuit outside
the Clifford fragment and too large for the exact unitary). Anything else is `FAILED`, with the first reason: a malformed certificate, an input that
is not this certificate's, a Lean refusal, a binding that does not describe the circuit, failed
transport or witnesses, a reorder, or a programme that computes something else.

So a compiler in any language that writes these two files, in this form, gets the same verdict
the platform gives its own. Lesson I5 is the other way to enter a compiler, which asks for less.
-/
