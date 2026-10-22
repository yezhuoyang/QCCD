import QCCDC.Cert.Sound

/-!
# Correct by construction

*Part I, lesson I2. You need lesson I1: this lesson proves its compiler correct.*

In lesson I1 the compiler wrote a certificate and the kernel checked it: one circuit, one check.
That is **translation validation**, and it says nothing about the next circuit until that one is
compiled and checked in turn.

This lesson proves one theorem about the compiler itself. For every circuit it can be given (up to
a size), and every placement, the certificate it writes satisfies `Implements`, the whole of the
checker's specification from lesson F1. That is what a **verified compiler** is: the proof covers
every run before any run happens, so no output needs checking. The classic example is CompCert,
a C compiler proved correct in Coq; ours is smaller, and its proof runs in this page, in Lean core
alone.

The compiler of lesson I1 is restated, unchanged, in a block this page runs and does not show:
`Gate`, `Circuit`, `opsFrom`, `trap`, `ion`, `chain4Sites`, `chain4Hops`, `right`, `left`,
`route`, `Policy`, `honest`, `walk`, `Gate.cycles`, `Gate.moves`, `Gate.witness`, `movesFrom`,
`witnessesFrom`, `compileWith`, `compile` and `id4`. Only its studio half is left out: everything
proved here is about the certificate.
-/

open QCCDC.Cert

-- setup
inductive Gate where
  | one (name : String) (q : Nat)
  | cx (c t : Nat)
  | measure (q : Nat)
  deriving Repr

structure Circuit where
  name : String
  qubits : Nat
  body : List Gate

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

def id4 : Nat → Nat := fun k => k

def right (a : Nat) : Nat → List (Nat × Nat)
  | 0 => []
  | n + 1 => (a, a + 1) :: right (a + 1) n

def left (a : Nat) : Nat → List (Nat × Nat)
  | 0 => []
  | n + 1 => (a, a - 1) :: left (a - 1) n

def route (a b : Nat) : List (Nat × Nat) :=
  if a ≤ b then right a (b - a) else left a (a - b)

structure Policy where
  route : Nat → Nat → List (Nat × Nat)
  back : Bool

def honest : Policy := ⟨route, true⟩

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

def compileWith (p : Policy) (home : Nat → Nat) (c : Circuit) : Input :=
  { circuit := c.name, arch := "chain4", nQubits := c.qubits,
    qmap := (List.range c.qubits).map fun k => (k, ion k),
    init := (List.range c.qubits).map fun k => (ion k, trap (home k)),
    ops := opsFrom 0 c.body,
    moves := movesFrom p home 0 c.body, rots := [],
    gates := witnessesFrom p home 0 0 c.body, unrealised := [],
    gateSites := chain4Sites, hops := chain4Hops, loops := [] }

def compile (home : Nat → Nat) (c : Circuit) : Input := compileWith honest home c

/-!
## 1. The theorem

Here is what this lesson proves, stated first; the rest of the page is its proof.

```
theorem compile_correct (home : Nat → Nat) (c : Circuit)
    (hn : c.qubits ≤ 8)
    (hhome : ∀ k ∈ List.range c.qubits, home k < 4)
    (hq : ∀ g ∈ c.body, ∀ q ∈ g.qubits, q < c.qubits) :
    Implements (compile home c)
```

Read the three hypotheses as the fragment the theorem covers:

* `hn`: at most eight qubits. `chain4` holds eight ions, two in each of four traps. The bound is
  used only to know that the ion names `q0` ... `q7` are different from each other.
* `hhome`: the placement puts every qubit in a trap of `chain4`, `C0` to `C3`.
* `hq`: every statement names only qubits the circuit declares.

Inside the fragment, every circuit `Gate` can write: one-qubit gates by any name, CNOTs between
any two qubits however far apart their traps are, measurements anywhere, of any length. And the
conclusion is all eleven conditions of `Implements`, the same proposition `check_sound` concludes
in F1.

What it does **not** say is as important, and section 8 lists it. In short: it is about the
certificate and nothing else.

A circuit to keep in view, from I1.
-/

def ghz4 : Circuit :=
  ⟨"ghz4", 4, [.one "h" 0, .cx 0 1, .cx 1 2, .cx 2 3,
               .measure 0, .measure 1, .measure 2, .measure 3]⟩

/-!
## 2. The replay, as a fold

The checker replays a certificate with snapshots (`posAt`, `replay`, `stateIn` in F1). To reason
about every certificate at once, we want a plainer description: the positions at the start of
cycle `t` are the initial placement with every move before `t` applied in order. `applyMoves`
applies a list of moves; `List.takeWhile p l` is the longest prefix of `l` whose elements all
satisfy `p`.
-/

/-- Apply moves one after another, starting from `st`. -/
def applyMoves (st : List (String × String)) (ms : List Move) : List (String × String) :=
  ms.foldl (fun s m => upd s m.ion m.dst) st

/-!
With no rotations, the checker's event list is just the moves (`merge` with nothing to merge).
Then the replay's state at cycle `t` is the fold of the moves up to the first one at or after `t`.
The proof is by induction on the move list, with the starting state `st` allowed to vary (a
recursive theorem: the `| m :: ms, st =>` case uses itself on `ms`).
-/

theorem merge_nil (ms : List Move) : merge ms [] = ms.map Ev.mv := by
  cases ms <;> simp [merge]

theorem stateIn_fold (L : List (String × List String)) (t : Nat) :
    ∀ (ms : List Move) (st : List (String × String)),
      stateIn (snapshots L st (ms.map Ev.mv), finalPos L st (ms.map Ev.mv)) t =
        applyMoves st (ms.takeWhile (fun m => decide (m.t < t)))
  | [], st => by simp [stateIn, snapshots, finalPos, applyMoves]
  | m :: ms, st => by
    by_cases h : t ≤ m.t
    · have h' : ¬ m.t < t := by omega
      simp [stateIn, snapshots, Ev.cyc, h, h', applyMoves]
    · have h' : m.t < t := by omega
      have ih := stateIn_fold L t ms (upd st m.ion m.dst)
      simp [stateIn, snapshots, finalPos, Ev.cyc, Ev.apply, h, h', applyMoves] at ih ⊢
      exact ih

theorem posAt_fold (inp : Input) (hr : inp.rots = []) (t : Nat) :
    posAt inp t = applyMoves inp.init (inp.moves.takeWhile (fun m => decide (m.t < t))) := by
  simp only [posAt, replay, events, hr, merge_nil]
  exact stateIn_fold inp.loops t inp.moves inp.init

/-!
`posAt_fold` is a fact about the checker, not about our compiler: any proof about any compiler
without rotations can start from it. Notice that it needs no sortedness. If the moves were out of
order, the fold would stop at the first late one, exactly as the checker's snapshots do.

The positions are association lists, read with `look` and written with `upd` (F1). What `upd`
does to a later `look` is the one fact we need about them: the key just written answers with its
new value, every other key as before.
-/

theorem look_cons (a b : String) (l : List (String × String)) (x : String) :
    look ((a, b) :: l) x = if a = x then some b else look l x := by
  by_cases h : a = x
  · simp [look, List.find?, h]
  · simp [look, List.find?, h]

theorem look_filter (k x : String) (h : x ≠ k) :
    ∀ l : List (String × String), look (l.filter (fun p => p.1 ≠ k)) x = look l x
  | [] => rfl
  | (a, b) :: l => by
    have ih := look_filter k x h l
    by_cases ha : a = k
    · have hax : ¬ a = x := fun e => h (e ▸ ha)
      have : ((a, b) :: l).filter (fun p => p.1 ≠ k) = l.filter (fun p => p.1 ≠ k) := by
        simp [List.filter, ha]
      rw [this, ih, look_cons, if_neg hax]
    · have : ((a, b) :: l).filter (fun p => p.1 ≠ k) = (a, b) :: l.filter (fun p => p.1 ≠ k) := by
        simp [List.filter, ha]
      rw [this, look_cons, look_cons, ih]

theorem look_upd (l : List (String × String)) (k v x : String) :
    look (upd l k v) x = if x = k then some v else look l x := by
  unfold upd
  rw [look_cons]
  by_cases h : x = k
  · rw [if_pos h.symm, if_pos h]
  · rw [if_neg (fun e => h e.symm), if_neg h, look_filter k x h]

/-!
Three facts about `takeWhile` on a joined list, and one about `applyMoves`: a prefix that passes
the test is kept whole; a list that fails it from the start gives nothing; and a list in which some
element fails stops inside it.
-/

theorem takeWhile_all {α} (p : α → Bool) : ∀ (l₁ l₂ : List α), (∀ x ∈ l₁, p x = true) →
    (l₁ ++ l₂).takeWhile p = l₁ ++ l₂.takeWhile p
  | [], _, _ => rfl
  | a :: l₁, l₂, h => by
    have ha : p a = true := h a (by simp)
    simp [List.takeWhile, ha]
    exact takeWhile_all p l₁ l₂ (fun x hx => h x (by simp [hx]))

theorem takeWhile_none {α} (p : α → Bool) : ∀ (l : List α), (∀ x ∈ l, p x = false) →
    l.takeWhile p = []
  | [], _ => rfl
  | a :: _, h => by simp [List.takeWhile, h a (by simp)]

theorem takeWhile_stop {α} (p : α → Bool) : ∀ (l₁ l₂ : List α), (∃ x ∈ l₁, p x = false) →
    (l₁ ++ l₂).takeWhile p = l₁.takeWhile p
  | [], _, ⟨_, hx, _⟩ => by simp at hx
  | a :: l₁, l₂, ⟨x, hx, hpx⟩ => by
    by_cases ha : p a = true
    · have : ∃ x ∈ l₁, p x = false := by
        simp at hx
        rcases hx with rfl | hx
        · rw [ha] at hpx; exact absurd hpx (by simp)
        · exact ⟨x, hx, hpx⟩
      simp [List.takeWhile, ha]
      exact takeWhile_stop p l₁ l₂ this
    · simp at ha
      simp [List.takeWhile, ha]

theorem applyMoves_append (st : List (String × String)) (l₁ l₂ : List Move) :
    applyMoves st (l₁ ++ l₂) = applyMoves (applyMoves st l₁) l₂ := by
  simp [applyMoves, List.foldl_append]

theorem applyMoves_cons (st : List (String × String)) (m : Move) (ms : List Move) :
    applyMoves st (m :: ms) = applyMoves (upd st m.ion m.dst) ms := rfl

/-!
## 3. The shape of the output

Next, what each piece of the certificate looks like. A walk's moves all move one ion, at the
cycles `t`, `t + 1`, ..., and each is one of the walk's hops; a statement's moves and witness
fall inside its own cycles `[T, T + cycles)`; nothing in the rest of the circuit comes earlier.
`omega` proves goals of linear arithmetic over `Nat` from the hypotheses in view; `rcases` and
`obtain` take a hypothesis apart (`rfl` in a pattern substitutes an equation away).
-/

theorem walk_spec (q : Nat) : ∀ (t : Nat) (hops : List (Nat × Nat)) (m : Move), m ∈ walk q t hops →
    m.ion = ion q ∧ t ≤ m.t ∧ m.t < t + hops.length ∧
      ∃ h ∈ hops, m.src = trap h.1 ∧ m.dst = trap h.2
  | _, [], _, hm => by simp [walk] at hm
  | t, (a, b) :: rest, m, hm => by
    simp only [walk, List.mem_cons] at hm
    rcases hm with rfl | hm
    · exact ⟨rfl, Nat.le_refl _, by simp, (a, b), by simp, rfl, rfl⟩
    · obtain ⟨h1, h2, h3, h, hh, h4, h5⟩ := walk_spec q (t + 1) rest m hm
      exact ⟨h1, by omega, by simp; omega, h, by simp [hh], h4, h5⟩

theorem moves_spec (p : Policy) (home : Nat → Nat) (T : Nat) (g : Gate) (m : Move)
    (hm : m ∈ g.moves p home T) : T ≤ m.t ∧ m.t < T + g.cycles p home := by
  cases g with
  | one n q => simp [Gate.moves] at hm
  | measure q => simp [Gate.moves] at hm
  | cx c t =>
    simp only [Gate.moves, List.mem_append] at hm
    rcases hm with hm | hm
    · have := walk_spec c _ _ m hm
      simp only [Gate.cycles]; omega
    · have := walk_spec c _ _ m hm
      simp only [Gate.cycles]; omega

theorem movesFrom_ge (p : Policy) (home : Nat → Nat) :
    ∀ (T : Nat) (gs : List Gate) (m : Move), m ∈ movesFrom p home T gs → T ≤ m.t
  | _, [], _, hm => by simp [movesFrom] at hm
  | T, g :: gs, m, hm => by
    simp only [movesFrom, List.mem_append] at hm
    rcases hm with hm | hm
    · exact (moves_spec p home T g m hm).1
    · have := movesFrom_ge p home _ gs m hm; omega

theorem witness_spec (p : Policy) (home : Nat → Nat) (i T : Nat) (g : Gate) (w : GateW)
    (hw : w ∈ g.witness p home i T) : w.dag = i ∧ T ≤ w.t ∧ w.t < T + g.cycles p home := by
  cases g with
  | one n q => simp [Gate.witness] at hw; subst hw; simp [Gate.cycles]
  | measure q => simp [Gate.witness] at hw
  | cx c t => simp [Gate.witness] at hw; subst hw; simp [Gate.cycles]; omega

theorem witnessesFrom_ge (p : Policy) (home : Nat → Nat) :
    ∀ (i T : Nat) (gs : List Gate) (w : GateW), w ∈ witnessesFrom p home i T gs →
      i ≤ w.dag ∧ T ≤ w.t
  | _, _, [], _, hw => by simp [witnessesFrom] at hw
  | i, T, g :: gs, w, hw => by
    simp only [witnessesFrom, List.mem_append] at hw
    rcases hw with hw | hw
    · have := witness_spec p home i T g w hw; omega
    · have := witnessesFrom_ge p home _ _ gs w hw; omega

theorem opsFrom_ge : ∀ (i : Nat) (gs : List Gate) (o : Op), o ∈ opsFrom i gs → i ≤ o.idx
  | _, [], _, ho => by simp [opsFrom] at ho
  | i, g :: gs, o, ho => by
    simp only [opsFrom, List.mem_cons] at ho
    rcases ho with rfl | ho
    · exact Nat.le_refl _
    · have := opsFrom_ge (i + 1) gs o ho; omega

/-!
The finite facts, which the kernel settles by trying every case: the eight ion names are
different, the four trap names are gate sites, and every route between two traps of `chain4` is
made of its hops (I1, Exercise 2). `List.mem_range` turns `k ∈ List.range n` into `k < n`.
-/

theorem ion_inj8 : ∀ a ∈ List.range 8, ∀ b ∈ List.range 8, ion a = ion b → a = b := by
  decide +kernel

theorem ion_inj {a b : Nat} (ha : a < 8) (hb : b < 8) (h : ion a = ion b) : a = b :=
  ion_inj8 a (List.mem_range.2 ha) b (List.mem_range.2 hb) h

theorem sites4 : ∀ k ∈ List.range 4, trap k ∈ chain4Sites := by decide +kernel

theorem hops4 : ∀ a ∈ List.range 4, ∀ b ∈ List.range 4, ∀ h ∈ route a b,
    (trap h.1, trap h.2) ∈ chain4Hops := by
  decide +kernel

/-!
And the map: looking a qubit up in `qmap` finds its own ion, and looking an ion up in `init`
finds its home trap. The second needs the names to be different, hence the bound eight.
-/

theorem ionOf_range (n q : Nat) (hq : q < n) :
    ((((List.range n).map fun k => (k, ion k)).find? (fun p => p.1 = q)).map Prod.snd) =
      some (ion q) := by
  have key : ∀ (l : List Nat), q ∈ l →
      (((l.map fun k => (k, ion k)).find? (fun p => p.1 = q)).map Prod.snd) = some (ion q) := by
    intro l
    induction l with
    | nil => intro h; simp at h
    | cons j l ih =>
      intro h
      by_cases hj : j = q
      · subst hj; simp [List.find?]
      · have : q ∈ l := by
          simp at h; rcases h with h | h
          · exact absurd h.symm hj
          · exact h
        simp [List.find?, hj]
        simpa using ih this
  exact key _ (List.mem_range.2 hq)

theorem look_init (home : Nat → Nat) (n k : Nat) (hn : n ≤ 8) (hk : k < n) :
    look ((List.range n).map fun j => (ion j, trap (home j))) (ion k) = some (trap (home k)) := by
  have key : ∀ (l : List Nat), k ∈ l → (∀ j ∈ l, j < 8) →
      look (l.map fun j => (ion j, trap (home j))) (ion k) = some (trap (home k)) := by
    intro l
    induction l with
    | nil => intro h; simp at h
    | cons j l ih =>
      intro h hl
      simp only [List.map]
      rw [look_cons]
      by_cases hj : ion j = ion k
      · have := ion_inj (hl j (by simp)) (by omega) hj
        subst this; simp
      · rw [if_neg hj]
        apply ih
        · simp at h; rcases h with h | h
          · exact absurd (h ▸ rfl) hj
          · exact h
        · intro x hx; exact hl x (by simp [hx])
  exact key _ (List.mem_range.2 hk) (fun j hj => by have := List.mem_range.1 hj; omega)

/-!
## 4. Order and numbering, for every policy

Four of the eleven conditions are about bookkeeping: the moves come in cycle order
(`EventsSorted`), every op that needs a witness has exactly one (`Covered`), every witness names an
op (`Grounded`), and ops are realised in program order (`RespectsOrder`). They follow from how the
compiler numbers ops and advances its clock, and not at all from where ions go. So they are
proved here for **every** policy `p`, the jumper and the forgetful router of I1 included.

`EventsSorted` first. `Inc lo l` says the moves of `l` are at or after cycle `lo`, each strictly
after the one before; it survives joining two lists end to end, and it is all the checker's
`sortedByT` asks.
-/

def Inc : Nat → List Move → Prop
  | _, [] => True
  | lo, m :: ms => lo ≤ m.t ∧ Inc (m.t + 1) ms

theorem Inc.mono : ∀ {lo lo' : Nat} {l : List Move}, lo' ≤ lo → Inc lo l → Inc lo' l
  | _, _, [], _, _ => trivial
  | _, _, _ :: _, _, ⟨h1, h2⟩ => ⟨by omega, h2⟩

theorem Inc.append : ∀ {lo mid : Nat} {l₁ l₂ : List Move}, Inc lo l₁ → (∀ m ∈ l₁, m.t < mid) →
    lo ≤ mid → Inc mid l₂ → Inc lo (l₁ ++ l₂)
  | _, _, [], _, _, _, hlo, h₂ => Inc.mono hlo h₂
  | _, _, m :: l₁, _, ⟨h1, h2⟩, hb, _, h₂ =>
    ⟨h1, Inc.append h2 (fun x hx => hb x (by simp [hx])) (hb m (by simp)) h₂⟩

theorem walk_inc (q : Nat) : ∀ (t : Nat) (hops : List (Nat × Nat)), Inc t (walk q t hops)
  | _, [] => trivial
  | t, (_, _) :: rest => ⟨Nat.le_refl _, walk_inc q (t + 1) rest⟩

theorem moves_inc (p : Policy) (home : Nat → Nat) (T : Nat) (g : Gate) :
    Inc T (g.moves p home T) := by
  cases g with
  | one n q => trivial
  | measure q => trivial
  | cx c t =>
    simp only [Gate.moves]
    exact Inc.append (walk_inc c T _) (fun m hm => by have := walk_spec c _ _ m hm; omega)
      (by omega) (walk_inc c _ _)

theorem movesFrom_inc (p : Policy) (home : Nat → Nat) :
    ∀ (T : Nat) (gs : List Gate), Inc T (movesFrom p home T gs)
  | _, [] => trivial
  | T, g :: gs => by
    simp only [movesFrom]
    exact Inc.append (moves_inc p home T g) (fun m hm => (moves_spec p home T g m hm).2)
      (by omega) (movesFrom_inc p home _ gs)

theorem sorted_of_inc : ∀ (lo : Nat) (l : List Move), Inc lo l → sortedByT (l.map Ev.mv) = true
  | _, [], _ => rfl
  | _, [_], _ => rfl
  | _, m :: m' :: rest, ⟨_, h1, h2⟩ => by
    have ih := sorted_of_inc (m.t + 1) (m' :: rest) ⟨h1, h2⟩
    simp only [List.map, sortedByT, Ev.cyc] at ih ⊢
    simp only [ih, Bool.and_true]
    exact decide_eq_true (by omega)

/-!
`Covered`: the witnesses for op `i` all come from statement `i` (later statements have larger
indices), and statement `i` writes exactly one unless it is a measurement, which needs none.
-/

theorem filter_none {α} (p : α → Bool) : ∀ (l : List α), (∀ x ∈ l, p x = false) → l.filter p = []
  | [], _ => rfl
  | a :: l, h => by
    simp only [List.filter, h a (by simp)]
    exact filter_none p l (fun x hx => h x (by simp [hx]))

theorem covered_from (p : Policy) (home : Nat → Nat) :
    ∀ (gs : List Gate) (i T : Nat), ∀ o ∈ opsFrom i gs, needsWitness o = true →
      ((witnessesFrom p home i T gs).filter (fun w => w.dag = o.idx)).length = 1
  | [], _, _, o, ho, _ => by simp [opsFrom] at ho
  | g :: gs, i, T, o, ho, hn => by
    simp only [opsFrom, List.mem_cons] at ho
    simp only [witnessesFrom, List.filter_append, List.length_append]
    rcases ho with rfl | ho
    · have rest : (witnessesFrom p home (i + 1) (T + g.cycles p home) gs).filter
          (fun w => w.dag = i) = [] :=
        filter_none _ _ (fun w hw => by
          have := witnessesFrom_ge p home _ _ gs w hw; simp; omega)
      rw [rest]
      cases g with
      | one n q => simp [Gate.witness]
      | cx c t => simp [Gate.witness]
      | measure q => simp [needsWitness, Gate.name] at hn
    · have hi := opsFrom_ge (i + 1) gs o ho
      have first : (g.witness p home i T).filter (fun w => w.dag = o.idx) = [] :=
        filter_none _ _ (fun w hw => by
          have := witness_spec p home i T g w hw; simp; omega)
      simp [first, covered_from p home gs (i + 1) _ o ho hn]

/-!
`Grounded`: each witness comes from a statement, whose op carries its index. `RespectsOrder`: a
witness for a later op is at a later cycle, because each statement's cycles end before the next
one's begin. (The checker only asks this of ops that share a qubit; we prove it of all.)
-/

theorem grounded_from (p : Policy) (home : Nat → Nat) :
    ∀ (gs : List Gate) (i T : Nat), ∀ w ∈ witnessesFrom p home i T gs,
      ∃ o ∈ opsFrom i gs, o.idx = w.dag
  | [], _, _, w, hw => by simp [witnessesFrom] at hw
  | g :: gs, i, T, w, hw => by
    simp only [witnessesFrom, List.mem_append] at hw
    rcases hw with hw | hw
    · exact ⟨⟨i, g.name, g.qubits⟩, by simp [opsFrom], (witness_spec p home i T g w hw).1.symm⟩
    · obtain ⟨o, ho, h⟩ := grounded_from p home gs (i + 1) _ w hw
      exact ⟨o, by simp [opsFrom, ho], h⟩

theorem order_from (p : Policy) (home : Nat → Nat) :
    ∀ (gs : List Gate) (i T : Nat), ∀ w ∈ witnessesFrom p home i T gs,
      ∀ w' ∈ witnessesFrom p home i T gs, w.dag < w'.dag → w.t < w'.t
  | [], _, _, w, hw, _, _, _ => by simp [witnessesFrom] at hw
  | g :: gs, i, T, w, hw, w', hw', hlt => by
    simp only [witnessesFrom, List.mem_append] at hw hw'
    rcases hw with hw | hw <;> rcases hw' with hw' | hw'
    · have := witness_spec p home i T g w hw
      have := witness_spec p home i T g w' hw'
      omega
    · have := witness_spec p home i T g w hw
      have := witnessesFrom_ge p home _ _ gs w' hw'
      omega
    · have := witnessesFrom_ge p home _ _ gs w hw
      have := witness_spec p home i T g w' hw'
      omega
    · exact order_from p home gs (i + 1) _ w hw w' hw' hlt

/-!
`OwnIons`, for every policy too: a witness for op `i` names exactly the ions of op `i`'s qubits,
given a map `f` that sends each declared qubit to its own ion (`ionOf_range` is that map).
-/

theorem own_from (p : Policy) (home : Nat → Nat) (f : Nat → Option String) (n : Nat)
    (hf : ∀ q, q < n → f q = some (ion q)) :
    ∀ (gs : List Gate) (i T : Nat), (∀ g ∈ gs, ∀ q ∈ g.qubits, q < n) →
      ∀ w ∈ witnessesFrom p home i T gs, ∀ o ∈ opsFrom i gs, o.idx = w.dag →
        (w.ions.map some).Perm (o.qubits.map f)
  | [], _, _, _, w, hw, _, _, _ => by simp [witnessesFrom] at hw
  | g :: gs, i, T, hq, w, hw, o, ho, he => by
    simp only [witnessesFrom, List.mem_append] at hw
    simp only [opsFrom, List.mem_cons] at ho
    have hq0 : ∀ q ∈ g.qubits, q < n := hq g (by simp)
    rcases hw with hw | hw <;> rcases ho with rfl | ho
    · cases g with
      | one nm q =>
        simp [Gate.witness] at hw; subst hw
        simp [Gate.qubits] at hq0
        simp [Gate.qubits, hf q hq0]
      | cx c t =>
        simp [Gate.witness] at hw; subst hw
        simp [Gate.qubits] at hq0
        simp [Gate.qubits, hf c hq0.1, hf t hq0.2]
      | measure q => simp [Gate.witness] at hw
    · have := witness_spec p home i T g w hw
      have := opsFrom_ge (i + 1) gs o ho
      omega
    · have := witnessesFrom_ge p home _ _ gs w hw
      simp at he; omega
    · exact own_from p home f n hf gs (i + 1) _ (fun g' hg' => hq g' (by simp [hg'])) w hw o ho he

/-!
## 5. Transport, for the honest router

The two remaining halves are about where ions really are: every move departs from the trap the
replay has its ion in (half of `MovesAreHops`), and every witness's ions are in its trap at its
cycle (half of `GatesLegal`). These depend on the route, and they are false for the jumper and the
forgetful router. From here on the policy is `honest`.

A route is **chained**: each hop starts where the last one ended. `endOf a hops` is where a chain
from `a` ends, and a route from `a` to `b` ends at `b`.
-/

def Chained : Nat → List (Nat × Nat) → Prop
  | _, [] => True
  | a, (x, y) :: rest => x = a ∧ Chained y rest

def endOf : Nat → List (Nat × Nat) → Nat
  | a, [] => a
  | _, (_, y) :: rest => endOf y rest

theorem right_spec : ∀ (a n : Nat), Chained a (right a n) ∧ endOf a (right a n) = a + n
  | a, 0 => ⟨trivial, rfl⟩
  | a, n + 1 => by
    have ih := right_spec (a + 1) n
    exact ⟨⟨rfl, ih.1⟩, by simp only [right, endOf, ih.2]; omega⟩

theorem left_spec : ∀ (a n : Nat), n ≤ a → Chained a (left a n) ∧ endOf a (left a n) = a - n
  | a, 0, _ => ⟨trivial, by simp [left, endOf]⟩
  | a, n + 1, h => by
    have ih := left_spec (a - 1) n (by omega)
    exact ⟨⟨rfl, ih.1⟩, by simp only [left, endOf, ih.2]; omega⟩

theorem route_spec (a b : Nat) : Chained a (route a b) ∧ endOf a (route a b) = b := by
  unfold route
  by_cases h : a ≤ b
  · rw [if_pos h]; have := right_spec a (b - a); exact ⟨this.1, by rw [this.2]; omega⟩
  · rw [if_neg h]; have := left_spec a (a - b) (by omega); exact ⟨this.1, by rw [this.2]; omega⟩

theorem route_bound (a b : Nat) (ha : a < 4) (hb : b < 4) :
    ∀ h ∈ route a b, (trap h.1, trap h.2) ∈ chain4Hops :=
  hops4 a (List.mem_range.2 ha) b (List.mem_range.2 hb)

/-!
Walking a chain moves one ion and no other. `walk_effect`: after the whole walk, the ion is at
the chain's end and every other ion is where it was. `walk_departs`: each move of the walk departs
from where the walk so far has put the ion.
-/

theorem walk_effect (q : Nat) : ∀ (t a : Nat) (hops : List (Nat × Nat)) (st : List (String × String)),
    Chained a hops → look st (ion q) = some (trap a) → ∀ x,
      look (applyMoves st (walk q t hops)) x =
        if x = ion q then some (trap (endOf a hops)) else look st x
  | _, a, [], st, _, hst, x => by
    by_cases hx : x = ion q
    · rw [if_pos hx, hx]; exact hst
    · rw [if_neg hx]; rfl
  | t, a, (a', b) :: rest, st, ⟨_, hc⟩, _, x => by
    have hst' : look (upd st (ion q) (trap b)) (ion q) = some (trap b) := by
      rw [look_upd, if_pos rfl]
    have ih := walk_effect q (t + 1) b rest (upd st (ion q) (trap b)) hc hst' x
    show look (applyMoves (upd st (ion q) (trap b)) (walk q (t + 1) rest)) x = _
    rw [ih, look_upd]
    by_cases hx : x = ion q
    · rw [if_pos hx, if_pos hx]; rfl
    · rw [if_neg hx, if_neg hx, if_neg hx]

theorem walk_departs (q : Nat) : ∀ (t a : Nat) (hops : List (Nat × Nat)) (st : List (String × String)),
    Chained a hops → look st (ion q) = some (trap a) → ∀ m ∈ walk q t hops,
      look (applyMoves st ((walk q t hops).takeWhile (fun m' => decide (m'.t < m.t)))) m.ion =
        some m.src
  | _, _, [], _, _, _, m, hm => by simp [walk] at hm
  | t, a, (a', b) :: rest, st, ⟨ha, hc⟩, hst, m, hm => by
    simp only [walk, List.mem_cons] at hm
    rcases hm with rfl | hm
    · have e : (walk q t ((a', b) :: rest)).takeWhile
          (fun m' => decide (m'.t < (⟨t, ion q, trap a', trap b⟩ : Move).t)) = [] := by
        simp [walk, List.takeWhile]
      rw [e]; subst ha; exact hst
    · have hge := (walk_spec q (t + 1) rest m hm).2.1
      have hst' : look (upd st (ion q) (trap b)) (ion q) = some (trap b) := by
        rw [look_upd, if_pos rfl]
      have ih := walk_departs q (t + 1) b rest (upd st (ion q) (trap b)) hc hst' m hm
      have e : (walk q t ((a', b) :: rest)).takeWhile (fun m' => decide (m'.t < m.t)) =
          ⟨t, ion q, trap a', trap b⟩ :: (walk q (t + 1) rest).takeWhile
            (fun m' => decide (m'.t < m.t)) := by
        have : decide (t < m.t) = true := decide_eq_true (by omega)
        simp [walk, List.takeWhile, this]
      rw [e, applyMoves_cons]
      exact ih

/-!
Two ways to cut the move list at a cycle `s`: everything before `s`, then a block that reaches
`s`, then the rest; or everything before `s` and nothing after.
-/

theorem tw_mid (A B C : List Move) (s : Nat) (hA : ∀ m ∈ A, m.t < s) (hB : ∃ m ∈ B, ¬ m.t < s) :
    (A ++ (B ++ C)).takeWhile (fun m => decide (m.t < s)) =
      A ++ B.takeWhile (fun m => decide (m.t < s)) := by
  rw [takeWhile_all _ A _ (fun m hm => decide_eq_true (hA m hm))]
  obtain ⟨m, hm, hs⟩ := hB
  rw [takeWhile_stop _ B C ⟨m, hm, decide_eq_false hs⟩]

theorem tw_end (A C : List Move) (s : Nat) (hA : ∀ m ∈ A, m.t < s) (hC : ∀ m ∈ C, s ≤ m.t) :
    (A ++ C).takeWhile (fun m => decide (m.t < s)) = A := by
  rw [takeWhile_all _ A _ (fun m hm => decide_eq_true (hA m hm)),
      takeWhile_none _ C (fun m hm => decide_eq_false (by have := hC m hm; omega)),
      List.append_nil]

/-!
The heart of the proof is the promise I1 made: at the start of every statement, every ion is at
home. `AtHome home n st` says so of a state `st`. The **invariant** below is proved by induction
over the statements still to compile, carrying the moves already written (`pre`, all before the
clock `T`) and the promise about the state they leave. For each statement it shows the two
transport conditions for the statement's own moves and witness, and that the promise holds again
after it, which is what the induction needs for the rest:

* a one-qubit gate: no moves; its ion is at home, where its witness says;
* a measurement: nothing at all;
* a CNOT: the walk there departs from home and arrives at the target's home (`walk_effect`,
  `route_spec`); the gate's cycle comes after the walk there and before the walk back (`tw_end`),
  so both ions are in the target's trap; the walk back ends at home, and every other ion never
  moved, so the promise holds again.
-/

def AtHome (home : Nat → Nat) (n : Nat) (st : List (String × String)) : Prop :=
  ∀ k, k < n → look st (ion k) = some (trap (home k))

theorem transport (home : Nat → Nat) (n : Nat) (hn : n ≤ 8) (init : List (String × String)) :
    ∀ (gs : List Gate) (i T : Nat) (pre : List Move),
      (∀ g ∈ gs, ∀ q ∈ g.qubits, q < n) →
      (∀ m ∈ pre, m.t < T) →
      AtHome home n (applyMoves init pre) →
      (∀ m ∈ movesFrom honest home T gs,
        look (applyMoves init ((pre ++ movesFrom honest home T gs).takeWhile
          (fun m' => decide (m'.t < m.t)))) m.ion = some m.src) ∧
      (∀ w ∈ witnessesFrom honest home i T gs, ∀ x ∈ w.ions,
        look (applyMoves init ((pre ++ movesFrom honest home T gs).takeWhile
          (fun m' => decide (m'.t < w.t)))) x = some w.site)
  | [], _, _, _, _, _, _ => ⟨by simp [movesFrom], by simp [witnessesFrom]⟩
  | g :: gs, i, T, pre, hq, hpre, hhome => by
    have hqs : ∀ g' ∈ gs, ∀ q ∈ g'.qubits, q < n := fun g' hg' => hq g' (by simp [hg'])
    have hq0 : ∀ q ∈ g.qubits, q < n := hq g (by simp)
    -- the rest of the circuit starts after this statement's moves
    have hrest : ∀ m ∈ movesFrom honest home (T + g.cycles honest home) gs,
        T + g.cycles honest home ≤ m.t := movesFrom_ge honest home _ gs
    have hpre' : ∀ m ∈ pre ++ g.moves honest home T, m.t < T + g.cycles honest home := by
      intro m hm
      rcases List.mem_append.1 hm with hm | hm
      · have := hpre m hm; omega
      · exact (moves_spec honest home T g m hm).2
    have assoc : pre ++ movesFrom honest home T (g :: gs) =
        (pre ++ g.moves honest home T) ++ movesFrom honest home (T + g.cycles honest home) gs := by
      simp [movesFrom, List.append_assoc]
    cases g with
    | measure q =>
      have ih := transport home n hn init gs (i + 1) (T + 0) pre hqs
        (fun m hm => by have := hpre m hm; omega) hhome
      have e : movesFrom honest home T (Gate.measure q :: gs) = movesFrom honest home (T + 0) gs := by
        simp [movesFrom, Gate.moves, Gate.cycles]
      have ew : witnessesFrom honest home i T (Gate.measure q :: gs) =
          witnessesFrom honest home (i + 1) (T + 0) gs := by
        simp [witnessesFrom, Gate.witness, Gate.cycles]
      rw [e, ew]; exact ih
    | one nm q =>
      have ih := transport home n hn init gs (i + 1) (T + 1) pre hqs
        (fun m hm => by have := hpre m hm; omega) hhome
      have e : movesFrom honest home T (Gate.one nm q :: gs) = movesFrom honest home (T + 1) gs := by
        simp [movesFrom, Gate.moves, Gate.cycles]
      rw [e]
      refine ⟨ih.1, ?_⟩
      intro w hw x hx
      simp only [witnessesFrom, Gate.witness, Gate.cycles, List.mem_append, List.mem_cons,
        List.not_mem_nil, or_false] at hw
      rcases hw with rfl | hw
      · simp only [List.mem_cons, List.not_mem_nil, or_false] at hx
        subst hx
        rw [tw_end pre _ T hpre (fun m hm => by have := movesFrom_ge honest home _ gs m hm; omega)]
        simp [Gate.qubits] at hq0
        exact hhome q hq0
      · exact ih.2 w hw x hx
    | cx c t =>
      simp [Gate.qubits] at hq0
      obtain ⟨hc, ht⟩ := hq0
      have hroute1 := route_spec (home c) (home t)
      have hroute2 := route_spec (home t) (home c)
      -- the state at the start of this statement, and after each walk
      have h0 : look (applyMoves init pre) (ion c) = some (trap (home c)) := hhome c hc
      have eff1 := walk_effect c T (home c) (route (home c) (home t)) _ hroute1.1 h0
      rw [hroute1.2] at eff1
      have h1 : look (applyMoves (applyMoves init pre) (walk c T (route (home c) (home t))))
          (ion c) = some (trap (home t)) := by rw [eff1, if_pos rfl]
      have eff2 := walk_effect c (T + (route (home c) (home t)).length + 1) (home t)
        (route (home t) (home c)) _ hroute2.1 h1
      rw [hroute2.2] at eff2
      have hmv : Gate.moves honest home T (Gate.cx c t) =
          walk c T (route (home c) (home t)) ++
            walk c (T + (route (home c) (home t)).length + 1) (route (home t) (home c)) := rfl
      have hhome' : AtHome home n (applyMoves init (pre ++ Gate.moves honest home T (Gate.cx c t))) := by
        intro k hk
        rw [hmv, applyMoves_append, applyMoves_append, eff2]
        by_cases hkc : ion k = ion c
        · rw [if_pos hkc, ion_inj (by omega) (by omega) hkc]
        · rw [if_neg hkc, eff1, if_neg hkc]; exact hhome k hk
      have ih := transport home n hn init gs (i + 1) _ _ hqs hpre' hhome'
      rw [assoc]
      have hw1 : ∀ m ∈ walk c T (route (home c) (home t)),
          T ≤ m.t ∧ m.t < T + (route (home c) (home t)).length := by
        intro m hm; have := walk_spec c T _ m hm; omega
      have hw2 : ∀ m ∈ walk c (T + (route (home c) (home t)).length + 1) (route (home t) (home c)),
          T + (route (home c) (home t)).length + 1 ≤ m.t := by
        intro m hm; have := walk_spec c _ _ m hm; omega
      have hcyc : Gate.cycles honest home (Gate.cx c t) =
          (route (home c) (home t)).length + 1 + (route (home t) (home c)).length := rfl
      refine ⟨?_, ?_⟩
      · intro m hm
        simp only [movesFrom, List.mem_append] at hm
        rcases hm with hm | hm
        · rw [hmv] at hm ⊢
          rcases List.mem_append.1 hm with hm | hm
          · -- a hop on the way there
            rw [List.append_assoc, List.append_assoc,
                tw_mid pre _ _ m.t (fun x hx => by have := hpre x hx; have := hw1 m hm; omega)
                  ⟨m, hm, Nat.lt_irrefl _⟩,
                applyMoves_append]
            exact walk_departs c T (home c) _ _ hroute1.1 h0 m hm
          · -- a hop on the way home
            have hm2 := hw2 m hm
            rw [List.append_assoc, List.append_assoc, ← List.append_assoc pre,
                tw_mid (pre ++ _) _ _ m.t (fun x hx => by
                  rcases List.mem_append.1 hx with hx | hx
                  · have := hpre x hx; omega
                  · have := hw1 x hx; omega) ⟨m, hm, Nat.lt_irrefl _⟩,
                applyMoves_append, applyMoves_append]
            exact walk_departs c _ (home t) _ _ hroute2.1 h1 m hm
        · exact ih.1 m hm
      · intro w hw x hx
        simp only [witnessesFrom, Gate.witness, List.mem_append, List.mem_cons,
          List.not_mem_nil, or_false] at hw
        rcases hw with rfl | hw
        · -- the gate: after the walk there, before the walk home
          rw [hmv, List.append_assoc, List.append_assoc, ← List.append_assoc pre,
              tw_end (pre ++ _) _ _ (fun y hy => by
                rcases List.mem_append.1 hy with hy | hy
                · have := hpre y hy; simp [Policy.go, honest]; omega
                · have := hw1 y hy; simp [Policy.go, honest]; omega)
                (fun y hy => by
                  rcases List.mem_append.1 hy with hy | hy
                  · have := hw2 y hy; simp [Policy.go, honest]; omega
                  · have := hrest y hy; rw [hcyc] at this; simp [Policy.go, honest]; omega),
              applyMoves_append]
          simp only [List.mem_cons, List.not_mem_nil, or_false] at hx
          show look _ x = some (trap (home t))
          rcases hx with rfl | rfl
          · exact h1
          · rw [eff1]
            by_cases htc : ion t = ion c
            · rw [if_pos htc]
            · rw [if_neg htc]; exact hhome t ht
        · exact ih.2 w hw x hx

/-!
## 6. Hops and sites

The device halves of `MovesAreHops` and `GatesLegal`: every move is a hop of `chain4` and every
witness's trap can gate, as long as every qubit's home is one of the four traps.
-/

theorem movesFrom_hops (home : Nat → Nat) (n : Nat) (hhome : ∀ k ∈ List.range n, home k < 4) :
    ∀ (T : Nat) (gs : List Gate), (∀ g ∈ gs, ∀ q ∈ g.qubits, q < n) →
      ∀ m ∈ movesFrom honest home T gs, (m.src, m.dst) ∈ chain4Hops
  | _, [], _, m, hm => by simp [movesFrom] at hm
  | T, g :: gs, hq, m, hm => by
    simp only [movesFrom, List.mem_append] at hm
    rcases hm with hm | hm
    · cases g with
      | one nm q => simp [Gate.moves] at hm
      | measure q => simp [Gate.moves] at hm
      | cx c t =>
        have hq0 := hq (Gate.cx c t) (by simp)
        simp [Gate.qubits] at hq0
        have hc := hhome c (List.mem_range.2 hq0.1)
        have ht := hhome t (List.mem_range.2 hq0.2)
        rcases List.mem_append.1 hm with hm | hm
        · obtain ⟨_, _, _, h, hh, hs, hd⟩ := walk_spec c _ _ m hm
          rw [hs, hd]; exact route_bound _ _ hc ht h hh
        · obtain ⟨_, _, _, h, hh, hs, hd⟩ := walk_spec c _ _ m hm
          rw [hs, hd]; exact route_bound _ _ ht hc h hh
    · exact movesFrom_hops home n hhome _ gs (fun g' hg' => hq g' (by simp [hg'])) m hm

theorem witnessesFrom_sites (home : Nat → Nat) (n : Nat) (hhome : ∀ k ∈ List.range n, home k < 4) :
    ∀ (i T : Nat) (gs : List Gate), (∀ g ∈ gs, ∀ q ∈ g.qubits, q < n) →
      ∀ w ∈ witnessesFrom honest home i T gs, w.site ∈ chain4Sites
  | _, _, [], _, w, hw => by simp [witnessesFrom] at hw
  | i, T, g :: gs, hq, w, hw => by
    simp only [witnessesFrom, List.mem_append] at hw
    have hq0 := hq g (by simp)
    rcases hw with hw | hw
    · cases g with
      | one nm q =>
        simp [Gate.witness] at hw; subst hw
        simp [Gate.qubits] at hq0
        exact sites4 _ (List.mem_range.2 (hhome q (List.mem_range.2 hq0)))
      | cx c t =>
        simp [Gate.witness] at hw; subst hw
        simp [Gate.qubits] at hq0
        exact sites4 _ (List.mem_range.2 (hhome t (List.mem_range.2 hq0.2)))
      | measure q => simp [Gate.witness] at hw
    · exact witnessesFrom_sites home n hhome _ _ gs (fun g' hg' => hq g' (by simp [hg'])) w hw

/-!
## 7. The proof

Everything is in place. One last small fact: a witness of the checker's order join
(`witnessQubits`, F1) carries its gate witness's index and cycle.
-/

theorem map_some {α β : Type} {f : α → β} : ∀ {x : Option α} {b : β}, x.map f = some b → ∃ a, f a = b
  | some a, _, h => ⟨a, Option.some.inj h⟩
  | none, _, h => nomatch h

theorem witnessQubits_spec (inp : Input) (a : WQ) (ha : a ∈ witnessQubits inp) :
    ∃ g ∈ inp.gates, a.idx = g.dag ∧ a.t = g.t := by
  simp only [witnessQubits, List.mem_filterMap] at ha
  obtain ⟨g, hg, h⟩ := ha
  obtain ⟨o, rfl⟩ := map_some h
  exact ⟨g, hg, rfl, rfl⟩

/-!
And the theorem. `refine ⟨rfl, ?_, ...⟩` splits `Implements` into its eleven conditions: the
first (nothing unrealised) holds by definition, and each `·` below proves one of the other ten,
numbered as in F1. `show` restates a goal in an equal form: here it unfolds the checker's
`let R := replay inp` into `posAt`.
-/

/-- **The compiler is correct.** For every circuit on at most eight qubits whose statements name
only qubits it declares, and every placement into the traps of `chain4`, the certificate the
compiler writes satisfies the checker's specification. -/
theorem compile_correct (home : Nat → Nat) (c : Circuit)
    (hn : c.qubits ≤ 8)
    (hhome : ∀ k ∈ List.range c.qubits, home k < 4)
    (hq : ∀ g ∈ c.body, ∀ q ∈ g.qubits, q < c.qubits) :
    Implements (compile home c) := by
  have hinit : AtHome home c.qubits (applyMoves (compile home c).init []) :=
    fun k hk => look_init home c.qubits k hn hk
  have tr := transport home c.qubits hn (compile home c).init c.body 0 0 [] hq
    (fun m hm => nomatch hm) hinit
  refine ⟨rfl, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
  · -- 2. MapInjective
    intro p hp p' hp' h
    simp only [compile, compileWith, List.mem_map, List.mem_range] at hp hp'
    obtain ⟨a, ha, rfl⟩ := hp
    obtain ⟨b, hb, rfl⟩ := hp'
    exact ion_inj (by omega) (by omega) h
  · -- 3. MapTotal
    simp [MapTotal, compile, compileWith]
  · -- 4. EventsSorted
    show sortedByT (merge (movesFrom honest home 0 c.body) []) = true
    rw [merge_nil]
    exact sorted_of_inc 0 _ (movesFrom_inc honest home 0 c.body)
  · -- 5. RotationsKnown: there are no rotations
    intro r hr; exact nomatch hr
  · -- 6. MovesAreHops
    show ∀ m ∈ (compile home c).moves, (m.src, m.dst) ∈ (compile home c).hops ∧
      look (posAt (compile home c) m.t) m.ion = some m.src
    intro m hm
    refine ⟨movesFrom_hops home c.qubits hhome 0 c.body hq m hm, ?_⟩
    rw [posAt_fold _ rfl]
    have := tr.1 m hm
    rw [List.nil_append] at this
    exact this
  · -- 7. GatesLegal
    show ∀ g ∈ (compile home c).gates, g.site ∈ (compile home c).gateSites ∧
      ∀ i ∈ g.ions, look (posAt (compile home c) g.t) i = some g.site
    intro g hg
    refine ⟨witnessesFrom_sites home c.qubits hhome 0 0 c.body hq g hg, ?_⟩
    intro x hx
    rw [posAt_fold _ rfl]
    have := tr.2 g hg x hx
    rw [List.nil_append] at this
    exact this
  · -- 8. OwnIons
    exact own_from honest home (ionOf (compile home c)) c.qubits
      (fun q hq' => ionOf_range c.qubits q hq') c.body 0 0 hq
  · -- 9. Covered
    exact covered_from honest home c.body 0 0
  · -- 10. Grounded
    intro g hg
    obtain ⟨o, ho, h⟩ := grounded_from honest home c.body 0 0 g hg
    exact List.any_eq_true.2 ⟨o, ho, by simp [h]⟩
  · -- 11. RespectsOrder
    show ∀ a ∈ witnessQubits (compile home c), ∀ b ∈ witnessQubits (compile home c),
      a.idx < b.idx → (a.qubits.any fun q => b.qubits.contains q) = true → a.t < b.t
    intro a ha b hb hlt _
    obtain ⟨g, hg, h1, h2⟩ := witnessQubits_spec _ a ha
    obtain ⟨g', hg', h1', h2'⟩ := witnessQubits_spec _ b hb
    rw [h2, h2']
    exact order_from honest home c.body 0 0 g hg g' hg' (by omega)

#print axioms compile_correct

/-!
## 8. Using it, and what it does not say

The theorem turns a proof about one certificate into three small checks about the circuit. Here
is GHZ-4 again; I1 proved its certificate by having the kernel replay it.

{{figure ghz4Cert}}

#### Exercise 1 (★) `ghz4_by_theorem`
Prove that the GHZ-4 certificate holds with `compile_correct`, not by computing it. The three
hypotheses are small decidable facts about the circuit: `by decide` proves each.
-/

def ghz4Cert : Input := compile id4 ghz4

theorem ghz4_by_theorem : Implements ghz4Cert :=
  compile_correct id4 ghz4 (by decide) (by decide) (by decide)

/-!
#### Exercise 2 (★) `check_always_true`
The checker never refuses this compiler on the fragment. Prove it with `check_complete`, the
converse of `check_sound` (both are in `QCCDC.Cert.Sound`): if `Implements` holds, `check`
says `true`.
-/

#check @check_complete

theorem check_always_true (home : Nat → Nat) (c : Circuit) (hn : c.qubits ≤ 8)
    (hhome : ∀ k ∈ List.range c.qubits, home k < 4)
    (hq : ∀ g ∈ c.body, ∀ q ∈ g.qubits, q < c.qubits) :
    check (compile home c) = true :=
  check_complete (compile_correct home c hn hhome hq)

/-!
A theorem can do what no number of checks can: cover a family of circuits of every length at
once. `swaps k` is `k` far swaps (I1's three CNOTs between `q0` and `q3`, each on the longest
route `chain4` has), so `manySwaps k` writes `18 k` moves. Here is `k = 2`.
-/

def swaps : Nat → List Gate
  | 0 => []
  | k + 1 => .cx 0 3 :: .cx 3 0 :: .cx 0 3 :: swaps k

def manySwaps (k : Nat) : Circuit := ⟨"swaps", 4, swaps k⟩

def swaps2Cert : Input := compile id4 (manySwaps 2)

/-!
{{figure swaps2Cert}}

#### Exercise 3 (★★) `swaps_hold`
Prove that for **every** `k`, the certificate for `manySwaps k` holds. The third hypothesis needs
an induction on `k`. The first two are about `4`, not about `k`, but `decide` refuses a goal with a
free variable in it, so state them about `4` with a type ascription: `(by decide : 4 ≤ 8)`.
-/

theorem swaps_hold (k : Nat) : Implements (compile id4 (manySwaps k)) := by
  have h : ∀ k, ∀ g ∈ swaps k, ∀ q ∈ g.qubits, q < 4 := by
    intro k
    induction k with
    | zero => intro g hg; simp [swaps] at hg
    | succ k ih =>
      intro g hg
      simp only [swaps, List.mem_cons] at hg
      rcases hg with rfl | rfl | rfl | hg
      · decide
      · decide
      · decide
      · exact ih g hg
  exact compile_correct id4 (manySwaps k) (by decide : 4 ≤ 8)
    (by decide : ∀ x ∈ List.range 4, id4 x < 4) (h k)

/-!
#### Exercise 4 (★★) `bookkeeping_any_policy`
Section 4 proved four conditions for every policy. Show three of them for an arbitrary policy `p`
and circuit: the jumper of I1, whose certificates the checker refuses, still numbers, covers and
orders its witnesses correctly. Its fault is all in transport.
-/

theorem bookkeeping_any_policy (p : Policy) (home : Nat → Nat) (c : Circuit) :
    Covered (compileWith p home c) ∧ Grounded (compileWith p home c) ∧
      RespectsOrder (compileWith p home c) := by
  refine ⟨covered_from p home c.body 0 0, ?_, ?_⟩
  · intro g hg
    obtain ⟨o, ho, h⟩ := grounded_from p home c.body 0 0 g hg
    exact List.any_eq_true.2 ⟨o, ho, by simp [h]⟩
  · show ∀ a ∈ witnessQubits (compileWith p home c), ∀ b ∈ witnessQubits (compileWith p home c),
      a.idx < b.idx → (a.qubits.any fun q => b.qubits.contains q) = true → a.t < b.t
    intro a ha b hb hlt _
    obtain ⟨g, hg, h1, h2⟩ := witnessQubits_spec _ a ha
    obtain ⟨g', hg', h1', h2'⟩ := witnessQubits_spec _ b hb
    rw [h2, h2']
    exact order_from p home c.body 0 0 g hg g' hg' (by omega)

/-!
**What the theorem does not say.** It is exactly `Implements (compile home c)`, and no more:

1. **Which gate.** `Implements` names no gate (F1, section 8). This compiler emits no pulses at
   all; its studio programme names abstract gates (`H`, `CX`). Whether the pulses make the
   circuit's gates is the semantic half of R10 (Part G), untouched here.
2. **The rules.** Capacity (R1) and every other rule of Part E are not part of `Implements`, so
   the theorem holds of programmes that break them. Exercise 5 is one.
3. **The programme.** The theorem is about the certificate. Nothing here proves that the studio
   programme `studioWith` writes (I1) moves ions as the certificate says; for a real compiler,
   that is a separate computed check (lesson I4).
4. **Other routers.** Only `honest`. The jumper and the forgetful router are wrong, and the
   checker says so (I1).
5. **Bigger devices.** `chain4`, at most eight qubits, homes among its four traps.

And it trusts what every proof here trusts: Lean's kernel, and that `Implements` says what you
want (Part F). It also trusts that the hidden block above is I1's compiler; it is a copy of it.

Here is point 2 made concrete. Put qubit 0 in `C0` and qubits 1 and 2 both in `C1` (the placement
`crowd`), and compile one CNOT from qubit 0 to qubit 1. The placement is inside the fragment, so
the theorem covers it.
-/

def crowd : Nat → Nat := fun k => if k = 0 then 0 else 1
def threeIn : Circuit := ⟨"three_in", 3, [.cx 0 1, .measure 0, .measure 1, .measure 2]⟩
def crowdCert : Input := compile crowd threeIn

/-!
{{figure crowdCert at=1}}

#### Exercise 5 (★★) `capacity_not_promised`
Prove that the certificate holds, by the theorem, and that at the start of cycle 1, the CNOT's
cycle, three ions are in `C1`, whose capacity is two (count them in the replay).
-/

theorem capacity_not_promised :
    Implements crowdCert ∧ ((posAt crowdCert 1).filter (fun p => p.2 = "C1")).length = 3 :=
  ⟨compile_correct crowd threeIn (by decide) (by decide) (by decide), by decide +kernel⟩

/-!
## 9. Two ways to trust a compiler

| | translation validation (I1, I3) | a verified compiler (this lesson) |
|---|---|---|
| what is proved | each certificate, after it is written | every certificate, before any is written |
| a wrong compiler is caught | on the run that writes a bad certificate | when the proof fails, before any run |
| the compiler | any program, in any language | a function in the proof assistant |
| cost per compile | one run of the checker | none |
| when the compiler changes | check its outputs as before | redo the proof |
| what is trusted | `Implements`, `check_sound`, Lean's kernel | `Implements`, Lean's kernel |

Both stop at the same place: `Implements`, the certificate's specification. A verified compiler
does not need `check_sound` at all, but it can only exist in the language of the proof; the
platform's own compiler is OCaml, and the next lesson is how anyone's compiler, in any language,
gets the first column.

## 10. More about the compiler

#### Exercise 6 (★★) `route_length`
Prove how many hops a route has. Two small inductions first (`right a n` and `left a n` have `n`
hops), as `have`s inside the proof, then split on `a ≤ b`.
-/

theorem route_length (a b : Nat) :
    (route a b).length = if a ≤ b then b - a else a - b := by
  have hr : ∀ a n, (right a n).length = n := by
    intro a n
    induction n generalizing a with
    | zero => rfl
    | succ n ih => simp [right, ih]
  have hl : ∀ a n, (left a n).length = n := by
    intro a n
    induction n generalizing a with
    | zero => rfl
    | succ n ih => simp [left, ih]
  unfold route
  by_cases h : a ≤ b
  · rw [if_pos h, if_pos h, hr]
  · rw [if_neg h, if_neg h, hl]

/-!
#### Exercise 7 (★★★) `witnesses_within`
`span p home gs` is how many cycles the statements `gs` take. Prove that every witness the
compiler writes for `gs` from clock `T` is before `T + span p home gs`, for every policy. By
induction on `gs`, with `i` and `T` allowed to vary: `witness_spec` for the first statement, the
induction hypothesis for the rest.
-/

def span (p : Policy) (home : Nat → Nat) : List Gate → Nat
  | [] => 0
  | g :: gs => g.cycles p home + span p home gs

theorem witnesses_within (p : Policy) (home : Nat → Nat) (i T : Nat) (gs : List Gate) :
    ∀ w ∈ witnessesFrom p home i T gs, w.t < T + span p home gs := by
  induction gs generalizing i T with
  | nil => intro w hw; simp [witnessesFrom] at hw
  | cons g gs ih =>
    intro w hw
    simp only [witnessesFrom, List.mem_append] at hw
    simp only [span]
    rcases hw with hw | hw
    · have := witness_spec p home i T g w hw; omega
    · have := ih (i + 1) _ w hw; omega

/-!
#### Exercise 8 (★★★) `moves_even`
The honest router writes an even number of moves: every walk out is matched by a walk home of
the same length. Prove it by induction on the statements. You will need that a walk has as many
moves as its route has hops, and Exercise 6 for the two routes' lengths (this exercise counts as
solved once Exercise 6 is).
-/

theorem moves_even (home : Nat → Nat) : ∀ (T : Nat) (gs : List Gate),
    (movesFrom honest home T gs).length % 2 = 0 := by
  have hw : ∀ q t (hops : List (Nat × Nat)), (walk q t hops).length = hops.length := by
    intro q t hops
    induction hops generalizing t with
    | nil => rfl
    | cons h rest ih => simp [walk, ih]
  intro T gs
  induction gs generalizing T with
  | nil => rfl
  | cons g gs ih =>
    have ih' := ih (T + g.cycles honest home)
    simp only [movesFrom, List.length_append]
    cases g with
    | one n q => simp [Gate.moves]; exact ih'
    | measure q => simp [Gate.moves]; exact ih'
    | cx c t =>
      have e : (Gate.moves honest home T (.cx c t)).length =
          (route (home c) (home t)).length + (route (home t) (home c)).length := by
        simp only [Gate.moves, List.length_append, hw]; rfl
      rw [e, route_length, route_length]
      by_cases h1 : home c ≤ home t <;> by_cases h2 : home t ≤ home c <;>
        simp only [h1, h2, if_true, if_false] <;> omega

/-!
#### Exercise 9 (★★★) `no_cx_still`
A circuit without a CNOT moves nothing: prove that every ion is, at every cycle, where it
started (compare F1, Exercise 8). First show by induction that `movesFrom` writes no moves for
statements on one qubit each; then `posAt_fold`.
-/

theorem no_cx_still (home : Nat → Nat) (c : Circuit) (h : ∀ g ∈ c.body, g.qubits.length = 1)
    (t : Nat) : posAt (compile home c) t = (compile home c).init := by
  have none : ∀ (T : Nat) (gs : List Gate), (∀ g ∈ gs, g.qubits.length = 1) →
      movesFrom honest home T gs = [] := by
    intro T gs
    induction gs generalizing T with
    | nil => intro _; rfl
    | cons g gs ih =>
      intro hg
      have hrest := ih (T + g.cycles honest home) (fun g' h' => hg g' (by simp [h']))
      have h0 := hg g (by simp)
      cases g with
      | one n q => simp [movesFrom, Gate.moves, hrest]
      | measure q => simp [movesFrom, Gate.moves, hrest]
      | cx c t => simp [Gate.qubits] at h0
  rw [posAt_fold _ rfl]
  show applyMoves _ ((movesFrom honest home 0 c.body).takeWhile _) = _
  rw [none 0 c.body h]
  rfl
