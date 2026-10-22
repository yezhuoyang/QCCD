import QCCDC.Cert.Sound

/-!
# A compiler in Lean

*Part I, lesson I1. You need Parts E and F, lesson F1 above all: this lesson writes the kind of
certificate F1 checked.*

In lesson F1 a certificate arrived from our compiler and you checked it. In this part you write
the compiler. This lesson's is small and complete. It reads a circuit, puts each qubit on an ion
in one of the four traps of `chain4`, routes every CNOT by carrying one ion along the rail, and
writes two things: the **certificate**, the same kind of Lean value F1 checked, and a **studio
programme**, so you can watch it run. The proved checker then decides every certificate it
writes, here in the page.

There are two ways to trust a compiler. One is to check every output it makes; that is this
lesson, and lesson I3 does the same for a compiler written in any language. The other is to
prove, once, that every output it can make is right; lesson I2 does that for this compiler.
-/

open QCCDC.Cert

/-!
## 1. Circuits as data

A circuit is a list of statements. Ours have three kinds: a one-qubit gate on one qubit (`h`,
`x`, `s`, ... by its QASM name), a CNOT between any two qubits, and a measurement. In Lean, a type
whose values come in several kinds is an `inductive` type: each `|` line is one kind, with its
fields. `deriving Repr` lets `#eval` print one.
-/

inductive Gate where
  | one (name : String) (q : Nat)   -- a one-qubit gate, by its QASM name: "h", "x", "s", ...
  | cx (c t : Nat)                  -- a CNOT: control c, target t
  | measure (q : Nat)
  deriving Repr

/-!
A circuit is a name, a number of qubits (QASM's `qreg q[n]`) and its statements, in order.
`.one "h" 0` is the statement `h q[0];` and `.cx 0 1` is `cx q[0],q[1];`.
-/

structure Circuit where
  name : String
  qubits : Nat
  body : List Gate

def bell : Circuit := ⟨"bell", 2, [.one "h" 0, .cx 0 1, .measure 0, .measure 1]⟩

def ghz3 : Circuit :=
  ⟨"ghz3", 3, [.one "h" 0, .cx 0 1, .cx 1 2, .measure 0, .measure 1, .measure 2]⟩

def ghz4 : Circuit :=
  ⟨"ghz4", 4, [.one "h" 0, .cx 0 1, .cx 1 2, .cx 2 3,
               .measure 0, .measure 1, .measure 2, .measure 3]⟩

/-!
The certificate carries the circuit as a list of `Op`s, numbered in program order (F1's `ops`).
A function on an inductive type is written by **pattern matching**: one line per kind of value.
`opsFrom i` numbers a list of statements from `i`; it calls itself on the rest of the list, which
is how Lean writes a loop. A `_` in a pattern matches anything and names nothing.
-/

def Gate.name : Gate → String
  | .one n _ => n
  | .cx _ _ => "cx"
  | .measure _ => "measure"

def Gate.qubits : Gate → List Nat
  | .one _ q => [q]
  | .cx c t => [c, t]
  | .measure q => [q]

def opsFrom (i : Nat) : List Gate → List Op
  | [] => []
  | g :: gs => ⟨i, g.name, g.qubits⟩ :: opsFrom (i + 1) gs

#eval opsFrom 0 bell.body

/-!
#### Exercise 1 (★) `bell_ops`
Prove that the op list of our Bell circuit is exactly the one in F1's certificate: `h` on qubit
0, `cx` on qubits 0 and 1, then the two measurements.
-/

theorem bell_ops :
    opsFrom 0 bell.body =
      [⟨0, "h", [0]⟩, ⟨1, "cx", [0, 1]⟩, ⟨2, "measure", [0]⟩, ⟨3, "measure", [1]⟩] := by
  rfl

/-!
## 2. The device

`chain4` is four traps in a row, `C0 - C1 - C2 - C3`, each able to hold two ions and to gate
(its document is `chain4.arch.json`). The certificate carries two facts about it: the traps
that can gate, and the ordered pairs of traps one hop apart. The compiler does not get to state
these. The device facts' builder (`mk_qcheck_input`) reads them out of the device's own
document, and for `chain4` it gives exactly the two lists below.

We number the traps and the qubits, and name them the way the certificate does: trap `k` is
`Ck`, and qubit `k` rides on the ion `qk`. The names `chain4` can use (four traps, and at most
eight ions, two to a trap) are spelled out, so that the kernel, when it decides a certificate,
only ever compares literal strings; past them the name is built (`++` joins two strings,
`toString 9` is `"9"`).
-/

def trap : Nat → String
  | 0 => "C0" | 1 => "C1" | 2 => "C2" | 3 => "C3"
  | k => "C" ++ toString k

def ion : Nat → String
  | 0 => "q0" | 1 => "q1" | 2 => "q2" | 3 => "q3"
  | 4 => "q4" | 5 => "q5" | 6 => "q6" | 7 => "q7"
  | k => "q" ++ toString k

def chain4Sites : List String := ["C0", "C1", "C2", "C3"]
def chain4Hops : List (String × String) :=
  [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")]

/-!
## 3. Placement and routing

**Placement.** Qubit `k` starts in trap `home k`. The placement `home` is yours to choose: any
function from qubits to trap numbers. Ours puts qubit `k` in trap `k`, one ion per trap, which is
why our circuits stop at four qubits. (`fun k => k` is the function that returns its argument.)
-/

def id4 : Nat → Nat := fun k => k

/-!
**Routing.** A CNOT needs both of its ions in one trap that can gate. Our compiler carries the
control's ion there: one hop per machine cycle along the rail to the target's trap, the gate,
then one hop per cycle back home. `right a n` is the `n` hops rightwards from trap `a`, `left a n`
the `n` hops leftwards, and `route a b` the hops from `a` to `b`.
-/

def right (a : Nat) : Nat → List (Nat × Nat)
  | 0 => []
  | n + 1 => (a, a + 1) :: right (a + 1) n

def left (a : Nat) : Nat → List (Nat × Nat)
  | 0 => []
  | n + 1 => (a, a - 1) :: left (a - 1) n

def route (a b : Nat) : List (Nat × Nat) :=
  if a ≤ b then right a (b - a) else left a (a - b)

#eval route 0 3
#eval route 3 1

/-!
Why carry it home again? It keeps one promise: at the start of every operation every ion is in
its home trap. With one ion per trap, the travelling ion then never shares a trap with more than
one other, so a trap's capacity of two holds on every hop and at every gate. Keep that promise in
mind. It is an argument about the *rules*, and the certificate checker never checks it: section
7 breaks it and watches what happens.

Every route the compiler can take on `chain4` is made of the device's own hops. There are only
sixteen pairs of traps, so Lean can try them all.

#### Exercise 2 (★★) `routes_are_hops`
Prove that for every two traps `a` and `b` of `chain4`, every hop of `route a b` is a hop of the
device. `List.range 4` is `[0, 1, 2, 3]`, and a statement that quantifies over the members of a
list is one `decide +kernel` can check: it runs through them.
-/

theorem routes_are_hops :
    ∀ a ∈ List.range 4, ∀ b ∈ List.range 4, ∀ h ∈ route a b,
      (trap h.1, trap h.2) ∈ chain4Hops := by
  decide +kernel

/-!
A **policy** bundles the two choices this router makes, so that section 7 can break them one at
a time: how to get from one trap to another, and whether to carry the ion home after its CNOT.
-/

structure Policy where
  route : Nat → Nat → List (Nat × Nat)
  back : Bool

def honest : Policy := ⟨route, true⟩

/-!
## 4. Time, and the certificate

The certificate stamps every move and every witness with a machine cycle. The compiler keeps a
clock `T`, the first free cycle. A one-qubit gate takes one cycle. A CNOT whose route has `d`
hops takes `d + 1 + d`: hop `k` there at cycle `T + k`, the gate at `T + d`, the walk home from
`T + d + 1`. A measurement takes none, and gets no witness (F1: measurements, resets and barriers
need none).

Recall from F1 that a move stamped `t` has happened by the start of cycle `t + 1`, so the gate at
`T + d` sees the ion arrived. `walk q t hops` turns a list of hops into the moves of qubit `q`'s
ion, one per cycle from `t`.
-/

def walk (q t : Nat) : List (Nat × Nat) → List Move
  | [] => []
  | (a, b) :: rest => ⟨t, ion q, trap a, trap b⟩ :: walk q (t + 1) rest

def Policy.go (p : Policy) (home : Nat → Nat) (c t : Nat) : List (Nat × Nat) :=
  p.route (home c) (home t)

def Policy.ret (p : Policy) (home : Nat → Nat) (c t : Nat) : List (Nat × Nat) :=
  if p.back then p.route (home t) (home c) else []

def Gate.cycles (p : Policy) (home : Nat → Nat) : Gate → Nat
  | .one _ _ => 1
  | .cx c t => (p.go home c t).length + 1 + (p.ret home c t).length
  | .measure _ => 0

def Gate.moves (p : Policy) (home : Nat → Nat) (T : Nat) : Gate → List Move
  | .cx c t => walk c T (p.go home c t) ++
               walk c (T + (p.go home c t).length + 1) (p.ret home c t)
  | _ => []

def Gate.witness (p : Policy) (home : Nat → Nat) (i T : Nat) : Gate → List GateW
  | .one _ q => [⟨i, T, trap (home q), [ion q]⟩]
  | .cx c t => [⟨i, T + (p.go home c t).length, trap (home t), [ion c, ion t]⟩]
  | .measure _ => []

def movesFrom (p : Policy) (home : Nat → Nat) (T : Nat) : List Gate → List Move
  | [] => []
  | g :: gs => g.moves p home T ++ movesFrom p home (T + g.cycles p home) gs

def witnessesFrom (p : Policy) (home : Nat → Nat) (i T : Nat) : List Gate → List GateW
  | [] => []
  | g :: gs => g.witness p home i T ++ witnessesFrom p home (i + 1) (T + g.cycles p home) gs

/-!
Now the certificate. Every field is one F1 read: the map (qubit `k` on ion `qk`), the initial
placement, the ops, the moves, no rotations (`chain4` has no closed loop to turn), the witnesses,
nothing left unrealised, and the device's two lists. `List.range n` is `[0, 1, ..., n - 1]`.
-/

def compileWith (p : Policy) (home : Nat → Nat) (c : Circuit) : Input :=
  { circuit := c.name, arch := "chain4", nQubits := c.qubits,
    qmap := (List.range c.qubits).map fun k => (k, ion k),
    init := (List.range c.qubits).map fun k => (ion k, trap (home k)),
    ops := opsFrom 0 c.body,
    moves := movesFrom p home 0 c.body, rots := [],
    gates := witnessesFrom p home 0 0 c.body, unrealised := [],
    gateSites := chain4Sites, hops := chain4Hops, loops := [] }

def compile (home : Nat → Nat) (c : Circuit) : Input := compileWith honest home c

def bellCert : Input := compile id4 bell

#eval bellCert

/-!
Read it against F1's. This lesson's compiler put qubit 0 in `C0` and qubit 1 in `C1`, did the
Hadamard at cycle 0, carried `q0` to `C1` at cycle 1, fired the CNOT there at cycle 2, and carried
`q0` home at cycle 3. The published certificate in F1 has one move, not two: the platform's
compiler, `qccdc`, started `q0` in `C1` and left it in `C0` after the CNOT. This one always walks
home. Both are correct certificates for the same circuit; the checker does not prefer either.

Here it is played in the studio from Lean's replay, with every condition the checker evaluates on
each move and witness.

{{figure bellCert}}

## 5. The studio programme

The second output is a programme in the studio's own language (the platform's `Program`):
`p.init` places the ions, `p.shuttle` walks one ion along a path of traps, `p.gate` fires a gate,
`p.measure` reads ions out. Before every CNOT the programme cools (`p.cool()`): carrying an ion
heats it, and a two-qubit gate needs cold ions (rule R7). The certificate says nothing about
cooling; the programme must.

A studio figure needs a value with two fields, the device and the programme's text, so that is
what we build. The text comes from the same placement and the same routes as the certificate.
-/

structure Studio where
  arch : String
  src : String

def quote (s : String) : String := "\"" ++ s ++ "\""

def commas : List String → String
  | [] => ""
  | [s] => s
  | s :: rest => s ++ ", " ++ commas rest

def lines : List String → String
  | [] => ""
  | [s] => s
  | s :: rest => s ++ "\n" ++ lines rest

def shuttle (q a : Nat) (hops : List (Nat × Nat)) : List String :=
  match hops with
  | [] => []
  | _ => ["p.shuttle(" ++ quote (ion q) ++ ", [" ++
            commas ((a :: hops.map Prod.snd).map fun k => quote (trap k)) ++ "])"]

def Gate.studio (p : Policy) (home : Nat → Nat) : Gate → List String
  | .one n q => ["p.gate(" ++ quote n.toUpper ++ ", [], [" ++ quote (trap (home q)) ++ "])"]
  | .cx c t =>
      shuttle c (home c) (p.go home c t) ++
      ["p.cool()", "p.gate(\"CX\", [[" ++ quote (ion c) ++ ", " ++ quote (ion t) ++ "]])"] ++
      shuttle c (home t) (p.ret home c t)
  | .measure q => ["p.measure([" ++ quote (ion q) ++ "])"]

def studioLines (p : Policy) (home : Nat → Nat) : List Gate → List String
  | [] => []
  | g :: gs => g.studio p home ++ studioLines p home gs

def studioWith (p : Policy) (home : Nat → Nat) (c : Circuit) : Studio :=
  ⟨"chain4", lines (("p.init({" ++ commas ((List.range c.qubits).map fun k =>
      quote (ion k) ++ ": " ++ quote (trap (home k))) ++ "})") :: studioLines p home c.body)⟩

def bellStudio : Studio := studioWith honest id4 bell

#eval IO.println bellStudio.src

/-!
And the studio plays it, with its own rule check under it. Edit `id4` or `bell` above and both
figures follow.

{{studio bellStudio}}

## 6. Proving what it wrote

Every certificate this compiler writes is a value the checker can decide. For a small one the
kernel decides it, exactly as in F1.

#### Exercise 3 (★) `bell_holds`
Prove that the compiled Bell certificate holds.
-/

theorem bell_holds : Implements bellCert :=
  check_sound (by decide +kernel)

/-!
#### Exercise 4 (★) `ghz_hold`
Prove that the GHZ-3 and GHZ-4 certificates hold (both, with `⟨_, _⟩`).
-/

def ghz3Cert : Input := compile id4 ghz3
def ghz4Cert : Input := compile id4 ghz4

theorem ghz_hold : Implements ghz3Cert ∧ Implements ghz4Cert :=
  ⟨check_sound (by decide +kernel), check_sound (by decide +kernel)⟩

/-!
The GHZ-4 programme in the studio: each CNOT's control walks one trap right, the gate fires, and
it walks back.
-/

def ghz4Studio : Studio := studioWith honest id4 ghz4

/-!
{{studio ghz4Studio}}

The clock for GHZ-4: the Hadamard at cycle 0; the first CNOT's hop at 1, gate at 2, hop home at
3; the second's at 4, 5, 6; the third's at 7, 8, 9. Step the figure to cycle 8, the last CNOT.

{{figure ghz4Cert at=8}}

#### Exercise 5 (★) `ghz4_meets`
Prove that at the start of cycle 8 both `q2` and `q3` are in `C3`, by computing the replay.
-/

theorem ghz4_meets :
    look (posAt ghz4Cert 8) "q2" = some "C3" ∧ look (posAt ghz4Cert 8) "q3" = some "C3" :=
  ⟨by decide +kernel, by decide +kernel⟩

/-!
#### Exercise 6 (★) `far_swap_holds`
A circuit of your own: three CNOTs between qubit 0 and qubit 3 swap them, and every one needs
the longest route `chain4` has. Prove that the compiler's certificate for it holds.
-/

def farSwap : Circuit :=
  ⟨"far_swap", 4, [.cx 0 3, .cx 3 0, .cx 0 3, .measure 0, .measure 3]⟩

theorem far_swap_holds : Implements (compile id4 farSwap) :=
  check_sound (by decide +kernel)

#eval (compile id4 farSwap).moves.length

/-!
## 7. Breaking it

A checker earns trust by what it refuses. Here are three broken routers, made by changing the
policy, and what the checker makes of each.

**The jumper.** Its route from `a` to `b` is a single hop, however far apart they are.
-/

def jump (a b : Nat) : List (Nat × Nat) := if a = b then [] else [(a, b)]
def jumper : Policy := ⟨jump, true⟩
def jumpCert : Input := compileWith jumper id4 farSwap

/-!
Its first move takes `q0` from `C0` straight to `C3`. The device has no such hop, and the studio,
asked to make it, finds no rail between the two.

{{figure jumpCert}}

#### Exercise 7 (★★) `jump_refused`
Prove that the checker refuses the jumper's certificate, with `rejects_teleport` (F1, Exercise 6).
The move is `⟨0, "q0", "C0", "C3"⟩`, the first of the certificate's moves.

Two side conditions. That the pair `("C0", "C3")` is not a hop is a computation. That the move is
in the list you prove by hand: `List.Mem.head _` proves that a list's first element is in it,
and `.tail _ p` that an element of the tail is in the list, given `p`. Lean computes the list to
match them. (`decide +kernel` would have to compare two `Move`s, and in this page's Lean it
cannot; it compares the strings and numbers inside them, which is all `check` needs.)
-/

theorem jump_refused : check jumpCert = false :=
  rejects_teleport (m := ⟨0, "q0", "C0", "C3"⟩) (List.Mem.head _) (by decide +kernel)

/-!
**The forgetful router.** It never carries an ion home, yet it still plans every route from the
ion's home, as if it had. On a circuit where one qubit is the control of two CNOTs, its second
route starts from `C0` while `q0` is in `C1`.
-/

def forgetful : Policy := ⟨route, false⟩
def fan : Circuit := ⟨"fan", 3, [.cx 0 1, .cx 0 2, .measure 0, .measure 1, .measure 2]⟩
def fanCert : Input := compileWith forgetful id4 fan

#eval diagnose fanCert

/-!
The studio, playing the same moves, stops at that hop too: its stage says the programme is
invalid, because `q0` is declared to move from `C0` but is at `C1`.

{{figure fanCert}}

#### Exercise 8 (★★) `forgetful_refused`
Prove it refused, with `rejects_wrong_departure`: a move that departs from a trap its ion is not
in. The move is the second CNOT's first hop, `⟨2, "q0", "C0", "C1"⟩`, the second move of the
certificate (`#eval fanCert.moves` lists them).
-/

#check @rejects_wrong_departure
#eval fanCert.moves

theorem forgetful_refused : check fanCert = false :=
  rejects_wrong_departure (m := ⟨2, "q0", "C0", "C1"⟩) (.tail _ (.head _)) (by decide +kernel)

/-!
**The pile-up.** The same forgetful router, on a circuit where two different ions visit the same
target: `q0` goes to `C1` for the first CNOT and stays; then `q2` comes to `C1` for the second.
Every move departs from where its ion is, and every witness's ions are together in a trap that can
gate. The certificate holds. But `C1` now holds three ions, and its capacity is two.
-/

def pile : Circuit := ⟨"pile", 3, [.cx 0 1, .cx 2 1, .measure 0, .measure 1, .measure 2]⟩
def pileCert : Input := compileWith forgetful id4 pile

/-!
{{figure pileCert at=4}}

#### Exercise 9 (★★) `pile_accepted`
Prove that the pile-up's certificate holds.
-/

theorem pile_accepted : Implements pileCert :=
  check_sound (by decide +kernel)

/-!
That is not a flaw in the checker; it is what the checker is for. `Implements` (F1, section 3)
has no condition about capacity. Capacity is rule R1, "occupancy(site) <= site.capacity at every
instant", one of the rules of Part E, which the studio and the grader
check by replaying the programme. The certificate says where every ion is and that each gate's
own ions meet; the rules say whether the machine can do it. A compiler needs both, and so does
the verdict. Your router keeps capacity by its own promise (section 3), and Part E's rules are how
anyone else finds out whether it did.

## 8. A fact about every circuit

The proofs above are about one certificate each. The last one is about the compiler: whatever
circuit it is given, the ops it writes are numbered `0, 1, 2, ...` with no gap or repeat, which is
what the semantic check requires of a certificate's copy of the circuit (lesson I4).

#### Exercise 10 (★★★) `ops_numbered`
Prove that `opsFrom i gs` numbers its ops `i, i + 1, ...`: its indices are `List.range' i n`, the
`n` numbers from `i`, where `n` is the length of `gs`. Use `induction gs generalizing i`: the step
needs the claim for `i + 1`, so `i` must vary. Then `simp` with the definitions and the induction
hypothesis.
-/

theorem ops_numbered (i : Nat) (gs : List Gate) :
    (opsFrom i gs).map Op.idx = List.range' i gs.length := by
  induction gs generalizing i with
  | nil => rfl
  | cons g gs ih => simp [opsFrom, List.range', ih]

/-!
Lesson I2 proves much more than this about the same compiler, for every circuit at once: that
every certificate it writes for a circuit on `chain4` holds.
-/
