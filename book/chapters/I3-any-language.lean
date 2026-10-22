import QCCDC.Cert.Sound

/-!
# Any language, checked

*Part I, lesson I3. You need lessons F1 and I1.*

Lesson I2's proof works because the compiler is a Lean function. Our own compiler, `qccdc`, is
written in OCaml, and yours may be Python, Rust or C++. No Lean theorem can be about those. What
they can do is write a certificate, as a file, and hand it to the proved checker. If the checker
accepts it, `check_sound` says the certificate satisfies `Implements`, and it says so whatever
wrote the file: the proof is about the checker, never about the compiler. That is **translation
validation**: every output checked, by a checker proved once.

This lesson follows a certificate from a file to a verdict: what the checker reads, how it reads
it, what it does with a field that is missing, and which steps are proved and which are only
computed.
-/

open QCCDC.Cert Lean

/-!
## 1. The checker as a program

The command-line checker `qcheck` is about fifty lines of Lean. It reads one file, parses it as
JSON (`Json.parse`, Lean's own parser), turns the JSON into an `Input` (`Input.parse`, in the
module `QCCDC.Cert.Syntax`), and runs `check`, the function `check_sound` is
proved about. It prints `ACCEPTED`, or `REJECTED` with `diagnose`'s list of failed conditions, or
`REJECTED -- the input is malformed` naming a field that is missing or an entry it cannot read;
it exits with 0 when it accepts, 1 when it refuses, 2 when the file is not JSON.

From a compiler in any language to a verdict is three steps. The compiler writes its
certificate, `prog.qcert.json` (lesson I4 gives its format). The device facts' builder
(`mk_qcheck_input`) adds the device's facts, read from the device's own document, and writes the
checker's input, `prog.qcheck.json`. Then `qcheck` decides:

```
qcheck prog.qcheck.json
```

The platform's grader takes exactly these steps for every design it grades (`qccd submit
--local` runs it on your machine). `qccd toolchain install` fetches a prebuilt `qcheck` where one
is published; elsewhere `lake build`, in the checker's own Lean package, builds it from Lean core
alone.

## 2. The document

The file `qcheck` reads is one JSON object, with these keys and no others. `qcheck`'s reader,
`Input.parse`, refuses a file that leaves one out or writes an entry it cannot read; this page
runs the module's other reader, `Input.ofJson`, which gives the same `Input` from a well-formed
file and, from a malformed one, drops what it cannot read:

| key | `Input` field | what it is |
|---|---|---|
| `circuit`, `arch` | `circuit`, `arch` | names, for the report |
| `n_qubits` | `nQubits` | the circuit's qubit count |
| `map` | `qmap` | qubit to ion; the keys are qubit numbers written as strings (`"0"`) |
| `init` | `init` | ion to its first trap |
| `circuit_ops` | `ops` | the circuit, as `{"i", "name", "qubits"}` |
| `moves` | `moves` | `{"t", "ion", "from", "to"}` |
| `rotations` | `rots` | `{"t", "loop", "delta"}` |
| `gates` | `gates` | the witnesses, `{"dag", "t", "site", "ions"}` |
| `unrealised` | `unrealised` | op indices the compiler gave up on |
| `gate_sites`, `hops`, `loops` | `gateSites`, `hops`, `loops` | the device's facts, which the builder adds |

Every other key is ignored: a certificate's `params`, `line`, `via`, `instr` and `pulses` travel
in the same file for the other checks (lesson I4), and `qcheck` never reads them. A key that is
missing, or a value of the wrong shape, becomes an empty list, or `0` for `n_qubits`. The parser's
own comment: *silence is never taken for assent*. An empty field does not pass quietly; the
conditions then fail on it (section 5).

Here is the published GHZ chain's input, exactly as the Compilation page ships it
([algo_ghz.qcheck.json](data/algo_ghz.qcheck.json)), written in Lean with `json%`, Lean's notation
for a JSON value.
-/

def ghzJ : Json := json% {"circuit": "circuit", "arch": "chain4", "n_qubits": 4,
  "map": {"0": "q0", "1": "q1", "2": "q2", "3": "q3"},
  "init": {"q0": "C3", "q1": "C2", "q2": "C1", "q3": "C0"},
  "circuit_ops": [{"i": 0, "name": "h", "qubits": [0], "params": [], "line": 5},
                  {"i": 1, "name": "cx", "qubits": [0, 1], "params": [], "line": 6},
                  {"i": 2, "name": "cx", "qubits": [1, 2], "params": [], "line": 7},
                  {"i": 3, "name": "cx", "qubits": [2, 3], "params": [], "line": 8},
                  {"i": 4, "name": "measure", "qubits": [0], "params": [], "line": 9},
                  {"i": 5, "name": "measure", "qubits": [1], "params": [], "line": 9},
                  {"i": 6, "name": "measure", "qubits": [2], "params": [], "line": 9},
                  {"i": 7, "name": "measure", "qubits": [3], "params": [], "line": 9}],
  "moves": [{"t": 3, "ion": "q0", "from": "C3", "to": "C2", "via": ["E2"]},
            {"t": 9, "ion": "q1", "from": "C2", "to": "C1", "via": ["E1"]},
            {"t": 16, "ion": "q2", "from": "C1", "to": "C0", "via": ["E0"]}],
  "rotations": [],
  "gates": [{"dag": 0, "instr": 3, "t": 2, "site": "C3", "ions": ["q0"],
             "pulses": ["R(1.5708,-1.5708)@q0", "VZ(3.14159)@q0"]},
            {"dag": 1, "instr": 9, "t": 8, "site": "C2", "ions": ["q0", "q1"],
             "pulses": ["R(1.5708,1.5708)@q0", "MS(1.5708)@q0,q1", "R(-1.5708,0)@q0",
                        "R(-1.5708,0)@q1", "R(-1.5708,1.5708)@q0"]},
            {"dag": 2, "instr": 15, "t": 14, "site": "C1", "ions": ["q1", "q2"],
             "pulses": ["R(1.5708,1.5708)@q1", "MS(1.5708)@q1,q2", "R(-1.5708,0)@q1",
                        "R(-1.5708,0)@q2", "R(-1.5708,1.5708)@q1"]},
            {"dag": 3, "instr": 22, "t": 21, "site": "C0", "ions": ["q2", "q3"],
             "pulses": ["R(1.5708,1.5708)@q2", "MS(1.5708)@q2,q3", "R(-1.5708,0)@q2",
                        "R(-1.5708,0)@q3", "R(-1.5708,1.5708)@q2"]}],
  "unrealised": [],
  "gate_sites": ["C0", "C1", "C2", "C3"],
  "hops": [{"from": "C0", "to": "C1"}, {"from": "C1", "to": "C0"}, {"from": "C1", "to": "C2"},
           {"from": "C2", "to": "C1"}, {"from": "C2", "to": "C3"}, {"from": "C3", "to": "C2"}],
  "loops": {}}

/-!
`qcheck`'s two steps, run here:
-/

#eval check (Input.ofJson ghzJ)
#eval (Input.ofJson ghzJ).moves

/-!
## 3. Computed, then proved

Which of those two steps is proved? `check` is: `check_sound` is about it. The reading is not:
`Input.parse` and `Input.ofJson` are run, never proved, and so is `Json.parse` before them. In this page Lean cannot even look
inside `Input.ofJson` during a proof: the page's Lean builds every file as a module, and a module
shares a definition's body only when its file exposes it, which the module `QCCDC.Cert.Syntax`,
with its `private` helpers, does not. Natively the kernel can unfold all of it except the
qubit map, whose keys it reads with `String.toNat?`, which the kernel cannot compute. Either way,
`qcheck`'s verdict trusts the parser as code that runs, and the trusted base of the verdict says
so (Part F).

So we split the work as `qcheck` does. Write down the `Input` the parse gives; check, by running
both, that the parse gives exactly it; and prove that value holds, in the kernel. `sameInput`
compares two `Input`s field by field (`==` is equality as a computation, `&&` is "and").
-/

def chain4Sites : List String := ["C0", "C1", "C2", "C3"]
def chain4Hops : List (String × String) :=
  [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")]

def ghzIn : Input :=
  { circuit := "circuit", arch := "chain4", nQubits := 4,
    qmap := [(0, "q0"), (1, "q1"), (2, "q2"), (3, "q3")],
    init := [("q0", "C3"), ("q1", "C2"), ("q2", "C1"), ("q3", "C0")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "cx", [1, 2]⟩, ⟨3, "cx", [2, 3]⟩,
            ⟨4, "measure", [0]⟩, ⟨5, "measure", [1]⟩, ⟨6, "measure", [2]⟩, ⟨7, "measure", [3]⟩],
    moves := [⟨3, "q0", "C3", "C2"⟩, ⟨9, "q1", "C2", "C1"⟩, ⟨16, "q2", "C1", "C0"⟩],
    rots := [],
    gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C2", ["q0", "q1"]⟩, ⟨2, 14, "C1", ["q1", "q2"]⟩,
              ⟨3, 21, "C0", ["q2", "q3"]⟩],
    unrealised := [], gateSites := chain4Sites, hops := chain4Hops, loops := [] }

def sameInput (a b : Input) : Bool :=
  a.circuit == b.circuit && a.arch == b.arch && a.nQubits == b.nQubits && a.qmap == b.qmap &&
  a.init == b.init && a.ops == b.ops && a.moves == b.moves && a.rots == b.rots &&
  a.gates == b.gates && a.unrealised == b.unrealised && a.gateSites == b.gateSites &&
  a.hops == b.hops && a.loops == b.loops

#eval sameInput (Input.ofJson ghzJ) ghzIn

/-!
The parse gives exactly `ghzIn`. It is the certificate `qccdc` wrote for GHZ-4: unlike I1's
compiler, it lays the chain along the register and never sends an ion home, so each control
moves once and stays.

{{figure ghzIn}}

#### Exercise 1 (★) `ghz_input_holds`
Prove that the published GHZ input, as the parser reads it, holds.
-/

theorem ghz_input_holds : Implements ghzIn :=
  check_sound (by decide +kernel)

/-!
## 4. A file is text

`Json.parse` turns text into JSON, or says why it cannot. `qcheckText` runs `qcheck`'s whole path
on a string and says what `qcheck` would print, in one line.
-/

def qcheckText (text : String) : String :=
  match Json.parse text with
  | .error e => "not JSON: " ++ e
  | .ok j =>
    let inp := Input.ofJson j
    if check inp then "ACCEPTED" else "REJECTED: " ++ "; ".intercalate (diagnose inp)

/-!
Here is a file from a compiler with the most common bug between two languages: it copies the
circuit's ops numbered from 0, as its QASM parser gave them, and numbers its own witnesses from 1.
Everything else is the published Bell certificate of F1.
-/

def offByOneText : String := "\n".intercalate [
  "{\"circuit\": \"bell\", \"arch\": \"chain4\", \"n_qubits\": 2,",
  " \"map\": {\"0\": \"q0\", \"1\": \"q1\"}, \"init\": {\"q0\": \"C1\", \"q1\": \"C0\"},",
  " \"circuit_ops\": [{\"i\": 0, \"name\": \"h\", \"qubits\": [0]},",
  "                 {\"i\": 1, \"name\": \"cx\", \"qubits\": [0, 1]},",
  "                 {\"i\": 2, \"name\": \"measure\", \"qubits\": [0]},",
  "                 {\"i\": 3, \"name\": \"measure\", \"qubits\": [1]}],",
  " \"moves\": [{\"t\": 3, \"ion\": \"q0\", \"from\": \"C1\", \"to\": \"C0\"}], \"rotations\": [],",
  " \"gates\": [{\"dag\": 1, \"t\": 2, \"site\": \"C1\", \"ions\": [\"q0\"]},",
  "           {\"dag\": 2, \"t\": 8, \"site\": \"C0\", \"ions\": [\"q0\", \"q1\"]}],",
  " \"unrealised\": [], \"gate_sites\": [\"C0\", \"C1\", \"C2\", \"C3\"],",
  " \"hops\": [{\"from\": \"C0\", \"to\": \"C1\"}, {\"from\": \"C1\", \"to\": \"C0\"},",
  "          {\"from\": \"C1\", \"to\": \"C2\"}, {\"from\": \"C2\", \"to\": \"C1\"},",
  "          {\"from\": \"C2\", \"to\": \"C3\"}, {\"from\": \"C3\", \"to\": \"C2\"}],",
  " \"loops\": {}}"]

#eval qcheckText offByOneText

/-!
Two conditions fail. The witness for op 1 names one ion, and op 1 is the CNOT, on two
(`OwnIons`); and the Hadamard, op 0, has no witness at all (`Covered`). The parse, as a value:
-/

def offByOne : Input :=
  { circuit := "bell", arch := "chain4", nQubits := 2,
    qmap := [(0, "q0"), (1, "q1")], init := [("q0", "C1"), ("q1", "C0")],
    ops := [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [1]⟩],
    moves := [⟨3, "q0", "C1", "C0"⟩], rots := [],
    gates := [⟨1, 2, "C1", ["q0"]⟩, ⟨2, 8, "C0", ["q0", "q1"]⟩],
    unrealised := [], gateSites := chain4Sites, hops := chain4Hops, loops := [] }

#eval match Json.parse offByOneText with
  | .ok j => sameInput (Input.ofJson j) offByOne
  | .error _ => false

/-!
The studio plays every move and gate where the file says; under it, Lean's rows show the
witnesses that are not their op's.

{{figure offByOne}}

#### Exercise 2 (★★) `off_by_one_refused`
Prove the file refused, with `rejects_dropped_gate`: an op that needs a witness and has not
exactly one. The op is the Hadamard, `⟨0, "h", [0]⟩`. Its membership in the literal op list is
`by simp [offByOne]`; the other two side conditions are computations.
-/

#check @rejects_dropped_gate

theorem off_by_one_refused : check offByOne = false :=
  rejects_dropped_gate (o := ⟨0, "h", [0]⟩) (by simp [offByOne]) (by decide) (by decide +kernel)

/-!
## 5. What a missing field does

A compiler that writes the qubit map with the ion names as keys (`{"q0": "q0"}`) instead of the
qubit numbers has written nothing a reader can take as a qubit. `qcheck` says so at once:
`REJECTED -- the input is malformed: map["q0"] is malformed`. The lenient reader this page runs
drops the entries instead, and the map comes out empty:
-/

#eval (Input.ofJson (json% {"n_qubits": 2, "map": {"q0": "q0", "q1": "q1"}})).qmap

/-!
Even then an empty map is never mistaken for a good one: the checker refuses it, for every
certificate of a circuit with any qubit at all.

#### Exercise 3 (★★) `no_map_refused`
Prove it, for every `inp`. Condition 3, `MapTotal`, is `inp.qmap.length = inp.nQubits`; it is
`h.2.2.1` of a proof `h` of `Implements inp`. Rewrite with `hq`, and the length of `[]` is `0`.
-/

theorem no_map_refused (inp : Input) (hq : inp.qmap = []) (hn : inp.nQubits ≠ 0) :
    check inp = false :=
  decide_eq_false fun h => hn (by
    have h3 : inp.qmap.length = inp.nQubits := h.2.2.1
    rw [hq] at h3
    exact h3.symm)

/-!
The same holds of the device's facts. A file without `gate_sites` (a compiler that wrote its own
qcheck input and forgot them) has no trap that can gate, so every certificate with a gate is
refused.

#### Exercise 4 (★★) `no_sites_refused`
Prove it with `rejects_ungateable_site` (`simp` knows nothing is a member of `[]`).
-/

theorem no_sites_refused (inp : Input) (g : GateW) (hg : g ∈ inp.gates)
    (hs : inp.gateSites = []) : check inp = false :=
  rejects_ungateable_site hg (by rw [hs]; simp)

/-!
## 6. What the file's labels and order mean

The `circuit` and `arch` fields are names for the report. The specification never reads them, so
a certificate holds or fails the same under any names. That is why the semantic check
(`check_cert`), which runs `qcheck` for the platform, first rebuilds the input from the certificate and the device it was
given and refuses a file that differs from it (lesson I4): the names alone bind nothing.

#### Exercise 5 (★★) `names_are_labels`
Prove that renaming the circuit and the device does not change the verdict. The two
specifications are the same proposition by definition (`Iff.rfl`); turn that into equal verdicts
with `Bool.eq_iff_iff` (two Booleans are equal when they are `true` together) and `check_iff` (F1:
`check inp = true ↔ Implements inp`).
-/

theorem names_are_labels (inp : Input) (s a : String) :
    check { inp with circuit := s, arch := a } = check inp := by
  apply Bool.eq_iff_iff.2
  rw [check_iff, check_iff]
  exact Iff.rfl

/-!
Order is different. The checker replays the moves in the order the file lists them, and condition
4, `EventsSorted`, requires that order to be by cycle. A compiler that writes its moves in the
order a hash table returns them, every move correct, is refused.

#### Exercise 6 (★★★) `unsorted_refused`
Prove that a certificate without rotations whose second move is at an earlier cycle than its first
is refused, for every such certificate. `h.2.2.2.1` is `EventsSorted`, a statement about
`sortedByT (events inp)`; rewrite `events` with your hypotheses, let `simp` unfold `merge` and
`sortedByT`, and `omega` finds the contradiction.
-/

theorem unsorted_refused (inp : Input) (m₁ m₂ : Move) (rest : List Move)
    (hr : inp.rots = []) (hm : inp.moves = m₁ :: m₂ :: rest) (ht : m₂.t < m₁.t) :
    check inp = false := by
  apply decide_eq_false
  intro h
  have hs : sortedByT (events inp) = true := h.2.2.2.1
  simp only [events, hr, hm] at hs
  simp [merge, sortedByT, Ev.cyc] at hs
  omega

/-!
And one witness per operation. A compiler that writes a witness for every pulse it emits, rather
than one for every op of the circuit, gives a CNOT several, and condition 9 (`Covered`) asks for
exactly one.

#### Exercise 7 (★★★) `duplicate_refused`
Prove that if the first two witnesses of a certificate name the same op, an op that needs a
witness, the certificate is refused. Use `rejects_dropped_gate`; after rewriting the witness list,
`simp` computes the filter and its length.
-/

theorem duplicate_refused (inp : Input) (o : Op) (g₁ g₂ : GateW) (rest : List GateW)
    (ho : o ∈ inp.ops) (hn : needsWitness o = true)
    (hg : inp.gates = g₁ :: g₂ :: rest) (h₁ : g₁.dag = o.idx) (h₂ : g₂.dag = o.idx) :
    check inp = false := by
  apply rejects_dropped_gate ho hn
  rw [hg]
  simp [List.filter, h₁, h₂]

/-!
## 7. Your own certificate

Now be the compiler. The circuit is `h q[0]; cx q[0],q[2]; measure q[0]; measure q[2];` on three
qubits (qubit 1 is declared and idle), on `chain4`. Its ops are fixed, and so are the device's
facts; the map, the placement, the moves and the witnesses are yours. Write the `Input` that your
compiler's file would parse to, inside the proof: the statement only says that one exists.

A certificate in the shape of `ghzIn` above works, with your own fields. If the kernel refuses,
`#eval diagnose` on your value (in a `def` of your own) says which condition failed.

#### Exercise 8 (★★) `hand_written`
Prove that a certificate exists for this circuit on `chain4`: give it, then prove the six
conjuncts (five are `rfl`; the last is `check_sound (by decide +kernel)`).
-/

def handOps : List Op :=
  [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 2]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [2]⟩]

theorem hand_written : ∃ inp : Input,
    inp.nQubits = 3 ∧ inp.ops = handOps ∧ inp.gateSites = chain4Sites ∧
      inp.hops = chain4Hops ∧ inp.loops = [] ∧ Implements inp :=
  ⟨{ circuit := "hand", arch := "chain4", nQubits := 3,
     qmap := [(0, "q0"), (1, "q1"), (2, "q2")],
     init := [("q0", "C0"), ("q1", "C1"), ("q2", "C2")],
     ops := handOps,
     moves := [⟨1, "q0", "C0", "C1"⟩, ⟨2, "q0", "C1", "C2"⟩],
     rots := [],
     gates := [⟨0, 0, "C0", ["q0"]⟩, ⟨1, 3, "C2", ["q0", "q2"]⟩],
     unrealised := [], gateSites := chain4Sites, hops := chain4Hops, loops := [] },
   rfl, rfl, rfl, rfl, rfl, check_sound (by decide +kernel)⟩

/-!
As the file your compiler would write, that is a `.qcheck.json` like section 2's: the same keys,
your values.

## 8. What the verdict covers

When `qcheck` accepts a file, `check_sound` gives `Implements` of the `Input` the parser made from
it, whoever wrote the file and in whatever language. That is the whole of what is proved. Three
things are around it, and none is part of the proof:

* **the parse**: `Json.parse` and `qcheck`'s reader `Input.parse` are run, not proved (section 3);
* **the binding**: that the file is this certificate's, on this device, for this circuit (the
  names bind nothing, Exercise 5). The semantic check (`check_cert`) checks it, by computation;
* **the programme**: that the hardware programme does what the certificate says, gate by gate
  and move by move. The certificate's `instr`, `pulses` and `via`, which `qcheck` ignores, are
  what that check reads.

Lesson I4 is the format those other checks require: the full contract for a compiler that wants
its programmes judged the way the platform judges its own.
-/
