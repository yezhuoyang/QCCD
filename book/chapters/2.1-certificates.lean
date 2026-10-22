import QCCDC.Cert.Sound

/-!
# Certificates: the Bell pair, proved

*Part F, lesson F1. You need Parts A and B of this tutorial (sites, rails, shuttles, gates).
You do not need to know Lean; this lesson teaches the little it uses.*

Our compiler is a search. It decides which ion carries which qubit, where each ion starts,
which ions move when, and where each gate fires. Nobody proves a search like that correct,
and we do not try to. Instead, every time it compiles a circuit, the compiler also writes a
**certificate**: a short account of where every ion is and where every gate happens. A small
**checker** then decides whether that account holds, and the checker is proved correct once,
in Lean. If the checker accepts, the account is right, whatever search produced it.

This lesson takes one real certificate, the Bell pair compiled for the four-site register
`chain4` (it is on the Compilation page), and does with it everything the checker does. The
Lean below runs in this page. Edit any of it; the checker is the real one.
-/

open QCCDC.Cert

/-!
## 1. What the compiler hands you

The circuit is four statements: `h q[0]`, `cx q[0],q[1]`, and a measurement of each qubit.
The device `chain4` is four traps in a row, `C0 - C1 - C2 - C3`, every one able to gate.
The compiler put qubit 0 on ion `q0` in trap `C1` and qubit 1 on ion `q1` in `C0`, did the
Hadamard where `q0` stood, moved `q0` next door, and did the CNOT in `C0`.

Its certificate, as Lean data, is the value `bell` below. Read it field by field:

* `qmap`: which ion carries which qubit.
* `init`: where each ion starts.
* `ops`: the circuit, statement by statement (`idx` is its position, `qubits` its operands).
* `moves`: each hop, at the machine cycle `t` it happens in.
* `gates`: one **witness** per gate: the op it realises (`dag`), the cycle, the trap, the ions.
* `gateSites` and `hops`: facts about the device. These do not come from the compiler; they
  are read out of the device's own document, so the compiler cannot widen the machine by
  claiming a hop that is not there.

A `structure` in Lean is a record; `⟨3, "q0", "C1", "C0"⟩` builds one positionally (here a
`Move`: cycle 3, ion `q0`, from `C1` to `C0`).
-/

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
Here is that certificate, played in the studio -- the same animation as everywhere else on this
site. The page asks Lean to replay `bell` with the checker's own definitions, and turns what Lean
reports into a studio programme: the ions where `init` puts them, each move in cycle order, each
gate in the trap its witness names, and a cooling step before the two-qubit gate (a certificate
leaves cooling to the cooling pass). Press Play: qubit 0 (ion `q0`) has its Hadamard in `C1`, hops
to `C0`, and the CNOT fires in `C0`. Under the studio, each row is one of the checker's conditions
on one move or one witness, as Lean evaluated it; click a row and the studio steps there. Edit
`bell` above and the figure follows.

{{figure bell}}

`#eval` runs an expression and shows the result. It proves nothing; it computes.
-/

#eval bell.gates.length
#eval bell.qmap.map (fun p => p.2)

/-!
A **theorem** is a statement with a proof. `rfl` ("reflexivity") proves an equation whose two
sides compute to the same thing.

#### Exercise 1 (★) `bell_ions`
Prove that the ions of the Bell certificate, in qubit order, are `q0` then `q1`.
-/

theorem bell_ions : bell.qmap.map (fun p => p.2) = ["q0", "q1"] := by
  rfl

/-!
## 2. Replaying the certificate

The checker never believes a position the certificate states. It starts from `init` and
replays the moves itself. `posAt bell t` is where every ion is **at the start of cycle `t`**:
after every move before `t`, before every move at or after it. `look` finds one ion in it.
-/

#eval (List.range 10).map fun t => (t, look (posAt bell t) "q0")

/-!
The move is stamped `t = 3`: `q0` is still in `C1` at the start of cycle 3, and in `C0` from
the start of cycle 4. Step the studio across that move and watch `q0` go: Exercise 2 is that
step, stated in Lean.

{{figure bell at=3}}

`decide +kernel` proves a statement by having Lean's kernel, the small trusted core that
checks every proof, compute it to `true`. It is the strongest kind of "by computation": the
kernel is the only thing trusted.

#### Exercise 2 (★) `q0_moves_in_cycle_3`
Prove both halves: at the start of cycle 3 `q0` is in `C1`, and at the start of cycle 4 it is
in `C0` -- either side of the move in the studio above. (`∧` is "and"; `⟨p, q⟩` proves it from two proofs.)
-/

theorem q0_moves_in_cycle_3 :
    look (posAt bell 3) "q0" = some "C1" ∧ look (posAt bell 4) "q0" = some "C0" :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
## 3. What "the certificate holds" means

Here is the whole specification. `#print` shows a definition.
-/

#print Implements

/-!
It is eleven conditions, joined by `∧`. In words:

1. `unrealised = []`: the compiler gave up on no operation.
2. `MapInjective`: no two qubits share an ion.
3. `MapTotal`: every qubit has an ion.
4. `EventsSorted`: moves and rotations are listed in cycle order, so the replay is well defined.
5. `RotationsKnown`: every rotation names a loop the device has.
6. `MovesAreHops`: every move is a hop of the device, and departs from where the replay
   says the ion is. No teleporting.
7. `GatesLegal`: every witness's ions are all in its trap at its cycle, and that trap can gate.
8. `OwnIons`: those ions are exactly the ions of the op's own qubits.
9. `Covered`: every operation other than a measurement, reset or barrier has exactly one
   witness.
10. `Grounded`: every witness names an operation the circuit has.
11. `RespectsOrder`: operations sharing a qubit are carried out in the order the circuit
    writes them.

In the figures, each move's row is condition 6 (a hop, and it starts where the ion is), and
each witness's row is conditions 7 and 8 (the ions together, a trap that can gate, the op's own
ions).

Each condition is a `Prop`, a statement, and each one is **decidable**: there is a program
that settles it. That is what makes a checker possible.

#### Exercise 3 (★) `bell_hops`
Prove condition 6 for the Bell certificate on its own.
-/

theorem bell_hops : MovesAreHops bell := by
  decide +kernel

/-!
## 4. The checker and its proof

The checker is one line: `check inp := decide (Implements inp)`. It runs the decision
procedures of the eleven conditions and answers `true` or `false`. The theorem that makes it
a *proved* checker is

```
theorem check_sound : check inp = true → Implements inp
```

read: whenever the checker says yes, the specification really holds. It is proved once, for
every certificate there will ever be, and it uses one axiom, propositional extensionality
(`propext`), which every Lean proof about logic may assume.
-/

#check @check_sound

/-!
#### Exercise 4 (★) `bell_implements`
Prove that the Bell certificate holds: apply `check_sound` to a proof that the checker says
`true`.
-/

theorem bell_implements : Implements bell :=
  check_sound (by decide +kernel)

#print axioms bell_implements

/-!
Three ways to "run the checker", and what each one trusts:

| how | what you get | what you trust |
|---|---|---|
| `#eval check bell` | the answer `true` | the compiled code of `check` |
| `by native_decide` | a theorem | the compiled code too, recorded as an extra axiom |
| `by decide +kernel` | a theorem | only Lean's kernel |

The command-line checker `qcheck` is the first row: the same `check`, compiled. Its answer is
trustworthy because `check_sound` is proved about *that* function. In this page you can have
the third row, the strongest, for any certificate small enough for the kernel to compute.

## 5. Breaking it

A checker is only as good as what it refuses. Here are three ways a compiler could lie about
the Bell pair, and what the checker says. `diagnose` lists the conditions that fail; it is a
convenience, not part of the proof.

**The late move.** The hop happens in cycle 9 instead of 3. At cycle 8 the CNOT's witness says
both ions are in `C0`, but `q0` has not arrived.
-/

def bellLate : Input := { bell with moves := [⟨9, "q0", "C1", "C0"⟩] }

#eval check bellLate
#eval diagnose bellLate

/-!
Click the CX row: the studio steps to the CNOT, and `q0` is still in `C1` -- the certificate says
the hop comes later. The studio's own rule check flags it too (R6b: a two-qubit gate acts only on
ions in one gate zone). The red row is exactly what Exercise 5 asks you to prove.

{{figure bellLate at=8}}

`decide +kernel` would prove `check bellLate = false` by computation. A proof that says *why*
is better: `rejects_not_colocated` states that if some ion of some witness is not where the
witness says at its cycle, the checker refuses.
-/

#check @rejects_not_colocated

/-!
#### Exercise 5 (★★) `late_rejected`
Prove the late move is refused, with `rejects_not_colocated`. It needs three things: the
witness (the CNOT's, `⟨1, 8, "C0", ["q0", "q1"]⟩`) is in the list (`by simp [bellLate, bell]`
shows membership in a literal list), the ion (`q0`) is one of its ions, and `q0` is not in `C0`
at cycle 8 (compute it).
-/

theorem late_rejected : check bellLate = false :=
  rejects_not_colocated (g := ⟨1, 8, "C0", ["q0", "q1"]⟩) (i := "q0")
    (by simp [bellLate, bell]) (by simp) (by decide +kernel)

/-!
**The teleport.** Start `q0` in `C3` (its Hadamard there too) and move it straight to `C0`.
The device has no such hop,
and the studio, asked to make that move, refuses it: there is no segment between `C3` and `C0`.

{{figure bellTeleport at=3}}

#### Exercise 6 (★★) `teleport_rejected`
Prove it is refused, with `rejects_teleport`.
-/

def bellTeleport : Input :=
  { bell with init := [("q0", "C3"), ("q1", "C0")], moves := [⟨3, "q0", "C3", "C0"⟩],
              gates := [⟨0, 2, "C3", ["q0"]⟩, ⟨1, 8, "C0", ["q0", "q1"]⟩] }

#check @rejects_teleport

theorem teleport_rejected : check bellTeleport = false :=
  rejects_teleport (m := ⟨3, "q0", "C3", "C0"⟩) (by simp [bellTeleport]) (by decide +kernel)

/-!
**Somebody else's ions.** The CNOT's witness names `q1` twice and leaves `q0` out. The two
named ions are certainly together, so condition 7 holds; condition 8 does not -- one green tick
and one red cross on the same row. Notice what the studio says: nothing is wrong. It moves and
gates whatever ions it is told to; only the certificate checker knows which ions the op's qubits
are on.

{{figure bellForeign at=8}}
-/

def bellForeign : Input :=
  { bell with gates := [⟨0, 2, "C1", ["q0"]⟩, ⟨1, 8, "C0", ["q1", "q1"]⟩] }

#eval diagnose bellForeign

/-!
## 6. Proving a refusal nobody wrote down

The checker's proof comes with eleven rejection lemmas like the two you used. One condition
has none: `MapTotal`, that every qubit has an ion.

#### Exercise 7 (★★★) `rejects_partial_map`
Prove that a certificate whose map has the wrong number of entries is refused, for **every**
certificate, not one example.

Hints: `decide_eq_false` turns "this condition cannot hold" into `check inp = false`; it wants
a function from a proof of `Implements inp` to `False`. That proof is a chain of `∧`s:
`himp.1` is the first condition, `himp.2.1` the second, `himp.2.2.1` the third.
-/

theorem rejects_partial_map {inp : Input} (h : inp.qmap.length ≠ inp.nQubits) :
    check inp = false :=
  decide_eq_false fun himp => h himp.2.2.1

/-!
#### Exercise 8 (★★★) `nothing_moves`
Prove that if a certificate has no moves and no rotations, every ion is, at every cycle,
where it started. This one is about the replay, not the checker: unfold the definitions
(`posAt`, `replay`, `events`, `merge`, `stateIn`, `snapshots`, `finalPos`) and let `simp`
use your two hypotheses.
-/

theorem nothing_moves {inp : Input} (hm : inp.moves = []) (hr : inp.rots = []) (t : Nat) :
    posAt inp t = inp.init := by
  simp [posAt, replay, events, merge, stateIn, snapshots, finalPos, hm, hr]

/-!
## 7. The website's certificates

Every compiled example on the Compilation page has its certificate published beside it, and the
input the checker reads from it. The panel below turns each input into a Lean value, exactly as
the checker parses it, and proves by `decide +kernel` that it holds: the same theorem as
Exercise 4, for certificates you did not write. Each row links the two files, so you can run the
command-line checker on them too and compare, and **watch** opens the real compiled programme in
the studio -- the same page the Compilation page embeds, the circuit in step with it -- with the
certificate's rows from Lean under it.

{{certificates}}

A large certificate (the BB[[144,12,12]] rounds run to thousands of moves) is too big for the
kernel to compute in a browser in reasonable time. For those the tutorial uses the compiled checker,
whose answer is trusted because `check_sound` is proved about the same function. Part H does
that for every board on the site.

## 8. What the checker does not see

`Implements` names no gate. A witness says *an operation happened here, now, on these ions*;
it never says *which* gate the laser pulses make. A compiler that wrote a CX where the circuit
says CZ, at the right place and time, would pass every condition above. The second half of
the verdict called R10 is a different check: it composes the emitted pulses back into a
circuit and compares that with the source. It is computed, not proved, and it is Part G.

The checker also sees only the certificate, never the programme. Two computed checks tie
the programme to it: every witness must name a real instruction of the programme, of the right
kind, on the right ions; and the programme's own transport, ion by ion and loop turn by loop
turn, must equal the certificate's moves and rotations. Whether each move is physically legal
(capacities, junctions, heating) is a third question, answered by the rules, which the rule
language of Part E states one assertion at a time.

A proof is exactly as strong as what it is about. Everything this lesson proved is about the
certificate; what ties it to the programme and to the circuit is computed, and the trusted
base of the whole verdict is listed at the end of Part F.
-/
